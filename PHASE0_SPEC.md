# Accelerator L2 — Phase 0 开发方案

> 给 Claude Code 的开发任务说明书
> Phase 0 目标:基础设施搭建 + 网络验证 + 数据库就绪
> 预计工时:2-3 小时
> 完成后产出:一台可被 L2 应用代码部署上去的、所有依赖就绪的服务器

---

## 一、项目背景(Claude Code 必读)

### 1.1 项目定位

我正在构建一个叫 **Accelerator** 的"运营元层"系统(也称 L2 系统)。它的作用是:

- 自动观察 L1 生产系统(我已有的另一个项目 Pulse,跑在 HK 服务器上)的运营数据
- 每周自动产出诊断报告,推送到 Telegram 由我做决策
- 我做决策后,自动把决策落地到 Pulse 代码库(通过 git PR)

所以 Accelerator 是 **Pulse 的服务者**,不是 Pulse 的扩展。两套系统物理隔离、生命周期独立。

### 1.2 当前阶段

Phase 0 不实现任何业务逻辑,只做"地基":服务器初始化、Postgres 就绪、网络验证、跨服务器只读连接打通。

完成后,我能在新服务器上拥有:
- 一个跑着 Postgres 16 的全新机器
- 三张空表(ops_decisions, ops_metrics, ops_playbook)等着被写入
- 一条到 HK Pulse 的只读数据通道
- 一套验证脚本,任何时候跑都能告诉我"基础设施还活着没"

### 1.3 服务器信息

- **新服务器**:火山引擎柔佛(Johor Bahru, Malaysia),2C4G,40GB SSD,Ubuntu 22.04
- **预期主机名**:`accelerator-jb`(在 ~/.ssh/config 里这么叫)
- **HK 现有服务器**:`pulse-hk`(我已配置好 SSH),IP 为 43.99.0.100
- **本地操作环境**:Windows + Claude Code,通过 SSH 远程操作两台服务器

### 1.4 命名约定(很重要,请严格遵守)

- 项目代码根目录:`/opt/accelerator/`(**不要叫 pulse-l2 或 pulse-meta,这台机器不从属于 Pulse**)
- 应用运行用户:`accelerator`(不要用 root 跑应用进程)
- 数据库名:`accelerator`
- 数据库应用用户:`acc_app`
- HK 上专门给 L2 用的只读账号:`l2_reader`
- 日志路径:`/var/log/accelerator/`

### 1.5 关于 secret 管理

**不要在代码里硬编码任何密码、API key、token**。本 Phase 涉及的 secret 通过以下方式管理:

- 服务器上密码类:写到 `/opt/accelerator/.env`(权限 600,owner accelerator)
- SSH 密钥类:`~/.ssh/` 标准位置
- 我会在每个步骤明确告诉你某个 secret 应该填到哪里。**任何时候你不确定 secret 怎么处理,停下来问我**。

---

## 二、Phase 0 的工作分解

按依赖顺序,Phase 0 拆成 5 个 Step。**严格按顺序执行**,前一个 Step 验证通过才能进下一个。

```
Step 1: 服务器初始化 + 用户/目录结构
   ↓
Step 2: 网络连通性验证(关键!Claude/TG/HK)
   ↓
Step 3: Postgres 16 安装 + 配置 + 三张表创建
   ↓
Step 4: HK 只读通道建立(在 HK 上创建 l2_reader,在新机上验证可连)
   ↓
Step 5: 健康检查脚本 + 部署 systemd timer 验证一切正常
```

每个 Step 都有明确的 **执行内容、验证标准、失败处置**。

---

## 三、Step 1: 服务器初始化

### 3.1 任务

在新服务器(accelerator-jb)上完成基础初始化。

### 3.2 执行内容

**1. 系统更新和基础包安装**

```bash
apt update && apt upgrade -y
apt install -y \
  postgresql-16 postgresql-client-16 \
  git \
  python3.11 python3.11-venv python3-pip \
  curl jq \
  systemd-timesyncd \
  ufw \
  htop tmux
```

注意:Ubuntu 22.04 默认仓库里 Postgres 版本可能不是 16。如果 `apt install postgresql-16` 失败,先添加 PostgreSQL 官方 APT 源:

```bash
sh -c 'echo "deb https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
sh -c 'echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
apt update
apt install -y postgresql-16
```

