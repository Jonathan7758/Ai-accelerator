# Accelerator L2 — 进度快照

> **快照时间**: 2026-05-03(Phase 2 Step 1 通路就绪、代码写完、待服务器首次同步验证)
> **目的**: 下次会话可直接接上,无需重新读全部历史
> **如何使用**: 新会话打开后,先读 `CLAUDE.md` → 本文件 → `PHASE2_SPEC.md` → 决定下一步

---

## 1. 一句话状态

**Phase 2 Step 1 通路全打通(HK rsync + GitHub deploy key),`pulse_source.py` 已写,`deploy.sh` 已写,等服务器端 deploy + 首次同步冒烟。4 个决策已落地(spec v1)。**

详细见 `PROJECT_BLUEPRINT.md §6.7 / §6.8 / §6.8.4 / §11(2026-05-02 条)` + `PHASE2_SPEC.md §5(决策 1-4 已答)`。

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

## 4. Phase 2 进展

### 4.1 Spec 状态:v1(2026-05-03)

`PHASE2_SPEC.md` — Librarian v1 升级,§5 四项决策已答(LLM 双轨留痕 / HK rsync+GitHub clone / docs 全要 / code_index 名单 Step 1 后由 Claude 提议)。

### 4.2 Step 进度

| Step | 状态 | 备注 |
|---|---|---|
| 0. 通路准备 | ✅ 通(2026-05-03) | HK `l2_docs` 用户已建 + authorized_keys 配好;GitHub `project-pulse` repo deploy key 已加;accelerator-jb `~/.ssh/config` 配好 `pulse-hk-docs` + `github-pulse` 双 alias;HK 上装了 rsync(原本没有) |
| 1. Pulse 源接入(`pulse_source.py`) | 🟡 代码写完,待服务器跑 | `meta_ops/librarian/pulse_source.py` 写好(`sync_pulse_docs` + `sync_pulse_code`,不可达时优雅返回,不抛异常) |
| 2. docs/ 全量同步 | ⬜ 未开始 | |
| 3. 增量检测 manifest(纯函数 + 单测) | ⬜ 未开始 | |
| 4. code_index/ LLM 加工 | ⬜ 未开始 | 起步名单 Step 1 跑完后 Claude 提议 |
| 5. extracted/ LLM 加工 | ⬜ 未开始 | 同上 |
| 6. 接入主流程 + acc status + 25/25 health_check | ⬜ 未开始 | |

### 4.3 Phase 1 遗留小工

| 项 | 状态 |
|---|---|
| `deploy.sh`(rsync 同步代码,排除 .env / .venv / knowledge/) | ✅ 写好(2026-05-03,本地 commit 待 push) |
| `interactions` 表接入 | 推迟到 Phase 2/3 末段决定 |

### 4.4 关键凭据 / 通道(2026-05-03 立的,后续别动)

| 资源 | 路径 / 值 |
|---|---|
| HK docs SSH key (acceleratorjb 端) | `~accelerator/.ssh/id_pulse_hk`(Phase 0 老 key,新加 l2_docs 授权) |
| HK docs 用户 | `l2_docs@43.99.0.100`,bash shell,只读 `/opt/pulse/version1/docs/` |
| GitHub deploy key | `~accelerator/.ssh/id_pulse_repo` (ed25519,2026-05-03 新生)+ GitHub repo deploy key 名 `accelerator-jb-l2-readonly`(read-only) |
| SSH alias (pulse-hk-docs) | l2_docs@43.99.0.100, IdentityFile id_pulse_hk |
| SSH alias (github-pulse) | git@github.com, IdentityFile id_pulse_repo |
| HK root 密码 | **应在 2026-05-03 后立刻 rotate**(本次走 SSHPASS 临时用,聊天里出现过) |

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
- 部署:`ssh accelerator-jb "cd /opt/accelerator-git && git pull"` → `cp` 同步(暂无 deploy.sh 封装)

---

**END OF SNAPSHOT — Phase 1 + 收尾增强圆满收官,Phase 2 蓄势待发**
