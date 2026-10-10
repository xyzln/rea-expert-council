---
name: rea-expert-council
slug: rea-expert-council
displayName: REA逆向工程专家团
version: 1.0.0
author: rea-council
description: >-
  REA（Reverse Engineer Anything）逆向工程专家团。一个决策中枢协调 6 位领域专家
  （结构解剖 / 行为动态 / 二进制逆向 / 协议解码 / 意图还原 / 复刻规划），
  按「五层还原法」把任意未知目标从「它是什么」推到「怎么照着做一个」。
  内置反幻觉机制：E0~E3 证据分级、证据不足闸门强制补证、法律与安全闸门转人工。
  自带零依赖体检器 peek.py 做第一轮勘察（魔数/熵值/字符串/结构探测/目录测绘）。
  纯标准库、不联网、不装额外依赖，分析判断由加载本 skill 的运行环境承担。
metadata:
  type: skill
  category: 开发工具
  triggers:
    - 逆向
    - 反编译
    - 反汇编
    - 复刻
    - 还原
    - 拆解
    - 协议分析
    - 抓包
    - 二进制
    - 加壳
    - 文件格式
    - 字段含义
    - 怎么实现的
    - 照着做一个
    - reverse engineering
    - decompile
    - disassemble
  allowed-tools:
    - Bash
    - Read
    - Write
    - Grep
    - Glob
---

# REA 逆向工程专家团（Reverse Engineer Anything）

把一个**未知目标**还原到可解释、可复刻的程度。目标可以是：一个二进制、一段报文、
一个仓库、一份数据文件、一个产品界面、甚至一条业务流程。

> 一点说明：本 skill **不联网、不装任何额外依赖，也不下载任何权重**。
> 逆向研判由加载本 skill 的运行环境完成；脚本只负责约束输出格式、
> 校验置信度、记录证据等级，并在该停的时候逼它停下来。

## 三条铁律（比方法论更重要）

1. **证据优先**：每条结论必须能追溯到具体观测（字节、源码行、运行输出）。
   没有证据的部分标注 `E3 推测`，**不得进入结论章节**。
2. **边界诚实**：还原不了就写"还原不了"，并说明卡在哪一层。
   一个漂亮的猜测比一句"不知道"危险得多。
3. **不越线**：涉及授权、许可协议或疑似恶意代码时停止自动推进，转人工确认。

## 五层还原法（REA 的主干）

| 层 | 要回答的问题 | 主责专家 |
|---|---|---|
| L1 是什么 | 形态、类型、来源、可信度 | structure-anatomist |
| L2 由什么组成 | 部件、边界、依赖、入口 | structure-anatomist |
| L3 怎么运作 | 输入→输出、状态、副作用、字段语义 | behavior-analyst / protocol-decoder |
| L4 为什么这样 | 约束、取舍、设计意图 | intent-reconstructor |
| L5 怎么复刻 | 接口契约、数据结构、伪代码、验收测试 | rebuild-planner |

**逐层推进，不允许跳层**。L2 没看清就谈 L4，产出的通常是故事而不是结论。

## 专家团

| 专家 | 负责 | 关键闸门 |
|---|---|---|
| `structure-anatomist` | 形态识别、部件分解、边界与依赖 | `evidence_gap` |
| `behavior-analyst` | 运行行为、状态机、时序、副作用 | `needs_dynamic_probe` |
| `binary-reverser` | 二进制/字节码、符号、混淆、控制流 | `legal_or_safety_risk`（转人工） |
| `protocol-decoder` | 报文与数据格式、字段边界与语义 | `speculation_risk` |
| `intent-reconstructor` | 约束、取舍、设计意图 | `narrative_risk` |
| `rebuild-planner` | 规格固化、契约、伪代码、验收 | `blocking_unknowns` |

## 工作流

```bash
# ① 勘察：先看清它是什么，不要猜（只读，绝不改目标）
python3 scripts/peek.py <目标路径>                 # 文件：魔数/熵/字符串/结构
python3 scripts/peek.py <目录> --depth 3           # 目录：类型分布/入口点/高熵文件

# ② 路由：选本次该上哪几位专家
python3 scripts/rea_engine.py route --state '<目标描述或勘察结论>'

# ③ 出题：生成 System 1 决策提示词（喂给自己作答）
python3 scripts/rea_engine.py prompt \
    --schema-file experts/protocol-decoder/schema.json --state '<目标描述>'

# ④ 校验：把作答的 JSON 过闸门
python3 scripts/rea_engine.py validate \
    --schema-file experts/protocol-decoder/schema.json --state '<目标描述>' \
    --answer '{"format_class":{"value":"tlv","confidence":0.8,"evidence":"E2"}}'

# ⑤ 共识 + 审计
python3 scripts/rea_engine.py consensus --results @results.json
python3 scripts/rea_engine.py audit
```

作答格式（**只输出 JSON，不要解释**）：

```json
{"format_class": {"value": "tlv", "confidence": 0.8, "evidence": "E2"}}
```

`evidence` 取值：`E0` 直接观测 / `E1` 强推断 / `E2` 弱推断 / `E3` 推测。

**闸门行为**：命中 `evidence_*` 类字段 → 升级 System 2 取证（禁止下结论）；
命中 `legal*` / `malicious` / `safety*` → 直接转人工，停止推进。

## 报告模板

产出逆向报告时固定以下结构，未确认内容一律进最后一节：

1. **目标与边界**（是什么、授权状态、本次止于哪一层）
2. **结构**（部件清单 + 锚点证据）
3. **机制**（输入→输出、状态、字段映射表，含证据等级列）
4. **意图**（有据意图 / 可证伪假设，**分栏写，不得混写**）
5. **复刻规格**（接口契约、数据结构、伪代码、验收测试）
6. **未知与下一步**（未确认项、获取方式、阻断性未知）

## 诚实的边界

- 置信度是**自报置信度**，不是严格校准概率；高 stakes 场景请把 `min_confidence` 提到 0.7+。
- `peek.py` 只做**只读勘察**，不解密、不脱壳、不执行目标。
- 引擎内置的是**离线启发式 baseline**（关键词/熵值/魔数），用作客观校验参照。
- 不提供绕过授权、破解商业软件许可、或 weaponize 恶意代码的能力。

## 详见

- `references/method.md` — 五层还原法与取证手法
- `references/evidence.md` — E0~E3 证据分级与标注规范
- `references/engine.md` — 引擎完整命令与字段说明
- `references/expert-team.md` — 专家团协作与冲突消解
