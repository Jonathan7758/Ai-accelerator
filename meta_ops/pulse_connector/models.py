"""L2 内部的统一数据模型。跟 Pulse 表结构解耦。

★ 本文件的字段定义对齐 SCHEMA_NOTES.md(权威基线)。
★ 任何 Pulse schema 变更先改 SCHEMA_NOTES.md,再改这里。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PulseArticle:
    """对齐 articles 表 + 关键 jsonb 字段抽取。

    字段映射详见 SCHEMA_NOTES.md §2.3
    """
    id: str
    title: str
    content_summary: str            # articles.content[:200]
    status: str
    topic_id: Optional[str]
    tenant_id: str

    # 从 versions jsonb 抽取
    word_count: Optional[int]       # versions->>'word_count'
    model_used: Optional[str]       # versions->>'model_used'
    language: Optional[str]         # versions->>'language'

    # 从 compliance_check jsonb 抽取(angle 是 article 的属性,不是 topic 的)
    angle: Optional[str]            # compliance_check->>'angle'

    # platform_versions 原样保留,Watcher 按平台拆封面
    platform_versions: dict

    created_at: datetime
    updated_at: datetime


@dataclass
class PulsePublish:
    """对齐 publishes 表。

    字段映射详见 SCHEMA_NOTES.md §3.2

    ⚠️ metrics 字段当前所有行 = {} (Pulse 还没实现数据回流)
       Connector 正常拉,Watcher 正常写,Phase 2 数据回流 worker 填充
    """
    id: str
    article_id: str
    platform: str
    status: str
    url: Optional[str]
    platform_article_id: Optional[str]
    published_at: Optional[datetime]
    metrics: dict                   # 空 {} 也保留(已知空通道)
    error_message: Optional[str]
    tenant_id: str
    created_at: datetime


@dataclass
class PulseTopic:
    """对齐 topics 表。

    字段映射详见 SCHEMA_NOTES.md §4.2

    ⚠️ 没有 angle 字段(角度归属在 article)
    """
    id: str
    title: str
    category: Optional[str]
    priority: Optional[int]
    status: str
    tenant_id: str
    outline: dict                   # jsonb 原样保留
    created_at: datetime
    updated_at: datetime


@dataclass
class PulseInteraction:
    """对齐 interactions 表。

    ⚠️ Phase 1 不接入(详见 SCHEMA_NOTES.md §5)
    保留 dataclass 定义但 Connector 方法 raise NotImplementedError
    """
    id: str
    publish_id: str
    interaction_type: str
    content: Optional[str]
    reply_by: Optional[str]         # 回复者(可能是博主自己),不是原评论用户
    reply_content: Optional[str]
    replied_at: Optional[datetime]  # NULL 表示未回复
    user_profile: dict              # jsonb 原样保留
    tenant_id: str
    created_at: datetime


@dataclass
class TableSchema:
    """Librarian 用,描述一张表的元信息。"""
    table_name: str
    columns: list  # list of dict: {column_name, data_type, is_nullable, ...}
    primary_keys: list
    indexes: list
