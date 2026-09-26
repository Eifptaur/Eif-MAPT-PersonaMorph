# -*- coding: utf-8 -*-
"""通用面板构建器 —— 输入 sec 键，产出对齐 web 版式的 QWidget。

  web 侧控制台的配置行带 `data-cfg="点.path"`、控件类型写在 HTML 标签上；
  本文件不手抄任何配置项 —— 运行时从 `sec_meta.py`（解析 assets/console/index.html）
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
from async_ui import run_async
from stylekit_qt import Tokens, pill, qfont, rgba
from widgets import Badge, Btn, Card, Field, Switch, desc, h2

# 本批要建面板的 26 个 sec（27 减去机器人=主面板），顺序照 web 文档序
BATCH_SECS = sec_meta.SECS_OF_THIS_BATCH


# ---------------------------------------------------------------- 徽章口径


def badge_for(sec: str, st: dict) -> tuple[str, str, str] | None:
    """面板徽章真值：sec 键 + 一份 /api/status → (level, text, tip)。

    口径逐条对齐 web `refreshBadges`（assets/console/index.html L3302-3427）——同一份
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
    拿不到数据一律 info「未检测」（中性灰，表示这版后台没给该字段），绝不默认成 ok（widgets.Badge 纪律）。
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
        # web stModel：configured 是 bool 才给结论，否则 info「未检测」（缺字段不伪装成读取中）
        mo = st.get("model")
        if isinstance(mo, dict) and isinstance(mo.get("configured"), bool):
            if mo["configured"]:
                name = str(mo.get("name") or "").strip()
                return ("ok", name[:10] or "已配置",
                        f"当前模型：{name or '（没记名字）'}；换厂商/换模型都在本页。")
            return ("err", "没填密钥", "还没填 API 密钥——模型不会工作。在本页填好密钥点保存即可。")
        return ("info", "未检测", "这一版后台没给模型配置状态，填入后点保存即可。")

    if sec == "tts":
        t = st.get("tts")
        t = t if isinstance(t, dict) else {}
        if isinstance(t.get("ready"), bool):
            if t["ready"]:
                return ("ok", "可用", "语音合成可用（形态是音频文件，不是微信语音条）。")
            why = t.get("why") or ""
            return ("warn", "缺一步", "还没配好" + (f"：{why}" if why else "") + "，看本页第一段说明。")
        return ("info", "未检测", "这一版后台没给语音合成就绪状态（tts.ready 缺失），本页操作不受影响。")

    if sec == "imggen":
        ig = st.get("image_gen") or st.get("imggen")
        ig = ig if isinstance(ig, dict) else {}
        if isinstance(ig.get("ready"), bool):
            if ig["ready"]:
                return ("ok", "可用", "群友说「画一张」时能生成并发出去。")
            why = ig.get("why") or ""
            return ("warn", "缺一步", "还没有可用的生图后端" + (f"：{why}" if why else "") + "，看本页说明怎么补。")
        return ("info", "未检测", "这一版后台没给生图后端就绪状态（image_gen.ready 缺失），本页操作不受影响。")

    if sec == "videogen":
        vd = st.get("video_gen") or st.get("videogen")
        vd = vd if isinstance(vd, dict) else {}
        if isinstance(vd.get("ready"), bool):
            if vd["ready"]:
                return ("ok", "可用", "能做短视频并自动发出去（比图慢得多，几十秒到几分钟）。")
            why = vd.get("why") or ""
            return ("warn", "缺一步", "还没有可用的视频后端" + (f"：{why}" if why else "") + "。")
        return ("info", "未检测", "这一版后台没给视频后端就绪状态（video_gen.ready 缺失），本页操作不受影响。")

    if sec == "search":
        # web stSearch：ready 是 bool 或给了 provider 才给结论；enabled!==false 算开
        we = st.get("web_search")
        we = we if isinstance(we, dict) else {}
        if isinstance(we.get("ready"), bool) or we.get("provider"):
            if we.get("enabled") is False:
                return ("idle", "关着", "联网搜索当前是关的。")
            p = str(we.get("provider") or "").strip()
            return ("ok", p[:8] or "已开", f"联网搜索走 {p or '默认引擎'}；换引擎在本页。")
        return ("info", "未检测", "这一版后台没给联网搜索就绪状态（web_search.ready/provider 缺失），本页操作不受影响。")

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
        return ("info", "未检测", "这一版后台没给工具清单计数（tools.count 缺失），本页操作不受影响。")

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
            return ("info", "读不到", f"{vt}：版本门读数读不到，不影响发送。")
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
            return ("info", "未检测", "这一版后台没给语音/生图/视频任一就绪状态，媒体能力暂未检测。")
        if all(flags):
            return ("ok", "可用", "语音 / 生图 / 视频都配好了。")
        return ("warn", "缺一步", "媒体能力有缺项：语音 / 生图 / 视频，看各页面第一段说明补齐。")

    if sec == "server":
        # web stServer：info「本机 端口」（web 用 location.port）
        from agent_bridge import current_url # noqa: PLC0415
        u = current_url() or ""
        port = u.rsplit(":", 1)[-1].rstrip("/") if ":" in u else ""
        return ("info", f"本机 {port or '?'}",
                f"控制台只听本机（端口 {port or '?'}）；改监听地址或口令要重启控制台才生效。")

    return None # overview/log/sessions/persona/memory 等：面板本地语义自己管


# ---------------------------------------------------------------- 基础控件


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


class Combo(QComboBox):
    """样式化下拉 —— 系统下拉在深色主题下很脏，QSS 全覆盖（shell._Combo 同款）。"""

    def __init__(self, t: Tokens, options: list[tuple[str, str]], default=None, parent=None):
        super().__init__(parent)
        self.t = t
        for v, label in options:
            self.addItem(label, v) # userData=option 的真 value（web data-cfg 口径）
        values = [v for v, _l in options]
        if default is not None and str(default) in values:
            self.setCurrentIndex(values.index(str(default)))
        self.setFixedHeight(32)
        # 宽度自适应：硬最小值只保「还看得清几个字」，不再用 200 把行顶死
        # （Field._STACK_AT 之下会改成上下排列，控件满宽）。
        self.setMinimumWidth(120)
        self.setFont(qfont(t, t.body_size))
        self.setStyleSheet(
            f"QComboBox{{background:{_hex(rgba(t.q('tx'), 16))};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{pill(32)}px;padding:0 10px;}}"
            f"QComboBox::drop-down{{border:none;width:22px;}}"
            f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
            f"color:{t.tx};border:1px solid {t.bd};selection-background-color:{t.blue_soft};}}"
        )