**2. 时区和时间同步**

时区设为新加坡(SGT, UTC+8),与 Pulse HK 一致。这样所有 cron/timer 时间不需要换算。

```bash
timedatectl set-timezone Asia/Singapore
systemctl enable --now systemd-timesyncd
timedatectl status   # 验证显示 Asia/Singapore
```

**3. 创建应用用户和目录**

```bash
useradd -m -s /bin/bash accelerator
mkdir -p /opt/accelerator
chown accelerator:accelerator /opt/accelerator

mkdir -p /var/log/accelerator
chown accelerator:accelerator /var/log/accelerator
```

**4. 防火墙配置(简单但必要)**

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp        # SSH
# Postgres 5432 不对外开放(本地连接为主,跨机连接走 SSH 隧道)
ufw --force enable
ufw status
```

**5. 创建 .env 文件骨架(不填值,只建结构)**

```bash
sudo -u accelerator bash -c 'cat > /opt/accelerator/.env <<EOF
# Accelerator L2 - Environment Variables
# Generated: <填日期>

# Database (本机 Postgres)
ACC_DB_HOST=127.0.0.1
ACC_DB_PORT=5432
ACC_DB_NAME=accelerator
ACC_DB_USER=acc_app
ACC_DB_PASSWORD=

# HK Pulse 只读连接(Step 4 填)
HK_DB_HOST=43.99.0.100
HK_DB_PORT=5432
HK_DB_NAME=pulse
HK_DB_USER=l2_reader
HK_DB_PASSWORD=

# Anthropic API(Phase 1 才用,先留空)
ANTHROPIC_API_KEY=

# Telegram(Phase 1 才用,先留空)
TG_BOT_TOKEN=
TG_ADMIN_CHAT_ID=

EOF'

chmod 600 /opt/accelerator/.env
chown accelerator:accelerator /opt/accelerator/.env
```

### 3.3 验证标准

执行以下检查,**全部通过**才进 Step 2:

```bash
# 1. 关键软件版本
postgres --version          # 应显示 PostgreSQL 16.x
python3.11 --version        # 应显示 Python 3.11.x
git --version               # 任意 2.x 版本

# 2. 时区
timedatectl | grep "Time zone"   # 应包含 Asia/Singapore

# 3. 用户和目录
id accelerator                                # 应存在
ls -la /opt/accelerator/                      # 应 owner 是 accelerator
ls -la /opt/accelerator/.env                  # 权限应是 -rw-------(600)

# 4. Postgres 服务
systemctl is-active postgresql                # 应输出 active

# 5. 防火墙
ufw status | grep "Status: active"            # 应匹配
```

### 3.4 失败处置

任何一项不通过,**停下来报告给我**,不要尝试"绕过"或"猜测修复"。

特别注意:**如果 Postgres 16 装不上(系统不支持等),立刻停下来问我**——不要降级到 14 或 15,数据库版本一旦定下后期不好改。

---

## 四、Step 2: 网络连通性验证(关键!)

### 4.1 任务

验证三条关键出站网络通道工作正常。**这一步如果有不通的,后面所有 Phase 都跑不起来**,所以必须在最早期验证。

### 4.2 执行内容

**1. 测试到 Anthropic API 的直连**

```bash
# 在新服务器上跑(注意:本步不消耗 token,只是握手)
curl -sS -o /dev/null -w "HTTP %{http_code} | DNS %{time_namelookup}s | Connect %{time_connect}s | Total %{time_total}s\n" \
  https://api.anthropic.com/v1/messages \
  -H "content-type: application/json" \
  -d '{}'
```

**期望输出**:`HTTP 401`(因为没带 API key,所以是 401)+ DNS 和连接时间合理(总耗时 < 1 秒)。

**最重要的是 401,而不是 timeout/connection refused/SSL error**。401 表示服务器接到了请求,只是拒绝处理——这正是我们要的"网络通"的证明。

如果是 timeout 或者 connection refused,**停下来报告**,这意味着柔佛节点也被封了或者网络配置有问题。

**2. 测试到 Telegram API 的连通**

```bash
curl -sS https://api.telegram.org -o /dev/null -w "HTTP %{http_code}\n"
```

**期望**:HTTP 200 或 404 都算通(404 是因为没指定 method,但服务器应答了)。

**3. 测试到 GitHub 的连通(后期 Craftsman 需要)**

```bash
curl -sS https://api.github.com -o /dev/null -w "HTTP %{http_code}\n"
```

**期望**:HTTP 200。

**4. 测试到 HK Pulse 服务器的网络可达性**

```bash
# 测试 ICMP(可能被防火墙挡,失败也没关系)
ping -c 3 -W 2 43.99.0.100

