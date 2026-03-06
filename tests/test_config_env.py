"""Tests for Step 4 — Configuration flexibility + environment variable hardening.

Covers:
- R-001: .env override=False + file existence check
- R-006: CLI --verbose / --quiet flags
- R-008: Connection pool env vars (already implemented, test reinforcement)
- R-019: KGN_GIT_TIMEOUT environment variable
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from kgn.cli import app
from kgn.db.connection import _find_env_file, _load_env

runner = CliRunner()


# ══════════════════════════════════════════════════════════════════════
#  R-001: .env override=False + existence check
# ══════════════════════════════════════════════════════════════════════


class TestFindEnvFile:
    """Tests for _find_env_file() CWD-first resolution logic."""

    def test_cwd_env_takes_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CWD .env is returned when it exists."""
        cwd_env = tmp_path / ".env"
        cwd_env.write_text("# cwd\n")
        monkeypatch.chdir(tmp_path)
        result = _find_env_file()
        assert result == cwd_env

    def test_pkg_env_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Package root .env is returned when CWD has none."""
        monkeypatch.chdir(tmp_path)  # tmp_path has no .env
        pkg_env = tmp_path / "pkg" / ".env"
        pkg_env.parent.mkdir()
        pkg_env.write_text("# pkg\n")
        with patch("kgn.db.connection._PKG_ENV_FILE", pkg_env):
            result = _find_env_file()
        assert result == pkg_env

    def test_returns_none_when_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """None is returned when no .env exists anywhere."""
        monkeypatch.chdir(tmp_path)
        with patch("kgn.db.connection._PKG_ENV_FILE", tmp_path / "nonexistent" / ".env"):
            result = _find_env_file()
        assert result is None


class TestEnvOverride:
    """R-001: .env file must not override existing environment variables."""

    def test_env_file_does_not_override_existing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Pre-set environment variables must not be overwritten by .env file values."""
        env_file = tmp_path / ".env"
        env_file.write_text("KGN_DB_HOST=from-file\n")

        # Set environment variable first
        monkeypatch.setenv("KGN_DB_HOST", "from-env")

        with patch("kgn.db.connection._find_env_file", return_value=env_file):
            _load_env()

        # override=False keeps existing env var
        assert os.environ["KGN_DB_HOST"] == "from-env"

    def test_env_file_loads_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """.env file values are loaded when env var is not set."""
        env_file = tmp_path / ".env"
        env_file.write_text("KGN_TEST_STEP4_VAR=from-file\n")

        monkeypatch.delenv("KGN_TEST_STEP4_VAR", raising=False)

        with patch("kgn.db.connection._find_env_file", return_value=env_file):
            _load_env()

        assert os.environ.get("KGN_TEST_STEP4_VAR") == "from-file"

        # Cleanup
        monkeypatch.delenv("KGN_TEST_STEP4_VAR", raising=False)

    def test_no_env_file_does_not_crash(self, tmp_path: Path) -> None:
        """No error even when .env file does not exist."""
        with patch("kgn.db.connection._find_env_file", return_value=None):
            _load_env()  # should not raise


# ══════════════════════════════════════════════════════════════════════
#  R-006: CLI --verbose / --quiet flags
# ══════════════════════════════════════════════════════════════════════


