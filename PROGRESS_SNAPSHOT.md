# Accelerator L2 — 进度快照

> **快照时间**: 2026-05-02(Phase 1 完成 + Phase 1 收尾增强落实 + Phase 2 spec v0-draft)
> **目的**: 下次会话可直接接上,无需重新读全部历史
> **如何使用**: 新会话打开后,先读 `CLAUDE.md` → 本文件 → `PHASE2_SPEC.md` → 决定下一步

---

## 1. 一句话状态

**Phase 0 + Phase 1 完成 ✅ + 收尾增强落实 ✅。Phase 2(Librarian v1)spec 已起草,待 Jonathan 拍板 4 个决策点后开干。**

详细见 `PROJECT_BLUEPRINT.md §6.7 / §6.8 / §6.8.4 / §11(2026-05-02 条)`。

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

## 4. Phase 2 起点(等开干)

### Spec 文件

`PHASE2_SPEC.md`(v0-draft, 289 行)— **只覆盖 Librarian v1**;Analyst v0 留单独 spec(Librarian v1 验收后再起草)。

### 6 个 Step 设计(已写入 spec)

1. Pulse 源接入(docs + code repo 在 L2 端如何可达)
2. docs/ 全量同步(无 LLM 路径)
3. 增量检测 manifest(纯函数,可单测)
4. code_index/ LLM 加工
5. extracted/ LLM 加工
6. 接入主流程 + acc status 反映新源 + 验收(目标 25/25 health_check)

### Jonathan 待决策(开 Step 1 前必须答)

1. **LLM 调用留痕落哪儿** — DB 表(migration 003) / 文件 jsonl / 双轨
2. **Pulse 源接入方式** — Pulse docs 物理位置 + Pulse 代码仓库 URL + read-only 凭据
3. **docs/ 同步过滤规则** — 黑/白名单
4. **code_index / extracted v1 起步范围** — 5+ 个关键 .py + 4+ 个业务概念主题(具体名单)

### 仍未做的 Phase 1 遗留小工

- 补 `/opt/accelerator/deploy.sh`(Phase 2 启动前再做,避免 Phase 2 期间手动 cp 出事)
- `interactions` 表接入(Phase 2/3 阶段决定,待 SCHEMA_NOTES.md §5.3 对齐)

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
