# -*- coding: utf-8 -*-
"""右下角鲸鱼挂件窗：Qt 透明顶层小窗，内嵌 WebView2 渲染上游挂件脚本。

结构：

    WhaleWidget (QWidget, Qt.Tool 顶层窗，置顶)
      └── 透明方窗，440×560 逻辑像素
            └── WhaleHostWebView（whale_host.py）在窗口 HWND 上挂 WebView2，
                加载宿主页 → 宿主页引入原版 widget.js → 挂件自己渲染

窗口比本体大：本体由上游 CSS 钉在视口下沿、尺寸随视口变（`--dshw-base`），
多出来的地方全是给弹层（菜单向上展开）留的，本体的**真实几何一律以页面上报为准**。

职责边界：窗口（显隐/位置记忆/收起圆点/吸附翻转）留在本模块；浏览器（环境/控制器/
页面加载）在 `whale_host.py`；挂件自身的菜单、音效、角色、用量全部由原版
脚本负责，本模块不重现任何一条。脚本文件 = `whale-widget/client/widget.js`
（vendor 自上游，仅一处移植补丁，见仓库内移植记录）。

降级：WebView2 不可用（运行库被卸载、程序集缺失、初始化异常）、控制台端口
不可达、页面未渲染出挂件本体时，退回**信息卡**：一张静态形象图 + 一行可操作
说明，不留纯白/纯黑/全透明空窗。
"""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

# ⛔ **模块级导入**，不能放进某个方法里：`build_host_html` 在 `_boot_webview`
# 导入的话，`_after_host_ready`（另一个方法）里用到它就是 NameError——
# 且该路径只有在「控制台正在运行」时才走得到（端口探活/认人两道闸会先返回），
# 平时自检全是静态判据 ⇒ 缺陷被长期掩盖。whale_host 顶层无 ctypes/comtypes，
# 导入即安全。
from whale_host import WhaleHostWebView, build_host_html  # noqa: PLC0415

ROOT = Path(__file__).resolve().parent.parent
_ASSET = ROOT / "whale-widget" / "assets" / "DSniang1.png"
_SET = ("WXAgent", "persona-morph-ui")

# 原版窗口边长（历史值，只用于把老版位置记忆换算过来）。
#
# ⛔ **不要拿它当"本体尺寸"**：上游 `--dshw-base = clamp(122px, calc(min(250px,
#    min(100vw,100vh) * 0.28) * var(--dshw-scale)), 625px)` —— 本体尺寸随**视口**
#    变，440×560 的窗口里本体只有 ≈123px。凡是要用本体几何的地方一律读页面上报的
#    root 矩形（`_whale_size()`），按常量猜会让吸附提前一百多像素触发。
_BASE_LEGACY = 250
# 页面还没上报几何之前的兜底本体边长（`--dshw-base` 在本窗口尺寸下的实测值）。
_WHALE_FALLBACK = 123
# 宿主窗 / 页面尺寸。
#
# ⛔ 本体由上游 CSS 钉在视口**下沿**，多出来的高度全是给弹层（菜单向上展开）留的。
#    为什么宽度冻结在 440：本体尺寸取 `min(100vw,100vh)` —— 视口宽小于高时由宽决定，
#    改宽就会把本体一起顶大。所以窗口只允许**长高**，绝不动宽。
_WIN_W, _WIN_H = 440, 560
# 增高护栏：小于它不动手（不为几像素反复改尺寸）；改尺寸前留一拍复核（瞬时元素
# 不该把窗口撑大）；多留的余量。
_FIT_DEADBAND = 12
_FIT_CONFIRM_MS = 350
_FIT_PAD = 10
# 换边回执的等待上限（页面没回话就认下并补偿）
_SIDE_CONFIRM_MS = 600


