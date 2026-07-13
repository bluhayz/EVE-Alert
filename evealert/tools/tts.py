"""Text-to-speech support for EVE Alert (#139).

Uses pyttsx3 (Windows SAPI5 — no additional install required on Windows).
Everything is import-guarded and degrades silently if pyttsx3 is absent
or fails to initialise.

Usage:
    if is_tts_available():
        speak("Enemy in local — Sabre pilot")
"""

import logging
import threading

logger = logging.getLogger("alert.tts")

_engine_lock = threading.Lock()
_engine = None


def is_tts_available() -> bool:
    """Return True if pyttsx3 is installed and can initialise an engine.

    Result is NOT cached so repeated calls reflect install/uninstall without restart.
    """
    try:
        import pyttsx3  # noqa: PLC0415

        eng = pyttsx3.init()
        eng.stop()
        return True
    except Exception as exc:
        logger.debug("TTS unavailable: %s", exc)
        return False


def speak(text: str, rate: int = 175) -> None:
    """Speak *text* asynchronously on a daemon thread.

    Returns immediately — the audio plays in the background so the alarm
    loop is never blocked.  If TTS is unavailable the call is a no-op.

    Args:
        text: The phrase to speak.
        rate: Speech rate in words per minute (50–400; default 175).
    """
    def _run() -> None:
        try:
            import pyttsx3  # noqa: PLC0415

            with _engine_lock:
                eng = pyttsx3.init()
                eng.setProperty("rate", max(50, min(400, rate)))
                eng.say(text)
                eng.runAndWait()
                eng.stop()
        except Exception as exc:
            logger.debug("TTS speak failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="eve-alert-tts").start()
