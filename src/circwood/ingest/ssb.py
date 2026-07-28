"""SSB API ingestion (JSON-stat2), hardened for unattended runs.

Only used by the *fresh* pipeline (``use_cached_ssb=False``). Requires the
optional ``ssb`` extras (``pyjstat`` + ``requests``) and network access. The
cached pipeline never imports this module.

Two query-file formats are supported, so a directory may mix them:

* **PxWebApi v2** (current) — ``{"url", "params", "payload"}``. ``params`` are
  sent as the query string (e.g. ``lang``, ``outputFormat``) and ``payload`` is
  the POST body (``selection`` + ``placement``).
* **API v0** (legacy) — ``{"postUrl", "queryObj", "tableIdForQuery"}``.

Both must request ``json-stat2`` and ``lang=no`` — the transforms downstream
filter on Norwegian category labels ('Gran', 'Skurtømmer', ...), so a query that
asks for ``lang=en`` returns HTTP 200, parses cleanly, and yields *zero*
matching rows. :func:`normalize_spec` injects the two settings when a query file
omits them and refuses the file when it asks for something else; it is not
optional and it is not a warning.

Failure policy
--------------
Two classes of problem, deliberately handled differently:

* **Configuration errors** — an unreadable query file, a query that violates the
  json-stat2/``lang=no`` contract, two files targeting the same table id. These
  raise :class:`SSBQueryError` immediately and are never excused by
  ``allow_partial``: they are repo bugs, reproducible offline, and every run
  will hit them.
* **Runtime errors** — network, HTTP, parse, validation. These are collected per
  table and raised together as :class:`SSBIngestError` at the end of the run, so
  one flaky table does not hide the other four. Passing ``allow_partial=True``
  downgrades them to a warning and returns whatever succeeded — an explicit
  opt-in, because a partial dataset silently becomes a *wrong* Sankey rather
  than a missing one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

import pandas as pd

from circwood import config, constants as C

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request contract
# ---------------------------------------------------------------------------

#: Query-string parameters every PxWebApi v2 query must resolve to.
REQUIRED_PARAMS = {"outputFormat": "json-stat2", "lang": "no"}

#: Language segment every legacy v0 ``postUrl`` must contain (``.../api/v0/no/...``).
REQUIRED_V0_LANG_SEGMENT = "/no/"

PROJECT_URL = "https://github.com/Zoet2202/circwood"

#: SSB asks API users to identify themselves. Override the contact with the
#: ``CIRCWOOD_CONTACT`` environment variable; the default points at the public
#: repository rather than hard-coding a personal address into a public repo.
CONTACT = os.environ.get("CIRCWOOD_CONTACT", PROJECT_URL)
USER_AGENT = f"circwood-wp4/0.1 (Circwood WP4 wood-flow pipeline; +{PROJECT_URL}; contact: {CONTACT})"

DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 5
DEFAULT_BACKOFF = 1.0
RETRY_STATUS = (429, 500, 502, 503, 504)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class SSBError(RuntimeError):
    """Base class for every SSB ingestion failure."""


class SSBQueryError(SSBError):
    """A query file is unusable: unreadable, off-contract, or a duplicate."""


class SSBParseError(SSBError):
    """A response body was not parseable json-stat2."""


class SSBValidationError(SSBError):
    """A parsed table does not satisfy its :class:`TableContract`."""


class SSBIngestError(SSBError):
    """One or more tables failed during a run; carries every failure.

    ``failures`` maps table id -> the reason that table did not make it, so a
    scheduled job's log names all the broken tables in one message instead of
    one per re-run.
    """

    def __init__(self, failures: Mapping[str, str]):
        self.failures = dict(failures)
        detail = "\n".join(
            f"  - {table_id}: {reason}" for table_id, reason in sorted(self.failures.items())
        )
        super().__init__(
            f"{len(self.failures)} SSB table(s) failed to ingest:\n{detail}\n"
            f"The dataset would be partial. Pass allow_partial=True only if an "
            f"incomplete Sankey is genuinely acceptable."
        )


# ---------------------------------------------------------------------------
# Validation contracts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TableContract:
    """What a parsed table must look like before the pipeline may build on it.

    ``required_columns`` are the pyjstat column names (Norwegian dimension
    *labels*, not the variable codes used in the query file) that the transforms
    index by name. ``min_year``/``max_year`` bracket the analysis window: a table
    that no longer reaches back to 2012 has been re-scoped upstream and must not
    be folded into the dataset unnoticed.
    """

    required_columns: tuple[str, ...] = ("value",)
    year_column: str = "år"
    min_year: int | None = C.MIN_YEAR
    max_year: int | None = C.MAX_YEAR


#: Per-table expectations, keyed by SSB table id. Column names were taken from
#: live responses; they are the labels ``pyjstat`` derives, which is why they are
#: lowercase Norwegian ('sortiment', not the 'Treslag' variable code).
TABLE_CONTRACTS: dict[str, TableContract] = {
    # Avvirkning for salg — roundwood harvest by species.
    "03795": TableContract(("år", "sortiment", "value")),
    # The same table selected by county (agg_KommFylker, the 2024-boundary
    # backcast series) — the data behind the `region` dimension. A separate
    # query file with an explicit tableIdForQuery, because process_query_files
    # correctly refuses two files with the same table id.
    "03795_fylke": TableContract(("år", "sortiment", "region", "value")),
    # Industrivirke avvirket for salg — removals by assortment.
    "07410": TableContract(("år", "sortiment", "value")),
    # Utenrikshandel — imports/exports by commodity code.
    "08801": TableContract(
        ("år", "varenummer", "import/eksport", "statistikkvariabel", "value")
    ),
    # Avfall — wood waste by treatment.
    "10513": TableContract(("år", "behandlingsmåte", "value")),
    # Ved til brensel — firewood by wood group.
    "11181": TableContract(("år", "virkesgruppe", "value")),
}

DEFAULT_CONTRACT = TableContract()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TableVintage:
    """Provenance for one fetched table — the input to the Phase B manifest.

    ``updated`` is SSB's own last-revision timestamp from the json-stat2
    document; ``fetched_at`` is when *we* pulled it. The two are different
    questions ("how old is the statistic" vs "how old is our copy") and a
    refresh loop needs both to tell a genuine revision from a re-run.
    """

    table_id: str
    query_file: str
    query_sha256: str
    updated: str | None
    fetched_at: str
    rows: int
    year_min: int | None
    year_max: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IngestResult:
    """Everything one ingestion run produced.

    ``datasets`` keeps the shape the pipeline consumes (``{table_id: [frame,
    ...]}`` — ``pyjstat`` yields a list per document). ``failures`` is empty
    unless ``allow_partial=True`` let the run continue past a broken table.
    """

    datasets: dict[str, list[pd.DataFrame]] = field(default_factory=dict)
    vintages: dict[str, TableVintage] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------------------
# Query files
# ---------------------------------------------------------------------------
def _table_id(spec: Mapping[str, Any], filename: str) -> str:
    """Derive the SSB table id used as the result dict key.

    Order of preference: explicit ``tableIdForQuery`` -> the digits in the v2
    ``url`` (``.../tables/07410/data``) -> the digits in the filename
    (``ssbapi_table_07410.json``).
    """
    if "tableIdForQuery" in spec:
        return str(spec["tableIdForQuery"])
    match = re.search(r"tables/(\d+)", spec.get("url", ""))
    if match:
        return match.group(1)
    match = re.search(r"(\d{4,})", filename)
    return match.group(1) if match else filename


def _normalize_v2_params(params: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    """Return ``params`` with the json-stat2/``lang=no`` contract enforced."""
    out = dict(params or {})
    for key, want in REQUIRED_PARAMS.items():
        have = out.get(key)
        if have is None:
            log.debug("%s: injecting missing query param %s=%s", name, key, want)
            out[key] = want
        elif str(have).strip().lower() != want.lower():
            raise SSBQueryError(
                f"{name}: query param {key}={have!r} violates the ingestion "
                f"contract (must be {want!r}). A wrong {key} returns HTTP 200 "
                f"and parses cleanly, then matches zero rows downstream."
            )
    return out


def _normalize_v0_spec(spec: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return a legacy v0 ``spec`` with the format/language contract enforced."""
    out = dict(spec)
    query_obj = dict(out.get("queryObj") or {})
    response = dict(query_obj.get("response") or {})
    fmt = response.get("format")
    want = REQUIRED_PARAMS["outputFormat"]
    if fmt is None:
        response["format"] = want
    elif str(fmt).strip().lower() != want:
        raise SSBQueryError(
            f"{name}: queryObj.response.format={fmt!r} violates the ingestion "
            f"contract (must be {want!r})."
        )
    query_obj["response"] = response
    out["queryObj"] = query_obj

    # v0 carries the language in the URL path, not a parameter.
    post_url = str(out.get("postUrl", ""))
    if REQUIRED_V0_LANG_SEGMENT not in post_url:
        raise SSBQueryError(
            f"{name}: postUrl {post_url!r} is not a Norwegian-language endpoint "
            f"(expected a {REQUIRED_V0_LANG_SEGMENT!r} path segment). The "
            f"transforms filter on Norwegian category labels."
        )
    return out


