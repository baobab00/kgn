"""Packaging E2E tests — verify wheel/sdist content, metadata, and migrations."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Expected SQL migration files (must match kgn/migrations/)
EXPECTED_SQL_FILES = [
    "kgn/migrations/001_init_enums.sql",
    "kgn/migrations/002_init_tables.sql",
    "kgn/migrations/003_init_indexes.sql",
    "kgn/migrations/004_embeddings.sql",
    "kgn/migrations/005_edge_status.sql",
    "kgn/migrations/006_task_queue.sql",
    "kgn/migrations/007_agent_roles.sql",
    "kgn/migrations/008_node_locks.sql",
    "kgn/migrations/009_conflict_activity_types.sql",
    "kgn/migrations/010_node_versions_full_snapshot.sql",
]

EXPECTED_PACKAGES = [
    "kgn",
    "kgn/cli",
    "kgn/conflict",
    "kgn/db",
    "kgn/embedding",
    "kgn/git",
    "kgn/github",
    "kgn/graph",
    "kgn/ingest",
    "kgn/logging",
    "kgn/lsp",
    "kgn/mcp",
    "kgn/mcp/tools",
    "kgn/migrations",
    "kgn/models",
    "kgn/orchestration",
    "kgn/parser",
    "kgn/serializer",
    "kgn/sync",
    "kgn/task",
    "kgn/web",
    "kgn/web/routes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_wheel() -> Path:
    """Build a wheel if none exists, return path."""
    wheels = sorted(DIST.glob("kgn_mcp-*.whl"))
    if wheels:
        return wheels[-1]
    subprocess.check_call(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(DIST)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wheels = sorted(DIST.glob("kgn_mcp-*.whl"))
    assert wheels, "wheel build failed — no .whl in dist/"
    return wheels[-1]


def _build_sdist() -> Path:
    """Build an sdist if none exists, return path."""
    sdists = sorted(DIST.glob("kgn_mcp-*.tar.gz"))
    if sdists:
        return sdists[-1]
    subprocess.check_call(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(DIST)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sdists = sorted(DIST.glob("kgn_mcp-*.tar.gz"))
    assert sdists, "sdist build failed — no .tar.gz in dist/"
    return sdists[-1]


# ---------------------------------------------------------------------------
# 1. Wheel structure tests
# ---------------------------------------------------------------------------


class TestWheelContents:
    """Verify the built wheel contains all required files."""

    @pytest.fixture(scope="class")
    def wheel_names(self) -> set[str]:
        whl = _build_wheel()
        with zipfile.ZipFile(whl) as zf:
            return set(zf.namelist())

    def test_entry_points_txt(self, wheel_names: set[str]):
        """Wheel must have entry_points.txt declaring the 'kgn' console script."""
        ep_files = [n for n in wheel_names if n.endswith("entry_points.txt")]
        assert ep_files, "entry_points.txt not found in wheel"

    def test_license_included(self, wheel_names: set[str]):
        """LICENSE must be bundled in the wheel."""
        license_files = [n for n in wheel_names if "LICENSE" in n.upper()]
        assert license_files, "LICENSE not found in wheel"

    def test_all_sql_migrations(self, wheel_names: set[str]):
        """All 6 SQL migration files must be present."""
        for sql in EXPECTED_SQL_FILES:
            assert sql in wheel_names, f"{sql} missing from wheel"

    def test_all_packages_present(self, wheel_names: set[str]):
        """Every sub-package must have an __init__.py in the wheel."""
        for pkg in EXPECTED_PACKAGES:
            init = f"{pkg}/__init__.py"
            assert init in wheel_names, f"{init} missing from wheel"

    def test_metadata_file(self, wheel_names: set[str]):
        """METADATA file must exist in dist-info."""
        meta_files = [n for n in wheel_names if n.endswith("/METADATA")]
        assert meta_files, "METADATA not found in wheel"

    def test_no_tests_in_wheel(self, wheel_names: set[str]):
        """Test files must NOT be included in the wheel."""
        test_files = [n for n in wheel_names if n.startswith("tests/")]
        assert not test_files, f"test files leaked into wheel: {test_files}"

    def test_no_docs_in_wheel(self, wheel_names: set[str]):
        """docs/ must NOT be included in the wheel."""
        doc_files = [n for n in wheel_names if n.startswith("docs/")]
        assert not doc_files, f"doc files leaked into wheel: {doc_files}"


# ---------------------------------------------------------------------------
# 2. Sdist structure tests
# ---------------------------------------------------------------------------


class TestSdistContents:
    """Verify the source distribution contains all necessary files."""

    @pytest.fixture(scope="class")
    def sdist_names(self) -> set[str]:
        sdist = _build_sdist()
        with tarfile.open(sdist, "r:gz") as tf:
            return {m.name for m in tf.getmembers()}

    def test_pyproject_toml(self, sdist_names: set[str]):
        """pyproject.toml must be in the sdist."""
        matches = [n for n in sdist_names if n.endswith("pyproject.toml")]
        assert matches, "pyproject.toml not found in sdist"

    def test_readme(self, sdist_names: set[str]):
        """README.md must be in the sdist."""
        matches = [n for n in sdist_names if n.endswith("README.md")]
        assert matches, "README.md not found in sdist"

    def test_license_in_sdist(self, sdist_names: set[str]):
        """LICENSE must be in the sdist."""
        matches = [n for n in sdist_names if n.endswith("/LICENSE") or n == "LICENSE"]
        assert matches, "LICENSE not found in sdist"

    def test_sql_in_sdist(self, sdist_names: set[str]):
        """All SQL migration files must be in the sdist."""
        for sql_basename in [Path(s).name for s in EXPECTED_SQL_FILES]:
            matches = [n for n in sdist_names if n.endswith(sql_basename)]
            assert matches, f"{sql_basename} not found in sdist"


# ---------------------------------------------------------------------------
# 3. Metadata tests (importlib.metadata — installed package)
# ---------------------------------------------------------------------------


class TestInstalledMetadata:
    """Verify metadata of the currently installed kgn package."""

    @pytest.fixture(scope="class")
    def meta(self) -> importlib.metadata.PackageMetadata:
        return importlib.metadata.metadata("kgn-mcp")

    def test_package_name(self, meta: importlib.metadata.PackageMetadata):
        assert meta["Name"] == "kgn-mcp"

    def test_version_matches_init(self, meta: importlib.metadata.PackageMetadata):
        from kgn import __version__

        assert meta["Version"] == __version__

    def test_license_field(self, meta: importlib.metadata.PackageMetadata):
        # License-Expression or License header
        license_val = meta.get("License-Expression") or meta.get("License") or ""
        assert "MIT" in license_val

    def test_requires_python(self, meta: importlib.metadata.PackageMetadata):
        rp = meta.get("Requires-Python", "")
        assert "3.12" in rp

    def test_entry_point_kgn(self):
        """The 'kgn' console_scripts entry point must be registered."""
        eps = importlib.metadata.entry_points()
        # Python 3.12+: eps is SelectableGroups or dict-like
        console_scripts = eps.select(group="console_scripts")
        kgn_eps = [ep for ep in console_scripts if ep.name == "kgn"]
        assert kgn_eps, "'kgn' entry point not found"
        assert kgn_eps[0].value == "kgn.cli:app"

    def test_project_urls(self, meta: importlib.metadata.PackageMetadata):
        """Project-URL headers must exist."""
        urls = meta.get_all("Project-URL") or []
        assert len(urls) >= 3, f"Expected ≥3 project URLs, got {len(urls)}"

    def test_classifiers(self, meta: importlib.metadata.PackageMetadata):
        classifiers = meta.get_all("Classifier") or []
        assert len(classifiers) >= 10, f"Expected ≥10 classifiers, got {len(classifiers)}"


# ---------------------------------------------------------------------------
# 4. Migrations accessibility test
# ---------------------------------------------------------------------------


class TestMigrationsAccessibility:
    """Verify MIGRATIONS_DIR resolves to actual SQL files at runtime."""

    def test_migrations_dir_exists(self):
        from kgn.db.migrations import MIGRATIONS_DIR

        assert MIGRATIONS_DIR.is_dir(), f"MIGRATIONS_DIR not a directory: {MIGRATIONS_DIR}"

    def test_migrations_sql_count(self):
        from kgn.db.migrations import MIGRATIONS_DIR

        sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        assert len(sql_files) == 10, f"Expected 10 SQL files, got {len(sql_files)}: {sql_files}"

    def test_migrations_filenames(self):
        from kgn.db.migrations import MIGRATIONS_DIR

        sql_names = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))
        expected = [
            "001_init_enums.sql",
            "002_init_tables.sql",
            "003_init_indexes.sql",
            "004_embeddings.sql",
            "005_edge_status.sql",
            "006_task_queue.sql",
            "007_agent_roles.sql",
            "008_node_locks.sql",
            "009_conflict_activity_types.sql",
            "010_node_versions_full_snapshot.sql",
        ]
        assert sql_names == expected

    def test_get_migration_files_returns_all(self):
        from kgn.db.migrations import _get_migration_files

        files = _get_migration_files()
        assert len(files) == 10


# ---------------------------------------------------------------------------
# 5. CLI version consistency
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """Verify version is consistent across all sources."""

    def test_init_version_matches_metadata(self):
        from kgn import __version__

        meta_version = importlib.metadata.version("kgn-mcp")
        assert __version__ == meta_version

    def test_cli_version_output(self):
        """'kgn --version' output must contain the correct version."""
        # Use the installed entry-point script directly
        kgn_exe = Path(sys.executable).parent / "kgn.exe"
        if not kgn_exe.exists():
            # Unix / non-Windows
            kgn_exe = Path(sys.executable).parent / "kgn"
        if not kgn_exe.exists():
            pytest.skip("kgn entry-point script not found")

        result = subprocess.run(
            [str(kgn_exe), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # typer --version may use either stdout or stderr
        output = result.stdout + result.stderr
        from kgn import __version__

        assert __version__ in output, f"Version {__version__} not found in: {output}"
