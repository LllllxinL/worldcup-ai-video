"""阶段1 · 发现（discover）：扫描 /football/ 列表页 → 当日待处理比赛。

列表项形如：
    <a href="/football/144925.html"
       title="06月24日 世界杯小组赛K组第2轮 哥伦比亚vs民主刚果 全场录像回放">

按「日期(默认今天) + 关键词(默认 世界杯)」过滤，返回比赛清单供 orchestrator 建 job。

独立运行：
    python -m loopwc.stages.discover           # 今天的世界杯比赛
    python -m loopwc.stages.discover --date 06-24 --keyword 世界杯
"""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Any

from bs4 import BeautifulSoup

from ..config import Config, load_config
from ..sources import txzqzhibo

_HREF_RE = re.compile(r"^/football/(\d+)\.html$")
_TITLE_RE = re.compile(
    r"(\d+)月(\d+)日\s*(.+?)\s+([一-龥A-Za-z]+)\s*vs\s*([一-龥A-Za-z]+)"
)


def _parse_title(title: str) -> dict[str, Any] | None:
    m = _TITLE_RE.search(title)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    return {
        "month": month,
        "day": day,
        "competition": m.group(3).strip(),
        "teams": [m.group(4), m.group(5)],
    }


def discover(
    cfg: Config,
    on: tuple[int, int] | None = None,
    keyword: str = "世界杯",
) -> list[dict[str, Any]]:
    """返回 [{match_id, url, title, competition, teams}]，按日期+关键词过滤、去重。"""
    if on is None:
        today = _date.today()
        on = (today.month, today.day)

    base_url = cfg.get("source", "base_url", default="https://www.txzqzhibo.com")
    list_url = base_url.rstrip("/") + "/football/"
    soup = BeautifulSoup(txzqzhibo.fetch(list_url), "lxml")

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=True):
        hm = _HREF_RE.match(a["href"])
        if not hm:
            continue
        title = a.get("title") or a.get_text(" ", strip=True)
        if keyword and keyword not in title:
            continue
        info = _parse_title(title)
        if not info or (info["month"], info["day"]) != on:
            continue
        match_id = hm.group(1)
        if match_id in seen:
            continue
        seen.add(match_id)
        out.append({
            "match_id": match_id,
            "url": txzqzhibo.match_url(base_url, match_id),
            "title": title,
            "competition": info["competition"],
            "teams": info["teams"],
        })
    return out


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="发现当日待处理比赛")
    ap.add_argument("--date", default="", help="MM-DD，默认今天")
    ap.add_argument("--keyword", default="世界杯", help="赛事过滤关键词")
    args = ap.parse_args()

    on = None
    if args.date:
        mm, dd = args.date.split("-")
        on = (int(mm), int(dd))

    cfg = load_config()
    matches = discover(cfg, on=on, keyword=args.keyword)
    print(f"发现 {len(matches)} 场比赛：")
    for m in matches:
        print(f"  {m['match_id']}  {' vs '.join(m['teams'])}  ({m['competition']})")


if __name__ == "__main__":
    _main()
