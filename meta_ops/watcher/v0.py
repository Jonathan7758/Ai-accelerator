"""Watcher v0:每日采集 Pulse 数据,写入 ops_metrics。

★ 字段对齐 SCHEMA_NOTES.md (Phase 1 Step 3 schema 重审后的权威基线)。
  Spec 原版字段(last_synced_at / topic.angle / interactions sentiment/replied)
  已不存在,本文件做了 4 处校正,详见对应注释。
"""
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
import json
import logging

import psycopg2.extras

from meta_ops.pulse_connector.connector import PulseConnector
from meta_ops.pulse_connector.models import (
    PulseArticle, PulsePublish, PulseTopic, PulseInteraction
)
from meta_ops.common.db import get_local_db
from meta_ops.common.run_log import RunLogger
from meta_ops.common.logging_config import setup_logging

log = logging.getLogger(__name__)

SG_TZ = ZoneInfo("Asia/Singapore")
KNOWLEDGE_META = Path("/opt/accelerator/knowledge/pulse/_meta/last_synced_at.txt")

# Librarian 必须在过去 36 小时内同步过,否则 Watcher 警告
LIBRARIAN_FRESHNESS_HOURS = 36


def check_librarian_freshness() -> Optional[float]:
    """返回 Librarian 上次同步距今多少小时。None 表示从未同步过。"""
    if not KNOWLEDGE_META.exists():
        return None
    try:
        last = datetime.fromisoformat(KNOWLEDGE_META.read_text().strip())
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except Exception as e:
        log.warning(f"Failed to read librarian freshness: {e}")
        return None


def run_watcher_v0(target_date: Optional[date] = None) -> dict:
    """执行 Watcher v0 采集。target_date 默认昨日(SG 时区)。"""
    setup_logging('watcher')

    if target_date is None:
        target_date = (datetime.now(SG_TZ) - timedelta(days=1)).date()

    log.info(f"Watcher v0 running for SG date: {target_date}")

    summary = {
        'target_date': target_date.isoformat(),
        'articles_count': 0,
        'publishes_count': 0,
        'topics_count': 0,
        'interactions_count': 0,
        'metrics_inserted': 0,
        'librarian_freshness_hours': None,
        'partial_reasons': [],
    }

    local_db = get_local_db()
    connector = PulseConnector()

    try:
        with RunLogger(local_db, kind='watcher') as run:
            # ── 健康检查:Librarian freshness ─────────
            freshness = check_librarian_freshness()
            summary['librarian_freshness_hours'] = freshness
            if freshness is None:
                summary['partial_reasons'].append("[degraded] Librarian never synced")
                log.warning("Librarian has never synced. Watcher continues but degraded.")
            elif freshness > LIBRARIAN_FRESHNESS_HOURS:
                summary['partial_reasons'].append(
                    f"[degraded] Librarian stale: {freshness:.1f}h old"
                )
                log.warning(f"Librarian last synced {freshness:.1f}h ago (>{LIBRARIAN_FRESHNESS_HOURS}h)")

            # ── 拉数据(各表独立失败处理)─────────────
            articles = _safe_fetch(connector.get_articles_by_date, target_date,
                                   'articles', summary)
            publishes = _safe_fetch(connector.get_publishes_by_date, target_date,
                                    'publishes', summary)
            topics = _safe_fetch(connector.get_topics_by_date, target_date,
                                 'topics', summary)

            # ★ Phase 1 校正 (3/4):interactions 在 Connector 层 raise NotImplementedError。
            #   Watcher 不调用,直接给空列表 + partial_reason 标记,避免 _safe_fetch
            #   把"故意不实现"误报成"拉取失败"。详见 SCHEMA_NOTES.md §5。
            interactions = []
            summary['partial_reasons'].append(
                "[deferred] interactions table — Phase 2/3 per SCHEMA_NOTES.md §5"
            )

            summary['articles_count'] = len(articles)
            summary['publishes_count'] = len(publishes)
            summary['topics_count'] = len(topics)
            summary['interactions_count'] = len(interactions)

            # ── 写入 ops_metrics ──────────────────────
            inserted = _write_metrics(local_db, target_date, articles, publishes,
                                      topics, interactions)
            summary['metrics_inserted'] = inserted

            # ── 周日聚合 ──────────────────────────────
            if target_date.weekday() == 6:  # Sunday
                agg_id = _write_weekly_aggregate(local_db, target_date)
                summary['weekly_aggregate_id'] = agg_id
                log.info(f"Weekly aggregate written: {agg_id}")

            run.set_summary(summary)

            if summary['partial_reasons']:
                run.mark_partial("; ".join(summary['partial_reasons']))

            log.info(f"Watcher v0 done: {summary}")
    finally:
        connector.close()
        local_db.close()

    return summary


