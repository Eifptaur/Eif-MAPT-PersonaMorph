# -*- coding: utf-8 -*-
"""全后台审计判据（⑥）——**不需要微信在跑**。

用户 2026-09-14 点的题：发送兜底 / 朋友圈 / 拍一拍 / 引用 / 标定 这五条路径，
要么**全程投递**，要么**如实标"跳过"**，并且要有一把"光标不变"的尺子。

四组：
  A 矩阵自洽：agent/bg_status.py 是单一事实源，五条被点名的路径都在表里、字段齐全；
  B 不静默降级：分派器必须"投递优先 + 退回时写明档位"，真鼠标路径必须有"只走后台"闸；
  C 光标不变（运行时 tripwire）：把真鼠标原语与 `_click_screen` 换成**会响的探针**，
    再驱动投递档路径（假窗口 + 假后端），全程不许响一声，同时必须真的投递出消息；
  D 如实可见：控制台显示这张表 + 档位 + 「只走后台」开关，/api/status 暴露 bg 段。

用法：py -3 scripts\\background_selftest.py   （非零退出＝有失败）
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from agent import bg_status as BG          # noqa: E402
from agent import input_backend as ib      # noqa: E402
from agent import wechat as WC             # noqa: E402

SRC_WECHAT = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
SRC_CONSOLE = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
SRC_WEBUI = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
SRC_CFG = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()

# ── A 矩阵自洽 ───────────────────────────────────────────────────────────
print("[A] 后台能力矩阵（单一事实源）")
PATHS = BG.paths()
ck("A1 矩阵非空且 key 唯一", PATHS and len({p["key"] for p in PATHS}) == len(PATHS), "%d 条" % len(PATHS))
_need = ("key", "label", "status", "detail", "evidence")
_missing = [p.get("key") for p in PATHS if any(not p.get(f) for f in _need)]
ck("A2 每条都有 key/label/status/detail/evidence", not _missing, str(_missing))
_bad_st = [p["key"] for p in PATHS if p["status"] not in BG.STATUS_LABEL]
ck("A3 status 取值合法且都有中文标签", not _bad_st, str(_bad_st))
# 用户点名的五条路径：发送兜底 / 朋友圈（打开+刷）/ 拍一拍 / 引用 / 标定
_named = ["send_text", "moments_open", "moments_scroll", "poke", "quote", "calibrate"]
_gone = [k for k in _named if k not in {p["key"] for p in PATHS}]
ck("A4 用户点名的 5 条路径都在表里（含朋友圈打开/刷两段）", not _gone, str(_gone))
# 表里写"后台"的，代码里必须真有投递实现（防止表上漂亮、代码里没写）
_claim = {"send_text": "send_text_posted", "moments_open": "moments_open_posted",
          "moments_scroll": "moments_scroll_posted", "switch_chat": "switch_chat_posted",
          "send_media": "send_file_posted", "sticker": "send_text_posted"}
_sham = [k for k, fn in _claim.items()
         if BG.get(k).get("status", "").startswith("posted") and fn not in SRC_WECHAT]
ck("A5 表里写「后台」的路径，代码里真有投递实现", not _sham, str(_sham))

# ── B 不静默降级 ─────────────────────────────────────────────────────────
print("[B] 不静默降级（投递优先 + 退回必写明档位 + 只走后台闸）")
for fn in ("moments_open", "moments_scroll"):
    body = SRC_WECHAT.split("def %s(" % fn)[1][:3000]
    ck("B1.%s 是分派器（投递 + 真鼠标两条都在）" % fn,
       ("_posted" in body) and ("_real" in body))
    ck("B2.%s 退回真鼠标时写明档位（消息里有「真鼠标档」）" % fn, "真鼠标档" in body)
    ck("B3.%s 退回前写了日志（不留暗账）" % fn, "log.warning" in body)
    ck("B4.%s 「只走后台」时不再退回（跳过并说明）" % fn,
       "_background_only" in body and "跳过" in body)
for fn, label in (("_send_poke_inner", "拍一拍"), ("_reply_quote_inner", "引用"),
                  ("moments_like", "点赞"), ("moments_comment", "评论"),
                  ("moments_publish_text", "发表")):
    body = SRC_WECHAT.split("def %s(" % fn)[1][:1500]
    ck("B5.%s（%s）有「只走后台」闸" % (fn, label), "_background_only" in body)
_gate = SRC_WECHAT.split("def _background_only(")[1][:800]
ck("B6 闸读的是 config.wechat.background_only", "background_only" in _gate and "wechat" in _gate)
ck("B7 config 默认值里有这个键", '"background_only"' in SRC_CFG)
_reason = BG.background_only_reason("拍一拍")
ck("B8 拒绝话术统一（含路径名 + 明说跳过 + 不动鼠标）",
   "拍一拍" in _reason and "跳过" in _reason and "不动你的鼠标" in _reason)
# 真鼠标路径的 docstring 必须自报家门（读代码的人一眼知道要动鼠标）
_real_tags = [p["key"] for p in PATHS if p["status"] == "real"]
ck("B9 有一条以上真鼠标路径被如实标注", len(_real_tags) >= 3, "、".join(_real_tags))
ck("B10 投递档判定不靠可见性（隐藏/最小化也算投递档）",
   "IsWindowVisible" not in SRC_WECHAT.split("def _bg_backend(")[1][:600])
# B11 最小化守卫：判据抓不到画面时，如实拒绝而不是硬点（真机取证见 _scratch/后台能力-真机记录.md）
_blind = SRC_WECHAT.split("def _moments_judge_blind(")[1][:900]
ck("B11 判据瞎了（最小化）时如实拒绝：守卫读 IsIconic", "IsIconic" in _blind)
ck("B12 拒绝话术说清「判不了就不动手」与「窗口留在屏幕上」",
   "判不了就不动手" in SRC_WECHAT and "留在屏幕上" in SRC_WECHAT)
ck("B13 两条靠画面判成功的投递路径都挂了这道守卫",
   SRC_WECHAT.split("def moments_open_posted(")[1][:2500].count("_moments_judge_blind") >= 1
   and SRC_WECHAT.split("def moments_scroll_posted(")[1][:2500].count("_moments_judge_blind") >= 1)
# B14~B17 最小化 ⇒ **不激活地**还原再干活（2026-09-15 用户：「那个最小化，你应该可以自己在后台切出来吧」）
_HELP = SRC_WECHAT.split("def _ensure_main_visible(")[1][:2200]
# ⚠️ 只认**代码形态**的字面量（带 `u.` 前缀与参数），不搜裸 API 名——docstring 里为了说明历史坑
#    **引用**了 `SetForegroundWindow`/`SW_RESTORE` 这些名字，搜整段会自命中（第三次踩同一个坑）。
ck("B14 有「无激活还原最小化窗口」的实现（真的读 IsIconic 判断）",
   "u.IsIconic(int(main))" in _HELP)
ck("B15 用的是 SW_SHOWNOACTIVATE（4）而不是 SW_RESTORE，且代码里不抢前台",
   "u.ShowWindow(int(main), 4)" in _HELP
   and "u.SetWindowPos(int(main), 0, 0, 0, 0, 0," in _HELP
   and "SetForegroundWindow(int(main))" not in _HELP
   and "ShowWindow(int(main), 9)" not in _HELP)
ck("B16 还原开关映射到 config + 界面（默认开）",
   '"restore_minimized"' in SRC_CFG and 'data-cfg="wechat.restore_minimized"' in SRC_CONSOLE)
ck("B17 三条会抓图的投递链都在入口调了它（切会话 / 搜索框切会话 / 投递发文字）",
   SRC_WECHAT.split("def switch_chat_posted(")[1][:4000].count("_ensure_main_visible") >= 1
   and SRC_WECHAT.split("def open_chat_by_search(")[1][:4000].count("_ensure_main_visible") >= 1
   and SRC_WECHAT.split("def send_text_posted(")[1][:4000].count("_ensure_main_visible") >= 1)
ck("B18 竞态如实写进控制台（用户 2026-09-15 要求「这个你要如实跟用户讲清楚」）",
   "会不会跟你抢操作" in SRC_CONSOLE and "撞了它会用聊天区内容复核" in SRC_CONSOLE)

# ── C 光标不变（运行时 tripwire）──────────────────────────────────────────
print("[C] 光标不变：真鼠标原语换成会响的探针，投递路径不许碰它")
TRIPPED = []


class _Tripwire:
    """任何属性调用都记一笔（真鼠标原语一旦被调用就响）。"""

    def __getattr__(self, name):
        def _boom(*a, **k):
            TRIPPED.append(name)
            raise AssertionError("投递路径碰了真鼠标原语：%s" % name)
        return _boom


from agent import ui_adapt as UA           # noqa: E402

_orig_user32 = UA._user32
_orig_click_screen = WC.WeChatAdapter._click_screen
_orig_prepare = UA.prepare_screen
_orig_find = ib.find_main_window
_orig_post = ib._post
_orig_select = ib.select_backend

POSTED = []
UA._user32 = _Tripwire()


def _no_real_click(self, x, y):
    TRIPPED.append("_click_screen")
    raise AssertionError("投递路径调了真鼠标 _click_screen")


WC.WeChatAdapter._click_screen = _no_real_click
UA.prepare_screen = lambda gui: True
ib.find_main_window = lambda: 7777
ib._post = lambda h, m, w, l: POSTED.append((int(h), int(m), int(w), int(l))) or 1
ib.select_backend = lambda cfg=None: ib.MessageBackend(press_ms=1, activate=True)


class _FakeGui:
    main_hwnd = 7777
    render_hwnd = 7778
    origin_x = 0
    origin_y = 0
    render_w = 1140
    render_h = 890
    render_rect = (0, 0, 1140, 890)


class _FakeAdapter:
    def _get_gui(self):
        return _FakeGui()


try:
    ad = object.__new__(WC.WeChatAdapter)
    ad._get_gui = lambda: _FakeGui()
    # 朋友圈矩形 + 缩略灰度 + OCR 全部换成假数据（判据需要"界面确实变了"）
    ad._moments_rect = lambda hwnd: (100, 100, 1300, 1000)
    _thumb_seq = []

    def _fake_thumb(rect, scale=(64, 48), gui=None):
        """奇偶交替返回两张不同的图 ⇒ "每次点击前后界面都变了"，用来验判据链路本身。"""
        _thumb_seq.append(1)
        return [10] * 64 if len(_thumb_seq) % 2 else [200] * 64
    ad._moments_gray_thumb = _fake_thumb
    ad._find_green_discover = lambda gui: (144, 682)          # 自证到的「发现」图标
    ad._moments_shot_ocr = lambda rect: [("朋友圈", 190, 159, 60, 20)]

    POSTED[:] = []
    ok1, m1 = ad.moments_open_posted()
    msgs1 = [m for _h, m, _w, _l in POSTED]
    ck("C1 投递档打开朋友圈：成功", ok1 is True, m1[:60])
    ck("C2 两枪都投递到主窗（发现 + 朋友圈）",
       len([m for m in msgs1 if m == ib.WM_LBUTTONDOWN]) == 2, "按下次数=%d" % len([m for m in msgs1 if m == ib.WM_LBUTTONDOWN]))
    ck("C3 全程没碰真鼠标原语", not TRIPPED, str(TRIPPED[:4]))
    ck("C4 投递消息里全是 WM_*（没有真实输入 API）",
       all(m in (ib.WM_ACTIVATE, ib.WM_NCACTIVATE, ib.WM_MOUSEMOVE, ib.WM_LBUTTONDOWN,
                 ib.WM_LBUTTONUP, ib.WM_MOUSEWHEEL, ib.WM_CHAR, ib.WM_KEYDOWN, ib.WM_KEYUP)
           for _h, m, _w, _l in POSTED), str(sorted({m for _h, m, _w, _l in POSTED})))

    POSTED[:] = []
    _thumb_seq[:] = []
    ok2, m2 = ad.moments_scroll_posted(direction=1, times=1)
    wheels = [1 for _h, m, _w, _l in POSTED if m == ib.WM_MOUSEWHEEL]
    ck("C5 投递档刷朋友圈：成功且说明未动光标", ok2 is True and "未动光标" in m2, m2[:70])
    ck("C6 滚轮是投递出去的（WM_MOUSEWHEEL ≥ 8 格）", len(wheels) >= 8, "%d 格" % len(wheels))
    ck("C7 刷朋友圈也没碰真鼠标原语", not TRIPPED, str(TRIPPED[:4]))

    # 假判据：界面没变 ⇒ 必须如实报"没生效"，不许假报成功
    ad._moments_gray_thumb = lambda rect, scale=(64, 48), gui=None: [10] * 64
    ok3, m3 = ad.moments_scroll_posted(direction=1, times=1)
    ck("C8 界面没变时如实报「没生效」（不假报）", ok3 is False and "没" in m3, m3[:60])
    ad._find_green_discover = lambda gui: None
    ok4, m4 = ad.moments_open_posted()
    ck("C9 没自证到图标时停手并说明（不盲点）", ok4 is False and "自证" in m4, m4[:60])

    # 非投递档 ⇒ 投递函数必须直接拒绝（不许在真鼠标档下假装投递）
    ib.select_backend = lambda cfg=None: ib.RealInputBackend()
    ok5, m5 = ad.moments_open_posted()
    ck("C10 真鼠标档下调投递函数 ⇒ 明确拒绝", ok5 is False and "真鼠标档" in m5, m5[:60])
    ok6, m6 = ad.moments_scroll_posted()
    ck("C11 同上（刷朋友圈）", ok6 is False and "真鼠标档" in m6, m6[:60])
finally:
    UA._user32 = _orig_user32
    WC.WeChatAdapter._click_screen = _orig_click_screen
    UA.prepare_screen = _orig_prepare
    ib.find_main_window = _orig_find
    ib._post = _orig_post
    ib.select_backend = _orig_select

# ── D 如实可见 ───────────────────────────────────────────────────────────
print("[D] 如实可见：控制台显示这张表 + 档位 + 「只走后台」开关")
ck("D1 /api/status 暴露 bg 段", 'st["bg"] = _bg.status()' in SRC_WEBUI)
ck("D2 控制台有这张表的位置（bgList）", 'id="bgList"' in SRC_CONSOLE and 'id="bgHead"' in SRC_CONSOLE)
ck("D3 控制台有「只走后台」开关（同一个 config 键）",
   'data-cfg="wechat.background_only"' in SRC_CONSOLE)
ck("D4 表上「全程后台 / 真鼠标」两种标注都出现在控制台脚本里",
   "全程后台" in SRC_CONSOLE and "真鼠标" in SRC_CONSOLE)
ck("D5 档位随 /api/status 显示（touches_cursor ⇒ 会动光标）",
   "touches_cursor" in SRC_CONSOLE and "会动光标" in SRC_CONSOLE)
ck("D6 矩阵只在 bg_status 一份（控制台不另写单子）",
   SRC_CONSOLE.count('"poke"') == 0 and SRC_CONSOLE.count('"calibrate"') == 0)

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
