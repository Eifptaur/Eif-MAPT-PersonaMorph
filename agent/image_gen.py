# -*- coding: utf-8 -*-
"""群友要图 → 生图：**后端抽象 + 意图解析 + 可插拔过滤链**（设计稿见 docs/设计-群友要图-生图链条.md）。

本模块只做"链条"，**不含真实生图后端**——接本地 ComfyUI 还是在线 API 待用户拍板（见设计稿 §5）。
所以：没有配后端时，`generate()` **明确失败并说清原因**（绝不假装生成）。

四条红线（硬编码，连开关都没有）：
  ① 不生成真人换脸/换身体 —— `ALLOW_REAL_FACE = False`，命中真人意图直接拒；
  ② `r18` 相关参数**根本不下发**（请求体里不构造这类字段）；
  ③ 过滤链任一层判否**或出错** ⇒ 不发（fail-closed，不允许"过滤器坏了就放行"）；
  ④ prompt 先脱敏（手机号/身份证/银行卡/邮箱/@提及/长数字串）。
"""
from __future__ import annotations

import json
import os
import re
import time

from . import config as _config

ALLOW_REAL_FACE = False          # 恒 False：不做真人换脸/换身体（无开关可开）

DEFAULTS = {
    "enabled": False,            # 总开关默认关（同"随机图"的口径）
    "trigger_mode": "on_request",  # on_request | sometimes | off
    "max_count": 2,              # 单次最多生成几张
    "size_default": "square",
    "online_allowed": False,     # 是否允许出网到在线生图 API
    "backends": [],              # [{"id","kind":"local|online","url","timeout"}...]（空＝没后端）
    "style_allow": [],           # 用户自填的风格白名单关键词（空＝不限）
    "style_block": [],           # 风格黑名单关键词
    "filter_chain": {"size": True, "dup": True, "blacklist": True, "text": True, "classifier": True},
}

_STYLE_WORDS = ("动漫", "二次元", "写实", "水彩", "油画", "像素", "国风", "赛博", "像素风", "手绘")
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}
# 真人换脸要**按模式**匹配，不能只列固定词：实测"把这张照片换成我朋友的脸"不含连续词"换脸"
# ⇒ 用"换/套/贴 + 若干字 + 脸/头/五官"的模式，外加几个固定说法
_REAL_FACE_RX = (re.compile(r"(换|套|贴|移植|合成).{0,6}(脸|头|五官|脸型)"),
                 re.compile(r"(真人|本人|照片|自拍).{0,6}(换|合成|改造)"))
_REAL_FACE = ("换脸", "换头", "真人脸", "deepfake", "ai换脸", "合成脸", "换个人", "把脸")
_NSFW = ("r18", "18禁", "色图", "露点", "裸", "情色", "porn", "nsfw")


def wants_real_face(text: str) -> bool:
    """是否涉及真人换脸/换身体（红线判定用，宁可多判也不许漏判）。"""
    t = str(text or "").lower()
    if any(w in t for w in _REAL_FACE):
        return True
    return any(rx.search(t) for rx in _REAL_FACE_RX)


def _as_list(v):
    """配置里的"列表"允许写成字符串（控制台就是一个输入框 ⇒ 省掉自定义端点）：
    `风格A,风格B` 或分号分隔都认；已经是 list 就原样返回。"""
    if isinstance(v, (list, tuple)):
        return [x for x in v]
    if isinstance(v, str):
        parts = re.split(r"[,，;；\n]", v)
        return [p.strip() for p in parts if p.strip()]
    return []


def _as_backends(v):
    """后端列表：list[dict] 直接用；字符串按 `id|local|url[|proto]` 解析（格式不对的条目丢弃）。

    为什么不给控制台单独做增删端点：照用户"低成本优先"的原则，一个输入框 + 一种人话格式就够用；
    解析不出来 ⇒ 后端列表为空 ⇒ `generate()` 会明确说"还没配生图后端"（不会静默当成功）。

    ⚠️ 2026-09-17 加第 4 段 `proto`：不带它的条目会落到 `generic` 协议（POST 根路径）——
    本机实测就是这么踩的：本地轻量后端写的是 `local-sd | local | http://127.0.0.1:7860`，
    结果按 generic 去 POST 根路径 ⇒ 404 ⇒ 生图直接失败。⇒ 支持显式写 `| a1111`。
    """
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, dict)]
    out = []
    if isinstance(v, str):
        for chunk in re.split(r"[;；\n]", v):
            f = [x.strip() for x in chunk.split("|")]
            if len(f) >= 3 and f[0] and f[2]:
                it = {"id": f[0], "kind": (f[1] or "local"), "url": f[2]}
                if len(f) >= 4 and f[3]:
                    it["proto"] = f[3]
                out.append(it)
    return out


