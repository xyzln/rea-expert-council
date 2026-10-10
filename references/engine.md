# 引擎用法（scripts/rea_engine.py）

纯标准库、零联网、跨平台。所有命令从 skill 根目录执行。

---

## experts — 列出专家团

```bash
python3 scripts/rea_engine.py experts            # 简表
python3 scripts/rea_engine.py experts --json     # 含 triggers 全量
```

## route — 选专家

```bash
python3 scripts/rea_engine.py route --state '未知格式的二进制抓包，含 TLV 结构' --top-k 3
```

按 state 与每位专家 `triggers` 的关键词重叠度打分（CJK 二元组 + ASCII 词 + 数字），
再乘专家权重排序。`--json` 取机器可读结果。

## prompt — 生成 System 1 决策提示词

```bash
python3 scripts/rea_engine.py prompt \
  --schema-file experts/protocol-decoder/schema.json \
  --state '一段 64 字节的报文，前 4 字节恒为 0xDEADBEEF'
```

把输出作为给自己的指令，按格式作答（只输出 JSON）。

## validate — 归一化 + 闸门 + 路由

```bash
python3 scripts/rea_engine.py validate \
  --schema-file experts/protocol-decoder/schema.json \
  --state '<目标描述>' \
  --answer '{"format_class":{"value":"tlv","confidence":0.8,"evidence":"E2"},
             "speculation_risk":{"value":true,"confidence":0.7,"evidence":"E1"}}'
```

`--answer` 接受 JSON 字符串，或 `@文件路径`（如 `@answer.json`）。

归一化规则：
- `choice` 值不在选项集内 → 弃权（大小写不敏感匹配）
- `score` 越界或非整数 → 弃权
- `noul` 无法解析为布尔 → 弃权（接受 `true/false/1/0/是/否/需要/无` 等）
- `confidence` 夹到 `[0,1]`
- `evidence` 归一化到 `E0|E1|E2|E3`（也接受 `0`~`3`、`fact`、`speculation`）

## decide — 完整链路

```bash
# 走 LLM 路径
python3 scripts/rea_engine.py decide \
  --schema-file experts/binary-reverser/schema.json \
  --state '<目标描述>' --answer '<JSON>' --expert binary-reverser

# 不带 --answer：走内置离线启发式兜底（未提供作答结果时仍有 baseline）
python3 scripts/rea_engine.py decide \
  --schema-file experts/binary-reverser/schema.json --state '<目标描述>'
```

`decide` 会自动写审计日志。可用 `--min-confidence 0.7` 调高闸门。

## consensus — 多专家融合

```bash
python3 scripts/rea_engine.py consensus --results @results.json
```

`results` 是数组，每项形如：

```json
{"expert": "protocol-decoder",
 "decision": {...},
 "routing": {"escalate": true, "escalate_to": "system2", ...},
 "source": "llm", "min_confidence": 0.6}
```

融合规则（**取最严**）：
- 任一专家 `escalate_to == "human"` → 团队 `human_review`
- 否则任一 `escalate_to == "system2"` → 团队 `evidence_needed`
- 否则 → `pass`

## audit — 审计统计

```bash
python3 scripts/rea_engine.py audit                    # 默认目录
python3 scripts/rea_engine.py audit --dir /tmp/rea     # 指定目录
```

默认审计目录：`$REA_AUDIT_DIR` 或 `~/.cache/rea-expert-council/audit.jsonl`。
输出：总量、升级数、升级率、转人工数、升级到 System 2 数、按专家分布。

## selftest — 自测

```bash
python3 scripts/rea_engine.py selftest
```

覆盖三个关键分支：证据不足升级、高置信通过、法律闸门转人工。

---

## schema 格式

```json
{
  "state_key": "target",
  "domain": "protocol-decoder",
  "title": "协议与数据格式专家决策题型",
  "questions": {
    "format_class": {
      "type": "choice",
      "instructions": "数据组织形式属于哪类？",
      "criteria": {"tlv": "标签-长度-值 结构", "unknown": "无法判断"}
    },
    "obfuscation_level": {
      "type": "score",
      "instructions": "混淆强度？",
      "criteria": ["无混淆", "轻度", "中度", "重度"]
    },
    "speculation_risk": {
      "type": "noul",
      "instructions": "字段含义是否主要靠猜？"
    }
  }
}
```

约束：`choice` ≤ 32 选项；`score` ≤ 10 等级；总题数 ≤ 32。违反即退出码 3。

**闸门靠字段名命中**：字段 id 含 `evidence_*` 类关键词 → 证据不足闸门；
含 `legal`/`malicious`/`safety` 等 → 人工闸门。新增题型时按此约定命名。

## 退出码

`0` 正常 | `2` 用法错误 | `3` schema 错误
