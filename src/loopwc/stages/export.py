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


def _ffmpeg_concat(tl: dict[str, Any], output_path: Path) -> None:
    video_clips = tl["video_clips"]
    audio_clips = tl["audio_clips"]
    fps = tl["fps"]

    if not video_clips:
        raise ValueError("时间线无视频片段，无法导出")
    if not audio_clips:
        raise ValueError("时间线无配音片段，无法导出")

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
                "-vf", f"fps={fps}",
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

        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(vcombined), "-i", str(acombined),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(output_path),
        ], check=True, capture_output=True)

    size_mb = output_path.stat().st_size // 1024 // 1024
    print(f"  ✓ 导出完成：{output_path}  ({size_mb} MB)", flush=True)


def run(match_id: str, cfg: Config) -> dict[str, Any]:
    """导出成片。优先从 MCP 读时间线，降级读 project.json 文件。"""
    job_dir = cfg.data_dir / match_id
    mcp_url = cfg.get("edit", "mcp_url", default="http://127.0.0.1:19789/mcp")

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

    output_path = job_dir / f"{match_id}_final.mp4"
    print("  开始 ffmpeg 合成（预计 1-3 分钟）...", flush=True)
    _ffmpeg_concat(tl, output_path)

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
