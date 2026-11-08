# Context — Circwood WP4

Archive key: PLATFORM-2026-CIRCWOOD-WP4

The inside view of the project for whoever opens it next. The binding rules
for working here are in [CLAUDE.md](CLAUDE.md); the plan is
[ROADMAP.md](ROADMAP.md); what was done and why is [CHANGELOG.md](CHANGELOG.md).

## What this is

A reproducible material- and carbon-flow analysis of Norwegian wood,
2012–2024, from roundwood removals through sawmills, pulp mills and board
mills to use, energy and export, built from Statistics Norway tables and the
Treindustrien annual report, published as an interactive Sankey, and the code
and data behind Modaresi & Landaas, *Wood extraction and utilisation in Norway
2012–2022: a national material and carbon flow analysis*, Wood Material
Science & Engineering (2026).

## Where the work stands (2026-11-08)

Every roadmap phase is delivered: hardened ingestion and a gated monthly
refresh (A, B, J), the filter dimensions and the public static site (C, D),
confidentiality guardrails (E), the empirical HWP fraction β with its
sensitivity envelope (F, G), a carbon-conserving carbon model and an HWP stock
(G), the forest-inventory layer (H), reproducibility files (I), and the
economic layer with the rotation and stock-projection models (L). The dataset
vintage is SSB data as of 2026-10-15. The last session ran under a whole-job
handover and left its work uncommitted for the owner's review; the ROADMAP
decision log of 2026-11-08 lists each decision it took.

## What is decided

- The committed CSVs are the canonical artifact; the static site is the
  public deployment; Streamlit is the internal tool.
- β is production-basis and traces conserved carbon; 2024 stays in the
  dataset although Treindustrien ends at 2023.
- The forward models live in `models/`, outside the package, exchanging
  files; the public site shows published statistics and the carbon envelope,
  not model results, until the owner has reviewed them.

## What is next (human)

Review and commit the 2026-11-08 change set; choose a licence; mint the
Zenodo DOI; replace the indicative sensitivity ranges and price assumptions
with sourced ones; simulate a SiTree stand at a realistic density if that
comparison is to carry weight; decide whether the model results go on the
public site.
