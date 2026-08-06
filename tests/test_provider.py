from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.models import (
    PROVIDER_ANTHROPIC_OFFICIAL,
    PROVIDER_OFFICIAL_COMPATIBLE,
    ProviderConfig,
)
from computer_use_anywhere.provider import (
    ANTHROPIC_BETA_2025_01_24,
    ANTHROPIC_BETA_2025_11_24,
    ANTHROPIC_TOOL_2025_01_24,
    ANTHROPIC_TOOL_2025_11_24,
    AnthropicOfficialProvider,
    OpenAICompatibleProvider,
    guess_anthropic_computer_contract,
    provider_diagnostics,
)


class ProviderTests(unittest.TestCase):
    def test_completion_url_adds_default_suffix(self) -> None:
        self.assertEqual(
            OpenAICompatibleProvider._completion_url("https://openrouter.ai/api"),
            "https://openrouter.ai/api/v1/chat/completions",
        )

    def test_completion_url_keeps_explicit_endpoint(self) -> None:
        self.assertEqual(
            OpenAICompatibleProvider._completion_url("https://example.com/custom/chat/completions"),
            "https://example.com/custom/chat/completions",
        )

    def test_guess_anthropic_contract_for_sonnet_4_5_models(self) -> None:
        beta, tool = guess_anthropic_computer_contract("claude-sonnet-4.5")
        self.assertEqual(beta, ANTHROPIC_BETA_2025_01_24)
        self.assertEqual(tool, ANTHROPIC_TOOL_2025_01_24)

    def test_guess_anthropic_contract_for_opus_4_5_models(self) -> None:
        beta, tool = guess_anthropic_computer_contract("claude-opus-4.5")
        self.assertEqual(beta, ANTHROPIC_BETA_2025_11_24)
        self.assertEqual(tool, ANTHROPIC_TOOL_2025_11_24)

    def test_guess_anthropic_contract_for_4_models(self) -> None:
        beta, tool = guess_anthropic_computer_contract("claude-sonnet-4")
        self.assertEqual(beta, ANTHROPIC_BETA_2025_01_24)
        self.assertEqual(tool, ANTHROPIC_TOOL_2025_01_24)

    def test_anthropic_messages_url_adds_messages_suffix(self) -> None:
        self.assertEqual(
            AnthropicOfficialProvider._messages_url("https://api.anthropic.com"),
            "https://api.anthropic.com/v1/messages",
        )

    def test_parse_response_reads_tool_call(self) -> None:
        parsed = OpenAICompatibleProvider._parse_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "computer",
                        "arguments": '{"action":"left_click","coordinate":[12,34]}',
                    },
                }
            ]
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].arguments["action"], "left_click")

    def test_openai_tool_schema_includes_window_activation(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderConfig(base_url="https://x.test", api_key="k", model="m")
        )
        computer_tool = next(
            tool
            for tool in provider.build_tools(1000, 800)
            if tool["function"]["name"] == "computer"
        )
        schema = computer_tool["function"]["parameters"]
        self.assertIn("activate_window", schema["properties"]["action"]["enum"])
        self.assertIn("window_title", schema["properties"])
        self.assertIn("expected_window_title", schema["properties"])

    def test_openai_tool_schema_includes_browser_dom(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderConfig(base_url="https://x.test", api_key="k", model="m")
        )
        browser_tool = next(
            tool
            for tool in provider.build_tools(1000, 800)
            if tool["function"]["name"] == "browser_dom"
        )
        schema = browser_tool["function"]["parameters"]
        self.assertIn("read_page", schema["properties"]["action"]["enum"])
        self.assertIn("click_selector", schema["properties"]["action"]["enum"])
        self.assertIn("type_selector", schema["properties"]["action"]["enum"])
        self.assertIn("wait_text", schema["properties"]["action"]["enum"])
        self.assertIn("press_selector", schema["properties"]["action"]["enum"])
        self.assertIn("timeout_seconds", schema["properties"])

    def test_anthropic_provider_parses_tool_use_blocks(self) -> None:
        provider = AnthropicOfficialProvider(
            ProviderConfig(
                provider_kind=PROVIDER_ANTHROPIC_OFFICIAL,
                base_url="https://api.anthropic.com",
                api_key="k",
                model="claude-sonnet-4.5",
            )
        )
        response = {
            "content": [
                {"type": "thinking", "thinking": "hidden", "signature": "sig"},
                {"type": "text", "text": "我会先点击搜索框。"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "computer",
                    "input": {"action": "left_click", "coordinate": [100, 200]},
                },
            ],
            "stop_reason": "tool_use",
        }
        reply = provider._parse_response_body(response)
        self.assertEqual(reply.text, "我会先点击搜索框。")
        self.assertEqual(reply.tool_calls[0].call_id, "toolu_1")
        self.assertIn("thinking", reply.reasoning_summary)

    def test_diagnostics_warn_when_official_mode_uses_chat_completions_url(self) -> None:
        diagnostics = provider_diagnostics(
            ProviderConfig(
                provider_kind=PROVIDER_ANTHROPIC_OFFICIAL,
                base_url="https://proxy.test/v1/chat/completions",
                api_key="k",
                model="claude-sonnet-4.5",
            )
        )
        self.assertTrue(any("/chat/completions" in item for item in diagnostics))

    def test_diagnostics_warn_when_official_tool_type_mismatches_model(self) -> None:
        diagnostics = provider_diagnostics(
            ProviderConfig(
                provider_kind=PROVIDER_ANTHROPIC_OFFICIAL,
                base_url="https://api.anthropic.com",
                api_key="k",
                model="claude-opus-4.5",
                anthropic_tool_type=ANTHROPIC_TOOL_2025_01_24,
            )
        )
        self.assertTrue(any(ANTHROPIC_TOOL_2025_11_24 in item for item in diagnostics))

    def test_diagnostics_warn_when_compatible_mode_uses_messages_url(self) -> None:
        diagnostics = provider_diagnostics(
            ProviderConfig(
                base_url="https://api.anthropic.com/v1/messages",
                api_key="k",
                model="claude-sonnet-4.5",
            )
        )
        self.assertTrue(any("/messages" in item for item in diagnostics))

    def test_diagnostics_warn_when_official_compatible_uses_messages_url(self) -> None:
        diagnostics = provider_diagnostics(
            ProviderConfig(
                provider_kind=PROVIDER_OFFICIAL_COMPATIBLE,
                base_url="https://api.anthropic.com/v1/messages",
                api_key="k",
                model="claude-sonnet-4.5",
            )
        )
        self.assertTrue(any("官方体验兼容模式" in item for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
