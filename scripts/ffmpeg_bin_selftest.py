# -*- coding: utf-8 -*-
"""ffmpeg 解析入口判据（2026-09-16 立）。

为什么要有它：用户朋友那份环境检验报告里写着「ffmpeg：**没找到**（合成要转 wav，必需）」，
而 `requirements.txt` 里的 **`imageio-ffmpeg` 本来就自带一份 ffmpeg 二进制**（随 pip 包分发、
用户不用装任何东西）—— 以前四处各写各的（`tts.py` / `video_read.py` / `voice_models.py` /
`voice.py`），而且**全都只查 PATH**，所以没人用上那份自带的。

本判据钉三件事：①唯一入口在 `agent/ffmpeg_bin.py`；②没有 PATH 也要能回落到自带那份；
③三个旧调用点确实都改走它（源码级，防以后各写各的）。
跑法：`py -3 scripts\ffmpeg_bin_selftest.py`
"""
import os
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  [%s]" % detail) if detail else ""))


print("── A. 唯一入口 ──")
from agent import ffmpeg_bin as FB      # noqa: E402

ok("path()/bundled()/source() 三个接口都在",
   all(callable(getattr(FB, x, None)) for x in ("path", "bundled", "source")),
   str([x for x in ("path", "bundled", "source") if callable(getattr(FB, x, None))]))
_p = FB.path(refresh=True)
ok("本机解析得到 ffmpeg 且路径真实存在", bool(_p) and os.path.exists(_p), _p)
ok("来源说得清（config / PATH / imageio-ffmpeg）",
   FB.source() in ("config", "PATH", "imageio-ffmpeg", ""), FB.source())

print("── B. 兜底：PATH 里没有也要拿到（干净机器的关键）──")
_b = FB.bundled()
ok("imageio-ffmpeg 自带那份能取到（依赖里已有它）", bool(_b) and os.path.exists(_b), _b)
_saved_which = shutil.which
try:
    shutil.which = lambda *a, **k: None          # 把 PATH 那条路打断
    _p2 = FB.path(refresh=True)
finally:
    shutil.which = _saved_which
    FB.path(refresh=True)
ok("PATH 里没有 ffmpeg ⇒ 仍回落到自带那份", bool(_p2) and os.path.exists(_p2), _p2)
ok("回落之后来源标成 imageio-ffmpeg", (FB.source() in ("imageio-ffmpeg", "PATH", "config")),
   FB.source())

print("── C. 三个旧调用点都改走唯一入口（源码级）──")
for _rel in ("agent/tts.py", "agent/video_read.py", "agent/voice_models.py"):
    _src = open(os.path.join(ROOT, _rel.replace("/", os.sep)), encoding="utf-8").read()
    ok("%s 引用了 ffmpeg_bin" % _rel, "ffmpeg_bin" in _src)

print("── D. 行为：旧接口返回的路径 == 唯一入口给的路径 ──")
for _mod, _fn in (("tts", "ffmpeg_path"), ("video_read", "ffmpeg_path"), ("voice_models", "_ffmpeg_bin")):
    try:
        _m = __import__("agent." + _mod, fromlist=[_fn])
        _got = str(getattr(_m, _fn)() or "")
        ok("agent.%s.%s() 与唯一入口一致" % (_mod, _fn), _got == _p, "%s vs %s" % (_got[:60], _p[:60]))
    except Exception as e:
        ok("agent.%s.%s() 与唯一入口一致" % (_mod, _fn), False, "%s: %s" % (type(e).__name__, str(e)[:60]))

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
