# -*- coding: utf-8 -*-
"""环境引导 + 缺件引导 —— 两个原生弹窗（Qt 侧）。

用户口径（2026-10-09，逐字）：
  · **环境引导**：「引导用户把微信调成浅色模式、标准字号」—— **一个**弹窗。
  · **缺件引导**：「每个缺件一个弹窗」—— **多个**弹窗，每个都是**三段式**：
      ① 初始：两个按钮「忽略」「下载」（弹窗上写明**要下载的东西有多大**）；
      ② 点「下载」：两个按钮「后台下载」「等待」（帮用户静默安装，不打扰）；
      ③ 选「等待」：这个弹窗上有**下载进度条 + 安装进度条**；装完切到"安装完成，您可以正常使用"。
    「逻辑就是不要教用户做事，要帮用户把事办好」。

⛔ 纪律：
  · 0 系统 `QMessageBox`（`launcher-src/README.md` 硬规矩 1）—— 全部自绘；
  · 界面文案**不许出现 Markdown 星号**（Qt 的 QLabel 不解析，会原样显示）⇒ 一律过 `_clean()`；
  · 体积/大小一律读后端 `env_guide` 给的那份（`dl.size_text`），**前端不许自己编数字**。
"""
from __future__ import annotations

import os
import subprocess
import sys

# ⚠️ 直接 `python ui_qt/envguide.py` 跑自检时，`ui_qt` 自己不在 `sys.path` 上
#   （同目录的 `confirm.py` 没这个问题，因为它只被 `shoot.py` 导入、从没单独跑过）。
#   补这一句，让自检能独立跑起来 —— 判据脚本也就不用再设 PYTHONPATH。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from stylekit_qt import Tokens, qfont, surface_bg
from widgets import Btn, DraggableDialog


# ---------------------------------------------------------------- 小工具
def _clean(s) -> str:
    """去掉 Markdown 星号 —— Qt 的 QLabel 不解析，写了会把星号原样显示出来。

    ⚠️ 后端（`agent/env_hint.py` / `agent/env_guide.py`）的文案里**故意**留着 `**`，
    那是给 web 控制台（真会渲染）看的；到 Qt 这边必须剥掉。集中在这一处剥，
    别在每处调用点各写一遍 —— 漏一处就是一个用户能看见的星号。
    """
    return str(s or "").replace("**", "")


def _wrap_h(text: str, size: float, width_px: int) -> int:
    """按"每行放几个字"估一个够用的最小高度。

    ⚠️ 与 `confirm.py` 同一口径：Qt 的 `QLabel(wordWrap=True)` 在 `adjustSize()`
    之后会被父布局压扁，两行文字挤成一坨（whale-3 首版实拍过）。宁可多留一行。
    """
    text = _clean(text)
    if not text:
        return 0
    per = max(1, int(width_px / max(1.0, size)))
    units = sum(0.55 if ord(ch) < 128 else 1.0 for ch in text)
    lines = max(1, int(units / per) + (1 if units % per else 0))
    return int(lines * (size + 5) + 2)


def _shell_qss(t: Tokens) -> str:
    return (f"#Shell{{background:{surface_bg(t)};border:1px solid {t.bd};"
            f"border-radius:{t.radius_card + 2}px;}}")


def _center_on(dlg: QDialog, parent: QWidget) -> None:
    dlg.adjustSize()
    if parent is not None and parent.isVisible():
        g = parent.frameGeometry()
        dlg.move(g.center() - dlg.rect().center())
    else:
        dlg.move(320, 240)


def _bar(t: Tokens, accent: bool = False) -> QProgressBar:
    """一条进度条。颜色走 token，不写死 —— 三套主题共用这一个实现点。"""
    b = QProgressBar()
    b.setRange(0, 100)
    b.setValue(0)
    b.setTextVisible(False)
    b.setFixedHeight(10)
    col = t.blue if accent else t.blue
    b.setStyleSheet(
        f"QProgressBar{{background:{t.bd};border:0;border-radius:5px;}}"
        f"QProgressBar::chunk{{background:{col};border-radius:5px;}}"
    )
    return b


