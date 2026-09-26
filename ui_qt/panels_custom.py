# -*- coding: utf-8 -*-
"""手写面板 —— web 侧**非配置行**形态的 5 个 sec（概览/体检/明细/日志/配置格式）。

为什么手写：这些面板在 web 侧不是「data-cfg 行」而是统计格 / 表格 / 按钮 / JSON
编辑器，没有行元数据可解析；但版式仍逐块对齐 web。
其余 22 个 sec 全部由 `panels_qt.py` 元数据驱动生成，本文件只装"手写特例"。

 接线口径（真实数据，不许"点了没反应"）：
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
import re
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
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
    QListView,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidget,
)

import config_io
import sec_meta
from confirm import ConfirmDialog
from stylekit_qt import SHAPE_CIRCLE, Tokens, pill, qfont, rgba, status_colors
from widgets import Badge, Btn, Card, ElideLabel, Field, FlowBox, Switch, desc, h2, row_label

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0]


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


def _qt_alive(w) -> bool:
    """控件在 C++ 侧是否还活着。

    面板重建/关页后，注册进全局轮询的闭包仍持有旧控件（Python 侧活、C++ 侧已析构）。
    用 `shiboken6.isValid` 判活；不可用则退回「调个无害 getter 看抛不抛
    RuntimeError」。已死 ⇒ 调用方直接退出，不发起任何阻塞请求。
    """
    if w is None:
        return False
    try:
        import shiboken6 # noqa: PLC0415

        return bool(shiboken6.isValid(w))
    except Exception: # noqa: BLE001
        try:
            w.objectName()
            return True
        except RuntimeError:
            return False


def _page(t: Tokens, title: str, level: str = "idle", badge: str = "读取中"):
    """面板公共骨架：页 + 标题行 + Badge。返回 (page, lay, badge)。

    badge 引用必须交回 —— P0-A① 的病根就是「徽章建出来没人再碰」
    （panels_qt 旧 _cfg_panel：Badge 创建后全文件无 set 调用）。"""
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    bd = Badge(t, level, badge)
    page._c8_badge = bd # build_panel 转挂到 wrap，Shell 分发取用
    lay.addWidget(h2(t, title, bd))
    return page, lay, bd


def _plain_area(t: Tokens, text: str = "", placeholder: str = "", height: int = 320) -> QPlainTextEdit:
    """大块文本区（json/日志样本用）—— panels_qt._area 的高个版，样式同源。"""
    e = QPlainTextEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size - 0.5))
    e.setFixedHeight(height)
    e.setStyleSheet(
        f"QPlainTextEdit{{background:{_hex(rgba(t.q('tx'), 16))};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_field}px;padding:4px 8px;}}"
        f"QPlainTextEdit:focus{{border:1px solid {t.blue};}}"
    )
    return e


def _btn_row(t: Tokens, pairs: list[tuple[str, str]], hooks: list | None = None) -> FlowBox:
    """按钮排（自动换行）。hooks 与 pairs 等长（None = 不接），可编辑面板按钮必须真接线。

    返回 FlowBox 而非 QHBoxLayout：一行放不下就折行，窄窗下不会把按钮压到互相重叠
    （见 widgets.FlowBox 注解）。调用处写 `xxx.addWidget(_btn_row(...))`。
    """
    r = FlowBox(t, spacing=6)
    for i, (label, role) in enumerate(pairs):
        b = Btn(label, t, role)
        if hooks and hooks[i] is not None:
            b.clicked.connect(hooks[i])
        r.add(b)
    return r


def _open_dir(p: Path) -> None:
    try:
        if p.exists():
            os.startfile(str(p)) # noqa: S606  （Windows 桌面语义；失败静默不崩）
    except Exception: # noqa: BLE001
        pass


# ---------------------------------------------------------------- 概览（GET /api/status 真数据 + 8s 刷新）


def _stat_cell(t: Tokens, val: str, label: str) -> tuple[QWidget, QLabel, QLabel]:
    """统计格（大数字 + 下方小字）。返回 (格子, 大数字标签, 小字标签)。

    ⚠️ 小字标签也要交回：旧版只交回 `findChildren(QLabel)[0]`（大数字），
    那些以状态词命名的小字（如 uptime 格曾写死 "读取中"）就再没人改得动 ——
    大数字已经刷成「2465 秒」，下面小字永远停着「读取中」，正是用户截图上
    「大文字下面的小文字老是显示检测中」的那一类。
    """
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
    return w, b, s


def overview_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("overview")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, "机器人运作状态与账户信息（每 8 秒自动刷新，与网页控制台同一份 /api/status）。"))
    card = Card(t)
    grid = QGridLayout()
    grid.setSpacing(8)
    keys = [
        ("run", "运行状态"), ("listen", "监听目标（群+私聊）"), ("model", "当前模型"), ("paused", "暂停开关"),
        ("wechat", "微信连接"), ("uptime", "已运行时长"), ("g1", "群 1"), ("g2", "群 2"),
        ("g3", "群 3"), ("g4", "群 4"), ("g5", "群 5"),
    ]
    cells: dict[str, QLabel] = {}
    caps: dict[str, QLabel] = {}
    seen: dict = {"ok": False} # 是否至少成功读过一次 /api/status（区分「首次读取中」与「事后读不到」）
    for i, (k, lb) in enumerate(keys):
        cell, big, cap = _stat_cell(t, "—", lb)
        cells[k] = big
        caps[k] = cap
        grid.addWidget(cell, i // 4, i % 4)
    card.body.addLayout(grid)
    lay.addWidget(card)
    lay.addWidget(desc(t, "其余统计（累计用量/成本等）在网页控制台的概览里看；这里只显示 /api/status 确认给到的字段，不编数。"))

    # ── 监听群明细（web #group-table 同款：群名 | 监听/忽略，数据来自 /api/status.groups）──
    card_g = Card(t)
    card_g.body.addWidget(h2(t, "监听群明细"))
    gtable = QTableWidget(0, 2)
    gtable.setHorizontalHeaderLabels(["群名", "目标"])
    gtable.verticalHeader().setVisible(False)
    gtable.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    gtable.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    gtable.setFixedHeight(200)
    gtable.setStyleSheet(
        f"QTableWidget{{background:transparent;color:{t.tx};border:1px solid {t.bd};"
        f"border-radius:{t.radius_field}px;gridline-color:{t.bd};}}"
        f"QHeaderView::section{{background:transparent;color:{t.tx2};"
        f"border:none;border-bottom:1px solid {t.bd};padding:4px;}}")
    card_g.body.addWidget(gtable)
    lay.addWidget(card_g)

    # ── 费用计算器（web #feeCalc 同款：POST /api/prices 官方价目 · 全厂商分区）──
    card_f = Card(t)
    card_f.body.addWidget(h2(t, "费用计算器（官方价目 · 全厂商分区）"))
    card_f.body.addWidget(desc(
        t, "选厂商与模型自动带出官方单价；高峰=工作日 9:00-12:00 / 14:00-18:00（×2），"
           "周末/夜间空闲价；缓存命中按 cached 价。"))
    _FC_VENDORS = {"deepseek": "DeepSeek", "glm": "智谱 GLM", "kimi": "月之暗面 Kimi",
                   "minimax": "MiniMax", "qwen": "阿里百炼", "hunyuan": "腾讯混元",
                   "doubao": "火山方舟", "ernie": "百度千帆", "oai": "OpenAI",
                   "gpt": "OpenAI", "claude": "Anthropic", "gemini": "Google",
                   "grok": "xAI", "mi": "小米 MiMo", "mimo": "小米 MiMo",
                   "openrouter": "OpenRouter"}
    fc_box: dict = {"prices": None}
    f_row1 = QHBoxLayout()
    lb_v = row_label(t, "厂商")
    fc_vendor = QComboBox()
    fc_vendor.addItem("加载中…", "")
    fc_model = QComboBox()
    fc_note = desc(t, "")
    fc_note.setWordWrap(True)
    f_row1.addWidget(lb_v)
    f_row1.addWidget(fc_vendor)
    f_row1.addWidget(fc_model, 1)
    card_f.body.addLayout(f_row1)
    card_f.body.addWidget(fc_note)
    f_inputs: dict[str, QLineEdit] = {}
    for key, label, dft in (("msgs", "每日消息数", "200"), ("tin", "每消息输入用量", "800"),
                            ("tout", "每消息输出用量", "800")):
        r = QHBoxLayout()
        lb = row_label(t, label)
        e = QLineEdit(dft)
        e.setMaximumWidth(140)
        f_inputs[key] = e
        r.addWidget(lb)
        r.addWidget(e)
        r.addStretch(1)
        card_f.body.addLayout(r)
    f_row2 = QHBoxLayout()
    lb_p = row_label(t, "时段")
    fc_peak = QComboBox()
    fc_peak.addItem("空闲（夜间/周末）", "0")
    fc_peak.addItem("高峰（工作日 9-12 / 14-18）", "1")
    f_row2.addWidget(lb_p)
    f_row2.addWidget(fc_peak)
    f_row2.addStretch(1)
    card_f.body.addLayout(f_row2)
    f_row3 = QHBoxLayout()
    b_calc = Btn("计算", t, "primary")
    b_calc.setMinimumWidth(160)
    f_row3.addStretch(1)
    f_row3.addWidget(b_calc)
    f_row3.addStretch(1)
    card_f.body.addLayout(f_row3)
    fc_result = QLabel("—")
    fc_result.setFont(qfont(t, 12.5))
    fc_result.setWordWrap(True)
    fc_result.setStyleSheet(
        f"color:{t.tx};background:{_hex(rgba(t.q('tx'), 8))};"
        f"border:1px solid {t.bd};border-radius:{t.radius_tile}px;padding:10px 12px;")
    card_f.body.addWidget(fc_result)
    lay.addWidget(card_f)

    def _fc_group(k: str) -> str:
        for pre, name in _FC_VENDORS.items():
            if k.startswith(pre):
                return name
        return "其他"

    def _fc_fill_models() -> None:
        pr = fc_box.get("prices") or {}
        g = fc_vendor.currentData() or ""
        fc_model.blockSignals(True)
        fc_model.clear()
        for k in sorted(k for k in pr if _fc_group(k) == g):
            fc_model.addItem(k, k)
        fc_model.blockSignals(False)
        _fc_note()

    def _fc_note() -> None:
        k = fc_model.currentData() or ""
        p = (fc_box.get("prices") or {}).get(k)
        fc_note.setText(("输入 %s / 输出 %s / 缓存 %s 元·百万 Token%s" % (
            p.get("in"), p.get("out"),
            p.get("cached") if p.get("cached") is not None else "—",
            ("；" + str(p.get("note"))) if p.get("note") else "")) if p else "")

    def _fc_load() -> None:
        import threading # noqa: PLC0415 — overview_panel 内自用（check_panel 的函数级 import 不跨函数）

        box: dict = {"done": False, "r": None, "tries": 0}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415

            try:
                box["r"] = post_json("/api/prices", {}, timeout=12.0)
            except Exception as e: # noqa: BLE001 — 网络异常要落到 __err，不能让线程静默死掉
                box["r"] = {"__err": str(e)}
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="ov-prices").start()

        def _apply() -> None:
            # 存活判 + 自链上限：同 check 页 vf_state 的两条病 —— 页面重建后本闭包
            # 仍被定时器引用，无上限重排 + setText 打在已析构控件上。
            if not _qt_alive(fc_vendor):
                return
            if not box["done"]:
                box["tries"] += 1
                if box["tries"] > 45: # 45 × 300ms ≈ 13.5s，够一次 12s 超时
                    # 超时也要**把下拉里的「加载中…」换掉**：否则下拉一直显示
                    # 「加载中…」，看着像还在请求，其实是永远等不到。
                    fc_vendor.clear()
                    fc_vendor.addItem("价目读不到（后台超时）", "")
                    fc_note.setText("价目加载失败（后台超时没返回，可稍后重试）")
                    return
                QTimer.singleShot(300, _apply)
                return
            P = box.get("r")
            if not P or P.get("__err"):
                (fc_vendor.clear(), fc_vendor.addItem("价目读不到（后台没连上）", ""))
                fc_note.setText("价目加载失败（" + str((P or {}).get("__err") or "后台没返回")
                                + "），可稍后重试")
                return
            fc_box["prices"] = P
            fc_vendor.blockSignals(True)
            fc_vendor.clear()
            for g in sorted({_fc_group(k) for k in P}):
                fc_vendor.addItem(g, g)
            fc_vendor.blockSignals(False)
            _fc_fill_models()

        QTimer.singleShot(300, _apply)

    fc_vendor.currentIndexChanged.connect(lambda _i: _fc_fill_models())
    fc_model.currentIndexChanged.connect(lambda _i: _fc_note())
    _fc_load()

    def _fc_calc() -> None:
        pr = fc_box.get("prices") or {}
        k = fc_model.currentData() or ""
        p = pr.get(k)
        if not p:
            fc_result.setText("请先选择模型")
            return

        def _num(key: str) -> float:
            try:
                return max(0.0, float(f_inputs[key].text()))
            except Exception: # noqa: BLE001
                return 0.0

        msgs, ti, to = _num("msgs"), _num("tin"), _num("tout")
        peak = (fc_peak.currentData() or "0") == "1"
        p_in = float(p.get("in") or 0)
        p_out = float(p.get("out") or 0)
        pr_in = p_in * 2 if peak else p_in
        pr_out = p_out * 2 if peak else p_out
        has_cache = p.get("cached") is not None
        pr_cached = float(p["cached"]) * 2 if (peak and has_cache) else (
            float(p["cached"]) if has_cache else pr_in)
        per = (ti * pr_in + to * pr_out) / 1e6
        per_hit = (ti * pr_cached + to * pr_out) / 1e6

        def fmt(n: float) -> str:
            return ("¥%.2f" % n) if n >= 0.01 else ("¥%.4f" % n)

        lines = ["模型：%s　|　单价：输入 %g 元/百万%s，输出 %g 元/百万" % (
            k, pr_in, ("（缓存 %g）" % pr_cached) if has_cache else "", pr_out),
            "──────────────────────────",
            "每消息 ≈ %s" % fmt(per)]
        if has_cache and pr_cached < pr_in:
            lines[-1] += "（输入全缓存命中 ≈ %s）" % fmt(per_hit)
        lines += ["每日 %g 条 ≈ %s" % (msgs, fmt(msgs * per)),
                  "月成本 ≈ %s" % fmt(msgs * per * 30)]
        if k.startswith("deepseek") and not peak:
            lines[-1] += "　（若全高峰月 %s）" % fmt(msgs * per * 60)
        fc_result.setText("\n".join(lines))

    b_calc.clicked.connect(_fc_calc)

    def _refresh() -> None:
        # 首帧（还没拿到过任何数据）才写「读取中」；只要拿到过一次，后续读失败
        # 一律写「读不到」——不然 8 秒轮询遇到一次网络抖动，整片格子会从
        # 「运行中/2465 秒」倒退回「读取中」，看起来就是「明明有结果还显示检测中」。
        if not _qt_alive(cells["run"]):
            return
        st = config_io.get_json("/api/status") or {}
        if not st:
            if seen["ok"]:
                for lb in cells.values():
                    lb.setText("读不到")
                badge.set("idle", "读不到")
            else:
                for lb in cells.values():
                    lb.setText("读取中")
                badge.set("idle", "读取中")
            return
        seen["ok"] = True
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
        # 「群 N」格：原来 5 个格子写死 "—"、全文件无人取值 ⇒ 从上线起就是 5 块死板。
        # 真值在 /api/status.groups（本页下面的明细表用的是同一份）——这里把目标群
        # 逐个填进格子，不足 5 个的格子写"—"，多于 5 个的在第 5 格报总数。
        gs0 = st.get("groups") or []
        tgt = [str(g.get("name") or g.get("wxid") or "")
               for g in gs0 if isinstance(g, dict) and g.get("target")]
        for i in range(5):
            k = "g%d" % (i + 1)
            if i < 4:
                cells[k].setText(tgt[i] if i < len(tgt) else "—")
            else: # 第 5 格：还有更多就写「等 N 个」，否则填第 5 个/空
                cells[k].setText(("等 %d 个" % len(tgt)) if len(tgt) > 5
                                 else (tgt[4] if len(tgt) > 4 else "—"))
        badge.set("warn" if (paused or not wx_on) else "ok",
                  "已暂停" if paused else ("运行中" if wx_on else "微信没连上"))
        # 监听群明细（web group-table 同款：白名单群 + 监听/忽略 pill）
        gs = st.get("groups") or []
        gtable.setRowCount(len(gs))
        for i, g in enumerate(gs):
            if not isinstance(g, dict):
                continue
            gtable.setItem(i, 0, QTableWidgetItem(str(g.get("name") or g.get("wxid") or "")))
            on = bool(g.get("target"))
            it = QTableWidgetItem("监听" if on else "忽略")
            gtable.setItem(i, 1, it)

    _refresh()
    timer = QTimer(page)
    timer.setInterval(8000) # web loadStatus 同款 8 秒
    timer.timeout.connect(_refresh)
    timer.start()

    # ── 其余四钮真接线──
    # web 同款 API：test-api / data/export / data/import / stats/cal_clear
    note2 = desc(t, "")
    lay.addWidget(note2)

    def _test_api() -> None:
        note2.setText("测试 API 连通中…")
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415
            try:
                box["r"] = post_json("/api/test-api", {}, timeout=60.0)
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th # noqa: PLC0415
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
        import urllib.request as ur # noqa: PLC0415
        from addr import join_url # noqa: PLC0415
        from agent_bridge import current_url # noqa: PLC0415

        url = join_url(current_url(), path)
        opener = ur.build_opener(ur.ProxyHandler({})) # 绕代理（全 ui_qt 口径）
        req = ur.Request(url, data=data, headers={"Content-Type": ctype} if data else {})
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()

    def _export() -> None:
        from PySide6.QtWidgets import QFileDialog # noqa: PLC0415
        ds = time.strftime("%Y-%m-%d")
        p, _f = QFileDialog.getSaveFileName(page, "导出记录", f"Persona Morph-数据迁移-{ds}.zip",
                                            "迁移包 (*.zip)")
        if not p:
            return
        # 选完路径立刻回显，再把「取整包 + 落盘」挪去后台线程 ——
        # 原写法在主线程同步跑满 120s 预算，期间整个控制台窗口动不了。
        note2.setText("导出中…")
        box: dict = {"done": False, "err": None, "tries": 0}

        def _work() -> None:
            try:
                Path(p).write_bytes(_raw_post("/api/data/export", None, "", 120.0))
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th # noqa: PLC0415
        _th.Thread(target=_work, daemon=True, name="c8-data-export").start()

        def _land() -> None:
            note2.setText(f"导出失败：{box['err']}" if box["err"]
                          else f"已导出记录（计费+对话）→ {p}")

        def _apply() -> None:
            # 存活判 + 自链上限：面板被重建/关掉后本闭包仍被定时器引用，
            # 无上限重排会一直占着事件循环。
            if not _ui_alive(page):
                return
            if not box["done"]:
                box["tries"] += 1
                if box["tries"] > 500: # 500 × 300ms ≈ 150s，盖住 120s 超时
                    box["err"] = "后台超时没返回（可稍后重试）"
                    _deliver(_land)
                    return
                QTimer.singleShot(300, _apply)
                return
            _deliver(_land)
        QTimer.singleShot(300, _apply)

    def _import() -> None:
        from PySide6.QtWidgets import QFileDialog # noqa: PLC0415
        p, _f = QFileDialog.getOpenFileName(page, "选迁移包", "", "迁移包 (*.zip)")
        if not p:
            return
        # 与 _export 同理：读包 + 上传 + 解析是分钟级 I/O，不能占着 UI 线程。
        note2.setText("迁移中（合并到当前数据，按内容去重）…")
        box: dict = {"done": False, "ok": False, "note": "", "err": None, "tries": 0}

        def _work() -> None:
            try:
                import json as _j # noqa: PLC0415
                resp = _raw_post("/api/data/import", Path(p).read_bytes(), "application/zip", 180.0)
                j = _j.loads(resp.decode("utf-8", errors="replace"))
                box["ok"] = bool(j.get("ok"))
                box["note"] = (j.get("note") or "ok") if box["ok"] else (j.get("error") or "未知")
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th # noqa: PLC0415
        _th.Thread(target=_work, daemon=True, name="c8-data-import").start()

        def _land() -> None:
            if box["err"]:
                note2.setText(f"迁移失败：{box['err']}")
                return
            note2.setText(f"迁移完成：{box['note']}" if box["ok"] else f"迁移失败：{box['note']}")
            _refresh()

        def _apply() -> None:
            # 存活判 + 自链上限：同 _export。
            if not _ui_alive(page):
                return
            if not box["done"]:
                box["tries"] += 1
                if box["tries"] > 700: # 700 × 300ms ≈ 210s，盖住 180s 超时
                    box["err"] = "后台超时没返回（可稍后重试）"
                    _deliver(_land)
                    return
                QTimer.singleShot(300, _apply)
                return
            _deliver(_land)
        QTimer.singleShot(300, _apply)

    def _clear_cost() -> None:
        from confirm import ConfirmDialog # noqa: PLC0415
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
            from agent_bridge import post_json # noqa: PLC0415
            try:
                box["r"] = post_json("/api/stats/cal_clear", {}, timeout=30.0)
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th # noqa: PLC0415
        _th.Thread(target=_work, daemon=True, name="c8-cal-clear").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r") or {}
            note2.setText("已清空计费历史" if r.get("ok") else
                          f"失败：{r.get('error') or box.get('err') or '后台没连上'}")
        QTimer.singleShot(300, _apply)

    # ── 计费日历（web renderCal/loadDay/calPrev/calNext/年份弹层 真值 :4016-4101）──
    # 周一起始 + 当月天数 + 今天描边 + 翻月跨年进位；点日期 POST /api/stats/cal {d}。
    card_cal = Card(t)
    card_cal.body.addWidget(h2(t, "计费日历"))
    cal_head = QHBoxLayout()
    b_calprev = Btn("‹", t, "ghost")
    b_calprev.setFixedWidth(40)
    b_calym = Btn("…", t, "ghost")
    b_calnext = Btn("›", t, "ghost")
    b_calnext.setFixedWidth(40)
    cal_head.addWidget(b_calprev)
    cal_head.addWidget(b_calym)
    cal_head.addWidget(b_calnext)
    cal_head.addStretch(1)
    card_cal.body.addLayout(cal_head)
    cal_host = QWidget()
    cal_host.setStyleSheet("background:transparent;")
    cal_lay = QVBoxLayout(cal_host)
    cal_lay.setContentsMargins(0, 0, 0, 0)
    cal_lay.setSpacing(6)
    card_cal.body.addWidget(cal_host)
    cal_detail = desc(t, "点一个日期看当天用量；删除计费日志用下方「勾选删」。")
    card_cal.body.addWidget(cal_detail)
    lay.addWidget(card_cal)

    cal_state = {"ym": time.localtime().tm_year * 100 + time.localtime().tm_mon, "sel": None}
    cal_today = time.strftime("%Y-%m-%d")

    def _cal_render() -> None:
        while cal_lay.count():
            it = cal_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
                w.setParent(None) # 立刻脱离父子树（见上）
        y, m = cal_state["ym"] // 100, cal_state["ym"] % 100
        b_calym.setText("%d年%d月" % (y, m))
        import calendar as _cal # noqa: PLC0415

        first, days = _cal.monthrange(y, m) # first: 0=周一（与 web (getDay()+6)%7 同口径）
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        g = QGridLayout(inner)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(4)
        g.setVerticalSpacing(4)
        for i, wd in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            lb = QLabel(wd)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setFont(qfont(t, 11.5))
            lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
            g.addWidget(lb, 0, i)
        for day in range(1, days + 1):
            ds = "%04d-%02d-%02d" % (y, m, day)
            bt = Btn(str(day), t, "primary" if ds == cal_state["sel"] else "ghost")
            bt.setFixedHeight(26)
            if ds == cal_today: # web :4057 outline 同款：ghost 底上描蓝边
                bt.setStyleSheet(bt._qss() + f"QPushButton{{border:1px solid {t.blue};}}")
            bt.clicked.connect(lambda _=False, ds=ds: _cal_load_day(ds))
            g.addWidget(bt, (first + day - 1) // 7, (first + day - 1) % 7)
        cal_lay.addWidget(inner)

    def _cal_apply_day(r: dict | None, e: str | None, d: str) -> None:
        if e or not r or r.get("ok") is False:
            cal_detail.setText("加载失败：" + (e or (r or {}).get("error") or "后台没连上"))
            return
        cal_detail.setText("%s：%s 会话 · %s tok · ¥%.4f · %s 条" % (
            d, r.get("sessions", 0), r.get("tokens", 0),
            float(r.get("cost") or 0), r.get("sent", 0)))
        _cal_render()

    def _cal_load_day(d: str) -> None:
        cal_state["sel"] = d
        cal_detail.setText("加载中 %s…" % d)
        _async_post(None, "/api/stats/cal", {"d": d}, lambda r, e: _cal_apply_day(r, e, d))

    def _cal_prev() -> None:
        ym = cal_state["ym"]
        cal_state["ym"] = (ym // 100 - 1) * 100 + 12 if ym % 100 == 1 else ym - 1
        _cal_render()

    def _cal_next() -> None:
        ym = cal_state["ym"]
        cal_state["ym"] = (ym // 100 + 1) * 100 + 1 if ym % 100 == 12 else ym + 1
        _cal_render()

    def _cal_jump_year(yy: int, dlg) -> None:
        ym = cal_state["ym"]
        cal_state["ym"] = yy * 100 + (ym % 100 or 1)
        dlg.reject()
        _cal_render()

    def _cal_pick_year() -> None:
        # web :4076-4099 年份弹层同款：当前年 -4 ~ +6 共 11 年网格
        dlg, v = _card_dialog(t, b_calym, "跳到年份", 420)
        cur_y = cal_state["ym"] // 100
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        g = QGridLayout(host)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        for i, yy in enumerate(range(cur_y - 4, cur_y + 7)):
            yb = Btn(str(yy), t, "primary" if yy == cur_y else "ghost")
            yb.clicked.connect(lambda _=False, yy=yy: _cal_jump_year(yy, dlg))
            g.addWidget(yb, i // 6, i % 6)
        v.addWidget(host)
        foot = QHBoxLayout()
        cb = Btn("关闭", t, "ghost")
        cb.clicked.connect(dlg.reject)
        foot.addStretch(1)
        foot.addWidget(cb)
        v.addLayout(foot)
        dlg.exec()

    b_calprev.clicked.connect(_cal_prev)
    b_calnext.clicked.connect(_cal_next)
    b_calym.clicked.connect(_cal_pick_year)
    _cal_render()

    # ── 勾选删（web costClearSel → openBillDlg 真值 :4140-4264）──
    def _open_bill_dlg(bills: list) -> None:
        dlg, v = _card_dialog(t, page, "勾选删除计费日志", 640)
        hint = _dlg_note(t)
        hint.setText("共 %d 天，精确到年月日；删除后概览自动刷新。勾选要删除的天"
                     "（可一键勾今日/本月）；操作不可恢复。" % len(bills))
        v.addWidget(hint)
        quick = QHBoxLayout()
        b_all = Btn("全选", t, "ghost")
        b_none = Btn("清空勾选", t, "ghost")
        b_day = Btn("勾选今日", t, "ghost")
        b_mon = Btn("勾选本月", t, "ghost")
        for b in (b_all, b_none, b_day, b_mon):
            quick.addWidget(b)
        v.addLayout(quick)
        loc_row = QHBoxLayout()
        lb_loc = _dlg_note(t)
        lb_loc.setText("按日期定位")
        loc_cb = QComboBox()
        for d in sorted((str(b.get("day")) for b in bills if b.get("day")), reverse=True):
            loc_cb.addItem(d, d)
        loc_btn = Btn("定位", t, "ghost")
        loc_btn.setFixedWidth(64)
        loc_row.addWidget(lb_loc)
        loc_row.addWidget(loc_cb)
        loc_row.addWidget(loc_btn)
        loc_row.addStretch(1)
        v.addLayout(loc_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        rows_host = QWidget()
        rows_host.setStyleSheet("background:transparent;")
        rows_lay = QVBoxLayout(rows_host)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(2)
        checks: dict[str, QCheckBox] = {}
        by_day = {str(b.get("day")): b for b in bills if b.get("day")}
        for d in sorted(by_day, reverse=True):
            b = by_day[d]
            cb = QCheckBox("%s · %s tok · ¥%.4f · %s 次 · %s 会话" % (
                d, b.get("tokens", 0), float(b.get("cost") or 0),
                b.get("calls", 0), b.get("sessions", 0)))
            cb.setStyleSheet(f"background:transparent;color:{t.tx};")
            checks[d] = cb
            rows_lay.addWidget(cb)
        rows_lay.addStretch(1)
        scroll.setWidget(rows_host)
        scroll.setFixedHeight(min(300, 36 * max(1, len(checks)) + 14))
        v.addWidget(scroll)
        bsum = _dlg_note(t)
        v.addWidget(bsum)
        foot = QHBoxLayout()
        foot.addStretch(1)
        b_cancel = Btn("取消", t, "ghost")
        b_ok = Btn("确认删除", t, "primary")
        foot.addWidget(b_cancel)
        foot.addWidget(b_ok)
        v.addLayout(foot)

        def _sel_days() -> list:
            return [d for d in sorted(checks) if checks[d].isChecked()]

        def _sum() -> None:
            days = _sel_days()
            tok = sum(int(by_day[d].get("tokens") or 0) for d in days)
            cost = sum(float(by_day[d].get("cost") or 0) for d in days)
            bsum.setText("已选 %d 天 · %d tok · ¥%.4f%s" % (
                len(days), tok, cost,
                "（全部选中）" if days and len(days) == len(checks) else ""))

        def _set_all(on: bool) -> None:
            for cb in checks.values():
                cb.setChecked(on)

        def _sel_today() -> None:
            _set_all(False)
            tdy = time.strftime("%Y-%m-%d")
            if tdy in checks:
                checks[tdy].setChecked(True)

        def _sel_month() -> None:
            _set_all(False)
            pre = time.strftime("%Y-%m")
            for d, cb in checks.items():
                if d.startswith(pre):
                    cb.setChecked(True)

        def _locate() -> None:
            d = str(loc_cb.currentData() or "")
            cb = checks.get(d)
            if cb is not None:
                scroll.ensureWidgetVisible(cb)
                b = by_day[d]
                lb_loc.setText("%s：%s tok · ¥%.4f · %s 次 · %s 会话（已在列表定位）" % (
                    d, b.get("tokens", 0), float(b.get("cost") or 0),
                    b.get("calls", 0), b.get("sessions", 0)))

        def _do_delete() -> None:
            days = _sel_days()
            if not days:
                bsum.setText("请先勾选要删除的天")
                return
            d2 = ConfirmDialog(
                t, dlg, "确认删除", "确认删除所选 %d 天的计费日志？删除后不可恢复。" % len(days),
                ["这些天的计费日志会删除", "这个动作不能撤销"],
                confirm_label="确认删除", cancel_label="取消", dangerous=True)
            if not d2.exec():
                return
            b_ok.setEnabled(False)
            b_ok.setText("删除中…")
            _async_post(None, "/api/stats/cal_delete", {"days": days},
                        lambda r, e: _del_done(r, e, days, dlg))

        def _del_done(r: dict | None, e: str | None, days: list, dlg) -> None:
            b_ok.setEnabled(True)
            b_ok.setText("确认删除")
            if e or not r or not r.get("ok"):
                bsum.setText("删除失败：" + (e or (r or {}).get("error") or "后台没连上"))
                return
            dlg.accept()
            note2.setText("已删除 %d 天计费日志" % len(r.get("removed") or days))
            _refresh()

        for cb in checks.values():
            cb.toggled.connect(_sum)
        b_all.clicked.connect(lambda: _set_all(True))
        b_none.clicked.connect(lambda: _set_all(False))
        b_day.clicked.connect(_sel_today)
        b_mon.clicked.connect(_sel_month)
        loc_btn.clicked.connect(_locate)
        b_cancel.clicked.connect(dlg.reject)
        b_ok.clicked.connect(_do_delete)
        _sum()
        dlg.exec()

    def _clear_cost_sel() -> None:
        note2.setText("读取计费日志中…")
        _async_post(None, "/api/stats/cal_list", {},
                    lambda r, e: _cal_list_done(r, e))

    def _cal_list_done(r: dict | None, e: str | None) -> None:
        if e or not r:
            note2.setText("加载计费日志失败：" + (e or "后台没连上"))
            return
        bills = r.get("bills") or []
        if not bills:
            note2.setText("当前没有可删除的计费日志")
            return
        note2.setText("")
        _open_bill_dlg(bills)

    hooks = [lambda: _refresh(), _test_api, _export, _import, _clear_cost_sel, _clear_cost]
    lay.addWidget(_btn_row(t, [("立即刷新", "primary"), ("测试 API 连通", "ghost"),
                               ("导出记录", "ghost"), ("迁移数据", "ghost"),
                               ("勾选删", "ghost"), ("一键删", "danger")], hooks))
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
    """检测中心 —— P0-A③ 把「入口说明页」升级为真检测：
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

    import threading # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    card = Card(t)
    card.body.addWidget(h2(t, "检测结果"))
    area = _plain_area(t, "", placeholder="点上面的按钮开始检测，结果逐项显示在这里", height=300)
    area.setReadOnly(True)
    card.body.addWidget(area)
    tip = desc(t, "尚未开始")
    card.body.addWidget(tip)
    lay.addWidget(card)

    row1 = FlowBox(t, spacing=6)
    b_code = Btn("代码检测", t, "primary")
    b_deps = Btn("代码检测＋依赖核对", t, "ghost")
    b_deps.setToolTip("额外跑依赖版本详细核对（55 项，稍慢）")
    b_tip2 = Btn("查看进度条", t, "ghost")
    b_tip2.setToolTip("点击切换到概览查看常驻状态条（web codeCheckTip2 同款语义）")
    row1.add(b_code)
    row1.add(b_deps)
    row1.add(b_tip2)
    lay.addWidget(row1)

    def _goto_overview() -> None:
        # web :1342 = sec-overview.scrollIntoView；原生壳是分页栈，等效 = 切到概览页
        w = b_tip2.window()
        go = getattr(w, "_go", None)
        if callable(go):
            go("overview", sec_meta.get("overview").title)
        else:
            tip.setText("常驻状态条在「概览」页顶部（导航切过去即可看）")

    b_tip2.clicked.connect(_goto_overview)

    # ── 症状检验器（web vfBtns/vfState/vfResult/vfCopy 同款：只读检查，四档判决，
    #    按钮跑完变色 绿=通过 黄=部分通过 红=卡住）──
    card_vf = Card(t)
    card_vf.body.addWidget(h2(t, "症状检验器（出了问题自己对症查）"))
    card_vf.body.addWidget(desc(
        t, "不用你点它 —— 出问题时产品自己会把这些（原因码 + 调用点 + 兼容性摘要）记进本机记录，"
           "你点一下「反馈」就一起带走了。这里留着是给你自己想看的时候点的："
           "**只读检查**（不动窗口、不发消息、不改配置），点完出一段可直接粘贴的报告。"))
    card_vf.body.addWidget(desc(
        t, "怎么看报告（判决分四档）：**通过** · **卡住**（证据说就是它）· "
           "**部分通过**（有项目没测到）· **没测到**（这一格这次验不了：不算通过也不算失败 —— "
           "别把它当「没问题」）。点完之后症状按钮自己会变色：绿＝通过 · 黄＝部分通过 · 红＝卡住。"))
    vf_host = QWidget()
    vf_host.setStyleSheet("background:transparent;")
    vf_grid = QGridLayout(vf_host)
    vf_grid.setContentsMargins(0, 0, 0, 0)
    vf_grid.setHorizontalSpacing(8)
    vf_grid.setVerticalSpacing(6)
    card_vf.body.addWidget(vf_host)
    vf_state = desc(t, "检验器清单读取中…")
    card_vf.body.addWidget(vf_state)
    vf_area = _plain_area(t, "", placeholder="点上面的症状按钮，报告显示在这里（每个 1~3 秒）", height=190)
    vf_area.setReadOnly(True)
    card_vf.body.addWidget(vf_area)
    vf_row = FlowBox(t, spacing=6)
    b_vfcopy = Btn("复制报告", t, "ghost")
    b_vfcopy.setEnabled(False)
    vf_row.add(b_vfcopy)
    card_vf.body.addWidget(vf_row)
    lay.addWidget(card_vf)

    vf_box: dict = {"report": ""}

    def _vf_load() -> None:
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            box["r"] = post_json("/api/verifiers", {}, timeout=10.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-verifiers").start()

        def _apply() -> None:
            # 存活判 + 自链上限：页面重建/关页后本闭包仍被定时器引用，
            # 旧实现会一直重排（无上限）并在 setText 上抛 RuntimeError
            # （C++ 对象已删），连同「检验器清单读取中…」占位一起留在页上。
            if not _qt_alive(vf_state):
                return
            if not box["done"]:
                box["tries"] = box.get("tries", 0) + 1
                if box["tries"] > 40: # 40 × 300ms ≈ 12s，够一次 10s 超时
                    vf_state.setText("检验器清单读不到（超时）")
                    return
                QTimer.singleShot(300, _apply)
                return
            lst = (box.get("r") or {}).get("verifiers") or []
            if not lst:
                vf_state.setText("检验器清单读不到（后台没返回清单）")
                return
            vf_state.setText("")
            for i, v in enumerate(lst):
                vid = str(v.get("id") or "")
                b = Btn(str(v.get("name") or vid), t, "ghost")
                b.setToolTip("只读检查：不动窗口、不发消息、不改配置")
                b.clicked.connect(lambda _ch=False, _vid=vid, _b=b: _vf_run(_vid, _b))
                vf_grid.addWidget(b, i // 3, i % 3)

        QTimer.singleShot(300, _apply)

    def _vf_run(vid: str, btn) -> None:
        vf_state.setText("正在检查：%s…" % btn.text())
        vf_area.setPlainText("正在检查：%s…" % btn.text())
        b_vfcopy.setEnabled(False)
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            box["r"] = post_json("/api/verify?id=%s" % vid, {}, timeout=60.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-verify").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r") or {}
            # web 同款四档判决映射：ok===false→卡住 / partial→部分通过 / ok===true→通过 / 其余→没测到
            st = ("fail" if r.get("ok") is False else
                  "partial" if r.get("partial") else
                  "ok" if r.get("ok") is True else "unknown")
            vf_state.setText({
                "fail": "这一次：卡住（证据说就是它 —— 照报告里的「下一步」做）",
                "partial": "这一次：部分通过（有项目没测到：不算通过也不算失败）",
                "ok": "这一次：通过（这一页的判据这次都验到了）",
                "unknown": "这一次：没测到（这次验不了 —— 别当成「没问题」）",
            }.get(st, ""))
            report = str(r.get("report") or "")
            vf_box["report"] = report
            vf_area.setPlainText(report or json.dumps(r, ensure_ascii=False, indent=1)[:4000])
            b_vfcopy.setEnabled(bool(report))
            lv = {"fail": "err", "partial": "warn", "ok": "ok"}.get(st)
            if lv:
                bg, bd, fg = status_colors(t, lv)
                btn.setStyleSheet(
                    f"QPushButton{{background:{_hex(bg)};color:{_hex(fg)};"
                    f"border:1px solid {_hex(bd)};border-radius:{pill(28)}px;padding:4px 12px;}}")

        QTimer.singleShot(300, _apply)

    def _vf_copy() -> None:
        from PySide6.QtWidgets import QApplication # noqa: PLC0415

        if vf_box.get("report"):
            QApplication.clipboard().setText(vf_box["report"])
            b_vfcopy.setText("已复制")
            QTimer.singleShot(1200, lambda: b_vfcopy.setText("复制报告"))

    b_vfcopy.clicked.connect(_vf_copy)
    _vf_load()

    row2 = FlowBox(t, spacing=6)
    b_self = Btn("点击测试", t, "primary")
    b_stop = Btn("停止检测", t, "ghost")
    b_stop.setEnabled(False)
    row2.add(b_self)
    row2.add(b_stop)
    lay.addWidget(row2)

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
            except Exception as e: # noqa: BLE001
                box["err"] = f"启动失败：{e}"
                box["done"] = True
                return
            for _i in range(300): # web 同款上限 300 轮
                try:
                    pr = post_json("/api/code-check/progress", {}, timeout=10.0)
                except Exception as e: # noqa: BLE001
                    box["err"] = f"进度查询失败：{e}"
                    break
                if pr:
                    if pr.get("items"):
                        box["items"] = pr["items"]
                    box["prog"] = pr.get("progress")
                    if pr.get("done"):
                        box["result"] = pr.get("result")
                        break
                time.sleep(0.15) # web 150ms 同款
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
                QTimer.singleShot(300, _apply) # web 150ms 轮询，UI 300ms 足够顺
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
            except Exception as e: # noqa: BLE001
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
        # 停止请求走后台线程 —— 原来主线程直连 post_json(timeout=10)，
        # 后端慢时整窗冻结最长十秒（全项目扫描出的唯一主线程网络点）。
        tip.setText("正在发送停止请求…")
        st_box: dict = {"done": False, "err": None}

        def _work() -> None:
            try:
                post_json("/api/selfcheck-stop", {}, timeout=10.0)
            except Exception as e: # noqa: BLE001
                st_box["err"] = str(e)
            st_box["done"] = True

        import threading as _thst # noqa: PLC0415

        _thst.Thread(target=_work, daemon=True, name="selfcheck-stop").start()

        def _ap() -> None:
            if not st_box["done"]:
                QTimer.singleShot(150, _ap)
                return
            tip.setText("已发出停止请求（当前检测项跑完即停）" if st_box["err"] is None
                        else f"停止失败：{st_box['err']}")

        QTimer.singleShot(150, _ap)

    b_self.clicked.connect(_run_self)
    b_stop.clicked.connect(_stop)

    # ── 拍一拍检测（web pokeGroup/pokeVerifyOnly/pokeTest/uiTestResult/uiTestDetail 同款）──
    card_pk = Card(t)
    card_pk.body.addWidget(h2(t, "拍一拍检测"))
    pk_row1 = QHBoxLayout()
    lb_g = row_label(t, "目标群")
    pk_group = QComboBox()
    pk_group.addItem("自动（最近有人发言的群）", "")
    pk_group.setMinimumWidth(260)
    pk_row1.addWidget(lb_g)
    pk_row1.addWidget(pk_group)
    pk_row1.addStretch(1)
    card_pk.body.addLayout(pk_row1)
    pk_row2 = QHBoxLayout()
    pk_only = QCheckBox("简易检测（只验证右键头像能弹出「拍一拍」菜单，不点击、不拍任何人）")
    pk_only.setChecked(True) # web 默认勾选同款
    pk_row2.addWidget(pk_only)
    pk_row2.addStretch(1)
    card_pk.body.addLayout(pk_row2)
    pk_row3 = QHBoxLayout()
    b_poke = Btn("拍一拍检测", t, "primary")
    pk_note = desc(t, "")
    pk_row3.addWidget(b_poke)
    pk_row3.addWidget(pk_note, 1)
    card_pk.body.addLayout(pk_row3)
    pk_detail = desc(t, "")
    pk_detail.setWordWrap(True)
    card_pk.body.addWidget(pk_detail)
    card_pk.body.addWidget(desc(
        t, "注意：拍一拍是右键「对方头像」触发：头像由程序识别，若群内同名/头像辨识不清，"
           "理论上有拍到其他群友的风险 —— 所以默认用「简易检测」，确认无误后再取消勾选完整执行。"))
    lay.addWidget(card_pk)

    def _pk_load_groups() -> None:
        """目标群下拉填充（web loadPokeGroups 同源：/api/wechat-groups）。"""
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            box["r"] = post_json("/api/wechat-groups", {}, timeout=10.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-pokegroups").start()

        def _apply() -> None:
            # 存活判 + 自链上限：同 _vf_load 的两条病 —— 页面重建后闭包仍被定时器
            # 引用，无上限重排 + setText 打在已析构的 C++ 对象上（RuntimeError 被
            # 外层 try 吞掉），下拉永远停在空、提示行永远回不到结论。
            if not _qt_alive(pk_note):
                return
            if not box["done"]:
                box["tries"] = box.get("tries", 0) + 1
                if box["tries"] > 40: # 40 × 300ms ≈ 12s，够一次 10s 超时
                    pk_note.setText("读不到群列表（后台超时没返回），可稍后重试")
                    return
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r")
            groups = (r or {}).get("groups") or [] if isinstance(r, dict) else []
            if not groups:
                # 读失败与"确实没有群"要分开说：ok=False（微信没接上/库被占用）时
                # 下拉空着会让人以为是"没有群可拍"。这里把原因写进提示行。
                if not isinstance(r, dict):
                    pk_note.setText("读不到群列表（后台没连上），可稍后重试")
                elif r.get("ok") is False:
                    pk_note.setText("读不到群列表：" + str(r.get("error") or "读取失败"))
                else:
                    pk_note.setText("没有可拍的群（先启动机器人并检测群）")
                return
            # 读到群：清掉占位（否则「正在读取」会一直挂着），下拉留空提示交给占位首项
            pk_note.setText("")
            for g in groups:
                if isinstance(g, dict) and g.get("wxid"):
                    pk_group.addItem(str(g.get("name") or g.get("wxid")), str(g.get("wxid")))

        QTimer.singleShot(300, _apply)

    _pk_load_groups()

    def _run_poke() -> None:
        b_poke.setEnabled(False)
        pk_note.setText(("简易检测中" if pk_only.isChecked() else "完整执行中")
                        + "（约 10~25 秒，请勿动鼠标）…")
        pk_detail.setText("")
        body = {"group_wxid": str(pk_group.currentData() or ""),
                "verify_only": bool(pk_only.isChecked())}
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            box["r"] = post_json("/api/poke-test", body, timeout=60.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-poketest").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            r = box.get("r") or {}
            pk_note.setText(str(r.get("message") or r.get("error") or "(无结果)"))
            tgt = r.get("target") or {}
            lines = ["目标：%s" % (("%s / %s 在群「%s」" % (tgt.get("name"), tgt.get("id"), r.get("group")))
                                  if isinstance(tgt, dict) and tgt.get("name") else "未解析")]
            if r.get("verify_only"):
                lines.append("（简易模式：仅验证菜单可弹，未实际拍）")
            steps = r.get("steps") or []
            if steps:
                lines.append("步骤：")
                lines.extend(str(s) for s in steps)
            pk_detail.setText("\n".join(lines))
            b_poke.setEnabled(True)

        QTimer.singleShot(300, _apply)

    b_poke.clicked.connect(_run_poke)

    card2 = Card(t)
    card2.body.addWidget(h2(t, "功能自检清单（按重要性排序）"))
    items = ["环境体检 · 点上方「点击测试」", "发消息 · 群里 @机器人 说句话",
             "拍一拍 · 先「简易检测」，再完整检测", "引用回复 · 引用某条消息回复",
             "发图 · 让机器人「发一张图」", "识图 · 引用图片＋@机器人 分析这张",
             "联网搜索 · @机器人 今天的天气/新闻", "记忆 · 让机器人记住一件事后到「记忆」页看",
             "挂件 · 看右下角鲸鱼挂件的数据与拖拽", "启停重启 · 顶部停止/重启后能接管",
             "多厂商切换 · 换厂商保存后测试连通"]
    # 对齐 web 真值（checkList 表：结果=勾选框 + 项目 + 怎么测 + 预期）——
    # 原来整表降级成纯文字 desc，「结果」勾选列蒸发。
    #   ⚠️ QCheckBox/QComboBox 只用模块级名，函数内**不许**再 import：函数内 import
    #   会把名字变局部变量 ⇒ 本函数前段（症状检验器/拍一拍区）用到时还没绑定 ⇒
    #   UnboundLocalError（worker 二轮踩过、c34 又踩一次，中段 import 已除）。
    ck_boxes: list[QCheckBox] = []
    # web :6784-6797「wxAgent.checklist」localStorage 的 Qt 等价：QSettings 跨会话记忆
    from PySide6.QtCore import QSettings as _QSet # noqa: PLC0415

    _ck_settings = _QSet("WXAgent", "persona-morph-ui")

    def _ck_save() -> None:
        _ck_settings.setValue("checklist", json.dumps([c.isChecked() for c in ck_boxes]))

    def _ck_load() -> None:
        try:
            arr = json.loads(str(_ck_settings.value("checklist") or "[]"))
        except Exception: # noqa: BLE001
            arr = []
        for c, v in zip(ck_boxes, arr if isinstance(arr, list) else []):
            if isinstance(v, bool):
                c.setChecked(v)

    _i_ck = 0
    for it in items:
        roww = QWidget()
        roww.setStyleSheet("background:transparent;")
        rh = QHBoxLayout(roww)
        rh.setContentsMargins(2, 1, 2, 1)
        rh.setSpacing(8)
        ck = QCheckBox()
        ck.setObjectName("ckItem%d" % _i_ck) # 测试定位用（页面另有零散勾选控件）
        _i_ck += 1
        ck.setToolTip("勾选=这项测过了（跨会话记忆）")
        rh.addWidget(ck)
        ck_boxes.append(ck)
        head, _, rest = it.partition(" · ")
        t1 = QLabel(head)
        t1.setFont(qfont(t, 12.5, 600))
        t1.setStyleSheet(f"color:{t.tx};background:transparent;")
        t1.setMinimumWidth(92)
        t2 = QLabel(rest)
        t2.setFont(qfont(t, 11.5))
        t2.setStyleSheet(f"color:{t.tx2};background:transparent;")
        t2.setWordWrap(True)
        rh.addWidget(t1)
        rh.addWidget(t2, 1)
        card2.body.addWidget(roww)

    # web :1396 同款：重置钮 + 已完成计数（ckReset/ckCount）放清单下方
    ck_foot = FlowBox(t, spacing=6)
    b_ckreset = Btn("重置勾选", t, "ghost")
    ck_count = QLabel("已完成 0 / %d" % len(items))
    ck_count.setFont(qfont(t, 12))
    ck_count.setStyleSheet(f"color:{t.tx3};background:transparent;")
    ck_foot.add(b_ckreset)
    ck_foot.add(ck_count)
    card2.body.addWidget(ck_foot)

    def _ck_recount() -> None:
        n = sum(1 for c in ck_boxes if c.isChecked())
        ck_count.setText("已完成 %d / %d" % (n, len(ck_boxes)))

    def _ck_reset() -> None:
        for c in ck_boxes:
            c.setChecked(False)

    for c in ck_boxes:
        c.toggled.connect(_ck_recount)
        c.toggled.connect(lambda _=False: _ck_save())
    b_ckreset.clicked.connect(_ck_reset)
    _ck_load() # 跨会话恢复（web localStorage 同语义）；恢复触发 toggled → 计数同步
    lay.addWidget(card2)

    # ── 视频通路：三态徽章，数据来自 /api/status 的 media.video ──
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
        name = row_label(t, label, 190)
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
        except Exception: # noqa: BLE001
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
    """运行明细 —— P0-C：web 的 #sessList / #arcList 两块动态列表原本全失，这里补齐：
      · sessList（web :2107）：GET /api/sessions?limit=30 拿运行明细，逐条渲染
        （勾选框 + 群名 + 状态 + 时间 + token/费用），支持**按条勾选删除**；
      · 四钮（:2096-2104）：刷新(sessRefresh) / 删除选中(sessSelDel) / 撤销(sessUndo)
        / 清空(sessClear)；删除选中 POST /api/sessions/delete、撤销 POST
        /api/sessions/restore、清空 POST /api/memory clear_sessions（与 web 同款）；
      · 存档 arcList（web :2121）：arcChat 群选 + arcLimit + 读取该会话存档(arcLoad) +
        刷新会话列表(arcReload)，逐条渲染 #arcList 并支持 屏蔽/解除/清除
        （POST /api/archive/block、/unblock、/delete）。
    说明（回执用）：原 sessions_panel 只渲染了一个**纯文本 JSON 阅读框**（见 时期
    实现，没有列表/勾选/撤销），本次在保留「原始 JSON 折叠查看」的同时，新增上面两组
    真动态列表与完整按钮语义。"""
    s = sec_meta.get("sessions")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "发了什么、多少用量、耗时，按天落盘可勾选删除。"))
    from panels_qt import _line # noqa: PLC0415

    state: dict = {"sessions": [], "undo": ""}

    # ── 运行明细卡（#sessList）──
    scard = Card(t)
    scard.body.addWidget(h2(t, "运行明细（/api/sessions）"))
    btn_row = FlowBox(t, spacing=6)
    b_refresh = Btn("刷新", t, "primary")
    b_refresh.setObjectName("sessRefresh")
    b_sel = Btn("删除选中", t, "danger")
    b_sel.setObjectName("sessSelDel")
    b_sel.setEnabled(False)
    b_undo = Btn("撤销上次删除", t, "ghost")
    b_undo.setObjectName("sessUndo")
    b_undo.setEnabled(False)
    b_clear = Btn("一键清全部", t, "danger")
    b_clear.setObjectName("sessClear")
    btn_row.add(b_refresh)
    btn_row.add(b_sel)
    btn_row.add(b_undo)
    btn_row.add(b_clear)
    scard.body.addWidget(btn_row)
    sess_list = _bordered_list(t, "sessList", 240)
    scard.body.addWidget(sess_list)
    snote = desc(t, "")
    scard.body.addWidget(snote)
    scard.body.addWidget(desc(t, "勾选每条左侧「删」→「删除选中」＝只删这几条（同一天其他记录不动）；"
                                "删错了点「撤销上次删除」。清空＝清全部明细（不可恢复）。"))
    # 「原始返回」卡的渲染器晚绑定（它在下方才构建；而 load_sessions 上面就会被调用）
    _raw_box: dict = {"fn": None}
    lay.addWidget(scard)

    def _sync_sel_btn() -> None:
        any_checked = False
        for i in range(sess_list.count()):
            w = sess_list.itemWidget(sess_list.item(i))
            cb = w.findChild(QCheckBox) if w else None
            if cb and cb.isChecked():
                any_checked = True
        b_sel.setEnabled(any_checked)

    def _render_sessions() -> None:
        items = state["sessions"]
        sess_list.clear()
        for e in items:
            it = QListWidgetItem()
            sess_list.addItem(it)
            sess_list.setItemWidget(it, _session_card(t, e, _sync_sel_btn))
            it.setSizeHint(sess_list.itemWidget(it).sizeHint())
        n = len(items)
        badge.set("idle", f"{n} 条" if n else "暂无记录")
        _sync_sel_btn()

    def load_sessions() -> None:
        _eb: dict = {}
        try:
            r = config_io.get_json("/api/sessions?limit=30", timeout=5.0, err_box=_eb)
        except Exception as e: # noqa: BLE001
            r = None
            _eb["err"] = str(e)
        if not isinstance(r, dict):
            snote.setText("读不到记录：" + _read_fail_hint(_eb.get("err")))
            badge.set("err", "读不到")
            return
        if r.get("ok") is False:
            # 后端活着但读档案目录失败 —— 不能显示「暂无记录」（那是"确实没有"）
            snote.setText("读不到记录：" + str(r.get("error") or "后台读取失败")
                          + "（这不代表没有记录，是这次没读到）")
            badge.set("err", "读不到")
            return
        state["sessions"] = r.get("sessions") or []
        snote.setText(f"读取成功 · {time.strftime('%H:%M:%S')} · {len(state['sessions'])} 条")
        _render_sessions()
        # 「原始返回」卡与上面用的是同一份数据 ⇒ 复用这次响应，不再单独发一次请求
        #   （原先两处各打一次 /api/sessions，白白翻倍）。
        #   晚绑定：原始卡在 load_sessions() 首次调用之后才构建，这里先用可变引用占位。
        _fn = _raw_box.get("fn")
        if callable(_fn):
            _fn(r)

    def _del_selected() -> None:
        sel = []
        for i in range(sess_list.count()):
            w = sess_list.itemWidget(sess_list.item(i))
            cb = w.findChild(QCheckBox) if w else None
            if cb and cb.isChecked():
                e = state["sessions"][i]
                sel.append({"date": str(e.get("ts") or "")[:10], "ts": str(e.get("ts") or "")})
        if not sel:
            snote.setText("请先勾选要删除的记录")
            return
        # 对齐 web uiConfirm 口径（assets/console/index.html:4280）：删除运行记录前二次确认
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "删除选中的运行记录",
            f"删除选中的 {len(sel)} 条运行记录？",
            [f"只删这 {len(sel)} 条，同一天的其他记录不受影响",
             "删错了点旁边的「撤销」就能还原"],
            confirm_label="删除")
        if not d.exec():
            return
        snote.setText(f"删除 {len(sel)} 条中…")
        _async_post(page, "/api/sessions/delete", {"items": sel}, lambda r, e: (
            state.__setitem__("undo", (r or {}).get("undo", "")) if (r and r.get("ok")) else None,
            b_undo.setEnabled(bool(state["undo"])),
            load_sessions(),
            snote.setText((r or {}).get("note") or f"已删除 {len(sel)} 条" if (r and r.get("ok"))
                          else f"删除失败：{e or (r or {}).get('error') or '后台没连上'}")))

    def _undo() -> None:
        if not state["undo"]:
            snote.setText("没有可撤销的删除")
            return
        snote.setText("撤销上次删除中…")
        _async_post(page, "/api/sessions/restore", {"undo": state["undo"]}, lambda r, e: (
            state.__setitem__("undo", ""), b_undo.setEnabled(False), load_sessions(),
            snote.setText("已撤销" if (r and r.get("ok")) else f"撤销失败：{e or (r or {}).get('error') or '后台没连上'}")))

    def _clear_sessions() -> None:
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "清空全部运行明细？",
            "删的是运行明细里的会话日志与对话历史。",
            ["模型之后不会再记得这些对话", "这个动作不能撤销"],
            confirm_label="确认清空", cancel_label="算了", dangerous=True)
        if not d.exec():
            return
        snote.setText("清空中…")
        _async_post(page, "/api/memory", {"action": "clear_sessions"}, lambda r, e: (
            load_sessions(),
            snote.setText("会话日志已清除" if (r and (r.get("ok") or r.get("note")))
                          else f"清空失败：{e or (r or {}).get('error') or '后台没连上'}")))

    b_refresh.clicked.connect(load_sessions)
    b_sel.clicked.connect(_del_selected)
    b_undo.clicked.connect(_undo)
    b_clear.clicked.connect(_clear_sessions)
    # 自检钩子
    page.c12_list = sess_list
    page.c12_load = load_sessions
    load_sessions()

    # ── 存档卡（#arcList，web :2112-2122）──
    acard = Card(t)
    acard.body.addWidget(h2(t, "存档：按条屏蔽 / 清除"))
    arc_row = QHBoxLayout()
    arc_chat = QComboBox()
    arc_chat.setObjectName("arcChat")
    arc_chat.setMinimumWidth(220)
    arc_chat.setFixedHeight(32)
    arc_chat.setFont(qfont(t, t.body_size))
    arc_chat.setStyleSheet(
        f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
        f"color:{t.tx};border:1px solid {t.bd};}}")
    arc_limit = _line(t, "30", placeholder="条数")
    arc_limit.setObjectName("arcLimit")
    arc_limit.setFixedWidth(70)
    b_arc_load = Btn("读取该会话存档", t, "primary")
    b_arc_load.setObjectName("arcLoad")
    b_arc_reload = Btn("刷新会话列表", t, "ghost")
    b_arc_reload.setObjectName("arcReload")
    arc_row.addWidget(QLabel("会话"))
    arc_row.addWidget(arc_chat, 1)
    arc_row.addWidget(QLabel("条数"))
    arc_row.addWidget(arc_limit)
    arc_row.addWidget(b_arc_load)
    arc_row.addWidget(b_arc_reload)
    acard.body.addLayout(arc_row)
    arc_list = _bordered_list(t, "arcList", 200)
    acard.body.addWidget(arc_list)
    anote = desc(t, "")
    acard.body.addWidget(anote)
    lay.addWidget(acard)

    def load_arc_chats() -> None:
        try:
            r = config_io.get_json("/api/archive", timeout=5.0)
        except Exception: # noqa: BLE001
            r = None
        chats = (r or {}).get("chats") or []
        arc_chat.clear()
        for c in chats:
            arc_chat.addItem(f"{c.get('chat_key')}（{c.get('messages') or 0} 条）", c.get("chat_key"))
        sn = (r or {}).get("snapshot") or {}
        anote.setText(f"屏蔽会话 {len(sn.get('chats') or [])} 个 ｜ 已屏蔽条目 {sn.get('blocked_entries') or 0} 条")

    def load_arc() -> None:
        ck = arc_chat.currentData()
        if not ck:
            anote.setText("还没有可选会话（先让机器人跑一会儿）")
            return
        try:
            lim = max(1, min(200, int((arc_limit.text() or "30").strip() or 30)))
        except ValueError:
            lim = 30
        anote.setText("读取中…")
        try:
            r = config_io.get_json(f"/api/archive?chat_key={ck}&limit={lim}", timeout=5.0)
        except Exception as e: # noqa: BLE001
            r = None
        if not isinstance(r, dict):
            anote.setText(f"读存档失败：{e}")
            return
        items = r.get("items") or []
        arc_list.clear()
        for m in items:
            it = QListWidgetItem()
            arc_list.addItem(it)
            arc_list.setItemWidget(it, _archive_card(t, ck, m, load_arc))
            it.setSizeHint(arc_list.itemWidget(it).sizeHint())
        anote.setText(f"该会话 {len(items)} 条存档条目")

    b_arc_load.clicked.connect(load_arc)
    b_arc_reload.clicked.connect(load_arc_chats)
    load_arc_chats()

    # 原始 JSON 折叠查看
    jcard = Card(t)
    jcard.body.addWidget(h2(t, "原始返回（/api/sessions 透视）"))
    area = _plain_area(t, "", placeholder="连上后台后显示明细数据", height=160)
    jcard.body.addWidget(area)
    jnote = desc(t, "")

    def _refresh_raw() -> None:
        # 原始返回卡不再自己发请求：它展示的就是 load_sessions 拿回来的同一份数据。
        # 这里保留一个「重新拉一次」的入口（万一用户只想刷这块），仍走 load_sessions，
        # 保证两处永远同源、不会一份新一份旧。
        load_sessions()

    # 把「渲染原始 JSON」注册给 load_sessions（晚绑定：本卡在其首次调用之后才建好）
    def _render_raw(data: dict) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=1)
        area.setPlainText(text[:8000] + ("\n…（截断显示前 8000 字符）" if len(text) > 8000 else ""))
        jnote.setText(f"读取成功 · {time.strftime('%H:%M:%S')}")

    _raw_box["fn"] = _render_raw
    jcard.body.addWidget(jnote)
    lay.addWidget(jcard)

    lay.addStretch(1)
    return page


