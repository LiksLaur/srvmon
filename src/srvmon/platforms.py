from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(args: list[str], timeout: float = 2.0) -> str:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def get_inode_percent(path: str) -> float | None:
    if not IS_LINUX:
        return None
    try:
        stats = os.statvfs(path)
    except OSError:
        return None
    total = stats.f_files
    free = stats.f_ffree
    if total <= 0:
        return None
    return (total - free) / total * 100


def get_log_paths() -> list[Path]:
    candidates = [
        Path("/var/log/syslog"),
        Path("/var/log/messages"),
        Path("/var/log/auth.log"),
        Path("/var/log/kern.log"),
        Path("/var/log/dmesg"),
    ]
    return [path for path in candidates if path.exists()]
