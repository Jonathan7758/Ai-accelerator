"""Phase 2 Step 2: docs/ 全量同步路径(无 LLM)。

把 pulse_src/docs/ 下的 .md 文件镜像到 knowledge/pulse/docs/,并写一份
manifest.json 给 Step 3-5 复用。

策略:
- 决策 3:同步全要(无黑/白名单),但只 .md(.pdf 不进 LLM 流水线)
- 输出层(knowledge/pulse/docs/)是给 Librarian 后续 LLM 加工准备的
- 内容相同的文件不重写(避免 mtime 噪音)
"""
from __future__ import annotations
from pathlib import Path
import json
import logging
import shutil

from meta_ops.librarian.change_detect import (
    detect_changes,
    scan_dir_sha,
    write_manifest,
)

log = logging.getLogger(__name__)

PULSE_SRC_DOCS = Path("/opt/accelerator/knowledge/pulse_src/docs")
KNOWLEDGE_DOCS = Path("/opt/accelerator/knowledge/pulse/docs")
DOCS_MANIFEST = KNOWLEDGE_DOCS / "_meta" / "manifest.json"
DOCS_GLOB = "*.md"


def sync_docs(
    source_root: Path = PULSE_SRC_DOCS,
    target_root: Path = KNOWLEDGE_DOCS,
    manifest_path: Path = DOCS_MANIFEST,
    glob_pattern: str = DOCS_GLOB,
) -> dict:
    """同步 source_root → target_root,写 manifest。

    Returns: {ok, files_total, files_added, files_modified, files_removed, error}
    """
    if not source_root.is_dir():
        return {
            "ok": False,
            "error": f"source_root not a dir: {source_root}",
            "files_total": 0,
            "files_added": 0,
            "files_modified": 0,
            "files_removed": 0,
        }

    target_root.mkdir(parents=True, exist_ok=True)

    changes = detect_changes(source_root, manifest_path, glob_pattern)

    for rel in changes.added + changes.modified:
        src = source_root / rel
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        log.info("synced: %s", rel)

    for rel in changes.removed:
        dst = target_root / rel
        if dst.is_file():
            dst.unlink()
            log.info("removed: %s", rel)

    new_manifest = scan_dir_sha(source_root, glob_pattern)
    write_manifest(manifest_path, new_manifest, source_root)

    return {
        "ok": True,
        "files_total": len(new_manifest),
        "files_added": len(changes.added),
        "files_modified": len(changes.modified),
        "files_removed": len(changes.removed),
        "error": None,
    }


def main() -> dict:
    from meta_ops.common.logging_config import setup_logging

    setup_logging("sync_docs")
    result = sync_docs()
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
