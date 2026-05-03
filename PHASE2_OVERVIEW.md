# Accelerator L2 — Phase 2 整体设计总结

> **生成时间**:2026-05-03
> **状态**:Librarian v1 ✅ 收官 / Analyst v0 📋 spec 起草中(v0-draft)
> **目的**:Phase 2 的"一张纸鸟瞰"——给 Jonathan 决策、给将来 Claude Code 开局快速对齐用。
> **不替代**:本文不替代 BLUEPRINT §7(高层蓝图)、PHASE2_SPEC.md(Librarian v1 spec)、PHASE2_ANALYST_SPEC.md(Analyst v0 spec),只把它们的脉络拉到一处。

---

## 1. Phase 2 的位置

| Phase | 名称 | 角色 | 状态 |
|---|---|---|---|
| 0 | 基础设施搭建 | — | ✅ 完成(2026-04-30) |
| 1 | 数据观察层 | Watcher v0 + Librarian v0(schema mirror)+ Connector + acc CLI | ✅ 完成(2026-05-02) |
| **2** | **分析层** | **Librarian v1 升级 + Analyst v0** | **进行中(下文展开)** |
| 3 | 协作层 | Facilitator + 决策状态机(TG Bot 链) | ⬜ 待启动 |
| 4 | 执行层 | Craftsman + Git PR 流水线 + Verification | ⬜ 待启动 |

Phase 2 的本质:**让 L2 从"看数据"升级到"看数据 + 业务知识 → 写诊断"**。Librarian v1 提供"业务知识",Analyst v0 消费它产出"诊断草稿"。

---

## 2. Phase 2 的两个子系统

### 2.1 Librarian v1(✅ 已完成 2026-05-03)

**职责**:把 Pulse 系统的**代码 + 文档**消化成 L2 可读的"业务知识库",每天 06:00 SGT 自动增量更新。

**新加的三块产出**:

```
knowledge/pulse/
├── schema/        ← Phase 1 已有(5 张表的列定义)
├── docs/          ← 新增:rsync HK 服务器,13 份 .md 全量同步
├── code_index/    ← 新增:LLM 加工的"修改导航图"(6 个关键 .py 文件)
└── extracted/     ← 新增:LLM 加工的"业务概念提炼"(4 个主题)
```

**两条新通道(都是只读)**:
- HK rsync:`l2_docs@43.99.0.100:/opt/pulse/version1/docs/` → `pulse_src/docs/`
- GitHub clone:`Jonathan7758/project-pulse`(deploy key,read-only)→ `pulse_src/code/`

**6 个 code_index 起步文件**(BLUEPRINT §7.7 完成标志):

| 文件 | 用途 |
|---|---|
| `services/daily_workflow.py` | 每日发文总编排(状态机) |
| `services/history_engine/content_matrix.py` | 内容矩阵核心策略层 |
| `services/publisher/telegram_review.py` | TG review 流程(Phase 3 前置参考) |
| `services/content_engine/wechat_draft_adapter.py` | 内容到微信的关键适配 |
| `services/cover_engine/dynasty_prompts.py` | 封面 prompt |
| `services/dashboard/collector.py` | Dashboard 采集(L2 ↔ Pulse 指标对接) |

**4 个 extracted 主题**:

| 主题 | 一句话 |
|---|---|
| `matrix_v2_taxonomy` | 10 类目 / 3 联动模式 / 5 等级 / 94 周 651 篇内容矩阵的权威定义 |
| `title_templates` | 标题模板规则与变量系统 |
| `tg_review_flow` | TG review 决策流的状态语义 |
| `publish_platforms` | 微信/小红书/微头条/视频号发布通道差异 |

**关键设计**:
- 增量重生(sha256 对比 manifest)→ 同源不变 = 0 LLM 调用
- 双轨 LLM 留痕:`l2_llm_calls` 表(摘要)+ `knowledge/_meta/llm_calls.jsonl`(全量)
- 失败优雅:任一 step 失败标 `[degraded]` 不拖整体
- systemd `acc-librarian.service` 切到 `meta_ops.librarian.v1`,timer 不变

**实测数据(收官)**:
- 26/26 health_check
- 首跑总成本 $1.47(后续日跑 = $0)
- 30 个文件 Pulse 代码 / docs / extracted 全过 sha 增量

