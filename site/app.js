/* Circwood WP4 — public static site.
 *
 * Loads the single data bundle (site/data/circwood.json, built by
 * `circwood-bundle` from the committed dataset), and renders an interactive,
 * cross-filterable Sankey entirely client-side. Filtering is row selection +
 * aggregation + index remapping over ~1,900 rows — no server, no precomputed
 * figures.
 *
 * Plotly.js, deliberately (see the roadmap decision log): the shipped dataset
 * contains two-way inventory flows (e.g. Sawmills ⇄ Timber inventory in the
 * same year), which ECharts' sankey rejects as a cycle, and the hand-placed
 * node positions in config/nodes.json can only be honoured with
 * arrangement:"fixed".
 *
 * Three metrics share one code path — volume, carbon and CO₂e (carbon ×
 * units.co2_per_c, a display conversion). The carbon envelope
 * (flows.carbon_low / carbon_high) and the forest card (bundle.forest) are
 * optional: an older bundle without them renders without the range column,
 * the CO₂e button and the card.
 */

"use strict";

(function () {
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------

  let B = null; // the decoded bundle
  let years = []; // every year the dataset covers
  let eventsBound = false;
  let applyingPreset = false;

  const state = {
    metric: "volume", // 'volume' | 'carbon' | 'co2'
    mode: "avg", // 'avg' (year range) | 'year' (single year)
    y0: 2012,
    y1: 2022,
    ysel: 2022,
    species: "all",
    region: "all",
    assortment: "all",
    destination: "all", // 'all' | a code from B.destinations
    focus: [], // node labels; empty = all
    sel: null, // {type:'node', label} | {type:'link', s, t}
  };
  let DEFAULTS = null;

  const METRICS = {
    volume: { label: "Volume", unit: "Mm³", scale: 1e6 },
    carbon: { label: "Carbon", unit: "Mt C", scale: 1e9 },
    // Display conversion only: carbon × units.co2_per_c (constants.CO2_PER_C).
    co2: { label: "CO₂e", unit: "Mt CO₂", scale: 1e9 },
  };

  const PRESETS = {
    overview: { label: "Overview", patch: {} },
    sawmill: {
      label: "Sawmill chain",
      patch: {
        focus: [
          "Sawmills", "Timber inventory", "Planingmills",
          "Planed timber inventory", "Impregnation", "Impregnated inventory",
        ],
      },
    },
    carbonlt: {
      label: "Carbon to long-term use",
      patch: {
        metric: "carbon",
        focus: ["Wood Product processing & Logistics", "Long-term USE"],
      },
    },
    energy: {
      label: "Firewood & energy",
      patch: { focus: ["Firewood", "Energy", "End-of-Life"] },
    },
  };

  // ---------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------

  const cssVar = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function hexToRgba(color, alpha) {
    if (color.startsWith("#")) {
      const h = color.slice(1);
      const r = parseInt(h.slice(0, 2), 16);
      const g = parseInt(h.slice(2, 4), 16);
      const b = parseInt(h.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${alpha})`;
    }
    return color;
  }

  function fmt(v) {
    const a = Math.abs(v);
    if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    // Below display precision (values are Mm³ / Mt C): a float-epsilon
    // residual on a balanced node should read as zero, not as 1.0e-7.
    return a >= 0.005 ? v.toFixed(2) : "0.00";
  }

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.assign(node, props || {});
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  const textCell = (tag, text, cls) => {
    const c = el(tag, { textContent: text });
    if (cls) c.className = cls;
    return c;
  };

  function windowText() {
    return state.mode === "year"
      ? String(state.ysel)
      : `${state.y0}–${state.y1} average`;
  }

  /** kg CO₂ per kg C, carried by the bundle (constants.CO2_PER_C); null in older bundles. */
  function co2Factor() {
    const k = B && B.units ? Number(B.units.co2_per_c) : NaN;
    return k > 0 ? k : null;
  }

  /** Whether the bundle carries the ±10 % density envelope (carbon_low / carbon_high). */
  function hasEnvelope() {
    return !!(B && B.flows.carbon_low && B.flows.carbon_high);
  }

  /** The envelope is a property of carbon, so it shows in the carbon-based metrics only. */
  function envelopeShown() {
    return state.metric !== "volume" && hasEnvelope();
  }

  /**
   * The per-row value column for the current metric, its low/high envelope
   * columns (null without one) and the display factor k applied after
   * aggregation: 1, or co2_per_c for CO₂e.
   */
  function metricColumns() {
    const f = B.flows;
    if (state.metric === "volume") return { val: f.volume, lo: null, hi: null, k: 1 };
    const env = hasEnvelope();
    return {
      val: f.carbon,
      lo: env ? f.carbon_low : null,
      hi: env ? f.carbon_high : null,
      k: state.metric === "co2" ? co2Factor() : 1,
    };
  }

  const sumBy = (edges, key) => edges.reduce((a, e) => a + e[key], 0);
  const rangeText = (edges) => `${fmt(sumBy(edges, "lo"))}–${fmt(sumBy(edges, "hi"))}`;

  // ---------------------------------------------------------------------
  // Data: filtering and aggregation
  // ---------------------------------------------------------------------

  /** Indices of flow rows passing the current filters. */
  function filteredRows(ignoreYears) {
    const f = B.flows;
    const yLo = state.mode === "year" ? state.ysel : state.y0;
    const yHi = state.mode === "year" ? state.ysel : state.y1;
    const sp = state.species === "all" ? -1 : B.species.indexOf(state.species);
    const rg =
      state.region === "all"
        ? -1
        : B.regions.findIndex((r) => r.code === state.region);
    const as =
      state.assortment === "all" ? -1 : B.assortments.indexOf(state.assortment);
    const ds =
      state.destination === "all" || !B.destinations
        ? -1
        : B.destinations.findIndex((d) => d.code === state.destination);
    const focusIds = new Set(
      state.focus
        .map((lb) => B.nodes.findIndex((n) => n.label === lb))
        .filter((i) => i >= 0)
    );

    const keep = [];
    for (let i = 0; i < B.row_count; i++) {
      if (!ignoreYears && (f.year[i] < yLo || f.year[i] > yHi)) continue;
      if (sp >= 0 && f.species[i] !== sp) continue;
      if (rg >= 0 && f.region[i] !== rg) continue;
      if (as >= 0 && f.assortment[i] !== as) continue;
      if (ds >= 0 && f.destination[i] !== ds) continue;
      if (focusIds.size && !focusIds.has(f.source[i]) && !focusIds.has(f.target[i]))
        continue;
      keep.push(i);
    }
    return keep;
  }

  /** Number of dataset years inside the current window (1 in single-year mode). */
  function windowYearCount() {
    if (state.mode === "year") return 1;
    return years.filter((y) => y >= state.y0 && y <= state.y1).length || 1;
  }

  /**
   * Aggregate rows to one value per (source, target) in display units.
   * Mirrors viz.prepare_flows: sum per (s, t) over the window, then — in
   * average mode — divide by the number of dataset years in the window. Not
   * by the number of years the flow has data for: that rule (until Phase K)
   * drew an edge present in three of eleven years at its three-year mean, so
   * the averaged diagram did not balance even though every single year does.
   * An absent year is a zero flow.
   */
  function aggregate(rows) {
    const f = B.flows;
    const metric = METRICS[state.metric];
    const { val, lo, hi, k } = metricColumns();
    const totals = new Map(); // "s,t" -> [sum, low, high] over the window
    for (const i of rows) {
      const key = f.source[i] + "," + f.target[i];
      let t = totals.get(key);
      if (!t) totals.set(key, (t = [0, 0, 0]));
      t[0] += val[i];
      if (lo) {
        t[1] += lo[i];
        t[2] += hi[i];
      }
    }
    const n = state.mode === "avg" ? windowYearCount() : 1;
    const display = (v) => (Math.abs(v) * k) / n / metric.scale;
    const out = [];
    for (const [key, [total, low, high]] of totals) {
      // A zero-value link (the water edge carries no carbon) must not reach
      // Plotly: a node with no value makes it abandon the fixed layout.
      if (total === 0) continue;
      const [s, t] = key.split(",").map(Number);
      const e = { s, t, value: display(total) };
      if (lo) {
        // The envelope follows the same rule as the central value; abs() may
        // swap the bounds of a negative (netted) flow, so order them.
        e.lo = Math.min(display(low), display(high));
        e.hi = Math.max(display(low), display(high));
      }
      out.push(e);
    }
    return out;
  }

  /** Per-year series for one link (all years, dimension filters applied). */
  function linkSeries(sLabel, tLabel) {
    const f = B.flows;
    const sId = B.nodes.findIndex((n) => n.label === sLabel);
    const tId = B.nodes.findIndex((n) => n.label === tLabel);
    const metric = METRICS[state.metric];
    const { val, k } = metricColumns();
    const byYear = new Map(years.map((y) => [y, 0]));
    for (const i of filteredRows(true)) {
      if (f.source[i] === sId && f.target[i] === tId)
        byYear.set(f.year[i], byYear.get(f.year[i]) + val[i]);
    }
    return years.map((y) => (Math.abs(byYear.get(y)) * k) / metric.scale);
  }

  /** Per-year inflow/outflow series for one node. */
  function nodeSeries(label) {
    const f = B.flows;
    const id = B.nodes.findIndex((n) => n.label === label);
    const metric = METRICS[state.metric];
    const { val, k } = metricColumns();
    const inflow = new Map(years.map((y) => [y, 0]));
    const outflow = new Map(years.map((y) => [y, 0]));
    for (const i of filteredRows(true)) {
      if (f.target[i] === id) inflow.set(f.year[i], inflow.get(f.year[i]) + val[i]);
      if (f.source[i] === id) outflow.set(f.year[i], outflow.get(f.year[i]) + val[i]);
    }
    return {
      inflow: years.map((y) => (inflow.get(y) * k) / metric.scale),
      outflow: years.map((y) => (outflow.get(y) * k) / metric.scale),
    };
  }

  // ---------------------------------------------------------------------
  // Rendering — Sankey
  // ---------------------------------------------------------------------

  function chartLayoutBase() {
    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: {
        family: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        size: 13,
        color: cssVar("--ink"),
      },
      margin: { l: 10, r: 10, t: 10, b: 10 },
    };
  }

  function selInvolvesNode(label) {
    if (!state.sel) return true;
    if (state.sel.type === "node") return state.sel.label === label;
    return state.sel.s === label || state.sel.t === label;
  }

  function linkAlpha(sLabel, tLabel) {
    if (!state.sel) return 0.4;
    if (state.sel.type === "node")
      return sLabel === state.sel.label || tLabel === state.sel.label ? 0.55 : 0.08;
    return sLabel === state.sel.s && tLabel === state.sel.t ? 0.65 : 0.08;
  }

  function renderSankey() {
    const metric = METRICS[state.metric];
    const agg = aggregate(filteredRows(false));

    const used = new Set();
    agg.forEach((e) => {
      used.add(e.s);
      used.add(e.t);
    });
    const order = [...used].sort((a, b) => a - b);
    const local = new Map(order.map((g, i) => [g, i]));
    const nodes = order.map((g) => B.nodes[g]);

    const nodeColors = nodes.map((n) =>
      state.sel && !selInvolvesNode(n.label) ? hexToRgba(n.color, 0.3) : n.color
    );
    const linkColors = agg.map((e) =>
      hexToRgba(B.nodes[e.s].color, linkAlpha(B.nodes[e.s].label, B.nodes[e.t].label))
    );

    // In the carbon metrics the node hover carries the envelope of the side
    // Plotly sizes the node by (the larger of inflow and outflow).
    const showEnv = envelopeShown();
    const nodeRange = showEnv
      ? order.map((g) => {
          const inn = agg.filter((e) => e.t === g);
          const out = agg.filter((e) => e.s === g);
          return rangeText(sumBy(inn, "value") >= sumBy(out, "value") ? inn : out);
        })
      : undefined;
    const nodeHover =
      "%{label}<br>Total: %{value:.2f} " +
      metric.unit +
      (showEnv ? "<br>Envelope: %{customdata} " + metric.unit : "") +
      "<extra></extra>";

    const trace = {
      type: "sankey",
      arrangement: "fixed",
      valueformat: ".2f",
      valuesuffix: " " + metric.unit,
      textfont: { color: cssVar("--ink"), size: 12 },
      node: {
        label: nodes.map((n) => n.label),
        x: nodes.map((n) => n.x),
        y: nodes.map((n) => n.y),
        color: nodeColors,
        customdata: nodeRange,
        pad: 22,
        thickness: 18,
        line: { color: "rgba(0,0,0,0.25)", width: 0.5 },
        hovertemplate: nodeHover,
      },
      link: {
        source: agg.map((e) => local.get(e.s)),
        target: agg.map((e) => local.get(e.t)),
        value: agg.map((e) => e.value),
        color: linkColors,
        hovertemplate:
          "%{source.label} → %{target.label}<br>%{value:.2f} " +
          metric.unit +
          "<extra></extra>",
      },
    };

    const layout = Object.assign(chartLayoutBase(), { height: 620 });
    Plotly.react($("sankey"), [trace], layout, {
      responsive: true,
      displayModeBar: false,
    }).then(() => {
      if (!eventsBound) {
        eventsBound = true;
        $("sankey").on("plotly_click", handleSankeyClick);
      }
    });
  }

  function handleSankeyClick(ev) {
    const p = ev.points && ev.points[0];
    if (!p) return;
    let next = null;
    if (p.source && p.target && p.source.label !== undefined) {
      next = { type: "link", s: p.source.label, t: p.target.label };
    } else if (typeof p.label === "string") {
      next = { type: "node", label: p.label };
    }
    if (!next) return;
    const same =
      state.sel &&
      state.sel.type === next.type &&
      state.sel.label === next.label &&
      state.sel.s === next.s &&
      state.sel.t === next.t;
    state.sel = same ? null : next;
    renderAll();
  }

  // ---------------------------------------------------------------------
  // Rendering — companion time series and balance table
  // ---------------------------------------------------------------------

  function windowShade() {
    const lo = state.mode === "year" ? state.ysel : state.y0;
    const hi = state.mode === "year" ? state.ysel : state.y1;
    return {
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: lo - 0.5,
      x1: hi + 0.5,
      y0: 0,
      y1: 1,
      fillcolor: hexToRgba("#898781", 0.1),
      line: { width: 0 },
      layer: "below",
    };
  }

  function tsLayout(unit) {
    return Object.assign(chartLayoutBase(), {
      height: 300,
      margin: { l: 50, r: 12, t: 8, b: 34 },
      hovermode: "x unified",
      xaxis: {
        tickmode: "linear",
        dtick: 2,
        gridcolor: cssVar("--grid"),
        zeroline: false,
        tickfont: { color: cssVar("--muted"), size: 12 },
        showspikes: true,
        spikemode: "across",
        spikethickness: 1,
        spikecolor: cssVar("--baseline"),
        spikedash: "solid",
      },
      yaxis: {
        title: { text: unit, font: { color: cssVar("--ink-2"), size: 12 } },
        rangemode: "tozero",
        gridcolor: cssVar("--grid"),
        zeroline: false,
        tickfont: { color: cssVar("--muted"), size: 12 },
      },
      legend: {
        orientation: "h",
        y: 1.12,
        x: 0,
        font: { color: cssVar("--ink-2") },
      },
      shapes: [windowShade()],
    });
  }

  function renderCompanion() {
    const box = $("companion");
    if (!state.sel) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const metric = METRICS[state.metric];
    const traces = [];

    if (state.sel.type === "link") {
      $("ts-title").textContent =
        `${state.sel.s} → ${state.sel.t} by year (${metric.unit})`;
      traces.push({
        type: "scatter",
        mode: "lines+markers",
        x: years,
        y: linkSeries(state.sel.s, state.sel.t),
        name: "Flow",
        showlegend: false,
        line: { color: cssVar("--series-1"), width: 2 },
        marker: { size: 6 },
        hovertemplate: "%{y:.2f} " + metric.unit + "<extra></extra>",
      });
    } else {
      $("ts-title").textContent =
        `${state.sel.label} — inflow and outflow by year (${metric.unit})`;
      const s = nodeSeries(state.sel.label);
      traces.push(
        {
          type: "scatter",
          mode: "lines+markers",
          x: years,
          y: s.inflow,
          name: "Inflow",
          line: { color: cssVar("--series-1"), width: 2 },
          marker: { size: 6 },
          hovertemplate: "%{y:.2f} " + metric.unit + "<extra></extra>",
        },
        {
          type: "scatter",
          mode: "lines+markers",
          x: years,
          y: s.outflow,
          name: "Outflow",
          line: { color: cssVar("--series-2"), width: 2 },
          marker: { size: 6 },
          hovertemplate: "%{y:.2f} " + metric.unit + "<extra></extra>",
        }
      );
    }
    Plotly.react($("timeseries"), traces, tsLayout(metric.unit), {
      responsive: true,
      displayModeBar: false,
    });
    renderBalanceTable();
  }

  function renderBalanceTable() {
    const holder = $("balance-table");
    holder.textContent = "";
    const metric = METRICS[state.metric];

    if (state.sel.type === "link") {
      $("table-title").textContent =
        `${state.sel.s} → ${state.sel.t} — values by year`;
      const values = linkSeries(state.sel.s, state.sel.t);
      const body = el("tbody", {}, years.map((y, i) =>
        el("tr", {}, [
          textCell("td", String(y)),
          textCell("td", fmt(values[i]), "num"),
        ])
      ));
      holder.appendChild(
        el("table", {}, [
          el("thead", {}, [
            el("tr", {}, [
              textCell("th", "Year"),
              textCell("th", metric.unit, "num"),
            ]),
          ]),
          body,
        ])
      );
      return;
    }

    const label = state.sel.label;
    $("table-title").textContent = `${label} — balance, ${windowText()}`;
    const id = B.nodes.findIndex((n) => n.label === label);
    const agg = aggregate(filteredRows(false));
    const inflows = agg.filter((e) => e.t === id);
    const outflows = agg.filter((e) => e.s === id);
    const totalIn = sumBy(inflows, "value");
    const totalOut = sumBy(outflows, "value");
    // The carbon metrics add a low–high column from the ±10 % density
    // envelope, aggregated by the same rule as the value, on the total rows.
    const showEnv = envelopeShown();
    const row = (cells, range, cls) => {
      if (showEnv) {
        const c = textCell("td", range, "num range");
        if (range) c.title = "Low–high of the ±10 % density envelope (config/carbon_densities.csv)";
        cells.push(c);
      }
      return el("tr", cls ? { className: cls } : {}, cells);
    };

    const rows = [];
    inflows
      .sort((a, b) => b.value - a.value)
      .forEach((e) =>
        rows.push(row([
          textCell("td", "In"),
          textCell("td", B.nodes[e.s].label),
          textCell("td", fmt(e.value), "num"),
        ], ""))
      );
    outflows
      .sort((a, b) => b.value - a.value)
      .forEach((e) =>
        rows.push(row([
          textCell("td", "Out"),
          textCell("td", B.nodes[e.t].label),
          textCell("td", fmt(e.value), "num"),
        ], ""))
      );
    [
      ["Total in", totalIn, showEnv ? rangeText(inflows) : ""],
      ["Total out", totalOut, showEnv ? rangeText(outflows) : ""],
      ["Balance (in − out)", totalIn - totalOut, ""],
    ].forEach(([name, v, range]) => {
      rows.push(row([
        textCell("td", ""),
        textCell("td", name),
        textCell("td", fmt(v), "num"),
      ], range, "total"));
    });

    const head = [
      textCell("th", ""),
      textCell("th", "Counterpart"),
      textCell("th", metric.unit, "num"),
    ];
    if (showEnv) head.push(textCell("th", "Low–high", "num"));
    holder.appendChild(
      el("table", {}, [el("thead", {}, [el("tr", {}, head)]), el("tbody", {}, rows)])
    );
  }

  // ---------------------------------------------------------------------
  // Rendering — the forest-resource card (Phase H)
  //
  // National figures at the latest NFI interval, independent of the filters:
  // the inventory has no calendar year, county or assortment to filter on.
  // Absent from older bundles, in which case the card stays hidden.
  // ---------------------------------------------------------------------

  const M3_PER_MM3 = 1e6;
  const SPECIES_NAMES = {
    all: "All species",
    spruce: "Spruce",
    pine: "Pine",
    "non-coniferous": "Non-coniferous",
  };
  const enDash = (s) => String(s).replace("-", "–");
  const fmtPct = (p) => (Number.isInteger(p) ? String(p) : p.toFixed(1)) + " %";

  function tile(label, value, sub) {
    return el("div", { className: "tile" }, [
      textCell("div", label, "label"),
      textCell("div", value, "value"),
      textCell("div", sub, "sub"),
    ]);
  }

  function renderForest() {
    const card = $("forest-card");
    const F = B.forest;
    if (!F || !F.standing_volume_m3 || !F.increment_m3_per_year) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    const interval = enDash(F.interval);
    const byRow = new Map((F.harvest_vs_increment || []).map((r) => [r.species, r]));
    const all = byRow.get("all") || {};
    const hwin = all.harvest_window ? enDash(all.harvest_window) : "the paper window";
    const mm3 = (v) => fmt(v / M3_PER_MM3);

    $("forest-title").textContent =
      `Forest resource — National Forest Inventory, ${interval} interval`;

    const tiles = $("forest-tiles");
    tiles.textContent = "";
    tiles.append(
      tile("Standing volume", `${mm3(F.standing_volume_m3.all)} Mm³`,
        "under bark, productive forestry land"),
      tile("Annual gross increment", `${mm3(F.increment_m3_per_year.all)} Mm³/yr`,
        "under bark, productive forestry land"),
      tile("Productive forestry land", `${fmt(F.productive_forestry_land_km2)} km²`,
        `excludes ${fmt(F.protected_productive_km2)} km² protected productive forest`),
      tile("Roundwood harvest",
        all.harvest_mean_m3 == null ? "–" : `${mm3(all.harvest_mean_m3)} Mm³/yr`,
        `Harvested wood edges, calendar-year mean ${hwin}`)
    );

    // Per-species table, "All species" last as the total row. The ratio is
    // the harvest over the increment shown beside it (productive forestry
    // land), so a reader can reproduce it from the row.
    const species = ["spruce", "pine", "non-coniferous", "all"].filter(
      (sp) => sp in F.standing_volume_m3 && sp in F.increment_m3_per_year
    );
    const body = species.map((sp) => {
      const r = byRow.get(sp) || {};
      const inc = F.increment_m3_per_year[sp];
      const harvest = r.harvest_mean_m3;
      const ratio = harvest != null && inc > 0 ? harvest / inc : null;
      return el("tr", sp === "all" ? { className: "total" } : {}, [
        textCell("td", SPECIES_NAMES[sp] || sp),
        textCell("td", mm3(F.standing_volume_m3[sp]), "num"),
        textCell("td", mm3(inc), "num"),
        textCell("td", harvest == null ? "–" : mm3(harvest), "num"),
        textCell("td", ratio == null ? "–" : ratio.toFixed(2), "num"),
      ]);
    });
    const holder = $("forest-table");
    holder.textContent = "";
    holder.appendChild(
      el("table", {}, [
        el("thead", {}, [
          el("tr", {}, [
            textCell("th", "Species"),
            textCell("th", "Standing volume (Mm³)", "num"),
            textCell("th", "Increment (Mm³/yr)", "num"),
            textCell("th", `Harvest ${hwin} (Mm³/yr)`, "num"),
            textCell("th", "Harvest ÷ increment", "num"),
          ]),
        ]),
        el("tbody", {}, body),
      ])
    );

    renderDevelopmentClasses(F, interval);

    const totalInc = all.increment_total_m3;
    const totalRatio = all.harvest_to_increment;
    $("forest-caption").textContent =
      `The National Forest Inventory (Landsskogtakseringen) is a rolling five-year ` +
      `inventory — these figures are its ${interval} interval, not a calendar year — ` +
      `measured under bark on productive forestry land, which excludes protected forest. ` +
      `The harvest is the calendar-year mean of the Harvested wood edges over ${hwin}, ` +
      `and its firewood component is measured by SSB with bark. The ratio is therefore ` +
      `for orientation, not a fence.` +
      (totalInc && totalRatio != null
        ? ` Against the gross increment on all forest land (${mm3(totalInc)} Mm³/yr, ` +
          `incl. other land) the national ratio is ${totalRatio.toFixed(2)}.`
        : "");
  }

  function renderDevelopmentClasses(F, interval) {
    const dc = (F.development_classes || []).filter((d) => d.percent != null);
    const box = $("devclass");
    const title = $("devclass-title");
    box.hidden = title.hidden = !dc.length;
    if (!dc.length) return;
    title.textContent =
      `Development classes — share of productive forestry land, ${interval} (%)`;
    // One series, one colour (slot 1). The tick labels keep the class numeral
    // and SSB's short name; the hover carries the full label.
    const short = dc.map((d) => String(d.label).replace(/^Development class\s+/, ""));
    box.setAttribute(
      "aria-label",
      "Share of productive forestry land by development class: " +
        dc.map((d, i) => `${short[i]} ${fmtPct(d.percent)}`).join(", ")
    );
    const trace = {
      type: "bar",
      orientation: "h",
      x: dc.map((d) => d.percent),
      y: short,
      customdata: dc.map((d) => d.label),
      text: dc.map((d) => fmtPct(d.percent)),
      textposition: "outside",
      cliponaxis: false,
      textfont: { color: cssVar("--ink-2"), size: 12 },
      marker: { color: cssVar("--series-1"), cornerradius: 4 },
      width: 0.6,
      hovertemplate: "%{customdata}<br>%{x} % of productive forestry land<extra></extra>",
    };
    const layout = Object.assign(chartLayoutBase(), {
      height: 230,
      margin: { l: 10, r: 48, t: 4, b: 8 },
      showlegend: false,
      xaxis: {
        range: [0, Math.max(...trace.x) * 1.25],
        showgrid: false,
        zeroline: false,
        showticklabels: false,
        fixedrange: true,
      },
      yaxis: {
        autorange: "reversed",
        automargin: true,
        tickfont: { color: cssVar("--ink-2"), size: 12 },
        fixedrange: true,
      },
    });
    Plotly.react(box, [trace], layout, { responsive: true, displayModeBar: false });
  }

  // ---------------------------------------------------------------------
  // Rendering — the current-view data table (the no-hover fallback)
  // ---------------------------------------------------------------------

  function renderFlowsTable() {
    const holder = $("flows-table");
    holder.textContent = "";
    const metric = METRICS[state.metric];
    const agg = aggregate(filteredRows(false)).sort((a, b) => b.value - a.value);
    holder.appendChild(
      el("table", {}, [
        el("thead", {}, [
          el("tr", {}, [
            textCell("th", "Source"),
            textCell("th", "Target"),
            textCell("th", `${metric.label} (${metric.unit}), ${windowText()}`, "num"),
          ]),
        ]),
        el("tbody", {}, agg.map((e) =>
          el("tr", {}, [
            textCell("td", B.nodes[e.s].label),
            textCell("td", B.nodes[e.t].label),
            textCell("td", fmt(e.value), "num"),
          ])
        )),
      ])
    );
  }

  // ---------------------------------------------------------------------
  // Captions, URL state, presets
  // ---------------------------------------------------------------------

  function renderCaptions() {
    const metric = METRICS[state.metric];
    const bits = [`${metric.label} (${metric.unit})`, windowText()];
    if (state.species !== "all") bits.push(state.species);
    if (state.region !== "all") {
      const r = B.regions.find((r) => r.code === state.region);
      bits.push(r ? r.label : state.region);
    }
    if (state.assortment !== "all") bits.push(state.assortment);
    if (state.destination !== "all") {
      const d = (B.destinations || []).find((d) => d.code === state.destination);
      bits.push("to " + (d ? d.label : state.destination));
    }
    if (state.focus.length) bits.push(`${state.focus.length} node(s) in focus`);
    $("sankey-title").textContent = bits.join(" · ");
    $("region-note").hidden = state.region === "all";
    $("destination-note").hidden = state.destination === "all";
    $("envelope-note").hidden = !envelopeShown();
  }

  function renderVintage() {
    const v = B.vintage;
    let line;
    if (v && v.data_as_of) {
      line =
        `Data as of ${v.data_as_of} (oldest source table ${v.oldest_table_as_of})` +
        ` · dataset ${B.year_min}–${B.year_max}` +
        ` · bundle built ${String(B.generated_at).slice(0, 10)}`;
    } else {
      line = `Dataset ${B.year_min}–${B.year_max} · data vintage unknown (no manifest)`;
    }
    $("vintage-line").textContent = line;
  }

  function stateToURL() {
    const p = new URLSearchParams();
    if (state.metric !== DEFAULTS.metric) p.set("m", state.metric);
    if (state.mode !== DEFAULTS.mode) p.set("a", state.mode);
    if (state.mode === "year") {
      if (state.ysel !== DEFAULTS.ysel) p.set("y", state.ysel);
    } else {
      if (state.y0 !== DEFAULTS.y0) p.set("y0", state.y0);
      if (state.y1 !== DEFAULTS.y1) p.set("y1", state.y1);
    }
    if (state.species !== "all") p.set("sp", state.species);
    if (state.region !== "all") p.set("rg", state.region);
    if (state.assortment !== "all") p.set("as", state.assortment);
    if (state.destination !== "all") p.set("ds", state.destination);
    if (state.focus.length) p.set("f", state.focus.join("~"));
    if (state.sel)
      p.set(
        "sel",
        state.sel.type === "node"
          ? "n~" + state.sel.label
          : "l~" + state.sel.s + "~" + state.sel.t
      );
    const q = p.toString();
    history.replaceState(null, "", q ? "?" + q : location.pathname);
  }

  function stateFromURL() {
    const p = new URLSearchParams(location.search);
    const year = (v, dflt) => {
      const n = parseInt(v, 10);
      return years.includes(n) ? n : dflt;
    };
    const known = (v, list, dflt) => (list.includes(v) ? v : dflt);
    if (p.get("m")) state.metric = known(p.get("m"), Object.keys(METRICS), "volume");
    if (state.metric === "co2" && !co2Factor()) state.metric = "carbon"; // older bundle
    if (p.get("a")) state.mode = known(p.get("a"), ["avg", "year"], "avg");
    state.y0 = year(p.get("y0"), state.y0);
    state.y1 = year(p.get("y1"), state.y1);
    state.ysel = year(p.get("y"), state.ysel);
    if (state.y0 > state.y1) [state.y0, state.y1] = [state.y1, state.y0];
    const labels = B.nodes.map((n) => n.label);
    if (p.get("sp")) state.species = known(p.get("sp"), B.species, "all");
    if (p.get("rg"))
      state.region = known(p.get("rg"), B.regions.map((r) => r.code), "all");
    if (p.get("as")) state.assortment = known(p.get("as"), B.assortments, "all");
    if (p.get("ds"))
      state.destination = known(
        p.get("ds"),
        (B.destinations || []).map((d) => d.code),
        "all"
      );
    if (p.get("f"))
      state.focus = p.get("f").split("~").filter((lb) => labels.includes(lb));
    const sel = p.get("sel");
    if (sel) {
      const parts = sel.split("~");
      if (parts[0] === "n" && labels.includes(parts[1]))
        state.sel = { type: "node", label: parts[1] };
      if (parts[0] === "l" && labels.includes(parts[1]) && labels.includes(parts[2]))
        state.sel = { type: "link", s: parts[1], t: parts[2] };
    }
  }

  function applyPreset(id) {
    const preset = PRESETS[id];
    if (!preset) return;
    applyingPreset = true;
    Object.assign(state, JSON.parse(JSON.stringify(DEFAULTS)), preset.patch);
    syncControls();
    renderAll();
    applyingPreset = false;
  }

  function markCustom() {
    if (!applyingPreset) $("preset").value = "custom";
  }

  // ---------------------------------------------------------------------
  // Controls
  // ---------------------------------------------------------------------

  function fillSelect(select, options, value) {
    select.textContent = "";
    options.forEach(([v, label]) =>
      select.appendChild(el("option", { value: v, textContent: label }))
    );
    select.value = value;
  }

  function buildControls() {
    // CO₂e needs the bundle's conversion factor; an older bundle hides the button.
    $("metric-co2").hidden = !co2Factor();
    fillSelect(
      $("preset"),
      Object.entries(PRESETS)
        .map(([id, p]) => [id, p.label])
        .concat([["custom", "Custom"]]),
      "overview"
    );
    const yearOpts = years.map((y) => [String(y), String(y)]);
    fillSelect($("year0"), yearOpts, String(state.y0));
    fillSelect($("year1"), yearOpts, String(state.y1));
    fillSelect($("yearSel"), yearOpts, String(state.ysel));
    fillSelect(
      $("species"),
      [["all", "All species"]].concat(B.species.map((s) => [s, s])),
      state.species
    );
    fillSelect(
      $("region"),
      [["all", "All regions"]].concat(B.regions.map((r) => [r.code, r.label])),
      state.region
    );
    fillSelect(
      $("assortment"),
      [["all", "All assortments"]].concat(
        B.assortments.filter((a) => a !== "all").map((a) => [a, a])
      ),
      state.assortment
    );
    // Destination: "domestic" first, then partner countries by exported
    // volume, then the unpublished residual — the bundle's own order.
    fillSelect(
      $("destination"),
      [["all", "All destinations"]].concat(
        (B.destinations || []).map((d) => [d.code, d.label])
      ),
      state.destination
    );

    // Node-focus dropdown: checkboxes in nodes.json (diagram) order.
    const menu = $("focus-menu");
    menu.textContent = "";
    B.nodes.forEach((n) => {
      const cb = el("input", { type: "checkbox", value: n.label });
      cb.checked = state.focus.includes(n.label);
      cb.addEventListener("change", () => {
        state.focus = [...menu.querySelectorAll("input:checked")].map(
          (x) => x.value
        );
        markCustom();
        renderAll();
      });
      menu.appendChild(el("label", {}, [cb, textCell("span", n.label)]));
    });
    const clear = el("button", { type: "button", textContent: "Clear" });
    clear.addEventListener("click", () => {
      state.focus = [];
      menu.querySelectorAll("input").forEach((x) => (x.checked = false));
      markCustom();
      renderAll();
    });
    menu.appendChild(el("div", { className: "menu-actions" }, [clear]));
  }

  function syncControls() {
    $("metric-volume").setAttribute("aria-pressed", state.metric === "volume");
    $("metric-carbon").setAttribute("aria-pressed", state.metric === "carbon");
    $("metric-co2").setAttribute("aria-pressed", state.metric === "co2");
    $("mode-avg").setAttribute("aria-pressed", state.mode === "avg");
    $("mode-year").setAttribute("aria-pressed", state.mode === "year");
    $("range-ctl").hidden = state.mode === "year";
    $("single-ctl").hidden = state.mode !== "year";
    $("year0").value = String(state.y0);
    $("year1").value = String(state.y1);
    $("yearSel").value = String(state.ysel);
    $("species").value = state.species;
    $("region").value = state.region;
    $("assortment").value = state.assortment;
    $("destination").value = state.destination;
    $("focus-summary").textContent = state.focus.length
      ? `Nodes: ${state.focus.length}`
      : "Nodes: all";
    $("focus-menu")
      .querySelectorAll("input[type=checkbox]")
      .forEach((cb) => (cb.checked = state.focus.includes(cb.value)));
  }

  function wireControls() {
    $("preset").addEventListener("change", (e) => {
      if (e.target.value !== "custom") applyPreset(e.target.value);
    });
    $("metric-volume").addEventListener("click", () => setState({ metric: "volume" }));
    $("metric-carbon").addEventListener("click", () => setState({ metric: "carbon" }));
    $("metric-co2").addEventListener("click", () => setState({ metric: "co2" }));
    $("mode-avg").addEventListener("click", () => setState({ mode: "avg" }));
    $("mode-year").addEventListener("click", () =>
      setState({ mode: "year", ysel: state.y1 })
    );
    $("year0").addEventListener("change", (e) => {
      const y = parseInt(e.target.value, 10);
      setState({ y0: Math.min(y, state.y1), y1: Math.max(y, state.y1) });
    });
    $("year1").addEventListener("change", (e) => {
      const y = parseInt(e.target.value, 10);
      setState({ y0: Math.min(y, state.y0), y1: Math.max(y, state.y0) });
    });
    $("yearSel").addEventListener("change", (e) =>
      setState({ ysel: parseInt(e.target.value, 10) })
    );
    $("species").addEventListener("change", (e) => setState({ species: e.target.value }));
    $("region").addEventListener("change", (e) => setState({ region: e.target.value }));
    $("assortment").addEventListener("change", (e) =>
      setState({ assortment: e.target.value })
    );
    $("destination").addEventListener("change", (e) =>
      setState({ destination: e.target.value })
    );
    $("reset").addEventListener("click", () => {
      $("preset").value = "overview";
      applyPreset("overview");
    });
    $("clear-sel").addEventListener("click", () => {
      state.sel = null;
      renderAll();
    });
    $("copy-link").addEventListener("click", () => {
      navigator.clipboard.writeText(location.href).then(() => {
        const b = $("copy-link");
        const original = b.textContent;
        b.textContent = "Copied!";
        setTimeout(() => (b.textContent = original), 1200);
      });
    });
    $("export-png").addEventListener("click", () => exportImage("png"));
    $("export-svg").addEventListener("click", () => exportImage("svg"));

    // Close the focus dropdown on outside click.
    document.addEventListener("click", (e) => {
      const dd = $("focus-dd");
      if (dd.open && !dd.contains(e.target)) dd.open = false;
    });
  }

  function setState(patch) {
    Object.assign(state, patch);
    markCustom();
    syncControls();
    renderAll();
  }

  function exportImage(format) {
    const metric = METRICS[state.metric];
    const name = `circwood-${state.metric}-${
      state.mode === "year" ? state.ysel : state.y0 + "-" + state.y1
    }`;
    Plotly.downloadImage($("sankey"), {
      format,
      width: 1400,
      height: 760,
      scale: format === "png" ? 2 : 1,
      filename: name,
    });
  }

  // ---------------------------------------------------------------------
  // Theme
  // ---------------------------------------------------------------------

  function initTheme() {
    const order = ["auto", "light", "dark"];
    const stored = localStorage.getItem("circwood-theme");
    let pref = order.includes(stored) ? stored : "auto";
    const label = () => ($("theme-toggle").textContent = "Theme: " + pref);
    label();
    $("theme-toggle").addEventListener("click", () => {
      pref = order[(order.indexOf(pref) + 1) % order.length];
      if (pref === "auto") {
        delete document.documentElement.dataset.theme;
        localStorage.removeItem("circwood-theme");
      } else {
        document.documentElement.dataset.theme = pref;
        localStorage.setItem("circwood-theme", pref);
      }
      label();
      if (B) renderAll();
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (B && pref === "auto") renderAll();
    });
  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------

  function renderAll() {
    renderCaptions();
    renderSankey();
    renderCompanion();
    renderForest();
    renderFlowsTable();
    stateToURL();
  }

  function init(bundle) {
    B = bundle;
    years = [];
    for (let y = B.year_min; y <= B.year_max; y++) years.push(y);

    state.y0 = B.paper_window ? B.paper_window[0] : B.year_min;
    state.y1 = B.paper_window ? B.paper_window[1] : B.year_max;
    state.ysel = state.y1;
    DEFAULTS = JSON.parse(JSON.stringify(state));

    stateFromURL();
    buildControls();
    wireControls();
    syncControls();
    renderVintage();
    renderAll();
  }

  function showError(err) {
    const box = $("load-error");
    box.hidden = false;
    box.textContent =
      "Could not load the data bundle (data/circwood.json). " +
      "If you are running locally, build it with `circwood-bundle` and serve " +
      "the site over HTTP (e.g. `python -m http.server -d site`). " +
      String(err);
    $("vintage-line").textContent = "Data failed to load.";
  }

  initTheme();
  fetch("data/circwood.json")
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(init)
    .catch(showError);
})();