# 测试 Postgres 端口可达(这个必须通,Step 4 要用)
nc -zv 43.99.0.100 5432
```

**期望**:`nc` 显示 succeeded 或 open。如果显示 timeout,说明 HK 那边的 Postgres 没对外开放或者防火墙挡了——记下来,Step 4 处理。

### 4.3 验证标准

| 通道 | 必须通过 | 失败的影响 |
|---|---|---|
| Anthropic API | ✅ 必须(否则 L2 无法运行) | 阻塞,需立即处理 |
| Telegram API | ✅ 必须 | 阻塞,需立即处理 |
| GitHub API | ✅ 必须 | Phase 0 不阻塞,但 Phase 4 阻塞 |
| HK Pulse 5432 | ⚠️ 期望通 | 如果不通,Step 4 改用 SSH 隧道方案 |

### 4.4 失败处置

**Anthropic 401 拿不到(timeout 或其他错误)**:
- 立即停止 Phase 0
- 报告给我具体错误信息
- 我们一起决定换阿里云 SG 还是加 VPN

**HK Pulse 5432 不通**:
- 不算阻塞,记下来
- 继续 Step 3
- Step 4 时改用"通过 SSH 隧道连 HK Postgres"的方案(我会在 Step 4 提供)

---

## 五、Step 3: Postgres 配置 + 三张表创建

### 5.1 任务

把本机 Postgres 配置成适合 2C4G 机器的参数,创建数据库和应用用户,创建三张核心表。

### 5.2 执行内容

**1. 优化 Postgres 配置**

编辑 `/etc/postgresql/16/main/postgresql.conf`,设置以下参数(用 sed 或直接编辑):

```conf
# Memory(2C4G 机器优化版)
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 16MB
maintenance_work_mem = 64MB

# Connections
max_connections = 30

# Logging(便于排查)
log_min_duration_statement = 500ms
log_line_prefix = '%m [%p] %u@%d '

# WAL(Write-Ahead Logging)
wal_level = replica
max_wal_size = 1GB
min_wal_size = 80MB
```

**重要**:这些参数不要加在文件末尾,要找到原配置项替换或在配置文件已有的位置修改。如果配置项原本注释掉的(`#shared_buffers = ...`),取消注释并改值。

如果文件结构不熟,**先备份,再修改**:
```bash
cp /etc/postgresql/16/main/postgresql.conf /etc/postgresql/16/main/postgresql.conf.bak
```

**2. 配置 pg_hba.conf 允许本地应用连接**

编辑 `/etc/postgresql/16/main/pg_hba.conf`,确保以下行存在(通常默认就有):

```
# 本地 socket 连接(默认应该就有)
local   all             all                                     peer

# 本机 TCP 连接(应用通过 127.0.0.1 连)
host    accelerator     acc_app         127.0.0.1/32            scram-sha-256
host    accelerator     acc_app         ::1/128                 scram-sha-256
```

**3. 重启 Postgres 应用配置**

```bash
systemctl restart postgresql
systemctl status postgresql   # 必须 active 且无 error
```

**4. 创建数据库和应用用户**

```bash
# 生成强密码并立即存到 .env
APP_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)

# 写入 .env(替换那一行,不是追加)
sed -i "s|^ACC_DB_PASSWORD=.*|ACC_DB_PASSWORD=$APP_PASSWORD|" /opt/accelerator/.env

# 创建用户和库
sudo -u postgres psql <<EOF
CREATE USER acc_app WITH PASSWORD '$APP_PASSWORD';
CREATE DATABASE accelerator OWNER acc_app;
GRANT ALL PRIVILEGES ON DATABASE accelerator TO acc_app;
EOF

# 验证连接(应该不报错)
PGPASSWORD=$APP_PASSWORD psql -h 127.0.0.1 -U acc_app -d accelerator -c "SELECT version();"
```

**5. 创建三张核心表**

把以下 SQL 保存到 `/opt/accelerator/sql/001_init_schema.sql`,然后跑它:

```sql
-- /opt/accelerator/sql/001_init_schema.sql
-- Accelerator L2 - Initial Schema

-- 启用 UUID 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 表 1: ops_decisions
-- 运营决策日志。每一次"我们改了什么、为什么改、怎么验证"都是一条记录。
-- ============================================================
CREATE TABLE ops_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- 决策内容
    decision_type TEXT NOT NULL,       -- prompt_change | matrix_update | workflow_tweak | strategy_pivot | other
    subject TEXT NOT NULL,             -- 受影响对象,如 "title_template_T1" 或 "matrix_entry_W3.1"
    before_state JSONB,                -- 改前
    after_state JSONB,                 -- 改后
    
    -- 决策的"思考"(给人和给未来 LLM 读)
    rationale TEXT NOT NULL,           -- 自然语言解释,完整因果链
    evidence JSONB DEFAULT '[]'::jsonb,-- 引用的数据,如 [{"type":"weekly_report","id":"2026W17"}]
    
    -- 验证回路
    verification_plan TEXT,            -- 怎么验证生效
    verification_due_at TIMESTAMPTZ,   -- 什么时候该回填
    verification_result JSONB,         -- 实际结果,验证时回填
    
    -- 状态
    status TEXT NOT NULL DEFAULT 'active'  -- active | reverted | superseded
        CHECK (status IN ('active', 'reverted', 'superseded')),
    superseded_by UUID REFERENCES ops_decisions(id),
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ops_decisions_tenant_decided ON ops_decisions(tenant_id, decided_at DESC);
CREATE INDEX idx_ops_decisions_subject ON ops_decisions(subject);
CREATE INDEX idx_ops_decisions_status ON ops_decisions(status) WHERE status = 'active';

-- ============================================================
-- 表 2: ops_metrics
-- 指标快照。每条记录 = 某个对象在某个时点的所有指标。
-- 设计上故意"长且窄"——一行一指标快照,JSONB 里平铺所有指标。
-- ============================================================
CREATE TABLE ops_metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- 主体
    subject_type TEXT NOT NULL,        -- article | matrix_entry | title_template | weekly_aggregate
    subject_id TEXT NOT NULL,          -- 主体的标识,如 article_id 或 'W18'
    
    -- 指标(平铺,如 {"ctr":0.12,"read_rate":0.55,"share":0.06,"views":12340})
    metrics JSONB NOT NULL,
    
    -- 数据来源(便于追溯)
    source TEXT NOT NULL,              -- pulse_db | wechat_api | manual_input | computed
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ops_metrics_subject ON ops_metrics(tenant_id, subject_type, subject_id, snapshot_at DESC);
CREATE INDEX idx_ops_metrics_time ON ops_metrics(tenant_id, snapshot_at DESC);

-- ============================================================
-- 表 3: ops_playbook
-- 沉淀下来的"已被验证的策略"。从 ops_decisions 里晋升而来。
-- ============================================================
CREATE TABLE ops_playbook (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',  -- 'global' 表示跨 tenant
    
    -- 策略本体
    pattern_name TEXT NOT NULL UNIQUE,         -- 唯一名称
    context TEXT NOT NULL,                     -- 什么场景下适用
    recipe TEXT NOT NULL,                      -- 具体怎么做(prompt 片段、步骤、规则)
    
    -- 出处
    evidence_decisions UUID[] DEFAULT '{}',    -- 来自哪些 ops_decisions 的支持
    
    -- 置信度
    confidence REAL NOT NULL DEFAULT 0.5       -- 0-1
        CHECK (confidence >= 0 AND confidence <= 1),
    last_validated_at TIMESTAMPTZ,
    
    -- 状态
    status TEXT NOT NULL DEFAULT 'active'      -- active | deprecated
        CHECK (status IN ('active', 'deprecated')),
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ops_playbook_tenant ON ops_playbook(tenant_id, status);
CREATE INDEX idx_ops_playbook_confidence ON ops_playbook(confidence DESC) WHERE status = 'active';

-- ============================================================
-- 自动维护 updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_ops_decisions_updated_at BEFORE UPDATE ON ops_decisions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_ops_playbook_updated_at BEFORE UPDATE ON ops_playbook
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- 元信息表(记录 schema 版本,以后迁移用)
-- ============================================================
CREATE TABLE schema_versions (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_versions (version, description) 
VALUES ('001', 'Initial schema: ops_decisions, ops_metrics, ops_playbook');
```