def cfg() -> dict:
    d = dict(DEFAULTS)
    try:
        got = _config.get_config().get("image_gen") or {}
        if isinstance(got, dict):
            d.update(got)
    except Exception:
        pass
    # 归一化：控制台把列表项当普通输入框编辑 ⇒ 到这里统一转成真正的 list
    d["style_allow"] = _as_list(d.get("style_allow"))
    d["style_block"] = _as_list(d.get("style_block"))
    d["backends"] = _as_backends(d.get("backends"))
    d["filter_chain"] = dict(DEFAULTS["filter_chain"], **(d.get("filter_chain") or {}))
    return d


def enabled() -> bool:
    return bool(cfg().get("enabled"))


# ————————————————— ① prompt 脱敏 —————————————————
_SCRUB = (
    (re.compile(r"\b1[3-9]\d{9}\b"), "[手机号]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[身份证]"),
    (re.compile(r"\b\d{16,19}\b"), "[卡号]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[邮箱]"),
    (re.compile(r"wxid_[0-9a-zA-Z_-]+"), "[wxid]"),
    (re.compile(r"@[^\s，。,.!！?？]{1,24}"), "[@提及]"),
)


def scrub_prompt(text: str) -> str:
    """生成前先脱敏：手机号/身份证/卡号/邮箱/wxid/@提及 一律替换成占位符。"""
    out = str(text or "")
    for rx, rep in _SCRUB:
        out = rx.sub(rep, out)
    return out.strip()


# ————————————————— ② 意图解析 —————————————————
def parse_intent(text: str) -> dict:
    """从群友的话里抽：主体 / 风格 / 张数 / 尺寸 / 是否要真人 / 是否涉黄。"""
    raw = str(text or "").strip()
    t = scrub_prompt(raw)
    n = 1
    m = re.search(r"(\d+)\s*张", t)
    if m:
        try:
            n = max(1, int(m.group(1)))
        except Exception:
            n = 1
    else:
        for k, v in _CN_NUM.items():
            if ("%s张" % k) in t:
                n = v
                break
    size = "square"
    if any(w in t for w in ("竖", "手机壁纸", "头像")):
        size = "portrait"
    elif any(w in t for w in ("横", "壁纸", "桌面")):
        size = "landscape"
    style = ""
    for w in _STYLE_WORDS:
        if w in t:
            style = w
            break
    # 主体：去掉祈使词/数量词/风格词，剩下的当主体
    subj = t
    for w in ("画", "生成", "来一", "来", "整一", "搞一", "给我", "帮我", "做", "弄", "张", "的", "图"):
        subj = subj.replace(w, " ")
    for w in _STYLE_WORDS:
        subj = subj.replace(w, " ")
    subj = re.sub(r"\s+", " ", subj).strip(" ，。,.!！?？")
    return {
        "raw": raw, "subject": subj or t, "style": style, "count": n, "size": size,
        "wants_real_face": wants_real_face(t),
        "nsfw": any(w in t.lower() for w in _NSFW),
    }


# ————————————————— ③ 后端选择 —————————————————
def backends() -> list:
    """可用后端＝**配置里填的** ∪ **本机探测到的**（用户口径：别等他选型，能用的先认出来）。

    去重按 id；在线后端（含免密钥的 pollinations）仍必须 `online_allowed=True` 才出现。
    """
    out, seen = [], set()
    for b in (cfg().get("backends") or []):
        try:
            if not isinstance(b, dict) or not b.get("id") or not b.get("url"):
                continue
            if str(b.get("kind") or "local") == "online" and not cfg().get("online_allowed"):
                continue                      # 在线后端必须显式允许出网
            b = dict(b)
            b.setdefault("proto", "generic")
            b.setdefault("kind", "local")
            out.append(b)
            seen.add(str(b["id"]))
        except Exception:
            continue
    try:
        for b in detect_local():
            if str(b["id"]) in seen:
                continue
            out.append(b)
            seen.add(str(b["id"]))
    except Exception:
        pass
    if cfg().get("online_allowed") and ONLINE_FREE["id"] not in seen:
        out.append(dict(ONLINE_FREE))          # 免密钥在线（仍受"允许出网"这一档管）
    # 🔴 2026-09-18：**默认在线优先**（用户拍板"默认出网"；理由见 config 注释：本地是动漫模型 + 8 步蒸馏，
    #   在线 3~4 秒出照片级）。`image_gen.online_first=False` 时保持"本地/自配优先"的老顺序。
    #   选中的在线后端（预设或自填接口）排最前 —— 用户填了 key 的那家优先于免密钥那条。
    try:
        _ob = online_backend()
        if _ob and cfg().get("online_allowed") and cfg().get("online_first", True) is not False:
            out = [_ob] + [b for b in out if str(b.get("id")) != str(_ob.get("id"))]
            seen.add(str(_ob.get("id")))
    except Exception:
        pass
    return out


