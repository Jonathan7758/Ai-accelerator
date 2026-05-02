# Accelerator L2 — Phase 1 开发方案

> 给 Claude Code 的开发任务说明书
> Phase 1 目标:数据观察层 — 让 Pulse 数据自动流入 L2 + 建立 schema 知识镜像
> 预计工时:13-15 小时,建议分散到 3-5 天完成
> 完成后产出:每天 23:00 自动归档昨日运营数据,你能用 SQL 查任意一天历史号表现

---

## 0. 必读前置

### 0.1 在动笔前先读这些

```
/opt/accelerator/CLAUDE.md             — 项目恒定知识(每次会话已自动读)
/opt/accelerator/docs/PROJECT_BLUEPRINT.md  — 项目宪法,重点读 §6 (Phase 1)
/opt/accelerator/docs/INFRASTRUCTURE.md     — 基础设施事实,重点读 §1-§4
```

### 0.2 Phase 0 已就绪的状态

- 服务器 accelerator-jb:Ubuntu 24.04 / Python 3.12.3 / Asia/Singapore
- 本机 Postgres 16:数据库 `accelerator`,用户 `acc_app`,3 张业务表 + schema_versions 已建
- SSH 隧道:`acc-pgtunnel.service` 把 pulse-hk 的 PG 转发到本机 5433
- 健康检查:`acc-healthcheck.timer` 每小时跑一次
- `.env` 已配置 ACC_DB_* 和 HK_DB_*

### 0.3 Phase 1 的总体设计

```
┌──────────────────────────────────────────────────────────────┐
│  Phase 1 五个模块(及其触发关系)                                │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│   每日 06:00  Librarian v0                                     │
│     └─ 用 Connector 拉 schema 元信息                           │
│     └─ 渲染成 markdown,写 knowledge/pulse/schema/             │
│     └─ 写一条 l2_run_log                                       │
│                                                                │
│   每日 23:00  Watcher v0                                       │
│     └─ 健康检查:Librarian 是否新鲜                            │
│     └─ 用 Connector 拉昨日 articles + publishes + topics +    │
│        interactions                                            │
│     └─ 转换 → INSERT ops_metrics                              │
│     └─ 周日额外算 weekly_aggregate                             │
│     └─ 写一条 l2_run_log                                       │
│                                                                │
│   随时   你跑 acc status / acc backfill / acc XXX run         │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

### 0.4 关键技术约束(已被 Phase 0 现实定下)

| 约束 | 来源 |
|---|---|
| Pulse 用 PG 13.23 — SQL 不用 PG 14+ 特性(MERGE/JSON_TABLE 等) | INFRASTRUCTURE §1 |
| L2 与 Pulse 之间必须经 SSH 隧道(127.0.0.1:5433) | INFRASTRUCTURE §1 |
| L2 对 Pulse 永远只读,不可写 | CLAUDE.md §11 |
| 时间内部用 UTC,边界做时区转换(zoneinfo) | BLUEPRINT §5.6.5 |
| 多 tenant schema 字段保留(default 'history'),Phase 1 不实际多 tenant | BLUEPRINT §10.5 |

### 0.5 决定性偏好(已与用户对齐,不要改)

- Watcher 拉 4 张表:`articles` / `publishes` / `topics` / `interactions`
- "昨日" = SG 时区昨日 00:00:00 至今日 00:00:00
- ops_metrics 颗粒度 = 一篇文章 × 一个平台 = 一条记录
- Connector 错误处理 = 部分成功允许,run_log 记 status='partial'
- CLI 命令叫 `acc`
- 测试:只写 Pulse Connector 的单元测试,Watcher/Librarian 不强求

---

## 1. Phase 1 工作分解

按依赖顺序,Phase 1 拆 8 个 Step。**严格按顺序**,前一个验收通过才进下一个。

```
Step 1: 项目骨架 + Python 环境 + 依赖
Step 2: l2_run_log 表 + 共享日志工具
Step 3: Pulse Connector(含单元测试)
Step 4: Librarian v0
Step 5: Watcher v0
Step 6: CLI 工具
Step 7: systemd timer 部署 + 端到端验证
Step 8: health_check 扩展 + Phase 1 验收
```

每 Step 含**目标 / 实现 / 验证 / 失败处置**四节。

---

## 2. Step 1: 项目骨架

### 2.1 目标

建立 Phase 1 的代码组织结构和 Python 运行环境。

### 2.2 实现

**1. 创建目录结构**

```bash
sudo -u accelerator bash -c '
mkdir -p /opt/accelerator/meta_ops/{pulse_connector,librarian,watcher,cli,common}
mkdir -p /opt/accelerator/meta_ops/pulse_connector/tests
mkdir -p /opt/accelerator/knowledge/pulse/{schema,_meta}
mkdir -p /opt/accelerator/sql
'

# 创建 Python 包标识
sudo -u accelerator bash -c '
cd /opt/accelerator/meta_ops
touch __init__.py
touch pulse_connector/__init__.py pulse_connector/tests/__init__.py
touch librarian/__init__.py
touch watcher/__init__.py
touch cli/__init__.py
touch common/__init__.py
'
```

**2. 创建 Python venv**

```bash
sudo -u accelerator python3 -m venv /opt/accelerator/.venv
sudo -u accelerator /opt/accelerator/.venv/bin/pip install --upgrade pip
```

**3. 写 `requirements.txt`**

```bash
sudo -u accelerator bash -c 'cat > /opt/accelerator/requirements.txt <<EOF
# Database
psycopg2-binary==2.9.9

