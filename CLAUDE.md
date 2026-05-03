# CLAUDE.md — Accelerator L2 项目常驻知识

> 本文件是 Accelerator 项目的"恒定上下文"。每次 Claude Code 会话启动时被自动读取。
> 它**不**包含具体任务说明(那些在 PHASE_*.md 或单次 prompt 里)。
> 它**只**包含"无论做什么任务都应该知道的项目事实和边界"。

---

## 1. 项目身份

**项目名**:Accelerator(运营加速器)
**英文代号**:Accelerator-L2(在文档里也叫 L2 系统)
**对应业务**:为内容运营提供"自我迭代能力"的元运营层系统

**它不是什么**:
- ❌ 不是 Pulse 的扩展或子模块
- ❌ 不是历史号专属系统  
- ❌ 不是内容生成工具

**它是什么**:
- ✅ 一个独立运行的服务者系统
- ✅ Pulse 是它的第一个客户;未来还会有别的客户
- ✅ 核心交付物:可被复用的"运营改进自动化"能力

**这个定位影响所有设计选择**。当你不确定某个改动是否合理,问自己:"这个改动让 Accelerator 更像独立产品,还是更像 Pulse 的子模块?"前者优先。

---

## 2. 与 Pulse 的关系

| 维度 | 规则 |
|---|---|
| 数据流向 | Accelerator 只读 Pulse,**永远不写** |
| 代码改动 | Accelerator 通过 Git PR 提议改 Pulse 代码,**不直接 SSH 改** |
| 部署独立性 | Pulse 挂了 Accelerator 不挂(只是没新数据);Accelerator 挂了 Pulse 不受影响 |
| 命名 | Accelerator 的任何文件/目录/服务,**不要带 pulse 前缀** |

---

## 3. 服务器和环境

**主服务器**:火山引擎柔佛(Johor Bahru),2C4G,Ubuntu 22.04
- SSH host alias: `accelerator-jb`
- 时区:Asia/Singapore (SGT, UTC+8)
- 项目根:`/opt/accelerator/`
- 应用用户:`accelerator`(不要用 root 跑应用)
- 日志根:`/var/log/accelerator/`

**外部连接**:
- HK Pulse:`pulse-hk` (43.99.0.100),用 Supabase REST API 读数据(不是直连 Postgres)
- Anthropic API:从柔佛节点直连,**不需要 VPN**(这是选这个节点的关键原因之一)
- Telegram API:直连
- GitHub API:直连

---

## 4. 技术栈选择

| 层 | 选择 | 不选什么 |
|---|---|---|
| 语言 | Python 3.11+ | 不用 Node.js / Go(单语言简化) |
| 数据库 | 本机 PostgreSQL 16 | 不用 Supabase(L1 才用) |
| 调度 | systemd timer | 不用 cron(可观测性差) |
| Agent 框架 | 直接调 Anthropic SDK | 不引入 LangChain / AutoGen 等(隐性复杂度高) |
| TG Bot | python-telegram-bot | 跟 Pulse 一致 |
| 配置 | `.env` + python-dotenv | 不用 YAML / TOML(单一格式) |

**坚持"少即是多"**:除非有明确理由,不引入新框架/库/工具。

---

## 5. 数据库 Schema(核心三张表,不要随便改)

```
ops_decisions   — 运营决策日志("我们改了什么、为什么、怎么验证")
ops_metrics     — 指标快照("某对象某时点的 KPI")
ops_playbook    — 已验证策略("沉淀下来的 known-good 做法")
```

详细字段定义见 `/opt/accelerator/sql/001_init_schema.sql`,**任何 schema 改动必须**:

1. 写新的 migration 文件 `sql/00N_<描述>.sql`,**不要直接改 001**
2. 在 `schema_versions` 表插入对应记录
3. 提交前在 staging 数据库验证迁移可逆

---

## 6. 数字员工角色边界(L2 核心架构)

L2 由 4 个角色构成,**职责不能越界**:

| 角色 | 能做 | 不能做 |
|---|---|---|
| **Watcher** | 拉数据、写 ops_metrics | 不分析、不建议、不改任何代码 |
| **Analyst** | 读 metrics 和 decisions、产出 markdown 报告 | 不直接落库 ops_decisions(必须人审批) |
| **Craftsman** | 接收已 approved 的 decision、改代码、提 PR | 不自主决定改什么、不能合并 PR、不动 main |
| **Facilitator** | TG 消息收发、维护决策状态机 | 不做任何业务判断 |

**人在哪些点必须介入**(治理点):
- 审批 Analyst 的建议 → 落 ops_decisions
- Review Craftsman 的 PR → 合 main
- 任何架构级改动(改 Schema、加新角色、改服务器)

---

## 7. Secret 管理规则

- 所有 secret 写在 `/opt/accelerator/.env`(权限 600,owner accelerator)
- **绝不**在代码里硬编码任何密码、token、API key
- **绝不**把 .env 进 git
- 调用外部 API 前,显式从 .env 加载,不要依赖环境变量"碰巧设置了"
- 如果某个 secret 不在 .env 里,**停下来问用户**,不要从其他位置(比如 ~/.bashrc)猜

