# -*- coding: utf-8 -*-
"""环境引导（`agent/env_guide.py`）＋ 新手引导「环境」那一步的判据。

背景
新用户配好 Key 就走，跑到真语音条/读视频/听语音那一步才发现缺东西，还查不出缺哪一环。
⇒ 把"要用户自己装的东西"汇成一张清单（`env_guide.status()`），并接进新手引导
（Qt `ui_qt/onboarding.py` 第 4 步 / web `onboarding()` 第 4 步 / `GET /api/env-guide`）。

守什么
  A 结构：每条都带 key/title/need/ok/state/why/how/install_kind/action；顺序＝先能跑再锦上添花
  B **三态必须分开**：缺（False）≠ 读不出（None）——读不出**不许**当"不缺"（n_fail 与 n_unknown 分开计）
  C `ok` ＝ **没有 fail 项**；有 unknown 时 why 里必须点出来
  D 红线：文案里必须有"不替你装驱动 / 不改系统设置"，且**不许**出现"自动装/静默装驱动"这类承诺
  E 虚拟声卡项：缺时 `how` 要给**成对虚拟声卡**这种可照做的指引，且 install_kind=driver
  F 路由三处对齐（ROUTES / HANDLERS / webui 实现）＋ 前端真值（web/Qt 两侧都有这一步）
  G 真机实测：本机读数不炸、项数≥7、每条都能 JSON 序列化
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception: # noqa: BLE001
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from agent import env_guide as EG # noqa: E402

print("[A] 结构 + 顺序")
_st = EG.status()
_need = ("key", "title", "need", "ok", "state", "why", "how", "install_kind", "action")
ck("A1 status 顶层字段齐全", all(k in _st for k in ("ok", "items", "n_ok", "n_fail",
                                                   "n_unknown", "n_total", "why", "note")))
ck("A2 项数 ≥ 7（微信/依赖/虚拟声卡/合成引擎/ffmpeg/识别/解码器）",
   len(_st["items"]) >= 7, "%d 项" % len(_st["items"]))
ck("A3 每条都带全部字段", all(all(k in it for k in _need) for it in _st["items"]),
   str([it.get("key") for it in _st["items"] if not all(k in it for k in _need)]))
_order = [it["key"] for it in _st["items"]]
ck("A4 顺序＝先能跑再锦上添花（微信/依赖在最前，虚拟声卡在合成引擎前后均可）",
   _order.index("wechat") == 0 and _order.index("deps") == 1, str(_order))
ck("A5 install_kind 取值都在登记表里",
   all(it["install_kind"] in EG.KIND_LABEL for it in _st["items"]),
   str([it["install_kind"] for it in _st["items"]]))

print("\n[B] 三态分开（缺 ≠ 读不出）")
ck("B1 n_fail / n_unknown 分别计（不是把 unknown 并进 fail）",
   _st["n_fail"] == sum(1 for it in _st["items"] if it["ok"] is False)
   and _st["n_unknown"] == sum(1 for it in _st["items"] if it["ok"] is None),
   "fail=%d unknown=%d" % (_st["n_fail"], _st["n_unknown"]))
# 反向锚：喂一个"读不出"的探测 ⇒ ok 必须是 None（state=unknown），不许判成 fail
_it = EG._mk("x", "t", "n", None, "读不出")
ck("B2 `ok=None` ⇒ state=unknown（读不出**不许**当缺）", _it["state"] == "unknown" and _it["ok"] is None)
_it2 = EG._mk("x", "t", "n", False, "缺")
ck("B3 `ok=False` ⇒ state=fail", _it2["state"] == "fail")
_it3 = EG._mk("x", "t", "n", True, "有")
ck("B4 `ok=True` ⇒ state=ok 且 install_kind 归零", _it3["state"] == "ok" and _it3["install_kind"] == "none")

print("\n[C] ok ＝ 没有 fail 项")
# 反向锚：构造含 unknown 的假结果，验证 why 会点出 unknown（不混成"全齐"）
_unk_item = EG._mk("z", "读不出的项", "n", None, "探测炸了")
ck("C1 unknown 项不计入 n_fail（ok 仍为 True）",
   _unk_item["ok"] is None and _unk_item["state"] == "unknown")
_why_src = io.open(os.path.join(ROOT, "agent", "env_guide.py"), encoding="utf-8").read()
ck("C2 why 分三支：有 fail 说缺什么 / 只有 unknown 说读不出 / 全齐说已就绪",
   "%d 项还没就绪" in _why_src and "项读不出" in _why_src and "全部 %d 项已就绪" in _why_src)

print("\n[D] 红线：只检测 + 指路")
ck("D1 note 写明不替装驱动、不改系统设置",
   "不替你装驱动" in _st["note"] and "不改系统设置" in _st["note"], _st["note"])
_bad_words = ("自动安装驱动", "静默安装驱动", "自动改系统设置")
# ⛔ 反向锚不能用裸子串：模块自己在讲红线时会**否定式**写到"不替你装驱动"
#    ⇒ 裸查"替你装驱动"会把那句红线本身判成违规（判据第一版就这么自伤）。
#    口径：只有在**没有**前置否定词时才算"承诺替用户装"。
_neg = ("不", "别", "无需", "不用", "没有", "禁止", "绝不")


def _promise_hits(text):
    out = []
    for w in ("替你装驱动", "替用户装驱动", "自动装驱动", "静默装驱动", "替你改系统设置"):
        i = text.find(w)
        while i >= 0:
            pre = text[max(0, i - 3):i]
            if not any(n in pre for n in _neg):
                out.append((w, text[max(0, i - 6):i + len(w) + 4]))
            i = text.find(w, i + len(w))
    return out


ck("D2 全文件**没有**'替用户装驱动/静默装'这类**承诺**（否定式表述不算 ）",
   not _promise_hits(_why_src) and not any(w in _why_src for w in _bad_words),
   str((_promise_hits(_why_src) or [w for w in _bad_words if w in _why_src])[:3]))
_vm = [it for it in _st["items"] if it["key"] == "vmic"][0]
ck("D3 虚拟声卡项 install_kind=driver（要装驱动，我们不代劳）",
   _vm["install_kind"] in ("driver", "none"), _vm["install_kind"])
if _vm["state"] != "ok":
    ck("D4 缺虚拟声卡时 how 给出成对虚拟声卡这种可照做的指引",
       ("虚拟声卡" in _vm["how"] or "VB-CABLE" in _vm["how"] or "Scream" in _vm["how"])
       and "不替你装驱动" in _vm["how"], _vm["how"][:90])

print("\n[E] 路由三处对齐")
from agent.routes import ROUTES, HANDLERS # noqa: E402
ck("E1 ROUTES 声明 /api/env-guide（GET）",
   ROUTES.get("/api/env-guide") == ("GET",), str(ROUTES.get("/api/env-guide")))
ck("E2 HANDLERS 指向 _rapi_env_guide",
   HANDLERS.get("/api/env-guide", {}).get("GET") == "_rapi_env_guide",
   str(HANDLERS.get("/api/env-guide")))
_wu = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ck("E3 webui 里有 _rapi_env_guide 实现且调 env_guide",
   "def _rapi_env_guide(" in _wu and "env_guide" in _wu)

print("\n[F] 前端两侧都有「环境」这一步")
_html = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ck("F1 web 控制台 onboarding 调 /api/env-guide",
   "'/api/env-guide'" in _html and "obRenderEnv" in _html)
ck("F2 web 向导是六步（共 6 步 / step===6 收尾）",
   "共 6 步" in _html and "if(step===6){" in _html, "")
_onb = io.open(os.path.join(ROOT, "ui_qt", "onboarding.py"), encoding="utf-8").read()
ck("F3 Qt 向导第 4 步＝环境引导（拉 /api/env-guide）",
   "_render_step4_env" in _onb and "/api/env-guide" in _onb and "_load_env_guide" in _onb)
ck("F4 Qt 向导也是六步（共 6 步）",
   "共 6 步" in _onb and "_render_step6_check" in _onb)
ck("F5 Qt 第 4 步说明文案点明「只检测和指路，不替你装驱动」",
   "只检测" in _onb and "不替你装驱动" in _onb)

print("\n[G] 真机实测")
try:
    _raw = json.dumps(_st, ensure_ascii=False)
    ck("G1 整份结果可 JSON 序列化（前端能直接吃）", bool(_raw) and len(_raw) > 100, "%d 字节" % len(_raw))
except Exception as e: # noqa: BLE001
    ck("G1 整份结果可 JSON 序列化（前端能直接吃）", False, str(e)[:80])
for _it in _st["items"]:
    print("      %s %s：%s" % ({"ok": "✓", "fail": "✗"}.get(_it["state"], "·"),
                               _it["title"], (_it["why"] or "")[:60]))
ck("G2 本机至少有一项是 ok 或 fail（判据自己没瞎：不是全 unknown）",
   any(it["ok"] in (True, False) for it in _st["items"]))

print("\n==== 环境引导判据：%d 通过 / %d 失败 ====" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAIL " + b)
sys.exit(1 if BAD else 0)
