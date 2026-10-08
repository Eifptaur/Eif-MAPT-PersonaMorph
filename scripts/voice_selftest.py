#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音转文字判据（能力补齐 B）：**本地、离线、绝不假装识别过**。

链路：微信语音 .silk →[pilk / silk_v3_decoder]→ WAV(16k) →[Windows 内置 SAPI 听写]→ 文本

判据（不需要微信；②需要 TTS 与识别引擎，缺了会打印 SKIP 而不是假绿）：
  ① 引擎检测**不撒谎**：decoders/recognizers 有结构、status 的 why 与实测一致、没引擎时 ok=False
  ② **闭环自证**：TTS 合成 → WAV → SILK（微信 `\\x02#!SILK_V3` 帧）→ 解码 → 识别 → 与原文比对
  ③ fail-closed：文件不存在 / 垃圾 SILK ⇒ 明确错误、**不抛异常、不返回编造文本**
  ④ 没引擎时不假装：把三个解码器都判成不可用 ⇒ status.ok=False，transcribe 返回原因而非空文本
  ⑤ 接线：适配器 `download_media(kind=image|voice|video|file)` 四类都认，未知 kind 返回 None
  ⑥ 文件兜底链：`MessageResourceDetail.packed_info` 的原文件名解析（正例/反例/真实库命中率）
     与 `download_media` 里 `_file_from_wx_cache` 的接线（第三、四类媒体落盘的最后一环）
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, detail=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}  [{}]".format(name, detail))


from agent import voice # noqa: E402

print("── A. 引擎检测不撒谎 ──")
st = voice.status()
dec = st["decode"]
rec = st["recognize"]
ok("解码器清单是三条实测（pilk / silk_v3_decoder / ffmpeg-silk）",
   isinstance(dec, list) and len(dec) == 3 and all({"name", "ok", "detail"} <= set(d) for d in dec))
ok("识别器清单有 SAPI 文件听写那条", any("SAPI 文件听写" in r["name"] for r in rec))
has_dec = any(d["ok"] for d in dec)
has_rec = any(r["ok"] and not r["name"].startswith("  ") for r in rec)
ok("status.ok 与实测一致", bool(st["ok"]) == bool(has_dec and has_rec), st["why"])
ok("没引擎时 why 必须点名缺什么",
   st["ok"] or ("没有可用引擎" in st["why"] and len(st["why"]) > 10), st["why"][:60])
for d in dec:
    print("     解码器 {} ok={} {}".format(d["name"], d["ok"], d["detail"][:60]))
for r in rec:
    print("     识别器 {} ok={} {}".format(r["name"].strip(), r["ok"], r["detail"][:70]))

print("── B. 闭环自证（TTS → SILK → 文本）──")
if not (has_dec and has_rec):
    skip("闭环自证", "本机缺引擎：解码=%s 识别=%s" % (has_dec, has_rec))
else:
    loop = voice.selftest_loop("今天天气不错，我们出去走走吧")
    for s in loop["steps"]:
        ok("闭环-%s" % s["name"], s["ok"], s["detail"][:70])
    ok("识别文本与原文相似（标点不计）", loop["ok"], "听到：%s" % (loop["text"] or loop["err"]))
    ok("期望文本与听到文本非空", bool(loop["expect"]) and bool(loop["text"]))

print("── C. fail-closed ──")
# ⛔ 这几个"不存在的路径 / 垃圾文件"夹具**不落仓库**：以前写 `ROOT/_scratch/…` ⇒ 跑一次就在
#    产品根里造出 `_scratch/`（gitignore 的目录，`git status` 看不出来）。改用系统临时目录。
_TMPD = tempfile.mkdtemp(prefix="pm-voice-")
t, err, info = voice.transcribe_silk(os.path.join(_TMPD, "不存在.silk"))
ok("文件不存在 ⇒ 空文本 + 明确原因", t == "" and "不存在" in err, err[:60])
junk = os.path.join(_TMPD, "junk_probe.silk")
with open(junk, "wb") as fh:
    fh.write(b"\x02#!SILK_V3" + b"\x00" * 200)
try:
    t2, err2, _ = voice.transcribe_silk(junk)
    ok("垃圾 SILK ⇒ 不崩、不编造文本", t2 == "", "err=%s" % (err2[:60] or "(空)"))
except Exception as e:
    ok("垃圾 SILK ⇒ 不崩、不编造文本", False, "抛了 %s: %s" % (type(e).__name__, e))
