# -*- coding: utf-8 -*-
"""真语音条：**合成 → 播进虚拟声卡 → 让微信自己录 → 点发送**（发出去是微信语音条，不是文件）。

为什么单开一个模块：
`tools.send_voice_reply` 走的是"发文件"那条路（微信里显示成文件）；真语音条要靠**成对的虚拟声卡**
（VB-CABLE 之类）+ 微信自己在录音态里录。这条链 在实验脚本里跑通过，但**产品里一直没接**
（实验脚本后来清掉了）。这个模块就是那次实验的产品化。

三道闸（缺哪道就如实说，绝不假装发过）：
  ① **虚拟麦克风**（成对虚拟声卡）——`agent/audio_devices.virtual_mics()`
  ② **sounddevice**（把 PCM 播到虚拟声卡"扬声器"那一端）
  ③ **合成引擎**——`agent/voice_models.status()`

⇒ **位置不靠标定**：发送前扫一遍输入条图标行现算落点，认不出就拒发。
   旧版"先跑 `calibrate` 标定圆圈位置"那条路**已弃用**（`calibrate()` 现在直接抛错）。

产物与判据：人看的是"语音条"，机器只认 **DB 回读 `type=语音`**（不信 GUI 返回值）。
判据用**「事件 + 标量」**（`wechat.wait_new_message`：`filewatch` 盯 `message_*.db-wal` ＋ 只读
`MAX(local_id)` 标量）⇒ **一有写入立刻确认**，不用固定 sleep 等落库。
"""
import ctypes
import logging
import os
import time
import wave

from .config import get_config

#: 本模块的日志器。**必须有**（不是可有可无的装饰）：
#:   `_popup_find_toggle` 的「模板过期 ⇒ 从候选重学」与 `_OffscreenShown` 的「还原兜底」
#:   都写 `log.warning(...)`；模块级没有 `log` 时这两处会抛 `NameError`，
#:   而它们各自被宽 `except` 吞掉 ⇒ **表现成"帧路静默失效 / 兜底静默不生效"**，极难查。
#:   名字与 `config.py` 同一个（`"persona-morph"`）⇒ 日志格式与级别统一走项目那一套。
log = logging.getLogger("persona-morph")

#: 进录音态后"发送"那个绿簇：默认取渲染区右下（老实测 (0.927, 0.924) 量级）
DEFAULT_SEND = (0.932, 0.945)

#: 输入条图标行的机械定位：右侧那个圆圈＝"按住说话"入口。
#: ⚠️ 它的比例**会漂**——右侧栏（聊天信息）开着时实测 0.745，关着时 0.878
#: （同一台机器、同一个窗口尺寸，差 0.13＝155px，足够点到空白处）⇒ **不许硬编码**：
#: 每次发送前扫一遍图标行现算（扫不到才退回配置值/兜底候选）。
RECORD_X_MIN = 0.60 # 圆圈一定在输入条右半（左半是表情/盒子/文件夹/剪刀/麦克风）
RECORD_X_MAX = 0.92 # 别撞上最右边那个「发送」按钮
#: 兜底候选（渲染区比例）。0.9165 是**实测**值：从窗口自身画面量「输入框内右侧那个麦克风圆圈」的中心
#: （x≈1693/1853=0.914、y≈1405/1461=0.962；用带比例网格的裁图对齐读出的）。
RECORD_FALLBACKS = (0.914, 0.90, 0.878, 0.86)
METER_X = (0.56, 0.82) # 录音态里那串绿色**音量点**的横带（用来判"音频有没有真进微信的麦克风"）
METER_Y = (0.86, 0.98)
GREEN_X_MIN = 0.78 # 「绿色发送 ↑」一定在这一带右侧（音量点在它左边，别把均值带偏）
# ══════════════════════════════════════════════════════════════════════════════
# 落点口径：**记"相对位置"，不记"坐标"**
#
# 三条：
#   ① ⛔ **不许写绝对坐标** —— 形如 "x=850" 的值只对一台机器、一个缩放成立，换机即废；
#   ② ✅ **优先当场量** —— 抓得到帧时，按窗口自身画面找元素（颜色 / 图标簇），零常量；
#   ③ ✅ **兜底只用"与某个边/某个键的相对距离"** —— 见 `_FROM_RIGHT_LP`。
#
# ⚠️ **不是"占窗口宽的比例"**（实测，帧存于 `_scratch/_calib2/`）：
#   输入条**左排**图标在 919 宽与 1374 宽两帧上，中心 x **逐像素相同**
#   （55.5 / 109.5 / 163.5 / 217.5 / 292.5）⇒ 左排贴**左缘**、按固定像素间距排；
#   **右排**（语音切换键 / ✕ / 绿↑）贴**右缘**、同样按固定像素间距排
#   （语音切换键距右缘：919 帧 149.5、1256 帧 148.1、1374 帧 149.5）。
#   ⇒ 占宽比例会随窗口拉宽而漂（左排第 1 个图标：919 帧 0.0604 → 1374 帧 0.0404），
#     **用比例反而会点偏**；"与锚定边的距离"才是稳的那个量。
#   ⇒ 同理，"某键与某键的距离恒定"**只对同一排/同一组内的键成立**：
#     同一排（右排内部）互相推是稳的；跨排（左排键 ↔ 右排键）会随窗口宽度变，不可用。
#
# ✅ **跨机器稳定的量** = 与锚定边的距离 ÷ 当前缩放（＝96DPI 逻辑像素）：
#   图标随 DPI 等比放大 ⇒ 物理距离 = 逻辑距离 × scale。本模块一律存逻辑像素，
#   用时 × `display_metrics.scale(hwnd)`。
#
# ⚠️⚠️ **单位必须逐处写清**：本模块同时存在三种像素空间，混用是本文件历史 bug 的根因——
#     · **逻辑像素（96DPI）**：本常量区、配置项、`render` 图（`_print_window` 默认不乘 DPI）；
#     · **物理像素**：`GetWindowRect` / `GetClientRect`（进程已锁 PerMonitorV2）、整窗帧；
#     · **客户区像素**：投递点击要的坐标系（`_posted_click` 的 cx/cy）。
#   写代码时**每个坐标变量都要能一眼看出属于哪一种**。
# ══════════════════════════════════════════════════════════════════════════════
#: ⛔ 三个历史常量**已作废**（值来自两套互不统一的基准：有的按"客户区宽"折、有的按"帧宽"量，
#:   且 `TOGGLE_FROM_RIGHT_PX` 当时量的还是**左排那个麦克风**、不是右排的语音切换键）。
#:   保留符号只为避免 ImportError；**任何调用点引用它们都算 bug**。
CANCEL_FROM_RIGHT_PX = None
TOGGLE_FROM_RIGHT_PX = None
SEND_GREEN_FROM_RIGHT_PX = None
#: ★★ 「投递路」：**不动光标、不借前台、不注入键盘**，全靠 `PostMessage` 点鼠标。
#:    实测（独立窗"文件传输助手"，可见非前台 + 最小化两档都过）：
#:      ① 先点**语音切换键**（输入条最右那个"声波"图标）⇒ 输入条切成「按住 说话」；
#:      ② 再点输入条中部的**大按钮**（"按住说话"热区）⇒ 进录音态（✕+音量点+绿↑）；
#:      ③ 播音频进虚拟麦；④ 点**绿↑**发送。
TOGGLE_Y = 0.952 # 输入条图标行 y 比例（客户区高）——**仅兜底**（有帧时一律用帧现算）
#: 出厂兜底：**各落点"距客户区右缘"的逻辑像素（96DPI）**。
#:   物理距离 = 本值 × 当前缩放（`display_metrics.scale(hwnd)`）。
#:   ⛔ **只在抓不到帧时用**（真最小化、被遮挡）；抓得到帧一律以**当场量到的**为准。
#:   量法：在独立窗整窗帧上按颜色 / 暗簇找元素中心，量"中心 距**帧**右缘"的物理像素，
#:        再减右边框（11 物理 @150%，即"帧右缘 → 客户区右缘"那一段）、最后 ÷1.5 折成逻辑像素。
#:   换微信版本 / 字体大小导致偏移时，改这里一处即可。
_FROM_RIGHT_LP = {          # 单位：逻辑像素（96DPI）｜基准：客户区右缘
    "toggle": (149.5 - 11) / 1.5,   # 语音切换键 —— 右排**最右的真图标**（不是左排那个麦克风）
    "green": (68.5 - 11) / 1.5,     # 绿↑ 发送 —— 录音态右下那团绿
    "cancel": (371.5 - 11) / 1.5,   # ✕ 取消 —— 录音态、与绿↑ 同排、在它左边
}
#: ✕ 与绿↑ 的**中心间距**（逻辑像素）：两键同在右排 ⇒ 这个差值与窗口宽无关。
#:   ⛔ 只对"同一排内部的键"成立；拿左排的键来推右排的键会随窗口宽度漂，不可用。
_DELTA_CANCEL_FROM_GREEN_LP = _FROM_RIGHT_LP["cancel"] - _FROM_RIGHT_LP["green"]


def _cancel_from_green(cw: int, green_x: int, ch: int, row_y: int, scale: float = 1.0) -> tuple:
    """**由"绿↑ 的位置"推出 ✕ 的落点** ⇒ `(x, y)`。

    ⭐ 口径：**记"相对位置"，不记坐标**。
      关系：**✕ 与绿↑ 同排**（同一 y 带），**✕ 在绿↑ 左边固定距离**。
      实测（919 宽整窗帧）：✕ 中心距**帧**右缘 371.5、绿↑ 中心距帧右缘 68.5 ⇒ 间距 303 物理像素。
      ⇒ 本函数只用**两键的中心间距**（相对距离），不碰任何绝对坐标：
        只要 `green_x` 是当场量到的，算出的 ✕ 就跟着对。
      ⛔ 这个间距**只对"同一排内部的键"成立**（两键同在右排、跟着右缘走）。
        拿左排的键来推右排的键会随窗口宽度漂 —— 不可用。

    ⚠️ **单位**：`cw / green_x / ch / row_y` 与返回值**必须同处一个像素空间**（由调用方保证），
      且该空间是**物理像素**（`scale` 用来把逻辑间距折成物理间距）。
      若调用方用的是逻辑像素空间，传 `scale=1.0`。

    ⚠️ **未经跨 DPI 交叉验证**：303 是**一台机器、150% 缩放**下量到的。
      换成别的 DPI 时，若该间距**不随 DPI 等比缩放**（例如两键间距由字号而非图标尺寸决定），
      本函数给出的 ✕ 会偏。⇒ 因此**返回 `None` 时调用方必须接受"这次取消不了"**
      （⛔ 不许退回写死的坐标）。

    ⛔ **返回前必过守卫**：算出的 ✕ 必须**既在绿↑ 左边、又在窗口内**，
      否则返回 `None`（调用方**宁可取消不了，也不许点错地方**）。
    """
    try:
        _delta = float(_DELTA_CANCEL_FROM_GREEN_LP) * float(scale or 1.0)   # 逻辑 → 当前空间的像素
        x = int(round(int(green_x) - _delta))
        y = int(row_y)
        # 守卫：✕ 必须在绿↑ 左边、且在窗口内（否则宁可不点，不许乱点）
        if x < 1 or x >= int(green_x) or x > int(cw) or y < 1 or y > int(ch):
            return None
        return x, y
    except Exception:  # noqa: BLE001
        return None
#: 帧内"贴边簇"阈值：≥ 此比例视为**窗口边框竖线**，不是图标（27 张图一致：0.988~0.999）。
FRAME_EDGE_REL = 0.97
#: ⭐ 「右排最右的真图标」的**相对位置**校验：它必须落在帧**右侧这一带**。
#:   用"占帧宽的比例"当**粗筛**是安全的（这里只是排除"把左排键或边框当成语音切换键"），
#:   真正的落点仍按"距右缘的相对距离"算 ⇒ 粗筛不参与定位，只参与识别。
TOGGLE_RIGHT_BAND = (0.75, 1.0)
#: 图标行「暗点」阈值 ＝ **本带背景亮度 − 这个差值**（相对量，不写死灰阶）。
#: 为什么必须相对（真机取证）：某档窗口尺寸下，输入条**右边那两个图标**
#: （声波切换键 / 发送键）比左排**画得浅**（灰阶 140+）⇒ 写死一个绝对灰阶会
#: **一个簇都切不出来** ⇒ 最右真图标变成左排那个 ⇒ 语音切换键定位失败。差值取 30~50 都稳。
ICON_INK_DELTA = 40
#: 图标相似度的**取样半径**：帧短边 × 本比例（**不写死像素** ⇒ 跨 DPI / 窗口尺寸都成立）。
_ICON_HALF_REL = 0.022
#: 图标相似度（模板匹配，归一化互相关）的**下限**。
#: 标定口径（两个探针，都用真机帧量，不是拍的）：
#:   · `_scratch/_icon/measure_icon_gate.py`（真位置 vs 错位置）：
#:     真位置 **0.930~1.000**（含 ±6px 偏移、含跨尺寸 0.8×~1.5×）；错位置 **≤0.760**（中位 0.252）；
#:   · `_scratch/_icon/measure_scale_invariance.py`（跨尺寸）：最低 **0.930**。
#:   ⇒ 取 **0.82**（不是正中点 0.845）：真位置侧余量 0.06~0.11、错位置侧余量 0.06；
#:   ⛔ 假阴（挡住正常发送）比假阳更难发现 ⇒ 阈值宁可**偏低一点**，但要**仍能拦住**实测的错位置。
#: ⛔ 改这个值必须**重跑那两个探针**；只在一帧上"自比 1.000"不能作为放行的依据。
_ICON_MATCH_MIN = 0.82

