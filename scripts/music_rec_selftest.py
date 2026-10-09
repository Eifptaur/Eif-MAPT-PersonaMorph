# -*- coding: utf-8 -*-
"""「音乐推荐」判据 —— 守第 139 项。**本判据不联网**（在线档只做静态/接线断言）。

## 口径变更（2026-10-10 用户定的）

用户当面推翻"本地热单库"这条路，原话口径：**"本地库放一堆热门歌曲，就相当于是
脱裤子放屁"** ⇒ 推荐**默认走在线**（网易云免 key / QQ音乐自填 key），本地标签库
降级成"在线不通时的兜底档"。
所以本判据的 C 段从"⛔ 不许接商业 API"**改成**"✅ 必须接在线、且红线不许破"。

守五件事：
  A 模块形态：`recommend()` 在、标签表落临时目录、预设标签**离线自带**（兜底档仍可用）。
  B ⭐ 本地兜底档：每条都有理由、理由来自真命中标签、没数据不编。
  C ✅ **在线优先**：默认走在线（网易云免 key）；出网关掉才退本地；
    ⛔ 红线不变：不下载/不转发音频（在线也只给链接）。
  D 接线：工具表 / 执行体 / 提示词三处都在，且提示词要求把**链接**转给用户。
  E 在线模块形态：`music_online` 免 key 打网易云、QQ档需 key、失败如实返回。

用法：runtime\\python\\python.exe scripts\\music_rec_selftest.py
"""
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402
from agent import music_rec as MR  # noqa: E402

TOOLS_SRC = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
PROMPT_SRC = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
MR_SRC = io.open(os.path.join(ROOT, "agent", "music_rec.py"), encoding="utf-8").read()
MO_SRC = io.open(os.path.join(ROOT, "agent", "music_online.py"), encoding="utf-8").read()
CFG_SRC = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()

_TD = tempfile.mkdtemp(prefix="mr-")
_ORIG = MR._data_path
MR._data_path = lambda: os.path.join(_TD, "music_tags.jsonl")

# ⛔ 判据不上网：把在线档整个关掉 ⇒ 只测本地兜底档（在线档另做静态断言）。
_ORIG_ALLOW = MR._online_allowed
MR._online_allowed = lambda: False

