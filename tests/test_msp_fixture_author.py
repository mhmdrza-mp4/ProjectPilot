"""Safety tests for the write-capable MSP fixture-authoring script.

Microsoft Project is never touched here: the opt-in guard and argument
handling are tested with the authoring function replaced by a recorder,
before any COM import could happen.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "msp_fixture_author.py"
)


@pytest.fixture(scope="module")
def author_script():
    spec = importlib.util.spec_from_file_location(
        "msp_fixture_author_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refuses_without_opt_in_before_any_authoring(
    author_script, monkeypatch, capsys
):
    calls: list[str] = []
    monkeypatch.setattr(
        author_script, "_author_fixture", lambda: calls.append("ran") or 0
    )

    assert author_script.main([]) == 2
    assert calls == []  # no COM access, no mutation

    stderr = capsys.readouterr().err
    assert "REFUSING" in stderr
    assert "--i-understand" in stderr
    assert "No Microsoft Project connection was made" in stderr


def test_explicit_opt_in_runs_the_authoring_function(author_script, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        author_script, "_author_fixture", lambda: calls.append("ran") or 0
    )

    assert author_script.main(["--i-understand"]) == 0
    assert calls == ["ran"]


def test_parser_defaults_to_no_opt_in(author_script):
    assert author_script._parse_args([]).i_understand is False
    assert author_script._parse_args(["--i-understand"]).i_understand is True
