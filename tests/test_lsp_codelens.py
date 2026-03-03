"""Tests for kgn.lsp.codelens — Code Lens + Find References."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from kgn.lsp.codelens import (
    LensInfo,
    ReferenceLocation,
    _resolve_label,
    _scan_file_for_id,
    build_kge_lenses,
    build_kgn_lenses,
    find_references,
)
from kgn.lsp.indexer import NodeMeta
from kgn.models.enums import NodeStatus, NodeType

# ── Constants ────────────────────────────────────────────────────────

SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"
SAMPLE_UUID_2 = "660e8400-e29b-41d4-a716-446655440111"

VALID_KGN = f"""\
---
kgn_version: "0.1"
id: "{SAMPLE_UUID}"
type: SPEC
title: "Auth Flow"
status: ACTIVE
project_id: "demo"
agent_id: "agent-1"
supersedes: "{SAMPLE_UUID_2}"
confidence: 0.92
---

Some body text.
"""

KGN_WITH_SLUG = """\
---
kgn_version: "0.1"
id: "new:auth-spec"
type: SPEC
title: "Auth Spec"
status: ACTIVE
---

Body.
"""

KGE_DOC = """\
---
kgn_version: "0.1"
project_id: "demo"
agent_id: "agent-1"
edges:
  - from: "new:auth-spec"
    to:   "new:auth-goal"
    type: IMPLEMENTS
    note: "implements the goal"

  - from: "new:token-dec"
    to:   "new:auth-spec"
    type: DERIVED_FROM
---
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _make_meta(
    *,
    node_id: str = SAMPLE_UUID,
    slug: str = "auth-flow",
    node_type: NodeType = NodeType.SPEC,
    title: str = "Auth Flow",
    status: NodeStatus = NodeStatus.ACTIVE,
    confidence: float | None = 0.92,
    path: Path | None = None,
) -> NodeMeta:
    return NodeMeta(
        id=node_id,
        slug=slug,
        type=node_type,
        title=title,
        status=status,
        confidence=confidence,
        path=path or Path("/workspace/auth-flow.kgn"),
    )


def _make_indexer(
    *,
    uuid_map: dict[str, Path] | None = None,
    slug_map: dict[str, Path] | None = None,
    meta_map: dict[Path, NodeMeta] | None = None,
    refs_map: dict[str, set[Path]] | None = None,
) -> MagicMock:
    mock = MagicMock()
    _uuid = uuid_map or {}
    _slug = slug_map or {}
    _meta = meta_map or {}
    _refs = refs_map or {}

    mock.resolve_uuid.side_effect = lambda uid: _uuid.get(uid)
    mock.resolve_slug.side_effect = lambda s: _slug.get(s.lower())
    mock.get_meta.side_effect = lambda p: _meta.get(p)
    mock.get_references.side_effect = lambda nid: _refs.get(nid, set())
    return mock


# ═══════════════════════════════════════════════════════════════════════
# Code Lens — .kgn files
# ═══════════════════════════════════════════════════════════════════════