# ══════════════════════════════════════════════════════════════════════════════
# 「屏外显示」用的 ShowWindow/SetWindowPos 常量（最小化档投递的前提，见 `_OffscreenShown`）
# ══════════════════════════════════════════════════════════════════════════════
_SW_SHOWMINIMIZED = 2
_SW_SHOWMAXIMIZED = 3
_SW_SHOWMINNOACTIVE = 7
_SW_SHOWNOACTIVATE = 4
# ⛔⛔ **不要用 `SW_MINIMIZE`(6) 收窗口**（常量索性不定义，防误用）：
#   `SW_MINIMIZE` 的文档语义是「最小化**并激活 Z 序里的下一个顶层窗**」
#   ⇒ 独立窗是主窗的 **owned window**，Z 序里紧挨着的下一个就是**微信主窗**
#   ⇒ 每收一次独立窗，**用户的主窗就被顶到前台一次**（而他什么都没做）。
#   ✅ 收窗口一律用 `_SW_SHOWMINNOACTIVE`(7)：「显示成最小化，**不激活**」（也不激活别人）。
#   ⚠️ `_SW_SHOWMINIMIZED`(2) 只用来**读原状**（`GetWindowPlacement.showCmd` 的比对值），不拿去调用。
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
#: 屏外位置时给窗口留的额外偏移（保证 rect 与屏幕**无交集**）。
_OFFSCREEN_MARGIN = 200
#: **按住录音、松开发送**，走 `SendInput` 注入（不动鼠标）；投递键盘消息**不认**（实测 A 段失败）。
VK_RMENU = 0xA5
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    """⚠️ x64 下这个结构必须是 **40 字节**（type 4 + 填充 + 联合体 32）；写小了 SendInput 直接失败。"""
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _send_alt(down: bool) -> bool:
    """注入**真·右 Alt**（SendInput + 扩展位）：**不动鼠标**，消息发给当前焦点窗口。

    为什么不用**投递键盘**：键盘这类"按住才有效"的消息要求**真实按键队列**，
    `PostMessage(WM_KEYDOWN)` 投给微信时它**不认**（`GetKeyState` 读的是系统键态，投递消息改不了它）；
    `SendInput` 才行。
    ⚠️ **但注意**：**鼠标**投递是**认**的——投递点击能进录音态、能发送
    （见 `_real_click` 的更正说明）。⇒ "投递不行"只对**键盘**成立，别把它推广到鼠标。

    前置＝微信在前台（`SendInput` 发给焦点窗口）——**这是 Alt 路自己的限制**，不是真语音条的限制
    （鼠标投递路不需要前台）。
    """
    try:
        flags = (0 if down else KEYEVENTF_KEYUP) | KEYEVENTF_EXTENDEDKEY
        inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(VK_RMENU, 0, flags, 0, None)))
        return bool(ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def _enter_record_alt(wechat, gui, cfg=None, wait_s: float = 4.0) -> tuple:
    """**按住右 Alt 进录音态** ⇒ (ok, 说明)。判据＝绿簇；**先确认抓得到帧**，否则是假阴性。

    这条路的好处：**不碰光标、不依赖任何坐标**（DPI/窗口尺寸/右侧栏怎么变都不影响）。
    前置＝微信在前台（`SendInput` 发给焦点窗口；提权窗口在最前时同样被 UIPI 挡 ⇒ 由 `_borrow_foreground` 覆盖）。
    """
    try:
        from . import chat_header as ch
    except Exception:
        ch = None
    if ch is not None:
        try:
            if ch.grab_render(gui) is None:
                return False, ("抓不到微信画面（窗口被遮挡/最小化）⇒ 没法确认有没有进录音态，"
                               "这一枪不发（先让微信可见）")
        except Exception:
            pass
    t0 = time.time()
    shot = 0
    blind = 0   # 抓到了"判不了"（而不是"没有绿簇"）的次数
    while time.time() - t0 < float(wait_s):
        shot += 1
        if not _send_alt(True):
            return False, "SendInput 注入右 Alt 失败（被系统挡了？）"
        time.sleep(1.0)
        _gc = _green_cluster(gui)
        if _gc:
            return True, "按住右 Alt 进录音态（第 %d 次）" % shot, "alt"
        if _gc is None:
            blind += 1
        _send_alt(False) # 没进态：先松开，免得误触发
        time.sleep(0.5)
    if blind:
        # 判据一直不可用 ⇒ 只能说"看不见"，不能说"没进态"（后者是结论，前者只是没拿到证据）
        return False, ("按住右 Alt 后**抓不到画面**（%d/%d 次判不了）⇒ 无法确认有没有进录音态；"
                       "先让微信可见再试" % (blind, shot)), "alt"
    return False, "按住右 Alt 也没进录音态（试了 %d 次）" % shot, "alt"


def _cancel_point(cw: int, ch: int, rb) -> tuple:
    """录音态「✕ 取消」的落点（**真点/alt 档用**）⇒ `(x, y)`（传入坐标系的像素）。

    ⛔⛔ **本函数已作废（复核）** —— 原实现用写死的 `CANCEL_FROM_RIGHT_PX`，
       而那个值（654.7）**基准错了**（真值 372，见常量区说明）⇒ 点下去会**偏 283px**。
       现在**直接抛错**，防止有人无意中调它点到用户屏幕上的错误位置。

    ⭐ **正确做法，待实现）**：**不写坐标、只写关系、关系当场量**。
      ✕ 的可靠关系有两条（都在标定帧上量到，见 `_MEASURED`）：
        ① **与绿↑ 同排**（同一 y 带）⇒ y 跟着绿↑/输入条图标行走，不另写；
        ② **距右缘 = 绿↑ 距右缘固定倍数/加数** ⇒ 用绿↑ 的位置推 ✕，而不是各写一个绝对数。
      ⇒ 实现要点：**先量到绿↑（或输入条右端锚点）**，再由它推出 ✕；
        ⛔ 不许两个键各写一个"距右缘多少像素"的常数（那还是坐标，到别人机器上没有意义）。
    """
    raise NotImplementedError(
        "`_cancel_point` 已作废：原用写死的 CANCEL_FROM_RIGHT_PX(=654.7，基准错误，真值 372)。"
        "改法：由'绿↑ / 输入条右端锚点'的位置按固定关系推出 ✕ —— 只写关系、关系当场量，见 PL 第 200 项。"
    )


def _cancel_point_live(gui, rw: int, rh: int, rb) -> object:
    """**当场量**出 ✕ 的落点 ⇒ `(x, y)` 或 `None`（拿不到就不点）。

    ⭐ 口径：**不写坐标，只记"键与键的相对位置"**。
      流程：① 复用 `_green_send(gui)` **当场找绿↑**（它抓的是**独立窗渲染区**，
               不碰主窗 ⇒ 不违反"不影响观看"）；
            ② 由绿↑ 推 ✕（同排、且在它左边固定间距，见 `_cancel_from_green`）。
      ⇒ 全程**不读**任何绝对坐标，窗口变宽/换 DPI 都跟着锚点走。

    ⛔ **单位**：`rw/rh` 与 `_green_send` 的返回值同处**渲染图像空间**；该图由
      `chat_header.grab_render` 走 `_print_window`（**默认不乘 DPI ⇒ 逻辑像素**）得到，
      ⇒ 传给 `_cancel_from_green` 的 `scale=1.0`（间距本身已是逻辑像素，不再折算）。
      ⚠️ 若哪天 `grab_render` 改抓物理像素，这里必须同步改成传 `scale`，否则 ✕ 会偏。

    ⛔ 找不到绿↑ / 算出的点越界 ⇒ 一律返回 `None`。
      **宁可取消不了（那次录音留在那儿等你处理），也不许瞎点**（点在用户屏幕上更糟）。
    """
    try:
        g = _green_send(gui)
        if not g:
            return None
        return _cancel_from_green(int(rw), int(g[0]), int(rh), int(g[1]), 1.0)
    except Exception:  # noqa: BLE001
        return None


def _from_right_lp(name: str) -> float:
    """取"某落点距**客户区右缘**的距离"（**逻辑像素 / 96DPI**）。

    取值顺序：① **本机实测记下的**（`display_metrics` 档案里的 `anchors`）→ ② 出厂默认 `_FROM_RIGHT_LP`。
    实测值优先，是因为微信版本 / 聊天字号差异会改变这个距离；出厂默认只保证"第一次能用"。
    """
    try:
        from . import display_metrics as _dm
        got = float(_dm.anchor(name) or 0)
        if got > 0:
            return got
    except Exception:  # noqa: BLE001
        pass
    return float(_FROM_RIGHT_LP.get(name) or 0)


def _remember_anchor_from_client(name: str, hwnd: int, x_client: int) -> None:
    """抓帧量到元素中心后，把它"距客户区右缘的逻辑像素"**记进档案**（供无帧兜底用）。

    ⛔ 单位换算：`x_client` 是**客户区物理像素** ⇒ 距离先按物理像素算，再 ÷ 当前缩放折成逻辑像素。
    ⛔ **合理性闸门**：只接受落在"出厂默认的 0.3~3.0 倍"内的值 ——
      抓帧可能抓到**不是目标窗**的画面（别的窗、工具窗），那样量出来的距离是垃圾；
      没有这道闸，一次误抓就会把正确的档案值冲掉。真·微信改版导致的偏移通常远小于这个带宽。
    任何异常一律吞掉（记账失败不该影响这一次发送）。
    """
    try:
        cw, _ch = _client_size_for_posted(int(hwnd), None)
        if int(cw) <= 100:
            return
        lp = (float(cw) - float(x_client)) / float(_scale_of(int(hwnd)) or 1.0)
        base = float(_FROM_RIGHT_LP.get(str(name)) or 0)
        if base > 0 and not (0.3 * base <= lp <= 3.0 * base):
            return                      # 不像这个元素 ⇒ 不记（宁可留着旧值）
        from . import display_metrics as _dm
        _dm.remember_anchor(str(name), lp, src="frame")
    except Exception:  # noqa: BLE001
        pass


def _scale_of(hwnd: int) -> float:
    """取缩放倍率（拿不到 ⇒ 1.0）。只读，不改档案。"""
    try:
        from . import display_metrics as _dm
        return float(_dm.scale(int(hwnd)) or 1.0)
    except Exception:  # noqa: BLE001
        return 1.0


#: "距客户区**下缘**的逻辑像素（96DPI）"这个锚点的键名。
#: ⛔ 它与 `_FROM_RIGHT_LP` 那一族**同用一个档案格子**（`display_metrics` 的
#:   `anchors.<键>.from_right_lp` 字段），但语义是"距**下缘**" —— 见 `_remember_bottom_anchor`。
_ANCHOR_TOGGLE_Y = "toggle_y_from_bottom"
#: 合理性带宽（逻辑像素）：输入条图标行离下缘就那么点距离，超出来的一定是量错了。
_TOGGLE_Y_LP_MIN, _TOGGLE_Y_LP_MAX = 4.0, 200.0


def _remember_bottom_anchor(hwnd: int, y_client: int) -> None:
    """把"语音键距**客户区下缘**"记进档案（逻辑像素），供**抓不到帧**时兜底。

    为什么需要它：`TOGGLE_Y` 是"客户区高的**比例**"，而输入条是贴着窗口**下缘**排的、
    行高固定 ⇒ 窗口一高，比例算出来的 y 就**偏高**（实测客户区 1446 时偏高约 28px）。
    **"距下缘的固定距离"才是与窗口高无关的量**（与 x 用"距右缘"同一个道理）。
    ⛔ 合理性闸门：只接受 4~200 逻辑像素 —— 抓帧可能抓到**别的窗**，没这道闸会冲掉好值。
    任何异常一律吞掉（记账失败不该影响这一次发送）。
    """
    try:
        _cw, _ch = _client_size_for_posted(int(hwnd), None)
        if int(_ch) <= 100:
            return
        lp = (float(_ch) - float(y_client)) / float(_scale_of(int(hwnd)) or 1.0)
        if not (_TOGGLE_Y_LP_MIN <= lp <= _TOGGLE_Y_LP_MAX):
            return
        from . import display_metrics as _dm
        _dm.remember_anchor(_ANCHOR_TOGGLE_Y, lp, src="frame")
    except Exception:  # noqa: BLE001
        pass


def _from_bottom_lp(name: str) -> float:
    """取"某落点距**客户区下缘**的逻辑像素"（96DPI）；本机没量过 ⇒ `0`（调用方退回比例）。"""
    try:
        from . import display_metrics as _dm
        return float(_dm.anchor(str(name)) or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def measure_landing_points(hwnd: int, cfg=None) -> dict:
    """**开机实测落点**：抓一帧，把能认出来的落点量一遍并**记进档案** ⇒ 返回台账 dict。

    为什么要它：抓不到帧时落点靠"距客户区右缘的逻辑像素"兜底，而这个距离会随
      **微信版本 / 聊天字号 / 图标尺寸**变 ⇒ 必须在真机上量一次、记住，而不是一直用出厂默认。

    三态（**不许把"量不到"当成"量到了"**）：
      · 认到元素 ⇒ `src="measured"`（已写进档案）；
      · 认不出 / 抓不到帧 ⇒ `src="cached"`（档案里记过的）或 `"factory"`（出厂默认）。

    ⛔ **只对独立窗量**（主窗的输入条几何与独立窗不同 ⇒ 在主窗上量的值会污染独立窗的落点）。
      传入主窗 ⇒ 直接返回 `{"skipped": …}`。
    ⛔ 能实测的只有**正常态**存在的元素（语音切换键）；绿↑ / ✕ 只在**录音态**才有
      ⇒ 开机时量不到，会如实报 `cached`/`factory`（它们在真发送时被当场量到、自动记档）。

    ⛔ 单位：`from_right_lp` 一律是**逻辑像素（96DPI）**。
    ⛔ **任何异常都不抛出**（启动链不能被它打断）。
    """
    out = {}
    try:
        h = int(hwnd or 0)
        from . import display_metrics as _dm
        if not h:
            return {"skipped": "没有窗口句柄"}
        try:
            from . import input_backend as _ib
            if h == int(_ib.find_main_window() or 0):
                return {"skipped": "只对独立窗量（主窗几何不同，量了会污染落点）"}
        except Exception:  # noqa: BLE001
            pass
        measured = False
        try:
            feat = _popup_find_toggle(h)     # 内部会顺带把实测值记档（`_remember_anchor_from_client`）
            measured = bool(feat)
        except Exception:  # noqa: BLE001
            measured = False
        for nm in ("toggle", "green", "cancel"):
            got = float(_dm.anchor(nm) or 0)
            if got > 0:
                out[nm] = {"from_right_lp": round(got, 1),
                           "src": ("measured" if (nm == "toggle" and measured) else "cached")}
            else:
                out[nm] = {"from_right_lp": round(float(_FROM_RIGHT_LP.get(nm) or 0), 1),
                           "src": "factory"}
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)[:80]
    return out


def _cancel_alt(gui) -> None:
    """Alt 路中途要放弃：**先发 Esc 取消**再松开 Alt（直接松开会把这段录进去发出去）。"""
    try:
        pw = ctypes.windll.user32
        for vk in (0x1B,): # VK_ESCAPE
            inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, 0, 0, None)))
            pw.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)))
            pw.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.3)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    _send_alt(False)
    time.sleep(0.4)


def pick_record_x(cols, width, x_min=RECORD_X_MIN, x_max=RECORD_X_MAX):
    """从**暗点列剖面**里挑「进录音态那个圆圈」的 x 比例（纯函数，便于离线自检）。

    ① 列计数聚簇（空 1 列以上即断开）；② 只留中心比例落在 [x_min, x_max] 的簇；
    ③ 取**最左**那个 —— 输入框里打了字时「发送」也会变深成簇，而它在圆圈右边，
    所以"右区最左的簇"才是圆圈（实测：圆圈 0.878、发送 0.93）。
    找不到 ⇒ None（调用方退回配置值 / 兜底候选）。
    """
    try:
        w = float(width or 0)
        if w <= 0 or not cols:
            return None
        thr = max(2, int(max(cols) * 0.12))
        cl, s = [], None
        for x, v in enumerate(cols):
            on = int(v) >= thr
            if on and s is None:
                s = x
            elif not on and s is not None:
                if x - s >= 3:
                    cl.append((s, x - 1))
                s = None
        if s is not None:
            cl.append((s, len(cols) - 1))
        m = []
        for c in cl:
            if m and c[0] - m[-1][1] <= 14:
                m[-1] = (m[-1][0], c[1])
            else:
                m.append((c[0], c[1]))
        good = [c for c in m if x_min <= ((c[0] + c[1]) / 2.0 / w) <= x_max]
        if not good:
            return None
        a, b = good[0]
        return (a + b) / 2.0 / w
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _icon_cols(gui):
    """抓一帧渲染图 → 找输入条图标行 → (列剖面, 图宽, 行 y) 或 None。"""
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        g = img.convert("L")
        w, h = g.size
        band = g.crop((0, int(h * 0.86), w, h))
        px = band.load()
        bw, bh = band.size
        rows = []
        for y in range(0, bh, 2):
            n = 0
            for x in range(0, bw, 2):
                if px[x, y] < 128:
                    n += 1
            rows.append((n, y))
        rows.sort(reverse=True)
        y0 = rows[0][1] if rows else 0
        cols = [0] * bw
        for y in range(max(0, y0 - 12), min(bh, y0 + 13)):
            for x in range(bw):
                if px[x, y] < 128:
                    cols[x] += 1
        return cols, bw, int(h * 0.86) + y0, h
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def locate_record(gui, cfg=None) -> tuple:
    """现算「按住说话」圆圈的 x 比例 ⇒ (x, 来源说明)。"""
    c = _cfg(cfg)
    got = _icon_cols(gui)
    if got:
        x = pick_record_x(got[0], got[1])
        if x:
            return x, "扫图标行现算"
    rb = c.get("record_btn") or []
    try:
        if rb and float(rb[0]) >= 0.80:
            return float(rb[0]), "配置 voice_strip.record_btn"
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return RECORD_FALLBACKS[0], "兜底候选"


def _cfg(cfg=None) -> dict:
    try:
        return dict((cfg or get_config()).get("voice_strip") or {})
    except Exception:
        return {}


def _virtual_mic() -> tuple:
    """有没有可用的虚拟麦克风（成对虚拟声卡）⇒ (名字, 说明)。"""
    try:
        from . import audio_devices as ad
        mics = ad.virtual_mics(only_active=True) or []
        clean = [m for m in mics if not ad._is_mixing(m)] if hasattr(ad, "_is_mixing") else mics
        if mics:
            pick = (clean or mics)[0]
            return str(pick.get("name") or ""), ""
        return "", "没检测到虚拟麦克风（要成对的虚拟声卡，如 VB-CABLE；装完在控制台点「重检」）"
    except Exception as e:
        return "", "虚拟声卡检测失败：%s" % e


def _out_device() -> tuple:
    """把声音播到哪块设备（虚拟声卡的"播放"端）⇒ (索引, 名字, 说明)。"""
    try:
        import sounddevice as sd
    except Exception as e:
        return -1, "", "没装 sounddevice（%s）⇒ 播不进虚拟声卡" % type(e).__name__
    want = str(_cfg().get("out_device") or "").strip().lower()
    try:
        devs = sd.query_devices()
    except Exception as e:
        return -1, "", "sounddevice 查不到设备：%s" % e
    for i, d in enumerate(devs):
        n = str(d.get("name") or "")
        if int(d.get("max_output_channels") or 0) <= 0:
            continue
        if want:
            if want in n.lower():
                return i, n, ""
            continue
        if "cable" in n.lower() or "scream" in n.lower():
            return i, n, ""
    _outs = [str(d.get("name") or "") for d in devs
             if int(d.get("max_output_channels") or 0) > 0]
    if want:
        return -1, "", ("配置里指定的输出设备没找到：%s ⇒ 本机能用的输出设备有：%s。"
                        "两条路：① 装一个虚拟声卡（VB-Cable / Scream）并让配置指过去（推荐，不吵人）；"
                        "② 临时把它指到一个**真实扬声器** —— 那条路会**外放一声**，"
                        "再由「立体声混音」录回来凑成环路（能跑通，但会打扰在场的人）"
                        % (want, "、".join(_outs[:6]) or "（一个都没枚举到）"))
    return -1, "", ("没找到虚拟声卡的播放端（名字里含 CABLE / Scream 的那块）⇒ "
                    "本机能用的输出设备有：%s。装一个 VB-Cable / Scream 最省事；"
                    "也可以把 `out_device` 指到真实扬声器（会外放一声）"
                    % ("、".join(_outs[:6]) or "（一个都没枚举到）"))


def status(cfg=None) -> dict:
    """能不能发真语音条 ⇒ {ok, why, mic, out_dev, out_idx, engine, calibrated}。"""
    c = _cfg(cfg)
    mic, why_mic = _virtual_mic()
    idx, name, why_out = _out_device()
    eng = {}
    try:
        from . import voice_models as vm
        eng = vm.status() or {}
    except Exception as e:
        eng = {"ok": False, "why": "合成引擎查不了：%s" % e}
    calibrated = bool(c.get("record_btn"))
    why = ""
    if not mic:
        why = why_mic
    elif idx < 0:
        why = why_out
    elif not eng.get("ok"):
        why = "没有可用的合成引擎：%s" % (eng.get("why") or "未知")
    # ⚠️ 位置**不再是前置条件**：`_enter_record` 每次发送前都会扫输入条图标行现算，
    #   配置里的 record_btn 只当兜底 ⇒ 这里不再因为"没标定"而拒发（那条老提示会把能用的人挡在门外）。
    return {"ok": not why, "why": why, "mic": mic, "out_dev": name, "out_idx": idx,
            "engine": (eng.get("engine") or eng.get("backend") or ""), "calibrated": calibrated,
            "auto_locate": True,
            "switch": bool(c.get("enabled"))}


def _read_pcm(path: str):
    """读成 (采样率, 声道, 样本宽度, 裸 PCM 字节)。"""
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())


def _ensure_wav(path: str) -> tuple:
    """不是 RIFF 就用 ffmpeg 转一份 wav（合成端可能是 mp3）。⇒ (wav 路径, 说明)"""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except Exception as e:
        return "", "音频读不了：%s" % e
    if head == b"RIFF":
        return path, ""
    try:
        from . import ffmpeg_bin as fb
        ff = fb.path()
    except Exception:
        ff = ""
    if not ff:
        return "", "产物不是 wav，而这台机器没有 ffmpeg 可转"
    out = os.path.splitext(path)[0] + "_pcm.wav"
    import subprocess
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-ar", "22050", "-ac", "1", out],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return "", "ffmpeg 转 wav 失败：%s" % e
    return (out, "") if os.path.exists(out) else ("", "ffmpeg 没产出 wav")


def play_to_cable(wav_path: str, out_name: str = "") -> tuple:
    """把 wav 播进虚拟声卡（阻塞到播完）。⇒ (ok, 说明)"""
    try:
        import sounddevice as sd
    except Exception as e:
        return False, "没装 sounddevice：%s" % e
    idx, name, why = _out_device()
    if idx < 0:
        return False, why
    try:
        rate, ch, width, pcm = _read_pcm(wav_path)
    except Exception as e:
        return False, "wav 读不了（%s）：%s" % (type(e).__name__, e)
    dtype = {1: "int8", 2: "int16", 4: "int32"}.get(width, "int16")
    try:
        with sd.RawOutputStream(samplerate=rate, channels=ch, dtype=dtype, device=idx) as st:
            st.write(pcm)
        return True, "已播到 %s（%.2f 秒）" % (name, len(pcm) / float(max(1, rate * ch * width)))
    except Exception as e:
        return False, "播到 %s 失败：%s" % (name, e)


def _real_click(wechat, gui, rel_x: int, rel_y: int) -> tuple:
    """**真鼠标**点一下就回来（用完立刻把光标还原到原位）。

    ⚠️ **旧结论已作废（15:47 实测推翻）**：本函数原来的 docstring 写
    「微信输入区那两个控件（"进录音态的圆圈"和录音态里的绿色发送）**对投递点击只出悬停高亮、
    不进录音态**」——**这是错的**（至少对**独立窗**不成立）。实测（独立窗"文件传输助手"，
    可见但**不在前台**）：**投递**点语音切换键 → 进"按住说话"态；**投递**点录音圆圈 → **进录音态**
    （✕+音量点+绿↑ 全出现）；播音频进虚拟麦；**投递**点绿↑ → 发送成功、库回读新语音条
    （`server_id`＋`voiceurl`），pilk 解码确认是真实音频。⇒ **投递路可行，且不需要前台。**
    ⇒ 真鼠标**不再是唯一路**；本函数保留作"真鼠标档"（配置 `voice_strip.real_click=true` 时用）。

    为什么仍保留真鼠标档：
      ① 真鼠标在**主窗**上的行为可能与独立窗不同（旧注释可能来自主窗的某次测试，未逐一复测）；
      ② 真鼠标是①③两步都验过的老路，留作投递失败时的兜底。
    ⇒ 它**必须显式开启**（`voice_strip.real_click`，默认 true，控制台写明"会动两下光标"），
      且用完**立刻把光标放回原位**并在日志/返回里说明（不许悄悄动）。

    实现落在 `ui_adapt.click_real_hold`（L0 唯一下沉点）：那边动手前会过 `real_guard`
    （确认这点真属于微信，用户正在用鼠标导致光标没到位就**不打这一枪**），并负责把光标放回去。
    """
    try:
        from . import ui_adapt
    except Exception as e: # pragma: no cover
        return False, "拿不到 ui_adapt：%s" % e
    return ui_adapt.click_real_hold(gui, int(rel_x), int(rel_y))


