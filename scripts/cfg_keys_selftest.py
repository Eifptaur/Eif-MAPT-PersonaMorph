#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""死键判据（队列 ③ 收口）：默认配置里**声明了却没有任何地方执行**的开关，必须是 0 个。

为什么要有这条：配置里写着一个开关、代码里从来不读，用户以为关了/开了有用 —— 这是"配置撒谎"。
2026-09-13 全量审计（229 个叶子键）抓出 6 个真死键，处置＝1 接线 + 5 删除；本判据守住"不再长回来"。

判据（不需要微信、不起服务、不出网）：
  ① 扫描：默认配置叶子键里，**服务端没读、全仓也找不到任何引用**的键 ⇒ 必须为空（逐条打印）
  ② 回归：本次删掉的键不得回填 `config.py` / `config.example.json`
  ③ 接线有效：`security.allow_private_image_hosts` 真起作用 —— 关＝内网图源被拒，开＝放行到连接阶段
  ④ 判据不瞎：往默认配置里塞一个假死键，扫描器必须抓得到（否则①永远是绿的）
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import config as C  # noqa: E402

SCAN_DIRS = ("agent", "scripts", "launcher-src")
SCAN_ROOT_FILES = ("persona_morph.py", "onestart.py")
TEXT_EXT = (".py", ".js", ".html", ".cs", ".css", ".json", ".md")
SERVER_READ_RE = re.compile(r"""\[\s*["']([a-z_][a-z0-9_]*)["']\s*\]|\.get\(\s*["']([a-z_][a-z0-9_]*)["']""")


def flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = ("%s.%s" % (prefix, k)) if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def _blobs():
    out = {}
    for d in SCAN_DIRS:
        for dirpath, _dirs, files in os.walk(d):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(TEXT_EXT):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    out[p.replace("\\", "/")] = open(p, encoding="utf-8", errors="ignore").read()
                except OSError:
                    pass
    for fn in SCAN_ROOT_FILES:
        if os.path.exists(fn):
            out[fn] = open(fn, encoding="utf-8", errors="ignore").read()
    return out


def dead_keys(default: dict, blobs: dict) -> list:
    """返回「服务端没读、全仓无引用」的配置键（即真死键）。"""
    server_read = set()
    for p, text in blobs.items():
        if not p.endswith(".py") or p.endswith("agent/config.py"):
            continue
        for m in SERVER_READ_RE.finditer(text):
            server_read.add(m.group(1) or m.group(2))
    dead = []
    for k in sorted(flatten(default)):
        leaf = k.split(".")[-1]
        if leaf in server_read:
            continue
        if any(re.search(r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(leaf), t)
               for p, t in blobs.items() if not p.endswith("agent/config.py")):
            continue
        dead.append(k)
    return dead


BLOBS = _blobs()

print("── A. 死键扫描（默认配置叶子键 %d 个）──" % len(flatten(C.DEFAULT_CONFIG)))
dead = dead_keys(C.DEFAULT_CONFIG, BLOBS)
ok("默认配置里没有「谁都不读」的键", not dead, "、".join(dead) if dead else "0 个")

print("── B. 已删键不得回填 ──")
REMOVED = ("memory.use_chat_model", "memory.provider", "memory.model",
           "scoring.import_seed_file", "ui.poke_degraded", "ui.poke_fail_count", "ui.moments_entry")
leaves = flatten(C.DEFAULT_CONFIG)
for k in REMOVED:
    ok("config.py 不含 %s" % k, k not in leaves)
example_json = json.load(open("config.example.json", encoding="utf-8"))


