"""watcher：基于赛程，在每场比赛开始后2小时精准触发轮询。

检测原理：txzqzhibo.com 列表页上，已发布战报的比赛链接文字用 <b> 标签加粗，
还没战报的是普通文字。比赛开始+2小时后才进入检查窗口（3分钟一次），避免白天空转。

用法：
    # 前台运行（看日志）
    PYTHONPATH=src python -m loopwc.watcher

    # 后台（launchd 自动管理，见 ~/Library/LaunchAgents/com.loopwc.watcher.plist）
    launchctl start com.loopwc.watcher
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .config import load_config
from .state import Status, load_job, ORDER

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = PROJECT_ROOT / "schedule.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watcher] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

_HREF_RE = re.compile(r"^/football/(\d+)\.html$")


def _notify(title: str, msg: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
            check=False, capture_output=True,
        )
    except Exception:
        pass


def _already_processing(cfg, match_id: str) -> bool:
    job = load_job(cfg.data_dir, match_id)
    if job is None:
        return False
    try:
        return ORDER.index(job.status) >= ORDER.index(Status.SCRAPED)
    except ValueError:
        return False


def _page_has_full_replay(match_id: str, base_url: str) -> bool:
    """检查比赛详情页的全场录像（lx）tab 里是否有有效回放链接。"""
    url = f"{base_url.rstrip('/')}/football/{match_id}.html"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"检查详情页失败 {match_id}: {e}")
        return False

    soup = BeautifulSoup(resp.text, "lxml")
    lx_div = soup.find("div", {"id": "lx"})
    if not lx_div:
        return False

    # lx tab 里只要有任意一个已知视频源链接，就认为全场回放已就绪
    for a in lx_div.find_all("a", href=True):
        text = a.get_text("", strip=True)
        if any(src in text for src in ("[小红书]", "[咪咕", "[央视频]")):
            return True
    return False


def _fetch_ready_matches(cfg, keyword: str = "世界杯") -> list[dict]:
    """扫列表页，找今天已发布全场回放但未处理的比赛。"""
    today = datetime.now()
    date_str = f"{today.month:02d}月{today.day:02d}日"

    base_url = cfg.get("source", "base_url", default="https://www.txzqzhibo.com")
    url = base_url.rstrip("/") + "/football/"

    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"请求列表页失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    ready = []

    for a in soup.find_all("a", href=True):
        hm = _HREF_RE.match(a["href"])
        if not hm:
            continue
        title = a.get("title", "") or a.get_text(" ", strip=True)
        if keyword not in title or date_str not in title:
            continue
        match_id = hm.group(1)
        if _already_processing(cfg, match_id):
            continue

        # 旧逻辑：首页加粗 = 已就绪（快速路径）
        # 新逻辑：即使没加粗，也检查详情页是否有全场回放
        if a.find("b") or _page_has_full_replay(match_id, base_url):
            ready.append({"match_id": match_id, "title": title})

    return ready


def _trigger_orchestrator(match_id: str) -> None:
    log_path = PROJECT_ROOT / "logs" / f"orch_{match_id}.log"
    log_path.parent.mkdir(exist_ok=True)
    import os
    env = {**os.environ,
           "PYTHONPATH": str(PROJECT_ROOT / "src"),
           "PYTHONUNBUFFERED": "1"}
    with log_path.open("w") as f:
        subprocess.Popen(
            [sys.executable, "-m", "loopwc.orchestrator", match_id],
            stdout=f, stderr=f, env=env, cwd=str(PROJECT_ROOT),
        )
    log.info(f"  → orchestrator 已启动，日志: {log_path}")


def _load_schedule() -> list[dict]:
    """读 schedule.json，返回未来的比赛列表（含 datetime 对象）。"""
    import json
    if not SCHEDULE_PATH.exists():
        return []
    entries = json.loads(SCHEDULE_PATH.read_text())
    result = []
    for e in entries:
        try:
            dt = datetime.strptime(e["beijing"], "%Y-%m-%d %H:%M")
            result.append({**e, "dt": dt})
        except ValueError:
            continue
    return sorted(result, key=lambda x: x["dt"])


def _next_window(schedule: list[dict], delay_hours: float = 2.0) -> datetime | None:
    """找下一个检查窗口的开始时间（比赛开始 + delay_hours）。"""
    now = datetime.now()
    for entry in schedule:
        check_start = entry["dt"] + timedelta(hours=delay_hours)
        if check_start > now:
            return check_start
    return None


def run_once(cfg, keyword: str = "世界杯") -> int:
    matches = _fetch_ready_matches(cfg, keyword=keyword)
    if not matches:
        return 0
    for m in matches:
        log.info(f"发现新战报：{m['match_id']} {m['title'][:50]}")
        _notify("loopwc", f"新战报：{m['title'][:30]}，开始处理")
        _trigger_orchestrator(m["match_id"])
    return len(matches)


def watch(keyword: str = "世界杯", delay_hours: float = 2.0,
          poll_interval: int = 180) -> None:
    """
    基于 schedule.json，在每场比赛开始后 delay_hours 小时进入轮询窗口。
    轮询窗口内每 poll_interval 秒扫一次，窗口外 sleep 到下一场比赛。
    轮询窗口持续 2 小时（战报一般 15 分钟内出现，最多等 2 小时）。
    """
    cfg = load_config()
    schedule = _load_schedule()

    log.info(f"watcher 启动，加载 {len(schedule)} 场赛程")
    log.info(f"策略：比赛开始后 {delay_hours} 小时进入检查窗口（每 {poll_interval//60} 分钟扫一次）")

    # 启动时检查 cookie
    from .stages.download import check_cookie
    cookie = cfg.get("download", "xhs_cookie", default="")
    if check_cookie(cookie):
        log.info("xhs_cookie 验证有效 ✓")
    else:
        log.warning("⚠ xhs_cookie 已失效！请更新 config.yaml，否则视频无法自动下载")
        _notify("loopwc", "⚠ xhs_cookie 失效，请更新 config.yaml")
    _notify("loopwc watcher", "监控已启动")

    while True:
        schedule = _load_schedule()  # 每轮刷新（支持运行中更新赛程）
        now = datetime.now()

        # 找当前是否在某个检查窗口内
        in_window = False
        for entry in schedule:
            window_start = entry["dt"] + timedelta(hours=delay_hours)
            window_end = window_start + timedelta(hours=2)  # 窗口持续 2 小时
            if window_start <= now <= window_end:
                stage = entry.get("stage", "")
                home = entry.get("home", "TBD")
                away = entry.get("away", "TBD")
                log.info(f"检查窗口中 [{stage}] {home} vs {away}（窗口至 {window_end.strftime('%H:%M')}）")
                in_window = True
                break

        if in_window:
            try:
                n = run_once(cfg, keyword=keyword)
                if n == 0:
                    log.info("  暂无新战报，继续轮询...")
                else:
                    log.info(f"  触发 {n} 场，继续监听其他比赛...")
            except Exception as e:
                log.error(f"扫描出错: {e}")
            time.sleep(poll_interval)
        else:
            # 不在窗口，计算下一个窗口开始时间
            next_win = _next_window(schedule, delay_hours)
            if next_win is None:
                # 所有比赛都处理完了
                wait_hours = 6
                log.info(f"无待处理赛程，{wait_hours} 小时后再检查（可更新 schedule.json）")
                _notify("loopwc", "所有赛程已处理，请更新 schedule.json")
                time.sleep(wait_hours * 3600)
            else:
                sleep_secs = (next_win - now).total_seconds()
                wake_str = next_win.strftime("%m-%d %H:%M")
                log.info(f"下一个检查窗口：{wake_str}（{sleep_secs/3600:.1f} 小时后），现在休眠")
                # 每小时醒来打印一次状态，确认还在跑
                while True:
                    now = datetime.now()
                    remaining = (next_win - now).total_seconds()
                    if remaining <= 0:
                        break
                    sleep_chunk = min(3600, remaining)
                    time.sleep(sleep_chunk)
                    now = datetime.now()
                    if (next_win - now).total_seconds() > 60:
                        log.info(f"  距下一窗口还有 {(next_win - now).total_seconds()/3600:.1f} 小时")


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="监控战报，基于赛程精准触发 orchestrator")
    ap.add_argument("--keyword", default="世界杯")
    ap.add_argument("--delay", type=float, default=2.0, help="比赛开始后几小时进入检查窗口")
    ap.add_argument("--interval", type=int, default=180, help="窗口内轮询间隔（秒）")
    ap.add_argument("--once", action="store_true", help="只扫一次就退出（测试用）")
    args = ap.parse_args()

    if args.once:
        cfg = load_config()
        n = run_once(cfg, keyword=args.keyword)
        print(f"触发 {n} 场")
    else:
        watch(keyword=args.keyword, delay_hours=args.delay, poll_interval=args.interval)


if __name__ == "__main__":
    _main()
