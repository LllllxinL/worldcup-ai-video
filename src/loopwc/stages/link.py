"""手动关联本地视频文件到 match，state 推进到 DOWNLOADED。

用法：
    python -m loopwc.stages.link 144922 /Users/xxx/Downloads/全场回放.mp4
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job


def run(match_id: str, video_path: str, cfg: Config) -> None:
    vp = Path(video_path).expanduser().resolve()
    if not vp.exists():
        raise FileNotFoundError(f"视频文件不存在: {vp}")

    match_json_path = cfg.data_dir / match_id / "match.json"
    if not match_json_path.exists():
        raise FileNotFoundError(f"match.json 不存在，请先 scrape {match_id}")

    with match_json_path.open("r", encoding="utf-8") as f:
        match = json.load(f)

    match["video_path"] = str(vp)

    with match_json_path.open("w", encoding="utf-8") as f:
        json.dump(match, f, ensure_ascii=False, indent=2)

    job = load_job(cfg.data_dir, match_id)
    if not job:
        job = Job(match_id=match_id)
    job.set_status(Status.DOWNLOADED, note=f"手动关联: {vp.name}")
    save_job(cfg.data_dir, job)

    print(f"✓ {match_id} 视频已关联: {vp}")
    print(f"  state → DOWNLOADED")


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="关联本地视频文件到 match")
    ap.add_argument("match_id", help="比赛 ID，如 144922")
    ap.add_argument("video_path", help="视频文件路径")
    args = ap.parse_args()

    cfg = load_config()
    run(args.match_id, args.video_path, cfg)


if __name__ == "__main__":
    _main()