def pick_backend():
    """挑一个后端；没有 ⇒ (None, 人话原因)。

    ⚠️ 2026-09-17 加：**装了「群相本地轻量后端」但服务没在跑时，顺手拉起来再探一次**——
    用户口径是「不要让用户搞这搞那的操作」：装好了就该直接用，不该还要求他记得去启动服务。
    """
    bs = backends()
    if not bs:
        try:
            from . import sd_local as _sd
            st = _sd.status()
            if st.get("installed") and not st.get("server_alive"):
                _sd.ensure_running()
                bs = backends()
        except Exception:
            pass
    if not bs:
        online = [b for b in (cfg().get("backends") or []) if isinstance(b, dict) and str(b.get("kind")) == "online"]
        if online and not cfg().get("online_allowed"):
            return None, "只配了在线生图后端，但 image_gen.online_allowed=False（出网未允许）"
        return None, ("本机没探到常见生图服务（A1111/Fooocus :7860/:7865 · ComfyUI :8188 · InvokeAI :9090），"
                      "也没有在控制台填后端 ⇒ 把其中一个跑起来，或在控制台「要图」面板点"
                      "「安装本地生图后端」（装完自动配好），或者打开「允许出网」用免密钥在线生图")
    return bs[0], ""


# ————— ③-b 后端协议适配：本地自动发现 + 免密钥在线（既有口径：别等我选型，直接找能用的）—————
#   本地：常见生图服务的默认端口 + 一个只读探测路径（探到就用，不用用户手配）。
#   在线：pollinations 是**免密钥**的 text-to-image（GET 一个 URL 就出图）；出网仍受 online_allowed 管。
SIZE_PX = {"square": (1024, 1024), "portrait": (832, 1216), "landscape": (1216, 832)}
# 本地服务探测结果短缓存（见 detect_local 的说明：一次探测 3.6 秒，别在每次刷状态时都真探）
_DETECT_CACHE = {"at": 0.0, "val": None}
LOCAL_PROBES = (
    {"id": "a1111", "kind": "local", "proto": "a1111", "port": 7860, "path": "/sdapi/v1/sd-models",
     "url": "http://127.0.0.1:7860"},
    {"id": "comfyui", "kind": "local", "proto": "comfyui", "port": 8188, "path": "/system_stats",
     "url": "http://127.0.0.1:8188"},
    {"id": "fooocus", "kind": "local", "proto": "a1111", "port": 7865, "path": "/sdapi/v1/sd-models",
     "url": "http://127.0.0.1:7865"},
    {"id": "invokeai", "kind": "local", "proto": "a1111", "port": 9090, "path": "/api/v1/app/version",
     "url": "http://127.0.0.1:9090"},
)
ONLINE_FREE = {"id": "pollinations", "kind": "online", "proto": "pollinations",
               "url": "https://image.pollinations.ai/prompt/"}