class TestBuildKgnLenses:
    def test_id_line_lens_with_references(self):
        """id: line shows N references."""
        ref_path = Path("/workspace/edges.kge")
        indexer = _make_indexer(refs_map={SAMPLE_UUID: {ref_path}})
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        id_lenses = [lens for lens in lenses if "reference" in lens.title]
        assert len(id_lenses) == 1
        assert id_lenses[0].title == "1 reference"
        assert id_lenses[0].line == 2  # id: line

    def test_id_line_lens_zero_references(self):
        indexer = _make_indexer()
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        id_lenses = [lens for lens in lenses if "reference" in lens.title]
        assert len(id_lenses) == 1
        assert id_lenses[0].title == "0 references"

    def test_id_line_lens_multiple_references(self):
        refs = {Path(f"/workspace/e{i}.kge") for i in range(5)}
        indexer = _make_indexer(refs_map={SAMPLE_UUID: refs})
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        id_lenses = [lens for lens in lenses if "reference" in lens.title]
        assert "5 references" in id_lenses[0].title

    def test_id_line_command(self):
        indexer = _make_indexer()
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        id_lenses = [lens for lens in lenses if "reference" in lens.title]
        assert id_lenses[0].command_id == "editor.action.showReferences"

    def test_supersedes_lens_found(self):
        """supersedes: line shows target node title and status."""
        meta2 = _make_meta(
            node_id=SAMPLE_UUID_2,
            title="Old Flow",
            status=NodeStatus.SUPERSEDED,
            path=Path("/workspace/old.kgn"),
        )
        indexer = _make_indexer(
            uuid_map={SAMPLE_UUID_2: meta2.path},
            meta_map={meta2.path: meta2},
        )
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        sup_lenses = [lens for lens in lenses if "Supersedes" in lens.title]
        assert len(sup_lenses) == 1
        assert "Old Flow" in sup_lenses[0].title
        assert "SUPERSEDED" in sup_lenses[0].title
        assert sup_lenses[0].command_id == "vscode.open"

    def test_supersedes_lens_no_meta(self):
        """supersedes: lens with path but no meta shows truncated UUID."""
        path2 = Path("/workspace/old.kgn")
        indexer = _make_indexer(uuid_map={SAMPLE_UUID_2: path2})
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        sup_lenses = [lens for lens in lenses if "Supersedes" in lens.title]
        assert len(sup_lenses) == 1
        assert SAMPLE_UUID_2[:8] in sup_lenses[0].title
        assert sup_lenses[0].command_id == "vscode.open"

    def test_supersedes_lens_not_found(self):
        """supersedes: target not found shows 'not found'."""
        indexer = _make_indexer()
        lenses = build_kgn_lenses(VALID_KGN, Path("/workspace/auth.kgn"), indexer)
        sup_lenses = [lens for lens in lenses if "Supersedes" in lens.title]
        assert len(sup_lenses) == 1
        assert "not found" in sup_lenses[0].title

    def test_slug_id_references(self):
        """id: new:slug gets reference count lens."""
        ref_path = Path("/workspace/edges.kge")
        indexer = _make_indexer(refs_map={"new:auth-spec": {ref_path}})
        lenses = build_kgn_lenses(KGN_WITH_SLUG, Path("/workspace/auth-spec.kgn"), indexer)
        id_lenses = [lens for lens in lenses if "reference" in lens.title]
        assert len(id_lenses) == 1
        assert id_lenses[0].title == "1 reference"

    def test_no_front_matter(self):
        indexer = _make_indexer()
        lenses = build_kgn_lenses("no front matter here", Path("/x.kgn"), indexer)
        assert lenses == []

    def test_empty_document(self):
        indexer = _make_indexer()
        lenses = build_kgn_lenses("", Path("/x.kgn"), indexer)
        assert lenses == []


# ═══════════════════════════════════════════════════════════════════════
# Code Lens — .kge files
# ═══════════════════════════════════════════════════════════════════════


class TestBuildKgeLenses:
    def test_edge_lenses_with_slugs(self):
        """Each edge entry gets a from → to (TYPE) lens."""
        indexer = _make_indexer()
        lenses = build_kge_lenses(KGE_DOC, Path("/workspace/edges.kge"), indexer)
        assert len(lenses) == 2
        assert "new:auth-spec" in lenses[0].title
        assert "new:auth-goal" in lenses[0].title
        assert "IMPLEMENTS" in lenses[0].title

    def test_edge_lenses_second_edge(self):
        indexer = _make_indexer()
        lenses = build_kge_lenses(KGE_DOC, Path("/workspace/edges.kge"), indexer)
        assert "new:token-dec" in lenses[1].title
        assert "new:auth-spec" in lenses[1].title
        assert "DERIVED_FROM" in lenses[1].title

    def test_edge_lens_with_uuid_resolved(self):
        """UUID from/to gets resolved to slug via indexer."""
        kge_doc = f"""\
---
kgn_version: "0.1"
project_id: "demo"
agent_id: "agent-1"
edges:
  - from: "{SAMPLE_UUID}"
    to:   "{SAMPLE_UUID_2}"
    type: DEPENDS_ON
---
"""
        meta1 = _make_meta(slug="auth-flow")
        meta2 = _make_meta(
            node_id=SAMPLE_UUID_2,
            slug="old-flow",
            path=Path("/workspace/old.kgn"),
        )
        indexer = _make_indexer(
            uuid_map={SAMPLE_UUID: meta1.path, SAMPLE_UUID_2: meta2.path},
            meta_map={meta1.path: meta1, meta2.path: meta2},
        )
        lenses = build_kge_lenses(kge_doc, Path("/workspace/edges.kge"), indexer)
        assert len(lenses) == 1
        assert "auth-flow" in lenses[0].title
        assert "old-flow" in lenses[0].title
        assert "DEPENDS_ON" in lenses[0].title

    def test_edge_lens_uuid_not_found(self):
        """Unresolvable UUID shows truncated form."""
        kge_doc = f"""\
---
kgn_version: "0.1"
project_id: "demo"
agent_id: "agent-1"
edges:
  - from: "{SAMPLE_UUID}"
    to:   "new:goal"
    type: IMPLEMENTS
---
"""
        indexer = _make_indexer()
        lenses = build_kge_lenses(kge_doc, Path("/workspace/edges.kge"), indexer)
        assert len(lenses) == 1
        assert SAMPLE_UUID[:8] + "…" in lenses[0].title

    def test_empty_kge(self):
        indexer = _make_indexer()
        lenses = build_kge_lenses("", Path("/x.kge"), indexer)
        assert lenses == []

    def test_kge_no_edges_section(self):
        doc = "---\nkgn_version: '0.1'\n---\n"
        indexer = _make_indexer()
        lenses = build_kge_lenses(doc, Path("/x.kge"), indexer)
        assert lenses == []

    def test_tightly_packed_edges(self):
        """Edges with no blank lines between them — scan-ahead hits next - from:."""
        doc = """\
---
edges:
  - from: "new:a"
    to: "new:b"
    type: IMPLEMENTS
  - from: "new:c"
    to: "new:d"
    type: DEPENDS_ON
---
"""
        indexer = _make_indexer()
        lenses = build_kge_lenses(doc, Path("/x.kge"), indexer)
        assert len(lenses) == 2
        assert "new:a" in lenses[0].title
        assert "IMPLEMENTS" in lenses[0].title
        assert "new:c" in lenses[1].title
        assert "DEPENDS_ON" in lenses[1].title


