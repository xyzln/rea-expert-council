# 专家团协作机制

## 路由：什么时候上哪位专家

`route` 给出候选排序，但最终由**目标形态**决定组合，而非机械取 Top-K。
典型组合：

| 目标 | 建议组合 |
|---|---|
| 未知二进制/样本 | structure → binary-reverser → behavior |
| 抓包/接口报文 | protocol-decoder → behavior → rebuild |
| 一个仓库 | structure → behavior → intent → rebuild |
| 一个产品功能 | structure → intent → rebuild |
| 一份数据文件 | protocol-decoder → structure |
| 一条业务流程 | structure → behavior → intent |

**原则**：结构专家几乎总是第一站；复刻专家只在用户明确要"照着做一个"时才上。

---

## 冲突消解

不同专家给出矛盾结论时，按以下顺序裁定：

1. **证据等级高者胜**：E0 > E1 > E2 > E3。高等级证据能否定低等级结论。
2. **可复现者胜**：能给出复现步骤的结论优先于只能给出理由的结论。
3. **直接观测胜于推断**：实测行为 > 静态分析 > 经验类比。
4. **仍无法裁定 → 同时保留并升级**：不要强行选一个。
   把矛盾双方连同各自证据并列写进报告，标记 `escalate_to: system2`，
   并设计能区分二者的**判决性实验**。

> 判决性实验：一个能让两个互斥假设给出不同预测的实验。想不出判决性实验，
> 说明两者其实不矛盾，只是粒度不同。

---

## 共识规则

引擎 `consensus` 取**最严**结论：

- 任一专家命中人工闸门 → 团队 `human_review`
- 否则任一专家证据不足 → 团队 `evidence_needed`
- 否则 → `pass`

理由：逆向工程的错误成本极不对称——漏报一个"不知道"只是慢，
错报一个"知道了"会让后续全部工作建立在沙上。

---

## 专家的 System 1 / System 2 双模

每位专家有两套工作模式：

- **System 1**（默认）：对固定题型快速作答，只输出结构化 JSON，带置信度与证据等级。
  快、可校验、可审计，但浅。
- **System 2**（升级时）：命中闸门或置信度不足时启用，执行该专家 `SKILL.md` 中
  的**取证协议**——具体到用什么命令、看什么、怎么设计对照实验。慢、深、可追溯。

切换由引擎闸门**强制触发**，不是可选优化。

---

## 新增专家

1. 建目录 `experts/<domain>/`，写 `SKILL.md`（角色 + 题型 + System 2 取证协议）
   与 `schema.json`（题型定义）。
2. 在 `experts/_registry.json` 注册：`skill_name` / `dir` / `schema` / `title` /
   `weight` / `triggers`。
3. 闸门字段按命名约定取名（见 `references/engine.md`）。
4. 跑 `python3 scripts/rea_engine.py selftest` 与 `route` 验证路由生效。
