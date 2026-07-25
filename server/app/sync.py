"""Pull the xochitl document store from the tablet via rsync over SSH.

Syncs are throttled: within settings.sync_max_age seconds of the last success
the call is a no-op, keeping rsync (and its connect timeout when the tablet
sleeps) out of the hot ask path. /api/refresh forces a real sync.
"""

import subprocess
import threading
import time

from .config import settings


class SyncError(RuntimeError):
    pass


_lock = threading.Lock()
_last_success_monotonic: float | None = None


def ssh_command(connect_timeout: int = 3) -> str:
    parts = [
        "ssh",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if settings.ssh_key_path:
        parts += ["-i", settings.ssh_key_path]
    return " ".join(parts)


def last_success_age() -> float | None:
    """Seconds since the last successful sync, or None if never synced."""
    with _lock:
        if _last_success_monotonic is None:
            return None
        return time.monotonic() - _last_success_monotonic


def run_sync(force: bool = False, timeout: int = 120) -> float:
    """Rsync the remote xochitl dir into settings.sync_dir. Returns elapsed seconds.

    Skipped (returns 0.0) when the last successful sync is fresher than
    settings.sync_max_age, unless force is set.
    """
    global _last_success_monotonic
    age = last_success_age()
    if not force and age is not None and age < settings.sync_max_age:
        return 0.0
    start = time.monotonic()
    # No -z: compression is CPU-bound on the tablet and far slower than the
    # LAN/USB link it would save bandwidth on (observed: 413 MB in ~20s without
    # -z vs a 90s+ stall with it).
    cmd = [
        "rsync",
        "-a",
        "--delete",
        "--exclude=*.thumbnails",
        "--exclude=*.cache",
        "--exclude=*.textconversion",
        "-e",
        ssh_command(),
        f"{settings.rm_user}@{settings.rm_host}:{settings.xochitl_remote_dir}/",
        str(settings.sync_dir) + "/",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise SyncError(proc.stderr.strip() or f"rsync exited {proc.returncode}")
    with _lock:
        _last_success_monotonic = time.monotonic()
    return time.monotonic() - start
