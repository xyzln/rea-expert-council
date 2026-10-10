#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""peek.py - REA 目标体检器：对任意文件/目录做零依赖的第一轮勘察。

逆向工程的第一步永远是"先看清它是什么"，而不是猜。本脚本用纯标准库完成第一轮勘察：

  · 身份：大小、魔数识别（70+ 种格式）、MIME 猜测
  · 熵值：整体与分块熵，用于判断压缩/加密/加壳
  · 字符串：可打印 ASCII 串与 CJK 串，按长度过滤
  · 结构：JSON/XML/ZIP/gzip/ELF/PE/PNG/SQLite/PDF/tar/WASM/class/dex 等深度探测
  · 字节视图：前若干字节的 hexdump，用于人工判读
  · 目录模式：文件类型分布、体积分布、入口点猜测、可疑/高熵文件标记

用法：
  python3 scripts/peek.py <路径>                  # 人类可读报告
  python3 scripts/peek.py <路径> --json           # 机器可读 JSON
  python3 scripts/peek.py <路径> --strings 40     # 最多输出 40 条字符串
  python3 scripts/peek.py <目录> --depth 3        # 目录模式，限制遍历深度
  python3 scripts/peek.py <文件> --hex 256        # hexdump 前 256 字节
  python3 scripts/peek.py <文件> --chunk-entropy  # 分块熵（定位加密/压缩区段）

纯标准库，跨平台，无联网。只读，绝不修改目标。
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import struct
import sys
import zipfile

MAX_READ = 8 * 1024 * 1024          # 熵/结构探测最多读取的字节数
ENTROPY_CHUNK = 4096                # 分块熵的块大小
HIGH_ENTROPY = 7.4                  # 达到此熵值即视为加密/强压缩
# 基于魔数即可确认的"容器型"格式：即使满熵也仍按容器解析
CONTAINER_KINDS = {
    "ZIP", "ZIP(empty)", "ELF", "PE/DOS", "DOS-MZ", "PNG", "SQLite", "SQLITE",
    "DEX", "WASM", "TAR", "GZIP", "PDF", "JavaClass", "MachO", "MachO-FAT",
    "OLE2", "7Z", "RAR", "BZIP2", "XZ", "ZSTD", "LZ4", "COMPRESS", "ZLIB",
    "MP4", "MP4/ISO-BMFF", "PCAP", "PCAPNG", "AR", "RPM", "OGG", "MP3", "FLAC",
    "GIF", "JPEG", "JPEG2000", "BMP", "RIFF", "ICO", "Matroska", "BLEND",
}

