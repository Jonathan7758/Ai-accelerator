# Accelerator L2 — Phase 3 协作层 开发方案

> 给 Claude Code 的开发任务说明书
> Phase 3 总体目标:**协作层** = 把 Phase 2 的"本地 markdown 周报"变成"在 TG 群点按钮就能完成决策"的协作流。
> 完成后:Jonathan 周日晚不用打开电脑,在手机 Telegram 上看周报、点按钮 → ops_decisions 入库。

---

## 0. 必读前置

### 0.1 在动笔前先读这些

```
/opt/accelerator/CLAUDE.md                    — 项目恒定知识(自动加载)
/opt/accelerator/docs/PROJECT_BLUEPRINT.md    — 重点读 §8(Phase 3 设计)、§4(角色边界)
/opt/accelerator/docs/PHASE2_OVERVIEW.md      — Phase 2 整体鸟瞰,理解 Analyst 产出格式
/opt/accelerator/docs/PHASE2_ANALYST_SPEC.md  — §4 候选决策 6 字段格式(Phase 3 推送器要解析这些)
/opt/accelerator/sql/001_init_schema.sql      — ops_decisions 字段定义
/opt/accelerator/reports/2026W19.md           — 已有周报样本,看 §4 实际长什么样
```

### 0.2 上游已就绪(本 Spec 的起点)

- Phase 2 整体收官,32/32 health_check
- `reports/<week>.md` 周日 20:00 SGT 自动产出,§4 用稳定 6 字段格式
- `reports/_meta/index.json` 维护周报清单
- ops_decisions 表存在,**status='active' 的语义**:已审批入库的决策
- LLM 双轨留痕通道就绪(Facilitator 可调用 LLM 处理 /ask 类问题)
- `.env` 已有 `TG_BOT_TOKEN` 和 `TG_ADMIN_CHAT_ID`(Phase 0 占位)

### 0.3 Phase 3 设计意图