def normalize_spec(spec: Mapping[str, Any], name: str = "<spec>") -> dict[str, Any]:
    """Return ``spec`` with the json-stat2 + Norwegian-language contract applied.

    Missing settings are injected; conflicting ones raise :class:`SSBQueryError`.
    Idempotent, and pure — call it in tests without touching the network.
    """
    if "url" in spec:
        out = dict(spec)
        out["params"] = _normalize_v2_params(spec.get("params"), name)
        return out
    if "postUrl" in spec:
        return _normalize_v0_spec(spec, name)
    raise SSBQueryError(
        f"{name}: query file has neither a v2 'url' nor a legacy 'postUrl' key."
    )


def load_query_spec(path: str | Path) -> dict[str, Any]:
    """Read one query JSON off disk and enforce the request contract on it."""
    path = Path(path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SSBQueryError(f"{path.name}: cannot read query file: {exc}") from exc
    if not isinstance(spec, dict):
        raise SSBQueryError(f"{path.name}: query file must contain a JSON object.")
    return normalize_spec(spec, path.name)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Fetch (network)
# ---------------------------------------------------------------------------
def make_session(
    *,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF,
    user_agent: str = USER_AGENT,
) -> "requests.Session":
    """Return a ``requests.Session`` that retries SSB's transient failures.

    ``allowed_methods`` has to include POST: every SSB query in this project is
    a POST, and urllib3 retries only idempotent methods by default — the default
    Retry would therefore retry nothing at all. ``raise_on_status=False`` lets
    the exhausted retry surface as a normal ``HTTPError`` from
    ``raise_for_status`` rather than a urllib3 ``MaxRetryError``.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUS,
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = user_agent
    return session


def fetch_table(
    spec: Mapping[str, Any],
    *,
    session: "requests.Session | None" = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """POST one query spec to SSB and return the raw response body.

    The only function in this module that touches the network — everything else
    is pure and testable against a saved response.
    """
    spec = normalize_spec(spec)
    owns_session = session is None
    session = session if session is not None else make_session()
    try:
        if "url" in spec:  # PxWebApi v2
            response = session.post(
                spec["url"],
                params=spec["params"],
                json=spec.get("payload"),
                timeout=timeout,
            )
        else:  # Legacy API v0
            response = session.post(
                spec["postUrl"], json=spec["queryObj"], timeout=timeout
            )
        response.raise_for_status()
        # json-stat2 is JSON, hence UTF-8 (RFC 8259). requests falls back to
        # ISO-8859-1 when the response header omits a charset, which mangles the
        # Norwegian labels the transforms filter on into no-match strings.
        return response.content.decode("utf-8")
    finally:
        if owns_session:
            session.close()


# ---------------------------------------------------------------------------
# Parse (pure)
# ---------------------------------------------------------------------------
def _loads(raw: str, table_id: str | None = None) -> dict[str, Any]:
    label = f"table {table_id}" if table_id else "response"
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SSBParseError(f"{label}: response body is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SSBParseError(f"{label}: expected a json-stat2 object, got {type(document).__name__}.")
    return document


def parse_table(raw: str, table_id: str | None = None) -> list[pd.DataFrame]:
    """Parse a raw json-stat2 body into DataFrames. Pure; no network."""
    document = _loads(raw, table_id)
    from pyjstat import pyjstat

    label = f"table {table_id}" if table_id else "response"
    try:
        frames = pyjstat.from_json_stat(document)
    except Exception as exc:  # noqa: BLE001 - pyjstat raises bare KeyError/TypeError
        raise SSBParseError(f"{label}: not valid json-stat2: {exc}") from exc
    if not frames:
        raise SSBParseError(f"{label}: json-stat2 document contained no datasets.")
    return frames


def table_updated(raw: str, table_id: str | None = None) -> str | None:
    """Return SSB's own last-revision timestamp for a raw json-stat2 body."""
    return _loads(raw, table_id).get("updated")


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
def validate_table(
    table_id: str,
    frames: list[pd.DataFrame],
    contract: TableContract | None = None,
) -> tuple[int | None, int | None]:
    """Assert a parsed table is usable and return its ``(year_min, year_max)``.

    Raises :class:`SSBValidationError` — always naming the table id, because the
    caller is a loop over five tables and "missing column 'sortiment'" on its own
    does not say which one.
    """
    contract = contract or TABLE_CONTRACTS.get(table_id, DEFAULT_CONTRACT)
    df = pd.concat(frames, ignore_index=True)

    if df.empty:
        raise SSBValidationError(f"table {table_id}: parsed to zero rows.")

    missing = [col for col in contract.required_columns if col not in df.columns]
    if missing:
        raise SSBValidationError(
            f"table {table_id}: missing expected column(s) {missing}; got "
            f"{list(df.columns)}. SSB may have renamed or dropped a dimension."
        )

    if "value" in df.columns and df["value"].isna().all():
        raise SSBValidationError(
            f"table {table_id}: the 'value' column is entirely NaN — the query "
            f"matched cells but SSB returned no figures for any of them."
        )

    year_col = contract.year_column
    if year_col not in df.columns:
        return None, None

    years = pd.to_numeric(df[year_col], errors="coerce").dropna()
    if years.empty:
        raise SSBValidationError(
            f"table {table_id}: no parseable years in column {year_col!r}."
        )
    year_min, year_max = int(years.min()), int(years.max())

    if contract.min_year is not None and year_min > contract.min_year:
        raise SSBValidationError(
            f"table {table_id}: coverage starts at {year_min}, after the "
            f"analysis window opens ({contract.min_year})."
        )
    if contract.max_year is not None and year_max < contract.max_year:
        raise SSBValidationError(
            f"table {table_id}: coverage ends at {year_max}, before the "
            f"analysis window closes ({contract.max_year})."
        )
    return year_min, year_max


# ---------------------------------------------------------------------------
# Raw cache
# ---------------------------------------------------------------------------
def cache_raw(raw: str, table_id: str, cache_dir: str | Path) -> Path:
    """Write a raw response to ``{cache_dir}/{table_id}.json`` and return the path.

    Written *before* parsing, so a response that pyjstat chokes on is still on
    disk to look at — and so successive pulls can be diffed to see what SSB
    changed.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{table_id}.json"
    path.write_text(raw, encoding="utf-8")
    return path


def write_vintages(vintages: Mapping[str, TableVintage], cache_dir: str | Path) -> Path:
    """Write the per-table vintages next to the raw cache they describe."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "_vintages.json"
    payload = {table_id: v.to_dict() for table_id, v in sorted(vintages.items())}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def process_query_files(
    directory_query_path: str | Path,
    *,
    allow_partial: bool = False,
    session: "requests.Session | None" = None,
    cache_dir: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> IngestResult:
    """Fetch, cache, parse and validate every query JSON in a directory.

    Returns an :class:`IngestResult`. Raises :class:`SSBQueryError` on a
    configuration problem and :class:`SSBIngestError` when any table failed at
    runtime, unless ``allow_partial=True`` — see the module docstring for why
    those two are not the same thing.
    """
    directory = Path(directory_query_path)
    if not directory.is_dir():
        raise SSBQueryError(f"Query directory not found: {directory}")
    cache_dir = Path(cache_dir) if cache_dir is not None else config.RAW_SSB_DIR

    query_files = sorted(p for p in directory.iterdir() if p.suffix == ".json")
    if not query_files:
        raise SSBQueryError(f"No .json query files in {directory}")

    # Resolve and contract-check every file up front: a bad query file or a
    # duplicate table id is a repo bug, and there is no reason to spend five
    # network round-trips before reporting it.
    specs: list[tuple[str, Path, dict[str, Any]]] = []
    seen: dict[str, str] = {}
    for path in query_files:
        spec = load_query_spec(path)
        table_id = _table_id(spec, path.name)
        if table_id in seen:
            raise SSBQueryError(
                f"Duplicate SSB table id {table_id!r}: {seen[table_id]} and "
                f"{path.name} both target it. One would silently overwrite the "
                f"other; split the selection or merge the files."
            )
        seen[table_id] = path.name
        specs.append((table_id, path, spec))

    result = IngestResult()
    owns_session = session is None
    session = session if session is not None else make_session()
    try:
        for table_id, path, spec in specs:
            fetched_at = _utcnow()
            try:
                raw = fetch_table(spec, session=session, timeout=timeout)
                cache_raw(raw, table_id, cache_dir)
                frames = parse_table(raw, table_id)
                year_min, year_max = validate_table(table_id, frames)
            except Exception as exc:  # noqa: BLE001 - collected, then re-raised
                log.error("table %s (%s) failed: %s", table_id, path.name, exc)
                result.failures[table_id] = f"{path.name}: {exc}"
                continue

            result.datasets[table_id] = frames
            result.vintages[table_id] = TableVintage(
                table_id=table_id,
                query_file=path.name,
                query_sha256=_sha256(path),
                updated=table_updated(raw, table_id),
                fetched_at=fetched_at,
                rows=int(sum(len(f) for f in frames)),
                year_min=year_min,
                year_max=year_max,
            )
            log.info(
                "table %s: %d rows, %s-%s, SSB updated %s",
                table_id,
                result.vintages[table_id].rows,
                year_min,
                year_max,
                result.vintages[table_id].updated,
            )
    finally:
        if owns_session:
            session.close()

    if result.vintages:
        write_vintages(result.vintages, cache_dir)

    if result.failures:
        if not allow_partial:
            raise SSBIngestError(result.failures)
        log.warning(
            "allow_partial=True: continuing with %d of %d tables; missing %s",
            len(result.datasets),
            len(specs),
            sorted(result.failures),
        )
    return result


__all__ = [
    "CONTACT",
    "DEFAULT_CONTRACT",
    "IngestResult",
    "REQUIRED_PARAMS",
    "SSBError",
    "SSBIngestError",
    "SSBParseError",
    "SSBQueryError",
    "SSBValidationError",
    "TABLE_CONTRACTS",
    "TableContract",
    "TableVintage",
    "USER_AGENT",
    "cache_raw",
    "fetch_table",
    "load_query_spec",
    "make_session",
    "normalize_spec",
    "parse_table",
    "process_query_files",
    "table_updated",
    "validate_table",
    "write_vintages",
]
