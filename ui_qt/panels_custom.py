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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidget,
)

import config_io
import sec_meta
from stylekit_qt import Tokens, qfont, rgba, status_colors
from widgets import Badge, Btn, Card, Field, Switch, desc, h2

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0]


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


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
            os.startfile(str(p)) # noqa: S606  （Windows 桌面语义；失败静默不崩）
    except Exception: # noqa: BLE001
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
        f"border-radius:{t.radius_btn}px;gridline-color:{t.bd};}}"
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
    lb_v = desc(t, "厂商")
    lb_v.setMinimumWidth(120)
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
        lb = desc(t, label)
        lb.setMinimumWidth(120)
        e = QLineEdit(dft)
        e.setMaximumWidth(140)
        f_inputs[key] = e
        r.addWidget(lb)
        r.addWidget(e)
        r.addStretch(1)
        card_f.body.addLayout(r)
    f_row2 = QHBoxLayout()
    lb_p = desc(t, "时段")
    lb_p.setMinimumWidth(120)
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
        f"color:{t.tx};background:{_hex(rgba(t.q('tx'), 0 if t.glass else 8))};"
        f"border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:10px 12px;")
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

        box: dict = {"done": False, "r": None}

        def _work() -> None:
            from agent_bridge import post_json # noqa: PLC0415

            box["r"] = post_json("/api/prices", {}, timeout=12.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="ov-prices").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            P = box.get("r")
            if not P or P.get("__err"):
                fc_note.setText("价目加载失败（请重启机器人在控制台重试）")
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
        note2.setText("导出中…")
        try:
            blob = _raw_post("/api/data/export", None, "", 120.0)
            Path(p).write_bytes(blob)
            note2.setText(f"已导出记录（计费+对话）→ {p}")
        except Exception as e: # noqa: BLE001
            note2.setText(f"导出失败：{e}")

    def _import() -> None:
        from PySide6.QtWidgets import QFileDialog # noqa: PLC0415
        p, _f = QFileDialog.getOpenFileName(page, "选迁移包", "", "迁移包 (*.zip)")
        if not p:
            return
        note2.setText("迁移中（合并到当前数据，按内容去重）…")
        try:
            import json as _j # noqa: PLC0415
            blob = Path(p).read_bytes()
            resp = _raw_post("/api/data/import", blob, "application/zip", 180.0)
            j = _j.loads(resp.decode("utf-8", errors="replace"))
            note2.setText(f"迁移完成：{j.get('note') or 'ok'}" if j.get("ok")
                          else f"迁移失败：{j.get('error') or '未知'}")
            _refresh()
        except Exception as e: # noqa: BLE001
            note2.setText(f"迁移失败：{e}")

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

    row1 = QHBoxLayout()
    b_code = Btn("代码检测", t, "primary")
    b_deps = Btn("代码检测＋依赖核对", t, "ghost")
    b_deps.setToolTip("额外跑依赖版本详细核对（55 项，稍慢）")
    row1.addWidget(b_code)
    row1.addWidget(b_deps)
    row1.addStretch(1)
    lay.addLayout(row1)

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
    vf_row = QHBoxLayout()
    b_vfcopy = Btn("复制报告", t, "ghost")
    b_vfcopy.setEnabled(False)
    vf_row.addWidget(b_vfcopy)
    vf_row.addStretch(1)
    card_vf.body.addLayout(vf_row)
    lay.addWidget(card_vf)

    vf_box: dict = {"report": ""}

    def _vf_load() -> None:
        box: dict = {"done": False, "r": None}

        def _work() -> None:
            box["r"] = post_json("/api/verifiers", {}, timeout=10.0)
            box["done"] = True

        threading.Thread(target=_work, daemon=True, name="c8-verifiers").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            lst = (box.get("r") or {}).get("verifiers") or []
            if not lst:
                vf_state.setText("检验器清单读不到")
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
                    f"border:1px solid {_hex(bd)};border-radius:{t.radius_btn}px;padding:4px 12px;}}")

        QTimer.singleShot(300, _apply)

    def _vf_copy() -> None:
        from PySide6.QtWidgets import QApplication # noqa: PLC0415

        if vf_box.get("report"):
            QApplication.clipboard().setText(vf_box["report"])
            b_vfcopy.setText("已复制")
            QTimer.singleShot(1200, lambda: b_vfcopy.setText("复制报告"))

    b_vfcopy.clicked.connect(_vf_copy)
    _vf_load()

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
    lb_g = desc(t, "目标群")
    lb_g.setMinimumWidth(120)
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
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
            for g in ((box.get("r") or {}).get("groups") or []):
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

    for it in items:
        roww = QWidget()
        roww.setStyleSheet("background:transparent;")
        rh = QHBoxLayout(roww)
        rh.setContentsMargins(2, 1, 2, 1)
        rh.setSpacing(8)
        ck = QCheckBox()
        ck.setToolTip("勾选=这项测过了（会话内状态；web 侧用 localStorage 记忆）")
        rh.addWidget(ck)
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
    btn_row = QHBoxLayout()
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
    btn_row.addWidget(b_refresh)
    btn_row.addWidget(b_sel)
    btn_row.addWidget(b_undo)
    btn_row.addWidget(b_clear)
    btn_row.addStretch(1)
    scard.body.addLayout(btn_row)
    sess_list = _bordered_list(t, "sessList", 240)
    scard.body.addWidget(sess_list)
    snote = desc(t, "")
    scard.body.addWidget(snote)
    scard.body.addWidget(desc(t, "勾选每条左侧「删」→「删除选中」＝只删这几条（同一天其他记录不动）；"
                                "删错了点「撤销上次删除」。清空＝清全部明细（不可恢复）。"))
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
        try:
            r = config_io.get_json("/api/sessions?limit=30", timeout=5.0)
        except Exception: # noqa: BLE001
            r = None
        if not isinstance(r, dict):
            snote.setText("后台没连上（或该接口未提供）。顶部状态灯恢复绿色后点「刷新」再试。")
            badge.set("err", "读不到")
            return
        state["sessions"] = r.get("sessions") or []
        snote.setText(f"读取成功 · {time.strftime('%H:%M:%S')} · {len(state['sessions'])} 条")
        _render_sessions()

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
        f"QComboBox{{background:{rgba(t.q('tx'), 0 if t.glass else 16).name(QColor.NameFormat.HexArgb)};"
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
        data = config_io.get_json("/api/sessions", timeout=5.0)
        if data is None:
            area.setPlainText("")
            jnote.setText("后台没连上（或该接口未提供）。")
            return
        text = json.dumps(data, ensure_ascii=False, indent=1)
        area.setPlainText(text[:8000] + ("\n…（截断显示前 8000 字符）" if len(text) > 8000 else ""))
        jnote.setText(f"读取成功 · {time.strftime('%H:%M:%S')}")

    _refresh_raw()
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
    b_del.clicked.connect(lambda _=False: _arc_action(chat_key, m.get("id"), "delete", reload_fn))
    h.addWidget(b_block)
    h.addWidget(b_del)
    return w


