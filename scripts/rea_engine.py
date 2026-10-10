#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rea_engine.py - REA（Reverse Engineer Anything）专家团决策中枢。

设计原则：**零依赖、零联网、纯标准库**。
加载本 skill 的运行环境即为引擎——它针对一组固定题型作答，并给出自报置信度。

REA 与通用决策引擎的三点差异：
  1. **证据分级 E0~E3**：每条答案可附带证据等级，E3（推测）不允许进入最终结论。
  2. **反幻觉闸门**：`speculation_*` / `evidence_gap` 等字段命中 -> 强制升级取证，
     禁止把推测写成事实。
  3. **法律/安全闸门**：`legal*` / `malicious` / `safety*` 命中 -> 直接转人工，不自行推进。

子命令：
  experts    列出专家团成员与触发词
  route      根据待逆向目标描述，选出最相关的专家（Top-K）
  prompt     为某 schema + state 生成 System 1 决策提示词
  validate   归一化作答结果 + 应用置信度闸门 + 升级路由
  decide     完整链路（给 --answer 走 LLM 路径，否则走离线启发式兜底）
  consensus  融合多个专家的结构化决策 -> 团队级结论
  audit      读取审计日志并输出统计
  selftest   内置样例跑通全链路（无网络、无 pip 依赖）

退出码：0 正常 | 2 用法错误 | 3 schema 错误。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCHEMA_DIR = os.path.join(ROOT, "assets", "schemas")
EXPERTS_DIR = os.path.join(ROOT, "experts")
REGISTRY_FILE = os.path.join(EXPERTS_DIR, "_registry.json")

EXIT_USAGE = 2
EXIT_SCHEMA = 3

VALID_TYPES = ("choice", "score", "noul")

# 证据分级：E0 直接观测 / E1 强推断 / E2 弱推断 / E3 推测
EVIDENCE_LEVELS = ("E0", "E1", "E2", "E3")
# 达到该级别即视为"不可写入结论"，必须先补证据
SPECULATION_LEVEL = "E3"

# 命中即强制升级：这些字段表示"证据不足 / 靠猜"，升级到 System 2 取证
EVIDENCE_GATE_KEYWORDS = (
    "evidence_gap", "needs_more_evidence", "needs_dynamic_probe", "speculation_risk",
    "narrative_risk", "guess_risk", "unverifiable", "blocking_unknowns",
    "underdetermined", "ambiguous",
)
# 命中即转人工：法律 / 授权 / 安全 / 恶意代码，引擎不自行推进
HUMAN_GATE_KEYWORDS = (
    "legal", "license", "licensing", "copyright", "safety", "malicious", "malware",
    "authorization", "authorized", "third_party_rights", "export_control",
)

AUDIT_DIR_ENV = "REA_AUDIT_DIR"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def die(code: int, *msg: str):
    for line in msg:
        print("rea_engine: %s" % line, file=sys.stderr)
    raise SystemExit(code)