# Configuration
python-dotenv==1.0.1

# CLI
click==8.1.7

# Testing
pytest==8.0.0
pytest-mock==3.12.0

# Utilities
python-dateutil==2.8.2
EOF'

sudo -u accelerator /opt/accelerator/.venv/bin/pip install -r /opt/accelerator/requirements.txt
```

**4. 写 `pyproject.toml`(用于 entry point 注册 acc 命令)**

```bash
sudo -u accelerator bash -c 'cat > /opt/accelerator/pyproject.toml <<EOF
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "accelerator"
version = "0.1.0"
description = "Accelerator L2 - Meta-Ops Layer"
requires-python = ">=3.11"

[project.scripts]
acc = "meta_ops.cli.main:cli"

[tool.setuptools.packages.find]
where = ["."]
include = ["meta_ops*"]

[tool.pytest.ini_options]
testpaths = ["meta_ops"]
python_files = ["test_*.py"]
EOF'

# 装成可执行
sudo -u accelerator /opt/accelerator/.venv/bin/pip install -e /opt/accelerator/
```

**5. 写 `.gitignore`**(项目根)

```bash
sudo -u accelerator bash -c 'cat > /opt/accelerator/.gitignore <<EOF
# Secrets — NEVER commit
.env
*.key
*.pem

# Python
__pycache__/
*.pyc
.venv/
*.egg-info/

# Logs / Runtime
/var/log/accelerator/
*.log

# Editor
.vscode/
.idea/
EOF'
```

### 2.3 验证

```bash
# venv 起作用
sudo -u accelerator /opt/accelerator/.venv/bin/python -c "
import psycopg2, click, dotenv
print('All deps imported OK')
"

# acc 命令注册了(虽然现在还没有 cli/main.py,但 entry point 已注册)
sudo -u accelerator /opt/accelerator/.venv/bin/pip show accelerator | grep -E "Name|Version"

# 目录结构正确
tree -L 3 /opt/accelerator/meta_ops/  # 或者 ls -R 如果 tree 不在
```

期望:依赖全部 import 成功,`accelerator` 包已安装。

### 2.4 失败处置

- pip 装包失败:多半是网络问题,重试 2 次,仍失败报告
- venv 创建失败:可能 `python3-venv` 没装,先 `apt install python3-venv` 再重试

---

## 3. Step 2: l2_run_log 表 + 共享日志工具

### 3.1 目标

建立 L2 自身运行历史的存储和工具,让 Librarian/Watcher 启动结束都留痕。

### 3.2 实现

**1. 写 SQL migration `sql/002_run_log.sql`**

```sql
-- /opt/accelerator/sql/002_run_log.sql
-- L2 自身运行历史

CREATE TABLE l2_run_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind TEXT NOT NULL,          -- 'librarian' | 'watcher' | 'analyst' | ...
    status TEXT NOT NULL          -- 'running' | 'ok' | 'failed' | 'partial'
        CHECK (status IN ('running','ok','failed','partial')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds REAL,
    summary JSONB DEFAULT '{}'::jsonb,
    error_message TEXT
);

CREATE INDEX idx_l2_run_log_kind_started ON l2_run_log(kind, started_at DESC);
CREATE INDEX idx_l2_run_log_status_started ON l2_run_log(status, started_at DESC) 
    WHERE status IN ('failed','partial');

INSERT INTO schema_versions (version, description) 
VALUES ('002', 'Add l2_run_log table');
```

执行:

```bash
PGPASSWORD=$(grep ACC_DB_PASSWORD /opt/accelerator/.env | cut -d= -f2) \
  psql -h 127.0.0.1 -U acc_app -d accelerator \
  -f /opt/accelerator/sql/002_run_log.sql
