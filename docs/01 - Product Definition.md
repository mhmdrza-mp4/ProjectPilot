# 01 - Product Definition

## User

The primary user is a **Project Planner / Project Controller** who plans and
controls projects in **Microsoft Project**.

## Problem

A project planner can see the *current state* of a project in Microsoft
Project (tasks, dates, progress, resources). However, converting that state
into an appropriate *project-control decision* is difficult:

- Many possible actions exist (add capacity, resequence tasks, cut scope, ...).
- Each action interacts with task dependencies, resource availability,
  milestones, and deadlines.
- Evaluating all options by hand is slow and error-prone.

## Product Goal

A **decision-support layer around Microsoft Project** that:

- reads the current project state,
- poses a decision problem (state + objective + constraints),
- evaluates candidate project-control decisions,
- recommends one decision, with an explanation.

The system **assists** the planner; it does **not** replace Microsoft Project
and does **not** execute decisions on its own.

## Initial Objective

**Reduce project schedule delay.** All early decisions are evaluated against
their expected effect on the project finish date.

## Constraints

Candidate decisions must respect project constraints such as:

- task dependencies (execution order),
- resource availability and capacity,
- milestones,
- project deadlines,
- other project-specific constraints.

## Expected Output

For a given project state and objective, the system produces:

1. **Candidate decisions** — actions that could be taken.
2. **Evaluation of each decision** — feasibility, constraint violations, and
   expected schedule impact.
3. **Recommended decision** — one selected action.
4. **Explanation** — why the recommended decision was chosen.

## Success Metrics

| Metric | Target |
| --- | --- |
| Recommendations respect all hard constraints | 100% of recommendations list no violated constraints |
| Explanation quality | Planner can understand every recommendation without consulting the developer |
| Time to evaluate a decision problem | Significantly faster than manual evaluation in Microsoft Project |
| Schedule delay on early pilot projects | Demonstrable reduction versus the planner's manual choice |

## Expected Use of JEV

The decision model **JEV** will evaluate candidate decisions against a posed
decision problem. JEV is a **decision-making model** — it is not an AI agent,
not machine learning, and not a predictive model. All `framework`/agent-style
interactive behavior is ruled out; JEV is a deterministic evaluation step in
the application layer.

## Explicit Non-Goals

- **Not** a replacement for Microsoft Project.
- **Not** an AI/ML/agent system; no predictive modeling, no LLMs.
- **Not** a full replacement for planner judgment (recommendations require
  human approval).
- **No authentication, database, Docker, or multi-user support** in the
  initial scope.
- **No automated execution** of decisions in Microsoft Project.
