# 世界杯 AI 锐评视频 loop 项目交接文档

> 文档目的：当未来重新打开 Claude Code 继续开发时，阅读本文档即可掌握项目目前为止的所有关键信息。
>
> 最后更新：2026-07-15
>
> 项目状态：**已搁置**（等待有应用场景再重启）

---

## 1. 项目是什么

一个把「足球比赛战报 + 全场回放」自动加工成「竖屏 9:16 AI 锐评短视频」的自动化流水线。

核心流程：

```
比赛结束 → scrape 采集 → download 下载 → script 文案 → tts 配音 → edit 剪辑 → export 导出
```

触发方式：watch 模块轮询 API-Football，比赛结束后等待 15 分钟自动触发。

---

## 2. 当前代码与数据状态

### Git 状态

- 分支：只有 `main` 一个分支
- 远程：`https://github.com/LllllxinL/worldcup-ai-video.git`
- 最新提交：包含 event 段支持、minimax 文案方案、繁体后处理、loop_analysis_report.md
- 工作区：干净

### 关键文件

| 文件/目录 | 说明 |
|---|---|
| `src/loopwc/` | 核心代码 |
| `src/loopwc/stages/` | 6 个阶段模块：scrape / download / script / tts / edit / export |
| `src/loopwc/text_overlay.py` | hook/字幕 PNG 渲染 |
| `assets/template_vertical.palmier` | 竖屏 Palmier 模板 |
| `assets/template_horizontal.palmier` | 横屏 Palmier 模板 |
| `config.yaml` | 实际配置（含密钥，已 gitignore） |
| `config.example.yaml` | 配置模板 |
| `loop_analysis_report.md` | 项目分析报告 |
| `data/matches/` | 比赛运行数据（保留 5 个成片） |
| `logs/` | 运行日志 |
| `reports/` | 早期报告 |

### 保留的成片

- `data/matches/144922/144922_final.mp4`
- `data/matches/144928/144928_final.mp4`
- `data/matches/144932/144932_final.mp4`
- `data/matches/144933/144933_final.mp4`
- `data/matches/145001/145001_final.mp4`（最新，event 段支持）

---

## 3. 已验证的工作方案

### 最终确定的模型组合

| 层级 | 方案 | 说明 |
|---|---|---|
| **script** | minimax 主，glm 兜底 | minimax 性价比最高；glm 稳定但慢贵 |
| **tts** | 火山引擎豆包语音 | 成本最低，效果稳定 |
| **edit** | Claude Sonnet（中转站） | 中转站国产模型跑不通，唯一稳定选择 |

### 为什么选 minimax

- script 阶段 8 个模型 benchmark 后，minimax 性价比最高
- 偶发繁体混用，已通过 OpenCC 后处理兜底
- 偶发 segments 结构异常，已通过 `_normalize_segments` 修复

### script 阶段 benchmark 结果（145001 巴西 vs 挪威）

| 模型 | 费用 (USD) | 耗时 | 结果 |
|---|---|---|---|
| gemini-3.5-flash | $0.103602 | 22.1s | 可用但贵 5 倍 |
| gemini-3.1-pro-preview | $0.100950 | 45.9s | 文案好但偏长 |
| glm-5.2 | $0.025346 | 134.8s | 稳定兜底 |
| **minimax-m2.5** | **$0.003674** | **18.7s** | **推荐** |
| deepseek-v4-pro | $0.003024 | 37.7s | hook 不合格 |
| deepseek-v4-flash | $0.000538 | 16.5s | 文案平淡 |
| qwen3.5/3.6 | — | — | 403 无权限 |
| kimi-k2.6 | — | — | 返回空 |

### edit 阶段中转站测试结果

- Qwen：403 无权限
- Kimi：`inspect_media` 转录太长，超 token 上限报 400
- GLM/Gemini：未跑完
- 结论：**edit 仍必须用 Claude Sonnet**

---

## 4. 关键设计决策

### 4.1 为什么 script 和 edit 分开

- script 成本低，可以快速迭代和 A/B 测试
- edit 成本高，需要稳定模型
- 分开测试避免变量过多

### 4.2 为什么加 event 段

用户反馈："点球被扑出"这类关键事件不应该放在 intro，应该和进球一样独立成段。

现在 segments 结构：

```
intro → event_1 → event_2 → ... → goal_1 → goal_2 → ... → outro
```

### 4.3 为什么 intro/outro 固定用庆祝画面

用户明确要求：intro 必须用赛前庆祝/动员，outro 用赛后庆祝/拥抱。具体事件（如点球）单独成 event 段。

### 4.4 为什么不用 Palmier 内置字幕

`text_overlay.py` 用 Pillow 生成 PNG，ffmpeg 独立叠加，更可控，避免 Palmier 导出限制。

### 4.5 为什么繁体用 OpenCC 后处理

