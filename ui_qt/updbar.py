# -*- coding: utf-8 -*-
"""更新公告 —— web updbar（agent/console_html.py L806-880）的 Qt 复刻。

 #13：拆掉 shell.py:473 的假条，接真值四态机 + 三按钮（join_url 口径）。
 #14：**形态改造** —— 把三按钮塞进 60px 顶栏是总调度自认的设计缺陷
（web 真值里 updBar 本是顶栏下方独立一行，console_html.py L760-767 全屏宽度；
用户截图里三按钮挤成墨块）。按用户点单改为「胶囊 + 下滑 popover」：

  · 顶栏常态只留一个胶囊：`有新版本 {theirs}` / `更新源异常` /
    `上次更新只装了一半`（warn 底色区分）；**无新版且无异常时胶囊不出现**
  · 点胶囊 → 顶栏下方滑出 Popover 面板（popover.py，220ms OutCubic 可打断）：
    正文 = notes 逐条（「这版本更了啥」）/ pending 明细 / error 原因；
    三按钮 = 立即更新（primary）/ 稍后 / 不再提醒这个版本
  · 更新中 = 进度态就地显示（胶囊 + 面板同步刷：download % / verify /
    done→重启 / error→可重试）
  · 面板外点 / Esc / 再点胶囊 → 收回（Qt.Popup 白拿）；stateSaved===false
    附加提示保留
  · API 链路全沿用/api/update /api/update_apply /api/update_skip

「稍后」的会话抑制语义对齐 web：同版本不再打扰，出**新**版本照常弹。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from confirm import ConfirmDialog
from popover import Popover
from stylekit_qt import Tokens, pill, qfont, rgba
from widgets import Btn


def _mb(n) -> str:
    try:
        return "%.1f MB" % (float(n) / 1048576.0)
    except Exception: # noqa: BLE001
        return "?"


def decide(s: dict | None) -> tuple[str, bool]:
    """四态机纯函数（web L817-837 逐条对照）：返回 (文案, warn)；隐藏由调用方判。

    快照失败提示独立追加（web 语义：无论主态显示什么、甚至主态隐藏，都出）。
    返回 text 为空 = 隐藏。
    """
    if not isinstance(s, dict):
        return "", False
    st = str(s.get("status") or "")
    text = ""
    warn = False
    if st == "pending":
        pend = s.get("pending") or []
        n = len(pend)
        names = "、".join(str(x) for x in pend[:3])
        text = ("上次更新只装了一半：有 %d 件没换成" % n
                + ("（%s）" % names if names else "")
                + " —— 再点一次「立即更新」即可补换")
        warn = True
    elif st == "newer":
        n = (" · " + " · ".join(str(x) for x in (s.get("notes") or []))) if s.get("notes") else ""
        text = "有新版本 %s（当前 %s）%s" % (s.get("theirs") or "？", s.get("mine") or "未记录", n)
    elif st == "older":
        text = "更新源里的版本（%s）比本机旧，可能是源配错了" % (s.get("theirs") or "？")
        warn = True
    elif st == "error":
        text = "更新源异常：%s" % (s.get("why") or "未知原因")
        warn = True
    # 其余（current / off=用户关了提醒 / 空态）→ text 空 = 隐藏
    if s.get("stateSaved") is False or s.get("stateSaveError"):
        snap = ("（注意：这次的更新读数没能写进快照：%s ⇒ 下次打开控制台会回到上一次的记录）"
                % (s.get("stateSaveError") or "写盘失败"))
        text = (text + " · " + snap) if text else snap
        warn = True
    return text, warn


def pill(s: dict | None) -> tuple[str, bool] | None:
    """胶囊短文案纯函数。返回 None = 胶囊不出现（无新版无异常）。"""
    if not isinstance(s, dict):
        return None
    st = str(s.get("status") or "")
    if st == "pending":
        return "上次更新只装了一半", True
    if st == "newer":
        return "有新版本 %s" % (s.get("theirs") or "？"), False
    if st == "older":
        return "更新源版本有误", True
    if st == "error":
        return "更新源异常", True
    # 主态隐藏（current/off）但快照失败 → web 语义：照样出（读数没落盘必须看得见）
    if s.get("stateSaved") is False or s.get("stateSaveError"):
        return "更新读数没写进快照", True
    return None


def _notes_text(s: dict | None) -> str:
    """面板正文用：notes 逐条（「这版本更了啥」，web updbar 同源数据）。"""
    if not isinstance(s, dict):
        return ""
    ns = s.get("notes") or []
    return "\n".join("· %s" % x for x in ns if str(x).strip())


class UpdateBar(QFrame):
    """顶栏更新胶囊。数据入口 apply_state(s)；动作自含。"""

    def __init__(self, t: Tokens, parent=None):
        super().__init__(parent)
        self.t = t
        self.setObjectName("UpdPill")
        from PySide6.QtCore import Qt # noqa: PLC0415

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击查看这版本更了啥")
        self._cur: dict | None = None # 最近一次 /api/update 真值
        self._dismissed: tuple | None = None # 「稍后」会话抑制（status, theirs）
        self._polling = False # 更新作业轮询中（主态机让位）
        self._poll_iv = QTimer(self)
        self._poll_iv.setInterval(900) # web setInterval(pollJob, 900)
        self._poll_iv.timeout.connect(self._poll_once)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 3, 12, 3)
        self.txt = QLabel("")
        self.txt.setFont(qfont(t, 11.5, 500))
        self.txt.setStyleSheet("background:transparent;")
        lay.addWidget(self.txt)
        self._style(warn=False, visible=False)

        # ── 下滑面板（点胶囊弹出）。parent=self：主题重建销毁旧胶囊时面板随葬，
        #    不留幽灵顶层窗（Qt.Popup 有 parent 只影响归属，显示仍是独立浮层）。──
        self.pop = Popover(t, parent=self)
        self.detail = QLabel("")
        self.detail.setFont(qfont(t, 12))
        self.detail.setWordWrap(True)
        self.pop.add(self.detail)
        self.notes = QLabel("")
        self.notes.setFont(qfont(t, 11.5))
        self.notes.setStyleSheet("background:transparent;")
        self.notes.setWordWrap(True)
        self.pop.add(self.notes)
        self.prog = QLabel("")
        self.prog.setFont(qfont(t, 11.5, 500))
        self.prog.setWordWrap(True)
        self.prog.hide()
        self.pop.add(self.prog)
        row = self.pop.add_row()
        self.btn_go = Btn("立即更新", t, "primary")
        self.btn_go.clicked.connect(self._on_go)
        row.addWidget(self.btn_go)
        self.btn_later = Btn("稍后", t, "ghost")
        self.btn_later.clicked.connect(self._on_later)
        row.addWidget(self.btn_later)
        self.btn_skip = Btn("不再提醒这个版本", t, "ghost")
        self.btn_skip.clicked.connect(self._on_skip)
        row.addWidget(self.btn_skip)
        self.btn_reset = Btn("重置更新状态", t, "ghost")
        self.btn_reset.setToolTip("更新闸门卡死时的出口：只清本机记的「见过的最高版本」")
        self.btn_reset.clicked.connect(self._on_reset)
        row.addWidget(self.btn_reset)

    # ------------------------------------------------------------ 交互

    def mousePressEvent(self, e): # noqa: N802
        self.pop.toggle_at(self, width=470, align="left")
        super().mousePressEvent(e)

    def close_pop(self) -> None:
        """⛔ P1（切界面闪小窗）：主题切换 `_rebuild` 会 deleteLater 掉旧胶囊，
        而 Qt.Popup 面板是**独立顶层窗**——父销毁的同一帧里它还在屏幕上闪一下。
        ⇒ Shell._rebuild 开头先挨个收回（本方法），再拆旧控件。"""
        try:
            if self.pop.isVisible():
                self.pop.close()
        except Exception: # noqa: BLE001
            pass

    # ------------------------------------------------------------ 样式

    def _style(self, warn: bool, visible: bool) -> None:
        t = self.t
        if warn:
            fg = t.warn
            self.setStyleSheet(
                f"QFrame#UpdPill{{background:{rgba(t.q('warn'), 26).name(QColor.NameFormat.HexArgb)};"
                f"border:1px solid {rgba(t.q('warn'), 120).name(QColor.NameFormat.HexArgb)};"
                f"border-radius:{pill(22)}px;}}"
                f"QFrame#UpdPill QLabel{{color:{fg};background:transparent;}}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#UpdPill{{background:{t.card};"
                f"border:1px solid {rgba(t.q('blue'), 90).name(QColor.NameFormat.HexArgb)};"
                f"border-radius:{pill(22)}px;}}"
                f"QFrame#UpdPill QLabel{{color:{t.blue};background:transparent;}}"
            )
        self.setVisible(visible)

    # ------------------------------------------------------------ 数据入口

    def apply_state(self, s: dict | None) -> None:
        """线程拉回的 /api/update 真值落地（主线程）。None = 拉取失败 = 隐藏。"""
        self._cur = s if isinstance(s, dict) else None
        if self._polling:
            return # 更新作业轮询中，主态机不抢（web 同款）
        self._render_main()

    def _render_main(self) -> None:
        """主态机：胶囊可见性/文案 + 面板正文。"""
        cur = self._cur
        st = str((cur or {}).get("status") or "")
        theirs = str((cur or {}).get("theirs") or "")
        if cur is None or (self._dismissed == (st, theirs) and st != ""):
            self._style(warn=False, visible=False) # 同版本「稍后」过了 → 本会话不再弹
            self._fill_pop()
            return
        p = pill(cur)
        if p is None:
            self._style(warn=False, visible=False)
        else:
            self.txt.setText(p[0])
            self._style(warn=p[1], visible=True)
        self._fill_pop()

    def _fill_pop(self) -> None:
        """面板正文/notes/按钮态（胶囊数据同源）。正文用短版 —— notes 逐条
        明细由独立区域负责，不与 decide 全文的「· 连接」段重复。"""
        cur = self._cur
        st = str((cur or {}).get("status") or "")
        if st == "newer":
            text = "有新版本 %s（当前 %s）" % (cur.get("theirs") or "？",
                                              cur.get("mine") or "未记录")
        else:
            text, _warn = decide(cur)
        self.detail.setText(text or "没有待处理的更新事项。")
        self.notes.setText(_notes_text(cur))
        self.notes.setVisible(bool(self.notes.text()))
        self.btn_skip.setEnabled(st == "newer") # 只有真有新版才允许消音
        self.btn_skip.setToolTip(
            "" if st == "newer" else "只有「确实有新版本」时才能不再提醒（半装状态下按了会把提醒永久消音）")

    # ------------------------------------------------------------ 三按钮

    def _on_later(self) -> None:
        st = str((self._cur or {}).get("status") or "")
        theirs = str((self._cur or {}).get("theirs") or "")
        self._dismissed = (st, theirs)
        self.pop.close()
        self._style(warn=False, visible=False)

    def _on_skip(self) -> None:
        cur = self._cur
        if not cur or str(cur.get("status") or "") != "newer" or not cur.get("theirs"):
            self._style(warn=False, visible=False) # web 同款守卫（按钮已禁用，双保险）
            return
        ver = str(cur.get("theirs"))
        box: dict = {"done": False}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415

            post_json("/api/update_skip", {"version": ver})
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="upd-skip").start()

        def _poll() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _poll)
                return
            self.pop.close()
            self._style(warn=False, visible=False) # web .then(hide).catch(hide)

        QTimer.singleShot(150, _poll)

    def _on_reset(self) -> None:
        """「更新闸门卡死」出口（web updReset console_html.py:794-807）：被镜像
        改过的清单会把本机记的「见过的最高版本」顶上天，此后真版本全判回滚
        ⇒ 这个按钮只清这一个键；结果如实回显，绝不假装成功。"""
        d = ConfirmDialog(
            self.t,
            self.window(),
            "重置更新状态？",
            "只会清掉本机记的「见过的最高版本」（更新状态快照里的一个键），别的什么都不动。",
            ["什么时候用：明明有新版本、它却说「更新源给的版本更旧 ⇒ 判为回滚」"],
            confirm_label="重置",
            dangerous=False,
        )
        d.exec()
        if not d.result_ok:
            return
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415

            try:
                box["r"] = post_json("/api/update_reset", {}, timeout=15.0)
            except Exception as e: # noqa: BLE001
                box["r"] = {"ok": False, "error": str(e)}
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="upd-reset").start()

        def _poll() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _poll)
                return
            r = box.get("r") or {}
            if r.get("ok"):
                self.detail.setText("已重置（原记录 %s ⇒ 现在 %s）"
                                    % (r.get("before") or "无", r.get("after") or "清空"))
            else:
                self.detail.setText("重置失败：%s"
                                    % (r.get("why") or r.get("error") or "未说明"))

        QTimer.singleShot(150, _poll)

    def _on_go(self) -> None:
        cur = self._cur or {}
        ver = str(cur.get("theirs") or "新版本")
        d = ConfirmDialog(
            self.t,
            self.window(),
            "确认更新",
            "现在就更新到 %s？" % ver,
            [
                "会从更新源下载整包、校验文件树哈希后替换本体文件",
                "data/、config.json、日志和你的聊天记录一概不碰",
                "装好后自动重启，这个窗口会自动重连",
            ],
            confirm_label="更新",
            cancel_label="算了",
            dangerous=False,
        )
        d.exec()
        if not d.result_ok:
            return # 可打断/取消，后台零影响
        self._set_progress("正在更新到 %s：准备中…" % ver)
        self._style(warn=False, visible=True)
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415

            box["r"] = post_json("/api/update_apply", {})
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="upd-apply").start()

        def _poll() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _poll)
                return
            r = box["r"]
            if not isinstance(r, dict) or r.get("ok") is False:
                why = r.get("why") if isinstance(r, dict) else None
                self._set_progress("更新启动失败：%s" % (why or "没连上后台（更新没有开始）"))
                self._style(warn=True, visible=True)
                return
            self._polling = True
            self._poll_iv.start()

        QTimer.singleShot(150, _poll)

    # ------------------------------------------------------------ 进度双写（胶囊 + 面板）

    def _set_progress(self, text: str, warn: bool = False) -> None:
        """更新中的状态同时刷到胶囊（收着面板也看得见）与面板进度行。"""
        self.txt.setText(text)
        self._style(warn=warn, visible=True)
        self.prog.setText(text)
        self.prog.setVisible(bool(text))

    # ------------------------------------------------------------ 更新作业轮询（web pollJob L887-913）

    def _poll_once(self) -> None:
        """轮询一跳：拉 /api/update 看 job（web pollJob 同款）。线程 + 主线程落地。"""
        box: dict = {"done": False, "val": None}

        def _work() -> None:
            from config_io import get_json # noqa: PLC0415

            box["val"] = get_json("/api/update", timeout=8.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="upd-poll").start()

        def _apply() -> None:
            if not box["done"]:
                return # 还没回来 → 下个 tick（900ms）再收
            s = box["val"]
            if not isinstance(s, dict):
                return # 拉取失败：跳过本轮（web catch return）
            j = s.get("job") or {}
            state = str(j.get("state") or "")
            if state == "running":
                self._render_running(j)
                return
            self._poll_iv.stop()
            self._polling = False
            if state == "done":
                self._set_progress((j.get("msg") or "更新完成")
                                   + " · 正在重启，页面稍后会自己连回来")

                def _rs() -> None:
                    from agent_bridge import post_api # noqa: PLC0415

                    post_api("/api/restart")

                threading.Thread(target=_rs, daemon=True, name="upd-restart").start()
            elif state == "error":
                self._set_progress("更新失败：%s（可以再点一次「立即更新」重试）"
                                   % (j.get("why") or "未知原因"), warn=True)
            else:
                self._set_progress("更新失败：后端没给作业状态"
                                   + (("（%s）" % j.get("why")) if j.get("why") else "")
                                   + "，请重启控制台后重试", warn=True)

        QTimer.singleShot(150, _apply)

    def _render_running(self, j: dict) -> None:
        ver = str((self._cur or {}).get("theirs") or j.get("version") or "新版本")
        phase = str(j.get("phase") or "")
        if phase == "download":
            total = j.get("total") or 0
            got = j.get("got") or 0
            if total:
                body = "%d%%（%s / %s）" % (int(got * 100 / max(1, total)), _mb(got), _mb(total))
            else:
                body = _mb(got)
            self._set_progress("正在下载 %s：%s%s" % (j.get("version") or ver, body,
                                                     (" · %s" % j.get("why")) if j.get("why") else ""))
        elif phase == "verify":
            self._set_progress("正在校验下载内容（文件树哈希）…")
        else:
            self._set_progress("正在更新到 %s：%s" % (ver, j.get("why") or "检查更新源…"))