def _session_card(t: Tokens, e: dict, sync_fn) -> QWidget:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet("background:transparent;")
    h = QHBoxLayout(w)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(8)
    pick = QCheckBox()
    pick.setFixedSize(18, 18)
    pick.toggled.connect(lambda _=False: sync_fn())
    h.addWidget(pick)
    name = QLabel(str(e.get("chat_name") or e.get("chat_key") or "?"))
    name.setFont(qfont(t, 12.5, 500))
    name.setStyleSheet(f"color:{t.tx};background:transparent;")
    name.setMinimumWidth(120)
    status = QLabel(str(e.get("status") or ""))
    status.setFont(qfont(t, 11.5))
    status.setStyleSheet(f"color:{t.tx2};background:transparent;")
    ts = QLabel(str(e.get("ts") or "").replace("T", " "))
    ts.setFont(qfont(t, 11))
    ts.setStyleSheet(f"color:{t.tx3};background:transparent;")
    meta = QLabel(f"{e.get('tokens') or 0} tok · ¥{float(e.get('cost') or 0):.4f} · {e.get('latency_ms') or 0}ms")
    meta.setFont(qfont(t, 11))
    meta.setStyleSheet(f"color:{t.tx3};background:transparent;")
    h.addWidget(name)
    h.addWidget(status)
    h.addWidget(ts)
    h.addWidget(meta)
    h.addStretch(1)
    return w


def _archive_card(t: Tokens, chat_key: str, m: dict, reload_fn) -> QWidget:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet("background:transparent;")
    h = QHBoxLayout(w)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(8)
    tag = "［已屏蔽］" if m.get("blocked") else ("［已撤回］" if m.get("recalled") else "")
    txt = QLabel(f"#{m.get('id')} {(m.get('self') and '我' or (m.get('sender') or '?'))}：{tag}{m.get('text')}")
    txt.setFont(qfont(t, 11.5))
    txt.setStyleSheet(f"color:{t.tx3 if m.get('blocked') else t.tx};background:transparent;")
    txt.setWordWrap(True)
    h.addWidget(txt, 1)
    b_block = Btn("屏蔽" if not m.get("blocked") else "解除", t, "ghost")
    b_block.setFixedWidth(54)
    b_block.clicked.connect(lambda _=False: _arc_action(chat_key, m.get("id"),
                                                        "unblock" if m.get("blocked") else "block", reload_fn))
    b_del = Btn("清除", t, "ghost")
    b_del.setFixedWidth(54)
    b_del.clicked.connect(lambda _=False: _arc_action(t, b_del, chat_key, m.get("id"),
                                                      "delete", reload_fn))
    h.addWidget(b_block)
    h.addWidget(b_del)
    return w


def _arc_action(t: Tokens, parent: QWidget, chat_key: str, mid, action: str,
                reload_fn) -> None:
    api = {"block": "/api/archive/block", "unblock": "/api/archive/unblock",
           "delete": "/api/archive/delete"}.get(action)
    if not api:
        return
    # 对齐 web uiConfirm 口径（assets/console/index.html:4570）：真删存档前二次确认
    #   （屏蔽/解除可逆，web 也不确认，直接发）。
    if action == "delete":
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, parent, "真删这条存档？", f"真删第 #{mid} 条存档？不可恢复。",
            ["删除后这条记录不会留在任何回收处"], confirm_label="真删")
        if not d.exec():
            return
    _async_post(None, api, {"chat_key": chat_key, "ids": [mid]},
                lambda r, e: reload_fn())


# ---------------------------------------------------------------- 日志（真读 logs/persona_morph.log 尾部）


def log_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("log")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "动作与失败原因，出问题先看这一屏。"))
    log_path = ROOT / "logs" / "persona_morph.log"
    card = Card(t)
    head_row = QHBoxLayout()
    head_row.addWidget(h2(t, "persona_morph.log 尾部 200 行"))
    head_row.addStretch(1)
    autolog = QCheckBox("自动刷新")
    autolog.setObjectName("autolog")
    autolog.setChecked(True) # web autolog :2137 同款默认开
    head_row.addWidget(autolog)
    card.body.addLayout(head_row)
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
        except Exception as e: # noqa: BLE001
            note.setText(f"读取失败：{e}")
            badge.set("err", "读不到")

    _refresh()
    card.body.addWidget(note)
    lay.addWidget(card)
    # P0-A②：原「清空日志」按钮移除 —— web「运行日志」区根本没有清空 API
    # （日志由后端按大小滚动），残壳按钮点了永远没反应；真要看历史用「打开日志目录」。
    hooks = [_refresh, lambda: _open_dir(ROOT / "logs")]
    lay.addWidget(_btn_row(t, [("刷新", "primary"), ("打开日志目录", "ghost")], hooks))
    # web autolog :7420 同款：勾选开启时每 4s 重读尾部并贴底（关掉回到手动）
    def _tick() -> None:
        if autolog.isChecked():
            _refresh()
            sb = area.verticalScrollBar()
            sb.setValue(sb.maximum())

    log_timer = QTimer(page)
    log_timer.setInterval(4000)
    log_timer.timeout.connect(_tick)
    log_timer.start()
    lay.addWidget(desc(t, "「自动刷新」开着时每 4 秒重读并贴底；日志由产品按大小自动滚动清理，"
                          "要翻完整历史用「打开日志目录」。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 配置格式（真读 config.json + 校验写回 + 备份）


def json_panel(t: Tokens, on_save=None) -> QWidget:
    s = sec_meta.get("json")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "全部配置的配置文件。只在面板里找不到对应开关时才动它，保存前先备份。"))
    from agent.config import CONFIG_FILE # noqa: PLC0415  同进程直连（语义与网页控制台一致）

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
        except Exception as e: # noqa: BLE001
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
            except Exception: # noqa: BLE001
                pass

    _load()
    card.body.addWidget(note)
    card.body.addWidget(desc(t, "保存时自动把原文件备份成 .bak-qt-时间戳 放同目录；改坏(JSON 不合法)会拒绝写入。"))
    lay.addWidget(card)

    def _view_raw() -> None:
        # web rawJsonBtn :5614 = window.open('/api/config'+token) —— 桌面等效 = 系统默认浏览器打开
        from PySide6.QtCore import QUrl # noqa: PLC0415
        from PySide6.QtGui import QDesktopServices # noqa: PLC0415
        from addr import join_url # noqa: PLC0415
        from agent_bridge import current_url # noqa: PLC0415

        QDesktopServices.openUrl(QUrl(join_url(current_url(), "/api/config")))
        note.setText("已在浏览器打开后台当前生效的配置（/api/config）· " + time.strftime("%H:%M:%S"))

    hooks = [_save, _load, lambda: _open_dir(cfg_path.parent), _view_raw]
    lay.addWidget(_btn_row(t, [("保存全部设置", "primary"), ("重新读取", "ghost"),
                               ("打开配置目录", "ghost"), ("新窗口查看配置", "ghost")], hooks))
    lay.addWidget(desc(t, "保存后需重启才能完全生效的部分：模型/人设/白名单等；界面与光标类即时生效。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 分发表（panels_qt.build_panel 查这里）

def vermat_panel(t: Tokens, on_save=None) -> QWidget:
    """ P0-3：版本能力矩阵（web `sec-vermat` 真值）+ 顶部「版本与更新」卡。

    两件事：
      3a. **矩阵三态**（allowed / 实测 / 严格档拦停，web assets/console/index.html:3346 口径）——
          从 `/api/status` 的 `version_gate` + `version` 取真值，绝不显示「检测中」占位。
      3b. **版本与更新卡**—— 顶栏胶囊点「稍后」
          只关 popover、不等于不再提示；这一张卡**常驻**，显示当前版本 / 有无新版 /
          「立即更新」入口，复用 `updbar` 的判定与动作，不重写一套。
    """
    s = sec_meta.get("vermat")
    page, lay, badge = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "当前「微信版本 × 适配层版本」下每个能力的实测状态。"))

    # ── 3b. 版本与更新──
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
        import threading # noqa: PLC0415

        box: dict = {}

        def _work() -> None:
            try:
                box["v"] = config_io.get_json("/api/update", timeout=8.0)
            except Exception: # noqa: BLE001
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
            import updbar # noqa: PLC0415

            txt, warn = updbar.decide(v)
        except Exception: # noqa: BLE001
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
                from agent_bridge import post_json # noqa: PLC0415

                post_json("/api/update_apply", {}, timeout=20.0)
            except Exception: # noqa: BLE001
                pass

        import threading # noqa: PLC0415

        threading.Thread(target=_work, daemon=True, name="vermat-apply").start()
        vline.setText("已发起更新；进度看顶栏胶囊（失败会在这里如实说明）。")

    def _on_check() -> None:
        vline.setText("检查中…")
        _fetch(_render_upd)

    btn_go.clicked.connect(_on_go)
    btn_check.clicked.connect(_on_check)
    page._c10_update_refresh = _fetch # Shell 探活可复用（卡内自足也能跑）

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

    # ── 接管与指纹（web vermat sec：图标指纹取/丢 + 版本不匹配拍板入口 + 一键修复）──
    tk_card = Card(t)
    tk_card.body.addWidget(h2(t, "接管与指纹"))
    tk_card.body.addWidget(desc(t, "图标指纹帮机器人点准微信的菜单和按钮；微信窗口大小/主题变了就重新取一次。"
                                   "丢掉旧指纹后只剩「放行但留痕」（不会再拦「点错」），要重新取才有新指纹。"))
    tk_note = QLabel("")
    tk_note.setFont(qfont(t, t.body_size - 1))
    tk_note.setWordWrap(True)
    tk_note.setStyleSheet(f"color:{t.tx3};background:transparent;")
    tk_row = QHBoxLayout()
    btn_take = Btn("重新取指纹", t, "ghost")
    btn_forget = Btn("丢掉旧指纹", t, "ghost")
    btn_pd = Btn("版本不匹配怎么办", t, "ghost")
    btn_pd.setObjectName("tkPendingDecisions")
    btn_heal = Btn("依赖自愈", t, "ghost")
    btn_up = Btn("升级适配层", t, "ghost")
    for b in (btn_take, btn_forget, btn_pd):
        tk_row.addWidget(b)
    tk_row.addStretch(1)
    tk_row2 = QHBoxLayout()
    tk_row2.addWidget(btn_heal)
    tk_row2.addWidget(btn_up)
    tk_row2.addStretch(1)
    tk_card.body.addLayout(tk_row)
    tk_card.body.addLayout(tk_row2)
    tk_card.body.addWidget(tk_note)
    lay.addWidget(tk_card)

    def _on_take() -> None:
        from agent_bridge import post_json # noqa: PLC0415

        btn_take.setEnabled(False)
        btn_take.setText("取指纹中…")

        def _run() -> str:
            r = post_json("/api/ui_fingerprint/take", {}, timeout=60.0) or {}
            res = r.get("result") or {}
            ok_n = len(res.get("ok") or [])
            fail_n = len(res.get("failed") or [])
            if ok_n:
                return ("取到 %d 条指纹" % ok_n) + (("，%d 条没取到" % fail_n) if fail_n else "")
            return ("一条也没取到：%s（微信窗口可能被最小化/遮住）"
                    % ((res.get("failed") or [""])[0] or ""))

        _post_action_raw(btn_take, tk_note, _run, "取指纹中…",
                         done=lambda: (btn_take.setEnabled(True),
                                       btn_take.setText("重新取指纹")))

    def _on_forget() -> None:
        from agent_bridge import post_json # noqa: PLC0415

        dlg = ConfirmDialog(t, page, "丢掉旧指纹", "丢掉全部旧指纹？",
                            ["丢完就只剩「放行但留痕」（不会再拦「点错」）",
                             "要重新点「重新取指纹」才有新指纹"],
                            "丢掉旧指纹", "先不丢", dangerous=True)
        if not dlg.exec():
            return

        def _run() -> str:
            post_json("/api/ui_fingerprint/forget", {}, timeout=30.0)
            return "旧指纹已丢掉"

        _post_action_raw(btn_forget, tk_note, _run, "丢掉中…")

    def _on_pd() -> None:
        def _run() -> str:
            r = config_io.get_json("/api/status", timeout=15.0) or {}
            pd = r.get("pending_decisions") or {}
            item = pd.get("item")
            if item:
                return ("有待拍板的事：%s——微信 %s × 适配层 %s（可到网页控制台按提示四选一）"
                        % (item.get("title") or item.get("reason") or "版本适配",
                           item.get("wechat") or "?", item.get("adapter") or "?"))
            return "现在没有待拍板的事。"

        _post_action_raw(btn_pd, tk_note, _run, "读取待决台账…")

    def _on_pd_web() -> None:
        # 拍板四选一只在网页控制台有（Qt 只读展示）——给可点入口，不补一套四选一 UI
        # ⚠️ fragment 不能交给 join_url 的 path 槽位（会拼成 /#sec-version?token=tk，
        #   fragment 落在 query 前）：浏览器把 ?token 一并归入 fragment ⇒ GET / 请求
        #   就不带 token（无 Cookie ⇒ 401；有 Cookie ⇒ 页面开了但锚名匹配不上
        #   section id，跳转照样失效）。正确形态：控制台 URL（token 已在 query）
        #   原样 + 直接追加 #fragment 收尾。
        from PySide6.QtCore import QUrl # noqa: PLC0415
        from PySide6.QtGui import QDesktopServices # noqa: PLC0415
        from agent_bridge import current_url # noqa: PLC0415

        QDesktopServices.openUrl(QUrl(str(current_url() or "").strip() + "#sec-version"))
        tk_note.setText("已在浏览器打开网页控制台（四选一在那里拍）· " + time.strftime("%H:%M:%S"))

    def _on_heal() -> None:
        from agent_bridge import post_json # noqa: PLC0415

        def _run() -> str:
            r = post_json("/api/version/action", {"choice": "update_host"}, timeout=30.0) or {}
            return str(r.get("message") or "依赖自愈已发出")

        _post_action_raw(btn_heal, tk_note, _run, "请求发送中…")

    def _on_up() -> None:
        from agent_bridge import post_json # noqa: PLC0415

        def _run() -> str:
            r = post_json("/api/version/action", {"choice": "upgrade_adapter"}, timeout=30.0) or {}
            return str(r.get("message") or "升级适配层已发出")

        _post_action_raw(btn_up, tk_note, _run, "请求发送中…")

    btn_take.clicked.connect(_on_take)
    btn_forget.clicked.connect(_on_forget)
    btn_pd.clicked.connect(_on_pd)
    btn_pd.clicked.connect(_on_pd_web) # 同一个按钮：读台账之外顺手给「去网页拍板」入口
    btn_heal.clicked.connect(_on_heal)
    btn_up.clicked.connect(_on_up)

    def _render_mtx() -> None:
        try:
            import panels_qt # noqa: PLC0415

            st = panels_qt._load_status()
        except Exception: # noqa: BLE001
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
        # web vmAllow（:7594-7601）：GET /api/version/allow 写「本次允许发送」的
        # 会话期放行（重启失效，不落配置）；成败都如实回显，不假成功。
        bx: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            try:
                bx["r"] = config_io.get_json("/api/version/allow", timeout=15.0)
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        import threading # noqa: PLC0415

        threading.Thread(target=_work, daemon=True, name="vermat-allow").start()
        gate_lb.setText("放行请求发送中…")
        gate_lb.setStyleSheet(f"color:{t.tx3};background:transparent;")

        def _apply() -> None:
            if not bx["done"]:
                QTimer.singleShot(150, _apply)
                return
            if bx["err"]:
                gate_lb.setText("放行失败：%s" % bx["err"])
                gate_lb.setStyleSheet(f"color:{t.err};background:transparent;")
                return
            gate_lb.setText("已放行（只对本次运行有效）：发送会按未验证版本对继续，"
                            "出问题就在本页点「升级适配层」（产品后台自己装）。")
            gate_lb.setStyleSheet(f"color:{t.ok};background:transparent;")

        QTimer.singleShot(150, _apply)

    btn_allow.clicked.connect(_on_allow)
    page._c10_mtx_refresh = _render_mtx
    _render_mtx()

    # 更新卡异步拉一次（不阻塞面板构建）
    _fetch(_render_upd)
    return page