minimax 偶发简繁混用，模型层不可控，工程层兜底。

---

## 5. 已知问题与限制

### 5.1 合规风险

- 素材是官方回放二次剪切，TikTok 已提醒违规
- 项目搁置部分原因也是合规风险过高

### 5.2 成本问题

- edit 阶段成本占大头
- Claude Sonnet 一场约 $2-3（用户估算）
- Claude Opus 一场约 $6-8

### 5.3 效果问题

- 用户反馈整体效果"差"
- 进球画面"余量"仍有改进空间
- 成片时长偏短（30s 左右）

### 5.4 稳定性问题

- minimax 偶发 hook 不合格、segments 结构异常
- edit agent 依赖 Claude Sonnet，中转站国产模型不可用
- xhs cookie 可能失效

---

## 6. 原先计划的迭代方向（未实现）

### P0：review agent（审核 Agent）

在 export 之后、发布之前加审核层：

- 文案审核：hook 格式、人名一致性、繁体纯净度、segments 完整性
- 视频审核：画面与配音/字幕同步、进球画面正确性、水印/违规内容

### P0：成本/效果监控

记录每场 script/edit/tts 的 token、耗时、费用，形成数据基础。

### P1：定时调度 + 失败重试

- orchestrator 自动化
- 多场比赛排队
- 失败自动重试

### P1：素材多源 fallback

xhs 失效时自动切咪咕/央视，无视频时降级图文版。

### P2：多平台版本

一次输出 9:16 / 1:1 / 16:9。

### P2：视频审核

用国产多模态模型（Qwen-VL / Kimi K2.7 / GLM-4V）检查成片画面。

### P2：数据反馈闭环

发布后监控播放量、评论等数据，传回输入端驱动优化。

### P3：合规改造

- 减少原片占比
- 增加数据可视化、文字板、AI 生成画面
- 使用公开授权片段

---

## 7. 停工原因

用户明确反馈：

1. **没有应用场景**：短期内无法上线/发布
2. **成本大**：edit 阶段费用高
3. **效果差**：整体成片质量不满意
4. **合规风险**：官方回放画面版权，TikTok 已提醒违规

因此项目搁置，等待未来有明确应用场景和合规解决方案后再重启。

---

## 8. 重启时应该从哪里开始

### 第一步：人工验证 145001 成片

打开 `data/matches/145001/145001_final.mp4`，重点检查：

1. 进球画面"余量"是否足够（球入网/庆祝是否完整）
2. 字幕与配音是否同步
3. event 段（点球被扑出）画面是否正确
4. hook 显示位置和时长

### 第二步：确认成本

根据中转站后台记录，计算 145001 完整跑通的 script + edit 总成本。

### 第三步：决定重启范围

根据成本/效果/合规三方面的评估，决定：

- 是否继续投入
- 是否换内容方向（历史回顾、数据分析等）
- 是否换素材来源（授权片段、AI 生成画面）

### 第四步：优先实现 review agent

如果决定重启，先做文案审核，拦截低质量产出。

---

## 9. 重要代码修改记录

### script.py

- 新增 `event` 段类型
- 新增 `_normalize_traditional`：OpenCC 繁体后处理
- 新增 `_normalize_segments`：修复重复 intro、缺失 outro
- `generate` 增加 events 参数和 segments 完整性校验/重试

### edit.py

- prompt 增加 event 段处理
- 增加"余量"要求：画面要比核心事件句更长
- intro 固定赛前庆祝，outro 固定赛后庆祝
- max_turns 恢复为 300

### tts.py

- 支持 `event` 段生成 `audio_event_N.mp3`

### export.py

- 无修改，天然兼容 event 段

### scrape.py / txzqzhibo.py

- 修复进球解析：按球员+动作关键词匹配
- 保留 download 阶段已写入的 video_path

---

## 10. 环境依赖

- Python 3.14（.venv）
- ffmpeg / ffprobe
- yt-dlp
- Palmier Pro（本地 MCP，端口 19789）
- OpenCC（`pip install opencc-python-reimplemented`）
- requests / beautifulsoup4 / openai / websockets / volcengine_audio

---

## 11. 快速启动命令

```bash
cd /Users/aa00083/Desktop/loop项目
source .venv/bin/activate

# 完整流程（需先配置 config.yaml）
python -m loopwc.stages.scrape 145001
python -m loopwc.stages.download 145001
# script 用 minimax 时需临时改 config 里 script_llm.model
python -m loopwc.stages.tts 145001
python -m loopwc.stages.edit 145001
python -m loopwc.stages.export 145001
```

---

## 12. 其他信息

- **世界杯时间**：2026 年 6-7 月
- **最后验证比赛**：145001 巴西 1-2 挪威（2026-07-06）
- **项目搁置日期**：2026-07-15
- **文档生成方式**：Claude Code 自动生成
