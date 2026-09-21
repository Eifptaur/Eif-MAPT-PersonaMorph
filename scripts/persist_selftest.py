# -*- coding: utf-8 -*-
"""持久化统一招式判据（审计第九轮 **V-R9-18 / V-R9-19 / V-R9-20 / V-R9-22**）。

跑法：`runtime\\python\\python.exe scripts\\persist_selftest.py`   （退出码 0=全过 / 1=有失败）

纪律：**只写 `tempfile` 下的临时目录**（不碰产品 `data\\`、不碰真实 `config.json` 内容——
      需要动配置的地方一律用 `config.set_config()` 改内存副本 / 猴补 `MIGRATIONS_MARK`），
      不起 GUI、不连网、不碰微信目录。

覆盖：
  ①坏 JSON ⇒ 返回默认值 **且** 生成 `.bad.<YYYYmmdd-HHMMSS>` 留证（原始坏内容一个字节不丢）
  ②文件不存在 ⇒ 返回默认值、不产生 `.bad.*`、不凭空造文件
  ③`atomic_write_json` 写的东西能被 `json.load` 读回、**不留 `.tmp`**；失败路径不静默、原档不动
  ④并发写（20 线程 × 10 次 + 边写边读）之后文件**始终**是可解析 JSON（审计里 memory 46/200 的那个场景）
  ⑤`risk_state.json` 坏掉 ⇒ 停机开关 **fail-closed**（坏档不许变成"没暂停"）
  ⑥V-R9-19：节日状态写失败 **留日志** + 内存标记 ⇒ 20 秒一轮的巡检**不再重发**
  ⑦V-R9-20：迁移记录坏掉 ⇒ **一条迁移都不跑**（不许静默重放去覆盖用户 config.json）
  ⑧V-R9-18：watermark 一条坏值**只丢那一条**（老写法会把整表归零）
  ⑨源码级锚：五个落盘点真的走了统一招式（防以后被顺手改回去）
"""
from __future__ import annotations

import glob
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)          # `_srcmatch`：源码级锚一律走它（空白容忍，别写脆断言）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _srcmatch as SM                 # noqa: E402
from agent import config as C          # noqa: E402
from agent import holidays as H        # noqa: E402
from agent import listener_watermark as LW   # noqa: E402
from agent import persist as P         # noqa: E402
from agent import risk as R            # noqa: E402
from agent import timers as T          # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, extra))


class _Cap(logging.Handler):
    """把 `persona-morph` 日志抓下来（判据要验"留了日志"，不是"看着像留了"）。"""

    def __init__(self):
        logging.Handler.__init__(self)
        self.msgs = []

    def emit(self, rec):
        try:
            self.msgs.append(rec.getMessage())
        except Exception:
            pass


CAP = _Cap()
logging.getLogger("persona-morph").addHandler(CAP)


def _hits(sub):
    return [m for m in CAP.msgs if sub in m]


def _read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def _code_lines(src):
    """只取代码行（丢掉空行/纯注释行）——源码级锚不该被"注释里提到的老写法"判红。"""
    return [l for l in src.splitlines() if l.strip() and not l.strip().startswith("#")]


def main():
    TMP = tempfile.mkdtemp(prefix="pm-persist-judge-")
    try:
        _s1_quarantine(TMP)
        _s2_missing(TMP)
        _s3_atomic(TMP)
        _s4_concurrent(TMP)
        _s5_risk_failclosed(TMP)
        _s6_holiday_no_resend(TMP)
        _s7_migrations(TMP)
        _s8_watermark_entries(TMP)
        _s9_source_anchors()
        _s10_concurrent_no_loss(TMP)
        _s11_quarantine_fail_no_overwrite(TMP)
        _s12_risk_shape_hole(TMP)
        _s13_risk_oneclick_recover(TMP)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


class _FailQuarantine:
    """上下文管理器：让"读这个档"必失败、且 `persist.quarantine` 对它**也必失败**。

    为什么用打桩而不是抢 Windows 独占句柄：判据要**确定、快、可无人运行**（抢句柄的做法
    在别的机器上时灵时不灵）。V-R10-23 要考的是"留证失败时下游怎么办"，
    打桩正好精确定位到那两行（读失败 + 改名失败），而且能给出反例锚。
    """

    def __init__(self, path, fail_read=True):
        self.path = os.path.abspath(str(path))
        self.fail_read = bool(fail_read)
        self.hits = 0
        self.renames = 0

    def __enter__(self):
        self._real_replace = P.os.replace
        self._real_load = P.json.load

        def _boom(src, dst):
            if os.path.abspath(str(src)) == self.path:
                self.renames += 1
                raise PermissionError(5, "判据打桩：留证必失败")
            return self._real_replace(src, dst)

        def _bad_load(fp, *a, **k):
            if self.fail_read:
                self.hits += 1
                raise ValueError("判据打桩：这个档读不出来")
            return self._real_load(fp, *a, **k)

        P.os.replace = _boom
        P.json.load = _bad_load
        return self

    def __exit__(self, *exc):
        P.os.replace = self._real_replace
        P.json.load = self._real_load
        return False


# ── ① 坏档留证 ──────────────────────────────────────────────────────────
def _s1_quarantine(TMP):
    print("== ① 坏 JSON ⇒ 默认值 + `.bad.<ts>` 留证（不删、不丢） ==")
    p = os.path.join(TMP, "bad.json")
    raw = '{"a": 1,'                       # 半截 JSON（这就是"写一半崩掉"的样子）
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(raw)
    dflt = {"k": "默认"}
    got = P.load_or_quarantine(p, dflt)
    bads = glob.glob(p + ".bad.*")
    check("① 坏档 ⇒ 返回传入的默认值（同一个对象）", got is dflt, repr(got))
    check("① 生成 .bad.<YYYYmmdd-HHMMSS> 留证", len(bads) == 1
          and re.match(r"^.*\.bad\.\d{8}-\d{6}(\.\w+)?$", bads[0]) is not None, str(bads))
    check("① 留证文件里是**原始坏内容**（一个字节没丢）",
          bool(bads) and _read(bads[0]) == raw, repr(_read(bads[0])) if bads else "无留证")
    check("① 坏档已让位（原路径不再存在，不会被人再读一次）", not os.path.exists(p))
    check("① 留了 warn（日志里说清是哪一份、为什么）",
          any(("bad.json" in m) for m in _hits("坏档留证")), str(CAP.msgs[-2:]))

    # 同一秒里连着坏两次 ⇒ 第二份留证不许覆盖第一份
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("{ 又坏了")
    P.load_or_quarantine(p, None)
    check("① 同一秒连着坏两次：两份留证都在（旧的那份不被覆盖）",
          len(glob.glob(p + ".bad.*")) == 2, str(glob.glob(p + ".bad.*")))


