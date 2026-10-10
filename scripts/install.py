#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py - 把 REA 逆向工程专家团部署到运行环境的 skill 目录。

用法：
  python3 scripts/install.py                          # 软链到默认 skill 目录
  python3 scripts/install.py --skills-dir <路径>      # 指定 skill 目录
  python3 scripts/install.py --mode copy              # 复制而非软链
  python3 scripts/install.py --uninstall              # 移除

skill 目录的解析顺序：
  1. --skills-dir 参数
  2. 环境变量 REA_SKILLS_DIR
  3. 环境变量 SKILLS_DIR
  4. 默认值 ~/.rea/skills（可自行改为所用运行环境的目录）

REA 不注入任何钩子、不修改全局配置——装完即用。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_NAME = "rea-expert-council"


def skills_dir(cli_value: str | None = None) -> str:
    if cli_value:
        return os.path.abspath(os.path.expanduser(cli_value))
    for var in ("REA_SKILLS_DIR", "SKILLS_DIR"):
        v = os.environ.get(var)
        if v:
            return os.path.abspath(os.path.expanduser(v))
    return os.path.join(os.path.expanduser("~"), ".rea", "skills")


def deploy(mode: str, target_dir: str) -> None:
    os.makedirs(target_dir, exist_ok=True)
    dest = os.path.join(target_dir, SKILL_NAME)
    if os.path.islink(dest):
        os.unlink(dest)
    elif os.path.isdir(dest):
        shutil.rmtree(dest)
    if mode == "link":
        os.symlink(ROOT, dest)
        print("已软链 skill -> %s" % dest)
    else:
        shutil.copytree(ROOT, dest,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        print("已复制 skill -> %s" % dest)
    # 安装后冒烟测试
    r = subprocess.run([sys.executable, os.path.join(dest, "scripts", "rea_engine.py"),
                        "selftest"], capture_output=True, text=True)
    print("冒烟测试: %s" % ("通过" if r.returncode == 0
                            else "失败\n" + r.stdout + r.stderr))
    print("\n完成。重新加载 skill 后即可通过触发词（逆向 / 反编译 / 复刻 / 抓包…）唤起。")


def uninstall(target_dir: str) -> None:
    dest = os.path.join(target_dir, SKILL_NAME)
    if os.path.islink(dest):
        os.unlink(dest)
        print("已移除软链: %s" % dest)
    elif os.path.isdir(dest):
        shutil.rmtree(dest)
        print("已移除目录: %s" % dest)
    else:
        print("未发现已安装的 %s（目录: %s）" % (SKILL_NAME, target_dir))
    cache = os.path.join(os.path.expanduser("~"), ".cache", "rea-expert-council")
    if os.path.isdir(cache):
        shutil.rmtree(cache)
        print("已清理审计缓存: %s" % cache)


def main() -> int:
    ap = argparse.ArgumentParser(description="部署 REA 逆向工程专家团")
    ap.add_argument("--skills-dir", help="目标 skill 目录（默认 ~/.rea/skills）")
    ap.add_argument("--mode", choices=["link", "copy"], default="link")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    target = skills_dir(args.skills_dir)
    if args.uninstall:
        uninstall(target)
    else:
        deploy(args.mode, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
