# Accelerator L2 — Phase 4 执行层 开发方案

> 给 Claude Code 的开发任务说明书
> Phase 4 总体目标:**执行层** = 把 Phase 3 已 approved 的 ops_decisions,自动落地为 **Pulse 仓库**的 git PR。
> 完成后:Jonathan 在 TG 点 ✅ + 给 rationale → `acc craftsman run --decision-id <uuid>` → Pulse 仓库收到一个新 PR。

---

## 0. 必读前置

### 0.1 在动笔前先读这些

```
/opt/accelerator/CLAUDE.md                      — 项目恒定知识(自动加载)
/opt/accelerator/docs/PROJECT_BLUEPRINT.md      — 重点读 §9(Phase 4 设计)、§4(角色边界)、§6.7-6.8(运维基线)
/opt/accelerator/docs/PHASE2_OVERVIEW.md        — Phase 2 鸟瞰,理解 ops_decisions 字段结构来源
/opt/accelerator/docs/PHASE3_SPEC.md            — Phase 3 收官 spec,理解 ops_decisions 入库轨迹
/opt/accelerator/sql/001_init_schema.sql        — ops_decisions 字段定义(Phase 4 输入源)
/opt/accelerator/meta_ops/librarian/llm_client.py — 双轨留痕入口(Phase 4 LLM 必走这里)
/opt/accelerator/knowledge/pulse/code_index/    — Pulse 代码索引(Phase 4 LLM 改 Pulse 时的上下文源)
```

### 0.2 上游已就绪(本 Spec 的起点)

- Phase 3 全部 7 Step 收官,health_check 37/37(2026-05-07)
- `ops_decisions` 表中已有 1 行 `9abd1caf…`(候选 4,decision_type='workflow_tweak',Phase 4 端到端测试样本)
- 4 个 TG bot 同进程 polling,`@acc_cra_bot` 占位等接业务 handler
- LLM 双轨留痕通道就绪(Craftsman 走 `kind='craftsman'`)
- `knowledge/pulse/code_index/` 6 文件 LLM 加工过的 Pulse 代码索引,Craftsman 在选要改的文件时可参考
- Pulse 仓库现有 read-only deploy key `accelerator-jb-l2-readonly`(Phase 2 用于读 docs / code)

### 0.3 Phase 4 设计意图

