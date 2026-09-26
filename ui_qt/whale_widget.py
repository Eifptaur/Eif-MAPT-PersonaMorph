# -*- coding: utf-8 -*-
"""右下角鲸鱼挂件窗：Qt 透明顶层小窗，内嵌 WebView2 渲染上游挂件脚本。

结构：

    WhaleWidget (QWidget, Qt.Tool 顶层窗，置顶)
      └── 透明方窗，边长 = 原版 `--dshw-base`（250px）
            └── WhaleHostWebView（whale_host.py）在窗口 HWND 上挂 WebView2，
                加载宿主页 → 宿主页引入原版 widget.js → 挂件自己渲染

职责边界：窗口（显隐/位置记忆/收起圆点）留在本模块；浏览器（环境/控制器/
页面加载）在 `whale_host.py`；挂件自身的菜单、音效、角色、用量全部由原版
脚本负责，本模块不重现任何一条。脚本文件 = `whale-widget/client/widget.js`
（vendor 自上游，仅一处移植补丁，见仓库内移植记录）。

降级：WebView2 不可用（运行库被卸载、程序集缺失、初始化异常）、控制台端口
不可达、页面未渲染出挂件本体时，退回**信息卡**：一张静态形象图 + 一行可操作
说明，不留纯白/纯黑/全透明空窗。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
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

# 原版边长（widget.js :245 `--dshw-base` 的 clamp 上限 = 250px）
_BASE = 250


class WhaleWidget(QWidget):
    """无边框置顶小窗，内嵌 WebView2 跑**原版挂件**（点挂件本体即原版菜单）。"""

    W, H = _BASE, _BASE

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
        self._host = None
        self._err = ""
        self._booted = False
        # 页面加载是否失败 —— 与「控制器是否就绪」分开记：paintEvent 靠它决定
        # 要不要画降级卡（只看 `_host.ok` 时，半成品状态会留一个纯黑窗口）。
        self._load_failed = False
        # 页面是否回报「鲸鱼本体渲染出来了」—— 与 _load_failed 互斥推进：
        # boot ok=True 会浇灭 watchdog，False 则直接转降级。
        self._boot_ok = False
        self.restyle(t)

        # 位置：上次拖到哪就还在哪；**越界/坏记录落右下角**。
        # ⛔ 必须校验上屏：实测 QSettings 里存过 (1611,1431) —— 在 1080p 屏上
        #    y=1431 已在屏幕底边之外，挂件"一直在显示、只是在屏幕外面"，
        #    用户眼里就是「没看到挂件」。位置记忆跨分辨率/换屏后天然可能越界。
        pos = QSettings(*_SET).value("whale_pos")
        moved = False
        if isinstance(pos, list) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
                if self._onscreen(x, y):
                    self.move(x, y)
                    moved = True
            except (TypeError, ValueError):
                moved = False
        if not moved:
            self._move_default()

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

    def paintEvent(self, _e) -> None:  # noqa: N802
        """WebView2 正常时由内核合成画面（本窗口只垫**键色**底）。

        键色像素经窗口颜色键抠像 = 透明且点击穿透；只有降级态才画提示卡
        （同样垫键色，卡片内容浮在桌面上）。

        ⛔ 判据是「**页面真的加载成功了**」而不是「控制器建好了」：
        `WhaleHostWebView.ok` 只说明 WebView2 环境/控制器就绪，**不代表
        `navigate_to_string` 成功**。只看 `.ok` 时，"控制器就绪但页面没内容"
        的半成品状态两头落空：Qt 侧不画降级卡、WebView2 侧没内容 ⇒ 空窗。
        `load_failed` 把"页面没起来"单独记下来，让降级卡在这种状态下兜底。
        """
        if self._host is not None and self._host.ok and not self._load_failed:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_fallback(p)

    def _paint_fallback(self, p: QPainter) -> None:  # noqa: N802
        """降级卡：形象图 + 一行说明（**给出可操作指引**，不做静默空白）。"""
        try:
            pm = QPixmap(str(_ASSET))
            if not pm.isNull():
                w = self.W * 0.6
                pm = pm.scaled(int(w), int(w), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap(int((self.W - pm.width()) / 2), int(self.H * 0.16), pm)
        except Exception:  # noqa: BLE001
            pass
        f = QFont(getattr(self, "_base_font", None) or self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#536ba9"))
        tip = "鲸鱼挂件暂不可用"
        sub = (self._err or "未就绪")[:60]
        p.drawText(QRectF(8, self.H * 0.62, self.W - 16, 20),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), tip)
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QColor("#9fb0d9"))
        p.drawText(QRectF(8, self.H * 0.62 + 20, self.W - 16, 46),
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
            self._host = WhaleHostWebView(hwnd, self.W, self.H)
            # 拖动交接：页面自己报位移（见 whale_host.build_host_html 的 dragSetup），
            # 这里收下来挪窗口。**不再**给子窗加 WS_EX_TRANSPARENT —— 那会让
            # 页内控件（减号、原版菜单）一起收不到点击。
            self._host.on_drag(self._on_pagedrag)
            # 启动回报：页面轮询原版挂件本体，出现了/超时了都告诉我们 ——
            # 超时（ok=False）说明"页面加载成功但鲸鱼没画"，必须降级成提示卡，
            # 否则就是一只全透明的空窗（「挂件没看到」的真实成因之一）。
            self._host.on_boot(self._on_pageboot)
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

    # ---- 输入注入（合成承载专用；窗口化承载下 host.send_mouse 是空操作）----
    _K_MOVE = 512 # WM_MOUSEMOVE
    _K_LDOWN = 513 # WM_LBUTTONDOWN
    _K_LUP = 514 # WM_LBUTTONUP
    _K_LEAVE = 675 # WM_MOUSELEAVE

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if self._host is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._host.send_mouse(self._K_LDOWN, int(ev.position().x()), int(ev.position().y()))
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if self._host is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._host.send_mouse(self._K_LUP, int(ev.position().x()), int(ev.position().y()))
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._host is not None:
            self._host.send_mouse(self._K_MOVE, int(ev.position().x()), int(ev.position().y()))
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        if self._host is not None:
            self._host.send_mouse(self._K_LEAVE, -1, -1)
        super().leaveEvent(ev)

    def _on_pagedrag(self, dx: int, dy: int, ended: bool) -> None:
        """页面报告的拖动位移 → 挪窗口；`ended` 时落盘位置。

        增量语义：页面每帧只发**这一帧的增量**（绝对坐标会被子窗与宿主窗的
        坐标系差异坑到），这里累加到 `self.pos()` 上。
        """
        try:
            if ended:
                QSettings(*_SET).setValue("whale_pos", [self.x(), self.y()])
                return
            if not dx and not dy:
                return
            p = self.pos()
            self.move(p.x() + int(dx), p.y() + int(dy))
        except Exception:  # noqa: BLE001 — 拖动是尽力而为，失败不影响挂件显示
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

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag0 = ev.globalPosition().toPoint()
            self._win0 = self.pos()
            self._moved = False

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drag0 is None or self._win0 is None:
            return
        d = ev.globalPosition().toPoint() - self._drag0
        if not self._moved and (abs(d.x()) + abs(d.y())) > 5:
            self._moved = True  # 位移阈值：小于它算「点击」而非拖拽
        if self._moved:
            self.move(self._win0 + d)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if self._drag0 is not None and self._moved:
            QSettings(*_SET).setValue("whale_pos", [self.x(), self.y()])
        self._drag0 = None
        self._win0 = None

    def moveEvent(self, ev) -> None:  # noqa: N802
        """窗口移动时通知内核重排（WebView2 不跟随父窗自动挪，会留在原地）。"""
        super().moveEvent(ev)
        if self._host is not None and self._host.ok:
            self._host.resize(self.W, self.H)

    def closeEvent(self, ev) -> None:  # noqa: N802
        """**必须关控制器** —— 不关会留下杀不掉的浏览器孤儿进程。"""
        if self._host is not None:
            self._host.close()
            self._host = None
        super().closeEvent(ev)

    def _move_default(self) -> None:
        try:
            scr = QApplication.primaryScreen()
            geo = scr.availableGeometry() if scr else None
            if geo is not None:
                self.move(geo.right() - self.W - 18, geo.bottom() - self.H - 18)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _onscreen(x: int, y: int) -> bool:
        """恢复位置前校验：这块矩形是否在**任一屏幕**上至少露出 60×60。

        为什么需要：位置记忆来自上一台/上一分辨率的会话，换屏、改缩放、
        拖到边缘时都可能存下「屏幕外」的坐标 —— 挂件照着它 move 就直接消失
        （实测 (1611,1431) 在 1080p 上 y 已越界 ⇒ 用户「没看到挂件」）。
        判不出来时**宁可达观**（返回 True 保持原位）：误判的代价是位置跳回
        默认角，漏判的代价是挂件彻底看不见。
        """
        try:
            scr = QApplication.instance()
            for s in (scr.screens() if scr else []):
                g = s.availableGeometry()
                ix = min(x + WhaleWidget.W, g.right()) - max(x, g.left())
                iy = min(y + WhaleWidget.H, g.bottom()) - max(y, g.top())
                if ix >= 60 and iy >= 60:
                    return True
        except Exception:  # noqa: BLE001
            return True
        return False

    def reset_position(self) -> None:
        """回到右下角（位置记忆清掉）—— 留给「找不到挂件了」的救援路径。"""
        QSettings(*_SET).setValue("whale_pos", "")
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
