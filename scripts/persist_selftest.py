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
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


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
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"group:a": 100, "group:b": "坏值", "group:c": 55, "group:d": "12"}, f)
    n_before = len(_hits("坏条目"))
    wm = LW.Watermark(p)
    check("⑧ 坏条目只丢自己（另一个会话的水位原样保留）",
          wm.get("group:a") == 100 and wm.get("group:c") == 55, str(wm.data))
    check("⑧ 坏条目本身回 0", wm.get("group:b") == 0, str(wm.data))
    check("⑧ 反例锚：老写法（整表推导式）在这里会把 group:a/group:c 一起归零", wm.data == {"group:a": 100, "group:c": 55, "group:d": 12}, str(wm.data))
    check("⑧ 日志里**如实说了丢几条**",
          len(_hits("坏条目")) > n_before
          and any(SM.has(m, "1 条坏条目") for m in _hits("坏条目")),
          str(_hits("坏条目")[:1]))
    check("⑧ 数字字符串被认下来（不是一刀切当坏值）", wm.get("group:d") == 12)

    wm.set("group:a", 200)
    check("⑧ flush 走原子写：文件合法且不留 .tmp",
          wm.flush() and json.load(io.open(p, encoding="utf-8"))["group:a"] == 200
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
    check("⑨ 读侧都走 load_or_quarantine",
          all(SM.has(files[n], "load_or_quarantine") for n in five),
          str([n for n in five if not SM.has(files[n], "load_or_quarantine")]))
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


if __name__ == "__main__":
    main()
    print("\n== 持久化判据（V-R9-18/19/20/22）：%d 通过 / %d 失败 ==" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
