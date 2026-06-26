"""orchestrator：串联全部阶段，一条命令跑完一场比赛。

用法：
    # 对指定比赛跑全部阶段
    python -m loopwc.orchestrator 144922

    # 今天所有世界杯比赛
    python -m loopwc.orchestrator --today

    # 指定日期
    python -m loopwc.orchestrator --date 06-24 --keyword 世界杯

阶段顺序：scrape → download → script → tts → edit → export
审核门（SCRAPED/TTS_DONE/EDITED）在自动化阶段跳过，后续调优后加回。
"""
from __future__ import annotations

import json as _json
import sys
import traceback
from pathlib import Path

from .config import Config, load_config
from .state import Status, ORDER, load_job, save_job, Job
from .stages import scrape, script as script_stage, tts, edit, export as export_stage
from .stages import download as download_stage
from .stages import discover as discover_stage


def _status_idx(s: Status) -> int:
    try:
        return ORDER.index(s)
    except ValueError:
        return -1


def _already_done(cfg: Config, match_id: str, target: Status) -> bool:
    job = load_job(cfg.data_dir, match_id)
    if job is None:
        return False
    return _status_idx(job.status) >= _status_idx(target)


def _video_path_exists(cfg: Config, match_id: str) -> str:
    """返回 match.json 里 video_path（若文件存在），否则返回空字符串。"""
    p = cfg.data_dir / match_id / "match.json"
    if not p.exists():
        return ""
    vp = _json.loads(p.read_text()).get("video_path", "")
    return vp if vp and Path(vp).exists() else ""


