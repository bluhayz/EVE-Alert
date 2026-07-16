"""OCR-based pilot name detection for EVE Alert (#98).

When an Enemy alarm fires, capture the configured Local-chat region and run
OCR to read the pilot name(s) on screen.  Parsed names are fed into the
existing KOS + ESI/Zkillboard intel pipeline.

Backend priority (Windows):
  1. Windows.Media.Ocr  — built into Windows 10 1607+, accessed via the
     ``winsdk`` package (already a base dependency on win32).  Zero user
     install required.
  2. pytesseract + Tesseract binary  — optional fallback; requires the user
     to install Tesseract separately AND ``pip install ".[ocr]"``.

On non-Windows platforms only pytesseract is attempted.

Every entry point degrades to a no-op with a log message when no backend is
available.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("alert.ocr")

# EVE character/corp/alliance names: letters (incl. accented), digits, spaces,
# and the punctuation EVE allows (hyphen, apostrophe, period). 3–37 chars.
# Must contain at least one letter so pure punctuation/number noise is dropped.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'\-]{2,36}$")
_HAS_LETTER = re.compile(r"[A-Za-z]")

# --------------------------------------------------------------------------- #
# Availability caches
# --------------------------------------------------------------------------- #
_winrt_available: bool | None = None
_tesseract_available: bool | None = None


def _import_winrt_modules():
    """Import the WinRT bridge modules, trying both package families.

    ``winsdk`` stopped publishing wheels after Python 3.12; its maintained
    successor is the ``winrt-*`` namespace packages (same underlying
    Windows.Media.Ocr engine, same API surface for our usage).  Try winsdk
    first (frozen builds ship it), then fall back to winrt.

    Returns (graphics_imaging, media_ocr, storage_streams) module triple.
    Raises ImportError when neither family is installed.
    """
    try:
        import winsdk.windows.graphics.imaging as wgi
        import winsdk.windows.media.ocr as wmo
        import winsdk.windows.storage.streams as wss

        return wgi, wmo, wss
    except ImportError:
        import winrt.windows.graphics.imaging as wgi
        import winrt.windows.media.ocr as wmo
        import winrt.windows.storage.streams as wss

        return wgi, wmo, wss


def is_winrt_ocr_available() -> bool:
    """Return True if Windows.Media.Ocr (via winsdk or winrt) can be used."""
    global _winrt_available
    if _winrt_available is not None:
        return _winrt_available
    if sys.platform != "win32":
        _winrt_available = False
        return False
    try:
        _wgi, wmo, _wss = _import_winrt_modules()

        engine = wmo.OcrEngine.try_create_from_user_profile_languages()
        _winrt_available = engine is not None
    except Exception as exc:
        logger.debug("Windows.Media.Ocr unavailable: %s", exc)
        _winrt_available = False
    return _winrt_available


def is_tesseract_available() -> bool:
    """Return True if pytesseract AND a working Tesseract binary are present."""
    global _tesseract_available
    if _tesseract_available is not None:
        return _tesseract_available
    try:
        import pytesseract  # noqa: F401

        pytesseract.get_tesseract_version()
        _tesseract_available = True
    except Exception as exc:
        logger.debug("pytesseract/Tesseract unavailable: %s", exc)
        _tesseract_available = False
    return _tesseract_available


def is_ocr_available() -> bool:
    """Return True if *any* OCR backend is available.

    Checks Windows.Media.Ocr first, then pytesseract.  Result is cached;
    never raises.
    """
    return is_winrt_ocr_available() or is_tesseract_available()


def reset_availability_cache() -> None:
    """Clear all cached availability checks (used by tests)."""
    global _winrt_available, _tesseract_available
    _winrt_available = None
    _tesseract_available = None


# --------------------------------------------------------------------------- #
# OCR helpers
# --------------------------------------------------------------------------- #

async def _winrt_recognize_async(pil_img) -> str:
    """Run Windows.Media.Ocr recognition on a PIL image (async, internal).

    Returns the recognized text with ONE LINE PER OcrLine (#199 root cause):
    ``OcrResult.text`` flattens the entire result into a single space-joined
    string with no newlines, which made every multi-name capture fail the
    per-line name regex downstream (names were read perfectly, then thrown
    away by the parser).  ``result.lines`` preserves the visual line
    structure of EVE's Local member list — one pilot per line.
    """
    wgi, wmo, wss = _import_winrt_modules()

    # Encode image to BMP in-memory so BitmapDecoder can load it.
    buf = io.BytesIO()
    pil_img.save(buf, "BMP")
    raw = buf.getvalue()

    mem_stream = wss.InMemoryRandomAccessStream()
    writer = wss.DataWriter(mem_stream)
    writer.write_bytes(raw)
    await writer.store_async()
    writer.detach_stream()
    mem_stream.seek(0)

    decoder = await wgi.BitmapDecoder.create_async(mem_stream)
    soft_bmp = await decoder.get_software_bitmap_async()

    engine = wmo.OcrEngine.try_create_from_user_profile_languages()
    result = await engine.recognize_async(soft_bmp)
    # NEVER use result.text here — it strips line structure (see docstring).
    return "\n".join(line.text for line in result.lines)


def _ocr_with_winrt(pil_img) -> str:
    """Synchronous wrapper around _winrt_recognize_async.

    Loop-aware (#205): the alarm path calls this from
    ``_build_enemy_alarm_text()``, which executes ON the engine's asyncio
    loop thread while that loop is RUNNING.  ``run_until_complete`` on any
    loop raises ``RuntimeError: Cannot run the event loop while another
    loop is running`` in that context — the error was swallowed at DEBUG
    level upstream, so alarm-time OCR silently returned no names on every
    single alarm while the Settings "Test OCR on Region" button (which runs
    on a plain worker thread) worked perfectly.  When a running loop is
    detected, recognition is executed on a short-lived worker thread with
    its own event loop instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running on this thread (Settings test path, plain threads)
        # — safe to spin up a private loop right here.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_winrt_recognize_async(pil_img))
        finally:
            loop.close()

    # A loop IS running on this thread (engine alarm path) — offload to a
    # worker thread.  This still blocks the caller for the OCR duration
    # (as the sync contract requires), but does not touch the caller's loop.
    import threading  # noqa: PLC0415

    result: dict = {}

    def _worker() -> None:
        try:
            result["text"] = asyncio.run(_winrt_recognize_async(pil_img))
        except Exception as exc:
            result["error"] = exc

    t = threading.Thread(target=_worker, daemon=True, name="eve-alert-ocr")
    t.start()
    t.join(timeout=15.0)
    if "error" in result:
        raise result["error"]
    if t.is_alive():
        raise TimeoutError("WinRT OCR recognition timed out after 15 s")
    return result.get("text", "")


def _ocr_with_tesseract(pil_img) -> str:
    """Run pytesseract on *pil_img* and return raw text."""
    import pytesseract  # noqa: PLC0415

    return pytesseract.image_to_string(pil_img)


# --------------------------------------------------------------------------- #
# Image preprocessing
# --------------------------------------------------------------------------- #

def _preprocess_for_ocr(pil_img):
    """Preprocess an EVE UI screenshot for better OCR accuracy.

    EVE's Local member list has white/light text on a dark space background.
    Pipeline (empirically tuned against a real Local capture — see #199):
      1. Grayscale — removes colour noise from standing icons
      2. 3× upscale (LANCZOS) — each name row is only ~20 px tall; a 3×
         upscale scored 8/10 exact names vs 6/10 at 2× on the reference
         capture
      3. Convert to RGBA — Windows.Media.Ocr requires Bgra8 (32-bit) format;
         an 8-bit grayscale BMP is decoded as Gray8 and OcrEngine silently
         returns empty text for non-Bgra8 input.

    Deliberately NOT done (measured to be neutral or harmful on WinRT):
      - Invert: WinRT scores identically on white-on-dark and dark-on-white.
      - Contrast boost: enhance(2.0) DROPPED accuracy from 8/10 to 5/10 at
        3×–4× scale (it destroys anti-aliasing the engine relies on).
    """
    try:
        img = pil_img.convert("L")                        # grayscale
        w, h = img.size
        img = img.resize((w * 3, h * 3), 1)               # 1 = LANCZOS (PIL constant)
        img = img.convert("RGBA")                         # 32-bit for WinRT Bgra8
        return img
    except Exception:
        return pil_img  # fallback: return original if preprocessing fails


def get_ocr_debug_path() -> Path:
    """Return the path where the last failed OCR debug screenshot is saved."""
    from platformdirs import user_config_dir  # noqa: PLC0415
    return Path(user_config_dir("evealert")) / "ocr_debug_last.png"


def _save_ocr_debug_screenshot(raw_img, region: tuple) -> None:
    """Save *raw_img* as a debug PNG when OCR finds no names.

    The file is always overwritten (keeps only the most recent miss).
    Never raises — silently logs and returns on any error.
    """
    try:
        path = get_ocr_debug_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_img.save(str(path))
        logger.debug(
            "OCR miss — debug screenshot saved to %s (region %s)", path, region
        )
    except Exception as exc:
        logger.debug("Could not save OCR debug screenshot: %s", exc)


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #

def parse_eve_names(text: str) -> list[str]:
    """Extract plausible EVE pilot names from raw OCR text.

    Splits on line boundaries, strips leading non-alphanumeric characters
    (EVE's standing/corp icons OCR as ■, ★, etc. before each name), trims
    whitespace noise, and keeps tokens that look like EVE names
    (letters/digits/space/.'- , 3–37 chars, at least one letter).
    De-duplicates while preserving order.

    Icon-glyph handling (#199): the standing icon frequently OCRs as a short
    LETTER token instead of a symbol ("S Naveia", "CS Bronwen Morgan"), which
    the non-alphanumeric strip cannot remove.  When a line's first token is
    1–2 chars and something name-like follows, BOTH the stripped remainder
    and the full line are emitted (stripped first).  Downstream ESI name
    resolution is exact-match, so the wrong candidate fails silently while
    the right one resolves — at worst this costs one extra lookup per line.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _emit(candidate: str) -> None:
        if not candidate or candidate.lower() in seen:
            return
        if not _NAME_RE.match(candidate):
            return
        if not _HAS_LETTER.search(candidate):
            return
        seen.add(candidate.lower())
        names.append(candidate)

    for raw_line in (text or "").splitlines():
        # Strip leading non-alphanumeric garbage (standing icons, symbols)
        # that EVE's UI renders before each pilot name in the Local list.
        base = re.sub(r"^[^A-Za-z0-9]+", "", raw_line.strip())
        base = re.sub(r"\s{2,}", " ", base).strip()
        if not base:
            continue
        parts = base.split(" ", 1)
        if len(parts) == 2 and len(parts[0]) <= 2 and len(parts[1]) >= 3:
            # Likely icon glyph misread as a short letter token — try the
            # remainder first, but keep the full line as a fallback candidate.
            _emit(parts[1].strip())
        _emit(base)
    return names


def resolve_region(
    override: tuple[int, int, int, int],
    alert_region: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """Pick the OCR capture region.

    Uses *override* (x1, y1, x2, y2) when it is non-zero, otherwise falls
    back to the alert region.  Returns a normalised (left, top, right, bottom)
    tuple with left<right and top<bottom, or None if neither region is usable.
    """
    x1, y1, x2, y2 = override
    if not any((x1, y1, x2, y2)):
        x1, y1, x2, y2 = alert_region
    left, right = sorted((int(x1), int(x2)))
    top, bottom = sorted((int(y1), int(y2)))
    if right - left < 1 or bottom - top < 1:
        return None
    return (left, top, right, bottom)


def read_local_names(region: tuple[int, int, int, int]) -> list[str]:
    """Capture *region* (left, top, right, bottom) and OCR pilot names from it.

    Backend priority: Windows.Media.Ocr → pytesseract.
    Returns [] on any failure or when no OCR backend is available — never raises.
    """
    if not is_ocr_available():
        return []

    try:
        import mss  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
    except Exception as exc:
        logger.debug("OCR imports failed: %s", exc)
        return []

    left, top, right, bottom = region
    grab = {
        "left": left,
        "top": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top),
    }
    raw_img = None
    try:
        with mss.mss() as sct:
            shot = sct.grab(grab)
        raw_img = Image.frombytes("RGB", shot.size, shot.rgb)
        img = _preprocess_for_ocr(raw_img)
    except Exception as exc:
        logger.debug("OCR screen capture failed: %s", exc)
        return []

    text = ""
    if is_winrt_ocr_available():
        try:
            text = _ocr_with_winrt(img)
            logger.debug("WinRT OCR raw output (%d chars): %r", len(text), text[:200])
        except Exception as exc:
            logger.debug("Windows.Media.Ocr recognition failed: %s", exc)

    if not text and is_tesseract_available():
        try:
            text = _ocr_with_tesseract(img)
            logger.debug("Tesseract OCR raw output (%d chars): %r", len(text), text[:200])
        except Exception as exc:
            logger.debug("pytesseract recognition failed: %s", exc)

    if not text:
        logger.debug("OCR backend(s) returned empty text for region %s", region)

    names = parse_eve_names(text)
    if not names and raw_img is not None:
        # No names found — save a debug screenshot so the user can check
        # whether the configured region is pointing at the right area.
        _save_ocr_debug_screenshot(raw_img, region)
    return names


# --------------------------------------------------------------------------- #
# Diagnostic runner
# --------------------------------------------------------------------------- #

def run_ocr_diagnostic(region: tuple[int, int, int, int]) -> dict:
    """Run the full OCR pipeline on *region* and return a diagnostic dict.

    Always returns a dict with keys:
      ok            bool   — True if at least one name was extracted
      names         list   — names found (may be empty)
      raw_text      str    — text returned by OCR engine before filtering
      backend       str    — "winrt" | "tesseract" | "none"
      input_mode    str    — PIL image mode of the raw capture
      input_size    tuple  — (width, height) of the raw capture
      proc_mode     str    — PIL image mode after preprocessing
      proc_size     tuple  — (width, height) after preprocessing
      debug_path    str    — path of saved debug screenshot (may be "")
      error         str    — exception message if something crashed (may be "")
    """
    import sys as _sys  # noqa: PLC0415

    result: dict = {
        "ok": False,
        "names": [],
        "raw_text": "",
        "backend": "none",
        "input_mode": "",
        "input_size": (0, 0),
        "proc_mode": "",
        "proc_size": (0, 0),
        "debug_path": "",
        "error": "",
    }

    try:
        import mss  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
    except Exception as exc:
        result["error"] = f"import failed: {exc}"
        return result

    left, top, right, bottom = region
    grab = {"left": left, "top": top,
            "width": max(1, right - left), "height": max(1, bottom - top)}
    try:
        with mss.mss() as sct:
            shot = sct.grab(grab)
        raw_img = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception as exc:
        result["error"] = f"screen capture failed: {exc}"
        return result

    result["input_mode"] = raw_img.mode
    result["input_size"] = raw_img.size

    try:
        proc_img = _preprocess_for_ocr(raw_img)
        result["proc_mode"] = proc_img.mode
        result["proc_size"] = proc_img.size
    except Exception as exc:
        result["error"] = f"preprocessing failed: {exc}"
        return result

    raw_text = ""
    if is_winrt_ocr_available():
        try:
            raw_text = _ocr_with_winrt(proc_img)
            result["backend"] = "winrt"
        except Exception as exc:
            result["error"] += f"winrt error: {exc}; "

    if not raw_text and is_tesseract_available():
        try:
            raw_text = _ocr_with_tesseract(proc_img)
            result["backend"] = "tesseract"
        except Exception as exc:
            result["error"] += f"tesseract error: {exc}; "

    result["raw_text"] = raw_text
    names = parse_eve_names(raw_text)
    result["names"] = names
    result["ok"] = bool(names)

    # Always save debug screenshot so it can be attached to a bug report
    debug_path = get_ocr_debug_path()
    _save_ocr_debug_screenshot(raw_img, region)
    result["debug_path"] = str(debug_path)

    return result
