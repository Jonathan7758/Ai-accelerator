# Accelerator L2 — Phase 2 开发方案(Librarian v1 升级)

> 给 Claude Code 的开发任务说明书
> Phase 2 总体目标:**分析层** = Librarian 升级(本 Spec)+ Analyst v0(独立 Spec,后续起草)
> **本 Spec 仅覆盖 Librarian v1 升级**。Analyst v0 留单独 Spec(在 Librarian v1 验收后再写)。
> 完成后产出:`knowledge/pulse/{docs,code_index,extracted}/` 三块新源就位,且能在源变化时增量重生。

---

## 0. 必读前置

### 0.1 在动笔前先读这些

```
/opt/accelerator/CLAUDE.md                          — 项目恒定知识(自动加载)
/opt/accelerator/docs/PROJECT_BLUEPRINT.md          — 重点读 §7(Phase 2 设计)、§10.10(数据契约)
/opt/accelerator/docs/PHASE1_SPEC.md                — 复用其结构与已立的接口(Connector / RunLogger)
/opt/accelerator/knowledge/pulse/SCHEMA_NOTES.md    — 数据契约权威基线
```

### 0.2 Phase 1 已就绪状态(本 Spec 的起点)

- Pulse Connector + Librarian v0(schema 镜像)+ Watcher v0 + acc CLI + systemd timer 全部稳定运行
- `acc status` 已区分 deferred / degraded / failed(Phase 1 收尾增强,详见 BLUEPRINT §6.8.4)
- 21 项 health_check 全过
- 部署:GitHub → `/opt/accelerator-git/`(git pull)→ `cp` 同步到 `/opt/accelerator/`(deploy.sh **暂未补**,本 Spec 不做)

### 0.3 Phase 2 Librarian v1 的设计意图

