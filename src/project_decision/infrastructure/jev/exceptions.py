"""Expected Jev/TypeSafe failure modes as application-level errors.

The Streamlit layer can display ``str(error)`` safely: messages are
written for planners and never contain the API key (the client redacts
it from anything echoed back by the API, even in the unlikely case a
server body repeats it).
"""


class JevError(Exception):
    """Base class for expected TypeSafe/Jev integration failures."""


class JevConfigurationError(JevError):
    """The integration is not usable: no key, empty key, or SDK rejected."""


class JevAuthenticationError(JevError):
    """The TypeSafe API rejected our credentials (HTTP 401)."""


class JevConnectionError(JevError):
    """The network failed: connection could not be established or timed out."""


class JevInvalidRequestError(JevError):
    """TypeSafe called the request invalid (HTTP 400/422)."""


class JevApiError(JevError):
    """Other TypeSafe API failures with a status code (403/404/429/5xx)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id


class JevUnexpectedResponseError(JevError):
    """The response could not be interpreted (missing/unknown structure)."""