def _green_center(gui):
    """在画面里找那个**绿色发送**簇的中心（渲染相对像素）⇒ 录音态里的发送按钮，比硬编码比例稳。"""
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        w, h = img.size
        img = img.crop((int(w * 0.55), int(h * 0.80), w, h)).convert("RGB")
        px = img.load()
        bw, bh = img.size
        xs, ys = [], []
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                rr, gg, bb = px[x, y]
                if gg > rr + 25 and gg > bb + 25 and gg > 90:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 10:
            return None
        return (int(w * 0.55) + sum(xs) // len(xs), int(h * 0.80) + sum(ys) // len(ys))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _click(wechat, gui, rel_x: int, rel_y: int, cfg=None) -> tuple:
    """点一下：默认走**真鼠标**（投递对这俩控件无效，见 `_real_click`）；配置关掉才用投递。"""
    c = _cfg(cfg)
    if bool(c.get("real_click", True)):
        return _real_click(wechat, gui, rel_x, rel_y)
    try:
        return wechat._click(gui, int(rel_x), int(rel_y))
    except Exception as e:
        return False, "投递点击失败：%s" % e


def _green_cluster(gui):
    """输入区右半边有没有**绿色簇**（进录音态后那个绿色↑发送）⇒ 用它当"进没进录音态"的判据。

    返回**三态**（`None` 是必需的第三种，别把它折回 `False`）：
      · `True`  —— 有绿簇：判据正面，确实进了录音态；
      · `False` —— **读到了帧、且确认没有绿簇**：判据正面，确实没进录音态；
      · `None`  —— **判不了**（抓不到帧 / 量图异常）。

    ⛔ **抓不到帧时必须返回 `None`，不许返回 `False`**：那会把"看不见"说成"没进录音态"，
      调用方据此松开按键换位置重试，最后报"按住右 Alt 也没进录音态"——而真相是从头到尾
      没看到画面（最小化/被遮挡时帧只是标题条桩）。**"判不了"与"不是"是两件事。**
    """
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        w, h = img.size
        band = img.crop((int(w * 0.55), int(h * 0.86), w, h)).convert("RGB")
        px = band.load()
        bw, bh = band.size
        hits = 0
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    hits += 1
        return hits >= 12
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _meter_level(gui) -> int:
    """录音态里那串**绿色音量点**亮了多少像素 ⇒ 判"音频到底有没有进微信的麦克风"。

    为什么要有它：整条链能"发出一个语音条"，但**没人能保证条里有声音**——
    虚拟声卡没被微信选成麦克风时，发出去的就是一条静音语音条（比文件更糟：对方点开什么都听不到，
    而我们自己还报"成功"）。这条读数让产品能**在发之前**发现并如实拒发。
    """
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return 0
        w, h = img.size
        box = (int(w * METER_X[0]), int(h * METER_Y[0]), int(w * METER_X[1]), int(h * METER_Y[1]))
        band = img.crop(box).convert("RGB")
        px = band.load()
        bw, bh = band.size
        n = 0
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    n += 1
        return n
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0


def _green_send(gui):
    """录音态里那个**绿色发送 ↑**的坐标（渲染相对像素）。

    取**最右**的绿簇，不用全部绿像素的均值——左边那串音量点也是绿的，均值会被它带偏
    （实测均值落在 (1112,831)＝比例 0.934，而 ↑ 与音量点混在一张图里时这个均值不稳）。
    """
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        w, h = img.size
        band = img.crop((int(w * GREEN_X_MIN), int(h * 0.84), w, h)).convert("RGB")
        px = band.load()
        bw, bh = band.size
        xs, ys = [], []
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 8:
            return None
        return (int(w * GREEN_X_MIN) + sum(xs) // len(xs), int(h * 0.84) + sum(ys) // len(ys))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _play_with_meter(gui, wav: str, out_name: str = "") -> tuple:
    """**边播边量**音量点 ⇒ (播放结果, 播放前底色, 播放中峰值)。"""
    import threading
    res = {}

    def _run():
        try:
            res["r"] = play_to_cable(wav, out_name)
        except Exception as e:
            res["r"] = (False, "播放异常：%s" % e)

    base = _meter_level(gui)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    peak = 0
    while t.is_alive():
        peak = max(peak, _meter_level(gui))
        time.sleep(0.15)
    t.join(timeout=8.0)
    return res.get("r", (False, "播放线程没有返回")), base, peak


def _enter_record(wechat, gui, cfg=None, rw: int = 0, rh: int = 0, wait_s: float = 8.0) -> tuple:
    """进录音态：**逐个候选位置真点**，谁点出绿簇就是它 ⇒ (ok, 说明, x比例)。

    候选顺序＝扫图标行现算 → 兜底表（全 ≥0.8，点空/点发送都无害）。

    ⚠️ 的关键一环（时钉死）：
    `ui_adapt.click_real_hold` 动手前要过 `real_guard`，而**用户正在用鼠标时 `SetCursorPos`
    会返回 0**（Windows 层面拒绝）⇒ guard 按红线**不打这一枪**。
    于是"点了却没反应"的真实原因往往是"**那一枪压根没打出去**"。⇒ 这里**等一等再试**：
    被 guard 拦下时每隔 0.6s 重试一次，最多等 `wait_s` 秒（用户手一离开鼠标，下一枪就能中）；
    只有"打出去了但没进态"才换下一个候选位置。
    """
    c = _cfg(cfg)
    rb = c.get("record_btn") or [0.878, 0.943]
    try:
        ry = int(rh * float(rb[1]))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        ry = int(rh * 0.943)
    x_scan, src = locate_record(gui, cfg)
    cands = [x_scan] + [x for x in RECORD_FALLBACKS if abs(x - x_scan) > 0.01]
    why, fired_never = [], True
    blind = 0   # 抓到了"判不了"（而不是"没有绿簇"）的次数
    for x in cands:
        t0 = time.time()
        shot = 0
        while True:
            shot += 1
            try:
                ok, why1 = _click(wechat, gui, int(rw * x), ry, c)
            except Exception as e:
                ok, why1 = False, "点击异常：%s" % str(e)[:40]
            time.sleep(1.0)
            _gc = _green_cluster(gui)
            if _gc:
                tag = src if abs(x - x_scan) < 1e-9 else "兜底候选"
                if shot > 1:
                    tag += "·第%d枪才中" % shot
                return True, "在 x=%.3f 进录音态（%s）" % (x, tag), x
            if _gc is None:
                blind += 1
            if ok:
                fired_never = False
                # 打出去了但没进态：同一位置再补一枪（自绘控件偶发丢第一下），仍不进就换候选
                if shot >= 2:
                    why.append("x=%.3f 打了两枪没反应" % x)
                    break
                continue
            # 没打出去（多半是 real_guard：你正在用鼠标）⇒ 等一等再试同一个位置
            if time.time() - t0 >= float(wait_s):
                why.append("x=%.3f 一直没能打出去（%s）" % (x, str(why1)[:70]))
                break
            time.sleep(0.6)
    tail = "（这几枪都被防打扰闸拦下了：你正在用鼠标 ⇒ 按红线不打；手一离开鼠标我就重试）" if fired_never else ""
    blind_tail = ("；另有 %d 次**抓不到画面**（判不了，不等于'没进态'）" % blind) if blind else ""
    return False, "候选位置都没进录音态：%s%s%s" % ("；".join(why), blind_tail, tail), x_scan


class _InvisibleShown:
    """**把独立窗"显示在它自己的原位、但全透明"**的上下文管理器，`finally` 精确还原。

    ⭐⭐ **为什么它比"移到屏外显示"更好**（实测对比，同一条链、同一台机）：
      · **移到屏外显示** ⇒ 投递的点击**真落到微信控件上**时，**微信自己会激活窗口**
        （`Weixin.dll` 导入表里有 `SetForegroundWindow`），而独立窗是主窗的 **owned window**
        ⇒ 被顶到最前的是**屏内的主窗**（用户看得见，实测约 7 秒）⇒ 违反判据①。
      · **显示在原位 + `WS_EX_LAYERED` + alpha=0** ⇒ 被激活的是**那个看不见的独立窗**
        ⇒ 实测**主窗全程没有到前台**（三次采样 `fg` 都没变），而点击**照样生效**
        （同一枪进了录音态、`_popup_find_green` 拿到绿↑ `(1281,1394)`）。
      · 判据①「不影响观看」仍然成立 —— 项目早先量过：全屏抓图那块区域是**后面的窗口**
        （`WS_EX_LAYERED` + alpha=0 ⇒ 一个像素都不画）。
      · 判据②「不影响操作」也变好了：**不再需要**每次点击后去抢回前台（原先要抢回来）。

    ⛔ **进入顺序**：**先设透明、再显示** —— 反了会露一帧（用户会看到窗口闪一下）。
    ⛔ **还原顺序与进入相反**：**先收成最小化、再去透明位、最后摆回原位置**
      ——反了会在屏上闪一下。
    ⛔ **透明位必须还原干净**：留在窗上 ⇒ 用户的独立窗"一直看不见"，比留在屏外更难发现。
      还原后读回扩展样式校验，仍在就再试一次（`set_popup_invisible(False)` 是幂等的）。
    ⛔ 自检不过（读回 alpha != 0 / 尺寸异常 / 显示失败）⇒ **不投递**，调用方走兜底。
    """

    def __init__(self, hwnd: int, *, log=None):
        self.hwnd = int(hwnd or 0)
        self.log = log
        self.shown = False
        self.why = ""
        self._wp0 = None
        self._nr = None
        self._nw = self._nh = 0

    def __enter__(self):
        if not self.hwnd:
            self.why = "没给窗口句柄"
            return self
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            u = _ct.windll.user32
            # ① 记原状
            self._wp0 = _get_window_placement(self.hwnd)
            if self._wp0 is None:
                self.why = "拿不到窗口原状（GetWindowPlacement 失败）"
                return self
            nr = self._wp0["rcNormalPosition"]
            self._nr = nr
            self._nw = int(nr[2] - nr[0])
            self._nh = int(nr[3] - nr[1])
            if self._nw <= 100 or self._nh <= 100:
                self.why = "窗口尺寸异常（%dx%d）" % (self._nw, self._nh)
                return self
            # ② **先设透明**（再显示）
            try:
                from . import wechat_windows as _ww
                r = _ww.set_popup_invisible(self.hwnd, True, alpha=0)
                if not r.get("ok"):
                    self.why = "设全透明失败：%s" % str(r.get("detail"))[:70]
                    return self
            except Exception as e:  # noqa: BLE001
                self.why = "设全透明异常：%s" % str(e)[:70]
                return self
            # ③ 读回 alpha 自检：`GetLayeredWindowAttributes` 的 alpha==0 ⇒ 完全透明
            _alpha = _wt.BYTE()
            _crk, _flag = _wt.DWORD(), _wt.DWORD()
            _got = u.GetLayeredWindowAttributes(_ct.c_void_p(self.hwnd), _ct.byref(_crk),
                                               _ct.byref(_alpha), _ct.byref(_flag))
            if not _got or int(_alpha.value) != 0:
                self.why = "透明自检不过（读回 alpha=%s）⇒ 中止" % int(_alpha.value)
                self._restore()
                return self
            # ④ **显示在它自己的原位**（不激活）
            u.ShowWindow(_wt.HWND(self.hwnd), _SHOW_NOACTIVATE)
            u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0), int(nr[0]), int(nr[1]),
                           self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
            time.sleep(0.25)
            rc = _window_rect(self.hwnd)
            if not rc or bool(u.IsIconic(_wt.HWND(self.hwnd))):
                self.why = "显示失败（rect=%s）⇒ 中止" % (rc,)
                self._restore()
                return self
            self.shown = True
            _LIVE_BOXES.add(self)
            _arm_atexit()
            if self.log:
                try:
                    self.log("[语音条] 最小化档：已切成'全透明显示'rect=%s（用户看不到，主窗不会被顶到前台）"
                             % (rc,))
                except Exception:  # noqa: BLE001
                    pass
            return self
        except Exception as e:  # noqa: BLE001
            self.why = "全透明显示异常：%s" % str(e)[:70]
            try:
                self._restore()
            except Exception:  # noqa: BLE001
                pass
            return self

    def _restore(self):
        """按**原状**还原：先藏（最小化）→ 去透明位 → 摆回原位置/原显示态 → 校验透明位已清。"""
        if self._wp0 is None or not self.hwnd:
            return False
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            u = _ct.windll.user32
            sc = int(self._wp0.get("showCmd") or 0)
            # ① **先藏起来**（此刻还带着透明位 ⇒ 这一步用户完全看不到）
            u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWMINNOACTIVE)
            time.sleep(0.10)
            # ② **再去透明位**（顺序不能反：反了会在屏上闪一下）
            try:
                from . import wechat_windows as _ww
                _ww.set_popup_invisible(self.hwnd, False)
            except Exception:  # noqa: BLE001
                pass
            # ③ 摆回原位置 + 原显示态
            u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0),
                           int(self._nr[0]), int(self._nr[1]),
                           self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
            if sc == _SW_SHOWMAXIMIZED:
                u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWMAXIMIZED)
            elif sc not in (_SW_SHOWMINIMIZED, _SW_SHOWMINNOACTIVE):
                u.ShowWindow(_wt.HWND(self.hwnd), _SHOW_NOACTIVATE)
            time.sleep(0.10)
            # ④ 校验：透明位必须已清掉（留着 ⇒ 用户的窗口"一直看不见"，比留在屏外更难发现）
            try:
                _ex = u.GetWindowLongW(_wt.HWND(self.hwnd), -20) & 0xFFFFFFFF
                if _ex & 0x00080000:                 # WS_EX_LAYERED 还在 ⇒ 再清一次（幂等）
                    log.warning("全透明还原：扩展样式里 LAYERED 仍在 ⇒ 再清一次")
                    from . import wechat_windows as _ww2
                    _ww2.set_popup_invisible(self.hwnd, False)
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception:
            return False

    def __exit__(self, *exc):
        if self.shown:
            self._restore()
            self.shown = False
        _LIVE_BOXES.discard(self)
        return False

    def release_early(self):
        """提前还原（三动作之间不需要一直占着透明态）。"""
        if self.shown:
            self._restore()
            self.shown = False
            _LIVE_BOXES.discard(self)


#: 两个"人看不见"上下文管理器共用的常量（各自模块内还留着自己的旧名，别混用）。
_SHOW_NOACTIVATE = 4


class _OffscreenShown:
    """**把窗口短暂移到屏幕外显示**（用户看不到）的上下文管理器，`finally` **精确还原**。

    ⭐⭐ **为什么需要**（实测钉死）：
      · **窗口真最小化（`IsIconic`）** ⇒ Windows **屏蔽最小化窗口的鼠标消息**
        ⇒ 光投递 `WM_*` **无效**（A/B/C 三种投法实测：借前台、SendMessage 都不行）；
      · **窗口脱离最小化**（哪怕不在前台） ⇒ 投递**立刻生效**（可见档早已实测发成功）。

    ⭐⭐ **为什么"屏外"是合法的**（的两条判据）：
      ① **不影响用户的观看** ⇒ 窗口 rect 移到**屏幕矩形之外**（无交集）⇒ 一个像素都不上屏；
      ② **不影响用户的操作** ⇒ 不调 `SetForegroundWindow`/`BringWindowToTop`（不抢焦点）、
         不动光标、不注入按键；**用完精确还原**（原位置 + 原 showCmd）。
      ⛔ 与"借前台"的区别：那是**抢前台**；这里是"**移到屏外显示**"——前台**从头到尾不变**。
      ⛔ 与"`SW_SHOWNOACTIVATE` 上屏"的区别：那个会把窗口显示到**屏幕内**（用户看得到）⇒ 禁止。

    **还原纪律**（状态变更类操作铁律）：先逐字段记原状（`showCmd` + `rcNormalPosition`），
    `finally` 按**原状**还原（不是按"。
    """

    def __init__(self, hwnd: int, *, log=None):
        self.hwnd = int(hwnd or 0)
        self.log = log
        self.shown = False
        self._wp0 = None
        self._nr = None
        self._nw = self._nh = 0
        self._off_x = self._off_y = 0
        self.why = ""

    def __enter__(self):
        if not self.hwnd:
            self.why = "没给窗口句柄"
            return self
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            u = _ct.windll.user32
            # ① 记原状
            self._wp0 = _get_window_placement(self.hwnd)
            if self._wp0 is None:
                self.why = "拿不到窗口原状（GetWindowPlacement 失败）"
                return self
            nr = self._wp0["rcNormalPosition"]
            self._nr = nr
            self._nw = int(nr[2] - nr[0])
            self._nh = int(nr[3] - nr[1])
            if self._nw <= 100 or self._nh <= 100:
                self.why = "窗口尺寸异常（%dx%d）" % (self._nw, self._nh)
                return self
            sw = int(u.GetSystemMetrics(0) or 0)
            sh = int(u.GetSystemMetrics(1) or 0)
            # ② 移到一个**与屏幕矩形无交集**的位置：
            #    取"窗口整个落在屏幕左边之外"，y 给负值（屏幕上方之外）双保险。
            # ⛔ **必须按"虚拟屏"（含多显示器）的原点算**：只按主屏 `(0,0)` 算的话，
            #    "主屏左侧还有一块屏"的机器上会停到**那块屏上**（用户看得见 ⇒ 判据①失效）。
            _vx, _vy, _vw, _vh = _virtual_screen(u)
            sw, sh = _vw, _vh
            self._off_x = int(_vx) - (self._nw + _OFFSCREEN_MARGIN)
            self._off_y = int(_vy) - (self._nh + _OFFSCREEN_MARGIN)
            # ③ ⭐⭐ **顺序与间隔都是实测钉死的**：
            #    · "先移后显"**不行**：窗口最小化时 `SetWindowPos` **只改 `rcNormalPosition`、
            #      不改占位 rect** ⇒ 移不动（实测移完 rect 仍是原位置，随后显示就露在屏幕内）；
            #    · `SetWindowPlacement` 改 `rcNormalPosition` 也**不行**：它**会自己触发显示**，
            #      且位置由系统重摆（实测落到 `(0,0)`）。
            #    ⇒ 唯一可行＝`ShowWindow` 后**立刻**（**中间绝不加 sleep**）`SetWindowPos` 移到位。
            #    ⭐ **为什么这样就不露**：Windows 的窗口显示是**异步的**（要等重绘消息泵），
            #      两次调用仅隔 3~6ms ⇒ 窗口**还没来得及绘制就被移走**。
            #      忙等采样实测：**"落在屏幕内"的采样 0 / 约 11 万次**（5 轮，采样间隔 ~0.5µs）。
            #      ⛔ **不许**在这两步之间插任何 `time.sleep` —— 插了窗口就会真的画出来
            #        （这正是此前"抓到 1~2 次屏内采样"的原因：旧代码在中间 sleep 了 0.25s）。
            u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWNOACTIVATE)
            u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0), self._off_x, self._off_y,
                           self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
            time.sleep(0.25)
            # ④ 自检：**必须真的在屏幕外 + 没抢前台**
            rc = _window_rect(self.hwnd)
            if not _rect_off_screen(rc, sw, sh, _vx, _vy):
                self.why = "移屏外失败（rect=%s 与屏幕 %dx%d 有交集）⇒ 中止" % (rc, sw, sh)
                self._restore()
                return self
            self.shown = True
            # ⭐ **登记到退出兜底**（见 `_LIVE_BOXES` 的注释）：进程若在"还原之前"退出，
            #    `atexit` 会把窗口还回去 —— 不留"窗口卡在屏外、用户切不回来"的死态。
            _LIVE_BOXES.add(self)
            _arm_atexit()
            if self.log:
                try:
                    self.log("[语音条] 最小化档：已移到屏外显示 rect=%s（用户看不到）" % (rc,))
                except Exception:
                    pass
            return self
        except Exception as e:  # noqa: BLE001
            self.why = "屏外显示异常：%s" % str(e)[:70]
            try:
                self._restore()
            except Exception:
                pass
            return self

    def _restore(self):
        """按**原状**还原（原位置 + 原显示态），还原后再读一次确认。"""
        if self._wp0 is None or not self.hwnd:
            return False
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            u = _ct.windll.user32
            sc = int(self._wp0.get("showCmd") or 0)
            # ⭐ **顺序与进入时相反、同样不能反**：
            #    先把窗口**变回原显示态**（最小化的继续最小化 —— 最小化时窗口本来就不上屏），
            #    再把它**移回原位置**（此时最小化窗口的 rect 是无意义的哨兵值，摆哪都不上屏）。
            #    ⛔ 反过来做（先移回原位置、再最小化）⇒ 中间的几毫秒窗口**在原位置显示着**
            #       ⇒ 用户会看到它闪一下（同类缺陷，见 `__enter__` 的注释）。
            if sc == _SW_SHOWMINIMIZED or self._wp0.get("iconic"):
                # ⛔ 中间不许 sleep（同 `__enter__`：一 sleep 窗口就真画出来了）
                #    最小化 + 移回原位置：最小化时窗口不再渲染 ⇒ 顺序无所谓，紧邻即可。
                # ⛔⛔ 这里**只能**用 `_SW_SHOWMINNOACTIVE`(7)：换成 `SW_MINIMIZE`(6) 会
                #    "最小化**并激活下一个顶层窗**" ⇒ 独立窗的下一个就是它的 owner ＝**微信主窗**
                #    ⇒ 用户会看到主窗被顶到前台（他没做任何操作）。详见常量区那段。
                u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWMINNOACTIVE)
                u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0),
                               int(self._nr[0]), int(self._nr[1]),
                               self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
                time.sleep(0.10)
            elif sc == _SW_SHOWMAXIMIZED:
                u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0),
                               int(self._nr[0]), int(self._nr[1]),
                               self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
                u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWMAXIMIZED)
                time.sleep(0.10)
            elif sc == _SW_SHOWMINNOACTIVE:
                u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWMINNOACTIVE)
                u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0),
                               int(self._nr[0]), int(self._nr[1]),
                               self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
                time.sleep(0.10)
            else:
                u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0),
                               int(self._nr[0]), int(self._nr[1]),
                               self._nw, self._nh, _SWP_NOZORDER | _SWP_NOACTIVATE)
                u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWNOACTIVATE)
                time.sleep(0.10)
            # ⛔⛔ **还原之后必须校验**（本条的由来：曾经因为某条异常分支跳过了还原，
            #     把用户的独立窗**留在屏幕外**，从任务栏也拉不回来，只能关掉重开）。
            #     判据＝窗口要么**在屏内**、要么**是最小化**（最小化时不上屏）⇒ 二者都不是才算失败。
            try:
                _rc2 = _window_rect(self.hwnd)
                _vx2, _vy2, _sw2, _sh2 = _virtual_screen(u)
                if _rc2 and (not _rect_off_screen(_rc2, _sw2, _sh2, _vx2, _vy2)):
                    return True                     # 在屏内 ⇒ 好
                if bool(u.IsIconic(_wt.HWND(self.hwnd))):
                    return True                     # 最小化 ⇒ 也不上屏，用户可以自己还原
                # 两种都不是 ⇒ **兜住**：先收成最小化（用户看不到），再摆回屏内一角
                # （收窗口同样只能用 `_SW_SHOWMINNOACTIVE`(7)，理由见 `_restore` 上半段）
                u.ShowWindow(_wt.HWND(self.hwnd), _SW_SHOWMINNOACTIVE)
                u.SetWindowPos(_wt.HWND(self.hwnd), _wt.HWND(0),
                               max(_vx2, _vx2 + _sw2 // 5), max(_vy2, _vy2 + _sh2 // 5),
                               int(self._nw or 600), int(self._nh or 800),
                               _SWP_NOZORDER | _SWP_NOACTIVATE)
                time.sleep(0.08)
                log.warning("屏外还原兜底：窗口仍在屏外 ⇒ 已收成最小化并摆回屏内一角")
                return True
            except Exception:  # noqa: BLE001
                return True
        except Exception:
            return False
        finally:
            # ⛔ **还原了就立刻注销**（不只靠 `__exit__` / `release_early` 记得清）：
            #   `offscreen_active()` 是"出屏自愈让路"的唯一依据；窗口已经还原却还挂在登记里，
            #   那条自愈就会**永远不敢再动这个窗口**（万一它真被挪到屏外，就没人救了）。
            #   收口在这里 ⇒ 无论谁触发还原，登记与真实状态都不会不一致。
            _LIVE_BOXES.discard(self)

    def __exit__(self, *exc):
        if self.shown:
            self._restore()
            self.shown = False
        _LIVE_BOXES.discard(self)
        return False

    def release_early(self):
        """提前还原（三动作之间不需要一直占着屏外位置）。"""
        if self.shown:
            self._restore()
            self.shown = False
            _LIVE_BOXES.discard(self)


def _get_window_placement(hwnd: int):
    """`GetWindowPlacement` ⇒ `{showCmd, iconic, rcNormalPosition}` 或 None。"""
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt

        class POINT(_ct.Structure):
            _fields_ = [("x", _ct.c_long), ("y", _ct.c_long)]

        class RECT(_ct.Structure):
            _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                        ("right", _ct.c_long), ("bottom", _ct.c_long)]

        class WINDOWPLACEMENT(_ct.Structure):
            _fields_ = [("length", _ct.c_uint), ("flags", _ct.c_uint), ("showCmd", _ct.c_uint),
                        ("ptMinPosition", POINT), ("ptMaxPosition", POINT),
                        ("rcNormalPosition", RECT)]

        u = _ct.windll.user32
        wp = WINDOWPLACEMENT()
        wp.length = _ct.sizeof(WINDOWPLACEMENT)
        if not u.GetWindowPlacement(_wt.HWND(int(hwnd)), _ct.byref(wp)):
            return None
        r = wp.rcNormalPosition
        return {"showCmd": int(wp.showCmd), "iconic": bool(u.IsIconic(_wt.HWND(int(hwnd)))),
                "rcNormalPosition": (r.left, r.top, r.right, r.bottom)}
    except Exception:
        return None