def _line(t: Tokens, text: str = "", password: bool = False, placeholder: str = "") -> QLineEdit:
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size))
    e.setFixedHeight(32)
    # 宽度自适应：见 Combo 注释 —— 120 是「还看得见值」的下限，不是版式常量；
    # 窄于 Field._STACK_AT 时该行会改成上下排列，输入框拿满宽（：
    # 「显示可以根据屏幕显示区域的宽度」自适应，这一条要覆盖所有输入控件）。
    e.setMinimumWidth(120)
    if password:
        e.setEchoMode(QLineEdit.EchoMode.Password)
    e.setStyleSheet(
        f"QLineEdit{{background:{_hex(rgba(t.q('tx'), 16))};"
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
        f"QPlainTextEdit{{background:{_hex(rgba(t.q('tx'), 16))};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_field}px;padding:4px 8px;}}"
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
            return _SKIP # 留空 = 不改这一项
        try:
            f = float(s)
        except ValueError:
            return _SKIP # 格式不对不改，保存反馈里如实说
        return int(f) if f == int(f) else f
    if r.kind == "select":
        return str(ctrl.currentData())
    if r.kind == "textarea":
        return _textish(r.cfg, ctrl.toPlainText())
    if r.kind == "chips":
        # web chips 存 list（如 group_name_white_list）—— 逗号/中文逗号分隔还原
        parts = [x.strip() for x in ctrl.text().replace("，", ",").split(",")]
        return [x for x in parts if x]
    return _textish(r.cfg, ctrl.text())


# 文本控件的「键级类型化」——与 web syncFromForm 的 path 特判**逐条对齐**。
# 不特判就会把「逗号分隔文本」或「JSON 文本」原样存成字符串：数组键存成字符串后，
# 读取方（如 risk.py 对 quiet_hours 判 isinstance(list,tuple)）静默失效。
_LIST_KEYS = (
    "store.keywords", "store.archive_block_chats", "store.tier_cmd_admins",
    "holiday.greet_chats", "api.fallback_models",
    "risk.block_keywords", "risk.watch_keywords",
)
_JSON_DICT_KEYS = ("api.model_prices", "store.group_blocklist")
_JSON_LIST_KEYS = ("store.tier_schedule.table",)


def _textish(cfg: str, raw: str):
    """文本 → 按 web 同款规则类型化（逗号切数组 / JSON 解析 / 原样字符串）。"""
    s = str(raw or "")
    if cfg in _LIST_KEYS:
        return [x.strip() for x in s.replace("，", ",").replace("\n", ",").split(",") if x.strip()]
    if cfg == "risk.quiet_hours":
        # 夜间静默时段 = 两个 0~23 的整数（起止小时）；不合规一律当"不启用"（与 web 同）
        try:
            parts = [int(x.strip()) for x in s.replace("，", ",").replace("；", ",").split(",") if x.strip()]
        except ValueError:
            return []
        return parts if len(parts) == 2 and all(0 <= x <= 23 for x in parts) else []
    if cfg in _JSON_LIST_KEYS or cfg in _JSON_DICT_KEYS:
        import json as _json # noqa: PLC0415

        want_list = cfg in _JSON_LIST_KEYS
        empty = [] if want_list else {}
        if not s.strip():
            return empty
        try:
            v = _json.loads(s)
        except ValueError:
            return empty # 非法 JSON 不改语义，退化为空（保存反馈会显示跳过的行数）
        if want_list:
            return v if isinstance(v, list) else empty
        return v if isinstance(v, dict) else empty
    return s


# ---------------------------------------------------------------- 行生成


def _row(t: Tokens, r: "sec_meta.Row", card: Card, binds: list | None = None) -> QWidget | None:
    """一行元数据 → 一行 Field。键类型 → 控件类型的唯一映射点。

    初值口径：**真 config 当前值优先**（同进程 get_config，网页侧
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
    # P0-2：三新类型（web 里确有按钮组/状态行/表格，原来一律降级 info ⇒ 蒸发）
    elif r.kind == "buttons":
        # 按钮组带行内 note —— 表内动作（testApi/wmReset/pokeTest…）真执行，
        # 结果回显；表外仍 stub。note 藏于按钮组下方，有结果才显示。
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        bnote = QLabel("")
        bnote.setFont(qfont(t, t.body_size - 1))
        bnote.setStyleSheet(f"color:{t.tx3};background:transparent;")
        bnote.setWordWrap(True)
        bnote.hide()
        v.addWidget(_btn_group(t, r.actions, bnote))
        v.addWidget(bnote)
        return wrap
    elif r.kind == "status":
        c = _status_chip(t, r.status_id)
    elif r.kind == "table":
        return _table_row(t, r, card)
    elif r.kind == "chips":
        # ⛔ chips 行（群白名单等）在 web 是「chips 组 + 行内按钮 + 添加输入」
        # 组合，原来不渲染任何控件 ⇒ 「选单都没有，用户怎么选」。
        #   ⇒ 可编辑文本（逗号分隔列表，保存时转 list）+ 行内按钮。
        c = _chips_editor(t, r)
    # info → 纯说明行（无控件语义）
    f = Field(t, r.label, r.hint, c, card)
    # 可写类型的控件都进 binds —— 带 cfg 的参与保存；无 cfg 的表单行
    # （feedback 的类型/内容/邮箱、community 导入 textarea 等）不参与保存
    # （_collect 侧按 r.cfg 跳过），进 binds 是给动作接线按 label 读同页值用。
    # ⚠️ 不能只看 `r.cfg` 判断可写：`chips` 可能带 cfg 却不是可编辑控件
    #    （混进保存会调 `editingFinished` ⇒ AttributeError），
    #    故仍须限制 `r.kind` 为**真正可写**的类型白名单。
    if c is not None and binds is not None and r.kind in _WRITABLE_KINDS:
        binds.append((r, c))
    return f


# **真正可写**的 row 类型白名单 —— 只有这几种的控件才进 binds、
# 才参与「改完即生效」的变更监听。其余（info/chips/status/buttons/table）
# 一律是**展示型**，既没有可写语义，其控件也不保证具备 `editingFinished` 等信号。
_WRITABLE_KINDS = frozenset({"text", "password", "number", "range", "checkbox", "select", "textarea", "chips"})


def _chips_editor(t: Tokens, r: "sec_meta.Row") -> QWidget:
    """web chips 勾选组（群白名单等）→ 可编辑输入 + 行内按钮。

    值语义对齐 web：list[str]（编辑框里逗号分隔展示，保存时 split 回 list）。
    「检测群聊并勾选 / 刷新群列表」按 web 原版真接线（assets/console/index.html:2806-2842）：
    点检测 → 拉 /api/wechat-groups → 弹窗勾选 → 确定 = 写回 + **当场保存**
    （GET /api/config → 改 wechat.group_name_white_list → POST 全量，web L2827 同款）。
    """
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    cur = config_io.read_path(r.cfg) if r.cfg else None
    if isinstance(cur, list):
        txt = ", ".join(str(x) for x in cur)
    elif cur:
        txt = str(cur)
    else:
        txt = ""
    line = _line(t, txt, placeholder=r.placeholder or "群 wxid / 群名，逗号分隔；留空=全部监听")
    v.addWidget(line)
    note = QLabel("")
    note.setFont(qfont(t, t.body_size - 1))
    note.setStyleSheet(f"color:{t.tx3};background:transparent;")
    note.setWordWrap(True)
    note.hide()
    if r.actions:
        hb = QWidget()
        hh = QHBoxLayout(hb)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(8)
        for txt2, aid in r.actions:
            b = Btn(txt2, t, role="ghost")
            b.setProperty("web_action", aid or "")
            if aid in ("pickGroups", "refreshGroups") and (r.cfg or "").endswith("group_name_white_list"):
                b.clicked.connect(lambda _=False, a=aid, _ln=line, _nt=note: _chips_group_action(a, _ln, _nt, t))
                b.setToolTip("对齐 web：弹窗列出检测到的群，勾选后确定即保存"
                             if aid == "pickGroups" else "重新读一次群列表（新加群/改群名/换号后用，不用重启）")
            else:
                b.setToolTip(("web 动作：" + aid) if aid else "web 侧按钮")
                b.clicked.connect(lambda _=False, _b=b: _btn_stub(_b))
            hh.addWidget(b)
        hh.addStretch(1)
        v.addWidget(hb)
    v.addWidget(note)
    return box


def _chips_group_action(aid: str, line, note, t: Tokens) -> None:
    """群白名单「检测群聊并勾选 / 刷新群列表」真实现（web 2806/2798 同款）。

    网络在后台线程（不冻 UI），弹窗与落地在主线程。失败/0 群如实说（web 同款三选一口径）。
    超时对齐 web getJSON 默认 30s：refresh 要重读微信联系人库（微信占用时会慢），
    原 8s 会在后端还没回话时先被掐断 → 看起来像「挂不上控制台」。
    """
    note.show()
    note.setText("正在刷新群列表（重读联系人库，微信占用时会慢，最长等 30 秒）…"
                 if aid == "refreshGroups" else "检测群聊中…")

    def _work(box: dict) -> None:
        try:
            box["val"] = config_io.get_json(
                "/api/wechat-groups" + ("?refresh=1" if aid == "refreshGroups" else ""),
                timeout=30.0, err_box=box)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)

    def _apply(box: dict) -> None:
        rsp = box.get("val")
        if not isinstance(rsp, dict):
            err = box.get("err") or ""
            if "timed out" in err or "timeout" in err.lower():
                note.setText("等结果超时（30 秒）。微信正占着联系人库或群太多时会这样，"
                             "稍等再点一次；反复出现就重启微信/控制台再试。")
            else:
                note.setText("检测失败：" + (err or "后台没连上（控制台没起来？"
                                         "先看「运行状态」那行是不是「已连接」）"))
            return
        if rsp.get("ok") is False:
            note.setText(str(rsp.get("error") or "读不到群列表（微信可能还没接上）"))
            return
        groups = rsp.get("groups") or []
        if aid == "refreshGroups":
            note.setText("已重读：这台机器上读到 %d 个群聊" % len(groups)
                         + ("" if groups else "（确认微信登录的是你要的那个号、且那个号里有群）"))
            return
        if not groups:
            note.setText("这台机器上读到 0 个群聊。请依次确认：①微信登录的是你要用的那个号 "
                         "②那个号里确实有群 ③「运行状态」那行写的是「已连接」")
            return
        _open_group_pick(t, line, note, groups)

    run_async(_work, _apply, page=note.window(), interval=150, name="chips-groups")


def _open_group_pick(t: Tokens, line, note, groups: list) -> None:
    """web「选择监听的群」弹窗的 Qt 版 —— 主题化（confirm.ConfirmDialog 同款设计语言）。

    无边框卡片壳 + 半透明背景 + 项目 Btn/Switch 组件 + 父窗口居中；
    必须模态 exec()（web 勾选层挂到用户点确定/取消为止）。
    """
    from PySide6.QtWidgets import ( # noqa: PLC0415
        QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout,
    )

    cur = {x.strip() for x in line.text().replace("，", ",").split(",") if x.strip()}

    def _gid(g) -> tuple[str, str]:
        """群项 → (显示文案, 唯一 id)。兼容 str 与 {name, wxid/id} 两种结构。"""
        if isinstance(g, dict):
            name = str(g.get("name") or g.get("nick") or "")
            wid = str(g.get("wxid") or g.get("id") or g.get("username") or name)
            return ("%s（%s）" % (name, wid) if name and wid != name else (name or wid)), wid
        s = str(g)
        return s, s

    def _preset(wid: str, text: str) -> bool:
        """预开判定：已存词精确命中 wxid，或词是「群名（wxid）」显示串的子串
        （兼容手填群名/部分名 —— 原来只整串匹配，手填词永远预开不了）。"""
        if wid in cur:
            return True
        return any(w and (w in text) for w in cur)

    # ── 壳：无边框 + 半透明 + 卡片 QFrame（confirm.py 同款三件套）
    from widgets import drag_dialog_cls # noqa: PLC0415

    dlg = drag_dialog_cls("PickGroupsDialog")(line.window())
    dlg.setWindowTitle("选择监听的群")
    dlg.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.setModal(True)
    dlg.resize(460, 520)
    dlg.enable_drag() # 可拖动

    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(0, 0, 0, 0)
    card = QFrame()
    card.setObjectName("GpCard")
    card.setStyleSheet(
        f"#GpCard{{background:{t.card};border:1px solid {t.bd};"
        f"border-radius:{t.radius_card + 2}px;}}")
    outer.addWidget(card)

    v = QVBoxLayout(card)
    v.setContentsMargins(24, 22, 24, 18)
    v.setSpacing(12)

    head = QLabel("选择监听的群")
    head.setFont(qfont(t, 16, 600))
    head.setStyleSheet(f"color:{t.tx};background:transparent;")
    v.addWidget(head)

    sub = QLabel("检测到 %d 个群聊，打开右侧开关选择机器人要监听的群"
                 "（全关=监听所有群）。" % len(groups))
    sub.setFont(qfont(t, t.body_size))
    sub.setWordWrap(True)
    sub.setStyleSheet(f"color:{t.tx2};background:transparent;")
    v.addWidget(sub)

    lst = QListWidget()
    lst.setStyleSheet(
        f"QListWidget{{background:{rgba(t.q('tx'), 10).name(QColor.NameFormat.HexArgb)};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_card}px;padding:4px;}}"
        f"QListWidget::item{{border-radius:{t.radius_btn}px;}}"
        f"QListWidget::item:selected{{background:{rgba(t.q('blue'), 30).name(QColor.NameFormat.HexArgb)};}}")
    v.addWidget(lst, 1)

    # 每行：群名 + 自绘开关（Switch 是项目自绘组件，状态色随主题）
    rows: list[tuple[str, Switch]] = []
    for g in groups:
        text, wid = _gid(g)
        row = QWidget()
        rh = QHBoxLayout(row)
        rh.setContentsMargins(10, 8, 12, 8)
        rh.setSpacing(10)
        nm = QLabel(text)
        nm.setFont(qfont(t, t.body_size))
        nm.setStyleSheet(f"color:{t.tx};background:transparent;")
        rh.addWidget(nm, 1)
        sw = Switch(t, on=_preset(wid, text))
        rh.addWidget(sw)
        it = QListWidgetItem()
        it.setSizeHint(row.sizeHint())
        lst.addItem(it)
        lst.setItemWidget(it, row)
        rows.append((wid, sw))

    brow = QHBoxLayout()
    brow.setContentsMargins(0, 2, 0, 0)
    brow.setSpacing(10)
    brow.addStretch(1)
    cancel = Btn("算了", t, "ghost")
    ok = Btn("确定", t, "primary")
    brow.addWidget(cancel)
    brow.addWidget(ok)
    v.addLayout(brow)
    cancel.clicked.connect(dlg.reject)

    def _ok() -> None:
        picked = [wid for wid, sw in rows if sw.isChecked()]
        line.setText(", ".join(picked))
        note.show()
        note.setText("保存群白名单中…（%d 个群）" % len(picked))
        dlg.accept()

        def _work(bx: dict) -> None:
            try:
                cfg = config_io.get_json("/api/config", timeout=8.0) or {}
                if isinstance(cfg, dict):
                    cfg.setdefault("wechat", {})["group_name_white_list"] = list(picked)
                    from agent_bridge import post_json # noqa: PLC0415

                    bx["rsp"] = post_json("/api/config", cfg, timeout=10.0)
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)

        def _apply_save(bx: dict) -> None:
            note.setText("已保存群白名单（%d 个群）" % len(picked)
                         if bx.get("err") is None else "群白名单保存失败：" + str(bx["err"]))

        run_async(_work, _apply_save, page=note.window(), interval=150, name="chips-save")

    ok.clicked.connect(_ok)

    # 居中于父窗口（confirm.ConfirmDialog 同款；Frameless 无系统标题栏不会自动居中）
    par = line.window()
    if par is not None and par.isVisible():
        g2 = par.frameGeometry()
        dlg.move(g2.center() - dlg.rect().center())

    # 必须模态 exec()：web 同款勾选层挂到用户点确定/取消为止；
    #   show() 是非模态——函数立即返回、局部 dlg 失引用被回收，弹窗闪现即销毁，
    #   且 note 停在「检测群聊中…」→ 表现为「点了没反应、无休止的卡」。
    dlg.exec()


def _open_export_dir_body() -> dict:
    """openExportDir 的请求体：路径取当前 community.export_dir（空回退 exports）。"""
    d = config_io.read_path("community.export_dir")
    d = str(d).strip() if d else ""
    return {"path": d or "exports"}


# ── 按钮动作分发器（web onclick 的 Qt 等价）──
# aid → POST 端点。命中 = 真执行（后台线程 + 结果回显行内 note）；
# 未命中 = 沿 _btn_stub 原型边界。web 62 个按钮按批次逐步接线进这张表。
_ACT_API: dict[str, tuple] = {
    "testApi": ("POST", "/api/test-api", {}), # web L2741/L5882：后端自测当前 api 配置
    "wmReset": ("POST", "/api/watermark/reset", {}), # web L2709：监听水位重对齐
    "pokeTest": ("POST", "/api/poke-test", {"verify_only": True}), # web 默认勾「简易检测」：只验证菜单可弹，不真拍（完整执行走检测中心页）
    "igTest": ("GET", "/api/image_gen/test", {}), # web L7675：生图链条只跑不发
    "ttsTest": ("GET", "/api/tts/test", {}), # web L7691：按当前档合成试听
    "vsTest": ("GET", "/api/voice/test", {}), # web L7707：语音链路（合成→SILK→识别）
    "selfCheck": ("POST", "/api/selfcheck", {"mode": "full"}), # web L6004：61 项环境体检
    # ── community / feedback──
    "exportHolyshits": ("POST", "/api/community/export", {"kind": "holyshits"}), # web L7993：导出金句
    "exportFeedback": ("POST", "/api/community/export", {"kind": "feedback"}), # web L8007：导出意见反馈
    "exportMessages": ("POST", "/api/community/export", {"kind": "messages"}), # web L8008：导出聊天记录
    "openSeedBtn": ("POST", "/api/open-path", {"path": "data/seed_library.json"}), # web L7676：资源管理器打开种子库
    "openExportDir": ("POST", "/api/open-path", _open_export_dir_body), # web L8000：打开导出目录（callable body 读当前配置）
    "fbFlush": ("POST", "/api/feedback/flush", {}), # web L7361：补发积压的反馈
}


def _act_run(aid: str, note) -> None:
    """真执行 aid（后台线程，按表内方法 GET/POST），结果回显 note（主线程落地）。"""
    spec = _ACT_API.get(aid)
    if not spec or note is None:
        return
    method, api, body = spec
    note.show()
    note.setText("执行中…（环境体检/链路测试可能要十几秒）")

    def _work(bx: dict) -> None:
        try:
            if method == "POST":
                from agent_bridge import post_json # noqa: PLC0415

                # body 允许是 callable（如 openExportDir：点击时才读当前配置拼路径）
                bx["rsp"] = post_json(api, body() if callable(body) else body, timeout=90.0)
            else:
                bx["rsp"] = config_io.get_json(api, timeout=60.0)
        except Exception as e: # noqa: BLE001
            bx["err"] = str(e)

    def _apply(bx: dict) -> None:
        if bx.get("err"):
            note.setText("%s 失败：%s" % (aid, bx["err"]))
            return
        rsp = bx.get("rsp") if isinstance(bx.get("rsp"), dict) else {}
        if aid == "testApi":
            note.setText(("测试连通成功（%sms）" % rsp.get("latency_ms")) if rsp.get("ok")
                         else ("测试失败：%s" % (rsp.get("error") or "未知原因")))
        elif aid == "pokeTest":
            if rsp.get("ok"):
                msg = str(rsp.get("message") or "完成")
                tgt = rsp.get("target") or {}
                if isinstance(tgt, dict) and tgt.get("name"):
                    msg += "（%s / %s）" % (tgt.get("name"), tgt.get("id"))
                steps = [str(s) for s in (rsp.get("steps") or []) if s]
                if steps:
                    msg += " ｜ " + " -> ".join(steps[:6])
                note.setText(msg)
            else:
                note.setText("拍一拍检测失败：%s" % (rsp.get("error") or "未知"))
        elif aid == "igTest":
            res = rsp.get("result") or {}
            note.setText(str(res.get("why") or "链条跑通") if rsp.get("ok")
                         else str(res.get("why") or rsp.get("error") or rsp.get("err") or "未知原因"))
        elif aid == "ttsTest":
            info = rsp.get("info") or {}
            if rsp.get("ok"):
                # 体验增强（web 只显示路径）：拿到产物后用系统默认播放器自动播放——
                # ；合成文件就在磁盘上，一键可听。播放失败不影响结果回显。
                try:
                    import os as _os # noqa: PLC0415

                    if rsp.get("path"):
                        _os.startfile(str(rsp.get("path")))
                except Exception: # noqa: BLE001
                    pass
                note.setText("合成成功（已调用系统播放器试听）：%s（%s / %s 字节 / 档位：%s / 声音：%s）"
                             % (rsp.get("path"), info.get("fmt") or "-", rsp.get("size"),
                                rsp.get("engine") or info.get("engine") or "-", info.get("voice") or "-"))
            else:
                note.setText("合成失败：%s" % (rsp.get("err") or rsp.get("error") or "未知原因"))
        elif aid == "vsTest":
            res = rsp.get("result") or {}
            lines = " ｜ ".join("%s：%s" % (st.get("name"), st.get("detail"))
                                for st in (res.get("steps") or []) if isinstance(st, dict))
            note.setText(("链路可用。" if rsp.get("ok") else "链路跑不通。") + lines
                         + (" ｜ 识别到：" + str(res.get("text")) if res.get("text")
                            else (" ｜ " + str(res.get("err")) if res.get("err") else "")))
        elif aid == "selfCheck":
            # selfcheck 返回项是 {status: ok/warn/fail, name, detail, hint} 形状 ——
            # 旧实现按 c.get("ok") is False 统计 ⇒ 恒「失败 0」误导；按 status 口径改。
            checks = [c for c in (rsp.get("checks") or []) if isinstance(c, dict)]
            if not checks:
                note.setText(str(rsp.get("summary") or "完成"))
            else:
                fails = [c for c in checks if c.get("status") == "fail"]
                warns = [c for c in checks if c.get("status") == "warn"]
                head = "共 %d 项，未通过 %d，注意 %d" % (len(checks), len(fails), len(warns))
                det = "；".join("%s：%s" % (c.get("name"), c.get("detail")) for c in fails[:3])
                tail = str(rsp.get("summary") or "")
                note.setText(head + ((" —— " + det) if det else "")
                             + ((" ｜ " + tail) if tail else ""))
        elif aid in ("exportHolyshits", "exportFeedback", "exportMessages"):
            if rsp.get("ok"):
                note.setText("已导出 %s 条 → %s" % (rsp.get("count"), rsp.get("path")))
            else:
                note.setText("失败：%s" % (rsp.get("error") or ""))
        elif aid == "openSeedBtn":
            note.setText("已打开种子库（编辑后保存即可生效）" if rsp.get("ok")
                         else "打开失败：%s" % (rsp.get("error") or ""))
        elif aid == "openExportDir":
            note.setText("已打开导出文件夹" if rsp.get("ok")
                         else "打开失败：%s" % ((rsp.get("error") or "") + (rsp.get("note") or "")))
        elif aid == "fbFlush":
            note.setText(str(rsp.get("why") or "补发完成"))
        else:
            note.setText(str(rsp.get("note") or rsp.get("summary") or "完成"))

    run_async(_work, _apply, page=note.window(), interval=150, name="act-" + aid)


def _code_check_run(note, deps: bool) -> None:
    """代码检测（web runCodeCheck :5893-5945 同款）：POST 启动 → 轮询 progress → 逐项落地。

    轮询在后台线程（300 次×间隔，约 45s 上限）；note 只在主线程读共享 dict，不冻 UI。
    """
    note.show()
    note.setText("代码检测启动中…")

    def _work(bx: dict) -> None:
        try:
            from agent_bridge import post_json # noqa: PLC0415

            post_json("/api/code-check", {"deps": bool(deps)}, timeout=20.0)
            for _i in range(300):
                pr = post_json("/api/code-check/progress", {}, timeout=10.0)
                if not isinstance(pr, dict):
                    continue
                if pr.get("done"):
                    r = pr.get("result") or {}
                    lines = [r.get("summary") or "代码检测完成"]
                    for c in (r.get("checks") or []):
                        mark = {"ok": "通过", "warn": "注意", "fail": "未通过"}.get(c.get("status"), "信息")
                        lines.append("%s %s：%s" % (mark, c.get("name"), c.get("detail")))
                        if c.get("hint"):
                            lines.append("    建议：" + str(c.get("hint")))
                    bx["line"] = "\n".join(lines)[:600]
                    return
                prg = pr.get("progress") or {}
                d, tt = prg.get("done") or 0, prg.get("total") or 0
                cur = prg.get("current") or ""
                items = pr.get("items") or []
                line = "检测中 %d%% · %s" % (round(d / tt * 100) if tt else 0, cur)
                if items and isinstance(items[-1], dict):
                    line += " ｜ 最新：" + str(items[-1].get("name"))
                bx["line"] = line
        except Exception as e: # noqa: BLE001
            bx["err"] = str(e)

    def _tick(bx: dict) -> None:
        note.setText(str(bx.get("line") or ""))

    def _apply(bx: dict) -> None:
        if bx.get("err"):
            note.setText("代码检测失败：" + str(bx["err"]))
            return
        note.setText(str(bx.get("line") or ""))

    # 后台那个循环自己就是 45 秒左右的上限，界面按 300ms 收进度即可
    run_async(_work, _apply, page=note.window(), interval=300, name="code-check", on_tick=_tick)


def _btn_group(t: Tokens, actions: list[tuple[str, str]], note=None) -> QWidget:
    """web `.row-btns` / 行内 `<button>` → 一串按钮（动作 id 挂 property 留取证）。

    ⚠️ 原型边界：在 `_ACT_POST` 表里的动作**真接后端**（后台线程，
    结果回显行内 note）；表外的仍走 `_btn_stub`（可见反馈 + tooltip，绝不静默无反应）。
    """
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    # 自定义动作（panels_custom.ACT_CUSTOM：要读同页表单/带确认框/多态回显的按钮）
    custom = getattr(panels_custom, "ACT_CUSTOM", {}) or {}
    for txt, aid in actions:
        b = Btn(txt, t, role="ghost")
        b.setProperty("web_action", aid or "")
        if note is not None:
            b.setProperty("c8_note", note) # 行内回显引用（custom 动作与自检断言共用）
        fn = custom.get(aid)
        if fn is not None:
            handler, tip = fn
            b.clicked.connect(lambda _=False, f=handler, bb=b, nn=note: f(bb, nn))
            b.setToolTip(tip)
        elif note is not None and aid in ("codeCheck", "codeCheckDeps"):
            b.clicked.connect(lambda _=False, a=aid: _code_check_run(note, a == "codeCheckDeps"))
            b.setToolTip("真接后端：/api/code-check（启动 → 轮询进度 → 逐项结果 + 建议）")
        elif note is not None and aid in _ACT_API:
            b.clicked.connect(lambda _=False, a=aid: _act_run(a, note))
            b.setToolTip("真接后端：" + _ACT_API[aid][1])
        else:
            b.setToolTip(("web 动作：" + aid) if aid else "web 侧按钮")
            b.clicked.connect(lambda _=False, _b=b: _btn_stub(_b))
        h.addWidget(b)
    return box


def _btn_stub(b) -> None:
    """Qt 壳里 web 动作按钮的点击反馈（原型边界：不假跑 web JS）。

    ⛔ 不再永久改按钮文字——连点几次会把「（web 侧动作）」叠成长串（用户截图实锤：
    「用这个（web 侧动作）」）。改为弹一次说明框，说清动作 id 与去向；按钮原样保留。
    """
    from PySide6.QtWidgets import QMessageBox # noqa: PLC0415

    _aid = str(b.property("web_action") or "")
    msg = ("动作 id：%s\n" % _aid if _aid else "") + \
        "这个动作的原生实现还没进桌面壳，请到网页控制台操作（顶栏可「去网页」）。"
    QMessageBox.information(b, "这个动作在网页控制台", msg)


_STATUS_ROWS: list = [] # 面板内 status 行注册表 [(_Weak)<label>, status_id]——跟随全局轮询刷新

_APPENDIX_REFRESHERS: list = [] # 附录卡自刷新回调 [[回调, 存活判据控件]]——跟随全局轮询


def _alive(w) -> bool: # noqa: ANN001
    """C++ 侧对象还在不在。

    ⛔ 为什么非判不可：面板被重建/关闭后，注册表里的 QLabel/QWidget 只是 Python
    侧还活着（被 list 引用），其 **C++ 对象已被析构**——`setText` 抛
    `RuntimeError: Internal C++ object already deleted`。更坏的是附录卡的刷新
    闭包：它一进去就先发一趟 `config_io.get_json("/api/status", timeout=10.0)`，
    对象早死了它**照样把 10 秒的阻塞请求打完**才去碰死控件。
    面板建一次注册一份、注册表从不清空 ⇒ 跑一长串用例会攒出几十个死闭包，
    8 秒轮询变成几十趟 10 秒阻塞串烧，事件循环再也空不下来（全量自检偶发挂死的真凶）。

    判活用 `shiboken6.isValid`；不可用时退回「调一个无害 getter 看抛不抛 RuntimeError」。
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


def _register_prune(reg: list, entry, anchor_idx: int = -1) -> None: # noqa: ANN001
    """注册并顺手清理已死条目（防止注册表随面板重建无限增长）。

    `anchor_idx` 指出条目里「哪个位置是存活判据控件」（status 行是 `[0]`＝label，
    附录卡是 `[1]`＝anchor 控件）。同时按回调/控件身份去重：同一张卡重复注册
    只留一份，避免闭包被反复追加（每追加一份就是每轮多一趟阻塞请求）。
    """
    reg[:] = [e for e in reg if _alive(e[anchor_idx])]
    for e in reg:
        if e[0] == entry[0]:
            return
    reg.append(entry)


def register_appendix_refresh(fn, anchor=None) -> None: # noqa: ANN001
    """把一张附录卡的自刷新回调挂进全局轮询（`refresh_status_rows` 会一并调用）。

    为什么要这条：附录卡原来多是 `QTimer.singleShot(N, _refresh)` **只拉一次**——
    控制台若在开页之后才起来（很常见：先开 UI 再点启动），这些卡就永远停在开页
    那一瞬的结论（用户看到「还是检测中」的一大来源）。挂进来即可随 8 秒轮询复活。

    `anchor` 是这张卡的**存活判据控件**（通常是卡里的状态标签）：控件一死，
    本回调即从注册表摘除，不再被轮询调用（否则死回调会继续打 10 秒阻塞请求，
    见 `_alive` 的长注解）。调用方省略 anchor 时退化为「永不摘除」——不推荐。
    """
    if not callable(fn):
        return
    _register_prune(_APPENDIX_REFRESHERS, [fn, anchor], anchor_idx=1)


def refresh_status_rows(st) -> None:
    """全局状态刷新时同步刷新面板内 status 行。

    只喂 `st`（调用方已拿到的 /api/status），**本函数自己不发请求** ——
    附录卡的刷新回调自带取数，故只在有真值那趟催它们（读不到时不打扰）。
    每趟先剪掉已死控件/回调，注册表不随面板重建膨胀。
    """
    if not isinstance(st, dict) or not st:
        return

    rows = [(lab, sid) for lab, sid in _STATUS_ROWS if _alive(lab)]
    if len(rows) != len(_STATUS_ROWS):
        _STATUS_ROWS[:] = rows
    for lab, sid in rows:
        try:
            lab.setText(_status_text_for(sid, st))
        except Exception: # noqa: BLE001
            pass

    # 附录卡自刷新（媒体组件等）：拿到真值那趟才催它们，读不到时不打扰。
    # 先按 anchor 剪掉死卡 —— 死掉的那份连请求都不该再发（10s 阻塞）。
    live = [e for e in _APPENDIX_REFRESHERS if _alive(e[1])]
    if len(live) != len(_APPENDIX_REFRESHERS):
        _APPENDIX_REFRESHERS[:] = live
    for fn, _anchor in live:
        try:
            fn()
        except Exception: # noqa: BLE001
            pass


def _status_chip(t: Tokens, status_id: str) -> QWidget:
    """web `<b id="…">` 状态位 → 只读状态标签。

    构建时就地读一次（后端活着即刻有值）；并注册进 `_STATUS_ROWS`，
    之后跟随 8 秒全局轮询持续刷新。

    ⛔ 「页面探到值了、状态位还写检测中」的根因有两层：
      ① 旧口径只读 _load_status() 的**进程内 3 秒缓存**——面板比状态缓存晚建、
         或上一趟是失败结果，就地读到的就是空 ⇒ 停在占位文案；
      ② 只挂 8 秒轮询 ⇒ 用户看到的那几秒恒为占位。
    现在改 async-refresh：立刻按 status_id 判**是否需要哪些数据段**下发一次
    真请求（命中进程缓存则同步即刻落地），回来必回调一次落字。占位只活到
    第一次回调，不再是「永远检测中」。
    """
    lab = QLabel("检测中…")
    lab.setFont(qfont(t, t.body_size - 0.5))
    lab.setStyleSheet(f"color:{t.tx2};background:transparent;")
    lab.setProperty("web_status_id", status_id or "")
    # 注册即剪：面板重建时旧标签的 C++ 对象已死，但 Python 侧还被本表引用 ——
    # 不剪的话本表随建页次数线性涨，每轮轮询都要对一堆死 QLabel 调 setText
    # （抛 RuntimeError 被吞，纯开销）。见 refresh_status_rows 的剪枝。
    _register_prune(_STATUS_ROWS, [lab, status_id or ""], anchor_idx=0)

    # ① 命中进程缓存的快路径：不阻塞建页，拿得到就立刻落字
    try:
        st = _load_status()
        if st:
            lab.setText(_status_text_for(status_id, st))
    except Exception: # noqa: BLE001
        pass

    # ② 真拉一次：按本状态位实际需要的数据段取数（status_id 缺省 ⇒ 整份）
    _fetch_status_async(set(_status_segments(status_id or "")), lab, status_id or "")
    return lab


# 后端 `/api/status?only=` 认识的段名（webui._rapi_status 富化段清单）。
# 本表只用来判「这个段名后端认不认」——不认就别发这一趟（老版本后端会 400）。
# 段名以 web 侧 loadStatus 的取数范围为准（同一份 /api/status 推导）。
_STATUS_SEGMENTS_INSTALLED = {
    "wechat_version", "version", "version_gate", "media", "video_read",
    "user_tools", "input", "ui_fp", "pending_decisions", "jobs",
}

_STATUS_SEGMENTS = {
    "wxver": "wechat_version,version",
    "vmVer": "version",
    "vmGate": "version_gate,version",
    "vmDec": "version",
    "vsWhy": "media",
    "ttsWhy": "media",
    "videoStat": "video_read,media",
    "igWhy": "media",
    "utGlobals": "user_tools",
    "bgHead": "input",
    "ufpHead": "ui_fp",
    "pdStat": "pending_decisions",
    "actStat": "jobs",
}


def _status_segments(status_id: str) -> str:
    """该状态位要用到的段名（空串 = 整份 /api/status）。"""
    return _STATUS_SEGMENTS.get(status_id, "")


def _fetch_status_async(segments: set[str], lab, status_id: str) -> None:
    """后台拉一次状态段 → 主线程回填标签。

    失败也回调一次：把「读不到」如实写上，**不留占位**（占位=「还在检测」是
    最误导的一种文案——它承诺后面会变，而其实这条路根本没接上）。
    """
    if segments and not segments <= _STATUS_SEGMENTS_INSTALLED:
        return # 后端不认这个段名（老版本）→ 交给整份状态的常规轮询，不额外打请求
    box: dict = {"done": False, "st": None, "err": None}
    q = ("?only=" + ",".join(sorted(segments))) if segments else ""

    def _work() -> None:
        st, err = None, None
        try:
            import urllib.request # noqa: PLC0415

            from addr import join_url # noqa: PLC0415
            from agent_bridge import current_url # noqa: PLC0415

            base = current_url()[0] if isinstance(current_url(), tuple) else current_url()
            req = urllib.request.Request(join_url(base, "/api/status" + q),
                                         headers={"Accept": "application/json"})
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(req, timeout=2.5) as r:
                st = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e: # noqa: BLE001
            err = str(e)
            for p in (3210, 3211):
                try:
                    with urllib.request.urlopen(
                            "http://127.0.0.1:%d/api/status%s" % (int(p), q), timeout=2.5) as r:
                        st = json.loads(r.read().decode("utf-8", "replace"))
                    err = None
                    break
                except Exception: # noqa: BLE001
                    continue
        box["st"], box["err"], box["done"] = st, err, True

    try:
        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, name="status-chip").start()
    except Exception: # noqa: BLE001
        return

    def _apply() -> None:
        # 控件已死（页面被重建/关页）⇒ 立刻退出，既不回写也不继续自链
        if not _alive(lab):
            return
        if not box["done"]:
            # ⛔ 自链必须有上限：后台线程若卡在连接上不返回，无上限重排会让这个
            #   `singleShot` 永远占着事件循环（全量自检挂死/CPU 打满的一类真因）。
            #   60 次 × 150ms ≈ 9s 未回 ⇒ 放弃，如实写「超时」收尾。
            box["tries"] = box.get("tries", 0) + 1
            if box["tries"] > 60:
                lab.setText(_status_unreachable("timeout"))
                return
            QTimer.singleShot(150, _apply)
            return
        st = box.get("st")
        if isinstance(st, dict) and st:
            lab.setText(_status_text_for(status_id, st))
        else:
            lab.setText(_status_unreachable(box.get("err")))

    QTimer.singleShot(150, _apply)


