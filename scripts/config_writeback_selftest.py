# -*- coding: utf-8 -*-
"""「配置读/写回失败**只许告警、不许停用能力**」的判据（对标 CowAgent 教训 ③，第 ⑥ 项）。

**为什么要它**：对标 CowAgent 时读到一条它的真实事故 —— *配置写回失败曾导致插件被**永久停用***
（它 commit `198247c` 才修）。同一条口径映射到我们身上，`config.json` 恰好是最要命的那个档：
**版本门 / 后台档 / 口令 / 微信数据目录 / 各项功能开关全在里面** ⇒ 它一旦读不到或写不成，
"用户什么都没改、能力却全回到默认"这件事就发生了。而本项目原来的实现正好各自踩一脚：

  ① `save_config()` **自己拼 `<path>.tmp`**（临时名固定）。`config.json` 有四处并发写它的落点
     （控制台 `/api/config`、`/api/wechat/dir`、启动时的一次性迁移、后台线程里的档位标定）
     ⇒ 两个写者打开**同一个临时档**、内容互相穿插，`os.replace` 换上去的就是**半截/混合 JSON**
     ⇒ 下次启动读配置失败 ⇒ **全部能力回到默认**。
  ② `load_config()` 读一下就退回内置默认值，而 `get_config()` 在**指纹一变**（别人刚换过档）
     就会重读 ⇒ 换档那一瞬间的偶发 `PermissionError`（本机压测里真出现过）就能让内存里那份
     配置**整份作废**；控制台 `/api/config` 还是"先换内存、再落盘" ⇒ 写失败时盘与内存不一致。

判据分六段（每段都带**能红的反向锚**，否则它只是"跑得过的脚本"）：
  A 结构性：`save_config` 改走 `persist.atomic_write_json`；全仓固定临时名棘轮只许降
  B 行为·并发：真并发写同一个 `config.json`，读侧**永远读不到半截/混合档**
  C 机制锚（**确定性**，不靠概率）：把"两个写者共用一个固定临时档"的机制当场复现 ⇒
    判据自己的坏档检测器必须抓到它；同一套并发交给新配方 ⇒ 一份都不坏、临时档名互不相同
  D 失败语义：落盘失败 ⇒ **抛异常**（不许静默成功）、**原档一个字节不动**、临时档清干净
  E 读侧：瞬态占用**重试**；真读不到时**保留内存里那份**（反向锚＝老写法会退回默认值）
  F 接线（活体）：`/api/config` 落盘失败时**内存那份不许被换掉**（先落盘、成了再换内存）

跑法：runtime\\python\\python.exe scripts\\config_writeback_selftest.py
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _srcmatch as _sm          # noqa: E402

from agent import config as C    # noqa: E402
from agent import persist as PS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:                                                # noqa: BLE001
    pass

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


def _payload(tag, pad=3000):
    return {"pad": "x" * pad, "tag": tag,
            "sum": hashlib.sha1(tag.encode()).hexdigest()}


def _judge_file(path):
    """判据自己的坏档检测器：一份 config 必须自洽（tag 与 sum 对得上、能 json.load）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception as e:                                       # noqa: BLE001
        return False, "坏档（%s: %s）" % (type(e).__name__, str(e)[:40])
    if not isinstance(obj, dict):
        return False, "顶层不是对象"
    tag = str(obj.get("tag") or "")
    if hashlib.sha1(tag.encode()).hexdigest() != obj.get("sum"):
        return False, "内容混了（tag=%r 与 sum 对不上）" % tag[:16]
    return True, tag


def _old_style_shared_tmp(p, long_obj, short_obj):
    """老配方（固定临时名）在**两个并发写者**下的真实样子：两个句柄指着同一个临时档。"""
    tmp = p + ".tmp"
    fa = open(tmp, "w", encoding="utf-8")
    fb = open(tmp, "w", encoding="utf-8")
    json.dump(long_obj, fa, ensure_ascii=False, indent=2)     # 先写的（长）
    json.dump(short_obj, fb, ensure_ascii=False, indent=2)    # 后写的（短）⇒ 盖住头部、留下前者的尾巴
    fa.close()
    fb.close()
    os.replace(tmp, p)


