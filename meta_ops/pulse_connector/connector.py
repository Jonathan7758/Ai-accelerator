"""L2 访问 Pulse 数据的唯一入口。

设计原则(Phase 1 已锁定):
- 时间内部全 UTC,边界(查询参数 / 报表)做 SG 时区转换
- 返回 dataclass 不返回 Pulse 原 row
- 只读(L2 数据库账号 l2_reader 物理上禁写)
"""
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
import logging

import psycopg2
import psycopg2.extras

from meta_ops.pulse_connector.models import (
    PulseArticle, PulsePublish, PulseTopic, PulseInteraction, TableSchema
)
from meta_ops.common.db import get_pulse_db

log = logging.getLogger(__name__)

# Pulse 数据库时区(Asia/Shanghai)— 跟 SG 都是 +08:00,实际等价
PULSE_TZ = ZoneInfo("Asia/Shanghai")
SG_TZ = ZoneInfo("Asia/Singapore")


def _safe_int(value) -> Optional[int]:
    """从 jsonb 字符串安全转 int。失败返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


class PulseConnectionError(Exception):
    pass


class PulseConnector:
    """所有 L2 → Pulse 的访问都经过这里。

    对外暴露的方法返回 dataclass,绝不返回 Pulse 原始 row。
    内部捕获 psycopg2 错误,统一抛 PulseConnectionError。
    """

    def __init__(self, db_conn=None):
        self._db = db_conn  # 可注入 mock 用于测试

    @property
    def db(self):
        if self._db is None:
            self._db = get_pulse_db()
        return self._db

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    # ─────────────────────────────────────────────────
    # 数据查询接口
    # ─────────────────────────────────────────────────

    def get_articles_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulseArticle]:
        """拉某 SG 日历日(00:00-24:00)创建的文章。

        ★ SQL 只 SELECT 实际存在的列(详见 SCHEMA_NOTES.md §2.1)
        ★ jsonb 字段(versions/platform_versions/compliance_check)整体取出,
          在 Python 侧做路径抽取,而不是 SQL 侧 ->>。
          理由:psycopg2 自动把 jsonb 转成 dict,Python 侧解析比 SQL 路径取值更灵活。
        """
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, content, status, topic_id, tenant_id,
                       versions, platform_versions, compliance_check,
                       created_at, updated_at
                FROM articles
                WHERE created_at >= %s AND created_at < %s
                  AND tenant_id = %s
                ORDER BY created_at ASC
            """, (start_utc, end_utc, tenant_id))
            rows = cur.fetchall()

        return [self._row_to_article(r) for r in rows]

    def get_publishes_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulsePublish]:
        """拉某 SG 日历日创建的发布记录。

        ⚠️ 当前 publishes.metrics 全部 = {} (Pulse 数据回流未实现)
           Connector 仍正常拉,空 dict 保留(详见 SCHEMA_NOTES.md §3.4)
        """
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, article_id, platform, status, url, platform_article_id,
                       published_at, metrics, error_message, tenant_id, created_at
                FROM publishes
                WHERE created_at >= %s AND created_at < %s
                  AND tenant_id = %s
                ORDER BY created_at ASC
            """, (start_utc, end_utc, tenant_id))
            rows = cur.fetchall()

        return [self._row_to_publish(r) for r in rows]

    def get_topics_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulseTopic]:
        """拉某 SG 日历日创建的选题。

        ⚠️ topics 表没有 angle 列(angle 归属 article,详见 SCHEMA_NOTES.md §7.1)
        """
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, category, priority, status, tenant_id,
                       outline, created_at, updated_at
                FROM topics
                WHERE created_at >= %s AND created_at < %s
                  AND tenant_id = %s
                ORDER BY priority DESC NULLS LAST, created_at ASC
            """, (start_utc, end_utc, tenant_id))
            rows = cur.fetchall()

        return [self._row_to_topic(r) for r in rows]

    def get_interactions_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulseInteraction]:
        """拉某 SG 日历日创建的互动记录。

        ⚠️ Phase 1 不接入(详见 SCHEMA_NOTES.md §5.2)
        当前 interactions 表 0 行,且字段映射跟 SPEC 假设差距大,
        推迟到 Phase 2/3 启动前再处理。
        """
        raise NotImplementedError(
            "get_interactions_by_date is not implemented in Phase 1. "
            "See SCHEMA_NOTES.md §5 for details."
        )

    def get_table_schema(self, table_name: str) -> TableSchema:
        """给 Librarian 用,从 information_schema 拉表元信息。"""
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 列信息
            cur.execute("""
                SELECT column_name, data_type, is_nullable, column_default,
                       character_maximum_length, numeric_precision
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """, (table_name,))
            columns = [dict(r) for r in cur.fetchall()]

            if not columns:
                raise ValueError(f"Table not found: {table_name}")

            # 主键
            cur.execute("""
                SELECT a.attname AS column_name
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = %s::regclass AND i.indisprimary
            """, (f"public.{table_name}",))
            pks = [r['column_name'] for r in cur.fetchall()]

            # 索引
            cur.execute("""
                SELECT indexname AS name, indexdef AS definition
                FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = %s
                ORDER BY indexname
            """, (table_name,))
            indexes = [dict(r) for r in cur.fetchall()]

        return TableSchema(
            table_name=table_name,
            columns=columns,
            primary_keys=pks,
            indexes=indexes,
        )

    def get_all_user_tables(self) -> list[str]:
        """列出 public schema 下所有表名。"""
        with self.db.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            return [r[0] for r in cur.fetchall()]

    # ─────────────────────────────────────────────────
    # 内部工具
    # ─────────────────────────────────────────────────

    def _sg_day_to_utc_range(self, sg_date: date) -> tuple[datetime, datetime]:
        """把 SG 时区某天 00:00:00 到次日 00:00:00,转成 UTC 时间戳。

        Pulse 的 created_at 是 timestamptz,内部存 UTC,
        所以查询条件用 UTC 即可,无歧义。
        """
        sg_start = datetime.combine(sg_date, datetime.min.time(), tzinfo=SG_TZ)
        sg_end = sg_start + timedelta(days=1)
        return sg_start.astimezone(timezone.utc), sg_end.astimezone(timezone.utc)

    def _row_to_article(self, r: dict) -> PulseArticle:
        """从 row(含 jsonb dict)构造 PulseArticle。"""
        versions = r.get('versions') or {}
        compliance = r.get('compliance_check') or {}
        platform_versions = r.get('platform_versions') or {}

        return PulseArticle(
            id=str(r['id']),
            title=r['title'] or '',
            content_summary=(r.get('content') or '')[:200],
            status=r.get('status') or 'unknown',
            topic_id=str(r['topic_id']) if r.get('topic_id') else None,
            tenant_id=r.get('tenant_id') or 'history',

            word_count=_safe_int(versions.get('word_count')),
            model_used=versions.get('model_used'),
            language=versions.get('language'),

            angle=compliance.get('angle'),

            platform_versions=platform_versions,

            created_at=r['created_at'],
            updated_at=r['updated_at'],
        )

    def _row_to_publish(self, r: dict) -> PulsePublish:
        return PulsePublish(
            id=str(r['id']),
            article_id=str(r['article_id']),
            platform=r['platform'],
            status=r.get('status') or 'unknown',
            url=r.get('url'),
            platform_article_id=r.get('platform_article_id'),
            published_at=r.get('published_at'),
            metrics=r.get('metrics') or {},
            error_message=r.get('error_message'),
            tenant_id=r.get('tenant_id') or 'history',
            created_at=r['created_at'],
        )

    def _row_to_topic(self, r: dict) -> PulseTopic:
        return PulseTopic(
            id=str(r['id']),
            title=r['title'] or '',
            category=r.get('category'),
            priority=r.get('priority'),
            status=r.get('status') or 'unknown',
            tenant_id=r.get('tenant_id') or 'history',
            outline=r.get('outline') or {},
            created_at=r['created_at'],
            updated_at=r['updated_at'],
        )
