# -*- coding: utf-8 -*-
"""会话行点击目标的**学习型偏好**（数据在 `data/click_targets.json`）。

⛔ 为什么不做"版本 → 方法"的写死表（2026-09-21 的实测教训）：
   2026-09-13 的结论是"会话行必须投**渲染子窗**、投主窗点不动"，这句话当年被写进了代码注释**和判据**；
   2026-09-21 同一台机器、同一落点实测**正好反了**（主窗 5/5 生效、渲染子窗 0/5）⇒ 写死的表一遇到
   微信/适配层变化就**静默失效**——更糟的是注释和判据还会"证明"它是对的（那轮六次实验全白做）。

⇒ 本模块只做一件事：**记住"上一次哪个目标真的生效了"**，用来决定**先试哪个**。
   · **授权永远来自每枪之后的现场自检**（`wechat._click_visible_session` 的复核）——偏好不参与"发不发"；
     所以"记错了"最坏后果＝多花一枪（还有 1.35s 冷却），不会变成"发错会话"；
   · 冷启动 / 无记录 ⇒ 默认顺序（主窗优先，`input_backend.row_click_targets`）；
   · 记过成功 ⇒ 把成功过的那个**提到最前**（省一枪、也少一次 1.35s 冷却）；
   · **连续失败 ≥2 ⇒ 丢掉偏好、退回默认顺序**（防"记错了"把后来版本锁死在错的目标上）。
轴的取法与 `ui_fingerprint` / `version_matrix` 一致：**微信版本 × 适配层版本 × 渲染区尺寸 × DPI × 输入后端**。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import DATA_DIR

log = logging.getLogger("persona-morph")

# ⛔ 2026-09-21 修（第六轮 **V-R6-20**）：原来写的是 `os.path.join("data", ...)`（吃 CWD）——
#   生产启动都钉了 `cwd=ROOT` 所以现网没炸，但换个启动方式就会写到别处、且和 `send_retry` 不同源。
PATH = os.path.join(DATA_DIR, "click_targets.json")
# ⛔ 2026-09-21 加（第六轮 **V-R6-8**）：模块里原来**一把锁都没有**，而写者确实并存
#   （30s 心跳线程经 `send_text`→切会话→`_click_visible_session` 调 `record_ok`，与监听线程并发）
#   ⇒ 实测 3 线程×150 次只剩 6 次（丢 444 次）。这里给"读-改-写"整段加锁。
_LOCK = threading.RLock()
MAX_FAIL = 2                       # 连续失败到这个数 ⇒ 丢弃偏好
MAX_KEYS = 40                      # 老版本/老尺寸的记录上限（防文件长胖）


def _safe_int(v) -> int:
    """安全转 int（脏数据不许把整条链路带崩）。"""
    try:
        return int(v)
    except Exception:
        return 0


def key(wechat: str = "", adapter: str = "", w: int = 0, h: int = 0,
        dpi: str = "", backend: str = "") -> str:
    """偏好键（五轴）。任何一项缺省都照建，只是会更"不专指"。"""
    return "%s|%s|%dx%d|%s|%s" % (str(wechat or "unknown"), str(adapter or "unknown"),
                                  int(w or 0), int(h or 0), str(dpi or ""), str(backend or ""))


def _load() -> dict:
    try:
        if not os.path.exists(PATH):
            return {"schema": 1, "keys": {}}
        with open(PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        if not isinstance(d.get("keys"), dict):
            d["keys"] = {}
        return d
    except Exception as e:                                        # noqa: BLE001
        log.debug("点击目标偏好读失败（按空处理）：%s", e)
        return {"schema": 1, "keys": {}}


def _save(d: dict) -> None:
    """原子写；**永不抛**（偏好坏了不能挡住切会话）。"""
    try:
        ks = [(k, v) for k, v in (d.get("keys") or {}).items() if isinstance(v, dict)]
        if len(ks) > MAX_KEYS:                                    # 只留最近的 MAX_KEYS 条
            # ⛔ V-R6-18：排序键原来直接 `int(v.get("at"))` —— 一条脏条目（`at` 不是数字）
            #   就会让**读整段**抛错，被本函数的 except 吞掉 ⇒ 之后每次记录都写不进盘。
            ks.sort(key=lambda t: _safe_int((t[1] or {}).get("at")), reverse=True)
            d["keys"] = dict(ks[:MAX_KEYS])
        os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PATH)
    except Exception as e:                                        # noqa: BLE001
        # ⛔ V-R6-18：原来只有 DEBUG ⇒ "之后再也写不进盘"这件事在日志里看不见
        log.warning("点击目标偏好写失败（忽略，不影响切会话）：%s", e)


def peek(k: str) -> dict:
    """看这条键记住了什么（只读，给日志/控制台用）。

    ⛔ V-R6-19：原来 `dict(...再解包)` 遇到"某条记录不是字典"会抛 `ValueError`
    （判据自己声称"坏数据不许挡路（永不抛）"，实际只测了会吞异常的那条路）⇒ 加类型判断。
    """
    try:
        v = (_load().get("keys") or {}).get(k)
        return dict(v) if isinstance(v, dict) else {}
    except Exception:                                             # noqa: BLE001
        return {}


def order_named(base, k: str, named: dict) -> list:
    """按偏好排序：把"上次生效过的那个目标"提到最前；没有偏好就原样返回 `base`。

    `base` 是句柄列表（`input_backend.row_click_targets` 给的默认顺序）；
    `named` 是 `{'main': hwnd, 'render': hwnd}`（缺的键不放）——本模块不 import input_backend，
    免得循环依赖。
    """
    seq = [h for h in (base or [])]
    try:
        want = str((peek(k) or {}).get("ok") or "")
        h = named.get(want) if want else 0
        # ⚠️ 只允许在 `base` 里**重排**：base 是"这次允许投的目标集合"（`row_click_targets` 给的），
        #    偏好**不许往里加**它没有的目标（否则"偏好文件坏/被写脏"会变成"往未知窗口投鼠标"）。
        if h and any(int(x) == int(h) for x in seq):
            seq = [h] + [x for x in seq if int(x) != int(h)]
    except Exception:                                             # noqa: BLE001
        return list(base or [])
    return seq


def record_ok(k: str, kind: str) -> None:
    """记一次"这个目标真的生效了"（kind ∈ main/render/其它）。"""
    try:
        with _LOCK:                                     # ⛔ V-R6-8：读-改-写整段加锁（丢更新实测 450→6）
            d = _load()
            cur = dict((d["keys"] or {}).get(k) or {})
            cur["ok"] = str(kind)
            cur["okN"] = _safe_int(cur.get("okN")) + 1
            cur["failN"] = 0
            cur["at"] = int(time.time())
            d.setdefault("keys", {})[k] = cur
            _save(d)
    except Exception as e:                                        # noqa: BLE001
        log.debug("点击目标偏好记录失败（忽略）：%s", e)


def record_fail(k: str) -> None:
    """记一次"所有目标都没生效"：连续到 `MAX_FAIL` 就丢掉这条偏好（退回默认顺序）。"""
    try:
        with _LOCK:
            d = _load()
            cur = dict((d["keys"] or {}).get(k) or {})
            cur["failN"] = _safe_int(cur.get("failN")) + 1
            cur["at"] = int(time.time())
            if cur["failN"] >= MAX_FAIL:
                (d.get("keys") or {}).pop(k, None)
                log.info("点击目标偏好：连 %d 次没生效 ⇒ 丢掉这条偏好、退回默认顺序（%s）", MAX_FAIL, k)
            else:
                d.setdefault("keys", {})[k] = cur
            _save(d)
    except Exception as e:                                        # noqa: BLE001
        log.debug("点击目标偏好记录失败（忽略）：%s", e)


def stats() -> dict:
    """给控制台/日志：当前记住了哪些键、各是什么。"""
    try:
        d = _load()
        return {"path": PATH, "n": len(d.get("keys") or {}), "keys": d.get("keys") or {}}
    except Exception:                                             # noqa: BLE001
        return {"path": PATH, "n": 0, "keys": {}}