# ==================================================================
# ① 环境引导 —— **一个**弹窗：引导用户把微信调成浅色 + 标准字号
# ==================================================================
class EnvGuideDialog(DraggableDialog, QDialog):
    """一个弹窗讲清"把微信调成什么样"（**只有两件事**：微信浅色 + 微信标准字号）。

    ⛔ 只问用户"微信那一层"的事（2026-10-10 用户当面纠正）：
       **显示缩放**会把用户所有软件的图标显示全搞坏；**OCR 引擎**是程序自己该干的活
       ⇒ 两样都**不许**出现在这里（用户最多只到微信这一层，再往下他管不了也不会）。

    数据来自 `agent/env_hint.check()`（后台算好，前端不自己编文案）。

    ⛔ 只提醒、不替用户改微信（`env_hint` 的三条口径之一）——
       所以这里**没有**"帮你改好"的按钮，只有"我调好了，重检一下"。
    """

    rechecked = Signal()

    def __init__(self, t: Tokens, parent: QWidget, hint: dict):
        super().__init__(parent)
        self.t = t
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        shell.setObjectName("Shell")
        shell.setStyleSheet(_shell_qss(t))
        outer.addWidget(shell)

        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 22, 24, 18)
        box.setSpacing(12)

        tl = QLabel("先花一分钟，把微信调成这个样子")
        tl.setFont(qfont(t, 16, 600))
        tl.setStyleSheet(f"color:{t.tx};background:transparent;")
        box.addWidget(tl)

        sub = QLabel("不用改也能用，只是识别会差一些（容易认错字、要多绕一层）。")
        sub.setFont(qfont(t, t.body_size))
        sub.setWordWrap(True)
        sub.setMinimumHeight(_wrap_h(sub.text(), t.body_size, 320))
        sub.setStyleSheet(f"color:{t.tx2};background:transparent;")
        box.addWidget(sub)

        # 逐条摆出来（✓ 已达标 / ! 建议调 / · 没测到）
        items = (hint or {}).get("items") or []
        mark = {"ok": ("✓", t.ok), "warn": ("!", t.err), "unknown": ("·", t.tx3)}
        if items:
            wrap = QVBoxLayout()
            wrap.setContentsMargins(0, 2, 0, 2)
            wrap.setSpacing(8)
            for it in items:
                lvl = str(it.get("level") or "")
                mk, col = mark.get(lvl, ("·", t.tx3))
                text = _clean(it.get("text"))
                if not text:
                    continue
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(9)
                ic = QLabel(mk)
                ic.setFont(qfont(t, t.body_size, 700))
                ic.setFixedWidth(14)
                ic.setStyleSheet(f"color:{col};background:transparent;")
                row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                lb = QLabel(text)
                lb.setFont(qfont(t, t.body_size))
                lb.setWordWrap(True)
                lb.setMinimumHeight(_wrap_h(text, t.body_size, 300))
                lb.setStyleSheet(f"color:{t.tx};background:transparent;")
                row.addWidget(lb, 1)
                wrap.addLayout(row)
                why = _clean(it.get("why"))
                if why and lvl == "warn":
                    wl = QLabel("　　" + why)
                    wl.setFont(qfont(t, t.body_size - 1))
                    wl.setWordWrap(True)
                    wl.setMinimumHeight(_wrap_h(why, t.body_size - 1, 290))
                    wl.setStyleSheet(f"color:{t.tx3};background:transparent;")
                    wrap.addWidget(wl)
            box.addLayout(wrap)
        else:
            empty = QLabel(_clean((hint or {}).get("text")) or "环境没测到（后台没连上）。")
            empty.setFont(qfont(t, t.body_size))
            empty.setWordWrap(True)
            empty.setMinimumHeight(_wrap_h(empty.text(), t.body_size, 320))
            empty.setStyleSheet(f"color:{t.tx2};background:transparent;")
            box.addWidget(empty)

        note = QLabel("调好之后点「我调好了，重检一下」；不想调也行，直接关掉就能继续用。")
        note.setFont(qfont(t, t.body_size - 1))
        note.setWordWrap(True)
        note.setMinimumHeight(_wrap_h(note.text(), t.body_size - 1, 320))
        note.setStyleSheet(f"color:{t.tx3};background:transparent;")
        box.addWidget(note)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)
        self.btn_later = Btn("以后再说", t, "ghost")
        self.btn_later.clicked.connect(self.reject)
        row.addWidget(self.btn_later)
        self.btn_re = Btn("我调好了，重检一下", t, "primary")
        self.btn_re.clicked.connect(self._recheck)
        row.addWidget(self.btn_re)
        box.addLayout(row)

        _center_on(self, parent)
        self.btn_re.setFocus()

    def _recheck(self) -> None:
        self.rechecked.emit()
        self.accept()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.enable_drag()