# ═══════════════════════════════════════════════════════════════════════
# LensInfo
# ═══════════════════════════════════════════════════════════════════════


class TestLensInfo:
    def test_creation(self):
        lens = LensInfo(line=5, title="3 references", command_id="cmd")
        assert lens.line == 5
        assert lens.title == "3 references"
        assert lens.command_id == "cmd"
        assert lens.command_args == []

    def test_with_args(self):
        lens = LensInfo(line=0, title="T", command_id="c", command_args=["a", 1])
        assert lens.command_args == ["a", 1]


# ═══════════════════════════════════════════════════════════════════════
# ReferenceLocation
# ═══════════════════════════════════════════════════════════════════════


class TestReferenceLocation:
    def test_creation(self):
        loc = ReferenceLocation(Path("/a.kge"), 3, 10, 46)
        assert loc.path == Path("/a.kge")
        assert loc.line == 3
        assert loc.start_col == 10
        assert loc.end_col == 46


# ═══════════════════════════════════════════════════════════════════════
# _resolve_label
# ═══════════════════════════════════════════════════════════════════════


class TestResolveLabel:
    def test_slug_passthrough(self):
        indexer = _make_indexer()
        assert _resolve_label("new:auth-spec", indexer) == "new:auth-spec"

    def test_uuid_found(self):
        meta = _make_meta(slug="auth-flow")
        indexer = _make_indexer(
            uuid_map={SAMPLE_UUID: meta.path},
            meta_map={meta.path: meta},
        )
        assert _resolve_label(SAMPLE_UUID, indexer) == "auth-flow"

    def test_uuid_not_found(self):
        indexer = _make_indexer()
        result = _resolve_label(SAMPLE_UUID, indexer)
        assert result == SAMPLE_UUID[:8] + "…"

    def test_uuid_no_meta(self):
        """UUID resolves to path but no meta → truncated."""
        indexer = _make_indexer(uuid_map={SAMPLE_UUID: Path("/x.kgn")})
        result = _resolve_label(SAMPLE_UUID, indexer)
        assert result == SAMPLE_UUID[:8] + "…"

    def test_empty_value(self):
        indexer = _make_indexer()
        assert _resolve_label("", indexer) == "?"

    def test_plain_text(self):
        indexer = _make_indexer()
        assert _resolve_label("some-value", indexer) == "some-value"


# ═══════════════════════════════════════════════════════════════════════
# _scan_file_for_id
# ═══════════════════════════════════════════════════════════════════════