详见:**[PHASE2_SPEC.md](./PHASE2_SPEC.md)**(Librarian v1 spec 定稿 v1)

---

### 2.2 Analyst v0(📋 spec 起草中,v0-draft 待答 5 决策)

**职责**:每周日 20:00 SGT 跑一次,读"数据 + 业务知识"产出运营周报草稿,**绝不直接落 ops_decisions**。

**架构**:

```
       数据                            业务知识                            
  ┌───────────────┐               ┌───────────────────┐               
  │ ops_metrics   │               │ knowledge/pulse/  │               
  │  (本周 + 4w)  │               │  extracted/ (4 份)│               
  │ ops_decisions │ ◄── 函数化 ──►│  schema/ (5 张表) │               
  │  (4w)         │     上下文    │  code_index/ (6) │               
  │ ops_playbook  │     组装     │  docs/ (13 份)    │               
  └───────┬───────┘               └─────────┬─────────┘               
          │                                 │                         
          └────────────┬────────────────────┘                         
                       ▼                                              
              ┌─────────────────┐                                     
              │ Analyst v0      │  ← Sonnet 4.6 + prompts/analyst_v0  
              │ assemble_context│                                     
              │ + call_claude   │                                     
              └────────┬────────┘                                     
                       ▼                                              
              ┌─────────────────┐                                     
              │ reports/        │                                     
              │ 2026W18.md      │  ← markdown 草稿(不入 ops_decisions)
              └─────────────────┘                                     
```

**v0 范围 7 个 Step**:
1. 上下文组装(纯函数,可单测)
2. prompt 模板 + Analyst 角色定义
3. v0.py 主流程(沿用 RunLogger + llm_client)
4. 报告输出 + index.json
5. acc CLI 扩展(`acc analyst run` / `latest`)
6. systemd timer(Sun 20:00 SGT)
7. health_check 加项 + 验收(首跑 ≥ 7/10)

**v0 不做(留下游)**:
- TG 周报推送(Phase 3 周报推送器)
- approve/reject 状态机(Phase 3 Facilitator)
- Verification 回填(Phase 4)
- `acc knowledge query` 交互查询 CLI(待决策 §5.3,可能独立 spec)

**5 项待决策**(Jonathan 答完才能开干 Step 1):
1. 时间窗口:本周 + 过去 4 周 + 上周报告 continuity?
2. 数据稀疏降级策略:仍调 LLM 让它如实标注?
3. `acc knowledge query` 进 v0 / 独立 spec / 推迟?
4. 候选决策必填字段(decision_type / subject / rationale / verification_plan / risk + evidence)?
5. health_check 加 4 / 6 / 8 项?

详见:**[PHASE2_ANALYST_SPEC.md](./PHASE2_ANALYST_SPEC.md)**(Analyst v0 spec v0-draft)

---

## 3. Phase 2 数据流(完整版)

```
┌──────────────────────────────────────────────────────┐
│ Pulse(HK)                                             │
│  ├─ /opt/pulse/version1/docs/    (13 份 .md)         │
│  ├─ Pulse PG (5 张表的 schema)                       │
│  └─ GitHub repo: project-pulse  (代码)               │
└──────┬───────────┬──────────────┬─────────────────────┘
       │           │              │                      
   ssh rsync   ssh tunnel     git clone                 
   (l2_docs)   (l2_tunnel)    (deploy key)              
       │           │              │                      
       ▼           ▼              ▼                      
┌──────────────────────────────────────────────────────┐
│ L2 服务器 accelerator-jb (柔佛)                       │
│                                                       │
│  Watcher v0 (Phase 1)         Librarian v1 (Phase 2) │
│  每日 23:00 SGT               每日 06:00 SGT         │
│   │                            │                      │
│   ▼                            ▼                      │
│  ops_metrics                  knowledge/pulse/        │
│   (Pulse 数据指标)            ├ schema/   (Phase 1)   │
│                                ├ docs/     (Phase 2)  │
│                                ├ code_index/ ← LLM    │
│                                └ extracted/  ← LLM    │
│                                                       │
│   └────────────┬───────────────┘                     │
│                ▼                                      │
│         Analyst v0  (Phase 2,周日 20:00 SGT)         │
│                │                                      │
│                ▼                                      │
│         reports/2026W18.md ← LLM 周报草稿            │
│                                                       │
│         (Phase 3:周报推送器 → TG → Facilitator       │
│          状态机 → ops_decisions 入库;               │
│          Phase 4:Craftsman 改 Pulse 代码)             │
└──────────────────────────────────────────────────────┘
```

