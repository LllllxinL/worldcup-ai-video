"""txzqzhibo.com 比赛页解析。

页面是静态 HTML。核心可抓取内容：
- 标题：含「日期 + 赛事 + 主队vs客队 + 全场录像回放」
- 战报正文 (.panel-article-content)：导语段 + 「第N分钟」分钟级流水
- 视频链接：锚文本带标签前缀，形如
    [小红书]...全场录像      → 完整回放（xhs 源）
    [咪咕]...全场录像[有比分] → 完整回放（migu 源）
    [央视频]...全场录像      → 完整回放（cctv 源）
    [小红书全场集锦]...      → 集锦
    [进球视频]...           → 每个进球一条（决定进球数量）
    [红牌罚下]...           → 看点事件

解析产出标准化 dict（写入 match.json）。进球的精确 minute/player 做
best-effort 提取：minute 优先取正文「进球句」，player 用前瞻正则 + 黑名单，
缺失时标记 needs_review=True 交人工审核 A 兜底。
"""
from __future__ import annotations

import re
from typing import Any

import requests
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def match_url(base_url: str, match_id: str) -> str:
    return f"{base_url.rstrip('/')}/football/{match_id}.html"


def fetch(url: str, timeout: int = 20) -> str:
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text

# 正文里判定一句是「进球句」的关键词（排除"领先"等总结性词，避免误抓）
_GOAL_KW = re.compile(
    r"破门|收获进球|打入|攻入|扳平|反超|梅开二度|帽子戏法|建功|点球命中|头球得分|头球破门|凌空"
)
# 破门球员：人名后紧跟动作/位置词（前瞻），避免把"开场/禁区"等当人名
_PLAYER_RE = re.compile(
    r"([一-龥]{2,4})(?=开场|在禁区|在中路|在前场|在左|在右|禁区|远射|头球|主罚|一脚|插上|跟进|推射|抽射|凌空|破门|打入|攻门|劲射)"
)
_PLAYER_STOP = {"球员", "门将", "裁判", "主裁", "后卫", "前锋", "中场",
                "对方", "双方", "禁区", "开场", "全队", "随后", "最终"}
_MINUTE_RE = re.compile(r"第(\d+(?:\+\d+)?)分钟")
_SECOND_RE = re.compile(r"(\d+)\s*秒")
_SCORE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")


def _source_of(tag: str) -> str | None:
    if "小红书" in tag:
        return "xhs"
    if "咪咕" in tag:
        return "migu"
    if "央视" in tag or "CCTV" in tag.upper():
        return "cctv"
    return None