def _safe_fetch(fetch_fn, target_date, table_label, summary):
    """单表拉取的容错包装。失败时记到 summary['partial_reasons'] 而不是抛出。"""
    try:
        return fetch_fn(target_date)
    except Exception as e:
        log.error(f"Failed to fetch {table_label}: {e}")
        summary['partial_reasons'].append(f"[degraded] {table_label}: {str(e)[:100]}")
        return []


def _write_metrics(db, target_date: date,
                   articles: list[PulseArticle],
                   publishes: list[PulsePublish],
                   topics: list[PulseTopic],
                   interactions: list[PulseInteraction]) -> int:
    """把数据转成 ops_metrics 行写入。返回插入行数。"""
    inserted = 0
    snapshot_at = datetime.now(timezone.utc)

    # ── 索引:每篇文章在哪些平台发布了 ─────
    publishes_by_article = {}
    for p in publishes:
        publishes_by_article.setdefault(p.article_id, []).append(p)

    # ── 索引:每个 publish 收到的互动 ─────
    interactions_by_publish = {}
    for i in interactions:
        interactions_by_publish.setdefault(i.publish_id, []).append(i)

    with db.cursor() as cur:
        # 文章 × 平台 = 一条 ops_metrics
        for article in articles:
            article_pubs = publishes_by_article.get(article.id, [])

            if not article_pubs:
                # 文章存在但无发布记录 — 也写一条占位记录
                cur.execute("""
                    INSERT INTO ops_metrics
                      (tenant_id, subject_type, subject_id, snapshot_at, metrics, source)
                    VALUES (%s, 'article', %s, %s, %s, 'pulse_pg_via_tunnel')
                """, ('history', article.id, snapshot_at, psycopg2.extras.Json({
                    'article_title': article.title[:100],
                    'article_status': article.status,
                    'article_word_count': article.word_count,
                    'article_created_at': article.created_at.isoformat(),
                    'topic_id': article.topic_id,
                    'platform': None,
                    'published': False,
                })))
                inserted += 1
                continue

            for pub in article_pubs:
                pub_interactions = interactions_by_publish.get(pub.id, [])
                interaction_summary = _summarize_interactions(pub_interactions)

                # ★ Phase 1 校正 (1/4):pub.last_synced_at 字段已删除
                #   (PulsePublish 不再持有,详见 SCHEMA_NOTES.md §3.3)
                cur.execute("""
                    INSERT INTO ops_metrics
                      (tenant_id, subject_type, subject_id, snapshot_at, metrics, source)
                    VALUES (%s, 'article', %s, %s, %s, 'pulse_pg_via_tunnel')
                """, ('history', article.id, snapshot_at, psycopg2.extras.Json({
                    'article_title': article.title[:100],
                    'article_status': article.status,
                    'article_word_count': article.word_count,
                    'article_created_at': article.created_at.isoformat(),
                    'topic_id': article.topic_id,
                    'platform': pub.platform,
                    'publish_id': pub.id,
                    'publish_status': pub.status,
                    'publish_url': pub.url,
                    'published': True,
                    'published_at': pub.published_at.isoformat() if pub.published_at else None,
                    'platform_metrics': pub.metrics,  # ★ KPI 主源 jsonb 原样保留(Phase 1 全为空 {})
                    'interactions': interaction_summary,
                })))
                inserted += 1

        # 选题作为独立 subject(便于 Analyst 后续分析选题质量)
        # ★ Phase 1 校正 (2/4):topic.angle 字段已删除(归属在 article)
        #   详见 SCHEMA_NOTES.md §4.3 / §7.1
        for topic in topics:
            cur.execute("""
                INSERT INTO ops_metrics
                  (tenant_id, subject_type, subject_id, snapshot_at, metrics, source)
                VALUES (%s, 'topic', %s, %s, %s, 'pulse_pg_via_tunnel')
            """, ('history', topic.id, snapshot_at, psycopg2.extras.Json({
                'topic_title': topic.title[:100],
                'category': topic.category,
                'priority': topic.priority,
                'status': topic.status,
                'created_at': topic.created_at.isoformat(),
            })))
            inserted += 1

    db.commit()
    return inserted


