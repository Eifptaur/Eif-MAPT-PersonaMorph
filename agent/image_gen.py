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
    """后端列表：list[dict] 直接用；字符串按 `id|local|url; id|online|url` 解析（格式不对的条目丢弃）。

    为什么不给控制台单独做增删端点：照用户"低成本优先"的原则，一个输入框 + 一种人话格式就够用；
    解析不出来 ⇒ 后端列表为空 ⇒ `generate()` 会明确说"还没配生图后端"（不会静默当成功）。
    """
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, dict)]
    out = []
    if isinstance(v, str):
        for chunk in re.split(r"[;；\n]", v):
            f = [x.strip() for x in chunk.split("|")]
            if len(f) >= 3 and f[0] and f[2]:
                out.append({"id": f[0], "kind": (f[1] or "local"), "url": f[2]})
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
    return out


def pick_backend():
    """挑一个后端；没有 ⇒ (None, 人话原因)。"""
    bs = backends()
    if not bs:
        online = [b for b in (cfg().get("backends") or []) if isinstance(b, dict) and str(b.get("kind")) == "online"]
        if online and not cfg().get("online_allowed"):
            return None, "只配了在线生图后端，但 image_gen.online_allowed=False（出网未允许）"
        return None, ("本机没探到常见生图服务（A1111/Fooocus :7860/:7865 · ComfyUI :8188 · InvokeAI :9090），"
                      "也没有在控制台填后端 ⇒ 把其中一个跑起来，或打开「允许出网」用免密钥在线生图")
    return bs[0], ""


# ————— ③-b 后端协议适配：本地自动发现 + 免密钥在线（既有口径：别等我选型，直接找能用的）—————
#   本地：常见生图服务的默认端口 + 一个只读探测路径（探到就用，不用用户手配）。
#   在线：pollinations 是**免密钥**的 text-to-image（GET 一个 URL 就出图）；出网仍受 online_allowed 管。
SIZE_PX = {"square": (1024, 1024), "portrait": (832, 1216), "landscape": (1216, 832)}
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


def detect_local(timeout: float = 1.2, probes=None) -> list:
    """探一遍常见本地生图服务（**只读 GET**，不动对方任何状态）⇒ 探到就返回可用的后端。

    为什么要有它：用户 2026-09-13 明确说"生图，不能直接去找那些能生图的模型或者工具吗"——别让他先选型、
    再手工填地址；本机在跑 ComfyUI/A1111/Fooocus/InvokeAI 就自己认出来。
    """
    import urllib.request
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

    支持三种协议：`a1111`（SD WebUI / Fooocus / InvokeAI 兼容层，POST /sdapi/v1/txt2img）·
    `pollinations`（免密钥在线，GET 一个 URL 就出图）· `generic`（用户自己填的 HTTP 端点，POST {prompt,n,size}）。
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
        for i in range(n):
            url = (backend["url"].rstrip("/") + "/" + urllib.parse.quote(prompt)
                   + "?width=%d&height=%d&nologo=true&seed=%d" % (w, h, int(time.time()) + i))
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or 180)) as resp:
                data = resp.read()
            if not data or len(data) < 128:
                raise ValueError("在线生图返回的数据太小（%d 字节）" % len(data or b""))
            files.append(_save_image_bytes(data, str(backend.get("id") or "pollinations")))
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
        resp = call_backend(backend, intent["subject"], n, intent.get("size") or "square")
    except Exception as e:
        return {"ok": False, "why": "生图后端调用失败：%s" % type(e).__name__, "intent": intent}
    files = [str(p) for p in (resp.get("files") or [])]
    if not files:
        return {"ok": False, "why": "后端没返回图片文件（原样返回：%s）" % str(resp)[:120], "intent": intent}
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
    return {"ok": True, "why": "生成并通过过滤：%d 张" % len(good), "files": [x["path"] for x in good],
            "results": out, "intent": intent, "backend": backend["id"]}


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
