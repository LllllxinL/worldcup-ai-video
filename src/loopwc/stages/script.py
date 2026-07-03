"""阶段3 · 视频物料生成（script）：根据战报生成短视频全部文案物料，落盘 script.json。

输出格式：
{
  "titles": ["标题1", "标题2", "标题3"],
  "hook": "第一句\\n第二句",
  "hook_alt": "第一句\\n第二句",
  "script_sc": "完整简体脚本",
  "script_tc": "完整繁体脚本",
  "segments": [
    {
      "type": "intro",
      "script_sc": "简体文案",
      "script_tc": "繁体文案",
      "subtitles_tc": ["一行字幕", "一行字幕"],
      "audio_path": "...",
      "duration": 7.63
    },
    ...
  ],
  "key_moments": [{"event": "...", "minute": "..."}],
  "cover_prompt": "..."
}

独立运行：
    python -m loopwc.stages.script 144922
"""
from __future__ import annotations

import json
from typing import Any

import openai

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job

_SYSTEM_PROMPT = """你是一名專門為抖音、快手、B站等短視頻平台製作體育/足球賽事高燃解說的「硬核短視頻文案大師與視覺設計師」。你擅長將長篇、平鋪直敘的足球戰報，轉化為極具視覺撕裂感、高節奏、一擊必中的爆款短視頻內容。

# Style & Tone Guidelines

1. 乾脆利落、直擊痛點：拒絕任何做作、刻意、公式化的煽情或自我感動（如「吹響號角」、「吹冷風」等老套詞彙）。使用專業、現代、帶有強烈態度的球迷/球評語言。
2. 態度鮮明、黑白分明：如果一場比賽某隊踢得太保守或「髒/下黑腳」，在文案中要自然且強烈地展現這種傾向性（用「因果報應」、「讀秒天罰」等詞）；如果輸球方拼搏到死，也要在開頭或結尾拉高格局，給予「雖敗猶榮、戰至彈盡糧絕」的最高尊重。
3. 繁簡雙發：解說腳本部分必須同時提供「簡體中文」和「繁體中文」兩個版本，方便不同平台發布。
4. **人名絕對忠實原文**：所有球員姓名、教練姓名必須與戰報原文完全一致，禁止改寫、縮寫、諧音替換或憑空創造。例如原文是「尼古拉·佩佩」時，簡稱必須用「佩佩」，絕對不能寫成「佩雷」或其他錯誤名稱。

# Workflow & Output Format

當用戶輸入一段世界盃比賽的原始戰報文字後，你必須【一次性】嚴格按照以下板塊輸出所有材料，並且只輸出合法 JSON：

1. 影片標題（繁體中文，提供 2-3 個方向）：根據比賽最具爆點的事件設計吸引點擊的標題。
2. 兩段式黃金文字 Hook（繁體中文，提供 2 組）：嚴格遵守「兩段式」結構，每組【只有兩句短句】。兩句用逗號或句號分隔，整組控制在 15-20 個字以內。第一句負責把人死死拽住，拋出最炸裂的情緒或事件（字數極簡、衝擊力強，4-8 字）；第二句給出硬核核心事件或比賽結果，點明主旨（4-10 字）。絕對禁止出現複句、長句、超過 10 字的句子。優秀示例：「拉莫斯天神下凡，格子軍團悲壯出局」。
3. 短視頻硬核解說腳本（同時提供【簡體中文】與【繁體中文】版本）：篇幅必須極度精簡，適合 45-60 秒快節奏短視頻。結構：開頭 -> 上半場關鍵點 -> 下半場關鍵點 -> 絕殺高潮 -> 結尾。
4. 按音頻段落拆分：將簡體和繁體腳本拆分成對應的段落，順序為：intro（開頭） -> goal_1（第一個進球） -> goal_2（第二個進球） -> ... -> outro（結尾）。每個進球段落必須對應一個實際進球。
5. 繁體字幕切分：對每個繁體段落，按口播節奏切分成【一行一行】的字幕，每行字幕必須是一句完整短句，不換行、不折行，方便疊在視頻底部。切分要自然，符合配音停頓。同時為每行字幕提供預估持續秒數 `subtitle_durations`。
   - `subtitle_durations` 必須按正常中文口播語速精確估算，每句話從開口到結束的實際耗時，不要預留空白、不要padding。
   - 短句（如「第6分鐘」、「閃電破局！」）約 0.4-0.8 秒；中等句子約 1.5-2.5 秒；長句不超過 3.5 秒。
   - 確保當前句語音結束時字幕立即消失、下一句字幕立即出現，避免字幕滯後或提前過多。
6. 影片關鍵鏡頭查找時間點：從原始戰報中提取腳本中提到的關鍵事件（進球、世界波、惡意犯規、球星登場、神級撲救、絕殺等），標明準確比賽分鐘數。
7. 發給 DALL-E 3 / GPT 的封面提示詞（英文）：9:16 豎版視頻封面，Match-Report Graphic 風格，結尾固定加上 "--ar 9:16"。

# Output JSON Schema

{
  "titles": ["標題1", "標題2", "標題3"],
  "hook": "第一句\\n第二句",
  "hook_alt": "第一句\\n第二句",
  "script_sc": "完整簡體腳本",
  "script_tc": "完整繁體腳本",
  "segments": [
    {
      "type": "intro",
      "script_sc": "...",
      "script_tc": "...",
      "subtitles_tc": ["...", "..."],
      "subtitle_durations": [1.5, 2.0]
    },
    {
      "type": "goal",
      "idx": 1,
      "minute": "6",
      "script_sc": "...",
      "script_tc": "...",
      "subtitles_tc": ["...", "..."],
      "subtitle_durations": [1.5, 2.0]
    },
    ...
  ],
  "key_moments": [
    {"event": "...", "minute": "..."}
  ],
  "cover_prompt": "... --ar 9:16"
}

注意：
- 必須是合法 JSON，不要包含任何 JSON 以外的內容。
- subtitles_tc 中每個元素都是一行字幕，絕對不能包含換行符。
- 段落數量必須與實際進球數匹配：1 個 intro + N 個 goal + 1 個 outro。
"""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "hook": {"type": "string"},
        "hook_alt": {"type": "string"},
        "script_sc": {"type": "string"},
        "script_tc": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["intro", "goal", "outro"]},
                    "idx": {"type": "integer"},
                    "minute": {"type": "string"},
                    "script_sc": {"type": "string"},
                    "script_tc": {"type": "string"},
                    "subtitles_tc": {"type": "array", "items": {"type": "string"}},
                    "subtitle_durations": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["type", "script_sc", "script_tc", "subtitles_tc"],
            },
        },
        "key_moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event": {"type": "string"},
                    "minute": {"type": "string"},
                },
                "required": ["event", "minute"],
            },
        },
        "cover_prompt": {"type": "string"},
    },
    "required": ["titles", "hook", "hook_alt", "script_sc", "script_tc", "segments", "key_moments", "cover_prompt"],
    "additionalProperties": False,
}


