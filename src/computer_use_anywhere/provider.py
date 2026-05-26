from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from .models import (
    AssistantReply,
    PROVIDER_ANTHROPIC_OFFICIAL,
    PROVIDER_OFFICIAL_COMPATIBLE,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_SEMI_OFFICIAL,
    ProviderConfig,
    Snapshot,
    ToolCall,
)


ANTHROPIC_VERSION_DEFAULT = "2023-06-01"
ANTHROPIC_BETA_2024_10_22 = "computer-use-2024-10-22"
ANTHROPIC_BETA_2025_01_24 = "computer-use-2025-01-24"
ANTHROPIC_BETA_2025_11_24 = "computer-use-2025-11-24"
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
COMPACT_2026_01_12_BETA = "compact-2026-01-12"

ANTHROPIC_TOOL_2024_10_22 = "computer_20241022"
ANTHROPIC_TOOL_2025_01_24 = "computer_20250124"
ANTHROPIC_TOOL_2025_11_24 = "computer_20251124"


DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def default_browser_headers(base_url: str) -> dict[str, str]:
    """Chrome-flavored default headers to dodge Cloudflare 1010 on relay sites.

    Skipped for api.anthropic.com itself (the official endpoint does not
    require browser-like headers and adding them is just noise).
    """
    parsed = urlparse((base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return {}
    if "api.anthropic.com" in parsed.netloc:
        return {}
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "User-Agent": DEFAULT_BROWSER_UA,
        "Origin": origin,
        "Referer": origin + "/",
    }


class ProviderError(RuntimeError):
    pass


def guess_anthropic_computer_contract(model: str) -> tuple[str, str]:
    normalized = (model or "").lower()
    newest_markers = ("4.6", "4-6", "4_6", "4.7", "4-7", "4_7")
    if any(marker in normalized for marker in newest_markers):
        return ANTHROPIC_BETA_2025_11_24, ANTHROPIC_TOOL_2025_11_24
    if "opus-4.5" in normalized or "opus 4.5" in normalized or "opus4.5" in normalized:
        return ANTHROPIC_BETA_2025_11_24, ANTHROPIC_TOOL_2025_11_24
    modern_markers = ("claude-4", "sonnet-4", "opus-4", "haiku-4", "3.7", "3-7", "3_7")
    if any(marker in normalized for marker in modern_markers):
        return ANTHROPIC_BETA_2025_01_24, ANTHROPIC_TOOL_2025_01_24
    return ANTHROPIC_BETA_2024_10_22, ANTHROPIC_TOOL_2024_10_22


def create_provider(config: ProviderConfig):
    if config.provider_kind == PROVIDER_ANTHROPIC_OFFICIAL:
        return AnthropicOfficialProvider(config)
    if config.provider_kind == PROVIDER_SEMI_OFFICIAL:
        return SemiOfficialProvider(config)
    return OpenAICompatibleProvider(config)


def provider_diagnostics(config: ProviderConfig) -> list[str]:
    if config.provider_kind == PROVIDER_ANTHROPIC_OFFICIAL:
        return anthropic_official_diagnostics(config)
    if config.provider_kind == PROVIDER_SEMI_OFFICIAL:
        return semi_official_diagnostics(config)
    return openai_compatible_diagnostics(config)


def anthropic_official_diagnostics(config: ProviderConfig) -> list[str]:
    diagnostics: list[str] = []
    cleaned = (config.base_url or "").strip().rstrip("/")
    lowered = cleaned.lower()
    guessed_beta, guessed_tool = guess_anthropic_computer_contract(config.model)

    if "/chat/completions" in lowered:
        diagnostics.append(
            "官方模式会调用 Anthropic Messages API；当前接口地址包含 /chat/completions，"
            "更像兼容中转站地址。如果中转站不支持 Anthropic Messages 协议，请切回兼容中转站模式。"
        )
    if "openrouter.ai" in lowered:
        diagnostics.append(
            "OpenRouter 通常走 OpenAI-compatible /chat/completions；官方模式需要中转站原样支持 "
            "Anthropic Messages 的请求体、content blocks 和 anthropic-beta。"
        )
    if "openai" in lowered and "anthropic" not in lowered:
        diagnostics.append("当前接口看起来像 OpenAI 兼容服务；官方模式可能因为协议不匹配而 400/404。")

    manual_beta = config.anthropic_beta.strip()
    if manual_beta:
        beta_values = {value.strip() for value in manual_beta.split(",") if value.strip()}
        if guessed_beta not in beta_values:
            diagnostics.append(
                f"按模型名建议 anthropic-beta={guessed_beta}，但当前填写的是 {manual_beta}；"
                "如果 computer 工具不可用，先改回建议 beta。"
            )

    manual_tool = config.anthropic_tool_type.strip()
    if manual_tool and manual_tool != guessed_tool:
        diagnostics.append(
            f"按模型名建议 computer tool={guessed_tool}，但当前填写的是 {manual_tool}；"
            "tool 类型不匹配时模型可能不会返回 tool_use。"
        )

    if config.enable_thinking and config.thinking_budget < 1024:
        diagnostics.append("官方 thinking token 预算低于 1024，实际请求会自动提升到 1024。")

    for header_name in config.extra_headers:
        normalized = header_name.lower()
        if normalized in {"anthropic-beta", "anthropic-version", "x-api-key"}:
            diagnostics.append(f"额外请求头里包含 {header_name}，会覆盖界面里的官方协议设置。")

    return diagnostics


def openai_compatible_diagnostics(config: ProviderConfig) -> list[str]:
    diagnostics: list[str] = []
    cleaned = (config.base_url or "").strip().rstrip("/")
    lowered = cleaned.lower()

    if lowered.endswith("/messages") or "/v1/messages" in lowered:
        diagnostics.append(
            "兼容模式会调用 /chat/completions；当前接口地址看起来是 Anthropic /messages。"
            "如果这是官方 Anthropic 地址，请切到官方模式。"
        )
    if "api.anthropic.com" in lowered:
        diagnostics.append("api.anthropic.com 原生不走 OpenAI-compatible 工具调用；建议使用官方模式。")
    if lowered.endswith("/responses"):
        diagnostics.append("当前接口像 OpenAI Responses API；兼容模式需要 chat/completions。")
    if config.extra_body.get("stream") is True:
        diagnostics.append("当前项目还不消费流式响应；extra body 里 stream=true 会导致解析失败，建议关闭。")
    if config.provider_kind == PROVIDER_OFFICIAL_COMPATIBLE and ("api.anthropic.com" in lowered or "/messages" in lowered):
        diagnostics.append("官方体验兼容模式仍走 /chat/completions；如果你使用原生 Anthropic /messages，请切到真官方模式。")

    return diagnostics


def semi_official_diagnostics(config: ProviderConfig) -> list[str]:
    diagnostics: list[str] = []
    cleaned = (config.base_url or "").strip().rstrip("/")
    lowered = cleaned.lower()
    if "/chat/completions" in lowered:
        diagnostics.append(
            "半官方模式走 /messages 协议；当前接口地址包含 /chat/completions，中转站可能不支持该路径。"
            "请检查中转站使用说明，确认它是否支持 /v1/messages。"
        )
    if not config.anthropic_version.strip():
        diagnostics.append("未填写 anthropic-version，部分中转站可能因为缺少版本号拒绝请求。")
    if not config.anthropic_beta.strip() and not config.extra_headers:
        diagnostics.append(
            "未填写 anthropic-beta 也无额外请求头。中转站如果支持透明转发 beta 头，"
            "界面会自动注入 computer-use-2025-11-24 + thinking/compact 等 beta。"
        )
    if config.enable_compact and not config.anthropic_beta.strip():
        diagnostics.append(
            "已启用 compact-2026-01-12：beta 头虽然未手动填写，但会跟随 thinking/computer-use beta 一同自动发送。"
        )
    return diagnostics


class OpenAICompatibleProvider:
    kind = PROVIDER_OPENAI_COMPATIBLE

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def build_tools(self, capture_width: int, capture_height: int) -> list[dict[str, Any]]:
        description = (
            "控制用户当前正在使用的 Windows 电脑。"
            f"所有坐标都必须基于当前截图尺寸 {capture_width}x{capture_height}，"
            "不能使用真实屏幕像素。每次工具调用只允许执行一个小动作。"
            "如果目标窗口可见但不在前台，优先使用 activate_window 按标题激活窗口。"
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": "computer",
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "screenshot",
                                    "mouse_move",
                                    "left_click",
                                    "double_click",
                                    "right_click",
                                    "middle_click",
                                    "left_click_drag",
                                    "type",
                                    "key",
                                    "scroll",
                                    "wait",
                                    "activate_window",
                                ],
                                "description": "下一步要执行的界面动作。",
                            },
                            "window_title": {
                                "type": "string",
                                "description": "当 action=activate_window 时，填写目标窗口标题里的关键词，例如 Edge、Chrome、记事本、Bilibili。",
                            },
                            "expected_window_title": {
                                "type": "string",
                                "description": "当 action=type 或 action=key 可能产生输入时，填写期望的前台窗口标题关键词。若当前前台不匹配，工具会拒绝执行，避免输错窗口。",
                            },
                            "coordinate": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                                "description": "目标坐标 [x, y]，单位是截图像素。",
                            },
                            "start_coordinate": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                                "description": "拖拽起点 [x, y]，单位是截图像素。",
                            },
                            "end_coordinate": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                                "description": "拖拽终点 [x, y]，单位是截图像素。",
                            },
                            "text": {
                                "type": "string",
                                "description": "当 action=type 时要输入的文本。",
                            },
                            "keys": {
                                "type": ["array", "string"],
                                "description": "当 action=key 时要按的组合键，例如 ['ctrl', 'l'] 或 'ctrl+l'。",
                                "items": {"type": "string"},
                            },
                            "scroll_amount": {
                                "type": "integer",
                                "description": "滚轮数值，正数向上，负数向下。",
                            },
                            "seconds": {
                                "type": "number",
                                "description": "当 action=wait 时等待的秒数。",
                            },
                            "duration_ms": {
                                "type": "integer",
                                "description": "可选时长，单位毫秒。",
                            },
                            "modifiers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "执行操作时临时按住的修饰键。",
                            },
                            "reason": {
                                "type": "string",
                                "description": "用一句简短中文说明为什么要执行这一步。",
                            },
                            "public_reasoning": {
                                "type": "string",
                                "description": "给用户看的公开说明，简短描述你看到了什么、准备做什么。",
                            },
                        },
                        "required": ["action"],
                    },
                },
            },
            self._browser_dom_tool_schema(),
        ]

    @staticmethod
    def _browser_dom_tool_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "browser_dom",
                "description": (
                    "通过 Chrome/Edge DevTools Protocol 读取和操作当前网页 DOM。"
                    "只适用于已用 --remote-debugging-port 启动的 Chromium 浏览器。"
                    "网页任务优先用它读取页面、点击按钮、填写表单；失败时再退回 computer 截图操作。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "status",
                                "read_page",
                                "navigate",
                                "click_selector",
                                "type_selector",
                                "click_text",
                                "wait_text",
                                "wait_selector",
                                "get_selector",
                                "press_selector",
                                "evaluate",
                            ],
                            "description": "要执行的浏览器 DOM 动作。",
                        },
                        "target": {
                            "type": "string",
                            "description": "可选。用于选择标题或 URL 包含该关键词的浏览器标签页。",
                        },
                        "url": {
                            "type": "string",
                            "description": "当 action=navigate 时的目标网址。",
                        },
                        "selector": {
                            "type": "string",
                            "description": "CSS 选择器，用于 click_selector 或 type_selector。",
                        },
                        "text": {
                            "type": "string",
                            "description": "输入文本，或 click_text 要匹配的可见文字。",
                        },
                        "submit": {
                            "type": "boolean",
                            "description": "type_selector 后是否提交表单。",
                        },
                        "key": {
                            "type": "string",
                            "description": "press_selector 要发送的按键，默认 Enter。",
                        },
                        "timeout_seconds": {
                            "type": "number",
                            "description": "wait_text 或 wait_selector 的最长等待秒数，默认 8。",
                        },
                        "allow_hidden": {
                            "type": "boolean",
                            "description": "wait_selector 是否允许匹配隐藏元素，默认 false。",
                        },
                        "clear": {
                            "type": "boolean",
                            "description": "type_selector 前是否清空原内容，默认 true。",
                        },
                        "script": {
                            "type": "string",
                            "description": "当 action=evaluate 时执行的 JavaScript 表达式。",
                        },
                        "await_promise": {
                            "type": "boolean",
                            "description": "evaluate 时是否等待 Promise。",
                        },
                        "reason": {
                            "type": "string",
                            "description": "用一句简短中文说明为什么要执行这一步。",
                        },
                        "public_reasoning": {
                            "type": "string",
                            "description": "给用户看的公开说明，简短描述你读到了什么、准备做什么。",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    def build_user_message(self, text: str, snapshot: Snapshot) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": snapshot.data_url}},
            ],
        }

    def build_tool_result_messages(
        self,
        *,
        call_id: str,
        result_message: str,
        snapshot: Snapshot,
        followup_text: str,
    ) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": call_id, "content": result_message},
            self.build_user_message(followup_text, snapshot),
        ]

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
    ) -> AssistantReply:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        payload.update(self.config.extra_body)
        body = self._post_json(self._completion_url(self.config.base_url), payload, self._headers())

        if "error" in body:
            raise ProviderError(self._extract_error(body["error"]))
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(f"Malformed response, missing choices: {body}")

        first = choices[0]
        message = first.get("message") or {}
        text = self._stringify_content(message.get("content"))
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))
        if not tool_calls:
            fallback = self._tool_call_from_text(text)
            if fallback is not None:
                tool_calls = [fallback]
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": text,
        }
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in tool_calls
            ]
        return AssistantReply(
            text=text,
            tool_calls=tool_calls,
            finish_reason=first.get("finish_reason"),
            raw=body,
            assistant_message=assistant_message,
        )

    @staticmethod
    def _completion_url(base_url: str) -> str:
        cleaned = (base_url or "").strip().rstrip("/")
        if not cleaned:
            raise ProviderError("Base URL is empty.")
        if cleaned.endswith("/chat/completions"):
            return cleaned
        if cleaned.endswith("/v1"):
            return f"{cleaned}/chat/completions"
        return f"{cleaned}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        if "openrouter.ai" in self.config.base_url:
            headers.setdefault("HTTP-Referer", "https://local.windows.computer.use")
            headers.setdefault("X-Title", "Claude Computer Use Proxy")
        for key, value in default_browser_headers(self.config.base_url).items():
            headers.setdefault(key, value)
        headers.update(self.config.extra_headers)
        return headers

    @staticmethod
    def _stringify_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text") or ""))
            return "\n".join(chunk for chunk in chunks if chunk).strip()
        return str(content)

    @staticmethod
    def _parse_tool_calls(items: Any) -> list[ToolCall]:
        if not isinstance(items, list):
            return []
        parsed: list[ToolCall] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            function = item.get("function") or {}
            raw_arguments = function.get("arguments")
            arguments: dict[str, Any]
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                arguments = {}
            parsed.append(
                ToolCall(
                    call_id=str(item.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )
        return parsed

    def _tool_call_from_text(self, text: str) -> ToolCall | None:
        payload = self._extract_json_object(text)
        if not isinstance(payload, dict):
            return None
        if "action" in payload:
            return ToolCall(call_id="json_fallback", name="computer", arguments=payload)
        if payload.get("name") == "computer" and isinstance(payload.get("arguments"), dict):
            return ToolCall(call_id="json_fallback", name="computer", arguments=payload["arguments"])
        return None

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        candidates: list[str] = []
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)
        for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE):
            candidates.append(match.group(1))
        for candidate in candidates:
            try:
                loaded = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                return loaded
        return None

    @staticmethod
    def _extract_error(error: Any) -> str:
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or "Unknown provider error")
            code = error.get("code")
            if code:
                return f"{code}: {message}"
            return message
        return str(error)

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"HTTP {exc.code}: {raw}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Network error: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Provider returned non-JSON response: {raw[:500]}") from exc


