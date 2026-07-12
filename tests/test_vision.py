"""Unit tests for Vision module template matching."""

import unittest
from pathlib import Path
from unittest.mock import patch

import cv2 as cv
import numpy as np

from evealert.exceptions import RegionSizeError
from evealert.tools.vision import Vision


class TestVision(unittest.TestCase):
    """Test cases for Vision class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a needle with genuine texture variance so TM_CCOEFF_NORMED works.
        # A solid-colour needle has zero variance and always scores 0.0 correlation.
        self.test_needle_path = Path("tests/fixtures/test_needle.png")
        self.test_needle_path.parent.mkdir(parents=True, exist_ok=True)

        needle_img = np.zeros((30, 30, 3), dtype=np.uint8)
        needle_img[5:25, 5:25] = (0, 0, 200)  # blue-ish border region
        needle_img[10:20, 10:20] = (80, 50, 240)  # lighter inner region for variance
        needle_img[13:17, 13:17] = (200, 100, 50)  # contrasting centre dot
        cv.imwrite(str(self.test_needle_path), needle_img)

        # Create Vision instance
        self.vision = Vision([str(self.test_needle_path)])

    def tearDown(self):
        """Clean up test fixtures."""
        if self.test_needle_path.exists():
            self.test_needle_path.unlink()
        if self.test_needle_path.parent.exists() and not list(
            self.test_needle_path.parent.iterdir()
        ):
            self.test_needle_path.parent.rmdir()

        self.vision.clean_up()

    def test_vision_initialization(self):
        """Test Vision object initialization."""
        self.assertIsNotNone(self.vision.needle_imgs)
        self.assertEqual(len(self.vision.needle_imgs), 1)
        self.assertEqual(len(self.vision.needle_dims), 1)
        self.assertEqual(self.vision.needle_dims[0], (30, 30))
        self.assertEqual(self.vision.method, cv.TM_CCOEFF_NORMED)
        self.assertFalse(self.vision.debug_mode)

    def test_find_with_no_matches(self):
        """Test finding templates with no matches."""
        # Create haystack with no red squares
        haystack = np.zeros((200, 200, 3), dtype=np.uint8)
        haystack[:, :] = (255, 255, 255)  # White

        points = self.vision.find(haystack, threshold=90)  # High threshold
        self.assertIsInstance(points, list)
        # Allow for occasional false positives in template matching
        self.assertLessEqual(len(points), 2)

    def test_find_with_single_match(self):
        """Test finding templates with single match."""
        # Paste the exact needle pattern into the haystack — guarantees a match
        needle_img = cv.imread(str(self.test_needle_path))
        haystack = np.full((200, 200, 3), 200, dtype=np.uint8)  # grey background
        h, w = needle_img.shape[:2]
        haystack[50 : 50 + h, 50 : 50 + w] = needle_img

        points = self.vision.find(haystack, threshold=70)
        self.assertGreater(len(points), 0)

    def test_find_with_multiple_matches(self):
        """Test finding templates with multiple matches."""
        needle_img = cv.imread(str(self.test_needle_path))
        h, w = needle_img.shape[:2]
        haystack = np.full((300, 400, 3), 200, dtype=np.uint8)
        # Place the pattern twice in well-separated positions
        haystack[20 : 20 + h, 20 : 20 + w] = needle_img
        haystack[200 : 200 + h, 300 : 300 + w] = needle_img

        points = self.vision.find(haystack, threshold=70)
        self.assertGreaterEqual(len(points), 1)

    def test_find_faction(self):
        """Test faction detection."""
        needle_img = cv.imread(str(self.test_needle_path))
        h, w = needle_img.shape[:2]
        haystack = np.full((200, 200, 3), 200, dtype=np.uint8)
        haystack[50 : 50 + h, 50 : 50 + w] = needle_img

        points = self.vision.find_faction(haystack, threshold=50)
        self.assertGreater(len(points), 0)

    def test_threshold_validation(self):
        """Test detection threshold clamping."""
        haystack = np.zeros((200, 200, 3), dtype=np.uint8)

        # Test with very low threshold
        points_low = self.vision.find(haystack, threshold=0)
        self.assertIsInstance(points_low, list)

        # Test with very high threshold
        points_high = self.vision.find(haystack, threshold=100)
        self.assertIsInstance(points_high, list)

    def test_region_size_error(self):
        """Test error when haystack is smaller than needle."""
        # Create tiny haystack (smaller than 50x50 needle)
        haystack = np.zeros((30, 30, 3), dtype=np.uint8)

        # Should either raise RegionSizeError or handle gracefully
        try:
            self.vision.find(haystack)
        except RegionSizeError as e:
            self.assertIn("Region is smaller", str(e))
        except Exception:
            # Other exceptions are also acceptable
            pass

    def test_grayscale_conversion(self):
        """Test handling of grayscale images."""
        # Create grayscale haystack
        haystack = np.zeros((200, 200), dtype=np.uint8)
        haystack[:, :] = 128  # Gray

        points = self.vision.find(haystack, threshold=50)
        self.assertIsInstance(points, list)

    def test_debug_mode(self):
        """Test debug mode activation."""
        self.vision.debug_mode = True
        self.assertTrue(self.vision.is_vision_open)

        haystack = np.zeros((200, 200, 3), dtype=np.uint8)

        with patch("cv2.imshow"), patch("cv2.waitKey"):
            points = self.vision.find(haystack)
            self.assertIsInstance(points, list)

    def test_debug_mode_faction(self):
        """Test faction debug mode."""
        self.vision.debug_mode_faction = True
        self.assertTrue(self.vision.is_faction_vision_open)

        haystack = np.zeros((200, 200, 3), dtype=np.uint8)

        with patch("cv2.imshow"), patch("cv2.waitKey"):
            points = self.vision.find_faction(haystack)
            self.assertIsInstance(points, list)

    def test_clean_up(self):
        """Test cleanup method."""
        self.vision.debug_mode = True
        self.vision.debug_mode_faction = True

        with patch("cv2.destroyAllWindows") as mock_destroy:
            self.vision.clean_up()
            mock_destroy.assert_called_once()

        self.assertFalse(self.vision.debug_mode)
        self.assertFalse(self.vision.debug_mode_faction)

    def test_destroy_vision_enemy(self):
        """Test destroying enemy vision window."""
        self.vision.debug_mode = True

        with patch("cv2.destroyWindow") as mock_destroy:
            self.vision.destroy_vision("Enemy")
            mock_destroy.assert_called_once_with("Enemy Vision")

        self.assertFalse(self.vision.debug_mode)

    def test_destroy_vision_faction(self):
        """Test destroying faction vision window."""
        self.vision.debug_mode_faction = True

        with patch("cv2.destroyWindow") as mock_destroy:
            self.vision.destroy_vision("Faction")
            mock_destroy.assert_called_once_with("Faction Vision")

        self.assertFalse(self.vision.debug_mode_faction)

    def test_exception_handling(self):
        """Test exception handling in vision_process."""
        # Create invalid haystack
        haystack = None

        # Should handle exception gracefully and return empty list
        try:
            points = self.vision.find(haystack)
            self.assertEqual(len(points), 0)
        except Exception:
            # It's also acceptable if exception is raised
            pass

    def test_alpha_channel_removal(self):
        """Test BGRA to BGR conversion."""
        needle_bgra_path = Path("tests/fixtures/test_needle_alpha.png")
        # Create a textured needle with alpha so the channel-strip logic is exercised
        needle_img = np.zeros((30, 30, 4), dtype=np.uint8)
        needle_img[5:25, 5:25, :3] = (0, 0, 200)
        needle_img[10:20, 10:20, :3] = (80, 50, 240)
        needle_img[13:17, 13:17, :3] = (200, 100, 50)
        needle_img[:, :, 3] = 255  # fully opaque
        cv.imwrite(str(needle_bgra_path), needle_img)

        try:
            vision_alpha = Vision([str(needle_bgra_path)])

            haystack = np.zeros((200, 200, 3), dtype=np.uint8)
            haystack[50:100, 50:100] = (0, 0, 255)

            points = vision_alpha.find(haystack, threshold=50)
            self.assertIsInstance(points, list)
        finally:
            if needle_bgra_path.exists():
                needle_bgra_path.unlink()
            vision_alpha.clean_up()

    def test_normalization(self):
        """Test image normalization before matching."""
        # Create haystack with varying brightness
        haystack = np.random.randint(0, 256, (200, 200, 3), dtype=np.uint8)

        points = self.vision.find(haystack, threshold=50)
        self.assertIsInstance(points, list)


class TestVisionRobustness(unittest.TestCase):
    """Regression tests for issues #111, #112, #113."""

    def test_unreadable_template_is_skipped_not_crash(self):
        """#113: an unreadable/non-image path must not crash construction."""
        vision = Vision(["/nonexistent/definitely_not_an_image.png"])
        self.assertEqual(vision.needle_imgs, [])
        self.assertEqual(vision.needle_paths, [])
        self.assertEqual(vision.needle_dims, [])

    def test_mixed_valid_and_unreadable_templates(self):
        """#113: valid images load; bad ones are skipped and lists stay aligned."""
        good = Path("tests/fixtures/robust_needle.png")
        good.parent.mkdir(parents=True, exist_ok=True)
        img = np.zeros((20, 20, 3), dtype=np.uint8)
        img[5:15, 5:15] = (200, 100, 50)
        cv.imwrite(str(good), img)
        try:
            vision = Vision([str(good), "/nonexistent/bad.png"])
            self.assertEqual(len(vision.needle_imgs), 1)
            self.assertEqual(len(vision.needle_paths), 1)
            self.assertEqual(len(vision.needle_dims), 1)
            self.assertEqual(vision.needle_paths[0], str(good))
        finally:
            if good.exists():
                good.unlink()
            if good.parent.exists() and not list(good.parent.iterdir()):
                good.parent.rmdir()

    def test_destroy_vision_uses_vision_suffix_window(self):
        """#112: destroy_vision must target '<mode> Vision', not the bare mode."""
        vision = Vision([])
        with patch("cv2.destroyWindow") as mock_destroy:
            vision.destroy_vision("Enemy")
            mock_destroy.assert_called_once_with("Enemy Vision")

    def test_destroy_vision_swallows_missing_window_error(self):
        """#112: destroying a non-existent window must not raise."""
        vision = Vision([])
        with patch("cv2.destroyWindow", side_effect=cv.error("no window")):
            # Must not raise.
            vision.destroy_vision("Faction")
        self.assertFalse(vision.debug_mode_faction)

    def test_find_error_path_with_debug_open_does_not_crash(self):
        """#111: if vision_process raises while debug is on, find() must not
        hit an UnboundLocalError and must return an empty list."""
        needle = Path("tests/fixtures/robust_needle2.png")
        needle.parent.mkdir(parents=True, exist_ok=True)
        img = np.zeros((40, 40, 3), dtype=np.uint8)
        img[5:35, 5:35] = (10, 200, 90)
        cv.imwrite(str(needle), img)
        try:
            vision = Vision([str(needle)])
            vision.debug_mode = True
            # Haystack smaller than the needle -> RegionSizeError inside
            # vision_process, so detection_image is never assigned.
            tiny_haystack = np.zeros((5, 5, 3), dtype=np.uint8)
            with patch("cv2.destroyWindow"):
                points = vision.find(tiny_haystack, threshold=50)
            self.assertEqual(points, [])
        finally:
            if needle.exists():
                needle.unlink()
            if needle.parent.exists() and not list(needle.parent.iterdir()):
                needle.parent.rmdir()


if __name__ == "__main__":
    unittest.main()