---

## 4. Phase 2 已对齐的关键技术决策

| # | 决策 | 选择 | 出处 |
|---|---|---|---|
| LLM 模型 | 默认 | Claude Sonnet 4.6 | spec PHASE2_SPEC.md §0.5 |
| LLM 留痕 | 双轨(DB + jsonl) | migration 003 + `knowledge/_meta/llm_calls.jsonl` | spec PHASE2_SPEC.md §5(决策 1) |
| 加工增量 | sha256 manifest 比对(不依赖 mtime) | `meta_ops/librarian/change_detect.py` | spec §0.4 |
| Pulse 源 | docs 走 HK rsync,code 走 GitHub clone | 双通道,凭据分离 | spec §5(决策 2) |
| 同步过滤 | docs 全要,无黑/白名单 | spec §5(决策 3) |
| 起步范围 | 6 个 .py + 4 个 extracted 主题 | spec §5(决策 4) |
| 报告输出 | markdown,不直接落 ops_decisions | BLUEPRINT §7.6 / Analyst spec §0.3 |
| 报告周期 | 周日 20:00 SGT(timer)+ `acc analyst run`(按需) | BLUEPRINT §7.4 |
| Analyst 上下文 | 函数化组装(纯函数)+ Agent 调用(单一入口) | BLUEPRINT §7.6 |

---

## 5. Phase 2 关键文件清单(开发交付物索引)

### 5.1 已交付(Librarian v1)

| 文件 | 用途 |
|---|---|
| `meta_ops/librarian/pulse_source.py` | Step 1:HK rsync + GitHub clone |
| `meta_ops/librarian/sync_docs.py` | Step 2:docs/ 全量同步 |
| `meta_ops/librarian/change_detect.py` | Step 3:sha256 增量检测(纯函数,9 单测) |
| `meta_ops/librarian/build_code_index.py` | Step 4:code_index LLM 加工 |
| `meta_ops/librarian/build_extracted.py` | Step 5:extracted LLM 加工 |
| `meta_ops/librarian/v1.py` | Step 6:主流程编排 |
| `meta_ops/librarian/llm_client.py` | Anthropic SDK 封装 + 双轨留痕 |
| `prompts/code_index_v1.md` | code_index prompt 模板 |
| `prompts/extracted_v1.md` | extracted prompt 模板 |
| `sql/003_llm_calls.sql` | l2_llm_calls 表 migration |
| `deploy.sh` | rsync 同步代码到 /opt/accelerator/ |
| `meta_ops/cli/main.py` | `acc librarian run`(默认 v1)+ status 加 3 行新源 |
| `scripts/health_check.py` | 加 5 项 Phase 2 检查(总数 26) |

### 5.2 待交付(Analyst v0)

| 文件 | 用途 | 状态 |
|---|---|---|
| `meta_ops/analyst/context.py` | Step 1:上下文组装(纯函数) | ⬜ |
| `prompts/analyst_v0_weekly.md` | Step 2:Analyst system prompt | ⬜ |
| `meta_ops/analyst/v0.py` | Step 3:主流程 | ⬜ |
| `meta_ops/analyst/tests/test_context.py` | 单测(6+) | ⬜ |
| `reports/_meta/index.json` | 周报索引 | ⬜ |
| `meta_ops/cli/main.py`(扩展) | `acc analyst run` / `latest` | ⬜ |
| `/etc/systemd/system/acc-analyst.{service,timer}` | 周日 20:00 SGT timer | ⬜ |
| `scripts/health_check.py`(扩展) | 加 6 项(总数 32) | ⬜ |

---

## 6. Phase 2 收官标志(全图)

完成后能宣告 Phase 2 整体收官的条件:

