"""Pulse Connector 单元测试。用 mock 不连真 DB。

Phase 1 测试范围(已与用户对齐,不要扩):
  - 时区换算正确(_sg_day_to_utc_range)
  - dataclass 转换正确(get_articles_by_date,含 jsonb 字段抽取)
  - 已知空通道处理(publishes.metrics={})
  - interactions Phase 1 故意拒接
"""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock
import pytest

from meta_ops.pulse_connector.connector import PulseConnector


@pytest.fixture
def mock_db():
    """伪造一个 db 连接对象。"""
    db = MagicMock()
    return db


@pytest.fixture
def connector(mock_db):
    return PulseConnector(db_conn=mock_db)


def test_sg_day_to_utc_range_basic(connector):
    """SG 日历日转 UTC 范围:SG 00:00 = UTC 前一天 16:00"""
    start, end = connector._sg_day_to_utc_range(date(2026, 5, 1))
    assert start == datetime(2026, 4, 30, 16, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 5, 1, 16, 0, 0, tzinfo=timezone.utc)


def test_get_articles_returns_dataclass(connector, mock_db):
    """确认返回的是 PulseArticle 列表,jsonb 字段被正确抽取。"""
    fake_row = {
        'id': 'abc-123',
        'title': 'Test Article',
        'content': 'A' * 500,
        'status': 'published',
        'topic_id': 'topic-1',
        'tenant_id': 'history',
        'versions': {
            'word_count': 1896,
            'model_used': 'claude-sonnet',
            'language': 'zh'
        },
        'platform_versions': {
            'cover_wechat': 'https://x.com/wx.jpg',
            'cover_xhs': 'https://x.com/xhs.jpg'
        },
        'compliance_check': {
            'angle': '从综合角度...'
        },
        'created_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
        'updated_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
    }
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [fake_row]
    mock_db.cursor.return_value.__enter__.return_value = mock_cur

    articles = connector.get_articles_by_date(date(2026, 5, 1))

    assert len(articles) == 1
    a = articles[0]
    assert a.id == 'abc-123'
    assert a.title == 'Test Article'
    assert len(a.content_summary) == 200
    assert a.word_count == 1896           # 从 versions 抽取
    assert a.model_used == 'claude-sonnet'
    assert a.language == 'zh'
    assert a.angle == '从综合角度...'      # 从 compliance_check 抽取
    assert a.platform_versions['cover_wechat'] == 'https://x.com/wx.jpg'
    assert a.tenant_id == 'history'


def test_get_publishes_handles_empty_metrics(connector, mock_db):
    """metrics 全空 dict 也要正确处理(已知空通道)。"""
    fake_row = {
        'id': 'pub-1',
        'article_id': 'art-1',
        'platform': 'wechat',
        'status': 'draft',
        'url': None,
        'platform_article_id': None,
        'published_at': None,
        'metrics': {},  # ★ 已知空通道
        'error_message': None,
        'tenant_id': 'history',
        'created_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
    }
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [fake_row]
    mock_db.cursor.return_value.__enter__.return_value = mock_cur

    pubs = connector.get_publishes_by_date(date(2026, 5, 1))

    assert pubs[0].metrics == {}
    assert pubs[0].platform == 'wechat'
    assert pubs[0].tenant_id == 'history'


def test_get_interactions_raises_not_implemented(connector):
    """Phase 1 故意不实现 interactions,调用应明确拒绝。"""
    with pytest.raises(NotImplementedError):
        connector.get_interactions_by_date(date(2026, 5, 1))
