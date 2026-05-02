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

    # Run log 过去 7 天
    print("\n── Run Log (past 7 days) ──")
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT kind, status, count(*) AS n
            FROM l2_run_log
            WHERE started_at > now() - interval '7 days'
            GROUP BY kind, status
            ORDER BY kind, status
        """)
        rows = cur.fetchall()

    if not rows:
        print("  (no runs)")
    else:
        # 透视
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r['kind'], {})[r['status']] = r['n']

        for kind, stats in by_kind.items():
            line = f"  {kind:<12}"
            for stat in ('ok', 'partial', 'failed', 'running'):
                if stat in stats:
                    sym = {'ok': '✅', 'partial': '⚠️', 'failed': '❌', 'running': '⏳'}[stat]
                    line += f"  {sym}{stats[stat]:<3}"
            print(line)

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

    # 最近一次失败
    print("\n── Recent failures ──")
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT kind, started_at, error_message
            FROM l2_run_log
            WHERE status = 'failed' AND started_at > now() - interval '7 days'
            ORDER BY started_at DESC LIMIT 3
        """)
        rows = cur.fetchall()

    if not rows:
        print("  (none)")
    else:
        for r in rows:
            ts = r['started_at'].astimezone(SG_TZ).strftime('%m-%d %H:%M')
            print(f"  {ts}  {r['kind']:<10}  {(r['error_message'] or '')[:80]}")

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
def librarian_run():
    """手动触发 Librarian 同步。"""
    from meta_ops.librarian.v0 import run_librarian_v0
    result = run_librarian_v0()
    print(json.dumps(result, indent=2, ensure_ascii=False))


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
