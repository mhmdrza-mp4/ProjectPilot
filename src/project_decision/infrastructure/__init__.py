"""Infrastructure layer: implementations of external-system access.

Implemented adapters:
- Microsoft Project read integration (``msp``): COM/pywin32, read-only,
  maps the active project into domain objects.

Planned adapters (not implemented yet):
- JEV integration via its API, turning a ``DecisionProblem`` into
  ``DecisionEvaluation`` results.
"""
