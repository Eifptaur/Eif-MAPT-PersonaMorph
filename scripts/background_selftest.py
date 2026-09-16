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
# B17′~B17c 用完要把"为干活还原出来的"主窗**放回收起状态**（2026-09-16 对面 r22 验收 FAIL 项：
#   不激活还原 ✓、还前台 ✓，但结束后 `IsIconic=False` ⇒ 用户的微信从"收在任务栏"变成"摊在桌面上"）
ck("B17a 还原时登记了「这是为干活还原的」",
   "_MINIMIZED_BY_US = int(main)" in SRC_WECHAT)
_HELP_MIN = SRC_WECHAT.split("def _minimize_back_if_needed(")[1][:1400]
ck("B17b 放回时三条安全线都在（没登记不动 / 已收起不动 / 用户正在用就不动）",
   "if not hwnd:" in _HELP_MIN
   and "u.IsIconic(hwnd)" in _HELP_MIN
   and "int(u.GetForegroundWindow() or 0) == hwnd" in _HELP_MIN
   and "u.ShowWindow(hwnd, 6)" in _HELP_MIN)
ck("B17c 在「还前台」之后立刻放回（顺序不能反：先最小化会让还前台更难成立）",
   "_minimize_back_if_needed(note)" in SRC_WECHAT)
# B18（2026-09-16 用户要求：「他老是想找会话列表那一条究竟在哪儿，**他不能直接点击输搜索框输入吗**」）：
#   切会话必须**搜索框优先** —— 搜索入口位置固定、不依赖滚动、也不怕列表被别的窗口盖住；
#   老的「找行 + 滚轮」只作兜底（保留，不删）。
_SEG_SW = SRC_WECHAT.split("def switch_chat_posted(")[1][:6000]
_HIT_SEARCH = _SEG_SW.find("open_chat_by_search(chat_id, name=name, gui=gui)")
_HIT_ROW = _SEG_SW.find("find_row")
ck("B18 切会话是搜索框优先（搜索调用必须出现在找行之前）",
   _HIT_SEARCH >= 0 and _HIT_ROW >= 0 and _HIT_SEARCH < _HIT_ROW)
# B19（2026-09-16 用户报「那为啥鼠标会滑我的控制台」）：
#   真鼠标档必须**默认不执行** —— `wechat.background_only` 默认必须是 True（安全的一侧），
#   要用那 5 条真鼠标路径（拍一拍 / 引用 / 朋友圈点赞评论 / 发朋友圈纯文字 / UI 标定）必须显式打开。
#   ⚠️ 与 `input.allow_real_fallback` 的区别：那个管"投递判据不过时退不退回真鼠标"，
#      本开关管"这条路径本来就走真鼠标时要不要执行"。两个都默认关掉才叫安全。
ck("B19 「只走后台」默认是 True（安全侧，改之前是 False）",
   '"background_only": True' in SRC_CFG)
ck("B19a 每条真鼠标路径都被 background_only 闸挡住（闸出现 ≥ 8 处）",
   SRC_WECHAT.count("self._background_only()") >= 8)
ck("B19b 真鼠标兜底也默认关（allow_real_fallback 默认 False）",
   '"allow_real_fallback": False' in SRC_CFG)
# B20（2026-09-16 用户报「他点了一下搜索框，又不点，又搁那划会话列表」）：
#   搜索路线点了**名字匹配**的结果行、内容也像目标，却因为「活动行时间戳读不出」被判否
#   ⇒ 整条搜索判失败 ⇒ **回退"找行 + 滚轮"** ⇒ 用户看到它在划会话列表。
#   修法：在搜索路线这个上下文里把"读不出"按**弱证据**放行（发送闸不动）。
_SEG_SEARCH = SRC_WECHAT.split("def open_chat_by_search(")[1][:6000]
ck("B20 搜索路线：'时间戳读不出'按弱证据放行（不再整条回退去滚列表）",
   '_why_s = str(idn_why)' in _SEG_SEARCH and '"读不出" in _why_s' in _SEG_SEARCH)
ck("B20a 发送闸没跟着放宽（注释里写明「发送闸一个字不动」）",
   "发送闸一个字不动" in _SEG_SEARCH)
