"""阶段7 · 导出（export）：从 Palmier 时间线用 ffmpeg 合成导出。

优先从 MCP 实时读取（解决 project.palmier 文件路径不对的问题），
降级从 project.palmier/project.json 文件解析。

独立运行：
    python -m loopwc.stages.export 144922
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job
from ..text_overlay import render_hook_png, render_subtitle_png, save_png

HOME = Path.home()


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return HOME / p


def _timeline_from_mcp(mcp_url: str) -> dict[str, Any] | None:
    """从 Palmier MCP 实时读取当前时间线数据。返回 None 表示失败。"""
    def mcp_call(name: str, args: dict = {}) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": args}}
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", mcp_url,
             "-H", "Content-Type: application/json",
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=30,
        )
        r = json.loads(result.stdout)
        return json.loads(r["result"]["content"][0]["text"])

    try:
        tl = mcp_call("get_timeline")
        media_list = mcp_call("get_media")
    except Exception as e:
        print(f"  MCP 读取失败: {e}", flush=True)
        return None

    fps = tl.get("fps", 25)

    # 建立 mediaRef(shortId) → 文件路径 映射
    media_map: dict[str, Path] = {}
    for entry in media_list.get("entries", []):
        uid = entry["id"]
        short_id = uid.split("-")[0]
        src = entry.get("source", {})
        raw_path = (
            src.get("external", {}).get("absolutePath", "")
            or src.get("project", {}).get("path", "")
        )
        if raw_path:
            p = _resolve_path(raw_path)
            media_map[short_id] = p
            media_map[uid] = p

    tracks = tl.get("tracks", [])
    video_tracks = [t for t in tracks if t["type"] == "video"]
    audio_tracks = [t for t in tracks if t["type"] == "audio"]

    # 主视频轨：含全场回放 clip（trimStartFrame > 0）的轨道，取 clip 最多的
    main_video_track = max(
        [t for t in video_tracks if any(c.get("trimStartFrame", 0) > 0 for c in t.get("clips", []))],
        key=lambda t: len(t.get("clips", [])),
        default=video_tracks[-1] if video_tracks else {"clips": []},
    )

    # 主配音轨：第一个 audio 轨
    main_audio_track = audio_tracks[0] if audio_tracks else {"clips": []}

    def parse(track: dict) -> list[dict]:
        result = []
        for c in track.get("clips", []):
            mid = c.get("mediaRef", "")
            path = media_map.get(mid)
            if not path:
                continue
            result.append({
                "path": path,
                "ss": round(c.get("trimStartFrame", 0) / fps, 4),
                "duration": round(c.get("durationFrames", 0) / fps, 4),
                "volume": c.get("volume", 1.0),
            })
        return result

    return {
        "fps": fps,
        "video_clips": parse(main_video_track),
        "audio_clips": parse(main_audio_track),
    }


def _timeline_from_file(project_path: Path) -> dict[str, Any]:
    """从 project.palmier/project.json 解析时间线（降级方案）。"""
    pj = json.loads((project_path / "project.json").read_text())
    mj = json.loads((project_path / "media.json").read_text())

    fps = pj.get("fps", 25)

    media_map: dict[str, Path] = {}
    for entry in mj.get("entries", []):
        uid = entry["id"]
        short_id = uid.split("-")[0]
        src = entry.get("source", {})
        raw_path = (
            src.get("external", {}).get("absolutePath", "")
            or src.get("project", {}).get("path", "")
        )
        if raw_path:
            p = _resolve_path(raw_path)
            media_map[short_id] = p
            media_map[uid] = p

    video_tracks = [t for t in pj.get("tracks", []) if t["type"] == "video"]
    audio_tracks = [t for t in pj.get("tracks", []) if t["type"] == "audio"]

    main_video_track = max(
        [t for t in video_tracks if any(c.get("trimStartFrame", 0) > 0 for c in t.get("clips", []))],
        key=lambda t: len(t.get("clips", [])),
        default=video_tracks[-1] if video_tracks else {"clips": []},
    )
    main_audio_track = audio_tracks[0] if audio_tracks else {"clips": []}

    def parse(track: dict) -> list[dict]:
        result = []
        for c in track.get("clips", []):
            mid = c.get("mediaRef", "")
            path = media_map.get(mid)
            if not path:
                continue
            result.append({
                "path": path,
                "ss": round(c.get("trimStartFrame", 0) / fps, 4),
                "duration": round(c.get("durationFrames", 0) / fps, 4),
                "volume": c.get("volume", 1.0),
            })
        return result

    return {
        "fps": fps,
        "video_clips": parse(main_video_track),
        "audio_clips": parse(main_audio_track),
    }


def _target_size(aspect: str) -> tuple[int, int]:
    a = aspect.lower().replace(":", "/").replace("\\", "")
    if a in ("9/16", "9:16", "vertical", "portrait"):
        return 1080, 1920
    return 1920, 1080


def _build_video_filter(width: int, height: int, fps: int, zoom: float) -> str:
    """为每个片段构造 ffmpeg video filter。

    竖屏：源视频按画布宽度轻微放大，居中裁剪出 16:9 区域，再上下 pad 到 9:16。
    横屏：直接缩放到目标宽度，保持比例。
    """
    if height > width:  # 竖屏
        # 16:9 内容区高度（偶数）
        video_height = int(round(width * 9 / 16 / 2) * 2)
        scaled_w = max(int(round(width * zoom / 2) * 2), width)
        return (
            f"scale={scaled_w}:-2:flags=lanczos,"
            f"crop={width}:{video_height}:((in_w-{width})/2):((in_h-{video_height})/2),"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1:1,fps={fps}"
        )
    # 横屏：保持原比例缩放到目标宽度
    return f"scale={width}:-2:flags=lanczos,setsar=1:1,fps={fps}"


def _prepare_overlay_pngs(
    tmp: Path,
    script: dict,
    cfg: Config,
    width: int,
    audio_clips: list[dict],
) -> tuple[Path | None, list[tuple[Path, float, float]]]:
    """生成 hook 和字幕 PNG，返回 (hook_png, [(sub_png, start, end), ...])。"""
    hook_png: Path | None = None
    subtitle_items: list[tuple[Path, float, float]] = []

    if cfg.get("edit", "hook", default=True):
        hook_text = script.get("hook", "").strip()
        if hook_text:
            img = render_hook_png(hook_text, cfg, width)
            hook_png = save_png(img, tmp / "hook.png")

    if cfg.get("edit", "subtitle", default=True):
        segments = script.get("segments", [])
        if len(segments) != len(audio_clips):
            print(f"  警告: segments 数量 ({len(segments)}) 与 audio_clips 数量 ({len(audio_clips)}) 不匹配，字幕可能错位", flush=True)

        current_time = 0.0
        for i, seg in enumerate(segments):
            if i >= len(audio_clips):
                break
            duration = audio_clips[i]["duration"]
            subtitles = seg.get("subtitles_tc", [])
            if not subtitles:
                current_time += duration
                continue

            # 优先使用 LLM 给出的每句时长，否则平均分配
            raw_durations = seg.get("subtitle_durations", [])
            if len(raw_durations) != len(subtitles) or sum(raw_durations) <= 0:
                sub_duration = duration / len(subtitles)
                durations = [sub_duration] * len(subtitles)
            else:
                total_est = sum(raw_durations)
                # 缩放到实际音频时长
                scale = duration / total_est
                durations = [d * scale for d in raw_durations]

            seg_start = current_time
            for j, sub_text in enumerate(subtitles):
                if not sub_text.strip():
                    continue
                img = render_subtitle_png(sub_text.strip(), cfg, width)
                png_path = save_png(img, tmp / f"sub_{i:02d}_{j:02d}.png")
                start = seg_start + sum(durations[:j])
                end = start + durations[j]
                subtitle_items.append((png_path, start, end))
            current_time += duration

    return hook_png, subtitle_items


def _build_overlay_filter(
    width: int,
    height: int,
    hook_png: Path | None,
    hook_duration: float,
    subtitle_items: list[tuple[Path, float, float]],
    cfg: Config,
) -> str:
    """构造 overlay filter_complex 字符串。

    返回形如：
    [base][hook]overlay=...[v1];[v1][sub00]overlay=...[v2];...
    """
    parts: list[str] = []
    stream = "[base]"

    # 竖屏时视频内容区在画布中垂直居中
    if height > width:
        video_height = int(round(width * 9 / 16 / 2) * 2)
        video_top = (height - video_height) // 2
        video_bottom = video_top + video_height
    else:
        video_top = 0
        video_bottom = height

    hook_bottom_margin = int(cfg.get("edit", "hook_bottom_margin", default=30))
    subtitle_bottom_margin = int(cfg.get("edit", "subtitle_bottom_margin", default=60))

    if hook_png:
        # Hook 放在顶部黑幕区域，底部紧贴视频内容区上沿
        from PIL import Image as _Image
        hook_h = _Image.open(hook_png).height
        hook_y = video_top - hook_h - hook_bottom_margin
        parts.append(
            f"{stream}[hook]overlay=(W-w)/2:{hook_y}:enable='between(t\\,0\\,{hook_duration})'[v_hook]"
        )
        stream = "[v_hook]"

    for idx, (png_path, start, end) in enumerate(subtitle_items):
        # 字幕贴在视频内容底部，减去自身高度和边距
        sub_y = f"{video_bottom - subtitle_bottom_margin}-h"
        next_stream = f"[v_sub_{idx}]"
        parts.append(
            f"{stream}[sub_{idx}]overlay=(W-w)/2:{sub_y}:enable='between(t\\,{start:.3f}\\,{end:.3f})'{next_stream}"
        )
        stream = next_stream

    if not parts:
        return ""
    return ";".join(parts)


def _ffmpeg_concat(
    tl: dict[str, Any],
    output_path: Path,
    script: dict,
    cfg: Config,
    aspect: str = "16:9",
    zoom: float = 1.0,
) -> None:
    video_clips = tl["video_clips"]
    audio_clips = tl["audio_clips"]
    fps = tl["fps"]

    if not video_clips:
        raise ValueError("时间线无视频片段，无法导出")
    if not audio_clips:
        raise ValueError("时间线无配音片段，无法导出")

    width, height = _target_size(aspect)
    vf = _build_video_filter(width, height, fps, zoom)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        video_parts = []
        for i, clip in enumerate(video_clips):
            out = tmp / f"v{i:02d}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["ss"]),
                "-t", str(clip["duration"]),
                "-i", str(clip["path"]),
                "-an",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-vf", vf,
                str(out),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            video_parts.append(out)
            print(f"  [v{i}] {clip['ss']:.1f}s+{clip['duration']:.1f}s → {out.name}", flush=True)

        audio_parts = []
        for i, clip in enumerate(audio_clips):
            out = tmp / f"a{i:02d}.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(clip["path"]), "-c:a", "copy", str(out)],
                check=True, capture_output=True,
            )
            audio_parts.append(out)

        vlist = tmp / "vlist.txt"
        vlist.write_text("\n".join(f"file '{p}'" for p in video_parts))
        vcombined = tmp / "video_combined.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(vlist), "-c:v", "copy", str(vcombined),
        ], check=True, capture_output=True)

        alist = tmp / "alist.txt"
        alist.write_text("\n".join(f"file '{p}'" for p in audio_parts))
        acombined = tmp / "audio_combined.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(alist), "-c:a", "copy", str(acombined),
        ], check=True, capture_output=True)

        # 生成 overlay PNG
        hook_png, subtitle_items = _prepare_overlay_pngs(tmp, script, cfg, width, audio_clips)
        hook_duration = float(cfg.get("edit", "hook_duration", default=3.0))
        overlay_filter = _build_overlay_filter(width, height, hook_png, hook_duration, subtitle_items, cfg)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if overlay_filter:
            # 需要 overlay，构造完整 filter_complex
            inputs = ["-i", str(vcombined)]
            if hook_png:
                inputs += ["-i", str(hook_png)]
                hook_label = "[hook]"
            else:
                hook_label = ""

            for idx, (png_path, _, _) in enumerate(subtitle_items):
                inputs += ["-i", str(png_path)]

            filter_complex = overlay_filter.replace("[base]", "[0:v]").replace("[hook]", "[1:v]")
            # 替换字幕输入标签
            sub_idx = 2 if hook_png else 1
            for idx, _ in enumerate(subtitle_items):
                filter_complex = filter_complex.replace(f"[sub_{idx}]", f"[{sub_idx + idx}:v]")

            cmd = [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-i", str(acombined),
                "-map", "[v_sub_{}]".format(len(subtitle_items) - 1) if subtitle_items else "[v_hook]",
                "-map", str(len(inputs) // 2) + ":a",  # 音频输入索引
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-movflags", "+faststart",
                str(output_path),
            ]
        else:
            # 无 overlay，直接 mux
            cmd = [
                "ffmpeg", "-y",
                "-i", str(vcombined), "-i", str(acombined),
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-movflags", "+faststart",
                str(output_path),
            ]

        subprocess.run(cmd, check=True, capture_output=True)

    size_mb = output_path.stat().st_size // 1024 // 1024
    print(f"  ✓ 导出完成：{output_path}  ({size_mb} MB)", flush=True)


def run(match_id: str, cfg: Config) -> dict[str, Any]:
    """导出成片。优先从 MCP 读时间线，降级读 project.json 文件。"""
    job_dir = cfg.data_dir / match_id
    mcp_url = cfg.get("edit", "mcp_url", default="http://127.0.0.1:19789/mcp")

    script_path = job_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script.json 不存在：{script_path}")
    with script_path.open("r", encoding="utf-8") as f:
        script = json.load(f)

    # 优先从 MCP 读（实时数据，不受文件路径影响）
    print("  从 Palmier MCP 读取时间线...", flush=True)
    tl = _timeline_from_mcp(mcp_url)

    if not tl or len(tl["video_clips"]) == 0:
        # 降级：从 project.json 文件读
        project_path = job_dir / "project.palmier"
        if not project_path.exists():
            raise FileNotFoundError(f"MCP 读取失败且 project.palmier 不存在：{project_path}")
        print("  MCP 数据为空，降级读取 project.json...", flush=True)
        tl = _timeline_from_file(project_path)

    v_count = len(tl["video_clips"])
    a_count = len(tl["audio_clips"])
    print(f"  视频片段：{v_count}，配音片段：{a_count}", flush=True)

    if v_count == 0 or a_count == 0:
        raise ValueError(f"时间线内容不完整（视频={v_count} 配音={a_count}），请检查 edit 阶段")

    aspect = cfg.get("edit", "aspect", default="16:9")
    zoom = float(cfg.get("edit", "zoom", default=1.0))

    output_path = job_dir / f"{match_id}_final.mp4"
    print(f"  开始 ffmpeg 合成（画幅 {aspect}, zoom={zoom}，预计 1-3 分钟）...", flush=True)
    _ffmpeg_concat(tl, output_path, script, cfg, aspect=aspect, zoom=zoom)

    job = load_job(cfg.data_dir, match_id)
    if not job:
        job = Job(match_id=match_id)
    job.set_status(Status.EXPORTED)
    save_job(cfg.data_dir, job)

    return {"output_path": str(output_path)}


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="从 Palmier 时间线用 ffmpeg 合成导出")
    ap.add_argument("match_id", help="比赛 ID，如 144922")
    args = ap.parse_args()

    cfg = load_config()
    result = run(args.match_id, cfg)
    print(f"\n✓ 成片：{result['output_path']}")


if __name__ == "__main__":
    _main()