class AnthropicOfficialProvider:
    kind = PROVIDER_ANTHROPIC_OFFICIAL

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        guessed_beta, guessed_tool = guess_anthropic_computer_contract(config.model)
        self.beta_header = config.anthropic_beta.strip() or guessed_beta
        self.tool_type = config.anthropic_tool_type.strip() or guessed_tool
        self.version = config.anthropic_version.strip() or ANTHROPIC_VERSION_DEFAULT

    def build_tools(self, capture_width: int, capture_height: int) -> list[dict[str, Any]]:
        return [
            {
                "type": self.tool_type,
                "name": "computer",
                "display_width_px": capture_width,
                "display_height_px": capture_height,
                "display_number": 1,
            }
        ]

    def build_user_message(self, text: str, snapshot: Snapshot) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": snapshot.image_base64,
                    },
                },
            ],
        }

    def build_tool_result_messages(
        self,
        *,
        call_id: str,
        result_message: str,
        snapshot: Snapshot,
        followup_text: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": [
                            {"type": "text", "text": result_message},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": snapshot.image_base64,
                                },
                            },
                        ],
                    },
                    {"type": "text", "text": followup_text},
                ],
            }
        ]

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
    ) -> AssistantReply:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": messages,
            "tools": tools,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if self.config.enable_thinking:
            budget = self._resolve_thinking_budget()
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
        payload.update(self.config.extra_body)
        body = OpenAICompatibleProvider._post_json(self._messages_url(self.config.base_url), payload, self._headers())
        return self._parse_response_body(body)

    def _resolve_thinking_budget(self) -> int:
        intensity = (self.config.thinking_intensity or "medium").lower()
        max_tokens = self.config.max_tokens
        if intensity == "high":
            return max(1024, int(max_tokens * 0.8))
        if intensity == "medium":
            return max(1024, int(max_tokens * 0.5))
        # Custom budget mode
        return max(1024, int(self.config.thinking_budget))

    @staticmethod
    def _messages_url(base_url: str) -> str:
        cleaned = (base_url or "").strip().rstrip("/")
        if not cleaned:
            raise ProviderError("Base URL is empty.")
        if cleaned.endswith("/messages"):
            return cleaned
        if cleaned.endswith("/v1"):
            return f"{cleaned}/messages"
        return f"{cleaned}/v1/messages"

    def _headers(self) -> dict[str, str]:
        beta_values = [value.strip() for value in self.beta_header.split(",") if value.strip()]
        if self.config.enable_thinking and INTERLEAVED_THINKING_BETA not in beta_values:
            beta_values.append(INTERLEAVED_THINKING_BETA)
        if self.config.enable_compact and COMPACT_2026_01_12_BETA not in beta_values:
            beta_values.append(COMPACT_2026_01_12_BETA)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": self.version,
            "anthropic-beta": ",".join(beta_values),
        }
        for key, value in default_browser_headers(self.config.base_url).items():
            headers.setdefault(key, value)
        headers.update(self.config.extra_headers)
        return headers

    def _parse_response_body(self, body: dict[str, Any]) -> AssistantReply:
        if "error" in body:
            raise ProviderError(OpenAICompatibleProvider._extract_error(body["error"]))
        content = body.get("content")
        if not isinstance(content, list):
            raise ProviderError(f"Malformed Anthropic response, missing content blocks: {body}")

        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        thinking_blocks = 0
        redacted_blocks = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                text_chunks.append(str(block.get("text") or ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        call_id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=dict(block.get("input") or {}),
                    )
                )
            elif block_type == "thinking":
                thinking_blocks += 1
            elif block_type == "redacted_thinking":
                redacted_blocks += 1

        reasoning_parts: list[str] = []
        if thinking_blocks:
            reasoning_parts.append(f"已启用官方 thinking，本轮收到 {thinking_blocks} 个 thinking 块。")
        if redacted_blocks:
            reasoning_parts.append(f"另有 {redacted_blocks} 个加密 thinking 块。")
        if reasoning_parts:
            reasoning_parts.append("为避免直接暴露原始思维链，界面只显示可见摘要，不直接显示 thinking 原文。")

        return AssistantReply(
            text="\n".join(chunk for chunk in text_chunks if chunk).strip(),
            tool_calls=tool_calls,
            finish_reason=str(body.get("stop_reason") or ""),
            raw=body,
            assistant_message={"role": "assistant", "content": content},
            reasoning_summary="\n".join(reasoning_parts),
        )


