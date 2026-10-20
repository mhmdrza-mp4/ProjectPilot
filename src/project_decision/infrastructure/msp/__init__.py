"""Public surface of the Microsoft Project integration (read-only)."""

from project_decision.infrastructure.msp.com_client import MspComClient
from project_decision.infrastructure.msp.exceptions import (
    MspError,
    MspMappingError,
    MspNoActiveProjectError,
    MspNotAvailableError,
    MspReadError,
)
from project_decision.infrastructure.msp.msp_adapter import MspProjectAdapter

__all__ = [
    "MspComClient",
    "MspError",
    "MspMappingError",
    "MspNoActiveProjectError",
    "MspNotAvailableError",
    "MspProjectAdapter",
    "MspReadError",
]
