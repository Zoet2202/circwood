# Data confidentiality rules

Written 2026-07-28 (Phase E), **before any member-level data exists in this
repository** — deliberately, because the failure mode these rules prevent is
unrecoverable: once a member's confidential volumes have been shipped in a
public bundle, they cannot be unshipped.

**Scope.** "Member-level data" is anything attributable to a single company
or mill: any flow row whose `entity_id` is not `"national"`, and any raw file
obtained under a data agreement with Treindustrien or its members. Today the
dataset contains none of it — every row is `entity_id == "national"` and every
source is published statistics. These rules bind from the moment that changes.

Negotiating the data agreements themselves, and deciding what is commercially
confidential, are human tasks (see ROADMAP.md, "Out of scope for the agent").

---

## The rules

### 1. Anything shipped to the browser is public

The static site loads one data bundle and filters it client-side. That
architecture is correct for published statistics and **categorically wrong for
member data**: a single combined bundle filtered client-side means any member
— or anyone at all — can read every competitor's volumes in devtools,
regardless of what the UI displays.

Member views therefore require **per-entity bundles built server-side behind
authentication**, each containing only what that entity may see. Never
client-side filtering of a combined bundle; never "the UI hides it"; never
obfuscation. If it is in the payload, it is published.

### 2. Suppression happens at build time, not render time

No published cell may be derived from fewer than **`MIN_ENTITIES` (default 5)**
distinct contributors. A "cell" is any value a consumer can read: a flow row,
an aggregate, a benchmark statistic.

Enforcement lives at the *export* step, not the rendering step:
`circwood.export.bundle.assert_publishable` gates every frame before it is
serialised, and Phase D's bundle builder must route through it. Render-time
suppression is not a control — the unsuppressed value is still in the payload.

### 3. Re-identification via benchmark views: suppress the group, not just the cell

Small peer groups leak by subtraction. If a benchmark shows "your volume vs.
your peer group's total" and the group has three members, each member can
solve for the others from its own number. Suppressing one member's cell does
not help when the group total and the other cells are published.

So the unit of suppression is the **group**: if a peer group has fewer than
`MIN_ENTITIES` members, the whole group view is suppressed, not just the
smallest cell. The same logic applies to complementary suppression — a single
suppressed cell alongside a published total is recoverable by subtraction, so
suppressing one cell forces suppressing a second (or the total).

### 4. This repository is public

Consequences, in the same spirit as the "PDFs are never committed" rule:

- **Member-level data is never committed** — not in `data/`, not in fixtures,
  not in test cases. Real member data lives outside the repository entirely;
  tests exercise the guard with synthetic entity ids.
- **Error messages and logs never name an entity.** CI logs are public. The
  guard reports *counts* (rows, entities, under-threshold cells), never ids —
  and this property is fenced by a test.

---

## Enforcement

| Control | Where | Status |
|---|---|---|
| `assert_publishable` refuses any `entity_id != "national"` row headed for a public bundle | `src/circwood/export/bundle.py` | Implemented (Phase E) |
| `MIN_ENTITIES = 5` disclosure floor, reported per cell in the refusal | `src/circwood/export/bundle.py` | Implemented (Phase E) |
| Phase D's bundle builder routes every frame through the gate | `export.bundle` (builder lands in Phase D) | Required by this document |
| No entity id ever appears in a refusal message | fenced by `tests/test_export_guard.py` | Implemented (Phase E) |

The gate is **inert today by construction, not by being switched off**: every
row is national, so it passes everything. The first member-level row to reach
the export path raises `ConfidentialityError`.

## When member data arrives (checklist)

1. A human has negotiated and reviewed the data agreement (out of scope for
   the agent), including what may be published at what aggregation.
2. The raw data is stored **outside this repository** and reaches the pipeline
   through a path that is git-ignored and documented.
3. Per-entity or member-aggregate publication is designed as a **server-side,
   authenticated** build — a change to `export.bundle` that keeps
   `assert_publishable` on the public path and adds a deliberate, reviewed
   route for the authenticated one.
4. Any aggregation step that produces publishable cells from member rows
   enforces `MIN_ENTITIES` per cell *and* per peer group (rules 2 and 3)
   before the result is allowed to carry a publishable `entity_id`.
5. The vintage-diff and golden fences are extended to cover whatever new
   artifacts the member build produces, so a confidentiality regression is a
   visible diff, not a silent change.