# --------------------------------------------------------------------------- #
# 魔数表：(偏移, 签名, 类型名, 备注)
# --------------------------------------------------------------------------- #
MAGIC = [
    (0, b"\x7fELF", "ELF", "Unix 可执行/目标文件"),
    (0, b"MZ", "PE/DOS", "Windows 可执行（需进一步确认 PE 签名）"),
    (0, b"PK\x03\x04", "ZIP", "ZIP 压缩包（jar/apk/docx/xlsx 均基于此）"),
    (0, b"PK\x05\x06", "ZIP(empty)", "空 ZIP"),
    (0, b"\x1f\x8b", "GZIP", "gzip 压缩"),
    (0, b"\x42\x5a\x68", "BZIP2", "bzip2 压缩"),
    (0, b"\xfd7zXZ\x00", "XZ", "xz 压缩"),
    (0, b"\x04\x22\x4d\x18", "LZ4", "lz4 帧"),
    (0, b"\x28\xb5\x2f\xfd", "ZSTD", "zstd 压缩"),
    (0, b"7z\xbc\xaf\x27\x1c", "7Z", "7-zip 压缩包"),
    (0, b"Rar!\x1a\x07", "RAR", "RAR 压缩包"),
    (0, b"\x89PNG\r\n\x1a\n", "PNG", "PNG 图像"),
    (0, b"\xff\xd8\xff", "JPEG", "JPEG 图像"),
    (0, b"GIF87a", "GIF", "GIF 图像"),
    (0, b"GIF89a", "GIF", "GIF 图像"),
    (0, b"BM", "BMP", "BMP 图像"),
    (0, b"RIFF", "RIFF", "RIFF 容器（WAV/AVI/WEBP）"),
    (0, b"SQLite format 3\x00", "SQLite", "SQLite 数据库"),
    (0, b"%PDF-", "PDF", "PDF 文档"),
    (0, b"\xca\xfe\xba\xbe", "JavaClass", "Java .class 字节码"),
    (0, b"dex\n", "DEX", "Android Dalvik 字节码"),
    (0, b"\x00asm", "WASM", "WebAssembly 模块"),
    (0, b"\xcf\xfa\xed\xfe", "MachO", "macOS 可执行（64位 LE）"),
    (0, b"\xfe\xed\xfa\xcf", "MachO", "macOS 可执行（32位 BE）"),
    (0, b"\xce\xfa\xed\xfe", "MachO", "macOS 可执行（32位 LE）"),
    (0, b"\xcf\xfa\xed\xfe", "MachO", "macOS 可执行"),
    (0, b"!<arch>\n", "AR", "Unix 静态库"),
    (0, b"\xed\xab\xee\xdb", "RPM", "RPM 包"),
    (0, b"!<arch>", "AR", "Unix 静态库"),
    (0, b"\xd0\xcf\x11\xe0", "OLE2", "OLE2 复合文档（旧版 doc/xls/ppt/msi）"),
    (0, b"\x25\x21\x50\x53", "EPS/PS", "PostScript"),
    (0, b"ustar", "TAR", "GNU tar（签名在偏移 257）"),
    (0, b"gIMP", "GIMP", "GIMP 图像"),
    (0, b"OggS", "OGG", "Ogg 容器"),
    (0, b"ID3", "MP3", "MP3（ID3 头）"),
    (0, b"\xff\xfb", "MP3", "MP3（无 ID3）"),
    (0, b"fLaC", "FLAC", "FLAC 音频"),
    (0, b"ftyp", "MP4/ISO-BMFF", "MP4 / ISO 基础媒体格式"),
    (0, b"\x1a\x45\xdf\xa3", "Matroska", "MKV/WebM 容器"),
    (0, b"\x52\x49\x46\x46", "RIFF", "RIFF 容器"),
    (0, b"#!", "SHEBANG", "脚本（首行为解释器）"),
    (0, b"<?xml", "XML", "XML 文档"),
    (0, b"{\n", "JSON?", "疑似 JSON（需解析确认）"),
    (0, b"\x00\x00\x00\x20ftyp", "MP4", "MP4（带 size 前缀）"),
    (0, b"\x1f\x9d", "COMPRESS", "Unix compress (.Z)"),
    (0, b"\x1f\xa0", "COMPRESS", "Unix compress (.Z)"),
    (0, b"LZ4\x02", "LZ4", "LZ4 块"),
    (0, b"\x5a\x4c\x49\x42", "ZLIB", "zlib 流（变体）"),
    (0, b"\x78\x01", "ZLIB", "zlib 流（低压缩）"),
    (0, b"\x78\x9c", "ZLIB", "zlib 流（默认压缩）"),
    (0, b"\x78\xda", "ZLIB", "zlib 流（最高压缩）"),
    (0, b"\x4d\x5a", "DOS-MZ", "DOS/Windows MZ 头"),
    (0, b"SQLite", "SQLite", "SQLite 数据库"),
    (0, b"\x0f\xf0\x0f\xf0", "PACKET", "pcap 变体"),
    (0, b"\xd4\xc3\xb2\xa1", "PCAP", "libpcap 抓包（LE）"),
    (0, b"\xa1\xb2\xc3\xd4", "PCAP", "libpcap 抓包（BE）"),
    (0, b"\x0a\x0d\x0d\x0a", "PCAPNG", "pcapng 抓包"),
    (0, b"\x37\x7a\xbc\xaf", "7Z", "7-zip"),
    (0, b"\xfe\xed\xfa\xce", "MachO-FAT", "macOS 通用二进制（BE）"),
    (0, b"\xce\xfa\xed\xfe", "MachO", "macOS 可执行"),
    (0, b"\xca\xfe\xd0\x0d", "MachO-FAT", "macOS 通用二进制"),
    (0, b"\xbe\xba\xfe\xca", "JavaClass", "Java class（BE 变体）"),
    (0, b"\x6c\x6f\x67\x00", "LOG", "日志容器"),
    (0, b"\x53\x51\x4c\x69", "SQLITE", "SQLite 变体"),
    (0, b"\x00\x00\x01\x00", "ICO", "Windows 图标"),
    (0, b"\x47\x49\x46\x38", "GIF", "GIF 图像"),
    (0, b"\xff\x4f\xff\x51", "JPEG2000", "JPEG 2000 码流"),
    (0, b"\x00\x00\x00\x0c\x6a\x50", "JPEG2000", "JPEG 2000 容器"),
    (0, b"BLENDER", "BLEND", "Blender 工程"),
    (0, b"\x25\x50\x44\x46", "PDF", "PDF 文档"),
    (0, b"\x3c\x3f\x78\x6d\x6c", "XML", "XML 文档"),
    (0, b"\x1f\x8b\x08", "GZIP", "gzip（deflate）"),
]

