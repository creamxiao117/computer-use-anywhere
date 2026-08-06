from __future__ import annotations

import html
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import Snapshot


@dataclass(slots=True)
class ActionVerification:
    status: str
    message: str


class SessionReplay:
    def __init__(self, session_root: Path) -> None:
        self.session_root = session_root
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.session_root / "replay.jsonl"
        self.html_path = self.session_root / "replay.html"
        self._records: list[dict[str, Any]] = []

    def record(self, record: dict[str, Any]) -> None:
        payload = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
        self._records.append(payload)
        with self.jsonl_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def record_initial(self, *, task: str, provider_kind: str, snapshot: Snapshot) -> None:
        self.record(
            {
                "kind": "initial",
                "task": task,
                "provider_kind": provider_kind,
                "snapshot": snapshot_summary(snapshot),
            }
        )

    def record_step(
        self,
        *,
        step: int,
        tool_name: str,
        arguments: dict[str, Any],
        result_message: str,
        verification: ActionVerification,
        before_snapshot: Snapshot | None,
        after_snapshot: Snapshot | None,
        model_text: str = "",
        public_reasoning: str = "",
        duration_seconds: float | None = None,
    ) -> None:
        action = str(arguments.get("action") or "").strip()
        self.record(
            {
                "kind": "step",
                "step": step,
                "tool_name": tool_name,
                "action": action,
                "arguments": _safe_json(arguments),
                "model_text": model_text,
                "public_reasoning": public_reasoning,
                "result_message": result_message,
                "verification": asdict(verification),
                "before_snapshot": snapshot_summary(before_snapshot),
                "after_snapshot": snapshot_summary(after_snapshot),
                "duration_seconds": duration_seconds,
            }
        )

    def write_html(self, final_text: str) -> Path:
        rows = []
        for record in self._records:
            if record.get("kind") == "initial":
                snapshot = record.get("snapshot") or {}
                rows.append(
                    _section(
                        "初始状态",
                        [
                            ("任务", str(record.get("task") or "")),
                            ("模式", str(record.get("provider_kind") or "")),
                            ("截图", _image_html(snapshot.get("path"))),
                            ("前台窗口", str(snapshot.get("foreground_window_title") or "")),
                        ],
                    )
                )
                continue
            if record.get("kind") != "step":
                continue
            verification = record.get("verification") or {}
            rows.append(
                _section(
                    f"第 {record.get('step')} 步 · {record.get('tool_name')} · {record.get('action')}",
                    [
                        (
                            "验证",
                            f"{verification.get('status') or ''}｜{verification.get('message') or ''}",
                        ),
                        ("公开说明", str(record.get("public_reasoning") or "")),
                        ("模型文本", str(record.get("model_text") or "")),
                        (
                            "动作参数",
                            json.dumps(record.get("arguments") or {}, ensure_ascii=False, indent=2),
                        ),
                        ("执行结果", str(record.get("result_message") or "")),
                        ("执行前", _image_html((record.get("before_snapshot") or {}).get("path"))),
                        ("执行后", _image_html((record.get("after_snapshot") or {}).get("path"))),
                    ],
                )
            )
        document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Computer Use 会话复盘</title>
  <style>
    body {{ margin: 0; padding: 24px; background: #f6f2ea; color: #2d261f; font-family: "Microsoft YaHei UI", sans-serif; }}
    h1 {{ margin: 0 0 8px; font-size: 26px; }}
    .final {{ margin: 0 0 20px; color: #6d655b; }}
    section {{ margin: 0 0 18px; padding: 16px; background: #fcf8f2; border: 1px solid #ded4c7; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    dl {{ display: grid; grid-template-columns: 110px 1fr; gap: 10px 14px; margin: 0; }}
    dt {{ color: #8b6f5d; font-weight: 700; }}
    dd {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
    img {{ max-width: 720px; max-height: 420px; border: 1px solid #ded4c7; background: #f3ece1; }}
    code, pre {{ font-family: Consolas, monospace; }}
  </style>
</head>
<body>
  <h1>Computer Use 会话复盘</h1>
  <p class="final">最终结果：{html.escape(final_text or "")}</p>
  {"".join(rows)}
</body>
</html>
"""
        self.html_path.write_text(document, encoding="utf-8")
        return self.html_path


def verify_action_result(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result_message: str,
    before_snapshot: Snapshot | None,
    after_snapshot: Snapshot | None,
    own_window_title: str,
) -> ActionVerification:
    action = str(arguments.get("action") or "").strip().lower()
    result = result_message or ""
    lowered_result = result.casefold()

    _FAILURE_ZH = ("失败", "出错", "拒绝", "不可用")
    _FAILURE_EN = (
        "failed", "failure", "error", "denied", "refused",
        "unavailable", "could not", "cannot", "not permitted",
    )
    if any(m in result for m in _FAILURE_ZH) or any(
        m in lowered_result for m in _FAILURE_EN
    ):
        return ActionVerification(
            "warn", "工具返回了失败/拒绝/不可用信息，需要模型基于最新截图修正。"
        )
    if tool_name == "browser_dom":
        return ActionVerification(
            "ok", "DOM 工具已返回结构化结果；后续应优先用 DOM 结果验证网页状态。"
        )
    if action in {"screenshot", "wait"}:
        return ActionVerification("info", "观察类动作不要求画面变化。")

    # 提取 click 类动作的坐标 (供"无变化"提示使用)
    click_xy: tuple[int, int] | None = None
    coord_raw = arguments.get("coordinate")
    if isinstance(coord_raw, (list, tuple)) and len(coord_raw) == 2:
        try:
            click_xy = (int(coord_raw[0]), int(coord_raw[1]))
        except (TypeError, ValueError):
            click_xy = None

    if after_snapshot is not None:
        foreground = after_snapshot.foreground_window_title or ""
        if _title_contains(foreground, own_window_title) and action in {"type", "key"}:
            return ActionVerification(
                "warn", "执行后前台仍是代理窗口，输入类动作可能没有进入目标应用。"
            )
        expected = str(
            arguments.get("expected_window_title") or arguments.get("target_window_title") or ""
        ).strip()
        if expected and action in {"type", "key"} and not _title_contains(foreground, expected):
            return ActionVerification(
                "warn", f"执行后前台窗口“{foreground or '未知'}”不匹配期望“{expected}”。"
            )
        if action == "activate_window":
            query = str(arguments.get("window_title") or arguments.get("title") or "").strip()
            if query and _title_contains(foreground, query):
                return ActionVerification("ok", f"前台窗口已匹配“{query}”。")
            return ActionVerification(
                "warn", f"激活窗口后前台是“{foreground or '未知'}”，未确认匹配目标窗口。"
            )

    # 微验证: 按动作类型给出差异化的 "几乎无变化" 反馈
    has_no_change = "几乎没有变化" in result or "变化很小" in result
    if has_no_change:
        if (
            action in {"left_click", "click", "double_click", "right_click", "middle_click"}
            and click_xy
        ):
            return ActionVerification(
                "warn",
                f"⚠️ 点击 ({click_xy[0]}, {click_xy[1]}) 后画面几乎无变化。"
                f"**这强烈提示点错了位置或目标不可点击**。请重新观察当前截图，"
                f"特别注意 ({click_xy[0]}, {click_xy[1]}) 附近 ±40px 范围内是否真有可点击元素 "
                f"(按钮 / 链接 / 图标)。若该位置没有目标元素，请重新定位到正确像素；"
                f"若有但 UI 反应慢，可调用 wait()。",
            )
        if action in {"type", "key"}:
            return ActionVerification(
                "warn",
                "⚠️ 输入/按键后画面几乎无变化。**可能输入到了错误焦点窗口或操作未生效**。"
                "请通过截图确认目标输入框是否真的接收了文本；如果没有，先用 left_click 显式聚焦目标输入框再重输。",
            )
        if action == "scroll":
            return ActionVerification(
                "warn",
                "⚠️ 滚动后画面几乎无变化。可能已到顶/到底，或滚动条不在该坐标。"
                "如目标内容未出现，尝试改变 scroll 位置或方向。",
            )
        return ActionVerification("warn", "截图变化很小，这一步可能没有产生可见效果。")
    if before_snapshot and after_snapshot and before_snapshot.path == after_snapshot.path:
        return ActionVerification("warn", "执行前后截图文件相同，无法确认动作效果。")
    return ActionVerification("ok", "动作已执行并生成新观察结果。")


def snapshot_summary(snapshot: Snapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    return {
        "path": snapshot.path.name,
        "width": snapshot.width,
        "height": snapshot.height,
        "actual_width": snapshot.actual_width,
        "actual_height": snapshot.actual_height,
        "foreground_window_title": snapshot.foreground_window_title,
        "visible_window_titles": snapshot.visible_window_titles,
    }


def _safe_json(value: dict[str, Any]) -> dict[str, Any]:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _title_contains(title: str, query: str) -> bool:
    normalized_title = (title or "").strip().casefold()
    normalized_query = (query or "").strip().casefold()
    return bool(normalized_title and normalized_query and normalized_query in normalized_title)


def _section(title: str, rows: list[tuple[str, str]]) -> str:
    items = []
    for key, value in rows:
        rendered = value if value.startswith("<img ") else html.escape(value)
        items.append(f"<dt>{html.escape(key)}</dt><dd>{rendered}</dd>")
    return f"<section><h2>{html.escape(title)}</h2><dl>{''.join(items)}</dl></section>"


def _image_html(path: Any) -> str:
    if not path:
        return ""
    escaped = html.escape(str(path), quote=True)
    return f'<img src="{escaped}" alt="{escaped}">'