def _status_unreachable(err) -> str: # noqa: ANN001
    """控制台没连上时状态位该说的话 —— 不写「检测中」（不是还在测，是没接上）。"""
    s = str(err or "")
    if "timed out" in s.lower() or "timeout" in s.lower():
        return "读不到：控制台响应超时（它还在启动吗）"
    if "refused" in s.lower() or "10061" in s or "Connect" in s:
        return "读不到：控制台没起来（先开控制台再接）"
    return "读不到：%s" % (s[:60] or "控制台未就绪")


def _table_row(t: Tokens, r: "sec_meta.Row", card: Card) -> QWidget:
    """web `<table>` → 表头 + 数据行（只读）。行标题在上，表体在下。"""
    from PySide6.QtWidgets import QGridLayout # noqa: PLC0415

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
            # web 清单表首列是勾选框（结果列）—— 原来渲染空文本 ⇒ 勾选列蒸发。
            if (j == 0 and i - 1 < len(r.cell_checks) and r.cell_checks[i - 1]
                    and not cell.strip()):
                from PySide6.QtWidgets import QCheckBox # noqa: PLC0415

                c: QWidget = QCheckBox()
                c.setToolTip("勾选=这项测过了（对齐 web 的记忆勾选；Qt 侧暂为会话内状态）")
            else:
                c = QLabel(cell)
                c.setFont(qfont(t, t.body_size - 1))
                c.setStyleSheet(f"color:{t.tx2};background:transparent;")
                c.setWordWrap(True)
            grid.addWidget(c, i, j)
    v.addLayout(grid)
    return wrap


