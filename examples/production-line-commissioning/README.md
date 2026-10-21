# Production Line Commissioning — Sample MSP Project

A self-contained sample Microsoft Project (`.mpp`) that exercises the
application's **live, read-only MSP-backed workflow** with a realistic
industrial project: commissioning a new production line at a
manufacturing site.

This is **not** the built-in Demo Mode. Demo Mode uses a small,
hard-coded fixture and needs no Microsoft Project at all. This sample
is a real Microsoft Project file — open it in Microsoft Project, make
it the active project, and analyze it through the application's own
MSP integration.

## Scenario

- **Name:** Production Line Commissioning — Sample Project
- **Phases:** 12 (Initiation → Planning → Procurement → Civil →
  Mechanical → Electrical → Utilities → Automation → Safety →
  Integration → Trial Production → Handover)
- **Leaf tasks:** 120 (plus 12 phase summary rows)
- **Resources:** 10 (Project Manager, Site Engineer, Mechanical Fitter,
  Electrician, Automation Engineer, QA Inspector, Safety Officer,
  Logistics Coordinator, Civil Contractor, Operator Trainer)
- **Progress snapshot:** 24 tasks completed, 9 in progress, 87 not
  yet started (as of the `StatusDate` embedded in the file).

Some tasks carry rich notes describing why a delay would have a
material business consequence (delivery, safety approval, PLC
integration, trial production). These are the intended feed for the
**Consequence of Delay** evaluation via the JEV integration.

## What you need

- Windows
- Microsoft Project (installed locally; the sample must be *opened*
  in it and be the active project while you use the app)
- `pywin32` (the repository's `[msp]` optional dependency) so the
  application can attach to Microsoft Project via COM
- The application itself installed per the main README

## How to run the app on the sample

1. **Install and run the application** as described in the main README.
2. **Open the sample in Microsoft Project** — double-click
   `Production_Line_Commissioning.mpp` or use *File ▸ Open*. The file
   sits in this directory. Make it the active project window.
3. In the application's **Project** section, click **Load Active MSP Project**. The integration reads the file strictly read-only.
4. **Pick a planning period.** A recommended first window is
   `2026-10-22 → 2026-11-06` (12 working days), which gives ~37
   candidate activities across procurement, civil, mechanical,
   electrical, and automation work.
5. Confirm the planning session, then run the decision analysis.
   The dashboard shows the ranking, schedule pressure (SP), successor
   impact (SI), consequence-of-delay (CD — provisional while offline),
   resource conflicts, and review warnings.
6. When you're done, **Start a new planning cycle** clears state.
7. To return to the built-in Demo Mode, pick **Load Demo Project**
   from the same section. Demo Mode needs no Microsoft Project at all.

## What is NOT required

- **No JEV/API key** is needed. Live JEV consequence evaluation is
  optional and only used when a `TYPESAFE_API_KEY` is configured in a
  `.env` next to the app entry point. Without it, the app stays in
  offline mode and marks every Consequence of Delay as provisional
  (0.5) with a review warning. See `scenario-data.json` in this
  directory for the intended *manual* assessment basis of each
  delayed-activity scenario.
- **No edit access** to Microsoft Project. The application never
  writes, saves, or closes anything.

## JEV offline sample

`scenario-data.json` documents the input contract the real JEV
integration would receive for the deliberately-delayable activities
(`task_name` + `task_notes`) and describes the *manual* assessment
level for each. **No JEV API call happens** while analyzing this
sample offline — that file is a documentation artifact, not a live
response log.

## Troubleshooting

- **"no project is active"** — the sample file must be the window
  currently focused in Microsoft Project. Close or minimize other
  project windows so this one is active, then try again.
- **"Microsoft Project is not reachable via COM"** — confirm
  `pywin32` is installed and Microsoft Project is running (not just
  installed).
- **Task counts look odd** — the application skips summary rows and
  inactive/blank rows by design; only leaf work tasks are mapped.
- **Slack shows as unknown** — Microsoft Project reports slack only
  once the schedule links are resolved. If the file was edited and
  saved with calculation disabled, re-open and let it recalculate
  (F9).

## Files in this directory

- `Production_Line_Commissioning.mpp` — the sample Microsoft Project file.
- `README.md` — this guide.
- `scenario-data.json` — intended JEV scenarios & manual assessment basis.
- `expected-results.md` — outcomes verified against the actual pipeline.
