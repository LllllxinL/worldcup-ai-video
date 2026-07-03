"""文字 overlay 渲染：用 Pillow 生成带透明通道的标题/字幕 PNG，供 ffmpeg overlay 使用。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .config import Config


def _load_font(font_path: str, font_index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, size, index=font_index)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke_width: int) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def render_text_png(
    text: str,
    font_path: str,
    font_index: int,
    font_size: int,
    text_color: tuple[int, int, int, int],
    stroke_color: tuple[int, int, int, int],
    stroke_width: int,
    canvas_width: int,
    max_width: int | None = None,
    line_spacing: float = 1.2,
) -> Image.Image:
    """渲染多行文字为透明 PNG，宽度超过 max_width 时整体等比缩放。"""
    font = _load_font(font_path, font_index, font_size)

    lines = text.split("\n")
    tmp = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)

    line_sizes = [_text_size(draw, line, font, stroke_width) for line in lines]
    max_w = max((w for w, _ in line_sizes), default=0)
    total_h = sum(h for _, h in line_sizes)
    if len(lines) > 1:
        total_h += int(sum(h for _, h in line_sizes[:-1]) * (line_spacing - 1.0))

    # 左右留边距，上下根据描边留边距
    padding_x = 40
    padding_y = stroke_width * 2 + 10
    img_w = max_w + padding_x * 2
    img_h = total_h + padding_y * 2

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = padding_y
    for i, line in enumerate(lines):
        w, h = line_sizes[i]
        x = (img_w - w) / 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
        )
        y += int(h * line_spacing)

    # 整体缩放，确保不超出最大宽度
    effective_max = max_width if max_width else canvas_width
    if img_w > effective_max:
        scale = effective_max / img_w
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    return img


def _resolve_color(cfg: Config, key: str, default: str) -> tuple[int, int, int, int]:
    raw = cfg.get("edit", key, default=default)
    raw = raw.strip()
    if raw.startswith("#"):
        raw = raw[1:]
        if len(raw) == 6:
            return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        if len(raw) == 8:
            return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4, 6))
    # 简单支持逗号分隔 rgba
    parts = [int(p.strip()) for p in raw.split(",")]
    if len(parts) == 3:
        return tuple(parts) + (255,)
    if len(parts) == 4:
        return tuple(parts)
    # 兜底白色
    return (255, 255, 255, 255)


def render_hook_png(
    text: str,
    cfg: Config,
    canvas_width: int,
) -> Image.Image:
    font_path = cfg.get("edit", "font_path", default="/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc")
    font_index = int(cfg.get("edit", "font_index", default=7))
    font_size = int(cfg.get("edit", "hook_font_size", default=54))
    text_color = _resolve_color(cfg, "hook_text_color", "255,255,255,255")
    stroke_color = _resolve_color(cfg, "hook_stroke_color", "0,0,0,255")
    stroke_width = int(cfg.get("edit", "hook_stroke_width", default=3))
    margin = int(cfg.get("edit", "hook_side_margin", default=40))
    max_width = canvas_width - margin * 2
    return render_text_png(text, font_path, font_index, font_size, text_color, stroke_color, stroke_width, canvas_width, max_width=max_width)


def render_subtitle_png(
    text: str,
    cfg: Config,
    canvas_width: int,
) -> Image.Image:
    font_path = cfg.get("edit", "font_path", default="/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc")
    font_index = int(cfg.get("edit", "font_index", default=7))
    font_size = int(cfg.get("edit", "subtitle_font_size", default=48))
    text_color = _resolve_color(cfg, "subtitle_text_color", "255,255,255,255")
    stroke_color = _resolve_color(cfg, "subtitle_stroke_color", "0,0,0,255")
    stroke_width = int(cfg.get("edit", "subtitle_stroke_width", default=2))
    margin = int(cfg.get("edit", "subtitle_side_margin", default=40))
    max_width = canvas_width - margin * 2
    return render_text_png(text, font_path, font_index, font_size, text_color, stroke_color, stroke_width, canvas_width, max_width=max_width)


def save_png(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), format="PNG")
    return path
