"""Phase 2 Step 3: change_detect 纯函数单测。

Spec 验收(PHASE2_SPEC.md §2 Step 3):
  - 首次(全 added)
  - 增量(部分 modified)
  - 删除
  - 文件移位(rename = added + removed)
"""
from pathlib import Path
import json

from meta_ops.librarian.change_detect import (
    ChangeSet,
    detect_changes,
    scan_dir_sha,
    write_manifest,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_first_run_all_added(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.md", "alpha")
    _write(src / "b.md", "bravo")
    _write(src / "sub/c.md", "charlie")
    manifest = tmp_path / "manifest.json"  # not yet written

    cs = detect_changes(src, manifest)

    assert cs.added == ("a.md", "b.md", "sub/c.md")
    assert cs.modified == ()
    assert cs.removed == ()
    assert cs.unchanged == ()
    assert cs.has_changes is True


def test_incremental_some_modified(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.md", "alpha-v1")
    _write(src / "b.md", "bravo")
    manifest = tmp_path / "manifest.json"

    # First, write manifest reflecting current state.
    write_manifest(manifest, scan_dir_sha(src), src)

    # Modify a.md, leave b.md alone.
    _write(src / "a.md", "alpha-v2")

    cs = detect_changes(src, manifest)

    assert cs.added == ()
    assert cs.modified == ("a.md",)
    assert cs.removed == ()
    assert cs.unchanged == ("b.md",)
    assert cs.has_changes is True


def test_file_removed(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.md", "alpha")
    _write(src / "b.md", "bravo")
    manifest = tmp_path / "manifest.json"

    write_manifest(manifest, scan_dir_sha(src), src)

    # Remove b.md from source.
    (src / "b.md").unlink()

    cs = detect_changes(src, manifest)

    assert cs.added == ()
    assert cs.modified == ()
    assert cs.removed == ("b.md",)
    assert cs.unchanged == ("a.md",)
    assert cs.has_changes is True


def test_file_renamed_yields_added_plus_removed(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "old-name.md", "same-content")
    manifest = tmp_path / "manifest.json"

    write_manifest(manifest, scan_dir_sha(src), src)

    # Rename: same content under new path.
    (src / "old-name.md").rename(src / "new-name.md")

    cs = detect_changes(src, manifest)

    assert cs.added == ("new-name.md",)
    assert cs.removed == ("old-name.md",)
    assert cs.modified == ()
    assert cs.has_changes is True


def test_no_changes_when_identical(tmp_path: Path):
    """Sanity: if nothing changed, all files are unchanged."""
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.md", "alpha")
    _write(src / "b.md", "bravo")
    manifest = tmp_path / "manifest.json"

    write_manifest(manifest, scan_dir_sha(src), src)

    cs = detect_changes(src, manifest)

    assert cs.added == ()
    assert cs.modified == ()
    assert cs.removed == ()
    assert cs.unchanged == ("a.md", "b.md")
    assert cs.has_changes is False


def test_glob_filters_to_md_only(tmp_path: Path):
    """When glob='*.md', non-md files (e.g. .pdf) are ignored."""
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.md", "alpha")
    _write(src / "b.pdf", "binary-blob")  # should be ignored

    cs = detect_changes(src, tmp_path / "manifest.json", glob_pattern="*.md")
    assert cs.added == ("a.md",)
    assert "b.pdf" not in cs.added


def test_load_manifest_missing_returns_empty(tmp_path: Path):
    assert {} == _load_manifest_via_module(tmp_path / "nope.json")


def test_load_manifest_malformed_returns_empty(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("not-json{", encoding="utf-8")
    assert {} == _load_manifest_via_module(p)


def _load_manifest_via_module(path: Path) -> dict:
    from meta_ops.librarian.change_detect import load_manifest
    return load_manifest(path)


def test_write_manifest_roundtrip(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.md", "alpha")
    manifest = tmp_path / "_meta" / "manifest.json"

    sha_map = scan_dir_sha(src)
    write_manifest(manifest, sha_map, src)

    raw = json.loads(manifest.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert "generated_at" in raw
    assert raw["files"] == sha_map
