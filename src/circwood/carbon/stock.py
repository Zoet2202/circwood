"""Carbon stored in harvested-wood products as a *stock* — ROADMAP Phase G.

The flow table says how much carbon enters long-lived products each year.
This module turns that into the quantity climate accounting cares about: how
much of it is still stored, year by year, as products decay. First-order
decay with product-class half-lives, as in the IPCC 2006 Guidelines /
2019 Refinement (Vol. 4, Ch. 12, Eq. 12.1)::

    C(i+1) = e^(−k) · C(i) + ((1 − e^(−k)) / k) · Inflow(i),     k = ln 2 / HL

Product classes and half-lives are researcher-editable in
``config/hwp_half_lives.csv`` (IPCC defaults: sawnwood 35 y, wood-based panels
25 y, paper and paperboard 2 y). The inflows come from the conserving carbon
model (Phase G): the carbon entering ``Long-term USE`` is split into sawnwood
and panels by the composition of the inflow to ``Wood Product processing &
Logistics`` (proportional mixing — the same assumption as the species model),
and the carbon entering ``Short-term USE`` (from Pulpmills) is paper.

Two initialisations are exported, because they answer different questions:

* ``cohort`` — the stock starts at zero in the first dataset year, so the
  series is *the carbon from this window's harvest that is still stored*. It
  is the figure the paper can state without assuming anything about products
  made before 2012.
* ``steady_state`` — the stock starts at ``Inflow / k``, the level a constant
  inflow would have reached; the series then shows the *total* product stock
  and its net change, on the assumption that pre-window inflows resembled the
  first year's. Flagged as an assumption in the output.

The observed outflow — ``Long-term USE → End-of-Life`` (SSB 10513, waste wood
delivered to incineration) — is carried alongside the modelled decay so the
two can be compared. They need not agree: 10513 counts waste wood reaching
incineration in Norway, the model counts all products leaving use.

Imported products are included in the inflow (this is a *stock in Norway*
view, the IPCC stock-change approach), unlike β, which traces domestic harvest
only. Nothing here changes the flow table.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from circwood import config
from circwood import constants as C

LTUSE = "Long-term USE"
STUSE = "Short-term USE"
WPP = "Wood Product processing & Logistics"
EOL = "End-of-Life"

#: Product classes of ``config/hwp_half_lives.csv``.
SAWNWOOD = "sawnwood"
PANELS = "wood-based panels"
PAPER = "paper and paperboard"
CLASSES = (SAWNWOOD, PANELS, PAPER)

#: WPP inflow sources whose wood is panel-based; everything else into WPP is
#: sawnwood-based (sawn, planed, impregnated, imported planed, net sawn imports).
PANEL_SOURCES = ("Boardmills", "Import Boards")

METHODS = ("cohort", "steady_state")

#: Columns of ``data/processed/hwp_stock.csv``.
STOCK_COLUMNS = (
    "method", "year", "product_class", "half_life_years", "inflow_kgc",
    "stock_start_kgc", "stock_end_kgc", "decay_kgc", "net_change_kgc",
    "observed_eol_kgc",
)


def load_half_lives(path: Path | None = None) -> pd.DataFrame:
    """Read and validate ``config/hwp_half_lives.csv`` (``product_class, half_life_years, source``)."""
    path = Path(path) if path is not None else config.HWP_HALF_LIVES_FILE
    df = pd.read_csv(path)
    for col in ("product_class", "half_life_years"):
        if col not in df.columns:
            raise ValueError(f"{path.name}: missing column {col!r}.")
    missing = sorted(set(CLASSES) - set(df["product_class"]))
    if missing:
        raise ValueError(f"{path.name}: no half-life for product class(es) {missing}.")
    if (df["half_life_years"] <= 0).any():
        raise ValueError(f"{path.name}: half-lives must be positive.")
    return df.set_index("product_class")


def class_inflows(df: pd.DataFrame) -> pd.DataFrame:
    """Carbon entering use each year, by product class (kg C).

    Columns: ``year, product_class, inflow_kgc``. ``Long-term USE`` inflow is
    split sawnwood / panels by the panel share of that year's WPP inflow;
    ``Short-term USE`` inflow is paper. Needs a carbon frame.
    """
    if "carbon" not in df.columns:
        raise KeyError("class_inflows needs a carbon frame (a 'carbon' column).")
    rows = []
    for year, g in df.groupby("year"):
        wpp_in = g[g["target"] == WPP]
        total = float(wpp_in["carbon"].sum())
        panel_share = (
            float(wpp_in.loc[wpp_in["source"].isin(PANEL_SOURCES), "carbon"].sum()) / total
            if total > 0 else 0.0
        )
        lt = float(g.loc[g["target"] == LTUSE, "carbon"].sum())
        st = float(g.loc[g["target"] == STUSE, "carbon"].sum())
        rows += [
            {"year": int(year), "product_class": SAWNWOOD, "inflow_kgc": lt * (1 - panel_share)},
            {"year": int(year), "product_class": PANELS, "inflow_kgc": lt * panel_share},
            {"year": int(year), "product_class": PAPER, "inflow_kgc": st},
        ]
    return pd.DataFrame(rows)


def observed_eol(df: pd.DataFrame) -> pd.Series:
    """Carbon leaving ``Long-term USE`` to ``End-of-Life`` per year (kg C), from SSB 10513."""
    sub = df[(df["source"] == LTUSE) & (df["target"] == EOL)]
    return sub.groupby("year")["carbon"].sum()


def decay_series(inflow: pd.Series, half_life: float, initial: float) -> pd.DataFrame:
    """First-order decay of one product class.

    ``inflow`` is indexed by consecutive years (kg C per year). Returns a frame
    with ``year, inflow_kgc, stock_start_kgc, stock_end_kgc, decay_kgc,
    net_change_kgc``; ``decay`` is the carbon leaving the stock during the
    year, so ``stock_end = stock_start + inflow − decay`` exactly.
    """
    years = list(inflow.index)
    if years != list(range(years[0], years[-1] + 1)):
        raise ValueError("inflow must cover consecutive years")
    k = np.log(2.0) / float(half_life)
    e = np.exp(-k)
    stock = float(initial)
    rows = []
    for year in years:
        i = float(inflow.loc[year])
        end = e * stock + (1 - e) / k * i
        rows.append({
            "year": int(year), "inflow_kgc": i, "stock_start_kgc": stock,
            "stock_end_kgc": end, "decay_kgc": stock + i - end, "net_change_kgc": end - stock,
        })
        stock = end
    return pd.DataFrame(rows)


def hwp_stock(
    df: pd.DataFrame | None = None,
    *,
    half_lives: pd.DataFrame | None = None,
    method: str = "cohort",
    min_year: int = C.BETA_MIN_YEAR,
    max_year: int = C.BETA_MAX_YEAR,
) -> pd.DataFrame:
    """The HWP carbon stock by year and product class, plus a ``total`` row per year.

    ``df`` defaults to the committed ``carbon_data.csv``. ``method`` is
    ``"cohort"`` (stock starts at zero) or ``"steady_state"`` (starts at the
    first year's inflow divided by the decay rate). Columns: :data:`STOCK_COLUMNS`.
    The default window is β's (``BETA_MIN_YEAR``–``BETA_MAX_YEAR``), for the
    same reason β refuses 2024: the Treindustrien production series ends in
    2023, so the 2024 inflow to use is a coverage break, not a measurement.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}.")
    if df is None:
        df = pd.read_csv(config.CARBON_CSV)
    hl = load_half_lives() if half_lives is None else half_lives
    window = df[(df["year"] >= min_year) & (df["year"] <= max_year)]
    if window.empty:
        raise ValueError(f"no flows in {min_year}-{max_year}.")
    inflows = class_inflows(window)
    eol = observed_eol(window)
    years = list(range(int(window["year"].min()), int(window["year"].max()) + 1))

    frames = []
    for cls in CLASSES:
        series = (inflows[inflows["product_class"] == cls]
                  .set_index("year")["inflow_kgc"].reindex(years, fill_value=0.0))
        half = float(hl.loc[cls, "half_life_years"])
        k = np.log(2.0) / half
        initial = 0.0 if method == "cohort" else float(series.iloc[0]) / k
        out = decay_series(series, half, initial)
        out.insert(0, "product_class", cls)
        out.insert(1, "half_life_years", half)
        frames.append(out)
    table = pd.concat(frames, ignore_index=True)

    total = table.groupby("year", as_index=False)[
        ["inflow_kgc", "stock_start_kgc", "stock_end_kgc", "decay_kgc", "net_change_kgc"]
    ].sum()
    total.insert(1, "product_class", "total")
    total.insert(2, "half_life_years", np.nan)
    table = pd.concat([table, total], ignore_index=True)
    table["observed_eol_kgc"] = np.where(
        table["product_class"] == "total",
        table["year"].map(eol).fillna(0.0).astype(float), np.nan,
    )
    table.insert(0, "method", method)
    order = {c: i for i, c in enumerate((*CLASSES, "total"))}
    table["class_order"] = table["product_class"].map(order)
    return (table.sort_values(["year", "class_order"]).drop(columns="class_order")
            [list(STOCK_COLUMNS)].reset_index(drop=True))