# ================================================================ 人设 / 记忆 / 运行明细
#
# 这三个面板在 web 侧核心是「动态列表」——由 JS 从 /api/personas / /api/memory
# / /api/sessions 拉取后再渲染进 #personaList / #memTable / #sessList，静态 HTML
# 里没有这些列表，元数据驱动（sec_meta/_cfg_panel）永远做不出来 ⇒ 必须手写挂 MANUAL。
# 所有后端接口名**照抄** agent/console_html.py 里的 JS 绑定（见各函数出处注释），
# 不自己编接口、不造死按钮。

# ---------------------------------------------------------------- 公共小工具（仅本文件内用）


def _bordered_list(t: Tokens, name: str, min_h: int = 200) -> QListWidget:
    """一个带边框、可滚的动态列表容器；objectName 固定，便于自检按名取证。

    ⛔ 横向滚动条强制关闭 + resizeMode=Adjust（：item 用 sizeHint 全宽时
    会把行撑出视口 ⇒ 右侧按钮被切掉、要手动拉宽窗口）。行宽一律=视口宽，
    行内自己用 sizePolicy 决定谁先被压缩。"""
    lw = QListWidget()
    lw.setObjectName(name)
    lw.setMinimumHeight(min_h)
    lw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    lw.setResizeMode(QListView.ResizeMode.Adjust)
    lw.setStyleSheet(
        f"QListWidget{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_field}px;padding:4px;}}"
        f"QListWidget::item{{border-bottom:1px solid {t.bd};padding:2px 0;}}"
    )
    return lw


def _ui_alive(w: object) -> bool:
    """异步回调落地前问一次「那个控件还在吗」。

    面板被关掉、或主题重建（`_rebuild` 会把整棵页栈 `setParent(None)` + `deleteLater`）
    之后，回调里再取控件数据会抛 `RuntimeError: Internal C++ object already deleted`。
    在 PySide6 里那是**未捕获异常**：打整段栈，视版本还会直接终止应用，而用户看到的
    只是「面板用着用着整个没了」。传 None 表示调用方没绑定控件（此时靠 `_deliver` 兜底）。
    """
    if w is None:
        return True
    try:
        import shiboken6 # noqa: PLC0415

        return bool(shiboken6.isValid(w))
    except Exception: # noqa: BLE001 — 拿不到 shiboken 就当它活着，由 _deliver 兜底
        return True


def _deliver(on_done, *args) -> None: # noqa: ANN001
    """把后台结果交给 UI 回调；回调途中控件已被销毁就丢弃这一次落地。

    丢弃是正确行为：那个面板已经不在了，没有任何界面需要更新（也没有地方报错）。
    """
    try:
        on_done(*args)
    except RuntimeError: # 控件已销毁
        pass


def _async_post(page: QWidget, api: str, body: dict, on_done, timeout: float = 120.0) -> None:
    """后台线程 POST（对齐 web 同款接口），UI 只在主线程落地（box 模式）。"""
    import threading # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    box: dict = {"done": False, "r": None, "err": None}

    def _work() -> None:
        try:
            box["r"] = post_json(api, body, timeout=timeout)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    threading.Thread(target=_work, daemon=True, name="c12-post").start()

    def _apply() -> None:
        if not box["done"]:
            if not _ui_alive(page):
                return # 面板没了就别再轮询（否则是一条没有终点的自链）
            QTimer.singleShot(300, _apply)
            return
        if not _ui_alive(page):
            return
        _deliver(on_done, box.get("r"), box.get("err"))

    QTimer.singleShot(300, _apply)


def _read_fail_hint(err: object) -> str:
    """把「连不上」的底层异常翻成一句用户能据此行动的话。

    为什么要分：`get_json` 失败时统一返回 None，而 None 的原因至少有三种，
    用户能做的事完全不同 —— 混成一句「后台没连上」会把人引向错误的排查方向：
      · 超时（handle 慢/正忙）→ 等一会儿重试就行；
      · 拒连（进程没起/端口变了）→ 去看顶部状态灯，多半是后台没跑；
      · 其它（接口不存在/返回不是 JSON）→ 说清是"这个接口没给出数据"。
    """
    s = str(err or "").lower()
    if "timed out" in s or "timeout" in s:
        return "后台响应超时（可能正在忙）。稍等几秒点「刷新」再试。"
    if "refused" in s or "connection" in s or "unreachable" in s or "10061" in s:
        return "后台没连上（进程可能没起来）。看顶部状态灯，恢复绿色后点「刷新」再试。"
    if "404" in s or "405" in s or "501" in s:
        return "后台这个接口没有给出数据（版本可能不一致）。"
    return "后台没连上（或该接口未提供）。顶部状态灯恢复绿色后点「刷新」再试。"


def _async_post_seq(page: QWidget, api: str, bodies: list, on_done,
                    timeout: float = 30.0) -> None:
    """**顺序**提交多条 POST，全部结束后一次回执（box 模式，同 `_async_post`）。

    为什么需要它：web 侧「清除勾选的印象」（`assets/console/index.html:6986-6990`）是
    **逐个成员单独 POST**、每次只带一个 `user_id` —— 因为后端
    （`webui.py::_rapi_memory_post`）只认单值 `user_id`，没有批量协议。
    面板要跟 web 同构就只能逐条发；逐条发又不能让 UI 线程等，所以放一个后台线程里
    **串行**跑完，再回主线程给**一次**汇总回执。

    与「并发多发」的区别：这里刻意串行 —— 记忆删除是写盘操作，并发会撞同一批文件；
    且总条数就是用户勾选数（个位数），串行的耗时可忽略。

    on_done 收 `(results, first_err)`；results 为各条响应（None 表示该条没拿到）。
    """
    import threading # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    box: dict = {"done": False, "results": [], "err": None}

    def _work() -> None:
        try:
            for b in bodies:
                box["results"].append(post_json(api, b, timeout=timeout))
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    threading.Thread(target=_work, daemon=True, name="c12-post-seq").start()

    def _apply() -> None:
        if not box["done"]:
            if not _ui_alive(page):
                return
            QTimer.singleShot(300, _apply)
            return
        if not _ui_alive(page):
            return
        _deliver(on_done, box.get("results") or [], box.get("err"))

    QTimer.singleShot(300, _apply)


def _async_get(api: str, on_done, timeout: float = 30.0,
               page: QWidget | None = None) -> None:
    """后台线程 GET（_async_post 同款 box 模式）——预览类只读接口用。

    `page` 可选：绑定了面板就顺带做存活判（面板没了就不再轮询/落地）。
    """
    import threading # noqa: PLC0415

    box: dict = {"done": False, "r": None, "err": None}

    def _work() -> None:
        try:
            box["r"] = config_io.get_json(api, timeout=timeout)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    threading.Thread(target=_work, daemon=True, name="c12-get").start()

    def _apply() -> None:
        if not box["done"]:
            if not _ui_alive(page):
                return
            QTimer.singleShot(300, _apply)
            return
        if not _ui_alive(page):
            return
        _deliver(on_done, box.get("r"), box.get("err"))

    QTimer.singleShot(300, _apply)


def _post_chain(seq: list, on_done, timeout: float = 15.0,
                page: QWidget | None = None) -> None:
    """后台线程按序 POST 多个请求，UI 只在主线程落地（box 模式，_async_post 同款）。

    用途：「添加角色到新分区」=先落分区再落角色（web :6356-6361 两连跳同款）；
    任一步失败即停，全部完成后回调 on_done(最后响应, 错误)。

    `page` 可选：绑定了面板就顺带做存活判（面板没了就不再轮询/落地）。
    """
    import threading # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    box: dict = {"r": None, "err": None, "done": False}

    def _work() -> None:
        for api, body in seq:
            try:
                r = post_json(api, body, timeout=timeout)
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
                break
            box["r"] = r
            if isinstance(r, dict) and r.get("ok") is False:
                box["err"] = r.get("error") or "请求被拒绝"
                break
        box["done"] = True

    threading.Thread(target=_work, daemon=True, name="c12-post-chain").start()

    def _apply() -> None:
        if not box["done"]:
            if not _ui_alive(page):
                return
            QTimer.singleShot(300, _apply)
            return
        if not _ui_alive(page):
            return
        _deliver(on_done, box.get("r"), box.get("err"))

    QTimer.singleShot(300, _apply)


def _append_save(t: Tokens, lay, binds: list, badge: Badge) -> None:
    """保存行：收集 {点路径:值}
    → config_io.write_patch；并带「改完即生效」开关（防抖 600ms 自动写）。"""
    from PySide6.QtCore import QSettings, QTimer as _QTimer # noqa: PLC0415

    brow = QHBoxLayout()
    btn = Btn("保存设置", t, "primary")
    note = QLabel("改完点保存 → 写入 config.json（与网页控制台同一份）")
    note.setFont(qfont(t, 12.5))
    note.setStyleSheet(f"color:{t.tx3};background:transparent;")

    def _collect() -> dict:
        patch: dict = {}
        for cfg, ctrl, kind in binds:
            try:
                if kind == "checkbox":
                    patch[cfg] = bool(ctrl.isChecked())
                elif kind == "number":
                    s = ctrl.text().strip()
                    if s:
                        try:
                            f = float(s)
                            patch[cfg] = int(f) if f == int(f) else f
                        except ValueError:
                            pass
                elif kind == "select":
                    patch[cfg] = str(ctrl.currentData())
                elif kind == "textarea":
                    patch[cfg] = ctrl.toPlainText()
                elif kind == "groups":
                    # 多选勾选容器（共享群）：勾中的群名列表，对齐 web
                    # syncMemGroupsToCfg（assets/console/index.html:5993）——只收选中项，
                    # 全不勾 = 空列表（= 用上方「共享记忆池」总开关）。
                    names: list[str] = []
                    for cb in ctrl.findChildren(QCheckBox):
                        if cb.isChecked():
                            nm = cb.property("gname")
                            if nm:
                                names.append(str(nm))
                    patch[cfg] = names
                else:
                    patch[cfg] = ctrl.text()
            except Exception: # noqa: BLE001
                pass
        return patch

    def _feedback(ok: bool, msg: str) -> None:
        note.setText(("已保存：" if ok else "没保存成：") + msg + f"  ·  {time.strftime('%H:%M:%S')}")
        note.setStyleSheet(f"color:{t.ok if ok else t.err};background:transparent;")
        if ok:
            badge.set("ok", "已保存")
        else:
            badge.set("err", "没保存成")

    def _do_save() -> None:
        patch = _collect()
        if not patch:
            _feedback(False, "没有要保存的改动")
            return
        ok, msg = config_io.write_patch(patch)
        _feedback(ok, msg)

    btn.clicked.connect(_do_save)

    auto_chk = Switch(t, bool(QSettings("WXAgent", "persona-morph-ui").value("auto_apply", True, type=bool)))
    auto_lbl = QLabel("改完即生效")
    auto_lbl.setFont(qfont(t, 12))
    auto_lbl.setStyleSheet(f"color:{t.tx3};background:transparent;")
    debounce = _QTimer(None)
    debounce.setSingleShot(True)
    debounce.setInterval(600)

    def _auto_write() -> None:
        patch = _collect()
        if not patch:
            return
        ok, msg = config_io.write_patch(patch)
        _feedback(ok, msg)

    debounce.timeout.connect(_auto_write)

    def _mark_dirty() -> None:
        if auto_chk.isChecked():
            debounce.start()

    # 把防抖钩子暴露给宿主页面 —— 追加区/手写控件（如共享群勾选框 memGroupsBox）
    # 不在 binds 里，靠这个钩子并入同一条「改完即生效」链。
    _host = lay.parentWidget() if lay is not None else None
    if _host is not None:
        _host._c12_mark_dirty = _mark_dirty
        _host._c12_auto_on = lambda: bool(auto_chk.isChecked()) # noqa: PLW0108

    for _cfg, ctrl, kind in binds:
        if ctrl is None:
            continue
        if kind == "checkbox":
            ctrl.toggled.connect(_mark_dirty)
        elif kind == "select":
            ctrl.currentIndexChanged.connect(_mark_dirty)
        elif kind == "textarea":
            ctrl.textChanged.connect(_mark_dirty)
        elif kind == "groups":
            # 多选勾选容器：勾选框由 load_groups 在后台返回后动态增删，
            # 建面板时容器里还是占位 ⇒ 不走这里的静态遍历，改由 load_groups
            # 在创建每个勾选框时就地挂 _mark_dirty（经 _mg_box 晚绑定转发）。
            pass
        else:
            ctrl.editingFinished.connect(_mark_dirty)

    def _on_auto(on: bool) -> None:
        QSettings("WXAgent", "persona-morph-ui").setValue("auto_apply", bool(on))
        if on:
            note.setText("改完即生效：已开启（改动自动写入 config.json）")
            debounce.start()
        else:
            note.setText("已关闭自动生效：改完请点「保存设置」")
            note.setStyleSheet(f"color:{t.tx3};background:transparent;")

    auto_chk.toggled.connect(_on_auto)

    brow.addWidget(btn)
    brow.addWidget(auto_chk)
    brow.addWidget(auto_lbl)
    brow.addWidget(note, 0, Qt.AlignmentFlag.AlignVCenter)
    brow.addStretch(1)
    lay.addLayout(brow)


# ---------------------------------------------------------------- 人设面板（P0-A：列表 / 搜索 / 排序 / 评分）


def persona_panel(t: Tokens) -> QWidget:
    """人设与响应 —— 一次解掉的四件事
      · 人设列表：读 /api/personas（照 assets/console/index.html:6180 同款接口，合并 custom/scores），
        逐张卡：星标 / 人设名 / 模型分 / 使用 / 删除（web #personaList :1318）。
      · 搜索框：personaSearch（:1317）—— textChanged 实时过滤列表（对齐 web L6259）。
      · 排序按钮：pSort WPS 三态（高→低 / 低→高 / 默认，:1312）+ pSortOff（:1314）
        + pRestorePrev 恢复上个人设（:1315）。
      · 评分三按钮：pScoreLLM（:1328 → POST /api/persona/score）、pEnrich
        （:1329 → POST /api/persona/ai-enrich）、pWebFetch（:1330 →
        POST /api/persona/web-fetch）—— 全部**真接后端**，结果回显 pScoreRst（:1336）。
      下方叠可写配置（人设名 / 参与度 / 角色文本 / 系统提示词补充 / 三个模块开关全要 /
      撤回后剔除 recallRows / 主动开话题 proactiveRows），复用 panels_qt 的控件 + config_io 落盘。
    """
    s = sec_meta.get("persona")
    page, lay, badge = _page(t, s.title, "idle", "读取中")
    lay.addWidget(desc(t, s.desc or "机器人以谁的身份在群里说话、怎么参与。改完保存即生效。"))

    from PySide6.QtWidgets import QHBoxLayout, QWidget # noqa: PLC0415

    # cat=当前分区过滤（空=全部）；built=内置分区名（/api/persona/cats 的 built）；
    # user_cats=用户自建分区 {name: desc}（同接口的 user 列，web :6084-6088 同款）
    state: dict = {"items": [], "sort": 0, "cat": "", "built": [], "user_cats": {}}

    # ── 人设库卡 ──
    pcard = Card(t)
    pcard.body.addWidget(h2(t, "人设库（/api/personas）"))

    # 按钮排：排序 / 恢复默认 / 恢复上个人设（对齐 web 人设选单 btns :1311-1316）
    btn_row = FlowBox(t, spacing=6)
    b_sort = Btn("↓ 按评估分数排序", t, "ghost")
    b_sort.setObjectName("pSort")
    b_sort_off = Btn("恢复默认顺序", t, "ghost")
    b_sort_off.setObjectName("pSortOff")
    b_restore = Btn("恢复上个人设", t, "ghost")
    b_restore.setObjectName("pRestorePrev")
    btn_row.add(b_sort)
    btn_row.add(b_sort_off)
    btn_row.add(b_restore)
    pcard.body.addWidget(btn_row)

    # 分区 chips（web #personaCats :1308，allCats/renderChips :6199-6231 同款）——
    # 人设按分区过滤（内置默认「网络热门」，自定义默认「自定义」）；
    # 分区管理（新建/删除，/api/persona/cats*）列。
    # ⛔ 用 FlowBox 而非 QHBoxLayout：分区多了以后一行放不下时，QHBoxLayout 会把
    #   每个 chip 压到最小宽 —— 几个 chip 直接叠在一起（实测「＋ 新建/添加」压在
    #   「女神异闻录」上面）。FlowBox 一行放不下就折行，宽度自适应必须落到这儿。
    cat_row_w = FlowBox(t, spacing=6)
    pcard.body.addWidget(cat_row_w)

    # 搜索框（personaSearch :1317）
    from panels_qt import _line # noqa: PLC0415
    search = _line(t, "", placeholder="搜索人设（如 傲娇/毒舌/猫/程序员）…")
    search.setObjectName("personaSearch")
    pcard.body.addWidget(search)

    listw = _bordered_list(t, "personaList", 220)
    pcard.body.addWidget(listw)
    pcard.body.addWidget(desc(t, "星标=收藏置顶；排序按模型评估分。点「使用」即切换当前人设（重启机器人后生效）。"))
    lay.addWidget(pcard)

    def _refit_items() -> None:
        """条目宽 = 视口宽（右侧顶格）。

        ⛔ QListWidget 的 itemWidget 矩形来自**条目 sizeHint**——宽度设 0 整行
        零宽不可见；设成 sizeHint 全宽又会撑出
        视口（右侧按钮被切、要手动拉宽窗口）。⇒ 宽度必须实时跟随视口，
        视口 Resize 时逐条重设（事件过滤器挂在 viewport 上）。"""
        wv = listw.viewport().width()
        if wv <= 8:
            return
        for i in range(listw.count()):
            it = listw.item(i)
            it.setSizeHint(QSize(wv, max(44, it.sizeHint().height())))

    from PySide6.QtCore import QEvent as _QEvent, QObject as _QObj # noqa: PLC0415

    class _VpFit(_QObj):
        def eventFilter(self, obj, ev): # noqa: ANN001, N802
            if ev.type() == _QEvent.Type.Resize:
                _refit_items()
            return False

    listw.viewport().installEventFilter(_VpFit(listw))

    pnote = desc(t, "")
    lay.addWidget(pnote)

    def _render() -> None:
        items = state["items"]
        q = (search.text() or "").strip().lower()
        cat = state.get("cat", "")

        def _pcat(p: dict) -> str:
            # 对齐 web allCats（L6201-6202）：内置默认「网络热门」，custom 默认「自定义」
            return p.get("cat") or ("自定义" if str(p.get("key") or "").startswith("custom") else "网络热门")

        show = [p for p in items
                if (not cat or _pcat(p) == cat)
                and (not q or q in (p.get("name") or "").lower()
                     or q in (p.get("key") or "").lower()
                     or q in (p.get("text") or "").lower())]
        if state["sort"] == 1: # 高→低
            show = sorted(show, key=lambda p: -(p.get("__score") or 0))
        elif state["sort"] == 2: # 低→高
            show = sorted(show, key=lambda p: (p.get("__score") or 0))
        show = sorted(show, key=lambda p: 0 if p.get("fav") else 1) # 星标置顶
        listw.clear()
        for p in show:
            it = QListWidgetItem()
            listw.addItem(it)
            wdg = _persona_card(t, p, handlers)
            listw.setItemWidget(it, wdg)
            # 条目宽 = 视口宽（零宽不可见 / 全宽撑出视口，两个方向都是坑，见 _refit_items）
            it.setSizeHint(QSize(max(listw.viewport().width(), 1),
                                 max(44, wdg.sizeHint().height())))
        _refit_items()
        # 空表必须说清「为什么空」：读不到（控制台没起/超时）≠ 真的没有人设。
        # 旧口径一句 badge「暂无人设」把两类情况混成一句 ⇒ 用户以为数据没了。
        if items:
            badge.set("info", f"{len(items)} 个")
            pnote.setText("")
        elif state.get("load_err"):
            badge.set("warn", "读不到")
            pnote.setText("人设列表读不到：" + _read_fail_hint(state.get("load_err")))
        else:
            badge.set("info", "暂无人设")
            pnote.setText("")

    def set_personas(items: list) -> None:
        state["items"] = list(items or [])
        _render_cats()
        _render()

    def apply_sort() -> None:
        state["sort"] = (state["sort"] + 1) % 3
        lbl = {0: "↓ 按评估分数排序", 1: "↑ 按评估分数排序（高→低）",
               2: "↑ 按评估分数排序（低→高）"}.get(state["sort"], "↓ 按评估分数排序")
        b_sort.setText(lbl)
        _render()

    def _render_cats() -> None:
        """分区 chips（web renderChips :6101 同款）：「全部」+ 各分区，点击过滤。
        用户分区（非内置、非「自定义」）chip 右侧带红 × 删除（web :6111-6130）；
        行尾「＋ 新建/添加」开新建分区/添加角色弹窗（web pCatAdd :1252）。"""
        cat_row_w.clear()
        cats: list = []
        for p in state["items"]:
            c = p.get("cat") or ("自定义" if str(p.get("key") or "").startswith("custom") else "网络热门")
            if c not in cats:
                cats.append(c)
        for c in list(state.get("user_cats", {}).keys()):
            if c not in cats:
                cats.append(c)
        built = state.get("built") or []
        for name in ["全部"] + cats:
            # ⛔ 分区名可能是用户自建的长名字（或模型生成的长分区）。给 chip 一个
            #   上限宽 + 尾部省略号，超长分区名才不会一颗吃掉半行、把别的 chip
            #   挤到换行之外（tooltip 存全文，鼠标一停能看到完整名）。
            label = name if len(name) <= 14 else ""
            chip = Btn(label, t, "ghost")
            if not label:
                chip = Btn(name[:12] + "…", t, "ghost")
            chip.setToolTip(name)
            chip.setMaximumWidth(180)
            cur = state.get("cat", "")
            active = (name == "全部" and not cur) or (name == cur and name != "全部")
            # ⛔ 这一句 setStyleSheet 会**整体替换** Btn 自带的 QSS（构造时写入的
            #    ghost 字色没了）⇒ 文字色落到"最近带样式表的祖先/系统回退"——
            #    浅色模式下渲染成白字白底，分区名整个看不见（；emoji 能
            #    看见是因为 emoji 字形自带颜色，恰好掩盖了"字没了"）。
            #    修法：替换时**显式带上主题色**（与 web renderChips 的 var(--tx2/--tx) 同口径）。
            chip.setStyleSheet(f"color:{t.tx if active else t.tx2};background:transparent;"
                               "border-radius:14px;padding:2px 12px;"
                               + ("font-weight:700;" if active else ""))
            chip.clicked.connect(lambda _=False, n=name: _pick_cat(n))
            # 与 web 同判据：非内置且名字不含「自定义」的分区才给 ×
            if name != "全部" and name not in built and "自定义" not in name:
                cell = QWidget()
                cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                cell.setStyleSheet("background:transparent;")
                cell_l = QHBoxLayout(cell)
                cell_l.setContentsMargins(0, 0, 0, 0)
                cell_l.setSpacing(0)
                cell_l.addWidget(chip)
                x = Btn("×", t, "ghost")
                x.setObjectName("pCatDel")
                x.setStyleSheet(f"color:{t.err};border:none;background:transparent;padding:0 2px;")
                x.setFixedWidth(18)
                x.setToolTip(f"删除分区「{name}」（分区下的自定义卡会移回 自定义）")
                x.clicked.connect(lambda _=False, n=name: _del_cat(n))
                cell_l.addWidget(x)
                cat_row_w.add(cell)
            else:
                cat_row_w.add(chip)
        b_add = Btn("＋ 新建/添加", t, "ghost")
        b_add.setObjectName("pCatAdd")
        b_add.setToolTip("新建分区，或添加角色到分区")
        b_add.clicked.connect(_open_add_dialog)
        cat_row_w.add(b_add)

    def _pick_cat(n: str) -> None:
        state["cat"] = "" if n == "全部" else n
        _render_cats()
        _render()

    def _del_cat(name: str) -> None:
        """删除用户分区（web :6119-6128 同款链路）——先主题化二次确认，再 POST。"""
        dlg = ConfirmDialog(
            t, page, "删除分区", f"删除分区「{name}」？",
            ["其中的自定义角色会自动移回「自定义」", "分区本身会被移除（不能撤销）"],
            confirm_label="删除")
        if not dlg.exec():
            return
        pnote.setText(f"删除分区「{name}」中…")
        _async_post(None, "/api/persona/cats/del", {"name": name},
                    lambda r, e: _del_cat_done(name, r, e))

    def _del_cat_done(name: str, r, e) -> None:
        if e or not isinstance(r, dict) or r.get("ok") is False:
            pnote.setText(f"删除失败：{e or (r or {}).get('error') or '后台没连上'}")
            return
        state.get("user_cats", {}).pop(name, None)
        if state.get("cat") == name:
            state["cat"] = ""
        pnote.setText(f"分区「{name}」已删除")
        load_personas()

    # 打星按钮已按移除（「打星这个没用的按钮去掉」）——原 _rate_persona
    # 走 /api/personas/rate 的路径一并删除，handlers 不再含 rate。

    def _open_add_dialog() -> None:
        """新建分区 / 添加角色（web pCatAdd 弹窗 :6286-6372 的主题化移植）。
        分区下拉=已有分区+「自定义…」；添加角色到新分区时先落分区再落角色。"""
        from PySide6.QtWidgets import QDialog # noqa: PLC0415

        from widgets import drag_dialog_cls # noqa: PLC0415

        dlg = drag_dialog_cls("PAddDialog")(page)
        dlg.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.setModal(True)
        dlg.enable_drag() # 可拖动
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        shell.setObjectName("PAddCard")
        shell.setStyleSheet(f"#PAddCard{{background:{t.card};border:1px solid {t.bd};"
                            f"border-radius:{t.radius_card + 2}px;}}")
        outer.addWidget(shell)
        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 22, 24, 18)
        box.setSpacing(10)

        tl = QLabel("新建分区 / 添加到分区")
        tl.setFont(qfont(t, 16, 600))
        tl.setStyleSheet(f"color:{t.tx};background:transparent;")
        box.addWidget(tl)

        combo_style = (
            f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
            f"QComboBox::drop-down{{border:none;width:22px;}}"
            f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
            f"color:{t.tx};border:1px solid {t.bd};}}")
        line_style = (
            f"QLineEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
            f"QLineEdit:focus{{border:1px solid {t.blue};}}")

        def _fl(txt: str) -> QLabel:
            lb = QLabel(txt)
            lb.setFont(qfont(t, 12.5))
            lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
            return lb

        def _le(ph: str) -> QLineEdit:
            e = QLineEdit()
            e.setPlaceholderText(ph)
            e.setFixedHeight(32)
            e.setFont(qfont(t, t.body_size))
            e.setStyleSheet(line_style)
            return e

        box.addWidget(_fl("类型"))
        type_sel = QComboBox()
        type_sel.addItems(["新建分区", "添加角色到分区"])
        type_sel.setFixedHeight(32)
        type_sel.setFont(qfont(t, t.body_size))
        type_sel.setStyleSheet(combo_style)
        box.addWidget(type_sel)

        box.addWidget(_fl("分区名"))
        cat_sel = QComboBox()
        cat_sel.setFixedHeight(32)
        cat_sel.setFont(qfont(t, t.body_size))
        cat_sel.setStyleSheet(combo_style)
        cat_in = _le("自定义分区名（如 我的游戏）")
        cat_wrap = QWidget()
        cat_wrap_l = QHBoxLayout(cat_wrap)
        cat_wrap_l.setContentsMargins(0, 0, 0, 0)
        cat_wrap_l.setSpacing(6)
        cat_wrap_l.addWidget(cat_sel, 1)
        cat_wrap_l.addWidget(cat_in, 1)
        box.addWidget(cat_wrap)

        box.addWidget(_fl("分区描述（可选）"))
        desc_in = _le("这分区的角色都是什么")
        box.addWidget(desc_in)

        name_lb = _fl("角色名")
        name_in = _le("角色名")
        text_lb = _fl("角色文本")
        text_in = QPlainTextEdit()
        text_in.setPlaceholderText("角色设定（会交补足引擎+评分）")
        text_in.setMinimumHeight(90)
        text_in.setFont(qfont(t, t.body_size))
        text_in.setStyleSheet(
            f"QPlainTextEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_field}px;padding:4px 10px;}}")
        for w in (name_lb, name_in, text_lb, text_in):
            w.setVisible(False)
            box.addWidget(w)

        built0 = list(state.get("built") or [])
        user0 = [n for n in state.get("user_cats", {}).keys() if n not in built0]
        known = built0 + user0
        if known:
            box.addWidget(_fl("已有分区：" + "、".join(known)))

        brow = QHBoxLayout()
        brow.addStretch(1)
        b_cancel = Btn("取消", t, "ghost")
        b_ok = Btn("创建", t, "primary")
        b_ok.setObjectName("pAddOk")
        brow.addWidget(b_cancel)
        brow.addWidget(b_ok)
        box.addLayout(brow)

        def _cat_opts() -> None:
            cat_sel.clear()
            for c in known:
                cat_sel.addItem(c)
            cat_sel.addItem("自定义…")
            cat_sel.setItemData(cat_sel.count() - 1, "__custom__", Qt.ItemDataRole.UserRole)

        def _pick_type() -> None:
            is_p = type_sel.currentIndex() == 1
            for w in (name_lb, name_in, text_lb, text_in):
                w.setVisible(is_p)
            if not is_p: # 新建分区：固定走自定义输入（web :6340 初始态同款）
                cat_sel.setEnabled(False)
                cat_sel.setCurrentIndex(cat_sel.count() - 1)
                cat_in.setVisible(True)
                desc_in.setEnabled(True)
            else:
                cat_sel.setEnabled(True)
                _on_cat_changed()

        def _on_cat_changed() -> None:
            custom = cat_sel.currentData(Qt.ItemDataRole.UserRole) == "__custom__"
            cat_in.setVisible(custom)
            if not custom:
                desc_in.setText(state.get("user_cats", {}).get(cat_sel.currentText(), ""))
                desc_in.setEnabled(False)
            else:
                desc_in.setEnabled(True)

        def _cat_value() -> str:
            if cat_sel.currentData(Qt.ItemDataRole.UserRole) == "__custom__":
                return (cat_in.text() or "").strip()
            return cat_sel.currentText()

        def _add_done(r, e, done_msg: str) -> None:
            if e or not isinstance(r, dict) or r.get("ok") is False:
                pnote.setText(f"创建失败：{e or (r or {}).get('error') or '后台没连上'}")
                return
            pnote.setText(done_msg)
            dlg.accept()
            load_personas()

        def _ok() -> None:
            cat = _cat_value()
            if not cat:
                pnote.setText("分区名不能为空")
                return
            if type_sel.currentIndex() == 0:
                seq = [("/api/persona/cats/save",
                        {"name": cat, "desc": (desc_in.text() or "").strip()})]
                done_msg = f"分区「{cat}」已创建（在分区栏可看/删除）"
            else:
                nm = (name_in.text() or "").strip()
                txt = (text_in.toPlainText() or "").strip()
                if not nm or not txt:
                    pnote.setText("角色名和文本都要填")
                    return
                seq = []
                if (cat_sel.currentData(Qt.ItemDataRole.UserRole) == "__custom__"
                        and cat not in known):
                    # 自定义新分区名时先落盘（web :6356-6361 同款两连跳）
                    seq.append(("/api/persona/cats/save",
                                {"name": cat, "desc": (desc_in.text() or "").strip()}))
                seq.append(("/api/personas/custom", {"name": nm, "text": txt, "cat": cat}))
                done_msg = f"角色「{nm}」已加入分区「{cat}」"
            pnote.setText("创建中…")
            _post_chain(seq, lambda r, e: _add_done(r, e, done_msg))

        type_sel.currentIndexChanged.connect(_pick_type)
        cat_sel.currentIndexChanged.connect(_on_cat_changed)
        b_ok.clicked.connect(_ok)
        b_cancel.clicked.connect(dlg.reject)
        _cat_opts()
        _pick_type()
        dlg.adjustSize()
        win = page.window()
        if win.isVisible():
            g = win.frameGeometry()
            dlg.move(g.center() - dlg.rect().center())
        else:
            dlg.move(320, 260)
        dlg.exec()

    def _restore_prev() -> None:
        prev = config_io.read_path("persona.last_used") or {}
        if not (prev.get("name") or prev.get("text")):
            pnote.setText("还没有可恢复的人设（先「使用」过一次）")
            return
        # 对齐 web uiConfirm 口径（assets/console/index.html:6271）：恢复上个人设前二次确认
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "恢复上一个人设",
            f"恢复上个人设「{prev.get('name') or '未命名'}」？",
            ["当前人设将被替换（保存后重启机器人生效）"], confirm_label="恢复")
        if not d.exec():
            return
        pnote.setText(f"恢复上个人设「{prev.get('name') or '未命名'}」中…")
        _async_post(None, "/api/config", {
            "persona": {"bot_name": prev.get("name", ""), "role_text": prev.get("text", "")}
        }, lambda r, e: pnote.setText(
            "已恢复上个人设并保存（重启后生效）" if (r and r.get("ok") is not False)
            else f"恢复失败：{e or (r or {}).get('error') or '后台没连上'}"))

    def _apply_persona(p: dict) -> None:
        cur = {"name": config_io.read_path("persona.bot_name") or "",
               "text": config_io.read_path("persona.role_text") or ""}
        pnote.setText(f"切换人设「{p.get('name') or '未命名'}」并保存…")
        _async_post(None, "/api/config", {
            "persona": {"last_used": cur, "bot_name": p.get("name", ""),
                        "role_text": p.get("text") or ""}
        }, lambda r, e: pnote.setText(
            f"已切换人设「{p.get('name') or '未命名'}」并保存（重启后生效）"
            if (r and r.get("ok") is not False)
            else f"切换失败：{e or (r or {}).get('error') or '后台没连上'}"))

    def _del_persona(p: dict) -> None:
        if not (p.get("key") or "").startswith("custom"):
            pnote.setText("仅自定义角色可删除（内置/默认角色不可删）")
            return
        # 对齐 web uiConfirm 口径（assets/console/index.html:6195）：删除自定义角色前二次确认
        dlg = ConfirmDialog(
            t, page, "删除自定义角色", f"删除自定义角色「{p.get('name') or ''}」？",
            ["该角色会从人设列表移除（不能撤销）"], confirm_label="删除")
        if not dlg.exec():
            return
        pnote.setText(f"删除「{p.get('name') or ''}」中…")
        _async_post(None, "/api/personas/custom/del", {"key": p.get("key")},
                    lambda r, e: (load_personas() or pnote.setText(
                        f"已删除（{r.get('note') if isinstance(r, dict) else ''}）"
                        if (r and r.get("ok") is not False)
                        else f"删除失败：{e or (r or {}).get('error') or '后台没连上'}")))

    def _fav_persona(p: dict) -> None:
        new_fav = not p.get("fav")
        p["fav"] = new_fav
        _async_post(None, "/api/personas/fav",
                    {"key": p.get("key"), "fav": new_fav}, lambda r, e: None)
        _render()

    def _move_persona(p: dict) -> None:
        # web mvBtn（assets/console/index.html:6237-6247，uiPrompt「移到哪个分区」）：
        # 输入已有分区名或新名字自动新建 → POST /api/personas/custom 带 cat。
        cats = sorted(set(list(state.get("built") or [])
                          + list((state.get("user_cats") or {}).keys())))
        dlg, v = _card_dialog(t, page, "移到哪个分区", width=520)
        dlg.resize(520, 250)
        tip = QLabel("可填已有分区（%s）或输入新名字自动新建"
                     % ("、".join(cats) if cats else "暂无，直接输入新名字"))
        tip.setFont(qfont(t, 12))
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{t.tx2};background:transparent;")
        v.addWidget(tip)
        ed = QLineEdit(str(p.get("cat") or ""))
        ed.setFont(qfont(t, 12.5))
        ed.setStyleSheet(
            f"QLineEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:6px 10px;}}")
        v.addWidget(ed)
        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = Btn("取消", t, "ghost")
        b_ok = Btn("移过去", t, "primary")
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        v.addLayout(row)

        def _go() -> None:
            cat = ed.text().strip()
            if not cat:
                tip.setText("分区名不能为空")
                return
            dlg.accept()
            pnote.setText(f"移到「{cat}」中…")
            _async_post(None, "/api/personas/custom",
                        {"key": p.get("key"), "name": p.get("name"),
                         "text": p.get("text"), "cat": cat},
                        lambda r, e: (load_personas() or pnote.setText(
                            f"已移到「{cat}」" if (r and r.get("ok") is not False)
                            else f"移动失败：{e or (r or {}).get('error') or '后台没连上'}")))

        b_ok.clicked.connect(_go)
        b_cancel.clicked.connect(dlg.reject)
        dlg.exec()

    handlers = {"use": _apply_persona, "del": _del_persona, "fav": _fav_persona,
                "move": _move_persona}

    def load_personas() -> None:
        # err_box：/api/personas 是**唯一必需**的取数口 —— 它失败（控制台没起/超时）
        # 时列表必空，此时绝不是「没有数据」，而是「读不到」。把原因带下去，
        # 让 set_personas 如实写出来，别让用户对着空表猜（「人设表一片空白」）。
        ebox: dict = {}
        try:
            r = config_io.get_json("/api/personas", timeout=5.0, err_box=ebox)
        except Exception as e: # noqa: BLE001
            r, ebox = None, {"err": str(e)}
        state["load_err"] = ebox.get("err")
        items = (r or {}).get("personas") or [] if isinstance(r, dict) else []
        try:
            rc = config_io.get_json("/api/personas/custom", timeout=5.0) or {}
            for c in (rc.get("custom") or []):
                items.append({"name": c.get("name"), "key": c.get("key"),
                              "text": c.get("text"), "cat": c.get("cat") or "自定义"})
        except Exception: # noqa: BLE001
            pass
        scores = {}
        try:
            rs = config_io.get_json("/api/personas/scores", timeout=5.0) or {}
            for x in (rs.get("rows") or []):
                scores[x.get("key")] = x
        except Exception: # noqa: BLE001
            pass
        favs = {}
        try:
            rf = config_io.get_json("/api/personas/favs", timeout=5.0) or {}
            favs = rf.get("favs") or {}
        except Exception: # noqa: BLE001
            pass
        # 分区元数据：内置分区名 + 用户自建分区（web :6084-6088 同款）——
        # 用户分区 chip 的红 × 删除与「＋ 新建/添加」弹窗都靠它判定
        try:
            rc2 = config_io.get_json("/api/persona/cats", timeout=5.0) or {}
            if isinstance(rc2, dict):
                state["built"] = list(rc2.get("built") or [])
                state["user_cats"] = {c.get("name"): (c.get("desc") or "")
                                      for c in (rc2.get("user") or [])
                                      if isinstance(c, dict) and c.get("name")}
        except Exception: # noqa: BLE001
            pass
        for p in items:
            sc = scores.get(p.get("key")) or {}
            p["__score"] = sc.get("model")
            p["fav"] = bool(favs.get(p.get("key")))
        set_personas(items)

    # 自检钩子（按名/按属性取证）
    page.c12_set_personas = set_personas
    page.c12_apply_sort = apply_sort
    page.c12_sort_state = lambda: state["sort"]
    page.c12_list = listw
    page.c12_search = search
    page.c12_load = load_personas
    page.c12_reload_personas = load_personas # shell 徽章轮询：本页空 + 后端有人设 ⇒ 补拉
    page.c12_handlers = handlers
    page.c12_cats_state = lambda: (list(state["built"]), dict(state["user_cats"]))

    search.textChanged.connect(_render)
    b_sort.clicked.connect(apply_sort)
    b_sort_off.clicked.connect(lambda: (state.__setitem__("sort", 0),
                                        b_sort.setText("↓ 按评估分数排序"), _render()))
    b_restore.clicked.connect(_restore_prev)
    load_personas()

    # ── 评分补足块（pScoreLLM / pEnrich / pWebFetch 真接后端，:1326-1336）──
    rcard = Card(t)
    rcard.body.addWidget(h2(t, "评分补足"))
    # 一行放不下就折行；「标签+控件」成组（_pair）保证折行时标签不会和后缀控件分家。
    rrow = FlowBox(t, spacing=8)
    b_score = Btn("模型评分", t, "ghost")
    b_score.setObjectName("pScoreLLM")
    b_enrich = Btn("模型补足", t, "ghost")
    b_enrich.setObjectName("pEnrich")
    b_wf = Btn("联网收集真实资料", t, "ghost")
    b_wf.setObjectName("pWebFetch")
    from panels_qt import Combo # noqa: PLC0415
    rounds = Combo(t, [("1 轮", "1"), ("2 轮", "2"), ("3 轮", "3")], "1")
    rounds.setObjectName("pRounds")
    use_llm = Switch(t, True)
    use_llm.setObjectName("pUseLlm")

    def _pair(lb_text: str, ctrl) -> QWidget: # noqa: ANN001
        w = QWidget()
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        lb = QLabel(lb_text)
        lb.setFont(qfont(t, t.body_size - 0.5))
        lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
        h.addWidget(lb)
        h.addWidget(ctrl)
        return w

    rrow.add(b_score)
    rrow.add(b_enrich)
    rrow.add(b_wf)
    rrow.add(_pair("补足轮数", rounds))
    rrow.add(_pair("允许模型处理", use_llm))
    rcard.body.addWidget(rrow)
    rst = QLabel("")
    rst.setObjectName("pScoreRst")
    rst.setFont(qfont(t, 12))
    rst.setStyleSheet(f"color:{t.tx2};background:transparent;")
    rst.setWordWrap(True)
    rcard.body.addWidget(rst)
    rcard.body.addWidget(desc(t, "「联网收集真实资料」按角色名检索主流媒体/官方/百科真实语录（只返回搜索摘要，不编造），"
                                "追加到下方「自定义角色文本」；「模型补足」按人设贴近度修正。"))
    lay.addWidget(rcard)

    def _score() -> None:
        text = role_text_ctrl.toPlainText()
        if not use_llm.isChecked():
            rst.setText('未勾选「允许模型处理」——评分需模型参与（勾选后点此）')
            return
        if not text.strip():
            rst.setText("请先填写角色文本（或从人设库选一个「使用」）")
            return
        rst.setText("模型评分中（约 10~30 秒）…")
        _async_post(None, "/api/persona/score", {"text": text, "llm": True},
                    lambda r, e: rst.setText(
                        f"模型评分 {float(r['score']):.1f} 分　{r.get('reason') or ''}"
                        if (r and r.get("ok") and r.get("score") is not None)
                        else f"评分失败：{e or (r or {}).get('error') or '后台没连上'}"),
                    timeout=60.0)

    def _enrich() -> None:
        text = role_text_ctrl.toPlainText()
        name = name_ctrl.text().strip()
        if not name:
            rst.setText("请先填「人设名」（模型按角色名联网整理设定）")
            return
        n = int(rounds.currentData() or "1")
        rst.setText(f"模型补足中（{n} 轮，每轮 10~30 秒）…")
        _async_post(None, "/api/persona/ai-enrich", {"name": name, "text": text, "rounds": n},
                    lambda r, e: (role_text_ctrl.setPlainText(r.get("text", text))
                                  if (r and r.get("ok") and r.get("text") is not None) else None,
                                  rst.setText(
                                      f"补足完成（最终 {r.get('score'):.2f} 分）"
                                      if (r and r.get("ok"))
                                      else f"模型失败：{e or (r or {}).get('error') or '后台没连上'}")),
                    timeout=120.0)

    def _webfetch() -> None:
        name = name_ctrl.text().strip()
        if not name:
            rst.setText("请先填「人设名」（按角色名联网检索其真实言论资料）")
            return
        rst.setText(f"联网检索「{name}」的真实语录/访谈/言论…（约 20~60 秒）")
        _async_post(None, "/api/persona/web-fetch", {"name": name},
                    lambda r, e: (role_text_ctrl.setPlainText(
                        (role_text_ctrl.toPlainText().strip() + "\n\n" + _wf_head(r))
                        if (r and r.get("ok")) else role_text_ctrl.toPlainText()),
                        rst.setText(
                            f"已收集真实资料（{(r or {}).get('results') and len(r.get('results')) or 0} 条来源）"
                            "，已追加到角色文本下方——请核对后再保存"
                            if (r and r.get("ok"))
                            else f"检索失败：{e or (r or {}).get('error') or '后台没连上'}")),
                    timeout=120.0)

    b_score.clicked.connect(_score)
    b_enrich.clicked.connect(_enrich)
    b_wf.clicked.connect(_webfetch)

    # ── 可写配置（人设名 / 参与度 / 角色文本 / 系统提示词补充 / 3 模块开关 / recall / proactive）──
    ccard = Card(t)
    ccard.body.addWidget(h2(t, "人设与响应配置"))
    binds: list = []
    from panels_qt import _area # noqa: PLC0415

    name_ctrl = _line(t, _as_text(config_io.read_path("persona.bot_name")),
                      placeholder="留空=内置小鲸鱼角色卡")
    name_ctrl.setObjectName("personaName")
    ccard.body.addWidget(Field(t, "人设名", "机器人以谁的身份说话", name_ctrl))
    binds.append(("persona.bot_name", name_ctrl, "text"))

    # ⛔ Combo 契约：options=(value, label)——此处原传反过（真机显示英文值），顺修
    part = Combo(t, [("low", "安静型"), ("medium", "普通群友"), ("high", "活跃型")],
                 config_io.read_path("persona.participation") or "medium")
    part.setObjectName("personaPart")
    ccard.body.addWidget(Field(t, "参与度", "在群里多话还是少话", part))
    binds.append(("persona.participation", part, "select"))

    role_text_ctrl = _area(t, _as_text(config_io.read_path("persona.role_text")),
                          rows=4, placeholder="留空=内置小鲸鱼角色卡；填了=完全替换")
    role_text_ctrl.setObjectName("personaRoleText")
    ccard.body.addWidget(Field(t, "自定义角色文本", "留空=内置；填了=完全替换（可参考 agent/persona.py）", role_text_ctrl))
    binds.append(("persona.role_text", role_text_ctrl, "textarea"))

    # ── 行为档推荐（web roleHintBtn :6379-6415）：按角色卡让模型评估参与度/表情包档，
    #    弹窗里可改推荐值，「应用」写 persona.participation + store.sticker_level。──
    def _role_hint() -> None:
        rt = role_text_ctrl.toPlainText()[:2400]
        pnote.setText("正在按角色卡评估行为档（模型多维度）…")

        def _done(r, e): # noqa: ANN001
            if e:
                pnote.setText(f"评估失败：{e}")
                return
            if isinstance(r, dict) and r.get("error"):
                pnote.setText(f"评估失败：{r.get('error')}")
                return
            r = r or {}
            part_v = str(r.get("participation") or "medium")
            st = int(r.get("sticker") or 0)
            via = ("（模型评估" + (("：" + str(r.get("reason"))) if r.get("reason") else "") + "）"
                   if r.get("via") == "llm" else "（本地规则）")
            extra = ""
            if isinstance(r.get("marks"), dict):
                extra = " [活跃m×%s 安静m×%s]" % (r["marks"].get("active"), r["marks"].get("passive"))
            pnote.setText("推荐：参与度 %s · 表情包 %d 级 %s%s"
                          % ({"high": "活跃", "low": "安静"}.get(part_v, "普通"), st, via, extra))
            _role_hint_dlg(part_v, st, pnote.text())

        _async_post(None, "/api/persona/behavior-recommend", {"text": rt}, _done)

    def _role_hint_dlg(part_v: str, st: int, rc_text: str = "") -> None:
        from panels_qt import Combo # noqa: PLC0415

        dlg, v = _card_dialog(t, page, "推荐行为档", width=520)
        dlg.resize(520, 340)
        tip = QLabel("模型按角色卡给出了推荐，可改。点「应用」写入参与度与表情包积极度（保存后生效）。")
        tip.setFont(qfont(t, 12))
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{t.tx2};background:transparent;")
        v.addWidget(tip)
        if rc_text:
            rc = QLabel(rc_text)
            rc.setFont(qfont(t, 12))
            rc.setWordWrap(True)
            rc.setStyleSheet(f"color:{t.tx};background:transparent;")
            v.addWidget(rc)
        c_part = Combo(t, [("low", "安静型"), ("medium", "普通群友"), ("high", "活跃型")], part_v)
        c_part.setObjectName("roleHintPart")
        v.addWidget(Field(t, "参与度", "在群里多话还是少话", c_part))
        c_stk = Combo(t, [("0", "0 级 · 少"), ("1", "1 级 · 偶尔"),
                          ("2", "2 级 · 较多"), ("3", "3 级 · 爱好者")], str(st))
        c_stk.setObjectName("roleHintSticker")
        v.addWidget(Field(t, "表情包积极度", "发表情包的频率档", c_stk))
        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = Btn("取消", t, "ghost")
        b_ok = Btn("应用", t, "primary")
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        v.addLayout(row)

        def _apply() -> None:
            pv = str(c_part.currentData() or "medium")
            try:
                sv = int(c_stk.currentData() or 0)
            except ValueError: # noqa: PERF203
                sv = 0
            ok, why = config_io.write_patch({"persona.participation": pv,
                                             "store.sticker_level": sv})
            if not ok:
                pnote.setText(f"应用失败：{why}")
                return
            i = part.findData(pv)
            if i >= 0:
                part.setCurrentIndex(i) # 参与度下拉同步显示新值（web syncToForm 同款）
            pnote.setText("已应用行为档（参与度/表情包积极度）并保存")
            dlg.accept()

        b_ok.clicked.connect(_apply)
        b_cancel.clicked.connect(dlg.reject)
        dlg.exec()

    hint_row = FlowBox(t, spacing=6)
    b_hint = Btn("根据角色卡推荐行为档", t, "ghost")
    b_hint.setObjectName("roleHintBtn")
    b_hint.clicked.connect(_role_hint)
    hint_row.add(b_hint)
    ccard.body.addWidget(hint_row)

    custom_rules = _area(t, _as_text(config_io.read_path("persona.custom_rules")),
                         rows=2, placeholder="如：回复永远不超过 5 个字")
    ccard.body.addWidget(Field(t, "额外规则", "人设之外的补充约束", custom_rules))
    binds.append(("persona.custom_rules", custom_rules, "textarea"))

    sys_custom = _area(t, _as_text(config_io.read_path("system_prompt.custom")),
                       rows=3, placeholder="追加到系统提示词末尾（管理员补充，最高优先级）")
    ccard.body.addWidget(Field(t, "系统提示词补充", "写在这里的文字追加到系统提示词末尾；保存后下一轮生效", sys_custom))
    binds.append(("system_prompt.custom", sys_custom, "textarea"))

    # ── prompt 预览/清空（web promptPreviewBtn/promptClearBtn 真值 :1303-1304/:4615-4640）──
    # 预览走服务端现算（/api/prompt/preview = 真 build_system_prompt），不是前端拼的。
    # ⚠️ pp_note 别用 pnote（:1987 人设列表回显行已占用；重名会把那批闭包的回显劫到这来）。
    pp_note = desc(t, "")
    ccard.body.addWidget(pp_note)
    p_row = FlowBox(t, spacing=6)
    b_preview = Btn("预览当前系统提示词", t, "ghost")
    b_pclear = Btn("清空补充", t, "ghost")
    p_row.add(b_preview)
    p_row.add(b_pclear)
    ccard.body.addWidget(p_row)

    def _prompt_preview() -> None:
        pp_note.setText("预览生成中…")
        _async_get("/api/prompt/preview", lambda r, e: _prompt_preview_show(r, e))

    def _prompt_preview_show(r: dict | None, e: str | None) -> None:
        if e or not r or r.get("error"):
            pp_note.setText("预览失败：" + (e or (r or {}).get("error") or "后台没连上"))
            return
        dlg, v = _card_dialog(t, b_preview, "当前系统提示词（服务端现算）", 780)
        ta = _plain_area(t, str(r.get("system") or "") or "（空）", "系统提示词全文", height=380)
        ta.setReadOnly(True)
        v.addWidget(ta)
        mods = [str(m.get("name")) for m in (r.get("modules") or []) if isinstance(m, dict) and m.get("enabled")]
        info = _dlg_note(t)
        info.setText("%s 字符 ｜ 已启用模块：%s ｜ 自定义补充 %s 字符" % (
            r.get("chars", 0), "、".join(mods) or "无", r.get("custom_chars", 0)))
        v.addWidget(info)
        foot = QHBoxLayout()
        okb = Btn("知道了", t, "primary")
        okb.clicked.connect(dlg.accept)
        foot.addStretch(1)
        foot.addWidget(okb)
        v.addLayout(foot)
        dlg.exec()
        pp_note.setText("")

    def _prompt_clear() -> None:
        if not sys_custom.toPlainText().strip():
            pp_note.setText("本来就是空的")
            return
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "清空「系统提示词补充」？", "清空后立即生效（安全规则不受影响）。",
            ["「系统提示词补充」会清空", "清空后下一轮对话立即生效"],
            confirm_label="确认清空", cancel_label="算了")
        if not d.exec():
            return
        sys_custom.setPlainText("")
        ok, msg = config_io.write_patch({"system_prompt.custom": ""})
        pp_note.setText("已清空系统提示词补充（下一轮生效）" if ok
                        else "清空失败：" + msg)

    b_preview.clicked.connect(_prompt_preview)
    b_pclear.clicked.connect(_prompt_clear)

    ccard.body.addWidget(_divider_local(t))
    ccard.body.addWidget(h2(t, "模块开关（三个全要，对齐 web :1366-1369）"))
    sw_scene = Switch(t, bool(config_io.read_path("system_prompt.enable_scene_rules")))
    sw_scene.setObjectName("swScene")
    ccard.body.addWidget(Field(t, "微信场景规则", "关掉=系统提示词少这一段", sw_scene))
    binds.append(("system_prompt.enable_scene_rules", sw_scene, "checkbox"))
    sw_mem = Switch(t, bool(config_io.read_path("system_prompt.enable_memory_rules")))
    sw_mem.setObjectName("swMemoryRules")
    ccard.body.addWidget(Field(t, "记忆使用规则", "关掉=系统提示词少这一段", sw_mem))
    binds.append(("system_prompt.enable_memory_rules", sw_mem, "checkbox"))
    sw_holiday = Switch(t, bool(config_io.read_path("system_prompt.enable_holiday_hint")))
    sw_holiday.setObjectName("swHoliday")
    ccard.body.addWidget(Field(t, "节日提示", "关掉=系统提示词少这一段", sw_holiday))
    binds.append(("system_prompt.enable_holiday_hint", sw_holiday, "checkbox"))

    ccard.body.addWidget(_divider_local(t))
    ccard.body.addWidget(h2(t, "撤回后剔除（recallRows，web :1373-1381）"))
    sw_recall = Switch(t, bool(config_io.read_path("store.recall.enabled")))
    ccard.body.addWidget(Field(t, "撤回后剔除", "群友撤回的消息从存档剔除（保留「已撤回」标记）", sw_recall))
    binds.append(("store.recall.enabled", sw_recall, "checkbox"))
    win_sec = _line(t, _as_text(config_io.read_path("store.recall.window_sec")), placeholder="默认 0")
    ccard.body.addWidget(Field(t, "兜底时间窗(秒)", "拿不到 newmsgid 时的时间窗", win_sec))
    binds.append(("store.recall.window_sec", win_sec, "number"))
    sw_heur = Switch(t, bool(config_io.read_path("store.recall.heuristic")))
    ccard.body.addWidget(Field(t, "兜底匹配", "关掉=只认 newmsgid 精确匹配", sw_heur))
    binds.append(("store.recall.heuristic", sw_heur, "checkbox"))

    ccard.body.addWidget(_divider_local(t))
    ccard.body.addWidget(h2(t, "主动开话题（proactiveRows，web :1383-1390）"))
    sw_pro = Switch(t, bool(config_io.read_path("proactive.enabled")))
    ccard.body.addWidget(Field(t, "主动开话题", "群冷场超阈值后按概率抛话题（默认关）", sw_pro))
    binds.append(("proactive.enabled", sw_pro, "checkbox"))
    pro_idle = _line(t, _as_text(config_io.read_path("proactive.idle_threshold_ms")), placeholder="默认 1800000")
    ccard.body.addWidget(Field(t, "冷场阈值(毫秒)", "默认 1800000（30 分钟）", pro_idle))
    binds.append(("proactive.idle_threshold_ms", pro_idle, "number"))
    pro_min = _line(t, _as_text(config_io.read_path("proactive.check_interval_min_ms")), placeholder="默认 1800000")
    ccard.body.addWidget(Field(t, "检查间隔(毫秒)", "默认 1800000（30 分钟）", pro_min))
    binds.append(("proactive.check_interval_min_ms", pro_min, "number"))
    pro_max = _line(t, _as_text(config_io.read_path("proactive.check_interval_max_ms")), placeholder="默认 5400000")
    ccard.body.addWidget(Field(t, "检查上限(毫秒)", "默认 5400000（90 分钟）", pro_max))
    binds.append(("proactive.check_interval_max_ms", pro_max, "number"))
    pro_prob = _line(t, _as_text(config_io.read_path("proactive.probability")), placeholder="默认 0.25")
    ccard.body.addWidget(Field(t, "触发概率(小数)", "0~1，默认 0.25", pro_prob))
    binds.append(("proactive.probability", pro_prob, "number"))

    lay.addWidget(ccard)
    _append_save(t, lay, binds, badge)

    lay.addStretch(1)
    return page


