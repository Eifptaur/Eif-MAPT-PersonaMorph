# -*- coding: utf-8 -*-
"""更新公告条 —— web updbar（agent/console_html.py L806-880）的 Qt 复刻。

丙-6 #13：拆掉 shell.py:473 的假条（写死「有新版可用 · 3 项改进」的静态
QLabel，无点击无真值，用户点了没下文）。本条**只说真话**：

  · 数据源  GET /api/update（走 config_io.get_json —— join_url 口径，丙-5 #0 教训：
            别手拼 base+path，token 会掉）
  · 四态机  pending（半装补换，warn）/ newer（有新版本）/ older（源配错，warn）/
            error（源异常，warn）/ 其余（off/current）与拉取失败 → **隐藏**
            —— 没有新版就不出条，绝不放占位（工单 P0 规则 3）
  · 快照    stateSaved===false / stateSaveError → 无论主态是什么都追加提示
            （web L833-837 同款：读数没写进快照，下次打开会回到旧记录）
  · 三按钮  立即更新 = ConfirmDialog 二次确认 → POST /api/update_apply →
            轮询 /api/update 的 job 显示进度（下载 % / 校验 / 完成→重启）；
            稍后 = 本会话不再打扰（同一版本再刷出来不重复弹）；
            不再提醒这个版本 = **仅 newer 允许**（V-R4-1：半装时 theirs=本机
            版本，按了会把提醒永久消音）→ POST /api/update_skip {"version": theirs}

「稍后」的会话抑制语义对齐 web：web hide 后页面不重载就不再现；Qt 壳没有
重载，用 (status, theirs) 记忆 —— 同版本不再打扰，出**新**版本照常弹。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from confirm import ConfirmDialog
from stylekit_qt import qfont, rgba
from widgets import Btn


def _mb(n) -> str:
    try:
        return "%.1f MB" % (float(n) / 1048576.0)
    except Exception:  # noqa: BLE001
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


class UpdateBar(QFrame):
    """顶栏真值更新条。数据入口 apply_state(s)；按钮动作自含（确认弹窗/POST/轮询）。"""

    def __init__(self, t: Tokens, parent=None):
        super().__init__(parent)
        self.t = t
        self.setObjectName("UpdBar")
        self._cur: dict | None = None          # 最近一次 /api/update 真值
        self._dismissed: tuple | None = None   # 「稍后」会话抑制（status, theirs）
        self._polling = False                  # 更新作业轮询中（主态机让位）
        self._poll_iv = QTimer(self)
        self._poll_iv.setInterval(900)         # web setInterval(pollJob, 900)
        self._poll_iv.timeout.connect(self._poll_once)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 8, 3)
        lay.setSpacing(8)
        self.txt = QLabel("")
        self.txt.setFont(qfont(t, 11, 500))
        self.txt.setStyleSheet("background:transparent;")
        self.txt.setMinimumWidth(120)
        lay.addWidget(self.txt, 1)
        self.btn_go = Btn("立即更新", t, "primary")
        self.btn_go.clicked.connect(self._on_go)
        lay.addWidget(self.btn_go)
        self.btn_later = Btn("稍后", t, "ghost")
        self.btn_later.clicked.connect(self._on_later)
        lay.addWidget(self.btn_later)
        self.btn_skip = Btn("不再提醒这个版本", t, "ghost")
        self.btn_skip.clicked.connect(self._on_skip)
        lay.addWidget(self.btn_skip)
        self.hide()
        self._style(warn=False, visible=False)

    # ------------------------------------------------------------ 样式

    def _style(self, warn: bool, visible: bool) -> None:
        t = self.t
        bd = rgba(t.q("warn"), 150) if warn else t.q("bd")
        card = rgba(t.q("warn"), 26) if warn else t.q("card")
        self.setStyleSheet(
            f"QFrame#UpdBar{{background:{card};border:1px solid {bd.name()};"
            f"border-radius:9px;}}"
            f"QFrame#UpdBar QLabel{{color:{t.tx};background:transparent;}}"
        )
        self.setVisible(visible)

    # ------------------------------------------------------------ 数据入口

    def apply_state(self, s: dict | None) -> None:
        """线程拉回的 /api/update 真值落地（主线程）。None = 拉取失败 = 隐藏。"""
        self._cur = s if isinstance(s, dict) else None
        if self._polling:
            return                              # 更新作业轮询中，主态机不抢（web 同款）
        if self._cur is None:
            self._style(warn=False, visible=False)
            return
        st = str(self._cur.get("status") or "")
        theirs = str(self._cur.get("theirs") or "")
        if self._dismissed == (st, theirs) and st != "":
            self._style(warn=False, visible=False)   # 同版本「稍后」过了 → 本会话不再弹
            return
        text, warn = decide(self._cur)
        self.txt.setText(text)
        self.txt.setToolTip(text if len(text) > 60 else "")
        self.btn_skip.setEnabled(st == "newer")      # V-R4-1：只有真有新版才允许消音
        self.btn_skip.setToolTip(
            "" if st == "newer" else "只有「确实有新版本」时才能不再提醒（半装状态下按了会把提醒永久消音）")
        self._style(warn=warn, visible=bool(text))

    # ------------------------------------------------------------ 三按钮

    def _on_later(self) -> None:
        st = str((self._cur or {}).get("status") or "")
        theirs = str((self._cur or {}).get("theirs") or "")
        self._dismissed = (st, theirs)
        self._style(warn=False, visible=False)

    def _on_skip(self) -> None:
        cur = self._cur
        if not cur or str(cur.get("status") or "") != "newer" or not cur.get("theirs"):
            self._style(warn=False, visible=False)   # web 同款守卫（按钮已禁用，双保险）
            return
        ver = str(cur.get("theirs"))
        box: dict = {"done": False}

        def _work() -> None:
            from agent_bridge import post_json  # noqa: PLC0415

            post_json("/api/update_skip", {"version": ver})
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="upd-skip").start()

        def _poll() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _poll)
                return
            self._style(warn=False, visible=False)   # web .then(hide).catch(hide)

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
            return                                   # 可打断/取消，后台零影响
        self.txt.setText("正在更新到 %s：准备中…" % ver)
        self._style(warn=False, visible=True)
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            from agent_bridge import post_json  # noqa: PLC0415

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
                self.txt.setText("更新启动失败：%s" % (why or "没连上后台（更新没有开始）"))
                self._style(warn=True, visible=True)
                return
            self._polling = True
            self._poll_iv.start()

        QTimer.singleShot(150, _poll)

    # ------------------------------------------------------------ 更新作业轮询（web pollJob L887-913）

    def _poll_once(self) -> None:
        """轮询一跳：拉 /api/update 看 job（web pollJob 同款）。线程 + 主线程落地。"""
        box: dict = {"done": False, "val": None}

        def _work() -> None:
            from config_io import get_json  # noqa: PLC0415

            box["val"] = get_json("/api/update", timeout=8.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="upd-poll").start()

        def _apply() -> None:
            if not box["done"]:
                return                                # 还没回来 → 下个 tick（900ms）再收
            s = box["val"]
            if not isinstance(s, dict):
                return                                # 拉取失败：跳过本轮（web catch return）
            j = s.get("job") or {}
            state = str(j.get("state") or "")
            if state == "running":
                self._render_running(j)
                return
            self._poll_iv.stop()
            self._polling = False
            if state == "done":
                self.txt.setText((j.get("msg") or "更新完成")
                                 + " · 正在重启，页面稍后会自己连回来")
                self._style(warn=False, visible=True)

                def _rs() -> None:
                    from agent_bridge import post_api  # noqa: PLC0415

                    post_api("/api/restart")

                threading.Thread(target=_rs, daemon=True, name="upd-restart").start()
            elif state == "error":
                self.txt.setText("更新失败：%s（可以再点一次「立即更新」重试）"
                                 % (j.get("why") or "未知原因"))
                self._style(warn=True, visible=True)
            else:
                self.txt.setText("更新失败：后端没给作业状态"
                                 + (("（%s）" % j.get("why")) if j.get("why") else "")
                                 + "，请重启控制台后重试")
                self._style(warn=True, visible=True)

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
            self.txt.setText("正在下载 %s：%s%s" % (j.get("version") or ver, body,
                                                    (" · %s" % j.get("why")) if j.get("why") else ""))
        elif phase == "verify":
            self.txt.setText("正在校验下载内容（文件树哈希）…")
        else:
            self.txt.setText("正在更新到 %s：%s" % (ver, j.get("why") or "检查更新源…"))
        self._style(warn=False, visible=True)
