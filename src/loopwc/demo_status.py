"""demo_status：项目状态看板（汇报用）。

用法：
    PYTHONPATH=src .venv/bin/python -m loopwc.demo_status
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import load_config
from .state import load_job, Status


def _format_size(p: Path) -> str:
    if not p.exists():
        return "-"
    b = p.stat().st_size
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1024 / 1024:.1f} MB"


def _duration(p: Path) -> str:
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(p)],
            capture_output=True, text=True,
        )
        s = float(out.stdout.strip())
        return f"{int(s // 60)}:{int(s % 60):02d}"
    except Exception:
        return "-"


def run() -> dict:
    cfg = load_config()
    data_dir = cfg.data_dir

    jobs = []
    if data_dir.exists():
        for child in sorted(data_dir.iterdir()):
            if child.is_dir():
                job = load_job(data_dir, child.name)
                if job:
                    jobs.append(job)

    status_counts = Counter(j.status.value for j in jobs)

    completed = []
    for j in jobs:
        final = data_dir / j.match_id / f"{j.match_id}_final.mp4"
        if final.exists():
            completed.append({
                "match_id": j.match_id,
                "status": j.status.value,
                "updated_at": j.updated_at,
                "size": _format_size(final),
                "duration": _duration(final),
                "path": str(final),
            })

    completed.sort(key=lambda x: x["updated_at"], reverse=True)
    return {
        "total": len(jobs),
        "status_counts": dict(status_counts),
        "completed": completed,
    }


def print_board() -> None:
    data = run()

    print("=" * 60)
    print("loopwc 项目状态看板")
    print("=" * 60)
    print(f"\n总比赛数：{data['total']}")
    print("\n阶段分布：")
    for status, count in data["status_counts"].items():
        print(f"  {status:12s} : {count} 场")

    print(f"\n已完成成片：{len(data['completed'])} 个")
    print("-" * 60)
    for c in data["completed"]:
        print(f"  {c['match_id']}  [{c['status']}]  {c['size']:8s}  {c['duration']:5s}  {c['path']}")

    print("\n" + "=" * 60)


def generate_html(output_path: str | None = None) -> str:
    data = run()
    if output_path is None:
        output_path = str(Path(__file__).resolve().parents[2] / "reports" / "2026-06-25" / "dashboard.html")

    rows = "\n".join(
        f"""      <tr>
        <td>{c['match_id']}</td>
        <td>{c['status']}</td>
        <td>{c['size']}</td>
        <td>{c['duration']}</td>
        <td>{c['updated_at']}</td>
      </tr>"""
        for c in data["completed"]
    )

    status_items = "\n".join(
        f'      <li class="list-group-item d-flex justify-content-between">\n        <span>{status}</span>\n        <span class="badge bg-primary rounded-pill">{count}</span>\n      </li>'
        for status, count in data["status_counts"].items()
    )

    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>loopwc 状态看板</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container py-5">
    <h1 class="mb-4">⚽ loopwc 世界杯 AI 锐评视频 · 状态看板</h1>
    <div class="row mb-4">
      <div class="col-md-4">
        <div class="card text-white bg-success">
          <div class="card-body">
            <h5 class="card-title">总比赛数</h5>
            <p class="card-text display-4">{data['total']}</p>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="card text-white bg-primary">
          <div class="card-body">
            <h5 class="card-title">已完成成片</h5>
            <p class="card-text display-4">{len(data['completed'])}</p>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="card text-white bg-info">
          <div class="card-body">
            <h5 class="card-title">更新时间</h5>
            <p class="card-text">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-md-4">
        <div class="card">
          <div class="card-header">阶段分布</div>
          <ul class="list-group list-group-flush">
{status_items}
          </ul>
        </div>
      </div>
      <div class="col-md-8">
        <div class="card">
          <div class="card-header">已完成成片</div>
          <div class="card-body">
            <table class="table table-striped">
              <thead>
                <tr>
                  <th>比赛 ID</th>
                  <th>状态</th>
                  <th>大小</th>
                  <th>时长</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
{rows}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
""".strip()

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="项目状态看板")
    ap.add_argument("--html", action="store_true", help="生成 HTML 看板")
    args = ap.parse_args()

    if args.html:
        path = generate_html()
        print(f"HTML 看板已生成：{path}")
    else:
        print_board()


if __name__ == "__main__":
    _main()
