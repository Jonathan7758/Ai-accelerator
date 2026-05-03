# Accelerator L2 — Phase 2 Analyst v0 开发方案

> 给 Claude Code 的开发任务说明书
> 本 Spec 紧接 PHASE2_SPEC.md(Librarian v1)之后,完成 Phase 2 整个分析层。
> 完成后产出:每周日 20:00 SGT 自动跑一次,在 `reports/YYYYWW.md` 落一份诊断周报草稿;`acc analyst run` 可手动触发。

---

## 0. 必读前置

### 0.1 在动笔前先读这些

```
/opt/accelerator/CLAUDE.md                          — 项目恒定知识(自动加载)
/opt/accelerator/docs/PROJECT_BLUEPRINT.md          — 重点读 §7(Phase 2 整体)、§8(Phase 3 状态机预告)
/opt/accelerator/docs/PHASE2_SPEC.md                — Librarian v1 spec,本 spec 复用其凭据/双轨留痕设计
/opt/accelerator/sql/001_init_schema.sql            — ops_decisions / ops_metrics / ops_playbook 字段
/opt/accelerator/knowledge/pulse/extracted/         — Analyst 主要消费的 4 份业务概念
```

### 0.2 上游已就绪(本 Spec 的起点)

- Librarian v1 收官,26/26 health_check 通过
- `knowledge/pulse/{schema,docs,code_index,extracted}/` 全部新鲜,每日 06:00 SGT 自动同步
- LLM 双轨留痕通道就绪(`meta_ops.librarian.llm_client.call_claude` + DB `l2_llm_calls` + jsonl)
- ops_metrics 已有数据(Watcher v0 每日 23:00 SGT 跑,目前累计 ~13 行,后续每天 +5-10 行)
- ops_decisions / ops_playbook 表存在但**Phase 2 期间空**(需要 Phase 3 Facilitator 才有手动入库流程)

### 0.3 Analyst v0 的设计意图