def _summarize_interactions(interactions: list[PulseInteraction]) -> dict:
    """把多条互动汇总成一个 dict。

    ★ Phase 1 校正 (4/4):PulseInteraction 不再有 sentiment / replied 字段。
      replied 由 replied_at IS NOT NULL 推断;sentiment 在 Pulse 不存在,
      Phase 2/3 接情感分析时再补。详见 SCHEMA_NOTES.md §5.3。
    Phase 1 这个函数只在 interactions=[] 时被调用,实际进入循环体的概率为 0。
    """
    if not interactions:
        return {'count': 0}

    by_type = {}
    replied_count = 0

    for i in interactions:
        by_type[i.interaction_type] = by_type.get(i.interaction_type, 0) + 1
        if i.replied_at is not None:
            replied_count += 1

    return {
        'count': len(interactions),
        'by_type': by_type,
        'replied_count': replied_count,
    }


def _write_weekly_aggregate(db, sunday_date: date) -> str:
    """在周日跑出来时,聚合本周 7 天 ops_metrics 写一条 weekly_aggregate。"""
    week_start = sunday_date - timedelta(days=6)
    iso_week = sunday_date.strftime('%G-W%V')  # 如 '2026-W18'
    snapshot_at = datetime.now(timezone.utc)

    week_start_utc = datetime.combine(week_start, datetime.min.time(), tzinfo=SG_TZ).astimezone(timezone.utc)
    week_end_utc = datetime.combine(sunday_date + timedelta(days=1), datetime.min.time(), tzinfo=SG_TZ).astimezone(timezone.utc)

    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
              count(*) FILTER (WHERE subject_type='article') AS articles,
              count(*) FILTER (WHERE subject_type='topic') AS topics,
              count(DISTINCT subject_id) FILTER (WHERE subject_type='article') AS unique_articles,
              count(*) FILTER (WHERE subject_type='article'
                                AND (metrics->>'published')::text = 'true') AS published_count
            FROM ops_metrics
            WHERE tenant_id = 'history'
              AND snapshot_at >= %s AND snapshot_at < %s
        """, (week_start_utc, week_end_utc))
        row = dict(cur.fetchone() or {})

    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO ops_metrics
              (tenant_id, subject_type, subject_id, snapshot_at, metrics, source)
            VALUES ('history', 'weekly_aggregate', %s, %s, %s, 'computed')
            RETURNING id
        """, (iso_week, snapshot_at, psycopg2.extras.Json({
            'iso_week': iso_week,
            'week_start': week_start.isoformat(),
            'week_end': sunday_date.isoformat(),
            'articles_total': row.get('articles', 0),
            'unique_articles': row.get('unique_articles', 0),
            'topics_total': row.get('topics', 0),
            'published_count': row.get('published_count', 0),
        })))
        agg_id = cur.fetchone()[0]
    db.commit()
    return str(agg_id)


if __name__ == '__main__':
    import sys
    target_date = None
    if len(sys.argv) > 1:
        target_date = date.fromisoformat(sys.argv[1])
    result = run_watcher_v0(target_date)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
