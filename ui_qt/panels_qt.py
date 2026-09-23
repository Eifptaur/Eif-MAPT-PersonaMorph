# -*- coding: utf-8 -*-
"""通用面板构建器 —— 输入 sec 键，产出对齐 web 版式的 QWidget。

生成思路与 web 侧同源（交接件硬规矩）：
  web 侧控制台的配置行带 `data-cfg="点.path"`、控件类型写在 HTML 标签上；
  本文件不手抄任何配置项 —— 运行时从 `sec_meta.py`（解析 console_html.py）
  拿行元数据，按「键类型 → 控件类型」映射批量生成：
      text/password/number/range → QLineEdit（password 打码）
      checkbox                   → Switch（widgets 自绘开关）
      select                     → Combo（样式化下拉）
      textarea                   → 多行输入
      chips / info               → 说明行（原型不接群检测，如实说明）
  默认值按点路径回填自 config.example.json（web data-cfg 同源）。

非配置型面板（概览/体检/明细/日志/配置格式）在 panels_custom.py 手写，
build_panel 查 MANUAL 表分发。逻辑下沉，shell.py 只做装配。
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config_io
import panels_custom
import sec_meta
from stylekit_qt import Tokens, qfont, rgba
from widgets import Badge, Btn, Card, Field, Switch, desc, h2

# 本批要建面板的 26 个 sec（27 减去机器人=主面板），顺序照 web 文档序
BATCH_SECS = sec_meta.SECS_OF_THIS_BATCH


# ---------------------------------------------------------------- 徽章口径（丙-8 P0-A①）


def badge_for(sec: str, st: dict) -> tuple[str, str, str] | None:
    """面板徽章真值：sec 键 + 一份 /api/status → (level, text, tip)。

    口径逐条对齐 web `refreshBadges`（console_html.py L3302-3427）——同一份
    数据源（/api/status），同一套判断，同一套短文案（≤8 字）；细节进 tip。
    web 侧的差异如实记录：
      · stImggen/stVideogen/stTools/stVermat/stSessions/stMemory/stServer 七个
        id 在 web HTML 里没有元素（setSt 的 if(!el) return 兜住）＝ web 死代码，
        但判断分支是活的真口径 —— Qt 侧对应面板存在，照抄分支补齐；
      · stCheck/stMedia web 只建不更（HTML 初始值，JS 无分支）—— Qt 侧
        check 走检测动作更新（P0-A③），media 用同一份 status 聚合补齐；
      · stOverview/stLog/stSessions 由各自面板自刷新（overview 8s 定时、
        log 真读文件行数、sessions 拉 /api/sessions 计数），此处返回 None 跳过，
        防止两处双写打架。
    拿不到数据一律 idle「读取中」，绝不默认成 ok（widgets.Badge 纪律）。
    返回 None = 该 sec 无后台口径，徽章由面板本地语义管（已加载/已保存）。
    """
    if not isinstance(st, dict):
        return None
    paused = bool(st.get("paused"))
    wx_on = bool(st.get("wechat_connected"))
    wa = st.get("wechat_attach") or {}
    wa_short = wa.get("short") if isinstance(wa, dict) else ""

    if sec == "bot":
        # web stBot：paused ? warn「停着」 : ok「已就绪」
        return ("warn", "停着", "机器人已暂停，档位改了也不会生效；先恢复再改。") if paused \
            else ("ok", "已就绪", "档位与昵称改完保存即生效。")

    if sec == "wechat":
        # web stWechat：没连上 / 要勾群 / 已连接 · 监听 N 个
        lis = st.get("listen")
        if not wx_on:
            return ("err", "没连上",
                    "微信客户端没接上" + (f"：{wa_short}" if wa_short else "") + "；去看看下面的逐步诊断")
        if isinstance(lis, dict) and lis.get("groups") == 0 and lis.get("privates") == 0:
            return ("warn", "要勾群",
                    "微信接上了，但一个监听目标都没有——群里 @ 它也不会回。请在本页勾选要监听的群/人。")
        if isinstance(lis, dict) and isinstance(lis.get("groups"), int):
            n = (lis.get("groups") or 0) + (lis.get("privates") or 0)
            return ("ok", f"已连接 · 监听 {n} 个",
                    f"微信已接上，正在监听 {n} 个会话（群 {lis.get('groups') or 0} · 私聊 {lis.get('privates') or 0}）。")
        # web 在 _n 为 null 时会拼出「监听 null 个」——Qt 不抄这个 bug
        return ("ok", "已连接", "微信已接上（监听目标数读不到）。")

    if sec == "model":
        # web stModel：configured 是 bool 才给结论，否则 idle「读取中」
        mo = st.get("model")
        if isinstance(mo, dict) and isinstance(mo.get("configured"), bool):
            if mo["configured"]:
                name = str(mo.get("name") or "").strip()
                return ("ok", name[:10] or "已配置",
                        f"当前模型：{name or '（没记名字）'}；换厂商/换模型都在本页。")
            return ("err", "没填密钥", "还没填 API 密钥——模型不会工作。在本页填好密钥点保存即可。")
        return ("idle", "读取中", "这一版后台没给模型配置状态，填入后点保存即可。")

    if sec == "tts":
        t = st.get("tts")
        t = t if isinstance(t, dict) else {}
        if isinstance(t.get("ready"), bool):
            if t["ready"]:
                return ("ok", "可用", "语音合成可用（形态是音频文件，不是微信语音条）。")
            why = t.get("why") or ""
            return ("warn", "缺一步", "还没配好" + (f"：{why}" if why else "") + "，看本页第一段说明。")
        return ("idle", "读取中", "")

    if sec == "imggen":
        ig = st.get("image_gen") or st.get("imggen")
        ig = ig if isinstance(ig, dict) else {}
        if isinstance(ig.get("ready"), bool):
            if ig["ready"]:
                return ("ok", "可用", "群友说「画一张」时能生成并发出去。")
            why = ig.get("why") or ""
            return ("warn", "缺一步", "还没有可用的生图后端" + (f"：{why}" if why else "") + "，看本页说明怎么补。")
        return ("idle", "读取中", "")

    if sec == "videogen":
        vd = st.get("video_gen") or st.get("videogen")
        vd = vd if isinstance(vd, dict) else {}
        if isinstance(vd.get("ready"), bool):
            if vd["ready"]:
                return ("ok", "可用", "能做短视频并自动发出去（比图慢得多，几十秒到几分钟）。")
            why = vd.get("why") or ""
            return ("warn", "缺一步", "还没有可用的视频后端" + (f"：{why}" if why else "") + "。")
        return ("idle", "读取中", "")

    if sec == "search":
        # web stSearch：ready 是 bool 或给了 provider 才给结论；enabled!==false 算开
        we = st.get("web_search")
        we = we if isinstance(we, dict) else {}
        if isinstance(we.get("ready"), bool) or we.get("provider"):
            if we.get("enabled") is False:
                return ("idle", "关着", "联网搜索当前是关的。")
            p = str(we.get("provider") or "").strip()
            return ("ok", p[:8] or "已开", f"联网搜索走 {p or '默认引擎'}；换引擎在本页。")
        return ("idle", "读取中", "")

    if sec == "tools":
        tl = st.get("tools")
        tl = tl if isinstance(tl, dict) else {}
        cnt = tl.get("count")
        if isinstance(cnt, (int, float)):
            en = tl.get("enabled") or 0
            txt = f"{en} / {cnt} 开"
            if en > 0:
                return ("ok", txt, f"tools.d/ 里共 {int(cnt)} 个工具，已勾选启用 {int(en)} 个（只发 HTTP，不执行本地程序）。")
            return ("idle", txt, f"tools.d/ 里共 {int(cnt)} 个工具，一个都没启用。")
        return ("idle", "读取中", "")

    if sec == "vermat":
        # web stVermat：allow 三态（None=读不到 / 真=已实测·能发 / 假=拦停）
        vg = st.get("version_gate")
        vm = st.get("version")
        vg = vg if isinstance(vg, dict) else {}
        vm = vm if isinstance(vm, dict) else {}
        allow = vg.get("allow") if isinstance(vg.get("allow"), bool) else None
        ver = str(vm.get("wechat") or "").strip()
        vt = f"微信 {ver}" if ver and ver != "unknown" else "微信版本读不到"
        if allow is None:
            return ("idle", "读不到", f"{vt}：版本门读数读不到，不影响发送。")
        if allow:
            if vg.get("level") == "ok":
                return ("ok", "已实测", f"{vt}：这一版有实测记录，照常发送。")
            return ("info", "能发", f"{vt}：这一版没实测记录，但照常发送（不影响使用）。")
        return ("err", "拦停", f"{vt}：按严格档暂停发送。可在本页关掉 version_gate.strict。")

    if sec == "media":
        # web stMedia 死徽章（只建不更）—— Qt 补齐：按同一份 status 聚合三能力
        flags: list[bool] = []
        for k in ("tts", "image_gen", "imggen", "video_gen", "videogen"):
            v = st.get(k)
            if isinstance(v, dict) and isinstance(v.get("ready"), bool):
                flags.append(v["ready"])
        if not flags:
            return ("idle", "读取中", "")
        if all(flags):
            return ("ok", "可用", "语音 / 生图 / 视频都配好了。")
        return ("warn", "缺一步", "媒体能力有缺项：语音 / 生图 / 视频，看各页面第一段说明补齐。")

    if sec == "server":
        # web stServer：info「本机 端口」（web 用 location.port）
        from agent_bridge import current_url  # noqa: PLC0415
        u = current_url() or ""
        port = u.rsplit(":", 1)[-1].rstrip("/") if ":" in u else ""
        return ("info", f"本机 {port or '?'}",
                f"控制台只听本机（端口 {port or '?'}）；改监听地址或口令要重启控制台才生效。")

    return None   # overview/log/sessions/persona/memory 等：面板本地语义自己管


# ---------------------------------------------------------------- 基础控件


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


class Combo(QComboBox):
    """样式化下拉 —— 系统下拉在深色主题下很脏，QSS 全覆盖（shell._Combo 同款）。"""

    def __init__(self, t: Tokens, options: list[tuple[str, str]], default=None, parent=None):
        super().__init__(parent)
        self.t = t
        for v, label in options:
            self.addItem(label, v)      # userData=option 的真 value（web data-cfg 口径）
        values = [v for v, _l in options]
        if default is not None and str(default) in values:
            self.setCurrentIndex(values.index(str(default)))
        self.setFixedHeight(32)
        self.setMinimumWidth(200)
        self.setFont(qfont(t, t.body_size))
        self.setStyleSheet(
            f"QComboBox{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
            f"QComboBox::drop-down{{border:none;width:22px;}}"
            f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
            f"color:{t.tx};border:1px solid {t.bd};selection-background-color:{t.blue_soft};}}"
        )


def _line(t: Tokens, text: str = "", password: bool = False, placeholder: str = "") -> QLineEdit:
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size))
    e.setFixedHeight(32)
    e.setMinimumWidth(220)
    if password:
        e.setEchoMode(QLineEdit.EchoMode.Password)
    e.setStyleSheet(
        f"QLineEdit{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
        f"color:{t.tx};border:1px solid {t.bd};"
        f"border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QLineEdit:focus{{border:1px solid {t.blue};}}"
    )
    return e


def _area(t: Tokens, text: str, rows: int = 2, placeholder: str = "") -> QPlainTextEdit:
    e = QPlainTextEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size - 0.5))
    e.setFixedHeight(20 * max(2, min(rows, 4)) + 20)
    e.setStyleSheet(
        f"QPlainTextEdit{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:4px 8px;}}"
        f"QPlainTextEdit:focus{{border:1px solid {t.blue};}}"
    )
    return e


def _divider(t: Tokens) -> QFrame:
    d = QFrame()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background:{t.bd};border:none;")
    return d


def _as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        # 空容器回空串 —— 显示裸 "[]"/"{}" 不如让 placeholder 说明格式
        return json.dumps(v, ensure_ascii=False) if v else ""
    return str(v)


# 该行不参与保存（info 说明行 / 数字留空或格式不对）—— 与"没填就不改"的 web 口径一致
_SKIP = object()


def _ctrl_value(r: "sec_meta.Row", ctrl: QWidget | None):
    """按行类型从控件取值（web 保存前同款类型化：数字 parseFloat、开关 bool）。"""
    if ctrl is None or not r.cfg:
        return _SKIP
    if r.kind == "checkbox":
        return bool(ctrl.isChecked())
    if r.kind in ("number", "range"):
        s = ctrl.text().strip()
        if not s:
            return _SKIP                      # 留空 = 不改这一项
        try:
            f = float(s)
        except ValueError:
            return _SKIP                      # 格式不对不改，保存反馈里如实说
        return int(f) if f == int(f) else f
    if r.kind == "select":
        return str(ctrl.currentData())
    if r.kind == "textarea":
        return ctrl.toPlainText()
    return ctrl.text()


# ---------------------------------------------------------------- 行生成


def _row(t: Tokens, r: "sec_meta.Row", card: Card, binds: list | None = None) -> QWidget | None:
    """一行元数据 → 一行 Field。键类型 → 控件类型的唯一映射点。

    初值口径（丙-4 接线）：**真 config 当前值优先**（同进程 get_config，网页侧
    改过即拿到新值），键缺失才回 config.example.json 默认（sec_meta 解析层）。
    """
    cur = config_io.read_path(r.cfg) if r.cfg else None
    default = r.default if cur is None else cur
    c: QWidget | None = None
    if r.kind == "checkbox":
        c = Switch(t, bool(default))
    elif r.kind == "select":
        c = Combo(t, r.options, default)
    elif r.kind == "textarea":
        c = _area(t, _as_text(default), rows=3, placeholder=r.placeholder)
    elif r.kind in ("text", "password", "number", "range"):
        c = _line(t, _as_text(default), password=r.kind == "password", placeholder=r.placeholder)
    # 丙-10 P0-2：三新类型（web 里确有按钮组/状态行/表格，原来一律降级 info ⇒ 蒸发）
    elif r.kind == "buttons":
        c = _btn_group(t, r.actions)
    elif r.kind == "status":
        c = _status_chip(t, r.status_id)
    elif r.kind == "table":
        return _table_row(t, r, card)
    # info / chips → 纯说明行（不伪造一个接不了后台的控件）
    f = Field(t, r.label, r.hint, c, card)
    # 只有**带 cfg 的可写控件**才进 binds（status/buttons 无 cfg ⇒ 不参与保存；
    # 它们不是 QLineEdit，混进 binds 会在保存时被当输入框调 editingFinished）。
    if c is not None and binds is not None and r.cfg:
        binds.append((r, c))
    return f


def _btn_group(t: Tokens, actions: list[tuple[str, str]]) -> QWidget:
    """web `.row-btns` / 行内 `<button>` → 一串按钮（动作 id 挂 property 留取证）。

    ⚠️ 原型边界（如实）：这些按钮的动作原语在 web 侧（`onclick` JS），Qt 壳
    不冒充「点了会真跑」——点击走 `_btn_stub`，只做**可见反馈**（灰一档 + tooltip
    说明），绝不静默无反应（可用性要点：不存在"点了没反应"）。
    """
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    for txt, aid in actions:
        b = Btn(txt, t, role="ghost")
        b.setProperty("web_action", aid or "")
        b.setToolTip(("web 动作：" + aid) if aid else "web 侧按钮")
        b.clicked.connect(lambda _=False, _b=b: _btn_stub(_b))
        h.addWidget(b)
    return box


def _btn_stub(b) -> None:
    """Qt 壳里 web 动作按钮的点击反馈（原型边界：不假跑 web JS）。"""
    b.setEnabled(False)
    b.setText(b.text() + "（web 侧动作）")
    b.setToolTip("这个动作在 web 控制台执行；Qt 壳只做版式还原，不冒充已执行。")


def _status_chip(t: Tokens, status_id: str) -> QWidget:
    """web `<b id="…">` 状态位 → 只读状态标签（口径对齐丙-8 badge_for）。

    Qt 壳不接实时 /api/status 时就地读一次；读不到如实写「读不到」，绝不编数。
    """
    lab = QLabel("读取中")
    lab.setFont(qfont(t, t.body_size - 0.5))
    lab.setStyleSheet(f"color:{t.tx2};background:transparent;")
    lab.setProperty("web_status_id", status_id or "")
    try:
        st = _load_status()
        tip = _status_text_for(status_id, st)
    except Exception:  # noqa: BLE001
        tip = "读不到（控制台状态未就绪）"
    lab.setText(tip)
    return lab


def _table_row(t: Tokens, r: "sec_meta.Row", card: Card) -> QWidget:
    """web `<table>` → 表头 + 数据行（只读）。行标题在上，表体在下。"""
    from PySide6.QtWidgets import QGridLayout  # noqa: PLC0415

    wrap = QWidget(card)
    v = QVBoxLayout(wrap)
    v.setContentsMargins(0, 4, 0, 8)
    v.setSpacing(4)
    if r.label:
        lb = QLabel(r.label)
        lb.setFont(qfont(t, t.body_size, 500))
        lb.setStyleSheet(f"color:{t.tx};background:transparent;")
        v.addWidget(lb)
    if r.hint:
        v.addWidget(desc(t, r.hint))
    grid = QGridLayout()
    grid.setContentsMargins(0, 2, 0, 0)
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(3)
    for j, htxt in enumerate(r.headers):
        c = QLabel(htxt)
        c.setFont(qfont(t, t.body_size - 1.5, 500))
        c.setStyleSheet(f"color:{t.tx3};background:transparent;")
        grid.addWidget(c, 0, j)
    for i, row in enumerate(r.rows, start=1):
        for j, cell in enumerate(row):
            c = QLabel(cell)
            c.setFont(qfont(t, t.body_size - 1))
            c.setStyleSheet(f"color:{t.tx2};background:transparent;")
            c.setWordWrap(True)
            grid.addWidget(c, i, j)
    v.addLayout(grid)
    return wrap


_STATUS_CACHE: dict = { }


def _load_status() -> dict:
    """/api/status 快照（进程内缓存 3 秒，避免每行打一次网络）。"""
    now = time.time()
    if _STATUS_CACHE.get("t") and now - _STATUS_CACHE["t"] < 3.0:
        return _STATUS_CACHE.get("st") or {}
    st = {}
    try:
        import urllib.request  # noqa: PLC0415

        from console_html import PORT as _PORT  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        _PORT = 3210
    for p in (_PORT, 3210, 3211):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % int(p), timeout=1.2) as r:
                st = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception:  # noqa: BLE001
            continue
    _STATUS_CACHE["t"], _STATUS_CACHE["st"] = now, st
    return st


def _status_text_for(status_id: str, st: dict) -> str:
    """把 web 状态位 id 映射成本地可见的一行文字（读不到就如实说读不到）。"""
    if not st:
        return "读不到（控制台未就绪）"
    sid = status_id or ""
    if sid in ("wxver",):
        vm = st.get("version") or {}
        v = str(vm.get("wechat") or "").strip()
        return ("微信 " + v) if v and v != "unknown" else "微信版本读不到"
    if sid in ("vmVer",):
        vm = st.get("version") or {}
        v = str(vm.get("wechat") or "").strip()
        a = str(vm.get("adapter") or "").strip()
        return (("微信 %s × 适配层 %s" % (v, a or "-")) if v and v != "unknown" else "微信版本读不到")
    if sid in ("vsWhy", "ttsWhy"):
        for k in ("tts", "voice", "voice_models"):
            d = st.get(k)
            if isinstance(d, dict) and d.get("ready") is not None:
                return ("可用" if d.get("ready") else "不可用")
        return "读不到"
    return "读不到"


def _cursor_extras(t: Tokens, on_save) -> Card:
    """光标面板的「上传自定义图 / 重置默认」卡（web 同款文件语义）。"""
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1]
    card = Card(t)
    fb = QLabel("")
    fb.setFont(qfont(t, 12.5))
    fb.setStyleSheet(f"color:{t.tx3};background:transparent;")
    fb.setWordWrap(True)

    def _after(ok: bool, msg: str) -> None:
        fb.setText(("已生效：" if ok else "没生效：") + msg)
        fb.setStyleSheet(f"color:{t.ok if ok else t.err};background:transparent;")
        if ok and callable(on_save):
            try:
                on_save()
            except Exception:  # noqa: BLE001
                pass

    def _upload() -> None:
        p, _flt = QFileDialog.getOpenFileName(card, "选一张光标图", "", "图片 (*.png *.jpg *.jpeg)")
        if not p:
            return
        try:
            if Path(p).stat().st_size > 8 * 1024 * 1024:
                _after(False, "图片超过 8MB（web 同款上限），换小一点的")
                return
            shutil.copyfile(p, root / "assets" / "custom-cursor.png")
            ok, msg = config_io.write_patch({"ui.cursor_image": "custom", "ui.whale_cursor": True})
            _after(ok, msg + "（新光标立即生效，点头/中键旋转同样可用）")
        except Exception as e:  # noqa: BLE001
            _after(False, str(e))

    def _reset() -> None:
        try:
            for name in ("custom-cursor.png", "custom-cursor-nod.png"):
                (root / "assets" / name).unlink(missing_ok=True)
            ok, msg = config_io.write_patch({"ui.cursor_image": ""})
            _after(ok, (msg + "，已重置为默认鲸鱼") if ok else msg)
        except Exception as e:  # noqa: BLE001
            _after(False, str(e))

    row = QHBoxLayout()
    b_up = Btn("上传自定义光标图…", t, "ghost")
    b_up.clicked.connect(_upload)
    b_rs = Btn("重置为默认鲸鱼", t, "ghost")
    b_rs.clicked.connect(_reset)
    row.addWidget(b_up)
    row.addWidget(b_rs)
    row.addStretch(1)
    card.body.addWidget(h2(t, "自定义图片"))
    card.body.addLayout(row)
    card.body.addWidget(fb)
    return card


def _cfg_panel(t: Tokens, s: "sec_meta.Sec", on_save=None) -> QWidget:
    """配置型面板：头部 + 一张设置卡（行 × 分隔线）+ 保存行。

    保存口径（丙-4 接线）：收集本卡全部可编辑行的 {点路径: 值}，走
    `config_io.write_patch`（与网页控制台 /api/config 同款深合并落盘），
    成败都在行内如实反馈；on_save 供 Shell 做即时联动（光标/主题/画卷）。
    """
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    badge = Badge(t, "idle", "读取中")
    page._c8_badge = badge          # Shell._apply_badges 按此引用分发（P0-A①）
    lay.addWidget(h2(t, s.title, badge))
    if s.desc:
        lay.addWidget(desc(t, s.desc))

    card = Card(t)
    binds: list[tuple["sec_meta.Row", QWidget]] = []
    for i, r in enumerate(s.rows):
        if i:
            card.body.addWidget(_divider(t))
        f = _row(t, r, card, binds)
        if f is not None:
            card.body.addWidget(f)
    lay.addWidget(card)

    # 徽章本地语义（丙-8 P0-A①）：行初值来自真 config（_row 里 read_path），
    # 面板建好 = 配置已加载；保存成败在保存行如实反馈的同时同步到徽章。
    # 有后台口径的 sec（badge_for 返回非 None）会被 Shell 的 8s 轮询覆盖。
    badge.set("info", "已加载")

    # 光标面板专属（对齐 web /api/cursor/upload · /api/cursor/reset）：
    # 上传把图拷成 assets/custom-cursor.png 并把 ui.cursor_image 记成 custom；
    # 重置删掉自定义文件并清空配置键 —— 与 step-1 光标本体的回退链正好咬合。
    if s.key == "cursor":
        lay.addWidget(_cursor_extras(t, on_save))

    brow = QHBoxLayout()
    btn = Btn("保存设置", t, "primary")
    note = QLabel("改完点保存 → 写入 config.json（与网页控制台同一份）")
    note.setFont(qfont(t, 12.5))
    note.setStyleSheet(f"color:{t.tx3};background:transparent;")

    # ── 改完即生效（丙-8 P0-A④）── web 顶栏 autoApplyChk（console_html.py L753，
    # 默认勾选、状态记本机）的 Qt 等价：勾上后本面板任何控件改动，防抖 600ms
    # 自动 write_patch（与手动保存同一条深合并落盘链路），不用再点「保存设置」。
    from PySide6.QtCore import QSettings, QTimer as _QTimer  # noqa: PLC0415

    auto_chk = Switch(t, bool(QSettings("WXAgent", "persona-morph-ui")
                              .value("auto_apply", True, type=bool)))
    auto_chk.setToolTip("勾选＝改动后自动写入 config.json（不用点「保存设置」）；开关状态记在本机。")
    auto_lbl = QLabel("改完即生效")
    auto_lbl.setFont(qfont(t, 12))
    auto_lbl.setStyleSheet(f"color:{t.tx3};background:transparent;")

    def _collect() -> tuple[dict[str, object], int]:
        patch: dict[str, object] = {}
        skipped = 0
        for r, ctrl in binds:
            v = _ctrl_value(r, ctrl)
            if v is _SKIP:
                if ctrl is not None:
                    skipped += 1          # 有控件但留空/格式不对 → 计数如实说
                continue
            patch[r.cfg] = v
        return patch, skipped

    def _save_feedback(ok: bool, msg: str, skipped: int) -> None:
        if ok and skipped:
            msg += f"（{skipped} 行留空或格式不对，未改动）"
        note.setText(("已保存：" if ok else "没保存成：") + msg + f"  ·  {time.strftime('%H:%M:%S')}")
        note.setStyleSheet(f"color:{t.ok if ok else t.err};background:transparent;")
        if ok:
            badge.set("ok", "已保存")
        else:
            badge.set("err", "没保存成")

    def _do_save() -> None:
        patch, skipped = _collect()
        ok, msg = config_io.write_patch(patch)
        if ok:
            ok, msg = _verify_on_disk(patch, msg)   # 真读回 config.json 验证（非 mock）
        _save_feedback(ok, msg, skipped)
        if ok and callable(on_save):
            try:
                on_save()
            except Exception:  # noqa: BLE001
                pass

    def _verify_on_disk(patch: dict[str, object], msg: str) -> tuple[bool, str]:
        """丙-8 P0-A④ 落盘真验证：保存后重新打开 config.json 逐键比对，
        写进去了才算成功（write_patch 返回 ok 只代表「没报错」）。"""
        from pathlib import Path  # noqa: PLC0415

        from agent.config import CONFIG_FILE  # noqa: PLC0415

        try:
            disk = json.loads(Path(CONFIG_FILE).read_text(encoding="utf-8"))

            def _dig(d: dict, path: str):
                cur: object = d
                for part in path.split("."):
                    if not isinstance(cur, dict) or part not in cur:
                        return None, False
                    cur = cur[part]
                return cur, True

            miss = [k for k, v in patch.items()
                    if not _dig(disk, k)[1] or _dig(disk, k)[0] != v]
            if miss:
                return False, msg + f"（落盘验证没过：{','.join(miss[:3])} 未出现在 config.json）"
            return True, msg + "（已验证落盘）"
        except Exception as e:  # noqa: BLE001
            return False, msg + f"（落盘验证失败：{e}）"

    btn.clicked.connect(_do_save)

    # 自动生效：控件改动 → 防抖 → write_patch（web autoApplyOn 消费点同语义）
    debounce = _QTimer(page)
    debounce.setSingleShot(True)
    debounce.setInterval(600)

    def _auto_write() -> None:
        patch, skipped = _collect()
        if not patch:
            return
        ok, msg = config_io.write_patch(patch)
        if ok:
            ok, msg = _verify_on_disk(patch, msg)
        note.setText(("已自动生效：" if ok else "自动写入失败：") + msg + f"  ·  {time.strftime('%H:%M:%S')}")
        note.setStyleSheet(f"color:{t.ok if ok else t.err};background:transparent;")
        if ok:
            badge.set("ok", "已生效")
        else:
            badge.set("err", "没保存成")

    debounce.timeout.connect(_auto_write)

    def _mark_dirty() -> None:
        if auto_chk.isChecked():
            debounce.start()

    for r, ctrl in binds:
        if ctrl is None:
            continue
        if r.kind == "checkbox":
            ctrl.toggled.connect(_mark_dirty)
        elif r.kind == "select":
            ctrl.currentIndexChanged.connect(_mark_dirty)
        elif r.kind == "textarea":
            ctrl.textChanged.connect(_mark_dirty)
        else:                                    # text/password/number/range
            ctrl.editingFinished.connect(_mark_dirty)

    def _on_auto(on: bool) -> None:
        QSettings("WXAgent", "persona-morph-ui").setValue("auto_apply", bool(on))
        if on:
            note.setText("改完即生效：已开启（改动自动写入 config.json）")
            debounce.start()                 # 打开瞬间把当前面板值落一次
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
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 入口


def build_panel(t: Tokens, sec: str, on_save=None) -> QWidget:
    """sec 键 → 面板 QWidget（内容包在透明滚动区里，长面板可滚）。

    分发：web 侧非配置行形态的 5 个 sec（概览/体检/明细/日志/配置格式）
    走 panels_custom.MANUAL 手写件；其余全部元数据驱动生成。
    on_save：保存成功后的联动回调（Shell 传入，做光标/主题/画卷即时生效）。
    """
    fn = panels_custom.MANUAL.get(sec)
    if fn is not None:
        inner = fn(t, on_save) if sec == "json" else fn(t)
    else:
        inner = _cfg_panel(t, sec_meta.get(sec), on_save)
    wrap = QScrollArea()
    wrap.setWidgetResizable(True)
    wrap.setFrameShape(QFrame.Shape.NoFrame)
    wrap.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    wrap.setWidget(inner)
    wrap._c8_badge = getattr(inner, "_c8_badge", None)   # Shell._apply_badges 取用
    return wrap