```
┌──────────────────────────────────────────────────────────────┐
│  Librarian v1 = Librarian v0 + 三块新源                       │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  每日 06:00  Librarian v1                                      │
│    ├─ [v0 沿用] 拉 schema 元信息 → schema/                     │
│    ├─ [新] 拉 Pulse docs → docs/                               │
│    ├─ [新] 检测 Pulse 代码变化 → 调 Claude 重生 code_index/    │
│    ├─ [新] 检测 docs 变化 → 调 Claude 重生 extracted/          │
│    └─ 写一条 l2_run_log(沿用)                                  │
│                                                                │
│  关键性质:                                                     │
│    ① docs 全量同步(无需 LLM,便宜)                            │
│    ② code_index / extracted 增量重生(只对变化的源调 LLM)     │
│    ③ 任意一块失败 → run_log 标 [degraded] 部分,非整体 failed   │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

### 0.4 关键技术约束(继承 Phase 1,新增几条)

| 约束 | 来源 / 备注 |
|---|---|
| 沿用 Connector / RunLogger / common.db / acc CLI 接口,**不破坏向后兼容** | 否则 Watcher v0 也要改 |
| LLM 调用走 Anthropic SDK,不引入 LangChain | CLAUDE.md §4 |
| 每次 LLM 调用必须留痕(prompt / response / tokens / cost / model)到本地文件或 DB | 待定:落 DB 还是文件,见 §5 待定决策 1 |
| `code_index/` 粒度 = 文件级,不到函数级 | BLUEPRINT §7.6 |
| 增量检测必须**显式留 manifest**,不能依赖文件 mtime(scp / rsync 会重置 mtime) | Phase 1 教训 |

### 0.5 决定性偏好(已对齐,不要改)

- Phase 2 Librarian v1 仍每日 06:00 SGT(沿用 timer)
- LLM 默认模型 = Claude Sonnet(成本/质量平衡);需要更高质量再升 Opus
- 测试范围:增量检测 manifest 的纯函数部分强制单测;LLM 调用部分人审 + 抽样验证
- 失败处理沿用 Phase 1 模式:`mark_partial("[degraded] ...")` 单源失败不拖整体

---

## 1. Phase 2 Librarian v1 工作分解

按依赖顺序,拆 6 个 Step。**严格按顺序**,前一步验收通过才进下一步。

```
Step 1: Pulse 源接入(docs + code repo 在 L2 端如何可达)
Step 2: docs/ 全量同步(无 LLM 路径,先打通管道)
Step 3: 增量检测 manifest(纯函数,可单测)
Step 4: code_index/ LLM 加工(单文件 → markdown 导航图)
Step 5: extracted/ LLM 加工(多文档 → 业务概念提炼)
Step 6: 接入 Librarian v0 主流程 + acc status 反映新源 + 验收
```

每个 Step 一旦完成,跑该 Step 的"验收清单",通过才进下一步。

---

## 2. Step 详细

### Step 1: Pulse 源接入

**目标**:在 L2 端能稳定读到 Pulse 的 ① 文档目录 ② 代码仓库,且增量可识别。

**前置决策**(必须先确认,见 §5 待定决策 2):
- Pulse docs 物理位置 + 同步方式(rsync over SSH / git clone / GitHub raw)
- Pulse 代码仓库的 read-only 接入方式(L2 server 上 git clone / GitHub API)

**动作**:
1. 在 `/opt/accelerator/knowledge/pulse_src/`(新增,与 `knowledge/pulse/` 平级)持有 Pulse 源的本地镜像
2. 提供一个 `meta_ops/librarian/pulse_source.py` 模块,封装两个能力:
   - `sync_pulse_docs() -> dict`(返回:文件数、变化数、错误)
   - `sync_pulse_code() -> dict`(返回:同上 + 当前 commit SHA)
3. 凭据(SSH key / GitHub token)走 `.env`,不硬编码

**验收**:
- `python -m meta_ops.librarian.pulse_source` 能跑通,首次同步 = 全量 / 二次 = 增量
- `knowledge/pulse_src/docs/` 存在 N 份 markdown / `knowledge/pulse_src/code/` 是 git workdir
- 失败场景(Pulse 不可达)优雅返回 error,不抛异常

---

### Step 2: docs/ 全量同步路径

**目标**:把 `pulse_src/docs/` 整理(过滤、规范命名)后落到 `knowledge/pulse/docs/`,无 LLM 介入。

**动作**:
1. `meta_ops/librarian/sync_docs.py`:
   - 读 `pulse_src/docs/` 的 .md 文件
   - 过滤规则(待 §5 待定决策 3):是否要排除某些目录(如 internal-only/)
   - 写到 `knowledge/pulse/docs/`,保留相对路径
2. 写 `knowledge/pulse/docs/_meta/manifest.json`:每文件 path + sha256

**验收**:
- 首次 = 全部写入;二次 = 0 写入(内容未变)
- manifest.json 完整可读
- 不污染 `knowledge/pulse/schema/`(v0 的成果)

---

### Step 3: 增量检测 manifest

**目标**:统一的"哪些源变了"判断逻辑,给 Step 4/5 复用。

**动作**:
1. `meta_ops/librarian/change_detect.py`:
   - 函数 `detect_changes(source_root, manifest_path) -> ChangeSet`
   - `ChangeSet` 返回:added / modified / removed 三组 path
   - 用 sha256 比对,**不依赖 mtime**
2. 单测覆盖:首次(全 added)/ 增量(部分 modified)/ 删除 / 文件移位

**验收**:
- 4 个单测全过
- 在 `pulse_src/` 上跑,结果跟 `git diff --name-status HEAD~1` 一致(对代码源)

---

### Step 4: code_index/ LLM 加工

**目标**:对每个变化的关键 .py 文件,调 Claude 产出"修改导航图"(目的、对外接口、关键依赖、常见改动点)。

**动作**:
1. `meta_ops/librarian/llm_client.py`(新增,Phase 2 起首次引入 Anthropic SDK):
   - 单一入口 `call_claude(model, system, user, max_tokens) -> Response`
   - 自动留痕:prompt / response / tokens / 估算 cost,写到待定位置(§5 决策 1)
2. `meta_ops/librarian/build_code_index.py`:
   - 输入:文件路径 + 文件内容
   - 调用 Claude Sonnet,prompt 模板存 `prompts/code_index_v1.md`(版本化)
   - 输出 markdown 写到 `knowledge/pulse/code_index/<相对路径>.md`
3. 关键文件白名单(待 §5 决策 4):用户指定 5+ 个文件作 v1 起步
4. 增量逻辑:Step 3 报告 modified 的 .py 才重生

**验收**:
- 5 个文件全部产出 markdown,内容人工 review ≥ 7/10
- 同一文件二次跑,无变化时跳过 LLM(0 token 消耗)
- LLM 调用留痕完整(可追溯本次跑了哪些文件、花了多少 token)

---

### Step 5: extracted/ LLM 加工

**目标**:从多份 Pulse 文档提炼业务关键概念(matrix_v2_taxonomy / title_templates 等),供 Analyst 引用。

**动作**:
1. `meta_ops/librarian/build_extracted.py`:
   - 输入:多份相关 docs(由人工 mapping 配置:每个 extracted 主题 → 哪几份源 doc)
   - 调 Claude,prompt 模板存 `prompts/extracted_<topic>.md`
   - 输出到 `knowledge/pulse/extracted/<topic>.md`
2. v1 起步至少 4 个主题(对齐 BLUEPRINT §7.7)— 待 §5 决策 4 列具体主题
3. 增量逻辑:任一源 doc 变 → 重生该 topic

**验收**:
- 4 个 extracted 主题文件全部产出,内容人工 review ≥ 7/10
- mapping 配置(`prompts/extracted_topics.yaml` 或类似)清晰可改
- 一份 source doc 变 → 只重生关联 topic,不全量重跑

---

### Step 6: 接入主流程 + status 反映 + 验收

**目标**:Librarian v1 替换 v0 在 systemd timer 中的位置;`acc status` 显示新源的 freshness。

**动作**:
1. `meta_ops/librarian/v1.py`:编排 Step 1-5,沿用 RunLogger
2. systemd unit `acc-librarian.service` 切到 `meta_ops.librarian.v1`(timer 不变)
3. `acc status` 增加显示:
   - `docs/` 文件数 + 上次同步时间
   - `code_index/` 文件数 + 上次重生时间
   - `extracted/` 主题数 + 上次重生时间
4. 健康检查 `health_check.py` 增加 4 项:
   - `pulse_src/` 可达
   - `docs/manifest.json` 存在且新鲜(<36h)
   - `code_index/` 至少 5 个文件
   - `extracted/` 至少 4 个文件

**验收**:
- 一次手动跑 `acc librarian run` 全流程通过
- 新增 4 项 health_check 通过(总数 21 → 25)
- `acc status` 多出 3 行新源信息显示
- 老的 `acc librarian run` 调用入口仍兼容(不破坏 Watcher 的 freshness 检查逻辑)

---

## 3. Phase 2 Librarian v1 完成标志

- [ ] BLUEPRINT §7.7 中 Librarian v1 相关 3 项全部满足:
  - [ ] 自动检测 Pulse 文档/代码变化并触发 LLM 加工
  - [ ] `code_index/` 至少 5 个关键文件
  - [ ] `extracted/` 至少 4 份核心业务概念
- [ ] 增量逻辑可验证:同源 0 变化 = 0 LLM 调用
- [ ] 单 Step 失败不拖整体(`[degraded]` 标记 + 其他 Step 继续)
- [ ] 25/25 health_check 通过
- [ ] `acc status` 正确显示新源

---

## 4. 不在本 Spec 范围(留给后续)

| 项 | 留给 |
|---|---|
| Analyst v0(消费 Librarian v1 的产出,产周报) | 独立 PHASE2_ANALYST_SPEC.md(本 Spec 验收后起草) |
| Facilitator(TG Bot)+ 决策状态机 | Phase 3 |
| Craftsman(改 Pulse 代码 + PR) | Phase 4 |
| `deploy.sh` idempotent rsync | 单独小工(BLUEPRINT §11 遗留) |
| `interactions` 表接入 | 待 SCHEMA_NOTES §5.3 对齐后,Phase 2/3 阶段决定 |

---

## 5. 待定决策(开干前必须先答)

> 以下 4 条不解决,Step 1-5 写不下去。请 Jonathan 拍板。

### 决策 1:LLM 调用留痕落哪儿?

| 选项 | 利弊 |
|---|---|
| A. 新增 DB 表 `l2_llm_calls`(走 migration 003) | 可 SQL 查、跟 run_log 关联;但要写 schema |
| B. 落 `knowledge/_meta/llm_calls.jsonl`(纯文件) | 零 schema 改动;但日后查询要 grep/jq |
| C. 同时落两边(DB 摘要 + 文件全量) | 最完整;但工作量翻倍 |

**推荐**:A(走 migration 003)。Phase 2 起 LLM 是核心成本项,DB 查询能力会一直需要。

### 决策 2:Pulse 源接入方式

| Pulse 资源 | 选项 |
|---|---|
| 文档(.md) | (a) `rsync over SSH from pulse-hk` / (b) Pulse 仓库已在 GitHub → `git clone` / (c) GitHub raw API |
| 代码 (.py) | 同上,但代码仓库一定在 git 管理,推荐 (b) |

**需要 Jonathan 提供**:Pulse 文档实际位于哪里(HK 服务器路径 or Git 仓库路径)+ Pulse 代码仓库 URL + 是否已有 read-only 凭据。

### 决策 3:docs/ 同步过滤规则

是否要排除某些目录?(internal-only / draft / archive)— 需要列一份白名单或黑名单。

### 决策 4:code_index 与 extracted 的 v1 起步范围

- `code_index/` 第一批 5+ 个关键 .py 文件:**Jonathan 列名单**(选业务最核心、最常改的)
- `extracted/` 第一批 4+ 个业务概念主题:**Jonathan 列名单**(BLUEPRINT 提到 matrix_v2_taxonomy / title_templates,需补齐到 4 个)

---

## 6. 工作量与排期估计

> 不给具体小时数(项目惯例:不做时间预测)。

依赖关系:Step 1 → 2 → 3 → 4 → 5 → 6,前面不通后面跑不动。Step 4/5 是 LLM 工作量大头(每加工一份要跑一遍 prompt 调试)。

---

## 7. 风险登记

| 风险 | 缓解 |
|---|---|
| Pulse 文档质量参差,extracted 提炼出错 | v1 阶段每份 extracted 必须人工 review;低分的不发布 |
| LLM 成本失控(每天全量重做) | 增量检测必须先于 LLM 调用,本 Spec 把它列为独立 Step 3,先单测过再接 LLM |
| Pulse 仓库结构变动(目录重命名) | manifest 用相对路径 sha256;变动时 ChangeSet 会显示大量 added/removed,主流程检测到异常规模时报警(Step 6 加阈值) |
| Anthropic SDK 首次接入埋雷 | Step 4 把 SDK 调用单独封装,先用 1 个文件试通,再扩到 5 个 |

---

> **本 Spec 状态**:v0-draft (2026-05-02)。待 Jonathan 答复 §5 决策 1-4 后,定稿为 v1,正式开干 Step 1。