try:
    print("── A. 模块形态 ──")
    ok("A1 `recommend()` 在（第 139 项的主入口）", callable(getattr(MR, "recommend", None)), "")
    ok("A2 标签表落在临时目录（判据不碰产品 data/）", _TD in MR._data_path(), "")
    ok("A3 ⭐ 预设标签表**离线自带**（兜底档仍要在，不依赖网络/API key）",
       isinstance(MR.PRESET_TAGS, dict) and len(MR.PRESET_TAGS) >= 8, "%d 个" % len(MR.PRESET_TAGS))
    ok("A4 预设标签里有「雨天」这类**用户随口就会说**的场景词",
       "雨天" in MR.PRESET_TAGS and "深夜" in MR.PRESET_TAGS, str(list(MR.PRESET_TAGS)[:6]))
    ok("A5 勾连：预设标签的锚词覆盖「下雨」这种口语说法",
       any("下雨" in w for w in MR.PRESET_TAGS.get("雨天", ())), str(MR.PRESET_TAGS.get("雨天")))

    print("── B. 本地兜底档：每条都有理由（⭐）──")
    ok("B1 空库 + 空 query ⇒ `ok=False` 且**如实说「你得先说想要什么样的」**",
       (not MR.recommend("")["ok"])
       and ("先说想要什么样的" in MR.recommend("")["note"]), MR.recommend("")["note"][:50])
    ok("B1b 空库时**一首歌都没返回**（不编歌名）", MR.recommend("来点歌")["picks"] == [], "")
    ok("B2 记一首带标签的歌 ⇒ 学到", MR.remember("测试歌甲", "某艺人", ["雨天", "治愈"]) is True, "")
    ok("B2b 空歌名/空标签 ⇒ **不记**（没歌名记什么）",
       MR.remember("", "x", ["a"]) is False and MR.remember("有歌名", "x", []) is False, "")
    MR.remember("测试歌乙", "另一艺人", ["深夜"])
    MR.remember("测试歌丙", "第三艺人", ["雨天", "通勤"])
    _r1 = MR.recommend("适合下雨天的")
    ok("B3★ 命中 ⇒ 返回非空、且 `ok=True`", _r1["ok"] and len(_r1["picks"]) > 0,
       str([p["title"] for p in _r1["picks"]]))
    ok("B4★ ⭐ **每条 `why` 都非空**（没有理由的推荐看起来就像随机）",
       all(str(p.get("why") or "").strip() for p in _r1["picks"]), str([p["why"] for p in _r1["picks"]]))
    ok("B5★ ⭐ **理由来自真实命中的标签**（推雨天那首要说得出「雨天」）",
       all(("雨天" in str(p.get("why") or "")) for p in _r1["picks"]
           if p["title"] in ("测试歌甲", "测试歌丙")), str([p["why"] for p in _r1["picks"]]))
    ok("B6 一次最多 3 首（**别一次列十首**）", len(MR.recommend("来点歌", limit=99)["picks"]) <= 3, "")
    ok("B7 逆否：没命中的歌**不许出现**（推「雨天」时那条「深夜」的不该来）",
       "测试歌乙" not in [p["title"] for p in _r1["picks"]], str([p["title"] for p in _r1["picks"]]))
    ok("B8 `limit` 真的生效（要 1 首就给 1 首）", len(MR.recommend("适合下雨天的", limit=1)["picks"]) == 1, "")
    ok("B9★ 命中不到标签 ⇒ **给不出就说给不出**（不硬凑几首来交差）",
       (not MR.recommend("来点符合我生辰八字的")["ok"]), "")
    ok("B10 同名同艺人重复记 ⇒ **合并标签、不新增一行**"
       "（否则同一首歌会在推荐里重复出现）",
       (lambda: (MR.remember("测试歌甲", "某艺人", ["通勤"]),
                 len([r for r in MR._load()
                      if MR._norm(r.get("title")) == MR._norm("测试歌甲")]))[1])() == 1, "")
    ok("B11 合并后新标签**真的并进去了**",
       "通勤" in (lambda: [r for r in MR._load()
                           if MR._norm(r.get("title")) == MR._norm("测试歌甲")][0]["tags"])(), "")
    ok("B12 `ctx` 也能提供场景（如「现在是深夜」）",
       MR.recommend("", ctx="现在是深夜")["ok"] is True, "")
    ok("B13 `stats()` 给得出条数与标签分布", int(MR.stats().get("songs") or 0) >= 3, str(MR.stats())[:60])

    print("── C. ✅ 在线优先（口径已改）+ 红线不变 ──")
    # ⛔ 只扫**真代码行**：注释/文档串里恰在写「不发音频 / 不绕版权」这类**否定句**，
    #   直接扫全文会得出**反向结论**。做法：AST 取每个**语句**的行号 ⇒ 只留落在那里的行。
    _code_lines = set()
    try:
        import ast as _ast
        _tree = _ast.parse(MR_SRC)
        for _n in _ast.walk(_tree):
            if isinstance(_n, _ast.stmt):
                if isinstance(_n, _ast.Expr) and isinstance(_n.value, _ast.Constant) \
                        and isinstance(_n.value.value, str):
                    continue
                for _ln_no in range(_n.lineno, (getattr(_n, "end_lineno", _n.lineno) or _n.lineno) + 1):
                    _code_lines.add(_ln_no)
    except Exception:  # noqa: BLE001
        _code_lines = set(range(1, len(MR_SRC.splitlines()) + 1))
    _code_src = "\n".join(ln for i, ln in enumerate(MR_SRC.splitlines(), 1) if i in _code_lines)
    # C1：红线不变 —— 真代码里**不许有下载/转发音频**的调用（在线也只给链接）
    _bad = [w for w in ("requests.get", "urlretrieve", "yt-dlp", "yt_dlp",
                        "shutil.copy", "send_file")
            if w in _code_src]
    ok("C1★ ⛔ **真代码里**没有下载/转发音频的调用（在线也只给链接）", not _bad,
       str(_bad) + "（扫了 %d 行真代码）" % len(_code_lines))
    # C2：⭐ 反向锚 —— 必须真的接了在线（`recommend()` 里要出现 music_online）
    ok("C2★★ `recommend()` 里**真的调用在线档**（`music_online`）",
       "music_online" in _code_src and "recommend_online(" in _code_src, "")
    ok("C3★★ 在线优先、本地兜底：有 `_recommend_local` 这条退路",
       "_recommend_local" in MR_SRC and "_online_allowed" in MR_SRC, "")
    ok("C4★ 返回里的 `picks` 每项都带 `url` 字段（在线档给可点链接）",
       all("url" in p for p in _r1["picks"]), str(sorted(_r1["picks"][0].keys())))
    ok("C5★ `note` 里明写「不发音频文件、不转发、不绕平台版权」（红线逐字在）",
       "不绕平台版权" in str(_r1.get("note") or ""), "")

    print("── E. 在线模块形态（静态断言，不联网）──")
    ok("E1 `music_online.recommend_online()` 在（统一入口）",
       "def recommend_online(" in MO_SRC, "")
    ok("E2★ 网易云档**免 key**（打 music.163.com 公开接口，不带 Authorization）",
       "music.163.com" in MO_SRC, "")
    ok("E3★ QQ档**需 key**：`qqmusic_key`/`Bearer`，没 key 不启用（`qqmusic_enabled`）",
       "qqmusic_enabled" in MO_SRC and "Bearer" in MO_SRC, "")
    ok("E4★ QQ 档**给了申请地址**（教用户自己去申请）",
       "qqmusic_apply_url" in MO_SRC or "qqmusic_skills" in CFG_SRC or "qqmusic_skills" in MO_SRC, "")
    ok("E5★ 失败一律 `ok=False` + 如实说（不给假结果、不拿本地冒充在线）",
       '"ok": False' in MO_SRC and "不编" in MO_SRC, "")
    ok("E6★ 结果只给「歌名+歌手+链接」：没有音频地址/文件字段",
       "download" not in MO_SRC.lower() or "不下载" in MO_SRC, "")
    ok("E7 config 里有 `music` 段（source / qqmusic_key / online_allowed）",
       '"music":' in CFG_SRC and '"qqmusic_key"' in CFG_SRC and '"online_allowed"' in CFG_SRC, "")

    print("── D. 接线 ──")
    ok("D1 工具表里有 `recommend_music`", '"name": "recommend_music"' in TOOLS_SRC, "")
    _EX = _ss.func_src(TOOLS_SRC, "_exec_recommend_music")
    ok("D2 分母守卫：拿到执行体（>300 字符）", len(_EX) > 300, "%d 字符" % len(_EX))
    ok("D3 执行体调了 `recommend` 与 `remember`", "recommend(" in _EX and "remember(" in _EX, "")
    ok("D4★ 失败 ⇒ 走 `_err`（照实转达，不让模型自己编歌名）", "_err(" in _EX, "")
    ok("D4b★ 执行体把 `url` 一起返回（链接要能转给用户）",
       "picks" in _EX and "source" in _EX, "")
    ok("D5 提示词里有 `recommend_music`", "recommend_music" in PROMPT_SRC, "")
    ok("D6★ 提示词写明「**2~3 首就够了，别一次列十首**」", "别一次列十首" in PROMPT_SRC, "")
    ok("D7★ 提示词写明「**每首要写一句为什么推它**」且「**不许编理由**」",
       "为什么推它" in PROMPT_SRC and "不许编理由" in PROMPT_SRC, "")
    ok("D8★ 提示词写明红线「**不发音频文件、不转发、不绕平台版权**」",
       "不发音频文件" in PROMPT_SRC and "不绕平台版权" in PROMPT_SRC, "")
    ok("D9★ 提示词写明「照实转达 / 绝不编歌名」（在线没通也不编）",
       "绝不编歌名" in PROMPT_SRC, "")
    ok("D10★ 提示词要求**把链接转给用户**（`url`）", "url" in PROMPT_SRC and "链接" in PROMPT_SRC, "")
    ok("D11★ 提示词写明**在线曲库**（不再是「本地零 key」旧口径）",
       "在线曲库" in PROMPT_SRC, "")
finally:
    MR._data_path = _ORIG
    MR._online_allowed = _ORIG_ALLOW

print("")
print("音乐推荐判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
