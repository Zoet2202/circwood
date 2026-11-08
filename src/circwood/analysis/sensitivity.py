"""One-at-a-time sensitivity of β to the fixed coefficients (ROADMAP Phase G).

β is only partly empirical: the routing of harvest between sawmills, pulp
mills, board mills, firewood and export is data (SSB 07410 / 03795 / 11181 /
08801, Treindustrien), but every downstream split is a point coefficient in
``constants.py``. This module measures how much each of those coefficients
moves β, by rebuilding the national flows with one coefficient at its low and
then its high bound (``config/coefficient_ranges.csv``) and re-running
:func:`circwood.analysis.hwp.beta_series` on each rebuild, and it turns the
deviations into the ``ci_low`` / ``ci_high`` columns of ``beta_series.csv``.

What is varied
--------------
* the sawmill by-product shares (``shrinkage``, ``sawdust``, ``cellulose``,
  ``scraps``; the sawn yield absorbs the change) and the planing factors
  (:class:`~circwood.transform.sawing.SawmillCoefficients`);
* the mill splits (``boardmill_loss``, ``pulp_short_term``, ``pulp_energy`` —
  the other pulp shares are rescaled to keep 100 % — and ``wpp_long_term``;
  :class:`~circwood.transform.mills.MillCoefficients`);
* ``firewood_percent_spruce``, by re-splitting the coniferous firewood rows of
  the cached primary table between the Spruce and Pine pools (it changes which
  pool the firewood leaves, so it moves the per-species β);
* the carbon-density envelope (``carbon_densities.csv`` low / high). It is run
  and reported so the invariance is visible, but it moves β by nothing worth
  the name: a species' β cannot depend on densities (all of a pool's wood
  enters at one density), and the shipped envelope is a uniform ±10 % of every
  row, which cancels in the harvest-carbon weights behind ``species = "all"``
  as well (|Δβ| ≤ 3e-5 in the committed table). Only species-asymmetric bounds
  would move ``all``; ``ci_method`` says the envelope adds nothing.

Bark and harvest residues are fixed shares of harvest that sit outside the
β denominator and are pure sinks, so they are not varied.

Scenario gates
--------------
A coefficient at its bound can widen an existing data imbalance past the
export gates (the 2020 Planingmills gap sits at 9 % of volume), so scenarios
run under ``BETA_SENSITIVITY_CLOSURE_TOL`` / ``BETA_SENSITIVITY_EXCESS_OUT_TOL``
while the central run keeps the export gates and therefore equals the
exported β.

How the bounds are formed
-------------------------
For each (year, species) the deviations ``β(parameter at bound) − β`` are
combined by root-sum-square, negative deviations into ``ci_low`` and positive
ones into ``ci_high`` (first-order propagation of independent, possibly
asymmetric ranges). It is an *envelope of the coefficient assumptions*, not a
statistical confidence interval, and ``ci_method`` says exactly that.

Independence is itself an assumption. The four sawing by-products are shares
of one split and their high bounds all lower β (cellulose −0.013, scraps
−0.010, shrinkage −0.004, sawdust −0.001 on β(all) 2012–2022), so the fully
correlated bound — the plain sum of the one-sided deviations — is wider:
0.156–0.254 against the RSS 0.186–0.235. :func:`beta_bounds` reports it as
``ci_low_linear`` / ``ci_high_linear`` beside the RSS columns so a reader can
pick the conservative one; ``beta_series.csv`` carries the RSS pair.

Everything here is a pure function of the committed dataset, the two config
CSVs and ``coefficient_ranges.csv``; the export is deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from circwood import config
from circwood import constants as C
from circwood.carbon.species_model import load_model
from circwood.ingest import treindustrien as tre
from circwood.transform.mills import MillCoefficients
from circwood.transform.sawing import SawmillCoefficients

#: Columns of ``config/coefficient_ranges.csv``.
RANGE_COLUMNS = ("parameter", "central", "low", "high", "unit", "note")

#: Columns of ``data/processed/beta_sensitivity.csv``.
SENSITIVITY_COLUMNS = (
    "parameter", "bound", "value", "year", "species", "beta", "delta_beta",
)

SAWING_PARAMETERS = (
    "shrinkage", "sawdust", "cellulose", "scraps",
    "planed_input_factor", "byproduct_planing",
)
MILL_PARAMETERS = ("boardmill_loss", "pulp_short_term", "pulp_energy", "wpp_long_term")
FIREWOOD_PARAMETER = "firewood_percent_spruce"
DENSITY_PARAMETER = "carbon_density"

KNOWN_PARAMETERS = SAWING_PARAMETERS + MILL_PARAMETERS + (FIREWOOD_PARAMETER,)

#: The ``constants.py`` value each parameter must agree with (the loader checks).
_CONSTANT_OF = {
    "shrinkage": C.SHRINKAGE, "sawdust": C.SAWDUST, "cellulose": C.CELLULOSE,
    "scraps": C.SCRAPS, "planed_input_factor": C.PLANED_INPUT_FACTOR,
    "byproduct_planing": C.BYPRODUCT_PLANING, "boardmill_loss": C.BOARDMILL_LOSS,
    "pulp_short_term": C.PULP_SHORT_TERM, "pulp_energy": C.PULP_ENERGY,
    "wpp_long_term": C.WPP_LONG_TERM, FIREWOOD_PARAMETER: C.FIREWOOD_PERCENT_SPRUCE,
}


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------
def load_ranges(path: Path | None = None) -> pd.DataFrame:
    """Read and validate ``config/coefficient_ranges.csv``.

    Every parameter must be one this module knows how to vary, its ``central``
    must equal the ``constants.py`` value (so the file cannot drift from the
    model), and ``low <= central <= high``.
    """
    path = Path(path) if path is not None else config.COEFFICIENT_RANGES_FILE
    df = pd.read_csv(path)
    missing = [c for c in RANGE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing column(s) {missing}.")
    unknown = sorted(set(df["parameter"]) - set(KNOWN_PARAMETERS))
    if unknown:
        raise ValueError(
            f"{path.name}: parameter(s) {unknown} are not varied by "
            "circwood.analysis.sensitivity; known: " + ", ".join(KNOWN_PARAMETERS)
        )
    if df["parameter"].duplicated().any():
        raise ValueError(f"{path.name}: duplicate parameter rows.")
    for r in df.itertuples(index=False):
        if not (r.low <= r.central <= r.high):
            raise ValueError(f"{path.name}: {r.parameter}: need low <= central <= high.")
        if not np.isclose(r.central, _CONSTANT_OF[r.parameter]):
            raise ValueError(
                f"{path.name}: {r.parameter} central {r.central} differs from "
                f"constants.py ({_CONSTANT_OF[r.parameter]}); keep the two in step."
            )
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Scenario construction
# ---------------------------------------------------------------------------
def _mill_scenario(parameter: str, value: float) -> MillCoefficients:
    base = MillCoefficients()
    if parameter == "boardmill_loss":
        return replace(base, boardmill_loss=value)
    if parameter == "wpp_long_term":
        return replace(base, wpp_long_term=value)
    if parameter == "pulp_short_term":
        rest = 100.0 - value
        scale = rest / (base.pulp_energy + base.pulp_loss)
        return replace(base, pulp_short_term=value,
                       pulp_energy=base.pulp_energy * scale, pulp_loss=base.pulp_loss * scale)
    if parameter == "pulp_energy":
        rest = 100.0 - value
        scale = rest / (base.pulp_short_term + base.pulp_loss)
        return replace(base, pulp_energy=value,
                       pulp_short_term=base.pulp_short_term * scale, pulp_loss=base.pulp_loss * scale)
    raise ValueError(f"not a mill parameter: {parameter}")


def resplit_firewood(primary: pd.DataFrame, percent_spruce: float) -> pd.DataFrame:
    """Re-split the coniferous firewood rows of a national primary table.

    SSB 11181 publishes coniferous firewood as one figure; the pipeline splits
    it spruce/pine by ``FIREWOOD_PERCENT_SPRUCE``. Moving the split moves the
    same volume between ``Spruce -> Firewood`` and ``Pine -> Firewood`` *and*
    between the two harvest edges ``Harvested wood -> Spruce/Pine`` (the pools
    must still balance). Non-coniferous firewood and imports are untouched.
    """
    out = primary.copy()
    share = percent_spruce / 100.0
    for year, g in out.groupby("year"):
        fw = g[(g["target"] == "Firewood") & g["source"].isin(["Spruce", "Pine"])]
        total = float(fw["volume"].sum())
        if total <= 0:
            continue
        current_spruce = float(fw.loc[fw["source"] == "Spruce", "volume"].sum())
        delta = share * total - current_spruce
        if abs(delta) < 1e-12:
            continue
        for node, sign in (("Spruce", 1.0), ("Pine", -1.0)):
            fw_idx = out.index[(out["year"] == year) & (out["source"] == node) & (out["target"] == "Firewood")]
            hw_idx = out.index[(out["year"] == year) & (out["source"] == "Harvested wood") & (out["target"] == node)]
            if len(fw_idx) != 1 or len(hw_idx) != 1:
                raise ValueError(
                    f"year {year}: expected exactly one {node} -> Firewood and one "
                    f"Harvested wood -> {node} primary row to re-split; found "
                    f"{len(fw_idx)} and {len(hw_idx)}."
                )
            out.loc[fw_idx, "volume"] += sign * delta
            out.loc[hw_idx, "volume"] += sign * delta
    if (out["volume"] < -1e-9).any():
        raise ValueError(f"firewood_percent_spruce={percent_spruce} drives a flow negative.")
    out["volume"] = out["volume"].clip(lower=0.0)
    return out


def rebuild_national(
    primary: pd.DataFrame,
    treindustrien_df: pd.DataFrame,
    parameter: str | None = None,
    value: float | None = None,
) -> pd.DataFrame:
    """National flows with one coefficient at ``value`` (or all at default)."""
    from circwood.transform.assemble import assemble_sankey

    sawing = SawmillCoefficients()
    mills = MillCoefficients()
    frame = primary
    if parameter is not None:
        if parameter in SAWING_PARAMETERS:
            sawing = replace(sawing, **{parameter: float(value)})
            if sawing.sawn_yield <= 0:
                raise ValueError(f"{parameter}={value} leaves no sawn yield.")
        elif parameter in MILL_PARAMETERS:
            mills = _mill_scenario(parameter, float(value))
        elif parameter == FIREWOOD_PARAMETER:
            frame = resplit_firewood(primary, float(value))
        else:
            raise ValueError(f"unknown parameter {parameter!r}")
    return assemble_sankey(
        frame, treindustrien_df, coefficients=sawing, mill_coefficients=mills, widen=False,
    )


# ---------------------------------------------------------------------------
# The analysis
# ---------------------------------------------------------------------------
def sensitivity_table(
    df: pd.DataFrame | None = None,
    *,
    ranges: pd.DataFrame | None = None,
    min_year: int = C.BETA_MIN_YEAR,
    max_year: int = C.BETA_MAX_YEAR,
    include_density: bool = True,
) -> pd.DataFrame:
    """β per (parameter, bound, year, species) with the deviation from central.

    ``df`` is a flow table (default: the committed dataset); its primary rows
    are extracted and the flows rebuilt for every scenario. The ``central``
    rows (``parameter == "central"``) are the default rebuild and are the
    reference every ``delta_beta`` is taken against. Raises on any β gate, like
    :func:`~circwood.analysis.hwp.beta_series` itself.
    """
    from circwood.analysis.hwp import beta_series
    from circwood.pipeline import load_cached_sankey
    from circwood.transform.assemble import primary_rows

    ranges = load_ranges() if ranges is None else ranges
    df = load_cached_sankey() if df is None else df
    primary = primary_rows(df)
    treindustrien_df = tre.load_treindustrien()
    model = load_model()

    def run(frame: pd.DataFrame, model_=model, scenario: bool = True) -> pd.DataFrame:
        gates = (
            dict(closure_tol=C.BETA_SENSITIVITY_CLOSURE_TOL,
                 excess_out_tol=C.BETA_SENSITIVITY_EXCESS_OUT_TOL)
            if scenario else {}
        )
        b = beta_series(frame, min_year=min_year, max_year=max_year, model=model_, **gates)
        return b[["year", "species", "beta"]]

    central_flows = rebuild_national(primary, treindustrien_df)
    # The central run keeps the export gates, so it equals the exported beta.
    central = run(central_flows, scenario=False).rename(columns={"beta": "central"})

    records = []

    def add(parameter: str, bound: str, value: float, beta: pd.DataFrame) -> None:
        merged = beta.merge(central, on=["year", "species"], how="left")
        for r in merged.itertuples(index=False):
            records.append({
                "parameter": parameter, "bound": bound, "value": value,
                "year": int(r.year), "species": r.species,
                "beta": float(r.beta), "delta_beta": float(r.beta - r.central),
            })

    add("central", "central", np.nan, central.rename(columns={"central": "beta"}))
    for r in ranges.itertuples(index=False):
        for bound in ("low", "high"):
            value = float(getattr(r, bound))
            flows = rebuild_national(primary, treindustrien_df, r.parameter, value)
            add(r.parameter, bound, value, run(flows))
    if include_density:
        for bound in ("low", "high"):
            add(DENSITY_PARAMETER, bound, np.nan, run(central_flows, load_model(scenario=bound)))

    out = pd.DataFrame(records, columns=list(SENSITIVITY_COLUMNS))
    return out.sort_values(["parameter", "bound", "year", "species"]).reset_index(drop=True)


def beta_bounds(sens: pd.DataFrame) -> pd.DataFrame:
    """Root-sum-square envelope per (year, species) from a sensitivity table.

    Columns: ``year, species, beta, ci_low, ci_high, ci_low_linear,
    ci_high_linear, ci_method``. Negative deviations add to the low side,
    positive to the high side; both bounds of one parameter can land on the
    same side (the larger one is used per side). The ``_linear`` pair is the
    fully correlated alternative (plain sums instead of root-sum-squares), the
    conservative bound when the ranges move together, as the sawing shares do.
    """
    central = (
        sens[sens["parameter"] == "central"]
        .set_index(["year", "species"])["beta"]
    )
    scen = sens[sens["parameter"] != "central"]
    params = sorted(scen["parameter"].unique())
    n_coef = len([p for p in params if p != DENSITY_PARAMETER])
    method = (
        f"RSS envelope of one-at-a-time deviations over {n_coef} coefficient(s) in "
        "config/coefficient_ranges.csv"
        + ("; the uniform carbon-density envelope (carbon_densities.csv low/high) is run but cancels in beta"
           if DENSITY_PARAMETER in params else "")
        + "; treats the ranges as independent (the sawing by-products move together so the linear sum "
        "is the conservative bound); an assumption envelope rather than a statistical confidence interval"
    )
    rows = []
    for (year, species), beta in central.items():
        g = scen[(scen["year"] == year) & (scen["species"] == species)]
        low2 = 0.0
        high2 = 0.0
        low_sum = 0.0
        high_sum = 0.0
        for _, pg in g.groupby("parameter"):
            d = pg["delta_beta"].to_numpy(dtype=float)
            lo = float(min(d.min(), 0.0))
            hi = float(max(d.max(), 0.0))
            low2 += lo ** 2
            high2 += hi ** 2
            low_sum += lo
            high_sum += hi
        rows.append({
            "year": int(year), "species": species, "beta": float(beta),
            "ci_low": float(beta - np.sqrt(low2)), "ci_high": float(beta + np.sqrt(high2)),
            "ci_low_linear": float(beta + low_sum), "ci_high_linear": float(beta + high_sum),
            "ci_method": method,
        })
    return pd.DataFrame(rows).sort_values(["year", "species"]).reset_index(drop=True)


def summary_table(sens: pd.DataFrame, min_year: int = C.MIN_YEAR, max_year: int = C.MAX_YEAR) -> pd.DataFrame:
    """One row per parameter: mean β(all) at low / central / high over the window.

    Columns: ``parameter, value_low, value_high, beta_all_central, beta_all_low,
    beta_all_high, max_abs_delta`` (the largest |Δβ| over every year and
    species). For the paper's sensitivity table.
    """
    w = sens[(sens["year"] >= min_year) & (sens["year"] <= max_year) & (sens["species"] == "all")]
    central = float(w.loc[w["parameter"] == "central", "beta"].mean())
    rows = []
    for parameter, g in w[w["parameter"] != "central"].groupby("parameter"):
        lo = g[g["bound"] == "low"]
        hi = g[g["bound"] == "high"]
        full = sens[(sens["parameter"] == parameter)]
        rows.append({
            "parameter": parameter,
            "value_low": float(lo["value"].iloc[0]) if len(lo) else np.nan,
            "value_high": float(hi["value"].iloc[0]) if len(hi) else np.nan,
            "beta_all_central": central,
            "beta_all_low": float(lo["beta"].mean()) if len(lo) else np.nan,
            "beta_all_high": float(hi["beta"].mean()) if len(hi) else np.nan,
            "max_abs_delta": float(full["delta_beta"].abs().max()),
        })
    return (pd.DataFrame(rows).sort_values("max_abs_delta", ascending=False)
            .reset_index(drop=True))


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
_FLOAT = "{:.8f}"


def export_sensitivity(path: Path | str | None = None, **kw) -> Path:
    """Write :func:`sensitivity_table` to ``path`` (default ``config.BETA_SENSITIVITY_CSV``).

    Deterministic: fixed float formats, LF line endings, sorted rows.
    """
    path = Path(path) if path is not None else config.BETA_SENSITIVITY_CSV
    sens = sensitivity_table(**kw)
    lines = [",".join(SENSITIVITY_COLUMNS)]
    for r in sens.itertuples(index=False):
        lines.append(",".join([
            r.parameter, r.bound, "" if pd.isna(r.value) else f"{r.value:g}",
            str(int(r.year)), r.species, _FLOAT.format(r.beta), _FLOAT.format(r.delta_beta),
        ]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def load_sensitivity(path: Path | str | None = None) -> pd.DataFrame | None:
    """The committed sensitivity table, or ``None`` if it has not been exported."""
    path = Path(path) if path is not None else config.BETA_SENSITIVITY_CSV
    if not path.exists():
        return None
    return pd.read_csv(path, keep_default_na=True)


def load_bounds(path: Path | str | None = None) -> pd.DataFrame | None:
    """β bounds from the committed sensitivity table, or ``None`` without one."""
    sens = load_sensitivity(path)
    if sens is None or sens.empty:
        return None
    return beta_bounds(sens)


__all__ = [
    "DENSITY_PARAMETER",
    "KNOWN_PARAMETERS",
    "RANGE_COLUMNS",
    "SENSITIVITY_COLUMNS",
    "beta_bounds",
    "export_sensitivity",
    "load_bounds",
    "load_ranges",
    "load_sensitivity",
    "rebuild_national",
    "resplit_firewood",
    "sensitivity_table",
    "summary_table",
]