def _minute_from_text(text: str) -> str:
    m = _MINUTE_RE.search(text)
    if m:
        return m.group(1)
    s = _SECOND_RE.search(text)
    if s:
        sec = int(s.group(1))
        return str(max(1, sec // 60 + 1))  # 65秒 → 第1分钟进行中，取下一整分
    return ""


def _player_from_text(text: str, blacklist: frozenset[str] = frozenset()) -> str:
    for m in _PLAYER_RE.finditer(text):
        name = m.group(1)
        if name in blacklist or name in _PLAYER_STOP:
            continue
        if re.search(r"[0-9一二三四五六七八九十百]", name):
            continue
        return name
    return ""


def parse(html: str, match_id: str, url: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""

    # 球队
    teams: list[str] = []
    tm = re.search(r"([一-龥A-Za-z]+)\s*vs\s*([一-龥A-Za-z]+)", title, re.I)
    if tm:
        teams = [tm.group(1), tm.group(2)]
    blacklist = frozenset(teams)

    # 赛事（日期与「主队vs」之间）
    competition = ""
    cm = re.search(r"\d+月\d+日\s*(.+?)\s*[一-龥A-Za-z]+\s*vs", title)
    if cm:
        competition = cm.group(1).strip()

    art = soup.select_one(".panel-article-content")
    art_text = art.get_text("\n", strip=True) if art else ""
    scope = art if art else soup

    # 发布日期 → date
    date = ""
    dm = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", art_text)
    if dm:
        date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"

    # 分类视频链接（用完整 label 判断类型，来源用标签前缀）
    replay_links: dict[str, str] = {}     # 全场录像
    highlight_links: dict[str, str] = {}  # 集锦
    goal_clips: list[dict[str, str]] = []  # [进球视频]
    events: list[dict[str, str]] = []      # 红牌/黄牌等看点
    for a in scope.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        href = a["href"]
        m = re.match(r"\[([^\]]+)\]\s*(.*)", label)
        if not m:
            continue
        tag, desc = m.group(1), m.group(2).strip()
        src = _source_of(tag)
        if "集锦" in label:
            if src:
                highlight_links[src] = href
        elif "全场录像" in label:
            if src:
                replay_links[src] = href
        elif tag.startswith("进球"):
            goal_clips.append({"desc": desc, "url": href})
        elif any(k in tag for k in ("红牌", "黄牌", "罚下", "点球", "乌龙")):
            events.append({
                "type": "red_card" if ("红牌" in tag or "罚下" in tag) else tag,
                "minute": _event_minute(
                    art_text,
                    ("红牌", "罚下") if ("红牌" in tag or "罚下" in tag) else (tag,),
                ),
                "desc": desc,
                "url": href,
            })

    # 比分：标题无比分，从进球/集锦描述或正文取
    score = ""
    for cand in [d["desc"] for d in goal_clips] + [a.get_text(" ", strip=True)
                                                   for a in scope.find_all("a")] + [art_text]:
        sm = _SCORE_RE.search(cand)
        if sm:
            score = f"{sm.group(1)}-{sm.group(2)}"
            break

    # 正文「分钟流水」里的进球句（必须以"第N分钟"开头，排除导语总述句），
    # 按出现顺序用于给每个进球对齐精确 minute/player
    goal_sentences: list[str] = [
        s for s in re.split(r"[。！；\n]", art_text)
        if re.match(r"\s*第\d+(?:\+\d+)?分钟", s) and _GOAL_KW.search(s)
    ]

    # 进球：数量以 [进球视频] 条目为准；minute 优先正文进球句，player best-effort
    goals: list[dict[str, Any]] = []
    for i, clip in enumerate(goal_clips, 1):
        desc = clip["desc"]
        sent = goal_sentences[i - 1] if i - 1 < len(goal_sentences) else ""
        minute = _minute_from_text(sent) or _minute_from_text(desc)
        player = _player_from_text(desc, blacklist) or _player_from_text(sent, blacklist)
        goals.append({
            "idx": i,
            "minute": minute,
            "player": player,
            "desc": desc,
            "url": clip["url"],
            "needs_review": not (minute and player),
        })

    highlights = [g["desc"] for g in goals] + [e["desc"] for e in events]

    return {
        "match_id": match_id,
        "url": url,
        "title": title,
        "teams": teams,
        "score": score,
        "date": date,
        "competition": competition,
        "report": art_text,
        "narrative": _extract_narrative(art_text),
        "highlights": highlights,
        "goals": goals,
        "events": events,
        "replay_links": replay_links,
        "highlight_links": highlight_links,
    }


_NOISE_PREFIXES = ("发布时间", "浏览：", "赛事：", "标签：", "来源：")
_LINK_TAGS = {"小红书", "咪咕", "央视频", "CCTV"}
_EVENT_TAGS = {"进球视频", "射门被挡", "红牌", "黄牌", "罚下", "点球", "乌龙"}


def _extract_narrative(art_text: str) -> str:
    """从完整 art_text 提取供 LLM 使用的有效信息，过滤元数据/链接/阵容等噪音。"""
    lines = art_text.splitlines()
    result: list[str] = []
    in_main_body = False
    stop = False

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        if i == 0:          # 第一行是标题，直接保留
            result.append(line)
            continue

        if line.startswith("双方出场阵容"):
            stop = True
        if stop:
            continue

        if line.startswith("#"):
            continue

        if any(line.startswith(p) for p in _NOISE_PREFIXES):
            continue

        bracket = re.match(r"^\[([^\]]+)\]", line)
        if bracket:
            tag = bracket.group(1)
            if any(t in tag for t in _LINK_TAGS) and any(k in line for k in ("录像", "集锦")):
                continue
            if any(t in tag for t in _EVENT_TAGS):
                result.append(line)
            continue  # 其他 [X] 行过滤

        if "天下足球直播网" in line:
            in_main_body = True

        if in_main_body:
            result.append(line)

    return "\n".join(result)


def _event_minute(art_text: str, keywords: tuple[str, ...]) -> str:
    """在正文「分钟流水」里找含事件关键词、以"第N分钟"开头的句子，取其分钟。"""
    for sent in re.split(r"[。！；\n]", art_text):
        if re.match(r"\s*第\d+(?:\+\d+)?分钟", sent) and any(k in sent for k in keywords):
            return _minute_from_text(sent)
    return ""