def load_json(path: str, what: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        die(EXIT_SCHEMA, "找不到%s: %s" % (what, path))
    except json.JSONDecodeError as exc:
        die(EXIT_SCHEMA, "%s 不是合法 JSON (%s)" % (what, exc))


def load_schema(path: str) -> Tuple[str, Dict[str, Any]]:
    raw = load_json(path, "schema")
    if not isinstance(raw, dict):
        die(EXIT_SCHEMA, "schema 顶层必须是对象")
    questions = raw.get("questions")
    if not isinstance(questions, dict) or not questions:
        die(EXIT_SCHEMA, "%s 必须有非空的 questions 对象" % path)
    validate_questions(questions)
    return raw.get("state_key") or "target", questions


def validate_questions(questions: Dict[str, Any]) -> None:
    if len(questions) > 32:
        die(EXIT_SCHEMA, "问题数 %d 超过 32 上限" % len(questions))
    for qid, qdef in questions.items():
        if not isinstance(qdef, dict):
            die(EXIT_SCHEMA, "问题 %r 必须是对象" % qid)
        qtype = qdef.get("type")
        if qtype not in VALID_TYPES:
            die(EXIT_SCHEMA, "问题 %r 的 type=%r，必须是 choice/score/noul" % (qid, qtype))
        if "instructions" not in qdef:
            die(EXIT_SCHEMA, "问题 %r 缺少 instructions" % qid)
        if qtype == "choice":
            crit = qdef.get("criteria")
            if not isinstance(crit, dict) or not crit:
                die(EXIT_SCHEMA, "choice 问题 %r 需要非空的 criteria 字典" % qid)
            if len(crit) > 32:
                die(EXIT_SCHEMA, "choice 问题 %r 选项 %d 超过 32" % (qid, len(crit)))
        elif qtype == "score":
            crit = qdef.get("criteria")
            if not isinstance(crit, (list, dict)) or not crit:
                die(EXIT_SCHEMA, "score 问题 %r 需要非空的 criteria 列表/字典" % qid)
            n = len(crit.values()) if isinstance(crit, dict) else len(crit)
            if n > 10:
                die(EXIT_SCHEMA, "score 问题 %r 等级 %d 超过 10" % (qid, n))


def score_levels(qdef: Dict[str, Any]) -> List[str]:
    crit = qdef.get("criteria", [])
    return list(crit.values()) if isinstance(crit, dict) else list(crit)


# --------------------------------------------------------------------------- #
# 专家注册表
# --------------------------------------------------------------------------- #
def load_registry() -> Dict[str, Any]:
    return load_json(REGISTRY_FILE, "专家注册表")


_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_DIGIT = re.compile(r"\d+")


def keywords(text: str) -> set:
    """中英混合分词：CJK 二元组 + 长度>=2 的 ASCII 词 + 数字。"""
    out: set = set()
    chars = [c for c in re.sub(r"\s+", "", text) if _CJK.match(c)]
    for i in range(len(chars) - 1):
        out.add(chars[i] + chars[i + 1])
    for m in _ASCII_WORD.findall(text):
        if len(m) >= 2:
            out.add(m.lower())
    for m in _DIGIT.findall(text):
        out.add(m)
    return out


def route_experts(state_text: str, registry: Dict[str, Any], top_k: int = 3
                  ) -> List[Dict[str, Any]]:
    """按 state 与各专家 triggers 的重叠度排序，返回 Top-K。"""
    state_keys = keywords(state_text)
    scored: List[Dict[str, Any]] = []
    for eid, edef in registry.get("experts", {}).items():
        trig_keys: set = set()
        for t in edef.get("triggers", []):
            trig_keys |= keywords(t)
        hit = len(state_keys & trig_keys)
        weight = float(edef.get("weight", 1.0))
        scored.append({
            "expert": eid,
            "skill_name": edef.get("skill_name", eid),
            "title": edef.get("title", eid),
            "match_score": hit,
            "weighted": round(hit * weight, 3),
            "schema": edef.get("schema"),
        })
    scored.sort(key=lambda x: (x["weighted"], x["match_score"]), reverse=True)
    return scored[:top_k]


# --------------------------------------------------------------------------- #
# 离线启发式兜底（无网络、无外部依赖时仍可跑出 baseline）
# --------------------------------------------------------------------------- #
_POS_CUES = {
    "加密", "混淆", "加壳", "压缩", "未知", "缺失", "不确定", "推测", "猜测", "可能",
    "版权", "授权", "许可", "恶意", "危险", "敏感", "未签名", "无符号", "剥离",
    "encrypt", "obfuscat", "packed", "packed_", "strip", "unknown", "guess",
    "speculat", "ambiguous", "legal", "license", "copyright", "malicious",
    "unsafe", "risk", "missing", "unclear", "needs", "requires",
}
_NEG_CUES = {
    "明文", "可读", "源码", "符号", "完整", "确定", "明确", "已有", "存在", "清晰",
    "plain", "readable", "source", "symbol", "complete", "clear", "documented",
    "self_describing", "simple", "static", "direct",
}


def _margin_conf(top: int, second: int) -> float:
    return round(min(0.95, 0.4 + 0.55 * ((top - second) / (top + second + 1e-6))), 3)


def _heuristic_choice(state_keys: set, crit: Dict[str, str]) -> Tuple[Optional[str], float]:
    ranked = sorted(((lb, len(state_keys & keywords(std))) for lb, std in crit.items()),
                    key=lambda kv: kv[1], reverse=True)
    top_label, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if top == 0:
        return ("other" if "other" in crit else None), 0.0
    return top_label, _margin_conf(top, second)


def _heuristic_score(state_keys: set, levels: List[str]) -> Tuple[int, float]:
    scores = [len(state_keys & keywords(lv)) for lv in levels]
    best = max(range(len(scores)), key=lambda i: scores[i])
    if scores[best] == 0:
        return len(levels) // 2, 0.0
    second = sorted(scores, reverse=True)[1] if len(scores) > 1 else 0
    return best, _margin_conf(scores[best], second)


def _heuristic_noul(state_text: str) -> Tuple[bool, float]:
    pos = sum(1 for c in _POS_CUES if c in state_text)
    neg = sum(1 for c in _NEG_CUES if c in state_text)
    if pos == 0 and neg == 0:
        return False, 0.0
    prob = 1.0 / (1.0 + math.exp(-1.2 * (pos - neg)))
    conf = round(min(0.9, 0.3 + 0.4 * abs(pos - neg)), 3)
    return (prob >= 0.5), conf


def heuristic_decide(state_text: str, questions: Dict[str, Any]) -> Dict[str, Any]:
    state_keys = keywords(state_text)
    out: Dict[str, Any] = {}
    for qid, qdef in questions.items():
        qtype = qdef["type"]
        if qtype == "choice":
            val, conf = _heuristic_choice(state_keys, qdef["criteria"])
            out[qid] = {"type": "choice", "value": val, "confidence": conf,
                        "abstained": val is None, "evidence": "E2"}
        elif qtype == "score":
            val, conf = _heuristic_score(state_keys, score_levels(qdef))
            out[qid] = {"type": "score", "value": val, "confidence": conf,
                        "abstained": conf <= 0.0, "evidence": "E2"}
        else:
            val, conf = _heuristic_noul(state_text)
            out[qid] = {"type": "noul", "value": val, "confidence": conf,
                        "abstained": conf <= 0.0, "evidence": "E2"}
    return out


# --------------------------------------------------------------------------- #
# 归一化 + 闸门
# --------------------------------------------------------------------------- #
def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "y", "1", "是", "需要", "要", "存在"):
            return True
        if s in ("false", "no", "n", "0", "否", "不", "无", "未"):
            return False
    return None