```

**2. 写 `meta_ops/common/run_log.py`**

```python
"""L2 自身运行历史的统一接入点。所有数字员工启动结束都用它留痕。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
import json
import uuid

import psycopg2
import psycopg2.extras


class RunLogger:
    """每个角色每次运行,包一层 with 块即可自动记录。
    
    Usage:
        with RunLogger(db_conn, kind='watcher') as run:
            # do stuff
            run.set_summary({'rows_inserted': 42})
            # 自动写 status='ok'/'failed'
    """
    
    def __init__(self, db_conn, kind: str):
        self.db = db_conn
        self.kind = kind
        self.run_id: Optional[str] = None
        self.summary: dict = {}
        self.partial: bool = False
        self.partial_reason: Optional[str] = None
    
    def __enter__(self):
        self.run_id = str(uuid.uuid4())
        with self.db.cursor() as cur:
            cur.execute("""
                INSERT INTO l2_run_log (id, kind, status, started_at)
                VALUES (%s, %s, 'running', %s)
            """, (self.run_id, self.kind, datetime.now(timezone.utc)))
        self.db.commit()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        ended_at = datetime.now(timezone.utc)
        
        if exc_type is not None:
            # 异常退出
            status = 'failed'
            err_msg = f"{exc_type.__name__}: {exc_val}"[:1000]
        elif self.partial:
            status = 'partial'
            err_msg = self.partial_reason
        else:
            status = 'ok'
            err_msg = None
        
        with self.db.cursor() as cur:
            cur.execute("""
                UPDATE l2_run_log
                SET status=%s, ended_at=%s, 
                    duration_seconds=EXTRACT(EPOCH FROM (%s - started_at)),
                    summary=%s, error_message=%s
                WHERE id=%s
            """, (status, ended_at, ended_at, 
                  json.dumps(self.summary), err_msg, self.run_id))
        self.db.commit()
        
        # 不吞异常,让上层看到
        return False
    
    def set_summary(self, data: dict):
        self.summary.update(data)
    
    def mark_partial(self, reason: str):
        """标记本次运行是部分成功(不算 failed,但有问题需关注)"""
        self.partial = True
        self.partial_reason = reason
```

**3. 写 `meta_ops/common/db.py`(L2 本机 DB 连接 + Pulse 隧道连接的工厂)**

```python
"""统一的 DB 连接工厂。"""
import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

ENV_PATH = Path("/opt/accelerator/.env")
load_dotenv(ENV_PATH)


def get_local_db():
    """连接 accelerator 本机 DB。"""
    return psycopg2.connect(
        host=os.environ['ACC_DB_HOST'],
        port=int(os.environ['ACC_DB_PORT']),
        dbname=os.environ['ACC_DB_NAME'],
        user=os.environ['ACC_DB_USER'],
        password=os.environ['ACC_DB_PASSWORD'],
    )


def get_pulse_db():
    """通过 SSH 隧道连接 Pulse 的 PG。隧道由 acc-pgtunnel.service 维护。"""
    return psycopg2.connect(
        host=os.environ['HK_DB_HOST'],   # 127.0.0.1
        port=int(os.environ['HK_DB_PORT']),  # 5433
        dbname=os.environ['HK_DB_NAME'],
        user=os.environ['HK_DB_USER'],
        password=os.environ['HK_DB_PASSWORD'],
        connect_timeout=10,
    )
```

**4. 写 `meta_ops/common/logging_config.py`**

```python
"""统一的 Python logging 配置。日志同时进 journal(stderr)和文件。"""
import logging
import sys
from pathlib import Path

LOG_DIR = Path("/var/log/accelerator")


def setup_logging(component: str, level=logging.INFO):
    """每个 entry point 启动时调一次。component 例如 'watcher'/'librarian'。"""
    LOG_DIR.mkdir(exist_ok=True)
    
    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    
    # 文件
    file_handler = logging.FileHandler(LOG_DIR / f"{component}.log")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    
    # stderr(systemd journal 会捕获)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)
    
    # 静默第三方库的 INFO 噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)
```

### 3.3 验证

```bash
# 1. 表创建成功
PGPASSWORD=... psql ... -c "\d l2_run_log"

# 2. RunLogger 能用(写一个小测试脚本)
sudo -u accelerator /opt/accelerator/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/accelerator')
from meta_ops.common.db import get_local_db
from meta_ops.common.run_log import RunLogger

db = get_local_db()
with RunLogger(db, kind='test') as run:
    run.set_summary({'test_key': 'test_value'})
print('RunLogger OK')
"

# 3. 看 l2_run_log 里有了 test 记录
PGPASSWORD=... psql ... -c "SELECT id, kind, status, summary FROM l2_run_log ORDER BY started_at DESC LIMIT 1;"

# 4. 清理测试数据
PGPASSWORD=... psql ... -c "DELETE FROM l2_run_log WHERE kind='test';"
```

### 3.4 失败处置

- 表创建失败:看是不是 schema_versions 那个 INSERT 主键冲突(已经有 v002),不是真正的失败,跳过 INSERT 但保留表创建
- 测试脚本 import 失败:八成是 PYTHONPATH 没设对,确认 `/opt/accelerator/` 在路径里(venv 装 -e 后应该自动有)

---

## 4. Step 3: Pulse Connector

### 4.1 目标

实现 L2 访问 Pulse 数据的统一抽象层(反腐败层)。**所有读 Pulse 的代码都通过它**。

### 4.2 实现

**1. 写 `meta_ops/pulse_connector/models.py`**

```python
"""L2 内部的统一数据模型。跟 Pulse 表结构解耦。"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PulseArticle:
    id: str
    title: str
    content_summary: str  # 截前 200 字
    topic_id: Optional[str]
    cover_url: Optional[str]
    word_count: Optional[int]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass
class PulsePublish:
    id: str
    article_id: str
    platform: str           # wechat / toutiao / xhs / baijiahao / ...
    platform_article_id: Optional[str]
    url: Optional[str]
    status: str             # draft / published / failed / ...
    metrics: dict           # jsonb 原样保留(空 {} 也接受)
    error_message: Optional[str]
    published_at: Optional[datetime]
    last_synced_at: Optional[datetime]
    created_at: datetime


@dataclass
class PulseTopic:
    id: str
    title: str
    angle: Optional[str]
    category: Optional[str]
    priority: Optional[int]
    status: str
    created_at: datetime


@dataclass
class PulseInteraction:
    id: str
    publish_id: str
    platform: str
    interaction_type: str
    user_name: Optional[str]
    content: Optional[str]
    sentiment: Optional[str]
    replied: bool
    created_at: datetime


@dataclass
class TableSchema:
    """Librarian 用,描述一张表的元信息。"""
    table_name: str
    columns: list  # list of dict: {name, data_type, is_nullable, default, ...}
    primary_keys: list  # list of column names
    indexes: list  # list of dict: {name, definition}
