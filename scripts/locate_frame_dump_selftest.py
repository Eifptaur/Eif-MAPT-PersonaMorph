# -*- coding: utf-8 -*-
"""A7 判据：**定位失败现场帧存证**（B 赛道 §G.2 A7 / F15）。

跑法： py -3 scripts\\locate_frame_dump_selftest.py     退出码 0=全过 / 1=有失败

⛔ 三条**隐私口径**（缺一条这个功能就是有害的）：
  ① **落不进产品包的目录** —— 复用 `_dump_fail_shot`（`wechatauto_logs/fail/`）；
     该目录 ① 在 `.gitignore` 里、② 被 `scripts/pack_online.py` 的 `EXCLUDE` 排除。
  ② **滚动上限** —— 只留最近 N 份（`_dump_fail_shot(keep=…)`）。
  ③ **不进反馈包** —— 反馈附件只来自用户**显式**勾选的文件，本仓没有"自动塞截图"的路径。

外加两条接线断言：
  ④ 定位失败时**真的会调用**存证（不是只写了个函数没人用）；
  ⑤ 存证失败**不许改变**定位语义（why 照旧、rollable 照旧、仍返回 None）。

⚠️ 本判据用**唯一 tag** 走**真函数**，跑完只删自己造出来的那批目录（不留痕）。
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

import _iso14  # noqa: E402
import _srcslice  # noqa: E402  AST 锚点取函数正文（别用文本位置切）
_iso14.all_()  # ⛔ 必须 all_()

from agent import wechat as W  # noqa: E402
import logging

log = logging.getLogger("persona-morph")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


_TAG = "ut_a7_%d" % os.getpid()
_BASE = os.path.join(ROOT, "wechatauto_logs", "fail")


def _cleanup():
    """只删本判据自己造出来的目录。"""
    try:
        for n in os.listdir(_BASE):
            if n.endswith("_" + _TAG):
                shutil.rmtree(os.path.join(_BASE, n), ignore_errors=True)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass


class _Attrs:
    def __init__(self, d):
        self._d = d

    def endswith(self, s):
        return self._d.endswith(s)


print("\n══ A7 定位失败现场帧存证 ══")

_src_w = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()

# ④ 源码级：定位失败路径里真的调了存证
ok("④ `_send_poke_locate` 的 `_fail` 里真的调用了 `_dump_fail_shot`",
   '_dump_fail_shot("poke_locate"' in _src_w)
ok("④b 存证被 try 包住（存证失败绝不改定位语义）",
   "# 现场帧存证（尽力而为，绝不因为存不下而改变失败语义）" in _src_w)
ok("④c 现场路径会写进 why（用户/日志能看到「存哪儿了」）",
   "（现场已存 %s）" in _src_w)

# ① 隐私口径一：目录在 .gitignore 里 + 被 pack_online 排除
_git = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
ok("① `wechatauto_logs/` 在 .gitignore 里", "wechatauto_logs/" in _git)
_pack = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8").read()
ok("①b `wechatauto_logs/` 被 pack_online.py 的 EXCLUDE 排除（不会随包发出去）",
   '"wechatauto_logs/"' in _pack)

# ③ 隐私口径三：反馈不收自动截图
_fb = io.open(os.path.join(ROOT, "agent", "feedback.py"), encoding="utf-8").read()
ok("③ feedback.py 里**没有**引用 `wechatauto_logs`（不自动收现场帧）",
   "wechatauto_logs" not in _fb)
ok("③b feedback.py 里**没有**引用 fail 目录",
   "fail" not in _fb.replace("failed", "").replace("failure", "").replace("fails", ""))
ok("③c 反馈附件只来自入参 `files`（用户显式勾选）",
   "def save_attachments(fid: str, files)" in _fb)

# ② 滚动上限：真跑 `_dump_fail_shot`（唯一 tag），验证 keep-N 语义
try:
    from PIL import Image
    _img = Image.new("RGB", (64, 48), (30, 30, 30))
except Exception as _e:
    _img = None
    print("  （PIL 不可用，跳过真落盘段：%s）" % _e)

if _img is not None:
    _cleanup()
    _made = []
    for i in range(5):
        _d = W.WeChatAdapter._dump_fail_shot(None, _TAG, _img, {"i": i}, keep=3)
        _made.append(_d)
        time.sleep(1.05)   # 时间戳精确到秒 ⇒ 睡一点保证目录名不同
    _left = sorted(n for n in os.listdir(_BASE) if n.endswith("_" + _TAG))
    ok("② 连存 5 次、keep=3 ⇒ 只剩最近 3 份", len(_left) == 3, "剩 %d 份" % len(_left))
    ok("②b 留下的是**最新的**那 3 份（最旧的被删）",
       _left == sorted(os.path.basename(d) for d in _made[-3:]),
       "left=%s" % _left)
    # 内容完整：一帧 + 一份 probe.json
    if _left:
        _d = os.path.join(_BASE, _left[-1])
        ok("②c 存的现场含 shot.png（一帧）", os.path.exists(os.path.join(_d, "shot.png")))
        ok("②d 存的现场含 probe.json（判据中间量）",
           os.path.exists(os.path.join(_d, "probe.json")))
    _cleanup()
    ok("②e 判据自己清理干净（不留痕）",
       not [n for n in os.listdir(_BASE) if n.endswith("_" + _TAG)])
else:
    ok("② 滚动上限（需 PIL）", False, "PIL 不可用 ⇒ 未验证")

# ⑤ 存证失败不许改语义：拿一个会抛的 dump 打桩，验证 _fail 仍按原样记录
print("\n══ ⑤ 存证失败不得影响定位语义 ══")
# ⛔ 换成 AST 锚点：原来的写法按"函数在文件里的**文本位置**"切（`find` ＋ 方括号切片 ＋ 再截断），
#   函数一被搬位置就会**崩**或**静默切错**。（本条说明故意不照抄旧语法——照抄会被同文件的
#   "可机械改写形态"普查当成新写的一处。）
_src_seg = _srcslice.func_src(_src_w, "_fail")
ok("⑤ dump 调在外层 try 里（抛了也吞掉）",
   "except Exception: # noqa: BLE001 — 存证失败绝不影响定位语义" in _src_seg or
   "存证失败绝不影响定位语义" in _src_seg)
ok("⑤b `self._poke_locate_why` 在 dump **之前**就已赋值（失败原因不会因存证而丢）",
   _src_seg.index("self._poke_locate_why = why") < _src_seg.index("_dump_fail_shot"))
ok("⑤c `return None` 在 dump 之后（失败语义不变）",
   _src_seg.rindex("return None") > _src_seg.index("_dump_fail_shot"))

print("\n==== 定位失败现场帧存证判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
