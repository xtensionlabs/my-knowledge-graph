"""Cross-platform daemon supervisor and OS-level installer.

Provides:
    install_clipboard_daemon()   — register with OS scheduler (boot-time autostart)
    uninstall_clipboard_daemon() — remove the scheduler entry
    start_clipboard_daemon()     — spawn the daemon process now (pythonw on Win)
    stop_clipboard_daemon()      — terminate via PID file
    daemon_status()              — read PID file + heartbeat freshness

Windows path uses Task Scheduler via `schtasks`. Linux generates a user
systemd unit. macOS generates a LaunchAgent plist. All three run the SAME
Python entry point: `python -m synapse.capture.clipboard`.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger

from synapse.config import get_settings

PID_FILENAME = "clipboard.pid"
HEARTBEAT_FILENAME = "clipboard.heartbeat"
TASK_NAME_WINDOWS = "SynapseClipboardDaemon"
SERVICE_NAME_UNIX = "synapse-clipboard"
HEARTBEAT_STALE_SECONDS = 30  # > 10× poll interval


@dataclass
class DaemonStatus:
    """Snapshot of daemon state."""

    name: str
    pid: int | None
    running: bool
    heartbeat_age_s: float | None
    pid_file: Path
    scheduler_installed: bool


# ── Platform detection ───────────────────────────────────────────────────────


def _platform() -> Literal["windows", "linux", "darwin"]:
    sysname = platform.system().lower()
    if sysname == "windows":
        return "windows"
    if sysname == "darwin":
        return "darwin"
    return "linux"


def _pythonw_path() -> str:
    """Resolve the pythonw.exe interpreter that pairs with the current python.exe."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    # Fallback: use python.exe (shows a console window). Better than failing.
    logger.warning("pythonw.exe not found alongside {exe}; using python.exe instead", exe=exe)
    return str(exe)


def _module_command(use_pythonw: bool) -> list[str]:
    """Command line that launches `python -m synapse.capture.clipboard`."""
    interp = _pythonw_path() if use_pythonw and _platform() == "windows" else sys.executable
    return [interp, "-m", "synapse.capture.clipboard"]


# ── PID + heartbeat helpers ──────────────────────────────────────────────────


def _pid_path() -> Path:
    return get_settings().pid_dir / PID_FILENAME


def _heartbeat_path() -> Path:
    return get_settings().pid_dir / HEARTBEAT_FILENAME


