"""Text-to-speech support for EVE Alert (#139).

Uses Windows' built-in System.Speech.Synthesis via PowerShell — no Python
package required, works in the bundled PyInstaller .exe out of the box.

The approach: spawn a hidden PowerShell process that calls
System.Speech.Synthesis.SpeechSynthesizer.  PowerShell ships with every
modern Windows installation; System.Speech ships with .NET Framework (part
of Windows since Vista).  The text is passed through an environment variable
so there is no shell-injection risk from alarm/pilot-name content.

Usage:
    if is_tts_available():
        speak("Enemy in local — Sabre pilot")
"""

import logging
import os
import shutil
import subprocess
import threading

logger = logging.getLogger("alert.tts")

# pyttsx3 is kept as an optional FALLBACK for users who have it installed.
# The platform-native path is tried first; pyttsx3 is only used if the
# platform path fails (e.g. old Windows without PowerShell on PATH).
_ENV_KEY = "_EVE_ALERT_TTS_TEXT"


def _speak_platform(text: str, rate: int) -> None:
    """Speak via PowerShell System.Speech (Windows built-in, zero deps)."""
    # Map WPM (50–400) to SAPI rate scale (-10 to +10, default 0 ≈ 175 WPM)
    sapi_rate = max(-10, min(10, round((rate - 175) / 25)))
    # Text is injected via environment variable — no injection risk.
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {sapi_rate}; "
        f"$s.Speak($env:{_ENV_KEY})"
    )
    env = os.environ.copy()
    env[_ENV_KEY] = text
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
        env=env,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _speak_pyttsx3(text: str, rate: int) -> None:
    """Fallback: speak via pyttsx3 if installed."""
    import pyttsx3  # noqa: PLC0415

    eng = pyttsx3.init()
    eng.setProperty("rate", max(50, min(400, rate)))
    eng.say(text)
    eng.runAndWait()
    eng.stop()


def is_tts_available() -> bool:
    """Return True if TTS can be used on this system.

    On Windows: True when powershell.exe is on PATH (always the case on any
    modern Windows installation — the bundled .exe will always succeed here).
    Fallback: True if pyttsx3 is importable.
    """
    if shutil.which("powershell"):
        return True
    try:
        import pyttsx3  # noqa: PLC0415
        return True
    except Exception:
        return False


def speak(text: str, rate: int = 175) -> None:
    """Speak *text* asynchronously on a daemon thread.

    Uses PowerShell + System.Speech (Windows built-in) with pyttsx3 as a
    fallback.  Returns immediately; audio plays in the background.

    Args:
        text: The phrase to speak.
        rate: Speech rate in words per minute (50–400; default 175).
    """
    def _run() -> None:
        if shutil.which("powershell"):
            try:
                _speak_platform(text, rate)
                return
            except Exception as exc:
                logger.debug("Platform TTS failed, trying pyttsx3: %s", exc)
        # Fallback to pyttsx3
        try:
            _speak_pyttsx3(text, rate)
        except Exception as exc:
            logger.debug("TTS speak failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="eve-alert-tts").start()
