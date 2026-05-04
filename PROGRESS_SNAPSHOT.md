# Accelerator L2 — 进度快照

> **快照时间**: 2026-05-04(**Phase 3 Step 1-5 完成,Step 6 archiver / Step 7 验收待启**)
> **目的**: 下次会话(包括跨机器接力)可直接接上,无需重新读全部历史
> **如何使用**: 新会话打开后,先读 `CLAUDE.md` → 本文件 → 进入 §8 决定下一步

---

## 1. 一句话状态

**Phase 2 整体完成 ✅**(2026-05-04)。Librarian v1(2026-05-03)+ Analyst v0(2026-05-04)双双收官。32/32 health_check。systemd 双 timer 自动跑(Librarian 每日 06:00 SGT / Analyst 每周日 20:00 SGT)。首份周报 2026W19.md 8.5/10。Phase 2 累计 LLM 成本 $1.62(后续日跑 = $0,周跑 ≈ $0.15)。

**Phase 3 Step 1-5 全部完成**(2026-05-04):
- Step 1:migration 004 `ops_decision_threads` 表(7 状态 CHECK + 3 索引 + FK→ops_decisions)在生产 DB 跑通,4/4 验收过
- Step 2:`meta_ops/facilitator/{bots,run}.py` + `acc-facilitator.service`(Restart=always),4 Bot(@acc_ana_bot/@acc_fac_bot/@acc_wat_bot/@acc_cra_bot)同进程 polling
- Step 3:`meta_ops/facilitator/report_parser.py` 纯函数 + `CandidateDecision` dataclass,11/11 单测过
- Step 4:`pusher.py` + `acc facilitator push` CLI + `acc-facilitator-pusher.timer`(Sun 20:05 SGT)。首跑 2026W19 → acc_work_gp 4 条决策卡(msg_id=13-16)
- **Step 5(本会话收尾)**:`state_machine.py`(7 actions × 9 transitions 纯函数,16/16 单测过)+ `handlers.py`(CallbackQueryHandler `dec:apv|rej|dsc|full` + `rep:full` + reply MessageHandler)。**端到端 smoke test 三路全通**(commit `78a5536`):
  - 决策 2 ❌ 否决 → state='rejected' ✅
  - 决策 3 💬 讨论 → LLM 答疑 200-500 字 reply 到群($0.0487 facilitator call)→ state 还原 'displayed' ✅
  - 决策 4 ✅ 采用 + reply rationale → state='approved' + **ops_decisions 写真行 `9abd1caf...`(status='active', evidence 3 条, decided_at=2026-05-04 15:13 SGT)** + ops_decision_id 关联 ✅
- 决策 1(msg_id=13)留作 Step 6 archiver 12h auto-deferred 测试样本

下一站:**Step 6 archiver + acc CLI 扩展**(`archiver.py` `archive_stale_threads(hours=12)` + `acc-facilitator-archiver.timer` + `acc facilitator status` + `acc status` 加 thread 一节),然后 **Step 7 验收**(health_check 加 5 项,32 → 37)。

---

## 2. 已完成里程碑

### Phase 1 收尾(2026-05-02)

