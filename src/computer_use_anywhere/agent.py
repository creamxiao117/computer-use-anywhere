from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .browser_dom import BrowserDomController
from .models import (
    AgentEvent,
    AGENT_STATE_ADVISOR_IDLE,
    AGENT_STATE_MAIN_IDLE,
    PROVIDER_OFFICIAL_COMPATIBLE,
    PROVIDER_SEMI_OFFICIAL,
    ProviderConfig,
    SessionConfig,
)
from .provider import (
    AnthropicOfficialProvider,
    OpenAICompatibleProvider,
    SemiOfficialProvider,
    create_provider,
    guess_anthropic_computer_contract,
)
from .replay import ActionVerification, SessionReplay, verify_action_result
from .skills import SkillRegistry, built_in_skills
from .windows_control import WindowsDesktopController


EventCallback = Callable[[AgentEvent], None]
ConfirmCallback = Callable[[str], bool]

AGENT_WINDOW_TITLE = "Computer Use Anywhere"


def build_system_prompt(
    actual_width: int,
    actual_height: int,
    capture_width: int,
    capture_height: int,
    *,
    official_mode: bool = False,
    official_enhanced: bool = True,
    official_compatible_mode: bool = False,
) -> str:
    official_compatible_only_line = (
        "- 如果使用的是官方体验兼容模式，不要请求 browser_dom、网页 DOM 或其它非 computer 工具；只能通过 computer 工具逐步操作。\n"
        if official_compatible_mode
        else ""
    )
    if official_compatible_mode:
        protocol_line = (
            "你当前使用的是“官方体验兼容模式”：底层是 OpenAI-compatible function calling，"
            "但必须按 Anthropic computer use 的观察-动作-再观察循环工作。"
        )
        window_control_line = "- 需要切换窗口时，必须先基于最新截图确认目标窗口可见，再执行一个安全的小动作；不要假装看见被遮挡内容。\n"
        browser_dom_line = ""
    elif official_mode:
        protocol_line = (
            "你当前使用的是 Anthropic 官方 computer use 协议。"
            "当前为“官方增强原生”：仍只使用官方 computer 工具，但本地执行器会提供安全校验和桌面状态。"
            if official_enhanced
            else "你当前使用的是 Anthropic 官方 computer use 协议。当前为“官方纯原生”：只使用官方 computer 工具和截图回传，不使用自定义工具。"
        )
        window_control_line = (
            "- 需要切换窗口时，必须先基于最新截图确认目标窗口可见，再使用官方协议支持的安全动作切换；不要假装看见被遮挡的窗口内容。\n"
            if official_enhanced
            else "- 严格遵循官方 computer use 工作流：观察截图、执行一个官方 computer 动作、再观察新截图。\n"
        )
        browser_dom_line = ""
    else:
        protocol_line = "你当前使用的是通用 function calling 兼容协议。"
        window_control_line = (
            "- 如果目标窗口可见但不在前台，优先调用 activate_window，并用 window_title 填写窗口标题关键词；不要靠猜测去点击标题栏。\n"
            "- 执行 type 或 enter/tab/backspace/delete/space 这类输入动作前，尽量填写 expected_window_title，工具会拒绝把输入送进错误窗口。\n"
        )
        browser_dom_line = "- 如果任务发生在网页里，优先使用 browser_dom 读取 DOM、导航、点击选择器或填写表单；如果 browser_dom 不可用，再退回截图操作。\n"
    own_window_line = (
        f"- 如果截图里出现名为“{AGENT_WINDOW_TITLE}”的控制窗口、预览、日志或配置面板，必须忽略，绝不能把它当成任务目标。\n"
        if (official_compatible_mode or not official_mode or official_enhanced)
        else ""
    )
    masked_line = (
        "- 如果截图里有被遮挡或涂灰的矩形区域，那表示该区域内容当前不可见，不能假设后面有什么页面、按钮或输入框。\n"
        if (official_compatible_mode or not official_mode or official_enhanced)
        else ""
    )
    return (
        "你正在控制一台实时 Windows 桌面。\n\n"
        f"{protocol_line}\n"
        f"真实屏幕尺寸：{actual_width}x{actual_height}\n"
        f"当前截图尺寸：{capture_width}x{capture_height}\n"
        "所有坐标都必须使用截图尺寸，不要使用真实屏幕像素。\n\n"
        "规则：\n"
        "- 只与当前可见的图形界面交互。\n"
        "- 每次只做一个小动作。\n"
        "- 每次动作执行后，都会重新提供最新截图。\n"
        "- 优先选择安全、可撤销的动作。\n"
        "- 如果快捷键明显且可靠，优先使用快捷键。\n"
        "- 如果当前画面不确定，优先重新截图，不要盲猜坐标。\n"
        f"{own_window_line}"
        f"{masked_line}"
        "- 优先操作用户真正想控制的目标应用，而不是代理工具自己的界面。\n"
        f"{window_control_line}"
        f"{browser_dom_line}"
        "- 如果某一步没有达到预期，下一步必须根据最新截图和当前前台窗口标题修正，不要沿用旧假设。\n"
        "- 点击、输入或等待之后，必须观察工具返回的新截图再判断是否成功；如果截图没有证明任务完成，不能声称完成。\n"
        f"{official_compatible_only_line}"
        "- 你的自然语言说明和最终答复都必须使用简体中文。\n"
        "- 在调用工具前，尽量先用一到两句简短中文公开说明你看到了什么、准备做什么。\n"
        "- 如果当前协议或模型不方便先输出文本，至少要在工具参数里填写 public_reasoning，供界面展示公开思考。\n"
    )


