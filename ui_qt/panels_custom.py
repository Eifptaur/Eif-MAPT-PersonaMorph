# -*- coding: utf-8 -*-
"""手写面板 —— web 侧**非配置行**形态的 5 个 sec（概览/体检/明细/日志/配置格式）。

为什么手写：这些面板在 web 侧不是「data-cfg 行」而是统计格 / 表格 / 按钮 / JSON
编辑器，没有行元数据可解析；但版式仍逐块对齐 web。
其余 22 个 sec 全部由 `panels_qt.py` 元数据驱动生成，本文件只装"手写特例"。
（第 3 棒从 panels_qt.py 拆出：生成器与手写件分文件，两份都不破 300 行红线。）
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
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

import sec_meta
from stylekit_qt import Tokens, qfont, rgba
from widgets import Badge, Btn, Card, desc, h2

HERE = Path(__file__).resolve().parent


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


def _btn_row(t: Tokens, pairs: list[tuple[str, str]]) -> QHBoxLayout:
    r = QHBoxLayout()
    for label, role in pairs:
        r.addWidget(Btn(label, t, role))
    r.addStretch(1)
    return r


# ---------------------------------------------------------------- 概览（统计格 + 操作按钮）


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
    s = QLabel(label)
    s.setFont(qfont(t, 12))
    s.setStyleSheet(f"color:{t.tx3};background:transparent;")
    v.addWidget(b)
    v.addWidget(s)
    return w


def overview_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("overview")
    page, lay = _page(t, s.title)
    lay.addWidget(desc(t, "机器人运作状态与账户信息（数据每 8 秒自动刷新）。"))
    tip = QLabel("代码检测：尚未运行（点「检测中心」页的代码检测/代码检测＋依赖核对）")
    tip.setFont(qfont(t, 12.5, 700))
    tip.setWordWrap(True)
    tip.setStyleSheet(
        f"color:{t.blue};background:{t.blue_soft};border:1px solid {_hex(rgba(t.q('blue'), 70))};"
        f"border-radius:{t.radius_btn}px;padding:8px 12px;"
    )
    lay.addWidget(tip)

    card = Card(t)
    grid = QGridLayout()
    grid.setSpacing(8)
    cells = [
        ("0", "累计会话数"), ("0", "累计用量"), ("0", "已发消息"), ("¥0", "累计成本"),
        ("—", "今日用量"), ("—", "本周期"), ("0", "目标群"), ("—", "最近5条成本"),
        ("—", "平均每条成本"), ("—", "今日其他工具成本"), ("—", "累计其他工具成本"),
    ]
    for i, (val, lb) in enumerate(cells):
        grid.addWidget(_stat_cell(t, val, lb), i // 4, i % 4)
    card.body.addLayout(grid)
    lay.addWidget(card)

    lay.addLayout(_btn_row(t, [("测试 API 连通", "primary"), ("一键删", "ghost"),
                               ("勾选删", "ghost"), ("导出记录", "ghost"), ("迁移数据", "ghost")]))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 体检（按钮组 + 自检清单）


def check_panel(t: Tokens) -> QWidget:
    page, lay = _page(t, "检测中心（代码检测 / 点击测试）", "warn", "还没检测")
    lay.addWidget(desc(t, "「代码检测」＝纯代码层检查（编译/依赖/角色卡评估/种子库/保护机制），零风险；"
                           "「点击测试」＝环境/配置/界面自动化共 55 项，全程序内完成，不碰鼠标。"))
    lay.addLayout(_btn_row(t, [("代码检测", "primary"), ("代码检测＋依赖核对", "ghost"),
                               ("查看进度条", "ghost")]))
    lay.addWidget(desc(t, "判决分四档：通过 · 卡住 · 部分通过 · 没测到；"
                          "点完之后症状按钮自己会变色：绿＝通过 · 黄＝部分通过 · 红＝卡住。"))
    lay.addLayout(_btn_row(t, [("点击测试", "primary"), ("停止检测", "ghost")]))

    card = Card(t)
    card.body.addWidget(h2(t, "功能自检清单（按重要性排序）"))
    items = ["环境体检 · 点上方「点击测试」", "发消息 · 群里 @机器人 说句话",
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


# ---------------------------------------------------------------- 明细（按天落盘的用量表）


def sessions_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("sessions")
    page, lay = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "发了什么、多少用量、耗时，按天落盘可勾选删除。"))
    card = Card(t)
    card.body.addWidget(h2(t, "按天查看"))
    card.body.addWidget(desc(t, "点日期查看当天会话/词数/成本。原型不接后台数据，形态对齐 web 侧的明细表："))
    for line in ("2026-09-23 · 会话 12 · 词数 3,408 · 成本 ¥0.21",
                 "2026-09-22 · 会话 9 · 词数 2,765 · 成本 ¥0.18",
                 "2026-09-21 · 会话 15 · 词数 4,120 · 成本 ¥0.26"):
        card.body.addWidget(desc(t, line))
    lay.addWidget(card)
    lay.addLayout(_btn_row(t, [("删除所选日期", "ghost"), ("清空全部明细", "danger")]))
    lay.addWidget(desc(t, "删除只动本机记录，不影响已发出的消息。"))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 日志（动作与失败原因）


def log_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("log")
    page, lay = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "动作与失败原因，出问题先看这一屏。"))
    card = Card(t)
    card.body.addWidget(h2(t, "最近记录（原型样本，不接后台）"))
    for line in ("09:12:03 · 发送文字 · 群「设计群」 · 成功",
                 "09:14:41 · 切换会话 · 重试 1 次 · 成功",
                 "09:15:02 · 发送图片 · 失败 · 图片超过大小限制，已压缩重试"):
        card.body.addWidget(desc(t, line))
    lay.addWidget(card)
    lay.addLayout(_btn_row(t, [("打开日志目录", "ghost"), ("清空日志", "danger")]))
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 配置格式（完整 JSON）


def json_panel(t: Tokens) -> QWidget:
    s = sec_meta.get("json")
    page, lay = _page(t, s.title)
    lay.addWidget(desc(t, s.desc or "全部配置的配置文件，改前先备份。"))
    sample = ""
    try:
        raw = json.loads((HERE.parents[0] / "config.example.json").read_text(encoding="utf-8"))
        sample = json.dumps(raw, ensure_ascii=False, indent=1)[:4000]
    except Exception:  # noqa: BLE001
        sample = ""
    card = Card(t)
    card.body.addWidget(h2(t, "config.json（原型样本片段）"))
    card.body.addWidget(_plain_area(t, sample, placeholder="完整配置文件内容", height=360))
    card.body.addWidget(desc(t, "改完保存后需要重启才能生效；改前先在配置目录留一份备份。"))
    lay.addWidget(card)
    lay.addLayout(_btn_row(t, [("保存并重启", "primary"), ("打开配置目录", "ghost")]))
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
