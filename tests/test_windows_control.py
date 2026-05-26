from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.models import SessionConfig
from computer_use_anywhere.windows_control import WindowsDesktopController


class WindowsControlTests(unittest.TestCase):
    def test_translate_coordinate_scales_linearly(self) -> None:
        self.assertEqual(
            WindowsDesktopController.translate_coordinate(50, capture_size=100, actual_size=200),
            101,
        )

    def test_translate_coordinate_clamps_high_values(self) -> None:
        self.assertEqual(
            WindowsDesktopController.translate_coordinate(999, capture_size=100, actual_size=200),
            199,
        )

    def test_clip_rect_rejects_tiny_overlap(self) -> None:
        self.assertIsNone(WindowsDesktopController._clip_rect(10, 10, 12, 12, 100, 100))

    def test_clip_rect_clamps_to_image_bounds(self) -> None:
        self.assertEqual(
            WindowsDesktopController._clip_rect(-5, 10, 120, 80, 100, 100),
            (0, 10, 100, 80),
        )

    def test_to_capture_coordinate_scales_linearly(self) -> None:
        self.assertEqual(
            WindowsDesktopController.to_capture_coordinate(100, actual_size=200, capture_size=100),
            50,
        )

    def test_coordinate_in_masked_region(self) -> None:
        controller = WindowsDesktopController.__new__(WindowsDesktopController)
        controller._last_masked_regions_capture = [(10, 10, 30, 30)]
        self.assertTrue(controller._coordinate_in_masked_region(20, 20))
        self.assertFalse(controller._coordinate_in_masked_region(40, 40))

    def test_coordinate_bounds_rejects_real_screen_like_coordinates(self) -> None:
        controller = WindowsDesktopController.__new__(WindowsDesktopController)
        controller.capture_width = 100
        controller.capture_height = 80
        self.assertTrue(controller.has_out_of_bounds_coordinate({"action": "left_click", "coordinate": [120, 20]}))
        self.assertTrue(
            controller.has_out_of_bounds_coordinate(
                {"action": "left_click_drag", "start_coordinate": [10, 10], "end_coordinate": [10, 90]}
            )
        )
        self.assertFalse(controller.has_out_of_bounds_coordinate({"action": "left_click", "coordinate": [99, 79]}))

    def test_enter_key_gets_longer_post_action_delay(self) -> None:
        self.assertGreaterEqual(
            WindowsDesktopController._post_action_delay("key", {"keys": "enter"}),
            0.8,
        )
        self.assertGreaterEqual(
            WindowsDesktopController._post_action_delay("key", {"keys": "return"}),
            0.8,
        )
        self.assertEqual(WindowsDesktopController._post_action_delay("wait", {"seconds": 1}), 0.0)

    def test_common_key_aliases_are_supported(self) -> None:
        self.assertEqual(WindowsDesktopController._vk_code("return"), WindowsDesktopController._vk_code("enter"))
        self.assertEqual(WindowsDesktopController._vk_code("del"), WindowsDesktopController._vk_code("delete"))

    def test_is_supported_action(self) -> None:
        controller = WindowsDesktopController.__new__(WindowsDesktopController)
        self.assertTrue(controller.is_supported_action({"action": "left_click"}))
        self.assertTrue(controller.is_supported_action({"action": "activate_window"}))
        self.assertFalse(controller.is_supported_action({"action": "drag_window"}))

    def test_foreground_guard_rejects_own_window_for_typing(self) -> None:
        controller = WindowsDesktopController.__new__(WindowsDesktopController)
        controller.settings = SessionConfig(own_window_title="Computer Use Anywhere")
        controller.get_foreground_window_title = lambda: "Computer Use Anywhere"
        self.assertFalse(controller.is_foreground_safe_for_action({"action": "type", "text": "hello"}))

    def test_foreground_guard_checks_expected_window_title(self) -> None:
        controller = WindowsDesktopController.__new__(WindowsDesktopController)
        controller.settings = SessionConfig(own_window_title="Computer Use Anywhere")
        controller.get_foreground_window_title = lambda: "Bilibili - Microsoft Edge"
        self.assertTrue(
            controller.is_foreground_safe_for_action(
                {"action": "key", "keys": "enter", "expected_window_title": "Edge"}
            )
        )
        self.assertFalse(
            controller.is_foreground_safe_for_action(
                {"action": "key", "keys": "enter", "expected_window_title": "Chrome"}
            )
        )


if __name__ == "__main__":
    unittest.main()
