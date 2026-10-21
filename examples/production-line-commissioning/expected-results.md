# Expected Results — Production Line Commissioning (sample MSP project)

Outcomes below were derived BY RUNNING the existing application's
actual pipeline (loading → planning period → candidate discovery →
scoring → ranking → review → conflict detection) against the generated
`Production_Line_Commissioning.mpp` on a Windows machine with
Microsoft Project installed. Nothing here is hand-written speculation.

## Snapshot verified through the existing MSP integration

| Fact | Value |
|---|---|
| Opened in Microsoft Project | yes (COM automation, current format) |
| Loaded through `load_active_project()` | yes |
| Project name as read | `Production_Line_Commissioning.mpp` |
| Mappable tasks (leaf, non-summary) | 120 |
| Summary rows correctly excluded | 12 |
| Resources | 10 |
| Milestones detected | 3 (Safety sign-off, Integration test complete, Project closure) |
| Completed tasks | 24 |
| In-progress tasks (0 % < p < 100 %) | 9 |
| Not-started tasks | 87 |
| Tasks with start & finish dates | all 120 |
| Critical-flagged tasks | 34 |
| Tasks with TotalSlack reported | all 120 |
| Dependency references intact | yes (no unknown/successor/self-dependency) |
| Resource assignments resolve | yes |
| Capture warnings | empty (nothing dropped by mapping) |
| Baseline start/finish | not set (validly reported as `None`) |
| Deadlines / constraints | none (validly reported as zero) |
| Notes on tasks | 9 (matches the authored scenario) |
| File size | ~441 KB |

## Recommended planning window

**`2026-10-22` → `2026-11-06`** (12 working days).

Derived from the sample's own `default_period_bounds` logic as the
"start". A tighter window keeps the candidate list focused; the
application's suggested bounds (`min start … max finish`) span the
whole project and yield 96 candidates if used as-is — a legitimate but
broader analysis.

## The planning window (2026-10-22 → 2026-11-06) — verified results

Running the existing pipeline (`load → set_planning_period → confirm →
run_decision`, offline consequence evaluator):

- Default candidates discovered in the window: **37**.
- Ranked list produced: **37/37** tasks scored (no unscored).
- Ranking computed by the existing `score_task` / ranking
  implementation — offline consequence evaluator returns `None`, so
  Consequence of Delay is **provisional CD = 0.5** for every candidate.
- Review summary: **REVIEW_REQUIRED** with
  - `provisional_consequence: 37` (every task, offline mode),
  - `resource_conflict: 36` (of the 37 candidates),
  - `missing_schedule: 0`, `low_confidence: 0`, `unscored: 0`,
  - no validation issues.

### Verified ranking (top 5 within the window)

| Rank | Task | SP | SI | Priority index | Critical | Slack (d) | Resource conflict |
|---|---|---|---|---|---|---|---|
| 1 | Roofing & cladding | 1.000 | 1.000 | 80.00 | yes | 0.0 | yes |
| 2 | Structural steel erection | 1.000 | 0.800 | 76.00 | yes | 0.0 | yes |
| 3 | Facility readiness check | 1.000 | 0.800 | 76.00 | yes | 0.0 | yes |
| 4 | Floor slabs & resin coating | 1.000 | 0.400 | 68.00 | yes | 0.0 | yes |
| 5 | Lighting & small power | 1.000 | 0.400 | 68.00 | yes | 0.0 | yes |

(Offline mode ranks by SP/SI only; CD is the same neutral provisional
value for all tasks, so it does not differentiate them.)

### Verified scenario placements (same window)

| Scenario task | Rank | Priority index | Slack (d) | Critical | Conflict | Discovered candidate |
|---|---|---|---|---|---|---|
| Delivery: extrusion press | 11 | 59.33 | 1.4 | no | yes | yes |
| Integrate PLC with SCADA | — | — | — | — | — | no (outside window) |
| Trial production run 1 | — | — | — | — | — | no (outside window) |
| Motor connections | — | — | — | — | — | no (outside window) |
| Integrated safety inspection | — | — | — | — | — | no (outside window) |

The remaining scenario tasks land in later phases of the schedule and
appear as candidates when the user widens the planning window.

## Resource conflicts (verified)

- Whole-snapshot conflict pairs: **323** (every pair is the
  `CONFLICT` status; none are the "unknown" or "undated" statuses —
  the sample deliberately avoids undated tasks).
- Conflict pairs with **both** tasks inside the recommended candidate
  set: **88**.
- Conflict pairs by resource (within the recommended set):
  Mechanical Fitter 34, Civil Contractor 26, Electrician 18,
  Automation Engineer 6, Logistics Coordinator 4, Safety Officer 2,
  Site Engineer 1.
- Representative verified pairs (within the recommended set):

| Task A | Task B | Shared resource |
|---|---|---|
| Vendor build monitoring: press | Conveyor fabrication & shipment | Logistics Coordinator |
| Vendor build monitoring: press | Delivery: conveyor systems | Logistics Coordinator |
| MCC build & shipment | Lighting & small power | Electrician |
| MCC build & shipment | Cable trays & containment | Electrician |
| Delivery: extrusion press | Pneumatic drops to stations | Mechanical Fitter |
| Delivery: extrusion press | Chiller & HVAC install | Mechanical Fitter |
| Delivery: extrusion press | DI-water lines | Mechanical Fitter |
| Delivery: extrusion press | Compressed-air dryer & receivers | Mechanical Fitter |
| Delivery: extrusion press | Cooling tower erection | Mechanical Fitter |

This is exactly what the conflict detector is designed to surface:
shared resource + overlapping dates, calculated purely from the
captured schedule.

## What is NOT claimed

- **No live JEV call was made.** The offline consequence evaluator
  returned `None` on every task, so no Consequence of Delay number in
  these results is a live JEV answer. `scenario-data.json` documents
  the real input contract (task name + notes) and the *manual* level
  each scenario would be judged on. With a configured
  `TYPESAFE_API_KEY`, the same sample project would feed the live
  integration those exact fields.
- **No live MSP test was replaced or weakened.** The 19 pre-existing
  `pytest -m msp` integration tests were run unchanged against this
  sample and all passed.
