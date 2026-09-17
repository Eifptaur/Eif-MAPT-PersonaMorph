#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按关键词找图 + "触发条件可自定义" 判据（不需要联网、不需要微信）。

为什么有这条（用户 2026-09-13 问）：「还有帮用户找图发群里，还有发语音，能做到不，这些功能，模型会怎么用？
**触发条件你写好了吗？这些东西能不能交给用户自定义？**」

判据：
  ① `send_image_search(keyword)` 已注册并接了执行器；描述里写清"什么时候用"
  ② 功能没开 ⇒ 明确拒绝且**不发送**；关键词为空 ⇒ 报错；会话冷却内 ⇒ 不重复发
  ③ **关键词真的传到了图源**（注入假图源记录 tag）+ 过滤链复用（没通过就返回原因、不发送）
  ④ 发送走 `sender.send_image`（与"随机图"同一条通路）
  ⑤ **触发条件可自定义**：`image_reply.trigger_mode` / `voice_reply.trigger_mode` 三档
     （off / on_request / sometimes）真的改变提示词内容；`image_reply.allow_search=false` 时找图直接拒
  ⑥ 提示词里**发语音的触发条件也写了**（send_voice_reply 出现在场景规则里）
"""
import os
import sys
import tempfile

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


from agent import tools as TL  # noqa: E402
from agent import image_lib as IL  # noqa: E402
from agent import image_sources as IS  # noqa: E402
from agent import prompt as PR  # noqa: E402
from agent import config as CFG  # noqa: E402

print("── A. 工具注册 ──")
d = {x["name"]: x for x in TL.build_tool_defs()}
ok("注册了 send_image_search", "send_image_search" in d)
ok("描述里写了触发条件（有人明确说…时用）", "有人明确说" in str(d.get("send_image_search", {}).get("description")))
ok("描述里说明失败要照实说", "不要假装发过图" in str(d.get("send_image_search", {}).get("description")))
ok("参数要 keyword", "keyword" in (d.get("send_image_search", {}).get("parameters", {}).get("properties") or {}))


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_image(self, chat_key, path):
        self.sent.append(path)
        return True


def mkctx(sender=None):
    return {"store": None, "chat_key": "group:x", "chat_id": "x", "wechat": None,
            "sender": sender or FakeSender(), "session": {"sent": []}}


print("── B. 拒绝路径 ──")
_real_cfg = TL.get_config
try:
    TL.get_config = lambda: {"image_reply": {"enabled": False}}
    fs = FakeSender()
    r = TL._exec_send_image_search(mkctx(fs), {"keyword": "猫"})
    ok("功能没开 ⇒ 报错且不发送", r.get("is_error") is True and fs.sent == [], (r.get("content") or "")[:40])
    TL.get_config = lambda: {"image_reply": {"enabled": True, "min_gap_seconds": 20}}
    fs2 = FakeSender()
    r2 = TL._exec_send_image_search(mkctx(fs2), {"keyword": "  "})
    ok("关键词为空 ⇒ 报错且不发送", r2.get("is_error") is True and fs2.sent == [])
finally:
    TL.get_config = _real_cfg

print("── C. 关键词真的传给图源 + 过滤链复用 ──")
seen = {}
tmp = tempfile.mkdtemp(prefix="pm_imgsearch_")
fake_img = os.path.join(tmp, "a.jpg")
open(fake_img, "wb").write(b"\xff\xd8\xff" + b"x" * 300)
_rm, _rd, _rf = IS.fetch_meta, IS.download, IL._filter_or_reject
try:
    def _fake_meta(src, cfg=None):
        seen["tag"] = (cfg or {}).get("tag")
        return {"url": "https://x/y.jpg", "page": "p", "tags": []}, ""

    IS.fetch_meta = _fake_meta
    # ⚠️ 假图源的签名要跟着真实现走（2026-09-17：真实现多了 `soft_max_mb`，这里的假函数没跟上
    #    ⇒ 线程里抛 TypeError ⇒ 判据变成假红。用 **kw 兜住，别再被签名变化绊倒）
    IS.download = lambda url, dest, max_mb=8, timeout_ms=9000, **kw: (fake_img, "")
    IL._filter_or_reject = lambda path, meta, cfg, root, why: (path, "过了过滤链")
    p, why = IL.search_image({"image_reply": {"enabled": True, "sources": ["pixiv"], "allow_search": True,
                                              "tag": "", "max_mb": 8}}, "赛博朋克")
    # ⚠️ 2026-09-18 口径变更：关键词会**先翻成图源标签**再查（中文直接丢进去图源多半不认）
    ok("关键词翻译后作为 tag 传到图源（赛博朋克→cyberpunk）", seen.get("tag") == "cyberpunk",
       str(seen.get("tag")))
    ok("过滤链通过后返回路径", bool(p) and "过滤链" in why, str(why)[:30])
    p2, why2 = IL.search_image({"image_reply": {"enabled": True, "sources": ["pixiv"], "allow_search": False}}, "猫")
    ok("allow_search=false ⇒ 直接拒（不去图源）", p2 is None and "关掉" in why2, why2[:30])
finally:
    IS.fetch_meta, IS.download, IL._filter_or_reject = _rm, _rd, _rf
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print("── D. 发送与冷却 ──")
_real_cfg2 = TL.get_config
_real_search = IL.search_image
try:
    TL.get_config = lambda: {"image_reply": {"enabled": True, "min_gap_seconds": 20}}
    IL.search_image = lambda cfg, kw, root=None, chat_id="": (fake_img, "图源 pixiv 取图（过了过滤链）")
    TL._IMG_SEARCH_LAST.clear()
    fs3 = FakeSender()
    ctx3 = mkctx(fs3)
    r3 = TL._exec_send_image_search(ctx3, {"keyword": "猫"})
    ok("正常路径：调了 sender.send_image", len(fs3.sent) == 1, str(r3.get("content"))[:40])
    ok("正常路径**不能**报错（曾因 _os 未导入而假失败）", r3.get("is_error") is not True, str(r3.get("content"))[:60])
    r4 = TL._exec_send_image_search(ctx3, {"keyword": "狗"})
    ok("冷却内第二次 ⇒ 不重复发", len(fs3.sent) == 1 and "刚发过图" in (r4.get("content") or ""), (r4.get("content") or "")[:30])
    IL.search_image = lambda cfg, kw, root=None, chat_id="": (None, "试了 4 个图源都没通过过滤")
    TL._IMG_SEARCH_LAST.clear()
    fs4 = FakeSender()
    r5 = TL._exec_send_image_search(mkctx(fs4), {"keyword": "猫"})
    ok("没找到 ⇒ 说明原因且不发送", fs4.sent == [] and "没找到" in (r5.get("content") or ""), (r5.get("content") or "")[:40])
finally:
    TL.get_config = _real_cfg2
    IL.search_image = _real_search
    TL._IMG_SEARCH_LAST.clear()

print("── E. 触发条件可自定义（提示词真的跟着变）──")
_rc = CFG.get_config
try:
    def mk(img_mode, vc_mode, allow=True):
        return lambda: {"image_reply": {"trigger_mode": img_mode, "allow_search": allow},
                        "voice_reply": {"trigger_mode": vc_mode},
                        "api": {"vision": True}, "web_search": {"enabled": True}}
    CFG.get_config = mk("on_request", "on_request")
    import importlib
    importlib.reload(PR)
    t1 = PR._scene_rules()
    ok("on_request：写了「被点名才找图」", "找张 XX 的图" in t1)
    ok("on_request：没有「偶尔主动发图」", "偶尔可以主动发一张图" not in t1)
    ok("提示词里有发语音的触发条件", "send_voice_reply" in t1 and "发条语音" in t1)
    CFG.get_config = mk("sometimes", "sometimes")
    importlib.reload(PR)
    t2 = PR._scene_rules()
    ok("sometimes：出现「偶尔主动发图」", "偶尔可以主动发一张图" in t2)
    ok("sometimes：出现「偶尔用语音回一句」", "偶尔可以用语音回一句" in t2)
    CFG.get_config = mk("off", "off")
    importlib.reload(PR)
    t3 = PR._scene_rules()
    ok("off：不再给找图指令", "找张 XX 的图" not in t3 and "不主动发图" in t3)
    ok("off：不再给发语音指令", "send_voice_reply(text" not in t3)
finally:
    CFG.get_config = _rc
    import importlib
    importlib.reload(PR)

print("── F. 配置项在默认配置里（用户可改）──")
_dflt = CFG.DEFAULT_CONFIG
ok("image_reply.allow_search 存在", "allow_search" in (_dflt.get("image_reply") or {}))
ok("image_reply.trigger_mode 存在", "trigger_mode" in (_dflt.get("image_reply") or {}))
ok("voice_reply.trigger_mode 存在", "trigger_mode" in (_dflt.get("voice_reply") or {}))

print("── G. 关键词真的进查询 + 标签校验 + 不许拿旧图冒充（2026-09-18 拍摄现场翻车后加）──")
# 现场：用户要「鲸鱼」，机器人调 `send_image_search(keyword="鲸鱼")` 参数没错，可发出去的是一张
# 动漫角色图（用户当场发现「跟我要的完全不一样」）。两个真根因：
#   ① 图源查询**根本没带关键词**（safebooru 写死 `tags=rating:safe`、booru 只用配置里的固定 tag）
#      ⇒ "要图"实际是"从图源随便抓一张"；② 在线没取到时**拿旧缓存图冒充**，工具回执照样写
#      「已找到并发出一张「鲸鱼」的图」。
ok("中文关键词会翻成图源标签（鲸鱼→whale）", IS.tag_candidates("鲸鱼")[:1] == ["whale"],
   str(IS.tag_candidates("鲸鱼")))
_mc = IS.tag_candidates("猫 咖啡")
ok("多词关键词逐个翻（猫→cat、咖啡→coffee，原词留在候选里兜底）",
   "cat" in _mc and "coffee" in _mc and _mc[0] == "cat", str(_mc))


def _url_of(name, tag):
    try:
        return str(IS._BUILDERS[name]({"tag": tag})[0])
    except Exception as e:
        return "构造失败：%s" % e


for _n in ("safebooru", "konachan", "yande", "wallhaven", "danbooru", "bing", "so360"):
    ok("图源 %s 把关键词带进了查询" % _n, "whale" in _url_of(_n, "whale"), _url_of(_n, "whale")[:90])
ok("360 图源带的是原样中文（它认中文）",
   "%E9%B2%B8%E9%B1%BC" in _url_of("so360", "鲸鱼"), _url_of("so360", "鲸鱼")[:80])
ok("国内源排在默认列表最前（国内通路好、不易被 ban）",
   IS.available()[:2] == ["so360", "bing"], str(IS.available()[:3]))
ok("默认配置里国内源也在最前",
   (CFG.DEFAULT_CONFIG.get("image_reply") or {}).get("sources", [])[:2] == ["so360", "bing"],
   str((CFG.DEFAULT_CONFIG.get("image_reply") or {}).get("sources", [])[:3]))
ok("只按类目出图的源被标出来（点名要图时不许用它们）",
   set(IS.TAGLESS_SOURCES) == {"waifu", "nekos"}, str(IS.TAGLESS_SOURCES))

# G2 标签校验：图源返回的图**不含该标签** ⇒ 判为这次没取到（别把不相干的图当命中）
_j_real, _b_real = IS._json, dict(IS._BUILDERS)
try:
    IS._BUILDERS["__fake__"] = lambda cfg: ("https://x/y.json", lambda js: {
        "url": "https://x/y.jpg", "tags": ["landscape", "sky"], "rating": "safe", "page": "p"})
    IS._json = lambda url, timeout_ms=9000, headers=None: {}
    _m, _e = IS.fetch_meta("__fake__", {"tag": "whale"})
    ok("返回的图不带关键词标签 ⇒ 判「这次没取到」（不再把不相干图当命中）",
       _m is None and "不含标签" in str(_e), str(_e)[:80])
    IS._BUILDERS["__fake__"] = lambda cfg: ("https://x/y.json", lambda js: {
        "url": "https://x/y.jpg", "tags": ["whale", "ocean"], "rating": "safe", "page": "p"})
    _m2, _e2 = IS.fetch_meta("__fake__", {"tag": "whale"})
    ok("带对了标签 ⇒ 放行", bool(_m2) and _m2.get("source") == "__fake__", str(_e2)[:60])
finally:
    IS._json = _j_real
    IS._BUILDERS.clear()
    IS._BUILDERS.update(_b_real)

# G3 不许拿旧图冒充：默认**不**走缓存兜底；打开也要如实标注"与关键词无关"
_f_real = IL.fetch_filtered
_cache_dir = os.path.join(ROOT, "media", "images")
os.makedirs(_cache_dir, exist_ok=True)
_old_img = os.path.join(_cache_dir, "old-cached.jpg")
open(_old_img, "wb").write(b"\xff\xd8\xff" + b"x" * 500)
try:
    IL.fetch_filtered = lambda cfg, root=None, chat_id="", tag="": (None, "四个源全挂了")
    _p3, _w3 = IL.search_image({"image_reply": {"enabled": True, "allow_search": True}}, "鲸鱼")
    ok("默认**不**拿旧图冒充（如实说没找到）", _p3 is None and "全挂了" in str(_w3), str(_w3)[:60])
    _p4, _w4 = IL.search_image({"image_reply": {"enabled": True, "allow_search": True,
                                                "cache_fallback": True}}, "鲸鱼")
    ok("显式打开 cache_fallback 时才兜底，且**明说与关键词无关**",
       bool(_p4) and "与关键词无关" in str(_w4), str(_w4)[:80])
    # 工具回执也要如实（不许再写「已找到并发出一张「鲸鱼」的图」）
    _real_cfg3, _real_search2 = TL.get_config, IL.search_image
    try:
        TL.get_config = lambda: {"image_reply": {"enabled": True, "min_gap_seconds": 0,
                                                 "cache_fallback": True}}
        IL.search_image = lambda cfg, kw, root=None, chat_id="": (_old_img, "兜底：从旧图缓存挑了一张，内容与关键词无关")
        TL._IMG_SEARCH_LAST.clear()
        _r6 = TL._exec_send_image_search(mkctx(FakeSender()), {"keyword": "鲸鱼"})
        _c6 = str(_r6.get("content"))
        ok("兜底发出去时，工具回执**明说与关键词无关**（不再谎称命中）",
           "无关" in _c6 and "已按" not in _c6, _c6[:110])
    finally:
        TL.get_config, IL.search_image = _real_cfg3, _real_search2
        TL._IMG_SEARCH_LAST.clear()
finally:
    IL.fetch_filtered = _f_real
    try:
        os.remove(_old_img)
    except Exception:
        pass

# G4 一批里随机挑（`limit=1` 时同一标签永远同一张，那张被过滤链拦掉就永远过不去）
ok("booru 系与 safebooru 都取了**一批**再随机挑（limit=20 + random.choice）",
   "limit=20" in _url_of("safebooru", "whale") and "random.choice(js)" in
   open(os.path.join(ROOT, "agent", "image_sources.py"), encoding="utf-8").read())

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
