"""Self-update helper for EVE Alert Windows builds.

Handles the file-swap problem: a running Windows .exe cannot replace itself
because the OS holds a write-lock on it for the process lifetime.

Solution: write a tiny PowerShell script to %TEMP% that:
  1. Waits for the current process to exit  (Wait-Process -Id <pid>)
  2. Moves the downloaded exe over the original  (Move-Item -Force)
  3. Optionally re-launches the new exe          (Start-Process)

The script is launched detached (CREATE_NO_WINDOW) immediately before the
app calls exit_app().  By the time PowerShell's Wait-Process unblocks, the
original process is fully gone and the file is unlocked.

Guards:
  - Only available on Windows (sys.platform == 'win32')
  - Only useful in a frozen bundle (sys.frozen == True); in a dev run
    sys.executable is the Python interpreter, not EVE-Alert.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def is_updatable() -> bool:
    """Return True if the app is a frozen Windows .exe and can self-update."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def get_current_exe() -> Path | None:
    """Return the path of the running .exe, or None when not frozen."""
    if not is_updatable():
        return None
    return Path(sys.executable)


def write_swap_script(
    current_exe: Path,
    new_exe: Path,
    current_pid: int,
    relaunch: bool = True,
) -> Path:
    """Write a PowerShell swap script to %TEMP% and return its path.

    The script:
      - Waits for *current_pid* to exit
      - Pauses 500 ms to let the OS release file handles
      - Moves *new_exe* over *current_exe* (atomic on same volume)
      - Optionally re-launches the new exe
    """
    relaunch_line = (
        f"Start-Process -FilePath '{current_exe}'"
        if relaunch
        else "# relaunch disabled"
    )
    script = (
        f"$target_pid = {current_pid}\n"
        f"$new_path  = '{new_exe}'\n"
        f"$old_path  = '{current_exe}'\n"
        "Wait-Process -Id $target_pid -ErrorAction SilentlyContinue\n"
        "Start-Sleep -Milliseconds 500\n"
        "try {\n"
        "    Move-Item -Force -Path $new_path -Destination $old_path\n"
        "} catch {\n"
        "    Write-Error \"EVE Alert update failed: $_\"\n"
        "    exit 1\n"
        "}\n"
        f"{relaunch_line}\n"
    )
    dest = Path(tempfile.gettempdir()) / "eve_alert_swap.ps1"
    dest.write_text(script, encoding="utf-8")
    return dest


def launch_swap_and_exit(swap_script: Path) -> None:
    """Launch the PowerShell swap script detached.

    The caller must call exit_app() immediately after this returns so the
    current process exits and Wait-Process in the script unblocks.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell",
            "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass",
            "-File", str(swap_script),
        ],
        creationflags=flags,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def temp_download_path() -> Path:
    """Return a consistent temp path for the downloaded exe."""
    return Path(tempfile.gettempdir()) / "EVE-Alert-update.exe"


def cleanup_temp_download() -> None:
    """Remove the temp download file if it exists (called on cancel or error)."""
    p = temp_download_path()
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass
