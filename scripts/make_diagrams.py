#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_diagrams.py - 生成文档配图（架构图、五层还原法图、终端演示图），支持多语言。

用法：
  python3 scripts/make_diagrams.py                # 生成全部语言
  python3 scripts/make_diagrams.py --lang en      # 只生成英文

输出：docs/images/architecture-<lang>.png、five-layers-<lang>.png、demo-peek.png

依赖：Pillow（必需）；arabic-reshaper + python-bidi（仅阿拉伯语需要，缺失时自动降级）。
本脚本只用于生成文档配图，skill 运行时不需要它，也不需要这些依赖。
"""
from __future__ import annotations

import argparse
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "docs", "images")

# --------------------------------------------------------------------------- #
# 配色
# --------------------------------------------------------------------------- #
BG = (247, 249, 252)
INK = (23, 32, 48)
MUTED = (104, 116, 136)
BLUE = (37, 99, 175)
BLUE_SOFT = (222, 234, 250)
BLUE_LINE = (110, 164, 224)
AMBER = (176, 108, 20)
AMBER_SOFT = (253, 238, 214)
RED = (168, 46, 46)
RED_SOFT = (250, 226, 226)
GREEN = (30, 116, 88)
GREEN_SOFT = (219, 240, 231)
WHITE = (255, 255, 255)

FONT_FILES = {
    "zh": ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2,
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    "ja": ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0,
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    "ar": ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", 0,
           "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf", 0),
    "en": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0,
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    "es": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0,
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
}
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

_font_cache: dict = {}


def font(lang: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (lang, size, bold)
    if key not in _font_cache:
        reg, ri, bo, bi = FONT_FILES[lang]
        path, idx = (bo, bi) if bold else (reg, ri)
        _font_cache[key] = ImageFont.truetype(path, size, index=idx)
    return _font_cache[key]


def prepare(lang: str, text: str) -> str:
    """阿拉伯语需要做字母整形与双向排布，其余语言原样返回。"""
    if lang != "ar":
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def tw(draw, text: str, f) -> int:
    b = draw.textbbox((0, 0), text, font=f)
    return b[2] - b[0]


def th(draw, text: str, f) -> int:
    b = draw.textbbox((0, 0), text, font=f)
    return b[3] - b[1]


def wrap_by_width(draw, text: str, f, max_w: int) -> list:
    """按像素宽度换行；CJK 按字符断行，拉丁/阿拉伯按词断行。"""
    if tw(draw, text, f) <= max_w:
        return [text]
    lines, cur = [], ""
    for ch in text:
        if tw(draw, cur + ch, f) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


_CUR_LANG = "en"          # 当前绘制语言（阿拉伯语走混排管线）


def _is_ar(ch: str) -> bool:
    o = ord(ch)
    return (0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or
            0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF)


def _seg(s: str) -> list:
    """把视觉序文本切成连续同脚本段：[(片段, 是否阿拉伯), ...]"""
    segs, cur, cur_ar = [], "", None
    for ch in s:
        a = _is_ar(ch)
        if cur_ar is None or a == cur_ar:
            cur += ch
            cur_ar = a
        else:
            segs.append((cur, cur_ar))
            cur, cur_ar = ch, a
    if cur:
        segs.append((cur, cur_ar))
    return segs


def _mixed_width(draw, visual: str, f_ar, f_lat) -> float:
    return sum(draw.textlength(seg, font=(f_ar if a else f_lat))
               for seg, a in _seg(visual))


def _draw_ar_line(draw, x, y, visual, f_ar, f_lat, fill, align):
    """按脚本分段选字体绘制一行已定形的阿拉伯语文本（视觉序，从左到右）。"""
    w = _mixed_width(draw, visual, f_ar, f_lat)
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    for seg, a in _seg(visual):
        f = f_ar if a else f_lat
        draw.text((x, y), seg, font=f, fill=fill)
        x += draw.textlength(seg, font=f)


def draw_text_block(draw, xy, text, f, fill, max_w=None, align="center", lh=None):
    """绘制多行文本块，返回整体高度。align: center / left / right（RTL 语言用 right）。"""
    global _CUR_LANG
    x, y = xy
    total = 0
    if _CUR_LANG == "ar":
        # 阿拉伯语：逻辑序上按词断行，每行独立定形；拉丁/数字段用 DejaVu 回退
        f_lat = font("en", f.size)
        lh = lh or int(f.size * 1.7)
        for para in text.split("\n"):
            words = para.split(" ")
            lines, cur = [], ""
            for w_ in words:
                cand = (cur + " " + w_).strip()
                if max_w and _mixed_width(draw, prepare("ar", cand), f, f_lat) > max_w and cur:
                    lines.append(cur)
                    cur = w_
                else:
                    cur = cand
            lines.append(cur)
            for ln in lines:
                _draw_ar_line(draw, x, y, prepare("ar", ln), f, f_lat, fill, align)
                y += lh
                total += lh
        return total
    lh = lh or int(f.size * 1.35)
    for para in text.split("\n"):
        lines = wrap_by_width(draw, para, f, max_w) if max_w else [para]
        for ln in lines:
            w = tw(draw, ln, f)
            if align == "center":
                px = x - w // 2
            elif align == "right":
                px = x - w
            else:
                px = x
            draw.text((px, y), ln, font=f, fill=fill)
            y += lh
            total += lh
    return total


def box(draw, xy, fill, outline, radius=12, width=2):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def arrow_down(draw, x, y0, y1, color=BLUE_LINE, head=9):
    draw.line([(x, y0), (x, y1)], fill=color, width=3)
    draw.polygon([(x, y1), (x - head, y1 - head), (x + head, y1 - head)], fill=color)


# --------------------------------------------------------------------------- #
# 文案
# --------------------------------------------------------------------------- #
L = {
    "zh": {
        "rtl": False,
        "arch_title": "REA 专家团 · 决策流程",
        "target": "未知目标",
        "target_sub": "二进制 / 报文 / 仓库 / 数据 / 产品 / 流程",
        "survey": "① peek.py 只读勘察",
        "survey_sub": "魔数 · 熵值 · 字符串 · 结构探测 · 目录测绘",
        "hub": "② 决策中枢 rea_engine.py",
        "hub_sub": "route 选专家 → prompt 出题 → validate 过闸门 → consensus 融合",
        "experts_title": "③ 领域专家作答（含证据等级 E0–E3）",
        "experts": [
            ("结构解剖", "evidence_gap"),
            ("行为动态", "needs_dynamic_probe"),
            ("二进制逆向", "legal_or_safety_risk"),
            ("协议解码", "speculation_risk"),
            ("意图还原", "narrative_risk"),
            ("复刻规划", "blocking_unknowns"),
        ],
        "gate_evi": "证据不足闸门",
        "gate_evi_sub": "升级 System 2 取证\n禁止下结论",
        "gate_human": "法律 / 安全闸门",
        "gate_human_sub": "停止自动推进\n转人工确认",
        "out": "④ 五层逆向报告（未验证内容进「未知与下一步」）",
        "layers_title": "五层还原法 · 逐层推进，不允许跳层",
        "layer_q": "要回答的问题",
        "layer_owner": "主责专家",
        "layers": [
            ("L1 · 是什么", "形态 / 来源 / 授权状态", "结构解剖"),
            ("L2 · 由什么组成", "部件 / 边界 / 依赖 / 入口", "结构解剖"),
            ("L3 · 怎么运作", "输入输出 / 状态 / 字段语义", "行为动态 · 协议解码"),
            ("L4 · 为什么这样", "约束 / 取舍 / 设计意图", "意图还原"),
            ("L5 · 怎么复刻", "契约 / 结构 / 伪代码 / 验收", "复刻规划"),
        ],
        "layer_note": "下层未看清就谈上层，产出的通常是故事而不是结论",
    },
    "en": {
        "rtl": False,
        "arch_title": "REA Expert Council · Decision Flow",
        "target": "Unknown target",
        "target_sub": "binary / packet / repo / data / product / process",
        "survey": "① peek.py read-only survey",
        "survey_sub": "magic · entropy · strings · structure probe · directory map",
        "hub": "② Decision hub rea_engine.py",
        "hub_sub": "route → prompt → validate → consensus",
        "experts_title": "③ Domain experts answer (with evidence level E0–E3)",
        "experts": [
            ("Structure", "evidence_gap"),
            ("Behavior", "needs_dynamic_probe"),
            ("Binary", "legal_or_safety_risk"),
            ("Protocol", "speculation_risk"),
            ("Intent", "narrative_risk"),
            ("Rebuild", "blocking_unknowns"),
        ],
        "gate_evi": "Evidence gate",
        "gate_evi_sub": "Escalate to System 2\nNo conclusion yet",
        "gate_human": "Legal / safety gate",
        "gate_human_sub": "Stop auto-execution\nEscalate to human",
        "out": "④ Five-layer report (unverified content goes to Open Questions)",
        "layers_title": "Five-Layer Method · Advance layer by layer, never skip",
        "layer_q": "Question to answer",
        "layer_owner": "Owner",
        "layers": [
            ("L1 · What is it", "form / source / authorization", "Structure"),
            ("L2 · What is it made of", "parts / boundaries / deps / entry", "Structure"),
            ("L3 · How it works", "I/O / state / field semantics", "Behavior · Protocol"),
            ("L4 · Why this way", "constraints / tradeoffs / intent", "Intent"),
            ("L5 · How to rebuild", "contract / structures / pseudocode / tests", "Rebuild"),
        ],
        "layer_note": "Judging L4 before L2 is settled produces stories, not conclusions",
    },
    "ar": {
        "rtl": True,
        "arch_title": "مجلس خبراء REA · مسار القرار",
        "target": "هدف غير معروف",
        "target_sub": "ثنائي / رزم / مستودع / بيانات / منتج / عملية",
        "survey": "① مسح للقراءة فقط peek.py",
        "survey_sub": "التوقيع · الإنتروبيا · النصوص · البنية · خريطة المجلدات",
        "hub": "② مركز القرار rea_engine.py",
        "hub_sub": "route → prompt → validate → consensus",
        "experts_title": "③ إجابات الخبراء (مع مستوى الدليل E0–E3)",
        "experts": [
            ("البنية", "evidence_gap"),
            ("السلوك", "needs_dynamic_probe"),
            ("الثنائيات", "legal_or_safety_risk"),
            ("البروتوكول", "speculation_risk"),
            ("النية", "narrative_risk"),
            ("إعادة البناء", "blocking_unknowns"),
        ],
        "gate_evi": "بوابة الدليل",
        "gate_evi_sub": "ترقية إلى System 2\nلا استنتاج بعد",
        "gate_human": "بوابة قانونية / أمنية",
        "gate_human_sub": "إيقاف التنفيذ التلقائي\nإحالة إلى الإنسان",
        "out": "④ تقرير من خمس طبقات (غير المؤكد يذهب إلى أسئلة مفتوحة)",
        "layers_title": "منهج الطبقات الخمس · تقدم طبقة بطبقة دون قفز",
        "layer_q": "السؤال المطلوب",
        "layer_owner": "الخبير المسؤول",
        "layers": [
            ("L1 · ما هو", "الشكل / المصدر / التصريح", "البنية"),
            ("L2 · مم يتكوّن", "الأجزاء / الحدود / الاعتماديات", "البنية"),
            ("L3 · كيف يعمل", "المدخلات والمخرجات / الحالة / الحقول", "السلوك · البروتوكول"),
            ("L4 · لماذا هكذا", "القيود / المقايضات / النية", "النية"),
            ("L5 · كيف نعيد بناءه", "العقد / البنى / الشفرة الوهمية / الاختبارات", "إعادة البناء"),
        ],
        "layer_note": "الحكم على الطبقة الرابعة قبل حسم الثانية ينتج قصصًا لا استنتاجات",
    },
    "ja": {
        "rtl": False,
        "arch_title": "REA エキスパート会議 · 意思決定フロー",
        "target": "未知の対象",
        "target_sub": "バイナリ / パケット / リポジトリ / データ / 製品 / プロセス",
        "survey": "① peek.py 読み取り専用調査",
        "survey_sub": "マジック · エントロピー · 文字列 · 構造解析 · ディレクトリ",
        "hub": "② 意思決定中枢 rea_engine.py",
        "hub_sub": "route → prompt → validate → consensus",
        "experts_title": "③ 各専門家が回答（証拠レベル E0–E3 付き）",
        "experts": [
            ("構造解剖", "evidence_gap"),
            ("挙動分析", "needs_dynamic_probe"),
            ("バイナリ解析", "legal_or_safety_risk"),
            ("プロトコル解析", "speculation_risk"),
            ("意図復元", "narrative_risk"),
            ("再構築", "blocking_unknowns"),
        ],
        "gate_evi": "証拠不足ゲート",
        "gate_evi_sub": "System 2 の追加調査へ\n結論はまだ出さない",
        "gate_human": "法務 / 安全ゲート",
        "gate_human_sub": "自動進行を停止\n人間へエスカレーション",
        "out": "④ 5 層レポート（未検証は「未解決事項」へ）",
        "layers_title": "5 層復元法 · 層を飛ばさず順に進める",
        "layer_q": "答えるべき問い",
        "layer_owner": "主担当",
        "layers": [
            ("L1 · 何か", "形態 / 出所 / 権限", "構造解剖"),
            ("L2 · 構成要素", "部品 / 境界 / 依存 / 入口", "構造解剖"),
            ("L3 · どう動く", "入出力 / 状態 / フィールド意味", "挙動 · プロトコル"),
            ("L4 · なぜこの形か", "制約 / トレードオフ / 意図", "意図復元"),
            ("L5 · どう再現するか", "契約 / 構造 / 疑似コード / 検収", "再構築"),
        ],
        "layer_note": "L2 が固まらないうちに L4 を語ると、結論ではなく物語になる",
    },
    "es": {
        "rtl": False,
        "arch_title": "Consejo de expertos REA · Flujo de decisión",
        "target": "Objeto desconocido",
        "target_sub": "binario / paquete / repositorio / datos / producto / proceso",
        "survey": "① Inspección de solo lectura peek.py",
        "survey_sub": "magia · entropía · cadenas · sondeo de estructura · mapa de carpetas",
        "hub": "② Núcleo de decisión rea_engine.py",
        "hub_sub": "route → prompt → validate → consensus",
        "experts_title": "③ Respuesta de los expertos (con nivel de evidencia E0–E3)",
        "experts": [
            ("Estructura", "evidence_gap"),
            ("Comportamiento", "needs_dynamic_probe"),
            ("Binario", "legal_or_safety_risk"),
            ("Protocolo", "speculation_risk"),
            ("Intención", "narrative_risk"),
            ("Reconstrucción", "blocking_unknowns"),
        ],
        "gate_evi": "Umbral de evidencia",
        "gate_evi_sub": "Escalar a System 2\nAún sin conclusión",
        "gate_human": "Umbral legal / de seguridad",
        "gate_human_sub": "Detener ejecución automática\nEscalar a humano",
        "out": "④ Informe de cinco capas (lo no verificado va a Preguntas abiertas)",
        "layers_title": "Método de cinco capas · avanzar capa por capa, sin saltos",
        "layer_q": "Pregunta a responder",
        "layer_owner": "Responsable",
        "layers": [
            ("L1 · Qué es", "forma / origen / autorización", "Estructura"),
            ("L2 · De qué está hecho", "partes / límites / dependencias / entrada", "Estructura"),
            ("L3 · Cómo funciona", "E/S / estado / semántica de campos", "Comportamiento · Protocolo"),
            ("L4 · Por qué así", "restricciones / compensaciones / intención", "Intención"),
            ("L5 · Cómo reconstruirlo", "contrato / estructuras / pseudocódigo / pruebas", "Reconstrucción"),
        ],
        "layer_note": "Juzgar L4 sin haber cerrado L2 produce relatos, no conclusiones",
    },
}


# --------------------------------------------------------------------------- #
# 图 1：架构图
# --------------------------------------------------------------------------- #
def make_architecture(lang: str, path: str) -> str:
    global _CUR_LANG
    _CUR_LANG = lang
    t = L[lang]
    rtl = t["rtl"]
    al = "center"          # 架构图内所有文本一律相对锚点居中，RTL 不改变盒内对齐
    W, H = 1240, 990
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = font(lang, 27, True)
    f_h = font(lang, 20, True)
    f_s = font(lang, 14)
    f_xs = font(lang, 13)
    # 等宽字体仅覆盖拉丁字符；CJK/阿拉伯语退回各自的无衬线字体
    f_mono = (ImageFont.truetype(MONO, 12) if lang in ("en", "es")
              else font(lang, 14))

    # 标题
    draw_text_block(d, (W // 2, 26), prepare(lang, t["arch_title"]), f_title, INK,
                    max_w=W - 80, align=al)

    def node(x0, y0, x1, y1, title, sub, fill, line, tf, sf, mono=False):
        box(d, (x0, y0, x1, y1), fill, line, radius=14, width=2)
        cy = y0 + 16
        cy += draw_text_block(d, ((x0 + x1) // 2, cy), prepare(lang, title), tf, INK,
                              max_w=x1 - x0 - 32, align=al)
        if sub:
            cy += 6
            sf2 = f_mono if mono else sf
            draw_text_block(d, ((x0 + x1) // 2, cy), prepare(lang, sub), sf2, MUTED,
                            max_w=x1 - x0 - 32, align=al)

    # ① 未知目标
    node(340, 82, 900, 158, t["target"], t["target_sub"], BLUE_SOFT, BLUE_LINE, f_h, f_s)
    arrow_down(d, 620, 158, 196)

    # ② peek 勘察
    node(340, 196, 900, 274, t["survey"], t["survey_sub"], GREEN_SOFT, GREEN, f_h, f_s)
    arrow_down(d, 620, 274, 312)

    # ③ 决策中枢
    node(300, 312, 940, 396, t["hub"], t["hub_sub"], BLUE_SOFT, BLUE, f_h, f_s, mono=True)
    arrow_down(d, 620, 396, 434)

    # 专家区
    draw_text_block(d, (W // 2, 440), prepare(lang, t["experts_title"]), f_h, INK,
                    max_w=W - 80, align=al)
    cols, ew, eh, gx, gy = 3, 300, 104, 26, 24
    x_start = (W - (cols * ew + (cols - 1) * gx)) // 2
    y_start = 486
    for i, (name, gate_) in enumerate(t["experts"]):
        r, c = divmod(i, cols)
        x0 = x_start + c * (ew + gx)
        y0 = y_start + r * (eh + gy)
        box(d, (x0, y0, x0 + ew, y0 + eh), WHITE, BLUE_LINE, radius=12, width=2)
        cy = y0 + 14
        cy += draw_text_block(d, (x0 + ew // 2, cy), prepare(lang, name), f_h, BLUE,
                              max_w=ew - 24, align=al)
        draw_text_block(d, (x0 + ew // 2, cy + 4), gate_, f_mono, MUTED,
                        max_w=ew - 24, align=al)

    # 闸门（右下）
    gy0 = y_start + 2 * (eh + gy) + 22
    gw = (W - 2 * x_start - 24) // 2
    box(d, (x_start, gy0, x_start + gw, gy0 + 92), AMBER_SOFT, AMBER, radius=12, width=2)
    cy = gy0 + 14
    cy += draw_text_block(d, (x_start + gw // 2, cy), prepare(lang, t["gate_evi"]),
                          f_h, AMBER, max_w=gw - 24, align=al)
    draw_text_block(d, (x_start + gw // 2, cy), prepare(lang, t["gate_evi_sub"]),
                    f_s, AMBER, max_w=gw - 24, align=al)

    gx2 = x_start + gw + 24
    box(d, (gx2, gy0, gx2 + gw, gy0 + 92), RED_SOFT, RED, radius=12, width=2)
    cy = gy0 + 14
    cy += draw_text_block(d, (gx2 + gw // 2, cy), prepare(lang, t["gate_human"]),
                          f_h, RED, max_w=gw - 24, align=al)
    draw_text_block(d, (gx2 + gw // 2, cy), prepare(lang, t["gate_human_sub"]),
                    f_s, RED, max_w=gw - 24, align=al)

    # 输出
    oy = gy0 + 92 + 26
    node(240, oy, 1000, oy + 62, t["out"], None, WHITE, GREEN, f_h, f_s)

    img.save(path, "PNG", optimize=True)
    return path


# --------------------------------------------------------------------------- #
# 图 2：五层还原法
# --------------------------------------------------------------------------- #
def make_layers(lang: str, path: str) -> str:
    global _CUR_LANG
    _CUR_LANG = lang
    t = L[lang]
    rtl = t["rtl"]
    W = 1100
    rows = len(t["layers"])
    H = 150 + rows * 96 + 90
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = font(lang, 27, True)
    f_lv = font(lang, 20, True)
    f_s = font(lang, 15)
    f_xs = font(lang, 13)
    f_mono = ImageFont.truetype(MONO, 12)

    draw_text_block(d, (W // 2, 26), prepare(lang, t["layers_title"]), f_title, INK,
                    max_w=W - 80, align="center")

    x0, x1 = 70, W - 70
    # 行内锚点：RTL 语言整体镜像（标题靠右、主责专家靠左）
    tx, ta = (x1 - 24, "right") if rtl else (x0 + 24, "left")
    ox, oa = (x0 + 24, "left") if rtl else (x1 - 24, "right")
    # 表头
    hy = 92
    draw_text_block(d, (tx, hy), prepare(lang, t["layer_q"]), f_xs, MUTED, align=ta)
    draw_text_block(d, (ox, hy), prepare(lang, t["layer_owner"]), f_xs, MUTED, align=oa)

    palette = [BLUE_SOFT, (226, 236, 250), (232, 241, 252), (238, 245, 253), GREEN_SOFT]
    y = hy + 34
    for i, (lv, q, owner) in enumerate(t["layers"]):
        h = 82
        box(d, (x0, y, x1, y + h), palette[i], (200, 214, 236) if i < 3 else GREEN,
            radius=12, width=2)
        # 层号
        draw_text_block(d, (tx, y + 20), prepare(lang, lv), f_lv, BLUE if i < 4 else GREEN,
                        max_w=(x1 - x0) - 320, align=ta)
        # 问题
        draw_text_block(d, (tx, y + 50), prepare(lang, q), f_s, INK,
                        max_w=(x1 - x0) - 320, align=ta)
        # 主责专家（另一侧）
        draw_text_block(d, (ox, y + 34), prepare(lang, owner), f_s, MUTED,
                        max_w=250, align=oa)
        if i < rows - 1:
            arrow_down(d, (x0 + x1) // 2, y + h, y + h + 12, (180, 196, 220), head=7)
        y += h + 14

    draw_text_block(d, (W // 2, y + 6), prepare(lang, t["layer_note"]), f_xs, MUTED,
                    max_w=W - 120, align="center")
    img.save(path, "PNG", optimize=True)
    return path


# --------------------------------------------------------------------------- #
# 图 3：终端演示
# --------------------------------------------------------------------------- #
DEMO = """$ python3 scripts/peek.py /bin/ls
====================================================================
REA target scan: /bin/ls
====================================================================
Size         : 139.0KB (142312 bytes)
Magic        : ELF (Unix executable / object)
Shannon ent. : 5.9351  -> moderate: mixed or uncompressed binary
Structure    : ELF
  . class        64-bit
  . endian       little-endian
  . type         DYN
  . machine      x86-64
  . entry        0x6d30
  . has_symtab_hint True

