"""统一的 Python logging 配置。日志同时进 journal(stderr)和文件。

每个 entry point(librarian / watcher / cli)启动时调一次 setup_logging(component_name)。
"""
import logging
import sys
from pathlib import Path

LOG_DIR = Path("/var/log/accelerator")


def setup_logging(component: str, level=logging.INFO):
    """每个 entry point 启动时调一次。component 例如 'watcher'/'librarian'。"""
    LOG_DIR.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # 文件
    file_handler = logging.FileHandler(LOG_DIR / f"{component}.log")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # stderr(systemd journal 会捕获)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)

    # 静默第三方库的 INFO 噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)
