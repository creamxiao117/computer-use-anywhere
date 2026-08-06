"""provider_kind → 工具裁剪行为的契约测试。

背景：ruff 静态检查（F841）发现 `agent.run()` 第 305 行计算了
`semi_official_mode` 却从未使用，怀疑 semi-official 分支漏写。

实测结论：**行为是正确的，属纯死代码，不是逻辑缺陷**。
因为第 301 行 `official_mode` 用 isinstance 检查
`(AnthropicOfficialProvider, SemiOfficialProvider)`，而
`SemiOfficialProvider` 本身就是 `AnthropicOfficialProvider` 的子类，
semi-official 已被 `official_mode` 完整覆盖。

但该正确性**依赖于继承链**这一隐式前提：若日后有人让
SemiOfficialProvider 不再继承 AnthropicOfficialProvider，
browser_dom 的裁剪行为会静默改变且无任何测试拦截。
本文件把这一隐式契约显式固定下来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.models import (
    PROVIDER_ANTHROPIC_OFFICIAL,
    PROVIDER_OFFICIAL_COMPATIBLE,
    PROVIDER_SEMI_OFFICIAL,
    ProviderConfig,
)
from computer_use_anywhere.provider import (
    AnthropicOfficialProvider,
    OpenAICompatibleProvider,
    SemiOfficialProvider,
    create_provider,
)


def _config(kind: str) -> ProviderConfig:
    return ProviderConfig(
        provider_kind=kind,
        model="claude-sonnet-4-5",
        api_key="test-key",
        base_url="https://example.test/v1",
    )


@pytest.mark.parametrize(
    "kind,expected_cls",
    [
        (PROVIDER_ANTHROPIC_OFFICIAL, AnthropicOfficialProvider),
        (PROVIDER_SEMI_OFFICIAL, SemiOfficialProvider),
        (PROVIDER_OFFICIAL_COMPATIBLE, OpenAICompatibleProvider),
        ("openai_compatible", OpenAICompatibleProvider),
    ],
)
def test_create_provider_maps_kind_to_class(kind, expected_cls):
    assert type(create_provider(_config(kind))) is expected_cls


def test_semi_official_provider_subclasses_official():
    """固定隐式契约：agent.run() 的 official_mode 判定依赖此继承关系。

    若此断言失败，说明有人改了继承链，`agent.run()` 中
    `official_mode` 将不再覆盖 semi-official，browser_dom 裁剪行为会静默改变。
    """
    assert issubclass(SemiOfficialProvider, AnthropicOfficialProvider)


@pytest.mark.parametrize(
    "kind,should_strip_browser_dom",
    [
        (PROVIDER_ANTHROPIC_OFFICIAL, True),
        (PROVIDER_SEMI_OFFICIAL, True),  # 经 isinstance 归入 official_mode
        (PROVIDER_OFFICIAL_COMPATIBLE, True),  # 由独立的 official_compatible_mode 命中
        ("openai_compatible", False),  # 唯一保留 browser_dom 的模式
    ],
)
def test_browser_dom_stripping_decision_per_provider_kind(kind, should_strip_browser_dom):
    """复刻 agent.run() 第 301-310 行的裁剪判定逻辑（browser_dom_enabled=True 前提）。

    这里刻意复刻而非调用 run()，因为 run() 需要真实桌面控制器；
    待 P0-2 的 seam test 建立 FakeDesktopController 后，应改为直接驱动 run()。
    """
    provider = create_provider(_config(kind))
    official_mode = isinstance(provider, (AnthropicOfficialProvider, SemiOfficialProvider))
    official_compatible_mode = kind == PROVIDER_OFFICIAL_COMPATIBLE
    stripped = official_mode or official_compatible_mode
    assert stripped is should_strip_browser_dom