def normalize_computer_action(arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action") or "").strip().lower()
    aliases = {
        "click": "left_click",
        "move": "mouse_move",
        "hover": "mouse_move",
        "drag": "left_click_drag",
        "press_key": "key",
        "type_text": "type",
        "focus_window": "activate_window",
        "switch_window": "activate_window",
        "bring_window_to_front": "activate_window",
    }
    normalized = dict(arguments)
    normalized["action"] = aliases.get(action, action)
    for field in ("coordinate", "start_coordinate", "end_coordinate"):
        if field in normalized:
            normalized[field] = _normalize_coordinate_value(normalized.get(field))
    return normalized


def _normalize_coordinate_value(value: Any) -> Any:
    if isinstance(value, dict):
        x = value.get("x", value.get("X"))
        y = value.get("y", value.get("Y"))
        if x is not None and y is not None:
            return [_coerce_coordinate_number(x), _coerce_coordinate_number(y)]
        return value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [_coerce_coordinate_number(value[0]), _coerce_coordinate_number(value[1])]
    if isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None and parsed is not value:
            return _normalize_coordinate_value(parsed)
        numbers = re.findall(r"-?\d+(?:\.\d+)?", stripped)
        if len(numbers) >= 2:
            return [_coerce_coordinate_number(numbers[0]), _coerce_coordinate_number(numbers[1])]
    return value


def _coerce_coordinate_number(value: Any) -> int:
    return int(round(float(value)))


WEB_TASK_PATTERNS = (
    r"https?://",
    r"\bwww\.",
    r"\.(com|cn|net|org|io|ai|cc|top)\b",
    "网页",
    "网站",
    "浏览器",
    "网址",
    "页面",
    "搜索",
    "查找",
    "登录",
    "注册",
    "表单",
    "填写",
    "提交",
    "b站",
    "bilibili",
    "哔哩",
    "百度",
    "google",
    "github",
    "openrouter",
)

DOM_FIRST_COMPUTER_ACTIONS = {
    "mouse_move",
    "left_click",
    "double_click",
    "right_click",
    "middle_click",
    "left_click_drag",
    "type",
    "key",
    "scroll",
}


def task_prefers_browser_dom(task: str) -> bool:
    normalized = (task or "").strip().casefold()
    if not normalized:
        return False
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in WEB_TASK_PATTERNS)


def should_apply_browser_dom_first_guard(
    *,
    task: str,
    official_mode: bool,
    browser_dom_enabled: bool,
    browser_dom_first: bool,
    browser_dom_attempted: bool,
    guard_used: bool,
    tool_name: str,
    arguments: dict[str, Any],
) -> bool:
    if official_mode or not browser_dom_enabled or not browser_dom_first:
        return False
    if browser_dom_attempted or guard_used:
        return False
    if tool_name != "computer":
        return False
    action = str(arguments.get("action") or "").strip().lower()
    return action in DOM_FIRST_COMPUTER_ACTIONS and task_prefers_browser_dom(task)


