"""Job 状态机与 state.json 持久化。

状态流转：
  DISCOVERED → SCRAPED →[审A]→ DATA_OK → DOWNLOADED
  → SCRIPTED → TTS_DONE →[审B]→ CONTENT_OK → EDITED →[审C]→ FINAL_OK → EXPORTED

[审X] 为人工审核门：到达该状态后 job 暂停，等 review CLI 批准后才前进。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    DISCOVERED = "DISCOVERED"      # 赛程页发现了比赛
    SCRAPED = "SCRAPED"            # 战报已抓取 → 等审A
    DATA_OK = "DATA_OK"           # 审A通过：数据确认
    DOWNLOADED = "DOWNLOADED"     # 回放已下载
    SCRIPTED = "SCRIPTED"         # 文案已生成
    TTS_DONE = "TTS_DONE"         # 分段配音已合成 → 等审B
    CONTENT_OK = "CONTENT_OK"     # 审B通过：文案+配音确认
    EDITED = "EDITED"             # Palmier 已成片 → 等审C
    FINAL_OK = "FINAL_OK"         # 审C通过：成片确认
    EXPORTED = "EXPORTED"         # 已导出本地（终态）
    FAILED = "FAILED"             # 出错，需人工介入


# 顺序（不含 FAILED）。用于推进与展示。
ORDER: list[Status] = [
    Status.DISCOVERED, Status.SCRAPED, Status.DATA_OK, Status.DOWNLOADED,
    Status.SCRIPTED, Status.TTS_DONE, Status.CONTENT_OK, Status.EDITED,
    Status.FINAL_OK, Status.EXPORTED,
]

# 人工审核门：处于这些状态时 job 暂停，等待 review 批准推进到下一状态。
REVIEW_GATES: dict[Status, Status] = {
    Status.SCRAPED: Status.DATA_OK,      # 审A
    Status.TTS_DONE: Status.CONTENT_OK,  # 审B
    Status.EDITED: Status.FINAL_OK,      # 审C
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    match_id: str
    status: Status = Status.DISCOVERED
    url: str = ""
    teams: list[str] = field(default_factory=list)
    score: str = ""
    error: str = ""
    updated_at: str = field(default_factory=_now)
    history: list[str] = field(default_factory=list)

    def is_review_gate(self) -> bool:
        return self.status in REVIEW_GATES

    def set_status(self, status: Status, note: str = "") -> None:
        self.status = status
        self.updated_at = _now()
        self.history.append(f"{self.updated_at} {status.value} {note}".strip())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        d["status"] = Status(d.get("status", Status.DISCOVERED.value))
        return cls(**d)


def job_dir(data_dir: Path, match_id: str) -> Path:
    return data_dir / match_id


def state_path(data_dir: Path, match_id: str) -> Path:
    return job_dir(data_dir, match_id) / "state.json"


def load_job(data_dir: Path, match_id: str) -> Job | None:
    p = state_path(data_dir, match_id)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return Job.from_dict(json.load(f))


def save_job(data_dir: Path, job: Job) -> None:
    d = job_dir(data_dir, job.match_id)
    d.mkdir(parents=True, exist_ok=True)
    with state_path(data_dir, job.match_id).open("w", encoding="utf-8") as f:
        json.dump(job.to_dict(), f, ensure_ascii=False, indent=2)


def list_jobs(data_dir: Path) -> list[Job]:
    if not data_dir.exists():
        return []
    jobs: list[Job] = []
    for child in sorted(data_dir.iterdir()):
        if child.is_dir():
            j = load_job(data_dir, child.name)
            if j:
                jobs.append(j)
    return jobs
