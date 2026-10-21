# Decision Model

This document mirrors the implementation in
`src/project_decision/application/priority_scoring.py`,
`priority_ranking.py`, and `decision_review.py`. If this text and the
code ever disagree, the code is wrong or outdated — both are tested so
that cannot happen silently.

It describes **one prioritization index**, computed once per confirmed
planning session. It is not a probability, not a prediction, not a
utility, and never an automatic decision. The human manager stays the
decision maker; the index only orders the conversation.

---

## Inputs

| Input | Source | Notes |
| --- | --- | --- |
| Planning period | chosen by the manager | inclusive date window; also supplies the horizon |
| Candidates | `PlanningSession` | default-discovered set the manager may add to / remove from |
| Schedule analysis | `analyze_schedule(state)` | slack (working days), successor counts per task |
| Conflict analysis | `analyze_resource_conflicts(state)` | warnings only, display-only |
| Consequence of Delay CD | JEV evaluation **or** provisional fill-in | see below |
| `ScoringParameters` | sidebar editor | weights, successor cap K, optional horizon override |

## Criteria

### Schedule Pressure — SP (weight 0.40)

```
SP = 1 - min(max(slack, 0), H) / H
```

- `H` = horizon in *working days* (Mon–Fri calculation), default: the
  planning period's own working-day count; an explicit override wins.
- Slack ≤ 0 (zero or negative — already late or just critical) → SP = 1.0.
- Slack ≥ H → SP = 0.0.
- `is_critical` is **never** used in the formula (it would double-count
  the same schedule signal); it is displayed only.
- Missing slack → SP unavailable, never invented.

### Successor Impact — SI (weight 0.20)

```
SI = min(successor_count, K) / K          # K default: 5
```

Direct successors only (derived from dependency direction once, in the
schedule analysis). **No candidate-relative normalization:** adding or
removing other candidates never changes any task's value.

### Consequence of Delay — CD (weight 0.40)

`CD` is a normalized value in `[0, 1]`, an *input* validated by scoring,
never an output of it. Two sources:

| Source | CD | Provenance flag | Review |
| --- | --- | --- | --- |
| JEV (live) | JEV score / 3 | `"evaluated"` | confidence < 0.5 flags review |
| OFFLINE (default) | neutral 0.5 | `"provisional"` | always flagged |

The offline mode never invents an evaluation; the provisional 0.5 keeps
the score computable while showing the range CD=0 vs CD=1 would still
allow. Confidence is **only** a review signal — it is never multiplied
into the score, and JEV probability mass is never re-normalized here.

## Score

```
score = 100 * (w_sp * SP + w_si * SI + w_cd * CD)
```

- Default weights 0.40 / 0.20 / 0.40 (must sum to 1.0; rejected, never
  silently normalized when invalid, and never re-normalized per task
  when a criterion is missing).
- When SP or SI is unavailable, the criterion and its contribution are
  `None` and **no total score is computed** (`None`). A missing input is
  never silently treated as zero.

**Interpretation contract:** a normalized prioritization index 0–100.
Not a probability, not a prediction, not a calibrated utility, not a
decision. Changing the weights changes the index — nothing else; the
ranking rules above stay fixed.

## Ranking (deterministic, fixed tie-breaks)

Ranks are sequential (1..n). Tasks are ordered by the total order
(`priority_ranking._ordering_key`), each tier "known beats missing" so
`None` never has to become a fabricated zero:

1. **round(score, 1)** — higher first;
2. **lower total slack** first (values may be negative);
3. **more successors** first;
4. **higher *evaluated* CD** first — a provisional CD does not count as
   an evaluation and ties there instead;
5. **task id** ascending (makes the order total, stable, reproducible).

Unscored tasks (no total score) rank **after** all scored tasks.

## Review semantics (review is a state, not a punishment)

`DecisionReview` aggregates per-task statuses into deterministic
categories (fixed order; a task may carry several):

| Category | Trigger |
| --- | --- |
| `unscored_candidate` | candidate with no score at all |
| `missing_schedule_data` | slack/dates unavailable → SP unknown |
| `provisional_consequence` | CD not evaluated (offline mode, missing notes) |
| `low_consequence_confidence` | JEV confidence < 0.5 |
| `consequence_evaluation_failed` | JEV call errored for that task |
| `consequence_evaluation_unavailable` | JEV unreachable / not configured |
| `resource_conflict` | task pair PROVEN to share at least one scheduled day (CONFLICT only; `UNKNOWN` shares never warn) |

Every failed/low-confidence evaluation keeps structured original
information (score, confidence, cause string) — nothing is replaced or
invented, one task's failure never affects another's.

## Resource warnings (display-only)

- CONFLICT pairs (shared resource **and** overlapping scheduled days,
  boundary dates included) produce a warning per task with its shared
  resources.
- UNKNOWN pairs (shared resources but undecidable overlap — e.g. a task
  without dates) produce no warning, only an explicit report.
- No resource capacity is modeled, planned, or optimized anywhere.