$ python3 scripts/rea_engine.py route \\
    --state 'a stripped ELF x86-64 binary, no symbols, unclear provenance'
binary-reverser      Binary Reverser Expert   match=2 weighted=2.8
structure-anatomist  Structure Anatomist      match=0 weighted=0.0

$ python3 scripts/rea_engine.py validate ... (legal gate hit)
  escalate   : True
  escalate_to: human
  human_flags: ['legal_or_safety_risk']
  note       : Legal/safety gate hit - stop and confirm with a human."""


def make_demo(path: str) -> str:
    f = ImageFont.truetype(MONO, 15)
    pad = 28
    lines = DEMO.split("\n")
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    line_h = int(f.size * 1.45)
    w = max(tw(tmp, ln, f) for ln in lines) + pad * 2
    h = len(lines) * line_h + pad * 2 + 42
    img = Image.new("RGB", (w, h), (252, 252, 250))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, w - 7, h - 7), radius=14, fill=WHITE,
                        outline=(214, 220, 230), width=2)
    # 窗口标题条
    d.rounded_rectangle((6, 6, w - 7, 44), radius=14, fill=(238, 242, 248))
    for cx, col in ((24, (230, 100, 100)), (44, (236, 190, 90)), (64, (110, 190, 130))):
        d.ellipse((cx - 6, 19, cx + 6, 31), fill=col)
    d.text((84, 18), "rea-expert-council", font=ImageFont.truetype(MONO, 13),
           fill=MUTED)
    y = pad + 30
    for ln in lines:
        color = INK
        if ln.startswith("$"):
            color = BLUE
        elif "escalate_to" in ln or "human_flags" in ln:
            color = RED
        d.text((pad, y), ln, font=f, fill=color)
        y += line_h
    img.save(path, "PNG", optimize=True)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 REA 文档配图")
    ap.add_argument("--lang", choices=sorted(L.keys()), help="只生成指定语言")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    langs = [args.lang] if args.lang else sorted(L.keys())
    for lg in langs:
        a = make_architecture(lg, os.path.join(OUT_DIR, "architecture-%s.png" % lg))
        b = make_layers(lg, os.path.join(OUT_DIR, "five-layers-%s.png" % lg))
        print("  %s (%dx%d)  %s (%dx%d)" % (os.path.basename(a), *Image.open(a).size,
                                             os.path.basename(b), *Image.open(b).size))
    p = make_demo(os.path.join(OUT_DIR, "demo-peek.png"))
    print("  %s (%dx%d)" % (os.path.basename(p), *Image.open(p).size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