def _old_load(path):
    """老写法读配置：**读一下就完**（读不到 ⇒ 上层退回内置默认值）。反向锚专用。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:                                            # noqa: BLE001
        return None


def _count_fixed_tmp():
    hits = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "agent"))):
        if not fn.endswith(".py") or fn == "persist.py":
            continue
        src = io.open(os.path.join(ROOT, "agent", fn), encoding="utf-8").read()
        for i, ln in enumerate(src.splitlines(), 1):
            s = ln.strip()
            if s.startswith("#") or '.tmp"' not in ln:
                continue
            hits.append("%s:%d" % (fn, i))
    return hits


def main():
    tmpdir = tempfile.mkdtemp(prefix="pm-cfgwb-")
    _orig_cfgfile = C.CONFIG_FILE
    try:
        print("== A. 结构性：落盘实现与棘轮 ==")
        _cfg_src = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
        ok("A1 `save_config` 改走 `persist.atomic_write_json`（不再自己拼临时名）",
           _sm.has(_cfg_src, "if not persist.atomic_write_json(p, cfg, indent=2):"))
        ok("A2 源码里不再有固定临时名（`p + \".tmp\"` 那种写法）",
           not _sm.has(_cfg_src, 'tmp = p + ".tmp"'))
        _hits = _count_fixed_tmp()
        ok("A3 全仓固定临时名实测 %d 处（基线曾 33；config.json 这一处被换掉 ⇒ 应 ≤ 32）" % len(_hits),
           len(_hits) <= 32, "超基线的：%s" % _hits[32:36])

        print("== B. 行为·并发：真并发写同一个 config.json，读侧读不到半截档 ==")
        p = os.path.join(tmpdir, "config.json")
        C.save_config(_payload("seed", 200), path=p)
        bad = []
        reads = [0]
        transient = [0]
        stop = threading.Event()
        lock = threading.Lock()

        def reader():
            while not stop.is_set():
                whole, why = _judge_file(p)
                if not whole and "PermissionError" in why:
                    # 换档那一瞬间的**瞬态占用**：产品侧的 `_read_config_once` 会重试，
                    # 所以这里也重试两下再记账——但"内容混了/半截"是**不许**被这样放过的。
                    time.sleep(0.01)
                    whole, why = _judge_file(p)
                    with lock:
                        transient[0] += 1
                with lock:
                    reads[0] += 1
                    if not whole:
                        bad.append(why)
                time.sleep(0.002)

        def worker(i):
            for j in range(15):
                try:
                    C.save_config(_payload("t%02d-%03d" % (i, j)), path=p)
                except Exception as e:                           # noqa: BLE001
                    with lock:
                        bad.append("写异常:%s" % type(e).__name__)

        ths = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        rt = threading.Thread(target=reader)
        rt.start()
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        stop.set()
        rt.join()
        ok("B1 4 线程 × 15 次并发写，读侧轮询 %d 次（其中 %d 次撞上瞬态占用、重试后都读到了）："
           "**一次半截/混合档都没有**" % (reads[0], transient[0]),
           not bad, str(sorted(set(bad))[:3]))
        _whole, _tag = _judge_file(p)
        ok("B2 收尾时盘上那份仍是**完整**的一份", _whole, _tag)

        print("== C. 机制锚：把「共用一个固定临时档」的机制当场复现，判据必须抓得住 ==")
        p2 = os.path.join(tmpdir, "old.json")
        C.save_config(_payload("seed", 200), path=p2)
        _old_style_shared_tmp(p2, _payload("A" * 8, 3000), _payload("B" * 8, 10))
        _whole2, _why2 = _judge_file(p2)
        ok("C1 老配方（固定临时名）在这套交错下**确实产出坏档**"
           "（这就是「能力被停用」的机制，不是纸面推测）", not _whole2, "竟然读到完整档：%s" % _why2)

        p3 = os.path.join(tmpdir, "new.json")
        C.save_config(_payload("seed", 200), path=p3)
        _bad3 = []
        _owner = {}                  # 临时档名 → 用它落盘的写者（线程）集合
        _orig_replace = PS.os.replace

        def _spy(src, dst):
            _owner.setdefault(os.path.basename(src), set()).add(threading.current_thread().name)
            return _orig_replace(src, dst)

        PS.os.replace = _spy
        try:
            _t3 = [threading.Thread(target=lambda i=i: [C.save_config(_payload("n%02d-%03d" % (i, j)), path=p3)
                                                        for j in range(6)], name="w%d" % i) for i in range(3)]
            for t in _t3:
                t.start()
            for t in _t3:
                t.join()
        finally:
            PS.os.replace = _orig_replace
        for _ in range(6):
            _w3, _t3s = _judge_file(p3)
            if not _w3:
                _bad3.append(_t3s)
            time.sleep(0.002)
        ok("C2 新配方同样并发：读回来的**每一份都完整**（反向对照 C1）", not _bad3, str(_bad3[:2]))
        _shared = {n: sorted(v) for n, v in _owner.items() if len(v) > 1}
        ok("C3 每个临时档**只属于一个写者**（%d 次落盘 / %d 个临时名；撞占重试会复用自己那一个）"
           % (sum(len(v) for v in _owner.values()), len(_owner)),
           not _shared and len(_owner) >= 18
           and all((".%d." % os.getpid()) in n for n in _owner),
           "被两个写者共用的：%s" % _shared)

        print("== D. 失败语义：抛异常 / 原档不动 / 临时档清干净 ==")
        p4 = os.path.join(tmpdir, "keep.json")
        C.save_config({"tag": "原件", "sum": "x"}, path=p4)
        _before = io.open(p4, "rb").read()
        _orig_retry = PS._replace_retry
        PS._replace_retry = lambda a, b: False          # 逼 os.replace 这一步失败（不睡退避）
        _raised = None
        try:
            try:
                C.save_config({"tag": "新值", "sum": "y"}, path=p4)
            except Exception as e:                               # noqa: BLE001
                _raised = e
        finally:
            PS._replace_retry = _orig_retry
        ok("D1 落盘失败**抛异常**（不许静默返回：否则「没保存」会被上报成「已保存」）",
           isinstance(_raised, OSError), repr(_raised)[:60])
        ok("D2 **原档一个字节都没动**（失败不许把用户的配置搞坏）",
           io.open(p4, "rb").read() == _before)
        _orphan = [n for n in os.listdir(tmpdir) if n.startswith("keep.json.") and n.endswith(".tmp")]
        ok("D3 失败时自己的临时档被清掉（不留孤儿）", not _orphan, str(_orphan))

        print("== E. 读侧：瞬态占用要重试；真读不到时保留内存里那份 ==")
        p5 = os.path.join(tmpdir, "retry.json")
        C.save_config({"tag": "要读到", "sum": "z"}, path=p5)
        import builtins
        _calls = {"n": 0}
        _flaky = None

        def _flaky(path, *a, **kw):
            if os.path.abspath(str(path)) == os.path.abspath(p5):
                _calls["n"] += 1
                if _calls["n"] <= 2:
                    raise PermissionError(13, "另一个进程正在换档（判据模拟）")
            return builtins._pm_real_open(path, *a, **kw)

        builtins._pm_real_open = builtins.open
        builtins.open = _flaky
        try:
            _got = C.load_config(p5)
            _n_e1 = _calls["n"]           # 记下来：下面反向锚要**重新从 0 开始**数
            _calls["n"] = 0               # 反向锚要**重新从 0 开始**：老写法第一次就会撞上占用
            _old = _old_load(p5)
        finally:
            builtins.open = builtins._pm_real_open
            del builtins._pm_real_open
        ok("E1 头两次读抛 PermissionError ⇒ **重试后仍拿到真内容**（不是退回默认值）",
           (_got or {}).get("tag") == "要读到" and _n_e1 >= 3,
           "tag=%r 读了几次=%d" % ((_got or {}).get("tag"), _n_e1))
        ok("E2 反向锚：不重试的老写法在这同一条件下**读不到**（证明 E1 是被修出来的）",
           _old is None, "老写法竟然也读到了：%r" % _old)

        _dir = os.path.join(tmpdir, "cfg-as-dir")
        os.makedirs(_dir, exist_ok=True)      # open(目录) 在 Windows 上必失败 ⇒ 稳定的「永久读不到」
        C.CONFIG_FILE = _dir
        C.set_config({"marker": "内存里这份", "api": {"model": "judge-model"}})
        _kept = C.reload_config()
        ok("E3 永久读不到时 `reload_config` **保留内存里那份**（能力不许被停用）",
           _kept.get("marker") == "内存里这份", "marker=%r" % _kept.get("marker"))
        _fresh = C.load_config(_dir)
        ok("E4 反向锚：直接 `load_config`（老路径）在这条件下**确实退回默认值** ⇒ 两条路真不一样",
           _fresh.get("marker") is None and "api" in _fresh, "marker=%r" % _fresh.get("marker"))
        C.CONFIG_FILE = _orig_cfgfile

        print("== F. 接线（活体）：/api/config 落盘失败时内存那份不许被换掉 ==")
        _webui_src = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        _i_save = _webui_src.find("                            save_config(new_cfg)")
        _i_set = _webui_src.find("                            set_config(new_cfg)")
        ok("F1 源码顺序＝**先 `save_config` 再 `set_config`**（盘与内存要么一起新、要么一起旧）",
           _i_save > 0 and _i_set > _i_save, "save@%d set@%d" % (_i_save, _i_set))
        import socket
        import urllib.error
        import urllib.request
        from agent import webui as W
        _s = socket.socket()
        _s.bind(("127.0.0.1", 0))
        _port = _s.getsockname()[1]
        _s.close()
        _saved = (W.get_config, W.save_config, W.set_config)
        _calls2 = {"set": 0, "save": 0}

        def _boom(*a, **k):
            _calls2["save"] += 1
            raise OSError("判据模拟：盘不可写")

        W.get_config = lambda: {"server": {"token": "cfgwb-token"}, "ui": {"judge_marker": 1}}
        W.save_config = _boom
        W.set_config = lambda *a, **k: _calls2.__setitem__("set", _calls2["set"] + 1)
        _w = None
        try:
            _w = W.WebUI(lambda: {}, [], on_save=None)
            _w.console_url_root = tmpdir
            _port = _w.start()
            _body = json.dumps({"ui": {"something": True}}).encode("utf-8")
            _req = urllib.request.Request("http://127.0.0.1:%d/api/config?token=cfgwb-token" % _port,
                                          data=_body, method="POST")
            _req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(_req, timeout=20) as _r:
                    _txt = _r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as _e:
                _txt = _e.read().decode("utf-8", "replace")
            ok("F2 落盘失败时控制台如实回 `ok:false`（用户看得见没保存）",
               _sm.has(_txt, '"ok": false'), _txt[:80])
            ok("F3 落盘失败时**内存那份没被换掉**（`set_config` 一次都没调）",
               _calls2["save"] == 1 and _calls2["set"] == 0,
               "save=%d set=%d" % (_calls2["save"], _calls2["set"]))
        finally:
            W.get_config, W.save_config, W.set_config = _saved
            try:
                if _w is not None:
                    _w.stop()
            except Exception:                                    # noqa: BLE001
                pass
    finally:
        C.CONFIG_FILE = _orig_cfgfile
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:                                        # noqa: BLE001
            pass

    print("== 配置写回判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
