"""Librarian v0:同步 Pulse schema 到知识镜像。"""
from datetime import datetime, timezone
from pathlib import Path
import json
import logging

from meta_ops.pulse_connector.connector import PulseConnector
from meta_ops.pulse_connector.models import TableSchema
from meta_ops.common.db import get_local_db
from meta_ops.common.run_log import RunLogger
from meta_ops.common.logging_config import setup_logging

log = logging.getLogger(__name__)

KNOWLEDGE_ROOT = Path("/opt/accelerator/knowledge/pulse")

# Phase 1 镜像这 5 张表 — 跟 Watcher 拉的 4 张 + configs
TABLES_TO_MIRROR = ['articles', 'topics', 'publishes', 'interactions', 'configs']


def render_schema_markdown(schema: TableSchema, synced_at: datetime) -> str:
    """把 TableSchema 渲染成 markdown。纯函数,无 LLM。"""
    lines = [
        f"# Pulse Table: `{schema.table_name}`",
        "",
        f"**Last synced**: {synced_at.isoformat()}",
        f"**Primary key(s)**: {', '.join(schema.primary_keys) or '(none)'}",
        "",
        "## Columns",
        "",
        "| Column | Type | Nullable | Default |",
        "|---|---|---|---|",
    ]
    for col in schema.columns:
        default = col.get('column_default') or ''
        # 截断长 default 表达式
        if len(default) > 50:
            default = default[:47] + '...'
        lines.append(
            f"| `{col['column_name']}` | {col['data_type']} | "
            f"{col['is_nullable']} | {default} |"
        )

    lines.extend(["", "## Indexes", ""])
    if schema.indexes:
        for idx in schema.indexes:
            lines.append(f"- **{idx['name']}**: `{idx['definition']}`")
    else:
        lines.append("(none)")

    return "\n".join(lines) + "\n"


def sync_schemas() -> dict:
    """纯逻辑:拉 schema 元信息镜像到 knowledge/pulse/schema/。

    不带 RunLogger,可在 v1 编排时复用(避免 RunLogger 嵌套)。
    Returns: {'tables_synced', 'tables_changed', 'errors'} dict。
    """
    KNOWLEDGE_ROOT.mkdir(parents=True, exist_ok=True)
    (KNOWLEDGE_ROOT / "schema").mkdir(exist_ok=True)
    (KNOWLEDGE_ROOT / "_meta").mkdir(exist_ok=True)

    connector = PulseConnector()
    summary = {'tables_synced': 0, 'tables_changed': 0, 'errors': []}
    changes_detail = []
    synced_at = datetime.now(timezone.utc)

    try:
        for table_name in TABLES_TO_MIRROR:
            try:
                schema = connector.get_table_schema(table_name)
                new_md = render_schema_markdown(schema, synced_at)
                target = KNOWLEDGE_ROOT / "schema" / f"{table_name}.md"

                changed = True
                if target.exists():
                    old = target.read_text()
                    old_no_ts = '\n'.join(l for l in old.split('\n') if not l.startswith('**Last synced**'))
                    new_no_ts = '\n'.join(l for l in new_md.split('\n') if not l.startswith('**Last synced**'))
                    changed = old_no_ts.strip() != new_no_ts.strip()

                target.write_text(new_md)
                summary['tables_synced'] += 1
                if changed:
                    summary['tables_changed'] += 1
                    changes_detail.append({'table': table_name, 'changed': True})
                    log.info(f"Schema changed: {table_name}")
                else:
                    log.debug(f"Schema unchanged: {table_name}")
            except Exception as e:
                log.error(f"Failed to sync {table_name}: {e}")
                summary['errors'].append({'table': table_name, 'error': str(e)})

        (KNOWLEDGE_ROOT / "_meta" / "last_synced_at.txt").write_text(
            synced_at.isoformat()
        )

        log_entry = {
            'at': synced_at.isoformat(),
            'tables_synced': summary['tables_synced'],
            'tables_changed': summary['tables_changed'],
            'changes_detail': changes_detail,
            'errors': summary['errors'],
        }
        log_path = KNOWLEDGE_ROOT / "_meta" / "sync_log.jsonl"
        with log_path.open("a") as f:
            f.write(json.dumps(log_entry) + "\n")
    finally:
        connector.close()

    return summary


def run_librarian_v0() -> dict:
    """v0 入口:schema mirror only,带 RunLogger。向后兼容。"""
    setup_logging('librarian')
    local_db = get_local_db()
    try:
        with RunLogger(local_db, kind='librarian') as run:
            summary = sync_schemas()
            run.set_summary(summary)

            if summary['errors'] and summary['tables_synced'] > 0:
                run.mark_partial(f"[degraded] {len(summary['errors'])} tables failed")
            elif summary['errors']:
                raise RuntimeError(f"All tables failed: {summary['errors']}")
    finally:
        local_db.close()

    return summary


if __name__ == '__main__':
    result = run_librarian_v0()
    print(json.dumps(result, indent=2, ensure_ascii=False))
