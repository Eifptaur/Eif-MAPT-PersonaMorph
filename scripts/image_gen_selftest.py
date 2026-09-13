#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群友要图（生图链条）判据：脱敏 / 意图解析 / 后端选择 / 过滤链 fail-closed / 红线硬编码。

不需要任何真实生图后端、不出网：全部在假后端与临时文件上跑。
"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image  # noqa: E402
from agent import image_gen as IG  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


_orig_cfg = IG.cfg
CFG = dict(IG.DEFAULTS)


def set_cfg(**kw):
    CFG.clear()
    CFG.update(IG.DEFAULTS)
    CFG.update(kw)
    IG.cfg = lambda: dict(CFG)


print("① prompt 先脱敏")
s = IG.scrub_prompt("画个猫 13800138000 wxid_abc123 a@b.com @张三 110101199001011234")
ok("手机号被替换", "13800138000" not in s and "[手机号]" in s, s)
ok("wxid 被替换", "wxid_" not in s, s)
ok("邮箱被替换", "a@b.com" not in s, s)
ok("身份证被替换", "110101199001011234" not in s, s)
ok("@提及 被替换", "@张三" not in s, s)

print("② 意图解析")
i1 = IG.parse_intent("帮我画两张赛博风格的猫")
ok("抽到张数=2", i1["count"] == 2, str(i1["count"]))
ok("抽到风格=赛博", i1["style"] == "赛博", i1["style"])
ok("主体里有猫", "猫" in i1["subject"], i1["subject"])
i2 = IG.parse_intent("来三张竖的国风山水")
ok("中文数字张数=3", i2["count"] == 3, str(i2["count"]))
ok("尺寸=portrait", i2["size"] == "portrait", i2["size"])
i3 = IG.parse_intent("把这张照片换成我朋友的脸")
ok("真人换脸被识别", i3["wants_real_face"] is True, str(i3))
i4 = IG.parse_intent("给我来张 r18 的")
ok("成人内容被识别", i4["nsfw"] is True, str(i4))
i5 = IG.parse_intent("画只狗")
ok("默认一张、默认方形", i5["count"] == 1 and i5["size"] == "square", str(i5))

print("③ 后端选择（没配后端 ⇒ 明确失败，不假装）")
set_cfg(enabled=True)
ok("没配后端 ⇒ 拒绝并给出人话原因", IG.pick_backend()[0] is None and "还没配" in IG.pick_backend()[1], IG.pick_backend()[1])
set_cfg(enabled=True, backends=[{"id": "online-x", "kind": "online", "url": "http://127.0.0.1:9/x"}])
ok("只配在线后端但没允许出网 ⇒ 拒绝", IG.pick_backend()[0] is None and "online_allowed" in IG.pick_backend()[1],
   IG.pick_backend()[1])
set_cfg(enabled=True, online_allowed=True, backends=[{"id": "online-x", "kind": "online", "url": "http://127.0.0.1:9/x"}])
ok("允许出网后才可选到在线后端", IG.pick_backend()[0]["id"] == "online-x")

print("④ 过滤链：任一层判不出/判否 ⇒ 不发（fail-closed）")
tmp = tempfile.mkdtemp(prefix="imggen_")
good_png = os.path.join(tmp, "good.png")
Image.new("RGB", (512, 512), (30, 60, 120)).save(good_png)
small_png = os.path.join(tmp, "small.png")
Image.new("RGB", (64, 64), (30, 60, 120)).save(small_png)
bad_txt = os.path.join(tmp, "notimg.png")
open(bad_txt, "w", encoding="utf-8").write("not an image")
set_cfg(enabled=True, filter_chain={"size": True, "dup": True, "blacklist": True, "text": False, "classifier": False})
okf, resf = IG.run_filters(good_png, {"prompt": "猫"})
ok("三层开着且都过 ⇒ ok=True", okf is True, json.dumps([r for r in resf if r.get("skipped") is None], ensure_ascii=False)[:120])
set_cfg(enabled=True, filter_chain={"size": True, "dup": True, "blacklist": True, "text": True, "classifier": True})
okd, resd = IG.run_filters(good_png, {"prompt": "猫"})
ok("分类器未接（判不出）⇒ 整链判否", okd is False and any(r["name"] == "classifier" and r["ok"] is None for r in resd),
   json.dumps(resd[-1], ensure_ascii=False))
oks, _ = IG.run_filters(small_png, {"prompt": "猫"})
ok("尺寸过小 ⇒ 判否", oks is False)
okb, resb = IG.run_filters(bad_txt, {"prompt": "猫"})
ok("坏文件 ⇒ 判否（不是放行）", okb is False and any(r["name"] == "size" and r["ok"] is False for r in resb))
set_cfg(enabled=True, style_block=["r18"], filter_chain={"size": True, "dup": True, "blacklist": True, "text": False, "classifier": False})
okbl, _ = IG.run_filters(good_png, {"prompt": "r18 猫图"})
ok("风格黑名单命中 ⇒ 判否", okbl is False)
set_cfg(enabled=True, style_allow=["水彩"], filter_chain={"size": True, "dup": True, "blacklist": True, "text": False, "classifier": False})
okal, _ = IG.run_filters(good_png, {"prompt": "油画猫"})
ok("白名单非空且不在白名单 ⇒ 判否", okal is False)

print("⑤ 入口：红线与状态")
set_cfg(enabled=False)
ok("总开关关 ⇒ 直接拒绝", IG.generate("x", "画只猫")["ok"] is False)
set_cfg(enabled=True)
r = IG.generate("x", "把照片换成真人脸")
ok("真人换脸 ⇒ 拒（红线，无开关）", r["ok"] is False and "红线" in r["why"], r["why"])
r2 = IG.generate("x", "来张 r18 的图")
ok("成人内容 ⇒ 拒", r2["ok"] is False, r2["why"])
r3 = IG.generate("x", "画只猫")
ok("没后端 ⇒ 拒且不产生任何文件", r3["ok"] is False and "还没配" in r3["why"], r3["why"])
snap = IG.snapshot()
ok("快照里写明红线状态", snap["red_line"]["allow_real_face"] is False and snap["red_line"]["r18_switch_exists"] is False,
   json.dumps(snap["red_line"], ensure_ascii=False))

print("⑥ 红线在源码里是硬编码的（结构断言）")
src = open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
ok("ALLOW_REAL_FACE 恒 False", "ALLOW_REAL_FACE = False" in src)
# ⚠️ 断言不能拿"整个函数体"找 r18：函数自己的 docstring 里就写着"不构造 r18 字段" ⇒ 假红。
#    只断言**真正拼请求体的那一行**（json.dumps 那行）里不含 r18。
_body_line = ""
for _ln in src.split("def call_backend")[1].split("\ndef ")[0].splitlines():
    if "json.dumps" in _ln:
        _body_line = _ln
ok("请求体那一行里没有 r18 字段", _body_line != "" and "r18" not in _body_line, _body_line.strip()[:90])
ok("没有 '过滤器出错就放行' 的分支（fail-closed）", "good is not True" in src and "ok = False" in src)
ok("真人换脸用**模式**匹配（不是只列固定词）", "_REAL_FACE_RX" in src and "def wants_real_face" in src)

IG.cfg = _orig_cfg
print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