```

**2. 写 `meta_ops/pulse_connector/connector.py`**

```python
"""L2 访问 Pulse 数据的唯一入口。"""
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
        """拉某 SG 日历日(00:00-24:00)创建的文章。"""
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, content, topic_id, status,
                       word_count, platform_versions, created_at, updated_at
                FROM articles
                WHERE created_at >= %s AND created_at < %s
                ORDER BY created_at ASC
            """, (start_utc, end_utc))
            rows = cur.fetchall()
        
        return [self._row_to_article(r) for r in rows]
    
    def get_publishes_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulsePublish]:
        """拉某 SG 日历日创建的发布记录。"""
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, article_id, platform, platform_article_id, url, status,
                       metrics, error_message, published_at, last_synced_at, created_at
                FROM publishes
                WHERE created_at >= %s AND created_at < %s
                ORDER BY created_at ASC
            """, (start_utc, end_utc))
            rows = cur.fetchall()
        
        return [self._row_to_publish(r) for r in rows]
    
    def get_topics_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulseTopic]:
        """拉某 SG 日历日创建的选题。"""
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, angle, category, priority, status, created_at
                FROM topics
                WHERE created_at >= %s AND created_at < %s
                ORDER BY priority DESC NULLS LAST, created_at ASC
            """, (start_utc, end_utc))
            rows = cur.fetchall()
        
        return [self._row_to_topic(r) for r in rows]
    
    def get_interactions_by_date(self, target_date: date, tenant_id: str = 'history') -> list[PulseInteraction]:
        """拉某 SG 日历日创建的互动记录。"""
        start_utc, end_utc = self._sg_day_to_utc_range(target_date)
        with self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, publish_id, platform, interaction_type,
                       user_name, content, sentiment, replied, created_at
                FROM interactions
                WHERE created_at >= %s AND created_at < %s
                ORDER BY created_at ASC
            """, (start_utc, end_utc))
            rows = cur.fetchall()
        
        return [self._row_to_interaction(r) for r in rows]
    
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
        return PulseArticle(
            id=str(r['id']),
            title=r['title'] or '',
            content_summary=(r.get('content') or '')[:200],
            topic_id=str(r['topic_id']) if r.get('topic_id') else None,
            cover_url=self._extract_cover_url(r.get('platform_versions')),
            word_count=r.get('word_count'),
            status=r.get('status') or 'unknown',
            created_at=r['created_at'],
            updated_at=r['updated_at'],
        )
    
    def _row_to_publish(self, r: dict) -> PulsePublish:
        return PulsePublish(
            id=str(r['id']),
            article_id=str(r['article_id']),
            platform=r['platform'],
            platform_article_id=r.get('platform_article_id'),
            url=r.get('url'),
            status=r.get('status') or 'unknown',
            metrics=r.get('metrics') or {},
            error_message=r.get('error_message'),
            published_at=r.get('published_at'),
            last_synced_at=r.get('last_synced_at'),
            created_at=r['created_at'],
        )
    
    def _row_to_topic(self, r: dict) -> PulseTopic:
        return PulseTopic(
            id=str(r['id']),
            title=r['title'] or '',
            angle=r.get('angle'),
            category=r.get('category'),
            priority=r.get('priority'),
            status=r.get('status') or 'unknown',
            created_at=r['created_at'],
        )
    
    def _row_to_interaction(self, r: dict) -> PulseInteraction:
        return PulseInteraction(
            id=str(r['id']),
            publish_id=str(r['publish_id']),
            platform=r['platform'],
            interaction_type=r['interaction_type'],
            user_name=r.get('user_name'),
            content=r.get('content'),
            sentiment=r.get('sentiment'),
            replied=bool(r.get('replied')),
            created_at=r['created_at'],
        )
    
    def _extract_cover_url(self, platform_versions: Optional[dict]) -> Optional[str]:
        """从 platform_versions jsonb 里抓封面 URL(如果存在)。"""
        if not platform_versions:
            return None
        # platform_versions 结构未知 — 尝试常见 key
        for key in ('cover_url', 'cover', 'image_url'):
            if key in platform_versions:
                return platform_versions[key]
        return None


class PulseConnectionError(Exception):
    pass
```

> ⚠️ **关于 `_extract_cover_url`**:Pulse 的 `articles.platform_versions` jsonb 实际结构,Phase 1 spec 里我没明确读到。如果跑起来发现封面 URL 没拉到,**先继续不要修**——记到 Phase 1 完成报告里,Phase 2 处理。Phase 1 不让"封面 URL 这种小细节"阻塞主流程。

**3. 写单元测试 `meta_ops/pulse_connector/tests/test_connector.py`**

```python
"""Pulse Connector 单元测试。用 mock 不连真 DB。"""
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
    """确认返回的是 PulseArticle 列表,不是原 row。"""
    fake_row = {
        'id': 'abc-123',
        'title': 'Test Article',
        'content': 'A' * 500,
        'topic_id': 'topic-1',
        'status': 'published',
        'word_count': 500,
        'platform_versions': {'cover_url': 'https://x.com/c.jpg'},
        'created_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
        'updated_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
    }
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [fake_row]
    mock_db.cursor.return_value.__enter__.return_value = mock_cur
    
    articles = connector.get_articles_by_date(date(2026, 5, 1))
    
    assert len(articles) == 1
    assert articles[0].id == 'abc-123'
    assert articles[0].title == 'Test Article'
    assert len(articles[0].content_summary) == 200  # 截断
    assert articles[0].cover_url == 'https://x.com/c.jpg'


