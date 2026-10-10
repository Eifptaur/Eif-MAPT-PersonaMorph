# -*- coding: utf-8 -*-
"""「点到搜一搜」的判据（2026-10-10 真机反馈驱动）。

## 用户报的现象（原话）
「老是点到『搜一搜』」—— 并给了浮窗截图：浮层里**一条真实会话结果都没有**，
只有顶部「搜索网络结果 / 文章、公众号、视频号等」＋下面「最近在搜 / <四条历史>」。

## 用户口述的机理（逐字核实过，成立）
1. 搜索框里**没字**（投字那一枪没落框）；
2. 程序**不查框**，直接去找"结果行"；
3. 但框里没字 ⇒ 微信**根本不会渲染"群聊/联系人"段**；
4. 于是 `find_popover_row` 的段兜底落到 **「搜索网络结果」那一片**；
5. `sec_y + 55` ⇒ 落点正好是 **"搜一搜"入口** ⇒ 点下去 ⇒ 出搜一搜。

## 本判据守四件事
  ① **闸⓪ 框空**：框里明确空着（占位词）⇒ `find_popover_row` 必须返回 None（源头掐断）。
  ② **闸④ 搜一搜行**：`_popover_web_row` 认出「搜索网络结果」及其副标题；**不许**误伤真会话名。
  ③ **落点带**：段兜底算出的落点若落在"搜一搜危险带"内 ⇒ 返回 None。
  ④ **反向控制**：正常浮层（框里有字 + 真有「联系人」段）⇒ 照旧能取到行（不误杀）。

用法：`python scripts/popover_souyisou_selftest.py`
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

from agent import chat_ocr as CO  # noqa: E402

_P = _F = 0


def ok(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
    else:
        _F += 1
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  [%s]" % detail) if detail else ""))


def _font(sz=18):
    """尽量拿一个可用字体；拿不到就返回默认字体（判据仍可跑）。"""
    try:
        from PIL import ImageFont
        for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                  r"C:\Windows\Fonts\simsun.ttc"):
            if os.path.exists(p):
                return ImageFont.truetype(p, sz)
    except Exception:  # noqa: BLE001
        pass
    return None


FONT = _font(18)
FONT_B = _font(16)


def popover(lines, box_text="", w=552, h=507):
    """造一张**像搜索浮层**的帧。

    `lines`：[(y, 文本), ...] 画在结果区。
    `box_text`：输入框里画什么（空串＝只有占位词「搜索」，模拟"字没投进去"）。
    """
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    # 顶部输入框
    d.rounded_rectangle((30, 20, w - 30, 62), radius=10, outline=(200, 200, 200), width=1)
    if box_text:
        d.text((60, 28), box_text, fill=(30, 30, 30), font=FONT)
    else:
        d.text((60, 28), "搜索", fill=(170, 170, 170), font=FONT)  # 占位词
    for y, t in lines:
        d.text((110, y), t, fill=(40, 40, 40), font=FONT_B)
    return im


print("=" * 72)
print("「点到搜一搜」判据")
print("=" * 72)

# ⛔ **本机没有 WinRT OCR**（`recognize()` 恒返回空）⇒ 任何"画字让 OCR 读"的判据
#   都会退化成空转。⇒ 本判据**全程打桩** `recognize` / `search_input_text`：
#   这一份测的是**行选取逻辑**（闸⓪④ 与落点），OCR 能力本身归 `chat_ocr_selftest` 管。
_ORIG_REC = CO.recognize
_ORIG_SIT = CO.search_input_text
_ORIG_SHELL = CO.popover_is_empty_shell


def _mk_stub(items, box_text):
    """造一对桩：`recognize` 按预设清单回文本、`search_input_text` 回框里的字。"""
    def _rec(img, *a, **k):
        # `find_popover_row` 内部裁剪时把 y 减了 80 ⇒ 还原回帧坐标
        return [(t, x, y - 80 if y >= 80 else y, w, h) for y, t, x, w, h in items]
    return _rec, (lambda img, _b=box_text: _b)


def _use(items, box_text, shell=False):
    CO.recognize, CO.search_input_text = _mk_stub(items, box_text)
    CO.popover_is_empty_shell = lambda img, _s=shell: (_s, "桩")


# ── ① `_popover_web_row` 认「搜索网络结果」那一行，且不误伤 ──
print("\n── ① `_popover_web_row`：认搜一搜那一行、不误伤真会话名 ──")
ok("「搜索网络结果」判是", CO._popover_web_row("搜索网络结果"))
ok("副标题「文章、公众号、视频号等」判是", CO._popover_web_row("文章、公众号、视频号等"))
ok("「搜一搜」判是", CO._popover_web_row("搜一搜"))
ok("OCR 认岔「搜索网绩结果」判是（骨架容错）", CO._popover_web_row("搜索网绩结果"))
for _good in ("文件传输助手", "群聊", "联系人", "张三", "aaa偷啃使用者", "宋", "E"):
    ok("真会话名/段标题「%s」**不许**判是" % _good, not CO._popover_web_row(_good))

# ── ② 闸⓪：框空 ⇒ 一律不取行（源头掐断）──
print("\n── ② 闸⓪：框里没字 ⇒ 不取行（这就是点到搜一搜的源头）──")
# 模拟用户截图那张浮层：只有「搜索网络结果」+「最近在搜」，框是空的
_EMPTY_ITEMS = [(130, "搜索网络结果", 0, 0, 16), (160, "文章、公众号、视频号等", 0, 0, 16),
                (215, "最近在搜", 0, 0, 16)]
_empty_box = popover([(130, "搜索网络结果"), (160, "文章、公众号、视频号等"), (215, "最近在搜")],
                     box_text="")
_use(_EMPTY_ITEMS, "")
ok("框空 + 只有「搜索网络结果」⇒ find_popover_row 返回 None",
   CO.find_popover_row(_empty_box, "宋") is None)
ok("框空 + 传入「群聊」（段兜底诱惑最大的那个词）⇒ 仍返回 None",
   CO.find_popover_row(_empty_box, "群聊") is None)
ok("框空 + 传入「联系」⇒ 仍返回 None", CO.find_popover_row(_empty_box, "联系") is None)

# ── ③ 反向控制：框里有字 + 真有「联系人」段 ⇒ 照旧取到行（不误杀）──
print("\n── ③ 反向控制：正常浮层（框有字 + 真有联系人段）⇒ 照旧取到行 ──")
_STUB_ITEMS = [(110, "联系人", 0, 0, 16), (185, "宋", 0, 0, 16), (240, "宋小某", 0, 0, 16)]
_use(_STUB_ITEMS, "宋")
_real = popover([(130, "联系人"), (185, "宋"), (240, "宋小某")], box_text="宋")
_r = CO.find_popover_row(_real, "宋")
ok("正常浮层里能取到「宋」那一行", _r is not None, str(_r))
if _r:
    ok("落点取的是「宋」那一行（≈185），不是段标题（110）也不是别处",
       170 <= int(_r["y"]) <= 215, "y=%d why=%s" % (_r["y"], _r["why"]))

# ── ③b 反向对照：同样的桩，只把「框里有字」换成「框空」⇒ 必须拦住 ──
print("\n── ③b 反向对照：同样的桩，只把「框里有字」换成「框空」⇒ 必须拦住 ──")
_use(_STUB_ITEMS, "")
_r_b = CO.find_popover_row(_real, "宋")
ok("框空 ⇒ 即使画面里有「宋」那一行也**不取**（闸⓪ 真的在源头拦）", _r_b is None, str(_r_b))

# ── ④ 落点带 ──
print("\n── ④ 落点带：段兜底算出的落点落在「搜索网络结果」危险带 ⇒ 拦 ──")
# ⚠️ 用例设计要点（设计错过两次，记下来）：
#   · `_popover_web_row` 认出那一行时会 `continue` **跳过它** ⇒ 它**不会**被当段标题 ——
#     那是闸④ 在更早处生效，**不是**落点带在起作用（所以用含"搜索…结果"的串测不出来）。
#   · `_popover_web_row` 的容错**已经覆盖**常见认岔（「搜索网绩结杲」「结菓」都认得出）
#     ⇒ 要触发落点带，必须是**判据认不出、却又含段骨架词**的形态。
#   真实最可能形态：那一行被 OCR **只读到「联系」两字**（整行其他字都糊了）——
#     于是它既不像"搜索网络结果"（判据认不出 ⇒ 不被 `continue`）、
#     又被当成了「联系」段标题 ⇒ `+55` 落点压在那一行下面 ⇒ 正是"搜一搜"。
#   桩：y=130 是那一行（读成「联系」），y=215 是真「群聊」段；
#     而 `_web_y` 由**同一次识别里另一条**读出（真机上一行常被读成两条：主标题+副标题）。
#   ⚠️ 行高必须给**真实值**（46）：给太小（如 14）会让危险带窄到拦不住 —— 真机浮层行高 42~52。
_TRAP_ITEMS = [(130, "联系", 0, 0, 46), (132, "搜索网络结果", 0, 0, 46),
               (215, "群聊", 0, 0, 46)]
_use(_TRAP_ITEMS, "完全不存在的名字")
_trap = popover([(130, "联系"), (132, "搜索网络结果"), (215, "群聊")],
                box_text="完全不存在的名字")
_r2 = CO.find_popover_row(_trap, "完全不存在的名字")
ok("段标题误落在那一片、+55 压在危险带 ⇒ 返回 None（防点到搜一搜）",
   _r2 is None, str(_r2))

# ── ④b 反向对照：把「搜索网络结果」那一行换成真段标题「群聊」⇒ 段兜底该照常生效 ──
print("\n── ④b 反向对照：正常段标题（「群聊」）⇒ 段兜底照常取行（不误杀）──")
_OK_ITEMS = [(130, "聊天记录", 0, 0, 16), (200, "群聊", 0, 0, 16), (255, "某群", 0, 0, 16)]
_use(_OK_ITEMS, "完全不存在的名字")
_okframe = popover([(130, "聊天记录"), (200, "群聊"), (255, "某群")], box_text="完全不存在的名字")
_r3 = CO.find_popover_row(_okframe, "完全不存在的名字")
ok("正常段标题 ⇒ 段兜底取到行（证明 ④ 拦的是搜一搜、不是把段兜底整条废掉）",
   _r3 is not None, str(_r3))

# ── ⑤ 源锚：发现页链**不许**再把「搜一搜」当"发现页已打开"的标志 ──
print("\n── ⑤ 源锚：**所有** `_discover_visible` 都不许把「搜一搜」当发现页标志 ──")
# 为什么（真机第二现场）：用户看到"点到搜一搜"还有**另一条路** ——
#   打开朋友圈的链里，`_discover_visible()` 原来判 `搜一搜 or 小程序 or 游戏`，
#   而**「搜一搜」本身也是"打开搜一搜之后"的画面内容** ⇒ 盲试点到侧栏的搜一搜图标、
#   打开后画面读到「搜一搜」⇒ 判据返回 True ⇒ 程序以为"发现页已打开"、
#   继续去点「朋友圈」坐标（落在搜一搜页面里）⇒ **连续两次点到搜一搜**。
#   ⚠️ 这个判据在库里**有两份**（投递档 + 真鼠标档）—— 这正是"之前说修了又出现"的原因，
#      所以本判据要**遍历全部**，不许只验第一处。
try:
    _wsrc = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    _segs, _pos = [], 0
    while True:
        _j = _wsrc.find("def _discover_visible", _pos)
        if _j < 0:
            break
        _segs.append(_wsrc[_j:_j + 1200])
        _pos = _j + 1
    ok("库里有两处 `_discover_visible`（投递档 + 真鼠标档）", len(_segs) >= 2,
       "找到 %d 处" % len(_segs))

    def _code_only(s):
        """只保留**代码行**（去掉 `#` 注释、三引号 docstring、行内注释）。

        ⛔ 必须去掉注释再判：判据要拦的是"代码里还拿「搜一搜」当标志"，
           而**注释里出现「搜一搜」是好事**（那是解释为什么不能这么干）。
        """
        _out, _inq = [], None
        for _ln in s.splitlines():
            _st = _ln.strip()
            if _inq:
                if _inq in _st:
                    _inq = None
                continue
            for _q in ('"""', "'''"):
                if _st.startswith(_q) and _st.count(_q) == 1:
                    _inq = _q
                    break
            if _inq:
                continue
            if _st.startswith("#"):
                continue
            _out.append(_ln.split("#")[0])
        return "\n".join(_out)

    for _n, _sg in enumerate(_segs, 1):
        _body = _code_only(_sg.split("except Exception")[0])
        ok("第 %d 处：**代码里**不含「搜一搜」" % _n, "搜一搜" not in _body,
           _body.replace("\n", " ")[-70:])
        ok("第 %d 处：代码里**仍有**「小程序」" % _n, "小程序" in _body)
except Exception as _e:  # noqa: BLE001
    ok("源锚读取", False, str(_e))

CO.recognize = _ORIG_REC
CO.search_input_text = _ORIG_SIT
CO.popover_is_empty_shell = _ORIG_SHELL

print("\n" + "=" * 72)
print("通过 %d / 失败 %d" % (_P, _F))
print("=" * 72)
sys.exit(1 if _F else 0)