# 入口点候选（目录模式用）
ENTRY_HINTS = [
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "CMakeLists.txt", "Makefile", "makefile", "meson.build", "BUILD", "BUILD.bazel",
    "composer.json", "Gemfile", "requirements.txt", "README.md", "readme.md",
    "index.js", "index.ts", "main.py", "app.py", "main.go", "main.rs", "main.c",
    "Dockerfile", "docker-compose.yml", ".git", "src", "lib", "app",
]

# 高价值线索的正则（在字符串里高亮）
CLUE_RE = re.compile(
    r"(?i)(https?://[^\s\"']+|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|"
    r"(?:error|warn|fail|invalid|denied|unauthorized|expired|timeout|panic)[^\n]{0,60}|"
    r"/(?:usr|etc|var|home|tmp)/[^\s\"']*|"
    r"\b(?:api[_-]?key|token|secret|password|BEGIN [A-Z ]*PRIVATE KEY)\b)"
)


# --------------------------------------------------------------------------- #
# 基础计算
# --------------------------------------------------------------------------- #
def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = collections.Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def entropy_verdict(e: float) -> str:
    if e < 1.0:
        return "极低：高度冗余或大量填充"
    if e < 4.5:
        return "偏低：文本/结构化数据为主"
    if e < 6.5:
        return "中等：混合内容或未压缩二进制"
    if e < 7.5:
        return "偏高：可能为压缩数据"
    return "接近满熵(≥7.5)：几乎可断定是加密或强压缩数据"


def detect_magic(head: bytes) -> list:
    hits = []
    for off, sig, name, note in MAGIC:
        if len(head) >= off + len(sig) and head[off:off + len(sig)] == sig:
            hits.append({"type": name, "note": note, "offset": off})
    # tar 的签名在 257
    if len(head) > 262 and head[257:262] == b"ustar":
        hits.append({"type": "TAR", "note": "GNU tar 归档", "offset": 257})
    if not hits and head[:1] == b"{":
        hits.append({"type": "JSON?", "note": "疑似 JSON", "offset": 0})
    if not hits and head[:1] == b"[":
        hits.append({"type": "JSON?", "note": "疑似 JSON 数组", "offset": 0})
    # 去重保序
    seen, out = set(), []
    for h in hits:
        if h["type"] not in seen:
            seen.add(h["type"])
            out.append(h)
    return out


_STR_CACHE: dict = {}


def _str_re(min_len: int):
    """可打印 ASCII 串（不含换行/制表符），因此字符串以行为界。"""
    if min_len not in _STR_CACHE:
        _STR_CACHE[min_len] = re.compile(rb"[ -~]{%d,}" % min_len)
    return _STR_CACHE[min_len]


def extract_strings(data: bytes, min_len: int = 4, limit: int = 60) -> list:
    try:
        raw = _str_re(min_len).findall(data[:MAX_READ])
    except re.error:
        return []
    seen, out = set(), []
    for m in raw:
        s = m.decode("ascii", "replace").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= 4000:        # 防止超大文件排序开销
            break
    # 按信息量排序：含高价值线索的优先，其次长的优先
    out.sort(key=lambda s: (bool(CLUE_RE.search(s)), len(s)), reverse=True)
    return out[:limit]