```
┌────────────────────────────────────────────────────────────────────┐
│  Phase 4 = "ops_decisions(approved) → Pulse 仓库 git PR"          │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Phase 3 末:Jonathan 在 TG ✅ → ops_decisions 入库                   │
│                          │                                          │
│                          ▼                                          │
│  人类:看一眼 ✅ 决策内容,觉得 OK 后手动跑                              │
│  $ acc craftsman run --decision-id 9abd1caf-…                       │
│                          │                                          │
│                          ▼                                          │
│  Craftsman v0(本 Spec 新):                                          │
│  ├─ 类型守门:只接 decision_type='workflow_tweak',其他类型拒          │
│  ├─ 加载 ops_decisions 行 + 关联 evidence + code_index 上下文          │
│  ├─ 调 LLM(Opus 4.7)→ 输出 unified diff                            │
│  ├─ 在 /opt/accelerator/workspace/pulse/ 切新分支 acc/<id8>-<type>   │
│  ├─ git apply patch(失败 → TG 报错 + 不 commit)                     │
│  ├─ git commit + push origin <branch>                                │
│  ├─ gh pr create(title / body 含决策 ID + rationale + verification)  │
│  ├─ 写 ops_decisions.craftsman_pr_url                                │
│  └─ @acc_cra_bot 在 acc_work_gp 群发 "决策 #X 的 PR 已开:URL"        │
│                          │                                          │
│                          ▼                                          │
│  Jonathan 在 GitHub review PR → comment / change-request / merge    │
│  (Craftsman 不能合并 — 治理边界)                                     │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

### 0.4 关键技术约束

| 约束 | 来源 / 备注 |
|---|---|
| 触发 = **手动 CLI** | Phase 4 v0,§5 决策 1 锁定。Auto trigger 留 v1 |
| 输入 = **ops_decisions 主表** | §5 决策 2;ops_decision_threads 是 Phase 3 的"决策中状态",Phase 4 不依赖 |
| Pulse 操作 = **本地 clone + read-write deploy key** | §5 决策 3.1;workspace 路径 `/opt/accelerator/workspace/pulse/` |
| Branch prefix = **`acc/`** | §5 决策 3.2 + §5 决策 D;deploy key 限定只能 push `acc/*` |
| 接受类型 = **`workflow_tweak` only** | §5 决策 4;其他类型 CLI 拒绝并提示"留待 v1" |
| LLM 模型 = **Opus 4.7** | §5 决策 B;代码改生产仓库,质量优先(成本 ≈ $0.5/PR 量级) |
| 单一 LLM 入口 | `meta_ops.librarian.llm_client.call_claude(kind='craftsman', model='claude-opus-4-7', ...)` |
| Craftsman **永不合并 PR** | CLAUDE.md §6 治理边界 |
| PR 失败时**不 commit 任何东西** | §5 决策 C;LLM 给的 patch 不能 apply 就直接 TG 报错,留 ops_decisions 不变 |
| 写 ops_decisions 字段限定 | 仅写 `craftsman_pr_url`(migration 005 新加),其他字段不动 |

### 0.5 决定性偏好(已对齐,不要改)

- **角色边界严守**:Craftsman **不自主决定改什么**(CLAUDE.md §6)。它接收一个 approved decision 当输入,**严格按照决策内容生成 patch**,不能"看到 evidence 觉得别的地方也该改顺手加上"。
- **Craftsman 不合并 PR**:这是治理点,不是流程仪式。
- **改动范围最小**:Phase 4 v0 patch 只允许改 Pulse 仓库内的文件,不能新增 .yml / CI 配置 / 新依赖。如 patch 含此类改动,Craftsman 拒绝并报错。

---

## 1. Phase 4 工作分解

按依赖顺序,6 个 Step。前一步验收通过才进下一步。

```
Step 1: 基础设施(read-write deploy key + workspace clone + push 验证)
Step 2: DB migration 005(ops_decisions.craftsman_pr_url 字段)
Step 3: Craftsman 主流程骨架(acc CLI + 类型守门 + 单测)
Step 4: LLM patch 生成(prompt 模板 + Opus 调用 + diff 解析)
Step 5: git workflow(apply / commit / push / PR / TG notify / 状态回写)
Step 6: 验收(端到端跑候选 4 9abd1caf…)+ health_check 加 5 项(37 → 42)
```

---

## 2. Step 详细

### Step 1: 基础设施

**目标**:L2 能从 accelerator 用户身份 `git push origin acc/test-branch` 到 Pulse 仓库,且只能 push `acc/*` 分支。

**动作**:

1. 在 accelerator-jb 服务器以 accelerator 用户生成新 SSH key:
   ```
   ssh-keygen -t ed25519 -f ~/.ssh/id_pulse_repo_rw -C "accelerator-jb-l2-craftsman"
   ```
2. 把公钥加到 Pulse GitHub 仓库的 Deploy keys(**勾 "Allow write access"**)
3. SSH config 加 alias:
   ```
   Host github-pulse-rw
       HostName github.com
       User git
       IdentityFile ~/.ssh/id_pulse_repo_rw
   ```
4. clone Pulse:
   ```
   mkdir -p /opt/accelerator/workspace
   cd /opt/accelerator/workspace
   git clone github-pulse-rw:<owner>/<pulse>.git pulse
   ```
5. **branch protect** 在 GitHub 上配置:
   - main 分支 require PR review,禁止 direct push
   - 这样即使 Craftsman 拿到 write key,也无法越权合并 main(治理 by GitHub policy,不依赖 Craftsman 自律)
6. 验证:在 workspace clone 里 `git push origin acc/test-permission`(空分支),应成功;然后 `git push origin main`(就算尝试),应被 GitHub 拒。

**验收**:
- `ls -la ~accelerator/.ssh/id_pulse_repo_rw{,.pub}` 存在,perm 600/644
- `sudo -u accelerator git -C /opt/accelerator/workspace/pulse rev-parse HEAD` 成功
- `sudo -u accelerator git -C /opt/accelerator/workspace/pulse push origin acc/test-permission` 成功
- 测试完删掉测试分支:`git push origin --delete acc/test-permission`

---

### Step 2: DB migration 005

**目标**:给 `ops_decisions` 加 `craftsman_pr_url` 字段。

**migration `sql/005_craftsman_pr_url.sql`**:

```sql
ALTER TABLE ops_decisions
  ADD COLUMN craftsman_pr_url TEXT,
  ADD COLUMN craftsman_status TEXT
    CHECK (craftsman_status IS NULL OR craftsman_status IN
      ('not_started', 'patch_failed', 'pr_open', 'pr_merged', 'pr_closed'));

-- 历史 1 行回填:9abd1caf… (Phase 3 收官时入库的 workflow_tweak,Phase 4 v0 测试样本)
-- 不预填 status, 留 NULL = 'not_started' 由 Craftsman 第一次 run 写入

CREATE INDEX idx_ops_decisions_craftsman_status
  ON ops_decisions(craftsman_status)
  WHERE craftsman_status IS NOT NULL;

INSERT INTO schema_versions (version, description)
VALUES ('005', 'Add craftsman_pr_url + craftsman_status (Phase 4)');
```

**验收**:
- `\d ops_decisions` 显示 2 个新列
- `SELECT version FROM schema_versions WHERE version='005'` 返 1 行

---

### Step 3: Craftsman 主流程骨架

**目标**:`acc craftsman run --decision-id <uuid>` 能跑通"读 decision + 类型守门 + 走到 LLM 调用前"。LLM 调用本身在 Step 4 接。

**新文件**:

- `meta_ops/craftsman/__init__.py`(空)
- `meta_ops/craftsman/v0.py`:主流程,函数 `run_craftsman_v0(decision_id: str, dry_run: bool=False) -> dict`
  - 加载 ops_decisions 行(若不存在 → 错误 "decision not found")
  - 类型守门:`if decision_type != 'workflow_tweak': raise SkipDecision("v0 only accepts workflow_tweak; got {decision_type}")`
  - 状态守门:`if craftsman_status not in (None, 'patch_failed'): raise SkipDecision("...")` — 防重复跑(已开 PR 的不让重跑)
  - 当前 Step 走到此处后返 `{"status": "ok_dryframe", "decision_id": ..., "decision_type": ...}`
- `meta_ops/craftsman/tests/test_v0.py`:
  - `test_rejects_non_workflow_tweak_type` — 给个 dummy decision dict 类型 'schema_change',应抛 SkipDecision
  - `test_rejects_already_in_progress_pr` — craftsman_status='pr_open' 应抛
  - `test_decision_not_found` — 不存在的 uuid 抛 not found

**acc CLI 扩展** `meta_ops/cli/main.py`:

```python
@cli.group()
def craftsman():
    """Craftsman 操作(Phase 4)。"""
    pass

@craftsman.command('run')
@click.option('--decision-id', required=True, help='ops_decisions.id (UUID)')
@click.option('--dry-run', is_flag=True, help='不调 LLM 不 push,仅类型守门 + 加载上下文')
def craftsman_run(decision_id, dry_run):
    """手动跑 Craftsman 处理一条 approved decision(Phase 4 v0)。"""
    ...
```

**验收**:
- 单测 3/3 过
- `acc craftsman run --decision-id 9abd1caf…` 在当前 Step 走通(类型守门过,返 ok_dryframe)
- `acc craftsman run --decision-id 9abd1caf… --dry-run` 同样

---

### Step 4: LLM patch 生成

**目标**:Craftsman 调 Opus 4.7,输出可 apply 的 unified diff。

**新文件**:

- `prompts/craftsman_v0.md` system prompt 模板:
  - 角色定义:你是 Accelerator Craftsman,基于一条已 approved 的运营决策生成 Pulse 仓库的最小代码修改
  - 输入说明:decision 6 字段 + evidence 数组 + 相关 code_index 文件
  - **强约束输出格式**:仅 unified diff,不带任何说明文字,以 `diff --git ...` 开头
  - 范围限制:
    - 只能改 Pulse 仓库内已存在的 .py 文件
    - 不能新增文件 / 删除文件 / 改 yml / 改 requirements
    - 单 PR 改动行数 ≤ 200(粗略,LLM 自我检查)
  - 风险提示:被改的代码如有副作用,在 diff 末尾加 `# CRAFTSMAN-NOTE: <风险>` 注释行(评审者会看)
- `meta_ops/craftsman/llm.py`:
  - `build_craftsman_prompt(decision: dict, code_index: dict[str, str]) -> tuple[str, str]` 返 (system, user)
  - `call_craftsman_llm(decision: dict) -> dict` 调 llm_client.call_claude(kind='craftsman', model='claude-opus-4-7', ...)
- `meta_ops/craftsman/diff.py`:
  - `validate_unified_diff(text: str) -> tuple[bool, str | None]` 纯函数,粗校(以 `diff --git` 开头 / 含 `@@` hunk header / 行数限制 / 文件路径不出 Pulse repo)
  - 单测覆盖

**code_index 加载**:Craftsman 加载 evidence 数组里 `type='code'` 的引用,从 `/opt/accelerator/knowledge/pulse/code_index/version1/` 读对应 .md(LLM 加工过的代码摘要,体积可控)。如 evidence 里没 type='code',加载 `extracted/` 主题或 `docs/` 兜底。

**验收**:
- `meta_ops/craftsman/tests/test_diff.py` 6/6 过(覆盖 valid / invalid / 越界 / 空 diff)
- 实跑:`acc craftsman run --decision-id 9abd1caf… --dry-run`(此时 dry-run 含 LLM 调用但不 apply)输出 LLM 给的 diff 文本到 stdout
- LLM 留痕:`l2_llm_calls` 加 1 行 `kind='craftsman' model='claude-opus-4-7' ok=true`

---

### Step 5: git workflow + PR + TG notify + 状态回写

**目标**:dry_run=False 时,完整跑通"clone → branch → apply → commit → push → PR → TG → 写 craftsman_pr_url"。

**新文件**:

- `meta_ops/craftsman/git_ops.py`:
  - `prepare_branch(repo_path: str, decision_id: str, decision_type: str) -> str` — `git fetch + checkout main + pull + checkout -b acc/<id8>-<type>`,返新分支名
  - `apply_patch(repo_path: str, diff_text: str) -> tuple[bool, str | None]` — 写 .patch tmp + `git apply --check` 后 `git apply`
  - `commit_and_push(repo_path: str, branch: str, decision: dict) -> str` — `git commit -m "..." && git push origin <branch>`,返 commit sha
- `meta_ops/craftsman/pr.py`:
  - `create_pr(branch: str, decision: dict) -> str` — 调 `gh pr create --base main --head <branch> --title ... --body ...`,返 PR URL
  - PR title:`acc/<id8>: <subject>`
  - PR body:模板 `prompts/craftsman_pr_v0.md`(简短 markdown,含决策 ID / rationale / verification_plan / 风险点 / "由 Accelerator Craftsman 自动生成,Jonathan 请审查")
- `meta_ops/craftsman/notify.py`:
  - `send_pr_notice_to_tg(pr_url: str, decision: dict)` — 用 `TG_BOT_TOKEN_CRAFTSMAN` + `TG_ADMIN_CHAT_ID`,发"🔨 Craftsman: 决策 #X 的 PR 已开:URL"
- `v0.py` 主流程把以上串起来:
  - 失败任一步:写 craftsman_status='patch_failed',TG 群发"❌ Craftsman: 决策 #X 失败:<原因>",返 status='failed'
  - 全过:写 craftsman_status='pr_open' + craftsman_pr_url,返 status='ok'

**验收**:
- 单测覆盖 git_ops 的纯字符串拼接部分(branch 名 / commit msg / PR title)
- 实跑候选 4(`9abd1caf…`)端到端:
  - 收到 PR(在 Pulse 仓库 GitHub 页面看)
  - TG 群收到 craftsman bot 通知
  - `SELECT craftsman_pr_url, craftsman_status FROM ops_decisions WHERE id='9abd1caf-…'` 都填了

---

### Step 6: 验收 + health_check

**端到端测试场景**(候选 4 已 approved 在等):

1. `acc craftsman run --decision-id 9abd1caf-4b63-4500-bfea-e6ac6cb37a66 --dry-run` 看 LLM 给的 diff,人审
2. 觉得 OK → `acc craftsman run --decision-id 9abd1caf-…`(去掉 --dry-run)
3. PR 在 Pulse 仓库被开
4. TG 群 craftsman bot 发链接
5. `acc craftsman status --decision-id 9abd1caf-…` 显示 craftsman_status='pr_open' + URL
6. Jonathan 上 GitHub review,觉得不对就 close,觉得 OK 就合并

**health_check 加 5 项**(37 → **42**):

| # | 检查 | 通过条件 |
|---|---|---|
| 1 | migration 005 应用 + craftsman_pr_url / craftsman_status 字段存在 | 单 query 复合 `SELECT 'OK' WHERE schema_versions has 005 AND ops_decisions has columns` |
| 2 | Pulse workspace clone 存在 + 是 git workdir | `/opt/accelerator/workspace/pulse/.git` exists |
| 3 | id_pulse_repo_rw key 存在 + perm 600 | `~accelerator/.ssh/id_pulse_repo_rw` exists, mode 600 |
| 4 | TG_BOT_TOKEN_CRAFTSMAN 非空(沿用 Phase 3 §Step 7 第 5 项的检查模式) | env 单项 |
| 5 | craftsman_v0 prompt 文件存在 | `/opt/accelerator/prompts/craftsman_v0.md` exists |

**Step 6 完成标志**:
- 端到端跑过候选 4(成功开 PR or 失败但 craftsman_status 写正确)
- health_check 42/42

---

## 3. Phase 4 完成标志

- [ ] Step 1-6 全过
- [ ] 候选 4(9abd1caf…)在 Pulse 仓库有真 PR(成功或失败都算"流程跑通")
- [ ] LLM 留痕 `l2_llm_calls` 至少 1 行 `kind='craftsman'`
- [ ] health_check 总数从 37 升到 **42**
- [ ] 文档 `PHASE4_OVERVIEW.md` 写完(收官鸟瞰,类似 PHASE2_OVERVIEW.md)

---

## 4. 不在本 Spec 范围(留给后续)

| 项 | 留给 |
|---|---|
| Auto trigger(systemd timer 自动扫 ops_decisions 起 Craftsman) | Phase 4.1 / Phase 5 |
| 接其他 decision_type(`prompt_change` / `parameter_tweak` 等) | Phase 4.1 |
| Verification 回填 ops_decisions.verification_result | Phase 5(Verifier 角色) |
| Multi-file refactor / new file / 改 schema | 永远不让 Craftsman 自动做(治理边界,人活) |
| ops_playbook 自动晋升(决策被验证 ≥ 3 次 → playbook) | Phase 5 |
| Craftsman 接受 reviewer 意见后修 PR(回灌 LLM 改 patch) | Phase 4.1 |
| PR 合并后通知 TG / 写 craftsman_status='pr_merged' | Phase 4.1(GitHub webhook 接入) |

---

## 5. 已确定决策(spec v1, 2026-05-07 Jonathan 拍板)

> 4 个核心决策已对齐(触发 = 手动 / 输入 = ops_decisions / Pulse = 本地 clone + RW key / 类型 = workflow_tweak only)。下面是写细节时的 5 个小决策点,**全部按 v0-draft 推荐定稿**(2026-05-07)。

### 决策 A:PR URL 存哪?

| 选项 | 说明 |
|---|---|
| **A.新加 `ops_decisions.craftsman_pr_url` + `craftsman_status` 字段**(migration 005)| 字段语义清晰,index 友好 |
| B.塞已有 `verification_result` jsonb 字段(`{"craftsman": {...}}`)| 不加 schema,但混合语义难 query |

**推荐 A**(已写进 §2 Step 2)。理由:`verification_result` 是给 Verifier(Phase 5)用的;Craftsman 状态是 Phase 4 自己的事,不该污染。新加 2 列总成本 = 1 个 migration + 1 个 index,不大。

### 决策 B:Craftsman LLM 模型

| 选项 | 模型 | 单次成本 | 推荐 |
|---|---|---|---|
| **A.Opus 4.7** | `claude-opus-4-7` | $0.3-0.8/PR(8K input + 2K output 量级,Opus 是 $15/$75 per MTok)| ✅ |
| B.Sonnet 4.6 | `claude-sonnet-4-6` | $0.05-0.15/PR | × |

**推荐 A**(已写进 §0.4)。理由:Phase 4 v0 跑量低(每周 ≤ 5 个 PR),代码质量比 5x 成本重要;Opus 在多文件 patch 准确度上明显胜 Sonnet。

### 决策 C:LLM 给的 patch 失败时的重试策略

| 选项 | 说明 |
|---|---|
| **A.不重试,直接报错**(craftsman_status='patch_failed')| 简单,失败明显 |
| B.重试 1 次:把 git apply error 喂回 LLM,让它修 patch 再试 | 自愈但加复杂度 + 双倍成本 |
| C.让 Jonathan 决定(CLI flag `--retry-on-fail`,默认 off)| 折中 |

**推荐 A**。理由:Phase 4 v0 求精不求多;failed 直接 TG 报"Craftsman 搞不定决策 #X,请人改",Jonathan 可以重新评估(也许该升 prompt 而不是重试);v1.1 再加 B 的"对话改 patch"。

### 决策 D:Branch 命名

| 选项 | 例 |
|---|---|
| **A.`acc/<id8>-<decision_type>`** | `acc/9abd1caf-workflow_tweak` |
| B.`acc/decision-<id8>` | `acc/decision-9abd1caf` |
| C.`acc/<id8>-<subject_slug>` | `acc/9abd1caf-content-matrix-cross-links` |

**推荐 A**。短 + 机器友好 + 含类型方便 reviewer 一眼分辨。subject 长度不一,slug 化复杂。

### 决策 E:PR description 是否嵌完整 evidence 数组?

| 选项 | 说明 |
|---|---|
| **A.嵌**,展开成 markdown 列表(可能 50-200 行)| 评审者一处可见全部上下文 |
| B.不嵌,只放摘要 + 决策 ID | PR 简洁,evidence 留 ops_decisions DB 查 |
| C.折叠 `<details>` 标签嵌 | GitHub 默认折,展开可见;两全 |

**推荐 C**。理由:GitHub 渲染 `<details>` 友好,评审默认看摘要,需要时点开看完整 evidence。

---

## 6. 工作量与排期估计

> 不给小时数。

依赖关系:Step 1 → 2 → 3 → 4 → 5 → 6。

- Step 1(基础设施)+ Step 2(migration)是基础,大概 1/4 工作量;**Step 1 涉及人手动操作 GitHub Deploy keys 页**
- Step 3(框架)+ Step 4(LLM)是主体,大概 1/2;**Step 4 是质量瓶颈**(prompt 设计 + diff 解析鲁棒性)
- Step 5(git workflow + PR + TG)+ Step 6(验收)收尾,1/4

**LLM 实跑**:Step 4 dry-run 测 1-3 次 + Step 6 端到端测 1 次,每次 ≈ $0.3-0.8。Phase 4 整体 LLM 预算 ≈ $2。

---

## 7. 风险登记

| 风险 | 缓解 |
|---|---|
| LLM 给的 patch 不能 apply / hunk 不匹配 | Step 4 `validate_unified_diff` 粗校 + Step 5 `git apply --check` 双关;失败直接 TG 报错不 commit |
| LLM "顺手"改 spec 之外的文件 / 加新依赖 | system prompt 强约束 + diff 解析时检查文件路径在 Pulse 内 + 行数上限 200 |
| read-write deploy key 泄露 | 限定只能 push `acc/*` 分支(GitHub branch protect);main 永远 review-required;泄露发现立刻 GitHub 删 key + 重生 |
| Pulse 仓库 main 已变,Craftsman 基于旧 main 生成的 patch 跟实际冲突 | Step 5 `prepare_branch` 强制 `git fetch + pull main` 后才 checkout 新分支;若 patch 仍冲突,直接报 patch_failed |
| Craftsman 跑到一半失败,workspace 留下脏分支 | 失败时 `git checkout main && git branch -D acc/*`(清理在 v0.py finally 块);Step 5 实现要带 |
| LLM 看不到关键代码,patch 改错地方 | code_index 加载 + 必要时让 evidence 包含 `code:src/...` 引用(Phase 2 Analyst 已有此能力) |
| Opus API 延迟 / 配额限制 | 单次跑同步等;失败标 patch_failed;v1.1 加 timeout + 重试 |
| `gh pr create` 需要 GitHub CLI 认证 | Step 1 加一项:`sudo -u accelerator gh auth login` (或用 `GH_TOKEN` env);verify 在 Step 5 实现前 |

---

## 8. 与上游(Phase 3 Facilitator)的接口约定

- **必须**:Phase 3 写 `ops_decisions` 时 `status='active'` + 6 主字段齐全(已锁,Phase 3 §9 已约束)
- **必须**:`evidence` 数组 JSON 格式合规(Phase 3 已通过 parser strip 反引号修复)
- **新增依赖**(Phase 4 起):Craftsman 假设 `decision_type` 值不带反引号 / 引号(Phase 3 Step 6 顺手已修)
- **不破坏**:Phase 4 不改 ops_decision_threads 表,不动 Phase 3 任何代码

---

## 9. 与下游(Phase 5 Verifier)的接口约定

- **预留**:`ops_decisions.craftsman_status` 提供给 Verifier 当过滤条件(只验 `pr_merged` 的决策)
- **预留**:Phase 5 Verifier 在 verification_plan 时间窗结束后,跟 ops_metrics 比对 → 写 `verification_result` jsonb + 改 `status`('active' / 'reverted')
- **不预留**:Phase 4 不动 ops_playbook(那是 Phase 5+ 自动晋升)

---

> **本 Spec 状态**:**v1 定稿**(2026-05-07)。§5 5 个小决策全部按推荐锁定(A=新字段 / B=Opus 4.7 / C=不重试 / D=`acc/<id8>-<type>` / E=`<details>` 折叠)。可启 Step 1。
