"""阶段6 · 剪辑（edit）：Palmier MCP 全自动剪辑锐评视频。

流程：
  1. 根据配置选择横/竖屏模板副本 → data/matches/{id}/project.palmier
  2. open 打开（PalmierPro 加载成空项目）
  3. claude-agent-sdk 驱动 Palmier MCP 全自动剪辑（导入→定位进球→裁单镜头→对齐配音→静音）
  4. 导出由独立 export 阶段处理
  5. 更新 state → EDITED

独立运行：
    python -m loopwc.stages.edit 144922
    python -m loopwc.stages.edit 144922 --video /path/to/全场回放.mp4
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _template_for_aspect(aspect: str) -> Path:
    a = aspect.lower().replace(":", "/").replace("\\", "")
    if a in ("9/16", "9:16", "vertical", "portrait"):
        return PROJECT_ROOT / "assets" / "template_vertical.palmier"
    return PROJECT_ROOT / "assets" / "template_horizontal.palmier"


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


def _build_prompt(
    video_path: str,
    fps: int,
    script: dict,
    segments: list[dict],
    goals: list[dict],
    aspect: str = "16:9",
    width: int = 1920,
    height: int = 1080,
) -> str:
    teams = " vs ".join(script.get("teams", []))
    score = script.get("score", "")

    source_desc = "横屏720p" if aspect == "16:9" else "横屏720p（最终输出会居中裁剪并轻微放大以遮住台标水印）"
    canvas_desc = f"{width}x{height}"
    target_desc = "横屏" if aspect == "16:9" else "竖屏"

    lines = [f"全场回放视频（{source_desc}，自带中文解说转录）：\n{video_path}\n"]
    lines.append("配音分段（已生成，按顺序排列）：")
    for i, s in enumerate(segments):
        lines.append(
            f"  [{i}] {s['role']}，时长 {s['duration']}秒\n"
            f"      音频：{s['audio_path']}\n"
            f"      文案：{s['text']}"
        )
    material = "\n".join(lines)

    goals_info = ""
    if goals:
        goals_info = "\n".join(
            f"  进球{i+1}：第{g.get('minute', '?')}分钟，{g.get('player', '未知球员')} — {g.get('desc', '')}"
            for i, g in enumerate(goals)
        )
        goals_info = "## 进球参考信息（来自官方战报）\n" + goals_info + "\n"

    vertical_note = ""
    if aspect == "9:16":
        vertical_note = "\n- 竖屏输出会裁剪画面中央区域并轻微放大，挑选镜头时优先保证球员和球都在画面中心附近，避免主体被裁掉。"

    return f"""你是专业体育短视频剪辑师。把一场足球比赛（{teams}，{score}）剪成{target_desc}锐评视频。

项目是{target_desc} {canvas_desc} / {fps}fps 空时间线。配音已分段生成，你的任务是为每段配音配上匹配的比赛画面。

## 素材
{material}

{goals_info}## 视频结构
时间线音频轨道(A1)从第0帧起依次排列：开头(intro) → 各进球(goal) → 结尾(outro)，首尾相接不留缝。
每段配音的正上方视频轨道(V1)放一段匹配画面。

## 步骤（按顺序执行）

1. **导入**：import_media 导入全场回放视频和全部配音文件（一次性全部导入）。

2. **排配音**：用 add_clips 把所有配音段按顺序放到音频轨 A1，首尾相接。
   - 第0段 startFrame=0
   - 第N段 startFrame = 前面所有段时长之和 × {fps}（秒×{fps}=帧）

3. **定位每个进球/事件画面**（核心，逐段执行）：
   - 对于 `goal` 段：从配音文案里找"第X分钟"，以此为比赛分钟；同时参考上面的"进球参考信息"。
   - 对于 `event` 段（如点球被扑、红牌、神级扑救等关键非进球事件）：从配音文案中提取事件类型和分钟，结合该事件在战报中的描述定位。
   - **先文本定位**：用 inspect_media 读该分钟附近（±2分钟）的 `wordTimestamps` 解说转录，不要返回图片（maxFrames=0 或最小帧数），找到解说员喊出关键事件（进球/点球/红牌/扑救）的精确秒数。
   - **再小范围抽帧确认**：在该秒数附近 ±5 秒用 inspect_media 抽 **2-3 帧**（maxFrames=2 或 3），确认是否为一个连续单镜头实时事件画面。若看不清，再缩小到 ±2 秒抽 2-3 帧，逐步精确。
   - **关键校验**：确认找到的源视频时间（秒数）与比赛第X分钟（X×60 + 约10分钟赛前偏移）偏差应在 5 分钟以内。如果偏差过大，说明转录时间戳或理解有误，必须重新定位。
   - **事件确认**：
     - 进球段：最终画面必须包含射门→球入网或进球后球员立刻庆祝的瞬间，不能只拍到普通进攻推进。
     - 事件段（点球被扑/红牌/神扑）：最终画面必须包含核心事件瞬间，如罚球+扑救、犯规+红牌出示、射门+扑救，不能只拍到普通攻防。
   - **余量要求（重要）**：画面要比核心事件句本身更长。当配音说到"...进球/扑出/罚下"时，画面里事件应该正在发生或刚刚完成；当配音继续说到后续结果/反应时，画面仍应保持同一视角，显示球员庆祝、裁判示意或观众反应，**不能切走或结束**。
   - **绝对禁止**：单次 inspect_media 返回大量帧（maxFrames 不得超过 3），或一次性大范围高密度抽帧。
   - 目标画面：一个固定机位连续的实时事件过程，包含核心动作和后续 1-3 秒余量。**必须避开**：慢动作回放段、导播切换后的特写/观众镜头。
   - 画面时长尽量贴近配音时长；若配音比画面事件长，从事件发起更早处开始取；若配音较短，也要保证核心事件+后续反应余量完整，宁可画面略长于配音。
   - 用 add_clips 把这段画面放到 V1 轨道，对齐对应配音段的起始帧（startFrame = 该配音段的 startFrame）。{vertical_note}

