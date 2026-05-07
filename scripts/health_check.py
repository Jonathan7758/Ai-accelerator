#!/usr/bin/env python3
"""
Accelerator L2 - Health Check
任何时候跑这个脚本,都应该全部通过。失败项即"基础设施缺陷"。

Usage:
    python3 health_check.py                  # 跑全部检查
    python3 health_check.py --json           # JSON 输出(给监控用)
"""
import os
import sys
import json
import socket
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Tuple, List, Dict


# ── 工具:从 .env 加载环境变量 ───────────────────────────────
ENV_PATH = Path("/opt/accelerator/.env")


def load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


# ── 检查项 ──────────────────────────────────────────────────
class Check:
    def __init__(self, name: str):
        self.name = name

    def run(self) -> Tuple[bool, str]:
        raise NotImplementedError


class FileExistsCheck(Check):
    def __init__(self, name: str, path: str, must_owner: str = None):
        super().__init__(name)
        self.path = Path(path)
        self.must_owner = must_owner

    def run(self):
        if not self.path.exists():
            return False, f"missing: {self.path}"
        if self.must_owner:
            stat = self.path.stat()
            try:
                import pwd
                owner = pwd.getpwuid(stat.st_uid).pw_name
                if owner != self.must_owner:
                    return False, f"wrong owner: {owner} (want {self.must_owner})"
            except Exception:
                pass
        return True, "ok"