def _read_pid() -> int | None:
    p = _pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    """Best-effort check that PID is running."""
    if _platform() == "windows":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return str(pid) in out.stdout
        except (subprocess.SubprocessError, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── Public API ───────────────────────────────────────────────────────────────


def daemon_status() -> DaemonStatus:
    """Return current status of the clipboard daemon."""
    pid = _read_pid()
    running = bool(pid and _process_alive(pid))
    hb = _heartbeat_path()
    hb_age: float | None = None
    if hb.exists():
        try:
            stamp = int(hb.read_text(encoding="utf-8").strip())
            hb_age = max(0.0, time.time() - stamp)
        except (OSError, ValueError):
            hb_age = None

    installed = False
    if _platform() == "windows":
        installed = _windows_task_exists()
    elif _platform() == "linux":
        installed = _linux_unit_path().exists()
    elif _platform() == "darwin":
        installed = _darwin_plist_path().exists()

    return DaemonStatus(
        name="clipboard",
        pid=pid,
        running=running,
        heartbeat_age_s=hb_age,
        pid_file=_pid_path(),
        scheduler_installed=installed,
    )


def start_clipboard_daemon() -> int:
    """Spawn the daemon process detached. Returns PID."""
    settings = get_settings()
    settings.pid_dir.mkdir(parents=True, exist_ok=True)

    status = daemon_status()
    if status.running:
        logger.info("clipboard daemon already running (pid {pid})", pid=status.pid)
        assert status.pid is not None
        return status.pid

    cmd = _module_command(use_pythonw=True)
    env = os.environ.copy()
    logger.info("starting clipboard daemon: {cmd}", cmd=" ".join(cmd))

    if _platform() == "windows":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP = 0x00000008 | 0x00000200 = 0x208
        creationflags = 0x00000008 | 0x00000200
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            close_fds=True,
            creationflags=creationflags,
            env=env,
            cwd=str(settings.synapse_vault_path),
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            close_fds=True,
            start_new_session=True,
            env=env,
            cwd=str(settings.synapse_vault_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # The daemon writes its own PID file on startup; wait briefly for confirmation.
    for _ in range(20):  # ~2s
        time.sleep(0.1)
        pid = _read_pid()
        if pid is not None and _process_alive(pid):
            return pid
    logger.warning("clipboard daemon spawned (pid {pid}) but no PID file yet", pid=proc.pid)
    return proc.pid


def stop_clipboard_daemon() -> bool:
    """Stop a running clipboard daemon. Returns True if a process was killed."""
    pid = _read_pid()
    if pid is None or not _process_alive(pid):
        try:
            _pid_path().unlink()
        except OSError:
            pass
        return False

    try:
        if _platform() == "windows":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                timeout=5,
            )
        else:
            os.kill(pid, 15)  # SIGTERM
            for _ in range(20):
                time.sleep(0.1)
                if not _process_alive(pid):
                    break
            else:
                os.kill(pid, 9)  # SIGKILL
    except OSError as exc:
        logger.warning("stop failed: {exc}", exc=exc)
        return False

    try:
        _pid_path().unlink()
    except OSError:
        pass
    return True


# ── Windows: Task Scheduler ──────────────────────────────────────────────────


def _windows_task_exists() -> bool:
    try:
        out = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME_WINDOWS],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _install_windows() -> None:
    cmd_parts = _module_command(use_pythonw=True)
    # schtasks needs the executable + args as a single TR string.
    tr = " ".join(f'"{p}"' if " " in p else p for p in cmd_parts)
    args = [
        "schtasks", "/Create", "/F",
        "/TN", TASK_NAME_WINDOWS,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/TR", tr,
    ]
    logger.info("installing Task Scheduler entry: {args}", args=args)
    result = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks failed ({result.returncode}): {result.stderr.strip()}"
        )


def _uninstall_windows() -> None:
    if not _windows_task_exists():
        return
    subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME_WINDOWS],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


# ── Linux: user systemd ──────────────────────────────────────────────────────


def _linux_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME_UNIX}.service"


def _install_linux() -> None:
    cmd = " ".join(_module_command(use_pythonw=False))
    unit = f"""\
[Unit]
Description=Synapse clipboard capture daemon
After=default.target

[Service]
Type=simple
ExecStart={cmd}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    path = _linux_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME_UNIX}.service"],
        check=False,
        timeout=10,
    )


def _uninstall_linux() -> None:
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME_UNIX}.service"],
        check=False,
        timeout=10,
    )
    path = _linux_unit_path()
    if path.exists():
        path.unlink()


# ── macOS: LaunchAgent ───────────────────────────────────────────────────────


def _darwin_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.xtensionlabs.{SERVICE_NAME_UNIX}.plist"


def _install_darwin() -> None:
    cmd = _module_command(use_pythonw=False)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.xtensionlabs.{SERVICE_NAME_UNIX}</string>
  <key>ProgramArguments</key>
  <array>
    {"".join(f"<string>{c}</string>" for c in cmd)}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
"""
    path = _darwin_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "load", "-w", str(path)], check=False, timeout=10)


def _uninstall_darwin() -> None:
    path = _darwin_plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", "-w", str(path)], check=False, timeout=10)
        path.unlink()


# ── Top-level install / uninstall ────────────────────────────────────────────


def install_clipboard_daemon() -> None:
    """Register the clipboard daemon with the OS scheduler for boot autostart."""
    p = _platform()
    if p == "windows":
        _install_windows()
    elif p == "linux":
        _install_linux()
    elif p == "darwin":
        _install_darwin()


def uninstall_clipboard_daemon() -> None:
    """Remove the OS scheduler entry. Does not stop a running process."""
    p = _platform()
    if p == "windows":
        _uninstall_windows()
    elif p == "linux":
        _uninstall_linux()
    elif p == "darwin":
        _uninstall_darwin()
