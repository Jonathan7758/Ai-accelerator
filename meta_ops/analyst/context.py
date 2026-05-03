"""Phase 2 Analyst v0 — 上下文组装(纯函数,可单测)。

把 DB + filesystem 数据抽出来,组成确定性 dict,给 LLM 调用使用。
所有函数纯函数:同输入 → 同输出。

入口:
- gather_metrics(local_db, current_week_iso, weeks_back)
- gather_decisions(local_db, weeks_back)
- gather_playbook(local_db)
- load_extracted_topics()
- gather_run_log_health(local_db, days)
- load_previous_report(reports_root, current_week_iso)
- assemble_context(local_db, current_week_iso, weeks_back)  ← 顶层组合
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import logging

import psycopg2.extras

log = logging.getLogger(__name__)

SG_TZ = ZoneInfo("Asia/Singapore")
EXTRACTED_ROOT = Path("/opt/accelerator/knowledge/pulse/extracted")
KNOWLEDGE_LAST_SYNCED = Path("/opt/accelerator/knowledge/pulse/_meta/last_synced_at.txt")
REPORTS_ROOT = Path("/opt/accelerator/reports")


# ── ISO week 工具 ────────────────────────────────────────────

def current_iso_week() -> str:
    """当前 SG 周的 ISO 标识,如 '2026W18'。"""
    now_sg = datetime.now(SG_TZ).date()
    y, w, _ = now_sg.isocalendar()
    return f"{y}W{w:02d}"


def parse_iso_week(week_iso: str) -> tuple[int, int]:
    if "W" not in week_iso:
        raise ValueError(f"bad iso week: {week_iso!r}")
    ystr, wstr = week_iso.split("W", 1)
    return int(ystr), int(wstr)


def previous_iso_week(week_iso: str) -> str:
    y, w = parse_iso_week(week_iso)
    monday = date.fromisocalendar(y, w, 1)
    prev = monday - timedelta(days=7)
    py, pw, _ = prev.isocalendar()
    return f"{py}W{pw:02d}"


def iso_week_range_utc(week_iso: str) -> tuple[datetime, datetime]:
    """该 ISO 周的 [周一 00:00 SGT, 下周一 00:00 SGT) 区间,转 UTC。"""
    y, w = parse_iso_week(week_iso)
    monday_sg = date.fromisocalendar(y, w, 1)
    sunday_sg = date.fromisocalendar(y, w, 7)
    start_sg = datetime.combine(monday_sg, datetime.min.time(), tzinfo=SG_TZ)
    end_sg = datetime.combine(sunday_sg + timedelta(days=1), datetime.min.time(), tzinfo=SG_TZ)
    return start_sg.astimezone(timezone.utc), end_sg.astimezone(timezone.utc)


# ── ops_metrics 聚合 ─────────────────────────────────────────

def gather_metrics(
    local_db,
    current_week_iso: str | None = None,
    weeks_back: int = 4,
    sample_size_per_week: int = 5,
) -> dict:
    """Aggregate ops_metrics by ISO week (current + past `weeks_back`)."""
    if current_week_iso is None:
        current_week_iso = current_iso_week()

    cur_y, cur_w = parse_iso_week(current_week_iso)
    base_monday = date.fromisocalendar(cur_y, cur_w, 1)

    weeks: list[dict] = []
    for offset in range(0, weeks_back + 1):
        target_monday = base_monday - timedelta(weeks=offset)
        ty, tw, _ = target_monday.isocalendar()
        week_iso = f"{ty}W{tw:02d}"
        start_utc, end_utc = iso_week_range_utc(week_iso)

        with local_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT subject_type, subject_id, snapshot_at, metrics, source
                FROM ops_metrics
                WHERE snapshot_at >= %s AND snapshot_at < %s
                ORDER BY snapshot_at ASC
                """,
                (start_utc, end_utc),
            )
            rows = cur.fetchall()

        by_subject_type: dict[str, int] = {}
        metric_keys: set[str] = set()
        for r in rows:
            by_subject_type[r["subject_type"]] = by_subject_type.get(r["subject_type"], 0) + 1
            m = r.get("metrics") if isinstance(r, dict) else r["metrics"]
            if isinstance(m, dict):
                metric_keys.update(m.keys())

        sample: list[dict] = []
        for r in rows[:sample_size_per_week]:
            sa = r["snapshot_at"]
            sample.append({
                "subject_type": r["subject_type"],
                "subject_id": r["subject_id"],
                "snapshot_at": sa.isoformat() if hasattr(sa, "isoformat") else str(sa),
                "metrics": r["metrics"],
                "source": r["source"],
            })

        weeks.append({
            "week": week_iso,
            "n_rows": len(rows),
            "by_subject_type": by_subject_type,
            "metric_keys_seen": sorted(metric_keys),
            "sample": sample,
        })

    return {
        "current_week": current_week_iso,
        "weeks_back": weeks_back,
        "weeks": weeks,
    }