# ── 在线出图预设表（2026-09-18 加；用户口径：「在线后端能不能扩展几个…让用户自己切。然后 UI 上对应好了，
#   记得加说明文本哈」）────────────────────────────────────────────────────────────
# 一手事实（当天实测，不是凭印象）：
#   · 三家付费/免费额度服务商的出图端点**路径都存在**（无 key POST 返回 401 而不是 404），且都是
#     **OpenAI 兼容**形状（`POST {base}/images/generations`）⇒ 一个 `openai_image` 协议全覆盖。
#   · pollinations（免密钥零配置）：能出图（3.6 秒/照片级），但 **`nologo=true` 现在要带 token 才生效**
#     ⇒ 免密钥档**右下角固定有水印**；它的可用模型列表现在只有 `sana`。
#   · 智谱的 `cogview-3-flash` 官方标注**免费**（注册即得 key），`cogview-4` / `glm-image` 是同族更强档。
# 每家的 `note` 都会**原样显示在控制台**——不许写空话：要不要 key、有没有免费额度、有没有水印、大概多快。
ONLINE_PRESETS = [
    {"id": "pollinations", "label": "Pollinations（免密钥·零配置）",
     "proto": "pollinations", "url": "https://image.pollinations.ai/prompt/",
     "model": "sana", "need_key": False,
     "note": "不用注册、开箱可用；实测 3~4 秒出一张照片级图。**右下角带 pollinations.ai 水印**："
             "它的 `nologo` 参数官方注明**要有账号才生效** ⇒ 在 pollinations.ai 免费注册拿一个 token，"
             "填到下面「出图密钥」里，水印就没了（本链也会在没 token 时裁掉底部水印带兜底）。"},
    {"id": "siliconflow", "label": "硅基流动 SiliconFlow（填 key·无水印）",
     "proto": "openai_image", "url": "https://api.siliconflow.cn/v1",
     "model": "black-forest-labs/FLUX.1-schnell", "need_key": True,
     "note": "国内直连、**无水印**。FLUX.1-schnell 快、有免费额度；另有 Kwai-Kolors/Kolors（中文友好）、"
             "Qwen/Qwen-Image（中文文字强）。模型名以它控制台的模型列表为准。注册后填 key 即可。"},
    {"id": "zhipu", "label": "智谱 BigModel（填 key·无水印）",
     "proto": "openai_image", "url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "cogview-3-flash", "need_key": True,
     "note": "国内直连、**无水印**。`cogview-3-flash` 官方标注**免费**（注册即得 key）；"
             "想要更好可换 cogview-4 / glm-image（同族、按量计费）。"},
    {"id": "volc", "label": "火山方舟（即梦 Seedream·填 key·无水印）",
     "proto": "openai_image", "url": "https://ark.cn-beijing.volces.com/api/v3",
     "model": "", "need_key": True,
     "note": "国内直连、**无水印**、质量属于第一梯队（Seedream 系）。**要 key**：到火山方舟开通并创建模型"
             "接入点（接入点 ID 或模型名填在下面「模型」里），key 填「出图密钥」。"},
    {"id": "custom", "label": "自定义（任何 OpenAI 兼容出图接口）",
     "proto": "openai_image", "url": "", "model": "", "need_key": True,
     "note": "只要对方提供 `POST {地址}/images/generations`、返回 `data[].b64_json` 或 `data[].url`，"
             "填「地址 + key + 模型名」就能接（地址写到 `/v1` 这一层即可）。有没有水印由对方决定——"
             "自建或按量计费的接口通常**没有水印**；免费公开接口大多会加。"},
]


def online_presets() -> list:
    """给控制台/判据用的预设表（返回副本，别让调用方改到表本体）。"""
    return [dict(p) for p in ONLINE_PRESETS]


def online_backend() -> dict:
    """按配置拼出**当前选中的在线后端**（没选/没填 key 就返回 `{}`）。

    优先级：① `image_gen.online_api` 里填了 url ⇒ 用它（自定义/带 key 的那家）
            ② 否则按 `image_gen.online_preset` 选预设（pollinations 不需要 key）
    """
    c = cfg()
    api = c.get("online_api") or {}
    url = str(api.get("url") or "").strip()
    if url:
        return {"id": "online-api", "kind": "online", "proto": "openai_image",
                "url": url, "key": str(api.get("key") or ""),
                "model": str(api.get("model") or ""), "timeout": int(api.get("timeout") or 120)}
    want = str(c.get("online_preset") or "pollinations").strip()
    for p in ONLINE_PRESETS:
        if p["id"] != want:
            continue
        b = {"id": p["id"], "kind": "online", "proto": p["proto"], "url": p["url"],
             "model": p.get("model") or "", "need_key": bool(p.get("need_key"))}
        if b["need_key"]:
            key = str(api.get("key") or "").strip()
            if not key:
                return {}                     # 要 key 的那家没填 key ⇒ 视为"没选它"
            b["key"] = key
            if str(api.get("model") or "").strip():
                b["model"] = str(api["model"]).strip()
        return b
    return {}