def _as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception: # noqa: BLE001
            return ""
    return str(v)


def _divider_local(t: Tokens) -> QFrame:
    d = QFrame()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background:{t.bd};border:none;")
    return d


def _persona_card(t: Tokens, p: dict, handlers: dict) -> QWidget:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet("background:transparent;")
    h = QHBoxLayout(w)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(6)
    # 星标改项目图标库 SVG；
    #   收藏=实心金星，未收藏=灰描边星；按钮窄条 34px。
    from PySide6.QtGui import QIcon # noqa: PLC0415

    from icons import INNER, svg_pixmap # noqa: PLC0415

    fav = Btn("", t, role="ghost")
    fav.setIcon(QIcon(svg_pixmap(
        INNER["star-filled"] if p.get("fav") else INNER["star"],
        t.warn if p.get("fav") else t.tx3, 14)))
    fav.setToolTip("已收藏（置顶；点击取消）" if p.get("fav") else "收藏置顶")
    fav.clicked.connect(lambda _=False, _p=p: handlers["fav"](_p))
    # 窄窗契约：name/sc 固定宽 → 评分列紧跟人设名、位置稳定不浮动；
    #   txt 最小宽 0 可被布局压缩；卡片最小宽压到 ~400px，
    #   列表视口再窄「使用/删」也不会被裁出视口（不用拉宽窗口）。
    from PySide6.QtGui import QFontMetrics # noqa: PLC0415

    name = ElideLabel(p.get("name") or "(未命名)")
    name.setObjectName("personaName")
    name.setFont(qfont(t, 13, 600))
    name.setStyleSheet(f"color:{t.tx};background:transparent;")
    name.setFixedWidth(122)
    sc = p.get("__score")
    sc_lb = QLabel(f"模型 {sc:.2f}" if isinstance(sc, (int, float)) else "")
    sc_lb.setFont(qfont(t, 12))
    sc_lb.setStyleSheet(f"color:{t.warn};background:transparent;")
    sc_lb.setFixedWidth(78)
    use = Btn("使用", t, "ghost")
    # ⛔ 不许再写 `setFixedWidth(数字)`：中文两字按钮按字号算需 ~62px（文字 28 +
    #    左右内边距 34），写死 54 会把字压到溢出、与邻居叠成一团（
    #    「使用/删除/移到其他分区/评估 的字全混在一起，挤在小按钮里看不清」）。
    #    宽度交给 sizeHint（= 文字宽 + 内边距），由 Btn 自己保证下限。
    use.clicked.connect(lambda _=False, _p=p: handlers["use"](_p))
    # 单字钮改**全文字钮**（：打星按钮没用、删掉；移动/删除要完整露出，
    # 不再缩成单字正圆）——宽度交给 sizeHint，两字胶囊 ~62px 放得下。
    delete = Btn("删除", t, "ghost")
    delete.setToolTip("删除这条人设")
    delete.clicked.connect(lambda _=False, _p=p: handlers["del"](_p))
    move = Btn("移动", t, "ghost")
    move.setToolTip("移到其他分区（可输入新分区名自动新建）")
    move.clicked.connect(lambda _=False, _p=p: handlers["move"](_p))
    # 星标是纯图标钮：正圆，直径跟行高对齐
    fav.set_shape(SHAPE_CIRCLE)
    fav.set_button_size(34, 34)
    txt = ElideLabel((p.get("text") or "").replace("\n", " ")[:120])
    txt.setObjectName("personaSum")
    txt.setFont(qfont(t, 11.5))
    txt.setStyleSheet(f"color:{t.tx3};background:transparent;")
    # 单行 + 右侧省略号 + tooltip 全文（见 ElideLabel 注解：此处若换行，
    # 60 字折成 5 行 ⇒ 行高 190px，一条就吃掉整个 220px 列表 ⇒ 「列表下面全空」）。
    txt.setMaximumWidth(300)
    txt.setMinimumWidth(0)
    txt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    txt.setFixedHeight(max(20, int(t.body_size * 1.5)))
    # 卡片本体：水平 Expanding ⇒ setItemWidget 时随条目矩形铺满（右侧顶格，
    # 不再按 sizeHint 居左留缝）。
    # ⛔ 行高必须**钉死**：条目的 sizeHint 高度 = 本控件的 sizeHint，任何子控件
    #   （多行 QLabel、带换行的 tooltip 文字等）把 sizeHint 抬高一截，就会让
    #   单条吃掉整个列表可视高度 ⇒ 用户看到「列表里只有第一条，下面全空」。
    _row_h = max(44, int(t.body_size * 2.6))
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    w.setFixedHeight(_row_h)
    h.setContentsMargins(6, 4, 6, 4)
    h.addWidget(fav)
    h.addWidget(name)
    h.addWidget(sc_lb)
    h.addWidget(txt, 1)
    h.addSpacing(6)
    h.addWidget(use)
    h.addWidget(move)
    h.addWidget(delete)
    return w


def _balance_text(b: dict | None, display: str = "real", fake: str = "") -> str:
    """/api/balance 响应 → 顶栏徽章文本（web loadBalance :3208-3221 + balMask 模式口径）。

    display：real 照实 / hide 隐藏 / fake 显示指定数字（fake 值不合法时回落照实）。
    拿不到数字时如实说「未配置」，绝不拼出「余额 ¥undefined」。"""
    if not isinstance(b, dict):
        return "查询失败"
    if b.get("ok") is False:
        return str(b.get("error") or "未配置")

    def _num(v): # noqa: ANN001
        try:
            if v is None or v == "":
                return None
            f = float(v)
            return f if f == f else None # NaN 防御
        except (TypeError, ValueError):
            return None

    cur = "$" if b.get("currency") == "USD" else "¥"
    if display == "hide":
        return "余额 已隐藏"
    if display == "fake":
        fv = _num(fake)
        if fv is not None:
            return "余额 " + cur + ("%.2f" % fv)
    total = _num(b.get("total_balance"))
    if total is None:
        return "余额 未配置"
    top = _num(b.get("topped_up_balance"))
    return "余额 " + cur + ("%.2f" % total) + ("" if top is None else "（充值 " + cur + ("%.2f" % top) + "）")


def _wf_head(r: dict) -> str:
    if not isinstance(r, dict) or not r.get("ok"):
        return ""
    head = "【联网真实资料 · 来源为主流媒体/官方/百科搜索摘要，未编造 —— 请人工核对后提取】\n"
    for q in (r.get("quotes") or []):
        head += f'- "{q}"\n'
    for n in (r.get("notes") or [])[:2]:
        if n:
            head += f"· {n}\n"
    head += "\n【来源链接】\n"
    for it in (r.get("results") or [])[:8]:
        head += f"- {it.get('title')}：{it.get('url')}\n"
    return head


# ---------------------------------------------------------------- 记忆面板（P0-B：印象列表 / 群选 / 清除）


def memory_panel(t: Tokens) -> QWidget:
    """记忆（群友印象）—— P0-B：web 核心动态列表全失，这里补齐：
      · memChats 群选下拉（:1598）：GET /api/memory 拿 chats 列表填充；
      · memTable 印象列表（:1619）：成员 / 印象数 / 更新时间 / 删除勾选，逐行渲染；
      · memRefresh（:1599）/ memClearSel（清除勾选的印象）/ memClearAll（清除全部）；
      · 下方叠 memory 可写配置（summarize_on_exit / consolidate_enabled /
        share_across_groups）+ 共享群勾选（memGroupsBox，GET /api/wechat-groups）。
    接口名照抄 console_html.py 的 loadMemory（:6969）/ 清除块（:7065）。"""
    s = sec_meta.get("memory")
    page, lay, badge = _page(t, s.title, "idle", "读取中")
    lay.addWidget(desc(t, s.desc or "每个群友的长期印象，机器人回复时会参考。"))
    from panels_qt import _line, Combo # noqa: PLC0415

    state: dict = {"members": [], "chat_key": "", "chats": [], "filling": False}

    mcard = Card(t)
    mcard.body.addWidget(h2(t, "群友印象（/api/memory）"))

    # 群选下拉 + 群内搜索 + 刷新（对齐 web :1595-1600）
    chat_row = QHBoxLayout()
    mem_search = _line(t, "", placeholder="搜索群名…")
    mem_search.setObjectName("memSearch")
    chat_sel = QComboBox()
    chat_sel.setObjectName("memChats")
    chat_sel.setMinimumWidth(240)
    chat_sel.setFixedHeight(32)
    chat_sel.setFont(qfont(t, t.body_size))
    chat_sel.setStyleSheet(
        f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
        f"color:{t.tx};border:1px solid {t.bd};}}")
    b_refresh = Btn("刷新", t, "ghost")
    b_refresh.setObjectName("memRefresh")
    chat_row.addWidget(QLabel("选择群聊"))
    chat_row.addWidget(chat_sel, 1)
    chat_row.addWidget(mem_search)
    chat_row.addWidget(b_refresh)
    mcard.body.addLayout(chat_row)
    mcard.body.addWidget(desc(t, "群内搜索框实时过滤上面的下拉选项。"))

    mem_table = _bordered_list(t, "memTable", 220)
    mcard.body.addWidget(mem_table)
    mnote = desc(t, "")
    mcard.body.addWidget(mnote)

    # 清除按钮排（memClearSel / memClearAll，web :1606-1610）
    clr_row = FlowBox(t, spacing=6)
    b_sel = Btn("清除勾选的印象", t, "danger")
    b_sel.setObjectName("memClearSel")
    b_sel.setEnabled(False)
    b_all = Btn("清除全部", t, "danger")
    b_all.setObjectName("memClearAll")
    clr_row.add(b_sel)
    clr_row.add(b_all)
    mcard.body.addWidget(clr_row)
    lay.addWidget(mcard)

    def _sync_sel_btn() -> None:
        any_checked = False
        for i in range(mem_table.count()):
            w = mem_table.itemWidget(mem_table.item(i))
            cb = w.findChild(QCheckBox) if w else None
            if cb and cb.isChecked():
                any_checked = True
        b_sel.setEnabled(any_checked)

    def _render_members() -> None:
        members = state["members"]
        mem_table.clear()
        for m in members:
            it = QListWidgetItem()
            mem_table.addItem(it)
            mem_table.setItemWidget(it, _member_card(
                t, m, state["chat_key"], _render_members, _sync_sel_btn,
                note=mnote, refresh_fn=lambda: load_memory(state["chat_key"])))
            it.setSizeHint(mem_table.itemWidget(it).sizeHint())
        badge.set("info", f"{len(members)} 人" if members else "暂无印象")
        _sync_sel_btn()

    def _fill_chats(kw: str = "") -> None:
        state["filling"] = True
        chats = state["chats"]
        prev = chat_sel.currentData()
        chat_sel.clear()
        chat_sel.addItem("— 选择群聊 —", "")
        for c in chats:
            nm = c.get("name") or ""
            if kw and kw.lower() not in nm.lower():
                continue
            chat_sel.addItem(f"{nm}（{c.get('count')} 人）", c.get("chat_key"))
        datas = [chat_sel.itemData(i) for i in range(chat_sel.count())]
        if prev and prev in datas:
            chat_sel.setCurrentIndex(datas.index(prev))
        state["filling"] = False

    def _on_chat(_idx: int) -> None:
        if state["filling"]:
            return
        load_memory(chat_sel.currentData() or "")

    def load_memory(chat_key: str = "") -> None:
        state["chat_key"] = chat_key
        _eb: dict = {}
        try:
            r = config_io.get_json("/api/memory" + (f"?chat_key={chat_key}" if chat_key else ""),
                                   timeout=5.0, err_box=_eb)
        except Exception as e: # noqa: BLE001
            r = None
            _eb["err"] = str(e)
        if not isinstance(r, dict):
            # 「读不到」有两种：连不上（拒连/超时）与后端给了非 JSON。文案要能让用户动起来。
            why = _read_fail_hint(_eb.get("err"))
            mnote.setText(f"读不到记忆：{why}")
            badge.set("err", "读不到")
            return
        # 后端活着但**读取本身失败**（记忆目录不可读/权限/占用）—— 不能显示成「读取成功 · 0 位」，
        # 那会让用户以为"记忆是空的"（实际是没读到）。如实报错并指明这不是"没人"。
        if r.get("ok") is False:
            mnote.setText("读不到记忆：" + str(r.get("error") or "后台读取失败")
                          + "（这不代表没有记忆，是这次没读到）")
            badge.set("err", "读不到")
            return
        state["chats"] = r.get("chats") or []
        _fill_chats(mem_search.text())
        state["members"] = r.get("members") or []
        mnote.setText(f"读取成功 · {time.strftime('%H:%M:%S')} · {len(state['members'])} 位成员")
        _render_members()

    # 共享群（memGroupsBox）：GET /api/wechat-groups + memory.shared_groups 回填
    # （对齐 web loadMemGroups，assets/console/index.html:5949）——每个群一个勾选框，
    # 勾选状态按已存 shared_groups 匹配（兼容存「群名」或「wxid」，web :5961 同款）。
    # 「改完即生效」防抖钩子在下方 _append_save 里建好后才可挂 → 用可变引用晚绑定。
    _mg_box: dict = {"dirty": None}

    def _mg_dirty() -> None:
        fn = _mg_box.get("dirty")
        if callable(fn):
            fn()

    def load_groups() -> None:
        _eb: dict = {}
        try:
            r = config_io.get_json("/api/wechat-groups", timeout=5.0, err_box=_eb)
        except Exception as e: # noqa: BLE001
            r = None
            _eb["err"] = str(e)
        # ⛔ 读失败 ≠ 没有群：后端 ok=False 时（微信没接上 / contact.db 被占用）
        #    也返回空 groups —— 直接说「未检测到群」会把用户引去查"为什么没群"，
        #    而真实原因是"这次没读到"。这里按状态分开说（web 侧 loadMemGroups 只读 groups，
        #    但 web 另有 pickGroups 判 ok 的路径；Qt 面板是唯一入口，必须自己判）。
        if not isinstance(r, dict):
            _clear_group_checks("读不到群列表：" + _read_fail_hint(_eb.get("err")))
            return
        if r.get("ok") is False:
            reason = str(r.get("error") or "读取失败")
            if r.get("attach_ok") is False:
                _clear_group_checks("读不到群列表：" + reason)
            else:
                _clear_group_checks("群列表暂时读不到：" + reason
                                    + "（消息收发与监听不受影响，稍后点「刷新」再试）")
            return
        groups = r.get("groups") or []
        saved = config_io.read_path("memory.shared_groups", [])
        saved = [str(x) for x in saved] if isinstance(saved, list) else []
        _clear_group_checks()
        if not groups:
            mem_flow.addWidget(desc(t, "未检测到群（启动机器人并检测群后这里会列出）"))
            return
        for g in groups:
            nm = g.get("name") or g.get("nick") or g.get("wxid") or ""
            wxid = g.get("wxid") or ""
            cb = QCheckBox(nm)
            cb.setFont(qfont(t, t.body_size))
            cb.setStyleSheet(f"color:{t.tx};background:transparent;")
            cb.setProperty("gname", nm)
            cb.setProperty("gwxid", wxid)
            cb.setChecked(any(s == nm or (wxid and s == wxid) for s in saved))
            cb.toggled.connect(_mg_dirty)  # 挂「改完即生效」防抖（经 _mg_box 晚绑定）
            mem_flow.addWidget(cb)

    def _clear_group_checks(hint: str = "") -> None:
        """清空共享群勾选容器；给了 hint 就在原位放一条说明（读失败时用）。

        ⚠️ 必须 `setParent(None)` + `deleteLater()` **两个都做**：只调 deleteLater
        时，删除事件走 Qt 的 deferred-delete 队列，若此时事件循环还没转起来
        （面板刚建、宿主还没 show/mainloop），这个事件会被搁置——控件仍挂在
        `memGroupsBox` 下、仍然可见，于是「加载中…」占位框在勾选框上方一直留着
        （用户第 4 项的同类症状：占位不消失）。setParent(None) 立刻把它摘出
        父子树（同时脱离布局与显示），deleteLater 只是让 C++ 侧回收内存。
        """
        while mem_flow.count():
            item = mem_flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None) # 立刻脱离父子树 → 不再可见、不再占位
                w.deleteLater()   # C++ 侧延迟回收
        if hint:
            mem_flow.addWidget(desc(t, hint))

    chat_sel.currentIndexChanged.connect(_on_chat)
    mem_search.textChanged.connect(lambda: _fill_chats(mem_search.text()))

    def _mem_search_enter() -> None:
        """Enter：选中第一个匹配的群并加载它的印象
        （对齐 web `#memSearch` keydown，assets/console/index.html:7031-7037）。"""
        kw = mem_search.text().strip().lower()
        if not kw:
            return
        for i in range(chat_sel.count()):
            if not chat_sel.itemData(i):
                continue # 跳过「— 选择群聊 —」占位项
            if kw in str(chat_sel.itemText(i)).lower():
                chat_sel.setCurrentIndex(i)
                load_memory(chat_sel.itemData(i) or "")
                return

    mem_search.returnPressed.connect(_mem_search_enter)
    b_refresh.clicked.connect(lambda: (load_memory(chat_sel.currentData() or ""), load_groups()))

    def _clear_sel() -> None:
        picked = []
        for i in range(mem_table.count()):
            w = mem_table.itemWidget(mem_table.item(i))
            cb = w.findChild(QCheckBox) if w else None
            if cb and cb.isChecked():
                picked.append(w.property("uid"))
        if not picked:
            mnote.setText("请先勾选要清除的成员")
            return
        scope = "this" if mem_scope.currentData() == "this" else "all"
        # 对齐 web uiConfirm 口径（assets/console/index.html:6968）：批量清除前二次确认
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "清除勾选的成员印象",
            f"清除勾选的 {len(picked)} 位成员全部印象？"
            + ("（只删当前这个群，别的群那份会留着）" if scope == "this" else "（所有群一起删）"),
            ["共 %d 位成员的印象会被清掉" % len(picked)], confirm_label="清除")
        if not d.exec():
            return
        mnote.setText(f"清除 {len(picked)} 位成员印象中…")
        # ⛔ 必须**逐条** POST（每次一个 user_id）：后端 `_rapi_memory_post`
        #    （webui.py:2026/2031）只读单值 `user_id`，**没有批量协议** ——
        #    曾一次传 `user_ids:[...]`（后端不认这个键）⇒ user_id 落空 ⇒ 一条都删不掉，
        #    而界面照样回执「已清除」。web 侧（assets/console/index.html:6986-6990）就是逐个循环发的。
        #    逐条发还有个好处：`scope="this"` 时的 `left_elsewhere` 提示（后端 :2667-2671）
        #    能逐条带回来，界面可以如实说「有几份留在别的群」。
        bodies = [{"chat_key": state["chat_key"], "user_id": uid, "scope": scope}
                  for uid in picked]

        def _seq_done(results: list, err) -> None:
            oks = [r for r in results if isinstance(r, dict) and r.get("ok")]
            notes = [str(r.get("note")) for r in results
                     if isinstance(r, dict) and r.get("note")]
            if len(oks) == len(picked):
                extra = ("；" + notes[0]) if notes else ""
                mnote.setText(f"已清除 {len(picked)} 位{extra}")
            elif oks:
                mnote.setText(f"只清除了 {len(oks)}/{len(picked)} 位"
                              "（其余没删掉，可点「刷新」看当前状态再试）")
            else:
                why = err or next((str(r.get("error")) for r in results
                                   if isinstance(r, dict) and r.get("error")), "")
                mnote.setText(f"清除失败：{why or '后台没连上'}")
            load_memory(state["chat_key"])

        _async_post_seq(page, "/api/memory", bodies, _seq_done, timeout=15.0)

    def _clear_all() -> None:
        # 对齐 web uiConfirm 口径（assets/console/index.html:6983）：清全部记忆前二次确认
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "清除全部记忆",
            "注意：清除全部记忆（所有群所有成员印象+共享记忆）？",
            ["所有群、所有成员的印象一起清空", "这个动作不可恢复"], confirm_label="全部清除")
        if not d.exec():
            return
        mnote.setText("清除全部记忆中…")
        _async_post(None, "/api/memory", {"action": "clear_all"},
                    lambda r, e: (load_memory(state["chat_key"]) or mnote.setText(
                        "全部记忆已清除" if (r and r.get("ok"))
                        else f"清除失败：{e or (r or {}).get('error') or '后台没连上'}")))

    b_sel.clicked.connect(_clear_sel)
    b_all.clicked.connect(_clear_all)

    # ── 可写配置块（memory + memory-set）──
    ccard = Card(t)
    ccard.body.addWidget(h2(t, "记忆设置"))
    binds: list = []
    sw_summ = Switch(t, bool(config_io.read_path("memory.summarize_on_exit")))
    ccard.body.addWidget(Field(t, "关机总结印象", "每次关闭机器人时把对话总结为群友印象（仅关机调一次模型）", sw_summ))
    binds.append(("memory.summarize_on_exit", sw_summ, "checkbox"))
    sw_consol = Switch(t, bool(config_io.read_path("memory.consolidate_enabled")))
    ccard.body.addWidget(Field(t, "自动整理", "定时把零散印象合并、去冗余", sw_consol))
    binds.append(("memory.consolidate_enabled", sw_consol, "checkbox"))
    sw_share = Switch(t, bool(config_io.read_path("memory.share_across_groups", True)))
    ccard.body.addWidget(Field(t, "共享记忆池", "勾选=所有群共享一个记忆池；不勾=每群独立", sw_share))
    binds.append(("memory.share_across_groups", sw_share, "checkbox"))

    ccard.body.addWidget(_divider_local(t))
    ccard.body.addWidget(h2(t, "共享群（可选，memGroupsBox）"))
    # 多选勾选组 —— 对齐 web #memGroupsBox（assets/console/index.html:1573-1576 / 5949-6001）：
    # 每个检测到的群一个勾选框，勾中的群名写回 memory.shared_groups（list[str]）。
    mem_groups = QWidget()
    mem_groups.setObjectName("memGroupsBox")
    mem_flow = QVBoxLayout(mem_groups)
    mem_flow.setContentsMargins(0, 0, 0, 0)
    mem_flow.setSpacing(6)
    _mg_placeholder = desc(t, "加载中…")
    mem_flow.addWidget(_mg_placeholder)
    ccard.body.addWidget(Field(t, "共享群（勾选后保存写入 memory.shared_groups）",
                               "只有勾中的这些群之间互相共享记忆（比全共享更精准）；"
                               "不勾 = 用上方「共享记忆池」总开关", mem_groups))
    ccard.body.addWidget(desc(t, "web 用一组勾选框管理共享群（#memGroupsBox）；这里同样按"
                                "「检测到的群」逐群勾选，保存时把勾中的群名写入 "
                                "memory.shared_groups。"))
    binds.append(("memory.shared_groups", mem_groups, "groups"))
    lay.addWidget(ccard)

    # memScope（删除范围，web :1614）
    scope_row = QHBoxLayout()
    mem_scope = Combo(t, [("所有群一起删（推荐）", "all"), ("只删当前选中的这个群", "this")], "all")
    mem_scope.setObjectName("memScope")
    scope_row.addWidget(QLabel("删除范围"))
    scope_row.addWidget(mem_scope)
    scope_row.addStretch(1)
    mcard.body.addLayout(scope_row)

    _append_save(t, lay, binds, badge)

    # 共享群勾选框接入「改完即生效」防抖（与页面其它可写控件同一条链）
    _mg_box["dirty"] = getattr(page, "_c12_mark_dirty", None)

    # 自检钩子
    page.c12_load = lambda: (load_memory(chat_sel.currentData() or ""), load_groups())
    page.c12_table = mem_table
    page.c12_chats = chat_sel
    page.c12_groups = mem_groups
    page.c12_binds = binds

    load_memory("")
    load_groups()
    lay.addStretch(1)
    return page


