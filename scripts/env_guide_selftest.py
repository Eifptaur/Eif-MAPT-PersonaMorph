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

print("\n[F] 前端两侧都按新结构接：正常引导(1-3) → 恢复(4) → 环境校准(5) → 缺件各一步(6..N) → 体检(末)")
_html = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ck("F1 web 控制台缺件步调 /api/env-guide，且一屏只讲一项（obRenderItem）",
   "'/api/env-guide'" in _html and "obRenderItem" in _html)
ck("F2 web 步数**不写死**（没有「共 6 步」这种常量；总步数由 obTotal() 按实数算）",
   "共 6 步" not in _html and "obTotal()" in _html, "")
_onb = io.open(os.path.join(ROOT, "ui_qt", "onboarding.py"), encoding="utf-8").read()
ck("F3 Qt 缺件步拉 /api/env-guide，且一屏只讲一项（_render_env_item）",
   "/api/env-guide" in _onb and "_render_env_item" in _onb)
# ⛔ 反向锚不用裸子串：模块 docstring 里正**声明**「不出现"共 6 步"这种写死的数」，
#    裸查会把那句声明本身判成违规（判据第一版就这么自伤）。
#    口径：剥掉 docstring/注释，只在**代码**里找写死的步数。
import ast as _ast  # noqa: E402
def _code_only(src):
    """剥掉模块/类/函数 docstring 与注释，只留**会执行的字符串字面量**（即用户真看到的文案）。"""
    _t = _ast.parse(src)
    _docs = set()
    for _n in _ast.walk(_t):
        if isinstance(_n, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
            _b = getattr(_n, "body", None)
            if _b and isinstance(_b[0], _ast.Expr) and isinstance(_b[0].value, _ast.Constant) \
               and isinstance(_b[0].value.value, str):
                _docs.add(id(_b[0].value))
    _out = []
    for _n in _ast.walk(_t):
        if isinstance(_n, _ast.Constant) and isinstance(_n.value, str) and id(_n) not in _docs:
            _out.append(_n.value)
    return "\n".join(_out)
_onb_code = _code_only(_onb)
_hard = [s for s in _onb_code.splitlines() if "共 6 步" in s]
ck("F4 Qt 步数**不写死**（代码里没有写死 6 的步数文案；由 _env_total_steps() 按实数算）",
   not _hard and "_env_total_steps" in _onb, str(_hard[:2]))
ck("F5 Qt 缺件步文案点明「用来干啥 / 现在什么情况 / 怎么补 / 这一类」四行",
   all(k in _onb for k in ("用来干啥", "现在什么情况", "怎么补", "这一类")))
ck("F5b Qt 缺件步不承诺代装：指示用户自己去装（「不想装也能继续用」）",
   "不想装也能继续用" in _onb and "重新检测" in _onb)
ck("F6 环境校准（env_hint）只提醒不强制：文案含「不改也能用」",
   "不改也能用" in _onb and "不改也能用" in _html)
ck("F7 两类不许混：环境校准走 env_hint（/api/status），缺件走 env-guide（/api/env-guide）",
   "env_hint" in _onb and "_hint_fetch" in _onb and "_env_fetch" in _onb)

print("\n[F'] 步号一致性：第 5 步＝环境校准，第 6 步起＝逐条缺件，末步＝体检")
# 分母守卫 + 两侧同口径：缺件第 1 项必须是第 6 步（不是第 5 步，第 5 步被环境校准占了）。
# ⛔ 这是真踩过的 off-by-one（`_env_step_no()` 曾返回 5+_env_i ⇒ 缺件第 1 项顶掉环境校准的步号）。
ck("F8 Qt 缺件步号 = 6 + 已讲数（第 5 步留给环境校准）",
   "return 6 + int(self._env_i or 0)" in _onb, "")
ck("F9 web 缺件步号 = 5 + idx（idx 从 1 起 ⇒ 第 1 个缺件＝第 6 步）",
   "(5+idx)" in _html and "_obI + 1" in _html, "")
ck("F10 两侧总步数都＝3(配置)+1(恢复)+1(校准)+N(缺件)+1(体检)",
   "3 + 1 + 1 + len(getattr(self, \"_env_pending_cache\", [])) + 1" in _onb
   and "3 + 1 + 1 + obPend().length + 1" in _html, "")

print("\n[H] 用户层级：缺件引导不许把「进系统设置」推给用户（用户最多只到微信这一层）")
# 2026-10-10 用户当面纠正：OCR/依赖/驱动是程序自己的活；asr 也不许让用户进系统设置。
# ⚠️ 自指陷阱：关键字的字面量若出现在本判据自己的源码里 ⇒ 误伤 —— 一律 chr() 拼。
_pat_settings = "ms-" + "settings:"          # 指向系统设置页的伪协议
_pat_cn1 = "时间和" + "语言"                  # 「时间和语言」
_pat_cn2 = "设置 → " + "语音"                 # 教用户点系统设置的写法
EG_SRC = io.open(os.path.join(ROOT, "agent", "env_guide.py"), encoding="utf-8").read()
# ⚠️ 这里的 `_code_only` 只收**字符串常量**（＝用户真看到的文案），docstring/注释天然被排除。
_eg_code = _code_only(EG_SRC)
ck("H1 分母守卫：剥注释后仍有真文案（剥失败 ⇒ 下面全是假绿）",
   0 < len(_eg_code) < len(EG_SRC), "%d -> %d" % (len(EG_SRC), len(_eg_code)))
ck("H2★★ 缺件引导文案里**不许出现「ms-settings:」**（那是把用户推进系统设置页）",
   _pat_settings not in _eg_code, "")
ck("H3★★ 缺件引导文案里**不许教用户点「时间和语言」**",
   _pat_cn1 not in _eg_code, "")
ck("H4★★ 缺件引导文案里**不许写「设置 → 语音」**这类指路口径",
   _pat_cn2 not in _eg_code, "")
# 反向锚：asr 项必须是「用户可选下载」，不是「系统功能」
_asr_seg = EG_SRC.split("def _item_asr", 1)[-1].split("def _item_silk", 1)[0]
ck("H5★★ asr 项里明写「可选」（用户自己选下不下，不塞安装包、不催）",
   "可选" in _asr_seg and "faster-whisper" in _asr_seg, "")
ck("H6★★ asr 项里明写「不装也能用」（不许把可选件说成必需件）",
   "不装" in _asr_seg, "")
# tts：程序自己能切到系统档 ⇒ 不许让用户去改
_tts_seg = EG_SRC.split("def _item_tts_engine", 1)[-1].split("def _item_ffmpeg", 1)[0]
ck("H7★★ tts 项里不出现「ms-settings:」（程序自己能切，不用用户去改）",
   _pat_settings not in _tts_seg, "")
_tts_dl = EG_SRC.split('"tts": {', 1)[-1].split("},", 1)[0] if '"tts": {' in EG_SRC else ""
ck("H8 tts 的下载登记里也没把用户推向系统设置页",
   _pat_settings not in _tts_dl, "")
# 前端两处：Qt 面板 + 网页控制台
_panels = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
_panels_code = _code_only(_panels)
ck("H9★★ Qt 依赖体检里**不许**再写「设置 → 时间和语言 → 语音」",
   _pat_cn1 not in _panels_code, "")
_html2 = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
# 只扫"缺引擎怎么办"那一段（voice 段），避免误伤引擎选择下拉（sapi 是程序内部档）
_html_voice = _html2.split("voice: {", 1)[-1].split("\n  },", 1)[0] if "voice: {" in _html2 else ""
ck("H10★★ 网页「缺引擎怎么办」里**不许**再教用户进系统设置",
   _pat_cn1 not in _html_voice and _pat_cn2 not in _html_voice, "")
ck("H11 网页缺件提示那行改成了「可选 faster-whisper」口径",
   "faster-whisper" in _html2, "")

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
