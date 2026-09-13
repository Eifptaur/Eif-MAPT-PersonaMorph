# -*- coding: utf-8 -*-
"""W7 版本能力矩阵：把"某个微信版本 × 某个适配层版本"到底能做什么，记成**可查的数据**。

为什么需要它：微信会自动更新（本机 2026-09-13 就从 4.1.13.65 跳到 4.1.15.8，驱动库的 UIA 当场
退化成 OCR）。版本一换，"我们到底还能做什么"必须有据可查，而不是靠记性或外推。

**铁律（写进代码，不只是文档）：没实测过的版本对，一律返回 `unknown`，绝不拿相近版本外推。**
所以本模块只存"实测过的 run"；查询命中不到就是 unknown。

数据：`data/capability_matrix.json`（原子写 temp+os.replace）
  {"schema":1, "runs":[{"wechat":"4.1.15.8","adapter":"1.2.2.2","when":"…",
                        "caps":{"send_text":{"status":"ok","evidence":"…"}, …}}]}

状态取值：`ok` 实测可用 · `no` 实测不可用 · `unknown` 没测过（**不许当"支持"**）·
         `user_gated` 机制已通但需要用户点头才允许执行（如发朋友圈/点赞/评论）。
"""
from __future__ import annotations

import json
import logging
import os
import time

from .config import ROOT

log = logging.getLogger("persona-morph")

MATRIX_PATH = os.path.join(ROOT, "data", "capability_matrix.json")

# 能力目录：id → 标签 + 实现档位（数字越小越"干净"）
CAPS = {
    "send_text":        {"label": "发文本（投递打字 + 投递点发送）", "level": "L5"},
    "emoji_panel_open": {"label": "打开表情面板（投递点笑脸）", "level": "L5"},
    "emoji_send":       {"label": "发收藏表情包（投递点收藏格）", "level": "L5"},
    "moments_open":     {"label": "打开朋友圈（发现 → 朋友圈）", "level": "L5"},
    "moments_scroll":   {"label": "刷朋友圈信息流（投递滚轮）", "level": "L5"},
    "moments_composer": {"label": "打开发表编辑窗（长按相机 2.5s）", "level": "L5"},
    "moments_publish":  {"label": "点「发表」真发出去", "level": "L5"},
    "moments_like":     {"label": "朋友圈点赞", "level": "L0/L5"},
    "moments_comment":  {"label": "朋友圈评论", "level": "L0/L5"},
    "switch_chat":      {"label": "切会话", "level": "L5"},
    "image_send":       {"label": "发图片（剪贴板 + 投递）", "level": "L5"},
    "poke_menu":        {"label": "拍一拍右键菜单", "level": "L5"},
    "minimized_send":   {"label": "微信最小化时仍能发送", "level": "L5"},
    "uia_tree":         {"label": "UIA 控件树可用（L2 档）", "level": "L2"},
}

# 2026-09-13 本机实测落库（证据写在开发笔记里，不随包发布）
SEED_RUNS = [{
    "wechat": "4.1.15.8",
    "adapter": "1.2.2.2",
    "when": "2026-09-13",
    "caps": {
        "send_text": {"status": "ok", "evidence": "投递 WM_CHAR + 投递点发送按钮，DB 回读 3/3（send_postclick.py）"},
        "emoji_panel_open": {"status": "ok", "evidence": "投递点笑脸 → 新弹层窗 Qt51514QWindowToolSaveBits 771×771"},
        "emoji_send": {"status": "ok", "evidence": "投递点 ♡ → 收藏格 → DB 回读 type=动画表情（local_id 550→551）"},
        "moments_open": {"status": "ok", "evidence": "投递点侧栏 ◎（发现）→ 朋友圈行，主窗内嵌，像素差 0.437"},
        "moments_scroll": {"status": "ok", "evidence": "投递 WM_MOUSEWHEEL -120：下滚差 0.389、反向回滚差 0.000"},
        "moments_composer": {"status": "ok", "evidence": "长按相机 2.5s → 弹「朋友圈」编辑窗，投递 WM_CHAR 草稿入框"},
        "moments_publish": {"status": "user_gated", "evidence": "编辑窗与「发表」(210,568) 已定位，按用户口径未点"},
        "moments_like": {"status": "user_gated", "evidence": "库里有真鼠标实现，投递版未测（对外可见动作）"},
        "moments_comment": {"status": "user_gated", "evidence": "同上"},
        "switch_chat": {"status": "unknown", "evidence": "用户目视确认切成功过，但第二枪把界面点乱 ⇒ 换判据重测"},
        "image_send": {"status": "unknown", "evidence": "未测"},
        "poke_menu": {"status": "unknown", "evidence": "未测"},
        "minimized_send": {"status": "unknown", "evidence": "未测（最高目标的最严条件）"},
        "uia_tree": {"status": "no", "evidence": "本版本 UIA 只有 2 个节点、mmui 命中 0（uia_probe 实测）"},
    },
}]


