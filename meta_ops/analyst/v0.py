"""Phase 2 Analyst v0 — 主流程编排。

Step 3 + Step 4 一起实现:
- 编排 context.assemble_context() + prompts/analyst_v0_weekly.md + LLM 调用
- 输出 reports/<week_iso>.md
- 维护 reports/_meta/index.json
- 沿用 RunLogger(kind='analyst')+ 双轨 LLM 留痕
- 失败优雅:数据稀疏标 [deferred],LLM 失败标 [degraded] 写 stub 报告

入口:
  python -m meta_ops.analyst.v0                # 跑当前 ISO 周
  python -m meta_ops.analyst.v0 --week 2026W18 # 跑指定周
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import sys

from meta_ops.common.db import get_local_db
from meta_ops.common.logging_config import setup_logging
from meta_ops.common.run_log import RunLogger
from meta_ops.librarian.llm_client import call_claude

from meta_ops.analyst.context import (
    assemble_context,
    current_iso_week,
)

log = logging.getLogger(__name__)

REPORTS_ROOT = Path("/opt/accelerator/reports")
REPORTS_INDEX = REPORTS_ROOT / "_meta" / "index.json"
PROMPT_TEMPLATE = Path("/opt/accelerator/prompts/analyst_v0_weekly.md")


def _load_template() -> str:
    if not PROMPT_TEMPLATE.is_file():
        raise FileNotFoundError(f"prompt template missing: {PROMPT_TEMPLATE}")
    return PROMPT_TEMPLATE.read_text(encoding="utf-8")


def _render_user_message(ctx: dict) -> str:
    """Render assembled context into a single user message string for the LLM."""
    parts: list[str] = []

    parts.append(f"# Analyst 周报任务\n")
    parts.append(f"**目标周** : `{ctx['current_week']}`")
    parts.append(f"**生成时刻** : {ctx['generated_at']}")
    parts.append(f"**取数窗口** : 本周 + 过去 {ctx['weeks_back']} 周\n")

    # 健康
    h = ctx["run_log_health"]
    parts.append("## 健康检查上下文\n")
    parts.append(
        f"- watcher 过去 7 天成功次数:{h['watcher_ok_runs_7d']} "
        f"(data_sparse={h['data_sparse']})"
    )
    parts.append(
        f"- librarian 过去 7 天成功次数:{h['librarian_ok_runs_7d']} "
        f"(librarian_fresh={h['librarian_fresh']})"
    )
    parts.append(f"- by_kind 详细:{json.dumps(h['by_kind'], ensure_ascii=False)}\n")

    # ops_metrics(本周 + 过去 4 周)
    parts.append("## ops_metrics(本周 + 过去 4 周)\n")
    for w in ctx["metrics"]["weeks"]:
        parts.append(
            f"### {w['week']}: n_rows={w['n_rows']} "
            f"by_subject_type={json.dumps(w['by_subject_type'], ensure_ascii=False)}"
        )
        if w["metric_keys_seen"]:
            parts.append(f"metric_keys: {w['metric_keys_seen']}")
        if w["sample"]:
            parts.append("代表样本:\n```json")
            parts.append(json.dumps(w["sample"], ensure_ascii=False, indent=2, default=str))
            parts.append("```")
        parts.append("")

    # ops_decisions
    parts.append("## ops_decisions(过去 4 周)\n")
    if ctx["decisions"]:
        parts.append("```json")
        parts.append(json.dumps(ctx["decisions"], ensure_ascii=False, indent=2, default=str))
        parts.append("```")
    else:
        parts.append("(无)Phase 2 期间通常为空,Analyst 不必生成验证回填段。\n")

    # ops_playbook
    parts.append("## ops_playbook(active + confidence ≥ 0.5)\n")
    if ctx["playbook"]:
        parts.append("```json")
        parts.append(json.dumps(ctx["playbook"], ensure_ascii=False, indent=2, default=str))
        parts.append("```")
    else:
        parts.append("(无)\n")

    # extracted topics
    parts.append("## 业务知识(knowledge/pulse/extracted/)\n")
    for name, content in ctx["extracted_topics"].items():
        parts.append(f"### topic = `{name}`\n")
        parts.append(content)
        parts.append("\n---\n")

    # 上周报告
    prev = ctx["previous_report"]
    parts.append(f"## 上周报告({prev['week']})\n")
    if prev["content"]:
        parts.append(prev["content"])
    else:
        parts.append(f"(无 — 路径 `{prev['path']}` 不存在)\n")

    parts.append("\n## 任务\n")
    parts.append(
        "按 system prompt 给定的章节结构,输出本周报告 markdown。直接输出,"
        "不要前言、不要外层代码围栏。"
    )

    return "\n".join(parts)


def _write_report(week_iso: str, content: str) -> Path:
    """Write report, suffix _v2/_v3/... if same-week file exists."""
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    base = REPORTS_ROOT / f"{week_iso}.md"
    if not base.is_file():
        base.write_text(content, encoding="utf-8")
        return base

    # Find next available _vN suffix
    n = 2
    while True:
        candidate = REPORTS_ROOT / f"{week_iso}_v{n}.md"
        if not candidate.is_file():
            candidate.write_text(content, encoding="utf-8")
            return candidate
        n += 1


def _update_index(report_path: Path, week_iso: str, status: str) -> None:
    REPORTS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    if REPORTS_INDEX.is_file():
        try:
            data = json.loads(REPORTS_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"version": 1, "reports": []}
    else:
        data = {"version": 1, "reports": []}

    entry = {
        "week": week_iso,
        "path": report_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": report_path.stat().st_size,
        "status": status,
    }
    # Replace existing entry for same path, else append
    reports = [r for r in data.get("reports", []) if r.get("path") != entry["path"]]
    reports.append(entry)
    reports.sort(key=lambda r: (r["week"], r["path"]))
    data["reports"] = reports
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    REPORTS_INDEX.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def run_analyst_v0(week_iso: str | None = None) -> dict:
    """Run one Analyst v0 cycle. Returns summary dict."""
    setup_logging("analyst")
    if week_iso is None:
        week_iso = current_iso_week()

    local_db = get_local_db()
    summary: dict = {"week": week_iso}
    partial_reasons: list[str] = []

    try:
        with RunLogger(local_db, kind="analyst") as run:
            # ── Step A: assemble context ──
            try:
                ctx = assemble_context(local_db, current_week_iso=week_iso, weeks_back=4)
                summary["context_metrics"] = {
                    "n_decisions": len(ctx["decisions"]),
                    "n_playbook": len(ctx["playbook"]),
                    "n_extracted_topics": len(ctx["extracted_topics"]),
                    "data_sparse": ctx["run_log_health"]["data_sparse"],
                    "previous_report_present": ctx["previous_report"]["content"] is not None,
                }
                if ctx["run_log_health"]["data_sparse"]:
                    partial_reasons.append(
                        f"[deferred] data_sparse: watcher_ok_runs_7d="
                        f"{ctx['run_log_health']['watcher_ok_runs_7d']}"
                    )
            except Exception as e:
                log.exception("assemble_context failed")
                summary["context_error"] = str(e)
                partial_reasons.append(f"[degraded] context: {type(e).__name__}: {e}"[:200])
                run.set_summary({**summary, "partial_reasons": partial_reasons})
                run.mark_partial(" | ".join(partial_reasons)[:1000])
                return summary

            # ── Step B: render user message + load template ──
            try:
                system = _load_template()
                user = _render_user_message(ctx)
                summary["prompt_lengths"] = {
                    "system_chars": len(system),
                    "user_chars": len(user),
                }
            except Exception as e:
                log.exception("prompt render failed")
                summary["render_error"] = str(e)
                partial_reasons.append(f"[degraded] render: {type(e).__name__}: {e}"[:200])
                run.set_summary({**summary, "partial_reasons": partial_reasons})
                run.mark_partial(" | ".join(partial_reasons)[:1000])
                return summary

            # ── Step C: call Claude ──
            llm_result = call_claude(
                kind="analyst",
                target_path=week_iso,
                prompt_template="prompts/analyst_v0_weekly.md",
                system=system,
                user=user,
                related_run_id=run.run_id,
                max_tokens=16000,
            )
            summary["llm"] = {
                "ok": llm_result["ok"],
                "cost_usd": llm_result["cost_usd"],
                "duration_seconds": llm_result["duration_seconds"],
                "usage": llm_result["usage"],
                "error": llm_result["error"],
            }

            if not llm_result["ok"]:
                # Write a stub report so downstream still has something
                stub = (
                    f"# 周报 {week_iso}\n\n"
                    f"> ⚠️ Analyst LLM 调用失败,本份为 stub 报告。\n\n"
                    f"## 失败信息\n\n```\n{llm_result['error']}\n```\n\n"
                    f"## 上下文摘要(供调试)\n\n```json\n"
                    f"{json.dumps(summary['context_metrics'], indent=2, ensure_ascii=False)}\n```\n"
                )
                report_path = _write_report(week_iso, stub)
                _update_index(report_path, week_iso, status="stub")
                summary["report_path"] = str(report_path)
                partial_reasons.append(
                    f"[degraded] llm_failed: {(llm_result['error'] or '')[:120]}"
                )
                run.set_summary({**summary, "partial_reasons": partial_reasons})
                run.mark_partial(" | ".join(partial_reasons)[:1000])
                return summary

            # ── Step D: write report + index ──
            report_path = _write_report(week_iso, llm_result["response_text"])
            _update_index(report_path, week_iso, status="ok")
            summary["report_path"] = str(report_path)

            run.set_summary({**summary, "partial_reasons": partial_reasons})
            if partial_reasons:
                run.mark_partial(" | ".join(partial_reasons)[:1000])
    finally:
        local_db.close()

    return summary


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", default=None,
                        help="ISO week like 2026W18 (default: current)")
    args = parser.parse_args()

    result = run_analyst_v0(week_iso=args.week)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return result


if __name__ == "__main__":
    main()
