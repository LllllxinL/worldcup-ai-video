"""LLM Benchmark 测试框架：并行测试多模型视频剪辑效果。

用法：
    python -m loopwc.benchmark --match-id 144933

功能：
    1. 并行启动多个模型的剪辑测试
    2. 每个模型使用独立的项目目录
    3. 自动记录每个模型的起止时间、状态
    4. 生成 benchmark_log.json 供后续费用统计
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .stages.edit import _build_prompt, _audio_duration, TEMPLATE
from .state import Status, load_job, save_job, Job

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 默认待测模型列表
DEFAULT_MODELS = [
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "Kimi-K2.6",
    "gpt-5.2",
    "gpt-5-mini",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "DeepSeek-V4-Pro",
    "DeepSeek-V4-Flash",
    "Qwen3.6-35B-A3B",
    "minimax.minimax-m2.5",
]

# 中转站配置
PROXY_BASE_URL = "https://newapi.elevatesphere.com"
PROXY_API_KEY = "sk-b3mwIegZKe12YF2zqyQZbgyka6WGyLg0HBqzYkjxDGhUHVaT"


@dataclass
class BenchmarkRun:
    """单次模型测试的运行记录。"""
    model: str
    match_id: str
    start_time: str = ""
    end_time: str = ""
    status: str = "pending"  # pending/running/success/failed
    project_path: str = ""
    error: str = ""
    api_calls: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BenchmarkRun":
        return cls(**d)


class BenchmarkLogger:
    """Benchmark 日志记录器。"""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.data: dict[str, Any] = {"runs": [], "created_at": self._now()}
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                pass

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add_run(self, run: BenchmarkRun) -> None:
        self.data["runs"].append(run.to_dict())
        self._save()

    def update_run(self, run: BenchmarkRun) -> None:
        for i, r in enumerate(self.data["runs"]):
            if r["model"] == run.model and r["start_time"] == run.start_time:
                self.data["runs"][i] = run.to_dict()
                break
        else:
            self.data["runs"].append(run.to_dict())
        self._save()

    def _save(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_segments(script: dict) -> list[dict]:
    """从 script.json 构建 segments 列表。"""
    audio_segments = script.get("audio_segments", [])
    goals_by_idx = {g["idx"]: g for g in script.get("goals", [])}
    segments: list[dict] = []
    for s in audio_segments:
        role = s["type"]
        if role == "intro":
            text = script.get("intro", "")
        elif role == "outro":
            text = script.get("outro", "")
        else:
            text = goals_by_idx.get(s.get("idx", -1), {}).get("text", "")
        segments.append({
            "role": role,
            "text": text,
            "audio_path": s["path"],
            "minute": s.get("minute", ""),
            "duration": _audio_duration(s["path"]),
        })
    return segments


async def _run_agent_with_model(
    prompt: str,
    model: str,
    mcp_url: str,
    api_key: str,
) -> tuple[str, int]:
    """运行 agent，返回 (report, api_calls)。"""
    from claude_agent_sdk import (
        query, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, SystemMessage,
        TextBlock, ToolUseBlock,
    )

    agent_env: dict[str, str] = {
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_BASE_URL": PROXY_BASE_URL,
    }

    options = ClaudeAgentOptions(
        mcp_servers={"palmier": {"type": "http", "url": mcp_url}},
        allowed_tools=["mcp__palmier__*"],
        permission_mode="bypassPermissions",
        model=model,
        max_turns=300,
        max_buffer_size=64 * 1024 * 1024,
        env=agent_env,
    )

    final = ""
    api_calls = 0
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            print(f"[{model}] [init] MCP ready", flush=True)
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    api_calls += 1
                    print(f"[{model}] [tool] {block.name}", flush=True)
                elif isinstance(block, TextBlock):
                    print(f"[{model}] [text] {block.text[:100]}...", flush=True)
        elif isinstance(message, ResultMessage):
            print(f"[{model}] [result] {message.subtype}", flush=True)
            final = message.result or ""

    return final, api_calls


def run_single_model(
    match_id: str,
    model: str,
    cfg: Config,
    logger: BenchmarkLogger,
) -> BenchmarkRun:
    """对单个模型运行剪辑测试。"""
    run = BenchmarkRun(model=model, match_id=match_id)
    run.start_time = _now()
    run.status = "running"
    logger.add_run(run)

    job_dir = cfg.data_dir / match_id
    match_json = job_dir / "match.json"
    script_json = job_dir / "script.json"

    try:
        with match_json.open(encoding="utf-8") as f:
            match = json.load(f)
        with script_json.open(encoding="utf-8") as f:
            script = json.load(f)

        video_path = match.get("video_path", "")
        if not video_path or not Path(video_path).exists():
            raise FileNotFoundError(f"视频不存在: {video_path}")

        # 构建 prompt
        segments = _build_segments(script)
        fps = int(cfg.get("edit", "fps", default=25))
        prompt = _build_prompt(video_path, fps, script, segments)

        # 创建独立项目目录
        safe_model_name = model.replace(".", "_").replace("-", "_")
        project_path = job_dir / f"project_144933_{safe_model_name}.palmier"
        if project_path.exists():
            shutil.rmtree(project_path)
        shutil.copytree(TEMPLATE, project_path)

        # 打开项目
        print(f"[{model}] 打开项目: {project_path}", flush=True)
        subprocess.run(["open", str(project_path)], check=True)
        time.sleep(5)

        # 运行 agent
        mcp_url = cfg.get("edit", "mcp_url", default="http://127.0.0.1:19789/mcp")
        report, api_calls = asyncio.run(_run_agent_with_model(
            prompt, model, mcp_url, PROXY_API_KEY
        ))

        run.end_time = _now()
        run.status = "success"
        run.project_path = str(project_path)
        run.api_calls = api_calls
        run.notes = f"Agent report length: {len(report)} chars"

    except Exception as e:
        run.end_time = _now()
        run.status = "failed"
        run.error = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()

    logger.update_run(run)
    return run


def run_benchmark(
    match_id: str,
    models: list[str],
    cfg: Config,
    max_workers: int = 3,
) -> list[BenchmarkRun]:
    """并行运行多个模型的 benchmark 测试。"""
    log_path = cfg.data_dir / match_id / "benchmark_log.json"
    logger = BenchmarkLogger(log_path)

    results: list[BenchmarkRun] = []

    # 使用线程池并行运行
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_single_model, match_id, model, cfg, logger): model
            for model in models
        }

        for future in as_completed(futures):
            model = futures[future]
            try:
                result = future.result()
                results.append(result)
                status_icon = "✓" if result.status == "success" else "✗"
                print(f"\n{status_icon} {model}: {result.status}", flush=True)
                if result.error:
                    print(f"   Error: {result.error}", flush=True)
            except Exception as e:
                print(f"\n✗ {model}: 异常 - {e}", flush=True)

    return results


def _main() -> None:
    ap = argparse.ArgumentParser(description="LLM Benchmark 测试框架")
    ap.add_argument("--match-id", default="144933", help="比赛 ID，默认 144933")
    ap.add_argument("--models", default="", help="指定模型列表，逗号分隔，默认全部")
    ap.add_argument("--max-workers", type=int, default=3, help="并行数，默认3")
    args = ap.parse_args()

    cfg = load_config()

    if args.models:
        models = [m.strip() for m in args.models.split(",")]
    else:
        models = DEFAULT_MODELS

    print(f"开始 Benchmark 测试")
    print(f"比赛: {args.match_id}")
    print(f"模型数: {len(models)}")
    print(f"并行数: {args.max_workers}")
    print(f"模型列表: {', '.join(models)}")
    print("-" * 50)

    results = run_benchmark(args.match_id, models, cfg, args.max_workers)

    # 汇总报告
    success = sum(1 for r in results if r.status == "success")
    failed = sum(1 for r in results if r.status == "failed")

    print("\n" + "=" * 50)
    print("Benchmark 完成")
    print(f"成功: {success}/{len(models)}")
    print(f"失败: {failed}/{len(models)}")
    print(f"日志: {cfg.data_dir / args.match_id / 'benchmark_log.json'}")
    print("=" * 50)

    for r in results:
        icon = "✓" if r.status == "success" else "✗"
        print(f"{icon} {r.model}: {r.status}")
        if r.project_path:
            print(f"   项目: {r.project_path}")
        if r.error:
            print(f"   错误: {r.error}")


if __name__ == "__main__":
    _main()
