# Demo guide

Explore the complete product offline: no Microsoft Project, no COM, no
JEV, no API key, no network. The demo loads a fixed, deterministic
fixture ("Production Line Commissioning") through the same workflow the
live MSP integration uses, and the OFFLINE consequence mode keeps every
consequence-provisional honest.

## 1. Start the app

From the repository root:

```text
streamlit run app/streamlit_app.py
```

## 2. Load the demo

In **Project**, click **Load Demo Project** (the primary button). The
Project panel shows the fixture name and capture time — nothing is
fetched, the snapshot is in your process.

## 3. Select a planning period

The period editor comes pre-filled with the fixture's own date span
(2026-06-01 → 2026-06-17). Applying it discovers the default candidates
in that window; the caption explains who is excluded and why
(a summary row, a completed task, an activity outside the window, an
undated activity).

## 4. Review candidates

The candidate list is editable: remove anything the rules suggested,
add anything else (for example the outside-period
"Post-go-live hypercare", or the undated "Update control-room floor
plan" to see the honest missing-data behaviour).

## 5. Configure parameters (optional)

Sidebar → *Scoring Parameters*: weights must sum to 1.0 (they are
rejected otherwise, never silently normalized), successor cap K, and an
optional horizon override.

## 6. Confirm

**Confirm Planning Session** freezes the funnel for this analysis.

## 7. Run the analysis

**Run Decision Analysis** executes the pipeline: schedule → conflicts →
consequence (offline: provisional) → scoring → ranking → review.

## 8. Inspect the ranking

The dashboard shows the priority index (0–100), SP/SI/CD contribution
chart, and status per activity. Note: the two critical zero-slack
activities top the table on equal scores — the documented tie-breaks
(then lower slack, successors, evaluated consequence, task id) are what
separate them.

## 9. Select a task

The detail panel shows where every score part came from: slack and
successor facts, consequence provenance (provisional/evaluated), plus
successor chains and conflict tutorials (which task pair, which shared
resource, which days).

## 10. Read the review centre

Every task carries explicit review categories: provisional consequence
(off-line mode always), missing schedule data (the undated task),
resource conflicts, low confidence — each with a WHAT/WHY/NEXT
explanation. The recommendation is *input* to a human decision; nothing
is executed automatically.

---

To switch projects later, open **New planning cycle → Start a new
planning cycle** (clears the workflow state honestly, then choose a
source again). To try the live consequence evaluator you would need the
real TypeSafe service and `TYPESAFE_API_KEY`; see README §Demo vs Live.
