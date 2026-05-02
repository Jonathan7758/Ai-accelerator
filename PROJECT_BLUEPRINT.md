# Accelerator L2 — 项目完整蓝图

> **文档性质**:项目宪法 / 长期参考蓝图
> **目标读者**:未来 6 个月内任何接手或回看这个项目的人(含 LLM 协作者)
> **更新频率**:每完成一个 Phase 后,回来更新对应 Phase 的"实际产出"和"踩到的坑"
> **不更新这里**:具体执行步骤(那些在 PHASE_N_SPEC.md)、临时决策(那些在 ops_decisions 表)
> **创建时间**:2026-04-29
> **最后更新**:2026-04-29 (项目蓝图初版)

---

## 目录

1. [项目背景与目标](#1-项目背景与目标)
2. [核心设计哲学(必读)](#2-核心设计哲学)
3. [整体架构](#3-整体架构)
4. [Phase 总览](#4-phase-总览)
5. [Phase 0 — 基础设施](#5-phase-0--基础设施)
6. [Phase 1 — 数据观察层](#6-phase-1--数据观察层)
7. [Phase 2 — 分析层](#7-phase-2--分析层)
8. [Phase 3 — 协作层](#8-phase-3--协作层)
9. [Phase 4 — 执行层](#9-phase-4--执行层)
10. [跨 Phase 的横切关注点](#10-跨-phase-的横切关注点)
11. [踩坑记录与决策日志](#11-踩坑记录与决策日志)
12. [术语表](#12-术语表)

---

## 1. 项目背景与目标

### 1.1 起源

项目所有者 Jonathan(江盛)运营一个由 LLM 驱动的中文内容矩阵,首个账号是历史号(江盛.红如火)。该业务的执行系统叫 **Pulse**,部署在阿里云香港,经过约 2 个月运营已基本稳定。

但运营过程中暴露了一个结构性问题:**所有的"运营改进动作"——调工作流、改 prompt、插入热点、周分析、即时验证、知识沉淀——都依赖人本人坐到 Claude Code 前面打字才能发生**。Pulse 是一个能"执行内容生产"的系统,但不是一个能"自己迭代自己"的系统。

### 1.2 项目目标

构建一个独立运行的"运营元层系统"(Meta-Ops Layer,代号 L2),为内容运营提供**自我迭代能力**。具体来说:

- 自动观察 Pulse(L1)的运营数据
- 每周自动产出诊断报告,推送到 Telegram 由人决策
- 人决策后,自动把决策落地到 Pulse 代码库

**根本价值**:Accelerator 不是历史号的工具。**历史号是 Accelerator 的第一个客户**。系统设计上,Accelerator 必须能未来服务多个不同领域的内容矩阵账号(A 象限号、其他博主)。

### 1.3 这个项目"不是什么"

为了避免范围漂移,显式列出:

- ❌ 不是 Pulse 的扩展模块
- ❌ 不是历史号专属系统
- ❌ 不是内容生成工具
- ❌ 不是替代人决策的"全自动 AI 主管"
- ❌ 不是通用的"AI agent 平台"或"workflow 工具"

它**只**是:一个为内容矩阵运营提供"决策-验证-沉淀"自动化闭环的系统。

### 1.4 衡量成功的标准

| 时间点 | 成功标准 |
|---|---|
| Phase 1 完成(W4) | 历史号一周的运营数据自动归档到 ops_metrics |
| Phase 2 完成(W8) | 每周日自动收到一份 Analyst 诊断报告,无需人工触发 |
| Phase 3 完成(W12) | 决策从"我打开 Claude Code 沟通"变成"在 TG 群里点几下" |
| Phase 4 完成(W16) | 一条决策从我点击"采用"到 Pulse 出现 PR 的全程不超过 15 分钟 |
| **核心指标** | **6 个月后 ops_decisions 累计 ≥ 50 条,其中 verification_result 已回填的 ≥ 30 条** |

---

## 2. 核心设计哲学

> 这一节是全文档最重要的一节。如果读者只能读一节,读这一节。所有具体设计都从这些哲学衍生。

### 2.1 三层结构哲学

任何能持续自我改进的 AI 应用,都至少有三层:

```
L3 治理层 (Governance)         — 人的决策点,不能自动化
L2 元运营层 (Meta-Ops)         — 本项目所在层,生产"规则"
L1 执行层 (Execution)          — Pulse 所在层,生产"内容"
```

**关键判断**:L1 必须自动化(否则规模不可能);L3 必须人决策(否则失控);**L2 是真正的设计空间**——它决定系统能否自我迭代。

### 2.2 服务者关系哲学

Accelerator 是 Pulse 的**服务者**,不是 Pulse 的扩展。这个区别决定了:

| 维度 | 规则 |
|---|---|
| 数据流向 | Accelerator 只读 Pulse,**永远不写** |
| 代码改动 | Accelerator 通过 Git PR 提议改 Pulse,**不直接 SSH 改** |
| 部署独立 | Pulse 挂了 Accelerator 不挂(只是没新数据);反向同理 |
| 命名 | Accelerator 任何文件/目录/服务**不带 pulse 前缀** |

### 2.3 单机简单哲学

明确选择**单机部署**而非"双机协作 / 控制平面+数据平面分离 / 微服务"。

**理由**:复杂度有"质量"不光有"数量"。两台机器协作引入的是分布式问题(网络抖动、状态同步、调度时序);一台机器多进程引入的是单机问题(内存、CPU、进程管理)。前者大多无标准解,后者有。**能单机就单机**。

### 2.4 知识所有权与使用权分离

L2 大量依赖 Pulse 的知识(schema、代码结构、业务规则)。但:

- Pulse 是知识的**所有者**(权威源单一)
- L2 是知识的**使用者**(持有派生镜像)
- 镜像由专门的同步机制(Librarian)更新,L2 内任何角色不"原创"对 Pulse 的认知

### 2.5 Agent 化是有成本的

不是所有事情都该用 LLM Agent。判断标准:

- 输入输出确定、规则清晰 → **写函数**(Watcher 拉数据、CLI 工具、schema 渲染)
- 需要理解、判断、生成 → **用 Agent**(Analyst 诊断、Craftsman 写代码)
- 需要协调状态、不需要判断 → **写状态机**(Facilitator)

Agent 化要付的成本:token 钱、延迟、不可预测性、调试难度。

### 2.6 克制比能力更重要

LLM 在面对模糊指令时倾向于"做更多",这是**反模式**。设计 spec 时:

- 显式写"这一版做什么"和"这一版**不做**什么"
- 明确风险点的"停下来问"而不是"继续猜"
- 不为想象的未来需求做提前抽象

### 2.7 延迟决策直到信息更多

每个 Phase 完成后,真实经验会让下一阶段的决策更准。**不要在 Phase 0 时就把 Phase 4 的细节定死**。

蓝图(本文)只定**架构层**(模块边界、数据流、原理);具体实现由各 Phase 启动时的 spec 决定。

### 2.8 "决策-验证"对子是系统的复利

L2 的智力上限不来自模型变聪明,来自系统积累的"做了什么决策 + 结果验证如何"对子的密度。一个跑 6 个月、积累 200 条 ops_decisions(其中 80 条已 verified)的 L2,远胜于刚部署时——不是因为 Claude 升级,是因为系统对自己业务的实证理解变深了。

**这是 AI 原生应用最具复利的部分,也是 L2 设计的根本目标**。

---

## 3. 整体架构

### 3.1 三层视图

```
┌──────────────────────────────────────────────────────────────────┐
│  L3 治理层(人的决策点)                                            │
│   • 审批 Analyst 建议 → 落 ops_decisions                           │
│   • Review Craftsman PR → 合 main                                  │
│   • 任何架构级改动                                                  │
└──────────────────────────────────────────────────────────────────┘
                                ↕  Telegram
┌──────────────────────────────────────────────────────────────────┐
│  L2 Accelerator(本项目,跑在火山引擎柔佛 2C4G)                     │
│                                                                    │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  数字员工层(5 个角色)                                     │   │
│   │   ├─ Watcher       —— 数据观察(函数,Phase 1)             │   │
│   │   ├─ Librarian     —— 知识同步(函数+少量LLM,Phase 1+2)    │   │
│   │   ├─ Analyst       —— 诊断分析(LLM Agent,Phase 2)         │   │
│   │   ├─ Facilitator   —— 协作主持(状态机,Phase 3)            │   │
│   │   └─ Craftsman     —— 代码改动(LLM Agent,Phase 4)         │   │
│   └──────────────────────────────────────────────────────────┘   │
│                                                                    │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  存储与知识层                                              │   │
│   │   ├─ Postgres 16 (本机)                                   │   │
│   │   │   ├─ ops_decisions   — 运营决策日志                    │   │
│   │   │   ├─ ops_metrics     — 指标快照                        │   │
│   │   │   ├─ ops_playbook    — 已验证策略                      │   │
│   │   │   └─ l2_run_log      — L2 自身运行历史                  │   │
│   │   └─ /opt/accelerator/knowledge/pulse/ — Pulse 知识镜像   │   │
│   │       ├─ docs/      ├─ schema/                            │   │
│   │       ├─ code_index/├─ extracted/                         │   │
│   │       └─ _meta/                                           │   │
│   └──────────────────────────────────────────────────────────┘   │
│                                                                    │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  抽象层                                                   │   │
│   │   └─ Pulse Connector — 所有访问 Pulse 的统一接口            │   │
│   └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                                ↕
 "L2 → SSH 隧道 → Pulse 本机 PG(SELECT-only 角色)+ git PR"
L2 与 Pulse 之间走 SSH 端口转发隧道,而非直接 PG 连接(因 HK 安全组未对柔佛开 5432)
Pulse 同时持有本机 PG(主数据)和 Supabase(辅助服务:storage/auth/realtime)
L2 只对接 Pulse 的本机 PG,不读 Supabase
隧道由 systemd 管理,具备自愈能力
L2 在 Pulse 那边的 PG 角色权限范围:仅 SELECT,无任何写权限,无 schema-level CREATE
具体拓扑/IP/端口/用户名/SSH 算法等运维级细节:见 /opt/accelerator/docs/INFRASTRUCTURE.md
                                ↕
┌──────────────────────────────────────────────────────────────────┐
│  L1 Pulse(已存在,不在本项目范围内)                                │
│   阿里云香港 / Supabase / Telegram Bot / 中文内容生成发布管线        │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 数字员工角色定义

| 角色 | 实现形态 | 主要触发 | 主要读 | 主要写 |
|---|---|---|---|---|
| Watcher | Python 函数 + systemd timer | 每日 23:00 SGT | Pulse(via Connector) | ops_metrics |
| Librarian | Python 函数 + 少量 LLM 调用 | 每日 06:00 SGT | Pulse 文档/schema | knowledge/pulse/ |
| Analyst | Claude Sonnet Agent | 每周日 20:00 + 按需 | ops_metrics + ops_decisions + knowledge/ | 周报 markdown(草稿) |
| Facilitator | TG Bot + Python 状态机 | 实时(消息驱动) | 各角色输出 | TG 消息 + ops_decisions(已审批) |
| Craftsman | Claude Code 风格 Agent | 决策审批后触发 | ops_decisions + knowledge/code_index/ | git PR 到 Pulse 仓库 |

### 3.3 角色间通信:经表不经直接调用

**关键设计原则**:角色之间不直接相互调用 Python 函数,而是**通过数据库表传递状态**。

```
Watcher → 写 ops_metrics → Analyst 启动时读
Analyst → 产出周报 markdown → Facilitator 推 TG
Facilitator → 用户审批 → 写 ops_decisions → Craftsman 启动时读
Craftsman → 写 ops_decisions.after_state → 后续 verification 阶段读
```

理由:松耦合 + 可独立测试 + 任何一个角色重启不影响其他。

### 3.4 单机资源画像

```
常驻服务(基线):
   Postgres 16            ~400 MB
   Facilitator TG Bot     ~250 MB(Phase 3 起)
   调度器/cron 进程        ~100 MB
   Ubuntu + buffer        ~800 MB
   ────────────────────────────────
   常驻总计               ~1.5 GB

按需触发(峰值,各角色不同时跑):
   Librarian              ~300 MB(LLM 调用时)
   Watcher                ~200 MB
   Analyst                ~500 MB(Sonnet 上下文)
   Craftsman              ~800-1000 MB(代码 + 测试)

   峰值总计              ~3 GB(2C4G 机器,余量 1GB)
```

---

## 4. Phase 总览

### 4.1 Phase 路线图

| Phase | 主题 | 核心交付物 | 预计工时 | 解锁的能力 |
|---|---|---|---|---|
| **0** | 基础设施 | 服务器初始化、Postgres 三表、网络验证、health_check | 2-3 小时 | 一台可被部署上去的就绪服务器 |
| **1** | 数据观察层 | Pulse Connector + Librarian v0 + Watcher + Run Log + CLI | 3-5 天(分散) | 自动化的运营数据归档 |
| **2** | 分析层 | Librarian 升级(code_index/extracted) + Analyst v0 + 知识查询 CLI | 5-7 天 | 每周自动产出诊断报告(本地查看) |
| **3** | 协作层 | Facilitator(TG Bot 多角色)+ 决策状态机 + 周报推送 | 5-7 天 | 在 TG 群里完成"看报告→批决策" |
| **4** | 执行层 | Craftsman + git PR 流水线 + Verification 回填机制 | 7-10 天 | 决策一键落地到 Pulse 代码 |

### 4.2 每个 Phase 的依赖关系

```
Phase 0 (基础设施)
   │
   ▼
Phase 1 (Watcher + Librarian v0)
   │  生产 ops_metrics 数据 + Pulse schema 镜像
   │
   ▼
Phase 2 (Analyst + Librarian 升级)
   │  消费 ops_metrics → 产出报告
   │  Librarian 加 code_index/extracted 给 Analyst 读
   │
   ▼
Phase 3 (Facilitator)
   │  消费 Analyst 报告 → 推 TG → 收审批 → 写 ops_decisions
   │
   ▼
Phase 4 (Craftsman)
      消费 ops_decisions → 改 Pulse 代码 → 提 PR
      Verification 阶段:Watcher 持续采集 → Analyst 回填验证结果
```

**关键约束**:Phase N+1 启动前,Phase N 必须达到"可用稳态"——不是"完美",但要"不会因为 N 的 bug 拖累 N+1"。

### 4.3 完成 Phase N 的判断标准

每个 Phase 内有 spec 写细节,通用的"完成标准":

1. 该 Phase 所有模块都跑通过至少一次成功的全流程
2. health_check 增加该 Phase 模块的检查项,全部通过
3. 该 Phase 引入的新角色,在 l2_run_log 中有连续 7 天的成功记录
4. 蓝图本节(对应 Phase 那一节)的"实际产出"和"踩到的坑"已回填

---

## 5. Phase 0 — 基础设施

### 5.1 业务目标

服务器从"裸机"到"可部署应用代码的就绪状态"。本 Phase **不实现任何业务逻辑**,只做地基。

### 5.2 模块清单

| 模块 | 说明 |
|---|---|
| 系统初始化 | Ubuntu 22.04 + 时区 + 用户 + 防火墙 |
| Postgres 16 | 安装、调优(2C4G 适配)、accelerator 数据库 |
| 三张核心表 | ops_decisions / ops_metrics / ops_playbook + schema_versions |
| 网络验证 | Anthropic / Telegram / GitHub / HK Pulse 连通性 |
| HK 只读通道 | 注:实际是 Supabase REST API(Pulse 用 Supabase) |
| health_check.py | 任何时候跑都能告诉我"基础设施是否健康" |

### 5.3 数据流

```
(无业务数据流,本 Phase 只产生"基础设施就绪"这一个事实)

服务器初始化命令 → 系统状态变更
                ↓
          Phase 0 完成的判断:
          health_check.py 全部 ✅
```

### 5.4 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 数据库版本 | Postgres 16 | 最新稳定版,jsonb 性能好,定下后期不变 |
| 时区 | Asia/Singapore (UTC+8) | 与 Pulse HK 一致,跨服务器协作不换算 |
| 应用用户 | accelerator(非 root) | 安全 + 权限可见 |
| 项目路径 | /opt/accelerator/ | 不带 pulse 前缀,体现独立身份 |
| Pulse 接入方式 | **Supabase REST API**(不是 PG 直连) | Pulse 用 Supabase,无法直连 |

### 5.5 实际产出(Phase 0 完成后回填)

服务器:火山引擎柔佛 accelerator-jb(具体 IP 见 INFRASTRUCTURE.md)
OS / Python:Ubuntu 24.04 LTS / Python 3.12.3(spec 写 22.04/3.11,实际更高,无影响)
完成日期:2026-04-30
资源使用:disk 3.2G/40G,memory 3.8GB,无 swap
健康检查:14 项全过(超过 spec 期望的 11 项)

比 spec 多做的事(完成度高一档):

SSH 隧道方案 + systemd 自愈
l2_reader 的 schema-level CREATE 权限主动闭合
l2_tunnel SSH 用户加固(限制 X11/agent/pty/permitopen)

具体配置参数见 INFRASTRUCTURE.md。

### 5.6 踩到的坑(Phase 0 进行中持续记录)

5.6.1 OpenSSH 便利关键字在不同发行版上行为不一致
教训:OpenSSH 的便利关键字(如 restrict)在不同版本/发行版上行为不一致,生产环境用经典枚举写法(no-X11-forwarding,no-agent-forwarding,no-pty,permitopen=...)更可靠。
5.6.2 通过 SSH 推 heredoc 脚本不能用 read -r 读密码
教训:bash -s 把整个 heredoc 当脚本读,内层 read 拿不到 stdin 输入。需要把敏感参数作为脚本变量直接嵌入(走 SSH 加密通道),不走 stdin。
5.6.3 paramiko 在 Windows console 触发 surrogates 错误
教训:跨平台 SSH 工具链假设 UTF-8 是不安全的,bytes-level 操作(stdin.buffer.read() + channel.sendall(bytes))更可靠。
5.6.4 PG schema-level CREATE 权限通过 PUBLIC 默认 ACL 继承
教训:PG 的默认 ACL public=UC 让 SELECT-only 角色仍能建空表。真正的"L2 不可写 L1"必须包含 schema-level CREATE 检查。Spec §6.3 验证表应在以后类似项目复用时加上 CREATE 检查。
5.6.5 Pulse HK 的环境与蓝图初版假设不一致
事实修订(此为蓝图核心事实变更,不仅是踩坑):

Pulse HK 的 PostgreSQL 是 13.23,不是蓝图初版假设的 16
Pulse HK 时区是 Asia/Shanghai,不是 Singapore
Pulse 是"本机 PG 主数据 + Supabase 辅助服务"混合架构,不是纯 Supabase

对后续 Phase 的硬约束:

Phase 1 SQL 不使用 PG 14+ 特性
Phase 1 Watcher 内部统一用 UTC,边界做时区转换
L2 Connector 走 SSH 隧道直连 PG,不用 Supabase REST API
Phase 2/4 启动前需评估"Supabase 部分功能是否影响 L2"

5.6.6 跨 LLM 会话协作中明文 secret 泄漏
教训:跨 LLM 会话协作时,secret 必须从源头 redact;源头未 redact 后只能轮换。这条规则刻意严苛是因为不对称——轮换成本几分钟,泄漏成本可能是几个月。
处理记录:Phase 0 期间两台机器 root 密码各在聊天中明文出现一次,已于 2026-04-30 全部轮换。

---

## 6. Phase 1 — 数据观察层

### 6.1 业务目标

让 Pulse 的运营数据**自动地、每天地、结构化地**流入 L2 的 ops_metrics 表,同时建立 Pulse 知识镜像的最小版本(schema 部分),作为后续 Phase 的数据基础。

完成后:每天早晨我能用一句 SQL 查到昨天历史号在各平台的 CTR/完读/转发数据。

### 6.2 模块清单

| 模块 | 类型 | 触发 | 在哪 |
|---|---|---|---|
| Pulse Connector | Python 库 | 被调用 | meta_ops/pulse_connector/ |
| Librarian v0 | 函数 + 少量 LLM | 06:00 SGT 每日 | meta_ops/librarian/ |
| Watcher v0 | 纯函数 | 23:00 SGT 每日 | meta_ops/watcher/ |
| Run Log | DB 表 | 各角色启动结束写 | sql/002_run_log.sql |
| CLI 工具 | Click/Typer | 手动 | meta_ops/cli/ |

### 6.3 业务流(每日时间线)

```
06:00 SGT  Librarian 启动
           │
           ├─ Pulse Connector 拉 5 张关心的表的 schema 元信息
           ├─ 渲染成 markdown,写到 knowledge/pulse/schema/*.md
           ├─ 跟上次 diff,记录变化
           ├─ 写 _meta/last_synced_at.txt
           └─ 写 l2_run_log 一条
           完成 (~30 秒)

(白天 Pulse 在 HK 生产内容,L2 静默)

23:00 SGT  Watcher 启动
           │
           ├─ 健康检查:Librarian last_synced 是否 < 36h
           ├─ Pulse Connector 拉昨日 articles + publishes
           ├─ 转换为 ops_metrics 行格式
           ├─ INSERT 到本机 Postgres
           ├─ 周日时额外计算一条 weekly_aggregate
           └─ 写 l2_run_log 一条
           完成 (~2-5 分钟)

23:30 SGT  health_check 自动跑(Phase 0 已部署,Phase 1 扩展检查项)
```

### 6.4 数据流

```
┌────────────────────┐                    
│  Pulse (HK)         │                    
│  Supabase           │                    
│   articles          │                    
│   publishes         │                    
│   (其他)            │                    
└────────┬───────────┘                    
         │                                 
         │  HTTPS REST API + Service Key  
         │                                 
         ▼                                 
┌────────────────────────────────────────┐
│  Pulse Connector(L2 内部)              │
│   认证、分页、重试、错误处理             │
│   输出:dataclass 列表                   │
└────────┬─────────────────┬─────────────┘
         │                 │              
         ▼                 ▼              
┌────────────────┐  ┌────────────────────┐
│  Librarian v0   │  │  Watcher v0         │
│                 │  │                     │
│  schema → md    │  │  articles+publishes │
│                 │  │  → ops_metrics 行    │
└───────┬────────┘  └──────────┬─────────┘
        │                      │           
        ▼                      ▼           
┌──────────────────┐  ┌─────────────────────┐
│ knowledge/pulse/  │  │ Postgres            │
│   schema/*.md     │  │   ops_metrics       │
│   _meta/...       │  │   l2_run_log        │
└──────────────────┘  └─────────────────────┘
                              ↑
                              │
                       ┌──────┴──────┐
                       │ CLI 工具     │
                       │ acc status   │
                       │ acc backfill │
                       └─────────────┘
```

### 6.5 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Pulse 接入抽象 | 反腐败层(Anti-Corruption Layer) | 隔离 Pulse 数据模型,L2 内部用自己的 dataclass |
| 指标存储格式 | ops_metrics 长表 + JSONB | 早期阶段灵活性 > 规范性,指标定义本身在迭代 |
| Librarian v0 范围 | 只做 schema 镜像 | code_index/extracted 是 Phase 2 Analyst 才用,提前做没用 |
| Watcher 数据质量 | 不做过滤,诚实输出 | v0 目标是"建立数据流",真实问题暴露后再针对性修 |
| CLI 工具 | 必须有 | 给"未来的自己"留接口,运维心智负担降低关键 |

### 6.6 Phase 1 完成标志

- [ ] Pulse Connector 单元测试通过(mock 数据)
- [ ] Librarian 连续 7 天自动跑成功,knowledge/pulse/schema/ 有 5 张表的 md
- [ ] Watcher 连续 7 天自动跑成功,ops_metrics 有 ≥ 7 天数据
- [ ] CLI:`acc status` 能输出本周所有 run_log 摘要
- [ ] CLI:`acc backfill 2026-04-22 2026-04-28` 能补一周数据
- [ ] health_check 加入 librarian/watcher 检查,全部 ✅
- [ ] 蓝图本节"实际产出"已回填

### 6.7 实际产出(Phase 1 完成后回填)

**完成日期**:2026-05-02

#### 模块清单与代码路径

| 模块 | 路径 | Step | 备注 |
|---|---|---|---|
| 共享 DB / 日志 / RunLogger | `meta_ops/common/{db,run_log,logging_config}.py` | 1 / 2 | Phase 1 全模块依赖 |
| `l2_run_log` migration | `sql/002_run_log.sql` | 2 | 已 apply |
| Pulse Connector + dataclass | `meta_ops/pulse_connector/{connector,models}.py` | 3 | 4 测试通过(对齐真实 schema 后) |
| 数据契约文档 | `knowledge/pulse/SCHEMA_NOTES.md` | 3 | 修订过程见 §6.8.1 |
| Librarian v0 | `meta_ops/librarian/v0.py` | 4 | 镜像 5 张表 schema |
| Watcher v0 | `meta_ops/watcher/v0.py` | 5 | 写 ops_metrics + 周日聚合 |
| `acc` CLI | `meta_ops/cli/main.py` | 6 | 4 个子命令 |
| systemd unit | `/etc/systemd/system/acc-{librarian,watcher}.{service,timer}` | 7 | 23:00 / 06:00 SGT |
| 健康检查扩展 | `scripts/health_check.py`(+ 7 项) | 8 | Phase 0 14 + Phase 1 7 = 21 |

#### 首次成功运行时间(SGT)

- Librarian v0:**2026-05-02 12:47:04**(手动触发)/ systemd 触发 14:08:47
- Watcher v0:**2026-05-02 13:09:18**(手动触发)/ systemd 触发 14:10:29
- 第一条 ops_metrics 行:**article 8306f44f**(冉闵杀胡令)at 13:09:18,source=`pulse_pg_via_tunnel`

#### Phase 1 验收当日数据

| 指标 | 值 |
|---|---|
| ops_metrics 总行数 | 11(article=5, topic=6) |
| l2_run_log 总行数 | 8(librarian ok×3 / watcher partial×5) |
| knowledge/pulse/schema/*.md | 5(articles, topics, publishes, interactions, configs) |
| health_check 通过项 | 21 / 21 |
| Pulse Connector 单元测试 | 4 / 4 |

> Watcher 全部 partial 是设计行为(`interactions deferred to Phase 2/3 per SCHEMA_NOTES.md §5`),不是缺陷。

### 6.8 踩到的坑(Phase 1 进行中持续记录)

#### 6.8.1 SPEC 设计方法论错误:理想模型 vs 接入规约未分离

**现象**:Phase 1 Step 3 真实连接 Pulse 时,Connector SQL 报 `UndefinedColumn: word_count`。深度诊断后发现失配不是单点 bug,而是 SPEC 在 5 张表上系统性地把"理想字段"当作"真实字段"。

**根因**(由项目所有者诊断,完整版见 SCHEMA_NOTES.md):

1. **SPEC 基于"运营该看什么"写,没基于"Pulse 真实长什么样"写**——典型的从想象设计而非从现实设计
2. **Pulse 用 jsonb 做"演进缓冲",SPEC 把 jsonb key 错当成顶层列**——`word_count` 在 `versions` jsonb 里,不在顶层
3. **概念归属错位**——`angle` 实际是 article 的属性(每文一选),SPEC 错挂在 topic 上
4. **Pulse 多平台改造后 SPEC 没跟上**——`cover_url` 顶层列保留为兼容遗迹但全空,真值搬到 `platform_versions.cover_<platform>`
5. **SPEC 假设了 Pulse 还没做的功能**——`publishes.metrics` 当前所有行 = `{}`(数据回流未实现);`publishes.last_synced_at` 不存在;`interactions` 表的 sentiment / replied / platform / user_name 几乎全错位

**教训**:

- 跨系统集成的 SPEC,必须把"L2 内部数据模型(应然)"和"L1 → L2 接入规约(实然)"分开写
- 写"读外部系统"代码前,**第一步必须是 dump 真实 schema 做基线**,不是直接写 SQL
- jsonb 字段在 Pulse 这种系统里是"演进缓冲",不能假装它们是顶层列
- 概念归属(谁拥有什么属性)必须从真实业务流验证,不能靠直觉推断

**处理**:

- 创建 `/opt/accelerator/knowledge/pulse/SCHEMA_NOTES.md` 作为权威数据契约文档
- 修订 Connector 代码(详见 CONNECTOR_REVISION.md)
- Phase 1 Step 3 范围内移除 `interactions` 表接入(推迟到 Phase 2/3)

#### 6.8.2 服务器部署管道在 Phase 1 才补上

**现象**:Phase 0 的 `/opt/accelerator/` 不是 git repo,也没有 `deploy.sh`;Phase 1 起开始频繁推代码改动到服务器,scp 单文件容易漏传或传错路径(PROGRESS_SNAPSHOT 已记录)。

**处理**:Q&A 后选择"独立 git workdir + cp 同步"两段式:
- `/opt/accelerator-git/`:HTTPS clone GitHub(public repo,无需 server 端 SSH key),`git pull` 拿最新
- `/opt/accelerator/`:运行时根目录,持有 `.env` / `.venv` / DB-applied migration state,通过 `cp` 从 git workdir 同步源码

**教训**:
- 部署路径要在 Phase 0 就立起来,不能等到"开始改代码"再补——本次靠人在场协调成本可控,自动化阶段会出问题
- Phase 0 `deploy.sh` 缺失是真实疏漏,补一份 idempotent 的 `deploy.sh`(读 git workdir → rsync 排除 .env/.venv → 重启相关 systemd unit)是 Phase 2 启动前应做的小工

#### 6.8.3 SSH heredoc + Windows Git Bash 转义反复踩坑

**现象**:Phase 1 期间至少 4 次因 `ssh server "...$VAR..."` 在 Windows Git Bash 上被双层处理,导致变量失踪、`\d` 反斜杠被吃、`bash -c "..."` 嵌入 `\$doc` 直接语法错。

**固化模式**:复杂命令一律走"本地写脚本 → scp 到 /tmp → server 端独立调用"三步,**不再写多层嵌套引号的 heredoc**。该模式已写进 MEMORY.md 作为跨会话记忆。

**教训**:执行环境的怪癖必须主动避雷,而不是每次撞墙都"试一下另一种引号"。`PROGRESS_SNAPSHOT.md` 里"已踩过的坑"列表是这个机制的载体。

#### 6.8.4 partial 状态语义需要明确区分"故意"与"故障"

**现象**:Watcher v0 因为 interactions 表 Phase 1 不接入,**每次跑都标 partial**(原因:`interactions deferred to Phase 2/3`)。`acc status` 因此一直显示 ⚠️5,看起来像系统在持续报警。

**处理**:本期接受这个"假阳性",因为 partial 的语义工具(`run.mark_partial(reason)`)是对的,问题在于 status 视图没区分"故意 partial"和"意外 partial"。Phase 2 起 `acc status` 应区分:
- ⚠️ "deferred"(已知不接入,无需关注)
- ⚠️ "degraded"(有些数据没拉到,需关注)
- ❌ "failed"(整体失败)

**教训**:状态机的语义粒度不够细,会把"显式标记"和"未知问题"混成一锅。Phase 2 Analyst 触发逻辑应基于"degraded/failed",不基于"非 ok"——否则会被一直 deferred 噪声淹没。

---

## 7. Phase 2 — 分析层

### 7.1 业务目标

让系统具备**"自己看数据 + 写诊断"**的能力。完成后:每周日 20:00 SGT,系统自动产出一份包含本周关键发现、归因、下周建议的 markdown 周报。

**Phase 2 不做 TG 推送、不做决策落库**——周报只是 markdown 文件,本地查看即可。这两件事是 Phase 3 Facilitator 的责任。

### 7.2 模块清单

| 模块 | 类型 | 触发 |
|---|---|---|
| Librarian v1(升级) | LLM 加工 | 每日 06:00 SGT |
| Analyst v0 | Claude Sonnet Agent | 每周日 20:00 SGT + 按需 |
| 知识查询 CLI | Python + LLM | 手动 |
| 周报模板 | markdown 模板 + prompt | 静态资产 |

### 7.3 Librarian v1 升级内容

Phase 1 的 Librarian 只做 schema。Phase 2 升级加上:

```
新增产出:
├─ knowledge/pulse/docs/      # rsync Pulse 文档
├─ knowledge/pulse/code_index/  # LLM 加工:每个关键 .py 文件的"修改导航图"
└─ knowledge/pulse/extracted/   # LLM 加工:从多份文档提炼业务关键概念
                                # 例:matrix_v2_taxonomy.md / title_templates.md
                                #     kpi_definitions.md / operational_rhythm.md
```

**关键设计**:code_index 和 extracted 不是每天重新生成,**只在源文件变化时重生**。Librarian 检测到 Pulse 仓库或文档有新 commit 时,才调 Claude 加工对应部分。

### 7.4 业务流(每周日 + 每日)

```
每日 06:00  Librarian v1 启动
           │
           ├─ Phase 1 已有:同步 schema
           ├─ 新增:rsync Pulse 文档到 docs/
           ├─ 新增:detect git diff,变化的 .py 文件 → 调 Claude 重生 code_index
           ├─ 新增:detect doc 变化 → 调 Claude 重生 extracted
           └─ 写 l2_run_log
           完成 (~1-3 分钟,有 LLM 调用时更长)

每周日 20:00  Analyst v0 启动
           │
           ├─ 健康检查:Librarian 是否新鲜?Watcher 是否过去 7 天都成功?
           │   └─ 如果 Watcher 缺数据,告警并降级运行
           ├─ 读数据上下文:
           │   ├─ ops_metrics 本周 + 过去 4 周
           │   ├─ ops_decisions 过去 4 周(尤其是 status='active' 的)
           │   ├─ ops_playbook 全部 high-confidence 项
           │   └─ knowledge/pulse/extracted/ 业务概念
           ├─ 调 Claude Sonnet,基于模板 prompt 产出周报草稿
           ├─ 写到 reports/YYYYWW.md
           └─ 写 l2_run_log
           完成 (~3-8 分钟)
```

### 7.5 数据流

```
┌─────────────────────────────────────────────┐
│  Pulse(HK)                                  │
│   docs/ + 代码仓库(GitHub)                  │
└────────────┬────────────────────────────────┘
             │
             │  rsync + git fetch
             ▼
┌─────────────────────────────────────────────┐
│  Librarian v1                               │
│   ├─ rsync 文档                              │
│   ├─ detect changes                         │
│   └─ 调 Claude 加工 code_index/extracted    │
└────────────┬────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────┐
│  knowledge/pulse/(完整版)                   │
│   docs/ schema/ code_index/ extracted/       │
└────────────┬────────────────────────────────┘
             │ 读
             ▼
┌─────────────────────────────────────────────┐
│  Analyst v0                                  │
│                                              │
│  上下文组装:                                  │
│   ├─ ops_metrics(数据)                      │
│   ├─ ops_decisions(历史决策)                 │
│   ├─ ops_playbook(已验证策略)                │
│   └─ knowledge/extracted/(业务知识)          │
│                                              │
│  调 Claude Sonnet                           │
│                                              │
│  输出:reports/2026W18.md                    │
└────────────┬────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────┐
│  /opt/accelerator/reports/                  │
│   2026W18.md                                │
│   2026W19.md                                │
│   ...                                        │
└─────────────────────────────────────────────┘
```

### 7.6 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Analyst 模型 | Claude Sonnet 4.5 | 诊断质量决定整体上限,不省 |
| 周报输出 | markdown 草稿,**不直接落 ops_decisions** | 强制人审批中间一道,避免 Agent 自动改世界 |
| 上下文组装 | 函数化(确定性)+ Agent(理解) | 数据筛选用代码,理解和写作用 Agent |
| code_index 粒度 | 文件级(不到函数级) | Phase 2 够用,过度细化是 Phase 4 的事 |
| Librarian 增量 LLM | 检测变化才调 | 控制成本,避免每天全量重做 |

### 7.7 Phase 2 完成标志

- [ ] Librarian v1 能自动检测 Pulse 文档/代码变化并触发 LLM 加工
- [ ] knowledge/pulse/code_index/ 至少包含 5 个关键文件的导航图
- [ ] knowledge/pulse/extracted/ 至少包含 4 份核心业务概念提炼
- [ ] Analyst v0 连续 4 周自动产出周报,人工评分 ≥ 7/10
- [ ] CLI:`acc analyst run` 可手动触发分析
- [ ] CLI:`acc knowledge query "X1 主线是什么"` 可基于知识镜像问答
- [ ] 蓝图本节"实际产出"已回填

### 7.8 实际产出(Phase 2 完成后回填)

> _待回填_

---

## 8. Phase 3 — 协作层

### 8.1 业务目标

把 Phase 2 的"本地 markdown 周报"转化为**人能在 TG 群里完成全部决策动作**的协作流。完成后:你周日晚上不用打开电脑,在手机 Telegram 上看周报、点按钮做决策即可。

### 8.2 模块清单

| 模块 | 类型 | 触发 |
|---|---|---|
| Facilitator | python-telegram-bot | 实时(消息驱动) |
| 4 个 Bot 角色 | 同进程多 token | 同上 |
| 决策状态机 | Python + DB | 用户消息触发 |
| 周报推送器 | 函数 | Analyst 完成后触发 |

### 8.3 4 个 Bot 角色定义

| Bot username 示例 | 角色 | 何时发言 |
|---|---|---|
| @acc_watcher_bot | Watcher | 异常时告警(数据缺失、HK 不通) |
| @acc_analyst_bot | Analyst | 周报推送、回答 /ask 类问题 |
| @acc_craftsman_bot | Craftsman | 派工时报告、PR 完成通知 |
| @acc_facilitator_bot | Facilitator | 议程主持、决策选项按钮 |

**为什么用 4 个 Bot 不用 1 个**:让每条消息暴露"是谁说的、以什么职能在说",帮助你大脑切换决策模式。Single-Bot 会让所有判断混在一起。

### 8.4 决策状态机

每条决策候选从产出到落地,经历这些状态:

```
proposed     ─── Analyst 在周报里建议
   │
   ▼
displayed   ─── Facilitator 推到 TG,等用户响应
   │
   │ 用户点选 [采用] [否决] [讨论]
   │
   ├──→ rejected                 (终态:已否决)
   │
   ├──→ in_discussion             (用户想要更多分析)
   │       │
   │       │ 用户讨论后再做选择
   │       ▼
   │     proposed (回到上一步)
   │
   └──→ approved_pending_rationale (用户点"采用",等填 rationale)
           │
           │ 用户回复一句 rationale
           ▼
         approved                  (终态:已批准,触发 Craftsman in Phase 4)
            │
            │ Phase 2 期间:卡在这里,不自动落代码
            │ Phase 4 起:触发 Craftsman
            ▼
         (Phase 4 流程)
```

### 8.5 业务流(周日晚示例)

```
20:00  Analyst v0 跑完,产出 reports/2026W18.md
       (Phase 2 已实现)

20:05  周报推送器(Phase 3 新)读 reports/2026W18.md
       │
       ├─ 解析出"关键发现 + 候选决策"
       ├─ 构造 TG 消息(用 @acc_analyst_bot 身份发)
       ├─ 附 Inline Keyboard:
       │     [📄 完整报告] [✅ 采用建议] [❌ 否决] [💬 讨论]
       └─ 推送到运营 TG 群

20:?   你看到消息,选了 ✅
       │
       ├─ Facilitator(@acc_facilitator_bot)接住回调:
       │   "好,请用一句话写下你采用的理由(rationale)"
       │   状态:approved_pending_rationale
       │
       └─ 你回复:"T2 已用 3 周,该轮换了"

20:?   Facilitator:
       │
       ├─ 写 ops_decisions 一条记录
       │   {decision_type, subject, rationale, evidence, verification_plan, status='active'}
       │
       ├─ 回复你:"决策 #d92a 已记录。预期 W19 末看 CTR 是否回到 11%+。"
       │
       └─ Phase 3 流程到此结束(Phase 4 起,这里会触发 Craftsman)
```

### 8.6 数据流

```
┌──────────────────┐
│ Analyst (Phase 2)│
│ 产出 weekly md    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 周报推送器        │
│ 解析报告 + 构造  │
│ TG inline keyboard│
└────────┬─────────┘
         │
         ▼ HTTPS
┌──────────────────┐
│ Telegram         │
│ 用户在手机上看到  │
│ 周报和按钮        │
└────────┬─────────┘
         │ 用户点击
         ▼
┌──────────────────┐
│ Facilitator       │
│ (TG Bot 监听)     │
│ 状态机推进         │
└────────┬─────────┘
         │
         │ 状态:approved
         ▼
┌──────────────────┐
│ Postgres          │
│ ops_decisions     │
│  ↑ 新记录         │
└──────────────────┘
```

### 8.7 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| TG 库 | python-telegram-bot v21+ | 跟 Pulse 一致,生态成熟 |
| 多 Bot 实现 | 同进程,不同 token | 资源占用低,代码共享 |
| 状态持久化 | DB 表(不是内存) | Bot 重启不丢决策中状态 |
| Inline Keyboard 风格 | emoji + 短文本 | 移动端可读性,2-4 个按钮一行 |
| 12 小时无响应处理 | 自动归档为 deferred | 不强迫用户,但留可追溯 |

### 8.8 Phase 3 完成标志

- [ ] 4 个 Bot 都能正常收发,不串台
- [ ] 决策状态机所有 transition 都有测试覆盖
- [ ] 一次完整的"看周报 → 选 ✅ → 填 rationale → ops_decisions 入库"流程跑通
- [ ] 12 小时无响应自动归档
- [ ] 蓝图本节"实际产出"已回填

---

## 9. Phase 4 — 执行层

### 9.1 业务目标

让"已批准的决策"**自动落地到 Pulse 代码库**,完成"决策-改代码-验证"的完整闭环。

完成后:你在 TG 上点 ✅ 后 15 分钟内,Pulse 仓库会出现一个 PR 等你 review。合并后下次 Pulse 跑就用新规则。

### 9.2 模块清单

| 模块 | 类型 | 触发 |
|---|---|---|
| Craftsman | Claude Code 风格 Agent | ops_decisions 入库且 status='approved' |
| Git PR 流水线 | Python + GitHub API | Craftsman 完成后 |
| Verification 调度器 | systemd timer | 每日 + 每周 |
| Verification 回填器 | Analyst 子模块 | 每周日 Analyst 跑时 |

### 9.3 Craftsman 的边界(极重要)

Craftsman 是整个系统**风险最高的角色**——它要改代码。设计上严格限制它能做什么:

**能做**:
- ✅ 改 prompt 文件(.txt / .py 中的 prompt 字符串)
- ✅ 改矩阵 entry(content_matrix.py 中的数据结构)
- ✅ 改配置文件(.env / config.yaml,但不含 secret)
- ✅ 添加注释或文档说明
- ✅ 写单元测试

**不能做**:
- ❌ 改核心引擎代码(orchestrator / engine 主循环)
- ❌ 改数据库 schema
- ❌ 改部署配置(systemd / docker)
- ❌ 直接 push 到 main(必须走 PR)
- ❌ 合并 PR(必须人 review)
- ❌ 改 secret

实现:**白名单文件路径** + **PR-only 工作流**。

### 9.4 业务流

```
ops_decisions 写入 status='approved' (Phase 3 完成)
       │
       ▼ 触发(Phase 4 新增的 dispatcher)
┌─────────────────────────────────────────┐
│  Craftsman v0                            │
│                                          │
│   1. 读 ops_decisions 详情                │
│   2. 读 knowledge/code_index/ 相关文件     │
│   3. git clone Pulse 到 /tmp/work/        │
│   4. checkout -b auto/decision-{id}      │
│   5. 调 Claude(白名单约束的 prompt)       │
│      │                                   │
│      └─ Claude 产出:                     │
│         - diff(具体改动)                 │
│         - explanation(为什么这么改)       │
│         - test_plan(怎么验证)            │
│   6. 应用 diff 到工作树                   │
│   7. 跑 lint / 简单测试                   │
│   8. git commit + push                   │
│   9. GitHub API 创建 PR,链接 ops_decision │
│   10. 写 ops_decisions.after_state =      │
│       {pr_url, diff_summary, ...}         │
│   11. 通过 Facilitator 推 TG 通知用户     │
└──────────────────────────────────────────┘
```

### 9.5 Verification 回填(关键的复利机制)

每条决策入库时记录 `verification_plan`(如"3 周后看 CTR 是否回到 11%+")和 `verification_due_at`(到期日)。

```
每周日 Analyst 跑时(Phase 2 已有,Phase 4 加这个分支):

┌────────────────────────────────────────────┐
│  查 ops_decisions WHERE                     │
│    verification_due_at <= now()             │
│    AND verification_result IS NULL          │
└────────────┬───────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────┐
│  对每条到期决策:                              │
│    1. 找 verification_plan 描述的指标         │
│    2. 查 ops_metrics 该指标的历史            │
│    3. 对比"决策前 N 周"vs"决策后 N 周"      │
│    4. Claude 评估:符合预期 / 不符合预期        │
│    5. 写 verification_result                 │
│    6. 如果生效 + confidence 高 → 升级 playbook│
│    7. 在本周周报里报告:                       │
│       "本周完成 3 个决策的验证,2 个生效"      │
└────────────────────────────────────────────┘
```

**这是 L2"系统会变聪明"的物理机制**。每周自动回填一批决策,playbook 越来越厚,Analyst 的下次诊断有越来越多 evidence 可援引。

### 9.6 数据流

```
┌──────────────────┐
│ ops_decisions     │
│ status='approved' │
└────────┬─────────┘
         │ 触发
         ▼
┌────────────────────────────────────────────┐
│ Craftsman v0                                │
│                                             │
│  ┌──────────────┐  ┌──────────────────┐   │
│  │ 读 decision   │  │ 读 code_index     │   │
│  └──────┬───────┘  └────────┬─────────┘   │
│         └────┬─────────────┘                │
│              ▼                              │
│     ┌─────────────────────────┐             │
│     │ git clone Pulse to /tmp │             │
│     │ checkout -b auto/{id}   │             │
│     └────────┬────────────────┘             │
│              ▼                              │
│     ┌─────────────────────────┐             │
│     │ Claude(白名单约束)       │             │
│     │ → 产出 diff + 解释        │             │
│     └────────┬────────────────┘             │
│              ▼                              │
│     ┌─────────────────────────┐             │
│     │ 应用 diff + lint + push  │             │
│     └────────┬────────────────┘             │
│              ▼                              │
│     ┌─────────────────────────┐             │
│     │ GitHub API 创建 PR      │             │
│     └────────┬────────────────┘             │
└──────────────┼──────────────────────────────┘
               │
        ┌──────┴───────┐
        ▼              ▼
┌──────────────┐  ┌────────────────┐
│ Pulse repo    │  │ ops_decisions  │
│ PR 待 review  │  │ after_state 回填│
└──────────────┘  └────────────────┘
        │
        │ 用户在 GitHub review + merge
        ▼
┌──────────────────┐
│ Pulse main 更新   │
│ 下次 Pulse cron 跑 │
│ 用新规则           │
└──────────────────┘
        │
        │ 时间过去 N 周
        ▼
┌──────────────────────────────┐
│ Verification 回填(每周日)    │
│ Analyst 自动评估决策效果       │
│ 生效 → 升级 ops_playbook      │
└──────────────────────────────┘
```

### 9.7 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Craftsman 模型 | Claude Sonnet 4.5+ | 写代码质量决定 PR 通过率 |
| 工作目录 | /tmp/work/(临时,跑完即删) | 避免长期持有 Pulse 代码副本 |
| Git 工作流 | 必须 branch + PR | 物理隔离 main,不可绕过 |
| 白名单文件 | 显式列表(初期 ~10 个文件) | 严格起步,后期按需放宽 |
| 测试要求 | 至少 lint + import 检查 | 不要求完美测试,但不能让明显错误进 PR |
| Verification 验证 | 用 Claude 评估而非纯阈值 | 业务情境复杂,纯数值阈值容易误判 |

### 9.8 Phase 4 完成标志

- [ ] Craftsman 至少成功提过 5 个 PR,均被人 review 合并
- [ ] 白名单约束生效:有过尝试改禁止文件被拒绝的记录
- [ ] Verification 自动回填至少 3 轮(超过 8 周连续运行)
- [ ] 至少 1 条决策走完完整闭环(approved → PR merged → verified → 升级 playbook)
- [ ] 蓝图本节"实际产出"已回填

---

## 10. 跨 Phase 的横切关注点

这一节列**任何 Phase 都要遵守、不属于某个具体 Phase**的规则。

### 10.1 Secret 管理

- 所有 secret 在 `/opt/accelerator/.env`,权限 600,owner accelerator
- 代码中绝不硬编码
- `.env` 永不进 git
- 任何怀疑泄露的 secret 立即 rotate,无"立刻删除就好"的捷径

### 10.2 数据库迁移

- Schema 改动必须新建 `sql/00N_<描述>.sql`,不直接改 001
- 每次迁移在 schema_versions 表插入记录
- 迁移必须可逆(写 down migration)
- 部署前在 staging 数据库跑过

### 10.3 Run Log 约定

每个角色每次运行都必须:
1. 启动时 INSERT l2_run_log,status='running',拿 run_id
2. 完成时 UPDATE 设 status='ok'/'failed'/'partial'
3. 异常退出靠 systemd 标记 'failed'(脚本崩溃时由外部捕获)

### 10.4 模型调用规范

- Anthropic API key 只在调用时从 .env 加载,不传入子进程
- 每次调用记录:输入 token / 输出 token / 耗时(写到 l2_run_log.summary)
- 失败重试:最多 3 次,exponential backoff
- 月底汇总 token 消耗,写入 ops_metrics 一条 monthly_cost 记录

### 10.5 多 Tenant 预留

整个 Phase 0-4 都按"history 单 tenant"实现,但 schema 上保留 tenant_id 字段(默认 'history')。

未来加 A 象限号:
- 添加新 tenant_id='ai_quadrant'
- 各表数据自动隔离(WHERE tenant_id=...)
- knowledge/pulse/ → knowledge/{tenant_id}/

不在 Phase 0-4 主动做多 tenant 测试,但**任何 SQL 查询都不能漏写 tenant_id 过滤**。

### 10.6 文档维护节奏

- **本蓝图**:每完成一个 Phase 后回填"实际产出"和"踩到的坑"
- **CLAUDE.md**:每次发现"Claude Code 本该知道但没知道"的规则,加一条
- **PHASE_N_SPEC.md**:Phase N 启动前由 Claude(策略 LLM)基于本蓝图细化产出
- **ops_decisions**:每次决策实时写,不积压
- **本表**:每月 review 一次,看是否需要新加一类文档

### 10.7 失败处理哲学

| 失败类型 | 处理 |
|---|---|
| Pulse 偶尔抽风(API 超时) | 自动重试 3 次 |
| Pulse 持续不可达(>2 小时) | 告警 + 降级运行(用旧数据) |
| Anthropic API 失败 | 重试 + 降级到 Doubao(如果是 Analyst) |
| L2 自身 bug 导致角色崩溃 | systemd 自动重启 + 告警 |
| 数据库不可用 | 告警 + 全部角色暂停 |

**不要试图自动修复 bug**——崩溃后停下来等人介入,比"猜测修复继续运行"安全。

### 10.8 备份策略

- Postgres `pg_dump`:每周日 02:00 SGT,保留最近 4 周
- knowledge/pulse/:每月 1 号打包,保留最近 3 个月
- 备份目标:本机 + (Phase 4 后)推到 GitHub 私有仓库 backup/ 分支
- 关键 ops_decisions 同时手动导出到 markdown(冗余保险)

### 10.10 数据契约文档化

#### 决策

任何"L2 读 L1 数据"的代码,**必须先有 SCHEMA_NOTES.md 描述的数据契约**,才能写代码。

#### 文档位置

`/opt/accelerator/knowledge/pulse/SCHEMA_NOTES.md`

放在 knowledge 目录而非 docs 目录,因为它是 knowledge mirror 的一部分(人类决策版),区别于 Librarian v0 自动产出的 `schema/*.md`(机器友好版)。

#### 维护规则

- **Phase 1 阶段**:人类手动维护(每次 Pulse 改 schema 时)
- **Phase 2 阶段起**:Librarian v1 升级时,LLM 自动产出 `extracted/schema_alignment.md` 作为补充,SCHEMA_NOTES.md 仍保留作人类决策记录
- **任何 Pulse schema 演化**:必须更新 SCHEMA_NOTES.md 变更日志,且评估是否需要改 Connector / Watcher / Librarian
- **任何 SCHEMA_NOTES.md 之外的代码**(Connector、Watcher、Librarian),**必须在写代码前对齐文档**,而不是写代码后回填文档

#### 这条规则的边界

- ✅ 适用:任何 L2 读 L1 数据的代码
- ❌ 不适用:L2 自己内部数据(ops_decisions / ops_metrics 等)——那是 L2 自己的 schema,蓝图直接定义
- ❌ 不适用:Pulse 自己的代码(L2 不参与 Pulse 内部演进)

---

## 11. 踩坑记录与决策日志

> 这一节是项目活档案。任何一个"原本以为能行的事情失败了"或者"做了一个非显而易见的选择",都记在这里。
> 格式:日期 + 简短描述 + 教训。

### 2026-04-29 项目蓝图初版

- **背景**:经过 5 轮深度对话,确定 L2 系统的架构哲学和路线图
- **关键决策**:放弃双机协作,选单机部署(火山引擎柔佛 2C4G)
- **关键决策**:Pulse 是 Supabase 不是本机 Postgres,Connector 走 REST API
- **关键决策**:加入 Layer 2(Pulse 知识镜像)+ Librarian 角色,补 Phase 1 漏洞
- **教训**:Spec 里的"使用方本能疑问"经常比"架构师自检"更准——把它视为压力测试

2026-04-30 Phase 0 完成

完成情况:14 项 health_check 全部通过,SSH 隧道方案落地,l2_reader 权限闭合到位
关键决策:Step 4 走 SSH 隧道而非 Supabase REST API,因 Pulse 主数据在本机 PG 13.23
架构层升级:蓝图 §3.1 从"REST API"改为"SSH 隧道 + 本机 PG",影响 Phase 1 Connector 实现
协作素养观察:执行方在权限闭合(5.6.4)上做了 spec 之外的正确加固,标志着"使用方反向 review spec"机制开始生效——这是 L2 自我迭代能力的微观体现
文档体系完善:本次合并蓝图补丁时,识别到"具体拓扑图不该住蓝图",拆出 INFRASTRUCTURE.md,完善文档分辨率分层

### 2026-05-02 Phase 1 完成

- **完成情况**:21 项 health_check 全部通过(Phase 0 14 + Phase 1 7),Pulse Connector 4 单元测试全过,Librarian / Watcher / acc CLI / systemd timer 全链路跑通
- **关键产出**:`SCHEMA_NOTES.md` 数据契约 / Connector + dataclass / Librarian v0 / Watcher v0(写 ops_metrics)/ `acc` CLI(status/librarian/watcher/backfill)/ acc-{librarian,watcher}.{service,timer}
- **首次运行**:Librarian 12:47 SGT(手动) / Watcher 13:09 SGT(手动) / systemd 触发于 14:08-14:10
- **关键决策**:Phase 1 内 interactions 表不接入,推迟到 Phase 2/3 启动前再处理;改 SCHEMA_NOTES.md 之外的代码,必须先对齐数据契约(已写入 §10.10 横切关注点)
- **元层启示**:Phase 1 暴露的 SPEC 设计方法论缺陷(理想字段 vs 真实字段)和部署管道 Phase 0 时未立起的疏漏,直接催生 §10.10 数据契约规则和 §6.8.2 部署管道改进。**这正是 L2 自我迭代能力在元层的早期体现:做项目本身改进了"做项目的方法论"**
- **遗留小工**:补 `/opt/accelerator/deploy.sh`(idempotent rsync + systemd reload);改 `acc status` 的 partial 状态视图区分 deferred / degraded(详见 §6.8.4)

### 2026-04-30 Phase 1 Step 3 — Schema 重审

- **背景**:Step 3 真实连接 Pulse 触发 UndefinedColumn 错误,引发对 SPEC 的系统性 schema 重审
- **根因**:SPEC 设计方法论错误——把"L2 内部数据模型"和"L1→L2 接入规约"压在同一份代码里写
- **关键产出**:
  - `SCHEMA_NOTES.md`(权威数据契约文档)
  - Connector 修订版(对齐真实 schema)
  - 新增蓝图横切关注点 §10.10
- **元层启示**(由项目所有者主动诊断,值得记录):
  - 协作里"反向 review SPEC"机制开始成熟——使用方不只发现错,还能从错中提炼方法论问题
  - 这种诊断深度直接影响后续 Phase——Phase 2/3 SPEC 会预先包含 schema 对齐步骤
- **决策**:Phase 1 内 interactions 表不接入,推迟到 Phase 2/3 启动前再处理

---

## 12. 术语表

为了未来的人(包括 LLM)读这份文档时不困惑:

| 术语 | 含义 |
|---|---|
| **L1 / Pulse** | 内容生产执行系统,跑在 HK,本项目不在范围内 |
| **L2 / Accelerator** | 本项目,运营元层,跑在火山柔佛 |
| **L3 / 治理层** | 人的决策点,概念性,无具体系统 |
| **元运营 / Meta-Ops** | L2 的别称,强调"运营运营本身"的元层 |
| **数字员工 / 角色** | L2 内的功能位:Watcher/Librarian/Analyst/Facilitator/Craftsman |
| **Pulse Connector** | L2 内访问 Pulse 数据的统一接口(反腐败层) |
| **知识镜像** | knowledge/pulse/ 目录,L2 持有的 Pulse 派生知识 |
| **Reverse-ACL / 反腐败层** | DDD 概念,Pulse Connector 的角色 |
| **ops_decisions** | 运营决策日志表,L2 的"心脏" |
| **ops_metrics** | 指标快照表,L2 的"输入" |
| **ops_playbook** | 已验证策略表,L2 的"复利积累" |
| **l2_run_log** | L2 自身运行历史表 |
| **A 象限号** | 项目所有者计划开的第二个内容矩阵账号(一个人的 AI 公司) |
| **历史号** | Pulse 当前服务的内容矩阵,江盛.红如火 |
| **SCHEMA_NOTES.md** | Pulse → L2 数据契约文档,knowledge mirror 的人类决策版 |
| **angle** | article 的属性(每篇文章选一个角度),不是 topic 的属性 |
| **已知空通道** | Pulse schema 里已存在但当前所有行为空的字段(如 `publishes.metrics`)。Connector 正常拉取,L2 内部数据流保持完整,等待 L2 数据回流 worker(Phase 2+)填充 |
| **数据契约对齐** | 写"读外部系统"代码前,先 dump 真实 schema 并固化到 SCHEMA_NOTES.md 的工序 |

---

## 附:维护本文档的 meta 规则

> 这份文档自身也需要演化。但演化必须遵守:

1. **不删除历史决策的描述**——即使决策已被推翻。删除会让"为什么后来这么改"变得无法理解
2. **新加 Phase 时,目录前面预留位置**——不要把新 Phase 塞最后
3. **任何超过 50 字的"为什么"段落,考虑是否应该提炼成"设计原理 #N"加进 §2**
4. **每月 review 一次**:有没有 §11 踩坑记录揭示了 §2 哲学需要新增?有没有 §10 横切关注点其实只在某个 Phase 用到?

> 文档活着,才有用。

---

**END OF BLUEPRINT — 长期参考蓝图,慎改,常读**
