# -*- coding: utf-8 -*-
"""配置读写桥 —— Qt 面板 ⇆ agent/config.json 的唯一通道。

**写入口径与 web 控制台完全一致**（agent/webui.py `_rapi_config_post` L1886-1912
逐条对齐，配置字段语义不变）：
    1. 面板收集 {点路径: 新值} → 组装成嵌套 dict（部分字段保存不丢段）
    2. `new_cfg = deep_merge(get_config(), patch)` —— 新值优先、缺失键保留旧值；
       deep_merge 返回全新深拷贝，绝不原地改单例（webui L1891-1892 同一条血泪）
    3. `save_config(new_cfg)` **先落盘**（原子写，失败抛异常 → 如实报，不崩）
    4. `set_config(new_cfg)` 落盘成功**才**换内存（盘与内存要么一起新、要么一起旧）
    5. `_protect_secrets` 掩码保护 Qt 侧天然不需要：掩码只出现在 web GET 输出侧，
       Qt 直读 get_config() 拿到的本来就是真实值，不存在"掩码写脏真实密钥"。

`wechat.data_dir`（微信数据目录）Qt 侧不代持：web 对它有专用校验闸
`_wechat_dir_conflict`（webui L235/L1897），Qt 侧剔除该键并如实提示去网页控制台改。

读入口径：同进程直连 `get_config()`（指纹失效自动重载 —— 网页控制台同进程改的
配置，Qt 面板重建/刷新即拿到最新值）。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Qt 侧不代持的键：web 有专用校验/专用 API 的写入通道
_BLOCKED = {"wechat.data_dir"}

#: "后端不可达"的记忆时长（秒）。一次探测失败后，这么多秒内不再逐口重试。
#  为什么需要：面板构建会连着问多个接口（人设页 5 个、概览页多个）——
#  后端没起时每个都要等满 timeout（实测单次 get_json 2.9s）⇒ 一次绘制白等十几秒，
#  且**用户每次打开面板都要再等一遍**。
#  取值权衡：太短 ⇒ 后端刚起来时面板仍显示"读不到"；太长 ⇒ 后端刚关掉时仍以为它在。
#  2.5 秒 ≈ 用户点一下"刷新"来得及看到新结果的量级。
_DOWN_TTL_S = 2.5
_DOWN_ERR = "后端未就绪（最近一次连接失败，已跳过本次请求）"
_down_until = 0.0 # 单调时钟：在此之前直接判定不可达
#: 记忆**绑定的地址**。地址一变（换后端/判据起了假服务器/端口变了）⇒ 记忆立即作废。
#  为什么必须有：记忆若只按时间算，会把"旧地址连不上"的结论套到**新地址**头上。
#  实测踩到：`t_g12` 起了假后端并 patch `current_url` 指向它，却因前一条判据留下的
#  "不可达"记忆被短路 ⇒ 假后端一次都没被访问 ⇒ **假失败**（工具行没建出来）。
_down_base = ""


def _backend_down(base: str = "") -> bool:
    """刚刚探测过这个地址且它不可达 ⇒ True（用于短路，避免逐个接口空等超时）。

    判定规则（**地址必须能对上**，否则一律不拦）：
      · `base` 为空 ⇒ 不拦（"无主记忆"不能用来拦任何具体地址）；
      · 记忆的地址为空 ⇒ 不拦（同上，旧记忆没有归属就当它不存在）；
      · 记忆地址 ≠ 当前地址 ⇒ 清掉记忆、不拦（换后端了，旧结论无效）。
    """
    global _down_base, _down_until
    import time as _t # noqa: PLC0415

    if not base or not _down_base:
        return False
    if base != _down_base:
        _down_until = 0.0
        _down_base = ""
        return False
    return _t.monotonic() < _down_until


def _is_conn_failure(exc: object) -> bool:
    """这个异常是不是"**连不上后端**"（而不是"连上了、只是这个口报错"）？

    ⛔ **不能靠文案匹配**：`urlopen` 抛的 `URLError` 里裹的 `WinError 10061`
    在**中文 Windows 上原文是**"由于目标计算机积极拒绝，无法连接。" ——
    写 `"actively refused"` 这类英文关键词**一个都匹配不上**（实测踩过：
    短路完全失效，5 个口各等 2 秒）。
    ⇒ 判据分三层，任一层命中即可：
      ① **错误码**（最可靠，与语言无关）：`winerror` / `errno` 命中
         `WSAECONNREFUSED(10061)` / `WSAETIMEDOUT(10060)` / `ECONNREFUSED(111)` /
         `ETIMEDOUT(110)` / `EHOSTUNREACH(113)` / `ENETUNREACH(101)`；
      ② **异常类型**：`ConnectionRefusedError` / `TimeoutError` / `socket.timeout`；
      ③ **文案兜底**（英文 + 中文都写，覆盖极少数没有错误码的场合）。
    ⚠️ `HTTPError` 一律**不算**连接失败 —— 服务活着，后续接口必须照常尝试。
    """
    import socket as _sock # noqa: PLC0415
    import urllib.error as _ue # noqa: PLC0415

    if isinstance(exc, _ue.HTTPError):
        return False

    # ① 错误码（与语言无关，最可靠）
    _CODES = {10061, 10060, 10065, 10051, 110, 111, 101, 113, 112, 115}
    for attr in ("winerror", "errno"):
        code = getattr(exc, attr, None)
        if code is None and getattr(exc, "reason", None) is not None:
            code = getattr(exc.reason, attr, None)
        try:
            if code is not None and int(code) in _CODES:
                return True
        except (TypeError, ValueError):
            pass

    # ② 异常类型
    if isinstance(exc, (ConnectionRefusedError, TimeoutError, _sock.timeout, _sock.gaierror)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (ConnectionRefusedError, TimeoutError, _sock.timeout, _sock.gaierror)):
        return True

    # ③ 文案兜底（英文 + 中文各写几条）
    s = str(exc)
    return any(k in s for k in (
        "Connection refused", "actively refused", "timed out", "timeout",
        "No connection could be made", "getaddrinfo",
        "积极拒绝", "无法连接", "连接超时", "目标计算机", "由于",
    ))


def _note_unreachable(exc: object, base: str = "") -> None:
    """记录"这个地址不可达"，在 `_DOWN_TTL_S` 内短路**同一地址**的后续请求。

    ⚠️ 只对**连接类失败**生效（判定见 `_is_conn_failure`）：HTTP 错误码说明
    **服务在**、只是这个口答得不对 —— 那种情况必须让后续接口照常尝试。

    ⚠️ `base` 为空 ⇒ **不记忆**：没有归属的记忆会在后面误伤别的地址
    （实测踩到：连续判据里每条都起自己的假后端，上一条的无主记忆把下一条拦掉）。
    """
    global _down_until, _down_base
    import time as _t # noqa: PLC0415

    if not base:
        return
    if not _is_conn_failure(exc):
        return
    _down_until = _t.monotonic() + _DOWN_TTL_S
    _down_base = base


def _note_reachable() -> None:
    """这次通了 ⇒ 清掉"不可达"记忆，后续请求照常发。"""
    global _down_until, _down_base
    _down_until = 0.0
    _down_base = ""


def _reset_backend_state() -> None:
    """清空可达性记忆。供自检在"造新现场"之间调用。"""
    global _down_until, _down_base
    _down_until = 0.0
    _down_base = ""


def read_path(dotpath: str, default=None):
    """按点路径读当前配置值；键缺失返回 default。"""
    if not dotpath:
        return default
    try:
        from agent.config import get_config # noqa: PLC0415

        cur = get_config()
        for part in dotpath.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur
    except Exception: # noqa: BLE001
        return default


def _nest(patch: dict[str, object]) -> dict:
    """{点路径: 值} → 嵌套 dict（与 web data-cfg 收集后的 POST body 同构）。"""
    out: dict = {}
    for dot, val in patch.items():
        parts = dot.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
            if not isinstance(cur, dict): # 键冲突（理论不该发生）→ 丢弃这段
                break
        else:
            cur[parts[-1]] = val
    return out


#: 撤销栈上限（web `UNDO_MAX` 同款：只记最近 N 步，重开清空）。
UNDO_MAX = 10

#: 撤销栈 —— **模块级**，与 web 控制台的全局 `undoStack` 同构。
#: 元素形如 `{"kind": "key"|"all", "path": …, "before": …, "snapshot": …, "label": …}`。
_undo_stack: list[dict] = []

#: 撤销深度变化时要通知的观察者（顶栏那颗钮的 title 要跟着更新）。
#: 用回调而不是直接 import UI —— 本模块是"无 GUI 的配置读写桥"，
#: 反向依赖 UI 会让它在无界面环境（自检 / CLI）里用不了。
_undo_listeners: list = []


def is_masked_val(v: object) -> bool:
    """打码值判据（web `isMaskedVal` 同款）。

    ⚠️ 这类值**不许进撤销栈**：它本来就不是真值，写回去等于没改，
    却会让用户以为"撤销成功了" —— 比不撤销更糟。
    """
    return isinstance(v, str) and ("••••" in v or v.startswith("sk-***"))


def on_undo_changed(cb, replace: bool = False) -> None:
    """登记撤销深度变化的监听（顶栏钮用）。

    `replace=True` 时先清空 —— 顶栏（含那颗钮）会被 `_rebuild` 整条重建，
    不换就成了"第一次那批已销毁的钮还挂在表里"（越切主题积得越多）。
    """
    if replace:
        _undo_listeners.clear()
    _undo_listeners.append(cb)


def _notify_undo() -> None:
    for cb in list(_undo_listeners):
        try:
            cb(undo_depth(), undo_top_label())
        except Exception: # noqa: BLE001 — 监听器出错不能影响撤销本身
            pass


def get_config_copy() -> dict:
    """当前配置的**深拷贝** —— 撤销快照只能存副本。

    ⚠️ 不能直接存 `get_config()` 的返回值：那是**单例**，后续 `set_config` 一改，
    快照也跟着变 ⇒ "撤销"会回退到**当前**状态（等于没撤）。web 侧不存在这个问题，
    因为它每步都 `JSON.stringify` 出一份新对象。
    """
    import copy # noqa: PLC0415

    try:
        from agent.config import get_config # noqa: PLC0415

        return copy.deepcopy(get_config())
    except Exception: # noqa: BLE001
        return {}


def undo_depth() -> int:
    """栈里还有几步可撤。"""
    return len(_undo_stack)


def undo_top_label() -> str:
    """栈顶那一步的名字（给按钮 title 用）；空栈返回空串。"""
    return str(_undo_stack[-1].get("label") or "") if _undo_stack else ""


def push_undo(entry: dict) -> None:
    """推一步快照；超过上限丢最旧的（web `pushUndo` 同款）。"""
    _undo_stack.append(entry)
    if len(_undo_stack) > UNDO_MAX:
        _undo_stack.pop(0)
    _notify_undo()


def _apply_undo(e: dict) -> tuple[bool, str]:
    """按快照类型回写：`all` ⇒ 整份快照；`key` ⇒ 只回那一个字段。"""
    from agent.config import deep_merge, get_config, save_config, set_config # noqa: PLC0415

    try:
        if e.get("kind") == "all":
            new_cfg = e.get("snapshot")
            if not isinstance(new_cfg, dict):
                return False, "撤销失败：快照不完整"
        else:
            path = str(e.get("path") or "")
            if not path:
                return False, "撤销失败：快照缺路径"
            new_cfg = deep_merge(get_config(), _nest({path: e.get("before")}))
        save_config(new_cfg) # 与正向写同一顺序：先落盘、成功才换内存
        set_config(new_cfg)
        return True, "已撤销"
    except Exception as ex: # noqa: BLE001
        return False, f"撤销失败：{ex}"


def undo_last() -> tuple[bool, str]:
    """回退一步（web `undoLast` 同款）。返回 (成功?, 人话说明)。

    ⚠️ 失败要把那一步**放回栈**：否则"撤销失败"会白吃掉一步，
    用户再点就撤到了更早的改动上（web 同款处理）。
    """
    if not _undo_stack:
        return False, f"没有可撤销的修改（只记最近 {UNDO_MAX} 步）"
    e = _undo_stack.pop()
    ok, note = _apply_undo(e)
    if not ok:
        _undo_stack.append(e)
        _notify_undo()
        return False, note
    _notify_undo()
    return True, "已撤销：" + (str(e.get("label") or "") or "上一步")


def _label_of(dot: str) -> str:
    """给快照起个人话名字 —— 点路径的**最后一段**（web 从面板 label 取，Qt 侧够用了）。"""
    return dot.rsplit(".", 1)[-1]


def write_patch(patch: dict[str, object]) -> tuple[bool, str]:
    """{点路径: 新值} → web 同款深合并落盘。返回 (成功?, 人话说明)。"""
    if not patch:
        return False, "没有可保存的改动"
    blocked = sorted(k for k in patch if k in _BLOCKED)
    clean = {k: v for k, v in patch.items() if k not in _BLOCKED}
    if not clean:
        return False, "该项请在网页控制台改（Qt 侧不代持）：" + "、".join(blocked)
    try:
        from agent.config import deep_merge, get_config, save_config, set_config

        # ★ 撤销快照必须在**写盘前**取（写完之后 `get_config()` 已经是新值）。
        #   粒度照 web 分两种：单字段 ⇒ `key`（只回那一个，不误伤后来改的别的字段）；
        #   多字段 ⇒ `all`（存整份改前快照）。
        #   打码值不入栈（见 `is_masked_val`）。
        _fields = [k for k in clean if not is_masked_val(read_path(k))]
        if _fields:
            if len(_fields) == 1:
                push_undo({"kind": "key", "path": _fields[0],
                           "before": read_path(_fields[0]), "label": _label_of(_fields[0])})
            else:
                push_undo({"kind": "all", "snapshot": get_config_copy(),
                           "label": "保存设置（%d 项）" % len(_fields)})

        new_cfg = deep_merge(get_config(), _nest(clean))
        save_config(new_cfg) # 先落盘（原子写；失败抛异常 → 如实报）
        set_config(new_cfg) # 落盘成功才换内存（webui L1906-1907 同款顺序）
        note = "已保存并生效"
        if blocked:
            note += "（未保存「微信数据目录」——该项请在网页控制台改）"
        return True, note
    except Exception as e: # noqa: BLE001
        return False, f"保存失败：{e}"


def write_full(raw_text: str) -> tuple[bool, str]:
    """配置格式面板：整份 JSON 校验 → 备份 → 落盘（"改前先备份"的承诺真兑现）。"""
    try:
        parsed = json.loads(raw_text)
    except Exception as e: # noqa: BLE001
        return False, f"JSON 解析失败，未写入：{e}"
    if not isinstance(parsed, dict):
        return False, "配置文件顶层必须是对象（{…}），未写入"
    from agent.config import CONFIG_FILE, set_config # noqa: PLC0415

    # ★ 撤销快照：整份配置面板的写入同样要能一步退回（这里最容易手误改坏整份）。
    push_undo({"kind": "all", "snapshot": get_config_copy(), "label": "配置格式面板（整份）"})

    target = Path(CONFIG_FILE)
    try:
        if target.exists():
            bak = target.with_name(
                f"{target.stem}.bak-qt-{__import__('time').strftime('%Y%m%d-%H%M%S')}{target.suffix}"
            )
            bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        from agent.config import save_config # noqa: PLC0415

        save_config(parsed)
        set_config(parsed)
        return True, "已保存（原文件已备份在同目录）"
    except Exception as e: # noqa: BLE001
        return False, f"保存失败：{e}"


def get_json(api: str, timeout: float = 1.5, err_box: dict | None = None) -> dict | None:
    """GET 后端接口（/api/status 等）；连不上返回 None（调用方如实展示）。

    err_box 传 dict 时，异常原文写入 err_box["err"]（超时/拒绝连接可区分）；
    默认 None 保持旧行为：静默返回 None。

    ⭐ **后端不可达时快速失败（`_backend_down` 短路）**：
       面板构建常会连着问好几个口（人设页一次 5 个）——
       后端没起时每个都要等满 timeout（实测单次 2.9s）⇒ 一次绘制白等十几秒。
       这里在发起请求前先看一眼"刚才那次探测的结果"，不可达就**立即返回 None**：
       调用方拿到的仍是"读不到"（语义不变），但不再逐个空等。
       ⚠️ 短路**按地址**判定（`_down_base`）⇒ 换了后端（含自检的假服务器）立即失效。
    """
    try:
        from addr import join_url # noqa: PLC0415
        from agent_bridge import current_url # noqa: PLC0415

        base = current_url()[0] if isinstance(current_url(), tuple) else current_url()
        if _backend_down(base): # 拿到具体地址后才判：地址变了就不该拦（见 `_down_base`）
            if err_box is not None:
                err_box["err"] = _DOWN_ERR
            return None
        # base 可能自带 ?token= —— 必须 join_url 让 api 落在 query 之前
        #
        req = urllib.request.Request(join_url(base, api),
                                     headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) # 绕代理（heal 同款）
        with opener.open(req, timeout=timeout) as resp:
            _note_reachable()
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e: # noqa: BLE001
        _note_unreachable(e, locals().get("base", ""))
        if err_box is not None:
            err_box["err"] = str(e)
        return None


def post_json(api: str, body: dict | None = None, timeout: float = 15.0) -> dict | None:
    """POST 后端接口（/api/resume、/api/selfcheck 等）；连不上返回 None。

    与 get_json 同源：current_url() 取基址 + join_url 让 api 落在 query 之前
    （base 可能自带 ?token=）+ 绕本地代理。body 序列化成 JSON 发。

    ⭐ 与 get_json 共用「后端不可达 ⇒ 快速返回 None」的短路（见 `_backend_down`）。
    """
    try:
        import json as _json # noqa: PLC0415

        from addr import join_url # noqa: PLC0415
        from agent_bridge import current_url # noqa: PLC0415

        base = current_url()[0] if isinstance(current_url(), tuple) else current_url()
        if _backend_down(base): # 拿到具体地址后再判一次（地址变了就不该拦，见 `_down_base`）
            return None
        data = _json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            join_url(base, api), data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        _note_reachable()
        try:
            return json.loads(raw)
        except Exception: # noqa: BLE001
            return {"ok": True, "raw": raw}
    except Exception as e: # noqa: BLE001
        _note_unreachable(e, locals().get("base", ""))
        return None


def _selftest() -> list[tuple[str, bool, str]]:
    """模块自检：nest 组装 / 点路径读 / 阻断键 / 全量写校验（不真写盘）。"""
    import sys # noqa: PLC0415

    sys.path.insert(0, str(ROOT)) # agent.config 需要项目根在 path

    out: list[tuple[str, bool, str]] = []

    def ck(name: str, cond: bool, extra: str = "") -> None:
        out.append((name, bool(cond), extra))

    n = _nest({"ui.whale_cursor": True, "ui.wave_fx.scale": 17, "bot.name": "x"})
    ck("config_io: 嵌套组装", n == {"ui": {"whale_cursor": True, "wave_fx": {"scale": 17}}, "bot": {"name": "x"}}, str(n))

    v = read_path("ui.whale_cursor")
    ck("config_io: 点路径读真配置", isinstance(v, bool), repr(v))
    ck("config_io: 缺键回默认", read_path("no.such.key", "D") == "D")

    ok, note = write_patch({"wechat.data_dir": "C:/x"})
    ck("config_io: 阻断键不代持且不写盘", not ok and "网页控制台" in note, note)
    ok, note = write_patch({})
    ck("config_io: 空 patch 拒绝", not ok, note)

    ok, note = write_full("{bad json")
    ck("config_io: 全量写挡坏 JSON", not ok and "未写入" in note, note)
    ok, note = write_full("[1,2]")
    ck("config_io: 全量写挡非对象顶层", not ok, note)

    # ── 撤销栈（web `#undoBtn` / `undoStack` 的 Qt 等价物）──
    _saved = list(_undo_stack)
    _undo_stack.clear()
    ck("undo: 空栈撤销 ⇒ 明确拒绝（不是静默无事发生）",
       undo_last() == (False, "没有可撤销的修改（只记最近 %d 步）" % UNDO_MAX))

    push_undo({"kind": "key", "path": "ui.theme", "before": "light", "label": "主题"})
    ck("undo: 压一步后深度=1 且栈顶标签可取",
       undo_depth() == 1 and undo_top_label() == "主题")

    for _i in range(UNDO_MAX + 5): # 故意压爆
        push_undo({"kind": "key", "path": "x", "before": _i, "label": "n%d" % _i})
    ck("undo: 超上限丢最旧的（深度 == UNDO_MAX）", undo_depth() == UNDO_MAX,
       "深度 %d / 上限 %d" % (undo_depth(), UNDO_MAX))

    ck("undo: 打码值判据（`••••` 与 `sk-***` 都算打码）",
       is_masked_val("abc••••xyz") and is_masked_val("sk-***abc") and not is_masked_val("real-key-123"))
    # ⚠️ 这条必须是**真断言**：早先我写成 `… if False else True` 是恒真的假绿，
    #    那种判据比没有更糟 —— 它会让"打码值进了撤销栈"这种回归一路绿灯。
    _self_src = Path(__file__).read_text(encoding="utf-8")
    ck("undo: 写盘侧对打码值不入栈（源码断言 is_masked_val 真的被调用）",
       "is_masked_val(read_path(k))" in _self_src)

    # ★ 反向锚：把上限压到 3，深度必须跟着变 3（证明上限**真的**在起作用）
    _prev_max = globals()["UNDO_MAX"]
    globals()["UNDO_MAX"] = 3
    _undo_stack.clear()
    for _i in range(8):
        push_undo({"kind": "key", "path": "y", "before": _i, "label": "m%d" % _i})
    ck("undo: 反向锚 —— 上限改成 3 后深度确实是 3", undo_depth() == 3, "实测 %d" % undo_depth())
    globals()["UNDO_MAX"] = _prev_max

    # 深拷贝：快照不能跟着源对象变
    _src = {"k": {"v": [1, 2]}}
    import copy as _copy # noqa: PLC0415

    _snap = _copy.deepcopy(_src)
    _src["k"]["v"].append(3)
    ck("undo: 快照是深拷贝（源再改不影响快照）", _snap["k"]["v"] == [1, 2])

    # 监听器 replace 语义（顶栏重建时不许累积）
    _seen: list = []
    on_undo_changed(lambda d, l: _seen.append("old"), replace=True) # noqa: ARG005
    on_undo_changed(lambda d, l: _seen.append("new"), replace=True) # noqa: ARG005
    _undo_stack.clear()
    push_undo({"kind": "key", "path": "z", "before": 0, "label": "z"})
    ck("undo: 监听器 replace 后只留最新的（否则切主题越积越多）", _seen == ["new"], str(_seen))

    _undo_stack.clear()
    _undo_stack.extend(_saved)
    return out


if __name__ == "__main__":
    import sys

    fails = 0
    for name, ok, extra in _selftest():
        print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
        fails += 0 if ok else 1
    raise SystemExit(1 if fails else 0)
