# -*- coding: utf-8 -*-
"""Qt 最小原生壳 —— 主窗口。

=====================================================================
这个原型要回答的问题（用户拍板'先做最小壳验证'）
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

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_io  # noqa: E402  丙-4：面板读写 config.json 的桥（save→set 同 webui 次序）

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from confirm import ConfirmDialog  # noqa: E402
from cursor_fx import WhaleCursor  # noqa: E402
from heal import Health, Probe, plan_for, probe_backend  # noqa: E402
from ocean import OceanWaves, paint_backdrop  # noqa: E402
from panels_qt import BATCH_SECS, build_panel  # noqa: E402
from stylekit_qt import THEMES, Tokens, apply_font_to_app, qfont, resolve_family, rgba  # noqa: E402
from widgets import Badge, Btn, Card, Field, NavGroup, NavItem, SearchBox, Switch, desc, h2  # noqa: E402


# ---------------------------------------------------------------- DPI 纪律


def set_per_monitor_dpi() -> str:
    """PerMonitorV2。

    照 `stylekit.cs` 的 `StyleKit.Prep()`：`SetThreadDpiAwarenessContext(-4)`。
    不做的后果（项目已实测过）：多显示器混合 DPI 下量到的是**虚拟坐标**，
    窗口尺寸会"莫名其妙变小"（1160×900 变 773×600 = ÷1.5）。
    Qt6 默认已经是 PerMonitorV2，这里显式再钉一遍，并把实际值回报出来供截图取证。
    """
    try:
        import ctypes  # noqa: PLC0415
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
# **以 web 源码为唯一真值**：不自造组名、不用比喻命名（用户 2026-09-23 口径：
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
    import importlib.util  # noqa: PLC0415

    p = Path(__file__).resolve().parents[1] / "agent" / "whale_text.py"
    try:
        spec = importlib.util.spec_from_file_location("whale_text_bridge", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def tr(s: str) -> str:
            return mod.NAV.get(s) or mod.DICT.get(s) or s

        return tr
    except Exception:  # noqa: BLE001
        return lambda s: s


# ---------------------------------------------------------------- 主窗口


class Shell(QWidget):
    def __init__(self, t: Tokens):
        super().__init__()
        self.t = t
        self.text_style = str(config_io.read_path("ui.text_style", "normal") or "normal")
        # ui.text_style 轴：normal / whale（丙-4 起从真配置初始化，不再硬编码 normal）
        # 主题跟随的「上次见过的值」基线 = config 当前值（不是本壳初始主题）：
        # 这样初始主题 ≠ config 值的多 Shell 场景（shoot 取证）不会被跟随逻辑拽回。
        self._theme_seen = str(config_io.read_path("ui.theme", "") or "")
        self._orig_texts: dict = {}  # 鲸语切换的原文缓存（控件重建后清空）
        # ── 鲸落视觉本体（ocean.py）：底图 + tint + 三层波浪，只在 whale 主题启用 ──
        self._wp_path: Path | None = None      # 当前底图来源（含自定义背景判路）
        self._wp_src: "QPixmap | None" = None  # 原图
        self._wp_scaled: "QPixmap | None" = None
        self._wp_scaled_for = None             # (w, h, dpr) 重缩放判据
        self._backdrop_on = False              # whale + 底图可用 才 True
        self.setWindowTitle("群相 控制台")   # 落位适配：正式壳不再是「原型」（app.py 启动器同名兜底）
        self.resize(1120, 720)
        self.setMinimumSize(860, 560)
        self._build()
        # 视觉本体与鱼光标：建在 _restyle 之前（_restyle 要按 backdrop 分支）
        self._ocean = OceanWaves(self)
        self._cursor = WhaleCursor(Path(__file__).resolve().parents[1], parent=self)
        self._load_wallpaper()
        self._refresh_backdrop()
        self._restyle()
        self._cursor.refresh_from_config()

        # 后台探活 —— 原型的关键演示点（顺带当配置轮询：光标/壁纸 4 秒内跟随 web 面板的改动）
        self._probe_timer = QTimer(self)
        self._probe_timer.timeout.connect(self._probe)
        self._probe_timer.start(4000)
        QTimer.singleShot(300, self._probe)

    # ------------------------------------------------------------ 鲸落视觉本体

    def _wallpaper_source(self) -> Path:
        """底图来源：config `ui.background == 'custom'` 用上传的 data/ui_bg.jpg，
        否则默认海 assets/wallpaper/ocean1.jpg（web applyCustomBg 同款判路）。
        自定义文件缺失 ⇒ 回默认海，不白屏。"""
        root = Path(__file__).resolve().parents[1]
        try:
            from agent.config import get_config  # noqa: PLC0415

            custom = (get_config().get("ui") or {}).get("background") == "custom"
        except Exception:  # noqa: BLE001
            custom = False
        if custom:
            try:
                from agent.config import DATA_DIR  # noqa: PLC0415

                p = Path(DATA_DIR) / "ui_bg.jpg"
                if p.exists():
                    return p
            except Exception:  # noqa: BLE001
                pass
        return root / "assets" / "wallpaper" / "ocean1.jpg"

    def _load_wallpaper(self) -> None:
        src = self._wallpaper_source()
        if src == self._wp_path and self._wp_src is not None:
            return
        self._wp_path = src
        pm = QPixmap(str(src)) if src.exists() else QPixmap()
        self._wp_src = None if pm.isNull() else pm
        self._wp_scaled_for = None   # 来源变了 ⇒ 强制重缩放

    def _rescale_wp(self) -> None:
        """cover 缩放缓存（30fps 每帧只做 1:1 贴图，缩放只在尺寸变化时做一次）。"""
        if self._wp_src is None:
            self._wp_scaled = None
            return
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if key == self._wp_scaled_for and self._wp_scaled is not None:
            return
        from ocean import cover_pixmap  # noqa: PLC0415

        self._wp_scaled = cover_pixmap(self._wp_src, self.width(), self.height(), self.devicePixelRatioF())
        self._wp_scaled_for = key

    def _refresh_backdrop(self) -> None:
        """whale 主题 + 底图可用 ⇒ 开画卷与波浪；其余主题回到原 flat 底。"""
        self._rescale_wp()
        self._backdrop_on = self.t.key == "whale" and self._wp_scaled is not None
        self._ocean.set_active(self._backdrop_on and self.isVisible())

    def _watch_config(self) -> None:
        """4 秒一跳的配置跟随（挂在探活定时器上）：web 面板改了光标/背景/主题，这里跟上。"""
        try:
            self._cursor.refresh_from_config()
        except Exception:  # noqa: BLE001
            pass
        # 主题跟随（丙-4）：**外部**（web 面板/别的进程）改了 ui.theme 才跟 ——
        # 判据是「与上次见过的值不同」，不是「与当前主题不同」：后者会把多 Shell
        # 异构场景（shoot 取证每镜头一主题）和本壳自己切的现场统统拽回 config
        # 旧值（2026-09-23 实测翻车）。Qt 无 system 跟随，映射表外的键不动。
        try:
            th = str(config_io.read_path("ui.theme", "") or "")
            if th and th != self._theme_seen:
                self._theme_seen = th
                if th in THEMES and th != self.t.key:
                    self._switch_theme(th, persist=False)
        except Exception:  # noqa: BLE001
            pass
        wp_before = self._wp_path
        self._load_wallpaper()
        if wp_before != self._wp_path or self._wp_scaled is None:
            self._refresh_backdrop()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        if self._backdrop_on and self._wp_scaled is not None:
            paint_backdrop(p, self.width(), self.height(), self._wp_scaled,
                           self._ocean, self.devicePixelRatioF())
        else:
            p.fillRect(self.rect(), self.t.q("bg"))

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._rescale_wp()
        self._ocean.invalidate()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._ocean.set_active(self._backdrop_on)

    def hideEvent(self, ev) -> None:  # noqa: N802
        super().hideEvent(ev)
        self._ocean.set_active(False)   # CPU 纪律：看不见就不转

    # ------------------------------------------------------------ 结构

    def _build(self) -> None:
        # 复用已有顶层布局（_rebuild 重建时 QWidget 的 d->layout 指针卸不掉，
        # 再 QVBoxLayout(self) 会撞 "already has a layout" 警告且新布局装不上，
        # 重建后整窗失去几何管理 —— 2026-09-23 主题跟随功能实测踩中）。
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

    def _build_titlebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(10)

        # 顶栏鲸鱼徽章 —— web 侧 `.whale-badge` 的 Qt 复刻（52×52 近黑底 + 真图，icons 不手画）
        from widgets import WhaleBadge  # noqa: PLC0415

        lay.addWidget(WhaleBadge())

        name = QLabel("群相 控制台")
        name.setFont(qfont(self.t, 14, 600, display=True))
        name.setStyleSheet(f"color:{self.t.tx};background:transparent;")
        lay.addWidget(name)

        ver = QLabel("原生界面 v1.0")   # 落位适配：版本标签摘掉「原型」字样
        ver.setFont(qfont(self.t, 11))
        ver.setStyleSheet(f"color:{self.t.tx2};background:transparent;")
        lay.addWidget(ver)

        # 更新公告条（web 侧顶部常驻那条）
        ann = QLabel("有新版可用 · 3 项改进")
        ann.setFont(qfont(self.t, 11.5, 500))
        ann.setStyleSheet(
            f"color:{self.t.blue};background:{self.t.blue_soft};"
            f"border-radius:9px;padding:3px 10px;"
        )
        lay.addWidget(ann)

        lay.addStretch(1)

        # 整体状态徽章（对齐 web 侧顶栏那个"在跑/没连上"）
        self.st_top = Badge(self.t, "idle", "读取中")
        lay.addWidget(self.st_top)

        # 文案风格切换（ui.text_style 轴）—— 用户观察：「语言风格切换在哪里？」
        # web 侧把它藏在「界面」面板的下拉里，可发现性差；原型把它提到顶栏，和主题轴并排。
        from widgets import Segmented  # noqa: PLC0415

        self.style_seg = Segmented(
            self.t, [("normal", "正常"), ("whale", "鲸语")], value=getattr(self, "text_style", "normal")
        )
        self.style_seg.changed.connect(self._apply_text_style)
        lay.addWidget(self.style_seg)

        # 主题切换（ui.theme 轴）—— 滑槽式，替代原来"三个并排方按钮"（用户：太方了）
        self.theme_seg = Segmented(
            self.t,
            [(k, tk.label.split(" · ")[0].split("（")[0]) for k, tk in THEMES.items()],
            value=self.t.key,
        )
        self.theme_seg.changed.connect(self._switch_theme)
        lay.addWidget(self.theme_seg)

        # 机器人控制（丙-5 #3）：重启/停止 —— web 侧顶栏同款动作，走原生确认弹窗。
        # 路由是 /api/restart、/api/shutdown（POST），经 agent_bridge.post_api 发出，
        # agent/ 一行不改。danger 红钮只给「停止」—— 它不可一键反悔。
        self.btn_restart = Btn("重启", self.t, "ghost")
        self.btn_restart.clicked.connect(self._bot_restart)
        lay.addWidget(self.btn_restart)

        self.btn_stop = Btn("停止", self.t, "danger")
        self.btn_stop.clicked.connect(self._bot_stop)
        lay.addWidget(self.btn_stop)

        return bar

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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.nav_lay = QVBoxLayout(inner)
        self.nav_lay.setContentsMargins(8, 4, 8, 4)
        self.nav_lay.setSpacing(0)

        self.items: list[tuple[NavItem, NavGroup, str, str]] = []
        for title, grp_key, entries in NAV:
            g = NavGroup(self.t, title)
            for label, hint, sec in entries:
                it = NavItem(self.t, label, hint, icon_key=sec)
                it.clicked.connect(lambda _=False, s=sec, l=label: self._go(s, l))
                g.add(it)
                self.items.append((it, g, sec, label))
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
        「内容 / 运行 / 外观」三组是下一棒的活：点导航仍只换高亮与徽章。
        """
        from PySide6.QtWidgets import QStackedWidget  # noqa: PLC0415

        self.stack = QStackedWidget()
        self._page_of: dict[str, int] = {"bot": 0}
        self.stack.addWidget(self._build_bot_panel())
        for sec in BATCH_SECS:
            if sec == "bot":
                continue
            self._page_of[sec] = self.stack.count()
            # 保存成功 → _watch_config：光标/壁纸/主题 4s 内不再等探活，当场跟上
            self.stack.addWidget(build_panel(self.t, sec, on_save=self._watch_config))
        return self.stack

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

        # ── 设置卡：四行全接真配置（web sec-bot 同款键位，丙-4）──
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
                self.style_seg._pick(style)  # noqa: SLF001  同包内复用（发 changed 即应用）
        else:
            self.bot_note.setText(f"没保存成：{msg}")
            self.bot_note.setStyleSheet(f"color:{self.t.err};border:none;")

    # ------------------------------------------------------------ 小零件

    def _line(self, text: str, placeholder: str = "") -> "QLineEdit":  # noqa: F821
        from PySide6.QtWidgets import QLineEdit  # noqa: PLC0415

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

    def _switch_theme(self, key: str, persist: bool = True) -> None:
        """现场换主题 —— 不用重启。

        persist=True（顶栏用户切换）：主题也写回 config.ui.theme —— 不写的话
        4 秒后主题跟随会把现场切回 config 旧值（2026-09-23 light/dark 截图
        全组翻车的现场教训），且重启不保持。persist=False（_watch_config
        跟随路径）：值本来就来自 config，不回写。
        ⚠️ 这正是 Qt 的**代价**所在：换主题要重建整套样式表（下面 `_restyle`），
           而 web 侧只是换一个 `data-theme` 属性、浏览器自己重画。
           原型里这次重建**会丢掉搜索框里的字与折叠状态** —— 真实产品必须把这些
           存下来再回填，否则每次换主题都像"重开了一次"。
           ⇒ 这条就是"Qt 的迭代成本"的具体形状，别只在文档里说。
        """
        self.t = THEMES[key]
        if persist:
            try:
                config_io.write_patch({"ui.theme": key})
                self._theme_seen = key   # 自己写的自己认，别让跟随逻辑再切一遍
            except Exception:  # noqa: BLE001
                pass
        # 记住状态再重建
        kept_find = self.find.text()
        closed = {g.title for g in self._groups() if g.collapsed}
        self._rebuild()
        self.find.setText(kept_find)
        for (_it, g, _s, _l) in self.items:
            if g.title in closed and not g.collapsed:
                g.toggle()
        apply_font_to_app(QApplication.instance(), self.t)

    def _groups(self) -> list[NavGroup]:
        seen: list[NavGroup] = []
        for _it, g, _s, _l in self.items:
            if g not in seen:
                seen.append(g)
        return seen

    def _rebuild(self) -> None:
        """整窗重建（原型做法，够用且诚实）。

        ⚠️ 旧版只删顶层 widget —— body 这个 QHBoxLayout 里的 side / 页栈
           会变成"孤儿"残留在窗口下层层堆积；接了 9 个面板后每切一次主题
           就多积几百个控件。这里补上第二层：布局里的子布局也逐 widget
           deleteLater（Qt 父删子递归，页栈整棵随之销毁）。
        """
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
        self._restyle()
        # 主题换轴 ⇒ 画卷开关/光标底图都可能变：重判 backdrop + 全控件重刷鱼光标
        self._refresh_backdrop()
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
        from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton  # noqa: PLC0415

        self.text_style = key
        if not hasattr(self, "_orig_texts") or not self._orig_texts:
            self._orig_texts = {}
            # 切换器本体不参与翻译（"正常/鲸语/主题名"不是内容文案）
            skip = {id(self.style_seg), id(self.theme_seg)}
            # ⚠️ PySide6 的 findChildren 不收类型元组（PyQt 才行）——传了直接
            #    TypeError，鲸语切换一点就崩（2026-09-23 鲸语截图脚本实锤）。
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
        # 对齐 web 侧 `.side.tight`（64px 图标栏）。原型先只改宽度 + 文案。
        cur = self.btn_tight.text().startswith("‹")
        side = self.find.parentWidget().parentWidget()
        side.setFixedWidth(232 if cur else 76)
        self.btn_tight.setText("› 展开" if cur else "‹ 收起")
        for it in self.find.parentWidget().findChildren(NavItem):
            it.setVisible(cur)

    # ------------------------------------------------------------ 交互

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
                g.toggle()  # 搜索时自动展开，否则"搜到了但看不见"

    def _go(self, sec: str, label: str) -> None:
        for it, _g, s, _l in self.items:
            it.set_active(s == sec)
        self.st_panel.set("info", label)
        # 真正换页（「日常」「智能」两组 9 页已建；未建组保持高亮+徽章反馈）
        idx = getattr(self, "_page_of", {}).get(sec)
        if idx is not None:
            self.stack.setCurrentIndex(idx)

    def _probe(self) -> None:
        """探一次后台，把结果翻译成界面上该说的话。"""
        from agent_bridge import current_url  # noqa: PLC0415

        p = probe_backend(current_url())
        self._show_probe(p)
        self._watch_config()   # 顺跳：光标/壁纸跟随 config（web 面板改了 4 秒内生效）

    def _show_probe(self, p: Probe) -> None:
        plan = plan_for(p)
        lvl = p.level
        self.st_top.set(lvl, {
            "ok": "后台在跑",
            "info": "启动中",
            "err": "没连上",
            "warn": "状态不明",
        }.get(lvl, "读取中"))
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

    # ------------------------------------------------------------ 机器人控制（丙-5 #3）

    def _fire_api(self, api: str, done) -> None:
        """后台线程发 POST，不冻结界面（网络调用可能拖到 5 秒超时）。

        结果经 150ms 轮询交回主线程 —— 跨线程直接摸 Qt 控件是禁区。
        `done(ok, note)` 一定在主线程被恰好调用一次。
        """
        import threading  # noqa: PLC0415

        from agent_bridge import post_api  # noqa: PLC0415

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
                return  # 探活循环接手：新进程起来后徽章自动回「后台在跑」
            self.st_top.set("warn", "重启失败", note)

        self._fire_api("/api/restart", _done)

    def _bot_stop(self) -> None:
        """停止机器人 → POST /api/shutdown（不可一键反悔 → typed_word 门槛）。

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
            confirm_label="停止",
            cancel_label="算了",
            dangerous=True,
            typed_word="停止",
        )
        d.exec()
        if not d.result_ok:
            return
        self.st_top.set("warn", "正在停止")

        def _done(ok: bool, note: str) -> None:
            if ok:
                return  # 进程马上会死，不需要再刷任何界面
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
        from PySide6.QtWidgets import QComboBox  # noqa: PLC0415

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