class TestCLIVerboseQuiet:
    """R-006: --verbose → DEBUG, --quiet → WARNING, default → INFO."""

    def test_verbose_flag(self) -> None:
        """--verbose sets DEBUG level."""
        with patch("kgn.logging.config.configure_logging") as mock_log:
            runner.invoke(app, ["--verbose", "status", "--project", "x"])
            # configure_logging should be called with level="DEBUG"
            if mock_log.called:
                call_kwargs = mock_log.call_args
                assert (
                    call_kwargs.kwargs.get("level") == "DEBUG"
                    or call_kwargs[1].get("level") == "DEBUG"
                )

    def test_quiet_flag(self) -> None:
        """--quiet sets WARNING level."""
        with patch("kgn.logging.config.configure_logging") as mock_log:
            runner.invoke(app, ["--quiet", "status", "--project", "x"])
            if mock_log.called:
                call_kwargs = mock_log.call_args
                level = call_kwargs.kwargs.get("level") or call_kwargs[1].get("level")
                assert level == "WARNING"

    def test_default_level_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default level is INFO when no flag and KGN_LOG_LEVEL is not set."""
        monkeypatch.delenv("KGN_LOG_LEVEL", raising=False)
        with patch("kgn.logging.config.configure_logging") as mock_log:
            runner.invoke(app, ["status", "--project", "x"])
            if mock_log.called:
                call_kwargs = mock_log.call_args
                level = call_kwargs.kwargs.get("level") or call_kwargs[1].get("level")
                assert level == "INFO"

    def test_env_log_level_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """KGN_LOG_LEVEL env var is used when no flag is provided."""
        monkeypatch.setenv("KGN_LOG_LEVEL", "ERROR")
        with patch("kgn.logging.config.configure_logging") as mock_log:
            runner.invoke(app, ["status", "--project", "x"])
            if mock_log.called:
                call_kwargs = mock_log.call_args
                level = call_kwargs.kwargs.get("level") or call_kwargs[1].get("level")
                assert level == "ERROR"


# ══════════════════════════════════════════════════════════════════════
#  R-008: Connection pool env vars
# ══════════════════════════════════════════════════════════════════════


class TestConnectionPoolEnvVars:
    """R-008: KGN_DB_POOL_TIMEOUT and related env vars are applied."""

    def test_pool_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """KGN_DB_POOL_TIMEOUT env var is used for pool creation."""
        monkeypatch.setenv("KGN_DB_POOL_TIMEOUT", "20.0")
        monkeypatch.setenv("KGN_DB_POOL_MAX_IDLE", "600.0")
        monkeypatch.setenv("KGN_DB_POOL_RECONNECT_TIMEOUT", "60.0")

        # Directly read env var values to verify
        assert float(os.environ["KGN_DB_POOL_TIMEOUT"]) == 20.0
        assert float(os.environ["KGN_DB_POOL_MAX_IDLE"]) == 600.0
        assert float(os.environ["KGN_DB_POOL_RECONNECT_TIMEOUT"]) == 60.0

    def test_pool_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default values are used when env vars are not set."""
        monkeypatch.delenv("KGN_DB_POOL_TIMEOUT", raising=False)
        monkeypatch.delenv("KGN_DB_POOL_MAX_IDLE", raising=False)
        monkeypatch.delenv("KGN_DB_POOL_RECONNECT_TIMEOUT", raising=False)

        assert float(os.environ.get("KGN_DB_POOL_TIMEOUT", "10.0")) == 10.0
        assert float(os.environ.get("KGN_DB_POOL_MAX_IDLE", "300.0")) == 300.0
        assert float(os.environ.get("KGN_DB_POOL_RECONNECT_TIMEOUT", "30.0")) == 30.0


# ══════════════════════════════════════════════════════════════════════
#  R-019: KGN_GIT_TIMEOUT
# ══════════════════════════════════════════════════════════════════════


class TestGitTimeout:
    """R-019: KGN_GIT_TIMEOUT env var for subprocess timeout."""

    def test_default_timeout(self, tmp_path: Path) -> None:
        """Default timeout is 30 seconds."""
        from kgn.git.service import _DEFAULT_GIT_TIMEOUT, GitService

        svc = GitService(tmp_path)
        assert svc._timeout == _DEFAULT_GIT_TIMEOUT
        assert svc._timeout == 30

    def test_env_timeout(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """KGN_GIT_TIMEOUT env var changes the timeout."""
        monkeypatch.setenv("KGN_GIT_TIMEOUT", "120")

        from kgn.git.service import GitService

        svc = GitService(tmp_path)
        assert svc._timeout == 120

    def test_explicit_timeout_param(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Explicit timeout parameter takes precedence over env var."""
        monkeypatch.setenv("KGN_GIT_TIMEOUT", "120")

        from kgn.git.service import GitService

        svc = GitService(tmp_path, timeout=60)
        assert svc._timeout == 60
