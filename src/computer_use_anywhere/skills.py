from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


SkillHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: SkillHandler | None = None
    mcp_server_command: list[str] | None = None
    enabled: bool = True


class SkillRegistry:
    """Registry for skills and MCP-based tools."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._mcp_processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def register(self, skill: SkillDefinition) -> None:
        with self._lock:
            self._skills[skill.name] = skill

    def unregister(self, name: str) -> None:
        with self._lock:
            self._skills.pop(name, None)

    def list_skills(self) -> list[SkillDefinition]:
        with self._lock:
            return [s for s in self._skills.values() if s.enabled]

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for skill in self.list_skills():
            schema = {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.parameters,
                },
            }
            schemas.append(schema)
        return schemas

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            skill = self._skills.get(name)
        if skill is None:
            return {"error": f"Skill '{name}' not found."}
        if skill.handler is not None:
            try:
                return skill.handler(arguments)
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}
        if skill.mcp_server_command is not None:
            return self._execute_mcp(skill, arguments)
        return {"error": f"Skill '{name}' has no handler or MCP command."}

    def _execute_mcp(self, skill: SkillDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        proc = self._mcp_processes.get(skill.name)
        if proc is None or proc.poll() is not None:
            try:
                proc = subprocess.Popen(
                    skill.mcp_server_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._mcp_processes[skill.name] = proc
                # Initialize
                init_req = (
                    json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
                    + "\n"
                )
                proc.stdin.write(init_req)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
                # Read init response
                line = proc.stdout.readline()  # type: ignore[union-attr]
            except Exception as exc:
                return {"error": f"Failed to start MCP server for {skill.name}: {exc}"}
        try:
            request = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "tools/call",
                "params": {"name": skill.name, "arguments": arguments},
            }
            proc.stdin.write(json.dumps(request) + "\n")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
            line = proc.stdout.readline()  # type: ignore[union-attr]
            response = json.loads(line)
            if "error" in response:
                return {"error": response["error"].get("message", "MCP error")}
            return {"result": response.get("result", {})}
        except Exception as exc:
            return {"error": f"MCP communication error: {exc}"}

    def shutdown(self) -> None:
        for proc in self._mcp_processes.values():
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass


def built_in_skills() -> SkillRegistry:
    """Return a registry with built-in skills pre-registered."""
    registry = SkillRegistry()
    # Example: file read/write skill (placeholder implementation)
    registry.register(
        SkillDefinition(
            name="file_read",
            description="Read a text file from the local filesystem.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path."},
                },
                "required": ["path"],
            },
            handler=lambda args: {"content": _read_file(args.get("path", ""))},
        )
    )
    registry.register(
        SkillDefinition(
            name="file_write",
            description="Write text to a file on the local filesystem.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=lambda args: _write_file(args.get("path", ""), args.get("content", "")),
        )
    )
    registry.register(
        SkillDefinition(
            name="shell",
            description="Run a shell command and return stdout/stderr.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
            handler=lambda args: _run_shell(args.get("command", ""), args.get("timeout", 30)),
        )
    )
    return registry


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as exc:
        return f"Error reading file: {exc}"


def _write_file(path: str, content: str) -> dict[str, Any]:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"message": f"Wrote {len(content)} chars to {path}."}
    except Exception as exc:
        return {"error": str(exc)}


def _run_shell(command: str, timeout: int) -> dict[str, Any]:
    import subprocess

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:2000],
        }
    except Exception as exc:
        return {"error": str(exc)}