def generate(narrative: str, goals: list[dict], cfg: Config) -> dict[str, Any]:
    """調用 script_llm（中转站）生成视频物料，返回完整 JSON 对象。"""
    api_key = cfg.get("script_llm", "api_key", default="")
    base_url = cfg.get("script_llm", "base_url", default="")
    model = cfg.get("script_llm", "model", default="")

    if not api_key or not base_url or not model:
        raise ValueError("config.yaml 缺少 script_llm.api_key / base_url / model")

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    goals_desc = "\n".join(
        f"{g['idx']}. 第{g['minute']}分钟 {g.get('player') or '（球员待确认）'}"
        for g in goals
    ) if goals else "（本场无进球）"

    user_content = f"【战报】\n{narrative}\n\n进球列表（共{len(goals)}个进球，按序号生成对应文案）：\n{goals_desc}"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        max_tokens=4096,
    )
    text = response.choices[0].message.content
    return json.loads(text)


def run(match_id: str, cfg: Config) -> dict[str, Any]:
    """生成视频物料，落盘 script.json，更新 state。"""
    job_dir = cfg.data_dir / match_id
    match_json_path = job_dir / "match.json"

    with match_json_path.open("r", encoding="utf-8") as f:
        match = json.load(f)

    narrative = match.get("narrative") or match.get("report", "")
    if not narrative:
        raise ValueError(f"match {match_id} 无有效战报，请先 scrape")

    goals = match.get("goals", [])
    result = generate(narrative, goals, cfg)

    # 合并比赛元数据
    script: dict[str, Any] = {
        "match_id": match_id,
        "teams": match.get("teams", []),
        "score": match.get("score", ""),
        "titles": result.get("titles", []),
        "hook": result.get("hook", ""),
        "hook_alt": result.get("hook_alt", ""),
        "script_sc": result.get("script_sc", ""),
        "script_tc": result.get("script_tc", ""),
        "segments": result.get("segments", []),
        "key_moments": result.get("key_moments", []),
        "cover_prompt": result.get("cover_prompt", ""),
    }

    script_path = job_dir / "script.json"
    with script_path.open("w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    job = load_job(cfg.data_dir, match_id)
    if not job:
        job = Job(match_id=match_id)
    job.set_status(Status.SCRIPTED)
    save_job(cfg.data_dir, job)

    return {"script": script, "script_path": str(script_path)}


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="生成短视频文案物料")
    ap.add_argument("match_id", help="比赛 ID，如 144922")
    args = ap.parse_args()

    cfg = load_config()
    result = run(args.match_id, cfg)
    s = result["script"]
    print(f"✓ script.json 已生成: {result['script_path']}")
    print(f"\n[Hook] {s['hook'].replace(chr(10), ' / ')}")
    print(f"[简体脚本] {s['script_sc'][:60]}...")
    print(f"[繁体脚本] {s['script_tc'][:60]}...")
    print(f"[段落数] {len(s['segments'])}")
    print(f"[封面提示词] {s['cover_prompt'][:80]}...")


if __name__ == "__main__":
    _main()
