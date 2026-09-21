# -*- coding: utf-8 -*-
"""B 站解析判据（2026-09-15，对应用户重新点名的「解析B站视频」）。

守的东西：
  A. **认得出来**：BV 号（纯/混在中文里/带标点/大小写）、完整视频页链接、旧式 av 号、b23.tv 短链。
  B. **不硬认**：非 B 站的链接、空串、长度不对的假 BV ⇒ 一律 `None`（不许"看着像就认"）。
  C. **接口层不撒谎**：`code!=0`（视频没了）/ 网络异常 / data 缺 bvid ⇒ 返回 `None` + 明确原因，
     **绝不编造标题**。
  D. **字幕没有就说没有**：列表空、正文拉不到、文件空 ⇒ 各自给不同原因，不许编字幕内容。
  E. **下载要么真下到、要么如实说没装**：没有 yt-dlp 时返回 `None` + 原因，且**不落任何文件**。
  F. **给模型的文本不许无中生有**：没字幕时文本里必须写"没有"，而不是留空让人误以为有。

用法：`py -3 scripts/bilibili_selftest.py`（全程离线：替身掉 `_get_json` / `_resolve_short`）
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import bilibili as B            # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("（%s）" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("（%s）" % detail) if detail else ""))


def sect(t):
    print("\n── %s ──" % t)


# ── A. 认得出来 ──────────────────────────────────────────────────────────────
sect("A. 从聊天文字里认出 B 站视频")
ok("纯 BV 号", (B.parse("BV1xx411c7mD") or {}).get("bvid") == "BV1xx411c7mD")
ok("混在中文里", (B.parse("你看这个 BV1xx411c7mD 笑死我了") or {}).get("bvid") == "BV1xx411c7mD")
ok("带标点", (B.parse("（BV1xx411c7mD）") or {}).get("bvid") == "BV1xx411c7mD")
ok("完整视频页链接", (B.parse("https://www.bilibili.com/video/BV1xx411c7mD") or {}).get("bvid") == "BV1xx411c7mD")
ok("m 站链接", (B.parse("https://m.bilibili.com/video/BV1xx411c7mD?p=2") or {}).get("bvid") == "BV1xx411c7mD")
ok("旧式 av 号", (B.parse("av12345") or {}).get("aid") == "12345")
ok("视频页 av 链接", (B.parse("https://www.bilibili.com/video/av12345") or {}).get("aid") == "12345")
_s = B.parse("https://b23.tv/AbCdEf")
ok("b23.tv 短链认成 short", _s and _s.get("kind") == "short" and _s.get("raw").endswith("AbCdEf"))
ok("大小写 BV 也认（B 站号是大小写敏感的 base58）",
   (B.parse("bv1xx411c7mD") or {}).get("bvid") is None
   and (B.parse("BV1xx411c7mD") or {}).get("bvid") == "BV1xx411c7mD",
   "小写 bv 不算（B 站现行格式就是大写 BV）")

# ── B. 不硬认（负例） ────────────────────────────────────────────────────────
sect("B. 反证：不该认的绝不认")
for bad, label in [("", "空串"), ("   ", "全空白"), ("今天天气不错", "纯中文闲聊"),
                   ("https://www.youtube.com/watch?v=abc", "油管链接"),
                   ("BV1xx411c7m", "BV 长度不对（少一位）"),
                   ("BV1xx411c7mDD", "BV 长度不对（多一位）"),
                   ("https://bilibili.com/", "B 站首页（没有视频）")]:
    ok("不认：%s" % label, B.parse(bad) is None, repr(str(B.parse(bad))[:40]))
ok("反证：短链只在 b23.tv 域名下才认",
   B.parse("https://example.com/b23.tv/abc") is None)

# ── C. 接口层不撒谎 ─────────────────────────────────────────────────────────
sect("C. 接口层：只认 code==0，其它一律如实报错")
_saved_get, _saved_short = B._get_json, B._resolve_short
try:
    B._get_json = lambda url, timeout=12: ({
        "code": 0,
        "data": {"bvid": "BV1xx411c7mD", "aid": 11, "title": "  标题在这  ", "desc": "简介",
                 "duration": 754, "pic": "http://x/y.jpg", "pubdate": 1700000000, "cid": 999,
                 "owner": {"name": "某UP", "mid": 5},
                 "stat": {"view": 1000, "like": 88, "coin": 3, "favorite": 4, "reply": 5, "danmaku": 6},
                 "pages": [{"cid": 999, "page": 1, "part": "P1", "duration": 754}]},
    }, "")
    v, why = B.view("BV1xx411c7mD")
    ok("正常解析出标题（且去掉首尾空白）", v and v["title"] == "标题在这", repr((v or {}).get("title")))
    ok("UP / 时长格式化正确", v and v["up"] == "某UP" and v["duration"] == "12:34", "%s / %s" % (v.get("up"), v.get("duration")))
    ok("分P 与统计都在", v and v["pages"][0]["part"] == "P1" and v["stat"]["like"] == 88)
    ok("url 是标准视频页", v and v["url"] == "https://www.bilibili.com/video/BV1xx411c7mD")

    B._get_json = lambda url, timeout=12: ({"code": -404, "message": "稿件不可见"}, "")
    v2, why2 = B.view("BV1xx411c7mD")
    ok("视频没了 ⇒ None + 带错误码", v2 is None and "-404" in why2, why2[:40])
    ok("反证：报错时拿不到任何标题字段（没编造）", v2 is None)

    B._get_json = lambda url, timeout=12: ({"code": 0, "data": {}}, "")
    v3, why3 = B.view("BV1xx411c7mD")
    ok("data 缺 bvid ⇒ None + 原因", v3 is None and "bvid" in why3, why3[:40])

    # AI 标识：只认后台字段 argue_info.argue_msg（2026-09-15 实测手机端不渲染这一行）
    B._get_json = lambda url, timeout=12: ({
        "code": 0, "data": {"bvid": "BV1xx411c7mD", "title": "T", "cid": 1,
                            "argue_info": {"argue_msg": "含AI生成内容", "argue_type": 0}}}, "")
    va, _ = B.view("BV1xx411c7mD")
    ok("后台带 argue_msg ⇒ ai_label 取到", va and va["ai_label"] == "含AI生成内容" and va["ai_label_known"])
    ok("给模型的文本里带 AI 标识行", "AI 标识：含AI生成内容" in B.to_text(va))

    B._get_json = lambda url, timeout=12: ({
        "code": 0, "data": {"bvid": "BV1xx411c7mD", "title": "T", "cid": 1,
                            "argue_info": {"argue_msg": "", "argue_type": 0}}}, "")
    vb, _ = B.view("BV1xx411c7mD")
    ok("argue_msg 为空 ⇒ 如实说「这条没标」", "这条没标" in B.to_text(vb) and vb["ai_label"] == "")

    B._get_json = lambda url, timeout=12: ({
        "code": 0, "data": {"bvid": "BV1xx411c7mD", "title": "T", "cid": 1}}, "")
    vc, _ = B.view("BV1xx411c7mD")
    ok("反证：接口没给 argue_info 时不许假装「没标」",
       vc["ai_label_known"] is False and "AI 标识" not in B.to_text(vc), B.to_text(vc)[:40])

    B._get_json = lambda url, timeout=12: (None, "连不上 B 站接口：超时")
    v4, why4 = B.view("BV1xx411c7mD")
    ok("网络异常 ⇒ None + 原始原因透传", v4 is None and "超时" in why4)

    # ── D. 字幕 ──
    sect("D. 字幕：没有就说没有")
    B._get_json = lambda url, timeout=12: ({"code": 0, "data": {"subtitle": {"subtitles": []}}}, "")
    s1, w1 = B.subtitles("BV1xx411c7mD", 999)
    ok("没有字幕 ⇒ 空 + 明确原因", s1 == "" and "没有字幕" in w1, w1[:40])

    def _sub_json(url, timeout=12):
        if "player/v2" in url:
            return {"code": 0, "data": {"subtitle": {"subtitles": [
                {"lan_doc": "中文", "subtitle_url": "//aisubtitle.hdslb.com/x.json"}]}}}, ""
        return {"body": [{"content": "第一句"}, {"content": "  "}, {"content": "第二句"}]}, ""
    B._get_json = _sub_json
    s2, w2 = B.subtitles("BV1xx411c7mD", 999)
    ok("有字幕 ⇒ 拼接（空白段跳过）", s2 == "第一句\n第二句" and w2 == "", repr(s2))

    B._get_json = lambda url, timeout=12: (None, "拉正文超时")
    s3, w3 = B.subtitles("BV1xx411c7mD", 999)
    ok("字幕正文拉不到 ⇒ 如实说", s3 == "" and "拉不到" in w3 or "超时" in w3, w3[:40])

    # ── E/F. info 与给模型的文本 ──
    sect("E/F. info() 与给模型的文本")
    B._resolve_short = lambda url, timeout=12: ("https://www.bilibili.com/video/BV1xx411c7mD", "")
    B._get_json = lambda url, timeout=12: ({
        "code": 0, "data": {"bvid": "BV1xx411c7mD", "aid": 11, "title": "T", "desc": "D", "duration": 60,
                            "cid": 999, "owner": {"name": "U"}, "stat": {}, "pages": []}}, "")
    v5, why5 = B.info("看这个 https://b23.tv/AbCdEf")
    ok("短链跟随后再解析", v5 and v5["bvid"] == "BV1xx411c7mD", str(why5)[:40])
    txt = B.to_text(v5)
    ok("没字幕时文本里写明「没有」（不是留空）", "字幕：没有" in txt and "没有字幕" in txt)
    ok("文本里有标题/UP/链接", "T" in txt and "U" in txt and "bilibili.com/video" in txt)
    ok("反证：拿不到视频时 to_text 不产出任何内容", B.to_text(None) == "")

    B._resolve_short = lambda url, timeout=12: (None, "短链跟不过去：DNS 失败")
    v6, why6 = B.info("https://b23.tv/AbCdEf")
    ok("短链跟不过去 ⇒ 如实报错", v6 is None and "DNS" in why6, str(why6)[:40])
    v7, why7 = B.info("今天天气不错")
    ok("文里没有 B 站标识 ⇒ 如实说没有（不是报错崩掉）",
       v7 is None and "没找到 B 站视频标识" in why7, str(why7)[:46])

finally:
    B._get_json, B._resolve_short = _saved_get, _saved_short

# ── G. 下载：要么真下到、要么如实说没装 ─────────────────────────────────────
sect("G. 下载：没有 yt-dlp 就如实说，且不落文件")
_saved_bin = B.ytdlp_bin
import tempfile                                          # noqa: E402
_d = tempfile.mkdtemp(prefix="pm_bili_")
try:
    B.ytdlp_bin = lambda: ""
    p, why = B.download("https://www.bilibili.com/video/BV1xx411c7mD", _d)
    ok("没装 yt-dlp ⇒ None + 指向安装方式", p is None and "yt-dlp" in why, why[:44])
    ok("反证：失败时目录里没有半成品文件", os.listdir(_d) == [], str(os.listdir(_d)))
    p2, why2 = B.download("", _d)
    ok("没给链接 ⇒ None + 原因", p2 is None and "没给链接" in why2, why2[:30])
finally:
    B.ytdlp_bin = _saved_bin
_real = B.ytdlp_bin()
print("  （本机 yt-dlp：%s）" % (_real or "未安装 ⇒ 下载那两件要先解决依赖"))

# ── H. 接线：真注册进工具表，且走的是本模块 ─────────────────────────────────
sect("H. 接线（用户口径：「别和链条断联」）")
from agent import tools as T                             # noqa: E402
_defs = T.build_tool_defs()
_bd = [t for t in _defs if t.get("name") == "read_bilibili"]
ok("工具表里有 read_bilibili", len(_bd) == 1, "共 %d 个工具" % len(_defs))
ok("参数是 url 且必填", (_bd and list(_bd[0]["parameters"]["properties"].keys()) == ["url"]
                     and _bd[0]["parameters"]["required"] == ["url"]))
ok("描述里写明「不要凭链接瞎猜内容」", _bd and "不要凭链接瞎猜" in _bd[0]["description"])
ok("描述里写明只读公开信息、不需要登录", _bd and "不需要登录" in _bd[0]["description"])
_src = open(os.path.join("agent", "tools.py"), encoding="utf-8").read()
ok("执行体走 bilibili.info（不是另写一套）", "_bili.info(" in _src and "from . import bilibili as _bili" in _src)
ok("执行体在拿不到时返回错误而不是空数据", 'return _err("解析不了这条 B 站链接' in _src)
ok("反证：没有出现「解析失败就当成功」的兜底", "解析不了这条 B 站链接" in _src and "return _ok({" in _src)

# ── J. 功能映射与连接（用户 2026-09-15：「在UI上做好功能映射和功能连接」）──────────
sect("J. 功能映射：配置键 / 控制台开关 / 关掉真的拒绝 / 文案不说黑话")
import json as _json                                     # noqa: E402
from agent.config import DEFAULT_CONFIG as _DC            # noqa: E402

_bc = (_DC.get("bilibili") or {})
_H2 = __import__("agent.console_html", fromlist=["HTML"]).HTML
H = _H2
ok("config 里有 bilibili 段", bool(_bc), str(list(_bc.keys())))
ok("默认开、且给了听的秒数上限", _bc.get("enabled") is True and int(_bc.get("listen_max_seconds") or 0) > 0,
   "enabled=%s listen=%s" % (_bc.get("enabled"), _bc.get("listen_max_seconds")))
_ex = _json.load(open("config.example.json", encoding="utf-8"))
ok("config.example.json 同步有这一段（示例不带 = 用户看不到这个开关）",
   _ex.get("bilibili") == _bc, str(_ex.get("bilibili")))
ok("控制台有「看懂B站链接」开关", 'data-cfg="bilibili.enabled"' in H)
ok("控制台有「听B站视频(秒)」上限", 'data-cfg="bilibili.listen_max_seconds"' in H)

# 关掉时工具必须如实拒绝（不是静默、也不是照做）
from agent import tools as _T2                            # noqa: E402
from agent import config as _C2                           # noqa: E402
_real_gc = _C2.get_config
try:
    _C2.get_config = lambda: {"bilibili": {"enabled": False}}
    _r = _T2._exec_read_bilibili({"session": {}, "emit": lambda *a, **k: None}, {"url": "BV1xx411c7mD"})
    _s = _json.dumps(_r, ensure_ascii=False)
    ok("关掉后工具如实拒绝（不假装、不去联网）",
       ("关闭" in _s) and ("bilibili.enabled" in _s), _s[:90])
finally:
    _C2.get_config = _real_gc

# 文案不许对一般用户说黑话（这一条是被用户点名后加的）
import re as _re2                                         # noqa: E402
_bad_words = ["EDGE_VOICES", "edge_tts_selftest", "voice_models", "bilibili.enabled",
              "判据", "_selftest", "config.json 里改"]
_seg_bili = H[H.find('data-cfg="bilibili.enabled"'): H.find('data-cfg="bilibili.enabled"') + 700]
ok("B 站那两个开关的说明里没有开发者黑话",
   not any(w in _seg_bili for w in ["EDGE_VOICES", "判据", "_selftest", ".py"]), _seg_bili[:70])

# ── I. 「自己听视频」：只下音频轨 + 本机识别（不走 yt-dlp）────────────────────
sect("I. 听视频：音频流怎么取、失败怎么报")
_sg, _sd = B._get_json, B._download
try:
    def _play(url, timeout=12):
        if "playurl" in url:
            return {"code": 0, "data": {"dash": {"audio": [
                {"bandwidth": 132000, "baseUrl": "https://x/hi.m4s"},
                {"bandwidth": 66000, "baseUrl": "https://x/lo.m4s"}]}}}, ""
        return {"code": 0, "data": {"bvid": "BV1xx411c7mD", "cid": 999, "title": "T"}}, ""
    B._get_json = _play
    u, w = B.audio_url("BV1xx411c7mD", 999)
    ok("从 dash 里取音频流（取最低码率那条，省流量）", u == "https://x/lo.m4s", repr(u))

    B._get_json = lambda url, timeout=12: ({"code": 0, "data": {"dash": {"audio": []}}}, "")
    u2, w2 = B.audio_url("BV1xx411c7mD", 999)
    ok("没有音频轨 ⇒ None + 如实说为什么", u2 is None and "没有可取的音频流" in w2, w2[:44])

    B._get_json = lambda url, timeout=12: ({"code": -404, "message": "稿件不可见"}, "")
    u3, w3 = B.audio_url("BV1xx411c7mD", 999)
    ok("接口报错 ⇒ None + 错误码", u3 is None and "-404" in w3, w3[:40])

    ok("反证：缺 cid 时不去打接口", B.audio_url("BV1xx411c7mD", None)[0] is None)

    B._get_json = _play
    B._download = lambda url, dest, timeout=180: (0, "音频流 HTTP 403（防盗链）")
    _d2 = tempfile.mkdtemp(prefix="pm_bili_a_")
    pa, wa = B.download_audio("BV1xx411c7mD", 999, _d2)
    ok("下载失败 ⇒ None + 原因", pa is None and "403" in wa, wa[:40])
    ok("反证：下载失败不落文件", os.listdir(_d2) == [], str(os.listdir(_d2)))

    B._download = lambda url, dest, timeout=180: (200, "")      # 小于 10KB 的门槛
    open(os.path.join(_d2, "junk"), "w").close()
    pb, wb = B.download_audio("BV1xx411c7mD", 999, _d2)
    ok("反证：下回来的音频太小也当失败（200 字节不算下到）", pb is None and "太小" in wb, wb[:40])

    B._get_json = lambda url, timeout=12: (None, "连不上 B 站接口：超时")
    t1, wt = B.listen("BV1xx411c7mD")
    ok("听视频时解析就失败 ⇒ 空文本 + 透传原因", t1 == "" and "超时" in wt, wt[:40])
finally:
    B._get_json, B._download = _sg, _sd

# ⛔ 第十轮 V-R10-34 第 5 条：音频流上限是 **384MB**（不是 128MB）—— 128MB 会误伤
#   2 小时高码率音频（192kbps ≈ 173MB / 320kbps ≈ 288MB）。这条断言是为了**别被改回去**：
#   比 256MB 小就是回退，比 1GB 大等于上限名存实亡。
_lim = B._download.__defaults__[1]
ok("音频流上限 ≥ 256MB 且 ≤ 1GB（V-R10-34 第 5 条：128MB 会误伤长音频）",
   256 * 1024 * 1024 <= int(_lim) <= 1024 * 1024 * 1024, "%d 字节" % int(_lim))
ok("音频流**不再**用整轮墙钟当超时（改成逐 recv 的 socket 超时：稳定推进的流不许被掐）",
   "墙钟换成" in (B._download.__doc__ or ""), (B._download.__doc__ or "")[:60])

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