class WhaleWidget(QWidget):
    """无边框置顶小窗，内嵌 WebView2 跑**原版挂件**（点挂件本体即原版菜单）。"""

    W, H = _WIN_W, _WIN_H

    def __init__(self, t, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("WhaleWidget")
        # ⛔ **必须置顶（WindowStaysOnTopHint）**：为「控制台最小化挂件还要显示」
        #    去掉了 parent —— 但无主的 Tool 窗在 Windows 的 Z 序里是**普通顶层窗**，
        #    主窗随后 show/activate 就把它压到下面；挂件又恰恰落在主窗右下角
        #    （最大化后覆盖的位置）⇒ **被主窗整个盖住**，用户眼里「完全看不见」
        #    。悬浮挂件就该浮在
        #    最上层 —— web 原版是页内浮层（z-index 9999），语义一致；不想看时
        #    点减号收起。
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        # 透明窗：挂件是悬浮件，必须让底层界面透出来。
        # 配套动作在 whale_host._enable_transparency() —— WebView2 的画面是窗口
        # 合成出来的，不把它的 DefaultBackgroundColor 设成 A=0，内核图层就合不上，
        # 表现是「窗口透明但鲸鱼不出现」。两者必须成对改，缺一个都不出画面。
        # 半透明窗：降级卡浮在桌面上（无灰块底）。合成承载下 WebView 无子窗、
        # 画面进 DComp 视觉树，同样与此属性兼容（画面 alpha 由 DWM 合成）。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True) # 无按键的移动也要收（hover/拖动中段）
        self.setFixedSize(self.W, self.H)
        self._drag0: QPoint | None = None
        self._win0: QPoint | None = None
        self._moved = False
        self._lbtn = False # 物理左键是否按住（注入给内核的 MOVE 要带这个状态）
        self._host = None
        self._err = ""
        self._booted = False
        # 页面加载是否失败 —— 与「控制器是否就绪」分开记：paintEvent 靠它决定
        # 要不要画降级卡（只看 `_host.ok` 时，半成品状态会留一个纯黑窗口）。
        self._load_failed = False
        # 页面是否回报「鲸鱼本体渲染出来了」—— 与 _load_failed 互斥推进：
        # boot ok=True 会浇灭 watchdog，False 则直接转降级。
        self._boot_ok = False
        # 可命中区（页面报告的矩形，CSS 像素 = Qt 逻辑像素）。空 = 页面还没报，
        # 由 `_veil_rects()` 退化成「本体所在的那块方形」。
        self._veil: list = []
        # 本体几何真值（页面上报，视口坐标）：[x, y, w, h]；None = 页面还没说话。
        # 宿主侧一切"本体几何"（吸附、翻转、落位、位置记忆、可命中底兜底）都从它算，
        # 因为本体尺寸随视口变、不等于窗口尺寸。
        self._whale: list | None = None
        # 弹层相对本体的四向溢出量 [左,上,右,下] 与"是否有弹层开着"（窗口长高的判据）
        self._need = [0, 0, 0, 0]
        self._pop = False
        # 本体钉在窗口哪一边（"left"/"right"）。窗口比本体宽得多，本体贴屏幕哪一侧就
        # 钉哪一边 —— 窗口才不会挂到屏幕外、菜单也才朝屏幕内侧展开。
        # `_pending_side` = 已发给页面、等回执的换边请求（见 `_set_side`）。
        self._side = "right"
        self._pending_side: str | None = None
        self._side_keep: QPoint | None = None
        # 窗口高度（只增不减）与"待复核的增高需求"（见 `_fit_window`）
        self._fit_h = _WIN_H
        self._fit_pending: int | None = None
        # 页面几何第一次到手后按位置记忆精确落位一次（此前用的是兜底尺寸）
        self._anchor_applied = False
        # 用户是否亲手拖过 —— 拖过就不再按默认角重排（不打扰用户摆放）
        self._user_moved = False
        # 页面转发拖动：页面只说开始，位置由**跟真实光标**的定时器算（见 _drag_tick）
        self._drag_cursor: QPoint | None = None
        # 按下那一刻本体左上角的屏幕坐标 —— 拖动的锚（不是窗口锚：本体在窗口内换边，
        # 窗口左上角与本体左上角不是同一个点）
        self._drag_whale: QPoint | None = None
        self._drag_t0 = 0.0
        self._flip_left: bool | None = None # 本体镜像态（原版 dshwv-left）
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(16)
        self._drag_timer.timeout.connect(self._drag_tick)
        # 窗口自适应：弹层需要多少竖向空间就长多少（只增不减），本体屏幕位置不动
        self.restyle(t)

        # 位置：上次拖到哪就还在哪；**越界/坏记录落右下角**。
        # ⛔ 必须校验上屏：实测 QSettings 里存过 (1611,1431) —— 在 1080p 屏上
        #    y=1431 已在屏幕底边之外，挂件"一直在显示、只是在屏幕外面"，
        #    用户眼里就是「没看到挂件」。位置记忆跨分辨率/换屏后天然可能越界。
        #    这里先按**兜底本体尺寸**落位，页面几何一到手 `_apply_anchor()` 会按真实
        #    尺寸重算一次（本体尺寸 123 与历史窗口边长 250 差着一倍，只靠兜底会偏）。
        anchor = self._read_anchor(QSettings(*_SET))
        moved = False
        if anchor is not None:
            w, h = self._whale_size()
            wx, wy = int(anchor[0]) - w, int(anchor[1]) - h
            if self._onscreen(wx, wy):
                self._place_whale(wx, wy)
                moved = True
        if not moved:
            self._move_default()

    # ------------------------------------------------------- 本体几何（页面真值）

    def _whale_size(self) -> tuple:
        """本体尺寸（逻辑像素）—— 页面上报的优先，没报就用兜底值。

        ⛔ 不能用窗口尺寸或任何常量代替：`--dshw-base` 随视口变，440×560 的窗口里
        本体只有 ≈123px 见方。用错尺寸的直接后果是吸附/翻转/上屏校验全部按错的地方
        算（拖到屏幕边缘像"卡住"、吸附"时灵时不灵"）。
        """
        if self._whale is not None:
            try:
                return int(self._whale[2]), int(self._whale[3])
            except (TypeError, ValueError, IndexError):
                pass
        return _WHALE_FALLBACK, _WHALE_FALLBACK

    def _whale_offset(self, side: str | None = None) -> tuple:
        """本体左上角在**窗口内**的偏移（视口坐标）。

        本体横向钉在窗口的左或右（页面上用 `!important` 定死），纵向下沉到窗口下沿。
        按 `_side` 而不是页面上报的坐标算，是为了避开上报延迟：换边那一瞬间若还用旧
        坐标落位，窗口会先飘一下再被下一拍纠正。
        """
        w, h = self._whale_size()
        s = self._side if side is None else side
        ox = 0 if s == "left" else max(0, self.width() - w)
        oy = max(0, self.height() - h)
        return ox, oy

    def _whale_rect(self) -> QRect:
        """本体在**屏幕**上的矩形（吸附、翻转、落位、位置记忆的唯一几何依据）。"""
        w, h = self._whale_size()
        ox, oy = self._whale_offset()
        return QRect(self.x() + ox, self.y() + oy, w, h)

    def _place_whale(self, wx: int, wy: int) -> None:
        """把本体左上角放到屏幕 (wx, wy)（窗口随之挪；本体在窗口内的偏移由 `_side` 定）。"""
        ox, oy = self._whale_offset()
        self.move(int(wx) - ox, int(wy) - oy)

    def _set_side(self, side: str) -> None:
        """决定本体钉在窗口的哪一边，并同步给页面（**异步 + 回执**）。

        为什么要有"边"这个概念：上游把本体钉死在视口右下、弹层一律朝左/上展开。
        本体若贴屏幕左缘，窗口就得挂到屏幕外，菜单跟着被裁掉一半。改成"本体在屏幕
        左半就钉窗口左边、在右半就钉右边"，窗口永远留在屏内，菜单永远朝屏幕内侧展开，
        翻转方向也自然正确（贴左必翻、贴右不翻）。

        ⛔ 位置是 `窗口位置 + 本体在窗口内的偏移`，换边会把这半个和数改掉 300 多像素。
        所以**不能**本地立刻改口径：必须等页面真的换完边（回执）再把窗口挪过去补偿，
        否则那一瞬间本体在屏幕上会跳 300 多像素。回执没来就等超时兜底。
        """
        want = "left" if side == "left" else "right"
        if want == self._side:
            self._pending_side = None
            return
        self._side_keep = self._whale_rect().topLeft() # 换边前后本体屏幕位置不变
        self._pending_side = want
        host = self._host
        if host is None or not host.ok:
            # 没有页面可同步（降级态 / 还没建链）：直接认下，没什么可补偿的
            self._pending_side = None
            self._side = want
            return
        try:
            ok = host.execute_script(
                "(function(){try{window.__pmSetSide&&window.__pmSetSide(%r);"
                "if(window.chrome&&window.chrome.webview)"
                "window.chrome.webview.postMessage({pm:'side',s:%r});"
                "}catch(e){}})();" % (want, want))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            self._pending_side = None
            self._side = want
            return
        QTimer.singleShot(_SIDE_CONFIRM_MS, self._side_timeout)

    def _on_pageside(self, side: str) -> None:
        """页面回执「已经换边」→ 此刻按新偏移把窗口挪过去，本体屏幕位置一动不动。"""
        want = "left" if side == "left" else "right"
        self._pending_side = None
        if want == self._side:
            return
        self._side = want
        # ⛔ 本方法是**从 WebView2 的消息回调里**被调的，挪窗会走 moveEvent → 内核 API，
        #    重入不得 ⇒ 推到事件循环下一拍。
        QTimer.singleShot(0, self._side_settle)

    def _side_settle(self) -> None:
        """按新偏移把窗口摆回去：本体屏幕位置不变，只有窗口位置被补偿。"""
        keep = self._side_keep
        if keep is None:
            return
        try:
            self._place_whale(keep.x(), keep.y())
        except Exception:  # noqa: BLE001
            pass

    def _side_timeout(self) -> None:
        """页面没回话的兜底：认下并补偿 —— 宁可有一次小跳，也不能一直卡在旧偏移。"""
        if self._pending_side is None:
            return
        self._on_pageside(self._pending_side)

    def _screen_geo(self):  # noqa: ANN201
        """本窗所在屏幕的可用区（取不到返回 None）。"""
        try:
            scr = self.screen()
            return scr.availableGeometry() if scr is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _clamp_whale_y(self, y: int) -> int:
        """本体的纵向范围夹在屏幕可用区内（本体是百来像素的小方块，没必要让它跑出屏）。"""
        g = self._screen_geo()
        if g is None:
            return int(y)
        _w, h = self._whale_size()
        return int(max(g.top(), min(int(y), g.bottom() + 1 - h)))

    # ------------------------------------------------------------ 外观

    def restyle(self, t) -> None:  # noqa: ANN001
        """主题切换：本模块只有降级卡用字体，原版挂件自身配色由脚本决定。"""
        self.t = t
        self._base_font = self._resolve_font(t)
        self.update()

    @staticmethod
    def _resolve_font(t):  # noqa: ANN001
        try:
            from stylekit_qt import qfont  # noqa: PLC0415

            return qfont(t, 16)
        except Exception:  # noqa: BLE001 — 主题对象异常不影响出图
            f = QFont()
            f.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "sans-serif"])
            return f

    def _veil_rects(self) -> list:
        """要铺**可命中底**的矩形。

        页面报来的（本体 + 当前可见弹层）优先；页面还没报就退化成「本体所在的那块
        方形」—— 尺寸用**本体兜底尺寸**而不是窗口尺寸：铺大了等于用一块看不见的
        方形吃掉桌面鼠标。
        """
        if self._veil:
            return self._veil
        ox, oy = self._whale_offset()
        w, h = self._whale_size()
        return [(ox, oy, w, h)]

    def paintEvent(self, _e) -> None:  # noqa: N802
        """在**可命中区**上铺一层极淡的底；画面由 WebView2 内核合成在它之上。

        ⛔ 这一层不是为了好看，是为了**让窗口能被点到**：半透明窗在 Windows 上
        按像素 alpha 做命中测试，**alpha 为零的地方鼠标消息直接穿过去**。而本窗口
        正常态什么都不画（画面全在 WebView2 子窗里）⇒ 系统眼里"没有可点的表面"
        ⇒ **连子窗也一起收不到鼠标**，表现就是「看着一切正常，却点不动、拖不动」。
        降级态之所以一直能拖，正是因为提示卡本身有像素。

        ⛔ 只铺在**页面报告的矩形**上（本体 + 可见弹层），不是整窗：铺到哪、哪才能
        被点到 —— 整窗铺满等于用一块看不见的方形吃掉桌面的鼠标；只铺本体与弹层，
        其余保持全透明，桌面照常可点。alpha=1/255 肉眼不可见。

        判据是「**页面真的加载成功了**」而不是「控制器建好了」：
        `WhaleHostWebView.ok` 只说明 WebView2 环境/控制器就绪，**不代表
        `navigate_to_string` 成功**。只看 `.ok` 时，"控制器就绪但页面没内容"
        的半成品状态两头落空：Qt 侧不画降级卡、WebView2 侧没内容 ⇒ 空窗。
        `load_failed` 把"页面没起来"单独记下来，让降级卡在这种状态下兜底。
        """
        p = QPainter(self)
        for x, y, w, h in self._veil_rects():
            p.fillRect(QRect(int(x), int(y), int(w), int(h)), QColor(0, 0, 0, 1))
        if self._host is not None and self._host.ok and not self._load_failed:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_fallback(p)

    def _on_rects(self, rects, whale=None, need=None, pop=False) -> None:  # noqa: ANN001
        """页面报告几何 → 记下真值、重铺底、按需长高、贴边落位，然后重绘。

        页面在「本体出现/弹层开合/窗口尺寸变化」时上报（见 `whale_host.build_host_html`
        的 `rectWatch`）。解析这里是尽力而为：脏数据一律忽略，退回兜底几何。

        `whale` = 本体视口矩形，`need` = 弹层相对本体的四向溢出量，`pop` = 是否有
        弹层开着 —— 后两个是"要不要给窗口留空间"的**无环判据**（见 `_fit_window`）。
        """
        try:
            self._veil = [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in rects]
        except Exception:  # noqa: BLE001
            return
        if isinstance(whale, (list, tuple)) and len(whale) >= 4:
            try:
                self._whale = [int(v) for v in whale[:4]]
            except (TypeError, ValueError):
                self._whale = None
        if isinstance(need, (list, tuple)) and len(need) >= 4:
            try:
                self._need = [max(0, int(v)) for v in need[:4]]
            except (TypeError, ValueError):
                pass
        self._pop = bool(pop)
        # ⛔ 布局动作（长高 / 落位 / 换边）一律**跳出 WebView2 的回调**再做：它们会
        #    setFixedSize/move，进而调 put_Bounds —— 在 WebView2 的事件回调里同步调它的
        #    API 是官方明令避免的重入（可能死锁或不生效）。
        QTimer.singleShot(0, self._after_geom)
        self.update()

    def _after_geom(self) -> None:
        """页面几何到手后统一落布局（已跳出 WebView2 回调）。"""
        if not self._anchor_applied and self._whale is not None:
            self._anchor_applied = True
            # 构造时用的是**兜底本体尺寸**（猜的），真实尺寸到手后按它重算一次落位。
            # ⛔ 用户已经亲手拖过就不再动：拖拽结束时锚点已按新位置落盘，重放要么是
            #    空操作、要么在慢启动页面上把用户刚摆好的位置拽回去。
            if not self._user_moved:
                # 有位置记忆就按记忆放；没有就按默认角重放（兜底尺寸是猜的）
                if not self._apply_anchor():
                    self._move_default()
        self._fit_window()
        # 拖动中位置归 `_drag_tick` 独占；此刻再吸附会把它拽回去（拖动抖动/卡顿的来源）
        if self._drag_cursor is None:
            self._snap_and_flip(force=True)

    # -------------------------------------------------- 贴边吸附 + 自动翻转

    _SNAP_PX = 40 # 距屏幕左/右边缘多近就吸附

    def _snap_geometry(self, wx: int) -> tuple:
        """给定本体左上角的**自由**屏幕位置，算出（吸附后的 x, 贴哪一边, 是否镜像）。

        规则**照抄原版**（`settle`/`refreshFlip`）：贴左必翻、贴右不翻，自由摆放按
        「图像中心在屏幕左/右半」判断 —— 结果都是让本体朝屏幕内侧。

        ⛔ 两个要点：
        ① 判据必须是**本体自己的矩形**。本体尺寸随视口变（440×560 的窗口里只有
           ≈123px），拿窗口矩形或某个常量当本体宽度，吸附点会差出一百多像素 ——
           表现就是「接近边缘时卡住、吸附时灵时不灵」。
        ② 入参是**自由位置**，不是当前位置。吸附结果一旦回喂给判据，吸住之后就永远
           "贴边"，脱离要么失灵要么抖。
        """
        try:
            g = self._screen_geo()
            if g is None:
                return int(wx), self._side, bool(self._flip_left)
            w, _h = self._whale_size()
            x = int(wx)
            if x - g.left() <= self._SNAP_PX:
                return int(g.left()), "left", True
            if g.right() + 1 - (x + w) <= self._SNAP_PX:
                return int(g.right() + 1 - w), "right", False
            mid = (g.left() + g.right() + 1) / 2.0
            side = "left" if (x + w / 2.0) < mid else "right"
            return x, side, side == "left"
        except Exception:  # noqa: BLE001
            return int(wx), self._side, bool(self._flip_left)

    def _snap_and_flip(self, force: bool = False, free_x: int | None = None) -> None:
        """贴屏幕左右边缘 + 到边自动翻转 + 定下本体贴窗口哪一边。

        幂等：每拍都从**自由位置**重算。`free_x` 传拖动中的自由位置（判据不受吸附
        结果影响，可随时脱开）；不传就按本体当前位置算（停着的时候）。
        """
        try:
            if free_x is None:
                free_x = self._whale_rect().x()
            nx, side, flip = self._snap_geometry(int(free_x))
            # ⛔ 顺序：先落位、再换边。`_set_side` 记的是"换边瞬间本体在哪"，先落位才记
            #    得准；换边本身只改窗口与页面的内部布局，本体的屏幕位置一点不动。
            self._place_whale(nx, self._clamp_whale_y(self._whale_rect().y()))
            self._set_side(side)
            self._set_flip(flip, force)
        except Exception:  # noqa: BLE001 — 吸附/翻转是锦上添花，失败不影响拖动
            pass

    def _set_flip(self, want_left: bool, force: bool = False) -> None:
        """让本体镜像 / 不镜像 —— 原版用 `dshwv-left` 这个类表达镜像（scaleX(-1)）。

        翻转靠一段脚本切类：屏幕几何只有 Qt 知道，而原版自己那套翻转是按**视口**算的
        （在我们的窗口里等于没有），所以由宿主按屏幕几何决定后下发。`force` 用于对抗
        原版自身重排时把类切回去。
        """
        if not force and getattr(self, "_flip_left", None) == want_left:
            return
        self._flip_left = want_left
        host = self._host
        if host is None or not host.ok:
            return
        try:
            host.execute_script(
                "(function(){try{var r=document.querySelector('.dshwv-root');"
                "if(r)r.classList.toggle('dshwv-left',%s);}catch(e){}})();"
                % ("true" if want_left else "false"))
        except Exception:  # noqa: BLE001
            pass

    def _fit_window(self) -> None:
        """窗口跟着弹层长高：弹层在本体之外需要多少竖向空间就长多少。

        ⛔ **判据必须相对「本体」求值**，不能用"矩形有没有超出窗口"：
        窗口一长，超出量就归零 ⇒ 宿主缩回去 ⇒ 弹层再被裁；而窗口尺寸一变，上游页面
        自己的 `resize → settle()` 又把本体重新贴到视口边缘、上报的矩形跟着变。三者
        叠起来是**死循环**，表现就是「点开菜单后窗口上下弹动、菜单上半部分被窗口上沿
        裁掉、每几秒反复一次」。相对本体求出的溢出量对"窗口尺寸"与"本体贴哪一边"都
        不变，因此天然无环。

        两条护栏：**只长不缩**（缩会让窗口上沿下移，把向上展开的菜单裁掉）、
        **宽度冻结**（本体尺寸取 `min(100vw,100vh)`，改宽会把本体一起顶大）。
        增高前留一拍复核（`_fit_confirm`）：瞬时冒出来的元素不该把窗口撑大。

        ⛔ 只看**向上**溢出（`need[1]`）。整体布局是"本体钉在视口下沿"的**底锚**结构：
        窗口长高时窗口只是往上长，视口底边（在屏幕上）不动 ⇒ 菜单/面板的屏幕上位置
        一点不变、上方的空间变大。于是**朝下伸出**的弹层（`need[3]`）长高帮不上忙 ——
        要让它露出来得把本体在窗口内往上抬，而抬了还得让上游重跑 `positionMenu`
        （没导出）。所以 `need[3]` 只作诊断记录，不参与长高。
        """
        try:
            if not self._pop:
                return
            up = max(0, int(self._need[1]))
        except (TypeError, ValueError, IndexError):
            return
        _w, wh = self._whale_size()
        want = int(wh + up + _FIT_PAD)
        if want <= self._fit_h + _FIT_DEADBAND:
            return
        if self._fit_pending == want:
            return # 已在排队复核
        self._fit_pending = want
        QTimer.singleShot(_FIT_CONFIRM_MS, self._fit_confirm)

    def _fit_confirm(self) -> None:
        """复核一拍：需求仍在（页面这几百毫秒没改口）才真的长高；瞬时元素自动作废。"""
        want, self._fit_pending = self._fit_pending, None
        if want is None or not self._pop:
            return
        try:
            up = max(0, int(self._need[1]))
            _w, wh = self._whale_size()
            if wh + up + _FIT_PAD < want - 4:
                return # 需求已消失/变小 ⇒ 不作数
            if want <= self._fit_h + _FIT_DEADBAND:
                return
            g = self._screen_geo()
            th = int(min(want, g.height())) if g is not None else int(want)
            if th <= self._fit_h:
                return
            keep = self._whale_rect() # 长高前后本体在屏幕上的位置不动
            self._fit_h = th
            self.setFixedSize(self.width(), th)
            self._place_whale(keep.x(), keep.y())
            if self._host is not None and self._host.ok:
                self._host.resize(self.width(), th)
        except Exception:  # noqa: BLE001 — 长高失败只是弹层可能被裁，不影响挂件本体
            pass

    def _paint_fallback(self, p: QPainter) -> None:  # noqa: N802
        """降级卡：形象图 + 一行说明（**给出可操作指引**，不做静默空白）。

        画在**本体所在的位置**：卡片比本体略大（要放得下标题和两行说明），以本体的
        右下角为锚往左上铺；窗口为弹层留出的余量保持透明，卡片不铺过去。
        """
        cw, chh = self._whale_size()
        ox, oy = self._whale_offset()
        card = max(cw, 220)
        ox, oy = ox + cw - card, oy + chh - card
        try:
            pm = QPixmap(str(_ASSET))
            if not pm.isNull():
                w = card * 0.6
                pm = pm.scaled(int(w), int(w), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap(ox + int((card - pm.width()) / 2),
                             oy + int(card * 0.16), pm)
        except Exception:  # noqa: BLE001
            pass
        f = QFont(getattr(self, "_base_font", None) or self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#536ba9"))
        tip = "鲸鱼挂件暂不可用"
        sub = (self._err or "未就绪")[:60]
        p.drawText(QRectF(ox + 8, oy + card * 0.62, card - 16, 20),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), tip)
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QColor("#9fb0d9"))
        p.drawText(QRectF(ox + 8, oy + card * 0.62 + 20, card - 16, 46),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap), sub)

    # ------------------------------------------------------------ 浏览器侧

    def showEvent(self, ev) -> None:  # noqa: N802
        """首次上屏时启动浏览器侧。

        为什么放在这里而不是 `__init__` 的定时器：控制器要挂到窗口 HWND 上，
        而**窗口没上屏时 HWND 是无效的**（实测 offscreen 下 `winId()` 是假句柄，
        控制器直接 hr=0x80070578 E_INVALID_WINDOW_HANDLE）。`showEvent` 是
        「窗口真的上屏了」的确定信号，比写死延时可靠。

        设 `PM_WHALE_NO_WEBVIEW2=1` 可完全跳过（排查主界面问题时用）。
        """
        super().showEvent(ev)
        if self._booted or os.environ.get("PM_WHALE_NO_WEBVIEW2"):
            return
        self._booted = True
        # 让本轮 showEvent 走完（窗口完成映射）再起，避免拿到未生效的 HWND
        QTimer.singleShot(0, self._boot_webview)

    def _boot_webview(self) -> None:
        """建 WebView2 并把原版挂件页加载进去。

        ⛔ **建链是异步的**：`WhaleHostWebView.__init__` 返回时环境/控制器都还没
        建好（COM 回调要等消息循环），此时 `ok` 必为 False。所以加载页面必须
        挂在 `when_ready()` 回调上 —— 在回调触发前 `ok` 恒为 False，同步判
        `if not self._host.ok: 降级` 会在控制器就绪之前误判失败。
        """
        try:
            hwnd = int(self.winId())
            # 传**逻辑像素**：`put_Bounds` 到底按逻辑还是物理解释随运行库而异，
            # 宿主会用页面视口自校准（见 whale_host._calibrate_bounds），这里不猜。
            self._host = WhaleHostWebView(hwnd, self.width(), self.height())
            # 拖动交接：页面自己报位移（见 whale_host.build_host_html 的 dragSetup），
            # 这里收下来挪窗口。**不再**给子窗加 WS_EX_TRANSPARENT —— 那会让
            # 页内控件（减号、原版菜单）一起收不到点击。
            self._host.on_drag(self._on_pagedrag)
            # 启动回报：页面轮询原版挂件本体，出现了/超时了都告诉我们 ——
            # 超时（ok=False）说明"页面加载成功但鲸鱼没画"，必须降级成提示卡，
            # 否则就是一只全透明的空窗（「挂件没看到」的真实成因之一）。
            self._host.on_boot(self._on_pageboot)
            # 可命中区：页面报「本体 + 可见弹层」的矩形，据此铺可命中底（见 paintEvent）。
            self._host.on_rects(self._on_rects)
            # 换边回执：页面确认"本体已钉到某一边"，此刻才补偿窗口位置（见 _set_side）
            self._host.on_side(self._on_pageside)
            self._host.when_ready(self._after_host_ready)
        except Exception as e:  # noqa: BLE001 — 任何异常都降级，不拖垮主界面
            self._err = "%s: %s" % (type(e).__name__, e)
            self._host = None
            self.update()

    def _on_pageboot(self, ok: bool) -> None:
        """页面的启动结论：鲸鱼本体出现了（True）或暂未出现（False）。

        ⛔ boot=False **不再藏宿主页**：页面自己会在等待期显示页内提示卡，
        并继续后台轮询——鲸鱼本体一旦渲染出来（慢启动可能远超 6 秒），
        页面会再发 boot(True)，卡片自动切回鲸鱼。藏掉宿主页 = 把"迟到的
        鲸鱼"也一起藏掉（实测慢启动超 6 秒会被永久藏掉）。
        """
        if ok:
            self._boot_ok = True
        else:
            self._boot_ok = False
        self.update()

    def _boot_watchdog(self) -> None:
        """页面迟迟不发 boot 结论的兜底（70 秒，等页面 60s 轮询预算走完）。

        注入脚本本身可能没跑起来（极端情况）⇒ 永远等不到 boot 消息。8 秒后
        仍无结论且无其他失败标记 ⇒ 按启动失败降级。宁可达观检查三遍再动手，
        也不能让用户守着一只看不见的空窗。
        """
        try:
            if self._boot_ok or self._load_failed:
                return
            if self._host is None or not self._host.ok:
                return
            self._err = "挂件页面无响应（未收到启动回报，请重启控制台重试）"
            self._load_failed = True
            self._keep_draggable()
            self.update()
        except RuntimeError:
            pass # 控件已销毁（关窗），一切无需再做
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 拖动（Qt 侧）
    #
    # 为什么**彻底**改到 Qt 侧：页面报的开始/结束要一路 postMessage → COM 回调 → Qt，
    # 而拖动中的位置过去靠"宿主每 16ms 读光标"补 —— 真机上仍不跟手（时序与投递都不可靠）。
    # 现在改成：页面只负责说一句"在本体上按下了"（且不是按在按钮/菜单上），
    # 宿主随即用 **`SetCapture` 把鼠标消息在系统层面收回本窗口** —— 此后所有移动/松手
    # 都由 Windows 直接投给 Qt，用 Qt 自己的全局坐标挪窗：1:1、不经内核、不依赖任何页面事件。

    def _on_pagedrag(self, kind: str) -> None:
        """页面说「本体上按下 / 松手」→ 由 Qt 接手拖（系统级捕获）。"""
        try:
            if kind == "begin":
                # ⛔ 锚点是**本体**（不是窗口）：本体在窗口内会左右换边，窗口左上角与
                #    本体左上角不是同一个点，用窗口当锚会在换边/吸附时跳掉一截。
                self._drag_cursor = QCursor.pos() # 锚点：按下那一刻的光标
                self._drag_whale = self._whale_rect().topLeft()
                self._drag_t0 = time.monotonic()
                # ⛔ 跳出 WebView2 的事件回调再抢捕获：抢捕获本身是普通 user32 调用，
                #    但此刻内核正在处理这次按下，等一拍再抢更稳（不让内核半路丢消息）。
                QTimer.singleShot(0, self._grab_mouse)
                if not self._drag_timer.isActive():
                    self._drag_timer.start(16)
            else:
                self._end_drag()
        except Exception:  # noqa: BLE001 — 拖动是尽力而为，失败不影响挂件显示
            pass

    def _grab_mouse(self) -> None:
        """把鼠标消息在系统层面收回本窗口（拖动期间不再经过 WebView2）。

        ⛔ 只在**真的上屏**时抢：没上屏就抢等于把一个不存在的窗口设成捕获窗口，
        会把系统的鼠标消息搅乱。
        """
        try:
            if not self.isVisible():
                return
            ctypes.windll.user32.SetCapture(ctypes.c_void_p(int(self.winId())))
        except Exception:  # noqa: BLE001
            pass

    def _release_mouse(self) -> None:
        """交还鼠标捕获（拖动结束；不还的话内核再也收不到鼠标）。"""
        try:
            ctypes.windll.user32.ReleaseCapture()
        except Exception:  # noqa: BLE001
            pass

    def _end_drag(self) -> None:
        """结束拖动：停计时器、还捕获、贴边落位、落盘位置。"""
        if self._drag_timer.isActive():
            self._drag_timer.stop()
        self._release_mouse()
        self._drag_cursor = None
        self._drag_whale = None
        self._drag0 = None
        self._win0 = None
        self._user_moved = True
        self._snap_and_flip()
        self._save_anchor()

    def _drag_tick(self) -> None:
        """拖动期间的兜底：①按"位置 = 本体起点 + 光标位移"**绝对定位**（与 Qt 自己那条
        路幂等，不会双倍位移）；②按键已全部松开、或拖得过久就自动收尾 —— 页面漏报松手
        也不会卡在"一直在拖"的状态里（也不会一直占着鼠标捕获）。

        ⛔ 吸附判据一律喂**自由位置**（光标算出来的那个），绝不喂吸附结果：
        喂结果的话，吸住之后判据永远成立、脱不开也回不来（拖着拖着"卡在边上，得先松手
        再拖"就是这么来的）。
        """
        try:
            if self._drag_cursor is None or self._drag_whale is None:
                self._drag_timer.stop()
                return
            _u = ctypes.windll.user32
            # ⛔ 左右键都要看：鼠标左右键互换（左手习惯）时，浏览器里的"左键"对应的是
            #    物理右键 —— 只看 VK_LBUTTON 会在按下的那一刻就判"已松手"⇒ 拖动刚起步就断。
            if not ((_u.GetAsyncKeyState(1) | _u.GetAsyncKeyState(2)) & 0x8000):
                self._end_drag()
                return
            if time.monotonic() - self._drag_t0 > 60:
                self._end_drag() # 上限：任何自链都必须有终止条件
                return
            free = self._drag_whale + (QCursor.pos() - self._drag_cursor)
            nx, _side, flip = self._snap_geometry(free.x())
            # 拖动中**不换边**：换边要动 300 多像素的窗口-本体偏移，而页面的换边是异步
            # 的，途中会让本体在屏幕上闪一帧。停在松手那一刻换（`_end_drag` 里做），
            # 那时本体位置不动、只补偿窗口，用户看不见。
            self._place_whale(nx, self._clamp_whale_y(free.y()))
            self._set_flip(flip)
        except Exception:  # noqa: BLE001
            try:
                self._end_drag()
            except Exception:  # noqa: BLE001
                pass

    def _after_host_ready(self) -> None:
        """建链结束（成功或失败）后的落点：成了就加载原版挂件页，没成就降级。

        ⛔ `navigate_to_string` 的返回值**必须**落到 `_load_failed` 上：它只表示
        「页面导航调用是否被接受」，失败时 `WhaleHostWebView.ok` 仍是 True。
        不单独记这个状态，`paintEvent` 就会以为"一切正常"而不画降级卡，
        用户拿到的是一个没有任何提示的纯黑窗口。
        """
        try:
            host = self._host
            if host is None:
                self._load_failed = True
                self.update()
                return
            if not host.ok:
                self._err = host.error
                self._load_failed = True
                self.update()
                return
            port, token = _server_addr()
            # ⛔ **先探端口，再导航**。挂件页是拿 `http://127.0.0.1:<port>/dsh-whale/
            #    widget.js` 去加载原版脚本的 —— 那个端口由 agent 侧控制台提供，
            #    agent 没起来时端口**根本没人听**。此时 `NavigateToString` 仍返回
            #    成功（它只管把 HTML 塞进去），页面却因为脚本 404 而**一片空白**，
            #    用户看到的就是「一个黑块，啥都没显示」。
            #    所以这里先做一次 TCP 探活，探不到就**如实降级**（画提示卡），
            #    不让用户对着纯黑发愣。
            why = _server_unreachable(port)
            if why:
                self._err = why
                self._load_failed = True
                self.update()
                return
            # ⛔ **认人后再加载** —— 端口活着不等于「在听的是我们」。
            #    同源上游产品（QQ agent）默认端口同为 3210；本产品被占时会**静默顺延**
            #    到 3211/3212…，但反过来，别的程序先占了 3210 而我们还没起来时，
            #    把地址直接递给 WebView2 就等于**把用户的挂件窗变成一个别人页面的壳**。
            #    启动器侧早就有这道闸（launcher.cs:2936 IsOurConsole），挂件侧此前没有
            #    —— 这是同一个漏洞的第二个入口，所以判据完全照抄启动器那份。
            why = _not_our_console(port)
            if why:
                self._err = why
                self._load_failed = True
                self.update()
                return
            if not host.navigate_to_string(build_host_html(port, token)):
                self._err = host.error
                self._load_failed = True
            else:
                # 导航成功 ≠ 鲸鱼画出来了。8 秒内等不到页面的启动结论就降级
                #（单次定时器，非自链；见 _boot_watchdog 注解）。
                QTimer.singleShot(70000, self._boot_watchdog)
        except Exception as e:  # noqa: BLE001
            self._err = "%s: %s" % (type(e).__name__, e)
            self._load_failed = True
        # 失败态才需要让出事件；成功态的拖动走页面转发（见 `_on_pagedrag`），
        # **不能**给子窗加 WS_EX_TRANSPARENT —— 那会把页内控件（减号、菜单）一起点死。
        if self._load_failed:
            self._keep_draggable()
        self.update()

    def _keep_draggable(self) -> None:
        """**降级态专用**：把控制器收起来，让事件回到 Qt 手上。

        只在「页面没起来」时用：此时页内没有任何可点的东西，把铺满窗口的
        WebView 子窗收掉、让 Qt 直接接鼠标，用户至少能把这块东西拖走或看清提示。

        页面正常时**不走这条路** —— 拖动由页面自己报位移（`_on_pagedrag`），
        子窗留着不动，页内控件照常可点。
        """
        try:
            host = self._host
            if host is not None and host.ok:
                host.hide()
        except Exception:  # noqa: BLE001 — 拖动能力是尽力而为，失败不影响主界面
            pass

    # ------------------------------------------------------------ 交互
    #
    # 鼠标一律**先转发给内核**（合成承载没有子窗替我们收事件，必须显式注入；
    # 窗口化承载下 `send_mouse` 是空操作，事件本来就由子窗直给页面）。
    # 拖动归谁则看承载方式 —— 判据只有一条：**会不会同时收到同一个事件**。
    _K_MOVE = 512 # WM_MOUSEMOVE
    _K_LDOWN = 513 # WM_LBUTTONDOWN
    _K_LUP = 514 # WM_LBUTTONUP
    _K_LEAVE = 675 # WM_MOUSELEAVE
    _MK_LBUTTON = 1 # 注入左键拖动时要带上「左键仍按下」的状态位

    def _page_drives_drag(self) -> bool:
        """这一拍的事件是否**已经**由页面推动拖动 —— 是则 Qt 不再插手。

        · 合成承载（无子窗）：宿主与页面会**同时**拿到同一个鼠标事件，
          两边都挪窗就是双倍位移 ⇒ 只让页面驱动（页内监听 → postMessage）；
        · 窗口化承载：页面与 Qt 是**互斥**的两条命中路径（子窗吃到就轮不到
          Qt，只有画布透明像素穿透时才轮到 Qt），谁收到谁拖，互不冲突 ⇒
          Qt 照常顶上，用户抓空白处也能把挂件拖走；
        · 页面没起来（降级态）：WebView 已被 `_keep_draggable()` 收起，
          由 Qt 接全部鼠标 ⇒ 一定不交给页面。
        """
        host = self._host
        if host is None or not getattr(host, "_composition", False):
            return False
        return bool(host.ok and not self._load_failed)

    def _inject(self, kind: int, ev, vkeys: int = 0) -> None:  # noqa: ANN001
        """把当前鼠标事件转给内核（合成承载专用；窗口化下是空操作）。"""
        try:
            if self._host is not None:
                self._host.send_mouse(kind, int(ev.position().x()),
                                      int(ev.position().y()), vkeys)
        except Exception:  # noqa: BLE001 — 注入是尽力而为，失败不影响窗口自身
            pass

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._lbtn = True
            self._inject(self._K_LDOWN, ev, self._MK_LBUTTON)
            if not self._page_drives_drag():
                self._drag0 = ev.globalPosition().toPoint()
                self._win0 = self.pos()
                self._moved = False
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        # 注入的 MOVE 要带真实按键状态（内核据此判断「这是拖动还是悬停」），
        # 与「谁负责挪窗」无关 —— 所以看物理左键，不看 `_drag0`。
        self._inject(self._K_MOVE, ev, self._MK_LBUTTON if self._lbtn else 0)
        # 页面转发的那次拖动由 `_drag_tick` 独占挪窗（它按本体算绝对位置）；这里再按
        # 窗口算一次就是两条公式互相打脸 —— 拖动会抖、会跟不住手。
        if self._drag_cursor is not None:
            return
        if self._drag0 is None or self._win0 is None:
            return
        d = ev.globalPosition().toPoint() - self._drag0
        if not self._moved and (abs(d.x()) + abs(d.y())) > 5:
            self._moved = True # 位移阈值：小于它算「点击」而非拖拽
        if self._moved:
            self.move(self._win0 + d)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._inject(self._K_LUP, ev)
            self._lbtn = False
        # 捕获在我们手上时，松手一定由 Qt 收到 —— 用它统一收尾（停表、还捕获、落盘位置）
        if self._drag_cursor is not None or self._drag0 is not None:
            self._end_drag()
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        if self._host is not None:
            self._host.send_mouse(self._K_LEAVE, -1, -1)
        super().leaveEvent(ev)

    def moveEvent(self, ev) -> None:  # noqa: N802
        """窗口移动时通知内核重排（WebView2 不跟随父窗自动挪，会留在原地）。"""
        super().moveEvent(ev)
        if self._host is not None and self._host.ok:
            self._host.resize(self.width(), self.height())
            # 父窗挪了要**明确通知内核**（窗口化承载下它不会自动知道）
            self._host.notify_moved()

    def closeEvent(self, ev) -> None:  # noqa: N802
        """**必须关控制器** —— 不关会留下杀不掉的浏览器孤儿进程。"""
        if self._host is not None:
            self._host.close()
            self._host = None
        super().closeEvent(ev)

    # ------------------------------------------------------------ 位置记忆

    @staticmethod
    def _read_anchor(st) -> list | None:  # noqa: ANN001
        """读位置记忆 —— **以「本体右下角」为锚**（锚点与窗口尺寸、贴边方向都无关）。

        ⛔ 为什么不记窗口左上角：本体由上游 CSS 钉在视口下沿，横向还会按"本体在屏幕
        哪半"在窗口内换边；窗口尺寸一变（为弹层长高），同一个窗口角对应的本体位置就
        跟着变 —— 一不小心就把本体顶到屏幕外面去（真实事故：窗口从 250 变 440×560 后，
        本体整块落在屏幕下方，用户看到「只剩一个减号悬在那儿」）。锚点记在**本体**上，
        改窗口尺寸/换边都与它无关。

        老记录存的是「窗口左上角」（当年窗口边长 = `_BASE_LEGACY`）⇒ 一次性换算：
        锚 = 老左上角 + (`_BASE_LEGACY`, `_BASE_LEGACY`)。换算只读不写，下次拖拽就落盘。
        """
        a = st.value("whale_anchor")
        if isinstance(a, list) and len(a) == 2:
            try:
                return [int(a[0]), int(a[1])]
            except (TypeError, ValueError):
                return None
        p = st.value("whale_pos")
        if isinstance(p, list) and len(p) == 2:
            try:
                return [int(p[0]) + _BASE_LEGACY, int(p[1]) + _BASE_LEGACY]
            except (TypeError, ValueError):
                return None
        return None

    def _save_anchor(self) -> None:
        """把「本体右下角」的屏幕坐标落盘（拖动结束时调用）。"""
        try:
            r = self._whale_rect()
            QSettings(*_SET).setValue(
                "whale_anchor", [int(r.x()) + int(r.width()), int(r.y()) + int(r.height())])
        except Exception:  # noqa: BLE001 — 位置记忆失败不影响挂件显示
            pass

    def _apply_anchor(self) -> bool:
        """按位置记忆把本体放到锚点（本体右下角），并定下贴边方向；无记忆/越界返回 False。

        页面几何到手后调一次：构造时用的是**兜底本体尺寸**（≈123）与历史窗口边长
        （250）差着一倍，只靠兜底会偏；这里按真实尺寸重算一次即可精确。
        """
        anchor = self._read_anchor(QSettings(*_SET))
        if anchor is None:
            return False
        w, h = self._whale_size()
        wx, wy = int(anchor[0]) - w, int(anchor[1]) - h
        if not self._onscreen(wx, wy):
            return False
        nx, side, flip = self._snap_geometry(wx)
        # 先落位再换边：换边只补偿窗口，本体屏幕位置不动（顺序见 `_snap_and_flip`）
        self._place_whale(nx, self._clamp_whale_y(wy))
        self._set_side(side)
        self._set_flip(flip, True)
        return True

    def _move_default(self) -> None:
        """默认位置：**本体**的右下角距屏幕右下角 18px（不是窗口角 —— 窗口比本体大）。"""
        try:
            scr = QApplication.primaryScreen()
            geo = scr.availableGeometry() if scr else None
            if geo is not None:
                w, h = self._whale_size()
                self._place_whale(int(geo.right()) + 1 - w - 18,
                                  int(geo.bottom()) + 1 - h - 18)
                self._set_side("right")
        except Exception:  # noqa: BLE001
            pass

    def _onscreen(self, wx: int, wy: int) -> bool:
        """上屏校验：本体（给定左上角屏幕坐标）是否在任一屏幕上至少露出 60×60。

        ⛔ 判据必须是本体、不是整个窗口：窗口为弹层留了大片透明余量，按窗口判的话
        "左上角刚好露一点"也算通过，而本体可能整个在屏幕外 —— 用户看到的就是
        「挂件不见了」。
        判不出来时**宁可达观**（返回 True 保持原位）：误判的代价是位置跳回默认角，
        漏判的代价是挂件彻底看不见。
        """
        try:
            scr = QApplication.instance()
            w, h = self._whale_size()
            for s in (scr.screens() if scr else []):
                g = s.availableGeometry()
                ix = min(wx + w, g.right() + 1) - max(wx, g.left())
                iy = min(wy + h, g.bottom() + 1) - max(wy, g.top())
                if ix >= 60 and iy >= 60:
                    return True
        except Exception:  # noqa: BLE001
            return True
        return False

    def reset_position(self) -> None:
        """回到右下角（位置记忆清掉）—— 留给「找不到挂件了」的救援路径。"""
        _st = QSettings(*_SET)
        _st.setValue("whale_anchor", "")
        _st.setValue("whale_pos", "")
        self._move_default()