---

## 8. 协作行为约定(给 Claude Code)

### 8.1 必须遵守

- **顺序执行**:有明确步骤的任务,按顺序跑,不并行、不跳步
- **每步验证**:每个步骤结束跑该步骤的"验证标准",通过才进下一步
- **遇错停**:意外错误立即停止报告,**不要尝试"绕过"或"猜测修复"**
- **边界严守**:Spec 没说要做的事不要做,即使你觉得"顺手做了更好"
- **优先问而不是猜**:Secret、版本、用户偏好等,不确定就问

### 8.2 禁止

- ❌ 自主安装 Spec 没列出的软件包(Docker、nginx、监控工具等)
- ❌ 自主创建 Spec 没要求的目录或文件
- ❌ 跨 Phase 行动(Phase 0 期间不要碰 Phase 1 的事)
- ❌ 直接修改 Pulse 仓库的 main 分支
- ❌ 把任何 secret 写进会被 commit 的文件

### 8.3 报告习惯

每个步骤结束后告诉我:
- 跑了哪些命令(贴关键命令,不必全贴)
- 验证标准是否通过(贴关键输出)
- 是否有 unexpected 情况
- 是否准备好进下一步

---

## 9. 项目目录约定

```
/opt/accelerator/
├── .env                          # secret(600 权限,不进 git)
├── .gitignore                    # 必须排除 .env、*.log、__pycache__
├── sql/
│   ├── 001_init_schema.sql       # Phase 0 创建
│   └── 00N_*.sql                 # 后续 migration
├── scripts/
│   └── health_check.py           # Phase 0 创建,后续扩展
├── meta_ops/                     # Phase 1 起,主应用代码
│   ├── watcher/                  # Phase 1
│   ├── analyst/                  # Phase 2
│   ├── craftsman/                # Phase 4
│   └── facilitator/              # Phase 3
└── docs/
    ├── CLAUDE.md                 # 本文件
    ├── PHASE0_SPEC.md            # Phase 0 任务说明
    └── PHASE_*.md                # 后续 Phase 任务
```

新加目录前,先确认它符合上面结构,或者跟我商量加新顶层目录。

---

## 10. 当前 Phase 状态

> 这一节会随项目演进更新。每次完成一个 Phase,更新这里。

- **Phase 0**:基础设施搭建 — ✅ **完成**(2026-04-30,14 项 health_check 通过)
- **Phase 1**:数据观察层(Connector + Librarian v0 + Watcher v0 + acc CLI + systemd timer)— ✅ **完成**(2026-05-02,21 项 health_check 通过,详见 PROJECT_BLUEPRINT.md §6.7)
- **Phase 2 (Librarian v1)**:分析层 — Librarian 升级到 v1 ✅ **完成**(2026-05-03,26 项 health_check 通过)
  - Step 1-6 全过:Pulse 源接入(HK rsync + GitHub deploy key)/ docs 全量同步 / 增量检测 manifest(纯函数 9 单测过)/ code_index 6 文件 LLM 加工 / extracted 4 主题 LLM 加工 / v1 主流程 + acc status + health_check
  - 首跑总成本 $1.47(后续增量为 0 = $0)
  - 双轨 LLM 留痕(`l2_llm_calls` 表 + `knowledge/_meta/llm_calls.jsonl`)
- **Phase 2 (Analyst v0)**:分析层 — Analyst 部分 — 待启动(独立 spec)
- **Phase 3**:协作层(Facilitator + 决策状态机)— 待启动
- **Phase 4**:执行层(Craftsman + git PR 流水线)— 待启动

每个 Phase 的具体内容见对应的 PHASE_N_SPEC.md。

---

## 11. 一些会让你想"绕过"但绝对不要绕过的点

这些是**经验留下来的硬规则**,看起来繁琐但有原因:

1. **Postgres schema 改动必须走 migration 文件**——直接 ALTER 在生产上跑,以后没人知道为什么字段长这样
2. **L2 写 L1 在数据库层就被禁止了(只读账号)**——不是"代码里小心"
3. **Craftsman 不能合并自己的 PR**——人类 review 是治理边界,不是流程仪式
4. **任何 cron/timer 配错时区的事故,8 成发生在以为 UTC 没问题的时候**——这台机器全栈 SGT,确认每个 timer 都是 SGT
5. **".env 不进 git" 这条规则被违反过一次,就要重新生成所有 secret**——没有"我立刻删掉就行"的捷径

---

## 12. 联系和升级

- 用户(项目所有者):Jonathan(江盛)
- 主要协作方式:Telegram + Claude Code
- 决策升级:任何架构级、Schema 级、跨 Phase 范围级的改动,都先问用户

---

> 本文件随项目演进更新。每次发现"Claude Code 本该知道但没知道"的事,就回来加一条。
> 不要为了"完整"而堆规则——每条规则都该有真实的失败案例支持。