class ComputerUseAgent:
    def __init__(
        self,
        provider_config: ProviderConfig,
        session_config: SessionConfig,
        *,
        event_callback: EventCallback | None = None,
        stop_event: threading.Event | None = None,
        confirm_callback: ConfirmCallback | None = None,
    ) -> None:
        self.provider_config = provider_config
        self.provider = create_provider(provider_config)
        self.advisor_provider = self._create_advisor_provider(provider_config)
        self.advisor_model_name = (
            (provider_config.advisor_model or "claude-opus-4-7")
            if provider_config.advisor_enabled
            else ""
        )
        if (
            isinstance(self.provider, AnthropicOfficialProvider)
            and not session_config.official_enhanced
        ):
            session_config.mask_own_window = False
        self.desktop = WindowsDesktopController(session_config)
        self.browser_dom = (
            BrowserDomController(session_config) if session_config.browser_dom_enabled else None
        )
        self.session_config = session_config
        self.event_callback = event_callback or (lambda event: None)
        self.stop_event = stop_event or threading.Event()
        self.confirm_callback = confirm_callback
        self.replay: SessionReplay | None = None
        self.skill_registry = built_in_skills()

    def _create_advisor_provider(self, main_config: ProviderConfig):
        if not main_config.advisor_enabled:
            return None
        advisor_config = ProviderConfig(
            provider_kind=main_config.provider_kind,
            base_url=main_config.advisor_base_url or main_config.base_url,
            api_key=main_config.advisor_api_key or main_config.api_key,
            model=main_config.advisor_model or "claude-opus-4-7",
            max_tokens=main_config.advisor_max_tokens,
            temperature=main_config.advisor_temperature,
            extra_headers=dict(main_config.extra_headers),
            extra_body=dict(main_config.extra_body),
            anthropic_version=main_config.anthropic_version,
            anthropic_beta=main_config.anthropic_beta,
            anthropic_tool_type=main_config.anthropic_tool_type,
            enable_thinking=True,
            thinking_intensity="high",
            enable_compact=True,
        )
        return create_provider(advisor_config)

    def run(self, task: str) -> dict[str, Any]:
        # Auto-detect model family hint if set to auto
        if self.session_config.model_family_hint == "auto":
            model = self.provider_config.model.lower()
            if "opus-4.7" in model or "opus 4.7" in model or "opus4.7" in model:
                self.session_config.model_family_hint = "opus_4_7"
            elif "4.6" in model or "4-6" in model or "haiku-4.5" in model:
                self.session_config.model_family_hint = "claude_4_6"
            else:
                self.session_config.model_family_hint = "claude_4_6"
        initial = self.desktop.capture_snapshot("initial")
        self.replay = SessionReplay(self.desktop.session_root)
        tools = self.provider.build_tools(initial.width, initial.height)
        # Append built-in skill tool schemas
        tools.extend(self.skill_registry.get_tool_schemas())
        official_mode = isinstance(self.provider, (AnthropicOfficialProvider, SemiOfficialProvider))
        official_compatible_mode = (
            self.provider_config.provider_kind == PROVIDER_OFFICIAL_COMPATIBLE
        )
        semi_official_mode = self.provider_config.provider_kind == PROVIDER_SEMI_OFFICIAL
        enhanced_guards = (not official_mode) or self.session_config.official_enhanced
        if official_mode or official_compatible_mode or not self.session_config.browser_dom_enabled:
            tools = [
                tool for tool in tools if tool.get("function", {}).get("name") != "browser_dom"
            ]
        system_prompt = build_system_prompt(
            initial.actual_width,
            initial.actual_height,
            initial.width,
            initial.height,
            official_mode=official_mode,
            official_enhanced=self.session_config.official_enhanced,
            official_compatible_mode=official_compatible_mode,
        )
        if official_compatible_mode:
            initial_prompt = (
                f"用户任务：{task}\n"
                f"注意：如果截图中出现“{AGENT_WINDOW_TITLE}”窗口，那只是代理工具自己的界面，不是任务目标，禁止操作它。\n"
                f"{self._desktop_state_text(initial)}\n"
                "已附上当前桌面截图。请模拟 Anthropic 官方 computer use 的工作流："
                "先观察截图，再只选择一个 computer 动作，再等待下一张截图。"
                "不要调用 browser_dom 或其它非 computer 工具。"
            )
        elif official_mode and not self.session_config.official_enhanced:
            initial_prompt = (
                f"用户任务：{task}\n"
                "已附上当前桌面截图。请严格按 Anthropic 官方 computer use 流程，只选择一个下一步 computer 动作，并使用简体中文。"
            )
        else:
            initial_prompt = (
                f"用户任务：{task}\n"
                f"注意：如果截图中出现“{AGENT_WINDOW_TITLE}”窗口，那只是代理工具自己的界面，不是任务目标，禁止操作它。\n"
                f"{self._desktop_state_text(initial)}\n"
                f"{self._browser_dom_state_text(official_mode, official_compatible_mode=official_compatible_mode)}\n"
                "已附上当前桌面截图。请为下一步选择且只选择一个最合适的工具动作，并使用简体中文。"
                "如果需要输入文字或按回车，请先确认真正的目标窗口在前台；兼容模式下可先用 activate_window。"
                "如果任务是网页导航、搜索、点击网页按钮或填写网页表单，兼容模式下第一步优先使用 browser_dom；"
                "可先调用 browser_dom status 检查是否可用，然后用 navigate/read_page/click_selector/type_selector。"
            )
        messages: list[dict[str, Any]]
        if isinstance(self.provider, OpenAICompatibleProvider):
            messages = [
                {"role": "system", "content": system_prompt},
                self.provider.build_user_message(initial_prompt, initial),
            ]
        else:
            messages = [self.provider.build_user_message(initial_prompt, initial)]

        self._emit(
            "snapshot",
            "已捕获初始截图。",
            snapshot_path=initial.path,
            payload={"provider_kind": self.provider.kind},
        )
        self.replay.record_initial(task=task, provider_kind=self.provider.kind, snapshot=initial)

        final_text = ""
        latest_snapshot = initial
        browser_dom_attempted = False
        browser_dom_guard_used = False
        use_advisor_next = False
        advisor_step_count = 0
        for step in range(1, self.session_config.max_steps + 1):
            if self.stop_event.is_set():
                final_text = "已按用户要求停止。"
                self._emit("warning", final_text)
                break

            current_provider = (
                self.advisor_provider
                if use_advisor_next and self.advisor_provider
                else self.provider
            )
            provider_name = (
                f"顾问({self.advisor_model_name})"
                if use_advisor_next and self.advisor_provider
                else "主模型"
            )
            # 可视化反馈：根据当前是否走顾问通道选 agent_state；status 阶段处于等待模型返回，归为 *_IDLE。
            status_agent_state = (
                AGENT_STATE_ADVISOR_IDLE
                if (use_advisor_next and self.advisor_provider)
                else AGENT_STATE_MAIN_IDLE
            )
            self._emit(
                "status",
                f"第 {step}/{self.session_config.max_steps} 步：正在请求{provider_name}下一步动作。",
                payload={
                    "agent_state": status_agent_state,
                    "step_n": step,
                    "step_total": self.session_config.max_steps,
                },
            )
            request_started_at = time.perf_counter()
            reply = current_provider.complete(
                messages, tools, system_prompt=system_prompt if official_mode else None
            )
            thought_seconds = max(0.0, time.perf_counter() - request_started_at)
            if reply.assistant_message:
                messages.append(reply.assistant_message)
            if use_advisor_next and self.advisor_provider:
                self._emit(
                    "analysis",
                    f"顾问模型({self.advisor_model_name}) 介入处理上一步失败。思考 {thought_seconds:.1f} 秒。",
                )
                use_advisor_next = False
                advisor_step_count += 1

            if reply.text:
                self._emit(
                    "assistant",
                    reply.text,
                    payload={
                        "step": step,
                        "thought_seconds": thought_seconds,
                        "derived": False,
                    },
                )
            if not reply.tool_calls:
                final_text = reply.text or "模型已停止，未继续返回工具调用。"
                break
            if self.stop_event.is_set():
                final_text = "已按用户要求停止。"
                self._emit("warning", final_text)
                break

            call = reply.tool_calls[0]
            if len(reply.tool_calls) > 1:
                self._emit("warning", "模型一次返回了多个工具调用，本轮只执行第一个。")
            if call.name == "browser_dom":
                browser_dom_attempted = True
                before_snapshot = latest_snapshot
                snapshot, result_message = self._handle_browser_dom_call(call, latest_snapshot)
                self._record_replay_step(
                    step=step,
                    tool_name="browser_dom",
                    arguments=call.arguments,
                    result_message=result_message,
                    before_snapshot=before_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=self._public_reasoning(
                        reply.text, reply.reasoning_summary, call.arguments
                    ),
                )
                latest_snapshot = snapshot
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=result_message,
                        snapshot=snapshot,
                        followup_text=f"{self._desktop_state_text(snapshot)}\n已附上最新截图。请继续下一步；如果网页任务完成，请用简体中文回答。",
                    )
                )
                continue
            if call.name not in {"computer"} and self.skill_registry._skills.get(call.name):
                before_snapshot = latest_snapshot
                result = self.skill_registry.execute(call.name, call.arguments)
                result_text = (
                    json.dumps(result, ensure_ascii=False)
                    if isinstance(result, dict)
                    else str(result)
                )
                snapshot = self.desktop.capture_snapshot(f"skill_{call.name}")
                latest_snapshot = snapshot
                self._emit("tool", f"Skill {call.name} 结果：{result_text[:200]}")
                self._record_replay_step(
                    step=step,
                    tool_name=call.name,
                    arguments=call.arguments,
                    result_message=result_text,
                    before_snapshot=before_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=self._public_reasoning(
                        reply.text, reply.reasoning_summary, call.arguments
                    ),
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=result_text,
                        snapshot=snapshot,
                        followup_text=f"Skill {call.name} 执行结果：{result_text}\n{self._desktop_state_text(snapshot)}",
                    )
                )
                continue
            if call.name != "computer":
                raise RuntimeError(f"模型返回了不支持的工具名：{call.name}")

            arguments = normalize_computer_action(call.arguments)
            before_decision_snapshot = latest_snapshot
            public_reasoning = self._public_reasoning(
                reply.text, reply.reasoning_summary, arguments
            )
            if should_apply_browser_dom_first_guard(
                task=task,
                official_mode=official_mode,
                browser_dom_enabled=self.session_config.browser_dom_enabled,
                browser_dom_first=self.session_config.browser_dom_first,
                browser_dom_attempted=browser_dom_attempted,
                guard_used=browser_dom_guard_used,
                tool_name=call.name,
                arguments=arguments,
            ):
                browser_dom_guard_used = True
                snapshot = self.desktop.capture_snapshot("dom_first_guard")
                latest_snapshot = snapshot
                tool_text = (
                    "这个任务看起来是网页任务，且浏览器 DOM 工具已启用。"
                    "本轮先不执行截图点击/输入，以免盲点。请先调用 browser_dom status；"
                    "如果能连接，再使用 navigate、read_page、click_selector 或 type_selector。"
                    "如果 browser_dom 返回不可用，下一步可以回退到 computer 截图操作。"
                )
                self._emit("warning", tool_text, snapshot_path=snapshot.path)
                self._record_replay_step(
                    step=step,
                    tool_name="computer",
                    arguments=arguments,
                    result_message=tool_text,
                    before_snapshot=before_decision_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=public_reasoning,
                    verification=ActionVerification(
                        "warn", "网页任务 DOM 优先守卫拦截了第一次截图点击/输入。"
                    ),
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=tool_text,
                        snapshot=snapshot,
                        followup_text=f"{tool_text}\n{self._desktop_state_text(snapshot)}",
                    )
                )
                continue
            if public_reasoning and not reply.text:
                self._emit(
                    "assistant",
                    public_reasoning,
                    payload={
                        "step": step,
                        "thought_seconds": thought_seconds,
                        "derived": True,
                    },
                )
            if not self.desktop.is_supported_action(arguments):
                snapshot = self.desktop.capture_snapshot("unsupported_action")
                latest_snapshot = snapshot
                tool_text = self.desktop.unsupported_action_message(arguments)
                self._emit("warning", tool_text, snapshot_path=snapshot.path)
                self._record_replay_step(
                    step=step,
                    tool_name="computer",
                    arguments=arguments,
                    result_message=tool_text,
                    before_snapshot=before_decision_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=public_reasoning,
                    verification=ActionVerification("warn", "模型返回了当前不支持的动作。"),
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=tool_text,
                        snapshot=snapshot,
                        followup_text=tool_text,
                    )
                )
                use_advisor_next = True
                continue
            action_summary = self.desktop.describe(arguments)
            self._emit(
                "analysis",
                self._decision_summary(
                    visible_text=reply.text,
                    official_reasoning=reply.reasoning_summary,
                    public_reasoning=public_reasoning,
                    action_summary=action_summary,
                    arguments=arguments,
                ),
                payload={"action": arguments.get("action"), "arguments": arguments},
            )

            if enhanced_guards and self.desktop.has_out_of_bounds_coordinate(arguments):
                snapshot = self.desktop.capture_snapshot("coordinate_bounds")
                latest_snapshot = snapshot
                tool_text = self.desktop.coordinate_bounds_message(arguments)
                self._emit("warning", tool_text, snapshot_path=snapshot.path)
                self._record_replay_step(
                    step=step,
                    tool_name="computer",
                    arguments=arguments,
                    result_message=tool_text,
                    before_snapshot=before_decision_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=public_reasoning,
                    verification=ActionVerification(
                        "warn", "坐标超出当前截图范围，已被安全阀拦截。"
                    ),
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=tool_text,
                        snapshot=snapshot,
                        followup_text=tool_text,
                    )
                )
                use_advisor_next = True
                continue

            if enhanced_guards and self.desktop.is_action_targeting_masked_region(arguments):
                snapshot = self.desktop.capture_snapshot("masked_region")
                latest_snapshot = snapshot
                tool_text = self.desktop.masked_region_message(arguments)
                self._emit("warning", tool_text, snapshot_path=snapshot.path)
                self._record_replay_step(
                    step=step,
                    tool_name="computer",
                    arguments=arguments,
                    result_message=tool_text,
                    before_snapshot=before_decision_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=public_reasoning,
                    verification=ActionVerification(
                        "warn", "目标坐标位于代理窗口遮挡区域，已被安全阀拦截。"
                    ),
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=tool_text,
                        snapshot=snapshot,
                        followup_text=tool_text,
                    )
                )
                use_advisor_next = True
                continue

            pre_execution_notes: list[str] = []
            auto_focus_message = (
                self.desktop.ensure_expected_window_foreground(arguments) if enhanced_guards else ""
            )
            if auto_focus_message:
                note = f"自动前台校正：{auto_focus_message}"
                pre_execution_notes.append(note)
                self._emit("tool", note)

            if enhanced_guards and not self.desktop.is_foreground_safe_for_action(arguments):
                snapshot = self.desktop.capture_snapshot("foreground_guard")
                latest_snapshot = snapshot
                tool_text = self.desktop.foreground_guard_message(arguments)
                self._emit("warning", tool_text, snapshot_path=snapshot.path)
                self._record_replay_step(
                    step=step,
                    tool_name="computer",
                    arguments=arguments,
                    result_message=tool_text,
                    before_snapshot=before_decision_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=public_reasoning,
                    verification=ActionVerification(
                        "warn", "输入类动作的前台窗口不安全，已被安全阀拦截。"
                    ),
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=tool_text,
                        snapshot=snapshot,
                        followup_text=tool_text,
                    )
                )
                use_advisor_next = True
                continue

            if self.session_config.confirm_actions and arguments.get("action") not in {
                "screenshot",
                "wait",
                "activate_window",
            }:
                approved = self._confirm(action_summary)
                if not approved:
                    snapshot = self.desktop.capture_snapshot("rejected")
                    latest_snapshot = snapshot
                    tool_text = (
                        f"用户拒绝了这次操作：{action_summary}。请基于最新截图重新判断下一步。"
                    )
                    self._emit("warning", tool_text, snapshot_path=snapshot.path)
                    self._record_replay_step(
                        step=step,
                        tool_name="computer",
                        arguments=arguments,
                        result_message=tool_text,
                        before_snapshot=before_decision_snapshot,
                        after_snapshot=snapshot,
                        model_text=reply.text,
                        public_reasoning=public_reasoning,
                        verification=ActionVerification("warn", "用户拒绝了本次动作。"),
                    )
                    messages.extend(
                        self.provider.build_tool_result_messages(
                            call_id=call.call_id,
                            result_message=tool_text,
                            snapshot=snapshot,
                            followup_text=tool_text,
                        )
                    )
                    continue

            self._emit("tool", f"正在执行：{action_summary}")
            before_snapshot = latest_snapshot
            try:
                result = self.desktop.execute(arguments)
            except Exception as exc:
                snapshot = self.desktop.capture_snapshot("execution_error")
                latest_snapshot = snapshot
                tool_text = (
                    f"执行动作时出错：{type(exc).__name__}: {exc}。"
                    "本轮没有完成该动作。已附上最新截图，请修正参数后重新选择下一步。"
                )
                self._emit("warning", tool_text, snapshot_path=snapshot.path)
                self._record_replay_step(
                    step=step,
                    tool_name="computer",
                    arguments=arguments,
                    result_message=tool_text,
                    before_snapshot=before_snapshot,
                    after_snapshot=snapshot,
                    model_text=reply.text,
                    public_reasoning=public_reasoning,
                    verification=ActionVerification("warn", f"执行器异常：{type(exc).__name__}。"),
                )
                error_followup = (
                    f"{tool_text}\n已附上最新截图。请严格按官方 computer use 流程修正下一步。"
                    if official_mode and not self.session_config.official_enhanced
                    else f"{tool_text}\n{self._desktop_state_text(snapshot)}"
                )
                messages.extend(
                    self.provider.build_tool_result_messages(
                        call_id=call.call_id,
                        result_message=tool_text,
                        snapshot=snapshot,
                        followup_text=error_followup,
                    )
                )
                use_advisor_next = True
                continue
            # 可视化反馈：execute 成功后补发一个带 action_kind 的 tool 事件，
            # 让 overlay 能拿到物理坐标和动作类型来画波纹/切状态。
            # 原 emit 不动；这里是额外的一条，payload 字段优先使用 ActionResult 的元数据。
            if result.action_kind:
                self._emit(
                    "tool",
                    f"已执行：{action_summary}",
                    payload={
                        "action_kind": result.action_kind,
                        "physical_coord": result.physical_coord,
                        "action_extra": result.action_extra,
                    },
                )
            change_text = self.desktop.describe_snapshot_change(latest_snapshot, result.snapshot)
            result_message = "\n".join([*pre_execution_notes, result.message, change_text])
            latest_snapshot = result.snapshot
            self._emit("snapshot", result_message, snapshot_path=result.snapshot.path)
            verification = self._record_replay_step(
                step=step,
                tool_name="computer",
                arguments=arguments,
                result_message=result_message,
                before_snapshot=before_snapshot,
                after_snapshot=result.snapshot,
                model_text=reply.text,
                public_reasoning=public_reasoning,
            )
            if verification.status == "warn":
                self._emit("warning", f"动作验证：{verification.message}")
                if "几乎没有变化" in verification.message or "变化很小" in verification.message:
                    use_advisor_next = True
            # RegionFocus 钩子: 点击类动作 + 画面几乎无变化 + 有合法坐标 时,
            # 在 result.snapshot 上画红框 + 右下角拼放大区域,让下一轮 (顾问) 模型
            # 看到"我应该重新看哪儿"。其他场景沿用普通 snapshot,不增加开销。
            click_xy_for_focus = (
                self._extract_click_coordinate(arguments)
                if verification.status == "warn" and use_advisor_next
                else None
            )
            action_for_focus = str(arguments.get("action") or "").strip().lower()
            region_focus_enabled = (
                click_xy_for_focus is not None
                and action_for_focus
                in {"left_click", "click", "double_click", "right_click", "middle_click"}
                and ("几乎没有变化" in verification.message or "变化很小" in verification.message)
            )
            outbound_snapshot = result.snapshot
            region_focus_hint = ""
            if region_focus_enabled:
                try:
                    outbound_snapshot = self.desktop.make_region_focus_snapshot(
                        result.snapshot, click_xy_for_focus
                    )
                    region_focus_hint = (
                        f"\n[RegionFocus 提示] 上次点击 ({click_xy_for_focus[0]},{click_xy_for_focus[1]}) 后画面几乎无变化。"
                        f"附图右下角是该点周围 ±{96}px 的放大区域,红框/十字标出了上次点击位置。"
                        f"**请放大看清楚:那个位置真的有可点击元素吗?** 如果没有,请在放大区域内重新挑选正确的目标像素坐标 (注意:坐标仍按全局截图尺寸给)。"
                        f"如果该位置确有目标但 UI 反应慢,改用 wait()。"
                    )
                    self._emit(
                        "analysis", "已启用 RegionFocus:在截图右下角拼了点击点附近的放大区域。"
                    )
                except Exception:
                    outbound_snapshot = result.snapshot
                    region_focus_hint = ""
            if official_mode and not self.session_config.official_enhanced:
                followup_text = (
                    f"工具执行结果：{result.message}{region_focus_hint}\n"
                    "已附上最新截图。请严格按官方 computer use 流程继续；如果任务已经完成，请直接用简体中文回答。"
                )
                tool_result_message = result.message
            else:
                followup_text = (
                    f"工具执行结果：{result_message}{region_focus_hint}\n"
                    f"{self._desktop_state_text(result.snapshot)}\n"
                    f"再次提醒：如果截图里还有“{AGENT_WINDOW_TITLE}”窗口或它的预览区域，必须忽略，不能把它当任务目标。\n"
                    "已附上最新截图。请继续只选择一个下一步动作；如果上一步没有达到预期，必须根据这张最新截图修正。"
                    "如果任务已经完成，请直接用简体中文回答。"
                )
                tool_result_message = result_message
            messages.extend(
                self.provider.build_tool_result_messages(
                    call_id=call.call_id,
                    result_message=tool_result_message,
                    snapshot=outbound_snapshot,
                    followup_text=followup_text,
                )
            )
        else:
            final_text = "已达到最大步数限制，运行停止。"
            self._emit("warning", final_text)

        replay_path = ""
        if self.replay is not None:
            replay_path = str(self.replay.write_html(final_text))
        self._emit(
            "finished",
            final_text,
            payload={
                "session_root": str(self.desktop.session_root),
                "replay_path": replay_path,
                "final_text": final_text,
                "provider_kind": self.provider.kind,
            },
        )
        return {
            "final_text": final_text,
            "session_root": str(self.desktop.session_root),
            "replay_path": replay_path,
        }

    def _confirm(self, summary: str) -> bool:
        if self.confirm_callback is None:
            return True
        return bool(self.confirm_callback(summary))

    def _handle_browser_dom_call(self, call, latest_snapshot) -> tuple[Any, str]:
        arguments = dict(call.arguments)
        public_reasoning = self._public_reasoning("", "", arguments)
        if public_reasoning:
            self._emit("assistant", public_reasoning, payload={"derived": True})
        if self.browser_dom is None:
            message = "browser_dom 未启用。请改用 computer 截图工具，或在设置中启用浏览器 DOM。"
        else:
            action = str(arguments.get("action") or "").strip()
            self._emit("tool", f"正在执行 DOM 动作：{action}")
            try:
                result = self.browser_dom.execute(arguments)
                message = result.message
            except Exception as exc:
                message = (
                    f"browser_dom 执行失败：{type(exc).__name__}: {exc}\n"
                    "如果需要使用 DOM，请确认 Edge/Chrome 已用 --remote-debugging-port=9222 启动；否则改用 computer 截图工具。"
                )
        snapshot = self.desktop.capture_snapshot("browser_dom")
        change_text = self.desktop.describe_snapshot_change(latest_snapshot, snapshot)
        full_message = f"{message}\n{change_text}"
        self._emit("snapshot", full_message, snapshot_path=snapshot.path)
        return snapshot, full_message

    def _record_replay_step(
        self,
        *,
        step: int,
        tool_name: str,
        arguments: dict[str, Any],
        result_message: str,
        before_snapshot,
        after_snapshot,
        model_text: str = "",
        public_reasoning: str = "",
        verification: ActionVerification | None = None,
    ) -> ActionVerification:
        verification = verification or verify_action_result(
            tool_name=tool_name,
            arguments=arguments,
            result_message=result_message,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            own_window_title=self.session_config.own_window_title,
        )
        if self.replay is not None:
            self.replay.record_step(
                step=step,
                tool_name=tool_name,
                arguments=arguments,
                result_message=result_message,
                verification=verification,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                model_text=model_text,
                public_reasoning=public_reasoning,
            )
        return verification

    @staticmethod
    def _desktop_state_text(snapshot) -> str:
        foreground = snapshot.foreground_window_title or "未知"
        titles = [title for title in snapshot.visible_window_titles if title]
        if not titles:
            return f"当前前台窗口标题：{foreground}\n当前可见窗口标题：未获取到。"
        compact_titles = "；".join(titles[:12])
        return f"当前前台窗口标题：{foreground}\n当前可见窗口标题：{compact_titles}"

    def _browser_dom_state_text(
        self, official_mode: bool, *, official_compatible_mode: bool = False
    ) -> str:
        if official_mode or official_compatible_mode:
            return "浏览器 DOM 工具：官方 Anthropic 模式不启用，保持官方 computer 协议。"
        if self.browser_dom is None:
            return "浏览器 DOM 工具：未启用。"
        return (
            "浏览器 DOM 工具：已启用。"
            f"调试端口 {self.session_config.browser_debug_host}:{self.session_config.browser_debug_port}。"
            "网页任务建议先调用 browser_dom status 或 read_page。"
        )

    @staticmethod
    def _decision_summary(
        *,
        visible_text: str,
        official_reasoning: str,
        public_reasoning: str,
        action_summary: str,
        arguments: dict[str, Any],
    ) -> str:
        parts: list[str] = []
        visible = (visible_text or "").strip()
        if visible:
            parts.append(f"模型说明：{visible}")
        elif public_reasoning:
            parts.append(f"公开思考：{public_reasoning}")
        if official_reasoning:
            parts.append(f"官方 thinking 元信息：{official_reasoning}")
        if public_reasoning and public_reasoning != visible:
            parts.append(f"公开思考：{public_reasoning}")
        reason = str(arguments.get("reason") or "").strip()
        if reason:
            parts.append(f"动作理由：{reason}")
        parts.append(f"准备执行：{action_summary}")
        safe_arguments = {
            key: value
            for key, value in arguments.items()
            if key not in {"reason", "public_reasoning"}
        }
        parts.append(f"动作参数：{json.dumps(safe_arguments, ensure_ascii=False)}")
        return "\n".join(parts)

    @staticmethod
    def _public_reasoning(
        visible_text: str, official_reasoning: str, arguments: dict[str, Any]
    ) -> str:
        visible = (visible_text or "").strip()
        if visible:
            return visible
        public_reasoning = str(arguments.get("public_reasoning") or "").strip()
        if public_reasoning:
            return public_reasoning
        reason = str(arguments.get("reason") or "").strip()
        if reason:
            return reason
        if official_reasoning:
            return official_reasoning
        return ""

    @staticmethod
    def _extract_click_coordinate(arguments: dict[str, Any]) -> tuple[int, int] | None:
        """从 computer 动作参数里抽出 (x,y),供 RegionFocus / 微验证使用。

        优先 coordinate;其次 start_coordinate (drag 起点);最后 end_coordinate。
        返回 None 表示参数里没合法坐标。
        """
        for key in ("coordinate", "start_coordinate", "end_coordinate"):
            raw = arguments.get(key)
            if isinstance(raw, (list, tuple)) and len(raw) == 2:
                try:
                    return (int(raw[0]), int(raw[1]))
                except (TypeError, ValueError):
                    continue
            if isinstance(raw, dict):
                x = raw.get("x", raw.get("X"))
                y = raw.get("y", raw.get("Y"))
                if x is not None and y is not None:
                    try:
                        return (int(x), int(y))
                    except (TypeError, ValueError):
                        continue
        return None

    def _emit(
        self,
        kind: str,
        message: str,
        *,
        snapshot_path: Path | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.event_callback(
            AgentEvent(
                kind=kind,
                message=message,
                snapshot_path=snapshot_path,
                payload=payload or {},
            )
        )
