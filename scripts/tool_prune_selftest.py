# -*- coding: utf-8 -*-
"""工具表「按能力裁剪」的判据（不需要微信、不需要起服务）。

背景（用户 2026-09-15）：「**省 token 不仅是你的事，也是群相的事。所有要用模型的地方都要省
token，尽量给用户省钱**（仍以不影响效果为前提）」。
实测：37 个工具 = 11626 字符 ≈ **7324 token**，比整份系统提示（3941 token）还大 ⇒ 每次请求最大的单块。
本轮落地：**只裁"当前配置/场景下调用必然失败"的工具**（裁掉不损失任何可达效果）。

本判据守七条：
  A 默认配置下必须真裁掉（并打印省下的 token）
  B **反向断言**：把开关打开 ⇒ 该工具必须回来（防"裁了回不来"）
  C 核心工具在任何配置下都不许被裁
  D 规则里点到的工具名必须真实存在（防拼错 ⇒ 静默失效 = 判据自己骗自己）
  E 规则里点到的**配置键必须真实存在**于 DEFAULT_CONFIG（防写错键名 ⇒ 规则永不触发）
  F 场景裁：私聊裁群成员工具、群聊保留
  G 接线：生产路径必须调 visible_defs，且旧的写死三行过滤已不在
"""
from __future__ import annotations

import inspect
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from agent import tools as T                       # noqa: E402
from agent.config import DEFAULT_CONFIG as D       # noqa: E402

BASE = T.build_tool_defs()
NAMES = [d["name"] for d in BASE]
CH = len(json.dumps(T.to_openai_tools(BASE), ensure_ascii=False))
CPC = 0.630


def size_of(defs):
    return len(json.dumps(T.to_openai_tools(defs), ensure_ascii=False))


print("[A] 默认配置：该裁的都裁掉了")
kept, dropped = T.visible_defs(BASE, D, kind="group")
dn = [x["name"] for x in dropped]
print("      工具 %d 个 → 给模型 %d 个 · 省 %d 字符 ≈ %d token/请求（%.0f%%）"
      % (len(BASE), len(kept), CH - size_of(kept), int((CH - size_of(kept)) * CPC),
         100.0 * (CH - size_of(kept)) / CH))
for x in dropped:
    print("        - %-20s %s" % (x["name"], x["why"]))
EXPECT_DROP = {"send_random_image", "send_image_search", "gen_image", "find_local_file",
               "send_local_file", "send_voice_reply", "moments_like", "moments_comment",
               "moments_publish", "moments_surf"}
ck("A1 默认配置下 10 个「必然失败」的工具被裁", set(dn) == EXPECT_DROP,
   "裁=%s" % sorted(set(dn) ^ EXPECT_DROP))
ck("A2 真的省了体积（≥1000 字符）", CH - size_of(kept) >= 1000,
   "省 %d 字符" % (CH - size_of(kept)))

print("\n[B] 反向断言：开关打开 ⇒ 工具必须回来")
CASES = [
    ("image_reply.enabled", True, "send_random_image"),
    ("image_reply.enabled", True, "send_image_search"),
    ("image_gen.enabled", True, "gen_image"),
    ("voice_reply.enabled", True, "send_voice_reply"),
    ("behavior.like_moments.enabled", True, "moments_like"),
    ("behavior.moments_comment.enabled", True, "moments_comment"),
    ("behavior.moments_publish.enabled", True, "moments_publish"),
    ("behavior.moments_surf.enabled", True, "moments_surf"),
]
for path, val, tool in CASES:
    cfg = json.loads(json.dumps(D))       # 深拷贝，别污染 DEFAULT_CONFIG
    cur = cfg
    ks = path.split(".")
    for k in ks[:-1]:
        cur = cur.setdefault(k, {})
    cur[ks[-1]] = val
    k2, _d2 = T.visible_defs(BASE, cfg, kind="group")
    ck("B %s ← %s" % (tool, path), tool in [x["name"] for x in k2])


cfg2 = json.loads(json.dumps(D))
cfg2.setdefault("file_search", {})["enabled"] = True
cfg2["file_search"]["dirs"] = ["C:/tmp"]
k3, _d3 = T.visible_defs(BASE, cfg2, kind="group")
ck("B file_search 开了且配了目录 ⇒ 两个找文件工具都回来",
   {"find_local_file", "send_local_file"} <= {x["name"] for x in k3})
