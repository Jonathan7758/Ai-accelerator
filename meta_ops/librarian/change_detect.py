"""Phase 2 Step 3: 通用增量检测。

输入:source_root(目录)+ manifest_path(json 文件,可能不存在)。
输出:ChangeSet(added / modified / removed / unchanged 四组相对路径)。

设计要点:
- 用 sha256 比对内容,**不依赖文件 mtime**(scp/rsync/git checkout 都会重置 mtime)
- 纯函数,易单测:同输入 → 同输出
- manifest 格式:{"version": 1, "generated_at": ..., "source_root": ..., "files": {rel: sha256}}
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json


@dataclass(frozen=True)
class ChangeSet:
    """四组相对路径(已排序,去重)。"""
    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.removed)

    def summary(self) -> dict:
        return {
            "added": len(self.added),
            "modified": len(self.modified),
            "removed": len(self.removed),
            "unchanged": len(self.unchanged),
        }


def sha256_file(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def scan_dir_sha(root: Path, glob_pattern: str = "*") -> dict[str, str]:
    """Recurse root, return {relpath -> sha256} for every regular file matching glob."""
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob(glob_pattern)):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out[rel] = sha256_file(p)
    return out


def load_manifest(manifest_path: Path) -> dict[str, str]:
    """Return {relpath -> sha256} from manifest, or {} if missing / malformed."""
    if not manifest_path.is_file():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def write_manifest(
    manifest_path: Path, files_sha: dict[str, str], source_root: Path
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "files": dict(sorted(files_sha.items())),
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def detect_changes(
    source_root: Path,
    manifest_path: Path,
    glob_pattern: str = "*",
) -> ChangeSet:
    """Compare current state of source_root against previous manifest."""
    current = scan_dir_sha(source_root, glob_pattern)
    previous = load_manifest(manifest_path)

    cur_keys = set(current.keys())
    prev_keys = set(previous.keys())

    added = tuple(sorted(cur_keys - prev_keys))
    removed = tuple(sorted(prev_keys - cur_keys))
    common = cur_keys & prev_keys
    modified = tuple(sorted(p for p in common if current[p] != previous[p]))
    unchanged = tuple(sorted(p for p in common if current[p] == previous[p]))

    return ChangeSet(added=added, modified=modified, removed=removed, unchanged=unchanged)
