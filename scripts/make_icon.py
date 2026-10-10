#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_icon.py - 生成 SkillHub 要求的 512x512 图标。

设计语义：三块**分离悬浮**的等距板 = 把目标逐层拆开下钻（REA 五层还原法的视觉化）；
最上层最亮表示"已还原到可解释层"，板间缝隙表示"层与层之间的取证边界"；
向上的箭头表示从表象向内部下钻。

依赖：pillow（仅用于生成图标，skill 运行时不需要）。
用法：python3 scripts/make_icon.py [输出路径]
"""
from __future__ import annotations

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
BG_TOP = (18, 32, 58)        # 渐变起始（左上）
BG_BOTTOM = (6, 9, 18)       # 渐变结束（右下）
GLOW = (56, 132, 220)        # 中心光晕色
PLATES = [
    {"top": (30, 58, 96), "side": (18, 34, 60), "edge": (72, 130, 200)},   # 底层：暗
    {"top": (42, 96, 158), "side": (24, 52, 92), "edge": (110, 178, 240)},  # 中层
    {"top": (86, 168, 240), "side": (38, 88, 152), "edge": (178, 224, 255)},  # 顶层：亮
]


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=radius, fill=255)
    return mask


def gradient_background(size: int) -> Image.Image:
    """对角渐变 + 中心光晕。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    cx = cy = size / 2.0
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * size)
            r = BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t
            g = BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t
            b = BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t
            d = math.hypot(x - cx, y - cy) / (size * 0.72)
            glow = max(0.0, 1.0 - d) ** 2.2
            r = r + (GLOW[0] - r) * glow * 0.55
            g = g + (GLOW[1] - g) * glow * 0.55
            b = b + (GLOW[2] - b) * glow * 0.55
            px[x, y] = (int(r), int(g), int(b))
    return img


def iso_plate(draw: ImageDraw.ImageDraw, cx: float, cy: float,
              half_w: float, half_h: float, thick: int, colors: dict) -> None:
    """画一块等距板：顶面菱形 + 左右侧面 + 描边。"""
    top = (cx, cy - half_h)
    right = (cx + half_w, cy)
    bottom = (cx, cy + half_h)
    left = (cx - half_w, cy)
    # 侧面（下沉 thick）
    draw.polygon([left, bottom, (bottom[0], bottom[1] + thick),
                  (left[0], left[1] + thick)], fill=colors["side"])
    draw.polygon([bottom, right, (right[0], right[1] + thick),
                  (bottom[0], bottom[1] + thick)], fill=colors["side"])
    # 顶面
    draw.polygon([top, right, bottom, left], fill=colors["top"])
    draw.line([top, right, bottom, left, top], fill=colors["edge"], width=3, joint="curve")


def _max_span(half_w: float, half_h: float, fc: float) -> float:
    """斜率 k 的线段以菱形纵轴上 fc 处为中点时，两端不出菱形的最大半长（二分求解）。"""
    k = half_h / half_w

    def ok(L: float) -> bool:
        for sgn in (-1.0, 1.0):
            f = fc + sgn * (L * k) / (2 * half_h)
            if L > half_w * (1 - abs(2 * f - 1)) * 0.96:
                return False
        return True

    lo, hi = 0.0, half_w
    for _ in range(28):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def data_lines(draw: ImageDraw.ImageDraw, cx: float, cy: float,
               half_w: float, half_h: float, color: tuple, n: int = 3) -> None:
    """板面上画几条平行于等距边的短线，象征数据/代码行（端点严格约束在菱形内）。"""
    k = half_h / half_w
    for i in range(n):
        fc = (i + 1) / (n + 1.0)          # 中点在菱形纵轴上的比例位置
        y = cy - half_h + 2 * half_h * fc
        span = min(0.52 * half_w, _max_span(half_w, half_h, fc)) * 0.92
        draw.line([(cx - span, y - span * k), (cx + span, y + span * k)],
                  fill=color, width=4)


def build(path: str) -> str:
    img = gradient_background(SIZE).convert("RGBA")

    cx = SIZE / 2.0
    half_w, half_h, thick = 120.0, 60.0, 15
    gap = 30          # 板间缝隙：取证边界
    base_cy = 348

    # 先画各层投影（下移 + 模糊），让「上层板悬浮在下层上方」的关系一眼可读
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    off = thick + 12
    for i in range(len(PLATES)):
        cy = base_cy - i * (half_h + gap)
        sdraw.polygon([(cx, cy - half_h + off), (cx + half_w, cy + off),
                       (cx, cy + half_h + off), (cx - half_w, cy + off)],
                      fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    img = Image.alpha_composite(img, shadow)

    draw = ImageDraw.Draw(img)
    # 自下而上绘制，保证遮挡关系正确
    for i, colors in enumerate(PLATES):
        cy = base_cy - i * (half_h + gap)
        iso_plate(draw, cx, cy, half_w, half_h, thick, colors)
        line_color = tuple(min(255, c + 46) for c in colors["edge"])
        data_lines(draw, cx, cy, half_w, half_h, line_color, n=3)

    # 顶部下钻箭头（指向最上层，表示向下还原）
    arrow_y = 66
    aw, ah = 22, 34
    draw.polygon([(cx, arrow_y + ah), (cx - aw, arrow_y), (cx - aw * 0.34, arrow_y),
                  (cx - aw * 0.34, arrow_y - ah * 0.55), (cx + aw * 0.34, arrow_y - ah * 0.55),
                  (cx + aw * 0.34, arrow_y), (cx + aw, arrow_y)],
                 fill=(150, 214, 255))

    # 柔光：让图标在深色 UI 上不死板
    img = img.filter(ImageFilter.GaussianBlur(0.4))

    # 圆角 + 透明底
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(img, (0, 0), rounded_mask(SIZE, 96))
    out.save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon.png")
    p = build(target)
    from PIL import Image as _I
    print("icon -> %s (%s)" % (p, _I.open(p).size))