# 端口来源标记（file / config / default / fallback）—— 只为排查可观测，不参与逻辑
_ADDR_SRC = ""


def _server_unreachable(port: int, timeout: float = 1.2) -> str:
    """控制台端口探活：**能连上就返回空串**，连不上返回一句人话原因。

    为什么需要：挂件是 WebView2 加载的宿主页，页里 `<script src=.../widget.js>`
    指向 `http://127.0.0.1:<port>`。这个端口由 agent 侧控制台提供 ——
    **agent 没起来时它没人监听**，而 `NavigateToString` 依然成功返回，
    于是页面因脚本加载失败而空白，用户眼里就是「一个黑块」。

    探活放在导航之前，作用只有一个：**把"没内容可显示"和"显示失败"分开说**，
    让降级卡能给出可操作的话（"请先启动控制台"），而不是让用户对着黑块猜。
    """
    import socket  # noqa: PLC0415

    try:
        s = socket.socket()
        s.settimeout(timeout)
        try:
            if s.connect_ex(("127.0.0.1", int(port))) != 0:
                return "控制台端口 %d 未在监听（请先从主界面打开一次控制台）" % int(port)
        finally:
            s.close()
    except Exception as e:  # noqa: BLE001 — 探活本身失败也当"不可用"，但不影响主界面
        return "控制台端口 %d 探活失败：%s" % (int(port), e)
    return ""


