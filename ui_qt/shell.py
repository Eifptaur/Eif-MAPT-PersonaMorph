# -*- coding: utf-8 -*-
"""Qt 最小原生壳 —— 主窗口。

=====================================================================
这个原型要回答的问题
=====================================================================
「先只做最小原生壳：一个真窗口 + 导航分组树 + 1 张设置卡 + 状态徽章 + 二次确认弹窗。
  用来看手感与代价。」

⇒ 本文件只证明**手感**，刻意不碰产品代码（`agent/` 一行不动）。
⇒ 真正的结论在第 4 个自检项："换原生能治什么、治不了什么"（见 `根因排查-控制台打不开.md`）。

四块内容，一一对应 web 控制台：
  1. 顶部条      ← 对齐 web 侧顶栏（鲸鱼挂件 + 更新公告条 + 主题切换）
  2. 左导航分组树 ← 对齐 `six 组`（天天用/脑子/嘴和手/维修站/门面/救命）+ 搜索框
  3. 设置卡       ← 对齐"机器人昵称 + 响应档位"那张卡（带状态徽章）
  4. 弹窗         ← 对齐危险操作二次确认

平台红利（web 侧做不到、Qt 天生有的）：
  · 折叠动画：`QPropertyAnimation` 真高度动画，不用猜 max-height
  · 真模态弹窗：不跟着页面滚、不被 WebView2 视口裁掉
  · 状态自愈：窗口就是我们自己 ⇒ 服务死了能自己拉起来（`heal.py`）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_io # noqa: E402 面板读写 config.json 的桥（save→set 同 webui 次序）

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer # noqa: E402
from PySide6.QtGui import ( # noqa: E402
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import ( # noqa: E402
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from confirm import ConfirmDialog # noqa: E402
from cursor_fx import WhaleCursor # noqa: E402
from heal import Health, Probe, plan_for, probe_backend # noqa: E402
from ocean import OceanWaves, paint_backdrop # noqa: E402
from panels_qt import BATCH_SECS, build_panel # noqa: E402
from stylekit_qt import THEMES, Tokens, apply_font_to_app, qfont, resolve_family, rgba # noqa: E402
from widgets import ( # noqa: E402
    Badge,
    Btn,
    Card,
    Field,
    IconBtn,
    NavGroup,
    NavItem,
    SearchBox,
    Switch,
    desc,
    h2,
)


# ---------------------------------------------------------------- DPI 纪律


def set_per_monitor_dpi() -> str:
    """PerMonitorV2。

    照 `stylekit.cs` 的 `StyleKit.Prep()`：`SetThreadDpiAwarenessContext(-4)`。
    不做的后果（项目已实测过）：多显示器混合 DPI 下量到的是**虚拟坐标**，
    窗口尺寸会"莫名其妙变小"（1160×900 变 773×600 = ÷1.5）。
    Qt6 默认已经是 PerMonitorV2，这里显式再钉一遍，并把实际值回报出来供截图取证。
    """
    try:
        import ctypes # noqa: PLC0415
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    try:
        return str(QGuiApplication.highDpiScaleFactorRoundingPolicy().name)
    except Exception:
        return ""


# ---------------------------------------------------------------- 导航数据


# 5 组 27 项 —— 组名 / key / 项名 / 顺序逐项抄自 agent/console_html.py 的
# <aside class="side">（nav 区，L936 起），hint 摘自各面板 <div class="desc"> 第一句。
# **以 web 源码为唯一真值**：不自造组名、不用比喻命名（
# 分区按功能来；文案对齐 web 侧朴素直给的口吻）。
# selftest 会运行时解析 web 源码与这份 NAV 全等比对 —— 手抄过时/写错会直接红。
NAV: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("日常", "day", [
        ("概览", "机器人运作状态与账户信息，每 8 秒自动刷新", "overview"),
        ("微信", "机器人微信身份与轮询、白名单（改完要重启）", "wechat"),
        ("机器人", "怎么称呼自己、响应到什么程度、给模型多少上下文", "bot"),
        ("人设", "以谁的身份在群里说话、怎么参与，改完立刻生效", "persona"),
        ("体检", "代码检测与点击测试共 55 项，零风险不碰鼠标", "check"),
    ]),
    ("智能", "brain", [
        ("模型", "密钥首次引导填入后自动保存，无需再改配置文件", "model"),
        ("记忆", "每个群友的长期印象，机器人回复时会参考", "memory"),
        ("共享", "记忆怎么存、怎么共享、什么时候整理", "memory-set"),
        ("搜索", "搜索引擎与参数，保存后真实落盘生效", "search"),
        ("社区", "金句与意见的本地导出、反应评分引擎", "community"),
    ]),
    ("内容", "media", [
        ("媒体", "发图、发语音、发文件，开关默认都是关的", "media"),
        ("语音", "文字合成音频后发出，声音来源分三档", "tts"),
        ("要图", "群友要图时调生图后端，探到哪个用哪个", "imggen"),
        ("视频", "做好自动发出，没配后端就如实说没配", "videogen"),
        ("插件", "一个工具一份清单，勾选后模型才能用", "tools"),
        ("拍拍", "自动回拍、主动皮一下的频率与冷却", "poke"),
    ]),
    ("运行", "ops", [
        ("明细", "发了什么、多少用量、耗时，按天落盘可勾选删除", "sessions"),
        ("反馈", "想说的、想让它变成什么样的，写在这儿提交", "feedback"),
        ("发送", "真人化间隔与限频，防刷屏与封号风险", "send"),
        ("日志", "动作与失败原因，出问题先看这一屏", "log"),
        ("版本", "微信版本与适配层版本下每个能力的实测状态", "vermat"),
        ("服务", "控制台监听地址与访问口令，改完要重启", "server"),
        ("高级", "行为引擎完整参数、UI 图标库、学习机制", "advanced"),
    ]),
    ("外观", "look", [
        ("界面", "显示缩放、遮挡清理与主题", "ui"),
        ("配置格式", "全部配置的配置文件，改前先备份", "json"),
        ("光标", "把鼠标指针换成鲸鱼，点击时向下点头", "cursor"),
        ("波纹", "鼠标处荡开波纹的动效，参数即时生效", "wavefx"),
    ]),
]


# ---------------------------------------------------------------- 鲸语桥


def _whale_tr():
    """鲸语翻译器 —— 从项目根读 `agent/whale_text.py`（**单一来源**），不复制字典。

    只读不改：这里是原型侧的"只读消费者"。读不到（文件缺失/语法错误）就退化为恒等函数，
    切鲸语 = 不换文案，界面不崩。
    """
    import importlib.util # noqa: PLC0415

    p = Path(__file__).resolve().parents[1] / "agent" / "whale_text.py"
    try:
        spec = importlib.util.spec_from_file_location("whale_text_bridge", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def tr(s: str) -> str:
            return mod.NAV.get(s) or mod.DICT.get(s) or s

        return tr
    except Exception: # noqa: BLE001
        return lambda s: s


# ---------------------------------------------------------------- 主窗口


class Shell(QWidget):
    def __init__(self, t: Tokens):
        super().__init__()
        self.t = t
        self.text_style = str(config_io.read_path("ui.text_style", "normal") or "normal")
        # ui.text_style 轴：normal / whale
        # 主题跟随的「上次见过的值」基线 = config 当前值（不是本壳初始主题）：
        # 这样初始主题 ≠ config 值的多 Shell 场景（shoot 取证）不会被跟随逻辑拽回。
        self._theme_seen = str(config_io.read_path("ui.theme", "") or "")
        self._orig_texts: dict = {} # 鲸语切换的原文缓存（控件重建后清空）
        # ── 鲸落视觉本体（ocean.py）：底图 + tint + 三层波浪，只在 whale 主题启用 ──
        self._wp_path: Path | None = None # 当前底图来源（含自定义背景判路）
        self._wp_src: "QPixmap | None" = None # 原图
        self._wp_scaled: "QPixmap | None" = None
        self._wp_scaled_for = None # (w, h, dpr) 重缩放判据
        self._backdrop_on = False # whale + 底图可用 才 True
        self.setWindowTitle("群相 控制台") # 落位适配：正式壳不再是「原型」（app.py 启动器同名兜底）
        # #6：任务栏/窗口图标用透明底完整鲸鱼（真机问题⑨：显示的是进程图标）
        icon = QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "icon-whale.png"))
        self.setWindowIcon(icon)
        # #5：去系统边框（对齐微信）—— 拖拽/边缘 resize/贴边走 nativeEvent
        # 的 WM_NCHITTEST（原生 HTCAPTION 才有 Aero Snap、Win+方向键、双击最大化）；
        # 叉号不进顶栏 —— 「停止」承担关停语义，关窗=收进托盘（关窗≠停机）。
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # ⛔ P0-5 真机实锤：默认 mouseMoveEvent 只在**按住鼠标键拖动**时来 ——
        #   波纹要跟着光标自由移动就必须开 mouseTracking（web 侧波纹随鼠标即动，
        #   没有「必须先按住」这一说）。不开就只在拖窗口时才见波纹，等于失效。
        self.setMouseTracking(True)
        self.resize(1120, 720)
        self.setMinimumSize(860, 560)
        self._build()
        self._setup_tray(icon)
        # 视觉本体与鱼光标：建在 _restyle 之前（_restyle 要按 backdrop 分支）
        self._ocean = OceanWaves(self)
        # P0-5：鼠标波纹 —— 与 _ocean **并列的独立对象**（各自 Timer、互不牵连）。
        # 取证结论：Qt 侧原本从未实现波纹；web 侧是 SVG 滤镜
        from wavefx import WaveFX # noqa: PLC0415

        self._wavefx = WaveFX(self)
        self._wavefx.set_enabled(bool(self._wavefx._cfg.get("enabled", True)))
        # P0-5：应用级兜住鼠标移动（内容层会先吃事件，见 eventFilter 说明）
        QApplication.instance().installEventFilter(self)
        self._cursor = WhaleCursor(Path(__file__).resolve().parents[1], parent=self)
        # #12：中键滚轮模式（web PM_WHEEL 完整迁移）—— 注入 WhaleCursor，
        # 事件委托见 cursor_fx.eventFilter；fallback = 当前页 QScrollArea（web scrollerAt 兜底）。
        from pm_wheel import WheelMode # noqa: PLC0415

        self._cursor.wheel = WheelMode(
            self._cursor, self.t,
            fallback=lambda: self.stack.currentWidget() if self.stack is not None else None,
            parent=self,
        )
        self._load_wallpaper()
        self._refresh_backdrop()
        # 右下角鲸鱼挂件（上游 DeepSeek-Balance-Whale-Widget 的 Qt 等价物：
        # 余额/今日已用/每轮消耗，点击刷新、可拖拽；数据与位置自持，随 Shell 显隐）
        from whale_widget import WhaleWidget # noqa: PLC0415

        self.whale = WhaleWidget(self.t, parent=self)
        self.whale.show()
        self._restyle()
        self._cursor.refresh_from_config()

        # 后台探活 —— 原型的关键演示点（顺带当配置轮询：光标/壁纸 4 秒内跟随 web 面板的改动）
        self._probe_timer = QTimer(self)
        self._probe_timer.timeout.connect(self._probe)
        self._probe_timer.start(4000)
        QTimer.singleShot(300, self._probe)
        # #13：更新公告条首拉（web updbar 语义 = 页面加载时 GET /api/update 一次；
        # 9 源竞速可能拖到秒级，后台线程拉）。
        self._upd_state: dict | None = None # 最近一次 /api/update 真值（_rebuild 恢复用）
        QTimer.singleShot(600, self._upd_first_check)
        # #16：暂停/恢复 —— 方向唯一依据 = /api/status 的 paused 字段
        # （web 血泪注释：不许读按钮文字做依据），独立 8 秒轻轮询（web loadStatus
        # 同款间隔）。_pause_busy = 点击防重入（web busy 守卫同款）。
        self._paused = False
        self._pause_busy = False
        QTimer.singleShot(900, self._poll_paused)
        self._pause_timer = QTimer(self)
        self._pause_timer.setInterval(8000)
        self._pause_timer.timeout.connect(self._poll_paused)
        self._pause_timer.start()
        # P0-A①：面板徽章全量接线 —— 一个 8 秒轮询拉 /api/status
        # （+ /api/personas 人设计数），按 panels_qt.badge_for 的 web 同款
        # 口径分发到 27 个面板徽章。旧病根：徽章建出来后全文件无 set，
        # 用户永远看到「读取中」。
        QTimer.singleShot(1200, self._poll_badges)
        self._badge_timer = QTimer(self)
        self._badge_timer.setInterval(8000)
        self._badge_timer.timeout.connect(self._poll_badges)
        self._badge_timer.start()

        # 首次引导向导（web onboarding :5427 的 Qt 等价）：仅「还没配好 Key」才弹，
        # 且本进程只弹一次。延迟到窗口立起来之后再弹，避免与首屏布局抢时机。
        QTimer.singleShot(1500, self._maybe_onboard)

    def _maybe_onboard(self) -> None:
        try:
            import onboarding # noqa: PLC0415

            onboarding.maybe_show(self.t, self)
        except Exception: # noqa: BLE001 — 向导失败不拖垮主窗口
            pass

    # ------------------------------------------------------------ 鲸落视觉本体

    def _wallpaper_source(self) -> Path:
        """底图来源：config `ui.background == 'custom'` 用上传的 data/ui_bg.jpg，
        否则默认海 assets/wallpaper/ocean1.jpg（web applyCustomBg 同款判路）。
        自定义文件缺失 ⇒ 回默认海，不白屏。"""
        root = Path(__file__).resolve().parents[1]
        try:
            from agent.config import get_config # noqa: PLC0415

            custom = (get_config().get("ui") or {}).get("background") == "custom"
        except Exception: # noqa: BLE001
            custom = False
        if custom:
            try:
                from agent.config import DATA_DIR # noqa: PLC0415

                p = Path(DATA_DIR) / "ui_bg.jpg"
                if p.exists():
                    return p
            except Exception: # noqa: BLE001
                pass
        return root / "assets" / "wallpaper" / "ocean1.jpg"

    def _load_wallpaper(self) -> None:
        src = self._wallpaper_source()
        if src == self._wp_path and self._wp_src is not None:
            return
        self._wp_path = src
        pm = QPixmap(str(src)) if src.exists() else QPixmap()
        self._wp_src = None if pm.isNull() else pm
        self._wp_scaled_for = None # 来源变了 ⇒ 强制重缩放

    def _rescale_wp(self) -> None:
        """cover 缩放缓存（30fps 每帧只做 1:1 贴图，缩放只在尺寸变化时做一次）。"""
        if self._wp_src is None:
            self._wp_scaled = None
            return
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if key == self._wp_scaled_for and self._wp_scaled is not None:
            return
        from ocean import cover_pixmap # noqa: PLC0415

        self._wp_scaled = cover_pixmap(self._wp_src, self.width(), self.height(), self.devicePixelRatioF())
        self._wp_scaled_for = key

    def _refresh_backdrop(self) -> None:
        """whale 主题 + 底图可用 ⇒ 开画卷（静底图）；其余主题回到原 flat 底。

         I：**三层海浪动效砍掉**——只留 ocean.jpg
        静底图 + tint。动画 Timer 永不再启动（CPU 同步受益：30fps 局部重绘
        整条链消失）；OceanWaves 类与瓦片渲染保留为设计资产/取证对象。"""
        self._rescale_wp()
        self._backdrop_on = self.t.key == "whale" and self._wp_scaled is not None
        self._ocean.set_active(False) # 波浪动效已砍：任何主题都不再转

    def _watch_config(self) -> None:
        """4 秒一跳的配置跟随（挂在探活定时器上）：web 面板改了光标/背景/主题，这里跟上。"""
        try:
            self._cursor.refresh_from_config()
        except Exception: # noqa: BLE001
            pass
        # P0-5：波纹参数即时生效（web 侧「应用水光波纹设置」同款语义）
        try:
            if getattr(self, "_wavefx", None) is not None:
                self._wavefx.refresh_from_config()
        except Exception: # noqa: BLE001
            pass
        # 主题跟随：**外部**（web 面板/别的进程）改了 ui.theme 才跟 ——
        # 判据是「与上次见过的值不同」，不是「与当前主题不同」：后者会把多 Shell
        # 异构场景（shoot 取证每镜头一主题）和本壳自己切的现场统统拽回 config
        # 旧值。Qt 无 system 跟随，映射表外的键不动。
        try:
            th = str(config_io.read_path("ui.theme", "") or "")
            if th and th != self._theme_seen:
                self._theme_seen = th
                if th in THEMES and th != self.t.key:
                    self._switch_theme(th, persist=False)
        except Exception: # noqa: BLE001
            pass
        wp_before = self._wp_path
        self._load_wallpaper()
        if wp_before != self._wp_path or self._wp_scaled is None:
            self._refresh_backdrop()

    def paintEvent(self, ev) -> None: # noqa: N802
        p = QPainter(self)
        if self._backdrop_on and self._wp_scaled is not None:
            paint_backdrop(p, self.width(), self.height(), self._wp_scaled)
        else:
            p.fillRect(self.rect(), self.t.q("bg"))
        # P0-5：鼠标波纹画在**内容之上**（与 OceanWaves 的壁纸层并列且解耦）
        try:
            if getattr(self, "_wavefx", None) is not None:
                self._wavefx.paint(p, self.width(), self.height(), self.devicePixelRatioF())
        except Exception: # noqa: BLE001   波纹绘制失败绝不能拖垮窗口
            pass

    def mouseMoveEvent(self, ev) -> None: # noqa: N802 P0-5：光标处投石
        self._wave_from_pos(QPointF(ev.position()))
        super().mouseMoveEvent(ev)

    def eventFilter(self, obj, ev) -> bool: # noqa: N802 P0-5：穿透取鼠标
        """内容层（滚动区/标签）会先吃掉 mouseMove ⇒ 光靠 Shell.mouseMoveEvent
        收不到「光标在内容上移动」。这里在**应用级**再兜一层：任何鼠标移动事件
        落到本窗口区域内时都转给波纹，保证 web 同款「随鼠标即动」。

        只在窗口可见且波纹开启时生效，代价可忽略（一次坐标映射 + 一次 update）。
        """
        try:
            if ev.type() == QEvent.Type.MouseMove and self.isVisible():
                gpos = ev.globalPosition()
                if self.rect().contains(self.mapFromGlobal(gpos.toPoint())):
                    self._wave_from_pos(QPointF(gpos) - QPointF(self.mapToGlobal(self.rect().topLeft())))
        except Exception: # noqa: BLE001
            pass
        return super().eventFilter(obj, ev)

    def _wave_from_pos(self, local: QPointF) -> None:
        """把窗口内逻辑坐标喂给波纹（mouseMoveEvent 与 eventFilter 共用）。"""
        try:
            if getattr(self, "_wavefx", None) is not None:
                self._wavefx.on_mouse_move(local)
        except Exception: # noqa: BLE001
            pass

    def resizeEvent(self, ev) -> None: # noqa: N802
        super().resizeEvent(ev)
        self._rescale_wp()
        self._ocean.invalidate()
        try:
            self._wavefx.sync_overlay() # 波纹覆盖层跟随窗口几何
        except Exception: # noqa: BLE001
            pass
        # #17：最大化/还原的画法随窗口态切换（双击顶栏的原生最大化
        # 不走 _toggle_max，挂在 resize 上才盖得住所有进入最大化的路径）
        btn = getattr(self, "btn_max", None)
        if btn is not None:
            btn.update()

    def showEvent(self, ev) -> None: # noqa: N802
        super().showEvent(ev)
        self._ocean.set_active(False) # 波浪动效已砍，show 也不再转
        self._apply_round_corners() # M：Win11 圆角 / Win10 方角回退
        self._ensure_resize_style() # 注入 WS_THICKFRAME（能拖不能缩的真根因）
        w = getattr(self, "whale", None)
        if w is not None:
            w.show() # 从托盘/最小化回来时挂件跟着回来

    def _apply_round_corners(self) -> None:
        """ M：Win11 走 DWM 圆角（DWMWA_WINDOW_CORNER_PREFERENCE = 33，ROUND = 2）。

        一次设置整个窗口生命周期有效 —— showEvent 重复调是幂等的，成本可忽略。
        Win10 上 dwmapi 没有这个属性（返回 E_INVALIDARG）或根本不是 Windows
        （AttributeError）→ **优雅回退方角**：不引 SetWindowRgn 锯齿方案
        。
        """
        try:
            import ctypes # noqa: PLC0415

            hwnd = int(self.winId())
            val = ctypes.c_int(2) # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 33, ctypes.byref(val), ctypes.sizeof(val))
        except Exception: # noqa: BLE001 — Win10/非 Windows：方角即回退
            pass

    _resize_style_done = False
    _thickframe_ok = False # 注入成功才处理 NCCALCSIZE；逃生门/失败时完全跳过

    def _ensure_resize_style(self) -> None:
        """（真机反馈「面板能拖动、不能缩放」）：给无边框窗注入 WS_THICKFRAME。

        根因：FramelessWindowHint 在 Windows 上 = WS_POPUP —— **没有 THICKFRAME 的窗口，
        Windows 会忽略一切 HT*(HTLEFT/HTRIGHT/HTBOTTOMRIGHT…) 缩放请求**；而 HTCAPTION
        拖动不需要 THICKFRAME ⇒「能拖、不能缩」。 P0-4 把 `_hit_test`/`nativeEvent`
        的 Python 层修对了（selftest 八方向全绿），但 Windows 样式层不放行照样缩不动 ——
        同一个坑的第二次：**「Python 逻辑层全绿 ≠ Windows 层接受」**。
        配方：showEvent 注入 WS_THICKFRAME + `nativeEvent` 吃掉 WM_NCCALCSIZE 的边框区
        （视觉保持无边框）。幂等；非 Windows / 注入失败静默回退（不缩放也不崩）。
        """
        if Shell._resize_style_done:
            return
        if os.environ.get("QT_NO_THICKFRAME"):
            # 逃生门：QT_NO_THICKFRAME=1 ⇒ 完全跳过注入与 NCCALCSIZE 接管，
            # 回到「能拖不能缩」的改前安全态。，
            # 该开关用于秒级自救与二分定位（黑屏是否与本配方相关）。
            Shell._resize_style_done = True
            return
        try:
            import ctypes # noqa: PLC0415

            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            _get = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            _set = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            style = int(_get(hwnd, GWL_STYLE))
            if not style & WS_THICKFRAME:
                _set(hwnd, GWL_STYLE, style | WS_THICKFRAME)
                # FRAMECHANGED（0x0020）让样式立刻生效；NOMOVE|NOSIZE 不动几何
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0020)
            Shell._thickframe_ok = True
            Shell._resize_style_done = True
        except Exception: # noqa: BLE001 — 非 Windows / 注入失败：保持现状（不缩放也不崩）
            Shell._resize_style_done = True

    def hideEvent(self, ev) -> None: # noqa: N802
        super().hideEvent(ev)
        self._ocean.set_active(False) # CPU 纪律：看不见就不转
        w = getattr(self, "whale", None)
        if w is not None:
            w.hide() # 收托盘/最小化时挂件一起藏（不留幽灵浮层）

    # ------------------------------------------------------------ 窗口壳

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def nativeEvent(self, etype, message): # noqa: N802
        """WM_NCHITTEST —— 无边框窗口的「原生手感」一刀切：
        顶栏空白=HTCAPTION（原生拖拽 + 双击最大化 + Aero Snap + Win+方向键贴边，
        顶栏上的按钮/分段器、内容区一律放回默认（HTCLIENT，能点能动）。
        非 Windows 平台直接放行。

        ⚠️ P0-B 根治（离屏逐点采样实锤，_c8_hitprobe.py）：
        老顺序「先放行控件、后判边缘热区」有两个结构性死区——
          ① #7 每页套 QScrollArea 后内容控件铺满四缘：左缘命中 Side、
             右缘命中页面滚动条（宽 6px 恰好躺在 8px 热区里）、下缘命中页面
             QLabel ⇒ on_widget 恒 True ⇒ 四边四角分支**永远到不了**，
             用户实测「不能放大缩小」；
          ② 顶栏左侧标题带是纯显示 QLabel（「群相 控制台」/「原生界面 v1.0」），
             命中即 HTCLIENT ⇒ 视觉标题栏一半拖不动。
        修复 = 顺序翻转 + 命中分类：
          · 四边热区**先于**控件放行（右缘滚动条例外放行，留给滚动交互）；
          · 顶栏子树内命中只放行**可交互**控件（按钮/输入/下拉/滚动条/鲸鱼徽章/
            更新胶囊/popover），纯显示 QLabel（含状态徽章）穿透为 HTCAPTION。
        判定核心抽在 _hit_test()（gp=窗口逻辑坐标）——取证脚本 _c8_hitprobe.py
        直接调它，保证「生产代码与取证代码同一逻辑」（ 教训：脚本内复刻
        一套只会原地踏步）。
        """
        if etype == b"windows_generic_MSG":
            try:
                import ctypes.wintypes as wt # noqa: PLC0415

                # ⛔ P0-4 真机实锤根因（_c10_real.py 真窗口跑出来）：
                #   `QCursor` 属于 **QtGui**，不在 QtCore —— 原写法
                #   `from PySide6.QtCore import QCursor` 每次都 ImportError，
                #   被本函数外层 except 吞掉 → 直接 return False,0（HTCLIENT）
                #   ⇒ **真机上 WM_NCHITTEST 永远走不到命中判定**，拖拽/缩放全失效。
                # 的 offscreen 单测直接调 _hit_test() 绕过了 nativeEvent，
                #   所以「逻辑对、真机废」。修：正确模块 + 把导入提到 try 外，
                #   避免它与命中判定共享的 except 互相掩盖。
                from PySide6.QtGui import QCursor # noqa: PLC0415

                msg = wt.MSG.from_address(int(message))
                if msg.message == 0x0083 and Shell._thickframe_ok:
                    # WM_NCCALCSIZE —— /仅 THICKFRAME 注入成功后才接管；
                    # 逃生门（QT_NO_THICKFRAME）或注入失败时完全放行 Qt 默认处理。
                    # ⛔ 
                    #   本机实测 THICKFRAME 窗口最大化时 **窗口 rect = 屏幕原生尺寸**
                    #   （frame=[0,0,1707,1067] == native，系统**不做**边框外扩）；
                    #   此前按「系统会外扩一圈」的假设在最大化时把客户区 RECT 内缩 dx
                    #   ⇒ 客户区变成 [7,8,1693,1052] —— 四周那圈 7-8px 就是白条+漏边。
                    #   ⇒ 最大化**不做任何内缩**：客户区 = 窗口 rect = 铺满屏幕。
                    #     （若个别机器真有外扩行为，代价只是内容贴边，也好过白条。）
                    if msg.wParam: # wParam=TRUE ⇒ 系统要画 non-client 边框区
                        return True, 0 # 0 = 客户区=整个窗口（无边框视觉保住）
                    return False, 0
                if msg.message == 0x0084: # WM_NCHITTEST
                    gp = self.mapFromGlobal(QCursor.pos()) # 全程 Qt 逻辑坐标，免 DPI 换算
                    r = self._hit_test(gp)
                    # P0-4：真机诊断开关（QT_HITTEST_LOG=1 时逐次落日志）。
                    # 的 offscreen 单测证明了 _hit_test **逻辑**对，但真机复验仍失效
                    # ⇒ 必须看清真机上到底有没有走到这里、etype/message 是什么。
                    if os.environ.get("QT_HITTEST_LOG"):
                        try:
                            with open(Path(__file__).resolve().parents[1] / "logs" / "hittest.log", "a",
                                      encoding="utf-8") as _f:
                                _f.write("WM_NCHITTEST gp=(%d,%d) hit=%s -> %s\n"
                                         % (gp.x(), gp.y(),
                                            type(self.childAt(gp)).__name__, r))
                        except Exception: # noqa: BLE001
                            pass
                    if r is None: # 放行：默认处理（HTCLIENT）
                        return False, 0
                    # ⛔ P0-4 真机实锤的第 2 个根因（同一次真机跑出来）：
                    #   `_hit_test` 返回的是 **_HT 的键名字符串**（"top"/"left"…），
                    #   原写法 `self._HT[r[0]]` 把它当成元组取首元素 ⇒ 得到 't' ⇒
                    #   KeyError，被外层 except 吞掉 → return False,0（HTCLIENT）
                    #   ⇒ 真机拖拽/缩放**永远失效**。正确写法是 `self._HT[r]`。
                    return True, self._HT[r]
            except Exception: # noqa: BLE001   命中测试失败绝不能拖垮窗口
                if os.environ.get("QT_HITTEST_LOG"):
                    try:
                        import traceback # noqa: PLC0415

                        with open(Path(__file__).resolve().parents[1] / "logs" / "hittest.log", "a",
                                  encoding="utf-8") as _f:
                            _f.write("nativeEvent 异常（etype=%r）：\n%s\n" % (etype, traceback.format_exc()))
                    except Exception: # noqa: BLE001
                        pass
                return False, 0
        return super().nativeEvent(etype, message)

    _HT = {"client": 1, "caption": 2, "left": 10, "right": 11,
           "top": 12, "topleft": 13, "topright": 14,
           "bottom": 15, "bottomleft": 16, "bottomright": 17}

    def _hit_test(self, gp) -> str | None:
        """WM_NCHITTEST 判定核心。gp=窗口内 Qt 逻辑坐标；返回 _HT 键名，
        None=放行系统默认（HTCLIENT）。offscreen 可直接调（取证/断言共用）。
        顺序与根因见 nativeEvent docstring。"""
        from PySide6.QtWidgets import ( # noqa: PLC0415
            QAbstractButton,
            QAbstractSlider,
            QComboBox,
            QLineEdit,
        )

        w, h, m = self.width(), self.height(), 8
        bar = getattr(self, "titlebar", None)
        hit = self.childAt(gp)
        # ① 四边四角热区（比控件命中优先 —— 见上，内容铺满后
        #    「先放行控件」会让边缘永远 HTCLIENT）。
        # ⛔ P0-4 真机实锤第 3 处（team-lead 亲验复现，6/8 命中）：
        #   右缘此前被**整条无条件豁免**给滚动条 ⇒ right/bottomright 死区。
        #   上一版收窄成 `_on_scrollbar()` 仍没修好——因为内容区的竖向滚动条
        #   高 660px、**纵向覆盖整个内容区**（实测窗口坐标 x=[1094,1100)
        #   y=[60,720)），而右缘热区只有 8px（x>=1092）⇒ 热区里除最内 2px 外
        #   全被判成「压在滚动条上」⇒ 右缘中段整条死掉。
        #   ⇒ 正确语义（对齐 Windows 原生）：**滚动条只让出它自己那 6px 本体**，
        #     热区里落在它内侧的部分照常给 right；两个**缩放角**（topright /
        #     bottomright）优先于滚动条例外（角点是缩放专用区，不该被滚动条抢）。
        near_l = gp.x() <= m
        near_t, near_b = gp.y() <= m, gp.y() >= h - m

        def _sb_x_left() -> float | None:
            """若右缘那一列压着**可见的竖向滚动条**，返回它在窗口坐标里的左边界 x；
            否则 None（右缘没有滚动条挡路，整条热区都能缩放）。

            ⛔ 不能用 `childAt(gp)` 判：热区里只有滚动条**本体**
              那 6px 的 childAt 才是滚动条；热区更靠内的部分拿到的是内容 QFrame
               ⇒ 用 childAt 判会漏掉「右缘被挡」这个事实，加宽逻辑永远不触发。
               ⇒ 改成**几何反查**：直接遍历右缘那一列的可见竖向滚动条。
            """
            try:
                from PySide6.QtWidgets import QScrollBar # noqa: PLC0415

                for sb in self.findChildren(QScrollBar):
                    if not sb.isVisible() or sb.orientation() != Qt.Orientation.Vertical:
                        continue
                    tl = sb.mapTo(self, sb.rect().topLeft())
                    x0 = tl.x()
                    x1 = x0 + sb.width()
                    y0 = tl.y()
                    y1 = y0 + sb.height()
                    # 该滚动条是否压在右缘热区内、且纵向覆盖当前光标
                    if x1 > w - m and x0 < w and y0 <= gp.y() <= y1:
                        return float(x0)
            except Exception: # noqa: BLE001
                pass
            return None

        sb_left = _sb_x_left()
        # 右缘热区：默认 w-m..w-1；若被滚动条挡住，向左加宽到「滚动条左侧仍有
        # m 宽」。注意 near_r 必须**先**由加宽后的下界算出（不能先算 near_r 再
        # 用它决定要不要查 sb_left —— 那样热区更靠内的点会因初始 near_r=False
        # 而永远查不到滚动条，加宽逻辑永不触发，返工时踩过）。
        right_edge = sb_left - m if sb_left is not None else w - m
        near_r = gp.x() >= min(w - m, right_edge)
        # 角点优先：上下两个右角落在滚动条纵向范围内时**不放行**（缩放优先）。
        at_corner = (near_t or near_b) and near_r
        # 中段：只有 gp.x() 真落在滚动条 6px 本体（x >= sb_left）才放行给滚动。
        on_sb_body = (sb_left is not None) and (gp.x() >= sb_left) and not at_corner

        if (near_l or near_r or near_t or near_b) and not on_sb_body:
            if near_t and near_l:
                return "topleft"
            if near_t and near_r:
                return "topright"
            if near_b and near_l:
                return "bottomleft"
            if near_b and near_r:
                return "bottomright"
            if near_l:
                return "left"
            if near_r:
                return "right"
            if near_t:
                return "top"
            if near_b:
                return "bottom"
        # ② 顶栏子树内命中：可交互控件放行；纯显示控件穿透为标题栏
        if hit is not None and hit is not bar \
                and bar is not None and bar.isAncestorOf(hit):
            cur = hit
            while cur is not None and cur is not bar:
                if isinstance(cur, (QAbstractButton, QAbstractSlider,
                                    QComboBox, QLineEdit)):
                    return None # 可交互：默认处理，能点能动
                if cur.objectName() in ("UpdPill", "Popover"):
                    return None # 更新胶囊/下滑面板（自管鼠标）
                if type(cur).__name__ == "WhaleBadge":
                    return None # 鲸鱼徽章有拖拽交互
                cur = cur.parentWidget()
            return "caption" # 纯显示 QLabel：当标题栏拖
        # ③ 内容区控件照旧放行（按钮/输入/滚动全不受影响）
        if hit is not None and hit is not bar:
            return None
        # ④ 顶栏空白 / 内容空白
        if gp.y() < 60:
            return "caption"
        return "client"

    def _setup_tray(self, icon: QIcon) -> None:
        """关窗≠停机的落点：鲸鱼托盘（菜单：显示主窗 / 停止）。

        agent/tray.py 是 raw Win32 线程托盘（后端通知用）；Qt 壳里
        QSystemTrayIcon 与界面同事件循环，菜单/激活信号直连槽，不另起线程。
        """
        self._tray = None
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return # 无托盘环境（服务器/精简系统）：关窗退回真关，机器人不受影响
            menu = QMenu(self)
            # 全局 QSS 的 QWidget{background:transparent} 会把弹窗打成透明 —— 菜单自带上底
            t = self.t
            menu.setStyleSheet(
                f"QMenu{{background:{t.card};color:{t.tx};border:1px solid {t.bd};border-radius:10px;}}"
                f"QMenu::item{{padding:7px 22px;}}"
                f"QMenu::item:selected{{background:{t.blue_soft};}}"
            )
            menu.addAction("显示主窗", self._tray_show)
            menu.addSeparator()
            menu.addAction("停止", self._tray_stop) # 与顶栏「停止」同一确认流
            self._tray = QSystemTrayIcon(icon, self)
            self._tray.setContextMenu(menu)
            self._tray.setToolTip("群相 控制台 —— 机器人正在跑，点图标回到主窗")
            self._tray.activated.connect(
                lambda r: self._tray_show()
                if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self._tray.show()
        except Exception: # noqa: BLE001
            self._tray = None # 托盘失败不拦启动

    def _tray_show(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _tray_stop(self) -> None:
        # 主窗藏着时先亮出来 —— 确认弹窗要居中在用户看得见的地方
        if not self.isVisible():
            self._tray_show()
        self._bot_stop()

    def closeEvent(self, ev) -> None: # noqa: N802
        """Alt+F4 / 任务栏关闭 = 收进托盘（web 版口径：关窗≠停机）。
        真正的关停在「停止」钮：POST /api/shutdown 带打字门槛确认。"""
        if getattr(self, "_tray", None) is not None and self._tray.isVisible():
            ev.ignore()
            self.hide()
            self._ocean.set_active(False) # CPU 纪律：藏起来就停画
            self._tray.showMessage(
                "群相 控制台", "机器人还在跑，窗口收进托盘了。点托盘图标可再打开。",
                QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        ev.accept() # 无托盘环境：退回真关（机器人主循环在另一条线程，不受影响）

    # ------------------------------------------------------------ 结构

    def _build(self) -> None:
        # 复用已有顶层布局（_rebuild 重建时 QWidget 的 d->layout 指针卸不掉，
        # 再 QVBoxLayout(self) 会撞 "already has a layout" 警告且新布局装不上，
        # 重建后整窗失去几何管理 —— 主题跟随功能实测踩中）。
        root = self.layout()
        if root is None:
            root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_titlebar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_side(), 0)
        body.addWidget(self._build_main(), 1)
        root.addLayout(body, 1)

        # #4 加固 + #7 滚动页：画卷是 Shell.paintEvent 整窗画的（z 序最底），
        # 内容区能不能透出海取决于中间容器的底。全局 QSS 已把 QWidget 打透明，
        # 但 QScrollArea 的 viewport 在真机/不同平台上有 palette 兜底
        # （qt_scrollarea_viewport 的 Base 色），显式点名最稳 —— 覆盖侧栏与
        # 全部页面滚动区。
        for sc in self.findChildren(QScrollArea):
            vp = sc.viewport()
            vp.setStyleSheet("background:transparent;")
            vp.setAutoFillBackground(False)
        self.stack.setStyleSheet("background:transparent;")

        # 侧栏收起状态恢复（web navTight localStorage 等价；items 已齐才能应用）
        try:
            from PySide6.QtCore import QSettings # noqa: PLC0415

            if QSettings("WXAgent", "persona-morph-ui").value("nav_tight", False, type=bool):
                self._set_tight(True)
        except Exception: # noqa: BLE001
            pass

    def _build_titlebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TitleBar")
        self.titlebar = bar # nativeEvent 命中测试要用（空白区=HTCAPTION）
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(10)

        # 顶栏鲸鱼徽章 —— web 侧 `.whale-badge` 的 Qt 复刻（52×52 近黑底 + 真图，icons 不手画）
        from widgets import WhaleBadge # noqa: PLC0415

        lay.addWidget(WhaleBadge())

        name = QLabel("群相 控制台")
        name.setFont(qfont(self.t, 14, 600, display=True))
        name.setStyleSheet(f"color:{self.t.tx};background:transparent;")
        lay.addWidget(name)

        ver = QLabel("原生界面 v1.0") # 落位适配：版本标签摘掉「原型」字样
        ver.setFont(qfont(self.t, 11))
        ver.setStyleSheet(f"color:{self.t.tx2};background:transparent;")
        lay.addWidget(ver)

        # 更新公告胶囊：顶栏只留胶囊，
        # 点开从下方滑出面板（notes 逐条 + 三按钮 + 进度态）—— 把三按钮
        # 塞进顶栏是设计失误（用户截图：三按钮挤成墨块；web 真值 updBar 本是
        # 顶栏下方独立一行）。API 链路沿用只换 UI 形态。
        from updbar import UpdateBar # noqa: PLC0415

        self.updbar = UpdateBar(self.t)
        self.updbar.apply_state(getattr(self, "_upd_state", None))
        lay.addWidget(self.updbar)

        # ── 余额徽章（web balance-badge :703 + loadBalance :3208-3221 + 30s 轮询 :7419）──
        #    显示模式（照实/隐藏/改数字）存 config 的 ui.balance_*，只改显示不动真实余额。
        import config_io as _cio # noqa: PLC0415
        from panels_custom import _balance_text # noqa: PLC0415

        self.bal_badge = QLabel("余额 查询中…")
        self.bal_badge.setObjectName("balanceBadge")
        self.bal_badge.setFont(qfont(self.t, 11.5))
        self.bal_badge.setStyleSheet(f"color:{self.t.tx2};background:transparent;")
        self.bal_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bal_badge.setToolTip("点击刷新余额（只读查询）")
        self.bal_badge.mousePressEvent = lambda _e: self._load_balance()
        self.bal_mask = QPushButton("显示", bar)
        self.bal_mask.setObjectName("balMask")
        self.bal_mask.setFixedHeight(26)
        self.bal_mask.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bal_mask.setToolTip("余额显示（只改界面上的数字，不动真实余额）")
        self.bal_mask.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:8px;"
            f"color:{self.t.tx3};padding:0 6px;}}"
            f"QPushButton:hover{{background:{rgba(self.t.q('tx'), 14).name(QColor.NameFormat.HexArgb)};}}"
        )
        self.bal_mask.clicked.connect(self._balance_mask)
        self.bal_timer = QTimer(self)
        self.bal_timer.setInterval(30000) # web :7419 同款 30s
        self.bal_timer.timeout.connect(self._load_balance)
        self.bal_timer.start()
        lay.addWidget(self.bal_badge)
        lay.addWidget(self.bal_mask)

        lay.addStretch(1)

        # ── 外观切换图标（ #15，用户点单：文案 + 主题「本质上都属于一种界面
        # 切换」，常态收起只显示图标，点开弹出切换组）──
        # 控件复用现有 Segmented，只是从顶栏搬进 Popover（改动最小）。
        import icons as _icons # noqa: PLC0415

        self.btn_look = QPushButton(bar)
        self.btn_look.setFixedSize(36, 34)
        self.btn_look.setIcon(QIcon(_icons.appearance_pixmap(self.t.tx, 20))) # F：tx2 太灰，提亮到全亮字色
        self.btn_look.setIconSize(QSize(20, 20))
        self.btn_look.setToolTip("外观切换（文案 / 主题）")
        self.btn_look.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_look.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:8px;}"
            f"QPushButton:hover{{background:{rgba(self.t.q('tx'), 14).name(QColor.NameFormat.HexArgb)};}}"
        )
        self.btn_look.clicked.connect(
            lambda: self._look_pop.toggle_at(self.btn_look, width=250, align="right"))
        lay.addWidget(self.btn_look)

        # 外观 popover：挂在 titlebar 下（主题重建销毁顶栏时面板随葬，不留幽灵浮层）
        from popover import Popover # noqa: PLC0415

        self._look_pop = Popover(self.t, parent=bar)
        cap_tx = QLabel("文案")
        cap_tx.setFont(qfont(self.t, 10.5))
        cap_tx.setStyleSheet(f"color:{self.t.tx3};background:transparent;")
        self._look_pop.add(cap_tx)
        from widgets import Segmented # noqa: PLC0415

        self.style_seg = Segmented(
            self.t, [("normal", "正常"), ("whale", "鲸语")], value=getattr(self, "text_style", "normal")
        )
        self.style_seg.changed.connect(self._apply_text_style)
        self._look_pop.add(self.style_seg)
        cap_th = QLabel("主题")
        cap_th.setFont(qfont(self.t, 10.5))
        cap_th.setStyleSheet(f"color:{self.t.tx3};background:transparent;")
        self._look_pop.add(cap_th)
        self.theme_seg = Segmented(
            self.t,
            [(k, tk.label.split(" · ")[0].split("（")[0]) for k, tk in THEMES.items()],
            value=self.t.key,
        )
        self.theme_seg.changed.connect(self._switch_theme)
        self._look_pop.add(self.theme_seg)

        # 整体状态徽章（对齐 web 侧顶栏那个"在跑/没连上"）
        self.st_top = Badge(self.t, "idle", "读取中")
        lay.addWidget(self.st_top)

        # ── 暂停/恢复──
        # web 控制台顶栏一直有 pauseBtn，~6 落位时漏了（用户抓的）。
        # 语义：暂停 = 进程活着只是不回任何消息（≠ 停止 = 进程退出）⇒
        # 轻按钮 ghost 档、不需要危险确认。方向唯一依据 = /api/status 的
        # paused 字段（web 血泪注释：不许读按钮文字做依据）。
        self.btn_pause = Btn("暂停", self.t, "ghost")
        self.btn_pause.setToolTip("暂停后机器人不回复任何消息（进程还活着，不是停止）")
        self.btn_pause.clicked.connect(self._on_pause_click)
        lay.addWidget(self.btn_pause)

        # 机器人控制：重启/停止 —— web 侧顶栏同款动作，走原生确认弹窗。
        # 路由是 /api/restart、/api/shutdown（POST），经 agent_bridge.post_api 发出，
        # agent/ 一行不改。danger 红钮只给「停止」—— 它不可一键反悔。
        self.btn_restart = Btn("重启", self.t, "ghost")
        self.btn_restart.clicked.connect(self._bot_restart)
        lay.addWidget(self.btn_restart)

        self.btn_stop = Btn("停止", self.t, "danger")
        self.btn_stop.clicked.connect(self._bot_stop)
        lay.addWidget(self.btn_stop)

        # 窗口控制：自绘 IconBtn ——
        # 最小化=粗横线、最大化/还原=直角方框（还原态双框交叠），无边框无底色
        # hover 浅底，笔画 2px。**没有叉号** ——
        # 关窗≠停机：Alt+F4/任务栏关闭收进托盘，真正的关停语义在「停止」钮。
        self.btn_min = IconBtn(self.t, "min", bar)
        self.btn_min.setToolTip("最小化")
        self.btn_min.clicked.connect(self.showMinimized)
        lay.addWidget(self.btn_min)

        self.btn_max = IconBtn(self.t, "max", bar)
        self.btn_max.setToolTip("最大化 / 还原（双击顶栏同款）")
        self.btn_max.clicked.connect(self._toggle_max)
        lay.addWidget(self.btn_max)

        return bar

    def _load_balance(self) -> None:
        """余额徽章数据（web loadBalance :3208-3221）：GET /api/balance 一次，
        按显示模式出文本；失败如实说，绝不拼 undefined。"""
        import config_io as _cio # noqa: PLC0415
        from panels_custom import _balance_text # noqa: PLC0415

        try:
            b = _cio.get_json("/api/balance", timeout=6.0)
        except Exception: # noqa: BLE001
            b = None
        display = str(_cio.read_path("ui.balance_display") or "real")
        fake = str(_cio.read_path("ui.balance_fake") or "")
        self.bal_badge.setText(_balance_text(b, display, fake))
        self.bal_mask.setText({"real": "显示", "hide": "已隐藏"}.get(display, "已改"))

    def _balance_mask(self) -> None:
        """余额显示三选一（web balMask :5564-5610）：照实 / 隐藏 / 改成指定数字。
        只改 config 的 ui.balance_*，真实余额与查询、账目一点都不动。"""
        from panels_custom import _card_dialog # noqa: PLC0415

        import config_io as _cio # noqa: PLC0415

        t = self.t
        cur = str(_cio.read_path("ui.balance_display") or "real")
        cur_fake = str(_cio.read_path("ui.balance_fake") or "")
        dlg, v = _card_dialog(t, self, "余额显示", width=460)
        dlg.resize(460, 320)
        tip = QLabel("只改界面上的数字，真实余额一点都不动（查询与账目照旧）。")
        tip.setFont(qfont(t, 12))
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{t.tx2};background:transparent;")
        v.addWidget(tip)
        from widgets import Btn as _Btn # noqa: PLC0415

        b_real = _Btn("照实显示", t, "primary" if cur == "real" else "ghost")
        v.addWidget(b_real)
        b_hide = _Btn("隐藏（不显示金额）", t, "primary" if cur == "hide" else "ghost")
        v.addWidget(b_hide)
        ed = QLineEdit(cur_fake)
        ed.setPlaceholderText("改成这个数字，例如 8888.88")
        ed.setFont(qfont(t, 12.5))
        v.addWidget(ed)
        b_fake = _Btn("用这个数字显示", t, "primary" if cur == "fake" else "ghost")
        v.addWidget(b_fake)
        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = _Btn("取消", t, "ghost")
        row.addWidget(b_cancel)
        v.addLayout(row)

        def _save(mode: str, fake: str) -> None:
            ok, why = _cio.write_patch({"ui.balance_display": mode,
                                        "ui.balance_fake": fake})
            if not ok:
                tip.setText(f"保存失败：{why}")
                return
            self._load_balance()
            dlg.accept()

        b_real.clicked.connect(lambda: _save("real", ""))
        b_hide.clicked.connect(lambda: _save("hide", ""))
        b_fake.clicked.connect(lambda: _save("fake", ed.text().strip()) if ed.text().strip()
                               else tip.setText("先填一个数字"))
        b_cancel.clicked.connect(dlg.reject)
        dlg.exec()

    def _build_side(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Side")
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)

        # 搜索框 —— 对齐 web 侧新加的 `#navFind`
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(12, 0, 12, 0)
        self.find = SearchBox(self.t)
        self.find.textChanged.connect(self._on_find)
        wl.addWidget(self.find)
        lay.addWidget(wrap)

        # 导航搜索键盘交互（对齐 web `#navFind` 的 keydown，console_html.py:7122-7128）：
        #   · `/`  全局聚焦搜索框（**输入态里不触发**，web :7151-7157 同口径）
        #   · Esc  清空并失焦（web :7123）
        #   · Enter 跳到当前唯一/首个可见项（web :7124-7127 点第一个候选）
        self.find.returnPressed.connect(self._find_enter)
        self._install_shortcuts()

        # 状态框 1:1 复刻 web `.side .status`（console_html.py L517-519）——
        #   background=blue-soft / border=**blue-line**（此前拿 blue 当边框 ⇒ 一圈亮蓝，
        # ）/ radius 10 / padding 10 12；标题 b 13px 蓝；正文 p 12px。
        sbox = QFrame()
        sbox.setObjectName("SideStatusBox")
        sbox.setStyleSheet(
            "QFrame#SideStatusBox{background:%s;border:1px solid %s;border-radius:10px;}"
            "QFrame#SideStatusBox QLabel{background:transparent;border:none;}"
            % (self.t.blue_soft, self.t.blue_line))
        sv = QVBoxLayout(sbox)
        sv.setContentsMargins(12, 10, 12, 10)
        sv.setSpacing(4)
        t_cap = QLabel("运行状态")
        t_cap.setFont(qfont(self.t, 13, 600))
        t_cap.setStyleSheet(f"color:{self.t.blue};")
        sv.addWidget(t_cap)
        self.side_status = QLabel("未连接") # web 真值初值（console_html.py L929）
        self.side_status.setFont(qfont(self.t, self.t.body_size - 1))
        self.side_status.setStyleSheet(f"color:{self.t.tx2};")
        self.side_status.setWordWrap(True)
        sv.addWidget(self.side_status)
        # 左右各缩 10px——
        #   搜索框同款做法（wrap+margins），视觉上与下方导航对齐但略收。
        swrap = QWidget()
        swl = QHBoxLayout(swrap)
        swl.setContentsMargins(10, 0, 10, 0)
        swl.addWidget(sbox)
        lay.addWidget(swrap)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.nav_lay = QVBoxLayout(inner)
        self.nav_lay.setContentsMargins(8, 4, 8, 4)
        self.nav_lay.setSpacing(0)

        self.items: list[tuple[NavItem, NavGroup, str, str]] = []
        # 分组折叠跨会话记忆（web `navGrpClosed` localStorage 的 QSettings 等价，
        # console_html.py:7043-7067）：以「被折叠的分组标题」为键，跨进程记住。
        from PySide6.QtCore import QSettings as _QS # noqa: PLC0415

        _qs = _QS("WXAgent", "persona-morph-ui")
        _closed_now = _qs.value("nav_grp_closed", [], type=list) or []
        _closed_now = [str(x) for x in _closed_now]
        for title, grp_key, entries in NAV:
            g = NavGroup(self.t, title)
            for label, hint, sec in entries:
                it = NavItem(self.t, label, hint, icon_key=sec)
                it.clicked.connect(lambda _=False, s=sec, l=label: self._go(s, l))
                g.add(it)
                self.items.append((it, g, sec, label))
            # 恢复折叠态（跨会话）—— 在 add 完条目之后置位，避免空组头闪烁
            if title in _closed_now:
                g.collapsed = True
                g.body.setVisible(False)
                g.gc.setPixmap(g._icons.chevron_pixmap(self.t.tx3, collapsed=True))
            g.toggled.connect(self._on_grp_toggled)
            self.nav_lay.addWidget(g)
        self.nav_lay.addStretch(1)

        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

        # 收起（对齐 web 侧 `‹ 收起`）
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 0, 12, 0)
        self.btn_tight = Btn("‹ 收起", self.t, "ghost")
        self.btn_tight.clicked.connect(self._toggle_tight)
        fl.addWidget(self.btn_tight)
        lay.addWidget(foot)
        return side

    def _build_main(self) -> QWidget:
        """主区 = 页栈：机器人主面板（原型既有那张）+「日常」「智能」组 8 张新面板。

        web 侧 27 个 sec 本是同一滚动页里显隐切换；原生壳用 QStackedWidget
        做真正的换页 —— 这是"切页"这个交互在 Qt 里比 web 侧更直接的地方。

         J（性能取证实锤：主题切换 _rebuild 全量重建 27 面板 19-21s，
        真机 3-5s 卡顿真因）改**惰性构建**：启动只建 "bot" 主面板，
        其余 sec 记入 _lazy，首次点击导航（_go）时现场构建 —— 页面切换
        本身 13-24ms 从来不卡，卡的是把没看过的面板也提前建了。
        """
        from PySide6.QtWidgets import QStackedWidget # noqa: PLC0415

        self.stack = QStackedWidget()
        self._page_of: dict[str, int] = {"bot": 0}
        self._lazy: set[str] = set(BATCH_SECS) - {"bot"} # 未建 sec 记账，首访现场建
        self.stack.addWidget(self._wrap_scroll(self._build_bot_panel()))
        return self.stack

    def _ensure_page(self, sec: str) -> None:
        """ J：sec 页面惰性构建 —— 首次导航到才建（一次构建永久复用）。"""
        if sec not in getattr(self, "_lazy", set()):
            return
        self._lazy.discard(sec)
        # 保存成功 → _watch_config：光标/壁纸/主题 4s 内不再等探活，当场跟上
        self.stack.addWidget(self._wrap_scroll(build_panel(self.t, sec, on_save=self._watch_config)))
        self._page_of[sec] = self.stack.count() - 1

    @staticmethod
    def _wrap_scroll(page: QWidget) -> QScrollArea:
        """页面套滚动容器。

        真机问题①的根因不是 DPI：主面板/模型页等内容**超一屏**时，
        页面 QVBoxLayout 把压缩量全打在可压缩的标签/描述上 —— 标签与描述
        直接叠成一坨（150% 截图复现实锤，1.0 档同样叠）。web 版是整页滚动，
        Qt 版对齐：每页套 QScrollArea，内容再高也只滚不压。
        """
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(page)
        return sc

    def _build_bot_panel(self) -> QWidget:
        main = QFrame()
        main.setObjectName("Main")
        lay = QVBoxLayout(main)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)

        # 面板头 + 状态徽章（对齐 web 侧 `.sec-hd`）
        self.st_panel = Badge(self.t, "idle", "读取中")
        lay.addWidget(h2(self.t, "机器人与响应档位", self.st_panel))
        lay.addWidget(
            desc(
                self.t,
                "机器人怎么称呼自己、响应到什么程度。改完保存即生效；涉及身份项的改完建议重启一次。",
            )
        )

        # ── 设置卡：四行全接真配置──
        #   机器人昵称→wechat.bot_nickname / 自我称呼→persona.self_nickname /
        #   响应档位→store.context_tier（后端 float() 消费，1-4 离散）/
        #   鲸语模式→ui.text_style（保存后走顶栏同一条 _apply_text_style 路径）。
        #   原「开机自启」行删除：config 里没有这个键，原型那行是摆设；换上真键。
        card = Card(self.t)
        self.card = card

        self.nick = self._line(
            str(config_io.read_path("wechat.bot_nickname", "") or ""),
            placeholder="它叫什么",
        )
        card.body.addWidget(
            Field(self.t, "机器人昵称", "群里别人 @它 时用的名字", self.nick, card)
        )
        card.body.addWidget(self._divider())

        self.self_nick = self._line(
            str(config_io.read_path("persona.self_nickname", "") or ""),
            placeholder="留空=机器人昵称，用于识别「我」",
        )
        card.body.addWidget(
            Field(self.t, "自我称呼", "它怎么称呼自己", self.self_nick, card)
        )
        card.body.addWidget(self._divider())

        tier_labels = ["1 档：仅艾特", "2 档：+关键词", "3 档：+随机", "4 档：全响应"]
        self.tier = _Combo(tier_labels, self.t)
        raw_tier = config_io.read_path("store.context_tier", 2)
        try:
            self.tier.cb.setCurrentIndex(max(0, min(3, int(float(raw_tier)) - 1)))
        except (TypeError, ValueError):
            self.tier.cb.setCurrentIndex(1)
        card.body.addWidget(
            Field(
                self.t,
                "响应档位",
                "1 档只回艾特；2 档加关键词；3 档再加随机；4 档全回",
                self.tier,
                card,
            )
        )
        card.body.addWidget(self._divider())

        self.sw_emoji = Switch(self.t, self.text_style == "whale")
        card.body.addWidget(
            Field(
                self.t,
                "鲸语模式",
                "把界面上这些词换成它自己的说法（这个功能保留 emoji）",
                self.sw_emoji,
                card,
            )
        )
        lay.addWidget(card)

        # ── 保存行：真写 config.json（config_io.write_patch）+ 回执 ──
        srow = QHBoxLayout()
        srow.setSpacing(10)
        self.btn_save_bot = Btn("保存设置", self.t, "primary")
        self.btn_save_bot.clicked.connect(self._save_bot_panel)
        srow.addWidget(self.btn_save_bot)
        self.bot_note = QLabel("")
        self.bot_note.setFont(qfont(self.t, 12))
        srow.addWidget(self.bot_note)
        srow.addStretch(1)
        lay.addLayout(srow)

        # ── 危险操作 + 二次确认 ──
        danger = Card(self.t)
        danger.body.addWidget(h2(self.t, "危险操作"))
        danger.body.addWidget(
            desc(self.t, "下面的动作会动到数据。每个都会再问你一次，并且写清代价。")
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_reset = Btn("清空全部记忆", self.t, "danger")
        self.btn_reset.clicked.connect(self._confirm_reset)
        row.addWidget(self.btn_reset)
        self.btn_wipe = Btn("重置访问口令", self.t, "ghost")
        self.btn_wipe.clicked.connect(self._confirm_key)
        row.addWidget(self.btn_wipe)
        row.addStretch(1)
        danger.body.addLayout(row)
        lay.addWidget(danger)

        # ── 自愈提示区：这是本原型最想让你看到的一块 ──
        self.heal_card = Card(self.t)
        self.heal_card.body.addWidget(h2(self.t, "后台状态"))
        self.heal_title = QLabel("—")
        self.heal_title.setFont(qfont(self.t, 13, 500))
        self.heal_title.setWordWrap(True)
        self.heal_card.body.addWidget(self.heal_title)
        self.heal_body = QLabel("—")
        self.heal_body.setFont(qfont(self.t, 12.5))
        self.heal_body.setWordWrap(True)
        self.heal_card.body.addWidget(self.heal_body)
        hrow = QHBoxLayout()
        self.heal_btn = Btn("重连", self.t, "primary")
        self.heal_btn.clicked.connect(self._probe)
        hrow.addWidget(self.heal_btn)
        self.heal_btn2 = Btn("假装后台挂了", self.t, "ghost")
        self.heal_btn2.clicked.connect(self._simulate_dead)
        hrow.addWidget(self.heal_btn2)
        hrow.addStretch(1)
        self.heal_card.body.addLayout(hrow)
        lay.addWidget(self.heal_card)

        lay.addStretch(1)
        return main

    def _save_bot_panel(self) -> None:
        """机器人主面板四行真写盘（config_io.write_patch → save→set）。

        鲸语模式保存后立即应用：走顶栏 Segmented 同一条 `_pick` 路径，
        发 changed → `_apply_text_style` 统一换词，两处切换器不会打架。
        """
        style = "whale" if self.sw_emoji.isChecked() else "normal"
        ok, msg = config_io.write_patch(
            {
                "wechat.bot_nickname": self.nick.text().strip(),
                "persona.self_nickname": self.self_nick.text().strip(),
                "store.context_tier": int(self.tier.cb.currentIndex() + 1),
                "ui.text_style": style,
            }
        )
        if ok:
            self.bot_note.setText(f"已保存 {time.strftime('%H:%M:%S')}")
            self.bot_note.setStyleSheet(f"color:{self.t.ok};border:none;")
            if style != self.text_style and hasattr(self, "style_seg"):
                self.style_seg._pick(style) # noqa: SLF001  同包内复用（发 changed 即应用）
        else:
            self.bot_note.setText(f"没保存成：{msg}")
            self.bot_note.setStyleSheet(f"color:{self.t.err};border:none;")

    # ------------------------------------------------------------ 小零件

    def _line(self, text: str, placeholder: str = "") -> "QLineEdit": # noqa: F821
        from PySide6.QtWidgets import QLineEdit # noqa: PLC0415

        e = QLineEdit(text)
        e.setPlaceholderText(placeholder)
        e.setFont(qfont(self.t, self.t.body_size))
        e.setFixedHeight(32)
        e.setMinimumWidth(220)
        e.setStyleSheet(
            f"QLineEdit{{background:{rgba(self.t.q('tx'), 0 if self.t.glass else 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{self.t.tx};border:1px solid {self.t.bd};"
            f"border-radius:{self.t.radius_btn}px;padding:0 10px;}}"
            f"QLineEdit:focus{{border:1px solid {self.t.blue};}}"
        )
        return e

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{self.t.bd};border:none;")
        return d

    # ------------------------------------------------------------ 主题切换

    # ------------------------------------------------------------ 切换动效

    def _animate_page_in(self, page: QWidget) -> None:
        """页面切换轻淡入：150ms OutCubic（禁弹跳一族），可打断 ——
        重入时先停旧动画、复位旧 effect，绝不叠加两层透明度。"""
        from PySide6.QtCore import ( # noqa: PLC0415
            QAbstractAnimation,
            QEasingCurve,
            QPropertyAnimation,
        )
        from PySide6.QtWidgets import QGraphicsOpacityEffect # noqa: PLC0415

        old = getattr(self, "_page_anim", None)
        if old is not None:
            old.stop()
            oe = getattr(self, "_page_effect", None)
            if oe is not None:
                oe.setOpacity(1.0)
        eff = QGraphicsOpacityEffect(page)
        eff.setOpacity(0.0)
        page.setGraphicsEffect(eff)
        an = QPropertyAnimation(eff, b"opacity", self)
        an.setDuration(150)
        an.setEasingCurve(QEasingCurve.Type.OutCubic)
        an.setStartValue(0.0)
        an.setEndValue(1.0)
        an.finished.connect(lambda: page.setGraphicsEffect(None))
        an.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        self._page_anim, self._page_effect = an, eff

    def _crossfade_snapshot(self) -> None:
        """旧帧抓取—— 必须在改样式/重建**之前**调。"""
        self._fade_pm = self.grab()

    def _crossfade_play(self) -> None:
        """交叉淡入：新 UI 就位后，旧帧盖顶 180ms 淡出。

        web 侧交叉淡入的 Qt 等价物 —— 换主题是整套样式重建（_rebuild），
        做不了逐帧插值，就用旧相盖顶淡出。动效不阻塞输入（纯视觉层，
        事件穿透），再切一次时新 veil 直接盖上（可打断）。
        """
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation # noqa: PLC0415
        from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel # noqa: PLC0415

        pm = getattr(self, "_fade_pm", None)
        if pm is None:
            return
        self._fade_pm = None
        veil = QLabel(self)
        veil.setPixmap(pm)
        veil.setGeometry(self.rect())
        veil.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        veil.show()
        veil.raise_() # _rebuild 后新控件 z 序更高 —— veil 必须再抬到最上
        eff = QGraphicsOpacityEffect(veil)
        veil.setGraphicsEffect(eff)
        an = QPropertyAnimation(eff, b"opacity", self)
        an.setDuration(180)
        an.setEasingCurve(QEasingCurve.Type.OutCubic)
        an.setStartValue(1.0)
        an.setEndValue(0.0)
        an.finished.connect(self._fade_veil_done)
        an.start()
        self._fade_veil = veil # 保活到动画完；重入时旧 veil 被新的盖住、各自收敛

    def _fade_veil_done(self) -> None:
        """淡出完成：清引用（防悬挂）+ 删覆盖层。"""
        v = getattr(self, "_fade_veil", None)
        self._fade_veil = None
        if v is not None:
            v.deleteLater()

    def _switch_theme(self, key: str, persist: bool = True) -> None:
        """现场换主题 —— 不用重启。

        persist=True（顶栏用户切换）：主题也写回 config.ui.theme —— 不写的话
        4 秒后主题跟随会把现场切回 config 旧值（light/dark 截图
        跟随路径）：值本来就来自 config，不回写。
        ⚠️ 这正是 Qt 的**代价**所在：换主题要重建整套样式表（下面 `_restyle`），
           而 web 侧只是换一个 `data-theme` 属性、浏览器自己重画。
           原型里这次重建**会丢掉搜索框里的字与折叠状态** —— 真实产品必须把这些
           存下来再回填，否则每次换主题都像"重开了一次"。
           ⇒ 这条就是"Qt 的迭代成本"的具体形状，别只在文档里说。
        """
        self.t = THEMES[key]
        self._crossfade_snapshot() # #9：旧帧先抓（新样式还没生效）
        if persist:
            try:
                config_io.write_patch({"ui.theme": key})
                self._theme_seen = key # 自己写的自己认，别让跟随逻辑再切一遍
            except Exception: # noqa: BLE001
                pass
        # 记住状态再重建
        kept_find = self.find.text()
        closed = {g.title for g in self._groups() if g.collapsed}
        cur = next((s for it, _g, s, _l in self.items if getattr(it, "active", False)), "bot")
        self._rebuild()
        self.find.setText(kept_find)
        for (_it, g, _s, _l) in self.items:
            if g.title in closed and not g.collapsed:
                g.toggle()
        # 惰性构建：重建后页栈只有 bot —— 当前页现场建回，别把人弹回主页
        self._ensure_page(cur)
        idx = self._page_of.get(cur)
        if idx is not None and idx != 0:
            self.stack.setCurrentIndex(idx)
        for it, _g, s, _l in self.items:
            it.set_active(s == cur) # 重建后 items 全新，active 高亮要手动还回去
        apply_font_to_app(QApplication.instance(), self.t)
        w = getattr(self, "whale", None)
        if w is not None:
            w.restyle(self.t) # 挂件是独立顶层窗，不走 QSS 重建，自己换皮
        self._crossfade_play() # 新 UI 就位后旧帧淡出

    def _groups(self) -> list[NavGroup]:
        seen: list[NavGroup] = []
        for _it, g, _s, _l in self.items:
            if g not in seen:
                seen.append(g)
        return seen

    def _on_grp_toggled(self, _collapsed: bool) -> None:
        """分组折叠 → 写 QSettings（对齐 web saveClosed，console_html.py:7049/7066）。"""
        from PySide6.QtCore import QSettings # noqa: PLC0415

        closed = [g.title for g in self._groups() if g.collapsed]
        QSettings("WXAgent", "persona-morph-ui").setValue("nav_grp_closed", closed)
        # 折叠改了高度 ⇒ 让外层滚动区重算（web :7068 dispatch resize 同义）
        sc = getattr(self, "scroll", None)
        if sc is not None and sc.viewport() is not None:
            sc.viewport().update()

    def _close_transient_popups(self) -> None:
        """收回全部临时浮层。

        浮层两类：
          · 壳自己的 `self._look_pop`（外观选择）；
          · 顶栏更新胶囊 UpdPill 内嵌的 `self.pop`（遍历对象名 UpdPill 找）。
        只 close 不 delete —— 由随后的 `_rebuild` 统一随父销毁，这里仅保证
        「销毁前屏幕上没有悬浮窗」。
        """
        cands: list = []
        for attr in ("_look_pop",):
            w = getattr(self, attr, None)
            if w is not None:
                cands.append(w)
        try:
            for pill in self.findChildren(QFrame, "UpdPill"):
                p = getattr(pill, "pop", None)
                if p is not None:
                    cands.append(p)
        except Exception: # noqa: BLE001
            pass
        for w in cands:
            try:
                cp = getattr(w, "close_pop", None)
                if callable(cp):
                    cp() # UpdPill.pop 的自收起（带 try 守卫）
                elif w.isVisible():
                    w.close()
            except Exception: # noqa: BLE001
                pass

    def _rebuild(self) -> None:
        """整窗重建（原型做法，够用且诚实）。

        ⚠️ 旧版只删顶层 widget —— body 这个 QHBoxLayout 里的 side / 页栈
           会变成"孤儿"残留在窗口下层层堆积；接了 9 个面板后每切一次主题
           就多积几百个控件。这里补上第二层：布局里的子布局也逐 widget
           deleteLater（Qt 父删子递归，页栈整棵随之销毁）。
        """
        # ⛔ P1（切界面闪小窗）：Qt.Popup 浮层（外观选择 / 更新胶囊）
        #   是**独立顶层窗**，deleteLater 销毁父控件要等到下一轮事件循环才生效，
        #   而顶层窗在这中间仍画在屏幕上 ⇒ 切主题时肉眼看到「闪一下小窗」。
        #   ⇒ 拆旧控件之前先把所有可见浮层收回（父还在，close 稳）。
        self._close_transient_popups()
        lay = self.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                sub = it.layout()
                while sub.count():
                    sit = sub.takeAt(0)
                    if sit.widget():
                        sit.widget().deleteLater()
        # 注：顶层布局对象本身卸不掉（QWidget 没有公开的卸载 API，QObject setParent
        # 动不了 d->layout 指针）—— 所以 _build 改为「有布局就复用」，不再新建。
        self._build()
        # ⛔ P0-1（真机复验：鲸鱼→浅色后导航/顶栏仍深灰）：**顺序错了**——
        #   `_restyle` 按 `self._backdrop_on` 选样式表（True 套写死的深蓝玻璃
        #   `rgba(8,24,46,178)`/`rgba(12,34,62,219)`），而 `_backdrop_on` 只在
        #   `_refresh_backdrop()` 里更新 ⇒ 原来「先 _restyle 再 _refresh_backdrop」时，
        #   whale→light 这一趟 `_restyle` 读到的是 whale 时代的 True ⇒ 套完深色、标志位
        #   才变 False，却没人再重刷样式 ⇒ 顶栏/侧栏永远深灰（dark→light 看不出，因为
        #   dark 本来就深）。⇒ 把 `_refresh_backdrop()` 提到 `_restyle()` 之前。
        self._refresh_backdrop()
        self._restyle()
        # 主题换轴 ⇒ 光标底图也可能变：全控件重刷鱼光标
        self._cursor.reapply()
        # 重建后控件是新的：鲸语缓存清掉；若正处鲸语态，对新城控件重放
        self._orig_texts = {}
        if getattr(self, "text_style", "normal") == "whale":
            self._apply_text_style("whale")

    # ------------------------------------------------------------ 文案风格轴（ui.text_style）

    def _apply_text_style(self, key: str) -> None:
        """正常 ⇄ 鲸语。

        单一来源：读 `agent/whale_text.py` 的真字典（NAV 短表 + DICT 全量表），
        **不自己另编一份** —— 两份必然漂移（whale_text.py 头部原话）。
        只换字典里真实存在的键，对不上就保持原文 —— 与 web 侧行为一致（键对不上 = 不换）。
        """
        from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton # noqa: PLC0415

        self.text_style = key
        if not hasattr(self, "_orig_texts") or not self._orig_texts:
            self._orig_texts = {}
            # 切换器本体不参与翻译（"正常/鲸语/主题名"不是内容文案）
            skip = {id(self.style_seg), id(self.theme_seg)}
            # ⚠️ PySide6 的 findChildren 不收类型元组（PyQt 才行）——传了直接
            # TypeError，鲸语切换一点就崩。
            #    逐类型扫再合并，语义不变。
            kids: list = []
            for typ in (QLabel, QPushButton, QCheckBox):
                kids.extend(self.findChildren(typ))
            for w in kids:
                p = w.parent()
                if p is not None and id(p) in skip:
                    continue
                if id(w) in skip:
                    continue
                self._orig_texts[id(w)] = (w, "text", w.text())
            for w in self.findChildren(QLineEdit):
                self._orig_texts[id(w)] = (w, "placeholder", w.placeholderText())
        tr = _whale_tr()
        for _wid, (w, kind, orig) in list(self._orig_texts.items()):
            new = tr(orig) if key == "whale" else orig
            try:
                if kind == "placeholder":
                    w.setPlaceholderText(new)
                else:
                    w.setText(new)
            except RuntimeError:
                # 重建后旧控件已被 deleteLater —— 剔除，下次重新收集
                del self._orig_texts[_wid]

    # ------------------------------------------------------------ 样式

    def _restyle(self) -> None:
        t = self.t
        bg = t.bg
        # ⚠️ 截图（whale-1）发现的三个瑕疵，都在这里一起收：
        #   ① 侧栏那条**竖着的空轨道**：QScrollArea 用了系统滚动条皮肤，跟三套主题都不搭
        #      ⇒ 自绘细滚动条（宽度 6px、无箭头、圆角），并用主题色半透明
        #   ② 分组头前面的 "▾" 会被裁成半格 ⇒ 给 hd 留足左边距
        #   ③ 卡片描边在 whale（玻璃）下几乎看不见 ⇒ 玻璃态的描边单独调亮一档
        sb = t.scrollbar_alpha
        knob = rgba(t.q("tx"), sb).name(QColor.NameFormat.HexArgb)
        knob_h = rgba(t.q("tx"), min(255, sb + 40)).name(QColor.NameFormat.HexArgb)
        if getattr(self, "_backdrop_on", False):
            # ── 画卷模式（whale + 底图可用）：全局透明让 Shell.paintEvent 透出来，
            #    顶栏/侧栏用 web 同款深蓝玻璃压住底图（--topbar rgba(8,24,46,.7) /
            #    --bg-solid rgba(12,34,62,.86)，console_html.py L39/L34）。
            #    各控件自带显式背景，全局透明只影响容器层 —— 卡片仍是半透玻璃。
            self.setStyleSheet(
                f"QWidget{{background:transparent;}}"
                f"#TitleBar{{background:rgba(8,24,46,178);border-bottom:1px solid {t.bd};}}"
                f"#Side{{background:rgba(12,34,62,219);border-right:1px solid {t.bd};}}"
                f"#Main{{background:transparent;}}"
                f"QLabel{{background:transparent;color:{t.tx};}}"
                f"QScrollArea{{background:transparent;border:none;}}"
                f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
                f"QScrollBar::handle:vertical{{background:{knob};border-radius:3px;min-height:28px;}}"
                f"QScrollBar::handle:vertical:hover{{background:{knob_h};}}"
                f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
                f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}"
                f"QScrollBar:horizontal{{height:0px;}}"
            )
            return
        self.setStyleSheet(
            f"QWidget{{background:{bg};}}"
            f"#TitleBar{{background:{t.card if not t.glass else rgba(t.q('tx'), 12).name(QColor.NameFormat.HexArgb)};"
            f"border-bottom:1px solid {t.bd};}}"
            f"#Side{{background:{t.card if not t.glass else rgba(t.q('bg'), 180).name(QColor.NameFormat.HexArgb)};"
            f"border-right:1px solid {t.bd};}}"
            f"#Main{{background:{bg};}}"
            f"QLabel{{background:transparent;color:{t.tx};}}"
            f"QScrollArea{{background:transparent;border:none;}}"
            # 细滚动条：三套主题同一形状，只有颜色/透明度跟着主题走
            f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{knob};border-radius:3px;min-height:28px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{knob_h};}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}"
            f"QScrollBar:horizontal{{height:0px;}}"
        )

    def _toggle_tight(self) -> None:
        cur = self.btn_tight.text().startswith("‹")
        self._set_tight(cur)
        # 跨会话记忆（web navTight localStorage 的 QSettings 等价）
        try:
            from PySide6.QtCore import QSettings # noqa: PLC0415

            QSettings("WXAgent", "persona-morph-ui").setValue("nav_tight", bool(cur))
        except Exception: # noqa: BLE001
            pass

    def _set_tight(self, on: bool) -> None:
        """按指定状态应用侧栏收起（on=True 收起）——_toggle_tight 与构建时恢复共用。

        #8 → E：收起=**纯图标侧栏**——按钮只显示 » 图标（点了=展开）；
        分组头整行隐藏（不留首字）；导航项清文字只留图标、图标放大一档 16→20、
        行距加宽。展开全部还原。"""
        import icons as _icons # noqa: PLC0415

        side = self.find.parentWidget().parentWidget()
        side.setFixedWidth(76 if on else 232)
        for _it, g, _sec, _label in self.items:
            g.set_tight(on)
            _it.set_tight(on)
        if on: # 收起态
            self.btn_tight.setText("")
            self.btn_tight.setIcon(QIcon(_icons.chevs_pixmap(self.t.tx2, collapsed=True)))
            self.btn_tight.setToolTip("展开侧栏")
        else: # 展开态
            self.btn_tight.setIcon(QIcon())
            self.btn_tight.setText("‹ 收起")
            self.btn_tight.setToolTip("")

    # ------------------------------------------------------------ 交互

    def _install_shortcuts(self) -> None:
        """全局键盘快捷键（对齐 web 的两处文档级 keydown）。

        web 侧 keydown 共 6 处注册，逐处定性后**只有这两条是通用快捷键**：
          · `/`  → 聚焦导航搜索框（`console_html.py:7151-7157`，**输入态不触发**，
                   免得在输入框里打斜杠被抢）；
          · Esc → 清空搜索并失焦（`:7123`）—— 面板弹窗的 Esc 由各自弹窗自理，
                   这里只管「搜索框有内容时」这一路。
        其余 4 处：customGroup/memSearch 的 Enter 是**行内输入框**自己的键
        （Qt 侧已由控件自身 `returnPressed` 承接）；`Escape 退光标接管`（:3036）
        与 `ESC 全屏还原 postMessage`（:3048）是 web 专有形态（Qt 无「接管模式」/
        无 WebView2 宿主），**不适用**。
        """
        from PySide6.QtGui import QKeySequence, QShortcut # noqa: PLC0415

        sc_find = QShortcut(QKeySequence("/"), self)
        sc_find.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_find.activated.connect(self._focus_find)
        self._sc_find = sc_find

        sc_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc_esc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_esc.activated.connect(self._find_escape)
        self._sc_esc = sc_esc

    def _focus_find(self) -> None:
        """`/` 聚焦搜索框 —— 焦点已在可输入控件上时不抢（web 同口径：打斜杠不触发）。"""
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit # noqa: PLC0415

        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit)) or (
                fw is not None and fw.inherits("QAbstractSpinBox")):
            return
        self.find.setFocus()
        self.find.selectAll()

    def _find_escape(self) -> None:
        """Esc：搜索框有内容 → 清空并失焦（web :7123）；无内容 → 不动（让弹窗自己接）。"""
        if self.find.text():
            self.find.setText("")
            self._on_find("")
            self.find.clearFocus()

    def _find_enter(self) -> None:
        """Enter：跳到当前首个可见导航项（web :7124-7127 点第一个候选）。"""
        for it, _g, sec, label in self.items:
            if it.isVisible() and self.find.text().strip():
                self._go(sec, label)
                return

    def _on_find(self, q: str) -> None:
        """导航搜索 —— 对齐 web 侧三路匹配（名字 / 分组 / 说明）。"""
        q = q.strip().lower()
        for it, g, _sec, _label in self.items:
            if not q:
                it.setVisible(True)
                continue
            hay = (it.text() + " " + g.title + " " + (it.hint or "")).lower()
            it.setVisible(q in hay)
        for g in self._groups():
            if q and g.collapsed:
                g.toggle() # 搜索时自动展开，否则"搜到了但看不见"

    def _go(self, sec: str, label: str) -> None:
        for it, _g, s, _l in self.items:
            it.set_active(s == sec)
        self.st_panel.set("info", label)
        # 惰性构建：首次导航到才建页，之后 setCurrentIndex 直达
        self._ensure_page(sec)
        idx = getattr(self, "_page_of", {}).get(sec)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
            self._animate_page_in(self.stack.currentWidget()) # #9 轻淡入

    def _probe(self) -> None:
        """探一次后台，把结果翻译成界面上该说的话。

        ⛔ 卡顿根治：原写法在 **UI 线程**同步跑 `probe_backend`
        （timeout=1.2s，_probe_timer 每 4 秒一次）—— 后端慢/刚重启/抖动时，
        用户切面板的动作正好撞上探活窗口 ⇒ 「每切一项卡 1 秒」。
        改与 _poll_paused/_poll_badges 同款 box+thread 模式：后台线程拉，
        主线程落地；busy 守卫防止慢响应时堆线程。
        """
        from agent_bridge import current_url # noqa: PLC0415

        if getattr(self, "_probe_busy", False):
            return
        self._probe_busy = True
        url = current_url()
        box: dict = {"done": False, "val": None}

        def _work() -> None:
            try:
                box["val"] = probe_backend(url)
            except Exception: # noqa: BLE001
                box["val"] = None
            finally:
                self._probe_busy = False
                box["done"] = True

        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="probe").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(120, _apply)
                return
            if box["val"] is not None:
                self._show_probe(box["val"])
            self._watch_config() # 顺跳：光标/壁纸跟随 config（web 面板改了 4 秒内生效）

        QTimer.singleShot(120, _apply)

    def _upd_first_check(self) -> None:
        """ #13：更新公告条首拉。web updbar = 页面加载时拉一次（不做周期轮询）；
        /api/update 背后是 9 源竞速，可能拖到秒级 —— 后台线程拉，主线程落地。"""
        box: dict = {"done": False, "val": None}

        def _work() -> None:
            from config_io import get_json # noqa: PLC0415

            box["val"] = get_json("/api/update", timeout=10.0)
            box["done"] = True

        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="upd-first").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _apply)
                return
            self._upd_state = box["val"] if isinstance(box["val"], dict) else None
            bar = getattr(self, "updbar", None)
            if bar is not None:
                bar.apply_state(self._upd_state)

        QTimer.singleShot(150, _apply)

    # ------------------------------------------------------------ 暂停/恢复

    def _poll_paused(self) -> None:
        """拉一次 /api/status，把顶层 paused 字段落到按钮（web loadStatus 同款）。"""
        box: dict = {"done": False, "val": None}

        def _work() -> None:
            from config_io import get_json # noqa: PLC0415

            box["val"] = get_json("/api/status", timeout=5.0)
            box["done"] = True

        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="status-poll").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _apply)
                return
            s = box["val"]
            if isinstance(s, dict):
                self._apply_paused(bool(s.get("paused")))

        QTimer.singleShot(150, _apply)

    def _apply_paused(self, paused: bool) -> None:
        """paused 真值落地。唯一依据 = /api/status 的 paused 字段（web L4180
        血泪注释：不许读按钮文字做依据）。点击进行中不抢（web busy 守卫同款）。"""
        if getattr(self, "_pause_busy", False):
            return
        self._paused = paused
        btn = getattr(self, "btn_pause", None)
        if btn is not None:
            # 按钮上写「下一步能做什么」（web L5723 同款语义）
            btn.setText("恢复" if paused else "暂停")
            btn.setToolTip("机器人现在不会回复任何消息；点这个恢复收发"
                           if paused else "暂停后机器人不回复任何消息（进程还活着，不是停止）")

    # ------------------------------------------------------------ 面板徽章

    def _poll_badges(self) -> None:
        """一个后台线程拉 /api/status（+ /api/personas 人设计数），落地到
        _apply_badges 分发。拿不到就不动徽章（与 web refreshBadges 只在
        loadStatus 成功后调用同款语义），不编数。"""
        box: dict = {"done": False, "st": None, "pn": None}

        def _work() -> None:
            from config_io import get_json # noqa: PLC0415

            box["st"] = get_json("/api/status", timeout=5.0)
            try:
                r = get_json("/api/personas", timeout=5.0)
                ps = r.get("personas") if isinstance(r, dict) else None
                if isinstance(ps, list):
                    box["pn"] = len(ps)
            except Exception: # noqa: BLE001
                pass
            box["done"] = True

        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="badge-poll").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _apply)
                return
            if isinstance(box["st"], dict):
                self._apply_badges(box["st"], box["pn"])

        QTimer.singleShot(150, _apply)

    def _apply_side_status(self, s: dict) -> None:
        """侧栏微信连接状态。

        连接态 / 失败短原因 / 监听目标 0 警告 / 启动时间；tooltip = 全文原因 +
        重试次数 + 逐步诊断（web 把逐步诊断同时铺进微信面板，Qt 后续对齐）。
        """
        lab = getattr(self, "side_status", None)
        if lab is None or not isinstance(s, dict):
            return
        wa = s.get("wechat_attach") or {}
        if s.get("wechat_connected"):
            txt = "微信已连接"
        else:
            short = wa.get("short")
            txt = "微信未连接" + ((" · 原因：" + str(short)) if short else "")
        if wa.get("targets_zero"):
            txt += " · 监听目标 0 个（去「微信」面板勾群）"
        started = str(s.get("started_at") or "")
        if started:
            txt += " · 启动于 " + started
        lab.setText(txt)
        # web 真值不给状态行上色——`.side .status p{font-size:12px;color:var(--tx2)}`
        # 恒为灰字（console_html.py L519）。此前按连接态换 ok/warn ⇒ 绿/黄字，。
        lab.setStyleSheet(f"color:{self.t.tx2};background:transparent;")
        tip = ("已接上微信客户端" if s.get("wechat_connected")
               else str(wa.get("reason") or "还没拿到失败原因（等一次接入尝试，或看日志）")
               + "\n自动重试：已试 %s 次（每 10 秒一次，接上就自动开始工作）" % (wa.get("tries") or 0))
        steps = wa.get("steps") or []
        if steps:
            tip += "\n\n逐步诊断：\n" + "\n".join(
                ("[通过] " if x.get("ok") else "[卡住] ") + str(x.get("name")) + "：" + str(x.get("detail"))
                for x in steps if isinstance(x, dict))
        lab.setToolTip(tip)

    def _apply_badges(self, s: dict, personas_n: int | None = None) -> None:
        """把一份 /api/status 真值分发到各面板徽章（web refreshBadges 的 Qt 版）。

        口径与出处集中在 panels_qt.badge_for（sec 键 → (level,text,tip)）；
        返回 None 的 sec（overview/log/sessions 自刷新、本地语义面板）此处不动。
        persona 单独处理：web stPersona 数的是 DOM 列表，Qt 等价 = /api/personas
        的列表长度（数不到就不覆盖，保持面板本地态）。
        """
        import panels_qt as pq # noqa: PLC0415

        pq.refresh_status_rows(s) # 面板内状态位跟随 8s 轮询刷新（「读不到」主治）
        self._apply_side_status(s) # 收尾：侧栏微信连接状态（web #sideStatus 同款）

        bot_badge = getattr(self, "st_panel", None)
        if bot_badge is not None:
            r = pq.badge_for("bot", s)
            if r is not None:
                bot_badge.set(*r)
        for sec, idx in getattr(self, "_page_of", {}).items():
            # stack 里是 _wrap_scroll(build_panel(...)) 双层：外层 QScrollArea
            # 内层才是 build_panel 的 wrap（徽章挂在它上面）
            wd = self.stack.widget(idx)
            inner = wd.widget() if isinstance(wd, QScrollArea) else wd
            badge = getattr(inner, "_c8_badge", None)
            if badge is None:
                continue
            r = pq.badge_for(sec, s)
            if r is not None:
                badge.set(*r)
                continue
            if sec == "persona" and isinstance(personas_n, int):
                if personas_n > 0:
                    badge.set("ok", f"{personas_n} 个人设",
                              f"人设库里有 {personas_n} 个人设，选中一个它就是机器人说话的身份。")
                else:
                    badge.set("warn", "还没有人设",
                              "还没有人设脚本：先在上面那个入口生成或导入一个。")

    def _on_pause_click(self) -> None:
        """暂停/恢复 → POST /api/pause 或 /api/resume。

        对齐 web pauseBtn.onclick（console_html.py L5702-5730）全语义：
          · 方向 = 用户这一下**想要**的结果状态（wantPaused = not paused；
            web 原话：判据必须钉住这个方向，第一版调反过）
          · 点下去立刻禁用 + 「暂停中…/恢复中…」防连点
          · 成功就地翻转（不等轮询）+ 徽章如实
          · 失败恢复原状并如实报错（toast 的 Qt 等价 = 按钮处 QToolTip）
          · 恢复路径补刀 /api/risk recover（paused.flag 与
            risk.paused 两把钥匙一起清，否则用户点「恢复」看着好了、
            实际还在停发；补刀失败不影响主结果）
        """
        if self._pause_busy:
            return
        want = not self._paused # 当前在跑 ⇒ 这一下是想暂停
        self._pause_busy = True
        btn = self.btn_pause
        btn.setEnabled(False)
        btn.setText("恢复中…" if want else "暂停中…")
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415

            box["r"] = post_json("/api/pause" if want else "/api/resume", {})
            box["done"] = True

        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="pause-toggle").start()

        def _done() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _done)
                return
            self._pause_busy = False
            btn.setEnabled(True)
            r = box["r"]
            ok = isinstance(r, dict) and r.get("ok") is not False
            if not ok:
                why = r.get("why") if isinstance(r, dict) else None
                self._apply_paused(self._paused) # 失败恢复原状（按钮文字/使能）
                QToolTip.showText(
                    btn.mapToGlobal(btn.rect().center()),
                    "没切成：%s" % (why or "没连上后台（状态没有改变）"), btn)
                return
            self._paused = want # 就地翻转，不等轮询（web 同款）
            btn.setText("恢复" if want else "暂停")
            # 状态徽章立即如实（探活循环随后按 /api/status 刷回同一结论）
            self.st_top.set("idle" if want else "ok", "已暂停" if want else "后台在跑")
            if not want:
                # 恢复补刀（web L5719-5721 同款；失败不遮主结果）
                def _recover() -> None:
                    from agent_bridge import post_json # noqa: PLC0415

                    try:
                        post_json("/api/risk", {"action": "recover"})
                    except Exception: # noqa: BLE001
                        pass

                _th.Thread(target=_recover, daemon=True, name="risk-recover").start()

        QTimer.singleShot(150, _done)

    def _show_probe(self, p: Probe) -> None:
        plan = plan_for(p)
        lvl = p.level
        txt = {
            "ok": "后台在跑",
            "info": "启动中",
            "err": "没连上",
            "warn": "状态不明",
        }.get(lvl, "读取中")
        # #16：暂停中徽章如实 —— 进程活着（探活 ok）但不回任何消息
        # （web L5724 同款：runText 显示「已暂停」）。下轮 /api/status 轮询
        # 会把按钮文案刷回同一结论。
        if lvl == "ok" and getattr(self, "_paused", False):
            txt = "已暂停"
        self.st_top.set(lvl, txt)
        self.heal_title.setText(plan.title)
        self.heal_body.setText(plan.body)
        self.heal_btn.setText(plan.action_label or "重连")
        self.heal_btn.setVisible(bool(plan.action_label) or p.health is not Health.OK)
        self.btn_sim_alive = p.health is Health.OK

    def _simulate_dead(self) -> None:
        """演示"服务死了但窗口还开着"—— 这正是用户报的那个场景。

        web 壳在这里只能给你一屏 Edge 错误页；
        原生壳能**明确说出**发生了什么，并给一个真能点的按钮。
        """
        self._show_probe(
            Probe(
                Health.DEAD,
                "演示：后台进程已经退了，但窗口还开着",
                fix_hint="点『现在就拉起来』",
                can_self_heal=True,
            )
        )

    # ------------------------------------------------------------ 机器人控制

    def _fire_api(self, api: str, done) -> None:
        """后台线程发 POST，不冻结界面（网络调用可能拖到 5 秒超时）。

        结果经 150ms 轮询交回主线程 —— 跨线程直接摸 Qt 控件是禁区。
        `done(ok, note)` 一定在主线程被恰好调用一次。
        """
        import threading # noqa: PLC0415

        from agent_bridge import post_api # noqa: PLC0415

        box: dict = {"ok": None, "note": ""}

        def _work() -> None:
            box["ok"], box["note"] = post_api(api)

        threading.Thread(target=_work, daemon=True, name="post-" + api).start()

        def _poll() -> None:
            if box["ok"] is None:
                QTimer.singleShot(150, _poll)
                return
            done(box["ok"], box["note"])

        QTimer.singleShot(150, _poll)

    def _bot_restart(self) -> None:
        """重启机器人 → POST /api/restart。

        后端语义（webui.py）：0.5 秒后杀旧看门狗→拉新看门狗→2 秒后本进程强退，
        由看门狗重新拉起全套。chip 先落「重启中」，探活循环几秒内会刷回真实状态。
        """
        d = ConfirmDialog(
            self.t,
            self,
            "重启机器人？",
            "后台进程会退出并重新拉起，几秒钟内消息不收发。",
            [
                "重启期间（通常几秒）新消息不收不发",
                "重启完成后状态徽章会自己变回『后台在跑』",
                "这个原生窗口不用关，会自动重连",
            ],
            confirm_label="重启",
            cancel_label="算了",
            dangerous=False,
        )
        d.exec()
        if not d.result_ok:
            return
        self.st_top.set("info", "重启中")

        def _done(ok: bool, note: str) -> None:
            if ok:
                return # 探活循环接手：新进程起来后徽章自动回「后台在跑」
            self.st_top.set("warn", "重启失败", note)

        self._fire_api("/api/restart", _done)

    def _bot_stop(self) -> None:
        """停止机器人 → POST /api/shutdown（ D：
        —— 去掉打字门槛，弹窗里直接「确定停止」一键确认；后果说明保留。
        打字门槛只留给「清空全部记忆」这类不可逆操作）。

        后端语义（webui.py）：响应一发出就写 stopped.flag + 杀看门狗 +
        `os._exit(0)` —— **整个进程死**，这个原生窗口随之关闭。所以后果
        必须写明白；响应多半读不完整，post_api 已按「送达」口径容忍。
        """
        d = ConfirmDialog(
            self.t,
            self,
            "停止机器人？",
            "整个后台进程会退出，这个原生窗口也会随之关闭。",
            [
                "机器人立刻停止收发消息",
                "后台看门狗一并退出，进程整个结束",
                "这个原生窗口会随之关闭",
                "想再用要重新一键启动",
            ],
            confirm_label="确定停止",
            cancel_label="算了",
            dangerous=True,
        )
        d.exec()
        if not d.result_ok:
            return
        self.st_top.set("warn", "正在停止")

        def _done(ok: bool, note: str) -> None:
            if ok:
                return # 进程马上会死，不需要再刷任何界面
            self.st_top.set("err", "停止失败", note)

        self._fire_api("/api/shutdown", _done)

    def _confirm_reset(self) -> None:
        d = ConfirmDialog(
            self.t,
            self,
            "清空全部记忆？",
            "它会忘掉所有群友的印象、聊过的话、以及自己攒下的经验。",
            [
                "所有群友的印象和标签会被删掉，重新开始认识人",
                "它攒下的说话经验、金句都会没有",
                "已经发出去的消息不受影响（那些在微信里）",
                # ⚠️ 不许写 `**不能撤销**` —— Qt 没有 Markdown，星号会原样显示。
                #    强调由 confirm.py 按关键词自动加粗+变红（见 `_clean()`）。
                "这个动作不能撤销",
            ],
            confirm_label="我真的要清空",
            cancel_label="算了",
            dangerous=True,
            typed_word="清空",
        )
        d.exec()

    def _confirm_key(self) -> None:
        d = ConfirmDialog(
            self.t,
            self,
            "重置访问口令？",
            "控制台的访问口令会重新生成。",
            [
                "现在开着的控制台窗口会立刻掉线，需要重新打开",
                "别的设备上保存的地址会失效",
                "如果这台机器是你自己在用，通常没必要换",
            ],
            confirm_label="重置",
            cancel_label="不用了",
            dangerous=True,
        )
        d.exec()


class _Combo(QWidget):
    """下拉框 —— 手写而非 QComboBox，为了完全控制外观（系统下拉在深色主题下很脏）。"""

    def __init__(self, items: list[str], t: Tokens, parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox # noqa: PLC0415

        self.t = t
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.cb = QComboBox()
        self.cb.addItems(items)
        self.cb.setFont(qfont(t, t.body_size))
        self.cb.setFixedHeight(32)
        self.cb.setMinimumWidth(200)
        self.cb.setStyleSheet(
            f"QComboBox{{background:{rgba(t.q('tx'), 0 if t.glass else 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
            f"QComboBox::drop-down{{border:none;width:22px;}}"
            f"QComboBox QAbstractItemView{{background:{t.bg if t.key!='whale' else '#0E2136'};"
            f"color:{t.tx};border:1px solid {t.bd};selection-background-color:{t.blue_soft};}}"
        )
        lay.addWidget(self.cb)


# ---------------------------------------------------------------- 入口


def main() -> int:
    set_per_monitor_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName("Persona Morph Qt Proto")

    key = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in THEMES else "whale"
    t = THEMES[key]

    # ⚠️ 顺序：app 先建好，才敢探字体（`available_ui_families()` 的硬崩坑）
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        print(f"[字体] 本机没有 {t.font_family}，降级用 {fam}")
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)

    w = Shell(t)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