# ── ② 文件不存在 ────────────────────────────────────────────────────────
def _s2_missing(TMP):
    print("== ② 文件不存在 ⇒ 默认值、零副作用 ==")
    p = os.path.join(TMP, "nope.json")
    dflt = [1, 2]
    n_before = len(_hits("JSON 读不出来"))
    got = P.load_or_quarantine(p, dflt)
    check("② 不存在 ⇒ 返回默认值（同一对象）", got is dflt, repr(got))
    check("② 不存在 ⇒ 不产生任何 .bad.*（不许把「没有文件」当成坏档）",
          glob.glob(p + ".bad.*") == [], str(glob.glob(p + ".bad.*")))
    check("② 不存在 ⇒ 不凭空造出这个文件", not os.path.exists(p))
    check("② 没有为此记 warn（这不是异常；计数与调用前一致）",
          len(_hits("JSON 读不出来")) == n_before, str(_hits("JSON 读不出来")[n_before:]))


# ── ③ 原子写 ────────────────────────────────────────────────────────────
def _s3_atomic(TMP):
    print("== ③ atomic_write_json：能读回 / 不留 .tmp / 失败不静默 ==")
    p = os.path.join(TMP, "deep", "w.json")          # 目录不存在 ⇒ 要能自己建
    data = {"中文": "值", "n": [1, 2], "nested": {"a": True}}
    check("③ 写入成功返回 True", P.atomic_write_json(p, data, indent=1) is True)
    check("③ 能被 json.load 读回（内容一致）",
          json.load(io.open(p, encoding="utf-8")) == data)
    check("③ 不留任何 .tmp（名字带 pid+随机后缀，用完就换上去）",
          glob.glob(p + "*.tmp") == [], str(glob.glob(p + "*.tmp")))

    # 临时名形态（改的是 os 模块里的 replace，取完证据立刻还原）
    p_name = os.path.join(TMP, "named.json")
    seen = []
    _real = P.os.replace

    def _spy(a, b):
        seen.append(a)
        return _real(a, b)

    P.os.replace = _spy
    try:
        P.atomic_write_json(p_name, {"x": 1})
    finally:
        P.os.replace = _real
    check("③ 临时名形如 <path>.<pid>.<8位随机>.tmp，且被 replace 到目标",
          len(seen) == 1 and re.match(r"^%s\.%d\.[0-9a-f]{8}\.tmp$" % (re.escape(p_name), os.getpid()),
                                      seen[0]) is not None, str(seen))
    check("③ 换上去之后临时文件不残留", not (seen and os.path.exists(seen[0])))

    # 失败路径 a：数据不可序列化 ⇒ 原档一个字节不动、临时文件删掉、返回 False
    q = os.path.join(TMP, "keep.json")
    P.atomic_write_json(q, {"keep": True})
    before = _read(q)
    check("③b 不可序列化的 data ⇒ 返回 False（不静默吞）",
          P.atomic_write_json(q, {"x": object()}) is False)
    check("③b 写失败后原档一个字节没变", _read(q) == before, repr(_read(q)))
    check("③b 写失败后临时文件被删掉（不留半个 .tmp）",
          glob.glob(q + "*.tmp") == [], str(glob.glob(q + "*.tmp")))
    check("③b 失败留了 warn（说清原档未动）",
          any("原子写失败" in m for m in CAP.msgs), str(CAP.msgs[-2:]))

    # 失败路径 b：目录创建不出来（父路径是个文件）
    blk = os.path.join(TMP, "blocker")
    with io.open(blk, "w", encoding="utf-8") as f:
        f.write("x")
    check("③c 落盘路径不可写 ⇒ 返回 False（不是抛出去炸掉调用方）",
          P.atomic_write_json(os.path.join(blk, "a.json"), {}) is False)


