"""阶段3 · 文案生成（script）：根据战报生成结构化解说文案，落盘 script.json。

输出格式：
{
  "intro": "开头文案",
  "goals": [{"idx":1,"minute":"6","player":"C罗","text":"进球文案"}, ...],
  "outro": "结尾文案"
}

独立运行：
    python -m loopwc.stages.script 144922
"""
from __future__ import annotations

import json
from typing import Any

import anthropic

from ..config import Config, load_config
from ..state import Status, load_job, save_job, Job

# ──────────────────────────────────────────────
# Few-shot 样本（结构化 JSON 格式）
# ──────────────────────────────────────────────
_FEW_SHOT_REPORT = """06月24日 世界杯小组赛K组第2轮 葡萄牙vs乌兹别克斯坦 全场录像回放
[进球视频] 塞梅多下底传中变线！莱奥禁区内无人盯防推射再下一城！葡萄牙5-0领先！
[进球视频] B费开出角球造险情！门将内马托夫忙中出乱自摆乌龙！葡萄牙4-0领先！
[进球视频] C罗单刀推射远角破门双响！独享葡萄牙世界杯射手王！葡萄牙3-0领先！
[进球视频] 骗了全世界！C罗佯装主罚任意球，随后门德斯主罚任意球破门！
[进球视频] 历史仅此一人！C罗连续6届世界杯进球！C罗前点抽射破门葡萄牙先下一城！
[射门被挡] 葡萄牙任意球精妙配合！B费送挑传，C罗鬼魅前插打门被门将扑出！
天下足球直播网北京时间6月24日凌晨1:00，美加墨世界杯小组赛K组第二轮，葡萄牙对阵乌兹别克斯坦的比赛在美国休斯敦进行。上半场比赛，C罗在第6分钟首开纪录，连续六届世界杯取得进球创下历史纪录，随后门德斯任意球破门扩大领先优势，C罗再进一球完成梅开二度，半场葡萄牙3-0领先；下半场，B费制造乌龙，莱奥破门扩大比分，最终葡萄牙5-0战胜乌兹别克斯坦。
第6分钟，葡萄牙取得进球，来自C罗！
第16分钟，葡萄牙扩大比分！门德斯任意球破门，葡萄牙2-0领先。
第39分钟，葡萄牙又进球了，还是C罗！葡萄牙3-0领先！
第60分钟，B费角球开到前点，门将内马托夫不慎将球弹进球门，葡萄牙4-0领先乌兹别克斯坦。
第88分钟，葡萄牙又进球了！莱奥轻松破门得手，葡萄牙5-0领先。
最终，葡萄牙5-0战胜乌兹别克斯坦。"""

_FEW_SHOT_GOALS_INPUT = """进球列表（共5个进球，按序号生成对应文案）：
1. 第6分钟 C罗
2. 第16分钟 门德斯
3. 第39分钟 C罗
4. 第60分钟 乌龙球
5. 第88分钟 莱奥"""

_FEW_SHOT_OUTPUT = """{
  "intro": "王者归来，历史留名！葡萄牙以一场5比0的酣畅大胜横扫乌兹别克斯坦，而站在这场胜利最耀眼处的，永远是那个穿着7号球衣的男人。",
  "goal_texts": [
    "开场仅仅6分钟，C罗便点燃了休斯敦之夜！坎塞洛妙传，C罗禁区内前点抽射应声入网，葡萄牙先下一城。更重要的是，这是他连续六届世界杯破门得分，历史上仅此一人，无人能及！",
    "第16分钟，葡萄牙用一记障眼法再添一球——C罗佯装主罚任意球，骗过全场，门德斯突然起脚破门，乌兹别克斯坦防线反应全无，2比0！",
    "第39分钟，B费反击直塞送出精准通球，C罗单刀推射远角破门完成梅开二度，葡萄牙半场便以3比0大幅领先，C罗也独享本队世界杯射手王！",
    "进入下半场，第60分钟，B费前点角球制造险情，乌兹别克斯坦门将内马托夫忙乱中自摆乌龙，4比0！",
    "第88分钟，塞梅多右路下底传中，对方后卫解围失误，莱奥门前轻松推射，5比0盖棺定论！"
  ],
  "outro": "两轮一胜一平，小组出线形势已然明朗。C罗用最强力的方式回应了所有质疑，而这支多点开花、攻守俱佳的葡萄牙，正以最凶悍的姿态向淘汰赛全力冲刺！"
}"""

