"""阶段6 · 剪辑（edit）：Palmier MCP 全自动剪辑横屏锐评视频。

流程：
  1. 复制横屏模板副本 → data/matches/{id}/project.palmier
  2. open 打开（PalmierPro 加载成 1920x1080 / 25fps 空项目）
  3. claude-agent-sdk 驱动 Palmier MCP 全自动剪辑（导入→定位进球→裁单镜头→对齐配音→静音）
  4. osascript 触发 File→Export 导出 mp4
  5. 更新 state → EDITED

独立运行：
    python -m loopwc.stages.edit 144922
    python -m loopwc.stages.edit 144922 --video /path/to/全场回放.mp4
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = PROJECT_ROOT / "assets" / "template_horizontal.palmier"


def _audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    try:
        return round(float(out.stdout.strip()), 2)
    except ValueError:
        return 0.0


def _open_project(project_path: Path) -> None:
    subprocess.run(["open", str(project_path)], check=True)
    time.sleep(5)


def _export(output_path: Path) -> None:
    """用 osascript 触发 PalmierPro File→Export，导出到指定路径。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dir_str = str(output_path.parent)
    stem = output_path.stem

    script = f'''tell application "PalmierPro" to activate
delay 0.5
tell application "System Events"
    tell process "PalmierPro"
        click menu item "Export…" of menu "File" of menu bar 1
    end tell
end tell
delay 2
tell application "System Events"
    tell process "PalmierPro"
        keystroke "g" using {{shift down, command down}}
        delay 0.8
        keystroke "{dir_str}"
        delay 0.3
        key code 36
        delay 0.8
        keystroke "a" using command down
        delay 0.2
        keystroke "{stem}"
        delay 0.3
        key code 36
    end tell
end tell'''

    subprocess.run(["osascript", "-e", script], check=True)

    print(f"  等待导出完成：{output_path}", flush=True)
    for _ in range(360):
        if output_path.exists() and output_path.stat().st_size > 1_000_000:
            print(f"  ✓ 导出完成，大小 {output_path.stat().st_size // 1024 // 1024} MB", flush=True)
            return
        time.sleep(1)
    raise TimeoutError(f"导出超时（6分钟），文件未出现：{output_path}")


def _build_prompt(video_path: str, fps: int, script: dict, segments: list[dict]) -> str:
    teams = " vs ".join(script.get("teams", []))
    score = script.get("score", "")

    lines = [f"全场回放视频（横屏720p，自带中文解说转录）：\n{video_path}\n"]
    lines.append("配音分段（已生成，按顺序排列）：")
    for i, s in enumerate(segments):
        lines.append(
            f"  [{i}] {s['role']}，时长 {s['duration']}秒\n"
            f"      音频：{s['audio_path']}\n"
            f"      文案：{s['text']}"
        )
    material = "\n".join(lines)

    return f"""你是专业体育短视频剪辑师。把一场足球比赛（{teams}，{score}）剪成横屏锐评视频。

项目是横屏 1920x1080 / {fps}fps 空时间线。配音已分段生成，你的任务是为每段配音配上匹配的比赛画面。

## 素材
{material}

## 视频结构
时间线音频轨道(A1)从第0帧起依次排列：开头(intro) → 各进球(goal) → 结尾(outro)，首尾相接不留缝。
每段配音的正上方视频轨道(V1)放一段匹配画面。

## 步骤（按顺序执行）

1. **导入**：import_media 导入全场回放视频和全部配音文件（一次性全部导入）。

2. **排配音**：用 add_clips 把所有配音段按顺序放到音频轨 A1，首尾相接。
   - 第0段 startFrame=0
   - 第N段 startFrame = 前面所有段时长之和 × {fps}（秒×{fps}=帧）

3. **定位每个进球画面**（核心，逐进球执行）：
   - 从该进球配音文案里找"第X分钟"，以此为比赛分钟。
   - 用 inspect_media 读该分钟附近（±3分钟）的解说转录，找解说员喊进球的精确秒数。
   - 用 inspect_media 在该秒附近由粗到细多轮抽帧（先±20秒每2秒1帧看全貌，再缩小到关键段提高密度），找出一段**连续单镜头**实时进球画面（球员带球→射门→进球入网，导播切特写/庆祝前结束）。
   - **必须避开**：慢动作回放段、导播切换后的特写/观众/庆祝镜头。只要一个固定机位连续的实时进攻进球过程。
   - 画面时长尽量贴近配音时长（不足则从进攻发起更早处开始取；过长则取射门前后核心段）。
   - 用 add_clips 把这段画面放到 V1 轨道，对齐对应配音段的起始帧（startFrame = 该配音段的 startFrame）。

4. **开头画面**：在全场回放里找球员庆祝/拥抱/入场特写镜头（单镜头连续），放到 intro 配音段正上方。

5. **结尾画面**：找本场最精彩的庆祝或比赛结束后镜头（单镜头连续），放到 outro 配音段正上方。

6. **静音原声**：所有 V1 视频 clip 用 set_clip_properties 设 volume=0（只保留配音声音）。

## 硬约束
- 每段配音对应的视频**只能是一个连续镜头、一个固定视角**，绝不允许中途切换视角/机位/慢动作。
- 全程只用全场回放里的真实画面，不要用 generate_video/generate_image。
- 不要添加标题文字、字幕、特效。

完成后用 get_timeline 确认时间线结构，报告：配音总时长、V1 视频数量是否等于配音段数、每段对应画面的源视频时间段（秒）。"""