def _set_window_placement(hwnd: int, show_cmd: int, rc_normal) -> bool:
    """`SetWindowPlacement` ⇒ 改**显示态 + `rcNormalPosition`**（`_get_window_placement` 的反向）。

    ⛔ 为什么必须有它：`put_window_onscreen()` 要**同时修 `rcNormalPosition``
      —— 否则"最小化还原 / 从任务栏还原"还会把窗口送回屏外（那个屏外位置会被写成 normal
      ⇒ 点任务栏都往外飞）。本模块原来只有 `_get_window_placement`、**没有 set 版**，
      直接调一个不存在的名字会 `AttributeError`，而它会被外层 `except` 吞掉
      ⇒ **窗口根本不会被挪**（"静默不生效"，比报错更坏）。
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt

        class POINT(_ct.Structure):
            _fields_ = [("x", _ct.c_long), ("y", _ct.c_long)]

        class RECT(_ct.Structure):
            _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                        ("right", _ct.c_long), ("bottom", _ct.c_long)]

        class WINDOWPLACEMENT(_ct.Structure):
            _fields_ = [("length", _ct.c_uint), ("flags", _ct.c_uint), ("showCmd", _ct.c_uint),
                        ("ptMinPosition", POINT), ("ptMaxPosition", POINT),
                        ("rcNormalPosition", RECT)]

        u = _ct.windll.user32
        wp = WINDOWPLACEMENT()
        wp.length = _ct.sizeof(WINDOWPLACEMENT)
        if not u.GetWindowPlacement(_wt.HWND(int(hwnd)), _ct.byref(wp)):
            return False
        wp.showCmd = int(show_cmd)
        wp.rcNormalPosition = RECT(int(rc_normal[0]), int(rc_normal[1]),
                                   int(rc_normal[2]), int(rc_normal[3]))
        return bool(u.SetWindowPlacement(_wt.HWND(int(hwnd)), _ct.byref(wp)))
    except Exception:  # noqa: BLE001
        return False


def _window_rect(hwnd: int):
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        r = _wt.RECT()
        u.GetWindowRect(_wt.HWND(int(hwnd)), _ct.byref(r))
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def put_window_onscreen(hwnd, *, size=None, log=None) -> dict:
    """把**停在屏幕外**的窗口放回屏内（**幂等 · 不抢前台 · 也修 `rcNormalPosition`**）。

    ⛔ **为什么必须有这个独立入口**：把窗口移到屏外之后，若没走 `_OffscreenShown` 的还原出口，
      窗口会停在"屏外且 `visible=1, iconic=0`"这种状态，而这种状态**谁也救不回来**：
      · `WeChatAdapter._ensure_main_visible()` 只认**最小化 / 隐藏**两态
        ⇒ 对**屏外但可见**什么都不做（它直接 `return False`）；
      · 从任务栏点它 ⇒ 它按 `rcNormalPosition` 走，而那个值可能**也被写成了屏外** ⇒ 又飞出去。
      ⭐ `_OffscreenShown._restore()` 本身**已经有**"还原后校验：不在屏内也不是最小化 ⇒ 兜住搬回屏内"
        的守卫 ⇒ 所以正确做法始终是走它的还原出口；本入口是**事后救回**的正式通道
        （以前只能人肉算坐标）。
    ⭐ **幂等**：窗口与屏幕**有交集**、且 `rcNormalPosition` 也在屏内 ⇒ 什么都不做（`moved=False`）。
    ⭐ **同时修 `rcNormalPosition`**：否则"最小化还原 / 任务栏还原"还会把它送出去。
    ⭐ **置前与否由前台闸决定**：本函数**只用不激活API**（`SWP_NOZORDER|SWP_NOACTIVATE`、
      `SW_SHOWNOACTIVATE`）；`fg_allowed()` 是"现在允不允许把窗口**置前/置顶**"的唯一真源，
      这里查它是为了**决定要不要额外做激活**——默认（后台档）**不激活**，所以救回动作不会打扰用户。
    """
    out = {"moved": False, "why": "", "before": None, "after": None, "activate_allowed": False}
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        hwnd = _wt.HWND(int(hwnd or 0))
        if not hwnd.value:
            out["why"] = "没给窗口句柄"
            return out
        # 前台闸（唯一真源）：它的结论只影响"要不要额外激活"，**不影响**"能不能搬回屏内"。
        try:
            from . import ui_adapt as _ua
            _ok_fg, _why_fg = _ua.fg_allowed()
            out["activate_allowed"] = bool(_ok_fg)
        except Exception as _e:  # noqa: BLE001
            out["activate_allowed"] = False
            out["why"] = "前台闸读不到（按拒绝处理）：%s " % str(_e)[:40]
        rc = _window_rect(int(hwnd.value))
        out["before"] = rc
        vx, vy, vw, vh = _virtual_screen(u)
        _wp = _get_window_placement(int(hwnd.value)) or {}
        _nr = _wp.get("rcNormalPosition") or ()
        _nr_off = bool(_nr) and _rect_off_screen((_nr[0], _nr[1], _nr[2], _nr[3]), vw, vh, vx, vy)
        if rc and (not _rect_off_screen(rc, vw, vh, vx, vy)) and not _nr_off:
            out["why"] += "窗口与屏幕有交集且 rcNormalPosition 也在屏内 ⇒ 不需要动（幂等）"
            out["after"] = rc
            return out
        w = int(size[0]) if size else max(600, (rc[2] - rc[0]) if rc else 900)
        h = int(size[1]) if size else max(500, (rc[3] - rc[1]) if rc else 800)
        w, h = min(w, int(vw)), min(h, int(vh))
        tx, ty = int(vx) + max(0, (int(vw) - w) // 8), int(vy) + max(0, (int(vh) - h) // 8)
        _SWP = _SWP_NOZORDER | _SWP_NOACTIVATE
        try:
            u.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        except Exception:  # noqa: BLE001
            pass
        u.SetWindowPos(hwnd, _wt.HWND(0), tx, ty, w, h, _SWP)
        if _wp and _nr:
            _set_window_placement(int(hwnd.value), int(_wp.get("showCmd") or 1), (tx, ty, tx + w, ty + h))
        u.SetWindowPos(hwnd, _wt.HWND(0), tx, ty, w, h, _SWP)
        try:
            u.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        except Exception:  # noqa: BLE001
            pass
        rc2 = _window_rect(int(hwnd.value))
        out["after"] = rc2
        out["moved"] = True
        if (not rc2) or _rect_off_screen(rc2, vw, vh, vx, vy):
            if not bool(u.IsIconic(hwnd)):
                out["why"] += "⛔ 摆回屏内后自检**未通过**（rect=%s）" % (rc2,)
        if log:
            try:
                log("把窗口放回屏内：%s → %s（moved=%s）" % (out["before"], rc2, out["moved"]))
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception as _e:  # noqa: BLE001
        out["why"] = "放回屏内异常：%s" % str(_e)[:70]
        return out


def _rect_off_screen(rc, sw: int, sh: int, ox: int = 0, oy: int = 0) -> bool:
    """窗口矩形与**屏幕矩形**无交集 ⇒ True（用户看不到任何像素）。

    `ox` / `oy` ＝屏幕矩形的**原点**：单显示器传默认 `(0,0)`；多显示器要传**虚拟屏原点**
    （否则"主屏左边那块屏"上的窗口会被误判成"看不见"，判据①当场失效）。
    """
    if not rc:
        return False
    l, t, r, b = rc
    if r <= int(ox) or l >= int(ox) + int(sw) or b <= int(oy) or t >= int(oy) + int(sh):
        return True
    return False


def _virtual_screen(u=None) -> tuple:
    """虚拟屏（**含多显示器**）⇒ `(x, y, w, h)`；取不到退主屏。

    用途：`_OffscreenShown` 要挑一个"用户绝对看不见"的位置 —— 只按主屏原点算的话，
    "主屏左侧还有一块屏"的机器上会把窗口停到**那块屏上**。
    """
    try:
        if u is None:
            import ctypes as _ct
            u = _ct.windll.user32
        x, y = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))
        w, h = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
        if w > 0 and h > 0:
            return x, y, w, h
        return 0, 0, int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 0, 0, 1920, 1080


#: **仍"显示在屏外"**的 `_OffscreenShown` 实例。**退出兜底**用：
#: 进程正常退出（含未捕获异常 ⇒ `atexit` 仍会跑）时把它们逐个还原。
#: ⚠️ 为什么必须有：一旦"屏外显示"没等到还原，窗口就停在屏外且 `iconic=False`
#:   ⇒ 表现是"窗口不见了、从任务栏点它还往外飞"，只能关掉重开。
#: ⛔ 它盖不住"被 `TerminateProcess` 硬杀"；那种情况由 `find_popup` 的自愈兜住。
_LIVE_BOXES = set()
_ATEXIT_ARMED = [False]


def _restore_live_boxes():
    """把还挂着的"人看不见"上下文逐个还原（`atexit` 钩子）。"""
    for _b in list(_LIVE_BOXES):
        try:
            _b._restore()
        except Exception:  # noqa: BLE001
            pass  # 清理型：静默合法
        _LIVE_BOXES.discard(_b)


def offscreen_active(hwnd: int) -> bool:
    """这个窗口**此刻是不是被我们故意摆在屏幕外**（`_OffscreenShown` 还挂着）⇒ 布尔。

    为什么要有这个查询口：别处另有一条"**窗口跑到屏幕外就自动拉回来**"的自愈逻辑
    （`ui_adapt.prepare_screen()` 里按 rect 判"出屏"的那两个分支）——
    它在**我方故意**把窗口摆到屏外时**同样会命中**，后果是双重的：
      ① **屏外档当场被破坏**：屏外档的全部意义就是"显示着但不上屏"（投递的前提），
         被拉回屏内 ⇒ 接下来的投递点击按屏外坐标算 ⇒ 落点全错；
      ② 窗口会在**用户眼前**跳到 (90,90)/(180,120) 并被激活
         （那两处用的是 `ShowWindow(hwnd, 9)` ＝ `SW_RESTORE`，**会激活窗口**）。
    ⇒ 那两处挪窗口之前**必须先问这一句**：是我方自己摆的 ⇒ **一个字都不许动**。

    ⚠️ 只按窗口句柄判：屏外档是**本进程内**的临时状态，跨进程问不到（也不该问）。
    ⚠️ 只读、不抛：拿不到就回 False（调用方按"不是我们摆的"处理，与改动前一致）。
    """
    try:
        _h = int(hwnd or 0)
        if not _h:
            return False
        return any(int(getattr(_b, "hwnd", 0) or 0) == _h for _b in list(_LIVE_BOXES))
    except Exception:  # noqa: BLE001
        return False


def _arm_atexit():
    """装一次退出兜底（幂等）：进程在还原前退出时，把还挂着的窗口状态收回去。"""
    if _ATEXIT_ARMED[0]:
        return
    try:
        import atexit as _ae
        _ae.register(_restore_live_boxes)
        _ATEXIT_ARMED[0] = True
    except Exception:  # noqa: BLE001
        pass


def _posted_click(hwnd: int, cx: int, cy: int, *, ensure_live: bool = True) -> bool:
    """把一次鼠标点击**投递**给 `hwnd`（客户区坐标）——不动光标、不抢前台、不上屏。

    为什么自己拼消息（而不走 `backend.click`）：`backend.click` 收的是**屏幕坐标**、
    内部 `ScreenToClient` 换算。而**最小化**的窗口在离屏哨兵 `(-32000,...)` 上，
    屏幕坐标根本给不出来 ⇒ 那条换算对最小化档失效。
    ⇒ 这里直接给**客户区坐标**（＝用窗口自身客户区尺寸算的比例位置），任何窗口态都成立。

    ⭐⭐ **最小化档的投递前提**（实测，A/B/C/D/E 五组对照）：
      · **窗口最小化（`IsIconic`）** ⇒ Windows **屏蔽鼠标消息** ⇒ 裸投递**无效**；
        `AttachThreadInput` 借前台（B）、`SendMessage`（C）**同样无效**；
      · **窗口脱离最小化** ⇒ 投递**立刻生效**（可见档、屏外档都实测成功）；
      ⇒ 正解＝`ensure_live=True` 时，**先把窗口移到屏幕外显示**（`_OffscreenShown`）再投递。
        ⛔ **绝不**用 `SW_SHOWNOACTIVATE` 把窗口显示到**屏幕内**、**绝不**抢前台。
        ⛔ 也**不再用"借前台"**（借前台是抢用户的焦点，不许；屏外档不需要它）。

    判据（守用户两条）：投递前后 **`fg` 不变**（不影响操作）、窗口 rect **与屏幕无交集**（不影响观看）。
    """
    try:
        from .input_backend import (_post, pack_lparam, WM_MOUSEMOVE,
                                    WM_LBUTTONDOWN, WM_LBUTTONUP)
    except Exception:
        return False
    hwnd = int(hwnd)
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        iconic = bool(u.IsIconic(_wt.HWND(hwnd)))
    except Exception:  # noqa: BLE001
        iconic = False

    def _fire():
        _post(hwnd, WM_MOUSEMOVE, 0, pack_lparam(int(cx), int(cy)))
        time.sleep(0.05)
        _post(hwnd, WM_LBUTTONDOWN, 1, pack_lparam(int(cx), int(cy)))
        time.sleep(0.15)
        _post(hwnd, WM_LBUTTONUP, 0, pack_lparam(int(cx), int(cy)))

    if not (iconic and ensure_live):
        try:
            _fg0 = _fg_current()
            _fire()
            _fg_watch_back(_fg0)
            return True
        except Exception:
            return False
    # 最小化档 ⇒ 屏外显示 → 投递 → 还原
    box = _OffscreenShown(hwnd)
    with box:
        if not box.shown:
            # 屏外显示没成功 ⇒ **不投递**（宁可失败，也不打扰用户）
            return False
        try:
            _fg0 = _fg_current()
            _fire()
            _fg_watch_back(_fg0)
            return True
        except Exception:
            return False
    return True


def _client_size_for_posted(hwnd: int, gui=None) -> tuple:
    """投递点击要用的**客户区宽高** —— 最小化也能算出来。

    ⭐⭐ **三级取值**（实测修正，别再"减边框猜"）：
      ① **可见档**：`GetClientRect` 直接给 ⇒ **顺手存下来**（`display_metrics` 的
         `client_size`）—— 这是**本机真实客户区尺寸**；
      ② **最小化档**：用①存下的值 **按当前整窗尺寸等比缩放**（窗口可能被用户拉过）；
      ③ 从没存过：退 `rcNormalPosition − 边框`（**只是估**，会在日志里标明"估的"）。

    ⛔ **为什么不再"减 22/11"**：那是在 150% 下用"整窗−客户区"差拍出来的，
      且**微信独立窗 `rcNormalPosition` 的高度口径与整窗不一致**
      （实测整窗 1096 而按比例算出的客户区却要 ~1050 ⇒ 直接减边框会**虚高约 35px**，
       使按比例算的 y 落到输入条以下 ⇒ **点空 ⇒ 发不出去**）。
      ⇒ 结论：**客户区尺寸必须实测存下来**，不许用"整窗减常数"推。
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        r = _wt.RECT()
        u.GetWindowRect(int(hwnd), _ct.byref(r))
        w, h = int(r.right - r.left), int(r.bottom - r.top)
        if w > 0 and h > 0 and r.left > -10000:
            c = _wt.RECT()
            u.GetClientRect(int(hwnd), _ct.byref(c))
            cw, ch = int(c.right - c.left), int(c.bottom - c.top)
            if cw > 100 and ch > 100:
                # ① 可见档：**实测客户区** ⇒ 存下来（含当时整窗尺寸，供最小化档等比换算）
                try:
                    from . import display_metrics as _dm
                    _dm.remember_client(cw, ch, win=(w, h))
                except Exception:  # noqa: BLE001
                    pass
                return cw, ch
        # ② 最小化/离屏 ⇒ 用**存下来的实测客户区**按整窗尺寸等比缩放
        try:
            from . import wechat_windows as _ww
            nr = _ww.window_rect_any_state(int(hwnd))
        except Exception:
            nr = None
        try:
            from . import display_metrics as _dm
            got = _dm.client_size(int(hwnd), win_any=nr)
            if got:
                return got
        except Exception:  # noqa: BLE001
            pass
        # ③ 从没存过 ⇒ 退"整窗减边框"（**只是估**）
        if nr:
            ww_, hh_ = int(nr[2] - nr[0]), int(nr[3] - nr[1])
            if ww_ > 100 and hh_ > 100:
                try:
                    from . import display_metrics as _dm2
                    fl, ft, fr_, fb = _dm2.frame(int(hwnd))
                except Exception:  # noqa: BLE001
                    fl = ft = fr_ = fb = 0.0
                return max(1, ww_ - int(round(fl + fr_))), max(1, hh_ - int(round(ft + fb)))
    except Exception:
        pass
    return 0, 0


