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
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

import config_io
import sec_meta
from stylekit_qt import Tokens, qfont, rgba
from widgets import Badge, Btn, Card, desc, h2

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0]


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


def _page(t: Tokens, title: str, level: str = "idle", badge: str = "读取中"):
    """面板公共骨架：页 + 标题行 + Badge。返回 (page, lay)。"""
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    lay.addWidget(h2(t, title, Badge(t, level, badge)))
    return page, lay


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
    page, lay = _page(t, s.title)
    badge = lay.itemAt(0).widget().findChildren(Badge)[0] if lay.itemAt(0).widget().findChildren(Badge) else None
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
            if badge is not None:
                badge.set("idle", "读取中")
            return
        paused = bool(st.get("paused"))
        wx_on = bool(st.get("wechat_connected"))
        lis = st.get("listen") or {}
        n = lis.get("groups", 0) + lis.get("privates", 0) if isinstance(lis, dict) else 0
        mo = st.get("model") or {}
        cells["run"].setText("运行中" if wx_on and not paused else ("已暂停" if paused else "微信没连上"))
        cells["listen"].setText(str(n))
        cells["model"].setText(str(mo.get("name") or ("已配置" if mo.get("configured") else "没填密钥")))
        cells["paused"].setText("是" if paused else "否")
        cells["wechat"].setText("已连接" if wx_on else "没连上")
        up = st.get("uptime_s") or st.get("uptime")
        cells["uptime"].setText(f"{round(up)} 秒" if isinstance(up, (int, float)) else "—")
        if badge is not None:
            badge.set("warn" if (paused or not wx_on) else "ok",
                      "已暂停" if paused else ("运行中" if wx_on else "微信没连上"))

    _refresh()
    timer = QTimer(page)
    timer.setInterval(8000)          # web loadStatus 同款 8 秒
    timer.timeout.connect(_refresh)
    timer.start()
    hooks = [lambda: _refresh(), None, None, None, None]
    lay.addLayout(_btn_row(t, [("立即刷新", "primary"), ("测试 API 连通", "ghost"),
                               ("导出记录", "ghost"), ("迁移数据", "ghost"), ("一键删", "danger")], hooks))
    lay.addWidget(desc(t, "「测试 API 连通 / 导出 / 迁移 / 删除」走网页控制台（涉及弹窗与文件选择，Qt 壳先不代跑）。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 体检（诚实入口 + 自检清单）


def check_panel(t: Tokens) -> QWidget:
    page, lay = _page(t, "检测中心（代码检测 / 点击测试）", "warn", "还没检测")
    lay.addWidget(desc(t, "「代码检测」＝纯代码层检查（编译/依赖/角色卡评估/种子库/保护机制），零风险；"
                           "「点击测试」＝环境/配置/界面自动化共 55 项，全程序内完成，不碰鼠标。"))
    lay.addWidget(desc(t, "检测本体在后台进程里跑（agent/code_check.py），入口在网页控制台的「体检」页——"
                           "Qt 壳不代跑长任务，也不伪造检测结果。"))
    card = Card(t)
    card.body.addWidget(h2(t, "功能自检清单（按重要性排序）"))
    items = ["环境体检 · 网页控制台「点击测试」", "发消息 · 群里 @机器人 说句话",
             "拍一拍 · 先「简易检测」，再完整检测", "引用回复 · 引用某条消息回复",
             "发图 · 让机器人「发一张图」", "识图 · 引用图片＋@机器人 分析这张",
             "联网搜索 · @机器人 今天的天气/新闻", "记忆 · 让机器人记住一件事后到「记忆」页看",
             "挂件 · 看右下角鲸鱼挂件的数据与拖拽", "启停重启 · 顶部停止/重启后能接管",
             "多厂商切换 · 换厂商保存后测试连通"]
    for it in items:
        card.body.addWidget(desc(t, it))
    lay.addWidget(card)
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 明细（GET /api/sessions，拿不到如实说）


def sessions_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("sessions")
    page, lay = _page(t, s.title)
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
            return
        text = json.dumps(data, ensure_ascii=False, indent=1)
        area.setPlainText(text[:8000] + ("\n…（截断显示前 8000 字符）" if len(text) > 8000 else ""))
        note.setText(f"读取成功 · {time.strftime('%H:%M:%S')}")

    _refresh()
    card.body.addWidget(note)
    lay.addWidget(card)
    hooks = [_refresh, None, None]
    lay.addLayout(_btn_row(t, [("刷新", "primary"), ("删除所选日期", "ghost"), ("清空全部明细", "danger")], hooks))
    lay.addWidget(desc(t, "删除类操作请用网页控制台（有二次确认与按天勾选）；这里只读，不动数据。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 日志（真读 logs/persona_morph.log 尾部）


def log_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("log")
    page, lay = _page(t, s.title)
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
                return
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = lines[-200:]
            area.setPlainText("\n".join(tail))
            note.setText(f"共 {len(lines)} 行 · 显示尾部 {len(tail)} 行 · {time.strftime('%H:%M:%S')}")
        except Exception as e:  # noqa: BLE001
            note.setText(f"读取失败：{e}")

    _refresh()
    card.body.addWidget(note)
    lay.addWidget(card)
    hooks = [_refresh, lambda: _open_dir(ROOT / "logs"), None]
    lay.addLayout(_btn_row(t, [("刷新", "primary"), ("打开日志目录", "ghost"), ("清空日志", "danger")], hooks))
    lay.addWidget(desc(t, "清空日志请用网页控制台；这里只读，不删任何记录。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 配置格式（真读 config.json + 校验写回 + 备份）


def json_panel(t: Tokens, on_save=None) -> QWidget:
    s = sec_meta.get("json")
    page, lay = _page(t, s.title)
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
        except Exception as e:  # noqa: BLE001
            note.setText(f"读取失败：{e}")

    def _save() -> None:
        ok, msg = config_io.write_full(area.toPlainText())
        note.setText(("已保存：" if ok else "没保存成：") + msg + f"  ·  {time.strftime('%H:%M:%S')}")
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

MANUAL = {
    "overview": overview_panel,
    "check": check_panel,
    "sessions": sessions_panel,
    "log": log_panel,
    "json": json_panel,
}