# B17d~B17g 破 `no_ref` 死锁（2026-09-16 对面 r23 现场：参照只在"发送成功之后"才学，而 `no_ref`
#   直接拒发 ⇒ 永远拒、永远学不到；A 枪走"宽松成功"分支同样不学 ⇒ 全日志没有一次学会参照的记录）
_ST = SRC_WECHAT.split("def send_text_posted(")[1][:9000]
ck("B17d 指纹档给不出结论时改用四档证据兜底（否则 no_ref 死锁）",
   "self.chat_is_open(chat_id, gui=gui)" in _ST)
ck("B17e 四档放行后顺手补参照（破死锁的钥匙）",
   "放行时补参照" in _ST)
ck("B17f 宽松成功分支也学参照（对面 r23 的 A 枪走的就是这条）",
   _ST.count("self._learn_chat_header(chat_id, gui=gui)") >= 2)
ck("B17g 两条投递链的收尾（含早退路径）都放回收起状态",
   '_minimize_back_if_needed("投递文本链收尾")' in SRC_WECHAT
   and '_minimize_back_if_needed("投递文件链收尾")' in SRC_WECHAT)
# B17h r24 现场：最小化还原后投递打字不生效（同一轮里可见态 A1/A2 都成功、token 两处都搜不到）
#   ⇒ 打字前必须先投递点一次输入栏把焦点给它（旧版从不点输入框，靠"正常态默认有焦点"）
ck("B17h 打字前先投递聚焦输入栏（最小化还原后 WM_CHAR 会被丢）",
   "backend.click(main, focus_pt)" in _ST and "投递聚焦输入栏失败" in _ST)
# B20~B21 档位强弱（2026-09-16 r25 对面实测：会话头指纹档**会假阳性**——当前明明开着「E」时
#   `chat_is_open("filehelper")` 也返回 True；而 r24 我刚把这个函数接进身份闸的兜底 ⇒ 等于给"发错
#   会话"开了一道缝。⇒ 指纹档降级为弱档、默认不采信；标题带档提为首选（对面实测它有区分力：
#   'OE' vs 'O文亻牛传输助手'，且 r24 那次 A1 命中的正是这一档）。
_CIS = SRC_WECHAT.split("def chat_is_open(")[1][:4600]
ck("B20 会话头指纹档降级为弱档、默认不采信（只有 allow_weak 时才认）",
   "allow_weak" in _CIS and "弱档" in _CIS
   and "不足以确认当前会话，按**未确认**处理" in _CIS)
ck("B21 标题带档排在指纹档之前（对面实测才有区分力的是它）",
   _CIS.find("header_text(_im4)") >= 0 and _CIS.find("chat_header as _ch") >= 0
   and _CIS.find("header_text(_im4)") < _CIS.find("chat_header as _ch"))
ck("B18 竞态如实写进控制台（用户 2026-09-15 要求「这个你要如实跟用户讲清楚」）",
   "会不会跟你抢操作" in SRC_CONSOLE and "撞了它会用聊天区内容复核" in SRC_CONSOLE)
# B19~B21 零动作对照：阈值不许写死（2026-09-15；实测抓屏退回路径零动作差 0.142 > 老阈值 0.01）
_M_OPEN = SRC_WECHAT.split("def moments_open_posted(")[1][:3200]
_M_SCROLL = SRC_WECHAT.split("def moments_scroll_posted(")[1][:2400]
ck("B19 两条靠画面判成功的路径都先量了「零动作地板」（阈值跟着地板走）",
   "_gray_thresholds" in _M_OPEN and "_gray_thresholds" in _M_SCROLL
   and "def _gray_noise_floor(" in SRC_WECHAT)
ck("B20 老写死的 0.15 / 0.01 绝对值判定已清除（改为 None ⇒ 不动手）",
   "<= 0.15" not in _M_OPEN and "<= 0.01" not in _M_OPEN
   and "<= 0.01" not in _M_SCROLL
   and "_GRAY_NOISE_MAX" in SRC_WECHAT and "_UNSTABLE_MSG" in SRC_WECHAT)