def _arc_action(chat_key: str, mid, action: str, reload_fn) -> None:
    api = {"block": "/api/archive/block", "unblock": "/api/archive/unblock",
           "delete": "/api/archive/delete"}.get(action)
    if not api:
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
        except Exception as e: # noqa: BLE001
            note.setText(f"读取失败：{e}")
            badge.set("err", "读不到")

    _refresh()
    card.body.addWidget(note)
    lay.addWidget(card)
    # P0-A②：原「清空日志」按钮移除 —— web「运行日志」区根本没有清空 API
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
    hooks = [_save, _load, lambda: _open_dir(cfg_path.parent)]
    lay.addLayout(_btn_row(t, [("保存全部设置", "primary"), ("重新读取", "ghost"),
                               ("打开配置目录", "ghost")], hooks))
    lay.addWidget(desc(t, "保存后需重启才能完全生效的部分：模型/人设/白名单等；界面与光标类即时生效。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 分发表（panels_qt.build_panel 查这里）

def vermat_panel(t: Tokens, on_save=None) -> QWidget:
    """ P0-3：版本能力矩阵（web `sec-vermat` 真值）+ 顶部「版本与更新」卡。

    两件事：
      3a. **矩阵三态**（allowed / 实测 / 严格档拦停，web console_html.py:3346 口径）——
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
                from agent_bridge import get_json # noqa: PLC0415

                box["v"] = get_json("/api/update", timeout=8.0)
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
        # web vmAllow：写「本次允许发送」的会话期放行（重启失效，不落配置）
        def _work() -> None:
            try:
                from agent_bridge import post_json # noqa: PLC0415

                post_json("/api/version_allow", {}, timeout=15.0)
            except Exception: # noqa: BLE001
                pass

        import threading # noqa: PLC0415

        threading.Thread(target=_work, daemon=True, name="vermat-allow").start()
        gate_lb.setText("已按「仅本次允许」放行（重启后重新拦）。")

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
    """一个带边框、可滚的动态列表容器；objectName 固定，便于自检按名取证。"""
    lw = QListWidget()
    lw.setObjectName(name)
    lw.setMinimumHeight(min_h)
    lw.setStyleSheet(
        f"QListWidget{{background:{rgba(t.q('tx'), 0 if t.glass else 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:4px;}}"
        f"QListWidget::item{{border-bottom:1px solid {t.bd};padding:2px 0;}}"
    )
    return lw


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
            QTimer.singleShot(300, _apply)
            return
        on_done(box.get("r"), box.get("err"))

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

    for _cfg, ctrl, kind in binds:
        if ctrl is None:
            continue
        if kind == "checkbox":
            ctrl.toggled.connect(_mark_dirty)
        elif kind == "select":
            ctrl.currentIndexChanged.connect(_mark_dirty)
        elif kind == "textarea":
            ctrl.textChanged.connect(_mark_dirty)
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
      · 人设列表：读 /api/personas（照 console_html.py:6180 同款接口，合并 custom/scores），
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

    state: dict = {"items": [], "sort": 0, "cat": ""} # cat=当前分区过滤（空=全部）

    # ── 人设库卡 ──
    pcard = Card(t)
    pcard.body.addWidget(h2(t, "人设库（/api/personas）"))

    # 按钮排：排序 / 恢复默认 / 恢复上个人设（对齐 web 人设选单 btns :1311-1316）
    btn_row = QHBoxLayout()
    b_sort = Btn("↓ 按评估分数排序", t, "ghost")
    b_sort.setObjectName("pSort")
    b_sort_off = Btn("恢复默认顺序", t, "ghost")
    b_sort_off.setObjectName("pSortOff")
    b_restore = Btn("恢复上个人设", t, "ghost")
    b_restore.setObjectName("pRestorePrev")
    btn_row.addWidget(b_sort)
    btn_row.addWidget(b_sort_off)
    btn_row.addWidget(b_restore)
    btn_row.addStretch(1)
    pcard.body.addLayout(btn_row)

    # 分区 chips（web #personaCats :1308，allCats/renderChips :6199-6231 同款）——
    # 人设按分区过滤（内置默认「网络热门」，自定义默认「自定义」）；
    # 分区管理（新建/删除，/api/persona/cats*）列。
    cat_row_w = QWidget()
    cat_row = QHBoxLayout(cat_row_w)
    cat_row.setContentsMargins(0, 0, 0, 0)
    cat_row.setSpacing(6)
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
            listw.setItemWidget(it, _persona_card(t, p, handlers))
            it.setSizeHint(listw.itemWidget(it).sizeHint())
        badge.set("info", f"{len(items)} 个" if items else "暂无人设")

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
        """分区 chips（web renderChips :6206 同款）：「全部」+ 各分区，点击过滤。"""
        while cat_row.count():
            it = cat_row.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.deleteLater()
        cats: list = []
        for p in state["items"]:
            c = p.get("cat") or ("自定义" if str(p.get("key") or "").startswith("custom") else "网络热门")
            if c not in cats:
                cats.append(c)
        for name in ["全部"] + cats:
            b = Btn(name, t, "ghost")
            cur = state.get("cat", "")
            active = (name == "全部" and not cur) or (name == cur and name != "全部")
            b.setStyleSheet("border-radius:14px;padding:2px 12px;"
                            + ("font-weight:700;" if active else ""))
            b.clicked.connect(lambda _=False, n=name: _pick_cat(n))
            cat_row.addWidget(b)
        cat_row.addStretch(1)

    def _pick_cat(n: str) -> None:
        state["cat"] = "" if n == "全部" else n
        _render_cats()
        _render()

    def _restore_prev() -> None:
        prev = config_io.read_path("persona.last_used") or {}
        if not (prev.get("name") or prev.get("text")):
            pnote.setText("还没有可恢复的人设（先「使用」过一次）")
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

    handlers = {"use": _apply_persona, "del": _del_persona, "fav": _fav_persona}

    def load_personas() -> None:
        try:
            r = config_io.get_json("/api/personas", timeout=5.0)
        except Exception: # noqa: BLE001
            r = None
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

    search.textChanged.connect(_render)
    b_sort.clicked.connect(apply_sort)
    b_sort_off.clicked.connect(lambda: (state.__setitem__("sort", 0),
                                        b_sort.setText("↓ 按评估分数排序"), _render()))
    b_restore.clicked.connect(_restore_prev)
    load_personas()

    # ── 评分补足块（pScoreLLM / pEnrich / pWebFetch 真接后端，:1326-1336）──
    rcard = Card(t)
    rcard.body.addWidget(h2(t, "评分补足"))
    rrow = QHBoxLayout()
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
    rrow.addWidget(b_score)
    rrow.addWidget(b_enrich)
    rrow.addWidget(b_wf)
    rrow.addWidget(QLabel("补足轮数"))
    rrow.addWidget(rounds)
    rrow.addWidget(use_llm)
    rrow.addWidget(QLabel("允许模型处理"))
    rrow.addStretch(1)
    rcard.body.addLayout(rrow)
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

    part = Combo(t, [("安静型", "low"), ("普通群友", "medium"), ("活跃型", "high")],
                 config_io.read_path("persona.participation") or "medium")
    part.setObjectName("personaPart")
    ccard.body.addWidget(Field(t, "参与度", "在群里多话还是少话", part))
    binds.append(("persona.participation", part, "select"))

    role_text_ctrl = _area(t, _as_text(config_io.read_path("persona.role_text")),
                          rows=4, placeholder="留空=内置小鲸鱼角色卡；填了=完全替换")
    role_text_ctrl.setObjectName("personaRoleText")
    ccard.body.addWidget(Field(t, "自定义角色文本", "留空=内置；填了=完全替换（可参考 agent/persona.py）", role_text_ctrl))
    binds.append(("persona.role_text", role_text_ctrl, "textarea"))

    custom_rules = _area(t, _as_text(config_io.read_path("persona.custom_rules")),
                         rows=2, placeholder="如：回复永远不超过 5 个字")
    ccard.body.addWidget(Field(t, "额外规则", "人设之外的补充约束", custom_rules))
    binds.append(("persona.custom_rules", custom_rules, "textarea"))

    sys_custom = _area(t, _as_text(config_io.read_path("system_prompt.custom")),
                       rows=3, placeholder="追加到系统提示词末尾（管理员补充，最高优先级）")
    ccard.body.addWidget(Field(t, "系统提示词补充", "写在这里的文字追加到系统提示词末尾；保存后下一轮生效", sys_custom))
    binds.append(("system_prompt.custom", sys_custom, "textarea"))

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
    h.setSpacing(8)
    # 星标改项目图标库 SVG；
    #   收藏=实心金星，未收藏=灰描边星；按钮窄条 34px。
    from PySide6.QtGui import QIcon # noqa: PLC0415

    from icons import INNER, svg_pixmap # noqa: PLC0415

    fav = Btn("", t, role="ghost")
    fav.setFixedWidth(34)
    fav.setIcon(QIcon(svg_pixmap(
        INNER["star-filled"] if p.get("fav") else INNER["star"],
        t.warn if p.get("fav") else t.tx3, 14)))
    fav.setToolTip("已收藏（置顶；点击取消）" if p.get("fav") else "收藏置顶")
    fav.clicked.connect(lambda _=False, _p=p: handlers["fav"](_p))
    # 窄窗契约：name/sc 固定宽 → 评分列紧跟人设名、位置稳定不浮动；
    #   txt 最小宽 0 可被布局压缩；卡片最小宽压到 ~400px，
    #   列表视口再窄「使用/删」也不会被裁出视口（不用拉宽窗口）。
    from PySide6.QtGui import QFontMetrics # noqa: PLC0415

    name = QLabel(p.get("name") or "(未命名)")
    name.setFont(qfont(t, 13, 600))
    name.setStyleSheet(f"color:{t.tx};background:transparent;")
    name.setText(QFontMetrics(name.font()).elidedText(
        name.text(), Qt.ElideRight, 110))
    name.setFixedWidth(122)
    sc = p.get("__score")
    sc_lb = QLabel(f"模型 {sc:.2f}" if isinstance(sc, (int, float)) else "")
    sc_lb.setFont(qfont(t, 12))
    sc_lb.setStyleSheet(f"color:{t.warn};background:transparent;")
    sc_lb.setFixedWidth(78)
    use = Btn("使用", t, "ghost")
    use.setFixedWidth(54)
    use.clicked.connect(lambda _=False, _p=p: handlers["use"](_p))
    delete = Btn("删", t, "ghost")
    delete.setFixedWidth(40)
    delete.clicked.connect(lambda _=False, _p=p: handlers["del"](_p))
    txt = QLabel((p.get("text") or "").replace("\n", " ")[:60])
    txt.setFont(qfont(t, 11.5))
    txt.setStyleSheet(f"color:{t.tx3};background:transparent;")
    txt.setWordWrap(True)
    # 摘要限宽 + 最小宽 0（窄窗时先牺牲摘要、保右侧按钮完整可见）
    # 。卡最小高度保证 itemWidget 不压扁。
    txt.setMaximumWidth(300)
    txt.setMinimumWidth(0)
    w.setMinimumHeight(44)
    h.setContentsMargins(6, 4, 12, 4)
    h.addWidget(fav)
    h.addWidget(name)
    h.addWidget(sc_lb)
    h.addWidget(txt, 1)
    h.addSpacing(6)
    h.addWidget(use)
    h.addWidget(delete)
    return w


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
        f"QComboBox{{background:{rgba(t.q('tx'), 0 if t.glass else 16).name(QColor.NameFormat.HexArgb)};"
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
    clr_row = QHBoxLayout()
    b_sel = Btn("清除勾选的印象", t, "danger")
    b_sel.setObjectName("memClearSel")
    b_sel.setEnabled(False)
    b_all = Btn("清除全部", t, "danger")
    b_all.setObjectName("memClearAll")
    clr_row.addWidget(b_sel)
    clr_row.addWidget(b_all)
    clr_row.addStretch(1)
    mcard.body.addLayout(clr_row)
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
            mem_table.setItemWidget(it, _member_card(t, m, state["chat_key"], _render_members, _sync_sel_btn))
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
        try:
            r = config_io.get_json("/api/memory" + (f"?chat_key={chat_key}" if chat_key else ""), timeout=5.0)
        except Exception: # noqa: BLE001
            r = None
        if not isinstance(r, dict):
            mnote.setText("后台没连上（或该接口未提供）。顶部状态灯恢复绿色后点「刷新」再试。")
            badge.set("err", "读不到")
            return
        state["chats"] = r.get("chats") or []
        _fill_chats(mem_search.text())
        state["members"] = r.get("members") or []
        mnote.setText(f"读取成功 · {time.strftime('%H:%M:%S')} · {len(state['members'])} 位成员")
        _render_members()

    # 共享群（memGroupsBox）：GET /api/wechat-groups（web :6043）
    def load_groups() -> None:
        try:
            r = config_io.get_json("/api/wechat-groups", timeout=5.0)
        except Exception: # noqa: BLE001
            r = None
        groups = (r or {}).get("groups") or []
        mem_groups.clear()
        if not groups:
            mem_groups.addItem("未检测到群（启动机器人并检测群后这里会列出）")
            return
        for g in groups:
            mem_groups.addItem(g.get("name") or g.get("nick") or g.get("wxid") or "",
                               g.get("wxid") or g.get("name") or "")

    chat_sel.currentIndexChanged.connect(_on_chat)
    mem_search.textChanged.connect(lambda: _fill_chats(mem_search.text()))
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
        mnote.setText(f"清除 {len(picked)} 位成员印象中…")
        _async_post(None, "/api/memory",
                    {"chat_key": state["chat_key"], "user_ids": picked, "scope": scope},
                    lambda r, e: (load_memory(state["chat_key"]) or mnote.setText(
                        f"已清除 {len(picked)} 位" if (r and r.get("ok") is not False)
                        else f"清除失败：{e or (r or {}).get('error') or '后台没连上'}")))

    def _clear_all() -> None:
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
    mem_groups = QComboBox()
    mem_groups.setObjectName("memGroupsBox")
    mem_groups.setMinimumWidth(240)
    mem_groups.setFixedHeight(32)
    mem_groups.setFont(qfont(t, t.body_size))
    mem_groups.setStyleSheet(
        f"QComboBox{{background:{rgba(t.q('tx'), 0 if t.glass else 16).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
        f"color:{t.tx};border:1px solid {t.bd};}}")
    ccard.body.addWidget(Field(t, "共享群（选好后保存写入 memory.shared_group_names）",
                               "与这些群共享记忆；与 web syncMemGroupsToCfg 同义", mem_groups))
    ccard.body.addWidget(desc(t, "web 用一组勾选框管理共享群；Qt 侧这里用下拉列出已检测到的群，"
                                "保存时把当前选中的群名写入 memory.shared_group_names。"))
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

    # 自检钩子
    page.c12_load = lambda: (load_memory(chat_sel.currentData() or ""), load_groups())
    page.c12_table = mem_table
    page.c12_chats = chat_sel

    load_memory("")
    load_groups()
    lay.addStretch(1)
    return page


def _member_card(t: Tokens, m: dict, chat_key: str, reload_fn, sync_fn) -> QWidget:
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
    del_btn = Btn("删除", t, "ghost")
    del_btn.setFixedWidth(54)
    del_btn.clicked.connect(lambda _=False: _del_member(chat_key, m, reload_fn))
    h.addWidget(pick)
    h.addWidget(name)
    h.addWidget(cnt)
    h.addWidget(upd)
    h.addStretch(1)
    h.addWidget(del_btn)
    return w


def _del_member(chat_key: str, m: dict, reload_fn) -> None:
    _async_post(None, "/api/memory",
                {"chat_key": chat_key, "user_id": m.get("userId")},
                lambda r, e: reload_fn())


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
    """表情包收藏夹（web #emojiBox :6820-6890 对齐）——批3 动态列表之一。

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
        def _work(bx: dict) -> None:
            try:
                from agent_bridge import post_json # noqa: PLC0415

                bx["rsp"] = post_json("/api/emojis/delete", {"name": name}, timeout=8.0)
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        import threading as _th # noqa: PLC0415

        bx: dict = {"done": False, "rsp": None, "err": None}
        _th.Thread(target=_work, daemon=True, args=(bx,), name="emoji-del").start()

        def _apply() -> None:
            if not bx["done"]:
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

        bx: dict = {"done": False, "rsp": None, "err": None}
        _th.Thread(target=_work, daemon=True, args=(bx,), name="emoji-load").start()

        def _apply() -> None:
            if not bx["done"]:
                QTimer.singleShot(150, _apply)
                return
            state["all"] = (bx["rsp"] or {}).get("emojis") or []
            _render()

        QTimer.singleShot(150, _apply)

    search.textChanged.connect(_render)
    QTimer.singleShot(400, _load) # 面板建好后后台拉一次（不冻建页）


def _tools_utlist_appendix(t: Tokens, page: QWidget) -> None:
    """工具清单（web sec-tools 的 utGlobals/utProblems/utList :3826-3935 对齐）——批3 动态列表。

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
    card.body.addWidget(ut_prob)
    host = QWidget()
    host.setStyleSheet("background:transparent;")
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    card.body.addWidget(host)
    page.layout().addWidget(card)

    from PySide6.QtWidgets import QCheckBox as _QCB # noqa: PLC0415 — 局部别名不遮蔽模块级名

    box: dict = {"done": False, "ut": None}

    def _load() -> None:
        def _work() -> None:
            box["ut"] = (config_io.get_json("/api/status", timeout=8.0) or {}).get("user_tools")
            box["done"] = True

        _th.Thread(target=_work, daemon=True, name="ut-list").start()

        def _apply() -> None:
            if not box["done"]:
                QTimer.singleShot(300, _apply)
                return
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
                v.addWidget(roww)

    import threading as _th # noqa: PLC0415
    import urllib.parse as _up # noqa: PLC0415

    QTimer.singleShot(400, _load)


def _model_local_appendix(t: Tokens, page: QWidget) -> None:
    """本机模型端点探测（web localProbe/localList :1444-1495 对齐）——批3 本地模型探测。

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
                    it.widget().deleteLater()
            for ep in (meta.get("all") or []):
                if not isinstance(ep, dict):
                    continue
                ok = bool(ep.get("reachable"))
                ms = ep.get("models") or []
                fr = QFrame()
                fr.setStyleSheet(
                    f"QFrame{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 8))};"
                    f"border:1px solid {t.bd};border-radius:{t.radius_btn}px;}}")
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


APPENDIX = {
    "wechat": _wechat_emoji_appendix,
    "tools": _tools_utlist_appendix,
    "model": _model_local_appendix,
}