def detect_local(timeout: float = 1.2, probes=None, ttl: float = 120.0) -> list:
    """探一遍常见本地生图服务（**只读 GET**，不动对方任何状态）⇒ 探到就返回可用的后端。

    为什么要有它：用户 2026-09-13 明确说"生图，不能直接去找那些能生图的模型或者工具吗"——别让他先选型、
    再手工填地址；本机在跑 ComfyUI/A1111/Fooocus/InvokeAI 就自己认出来。

    ⚡ 2026-09-18 加**短缓存**（默认 20 秒）：实测一次探测要 **3.6 秒**（四个端口里三个连不上时要等满
    `timeout`）⇒ 控制台每次刷状态、`snapshot()`、`pick_backend()` 都各问一遍，面板会明显发木
    （判据也因此在 80 秒上下徘徊）。"本机有没有生图服务"20 秒内几乎不会变，缓存完全安全。
    传了 `probes` 的调用（测试）**不走缓存**，保证测试拿到的是即时结果。
    """
    import urllib.request
    if probes is None:
        _now = time.time()
        if _DETECT_CACHE["val"] is not None and (_now - float(_DETECT_CACHE["at"] or 0)) < float(ttl or 0):
            return [dict(x) for x in _DETECT_CACHE["val"]]
    out = []
    for p in (probes if probes is not None else LOCAL_PROBES):
        try:
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (p["port"], p["path"]), method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = int(getattr(r, "status", 200) or 200)
            if code < 500:
                out.append({"id": p["id"], "kind": "local", "proto": p["proto"], "url": p["url"]})
        except Exception:
            continue
    if probes is None:
        _DETECT_CACHE["at"], _DETECT_CACHE["val"] = time.time(), [dict(x) for x in out]
    return out


def _save_image_bytes(data: bytes, backend_id: str = "gen") -> str:
    """把生图结果落盘到 `data/gen_images/<日期>/`（**只落盘，不写台账**——台账要在过滤通过之后写）。"""
    day = time.strftime("%Y%m%d")
    d = os.path.join("data", "gen_images", day)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s_%s.png" % (backend_id, time.strftime("%H%M%S")))
    with open(p, "wb") as f:
        f.write(data)
    return p