# ── ops_decisions / ops_playbook ─────────────────────────────

def gather_decisions(local_db, weeks_back: int = 4) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks_back)
    with local_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, tenant_id, decided_at, decision_type, subject,
                   rationale, evidence, verification_plan, status
            FROM ops_decisions
            WHERE decided_at >= %s
            ORDER BY decided_at DESC
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": str(r["id"]),
            "tenant_id": r["tenant_id"],
            "decided_at": r["decided_at"].isoformat(),
            "decision_type": r["decision_type"],
            "subject": r["subject"],
            "rationale": r["rationale"],
            "evidence": r["evidence"],
            "verification_plan": r["verification_plan"],
            "status": r["status"],
        })
    return out


def gather_playbook(local_db) -> list[dict]:
    with local_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, tenant_id, pattern_name, context, recipe,
                   confidence, last_validated_at, status
            FROM ops_playbook
            WHERE status = 'active' AND confidence >= 0.5
            ORDER BY confidence DESC
            """
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": str(r["id"]),
            "pattern_name": r["pattern_name"],
            "context": r["context"],
            "recipe": r["recipe"],
            "confidence": float(r["confidence"]),
            "last_validated_at": (
                r["last_validated_at"].isoformat() if r["last_validated_at"] else None
            ),
        })
    return out


# ── 知识库 / 报告 / 健康 ──────────────────────────────────────

def load_extracted_topics(root: Path = EXTRACTED_ROOT) -> dict[str, str]:
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.glob("*.md")):
        out[p.stem] = p.read_text(encoding="utf-8")
    return out


def gather_run_log_health(local_db, days: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with local_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT kind, status
            FROM l2_run_log
            WHERE started_at >= %s
            """,
            (cutoff,),
        )
        rows = cur.fetchall()

    by_kind: dict[str, dict[str, int]] = {}
    for r in rows:
        kind = r["kind"]
        by_kind.setdefault(kind, {"ok": 0, "partial": 0, "failed": 0, "running": 0})
        st = r["status"]
        if st in by_kind[kind]:
            by_kind[kind][st] += 1

    watcher = by_kind.get("watcher", {})
    librarian = by_kind.get("librarian", {})
    watcher_ok = watcher.get("ok", 0) + watcher.get("partial", 0)
    librarian_ok = librarian.get("ok", 0) + librarian.get("partial", 0)

    librarian_fresh = False
    if KNOWLEDGE_LAST_SYNCED.is_file():
        try:
            ts = datetime.fromisoformat(KNOWLEDGE_LAST_SYNCED.read_text().strip())
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            librarian_fresh = age_h < 36
        except (ValueError, OSError):
            pass

    return {
        "by_kind": by_kind,
        "watcher_ok_runs_7d": watcher_ok,
        "librarian_ok_runs_7d": librarian_ok,
        "librarian_fresh": librarian_fresh,
        "data_sparse": watcher_ok < 5,
    }


def load_previous_report(
    reports_root: Path = REPORTS_ROOT,
    current_week_iso: str | None = None,
) -> dict:
    """Returns {'week', 'path', 'content'} — content is None if file missing."""
    if current_week_iso is None:
        current_week_iso = current_iso_week()
    prev = previous_iso_week(current_week_iso)
    path = reports_root / f"{prev}.md"
    if path.is_file():
        return {
            "week": prev,
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
        }
    return {"week": prev, "path": None, "content": None}


# ── 顶层组合 ─────────────────────────────────────────────────

def assemble_context(
    local_db,
    current_week_iso: str | None = None,
    weeks_back: int = 4,
) -> dict:
    if current_week_iso is None:
        current_week_iso = current_iso_week()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_week": current_week_iso,
        "weeks_back": weeks_back,
        "metrics": gather_metrics(local_db, current_week_iso, weeks_back),
        "decisions": gather_decisions(local_db, weeks_back),
        "playbook": gather_playbook(local_db),
        "extracted_topics": load_extracted_topics(),
        "run_log_health": gather_run_log_health(local_db, days=7),
        "previous_report": load_previous_report(REPORTS_ROOT, current_week_iso),
    }


def main() -> None:
    """python -m meta_ops.analyst.context — print summary JSON (no LLM)."""
    from meta_ops.common.db import get_local_db
    from meta_ops.common.logging_config import setup_logging

    setup_logging("analyst")
    db = get_local_db()
    try:
        ctx = assemble_context(db)
        summary = {
            "generated_at": ctx["generated_at"],
            "current_week": ctx["current_week"],
            "metrics_summary": [
                {"week": w["week"], "n_rows": w["n_rows"], "by_subject_type": w["by_subject_type"]}
                for w in ctx["metrics"]["weeks"]
            ],
            "n_decisions": len(ctx["decisions"]),
            "n_playbook": len(ctx["playbook"]),
            "extracted_topics": list(ctx["extracted_topics"].keys()),
            "run_log_health": ctx["run_log_health"],
            "previous_report_present": ctx["previous_report"]["content"] is not None,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