def _post_mouse_down(hwnd: int, cx: int, cy: int) -> bool:
    """只投 **WM_MOUSEMOVE + WM_LBUTTONDOWN**（**不投 UP**）—— "按住不放"的开始。

    ⛔ 与 `_posted_click` 的区别是本质的（用户 点破）：
      · `_posted_click`＝down + 0.15s + up ⇒ 微信收到"按住又立刻松开" ⇒ **录 0 秒 ⇒ 什么都不会落库**；
      · 本函数＝**只按住不放** ⇒ 微信进入"录音中"（悬浮条一直显示，悬浮时长＝录音时长）。
      ⇒ **"按住说话"必须用本函数起手，不能拿 `_posted_click` 冒充。**
    """
    try:
        from .input_backend import _post, pack_lparam, WM_MOUSEMOVE, WM_LBUTTONDOWN
    except Exception:
        return False
    try:
        hwnd = int(hwnd)
        _post(hwnd, WM_MOUSEMOVE, 0, pack_lparam(int(cx), int(cy)))
        time.sleep(0.05)
        _post(hwnd, WM_LBUTTONDOWN, 1, pack_lparam(int(cx), int(cy)))
        return True
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def _post_mouse_up(hwnd: int, cx: int, cy: int) -> bool:
    """只投 **WM_LBUTTONUP** —— "松手"（＝结束录音并发送；按住说话松手即发）。"""
    try:
        from .input_backend import _post, pack_lparam, WM_LBUTTONUP
    except Exception:
        return False
    try:
        _post(int(hwnd), WM_LBUTTONUP, 0, pack_lparam(int(cx), int(cy)))
        return True
    except Exception:
        return False


def _enter_record_posted(wechat, gui, cfg=None, hwnd: int = 0, wait_s: float = 6.0,
                         *, ensure_live: bool = True) -> tuple:
    """**纯投递**进录音态（不动光标、不借前台、不注入键盘）⇒ (ok, 说明, x_used)。

    步骤（实测 + 用户点破机制）：
      ① 投递**点一下**「语音切换键」（输入条最右那个圆圈）⇒ 输入条切成「按住 说话」；
      ② 在「按住说话」热区**点一下**（`_posted_click`）⇒ 进录音态、**开始录**。
     ⚠️ **发送是在发送阶段"点绿↑"完成的**（`_posted_send`），**不是**"松手即发"
        —— 早先的"按住 + 松手"那套（`_post_mouse_down`/`_post_mouse_up` + `_POSTED_HOLD`）
        **已作废**：用户 澄清的第三个动作是"声音输完之后，点那个绿色的发送键"。

    ⛔ **判据口径**：**不再用画面判"进没进录音态"**——
      最小化档 `grab_window` **必然返回 None**（`wechat_windows` 里写死：最小化无有效画面），
      而语音条**当务之急是"发出去"**，不是"看得见"。
      ⇒ 成败一律交给 **DB 回读**（与发文件同一口径）。
      抓得到帧时（独立窗可见档）顺手用特征找回更准的落点，抓不到就按**算式**投递。

    hwnd：投递目标窗口 —— **必须是独立窗句柄**（由调用方传入；本函数**不退回主窗**，
      因为主窗是用户的、发消息只在独立窗上做）。
    """
    c = _cfg(cfg)
    tgt = int(hwnd or 0)
    if not tgt:
        # ⛔ 不退回 `gui.main_hwnd`：投给主窗＝动用户的窗口（违反项目铁律）。
        return False, "没给独立窗句柄 ⇒ 不动主窗，不发", None
    cw, ch = _client_size_for_posted(tgt, gui)
    if cw <= 100 or ch <= 100:
        return False, ("算不出投递落点（客户区 %dx%d）：窗口既不可见、也拿不到还原尺寸" % (cw, ch)), None
    # ⓪ **落点优先级：当场量 ＞ 兜底算式**
    #    ① **优先抓帧**：按帧特征找"输入条右排最右的真图标"（`_popup_find_toggle`）——
    #       位置完全由窗口自身画面决定 ⇒ 换窗口尺寸 / DPI / 微信版本都**不用改代码**；
    #    ② **抓不到帧**（真最小化、被遮挡）才退回算式：
    #       `x = 客户区宽 − (距右缘的逻辑像素 × 当前缩放)`，常量见 `_FROM_RIGHT_LP`。
    #       ⛔ 兜底用的是"距**右缘**的距离"，**不是**"占窗口宽的比例"：
    #          实测左排图标贴**左缘**定距、右排图标贴**右缘**定距，
    #          ⇒ 比例会随窗口拉宽而漂（见文件头常量区那段实测），用比例反而会点偏。
    def _px_to_toggle(cw_: int, ch_: int) -> tuple:
        """兜底算式 ⇒ `(客户区x, 客户区y)`（单位：客户区**物理像素**）。"""
        try:
            from . import display_metrics as _dm
            _sc = float(_dm.scale(tgt) or 1.0)
        except Exception:  # noqa: BLE001
            _sc = 1.0
        # 距右缘的距离：配置项单位＝逻辑像素（96DPI）；× 缩放 ⇒ 物理像素
        _lp = float(c.get("toggle_from_right_px") or _from_right_lp("toggle"))
        # y：**优先"距下缘的逻辑像素"**（本机量到过就用它 —— 它才是与窗口高无关的量）；
        #    一次都没量到才退回"客户区高 × 比例"（那是出厂兜底，窗口一高就偏高）。
        _lp_y = float(c.get("toggle_from_bottom_px") or 0) or _from_bottom_lp(_ANCHOR_TOGGLE_Y)
        if _lp_y > 0:
            _cy = int(round(float(ch_) - _lp_y * _sc))
        else:
            _cy = int(ch_ * float(c.get("toggle_y") or TOGGLE_Y))
        return max(1, int(round(cw_ - _lp * _sc))), max(1, _cy)

    # ★★ **落点优先级（本项目定调）**：**"记住的相对距离"是主路，抓帧只用来复核/重测。**
    #   为什么这样定：相对距离是**量一次、记下来**的稳定量（用户平时不改缩放/分辨率），
    #   而"按帧特征找图标"会受主题/尺寸/图标浓淡影响（实测踩过多次）。
    #   ⇒ 主路 = `_px_to_toggle`（距右缘逻辑像素 × 当前缩放）；帧路**只在两者差得多时**才接管，
    #     并**顺手把档案更新掉**（自愈）＋ 如实记日志。⛔ 帧路拿不到 ⇒ **不算失败**，主路照走。
    tx, y = _px_to_toggle(cw, ch)
    _src = "right-edge-lp"                  # 主路：距右缘的逻辑像素（**不是兜底**）
    feat = _popup_find_toggle(tgt)          # 只用来复核/重测
    if feat:
        _fx, _fy = int(feat.get("x") or 0), int(feat.get("y") or 0)
        _dx, _dy = abs(_fx - int(tx)), abs(_fy - int(y))
        if _fx > 0 and _dx > max(8, int(0.03 * int(cw or 0))):
            log.warning("[语音条] 帧上量到的语音键 x=%d 与记下的相对距离 x=%d 差 %dpx ⇒ "
                        "以**当场量到的**为准并更新档案（缩放/布局可能变过）", _fx, int(tx), _dx)
            y, tx, _src = _fy, _fx, "frame-remeasured"
        elif _fy > 0 and _dy > max(6, int(0.01 * int(ch or 0))):
            # ⛔ 阈值量级取"**半个图标高**"（约 1% 客户区高，实测图标 ~28px 物理）：
            #    取 2% 会漏掉实测的 25px 偏差（那已经点在图标上沿之外 ⇒ 点空）。
            # ⭐⭐ **x 复核通过、但 y 差得多** ⇒ y 也以**当场量到的**为准。
            #   为什么 y 的兜底模型不可靠：它是"客户区高的**比例**"（`TOGGLE_Y`），
            #   而输入条是贴着窗口**下缘**排的、行高固定 ⇒ 窗口一高，比例算出的 y 就**偏高**
            #   （实测：客户区 1446 时偏高约 28px，点上去落在图标上方 ⇒ **点空**
            #     ⇒ 整条链"每步都 True、库里却没有新行"）。
            #   ⇒ 与 x 同一口径：**能当场量就用当场量到的**，并记下"距下缘"供抓不到帧时兜底。
            log.warning("[语音条] 帧上量到的语音键 y=%d 与比例算式 y=%d 差 %dpx ⇒ "
                        "以**当场量到的**为准（输入条贴下缘，比例会随窗口高漂）", _fy, int(y), _dy)
            y = _fy
            _src = "right-edge-lp(y 帧实测)"
            _remember_bottom_anchor(tgt, y)
        else:
            _src = "right-edge-lp(帧复核通过)"
    # ⓵ **单击语音键 ⇒ 直接进录音态**（⭐ 用户 澄清的决定性机制）
    #    三步就是：**单击语音键进态 → 让声音进去 → 点绿↑发送**。
    #    ⛔ **不是**"按住说话"（那是界面上的文字提示，看着像要按住，实际单击就录）；
    #       ⇒ 旧实现"按住不放 + 松手即发"**整段作废**（那等于没进录音态、录 0 秒）。
    #    ⛔ 也**不是**"先点语音再点一下热区"（点两下会把这一个切换来回切）。
    _posted_click(tgt, tx, y, ensure_live=bool(ensure_live))
    _cut_note = "已单击语音键（落点来源：%s）" % _src
    time.sleep(0.9)     # 等界面切进录音态（出现 ✕ / 绿↑）
    # ⓶ **不投任何"按住"**：进录音态后，只需让声音进去（音频在 `send()` 里播）。
    #    ⛔ **不拿画面判成败**（修正）：帧确认只能**三态**用 ——
    #      历史实现里 `hit is False` 会被判成"没进录音态 ⇒ 失败"，但实测证明
    #      **抓帧这条路在最小化档/被遮挡时读到的可能不是这个窗口的内容**
    #      （实测：最小化档跑一次，返回 False 直接把流程掐断，而投递其实打出去了）。
    #      ⇒ 抓帧**只作正向证据**（`True` ⇒ 更确定）；`False`/`None` **一律不判失败**，
    #        成败交给 **DB 回读**（与发文件/图片同一口径：不靠画面下死结论）。
    hit = _posted_frame_shows_record(tgt) if c.get("find_by_frame", True) else None
    if hit is True:
        return True, "单击语音键已进录音态（帧特征确认）", tx
    return True, ("已单击语音键（%s）；画面%s —— 成败以 DB 回读为准（播完点绿↑发送）"
                  % (_src, "未确认" if hit is False else "抓不到、不判断")), tx


#: `_enter_record_posted` 按住时用的坐标 `(hwnd, x, y)` —— **松手必须用同一坐标**。
def _popup_in_holdtalk(hwnd: int):
    """帧里是不是**已经在「按住 说话」态** ⇒ `True / False / None`（**三态，不混**）。

    ⭐ 判据（实测修正）：**看"录音态独有的视觉特征"，不看图标位置**。
      "按住 说话"态与正常态的**图标行位置几乎相同**（实测：语音键距右缘
      两态都是 149.5px）⇒ **按位置判会误判**。
      ✅ 用**录音态的独有特征**：
        · 输入条里出现 **✕（取消）** —— 正常态**没有** ✕；
        · 或出现**绿色大圆 ↑**（发送）。
      ⇒ 在"输入条中部偏右带"（x∈0.60~0.85、y∈0.90~1.0）里找**✕ 的暗像素团**：
        正常态该带是空白/浅色（输入框），录音态该带左端是 ✕ ⇒ 暗像素显著增多。

    `None` ＝ 抓不到帧（最小化/遮挡）⇒ 调用方**不许据此下结论**，按"没切"处理（多切一次无害）。
    """
    try:
        img, _why = _popup_frame(int(hwnd))
        if img is None:
            return None
        got = _frame_icon_row(img)          # (cols, fw, fy, fh)
        if not got:
            return None
        cols, fw, fy, fh = got
        # ✕ 在图标行**上方**（输入条那一行），不在图标行本身 ⇒ 量图标行上方约 40px 处那条带
        return _band_has_cancel(img, fy, fw, fh)
    except Exception:  # noqa: BLE001
        return None


def _band_has_cancel(img, icon_y: int, fw: int, fh: int):
    """在"输入条那一行"里找 **✕**（录音态独有）⇒ `True/False`。

    做法：取图标行**上方** `icon_y-40 .. icon_y` 那条带（＝输入框/录音条所在行），
      在 x∈[0.60, 0.86] 段统计**中等暗度**像素（✕ 是细线条，不是纯黑块）。
      · 正常态：该段是**输入框内空白**（白/浅灰）⇒ 暗像素很少；
      · 录音态：该段左端有 **✕** ⇒ 暗像素明显增多。
    ⇒ 阈值取相对量（与"同带宽度的总像素"比），**不写死绝对像素数**。
    """
    try:
        rgb = img.convert("L")
        w, h = rgb.size
        y1 = max(0, int(icon_y) - 40)
        y0 = max(0, y1 - int((fh or h) * 0.045))      # 带高按帧高比例（不写死像素）
        x0, x1 = int(w * 0.60), int(w * 0.86)
        if x1 - x0 < 20 or y1 - y0 < 8:
            return False
        band = rgb.crop((x0, y0, x1, y1))
        px = band.load()
        bw, bh = band.size
        dark = 0
        for y in range(bh):
            for x in range(bw):
                if px[x, y] < 150:                     # ✕ 是深灰线条
                    dark += 1
        ratio = dark / float(bw * bh or 1)
        return ratio > 0.012                           # 正常态 ~0.003，录音态 ~0.02+
    except Exception:  # noqa: BLE001
        return False


def _popup_frame(hwnd: int):
    """抓**独立窗自己的帧** ⇒ `(PIL.Image | None, 说明)`。

    ⛔ 最小化档 ⇒ `(None, "窗口已最小化…")` —— 这是**客观事实**（`grab_window` 写死），
      调用方据此返回"抓不到、不判断"，**不许**把"看不见"当成"没有"。
    ⚠️ 抓到的图是**整窗**位图（含标题栏/边框）：本机独立窗边框 左=11 上=0 右=11 下=11
      （物理像素、DPI 已锁 PerMonitorV2 ⇒ `GetWindowRect` 就是物理像素）。
    """
    try:
        from . import wechat_windows as _ww
        img, meta = _ww.grab_window(int(hwnd), physical=True)
        if img is not None:
            return img, "ok"
        return None, str((meta or {}).get("err") or "抓不到画面")
    except Exception as e: # noqa: BLE001
        return None, "抓帧异常：%s" % str(e)[:60]


def _frame_icon_row(img, y_lo: float = 0.90, y_hi: float = 0.99):
    """在帧的**底部输入条图标行**里找"图标列剖面" ⇒ `(cols, w, y0, h)` 或 None（纯函数，便于自检）。

    做法（与 `_icon_cols` 同一套，但**只吃一张图**、不依赖主窗 gui）：
      ① 在 y∈[y_lo·h, y_hi·h] 这条带里逐行数**暗像素**，取最密的那一行当图标行的 y；
      ② 以该行为中心上下各 12px 统计每一列的暗点数 ⇒ 列剖面（有图标的列计数高）。
    ⇒ 结果**只跟这张图自己的比例有关** ⇒ 跨窗口尺寸/DPI 自动成立（不写死任何绝对像素）。

    ⛔ **暗点阈值必须是"相对量"，不能写死一个灰阶**（真机取证）：
      原来写死 `< 128`。而在某档窗口尺寸下，输入条**右边那两个图标（声波切换键 / 发送键）
      画得比左边那排浅**（灰阶 140+）⇒ **右边一个簇都切不出来** ⇒ 最右真图标变成左排那个
      ⇒ `_popup_find_toggle` 返回 None（发送链第一步退回兜底算式、第二道保险也失效）。
      实测那一帧：底排只切出 4 个簇、全在 x≤117（占宽 ≤0.089），右边空无一簇。
      ⇒ 改成**按本带的背景亮度算**：`thr = 背景 - ICON_INK_DELTA`，背景取该带灰阶的高分位
        （图标是少数、背景是多数）。这样"浅色图标"也能切出来，且**跨主题/跨版本**都成立。
    """
    try:
        g = img.convert("L")
        w, h = g.size
        y0c, y1c = int(h * float(y_lo)), int(h * float(y_hi))
        band = g.crop((0, y0c, w, y1c))
        px = band.load()
        bw, bh = band.size
        # ① 背景亮度：取整带灰阶的高分位（图标是少数点，背景占多数）
        _hist = band.histogram()
        _tot = sum(_hist) or 1
        _acc = 0
        _bg = 255
        for _v in range(255, -1, -1):
            _acc += _hist[_v]
            if _acc >= _tot * 0.75:      # 75 分位 ≈ 背景
                _bg = _v
                break
        _thr = int(_bg) - int(ICON_INK_DELTA)
        rows = []
        for y in range(0, bh, 2):
            n = 0
            for x in range(0, bw, 2):
                if px[x, y] < _thr:
                    n += 1
            rows.append((n, y))
        rows.sort(reverse=True)
        ry = rows[0][1] if rows else 0
        cols = [0] * bw
        _band = max(4, int(bh * 0.0106))          # 原写死 12px（本机帧高 1137 ⇒ 12）
        for y in range(max(0, ry - _band), min(bh, ry + _band + 1)):
            for x in range(bw):
                if px[x, y] < _thr:
                    cols[x] += 1
        return cols, bw, y0c + ry, h
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _clusters_from_cols(cols, width, min_w: int = 0, merge_gap: int = 0):
    #: 两个阈值都是**相对量**（原来写死 3 / 14px）—— 系数按本机帧宽 1274 反推，
    #:  ⇒ 本机行为与原来**逐字节相同**，换 DPI/尺寸时才按比例走。
    if not min_w:
        min_w = max(2, int(width * 0.0024))
    if not merge_gap:
        merge_gap = max(3, int(width * 0.011))
    """把列剖面切成簇 `[(x0, x1)]`（**纯函数**）：空 ≥1 列即断，间隔离太近的合并。"""
    try:
        w = float(width or 0)
        if w <= 0 or not cols:
            return []
        thr = max(2, int(max(cols) * 0.12))
        raw, s = [], None
        for x, v in enumerate(cols):
            on = int(v) >= thr
            if on and s is None:
                s = x
            elif not on and s is not None:
                if x - s >= int(min_w):
                    raw.append((s, x - 1))
                s = None
        if s is not None:
            raw.append((s, len(cols) - 1))
        out = []
        for c in raw:
            if out and c[0] - out[-1][1] <= int(merge_gap):
                out[-1] = (out[-1][0], c[1])
            else:
                out.append((c[0], c[1]))
        return out
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return []


def _icon_clusters(img) -> tuple:
    """取帧底部"输入条图标行"的簇 ⇒ `(clusters, fw, fy, fh)`；失败 `(None, 0, 0, 0)`。

    **纯函数**（只吃一张图）⇒ 便于离线标定与自检（本项目 27 张既有帧图都可用它复算）。
    """
    try:
        got = _frame_icon_row(img)
        if not got:
            return None, 0, 0, 0
        cols, fw, fy, fh = got
        cl = _clusters_from_cols(cols, fw)
        return (cl or None), fw, fy, fh
    except Exception:  # noqa: BLE001
        return None, 0, 0, 0


def _icon_clusters_no_edge(img) -> tuple:
    """同 `_icon_clusters`，但**剔除贴边的那个簇**（窗口右边框竖线，不是图标）。

    ⭐⭐ **为什么必须剔**（用 27 张帧图标定出来的）：
      贴着右缘那一簇恒在帧内 `0.988~0.999`、**宽度只有 0.011**（≈10px）——
      那是**窗口右边框的竖线**，不是"发送"按钮。
      ⛔ 不剔的后果：把"语音切换键 = 最右簇左边固定距离"算到**边框上**
        ⇒ 每次点击都落在窗口边缘 ⇒ **发不出去**（这正是此前多轮失败的直接原因）。
      ✅ 剔掉后，正常态最右的真图标 = 帧内 **0.837** ＝ 语音切换键（圆圈+声波）×
    """
    cl, fw, fy, fh = _icon_clusters(img)
    if not cl:
        return None, fw, fy, fh
    real = [c for c in cl if (c[1] / float(fw or 1)) < FRAME_EDGE_REL]
    return (real or None), fw, fy, fh