| 项 | 数据 |
|---|---|
| health_check 通过 | 21 / 21(Phase 0 14 + Phase 1 7) |
| Pulse Connector 单元测试 | 4 / 4 |
| ops_metrics 行数 | 11(article=5, topic=6) |
| l2_run_log | librarian ok×3 / watcher partial×5(partial 现已正确显示为 🟡 deferred) |
| knowledge/pulse/schema/*.md | 5 张表 mirror 齐全 |
| systemd timer | acc-librarian.timer (06:00 SGT) / acc-watcher.timer (23:00 SGT) 双 enabled |
| `acc` CLI | 4 个子命令(status / librarian / watcher / backfill)就位 |
| 部署管道 | `/opt/accelerator-git/`(HTTPS clone)+ `cp` 同步到 `/opt/accelerator/` |
| docs/ 目录 | 5 个文件 symlink 到 git workdir |

### Phase 1 收尾增强(2026-05-02 当日落实)

| 项 | 状态 |
|---|---|
| BLUEPRINT §6.8.4 — `acc status` 区分 deferred / degraded / failed | ✅ 已上线 |
| 实现位置 | `meta_ops/cli/main.py:_classify_run_status`(纯函数,12 用例 smoke 通过) |
| 写入侧前缀约定 | `[deferred]` / `[degraded]` 加在 `summary->'partial_reasons'` 各条前 |
| 改动文件 | `cli/main.py` + `watcher/v0.py` + `librarian/v0.py` |
| 服务器上验证 | `acc status` 输出 watcher 5 行显示为 🟡 deferred,Recent issues 正确 (none) |
| 老数据兜底 | "字面包含 deferred"路径生效;~5/9 后老数据出 7 天窗口,兜底自然失效 |

---

## 3. 服务器当前状态(/opt/accelerator/ on accelerator-jb)

```
/opt/accelerator/
├── .env                       # secret (600 perm)
├── .venv/                     # editable accelerator-0.1.0 + 8 依赖
├── pyproject.toml
├── requirements.txt
├── sql/{001_init_schema,002_run_log}.sql   # both applied
├── scripts/health_check.py    # 21 checks
├── docs/{CLAUDE,PROJECT_BLUEPRINT,INFRASTRUCTURE,PHASE0_SPEC,PHASE1_SPEC}.md  # symlinks
│  (Phase 2 spec 待 push 后 symlink 进来)
├── meta_ops/
│   ├── common/{db,run_log,logging_config}.py
│   ├── pulse_connector/{connector,models}.py + tests/
│   ├── librarian/v0.py        # ★ 2026-05-02 加 [degraded] 前缀
│   ├── watcher/v0.py          # ★ 2026-05-02 加 [deferred]/[degraded] 前缀
│   └── cli/main.py            # ★ 2026-05-02 加 _classify_run_status + 新 status 视图
└── knowledge/pulse/
    ├── SCHEMA_NOTES.md        # 数据契约权威基线
    ├── schema/{articles,topics,publishes,interactions,configs}.md
    └── _meta/{last_synced_at.txt, sync_log.jsonl}

/opt/accelerator-git/          # git workdir, public HTTPS clone of GitHub repo
/etc/systemd/system/acc-{librarian,watcher}.{service,timer}  # enabled

数据库 accelerator (本机 PG):
- schema_versions: v001 / v002
- ops_metrics: 11 rows
- l2_run_log: 8 rows
```

---

## 4. Phase 2 整体收官(2026-05-04)

### 4.0 一图概览

| 子系统 | Step 数 | 完成日 | 验收 |
|---|---|---|---|
| Librarian v1 | 6 | 2026-05-03 | 26/26 health_check |
| **Analyst v0** | **7** | **2026-05-04** | **32/32 health_check + 周报 8.5/10** |

### 4.A Phase 2 Librarian v1 收官(2026-05-03)

### 4.1 6 个 Step 全过

| Step | 落地物 | 状态 |
|---|---|---|
| 1. Pulse 源接入 | `meta_ops/librarian/pulse_source.py`(sync_pulse_docs / sync_pulse_code) | ✅ |
| 2. docs/ 全量同步 | `meta_ops/librarian/sync_docs.py` + `knowledge/pulse/docs/_meta/manifest.json` | ✅ |
| 3. 增量检测 manifest(纯函数) | `meta_ops/librarian/change_detect.py` + 9 单测全过 | ✅ |
| 4. code_index/ LLM 加工 | `meta_ops/librarian/build_code_index.py` + `prompts/code_index_v1.md` | ✅ |
| 5. extracted/ LLM 加工 | `meta_ops/librarian/build_extracted.py` + `prompts/extracted_v1.md` | ✅ |
| 6. 主流程 + status + health_check | `meta_ops/librarian/v1.py` + cli/main.py 改造 + health_check 加 5 项 | ✅ |

### 4.2 验收数据

| 维度 | 数据 |
|---|---|
| health_check | **26/26**(spec 目标 25,实际加 5 项含 l2_llm_calls 表 check) |
| `acc status` 新源显示 | docs 13 / code_index 6 / extracted 4(全 ✅) |
| systemd | `acc-librarian.service` ExecStart 切到 `meta_ops.librarian.v1`,TimeoutStartSec=600 |
| LLM 留痕双轨 | DB `l2_llm_calls` 12 行 + jsonl 12 行(对得上) |
| 首跑成本 | $1.47(code_index 8 calls $0.66 + extracted 4 calls $0.81) |
| 增量行为 | sha256 跳过 unchanged → 后续日跑 = $0 |

### 4.3 6 个 code_index 文件(全部 ≥7/10 人工 review)

`knowledge/pulse/code_index/version1/hk_node/services/`:
- `daily_workflow.py.md`(8.5/10 by Jonathan)
- `history_engine/content_matrix.py.md`
- `publisher/telegram_review.py.md`
- `content_engine/wechat_draft_adapter.py.md`
- `cover_engine/dynasty_prompts.py.md`
- `dashboard/collector.py.md`

### 4.4 4 个 extracted 主题

`knowledge/pulse/extracted/`:
- `matrix_v2_taxonomy.md`(308 行,$0.38 — 最贵也最重要,把 10 类目/3 联动模式/5 等级/94 周/651 篇全挖出来)
- `title_templates.md`(108 行,$0.15)
- `tg_review_flow.md`(157 行,$0.11)
- `publish_platforms.md`(207 行,$0.16)

### 4.5 Phase 1 遗留小工(已收尾)

| 项 | 状态 |
|---|---|
| `deploy.sh` | ✅ 已写并多次跑通,排除 .venv / .env / knowledge / docs symlink |
| `interactions` 表接入 | 推迟到 Phase 2/3 末段决定 |

### 4.6 关键凭据 / 通道(2026-05-03 立的,后续别动)

| 资源 | 路径 / 值 |
|---|---|
| HK docs SSH key (accelerator-jb 端) | `~accelerator/.ssh/id_pulse_hk`(Phase 0 老 key,新加 l2_docs 授权) |
| HK docs 用户 | `l2_docs@43.99.0.100`,bash shell,只读 `/opt/pulse/version1/docs/` |
| GitHub deploy key | `~accelerator/.ssh/id_pulse_repo` (ed25519,2026-05-03 新生)+ repo deploy key 名 `accelerator-jb-l2-readonly`(read-only) |
| SSH alias `pulse-hk-docs` | l2_docs@43.99.0.100, IdentityFile id_pulse_hk |
| SSH alias `github-pulse` | git@github.com, IdentityFile id_pulse_repo |
| HK root 密码 | **2026-05-03 在聊天里暴露过(SSHPASS 临时用)→ 必须立刻 rotate** |
| Anthropic API key | 同样在聊天里暴露过 → **必须立刻在 console.anthropic.com 旋转** |

### 4.7 Phase 2 全局 schema 改动

`sql/003_llm_calls.sql` — `l2_llm_calls` 表(决策 1 = C 双轨之 DB 摘要侧)。schema_versions 三条:001 / 002 / 003。

---

### 4.B Phase 2 Analyst v0 收官(2026-05-04)

#### 7 Step 全过

| Step | 落地物 | 状态 |
|---|---|---|
| 1. 上下文组装(纯函数) | `meta_ops/analyst/context.py`(7 函数 + 4 工具)+ 13 单测全过 | ✅ |
| 2. prompt 模板 | `prompts/analyst_v0_weekly.md`(6 章节固定结构 / 5 必填字段 / 数据稀疏铁律) | ✅ |
| 3. v0 主流程 | `meta_ops/analyst/v0.py`(assemble → render → call_claude → 写报告) | ✅ |
| 4. 报告输出 + index.json | `reports/<week>.md` + `reports/_meta/index.json` 维护 | ✅ |
| 5. acc CLI 扩展 | `acc analyst run` / `acc analyst latest [--print]` + `acc status` 加 Latest weekly report 行 | ✅ |
| 6. systemd timer | `acc-analyst.service` + `acc-analyst.timer`(Sun 20:00 SGT)enabled,下次触发 2026-05-10 20:00 | ✅ |
| 7. health_check 加 6 项 + 验收 | 总数 26 → **32** | ✅ |

#### 验收数据

| 维度 | 数据 |
|---|---|
| health_check | **32/32**(spec 目标 32) |
| 单测 | 13/13(spec 要求 6+) |
| 首份周报 | `reports/2026W19.md` 163 行 |
| 周报评分 | **8.5/10**(spec 要求 ≥7/10) |
| LLM 成本(单次) | $0.152(23.6K input + 5.4K output tokens) |
| 双轨留痕 | `l2_llm_calls` 加 1 条 kind='analyst' / jsonl 加 1 条 |
| systemd timer | enabled,下次 Sun 2026-05-10 20:00 SGT |

#### 5 项决策落地(spec v1)

详见 `PHASE2_ANALYST_SPEC.md §5`:
1. ✅ 时间窗口 = 本周 + 过去 4 周 + 上周报告 continuity
2. ✅ 数据稀疏 = 仍调 LLM,prompt 强制如实标注
3. ✅ `acc knowledge query` = 推迟到独立 spec(Phase 2 后续起草)
4. ✅ 候选决策 5 必填字段 + evidence ≥1 引用
5. ✅ health_check 加 6 项

#### 首份周报亮点(8.5/10 详情)

- **6 章节结构完美**:数据状态 / 摘要 / 信号识别 / 候选决策 / 验证回填 / 下周关注
- **诚实**:本周 0 行 ops_metrics → 直接写"数据不足以下结论",不发明数字
- **跨源整合**:从 matrix_v2_taxonomy + publish_platforms + tg_review_flow 多源融合
- **挖出潜规则**:决策 4 找出 `setup_cross_links_for_3lian()` 是"唯一需显式调用才生效的 cross_link" — 这种是 Phase 2 设计目标的体现
- **可验证**:每条决策的 verification_plan 有具体指标 + 时间窗(如"目标 published=true 占比 ≥ 80%,2026W20-W21 验证")

#### 改进项(留 v1.1)

- system prompt 没区分 watcher partial 是 by-design(deferred)还是真 degraded → LLM 把"7 partial"标为问题。下次调 prompt 加上"watcher partial = deferred 是 by-design,不是 degraded"
- evidence 字段里偶尔把 `topic.status` 当作 `ops_metric` 的 metric 字段引用 — 语义类型混淆,建议加 `type=database_field` 选项
- prompt cache 没生效(system prompt ~2.7K chars ≈ 800 token,不到 cacheable prefix 1024 token 阈值);周跑成本 $0.15 没省下,但绝对值小不优化

---

## 5. 跨会话固化的协作约束(已对齐)

- **遇错停 + 报告 + 等决策**(Phase 1 多次救场,Phase 1 收尾再次救场:scp 同名覆盖)
- **Spec 没说不做**(包括"看起来更好的小改进")
- **数据契约对齐**:写"读外部系统"代码前,先 dump 真实 schema(已成蓝图 §10.10)
- **复杂命令不写多层引号 heredoc**:本地脚本 → scp → server 端独立调用
- **scp 多文件同名要显式区分**(2026-05-02 新增,见 MEMORY.md)
- **secret 永远在 .env**(600 perm, owner accelerator)
- **schema 改动必走 migration 文件**,不直接 ALTER

---

## 6. 下次会话开局动作

1. 读 `CLAUDE.md`(自动加载)→ 本文件 → `PHASE2_SPEC.md`(已就绪)
2. 检查 Jonathan 是否已答 PHASE2_SPEC §5 的 4 个待决策点
3. 若已答 → 起 Step 1;若未答 → 提醒 Jonathan 拍板,不要替他猜

---

## 7. SSH / 环境关键事实

- SSH host alias: `accelerator-jb`(默认登录 root,业务命令必须 `sudo -u accelerator`)
- `.env` owned by accelerator (mode 600);root 也可读
- 两个数据库连接:
  - 本机:`psql -h 127.0.0.1 -U acc_app -d accelerator`(`ACC_DB_PASSWORD` 在 .env)
  - Pulse 经隧道:`psql -h 127.0.0.1 -p 5433 -U l2_reader -d pulse`(`HK_DB_PASSWORD` 在 .env)
- 部署:`ssh accelerator-jb /opt/accelerator-git/deploy.sh`(自动 git pull + rsync 同步代码)

---

## 8. 当前下一步(明确指令)

**Phase 3 Step 1-5 完成,端到端打通。下一站:Step 6 archiver + Step 7 验收收尾。**

新会话开局动作:
1. 读 `CLAUDE.md`(自动加载)
2. 读 **本文件**(`PROGRESS_SNAPSHOT.md`)
3. 读 **`PHASE3_SPEC.md`** §Step 6 / §Step 7
4. 先看 §8.A 已知小瑕,问 Jonathan 是否要在 Step 6 顺手修(还是留 v1.1)
5. 然后开 Step 6 编码

### 8.A 已知小瑕(Step 5 收尾发现,留 v1.1 或 Step 6 顺手修)

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `ops_decision_threads.decided_by='unknown'`(2026W19 全 4 行)| Jonathan 早期 TG 没设 username,callback `from_user.username=None`,fallback 'unknown';COALESCE 不覆盖 | 已设 username,新决策正常;旧 4 行需手动 `UPDATE ops_decision_threads SET decided_by='<新username>' WHERE decided_by='unknown' AND report_week='2026W19';`(待 Jonathan 给 username) |
| 2 | `ops_decisions.decision_type` 形如 `` `workflow_tweak` ``(带反引号)| `report_parser._extract_field` 未去除值外的反引号 | `value.strip("`")` 一行修;Step 3 范畴 |
| 3 | (无,LLM 答疑 reply 到群已确认收到)| — | — |

### 8.B Step 6 任务范围

- `meta_ops/facilitator/archiver.py` `archive_stale_threads(hours=12)`:
  - 查 `state IN ('displayed','in_discussion','approved_pending_rationale')` 且 `created_at < now() - 12h`
  - 逐条:`sm.transition(state, 'archive_stale')` → state='deferred',edit 卡(append "⏰ 已归档" + 移按钮)
- systemd `acc-facilitator-archiver.service` + `.timer`(每小时跑一次)
- acc CLI 扩展:
  - `acc facilitator status` — 列当前 thread 状态分布(per state count)
  - `acc status` 加一节 `── Phase 3 decision threads ──`
- 测试样本:msg_id=13(决策 1)是当前唯一 displayed 行,等 archiver 跑后应转 deferred

### 8.C Step 7 验收(health_check 加 5 项,32 → 37)

详见 PHASE3_SPEC.md §Step 7 表:
1. `ops_decision_threads` 表 + 3 索引(migration 004)
2. `acc-facilitator.service` is-active
3. `acc-facilitator-pusher.timer` is-enabled
4. `acc-facilitator-archiver.timer` is-enabled
5. 4 个 TG_BOT_TOKEN 全非空(单项合并)

### 8.D Step 5 端到端验证数据(供回看)

```
ops_decision_threads (2026W19) at 2026-05-04 15:13 SGT:
  cand=1 displayed       msg_id=13                                   [留作 Step 6 测试]
  cand=2 rejected        msg_id=14  decided_by='unknown'
  cand=3 displayed       msg_id=15  decided_by='unknown'             [💬 后回 displayed]
  cand=4 approved        msg_id=16  decided_by='unknown'  rationale='提升用户体验，ok'
                                    ops_decision_id=9abd1caf-4b63-4500-bfea-e6ac6cb37a66

ops_decisions:
  9abd1caf… `workflow_tweak` `content_matrix.setup_cross_links_for_3lian()`
  rationale='提升用户体验，ok'  status='active'  evidence=3 条
  decided_at=2026-05-04 15:13:15 SGT

l2_llm_calls:
  facilitator/ok  cost=$0.0487  (1 次,决策 3 💬 讨论答疑)
```

### 8.E 服务器关键产出清单(Step 1-5 累计,新会话不要重做)

- migration 004 `ops_decision_threads` 表已在生产 DB
- `acc-facilitator.service` active(Restart=always,4 Bot polling,Step 5 handlers 装在 analyst bot 上)
  - 启动日志关键行:`meta_ops.facilitator.handlers: Step 5 handlers registered on analyst bot`
- `acc-facilitator-pusher.timer` enabled(Sun 20:05 SGT)
- 4 Bot = `@acc_ana_bot` / `@acc_fac_bot` / `@acc_wat_bot` / `@acc_cra_bot`
- TG 协作群 = `acc_work_gp`(chat_id 已填 .env `TG_ADMIN_CHAT_ID`)
- `report_parser.parse_report()` + 11 单测过(本地+服务器)
- `pusher.push_weekly_report()` + `acc facilitator push [--week] [--dry-run]` CLI
- `state_machine.transition()` + 16 单测过(本地+服务器)
- `handlers.register_handlers(analyst_app)` — CallbackQueryHandler `^dec:` + `^rep:full:` + reply MessageHandler
- Phase 3 git commits push 到 origin/main(HEAD = `78a5536`)

### 8.F 安全债务(新会话开始时**先提醒 Jonathan**)

- ⚠️ 4 个 TG_BOT_TOKEN_* 在 2026-05-04 transcript 暴露过
- ⚠️ Anthropic API key + HK root 密码 在更早 transcript 暴露过
- 建议:Phase 3 Step 7 端到端验收完成后立刻轮换全部 secret
- chat_id `acc_work_gp` 也在 transcript 但不算 secret(非群成员无发言权)

---

## 9. 跨会话 / 跨机器接力须知

> 这一节的内容在 Windows 本地 `~/.claude/projects/.../memory/MEMORY.md` 也有,但
> 那个文件**不进 git**。以下内容是镜像版,任何机器都能从 git 读到。

### 9.1 项目级偏好(Jonathan 已对齐,不要再改)

- CLI 命令名 `acc`(不要叫 accelerator-cli)
- 单语言 Python 3.11+,不引入 Node/Go
- 不引入 LangChain/AutoGen 这类 LLM 框架,直接调 Anthropic SDK
- 配置只用 `.env` + python-dotenv(不混 yaml/toml)
- 调度只用 systemd timer(不用 cron)
- 测试范围:Pulse Connector / change_detect / analyst.context 强制单测;Watcher/Librarian 主流程不强求
- LLM 默认模型 = `claude-sonnet-4-6`(Phase 2 §0.4),需要更高质量再升 Opus
- LLM 调用必须双轨留痕:DB 表 `l2_llm_calls`(摘要)+ `knowledge/_meta/llm_calls.jsonl`(全量),单一入口 `meta_ops.librarian.llm_client.call_claude()`

### 9.2 个人协作偏好(Jonathan 风格)

- 喜欢"先停下报告 + 多个选项 + 推荐方案",**不要替他做决策**
- 阶段性结果倾向输出到本地 .md 文件,而非长聊天回复
- 验证必须实打实跑,不接受"代码看起来对就 OK"
- 进度文件写实诚,失败明确标 ❌,不粉饰

### 9.3 协作红线(已验证有效)

- **遇错立即停**,不绕过、不猜测修复
- **Spec 没说的不做**(包括"看起来更好的小改进")
- **Secret 永远在 .env**(600 perm, owner accelerator),不在代码里
- **Schema 改动必走 migration 文件**,不直接 ALTER

### 9.4 已踩过的坑(避免重复)

#### scp 多文件同名互相覆盖(2026-05-02)
`scp watcher/v0.py librarian/v0.py server:/tmp/` 在 `/tmp/` 下两个 v0.py 互相覆盖。
→ **固化**:scp 多个同名源文件时,目标必须显式区分,或一次只 scp 一个。
→ **更优**:`git pull` 在服务器拉,而非 `scp + cp`。

#### SSH heredoc + Windows Git Bash 转义
`ssh server "...\$VAR..."` 在 Windows Git Bash 上被处理两次,变量失踪/输出重复。
→ **固化模式**:复杂命令一律走"本地写脚本 → scp 到 /tmp → server 端独立调用 → 删 /tmp 文件"三步,不再写多层嵌套引号 heredoc。
→ 单条命令也用 `ssh server 'cmd1 && cmd2'`(单引号)优于双引号。

#### Pulse schema vs Spec 文本不一致(Phase 1)
- `articles.word_count` 实际在 `versions->>'word_count'` jsonb 嵌套
- `articles.cover_url` 顶层全空,真封面在 `platform_versions.cover_<platform>`
- `topics.angle` **不在 topics 表**,在 `articles.compliance_check->>'angle'`
- `publishes.last_synced_at` / `interactions.platform/user_name/sentiment/replied` 全部不存在
- `publishes.metrics` 存在但全是默认 `{}`

→ **教训**:任何"按 spec 写 SQL"的代码,第一次跑要在真数据上 sanity check。
→ **教训**:dataclass 字段 ⇄ DB 列 ⇄ Spec 文本三者一致性,要靠先建一份"数据契约对齐表"再写代码。

#### deploy.sh 自更新延迟(2026-05-03)
deploy.sh 改自己后,**当次跑用的是 bash 已加载在内存的旧版**,新配置直到下一次 deploy 才生效。
→ 改 deploy.sh 后跑一次会"看似没生效",再跑一次才对。

#### 数据契约规则(Phase 1 强制)
写"读外部系统"代码前,**必须先 dump 真实 schema 并固化到 `knowledge/pulse/SCHEMA_NOTES.md`**。jsonb 字段在 Pulse 是"演进缓冲",不能假装是顶层列;Connector 在 Python 侧抽取(不用 SQL `->>` 路径)。已知空通道(如 `publishes.metrics={}`)Connector 正常拉,Watcher 写 ops_metrics 时空 dict 也写。

### 9.5 在另一台机器启动 Claude Code 的步骤

1. **clone 仓库**:
   ```bash
   git clone git@github.com:Jonathan7758/Ai-accelerator.git
   # 或 HTTPS:
   git clone https://github.com/Jonathan7758/Ai-accelerator.git
   cd Ai-accelerator
   ```

2. **配 SSH alias 到 accelerator-jb**(只在你需要让 Claude Code 远程操作服务器时)。
   `~/.ssh/config` 加:
   ```
   Host accelerator-jb
       HostName <服务器 IP,见 INFRASTRUCTURE.md>
       User root
       IdentityFile ~/.ssh/<你的私钥>
   ```
   把你登录服务器的私钥从原机器拷过来(或新生成 + 加到服务器 `~/.ssh/authorized_keys`)。

3. **启动 Claude Code**(在 `Ai-accelerator/` 目录下):
   - CLAUDE.md 自动加载
   - 主动让 Claude 读:`PROGRESS_SNAPSHOT.md`(本文件)+ `PHASE3_SPEC.md`(当前 active spec)
   - 告诉 Claude:"继续 Phase 3 spec 起草,Jonathan 已答 §5 决策为 X/Y/Z"(或仍在 v0-draft)

4. **不需要本地配 ANTHROPIC_API_KEY**:LLM 实跑都在服务器上(`/opt/accelerator/.env`)。本地 Claude Code 只做代码编辑 + git push + 远程 ssh 调用。

5. **第一句让 Claude 跑的命令**(自检):
   ```
   ssh accelerator-jb 'cd /tmp && sudo -u accelerator /opt/accelerator/.venv/bin/python /opt/accelerator/scripts/health_check.py 2>&1 | tail -3'
   ```
   应该看到 `All 32 checks passed.`

### 9.6 重要凭据状态(2026-05-04)

| 凭据 | 状态 |
|---|---|
| Anthropic API key | ⚠️ 在 transcript 暴露过,**应该旋转**(Jonathan 还没确认做) |
| HK root 密码 `1Qxcjyb!@` | ⚠️ 同上,**应该改密码** |
| 4 个 TG_BOT_TOKEN_*(`@acc_ana/fac/wat/cra_bot`) | ⚠️ 2026-05-04 在 transcript 暴露,Step 7 端到端验收后立刻 BotFather `/revoke` + `/token` 重生 |
| TG 协作群 `acc_work_gp` chat_id | ✅ 2026-05-04 已抽并填入 .env `TG_ADMIN_CHAT_ID`(.env.bak.before_chat_id 留底);chat_id 不是 secret(非群成员无法发消息),不强求轮换 |
| accelerator-jb 登录用 SSH key | 由 Jonathan 个人持有,在原机器 `~/.ssh/` |
| HK l2_docs / l2_tunnel 用 SSH key | 在服务器 `/home/accelerator/.ssh/id_pulse_hk`(不动) |
| GitHub project-pulse deploy key | 在服务器 `/home/accelerator/.ssh/id_pulse_repo`(只读 deploy key,不动) |

---

**END OF SNAPSHOT — Phase 3 Step 1-5 收官,Step 6 archiver / Step 7 验收待启**
