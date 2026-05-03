#!/usr/bin/env bash
# 把 /opt/accelerator-git (git workdir) 同步到 /opt/accelerator/ (应用 root)。
# 只同步代码;运行时状态(.env / .venv / knowledge/)不动。
#
# 使用:在 accelerator-jb 上以 root 跑:
#     /opt/accelerator-git/deploy.sh
# 或本地:
#     ssh accelerator-jb /opt/accelerator-git/deploy.sh
set -euo pipefail

GIT_DIR=/opt/accelerator-git
DEPLOY_DIR=/opt/accelerator

if [[ $EUID -ne 0 ]]; then
  echo "[deploy] FATAL: must run as root (or via sudo)" >&2
  exit 1
fi
if [[ ! -d "$GIT_DIR/.git" ]]; then
  echo "[deploy] FATAL: $GIT_DIR is not a git workdir" >&2
  exit 1
fi

echo "[deploy] step 1/3: git pull..."
sudo -u accelerator git -C "$GIT_DIR" pull --ff-only --quiet
HEAD_SHA=$(sudo -u accelerator git -C "$GIT_DIR" rev-parse --short HEAD)
echo "[deploy]         HEAD now: $HEAD_SHA"

echo "[deploy] step 2/3: rsync code dirs..."
SYNC_DIRS=(meta_ops scripts sql prompts)
for d in "${SYNC_DIRS[@]}"; do
  [[ -d "$GIT_DIR/$d" ]] || { echo "[deploy]         skip $d/ (not present)"; continue; }
  rsync -a --delete \
    --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
    --chown=accelerator:accelerator \
    "$GIT_DIR/$d/" "$DEPLOY_DIR/$d/"
  echo "[deploy]         synced $d/"
done

echo "[deploy] step 3/3: copy root manifests..."
SYNC_FILES=(pyproject.toml requirements.txt)
for f in "${SYNC_FILES[@]}"; do
  [[ -f "$GIT_DIR/$f" ]] || { echo "[deploy]         skip $f (not present)"; continue; }
  install -o accelerator -g accelerator -m 644 "$GIT_DIR/$f" "$DEPLOY_DIR/$f"
  echo "[deploy]         copied $f"
done

echo "[deploy] done — $DEPLOY_DIR @ $HEAD_SHA"
echo "[deploy] tip: if pyproject changed, re-run:"
echo "[deploy]      sudo -u accelerator $DEPLOY_DIR/.venv/bin/pip install -e $DEPLOY_DIR"
