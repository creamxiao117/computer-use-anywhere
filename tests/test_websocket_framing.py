from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.browser_dom import _SimpleWebSocket, BrowserDomError


class _MockSocket:
    """模拟服务端->客户端链路：recv 依次吐出预置帧；sendall 记录发送内容。"""

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self.sent = b""

    def recv(self, n: int) -> bytes:
        if not self._buf:
            return b""  # 对端关闭
        chunk = self._buf[:n]
        self._buf = self._buf[n:]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def close(self) -> None:
        self._buf = b""


def _make_frame(*, fin: bool, opcode: int, payload: bytes) -> bytes:
    """构造服务端->客户端的未掩码帧（服务器发送方不掩码）。"""
    b0 = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    if length < 126:
        header = bytes([b0, length])
    elif length < 65536:
        header = bytes([b0, 126]) + struct.pack("!H", length)
    else:
        header = bytes([b0, 127]) + struct.pack("!Q", length)
    return header + payload


class WebSocketFramingTests(unittest.TestCase):
    def _ws(self, data: bytes) -> _SimpleWebSocket:
        ws = _SimpleWebSocket("ws://127.0.0.1:9222")
        ws.sock = _MockSocket(data)
        return ws

    def test_fragmentation_reassembled(self) -> None:
        """续帧(0x0)+FIN 必须拼接成完整消息（修复核心路径）。"""
        frames = _make_frame(fin=False, opcode=0x1, payload=b"Hello ") + \
                 _make_frame(fin=True, opcode=0x0, payload=b"World")
        ws = self._ws(frames)
        self.assertEqual(ws._recv_message(), b"Hello World")

    def test_ping_gets_pong(self) -> None:
        """收到 ping(0x9) 应自动回 pong(0xA)，并继续读完后续消息。"""
        frames = _make_frame(fin=True, opcode=0x9, payload=b"ping") + \
                 _make_frame(fin=True, opcode=0x1, payload=b"done")
        mock = _MockSocket(frames)
        ws = _SimpleWebSocket("ws://127.0.0.1:9222")
        ws.sock = mock
        self.assertEqual(ws._recv_message(), b"done")
        # 回送的 pong 帧：FIN+opcode 0xA，掩码位+长度4 -> 0x8a 0x84，后跟 4 掩码 + 4 载荷
        self.assertEqual(mock.sent[:2], b"\x8a\x84")
        self.assertEqual(len(mock.sent), 10)

    def test_close_raises(self) -> None:
        """收到 close(0x8) 必须抛 BrowserDomError，而非静默当作连接关闭。"""
        ws = self._ws(_make_frame(fin=True, opcode=0x8, payload=b""))
        with self.assertRaises(BrowserDomError):
            ws._recv_message()


if __name__ == "__main__":
    unittest.main()