def _not_our_console(port: int, timeout: float = 1.5) -> str:
    """**认人**：确认这个端口上应答的是本产品控制台 —— 是则返空串，否则返原因。

    ⛔ 为什么必须有（用户点出的真风险）：挂件本质上就是「浏览器拉一个页面」。
    端口上有东西应答**只说明"有人听"，不说明"是我们的"**。同源上游产品
    （QQ agent）默认端口与本产品同为 3210 —— 它先开着、本产品还没起来时，
    把地址递给 WebView2 就等于**把这个挂件窗变成别人程序的页面壳**。
    启动器侧早就为此加了闸（`launcher-src/launcher.cs:2936 IsOurConsole`），
    但**挂件是第二个入口，此前完全没有这道校验** —— 同一个洞的旁路。

    判据**完全照抄启动器那份**（两处必须同口径，否则又是一处"两个理解"）：
      ① `GET /api/version` —— 本产品 webui 专为「启动器/新实例探测旧实例」留的
         **免认证**路由，200 且正文含 `"ver"` ⇒ 是我们（上游没有这个口）；
         200 但没有 `ver` ⇒ **明确不是我们**（有别的 HTTP 服务在应答）；
         非 200 ⇒ 退回 ② 再认一次。
      ② `GET /` 首页正文非空且含「群相」/`PersonaMorph`/`persona_morph`。
         ⚠️ 仅作兜底：鲸语模式会把页面可见文案（含 `<title>`）整段换成鲸语，
         正文指纹会失效，所以**不作主判据**。

    哲学与「自愈链」相反：这里**宁可错杀** —— 判不出的代价只是挂件降级显示提示
    （用户从主界面再打开一次即可），错认的代价是把用户导去别的程序。

    ⛔ 必须**绕代理**（`ProxyHandler({})`）：本机代理会把 127.0.0.1 也代理走
    （实测会返回 502），不绕开就会把「代理劫持」误判成「不是我们」。
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    base = "http://127.0.0.1:%d" % int(port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get(path: str) -> tuple[int, str]:
        try:
            req = urllib.request.Request(base + path, headers={"Host": "127.0.0.1"})
            with opener.open(req, timeout=timeout) as f:
                return int(f.status), f.read(65536).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return int(e.code), ""
        except Exception:  # noqa: BLE001 — 连不上/超时都当"拿不到"
            return 0, ""

    code, body = _get("/api/version")
    if code == 200:
        if '"ver"' in body:
            return ""
        return "端口 %d 上有 HTTP 服务应答，但不是本产品控制台" % int(port)
    # 非 200（404/401/403…）⇒ 这个口上没有版本路由，退回首页正文兜底再认一次
    hc, hb = _get("/")
    if hc == 200 and hb:
        if ("群相" in hb or "PersonaMorph" in hb
                or "persona_morph" in hb.lower()):
            return ""
        return "端口 %d 被其它程序占用（像是同名端口的别的应用）" % int(port)
    return "端口 %d 上应答的不是本产品控制台（已拒绝加载，避免显示别人的页面）" % int(port)


def _server_addr() -> tuple[int, str]:
    """控制台监听端口与访问口令 —— 挂件脚本用它们去请求 /dsh-whale/*。

    ⛔ **不自己解析端口，复用 Qt 侧唯一权威实现 `addr.resolve_base_url()`**。
    理由：端口的权威顺序（`logs/console.url` 活性检查 → 配置 → 默认）在本产品
    已经被踩过三次坑（webui 端口被占会**静默顺延** 3210→3211…，手拼
    `server.port` 拼出来的地址根本没人听；写成死链还会 401/404）。`addr.py`
    就是这条口径的**唯一实现**，自带活性检查、死链回落、空 token 守卫
    （`join_url` 还专门修过双 `?` 把 token 吞掉的 401 事故）。
    挂件另起一套解析 = 迟早与主界面走不同的端口 ⇒ 又是「一片空白」。

    `addr.resolve_base_url()` 返回 `(url, source)`，source ∈
    `file` / `config` / `default`；这里把 url 拆成 (port, token) 给挂件用。
    拿不到端口时兜底 3210（与 `addr.DEFAULT_BASE` 同值，文档化默认）。
    """
    global _ADDR_SRC
    try:
        import urllib.parse as _up  # noqa: PLC0415

        from addr import resolve_base_url  # noqa: PLC0415

        url, src = resolve_base_url()
        _ADDR_SRC = str(src or "")
        u = _up.urlparse(str(url or ""))
        port = int(u.port or 3210)
        token = str((_up.parse_qs(u.query).get("token") or [""])[0]).strip()
        return port, token
    except Exception:  # noqa: BLE001 — 解析层任何异常都不该让挂件起不来
        _ADDR_SRC = "fallback(3210)"
        return 3210, ""