def export_hwp_stock(path: Path | str | None = None, df: pd.DataFrame | None = None) -> Path:
    """Write both methods' stock tables to ``path`` (default ``config.HWP_STOCK_CSV``).

    ``#`` header lines state the method, units and sources, like
    ``beta_series.csv``; read with ``pandas.read_csv(path, comment="#")``.
    Deterministic output.
    """
    path = Path(path) if path is not None else config.HWP_STOCK_CSV
    if df is None:
        df = pd.read_csv(config.CARBON_CSV)
    hl = load_half_lives()
    tables = pd.concat([hwp_stock(df, half_lives=hl, method=m) for m in METHODS], ignore_index=True)
    cohort = tables[(tables["method"] == "cohort") & (tables["product_class"] == "total")]
    last = cohort.iloc[-1]
    header = [
        "Circwood WP4 - carbon stored in harvested-wood products (HWP), first-order decay, ROADMAP Phase G.",
        "Generated by circwood.carbon.stock.export_hwp_stock; do not edit by hand. Read with "
        "pandas.read_csv(path, comment='#').",
        "Units: kg C. inflow_kgc = carbon entering use in the year (Long-term USE split into sawnwood "
        "and wood-based panels by the panel share of the WPP inflow; Short-term USE = paper). "
        "stock_end = stock_start + inflow - decay; net_change = stock_end - stock_start.",
        "Model: C(i+1) = exp(-k) C(i) + (1 - exp(-k))/k * Inflow(i), k = ln2 / half-life "
        "(IPCC 2006 GL / 2019 Refinement Vol. 4 Ch. 12 Eq. 12.1). Half-lives: "
        + ", ".join(f"{c} {float(hl.loc[c, 'half_life_years']):g} y" for c in CLASSES)
        + " (config/hwp_half_lives.csv).",
        "method=cohort: the stock starts at zero in the first year, so it is the carbon from this "
        "window's inflows still stored. method=steady_state: starts at Inflow/k, the level a constant "
        "inflow would have reached (an assumption about pre-window products).",
        "observed_eol_kgc (total rows): Long-term USE -> End-of-Life from SSB 10513 (waste wood "
        "delivered to incineration), for comparison with decay_kgc; the two measure different things.",
        "Imports are included (a stock-in-Norway view); beta traces domestic harvest only. "
        "Carbon from the Phase G conserving model (config/carbon_densities.csv central densities).",
        f"Years {int(cohort['year'].min())}-{int(cohort['year'].max())}: the window beta covers "
        "(constants.BETA_MIN_YEAR/BETA_MAX_YEAR); 2024 is excluded because the Treindustrien "
        "production series ends in 2023 and the 2024 inflow to use is a coverage break.",
        f"Cohort stock at end of {int(last['year'])}: {last['stock_end_kgc'] / 1e9:.3f} Mt C from "
        f"{cohort['inflow_kgc'].sum() / 1e9:.3f} Mt C of inflow over {int(cohort['year'].min())}-"
        f"{int(cohort['year'].max())}.",
    ]
    lines = ["# " + h for h in header] + [",".join(STOCK_COLUMNS)]
    for r in tables.itertuples(index=False):
        cells = []
        for col, val in zip(STOCK_COLUMNS, r):
            if col in ("method", "product_class"):
                cells.append(str(val))
            elif col == "year":
                cells.append(str(int(val)))
            elif pd.isna(val):
                cells.append("")
            elif col == "half_life_years":
                cells.append(f"{float(val):g}")
            else:
                cells.append(f"{float(val):.1f}")
        lines.append(",".join(cells))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


__all__ = [
    "CLASSES",
    "METHODS",
    "PANEL_SOURCES",
    "STOCK_COLUMNS",
    "class_inflows",
    "decay_series",
    "export_hwp_stock",
    "hwp_stock",
    "load_half_lives",
    "observed_eol",
]