def _member_card(t: Tokens, m: dict, chat_key: str, reload_fn, sync_fn,
                 note=None, refresh_fn=None) -> QWidget:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet("background:transparent;")
    w.setProperty("uid", m.get("userId") or m.get("name") or "")
    h = QHBoxLayout(w)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(8)
    pick = QCheckBox()
    pick.setFixedSize(18, 18)
    pick.toggled.connect(lambda _=False: sync_fn())
    name = QLabel(m.get("name") or m.get("userId") or "某人")
    name.setFont(qfont(t, 12.5))
    name.setStyleSheet(f"color:{t.tx};background:transparent;")
    n = len(m.get("impressions") or []) if isinstance(m.get("impressions"), list) else 0
    cnt = QLabel(f"{n} 条印象")
    cnt.setFont(qfont(t, 12))
    cnt.setStyleSheet(f"color:{t.tx2};background:transparent;")
    upd = QLabel(str(m.get("updatedAt") or ""))
    upd.setFont(qfont(t, 11.5))
    upd.setStyleSheet(f"color:{t.tx3};background:transparent;")
    edit_btn = Btn("编辑", t, "ghost")
    edit_btn.setFixedWidth(54)
    edit_btn.clicked.connect(
        lambda _=False: _mem_edit_dlg(t, edit_btn, m, chat_key, refresh_fn, note))
    deep_btn = Btn("深度印象", t, "ghost")
    deep_btn.clicked.connect(
        lambda _=False: _mem_deep_dlg(t, deep_btn, m, chat_key, refresh_fn, note))
    del_btn = Btn("删除", t, "ghost")
    del_btn.setFixedWidth(54)
    del_btn.clicked.connect(
        lambda _=False: _del_member(t, del_btn, chat_key, m, reload_fn))
    h.addWidget(pick)
    h.addWidget(name)
    h.addWidget(cnt)
    h.addWidget(upd)
    h.addStretch(1)
    h.addWidget(edit_btn)
    h.addWidget(deep_btn)
    h.addWidget(del_btn)
    return w


def _del_member(t: Tokens, parent: QWidget, chat_key: str, m: dict, reload_fn) -> None:
    # 对齐 web uiConfirm 口径（assets/console/index.html:6895）：删成员全部印象前二次确认
    from confirm import ConfirmDialog # noqa: PLC0415
    d = ConfirmDialog(
        t, parent, "删除成员印象",
        f"删除「{m.get('name') or m.get('userId') or '某人'}」的全部印象？",
        ["这位成员在该群的全部印象一次清空"], confirm_label="删除")
    if not d.exec():
        return
    _async_post(None, "/api/memory",
                {"chat_key": chat_key, "user_id": m.get("userId")},
                lambda r, e: reload_fn())


def _mem_membrs(m: dict) -> list:
    """一条成员记录里的印象文本列表（web :6908 同口径：逐条 content，缺失补空串）。"""
    imps = m.get("impressions")
    if not isinstance(imps, list):
        return []
    return [(e.get("content") or "") if isinstance(e, dict) else "" for e in imps]


def _mem_edit_dlg(t: Tokens, btn, m: dict, chat_key: str, refresh_fn, note) -> None:
    """编辑印象弹窗（web memEdit :6906-6924）：
    textarea 预填现有印象（每行一条）→ 保存=按行拆、trim、滤空后**整份覆盖**
    POST /api/memory action:update —— 清空=删除全部（web 同语义，无护栏）；
    取消不发包。失败在弹窗内回显原因、弹窗不关。"""
    name = m.get("name") or m.get("userId") or "某人"
    dlg, v = _card_dialog(t, btn, f"编辑「{name}」的印象", width=640)
    dlg.resize(640, 380)
    tip = QLabel("每行一条印象；清空=删除全部。")
    tip.setFont(qfont(t, 12))
    tip.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(tip)
    ta = _plain_area(t, "\n".join(_mem_membrs(m)), "", 130)
    ta.setObjectName("memEditText")
    v.addWidget(ta, 1)
    st = QLabel("")
    st.setFont(qfont(t, 11.5))
    st.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(st)
    row = QHBoxLayout()
    row.addStretch(1)
    b_cancel = Btn("取消", t, "ghost")
    b_ok = Btn("保存", t, "primary")
    row.addWidget(b_cancel)
    row.addWidget(b_ok)
    v.addLayout(row)

    def _do_save() -> None:
        lines = [s.strip() for s in ta.toPlainText().splitlines() if s.strip()]
        st.setText("保存中…")
        b_ok.setEnabled(False)

        def _done(r, e): # noqa: ANN001
            err = e or ((r or {}).get("error") if isinstance(r, dict) else None)
            if err:
                st.setText(f"更新失败：{err}")
                b_ok.setEnabled(True)
                return
            dlg.accept()
            if refresh_fn is not None:
                refresh_fn()
            if note is not None:
                note.setText("已更新")

        _async_post(None, "/api/memory",
                    {"action": "update", "chat_key": chat_key,
                     "user_id": m.get("userId"), "name": m.get("name"),
                     "contents": lines},
                    _done)

    b_ok.clicked.connect(_do_save)
    b_cancel.clicked.connect(dlg.reject)
    dlg.exec()


def _mem_deep_dlg(t: Tokens, btn, m: dict, chat_key: str, refresh_fn, note) -> None:
    """深度印象（web deepBtn :6926-6951）：先 POST /api/memory/deep-profile
    {user_id, name} 拿整理稿；ok 才弹窗（说明行=note + 稿件预填），
    「追加为印象」= 旧印象 + 稿件按行 合并整份覆盖；不 ok / 出错只更新状态行、不弹窗。"""
    name = m.get("name") or m.get("userId") or "某人"
    if note is not None:
        note.setText(f"正在整理「{name}」的全部历史印象…")

    def _fetched(r, e): # noqa: ANN001
        err = e or ((r or {}).get("error") if isinstance(r, dict) else None)
        if err:
            if note is not None:
                note.setText(f"整理失败：{err}")
            return
        if not (isinstance(r, dict) and r.get("ok")):
            if note is not None:
                note.setText("暂无可整理的记录："
                             + str((r or {}).get("note") or (r or {}).get("error") or "无返回"))
            return
        _mem_deep_show(t, btn, m, chat_key, refresh_fn, note,
                       str(r.get("note") or ""), str(r.get("text") or ""))

    _async_post(None, "/api/memory/deep-profile",
                {"user_id": m.get("userId") or "", "name": m.get("name") or ""},
                _fetched)


def _mem_deep_show(t: Tokens, btn, m: dict, chat_key: str, refresh_fn, note,
                   dnote: str, dtext: str) -> None:
    """深挖结果弹窗（web :6933-6948）：稿件可改，「追加为印象」合并旧印象整份覆盖。"""
    name = m.get("name") or m.get("userId") or "某人"
    dlg, v = _card_dialog(t, btn, f"「{name}」深度印象", width=640)
    dlg.resize(640, 460)
    head = QLabel(dnote)
    head.setFont(qfont(t, 12))
    head.setWordWrap(True)
    head.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(head)
    ta = _plain_area(t, dtext, "", 200)
    ta.setObjectName("memDeepText")
    v.addWidget(ta, 1)
    st = QLabel("")
    st.setFont(qfont(t, 11.5))
    st.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(st)
    row = QHBoxLayout()
    row.addStretch(1)
    b_cancel = Btn("取消", t, "ghost")
    b_ok = Btn("追加为印象", t, "primary")
    row.addWidget(b_cancel)
    row.addWidget(b_ok)
    v.addLayout(row)

    def _do_append() -> None:
        lines = [s.strip() for s in ta.toPlainText().splitlines() if s.strip()]
        st.setText("追加中…")
        b_ok.setEnabled(False)

        def _done(r, e): # noqa: ANN001
            err = e or ((r or {}).get("error") if isinstance(r, dict) else None)
            if err:
                st.setText(f"保存失败：{err}")
                b_ok.setEnabled(True)
                return
            dlg.accept()
            if refresh_fn is not None:
                refresh_fn()
            if note is not None:
                note.setText("已追加印象")

        _async_post(None, "/api/memory",
                    {"action": "update", "chat_key": chat_key,
                     "user_id": m.get("userId"), "name": m.get("name"),
                     "contents": _mem_membrs(m) + lines},
                    _done)

    b_ok.clicked.connect(_do_append)
    b_cancel.clicked.connect(dlg.reject)
    dlg.exec()


# ---------------------------------------------------------------- 分发表（panels_qt.build_panel 查这里）

MANUAL = {
    "overview": overview_panel,
    "check": check_panel,
    "persona": persona_panel,
    "memory": memory_panel,
    "sessions": sessions_panel,
    "log": log_panel,
    "json": json_panel,
    "vermat": vermat_panel,
}

# 面板追加区（build_panel 在元数据页构建后调用）——动态列表卡挂进元数据面板
# （APPENDIX 定义在 _wechat_emoji_appendix 之后，避免前向引用）


def _wechat_emoji_appendix(t: Tokens, page: QWidget) -> None:
    """表情包收藏夹（web #emojiBox :6820-6890 对齐）。

    GET /api/emojis → 名称网格（显示前 60）+ 搜索过滤 + 单个删除（POST /api/emojis/delete）
    + 刷新。图片走后台 URL 拉取（/assets/emoji/<name>），拉不到降级为名称块（可用性优先）。
    """
    card = Card(t)
    card.body.addWidget(h2(t, "表情包收藏夹（机器人 send_emoji 用）"))
    from panels_qt import _line # noqa: PLC0415

    search = _line(t, "", placeholder="搜索表情…")
    search.setObjectName("emojiSearch")
    card.body.addWidget(search)
    count_lb = desc(t, "读取中…")
    card.body.addWidget(count_lb)

    from PySide6.QtCore import Qt as _Qt, QTimer # noqa: PLC0415
    from PySide6.QtGui import QPixmap # noqa: PLC0415
    from PySide6.QtWidgets import QGridLayout, QLabel # noqa: PLC0415

    grid_w = QWidget()
    grid = QGridLayout(grid_w)
    grid.setContentsMargins(0, 2, 0, 2)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(8)
    card.body.addWidget(grid_w)
    note = desc(t, "")
    card.body.addWidget(note)
    page.layout().addWidget(card)

    state: dict = {"all": []}

    def _set_grid(rows: list) -> None:
        while grid.count():
            it = grid.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.deleteLater()
                wdg.setParent(None) # 立刻脱离父子树（见上）
        import urllib.parse as _up # noqa: PLC0415
        import urllib.request as _uq # noqa: PLC0415

        try:
            from agent_bridge import current_url # noqa: PLC0415

            base = current_url().split("?")[0].rstrip("/")
        except Exception: # noqa: BLE001
            base = "http://127.0.0.1:3210"
        for i, e in enumerate(rows):
            cell = QWidget()
            cell.setStyleSheet("background:transparent;")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(0)
            name = str(e.get("name") or "")
            img = QLabel()
            img.setFixedSize(44, 44)
            img.setAlignment(_Qt.AlignCenter)
            img.setStyleSheet("border:1px solid %s;border-radius:8px;background:rgba(0,0,0,.15);"
                              % getattr(t, "bd", "#334"))
            try:
                req = _uq.Request(base + "/assets/emoji/" + _up.quote(name),
                                  headers={"User-Agent": "persona-morph-qt"})
                with _uq.urlopen(req, timeout=3.0) as rr:
                    pm = QPixmap()
                    if pm.loadFromData(rr.read()) and not pm.isNull():
                        img.setPixmap(pm.scaled(42, 42, _Qt.KeepAspectRatio, _Qt.SmoothTransformation))
                    else:
                        raise ValueError("pm")
            except Exception: # noqa: BLE001 — 拉图失败降级为名称块（收藏夹仍可用）
                img.setText(name[:4])
                img.setToolTip(name)
            cv.addWidget(img)
            delb = QPushButton("×")
            delb.setFixedSize(18, 18)
            delb.setToolTip("删除 " + name)
            delb.clicked.connect(lambda _=False, n=name: _del(n))
            cv.addWidget(delb, 0, _Qt.AlignRight)
            grid.addWidget(cell, i // 8, i % 8)

    def _render() -> None:
        q = (search.text() or "").strip().lower()
        lst = [e for e in state["all"] if q in (e.get("name") or "").lower()]
        count_lb.setText("共 %d 个（显示前 60）" % len(lst) if lst else
                         ("没有匹配「%s」的表情" % q if q else
                          "收藏夹为空：群里收到好玩的表情后，机器人可用 collect_emoji 收藏。"))
        _set_grid(lst[:60])

    def _del(name: str) -> None:
        # 对齐 web uiConfirm 口径（assets/console/index.html:6755）：删表情前二次确认
        from confirm import ConfirmDialog # noqa: PLC0415
        d = ConfirmDialog(
            t, page, "删除表情", f"删除表情「{name}」？",
            ["会从收藏夹移除（机器人不再能发送它）"], confirm_label="删除")
        if not d.exec():
            return

        def _work(bx: dict) -> None:
            try:
                from agent_bridge import post_json # noqa: PLC0415

                bx["rsp"] = post_json("/api/emojis/delete", {"name": name}, timeout=8.0)
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        import threading as _th # noqa: PLC0415

        bx: dict = {"done": False, "rsp": None, "err": None, "tries": 0}
        _th.Thread(target=_work, daemon=True, args=(bx,), name="emoji-del").start()

        def _apply() -> None:
            if not _qt_alive(note): # 同上：页面销毁后本闭包仍会被定时器叫醒
                return
            if not bx["done"]:
                bx["tries"] += 1
                if bx["tries"] > 25:
                    return
                QTimer.singleShot(150, _apply)
                return
            note.setText(("已删除 " + name) if bx["err"] is None else ("删除失败：" + bx["err"]))
            _load()

        QTimer.singleShot(150, _apply)

    def _load() -> None:
        def _work(bx: dict) -> None:
            try:
                bx["rsp"] = config_io.get_json("/api/emojis", timeout=6.0)
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        import threading as _th # noqa: PLC0415

        bx: dict = {"done": False, "rsp": None, "err": None, "tries": 0}
        _th.Thread(target=_work, daemon=True, args=(bx,), name="emoji-load").start()

        def _apply() -> None:
            # 存活判 + 自链上限：本页是**可重建**的（切页/刷新），闭包挂在定时器上，
            # 页面销毁后 `_render()` 里的 `search.text()` 会打在已析构的 QLineEdit 上
            # （RuntimeError: Internal C++ object already deleted）。
            if not _qt_alive(search):
                return
            if not bx["done"]:
                bx["tries"] += 1
                if bx["tries"] > 25: # 25 × 150ms ≈ 3.8s，盖住 6s 超时的一半就够止损
                    return
                QTimer.singleShot(150, _apply)
                return
            state["all"] = (bx["rsp"] or {}).get("emojis") or []
            _render()

        QTimer.singleShot(150, _apply)

    search.textChanged.connect(_render)
    QTimer.singleShot(400, _load) # 面板建好后后台拉一次（不冻建页）


def _tools_utlist_appendix(t: Tokens, page: QWidget) -> None:
    """工具清单（web sec-tools 的 utGlobals/utProblems/utList :3826-3935 对齐）。

    数据源 = /api/status 的 user_tools 快照（tools/enabled/dir/ticked/counts_total/problems/error）；
    勾选切换 = GET /api/tools/toggle?name=&on=（routes 只注册 GET）。只读展示 + 勾选，不改配置文件。
    """
    card = Card(t)
    card.body.addWidget(h2(t, "自定义工具清单（勾选后模型才能用）"))
    ut_stat = desc(t, "清单现状读取中…")
    ut_stat.setWordWrap(True)
    card.body.addWidget(ut_stat)
    ut_prob = desc(t, "")
    ut_prob.setWordWrap(True)
    ut_prob.setObjectName("utProblemsQt") # 「看问题」按钮的滚动锚点
    card.body.addWidget(ut_prob)
    host = QWidget()
    host.setStyleSheet("background:transparent;")
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    card.body.addWidget(host)
    # ── 可搜目录（web fileSearch 卡 :3940-3975 对齐）：逐目录 打开/移除 ──
    card.body.addWidget(_divider_local(t))
    card.body.addWidget(h2(t, "可搜目录（文件搜索）"))
    fs_host = QWidget()
    fs_host.setStyleSheet("background:transparent;")
    fs_v = QVBoxLayout(fs_host)
    fs_v.setContentsMargins(0, 0, 0, 0)
    fs_v.setSpacing(4)
    card.body.addWidget(fs_host)
    fs_note = desc(t, "")
    card.body.addWidget(fs_note)
    page.layout().addWidget(card)

    from PySide6.QtWidgets import QCheckBox as _QCB # noqa: PLC0415 — 局部别名不遮蔽模块级名

    box: dict = {"done": False, "ut": None, "fs": None}
    _ut_keep: dict = {} # 每工具「试一下」的参数与结果（跨清单重载保留，web UT_KEEP :3875 同款）

    def _ut_test(name: str, ai, keep: dict, res_lb, tb) -> None:
        """web :3879-3900 同款：GET /api/tools/test?name=&args=——通了/没通都如实回显；
        测试不改清单 ⇒ 绝不触发重载（web :3897 注释同口径）。"""
        keep["args"] = ai.text()
        tb.setEnabled(False)
        tb.setText("发请求…")
        keep["err"] = False
        keep["text"] = "正在真发一次 HTTP 请求（最多等到这个工具的超时设置）…"
        res_lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
        res_lb.setText(keep["text"])

        def _work(bx: dict) -> None:
            try:
                bx["r"] = config_io.get_json(
                    "/api/tools/test?name=%s&args=%s"
                    % (_up.quote(name), _up.quote(ai.text() or "{}")), timeout=90.0)
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        bx: dict = {"done": False, "r": None, "err": None}
        _th.Thread(target=_work, daemon=True, args=(bx,), name="ut-test").start()

        def _apply_test() -> None:
            if not bx["done"]:
                QTimer.singleShot(300, _apply_test)
                return
            r = bx.get("r") if isinstance(bx.get("r"), dict) else {}
            if bx.get("err"):
                keep["err"] = True
                keep["text"] = "没通 · " + str(bx["err"])
            elif r.get("ok"):
                keep["text"] = "通了 · %sms · %s 返回：%s" % (
                    r.get("ms") or 0, r.get("host") or "", r.get("content") or "（空）")
            else:
                keep["err"] = True
                keep["text"] = "没通 · " + str(r.get("error") or r.get("content") or "未知错误")
            res_lb.setStyleSheet(
                f"color:{t.err if keep['err'] else t.tx3};background:transparent;")
            res_lb.setText(keep["text"])
            tb.setEnabled(True)
            tb.setText("试一下")

        QTimer.singleShot(300, _apply_test)

    def _fs_del(dir_path: str) -> None:
        """web :3961-3967：POST /api/file_search/del {dir} → 回显服务端 note + 重载清单。"""
        fs_note.setText("移除「%s」中…" % dir_path)
        _async_post(None, "/api/file_search/del", {"dir": dir_path},
                    lambda r, e: (fs_note.setText(
                        "移除失败：%s" % (e or (r or {}).get("error") or "后台没连上")
                        if (e or (isinstance(r, dict) and r.get("ok") is False))
                        else str((r or {}).get("note") or "已移除")),
                        _load()))

    def _load() -> None:
        def _work() -> None:
            st = config_io.get_json("/api/status", timeout=8.0) or {}
            box["ut"] = st.get("user_tools")
            box["fs"] = st.get("file_search")
            box["done"] = True

        _th.Thread(target=_work, daemon=True, name="ut-list").start()

        def _apply() -> None:
            # 存活判 + 自链上限（同 check 页 vf_state）：本闭包挂在定时器上，
            # 页面重建/关页后会继续重排并对已析构控件写文本 ⇒ 占位永不更新。
            if not _qt_alive(v):
                return
            if not box["done"]:
                box["tries"] = box.get("tries", 0) + 1
                if box["tries"] > 35: # 35 × 300ms ≈ 10.5s，够一次 8s 超时
                    ut_stat.setText("清单现状读不到（后台超时）")
                    return
                QTimer.singleShot(300, _apply)
                return
            while v.count(): # 重扫/导入后重载：先清掉上一轮的动态行
                it = v.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None) # 立刻脱离父子树（见 _clear_group_checks 注解）
                    w.deleteLater()
            # ── 可搜目录行（web :3950-3969 同款：文本 + 打开 + 移除）──
            while fs_v.count():
                it = fs_v.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None) # 立刻脱离父子树（见 _clear_group_checks 注解）
                    w.deleteLater()
            fs = box.get("fs") or {}
            ds = fs.get("dirs") or []
            if not ds:
                fs_v.addWidget(desc(t, "还没有可搜目录：在上方「可搜目录」输入框填一个目录点「加入目录」。"))
            for d in ds:
                if not isinstance(d, dict):
                    continue
                drow = QWidget()
                drow.setStyleSheet("background:transparent;")
                dh = QHBoxLayout(drow)
                dh.setContentsMargins(2, 1, 2, 1)
                dh.setSpacing(8)
                exists = d.get("exists")
                lb = QLabel("%s%s" % (d.get("dir") or "",
                                      ("　（%s 个文件）" % d.get("count")) if exists
                                      else "　（目录不存在）"))
                lb.setFont(qfont(t, 12))
                lb.setStyleSheet(
                    f"color:{t.err if not exists else t.tx};background:transparent;")
                dh.addWidget(lb, 1)
                b_open = Btn("打开", t, "ghost")
                b_open.setFixedWidth(54)
                b_open.clicked.connect(
                    lambda _=False, _d=str(d.get("dir") or ""): _async_post(
                        None, "/api/open-path", {"path": _d}, lambda r, e: None))
                b_rm = Btn("移除", t, "ghost")
                b_rm.setFixedWidth(54)
                b_rm.clicked.connect(
                    lambda _=False, _d=str(d.get("dir") or ""): _fs_del(_d))
                dh.addWidget(b_open)
                dh.addWidget(b_rm)
                fs_v.addWidget(drow)
            ut = box.get("ut")
            if not isinstance(ut, dict):
                ut_stat.setText("清单现状读取失败（后台没连上）")
                return
            if ut.get("error"):
                ut_stat.setText("读取失败：%s" % ut["error"])
                return
            tools = ut.get("tools") or []
            ut_stat.setText("总开关：%s ｜ 目录：%s ｜ 已装 %d 个 ｜ 已勾选 %d 个 ｜ 累计调用 %d 次" % (
                "开" if ut.get("enabled") else "关（清单不加载）",
                ut.get("dir") or "-", len(tools), int(ut.get("ticked") or 0),
                int(ut.get("counts_total") or 0)))
            ps = ut.get("problems") or []
            if not ps:
                ut_prob.setText("清单没有问题。")
            else:
                ut_prob.setText("\n".join(
                    "注意：%s：%s%s%s" % (
                        p.get("file") or "", p.get("why") or "",
                        (" ［%s%s］" % (p.get("code_label"), ("·" + str(p.get("code"))) if p.get("code") else ""))
                        if p.get("code_label") else "",
                        ("  → %s" % p.get("fix")) if p.get("fix") else "")
                    for p in ps if isinstance(p, dict)))

            def _mk_toggle(cb, name: str):
                def _flip() -> None:
                    on = 1 if cb.isChecked() else 0
                    r = config_io.get_json(
                        "/api/tools/toggle?name=%s&on=%d" % (_up.quote(str(name)), on),
                        timeout=8.0)
                    if not isinstance(r, dict) or r.get("ok") is False:
                        cb.setChecked(not cb.isChecked()) # 失败回滚勾选态，不骗人
                return _flip

            if not tools:
                empty = desc(t, "还没有自定义工具：点上方「怎么加工具」——它能在 tools.d/ 里直接生成一份可编辑的模板。")
                v.addWidget(empty)
                return
            for tl in tools:
                if not isinstance(tl, dict):
                    continue
                roww = QWidget()
                roww.setStyleSheet("background:transparent;")
                rh = QVBoxLayout(roww)
                rh.setContentsMargins(2, 2, 2, 2)
                rh.setSpacing(2)
                cb = _QCB(str(tl.get("name") or ""))
                cb.setChecked(bool(tl.get("enabled")))
                cb.clicked.connect(_mk_toggle(cb, tl.get("name") or ""))
                rh.addWidget(cb)
                info = desc(t, "[%s%s] %s · %s ｜ 调用 %d 次%s ｜ 最近 %s" % (
                    tl.get("source") or "第三方",
                    ("：" + str(tl["author"])) if tl.get("author") else "",
                    ("v" + str(tl["version"])) if tl.get("version") else "",
                    tl.get("host") or "-", int(tl.get("calls") or 0),
                    ("（失败 %d）" % int(tl["errors"])) if tl.get("errors") else "",
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(tl["last"]))
                    if isinstance(tl.get("last"), (int, float)) and tl.get("last") else "-"))
                rh.addWidget(info)
                if tl.get("usage"):
                    uh = desc(t, "用法：" + str(tl["usage"]))
                    rh.addWidget(uh)
                # 每行「试一下」（web :3866-3900）：args 输入 + 结果行；
                #   参数与结果存 _ut_keep（跨清单重载保留，web UT_KEEP :3875 同款）
                _nm = str(tl.get("name") or "")
                keep = _ut_keep.setdefault(_nm, {})
                hrow = QHBoxLayout()
                hrow.setContentsMargins(0, 0, 0, 0)
                hrow.setSpacing(6)
                ai = QLineEdit(str(keep.get("args") or "{}"))
                ai.setObjectName("utArgs")
                ai.setFixedWidth(170)
                ai.setFont(qfont(t, 11.5))
                ai.setToolTip('给这个工具的参数（JSON 对象，例：{"city": "北京"}）；'
                              "它会替换 url/query/body 里的 {占位}")
                ai.setStyleSheet(
                    f"QLineEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
                    f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:4px 8px;}}")
                ai.textChanged.connect(lambda txt, _k=keep: _k.__setitem__("args", txt))
                tb = Btn("试一下", t, "ghost")
                tb.setObjectName("utTest")
                res_lb = desc(t, str(keep.get("text") or ""))
                res_lb.setWordWrap(True)
                if keep.get("err"):
                    res_lb.setStyleSheet(f"color:{t.err};background:transparent;")
                hrow.addWidget(ai)
                hrow.addWidget(tb)
                hrow.addWidget(res_lb, 1)
                rh.addLayout(hrow)
                tb.clicked.connect(
                    lambda _=False, _n=_nm, _ai=ai, _k=keep, _r=res_lb, _t=tb:
                    _ut_test(_n, _ai, _k, _r, _t))
                v.addWidget(roww)

        QTimer.singleShot(300, _apply) # 首次调度：此前只有 done=false 的重试链，_apply 从未启动（清单卡永远「读取中」）

    import threading as _th # noqa: PLC0415
    import urllib.parse as _up # noqa: PLC0415

    page._ut_reload = _load # 「重新加载清单」/「导入」完成后重载本卡（panels_custom._act_ut_reload 调）
    QTimer.singleShot(400, _load)


def _model_local_appendix(t: Tokens, page: QWidget) -> None:
    """本机模型端点探测（web localProbe/localList :1444-1495 对齐）。

    GET /api/local-models → {meta:{all:[{id,name,base_url,reachable,ms,models,error}],checked,
    elapsed_ms}, found, capability}；连通测试 = GET /api/local-models?test=1&base_url=&model=。
    探测本身不改任何配置（本机模型零成本、不出网）；「用这个」只回填接口地址行，仍需点保存。
    """
    card = Card(t)
    card.body.addWidget(h2(t, "本机模型端点探测（Ollama / LM Studio / vLLM…）"))
    card.body.addWidget(desc(
        t, "探测只能证明端点活着、有哪些模型、多快；工具与视觉是否支持一律「未声明」——"
           "要判定请用本页的「测试 API」真跑一轮。探测本身不改任何配置。"))
    probe_row = QHBoxLayout()
    b_probe = Btn("探测本机端点", t, "primary")
    probe_hint = desc(t, "没发现本机端点（没装或没启动都算正常）")
    probe_hint.setWordWrap(True)
    probe_row.addWidget(b_probe)
    probe_row.addWidget(probe_hint, 1)
    card.body.addLayout(probe_row)
    list_host = QWidget()
    list_host.setStyleSheet("background:transparent;")
    lv = QVBoxLayout(list_host)
    lv.setContentsMargins(0, 0, 0, 0)
    lv.setSpacing(8)
    card.body.addWidget(list_host)
    page.layout().addWidget(card)

    def _use_local(base_url: str, model: str) -> None:
        """「用这个」：只把地址与模型名填进上方输入框，仍需用户点保存（web 同款语义）。"""
        filled_url = False
        filled_model = False
        for r, w in (getattr(page, "_c8_binds", None) or []):
            cfg = str(getattr(r, "cfg", "") or "")
            if cfg == "api.base_url" and hasattr(w, "setText"):
                w.setText(base_url)
                filled_url = True
            elif "model" in cfg and getattr(r, "kind", "") == "text" and hasattr(w, "setText") \
                    and not filled_model:
                w.setText(model)
                filled_model = True
        probe_hint.setText(
            ("已填入接口地址%s —— 记得点上方「保存设置」" % ("与模型名" if filled_model else ""))
            if filled_url else "没找到接口地址输入框（请手动填）")

    def _test_local(base_url: str, model: str, out) -> None:
        out.setText("测试中……")
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            box["r"] = config_io.get_json(
                "/api/local-models?test=1&base_url=%s&model=%s"
                % (_up2.quote(base_url), _up2.quote(model)), timeout=20.0)
            box["done"] = True

        _th2.Thread(target=_work, daemon=True, name="local-test").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            d = box.get("r") or {}
            out.setText(("能对话 · %sms · 回显「%s」 · %s" % (d.get("ms"), d.get("reply") or "", d.get("note") or ""))
                        if d.get("ok") else ("失败：%s" % (d.get("error") or "未知")))

        QTimer.singleShot(300, _apply)

    def _probe() -> None:
        b_probe.setEnabled(False)
        probe_hint.setText("探测中（每端点 1.2s 超时，并发）…")
        box: dict = {"done": False, "d": None}

        def _work() -> None:
            box["d"] = config_io.get_json("/api/local-models", timeout=25.0)
            box["done"] = True

        _th2.Thread(target=_work, daemon=True, name="local-probe").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            b_probe.setEnabled(True)
            d = box.get("d") or {}
            meta = d.get("meta") or {}
            while lv.count():
                it = lv.takeAt(0)
                if it.widget():
                    it.widget().setParent(None) # 立刻脱离父子树（只 deleteLater 时事件循环未转则被搁置，控件仍可见）
                    it.widget().deleteLater()
            for ep in (meta.get("all") or []):
                if not isinstance(ep, dict):
                    continue
                ok = bool(ep.get("reachable"))
                ms = ep.get("models") or []
                fr = QFrame()
                fr.setStyleSheet(
                    f"QFrame{{background:{_hex(rgba(t.q('tx'), 8))};"
                    f"border:1px solid {t.bd};border-radius:{t.radius_tile}px;}}")
                fv = QVBoxLayout(fr)
                fv.setContentsMargins(10, 8, 10, 8)
                fv.setSpacing(4)
                name = QLabel("%s　%s" % (ep.get("name") or ep.get("id") or "", ep.get("base_url") or ""))
                name.setFont(qfont(t, 12.5, 600))
                name.setStyleSheet(f"color:{t.tx};background:transparent;border:none;")
                name.setWordWrap(True)
                fv.addWidget(name)
                stx = desc(t, ("可用 · %sms · 模型 %d 个" % (ep.get("ms"), len(ms))) if ok
                           else ("未发现 · %s" % (ep.get("error") or "超时/未响应")))
                fv.addWidget(stx)
                if ms:
                    chips = desc(t, "  ".join(str(m) for m in ms[:12])
                                 + ("…" if len(ms) > 12 else ""))
                    fv.addWidget(chips)
                btn_row = QHBoxLayout()
                if ok and ms:
                    bu = Btn("用这个", t, "ghost")
                    bu.clicked.connect(lambda _ch=False, u=str(ep.get("base_url")), m=str(ms[0]):
                                       _use_local(u, m))
                    bt = Btn("连通测试", t, "ghost")
                    out = desc(t, "")
                    out.setWordWrap(True)
                    bt.clicked.connect(lambda _ch=False, u=str(ep.get("base_url")), m=str(ms[0]), o=out:
                                       _test_local(u, m, o))
                    btn_row.addWidget(bu)
                    btn_row.addWidget(bt)
                    btn_row.addWidget(out, 1)
                else:
                    btn_row.addStretch(1)
                fv.addLayout(btn_row)
                lv.addWidget(fr)
                if ok and ms:
                    # fr 内控件引用保留在布局树即可；无额外状态
                    pass
            tail = desc(t, "共探 %s 个端点，用时 %sms。能力口径：%s" % (
                meta.get("checked", 0), meta.get("elapsed_ms", 0), d.get("capability") or "—"))
            lv.addWidget(tail)
            found = d.get("found") or []
            probe_hint.setText(("发现 %d 个可用端点" % len(found)) if found
                               else "没发现本机端点（没装或没启动都算正常）")

        QTimer.singleShot(300, _apply)

    import threading as _th2 # noqa: PLC0415
    import urllib.parse as _up2 # noqa: PLC0415

    b_probe.clicked.connect(_probe)


