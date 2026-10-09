# -*- coding: utf-8 -*-
"""更新公告条 —— web `#updBar`（assets/console/index.html L759-792）的 Qt 复刻。

**形态**：顶栏**下方**一条通栏 —— 上行「文本 ＋ 四个按钮」
（立即更新 primary／稍后／不再提醒这个版本／重置更新状态），下行明细（有内容才占位）。
按钮**内联在条里**，没有弹层。

  · 常态：**没有待处理事项就整条不出现**（web 侧 `display:none`）
  · 有新版：`有新版本 {theirs}（当前 {mine}）`
  · warn 态：`更新源异常` / `上次更新只装了一半`（只换描边与字色）
  · 更新中：进度态就地刷（download % / verify / done→重启 / error→可重试）
  · API 链路沿用 /api/update /api/update_apply /api/update_skip

「稍后」的会话抑制语义对齐 web：同版本不再打扰，出**新**版本照常弹。

⚠️ **形态沿革（别再往回走）**：最早把四个按钮塞进 60px 顶栏 —— 横向空间不够，
会被挤成"每行一两个字"；后来改成"顶栏胶囊 ＋ 下滑 popover"（要两次点击才看得到按钮）；
现在回到 web 的原形态：**顶栏下方独立一条 ＋ 内联按钮**。
"""