# ── ④ 并发写 ────────────────────────────────────────────────────────────
def _s4_concurrent(TMP):
    print("== ④ 并发写：文件**始终**是可解析 JSON（老写法实测 46/200 坏） ==")
    p = os.path.join(TMP, "conc.json")
    stop = threading.Event()
    read_err = []
    opened = []

    def _reader():
        while not stop.is_set():
            try:
                with io.open(p, "r", encoding="utf-8") as f:
                    txt = f.read()
            except FileNotFoundError:
                continue                   # 还没写第一版，正常
            except (PermissionError, OSError):
                # ⚠️ Windows 上 `os.replace` 与"另一个句柄正打开着"会撞出**短暂的 EACCES**
                #    （连打开都没打开）——这不是"读到了坏内容"，不计入坏档。
                continue
            opened.append(1)
            try:
                json.loads(txt)            # 只把"真的读到了内容、但解析不了"算坏档
            except Exception as e:
                read_err.append("%s: %s" % (type(e).__name__, e))
            time.sleep(0.001)

    def _writer(i):
        for r in range(10):
            P.atomic_write_json(p, {"i": i, "r": r, "pad": "x" * 2000})

    rt = threading.Thread(target=_reader)
    rt.daemon = True
    rt.start()
    ths = [threading.Thread(target=_writer, args=(i,)) for i in range(20)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    stop.set()
    rt.join(timeout=2)

    check("④ 20 线程 × 10 次写完之后，文件仍是可解析 JSON",
          isinstance(json.load(io.open(p, encoding="utf-8")), dict))
    check("④ 边写边读：真的读到了内容（不是空跑出来的绿）", len(opened) > 0, str(len(opened)))
    check("④ 边写边读：读到内容的那 %d 次里**一次都没有解析失败**（原写法这里会读到半截）"
          % len(opened), read_err == [], str(read_err[:3]))
    check("④ 200 次写完之后一个 .tmp 都不剩",
          glob.glob(p + "*.tmp") == [], str(glob.glob(p + "*.tmp")))
    # 反例锚（确定性）：老写法"就地/半截写"出来的是什么样 —— 这份档就是审计里
    # 「写一半失败 ⇒ 文件 167B → 1B、读方静默 {}」的那一幕，同一组判据必须抓得住。
    p2 = os.path.join(TMP, "old.json")
    with io.open(p2, "w", encoding="utf-8") as f:
        f.write('{"i": 0, "r": 0, "pad": "')          # 写到一半，进程没了
    try:
        json.load(io.open(p2, encoding="utf-8"))
        old_ok = True
    except Exception:
        old_ok = False
    check("④ 反例锚：半截写出来的档确实读不出来（证明「可解析」这条有灵敏度）",
          old_ok is False, "old_ok=%s" % old_ok)


# ── ⑤ risk 停机开关 fail-closed ─────────────────────────────────────────
def _s5_risk_failclosed(TMP):
    print("== ⑤ risk_state.json 坏掉 ⇒ 停机开关 fail-closed（坏档 ≠ 没暂停） ==")
    cfg = json.loads(json.dumps(C.get_config()))
    rk = dict(cfg.get("risk") or {})
    rk.update({"enabled": True, "paused": False, "per_minute": 0, "per_hour": 0, "per_day": 0,
               "per_chat_per_hour": 0, "min_gap_seconds": 0, "quiet_hours": [],
               "broadcast_chats": 0, "escalate_after": 0, "block_keywords": [], "watch_keywords": []})
    cfg["risk"] = rk
    C.set_config(cfg)

    d = os.path.join(TMP, "risk")
    os.makedirs(d, exist_ok=True)
    st = os.path.join(d, "risk_state.json")
    with io.open(st, "w", encoding="utf-8") as f:
        f.write('{ "paused": true, "paused_reas')      # 坏档（半截，读不出来）
    g = R.RiskGate(path=st, event_path=os.path.join(d, "risk_events.jsonl"))
    check("⑤ 坏档 ⇒ `paused=True`（fail-closed，老写法这里是 False＝静默解除停机）",
          g.is_paused() is True, "paused=%s" % g.is_paused())
    v = g.check("group:x", "你好")
    check("⑤ 坏档之后 check() 真的拦下（L0 / code=paused）",
          (not v.allowed) and v.code == "paused", repr(v))
    check("⑤ 坏档已改名留证（原档没被删）",
          len(glob.glob(st + ".bad.*")) == 1, str(glob.glob(st + ".bad.*")))
    check("⑤ 坏档时的原因对用户说清楚了（不是空串）",
          "读不出来" in (g.snapshot().get("paused_reason") or ""), g.snapshot().get("paused_reason"))

    g.resume()
    check("⑤ 用户点『恢复发送』后能恢复（fail-closed 不是死锁）",
          g.is_paused() is False and g.check("group:x", "你好").allowed, repr(g.check("group:x", "你好")))

    # 阳性对照：从来没有过状态文件 ⇒ 不许误判成暂停（首启不许被锁死）
    fresh = os.path.join(d, "fresh.json")
    g2 = R.RiskGate(path=fresh, event_path=os.path.join(d, "ev2.jsonl"))
    check("⑤ 阳性对照：从来没有状态文件 ⇒ 不暂停", g2.is_paused() is False)
    check("⑤ 阳性对照：正常放行", g2.check("group:x", "你好").allowed)
    check("⑤ 阳性对照：没有产生 .bad.*", glob.glob(fresh + ".bad.*") == [])


# ── ⑥ 节日问候：写失败留日志 + 不重发 ───────────────────────────────────
def _s6_holiday_no_resend(TMP):
    print("== ⑥ V-R9-19：状态写失败 ⇒ 留日志 + 内存标记 ⇒ 不再重发 ==")
    groups = [{"name": "群deepseek", "wxid": "wxid_a"}]
    hcfg = {"holiday": {"mode": "active", "greet_chats": ["群deepseek"]}}
    day_ts = time.mktime((2026, 10, 1, 10, 0, 0, 0, 0, -1))       # 国庆节 · 10:00（时段内）
    H.custom_path = lambda: os.path.join(TMP, "no_such_holidays.json")   # 不读产品 data

    # 正向：正常落盘 ⇒ 不再重发、返回 True
    H.state_path = lambda: os.path.join(TMP, "holiday_state_ok.json")
    due = H.due_greetings(hcfg, groups, now=day_ts)
    check("⑥ 正向对照：active + 名单 + 节日 + 时段内 ⇒ 该发 1 条", len(due) == 1, str(due))
    check("⑥ 正向对照：正常落盘返回 True", H.mark_greeted(due[0]["day"], due[0]["chat_key"]) is True)
    check("⑥ 正向对照：记账后不再出现（读文件即可）",
          H.due_greetings(hcfg, groups, now=day_ts) == [])
    check("⑥ 正向对照：状态文件是合法 JSON",
          isinstance(json.load(io.open(H.state_path(), encoding="utf-8")), dict))

    # 反向：写盘写不进去（父路径是个文件）⇒ 必须留日志 + 内存标记 ⇒ 不重发
    blk = os.path.join(TMP, "holiday_blocker")
    with io.open(blk, "w", encoding="utf-8") as f:
        f.write("x")
    H.state_path = lambda: os.path.join(blk, "holiday_state.json")
    n_before = len(_hits("节日问候状态落盘失败"))
    due2 = H.due_greetings(hcfg, groups, now=time.mktime((2026, 12, 25, 10, 0, 0, 0, 0, -1)))
    check("⑥ 反向：另一个节日（圣诞节）该发 1 条", len(due2) == 1, str(due2))
    ok_save = H.mark_greeted(due2[0]["day"], due2[0]["chat_key"])
    check("⑥ 反向：写不进去 ⇒ 返回 False（不再 `except: pass` 装作没事）", ok_save is False)
    check("⑥ 反向：**留了日志**（老写法这里一行都没有）",
          len(_hits("节日问候状态落盘失败")) > n_before,
          str(_hits("节日问候状态落盘失败")[:1]))
    check("⑥ 反向：写盘失败后，同一轮/下一轮**不再重发**（内存标记挡住了 20 秒一轮的重发）",
          H.due_greetings(hcfg, groups, now=time.mktime((2026, 12, 25, 10, 0, 0, 0, 0, -1))) == [])
    check("⑥ 反向：阳性对照仍在（没把整条路堵死——同一天别的会话照发）",
          len(H.due_greetings(hcfg, [{"name": "群deepseek", "wxid": "wxid_b"}],
                              now=time.mktime((2026, 12, 25, 10, 0, 0, 0, 0, -1)))) == 1)


# ── ⑦ 迁移记录坏掉不许重放 ──────────────────────────────────────────────
def _s7_migrations(TMP):
    print("== ⑦ V-R9-20：迁移记录坏掉 ⇒ 一条迁移都不跑（不覆盖用户 config.json） ==")
    mark = os.path.join(TMP, "config_migrations.json")
    saved = []
    real_mark, real_save = C.MIGRATIONS_MARK, C.save_config
    C.MIGRATIONS_MARK = mark
    C.save_config = lambda cfg=None: saved.append(cfg)
    try:
        # (a) 坏档 ⇒ 不跑、不写盘、留证
        with io.open(mark, "w", encoding="utf-8") as f:
            f.write('["safe_defaults_2026_09_16",')          # 半截
        cfg = {"wechat": {"background_only": False}}
        out = C._migrate_once(cfg)
        check("⑦(a) 坏档 ⇒ 一条迁移都不跑（用户显式关掉的没被改回去）",
              out["wechat"]["background_only"] is False, str(out))
        check("⑦(a) 没有触发 save_config（绝不整体覆盖 config.json）", saved == [], str(saved))
        check("⑦(a) 坏档已留证（改名 .bad.<ts>）",
              len(glob.glob(mark + ".bad.*")) == 1, str(glob.glob(mark + ".bad.*")))
        check("⑦(a) 留了日志（不是静默跳过）",
              any("跳过全部一次性迁移" in m for m in CAP.msgs), str(CAP.msgs[-2:]))

        # (b) 第一次运行（文件本来就不在）⇒ 照常全跑（别把正常首启也堵了）
        for f in glob.glob(mark + ".bad.*"):          # 清掉 (a) 留下的留证，本条只看本次
            os.remove(f)
        if os.path.exists(mark):
            os.remove(mark)
        saved.clear()
        cfg = {"wechat": {"background_only": False}}
        C._migrate_once(cfg)
        check("⑦(b) 首次运行（无标记文件）⇒ 迁移照常生效", cfg["wechat"]["background_only"] is True)
        check("⑦(b) 生效后写回了标记，且**不产生 .bad.***",
              json.load(io.open(mark, encoding="utf-8")) and glob.glob(mark + ".bad.*") == [],
              str(glob.glob(mark + ".bad.*")))
        check("⑦(b) 标记写的是合法 JSON 列表（原子写）",
              isinstance(json.load(io.open(mark, encoding="utf-8")), list))

        # (c) 标记齐全 ⇒ 不再动用户配置
        saved.clear()
        cfg = {"wechat": {"background_only": False}}
        C._migrate_once(cfg)
        check("⑦(c) 标记齐全 ⇒ 用户的选择被尊重（一条都不改）",
              cfg["wechat"]["background_only"] is False and saved == [], str(cfg))

        # (d) 形状不对（能解析但不是一个列表）⇒ 同样一条都不跑
        with io.open(mark, "w", encoding="utf-8") as f:
            f.write('{"safe_defaults_2026_09_16": true}')
        saved.clear()
        cfg = {"wechat": {"background_only": False}}
        C._migrate_once(cfg)
        check("⑦(d) 形状不对 ⇒ 也一条都不跑 + 留证",
              cfg["wechat"]["background_only"] is False and saved == []
              and len(glob.glob(mark + ".bad.*")) == 1, str(glob.glob(mark + ".bad.*")))
    finally:
        C.MIGRATIONS_MARK, C.save_config = real_mark, real_save


# ── ⑧ watermark 逐条校验 ────────────────────────────────────────────────
def _s8_watermark_entries(TMP):
    print("== ⑧ V-R9-18：水位表一条坏值 ⇒ 只丢那一条（不整表归零） ==")
    p = os.path.join(TMP, "listener_watermark.json")
    # ⛔ 第十一轮 V-R11-5 之后：**水位表永远带账号前缀**（认不出账号时用保留名 `?`）——
    #   所以这里用带前缀的键做夹具；另留一个无前缀老键，专门验"升级不丢数据、但也不越权继承"。
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"acctA|group:a": 100, "acctA|group:b": "坏值", "acctA|group:c": 55,
                   "acctA|group:d": "12", "group:legacy": 7}, f)
    n_before = len(_hits("坏条目"))
    wm = LW.Watermark(p, "acctA")
    check("⑧ 坏条目只丢自己（另一个会话的水位原样保留）",
          wm.get("group:a") == 100 and wm.get("group:c") == 55, str(wm.data))
    check("⑧ 坏条目本身回 0", wm.get("group:b") == 0, str(wm.data))
    check("⑧ 反例锚：老写法（整表推导式）会把好的那几条一起归零",
          wm.data.get("acctA|group:a") == 100 and wm.data.get("acctA|group:b") is None, str(wm.data))
    check("⑧ 无前缀老键：**保留在表里、但不参与读写**（V-R11-5：不许当成本账号的水位）",
          wm.data.get("group:legacy") == 7 and wm.get("group:legacy") == 0, str(wm.data))
    check("⑧ 日志里**如实说了丢几条**",
          len(_hits("坏条目")) > n_before
          and any(SM.has(m, "1 条坏条目") for m in _hits("坏条目")),
          str(_hits("坏条目")[:1]))
    check("⑧ 数字字符串被认下来（不是一刀切当坏值）", wm.get("group:d") == 12)

    wm.set("group:a", 200)
    check("⑧ flush 走原子写：文件合法且不留 .tmp",
          wm.flush() and json.load(io.open(p, encoding="utf-8"))["acctA|group:a"] == 200
          and glob.glob(p + "*.tmp") == [])

    # 整档坏掉 ⇒ 留证（而不是静默当空表，然后被下一次 flush 覆盖）
    p2 = os.path.join(TMP, "wm_broken.json")
    with io.open(p2, "w", encoding="utf-8") as f:
        f.write("{ not json")
    wm2 = LW.Watermark(p2)
    check("⑧ 整档坏掉 ⇒ 空表 + `.bad.<ts>` 留证",
          wm2.data == {} and len(glob.glob(p2 + ".bad.*")) == 1, str(glob.glob(p2 + ".bad.*")))


