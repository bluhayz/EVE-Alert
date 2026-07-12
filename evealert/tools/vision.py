import logging

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
            self.needle_paths.append(path)
            self.needle_imgs.append(img)
        # Save the dimensions of the (valid) needle images
        self.needle_dims = [(img.shape[1], img.shape[0]) for img in self.needle_imgs]

        self.method = method
        self.debug_mode = False
        self.debug_mode_faction = False
        self.enemy = None
        self.faction = None

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
    ) -> tuple:
        all_points = []
        color = CV_DETECTION_COLOR

        # Normalise the haystack once per frame (not per template)
        if len(haystack_img.shape) == 2:
            haystack_img = cv.cvtColor(haystack_img, cv.COLOR_GRAY2BGR)
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

            logger.debug("Detecting %s %s", vision_mode, idx)

            # Remove alpha channel if present (convert BGRA to BGR)
            if needle_img.shape[-1] == 4:
                needle_img = cv.cvtColor(needle_img, cv.COLOR_BGRA2BGR)

            # Convert images to same type if necessary
            if haystack_img_norm.dtype != needle_img.dtype:
                needle_img = needle_img.astype(haystack_img_norm.dtype)

            # Ensure needle is also BGR
            if len(needle_img.shape) == 2:
                needle_img = cv.cvtColor(needle_img, cv.COLOR_GRAY2BGR)

            needle_img_norm = cv.normalize(needle_img, None, 0, 255, cv.NORM_MINMAX)

            # Check if the haystack image is larger than the needle image
            if (
                haystack_img.shape[0] < needle_img.shape[0]
                or haystack_img.shape[1] < needle_img.shape[1]
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
                rect = [int(loc[0]), int(loc[1]), needle_dim[0], needle_dim[1]]
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
                # Loop over all the rectangles
                for x, y, w, h in rectangles:
                    # Determine the center position
                    center_x = x + int(w / 2)
                    center_y = y + int(h / 2)
                    # Save the points
                    points.append((center_x, center_y))
                    if self.debug_mode or self.debug_mode_faction:
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
        return all_points, haystack_img

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
        self, haystack_img, threshold: int = 50, per_image_thresholds: dict = None
    ) -> list:
        detection_image = None
        try:
            all_points, detection_image = self.vision_process(
                haystack_img, threshold, "Enemy", per_image_thresholds
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
        self, haystack_img, threshold: int = 50, per_image_thresholds: dict = None
    ) -> list:
        detection_image = None
        try:
            all_points, detection_image = self.vision_process(
                haystack_img, threshold, "Faction", per_image_thresholds
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