_SYSTEM_PROMPT = f"""你是一名足球解说文案编辑，专注世界杯赛事短视频内容。根据天下足球直播网提供的战报和进球列表，生成结构化解说文案。

输出格式（严格 JSON）：
{{
  "intro": "开头1-2句，总结本场核心亮点，解说腔有情绪感",
  "goal_texts": ["进球1文案", "进球2文案", ...],
  "outro": "结尾1-2句，升华点评本场意义或展望后续"
}}

goal_texts 数组长度必须与进球数量完全一致，每个元素对应一个进球的完整描述（含分钟数、球员、进球方式，约2-3句）。

写作风格：解说腔，有情绪感，语言生动，可用感叹号。

参考示例：

【战报】
{_FEW_SHOT_REPORT}

{_FEW_SHOT_GOALS_INPUT}

【输出】
{_FEW_SHOT_OUTPUT}"""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "goal_texts": {"type": "array", "items": {"type": "string"}},
        "outro": {"type": "string"},
    },
    "required": ["intro", "goal_texts", "outro"],
    "additionalProperties": False,
}


def generate(narrative: str, goals: list[dict], cfg: Config) -> dict[str, Any]:
    """调用 DeepSeek 生成结构化文案，返回 {intro, goal_texts, outro}。"""
    ds_key = cfg.get("deepseek", "api_key", default="")
    if not ds_key:
        raise ValueError("config.yaml 缺少 deepseek.api_key")

    import openai
    client = openai.OpenAI(
        api_key=ds_key,
        base_url=cfg.get("deepseek", "base_url", default="https://api.deepseek.com/v1"),
    )
    model = cfg.get("deepseek", "model", default="deepseek-chat")

    goals_desc = "\n".join(
        f"{g['idx']}. 第{g['minute']}分钟 {g['player'] or '（球员待确认）'}"
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
        max_tokens=2048,
    )
    text = response.choices[0].message.content
    return json.loads(text)


def run(match_id: str, cfg: Config) -> dict[str, Any]:
    """生成结构化文案，落盘 script.json，更新 state。"""
    job_dir = cfg.data_dir / match_id
    match_json_path = job_dir / "match.json"

    with match_json_path.open("r", encoding="utf-8") as f:
        match = json.load(f)

    narrative = match.get("narrative") or match.get("report", "")
    if not narrative:
        raise ValueError(f"match {match_id} 无有效战报，请先 scrape")

    goals = match.get("goals", [])
    result = generate(narrative, goals, cfg)

    # 合并 LLM 输出和 goals 元数据
    goal_texts = result.get("goal_texts", [])
    script = {
        "match_id": match_id,
        "teams": match.get("teams", []),
        "score": match.get("score", ""),
        "intro": result["intro"],
        "goals": [
            {**g, "text": goal_texts[i] if i < len(goal_texts) else ""}
            for i, g in enumerate(goals)
        ],
        "outro": result["outro"],
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

    ap = argparse.ArgumentParser(description="生成结构化解说文案")
    ap.add_argument("match_id", help="比赛 ID，如 144922")
    args = ap.parse_args()

    cfg = load_config()
    result = run(args.match_id, cfg)
    s = result["script"]
    print(f"✓ script.json 已生成: {result['script_path']}")
    print(f"\n[开头] {s['intro'][:60]}...")
    for g in s["goals"]:
        print(f"[进球{g['idx']}] 第{g['minute']}分钟 {g['player']}: {g['text'][:40]}...")
    print(f"[结尾] {s['outro'][:60]}...")


if __name__ == "__main__":
    _main()
