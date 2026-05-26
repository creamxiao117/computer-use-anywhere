from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .models import SessionConfig


class BrowserDomError(RuntimeError):
    pass


@dataclass(slots=True)
class BrowserDomResult:
    message: str


class BrowserDomController:
    def __init__(self, settings: SessionConfig) -> None:
        self.settings = settings
        self.base_url = f"http://{settings.browser_debug_host}:{settings.browser_debug_port}"

    def execute(self, arguments: dict[str, Any]) -> BrowserDomResult:
        action = str(arguments.get("action") or "").strip().lower()
        if not action:
            raise BrowserDomError("browser_dom 缺少 action。")
        if action == "status":
            return BrowserDomResult(self._status())
        if action == "read_page":
            return BrowserDomResult(self._read_page(arguments))
        if action == "navigate":
            return BrowserDomResult(self._navigate(arguments))
        if action == "click_selector":
            return BrowserDomResult(self._click_selector(arguments))
        if action == "type_selector":
            return BrowserDomResult(self._type_selector(arguments))
        if action == "click_text":
            return BrowserDomResult(self._click_text(arguments))
        if action == "wait_text":
            return BrowserDomResult(self._wait_text(arguments))
        if action == "wait_selector":
            return BrowserDomResult(self._wait_selector(arguments))
        if action == "get_selector":
            return BrowserDomResult(self._get_selector(arguments))
        if action == "press_selector":
            return BrowserDomResult(self._press_selector(arguments))
        if action == "evaluate":
            return BrowserDomResult(self._evaluate_user_script(arguments))
        raise BrowserDomError(f"不支持的 browser_dom 操作：{action}。")

    def _status(self) -> str:
        pages = self._list_pages()
        if not pages:
            return "浏览器调试端口已连接，但没有找到可操作的页面标签。"
        lines = ["浏览器 DOM 已连接。当前可操作页面："]
        for index, page in enumerate(pages[:8], start=1):
            title = page.get("title") or "无标题"
            url = page.get("url") or ""
            lines.append(f"{index}. {title} | {url}")
        return "\n".join(lines)

    def _read_page(self, arguments: dict[str, Any]) -> str:
        page = self._select_page(arguments)
        expression = r"""
(() => {
  const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
  const selectorOf = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const name = el.getAttribute('name');
    if (name) return el.tagName.toLowerCase() + '[name="' + CSS.escape(name) + '"]';
    const aria = el.getAttribute('aria-label');
    if (aria) return el.tagName.toLowerCase() + '[aria-label="' + CSS.escape(aria) + '"]';
    const classes = Array.from(el.classList || []).slice(0, 3).map((c) => '.' + CSS.escape(c)).join('');
    return el.tagName.toLowerCase() + classes;
  };
  const pick = (items) => Array.from(items).slice(0, 40).map((el) => ({
    selector: selectorOf(el),
    text: textOf(el).slice(0, 120),
    href: el.href || '',
    type: el.type || '',
    placeholder: el.getAttribute('placeholder') || '',
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
  }));
  return JSON.stringify({
    title: document.title,
    url: location.href,
    text: (document.body ? document.body.innerText : '').trim().replace(/\s+/g, ' ').slice(0, 4000),
    inputs: pick(document.querySelectorAll('input, textarea, [contenteditable="true"]')),
    buttons: pick(document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')),
    links: pick(document.querySelectorAll('a[href]')),
  });
})()
"""
        result = self._runtime_eval(page, expression)
        return self._format_json_result("页面 DOM 摘要", result)

    def _navigate(self, arguments: dict[str, Any]) -> str:
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise BrowserDomError("navigate 需要提供 url。")
        if "://" not in url:
            url = "https://" + url
        page = self._select_page(arguments)
        self._cdp_call(page, "Page.navigate", {"url": url})
        time.sleep(0.5)
        return f"已通过 DOM 工具导航到：{url}"

    def _click_selector(self, arguments: dict[str, Any]) -> str:
        selector = str(arguments.get("selector") or "").strip()
        if not selector:
            raise BrowserDomError("click_selector 需要提供 selector。")
        page = self._select_page(arguments)
        expression = """
(async () => {
  const selector = %s;
  const el = document.querySelector(selector);
  if (!el) return JSON.stringify({ok:false, error:'未找到选择器', selector});
  el.scrollIntoView({block:'center', inline:'center'});
  await new Promise((resolve) => setTimeout(resolve, 80));
  el.click();
  return JSON.stringify({ok:true, selector, text:(el.innerText || el.value || '').trim().slice(0, 120), url: location.href});
})()
""" % json.dumps(selector, ensure_ascii=False)
        result = self._runtime_eval(page, expression, await_promise=True)
        return self._format_json_result("已尝试按选择器点击", result)

    def _type_selector(self, arguments: dict[str, Any]) -> str:
        selector = str(arguments.get("selector") or "").strip()
        text = str(arguments.get("text") or "")
        submit = bool(arguments.get("submit", False))
        clear = bool(arguments.get("clear", True))
        if not selector:
            raise BrowserDomError("type_selector 需要提供 selector。")
        page = self._select_page(arguments)
        expression = """
(async () => {
  const selector = %s;
  const text = %s;
  const submit = %s;
  const clear = %s;
  const el = document.querySelector(selector);
  if (!el) return JSON.stringify({ok:false, error:'未找到选择器', selector});
  el.scrollIntoView({block:'center', inline:'center'});
  await new Promise((resolve) => setTimeout(resolve, 80));
  el.focus();
  if (clear) {
    if ('value' in el) el.value = '';
    else el.textContent = '';
  }
  if ('value' in el) {
    el.value = text;
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  } else {
    document.execCommand('insertText', false, text);
  }
  if (submit) {
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true}));
    const form = el.closest('form');
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  }
  return JSON.stringify({ok:true, selector, typed:text.length, submit, url: location.href});
})()
""" % (
            json.dumps(selector, ensure_ascii=False),
            json.dumps(text, ensure_ascii=False),
            json.dumps(submit),
            json.dumps(clear),
        )
        result = self._runtime_eval(page, expression, await_promise=True)
        return self._format_json_result("已尝试按选择器输入", result)

    def _click_text(self, arguments: dict[str, Any]) -> str:
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise BrowserDomError("click_text 需要提供 text。")
        page = self._select_page(arguments)
        expression = """
(async () => {
  const needle = %s.toLowerCase();
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, input[type="button"], input[type="submit"], label, span, div'));
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ');
  const el = candidates.find((item) => visible(item) && textOf(item).toLowerCase().includes(needle));
  if (!el) return JSON.stringify({ok:false, error:'未找到包含该文本的可见元素', text:%s});
  el.scrollIntoView({block:'center', inline:'center'});
  await new Promise((resolve) => setTimeout(resolve, 80));
  el.click();
  return JSON.stringify({ok:true, matched:textOf(el).slice(0, 120), url: location.href});
})()
""" % (json.dumps(text, ensure_ascii=False), json.dumps(text, ensure_ascii=False))
        result = self._runtime_eval(page, expression, await_promise=True)
        return self._format_json_result("已尝试按文本点击", result)

    def _wait_text(self, arguments: dict[str, Any]) -> str:
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise BrowserDomError("wait_text 需要提供 text。")
        timeout_seconds = self._timeout_seconds(arguments)
        deadline = time.monotonic() + timeout_seconds
        page = self._select_page(arguments)
        while time.monotonic() <= deadline:
            expression = """
(() => {
  const needle = %s.toLowerCase();
  const bodyText = (document.body ? document.body.innerText : '').toLowerCase();
  return JSON.stringify({ok: bodyText.includes(needle), title: document.title, url: location.href});
})()
""" % json.dumps(text, ensure_ascii=False)
            result = self._runtime_eval(page, expression)
            parsed = self._parse_json_value(result)
            if isinstance(parsed, dict) and parsed.get("ok"):
                return self._format_json_result(f"已等待到页面文字“{text}”", parsed)
            time.sleep(0.35)
        raise BrowserDomError(f"等待 {timeout_seconds:.1f} 秒后仍未看到页面文字“{text}”。")

    def _wait_selector(self, arguments: dict[str, Any]) -> str:
        selector = str(arguments.get("selector") or "").strip()
        if not selector:
            raise BrowserDomError("wait_selector 需要提供 selector。")
        timeout_seconds = self._timeout_seconds(arguments)
        deadline = time.monotonic() + timeout_seconds
        page = self._select_page(arguments)
        while time.monotonic() <= deadline:
            expression = """
(() => {
  const selector = %s;
  const el = document.querySelector(selector);
  const visible = !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  return JSON.stringify({ok: !!el, visible, selector, title: document.title, url: location.href});
})()
""" % json.dumps(selector, ensure_ascii=False)
            result = self._runtime_eval(page, expression)
            parsed = self._parse_json_value(result)
            if isinstance(parsed, dict) and parsed.get("ok") and (parsed.get("visible") or arguments.get("allow_hidden")):
                return self._format_json_result(f"已等待到选择器“{selector}”", parsed)
            time.sleep(0.35)
        raise BrowserDomError(f"等待 {timeout_seconds:.1f} 秒后仍未找到选择器“{selector}”。")

    def _get_selector(self, arguments: dict[str, Any]) -> str:
        selector = str(arguments.get("selector") or "").strip()
        if not selector:
            raise BrowserDomError("get_selector 需要提供 selector。")
        page = self._select_page(arguments)
        expression = """
(() => {
  const selector = %s;
  const el = document.querySelector(selector);
  if (!el) return JSON.stringify({ok:false, error:'未找到选择器', selector});
  const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
  const value = ('value' in el) ? el.value : '';
  return JSON.stringify({
    ok:true,
    selector,
    tag: el.tagName.toLowerCase(),
    text: text.slice(0, 2000),
    value: String(value).slice(0, 2000),
    href: el.href || '',
    checked: !!el.checked,
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
    url: location.href,
  });
})()
""" % json.dumps(selector, ensure_ascii=False)
        result = self._runtime_eval(page, expression)
        return self._format_json_result("选择器读取结果", result)

    def _press_selector(self, arguments: dict[str, Any]) -> str:
        selector = str(arguments.get("selector") or "").strip()
        key = str(arguments.get("key") or "Enter").strip() or "Enter"
        if not selector:
            raise BrowserDomError("press_selector 需要提供 selector。")
        page = self._select_page(arguments)
        expression = """
(async () => {
  const selector = %s;
  const key = %s;
  const el = document.querySelector(selector);
  if (!el) return JSON.stringify({ok:false, error:'未找到选择器', selector});
  el.scrollIntoView({block:'center', inline:'center'});
  await new Promise((resolve) => setTimeout(resolve, 80));
  el.focus();
  const eventInit = {key, code:key, bubbles:true, cancelable:true};
  el.dispatchEvent(new KeyboardEvent('keydown', eventInit));
  el.dispatchEvent(new KeyboardEvent('keyup', eventInit));
  if (key.toLowerCase() === 'enter') {
    const form = el.closest('form');
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  }
  return JSON.stringify({ok:true, selector, key, url: location.href});
})()
""" % (json.dumps(selector, ensure_ascii=False), json.dumps(key, ensure_ascii=False))
        result = self._runtime_eval(page, expression, await_promise=True)
        return self._format_json_result("已尝试向选择器发送按键", result)

    def _evaluate_user_script(self, arguments: dict[str, Any]) -> str:
        script = str(arguments.get("script") or "").strip()
        if not script:
            raise BrowserDomError("evaluate 需要提供 script。")
        page = self._select_page(arguments)
        result = self._runtime_eval(page, script, await_promise=bool(arguments.get("await_promise", False)))
        return self._format_json_result("JS 执行结果", result)

    def _select_page(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pages = self._list_pages()
        if not pages:
            raise BrowserDomError("没有找到可操作的浏览器页面。请确认 Chrome/Edge 已用 --remote-debugging-port 启动。")
        target = str(arguments.get("target") or arguments.get("url_contains") or "").strip().casefold()
        if target:
            for page in pages:
                haystack = f"{page.get('title') or ''} {page.get('url') or ''}".casefold()
                if target in haystack:
                    return page
        return pages[0]

    def _list_pages(self) -> list[dict[str, Any]]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/json/list", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BrowserDomError(
                f"无法连接浏览器调试端口 {self.base_url}。请用 --remote-debugging-port={self.settings.browser_debug_port} 启动 Edge/Chrome。原始错误：{exc}"
            ) from exc
        if not isinstance(payload, list):
            return []
        pages = [
            item
            for item in payload
            if isinstance(item, dict)
            and item.get("type") == "page"
            and item.get("webSocketDebuggerUrl")
            and not str(item.get("url") or "").startswith("devtools://")
        ]
        return pages

    def _runtime_eval(self, page: dict[str, Any], expression: str, *, await_promise: bool = False) -> Any:
        response = self._cdp_call(
            page,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        )
        result = response.get("result", {}).get("result", {})
        if "exceptionDetails" in response.get("result", {}):
            raise BrowserDomError(f"JS 执行异常：{response['result']['exceptionDetails']}")
        return result.get("value")

    def _cdp_call(self, page: dict[str, Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        ws_url = str(page.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            raise BrowserDomError("页面缺少 webSocketDebuggerUrl。")
        with _SimpleWebSocket(ws_url, timeout=5) as ws:
            message_id = 1
            ws.send_json({"id": message_id, "method": method, "params": params or {}})
            while True:
                message = ws.recv_json()
                if message.get("id") == message_id:
                    if "error" in message:
                        raise BrowserDomError(f"CDP 调用失败：{message['error']}")
                    return message

    @staticmethod
    def _format_json_result(title: str, value: Any) -> str:
        value = BrowserDomController._parse_json_value(value)
        return f"{title}：\n{json.dumps(value, ensure_ascii=False, indent=2)[:8000]}"

    @staticmethod
    def _parse_json_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped

    @staticmethod
    def _timeout_seconds(arguments: dict[str, Any]) -> float:
        raw = arguments.get("timeout_seconds", arguments.get("timeout", 8))
        return max(0.5, min(float(raw), 60.0))


class _SimpleWebSocket:
    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def __enter__(self) -> _SimpleWebSocket:
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "ws":
            raise BrowserDomError(f"当前只支持 ws:// 调试地址：{self.url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        sock = socket.create_connection((host, port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            sock.close()
            raise BrowserDomError(f"WebSocket 握手失败：{response[:200]!r}")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest())
        if accept not in response:
            sock.close()
            raise BrowserDomError("WebSocket 握手校验失败。")
        self.sock = sock
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def recv_json(self) -> dict[str, Any]:
        payload = self._recv_frame()
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BrowserDomError(f"WebSocket 返回了非 JSON 数据：{payload[:200]!r}") from exc
        if not isinstance(decoded, dict):
            raise BrowserDomError(f"WebSocket 返回格式异常：{decoded!r}")
        return decoded

    def _send_frame(self, payload: bytes) -> None:
        if self.sock is None:
            raise BrowserDomError("WebSocket 未连接。")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126])
            header.extend(struct.pack("!H", length))
        else:
            header.extend([0x80 | 127])
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_frame(self) -> bytes:
        if self.sock is None:
            raise BrowserDomError("WebSocket 未连接。")
        first = self._recv_exact(2)
        opcode = first[0] & 0x0F
        if opcode == 0x8:
            raise BrowserDomError("WebSocket 已关闭。")
        if opcode not in {0x1, 0x2}:
            return self._recv_frame()
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return payload

    def _recv_exact(self, size: int) -> bytes:
        if self.sock is None:
            raise BrowserDomError("WebSocket 未连接。")
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise BrowserDomError("WebSocket 连接提前关闭。")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