# ── 纯函数 ──────────────────────────────────────────────────────────────
def _key(run: dict) -> tuple:
    return (str(run.get("wechat") or ""), str(run.get("adapter") or ""))


def merge_runs(old: dict, new_run: dict) -> dict:
    """把一次 run 并进矩阵（同版本对**覆盖**，不追加重复），返回新矩阵。"""
    data = {"schema": 1, "runs": list((old or {}).get("runs") or [])}
    new_run = dict(new_run or {})
    new_run.setdefault("when", time.strftime("%Y-%m-%d"))
    k = _key(new_run)
    kept = [r for r in data["runs"] if _key(r) != k]
    kept.append(new_run)
    data["runs"] = kept
    return data


def find_run(data: dict, wechat: str, adapter: str) -> dict | None:
    for r in (data or {}).get("runs") or []:
        if _key(r) == (str(wechat), str(adapter)):
            return r
    return None


def capabilities(data: dict, wechat: str, adapter: str) -> dict:
    """按 (微信版本, 适配层版本) 给出**逐能力状态**；没实测过的版本对 ⇒ 全部 unknown。"""
    run = find_run(data, wechat, adapter)
    caps = (run or {}).get("caps") or {}
    out = {}
    for cid, meta in CAPS.items():
        got = caps.get(cid) or {}
        out[cid] = {
            "label": meta["label"],
            "level": meta["level"],
            "status": str(got.get("status") or "unknown"),
            "evidence": str(got.get("evidence") or ("未实测：微信 %s × 适配层 %s" % (wechat, adapter))),
        }
    return out


def gate(data: dict, wechat: str, adapter: str) -> dict:
    """版本门：当前这对版本**实测过没有**？没实测过就该降级/提示，不许假定一样。"""
    run = find_run(data, wechat, adapter)
    if run:
        return {"measured": True, "wechat": wechat, "adapter": adapter,
                "advice": "", "when": str(run.get("when") or "")}
    return {
        "measured": False, "wechat": wechat, "adapter": adapter,
        "advice": ("微信 %s × 适配层 %s **没有实测记录**：发送这类动窗口/动键盘的能力按未验证处理 —— "
                   "控制台出横幅、默认降到真鼠标档或暂停自动发送，等跑一次实测再放开" % (wechat, adapter)),
    }


def summarize(caps: dict) -> str:
    """一行摘要：ok / no / unknown / user_gated 各几个。"""
    n = {"ok": 0, "no": 0, "unknown": 0, "user_gated": 0}
    for v in (caps or {}).values():
        s = str(v.get("status") or "unknown")
        n[s] = n.get(s, 0) + 1
    return "可用 %d · 不可用 %d · 未实测 %d · 待用户点头 %d" % (
        n["ok"], n["no"], n["unknown"], n["user_gated"])


# ── 薄 I/O ─────────────────────────────────────────────────────────────
def load(path: str | None = None) -> dict:
    p = path or MATRIX_PATH
    if not os.path.exists(p):
        return {"schema": 1, "runs": list(SEED_RUNS)}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not data.get("runs"):
            data["runs"] = list(SEED_RUNS)
        return data
    except Exception as e:
        log.warning("能力矩阵读失败（用内置种子）：%s", e)
        return {"schema": 1, "runs": list(SEED_RUNS)}


def save(data: dict, path: str | None = None) -> str:
    """原子写（temp + os.replace + fsync），避免半个 JSON 把矩阵读坏。"""
    p = path or MATRIX_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return p


def record(wechat: str, adapter: str, caps: dict, path: str | None = None) -> dict:
    """记一次实测（caps: {能力id: {"status":…, "evidence":…}}），返回新矩阵。"""
    data = merge_runs(load(path), {"wechat": wechat, "adapter": adapter, "caps": caps})
    save(data, path)
    return data


def adapter_version() -> str:
    """适配层（驱动库）版本：**只认运行时实测装的那一个**。

    坑（2026-09-13 实测）：`wechat_version_info()` 在没有驱动库的解释器里会回落到 `MIN_VER`
    常量（读到 1.1.5.1），照它查矩阵会得到"14 项全部未实测"的假结论 ⇒ 必须问 `dep_heal`
    （它读的是运行时的 `*.dist-info`）。
    """
    try:
        from . import dep_heal
        v = str(dep_heal.installed_version("wechatauto-replica") or "")
        if v:
            return v
    except Exception:
        pass
    return "unknown"


def current() -> dict:
    """当前环境（微信版本 × 适配层版本）的能力视图，给控制台/日志用。"""
    ver = "unknown"
    try:
        from .wechat import wechat_version_info
        info = wechat_version_info() or {}
        ver = str(info.get("version") or "unknown")
    except Exception:
        pass
    adp = adapter_version()
    data = load()
    caps = capabilities(data, ver, adp)
    g = gate(data, ver, adp)
    return {"wechat": ver, "adapter": adp, "caps": caps, "gate": g, "summary": summarize(caps)}