def test_get_publishes_handles_empty_metrics(connector, mock_db):
    """metrics 为 None 时,转换成空 dict。"""
    fake_row = {
        'id': 'pub-1',
        'article_id': 'art-1',
        'platform': 'wechat',
        'platform_article_id': None,
        'url': None,
        'status': 'draft',
        'metrics': None,  # ★ 关键测试点
        'error_message': None,
        'published_at': None,
        'last_synced_at': None,
        'created_at': datetime(2026, 5, 1, tzinfo=timezone.utc),
    }
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [fake_row]
    mock_db.cursor.return_value.__enter__.return_value = mock_cur
    
    pubs = connector.get_publishes_by_date(date(2026, 5, 1))
    
    assert pubs[0].metrics == {}


def test_extract_cover_url_handles_various_keys(connector):
    """封面 URL 的 key 可能是 cover_url/cover/image_url"""
    assert connector._extract_cover_url({'cover_url': 'a'}) == 'a'
    assert connector._extract_cover_url({'cover': 'b'}) == 'b'
    assert connector._extract_cover_url({'image_url': 'c'}) == 'c'
    assert connector._extract_cover_url({'unknown_key': 'd'}) is None
    assert connector._extract_cover_url(None) is None
    assert connector._extract_cover_url({}) is None
```

### 4.3 验证

```bash
# 1. 单元测试通过
cd /opt/accelerator
sudo -u accelerator /opt/accelerator/.venv/bin/pytest meta_ops/pulse_connector/tests/ -v

# 2. 真实连接测试(实打实连 Pulse)
sudo -u accelerator /opt/accelerator/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/accelerator')
from datetime import date, timedelta
from meta_ops.pulse_connector.connector import PulseConnector

c = PulseConnector()

# 列所有表
tables = c.get_all_user_tables()
print(f'Pulse 表清单: {tables}')

# 拉某天数据
yesterday = date.today() - timedelta(days=1)
articles = c.get_articles_by_date(yesterday)
print(f'昨日 articles: {len(articles)} 条')
for a in articles[:3]:
    print(f'  - {a.id[:8]} | {a.title[:40]}')

# 拉 schema
schema = c.get_table_schema('articles')
print(f'articles 表有 {len(schema.columns)} 列, {len(schema.indexes)} 个索引')

c.close()
print('Connector OK')
"
```

期望:
- 单元测试 4 个全过
- 真实连接列出 9 张表
- 昨日数据拉到了(可能 0 条 — 视 Pulse 当天活跃度)
- articles schema 拉到了

### 4.4 失败处置

- `psycopg2.OperationalError: timeout`:隧道死了,先 `systemctl restart acc-pgtunnel`
- `permission denied for table xxx`:l2_reader 没 SELECT 权限,在 pulse-hk 上 GRANT 一下
- 单元测试某条 fail:多半是我的 mock 写法跟你环境略有差异,**先告诉我具体哪条 fail 在哪,不要绕过**

---

## 5. Step 4: Librarian v0

### 5.1 目标

每天 06:00 把 Pulse 5 张关心的表的 schema 元信息镜像到 `knowledge/pulse/schema/`。Phase 1 只做 schema,不做 docs/code_index。

### 5.2 实现

**1. 写 `meta_ops/librarian/v0.py`**

```python
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


def run_librarian_v0() -> dict:
    """执行 Librarian v0 同步。返回结果摘要。"""
    setup_logging('librarian')
    
    KNOWLEDGE_ROOT.mkdir(parents=True, exist_ok=True)
    (KNOWLEDGE_ROOT / "schema").mkdir(exist_ok=True)
    (KNOWLEDGE_ROOT / "_meta").mkdir(exist_ok=True)
    
    local_db = get_local_db()
    connector = PulseConnector()
    
    summary = {'tables_synced': 0, 'tables_changed': 0, 'errors': []}
    changes_detail = []
    synced_at = datetime.now(timezone.utc)
    
    try:
        with RunLogger(local_db, kind='librarian') as run:
            for table_name in TABLES_TO_MIRROR:
                try:
                    schema = connector.get_table_schema(table_name)
                    new_md = render_schema_markdown(schema, synced_at)
                    target = KNOWLEDGE_ROOT / "schema" / f"{table_name}.md"
                    
                    changed = True
                    if target.exists():
                        # 比内容时排除 "Last synced" 那一行
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
            
            # 写 last_synced_at
            (KNOWLEDGE_ROOT / "_meta" / "last_synced_at.txt").write_text(
                synced_at.isoformat()
            )
            
            # 写 sync_log.jsonl 一条
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
            
            run.set_summary(summary)
            
            # 部分失败:有 error 但有些表成功了
            if summary['errors'] and summary['tables_synced'] > 0:
                run.mark_partial(f"{len(summary['errors'])} tables failed")
            elif summary['errors']:
                # 全失败:直接抛,RunLogger __exit__ 会标 failed
                raise RuntimeError(f"All tables failed: {summary['errors']}")
    finally:
        connector.close()
        local_db.close()
    
    return summary


if __name__ == '__main__':
    result = run_librarian_v0()
    print(json.dumps(result, indent=2, ensure_ascii=False))
```

### 5.3 验证

```bash
# 1. 手动跑一次
sudo -u accelerator /opt/accelerator/.venv/bin/python -m meta_ops.librarian.v0

# 期望输出:
# {
#   "tables_synced": 5,
#   "tables_changed": 5,  # 第一次跑都是新增
#   "errors": []
# }

