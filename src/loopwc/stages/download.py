"""阶段2 · 下载（download）：下载全场回放视频 → 落盘路径写入 match.json。

使用 yt-dlp 下载小红书视频（比 XHS-Downloader 更可靠，不受反爬虫影响）。
需要北京/国内节点的 VPN，或者无 VPN 直连。

独立运行：
    python -m loopwc.stages.download 144899
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job


def _notify(msg: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{msg}" with title "loopwc 下载"'],
            check=False, capture_output=True,
        )
    except Exception:
        pass


def check_cookie(cookie: str) -> bool:
    """验证 xhs_cookie 是否仍然有效。访问小红书用户页，检查是否已登录。"""
    if not cookie:
        return False
    try:
        resp = requests.get(
            "https://www.xiaohongshu.com/user/profile",
            headers={"User-Agent": "Mozilla/5.0", "Cookie": cookie},
            timeout=10, allow_redirects=False,
        )
        # 302 跳转到登录页 = cookie 失效；200 = 有效
        return resp.status_code == 200
    except Exception:
        return False


# 按优先级排列的可用 CDN 主机（v4-m 在部分 VPN 下不通）
_GOOD_CDN = [
    "sns-video-qc-m.xhscdn.com",
    "sns-video-v3-m.xhscdn.com",
    "sns-video-v2-m.xhscdn.com",
    "sns-video-v6-m.xhscdn.com",
]


def _write_cookie_file(cookie_str: str) -> Path:
    """把 cookie 字符串写成 Netscape 格式临时文件供 yt-dlp 使用。"""
    lines = ["# Netscape HTTP Cookie File"]
    for part in cookie_str.split("; "):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        for domain in [".xiaohongshu.com", "www.xiaohongshu.com"]:
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            lines.append(f"{domain}\t{flag}\t/\tFALSE\t9999999999\t{k}\t{v}")
    tmp = Path(tempfile.mktemp(suffix=".txt", prefix="xhs_cookie_"))
    tmp.write_text("\n".join(lines))
    return tmp


def _pick_format_id(xhs_url: str, cookie_file: Path | None) -> str:
    """获取视频格式列表，找到用可用 CDN 的 720P H264 format_id。"""
    cmd = ["yt-dlp", "--no-update", "-j"]
    if cookie_file:
        cmd += ["--cookies", str(cookie_file)]
    cmd.append(xhs_url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return "bestvideo[height<=720]+bestaudio/best[height<=720]"

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "bestvideo[height<=720]+bestaudio/best[height<=720]"

    formats = info.get("formats", [])

    # 找 720P H264 + 可用 CDN（按 _GOOD_CDN 优先级）
    for cdn in _GOOD_CDN:
        for fmt in formats:
            if (fmt.get("height") == 720
                    and fmt.get("vcodec", "").startswith("h264")
                    and cdn in fmt.get("url", "")):
                fid = fmt["format_id"]
                print(f"  选择格式 {fid}（720P H264，{cdn}）", flush=True)
                return fid

    # 降级：任意 720P + 可用 CDN
    for cdn in _GOOD_CDN:
        for fmt in formats:
            if fmt.get("height") == 720 and cdn in fmt.get("url", ""):
                fid = fmt["format_id"]
                print(f"  选择格式 {fid}（720P，{cdn}）", flush=True)
                return fid

    print("  未找到可用格式，使用默认选择", flush=True)
    return "bestvideo[height<=720]+bestaudio/best[height<=720]"


def _download_xhs(xhs_url: str, dest_dir: Path, cookie: str = "") -> Path:
    """用 yt-dlp 下载小红书视频，返回 mp4 路径。"""
    dl_dir = dest_dir / "xhs_dl"
    dl_dir.mkdir(parents=True, exist_ok=True)

    output_tmpl = str(dl_dir / "%(title)s [%(id)s].%(ext)s")

    cookie_file = None
    try:
        if cookie:
            cookie_file = _write_cookie_file(cookie)

        print("  获取视频格式列表...", flush=True)
        fmt = _pick_format_id(xhs_url, cookie_file)

        cmd = [
            "yt-dlp", "--no-update",
            "-f", fmt,
            "--merge-output-format", "mp4",
            "-o", output_tmpl,
            "--newline",
            "--retries", "3",
            "--fragment-retries", "3",
        ]
        if cookie_file:
            cmd += ["--cookies", str(cookie_file)]
        cmd.append(xhs_url)

        print(f"  yt-dlp 下载中：{xhs_url[:80]}...", flush=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

        for line in result.stdout.splitlines()[-5:]:
            if line.strip():
                print(f"  {line}", flush=True)
        if result.returncode != 0:
            print(f"  [yt-dlp stderr] {result.stderr[-300:]}", flush=True)

        mp4s = sorted(dl_dir.glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
        if not mp4s:
            raise RuntimeError(
                f"yt-dlp 下载后找不到 mp4（returncode={result.returncode}）\n"
                f"stderr: {result.stderr[-400:]}"
            )
        return mp4s[0]
    finally:
        if cookie_file and cookie_file.exists():
            cookie_file.unlink(missing_ok=True)


def run(match_id: str, cfg: Config) -> dict[str, Any]:
    """下载全场回放，更新 match.json 和 state.json，返回 {video_path}。"""
    job_dir = cfg.data_dir / match_id
    match_json_path = job_dir / "match.json"

    with match_json_path.open("r", encoding="utf-8") as f:
        match = json.load(f)

    xhs_url = match.get("replay_links", {}).get("xhs", "")
    if not xhs_url:
        raise ValueError(f"match {match_id} 无 xhs 回放链接，请手动下载后用 link.py 关联")

    cookie = cfg.get("download", "xhs_cookie", default="")

    # 下载前验证 cookie 有效性
    print("  验证 xhs_cookie...", flush=True)
    if not check_cookie(cookie):
        msg = "xhs_cookie 已失效！请更新 config.yaml 里的 xhs_cookie，否则无法下载视频。"
        print(f"  ⚠ {msg}", flush=True)
        _notify(msg)
        raise RuntimeError(msg)
    print("  ✓ cookie 有效", flush=True)

    video_path = _download_xhs(xhs_url, job_dir, cookie=cookie)

    match["video_path"] = str(video_path)
    with match_json_path.open("w", encoding="utf-8") as f:
        json.dump(match, f, ensure_ascii=False, indent=2)

    job = load_job(cfg.data_dir, match_id)
    if not job:
        job = Job(match_id=match_id)
    job.set_status(Status.DOWNLOADED)
    save_job(cfg.data_dir, job)

    return {"video_path": str(video_path)}


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="下载全场回放视频（yt-dlp）")
    ap.add_argument("match_id", help="比赛 ID，如 144899")
    args = ap.parse_args()

    cfg = load_config()
    result = run(args.match_id, cfg)
    size_mb = Path(result["video_path"]).stat().st_size // 1024 // 1024
    print(f"✓ 视频已下载: {result['video_path']} ({size_mb} MB)")


if __name__ == "__main__":
    _main()