cfg3 = json.loads(json.dumps(D))
cfg3["file_search"] = {"enabled": True, "dirs": []}
k4, d4 = T.visible_defs(BASE, cfg3, kind="group")
ck("B file_search 开着但没配目录 ⇒ 仍然裁（搜不到任何文件）",
   {"find_local_file", "send_local_file"} <= {x["name"] for x in d4})

cfg4 = json.loads(json.dumps(D))
cfg4.setdefault("web_search", {})["enabled"] = False
cfg4.setdefault("api", {})["vision"] = False
k5, d5 = T.visible_defs(BASE, cfg4, kind="group")
_5 = {x["name"] for x in d5}
ck("B vision/web_search 关掉时照样裁（原有能力，别回退）",
   {"get_message_images", "web_search", "web_fetch"} <= _5, str(sorted(_5)))

print("\n[C] 核心工具在任何配置下都不许被裁")
CORE = ["send_message", "finish", "get_recent_messages", "get_message_detail", "send_image",
        "transcribe_voice", "download_media", "forward_media", "collect_emoji", "list_emojis",
        "send_emoji", "view_merge_forward", "collect_message", "recall_message", "send_poke",
        "memory_append", "memory_query", "memory_remove", "report_feedback",
        "set_timer", "list_timers", "cancel_timer", "read_video"]
EMPTY = {}
k_empty, d_empty = T.visible_defs(BASE, EMPTY, kind="group")
_gone = [t for t in CORE if t not in [x["name"] for x in k_empty]]
ck("C1 所有开关都关（空配置）时核心工具一个不少", not _gone, "丢了=%s" % _gone)
ck("C2 空配置下也没有把工具裁光（≥20 个留下）", len(k_empty) >= 20, "%d 个" % len(k_empty))

print("\n[D] 规则里点到的工具名必须真实存在")
src = inspect.getsource(T._prune_reason)
mentioned = set(re.findall(r'n == "([a-z_]+)"', src))
for grp in re.findall(r'n in \(([^)]*)\)', src):
    mentioned |= set(re.findall(r'"([a-z_]+)"', grp))
_unknown = sorted(m for m in mentioned if m not in NAMES)
ck("D1 规则里的工具名都在真实工具表里（拼错就静默失效）", not _unknown, "不存在=%s" % _unknown)
ck("D2 规则真的被读到了（抽出的名字 ≥8 个）", len(mentioned) >= 8, "%d 个" % len(mentioned))

print("\n[E] 规则里点到的配置键必须真实存在")
paths = re.findall(r'_cfg_at\(cfg, "([^"]+)"', src)
_bad = []
for p in paths:
    cur = D
    ok = True
    for k in p.split("."):
        if not isinstance(cur, dict) or k not in cur:
            ok = False
            break
        cur = cur[k]
    if not ok:
        _bad.append(p)
ck("E1 每个配置路径都能在 DEFAULT_CONFIG 里走到（写错键名 ⇒ 规则永不触发）",
   not _bad and len(paths) >= 8, "坏=%s 共%d条" % (_bad, len(paths)))

print("\n[F] 场景裁：私聊 / 群聊")
_, d_priv = T.visible_defs(BASE, D, kind="private")
_, d_grp = T.visible_defs(BASE, D, kind="group")
ck("F1 私聊裁掉群成员工具",
   "get_active_members" in [x["name"] for x in d_priv]
   and "get_active_members" not in [x["name"] for x in d_grp])
ck("F2 私聊不会误裁别的（群聊/私聊差异恰好 1 个）",
   len(d_priv) == len(d_grp) + 1, "私聊裁 %d / 群聊裁 %d" % (len(d_priv), len(d_grp)))

print("\n[G] 接线（生产路径真的调了它）")
pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ck("G1 主回复链调用 visible_defs", "visible_defs" in pm and "tool_defs, _dropped_tools" in pm)
ck("G2 旧的写死三行过滤已删除（不许两处实现）",
   'search_enabled = cfg.get("web_search"' not in pm and "vision_enabled = cfg.get" not in pm)
ck("G3 裁掉时留日志（用户/排障看得见）", "工具表按能力省去" in pm)
ck("G4 /api/status 暴露当前工具表（省 token 要看得见）", '"tools": _tools_status()' in pm)

print("\n==== 工具裁剪判据：%d 通过 / %d 失败 ====" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAIL " + b)
sys.exit(1 if BAD else 0)