执行:

```bash
mkdir -p /opt/accelerator/sql
chown accelerator:accelerator /opt/accelerator/sql
# 把上面 SQL 内容写入 /opt/accelerator/sql/001_init_schema.sql

# 跑 schema 创建
PGPASSWORD=$(grep ACC_DB_PASSWORD /opt/accelerator/.env | cut -d= -f2) \
  psql -h 127.0.0.1 -U acc_app -d accelerator -f /opt/accelerator/sql/001_init_schema.sql
```

### 5.3 验证标准

```bash
# 1. 三张表都存在
PGPASSWORD=$(grep ACC_DB_PASSWORD /opt/accelerator/.env | cut -d= -f2) \
  psql -h 127.0.0.1 -U acc_app -d accelerator -c "\dt"
# 应看到 ops_decisions, ops_metrics, ops_playbook, schema_versions

# 2. schema 版本记录已写入
PGPASSWORD=... psql ... -c "SELECT * FROM schema_versions;"
# 应看到 version='001' 那一行

# 3. 索引都创建了
PGPASSWORD=... psql ... -c "\di"
# 应看到 idx_ops_decisions_*, idx_ops_metrics_*, idx_ops_playbook_*

# 4. 写一条测试数据再删掉,确认表能写
PGPASSWORD=... psql ... <<EOF
INSERT INTO ops_decisions (decision_type, subject, rationale)
VALUES ('other', 'test', 'phase0 verification');
SELECT id, decision_type, rationale FROM ops_decisions WHERE subject='test';
DELETE FROM ops_decisions WHERE subject='test';
EOF
```

### 5.4 失败处置

- 如果 SQL 文件跑到一半报错,**不要继续**。把已创建的对象 DROP 掉,排查原因再重来。
- 如果是字段类型/约束的问题,报告给我看(可能 Postgres 16 某些语法和我熟悉的版本有差异)。

---

## 六、Step 4: HK 只读通道建立

### 6.1 任务

在 HK Pulse 服务器(43.99.0.100)上创建一个**只读账号** `l2_reader`,在新服务器上验证可连。

**为什么是只读**:依赖最小权限原则。L2 永远不应该写 L1 的数据。这是物理隔离,比代码层"小心点"强一万倍。

### 6.2 执行内容

**1. 在 HK Pulse 上创建 l2_reader**

SSH 到 pulse-hk(我已经配好这个 host),执行:

```bash
# 生成密码
L2_READER_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
echo "记下来:$L2_READER_PASSWORD"   # 等下要填到新机的 .env

# 在 pulse 数据库里创建
sudo -u postgres psql <<EOF
CREATE USER l2_reader WITH PASSWORD '$L2_READER_PASSWORD';
GRANT CONNECT ON DATABASE pulse TO l2_reader;
\c pulse
GRANT USAGE ON SCHEMA public TO l2_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO l2_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO l2_reader;
EOF
```

**注意**:Pulse 用的是 Supabase 而不是本机 Postgres——根据 user memory 里 Pulse 的 01-system-architecture.md。**这一步要重新评估**:

- **如果 Pulse 用的是 Supabase**:不能直接 `sudo -u postgres psql`。需要在 Supabase 控制台或通过 Supabase 的 SQL Editor 创建 l2_reader,而且 Supabase 不一定支持你直连其底层 Postgres——你可能需要走 PostgREST API。**这种情况下停下来问我**,我们改用 Supabase Service Key + REST API 拉数据的方案。
- **如果 Pulse 改用了本机 Postgres**:按上面 SQL 跑。

**Claude Code,执行前先帮我检查一下 pulse-hk 的 Postgres 是怎么部署的**:

```bash
ssh pulse-hk 'systemctl is-active postgresql'  # 如果是 active 说明本机 Postgres 在跑
ssh pulse-hk 'cat /opt/pulse/.env | grep -E "(SUPABASE|DB_HOST)" | head -5'  # 看用的是什么
```

**根据上面输出告诉我**:Pulse 用的是 Supabase 还是本机 Postgres?然后**等我决定后**再继续 Step 4。

**2. 配置 HK 防火墙允许新服务器的 Postgres 连接(如果是本机 Postgres)**

