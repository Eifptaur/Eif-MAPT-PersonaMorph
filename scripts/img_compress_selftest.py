#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大图自动压缩判据（对账清单第 22 条「对较大的图片进行自动压缩」）。

不需要微信、不出网：全部在临时文件上跑。
判据要点：大图必缩（最长边 ≤ 上限 / 字节数下降 / 宽高比保持）· 小图字节级不动 ·
关掉开关不动 · 压不动时**回退原图**且说明里讲清（不许静默）· 配置项能从 config 读进来。
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image  # noqa: E402
from agent import img_compress as IC  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


_orig_cfg = IC.cfg
CFG = dict(IC.DEFAULTS)


def set_cfg(**kw):
    CFG.clear()
    CFG.update(IC.DEFAULTS)
    CFG.update(kw)
    IC.cfg = lambda: dict(CFG)


tmp = tempfile.mkdtemp(prefix="imgc_")
# 造一张"大图"：4000×2000 的噪点图（压缩率低 ⇒ 文件真的大）
import random  # noqa: E402
big = os.path.join(tmp, "big.png")
im = Image.new("RGB", (4000, 2000))
px = im.load()
for y in range(0, 2000, 4):
    for x in range(0, 4000, 4):
        c = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for dy in range(4):
            for dx in range(4):
                px[x + dx, y + dy] = c
im.save(big)
small = os.path.join(tmp, "small.png")
Image.new("RGB", (320, 240), (90, 140, 200)).save(small)
bad = os.path.join(tmp, "bad.png")
open(bad, "w", encoding="utf-8").write("not an image")

print("① 大图要压（最长边/字节数/宽高比三条同时判）")
set_cfg(enabled=True, max_mb=0.5, max_px=1600, quality=82)
out, note = IC.compress_if_needed(big, out_dir=tmp)
with Image.open(big) as a:
    wa, ha = a.size
with Image.open(out) as b:
    wb, hb = b.size
ok("产出了新文件（不是原图）", out != big, os.path.basename(out))
ok("最长边 ≤ max_px", max(wb, hb) <= 1600, "%dx%d" % (wb, hb))
ok("字节数下降", os.path.getsize(out) < os.path.getsize(big),
   "%.2fMB→%.2fMB" % (os.path.getsize(big) / 1048576.0, os.path.getsize(out) / 1048576.0))
ok("宽高比保持（±2%）", abs((wb / float(hb)) - (wa / float(ha))) <= 0.02 * (wa / float(ha)),
   "原 %dx%d → %dx%d" % (wa, ha, wb, hb))
ok("说明里写清压缩前后", "已压缩" in note and "→" in note, note)

print("② 小图：字节级不动（不做无意义重编码）")
set_cfg(enabled=True, max_mb=8.0, max_px=1600, quality=82)
before = open(small, "rb").read()
out2, note2 = IC.compress_if_needed(small, out_dir=tmp)
ok("返回原路径", out2 == small, str(out2))
ok("文件字节没被改", open(small, "rb").read() == before)
ok("说明写明「本来就够小」", "够小" in note2, note2)

print("③ 关掉开关：什么都不做")
set_cfg(enabled=False)
out3, note3 = IC.compress_if_needed(big, out_dir=tmp)
ok("返回原路径", out3 == big)
ok("说明写明未开启", "未开启" in note3, note3)

print("④ 压不动/坏文件：回退原图 + 说明（不许静默、不许抛异常）")
set_cfg(enabled=True, max_mb=0.001, max_px=64, quality=82)
out4, note4 = IC.compress_if_needed(bad, out_dir=tmp)
ok("坏文件不抛异常且返回原路径", out4 == bad, note4[:40])
set_cfg(enabled=True, max_mb=0.0001, max_px=32, quality=82)
out5, note5 = IC.compress_if_needed(small, out_dir=tmp)
# 契约（不写死"一定更大"）：要么返回原图并说明原因，要么返回**更小**的新文件；不许"压完更大还发新的"。
_ok5 = (out5 == small and note5 != "") or (out5 != small and os.path.getsize(out5) < os.path.getsize(small))
ok("要么原样发并说明、要么只输出更小的文件（不许压完更大还换新文件）", _ok5, note5)

print("⑤ 配置：能从 config 读进来（send.image_compress.*）")
_cfg_src = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
ok("config.py 默认段里有 image_compress", "image_compress" in _cfg_src)
ok("snapshot() 给出开关与阈值", set(IC.snapshot().keys()) >= {"enabled", "max_mb", "max_px", "quality"})

print("⑥ 接线：发送前会调用压缩")
_w = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("send_image_posted 里调了 compress_if_needed", "compress_if_needed(local_path)" in _w)
ok("异常时回退原图（不改 local_path 之外的行为）", "压缩环节异常" in _w)

print("⑦ UI 映射：能力必须有可点的面（面板 + 键 + 只读状态）")
_html = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_ms = open(os.path.join(ROOT, "agent", "media_status.py"), encoding="utf-8").read()
for _k in ("enabled", "max_px", "max_mb", "quality"):
    ok("控制台绑定了 send.image_compress.%s" % _k, ('data-cfg="send.image_compress.%s"' % _k) in _html)
ok("面板文案写明「压不动就原样发送并说明」", "原样发送并在回执里说明" in _html)
ok("/api/status 的媒体段带 img_compress", "img_compress" in _ms)

IC.cfg = _orig_cfg
print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
