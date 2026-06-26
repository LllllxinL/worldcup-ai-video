"""基于 OpenAI 兼容 API 的通用 agent loop，驱动 Palmier MCP。

用法：
    from loopwc.agent import EditingAgent
    agent = EditingAgent(base_url="...", api_key="...", model="gpt-4o")
    report = agent.run(system_prompt="...", task_prompt="...")
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

from .mcp_client import PalmierMCPClient


@dataclass
class AgentMetrics:
    model: str = ""
    total_time_seconds: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    tool_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "total_time_seconds": round(self.total_time_seconds, 2),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "tool_history": self.tool_history,
        }


def _mcp_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 MCP tool schema 转成 OpenAI function-calling 格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object"}),
            },
        }
        for t in tools
    ]


class EditingAgent:
    """用 OpenAI 兼容 API 驱动 Palmier MCP 完成剪辑。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        mcp_url: str = "http://127.0.0.1:19789/mcp",
        max_turns: int = 200,
        temperature: float = 0.2,
    ):
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=600)
        self.model = model
        self.mcp = PalmierMCPClient(mcp_url)
        self.max_turns = max_turns
        self.temperature = temperature
        self.metrics = AgentMetrics(model=model)
        # 拉取可用工具
        self.mcp_tools = self.mcp.list_tools()
        self.openai_tools = _mcp_tools_to_openai(self.mcp_tools)

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """粗略估算成本（USD）。不同模型差异大，仅用于横向对比。"""
        # 按常见价格粗估，可后续在 config 里配精确价格
        pricing = {
            "claude-sonnet-4-6": (3.0, 15.0),
            "claude-sonnet-4-7": (3.0, 15.0),
            "claude-opus-4-7": (15.0, 75.0),
            "claude-opus-4-8": (15.0, 75.0),
            "gpt-4o": (2.5, 10.0),
            "gpt-4o-mini": (0.15, 0.6),
            "gemini-1.5-pro": (3.5, 10.5),
            "deepseek-chat": (0.14, 0.28),
            "deepseek-v4": (0.14, 0.28),
            "kimi-latest": (2.0, 6.0),
            "glm-4v": (2.0, 6.0),
            "qwen2.5-vl": (2.0, 6.0),
            "minimax": (2.0, 6.0),
        }
        # 尝试前缀匹配
        for key, (inp, out) in pricing.items():
            if key in self.model.lower() or self.model.lower().startswith(key):
                return (prompt_tokens * inp + completion_tokens * out) / 1_000_000
        # 兜底：按 2/10 估
        return (prompt_tokens * 2.0 + completion_tokens * 10.0) / 1_000_000

    def _build_image_content(self, data: bytes, media_type: str = "image/png") -> dict[str, Any]:
        """把二进制图片转成 OpenAI image_url 格式。"""
        b64 = base64.b64encode(data).decode("utf-8")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{b64}"},
        }

    def _parse_mcp_result(self, result: Any) -> list[dict[str, Any]]:
        """把 MCP 工具结果转成 OpenAI message content 块。"""
        contents: list[dict[str, Any]] = []
        if result is None:
            contents.append({"type": "text", "text": "(no result)"})
            return contents

        # inspect_media 返回的内容可能是图片数组
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    if item.get("type") == "image":
                        data = item.get("data", b"")
                        if isinstance(data, str):
                            data = base64.b64decode(data)
                        mt = item.get("mimeType", "image/png")
                        contents.append(self._build_image_content(data, mt))
                    elif item.get("type") == "text":
                        contents.append({"type": "text", "text": item.get("text", "")})
                    else:
                        contents.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
                else:
                    contents.append({"type": "text", "text": str(item)})
        else:
            text = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
            contents.append({"type": "text", "text": text})
        return contents

    def run(self, system_prompt: str, task_prompt: str) -> tuple[str, AgentMetrics]:
        """运行 agent loop，返回最终报告和指标。"""
        start = time.time()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt},
        ]

        for turn in range(self.max_turns):
            t0 = time.time()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.openai_tools,
                tool_choice="auto",
                temperature=self.temperature,
                max_tokens=4096,
            )
            self.metrics.llm_calls += 1
            self.metrics.total_time_seconds += time.time() - t0

            usage = response.usage
            if usage:
                self.metrics.prompt_tokens += usage.prompt_tokens or 0
                self.metrics.completion_tokens += usage.completion_tokens or 0
                self.metrics.total_tokens += usage.total_tokens or 0
                self.metrics.estimated_cost_usd = self._estimate_cost(
                    self.metrics.prompt_tokens, self.metrics.completion_tokens
                )

            choice = response.choices[0]
            message = choice.message

            # 把 assistant 消息加入历史
            assistant_msg = {
                "role": "assistant",
                "content": message.content or "",
            }
            if message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]
            messages.append(assistant_msg)

            # 如果没有 tool_calls，说明 agent 完成了
            if not message.tool_calls:
                self.metrics.total_time_seconds = time.time() - start
                return message.content or "", self.metrics

            # 执行 tool_calls
            for tc in message.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                self.metrics.tool_calls += 1
                self.metrics.tool_history.append({"turn": turn, "tool": name, "args": args})
                print(f"[tool] {name} {json.dumps(args, ensure_ascii=False)[:120]}", flush=True)

                try:
                    result = self.mcp.call(name, args)
                except Exception as e:
                    result = {"error": str(e)}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False) if not isinstance(result, (str, bytes)) else str(result),
                })

        self.metrics.total_time_seconds = time.time() - start
        return "(reached max turns)", self.metrics