async def _run_agent(prompt: str, model: str, mcp_url: str, api_key: str = "") -> str:
    from claude_agent_sdk import (
        query, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, SystemMessage,
        TextBlock, ToolUseBlock,
    )

    agent_env: dict[str, str] = {}
    if api_key:
        agent_env["ANTHROPIC_API_KEY"] = api_key
        agent_env["ANTHROPIC_BASE_URL"] = "https://api.anthropic.com"

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
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            print(f"[init] MCP: {message.data.get('mcp_servers')}", flush=True)
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[tool] {block.name}  {str(block.input)[:120]}", flush=True)
                elif isinstance(block, TextBlock):
                    print(f"[text] {block.text}", flush=True)
        elif isinstance(message, ResultMessage):
            print(f"[result] {message.subtype}", flush=True)
            final = message.result or ""
    return final


def run(match_id: str, cfg: Config, video_override: str = "", dry_run: bool = False) -> dict[str, Any]:
    """全自动剪辑，返回 {project_path, output_path, agent_report}。"""
    job_dir = cfg.data_dir / match_id
    match_json = job_dir / "match.json"
    script_json = job_dir / "script.json"

    with match_json.open(encoding="utf-8") as f:
        match = json.load(f)
    with script_json.open(encoding="utf-8") as f:
        script = json.load(f)

    video_path = video_override or match.get("video_path", "")
    if not video_path or not Path(video_path).exists():
        raise FileNotFoundError(
            f"视频不存在，请先下载并用 --video 指定路径：{video_path!r}\n"
            f"示例：python -m loopwc.stages.edit {match_id} --video /path/to/video.mp4"
        )

    if not TEMPLATE.exists():
        raise FileNotFoundError(f"横屏模板不存在：{TEMPLATE}")

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

    fps = int(cfg.get("edit", "fps", default=25))
    model = cfg.get("edit", "model", default="claude-sonnet-4-6")
    api_key = cfg.get("edit", "api_key", default="")
    mcp_url = cfg.get("edit", "mcp_url", default="http://127.0.0.1:19789/mcp")

    prompt = _build_prompt(video_path, fps, script, segments)

    if dry_run:
        print(prompt)
        return {"prompt": prompt}

    # 1. 复制横屏模板副本
    project_path = job_dir / "project.palmier"
    if project_path.exists():
        shutil.rmtree(project_path)
    shutil.copytree(TEMPLATE, project_path)

    # 2. 打开项目
    _open_project(project_path)

    # 3. 运行 agent 剪辑
    report = asyncio.run(_run_agent(prompt, model, mcp_url, api_key))

    # 4. 导出由独立 export 阶段处理（ffmpeg 合成），不在此触发

    # 5. 更新状态
    job = load_job(cfg.data_dir, match_id)
    if not job:
        job = Job(match_id=match_id)
    job.set_status(Status.EDITED)
    save_job(cfg.data_dir, job)

    return {
        "project_path": str(project_path),
        "agent_report": report,
    }


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Palmier 全自动剪辑")
    ap.add_argument("match_id", help="比赛 ID，如 144922")
    ap.add_argument("--video", default="", help="视频路径（默认读 match.json.video_path）")
    ap.add_argument("--dry-run", action="store_true", help="只构建并打印剪辑指令，不实际剪辑")
    args = ap.parse_args()

    cfg = load_config()
    result = run(args.match_id, cfg, video_override=args.video, dry_run=args.dry_run)
    if args.dry_run:
        return
    print(f"\n✓ 剪辑完成")
    print(f"  项目：{result['project_path']}")
    print(f"  成片：{result['output_path']}")
    print(f"\n=== Agent 报告 ===\n{result['agent_report']}")


if __name__ == "__main__":
    _main()
