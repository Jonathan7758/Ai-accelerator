"""L2 自身运行历史的统一接入点。所有数字员工启动结束都用它留痕。"""
from datetime import datetime, timezone
from typing import Optional
import json
import uuid


class RunLogger:
    """每个角色每次运行,包一层 with 块即可自动记录。

    Usage:
        with RunLogger(db_conn, kind='watcher') as run:
            # do stuff
            run.set_summary({'rows_inserted': 42})
            # 自动写 status='ok' / 'failed'

    状态语义:
      - 进入 with 块:写入一条 status='running'
      - 正常退出:更新为 status='ok'
      - mark_partial() 被调用且无异常:更新为 status='partial'
      - 任何异常逃出:更新为 status='failed',error_message 取异常类型 + 文本
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
        """标记本次运行是部分成功(不算 failed,但有问题需关注)。"""
        self.partial = True
        self.partial_reason = reason