# ==================================================================
# ② 缺件引导 —— **每个缺件一个**弹窗，三段式
# ==================================================================
class MissingGuideDialog(DraggableDialog, QDialog):
    """单个缺件的三段式弹窗。

    三段（同一扇窗里切换，不连开三个窗追着用户跑）：
      ① 初始：「忽略」「下载」（并写明**要下多大**）；
      ② 点「下载」：「后台下载」「等待」；
      ③ 选「等待」：两条进度条（下载 + 安装），完了切到「安装完成，您可以正常使用」。

    `Action` 取值：`ignore` / `download-bg` / `install-now` / `none`。
    """

    Action = "none"

    def __init__(self, t: Tokens, parent: QWidget, item: dict):
        super().__init__(parent)
        self.t = t
        self._item = dict(item or {})
        dl = self._item.get("dl") or {}
        self._url = str(dl.get("url") or "")
        self._size_text = str(dl.get("size_text") or "大小以官网为准")
        self._silent = str(dl.get("silent") or "no")
        self._note = _clean(dl.get("note"))
        self._stage = 1

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        shell.setObjectName("Shell")
        shell.setStyleSheet(_shell_qss(t))
        outer.addWidget(shell)

        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 22, 24, 18)
        box.setSpacing(12)

        title = _clean(self._item.get("title")) or "还缺一样东西"
        self.lbl_title = QLabel("还差一步：%s" % title)
        self.lbl_title.setFont(qfont(t, 16, 600))
        self.lbl_title.setStyleSheet(f"color:{t.tx};background:transparent;")
        box.addWidget(self.lbl_title)

        # 正文（每段换一次文字；高度按最长那段留 —— 免得切换时窗口跳）
        self.lbl_body = QLabel()
        self.lbl_body.setFont(qfont(t, t.body_size))
        self.lbl_body.setWordWrap(True)
        self.lbl_body.setStyleSheet(f"color:{t.tx2};background:transparent;")
        box.addWidget(self.lbl_body)

        # 体积行（弹窗上**必须**写明要下多大 —— 用户口径）
        self.lbl_size = QLabel()
        self.lbl_size.setFont(qfont(t, t.body_size))
        self.lbl_size.setWordWrap(True)
        self.lbl_size.setStyleSheet(f"color:{t.tx3};background:transparent;")
        box.addWidget(self.lbl_size)

        # ③ 的两条进度条（先建好、默认藏起来）
        self.box_dl = QHBoxLayout()
        self.box_dl.setContentsMargins(0, 2, 0, 0)
        self.box_dl.setSpacing(10)
        self.lbl_dl = QLabel("下载")
        self.lbl_dl.setFont(qfont(t, t.body_size - 1))
        self.lbl_dl.setFixedWidth(40)
        self.lbl_dl.setStyleSheet(f"color:{t.tx2};background:transparent;")
        self.bar_dl = _bar(t)
        self.box_dl.addWidget(self.lbl_dl)
        self.box_dl.addWidget(self.bar_dl, 1)
        box.addLayout(self.box_dl)

        self.box_in = QHBoxLayout()
        self.box_in.setContentsMargins(0, 0, 0, 0)
        self.box_in.setSpacing(10)
        self.lbl_in = QLabel("安装")
        self.lbl_in.setFont(qfont(t, t.body_size - 1))
        self.lbl_in.setFixedWidth(40)
        self.lbl_in.setStyleSheet(f"color:{t.tx2};background:transparent;")
        self.bar_in = _bar(t)
        self.box_in.addWidget(self.lbl_in)
        self.box_in.addWidget(self.bar_in, 1)
        box.addLayout(self.box_in)

        for w in (self.lbl_dl, self.bar_dl, self.lbl_in, self.bar_in):
            w.setVisible(False)

        self.lbl_step = QLabel()
        self.lbl_step.setFont(qfont(t, t.body_size - 1))
        self.lbl_step.setWordWrap(True)
        self.lbl_step.setStyleSheet(f"color:{t.tx3};background:transparent;")
        box.addWidget(self.lbl_step)

        # 按钮行（三组 → 按段显隐；左「忽略/后台下载」，右「下载/等待/继续」）
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(10)
        self.btn_left = Btn("忽略", t, "ghost")
        self.btn_right = Btn("下载", t, "primary")
        row.addWidget(self.btn_left)
        row.addStretch(1)
        row.addWidget(self.btn_right)
        box.addLayout(row)
        self.btn_left.clicked.connect(self._on_left)
        self.btn_right.clicked.connect(self._on_right)

        self._apply_stage()
        _center_on(self, parent)
        self.btn_right.setFocus()

    # ---------------------------------------------------------- 段切换
    def _apply_stage(self) -> None:
        it = self._item
        need = _clean(it.get("need"))
        why = _clean(it.get("why"))
        st = self._stage

        if st == 1:
            body = why or need or "这一项现在还没就绪。"
            extra = ""
            if self._silent == "yes":
                extra = "装它不用联网，也不会跳任何窗口。"
            elif self._silent == "admin":
                extra = "装的时候 Windows 会弹一次确认框，点「是」就行 —— 那一下躲不掉。"
            self.lbl_body.setText(body + ("\n" + extra if extra else ""))
            self.lbl_size.setText("要下载的东西：%s" % self._size_text)
            self.lbl_step.setText("")
            self.btn_left.setText("忽略")
            self.btn_right.setText("下载")
            for w in (self.lbl_dl, self.bar_dl, self.lbl_in, self.bar_in):
                w.setVisible(False)
        elif st == 2:
            self.lbl_body.setText("要现在看着它装完，还是让它自己在后台装？\n"
                                  "选「后台下载」你不用等，装好下次打开就能直接用。")
            self.lbl_size.setText("要下载的东西：%s" % self._size_text)
            self.lbl_step.setText(self._note)
            self.btn_left.setText("后台下载")
            self.btn_right.setText("等待")
            for w in (self.lbl_dl, self.bar_dl, self.lbl_in, self.bar_in):
                w.setVisible(False)
        elif st == 3:
            self.lbl_body.setText("正在处理。这个窗口关掉也不影响它在后台继续。")
            self.lbl_size.setText("")
            self.lbl_step.setText("正在下载…")
            self.btn_left.setText("在后台继续")
            self.btn_right.setText("")
            self.btn_right.setVisible(False)
            for w in (self.lbl_dl, self.bar_dl, self.lbl_in, self.bar_in):
                w.setVisible(True)
        else:  # 4 完成
            self.lbl_body.setText("安装完成，您可以正常使用。")
            self.lbl_size.setText("")
            self.lbl_step.setText("已经装好了。")
            self.btn_left.setText("知道了")
            self.btn_right.setText("")
            self.btn_right.setVisible(False)
            for w in (self.lbl_dl, self.bar_dl, self.lbl_in, self.bar_in):
                w.setVisible(True)
            self.bar_dl.setValue(100)
            self.bar_in.setValue(100)

        # 高度按当前这段重算（每段行数不同，不重算会切字或留白）
        self._fit()

    def _fit(self) -> None:
        for lb in (self.lbl_body, self.lbl_size, self.lbl_step):
            txt = lb.text()
            lb.setMinimumHeight(_wrap_h(txt, lb.font().pointSizeF() or self.t.body_size, 330))
        self.adjustSize()

    # ---------------------------------------------------------- 行为
    def _on_left(self) -> None:
        if self._stage == 2:
            # 「后台下载」：起个静默子进程就走，不打扰用户
            self.Action = "download-bg"
            _spawn_detached(self._url, self._silent)
            self.accept()
            return
        if self._stage == 3:
            # 「在后台继续」：进程本来就还跑着，直接关窗
            self.Action = "download-bg"
            self.accept()
            return
        if self._stage == 4:
            self.Action = "install-now"
            self.accept()
            return
        # ① 「忽略」
        self.Action = "ignore"
        self.reject()

    def _on_right(self) -> None:
        if self._stage == 1:
            self._stage = 2
            self._apply_stage()
            return
        if self._stage == 2:
            self._stage = 3
            self._apply_stage()
            _run_with_progress(self)
            return

    # ---------------------------------------------------------- 进度刷新（供线程回调）
    def set_progress(self, phase: str) -> None:
        if phase == "download":
            self.bar_dl.setValue(min(95, self.bar_dl.value() + 12))
            self.lbl_step.setText("正在下载…")
        elif phase == "install":
            self.bar_dl.setValue(100)
            self.lbl_step.setText("正在安装…")
        elif phase == "verify":
            self.bar_in.setValue(92)
            self.lbl_step.setText("正在确认…")
        elif phase == "done":
            self._stage = 4
            self._apply_stage()
        elif phase.startswith("fail:"):
            self.bar_in.setValue(100)
            self.lbl_body.setText("这次没装上（%s）。\n机器人仍然在后台正常运行，不影响它在微信里干活。"
                                  % phase[5:])
            self.lbl_step.setText("")
            self.btn_left.setText("知道了")
            self._fit()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            # Esc 一律当"忽略"（不是"下载"）—— 手快按 Esc 不会误装
            self.Action = "ignore"
            self.reject()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.enable_drag()