4. **开头(intro)画面**：用 inspect_media 读取开场前/入场/奏国歌时段的 `wordTimestamps` 定位，再抽 2-3 帧确认**球员激情庆祝、拥抱、围圈动员或入场激情互动**的连续镜头（单镜头连续），优先避开平淡的球场空镜/列队走场。放到 intro 配音段正上方。

5. **结尾画面**：同样先读 wordTimestamps 定位比赛结束前后，再小范围抽 2-3 帧找**进球后最精彩庆祝、球员拥抱、教练席激情反应**的连续镜头（单镜头连续），放到 outro 配音段正上方。若不存在长连续庆祝，取最长的一段庆祝镜头；仍不足时，才用终场前最后一段连续攻防兜底。

6. **静音原声**：所有 V1 视频 clip 用 set_clip_properties 设 volume=0（只保留配音声音）。

## 硬约束
- 每段配音对应的视频**只能是一个连续镜头、一个固定视角**，绝不允许中途切换视角/机位/慢动作。
- 全程只用全场回放里的真实画面，不要用 generate_video/generate_image。
- 不要添加标题文字、字幕、特效。

完成后用 get_timeline 确认时间线结构，报告：配音总时长、V1 视频数量是否等于配音段数、每段对应画面的源视频时间段（秒）。"""


async def _run_agent(prompt: str, model: str, mcp_url: str, api_key: str = "", base_url: str = "https://api.anthropic.com") -> str:
    from claude_agent_sdk import (
        query, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, SystemMessage,
        TextBlock, ToolUseBlock,
    )

    agent_env = os.environ.copy()
    # 清除 Claude Code harness 注入的代理环境变量，确保走配置里的中转站/官方 API
    agent_env.pop("ANTHROPIC_AUTH_TOKEN", None)
    agent_env.pop("ANTHROPIC_BASE_URL", None)
    if api_key:
        agent_env["ANTHROPIC_API_KEY"] = api_key
        agent_env["ANTHROPIC_BASE_URL"] = base_url

    options = ClaudeAgentOptions(
        mcp_servers={"palmier": {"type": "http", "url": mcp_url}},
        allowed_tools=["mcp__palmier__*"],
        permission_mode="bypassPermissions",
        model=model,
        max_turns=300,
        max_buffer_size=64 * 1024 * 1024,
        env=agent_env,
        extra_args={"bare": None},
        setting_sources=[],
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
    """全自动剪辑，返回 {project_path, agent_report}。"""
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

    aspect = cfg.get("edit", "aspect", default="16:9")
    template = _template_for_aspect(aspect)
    if not template.exists():
        raise FileNotFoundError(f"模板不存在：{template}")

    segments: list[dict] = []
    for seg in script.get("segments", []):
        audio_path = seg.get("audio_path", "")
        if not audio_path or not Path(audio_path).exists():
            raise FileNotFoundError(
                f"segment {seg['type']}/{seg.get('idx', 0)} 音频不存在：{audio_path}\n"
                f"请先运行 python -m loopwc.stages.tts {match_id}"
            )
        segments.append({
            "role": seg["type"],
            "text": seg.get("script_sc", ""),
            "audio_path": audio_path,
            "minute": seg.get("minute", ""),
            "duration": seg.get("duration") or _audio_duration(audio_path),
        })

    if not segments:
        raise ValueError(f"script.json 中无有效 segments，请检查 script/tts 阶段")

    fps = int(cfg.get("edit", "fps", default=25))
    model = cfg.get("edit", "model", default="claude-sonnet-4-6")
    api_key = cfg.get("edit", "api_key", default="")
    base_url = cfg.get("edit", "base_url", default="https://api.anthropic.com")
    mcp_url = cfg.get("edit", "mcp_url", default="http://127.0.0.1:19789/mcp")

    width, height = (1920, 1080) if aspect == "16:9" else (1080, 1920)
    goals = match.get("goals", [])
    prompt = _build_prompt(video_path, fps, script, segments, goals, aspect=aspect, width=width, height=height)

    if dry_run:
        print(prompt)
        return {"prompt": prompt}

    # 1. 复制模板副本
    project_path = job_dir / "project.palmier"
    if project_path.exists():
        shutil.rmtree(project_path)
    shutil.copytree(template, project_path)

    # 2. 打开项目
    _open_project(project_path)

    # 3. 运行 agent 剪辑
    report = asyncio.run(_run_agent(prompt, model, mcp_url, api_key, base_url))

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
    print(f"\n=== Agent 报告 ===\n{result['agent_report']}")


if __name__ == "__main__":
    _main()