# 2. 文件产出
ls -la /opt/accelerator/knowledge/pulse/schema/
# 期望:articles.md / topics.md / publishes.md / interactions.md / configs.md

# 3. 看其中一个文件
cat /opt/accelerator/knowledge/pulse/schema/articles.md | head -30

# 4. _meta 文件
cat /opt/accelerator/knowledge/pulse/_meta/last_synced_at.txt
cat /opt/accelerator/knowledge/pulse/_meta/sync_log.jsonl

# 5. l2_run_log 留痕
PGPASSWORD=... psql -h 127.0.0.1 -U acc_app -d accelerator \
  -c "SELECT id, kind, status, duration_seconds, summary 
      FROM l2_run_log WHERE kind='librarian' ORDER BY started_at DESC LIMIT 1;"

# 6. 第二次跑(测试 idempotent)
sudo -u accelerator /opt/accelerator/.venv/bin/python -m meta_ops.librarian.v0
# 期望:tables_changed: 0(没变化)
```

### 5.4 失败处置

- 5 张表里某张拉不到(Pulse 表名跟我假设不一致):看错误信息哪张,先注释掉那张继续,记到完成报告
- markdown 写不进:权限问题,看 `/opt/accelerator/knowledge/` owner 对不对

---

## 6. Step 5: Watcher v0

### 6.1 目标

每天 23:00 把昨日的 articles + publishes + topics + interactions 拉来,转换成 ops_metrics 行写入。

### 6.2 实现

**写 `meta_ops/watcher/v0.py`**

```python
"""Watcher v0:每日采集 Pulse 数据,写入 ops_metrics。"""
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
                summary['partial_reasons'].append("Librarian never synced")
                log.warning("Librarian has never synced. Watcher continues but degraded.")
            elif freshness > LIBRARIAN_FRESHNESS_HOURS:
                summary['partial_reasons'].append(
                    f"Librarian stale: {freshness:.1f}h old"
                )
                log.warning(f"Librarian last synced {freshness:.1f}h ago (>{LIBRARIAN_FRESHNESS_HOURS}h)")
            
            # ── 拉数据(各表独立失败处理)─────────────
            articles = _safe_fetch(connector.get_articles_by_date, target_date, 
                                   'articles', summary)
            publishes = _safe_fetch(connector.get_publishes_by_date, target_date,
                                    'publishes', summary)
            topics = _safe_fetch(connector.get_topics_by_date, target_date,
                                 'topics', summary)
            interactions = _safe_fetch(connector.get_interactions_by_date, target_date,
                                       'interactions', summary)
            
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
        summary['partial_reasons'].append(f"{table_label}: {str(e)[:100]}")
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
                    'published_at': pub.published_at.isoformat() if pub.published_at else None,
                    'last_synced_at': pub.last_synced_at.isoformat() if pub.last_synced_at else None,
                    'platform_metrics': pub.metrics,  # ★ KPI 主源 jsonb 原样保留
                    'interactions': interaction_summary,
                })))
                inserted += 1
        
        # 选题作为独立 subject(便于 Analyst 后续分析选题质量)
        for topic in topics:
            cur.execute("""
                INSERT INTO ops_metrics
                  (tenant_id, subject_type, subject_id, snapshot_at, metrics, source)
                VALUES (%s, 'topic', %s, %s, %s, 'pulse_pg_via_tunnel')
            """, ('history', topic.id, snapshot_at, psycopg2.extras.Json({
                'topic_title': topic.title[:100],
                'category': topic.category,
                'angle': topic.angle,
                'priority': topic.priority,
                'status': topic.status,
                'created_at': topic.created_at.isoformat(),
            })))
            inserted += 1
    
    db.commit()
    return inserted


