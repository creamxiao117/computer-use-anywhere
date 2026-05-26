from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from .models import SessionConfig
from .windows_control import WindowsDesktopController, SUPPORTED_COMPUTER_ACTIONS
from .browser_dom import BrowserDomController


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class McpServer:
    """Lightweight MCP server exposing computer-use tools via stdio JSON-RPC."""

    def __init__(self, session_config: SessionConfig) -> None:
        self.session_config = session_config
        self.desktop = WindowsDesktopController(session_config)
        self.browser_dom = BrowserDomController(session_config) if session_config.browser_dom_enabled else None
        self.tools: dict[str, ToolHandler] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        self.tools["computer"] = self._handle_computer
        if self.browser_dom is not None:
            self.tools["browser_dom"] = self._handle_browser_dom
        self.tools["screenshot"] = self._handle_screenshot

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self._process(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    def _process(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")
        if method == "initialize":
            return self._result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "computer-use-anywhere", "version": "3.0.0"},
            })
        if method == "tools/list":
            return self._result(req_id, {"tools": self._tool_schemas()})
        if method == "tools/call":
            params = request.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            handler = self.tools.get(name)
            if handler is None:
                return self._error(req_id, f"Unknown tool: {name}")
            try:
                result = handler(arguments)
                return self._result(req_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]})
            except Exception as exc:
                return self._error(req_id, f"{type(exc).__name__}: {exc}")
        if req_id is not None:
            return self._error(req_id, f"Unknown method: {method}")
        return None

    def _result(self, req_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id: Any, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": message}}

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas = [
            {
                "name": "computer",
                "description": "Control the Windows desktop: click, type, scroll, drag, key shortcuts, activate window.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(SUPPORTED_COMPUTER_ACTIONS)},
                        "coordinate": {"type": "array", "items": {"type": "integer"}},
                        "start_coordinate": {"type": "array", "items": {"type": "integer"}},
                        "end_coordinate": {"type": "array", "items": {"type": "integer"}},
                        "text": {"type": "string"},
                        "keys": {"type": ["array", "string"]},
                        "scroll_amount": {"type": "integer"},
                        "seconds": {"type": "number"},
                        "window_title": {"type": "string"},
                        "expected_window_title": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "screenshot",
                "description": "Capture a screenshot of the current desktop and return its base64 JPEG.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
        if self.browser_dom is not None:
            schemas.append({
                "name": "browser_dom",
                "description": "Operate browser DOM via Chrome DevTools Protocol.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "target": {"type": "string"},
                        "url": {"type": "string"},
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["action"],
                },
            })
        return schemas

    def _handle_computer(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.desktop.execute(arguments)
        return {
            "message": result.message,
            "snapshot_path": str(result.snapshot.path),
            "snapshot_width": result.snapshot.width,
            "snapshot_height": result.snapshot.height,
            "actual_width": result.snapshot.actual_width,
            "actual_height": result.snapshot.actual_height,
        }

    def _handle_browser_dom(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.browser_dom is None:
            return {"error": "browser_dom not enabled"}
        result = self.browser_dom.execute(arguments)
        return {"message": result.message}

    def _handle_screenshot(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.desktop.capture_snapshot("mcp")
        return {
            "snapshot_path": str(snapshot.path),
            "snapshot_width": snapshot.width,
            "snapshot_height": snapshot.height,
            "actual_width": snapshot.actual_width,
            "actual_height": snapshot.actual_height,
            "base64": snapshot.image_base64,
        }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Claude Computer Use MCP Server")
    parser.add_argument("--browser-debug-port", type=int, default=9222)
    parser.add_argument("--browser-debug-host", type=str, default="127.0.0.1")
    parser.add_argument("--target-resolution", type=str, default="1280x720")
    args = parser.parse_args()
    config = SessionConfig(
        target_resolution=args.target_resolution,
        browser_dom_enabled=True,
        browser_debug_port=args.browser_debug_port,
        browser_debug_host=args.browser_debug_host,
    )
    server = McpServer(config)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