_audit = os.path.join(_TMPD, "音" + "频") # 不存在的目录，确认不抛异常
try:
    os.remove(junk)
except OSError:
    pass  # 清理型：静默合法

print("── D. 没引擎时不假装 ──")
_real_dec, _real_rec = voice.decoders, voice.recognizers
try:
    voice.decoders = lambda: [{"name": "pilk", "ok": False, "detail": "probe"},
                              {"name": "silk_v3_decoder.exe", "ok": False, "detail": "probe"},
                              {"name": "ffmpeg(silk)", "ok": False, "detail": "probe"}]
    voice.recognizers = lambda: [{"name": "Windows 内置 SAPI 文件听写", "ok": False, "detail": "probe"}]
    st2 = voice.status()
    ok("三解码器全挂 ⇒ status.ok=False", st2["ok"] is False, st2["why"][:60])
    # 这一条要测的是**「文件在、但引擎全挂」**⇒ 夹具文件必须**真的存在**：
    # `voice.transcribe_silk` 有意把"文件在不在"判在"有没有引擎"**前面**（见 `agent/voice.py:327`
    # 的注释：没解码器时老顺序会先报"没有可用引擎"，把一个"路径写错了"的问题说成引擎问题）。
    # ⚠️ 以前这里指向开发目录里一个**真实存在的**夹具文件；开发目录移出产品根之后，这条用例就
    # 静默变成在测"文件不存在"了 —— 断言与它实际走的分支不符（实测抓到）。现在自带夹具、不依赖外部文件。
    _VFIX = os.path.join(_TMPD, "voice-test")
    os.makedirs(_VFIX, exist_ok=True)
    _VPROBE = os.path.join(_VFIX, "round.silk")
    with open(_VPROBE, "wb") as _fh:
        _fh.write(b"\x02#!SILK_V3" + b"\x00" * 200)
    t3, err3, _ = voice.transcribe_silk(_VPROBE)
    ok("没引擎 ⇒ 空文本 + 明确原因（不是编的文本）", t3 == "" and "没有可用引擎" in err3, err3[:70])
finally:
    voice.decoders, voice.recognizers = _real_dec, _real_rec

print("── E. 接线：适配器 download_media（四类媒体一条链，最终都经 MediaDownloader）──")
import inspect # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _srcmatch import has as _has # noqa: E402
from agent import wechat as W # noqa: E402
src = inspect.getsource(W.WeChatAdapter.download_media)
ok("download_media 四类都认（`image` / `voice` / `video` / `file`）",
   all(k in src for k in ('"image"', '"voice"', '"video"', '"file"')))
ok("图片转 `download_image`（保住 `tier='best'`：不传就可能只拿到缩略图）",
   "self.download_image(" in src)
ok("语音/视频/文件经驱动库 `MediaDownloader`（`self._md` 的三个 download_*）",
   "_md" in src and _has(src, '"voice": "download_voice"')
   and "download_video" in src and "download_file" in src)
ok("未知 kind 返回 None（白名单，不做兜底瞎猜）",
   'return None' in src and _has(src, '"voice": "download_voice"'))
ok("语音工具链有 download_voice", "download_voice" in src)
ok("⭐ 四类都先过 `self._md is None` 这一关（没装驱动库时如实返回 None，不假装下过）",
   src.index("self._md is None") < src.index('"image"'))
print("     语音落盘目录：%s" % voice.voice_dir())

print("── F. 文件兜底链：原文件名解析（纯函数，离线可验）──")
_PF = W.WeChatAdapter._parse_resource_file_name
# ── 正例：真实样本（`msg/file` 里那份 `报错 (2).txt` 的资源登记，34 字节）──
_GOOD = b"\n \n\x0e\xe6\x8a\xa5\xe9\x94\x99 (2).txt\x12\x0e\xe6\x8a\xa5\xe9\x94\x99 (2).txt"
ok("正例：protobuf 包装 ⇒ 解出干净文件名（不带控制字符）",
   _PF(_GOOD) == "报错 (2).txt", repr(_PF(_GOOD)))
ok("正例：中文 + 空格 + 括号都保真", _PF(_GOOD) == os.path.basename(_PF(_GOOD) or ""))
ok("正例：ASCII 名同样解得出",
   _PF(b"\n\x1e\n\x0c\x6f\x6e\x65\x73\x74\x61\x72\x74\x2e\x6c\x6f\x67\x12\x0c\x6f\x6e\x65\x73\x74\x61\x72\x74\x2e\x6c\x6f\x67")
   == "onestart.log")
