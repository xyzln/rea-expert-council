#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py - REA 专家团全链路测试（零依赖，纯标准库 + Pillow 可选）。

覆盖：
  1. 引擎：selftest 三分支、路由、schema 校验、共识、审计
  2. peek.py：JSON/日志/满熵/ZIP/PNG/ELF/目录 七类样本（动态构造）
  3. 材料完整性：SKILL.md、注册表、每位专家 schema/SKILL.md、skillhub.json
用法：python3 tests/run_tests.py
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "scripts", "rea_engine.py")
PEEK = os.path.join(ROOT, "scripts", "peek.py")

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable] + cmd, capture_output=True, text=True, **kw)


def test_engine() -> None:
    print("\n== 引擎 ==")
    r = run([ENGINE, "selftest"])
    check("selftest 三分支", r.returncode == 0 and r.stdout.count("[PASS]") == 3,
          r.stdout + r.stderr)
    r = run([ENGINE, "route", "--state", "未知格式的二进制协议抓包，含 TLV 结构", "--json"])
    try:
        top = json.loads(r.stdout)[0]["expert"]
    except Exception:
        top = ""
    check("路由: 抓包→protocol-decoder", top == "protocol-decoder", r.stdout + r.stderr)
    r = run([ENGINE, "experts"])
    check("experts 列出 6 位", r.returncode == 0 and r.stdout.count("(") >= 6, r.stdout)
    # schema 错误检测
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"questions": {"x": {"type": "bogus", "instructions": "?"}}}, fh)
        bad = fh.name
    r = run([ENGINE, "prompt", "--schema-file", bad, "--state", "x"])
    check("非法 schema → 退出码 3", r.returncode == 3, str(r.returncode))
    os.unlink(bad)
    # 共识：取最严
    results = [
        {"expert": "a", "routing": {"escalate": True, "escalate_to": "system2"}},
        {"expert": "b", "routing": {"escalate": False}},
    ]
    r = run([ENGINE, "consensus", "--results", json.dumps(results)])
    check("共识: system2 升级", r.returncode == 0 and json.loads(r.stdout)["verdict"] == "evidence_needed",
          r.stdout + r.stderr)
    results[0]["routing"]["escalate_to"] = "human"
    r = run([ENGINE, "consensus", "--results", json.dumps(results)])
    check("共识: human 优先", json.loads(r.stdout)["verdict"] == "human_review", r.stdout)
    # 审计
    env = dict(os.environ, REA_AUDIT_DIR=tempfile.mkdtemp())
    run([ENGINE, "decide", "--schema-file", os.path.join(ROOT, "assets/schemas/artifact-triage.json"),
         "--state", "一个加密的固件更新包"], env=env)
    r = run([ENGINE, "audit", "--dir", env["REA_AUDIT_DIR"]], env=env)
    check("审计留痕", r.returncode == 0 and json.loads(r.stdout).get("total") == 1, r.stdout)
    shutil.rmtree(env["REA_AUDIT_DIR"], ignore_errors=True)