def _popup_find_toggle(hwnd: int, cfg=None):
    """按**帧特征**在独立窗上找"语音切换键"的落点 ⇒ `{"x","y",...}` 或 `None`。

    ⭐ 算法（只用帧自身特征 ⇒ 换窗口尺寸/DPI 自动成立）：
      ① 帧底图标行取暗像素列剖面 → 切簇；
      ② **剔掉贴边那簇**（窗口右边框竖线，恒在帧内 0.988~0.999、宽 0.011）；
      ③ 剔完后**最右的真图标 = 语音切换键**（输入条**右排**贴右缘排布，它是最右那个）；
      ④ **粗筛**：该簇必须落在帧右侧 `TOGGLE_RIGHT_BAND` 内。
         这条只用来排除"把**左排**的麦克风或右边框当成语音切换键"
         （实测左排麦克风在帧内 0.32，右排语音切换键在 0.89）；
         **落点本身按"距右缘的相对距离"换算**，粗筛不参与定位。
         ⛔ 不要把粗筛的带子当成落点 —— 带宽是"识别用"，不是"点击用"。

    ⛔ **单位**：帧是**物理像素**（`grab_window(physical=True)`）；
      返回的 `x/y` 是**客户区物理像素**（`_frame_to_client` 把边框当场量掉后得到）。

    返回 `{"x","y","tx_rel","ty_rel","px_from_right","px_from_right_96","src","found"}`；
    **抓不到帧 / 剔完没图标 / 粗筛不过 ⇒ 返回 `None`**（调用方退回"距右缘"兜底，不硬失败）。
    """
    try:
        img, why = _popup_frame(int(hwnd))
        if img is None:
            return None
        real, fw, fy, fh = _icon_clusters_no_edge(img)
        if not real:
            return None
        # ⭐ **候选顺序：从右往左，但"最右那个"排在最后** ——
        #   真机取证（真机取证，帧 1274×1137）：输入条右排**有两个**图标
        #   （声波切换键 0.921w、**发送键 0.959w**）。**发送键恒在最右**（这是它的位置定义），
        #   所以"语音切换键"＝**右起第二个**；死取最右会去点发送键（发一条空消息）。
        #   ⇒ 先试右起第二个及更左的候选，最后才退到最右那个。
        _half = _icon_half(img)
        _inband = [c for c in real
                   if float(TOGGLE_RIGHT_BAND[0])
                   <= ((c[0] + c[1]) / 2.0) / float(fw or 1) <= float(TOGGLE_RIGHT_BAND[1])]
        _order = list(reversed(_inband[:-1])) + _inband[-1:] if len(_inband) >= 2 \
            else list(reversed(_inband))
        tog = None
        _best = (None, -2.0)
        for _cand in _order:
            _m = (_cand[0] + _cand[1]) / 2.0
            _s, _o, _w = _icon_match(img, int(_m), int(fy), "toggle", half=_half)
            if _s is None:
                _icon_learn(img, int(_m), int(fy), "toggle", half=_half)
                tog = _cand
                break
            if _best[0] is None or _s > _best[1]:
                _best = (_cand, _s)
            if _s >= _ICON_MATCH_MIN:
                tog = _cand
                break
        # ★ **模板自愈**：整帧所有候选都够不上阈值 ⇒ 多半是**模板过期/当初学错了**
        #   （真机取证：模板与这一帧里**每一个**图标都只有 0.2~0.3 分，连左排都不像）。
        #   此时**从"最可能那个候选"重学**并采用它，而不是把整条帧路永久废掉。
        #   ⛔ 只在"这一帧确实像个输入条"时自愈（有 ≥1 个右侧候选）；否则保持 fail-closed。
        if tog is None and _best[0] is not None:
            _m = (_best[0][0] + _best[0][1]) / 2.0
            log.warning("[语音条] 图标模板与本帧全部候选都不像（最高 %.3f < %.2f）⇒ "
                        "判为模板过期，从候选 x=%.0f 重学", _best[1], _ICON_MATCH_MIN, _m)
            _save_icon_template("toggle",
                                _icon_patch(img, int(_m), int(fy), _half) or img.crop((0, 0, 8, 8)))
            tog = _best[0]
        if tog is None:
            return None
        tog_mid = (tog[0] + tog[1]) / 2.0
        px_right = float(fw or 0) - tog_mid
        try:
            from . import display_metrics as _dm
            _sc = float(_dm.scale(int(hwnd)) or 1.0)
        except Exception:  # noqa: BLE001
            _sc = 1.0
        px_96 = px_right / _sc             # 距右缘的**逻辑像素**（只作留痕/排障，不当落点用）
        tx_rel = tog_mid / float(fw or 1)
        ty_rel = float(fy) / float(fh or 1)
        cx, cy = _frame_to_client(int(hwnd), toggle_rel=(tx_rel, ty_rel))
        if cx <= 0 or cy <= 0:
            return None
        # 记下"距右缘的逻辑像素" ⇒ 以后抓不到帧时用本机实测值兜底（不是出厂默认）
        _remember_anchor_from_client("toggle", int(hwnd), int(cx))
        return {"x": cx, "y": cy, "tx_rel": tx_rel, "ty_rel": ty_rel,
                "px_from_right": px_right, "px_from_right_96": px_96,
                "src": "frame-anchor", "found": tog}
    except Exception:  # noqa: BLE001
        return None


