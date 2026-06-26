"""阶段4 · TTS 配音（tts）：script.txt → audio.mp3。

使用火山引擎豆包语音 V3 WebSocket 双向流式接口（二进制协议）。

独立运行：
    python -m loopwc.stages.tts 144922
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job

_WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"


async def _synthesize(
    text: str,
    api_key: str,
    speaker: str,
    resource_id: str,
    model: str,
    fmt: str,
    sample_rate: int,
    output_path: Path,
) -> None:
    import websockets
    from volcengine_audio import (
        VolcengineTTSFunctions as F,
        EventReceive,
        MessageType,
    )

    session_id = str(uuid.uuid4())
    audio_chunks: list[bytes] = []

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
    }

    req_params = {
        "model": model,
        "speaker": speaker,
        "audio_params": {
            "format": fmt,
            "sample_rate": sample_rate,
        },
    }

    async with websockets.connect(_WS_URL, additional_headers=headers) as ws:
        await ws.send(F.start_connection_payload())
        await ws.send(F.start_session_payload(session_id, req_params))
        await ws.send(F.task_request_payload(session_id, text, speaker, req_params["audio_params"]))
        await ws.send(F.finish_session_payload(session_id))

        try:
            async for raw in ws:
                if not isinstance(raw, bytes):
                    continue
                try:
                    event, sid, payload = F.extract_response_payload(raw)
                except Exception:
                    continue

                if event == EventReceive.TTSResponse:
                    if isinstance(payload, bytes):
                        audio_chunks.append(payload)
                elif event in (EventReceive.SessionFinished, EventReceive.ConnectionFinished, EventReceive.TTSEnded):
                    break
                elif isinstance(event, EventReceive) and event.value >= 45000000:
                    raise RuntimeError(f"TTS 失败: {event.name} payload={payload}")
        except Exception as e:
            if not audio_chunks:
                raise RuntimeError(f"TTS 异常且无音频数据: {e}") from e

        try:
            await ws.send(F.finish_connection_payload())
        except Exception:
            pass

    if not audio_chunks:
        raise RuntimeError("TTS 未返回音频数据，请检查 API Key / speaker ID")

    output_path.write_bytes(b"".join(audio_chunks))


def run(match_id: str, cfg: Config) -> dict[str, Any]:
    """分段合成配音，落盘 audio_intro.mp3 / audio_goal_N.mp3 / audio_outro.mp3，更新 state。"""
    job_dir = cfg.data_dir / match_id
    script_path = job_dir / "script.json"

    if not script_path.exists():
        raise FileNotFoundError(f"script.json 不存在，请先 script {match_id}")

    with script_path.open("r", encoding="utf-8") as f:
        script = json.load(f)

    api_key     = cfg.get("tts", "api_key", default="")
    speaker     = cfg.get("tts", "speaker", default="")
    resource_id = cfg.get("tts", "resource_id", default="seed-tts-2.0")
    model       = cfg.get("tts", "model", default="seed-tts-2.0-standard")
    fmt         = cfg.get("tts", "format", default="mp3")
    sample_rate = int(cfg.get("tts", "sample_rate", default=24000))

    if not api_key or not speaker:
        raise ValueError("config.yaml 缺少 tts.api_key 或 tts.speaker")

    segments: list[dict] = []

    # 开头
    intro_path = job_dir / "audio_intro.mp3"
    asyncio.run(_synthesize(script["intro"], api_key, speaker, resource_id, model, fmt, sample_rate, intro_path))
    segments.append({"type": "intro", "path": str(intro_path)})
    print(f"  ✓ intro ({intro_path.stat().st_size // 1024} KB)")

    # 每个进球
    for g in script.get("goals", []):
        if not g.get("text"):
            continue
        out = job_dir / f"audio_goal_{g['idx']}.mp3"
        asyncio.run(_synthesize(g["text"], api_key, speaker, resource_id, model, fmt, sample_rate, out))
        segments.append({"type": "goal", "idx": g["idx"], "minute": g.get("minute", ""), "path": str(out)})
        print(f"  ✓ goal_{g['idx']} ({out.stat().st_size // 1024} KB)")

    # 结尾
    outro_path = job_dir / "audio_outro.mp3"
    asyncio.run(_synthesize(script["outro"], api_key, speaker, resource_id, model, fmt, sample_rate, outro_path))
    segments.append({"type": "outro", "path": str(outro_path)})
    print(f"  ✓ outro ({outro_path.stat().st_size // 1024} KB)")

    # 把段落路径写回 script.json
    script["audio_segments"] = segments
    with script_path.open("w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    job = load_job(cfg.data_dir, match_id)
    if not job:
        job = Job(match_id=match_id)
    job.set_status(Status.TTS_DONE)
    save_job(cfg.data_dir, job)

    total_kb = sum(Path(s["path"]).stat().st_size for s in segments) // 1024
    return {"segments": segments, "total_kb": total_kb}


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="分段合成配音")
    ap.add_argument("match_id", help="比赛 ID，如 144922")
    args = ap.parse_args()

    cfg = load_config()
    print(f"开始分段合成 {args.match_id}...")
    result = run(args.match_id, cfg)
    print(f"✓ 共 {len(result['segments'])} 段，总计 {result['total_kb']} KB")


if __name__ == "__main__":
    _main()