# ── ⑨ 源码级锚 ──────────────────────────────────────────────────────────
def _s9_source_anchors():
    print("== ⑨ 源码级锚：五个落盘点真的走了统一招式（防回退） ==")
    files = {}
    for n in ("risk", "timers", "listener_watermark", "holidays", "config", "memory",
              "window_borrow", "wechat_ui"):
        files[n] = _read(os.path.join(ROOT, "agent", n + ".py"))

    five = ("risk", "timers", "listener_watermark", "holidays", "config")
    check("⑨ 五个落盘点都 import 了 persist（唯一实现，不再各写一份）",
          all(SM.has(files[n], "from . import persist") for n in five),
          str([n for n in five if not SM.has(files[n], "from . import persist")]))
    check("⑨ 读侧都走统一读招式（`load_or_quarantine` / 带「原档还在」信号的 `load_checked`）",
          all((SM.has(files[n], "load_or_quarantine") or SM.has(files[n], "load_checked"))
              for n in five),
          str([n for n in five if not (SM.has(files[n], "load_or_quarantine")
                                      or SM.has(files[n], "load_checked"))]))
    check("⑨ 写侧都走 atomic_write_json",
          all(SM.has(files[n], "atomic_write_json") for n in five),
          str([n for n in five if not SM.has(files[n], "atomic_write_json")]))
    _TMPNAME = ('+ ".tmp"',)
    check("⑨ 这四个文件的**代码行**里不再有 `+ \".tmp\"` 这种共用临时名（注释/文档里提旧写法不算）",
          all(not SM.has(l, *_TMPNAME) for n in ("risk", "timers", "listener_watermark", "holidays")
              for l in _code_lines(files[n])),
          str([n for n in ("risk", "timers", "listener_watermark", "holidays")
               if any(SM.has(l, *_TMPNAME) for l in _code_lines(files[n]))]))
    check("⑨ risk 的 fail-closed 写在代码里（读不出来 ⇒ paused=True）",
          SM.has(files["risk"], 'self._st["paused"] = True') and SM.has(files["risk"], "_BAD"))
    check("⑨ holidays 的 `_save_state` 不再吞异常（返回 bool + 写失败必留日志）",
          SM.has(files["holidays"], "def _save_state(st: dict) -> bool")
          and SM.has(files["holidays"], "节日问候状态落盘失败"))
    check("⑨ memory / window_borrow / wechat_ui 三处就地重写也改了（V-R9-22 点名）",
          all(SM.has(files[n], "atomic_write_json") for n in ("memory", "window_borrow", "wechat_ui")))


