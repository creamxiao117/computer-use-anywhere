from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_OFFICIAL_COMPATIBLE = "official_compatible"
PROVIDER_ANTHROPIC_OFFICIAL = "anthropic_official"
PROVIDER_SEMI_OFFICIAL = "semi_official"


@dataclass(slots=True)
class ProviderConfig:
    provider_kind: str = PROVIDER_OPENAI_COMPATIBLE
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.2
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    anthropic_version: str = "2023-06-01"
    anthropic_beta: str = ""
    anthropic_tool_type: str = ""
    enable_thinking: bool = False
    thinking_budget: int = 2048
    thinking_intensity: str = "medium"  # medium | high
    enable_compact: bool = False
    # Advisor model for dual-model strategy
    advisor_enabled: bool = False
    advisor_source: str = "inherit_main"  # inherit_main | custom (forced to inherit_main when main is anthropic_official)
    advisor_model: str = ""
    advisor_api_key: str = ""
    advisor_base_url: str = ""
    advisor_max_tokens: int = 4096
    advisor_temperature: float = 0.2


@dataclass(slots=True)
class SessionConfig:
    # Resolution strategy: "1280x720" | "1920x1080" | "max_api_fit" | "scale"
    target_resolution: str = "1280x720"
    scale: float = 0.8  # fallback when target_resolution == "scale"
    jpeg_quality: int = 70
    max_steps: int = 30
    confirm_actions: bool = True
    hide_window_while_running: bool = True
    mask_own_window: bool = True
    own_window_title: str = "Computer Use Anywhere"
    official_enhanced: bool = True
    browser_dom_enabled: bool = True
    browser_dom_first: bool = True
    browser_debug_host: str = "127.0.0.1"
    browser_debug_port: int = 9222
    session_root: Path | None = None
    # Model family hint for API limits ("claude_4_6" | "opus_4_7" | "auto")
    model_family_hint: str = "auto"


@dataclass(slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class AssistantReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    assistant_message: dict[str, Any] | None = None
    reasoning_summary: str = ""


@dataclass(slots=True)
class Snapshot:
    path: Path
    data_url: str
    width: int
    height: int
    actual_width: int
    actual_height: int
    foreground_window_title: str = ""
    visible_window_titles: list[str] = field(default_factory=list)

    @property
    def image_base64(self) -> str:
        return self.data_url.split(",", 1)[1]


@dataclass(slots=True)
class ActionResult:
    message: str
    snapshot: Snapshot
    # Visualization metadata (added v3.2 for overlay/HUD feedback)
    # action_kind: one of the ACTION_* constants below, or "" if not visualizable
    # physical_coord: screen pixel coord after DPI/resolution mapping, or None
    # action_extra: action-specific payload, e.g. {"keys": "Ctrl+C"} / {"text": "hello"} / {"drag_to": (x,y)}
    action_kind: str = ""
    physical_coord: tuple[int, int] | None = None
    action_extra: dict[str, Any] = field(default_factory=dict)


# Action kind constants used by visual_overlay.flash_action() and AgentEvent.payload
ACTION_LEFT_CLICK = "left_click"
ACTION_RIGHT_CLICK = "right_click"
ACTION_DOUBLE_CLICK = "double_click"
ACTION_MIDDLE_CLICK = "middle_click"
ACTION_DRAG_START = "drag_start"
ACTION_DRAG_MOVE = "drag_move"
ACTION_DRAG_END = "drag_end"
ACTION_SCROLL_UP = "scroll_up"
ACTION_SCROLL_DOWN = "scroll_down"
ACTION_KEY_PRESS = "key_press"
ACTION_TYPE_TEXT = "type_text"
ACTION_MOVE_ONLY = "move_only"

ACTION_KINDS = {
    ACTION_LEFT_CLICK,
    ACTION_RIGHT_CLICK,
    ACTION_DOUBLE_CLICK,
    ACTION_MIDDLE_CLICK,
    ACTION_DRAG_START,
    ACTION_DRAG_MOVE,
    ACTION_DRAG_END,
    ACTION_SCROLL_UP,
    ACTION_SCROLL_DOWN,
    ACTION_KEY_PRESS,
    ACTION_TYPE_TEXT,
    ACTION_MOVE_ONLY,
}

# Agent state for visualization breathing-light state machine
AGENT_STATE_MAIN_IDLE = "main_idle"
AGENT_STATE_MAIN_RUNNING = "main_running"
AGENT_STATE_ADVISOR_IDLE = "advisor_idle"
AGENT_STATE_ADVISOR_RUNNING = "advisor_running"


@dataclass(slots=True)
class AgentEvent:
    """Event emitted from agent main loop to the UI / overlay layer.

    payload schema (when applicable):
      kind="tool":
        - action_kind: str (one of ACTION_* constants)
        - physical_coord: tuple[int, int] | None  (screen pixels)
        - action_extra: dict (action-specific extras)
      kind="status":
        - agent_state: str (one of AGENT_STATE_* constants)
        - step_n: int
        - step_total: int
      kind="assistant":
        - step: int
        - thought_seconds: float
        - derived: bool
    """

    kind: str
    message: str
    snapshot_path: Path | None = None
    payload: dict[str, Any] = field(default_factory=dict)
