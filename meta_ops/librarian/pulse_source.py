"""Phase 2 Step 1: Pulse 源接入。

把 Pulse 的 docs 和 code 镜像到 L2 本地,供后续 Librarian v1 的增量检测、
code_index、extracted 加工使用。

数据通道(都是只读):
- docs:rsync over SSH alias `pulse-hk-docs`(账号 l2_docs,key id_pulse_hk)
- code:git clone/pull via SSH alias `github-pulse`(deploy key id_pulse_repo)

入口:
- sync_pulse_docs() -> dict
- sync_pulse_code() -> dict
- python -m meta_ops.librarian.pulse_source(顺次跑两个,打印 JSON)

设计原则:
- 不可达 / 凭据失效 / 命令不存在 → 返回 {'ok': False, 'error': '...'},
  绝不抛异常拖挂上层 timer。
- 凭据细节(IdentityFile 等)全在 ~/.ssh/config,本模块不感知。
"""
from __future__ import annotations
from pathlib import Path
import json
import logging
import subprocess
import time

log = logging.getLogger(__name__)

PULSE_SRC_ROOT = Path("/opt/accelerator/knowledge/pulse_src")
DOCS_DIR = PULSE_SRC_ROOT / "docs"
CODE_DIR = PULSE_SRC_ROOT / "code"

HK_DOCS_REMOTE = "pulse-hk-docs:/opt/pulse/version1/docs/"
PULSE_REPO_URL = "git@github-pulse:Jonathan7758/project-pulse.git"

RSYNC_TIMEOUT_SEC = 120
GIT_TIMEOUT_SEC = 180
SSH_CONNECT_TIMEOUT_SEC = 10

# rsync 用这个 ssh 选项注入,避免远端 unreachable 时挂死 2 分钟
RSYNC_SSH_OPTS = f"ssh -o ConnectTimeout={SSH_CONNECT_TIMEOUT_SEC} -o BatchMode=yes"
GIT_SSH_COMMAND = f"ssh -o ConnectTimeout={SSH_CONNECT_TIMEOUT_SEC} -o BatchMode=yes"


def sync_pulse_docs(timeout: int = RSYNC_TIMEOUT_SEC) -> dict:
    """rsync 拉 HK 上 docs 到本地 pulse_src/docs/。

    Returns: {ok, files_total, files_changed, error, duration_seconds}
    """
    started = time.monotonic()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rsync",
        "-az",
        "--delete",
        "-i",  # itemize changes,用来数 files_changed
        "-e", RSYNC_SSH_OPTS,
        HK_DOCS_REMOTE,
        str(DOCS_DIR) + "/",
    ]
    log.info("rsync cmd: %s", " ".join(cmd))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _docs_err(f"rsync timeout after {timeout}s", started)
    except FileNotFoundError:
        return _docs_err("rsync binary not found on accelerator-jb", started)

    if proc.returncode != 0:
        stderr_brief = proc.stderr.strip().splitlines()[-1][:300] if proc.stderr else ""
        return _docs_err(f"rsync exit {proc.returncode}: {stderr_brief}", started)

    files_total = sum(1 for p in DOCS_DIR.rglob("*") if p.is_file())
    files_changed = sum(1 for line in proc.stdout.splitlines() if line.strip())

    return {
        "ok": True,
        "files_total": files_total,
        "files_changed": files_changed,
        "error": None,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def sync_pulse_code(timeout: int = GIT_TIMEOUT_SEC) -> dict:
    """git clone(首次)或 pull(后续)Pulse repo 到本地 pulse_src/code/。

    Returns: {ok, commit_sha, commit_short, changed, error, duration_seconds}
    """
    started = time.monotonic()
    PULSE_SRC_ROOT.mkdir(parents=True, exist_ok=True)

    is_existing = (CODE_DIR / ".git").is_dir()
    env = {"GIT_SSH_COMMAND": GIT_SSH_COMMAND}
    # 把现有 env 也带上(PATH / HOME 等),不然 git 找不到 ssh
    import os
    full_env = {**os.environ, **env}

    if not is_existing:
        old_sha = None
        cmd = ["git", "clone", "--quiet", PULSE_REPO_URL, str(CODE_DIR)]
    else:
        old_sha = _git_head_sha(CODE_DIR)
        cmd = ["git", "-C", str(CODE_DIR), "pull", "--ff-only", "--quiet"]

    log.info("git cmd: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=full_env,
        )
    except subprocess.TimeoutExpired:
        return _code_err(f"git timeout after {timeout}s", started, old_sha)
    except FileNotFoundError:
        return _code_err("git binary not found", started, old_sha)

    if proc.returncode != 0:
        stderr_brief = proc.stderr.strip().splitlines()[-1][:300] if proc.stderr else ""
        return _code_err(f"git exit {proc.returncode}: {stderr_brief}", started, old_sha)

    new_sha = _git_head_sha(CODE_DIR)
    if not new_sha:
        return _code_err("could not read HEAD after sync", started, old_sha)

    changed = (not is_existing) or (new_sha != old_sha)

    return {
        "ok": True,
        "commit_sha": new_sha,
        "commit_short": new_sha[:8],
        "changed": changed,
        "error": None,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def _git_head_sha(repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _docs_err(msg: str, started: float) -> dict:
    return {
        "ok": False,
        "files_total": 0,
        "files_changed": 0,
        "error": msg,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def _code_err(msg: str, started: float, last_sha: str | None) -> dict:
    return {
        "ok": False,
        "commit_sha": last_sha,
        "commit_short": last_sha[:8] if last_sha else None,
        "changed": False,
        "error": msg,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def main() -> dict:
    """Entry point: python -m meta_ops.librarian.pulse_source"""
    from meta_ops.common.logging_config import setup_logging
    setup_logging("pulse_source")

    docs = sync_pulse_docs()
    code = sync_pulse_code()
    result = {"docs": docs, "code": code}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
