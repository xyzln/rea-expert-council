#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish.py - 把本目录打包成 SkillHub 上传包（zip）。

用法：
  python3 scripts/publish.py            # 生成 <skill_id>-skillhub.zip
  python3 scripts/publish.py --check    # 仅校验发布材料是否齐全，不打包

SkillHub 上架需要：技能名称(<=20字)、描述(100-300字)、512x512 图标、
分类标签、权限声明、使用说明，以及 skill 安装包(zip)。
"""
from __future__ import annotations
import argparse, json, os, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
META = os.path.join(ROOT, "skillhub.json")
EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
EXCLUDE_FILES = {".gitignore", ".DS_Store"}


def load_meta() -> dict:
    with open(META, encoding="utf-8") as fh:
        return json.load(fh)


def check() -> list:
    meta = load_meta()
    problems = []
    # 名称
    if not meta.get("name"):
        problems.append("skillhub.json 缺少 name")
    elif len(meta["name"]) > 20:
        problems.append("name 超过 20 字: %s" % meta["name"])
    # 描述长度
    d = meta.get("description", "")
    if not d:
        problems.append("缺少 description")
    elif not (100 <= len(d) <= 300):
        problems.append("description 长度 %d 不在 100-300 字区间" % len(d))
    # 图标
    icon = os.path.join(ROOT, meta.get("icon", "icon.png"))
    if not os.path.exists(icon):
        problems.append("图标缺失: %s" % meta.get("icon"))
    else:
        try:
            from PIL import Image
            im = Image.open(icon)
            if im.size != (512, 512):
                problems.append("图标尺寸 %s 需为 512x512" % str(im.size))
        except ImportError:
            print("  [提示] 未安装 Pillow，跳过图标尺寸校验")
    # 必要字段
    for k in ("skill_id", "version", "author", "category", "tags", "permissions"):
        if k not in meta:
            problems.append("skillhub.json 缺少字段: %s" % k)
    # 主 skill 与核心脚本
    for rel in ("SKILL.md", "scripts/rea_engine.py", "scripts/peek.py",
                "experts/_registry.json"):
        if not os.path.exists(os.path.join(ROOT, rel)):
            problems.append("缺少 %s" % rel)
    # 专家注册表引用的 schema 必须都存在
    reg = os.path.join(ROOT, "experts", "_registry.json")
    if os.path.exists(reg):
        try:
            data = json.load(open(reg, encoding="utf-8"))
            for eid, edef in data.get("experts", {}).items():
                sp = os.path.join(ROOT, edef.get("schema", ""))
                if not os.path.exists(sp):
                    problems.append("专家 %s 的 schema 缺失: %s" % (eid, edef.get("schema")))
                sk = os.path.join(ROOT, edef.get("dir", ""), "SKILL.md")
                if not os.path.exists(sk):
                    problems.append("专家 %s 缺少 SKILL.md" % eid)
        except json.JSONDecodeError as exc:
            problems.append("experts/_registry.json 不是合法 JSON: %s" % exc)
    # 引擎自测
    return problems


def run_engine_selftest() -> bool:
    """打包前确保引擎自测通过。"""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "rea_engine.py"),
                        "selftest"], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        return False
    print("  [OK] rea_engine selftest 通过")
    return True


def build() -> str:
    meta = load_meta()
    out = os.path.join(ROOT, "%s-skillhub.zip" % meta["skill_id"])
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dirs, fns in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in fns:
                if fn in EXCLUDE_FILES:
                    continue
                fp = os.path.join(dp, fn)
                rel = os.path.relpath(fp, ROOT)
                if rel == os.path.basename(out):
                    continue
                z.write(fp, rel)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 SkillHub 上传包")
    ap.add_argument("--check", action="store_true", help="仅校验发布材料")
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()
    print("=== 校验发布材料 ===")
    problems = check()
    if problems:
        for p in problems:
            print("  [缺失] %s" % p)
        print("校验未通过。")
        return 1
    print("  [OK] 名称/描述/图标/分类/权限/主skill/引擎/体检器/注册表/专家schema 全部就位")
    if not args.skip_selftest and not run_engine_selftest():
        print("引擎自测未通过，中止打包。")
        return 1
    if args.check:
        return 0
    out = build()
    print("=== 已打包 ===")
    print("  %s (%d bytes)" % (out, os.path.getsize(out)))
    print("\n下一步：访问 https://skillhub.cn → 右上角『发布团队skill』→ 上传此 zip 并填写元信息 → 提交审核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
