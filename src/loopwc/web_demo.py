"""web_demo：前端展示每场比赛的战报、文案、配音、成片。

用法：
    PYTHONPATH=src .venv/bin/python -m loopwc.web_demo
    然后浏览器打开 http://127.0.0.1:5000
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template_string

from .config import load_config

app = Flask(__name__, static_folder=None)
cfg = load_config()

HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>loopwc 世界杯 AI 锐评视频 · 前端展示</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #f8f9fa; }
    .nav-link.active { background: #4361EE !important; color: white !important; }
    .stage-card { border-left: 4px solid #4361EE; }
    pre { white-space: pre-wrap; word-break: break-word; }
    .video-box { max-width: 720px; }
  </style>
</head>
<body>
  <div class="container py-4">
    <h1 class="mb-2">⚽ loopwc 世界杯 AI 锐评视频</h1>
    <p class="text-muted">从战报到成片的每个阶段产物展示</p>

    <div class="row mb-4">
      <div class="col-md-4">
        <div class="card">
          <div class="card-header">比赛列表</div>
          <div class="list-group list-group-flush" id="match-list">
            <!-- 由 JS 填充 -->
          </div>
        </div>
      </div>
      <div class="col-md-8">
        <div id="match-info" class="mb-3"></div>
        <ul class="nav nav-tabs" id="stage-tab" role="tablist">
          <li class="nav-item" role="presentation">
            <button class="nav-link active" id="tab-report" data-bs-toggle="tab" data-bs-target="#pane-report" type="button">战报原文</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-narrative" data-bs-toggle="tab" data-bs-target="#pane-narrative" type="button">清洗后 narrative</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-script" data-bs-toggle="tab" data-bs-target="#pane-script" type="button">AI 文案</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-audio" data-bs-toggle="tab" data-bs-target="#pane-audio" type="button">配音</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-video" data-bs-toggle="tab" data-bs-target="#pane-video" type="button">成片</button>
          </li>
        </ul>
        <div class="tab-content p-3 bg-white border border-top-0 rounded-bottom" id="stage-content">
          <div class="tab-pane fade show active" id="pane-report" role="tabpanel">
            <div id="report-content">请选择一场比赛</div>
          </div>
          <div class="tab-pane fade" id="pane-narrative" role="tabpanel">
            <div id="narrative-content"></div>
          </div>
          <div class="tab-pane fade" id="pane-script" role="tabpanel">
            <div id="script-content"></div>
          </div>
          <div class="tab-pane fade" id="pane-audio" role="tabpanel">
            <div id="audio-content"></div>
          </div>
          <div class="tab-pane fade" id="pane-video" role="tabpanel">
            <div id="video-content"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    let matches = [];

    async function loadMatches() {
      const res = await fetch('/api/matches');
      matches = await res.json();
      const list = document.getElementById('match-list');
      list.innerHTML = matches.map((m, i) => `
        <button class="list-group-item list-group-item-action" id="btn-${m.id}"
                onclick="selectMatch('${m.id}')">
          <strong>${m.teams.join(' vs ')}</strong>
          <span class="badge bg-secondary float-end">${m.score}</span>
          <div class="small text-muted">${m.competition} · ${m.status}</div>
        </button>
      `).join('');
      // 默认选中有成片的最完整比赛
      const exported = matches.find(m => m.status === 'EXPORTED');
      const defaultId = exported ? exported.id : matches[0].id;
      selectMatch(defaultId);
    }

    async function selectMatch(id) {
      // 高亮当前项
      document.querySelectorAll('#match-list button').forEach(btn => btn.classList.remove('active'));
      const idx = matches.findIndex(m => m.id === id);
      document.querySelectorAll('#match-list button')[idx]?.classList.add('active');

      const res = await fetch(`/api/match/${id}`);
      const data = await res.json();

      document.getElementById('match-info').innerHTML = `
        <div class="card stage-card">
          <div class="card-body">
            <h4>${data.teams.join(' vs ')} <span class="badge bg-primary">${data.score}</span></h4>
            <p class="mb-0 text-muted">${data.date} · ${data.competition}</p>
          </div>
        </div>
      `;

      document.getElementById('report-content').innerHTML = `<pre>${escapeHtml(data.report)}</pre>`;
      document.getElementById('narrative-content').innerHTML = `<pre>${escapeHtml(data.narrative)}</pre>`;

      // 文案
      if (data.script) {
        let scriptHtml = `<h5>开头</h5><p>${escapeHtml(data.script.intro)}</p>`;
        scriptHtml += data.script.goals.map(g => `
          <h5>进球 ${g.idx} · 第 ${g.minute} 分钟 · ${g.player}</h5>
          <p>${escapeHtml(g.text)}</p>
        `).join('');
        scriptHtml += `<h5>结尾</h5><p>${escapeHtml(data.script.outro)}</p>`;
        document.getElementById('script-content').innerHTML = scriptHtml;
      } else {
        document.getElementById('script-content').innerHTML = '<p class="text-muted">尚未生成文案</p>';
      }

      // 配音
      if (data.audios.length) {
        document.getElementById('audio-content').innerHTML = data.audios.map(a => `
          <div class="mb-3">
            <strong>${a.name}</strong>
            <audio controls src="${a.url}" class="w-100"></audio>
          </div>
        `).join('');
      } else {
        document.getElementById('audio-content').innerHTML = '<p class="text-muted">尚未合成配音</p>';
      }

      // 成片
      if (data.video) {
        document.getElementById('video-content').innerHTML = `
          <div class="video-box">
            <video controls class="w-100" src="${data.video}"></video>
          </div>
        `;
      } else {
        document.getElementById('video-content').innerHTML = '<p class="text-muted">尚未导出成片</p>';
      }
    }

    function escapeHtml(text) {
      if (!text) return '';
      return text.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
    }

    loadMatches();
  </script>
</body>
</html>
"""


