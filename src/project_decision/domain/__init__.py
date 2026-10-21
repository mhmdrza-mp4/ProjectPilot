"""Domain layer of the Project Decision System.

The domain is pure business logic:
- no Microsoft Project (COM) imports,
- no JEV API imports,
- no Streamlit imports,
- only the Python standard library.

External systems adapt to the domain, never the other way around.
"""

from project_decision.domain.constraint import Constraint, ConstraintKind
from project_decision.domain.decision import Decision, DecisionKind
from project_decision.domain.decision_assumption import DecisionAssumption
from project_decision.domain.decision_evaluation import DecisionEvaluation
from project_decision.domain.decision_problem import DecisionProblem
from project_decision.domain.decision_recommendation import DecisionRecommendation
from project_decision.domain.evaluation_criterion import EvaluationCriterion
from project_decision.domain.milestone import Milestone
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project import Project
from project_decision.domain.project_state import ProjectState
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task
from project_decision.domain.task_status import TaskStatus, classify_task, task_status_label
from project_decision.domain.working_days import calculate_working_days

__all__ = [
    "Constraint",
    "ConstraintKind",
    "Decision",
    "DecisionAssumption",
    "DecisionEvaluation",
    "DecisionKind",
    "DecisionProblem",
    "DecisionRecommendation",
    "EvaluationCriterion",
    "Milestone",
    "PlanningPeriod",
    "PlanningSession",
    "Project",
    "ProjectState",
    "Resource",
    "Task",
    "TaskStatus",
    "calculate_working_days",
    "classify_task",
    "task_status_label",
]
