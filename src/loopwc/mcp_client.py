"""Palmier MCP 的极简 HTTP 客户端。"""
from __future__ import annotations

import json
import uuid
from typing import Any

import requests


class PalmierMCPClient:
    """通过 HTTP JSON-RPC 调用 Palmier MCP 工具。"""

    def __init__(self, url: str = "http://127.0.0.1:19789/mcp", timeout: int = 120):
        self.url = url
        self.timeout = timeout

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        resp = requests.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        result = data.get("result", {})
        # MCP tool result 封装在 content 数组里
        content = result.get("content", [])
        if not content:
            return None
        # 通常第一个 content 是 text 类型，包含 JSON
        first = content[0]
        if first.get("type") == "text":
            text = first.get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return content

    def list_tools(self) -> list[dict[str, Any]]:
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/list"}
        resp = requests.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("tools", [])