_STATUS_CACHE: dict = {}
_STATUS_FAIL_TS: float = 0.0 # 后端连不上时 30s 内不再重试（否则每次惰性建页同步卡 3 端口×超时）


def _load_status() -> dict:
    """/api/status 快照（进程内缓存 3 秒，避免每行打一次网络；失败缓存 30 秒）。"""
    global _STATUS_FAIL_TS
    now = time.time()
    if _STATUS_CACHE.get("t") and now - _STATUS_CACHE["t"] < 3.0:
        return _STATUS_CACHE.get("st") or {}
    if now - _STATUS_FAIL_TS < 30.0:
        return {}
    st = {}
    import urllib.request # noqa: PLC0415

    # ⛔ 这里不要去别处问"控制台端口"：候选就这两个（与启动器的顺延一致）。
    #   原先写的是 `from console_html import PORT as _PORT` —— 那个名字**从来不存在**，
    #   被 except 吞掉后静默退回 3210：看着像"兼容老版本"，其实每次都在走兜底。
    for p in (3210, 3211):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % int(p), timeout=0.8) as r:
                st = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception: # noqa: BLE001
            continue
    if not st:
        _STATUS_FAIL_TS = now
    _STATUS_CACHE["t"], _STATUS_CACHE["st"] = now, st
    return st