```
┌────────────────────────────────────────────────────────────────┐
│  Analyst v0 = "看数据 + 看业务知识 → 写运营周报草稿"             │
├────────────────────────────────────────────────────────────────┤
│                                                                  │
│  每周日 20:00 SGT  Analyst v0                                    │
│    ├─ 健康检查:Librarian 新鲜?Watcher 过去 7 天有几次成功?      │
│    │   └─ 数据稀疏 → 降级运行(产报告但坦诚标注"数据不足")       │
│    ├─ 函数化组装上下文:                                           │
│    │   ├─ ops_metrics(本周 + 过去 4 周聚合)                     │
│    │   ├─ ops_decisions(过去 4 周,Phase 2 期间通常 0 条)        │
│    │   ├─ ops_playbook(全部 active)                             │
│    │   ├─ knowledge/pulse/extracted/(4 份业务概念,sonnet 上下文)│
│    │   └─ 上周报告(若存在,作为 Continuity 参考)                 │
│    ├─ 调 Claude Sonnet 4.6,模板 prompt 产出周报 markdown         │
│    ├─ 写 reports/YYYYWW.md                                       │
│    └─ 写 l2_run_log + l2_llm_calls(双轨)                       │
│                                                                  │
│  关键铁律:                                                       │
│    ① **不直接落 ops_decisions** — 周报里给候选决策建议,人审批     │
│    ② **不连续追问 Pulse 改源代码** — 这是 Craftsman (Phase 4)    │
│    ③ **数据不足时如实标注** — 不发明数字,不臆测趋势               │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

### 0.4 关键技术约束

| 约束 | 来源 / 备注 |
|---|---|
| 沿用 Librarian v1 的 `llm_client.call_claude()`(双轨留痕已在) | 不重复造轮子 |
| 上下文组装是**纯函数**,LLM 调用是单一入口 | "数据筛选用代码,理解和写作用 Agent" — BLUEPRINT §7.6 |
| 默认模型 Claude Sonnet 4.6(跟 Librarian 一致) | spec PHASE2_SPEC.md §0.5 已对齐 |
| 报告路径 `reports/YYYYWW.md`,ISO week 编号 | BLUEPRINT §7.4 |
| 触发节奏:每周日 20:00 SGT(systemd timer)+ `acc analyst run` 按需 | BLUEPRINT §7.2 |
| 沿用 RunLogger,kind='analyst';partial 标 [degraded] / [deferred] 前缀 | 跟 Phase 1 收尾增强一致 |

### 0.5 决定性偏好(已对齐,不要改)

- Analyst 模型 = Sonnet 4.6(诊断质量决定 L2 整体上限,不省;但也不抢 Opus)
- 周报输出 = markdown 草稿,绝不直接落 ops_decisions
- 上下文组装走纯函数,可单测;LLM 调用部分人审 + 抽样验证(沿用 Librarian v1 的 prompt 调试节奏)
- Watcher 缺数据时降级运行(发报告 + 告警),不直接 fail

---

## 1. Phase 2 Analyst v0 工作分解

按依赖顺序,拆 7 个 Step。**严格按顺序**,前一步验收通过才进下一步。

```
Step 1: 上下文组装(纯函数,可单测)
Step 2: prompt 模板 + Analyst 角色定义(prompts/analyst_v0_weekly.md)
Step 3: v0.py 主流程(编排 Step 1+2,沿用 RunLogger + llm_client)
Step 4: 报告输出 + 文件命名(reports/YYYYWW.md,index.json)
Step 5: acc CLI(`acc analyst run` / `acc analyst latest` + acc status 加一行)
Step 6: systemd timer(acc-analyst.service + .timer,Sun 20:00 SGT)
Step 7: health_check 加 N 项 + 验收(首次跑 + 4 周连续 ≥ 7/10)
```

每个 Step 一旦完成,跑该 Step 的"验收清单",通过才进下一步。

---

## 2. Step 详细

### Step 1: 上下文组装

**目标**:把"分析需要的数据"从 DB + filesystem 抽出来,组成确定性结构,给 Step 3 的 LLM 调用使用。**纯函数**,易单测。

**动作**:

1. 新模块 `meta_ops/analyst/context.py`,提供函数:
   - `gather_metrics(local_db, weeks_back=4) -> dict`
     - 返回:`{this_week: {avg_views, avg_read_rate, ...}, prev_weeks: [...], n_articles, n_topics, raw_rows: [...]}`
     - 按 ISO week 分桶
   - `gather_decisions(local_db, weeks_back=4) -> list[dict]`
     - 返回:`[{id, decided_at, decision_type, subject, rationale, status}, ...]`
     - Phase 2 期间通常返回 `[]`
   - `gather_playbook(local_db) -> list[dict]`
     - 仅 status='active' 且 confidence ≥ 0.5
   - `load_extracted_topics() -> dict[topic_name, markdown_content]`
     - 读 `knowledge/pulse/extracted/*.md`,返回 dict
   - `gather_run_log_health(local_db, days=7) -> dict`
     - 返回 `{watcher: {ok: 5, deferred: 1, failed: 0}, librarian: {...}, data_sparse: bool}`
     - `data_sparse=True` 当 watcher ok < 5(过去 7 天少于 5 天成功)
   - `load_previous_report(reports_root, current_week_iso) -> str | None`
     - 读上一周报告作为 continuity 参考

2. 提供 `assemble_context(local_db) -> dict`,把上面所有合并成单一 dict,带元数据(`generated_at`、`week_iso`)

**验收**:
- `meta_ops/analyst/tests/test_context.py` 至少 6 个单测:
  - gather_metrics 0 行 / 跨周聚合 / 缺 metrics 字段降级
  - gather_decisions 0 行 / 含历史
  - gather_run_log_health data_sparse 判断
- `python -m meta_ops.analyst.context` 入口跑通(打印 assembled context 的 JSON 摘要,不走 LLM)

---

### Step 2: prompt 模板 + Analyst 角色定义

**目标**:写一份 stable system prompt 模板,定义 Analyst 角色、输出格式、铁律。

**动作**:

1. `prompts/analyst_v0_weekly.md`(system prompt)
2. 模板覆盖:
   - 角色定义(L2 Analyst,产周报草稿,绝不写 ops_decisions)
   - 输出格式(固定章节:摘要 / 信号识别 / 候选决策 / 验证回填)
   - 候选决策的"必填字段"(decision_type / subject / rationale / verification_plan / risk)
   - 数据稀疏时的降级写法("数据不足以支持 X 结论"硬性表述)
   - 写作风格(中文、量化、不发明、不夸大)

**验收**:
- 模板写完人工通读一遍,没有 placeholder 残留
- Step 3 跑一次后输出符合模板章节结构

---

### Step 3: Analyst v0 主流程

**目标**:编排 Step 1(context)+ Step 2(prompt)+ LLM 调用 + 写报告。

**动作**:

1. `meta_ops/analyst/v0.py`
2. 入口 `run_analyst_v0(week_iso: str | None = None) -> dict`:
   - 用 RunLogger(kind='analyst')包整体
   - 调 `assemble_context()` → user message
   - 加载 `prompts/analyst_v0_weekly.md` → system
   - 调 `meta_ops.librarian.llm_client.call_claude(kind="analyst", target_path=week_iso, ...)`
     - max_tokens=16000(报告通常 3-6K output)
   - 输出写 `reports/<week_iso>.md`
   - 失败处理:
     - 数据稀疏 → mark_partial("[deferred] data_sparse: ...")
     - LLM 失败 → 写 stub 报告(描述失败原因)+ mark_partial("[degraded] llm_failed: ...")
3. 重复保护:同 week_iso 已有报告 → 输出到 `<week_iso>_v2.md`(给手动覆写留余地)

**验收**:
- `python -m meta_ops.analyst.v0` 首次跑通,产出 `reports/2026WNN.md` 一份
- l2_run_log 写一行 kind='analyst' status='ok'(或 partial 含原因)
- l2_llm_calls 写一行 kind='analyst'

---

### Step 4: 报告输出与索引

**目标**:reports/ 目录有可发现的索引,便于 CLI 和将来 Phase 3 周报推送器消费。

**动作**:

1. `reports/_meta/index.json` 维护所有报告的清单:
   ```json
   {
     "version": 1,
     "updated_at": "...",
     "reports": [
       {"week": "2026W18", "path": "2026W18.md", "generated_at": "...", "size_bytes": 12345, "status": "ok"},
       ...
     ]
   }
   ```
2. v0.py 在写报告后追加/更新 index.json 的对应条目

**验收**:
- 跑两周(2 个 week_iso)后,index.json 有 2 条记录,顺序正确(按 week 排序)
- index.json 损坏 / 缺失时,Step 3 仍能正常跑(自动重建)

---

### Step 5: acc CLI 扩展

**动作**:

1. `acc analyst run [--week YYYY-WW]`(默认本周;指定 week 可重跑/补跑)
2. `acc analyst latest [--print]`(打印最新报告路径;`--print` 直接 cat 内容)
3. `acc status` 加一行:
   - `Latest weekly report: 2026W18.md (3.2 days ago)`
   - 4 周内无报告 → ⚠️ 标注

**验收**:
- 上面三个命令在服务器上手测通过
- `acc status` 输出多了一行 `Latest weekly report:`

---

### Step 6: systemd timer

**动作**:

1. `/etc/systemd/system/acc-analyst.service`
   - ExecStart `/opt/accelerator/.venv/bin/python -m meta_ops.analyst.v0`
   - User=accelerator, WorkingDirectory=/opt/accelerator
   - TimeoutStartSec=900(LLM 长上下文 + 思考可能 5-8 分钟)
2. `/etc/systemd/system/acc-analyst.timer`
   - `OnCalendar=Sun *-*-* 20:00:00`
   - `Unit=acc-analyst.service`
   - `Persistent=true`
3. systemctl enable + start

**验收**:
- `systemctl is-enabled acc-analyst.timer` → enabled
- `systemctl list-timers | grep analyst` 显示下次触发时间是 Sun 20:00 SGT
- 手动 `systemctl start acc-analyst.service` 跑通

---

### Step 7: health_check 加项 + 验收

**动作**:

1. health_check.py 加 N 项(待定 §5 决策 5,候选):
   - `acc-analyst.timer enabled`
   - `reports/ directory exists`
   - `reports has at least 1 file`(首次跑后)
   - `analyst_v0_weekly prompt exists`
2. 验收清单(完整):
   - [ ] Step 1-6 全过
   - [ ] `python -m meta_ops.analyst.v0` 一次跑通
   - [ ] 周报内容人工评分 ≥ 7/10
   - [ ] health_check 总数 ≥ 30(26 + 至少 4 个新项)
   - [ ] systemd timer 下周日实际跑出一份新报告

**验收(超出 v0 的"4 周连续 ≥ 7/10"留作 follow-up)**:
- BLUEPRINT §7.7 提到"连续 4 周自动产出周报,人工评分 ≥ 7/10",但 4 周等待是项目级里程碑,不是单 spec 验收门槛
- v0 spec 验收门槛:**首跑通过 + 7/10 + timer enable + health_check 加分项过**

---

## 3. Phase 2 Analyst v0 完成标志

- [ ] 7 个 Step 全过
- [ ] `acc analyst run` 手动可跑
- [ ] systemd `acc-analyst.timer` enabled,Sun 20:00 SGT 自动触发
- [ ] 至少一份 `reports/YYYYWW.md` 产出 + 人工评分 ≥ 7/10
- [ ] health_check 总数从 26 升到 ≥ 30
- [ ] 双轨留痕完整(l2_llm_calls 加 ≥1 行 kind='analyst')

---

## 4. 不在本 Spec 范围(留给后续)

| 项 | 留给 |
|---|---|
| 周报推送器(解析报告 + 推 TG) | Phase 3(BLUEPRINT §8) |
| Facilitator 决策状态机(approved → ops_decisions) | Phase 3 |
| Verification 回填(Analyst 子模块,验证决策效果) | Phase 4(BLUEPRINT §9.2) |
| `acc knowledge query "X1 主线是什么"`(知识查询 CLI) | 待 §5 决策 3 拍板:并入本 spec 还是独立 |
| Craftsman 改 Pulse 代码 | Phase 4 |

---

## 5. 待定决策(开干前必须先答)

> 以下 5 条不解决,Step 1-3 写不下去。请 Jonathan 拍板。

### 决策 1:时间窗口 = 本周 + 过去 4 周?

| 选项 | 利弊 |
|---|---|
| A. 仅本周(简单) | v0 起步快,但缺趋势,周报内容贫 |
| B. 本周 + 过去 4 周(BLUEPRINT 推荐) | 有趋势,Sonnet 上下文绰绰够;但 Phase 2 期间数据稀疏(每周 ~50 行),"过去 4 周" 也只 ~200 行 |
| C. 本周 + 过去 4 周 + 上周报告作 continuity 参考 | B 的基础上加"接上周关注的指标",连续性强 |

**推荐**:**C**。报告之间有 continuity,运营看着不像每周从零开始。Sonnet 4.6 上下文 1M tokens,完全够。

### 决策 2:数据稀疏(Watcher 不全)的降级策略

Phase 1 ops_metrics 才 13 行,Phase 2 期间预计 ~50/周。Watcher 偶尔 deferred(已知 by-design)。Analyst 第一次跑很可能遇到"过去 4 周一共 30 行"的稀疏场景。

| 选项 | 行为 |
|---|---|
| A. 跳过分析,产出 stub 报告"等数据" | 防止 LLM 编数字,但报告价值低 |
| B. 仍调 LLM,在 prompt 里强制要求"如实标注数据稀疏",报告里写诚实结论 | 锻炼 prompt 的纪律性,首跑就能用 |
| C. 计算 metrics 行数,< 阈值(如 50 行)→ A,≥ 阈值 → B | 折中 |

**推荐**:**B**。系统第一性原则:Analyst 是"读数据 + 业务知识 + 写诊断",数据少时也能产出"目前数据不足以下结论,但根据 extracted/ 业务文档,以下假设值得跟进"这样的草稿。stub 报告等于"不工作",失去练手机会。

### 决策 3:`acc knowledge query` 是否进 v0?

BLUEPRINT §7.7 列为 Phase 2 完成标志的一项,但跟周报是两个 use case(交互查询 vs 周期诊断)。

| 选项 | 利弊 |
|---|---|
| A. 进本 spec(加 Step 8) | 一次完成 BLUEPRINT §7.7 全清单 |
| B. 单独 spec(`PHASE2_KNOWLEDGE_QUERY_SPEC.md`) | Spec 不臃肿,各自验收门槛清晰 |
| C. 推迟到 Phase 3(配 Facilitator 一起做,因 TG 也是交互入口) | Phase 2 收得更紧 |

**推荐**:**B**。Analyst 周报和知识查询有不同的工程关注点(批处理 vs 交互延迟、长上下文 vs RAG-style 切片);分开 spec,首先收 Analyst v0,再起草 knowledge query。

### 决策 4:候选决策的字段必填性

每条决策建议(报告里"候选决策"小节的每一条)需要包含哪些字段?对齐 ops_decisions 表的话:

| 字段 | 必填 / 可选 | 说明 |
|---|---|---|
| `decision_type` | 必填 | enum: prompt_change / matrix_update / workflow_tweak / strategy_pivot / other |
| `subject` | 必填 | 受影响对象,如 `title_template_T1` |
| `rationale` | 必填 | 为什么这么改,带数据/业务知识引用 |
| `verification_plan` | 必填 | 怎么验证生效(具体指标 + 时间窗) |
| `risk` | 必填 | 改坏了会影响什么 |
| `evidence` | 必填 | 引用了哪些 ops_metrics 行 / extracted topic / 报告(JSON 数组形式) |
| `before / after` | 可选 | code-level 改动初稿,如果 LLM 能猜到 |

**推荐**:5 个必填(decision_type / subject / rationale / verification_plan / risk)+ evidence 必填(≥1 条引用)。before/after 可选(Phase 4 Craftsman 才需要)。

### 决策 5:health_check 新增项目数

| 选项 | 加哪些 |
|---|---|
| A. 4 项 | timer enabled / reports dir / reports ≥ 1 / prompt 模板存在 |
| B. 6 项 | A + analyst 至少跑过一次成功 + l2_llm_calls 含 analyst kind |
| C. 8 项 | B + reports/_meta/index.json 存在且非空 + 最近 reports 新鲜(<10 天) |

**推荐**:**B**(总数 26 + 6 = 32)。reports 新鲜度由 acc status 显示更合适,不进硬性 health_check。

---

## 6. 工作量与排期估计

> 不给具体小时数。

依赖关系:Step 1 → 2 → 3 → 4 → 5 → 6 → 7,每一步都是前一步的"前置就绪"信号。Step 3 是 LLM 工作量大头(prompt 调试 + 实跑 + 人审 + 调 prompt 再跑)。Step 7 验收门槛低(首跑 7/10),但"4 周连续"留作项目级 follow-up,**不卡 v0 spec 验收**。

---

## 7. 风险登记

| 风险 | 缓解 |
|---|---|
| 数据稀疏导致首份报告价值低 | 决策 2 的"如实标注"路径;接受 v1.0 价值低,迭代 prompt 改进 |
| LLM 编数字 / 引用不存在的 ops_metrics 行 | prompt 模板硬性要求"引用必带 metric_id";抽查机制 |
| 报告同周重复触发覆盖 | Step 3 的 `_v2.md` 后缀策略;Step 4 的 index.json 记录每次 |
| systemd timer 周日 20:00 SGT 与人工运营周相位不齐(用户在境外/休假) | v0 不解决,留作 v1 优化(可配置 cadence) |
| Sonnet 4.6 上下文上限 1M tokens 但 prompt + 4 份 extracted + 4 周 metrics 也才 ~30K tokens | 不是问题;为未来 history-rich 数据预留 |
| 决策 4 的"必填 5 字段"对 LLM 太刚性,导致内容空洞 | 实跑后调 prompt;字段必填但允许"未知"声明 |

---

## 8. 与上游(Librarian v1)的接口约定

- 读 `knowledge/pulse/extracted/*.md` 时,假定 Librarian 已最新(每日 06:00 SGT 自动同步)
- 读 `knowledge/pulse/_meta/last_synced_at.txt` 检查 Librarian 新鲜度,> 36h 标 [degraded]
- 调 LLM 复用 `meta_ops.librarian.llm_client.call_claude()`(单一入口、双轨留痕)
- 不直接读 pulse_src/code/(那是 code_index 的素材源,Analyst 不该越界看原代码)

---

## 9. 与下游(Phase 3 周报推送器)的接口约定

- reports/YYYYWW.md 用稳定的章节标题(便于 Phase 3 解析"候选决策"段)
- index.json 为 Phase 3 的"哪些周报还没推过 TG"提供查询入口

---

> **本 Spec 状态**:v0-draft (2026-05-03)。待 Jonathan 答复 §5 决策 1-5 后,定稿为 v1,正式开干 Step 1。