# ── ⑩ 并发写：不许"返回 False 却当成功"（V-R10-22）──────────────────────
def _s10_concurrent_no_loss(TMP):
    print("== ⑩ V-R10-22：竞争下不许静默丢写（老写法实测 71% 返回 False） ==")
    p = os.path.join(TMP, "loss.json")
    stop = threading.Event()
    fails = []
    total = []

    def _reader():
        while not stop.is_set():
            try:
                with io.open(p, "r", encoding="utf-8") as f:
                    json.loads(f.read())
            except (FileNotFoundError, PermissionError, OSError):
                pass
            except Exception as e:
                fails.append("read:%s" % type(e).__name__)
            time.sleep(0.0005)

    def _writer(i):
        for r in range(10):
            total.append(1)
            if P.atomic_write_json(p, {"i": i, "r": r, "pad": "x" * 2000}) is False:
                fails.append("write:%d/%d" % (i, r))

    rt = threading.Thread(target=_reader)
    rt.daemon = True
    rt.start()
    ths = [threading.Thread(target=_writer, args=(i,)) for i in range(20)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    stop.set()
    rt.join(timeout=2)

    check("⑩ 20 线程 × 10 写 + 1 读线程：**一次 False 都没有**（重试把瞬时共享冲突吃掉）",
          len(fails) == 0, "失败 %d 次：%s" % (len(fails), str(fails[:4])))
    check("⑩ 反例锚：老写法（一次 `os.replace` 失败就返回 False）在这个夹具下**确实会失败**"
          "（证明 ⑩ 的「0 次」不是恒真）",
          _repro_old_write_fails(TMP) > 0)
    check("⑩ 写完之后文件是可解析 JSON，且内容与某一次写完全一致（不半截）",
          isinstance(json.load(io.open(p, encoding="utf-8")), dict))
    check("⑩ 一个 .tmp 都不剩", glob.glob(p + "*.tmp") == [], str(glob.glob(p + "*.tmp")))
    check("⑩ 模块级如实记账：`REPLACE_FAILURES` 是计数字典（失败不许无声无息）",
          isinstance(getattr(P, "REPLACE_FAILURES", None), dict)
          and "count" in P.REPLACE_FAILURES)


def _repro_old_write_fails(TMP, rounds=200) -> int:
    """复刻"老写法"（共用 tmp 名 + 一次 os.replace 不作重试）在同一夹具下的失败次数。

    这是 ⑩ 的**反例锚**：如果这里也是 0，说明夹具退化了（比如盘/系统不再抢），
    判据得换夹具；这里 >0 才说明"重试"是真正起作用的那一环。
    """
    p = os.path.join(TMP, "old_loss.json")
    n = [0]
    lock = threading.Lock()

    def _old_write(data):
        tmp = p + ".tmp"
        try:
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            P.os.replace(tmp, p)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False

    def _w(i):
        for r in range(rounds // 4):
            if not _old_write({"i": i, "r": r, "pad": "x" * 2000}):
                with lock:
                    n[0] += 1

    ths = [threading.Thread(target=_w, args=(i,)) for i in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return n[0]


# ── ⑪ 留证失败 ⇒ 禁止覆盖（V-R10-23，V-R9-18 的回归）───────────────────
def _s11_quarantine_fail_no_overwrite(TMP):
    print("== ⑪ V-R10-23：坏档**留证失败** ⇒ 必须拒绝覆盖（原档一个字节不许动） ==")
    # (a) 纯 persist 层：`load_checked` 要把"原档还在"如实回出来
    p = os.path.join(TMP, "keep17.json")
    raw = '{"items": [1, 2, 3]}   <<< 坏在半截'
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(raw)
    with _FailQuarantine(p) as fc:
        got, ok_over = P.load_checked(p, "默认")
    check("⑪(a) 留证失败 ⇒ `可覆盖=False`（调用方能知道原档还在）", ok_over is False, str(ok_over))
    check("⑪(a) 留证失败 ⇒ 原档**原地不动**（一个字节没丢）",
          os.path.exists(p) and _read(p) == raw, repr(_read(p))[:40] if os.path.exists(p) else "缺失")
    check("⑪(a) 打桩确实生效过（不是没走到那条路）", fc.hits >= 1 and fc.renames >= 1,
          "read_fail=%s rename_fail=%s" % (fc.hits, fc.renames))
    check("⑪(a) 日志如实说「留证失败 ⇒ 禁止覆盖」",
          any("留证失败" in m for m in _hits("JSON 读不出来")), str(_hits("JSON 读不出来")[-1:]))

    # (b) timers：审计实测场景（20 条提醒只剩 2 条）——留证失败时 add() 必须回 ok:False
    tdir = os.path.join(TMP, "t11")
    os.makedirs(tdir, exist_ok=True)
    tpath = os.path.join(tdir, "timers.json")
    # ⚠️ 用 **2 条**（不是 20 条）：要真的走到 `_save()` 那条路，不能先被"每会话上限 3 条"的
    #    业务闸门拦掉（那样 `ok:False` 是假阳性，`_save` 根本没被调用）。
    body = json.dumps({"items": [{"id": i, "chat": "group:x", "note": "提醒%d" % i,
                                  "status": "pending", "fire_at": 0, "seconds": 60}
                                 for i in range(2)], "history": [], "next_id": 3})
    with io.open(tpath, "w", encoding="utf-8") as f:
        f.write(body)
    _real_path = T.path
    T.path = lambda: tpath
    n_before_refuse = len(_hits("拒绝覆盖"))
    try:
        with _FailQuarantine(tpath):
            got2 = T.add("group:x", "新提醒", seconds=120)
        n_items = len(json.load(io.open(tpath, encoding="utf-8")).get("items") or [])
    finally:
        T.path = _real_path
    check("⑪(b) 留证失败时 `timers.add` 回 ok:False（不许当作「已定好」）",
          got2.get("ok") is False, str(got2))
    check("⑪(b) 3 条原提醒**一条都没丢**（老写法这里会被空表盖掉）", n_items == 2, "items=%d" % n_items)
    check("⑪(b) 拒绝覆盖时留了 warn（不是静默）",
          len(_hits("拒绝覆盖")) > n_before_refuse,
          str(_hits("拒绝覆盖")[:1]))

    # (c) watermark / risk / holidays 三处同口径
    wpath = os.path.join(TMP, "wm11.json")
    with io.open(wpath, "w", encoding="utf-8") as f:
        f.write("{ 半截")
    with _FailQuarantine(wpath):
        wm = LW.Watermark(wpath)
        wm.set("group:a", 99)
        wm_ok = wm.flush()
    check("⑪(c) watermark：留证失败 ⇒ flush 拒绝写（原档没被空表盖掉）",
          wm_ok is False and _read(wpath) == "{ 半截", "ok=%s raw=%r" % (wm_ok, _read(wpath)))

    rpath = os.path.join(TMP, "risk11.json")
    with io.open(rpath, "w", encoding="utf-8") as f:
        f.write("[1,2,3]")
    with _FailQuarantine(rpath):
        g = R.RiskGate(path=rpath, event_path=os.path.join(TMP, "ev11.jsonl"))
        g2 = R.RiskGate(path=rpath, event_path=os.path.join(TMP, "ev11b.jsonl"))
    check("⑪(c) risk：留证失败 ⇒ 依旧 fail-closed（暂停）且原档没被覆盖",
          g.is_paused() is True and _read(rpath) == "[1,2,3]", repr(_read(rpath)))

    hpath = os.path.join(TMP, "hol11.json")
    with io.open(hpath, "w", encoding="utf-8") as f:
        f.write("{ 半截")
    _real_hp = H.state_path
    H.state_path = lambda: hpath
    try:
        with _FailQuarantine(hpath):
            H.mark_greeted("2026-10-01", "群deepseek")
        h_ok = (not os.path.exists(hpath + ".bad.json"))
    finally:
        H.state_path = _real_hp
    check("⑪(c) holidays：留证失败 ⇒ 拒绝写（原档保持原样）",
          _read(hpath) == "{ 半截" and h_ok, repr(_read(hpath)))

    # (d) 阳性对照：正常坏档（留证**成功**）时，写侧照常工作（没把整条路堵死）
    p2 = os.path.join(TMP, "normal11.json")
    with io.open(p2, "w", encoding="utf-8") as f:
        f.write("{ 半截")
    got3, ok_over3 = P.load_checked(p2, "默认")
    check("⑪(d) 阳性对照：能留证时 `可覆盖=True`（不许一刀切全拒）",
          ok_over3 is True and got3 == "默认" and len(glob.glob(p2 + ".bad.*")) == 1,
          "ok=%s bads=%s" % (ok_over3, glob.glob(p2 + ".bad.*")))


# ── ⑫ risk 形状洞必须 fail-closed（V-R10-25）────────────────────────────
def _s12_risk_shape_hole(TMP):
    print("== ⑫ V-R10-25：risk 顶层形状不对（list/str/null/空 dict）⇒ 必须 fail-closed ==")
    cases = [("[]", "list"), ('"hello"', "str"), ("null", "null"), ("{}", "空 dict"), ("123", "数字")]
    for payload, label in cases:
        p = os.path.join(TMP, "shape_%s.json" % label)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(payload)
        g = R.RiskGate(path=p, event_path=os.path.join(TMP, "ev12.jsonl"))
        v = g.check("group:x", "你好")
        check("⑫ 顶层是%s ⇒ paused=True（老写法这里是 False＝闸门放行）" % label,
              g.is_paused() is True, "payload=%s paused=%s" % (payload, g.is_paused()))
        check("⑫ 顶层是%s ⇒ check() 真的拦下（code=paused）" % label,
              (not v.allowed) and v.code == "paused", repr(v))
        check("⑫ 顶层是%s ⇒ 原因对用户说清了（不是空串）" % label,
              bool(g.snapshot().get("paused_reason")), g.snapshot().get("paused_reason"))
        check("⑫ 顶层是%s ⇒ 形状不对=坏档，已留证（`{}`/`null` 也不放过）" % label,
              len(glob.glob(p + ".bad.*")) == 1, str(glob.glob(p + ".bad.*")))

    # 键值类型错（`paused` 是字符串）同样是形状不对 ⇒ fail-closed，不许"当 True"或"当 False"
    p = os.path.join(TMP, "shape_keytype.json")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write('{"paused": "yes", "blocks": 3}')
    g = R.RiskGate(path=p, event_path=os.path.join(TMP, "ev12b.jsonl"))
    check("⑫ 键值类型不对（paused 是字符串）⇒ fail-closed + 留证",
          g.is_paused() is True and len(glob.glob(p + ".bad.*")) == 1,
          "paused=%s bads=%s" % (g.is_paused(), glob.glob(p + ".bad.*")))

    # 阳性对照：形状**正确**的状态档照常载入（不许把正常档也判成坏档）
    p2 = os.path.join(TMP, "shape_ok.json")
    with io.open(p2, "w", encoding="utf-8") as f:
        json.dump({"paused": True, "paused_reason": "连续 5 次被拦", "blocks": 5,
                   "min": [], "hour": [], "day": [], "day_key": "", "chats": {},
                   "events": [], "recent": []}, f)
    g2 = R.RiskGate(path=p2, event_path=os.path.join(TMP, "ev12c.jsonl"))
    check("⑫ 阳性对照：形状正确的档照常载入（paused=True 被读回来）",
          g2.is_paused() is True and glob.glob(p2 + ".bad.*") == [], str(glob.glob(p2 + ".bad.*")))
    p3 = os.path.join(TMP, "shape_false.json")
    with io.open(p3, "w", encoding="utf-8") as f:
        json.dump({"paused": False, "blocks": 0, "min": [], "hour": [], "day": [],
                   "day_key": "", "chats": {}, "events": [], "recent": []}, f)
    g3 = R.RiskGate(path=p3, event_path=os.path.join(TMP, "ev12d.jsonl"))
    check("⑫ 阳性对照：`paused=False` 的合法档不被误判成坏档，闸门放行",
          g3.is_paused() is False and g3.check("group:x", "你好").allowed
          and glob.glob(p3 + ".bad.*") == [], str(glob.glob(p3 + ".bad.*")))


# ── ⑬ fail-closed 之后的一键恢复（V-R10-24）─────────────────────────────
def _s13_risk_oneclick_recover(TMP):
    print("== ⑬ V-R10-24：fail-closed 不许把用户锁死——要有一键恢复路径 ==")
    p = os.path.join(TMP, "rec13.json")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("{ 半截")
    g = R.RiskGate(path=p, event_path=os.path.join(TMP, "ev13.jsonl"))
    check("⑬ 前置：坏档 ⇒ 确实被锁住（fail-closed）",
          g.is_paused() is True and not g.check("group:x", "你好").allowed)
    snap = g.recover()
    check("⑬ `recover()` 之后 paused=False（一键恢复真的解开了）", snap.get("paused") is False, str(snap)[:80])
    check("⑬ `recover()` 之后 check() 放行", g.check("group:x", "你好").allowed)
    check("⑬ `recover()` 之后 blocks 归零", int(snap.get("blocks") or 0) == 0, str(snap.get("blocks")))
    check("⑬ 模块级也有一键恢复入口（控制台/脚本能直接调）", callable(getattr(R, "recover", None)))
    check("⑬ 受控的闸门本体（`POST /api/risk` action=resume 走的就是它）仍是活的",
          callable(getattr(g, "resume", None)))

    # "被拦升级导致暂停"也必须能一键恢复（不是只有坏档那一种）
    p2 = os.path.join(TMP, "rec13b.json")
    g2 = R.RiskGate(path=p2, event_path=os.path.join(TMP, "ev13b.jsonl"))
    g2.pause("连续 5 次被风险闸门拦下")
    check("⑬ 人工/自动暂停之后同样能一键恢复",
          g2.is_paused() is True and g2.recover().get("paused") is False
          and g2.check("group:x", "你好").allowed)

    # 两套停机开关的同步（V-R10-24 的另一半：控制台的勾选框/按钮不再是"另一套"）
    check("⑬ 源码级锚：risk 会去读控制台的暂停标记（`control.is_paused`）",
          SM.has(_read(os.path.join(ROOT, "agent", "risk.py")), "control"))
    check("⑬ 源码级锚：`recover()` 同时清两套开关（写回 `set_paused_flag(False)`）",
          SM.has(_read(os.path.join(ROOT, "agent", "risk.py")), "set_paused_flag"))

    print("\n== ⑭ V-R10-26：文本也能原子写 · webui 不再就地重写 ==")
    _t14 = tempfile.mkdtemp(prefix="pm-persist14-")
    _t14p = os.path.join(_t14, "log.jsonl")
    check("⑭ `atomic_write_text` 写入正确且返回 True",
          P.atomic_write_text(_t14p, "a\nb\n", newline="\n") is True
          and io.open(_t14p, encoding="utf-8").read() == "a\nb\n")
    _t14old = io.open(_t14p, encoding="utf-8").read()
    _orig_rr = P._replace_retry
    try:
        P._replace_retry = lambda tmp, path: False
        _ok14 = P.atomic_write_text(_t14p, "坏内容", newline="\n")
    finally:
        P._replace_retry = _orig_rr
    check("⑭ 换档失败 ⇒ 返回 False 且**原档一个字节没动**（不许写半截）",
          _ok14 is False and io.open(_t14p, encoding="utf-8").read() == _t14old)
    _left14 = [f for f in os.listdir(_t14) if f.endswith(".tmp")]
    check("⑭ 失败后**自己的临时档已被清掉**（不留孤儿）", _left14 == [], str(_left14))
    _p14src = _read(os.path.join(ROOT, "agent", "persist.py"))
    check("⑭ 两个原子写都用**唯一临时名**（pid + 随机段），不是共用的 `<path>.tmp`",
          _p14src.count("secrets.token_hex(4)") >= 2)
    # webui：这一族"就地重写"是老毛病（写一半断电/并发 ⇒ 半截 JSON；V-R10-26 点名 8 处）
    _w14 = _read(os.path.join(ROOT, "agent", "webui.py"))
    check("⑭ webui 里**没有**就地重写（`open(<数据档>, \"w\")` ⇒ 0 处）",
          not re.search(r'with open\((_p|cats_p|pers_p), "w"', _w14))
    check("⑭ webui 的落盘全走 `persist.atomic_write_*`（≥8 处）",
          (_w14.count("persist.atomic_write_json(") + _w14.count("persist.atomic_write_text(")) >= 8)
    check("⑭ 而且**每一处都看返回值**（原子写失败是返回 False、不抛 ⇒ 不看就变成"
          "「失败了还报成功」）",
          (_w14.count("if not persist.atomic_write_json(")
           + _w14.count("if not persist.atomic_write_text(")) >= 8)
    check("⑭ 反例锚：老写法（`with open(_p, \"w\")` + `json.dump`）用**同一条判据**判不合格",
          bool(re.search(r'with open\((_p|cats_p|pers_p), "w"',
                         'with open(_p, "w", encoding="utf-8") as f:\n    _json.dump(x, f, indent=1)\n')))
    shutil.rmtree(_t14, ignore_errors=True)

    print("\n== ⑯ V-R11-7：顶栏『恢复』解开坏档 fail-closed 的锁 ⇒ 必须**落盘 + 留痕** ==")
    # ⛔ 现场（第十一轮 P2）：`risk._sync_operator` 的恢复分支**只改内存不落盘** ⇒ 重启又粘上暂停
    #   （用户看到"恢复了又自己停了"却查不出原因）；而且它解开的是坏档 fail-closed 的锁，
    #   解开了却**不留痕** ⇒ 事后无从判断"这个暂停本来是坏档引起的、被人顶开了"。
    _t16 = tempfile.mkdtemp(prefix="pm-persist16-")
    _st16 = os.path.join(_t16, "risk_state.json")
    _ev16 = os.path.join(_t16, "risk_events.jsonl")
    _saved_state16 = R.STATE_PATH
    try:
        from agent import control as _ctl16                            # noqa: E402
        _saved_ip16, _saved_spf16 = _ctl16.is_paused, _ctl16.set_paused_flag
        R.STATE_PATH = _st16
        _ctl16.is_paused = lambda: False          # 顶栏此刻已回到「恢复」态（＝跳变的另一半）
        _ctl16.set_paused_flag = lambda *a, **k: None
        _n_cap16 = len(CAP.msgs)
        _g16 = R.RiskGate(path=_st16, event_path=_ev16)
        _g16._fail_closed("夹具：风险状态文件读不出来")     # 造出"坏档 fail-closed 的暂停"
        check("⑯a 夹具到位：坏档 ⇒ paused=True 且标记 fail_closed（这次暂停的**来源**是可读的）",
           _g16.snapshot().get("paused") is True and _g16.snapshot().get("fail_closed") is True,
           str(_g16.snapshot())[:120])
        _g16._flag_seen = True                    # 上一轮看到的标记是「暂停」⇒ 现在消失＝有人按了恢复
        _g16.is_paused()                          # 触发 _sync_operator
        _on_disk16 = json.load(io.open(_st16, encoding="utf-8"))
        check("⑯b 一键恢复**落盘**（老写法：内存变了、盘上还是 paused=True ⇒ 重启又粘住）",
           _on_disk16.get("paused") is False and _on_disk16.get("fail_closed") is False,
           str({k: _on_disk16.get(k) for k in ("paused", "fail_closed", "recovered_by_operator")}))
        check("⑯c 留痕：盘上记下「被操作者解开过」（`recovered_by_operator` 计数 ≥1）",
           int(_on_disk16.get("recovered_by_operator") or 0) >= 1,
           str(_on_disk16.get("recovered_by_operator")))
        _msgs16 = " ".join(CAP.msgs[_n_cap16:])
        check("⑯d 日志里说清「解的是坏档那把锁」（用户/我们事后能归因，不是一句「已恢复」）",
           ("坏档" in _msgs16) and ("恢复" in _msgs16), _msgs16[-160:])
        check("⑯e 坏档**留证不丢**（恢复只解锁，不改写/不删除原档保护）",
           bool(_g16.snapshot().get("fail_closed")) is False and os.path.exists(_st16) is True)
        # 反例锚：老写法（只改内存）
        _st16b = os.path.join(_t16, "risk_state_old.json")
        with io.open(_st16b, "w", encoding="utf-8") as _f16:
            json.dump({"paused": True, "paused_reason": "夹具：坏档", "blocks": 3,
                       "min": [], "hour": [], "day": [], "day_key": "", "chats": {},
                       "events": [], "recent": [], "fail_closed": True}, _f16)
        _old_st16 = json.load(io.open(_st16b, encoding="utf-8"))
        _old_st16["paused"] = False                    # ⬅ 老写法：只改内存
        _old_st16["paused_reason"] = ""
        _after16 = json.load(io.open(_st16b, encoding="utf-8"))
        check("⑯f 反例锚：老写法（只改内存、不落盘）⇒ 盘上仍是 paused=True（重启就粘回来）",
           _after16.get("paused") is True)
    finally:
        R.STATE_PATH = _saved_state16
        try:
            _ctl16.is_paused, _ctl16.set_paused_flag = _saved_ip16, _saved_spf16
        except Exception:
            pass
        shutil.rmtree(_t16, ignore_errors=True)

    print("\n== ⑮ V-R10-24 收尾：`recover()` 必须有真调用者（一键恢复） ==")
    _ui15 = _read(os.path.join(ROOT, "agent", "webui.py"))
    _ch15 = _read(os.path.join(ROOT, "agent", "console_html.py"))
    check("⑮ `POST /api/risk` 支持 `action=recover`（不再只是模块里的死函数）",
          SM.has(_ui15, 'act == "recover"') and SM.has(_ui15, "_risk.recover()"))
    check("⑮ 控制台点「恢复」时补一发 recover（两套停机开关一起清）",
          SM.has(_ch15, "postJSON('/api/risk', {action: 'recover'})"))
    _OLDCH15 = "await getJSON('/api/resume', {method:'POST'});"        # 老写法：控制台只打 resume
    _OLDU15 = 'elif act == "resume":\n            _risk.resume()'       # 老写法：/api/risk 没有 recover 档
    check("⑮ 反例锚：老写法（控制台只打 /api/resume、`/api/risk` 只有 pause/resume）"
          "用**同一条判据**判不合格",
          (not SM.has(_OLDCH15, "postJSON('/api/risk'")) and (not SM.has(_OLDU15, 'act == "recover"')))


if __name__ == "__main__":
    main()
    print("\n== 持久化判据（V-R9-18/19/20/22 · V-R10-22/23/24/25）：%d 通过 / %d 失败 ==" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
