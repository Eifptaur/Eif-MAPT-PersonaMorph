# -*- coding: utf-8 -*-
"""首次引导向导 —— 五步上手（对齐 web `onboarding()`，console_html.py:5427-5570）。

触发时机与 web 一致：配置载入后调用，**仅当「还没配好 Key」才弹**
（打码 `••••` / 占位符 / 空 → 弹；真实 Key → 不打扰）。五步：

  1. 选厂商 → 选模型 → 填密钥（写 api.api_key / api.model / api.base_url /
     api.provider_keys.<prov>，并把 api.provider 清空走顶层）；
  2. 机器人昵称 = 你自己微信的原名（写 wechat.bot_nickname）；
  3. 勾选要监听的群（不勾 = 监听所有群；写 wechat.group_name_white_list）；
  4. 点「恢复」开始工作（POST /api/resume）；
  5. 代码与依赖体检（POST /api/selfcheck mode=code，只读展示）。

厂商清单/模型列表/接口地址/Key 前缀一律**运行时解析 web 源码**（`_providers_from_web`），
不往 Qt 复制一份 —— web 改厂商，向导自动跟。

为什么独立成模块：向导是「多步 + 每步换内容」的流程件，塞进 panels_custom（已很长）
会把「手写面板」这一职责搅浑；这里只装向导，入口 `maybe_show(t, shell)`。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from stylekit_qt import Tokens, qfont, rgba
from widgets import Btn, DraggableDialog, desc, h2

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
    """五步向导（无边框 + 卡片，与 confirm.ConfirmDialog 同款壳）。可拖动。"""

    def __init__(self, t: Tokens, shell: QWidget):
        super().__init__(shell.window())
        self.t = t
        self.shell = shell
        self.step = 1
        self.picked: list[str] = []
        self._force_refresh = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setObjectName("onboard")
        self.resize(620, 460)
        self.enable_drag() # 可拖动（用户点单：所有弹窗都能按背景挪）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget()
        card.setObjectName("C8OnboardCard")
        card.setStyleSheet(
            f"#C8OnboardCard{{background:{t.card};border:1px solid {t.bd};"
            f"border-radius:{t.radius_card + 2}px;}}")
        outer.addWidget(card)
        self.v = QVBoxLayout(card)
        self.v.setContentsMargins(24, 22, 24, 18)
        self.v.setSpacing(10)

        self.v.addWidget(h2(t, "欢迎使用 Persona Morph · 五步上手"))
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setFont(qfont(t, t.body_size))
        self.desc.setStyleSheet(f"color:{t.tx2};background:transparent;")
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

        self.prov_w = QComboBox()
        self.prov_w.setObjectName("obProvider")
        for k, v in self.providers.items():
            self.prov_w.addItem(str(v.get("label") or k), k)
        self.prov_w.activated.connect(lambda _i: self._render_models())

        self.model_w = QComboBox()
        self.model_w.setObjectName("obModel")

        self.key_w = QLineEdit()
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
            lay = it.layout()
            if lay is not None:
                self._drop_layout(lay)

    def _drop_layout(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            sub = it.layout()
            if sub is not None:
                self._drop_layout(sub)

    def _render_step1(self) -> None:
        self.desc.setText("第 1 步/共 5 步：选择模型厂商 → 选择模型 → 填入该厂商的 密钥。")
        self.mid.setVisible(True)
        self._clear_body()
        self.b_next.setText("下一步")

    def _render_step2(self) -> None:
        t = self.t
        self.desc.setText("第 2 步/共 5 步：把「机器人昵称」改成你自己微信的原名"
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
        self.nick_w = QLineEdit()
        self.nick_w.setObjectName("obNick")
        self.nick_w.setPlaceholderText("填你自己微信的原名")
        self.nick_w.setText("" if cur == "群deepseek" else cur)
        row.addWidget(lb)
        row.addWidget(self.nick_w, 1)
        self.body_lay.addLayout(row)
        self.body_lay.addWidget(desc(
            t, "默认的「群deepseek」只是个占位，不是你的名字。填成你自己微信的原名"
               "（你那个号在微信里叫什么，就填什么）。"))
        self.b_next.setText("保存并继续")

    def _render_step3_groups(self, groups: list) -> None:
        t = self.t
        self.desc.setText(f"第 3 步/共 5 步：勾选需要机器人监听的群"
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
        self.desc.setText(f"第 3 步/共 5 步：这台机器上暂时读不到群列表 —— {err}")
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

    def _render_step4(self) -> None:
        t = self.t
        self.desc.setText("第 4 步/共 5 步：点下面的「恢复」——程序默认是暂停的，"
                          "点了它才开始监听群消息。")
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

    def _render_step5(self) -> None:
        t = self.t
        self.desc.setText("第 5 步/共 5 步：代码与依赖检测（不动鼠标，几秒完成："
                          "环境/依赖/微信接入/配置逐项检查）。需要更多「点击测试」"
                          "可在检测中心用单独按钮。")
        self.mid.setVisible(False)
        self._clear_body()
        self.check_out = QPlainTextEdit("体检中…")
        self.check_out.setObjectName("obCheck")
        self.check_out.setReadOnly(True)
        self.check_out.setFont(qfont(t, 11.5))
        self.check_out.setFixedHeight(190)
        self.check_out.setStyleSheet(
            f"QPlainTextEdit{{background:{rgba(t.q('tx'), 0 if t.glass else 10).name()};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:8px;padding:6px;}}")
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
        t = self.t
        if self.step == 1:
            self._step1_save()
        elif self.step == 2:
            self._step2_save()
        elif self.step == 3:
            self._step3_save()
        elif self.step == 4:
            self._render_step5()
            self.step = 5
        elif self.step == 5:
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
        self._render_step4()
        self.step = 4

    def _on_later(self) -> None:
        self.reject()