ck("B21 判据不可用时**一枪都不投**（拒绝早于任何 click/wheel）",
   _M_OPEN.index("_gray_thresholds") < _M_OPEN.index("b.click(")
   and _M_SCROLL.index("_gray_thresholds") < _M_SCROLL.index("b.wheel("))

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
class _Screen:
    """假主窗画面：**只有"被操作"才变**（真机口径），外加可配置的抓图噪声。

    为什么必须这么建模：老的假图是"每抓一次翻转一次"，于是**零动作对照本身**就有 1.0 的差异
    ——那正是真机上的假成功形态（实测退回抓屏时零动作连拍两张差 0.142，远超当时写死的 0.01）。
    噪声按"奇偶抓图翻转前 k 个像素"生成 ⇒ 相邻两张的差异恰为 `noise`，与真机同形。
    """

    N = 64 * 48

    def __init__(self, noise=0.0, dead=False):
        self.state = 0
        self.noise = float(noise)
        self.dead = bool(dead)      # dead=True ⇒ 投递了画面也不变（模拟"投了没生效"）
        self.shots = 0

    def post(self, h, m, w, l):
        if not self.dead:
            self.state += 1         # 投递一枪 = 界面动一格
        return 1

    def thumb(self, rect, scale=(64, 48), gui=None):
        self.shots += 1
        # 内容只由"被操作过几次"决定：任何一次投递都让整幅图换一个灰度（相邻态差 ≥61 >28）
        base = 10 + (self.state * 61) % 190
        alt = 10 if base > 100 else 200
        seq = [base] * self.N
        for j in range(int(self.noise * self.N)):
            seq[(j * 7) % self.N] = alt if self.shots % 2 else base
        return seq


SCREEN = _Screen()


def _post(h, m, w, l):
    POSTED.append((int(h), int(m), int(w), int(l)))
    SCREEN.post(int(h), int(m), int(w), int(l))
    return 1


ib._post = _post
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
    ad._moments_gray_thumb = SCREEN.thumb
    ad._find_green_discover = lambda gui: (144, 682)          # 自证到的「发现」图标
    ad._moments_shot_ocr = lambda rect: [("朋友圈", 190, 159, 60, 20)]

    SCREEN.state = 0
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
    SCREEN.state = 0
    ok2, m2 = ad.moments_scroll_posted(direction=1, times=1)
    wheels = [1 for _h, m, _w, _l in POSTED if m == ib.WM_MOUSEWHEEL]
    ck("C5 投递档刷朋友圈：成功且说明未动光标", ok2 is True and "未动光标" in m2, m2[:70])
    ck("C6 滚轮是投递出去的（WM_MOUSEWHEEL ≥ 8 格）", len(wheels) >= 8, "%d 格" % len(wheels))
    ck("C7 刷朋友圈也没碰真鼠标原语", not TRIPPED, str(TRIPPED[:4]))

    # 假判据：界面没变 ⇒ 必须如实报"没生效"，不许假报成功
    SCREEN.dead, SCREEN.noise = True, 0.0
    POSTED[:] = []
    ok3, m3 = ad.moments_scroll_posted(direction=1, times=1)
    ck("C8 界面没变时如实报「没生效」（不假报）", ok3 is False and "没" in m3, m3[:60])
    ad._find_green_discover = lambda gui: None
    ok4, m4 = ad.moments_open_posted()
    ck("C9 没自证到图标时停手并说明（不盲点）", ok4 is False and "自证" in m4, m4[:60])
    ad._find_green_discover = lambda gui: (144, 682)

    # ── 零动作对照（2026-09-15 补）：阈值不许写死，先量"什么都不做时画面自己抖多少" ──
    SCREEN.dead, SCREEN.noise = True, 0.05          # 抓图在抖、投递又没生效
    POSTED[:] = []
    base_a = SCREEN.thumb((100, 100, 1300, 1000))
    base_b = SCREEN.thumb((100, 100, 1300, 1000))
    _floor = ad._gray_diff(base_a, base_b)
    ok7, m7 = ad.moments_scroll_posted(direction=1, times=1)
    ck("C12 地板 %.3f > 老写死阈值 0.01（老代码会拿它当「界面变了」假报成功）" % _floor,
       _floor > 0.01, "地板=%.3f" % _floor)
    ck("C13 有噪声 + 没生效 ⇒ 仍如实报没生效（阈值跟着地板走）",
       ok7 is False and "没" in m7, m7[:60])

    SCREEN.dead, SCREEN.noise = True, 0.14          # 噪声大到判据不可用
    ad._moments_gray_thumb = SCREEN.thumb
    POSTED[:] = []
    ok8, m8 = ad.moments_open_posted()
    ck("C14 噪声超限 ⇒ 判据不可用时**一枪都不投**并说明（不拿噪声当变化）",
       ok8 is False and "判据不稳" in m8 and not POSTED, m8[:60] + " / 投递=%d" % len(POSTED))
    POSTED[:] = []
    ok9, m9 = ad.moments_scroll_posted(direction=1, times=1)
    ck("C15 同上（刷朋友圈这条也拒绝动手）",
       ok9 is False and "判据不稳" in m9 and not POSTED, m9[:60] + " / 投递=%d" % len(POSTED))
    SCREEN.dead, SCREEN.noise = False, 0.0

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

