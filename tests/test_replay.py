from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.models import Snapshot
from computer_use_anywhere.replay import (
    ActionVerification,
    SessionReplay,
    verify_action_result,
)


def fake_snapshot(name: str, *, foreground: str = "Edge") -> Snapshot:
    return Snapshot(
        path=Path(name),
        data_url="data:image/jpeg;base64,AA==",
        width=100,
        height=80,
        actual_width=100,
        actual_height=80,
        foreground_window_title=foreground,
        visible_window_titles=[foreground],
    )


class ReplayTests(unittest.TestCase):
    def test_verify_activate_window_checks_foreground_title(self) -> None:
        verification = verify_action_result(
            tool_name="computer",
            arguments={"action": "activate_window", "window_title": "Edge"},
            result_message="已激活窗口",
            before_snapshot=fake_snapshot("before.jpg", foreground="代理"),
            after_snapshot=fake_snapshot("after.jpg", foreground="Bilibili - Edge"),
            own_window_title="Computer Use Anywhere",
        )
        self.assertEqual(verification.status, "ok")

    def test_verify_warns_when_input_returns_to_own_window(self) -> None:
        verification = verify_action_result(
            tool_name="computer",
            arguments={"action": "type", "text": "abc"},
            result_message="已输入 3 个字符。",
            before_snapshot=fake_snapshot("before.jpg", foreground="Edge"),
            after_snapshot=fake_snapshot("after.jpg", foreground="Computer Use Anywhere"),
            own_window_title="Computer Use Anywhere",
        )
        self.assertEqual(verification.status, "warn")
        self.assertIn("代理窗口", verification.message)

    def test_replay_writes_jsonl_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            replay = SessionReplay(root)
            replay.record_initial(task="打开 B 站", provider_kind="openai_compatible", snapshot=fake_snapshot("001_initial.jpg"))
            replay.record_step(
                step=1,
                tool_name="computer",
                arguments={"action": "left_click", "coordinate": [10, 20]},
                result_message="已点击",
                verification=ActionVerification("ok", "动作已执行"),
                before_snapshot=fake_snapshot("001_initial.jpg"),
                after_snapshot=fake_snapshot("002_left_click.jpg"),
                model_text="<script>alert(1)</script>",
                public_reasoning="点击地址栏",
            )
            html_path = replay.write_html("完成")
            lines = replay.jsonl_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["verification"]["status"], "ok")
            html_text = html_path.read_text(encoding="utf-8")
            self.assertIn("Computer Use 会话复盘", html_text)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_text)


if __name__ == "__main__":
    unittest.main()
