import logging
import zlib

import cv2 as cv
import numpy as np

from evealert.constants import (
    CV_DETECTION_COLOR,
    CV_LINE_TYPE,
    CV_RECTANGLE_THICKNESS,
    DETECTION_THRESHOLD_MAX,
    DETECTION_THRESHOLD_MIN,
    GROUP_RECTANGLES_EPS,
    GROUP_RECTANGLES_THRESHOLD,
)
from evealert.exceptions import RegionSizeError, ScreenshotError

logger = logging.getLogger("tools")


class Vision:
    """Computer vision handler for EVE Online UI element detection.

    Uses OpenCV template matching to detect enemy players and faction spawns
    in EVE Online screenshots. Supports multiple template images and various
    UI scaling factors.

    Attributes:
        needle_imgs: List of template images to match
        needle_dims: Dimensions of each template image
        method: OpenCV template matching method
        debug_mode: Show enemy detection visualization
        debug_mode_faction: Show faction detection visualization
    """

    # Template matching method — TM_CCOEFF_NORMED
    # There are 6 methods: TM_CCOEFF, TM_CCOEFF_NORMED, TM_CCORR, TM_CCORR_NORMED, TM_SQDIFF, TM_SQDIFF_NORMED
    def __init__(self, needle_img_paths, method=cv.TM_CCOEFF_NORMED):
        # Load the images we're trying to match. cv.imread returns None for
        # unreadable/corrupt/non-image files (rather than raising), so skip
        # those instead of crashing on img.shape — the Image Manager lets
        # users add arbitrary files to the img/ dir (#113). needle_paths and
        # needle_imgs are kept index-aligned because vision_process zips them.
        self.needle_paths = []
        self.needle_imgs = []
        for path in needle_img_paths:
            img = cv.imread(path, cv.IMREAD_UNCHANGED)
            if img is None:
                logger.warning("Vision: skipping unreadable template image: %s", path)
                continue
            # Normalize channel layout once at load time (#175) -- this used
            # to run on every vision_process() call (~10x/sec) even though a
            # needle's pixel data never changes after load.
            if img.shape[-1] == 4:
                img = cv.cvtColor(img, cv.COLOR_BGRA2BGR)
            elif len(img.shape) == 2:
                img = cv.cvtColor(img, cv.COLOR_GRAY2BGR)
            self.needle_paths.append(path)
            self.needle_imgs.append(img)
        # Save the dimensions of the (valid) needle images
        self.needle_dims = [(img.shape[1], img.shape[0]) for img in self.needle_imgs]

        self.method = method
        self.debug_mode = False
        self.debug_mode_faction = False
        self.enemy = None
        self.faction = None

        # #175 performance pass:
        # - Per-needle (scale, normalized_img, scaled_dims) cache, keyed by
        #   needle index -- dtype-casting/cv.normalize()/downscale-resize of
        #   a needle produce the same result every frame until either the
        #   haystack dtype or the downscale factor changes, so redoing it
        #   ~10x/sec per needle was pure waste.
        self._needle_norm_cache: list = [None] * len(self.needle_imgs)
        # - Per-vision_mode (frame_hash, points) cache. Local chat (and most
        #   D-scan/faction regions) is visually static most of the time --
        #   when the captured region is byte-identical to the previous
        #   frame, the detection result MUST be identical too, so re-running
        #   cv.matchTemplate for every needle is skippable with zero
        #   correctness cost. Disabled while the matching debug/calibration
        #   window is open for that mode, so the live preview always
        #   reflects a fresh pass.
        self._frame_cache: dict = {}
        # Per-needle hit counters (this session), surfaced via
        # get_needle_hit_counts() for diagnostics/benchmarking.
        #
        # #175's design note proposed also using hit counts to stop scanning
        # further needles once one has matched ("early exit"). That would
        # silently drop points from any OTHER needle that also matches the
        # same frame -- and _enemy_points (every matched point, not just a
        # yes/no boolean) is load-bearing for AlertAgent's per-enemy dedup
        # (#100) and OCR row correlation (#213): two simultaneously-present
        # enemies matched by two different template variants would collapse
        # to one. All needles are still scanned every non-cached frame;
        # only the frame-level cache above and the per-needle normalization
        # cache below skip redundant work, and neither can drop a real,
        # currently-visible match.
        self._needle_hit_counts: dict = {}

    @property
    def is_vision_open(self):
        """Returns True if the vision window is open."""
        return self.debug_mode

    @property
    def is_faction_vision_open(self):
        """Returns True if the faction vision window is open."""
        return self.debug_mode_faction

    def vision_process(
        self,
        haystack_img,
        threshold: int = 50,
        vision_mode: str = "Enemy",
        per_image_thresholds: dict = None,
        downscale: float = 1.0,
    ) -> tuple:
        all_points = []
        color = CV_DETECTION_COLOR
        debug = self.debug_mode if vision_mode == "Enemy" else self.debug_mode_faction

        # #175: frame-change short-circuit. Skipped while the debug/
        # calibration preview for this mode is open, so it always shows a
        # fresh pass (a cache hit would otherwise reuse the previous
        # detection_image, which has no rectangles drawn on it).
        #
        # The cache key includes threshold/per_image_thresholds/downscale,
        # not just the frame hash -- an unchanged frame with a CHANGED
        # detection parameter (e.g. the user just moved the sensitivity
        # slider in Settings) must still be re-matched. "Same frame" alone
        # is not sufficient to guarantee "same result".
        if not debug:
            frame_hash = zlib.crc32(haystack_img.tobytes())
            params_key = (
                threshold,
                downscale,
                tuple(sorted((per_image_thresholds or {}).items())),
            )
            cached = self._frame_cache.get(vision_mode)
            if cached is not None and cached[0] == frame_hash and cached[1] == params_key:
                return cached[2], haystack_img

        # Normalise the haystack once per frame (not per template)
        if len(haystack_img.shape) == 2:
            haystack_img = cv.cvtColor(haystack_img, cv.COLOR_GRAY2BGR)
        if downscale < 1.0:
            haystack_img = cv.resize(
                haystack_img, None, fx=downscale, fy=downscale,
                interpolation=cv.INTER_AREA,
            )
        haystack_img_norm = cv.normalize(haystack_img, None, 0, 255, cv.NORM_MINMAX)

        # Default global threshold
        global_threshold = max(
            min(threshold / 100, DETECTION_THRESHOLD_MAX),
            DETECTION_THRESHOLD_MIN,
        )

        for idx, (needle_img, needle_dim, needle_path) in enumerate(
            zip(self.needle_imgs, self.needle_dims, self.needle_paths)
        ):

            logger.debug("Detecting %s %s", vision_mode, idx)

            # Per-image threshold override (stored by basename)
            import os  # pylint: disable=import-outside-toplevel

            fname = os.path.basename(needle_path)
            if (
                per_image_thresholds
                and fname in per_image_thresholds
                and per_image_thresholds[fname] is not None
            ):
                img_val = per_image_thresholds[fname]
                detection_threshold = max(
                    min(img_val / 100, DETECTION_THRESHOLD_MAX), DETECTION_THRESHOLD_MIN
                )
            else:
                detection_threshold = global_threshold

            # #175: needle normalization (dtype cast + cv.normalize + any
            # downscale resize) is deterministic for a given (haystack
            # dtype, downscale) pair and a needle's pixel data never
            # changes after load, so cache it instead of redoing it every
            # ~100ms poll cycle.
            cache_entry = self._needle_norm_cache[idx]
            if (
                cache_entry is not None
                and cache_entry[0] == downscale
                and cache_entry[1].dtype == haystack_img_norm.dtype
            ):
                needle_img_norm, scaled_dim = cache_entry[1], cache_entry[2]
            else:
                scaled_needle = needle_img
                if downscale < 1.0:
                    scaled_needle = cv.resize(
                        scaled_needle, None, fx=downscale, fy=downscale,
                        interpolation=cv.INTER_AREA,
                    )
                if haystack_img_norm.dtype != scaled_needle.dtype:
                    scaled_needle = scaled_needle.astype(haystack_img_norm.dtype)
                needle_img_norm = cv.normalize(scaled_needle, None, 0, 255, cv.NORM_MINMAX)
                scaled_dim = (needle_img_norm.shape[1], needle_img_norm.shape[0])
                self._needle_norm_cache[idx] = (downscale, needle_img_norm, scaled_dim)

            # Check if the haystack image is larger than the needle image
            if (
                haystack_img.shape[0] < needle_img_norm.shape[0]
                or haystack_img.shape[1] < needle_img_norm.shape[1]
            ):
                raise RegionSizeError(
                    f"Detection {vision_mode} Error: Region is smaller than Detection Region please make a larger Area."
                )

            # Run the OpenCV template matching
            try:
                result = cv.matchTemplate(
                    haystack_img_norm, needle_img_norm, self.method
                )
            except Exception as e:
                logger.error("Detection %s Error: %s", vision_mode, e)
                # pylint: disable=raise-missing-from
                raise ScreenshotError(
                    f"Detection {vision_mode} Error: Something went wrong"
                )

            # Get positions from the match result that exceed our threshold
            locations = np.where(result >= detection_threshold)
            locations = list(zip(*locations[::-1]))

            # You'll notice a lot of overlapping rectangles get drawn.
            rectangles = []
            for loc in locations:
                rect = [int(loc[0]), int(loc[1]), scaled_dim[0], scaled_dim[1]]
                # Add every box to the list twice to retain single (non-overlapping) boxes
                rectangles.append(rect)
                rectangles.append(rect)

            # Apply group rectangles.
            rectangles, _ = cv.groupRectangles(
                rectangles,
                groupThreshold=GROUP_RECTANGLES_THRESHOLD,
                eps=GROUP_RECTANGLES_EPS,
            )

            points = []
            if len(rectangles):
                self._needle_hit_counts[idx] = self._needle_hit_counts.get(idx, 0) + 1
                # Loop over all the rectangles
                for x, y, w, h in rectangles:
                    # Determine the center position
                    center_x = x + int(w / 2)
                    center_y = y + int(h / 2)
                    if downscale < 1.0:
                        # Points must stay in ORIGINAL (undownscaled) region
                        # coordinates -- OCR row correlation (#213) and
                        # per-enemy dedup quantization both key off these.
                        center_x = int(center_x / downscale)
                        center_y = int(center_y / downscale)
                    # Save the points
                    points.append((center_x, center_y))
                    if debug:
                        # Ensure the image is writable
                        haystack_img = haystack_img.copy()
                        # Determine the box position
                        top_left = (x, y)
                        bottom_right = (x + w, y + h)
                        # Draw the box
                        try:
                            cv.rectangle(
                                haystack_img,
                                top_left,
                                bottom_right,
                                color=color,
                                lineType=CV_LINE_TYPE,
                                thickness=CV_RECTANGLE_THICKNESS,
                            )
                        except Exception as e:
                            logger.error("Rectangle Error: %s", e)

            all_points.extend(points)

        if not debug:
            self._frame_cache[vision_mode] = (frame_hash, params_key, list(all_points))
        return all_points, haystack_img

    def get_needle_hit_counts(self) -> dict:
        """Session-scoped {needle_index: match_count}, for diagnostics and
        the #175 benchmark harness."""
        return dict(self._needle_hit_counts)

    def clean_up(self) -> None:
        """Close all open windows."""
        cv.destroyAllWindows()
        self.debug_mode = False
        self.debug_mode_faction = False

    @staticmethod
    def _safe_destroy_window(name: str) -> None:
        """Destroy an OpenCV window, ignoring the cv.error raised when the
        window does not exist."""
        try:
            cv.destroyWindow(name)
        except cv.error:
            pass

    def destroy_vision(self, vision_mode: str = "Enemy") -> None:
        """Close the vision window."""
        if vision_mode == "Enemy":
            self.debug_mode = False
        elif vision_mode == "Faction":
            self.debug_mode_faction = False
        # Windows are created as "<mode> Vision" (e.g. "Enemy Vision"), so
        # destroy that exact name — not the bare mode (#112).
        self._safe_destroy_window(f"{vision_mode} Vision")

    def find(
        self,
        haystack_img,
        threshold: int = 50,
        per_image_thresholds: dict = None,
        downscale: float = 1.0,
    ) -> list:
        detection_image = None
        try:
            all_points, detection_image = self.vision_process(
                haystack_img, threshold, "Enemy", per_image_thresholds, downscale
            )
        except Exception as e:
            logger.exception("Enemy Detection Error: %s", e)
            self.destroy_vision("Enemy")
            all_points = []

        # Guard against detection_image being unbound on the error path (#111).
        if self.debug_mode and detection_image is not None:
            cv.imshow("Enemy Vision", detection_image)
            self.enemy = True
            cv.waitKey(1)
        elif self.enemy:
            self._safe_destroy_window("Enemy Vision")
            self.enemy = None
        return all_points

    def find_faction(
        self,
        haystack_img,
        threshold: int = 50,
        per_image_thresholds: dict = None,
        downscale: float = 1.0,
    ) -> list:
        detection_image = None
        try:
            all_points, detection_image = self.vision_process(
                haystack_img, threshold, "Faction", per_image_thresholds, downscale
            )
        except Exception as e:
            logger.exception("Faction Detection Error: %s", e)
            self.destroy_vision("Faction")
            all_points = []

        # Guard against detection_image being unbound on the error path (#111).
        if self.debug_mode_faction and detection_image is not None:
            cv.imshow("Faction Vision", detection_image)
            self.faction = True
            cv.waitKey(1)
        elif self.faction:
            self._safe_destroy_window("Faction Vision")
            self.faction = None
        return all_points
