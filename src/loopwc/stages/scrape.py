"""阶段1 · 采集（scrape）：抓取比赛页 → 解析 → 写 match.json。

可作为 orchestrator 的一步调用 run()，也可独立运行：
    python -m loopwc.stages.scrape 144899
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..sources import txzqzhibo


def save_match(data_dir: Path, match: dict[str, Any]) -> Path:
    d = data_dir / match["match_id"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / "match.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(match, f, ensure_ascii=False, indent=2)
    return p


def run(match_id: str, cfg: Config, url: str = "") -> dict[str, Any]:
    """抓取并解析一场比赛，落盘 match.json，返回结构化数据。"""
    base_url = cfg.get("source", "base_url", default="https://www.txzqzhibo.com")
    if not url:
        url = txzqzhibo.match_url(base_url, match_id)
    html = txzqzhibo.fetch(url)
    match = txzqzhibo.parse(html, match_id, url)
    save_match(cfg.data_dir, match)
    return match


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="抓取并解析一场比赛 → match.json")
    ap.add_argument("match_id", help="比赛内容 ID，如 144899")
    ap.add_argument("--url", default="", help="可选：直接指定详情页 URL")
    args = ap.parse_args()

    cfg = load_config()
    match = run(args.match_id, cfg, url=args.url)
    n_goals = len(match["goals"])
    n_review = sum(1 for g in match["goals"] if g["needs_review"])
    print(f"✓ match.json 已写入 {cfg.data_dir / args.match_id}")
    print(f"  {' vs '.join(match['teams'])}  比分 {match['score']}  {match['date']}")
    print(f"  进球 {n_goals} 个（{n_review} 个待人工核对）, 事件 {len(match['events'])} 个")
    print(f"  全场录像源: {list(match['replay_links'])}")


if __name__ == "__main__":
    _main()