def has_path(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


for k in REMOVED:
    ok("config.example.json 不含 %s" % k, not has_path(example_json, k))

print("── C. 接线有效：security.allow_private_image_hosts ──")
from agent import image_sources as IS  # noqa: E402
from agent import config as _cfgmod  # noqa: E402

_real_get = _cfgmod.get_config
try:
    _cfgmod.get_config = lambda: {"security": {"allow_private_image_hosts": False}}
    ok("默认关：allow_private_hosts() = False", IS.allow_private_hosts() is False)
    blocked = False
    try:
        IS._get("http://127.0.0.1:9/probe.png", timeout_ms=1500)
    except RuntimeError as e:
        blocked = "安全策略拒绝" in str(e)
    except Exception:
        blocked = False
    ok("默认关：内网图源被 SSRF 闸门拒（RuntimeError）", blocked)

    _cfgmod.get_config = lambda: {"security": {"allow_private_image_hosts": True}}
    ok("显式开：allow_private_hosts() = True", IS.allow_private_hosts() is True)
    import urllib.error
    opened = False
    try:
        IS._get("http://127.0.0.1:9/probe.png", timeout_ms=1500)
    except RuntimeError as e:
        opened = "安全策略拒绝" not in str(e)
    except urllib.error.URLError:
        opened = True          # 过了闸门、卡在"连不上"就是我们要的证据
    except Exception:
        opened = True
    ok("显式开：放行到连接阶段（不再被闸门拦）", opened)
finally:
    _cfgmod.get_config = _real_get

print("── D. 判据不瞎（阴性对照）──")
# 注意：探针名必须**运行时拼出来** —— 写成字面量就会出现在被扫描的源码里，扫描器自然找得到它，
# 阴性对照会永远"抓不到"（本条第一版就是这么写的，被自己抓了个正着）。
_probe = "zz_dead_probe_" + "key"
fake = dict(C.DEFAULT_CONFIG)
fake["__probe__"] = {_probe: 1}
found = dead_keys(fake, BLOBS)
ok("塞进假死键能被抓到", any(k.endswith(_probe) for k in found), str(found[:3]))

# ── ⛔ 2026-09-21（第四轮审计 **V-R4-13**）：开关写成字符串时**不许反向打开** ──
#    `bool("false")` 是 **True** ⇒ 全项目几十处 `bool(cfg.get("开关"))` 会把本该关掉的红线开关
#    **反着打开**。修法＝`get_config()` 载入时**一处归一化** + 新代码用 `as_bool()`。
print("\n── J. 开关真值：\"false\" 不许被理解成开（V-R4-13）──")
import tempfile as _tmp2                                                       # noqa: E402

_p2 = os.path.join(_tmp2.mkdtemp(prefix="pm_cfg_j_"), "c.json")
with open(_p2, "w", encoding="utf-8") as _fh2:
    json.dump({"wechat": {"background_only": "false", "restore_minimized": "true",
                          "minimize_warning": "false"},
               "risk": {"block_keywords": ["off", "no", "true"]},
               "image_gen": {"enabled": "false"}}, _fh2, ensure_ascii=False)
_c2 = C.load_config(_p2)
ok("J1 写成字符串 `\"false\"` 的开关，载入后是**真布尔 False**",
   _c2["wechat"]["background_only"] is False and _c2["wechat"]["minimize_warning"] is False,
   repr(_c2["wechat"]["background_only"]))
ok("J2 `\"true\"` 载入后是 True（别只修一半）", _c2["wechat"]["restore_minimized"] is True)
ok("J3 嵌套开关一样管用（image_gen.enabled）", _c2["image_gen"]["enabled"] is False)
ok("J4 **列表一律不动**（`block_keywords` 里的 off/no/true 是真关键词，不许被转成布尔）",
   _c2["risk"]["block_keywords"] == ["off", "no", "true"], _c2["risk"]["block_keywords"])
ok("J5 `as_bool()`：false/FALSE/' no '/off/0 ⇒ 假；true/1 ⇒ 真；None ⇒ 走 default；"
   "**认不出来的值（[]/{} /'null'/'maybe'）⇒ 也走 default，绝不朝「开」倒**",
   C.as_bool("false") is False and C.as_bool("FALSE") is False and C.as_bool(" no ") is False
   and C.as_bool("off") is False and C.as_bool("0") is False
   and C.as_bool("true") is True and C.as_bool(1) is True and C.as_bool(None, True) is True
   and C.as_bool(None) is False
   # ⛔ 2026-09-21（第五轮回执 V-R5B-8）：这几条以前是 `bool(s)` ⇒ `[]` / `{}` / `"null"` 全变 True
   and C.as_bool([]) is False and C.as_bool({}) is False and C.as_bool("null") is False
   and C.as_bool("maybe") is False and C.as_bool("maybe", True) is True)
ok("J5b 反例锚：`bool([])` / `bool(\"null\")` 在裸 bool 下**都是真值** ⇒ 老写法就是这么把开关打开的",
   bool([]) is False and bool("null") is True and bool(str([])) is True)
ok("J6 载入路径真的调了归一化（源码级：`load_config` 里有 `_coerce_bool_strings`）",
   "_coerce_bool_strings(cfg)" in open(os.path.join(ROOT, "agent", "config.py"),
                                       encoding="utf-8").read())
ok("J7 反例锚：老写法 `bool(\"false\")` **确实是 True**（这就是「反向打开」的来历）",
   bool("false") is True and C.as_bool("false") is False)

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
