# -*- coding: utf-8 -*-
"""判据：剪贴板原语（图片/文件/文本）不需要微信，纯本地可测。
用法：py -3 scripts/clipboard_selftest.py
"""
import os
import shutil
import sys

try:      # 控制台默认 GBK：判据里的 ✔/✘ 一旦被重定向就 UnicodeEncodeError 崩掉整条判据
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image                     # noqa: E402

from agent import clipboard as cb         # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s  %s" % (name, detail))


root = tempfile.mkdtemp(prefix="clip_")
img = os.path.join(root, "t.png")
Image.new("RGB", (64, 48), (200, 60, 60)).save(img)

print("① 文本进出剪贴板（自证通路可用）")
ok, why = cb.set_text("剪贴板自测-ABC")
ck("写文本成功", ok, why)
ck("读回一致", cb.get_text() == "剪贴板自测-ABC", repr(cb.get_text()))

print("② 图片进剪贴板（CF_DIB）")
ok, why = cb.set_image(img)
ck("写图片成功", ok, why)

print("③ 文件进剪贴板（CF_HDROP）")
ok, why = cb.set_files([img])
ck("写文件成功", ok, why)

print("④ 坏输入：都要给可读原因、不抛异常")
ok, why = cb.set_image(os.path.join(root, "不存在.png"))
ck("不存在 ⇒ False + 文案", (not ok) and "不存在" in why, why)
ok, why = cb.set_files([])
ck("空列表 ⇒ False + 文案", (not ok) and "没有" in why, why)
ok, why = cb.set_files([os.path.join(root, "x.bin")])
ck("坏路径 ⇒ False + 文案", (not ok), why)

shutil.rmtree(root, ignore_errors=True)
print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
