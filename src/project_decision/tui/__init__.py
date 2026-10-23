"""The terminal (Textual) presentation layer of the Project Decision
Assistant.

Consumer boundaries unchanged from the product's thin-UI rule: widgets
call only the workflow functions in ``application.decision_workflow``
and the bridge in ``tui.data`` — never the pipeline internals, never
domain rules, never COM directly.
"""

from project_decision.tui.app import DecisionTUI
from project_decision.tui.data import sample_project_guide_text, sample_project_path


def main() -> None:
    """Launch the terminal application."""
    DecisionTUI().run()


__all__ = ["DecisionTUI", "main", "sample_project_guide_text", "sample_project_path"]