def _note_generated(path: str, backend_id: str = "gen") -> str:
    """**过滤通过之后**才把这张图记进台账（返回 sha256）。

    ⚠️ 为什么不能在图一落盘就记（2026-09-13 自己踩的同类坑，和 wx-agent 的"重复发送台账写太早"一模一样）：
    落盘即记 ⇒ `dup` 层读到的是**自己**的 sha ⇒ **每张新图都被判成重复**、一张都发不出去。
    正确顺序：落盘 → 过滤链 → 全过 → 记账（下次生成才拿它去重）。
    """
    import hashlib
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        os.makedirs(os.path.join("data", "gen_images"), exist_ok=True)
        with open(os.path.join("data", "gen_images", "index.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": path, "sha256": h, "backend": backend_id,
                                "when": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
        return h
    except Exception:
        return ""


def call_backend(backend: dict, prompt: str, count: int = 1, size: str = "square"):
    """真去生图：按后端协议分发。**未接后端时不会被调用**；请求体里不构造任何 r18 字段。

    支持四种协议：`a1111`（SD WebUI / Fooocus / InvokeAI 兼容层，POST /sdapi/v1/txt2img）·
    `pollinations`（免密钥在线，GET 一个 URL 就出图）· **`openai_image`**（OpenAI 兼容出图：
    硅基流动 / 智谱 / 火山方舟 / 任何自建服务，POST {url}/images/generations）·
    `generic`（用户自己填的 HTTP 端点，POST {prompt,n,size}）。
    `comfyui` 目前只做**探测**：它要一份工作流 JSON，还没内置（探到会在面板里说明）。
    """
    import base64
    import urllib.parse
    import urllib.request
    w, h = SIZE_PX.get(size or "square", SIZE_PX["square"])
    proto = str(backend.get("proto") or "generic")
    n = max(1, int(count or 1))
    files = []
    if proto == "a1111":
        body = json.dumps({"prompt": prompt, "width": w, "height": h, "batch_size": n,
                           "n_iter": 1, "steps": int(backend.get("steps") or 20)}).encode("utf-8")
        req = urllib.request.Request(backend["url"].rstrip("/") + "/sdapi/v1/txt2img", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or 180)) as resp:
            j = json.loads(resp.read().decode("utf-8") or "{}")
        for i, b64 in enumerate(j.get("images") or []):
            files.append(_save_image_bytes(base64.b64decode(b64.split(",")[-1]),
                                           "%s_%d" % (backend.get("id") or "a1111", i)))
    elif proto == "pollinations":
        # 🔴 2026-09-18：**带 token 就能去水印**（官方 APIDOCS 原话：`nologo` = Remove the Pollinations
        #   watermark **(needs account)**）⇒ 控制台「出图密钥」里填 pollinations 的 token，这里带
        #   `Authorization: Bearer`，`nologo=true` 才真的生效。没 token 时水印去不掉（见 strip_watermark 兜底）。
        _tok = str(backend.get("key") or "").strip()
        _hdr = {"User-Agent": "Mozilla/5.0"}
        if _tok:
            _hdr["Authorization"] = "Bearer " + _tok
        for i in range(n):
            _m = str(backend.get("model") or "").strip()
            url = (backend["url"].rstrip("/") + "/" + urllib.parse.quote(prompt)
                   + "?width=%d&height=%d&nologo=true&referrer=PersonaMorph&seed=%d" % (w, h, int(time.time()) + i)
                   + (("&model=" + urllib.parse.quote(_m)) if _m else ""))
            req = urllib.request.Request(url, headers=dict(_hdr), method="GET")
            with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or 180)) as resp:
                data = resp.read()
            if not data or len(data) < 128:
                raise ValueError("在线生图返回的数据太小（%d 字节）" % len(data or b""))
            files.append(_save_image_bytes(data, str(backend.get("id") or "pollinations")))
    elif proto == "openai_image":
        # 🔴 2026-09-18 加：**OpenAI 兼容出图**（硅基流动 / 智谱 / 火山方舟 / 任何自建服务）。
        #   三家端点的形状当天都验过（无 key POST → 401，说明路径对、只是缺鉴权）：
        #     https://api.siliconflow.cn/v1/images/generations
        #     https://open.bigmodel.cn/api/paas/v4/images/generations
        #     https://ark.cn-beijing.volces.com/api/v3/images/generations
        #   返回 `data[].b64_json`（优先）或 `data[].url`（再下载一次）。
        base = str(backend.get("url") or "").rstrip("/")
        if not base:
            raise ValueError("这个在线后端没填地址（控制台「要生图」面板 → 在线后端）")
        if "/images/generations" not in base:
            base = base + "/images/generations"
        model = str(backend.get("model") or "").strip()
        if not model:
            raise ValueError("这个在线后端要填模型名（不同服务商的模型名不一样，见面板上的说明）")
        body_obj = {"model": model, "prompt": prompt, "n": n, "size": "%dx%d" % (w, h)}
        req = urllib.request.Request(
            base, data=json.dumps(body_obj, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + str(backend.get("key"))}
                        if str(backend.get("key") or "") else {})},
            method="POST")
        with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or 180)) as resp:
            j = json.loads(resp.read().decode("utf-8") or "{}")
        for i, it in enumerate(j.get("data") or []):
            b64 = it.get("b64_json") if isinstance(it, dict) else None
            if b64:
                files.append(_save_image_bytes(base64.b64decode(str(b64).split(",")[-1]),
                                               "%s_%d" % (backend.get("id") or "online", i)))
                continue
            link = (it or {}).get("url") if isinstance(it, dict) else None
            if link:
                r2 = urllib.request.Request(str(link), headers={"User-Agent": "PersonaMorph/1.0"})
                with urllib.request.urlopen(r2, timeout=int(backend.get("timeout") or 180)) as rr:
                    files.append(_save_image_bytes(rr.read(),
                                                   "%s_%d" % (backend.get("id") or "online", i)))
    else:                                     # generic：用户自填端点，约定返回 {"files":[...]}
        body = json.dumps({"prompt": prompt, "n": n, "size": size}).encode("utf-8")
        req = urllib.request.Request(backend["url"], data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or 180)) as resp:
            j = json.loads(resp.read().decode("utf-8") or "{}")
        files = [str(p) for p in (j.get("files") or [])]
    return {"files": files, "proto": proto}


# ————————————————— ④ 可插拔过滤链（任一层判否/出错 ⇒ 不发）—————————————————
def _f_size(path: str, meta: dict):
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            im.verify()
        if w < 128 or h < 128:
            return False, "尺寸过小（%dx%d）" % (w, h)
        return True, "%dx%d" % (w, h)
    except Exception as e:
        return False, "图片打不开/损坏：%s" % type(e).__name__


def _f_blacklist(path: str, meta: dict):
    bl = [str(x).lower() for x in (cfg().get("style_block") or []) if str(x).strip()]
    al = [str(x).lower() for x in (cfg().get("style_allow") or []) if str(x).strip()]
    p = str(meta.get("prompt") or "").lower()
    for w in bl:
        if w and w in p:
            return False, "命中风格黑名单 %r" % w
    if al and not any(w in p for w in al):
        return False, "不在风格白名单内（白名单非空时只放行白名单风格）"
    return True, "风格词检查通过"


