"""SSB API ingestion (JSON-stat).

Only used by the *fresh* pipeline (``use_cached_ssb=False``). Requires the
optional ``ssb`` extras (``pyjstat`` + ``requests``) and network access. The
cached pipeline never imports this module.

Two query-file formats are supported, so a directory may mix them:

* **PxWebApi v2** (current) — ``{"url", "params", "payload"}``. ``params`` are
  sent as the query string (e.g. ``lang``, ``outputFormat``) and ``payload`` is
  the POST body (``selection`` + ``placement``).
* **API v0** (legacy) — ``{"postUrl", "queryObj", "tableIdForQuery"}``.

Both must request ``json-stat2`` and ``lang=no`` (the transforms downstream
filter on Norwegian category labels). The table id is taken from
``tableIdForQuery`` when present, otherwise parsed from the v2 ``url`` or the
filename, and used as the dict key the pipeline looks flows up by.
"""

from __future__ import annotations

import json
import os
import re

import pandas as pd


def _post_query(spec: dict, requests):
    """POST a single query spec, handling both the v2 and legacy v0 shapes."""
    if "url" in spec:  # PxWebApi v2
        return requests.post(
            spec["url"],
            params=spec.get("params"),
            json=spec.get("payload"),
            timeout=60,
        )
    # Legacy API v0
    return requests.post(spec["postUrl"], json=spec["queryObj"], timeout=60)


def _table_id(spec: dict, filename: str) -> str:
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


def process_query_files(directory_query_path: str) -> dict[str, list[pd.DataFrame]]:
    """POST each query JSON in a directory to SSB and parse the JSON-stat reply.

    Returns ``{table_id: [DataFrame, ...]}`` (``pyjstat.from_json_stat`` yields a
    list of frames; the pipeline concatenates them per table). Files that fail
    are reported and skipped rather than aborting the whole run.
    """
    import requests
    from pyjstat import pyjstat

    dataset_output: dict[str, list[pd.DataFrame]] = {}

    for filename in sorted(os.listdir(directory_query_path)):
        if not filename.endswith(".json"):
            continue
        file_path = os.path.join(directory_query_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                spec = json.load(fh)

            result = _post_query(spec, requests)
            result.raise_for_status()

            dataset = pyjstat.from_json_stat(json.loads(result.text))
            table_id = _table_id(spec, filename)
            dataset_output[table_id] = dataset
            print(f"Processed {filename} (table {table_id})")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"Error processing {filename}: {exc}")

    return dataset_output
