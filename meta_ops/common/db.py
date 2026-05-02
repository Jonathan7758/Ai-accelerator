"""统一的 DB 连接工厂。

所有 L2 内部代码访问数据库都从这里取连接,不要在各模块各自调 psycopg2.connect。
- get_local_db():  L2 自己的本机 PG (accelerator DB)
- get_pulse_db():  Pulse 的 PG,经 acc-pgtunnel.service 暴露在 127.0.0.1:5433
"""
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
    """通过 SSH 隧道连接 Pulse 的 PG。隧道由 acc-pgtunnel.service 维护。

    若 timeout 超过 10 秒,通常意味着隧道挂了 — 应当报错让 systemd 拉起。
    """
    return psycopg2.connect(
        host=os.environ['HK_DB_HOST'],       # 127.0.0.1
        port=int(os.environ['HK_DB_PORT']),  # 5433
        dbname=os.environ['HK_DB_NAME'],
        user=os.environ['HK_DB_USER'],
        password=os.environ['HK_DB_PASSWORD'],
        connect_timeout=10,
    )