class TestScanFileForId:
    def test_single_occurrence(self):
        text = f"from: {SAMPLE_UUID}\nto: other\n"
        out: list[ReferenceLocation] = []
        _scan_file_for_id(Path("/a.kge"), text, SAMPLE_UUID, out)
        assert len(out) == 1
        assert out[0].line == 0
        assert out[0].start_col == 6
        assert out[0].end_col == 6 + 36

    def test_multiple_occurrences(self):
        text = f"from: {SAMPLE_UUID}\nto: {SAMPLE_UUID}\n"
        out: list[ReferenceLocation] = []
        _scan_file_for_id(Path("/a.kge"), text, SAMPLE_UUID, out)
        assert len(out) == 2
        assert out[0].line == 0
        assert out[1].line == 1

    def test_no_occurrences(self):
        out: list[ReferenceLocation] = []
        _scan_file_for_id(Path("/a.kge"), "nothing here\n", SAMPLE_UUID, out)
        assert len(out) == 0

    def test_slug_occurrence(self):
        text = "from: new:auth-spec\nto: new:auth-goal\n"
        out: list[ReferenceLocation] = []
        _scan_file_for_id(Path("/a.kge"), text, "new:auth-spec", out)
        assert len(out) == 1
        assert out[0].start_col == 6


# ═══════════════════════════════════════════════════════════════════════
# find_references
# ═══════════════════════════════════════════════════════════════════════


class TestFindReferences:
    def test_uuid_references(self, tmp_path: Path):
        """Find references for a UUID in .kge files."""
        kge_file = tmp_path / "edges.kge"
        kge_content = f"""\
---
edges:
  - from: "{SAMPLE_UUID}"
    to: "new:goal"
    type: IMPLEMENTS
---
"""
        kge_file.write_text(kge_content, encoding="utf-8")

        indexer = _make_indexer(refs_map={SAMPLE_UUID: {kge_file}})
        refs = find_references(VALID_KGN, 2, 10, indexer)
        assert len(refs) >= 1
        assert refs[0].path == kge_file

    def test_slug_references(self, tmp_path: Path):
        """Find references for a new:slug."""
        kge_file = tmp_path / "edges.kge"
        kge_content = """\
---
edges:
  - from: "new:auth-spec"
    to: "new:goal"
    type: IMPLEMENTS
---
"""
        kge_file.write_text(kge_content, encoding="utf-8")

        indexer = _make_indexer(refs_map={"new:auth-spec": {kge_file}})
        refs = find_references(KGN_WITH_SLUG, 2, 6, indexer)
        assert len(refs) >= 1

    def test_no_references(self):
        """No referencing files → empty list."""
        indexer = _make_indexer()
        refs = find_references(VALID_KGN, 2, 10, indexer)
        assert refs == []

    def test_empty_document(self):
        indexer = _make_indexer()
        refs = find_references("", 0, 0, indexer)
        assert refs == []

    def test_line_out_of_range(self):
        indexer = _make_indexer()
        refs = find_references("text", 999, 0, indexer)
        assert refs == []

    def test_body_text_no_refs(self):
        indexer = _make_indexer()
        refs = find_references(VALID_KGN, 14, 5, indexer)
        assert refs == []

    def test_unknown_word_no_refs(self):
        """Word that is not UUID/slug and no UUID on line → empty."""
        indexer = _make_indexer()
        refs = find_references("title: Hello\n", 0, 8, indexer)
        assert refs == []

    def test_uuid_substring_on_line(self, tmp_path: Path):
        """UUID embedded in a prefixed word still resolves."""
        doc = f"ref:{SAMPLE_UUID}\n"
        kge_file = tmp_path / "edges.kge"
        kge_file.write_text(f"from: {SAMPLE_UUID}\n", encoding="utf-8")
        indexer = _make_indexer(refs_map={SAMPLE_UUID: {kge_file}})
        refs = find_references(doc, 0, 5, indexer)
        assert len(refs) >= 1

    def test_file_read_error(self, tmp_path: Path):
        """Unreadable file is skipped gracefully."""
        missing = tmp_path / "nonexistent.kge"
        indexer = _make_indexer(refs_map={SAMPLE_UUID: {missing}})
        refs = find_references(VALID_KGN, 2, 10, indexer)
        assert refs == []

    def test_supersedes_uuid_references(self, tmp_path: Path):
        """Find references for UUID on supersedes: line."""
        kge_file = tmp_path / "edges.kge"
        kge_content = f"to: {SAMPLE_UUID_2}\n"
        kge_file.write_text(kge_content, encoding="utf-8")

        indexer = _make_indexer(refs_map={SAMPLE_UUID_2: {kge_file}})
        # Line 8 is supersedes: "UUID_2"
        line_text = VALID_KGN.split("\n")[8]
        col = line_text.index(SAMPLE_UUID_2[:4])
        refs = find_references(VALID_KGN, 8, col, indexer)
        assert len(refs) >= 1
