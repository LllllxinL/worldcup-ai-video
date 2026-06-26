# World Cup AI Video Pipeline

世界杯每日战况 AI 锐评视频自动化流水线。

比赛一结束，系统自动抓取战报、下载全场回放、生成解说文案、AI 配音、自动剪辑，最后导出可发布的短视频。整个链路无需人工干预。

## 流程

```
比赛结束
    |
    v
scrape  --抓取战报、进球列表、视频链接
    |
    v
download --下载全场回放视频
    |
    v
script   --AI 生成结构化解说文案
    |
    v
tts      --分段合成配音
    |
    v
edit     --Palmier MCP 自动剪辑
    |
    v
export   --ffmpeg 合成导出 mp4
```

## 快速开始

### 1. 安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -e .
```

### 2. 配置

复制 `config.example.yaml` 为 `config.yaml`，填入 API 密钥：

```yaml
# 关键配置项
tts:
  api_key: "your-volcano-tts-key"
  speaker: "your-speaker-id"

deepseek:
  api_key: "your-deepseek-key"

edit:
  api_key: "your-anthropic-key"
  model: "claude-sonnet-4-6"
```

### 3. 运行单场比赛

```bash
# 完整流水线
python -m loopwc.orchestrator 144933

# 或分阶段运行
python -m loopwc.stages.scrape 144933
python -m loopwc.stages.script 144933
python -m loopwc.stages.tts 144933
python -m loopwc.stages.edit 144933
python -m loopwc.stages.export 144933
```

### 4. 运行今日所有比赛

```bash
python -m loopwc.orchestrator --today
```

## 项目结构

```
.
├── assets/              # Palmier 模板
├── data/matches/        # 每场比赛工作目录
│   └── {match_id}/
│       ├── match.json      # 战报数据
│       ├── script.json     # AI 文案
│       ├── audio_*.mp3     # 配音文件
│       └── project.palmier # 剪辑项目
├── src/loopwc/
│   ├── stages/          # 6 个阶段
│   │   ├── scrape.py
│   │   ├── download.py
│   │   ├── script.py
│   │   ├── tts.py
│   │   ├── edit.py
│   │   └── export.py
│   ├── agent.py         # OpenAI 兼容 agent
│   ├── benchmark.py     # 多模型 benchmark（WIP）
│   └── orchestrator.py  # 流水线编排
├── config.yaml          # 配置文件（gitignored）
└── pyproject.toml
```

## 各阶段说明

### scrape

抓取天下足球直播网战报，解析进球列表和视频链接。

```bash
python -m loopwc.stages.scrape 144933
```

### download

yt-dlp 下载小红书全场回放。

```bash
python -m loopwc.stages.download 144933
```

### script

DeepSeek 生成结构化解说文案（intro / goals / outro）。

```bash
python -m loopwc.stages.script 144933
```

### tts

豆包语音 V3 分段合成配音。

```bash
python -m loopwc.stages.tts 144933
```

### edit

Palmier MCP Agent 自动剪辑，定位进球画面、对齐配音。

```bash
python -m loopwc.stages.edit 144933
```

### export

ffmpeg 合成最终 mp4。

```bash
python -m loopwc.stages.export 144933
```

## 依赖

- Python >= 3.10
- PalmierPro（本地 MCP 服务）
- ffmpeg
- yt-dlp

Python 包见 `pyproject.toml`。

## 技术栈

- **战报抓取**: requests + BeautifulSoup
- **文案生成**: DeepSeek / Claude
- **语音合成**: 火山引擎豆包语音 V3
- **视频剪辑**: Palmier MCP + claude-agent-sdk
- **视频导出**: ffmpeg
- **调度**: APScheduler

## License

MIT