```bash
# 在 pulse-hk 上
ufw allow from <柔佛新机 IP> to any port 5432
ufw status
```

同时确认 `pg_hba.conf` 允许从外网连接:

```
host    pulse           l2_reader       <柔佛IP>/32             scram-sha-256
```

**3. 在新服务器上把密码填入 .env**

```bash
sed -i "s|^HK_DB_PASSWORD=.*|HK_DB_PASSWORD=$L2_READER_PASSWORD|" /opt/accelerator/.env
```

**4. 验证连接通**

```bash
PGPASSWORD=$L2_READER_PASSWORD psql \
  -h 43.99.0.100 -U l2_reader -d pulse \
  -c "SELECT current_user, current_database(), now();"
```

**期望**:输出 `l2_reader | pulse | <时间>`。

**5. 验证只读权限正确**

```bash
# 这条应该成功(SELECT 是允许的)
PGPASSWORD=$L2_READER_PASSWORD psql -h 43.99.0.100 -U l2_reader -d pulse \
  -c "SELECT count(*) FROM articles LIMIT 1;"

# 这条应该失败(写权限被拒)
PGPASSWORD=$L2_READER_PASSWORD psql -h 43.99.0.100 -U l2_reader -d pulse \
  -c "INSERT INTO articles (title) VALUES ('test');"
# 期望:ERROR: permission denied for table articles
```

**第二条必须报错**——如果它居然成功了,说明权限给多了,**立即在 HK 上 REVOKE**。

### 6.3 验证标准

| 检查 | 期望 |
|---|---|
| `psql l2_reader` 连接 pulse | 成功 |
| `SELECT * FROM articles` | 成功 |
| `INSERT INTO articles` | 必须失败,permission denied |
| `UPDATE articles SET ...` | 必须失败 |
| `DROP TABLE articles` | 必须失败 |

### 6.4 失败处置

**如果 Pulse 用 Supabase**:
- Step 4 整体不按这个方案执行
- 改用 Supabase Service Key(已经在 Pulse .env 里)+ REST API 来读数据
- 这种情况下,新机的 .env 里要填 SUPABASE_URL 和 SUPABASE_SERVICE_KEY 而不是 HK_DB_*
- 由我决定,Claude Code 等我反馈

**如果连接 timeout**:
- 检查 HK 防火墙(ufw 和阿里云控制台安全组)
- 检查 Postgres 的 listen_addresses(应该是 '*' 或包含外网 IP)
- 检查 pg_hba.conf 的 host 行

---

## 七、Step 5: 健康检查脚本

### 7.1 任务

写一个 `health_check.py`,任何时候跑都能告诉我"基础设施还活着没"。这是 Phase 0 的最终交付物——以后每个 Phase 跑通后,这个脚本要扩展;它是整个 L2 系统的"心跳"。

### 7.2 执行内容

**创建 `/opt/accelerator/scripts/health_check.py`**:

```python
#!/usr/bin/env python3
"""
Accelerator L2 - Health Check
任何时候跑这个脚本,都应该全部通过。失败项即"基础设施缺陷"。

Usage:
    python3 health_check.py                  # 跑全部检查
    python3 health_check.py --json           # JSON 输出(给监控用)
"""
import os
import sys
import json
import socket
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Dict


# ── 工具:从 .env 加载环境变量 ───────────────────────────────
ENV_PATH = Path("/opt/accelerator/.env")

def load_env() -> Dict[str, str]:
    env = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


# ── 检查项 ──────────────────────────────────────────────────
class Check:
    def __init__(self, name: str):
        self.name = name
    
    def run(self) -> Tuple[bool, str]:
        raise NotImplementedError


class FileExistsCheck(Check):
    def __init__(self, name: str, path: str, must_owner: str = None):
        super().__init__(name)
        self.path = Path(path)
        self.must_owner = must_owner
    
    def run(self):
        if not self.path.exists():
            return False, f"missing: {self.path}"
        if self.must_owner:
            stat = self.path.stat()
            try:
                import pwd
                owner = pwd.getpwuid(stat.st_uid).pw_name
                if owner != self.must_owner:
                    return False, f"wrong owner: {owner} (want {self.must_owner})"
            except Exception:
                pass
        return True, "ok"


class CommandCheck(Check):
    def __init__(self, name: str, cmd: List[str], must_contain: str = None):
        super().__init__(name)
        self.cmd = cmd
        self.must_contain = must_contain
    
    def run(self):
        try:
            result = subprocess.run(
                self.cmd, capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                return False, f"exit {result.returncode}: {output[:200]}"
            if self.must_contain and self.must_contain not in output:
                return False, f"missing '{self.must_contain}' in output"
            return True, output.strip()[:80] or "ok"
        except subprocess.TimeoutExpired:
            return False, "timeout (10s)"
        except FileNotFoundError:
            return False, f"command not found: {self.cmd[0]}"


class TCPCheck(Check):
    def __init__(self, name: str, host: str, port: int):
        super().__init__(name)
        self.host = host
        self.port = port
    
    def run(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=5):
                return True, f"{self.host}:{self.port} reachable"
        except Exception as e:
            return False, f"{self.host}:{self.port} unreachable: {e}"


class PostgresCheck(Check):
    def __init__(self, name: str, env: Dict[str, str], 
                 host_key: str, port_key: str, db_key: str, 
                 user_key: str, pwd_key: str, query: str = "SELECT 1"):
        super().__init__(name)
        self.env = env
        self.host = env.get(host_key, "")
        self.port = env.get(port_key, "5432")
        self.db = env.get(db_key, "")
        self.user = env.get(user_key, "")
        self.pwd = env.get(pwd_key, "")
        self.query = query
    
    def run(self):
        if not self.pwd:
            return False, "password not set in .env"
        try:
            env = os.environ.copy()
            env["PGPASSWORD"] = self.pwd
            result = subprocess.run(
                ["psql", "-h", self.host, "-p", self.port, 
                 "-U", self.user, "-d", self.db,
                 "-tAc", self.query],
                capture_output=True, text=True, timeout=10, env=env
            )
            if result.returncode != 0:
                return False, result.stderr.strip()[:200]
            return True, f"query ok: {result.stdout.strip()[:60]}"
        except Exception as e:
            return False, str(e)[:200]


# ── 检查清单 ────────────────────────────────────────────────
def build_checks(env: Dict[str, str]) -> List[Check]:
    checks = []
    
    # 1. 文件系统
    checks.append(FileExistsCheck("env file exists", "/opt/accelerator/.env", "accelerator"))
    checks.append(FileExistsCheck("log dir exists", "/var/log/accelerator", "accelerator"))
    checks.append(FileExistsCheck("sql dir exists", "/opt/accelerator/sql"))
    
    # 2. 系统服务
    checks.append(CommandCheck(
        "postgres service active",
        ["systemctl", "is-active", "postgresql"],
        must_contain="active"
    ))
    
    # 3. 时区
    checks.append(CommandCheck(
        "timezone is Singapore",
        ["timedatectl", "show", "-p", "Timezone"],
        must_contain="Asia/Singapore"
    ))
    
    # 4. 网络出站
    checks.append(TCPCheck("anthropic api reachable", "api.anthropic.com", 443))
    checks.append(TCPCheck("telegram api reachable", "api.telegram.org", 443))
    checks.append(TCPCheck("github api reachable", "api.github.com", 443))
    
    # 5. 本机 Postgres
    checks.append(PostgresCheck(
        "local postgres connect",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD"
    ))
    checks.append(PostgresCheck(
        "ops_decisions table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM ops_decisions"
    ))
    checks.append(PostgresCheck(
        "ops_metrics table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM ops_metrics"
    ))
    checks.append(PostgresCheck(
        "ops_playbook table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM ops_playbook"
    ))
    
    # 6. HK 只读连接(如果配了)
    if env.get("HK_DB_PASSWORD"):
        checks.append(PostgresCheck(
            "hk pulse readonly connect",
            env, "HK_DB_HOST", "HK_DB_PORT", "HK_DB_NAME",
            "HK_DB_USER", "HK_DB_PASSWORD"
        ))
    
    return checks


# ── 主流程 ──────────────────────────────────────────────────
def main():
    json_mode = "--json" in sys.argv
    env = load_env()
    checks = build_checks(env)
    
    results = []
    all_ok = True
    
    for check in checks:
        ok, msg = check.run()
        results.append({"name": check.name, "ok": ok, "detail": msg})
        if not ok:
            all_ok = False
    
    if json_mode:
        print(json.dumps({
            "ok": all_ok,
            "checked_at": datetime.now().isoformat(),
            "results": results
        }, indent=2))
    else:
        print(f"=== Accelerator L2 Health Check @ {datetime.now()} ===\n")
        for r in results:
            symbol = "✅" if r["ok"] else "❌"
            print(f"{symbol} {r['name']:<40} {r['detail']}")
        print()
        if all_ok:
            print(f"🟢 All {len(results)} checks passed.")
        else:
            failed = sum(1 for r in results if not r["ok"])
            print(f"🔴 {failed}/{len(results)} checks failed.")
    
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
```

