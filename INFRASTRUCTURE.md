# Accelerator L2 — 基础设施参考文档

> **文档性质**:运维参考 / 故障档案
> **目标读者**:遇到基础设施问题需要诊断的人(含 LLM 协作者)
> **更新规则**:任何拓扑变更/配置变更/新故障档案都更新此文档,在文件末尾追加变更日志
> **不属于这里**:架构设计哲学(在 PROJECT_BLUEPRINT.md)、当前任务执行步骤(在 PHASE_N_SPEC.md)

---

## 1. 网络拓扑(权威版)

```
accelerator-jb (柔佛, 火山引擎 2C4G)              pulse-hk (HK, 阿里云)
─────────────────────────────────                 ─────────────────────
IP: 101.47.30.178                                 IP: 43.99.0.100
OS: Ubuntu 24.04 LTS                              OS: Alibaba Cloud Linux 3
TZ: Asia/Singapore                                TZ: Asia/Shanghai
Python: 3.12.3                                    PostgreSQL: 13.23

业务代码 (Phase 1+)                                postgres 13.23
   ↓ libpq                                         ↑
 127.0.0.1:5433        ssh -N -L 5433:127.0.0.1:5432   127.0.0.1:5432
       └─── systemd acc-pgtunnel.service ──────────┘     ↑
            (Restart=always, ServerAlive 心跳)            │
                                                          │
                    l2_tunnel@43.99.0.100                 │
                    ed25519 key + permitopen 限制         │
                                                          │
                                                     l2_reader (PG 角色)
                                                     SELECT-only · 无 CREATE
```

## 2. 关键事实速查表

| 项目 | 值 |
|---|---|
| accelerator-jb IP | 101.47.30.178 |
| pulse-hk IP | 43.99.0.100 |
| L2 数据库连接(本机) | `127.0.0.1:5432` (DB: `accelerator`, user: `acc_app`) |
| Pulse 数据库连接(经隧道) | `127.0.0.1:5433` (DB: `pulse`, user: `l2_reader`) |
| 隧道 SSH 用户 | `l2_tunnel@pulse-hk` |
| 隧道密钥 | `/home/accelerator/.ssh/id_pulse_hk` (ed25519) |
| 隧道 systemd 单元 | `acc-pgtunnel.service` |
| 健康检查 timer | `acc-healthcheck.timer`(每小时) |

## 3. SSH 隧道配置详情

### 3.1 pulse-hk 上的 l2_tunnel 用户

```bash
# 用户创建(已完成,记录命令以备重建)
useradd --system --shell /sbin/nologin --home-dir /home/l2_tunnel --create-home l2_tunnel

# authorized_keys 内容
# 路径: /home/l2_tunnel/.ssh/authorized_keys
# 权限: 600, owner l2_tunnel:l2_tunnel
# 内容(单行):
no-X11-forwarding,no-agent-forwarding,no-pty,permitopen="127.0.0.1:5432" ssh-ed25519 AAAA... acc-jb tunnel to pulse-hk
```

**关键约束**:
- `no-pty`:即使 key 泄漏,攻击者也拿不到 shell
- `no-agent-forwarding`:不能转发回攻击者本地的 SSH agent
- `permitopen="127.0.0.1:5432"`:只能转发到 pulse-hk 自己的 5432,不能转发到任何其他端口或主机
- 用户 shell 是 `/sbin/nologin`:即使 SSH 限制被绕过,登录也无效

### 3.2 acc-pgtunnel.service systemd 单元

```ini
# 路径: /etc/systemd/system/acc-pgtunnel.service
[Unit]
Description=SSH tunnel from accelerator-jb to pulse-hk PostgreSQL
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=accelerator
ExecStart=/usr/bin/ssh -N \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=yes \
    -L 5433:127.0.0.1:5432 \
    -i /home/accelerator/.ssh/id_pulse_hk \
    l2_tunnel@43.99.0.100
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**关键参数解释**:
- `ServerAliveInterval=30 / ServerAliveCountMax=3`:每 30s 心跳,3 次失败(共 90s)判定连接死,触发重启
- `ExitOnForwardFailure=yes`:转发失败立即退出(配合 Restart=always 做健康自愈)
- `StrictHostKeyChecking=yes`:防止 MITM(known_hosts 已固化 pulse-hk 指纹)
- `Restart=always / RestartSec=5`:5 秒后自动重启

## 4. PostgreSQL 配置详情

### 4.1 pulse-hk 上 l2_reader 角色权限

```sql
-- 创建角色
CREATE ROLE l2_reader WITH LOGIN PASSWORD '...';

-- 授权
GRANT CONNECT ON DATABASE pulse TO l2_reader;
\c pulse
GRANT USAGE ON SCHEMA public TO l2_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO l2_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO l2_reader;

-- ★ 关键加固:闭合 PUBLIC 默认 ACL 中的 CREATE 权限(踩坑 5.6.4)
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON SCHEMA public TO pulse;  -- 把 CREATE 显式授给 owner
```

### 4.2 当前 schema public ACL 状态

```
postgres=UC      -- owner: 全部权限
=U               -- PUBLIC: 仅 USAGE(可见,不可建)
l2_reader=U      -- L2 读账号: 仅 USAGE
pulse=C          -- pulse 应用: 显式授予 CREATE(为 Pulse 应用本身保留建表能力)
```

### 4.3 accelerator-jb 上 acc_app 配置(本机)

```
Database: accelerator
User: acc_app
Host: 127.0.0.1:5432
pg_hba: host accelerator acc_app 127.0.0.1/32 scram-sha-256
        host accelerator acc_app ::1/128 scram-sha-256
