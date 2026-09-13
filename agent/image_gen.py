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


def cfg() -> dict:
    d = dict(DEFAULTS)
    try:
        got = _config.get_config().get("image_gen") or {}
        if isinstance(got, dict):
            d.update(got)
    except Exception:
        pass
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
    out = []
    for b in (cfg().get("backends") or []):
        try:
            if not isinstance(b, dict) or not b.get("id") or not b.get("url"):
                continue
            if str(b.get("kind") or "local") == "online" and not cfg().get("online_allowed"):
                continue                      # 在线后端必须显式允许出网
            out.append({"id": str(b["id"]), "kind": str(b.get("kind") or "local"),
                        "url": str(b["url"]), "timeout": int(b.get("timeout") or 120)})
        except Exception:
            continue
    return out


def pick_backend():
    """挑一个后端；没有 ⇒ (None, 人话原因)。"""
    bs = backends()
    if not bs:
        online = [b for b in (cfg().get("backends") or []) if isinstance(b, dict) and str(b.get("kind")) == "online"]
        if online and not cfg().get("online_allowed"):
            return None, "只配了在线生图后端，但 image_gen.online_allowed=False（出网未允许）"
        return None, "还没配生图后端（本地 ComfyUI / 在线 API 二选一，配好后再试）"
    return bs[0], ""


def call_backend(backend: dict, prompt: str, count: int = 1, size: str = "square"):
    """真的去生图（HTTP）。**未接后端时不会被调用**；请求体里不构造任何 r18 字段。"""
    import urllib.request
    body = json.dumps({"prompt": prompt, "n": int(count), "size": size}).encode("utf-8")
    req = urllib.request.Request(backend["url"], data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or 120)) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


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