def _summarize_interactions(interactions: list[PulseInteraction]) -> dict:
    """把多条互动汇总成一个 dict。"""
    if not interactions:
        return {'count': 0}
    
    by_type = {}
    by_sentiment = {}
    replied_count = 0
    
    for i in interactions:
        by_type[i.interaction_type] = by_type.get(i.interaction_type, 0) + 1
        if i.sentiment:
            by_sentiment[i.sentiment] = by_sentiment.get(i.sentiment, 0) + 1
        if i.replied:
            replied_count += 1
    
    return {
        'count': len(interactions),
        'by_type': by_type,
        'by_sentiment': by_sentiment,
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
```

### 6.3 验证

```bash
# 1. 手动跑昨日(SG 时区)
sudo -u accelerator /opt/accelerator/.venv/bin/python -m meta_ops.watcher.v0

# 期望输出 JSON 含:
#   articles_count / publishes_count / metrics_inserted / partial_reasons:[]

# 2. 跑指定日期(测试 backfill 路径)
sudo -u accelerator /opt/accelerator/.venv/bin/python -m meta_ops.watcher.v0 2026-04-28

# 3. 看 ops_metrics 写入了
PGPASSWORD=... psql -h 127.0.0.1 -U acc_app -d accelerator <<EOF
SELECT subject_type, count(*) FROM ops_metrics GROUP BY subject_type;
SELECT subject_type, subject_id, metrics->>'platform' AS platform, 
       metrics->>'article_title' AS title
FROM ops_metrics 
WHERE subject_type='article'
ORDER BY snapshot_at DESC LIMIT 5;
EOF

# 4. l2_run_log 留痕
PGPASSWORD=... psql ... -c "
  SELECT kind, status, duration_seconds, summary 
  FROM l2_run_log WHERE kind='watcher' ORDER BY started_at DESC LIMIT 1;"

# 5. 重复运行(注意:会重复插入,这是预期行为 — Watcher 不去重)
# 真正生产 cron 触发时,每天只跑一次,不会重复
```

### 6.4 失败处置

- `partial_reasons` 非空但 `metrics_inserted > 0`:**这是 partial 状态,不是失败**,记录在案但不阻塞
- 全部表都失败:多半是隧道断了,先 `systemctl restart acc-pgtunnel` 再重试
- ops_metrics 写不进:看 acc_app 是否有 INSERT 权限(应该有)

---

## 7. Step 6: CLI 工具

### 7.1 目标

提供 `acc` 命令行工具,让你不用写 SQL/Python 也能操作 L2。

### 7.2 实现

**写 `meta_ops/cli/main.py`**

```python
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
                    sym = {'ok':'✅','partial':'⚠️','failed':'❌','running':'⏳'}[stat]
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
        for st, n in cur.fetchall():
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
```

### 7.3 验证

```bash
# 1. acc 命令在 PATH 里
sudo -u accelerator /opt/accelerator/.venv/bin/acc --help

# 2. 软链到系统级(便于不带 venv 路径敲)
sudo ln -sf /opt/accelerator/.venv/bin/acc /usr/local/bin/acc

# 3. 各子命令 help
acc status --help
acc librarian --help
acc watcher --help
acc backfill --help

# 4. 实际跑
acc status              # 应输出系统状态
acc librarian run       # 应跑 Librarian
acc watcher run         # 应跑 Watcher 昨日
acc backfill 2026-04-28 2026-04-29   # 应补两天
acc status              # 跑完后再看,数字应增加
```

### 7.4 失败处置

- `acc: command not found`:venv entry point 没装好,`pip install -e /opt/accelerator/` 重装
- 软链权限报错:用 sudo

---

## 8. Step 7: systemd timer 部署 + 端到端验证

### 8.1 目标

让 Librarian 和 Watcher 自动按时跑,无需人工介入。

### 8.2 实现

**1. systemd service:librarian**

```bash
sudo bash -c 'cat > /etc/systemd/system/acc-librarian.service <<EOF
[Unit]
Description=Accelerator L2 Librarian — sync Pulse schema mirror
After=network-online.target acc-pgtunnel.service postgresql.service
Wants=network-online.target
Requires=acc-pgtunnel.service

[Service]
Type=oneshot
User=accelerator
WorkingDirectory=/opt/accelerator
ExecStart=/opt/accelerator/.venv/bin/python -m meta_ops.librarian.v0
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300
EOF'

sudo bash -c 'cat > /etc/systemd/system/acc-librarian.timer <<EOF
[Unit]
Description=Run accelerator librarian daily at 06:00 SGT

[Timer]
OnCalendar=*-*-* 06:00:00 Asia/Singapore
Persistent=true
Unit=acc-librarian.service

[Install]
WantedBy=timers.target
EOF'
```

**2. systemd service:watcher**

```bash
sudo bash -c 'cat > /etc/systemd/system/acc-watcher.service <<EOF
[Unit]
Description=Accelerator L2 Watcher — daily metrics ingestion
After=network-online.target acc-pgtunnel.service postgresql.service
Wants=network-online.target
Requires=acc-pgtunnel.service

[Service]
Type=oneshot
User=accelerator
WorkingDirectory=/opt/accelerator
ExecStart=/opt/accelerator/.venv/bin/python -m meta_ops.watcher.v0
StandardOutput=journal
StandardError=journal
TimeoutStartSec=600
EOF'

sudo bash -c 'cat > /etc/systemd/system/acc-watcher.timer <<EOF
[Unit]
Description=Run accelerator watcher daily at 23:00 SGT

[Timer]
OnCalendar=*-*-* 23:00:00 Asia/Singapore
Persistent=true
Unit=acc-watcher.service

[Install]
WantedBy=timers.target
EOF'
```

**3. 启用**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now acc-librarian.timer
sudo systemctl enable --now acc-watcher.timer
```

### 8.3 验证

```bash
# 1. timer 正常
systemctl list-timers acc-* --no-pager

# 期望:
# NEXT                          UNIT                ACTIVATES
# Wed 2026-05-01 06:00:00 +08    acc-librarian.timer acc-librarian.service
# Wed 2026-05-01 23:00:00 +08    acc-watcher.timer   acc-watcher.service

# 2. service 单元定义无错
systemctl status acc-librarian.service
systemctl status acc-watcher.service

# 3. 手动触发一次,看 systemd 跑通
sudo systemctl start acc-librarian.service
sudo systemctl status acc-librarian.service   # 应 active(exited) code=0
journalctl -u acc-librarian.service -n 30 --no-pager   # 看日志

sudo systemctl start acc-watcher.service
sudo systemctl status acc-watcher.service
journalctl -u acc-watcher.service -n 30 --no-pager

# 4. l2_run_log 里有了由 systemd 触发的记录(看 started_at 在最近几分钟)
acc status
```

### 8.4 失败处置

- `systemctl status` 显示 failed:看 journalctl,80% 是路径或 venv 问题
- timer 没出现在 list-timers:确认 `enable --now` 跑过了

---

## 9. Step 8: health_check 扩展 + Phase 1 验收

### 9.1 目标

把 Phase 1 的检查项加入 health_check.py,跑出来全 ✅ = Phase 1 完成。

### 9.2 实现

**编辑 `/opt/accelerator/scripts/health_check.py`**,在 `build_checks()` 里**追加**(不要替换现有检查):

```python
# 在 build_checks(env) 函数里,return checks 之前追加:

    # ── Phase 1 checks ──────────────────────────────
    
    # systemd timers
    checks.append(CommandCheck(
        "acc-librarian.timer enabled",
        ["systemctl", "is-enabled", "acc-librarian.timer"],
        must_contain="enabled"
    ))
    checks.append(CommandCheck(
        "acc-watcher.timer enabled",
        ["systemctl", "is-enabled", "acc-watcher.timer"],
        must_contain="enabled"
    ))
    
    # l2_run_log 表
    checks.append(PostgresCheck(
        "l2_run_log table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM l2_run_log"
    ))
    
    # Librarian 至少跑成功过一次
    checks.append(PostgresCheck(
        "librarian has succeeded",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM l2_run_log WHERE kind='librarian' AND status IN ('ok','partial')"
        # 如果返回 0 这条会算 ok(query 本身成功),
        # 但你看输出会发现 count=0,人工识别。
        # 真正严格判断在验收清单里手动确认。
    ))
    
    # knowledge mirror 存在
    checks.append(FileExistsCheck(
        "knowledge schema mirror",
        "/opt/accelerator/knowledge/pulse/schema/articles.md"
    ))
    checks.append(FileExistsCheck(
        "librarian last_synced_at",
        "/opt/accelerator/knowledge/pulse/_meta/last_synced_at.txt"
    ))
    
    # acc 命令可用
    checks.append(CommandCheck(
        "acc command installed",
        ["/usr/local/bin/acc", "--help"],
    ))
```

### 9.3 Phase 1 完成的硬性验收

按顺序跑这些命令,**全部满足 = Phase 1 通过**:

```bash
# 1. 所有 health_check 项 ✅
sudo -u accelerator /opt/accelerator/.venv/bin/python /opt/accelerator/scripts/health_check.py
# 期望:All N checks passed (N >= 14 + 7 = 21 左右)

# 2. Pulse Connector 单元测试全过
cd /opt/accelerator
sudo -u accelerator /opt/accelerator/.venv/bin/pytest meta_ops/pulse_connector/tests/ -v
# 期望:4 passed

# 3. knowledge/pulse/schema/ 5 张表 .md 文件齐全
ls /opt/accelerator/knowledge/pulse/schema/
# 期望:articles.md  configs.md  interactions.md  publishes.md  topics.md

# 4. ops_metrics 至少有 1 天的数据(跑了 watcher 至少 1 次)
PGPASSWORD=$(sudo grep ACC_DB_PASSWORD /opt/accelerator/.env | cut -d= -f2) \
  psql -h 127.0.0.1 -U acc_app -d accelerator <<EOF
SELECT count(*) AS metrics_rows,
       count(DISTINCT date(snapshot_at)) AS distinct_days,
       min(snapshot_at) AS earliest,
       max(snapshot_at) AS latest
FROM ops_metrics;
EOF
# 期望:metrics_rows > 0

# 5. l2_run_log 里 librarian 和 watcher 都有 ok/partial 记录
PGPASSWORD=... psql ... -c "
  SELECT kind, status, count(*) FROM l2_run_log GROUP BY kind, status;"
# 期望:librarian ok N, watcher ok N(允许 partial)

# 6. acc status 输出健康
acc status

# 7. CLI 各命令都跑得通(不必改数据,跑 --help 都行)
acc librarian run --help && acc watcher run --help && acc backfill --help

# 8. 文档同步上传到 /opt/accelerator/docs/
ls /opt/accelerator/docs/
# 期望:CLAUDE.md / PROJECT_BLUEPRINT.md / INFRASTRUCTURE.md / PHASE0_SPEC.md / PHASE1_SPEC.md
```

### 9.4 完成后回填蓝图

把以下信息回填到 PROJECT_BLUEPRINT.md 第 §6.7 / §6.8 节:

- §6.7 实际产出:列出所有 Phase 1 模块的代码路径、首次成功跑的时间戳、ops_metrics 第一天行数
- §6.8 踩到的坑:Phase 1 期间所有 unexpected 都记下(参考 Phase 0 §5.6 的格式)

---

## 10. 给 Claude Code 的执行约束

1. **严格按 Step 顺序**,前一个验收通过才进下一个
2. **每个 Step 完成后报告**:跑了什么、验证结果、unexpected
3. **遇到不明确立即停下来问**,尤其是:
   - Pulse 表实际字段跟 spec 假设不一致
   - psycopg2 连接报错
   - systemd unit 启动失败
4. **不要自作主张做 spec 没要求的事**:
   - 不加监控告警(留 Phase 2)
   - 不补 docs 镜像(留 Phase 2)
   - 不写 Watcher/Librarian 的单元测试(只 Connector 写)
   - 不优化 SQL 查询(暴露真实瓶颈再说)
5. **Phase 1 范围严格按 §0.5 的偏好执行**,若发现某条偏好实际不可行,**报告而非绕过**

---

## 11. Phase 1 之后预览(不要执行)

Phase 1 完成后 Phase 2 会做:

- Librarian v1 升级(加 docs/code_index/extracted)
- Analyst v0(Claude Sonnet 写周报)
- 知识查询 CLI

但 Phase 1 期间**不要碰这些**。

---

> 任何模糊不清、跟现实冲突的地方,**问我,不要猜**。