def _posted_record_anchors(hwnd: int, cfg=None):
    """**录音态**两个落点（发送↑ / ✕取消），按**锚点相对定位**现算 ⇒ 跨尺寸/DPI 稳。

    算法（**不写死绝对坐标**，只用"键与键的相对位置"）：
      ① **绿色 ↑ 是唯一锚点**（录音态里唯一的大片绿，`_popup_find_green` 找它的**客户区物理像素**）；
      ② **✕ 取消 = 锚点左边固定间距**（两键同在输入条**右排** ⇒ y 相同、间距不随窗口宽变）。
    抓不到帧（真最小化）⇒ 返回 `None`，调用方退回"距右缘"兜底（**不硬失败**）。

    ⛔ **单位**：返回的坐标是**客户区物理像素**（`_popup_find_green` → `_frame_to_client` 得到），
      所以间距要由逻辑像素 × 当前缩放折出来。

    ⚠️ **`cancel_dx_px` 配置项**：单位＝**逻辑像素（96DPI）**，供换微信版本后不改代码就能修；
      缺省用 `_DELTA_CANCEL_FROM_GREEN_LP`（标定值）。⛔ 它必须是"两键间距"，不许填绝对坐标。
    """
    try:
        got = _popup_green_and_frame(int(hwnd))
        if not got:
            return None
        img, pt, (ffx, ffy), _fsz = got
        sx, sy = int(pt[0]), int(pt[1])
        cw, ch = _client_size_for_posted(int(hwnd), None)
        if cw <= 100:
            return None
        try:
            from . import display_metrics as _dm
            _sc = float(_dm.scale(int(hwnd)) or 1.0)
        except Exception:  # noqa: BLE001
            _sc = 1.0
        # ✕ 与锚点（绿↑）的**间距**（逻辑像素）—— 常量的是"间距"，不是绝对位置
        _lp = float((_cfg(cfg) or {}).get("cancel_dx_px") or _DELTA_CANCEL_FROM_GREEN_LP)
        dx = int(round(_lp * _sc))          # 逻辑 → 客户区物理像素
        # ★ **第二道保险：图标相似度（模板匹配）** —— 两个键各比对一次；不像就**不点**。
        #    ⛔ 帧坐标与客户区坐标只差一个平移 ⇒ **差值（间距）在两套坐标里相同**，
        #      所以 ✕ 的帧坐标 = 绿↑ 的帧坐标 − 同一个 dx。
        _half = _icon_half(img)
        for _kd, _kx, _ky in (("green", int(ffx), int(ffy)),
                              ("cancel", int(ffx) - dx, int(ffy))):
            _isc, _ioff, _iwhy = _icon_match(img, _kx, _ky, _kd, half=_half)
            if _isc is None:
                _icon_learn(img, _kx, _ky, _kd, half=_half)      # 首次：记下模板
            elif _isc < _ICON_MATCH_MIN:
                return None                                      # 不像 ⇒ 不点
        # 记下两个键"距右缘的逻辑像素" ⇒ 以后抓不到帧时用本机实测值兜底
        _remember_anchor_from_client("green", int(hwnd), int(sx))
        _remember_anchor_from_client("cancel", int(hwnd), max(1, int(sx) - dx))
        return {"send": (sx, sy),
                "cancel": (max(1, sx - dx), sy),
                "src": "record-anchor"}
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _frame_to_client(hwnd: int, toggle_rel):
    """把**帧内比例**换算成**客户区像素**（投递点击要的就是客户区坐标）。

    帧＝整窗位图（含边框）⇒ `客户区x = 帧x − 左边框宽`。边框宽度**实测**（本机 左=11 上=0），
    且**按窗口尺寸成比例**更稳：用 `GetWindowRect` 与 `GetClientRect` 的差当场量出边框
    （不写死 11）⇒ 换尺寸/DPI 都成立。量不到 ⇒ 退回"按比例估"。
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        wr, cr = _wt.RECT(), _wt.RECT()
        u.GetWindowRect(int(hwnd), _ct.byref(wr))
        u.GetClientRect(int(hwnd), _ct.byref(cr))
        ww_, wh_ = int(wr.right - wr.left), int(wr.bottom - wr.top)
        cw, ch = int(cr.right - cr.left), int(cr.bottom - cr.top)
        if ww_ <= 0 or wh_ <= 0 or cw <= 0 or ch <= 0:
            return 0, 0
        # 边框（物理像素）：左右对称取一半总宽差；上边框＝标题栏高
        bx = max(0, (ww_ - cw) // 2)
        by = max(0, (wh_ - ch) - bx)   # 减去下边框（约等于左右边框宽）
        fx, fy = float(toggle_rel[0]), float(toggle_rel[1])
        cx = int(round(ww_ * fx)) - bx
        cy = int(round(wh_ * fy)) - by
        return max(1, cx), max(1, cy)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0, 0


def _posted_frame_shows_record(hwnd: int):
    """抓**独立窗自己的帧**看"有没有进录音态" ⇒ `True / False / None`。

    三态（**别把"测不了"混进"不是"**）：
      · `True`  ⇒ 看到录音态特征：底部右区有**绿色圆（发送↑）** ＋ 中部有**绿色音量点**；
      · `False` ⇒ 抓到了帧、但**没有**这些特征（落点或版本不同）；
      · `None`  ⇒ **抓不到帧**（最小化档、遮挡、权限）⇒ 调用方**不许据此下结论**，以 DB 回读为准。
    特征与 `_green_cluster`/`_meter_level` 同一口径（`g>r+25 and g>b+25 and g>90`），
    但**在帧内按比例取区域** ⇒ 跨尺寸/DPI 稳。
    """
    try:
        img, why = _popup_frame(int(hwnd))
        if img is None:
            return None
        try:
            from PIL import Image  # noqa: F401
        except Exception:
            return None
        rgb = img.convert("RGB")
        w, h = rgb.size

        def _green_hits(box):
            b = rgb.crop(box)
            px = b.load()
            bw, bh = b.size
            n = 0
            for y in range(0, bh, 2):
                for x in range(0, bw, 2):
                    r, g, bl = px[x, y]
                    if g > r + 25 and g > bl + 25 and g > 90:
                        n += 1
            return n

        # 绿色发送 ↑：右下（帧比例 0.78~1.0 宽 / 0.86~1.0 高）
        send_hits = _green_hits((int(w * 0.78), int(h * 0.86), w, h))
        # 音量点：中带（0.45~0.80 宽 / 0.86~1.0 高）
        meter_hits = _green_hits((int(w * 0.45), int(h * 0.86), int(w * 0.80), h))
        # 录音态＝两个特征都在（发送↑ 是圆、音量点是外圈串）
        return (send_hits >= 10) and (meter_hits >= 6)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _posted_target_hwnd(gui, chat_id: str = None, name: str = None, hwnd: int = 0) -> int:
    """投递路要点的目标窗口。

    ⛔ **只认独立窗**（`hwnd` 显式传入优先）——本产品发消息只在独立窗上做，
      **绝不把点击投给主窗**（主窗是用户的；投给它等于动用户的窗口）。
    兜底顺序：① 显式 `hwnd` ② 按会话名 `find_popup` 找的独立窗。
    **拿不到独立窗就返回 0**（调用方据此如实失败），**不退回主窗**。
    """
    if int(hwnd or 0):
        return int(hwnd)
    try:
        from . import wechat_windows as _ww
        tname = name or chat_id
        if tname:
            info = _ww.find_popup(str(tname))
            if info and info.get("hwnd"):
                return int(info["hwnd"])
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return 0


def _posted_cancel(gui, rw: int, rb, chat_id: str = None, name: str = None,
                   hwnd: int = 0, *, ensure_live: bool = True) -> None:
    """投递路放弃时点 ✕ 退出录音态 ⇒ `None`（不返回成败，调用方以 DB 回读为准）。

    优先级：**当场量 ＞ 兜底算式**。
      ① 抓得到帧 ⇒ `_posted_record_anchors`：绿↑ 当场定位，✕ = 它左边固定间距
         （两键同在输入条**右排** ⇒ 间距不随窗口宽变）⇒ 跨尺寸/DPI 稳；
      ② 抓不到帧 ⇒ 退回算式：`x = 客户区宽 − (✕ 距右缘的逻辑像素 × 当前缩放)`，见 `_FROM_RIGHT_LP`。
         ⛔ 用"距右缘的距离"，**不是**"占客户区宽的比例"（比例会随窗口宽漂，见文件头常量区）。

    ⛔ **单位**：`_posted_click` 收的是**客户区物理像素** ⇒ 两条路都必须换算到这个空间。
    ⛔ 两条路都拿不到 ⇒ **直接返回、不点**（宁可这次取消不了，也不许点错地方）。
    """
    h = _posted_target_hwnd(gui, chat_id, name, hwnd=hwnd)
    if not h:
        return
    cw, ch = _client_size_for_posted(h, gui)
    if cw <= 100:
        return
    # ① 当场量：抓帧定位绿↑ → 推 ✕（间距逻辑像素 × 缩放 = 物理像素）
    try:
        anc = _posted_record_anchors(h)
        if anc:
            _posted_click(h, int(anc["cancel"][0]), int(anc["cancel"][1]), ensure_live=ensure_live)
            return
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    # ② 兜底算式：✕ 距客户区右缘的逻辑像素 × 缩放
    try:
        from . import display_metrics as _dm
        _sc = float(_dm.scale(h) or 1.0)
    except Exception:  # noqa: BLE001
        _sc = 1.0
    _lp = float((_cfg() or {}).get("cancel_from_right_px") or _from_right_lp("cancel"))
    cx = max(1, int(round(cw - _lp * _sc)))
    _posted_click(h, cx, int(ch * float(TOGGLE_Y)), ensure_live=ensure_live)


def _posted_send(gui, rw: int, chat_id: str = None, name: str = None,
                 hwnd: int = 0, *, ensure_live: bool = True) -> tuple:
    """投递路发送：**点绿色 ↑** ⇒ `(ok, 说明)`。

    优先级：**当场量 ＞ 兜底算式**。
      ① 抓得到帧 ⇒ `_popup_find_green` 按**颜色**当场找那团绿（录音态右下唯一的大片绿）
         ⇒ 位置由画面本身决定，换尺寸/DPI/微信版本都不用改代码；
      ② 抓不到帧（真最小化）⇒ 退回算式：`x = 客户区宽 − (绿↑ 距右缘的逻辑像素 × 当前缩放)`。
         ⛔ 用"距**右缘**的距离"，**不是**"占客户区宽的比例"：
            实测右排图标贴右缘定距（绿↑ 中心距帧右缘 919 帧 68.5），
            而比例会随窗口拉宽而漂 ⇒ 用比例会点偏。

    ⛔ **单位**：`_posted_click` 收的是**客户区物理像素**，两条路都换算到这个空间。
    ⛔ 返回 `(False, ...)` 时调用方**不许当成"发出去了"**（以 DB 回读为准）。
    """
    h = _posted_target_hwnd(gui, chat_id, name, hwnd=hwnd)
    if not h:
        return False, "拿不到独立窗句柄"
    cw, ch = _client_size_for_posted(h, gui)
    if cw <= 100:
        return False, "算不出客户区尺寸"
    # ① 当场量：抓帧按颜色找绿↑（拿不到再走下面的兜底）
    try:
        pt = _popup_find_green(h)
        if pt and int(pt[0]) > 0 and int(pt[1]) > 0:
            gx, gy = int(pt[0]), int(pt[1])
            _remember_anchor_from_client("green", int(h), gx)   # 记下本机实测的"距右缘逻辑像素"
            ok = _posted_click(h, gx, gy, ensure_live=ensure_live)
            return bool(ok), "点绿↑(%d,%d) 帧锚点" % (gx, gy)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    # ② 兜底算式：绿↑ 距客户区右缘的逻辑像素 × 缩放
    try:
        from . import display_metrics as _dm
        _sc = float(_dm.scale(h) or 1.0)
    except Exception:  # noqa: BLE001
        _sc = 1.0
    _lp = float((_cfg() or {}).get("send_green_from_right_px") or _from_right_lp("green"))
    gx, gy = max(1, int(round(cw - _lp * _sc))), int(ch * float(TOGGLE_Y))
    ok = _posted_click(h, gx, gy, ensure_live=ensure_live)
    return bool(ok), "点绿↑(%d,%d) 距右缘兜底" % (gx, gy)


def _icon_half(img) -> int:
    """取样半径 ＝ 帧短边 × `_ICON_HALF_REL`（**按帧尺寸取**，不写死像素 ⇒ 跨 DPI/尺寸成立）。"""
    try:
        w, h = img.size
        return max(6, int(min(w, h) * _ICON_HALF_REL))
    except Exception:  # noqa: BLE001
        return 12


def _icon_patch(img, cx: int, cy: int, half: int):
    """按半径 `half` 从帧里裁一块 ⇒ `PIL.Image` 或 `None`（越界/太小 ⇒ None）。"""
    try:
        w, h = img.size
        x0, y0 = max(0, int(cx) - int(half)), max(0, int(cy) - int(half))
        x1, y1 = min(w, int(cx) + int(half) + 1), min(h, int(cy) + int(half) + 1)
        if x1 - x0 < 6 or y1 - y0 < 6:
            return None
        return img.crop((x0, y0, x1, y1))
    except Exception:  # noqa: BLE001
        return None


def _as_lum(seq):
    """把像素序列统一成**亮度浮点序列** —— 同时接受 `L` 模式的单值 与 `RGB` 的三元组。

    ⛔ 为什么必须兼容两种：`getdata()` 的返回值**取决于图像模式**。
      只按"单值"写，喂进 RGB 图时会 `TypeError`（在 `except` 里被吞成 `None`）
      ⇒ 表现成"相似度全算不出来"，**不报错**（实测踩过）。
    """
    out = []
    for v in seq:
        if isinstance(v, (int, float)):
            out.append(float(v))
        else:
            r, g, b = v[0], v[1], v[2]
            out.append(r * 0.299 + g * 0.587 + b * 0.114)
    return out


def _ncc(a, b):
    """**归一化互相关**（-1~1）。对"整体变亮/变暗"不敏感 ⇒ 换主题、换亮度也认得出。"""
    try:
        a = _as_lum(a)
        b = _as_lum(b)
        n = min(len(a), len(b))
        if n < 25:
            return None
        ma = sum(a[:n]) / float(n)
        mb = sum(b[:n]) / float(n)
        num = da2 = db2 = 0.0
        for i in range(n):
            da = a[i] - ma
            db = b[i] - mb
            num += da * db
            da2 += da * da
            db2 += db * db
        den = (da2 * db2) ** 0.5
        return (num / den) if den > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _icon_template_path(kind: str) -> str:
    try:
        from .config import DATA_DIR
        return os.path.join(DATA_DIR, "icon_templates", "voice_%s.png" % str(kind))
    except Exception:  # noqa: BLE001
        return ""


def _load_icon_template(kind: str):
    """读**本机记住的图标模板** ⇒ `PIL.Image` 或 `None`（没记过 ⇒ None）。"""
    try:
        from PIL import Image  # noqa: F401
        p = _icon_template_path(kind)
        if p and os.path.exists(p):
            return Image.open(p).convert("L")
    except Exception:  # noqa: BLE001
        pass
    return None


def _save_icon_template(kind: str, patch) -> bool:
    """把这一块存成**本机模板**（原子写；失败只记日志，不影响这一次发送）。"""
    try:
        p = _icon_template_path(kind)
        if not p:
            return False
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = persist.tmp_for(p)
        patch.convert("L").save(tmp, "PNG")
        from . import persist
        persist.replace_into(tmp, p)
        return True
    except Exception:  # noqa: BLE001
        return False


def _icon_match(img, cx: int, cy: int, kind: str, half: int = 0, search: int = 0,
                tpl=None) -> tuple:
    """**图标相似度（模板匹配）**：拿本机记住的模板在候选点**附近**找最像的位置 ⇒
    `(score, (dx, dy), 说明)`。

    为什么要模板匹配、不用"特征阈值"（**实测教训**）：同一条输入条上有好几个"深色描边圆"，
      按"深色占比 / 长宽比 / 中心亮度"这类特征，**同一排的邻居图标全都合格**
      （实测 8 个错位置里 6 个判成"像"），而真位置反而有 2 个被判"不像" ⇒
      既挡不住点错、又会把正常发送挡掉。⇒ 只有**拿图标自己的样子比对**才分得开。

    `score` ＝ 归一化互相关（-1~1）：**1.0 ＝ 一模一样**。
      `search` 允许在 ±`search` 像素内找最佳位置 ⇒ 小偏差**能被自动吸收**，
      同时把"最佳位置偏得太远"当成不像（因为分数上不去）。

    三态（⛔ 不许把"没得比"当成"不像"）：
      · `score` 有值 ⇒ 拿去跟 `_ICON_MATCH_MIN` 比；
      · `score is None` ⇒ **没记过模板 / 算不出** ⇒ 调用方**不许据此否决**（首次必须能发）。
    """
    try:
        if tpl is None:
            tpl = _load_icon_template(kind)
        if tpl is None:
            return None, None, "本机还没记过这个图标的模板"
        hf = int(half) if half else _icon_half(img)
        #: 搜索半径也按帧尺寸取（原写死 6px；本机帧短边 1137 ⇒ 6）——
        #:  ⛔ 不写死：小帧上 6px 太大（会去够到隔壁图标），大帧上又太小。
        if not search:
            search = max(3, int(min(img.size) * 0.0053))
        best = None
        for dy in range(-int(search), int(search) + 1):
            for dx in range(-int(search), int(search) + 1):
                pt = _icon_patch(img, int(cx) + dx, int(cy) + dy, hf)
                if pt is None:
                    continue
                t = tpl.resize(pt.size)
                s = _ncc(list(pt.getdata()), list(t.getdata()))
                if s is None:
                    continue
                if best is None or s > best[0]:
                    best = (s, (dx, dy))
        if best is None:
            return None, None, "算不出相似度"
        return best[0], best[1], "与模板的相似度 %.3f（偏移 %s）" % (best[0], best[1])
    except Exception as e:  # noqa: BLE001
        return None, None, "判不了（%s）" % str(e)[:40]


def _icon_learn(img, cx: int, cy: int, kind: str, half: int = 0) -> bool:
    """**首次成功定位**时，把这一块裁下来**记成本机模板**（下次点之前拿它比对）。

    ⛔ 只在"还没记过"时记 —— 记过一次就不再覆盖，免得被一次误抓带偏。
    """
    try:
        if _load_icon_template(kind) is not None:
            return False
        pt = _icon_patch(img, int(cx), int(cy), int(half) if half else _icon_half(img))
        if pt is None:
            return False
        return _save_icon_template(kind, pt)
    except Exception:  # noqa: BLE001
        return False



def _popup_green_and_frame(hwnd: int):
    """在独立窗帧的**右下**找那团绿 ⇒ `(img, 客户区点, 帧内点, 帧尺寸)` 或 `None`。

    与 `_popup_frame_shows_record` 同一套绿色口径；位置**从帧内比例算** ⇒ 跨尺寸/DPI 稳。
    把**帧本体**一起返回，是为了让调用方能在**同一帧**上再做一次"图标相似度"复核
    （⛔ 不要为了复核再抓一次帧：PrintWindow 一次就是上百毫秒）。
    """
    try:
        img, _why = _popup_frame(int(hwnd))
        if img is None:
            return None
        rgb = img.convert("RGB")
        w, h = rgb.size
        box = (int(w * 0.78), int(h * 0.86), w, h)
        b = rgb.crop(box)
        px = b.load()
        bw, bh = b.size
        xs, ys = [], []
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, bl = px[x, y]
                if g > r + 25 and g > bl + 25 and g > 90:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 8:
            return None
        fx = box[0] + sum(xs) / len(xs)
        fy = box[1] + sum(ys) / len(ys)
        pt = _frame_to_client(int(hwnd), (fx / float(w), fy / float(h)))
        if not pt or int(pt[0]) <= 0 or int(pt[1]) <= 0:
            return None
        return img, (int(pt[0]), int(pt[1])), (int(round(fx)), int(round(fy))), (w, h)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None


def _popup_find_green(hwnd: int):
    """在独立窗帧的**右下**找那团绿（录音态的发送↑）⇒ 客户区像素 `(x, y)` 或 None。"""
    got = _popup_green_and_frame(int(hwnd))
    return got[1] if got else None


def calibrate(wechat, save: bool = True, log=None) -> dict:
    """**已弃用**：不再需要标定那个"圆圈"的位置。

    ⭐ 为什么弃用：落点改成 **"客户区宽 − 距右缘固定像素"** ⇒ 语音键/绿↑/✕ 的
      位置**由算式现算**，不再依赖任何标定值（见 `_enter_record_posted` / `_posted_send`）。
      "连『看发送键』这一环都省掉" ⇒ **标定这一步整体消失**。

    ⛔ **本函数含有违反用户判据的动作**（保留仅为历史参照，**产品代码不许调用**）：
      · `gui._update_render_rect()` —— 会把**主窗摆回屏幕内** ⇒ 违反判据①「不影响观看」；
      · `_force_foreground(main_hwnd)` —— 借前台、抢焦点 ⇒ 违反判据②「不影响操作」。
      ⇒ 现在**没有任何调用点**；若将来真需要，必须改成"只对独立窗、且只抓帧不摆主窗"的做法。

    原功能（仅作文档留痕）：沿输入区右下扫几个候选点，谁点了出现绿簇就是"进录音态"的圆圈，
    进态后**立刻点 ✕ 退出**，结果写回配置 `voice_strip.record_btn`。
    """
    raise RuntimeError(
        "voice_strip.calibrate() 已弃用：落点改成『距右缘像素』算式、不再需要标定；"
        "且本函数会把主窗摆回屏幕内 + 借前台，违反判据①②。请不要调用。")


def _calibrate_legacy(wechat, save: bool = True, log=None) -> dict:  # noqa: D401
    """⚠️ **历史实现**（留档用，含违规动作，不许接线）。见上方 `calibrate` 的弃用说明。"""
    c = _cfg()
    send = tuple(c.get("send_btn") or DEFAULT_SEND)
    gui = wechat._get_gui()
    gui._update_render_rect()
    # ⚠️ 标定必须在"**看得见**"的前提下做：控制台/浏览器常常盖着微信（实测：渲染区被遮挡时
    #    PrintWindow 都拿不到帧 ⇒ 绿簇永远判 False、四个候选点全假阴性）。⇒ 先借前台、标完立刻还。
    import ctypes
    _u32 = ctypes.windll.user32
    _stashed = 0
    try:
        from . import wechat as _w
        _stashed = int(_w._fg_now() or 0)
        _hwnd = int(getattr(gui, "main_hwnd", 0) or 0)
        if _hwnd:
            _w._force_foreground(_u32, _hwnd)
            time.sleep(0.7)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        _stashed = 0
    r = gui.render_rect or (0, 0, 0, 0)
    rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
    tried = []
    _result = None
    for rx in CALIB_XS:
        pt = (int(rw * rx), int(rh * send[1]))
        try:
            ok, why = _click(wechat, gui, pt[0], pt[1], c)
        except Exception as e:
            tried.append((rx, "点击异常：%s" % e))
            continue
        time.sleep(0.7)
        hit = _green_cluster(gui)
        tried.append((rx, "绿簇=%s" % hit))
        if hit:
            # 退出录音态（✕ 取消在最左）
            try:
                _click(wechat, gui, int(rw * 0.30), int(rh * send[1]), c)
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass
            time.sleep(0.4)
            if save:
                cfg = get_config()
                seg = cfg.setdefault("voice_strip", {})
                seg["record_btn"] = [rx, send[1]]
                seg.setdefault("send_btn", list(send))
                try:
                    from .config import save_config
                    save_config(cfg)
                except Exception as e:
                    if log:
                        log("warn", "标定结果写配置失败：%s", e)
            _give_back(_stashed)
            return {"ok": True, "record_btn": [rx, send[1]], "tried": tried}
    _give_back(_stashed)
    return {"ok": False, "why": "几个候选点都没进录音态（微信窗口被挡住 / 没打开聊天 / 位置完全不同？）",
            "tried": tried}


def _fg_current() -> int:
    """**只读**当前前台窗口句柄（不借、不抢、不改任何状态）。

    ⛔ 不许在本模块自己拼 `ctypes.windll.user32.GetForegroundWindow()`：那个写法**默认按 32 位
      int 收句柄**，实测**同一进程同一瞬间**它给 `0`、而 `wechat._fg_now()` 给正常句柄
      ⇒ 记下一个 0 ＝「还无可还」，症状是"前台被顶走了却什么都没还，而且日志里看不出来"。
      ⇒ 读前台一律走 `wechat._fg_now()`（唯一实现点）。
    """
    try:
        from . import wechat as _w
        return int(_w._fg_now() or 0)
    except Exception:
        return 0


def _pid_of(hwnd: int) -> int:
    """窗口所属进程 id（拿不到 ⇒ 0）。"""
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        p = _wt.DWORD()
        _ct.windll.user32.GetWindowThreadProcessId(_wt.HWND(int(hwnd)), _ct.byref(p))
        return int(p.value)
    except Exception:  # noqa: BLE001
        return 0


def _fg_watch_back(prev: int, *, timeout: float = 0.15, gap: float = 0.03) -> bool:
    """点完之后**盯一小会儿**：一发现前台被微信的窗占着就立刻还回 `prev`。

    ⛔ 为什么要"盯"而不是"点完就查一次"（实测）：微信处理投递过来的消息是**异步的** ——
      点击返回时前台还没变，**几毫秒之后**它才把窗口置前。点完只查一次必然漏。
    ⛔ 代价：最多 `timeout` 秒（默认 0.15）。**全透明档下它一路空转**（实测前台根本不动），
      所以值要给得小 —— 三枪就是三次 0.15s；屏外兜底档下它是唯一的"还回去"手段，别调到 0。
    """
    _deadline = time.time() + max(0.05, float(timeout))
    while True:
        if prev and _fg_give_back_now(prev):
            return True
        if time.time() >= _deadline:
            return False
        time.sleep(float(gap))


def _fg_give_back_now(prev: int) -> bool:
    """**立刻**把前台还回 `prev`（一枪，不等）—— 只在"当前前台是**微信自己的窗**"时才动手。

    ⭐ 为什么要它（实测）：投递的点击**真落到微信的控件上**时，**微信自己会把窗口置前**
      （`Weixin.dll` 里就有 `SetForegroundWindow`）；独立窗最小化档下被顶到前台的是
      **主窗**（它在屏内、用户看得见）⇒ 用户会看着自己的主窗突然跳到最前。
      ⛔ 我们拦不住微信那一下，但可以**当场还回去**：整条链收尾才还的话，主窗会在最前
      停 **约 7 秒**（实测），每次点击后就还则只有几十毫秒（人眼看不到）。
    ⛔ 安全线（与 `wechat._restore_fg_until` 同口径）：当前前台**不是微信的窗**时一律不碰 ——
      那是用户自己切过去的窗口，抢回来就是打扰。
    """
    try:
        if not prev:
            return False
        import ctypes as _ct
        u = _ct.windll.user32
        u.GetForegroundWindow.restype = _ct.c_void_p
        cur = int(u.GetForegroundWindow() or 0)
        if not cur or cur == int(prev):
            return False
        from . import input_backend as _ib
        _main = int(_ib.find_main_window() or 0)
        if not _main:
            return False
        _pm, _pc = _pid_of(_main), _pid_of(cur)
        if not (_pm and _pc and _pm == _pc):
            return False                 # 前台在别的进程 ⇒ 是用户自己切的，不动
        from . import wechat as _w
        ok = bool(_w._force_foreground(u, int(prev)))
        log.info("[语音条] 投递点击把微信顶到前台 ⇒ 立刻还回 %s（结果=%s）", int(prev), ok)
        return ok
    except Exception:  # noqa: BLE001
        return False


def _give_back(stashed_fg: int):
    """把借走的前台还给用户原来的那个窗口（盯着还，最多 2.5 秒）。

    谁需要调（三种情形都走这里）：
      · **真点/标定档** ⇒ `_borrow_foreground` 借了前台（要 `SetCursorPos`）；
      · **投递档** ⇒ 没借，但**收独立窗时激活可能落到微信主窗上**（owned window 的 Z 序邻居）
        ⇒ 记下原前台、这里还回去；
      · 标定/试发的收尾。
    ⚠️ 前台**没被顶动**时它一次 `SetForegroundWindow` 都不发（`_restore_fg_until` 先比一次就 break）。
    ⚠️ `_restore_fg_until` **不再顺手**把"为干活还原出来的主窗"放回收起状态
    （那会在一条链里被调很多次 ⇒ 收→放→收→放，用户看到微信在抽风）。⇒ 收尾动作**必须由调用方
    自己补一句**（文本链/文件链就是这么做的：`_minimize_back_if_needed`）。
    这条链（语音条标定/试发）也是一条会 `_ensure_main_visible` 还原主窗的链，所以同样要补。
    """
    if not stashed_fg:
        return
    try:
        from . import wechat as _w
        _w._FG_STASH["hwnd"] = int(stashed_fg)
        _w._restore_fg_until("语音条标定/试发回还", timeout=2.5, keep=False)
        _w._minimize_back_if_needed("语音条标定/试发收尾")
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass


def _borrow_foreground(gui) -> int:
    """**借一下前台**（把微信主窗置前），返回原来那个前台窗口句柄（给 `_give_back` 还回去）。

    为什么非借不可：真点要 `SetCursorPos`，而
    **最前面的窗口是提权进程时 Windows 直接拒绝**（`GetLastError=5` ACCESS_DENIED）——本机最常见的就是
    **我们自己的控制台**（`一键启动.exe` 带提权 manifest 起的 WebView2 窗）。实测：控制台在前 ⇒ 三处候选
    连 30 秒全失败；把微信置前 ⇒ 立刻成功。⇒ 发之前借、发完还（与 `calibrate()` 同一套借还机制）。
    """
    try:
        import ctypes
        from . import wechat as _w
        stashed = int(_w._fg_now() or 0)
        hwnd = int(getattr(gui, "main_hwnd", 0) or 0)
        if hwnd and stashed != hwnd:
            _w._force_foreground(ctypes.windll.user32, hwnd)
            time.sleep(0.6)
        return stashed
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0


def _idn_scoped_mark(fn):
    """给 `send` 打「已包会话身份事务」标记——与 wechat.py 装饰器的
    `_idn_scoped` 同名同义（本函数是模块级函数、适配器是参数，不能用那边的装饰器；
    标记供将来按标记清点接线时不含漏）。"""
    fn._idn_scoped = True
    return fn


@_idn_scoped_mark
def send(wechat, chat_id: str, text: str, cfg=None, timeout: float = 60.0) -> tuple:
    """发一条**真语音条**。⇒ (ok, 说明, info)

    流程：查三闸 → 合成 → 播进虚拟声卡（同时微信在录音）→ 点发送 → **只认 DB 回读 `type=语音`**。
    契约：`wechat` 若提供 `_idn_txn_begin/_idn_txn_end`（产品适配器必有），
    会话闸段会包事务；缺省对象（测试桩）没有时**跳过事务不报错**（getattr 兜底）。
    """
    c = _cfg(cfg)
    if not c.get("enabled"):
        return False, "真语音条默认关（控制台「语音回复」里打开「发真语音条」）——关了就不发，不假装。", {}
    # ★ **几何变化跟踪**：用户平时不改缩放/分辨率，但**改了之后落点会全偏且不报错**。
    #   ⇒ 每次真发之前比一次"几何签名"（几次便宜的系统调用）；变了就**立刻重测**，
    #     不等下次开机。⛔ 这一步**不许抛**（发语音条不能被它挡）。
    try:
        from . import display_metrics as _dmx
        _g = _dmx.check_geometry_change(int(getattr(wechat, "hwnd", 0) or 0), log=log)
        if _g.get("changed"):
            try:
                _hp = _popup_hwnd(wechat) if "_popup_hwnd" in globals() else 0
            except Exception:  # noqa: BLE001
                _hp = 0
            measure_landing_points(_hp)
    except Exception:  # noqa: BLE001
        pass
    # ⛔ 闸（发布前静态审计发现）：真语音条＝**真鼠标（`ui_adapt.click_real_hold`）
    #   + 真键盘（`SendInput` 右 Alt）**，天然需要"微信在前台"且会动用户光标；而它原来只查
    #   ⇒ 与朋友圈那条同口径：`background_only=开`（默认）时**如实拒绝**，并把替代路写清楚。
    # ⚠️ 更正：`background_only` 这道闸原来把所有真语音条都当"真鼠标+真键盘路"
    #   一刀拒掉。但**投递档（enter_via=posted/auto_posted）不动光标、不注入键盘、不借前台**
    #   ⇒ 与"只走后台"的诉求不矛盾，**不该被它拦**。只有 alt/真点那些借前台的档才拦。
    _want_pre = str((_cfg(cfg).get("enter_via") or "posted")).lower()
    _posted_pre = _want_pre in ("posted", "auto_posted")
    try:
        _cfg_all = (cfg if isinstance(cfg, dict) else None)
        if _cfg_all is None:
            from .config import get_config
            _cfg_all = get_config() or {}
        if (not _posted_pre) and bool((_cfg_all.get("wechat") or {}).get("background_only", True)):
            from . import bg_status as _bg
            return False, (_bg.background_only_reason("发真语音条") +
                           "（这条档是真鼠标+真键盘的路：会点到录音按钮、还要靠右 Alt 注入。）"
                           "替代：把 voice_strip.enter_via 设成 posted（纯投递、不动鼠标、不借前台），"
                           "或关掉「只走后台」再发，或改发文字/文件。"), {}
    except Exception as _e: # noqa: BLE001
        return False, "读「只走后台」开关失败（按拒绝处理）：%s" % str(_e)[:60], {}
    st = status(cfg)
    if not st["ok"]:
        return False, st["why"], st
    if not str(text or "").strip():
        return False, "要念的内容是空的", {}
    try:
        from . import voice_models as vm
        path, err, info = vm.make(str(text).strip(), cfg=cfg, timeout=int(timeout))
    except Exception as e:
        return False, "合成失败：%s" % e, {}
    if not path:
        return False, "合成没产出音频：%s" % (err or "未知"), info or {}
    wav, why2 = _ensure_wav(path)
    if not wav:
        return False, why2, info or {}
    # ⭐⭐ **投递档（posted/auto_posted）完全绕开主窗 gui**（实测钉死）：
    #   投递路只对**独立窗**投 `WM_*`，不需要主窗的任何几何 ⇒ 不需要 `_get_gui()` /
    #   `_update_render_rect()`。而 `_update_render_rect()` 会把**主窗摆回屏幕内**
    #   （实测：最小化档跑一次，主窗 rect 从 -32000 变成 (175,224,1549,1320) 上屏了一瞬）
    #   ⇒ **违反用户判据①「不影响用户的观看」**。
    #   ⇒ 投递档**不碰主窗**：`gui` 置 None、`rw/rh/rb` 给零值（投递算式只用客户区尺寸）。
    want_gate = str((_cfg(cfg).get("enter_via") or "posted")).lower()
    _posted_gate = want_gate in ("posted", "auto_posted")
    gui = None
    if _posted_gate:
        rw, rh, rb = 0, 0, [0.0, 0.0]
        _wx_open, _wx_err = True, ""
    else:
        try:
            gui = wechat._get_gui()
            gui._update_render_rect()
        except Exception as e:
            # 拿不到窗口（微信没开 / 适配器不可用）⇒ 如实失败，绝不抛出去（抛出去连"回退成文件"都没机会）
            return False, "拿不到微信窗口（%s）⇒ 真语音条发不了" % str(e)[:60], info or {}
        r = gui.render_rect or (0, 0, 0, 0)
        rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
    rb = c.get("record_btn") or [0.878, 0.943]
    # ⓿ **独立窗口径（不再碰主窗）**：
    #   ⛔ 原实现这里查"当前会话是不是目标会话"，不对就 `switch_chat_posted` 切过去——
    #      而切会话的实现里有 `_ensure_main_visible`（最小化时会**把主窗还原摊出来**）。
    #      这违反项目铁律（`input_backend.py` 三条硬纪律之三）：
    #        **「主窗只用来找会话，之后一律在独立窗做完；主窗不许最小化，更不许放出来看一眼」**，
    #      也是"发一条语音条把用户主窗顶到前台"的**唯一真因**（调用栈实测钉死）。
    #   ✅ 正解＝照 `send_image_to_popup` / `send_file_blind` 的先例：独立窗**一窗一会话、
    #      标题即会话名** ⇒ **不需要任何会话判据**；直接找那个独立窗，只在它上面投递点击。
    #      找不到独立窗 ⇒ **如实说"没有独立窗"**，绝不退回主窗（宁可不发，不动用户的窗）。
    try:
        name = ""
        try:
            name = wechat.display_name(chat_id) or chat_id
        except Exception:
            name = chat_id
        from . import wechat_windows as _www
        _tname = _www.resolve_title_name(name, wechat) if name else ""
        _pop = _www.find_popup(_tname or name)
        _pop_hwnd = int((_pop or {}).get("hwnd") or 0)
        if not _pop_hwnd:
            return False, ("「%s」没有独立窗 ⇒ 不发（本产品发消息只在独立窗上做，"
                           "绝不去动你的主窗）。先在微信里把该会话开成独立窗再来。"
                           % (name or chat_id)), info or {}
        info = dict(info) if isinstance(info, dict) else {}
        info["popup_hwnd"] = _pop_hwnd
        _pop_tgt = _pop_hwnd  # 投递路唯一目标＝这个独立窗（绝不投主窗）
    except Exception as e:
        return False, "定位独立窗失败（%s）⇒ 不发（不动主窗）" % str(e)[:70], info or {}
    before = _voice_baseline(wechat, chat_id)
    # ⛔ 基线的**集合形态**（"发前最近这几行的 id"）：判据要用**集合差**而不是比大小 ——
    #   标量 `MAX(local_id)` 是按分片/表量的、跟"这个会话"不一定是同一个范围，
    #   拿它比大小既可能恒真（一醒就报成功）也可能恒假（真发出去了却报超时 ⇒ 上层补发文件 ⇒ 收到两条）。
    #   这一份**必须在投递之前**取，而且**两次等待（主窗 + 宽限）都要用它**，
    #   否则第二次等待会把"已经落库的那一行"当成发前就有的 ⇒ 照样假阴性。
    try:
        _before_ids = wechat._db_recent_ids(chat_id, limit=6)
    except Exception: # noqa: BLE001
        _before_ids = set()
    base = peak = 0
    x_used = None
    via = "alt"
    # ★ 要不要借前台：**投递路不需要**（它不动光标、不注入键盘）⇒ 投递档**一概不借**，
    #   免得平白把用户的窗口抢走一下。alt/真点路才借。
    want_up = str(c.get("enter_via") or "posted").lower()
    _posted_mode = (want_up in ("posted", "auto_posted"))
    # ⭐⭐ 投递档**不借**前台，但**必须把"当时的前台"记下来**（只读，一次 `GetForegroundWindow`）：
    #   最小化档要先让独立窗**脱离最小化**（`_OffscreenShown`）才投得进去，而**收窗口那一下**
    #   有可能把激活交给别的窗口 —— 独立窗是主窗的 **owned window**，Z 序里的下一个正是**微信主窗**
    #   ⇒ 用户什么都没做，却看见**主窗被顶到前台**。
    #   投递档过去是 `stashed_fg = 0` ⇒ `_give_back(0)` 直接 return ⇒ **谁都不还、主窗一直留在前台**。
    #   ⇒ 现在记下来，收尾交给 `_give_back`。它自带三条安全线（用户自己在操作就不抢；
    #     stash 若是"微信自己"也不还；还完按 `_minimize_back_if_needed` 的登记收尾）⇒ 正常情况
    #     （前台没被顶动）它只读一次就退出，**不产生任何 `SetForegroundWindow` 调用**。
    stashed_fg = (_fg_current() if _posted_mode else _borrow_foreground(gui))
    # ★★ 投递档 + 窗口最小化 ⇒ **让它"脱离最小化但人看不见"**（三动作共用，别每枪都做一遍）：
    #    ① **首选"全透明档"**（`_InvisibleShown`）：显示在**它自己的原位**、`alpha=0` ⇒
    #       用户看不到；而微信自己那一下激活落到**这个看不见的窗**上，
    #       **屏内的主窗不会被顶到前台**（实测：`fg` 三次采样都没变）。
    #    ② **兜底"屏外档"**（`_OffscreenShown`）：透明设不上（老系统/样式冲突）时，
    #       仍把窗口移到屏幕外显示 —— 也能投递，代价是主窗可能被微信顶到前台一下
    #       （由 `_fg_watch_back` 立刻还回去）。
    #    ⛔ 只在**最小化档**才做（可见档本来就能投递，不必动窗口）。
    _box = None
    _hide = ""
    if _posted_mode and _pop_tgt:
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            if _ct.windll.user32.IsIconic(_wt.HWND(int(_pop_tgt))):
                info = dict(info) if isinstance(info, dict) else {}
                _box = _InvisibleShown(int(_pop_tgt), log=None)
                _box.__enter__()
                _hide = "invisible" if _box.shown else ""
                if not _box.shown:
                    _why1 = _box.why
                    _box = _OffscreenShown(int(_pop_tgt), log=None)
                    _box.__enter__()
                    _hide = "offscreen" if _box.shown else ""
                    if not _box.shown:
                        # ⛔ **异常/失败时不许把 `_box` 置 None**：置空会让文件尾 `finally` 里的
                        #    `_box.__exit__` 被跳过 ⇒ 被移动到屏外显示的窗口**永远回不来**
                        #    （它既不在屏内、也不是最小化态 ⇒ 用常规方式调不回来，只能关掉重开）。
                        info["offscreen_why"] = "透明档失败（%s）；屏外兜底也失败（%s）" % (
                            _why1, _box.why)
                info["hide_mode"] = _hide or "none"
        except Exception as _e_box:
            # ⛔ 同上：**保留 `_box`**，还原交给 `finally`（那里无论成败都还原）。
            info = dict(info) if isinstance(info, dict) else {}
            info["offscreen_why"] = "显示（透明/屏外）异常（仍会还原）：%s" % str(_e_box)[:60]
    try:
        # ⓵ 开录前先确保**不在**录音态：上一轮若没退干净，再点那个圆圈就变成"结束/取消"（实测踩过）
        #    ⚠️ 投递档**不做这个"抓帧判态"**：`_green_cluster` 走的是**主窗**渲染区
        #       （会把主窗顶出来、且独立窗的态它根本读不到）⇒ 投递档一律**直接点一次 ✕ 复位**
        #       （✕ 只在录音态存在；不在录音态时点它落在空白处，无副作用）。
        try:
            if _posted_mode:
                _posted_cancel(gui, rw, rb, chat_id, name, hwnd=_pop_tgt, ensure_live=False)
                time.sleep(0.8)
            elif _green_cluster(gui):
                _cp = _cancel_point_live(gui, rw, rh, rb)
                if _cp:
                    _click(wechat, gui, _cp[0], _cp[1], c) # 点 ✕ 退出来
                time.sleep(0.8)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        # ⓶ 进录音态：**默认走右 Alt（不动鼠标）**；不认右 Alt 的版本才退回真点
        want = want_up
        ok_in, why_in, x_used = False, "", None
        if want in ("posted", "auto_posted"):
            # ★ 纯投递路（实测：可见非前台 + 最小化两档都通）
            _tgt = _posted_target_hwnd(gui, chat_id, name, hwnd=_pop_tgt)
            ok_in, why_in, x_used = _enter_record_posted(wechat, gui, cfg, hwnd=_tgt,
                                                         ensure_live=False)
            via = "posted"
        elif want in ("alt", "auto"):
            ok_in, why_in, x_used = _enter_record_alt(wechat, gui, cfg)
            if ok_in:
                via = "alt"
        if not ok_in and want in ("alt", "auto"):
            if want == "alt" and bool(c.get("fallback_click", True)):
                ok2, why2, x2 = _enter_record(wechat, gui, cfg, rw, rh)
                if ok2:
                    ok_in, why_in, x_used, via = ok2, why2, x2, "click"
                else:
                    why_in = "%s；退回首击也不成：%s" % (why_in, why2)
            elif want not in ("alt", "auto"):
                ok_in, why_in, x_used = _enter_record(wechat, gui, cfg, rw, rh)
                via = "click" if ok_in else "click"
        # ⚠️ ：**投递档在"打出去了但看不出"时不许硬失败** ——
        #   最小化/离屏时抓不到真帧（帧是标题条桩）⇒ `_green_cluster` 必瞎 ⇒
        #   `_enter_record_posted` 返回的是"不确定"，不是"没进态"。这时**继续往下走**
        #   （播音频 → 点发送），最终以 **DB 回读** 论成败（与项目铁律一致：不靠画面下死结论）。
        _posted_uncertain = (via == "posted") and (not ok_in) and ("打出去了" in str(why_in))
        if not ok_in and not _posted_uncertain:
            if via == "posted":
                return False, ("投递没能进录音态（%s）——落点算不出或投递目标窗口拿不到。" % why_in), info or {}
            return False, ("没能进录音态（%s）——按住右 Alt 与真点两条路都试过了。"
                           "Alt 路的前提：**微信在前台的那几秒别切窗口**。" % why_in), info or {}
        # ⓷ 边录边播（**投递档不量音量点** —— 量它必须抓主窗帧，会违反"不影响观看"）
        if via == "posted":
            okp, whyp = play_to_cable(wav)
            base, peak = 0, 0
            _meter_usable = False
        else:
            (okp, whyp), base, peak = _play_with_meter(gui, wav)
        if not okp:
            if via == "alt":
                _cancel_alt(gui)
            elif via == "posted":
                _posted_cancel(gui, rw, rb, chat_id, name, hwnd=_pop_tgt, ensure_live=False)
            else:
                try:
                    _cp = _cancel_point_live(gui, rw, rh, rb)
                    if _cp:
                        _click(wechat, gui, _cp[0], _cp[1], c)
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    pass
            return False, whyp, info or {}
        # 音量点守卫：**投递档一律不用**（见上：量它必须抓主窗帧 ⇒ 违反"不影响观看"；
        #   且录音态音量点属于独立窗，主窗帧里根本没有）。最终仍以 DB 回读为准。
        if bool(c.get("meter_guard", True)) and _meter_usable and peak <= base:
            if via == "alt":
                _cancel_alt(gui)
            elif via == "posted":
                _posted_cancel(gui, rw, rb, chat_id, name, hwnd=_pop_tgt, ensure_live=False)
            else:
                try:
                    _cp = _cancel_point_live(gui, rw, rh, rb)
                    if _cp:
                        _click(wechat, gui, _cp[0], _cp[1], c)
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    pass
            return False, ("音频**没进到微信的麦克风**（录音那条音量点一直没亮：底色 %d、播放峰值 %d）"
                           "⇒ 已取消，不给你发一条静音语音条。查一下微信的麦克风是不是选成了"
                           "「CABLE Output」（关掉这道校验：voice_strip.meter_guard）" % (base, peak)), info or {}
        time.sleep(0.5)
        if via == "alt":
            _send_alt(False) # ⓸ 松开右 Alt＝发送
        elif via == "posted":
            # ⓸ **点绿色 ↑ 发送**（⭐ 用户 澄清的第三个动作："声音输完之后，
            #    点那个绿色的发送键"）。
            #    ⛔ 不是"松手"（本轮已作废"按住"那套）；⛔ 也不是"录音里抓帧找绿↑"
            #      （最小化档抓不到帧）⇒ 用**同一套"距右缘像素"**直接算绿↑落点。
            #    实测：绿↑ 中心距客户区右缘 ≈ 与语音键相近（同在右侧图标簇）；
            #      `st_03_hold.png` 绿↑ 中心帧内 x=850/919 ⇒ 距右缘 69px(物理) ⇒ 逻辑 46。
            ok_send, why_send = _posted_send(gui, rw, chat_id, name, hwnd=_pop_tgt, ensure_live=False)
            if not ok_send:
                return False, ("投递点绿↑没打出去：%s —— 已尽力取消" % str(why_send)[:80]), info or {}
        else:
            g = _green_send(gui) or _green_center(gui) # ⓸ 点绿色发送 ↑
            if not g:
                try:
                    _cp = _cancel_point_live(gui, rw, rh, rb)
                    if _cp:
                        _click(wechat, gui, _cp[0], _cp[1], c)
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    pass
                return False, "录音态里找不到绿色发送按钮（已取消，没发出去）", info or {}
            _click(wechat, gui, g[0], g[1], c)
    except Exception as e:
        return False, "微信侧操作失败：%s" % e, info or {}
    finally:
        # ⛔⛔ **顺序不能反**：先**把窗口收回去**，再**还前台**。
        #   反过来的话，"收窗口/收最小化"那一步可能又把激活交给别的窗口（独立窗的 owner ＝主窗）
        #   ⇒ 我们刚还完前台、立刻又被顶掉 —— 用户看到的结果是"还了等于没还"。
        #   ✅ 先 `_box.__exit__`（把独立窗收成最小化）⇒ 前台该落到哪就落到哪 ⇒ 最后统一还一次。
        # 屏外显示的窗口**无论成败都精确还原**（原位置 + 原显示态）
        try:
            if _box is not None:
                _box.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        # 借走（或被顶掉）的前台**无论成败都还回去**（用户原来的窗口不能被我们占着）
        try:
            _give_back(stashed_fg)
        except Exception:
            pass
    # ⚡ 超时按**产物大小**分开定：文字 ~0.4s 落库；图/表情 30s~3min）。
    #   语音条是小 blob，跟文字一个量级 ⇒ 默认 12s 足够；配置可改（voice_strip.confirm_timeout）。
    _to = 12.0
    try:
        _to = float(c.get("confirm_timeout") or 12.0) or 12.0
    except Exception:
        _to = 12.0
    ok, msg = _wait_voice(wechat, chat_id, before, timeout=_to, before_ids=_before_ids)
    if not ok:
        # ⛔ **判否之前先给"慢落库"一次宽限**（语音条写库晚到窗外是常态）：
        #   不宽限就直接报失败 ⇒ 上层按 `fallback_file` 再补发一个语音文件 ⇒ 用户收到两条。
        #   ⚠️ 宽限这一轮**必须复用同一份发前集合**（见上面 `_before_ids` 的注释），
        #   否则"已经落库的那行"会被当成发前就有的 ⇒ 白宽限。
        _grace = 6.0
        try:
            _grace = float(c.get("confirm_grace") or 6.0)
        except Exception: # noqa: BLE001
            _grace = 6.0
        if _grace > 0:
            _ok2, _msg2 = _wait_voice(wechat, chat_id, before, timeout=_grace,
                                      before_ids=_before_ids, kinds=("语音",))
            if _ok2:
                ok, msg = True, ("%s（**超出主判据窗**才落库，宽限 %.0fs 内确认到）" % (_msg2, _grace))
    if isinstance(info, dict):
        info = dict(info)
        info["meter"] = {"base": base, "peak": peak}
        info["record_x"] = x_used
        info["enter_via"] = via
    return ok, (msg if ok else ("语音条没发出去：%s" % msg)), info or {}


def _voice_baseline(wechat, chat_id: str):
    """发前记基线：**优先标量**（`MAX(local_id)`，一个 int、不碰大 BLOB）⇒ 快；

    适配器没有那条快路（老桩/单测桩）时退回 `_latest_voice_seq`（读最后 20 条找语音）。
    返回 int 或 None（None＝拿不到基线，`_wait_voice` 会自己现取）。
    """
    _sq = getattr(wechat, "_db_max_local_only", None)
    if callable(_sq):
        try:
            v = _sq(chat_id)
            if v is not None:
                return v
        except Exception:
            pass
    return _latest_voice_seq(wechat, chat_id)


def _latest_voice_seq(wechat, chat_id: str):
    """当前会话里最后一条**语音**的 id（回读判据的基线；**慢路，仅兜底**）。"""
    try:
        rows = wechat._db.get_messages(chat_id, limit=20) or []
        for row in rows:
            if str(row.get("type") or "") == "语音":
                return row.get("local_id") or row.get("id")
        return 0
    except Exception:
        return 0


def _wait_voice(wechat, chat_id: str, before, timeout: float = 12.0,
                before_ids=None, kinds: tuple = ()) -> tuple:
    """等 DB 里出现**新的**语音行（只认回读，不信 GUI）。

    ⚠️ （差点造成"明明发出去了却报失败，然后按回退设置又发一个文件"）：
    旧实现在第一次查库时**只要第一条语音行还是旧的就直接 return False** —— 而微信**写库有延迟**，
    松手那一刻库里通常还是上一条 ⇒ 假阴性。

    ⚡ **换成"事件 + 标量"的快判据**（固定间隔轮询太慢，改成"有写入就立刻确认"）：
      老实现是 `while 12s: 读一次 20 条消息; sleep 1s` —— 既**慢**（1s 一拍、每次把 20 条连
      `message_content` 大 BLOB 一起读出来）又**可能假失败**（固定拍点错过落库瞬间）。
      正解＝**标量增量 + `-wal` 写入事件**（见 `wechat.wait_new_message`）：
        发前记 `MAX(local_id)` 基线（只读标量）⇒ 发后**监听 `message_*.db-wal`**，
        一有写入**立刻**查一次标量，`> 基线` 即命中；**超时才判失败**。
      这里**优先走适配器的 `wait_new_message`**；老桩没有它时才退回"标量/列表轮询"（仍比原来快）。
    """
    # ① 优先：适配器的"事件 + 标量"快路（新产品适配器都有）
    _fast = getattr(wechat, "wait_new_message", None)
    if callable(_fast):
        try:
            ok, why, info = _fast(chat_id, base=before, timeout=float(timeout),
                                  kinds=tuple(kinds or ()),
                                  base_ids=(set(before_ids) if before_ids else None))
            return bool(ok), why
        except Exception as _e: # noqa: BLE001 — 快路自己坏了不许把结论带偏 ⇒ 退回轮询
            pass
    # ② 兜底：标量轮询（只读 int；拿不到标量才退回老列表法）。间隔 50ms，比原来 1s 快得多。
    t0 = time.time()
    _reads = _err = 0
    _sq = getattr(wechat, "_db_max_local_only", None)
    _ok_any = False
    while time.time() - t0 < timeout:
        if callable(_sq):
            try:
                cur = _sq(chat_id)
                _reads += 1
                if cur is None:
                    _err += 1
                else:
                    _ok_any = True
                    if before in (None, 0) or int(cur) > int(before):
                        return True, ("DB 回读确认：新语音条 local_id=%s（%.2fs）"
                                      % (cur, time.time() - t0))
            except Exception:
                _err += 1
        else:
            try:
                _getm = getattr(wechat, "_db_messages", None)
                rows = ((_getm(chat_id, limit=20) if callable(_getm)
                         else wechat._db.get_messages(chat_id, limit=20)) or [])
                _reads += 1
                _ok_any = True
                for row in rows:
                    if str(row.get("type") or "") == "语音":
                        cur = row.get("local_id") or row.get("id")
                        if before in (None, 0) or (cur and str(cur) != str(before)):
                            return True, "DB 回读确认：新语音条 local_id=%s" % cur
                        break
            except Exception:
                _err += 1
        time.sleep(0.05)
    if not _ok_any:
        # ⛔ 一次标量/列表都没读成功 ⇒ 判据不可用。此时说"没发出去"会让上层按回退设置再发一个文件
        #    （重复/内容不符），所以如实说"判据不可用、请按现场确认"。
        return False, ("判据不可用：这 %.0f 秒里**一次都没读成功**会话库 ⇒ "
                       "不能据此判断语音发出去了没有" % timeout)
    # 读得通、但一直没见到新行 ⇒ **确实超时**（与"判据不可用"分开说，语义不同）
    return False, ("超时：%.1f 秒内库里没出现新的语音行（基线 %s）" % (timeout, before))
    return False, "等了 %.0f 秒，DB 里没出现新语音条（最新还是 %s）" % (timeout, last)
