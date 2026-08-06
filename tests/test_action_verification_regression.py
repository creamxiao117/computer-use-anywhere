"""动作校验链路的回归测试。

背景：ruff 静态检查（F841）发现 `replay.verify_action_result` 计算了
`lowered_result = result.casefold()` 却从未使用，失败判定仅匹配中文关键词。
本文件用可执行断言把该缺陷及其边界固定下来。

设计约定：
1. **纯内存、零文件 I/O**。`verify_action_result` 只比较 `Snapshot.path` 对象
   （replay.py:226），从不读取图片内容，因此用不存在的路径即可完整驱动所有分支。
   刻意不使用 `tmp_path` fixture —— 它会产生临时目录并触发 pytest 的滚动清理，
   在受限环境下清理被拦截会导致 pytest 进程整体失败（非测试失败），
   表现为**非确定性**的 ERROR。门禁测试必须确定性且无副作用。
2. 已确认为缺陷、但尚未修复的期望行为，用 `@pytest.mark.xfail(strict=True)` 标注。
   套件保持绿灯（不阻塞 pre-commit 门禁）；一旦缺陷被修复，strict xfail 会转为
   XPASS 失败（已实测：退出码 1），强制提醒删除标记 —— 缺陷不会被悄悄遗忘。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.models import Snapshot
from computer_use_anywhere.replay import verify_action_result


def _snapshot(name: str, *, foreground: str = "Notepad") -> Snapshot:
    """构造一个仅用于校验逻辑的 Snapshot；路径无需真实存在。"""
    return Snapshot(
        path=Path(name),
        data_url="data:image/jpeg;base64,AA==",
        width=100,
        height=80,
        actual_width=100,
        actual_height=80,
        foreground_window_title=foreground,
    )


def _verify(result_message: str, *, before: str = "before.jpg", after: str = "after.jpg"):
    """驱动 verify_action_result；默认前后路径不同，避开"截图文件相同"兜底分支。"""
    return verify_action_result(
        tool_name="computer",
        arguments={"action": "left_click", "coordinate": [10, 10]},
        result_message=result_message,
        before_snapshot=_snapshot(before),
        after_snapshot=_snapshot(after),
        own_window_title="Agent",
    )


# --------------------------------------------------------------------------
# 现状基线：中文失败关键词可被正确识别
# --------------------------------------------------------------------------


@pytest.mark.parametrize("message", ["点击失败", "执行出错", "操作被拒绝", "该功能不可用"])
def test_chinese_failure_keywords_are_detected(message):
    assert _verify(message).status == "warn"


# --------------------------------------------------------------------------
# 缺陷 #1：英文失败信息被判为成功（replay.py:155 casefold 结果被丢弃）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Failed to click element",
        "ERROR: element not found",
        "Denied by permission policy",
        "Element unavailable",
        "FAILED",  # 大小写变体：casefold 后也应命中
    ],
)
def test_english_failure_keywords_should_be_detected(message):
    assert _verify(message).status == "warn"


def test_english_failure_detected_independent_of_snapshot_paths():
    """修复后：无论前后截图路径是否相同，英文失败串都会被关键词直接识别。

    此前该场景靠"截图文件相同"兜底偶然返回 warn（掩盖了英文识别缺陷）；
    现在英文失败识别不依赖截图路径，是确定性的正确行为。
    """
    verification = _verify("Failed to click element", before="same.jpg", after="same.jpg")
    assert verification.status == "warn"
    assert "失败" in verification.message


# --------------------------------------------------------------------------
# 相邻分支的补充覆盖（这些分支此前均为 0 覆盖）
# --------------------------------------------------------------------------


def test_browser_dom_tool_short_circuits_to_ok():
    result = verify_action_result(
        tool_name="browser_dom",
        arguments={"action": "click_selector"},
        result_message="clicked",
        before_snapshot=_snapshot("a.jpg"),
        after_snapshot=_snapshot("b.jpg"),
        own_window_title="Agent",
    )
    assert result.status == "ok"


@pytest.mark.parametrize("action", ["screenshot", "wait"])
def test_observation_actions_are_info(action):
    result = verify_action_result(
        tool_name="computer",
        arguments={"action": action},
        result_message="done",
        before_snapshot=_snapshot("a.jpg"),
        after_snapshot=_snapshot("b.jpg"),
        own_window_title="Agent",
    )
    assert result.status == "info"


@pytest.mark.parametrize("action", ["type", "key"])
def test_input_action_warns_when_agent_window_still_foreground(action):
    """输入类动作执行后前台仍是代理自身窗口 —— 说明输入很可能没进目标应用。"""
    result = verify_action_result(
        tool_name="computer",
        arguments={"action": action, "text": "hello"},
        result_message="typed",
        before_snapshot=_snapshot("a.jpg"),
        after_snapshot=_snapshot("b.jpg", foreground="My Agent Window"),
        own_window_title="My Agent Window",
    )
    assert result.status == "warn"
    assert "代理窗口" in result.message


def test_activate_window_ok_when_foreground_matches():
    result = verify_action_result(
        tool_name="computer",
        arguments={"action": "activate_window", "window_title": "Notepad"},
        result_message="activated",
        before_snapshot=_snapshot("a.jpg"),
        after_snapshot=_snapshot("b.jpg", foreground="Untitled - Notepad"),
        own_window_title="Agent",
    )
    assert result.status == "ok"


def test_activate_window_warns_when_foreground_mismatches():
    result = verify_action_result(
        tool_name="computer",
        arguments={"action": "activate_window", "window_title": "Calculator"},
        result_message="activated",
        before_snapshot=_snapshot("a.jpg"),
        after_snapshot=_snapshot("b.jpg", foreground="Untitled - Notepad"),
        own_window_title="Agent",
    )
    assert result.status == "warn"


def test_click_with_no_visible_change_warns_with_coordinates():
    """点击后画面几乎无变化 —— 应给出带坐标的强提示，这是纠偏的关键反馈。"""
    result = verify_action_result(
        tool_name="computer",
        arguments={"action": "left_click", "coordinate": [640, 480]},
        result_message="点击完成，画面几乎没有变化",
        before_snapshot=_snapshot("a.jpg"),
        after_snapshot=_snapshot("b.jpg"),
        own_window_title="Agent",
    )
    assert result.status == "warn"
    assert "640" in result.message and "480" in result.message