# ── 反例：每一类"结构不符"都必须返回 None（不认识就不猜）──
ok("反例：空 / None / 全零 ⇒ None（不编名字）",
   _PF(None) is None and _PF(b"") is None and _PF(b"\x00" * 40) is None)
ok("反例：首字节不是 0x0A ⇒ None", _PF(b"\x99" + _GOOD[1:]) is None)
ok("反例：声明长度越界（名副其实的截断）⇒ None",
   _PF(b"\n \n\x7f" + b"a" * 5) is None)
ok("反例：名字里含路径分隔符 ⇒ None（防穿越）",
   _PF(b"\n$\n\x10a/b/c/d/e.txt\x12\x10a/b/c/d/e.txt") is None
   and _PF(b"\n$\n\x10a\\b\\c\\d.txt\x12\x10a\\b\\c\\d.txt") is None)
ok("反例：`\\x12` 后的冗余副本与前方不一致 ⇒ None（结构不是这个形态）",
   _PF(b"\n\x1a\n\x0conestart.log\x12\x0eOTHER_x.longer") is None)
ok("反例：名字长度声明与实际不符 ⇒ None（不按\"取到哪算哪\"硬塞）",
   _PF(b"\n\x18\n\x0aa.txt" + b"\x00" * 4 + b"\x12\x0ab.txt") is None)
# ── 反向对照：证明上面不是"恒 None"（否则正例也过不了、这组断言就是恒假）──
_n_ok = sum(bool(_PF(x)) for x in (_GOOD,
             b"\n\x1e\n\x0conestart.log\x12\x0conestart.log"))
ok("★ 反向对照：正例必须解得出来（证明这组断言不是恒 None）", _n_ok == 2, "%d/2" % _n_ok)
# ── 分母守卫：真实库里的 packed_info 抽样，确认解析器对**真实分布**不是恒 None ──
#   ⛔ 微信这几个库是 **SQLCipher 加密**的：直接 `sqlite3.connect` 会报 "file is not a database"。
#      必须走驱动库 `WeChatDB._open(rel)`（它内部解密 + 合并 -wal）。拿不到驱动库/密钥 ⇒ SKIP。
try:
    from agent.config import load_config as _lc
    from wechatauto.db import WeChatDB as _WDB
    _cfg = _lc() or {}
    _dbdir = str((_cfg.get("wechat", {}) or {}).get("db_dir") or "")
    _hit = _tot = _srcdb = 0
    if _dbdir and os.path.isdir(_dbdir):
        _db = _WDB(db_dir=_dbdir)
        for rel, path, _ in getattr(_db, "_db_files", []):
            if os.path.basename(path) != "message_resource.db":
                continue
            _srcdb += 1
            conn = _db._open(rel)
            try:
                rows = conn.execute(
                    "SELECT packed_info FROM MessageResourceDetail "
                    "WHERE LENGTH(packed_info)>0 LIMIT 400").fetchall()
            finally:
                conn.close()
            _tot += len(rows)
            _hit += sum(1 for (r,) in rows if _PF(r))
    if _tot:
        ok("★ 分母守卫：真实库里 packed_info 的解析命中率必须 >0（实测分布，不是构造样本）",
           _hit > 0, "%d/%d（扫到 %d 个库）" % (_hit, _tot, _srcdb))
    else:
        skip("分母守卫（真实库抽样）",
             "本机没扫到 message_resource.db（db_dir=%r）⇒ 只跑构造样本" % _dbdir[:40])
except Exception as _e:
    skip("分母守卫（真实库抽样）", "读库失败（多为微信没开/没密钥）：%s" % str(_e)[:60])

print("── G. 文件兜底链接线（download_media 的 file 分支）──")
ok("file 分支里调了本机兜底 `_file_from_wx_cache`",
   "_file_from_wx_cache(" in src and _has(src, 'k == "file"'))
ok("兜底只在**驱动库没拿到 / 路径不存在**时才触发（不抢驱动库的正常路）",
   src.index('k == "file" and (not got') < src.index("_file_from_wx_cache(sub"))
ok("兜底不构造 GUI、不点任何东西（只查库 + 复制文件）",
   "os.walk" in inspect.getsource(W.WeChatAdapter._file_from_wx_cache))
ok("本机没有这份 ⇒ 返回 None（如实说取不到，不假装）",
   "return None" in inspect.getsource(W.WeChatAdapter._file_from_wx_cache))

print("\n%d/%d 通过（跳过 %d）" % (PASS, PASS + FAIL, SKIP))
sys.exit(1 if FAIL else 0)
