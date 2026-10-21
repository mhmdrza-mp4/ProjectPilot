"""Minimal configuration layer for external-service settings.

Scope (deliberately small):
- loads a local ``.env`` file (``KEY=VALUE`` lines, no shell commands,
  no overriding of variables already present in the environment),
- reads ``TYPESAFE_API_KEY``, the documented TypeSafe SDK environment
  variable, into a frozen settings object.

Security properties:
- the API key value is neverLogged, printed, or included in repr
  strings; ``TypesafeSettings.__repr__`` masks it so tracebacks and
  logs cannot leak it,
- an empty/whitespace-only value counts as *not configured*, which
  produces a clear, actionable configuration error before any request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

API_KEY_VARIABLE = "TYPESAFE_API_KEY"
ENV_FILE_NAME = ".env"


def load_env_file(directory: Path | None = None) -> list[str]:
    """Load KEY=VALUE lines from ``.env`` in the given directory.

    Variables already present in the process environment are never
    overridden. Returns the *names* of the variables that were set —
    never their values. Lines that are blank, comments, or without
    ``=`` are ignored; a variable with an empty value is not set.
    """
    path = (directory if directory is not None else Path.cwd()) / ENV_FILE_NAME
    if not path.exists():
        return []
    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not name or name in os.environ or not value:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


@dataclass(frozen=True)
class TypesafeSettings:
    """TypeSafe settings; the API key value is kept out of repr/logs."""

    api_key: str | None

    def __post_init__(self) -> None:
        if self.api_key is not None and not self.api_key.strip():
            object.__setattr__(self, "api_key", None)

    def __repr__(self) -> str:  # noqa: D105 - keep the key out of reprs
        return "TypesafeSettings(api_key=<hidden>)"

    @property
    def has_api_key(self) -> bool:
        """True when an API key is configured (value is never exposed)."""
        return self.api_key is not None


def load_typesafe_settings(env_file_directory: Path | None = None) -> TypesafeSettings:
    """Read the current TypeSafe configuration for this process."""
    load_env_file(env_file_directory)
    value = os.environ.get(API_KEY_VARIABLE)
    return TypesafeSettings(api_key=value)
