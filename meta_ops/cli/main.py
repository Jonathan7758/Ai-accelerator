"""acc 命令行工具入口。"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sys

import click
import psycopg2.extras

from meta_ops.common.db import get_local_db

SG_TZ = ZoneInfo("Asia/Singapore")
KNOWLEDGE_META = Path("/opt/accelerator/knowledge/pulse/_meta/last_synced_at.txt")


@click.group()
def cli():
    """Accelerator L2 management CLI."""
    pass


# ─────────────────────────────────────────────
# acc status helpers
# ─────────────────────────────────────────────

def _print_latest_report(reports_root: Path) -> None:
    """One line:'Latest weekly report: 2026W18.md (3.2 days ago)' or warning."""
    print("\n── Latest weekly report ──")
    if not reports_root.is_dir():
        print("  ⚠️ no reports/ directory — Analyst v0 not run yet")
        return
    files = sorted(reports_root.glob("*.md"))
    if not files:
        print("  ⚠️ no reports yet")
        return
    latest = files[-1]
    mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    age_d = (datetime.now(timezone.utc) - mtime).total_seconds() / 86400
    symbol = "✅" if age_d < 10 else "⚠️"
    print(f"  {symbol} {latest.name}  ({age_d:.1f} days ago)")


def _print_source_freshness(label: str, root: Path, manifest_path: Path, glob: str) -> None:
    """Print one line: '<label>: N files, <age>h ago' or 'never built'."""
    if not root.is_dir():
        print(f"  {label:<11}: ❌ never built")
        return
    n_files = sum(1 for p in root.rglob(glob) if p.is_file())
    if not manifest_path.is_file():
        print(f"  {label:<11}: 📁 {n_files} files (no manifest)")
        return
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        gen = m.get("generated_at")
        if not gen:
            print(f"  {label:<11}: 📁 {n_files} files (manifest unparseable)")
            return
        last = datetime.fromisoformat(gen)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        symbol = "✅" if age < 36 else "⚠️"
        print(f"  {label:<11}: {symbol} {n_files} files, {age:.1f}h ago")
    except (OSError, json.JSONDecodeError) as e:
        print(f"  {label:<11}: ⚠️  {n_files} files (manifest read err: {e})")


def _classify_run_status(status: str, reasons) -> str:
    """把 l2_run_log 行映射成显示态。

    - status='partial' 的行根据 summary['partial_reasons'] 前缀细分:
      * 全部 [deferred] → 'deferred' (设计行为, 不需关注)
      * 任一 [degraded] / 无前缀 → 'degraded' (需关注)
    - 老数据 (无前缀) 按字面包含 'deferred' 兜底, 否则归 degraded。
      过去 7 天窗口期满后此分支自然失效。
    """
    if status != 'partial':
        return status
    if not reasons:
        return 'degraded'
    deferred_count = 0
    for r in reasons:
        if not isinstance(r, str):
            return 'degraded'
        if r.startswith('[deferred]'):
            deferred_count += 1
        elif r.startswith('[degraded]'):
            return 'degraded'
        elif 'deferred' in r:  # 老数据兜底
            deferred_count += 1
        else:
            return 'degraded'
    return 'deferred' if deferred_count == len(reasons) else 'degraded'


# ─────────────────────────────────────────────
# acc status
# ─────────────────────────────────────────────

@cli.command()
def status():
    """显示 L2 整体运行状态(过去 7 天)。"""
    db = get_local_db()

    print("=" * 60)
    print(f"  Accelerator L2 Status @ {datetime.now(SG_TZ).strftime('%Y-%m-%d %H:%M %Z')}")
    print("=" * 60)

    # Librarian freshness
    if KNOWLEDGE_META.exists():
        last = datetime.fromisoformat(KNOWLEDGE_META.read_text().strip())
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        symbol = "✅" if age < 36 else "⚠️"
        print(f"\n{symbol} Librarian last synced: {age:.1f}h ago")
    else:
        print("\n❌ Librarian never synced")

    # Phase 2 sources(docs / code_index / extracted)
    print("\n── Librarian v1 sources ──")
    _print_source_freshness(
        "docs",
        Path("/opt/accelerator/knowledge/pulse/docs"),
        Path("/opt/accelerator/knowledge/pulse/docs/_meta/manifest.json"),
        "*.md",
    )
    _print_source_freshness(
        "code_index",
        Path("/opt/accelerator/knowledge/pulse/code_index"),
        Path("/opt/accelerator/knowledge/pulse/code_index/_meta/manifest.json"),
        "*.md",
    )
    _print_source_freshness(
        "extracted",
        Path("/opt/accelerator/knowledge/pulse/extracted"),
        Path("/opt/accelerator/knowledge/pulse/extracted/_meta/manifest.json"),
        "*.md",
    )

    # Analyst latest weekly report
    _print_latest_report(Path("/opt/accelerator/reports"))

    # Run log 过去 7 天 — 按"显示状态"聚合 (区分 deferred / degraded)
    # partial 行在 DB 一律 status='partial';区分逻辑由本视图基于
    # summary->'partial_reasons' 中的前缀 [deferred] / [degraded] 完成。
    # 详见 PROJECT_BLUEPRINT.md §6.8.4。
    print("\n── Run Log (past 7 days) ──")
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT kind, status, summary->'partial_reasons' AS reasons
            FROM l2_run_log
            WHERE started_at > now() - interval '7 days'
        """)
        rows = cur.fetchall()

    if not rows:
        print("  (no runs)")
    else:
        by_kind: dict = {}
        for r in rows:
            display = _classify_run_status(r['status'], r['reasons'])
            by_kind.setdefault(r['kind'], {})
            by_kind[r['kind']][display] = by_kind[r['kind']].get(display, 0) + 1

        sym_map = {
            'ok': '✅', 'deferred': '🟡', 'degraded': '⚠️',
            'failed': '❌', 'running': '⏳',
        }
        order = ('ok', 'deferred', 'degraded', 'failed', 'running')

        for kind in sorted(by_kind):
            stats = by_kind[kind]
            line = f"  {kind:<12}"
            for stat in order:
                if stat in stats:
                    line += f"  {sym_map[stat]}{stats[stat]:<3}"
            print(line)
        print("  legend: ✅ ok  🟡 deferred (by-design)  ⚠️ degraded (attention)  ❌ failed  ⏳ running")

    # ops_metrics 行数
    print("\n── ops_metrics ──")
    with db.cursor() as cur:
        cur.execute("""
            SELECT subject_type, count(*)
            FROM ops_metrics
            WHERE snapshot_at > now() - interval '7 days'
            GROUP BY subject_type
            ORDER BY subject_type
        """)
        rows = cur.fetchall()
        if not rows:
            print("  (none)")
        else:
            for st, n in rows:
                print(f"  {st:<20} {n:>6}")

    # 需关注的运行: failed + degraded (不含 deferred 噪声)
    print("\n── Recent issues (failed / degraded, past 7 days) ──")
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT kind, status, started_at, error_message,
                   summary->'partial_reasons' AS reasons
            FROM l2_run_log
            WHERE started_at > now() - interval '7 days'
              AND status IN ('failed', 'partial')
            ORDER BY started_at DESC LIMIT 20
        """)
        rows = cur.fetchall()

    issue_rows = []
    for r in rows:
        display = _classify_run_status(r['status'], r['reasons'])
        if display in ('failed', 'degraded'):
            issue_rows.append((display, r))

    if not issue_rows:
        print("  (none)")
    else:
        for display, r in issue_rows[:5]:
            ts = r['started_at'].astimezone(SG_TZ).strftime('%m-%d %H:%M')
            sym = '❌' if display == 'failed' else '⚠️'
            msg = (r['error_message'] or '')[:80]
            print(f"  {sym} {ts}  {r['kind']:<10}  {msg}")

    print()
    db.close()


# ─────────────────────────────────────────────
# acc librarian / watcher
# ─────────────────────────────────────────────

@cli.group()
def librarian():
    """Librarian 操作。"""
    pass


@librarian.command('run')
@click.option('--v0', 'use_v0', is_flag=True, help='Run schema-mirror only (v0)')
def librarian_run(use_v0):
    """手动触发 Librarian 同步(默认 v1 全流程,--v0 只跑 schema mirror)。"""
    if use_v0:
        from meta_ops.librarian.v0 import run_librarian_v0
        result = run_librarian_v0()
    else:
        from meta_ops.librarian.v1 import run_librarian_v1
        result = run_librarian_v1()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


@cli.group()
def analyst():
    """Analyst 操作。"""
    pass


@analyst.command('run')
@click.option('--week', 'week_iso', default=None,
              help='ISO week like 2026W18 (default: current)')
def analyst_run(week_iso):
    """手动触发 Analyst v0 周报。"""
    from meta_ops.analyst.v0 import run_analyst_v0
    result = run_analyst_v0(week_iso=week_iso)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


@analyst.command('latest')
@click.option('--print', 'print_content', is_flag=True,
              help='Print full report content (default: just print path)')
def analyst_latest(print_content):
    """打印最新的 Analyst 周报路径(或内容)。"""
    reports_root = Path("/opt/accelerator/reports")
    if not reports_root.is_dir():
        click.echo("(no reports/ directory)")
        return
    files = sorted(reports_root.glob("*.md"))
    if not files:
        click.echo("(no reports yet)")
        return
    latest = files[-1]
    if print_content:
        click.echo(latest.read_text(encoding='utf-8'))
    else:
        click.echo(str(latest))


@cli.group()
def watcher():
    """Watcher 操作。"""
    pass


@watcher.command('run')
@click.argument('target_date', required=False, type=click.DateTime(['%Y-%m-%d']))
def watcher_run(target_date):
    """手动触发 Watcher。不指定日期则跑昨日。"""
    from meta_ops.watcher.v0 import run_watcher_v0
    d = target_date.date() if target_date else None
    result = run_watcher_v0(d)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────
# acc backfill
# ─────────────────────────────────────────────

@cli.command()
@click.argument('date_from', type=click.DateTime(['%Y-%m-%d']))
@click.argument('date_to', type=click.DateTime(['%Y-%m-%d']))
def backfill(date_from, date_to):
    """补一段日期区间的 Watcher 数据(包含 from 和 to)。"""
    from meta_ops.watcher.v0 import run_watcher_v0

    d_from = date_from.date()
    d_to = date_to.date()
    if d_from > d_to:
        click.echo("ERROR: date_from > date_to", err=True)
        sys.exit(1)

    days = (d_to - d_from).days + 1
    if days > 31:
        click.echo(f"ERROR: backfill range too large ({days} days). Max 31.", err=True)
        sys.exit(1)

    click.echo(f"Backfilling {days} days from {d_from} to {d_to}...")

    success = 0
    failed = []
    cur = d_from
    while cur <= d_to:
        try:
            result = run_watcher_v0(cur)
            success += 1
            click.echo(f"  [{cur}] OK  {result['metrics_inserted']} metrics")
        except Exception as e:
            failed.append((cur.isoformat(), str(e)[:100]))
            click.echo(f"  [{cur}] FAILED  {e}", err=True)
        cur += timedelta(days=1)

    click.echo(f"\nDone: {success}/{days} succeeded.")
    if failed:
        click.echo(f"Failed dates: {failed}")


if __name__ == '__main__':
    cli()