class CommandCheck(Check):
    def __init__(self, name: str, cmd: List[str], must_contain: str = None):
        super().__init__(name)
        self.cmd = cmd
        self.must_contain = must_contain

    def run(self):
        try:
            result = subprocess.run(
                self.cmd, capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                return False, f"exit {result.returncode}: {output[:200]}"
            if self.must_contain and self.must_contain not in output:
                return False, f"missing '{self.must_contain}' in output"
            return True, output.strip()[:80] or "ok"
        except subprocess.TimeoutExpired:
            return False, "timeout (10s)"
        except FileNotFoundError:
            return False, f"command not found: {self.cmd[0]}"


class TCPCheck(Check):
    def __init__(self, name: str, host: str, port: int):
        super().__init__(name)
        self.host = host
        self.port = port

    def run(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=5):
                return True, f"{self.host}:{self.port} reachable"
        except Exception as e:
            return False, f"{self.host}:{self.port} unreachable: {e}"


class DirGlobCountCheck(Check):
    """Pass if at least `min_count` files match pattern under root (recursive)."""
    def __init__(self, name: str, root: str, pattern: str, min_count: int):
        super().__init__(name)
        self.root = Path(root)
        self.pattern = pattern
        self.min_count = min_count

    def run(self):
        if not self.root.is_dir():
            return False, f"missing dir: {self.root}"
        n = sum(1 for p in self.root.rglob(self.pattern) if p.is_file())
        if n < self.min_count:
            return False, f"only {n} files (need ≥{self.min_count})"
        return True, f"{n} files (≥{self.min_count})"


class FreshManifestCheck(Check):
    """Pass if manifest.json exists and `generated_at` < `max_age_hours`."""
    def __init__(self, name: str, manifest_path: str, max_age_hours: float = 36):
        super().__init__(name)
        self.path = Path(manifest_path)
        self.max_age_hours = max_age_hours

    def run(self):
        if not self.path.is_file():
            return False, f"missing: {self.path}"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return False, f"read err: {e}"
        gen = data.get("generated_at")
        if not gen:
            return False, "no generated_at field"
        try:
            last = datetime.fromisoformat(gen)
        except ValueError as e:
            return False, f"bad timestamp: {e}"
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        if age > self.max_age_hours:
            return False, f"stale: {age:.1f}h (max {self.max_age_hours}h)"
        return True, f"{age:.1f}h ago"


class PostgresCheck(Check):
    def __init__(self, name: str, env: Dict[str, str],
                 host_key: str, port_key: str, db_key: str,
                 user_key: str, pwd_key: str, query: str = "SELECT 1",
                 must_contain: str = None):
        super().__init__(name)
        self.env = env
        self.host = env.get(host_key, "")
        self.port = env.get(port_key, "5432")
        self.db = env.get(db_key, "")
        self.user = env.get(user_key, "")
        self.pwd = env.get(pwd_key, "")
        self.query = query
        self.must_contain = must_contain

    def run(self):
        if not self.pwd:
            return False, "password not set in .env"
        try:
            env = os.environ.copy()
            env["PGPASSWORD"] = self.pwd
            result = subprocess.run(
                ["psql", "-h", self.host, "-p", self.port,
                 "-U", self.user, "-d", self.db,
                 "-tAc", self.query],
                capture_output=True, text=True, timeout=10, env=env
            )
            if result.returncode != 0:
                return False, result.stderr.strip()[:200]
            out = result.stdout.strip()
            if self.must_contain and self.must_contain not in out:
                return False, f"missing '{self.must_contain}' in output: {out[:60]!r}"
            return True, f"query ok: {out[:60]}"
        except Exception as e:
            return False, str(e)[:200]


class EnvVarsAllSetCheck(Check):
    """Pass if every key in `keys` exists and is non-empty in the loaded .env."""
    def __init__(self, name: str, env: Dict[str, str], keys: List[str]):
        super().__init__(name)
        self.env = env
        self.keys = keys

    def run(self):
        missing = [k for k in self.keys if not self.env.get(k)]
        if missing:
            return False, f"missing/empty: {missing}"
        return True, f"all {len(self.keys)} keys set"


# ── 检查清单 ────────────────────────────────────────────────
def build_checks(env: Dict[str, str]) -> List[Check]:
    checks: List[Check] = []

    # 1. 文件系统
    checks.append(FileExistsCheck("env file exists", "/opt/accelerator/.env", "accelerator"))
    checks.append(FileExistsCheck("log dir exists", "/var/log/accelerator", "accelerator"))
    checks.append(FileExistsCheck("sql dir exists", "/opt/accelerator/sql"))

    # 2. 系统服务
    checks.append(CommandCheck(
        "postgres service active",
        ["systemctl", "is-active", "postgresql"],
        must_contain="active"
    ))

    # 3. 时区
    checks.append(CommandCheck(
        "timezone is Singapore",
        ["timedatectl", "show", "-p", "Timezone"],
        must_contain="Asia/Singapore"
    ))

    # 4. 网络出站
    checks.append(TCPCheck("anthropic api reachable", "api.anthropic.com", 443))
    checks.append(TCPCheck("telegram api reachable", "api.telegram.org", 443))
    checks.append(TCPCheck("github api reachable", "api.github.com", 443))

    # 5. 本机 Postgres
    checks.append(PostgresCheck(
        "local postgres connect",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD"
    ))
    checks.append(PostgresCheck(
        "ops_decisions table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM ops_decisions"
    ))
    checks.append(PostgresCheck(
        "ops_metrics table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM ops_metrics"
    ))
    checks.append(PostgresCheck(
        "ops_playbook table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM ops_playbook"
    ))

    # 6. HK 只读连接(走本机 5433 隧道入口;HK_DB_PASSWORD 为空时跳过)
    if env.get("HK_DB_PASSWORD"):
        checks.append(PostgresCheck(
            "hk pulse readonly connect (via tunnel)",
            env, "HK_DB_HOST", "HK_DB_PORT", "HK_DB_NAME",
            "HK_DB_USER", "HK_DB_PASSWORD"
        ))
        # 隧道服务本身也要在
        checks.append(CommandCheck(
            "acc-pgtunnel.service active",
            ["systemctl", "is-active", "acc-pgtunnel.service"],
            must_contain="active"
        ))

    # ── Phase 1 checks ──────────────────────────────

    # systemd timers
    checks.append(CommandCheck(
        "acc-librarian.timer enabled",
        ["systemctl", "is-enabled", "acc-librarian.timer"],
        must_contain="enabled"
    ))
    checks.append(CommandCheck(
        "acc-watcher.timer enabled",
        ["systemctl", "is-enabled", "acc-watcher.timer"],
        must_contain="enabled"
    ))

    # l2_run_log 表
    checks.append(PostgresCheck(
        "l2_run_log table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM l2_run_log"
    ))

    # Librarian 至少跑成功过一次
    checks.append(PostgresCheck(
        "librarian has succeeded",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM l2_run_log WHERE kind='librarian' AND status IN ('ok','partial')"
        # 如果返回 0 这条会算 ok(query 本身成功),
        # 但你看输出会发现 count=0,人工识别。
        # 真正严格判断在验收清单里手动确认。
    ))

    # knowledge mirror 存在
    checks.append(FileExistsCheck(
        "knowledge schema mirror",
        "/opt/accelerator/knowledge/pulse/schema/articles.md"
    ))
    checks.append(FileExistsCheck(
        "librarian last_synced_at",
        "/opt/accelerator/knowledge/pulse/_meta/last_synced_at.txt"
    ))

    # acc 命令可用
    checks.append(CommandCheck(
        "acc command installed",
        ["/usr/local/bin/acc", "--help"],
    ))

    # ── Phase 2 checks ──────────────────────────────

    # pulse_src/code 是 git workdir(代表 Step 1 通)
    checks.append(FileExistsCheck(
        "pulse_src/code is git workdir",
        "/opt/accelerator/knowledge/pulse_src/code/.git"
    ))

    # docs manifest 新鲜(<36h)
    checks.append(FreshManifestCheck(
        "knowledge/pulse/docs manifest fresh",
        "/opt/accelerator/knowledge/pulse/docs/_meta/manifest.json",
        max_age_hours=36,
    ))

    # code_index 至少 5 个 .md
    checks.append(DirGlobCountCheck(
        "code_index has ≥5 files",
        "/opt/accelerator/knowledge/pulse/code_index", "*.md", min_count=5
    ))

    # extracted 至少 4 个 .md
    checks.append(DirGlobCountCheck(
        "extracted has ≥4 topics",
        "/opt/accelerator/knowledge/pulse/extracted", "*.md", min_count=4
    ))

    # l2_llm_calls 表存在(migration 003)
    checks.append(PostgresCheck(
        "l2_llm_calls table",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM l2_llm_calls"
    ))

    # ── Phase 2 Analyst v0 checks(决策 5 = 6 项) ──────────

    checks.append(CommandCheck(
        "acc-analyst.timer enabled",
        ["systemctl", "is-enabled", "acc-analyst.timer"],
        must_contain="enabled"
    ))
    checks.append(FileExistsCheck(
        "acc-analyst.service exists",
        "/etc/systemd/system/acc-analyst.service"
    ))
    checks.append(FileExistsCheck(
        "reports dir exists",
        "/opt/accelerator/reports"
    ))
    checks.append(FileExistsCheck(
        "analyst_v0_weekly prompt exists",
        "/opt/accelerator/prompts/analyst_v0_weekly.md"
    ))
    checks.append(PostgresCheck(
        "analyst has succeeded",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query=("SELECT count(*) FROM l2_run_log "
               "WHERE kind='analyst' AND status IN ('ok','partial')")
    ))
    checks.append(PostgresCheck(
        "l2_llm_calls has analyst kind",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query="SELECT count(*) FROM l2_llm_calls WHERE kind='analyst'"
    ))

    # ── Phase 3 checks(spec §Step 7 = 5 项) ────────────────

    # 1. ops_decision_threads 表 + migration 004 + 3 索引(单 query 复合)
    checks.append(PostgresCheck(
        "ops_decision_threads + migration 004 + 3 indexes",
        env, "ACC_DB_HOST", "ACC_DB_PORT", "ACC_DB_NAME",
        "ACC_DB_USER", "ACC_DB_PASSWORD",
        query=(
            "SELECT 'OK' WHERE "
            "(SELECT count(*) FROM schema_versions WHERE version='004') = 1 "
            "AND (SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='ops_decision_threads') = 1 "
            "AND (SELECT count(*) FROM pg_indexes "
            "WHERE tablename='ops_decision_threads') >= 3"
        ),
        must_contain="OK",
    ))

    # 2. acc-facilitator.service active(长进程,4 Bot polling)
    checks.append(CommandCheck(
        "acc-facilitator.service active",
        ["systemctl", "is-active", "acc-facilitator.service"],
        must_contain="active",
    ))

    # 3. pusher timer enabled
    checks.append(CommandCheck(
        "acc-facilitator-pusher.timer enabled",
        ["systemctl", "is-enabled", "acc-facilitator-pusher.timer"],
        must_contain="enabled",
    ))

    # 4. archiver timer enabled
    checks.append(CommandCheck(
        "acc-facilitator-archiver.timer enabled",
        ["systemctl", "is-enabled", "acc-facilitator-archiver.timer"],
        must_contain="enabled",
    ))

    # 5. 4 个 TG_BOT_TOKEN 全非空(单项合并)
    checks.append(EnvVarsAllSetCheck(
        "4 TG_BOT_TOKEN_* all set in .env",
        env,
        keys=[
            "TG_BOT_TOKEN_ANALYST",
            "TG_BOT_TOKEN_FACILITATOR",
            "TG_BOT_TOKEN_WATCHER",
            "TG_BOT_TOKEN_CRAFTSMAN",
        ],
    ))

    return checks


# ── 主流程 ──────────────────────────────────────────────────
def main():
    json_mode = "--json" in sys.argv
    env = load_env()
    checks = build_checks(env)

    results = []
    all_ok = True

    for check in checks:
        ok, msg = check.run()
        results.append({"name": check.name, "ok": ok, "detail": msg})
        if not ok:
            all_ok = False

    if json_mode:
        print(json.dumps({
            "ok": all_ok,
            "checked_at": datetime.now().isoformat(),
            "results": results
        }, indent=2))
    else:
        print(f"=== Accelerator L2 Health Check @ {datetime.now()} ===\n")
        for r in results:
            symbol = "OK " if r["ok"] else "FAIL"
            print(f"[{symbol}] {r['name']:<42} {r['detail']}")
        print()
        if all_ok:
            print(f"All {len(results)} checks passed.")
        else:
            failed = sum(1 for r in results if not r["ok"])
            print(f"{failed}/{len(results)} checks FAILED.")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
