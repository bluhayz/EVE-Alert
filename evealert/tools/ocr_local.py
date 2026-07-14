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


def is_winrt_ocr_available() -> bool:
    """Return True if Windows.Media.Ocr (winsdk) can be used."""
    global _winrt_available
    if _winrt_available is not None:
        return _winrt_available
    if sys.platform != "win32":
        _winrt_available = False
        return False
    try:
        import winsdk.windows.media.ocr as _ocr  # noqa: F401

        engine = _ocr.OcrEngine.try_create_from_user_profile_languages()
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
    """Run Windows.Media.Ocr recognition on a PIL image (async, internal)."""
    import winsdk.windows.graphics.imaging as wgi
    import winsdk.windows.media.ocr as wmo
    import winsdk.windows.storage.streams as wss

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
    return result.text


def _ocr_with_winrt(pil_img) -> str:
    """Synchronous wrapper around _winrt_recognize_async."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_winrt_recognize_async(pil_img))
    finally:
        loop.close()


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
    Both Windows.Media.Ocr and Tesseract perform better with:
      1. Grayscale — removes colour noise from standing icons
      2. 2× upscale — each name row is only ~20 px tall; upscaling dramatically
         improves recognition of small text
      3. Invert — both engines recognise dark-on-white more reliably than
         white-on-dark
      4. Contrast boost — sharpens the text edges after inversion
    """
    try:
        from PIL import ImageEnhance, ImageOps  # noqa: PLC0415

        img = pil_img.convert("L")                        # grayscale
        w, h = img.size
        img = img.resize((w * 2, h * 2), 1)              # 1 = LANCZOS (PIL constant)
        img = ImageOps.invert(img)                        # white-on-dark → dark-on-white
        img = ImageEnhance.Contrast(img).enhance(2.0)     # boost contrast
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
    """
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        # Strip leading non-alphanumeric garbage (standing icons, symbols)
        # that EVE's UI renders before each pilot name in the Local list.
        candidate = re.sub(r"^[^A-Za-z0-9]+", "", raw_line.strip())
        candidate = re.sub(r"\s{2,}", " ", candidate)
        if not candidate or candidate.lower() in seen:
            continue
        if not _NAME_RE.match(candidate):
            continue
        if not _HAS_LETTER.search(candidate):
            continue
        seen.add(candidate.lower())
        names.append(candidate)
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
        except Exception as exc:
            logger.debug("Windows.Media.Ocr recognition failed: %s", exc)

    if not text and is_tesseract_available():
        try:
            text = _ocr_with_tesseract(img)
        except Exception as exc:
            logger.debug("pytesseract recognition failed: %s", exc)

    names = parse_eve_names(text)
    if not names and raw_img is not None:
        # No names found — save a debug screenshot so the user can check
        # whether the configured region is pointing at the right area.
        _save_ocr_debug_screenshot(raw_img, region)
    return names