**赋权和验证**:

```bash
chmod +x /opt/accelerator/scripts/health_check.py
chown -R accelerator:accelerator /opt/accelerator/scripts/

# 第一次跑(用 accelerator 用户)
sudo -u accelerator python3 /opt/accelerator/scripts/health_check.py
```

### 7.3 验证标准

`health_check.py` 跑出来**所有项必须 ✅ 通过**。允许"hk pulse readonly connect"在 Pulse 用 Supabase 的情况下被跳过(脚本里已经判断了 HK_DB_PASSWORD 为空就跳过)。

### 7.4 部署成 systemd timer(可选,但建议)

让 health_check 每小时自动跑一次,失败时通过 stderr 进 journal:

```ini
# /etc/systemd/system/acc-healthcheck.service
[Unit]
Description=Accelerator L2 hourly health check

[Service]
Type=oneshot
User=accelerator
ExecStart=/usr/bin/python3 /opt/accelerator/scripts/health_check.py
StandardOutput=journal
StandardError=journal
```

```ini
# /etc/systemd/system/acc-healthcheck.timer
[Unit]
Description=Run accelerator health check hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now acc-healthcheck.timer
systemctl list-timers | grep acc-healthcheck
```

---

## 八、Phase 0 完成的标志

满足以下**全部条件**,Phase 0 才算完成:

- [ ] `health_check.py` 跑出全 ✅
- [ ] 三张表存在,可读可写
- [ ] HK 只读连接通(或者已经确认 Supabase 方案,等 Phase 1 改用 REST API)
- [ ] systemd timer `acc-healthcheck.timer` 已激活
- [ ] `/opt/accelerator/.env` 权限是 600,owner 是 accelerator
- [ ] 没有任何 secret 进 git(本 Phase 没创建 git repo,但要确认 .env 不会被随手 push 出去)

完成后,**新机器进入"待 Phase 1 上代码"状态**。所有应用代码会写到 `/opt/accelerator/meta_ops/` 下,但 Phase 0 不创建这个目录——它属于 Phase 1。

---

## 九、给 Claude Code 的执行规则

请严格遵守:

1. **顺序执行 Step 1 → 5,不跳步、不并行**
2. **每个 Step 跑完,执行该 Step 的"验证标准"**,验证通过才进下一个
3. **遇到 unexpected 情况立即停下来问我**——尤其是:
   - Postgres 16 装不上
   - Anthropic API 不通
   - HK Pulse 不是本机 Postgres 而是 Supabase
   - 任何权限/防火墙相关报错
4. **不要为了"看起来更好"做计划外的事**——比如安装 Docker、配 nginx、装监控工具。Phase 0 就是上面这些,不多做。
5. **报告格式**:每个 Step 完成后,告诉我:
   - 哪些命令跑了
   - 验证结果(贴关键输出)
   - 有没有遇到 unexpected 情况
   - 是否准备好进下一 Step

完成全部 5 个 Step 后,**最后跑一遍 health_check.py 把全 ✅ 的输出贴给我**,这就是 Phase 0 的交付凭证。

---

## 十、Phase 0 之后是什么(给你预览,但不执行)

Phase 0 完成后,我会基于实际经验给你 **Phase 1 spec**,内容是:

- 写 Watcher v0(从 HK Pulse 拉昨日 articles 数据 → 写本机 ops_metrics)
- 部署 systemd timer 每日 23:00 SGT 跑
- 第一份周度数据报表(简单 CLI 命令,看本周积累的指标)

Phase 1 完成后,你已经拥有"自动化的运营数据归档",这是 L2 第一个真正可见的能力。

但 **Phase 0 不要看 Phase 1 的事**,集中把地基打好。

---

> 如有任何模糊不清的地方,**问我,不要猜**。
