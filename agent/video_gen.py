# -*- coding: utf-8 -*-
"""AI 视频生成（用户 2026-09-15 提："能让模型自己选取工具并生成 AI 视频"）。

形制**照抄 `agent/image_gen.py`**（那条链已经把"开关 → 触发条件 → 后端 → 出网闸 → 过滤链 →
落盘 → 发送 → 只读快照 → 面板 → 引导"整套跑通了），VIDEO 版把"图"换成"短视频"，并按视频的
特殊性多两条：**时长上限**与**异步生成**（视频要几十秒到几分钟，不能让聊天卡在那儿）。

七条"相关事宜"的落点（用户点名要检查的）：
  ① 后端选型——本模块不写死：支持"用户自填 HTTP 端点"（`generic`）与"本机自动发现"；
     真后端（本地 ComfyUI / 在线 API）由用户在控制台填，**默认不配 ⇒ 默认关**。
  ② 异步——`submit()` 起后台线程生成，工具**立刻**回"已在做"，做完再发；不阻塞对话。
  ③ 成本——在线按次收费，面板上有单次上限与出网闸（`online_allowed` 默认关）。
  ④ 合规——红线沿用生图那套（**不做真人换脸**），且生成物一律由调用方标注"AI 生成"。
  ⑤ 传输——发送走既有的投递档 `send_file`（不碰真实鼠标），失败如实报。
  ⑥ 六格交付——功能 / 判据 / UI / 交互（空态·失败态·未配置态） / 中文文案 / 说明同步。
  ⑦ 触发条件有 UI——`trigger_mode` 是配置项，面板上有档位，不写死在提示词里。

口径（与生图一致）：**绝不假装**——没后端、端点不通、回的不是视频、过滤不过 ⇒ 一律如实报错，
并把失败原因原样写回，绝不返回空文件或编造的路径。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TIMEOUT = 300                      # 视频比图慢得多，默认给 5 分钟
MAX_SECONDS = 30                          # 单条时长上限（秒）——超过就拒
MAX_MB = 30.0                             # 单条体积上限
PROMPT_MAX = 500

#: 红线（与生图同一套口径）：真人换脸这类不做
_RED_LINE = [("换脸", "真人换脸属于红线，不做"),
             ("明星", "涉及真实人物形象，不做"),
             ("deepfake", "真人换脸属于红线，不做")]


def cfg() -> dict:
    try:
        from .config import get_config
        return dict((get_config() or {}).get("video_gen") or {})
    except Exception:
        return {}


def enabled() -> bool:
    c = cfg()
    return bool(c.get("enabled")) and str(c.get("trigger_mode") or "on_request") != "off"


def out_dir() -> str:
    d = os.path.join(ROOT, "data", "gen_videos")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _as_list(v) -> list:
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[,\n;，；]", str(v or "")) if x.strip()]


def backends() -> list:
    """把配置里的后端列表规范化成 dict：`{id, url, proto, timeout, workflow}`。

    **不二择一**（用户 2026-09-15 原话："后端肯定是让用户自己选啊，我们给他提供最多的选项，
    要求就是这样的，不是非得二择一的"）⇒ 支持**一行一个、填多少个都行**，程序按顺序挨个试。

    写法（控制台里一行一个，逗号/换行分隔；`协议:` 前缀可省，省了就是 generic）：
      · `http://127.0.0.1:8189/generate`      ⇒ generic：POST {prompt,seconds,size}，
                                                  回视频字节 / JSON{url|files|video_b64} 都收
      · `comfyui:http://127.0.0.1:8188`       ⇒ 本机 ComfyUI：提交 API 格式工作流 + 轮询历史
      · `generic:https://你的中转/v1/video`    ⇒ 任意自建/中转端点
      · 别的协议名不会被认（会**如实列出"不支持的协议"**，而不是悄悄当 generic 用）
    """
    out = []
    unknown = []
    for raw in _as_list(cfg().get("backends")):
        proto, url = "generic", raw
        if ":" in raw and not raw.lower().startswith(("http://", "https://")):
            head, tail = raw.split(":", 1)
            if head in KNOWN_PROTOS:
                proto, url = head, tail.strip()
            elif head and not head.startswith("/"):
                unknown.append(head)
        if not url.lower().startswith(("http://", "https://")):
            continue
        out.append({"id": "%s-%d" % (proto, len(out) + 1), "proto": proto, "url": url,
                    "timeout": int(cfg().get("timeout") or DEFAULT_TIMEOUT),
                    "workflow": str(cfg().get("comfy_workflow") or "").strip()})
    for u in unknown:
        out.append({"id": "unsupported-%s" % u, "proto": "__unsupported__", "url": "",
                    "timeout": 0, "workflow": "", "why": "不支持的协议：%s" % u})
    return out


#: 认得的协议（认不得的会如实报，不静默降级）
KNOWN_PROTOS = ("generic", "comfyui")


def online_allowed() -> bool:
    return bool(cfg().get("online_allowed"))


def _is_local(url: str) -> bool:
    try:
        h = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    return h in ("127.0.0.1", "localhost", "::1") or h.startswith("192.168.") or h.startswith("10.")


def red_line_hit(prompt: str):
    """返回命中的红线原因；没命中返回空串。**在调用后端之前先拦**。"""
    low = str(prompt or "").lower()
    for kw, why in _RED_LINE:
        if kw in low:
            return why
    return ""


def parse_intent(text: str) -> dict:
    """从群友的话里读意图：想要几条、多长。读不出就用默认值（不瞎猜内容）。"""
    t = str(text or "")
    secs = None
    m = re.search(r"(\d+)\s*(?:秒|s\b)", t, re.I)
    if m:
        secs = int(m.group(1))
    return {"seconds": min(int(secs or cfg().get("seconds_default") or 5), MAX_SECONDS),
            "prompt": t.strip()[:PROMPT_MAX]}


def _save_bytes(data: bytes, tag: str) -> str:
    d = out_dir()
    name = "gen_%s_%s.mp4" % (re.sub(r"[^A-Za-z0-9_-]", "", tag)[:24] or "video",
                              time.strftime("%H%M%S") + ("%03d" % (int(time.time() * 1000) % 1000)))
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "pm-video-gen"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _comfy_generate(backend: dict, prompt: str, seconds: int):
    """本机 ComfyUI：提交 **API 格式工作流**（用户在控制台指一个 .json）→ 轮询历史取输出。

    为什么走工作流文件：ComfyUI 的图/视频管线是节点图，没有"一把梭"的通用参数；
    让用户把自己调好的工作流导成 API 格式（`工作流 → 导出（API）`）指给我们，才是**真能用**的路子。
    工作流里凡是字符串值等于 `%PROMPT%` / `%SECONDS%` 的地方会被替换（不写就原样跑）。
    """
    wf = str(backend.get("workflow") or "")
    if not wf or not os.path.isfile(wf):
        raise ValueError("ComfyUI 要指一个工作流 JSON（控制台里填「工作流文件」路径），当前没有")
    with open(wf, encoding="utf-8") as f:
        txt = f.read()
    txt = txt.replace("%PROMPT%", json.dumps(prompt, ensure_ascii=False)[1:-1]).replace("%SECONDS%", str(int(seconds)))
    try:
        graph = json.loads(txt)
    except Exception as e:
        raise ValueError("工作流 JSON 读不动：%s" % type(e).__name__)
    base = backend["url"].rstrip("/")
    body = json.dumps({"prompt": graph, "client_id": "pm-video-gen"}).encode("utf-8")
    req = urllib.request.Request(base + "/prompt", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        pid = str((json.loads(r.read().decode("utf-8") or "{}") or {}).get("prompt_id") or "")
    if not pid:
        raise ValueError("ComfyUI 没返回 prompt_id（工作流可能不合法）")
    t0 = time.time()
    while time.time() - t0 < int(backend.get("timeout") or DEFAULT_TIMEOUT):
        time.sleep(1.5)
        with urllib.request.urlopen(base + "/history/" + pid, timeout=20) as r:
            hist = json.loads(r.read().decode("utf-8") or "{}")
        ent = (hist or {}).get(pid)
        if not ent:
            continue
        outs = (ent.get("outputs") or {})
        files = []
        for node in outs.values():
            for key in ("gifs", "videos", "images"):
                for it in (node.get(key) or []):
                    fn = str((it or {}).get("filename") or "")
                    sub = str((it or {}).get("subfolder") or "")
                    typ = str((it or {}).get("type") or "output")
                    if not fn:
                        continue
                    q = urllib.parse.urlencode({"filename": fn, "subfolder": sub, "type": typ})
                    data = _get(base + "/view?" + q, 60)
                    if len(data) > 4096:
                        files.append(_save_bytes(data, "comfy"))
        if files:
            return {"files": files, "proto": "comfyui", "why": ""}
        if (ent.get("status") or {}).get("status_str") == "error":
            raise ValueError("ComfyUI 执行报错（去它的界面看红字）")
    raise ValueError("ComfyUI 在超时内没产出（工作流太长或卡住）")


def call_backend(backend: dict, prompt: str, seconds: int = 5):
    """真去生成 ⇒ `{files:[路径], proto, why}`。**没配后端时不会被调用**。

    generic 收三种响应（覆盖常见自建服务）：
      ① 直接回视频字节（Content-Type: video/*）——最省事；
      ② 回 JSON，里面有 `url`/`video_url`/`output` ⇒ 再 GET 下载；
      ③ 回 JSON，里面有 `files`/`path` ⇒ 当成**本地路径**收下（先检查存在）。
    其它一律如实报错（"回的不是视频"），不落空文件。
    """
    if backend.get("proto") == "comfyui":
        return _comfy_generate(backend, prompt, seconds)
    if backend.get("proto") == "__unsupported__":
        raise ValueError(backend.get("why") or "不支持的协议")
    import base64
    body = json.dumps({"prompt": prompt, "seconds": int(seconds), "size": "video"}).encode("utf-8")
    req = urllib.request.Request(backend["url"], data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=int(backend.get("timeout") or DEFAULT_TIMEOUT)) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        raw = resp.read()
    files = []
    if ctype.startswith("video/") or raw[:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x20") or b"ftyp" in raw[:16]:
        files.append(_save_bytes(raw, backend.get("id") or "video"))
    else:
        try:
            j = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            j = {}
        u = ""
        for k in ("url", "video_url", "output", "video"):
            if isinstance(j.get(k), str) and j[k].strip():
                u = j[k].strip()
                break
        if u.startswith("http"):
            files.append(_save_bytes(_get(u, int(backend.get("timeout") or DEFAULT_TIMEOUT)),
                                     backend.get("id") or "video"))
        elif isinstance(j.get("files"), list) and j["files"]:
            for p in j["files"]:
                if os.path.isfile(str(p)):
                    files.append(str(p))
            if not files:
                raise ValueError("后端回了 files，但那些路径在本机都不存在")
        elif isinstance(j.get("video_b64"), str) and j["video_b64"]:
            files.append(_save_bytes(base64.b64decode(j["video_b64"].split(",")[-1]),
                                     backend.get("id") or "video"))
        else:
            raise ValueError("后端回的不是视频（Content-Type=%s，也没有 url/files 字段）" % (ctype or "空"))
    return {"files": files, "proto": backend.get("proto") or "generic", "why": ""}


# ── 过滤链（任一层判否/出错 ⇒ 不发）─────────────────────────────────────────
def _f_size(path: str, meta: dict):
    try:
        mb = os.path.getsize(path) / 1048576.0
    except Exception:
        return False, "文件读不到"
    if mb <= 0:
        return False, "空文件"
    if mb > MAX_MB:
        return False, "体积 %.1f MB 超过上限 %.0f MB" % (mb, MAX_MB)
    return True, "%.2f MB" % mb


def probe_seconds(path: str):
    """用 ffprobe 读时长 ⇒ `(秒|None, 原因)`。没有 ffprobe 就如实说读不到（不假装）。"""
    import shutil
    exe = shutil.which("ffprobe")
    if not exe:
        return None, "本机没有 ffprobe，读不出时长"
    try:
        r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, timeout=30,
                           creationflags=0x08000000 if os.name == "nt" else 0)   # 不许闪控制台窗
        return (float((r.stdout or "").strip()), "") if r.returncode == 0 else (None, "ffprobe rc=%s" % r.returncode)
    except Exception as e:
        return None, "ffprobe 跑不动：%s" % type(e).__name__


def _f_duration(path: str, meta: dict):
    sec, why = probe_seconds(path)
    if sec is None:
        return True, why or "时长未验"          # 读不到时长**不当成不过**，但要如实写出来
    if sec > MAX_SECONDS + 0.5:
        return False, "时长 %.1fs 超过上限 %ds" % (sec, MAX_SECONDS)
    return True, "%.1fs" % sec


def _f_dup(path: str, meta: dict):
    try:
        with open(path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
    except Exception:
        return False, "算不出指纹"
    meta["md5"] = md5
    seen = _load_seen()
    if md5 in seen:
        return False, "这条跟之前生成过的完全一样（重复）"
    seen[md5] = time.time()
    _save_seen(seen)
    return True, md5[:8]


def _seen_path() -> str:
    return os.path.join(ROOT, "data", "gen_videos_seen.json")


def _load_seen() -> dict:
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            return dict(json.load(f) or {})
    except Exception:
        return {}


def _save_seen(d: dict) -> None:
    try:
        items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:500]
        with open(_seen_path(), "w", encoding="utf-8") as f:
            json.dump(dict(items), f, ensure_ascii=False)
    except Exception:
        pass


def _f_redline(path: str, meta: dict):
    why = red_line_hit(meta.get("prompt") or "")
    return (False, why) if why else (True, "未命中红线")


def _f_classifier(path: str, meta: dict):
    """内容分类器：本项目**没有内置模型**（不引大依赖）。开着就说"没装分类器"，
    把它记进结论但不因此拦截——**如实性优先于假装把关**。"""
    return True, "本机没装内容分类器（只记录，不拦）"


FILTERS = [("size", _f_size), ("duration", _f_duration), ("dup", _f_dup),
           ("redline", _f_redline), ("classifier", _f_classifier)]


def run_filters(path: str, meta: dict):
    """按面板开关跑过滤链 ⇒ `(过没过, 说明列表)`。任一层判否 ⇒ 不过（fail-closed）。"""
    chain = dict(cfg().get("filter_chain") or {})
    notes = []
    for name, fn in FILTERS:
        if chain.get(name) is False:
            notes.append("%s=关" % name)
            continue
        try:
            okay, detail = fn(path, meta)
        except Exception as e:
            return False, notes + ["%s=异常(%s)" % (name, type(e).__name__)]
        notes.append("%s=%s" % (name, detail))
        if not okay:
            return False, notes
    return True, notes


def candidate_backends():
    """按顺序给出**可以试的**后端（⇒ `(列表, 被出网闸挡下的原因)`）。

    "不二择一"的落地：本地与在线**都留着**，按填写顺序挨个试；只有"允许出网"关着时，
    非本机地址才会被挡下（并把原因说清楚，免得用户以为配错了）。
    """
    bs = [b for b in backends() if b.get("proto") != "__unsupported__"]
    bad = [b.get("why") or "" for b in backends() if b.get("proto") == "__unsupported__"]
    if not bs:
        if bad:
            return [], "填的地址没被认出协议：" + "、".join(x for x in bad if x)
        return [], "还没配视频生成后端（控制台里填一个地址，可填多个，一行一个）"
    if online_allowed():
        return bs, ""
    local = [b for b in bs if _is_local(b["url"])]
    return local, ("" if local else "只配了在线后端，而「允许出网」是关的（默认关）⇒ 这次不做")


def pick_backend():
    """兼容旧调用：给"第一个可以试的"⇒ `(backend|None, 原因)`。"""
    bs, why = candidate_backends()
    return (bs[0], "") if bs else (None, why)


def generate(request_text: str, prompt: str = None):
    """同步生成一条 ⇒ `{ok, files, why, notes, seconds}`。失败一律给原因，不落空文件。"""
    if not enabled():
        return {"ok": False, "files": [], "why": "视频生成没开（控制台里打开总开关，并配一个后端）", "notes": []}
    it = parse_intent(request_text)
    p = (prompt or it["prompt"] or "").strip()
    if not p:
        return {"ok": False, "files": [], "why": "没说想要什么内容", "notes": []}
    hit = red_line_hit(p)
    if hit:
        return {"ok": False, "files": [], "why": hit, "notes": []}
    cands, gate_why = candidate_backends()
    if not cands:
        return {"ok": False, "files": [], "why": gate_why, "notes": []}
    # 按填写顺序挨个试——这就是"给用户最多选项、程序自己挑能用的"
    tried = []
    r = None
    for b in cands:
        try:
            r = call_backend(b, p, it["seconds"])
            if r.get("files"):
                break
            tried.append("%s：没产出文件" % b["id"])
            r = None
        except Exception as e:
            tried.append("%s：%s" % (b["id"], str(e)[:70] or type(e).__name__))
            r = None
    if not r:
        return {"ok": False, "files": [], "why": "配的后端都没成功 ⇒ " + "；".join(tried[-3:]), "notes": tried}
    files = list(r.get("files") or [])
    if not files:
        return {"ok": False, "files": [], "why": "后端没给出视频文件", "notes": tried}
    kept, notes = [], []
    for f in files:
        okf, nt = run_filters(f, {"prompt": p})
        notes.extend(nt)
        if okf:
            kept.append(f)
        else:
            try:
                os.remove(f)                 # 不过就删掉，绝不留在盘上
            except Exception:
                pass
    if not kept:
        return {"ok": False, "files": [], "why": "生成的东西没过过滤链（原因见 notes）", "notes": notes}
    return {"ok": True, "files": kept, "why": "", "notes": notes, "seconds": it["seconds"]}


# ── 异步：不让聊天卡住 ──────────────────────────────────────────────────────
_LOCK = threading.Lock()
_JOBS = {}                                  # job_id -> {state, files, why, started, prompt}


def submit(request_text: str, prompt: str = None) -> dict:
    """提交一次后台生成 ⇒ `{ok, job_id, why}`。**立刻返回**，生成在后台线程里做。

    调用方（工具）拿到 job_id 就该如实告诉对方"正在做"，**不许说已经做好了**。
    """
    if not enabled():
        return {"ok": False, "job_id": "", "why": "视频生成没开（控制台里打开总开关，并配一个后端）"}
    jid = "v%d" % int(time.time() * 1000)
    with _LOCK:
        # 最多同时跑 2 个，防把机器打满（超了如实拒绝）
        running = [j for j in _JOBS.values() if j.get("state") == "running"]
        if len(running) >= 2:
            return {"ok": False, "job_id": "", "why": "已经有两条在做，等它们出来再做（同时做会拖慢机器）"}
        _JOBS[jid] = {"state": "running", "files": [], "why": "", "started": time.time(),
                      "prompt": str(prompt or request_text or "")[:200], "result": None}

    def _work():
        res = generate(request_text, prompt)
        with _LOCK:
            j = _JOBS.get(jid) or {}
            j["result"] = res
            j["files"] = res.get("files") or []
            j["why"] = res.get("why") or ""
            j["state"] = "done" if res.get("ok") else "failed"
            j["ended"] = time.time()

    threading.Thread(target=_work, name="pm-video-%s" % jid, daemon=True).start()
    return {"ok": True, "job_id": jid, "why": ""}


def job(job_id: str) -> dict:
    with _LOCK:
        j = dict(_JOBS.get(job_id) or {})
    return j


def jobs() -> list:
    with _LOCK:
        return [dict(j, id=k) for k, j in _JOBS.items()]


def snapshot() -> dict:
    """给控制台的只读快照（与生图同一形制）。"""
    c = cfg()
    bs = backends()
    return {
        "enabled": bool(c.get("enabled")),
        "trigger_mode": str(c.get("trigger_mode") or "on_request"),
        "online_allowed": online_allowed(),
        "backends": [{"id": b["id"], "proto": b["proto"], "url": b["url"]} for b in bs],
        "filter_chain": dict(c.get("filter_chain") or {}),
        "limits": {"max_seconds": MAX_SECONDS, "max_mb": MAX_MB},
        "red_line": {"allow_real_face": False},
        "running": len([j for j in _JOBS.values() if j.get("state") == "running"]),
        "recent": [{"id": j.get("id"), "state": j.get("state"), "why": j.get("why"),
                    "seconds": round((j.get("ended") or time.time()) - (j.get("started") or 0), 1)}
                   for j in jobs()[-5:]],
        "note": "视频比图慢得多：提交后后台生成、好了再发；没配后端时模型会如实说「还没配后端」。",
    }
