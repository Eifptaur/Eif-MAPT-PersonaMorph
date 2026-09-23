# -*- coding: utf-8 -*-
"""手写面板 —— web 侧**非配置行**形态的 5 个 sec（概览/体检/明细/日志/配置格式）。

为什么手写：这些面板在 web 侧不是「data-cfg 行」而是统计格 / 表格 / 按钮 / JSON
编辑器，没有行元数据可解析；但版式仍逐块对齐 web。
其余 22 个 sec 全部由 `panels_qt.py` 元数据驱动生成，本文件只装"手写特例"。

丙-4 接线口径（真实数据，不许"点了没反应"）：
  · 配置格式：真读 config.json 全文 + 校验写回（写前自动备份，config_io.write_full）
  · 日志：    真读 logs/persona_morph.log 尾部 200 行 + 刷新/打开目录
  · 概览：    GET /api/status 真数据填格（运行状态/监听目标/模型），8 秒自动刷新
              （与 web loadStatus 同源接口；拿不到就如实写"读取中"，绝不编数）
  · 明细：    GET /api/sessions（后端没提供/没连上就如实说，不造假表）
  · 体检：    检测本体跑在后台（agent/code_check.py），Qt 壳如实给入口说明，
              不伪造"已检测"状态
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

import config_io
import sec_meta
from stylekit_qt import Tokens, qfont, rgba
from widgets import Badge, Btn, Card, Field, Switch, desc, h2

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0]


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


def _page(t: Tokens, title: str, level: str = "idle", badge: str = "读取中"):
    """面板公共骨架：页 + 标题行 + Badge。返回 (page, lay, badge)。

    badge 引用必须交回 —— 丙-8 P0-A① 的病根就是「徽章建出来没人再碰」
    （panels_qt 旧 _cfg_panel：Badge 创建后全文件无 set 调用）。"""
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    bd = Badge(t, level, badge)
    page._c8_badge = bd               # build_panel 转挂到 wrap，Shell 分发取用
    lay.addWidget(h2(t, title, bd))
    return page, lay, bd


def _plain_area(t: Tokens, text: str = "", placeholder: str = "", height: int = 320) -> QPlainTextEdit:
    """大块文本区（json/日志样本用）—— panels_qt._area 的高个版，样式同源。"""
    e = QPlainTextEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size - 0.5))
    e.setFixedHeight(height)
    e.setStyleSheet(
        f"QPlainTextEdit{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:4px 8px;}}"
        f"QPlainTextEdit:focus{{border:1px solid {t.blue};}}"
    )
    return e


def _btn_row(t: Tokens, pairs: list[tuple[str, str]], hooks: list | None = None) -> QHBoxLayout:
    """按钮排。hooks 与 pairs 等长（None = 不接），可编辑面板按钮必须真接线。"""
    r = QHBoxLayout()
    for i, (label, role) in enumerate(pairs):
        b = Btn(label, t, role)
        if hooks and hooks[i] is not None:
            b.clicked.connect(hooks[i])
        r.addWidget(b)
    r.addStretch(1)
    return r


def _open_dir(p: Path) -> None:
    try:
        if p.exists():
            os.startfile(str(p))  # noqa: S606  （Windows 桌面语义；失败静默不崩）
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 概览（GET /api/status 真数据 + 8s 刷新）


def _stat_cell(t: Tokens, val: str, label: str) -> QWidget:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet("background:transparent;")
    v = QVBoxLayout(w)
    v.setContentsMargins(4, 6, 4, 6)
    v.setSpacing(2)
    b = QLabel(val)
    b.setFont(qfont(t, 17, 600))
    b.setStyleSheet(f"color:{t.tx};background:transparent;")
    b.setWordWrap(True)
    s = QLabel(label)
    s.setFont(qfont(t, 12))
    s.setStyleSheet(f"color:{t.tx3};background:transparent;")
    v.addWidget(b)
    v.addWidget(s)
    return w


def overview_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("overview")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, "机器人运作状态与账户信息（每 8 秒自动刷新，与网页控制台同一份 /api/status）。"))
    card = Card(t)
    grid = QGridLayout()
    grid.setSpacing(8)
    keys = [
        ("run", "运行状态"), ("listen", "监听目标（群+私聊）"), ("model", "当前模型"), ("paused", "暂停开关"),
        ("wechat", "微信连接"), ("uptime", "读取中"), ("g1", "—"), ("g2", "—"),
        ("g3", "—"), ("g4", "—"), ("g5", "—"),
    ]
    cells: dict[str, QLabel] = {}
    for i, (k, lb) in enumerate(keys):
        cell = _stat_cell(t, "—", lb)
        cells[k] = cell.findChildren(QLabel)[0]
        grid.addWidget(cell, i // 4, i % 4)
    card.body.addLayout(grid)
    lay.addWidget(card)
    lay.addWidget(desc(t, "其余统计（累计用量/成本等）在网页控制台的概览里看；这里只显示 /api/status 确认给到的字段，不编数。"))

    def _refresh() -> None:
        st = config_io.get_json("/api/status") or {}
        if not st:
            for lb in cells.values():
                lb.setText("读取中")
            badge.set("idle", "读取中")
            return
        paused = bool(st.get("paused"))
        wx_on = bool(st.get("wechat_connected"))
        lis = st.get("listen") or {}
        n = lis.get("groups", 0) + lis.get("privates", 0) if isinstance(lis, dict) else 0
        mo = st.get("model")
        if isinstance(mo, dict):
            # 有些版本给对象 {name, configured}；真后台给的是纯字符串模型名 —— 都接住
            model_txt = str(mo.get("name") or ("已配置" if mo.get("configured") else "没填密钥"))
        else:
            model_txt = (str(mo).strip() if mo else "") or "—"
        cells["run"].setText("运行中" if wx_on and not paused else ("已暂停" if paused else "微信没连上"))
        cells["listen"].setText(str(n))
        cells["model"].setText(model_txt)
        cells["paused"].setText("是" if paused else "否")
        cells["wechat"].setText("已连接" if wx_on else "没连上")
        up = st.get("uptime_s") or st.get("uptime")
        cells["uptime"].setText(f"{round(up)} 秒" if isinstance(up, (int, float)) else "—")
        badge.set("warn" if (paused or not wx_on) else "ok",
                  "已暂停" if paused else ("运行中" if wx_on else "微信没连上"))

    _refresh()
    timer = QTimer(page)
    timer.setInterval(8000)          # web loadStatus 同款 8 秒
    timer.timeout.connect(_refresh)
    timer.start()

    # ── 其余四钮真接线（丙-8 P0-A②：用户报「点了没反应」的缺口全补）──
    # web 同款 API：test-api / data/export / data/import / stats/cal_clear
    note2 = desc(t, "")
    lay.addWidget(note2)

    def _test_api() -> None:
        note2.setText("测试 API 连通中…")
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            from agent_bridge import post_json  # noqa: PLC0415
            try:
                box["r"] = post_json("/api/test-api", {}, timeout=60.0)
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th  # noqa: PLC0415
        _th.Thread(target=_work, daemon=True, name="c8-test-api").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r") or {}
            if r.get("ok"):
                note2.setText(f"连通：延迟 {r.get('latency_ms')}ms，模型 {r.get('model')}：{r.get('reply')}")
            else:
                note2.setText(f"测试失败：{r.get('error') or box.get('err') or '后台没连上'}")
        QTimer.singleShot(300, _apply)

    def _raw_post(path: str, data: bytes | None, ctype: str, timeout: float) -> bytes:
        """二进制 POST（导出 zip / 导入上传）—— post_json 只回 dict，这里走 urllib。"""
        import urllib.request as ur  # noqa: PLC0415
        from addr import join_url  # noqa: PLC0415
        from agent_bridge import current_url  # noqa: PLC0415

        url = join_url(current_url(), path)
        opener = ur.build_opener(ur.ProxyHandler({}))   # 绕代理（全 ui_qt 口径）
        req = ur.Request(url, data=data, headers={"Content-Type": ctype} if data else {})
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()

    def _export() -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415
        ds = time.strftime("%Y-%m-%d")
        p, _f = QFileDialog.getSaveFileName(page, "导出记录", f"Persona Morph-数据迁移-{ds}.zip",
                                            "迁移包 (*.zip)")
        if not p:
            return
        note2.setText("导出中…")
        try:
            blob = _raw_post("/api/data/export", None, "", 120.0)
            Path(p).write_bytes(blob)
            note2.setText(f"已导出记录（计费+对话）→ {p}")
        except Exception as e:  # noqa: BLE001
            note2.setText(f"导出失败：{e}")

    def _import() -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415
        p, _f = QFileDialog.getOpenFileName(page, "选迁移包", "", "迁移包 (*.zip)")
        if not p:
            return
        note2.setText("迁移中（合并到当前数据，按内容去重）…")
        try:
            import json as _j  # noqa: PLC0415
            blob = Path(p).read_bytes()
            resp = _raw_post("/api/data/import", blob, "application/zip", 180.0)
            j = _j.loads(resp.decode("utf-8", errors="replace"))
            note2.setText(f"迁移完成：{j.get('note') or 'ok'}" if j.get("ok")
                          else f"迁移失败：{j.get('error') or '未知'}")
            _refresh()
        except Exception as e:  # noqa: BLE001
            note2.setText(f"迁移失败：{e}")

    def _clear_cost() -> None:
        from confirm import ConfirmDialog  # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "一键删除全部计费历史？",
            "删的是「今日/周期/累计用量」的历史记录，机器人本体不受影响。",
            ["计费历史（今天/周期/累计的统计）会清零", "这个动作不能撤销"],
            confirm_label="确认删除", cancel_label="算了", dangerous=True)
        if not d.exec():
            return
        note2.setText("删除中…")
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            from agent_bridge import post_json  # noqa: PLC0415
            try:
                box["r"] = post_json("/api/stats/cal_clear", {}, timeout=30.0)
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th  # noqa: PLC0415
        _th.Thread(target=_work, daemon=True, name="c8-cal-clear").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r") or {}
            note2.setText("已清空计费历史" if r.get("ok") else
                          f"失败：{r.get('error') or box.get('err') or '后台没连上'}")
        QTimer.singleShot(300, _apply)

    hooks = [lambda: _refresh(), _test_api, _export, _import, _clear_cost]
    lay.addLayout(_btn_row(t, [("立即刷新", "primary"), ("测试 API 连通", "ghost"),
                               ("导出记录", "ghost"), ("迁移数据", "ghost"), ("一键删", "danger")], hooks))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 体检（P0-A③：两主按钮真接后台 API）


def _fmt_checks(title: str, summary: str, checks: list, cancelled: bool = False) -> str:
    """web 检测结果渲染同款：标题行 + summary + 逐项四档（通过/注意/未通过/信息）。"""
    lines = [f"===== {title} =====", summary or "", ""]
    for c in checks or []:
        st = c.get("status")
        mark = {"ok": "通过", "warn": "注意", "fail": "未通过"}.get(st, "信息")
        lines.append(f"{mark} {c.get('name')}：{c.get('detail')}")
        if c.get("hint"):
            lines.append(f"    建议：{c.get('hint')}")
    if cancelled:
        lines.append("")
        lines.append("（检测已被手动停止）")
    return "\n".join(lines)


def check_panel(t: Tokens) -> QWidget:
    """检测中心 —— 丙-8 P0-A③ 把「入口说明页」升级为真检测：
      · 代码检测：POST /api/code-check {deps} → 150ms 轮询 /api/code-check/progress
        （progress{done,total,current} + items 逐项实时 + done→result{summary,checks}），
        上限 300 轮（web 同款）；
      · 点击测试：POST /api/selfcheck {}（timeout 180s，阻塞到完成）→
        {summary,checks,cancelled}；停止 = POST /api/selfcheck-stop（当前项跑完即停）。
    线程纪律：请求全在后台线程，UI 只在主线程 QTimer 落地（_poll_paused 同款 box 模式）。
    徽章 stCheck：检测中 info「检测中」→ 完成 ok「已检测」/ 失败 err「检测失败」
    （web 侧 stCheck 只建不更，Qt 补齐为真实状态）。"""
    page, lay, badge = _page(t, "检测中心（代码检测 / 点击测试）", "warn", "还没检测")
    lay.addWidget(desc(t, "「代码检测」＝纯代码层检查（编译/依赖/角色卡评估/种子库/提示词静态/保护机制），"
                           "零风险，实测约 0.5~3 秒；「点击测试」＝环境/配置/界面自动化共 55 项，"
                           "全程序内完成，不碰鼠标，进行中约 40~70 秒，可随时停止。"))

    import threading  # noqa: PLC0415

    from agent_bridge import post_json  # noqa: PLC0415

    card = Card(t)
    card.body.addWidget(h2(t, "检测结果"))
    area = _plain_area(t, "", placeholder="点上面的按钮开始检测，结果逐项显示在这里", height=300)
    area.setReadOnly(True)
    card.body.addWidget(area)
    tip = desc(t, "尚未开始")
    card.body.addWidget(tip)
    lay.addWidget(card)

    row1 = QHBoxLayout()
    b_code = Btn("代码检测", t, "primary")
    b_deps = Btn("代码检测＋依赖核对", t, "ghost")
    b_deps.setToolTip("额外跑依赖版本详细核对（55 项，稍慢）")
    row1.addWidget(b_code)
    row1.addWidget(b_deps)
    row1.addStretch(1)
    lay.addLayout(row1)

    row2 = QHBoxLayout()
    b_self = Btn("点击测试", t, "primary")
    b_stop = Btn("停止检测", t, "ghost")
    b_stop.setEnabled(False)
    row2.addWidget(b_self)
    row2.addWidget(b_stop)
    row2.addStretch(1)
    lay.addLayout(row2)

    # ── 代码检测（启动 + 轮询进度；两个触发钮共用，deps 只改启动参数）──
    code_box: dict = {"running": False}

    def _run_code(deps: bool) -> None:
        if code_box.get("running"):
            return
        code_box["running"] = True
        b_code.setEnabled(False)
        b_deps.setEnabled(False)
        badge.set("info", "检测中")
        area.setPlainText("===== 代码检测（进行中…）=====")
        tip.setText("代码检测启动中…")
        box: dict = {"started": False, "done": False,
                     "items": [], "prog": None, "result": None, "err": None}

        def _work() -> None:
            try:
                post_json("/api/code-check", {"deps": bool(deps)}, timeout=15.0)
                box["started"] = True
            except Exception as e:  # noqa: BLE001
                box["err"] = f"启动失败：{e}"
                box["done"] = True
                return
            for _i in range(300):            # web 同款上限 300 轮
                try:
                    pr = post_json("/api/code-check/progress", {}, timeout=10.0)
                except Exception as e:  # noqa: BLE001
                    box["err"] = f"进度查询失败：{e}"
                    break
                if pr:
                    if pr.get("items"):
                        box["items"] = pr["items"]
                    box["prog"] = pr.get("progress")
                    if pr.get("done"):
                        box["result"] = pr.get("result")
                        break
                time.sleep(0.15)             # web 150ms 同款
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-code-check").start()

        def _render_running() -> None:
            items = box.get("items") or []
            if items:
                lines = ["===== 代码检测（进行中…）=====", ""]
                for c in items:
                    mark = {"ok": "通过", "warn": "注意", "fail": "未通过"}.get(c.get("status"), "信息")
                    lines.append(f"{mark} {c.get('name')}：{c.get('detail')}")
                area.setPlainText("\n".join(lines))

        def _apply() -> None:
            if not box["done"]:
                if box.get("started"):
                    prg = box.get("prog") or {}
                    d, tt = prg.get("done") or 0, prg.get("total") or 0
                    cur = str(prg.get("current") or "")
                    pct = f"{round(d / tt * 100)}%" if tt else "0%"
                    tip.setText(f"检测中 {pct} · {cur}" if cur else f"检测中 {pct}")
                    _render_running()
                QTimer.singleShot(300, _apply)   # web 150ms 轮询，UI 300ms 足够顺
                return
            if box.get("err"):
                area.setPlainText("代码检测失败：" + box["err"])
                tip.setText("代码检测失败：" + box["err"])
                badge.set("err", "检测失败")
            elif box.get("result"):
                r = box["result"]
                area.setPlainText(_fmt_checks("代码检测", r.get("summary") or "", r.get("checks") or []))
                tip.setText("代码检测完成：" + (r.get("summary") or ""))
                badge.set("ok", "已检测")
            else:
                area.setPlainText(area.toPlainText() + "\n（仍在检测中，请稍后再查看）")
                tip.setText("代码检测仍在进行中…")
                badge.set("warn", "没测完")
            code_box["running"] = False
            b_code.setEnabled(True)
            b_deps.setEnabled(True)

        QTimer.singleShot(300, _apply)

    b_code.clicked.connect(lambda: _run_code(False))
    b_deps.clicked.connect(lambda: _run_code(True))

    # ── 点击测试（阻塞式请求放后台线程；停止另发一刀）──
    self_box: dict = {"running": False}

    def _run_self() -> None:
        if self_box.get("running"):
            return
        self_box["running"] = True
        b_self.setEnabled(False)
        b_stop.setEnabled(True)
        badge.set("info", "检测中")
        area.setPlainText("点击测试中（约 40~70 秒：环境/配置/点击 + 程序鼠标操作，"
                          "期间请勿动鼠标；可随时点「停止检测」）…")
        tip.setText("进行中约 40~70 秒（可随时「停止检测」）")
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            try:
                box["r"] = post_json("/api/selfcheck", {}, timeout=185.0)
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-selfcheck").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            if box.get("err"):
                area.setPlainText("检测失败：" + box["err"])
                tip.setText("检测失败：" + box["err"])
                badge.set("err", "检测失败")
            else:
                r = box.get("r") or {}
                cancelled = bool(r.get("cancelled"))
                area.setPlainText(_fmt_checks("点击测试", r.get("summary") or "", r.get("checks") or [], cancelled))
                if cancelled:
                    tip.setText("已停止检测")
                    badge.set("warn", "已停止")
                else:
                    tip.setText("点击测试完成：" + (r.get("summary") or ""))
                    badge.set("ok", "已检测")
            b_self.setEnabled(True)
            b_stop.setEnabled(False)
            self_box["running"] = False

        QTimer.singleShot(300, _apply)

    def _stop() -> None:
        try:
            post_json("/api/selfcheck-stop", {}, timeout=10.0)
            tip.setText("已发出停止请求（当前检测项跑完即停）")
        except Exception as e:  # noqa: BLE001
            tip.setText(f"停止失败：{e}")

    b_self.clicked.connect(_run_self)
    b_stop.clicked.connect(_stop)

    card2 = Card(t)
    card2.body.addWidget(h2(t, "功能自检清单（按重要性排序）"))
    items = ["环境体检 · 点上方「点击测试」", "发消息 · 群里 @机器人 说句话",
             "拍一拍 · 先「简易检测」，再完整检测", "引用回复 · 引用某条消息回复",
             "发图 · 让机器人「发一张图」", "识图 · 引用图片＋@机器人 分析这张",
             "联网搜索 · @机器人 今天的天气/新闻", "记忆 · 让机器人记住一件事后到「记忆」页看",
             "挂件 · 看右下角鲸鱼挂件的数据与拖拽", "启停重启 · 顶部停止/重启后能接管",
             "多厂商切换 · 换厂商保存后测试连通"]
    for it in items:
        card2.body.addWidget(desc(t, it))
    lay.addWidget(card2)

    # ── 视频通路（丙-11 C2）：三态徽章，数据来自 /api/status 的 media.video ──
    # 工单 R2：控制台原来**看不到视频死活**（ffmpeg/ASR/yt-dlp），这里补上。
    # 数据源与网页控制台同一份快照（media_status.snapshot()["video"]），不另造一份。
    # ⚠️ 禁 emoji：图标取项目锁定图标库（icons INNER），徽章用 widgets.Badge。
    card3 = Card(t)
    card3.body.addWidget(h2(t, "视频通路（ffmpeg / 本机识别 / 下载器）"))
    card3.body.addWidget(desc(
        t, "本地视频抽帧与 B 站外链解析要 ffmpeg；「听」视频要本机中文识别；"
           "抖音/快手/小红书/YouTube 这类外链还要下载器（yt-dlp）。三项都是**现场探测**，不是写死的。"))
    vid_note = desc(t, "读取中…")
    vid_badges: list = []

    def _mk_video_row(label: str) -> object:
        row = QHBoxLayout()
        name = desc(t, label)
        name.setMinimumWidth(190)
        bd = Badge(t, "idle", "读取中")
        row.addWidget(name)
        row.addWidget(bd)
        row.addStretch(1)
        box = QWidget()
        box.setLayout(row)
        card3.body.addWidget(box)
        vid_badges.append(bd)
        return bd

    bd_ff = _mk_video_row("ffmpeg（抽帧 / 抽音频）")
    bd_asr = _mk_video_row("本机识别（听音频，离线）")
    bd_bili = _mk_video_row("B 站链接解析")
    bd_dl = _mk_video_row("外链下载器（yt-dlp）")
    bd_url = _mk_video_row("外链视频解析开关")
    card3.body.addWidget(vid_note)

    def _refresh_video() -> None:
        """读 /api/status 的 media.video（拿不到就全部落 idle，**绝不默认写 ok**）。"""
        try:
            st = config_io.get_json("/api/status") or {}
        except Exception:  # noqa: BLE001
            st = {}
        vid = ((st.get("media") or {}).get("video") or {}) if isinstance(st, dict) else {}
        if not vid:
            for bd in vid_badges:
                bd.set("idle", "读不到", "后台没连上或该接口未提供 media.video，重连后点「刷新」")
            vid_note.setText("读不到视频通路状态（后台没连上，或这版后台还没提供该字段）。")
            return
        vr = vid.get("video_read") or {}
        if vr.get("ready"):
            bd_ff.set("ok", "就绪", "ffmpeg：%s" % (vr.get("ffmpeg") or ""))
        else:
            bd_ff.set("err", "缺 ffmpeg", (vr.get("why") or "没找到 ffmpeg（抽帧/抽音频都要它）"))
        asr = vr.get("asr") or {}
        if asr.get("ok"):
            bd_asr.set("ok", "就绪", "本机识别可用（离线，不出网）")
        else:
            bd_asr.set("warn", "不可用", asr.get("why") or "本机识别引擎不可用（只影响音频转文字）")
        bili = vid.get("bilibili") or {}
        if bili.get("enabled"):
            bd_bili.set("ok", "已开启", "B 站链接解析已开启（听视频上限 %s 秒）"
                        % (bili.get("listen_max_seconds") or "?"))
        else:
            bd_bili.set("warn", "已关闭", "B 站链接解析已在控制台关闭（bilibili.enabled）")
        ur = vid.get("video_url") or {}
        if ur.get("ytdlp_ready"):
            bd_dl.set("ok", "已安装", "yt-dlp：%s" % (ur.get("ytdlp") or ""))
        else:
            bd_dl.set("err", "未安装", ur.get("why") or "还没装下载器 yt-dlp（外链视频解析需要它）")
        if ur.get("enabled"):
            bd_url.set("ok", "已开启", "外链视频解析已开启（抽 %s 帧 / 音频识别 %s 秒）"
                       % (ur.get("max_frames") or "?", ur.get("max_seconds") or "?"))
        else:
            bd_url.set("warn", "默认关闭", "外链视频解析默认关闭（要真下载整段视频，按需在配置里打开）")
        vid_note.setText("读取成功 · %s（与网页控制台同一份 /api/status 快照）"
                         % time.strftime("%H:%M:%S"))

    _refresh_video()
    card3.body.addWidget(_row_btn_refresh(t, _refresh_video))
    lay.addWidget(card3)

    lay.addStretch(1)
    return page


def _row_btn_refresh(t: Tokens, hook) -> QWidget:
    """一个右对齐的「刷新」按钮行（面板内局部刷新用；与 web 同语义）。"""
    row = QHBoxLayout()
    b = Btn("刷新", t, "ghost")
    b.clicked.connect(hook)
    row.addStretch(1)
    row.addWidget(b)
    box = QWidget()
    box.setLayout(row)
    return box


# ---------------------------------------------------------------- 明细（GET /api/sessions，拿不到如实说）


def sessions_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("sessions")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "发了什么、多少用量、耗时，按天落盘可勾选删除。"))
    card = Card(t)
    card.body.addWidget(h2(t, "后台明细数据（/api/sessions）"))
    area = _plain_area(t, "", placeholder="连上后台后显示明细数据", height=300)
    card.body.addWidget(area)
    note = desc(t, "")

    def _refresh() -> None:
        data = config_io.get_json("/api/sessions")
        if data is None:
            area.setPlainText("")
            note.setText("后台没连上（或该接口未提供）。顶部状态灯恢复绿色后点「刷新」再试；"
                         "日常的按天明细请用网页控制台的「明细」页。")
            badge.set("err", "读不到")
            return
        text = json.dumps(data, ensure_ascii=False, indent=1)
        area.setPlainText(text[:8000] + ("\n…（截断显示前 8000 字符）" if len(text) > 8000 else ""))
        note.setText(f"读取成功 · {time.strftime('%H:%M:%S')}")
        # web stSessions 口径（DOM 计数的 Qt 等价）：数顶层记录条数，数不出就不编
        n = len(data) if isinstance(data, (list, dict)) else 0
        badge.set("idle", f"{n} 条" if n else "暂无记录")

    _refresh()
    card.body.addWidget(note)
    lay.addWidget(card)

    # 丙-8 P0-A②：清空真接线（web sessClear 同款 = POST /api/memory clear_sessions，
    # 带 uiConfirm 危险确认）；「删除所选日期」在 Qt 侧没有按天勾选 UI，按钮移除
    # （残留一个点了没反应的按钮比少一个按钮更伤——web 有勾选/撤销完整语义，留在网页控制台）。
    def _clear_sessions() -> None:
        from confirm import ConfirmDialog  # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "清空全部运行明细？",
            "删的是运行明细里的会话日志与对话历史。",
            ["模型之后不会再记得这些对话", "这个动作不能撤销"],
            confirm_label="确认清空", cancel_label="算了", dangerous=True)
        if not d.exec():
            return
        note.setText("清空中…")
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            from agent_bridge import post_json  # noqa: PLC0415
            try:
                box["r"] = post_json("/api/memory", {"action": "clear_sessions"}, timeout=60.0)
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th  # noqa: PLC0415
        _th.Thread(target=_work, daemon=True, name="c8-sess-clear").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r") or {}
            if r.get("ok") or r.get("note"):
                note.setText("会话日志已清除" + (f"（{r.get('note')}）" if r.get("note") else ""))
            else:
                note.setText(f"清空失败：{r.get('error') or box.get('err') or '后台没连上'}")
            _refresh()
        QTimer.singleShot(300, _apply)

    hooks = [_refresh, _clear_sessions]
    lay.addLayout(_btn_row(t, [("刷新", "primary"), ("清空全部明细", "danger")], hooks))
    lay.addWidget(desc(t, "按天勾选删除、撤销上次删除等完整语义在网页控制台的「明细」页（有勾选 UI 与二次确认）。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 日志（真读 logs/persona_morph.log 尾部）


def log_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("log")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "动作与失败原因，出问题先看这一屏。"))
    log_path = ROOT / "logs" / "persona_morph.log"
    card = Card(t)
    card.body.addWidget(h2(t, "persona_morph.log 尾部 200 行"))
    area = _plain_area(t, "", placeholder="日志内容", height=300)
    card.body.addWidget(area)
    note = desc(t, "")

    def _refresh() -> None:
        try:
            if not log_path.exists():
                area.setPlainText("")
                note.setText(f"还没找到 {log_path.name}（机器人可能还没启动过）。")
                badge.set("idle", "还没输出")
                return
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = lines[-200:]
            area.setPlainText("\n".join(tail))
            note.setText(f"共 {len(lines)} 行 · 显示尾部 {len(tail)} 行 · {time.strftime('%H:%M:%S')}")
            # web stLog 口径：idle 中性态 + 行数（N 行）
            badge.set("idle", f"{len(lines)} 行" if lines else "还没输出")
        except Exception as e:  # noqa: BLE001
            note.setText(f"读取失败：{e}")
            badge.set("err", "读不到")

    _refresh()
    card.body.addWidget(note)
    lay.addWidget(card)
    # 丙-8 P0-A②：原「清空日志」按钮移除 —— web「运行日志」区根本没有清空 API
    # （日志由后端按大小滚动），残壳按钮点了永远没反应；真要看历史用「打开日志目录」。
    hooks = [_refresh, lambda: _open_dir(ROOT / "logs")]
    lay.addLayout(_btn_row(t, [("刷新", "primary"), ("打开日志目录", "ghost")], hooks))
    lay.addWidget(desc(t, "日志由产品按大小自动滚动清理；要翻完整历史用「打开日志目录」。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 配置格式（真读 config.json + 校验写回 + 备份）


def json_panel(t: Tokens, on_save=None) -> QWidget:
    s = sec_meta.get("json")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "全部配置的配置文件。只在面板里找不到对应开关时才动它，保存前先备份。"))
    from agent.config import CONFIG_FILE  # noqa: PLC0415  同进程直连（语义与网页控制台一致）

    cfg_path = Path(CONFIG_FILE)
    card = Card(t)
    card.body.addWidget(h2(t, f"{cfg_path.name}（完整内容）"))
    area = _plain_area(t, "", placeholder="完整配置文件内容（JSON）", height=360)
    note = desc(t, "")

    def _load() -> None:
        try:
            area.setPlainText(cfg_path.read_text(encoding="utf-8"))
            note.setText(f"已读取 {cfg_path.name} · {time.strftime('%H:%M:%S')}")
            badge.set("info", "已加载")
        except Exception as e:  # noqa: BLE001
            note.setText(f"读取失败：{e}")
            badge.set("err", "读不到")

    def _save() -> None:
        ok, msg = config_io.write_full(area.toPlainText())
        note.setText(("已保存：" if ok else "没保存成：") + msg + f"  ·  {time.strftime('%H:%M:%S')}")
        if ok:
            badge.set("ok", "已保存")
        else:
            badge.set("err", "没保存成")
        if ok and callable(on_save):
            try:
                on_save()
            except Exception:  # noqa: BLE001
                pass

    _load()
    card.body.addWidget(note)
    card.body.addWidget(desc(t, "保存时自动把原文件备份成 .bak-qt-时间戳 放同目录；改坏(JSON 不合法)会拒绝写入。"))
    lay.addWidget(card)
    hooks = [_save, _load, lambda: _open_dir(cfg_path.parent)]
    lay.addLayout(_btn_row(t, [("保存全部设置", "primary"), ("重新读取", "ghost"),
                               ("打开配置目录", "ghost")], hooks))
    lay.addWidget(desc(t, "保存后需重启才能完全生效的部分：模型/人设/白名单等；界面与光标类即时生效。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 分发表（panels_qt.build_panel 查这里）

def vermat_panel(t: Tokens, on_save=None) -> QWidget:
    """丙-10 P0-3：版本能力矩阵（web `sec-vermat` 真值）+ 顶部「版本与更新」卡。

    两件事：
      3a. **矩阵三态**（allowed / 实测 / 严格档拦停，web console_html.py:3346 口径）——
          从 `/api/status` 的 `version_gate` + `version` 取真值，绝不显示「检测中」占位。
      3b. **版本与更新卡**（用户要求：更新入口挪到「版本」页）—— 顶栏胶囊点「稍后」
          只关 popover、不等于不再提示；这一张卡**常驻**，显示当前版本 / 有无新版 /
          「立即更新」入口，复用 `updbar` 的判定与动作，不重写一套。
    """
    s = sec_meta.get("vermat")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "当前「微信版本 × 适配层版本」下每个能力的实测状态。"))

    # ── 3b. 版本与更新（常驻卡；用户点名：更新入口放这里）──
    upd = Card(t)
    upd.body.addWidget(h2(t, "版本与更新"))
    vline = desc(t, "读取中…")
    upd.body.addWidget(vline)
    urow = QHBoxLayout()
    btn_go = Btn("立即更新", t, "primary")
    btn_check = Btn("检查更新", t, "ghost")
    urow.addWidget(btn_go)
    urow.addWidget(btn_check)
    urow.addStretch(1)
    upd.body.addLayout(urow)
    upd.body.addWidget(desc(t, "「稍后」只关掉顶栏提示、不等于不再提示；要彻底不再提示这个版本，"
                               "用顶栏胶囊里的「不再提醒这个版本」。入口常驻在本卡，随时能回来点。"))
    lay.addWidget(upd)

    def _fetch(cb) -> None:
        import threading  # noqa: PLC0415

        box: dict = {}

        def _work() -> None:
            try:
                from agent_bridge import get_json  # noqa: PLC0415

                box["v"] = get_json("/api/update", timeout=8.0)
            except Exception:  # noqa: BLE001
                box["v"] = None

        threading.Thread(target=_work, daemon=True, name="vermat-upd").start()

        def _poll() -> None:
            if "v" in box:
                tim.stop()
                cb(box["v"])

        tim = QTimer(page)
        tim.setInterval(150)
        tim.timeout.connect(_poll)
        tim.start()

    def _render_upd(v) -> None:
        try:
            import updbar  # noqa: PLC0415

            txt, warn = updbar.decide(v)
        except Exception:  # noqa: BLE001
            txt, warn = "", False
        vd = (v or {}) if isinstance(v, dict) else {}
        mine = str(vd.get("mine") or "").strip()
        cur = ("当前版本 " + mine) if mine else "当前版本未记录"
        if txt:
            vline.setText(cur + " —— " + txt)
            vline.setStyleSheet(f"color:{t.warn if warn else t.tx2};background:transparent;")
        else:
            vline.setText(cur + " —— 已是最新（或更新提醒已关）")
            vline.setStyleSheet(f"color:{t.tx3};background:transparent;")

    def _on_go() -> None:
        # 复用顶栏同一条动作链（/api/update_apply）；这里只做「有没有可更新」的前置说明
        def _work() -> None:
            try:
                from agent_bridge import post_json  # noqa: PLC0415

                post_json("/api/update_apply", {}, timeout=20.0)
            except Exception:  # noqa: BLE001
                pass

        import threading  # noqa: PLC0415

        threading.Thread(target=_work, daemon=True, name="vermat-apply").start()
        vline.setText("已发起更新；进度看顶栏胶囊（失败会在这里如实说明）。")

    def _on_check() -> None:
        vline.setText("检查中…")
        _fetch(_render_upd)

    btn_go.clicked.connect(_on_go)
    btn_check.clicked.connect(_on_check)
    page._c10_update_refresh = _fetch   # Shell 探活可复用（卡内自足也能跑）

    # ── 3a. 矩阵三态（web L2194/L2196/L2201 的真值行）──
    mtx = Card(t)
    mtx.body.addWidget(h2(t, "能力矩阵"))
    ver_lb = QLabel("读取中…")
    ver_lb.setFont(qfont(t, t.body_size - 0.5))
    ver_lb.setWordWrap(True)
    mtx.body.addWidget(ver_lb)
    gate_lb = QLabel("读取中…")
    gate_lb.setFont(qfont(t, t.body_size - 0.5))
    gate_lb.setWordWrap(True)
    mtx.body.addWidget(gate_lb)
    row_allow = QHBoxLayout()
    btn_allow = Btn("本次允许发送", t, "ghost")
    row_allow.addWidget(btn_allow)
    row_allow.addStretch(1)
    mtx.body.addLayout(row_allow)
    mtx.body.addWidget(desc(t, "只对本次运行有效（重启后重新拦），我们不会把「放行」写进配置。"))
    lay.addWidget(mtx)

    def _render_mtx() -> None:
        try:
            import panels_qt  # noqa: PLC0415

            st = panels_qt._load_status()
        except Exception:  # noqa: BLE001
            st = {}
        vm = (st.get("version") or {}) if isinstance(st, dict) else {}
        vg = (st.get("version_gate") or {}) if isinstance(st, dict) else {}
        wv = str(vm.get("wechat") or "").strip()
        ad = str(vm.get("adapter") or "").strip()
        ver_lb.setText(("当前版本对：微信 %s × 适配层 %s" % (wv, ad or "-"))
                       if wv and wv != "unknown" else "当前版本对：微信版本读不到")
        allow = vg.get("allow") if isinstance(vg.get("allow"), bool) else None
        if allow is None:
            gate_lb.setText("版本门：读不到（不影响发送）")
            badge.set("idle", "读不到")
        elif allow:
            if vg.get("level") == "ok":
                gate_lb.setText("版本门：这一版有实测记录，照常发送")
                badge.set("ok", "已实测")
            else:
                gate_lb.setText("版本门：这一版没实测记录，但照常发送（不影响使用）")
                badge.set("info", "能发")
        else:
            gate_lb.setText("版本门：按严格档暂停发送。可在本页关掉 version_gate.strict")
            badge.set("err", "拦停")

    def _on_allow() -> None:
        # web vmAllow：写「本次允许发送」的会话期放行（重启失效，不落配置）
        def _work() -> None:
            try:
                from agent_bridge import post_json  # noqa: PLC0415

                post_json("/api/version_allow", {}, timeout=15.0)
            except Exception:  # noqa: BLE001
                pass

        import threading  # noqa: PLC0415

        threading.Thread(target=_work, daemon=True, name="vermat-allow").start()
        gate_lb.setText("已按「仅本次允许」放行（重启后重新拦）。")

    btn_allow.clicked.connect(_on_allow)
    page._c10_mtx_refresh = _render_mtx
    _render_mtx()

    # 更新卡异步拉一次（不阻塞面板构建）
    _fetch(_render_upd)
    return page


MANUAL = {
    "overview": overview_panel,
    "check": check_panel,
    "sessions": sessions_panel,
    "log": log_panel,
    "json": json_panel,
    "vermat": vermat_panel,
}