def _sd_local_appendix(t: Tokens, page: QWidget) -> None:
    """本地生图后端卡（web sdLocal* :8015-8107 全套对齐）。

    GET /api/image_gen/local → {status:{ok,installed,why,preset,presets:[…]}}；
    ?estimate=1 → {estimate:{gb,model_gb,deps_gb,mbps,source,human,warn}}；
    /progress → {progress:{running,done_bytes,total_bytes,percent,mbps,eta_seconds,message,ok}}；
    install/start/stop/preset POST → {ok, note}。安装确认后 1s 轮询进度，
    空闲每 8s 刷新状态（web setInterval 8000 同款）。
    """
    import threading as _th # noqa: PLC0415
    import urllib.parse as _up # noqa: PLC0415

    from PySide6.QtWidgets import QComboBox, QProgressBar # noqa: PLC0415

    def _fmt_gb(b) -> str:
        try:
            return "%.2f GB" % (float(b) / 1073741824.0)
        except Exception: # noqa: BLE001
            return "?"

    card = Card(t)
    card.body.addWidget(h2(t, "本地生图后端（可选；装完全在本机跑、不出网）"))
    card.body.addWidget(desc(t, "速度档 / 画质档都能装、能切，装哪个用哪个由你挑；"
                                "安装在后台进行，进度在这张卡里能看到。"))
    st_lb = QLabel("读取中…")
    st_lb.setFont(qfont(t, 13, 600))
    st_lb.setStyleSheet(f"color:{t.tx};background:transparent;")
    why_lb = desc(t, "")
    why_lb.setWordWrap(True)
    card.body.addWidget(st_lb)
    card.body.addWidget(why_lb)

    preset_row = QHBoxLayout()
    preset_row.addWidget(QLabel("档位"))
    preset_sel = QComboBox()
    preset_sel.setMinimumWidth(260)
    preset_sel.setFixedHeight(30)
    preset_sel.setFont(qfont(t, t.body_size))
    preset_sel.setStyleSheet(
        f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
        f"color:{t.tx};border:1px solid {t.bd};}}")
    preset_row.addWidget(preset_sel, 1)
    card.body.addLayout(preset_row)
    preset_note = desc(t, "")
    preset_note.setWordWrap(True)
    card.body.addWidget(preset_note)

    bar = QProgressBar()
    bar.setObjectName("sdLocalBar")
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    bar.setStyleSheet(
        f"QProgressBar{{background:{rgba(t.q('tx'), 24).name(QColor.NameFormat.HexArgb)};"
        f"border:none;border-radius:4px;}}"
        f"QProgressBar::chunk{{background:{t.blue};border-radius:4px;}}")
    bar.hide()
    prog_txt = QLabel("")
    prog_txt.setFont(qfont(t, 12))
    prog_txt.setWordWrap(True)
    prog_txt.setStyleSheet(f"color:{t.tx2};background:transparent;")
    prog_txt.hide()
    card.body.addWidget(bar)
    card.body.addWidget(prog_txt)

    btn_row = QHBoxLayout()
    b_inst = Btn("下载当前档", t, "primary")
    b_inst.setObjectName("sdLocalInstall")
    b_start = Btn("启动本地服务", t, "ghost")
    b_start.setObjectName("sdLocalStart")
    b_stop = Btn("停止", t, "ghost")
    b_stop.setObjectName("sdLocalStop")
    for b in (b_inst, b_start, b_stop):
        btn_row.addWidget(b)
    btn_row.addStretch(1)
    card.body.addLayout(btn_row)
    page.layout().addWidget(card)

    state: dict = {"presets": [], "preset": "", "polling": False, "filling": False}
    poll_timer = QTimer(card)
    poll_timer.setInterval(1000)
    idle_timer = QTimer(card)
    idle_timer.setInterval(8000)

    def _get(api: str, on_done, timeout: float = 8.0, guard=None) -> None: # noqa: ANN001
        """异步 GET → on_done(r)。

        `guard` 是要写结果的控件（调用方给出）。页面重建/关页后本闭包仍挂在定时器上，
        `on_done` 里对已析构的 C++ 对象 `setText` 会抛 RuntimeError —— 这里先探活，
        已死就直接退出（不再回调）。不传 guard 时退回旧行为。
        """
        box: dict = {"done": False, "r": None, "tries": 0}

        def _work() -> None:
            box["r"] = config_io.get_json(api, timeout=timeout)
            box["done"] = True

        _th.Thread(target=_work, daemon=True, name="sd-local-get").start()

        def _apply() -> None:
            if guard is not None and not _qt_alive(guard):
                return
            if not box["done"]:
                box["tries"] += 1
                if box["tries"] > 40: # 40 × 300ms ≈ 12s，够一次 8s 超时
                    return
                QTimer.singleShot(300, _apply)
                return
            on_done(box.get("r"))

        QTimer.singleShot(300, _apply)

    def _post(api: str, on_done, timeout: float = 30.0, body: dict | None = None, # noqa: ANN001
              guard=None) -> None:
        box: dict = {"done": False, "r": None, "err": None, "tries": 0}

        def _work() -> None:
            try:
                from agent_bridge import post_json # noqa: PLC0415
                box["r"] = post_json(api, body or {}, timeout=timeout)
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        _th.Thread(target=_work, daemon=True, name="sd-local-post").start()

        def _apply() -> None:
            if guard is not None and not _qt_alive(guard):
                return
            if not box["done"]:
                box["tries"] += 1
                if box["tries"] > 110: # 110 × 300ms ≈ 33s，盖住 30s 超时
                    return
                QTimer.singleShot(300, _apply)
                return
            on_done(box.get("r"), box.get("err"))

        QTimer.singleShot(300, _apply)

    def paint(p: dict | None) -> None:
        # web paint :8019-8030 同款：没在跑也没下载量 → 收起进度条
        if not p or (not p.get("running") and not p.get("total_bytes")):
            bar.hide()
            prog_txt.hide()
            return
        bar.show()
        prog_txt.show()
        total = p.get("total_bytes") or 0
        done = p.get("done_bytes") or 0
        pct = min(100.0, 100.0 * done / total) if total else 0.0
        bar.setValue(int(pct))
        eta = p.get("eta_seconds") or 0
        eta_s = ("，预计还需 %.1f 小时" % (eta / 3600.0)) if eta > 3600 \
            else ("，预计还需 %d 分钟" % max(1, int(eta / 60))) if eta else ""
        tail = "" if p.get("running") else (" ｜ ✅ 完成" if p.get("ok") else " ｜ ❌ 失败")
        mbps_s = " ｜ %.1f MB/s" % p["mbps"] if p.get("mbps") else ""
        prog_txt.setText("%s ｜ 已下 %s / 共 %s ｜ %.1f%%%s%s%s" % (
            p.get("message") or "", _fmt_gb(done), _fmt_gb(total), pct, mbps_s, eta_s, tail))

    def tick() -> None:
        _get("/api/image_gen/local/progress", lambda r: _tick_done((r or {}).get("progress")), guard=st_lb)

    def _tick_done(p: dict | None) -> None:
        paint(p)
        if p is not None and not p.get("running") and state["polling"]:
            state["polling"] = False
            poll_timer.stop()
            refresh()

    def _fill_presets(st: dict) -> None:
        # web :8048-8062 同款：填充防回环；当前档说明+许可；安装按钮文字随档位
        state["presets"] = list(st.get("presets") or [])
        state["preset"] = st.get("preset") or ""
        state["filling"] = True
        try:
            preset_sel.clear()
            for p in state["presets"]:
                preset_sel.addItem(
                    "%s（%s）" % (p.get("label") or p.get("id"),
                                  "已装" if p.get("installed") else "要下 %s GB" % (p.get("gb") or "?")),
                    p.get("id"))
            i = preset_sel.findData(state["preset"], Qt.ItemDataRole.UserRole)
            if i >= 0:
                preset_sel.setCurrentIndex(i)
        finally:
            state["filling"] = False
        cur = next((p for p in state["presets"] if p.get("id") == state["preset"]), {})
        preset_note.setText("%s%s" % (cur.get("note") or "",
                                      ("｜许可：" + cur["license"]) if cur.get("license") else ""))
        b_inst.setText("重新下载当前档" if cur.get("installed")
                       else "下载当前档（%s GB）" % (cur.get("gb") or "?"))

    def refresh() -> None:
        _get("/api/image_gen/local", _refresh_done, guard=st_lb)

    def _refresh_done(r) -> None:
        if r is None:
            st_lb.setText("查询失败：后台没连上")
            return
        st = (r or {}).get("status") or {}
        if st.get("ok"):
            st_lb.setText("已就绪（本地跑，不出网）")
            st_lb.setStyleSheet(f"color:{t.ok};background:transparent;")
            paint(None)
        elif st.get("installed"):
            st_lb.setText("已安装，服务未启动（%s）" % (st.get("why") or ""))
            st_lb.setStyleSheet(f"color:{t.warn};background:transparent;")
        else:
            st_lb.setText("未安装")
            st_lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
        why_lb.setText(st.get("why") or "")
        _fill_presets(st)

    def _install() -> None:
        # web :8068-8084 同款：先拿估算，确认后后台安装 + 开轮询
        st_lb.setText("拿安装信息中…")

        def _est_done(r) -> None:
            est = (r or {}).get("estimate") if isinstance(r, dict) else None
            if not est:
                st_lb.setText("拿不到安装信息，稍后再试")
                return
            cons = ["要下载：约 %s GB（模型 %s GB + 运行库 %s GB）"
                    % (est.get("gb"), est.get("model_gb"), est.get("deps_gb")),
                    "现在实测速度：%s MB/s（源：%s）" % (est.get("mbps"), est.get("source")),
                    "预计耗时：%s" % (est.get("human") or "?")]
            if est.get("warn"):
                cons.append(str(est["warn"]))
            cons.append("点「开始下载」就在后台进行，你可以去办别的事，进度在这张卡里看")
            dlg = ConfirmDialog(t, card, "安装本地生图后端？",
                                "装完生图全在本机跑、不出网。", cons,
                                confirm_label="开始下载", dangerous=False)
            if not dlg.exec():
                st_lb.setText("未安装")
                return
            _post("/api/image_gen/local/install", _install_done, timeout=60.0, guard=st_lb)

        _get("/api/image_gen/local?estimate=1", _est_done, timeout=20.0, guard=st_lb)

    def _install_done(r, e) -> None:
        if e or not isinstance(r, dict) or r.get("ok") is False:
            prog_txt.setText("安装没起来：%s" % (e or (r or {}).get("error") or "后台没连上"))
            prog_txt.show()
            return
        prog_txt.setText(r.get("note") or "已开始安装")
        prog_txt.show()
        if not state["polling"]:
            state["polling"] = True
            poll_timer.start()

    def _start() -> None:
        # 服务端等模型加载最多 60 秒 ⇒ 前端给 90 秒（web :8086-8092 假失败教训同款）
        prog_txt.setText("正在启动本地服务…（首次要加载模型，最多约 1 分钟）")
        prog_txt.show()
        _post("/api/image_gen/local/start", _start_done, timeout=90.0, guard=st_lb)

    def _start_done(r, e) -> None:
        if e or not isinstance(r, dict) or r.get("ok") is False:
            prog_txt.setText("启动失败：%s" % (e or (r or {}).get("error") or "后台没连上"))
            return
        prog_txt.setText(r.get("note") or "已启动")
        refresh()

    def _preset_apply(r, e) -> None:
        if e or not isinstance(r, dict) or r.get("ok") is False:
            prog_txt.setText("切换失败：%s" % (e or (r or {}).get("error") or "后台没连上"))
        else:
            prog_txt.setText(r.get("note") or "已切换")
        prog_txt.show()
        refresh()

    def _preset_changed(idx: int) -> None:
        if state["filling"] or idx < 0:
            return
        pid = preset_sel.itemData(idx, Qt.ItemDataRole.UserRole)
        _post("/api/image_gen/local/preset", _preset_apply, body={"preset": pid}, guard=st_lb)

    def _stop_done(r, e) -> None:
        if e:
            prog_txt.setText("停止失败：%s" % e)
        else:
            prog_txt.setText(r.get("note") or "已停止" if isinstance(r, dict) else "已停止")
        prog_txt.show()
        refresh()

    def _stop() -> None:
        _post("/api/image_gen/local/stop", _stop_done, guard=st_lb)

    def _idle() -> None:
        if page.isVisible() and not state["polling"]:
            refresh()

    poll_timer.timeout.connect(tick)
    idle_timer.timeout.connect(_idle)
    preset_sel.currentIndexChanged.connect(_preset_changed)
    b_inst.clicked.connect(_install)
    b_start.clicked.connect(_start)
    b_stop.clicked.connect(_stop)
    idle_timer.start()
    refresh()


def _tts_probe_appendix(t: Tokens, page: QWidget) -> None:
    """TTS / 变声服务连通测试卡（web ttsProbe :1790-1807、vcProbe :1823-1841 对齐）。

    读上方输入框当前值（page._c8_binds 找 voice_reply.http_url / voice_reply.vc_url，
    找不到回退 config 现值）→ GET /api/voice/probe?url= / /api/voice/vc-probe?url=，
    回显 通/不通/失败；测试中按钮禁用（web disabled 同款）。
    """
    import threading as _th # noqa: PLC0415
    import urllib.parse as _up # noqa: PLC0415

    card = Card(t)
    card.body.addWidget(h2(t, "连通测试"))
    card.body.addWidget(desc(t, "这是你自己电脑上跑起来的服务（GPT-SoVITS 文字→音频；"
                                "RVC 音频→音频变声要另填第二段地址）。测试只证明端点通不通、"
                                "返回的是不是音频；变声测试会送一段 0.4 秒测试音进去。"))
    rows: list[tuple[Btn, QLabel]] = []

    def _make_row(label: str, btn_text: str, api: str, cfg: str) -> None:
        row = QHBoxLayout()
        lb = QLabel(label)
        lb.setFont(qfont(t, 12.5))
        lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
        b = Btn(btn_text, t, "ghost")
        out = QLabel("")
        out.setFont(qfont(t, 12))
        out.setWordWrap(True)
        out.setStyleSheet(f"color:{t.tx3};background:transparent;")
        row.addWidget(lb)
        row.addWidget(b)
        row.addWidget(out, 1)
        card.body.addLayout(row)
        rows.append((b, out))

        def _url() -> str:
            for r, w in (getattr(page, "_c8_binds", None) or []):
                if str(getattr(r, "cfg", "") or "") == cfg and hasattr(w, "text"):
                    return str(w.text()).strip()
            return str(config_io.read_path(cfg) or "").strip()

        def _run() -> None:
            b.setEnabled(False)
            out.setText("测试中…")
            box: dict = {"done": False, "r": None, "err": None}

            def _work() -> None:
                try:
                    box["r"] = config_io.get_json(
                        "%s?url=%s" % (api, _up.quote(_url())), timeout=30.0)
                except Exception as e: # noqa: BLE001
                    box["err"] = str(e)
                box["done"] = True

            _th.Thread(target=_work, daemon=True, name="voice-probe").start()

            def _apply() -> None:
                if not box["done"]:
                    QTimer.singleShot(300, _apply)
                    return
                b.setEnabled(True)
                d = box.get("r") if isinstance(box.get("r"), dict) else {}
                if box.get("err"):
                    out.setText("失败：%s" % box["err"])
                    out.setStyleSheet(f"color:{t.err};background:transparent;")
                elif d.get("ok"):
                    msg = "通：%s 字节音频 · %sms" % (d.get("bytes") or 0, d.get("ms") or 0)
                    if d.get("mode"):
                        msg += " · %s" % d["mode"]
                    out.setText(msg)
                    out.setStyleSheet(f"color:{t.ok};background:transparent;")
                else:
                    out.setText("不通：%s" % (d.get("why") or "未知原因"))
                    out.setStyleSheet(f"color:{t.err};background:transparent;")

            QTimer.singleShot(300, _apply)

        b.clicked.connect(_run)

    _make_row("TTS 服务", "连通测试", "/api/voice/probe", "voice_reply.http_url")
    _make_row("变声服务", "变声连通测试", "/api/voice/vc-probe", "voice_reply.vc_url")
    page.layout().addWidget(card)


# ── community / feedback 动作接线 ──
#
# 分工：纯 POST、无上下文依赖的动作进 panels_qt._ACT_API（导出/打开目录/补发）；
# 要读同页表单、带确认框、多态回显的（提交反馈、上传确认、种子导入）注册进
# ACT_CUSTOM —— (handler, tooltip)，handler 签名 (btn, note)，note 是按钮组
# 行内回显 QLabel（btn.property("c8_note") 同一引用），随语义色如实回显。


def _c8_page_of(w) -> QWidget | None:
    """沿父链爬到面板页（挂着 _c8_binds 控件索引的那个 page）。"""
    p = w.parentWidget()
    while p is not None:
        if hasattr(p, "_c8_binds"):
            return p
        p = p.parentWidget()
    return None


def _c8_find_row(page, want):
    """page._c8_binds 按 Row 谓词找控件 —— 无 cfg 的表单行（feedback 全套、
    community 导入 textarea）只能按 label/kind 匹配。"""
    for r, w in (getattr(page, "_c8_binds", None) or []):
        if want(r):
            return w
    return None


def _c8_say(note, t: Tokens, text: str, tone: str = "") -> None:
    """行内回显（web fbRst / uploadRst / exportRst 的 Qt 等价）：tone → 语义色。"""
    color = {"ok": t.ok, "warn": t.warn, "err": t.err}.get(tone, t.tx3)
    note.setStyleSheet(f"color:{color};background:transparent;")
    note.setText(text)


def _fb_submit(btn, note) -> None:
    """提交反馈（web fbSubmit 全对齐）：校验（内容必有 / 邮箱格式）→
    POST /api/feedback/submit → sent / queued / blocked 三态回显；
    blocked（限流）不清空输入框——内容还给用户，改改或等会儿再发。
    Qt 壳暂不做附件选择，files 恒空（附件在网页控制台里添加）。
    """
    import re as _re # noqa: PLC0415
    import threading as _th # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    page = _c8_page_of(btn)
    kind_w = _c8_find_row(page, lambda r: r.kind == "select" and r.label == "类型")
    text_w = _c8_find_row(page, lambda r: r.kind == "textarea" and r.label == "内容")
    mail_w = _c8_find_row(page, lambda r: r.kind == "text" and r.label == "联系邮箱")
    kind = str((kind_w.currentData() if kind_w is not None else None) or "其他")
    text = text_w.toPlainText() if text_w is not None else ""
    contact = mail_w.text() if mail_w is not None else ""
    if not text.strip():
        _c8_say(note, t, "先写点内容吧", "err")
        return
    if contact.strip() and not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", contact.strip()):
        _c8_say(note, t, "联系邮箱写得不太对（像这样：xxx@qq.com），不想留就清空它", "err")
        return
    btn.setEnabled(False)
    _c8_say(note, t, "提交中…")
    box: dict = {"done": False, "r": None, "err": None}

    def _work() -> None:
        try:
            box["r"] = post_json("/api/feedback/submit",
                                 {"kind": kind, "text": text, "contact": contact, "files": []},
                                 timeout=30.0)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    _th.Thread(target=_work, daemon=True, name="fb-submit").start()

    def _apply() -> None:
        if not box["done"]:
            QTimer.singleShot(150, _apply)
            return
        btn.setEnabled(True)
        if box["err"]:
            _c8_say(note, t, str(box["err"]), "err")
            return
        d = box["r"] if isinstance(box["r"], dict) else {}
        via = {"smtp": "邮件", "webhook": "推送到你的群/设备", "upload_url": "网址"}.get(
            d.get("via"), "已送出")
        n = ("，带 %s 个附件" % d.get("files")) if d.get("files") else ""
        if d.get("state") == "sent":
            _c8_say(note, t, "已发出（%s%s）" % (via, n), "ok")
        elif d.get("state") == "queued":
            _c8_say(note, t, "注意：已存在本机，但还没发出去：%s（待发 %s 条）"
                    % (d.get("why") or "", d.get("pending") or 0), "warn")
        elif d.get("state") == "blocked":
            _c8_say(note, t, "%s——这条没有发出，也没保存，内容还在框里。"
                    % (d.get("why") or "发得太频繁了"), "warn")
        else:
            _c8_say(note, t, str(d.get("why") or "提交失败"), "err")
        if d.get("state") != "blocked" and text_w is not None:
            text_w.setPlainText("")
        rl = getattr(page, "_fb_reload", None)
        if rl is not None:
            rl()

    QTimer.singleShot(150, _apply)


def _upload(kind: str, btn, note) -> None:
    """确认上传（web uploadSeeds / uploadFeedback 对齐）：确认框 →
    POST /api/community/upload {kind} → 已上传 X 条 / 上传失败。"""
    import threading as _th # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    ask = ("确认把当前种子库上传到配置的服务器？" if kind == "holyshits"
           else "确认把意见反馈上传到配置的服务器？")
    dlg = ConfirmDialog(t, btn.window(), "上传确认", ask,
                        ["数据会 POST 到你配置的上传 URL（自己的服务器，不是官方）",
                         "上传内容：" + ("当前种子库" if kind == "holyshits" else "已记录的意见反馈")],
                        "确认上传", "先不上传", dangerous=False)
    if not dlg.exec():
        return
    btn.setEnabled(False)
    _c8_say(note, t, "上传中…")
    box: dict = {"done": False, "r": None, "err": None}

    def _work() -> None:
        try:
            box["r"] = post_json("/api/community/upload", {"kind": kind}, timeout=30.0)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    _th.Thread(target=_work, daemon=True, name="community-upload").start()

    def _apply() -> None:
        if not box["done"]:
            QTimer.singleShot(150, _apply)
            return
        btn.setEnabled(True)
        if box["err"]:
            _c8_say(note, t, "上传失败：%s" % box["err"], "err")
            return
        d = box["r"] if isinstance(box["r"], dict) else {}
        if d.get("ok"):
            head = "意见已上传" if kind == "feedback" else "已上传"
            _c8_say(note, t, "%s %s 条" % (head, d.get("count") or d.get("uploaded") or 0), "ok")
        else:
            _c8_say(note, t, "上传失败：%s" % (d.get("error") or "未配置"), "err")

    QTimer.singleShot(150, _apply)


def _upload_seeds(btn, note) -> None:
    _upload("holyshits", btn, note)


def _upload_feedback(btn, note) -> None:
    _upload("feedback", btn, note)


def _seed_import_post(btn, note, text: str, ok_prefix: str) -> None:
    """导入种子库的公共发送段：POST /api/scoring/import {text} → 计数回显。"""
    import threading as _th # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    btn.setEnabled(False)
    _c8_say(note, t, "导入中…")
    box: dict = {"done": False, "r": None, "err": None}

    def _work() -> None:
        try:
            box["r"] = post_json("/api/scoring/import", {"text": text}, timeout=30.0)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    _th.Thread(target=_work, daemon=True, name="seed-import").start()

    def _apply() -> None:
        if not box["done"]:
            QTimer.singleShot(150, _apply)
            return
        btn.setEnabled(True)
        if box["err"]:
            _c8_say(note, t, "导入失败：%s" % box["err"], "err")
            return
        d = box["r"] if isinstance(box["r"], dict) else {}
        if d.get("ok"):
            suffix = "（查重后）" if ok_prefix.startswith("从文件") else ""
            _c8_say(note, t, "%s %s 条%s" % (ok_prefix, d.get("imported"), suffix), "ok")
        else:
            _c8_say(note, t, "失败：%s" % (d.get("error") or ""), "err")

    QTimer.singleShot(150, _apply)


def _seed_import(btn, note) -> None:
    """粘贴导入（web seedImportBtn 对齐）：读同页「导入金句种子」textarea。"""
    page = _c8_page_of(btn)
    ta = _c8_find_row(page, lambda r: r.kind == "textarea" and r.label == "导入金句种子")
    text = ta.toPlainText() if ta is not None else ""
    t = getattr(btn, "t", None)
    if not text.strip():
        if note is not None and t is not None:
            _c8_say(note, t, "请先粘贴要导入的金句文本", "warn")
        return
    _seed_import_post(btn, note, text, "已导入")


def _seed_import_file(btn, note) -> None:
    """选择文件导入（web seedImportFile 对齐）：txt/json → 读文本 → 服务端查重合并。"""
    from PySide6.QtWidgets import QFileDialog # noqa: PLC0415

    path, _fl = QFileDialog.getOpenFileName(btn.window(), "选择要导入的文件", "",
                                            "文本/JSON (*.txt *.json)")
    if not path:
        return
    t = getattr(btn, "t", None)
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e: # noqa: BLE001
        if note is not None and t is not None:
            _c8_say(note, t, "读文件失败：%s" % e, "err")
        return
    if not text.strip():
        if note is not None and t is not None:
            _c8_say(note, t, "文件为空", "warn")
        return
    _seed_import_post(btn, note, text, "从文件导入")


def _post_action_raw(btn, note, run, busy: str = "执行中…", done=None) -> None:
    """按钮动作的公共发送段：run() 在后台线程跑（成功返回回显文案，失败抛异常）；
    busy/结果回显 note（主线程落地）；done 在落定后回调（恢复按钮文字等）。"""
    import threading as _th # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    btn.setEnabled(False)
    _c8_say(note, t, busy)
    box: dict = {"done": False, "msg": None, "err": None}

    def _work() -> None:
        try:
            box["msg"] = run()
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    _th.Thread(target=_work, daemon=True, name="act-custom").start()

    def _apply() -> None:
        if not box["done"]:
            QTimer.singleShot(150, _apply)
            return
        btn.setEnabled(True)
        if done is not None:
            done()
        if box["err"]:
            _c8_say(note, t, "失败：%s" % box["err"], "err")
            return
        _c8_say(note, t, str(box["msg"]), "ok")

    QTimer.singleShot(150, _apply)


def _act_ui_layout_reload(btn, note) -> None:
    """刷新 UI 布局状态（web uiLayoutReload :6429 附近对齐——web 整页刷新，
    Qt 壳重读一次标定状态回显）。"""
    def _run() -> str:
        r = config_io.get_json("/api/ui-layout", timeout=15.0) or {}
        it = r.get("layout") or {}
        items = it.get("sidebar_items") or []
        names = ",".join(str(x) for x in items)
        return ("已标定 %d 个侧栏图标" % len(items)) + (("（%s）" % names) if names else "")

    _post_action_raw(btn, note, _run, "读取布局状态…")


def _act_ui_recalibrate(btn, note) -> None:
    """重新标定（web uiRecalibrate 对齐）：后台接管微信窗口标定图标位置，
    期间按钮禁用变「标定中…（微信前台）」，完成回显检测到的图标数。"""
    from agent_bridge import post_json # noqa: PLC0415

    btn.setText("标定中…（微信前台）")

    def _run() -> str:
        r = post_json("/api/ui/recalibrate", {}, timeout=120.0) or {}
        if r.get("ok"):
            return "标定完成：检测到 %s 个侧栏图标" % r.get("count")
        raise RuntimeError(str(r.get("error") or "标定失败"))

    _post_action_raw(btn, note, _run, "标定中…（微信前台）",
                     done=lambda: btn.setText("重新标定（接管鼠标）"))


def _act_seed_reload(btn, note) -> None:
    """种子库状态刷新（web loadSeedStats :7904-7912 对齐）。"""
    from agent_bridge import post_json # noqa: PLC0415

    def _run() -> str:
        r = post_json("/api/scoring/stats", {}, timeout=30.0) or {}
        d = r.get("data") or {}
        return "种子库 %s 条 · 已学反应 %s 条 · 高分参考 %s 条" % (
            d.get("seed_count") or 0, d.get("reaction_count") or 0, len(d.get("top") or []))

    _post_action_raw(btn, note, _run, "读取种子库状态…")


def _act_learn_apply(btn, note) -> None:
    """确定学习（web learnApply :7958-7964 对齐）：开启学习机制。"""
    from agent_bridge import post_json # noqa: PLC0415

    def _run() -> str:
        r = post_json("/api/learning/start", {}, timeout=30.0) or {}
        if r.get("ok"):
            return str(r.get("note") or "已开启")
        raise RuntimeError(str(r.get("error") or r.get("note") or "开启失败"))

    _post_action_raw(btn, note, _run, "正在确认学习机制…")


def _act_learn_eval(btn, note) -> None:
    """学习评估（web learnEval :7965-7971 对齐）：模型按评分细则打分，可能要一会儿。"""
    from agent_bridge import post_json # noqa: PLC0415

    def _run() -> str:
        r = post_json("/api/learning/evaluate", {}, timeout=180.0) or {}
        if r.get("eval"):
            return "评估：%s" % r.get("eval")
        raise RuntimeError(str(r.get("error") or r.get("note") or "评估完成"))

    _post_action_raw(btn, note, _run, "正在让模型评估学习效果（按评分细则）…")


_BG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


