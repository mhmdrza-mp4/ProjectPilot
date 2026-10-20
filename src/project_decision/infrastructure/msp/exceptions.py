"""Exceptions for the Microsoft Project integration.

Raw COM errors (``pywintypes.com_error``) and Windows-only import errors
are translated into these exceptions at the infrastructure boundary, so
that application code never sees COM internals.
"""

from __future__ import annotations


class MspError(Exception):
    """Base class for Microsoft Project integration failures."""


class MspNotAvailableError(MspError):
    """Microsoft Project cannot be reached via COM.

    Typical causes: Microsoft Project not installed, COM registration
    broken, or pywin32 missing.
    """


class MspNoActiveProjectError(MspError):
    """Microsoft Project is running but has no active project open."""


class MspReadError(MspError):
    """A project could not be read from Microsoft Project."""


class MspMappingError(MspError):
    """Microsoft Project data violates what our domain model requires.

    Raised for essential fields (e.g. a task without id or name) that we
    cannot map meaningfully.
    """
