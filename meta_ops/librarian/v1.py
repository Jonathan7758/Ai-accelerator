"""Phase 2: Librarian v1 主流程。

编排 v0 的 schema mirror + 4 个新源 step,沿用 RunLogger,任一 step
失败标 [degraded] 不拖整体。每个 step 单独 try/except。

Step 0:沿用 v0.sync_schemas(schema 镜像)
Step 1:pulse_source.sync_pulse_docs / sync_pulse_code(从 HK + GitHub 拉源)
Step 2:sync_docs(整理 docs 到 knowledge/pulse/docs/)
Step 4:build_code_index.build_all(LLM 加工)
Step 5:build_extracted.build_all(LLM 加工)

(Step 3 增量检测是工具模块,被 Step 2/4/5 内部调用,不独立编排)
"""
from __future__ import annotations
import json
import logging

from meta_ops.common.db import get_local_db
from meta_ops.common.logging_config import setup_logging
from meta_ops.common.run_log import RunLogger

log = logging.getLogger(__name__)


def run_librarian_v1() -> dict:
    """Run all Librarian v1 steps. Returns aggregate summary."""
    setup_logging("librarian")
    local_db = get_local_db()

    summary: dict = {}
    partial_reasons: list[str] = []

    try:
        with RunLogger(local_db, kind="librarian") as run:
            # Step 0: schema mirror (v0)
            try:
                from meta_ops.librarian.v0 import sync_schemas
                schemas = sync_schemas()
                summary["schemas"] = schemas
                if schemas.get("errors"):
                    partial_reasons.append(
                        f"[degraded] schema mirror: {len(schemas['errors'])} table(s) failed"
                    )
            except Exception as e:
                log.exception("schema mirror failed")
                summary["schemas"] = {"error": str(e)}
                partial_reasons.append(f"[degraded] schema mirror: {type(e).__name__}: {e}"[:200])

            # Step 1: Pulse src sync (docs + code)
            try:
                from meta_ops.librarian.pulse_source import (
                    sync_pulse_docs,
                    sync_pulse_code,
                )
                docs_pull = sync_pulse_docs()
                code_pull = sync_pulse_code()
                summary["pulse_src_docs"] = docs_pull
                summary["pulse_src_code"] = code_pull
                if not docs_pull.get("ok"):
                    partial_reasons.append(
                        f"[degraded] sync_pulse_docs: {docs_pull.get('error')}"
                    )
                if not code_pull.get("ok"):
                    partial_reasons.append(
                        f"[degraded] sync_pulse_code: {code_pull.get('error')}"
                    )
            except Exception as e:
                log.exception("pulse_source step failed")
                summary["pulse_src_error"] = str(e)
                partial_reasons.append(f"[degraded] pulse_source: {type(e).__name__}: {e}"[:200])

            # Step 2: docs/ sync (no LLM)
            try:
                from meta_ops.librarian.sync_docs import sync_docs
                docs_result = sync_docs()
                summary["docs_sync"] = docs_result
                if not docs_result.get("ok"):
                    partial_reasons.append(
                        f"[degraded] sync_docs: {docs_result.get('error')}"
                    )
            except Exception as e:
                log.exception("sync_docs failed")
                summary["docs_sync_error"] = str(e)
                partial_reasons.append(f"[degraded] sync_docs: {type(e).__name__}: {e}"[:200])

            # Step 4: code_index/ (LLM)
            try:
                from meta_ops.librarian.build_code_index import build_all as build_code_index
                ci_result = build_code_index(related_run_id=run.run_id)
                summary["code_index"] = ci_result
                if ci_result.get("files_failed", 0) > 0:
                    partial_reasons.append(
                        f"[degraded] code_index: {ci_result['files_failed']} file(s) failed"
                    )
            except Exception as e:
                log.exception("build_code_index failed")
                summary["code_index_error"] = str(e)
                partial_reasons.append(
                    f"[degraded] code_index: {type(e).__name__}: {e}"[:200]
                )

            # Step 5: extracted/ (LLM)
            try:
                from meta_ops.librarian.build_extracted import build_all as build_extracted
                ex_result = build_extracted(related_run_id=run.run_id)
                summary["extracted"] = ex_result
                if ex_result.get("topics_failed", 0) > 0:
                    partial_reasons.append(
                        f"[degraded] extracted: {ex_result['topics_failed']} topic(s) failed"
                    )
            except Exception as e:
                log.exception("build_extracted failed")
                summary["extracted_error"] = str(e)
                partial_reasons.append(
                    f"[degraded] extracted: {type(e).__name__}: {e}"[:200]
                )

            run.set_summary({**summary, "partial_reasons": partial_reasons})
            if partial_reasons:
                run.mark_partial(" | ".join(partial_reasons)[:1000])
    finally:
        local_db.close()

    return summary


if __name__ == "__main__":
    result = run_librarian_v1()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
