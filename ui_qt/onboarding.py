# -*- coding: utf-8 -*-
"""首次引导向导 —— 多步上手（步数**动态**，环境缺项越多步越多）。

触发时机与 web 一致：配置载入后调用，**仅当「还没配好 Key」才弹**
（打码 `••••` / 占位符 / 空 → 弹；真实 Key → 不打扰）。步骤：

  1. 选厂商 → 选模型 → 填密钥（写 api.api_key / api.model / api.base_url /
     api.provider_keys.<prov>，并把 api.provider 清空走顶层）；
  2. 机器人昵称 = 你自己微信的原名（写 wechat.bot_nickname）；
  3. **点「恢复」开始工作**（POST /api/resume）—— 正常引导到此完结；
  4. **环境校准**（`/api/status` 的 `env_hint`，与 Qt 面板/web 同一份文案）：
     **只有两项 —— 微信界面设成浅色 / 微信字号设为标准字号** —— 只提醒、不强制
     （`agent/env_hint.py` 口径）。
     ⛔ **只问"微信那一层"的事**（2026-10-10 用户当面纠正）：**显示缩放**会把用户所有软件的
     图标显示全搞坏、**OCR 引擎**是程序自己该干的活 ⇒ 两样都**不许**出现在这里。
     这几样直接影响**识别准头与像素落点**，排在"缺什么"之前讲；
  5..N. **每个"缺什么"各占一步**（GET /api/env-guide）：虚拟声卡（发真语音条）、ffmpeg、
     语音识别、TTS、依赖补齐 —— 说清「用来干啥 / 现在什么情况 / 怎么补 / 这一类要不要你动手」。
     ⛔ **已就绪的项不单独占步**（否则白让用户点好几下）；
  N+1. 代码与依赖体检（POST /api/selfcheck mode=code，只读展示）。

⛔ **两类不许混**：① **环境校准**（微信浅色 / 微信标准字号 —— 影响识别准头与落点）
② **缺什么**（虚拟声卡/ffmpeg/... —— 影响某个功能能不能用）。
它们**各占自己的步**，**不塞进同一屏**（原先把两类全挤在一个滚动区，
弹窗只有 620×460 ⇒ 装不下、要滚半天，用户看到的就是"一屏塞满"）。

⛔ **步数不写死**：0 项缺 ⇒ 共 5 步；7 项全缺 ⇒ 共 11 步。文案里的"共 N 步"按实数算
（`_env_total_steps()`），**不出现"共 6 步"这种写死的数**。

厂商清单/模型列表/接口地址/Key 前缀一律**运行时解析 web 源码**（`_providers_from_web`），
不往 Qt 复制一份 —— web 改厂商，向导自动跟。

为什么独立成模块：向导是「多步 + 每步换内容」的流程件，塞进 panels_custom（已很长）
会把「手写面板」这一职责搅浑；这里只装向导，入口 `maybe_show(t, shell)`。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config_io
from stylekit_qt import Tokens, qfont, surface_bg
from widgets import Btn, DraggableDialog, FieldCombo, desc, h2, style_field

try:  # 每项"要装的是什么类型"的人话标签（与 agent/env_guide.py 同源，避免两处各写一份）
    from agent.env_guide import KIND_LABEL
except Exception:  # noqa: BLE001
    KIND_LABEL = {}

# 进程内只弹一次（web `_onboardOnce` 的 Qt 等价）
_ONBOARD_ONCE = False


def _key_is_unconfigured(key: str) -> bool:
    """Key 是否「还没配好」—— 对齐 web :5429-5432 的判定：
    空 / ★占位符 / 打码(`••••`) / `******` ⇒ 视为未配置（要弹向导）；
    其余（真实 Key）⇒ 已配置（不打扰）。"""
    k = str(key or "").strip()
    if not k:
        return True
    if k == "******":
        return True
    if "在这里填" in k:
        return True
    return "••••" in k


def maybe_show(t: Tokens, shell: QWidget) -> None:
    """入口（shell 启动后延迟调用）：未配置 Key 且本进程未弹过 → 弹五步向导。"""
    global _ONBOARD_ONCE # noqa: PLW0603
    if _ONBOARD_ONCE:
        return
    try:
        key = config_io.read_path("api.api_key", "")
    except Exception: # noqa: BLE001
        key = ""
    if not _key_is_unconfigured(key):
        return # 已配置 → 不打扰（web 同口径）
    _ONBOARD_ONCE = True
    dlg = _Wizard(t, shell)
    dlg.exec()


class _Wizard(DraggableDialog, QDialog):
    """多步向导（无边框 + 卡片，与 confirm.ConfirmDialog 同款壳）。可拖动。

    步号语义（`self.step`）：
      1/2/3 ＝ 配 Key / 昵称 / 选群；
      4     ＝ 环境总览；5..N ＝ 逐条讲缺项（由 `_env_i` 计数）；
      `_advance_env()` 走完缺项后跳「恢复」与「体检」（用 `_render_step_resume` /
      `_render_step_check`，**不是**固定的 5/6，因为总数是动态的）。
    """

    def __init__(self, t: Tokens, shell: QWidget):
        super().__init__(shell.window())
        self.t = t
        self.shell = shell
        self.step = 1
        self.picked: list[str] = []
        self._force_refresh = False
        self._env_items: list = []       # 环境清单（总览时拉一次）
        self._env_pending_cache: list = []  # 要单独给一步的缺项
        self._env_i = 0                  # 已讲到的第几个缺项

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setObjectName("onboard")
        self.resize(620, 460)
        self.enable_drag() # 可拖动
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget()
        card.setObjectName("C8OnboardCard")
        card.setStyleSheet(
            f"#C8OnboardCard{{background:{surface_bg(t)};border:1px solid {t.bd};"
            f"border-radius:{t.radius_card + 2}px;}}")
        outer.addWidget(card)
        self.v = QVBoxLayout(card)
        self.v.setContentsMargins(24, 22, 24, 18)
        self.v.setSpacing(10)

        self.v.addWidget(h2(t, "欢迎使用 Persona Morph · 上手向导"))
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setFont(qfont(t, t.body_size))
        self.desc.setStyleSheet(f"color:{t.tx2};background:transparent;")
        self.desc.setMinimumHeight(46)   # 各步文案长短不一，留两行高，避免按钮上下跳
        self.v.addWidget(self.desc)

        # 第 1 步的「厂商/模型/密钥」区（后续步骤隐藏，与 web 同款）
        self.mid = QWidget()
        self.mid_lay = QVBoxLayout(self.mid)
        self.mid_lay.setContentsMargins(0, 0, 0, 0)
        self.mid_lay.setSpacing(8)
        self._build_step1_fields()
        self.v.addWidget(self.mid)

        self.body = QWidget() # 各步的动态内容区（对应 web `#obBody`）
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        self.v.addWidget(self.body, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.b_next = Btn("下一步", t, "primary")
        self.b_later = Btn("跳过向导", t, "ghost")
        self.b_next.clicked.connect(self._on_next)
        self.b_later.clicked.connect(self._on_later)
        btns.addWidget(self.b_next)
        btns.addWidget(self.b_later)
        btns.addStretch(1)
        self.v.addLayout(btns)

        self._render_step1()

    # ── 第 1 步控件（厂商/模型/密钥，web `obProvider`/`obModel`/`obKey`）──
    def _build_step1_fields(self) -> None:
        t = self.t
        from panels_custom import _providers_from_web # noqa: PLC0415

        provs = _providers_from_web()
        self.providers = {k: v for k, v in provs.items() if k != "custom"}

        self.prov_w = FieldCombo(t)
        self.prov_w.setObjectName("obProvider")
        for k, v in self.providers.items():
            self.prov_w.addItem(str(v.get("label") or k), k)
        self.prov_w.activated.connect(lambda _i: self._render_models())

        self.model_w = FieldCombo(t)
        self.model_w.setObjectName("obModel")

        self.key_w = style_field(QLineEdit(), t)
        self.key_w.setObjectName("obKey")
        self.key_w.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_w.setPlaceholderText("粘贴该厂商的 Key（如 sk-...）")
        self.key_hint = QLabel("")
        self.key_hint.setWordWrap(True)
        self.key_hint.setFont(qfont(t, t.body_size - 1))
        self.key_hint.setStyleSheet(f"color:{t.tx3};background:transparent;")

        for lab, w in (("模型厂商", self.prov_w), ("模型", self.model_w), ("密钥", self.key_w)):
            row = QHBoxLayout()
            lb = QLabel(lab)
            lb.setFont(qfont(t, t.body_size))
            lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
            lb.setFixedWidth(74)
            row.addWidget(lb)
            row.addWidget(w, 1)
            self.mid_lay.addLayout(row)
        self.mid_lay.addWidget(self.key_hint)

        # 预填已有 Key（web :5458-5459）
        pre = str(config_io.read_path("api.api_key", "") or "")
        if pre and _key_is_unconfigured(pre) is False:
            self.key_w.setText(pre)
        self._render_models()

    def _render_models(self) -> None:
        """web `obRenderModels`：按厂商刷模型列表 + Key 前缀提示。"""
        prov = str(self.prov_w.currentData() or "")
        p = self.providers.get(prov) or {}
        self.model_w.clear()
        for mm in (p.get("models") or []):
            self.model_w.addItem(str(mm), str(mm))
        kh = str(p.get("keyHint") or "")
        label = str(p.get("label") or prov)
        self.key_hint.setText(
            f"Key 以「{kh}」开头；{label} 可在官网申请" if kh
            else f"在 {label} 官网申请 Key")

    # ── 步骤渲染 ──
    def _clear_body(self) -> None:
        while self.body_lay.count():
            it = self.body_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
                w.setParent(None) # 立刻脱离父子树（见上）
            lay = it.layout()
            if lay is not None:
                self._drop_layout(lay)

    def _drop_layout(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
                w.setParent(None) # 立刻脱离父子树（见上）
            sub = it.layout()
            if sub is not None:
                self._drop_layout(sub)

    def _render_step1(self) -> None:
        self.desc.setText("第 1 步：选择模型厂商 → 选择模型 → 填入该厂商的 密钥。")
        self.mid.setVisible(True)
        self._clear_body()
        self.b_next.setText("下一步")

    def _render_step2(self) -> None:
        t = self.t
        self.desc.setText("第 2 步：把「机器人昵称」改成你自己微信的原名"
                          "——就是你那个号在微信里显示的名字。群里 @ 到这个名字，"
                          "它才知道是在叫它。")
        self.mid.setVisible(False)
        self._clear_body()
        cur = str(config_io.read_path("wechat.bot_nickname", "") or "")
        row = QHBoxLayout()
        lb = QLabel("机器人昵称")
        lb.setFont(qfont(t, t.body_size))
        lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
        lb.setFixedWidth(84)
        self.nick_w = style_field(QLineEdit(), t)
        self.nick_w.setObjectName("obNick")
        self.nick_w.setPlaceholderText("填你自己微信的原名")
        self.nick_w.setText(cur)
        row.addWidget(lb)
        row.addWidget(self.nick_w, 1)
        self.body_lay.addLayout(row)
        self.body_lay.addWidget(desc(
            t, "填你自己微信的原名（机器人那个号在微信里叫什么，就填什么）——"
               "群友 @ 的就是这个名字。留空也行：那样它只认「角色身份名」。"))
        self.b_next.setText("保存并继续")

    def _render_step3_groups(self, groups: list) -> None:
        t = self.t
        self.desc.setText(f"第 3 步：勾选需要机器人监听的群"
                          f"（全不勾=监听所有群）。检测到 {len(groups)} 个群聊。")
        self.mid.setVisible(False)
        self._clear_body()
        self.picked = []
        saved = config_io.read_path("wechat.group_name_white_list", [])
        saved = [str(x) for x in saved] if isinstance(saved, list) else []
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        self._group_cbs: list[QCheckBox] = []
        for g in groups:
            nm = g.get("name") or g.get("nick") or g.get("wxid") or ""
            cb = QCheckBox(nm)
            cb.setFont(qfont(t, t.body_size))
            cb.setStyleSheet(f"color:{t.tx};background:transparent;")
            cb.setProperty("gname", nm)
            cb.setChecked(nm in saved)
            bl.addWidget(cb)
            self._group_cbs.append(cb)
        # 群多时给滚动区（web 侧 `.group-search` + 可滚列表）
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        sc.setWidget(box)
        sc.setMinimumHeight(150)
        self.body_lay.addWidget(sc)
        self.picked = [nm for nm in saved if any(
            cb.property("gname") == nm for cb in self._group_cbs)]
        self.b_next.setText("下一步")

    def _render_step3_error(self, err: str) -> None:
        t = self.t
        self.desc.setText(f"第 3 步：这台机器上暂时读不到群列表 —— {err}")
        self.mid.setVisible(False)
        self._clear_body()
        self.body_lay.addWidget(desc(
            t, "读不到群列表时这一步勾不了，但不影响先把程序跑起来："
               "不勾＝监听所有群（等你之后在「微信」面板里勾也行）。\n"
               "要现在勾：先确认微信已登录的是你要的那个号、那个号里确实有群，"
               "然后点下面的「重试读取」。"))
        row = QHBoxLayout()
        b_retry = Btn("重试读取（重新读微信）", t, "primary")
        b_skip = Btn("先跳过（不勾＝监听所有群）", t, "ghost")
        b_retry.clicked.connect(self._retry_groups)
        b_skip.clicked.connect(self._skip_groups)
        row.addWidget(b_retry)
        row.addWidget(b_skip)
        row.addStretch(1)
        self.body_lay.addLayout(row)
        self.b_next.setText("下一步")

    def _retry_groups(self) -> None:
        self._force_refresh = True
        self.step = 2
        self._on_next()

    def _skip_groups(self) -> None:
        self.step = 3
        self._on_next()

    # ── 环境区：第 4 步＝运行环境校准（env_hint：只有微信浅色 / 微信标准字号两项），
    #   然后**每个"功能缺件"各占一步**（虚拟声卡 / ffmpeg / TTS / 语音识别 / pilk）。
    #   ⛔ 两类不许混：① 运行环境（画面校准，影响**识别与落点**）② 功能缺件（影响**某个功能能不能用**）。
    #   原先我把两类塞进同一步的一个滚动区 ⇒ 弹窗 620×460 装不下、挤成"雷霆大画面"。

    def _env_fetch(self) -> dict:
        """拉 /api/env-guide（功能缺件清单）。失败 ⇒ 空清单 + 原因。"""
        try:
            r = config_io.get_json("/api/env-guide", timeout=20.0)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "items": [], "why": "环境清单读不到：%s" % e}
        return r if isinstance(r, dict) else {}

    def _hint_fetch(self) -> dict:
        """拉运行环境校准（`/api/status` 里的 `env_hint`，与 Qt 面板/ web 同一份文案）。

        `env_hint.check()` 的口径（`agent/env_hint.py`）：只提示、不强制、测不到就说测不到。
        """
        try:
            st = config_io.get_json("/api/status", timeout=20.0)
            h = (st or {}).get("env_hint") if isinstance(st, dict) else None
            return h if isinstance(h, dict) else {}
        except Exception as e:  # noqa: BLE001
            return {"level": "partial", "ok": False, "title": "运行环境",
                    "text": "环境没测到（%s）" % str(e)[:60], "items": [], "detail": {}}

    def _env_pending(self) -> list:
        """要单独给一步的项＝ `ok is not True`（缺的 **和** 读不出的）。
        ⛔ 已就绪的项不单独占步（否则 7 步里塞 4 个"不用装"，纯浪费用户点击）。"""
        return [it for it in (self._env_items or []) if it.get("ok") is not True]

    def _env_step_no(self) -> int:
        """缺件步号从 **6** 起。

        前 5 步是：1 配 Key / 2 昵称 / 3 选群 / 4 恢复 / 5 环境校准；
        缺件是第 6 步起（`_env_i` 是"已讲过的缺件数"）。
        """
        return 6 + int(self._env_i or 0)

    def _env_total_steps(self) -> int:
        """总步数：3（配置：Key/昵称/选群）+ 1（恢复）+ 1（环境校准）+ N（缺件）+ 1（体检）。"""
        return 3 + 1 + 1 + len(getattr(self, "_env_pending_cache", [])) + 1

    # ── 第 4 步：点「恢复」开始工作（正常引导到这儿就完结了）──
    def _render_step_resume(self) -> None:
        t = self.t
        total = self._env_total_steps()
        self.desc.setText("第 4 步/共 %d 步：点下面的「恢复」——程序默认是暂停的，"
                          "点了它才开始监听群消息。" % total)
        self.mid.setVisible(False)
        self._clear_body()
        row = QHBoxLayout()
        b_resume = Btn("恢复（开始工作）", t, "primary")
        self.resume_hint = QLabel("点一下就开始了；之后想停，顶部有「暂停」。")
        self.resume_hint.setWordWrap(True)
        self.resume_hint.setFont(qfont(t, t.body_size - 1))
        self.resume_hint.setStyleSheet(f"color:{t.tx3};background:transparent;")
        b_resume.clicked.connect(lambda: self._do_resume(b_resume))
        row.addWidget(b_resume)
        row.addWidget(self.resume_hint, 1)
        self.body_lay.addLayout(row)
        self.b_next.setText("下一步")

    def _do_resume(self, btn) -> None:
        try:
            config_io.post_json("/api/resume", {}, timeout=6.0)
            self.resume_hint.setText("已开始工作 ✓（要停就在顶部点「暂停」）")
            btn.setText("已恢复")
        except Exception as e: # noqa: BLE001
            self.resume_hint.setText(f"恢复失败：{e}（也可以稍后在控制台顶部点「恢复」）")

    # ── 第 5 步：环境校准（只有两项 —— 微信界面浅色 / 微信字号标准）──
    def _render_step_hint(self) -> None:
        """把「会让识别/落点失准」的两项摆出来（微信界面浅色 / 微信字号标准）。

        ⛔ 只提醒、不替用户改；文案直接用后端那份（`env_hint.text`），**前端不自己编**。
        拉一次 `/api/env-guide` 只为**算出总步数**（后面有多少个缺件），不在这里展示。
        """
        t = self.t
        h = self._hint_fetch()
        r = self._env_fetch()
        self._env_items = r.get("items") or []
        self._env_pending_cache = self._env_pending()
        self._env_i = 0
        total = self._env_total_steps()
        self.desc.setText(
            "第 5 步/共 %d 步：环境校准 —— 这几样会直接影响识别的准头，先对一下。"
            "**只是提醒：不改也能用**，只是容易认错、要多绕一层。" % total)
        self.mid.setVisible(False)
        self._clear_body()
        items = h.get("items") or []
        if not items:
            self.body_lay.addWidget(desc(t, str(h.get("text") or "环境没测到（后台没连上）")))
        else:
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(3)
            _mark = {"ok": ("✓", t.ok), "warn": ("!", t.err), "unknown": ("·", t.tx3)}
            for it in items:
                mk, col = _mark.get(str(it.get("level") or ""), ("·", t.tx3))
                lb = QLabel("%s %s" % (mk, str(it.get("text") or "").replace("**", "")))
                lb.setWordWrap(True)
                lb.setFont(qfont(t, t.body_size))
                lb.setStyleSheet("color:%s;background:transparent;" % col)
                bl.addWidget(lb)
                why = str(it.get("why") or "").replace("**", "")
                if why:
                    wl = QLabel("　　" + why)
                    wl.setWordWrap(True)
                    wl.setFont(qfont(t, t.body_size - 1))
                    wl.setStyleSheet("color:%s;background:transparent;" % t.tx2)
                    bl.addWidget(wl)
            self.body_lay.addWidget(box)
            self.body_lay.addWidget(desc(t, str(h.get("text") or "").replace("**", "")))
        if self._env_pending_cache:
            self.body_lay.addWidget(desc(
                t, "接下来 %d 步逐条讲「还缺的是什么、用来干啥、怎么补」。" % len(self._env_pending_cache)))
        row = QHBoxLayout()
        # ⭐ 「打开引导」：把这几条摆进**一个弹窗**（用户口径："环境引导，弹窗引导"）。
        #   向导页里仍留一份摘要（用户往下走时不至于两眼一抹黑），
        #   但弹窗才是"照着调"的主场 —— 它可拖动、不被向导页的行数挤扁。
        b_open = Btn("打开引导", t, "primary")
        b_open.clicked.connect(self._open_env_guide)
        row.addWidget(b_open)
        b_re = Btn("重新检测", t, "ghost")
        b_re.clicked.connect(self._render_step_hint)
        row.addWidget(b_re)
        row.addStretch(1)
        self.body_lay.addLayout(row)
        self.b_next.setText("下一步")

    def _open_env_guide(self) -> None:
        """打开环境引导弹窗（一个弹窗，引导用户把微信调成浅色 + 标准字号）。

        ⚠️ 弹窗**只提醒、不替用户改微信** —— 所以重检按钮只是重拉一次清单。
        """
        try:
            from envguide import EnvGuideDialog # noqa: PLC0415
            d = EnvGuideDialog(self.t, self, self._hint_fetch())
            if d.exec():
                self._render_step_hint() # 用户说"我调好了" ⇒ 重拉清单
        except Exception as e: # noqa: BLE001
            self.desc.setText("引导弹窗打不开：%s" % e)

    # ── 第 5..N 步：一个功能缺件一步 ──
    def _render_env_item(self, it: dict) -> None:
        """一个缺项一步 —— 用来干啥 / 现在什么情况 / 怎么补 / 这一类要不要你动手。

        ⭐ 2026-10-09 用户口径：缺件引导改成「**每个缺件一个弹窗**」，
        每个弹窗都是三段式（忽略/下载 → 后台下载/等待 → 双进度条 → 完成），
        弹窗上写明**要下载多大**。向导页这边保留摘要，另给一颗「现在就装」直接拉起弹窗。
        """
        t = self.t
        total = self._env_total_steps()
        st = str(it.get("state") or "unknown")
        head = "还缺一项" if st == "fail" else "这一项读不出"
        self.desc.setText("第 %d 步/共 %d 步：%s —— %s" % (
            self._env_step_no(), total, head, str(it.get("title") or "")))
        self.mid.setVisible(False)
        self._clear_body()

        def _lab(txt, color):
            lb = QLabel(txt.replace("**", ""))
            lb.setWordWrap(True)
            lb.setFont(qfont(t, self.t.body_size))
            lb.setStyleSheet("color:%s;background:transparent;" % color)
            return lb

        need = str(it.get("need") or "")
        why = str(it.get("why") or "")
        how = str(it.get("how") or "")
        kl = KIND_LABEL.get(str(it.get("install_kind") or "none"), "")
        dl = it.get("dl") or {}
        if need:
            self.body_lay.addWidget(_lab("用来干啥：" + need, t.tx))
        if why:
            self.body_lay.addWidget(_lab("现在什么情况：" + why, t.tx2))
        # ⭐ 写明**要下载多大**（用户口径：缺件弹窗上必须有体积）——
        #   数据来自后端 `env_guide`，前端不自己编数字。
        if dl.get("size_text"):
            self.body_lay.addWidget(_lab("要下载的东西：" + str(dl.get("size_text")), t.tx2))
        if how:
            self.body_lay.addWidget(_lab("怎么补：" + how, t.tx2))
        if kl:
            self.body_lay.addWidget(_lab("这一类：" + kl, t.tx3))
        self.body_lay.addWidget(desc(
            t, "点「现在就装」会弹出一个安装窗，你可以让它自己在后台装；"
               "不想装也能继续用 —— 只是这一项对应的功能用不了。"))
        row = QHBoxLayout()
        b_go = Btn("现在就装", t, "primary")
        b_go.clicked.connect(lambda: self._open_missing_guide(it))
        row.addWidget(b_go)
        b_re = Btn("重新检测", t, "ghost")
        b_re.clicked.connect(self._recheck_env)
        row.addWidget(b_re)
        row.addStretch(1)
        self.body_lay.addLayout(row)
        self.b_next.setText("下一步")

    def _open_missing_guide(self, it: dict) -> None:
        """打开**这一个**缺件的三段式安装弹窗（每个缺件一个）。

        ⚠️ 弹窗返回后**重拉清单**：用户可能刚点了「后台下载」，也可能点了「忽略」——
        不管哪种，界面上那行状态都该跟着变，不能停在旧读数上。
        """
        try:
            from envguide import MissingGuideDialog # noqa: PLC0415
            d = MissingGuideDialog(self.t, self, it)
            d.exec()
            self._env_fetch()
            self._env_pending_cache = self._env_pending()
            self._render_step_hint()
        except Exception as e: # noqa: BLE001
            self.desc.setText("安装窗打不开：%s" % e)

    def _recheck_env(self) -> None:
        """重检：重新拉清单，回到运行环境那一步（步号重算）。"""
        self._render_step_hint()

    def _advance_env(self) -> None:
        """缺件区推进：还有缺件 ⇒ 下一个；走完 ⇒ 跳体检（末步）。

        `_env_i` 语义＝"当前正在讲的是第几个缺件"（从 0 起）⇒ 步号 = 6 + _env_i。
        """
        pend = self._env_pending_cache
        if self._env_i + 1 < len(pend):
            self._env_i += 1
            self._render_env_item(pend[self._env_i])
            return
        self._render_step_check()
        self.step = 7

    # ── 体检步（末步）──
    def _render_step_check(self) -> None:
        t = self.t
        self.desc.setText("第 %d 步/共 %d 步：代码与依赖检测（不动鼠标，几秒完成："
                          "环境/依赖/微信接入/配置逐项检查）。需要更多「点击测试」"
                          "可在检测中心用单独按钮。" % (self._env_total_steps(),
                                                  self._env_total_steps()))
        self.mid.setVisible(False)
        self._clear_body()
        self.check_out = QPlainTextEdit("体检中…")
        self.check_out.setObjectName("obCheck")
        self.check_out.setReadOnly(True)
        style_field(self.check_out, t, shape="area")
        self.check_out.setFont(qfont(t, 11.5))   # 日志体密排，字号比全族默认再小一档
        self.check_out.setFixedHeight(190)
        self.body_lay.addWidget(self.check_out)
        self.b_next.setText("完成")
        self._run_selfcheck()

    def _run_selfcheck(self) -> None:
        try:
            r = config_io.post_json("/api/selfcheck", {"mode": "code"}, timeout=70.0)
        except Exception as e: # noqa: BLE001
            self.check_out.setPlainText(f"体检没跑成：{e}")
            return
        if not isinstance(r, dict):
            self.check_out.setPlainText("体检没跑成：后台没连上")
            return
        lines = [str(r.get("summary") or ""), ""]
        for c in (r.get("checks") or []):
            st = {"ok": "通过", "warn": "注意"}.get(str(c.get("status")), "未通过")
            lines.append(f"{st} {c.get('name')}：{c.get('detail')}")
            if c.get("hint"):
                lines.append(f"   建议：{c['hint']}")
        self.check_out.setPlainText("\n".join(lines))

    # ── 流程推进 ──
    def _on_next(self) -> None:
        if self.step == 1:
            self._step1_save()
        elif self.step == 2:
            self._step2_save()
        elif self.step == 3:
            self._step3_save()
        elif self.step == 4:
            # 恢复步之后 ⇒ 环境校准（第 5 步）
            self._render_step_hint()
            self.step = 5
        elif self.step == 5:
            # 环境校准之后 ⇒ 进第一个缺件（第 6 步起）；没有缺件 ⇒ 直接跳体检
            if self._env_pending_cache:
                self._env_i = 0                      # 第 6 步＝第 0 个缺件
                self._render_env_item(self._env_pending_cache[0])
            else:
                self._render_step_check()
            self.step = 6
        elif self.step == 6:
            self._advance_env()
        elif self.step == 7:
            self.accept()

    def _step1_save(self) -> None:
        prov = str(self.prov_w.currentData() or "")
        p = self.providers.get(prov) or {}
        k = self.key_w.text().strip()
        model = str(self.model_w.currentData() or "")
        patch: dict = {}
        if k:
            patch["api.api_key"] = k
            if "••••" not in k and not k.startswith("sk-***"):
                kv = config_io.read_path("api.provider_keys", {})
                kv = dict(kv) if isinstance(kv, dict) else {}
                kv[prov] = k
                patch["api.provider_keys"] = kv
        patch["api.model"] = model
        patch["api.base_url"] = str(p.get("base") or "")
        patch["api.provider"] = "" # 走顶层 base_url/api_key（向导场景，web :5474）
        ok, msg = config_io.write_patch(patch)
        if ok:
            self.step = 2
            self._render_step2()
        else:
            self.desc.setText(f"保存没成：{msg}")

    def _step2_save(self) -> None:
        nick = self.nick_w.text().strip()
        if nick:
            config_io.write_patch({"wechat.bot_nickname": nick})
        # 第 3 步：读群列表（web :5500 带 ?refresh=1 的重试语义）
        url = "/api/wechat-groups" + ("?refresh=1" if self._force_refresh else "")
        self._force_refresh = False
        try:
            r = config_io.get_json(url, timeout=20.0)
        except Exception as e: # noqa: BLE001
            self._render_step3_error(str(e))
            self.step = 3
            return
        r = r if isinstance(r, dict) else {}
        if r.get("ok") is False: # 如实说原因（web :5507）
            self._render_step3_error(str(r.get("error") or "原因未明"))
            self.step = 3
            return
        self._render_step3_groups(r.get("groups") or [])
        self.step = 3

    def _step3_save(self) -> None:
        if hasattr(self, "_group_cbs"):
            picked = [str(cb.property("gname")) for cb in self._group_cbs if cb.isChecked()]
        else:
            picked = list(self.picked)
        if picked:
            config_io.write_patch({"wechat.group_name_white_list": picked})
        self._render_step_resume()
        self.step = 4

    def _on_later(self) -> None:
        self.reject()