def _as_evidence(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in EVIDENCE_LEVELS:
        return s
    if s.isdigit() and 0 <= int(s) <= 3:
        return "E%d" % int(s)
    if s in ("fact", "observed", "观测", "事实"):
        return "E0"
    if s in ("speculation", "guess", "推测"):
        return "E3"
    return "E2"


def normalize_answer(questions: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for qid, qdef in questions.items():
        if qid not in raw:
            out[qid] = {"type": qdef["type"], "value": None, "confidence": 0.0,
                        "abstained": True, "evidence": "E3", "note": "missing"}
            continue
        item = raw[qid] if isinstance(raw[qid], dict) else {"value": raw[qid]}
        conf = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
        ev = _as_evidence(item.get("evidence"))
        qtype = qdef["type"]
        if qtype == "choice":
            crit = qdef["criteria"]
            want = str(item.get("value", "")).strip()
            key = want if want in crit else {k.lower(): k for k in crit}.get(want.lower())
            out[qid] = {"type": "choice", "value": key, "confidence": conf,
                        "abstained": key is None, "evidence": ev}
            if key is None:
                out[qid]["note"] = "value %r 不在选项集内" % want
        elif qtype == "score":
            n = len(score_levels(qdef))
            try:
                val = int(round(float(item.get("value"))))
            except (TypeError, ValueError):
                val = None
            if val is None or not (0 <= val < n):
                out[qid] = {"type": "score", "value": None, "confidence": 0.0,
                            "abstained": True, "evidence": ev, "note": "score 越界或非整数"}
            else:
                out[qid] = {"type": "score", "value": val, "confidence": conf,
                            "abstained": False, "evidence": ev}
        else:
            val = _as_bool(item.get("value"))
            out[qid] = {"type": "noul", "value": val, "confidence": conf,
                        "abstained": val is None, "evidence": ev}
            if val is None:
                out[qid]["note"] = "noul 值无法解析为布尔"
    return out


def apply_gate(decision: Dict[str, Any], min_confidence: float) -> Dict[str, Any]:
    for ans in decision.values():
        if ans["confidence"] < min_confidence:
            ans["abstained"] = True
            if ans["type"] in ("choice", "score"):
                ans["value"] = None
    return decision


def _gate_bucket(qid: str) -> Optional[str]:
    low = qid.lower()
    if any(k in low for k in HUMAN_GATE_KEYWORDS):
        return "human"
    if any(k in low for k in EVIDENCE_GATE_KEYWORDS):
        return "evidence"
    return None


def route_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """升级路由：法律/安全闸门 -> human；证据不足闸门 -> system2(取证)；弃权 -> system2。"""
    abstained = [q for q, a in decision.items() if a["abstained"]]
    human_flags, evidence_flags = [], []
    for qid, a in decision.items():
        if a["type"] == "noul" and a["value"] is True:
            bucket = _gate_bucket(qid)
            if bucket == "human":
                human_flags.append(qid)
            elif bucket == "evidence":
                evidence_flags.append(qid)
    # E3 推测且被当作确定结论 -> 同样视为证据不足
    spec = [q for q, a in decision.items()
            if a.get("evidence") == SPECULATION_LEVEL and not a["abstained"]]
    if human_flags:
        escalate, to = True, "human"
    elif evidence_flags or abstained:
        escalate, to = True, "system2"
    else:
        escalate, to = False, None
    return {
        "escalate": escalate,
        "escalate_to": to,
        "abstained": abstained,
        "human_flags": human_flags,
        "evidence_flags": evidence_flags,
        "speculative_fields": spec,
        "note": _route_note(escalate, to, abstained, human_flags, evidence_flags, spec),
    }


def _route_note(esc, to, abstained, human, evi, spec) -> str:
    if to == "human":
        return ("命中法律/安全闸门 %s：必须停下等待人工确认，不自行推进逆向或复刻。" %
                ", ".join(human))
    if to == "system2":
        parts = []
        if evi:
            parts.append("证据不足闸门 %s" % ", ".join(evi))
        if abstained:
            parts.append("字段 %s 置信度低于闸门已弃权" % ", ".join(abstained))
        return "；".join(parts) + "。需升级到 System 2 取证，禁止把推测写成结论。"
    if spec:
        return "可直接采用；注意 %s 为 E3 推测级，报告中须显式标注。" % ", ".join(spec)
    return "全部字段置信度达标，可直接采用（仍需标注证据等级）。"


# --------------------------------------------------------------------------- #
# 共识 / 审计
# --------------------------------------------------------------------------- #
def consensus(expert_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not expert_results:
        return {"verdict": "no_input", "escalate_to": None, "experts": [],
                "note": "无专家结果"}
    human = [r["expert"] for r in expert_results
             if r.get("routing", {}).get("escalate_to") == "human"]
    sys2 = [r["expert"] for r in expert_results
            if r.get("routing", {}).get("escalate_to") == "system2"]
    if human:
        verdict, to = "human_review", "human"
    elif sys2:
        verdict, to = "evidence_needed", "system2"
    else:
        verdict, to = "pass", None
    return {
        "verdict": verdict,
        "escalate_to": to,
        "human_flags_from": human,
        "evidence_flags_from": sys2,
        "experts": [r["expert"] for r in expert_results],
        "note": _consensus_note(verdict, human, sys2),
    }


def _consensus_note(verdict, human, sys2) -> str:
    if verdict == "human_review":
        return ("专家团判定需人工确认（命中法律/安全闸门的专家：%s）。"
                "禁止自动推进，先停下等待人工。" % ", ".join(human))
    if verdict == "evidence_needed":
        return ("专家团中 %s 判定证据不足，需 System 2 取证；"
                "在补齐证据前不得下确定性结论。" % ", ".join(sys2))
    return "专家团一致通过：证据充分、无法律/安全闸门、无弃权，可产出逆向结论。"


def default_audit_dir() -> str:
    return os.environ.get(AUDIT_DIR_ENV) or os.path.join(
        os.path.expanduser("~"), ".cache", "rea-expert-council")


def write_audit(record: Dict[str, Any], audit_dir: str) -> str:
    os.makedirs(audit_dir, exist_ok=True)
    path = os.path.join(audit_dir, "audit.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(record, ts=time.time()), ensure_ascii=False) + "\n")
    return path


def read_audit(audit_dir: str) -> List[Dict[str, Any]]:
    path = os.path.join(audit_dir, "audit.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def audit_stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(records)
    if n == 0:
        return {"total": 0}
    esc = sum(1 for r in records if r.get("routing", {}).get("escalate"))
    human = sum(1 for r in records if r.get("routing", {}).get("escalate_to") == "human")
    sys2 = sum(1 for r in records if r.get("routing", {}).get("escalate_to") == "system2")
    by_expert: Dict[str, int] = {}
    for r in records:
        e = r.get("expert") or r.get("domain") or "unknown"
        by_expert[e] = by_expert.get(e, 0) + 1
    return {"total": n, "escalated": esc, "escalate_rate": round(esc / n, 3),
            "to_human": human, "to_system2": sys2, "by_expert": by_expert}


# --------------------------------------------------------------------------- #
# 提示词生成
# --------------------------------------------------------------------------- #
def build_decision_prompt(state_text: str, state_key: str,
                          questions: Dict[str, Any]) -> str:
    lines = [
        "你是一个 REA（Reverse Engineer Anything）System 1 决策引擎。",
        "给定一段待逆向目标的 state 和一组**固定题型**，只输出 JSON 对象，不要解释、不要多余文字。",
        "",
        "输出格式：",
        '  {"<问题id>": {"value": <答案>, "confidence": <0~1>, "evidence": "E0|E1|E2|E3"}}',
        "  - choice 题：value 是 criteria 里的标签名（字符串）",
        "  - score  题：value 是整数等级（0 起，上界为等级数-1）",
        "  - noul   题：value 是 true / false",
        "  - confidence：你对该答案正确的校准概率，不确定就给低值",
        "  - evidence：支撑该答案的证据等级 ——",
        "      E0 直接观测（字节/源码行/可运行输出）；E1 强推断（有明确证据链，如魔数→格式）；",
        "      E2 弱推断（模式匹配/经验）；E3 推测（无直接证据，必须标注）",
        "",
        "state（键=%s）：" % state_key,
        state_text,
        "",
        "问题集：",
    ]
    for qid, qdef in questions.items():
        qtype = qdef["type"]
        lines.append("- %s [%s] %s" % (qid, qtype, qdef.get("instructions", "")))
        if qtype == "choice":
            for label, std in qdef["criteria"].items():
                lines.append("    · %s：%s" % (label, std))
        elif qtype == "score":
            for i, lv in enumerate(score_levels(qdef)):
                lines.append("    · %d：%s" % (i, lv))
    lines += ["", "只输出 JSON："]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 决策入口
# --------------------------------------------------------------------------- #
def run_decide(state_text: str, state_key: str, questions: Dict[str, Any],
               raw_answer: Optional[Dict[str, Any]], min_confidence: float,
               source_label: str, expert_id: Optional[str] = None) -> Dict[str, Any]:
    decision = (normalize_answer(questions, raw_answer) if raw_answer is not None
                else heuristic_decide(state_text, questions))
    apply_gate(decision, min_confidence)
    payload: Dict[str, Any] = {
        "state_key": state_key,
        "decision": decision,
        "routing": route_decision(decision),
        "source": source_label,
        "min_confidence": min_confidence,
        "usage": {"generated_tokens_estimate": 0, "external_model": False},
    }
    if expert_id:
        payload["expert"] = expert_id
    return payload


# --------------------------------------------------------------------------- #
# 自测
# --------------------------------------------------------------------------- #
SELFTEST_CASES = [
    {
        "name": "加密二进制-证据不足应升级",
        "state_key": "target",
        "state": "一个 2MB 的 .bin 文件，熵值 7.98，strings 几乎无可读字符串，无已知魔数，来源不明",
        "questions": {
            "representation": {"type": "choice",
                               "instructions": "目标的表示形态？",
                               "criteria": {"source": "可读源码", "bytecode": "字节码/中间码",
                                            "native": "原生机器码", "packed_obfuscated": "加壳/混淆",
                                            "unknown": "无法判断"}},
            "evidence_gap": {"type": "noul",
                             "instructions": "判断缺乏直接证据、主要靠推测吗？"},
        },
        "answer": {"representation": {"value": "packed_obfuscated", "confidence": 0.7,
                                      "evidence": "E2"},
                   "evidence_gap": {"value": True, "confidence": 0.8, "evidence": "E1"}},
        "expect_escalate_to": "system2",
    },
    {
        "name": "开源源码-高置信应通过",
        "state_key": "target",
        "state": "GitHub 上 MIT 许可的 Python 仓库，含完整源码、README 与单元测试",
        "questions": {
            "representation": {"type": "choice", "instructions": "目标的表示形态？",
                               "criteria": {"source": "可读源码", "bytecode": "字节码",
                                            "native": "原生机器码", "unknown": "无法判断"}},
            "legal_or_safety_risk": {"type": "noul",
                                     "instructions": "是否涉及授权/许可/恶意代码风险？"},
        },
        "answer": {"representation": {"value": "source", "confidence": 0.95, "evidence": "E0"},
                   "legal_or_safety_risk": {"value": False, "confidence": 0.9,
                                            "evidence": "E0"}},
        "expect_escalate_to": None,
    },
    {
        "name": "加壳样本-法律闸门应转人工",
        "state_key": "target",
        "state": "疑似恶意的加壳样本，含反调试与自解密 stub，来源为第三方且无授权",
        "questions": {
            "legal_or_safety_risk": {"type": "noul",
                                     "instructions": "是否涉及授权/恶意代码风险？"},
        },
        "answer": {"legal_or_safety_risk": {"value": True, "confidence": 0.9, "evidence": "E0"}},
        "expect_escalate_to": "human",
    },
]


def selftest() -> int:
    print("rea_engine 自测（零依赖、无联网）\n")
    all_ok = True
    for case in SELFTEST_CASES:
        qs = case["questions"]
        res = run_decide(case["state"], case["state_key"], qs, case["answer"],
                         0.6, "selftest", "selftest")
        got = res["routing"]["escalate_to"]
        ok = got == case["expect_escalate_to"]
        all_ok &= ok
        print("  [%s] %s -> escalate_to=%r (期望 %r)"
              % ("PASS" if ok else "FAIL", case["name"], got, case["expect_escalate_to"]))
    reg = load_registry()
    print("\n专家路由（state='未知格式的二进制协议抓包，含 TLV 结构'）：")
    for r in route_experts("未知格式的二进制协议抓包，含 TLV 结构", reg, top_k=3):
        print("  - %-22s %s  match=%s weighted=%s"
              % (r["expert"], r["title"], r["match_score"], r["weighted"]))
    print("\n%s" % ("全部通过" if all_ok else "存在失败用例"))
    return 0 if all_ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="rea_engine", description="REA 专家团决策中枢")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("experts", help="列出专家团成员")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("route", help="选择最相关的专家")
    p.add_argument("--state", required=True)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("prompt", help="生成 System 1 决策提示词")
    p.add_argument("--schema-file", required=True)
    p.add_argument("--state", required=True)

    p = sub.add_parser("validate", help="归一化 + 闸门 + 路由")
    p.add_argument("--schema-file", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--answer", required=True, help="作答结果 JSON（字符串或 @文件）")
    p.add_argument("--min-confidence", type=float, default=0.6)

    p = sub.add_parser("decide", help="完整链路")
    p.add_argument("--schema-file", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--answer", help="给了走 LLM 路径，否则离线启发式")
    p.add_argument("--min-confidence", type=float, default=0.6)
    p.add_argument("--expert")

    p = sub.add_parser("consensus", help="融合多专家决策")
    p.add_argument("--results", required=True, help="专家结果 JSON 数组（字符串或 @文件）")

    p = sub.add_parser("audit", help="审计统计")
    p.add_argument("--dir")

    sub.add_parser("selftest", help="内置样例自测")

    args = ap.parse_args(argv)

    if args.cmd == "experts":
        reg = load_registry()
        rows = [{"expert": eid, "title": d.get("title"), "weight": d.get("weight"),
                 "triggers": d.get("triggers", []), "schema": d.get("schema")}
                for eid, d in reg.get("experts", {}).items()]
        print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json
              else "\n".join("%-22s %s (w=%s)" % (r["expert"], r["title"], r["weight"])
                             for r in rows))
        return 0

    if args.cmd == "route":
        res = route_experts(args.state, load_registry(), args.top_k)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else "\n".join("%-22s %-14s match=%s weighted=%s"
                             % (r["expert"], r["title"], r["match_score"], r["weighted"])
                             for r in res))
        return 0

    if args.cmd == "prompt":
        _, qs = load_schema(args.schema_file)
        print(build_decision_prompt(args.state, "target", qs))
        return 0

    def load_answer(spec: str) -> Dict[str, Any]:
        if spec.startswith("@"):
            return load_json(spec[1:], "答案文件")
        return json.loads(spec)

    if args.cmd == "validate":
        _, qs = load_schema(args.schema_file)
        res = run_decide(args.state, "target", qs, load_answer(args.answer),
                         args.min_confidence, "llm")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "decide":
        _, qs = load_schema(args.schema_file)
        raw = load_answer(args.answer) if args.answer else None
        res = run_decide(args.state, "target", qs, raw, args.min_confidence,
                         "llm" if raw else "heuristic", args.expert)
        write_audit(res, default_audit_dir())
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "consensus":
        results = load_answer(args.results)
        print(json.dumps(consensus(results), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "audit":
        d = args.dir or default_audit_dir()
        print(json.dumps(audit_stats(read_audit(d)), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "selftest":
        return selftest()

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