def run_match(match_id: str, cfg: Config, video_path: str = "") -> bool:
    """对单场比赛串联运行全部阶段，跳过已完成的。返回 True 表示成功。"""
    print(f"\n{'='*50}")
    print(f"比赛 {match_id}")
    print(f"{'='*50}")

    # ── 1. scrape ──────────────────────────────────────
    if _already_done(cfg, match_id, Status.SCRAPED):
        print("[1/6] scrape   已完成，跳过")
    else:
        print("[1/6] scrape   抓取战报...")
        try:
            scrape.run(match_id, cfg)
            print("      ✓ 完成")
        except Exception as e:
            print(f"      ✗ 失败: {e}")
            traceback.print_exc()
            return False

    # ── 2. download ────────────────────────────────────
    if _already_done(cfg, match_id, Status.DOWNLOADED):
        print("[2/6] download 已完成，跳过")
    elif _video_path_exists(cfg, match_id) or video_path:
        # 已有本地视频（手动下载 / --video 参数），自动标记为 DOWNLOADED
        vp = video_path or _video_path_exists(cfg, match_id)
        print(f"[2/6] download 已有本地视频，跳过  ({Path(vp).name})")
        job = load_job(cfg.data_dir, match_id)
        if not job:
            job = Job(match_id=match_id)
        if _status_idx(job.status) < _status_idx(Status.DOWNLOADED):
            job.set_status(Status.DOWNLOADED)
            save_job(cfg.data_dir, job)
        if video_path:
            # 把 --video 路径写入 match.json
            mj = cfg.data_dir / match_id / "match.json"
            m = _json.loads(mj.read_text())
            m["video_path"] = video_path
            mj.write_text(_json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print("[2/6] download 开始下载视频...")
        try:
            result = download_stage.run(match_id, cfg)
            print(f"      ✓ 完成  {Path(result['video_path']).name}")
        except Exception as e:
            print(f"      ✗ 下载失败: {e}")
            print(f"      → 请手动下载视频后运行：")
            print(f"        python -m loopwc.stages.link {match_id} /path/to/video.mp4")
            print(f"      → 然后重新运行 orchestrator，下载步骤会自动跳过")
            # 下载失败不终止：edit 阶段还会检查 video_path，用户补充后可继续

    # ── 3. script ──────────────────────────────────────
    if _already_done(cfg, match_id, Status.SCRIPTED):
        print("[3/6] script   已完成，跳过")
    else:
        print("[3/6] script   生成文案...")
        try:
            script_stage.run(match_id, cfg)
            print("      ✓ 完成")
        except Exception as e:
            print(f"      ✗ 失败: {e}")
            traceback.print_exc()
            return False

    # ── 4. tts ─────────────────────────────────────────
    if _already_done(cfg, match_id, Status.TTS_DONE):
        print("[4/6] tts      已完成，跳过")
    else:
        print("[4/6] tts      合成配音...")
        try:
            tts.run(match_id, cfg)
            print("      ✓ 完成")
        except Exception as e:
            print(f"      ✗ 失败: {e}")
            traceback.print_exc()
            return False

    # ── 5. edit ────────────────────────────────────────
    if _already_done(cfg, match_id, Status.EDITED):
        print("[5/6] edit     已完成，跳过")
    else:
        effective_video = _video_path_exists(cfg, match_id)
        if not effective_video:
            print("[5/6] edit     ✗ 跳过（视频未下载，请先补充视频路径）")
            print(f"      → python -m loopwc.stages.link {match_id} /path/to/video.mp4")
            return False
        print(f"[5/6] edit     开始剪辑（{Path(effective_video).name}）...")
        print("      Palmier agent 运行中，预计 20-40 分钟...")
        try:
            result = edit.run(match_id, cfg, video_override=effective_video)
            print(f"      ✓ 完成")
        except Exception as e:
            print(f"      ✗ 失败: {e}")
            traceback.print_exc()
            return False

    # ── 6. export ──────────────────────────────────────
    if _already_done(cfg, match_id, Status.EXPORTED):
        print("[6/6] export   已完成，跳过")
    else:
        print("[6/6] export   ffmpeg 合成导出...")
        try:
            result = export_stage.run(match_id, cfg)
            print(f"      ✓ 完成  成片：{result['output_path']}")
        except Exception as e:
            print(f"      ✗ 失败: {e}")
            traceback.print_exc()
            return False

    print(f"\n✓✓✓  {match_id} 全部完成！")
    return True


def run_today(cfg: Config, keyword: str = "世界杯", video_path: str = "", on: tuple | None = None) -> None:
    """发现今天（或指定日期）所有比赛并逐一处理。"""
    print(f"发现今日 {keyword} 比赛...")
    try:
        matches = discover_stage.discover(cfg, on=on, keyword=keyword)
    except Exception as e:
        print(f"discover 失败: {e}")
        traceback.print_exc()
        return

    if not matches:
        print("今日无比赛。")
        return

    print(f"共 {len(matches)} 场：")
    for m in matches:
        print(f"  {m['match_id']}  {' vs '.join(m['teams'])}")

    success, fail = 0, 0
    for m in matches:
        ok = run_match(m["match_id"], cfg, video_path=video_path)
        if ok:
            success += 1
        else:
            fail += 1

    print(f"\n完成：{success} 成功，{fail} 失败")


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="世界杯锐评视频自动化流水线")
    ap.add_argument("match_id", nargs="?", help="指定比赛 ID（如 144922）；省略则跑今日所有比赛")
    ap.add_argument("--video", default="", help="手动指定全场回放视频路径（会写入 match.json）")
    ap.add_argument("--today", action="store_true", help="强制跑今日所有比赛")
    ap.add_argument("--date", default="", help="指定日期 MM-DD")
    ap.add_argument("--keyword", default="世界杯", help="赛事过滤关键词，默认世界杯")
    ap.add_argument("--reset", action="store_true", help="忽略已完成状态，从头重跑")
    args = ap.parse_args()

    cfg = load_config()

    if args.match_id:
        if args.reset:
            job = load_job(cfg.data_dir, args.match_id)
            if job:
                job.set_status(Status.DISCOVERED)
                save_job(cfg.data_dir, job)
                print(f"已重置 {args.match_id} 状态")
        ok = run_match(args.match_id, cfg, video_path=args.video)
        sys.exit(0 if ok else 1)
    else:
        on = None
        if args.date:
            mm, dd = args.date.split("-")
            on = (int(mm), int(dd))
        run_today(cfg, keyword=args.keyword, video_path=args.video, on=on)


if __name__ == "__main__":
    _main()