# ---------------------------------------------------------------- 静默安装的执行
def _bootstrapper_dir() -> str:
    """找 `assets/webview2/` 这类随包目录的根（Qt 侧从自己的文件位置往上找仓库根）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _spawn_detached(url: str, silent: str) -> None:
    """起一个**不打扰用户**的安装进程就返回（窗口已经关了，用户在干别的）。

    ⛔ 静默到底：不开新窗口、不弹控制台。做不到静默的（`silent == "no"`）就**不下手** ——
       那种情况由调用方给"打开官网"的入口，不许假装装上了。
    """
    if not url or silent == "no":
        return
    try:
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([url], creationflags=flags, close_fds=True)
    except Exception:  # noqa: BLE001
        pass


def _run_with_progress(dlg: "MissingGuideDialog") -> None:
    """③ 的安装：后台跑，主线程按轮询刷进度条。

    ⛔ **不许手搓 `threading.Thread`**（全量棘轮硬零）：跨线程落地一律走
       `async_ui.run_async` —— 它带**存活判**（弹窗被关掉/切页销毁后不再落地，
       否则取已销毁控件抛 `RuntimeError`，用户看到的是"整个界面用着用着没了"）。
       本函数原来自己 `threading.Thread(...) + QMetaObject.invokeMethod` 就是踩了这条。

    ⚠️ 进度是**按外部可观测量推**的（官方引导器没有进度接口）—— 见
    `launcher-src/webview2guide.cs` 里 `Install(dir, Action<string>, out why)` 的同一段注释。
    这里只把"到哪一步了"如实显示出来，不编造精确百分比。
    """
    from async_ui import run_async  # noqa: PLC0415

    def _work(box: dict) -> None:
        url = dlg._url
        silent = dlg._silent
        if not url or silent == "no":
            box["phase"] = "fail:这一项没法自动装，请到官网自己装一次"
            box["done"] = True
            return
        box["phase"] = "download"
        try:
            flags = 0
            if sys.platform == "win32":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            p = subprocess.Popen([url], creationflags=flags, close_fds=True)
            # 轮询：还在跑 ⇒ 继续"下载"；过了 12 秒还没退 ⇒ 换到"安装"
            import time  # noqa: PLC0415
            t0 = time.time()
            while p.poll() is None:
                if box.get("phase") == "download" and time.time() - t0 > 12:
                    box["phase"] = "install"
                time.sleep(0.25)
            box["phase"] = "verify"
            code = p.returncode
            box["phase"] = "done" if code == 0 else "fail:安装程序退出码 %s" % code
        except Exception as e:  # noqa: BLE001
            box["phase"] = "fail:%s" % str(e)[:60]
        box["done"] = True

    def _tick(box: dict) -> None:
        # 主线程：把后台上报的当前阶段刷到进度条（读写 box 里的中间量）
        ph = box.get("phase")
        if ph:
            dlg.set_progress(str(ph))

    def _done(box: dict) -> None:
        _tick(box) # 最后一次落地，确保"完成/失败"文案一定显示出来

    run_async(_work, _done, page=dlg, on_tick=_tick, name="env-install")


# ---------------------------------------------------------------- 高层入口
def guide_env(t: Tokens, parent: QWidget, hint: dict) -> bool:
    """环境引导（一个弹窗）。返回 True ＝ 用户点了"重检一下"。"""
    d = EnvGuideDialog(t, parent, hint)
    return d.exec() == QDialog.DialogCode.Accepted


def guide_missing(t: Tokens, parent: QWidget, items: list) -> list:
    """缺件引导：**逐个缺件各弹一个**，返回用户的选择清单。

    ⚠️ 只对"确实缺"的项弹（`ok is False`）；`unknown`（读不出）不弹 ——
       不许把"没测到"当成"缺"（`env_guide` 的三态口径）。
    """
    decisions = []
    for it in (items or []):
        if it.get("ok") is not False:
            continue
        d = MissingGuideDialog(t, parent, it)
        d.exec()
        decisions.append({"key": str(it.get("key") or ""), "action": d.Action})
    return decisions


def _selftest() -> list:
    """不需要真窗口的纪律自检（防"纪律被改掉"）。

    ⚠️ **先剥注释再查**：本文件开头的纪律说明里就写着 `QMessageBox` 与「怎么办：点…」
    这两个**反例**，不剥的话会把自己写的说明当成违规命中（实拍踩过这个误报）。
    """
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    mod = inspect.getsource(__import__(__name__))
    # 用 AST 把**注释与文档字符串**整段去掉：`ast.unparse` 只吐代码。
    # ⚠️ 但 `ast.unparse` **会保留 docstring**（它是 `Expr(Constant)` 节点，不是注释）
    #   —— 本文件开头的纪律说明就是 docstring，照样会被抓出来 ⇒ 先手动把 docstring 摘掉。
    try:
        tree = ast.parse(mod)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)  # 摘掉这一层的 docstring
        code = ast.unparse(tree)
    except Exception:  # noqa: BLE001
        code = mod
    # ⚠️ 两个"反例关键词"必须**在运行时拼出来**，不能写字面量：`_selftest` 自己的源码也是
    #   `inspect.getsource` 的对象 ⇒ 写明面量会让这条判据命中自己（永远 FAIL，实拍过）。
    #   ⚠️ 也**不能**用 `"A" + "B"`：`ast.unparse` 会把它折叠回 `'AB'` 单个字面量 ⇒ 照样命中。
    #   用 `chr()` 拼，AST 里就没有那个连续字面量。
    _msgbox = "Q" + chr(77) + "essageBox"
    _tutorial = "怎么办" + chr(65306) + "点"
    # ⛔ 同样必须拼出来：`Thread(` 若写字面量，本行自己就把它引进 `code` 里了（自指陷阱）。
    _rawthread = "Th" + "read("
    rows = [
        ("没有用系统 MessageBox（硬规矩 1）", _msgbox not in code, ""),
        ("三段式：初始两按钮「忽略」「下载」", 'Btn("忽略"' in code and 'Btn("下载"' in code, ""),
        ("② 两按钮「后台下载」「等待」", 'setText("后台下载")' in code and 'setText("等待")' in code, ""),
        ("③ 有下载进度条与安装进度条（两条）", "bar_dl" in code and "bar_in" in code, ""),
        ("③ 装完说「安装完成，您可以正常使用」", "安装完成，您可以正常使用" in code, ""),
        ("★ 弹窗上写明要下载多大（读后端 size_text）", "size_text" in code and "要下载的东西" in code, ""),
        ("★ 环境引导＝引导用户调微信（浅色 + 标准字号）",
         "调成这个样子" in code and "不用改也能用" in code, ""),
        ("⛔ 文案过 _clean（Qt 不解析 Markdown 星号）", "_clean(" in code, ""),
        ("⛔ 没有教程式文案（不出现在弹窗里）", _tutorial not in code, ""),
        # ⛔ 跨线程落地不许手搓（全量棘轮硬零）：必须走 async_ui.run_async，
        #    否则弹窗被销毁后回调落地会取到已销毁控件 ⇒ RuntimeError（用户看到整个界面没了）。
        ("⛔ 装进度不许手搓线程（走 async_ui.run_async）",
         "run_async(" in code and _rawthread not in code, ""),
    ]
    return rows


if __name__ == "__main__":
    rows = _selftest()
    bad = [r for r in rows if not r[1]]
    for name, okk, extra in rows:
        print(("  OK  " if okk else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print("%d 通过 / %d 失败" % (len(rows) - len(bad), len(bad)))
    raise SystemExit(1 if bad else 0)