from __future__ import annotations
from agent import version as _ver

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from async_ui import run_async, run_bg
from confirm import ConfirmDialog
from stylekit_qt import Tokens, qfont, rgba
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
        text = "有新版本 %s（当前 %s）%s" % (_ver.tag(s.get("theirs")), _ver.tag(s.get("mine")) or "未记录", n)
    elif st == "older":
        text = "更新源里的版本（%s）比本机旧，可能是源配错了" % _ver.tag(s.get("theirs"))
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
        return "有新版本 %s" % _ver.tag(s.get("theirs")), False
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
    """顶栏**下方**的更新公告条（web `#updBar`）。数据入口 `apply_state(s)`；动作自含。

    形态＝通栏一条（文本 ＋ 内联按钮 ＋ 可选明细行），**不是**顶栏里的胶囊。
    """

    def __init__(self, t: Tokens, parent=None):
        super().__init__(parent)
        self.t = t
        self.setObjectName("UpdBar")
        self._cur: dict | None = None # 最近一次 /api/update 真值
        self._dismissed: tuple | None = None # 「稍后」会话抑制（status, theirs）
        self._polling = False # 更新作业轮询中（主态机让位）
        self._poll_busy = False # 上一跳还没回来 ⇒ 这一跳跳过（别把请求叠起来）
        self._poll_iv = QTimer(self)
        self._poll_iv.setInterval(900) # web setInterval(pollJob, 900)
        self._poll_iv.timeout.connect(self._poll_once)

        # ── 形态：web `#updBar`（assets/console/index.html L759-766）**同构** ──
        #   一条通栏：上行「文本 ＋ 按钮组」，下行明细（有内容才显示）。
        #   ⚠️ 它不是顶栏里的胶囊：四个按钮塞进顶栏会被挤成"每行一两个字"，
        #      web 自己也是把它放在**顶栏下方独立一行**。
        # ── 版式：**左右两栏**（左＝文本＋明细，右＝按钮组）──
        # ⚠️ 按钮不能和文本同处一个 QHBoxLayout：那样它们只相对**文本那一行**居中，
        #    一旦有明细行，整条变高、按钮却吊在上半 ⇒ 看着"没在中间"。
        #    拆成两栏后，按钮组由外层 HBox 相对**整条高度**居中，多一行明细也不跑偏。
        outer = QHBoxLayout(self)
        outer.setContentsMargins(15, 12, 14, 12)
        outer.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.txt = QLabel("")
        self.txt.setObjectName("UpdMain")
        self.txt.setFont(qfont(t, 13))
        self.txt.setWordWrap(True)
        self.txt.setStyleSheet("background:transparent;")
        left.addWidget(self.txt)

        self.notes = QLabel("")
        self.notes.setFont(qfont(t, 12))
        self.notes.setWordWrap(True)
        self.notes.hide() # 明细：有内容才占位
        left.addWidget(self.notes)
        outer.addLayout(left, 1)

        right = QHBoxLayout()
        right.setSpacing(10)
        self.prog = QLabel("")
        self.prog.setFont(qfont(t, 12))
        self.prog.setStyleSheet("background:transparent;")
        self.prog.hide()
        right.addWidget(self.prog)

        self.btn_go = Btn("立即更新", t, "primary")
        self.btn_go.clicked.connect(self._on_go)
        right.addWidget(self.btn_go)
        self.btn_later = Btn("稍后", t, "ghost")
        self.btn_later.clicked.connect(self._on_later)
        right.addWidget(self.btn_later)
        self.btn_skip = Btn("不再提醒这个版本", t, "ghost")
        self.btn_skip.clicked.connect(self._on_skip)
        right.addWidget(self.btn_skip)
        self.btn_reset = Btn("重置更新状态", t, "ghost")
        self.btn_reset.setToolTip("更新闸门卡死时的出口：只清本机记的「见过的最高版本」")
        self.btn_reset.clicked.connect(self._on_reset)
        right.addWidget(self.btn_reset)
        outer.addLayout(right)

        self._style(warn=False, visible=False)

    # ------------------------------------------------------------ 交互

    def close_pop(self) -> None:
        """保留的空实现（外部按老契约还会调它）。

        改版后更新条**不再有弹出面板** —— web `#updBar` 本来就是"一条 + 四个内联按钮"，
        没有弹层，所以也没有需要收回的顶层窗。方法留着只为不破坏调用方。
        """
        return

    # ------------------------------------------------------------ 样式

    def _style(self, warn: bool, visible: bool) -> None:
        """外观与**常驻公告**（`shell._build_notice` 的 `#fbNotice`，web `#noticeBar`）**同一套**：
        左侧 3px 实色条 ＋ 2px 近直角 ＋「左浓右淡」的横向渐变底，**没有整块底色**。

        ⚠️ 别退回"实心矩形 ＋ 四边框"那一版：在底图上那是一块**沉重的板**，
           而公告条要的是"一条轻的提示" —— 靠左缘那道色条立住就够，不必把整块涂满。
        warn 态只换强调色（蓝 → 黄），版式不动。
        """
        t = self.t
        acc = t.q("warn") if warn else t.q("blue")
        self.setStyleSheet(
            f"QFrame#UpdBar{{"
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {rgba(acc, 26).name(QColor.NameFormat.HexArgb)},"
            f"stop:0.55 {rgba(acc, 6).name(QColor.NameFormat.HexArgb)},"
            f"stop:1 {rgba(acc, 0).name(QColor.NameFormat.HexArgb)});"
            f"border-left:3px solid {acc.name()};border-radius:2px;}}"
            # 主句走强调色（= 公告标题那一档），明细退回次要字色 ⇒ 两行之间自然分层
            f"QFrame#UpdBar QLabel{{color:{t.tx2};background:transparent;}}"
            f"QFrame#UpdBar QLabel#UpdMain{{color:{acc.name()};font-weight:600;}}"
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
            self._fill_notes()
            return
        p = pill(cur)
        if p is None:
            self._style(warn=False, visible=False)
        else:
            st = str((cur or {}).get("status") or "")
            if st == "newer":
                # 「有新版本」这一态要把"当前是哪个版本"一起说 —— 只报"有新版本"
                # 不了自己差多远（web 的 `updText` 也是这么写的）。
                self.txt.setText("有新版本 %s（当前 %s）" % (
                    _ver.tag(cur.get("theirs")), _ver.tag(cur.get("mine")) or "未记录"))
            else:
                self.txt.setText(p[0])
            self._style(warn=p[1], visible=True)
        self._fill_notes()

    def _fill_notes(self) -> None:
        """条内第二行（明细）＋ 按钮态，与主文本同源。

        明细为空就整行收起 —— 空标签占着一行会把条撑高，看着像"这里本该有东西"。
        """
        cur = self._cur
        st = str((cur or {}).get("status") or "")
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
        self._style(warn=False, visible=False)

    def _on_skip(self) -> None:
        cur = self._cur
        if not cur or str(cur.get("status") or "") != "newer" or not cur.get("theirs"):
            self._style(warn=False, visible=False) # web 同款守卫（按钮已禁用，双保险）
            return
        ver = str(cur.get("theirs"))

        def _work(box: dict) -> None:
            from agent_bridge import post_json # noqa: PLC0415

            post_json("/api/update_skip", {"version": ver})

        def _apply(box: dict) -> None:
            self._style(warn=False, visible=False) # web .then(hide).catch(hide)

        run_async(_work, _apply, page=self, interval=150, name="upd-skip")

    def _on_reset(self) -> None:
        """「更新闸门卡死」出口（web updReset assets/console/index.html:794-807）：被镜像
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

        def _work(box: dict) -> None:
            from agent_bridge import post_json # noqa: PLC0415

            try:
                box["r"] = post_json("/api/update_reset", {}, timeout=15.0)
            except Exception as e: # noqa: BLE001
                box["r"] = {"ok": False, "error": str(e)}

        def _apply(box: dict) -> None:
            r = box.get("r") or {}
            if r.get("ok"):
                self.txt.setText("已重置（原记录 %s ⇒ 现在 %s）"
                                    % (r.get("before") or "无", r.get("after") or "清空"))
            else:
                self.txt.setText("重置失败：%s"
                                    % (r.get("why") or r.get("error") or "未说明"))

        run_async(_work, _apply, page=self, interval=150, name="upd-reset")

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

        def _work(box: dict) -> None:
            from agent_bridge import post_json # noqa: PLC0415

            box["r"] = post_json("/api/update_apply", {})

        def _apply(box: dict) -> None:
            r = box.get("r")
            if not isinstance(r, dict) or r.get("ok") is False:
                why = r.get("why") if isinstance(r, dict) else None
                self._set_progress("更新启动失败：%s" % (why or "没连上后台（更新没有开始）"))
                self._style(warn=True, visible=True)
                return
            self._polling = True
            self._poll_iv.start()

        run_async(_work, _apply, page=self, interval=150, name="upd-apply")

    # ------------------------------------------------------------ 进度双写（胶囊 + 面板）

    def _set_progress(self, text: str, warn: bool = False) -> None:
        """更新中的状态同时刷到胶囊（收着面板也看得见）与面板进度行。"""
        self.txt.setText(text)
        self._style(warn=warn, visible=True)
        self.prog.setText(text)
        self.prog.setVisible(bool(text))

    # ------------------------------------------------------------ 更新作业轮询（web pollJob L887-913）

    def _poll_once(self) -> None:
        """轮询一跳：拉 /api/update 看 job（web pollJob 同款）。线程 + 主线程落地。

        驱动是外部那个 900ms 的 `_poll_iv`（不是自链）：**一跳一次请求**。
        加一个"上一跳没回来就跳过这一跳"的守卫 —— 原来的写法每 900ms 无条件起一条线程、
        150ms 没回来就把结果丢掉再起下一条，后端慢时最多叠 6 条在飞；守卫之后最多 1 条。
        """
        if self._poll_busy:
            return
        self._poll_busy = True

        def _work(box: dict) -> None:
            from config_io import get_json # noqa: PLC0415

            box["val"] = get_json("/api/update", timeout=8.0)

        def _apply(box: dict) -> None:
            self._poll_busy = False # 这一跳收摊（无论有没有拿到）
            s = box.get("val")
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

                run_bg(_rs, name="upd-restart")
            elif state == "need_kill":
                # 有文件被别的程序占用 ⇒ 自绘弹窗把"谁占用了什么"摆出来，问用户要不要临时关掉它。
                # 点「确定」⇒ 带 allow_kill + PID 重跑（后端先关占用者、再继续装）；「取消」⇒ 什么都不做。
                self._ask_kill(j)
            elif state == "error":
                self._set_progress("更新失败：%s（可以再点一次「立即更新」重试）"
                                   % (j.get("why") or "未知原因"), warn=True)
            else:
                self._set_progress("更新失败：后端没给作业状态"
                                   + (("（%s）" % j.get("why")) if j.get("why") else "")
                                   + "，请重启控制台后重试", warn=True)

        # 下一次 900ms tick 会再来一遍；这里的轮询只为把这一次的结果收回来
        run_async(_work, _apply, page=self, interval=150, name="upd-poll")

    def _ask_kill(self, j: dict) -> None:
        """「有程序正占用要更新的文件」——自绘弹窗（确定 / 取消），0 系统 MessageBox。

        列出 `lockedBy` 里的「占用者（PID）占用 了哪个文件」，问用户是否临时关掉它。
        同意 ⇒ `POST /api/update_apply {allow_kill:true, killed:[pid…]}`，重新起轮询；
        拒绝 ⇒ 如实说"文件没有任何改动"（此时后端一件都没换）。
        """
        lk = j.get("lockedBy") or {}
        procs = list(j.get("procNames") or [])
        lines, pids = [], []
        for rel, info in (lk or {}).items():
            for u in ((info or {}).get("users") or []):
                pid = u.get("pid")
                if pid and pid not in pids:
                    pids.append(pid)
                lines.append("· %s（PID %s）占用了 %s"
                             % (u.get("name") or "未知程序", pid or "?", rel))
        if not lines:
            lines.append("· 查不到具体是哪个程序占用的（可能是系统进程）")
        lines.append("同意后会临时关掉它，更新完自动重启；你的其它文件与数据一概不动。")
        d = ConfirmDialog(
            self.t, self.window(),
            "有程序正占用要更新的文件",
            "更新要替换的文件，现在被下面这些程序占用：",
            lines,
            confirm_label="关掉它并继续",
            cancel_label="取消",
            dangerous=False,
        )
        d.exec()
        if not d.result_ok:
            self._set_progress("已取消更新（文件没有任何改动）。想更新时再点一次「立即更新」。")
            return
        self._set_progress("正在关掉占用者并继续更新…")

        def _work(box: dict) -> None:
            from agent_bridge import post_json # noqa: PLC0415

            box["r"] = post_json("/api/update_apply", {"allow_kill": True, "killed": pids})

        def _apply(box: dict) -> None:
            r = box.get("r")
            if not isinstance(r, dict) or r.get("ok") is False:
                why = r.get("why") if isinstance(r, dict) else None
                self._set_progress("继续更新失败：%s" % (why or "没连上后台"))
                self._style(warn=True, visible=True)
                return
            self._polling = True
            self._poll_iv.start()

        run_async(_work, _apply, page=self, interval=150, name="upd-kill")

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
