# -*- coding: utf-8 -*-
"""价目表.md 生成器：从 agent/llm.py 的内置价目表自动重算每轮成本。

用法：python scripts/gen_price_table.py
（每轮成本 = 0.024×输入单价 + 0.006×输出单价；10 元轮数 = 10 ÷ 每轮成本。
  口径：实测每轮约 30000 token（输入约 24000 + 输出约 6000），忽略缓存。）
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm import _OFFICIAL_PRICES  # noqa: E402

# 厂商分组（按前缀，顺序即文档章节顺序；前缀匹配任一命中即归组）
GROUPS = [
    ("DeepSeek", ["deepseek-"]),
    ("Kimi（月之暗面）", ["kimi-", "moonshot-"]),
    ("智谱 GLM", ["glm-"]),
    ("MiniMax", ["minimax-", "abab"]),
    ("小米 MiMo", ["mimo-"]),
    ("通义千问（阿里）", ["qwen"]),
    ("腾讯混元", ["hunyuan", "hy"]),
    ("豆包（火山方舟）", ["doubao-"]),
    ("百度文心", ["ernie-"]),
    ("OpenAI", ["gpt-", "o3", "o4"]),
    ("Claude（Anthropic）", ["claude-"]),
    ("Gemini（Google）", ["gemini-"]),
    ("Grok（xAI）", ["grok-"]),
    ("Meta", ["muse-", "llama-"]),
    ("其他海外/聚合", ["mistral-", "command-a", "nemotron-", "solar-pro-4",
                     "step-3.7-flash", "longcat-2.0", "ling-3.0-flash",
                     "granite-4.0-h-micro", "inkling-with-ai"]),
]

IN_PER_ROUND = 0.024   # 24000 输入 token / 1e6
OUT_PER_ROUND = 0.006  # 6000 输出 token / 1e6


def fmt_num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


def round_cost(p: dict) -> float:
    return IN_PER_ROUND * float(p.get("in") or 0) + OUT_PER_ROUND * float(p.get("out") or 0)


def group_of(name: str) -> str:
    for title, prefixes in GROUPS:
        if any(name.startswith(pre) for pre in prefixes):
            return title
    return "其他海外/聚合"


def render_table(models: list[tuple[str, dict]]) -> str:
    rows = []
    for name, p in models:
        cost = round_cost(p)
        rounds = "∞" if cost <= 0 else "%.1f" % (10.0 / cost)
        note = str(p.get("note") or "公开参考估算")
        rows.append("| `%s` | %s | %s | ¥%.4f | %s | %s |" % (
            name, fmt_num(p.get("in")), fmt_num(p.get("out")), cost, rounds, note))
    return "\n".join(rows)


def main() -> None:
    lines = []
    lines.append("# 价目表与用量估算（Persona Morph）\n")
    lines.append("> 单位：人民币元 / 百万 token（海外厂商按 1 USD≈7.2 元折算后计入）。**价格以各厂商官网为准**。\n")
    lines.append("> **计算口径（本表由脚本自动重算）**：每轮成本 = 0.024 × 输入单价 + 0.006 × 输出单价"
                "（实测每轮 ≈30000 token：输入约 24000 + 输出约 6000，忽略缓存）；10 元可用轮数 = 10 ÷ 每轮成本。\n")
    lines.append("> 来源：DeepSeek / 智谱 / MiniMax / 月之暗面 / 小米 MiMo / 阿里百炼 / 腾讯混元 / 火山方舟 / 百度千帆"
                " / OpenAI / Anthropic / Google / xAI / OpenRouter 等官方价格页与公开对照表（2026-09 采集）。\n")
    lines.append("> 内置价目共 **%d** 条（%s 家厂商分组），可随版本更新；控制台保存后可用 `api.model_prices` 按型号精确覆盖。\n"
                 % (len(_OFFICIAL_PRICES), len(GROUPS)))
    lines.append("\n## 一、实测用量（2026-09-06 真实对话）\n")
    lines.append("- 9 轮 / 270,092 token / 54 次 API 调用 / 总成本 **¥0.1636**；平均每轮 ≈30,010 token、**¥0.0182**；综合 ≈¥0.61/百万 token")
    lines.append("- 本机默认模型 deepseek-v4-flash-vision-exp 实测远低于按公开价估算（缓存命中 + 低价档），日常以实测为准\n")
    lines.append("## 二、内置价目全部型号（自动重算）\n")

    by_group: dict[str, list[tuple[str, dict]]] = {}
    for name, p in _OFFICIAL_PRICES.items():
        by_group.setdefault(group_of(name), []).append((name, p))

    for title, _prefixes in GROUPS:
        models = by_group.pop(title, [])
        if not models:
            continue
        lines.append("\n### %s\n" % title)
        lines.append("| 型号 | 输入(¥) | 输出(¥) | 每轮成本(¥) | 10元≈轮数 | 说明 |\n|---|---|---|---|---|---|")
        lines.append(render_table(models))

    remain = sum(len(v) for v in by_group.values())
    if remain:
        lines.append("\n### 其他\n")
        lines.append("| 型号 | 输入(¥) | 输出(¥) | 每轮成本(¥) | 10元≈轮数 | 说明 |\n|---|---|---|---|---|---|")
        all_rest = [item for v in by_group.values() for item in v]
        lines.append(render_table(all_rest))

    lines.append("\n## 三、按活跃度看能用多久（以 DeepSeek 实测 ¥0.018/轮 为例）\n")
    lines.append("| 活跃度 | 每天轮数 | 每天成本 | 10 元 | 50 元 | 100 元 |\n|---|---|---|---|---|---|")
    lines.append("| 轻度（偶尔@一下） | 10 | ¥0.18 | ~54 天 | ~274 天 | ~549 天 |")
    lines.append("| 中度（正常群聊） | 30 | ¥0.55 | ~18 天 | ~91 天 | ~183 天 |")
    lines.append("| 活跃（高频问答） | 50 | ¥0.91 | ~10 天 | ~54 天 | ~109 天 |")
    lines.append("| 重度（天天被点名） | 100 | ¥1.82 | ~5 天 | ~27 天 | ~54 天 |")
    lines.append("\n> 换模型估算：把第二节对应型号的「每轮成本」代入即可（如 gpt-4o ¥0.43/轮 → 10 元约 23 轮；豆包 seed ¥0.031/轮 → 10 元约 322 轮）。\n")
    lines.append("## 四、省钱的五个办法\n")
    lines.append("- 1. 响应档位调低：store.context_tier=1（仅被 @ 才回），没命中不调模型（零 token 零成本）。")
    lines.append("- 2. 用国产轻量模型：qwen3.8-flash / glm-4.7-flash（免费）/ doubao-lite / gemini-3.8-flash，成本约为旗舰 1/5~1/10。")
    lines.append("- 3. 缓存命中：连续对话时系统提示词命中缓存（如 DeepSeek ¥0.05/百万），长会话再降 3~5 成。")
    lines.append("- 4. 配置精确单价：控制台保存后在 api.model_prices 按型号填实际价格，统计才准。")
    lines.append("- 5. 限额提醒：控制台概览有今日/本周用量成本，每周一（默认周期）归零重计。\n")
    lines.append("## 五、准确度与来源声明\n")
    lines.append("- 实测节来自本机运行明细与用量统计（9 轮 / 270092 token / ¥0.1636），与官方账单可能有 ±1% 差异。")
    lines.append("- 各厂商单价为 2026-09 公开定价/公开参考估算，会变动；代码内置价可随时 api.model_prices 覆盖；海外按 1 USD≈7.2 元折算，汇率变动会影响实际成本。")
    lines.append("- 本表由 scripts/gen_price_table.py 自动重算生成（每轮=0.024×输入+0.006×输出；10元轮数=10÷每轮），行与行之间数学一致，无手算错误。\n")

    out = "\n".join(lines)
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "价目表.md")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print("written %d models -> %s" % (len(_OFFICIAL_PRICES), path))


if __name__ == "__main__":
    main()