def _list_matches():
    matches = []
    if not cfg.data_dir.exists():
        return matches
    for child in sorted(cfg.data_dir.iterdir()):
        if not child.is_dir():
            continue
        match_path = child / "match.json"
        state_path = child / "state.json"
        if not match_path.exists():
            continue
        with match_path.open(encoding="utf-8") as f:
            match = json.load(f)
        status = "UNKNOWN"
        if state_path.exists():
            with state_path.open(encoding="utf-8") as f:
                status = json.load(f).get("status", "UNKNOWN")
        matches.append({
            "id": match.get("match_id", child.name),
            "teams": match.get("teams", []),
            "score": match.get("score", ""),
            "date": match.get("date", ""),
            "competition": match.get("competition", ""),
            "status": status,
        })
    return matches


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/matches")
def api_matches():
    return jsonify(_list_matches())


@app.route("/api/match/<match_id>")
def api_match(match_id: str):
    job_dir = cfg.data_dir / match_id
    match_path = job_dir / "match.json"
    script_path = job_dir / "script.json"

    if not match_path.exists():
        return jsonify({"error": "比赛不存在"}), 404

    with match_path.open(encoding="utf-8") as f:
        match = json.load(f)

    script = None
    if script_path.exists():
        with script_path.open(encoding="utf-8") as f:
            script = json.load(f)

    # 收集配音文件
    audios = []
    for p in sorted(job_dir.glob("audio_*.mp3")):
        audios.append({"name": p.stem, "url": f"/static/{match_id}/{p.name}"})

    # 成片
    final = job_dir / f"{match_id}_final.mp4"
    video_url = f"/static/{match_id}/{final.name}" if final.exists() else ""

    return jsonify({
        "id": match_id,
        "teams": match.get("teams", []),
        "score": match.get("score", ""),
        "date": match.get("date", ""),
        "competition": match.get("competition", ""),
        "report": match.get("report", ""),
        "narrative": match.get("narrative", ""),
        "script": script,
        "audios": audios,
        "video": video_url,
    })


@app.route("/static/<path:filename>")
def static_files(filename: str):
    """提供 data/matches/ 下的音频和视频文件。"""
    p = cfg.data_dir / filename
    if not p.exists():
        return "Not found", 404
    from flask import send_file
    return send_file(p, mimetype="video/mp4" if p.suffix == ".mp4" else "audio/mpeg")


def main():
    print("=" * 50)
    print("loopwc 前端展示已启动")
    print("请浏览器打开: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
