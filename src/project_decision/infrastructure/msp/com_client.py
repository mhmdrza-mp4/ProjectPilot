"""Raw COM communication with a locally installed Microsoft Project.

Responsibilities limited to:
- connecting to the Microsoft Project COM application,
- obtaining the active project,
- reading a few COM application settings,
- releasing the connection.

No domain logic lives here, and no imports happen at module level: this
module imports ``win32com`` lazily inside ``connect()`` so that unit
tests (and non-Windows machines) can import the package without pywin32.

Read-only guarantee: this client never writes, saves, or closes a
Microsoft Project document. ``disconnect()`` only releases our COM
references; it never quits the user's application.
"""

from __future__ import annotations

from typing import Any

from project_decision.infrastructure.msp.exceptions import (
    MspNoActiveProjectError,
    MspNotAvailableError,
)

_MSP_APPLICATION = "MSProject.Application"

#: Microsoft Project's default working day (8 hours) in minutes.
DEFAULT_MINUTES_PER_DAY = 480.0


class MspComClient:
    """Owns the single COM connection to Microsoft Project."""

    def __init__(self) -> None:
        self._application: Any | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        """Attach to the locally installed Microsoft Project application.

        This attaches to a running instance or starts one, as Microsoft
        Project's COM automation dictate. It never opens a document.
        """
        if self._application is not None:
            return
        try:
            import pywintypes
            import win32com.client
        except ImportError as exc:
            raise MspNotAvailableError(
                "pywin32 is not installed; install it with 'pip install pywin32'"
            ) from exc
        try:
            self._application = win32com.client.Dispatch(_MSP_APPLICATION)
        except pywintypes.com_error as exc:
            self._application = None
            raise MspNotAvailableError(
                "could not connect to Microsoft Project via COM; is Microsoft Project installed?"
            ) from exc

    def disconnect(self) -> None:
        """Release our COM references only. Never closes Microsoft Project."""
        self._application = None

    def is_available(self) -> bool:
        """True if a COM connection to Microsoft Project can be made.

        Note: in Microsoft Project automation, connecting may *start* the
        application if it is not running; this is a side effect of COM.
        """
        try:
            self.connect()
        except MspNotAvailableError:
            return False
        return True

    def __enter__(self) -> MspComClient:
        self.connect()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.disconnect()

    # -- reads -------------------------------------------------------------

    def get_active_project(self) -> Any:
        """Return the raw COM active project object.

        Raises:
            MspNotAvailableError: not connected yet.
            MspNoActiveProjectError: Microsoft Project has no project open.
        """
        self._ensure_connected()
        try:
            project = self._application.ActiveProject
        except Exception as exc:
            # In Microsoft Project COM, reading ActiveProject without an
            # open project raises a COM error; that is by far the most
            # common cause here. Any other COM failure surfaces the same
            # domain-level outcome: we have no project to read.
            raise MspNoActiveProjectError(
                "cannot read the active project; is a project open in Microsoft Project?"
            ) from exc
        if project is None:
            raise MspNoActiveProjectError(
                "cannot read the active project; is a project open in Microsoft Project?"
            )
        return project

    def get_minutes_per_day(self) -> float:
        """Working minutes per day Microsoft Project is configured for.

        Used to convert COM durations (given in minutes) into days.
        Falls back to the 8-hour default if the setting is unreadable.
        """
        self._ensure_connected()
        try:
            value = self._application.MinutesPerDay
        except Exception:
            return DEFAULT_MINUTES_PER_DAY
        if not value:
            return DEFAULT_MINUTES_PER_DAY
        return float(value)

    # -- internal ----------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._application is None:
            raise MspNotAvailableError("not connected; call connect() first")
