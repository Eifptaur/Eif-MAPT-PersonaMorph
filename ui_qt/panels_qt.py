# -*- coding: utf-8 -*-
"""通用面板构建器 —— 输入 sec 键，产出对齐 web 版式的 QWidget。

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


# ---------------------------------------------------------------- 徽章口径


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
            return _SKIP # 留空 = 不改这一项
        try:
            f = float(s)
        except ValueError:
            return _SKIP # 格式不对不改，保存反馈里如实说
        return int(f) if f == int(f) else f
    if r.kind == "select":
        return str(ctrl.currentData())
    if r.kind == "textarea":
        return ctrl.toPlainText()
    if r.kind == "chips":
        # web chips 存 list（如 group_name_white_list）—— 逗号/中文逗号分隔还原
        parts = [x.strip() for x in ctrl.text().replace("，", ",").split(",")]
        return [x for x in parts if x]
    return ctrl.text()


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
    「检测群聊并勾选 / 刷新群列表」按 web 原版真接线（console_html.py:2806-2842）：
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
    box: dict = {"done": False, "val": None, "err": None}

    def _work() -> None:
        try:
            box["val"] = config_io.get_json(
                "/api/wechat-groups" + ("?refresh=1" if aid == "refreshGroups" else ""),
                timeout=30.0, err_box=box)
        except Exception as e: # noqa: BLE001
            box["err"] = str(e)
        box["done"] = True

    import threading as _th # noqa: PLC0415

    _th.Thread(target=_work, daemon=True, name="chips-groups").start()

    from PySide6.QtCore import QTimer # noqa: PLC0415

    def _apply() -> None:
        if not box["done"]:
            QTimer.singleShot(150, _apply)
            return
        rsp = box["val"]
        if not isinstance(rsp, dict):
            err = box["err"] or ""
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

    QTimer.singleShot(150, _apply)


def _open_group_pick(t: Tokens, line, note, groups: list) -> None:
    """web「选择监听的群」弹窗的 Qt 版 —— 主题化（confirm.ConfirmDialog 同款设计语言）。

    无边框卡片壳 + 半透明背景 + 项目 Btn/Switch 组件 + 父窗口居中；
    必须模态 exec()（web 勾选层挂到用户点确定/取消为止）。
    """
    from PySide6.QtCore import QTimer # noqa: PLC0415
    from PySide6.QtWidgets import ( # noqa: PLC0415
        QDialog, QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout,
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
    dlg = QDialog(line.window())
    dlg.setWindowTitle("选择监听的群")
    dlg.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.setModal(True)
    dlg.resize(460, 520)

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
        f"QListWidget{{background:{rgba(t.q('tx'), 0 if t.glass else 10).name(QColor.NameFormat.HexArgb)};"
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
            bx["done"] = True

        bx: dict = {"done": False, "rsp": None, "err": None}
        import threading as _th # noqa: PLC0415

        _th.Thread(target=_work, daemon=True, args=(bx,), name="chips-save").start()

        def _apply_save() -> None:
            if not bx["done"]:
                QTimer.singleShot(150, _apply_save)
                return
            note.setText("已保存群白名单（%d 个群）" % len(picked)
                         if bx["err"] is None else "群白名单保存失败：" + bx["err"])

        QTimer.singleShot(150, _apply_save)

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
    # ── community / feedback（批4）──
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
    bx: dict = {"done": False, "rsp": None, "err": None}

    def _work() -> None:
        try:
            if method == "POST":
                from agent_bridge import post_json # noqa: PLC0415

                # body 允许是 callable（如 openExportDir：点击时才读当前配置拼路径）
                bx["rsp"] = post_json(api, body() if callable(body) else body, timeout=90.0)
            else:
                bx["rsp"] = config_io.get_json(api, timeout=60.0)
        except Exception as e: # noqa: BLE001
            bx["err"] = str(e)
        bx["done"] = True

    import threading as _th # noqa: PLC0415

    _th.Thread(target=_work, daemon=True, name="act-" + aid).start()

    from PySide6.QtCore import QTimer # noqa: PLC0415

    def _apply() -> None:
        if not bx["done"]:
            QTimer.singleShot(150, _apply)
            return
        if bx["err"]:
            note.setText("%s 失败：%s" % (aid, bx["err"]))
            return
        rsp = bx["rsp"] if isinstance(bx["rsp"], dict) else {}
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
            # 旧写法按 c.get("ok") is False 统计 ⇒ 恒「失败 0」误导；按 status 口径改。
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

    QTimer.singleShot(150, _apply)


def _code_check_run(note, deps: bool) -> None:
    """代码检测（web runCodeCheck :5893-5945 同款）：POST 启动 → 轮询 progress → 逐项落地。

    轮询在后台线程（300 次×间隔，约 45s 上限）；note 只在主线程读共享 dict，不冻 UI。
    """
    note.show()
    note.setText("代码检测启动中…")
    bx: dict = {"done": False, "err": None, "line": "代码检测启动中…"}

    def _work() -> None:
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
        finally:
            bx["done"] = True

    import threading as _th # noqa: PLC0415

    _th.Thread(target=_work, daemon=True, name="code-check").start()

    from PySide6.QtCore import QTimer # noqa: PLC0415

    def _apply() -> None:
        note.setText(bx["line"])
        if bx["done"]:
            if bx["err"]:
                note.setText("代码检测失败：" + bx["err"])
            return
        QTimer.singleShot(300, _apply)

    QTimer.singleShot(300, _apply)


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
    """Qt 壳里 web 动作按钮的点击反馈（原型边界：不假跑 web JS）。"""
    b.setEnabled(False)
    b.setText(b.text() + "（web 侧动作）")
    b.setToolTip("这个动作在 web 控制台执行；Qt 壳只做版式还原，不冒充已执行。")


_STATUS_ROWS: list = [] # 面板内 status 行注册表 [(label, status_id)]——跟随全局轮询刷新


def refresh_status_rows(st) -> None:
    """全局状态刷新时同步刷新面板内 status 行。

    原来 `_status_chip` 只在构建那一刻读一次 ⇒ 之后永远停在「读不到/未检测/检测中」。
    现在注册进表，由 shell._poll_badges 的 8 秒轮询携带最新 /api/status 调用本函数；
    st 拿不到就不动（不编数）。
    """
    if not isinstance(st, dict) or not st:
        return
    for lab, sid in list(_STATUS_ROWS):
        try:
            lab.setText(_status_text_for(sid, st))
        except Exception: # noqa: BLE001
            pass


def _status_chip(t: Tokens, status_id: str) -> QWidget:
    """web `<b id="…">` 状态位 → 只读状态标签。

    构建时就地读一次（后端活着即刻有值）；并注册进 `_STATUS_ROWS`，
    之后跟随 8 秒全局轮询持续刷新。
    """
    lab = QLabel("未检测")
    lab.setFont(qfont(t, t.body_size - 0.5))
    lab.setStyleSheet(f"color:{t.tx2};background:transparent;")
    lab.setProperty("web_status_id", status_id or "")
    try:
        st = _load_status()
        tip = _status_text_for(status_id, st) if st else "读取中…"
    except Exception: # noqa: BLE001
        tip = "读不到（控制台状态未就绪）"
    lab.setText(tip)
    _STATUS_ROWS.append((lab, status_id or ""))
    return lab


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
    try:
        import urllib.request # noqa: PLC0415

        from console_html import PORT as _PORT # noqa: PLC0415
    except Exception: # noqa: BLE001
        _PORT = 3210
    for p in (_PORT, 3210, 3211):
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
        for k in ("tts", "voice", "voice_models"):
            d = st.get(k)
            if isinstance(d, dict) and d.get("ready") is not None:
                return ("可用" if d.get("ready") else "不可用")
        return "读不到"
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
    for i, r in enumerate(s.rows):
        if i:
            card.body.addWidget(_divider(t))
        f = _row(t, r, card, binds)
        if f is not None:
            card.body.addWidget(f)
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

    # ── 改完即生效── web 顶栏 autoApplyChk（console_html.py L753，
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
    # 面板追加区（元数据页 + 动态列表卡共存）——如 wechat 的表情收藏夹
    # （web 的 emojiBox 挂在微信卡下方；元数据驱动做不了动态列表，APPENDIX 补位）。
    append_fn = getattr(panels_custom, "APPENDIX", {}).get(sec)
    if append_fn is not None:
        try:
            append_fn(t, inner)
        except Exception: # noqa: BLE001 — 追加区失败不拖垮整页
            pass
    wrap = QScrollArea()
    wrap.setWidgetResizable(True)
    wrap.setFrameShape(QFrame.Shape.NoFrame)
    wrap.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    wrap.setWidget(inner)
    wrap._c8_badge = getattr(inner, "_c8_badge", None) # Shell._apply_badges 取用
    return wrap