class SemiOfficialProvider(AnthropicOfficialProvider):
    """Fourth mode: Messages API request body + Bearer auth + optional beta headers.

    Designed for relay/proxy stations that expose a /messages endpoint and
    transparently forward to Anthropic API, but use Bearer token auth instead
    of x-api-key.

    Key differences from AnthropicOfficialProvider:
    - Authorization: Bearer <key>  (not x-api-key)
    - anthropic-version: optional, controlled by user's manual config
    - anthropic-beta: optional, no automatic injection
    - Response parsing: tries Anthropic format first, falls back to OpenAI
    """

    kind = PROVIDER_SEMI_OFFICIAL

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        version = self.config.anthropic_version.strip()
        if version:
            headers["anthropic-version"] = version
        beta = self.config.anthropic_beta.strip()
        if beta:
            beta_values = [b.strip() for b in beta.split(",") if b.strip()]
        else:
            beta_values = []
        if self.config.enable_thinking and INTERLEAVED_THINKING_BETA not in beta_values:
            beta_values.append(INTERLEAVED_THINKING_BETA)
        if self.config.enable_compact and COMPACT_2026_01_12_BETA not in beta_values:
            beta_values.append(COMPACT_2026_01_12_BETA)
        if beta_values:
            headers["anthropic-beta"] = ",".join(beta_values)
        for key, value in default_browser_headers(self.config.base_url).items():
            headers.setdefault(key, value)
        headers.update(self.config.extra_headers)
        return headers

    def _parse_response_body(self, body: dict[str, Any]) -> AssistantReply:
        """Try Anthropic format first, fall back to OpenAI-compatible."""
        content = body.get("content")
        if isinstance(content, list):
            return AnthropicOfficialProvider._parse_response_body(self, body)
        # Fallback: the relay wraps results in OpenAI-style 'choices'
        return self._parse_openai_style_body(body)

    @staticmethod
    def _parse_openai_style_body(body: dict[str, Any]) -> AssistantReply:
        if "error" in body:
            raise ProviderError(OpenAICompatibleProvider._extract_error(body["error"]))
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(f"Malformed response, missing choices: {body}")
        first = choices[0]
        message = first.get("message") or {}
        text = OpenAICompatibleProvider._stringify_content(message.get("content"))
        tool_calls = OpenAICompatibleProvider._parse_tool_calls(message.get("tool_calls"))
        assistant_message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in tool_calls
            ]
        return AssistantReply(
            text=text,
            tool_calls=tool_calls,
            finish_reason=first.get("finish_reason"),
            raw=body,
            assistant_message=assistant_message,
        )
