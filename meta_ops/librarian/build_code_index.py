"""Phase 2 Step 4: code_index/ LLM 加工。

对决策 4 锁定的 6 个关键 .py 文件,调 Claude Sonnet 4.6 产出"修改导航图"
markdown,落到 knowledge/pulse/code_index/<相对路径>.md。

增量逻辑:用 change_detect 比对 pulse_src/code/ 的当前 sha 与 manifest;
只对 added / modified 的文件重生,unchanged 的跳过(0 LLM 调用)。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import logging

from meta_ops.librarian.change_detect import (
    detect_changes,
    scan_dir_sha,
    write_manifest,
)
from meta_ops.librarian.llm_client import call_claude

log = logging.getLogger(__name__)

PULSE_CODE_ROOT = Path("/opt/accelerator/knowledge/pulse_src/code")
CODE_INDEX_ROOT = Path("/opt/accelerator/knowledge/pulse/code_index")
CODE_MANIFEST = CODE_INDEX_ROOT / "_meta" / "manifest.json"
PROMPT_TEMPLATE_PATH = Path("/opt/accelerator/prompts/code_index_v1.md")

# Spec §5 决策 4 锁定的 6 个起步文件(相对 PULSE_CODE_ROOT 的路径)
TARGET_FILES = [
    "version1/hk_node/services/daily_workflow.py",
    "version1/hk_node/services/history_engine/content_matrix.py",
    "version1/hk_node/services/publisher/telegram_review.py",
    "version1/hk_node/services/content_engine/wechat_draft_adapter.py",
    "version1/hk_node/services/cover_engine/dynasty_prompts.py",
    "version1/hk_node/services/dashboard/collector.py",
]


def _load_system_prompt() -> str:
    if not PROMPT_TEMPLATE_PATH.is_file():
        raise FileNotFoundError(f"prompt template missing: {PROMPT_TEMPLATE_PATH}")
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def _read_source_file(rel: str) -> str | None:
    """Return source text for rel; None if not present in pulse_src/code."""
    src = PULSE_CODE_ROOT / rel
    if not src.is_file():
        log.warning("target source missing: %s", rel)
        return None
    return src.read_text(encoding="utf-8", errors="replace")


def build_one(
    rel: str,
    *,
    related_run_id: str | None = None,
    force: bool = False,
) -> dict:
    """Build code_index for a single file.

    Returns: {ok, target_path, regenerated, skipped_reason, cost_usd, error}
    """
    source_text = _read_source_file(rel)
    if source_text is None:
        return {
            "ok": False, "target_path": rel, "regenerated": False,
            "skipped_reason": None,
            "cost_usd": 0.0, "error": "source file missing",
        }

    system = _load_system_prompt()
    user = f"# 文件:`{rel}`\n\n## 源文件内容(共 {len(source_text.splitlines())} 行)\n\n```python\n{source_text}\n```"

    result = call_claude(
        kind="code_index",
        target_path=rel,
        prompt_template="prompts/code_index_v1.md",
        system=system,
        user=user,
        related_run_id=related_run_id,
    )

    if not result["ok"]:
        return {
            "ok": False, "target_path": rel, "regenerated": False,
            "skipped_reason": None,
            "cost_usd": result["cost_usd"],
            "error": result["error"],
        }

    out_path = CODE_INDEX_ROOT / f"{rel}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result["response_text"], encoding="utf-8")
    log.info("wrote code_index: %s (cost $%.4f)", out_path, result["cost_usd"])

    # Update manifest entry for this file (so a subsequent build_all skips it)
    from meta_ops.librarian.change_detect import sha256_file, load_manifest
    src = PULSE_CODE_ROOT / rel
    manifest = load_manifest(CODE_MANIFEST)
    manifest[rel] = sha256_file(src)
    write_manifest(CODE_MANIFEST, manifest, PULSE_CODE_ROOT)

    return {
        "ok": True, "target_path": rel, "regenerated": True,
        "skipped_reason": None,
        "cost_usd": result["cost_usd"],
        "error": None,
    }


def build_all(
    *,
    related_run_id: str | None = None,
    force: bool = False,
) -> dict:
    """Run incremental build over TARGET_FILES.

    Skips files whose source sha matches the previous manifest entry
    (unless force=True).

    Returns aggregate dict.
    """
    CODE_INDEX_ROOT.mkdir(parents=True, exist_ok=True)

    # Build sha map for the 6 targets only (subset of full repo)
    current_sha: dict[str, str] = {}
    for rel in TARGET_FILES:
        src = PULSE_CODE_ROOT / rel
        if src.is_file():
            from meta_ops.librarian.change_detect import sha256_file
            current_sha[rel] = sha256_file(src)

    # Compare against manifest
    from meta_ops.librarian.change_detect import load_manifest
    previous = load_manifest(CODE_MANIFEST)

    summary = {
        "files_total": len(TARGET_FILES),
        "files_built": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "total_cost_usd": 0.0,
        "details": [],
    }

    for rel in TARGET_FILES:
        if rel not in current_sha:
            summary["files_failed"] += 1
            summary["details"].append({
                "target_path": rel, "status": "missing", "cost_usd": 0.0,
            })
            continue

        out_path = CODE_INDEX_ROOT / f"{rel}.md"
        same_sha = previous.get(rel) == current_sha[rel]
        if same_sha and out_path.is_file() and not force:
            summary["files_skipped"] += 1
            summary["details"].append({
                "target_path": rel, "status": "skipped_unchanged", "cost_usd": 0.0,
            })
            continue

        result = build_one(rel, related_run_id=related_run_id)
        summary["total_cost_usd"] = round(
            summary["total_cost_usd"] + result["cost_usd"], 6
        )
        if result["ok"]:
            summary["files_built"] += 1
            summary["details"].append({
                "target_path": rel, "status": "built", "cost_usd": result["cost_usd"],
            })
        else:
            summary["files_failed"] += 1
            summary["details"].append({
                "target_path": rel, "status": "failed",
                "cost_usd": result["cost_usd"], "error": result["error"],
            })

    write_manifest(CODE_MANIFEST, current_sha, PULSE_CODE_ROOT)
    return summary


def main() -> dict:
    from meta_ops.common.logging_config import setup_logging
    import sys
    setup_logging("build_code_index")

    force = "--force" in sys.argv
    only = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only = sys.argv[idx + 1]

    if only:
        result = {"single_run": build_one(only, force=force)}
    else:
        result = build_all(force=force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
