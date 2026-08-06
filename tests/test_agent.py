from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.agent import (
    ComputerUseAgent,
    build_system_prompt,
    normalize_computer_action,
    should_apply_browser_dom_first_guard,
    task_prefers_browser_dom,
)
from computer_use_anywhere.models import ProviderConfig, SessionConfig, Snapshot
from computer_use_anywhere.provider import OpenAICompatibleProvider


class AgentTests(unittest.TestCase):
    def test_prompt_mentions_screenshot_coordinate_space(self) -> None:
        prompt = build_system_prompt(1920, 1080, 1536, 864)
        self.assertIn("1920x1080", prompt)
        self.assertIn("1536x864", prompt)
        self.assertIn("所有坐标都必须使用截图尺寸", prompt)

    def test_prompt_can_switch_to_official_mode(self) -> None:
        prompt = build_system_prompt(1920, 1080, 1536, 864, official_mode=True)
        self.assertIn("Anthropic 官方 computer use 协议", prompt)

    def test_prompt_can_use_pure_official_mode(self) -> None:
        prompt = build_system_prompt(
            1920, 1080, 1536, 864, official_mode=True, official_enhanced=False
        )
        self.assertIn("官方纯原生", prompt)
        self.assertNotIn("browser_dom", prompt)
        self.assertNotIn("遮挡或涂灰", prompt)

    def test_prompt_can_use_official_compatible_mode(self) -> None:
        prompt = build_system_prompt(1920, 1080, 1280, 720, official_compatible_mode=True)
        self.assertIn("官方体验兼容模式", prompt)
        self.assertIn("OpenAI-compatible", prompt)
        self.assertIn("只能通过 computer 工具", prompt)

    def test_normalize_computer_action_accepts_aliases(self) -> None:
        normalized = normalize_computer_action({"action": "click", "coordinate": "[10, 20]"})
        self.assertEqual(normalized["action"], "left_click")
        self.assertEqual(normalized["coordinate"], [10, 20])

    def test_normalize_computer_action_accepts_window_alias(self) -> None:
        normalized = normalize_computer_action({"action": "focus_window", "window_title": "Edge"})
        self.assertEqual(normalized["action"], "activate_window")

    def test_normalize_computer_action_accepts_coordinate_dict(self) -> None:
        normalized = normalize_computer_action(
            {"action": "click", "coordinate": {"x": "10.4", "y": 20}}
        )
        self.assertEqual(normalized["action"], "left_click")
        self.assertEqual(normalized["coordinate"], [10, 20])

    def test_normalize_computer_action_accepts_loose_coordinate_string(self) -> None:
        normalized = normalize_computer_action({"action": "left_click", "coordinate": "x=10, y=20"})
        self.assertEqual(normalized["coordinate"], [10, 20])

    def test_openai_tool_schema_exposes_single_computer_function(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderConfig(base_url="https://x.test", api_key="k", model="m")
        )
        tools = provider.build_tools(1000, 800)
        names = [tool["function"]["name"] for tool in tools]
        self.assertIn("computer", names)
        self.assertIn("browser_dom", names)
        computer_tool = next(tool for tool in tools if tool["function"]["name"] == "computer")
        properties = computer_tool["function"]["parameters"]["properties"]
        self.assertIn("public_reasoning", properties)
        self.assertIn("expected_window_title", properties)

    def test_desktop_state_text_includes_visible_windows(self) -> None:
        snapshot = Snapshot(
            path=ROOT / "fake.jpg",
            data_url="data:image/jpeg;base64,AA==",
            width=100,
            height=80,
            actual_width=100,
            actual_height=80,
            foreground_window_title="Bilibili - Edge",
            visible_window_titles=["Bilibili - Edge", "记事本"],
        )
        state = ComputerUseAgent._desktop_state_text(snapshot)
        self.assertIn("当前前台窗口标题：Bilibili - Edge", state)
        self.assertIn("当前可见窗口标题：Bilibili - Edge；记事本", state)

    def test_browser_dom_state_text_mentions_port(self) -> None:
        agent = ComputerUseAgent.__new__(ComputerUseAgent)
        agent.browser_dom = object()
        agent.session_config = SessionConfig(
            browser_debug_host="127.0.0.1", browser_debug_port=9222
        )
        state = agent._browser_dom_state_text(False)
        self.assertIn("浏览器 DOM 工具：已启用", state)
        self.assertIn("127.0.0.1:9222", state)

    def test_web_task_prefers_browser_dom(self) -> None:
        self.assertTrue(task_prefers_browser_dom("打开 B 站并搜索猫"))
        self.assertTrue(task_prefers_browser_dom("https://example.com 填写表单"))
        self.assertFalse(task_prefers_browser_dom("打开本地记事本"))

    def test_dom_first_guard_blocks_first_visual_web_action(self) -> None:
        self.assertTrue(
            should_apply_browser_dom_first_guard(
                task="打开 bilibili.com",
                official_mode=False,
                browser_dom_enabled=True,
                browser_dom_first=True,
                browser_dom_attempted=False,
                guard_used=False,
                tool_name="computer",
                arguments={"action": "left_click", "coordinate": [10, 20]},
            )
        )

    def test_dom_first_guard_does_not_block_after_dom_attempt(self) -> None:
        self.assertFalse(
            should_apply_browser_dom_first_guard(
                task="打开 bilibili.com",
                official_mode=False,
                browser_dom_enabled=True,
                browser_dom_first=True,
                browser_dom_attempted=True,
                guard_used=False,
                tool_name="computer",
                arguments={"action": "left_click", "coordinate": [10, 20]},
            )
        )

    def test_dom_first_guard_ignores_official_mode(self) -> None:
        self.assertFalse(
            should_apply_browser_dom_first_guard(
                task="打开 bilibili.com",
                official_mode=True,
                browser_dom_enabled=True,
                browser_dom_first=True,
                browser_dom_attempted=False,
                guard_used=False,
                tool_name="computer",
                arguments={"action": "left_click", "coordinate": [10, 20]},
            )
        )


if __name__ == "__main__":
    unittest.main()