- [x] **Librarian v1 完成**:6 个 step 全过,26/26 health_check
- [x] **6 个 code_index** 平均评分 ≥ 7/10
- [x] **4 个 extracted** 主题就位
- [ ] **Analyst v0 spec v1 定稿**(§5 五项决策已答)
- [ ] **Analyst v0 实跑**:`acc analyst run` 通过 + 首份周报评分 ≥ 7/10
- [ ] **Analyst timer enabled**:Sun 20:00 SGT 自动跑
- [ ] **health_check 总数 ≥ 32**(26 + 6 项 Analyst)
- [ ] (后续观察)Analyst 连续 4 周自动产报告 ≥ 7/10(BLUEPRINT §7.7,不卡 spec 验收)
- [ ] (可选)`acc knowledge query` 起草(待决策 3 拍板)

---

## 7. Phase 2 与下游的衔接预告

### 7.1 与 Phase 3 的接口

Phase 3 重点是"决策状态机 + TG Bot 多 Bot 协作":
- 周报推送器读 `reports/_meta/index.json` 找新报告
- 解析 reports/YYYYWW.md 的"候选决策"小节(本 Spec §4 决策 4 的 5 必填字段格式)
- 推 TG → 用户审批 → Facilitator 写 ops_decisions

**Analyst v0 留的 hook**:
- reports/ 用稳定章节标题
- index.json 维护"已推 / 未推"字段(预留)

### 7.2 与 Phase 4 的接口

Phase 4 是"Craftsman 改 Pulse 代码":
- 已批准的决策(`status='approved'` in ops_decisions)→ Craftsman 读 code_index/<相关文件>.md 找改动点 → 提 PR
- 这里 Phase 2 留下的 code_index 是 Craftsman 的"地图"——文件级,不到函数级,Phase 4 时再看是否要细化

### 7.3 反向闭环(Phase 4 完成后回到 Analyst)

- Verification 回填:Analyst 子模块每周日跑时,检查"过去 N 周决策的 verification_plan 是否生效"
- ops_playbook 演进:验证过 ≥ 3 次的决策晋升为 playbook 条目

---

## 8. 当前阻塞 / 下一步

**当前唯一阻塞**:Analyst v0 spec §5 的 5 项决策待 Jonathan 答。

**Jonathan 答完 → Claude Code 立刻**:
1. 把 PHASE2_ANALYST_SPEC.md 的 §5 改成"已确定决策(spec v1)"
2. 开 Step 1(`meta_ops/analyst/context.py` + 6 单测)
3. ...一路推到 Step 7 + 验收

预估:Step 1-7 全做完 ≈ 跟 Librarian v1 量级相当(代码 ~600 行 + 1 prompt 模板 + LLM 实跑成本 ~$0.5-1)。

---

## 9. 风险与"应该现在就关注"的事

### 9.1 Jonathan 必做的安全收尾(还没确认)
- [ ] **旋转 Anthropic API key**(2026-05-03 在 transcript 暴露过)
- [ ] **改 HK root 密码**(`1Qxcjyb!@` 同样在 transcript 暴露过)

### 9.2 Phase 2 收官后的两周观察期
- 跟踪 `l2_run_log` 看每天 06:00 librarian 是否仍 status=ok
- 跟踪 `l2_llm_calls` 看每周累计成本是否仍 ≈ $0(意味增量逻辑正确)
- 任何意外飙升(突然 $1+)需立刻排查 — 可能是 manifest 写失败导致每天全量重跑

(可考虑用 `/schedule` 每两周来一次自动审计)

---

## 10. 文档导航

| 文档 | 用途 |
|---|---|
| `CLAUDE.md` | 项目恒定知识(每次会话自动加载) |
| `PROJECT_BLUEPRINT.md` | 高层设计蓝图(§7 = Phase 2) |
| `PROGRESS_SNAPSHOT.md` | 当前进度快照(Phase 2 收官版) |
| `PHASE2_SPEC.md` | Librarian v1 开发 spec(已 v1 定稿) |
| `PHASE2_ANALYST_SPEC.md` | Analyst v0 开发 spec(v0-draft 待 Jonathan 答 5 决策) |
| **`PHASE2_OVERVIEW.md`** | **本文件:Phase 2 整体设计鸟瞰** |
| `INFRASTRUCTURE.md` | 服务器 / 网络 / 凭据基线 |

---

> 本文随 Phase 2 进展更新。Analyst v0 完成后,加一节 §11 "Phase 2 实际产出回填",对齐 BLUEPRINT §7.8。
