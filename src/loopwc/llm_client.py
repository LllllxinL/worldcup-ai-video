"""统一的LLM客户端，支持多模型调用和成本追踪。

支持模型：
- Claude (Anthropic API)
- GPT-4o / GPT-4o-mini (OpenAI API)
- Gemini (Google API)
- DeepSeek (OpenAI兼容)
- Kimi (Moonshot API)
- Qwen (Alibaba API)
- MiniMax (OpenAI兼容)
- 其他OpenAI兼容模型

用法：
    from llm_client import LLMClient, ModelConfig

    client = LLMClient()
    # 使用默认模型（从config读取）
    result = client.complete(prompt, task_type="edit")
    # 使用指定模型
    result = client.complete(prompt, model="gpt-4o")
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# 尝试导入各SDK，不强制依赖
_anthropic = None
try:
    import anthropic
except ImportError:
    pass

_openai = None
try:
    import openai
except ImportError:
    pass


@dataclass
class CompletionResult:
    """LLM调用结果，包含响应内容和成本信息。"""
    text: str = ""                          # 响应文本
    model: str = ""                         # 实际使用的模型
    prompt_tokens: int = 0                  # prompt token数
    completion_tokens: int = 0              # completion token数
    total_tokens: int = 0                   # 总token数
    cost_usd: float = 0.0                   # 估算成本（美元）
    latency_ms: float = 0.0                 # 延迟（毫秒）
    tool_calls: list[dict] = field(default_factory=list)  # tool calls
    raw_response: Any = None                # 原始响应
    error: str = ""                         # 错误信息

    @property
    def success(self) -> bool:
        return not self.error and bool(self.text)


@dataclass
class ModelConfig:
    """模型配置。"""
    name: str                               # 模型标识名
    provider: str                         # 提供商: anthropic/openai/gemini/deepseek/kimi/qwen/minimax/other
    api_key: str = ""                       # API密钥
    base_url: str = ""                      # 自定义base_url（OpenAI兼容）
    model_id: str = ""                      # 实际API模型ID
    input_price: float = 0.0              # 输入价格（每百万token美元）
    output_price: float = 0.0             # 输出价格（每百万token美元）
    supports_tools: bool = True             # 是否支持tool calling
    supports_vision: bool = False           # 是否支持vision
    max_tokens: int = 4096                  # 最大输出token数
    temperature: float = 0.2                # 温度

    def __post_init__(self):
        if not self.model_id:
            self.model_id = self.name


# 默认模型价格表（每百万token，美元）
# 格式: (input_price, output_price)
DEFAULT_PRICING = {
    # Claude
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-7": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-haiku-4-5": (0.8, 4.0),
    # OpenAI
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    # Gemini
    "gemini-1.5-pro": (3.5, 10.5),
    "gemini-1.5-flash": (0.35, 0.7),
    "gemini-2.0-flash": (0.1, 0.4),
    # DeepSeek
    "deepseek-chat": (0.14, 0.28),
    "deepseek-v4": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    # Kimi
    "kimi-latest": (2.0, 6.0),
    "kimi-k1.5": (2.0, 6.0),
    "moonshot-v1-8k": (1.0, 3.0),
    # Qwen
    "qwen2.5-vl": (2.0, 6.0),
    "qwen-max": (2.4, 9.6),
    "qwen-plus": (0.8, 2.0),
    # MiniMax
    "minimax": (2.0, 6.0),
    "minimax-text-01": (1.0, 3.0),
    # 其他
    "glm-4v": (2.0, 6.0),
    "glm-4-plus": (0.5, 1.5),
}

# 模型到provider的映射
MODEL_TO_PROVIDER = {
    # Claude
    "claude-sonnet-4-6": "anthropic",
    "claude-sonnet-4-7": "anthropic",
    "claude-opus-4-7": "anthropic",
    "claude-opus-4-8": "anthropic",
    "claude-haiku-4-5": "anthropic",
    # OpenAI
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "gpt-4-turbo": "openai",
    "gpt-4": "openai",
    # Gemini
    "gemini-1.5-pro": "gemini",
    "gemini-1.5-flash": "gemini",
    "gemini-2.0-flash": "gemini",
    # DeepSeek
    "deepseek-chat": "deepseek",
    "deepseek-v4": "deepseek",
    "deepseek-reasoner": "deepseek",
    # Kimi
    "kimi-latest": "kimi",
    "kimi-k1.5": "kimi",
    "moonshot-v1-8k": "kimi",
    # Qwen
    "qwen2.5-vl": "qwen",
    "qwen-max": "qwen",
    "qwen-plus": "qwen",
    # MiniMax
    "minimax": "minimax",
    "minimax-text-01": "minimax",
    # 其他
    "glm-4v": "other",
    "glm-4-plus": "other",
}

# 各provider的默认base_url
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "gemini": "",  # 使用google SDK
    "deepseek": "https://api.deepseek.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "minimax": "https://api.minimax.chat/v1",
    "other": "",
}


def _get_env_key(provider: str) -> str:
    """获取环境变量名。"""
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }
    return env_map.get(provider, f"{provider.upper()}_API_KEY")


def _get_pricing(model_name: str) -> tuple[float, float]:
    """获取模型价格，返回(input_price, output_price)。"""
    model_lower = model_name.lower()
    for key, prices in DEFAULT_PRICING.items():
        if key in model_lower or model_lower.startswith(key):
            return prices
    # 兜底
    return (2.0, 10.0)


class LLMClient:
    """统一的LLM客户端。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """
        初始化LLM客户端。

        Args:
            config: 配置字典，从config.yaml读取
        """
        self.config = config or {}
        self._clients: dict[str, Any] = {}  # 缓存的客户端实例

    def _get_client(self, provider: str, api_key: str, base_url: str = "") -> Any:
        """获取或创建API客户端。"""
        cache_key = f"{provider}:{api_key[:8]}"
        if cache_key in self._clients:
            return self._clients[cache_key]

        if provider == "anthropic":
            if _anthropic is None:
                raise ImportError("anthropic SDK未安装，请运行: pip install anthropic")
            client = anthropic.Anthropic(api_key=api_key)
        elif provider in ("openai", "deepseek", "kimi", "qwen", "minimax", "other"):
            if _openai is None:
                raise ImportError("openai SDK未安装，请运行: pip install openai")
            url = base_url or DEFAULT_BASE_URLS.get(provider, "")
            client = openai.OpenAI(base_url=url, api_key=api_key)
        elif provider == "gemini":
            # Gemini使用google SDK
            try:
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                client = genai
            except ImportError:
                raise ImportError("google-generativeai未安装，请运行: pip install google-generativeai")
        else:
            raise ValueError(f"不支持的provider: {provider}")

        self._clients[cache_key] = client
        return client

    def _call_anthropic(
        self,
        client: Any,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int,
    ) -> CompletionResult:
        """调用Anthropic API。"""
        # 转换消息格式
        system_msg = ""
        api_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                api_messages.append(msg)

        kwargs = {
            "model": model_id,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            # Anthropic tool格式转换
            anthropic_tools = []
            for t in tools:
                anthropic_tools.append({
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object"}),
                })
            kwargs["tools"] = anthropic_tools

        response = client.messages.create(**kwargs)

        result = CompletionResult()
        result.model = model_id
        result.text = response.content[0].text if response.content else ""
        result.prompt_tokens = response.usage.input_tokens if response.usage else 0
        result.completion_tokens = response.usage.output_tokens if response.usage else 0
        result.total_tokens = result.prompt_tokens + result.completion_tokens

        # 提取tool calls
        if hasattr(response, "content"):
            for block in response.content:
                if block.type == "tool_use":
                    result.tool_calls.append({
                        "name": block.name,
                        "arguments": block.input,
                    })

        return result

    def _call_openai_compatible(
        self,
        client: Any,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int,
    ) -> CompletionResult:
        """调用OpenAI兼容API。"""
        kwargs = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)

        result = CompletionResult()
        result.model = response.model or model_id
        message = response.choices[0].message
        result.text = message.content or ""

        # 提取tool calls
        if message.tool_calls:
            for tc in message.tool_calls:
                result.tool_calls.append({
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })

        if response.usage:
            result.prompt_tokens = response.usage.prompt_tokens or 0
            result.completion_tokens = response.usage.completion_tokens or 0
            result.total_tokens = response.usage.total_tokens or 0

        result.raw_response = response
        return result

    def _call_gemini(
        self,
        client: Any,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int,
    ) -> CompletionResult:
        """调用Gemini API。"""
        # 合并消息为单个prompt
        prompt_parts = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            prompt_parts.append(f"[{role.upper()}]\n{content}")
        prompt = "\n\n".join(prompt_parts)

        model = client.GenerativeModel(model_id)
        response = model.generate_content(
            prompt,
            generation_config=client.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )

        result = CompletionResult()
        result.model = model_id
        result.text = response.text if hasattr(response, "text") else str(response)
        # Gemini不提供token统计，需要估算
        result.prompt_tokens = len(prompt) // 4  # 粗略估算
        result.completion_tokens = len(result.text) // 4
        result.total_tokens = result.prompt_tokens + result.completion_tokens
        return result

    def complete(
        self,
        prompt: str | None = None,
        messages: list[dict] | None = None,
        model: str | None = None,
        tools: list[dict] | None = None,
        task_type: str = "edit",
        **kwargs: Any,
    ) -> CompletionResult:
        """
        发送completion请求。

        Args:
            prompt: 单条prompt（与messages互斥）
            messages: 消息列表
            model: 指定模型名称，None则使用默认
            tools: tool定义列表
            task_type: 任务类型，用于从config读取默认配置
            **kwargs: 额外参数

        Returns:
            CompletionResult: 包含响应和成本信息
        """
        start_time = time.time()

        # 确定模型
        if model is None:
            # 从config读取默认模型
            if task_type == "edit":
                model = self.config.get("edit", "model", default="claude-sonnet-4-6")
            elif task_type == "script":
                model = self.config.get("deepseek", "model", default="deepseek-chat")
            else:
                model = "claude-sonnet-4-6"

        # 确定provider
        provider = MODEL_TO_PROVIDER.get(model, "other")

        # 获取API密钥
        api_key = ""
        if task_type == "edit":
            api_key = self.config.get("edit", "api_key", default="")
        elif task_type == "script":
            api_key = self.config.get("deepseek", "api_key", default="")

        if not api_key:
            env_key = _get_env_key(provider)
            api_key = os.environ.get(env_key, "")

        if not api_key:
            result = CompletionResult()
            result.error = f"未找到{provider}的API密钥"
            return result

        # 获取base_url
        base_url = ""
        if provider in ("deepseek", "kimi", "qwen", "minimax", "other"):
            base_url = self.config.get(provider, "base_url", default="")

        # 构建消息
        if messages is None:
            if prompt is None:
                result = CompletionResult()
                result.error = "必须提供prompt或messages"
                return result
            messages = [
                {"role": "user", "content": prompt},
            ]

        # 调用API
        try:
            client = self._get_client(provider, api_key, base_url)

            if provider == "anthropic":
                result = self._call_anthropic(
                    client, model, messages, tools, 0.2, 4096
                )
            elif provider == "gemini":
                result = self._call_gemini(
                    client, model, messages, tools, 0.2, 4096
                )
            else:
                result = self._call_openai_compatible(
                    client, model, messages, tools, 0.2, 4096
                )

        except Exception as e:
            result = CompletionResult()
            result.error = str(e)
            result.model = model
            return result

        # 计算成本
        result.latency_ms = (time.time() - start_time) * 1000
        input_price, output_price = _get_pricing(model)
        result.cost_usd = (
            result.prompt_tokens * input_price +
            result.completion_tokens * output_price
        ) / 1_000_000

        return result

    def get_model_config(self, model_name: str) -> ModelConfig:
        """获取模型配置。"""
        provider = MODEL_TO_PROVIDER.get(model_name, "other")
        input_price, output_price = _get_pricing(model_name)

        return ModelConfig(
            name=model_name,
            provider=provider,
            model_id=model_name,
            input_price=input_price,
            output_price=output_price,
        )

    def list_supported_models(self) -> list[str]:
        """返回支持的模型列表。"""
        return list(DEFAULT_PRICING.keys())


# 便捷函数
def create_client(config: dict[str, Any] | None = None) -> LLMClient:
    """创建LLM客户端实例。"""
    return LLMClient(config)
