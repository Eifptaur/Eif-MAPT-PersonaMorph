# -*- coding: utf-8 -*-
"""过滤链自测（不需要微信、不联网）：分级/标签/图源/尺寸/肤色比/视觉审核 逐道验，被拒必记录、绝不放过。
用法：py -3 scripts/image_filter_selftest.py
"""
import json
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

from PIL import Image, ImageDraw                  # noqa: E402

from agent import image_filter as fl              # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s  %s" % (name, detail))


root = tempfile.mkdtemp(prefix="imgfilter_")
img_dir = os.path.join(root, "imgs")
os.makedirs(img_dir, exist_ok=True)


def mk(name, color=(120, 160, 210), size=(600, 600), skin=False):
    p = os.path.join(img_dir, name)
    im = Image.new("RGB", size, color)
    if skin:                                   # 大面积肤色（触发肤色比过滤器）
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, size[0], size[1]], fill=(226, 178, 152))
    im.save(p)
    return p


plain = mk("plain.png")
skinny = mk("skin.png", skin=True)
tiny = mk("tiny.png", size=(80, 80))

base_cfg = {"image_reply": {"enabled": True, "mode": "online", "sources": ["pixiv", "konachan"],
                            "safe_only": True, "allow_questionable": False, "vision_filter": False,
                            "skin_max_ratio": 0.45, "min_side": 300, "max_mb": 8}}
good_meta = {"source": "pixiv", "rating": "safe", "tags": ["scenery", "sky"]}

print("① 正常图：全链通过（视觉审核已关）")
r = fl.check(plain, good_meta, base_cfg, root=root)
ck("通过", r["ok"], r)
ck("trace 覆盖 6 道", len(r["trace"]) == 6, [t[0] for t in r["trace"]])

print("② 分级硬约束")
cfg = {"image_reply": dict(base_cfg["image_reply"])}
for rating, want in (("explicit", False), ("questionable", False), ("safe", True)):
    r = fl.check(plain, dict(good_meta, rating=rating), cfg, root=root)
    ck("rating=%s ⇒ %s" % (rating, "放过" if want else "拦下"), r["ok"] is want, r.get("reason"))
cfg2 = {"image_reply": dict(base_cfg["image_reply"], allow_questionable=True)}
r = fl.check(plain, dict(good_meta, rating="questionable"), cfg2, root=root)
ck("显式放宽后 questionable 放过", r["ok"], r.get("reason"))

print("③ 标签黑名单（内置 + 自定义）")
for tag in ("r18", "nsfw", "hentai", "エロ", "全裸"):
    r = fl.check(plain, dict(good_meta, tags=["scenery", tag]), base_cfg, root=root)
    ck("标签 %s 被拦" % tag, (not r["ok"]) and r["rejected_by"] == "tag_blacklist", r.get("reason"))
cfg3 = {"image_reply": dict(base_cfg["image_reply"], tag_blacklist=["scenery"])}
r = fl.check(plain, good_meta, cfg3, root=root)
ck("自定义黑名单生效", (not r["ok"]) and r["rejected_by"] == "tag_blacklist", r.get("reason"))

print("④ 图源允许清单")
cfg4 = {"image_reply": dict(base_cfg["image_reply"], sources=["konachan"])}
r = fl.check(plain, good_meta, cfg4, root=root)
ck("不在清单里的图源被拦", (not r["ok"]) and r["rejected_by"] == "source_allow", r.get("reason"))

print("⑤ 尺寸 / 分辨率")
r = fl.check(tiny, good_meta, base_cfg, root=root)
ck("太小的图被拦", (not r["ok"]) and r["rejected_by"] == "geometry", r.get("reason"))
bad = os.path.join(img_dir, "notimage.png")
open(bad, "w").write("这不是图片")
r = fl.check(bad, good_meta, base_cfg, root=root)
ck("非图片被拦", (not r["ok"]) and r["rejected_by"] == "geometry", r.get("reason"))

print("⑥ 肤色比启发式")
r = fl.check(skinny, good_meta, base_cfg, root=root)
ck("大面积肤色被拦", (not r["ok"]) and r["rejected_by"] == "skin_ratio", r.get("reason"))
cfg5 = {"image_reply": dict(base_cfg["image_reply"], skin_max_ratio=0)}
r = fl.check(skinny, good_meta, cfg5, root=root)
ck("肤色比关掉后放过", r["ok"], r.get("reason"))

print("⑦ 视觉审核：SAFE 放过 / UNSAFE 拦下 / 说不清也拦下（fail-closed）")
orig = fl.vision_check
try:
    for ans, want in (("SAFE", True), ("UNSAFE", False), ("嗯……说不好", False)):
        fl.vision_check = lambda p, c, r=None, _a=ans: (_a == "SAFE", "模型回答 %s" % _a)
        cfg6 = {"image_reply": dict(base_cfg["image_reply"], vision_filter=True)}
        r = fl.check(plain, good_meta, cfg6, root=root)
        ck("模型答 %r ⇒ %s" % (ans, "放过" if want else "拦下"), r["ok"] is want, r.get("reason"))
finally:
    fl.vision_check = orig
cfg7 = {"image_reply": dict(base_cfg["image_reply"], vision_filter=True, vision_fail_open=True)}
try:
    fl.vision_check = lambda p, c, r=None: (False, "模型挂了")
    r = fl.check(plain, good_meta, cfg7, root=root)
    ck("失败放行开关生效", r["ok"], r.get("reason"))
finally:
    fl.vision_check = orig

print("⑧ 被拒必记录 + 记录上限 200 条")
log = os.path.join(root, fl.REJECT_LOG_REL)
lines = open(log, encoding="utf-8").read().strip().split("\n")
ck("reject 日志有内容", len(lines) >= 5, len(lines))
rec = json.loads(lines[-1])
ck("记录含过滤器/原因/分级", all(k in rec for k in ("filter", "reason", "rating")), list(rec.keys()))
for i in range(210):
    fl._log_reject(plain, good_meta, base_cfg, "tag_blacklist", "压测 %d" % i, {}, root)
lines = open(log, encoding="utf-8").read().strip().split("\n")
ck("日志上限 200 条", len(lines) <= 200, len(lines))

print("⑨ summary 给控制台的状态")
s = fl.summary(base_cfg, root)
ck("包含已启用过滤器名", "source_rating" in s["enabled"] and "vision" not in s["enabled"], s["enabled"])
ck("包含被拒总数", s["rejected_total"] > 0, s["rejected_total"])

shutil.rmtree(root, ignore_errors=True)
print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