```
┌────────────────────────────────────────────────────────────────┐
│  Phase 3 = "周报草稿 → TG 协作 → ops_decisions 入库"            │
├────────────────────────────────────────────────────────────────┤
│                                                                  │
│  周日 20:00  Analyst v0 跑完 → reports/2026W19.md               │
│                          │                                       │
│                          ▼                                       │
│  周日 20:05  周报推送器(本 spec 新)                              │
│              ├─ 解析 §4 候选决策(6 字段)                          │
│              ├─ @acc_analyst_bot 发到 TG 群                      │
│              └─ Inline Keyboard:[📄 全文] [✅采用] [❌否决] [💬讨论]│
│                          │                                       │
│                          ▼                                       │
│  Jonathan 在手机点按钮 → Facilitator 接回调                       │
│                          │                                       │
│              ┌───────────┼───────────┬─────────────┐            │
│              ▼           ▼           ▼             ▼            │
│           rejected  in_discussion  approved_   (12h 无响应)     │
│           (终态)     (LLM 答疑)    pending_     auto archive    │
│                          │         rationale     (deferred)     │
│                          ▼           │                          │
│                       proposed       ▼                          │
│                                "请用一句话写理由" → 用户回复     │
│                                       │                         │
│                                       ▼                         │
│                                    approved                     │
│                                       │                         │
│                                       ▼                         │
│                              写 ops_decisions(status='active') │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

### 0.4 关键技术约束

| 约束 | 来源 / 备注 |
|---|---|
| TG 库 = `python-telegram-bot` v21+ | BLUEPRINT §8.7,跟 Pulse 一致,生态成熟 |
| 多 Bot = 同进程,不同 token | BLUEPRINT §8.7,资源占用低,代码共享 |
| 决策中状态持久化到 DB(不是内存) | Bot 重启不丢状态,沿用 Postgres |
| 12h 无响应自动归档为 `deferred` | BLUEPRINT §8.7 |
| Inline Keyboard 风格 = emoji + 短文本,2-4 个按钮一行 | 移动端可读 |
| 周报推送器只解析 markdown,**不 LLM** | 解析失败 → 推 raw markdown,降级而非崩 |
| Facilitator 接 /ask 等交互时**才**调 LLM | 走 `meta_ops.librarian.llm_client.call_claude(kind='facilitator')`,沿用双轨留痕 |

### 0.5 决定性偏好(已对齐,不要改)

- **角色边界严守**:Facilitator **不做业务判断**(CLAUDE.md §6)。它只主持议程、推进状态机、写 ops_decisions,**不替 Jonathan 评估某条决策好坏**。
- **TG 推送是写,绝不允许 Bot 自动回应运营消息**:除非用户主动 @ 或回复 Bot 的消息。避免群里炸消息。
- **每条 TG 消息开头标 Bot 角色 emoji**(如 `🤖 Analyst:`),帮助 Jonathan 切换决策模式。

---

## 1. Phase 3 工作分解

按依赖顺序,7 个 Step。前一步验收通过才进下一步。

```
Step 1: DB schema 扩展(migration 004:ops_decision_threads)
Step 2: TG Bot 框架(同进程多 token + 启动入口)
Step 3: 周报解析器(reports/<week>.md → 结构化候选决策 list)
Step 4: 周报推送器(@acc_analyst_bot 推 TG + Inline Keyboard)
Step 5: Facilitator 状态机(回调 + 状态转移 + ops_decisions 写入)
Step 6: 12h 自动归档(systemd timer)+ acc CLI 扩展
Step 7: 验收(端到端跑通"看周报 → 点 ✅ → 填 rationale → 入库")
```

---

## 2. Step 详细

### Step 1: DB schema 扩展

**目标**:存"决策中状态"——周报推到 TG 后,每条候选决策的状态 / 关联消息 ID / 用户响应轨迹。

**新表 `ops_decision_threads`**(migration 004):

```sql
CREATE TABLE ops_decision_threads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',

    -- 来源
    report_week TEXT NOT NULL,          -- '2026W19'
    candidate_index INTEGER NOT NULL,   -- 报告里第几条候选决策(1-based)
    decision_type TEXT NOT NULL,        -- 解析自报告
    subject TEXT NOT NULL,
    rationale_proposed TEXT NOT NULL,   -- 报告里 LLM 写的 rationale
    verification_plan TEXT NOT NULL,
    risk TEXT,
    evidence JSONB DEFAULT '[]'::jsonb,

    -- 状态机
    state TEXT NOT NULL                 -- 'proposed' | 'displayed' | 'in_discussion'
                                        -- | 'approved_pending_rationale'
                                        -- | 'approved' | 'rejected' | 'deferred'
        CHECK (state IN ('proposed','displayed','in_discussion',
                         'approved_pending_rationale','approved',
                         'rejected','deferred')),

    -- TG 关联
    tg_chat_id BIGINT,
    tg_message_id BIGINT,               -- 推送时的消息 ID,后续 edit 用

    -- 用户响应
    user_rationale TEXT,                -- 用户填的 rationale(approved 时)
    decided_by TEXT,                    -- TG username,审批人
    decided_at TIMESTAMPTZ,

    -- 终态后写入 ops_decisions 的关联
    ops_decision_id UUID REFERENCES ops_decisions(id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_decision_threads_state ON ops_decision_threads(state, created_at DESC);
CREATE INDEX idx_decision_threads_week ON ops_decision_threads(report_week, candidate_index);
CREATE INDEX idx_decision_threads_tg_msg ON ops_decision_threads(tg_chat_id, tg_message_id);
```

**验收**:
- Migration 004 应用成功,schema_versions 加 v004
- 表有 3 索引,CHECK 约束生效
- ops_decisions 仍可独立写(无 FK 依赖关系阻塞)

---

### Step 2: TG Bot 框架

**目标**:同进程跑多个 Bot(每个用不同 token),收发不串台。

**动作**:

1. `meta_ops/facilitator/__init__.py` 等
2. `meta_ops/facilitator/bots.py`:
   - 单一 `BotRegistry` 类,启动 N 个 `Application`(python-telegram-bot v21)
   - 角色 → token 映射读 `.env`(`TG_BOT_TOKEN_ANALYST` / `TG_BOT_TOKEN_FACILITATOR` 等)
   - `register_handler(role, handler)` 注册回调
3. `meta_ops/facilitator/run.py` 启动入口(supervisord-like 长进程)
4. systemd `acc-facilitator.service` 守护进程(Restart=always)

**Bot 角色枚举**(spec v1 §5 决策 2:4 个 Bot 框架一次到位):

| Bot | 用途 | Phase 3 状态 |
|---|---|---|
| @acc_analyst_bot | 周报推送、回答 /ask 类问题 | ✅ 完整 handler |
| @acc_facilitator_bot | 议程主持、决策回调、状态机 | ✅ 完整 handler |
| @acc_watcher_bot | 异常告警(数据缺失、HK 不通) | ⬜ 占位:仅 `/start` 报身份,业务 handler 留 Phase 4 |
| @acc_craftsman_bot | 派工 / PR 通知 | ⬜ 占位:仅 `/start` 报身份,业务 handler 留 Phase 4 |

**.env 必须 4 个 token 都配齐**(否则 Bot 框架启动报错):
```
TG_BOT_TOKEN_ANALYST=...
TG_BOT_TOKEN_FACILITATOR=...
TG_BOT_TOKEN_WATCHER=...
TG_BOT_TOKEN_CRAFTSMAN=...
```

**验收**:
- 启动 service,**4 个 Bot** 都能 `/start` 响应(Watcher/Craftsman 占位 Bot 回 "I'm acc_watcher_bot, business handlers come in Phase 4." 类似文案)
- 关闭其中一个 Bot token,其他不受影响(框架隔离)
- 每条消息开头有 emoji 角色标识(`🔍 Analyst:` / `🎯 Facilitator:` / `👁 Watcher:` / `🔨 Craftsman:`)

---

### Step 3: 周报解析器

**目标**:把 `reports/<week>.md` §4 的 N 条候选决策抽成结构化 list。**纯函数,可单测**。

**动作**:

1. `meta_ops/facilitator/report_parser.py`
2. 函数 `parse_report(md_text: str) -> list[CandidateDecision]`
3. CandidateDecision dataclass(对应 PHASE2_ANALYST_SPEC §5 决策 4 的 6 字段):
   - decision_type / subject / rationale / verification_plan / risk / evidence
4. 解析策略:
   - 用 markdown header 找 `## 4. 候选决策...`
   - 每个 `### 决策 N:` 切一段
   - 字段名按 ` - **decision_type**: ` 模式提取
   - evidence 是 JSON,用 `json.loads` 解析,失败标 evidence_parse_error 不崩
5. 失败容错:
   - 缺章节 → 返回 `[]`,不抛
   - 字段缺失 → 该字段填 `None`,but `parse_warnings` 列表记一条
6. `meta_ops/facilitator/tests/test_report_parser.py`:6+ 单测
   - 完整报告(2026W19.md 真实样本)解析出 4 条
   - 缺 §4 章节
   - 字段乱序
   - evidence JSON 损坏
   - 单条决策(只有 1 条)
   - 候选决策 0 条(`§4` 写"本周无候选")

**验收**:
- 单测全过
- 用 `reports/2026W19.md` 实际跑,解析出 4 条 CandidateDecision

---

### Step 4: 周报推送器

**目标**:Analyst 完成后(或手动触发)→ 推 TG。

**动作**:

1. `meta_ops/facilitator/pusher.py`
2. 函数 `push_weekly_report(week_iso: str) -> dict`:
   - 读 `reports/<week>.md` + 解析
   - 推一条 summary 消息(@acc_analyst_bot)+ Inline Keyboard `[📄 全文]` 按钮
   - 每条候选决策**单独一条消息**,各自带按钮 `[✅采用] [❌否决] [💬讨论]`
   - 写 ops_decision_threads(state='displayed',记 tg_message_id)
3. 触发方式(spec v1 §5 决策 3 已锁定:**B + C**):
   - **B**. 单独 systemd `acc-facilitator-pusher.service` + `.timer`(`OnCalendar=Sun 20:05 SGT`,Analyst 完成 5 分钟后)
     - 跑前 check `reports/_meta/index.json` 最新条目 `status=='ok'` 且 `week == 当前 ISO 周`,否则跳过(写一行 deferred 到 l2_run_log,不报错)
   - **C**. 手动 `acc facilitator push --week YYYY-WW` 兜底入口(忘记跑 / 想补推 / 测试时用)
   - ❌ 不走 A(Analyst 进程内 hook)— 解耦优先,Analyst 跑挂不连带推送挂
4. 失败处理:
   - reports 不存在 → 推一条 "周报缺失" 通知
   - 解析失败 → 推 raw markdown(不解析候选决策按钮)
   - TG API 失败 → mark_partial,不抛

**验收**:
- 手动 `acc facilitator push --week 2026W19` 成功推 5 条消息(1 summary + 4 候选)
- ops_decision_threads 加 4 行 state='displayed'
- 关闭 TG_BOT_TOKEN 后跑,优雅 mark_partial
- `acc-facilitator-pusher.timer` enabled,`systemctl list-timers` 看到下次触发 = 下个 Sun 20:05 SGT
- 用伪造 `index.json`(status='partial')触发,pusher 跳过推送 + l2_run_log 写一行 deferred

---

### Step 5: Facilitator 状态机

**目标**:接 Inline Keyboard 回调 + 推进状态。

**动作**:

1. `meta_ops/facilitator/state_machine.py` — 纯函数,可单测
   - `transition(thread, action, payload) -> new_state`
   - 8 种合法 transition(BLUEPRINT §8.4 的状态图)
   - 非法 transition 返回 error,不改 state
2. `meta_ops/facilitator/handlers.py` — TG 回调处理
   - `on_callback_采用`:state → approved_pending_rationale,Bot 回 "请用一句话写理由"
   - `on_callback_否决`:state → rejected,记 decided_by,刷新消息
   - `on_callback_讨论`:state → in_discussion,Bot 调 LLM 答 "为什么这么建议"(用 evidence 数组重组上下文)
   - `on_message_rationale`(after approved_pending_rationale):state → approved,**写 ops_decisions** + 回 "决策 #ID 已记录"
3. **写 ops_decisions** 是关键动作,必须:
   - 完整 6 字段(decision_type / subject / before_state / after_state / rationale / evidence / verification_plan)
   - rationale 用 user_rationale,不用 rationale_proposed(用户的话权威)
   - status='active',decided_at=NOW
4. `meta_ops/facilitator/tests/test_state_machine.py`:8 单测覆盖所有 transition

**验收**:
- 8 单测全过
- 端到端测:手动推一份测试报告 → 点 ✅ → 输文字 → 看 ops_decisions 表多了一行

---

### Step 6: 12h 自动归档 + acc CLI

**目标**:无响应的决策不污染待办,自动 archive 为 deferred。

**动作**:

1. `meta_ops/facilitator/archiver.py` 函数 `archive_stale_threads(hours=12)`:
   - 查 `state IN ('displayed', 'in_discussion', 'approved_pending_rationale')` 且 `created_at < now() - 12h`
   - 转 `state='deferred'`,刷新 TG 消息(改文字 + 移除按钮)
2. systemd `acc-facilitator-archiver.service` + `acc-facilitator-archiver.timer`(每小时跑一次)
3. acc CLI 扩展:
   - `acc facilitator push [--week]` 手动推送
   - `acc facilitator status` 列当前 thread 状态分布
   - `acc status` 加一节 `── Phase 3 decision threads ──`

**验收**:
- archiver 跑后 stale thread 都标 deferred
- TG 消息按钮被移除(用 InlineKeyboardMarkup(None))

---

### Step 7: 验收

**端到端测试场景**:

1. 手动 `acc facilitator push --week 2026W19`
2. TG 群收到 5 条消息
3. 在某条决策上点 [✅采用] → Bot 回 "请用一句话写理由"
4. 用户输 "T2 已用 3 周,该轮换了"
5. Bot 回 "决策 #d92a 已记录,预期 2026W20-W21 验证"
6. SQL 查 ops_decisions:多了一行 status='active'
7. SQL 查 ops_decision_threads:对应行 state='approved',ops_decision_id 关联

**health_check 加项**(spec v1 §5 决策 5 已锁定:**5 项**,总数 32 → **37**):

| # | 检查 | 通过条件 |
|---|---|---|
| 1 | `ops_decision_threads` 表存在(migration 004 应用)| `schema_versions` 含 v004 + `\d ops_decision_threads` 有 3 索引 |
| 2 | `acc-facilitator.service` active | `systemctl is-active` 返回 `active` |
| 3 | `acc-facilitator-pusher.timer` enabled | `systemctl is-enabled` 返回 `enabled` |
| 4 | `acc-facilitator-archiver.timer` enabled | 同上 |
| 5 | 4 个 TG_BOT_TOKEN 都在 .env 非空 | 单项合并:`TG_BOT_TOKEN_{ANALYST,FACILITATOR,WATCHER,CRAFTSMAN}` 全非空,任一缺则失败 |

---

## 3. Phase 3 完成标志

- [ ] Step 1-7 全过
- [ ] 4 个 Bot 都能正常收发,不串台(Watcher/Craftsman 占位 OK)
- [ ] 决策状态机 8 transition 都有测试覆盖
- [ ] 一次完整的"看周报 → 选 ✅ → 填 rationale → ops_decisions 入库"端到端跑通
- [ ] 12 小时无响应自动归档生效
- [ ] health_check 总数从 32 升到 **37**

---

## 4. 不在本 Spec 范围(留给后续)

| 项 | 留给 |
|---|---|
| Craftsman 改 Pulse 代码 | Phase 4(BLUEPRINT §9) |
| Verification 回填(决策生效后跟踪指标) | Phase 4 |
| Bot 主动推送除周报以外的事(每日早安、热点告警) | Phase 4 / 后续 |
| 多 admin / 多 chat 支持 | 待 §5 决策 6 拍板 |
| 复杂讨论(`💬讨论` 后自由对话超过 1 轮) | v0 限定单轮 LLM 答疑;深度讨论留 v1 |
| ops_playbook 自动晋升(决策被验证 ≥ 3 次 → playbook) | Phase 4 |

---

## 5. 已确定决策(spec v1, 2026-05-04 Jonathan 拍板)

> v0-draft 6 条待定决策已全部锁定。下面是定稿,实现时按这些来,**别再翻 v0-draft 推荐项**。

### 决策 1:推送目标 = **TG 群**

- 用 `TG_ADMIN_CHAT_ID`(Phase 0 已是群 ID),所有周报 / 候选决策推到此群
- 推论:`@acc_analyst_bot` / `@acc_facilitator_bot` 必须先被加入该群且授予发消息权限
- 推论:CLAUDE.md §0.5 的"绝不允许 Bot 自动回应运营消息"在群里更重要 — Bot 仅响应 callback_query 和 reply 自己消息的 message,**不主动**回应群里其它消息

### 决策 2:Bot 数量 = **4 个**(Watcher/Craftsman 占位)

- 框架一次到位:启动 4 个 `Application` 实例
- Phase 3 业务 handler 只装在 Analyst / Facilitator 上
- Watcher / Craftsman 仅注册 `/start` 占位回调,业务 handler 留 Phase 4
- 推论:`.env` 必须有 4 个 token,缺任何一个 service 启动就失败(防隐性遗漏)

### 决策 3:周报推送触发 = **B + C**

- **B**:`acc-facilitator-pusher.timer`(`OnCalendar=Sun 20:05 SGT`),Analyst 完成 5 分钟后跑
- **C**:`acc facilitator push --week YYYY-WW` 手动入口(补推 / 测试 / 忘跑兜底)
- pusher 启动时必查 `reports/_meta/index.json` 最新条目 `status=='ok'` 且 week 匹配,否则跳过 + 写 deferred 一行
- ❌ 不走 A(进程内 hook),解耦优先

### 决策 4:`💬讨论` 回调 = **A 单轮 LLM**

- Bot 用候选决策的 evidence + 关联 extracted/ 文档,组装 system prompt
- 调一次 LLM 产出 200-500 字答疑,直接回到 TG,**不进入多轮会话**
- 多轮 / 深度追问留 v1
- 推论:状态机里 `in_discussion` 是过场态,LLM 答完后 thread state 应回到 `displayed`(用户可继续点 ✅/❌)

### 决策 5:health_check 加 **5 项**(32 → 37)

详见 Step 7 表格。要点:
- 4 项 service / timer / 表 各自独立
- 4 个 TG_BOT_TOKEN 检查合并为 1 项(任一缺则失败)— 不展开成 4 项,因为运维语义上"4 个都得在"是单一约束

### 决策 6:多 admin = **A 单 admin**(`TG_ADMIN_CHAT_ID` 不动)

- Phase 3 全程沿用 Phase 1 的单值 `TG_ADMIN_CHAT_ID`
- decided_by 字段直接记 TG username(group 里发 callback 的人是谁就是谁)
- 多 admin / 多 chat 改名 / admin 表 全部留 v1.1+,本 spec 不预留 schema 字段

---

## 6. 工作量与排期估计

> 不给小时数。

依赖关系:Step 1 → 2 → 3 → 4 → 5 → 6 → 7。

- Step 1(migration)+ Step 2(框架)是基础,大概 1/3 工作量
- Step 3(parser)+ Step 4(pusher)+ Step 5(state machine)是主体,大概 1/2
- Step 6(archiver / CLI)+ Step 7(验收)收尾,1/6

**LLM 实跑**:Step 5 的 `💬讨论` 和 Step 7 端到端测,每次 ~$0.05-0.10。

---

## 7. 风险登记

| 风险 | 缓解 |
|---|---|
| TG API 限流 / 网络抖动 | python-telegram-bot 自带重试;失败标 partial,不影响 ops_decisions 入库 |
| 周报 §4 解析失败(LLM 偶尔写错格式) | 解析器降级:推 raw markdown 而不阻塞;同时报告里给 Analyst 加更严格的字段输出约束 |
| 用户回 rationale 里换行 / 长文 | DB 字段 TEXT 无限制;但 TG 消息渲染要小心 |
| 状态机非法 transition(用户点旧消息按钮) | transition() 返回 error 不改 state;Bot 回 "此消息已过期" |
| 多 admin 时谁 decided_by? | v0 单 admin,直接记 username。多 admin 留 v1 |
| Bot 启动时 TG token 错 | service Restart=always 会反复重启失败;health_check 探 token 非空,真值由 TG API 返回时才发现 |

---

## 8. 与上游(Phase 2 Analyst)的接口约定

- **必须**:reports/<week>.md §4 用稳定 6 字段格式(已在 Analyst spec §5 决策 4 锁定)
- **必须**:reports/_meta/index.json status='ok' 才推送
- **可选**:Analyst 改 prompt 加更严格的 evidence JSON 格式约束(便于 parser),但向后兼容
- **不破坏**:Phase 3 不改 reports/ 内容(只读)

---

## 9. 与下游(Phase 4 Craftsman)的接口约定

- **必须**:写 ops_decisions 时 status='active' + 完整 6 字段,Phase 4 Craftsman 监听 status='active' 触发改代码
- **预留**:ops_decision_threads.ops_decision_id FK,Phase 4 verification 回填时反向查 thread
- **不破坏**:Phase 3 不动 ops_playbook(那是 Phase 4 自动晋升)

---

> **本 Spec 状态**:**v1 定稿** (2026-05-04)。§5 6 决策已锁定。待 Jonathan 启 Step 1(migration 004:`ops_decision_threads` 表)。