print("\n[E] 死键收口：wechat.minimize_warning 必须真的有代码读它")
# 2026-09-15：这个键原来只在 config.py 与控制台出现，业务代码一处都没读 ⇒ 勾了没用（死键）。
# 现在它管 `_ensure_main_visible()` 里「最小化 + 未开自动还原」那一条的提醒。
_WX_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ck("E1 wechat.py 真的读了 minimize_warning", 'cfg.get("minimize_warning"' in _WX_SRC)
_i_restore = _WX_SRC.find('cfg.get("restore_minimized"')
_i_warn = _WX_SRC.find('cfg.get("minimize_warning"')
ck("E2 提醒就在「最小化 + 未开自动还原」那条分支上（两处读配置挨着）",
   _i_restore > 0 and _i_warn > _i_restore and (_i_warn - _i_restore) < 600,
   "restore@%d warn@%d" % (_i_restore, _i_warn))
ck("E3 关掉提醒时仍留一行说明（不是什么都不说）", "已按设置不提醒" in _WX_SRC)
ck("E4 提醒文案告诉用户两条出路（打开自动还原 / 关掉提醒）",
   "就把「最小化时自己还原」打开" in _WX_SRC and "就把「最小化提醒」关掉" in _WX_SRC)
ck("E5 反证：这个键只在函数体里被读，不是散在别处又抄一份默认值",
   _WX_SRC.count('minimize_warning') <= 3, "出现 %d 次" % _WX_SRC.count("minimize_warning"))

print("\n== F. 前台口径（跨机 r15 实测：伪激活会把微信短暂带到前台）==")
# 对面 r15 实测：投递链的伪激活会让微信**短暂真占前台**（发文字 1.8s、切会话 2.9~3.2s）后自动还回。
# ⇒ **对外文案不许写"不抢前台"**（那是过头话），必须写成"不动光标 + 可能短暂置前约 1~3 秒后自动还回"。
#    这条同时满足用户的口径要求：能力边界必须写进**终端用户看得到的地方**。
_CONSOLE = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_REPORT = open(os.path.join(ROOT, "scripts", "collect_report.py"), encoding="utf-8").read()
_BG = open(os.path.join(ROOT, "agent", "bg_status.py"), encoding="utf-8").read()
ck("F1 控制台不再写「不抢前台」，改成实测口径", "不抢前台" not in _CONSOLE and "短暂置前" in _CONSOLE)
ck("F2 体检报告那行也改了（跨机 r15 引用的就是它）",
   "不抢前台" not in _REPORT and "短暂把微信带到前台" in _REPORT)
ck("F3 bg_status（单一事实源）写明伪激活代价", "不抢前台" not in _BG and "短暂置前" in _BG)
# ⚠️ 2026-09-16 r17（跨机 r16 报的"文案残余"）：F 段原来只守三个文件 ⇒ 判据绿了、别处的旧说法还在。
#    ⇒ 扩到**所有对用户/工程可见的声明点**（历史更新日志与 _scratch 不算）。
_BAN = "不抢" + "前台"          # 自己拼出来，免得判据文件本身命中
_EXTRA = ["使用说明.md", "检验说明（另一台电脑用）.md", "AGENTS.md",
          os.path.join("agent", "tools.py"), os.path.join("agent", "notify_ui.py"),
          os.path.join("agent", "tray.py"), os.path.join("agent", "wechat.py")]
_bad = []
for _p in _EXTRA:
    _fp = os.path.join(ROOT, _p)
    if not os.path.isfile(_fp):
        continue
    if _BAN in open(_fp, encoding="utf-8", errors="ignore").read():
        _bad.append(_p)
ck("F6 其余声明点也不许再出现那句过头话（使用说明/检验说明/AGENTS/工具描述/托盘/wechat 注释）",
   not _bad, "还有：%s" % _bad)
_seg_sp = _WX_SRC[_WX_SRC.index("def send_text_posted"):]
_seg_sp = _seg_sp[:_seg_sp.find("\n    def ", 10)]
ck("F4 投递发送链进链就 stash 前台", "_stash_fg()" in _seg_sp)
ck("F5 点完「发送」后立刻盯着还前台（把可见时长压到最短）",
   '_restore_fg_until("投递发送后"' in _seg_sp)

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