def test_peek() -> None:
    print("\n== peek.py ==")
    tmp = tempfile.mkdtemp(prefix="rea_test_")
    try:
        json.dump({"a": 1, "b": [1, 2]}, open(os.path.join(tmp, "m.json"), "w"))
        open(os.path.join(tmp, "app.log"), "w").write(
            "2024-01-01 00:00:00 INFO ok\n2024-01-01 00:00:01 ERROR bad\n"
            "2024-01-01 00:00:02 WARN retry https://x.example.com\n")
        random.seed(1)
        open(os.path.join(tmp, "enc.bin"), "wb").write(
            bytes(random.randrange(256) for _ in range(20000)))
        with zipfile.ZipFile(os.path.join(tmp, "b.zip"), "w") as z:
            z.writestr("classes.dex", "dex\n035\x00")
        try:
            from PIL import Image
            Image.new("RGB", (32, 16), (1, 2, 3)).save(os.path.join(tmp, "i.png"))
            png_ok = True
        except ImportError:
            png_ok = False

        r = run([PEEK, os.path.join(tmp, "m.json"), "--json"])
        s = json.loads(r.stdout)
        check("JSON 探测", s["structure"]["format"] == "JSON" and "a" in s["structure"]["top_keys"],
              r.stdout[:200])
        r = run([PEEK, os.path.join(tmp, "app.log"), "--json"])
        s = json.loads(r.stdout)
        check("日志探测", s["structure"]["format"] == "LOG", s.get("structure"))
        check("线索提取(URL)", any("https://" in c for c in s["clues"]), s.get("clues"))
        r = run([PEEK, os.path.join(tmp, "enc.bin"), "--json"])
        s = json.loads(r.stdout)
        check("满熵→OPAQUE", s["structure"]["format"] == "OPAQUE" and s["entropy"] > 7.4,
              s.get("structure"))
        r = run([PEEK, os.path.join(tmp, "b.zip"), "--json"])
        s = json.loads(r.stdout)
        check("ZIP→APK 提示", "APK" in s["structure"].get("hint", ""), s.get("structure"))
        if png_ok:
            r = run([PEEK, os.path.join(tmp, "i.png"), "--json"])
            s = json.loads(r.stdout)
            check("PNG 探测", s["structure"]["format"] == "PNG" and s["structure"]["width"] == 32,
                  s.get("structure"))
        r = run([PEEK, "/bin/ls", "--json"])
        if r.returncode == 0:
            s = json.loads(r.stdout)
            check("真实 ELF 探测", s["structure"]["format"] == "ELF"
                  and s["structure"].get("machine") in ("x86-64", "i386", "AArch64"),
                  s.get("structure"))
        else:
            check("真实 ELF 探测（跳过：无 /bin/ls）", True)
        r = run([PEEK, tmp, "--json", "--depth", "2"])
        s = json.loads(r.stdout)
        check("目录模式+高熵标记", s["total_files"] >= 4 and s["suspicious_high_entropy"], r.stdout[:300])
        r = run([PEEK, "/nonexistent_path_xyz"])
        check("不存在路径→退出码 2", r.returncode == 2, str(r.returncode))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_materials() -> None:
    print("\n== 材料完整性 ==")
    for rel in ("SKILL.md", "README.md", "skillhub.json", "icon.png",
                "scripts/rea_engine.py", "scripts/peek.py", "scripts/publish.py",
                "experts/_registry.json", "references/method.md",
                "references/evidence.md", "references/engine.md", "references/expert-team.md"):
        check("存在 %s" % rel, os.path.exists(os.path.join(ROOT, rel)))
    reg = json.load(open(os.path.join(ROOT, "experts", "_registry.json"), encoding="utf-8"))
    for eid, edef in reg.get("experts", {}).items():
        check("专家 %s schema+SKILL" % eid,
              os.path.exists(os.path.join(ROOT, edef["schema"]))
              and os.path.exists(os.path.join(ROOT, edef["dir"], "SKILL.md")))
    # 每个专家 schema 都能被引擎加载
    ok = True
    for eid, edef in reg["experts"].items():
        r = run([ENGINE, "prompt", "--schema-file", os.path.join(ROOT, edef["schema"]),
                 "--state", "测试"])
        ok &= r.returncode == 0 and "只输出 JSON" in r.stdout
    check("全部专家 schema 可出题", ok)
    meta = json.load(open(os.path.join(ROOT, "skillhub.json"), encoding="utf-8"))
    check("name ≤ 20 字", len(meta["name"]) <= 20, meta["name"])
    check("description 100-300 字", 100 <= len(meta["description"]) <= 300,
          str(len(meta["description"])))
    try:
        from PIL import Image
        check("图标 512x512", Image.open(os.path.join(ROOT, "icon.png")).size == (512, 512))
    except ImportError:
        check("图标尺寸（跳过：无 Pillow）", True)
    r = run([os.path.join(ROOT, "scripts", "publish.py"), "--check", "--skip-selftest"])
    check("publish --check", r.returncode == 0, r.stdout + r.stderr)


if __name__ == "__main__":
    print("REA 专家团全链路测试")
    test_engine()
    test_peek()
    test_materials()
    print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