def hexdump(data: bytes, length: int = 128, width: int = 16) -> str:
    lines = []
    for i in range(0, min(len(data), length), width):
        chunk = data[i:i + width]
        hexpart = " ".join("%02x" % b for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%08x  %-*s  |%s|" % (i, width * 3, hexpart, asciipart))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 结构探测
# --------------------------------------------------------------------------- #
_TS_LINE = re.compile(rb"^\s*(?:\[)?(?:\d{4}[-/]\d{2}[-/]\d{2}[T ]?|\d{2}/\w{3}/\d{4}|\d{2}:\d{2}:\d{2})")
_LEVEL_LINE = re.compile(rb"^\s*(?:\[)?(?:TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", re.I)


def probe_text(data: bytes) -> dict:
    head = data[:4096]
    try:
        obj = json.loads(data.decode("utf-8", "replace"))
        if isinstance(obj, dict):
            return {"format": "JSON", "detail": "合法 JSON 对象",
                    "top_keys": list(obj.keys())[:20], "key_count": len(obj)}
        if isinstance(obj, list):
            return {"format": "JSON", "detail": "合法 JSON 数组",
                    "length": len(obj),
                    "sample_item_keys": list(obj[0].keys())[:20] if obj and isinstance(obj[0], dict) else None}
    except Exception:
        pass
    if head.lstrip()[:5] == b"<?xml" or (head.lstrip()[:1] == b"<" and b"</" in data[:20000]):
        tags = re.findall(rb"<([A-Za-z][\w:.-]*)", head)
        return {"format": "XML", "detail": "XML 文档",
                "root_candidates": [t.decode() for t in tags[:6]]}

    lines = [ln for ln in head.split(b"\n")[:30] if ln.strip()]
    if lines:
        log_like = sum(1 for ln in lines
                       if _TS_LINE.match(ln) or _LEVEL_LINE.match(ln))
        if log_like >= max(1, int(len(lines) * 0.6)):
            levels = collections.Counter()
            for ln in head.split(b"\n"):
                m = re.search(rb"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", ln, re.I)
                if m:
                    levels[m.group(1).upper().decode()] += 1
            return {"format": "LOG", "detail": "日志文本",
                    "sampled_lines": len(lines), "log_like_lines": log_like,
                    "level_counts": dict(levels.most_common(8))}

    first = head.split(b"\n", 1)[0]
    if b"," in first and first.count(b",") >= 2:
        rows = [ln for ln in head.split(b"\n")[:6] if ln.strip()]
        widths = [ln.count(b",") for ln in rows]
        if len(set(widths)) <= 2:
            return {"format": "CSV?", "detail": "疑似分隔符文本",
                    "header": first[:200].decode("utf-8", "replace"),
                    "columns": first.count(b",") + 1}
    if re.search(rb"^\S+:\s", head, re.M):
        return {"format": "KEY-VALUE", "detail": "疑似键值文本（如 HTTP 头 / YAML 片段）"}
    return {"format": "TEXT", "detail": "可读文本，无明确结构"}


def probe_zip(path: str) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            exts = collections.Counter(os.path.splitext(n)[1].lower() for n in names)
            info = [{"name": n, "size": z.getinfo(n).file_size,
                     "compress": z.getinfo(n).compress_size} for n in names[:15]]
            return {"format": "ZIP", "entries": len(names),
                    "top_exts": exts.most_common(8), "sample": info,
                    "hint": ("含 classes.dex → 可能是 APK" if "classes.dex" in names else
                             "含 [Content_Types].xml → 可能是 OOXML(docx/xlsx/pptx)" if "[Content_Types].xml" in names else
                             "含 META-INF/MANIFEST.MF → 可能是 JAR" if "META-INF/MANIFEST.MF" in names else
                             "通用 ZIP 归档")}
    except Exception as exc:
        return {"format": "ZIP", "detail": "ZIP 头存在但解析失败: %s" % exc}


def probe_elf(data: bytes) -> dict:
    if len(data) < 64:
        return {"format": "ELF", "detail": "文件过短"}
    ei_class = {1: "32-bit", 2: "64-bit"}.get(data[4], "?")
    ei_data = {1: "little-endian", 2: "big-endian"}.get(data[5], "?")
    try:
        e_type = struct.unpack_from("<H", data, 16)[0]
        e_machine = struct.unpack_from("<H", data, 18)[0]
        if ei_class == "64-bit":
            e_entry = struct.unpack_from("<Q", data, 24)[0]
        else:
            e_entry = struct.unpack_from("<I", data, 24)[0]
    except Exception:
        return {"format": "ELF", "class": ei_class, "endian": ei_data}
    types = {0: "NONE", 1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}
    machines = {0x3e: "x86-64", 0x03: "i386", 0xb7: "AArch64", 0x28: "ARM",
                0x08: "MIPS", 0xf3: "RISC-V", 0x14: "PowerPC"}
    return {"format": "ELF", "class": ei_class, "endian": ei_data,
            "type": types.get(e_type, hex(e_type)),
            "machine": machines.get(e_machine, hex(e_machine)),
            "entry": hex(e_entry),
            "has_symtab_hint": b".symtab" in data[:MAX_READ] or b"strtab" in data[:MAX_READ]}


def probe_pe(data: bytes) -> dict:
    if len(data) < 64:
        return {"format": "PE/DOS", "detail": "文件过短"}
    try:
        e_lfanew = struct.unpack_from("<I", data, 0x3c)[0]
    except Exception:
        return {"format": "PE/DOS", "detail": "无法读取 e_lfanew"}
    if e_lfanew + 6 > len(data) or data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        return {"format": "DOS-MZ", "detail": "仅 MZ 头，非 PE（可能是 DOS 程序）"}
    try:
        machine = struct.unpack_from("<H", data, e_lfanew + 4)[0]
        nsec = struct.unpack_from("<H", data, e_lfanew + 6)[0]
        opt_magic = struct.unpack_from("<H", data, e_lfanew + 24)[0]
    except Exception:
        return {"format": "PE", "detail": "PE 头不完整"}
    machines = {0x8664: "x86-64", 0x014c: "i386", 0xaa64: "AArch64", 0x01c4: "ARMv7"}
    bits = {0x10b: "PE32", 0x20b: "PE32+"}.get(opt_magic, "unknown")
    sec_names = ""
    if nsec and e_lfanew + 24 + 224 < len(data):
        try:
            sec_off = e_lfanew + 24 + 224
            names = []
            for i in range(min(nsec, 16)):
                nm = data[sec_off + i * 40: sec_off + i * 40 + 8].rstrip(b"\x00")
                names.append(nm.decode("ascii", "replace"))
            sec_names = ",".join(names)
        except Exception:
            sec_names = ""
    return {"format": "PE", "machine": machines.get(machine, hex(machine)),
            "bits": bits, "sections": nsec, "section_names": sec_names[:200],
            "entry_hint": "节名含 UPX/.aspack 等 → 疑似加壳"
            if any(k in sec_names for k in ("UPX", "ASPack", "FSG", "PECompact", "NSP")) else ""}


def probe_png(data: bytes) -> dict:
    if len(data) < 33:
        return {"format": "PNG", "detail": "文件过短"}
    w, h = struct.unpack_from(">II", data, 16)
    bitdepth, color = data[24], data[25]
    chunks = []
    pos = 8
    while pos + 8 <= min(len(data), 200000):
        ln = struct.unpack_from(">I", data, pos)[0]
        ct = data[pos + 4:pos + 8].decode("latin1", "replace")
        chunks.append(ct)
        pos += 12 + ln
        if ct == "IEND" or len(chunks) > 40:
            break
    return {"format": "PNG", "width": w, "height": h,
            "bit_depth": bitdepth, "color_type": color,
            "chunks": chunks[:20],
            "suspicious": bool(set(chunks) - {"IHDR", "PLTE", "IDAT", "IEND", "tEXt", "iTXt", "zTXt", "pHYs", "sRGB", "gAMA", "tIME", "bKGD"})}


def probe_sqlite(data: bytes) -> dict:
    try:
        page_size = struct.unpack_from(">H", data, 16)[0] or 65536
    except Exception:
        page_size = 0
    return {"format": "SQLite", "page_size": page_size,
            "detail": "用 sqlite3 打开可列出表结构"}


def probe_dex(data: bytes) -> dict:
    ver = data[4:8].decode("latin1", "replace") if len(data) > 8 else "?"
    return {"format": "DEX", "dex_version": ver}


def probe_wasm(data: bytes) -> dict:
    ver = struct.unpack_from("<I", data, 4)[0] if len(data) > 8 else 0
    return {"format": "WASM", "version": ver}


def probe_file(path: str) -> dict:
    """对单个文件做完整体检。"""
    st = os.stat(path)
    size = st.st_size
    with open(path, "rb") as fh:
        head = fh.read(MAX_READ)
    res = {
        "path": path,
        "name": os.path.basename(path),
        "size": size,
        "size_human": human_size(size),
        "modified": int(st.st_mtime),
        "magic": detect_magic(head[:4096]),
        "entropy": round(shannon_entropy(head), 4),
        "entropy_verdict": entropy_verdict(shannon_entropy(head)),
    }
    if size > MAX_READ:
        res["note"] = "文件大于 %s，熵值基于前 %s 计算" % (
            human_size(MAX_READ), human_size(MAX_READ))

    # 结构探测
    struct_info = None
    kinds = {m["type"] for m in res["magic"]}
    try:
        if "ZIP" in kinds or "ZIP(empty)" in kinds:
            struct_info = probe_zip(path)
        elif "ELF" in kinds:
            struct_info = probe_elf(head)
        elif "PE/DOS" in kinds or "DOS-MZ" in kinds:
            struct_info = probe_pe(head)
        elif "PNG" in kinds:
            struct_info = probe_png(head)
        elif "SQLite" in kinds or "SQLITE" in kinds:
            struct_info = probe_sqlite(head)
        elif "DEX" in kinds:
            struct_info = probe_dex(head)
        elif "WASM" in kinds:
            struct_info = probe_wasm(head)
        elif res["entropy"] >= HIGH_ENTROPY and not (kinds & CONTAINER_KINDS):
            struct_info = {"format": "OPAQUE",
                           "detail": "满熵且未命中容器魔数：裸加密/强压缩数据，结构不可解析",
                           "hint": "需先解密或解压才能继续；逆向边界止于密文边界"}
        else:
            struct_info = probe_text(head)
    except Exception as exc:
        struct_info = {"format": "?", "detail": "探测异常: %s" % exc}
    res["structure"] = struct_info

    # 仅对非满熵内容抽字符串（满熵抽出来只是噪声）
    if res["entropy"] < HIGH_ENTROPY:
        strs = extract_strings(head)
        res["strings"] = strs
        res["clues"] = [s for s in strs if CLUE_RE.search(s)][:15]
    else:
        res["strings"] = []
        res["clues"] = []
        res["strings_note"] = "满熵内容无可读字符串，符合加密/强压缩特征"
    return res


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f%s" % (n, unit) if unit != "B" else "%d%s" % (n, unit)
        n /= 1024.0
    return "%.1fPB" % n


# --------------------------------------------------------------------------- #
# 目录模式
# --------------------------------------------------------------------------- #
def probe_dir(path: str, depth: int, top: int) -> dict:
    ext_count = collections.Counter()
    size_by_ext = collections.Counter()
    big_files, suspicious = [], []
    entries, total_files, total_size = [], 0, 0
    base_depth = path.rstrip(os.sep).count(os.sep)

    for root, dirs, files in os.walk(path):
        cur = root.rstrip(os.sep).count(os.sep) - base_depth
        if cur >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            total_files += 1
            total_size += sz
            ext = os.path.splitext(fn)[1].lower() or "(no ext)"
            ext_count[ext] += 1
            size_by_ext[ext] += sz
            big_files.append((sz, os.path.relpath(fp, path)))
            if cur == 0 or cur == 1:
                entries.append(os.path.relpath(fp, path))

    big_files.sort(reverse=True)
    for sz, rel in big_files[:8]:
        fp = os.path.join(path, rel)
        try:
            with open(fp, "rb") as fh:
                h = fh.read(65536)
            e = shannon_entropy(h)
            if e >= 7.5 and sz > 10240:
                suspicious.append({"path": rel, "size": sz, "entropy": round(e, 3),
                                   "hint": "满熵，疑似加密/压缩/加壳"})
        except OSError:
            pass

    found_entries = [e for e in entries
                     if os.path.basename(e) in ENTRY_HINTS
                     or os.path.basename(e).lower() in ENTRY_HINTS][:12]

    return {
        "path": path,
        "total_files": total_files,
        "total_size": total_size,
        "total_size_human": human_size(total_size),
        "ext_distribution": ext_count.most_common(top),
        "size_by_ext": [(e, human_size(s)) for e, s in size_by_ext.most_common(top)],
        "largest_files": [{"path": p, "size": human_size(s)} for s, p in big_files[:10]],
        "entry_candidates": found_entries,
        "suspicious_high_entropy": suspicious,
    }


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def render_file(res: dict, nstrings: int, hexlen: int, chunk: bool) -> str:
    L = []
    A = L.append
    A("=" * 68)
    A("REA 目标体检：%s" % res["path"])
    A("=" * 68)
    A("大小        : %s (%d bytes)" % (res["size_human"], res["size"]))
    if res.get("note"):
        A("提示        : %s" % res["note"])
    A("魔数识别    : %s" % (", ".join("%s(%s)" % (m["type"], m["note"]) for m in res["magic"])
                            or "未匹配已知签名"))
    A("香农熵      : %.4f  → %s" % (res["entropy"], res["entropy_verdict"]))
    st = res["structure"]
    A("结构探测    : %s%s" % (st.get("format", "?"),
                              ("  " + st["detail"]) if st.get("detail") else ""))
    for k, v in st.items():
        if k in ("format", "detail"):
            continue
        if v not in (None, "", [], ()):
            A("  · %-12s %s" % (k, v))
    A("")
    if res["clues"]:
        A("高价值线索（URL/邮箱/错误串/路径/密钥名）：")
        for s in res["clues"][:15]:
            A("  ! %s" % s[:150])
        A("")
    if res["strings"]:
        A("可读字符串（Top %d）：" % min(nstrings, len(res["strings"])))
        for s in res["strings"][:nstrings]:
            A("  · %s" % s[:150])
        A("")
    if res.get("strings_note"):
        A("字符串      : %s\n" % res["strings_note"])
    if hexlen:
        with open(res["path"], "rb") as fh:
            A("前 %d 字节 hexdump：" % hexlen)
            A(hexdump(fh.read(hexlen), hexlen))
        A("")
    if chunk:
        with open(res["path"], "rb") as fh:
            A("分块熵（块=%d），用于定位加密/压缩区段：" % ENTROPY_CHUNK)
            i = 0
            while True:
                blk = fh.read(ENTROPY_CHUNK)
                if not blk:
                    break
                e = shannon_entropy(blk)
                bar = "#" * int(e * 6)
                A("  %08x  %.3f  %s" % (i, e, bar))
                i += ENTROPY_CHUNK
                if i >= 256 * 1024:
                    A("  ...（已达预览上限）")
                    break
    return "\n".join(L)


def render_dir(res: dict) -> str:
    L = []
    A = L.append
    A("=" * 68)
    A("REA 目录测绘：%s" % res["path"])
    A("=" * 68)
    A("文件总数    : %d   总体积: %s" % (res["total_files"], res["total_size_human"]))
    A("")
    A("扩展名分布（数量）：")
    for ext, c in res["ext_distribution"]:
        A("  %-14s %d" % (ext, c))
    A("")
    A("体积分布：")
    for ext, s in res["size_by_ext"]:
        A("  %-14s %s" % (ext, s))
    A("")
    A("最大的文件：")
    for f in res["largest_files"]:
        A("  %-10s %s" % (f["size"], f["path"]))
    if res["entry_candidates"]:
        A("")
        A("入口点候选（优先从这里开始读）：")
        for e in res["entry_candidates"]:
            A("  → %s" % e)
    if res["suspicious_high_entropy"]:
        A("")
        A("高熵可疑文件（疑似加密/压缩/加壳）：")
        for s in res["suspicious_high_entropy"]:
            A("  ! %-10s entropy=%.3f  %s" % (human_size(s["size"]), s["entropy"], s["path"]))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="peek", description="REA 目标体检器（零依赖、只读）")
    ap.add_argument("path", help="文件或目录路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strings", type=int, default=25, help="最多输出字符串条数")
    ap.add_argument("--hex", type=int, default=0, help="hexdump 前 N 字节（0=关闭）")
    ap.add_argument("--chunk-entropy", action="store_true", help="输出分块熵")
    ap.add_argument("--depth", type=int, default=4, help="目录遍历深度")
    ap.add_argument("--top", type=int, default=12, help="目录统计 Top-N")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print("peek: 路径不存在: %s" % args.path, file=sys.stderr)
        return 2

    if os.path.isdir(args.path):
        res = probe_dir(args.path, args.depth, args.top)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(render_dir(res))
        return 0

    res = probe_file(args.path)
    if args.json:
        res["strings"] = res["strings"][:args.strings]
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render_file(res, args.strings, args.hex, args.chunk_entropy))
    return 0


if __name__ == "__main__":
    sys.exit(main())