def _act_bg_upload(btn, note) -> None:
    """上传背景（web bgUpload :3176-3191 对齐）：选图 → dataURL → POST ui/background。"""
    import base64 as _b64 # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog # noqa: PLC0415

    path, _fl = QFileDialog.getOpenFileName(
        btn.window(), "选择背景图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
    if not path:
        return
    t = getattr(btn, "t", None)
    ext = Path(path).suffix.lower()
    mime = _BG_MIME.get(ext)
    if mime is None:
        if note is not None and t is not None:
            _c8_say(note, t, "先选一张 png/jpg/webp/gif/bmp 图片", "warn")
        return
    try:
        data = _b64.b64encode(Path(path).read_bytes()).decode("ascii")
    except Exception as e: # noqa: BLE001
        if note is not None and t is not None:
            _c8_say(note, t, "读取文件失败：%s" % e, "err")
        return

    from agent_bridge import post_json # noqa: PLC0415

    def _run() -> str:
        r = post_json("/api/ui/background", {"data": "data:%s;base64,%s" % (mime, data)},
                      timeout=60.0) or {}
        if r.get("ok"):
            return str(r.get("note") or "背景已应用")
        raise RuntimeError(str(r.get("error") or "上传失败"))

    _post_action_raw(btn, note, _run, "上传中…")


def _act_bg_clear(btn, note) -> None:
    """恢复默认背景（web bgClear :3193-3198 对齐）。"""
    from agent_bridge import post_json # noqa: PLC0415

    def _run() -> str:
        r = post_json("/api/ui/background", {"clear": True}, timeout=30.0) or {}
        if r.get("ok"):
            return "已恢复默认背景"
        raise RuntimeError(str(r.get("error") or "操作失败"))

    _post_action_raw(btn, note, _run, "恢复中…")


def _act_cursor_reset(btn, note) -> None:
    """重置光标（web cursorReset :3108-3120 对齐）：POST reset 清残留文件
    → 写回默认配置（whale_cursor 开、自定义清空）。"""
    from agent_bridge import post_json # noqa: PLC0415

    def _run() -> str:
        post_json("/api/cursor/reset", {}, timeout=30.0)
        config_io.write_patch({"ui": {"whale_cursor": True, "cursor_image": ""}})
        return "已重置为默认鲸鱼（马上生效）"

    _post_action_raw(btn, note, _run, "重置中…")


def _act_cursor_save(btn, note) -> None:
    """保存光标设置（web cursorSaveBtn :3123-3137 对齐）：选图 → POST upload
    → 写配置 cursor_image=custom → 光标生效。"""
    import base64 as _b64 # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog # noqa: PLC0415

    from agent_bridge import post_json # noqa: PLC0415

    path, _fl = QFileDialog.getOpenFileName(
        btn.window(), "选择光标图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
    if not path:
        return
    t = getattr(btn, "t", None)
    ext = Path(path).suffix.lower()
    mime = _BG_MIME.get(ext)
    if mime is None:
        if note is not None and t is not None:
            _c8_say(note, t, "先选一张 png/jpg/webp 图片", "warn")
        return
    try:
        data = _b64.b64encode(Path(path).read_bytes()).decode("ascii")
    except Exception as e: # noqa: BLE001
        if note is not None and t is not None:
            _c8_say(note, t, "读取文件失败：%s" % e, "err")
        return

    def _run() -> str:
        post_json("/api/cursor/upload", {"image": "data:%s;base64,%s" % (mime, data)},
                  timeout=30.0)
        config_io.write_patch({"ui": {"whale_cursor": True, "cursor_image": "custom"}})
        return "自定义光标已保存并生效"

    _post_action_raw(btn, note, _run, "保存中…")


# ── generic 面板剩余动作 ─────────────────────────────────────
# model keySave/keyReset、wechat 数据目录/官网、tools 导出导入重扫、
# media 可搜目录、wavefx 应用、应用内说明弹窗（GUIDES）。全部对齐 web onclick。


def _js_unescape(s: str) -> str:
    """JS 单引号字符串字面量 → 原文（\\n 换行、\\' 引号、\\\\ 反斜杠等）。"""
    import re as _re # noqa: PLC0415

    def _sub(m) -> str:
        c = m.group(1)
        return {"n": "\n", "t": "\t", "r": "\r"}.get(c, c)

    return _re.sub(r"\\(.)", _sub, s, flags=_re.S)


_GUIDES_CACHE: dict | None = None


def _js_array_span(blk: str, name: str) -> str:
    """取 `name: [ ... ]` 括号内原文 —— 引号感知的配对扫描
    （模板串里含 `"],` 这类序列，正则非贪婪会提前截断）。"""
    i = blk.find(name + ": [")
    if i < 0:
        return ""
    j = i + len(name) + 3 # 跳过 "name: ["（开括号已消费，depth 从 1 起算）
    depth = 1
    ins = False
    k = j
    while k < len(blk):
        ch = blk[k]
        if ins:
            if ch == "\\":
                k += 2
                continue
            if ch == "'":
                ins = False
        elif ch == "'":
            ins = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return blk[j:k]
        k += 1
    return ""


def _web_guides() -> dict:
    """解析 console_html.py 的 GUIDES 对象（web 应用内引导真值）。

    文本以 web 为唯一源、运行时解析，不往 Qt 复制一份——web 改了引导词
    这里自动跟。解析失败降级为空 dict（按钮如实提示「没有这条引导」）。
    """
    global _GUIDES_CACHE
    if _GUIDES_CACHE is not None:
        return _GUIDES_CACHE
    import re as _re # noqa: PLC0415

    out: dict = {}
    try:
        txt = Path(sec_meta.WEB_PATH).read_text(encoding="utf-8")
        i = txt.find("const GUIDES = ")
        j = txt.find("\n};", i)
        seg = txt[i:j] if 0 <= i < j else ""
        marks = [(m.group(1), m.start()) for m in _re.finditer(r"\n  (\w+): \{", seg)]
        for n, (k, s) in enumerate(marks):
            blk = seg[s:marks[n + 1][1] if n + 1 < len(marks) else len(seg)]
            g: dict = {"title": "", "intro": "", "steps": [], "copy": [], "actions": []}
            m = _re.search(r"title: '((?:[^'\\]|\\.)*)'", blk)
            if m:
                g["title"] = _js_unescape(m.group(1))
            m = _re.search(r"intro: '((?:[^'\\]|\\.)*)'", blk)
            if m:
                g["intro"] = _js_unescape(m.group(1))
            sspan = _js_array_span(blk, "steps")
            if sspan:
                g["steps"] = [_js_unescape(x)
                              for x in _re.findall(r"'((?:[^'\\]|\\.)*)'", sspan)]
            cspan = _js_array_span(blk, "copy")
            if cspan:
                labels = _re.findall(r"label: '((?:[^'\\]|\\.)*)'", cspan)
                texts = _re.findall(r"text: '((?:[^'\\]|\\.)*)'", cspan)
                g["copy"] = [(_js_unescape(a), _js_unescape(b)) for a, b in zip(labels, texts)]
            # actions（web :5155 起，全量 5 种 kind：gen/open/test/goto/openUrl）
            aspan = _js_array_span(blk, "actions")
            if aspan:
                acts: list = []
                for am in _re.finditer(r"\{([^}]*)\}", aspan):
                    seg2 = am.group(1)
                    ml = _re.search(r"label: '((?:[^'\\]|\\.)*)'", seg2)
                    mk = _re.search(r"kind: '(\w+)'", seg2)
                    ma = _re.search(r"arg: '((?:[^'\\]|\\.)*)'", seg2)
                    acts.append({"label": _js_unescape(ml.group(1)) if ml else "执行",
                                 "kind": mk.group(1) if mk else "",
                                 "arg": _js_unescape(ma.group(1)) if ma else ""})
                g["actions"] = acts
            out[k] = g
    except Exception: # noqa: BLE001
        out = {}
    _GUIDES_CACHE = out
    return out


_GUIDE_KEY_OF = {
    "utGuide": "tools", "utBarGuide": "tools", "ttsGuide": "tts", "igGuide": "imggen",
    "vgGuide": "video", "vsGuide": "voice", "irGuide": "image", "fsGuide": "file",
}


def _card_dialog(t: Tokens, btn, title: str, width: int = 640):
    """confirm.ConfirmDialog 同款壳（无边框+半透明+卡片+模态）的通用构造。

    供说明/导入/导出等交互弹窗复用；返回 (dlg, 内容布局)。调用方负责 exec()。

    **可拖动**：「所有弹窗都要可挪动，按住任意位置（除文字显示区域）
    都能挪」。这里统一装上 —— 全项目的说明/表单类弹窗都从这个工厂出货，
    一处装好即全覆盖（比逐个弹窗各写一份拖拽实现可靠得多）。
    """
    from PySide6.QtWidgets import QDialog # noqa: PLC0415

    from widgets import drag_dialog_cls # noqa: PLC0415

    dlg = drag_dialog_cls("CardDialog")(btn.window())
    dlg.setWindowTitle(title)
    dlg.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.setModal(True)
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(0, 0, 0, 0)
    card = QFrame()
    card.setObjectName("C8CardDlg")
    # ⛔ 玻璃主题下 t.card 是（近）全透明 —— 弹窗浮在正文上会和下面的字叠成一团
    #   。弹窗必须有实色底：
    #   玻璃主题给深海底实色 #0E2136（confirm.py 同款口径），普通主题照旧 t.card。
    _card_bg = "#0E2136" if getattr(t, "glass", False) else t.card
    card.setStyleSheet(
        f"#C8CardDlg{{background:{_card_bg};border:1px solid {t.bd};"
        f"border-radius:{t.radius_card + 2}px;}}")
    outer.addWidget(card)
    v = QVBoxLayout(card)
    v.setContentsMargins(24, 22, 24, 18)
    v.setSpacing(12)
    head = QLabel(title)
    head.setFont(qfont(t, 16, 600))
    head.setWordWrap(True)
    head.setStyleSheet(f"color:{t.tx};background:transparent;")
    v.addWidget(head)
    dlg.resize(width, 200)
    dlg.enable_drag() # 可拖动（见本函数 docstring）
    return dlg, v


def _dlg_note(t: Tokens) -> QLabel:
    """弹窗内的次要说明/结果行（灰字，可换行）。"""
    lb = QLabel("")
    lb.setFont(qfont(t, t.body_size - 1))
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
    return lb


def _guide_open(key: str, btn, note) -> None:
    """web openGuide 对齐：说明弹窗（标题/引子/步骤/可复制模板/知道了）。"""
    t = getattr(btn, "t", None)
    if t is None:
        return
    g = _web_guides().get(key) or {}
    if not g.get("title"):
        if note is not None:
            _c8_say(note, t, "没有这条引导", "warn")
        return
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    dlg, v = _card_dialog(t, btn, g["title"])
    if g["intro"]:
        il = QLabel(g["intro"].replace("**", ""))
        il.setFont(qfont(t, t.body_size))
        il.setWordWrap(True)
        il.setStyleSheet(f"color:{t.tx2};background:transparent;")
        v.addWidget(il)
    for s in g["steps"]:
        sl = QLabel("· " + s.replace("**", ""))
        sl.setFont(qfont(t, t.body_size))
        sl.setWordWrap(True)
        sl.setStyleSheet(f"color:{t.tx2};background:transparent;")
        v.addWidget(sl)
    for label, text in g["copy"]:
        row = QHBoxLayout()
        row.setSpacing(8)
        code = QLabel(text)
        code.setFont(qfont(t, t.body_size - 1))
        code.setWordWrap(True)
        code.setStyleSheet(
            f"color:{t.tx};background:rgba(127,127,127,40);"
            "border-radius:8px;padding:8px 10px;")
        row.addWidget(code, 1)
        cp = Btn("复制", t, role="ghost")

        def _copy(checked=False, _tx=text, _b=cp) -> None:
            QApplication.clipboard().setText(_tx)
            _b.setText("已复制")
            QTimer.singleShot(1200, lambda: _b.setText("复制"))

        cp.clicked.connect(_copy)
        row.addWidget(cp)
        v.addLayout(row)
    for a in (g.get("actions") or []):
        ab = Btn(str(a.get("label") or "执行"), t, role="ghost")
        ab.setObjectName("guideAct")
        # ⛔ 透传**来源按钮**（页面上的引导钮）而不是弹窗内按钮：goto 要靠
        #    btn.window() 找主壳（Shell）；弹窗内按钮的 window() 是 QDialog，
        #    拿不到 _go ⇒ 会永远走「请到左侧点…」降级分支（t_g14 抓出）。
        ab.clicked.connect(lambda _=False, _a=a: _guide_action(t, btn, _a, note))
        v.addWidget(ab)
    rowh = QHBoxLayout()
    rowh.addStretch(1)
    okb = Btn("知道了", t, role="primary")
    okb.clicked.connect(dlg.accept)
    dlg.btn_ok = okb # 测试钩子：与 ConfirmDialog 同名
    rowh.addWidget(okb)
    v.addLayout(rowh)
    dlg.exec()


def _guide_action(t: Tokens, btn, a: dict, note) -> None: # noqa: ANN001
    """引导弹窗里的动作按钮（web guideAction :5389-5410 同款，全量 5 种 kind）：
    open=打开路径（/api/open-path）｜gen=生成模板（/api/tools/new_manifest）｜
    test=跑一次对应测试（voice/tts/wechat 重检）｜goto=跳到对应面板（分页栈）｜
    openUrl=打开微信官网。结果回显到来源面板的状态行。"""
    kind = str(a.get("kind") or "")
    arg = str(a.get("arg") or "")
    page = _c8_page_of(btn)

    def _say(msg: str, tone: str = "") -> None:
        if note is not None:
            _c8_say(note, t, msg, tone)

    if kind == "open":
        _say("打开「%s」中…" % arg)

        def _od(r, e): # noqa: ANN001
            bad = bool(e) or (isinstance(r, dict) and r.get("ok") is False)
            _say("打开失败：%s" % (e or (r or {}).get("error") or "后台没连上") if bad
                 else "已打开「%s」" % arg, "err" if bad else "")

        _async_post(None, "/api/open-path", {"path": arg}, _od)
    elif kind == "gen":
        _say("正在生成模板到 tools.d/…")

        def _gd(r, e): # noqa: ANN001
            if e or (isinstance(r, dict) and r.get("ok") is False):
                _say("生成失败：%s" % (e or (r or {}).get("error") or "后台没连上"), "err")
                return
            _say("已生成模板：%s（改完点「重新加载清单」）"
                 % ((r or {}).get("path") or "tools.d/"))
            fn = getattr(page, "_ut_reload", None)
            if callable(fn):
                fn()

        _async_post(None, "/api/tools/new_manifest", {}, _gd)
    elif kind == "test":
        api = {"voice": "/api/voice/test", "tts": "/api/tts/test"}.get(
            arg, "/api/wechat/recheck")
        _say("正在跑一次测试…")

        def _td(r, e): # noqa: ANN001
            if e or (isinstance(r, dict) and r.get("ok") is False):
                _say("测试失败：%s" % (e or (r or {}).get("error") or "后台没连上"), "err")
                return
            inst = (r or {}).get("install") if isinstance(r, dict) else None
            if isinstance(inst, dict):
                st = inst.get("state")
                _say("检测到微信在运行" if st == "running"
                     else ("微信已安装，登录后即可用" if st == "installed_not_running"
                           else "仍未检测到微信"))
            else:
                _say(str((r or {}).get("note") or (r or {}).get("reply") or "测试完成"))

        _async_get(api, _td)
    elif kind == "goto":
        sec = arg.lstrip("#")
        if sec.startswith("sec-"):
            sec = sec[4:]
        win = btn.window() if hasattr(btn, "window") else None
        go = getattr(win, "_go", None) if win is not None else None
        if callable(go):
            go(sec, "")
            _say("已跳到对应面板")
        else:
            _say("请到左侧点「%s」面板" % sec)
    elif kind == "openUrl":
        from PySide6.QtCore import QUrl # noqa: PLC0415
        from PySide6.QtGui import QDesktopServices # noqa: PLC0415

        QDesktopServices.openUrl(QUrl("https://weixin.qq.com/"))
        _say("已尝试打开微信官网（打不开就手动复制 https://weixin.qq.com/ 到浏览器）")


def _act_guide(key: str):
    """说明按钮 handler 工厂（aid → GUIDES key 绑定）。"""
    def _h(btn, note) -> None:
        _guide_open(key, btn, note)
    return _h


def _act_key_save(btn, note) -> None:
    """web keySave 对齐：只保存 Key 并与当前厂商关联（GET config → 改 → POST 全量），
    成功后立即 POST /api/test-api 让用户当场看到能不能跑。空值/打码值不保存。"""
    from agent_bridge import post_json # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    page = _c8_page_of(btn)
    key_w = _c8_find_row(page, lambda r: r.cfg == "api.api_key")
    prov_w = _c8_find_row(page, lambda r: r.kind == "select" and "厂商" in r.label)
    if prov_w is None:
        # ⛔ 解析版「模型厂商」行已被 _row_redundant 跳过（纯 JS 控件，真控件在
        #    追加区）⇒ 回退到追加区的 providerSel（objectName 同名）。
        from PySide6.QtWidgets import QComboBox as _QCombo # noqa: PLC0415

        prov_w = page.findChild(_QCombo, "providerSel")
    k = (key_w.text() if key_w is not None else "").strip()
    prov = str(prov_w.currentData() or "") if prov_w is not None else ""
    if not k or "••••" in k or k.startswith("sk-***"):
        _c8_say(note, t, "Key 为空或仍是打码值，未保存", "warn")
        return

    def _run() -> str:
        cfg = config_io.get_json("/api/config", timeout=10.0)
        if not isinstance(cfg, dict):
            raise Exception("读不到当前配置")
        api = cfg.setdefault("api", {})
        if not isinstance(api, dict):
            raise Exception("配置里 api 段不是对象")
        api["api_key"] = k
        if prov and prov != "custom":
            pk = api.setdefault("provider_keys", {})
            if not isinstance(pk, dict):
                pk = api["provider_keys"] = {}
            pk[prov] = k
        post_json("/api/config", cfg, timeout=20.0)
        msg = "密钥 已保存（%s）" % (prov or "custom")
        try:
            tr = post_json("/api/test-api", {}, timeout=60.0) or {}
            msg += ("，测试连通成功（%sms）" % tr.get("latency_ms")) if tr.get("ok") \
                else ("，但测试失败：%s" % (tr.get("error") or "未知原因"))
        except Exception as e: # noqa: BLE001
            msg += "，但测试失败：%s" % e
        return msg

    _post_action_raw(btn, note, _run, "保存中…")


def _act_key_reset(btn, note) -> None:
    """web keyReset 对齐：清空 Key 输入框（纯本地，不发请求）。"""
    page = _c8_page_of(btn)
    key_w = _c8_find_row(page, lambda r: r.cfg == "api.api_key")
    if key_w is not None:
        key_w.setText("")
        key_w.setFocus()


def _act_wx_dir_probe(btn, note) -> None:
    """web wxDirProbe 对齐：GET /api/wechat/dir（带当前输入作提示路径）→
    回显「当前读 + 候选清单」（web renderWechatDir 的文字版，含可用/库文件数）。"""
    import urllib.parse as _up # noqa: PLC0415

    page = _c8_page_of(btn)
    dir_w = _c8_find_row(page, lambda r: r.cfg == "wechat.db_dir")
    p = (dir_w.text() if dir_w is not None else "").strip()

    def _run() -> str:
        r = config_io.get_json("/api/wechat/dir" + ("?path=" + _up.quote(p) if p else ""),
                               timeout=30.0) or {}
        lines = ["已探完，下面列出候选目录"]
        if r.get("now"):
            lines.append("当前在读：" + str(r["now"]))
        cs = r.get("candidates")
        if isinstance(cs, list):
            for c in cs[:8]:
                if not isinstance(c, dict):
                    continue
                usable = c.get("usable")
                if usable is None:
                    usable = c.get("ok")
                lines.append("✔ %s，可用，%s 个库文件" % (c.get("path") or "", c.get("dbs") or 0)
                             if usable else "✘ %s，%s" % (c.get("path") or "", c.get("why") or "不可用"))
            if len(cs) > 8:
                lines.append("…共 %d 条候选" % len(cs))
        return "\n".join(lines)

    _post_action_raw(btn, note, _run, "探测中…（要翻微信目录，可能要几秒）")


def _act_wx_dir_save(btn, note) -> None:
    """web wxDirSave 对齐：POST /api/wechat/dir {path}（空=自动检测），失败说回落。"""
    from agent_bridge import post_json # noqa: PLC0415

    page = _c8_page_of(btn)
    dir_w = _c8_find_row(page, lambda r: r.cfg == "wechat.db_dir")
    p = (dir_w.text() if dir_w is not None else "").strip()

    def _run() -> str:
        r = post_json("/api/wechat/dir", {"path": p}, timeout=30.0) or {}
        if r.get("ok") is False:
            raise Exception("%s%s" % (r.get("error") or "这个目录用不了",
                                      ("，当前会回落到 %s" % r["fallback"]) if r.get("fallback") else ""))
        wd = r.get("wechat_dir") if isinstance(r.get("wechat_dir"), dict) else r
        msg = "已保存，现在读的是：%s" % (wd.get("now") or "自动检测到的目录")
        if wd.get("hint"):
            msg += "。" + str(wd["hint"])
        return msg

    _post_action_raw(btn, note, _run, "保存中…")


def _act_wx_open_site(btn, note) -> None:
    """web wxOpenSite 对齐：系统浏览器打开微信官网（web 的 official_url 来自
    服务端注入，Qt 用官方固定址，文案同款提醒可手动复制）。"""
    from PySide6.QtCore import QUrl # noqa: PLC0415
    from PySide6.QtGui import QDesktopServices # noqa: PLC0415

    t = getattr(btn, "t", None)
    u = "https://weixin.qq.com/"
    try:
        QDesktopServices.openUrl(QUrl(u))
    except Exception: # noqa: BLE001
        pass
    if note is not None and t is not None:
        _c8_say(note, t, "已尝试打开官网：%s（打不开就手动复制到浏览器）" % u, "ok")


def _act_ut_reload(btn, note) -> None:
    """web utReload 对齐：GET /api/tools/reload 重扫清单 → 回显数量，
    并触发下方清单卡重载（page._ut_reload 钩子）。"""
    page = _c8_page_of(btn)

    def _run() -> str:
        r = config_io.get_json("/api/tools/reload", timeout=30.0) or {}
        tt = r.get("tools") if isinstance(r.get("tools"), dict) else {}
        n = len(tt.get("tools") or [])
        bad = len(tt.get("problems") or [])
        return "清单已重扫：%d 个工具%s" % (n, ("，%d 条问题（看面板）" % bad) if bad else "")

    def _done() -> None:
        hook = getattr(page, "_ut_reload", None)
        if callable(hook):
            try:
                hook()
            except Exception: # noqa: BLE001
                pass

    _post_action_raw(btn, note, _run, "重扫中…", done=_done)


def _act_ut_export(btn, note) -> None:
    """web utExportDlg 对齐：GET /api/tools/export → 文档弹窗
    （全文可复制 + 另存为 .json + 关闭）。"""
    from PySide6.QtWidgets import QApplication, QFileDialog # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    btn.setEnabled(False)
    _c8_say(note, t, "正在生成导出文档…")
    box: dict = {"done": False, "r": None, "err": None}

    def _work() -> None:
        try:
            box["r"] = config_io.get_json("/api/tools/export", timeout=30.0)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    import threading as _th # noqa: PLC0415

    _th.Thread(target=_work, daemon=True, name="ut-export").start()

    def _apply() -> None:
        if not box["done"]:
            QTimer.singleShot(150, _apply)
            return
        btn.setEnabled(True)
        r = box["r"]
        if box["err"] or not isinstance(r, dict) or not r.get("ok"):
            _c8_say(note, t, "导出失败：%s" % (box["err"] or (r or {}).get("why")
                                              or (r or {}).get("error") or "未知原因"), "err")
            return
        _c8_say(note, t, "")
        dlg, v = _card_dialog(t, btn, "导出全部工具")
        intro = QLabel("这是一份可以直接发给别人的文档：对方在自己的控制台点「导入工具」"
                       "贴上/选中它，就等于装上了。文档里只有清单本身（地址、白名单、说明、"
                       "用法、示例），不含你的任何本机信息。")
        intro.setFont(qfont(t, t.body_size))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{t.tx2};background:transparent;")
        v.addWidget(intro)
        ta = QPlainTextEdit(str(r.get("text") or ""))
        ta.setReadOnly(True)
        ta.setFont(qfont(t, t.body_size - 1))
        ta.setMinimumHeight(180)
        v.addWidget(ta)
        res = _dlg_note(t)
        v.addWidget(res)
        rowh = QHBoxLayout()
        bcopy = Btn("复制文档", t, role="ghost")

        def _copy(checked=False) -> None:
            QApplication.clipboard().setText(ta.toPlainText())
            res.setText("已复制到剪贴板")

        bcopy.clicked.connect(_copy)
        rowh.addWidget(bcopy)
        bsave = Btn("另存为 .json", t, role="ghost")

        def _save(checked=False) -> None:
            f, _ = QFileDialog.getSaveFileName(dlg, "另存为", "my-tools.json", "JSON (*.json)")
            if not f:
                return
            try:
                Path(f).write_text(ta.toPlainText(), encoding="utf-8")
                res.setText("已保存到 " + f)
            except Exception as e: # noqa: BLE001
                res.setText("保存失败：%s" % e)

        bsave.clicked.connect(_save)
        rowh.addWidget(bsave)
        rowh.addStretch(1)
        bclose = Btn("关闭", t, role="primary")
        bclose.clicked.connect(dlg.accept)
        dlg.btn_ok = bclose
        rowh.addWidget(bclose)
        v.addLayout(rowh)
        dlg.exec()

    QTimer.singleShot(150, _apply)


def _act_ut_import(btn, note) -> None:
    """web utImportDlg 对齐：选 .json 或贴文 → POST /api/tools/import
    {text, overwrite} → 新增/替换/跳过逐条回显（导入只写清单，不执行代码）。"""
    from PySide6.QtWidgets import QFileDialog # noqa: PLC0415

    t = getattr(btn, "t", None)
    if t is None:
        return
    from agent_bridge import post_json # noqa: PLC0415

    dlg, v = _card_dialog(t, btn, "导入工具 ＝ 把一份文档变成插件")
    intro = QLabel("选一个别人给你的 .json，或把内容贴进下面。导入只做一件事：把清单校验后"
                   "写进 tools.d/ ——不执行任何代码、不下载任何东西；域名白名单必填、"
                   "内网/本机地址照旧一律拒。同名工具默认不动它（要替换就勾「覆盖同名」）。")
    intro.setFont(qfont(t, t.body_size))
    intro.setWordWrap(True)
    intro.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(intro)
    opt = QCheckBox("覆盖同名工具")
    opt.setFont(qfont(t, t.body_size))
    opt.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(opt)
    pick = Btn("选 .json 文件…", t, role="ghost")
    v.addWidget(pick)
    ta = QPlainTextEdit()
    ta.setPlaceholderText("也可以把文档内容粘在这里…")
    ta.setFont(qfont(t, t.body_size - 1))
    ta.setMinimumHeight(120)
    v.addWidget(ta)
    res = _dlg_note(t)
    v.addWidget(res)

    def _pick(checked=False) -> None:
        f, _ = QFileDialog.getOpenFileName(dlg, "选择工具文档", "", "JSON (*.json)")
        if not f:
            return
        try:
            txt = Path(f).read_text(encoding="utf-8")
        except Exception as e: # noqa: BLE001
            res.setText("读文件失败：%s" % e)
            return
        ta.setPlainText(txt)
        res.setText("已读入 %s（%d 字符），按「导入」写入。" % (Path(f).name, len(txt)))

    pick.clicked.connect(_pick)

    def _lines(r: dict) -> str:
        out: list = []
        if r.get("error"):
            out.append("没成功：" + str(r["error"]))
        if r.get("added"):
            out.append("新增：" + "、".join(str(x) for x in r["added"]))
        if r.get("replaced"):
            out.append("替换：" + "、".join(str(x) for x in r["replaced"]))
        for s in r.get("skipped") or []:
            if isinstance(s, dict):
                out.append("跳过 %s：%s%s" % (s.get("name") or "", s.get("why") or "",
                                             ("　→ %s" % s["fix"]) if s.get("fix") else ""))
        if r.get("note"):
            out.append(str(r["note"]))
        return "\n".join(out) or "没有可导入的内容"

    go = Btn("导入", t, role="primary")
    rowh = QHBoxLayout()
    rowh.addWidget(go)
    rowh.addStretch(1)
    bclose = Btn("关闭", t, role="ghost")
    bclose.clicked.connect(dlg.accept)
    rowh.addWidget(bclose)
    v.addLayout(rowh)

    def _go(checked=False) -> None:
        text = ta.toPlainText()
        if not text.strip():
            res.setText("先选文件、或把内容贴进来")
            return
        go.setEnabled(False)
        go.setText("导入中…")
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            try:
                box["r"] = post_json("/api/tools/import",
                                     {"text": text, "overwrite": bool(opt.isChecked())},
                                     timeout=60.0)
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="ut-import").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(150, _apply)
                return
            go.setEnabled(True)
            go.setText("导入")
            if box["err"]:
                res.setText("导入失败：" + box["err"])
                return
            r = box["r"] if isinstance(box["r"], dict) else {}
            res.setText(_lines(r))
            page = _c8_page_of(btn)
            hook = getattr(page, "_ut_reload", None)
            if callable(hook):
                try:
                    QTimer.singleShot(0, hook)
                except Exception: # noqa: BLE001
                    pass

        QTimer.singleShot(150, _apply)

    go.clicked.connect(_go)
    dlg.btn_ok = go # 测试钩子
    dlg.exec()


def _act_fs_add(btn, note) -> None:
    """web fsAdd 对齐：读「可搜目录」行 → POST /api/file_search/add {dir} → 清空输入。"""
    from agent_bridge import post_json # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    page = _c8_page_of(btn)
    dir_w = _c8_find_row(page, lambda r: r.kind == "text" and r.label == "可搜目录")
    d = (dir_w.text() if dir_w is not None else "").strip()
    if not d:
        _c8_say(note, t, "先填一个目录，比如 D:\\下载", "warn")
        return

    def _run() -> str:
        r = post_json("/api/file_search/add", {"dir": d}, timeout=15.0) or {}
        return str(r.get("note") or "已加入目录")

    def _done() -> None:
        if dir_w is not None:
            dir_w.clear()

    _post_action_raw(btn, note, _run, "加入中…", done=_done)


def _act_wavefx_apply(btn, note) -> None:
    """web wavefxApply 对齐：收集本页 ui.wave_fx.* 行 → 落盘 → 即时生效。"""
    from panels_qt import _SKIP, _ctrl_value # noqa: PLC0415

    t = getattr(btn, "t", None)
    if note is None or t is None:
        return
    page = _c8_page_of(btn)
    patch: dict = {}
    for r, w in (getattr(page, "_c8_binds", None) or []):
        if (r.cfg or "").startswith("ui.wave_fx."):
            val = _ctrl_value(r, w)
            if val is not _SKIP:
                patch[r.cfg] = val
    if not patch:
        _c8_say(note, t, "没有可应用的参数", "warn")
        return

    def _run() -> str:
        ok, msg = config_io.write_patch(patch)
        if not ok:
            raise Exception(msg)
        return "水光波纹已应用"

    _post_action_raw(btn, note, _run, "应用中…")


def _act_ut_problems(btn, note) -> None:
    """web utBarProblems 对齐（scrollIntoView utProblems）：滚动到问题清单。"""
    from PySide6.QtWidgets import QScrollArea # noqa: PLC0415

    t = getattr(btn, "t", None)
    page = _c8_page_of(btn)
    prob = page.findChild(QLabel, "utProblemsQt") if page is not None else None
    if note is None or t is None:
        return
    if prob is None:
        _c8_say(note, t, "问题清单还没生成（等下方卡片加载）", "warn")
        return
    p = prob.parentWidget()
    while p is not None and not isinstance(p, QScrollArea):
        p = p.parentWidget()
    if isinstance(p, QScrollArea):
        p.ensureWidgetVisible(prob, 0, 60)
    _c8_say(note, t, "问题清单在下方卡片里（已滚动到可见）", "ok")


ACT_CUSTOM = {
    "fbSubmit": (_fb_submit,
                 "真接后端：/api/feedback/submit（校验内容/邮箱；被限流时不清空输入框）"),
    "uploadSeeds": (_upload_seeds,
                    "真接后端：/api/community/upload kind=holyshits（先勾「社区上传」并填金句上传 URL）"),
    "uploadFeedback": (_upload_feedback,
                       "真接后端：/api/community/upload kind=feedback（先勾「社区上传」并填意见反馈上传 URL）"),
    "seedImportBtn": (_seed_import,
                      "真接后端：/api/scoring/import（读上方文本框，服务端查重合并后生效）"),
    "seedImportFile": (_seed_import_file,
                       "真接后端：/api/scoring/import（选 txt/json 文件导入，自动查重）"),
    "uiLayoutReload": (_act_ui_layout_reload,
                       "真接后端：/api/ui-layout（重读侧栏图标标定状态）"),
    "uiRecalibrate": (_act_ui_recalibrate,
                      "真接后端：/api/ui/recalibrate（后台接管微信窗口标定图标位置，约几十秒）"),
    "seedReload": (_act_seed_reload,
                   "真接后端：/api/scoring/stats（刷新种子库统计）"),
    "learnApply": (_act_learn_apply,
                   "真接后端：/api/learning/start（开启学习机制，有群友回应时学习）"),
    "learnEval": (_act_learn_eval,
                  "真接后端：/api/learning/evaluate（模型按评分细则评估学习效果，可能要一会儿）"),
    "bgUpload": (_act_bg_upload,
                 "真接后端：/api/ui/background（选图上传为控制台背景）"),
    "bgClear": (_act_bg_clear,
                "真接后端：/api/ui/background clear（恢复默认背景）"),
    "cursorReset": (_act_cursor_reset,
                    "真接后端：/api/cursor/reset（清自定义残留文件并写回默认配置）"),
    "cursorSaveBtn": (_act_cursor_save,
                      "真接后端：/api/cursor/upload（选图后保存并生效）"),
    # ── generic 面板剩余动作 ──
    "keySave": (_act_key_save,
                "真接后端：保存 Key 并与当前厂商关联，保存后立即测试连通（空值/打码值不保存）"),
    "keyReset": (_act_key_reset,
                 "清空 Key 输入框（不发请求，填好新 Key 再点「保存 Key」）"),
    "wxDirProbe": (_act_wx_dir_probe,
                   "真接后端：/api/wechat/dir 自动探测微信数据目录，列出候选与库文件数"),
    "wxDirSave": (_act_wx_dir_save,
                  "真接后端：/api/wechat/dir 保存当前填的目录并重探（失败会说明回落目录）"),
    "wxOpenSite": (_act_wx_open_site,
                   "打开微信官网 weixin.qq.com（系统浏览器）"),
    "utExport": (_act_ut_export,
                 "真接后端：/api/tools/export 生成可分享的工具清单文档（可复制/另存 .json）"),
    "utImport": (_act_ut_import,
                 "真接后端：/api/tools/import 把工具文档校验后写进 tools.d/（不执行任何代码）"),
    "utReload": (_act_ut_reload,
                 "真接后端：/api/tools/reload 重扫清单并刷新下方卡片"),
    "utBarProblems": (_act_ut_problems,
                      "滚动到下方问题清单（哪些工具没装上、为什么）"),
    "fsAdd": (_act_fs_add,
              "真接后端：/api/file_search/add 把填的目录加入可搜目录"),
    "wavefxApply": (_act_wavefx_apply,
                    "保存本页水光波纹参数并即时生效"),
    "irGuide": (_act_guide(_GUIDE_KEY_OF["irGuide"]),
                "打开应用内引导：怎么放图"),
    "vsGuide": (_act_guide(_GUIDE_KEY_OF["vsGuide"]),
                "打开应用内引导：语音链路怎么用"),
    "fsGuide": (_act_guide(_GUIDE_KEY_OF["fsGuide"]),
                "打开应用内引导：怎么让机器人发文件"),
    "ttsGuide": (_act_guide(_GUIDE_KEY_OF["ttsGuide"]),
                "打开应用内引导：为什么发出去是文件、不是语音条"),
    "igGuide": (_act_guide(_GUIDE_KEY_OF["igGuide"]),
                "打开应用内引导：怎么接一个生图后端"),
    "vgGuide": (_act_guide(_GUIDE_KEY_OF["vgGuide"]),
                "打开应用内引导：怎么接视频后端"),
    "utGuide": (_act_guide(_GUIDE_KEY_OF["utGuide"]),
                "打开应用内引导：怎么加工具 / 自己写一个"),
    "utBarGuide": (_act_guide(_GUIDE_KEY_OF["utBarGuide"]),
                   "打开应用内引导：怎么加工具"),
}


def _community_appendix(t: Tokens, page: QWidget) -> None:
    """社区上传两钮的可用性联动（web syncState 对齐）：勾「社区上传」+
    对应 URL 非空才可用；开关/两个 URL 任一变化即时重算。
    上传/导出/导入动作本体走 ACT_CUSTOM 与 _ACT_API，这里只补 disabled 联动。
    """
    chk = _c8_find_row(page, lambda r: (r.cfg or "") == "community.upload_enabled")
    url_h = _c8_find_row(page, lambda r: (r.cfg or "") == "community.holyshits_upload_url")
    url_f = _c8_find_row(page, lambda r: (r.cfg or "") == "community.feedback_upload_url")
    ub = fb = None
    for b in page.findChildren(QPushButton):
        aid = b.property("web_action")
        if aid == "uploadSeeds":
            ub = b
        elif aid == "uploadFeedback":
            fb = b
    if ub is None or fb is None or chk is None:
        return

    def _sync() -> None:
        on = chk.isChecked()
        ub.setEnabled(on and url_h is not None and bool(url_h.text().strip()))
        fb.setEnabled(on and url_f is not None and bool(url_f.text().strip()))

    chk.toggled.connect(lambda _=False: _sync())
    if url_h is not None:
        url_h.textChanged.connect(lambda _=False: _sync())
    if url_f is not None:
        url_f.textChanged.connect(lambda _=False: _sync())
    _sync()

    # ── 上云（预留）· 测试连通（web sec-community :1640-1657 的 data-cloud-test 两钮）──
    #    只探测、不上传；探测不带口令（web :1652 口径）。结果区文案同 web :4604/:4607。
    ocard = Card(t)
    ocard.body.addWidget(h2(t, "上云（预留）· 测试连通"))
    ocard.body.addWidget(desc(t, "总开关关着时一个字节都不会上传；这里只探测接收端通不通（不带口令）。"))
    ct_row = FlowBox(t, spacing=6)
    b_ct1 = Btn("测试人设接收端", t, "ghost")
    b_ct1.setObjectName("cloudTestPersona")
    b_ct2 = Btn("测试名单接收端", t, "ghost")
    b_ct2.setObjectName("cloudTestBlocklist")
    ct_row.add(b_ct1)
    ct_row.add(b_ct2)
    ocard.body.addWidget(ct_row)
    ct_note = desc(t, "")
    ct_note.setWordWrap(True)
    ocard.body.addWidget(ct_note)
    page.layout().addWidget(ocard)

    def _cloud_test(which: str) -> None:
        cfg_key = "cloud.persona_url" if which == "persona" else "cloud.blocklist_url"
        url_w = _c8_find_row(page, lambda r, _k=cfg_key: r.cfg == _k)
        url = ""
        if url_w is not None and hasattr(url_w, "text"):
            url = str(url_w.text() or "").strip()
        name = "人设" if which == "persona" else "名单"
        ct_note.setText("%s：正在探测 %s …" % (name, url or "（未配置）"))

        def _done(r, e): # noqa: ANN001
            if e:
                ct_note.setText("%s：探测失败 ｜ %s" % (name, e))
                return
            r = r if isinstance(r, dict) else {}
            if r.get("ok"):
                ct_note.setText("%s：可达（%s · HTTP %s · %sms）%s" % (
                    name, r.get("stage") or "", r.get("status") or "?", r.get("ms") or 0,
                    (" ｜ " + str(r.get("why"))) if r.get("why") else ""))
            else:
                ct_note.setText("%s：不通 ｜ 卡在「%s」段 · %s" % (
                    name, r.get("stage") or "?", r.get("why") or "未知原因"))

        _async_post(None, "/api/cloud/test", {"which": which, "url": url}, _done)

    b_ct1.clicked.connect(lambda: _cloud_test("persona"))
    b_ct2.clicked.connect(lambda: _cloud_test("blocklist"))


def _feedback_appendix(t: Tokens, page: QWidget) -> None:
    """反馈状态卡（web fbLoad 对齐）：can_send / pending 警示 + 最近提交一览；
    「补发积压」走 _ACT_API 的 fbFlush（后台线程 + 卡内回显）。提交按钮的
    三态回显在按钮行内 note（ACT_CUSTOM._fb_submit），提交成功会联动刷新本卡。
    """
    import threading as _th # noqa: PLC0415

    card = Card(t)
    card.body.addWidget(h2(t, "反馈状态"))
    card.body.addWidget(desc(t, "反馈发不出去时只存在本机，网络/邮箱修好后点「补发积压」再试；"
                                "附件暂时要在网页控制台里添加（这里提交不带附件）。"))
    lb = QLabel("")
    lb.setFont(qfont(t, 12))
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
    fl_note = QLabel("") # 补发回执独立落点（web toast 的 Qt 等价：与状态卡分开，互不覆盖）
    fl_note.setFont(qfont(t, 12))
    fl_note.setWordWrap(True)
    fl_note.setStyleSheet(f"color:{t.tx3};background:transparent;")
    row = QHBoxLayout()
    b_rf = Btn("刷新", t, "ghost")
    b_fl = Btn("补发积压", t, "ghost")
    b_web = Btn("去网页加附件", t, "ghost") # Qt 壳不代持附件上传（web 为真值）——给入口不补 UI
    b_web.setObjectName("fbWebAdd")
    row.addWidget(b_rf)
    row.addWidget(b_fl)
    row.addWidget(b_web)
    row.addStretch(1)
    card.body.addLayout(row)
    card.body.addWidget(fl_note)
    card.body.addWidget(lb)
    page.layout().addWidget(card)

    def _open_web() -> None:
        # 附件上传只在网页控制台有（Qt 壳不代持）——给入口不补 UI。
        # fragment 不走 join_url 的 path 槽位（同 vermat 拍板入口的口径：
        # /#sec-feedback?token=tk 会把 token 困进 fragment ⇒ 401 / 锚跳转失效）。
        from PySide6.QtCore import QUrl as _QUrl # noqa: PLC0415
        from PySide6.QtGui import QDesktopServices as _QDS # noqa: PLC0415
        from agent_bridge import current_url as _cur # noqa: PLC0415

        _QDS.openUrl(_QUrl(str(_cur() or "").strip() + "#sec-feedback"))
        fl_note.setText("已在浏览器打开网页控制台的反馈页（附件在那里添加）· " + time.strftime("%H:%M:%S"))

    b_web.clicked.connect(_open_web)

    def _load() -> None:
        box: dict = {"done": False, "r": None, "err": None}

        def _work() -> None:
            try:
                box["r"] = config_io.get_json("/api/feedback", timeout=15.0)
            except Exception as e: # noqa: BLE001
                box["err"] = str(e)
            box["done"] = True

        _th.Thread(target=_work, daemon=True, name="fb-status").start()

        def _apply() -> None:
            # 延迟回调可能跨过页面生命周期（用户 300ms 内关窗/切走、测试环境页面对象
            # 被回收）⇒ 落地前先探活，C++ 对象已删就静默放弃，不往已删控件上写。
            try:
                lb.isVisible()
            except RuntimeError:
                return
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            if box["err"]:
                lb.setText("读不到反馈状态：%s" % box["err"])
                lb.setStyleSheet(f"color:{t.err};background:transparent;")
                return
            d = box["r"] if isinstance(box["r"], dict) else {}
            if d.get("ok") is False:
                lb.setText("读不到反馈状态：%s" % (d.get("error") or ""))
                lb.setStyleSheet(f"color:{t.err};background:transparent;")
                return
            if d.get("enabled") is False:
                lb.setText("反馈栏已关闭（config：feedback.enabled=false）。")
                lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
                return
            bad = (not d.get("can_send", True)) or (d.get("pending") or 0) > 0
            msg = ""
            if not d.get("can_send", True):
                msg += "这条只会存在本机，暂时发不出去。"
            if (d.get("pending") or 0) > 0:
                msg += (("；" if msg else "") + "有 %s 条还没发出去" % d.get("pending"))
            recent = d.get("recent") or []
            rec = ("最近提交：" + " ｜ ".join(
                "%s %s（%s）" % (x.get("at_h"), x.get("kind"),
                                 "已发" if x.get("sent_h") else "待发")
                for x in recent)) if recent else "还没有提交过反馈。"
            lb.setText((msg + ("\n" if msg else "") + rec))
            lb.setStyleSheet(f"color:{t.warn if bad else t.tx3};background:transparent;")

        QTimer.singleShot(300, _apply)

    def _flush() -> None:
        """补发积压（web fbFlush 对齐）。

        web 侧把回执走 toast、状态卡另行 fbLoad() 刷新——两边是**不同的落点**。
        Qt 若把回执写进状态卡，会被随后的 _load() 回填覆盖（谁后到谁赢），
        故回执写独立的 fl_note 行，状态卡只由 _load() 独占。
        """
        from panels_qt import _act_run # noqa: PLC0415

        _act_run("fbFlush", fl_note)

        def _after(left: int = 40) -> None:
            # 等 _act_run 的 150ms 轮询把回执落下来（上限约 8s，绝不无限重排——
            # 控件在测试拆窗时会被销毁，无界重试会把事件循环拖住）。
            try:
                pending = fl_note.text().startswith("执行中")
            except RuntimeError: # 控件已销毁（拆窗），放弃刷新
                return
            if pending and left > 0:
                QTimer.singleShot(200, lambda: _after(left - 1))
                return
            _load() # 回执已落，再刷新状态卡（web：toast 后 fbLoad()）

        QTimer.singleShot(200, _after)

    b_rf.clicked.connect(_load)
    b_fl.clicked.connect(_flush)
    page._fb_reload = _load # 提交成功后联动刷新（_fb_submit 读）
    page._fb_lb = lb # 状态行引用（自检断言用）
    page._fb_flnote = fl_note # 补发回执行引用（自检断言用）
    _load()


_PROVIDERS_CACHE: dict = {}


def _providers_from_web() -> dict:
    """从 web 源码运行时解析 PROVIDERS 表（label/base/keyHint/models）——
    sec_meta 同款「web 源码是唯一真值」思路：web 改厂商清单，Qt 联动跟着变。
    解析失败返回空 dict（联动静默跳过，不拖垮 model 页）。"""
    if _PROVIDERS_CACHE:
        return _PROVIDERS_CACHE
    out: dict = {}
    try:
        src = (ROOT / "agent" / "console_html.py").read_text(encoding="utf-8")
        i = src.find("const PROVIDERS = {")
        j = src.find("};", i) if i >= 0 else -1
        if i >= 0 and j > i:
            blk = src[i:j]
            # 逐厂商：吃下从 `key:{` 到下一个 `},` 或块尾的整段（含换行的 models 数组）
            for m in re.finditer(r"(\w+):\{(.*?)(?=\n\s*\w+:\{|$)", blk, re.S):
                key, body = m.group(1), m.group(2)
                lab = re.search(r"label:'([^']*)'", body)
                bas = re.search(r"base:'([^']*)'", body)
                kh = re.search(r"keyHint:'([^']*)'", body)
                mm = re.search(r"models:\[(.*?)\]", body, re.S)
                models = re.findall(r"'([^']*)'", mm.group(1)) if mm else []
                out[key] = {"label": lab.group(1) if lab else key,
                            "base": bas.group(1) if bas else "",
                            "keyHint": kh.group(1) if kh else "",
                            "models": models}
    except Exception: # noqa: BLE001
        out = {}
    _PROVIDERS_CACHE.update(out)
    return out


def _ws_options_from_web() -> list[tuple[str, str]]:
    """解析联网搜索引擎选择器的选项（value, 文案）——web sec-search 的静态
    `<option>` 清单（#wsProvider），选项序与文案随 web 源码走。解析失败返回空。"""
    try:
        src = (ROOT / "agent" / "console_html.py").read_text(encoding="utf-8")
        i = src.find('<select data-cfg="web_search.provider" id="wsProvider">')
        j = src.find("</select>", i) if i >= 0 else -1
        if i < 0 or j < 0:
            return []
        return [(m.group(1), m.group(2)) for m in
                re.finditer(r'<option value="([^"]+)">([^<]+)</option>', src[i:j])]
    except Exception: # noqa: BLE001
        return []


def _web_search_appendix(t: Tokens, page: QWidget) -> None:
    """联网搜索「当前引擎参数」卡（对齐 web sec-search :1595-1601 的 wsKey/wsUrl/
    wsModel/wsEngine/wsCount + :4362-4391 的三个专用函数）。

    为什么手写：这五个控件在 web 里**没有 data-cfg**（是 `web_search.<provider>`
    动态下钻的 JS 控件），元数据渲染取不到 —— 与模型页 providerSel 同类盲区。

    逐条对齐 web 机制：
    · 引擎下拉 = `web_search.provider`（等价 id="wsProvider"），选项照 web 源码解析；
    · **载入时** `wsSyncToForm()`：按当前 provider 取 `web_search.<prov>` 小节回填五控件；
    · **切引擎** `change → wsShowRows(prov)`：只做「模型/engine」两行的显隐（`.row[data-ws]`），
      不回填（web 真实行为；回填发生在后续 syncToForm）；
    · **保存** `wsSyncFromForm()`：把五控件写回 `web_search[prov]`，随「保存设置」一并落盘
      （count 走 parseInt 语义：空/非数 → 6）。
    读取与落盘走页面的统一保存链（page._ws_collect 由 build_panel 的保存动作并入），
    不另起一份写盘路径。
    """
    opts = _ws_options_from_web()
    if not opts:
        return
    from panels_qt import _line # noqa: PLC0415
    from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel # noqa: PLC0415

    card = Card(t)
    card.body.addWidget(h2(t, "当前引擎参数"))
    card.body.addWidget(desc(t, "按当前联网搜索引擎分别保存密钥与地址（换引擎时各自独立，"
                                "互不覆盖）；保存随本页「保存设置」一并写入 config.json。"))

    def _mk_line():
        e = _line(t)
        e.setFixedHeight(32)
        return e

    # 引擎下拉（provider 本身也是 web_search.provider，元数据页已渲染同一键的行；
    # 这里再放一个是 web 的真实双层结构——上面选择器、下面参数区，保持同一份取值）
    prow = QHBoxLayout()
    lbl = QLabel("引擎")
    lbl.setFont(qfont(t, t.body_size))
    lbl.setStyleSheet(f"color:{t.tx};background:transparent;")
    combo = QComboBox()
    combo.setObjectName("wsProvider")
    combo.setFixedHeight(32)
    combo.setFont(qfont(t, t.body_size))
    combo.setStyleSheet(
        f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
        f"color:{t.tx};border:1px solid {t.bd};}}")
    for val, txt in opts:
        combo.addItem(txt, val)
    prow.addWidget(lbl)
    prow.addWidget(combo, 1)
    card.body.addLayout(prow)

    # 五个 provider 下钻控件（Key/地址/模型/engine/请求数）
    def _row(label: str, w):
        r = QHBoxLayout()
        lb = QLabel(label)
        lb.setFont(qfont(t, t.body_size))
        lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
        lb.setFixedWidth(74)
        r.addWidget(lb)
        r.addWidget(w, 1)
        return r, lb

    key_w = _line(t, password=True, placeholder="贴该引擎的密钥")
    url_w = _line(t, placeholder="留空=官方默认")
    model_w = _line(t, placeholder="deepseek-chat")
    engine_w = _line(t, placeholder="search_std")
    count_w = _line(t, placeholder="6") # 文本输入 + parseInt 语义（对齐 web，非 number 控件）
    r1, _l1 = _row("引擎 Key", key_w)
    r2, _l2 = _row("接口地址", url_w)
    r3, model_lb = _row("模型", model_w)
    r4, engine_lb = _row("engine", engine_w)
    r5, _l5 = _row("请求数", count_w)
    card.body.addLayout(r1)
    card.body.addLayout(r2)
    card.body.addLayout(r3)
    card.body.addLayout(r4)
    card.body.addLayout(r5)
    hint = desc(t, "Bing 免 Key；其余引擎填「引擎 Key」与「接口地址」（留空=官方默认；"
                   "DeepSeek 另有模型、智谱另有 engine）。")
    card.body.addWidget(hint)
    page.layout().addWidget(card)
    page._ws_card = card
    page._ws_provider = combo
    page._ws_widgets = {"api_key": key_w, "base_url": url_w, "model": model_w,
                        "engine": engine_w, "count": count_w}
    page._ws_model_row = (model_w, model_lb)
    page._ws_engine_row = (engine_w, engine_lb)

    def _show_rows(prov: str) -> None:
        """web wsShowRows：只显隐带 data-ws 的行（.=仅当前 provider 可见）。"""
        for w, lb in (page._ws_model_row,):
            vis = prov == "deepseek"
            w.setVisible(vis)
            lb.setVisible(vis)
        for w, lb in (page._ws_engine_row,):
            vis = prov == "zhipu"
            w.setVisible(vis)
            lb.setVisible(vis)

    def _sync_to_form() -> None:
        """web wsSyncToForm：按当前 provider 取小节回填五控件 + 显隐。"""
        prov = str(combo.currentData() or "") or "bing"
        cur = config_io.read_path("web_search.%s" % prov, {})
        sec = cur if isinstance(cur, dict) else {}
        key_w.setText(str(sec.get("api_key") or ""))
        url_w.setText(str(sec.get("base_url") or ""))
        model_w.setText(str(sec.get("model") or ""))
        engine_w.setText(str(sec.get("engine") or ""))
        # web `sec.count || 6`：缺省/0/空串一律显示 6（非留空）
        count_w.setText(str(sec.get("count") or 6))
        _show_rows(prov)

    def _sync_from_form() -> None:
        """web wsSyncFromForm：五控件写回 web_search[prov]（count 空/非数→6）。"""
        prov = str(combo.currentData() or "") or "bing"
        try:
            c = int(str(count_w.text() or "").strip())
        except ValueError:
            c = 6
        if c <= 0:
            c = 6
        page._ws_patch = {"web_search.%s.api_key" % prov: key_w.text(),
                          "web_search.%s.base_url" % prov: url_w.text(),
                          "web_search.%s.model" % prov: model_w.text(),
                          "web_search.%s.engine" % prov: engine_w.text(),
                          "web_search.%s.count" % prov: c}

    def _on_prov_changed(idx: int) -> None:
        _show_rows(str(combo.itemData(idx) or ""))

    combo.activated.connect(_on_prov_changed)
    page._ws_sync_to_form = _sync_to_form
    page._ws_sync_from_form = _sync_from_form

    # 手写控件与元数据行共用同一条「改完即生效」防抖（page._c8_mark_dirty）
    _dirty = getattr(page, "_c8_mark_dirty", None)
    if callable(_dirty):
        for w in (key_w, url_w, model_w, engine_w, count_w):
            w.editingFinished.connect(_dirty)
        combo.currentIndexChanged.connect(_dirty)

    # 初值：以 web_search.provider 为准选下拉，再回填（= 载入后 wsSyncToForm）
    cur_prov = str(config_io.read_path("web_search.provider", "") or "").strip() or "bing"
    for i in range(combo.count()):
        if str(combo.itemData(i)) == cur_prov:
            combo.setCurrentIndex(i)
            break
    _sync_to_form()


def _provider_options_from_web() -> list[tuple[str, str]]:
    """解析 web 厂商选择器的选项（value, 文案）——web :1412-1421 的静态
    <option> 清单，选项序与文案随 web 源码走。解析失败返回空（行不建）。"""
    try:
        src = (ROOT / "agent" / "console_html.py").read_text(encoding="utf-8")
        i = src.find('<select id="providerSel">')
        j = src.find("</select>", i) if i >= 0 else -1
        if i < 0 or j < 0:
            return []
        return [(m.group(1), m.group(2)) for m in
                re.finditer(r'<option value="([^"]+)">([^<]+)</option>', src[i:j])]
    except Exception: # noqa: BLE001
        return []


def _model_provider_linkup(t: Tokens, page: QWidget) -> None:
    """模型厂商选择行（web :1411-1421 providerSel）+ **模型下拉**（web :1431 modelSel +
    renderModelSel :4790）+ 联动（applyProvider :4799-4828）。

    ⛔ 这两个选择器在 web 都是**纯 JS 交互控件（无 data-cfg）**——元数据渲染不覆盖
    （r5 教训的原样：sec_meta 把空 `<select id="modelSel">` 解析成空下拉，用户点了
    「啥都出不来」）。这里手写补位：厂商切 → 回填接口地址 + 回填已存密钥（打码值不
    回填，防误存）+ **重灌模型预设**；模型落点 = `api.model`（web syncFromForm :2626
    同键），经隐藏 QLineEdit 挂进 page._c8_binds ⇒ 与「保存设置/改完即生效」同一条链。
    触发用 activated（仅用户选择才发，对齐 web change 事件语义）——初始渲染与程序
    回填不弹窗；初始选中按接口地址反推（web providerFromBase）。
    """
    provs = _providers_from_web()
    opts = _provider_options_from_web()
    if not opts:
        return
    base_w = key_w = None
    for r, w in (getattr(page, "_c8_binds", None) or []):
        cfg = str(getattr(r, "cfg", "") or "")
        if cfg == "api.base_url":
            base_w = w
        elif cfg == "api.api_key":
            key_w = w
    if base_w is None:
        return

    card = Card(t)
    card.body.addWidget(h2(t, "模型厂商与模型"))

    def _combo_style() -> str:
        return (f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
                f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
                f"QComboBox::drop-down{{border:none;width:22px;}}"
                f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
                f"color:{t.tx};border:1px solid {t.bd};}}")

    prow = QHBoxLayout()
    plab = QLabel("厂商")
    plab.setFont(qfont(t, t.body_size, 500))
    plab.setStyleSheet(f"color:{t.tx};background:transparent;")
    combo = QComboBox()
    combo.setObjectName("providerSel")
    combo.setFixedHeight(32)
    combo.setFont(qfont(t, t.body_size))
    combo.setStyleSheet(_combo_style())
    for val, txt in opts:
        combo.addItem(txt, val)
    prow.addWidget(plab)
    prow.addWidget(combo, 1)
    card.body.addLayout(prow)

    # ── 模型行（web :1429-1434 同款版式：下拉 + 自定义手填，二选一显示）──
    mrow = QHBoxLayout()
    mlab = QLabel("模型")
    mlab.setFont(qfont(t, t.body_size, 500))
    mlab.setStyleSheet(f"color:{t.tx};background:transparent;")
    mcombo = QComboBox()
    mcombo.setObjectName("modelSel")
    mcombo.setFixedHeight(32)
    mcombo.setFont(qfont(t, t.body_size))
    mcombo.setStyleSheet(_combo_style())
    medit = QLineEdit()
    medit.setPlaceholderText("自定义模型名（如 glm-4-plus）")
    medit.setFixedHeight(32)
    medit.setFont(qfont(t, t.body_size))
    medit.setStyleSheet(
        f"QLineEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}")
    medit.hide()
    mrow.addWidget(mlab)
    mv = QVBoxLayout()
    mv.setContentsMargins(0, 0, 0, 0)
    mv.addWidget(mcombo)
    mv.addWidget(medit)
    mrow.addLayout(mv, 1)
    card.body.addLayout(mrow)
    page.layout().addWidget(card)

    # 落点：隐藏 QLineEdit 挂 _c8_binds（kind=text → _ctrl_value 走 text()），
    # combo/edit 任一变化都同步到它 ⇒ _collect / 改完即生效 自动带上 api.model。
    from sec_meta import Row as _Row # noqa: PLC0415

    bind_edit = QLineEdit()
    bind_edit.hide()
    page._c8_binds.append((_Row(kind="text", label="模型", cfg="api.model",
                                hint="所选厂商的常用模型都在下拉里；不够用就手填。"), bind_edit))

    def _refill_models(prov: str) -> None:
        """按厂商重灌模型预设（web renderModelSel :4790 同款）；预设为空 ⇒ 显示手填行。"""
        p = provs.get(prov) or {}
        models = [str(x) for x in (p.get("models") or [])]
        mcombo.blockSignals(True)
        mcombo.clear()
        if models:
            for m in models:
                mcombo.addItem(m, m)
            mcombo.show()
            medit.hide()
        else:
            mcombo.addItem("（无预设，请在下方手填）", "")
            mcombo.hide()
            medit.show()
        mcombo.blockSignals(False)

    def _sync_from_ui() -> None:
        v = medit.text().strip() if medit.isVisible() else str(mcombo.currentData() or "")
        if bind_edit.text() != v:
            bind_edit.setText(v)

    mcombo.activated.connect(lambda _i: _sync_from_ui())
    medit.textChanged.connect(lambda _t: _sync_from_ui())

    def _init_selection() -> None:
        """初始：厂商按接口地址反推（web providerFromBase 同款）；模型按 cfg api.model
        对号入座——在预设里就选中，不在（且非空）以临时项插入保留原值（不丢用户配置）。"""
        b = str(base_w.text() or "").strip()
        pick = ""
        for k, p in provs.items():
            if k != "custom" and p.get("base") and b.startswith(str(p["base"])):
                pick = k
                break
        if not pick and not b:
            pick = "deepseek"
        if pick:
            for i in range(combo.count()):
                if str(combo.itemData(i)) == pick:
                    combo.setCurrentIndex(i)
                    break
        _refill_models(pick or "deepseek")
        cur = ""
        try:
            cur = str(config_io.read_path("api.model") or "").strip()
        except Exception: # noqa: BLE001
            cur = ""
        if cur:
            hit = -1
            for i in range(mcombo.count()):
                if str(mcombo.itemData(i)) == cur:
                    hit = i
                    break
            if hit >= 0:
                mcombo.setCurrentIndex(hit)
            elif mcombo.isVisible():
                mcombo.blockSignals(True)
                mcombo.addItem(cur, cur)
                mcombo.setCurrentIndex(mcombo.count() - 1)
                mcombo.blockSignals(False)
            else:
                medit.setText(cur)
        _sync_from_ui()

    def _prov_changed2(idx: int) -> None:
        prov = str(combo.itemData(idx) or "")
        p = provs.get(prov)
        if not p:
            return
        if p.get("base") and hasattr(base_w, "setText"):
            base_w.setText(str(p["base"]))
        _refill_models(prov) # 厂商切 ⇒ 模型预设跟着换（web :4819 同款）
        _sync_from_ui()
        saved = ""
        try:
            kv = config_io.read_path("api.provider_keys")
            if isinstance(kv, dict):
                saved = str(kv.get(prov) or "")
        except Exception: # noqa: BLE001
            saved = ""
        if (saved and key_w is not None and hasattr(key_w, "setText")
                and "••••" not in saved and not saved.startswith("sk-***")):
            key_w.setText(saved)
        have = str(key_w.text()).strip() if key_w is not None and hasattr(key_w, "text") else ""
        is_masked = (not have) or ("••••" in have) or have.startswith("sk-***")
        # web 同口径：非打码且（非 deepseek 或已有已存 Key）→ 不打扰；
        # deepseek 首次（无已存 Key）即使输入框有值也必问一次。
        if not is_masked and not (prov == "deepseek" and not saved):
            return
        _prov_key_dlg(t, page, prov, p, have, key_w)

    combo.activated.connect(_prov_changed2)
    _init_selection()


def _prov_key_dlg(t: Tokens, page: QWidget, prov: str, p: dict,
                  have: str, key_w) -> None: # noqa: ANN001
    """密钥弹窗（web :4820-4825）：密码框预填当前值；「保存 Key」= 填入密钥行
    （落盘仍走「保存 Key/保存设置」按钮，web 同语义）；沿用/暂不填=关窗不动。"""
    label = str(p.get("label") or prov)
    dlg, v = _card_dialog(t, page, f"{label} 密钥", width=560)
    dlg.resize(560, 300)
    tip = QLabel(f"已切换到 {label}（接口地址：{p.get('base') or ''}）。"
                 f"请填写该公司的 密钥（{p.get('keyHint') or '见官网'} 开头）。")
    tip.setFont(qfont(t, 12.5))
    tip.setWordWrap(True)
    tip.setStyleSheet(f"color:{t.tx};background:transparent;")
    v.addWidget(tip)
    ed = QLineEdit(have.replace('"', ""))
    ed.setEchoMode(QLineEdit.EchoMode.Password)
    ed.setFont(qfont(t, 12.5))
    ed.setPlaceholderText((str(p.get("keyHint")) if p.get("keyHint") else "") + "...")
    ed.setStyleSheet(
        f"QLineEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:6px 10px;}}")
    v.addWidget(ed)
    row = QHBoxLayout()
    row.addStretch(1)
    b_no = Btn("暂不填", t, "ghost")
    b_same = Btn("沿用现有 Key", t, "ghost")
    b_ok = Btn("保存 Key", t, "primary")
    row.addWidget(b_no)
    row.addWidget(b_same)
    row.addWidget(b_ok)
    v.addLayout(row)

    def _fill() -> None:
        val = ed.text().strip()
        if val and key_w is not None and hasattr(key_w, "setText"):
            key_w.setText(val)
        dlg.accept()

    b_ok.clicked.connect(_fill)
    b_same.clicked.connect(dlg.reject)
    b_no.clicked.connect(dlg.reject)
    dlg.exec()


def _briefs_appendix(t: Tokens, page: QWidget) -> None:
    """简报卡（web bf* :1090-1184，sec-wechat 尾）：给指定会话注入预设信息——
    选群（或手填会话 key）→ 列出在用/已到期条目 → 添加（内容/截止/每次都用）/删除。
    代价在卡内说明行讲清（随该会话请求发给模型；相关性判断是字面匹配）。"""
    from panels_qt import _line as _bf_line # noqa: PLC0415

    card = Card(t)
    card.body.addWidget(h2(t, "简报（给指定会话注入预设信息，/api/briefs）"))
    prow = QHBoxLayout()
    bf_chat = QComboBox()
    bf_chat.setObjectName("bfChat")
    bf_chat.setMinimumWidth(220)
    bf_chat.setFixedHeight(32)
    bf_chat.setFont(qfont(t, t.body_size))
    bf_chat.setStyleSheet(
        f"QComboBox{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
        f"color:{t.tx};border:1px solid {t.bd};}}")
    b_bf_rf = Btn("刷新会话", t, "ghost")
    b_bf_rf.setObjectName("bfRefresh")
    prow.addWidget(QLabel("会话"))
    prow.addWidget(bf_chat, 1)
    prow.addWidget(b_bf_rf)
    card.body.addLayout(prow)
    bf_key = _bf_line(t, "", placeholder="也可以直接填会话 key（私聊形如 private:wxid_xxx）")
    bf_key.setObjectName("bfChatKey")
    card.body.addWidget(Field(t, "会话 key", "手填优先于上面的下拉", bf_key))
    bf_text = _bf_line(t, "", placeholder="例：这个群周六下午社庆，地点在 3 号工作室")
    bf_text.setObjectName("bfText")
    card.body.addWidget(Field(t, "内容", "", bf_text))
    row2 = QHBoxLayout()
    bf_until = _bf_line(t, "", placeholder="YYYY-MM-DD（留空＝永久）")
    bf_until.setObjectName("bfUntil")
    bf_always = QCheckBox("每次都用（不做相关性判断）")
    b_add = Btn("添加", t, "primary")
    b_add.setObjectName("bfAdd")
    row2.addWidget(bf_until, 1)
    row2.addWidget(bf_always)
    row2.addWidget(b_add)
    card.body.addLayout(row2)
    bf_rows_host = QWidget()
    bf_rows_host.setStyleSheet("background:transparent;")
    bf_rows = QVBoxLayout(bf_rows_host)
    bf_rows.setContentsMargins(0, 0, 0, 0)
    bf_rows.setSpacing(4)
    card.body.addWidget(bf_rows_host)
    card.body.addWidget(desc(t, "代价说清：这段文字会随该会话的请求一起发给模型（用本机模型则不出本机）；"
                                "相关性判断是字面匹配——换个说法可能匹配不上，那这一次就不注入。"))
    page.layout().addWidget(card)

    def _bf_key() -> str:
        return (bf_key.text() or "").strip() or str(bf_chat.currentData() or "")

    def _bf_render(active: list, expired: list, head_text: str = "") -> None:
        while bf_rows.count():
            it = bf_rows.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
                w.setParent(None) # 立刻脱离父子树（见上）
        chat = _bf_key()
        head = QLabel(head_text or
                      (f"{chat}：{len(active)} 条在用"
                       + (f"，{len(expired)} 条已到期（下次用到时自动舍弃）" if expired else "")))
        head.setFont(qfont(t, 12, 500))
        head.setStyleSheet(f"color:{t.tx};background:transparent;")
        bf_rows.addWidget(head)
        for it, done in [(x, False) for x in active] + [(x, True) for x in expired]:
            rid = str(it.get("id") or "")
            line = QWidget()
            lh = QHBoxLayout(line)
            lh.setContentsMargins(0, 0, 0, 0)
            lh.setSpacing(8)
            lb = QLabel("· %s（%s）" % (it.get("text") or "",
                                        "已到期" if done else
                                        ("%s · %s" % ("每次都用" if it.get("always") else "相关才用",
                                                      it.get("until_text") or ""))))
            lb.setFont(qfont(t, 12))
            lb.setStyleSheet(f"color:{t.tx3 if done else t.tx};background:transparent;")
            lh.addWidget(lb, 1)
            b = Btn("删除", t, "ghost")
            b.setObjectName("bfDel")
            b.setFixedWidth(54)
            b.clicked.connect(lambda _=False, _rid=rid: _bf_del(_rid))
            lh.addWidget(b)
            bf_rows.addWidget(line)
        if not active and not expired and not head_text:
            empty = QLabel("这个会话还没设过预设信息。")
            empty.setFont(qfont(t, 12))
            empty.setStyleSheet(f"color:{t.tx3};background:transparent;")
            bf_rows.addWidget(empty)

    def _bf_load() -> None:
        chat = _bf_key()
        if not chat:
            _bf_render([], [], "先选一个会话（或在上面填会话 key）。")
            return
        import urllib.parse as _up # noqa: PLC0415

        r = config_io.get_json("/api/briefs?chat=" + _up.quote(chat), timeout=6.0)
        if not isinstance(r, dict):
            _bf_render([], [], "读不到：后台没连上")
            return
        if r.get("ok") is False:
            _bf_render([], [], "读不到：" + str(r.get("error") or r.get("why") or ""))
            return
        _bf_render(r.get("active") or [], r.get("expired") or [])

    def _bf_del(rid: str) -> None:
        _async_post(None, "/api/briefs", {"action": "del", "chat": _bf_key(), "id": rid},
                    lambda r, e: _bf_load())

    def _bf_add() -> None:
        chat = _bf_key()
        text = (bf_text.text() or "").strip()
        if not chat:
            bf_text.setPlaceholderText("先选会话（或填会话 key）")
            return
        if not text:
            bf_text.setPlaceholderText("先写内容")
            return
        _async_post(None, "/api/briefs",
                    {"action": "add", "chat": chat, "text": text,
                     "until": (bf_until.text() or "").strip(),
                     "always": bf_always.isChecked()},
                    lambda r, e: _bf_add_done(r, e))

    def _bf_add_done(r, e): # noqa: ANN001
        if e or (isinstance(r, dict) and r.get("ok") is False):
            bf_text.setPlaceholderText("没加上：%s" % (e or (r or {}).get("why") or "后台没连上"))
            return
        bf_text.clear()
        bf_until.clear()
        bf_always.setChecked(False)
        bf_text.setPlaceholderText("例：这个群周六下午社庆，地点在 3 号工作室")
        _bf_load()

    def _bf_chats() -> None:
        _eb: dict = {}
        try:
            r = config_io.get_json("/api/wechat-groups", timeout=5.0, err_box=_eb)
        except Exception as e: # noqa: BLE001
            r = None
            _eb["err"] = str(e)
        groups = (r or {}).get("groups") or [] if isinstance(r, dict) else []
        cur = str(bf_chat.currentData() or "")
        bf_chat.clear()
        if not groups:
            # 读失败与"确实没有群"分开说（下拉本来就能手填 key，这里只把原因说清）
            if not isinstance(r, dict):
                bf_chat.addItem("（读不到群聊：" + _read_fail_hint(_eb.get("err"))
                                + "；可在下面手填 key）", "")
            elif r.get("ok") is False:
                bf_chat.addItem("（读不到群聊：" + str(r.get("error") or "读取失败")
                                + "；可在下面手填 key）", "")
            else:
                bf_chat.addItem("（没读到群聊，可在下面手填 key）", "")
        for g in groups:
            wxid = str(g.get("wxid") or "")
            bf_chat.addItem(str(g.get("name") or g.get("wxid") or ""), "group:" + wxid)
        if cur:
            i = bf_chat.findData(cur)
            if i >= 0:
                bf_chat.setCurrentIndex(i)
        _bf_load()

    b_bf_rf.clicked.connect(_bf_chats)
    bf_chat.currentIndexChanged.connect(lambda _i: _bf_load())
    bf_key.editingFinished.connect(_bf_load)
    b_add.clicked.connect(_bf_add)
    _bf_chats() # web :1183 同款：进页自动加载（web 延时 1.2s，这里直接同步）


def _wechat_recheck_appendix(t: Tokens, page: QWidget) -> None:
    """微信安装重检（web sec-wechat :1006「我装好了，重新检测」+ :7627-7633）：
    GET /api/wechat/recheck → 回显三态（运行中 / 已安装未运行 / 仍未检测到）。"""
    card = Card(t)
    card.body.addWidget(h2(t, "微信安装检测"))
    card.body.addWidget(desc(t, "装好或登录微信后点「我装好了，重新检测」——重新扫安装态，不用重启机器人。"))
    row = QHBoxLayout()
    b = Btn("我装好了，重新检测", t, "primary")
    b.setObjectName("wxRecheck")
    row.addWidget(b)
    row.addStretch(1)
    card.body.addLayout(row)
    note = desc(t, "")
    card.body.addWidget(note)
    page.layout().addWidget(card)

    def _recheck() -> None:
        note.setText("正在重新检测微信…")

        def _done(r, e): # noqa: ANN001
            if e:
                note.setText("检测失败：%s" % e)
                return
            inst = (r or {}).get("install") if isinstance(r, dict) else None
            if isinstance(inst, dict):
                st = inst.get("state")
                note.setText("检测到微信在运行" if st == "running"
                             else ("微信已安装，登录后即可用" if st == "installed_not_running"
                                   else "仍未检测到微信"))
            else:
                note.setText(str((r or {}).get("note") or "已重新检测"))

        _async_get("/api/wechat/recheck", _done)

    b.clicked.connect(_recheck)


def _media_components_appendix(t: Tokens, page: QWidget) -> None:
    """媒体组件卡——缺什么装什么。

    数据源 = /api/status 的 media 段（media_status.snapshot）：语音解码链逐件
    （voice.decode[].{name,ok,detail}）、下载器 yt-dlp（media.video.video_url.ytdlp_ready）。
    pip 能装的（pilk / yt-dlp）走**产品自带运行时**的 pip 装进运行时环境（不碰系统
    Python）；装不了的（ffmpeg 二进制 / Windows 语音识别）如实给指引，不假装装好。
    装完自动重拉一次状态，结果原样回显。
    """
    card = Card(t)
    card.body.addWidget(h2(t, "媒体组件（缺什么装什么）"))
    st_lb = desc(t, "检测中…")
    st_lb.setWordWrap(True)
    card.body.addWidget(st_lb)
    btn = Btn("一键安装缺件", t, "primary")
    brow = QHBoxLayout()
    brow.addWidget(btn)
    brow.addStretch(1)
    card.body.addLayout(brow)
    page.layout().addWidget(card)

    state: dict = {"pkgs": [], "manual": []}

    def _refresh() -> None:
        # ⛔ 先判存活再取数：本卡被关页/重建后，`st_lb` 的 C++ 对象已析构，而本函数
        #   还被全局轮询表引用。若不先判，下面这趟 `timeout=10.0` 的请求照样会打完
        #   （10 秒阻塞），最后才在 setText 上抛——攒几十个死卡就是几十趟串烧阻塞，
        #   事件循环再也空不下来（全量自检偶发挂死的真因之一）。
        if not _qt_alive(st_lb):
            return
        ebox: dict = {}
        try:
            st = config_io.get_json("/api/status", timeout=10.0, err_box=ebox) or {}
        except Exception as e: # noqa: BLE001
            ebox = {"err": str(e)}
            st = {}
        # ⛔ get_json 连不上时**返回 None 而不抛** ⇒ 旧实现 `st = ... or {}` 顺手吞掉，
        #   一路掉到 `md.get(...)` 全空、最后 `lines` 为空再走 else 分支，文案变成
        #   「组件状态读不到」——虽然不算「检测中」，但把「没连上」混成了「读不到组件」。
        #   这里显式分派：连不上就说连不上（用户据此去开控制台）。
        if not st:
            st_lb.setText("组件状态读不到：" + _read_fail_hint(ebox.get("err")))
            btn.setEnabled(False)
            state["pkgs"] = []
            state["manual"] = []
            return
        md = st.get("media") or {}
        if not isinstance(md, dict):
            st_lb.setText("状态读不到（media 段缺失）")
            return
        lines: list[str] = []
        pkgs: list[str] = []
        manual: list[str] = [] # 缺、但 pip 装不了的（按钮仍要点得动，点了给指引）
        v = md.get("voice") or {}
        dec = [d for d in (v.get("decode") or []) if isinstance(d, dict)]
        dec_ok = any(d.get("ok") for d in dec)
        if dec:
            for d in dec:
                lines.append("解码器 %s：%s" % (d.get("name") or "?", "可用" if d.get("ok")
                                               else ("缺（%s）" % str(d.get("detail") or "")[:40])))
                if not d.get("ok"):
                    manual.append(str(d.get("name") or "解码器"))
            if not dec_ok:
                pkgs.append("pilk")
        else:
            lines.append("语音解码：组件清单读不到")
        vurl = ((md.get("video") or {}).get("video_url") or {})
        if vurl.get("ytdlp_ready"):
            lines.append("下载器 yt-dlp：已安装")
        elif "ytdlp_ready" in vurl:
            lines.append("下载器 yt-dlp：未安装（外链视频解析需要）")
            pkgs.append("yt-dlp")
        vr = st.get("video_read") or {}
        if vr and not vr.get("ready"):
            manual.append("ffmpeg")
            lines.append("ffmpeg：%s（这个装不了自动版——到 ffmpeg 官网下载后放进 PATH，"
                         "或看「检测中心」的依赖体检）" % str(vr.get("why") or "缺")[:60])
        asr = (vr.get("asr") or {}) if isinstance(vr, dict) else {}
        if isinstance(asr, dict) and asr.get("ok") is False:
            manual.append("Windows 语音识别")
            lines.append("音频识别：%s（Windows 功能——设置 → 时间和语言 → 语音，装完重启生效）"
                         % str(asr.get("why") or "不可用")[:50])
        state["pkgs"] = pkgs
        state["manual"] = manual
        st_lb.setText("\n".join(lines) if lines else "组件状态读不到")
        # ⛔ 按钮不许在「有缺件」时禁用成死点击：缺的哪怕不是 pip 包（备用解码器/
        #    ffmpeg），点下去也要给指引 —— 用户实测「点了没反应」的根因就是
        #    pkgs 为空 ⇒ setEnabled(False)，按钮看着能点、点了无声。
        if pkgs:
            btn.setText("一键安装缺件（%s）" % "、".join(pkgs))
        elif manual:
            btn.setText("缺件没法自动装（看指引）")
        else:
            btn.setText("一键安装缺件")
        btn.setEnabled(bool(pkgs or manual))

    def _install() -> None:
        pkgs = list(state.get("pkgs") or [])
        manual = list(state.get("manual") or [])
        if pkgs:
            pass # 正常自动安装，往下走
        elif manual:
            st_lb.setText("这几样不是 pip 包、装不了自动版：%s。\n"
                          "功能上能自动装的都会出现在按钮文字里；确需补齐按上面"
                          "每行括号里的指引手动装。" % "、".join(manual))
            return
        else:
            st_lb.setText("没有要装的（组件都齐了）")
            return
        btn.setEnabled(False)
        st_lb.setText("安装中（%s）… 走产品自带运行时的 pip，不改系统 Python" % "、".join(pkgs))
        py = ROOT / "runtime" / "python" / "python.exe"
        if not py.exists():
            st_lb.setText("没找到产品运行时（%s），装不了" % py.name)
            btn.setEnabled(True)
            return
        box: dict = {"done": False, "outs": []}

        def _work() -> None:
            import subprocess # noqa: PLC0415

            for pkg in pkgs:
                try:
                    r = subprocess.run([str(py), "-m", "pip", "install", pkg],
                                       capture_output=True, text=True, timeout=600)
                    box["outs"].append((pkg, r.returncode == 0,
                                        str(r.stderr or r.stdout or "")[-120:]))
                except Exception as e: # noqa: BLE001
                    box["outs"].append((pkg, False, str(e)[:120]))
            box["done"] = True

        import threading as _th_main # noqa: PLC0415

        _th_main.Thread(target=_work, daemon=True, name="media-install").start()

        def _apply() -> None:
            if not _qt_alive(st_lb):
                return # 卡已销毁 ⇒ 不再回写、不再自链
            if not box["done"]:
                # 自链设上限（安装线程卡住时不让定时器链无限重排，占死事件循环）
                box["tries"] = box.get("tries", 0) + 1
                if box["tries"] > 100: # 100 × 300ms ≈ 30s
                    st_lb.setText("安装结果读不到（超时）")
                    btn.setEnabled(True)
                    return
                QTimer.singleShot(300, _apply)
                return
            outs = box.get("outs") or []
            bad = [p for p, ok, _m in outs if not ok]
            st_lb.setText("\n".join(("已装 %s ✓" % p) if ok else ("装 %s 没成功：%s" % (p, m))
                                    for p, ok, m in outs) or "没执行")
            if not bad:
                st_lb.setText(st_lb.text() + "\n重查状态…")
                _refresh()
            else:
                btn.setEnabled(True)

    btn.clicked.connect(_install)
    # 跟随全局 8s 轮询：原来只 singleShot 一次 ⇒ 控制台在开页之后才起来，
    # 这张卡就永远停在开页那一刻的结论（「还是检测中」的一个真凶）。
    # anchor=st_lb：这张卡一被销毁，回调即从轮询表摘除，不再打 10 秒阻塞请求
    # （注册表随建页次数膨胀 + 死卡仍发请求 = 全量自检偶发挂死的真因）。
    try:
        import panels_qt as _pq # noqa: PLC0415

        _pq.register_appendix_refresh(_refresh, anchor=st_lb)
    except Exception: # noqa: BLE001
        pass
    QTimer.singleShot(600, _refresh)


APPENDIX = {
    "wechat": (_wechat_emoji_appendix, _briefs_appendix, _wechat_recheck_appendix),
    "tools": _tools_utlist_appendix,
    "model": (_model_local_appendix, _model_provider_linkup),
    "search": _web_search_appendix,
    "imggen": _sd_local_appendix,
    "media": _media_components_appendix,
    "tts": _tts_probe_appendix,
    "community": _community_appendix,
    "feedback": _feedback_appendix,
}