def _status_text_for(status_id: str, st: dict) -> str:
    """把 web 状态位 id 映射成本地可见的一行文字（读不到就如实说读不到）。"""
    if not st:
        return "读不到（控制台未就绪）"
    sid = status_id or ""
    if sid in ("wxver",):
        # 对齐 web L3506-3511 —— 微信版本行真值 = /api/status 的
        #   `wechat_version` 段（version/adapter/supported）；原读 vermat 的
        # version.wechat ⇒ 恒「微信版本读不到」。
        wv = st.get("wechat_version") or {}
        v = str(wv.get("version") or "").strip()
        if not v:
            vm = st.get("version") or {}
            v = str(vm.get("wechat") or "").strip()
            return ("微信 " + v) if (v and v != "unknown") else "未检测到"
        out = "微信 " + v + (" · 适配层 " + str(wv.get("adapter") or "-") if wv.get("adapter") else "")
        if wv.get("supported") is False:
            out += "（注意：低于 4.0，请升级微信）"
        return out
    if sid in ("vmVer",):
        vm = st.get("version") or {}
        v = str(vm.get("wechat") or "").strip()
        a = str(vm.get("adapter") or "").strip()
        return (("微信 %s × 适配层 %s" % (v, a or "-")) if v and v != "unknown" else "微信版本读不到")
    if sid in ("vsWhy", "ttsWhy"):
        # ⛔ 旧读法（st.tts/voice.ready）在 /api/status 里根本不存在 ⇒ 恒「读不到」
        #   （用户截图实锤：媒体页「引擎状态 检测中…→读不到」）。真值口径对齐 web
        #   loadStatus（assets/console/index.html :3714-3721 / :3774-3777）：媒体组件在
        #   `media` 段——语音转文字=media.voice、TTS=media.tts，各带 ok/why。
        md = st.get("media") or {}
        if not isinstance(md, dict):
            return "读不到"
        if md.get("error"):
            return "状态读取失败：%s" % md.get("error")
        # TTS 的 ok/why 在 media.tts.status 里（media_status.py :61 两层结构；web :3761 同口径）
        d = md.get("voice") if sid == "vsWhy" else (md.get("tts") or {}).get("status")
        if not isinstance(d, dict) or d.get("ok") is None:
            return "读不到"
        return (str(d.get("why") or "可用") if d.get("ok")
                else str(d.get("why") or "不可用"))
    if sid == "videoStat":
        # 视频链路现状（web :3639-3656 同口径）：video_read 段 + media.video 的外链/下载器
        vr = st.get("video_read") or {}
        if not isinstance(vr, dict):
            return "读不到"
        if vr.get("ready"):
            a = vr.get("asr") or {}
            out = ("可用：ffmpeg 已就绪 ｜ 音频识别"
                   + ("可用" if a.get("ok") else ("不可用（%s）" % (a.get("why") or "")))
                   + " ｜ 默认抽 %s 帧" % ((vr.get("limits") or {}).get("default_frames") or 4))
            ur = ((st.get("media") or {}).get("video") or {}).get("video_url") or {}
            if isinstance(ur, dict) and ur.get("enabled") is not None:
                out += " ｜ 外链解析：%s ｜ 下载器 yt-dlp：%s" % (
                    "已开启" if ur.get("enabled") else "默认关闭",
                    "已安装" if ur.get("ytdlp_ready") else "未安装")
            return out
        return "不可用：%s —— 群里发视频时会如实说读不了" % (vr.get("why") or "缺 ffmpeg")
    if sid == "igWhy":
        # 生图后端状态（web :3800-3808 同口径；权威=/api/status 的 media.image_gen 段）
        md0 = st.get("media") or {}
        if md0.get("error"):
            return "状态读取失败：%s" % md0.get("error")
        ig = md0.get("image_gen") or {}
        n = len(ig.get("backends") or [])
        if not ig.get("enabled"):
            return "未开启（默认关）"
        if n:
            return "已开 · %d 个后端可用（%s）" % (
                n, " / ".join(str(b.get("id") or "") for b in (ig.get("backends") or [])))
        return ("已开：本机没探到生图服务、也没手填 ⇒ 调 gen_image 会如实回「还没配后端」"
                "（常探 7860/7865/8188/9090；服务没起或端口不同都探不到）")
    if sid == "utGlobals":
        # 工具清单现状（web utStatLine :6828 同口径）
        ut = st.get("user_tools") or {}
        if not isinstance(ut, dict):
            return "读不到"
        if ut.get("error"):
            return "读取失败：%s" % ut.get("error")
        return ("总开关：%s ｜ 目录：%s ｜ 已装 %d 个 ｜ 已勾选 %d 个 ｜ 累计调用 %d 次"
                % ("开" if ut.get("enabled") else "关（清单不加载）", ut.get("dir") or "-",
                   len(ut.get("tools") or []), ut.get("ticked") or 0, ut.get("counts_total") or 0))
    if sid == "vmGate":
        # 版本门三态（web :3444-3464 同口径：true/false/读不到 分开，不许把读不到画成拦截）
        vg = st.get("version_gate") or {}
        allow = vg.get("allow") if isinstance(vg.get("allow"), bool) else None
        if allow is None:
            return "版本门读数读不到%s：不影响发送" % (
                "（%s）" % vg.get("error") if vg.get("error") else "")
        if allow:
            return ("版本可用：已实测，照常发送" if vg.get("level") == "ok"
                    else "版本可用：照常发送（这一版没实测记录，不影响使用）")
        return "版本未实测：按严格档暂停发送（可在配置里关掉 version_gate.strict）"
    if sid == "bgHead":
        # 输入方式（web :3496-3504 同口径）
        inp = st.get("input") or {}
        if not isinstance(inp, dict) or not inp:
            return "读不到"
        lv = ("（%s）" % inp["level"]) if inp.get("level") else ""
        return ("当前：真鼠标档%s · 会动光标、可能短暂置前" if inp.get("touches_cursor")
                else "当前：投递档%s · 不动光标、不要求可见；可能短暂置前，随后自动还回") % lv
    if sid == "ufpHead":
        # 环境指纹（web :3561-3568 同口径）
        f = st.get("ui_fp") or {}
        if not isinstance(f, dict):
            return "读不到"
        if f.get("error"):
            return "读不到指纹表：%s" % f.get("error")
        ks = list((f.get("keys") or {}).keys())
        cur = f.get("current") or ""
        hit = (f.get("keys") or {}).get(cur) or {}
        return ("本环境指纹 %s 条 · 共 %d 组环境 · 摘要 %s"
                % (hit.get("n") if isinstance(hit, dict) else 0, len(ks), f.get("digest") or "-")
                if ks else "还没有任何指纹（点右边「重新取指纹」）")
    if sid == "pdStat":
        # 待决台账（web :3585-3588 同口径）
        pd = st.get("pending_decisions") or {}
        if not isinstance(pd, dict):
            return "读不到"
        if pd.get("error"):
            return "读不到待决台账：%s" % pd.get("error")
        return ("待拍板 %d 件 · %s" % (pd.get("open") or 0, pd.get("summary") or "")
                if pd.get("open") else "没有待拍板的事")
    if sid == "vmDec":
        # 适配层裁定记录（web :3597-3605 同口径）
        vm = st.get("version") or {}
        ds = vm.get("decisions") or []
        if not ds:
            return "暂无"
        nm = {"upgrade_adapter": "去升级适配层", "update_host": "去更新本体",
              "allow_once": "仅本次允许", "wechat_side": "微信本身要处理", "none": "什么都不做"}
        return " ｜ ".join("%s · %s" % (nm.get(d.get("choice"), d.get("choice")),
                                        str(d.get("when") or "")[5:16]) for d in ds)
    if sid == "actStat":
        # 后台作业（web :3606-3615 同口径）
        jb = st.get("jobs") or {}
        ks = [k for k in jb.keys() if not k.startswith("_")] if isinstance(jb, dict) else []
        if not ks:
            return "没有在跑的事"
        return " ｜ ".join(
            "%s：%s" % (k, ("正在跑" if (jb[k] or {}).get("running")
                            else ("完成" if (jb[k] or {}).get("returncode") == 0
                                  else "结束 rc=%s" % (jb[k] or {}).get("returncode"))))
            for k in ks)
    return "读不到"


