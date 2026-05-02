# Accelerator L2 — 进度快照

> **快照时间**: 2026-05-02 (Phase 1 完成)
> **目的**: 下次会话可直接接上,无需重新读全部历史
> **如何使用**: 新会话打开后,先读 `CLAUDE.md` → 本文件 → 决定下一步

---

## 1. 一句话状态

**Phase 0 + Phase 1 全部完成 ✅。Phase 2(分析层)待启动。**

详细完成情况见 `PROJECT_BLUEPRINT.md §6.7 / §6.8 / §11(2026-05-02 条)`。

---

## 2. Phase 1 收尾状态(2026-05-02)

| 项 | 数据 |
|---|---|
| health_check 通过 | 21 / 21(Phase 0 14 + Phase 1 7) |
| Pulse Connector 单元测试 | 4 / 4 |
| ops_metrics 行数 | 11(article=5, topic=6) |
| l2_run_log | librarian ok×3 / watcher partial×5(partial 是 deferred 标记,非故障) |
| knowledge/pulse/schema/*.md | 5 张表 mirror 齐全 |
| systemd timer | acc-librarian.timer (06:00 SGT) / acc-watcher.timer (23:00 SGT) 双 enabled |
| `acc` CLI | 4 个子命令(status / librarian / watcher / backfill)就位 |
| 部署管道 | `/opt/accelerator-git/`(HTTPS clone)+ `cp` 同步到 `/opt/accelerator/` |
| docs/ 目录 | 5 个文件 symlink 到 git workdir |

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
├── meta_ops/
│   ├── common/{db,run_log,logging_config}.py
│   ├── pulse_connector/{connector,models}.py + tests/
│   ├── librarian/v0.py
│   ├── watcher/v0.py
│   └── cli/main.py
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

## 4. Phase 1 期间留下的"小工" / 未来要做

来自 BLUEPRINT §11 (2026-05-02 条):

1. 补 `/opt/accelerator/deploy.sh`(idempotent rsync + systemd reload)— Phase 2 启动前
2. `acc status` 状态视图区分 deferred / degraded / failed(详见 §6.8.4)
3. interactions 表接入(推迟到 Phase 2/3 启动前对齐 SCHEMA_NOTES.md §5.3)

---

## 5. 跨会话固化的协作约束(已对齐)

- **遇错停 + 报告 + 等决策**(Phase 1 多次救场)
- **Spec 没说不做**(包括"看起来更好的小改进")
- **数据契约对齐**:写"读外部系统"代码前,先 dump 真实 schema(已成蓝图 §10.10)
- **复杂命令不写多层引号 heredoc**:本地脚本 → scp → server 端独立调用
- **secret 永远在 .env**(600 perm, owner accelerator)
- **schema 改动必走 migration 文件**,不直接 ALTER

---

## 6. 下次会话开局动作

1. 读 `CLAUDE.md`(自动加载)→ 本文件 → `PHASE2_SPEC.md`(还没写,要先开)
2. 决定:Phase 2 启动前,要不要先把"小工 1/2"清掉?
3. Phase 2 第一步多半是 Librarian v1 升级(加 `docs/` / `code_index/` / `extracted/`)— 详见 BLUEPRINT §7

---

## 7. SSH / 环境关键事实

- SSH host alias: `accelerator-jb`(默认登录 root,业务命令必须 `sudo -u accelerator`)
- `.env` owned by accelerator (mode 600);root 也可读
- 两个数据库连接:
  - 本机:`psql -h 127.0.0.1 -U acc_app -d accelerator`(`ACC_DB_PASSWORD` 在 .env)
  - Pulse 经隧道:`psql -h 127.0.0.1 -p 5433 -U l2_reader -d pulse`(`HK_DB_PASSWORD` 在 .env)
- 部署:`ssh accelerator-jb "cd /opt/accelerator-git && git pull && cp -r ... /opt/accelerator/"`(暂无脚本封装)

---

**END OF SNAPSHOT — Phase 1 圆满收官,Phase 2 起点干净**