postgresql.conf 调优:
   shared_buffers = 256MB
   effective_cache_size = 1GB
   work_mem = 16MB
   maintenance_work_mem = 64MB
   max_connections = 30
```

## 5. 验证操作清单

### 5.1 隧道健康验证

```bash
# 在 accelerator-jb 上跑
systemctl status acc-pgtunnel.service       # 应显示 active(running)
ss -tnlp | grep 5433                        # 应看到 ssh 监听 5433
nc -zv 127.0.0.1 5433                       # 应显示 succeeded
```

### 5.2 l2_reader 权限验证(完整版,含踩坑 5.6.4 的 CREATE 检查)

```bash
# 加载 .env 中的密码
export PGPASSWORD=$(grep HK_DB_PASSWORD /opt/accelerator/.env | cut -d= -f2)

# 1. 读权限正常
psql -h 127.0.0.1 -p 5433 -U l2_reader -d pulse \
    -c "SELECT count(*) FROM articles;"
# 期望:返回行数(当前约 110)

# 2. 写权限被拒(必须全部失败)
for cmd in \
    "INSERT INTO articles (id) VALUES (gen_random_uuid())" \
    "UPDATE articles SET id=id" \
    "DELETE FROM articles" \
    "DROP TABLE articles" \
    "TRUNCATE TABLE articles" \
    "CREATE TABLE l2_test_xxx (x int)"
do
    echo "Testing: $cmd"
    psql -h 127.0.0.1 -p 5433 -U l2_reader -d pulse -c "$cmd" 2>&1 | head -2
    echo "---"
done

# 期望:每条都报 "permission denied" 或 "must be owner"
```

### 5.3 healthcheck.py 完整运行

```bash
sudo -u accelerator python3 /opt/accelerator/scripts/health_check.py
# 期望:All 14 checks passed
```

## 6. 故障档案

### 6.1 OpenSSH `restrict` + `permitopen` 在 OpenSSH 8.0p1 (Alibaba Cloud Linux 3) 上行为异常

**症状**:
authorized_keys 用 `restrict,permitopen="127.0.0.1:5432" ssh-ed25519 ...` 时,SSH 连接握手成功但 channel open 阶段被拒,日志:
```
debug1: Connection to port 5433 forwarding to 127.0.0.1 port 5432 requested.
channel 2: open failed: administratively prohibited: open failed
```

**根因**:OpenSSH 8.0 上 `restrict` 关键字与 `permitopen` 联动行为有 bug,虽然 `permitopen` 应该是允许列表,但被 `restrict` 默认禁止覆盖了。

**解法**:回退到经典写法:
```
no-X11-forwarding,no-agent-forwarding,no-pty,permitopen="127.0.0.1:5432" ssh-ed25519 ...
```

效果完全等价(都是禁 X11/agent/pty + 仅允许特定端口转发),但绕开了便利关键字的 bug。

### 6.2 通过 SSH 推 heredoc 脚本不能用 `read -r` 读密码

**症状**:
```bash
ssh server "bash -s" <<'SCRIPT'
    read -r PWD
    echo "got: $PWD"
SCRIPT
```
`PWD` 永远是空字符串。

**根因**:`bash -s` 把整个 heredoc 当成脚本本体读(stdin),内层 `read` 无 stdin 可读。

**解法**:把敏感参数作为脚本变量直接嵌入:
```bash
PASSWORD="..."
ssh server "bash -s" <<SCRIPT
    PWD="$PASSWORD"
    echo "got: \$PWD"
SCRIPT
```

(密码通过 SSH 加密通道传输,不暴露在命令行参数 / process list 中)

### 6.3 paramiko stdin.write() 在 Windows console 触发 UnicodeEncodeError

**症状**:
```python
stdin.write("password\n")
# UnicodeEncodeError: 'utf-8' codec can't encode character '\udcef' (surrogate)
```

**根因**:Windows console codec 引入了非 UTF-8 surrogate 字符,污染了字符串。

**解法**:绕过 Python 字符层,直接写 bytes:
```python
# 原写法(失败):
stdin.write("password\n")

# 修复后:
channel.sendall(b"password\n")
```

### 6.4 PG schema-level CREATE 权限通过 PUBLIC 默认 ACL 继承

**症状**:`l2_reader` 仅 GRANT SELECT 后,实测仍可执行 `CREATE TABLE`。

**根因**:PG 默认 schema public 的 ACL 是 `=UC`(PUBLIC has USAGE+CREATE),所有角色继承到这两个权限。

**解法**:
```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON SCHEMA public TO pulse;  -- 显式给 owner
```

**附加教训**:任何 SELECT-only 角色测试都必须包含 CREATE 检查,否则会有"看似 SELECT-only 实则可建表"的隐性写权限。

---

## 变更日志

| 日期 | 变更 | 操作者 |
|---|---|---|
| 2026-04-30 | 初版创建,记录 Phase 0 完成时的基础设施状态 | Phase 0 验收 |

---

**END OF INFRASTRUCTURE.md**