def _cursor_extras(t: Tokens, on_save) -> Card:
    """光标面板的「上传自定义图 / 重置默认」卡（web 同款文件语义）。"""
    import shutil # noqa: PLC0415
    from pathlib import Path # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog # noqa: PLC0415

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
            except Exception: # noqa: BLE001
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
        except Exception as e: # noqa: BLE001
            _after(False, str(e))

    def _reset() -> None:
        try:
            for name in ("custom-cursor.png", "custom-cursor-nod.png"):
                (root / "assets" / name).unlink(missing_ok=True)
            ok, msg = config_io.write_patch({"ui.cursor_image": ""})
            _after(ok, (msg + "，已重置为默认鲸鱼") if ok else msg)
        except Exception as e: # noqa: BLE001
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


# 这些动作 id 在本页**追加区/自定义卡**里已有 Qt 原生实现——元数据行再渲染一个
# stub 假按钮只会误导（用户点中的正是它们）。渲染时整行跳过。
_AIDS_COVERED_ELSEWHERE = {"localProbe", "wxRecheck", "ttsProbe", "vcProbe"}
# 这两个下拉是 web **纯 JS 专属控件**（providerSel 静态选项+change 联动、modelSel
# 动态填充），Qt 由模型页追加区原生实现（objectName 同名）——元数据行只渲染出
# 假/空下拉。fbKind 这类「无 cfg 但有静态
# 选项、被自定义提交真读」的活控件**不在**此列。
_JS_OWNED_SELECT_IDS = {"providerSel", "modelSel"}
# 这些状态位在追加区/自定义卡里有**带动作的等价物**（SD 本地服务卡有自己的状态行
# + 启停按钮，轮询独立接口）——元数据版只是颗「永远检测中」的死芯片。
_STATUS_COVERED_ELSEWHERE = {"sdLocalState"}


