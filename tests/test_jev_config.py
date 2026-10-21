"""Tests for the minimal configuration layer (TYPESAFE_API_KEY, .env)."""

import os

import pytest

from project_decision.config import (
    ENV_FILE_NAME,
    TypesafeSettings,
    load_typesafe_settings,
)


@pytest.fixture(autouse=True)
def clean_key_variable(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    # Configuration values must never leak into test output either.
    monkeypatch.setattr(os, "environ", os.environ.copy())


class TestApiKeyFromEnvironment:
    def test_missing_key_is_not_configured(self, tmp_path):
        # env_file_directory points at an empty directory: the project's
        # real local .env (when the developer has one) must not leak in.
        settings = load_typesafe_settings(env_file_directory=tmp_path)
        assert settings.api_key is None
        assert settings.has_api_key is False

    def test_key_from_environment_variable(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "sk-env-key")
        settings = load_typesafe_settings()
        assert settings.has_api_key is True

    def test_empty_value_counts_as_not_configured(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
        assert load_typesafe_settings().has_api_key is False

    def test_key_value_is_never_in_repr_or_failure(self, monkeypatch):
        key = "sk-secret-repr-check"
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
        settings = load_typesafe_settings()
        assert key not in repr(settings)
        assert "hidden" in repr(settings)

    def test_masked_settings_warns_without_leaking(self):
        settings = TypesafeSettings(api_key=None)
        assert settings.has_api_key is False


class TestEnvFile:
    def test_key_is_loaded_from_a_local_env_file(self, monkeypatch, tmp_path):
        (tmp_path / ENV_FILE_NAME).write_text(
            "TYPESAFE_API_KEY=sk-file-key\n", encoding="utf-8"
        )
        settings = load_typesafe_settings(env_file_directory=tmp_path)
        assert settings.has_api_key is True

    def test_existing_environment_variable_wins_over_env_file(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("TYPESAFE_API_KEY", "sk-env-key")
        (tmp_path / ENV_FILE_NAME).write_text(
            "TYPESAFE_API_KEY=sk-file-key\n", encoding="utf-8"
        )
        settings = load_typesafe_settings(env_file_directory=tmp_path)
        assert settings.api_key == "sk-env-key"

    def test_comments_and_empty_values_are_ignored(self, monkeypatch, tmp_path):
        (tmp_path / ENV_FILE_NAME).write_text(
            "# TYPESAFE_API_KEY=sk-commented\n"
            "OTHER=note\n"
            "TYPESAFE_API_KEY=\n",
            encoding="utf-8",
        )
        settings = load_typesafe_settings(env_file_directory=tmp_path)
        assert settings.has_api_key is False

    def test_missing_env_file_is_not_an_error(self, tmp_path):
        settings = load_typesafe_settings(env_file_directory=tmp_path)
        assert settings.has_api_key is False