def _f_dup(path: str, meta: dict):
    """与最近生成过的图去重（同 sha256 视为重复）。"""
    import hashlib
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        return False, "读不出文件算 hash：%s" % type(e).__name__
    idx = os.path.join("data", "gen_images", "index.jsonl")
    try:
        if os.path.exists(idx):
            with open(idx, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        if json.loads(line).get("sha256") == h:
                            return False, "同一个文件之前已经生成过（sha256 相同）"
                    except Exception:
                        continue
    except Exception:
        return False, "去重台账读不了（按 fail-closed 处理）"
    meta["_sha256"] = h
    return True, "sha256 未重复"


def _f_text(path: str, meta: dict):
    """图里如果有文字（水印/二维码提醒），按未验证处理 ⇒ 判否（除非用户关掉这一层）。"""
    return None, "图片文字/水印检查尚未接（按 fail-closed 处理：默认不放行）"


def _f_classifier(path: str, meta: dict):
    """涉黄/涉政/暴力分类器**尚未接**（待拍板用哪个模型）⇒ 返回 None ⇒ 整链判否。"""
    return None, "内容分类器未接（按 fail-closed 处理：不确定不发）"


CHAIN = (("size", _f_size), ("dup", _f_dup), ("blacklist", _f_blacklist),
         ("text", _f_text), ("classifier", _f_classifier))


# 画质后缀（2026-09-18 加）：以前我们把**中文请求碎片**原样丢给模型（日志实录：`｜一 在窗台上看雨 猫`），
# 而多数文生图模型是英文语料训的、且没有风格/构图/光线提示 ⇒ 出来就是"写意抽象图"（用户当场吐槽）。
# 这里只追加**中性**的质量词（不加"照片级"这种会和动漫风打架的定语，风格由用户/模型自己指定）。
PROMPT_SUFFIX = ", highly detailed, sharp focus, well composed, natural lighting"
_QUALITY_WORDS = ("detailed", "sharp", "4k", "8k", "photorealistic", "masterpiece", "best quality",
                  "high resolution", "sharp focus", "well composed")


def build_prompt(subject: str, style: str = "") -> str:
    """把"要画什么"整理成像样的生图提示词：主体 + 风格 + 中性质量后缀（已有的质量词不重复加）。

    ⚠️ 语种口径：**优先英文**——模型侧（`gen_image` 工具）已被要求传英文画面描述；中文主体也照传
    （智谱/火山/通义这些国内模型认中文，Sana/FLUX 系认英文为主）。这里只做"别太干"的兜底。
    """
    s = str(subject or "").strip()
    st = str(style or "").strip()
    if not s:
        return ""
    tail = ""
    low = s.lower()
    if not any(w in low for w in _QUALITY_WORDS):
        tail = PROMPT_SUFFIX
    if st and st.lower() not in low:
        return "%s, %s%s" % (s, st, tail)
    return "%s%s" % (s, tail)


def strip_watermark(path: str, backend_id: str = "", has_token: bool = False,
                    bottom_ratio: float = 0.06) -> tuple:
    """**去掉在线图右下角那条水印带**（免密钥档去不掉时的兜底）：裁掉最底部一小条。

    为什么用"裁"而不是"抹"：水印是**固定贴在右下角**的（pollinations 实测 768×768 图上落在 y≈0.94~0.99
    那一带），裁掉底部 6% 是**诚实的修剪**——留下的像素都是原图，不伪造、不做局部涂改。
    ⚠️ 只对**明确带水印的后端**（pollinations 且没给 token）动；带 token 或要 key 的那三家本来就没有水印。

    返回 `(新路径 或 原路径, 说明)`；任何异常都**原样返回原图**（宁可带水印，也别把图弄坏）。
    """
    try:
        if str(backend_id or "") != "pollinations" or has_token:
            return path, ""
        from PIL import Image
        im = Image.open(path)
        w, h = im.size
        cut = int(h * max(0.02, min(0.15, float(bottom_ratio or 0.06))))
        if cut < 4 or (h - cut) < 64:
            return path, ""
        im2 = im.crop((0, 0, w, h - cut))
        im2.save(path)
        return path, "已裁掉底部 %d px 的水印带（%d×%d → %d×%d）" % (cut, w, h, w, h - cut)
    except Exception as e:
        return path, "去水印跳过（%s）" % str(e)[:40]


def run_filters(path: str, meta: dict) -> tuple:
    """跑过滤链：返回 (ok, 逐层结论列表)。任一层判否 **或判不出（None）** ⇒ ok=False。"""
    on = cfg().get("filter_chain") or {}
    results = []
    ok = True
    for name, fn in CHAIN:
        if on.get(name) is False:
            results.append({"name": name, "ok": None, "why": "该层被用户关掉（未参与判定）", "skipped": True})
            continue
        try:
            good, why = fn(path, meta)
        except Exception as e:
            good, why = None, "该层异常：%s" % type(e).__name__
        results.append({"name": name, "ok": good, "why": why})
        if good is not True:
            ok = False
    return ok, results


# ————————————————— ⑤ 入口 —————————————————
def generate(chat_id: str, request_text: str, out_dir: str = None):
    """群友要图的主入口：解析 → 红线 → 选后端 → 生成 → 过滤。返回 dict（绝不假装成功）。"""
    c = cfg()
    if not c.get("enabled"):
        return {"ok": False, "why": "群友要图这个能力还没打开（控制台里默认关）"}
    intent = parse_intent(request_text)
    if intent.get("wants_real_face") and not ALLOW_REAL_FACE:
        return {"ok": False, "why": "涉及真人换脸/换身体，这个功能不提供（红线，没有开关）", "intent": intent}
    if intent.get("nsfw"):
        return {"ok": False, "why": "请求涉及成人内容，不生成（红线）", "intent": intent}
    n = min(max(1, int(intent.get("count") or 1)), max(1, int(c.get("max_count") or 1)))
    backend, why = pick_backend()
    if not backend:
        return {"ok": False, "why": why, "intent": intent}
    try:
        # 🔴 2026-09-18：提示词过 `build_prompt`（主体＋风格＋中性质量后缀）——以前是把中文碎片
        #   原样喂给模型，出来的图像"写意抽象图"（用户原话「你觉得这是一个猫吗？…写实是完全不行的」）。
        _pr = build_prompt(intent.get("subject"), intent.get("style") or intent.get("style_hint") or "")
        resp = call_backend(backend, _pr or intent["subject"], n, intent.get("size") or "square")
    except Exception as e:
        return {"ok": False, "why": "生图后端调用失败：%s" % type(e).__name__, "intent": intent}
    files = [str(p) for p in (resp.get("files") or [])]
    if not files:
        return {"ok": False, "why": "后端没返回图片文件（原样返回：%s）" % str(resp)[:120], "intent": intent}
    # 🔴 2026-09-18：**去水印**（用户口径「可以找找有没有去水印的，把它加到这条链里面」）。
    #   优先级：①带 token / 要 key 的后端**本来就没水印** ⇒ 什么都不做；②免密钥那条（pollinations 无 token）
    #   的水印去不掉（官方要求有账号）⇒ 按 `image_gen.strip_watermark`（默认开）裁掉底部水印带。
    _wm_note = ""
    if c.get("strip_watermark", True) is not False:
        for _i, _p in enumerate(files):
            _np, _note = strip_watermark(_p, str(backend.get("id") or ""),
                                         bool(str(backend.get("key") or "").strip()))
            files[_i] = _np
            if _note:
                _wm_note = _note
    out = []
    for p in files:
        ok, res = run_filters(p, {"prompt": intent["subject"], "chat_id": chat_id,
                                  "backend": backend["id"], "when": time.strftime("%Y-%m-%d %H:%M:%S")})
        if ok:
            _note_generated(p, str(backend.get("id") or "gen"))     # ⚠️ 记账必须在过滤通过之后（见 _note_generated 注释）
        out.append({"path": p, "ok": ok, "filters": res})
    good = [x for x in out if x["ok"]]
    if not good:
        return {"ok": False, "why": "生成出来了，但**过滤没全过** ⇒ 不发（不确定就不发）", "results": out,
                "intent": intent}
    return {"ok": True, "why": "生成并通过过滤：%d 张%s" % (len(good), ("｜" + _wm_note) if _wm_note else ""),
            "files": [x["path"] for x in good],
            "results": out, "intent": intent, "backend": backend["id"], "watermark_note": _wm_note}


def snapshot() -> dict:
    """给控制台用的只读快照。"""
    c = cfg()
    return {
        "enabled": bool(c.get("enabled")), "trigger_mode": c.get("trigger_mode"),
        "online_allowed": bool(c.get("online_allowed")),
        "backends": backends(), "backend_count": len(backends()),
        "max_count": int(c.get("max_count") or 1),
        "filter_chain": c.get("filter_chain") or {},
        "red_line": {"allow_real_face": ALLOW_REAL_FACE, "r18_switch_exists": False},
    }