def _row_redundant(r: "sec_meta.Row") -> bool:
    """要不要跳过这条 buttons 行（都不渲染，整行省略）。

    ① `data-save` 按钮（web 每个配置段尾部的「保存设置（…）」，无动作 id）——
       `_cfg_panel` 页尾本来就有原生「保存设置」（同一条 _collect→write_patch
       落盘链），再渲染一个 stub 是重复假按钮（用户截图实锤）；
    ② 动作 id 全部落在 `_AIDS_COVERED_ELSEWHERE`（本机模型探测/微信重检/TTS 探测
       ——追加区已原生实现）；
    ③ **JS 专属 select 行**（无 data-cfg 且 id ∈ providerSel/modelSel，或既无
       data-cfg 又无任何选项的动态填充下拉）——渲染出来是空的或换了没反应的
       假控件，真控件在对应追加区。
    """
    if getattr(r, "kind", "") == "select" and not getattr(r, "cfg", ""):
        if getattr(r, "html_id", "") in _JS_OWNED_SELECT_IDS or not (r.options or []):
            return True
    if (getattr(r, "kind", "") == "status"
            and getattr(r, "status_id", "") in _STATUS_COVERED_ELSEWHERE):
        return True
    if getattr(r, "kind", "") != "buttons":
        return False
    acts = getattr(r, "actions", None) or []
    if not acts:
        return False
    for txt, aid in acts:
        if str(aid) in _AIDS_COVERED_ELSEWHERE:
            continue
        if not aid and str(txt).strip().startswith("保存"):
            continue
        return False
    return True


