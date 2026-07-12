"""OCR-based pilot name detection for EVE Alert (#98).

When an Enemy alarm fires, capture the configured Local-chat region and run
OCR (Tesseract via pytesseract) to read the pilot name(s) on screen. Parsed
names are fed into the existing KOS + ESI/Zkillboard intel pipeline.

This is entirely optional and OFF by default:
  - pytesseract (a thin wrapper) may be bundled, but the Tesseract *engine*
    binary must be installed separately by the user (it can't be shipped in a
    PyInstaller --onefile build).
  - Every entry point is import-guarded and degrades to a no-op with a log
    message when pytesseract or the Tesseract binary is unavailable.
"""

import logging
import re

logger = logging.getLogger("alert.ocr")

# EVE character/corp/alliance names: letters (incl. accented), digits, spaces,
# and the punctuation EVE allows (hyphen, apostrophe, period). 3–37 chars.
# Must contain at least one letter so pure punctuation/number noise is dropped.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'\-]{2,36}$")
_HAS_LETTER = re.compile(r"[A-Za-z]")

# Cached availability result (None = not yet checked).
_available: bool | None = None


def is_ocr_available() -> bool:
    """Return True if pytesseract AND a working Tesseract binary are present.

    Result is cached; never raises.
    """
    global _available
    if _available is not None:
        return _available
    try:
        import pytesseract  # pylint: disable=import-outside-toplevel

        pytesseract.get_tesseract_version()
        _available = True
    except Exception as exc:  # ImportError, TesseractNotFoundError, etc.
        logger.debug("OCR unavailable: %s", exc)
        _available = False
    return _available


def reset_availability_cache() -> None:
    """Clear the cached availability check (used by tests)."""
    global _available
    _available = None


def parse_eve_names(text: str) -> list[str]:
    """Extract plausible EVE pilot names from raw OCR text.

    Splits on line boundaries, trims OCR noise, and keeps tokens that look
    like EVE names (letters/digits/space/.'- , 3–37 chars, at least one
    letter). De-duplicates while preserving order.
    """
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        candidate = raw_line.strip()
        # Collapse internal whitespace runs left by OCR.
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

    Uses *override* (x1, y1, x2, y2) when it is non-zero, otherwise falls back
    to the alert region. Returns a normalized (left, top, right, bottom) tuple
    with left<right and top<bottom, or None if neither region is usable.
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

    Returns [] on any failure or when OCR is unavailable — never raises.
    """
    if not is_ocr_available():
        return []
    try:
        import mss  # pylint: disable=import-outside-toplevel
        import pytesseract  # pylint: disable=import-outside-toplevel
        from PIL import Image  # pylint: disable=import-outside-toplevel
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
    try:
        with mss.mss() as sct:
            shot = sct.grab(grab)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        text = pytesseract.image_to_string(img)
    except Exception as exc:
        logger.debug("OCR capture/recognition failed: %s", exc)
        return []
    return parse_eve_names(text)