def _cfg_panel(t: Tokens, s: "sec_meta.Sec", on_save=None) -> QWidget:
    """配置型面板：头部 + 一张设置卡（行 × 分隔线）+ 保存行。

    保存口径：收集本卡全部可编辑行的 {点路径: 值}，走
    `config_io.write_patch`（与网页控制台 /api/config 同款深合并落盘），
    成败都在行内如实反馈；on_save 供 Shell 做即时联动（光标/主题/画卷）。
    """
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    badge = Badge(t, "info", "未检测")
    page._c8_badge = badge # Shell._apply_badges 按此引用分发（P0-A①）
    lay.addWidget(h2(t, s.title, badge))
    if s.desc:
        lay.addWidget(desc(t, s.desc))

    card = Card(t)
    binds: list[tuple["sec_meta.Row", QWidget]] = []
    _placed = 0 # 实际渲染的行数（跳过冗余行时不画多余分隔线）
    for r in s.rows:
        if _row_redundant(r):
            continue
        if _placed:
            card.body.addWidget(_divider(t))
        f = _row(t, r, card, binds)
        if f is not None:
            card.body.addWidget(f)
            _placed += 1
    lay.addWidget(card)
    # 行控件索引（Row, 控件）挂 page —— APPENDIX 追加区（本机模型探测「用这个」）按 cfg 回填
    page._c8_binds = binds

    # 徽章本地语义：行初值来自真 config（_row 里 read_path），
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

    # ── 改完即生效── web 顶栏 autoApplyChk（assets/console/index.html L753，
    # 默认勾选、状态记本机）的 Qt 等价：勾上后本面板任何控件改动，防抖 600ms
    # 自动 write_patch（与手动保存同一条深合并落盘链路），不用再点「保存设置」。
    from PySide6.QtCore import QSettings, QTimer as _QTimer # noqa: PLC0415

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
            if not r.cfg:
                continue # 无 cfg 的表单行只供动作接线读值，不参与保存
            v = _ctrl_value(r, ctrl)
            if v is _SKIP:
                if ctrl is not None:
                    skipped += 1 # 有控件但留空/格式不对 → 计数如实说
                continue
            patch[r.cfg] = v
        # 追加区（APPENDIX）里手写的动态下钻控件（如联网搜索的 web_search.<prov>.*）
        # 不在 binds 里，靠它们自己暴露的 sync_from_form 钩子并入同一份 patch
        # —— 与 web「saveAll 里串 wsSyncFromForm()」同语义，共用同一条落盘链。
        _ws_sync = getattr(page, "_ws_sync_from_form", None)
        if callable(_ws_sync):
            try:
                _ws_sync()
                page._ws_collected = True
                patch.update(getattr(page, "_ws_patch", None) or {})
            except Exception: # noqa: BLE001 — 追加区取不到值不拖垮主保存
                pass
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
            ok, msg = _verify_on_disk(patch, msg) # 真读回 config.json 验证（非 mock）
        _save_feedback(ok, msg, skipped)
        if ok and callable(on_save):
            try:
                on_save()
            except Exception: # noqa: BLE001
                pass

    def _verify_on_disk(patch: dict[str, object], msg: str) -> tuple[bool, str]:
        """ P0-A④ 落盘真验证：保存后重新打开 config.json 逐键比对，
        写进去了才算成功（write_patch 返回 ok 只代表「没报错」）。"""
        from pathlib import Path # noqa: PLC0415

        from agent.config import CONFIG_FILE # noqa: PLC0415

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
        except Exception as e: # noqa: BLE001
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

    page._c8_mark_dirty = _mark_dirty # 追加区手写控件（如联网搜索下钻行）挂同一防抖
    page._c8_auto_on = auto_chk # 追加区查询「改完即生效」当前开关态

    for r, ctrl in binds:
        if ctrl is None:
            continue
        if r.kind == "checkbox":
            ctrl.toggled.connect(_mark_dirty)
        elif r.kind == "select":
            ctrl.currentIndexChanged.connect(_mark_dirty)
        elif r.kind == "textarea":
            ctrl.textChanged.connect(_mark_dirty)
        elif r.kind in ("text", "password", "number", "range"):
            # ⚠️ 修：不再用裸 `else` 兜底 —— 那样任何漏网类型都会撞到这里。
            #   具名判定 + hasattr 防御，缺信号就跳过（宁可少监听，不可崩面板）。
            sig = getattr(ctrl, "editingFinished", None)
            if sig is not None:
                sig.connect(_mark_dirty)
        # 其余类型（info/chips/status/buttons/table）:展示型，不监听变更。

    def _on_auto(on: bool) -> None:
        QSettings("WXAgent", "persona-morph-ui").setValue("auto_apply", bool(on))
        if on:
            note.setText("改完即生效：已开启（改动自动写入 config.json）")
            debounce.start() # 打开瞬间把当前面板值落一次
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
    # 面板追加区（元数据页 + 动态列表卡共存）——如 wechat 的表情收藏夹。
    #   值可为单函数或函数列表（一页多张追加卡：model 的探测卡 + 厂商联动）。
    append_fn = getattr(panels_custom, "APPENDIX", {}).get(sec)
    if append_fn is not None:
        fns = append_fn if isinstance(append_fn, (list, tuple)) else (append_fn,)
        for af in fns:
            try:
                af(t, inner)
            except Exception: # noqa: BLE001 — 追加区失败不拖垮整页
                pass
    wrap = QScrollArea()
    wrap.setWidgetResizable(True)
    wrap.setFrameShape(QFrame.Shape.NoFrame)
    wrap.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    wrap.setWidget(inner)
    wrap._c8_badge = getattr(inner, "_c8_badge", None) # Shell._apply_badges 取用
    return wrap
