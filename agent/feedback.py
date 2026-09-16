# -*- coding: utf-8 -*-
"""反馈收集与投递（2026-09-14 用户要求「左导航单开一栏 + 自动提交 + 发邮件」）。

用户原话：「反馈功能需要在左导航单开一栏…**让用户直接在控制台里面填，然后自动提交就好，
没必要让用户去邮箱那儿填，程序自动整理并把用户的诉求发邮件**。所有添加上的功能，你要自己测一测」
  ⚠️ 2026-09-15 口径更新：「没必要让程序帮我整理，反正他只要用邮箱发到我的邮箱就行」⇒ 正文＝用户原话原样，只留一行元信息（见 `compose()`）。

设计三条硬口径：
  ① **三态如实**：`sent`（真的发出去了）/ `queued`（没配通道 ⇒ 落盘排队，明确告诉用户"还没发出去"）
     / `error`；**绝不留假成功**（这条是本项目反复踩过的坑：假成功比报错更糟）。
  ② **不把个人信息写进代码**：收件人邮箱、SMTP 授权码只存在 `config.json`（已 gitignore），
     代码与示例配置里一律留空——外发包扫描闸门也会拦 PII。
  ③ **一条一档**：每条反馈独立 append 到 `data/feedback.jsonl`（带 id/时间/版本/环境摘要），
     发送成功后回写 `sent_at`；重发按"没有 sent_at"筛，幂等、不重复打扰收件人。

通道（按顺序尝试，任一成功即算 sent）：
  · `feedback.upload_url`：POST JSON 到你自己的中转（三态返回，见下）；
  · `feedback.smtp`：SMTP 直发（默认 smtp.qq.com:465 SSL；密码填 QQ 邮箱**授权码**，不是登录密码）。

用法（程序内）：`submit({...})` / `pending()` / `flush()`；判据见 `scripts/feedback_selftest.py`。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import smtplib
import ssl
import time
import uuid
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_FILE = os.path.join(ROOT, "data", "feedback.jsonl")

#: 反馈类型（前端下拉与后端校验共用同一份，避免"界面能填、后端不认"）
KINDS = ("问题", "建议", "想法", "其他")

#: 防刷限流默认值（2026-09-15 用户要求：「要是有人一瞬间给我发 100 封怎么办」）。
#: 数值依据：正常用户一天写 2~3 条反馈 ⇒ 留约 6 倍余量；真被刷时收件箱最多被灌 20 封/天。
#: ⚠️ 这是**咽喉点**：控制台表单、HTTP 接口、机器人工具都走 `submit()`，改这里一处全生效。
LIMIT = {"per_minute": 3, "per_hour": 10, "per_day": 20, "dup_window_s": 600}
#: 被限流拦下的**不写进 feedback.jsonl**（否则刷子能把存档撑爆），只在这里留一行轻量痕迹。
REJECT_FILE = os.path.join(ROOT, "data", "feedback_rejected.jsonl")

#: 附件落盘目录：用户传的图片/文件**只留在本机**（推送得出去就顺手送过去，送不出去也如实说）。
MEDIA_DIR = os.path.join(ROOT, "data", "feedback_media")
#: 附件上限——**数字来自接收端的硬限制**，不是随手定的：
#: 企业微信群机器人的 image 消息要求 `base64 + md5`、原图 **≤ 2MB**；`webhook/upload_media` 的 file **≤ 20MB**。
#: `keep_*` 是"会写盘就必须有上限"（用户口径）：附件目录超过 200 个文件或 200MB 就从最旧的删起。
ATTACH = {"max_files": 4, "image_max": 2 * 1024 * 1024, "file_max": 20 * 1024 * 1024,
          "total_max": 20 * 1024 * 1024, "keep_files": 200, "keep_bytes": 200 * 1024 * 1024}
#: 认得出是图片的扩展名（决定走 base64 图片消息还是 upload_media 文件消息）
IMG_EXT = ("png", "jpg", "jpeg", "gif", "bmp", "webp")

#: 联系邮箱（选填）：留了就说明用户愿意被回信 ⇒ 合法才收，写错当场告诉他，别默默丢掉。
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def clean_email(s) -> str:
    """把联系邮箱归一化：空 ⇒ ""；**合法才返回原值，不合法一律返回 ""**（调用方据此判"写错了"）。"""
    v = str(s or "").strip()[:120]
    return v if EMAIL_RE.match(v) else ""


def human_size(n) -> str:
    n = int(n or 0)
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n


def _safe_name(name, fallback="附件") -> str:
    """附件名只留文件名本身并去掉非法字符（**不许带路径**：免得用户传个 `../..` 写到别处）。"""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(". ")
    return (base or fallback)[:80]


def is_image(name) -> bool:
    return str(name or "").lower().rsplit(".", 1)[-1] in IMG_EXT


def save_attachments(fid: str, files) -> tuple:
    """把前端传来的附件（base64）落到 `data/feedback_media/<id>/` ⇒ `(saved, notes)`。

    三态如实的老规矩照旧：**存不下就明说哪一条、为什么**（`notes` 会拼进给用户的回执里），
    绝不留"看着提交成功了、其实附件丢了"。超上限的一律不收，不做静默截断。
    """
    saved, notes = [], []
    files = [f for f in list(files or []) if isinstance(f, dict)]
    if not files:
        return saved, notes
    cap_n = int(ATTACH["max_files"])
    if len(files) > cap_n:
        notes.append("一次最多带 %d 个附件（多出来的没存）" % cap_n)
    total = 0
    for i, f in enumerate(files[:cap_n]):
        name = _safe_name(f.get("name"))
        img = is_image(name)
        try:
            raw = base64.b64decode(str(f.get("data") or ""), validate=False)
        except Exception:
            notes.append("%s：内容读不出来" % name)
            continue
        if not raw:
            notes.append("%s：是空文件" % name)
            continue
        cap = int(ATTACH["image_max"]) if img else int(ATTACH["file_max"])
        if len(raw) > cap:
            notes.append("%s：%s 超过上限（图片 2MB / 文件 20MB）" % (name, human_size(len(raw))))
            continue
        if total + len(raw) > int(ATTACH["total_max"]):
            notes.append("%s：加起来超过 20MB 了" % name)
            break
        try:
            d = os.path.join(MEDIA_DIR, str(fid))
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "%d_%s" % (i + 1, name))
            with open(p, "wb") as fh:
                fh.write(raw)
        except Exception as e:
            notes.append("%s：存不下（%s）" % (name, type(e).__name__))
            continue
        saved.append({"name": name, "size": len(raw), "image": img, "path": p})
        total += len(raw)
    return saved, notes


def prune_media() -> dict:
    """附件目录的上限（"凡会写盘的功能都要有上限与清理"）：超 200 个文件或 200MB ⇒ 从最旧的删起。

    **只动 `data/feedback_media/` 这一棵树**（自己造的才收拾），删了几个、回收多少照实返回。
    """
    removed, freed = 0, 0
    try:
        items = []
        for dirpath, _dirs, names in os.walk(MEDIA_DIR):
            for n in names:
                p = os.path.join(dirpath, n)
                try:
                    items.append((os.path.getmtime(p), os.path.getsize(p), p))
                except Exception:
                    continue
        items.sort(key=lambda x: x[0])
        total = sum(x[1] for x in items)
        keep_n, keep_b = int(ATTACH["keep_files"]), int(ATTACH["keep_bytes"])
        for _mt, sz, p in items:
            if len(items) - removed <= keep_n and total - freed <= keep_b:
                break
            try:
                os.remove(p)
                removed += 1
                freed += sz
            except Exception:
                pass
    except Exception:
        pass
    return {"removed": removed, "freed": freed}



def _cfg() -> dict:
    try:
        from .config import get_config
        return (get_config() or {}).get("feedback", {}) or {}
    except Exception:
        return {}


def _version() -> str:
    try:
        import io
        import re
        p = os.path.join(ROOT, "agent", "console_html.py")
        mt = os.path.getmtime(p)
        return time.strftime("%m%d-%H%M", time.localtime(mt))
    except Exception:
        return "?"


def _read_all() -> list:
    out = []
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return out


def _write_all(items: list) -> bool:
    """原子重写（temp + os.replace）——反馈是用户唯一的手写内容，别写坏。"""
    try:
        os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
        tmp = FEEDBACK_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        os.replace(tmp, FEEDBACK_FILE)
        return True
    except Exception:
        return False


def _append(item: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def _limit() -> dict:
    """限流配置：config.json 的 `feedback.limit.*` 覆盖默认值（非法值一律忽略、回默认）。"""
    lim = dict(LIMIT)
    try:
        cfg = _cfg().get("limit") or {}
        for k in list(lim):
            if cfg.get(k) is not None:
                lim[k] = int(cfg.get(k))
    except Exception:
        pass
    return lim


def _norm(s) -> str:
    """归一化：去掉所有空白 + 转小写（"同一句话换个空格/大小写"也算同一条）。"""
    return "".join(str(s or "").split()).lower()


def _dup_key(item_or_text, names=None) -> str:
    """"算不算同一条"的键＝正文 + **附件名**（只发附件不写字时，正文是占位句，光看正文会误判重复）。"""
    if isinstance(item_or_text, dict):
        text = item_or_text.get("text")
        names = [f.get("name") for f in (item_or_text.get("files") or []) if isinstance(f, dict)]
    else:
        text = item_or_text
    return _norm(text) + "|" + ",".join(sorted(_norm(n) for n in (names or [])))


def _reject(why: str, item: dict) -> None:
    """被拒的一律**不落 `feedback.jsonl`**（防刷爆存档），只留一行轻量痕迹。"""
    try:
        os.makedirs(os.path.dirname(REJECT_FILE), exist_ok=True)
        with open(REJECT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(),
                                "at_h": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "why": why, "kind": item.get("kind"),
                                "len": len(str(item.get("text") or "")),
                                "head": str(item.get("text") or "")[:120]}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def rate_check(text: str, now: float = None, names=None) -> tuple:
    """限流判定 → `(ok, why)`。**唯一的咽喉点**：控制台表单 / HTTP / 机器人工具都走 `submit()`。

    三道闸：
      ① **同内容去重**（默认 10 分钟内只算一条）—— 防"手滑连点"与"同一句话反复发"；
         键＝正文 + 附件名（`_dup_key`），所以"只发附件不写字"的两条不会被当成重复；
      ② **分钟 / 小时 / 天三档条数**（默认 3 / 10 / 20）—— 防"一瞬间发 100 封"；
      ③ 被拒的**不落盘**，只写 `data/feedback_rejected.jsonl` 留痕（谁在刷要看得见）。
    """
    now = float(now if now is not None else time.time())
    lim = _limit()
    items = _read_all()
    win = int(lim.get("dup_window_s") or 0)
    if win > 0:
        n = _dup_key(text, names)
        if _norm(text) or names:
            for it in reversed(items):
                try:
                    if now - float(it.get("at") or 0) > win:
                        break
                    if _dup_key(it) == n:
                        return False, "这条反馈 %d 分钟内已经发过一次了" % (win // 60)
                except Exception:
                    continue
    for key, span, label in (("per_minute", 60, "分钟"), ("per_hour", 3600, "小时"), ("per_day", 86400, "天")):
        cap = int(lim.get(key) or 0)
        if cap <= 0:
            continue
        cnt = 0
        for it in items:
            try:
                if now - float(it.get("at") or 0) <= span:
                    cnt += 1
            except Exception:
                continue
        if cnt >= cap:
            return False, "发得太频繁了（每%s最多 %d 条，已经用满）——稍后再试" % (label, cap)
    return True, ""


def compose(item: dict) -> str:
    """邮件正文＝**用户原话，一字不改**。

    口径变化（用户 2026-09-15 原话）：「没必要让程序帮我整理，反正他只要用邮箱发到我的邮箱就行」
    ⇒ 不再"把诉求改写成人类可读正文"，只在最上面留一行元信息（类型/时间/版本/联系方式）方便定位；
    正文原样贴用户写的内容。末尾照旧附环境摘要——那是**排障用的机器信息**，不是替他改话。
    """
    head = "群相反馈 · %s · %s · v%s" % (item.get("kind") or "其他", item.get("at_h", ""), item.get("ver", "?"))
    if item.get("contact"):
        head += " · 联系邮箱：%s" % item["contact"]
    out = head + "\n\n" + str(item.get("text") or "").strip()
    _fs = [f for f in (item.get("files") or []) if isinstance(f, dict)]
    if _fs:
        # 附件清单一定进正文：**没配推送通道时，至少收信的人知道该找用户要哪几个文件**
        out += "\n\n--- 附件（%d 个）\n" % len(_fs) + "\n".join(
            "- %s（%s）" % (f.get("name"), human_size(f.get("size"))) for f in _fs)
    if item.get("env"):
        out += "\n\n--- 环境（自动附带，便于定位）\n" + json.dumps(item["env"], ensure_ascii=False)
    return out


def _post(url: str, payload: dict, timeout: int = 10) -> dict:
    """POST JSON 到用户自建中转；返回 {ok, status, why}（401/400 也算"送到了但对方拒收"，如实报）。"""
    import urllib.error
    import urllib.request
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")[:300]
            return {"ok": 200 <= r.status < 300, "status": r.status, "why": txt}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "why": (e.read()[:200].decode("utf-8", "replace"))}
    except Exception as e:
        return {"ok": False, "status": 0, "why": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _mail(item: dict, to_list: list, smtp_cfg: dict) -> dict:
    """SMTP 直发（465 SSL 优先；587 走 STARTTLS）。失败如实回原因。**带附件时发 multipart**。"""
    host = str(smtp_cfg.get("host") or "smtp.qq.com")
    port = int(smtp_cfg.get("port") or 465)
    user = str(smtp_cfg.get("user") or "")
    pwd = str(smtp_cfg.get("password") or "")
    if not (user and pwd and to_list):
        return {"ok": False, "why": "未配置发件邮箱或收件人"}
    _fs = [f for f in (item.get("files") or []) if isinstance(f, dict)]
    if _fs:
        msg = MIMEMultipart()
        msg.attach(MIMEText(compose(item), "plain", "utf-8"))
        _miss = []
        for f in _fs:
            try:
                with open(f.get("path") or "", "rb") as fh:
                    raw = fh.read()
            except Exception:
                _miss.append(str(f.get("name")))
                continue
            part = MIMEBase("application", "octet-stream")
            part.set_payload(raw)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=("utf-8", "", str(f.get("name") or "附件")))
            msg.attach(part)
        if _miss:
            msg.attach(MIMEText("（这几个附件在本机读不到了，没能带上：%s）" % "、".join(_miss), "plain", "utf-8"))
    else:
        msg = MIMEText(compose(item), "plain", "utf-8")
    msg["Subject"] = Header("[群相反馈] %s · %s" % (item.get("kind", "其他"), str(item.get("text", ""))[:24]), "utf-8")
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    try:
        if port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
                s.login(user, pwd)
                s.sendmail(user, to_list, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pwd)
                s.sendmail(user, to_list, msg.as_string())
        return {"ok": True, "why": "已发送至 %s" % "、".join(to_list)}
    except Exception as e:
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:140])}


def _dingtalk_sign(url: str, secret: str) -> str:
    """钉钉「加签」：`timestamp` + `HMAC-SHA256(secret)` 拼回地址（2026-09-16 补）。

    钉钉群机器人的安全设置里若选的是**加签**，请求必须带这两个参数；选**自定义关键词**则不用
    （我们的标题里固定带「群相反馈」四个字，所以关键词填它即可 —— 更省事，不用给密钥）。
    """
    import base64
    import hashlib
    import hmac
    from urllib.parse import quote_plus
    ts = str(int(time.time() * 1000))
    s = "%s\n%s" % (ts, secret)
    sign = quote_plus(base64.b64encode(
        hmac.new(str(secret).encode("utf-8"), s.encode("utf-8"), hashlib.sha256).digest()))
    sep = "&" if "?" in str(url) else "?"
    return "%s%stimestamp=%s&sign=%s" % (url, sep, ts, sign)


def _qy_key(url: str) -> str:
    """从企业微信 webhook 地址里取 `key=`（upload_media 要把它带上）。"""
    try:
        from urllib.parse import parse_qs, urlparse
        return (parse_qs(urlparse(str(url)).query).get("key") or [""])[0]
    except Exception:
        return ""


def _wecom_upload(url: str, key: str, name: str, raw: bytes, timeout: int = 30) -> dict:
    """企业微信 webhook 的 `upload_media`：multipart 上传换 `media_id`（官方口径 3 天有效）。"""
    import urllib.parse
    import urllib.request
    if not key:
        return {"ok": False, "why": "推送地址里没有 key"}
    u = urllib.parse.urlparse(str(url))
    up = "%s://%s/cgi-bin/webhook/upload_media?%s" % (
        u.scheme, u.netloc, urllib.parse.urlencode({"key": key, "type": "file"}))
    bound = "----pmfb" + uuid.uuid4().hex
    body = b"".join([
        ("--%s\r\n" % bound).encode(),
        ('Content-Disposition: form-data; name="media"; filename="%s"\r\n' % name).encode("utf-8"),
        b"Content-Type: application/octet-stream\r\n\r\n", raw, b"\r\n",
        ("--%s--\r\n" % bound).encode()])
    req = urllib.request.Request(up, data=body, headers={
        "Content-Type": "multipart/form-data; boundary=%s" % bound})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:100])}
    try:
        d = json.loads(txt)
    except Exception:
        return {"ok": False, "why": txt[:100]}
    mid = str(d.get("media_id") or "")
    if int(d.get("errcode") or 0) != 0 or not mid:
        return {"ok": False, "why": "上传被拒：%s" % txt[:100]}
    return {"ok": True, "media_id": mid}


def _wecom_media(url: str, files) -> dict:
    """把附件推给企业微信群机器人：**图片走 `base64 + md5`，其它文件先换 `media_id` 再发**。

    为什么分两条：企业微信的 webhook 只认这两种形态——图片消息直接吃 base64（≤2MB），
    文件必须先 `upload_media` 拿 media_id。**一条失败不影响别的**，逐条如实记账。
    """
    files = [f for f in (files or []) if isinstance(f, dict)]
    if not files:
        return {"ok": True, "sent": 0, "why": ""}
    key = _qy_key(url)
    sent, bad = 0, []
    for f in files:
        try:
            with open(f.get("path") or "", "rb") as fh:
                raw = fh.read()
        except Exception:
            bad.append("%s：本机读不到" % f.get("name"))
            continue
        if f.get("image") and len(raw) <= int(ATTACH["image_max"]):
            r = _post(url, {"msgtype": "image",
                            "image": {"base64": base64.b64encode(raw).decode("ascii"),
                                      "md5": hashlib.md5(raw).hexdigest()}}, timeout=30)
        else:
            up = _wecom_upload(url, key, str(f.get("name") or "附件"), raw)
            if not up.get("ok"):
                bad.append("%s：%s" % (f.get("name"), str(up.get("why"))[:60]))
                continue
            r = _post(url, {"msgtype": "file", "file": {"media_id": up.get("media_id")}}, timeout=30)
        low = str(r.get("why") or "").replace(" ", "").lower()
        if r.get("ok") and '"errcode":0' in low:
            sent += 1
        else:
            bad.append("%s：%s" % (f.get("name"), (low or "没有回执")[:60]))
    return {"ok": sent == len(files), "sent": sent,
            "why": ("附件 %d/%d 已推送" % (sent, len(files))) + (("；" + "；".join(bad[:2])) if bad else "")}


def _post_webhook(url: str, item: dict, token: str = "", timeout: int = 15) -> dict:
    """按 URL 自动选请求体，把反馈**推到作者自己的设备/群里**。

    为什么加它：以前只有"自建中转"和"自己邮箱+授权码"两条路，**两条都要用户自己配**，而收件凭据
    又不能进包（PII 闸门）⇒ 普通用户点了提交只能存在本机。群机器人这条**国内可达、URL 即凭据**。
    """
    u = str(url or "").lower()
    if not u:
        return {"ok": False, "why": "未配置推送地址"}
    txt = compose(item)
    title = "[群相反馈] %s · v%s" % (item.get("kind") or "其他", item.get("ver") or "?")
    if "pushplus" in u:
        if not token:
            return {"ok": False, "why": "PushPlus 需要 token（填 feedback.webhook_token）"}
        body = {"token": str(token).strip(), "title": title, "content": txt, "template": "txt"}
    elif "dingtalk" in u:
        body = {"msgtype": "text", "text": {"content": title + "\n" + txt}}
        if token:
            url = _dingtalk_sign(url, token)      # 安全设置选了「加签」时才需要
    elif "feishu" in u or "larksuite" in u:
        body = {"msg_type": "text", "content": {"text": title + "\n" + txt}}
    elif "qyapi.weixin" in u or "wecom" in u:
        body = {"msgtype": "text", "text": {"content": title + "\n" + txt}}
    else:
        body = {"title": title, "text": txt, "kind": item.get("kind"), "ver": item.get("ver")}
    r = _post(str(url), body, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "why": "推送失败：%s" % str(r.get("why"))[:80]}
    low = str(r.get("why") or "").replace(" ", "").lower()
    _known = ("dingtalk" in u or "qyapi" in u or "feishu" in u or "larksuite" in u or "pushplus" in u)
    if _known:
        # 这几家失败时也是 HTTP 200 ⇒ 必须看它们自己的返回码，别把"被拒"当成功
        _good = ('"errcode":0' in low) or ('"code":200' in low) or ('"code":0' in low) \
            or ('"success":true' in low)
        if not _good:
            return {"ok": False, "why": "推送被对方拒了：%s" % low[:80]}
    # 附件（2026-09-17 用户问「我们的反馈提交能不能提交图片和文件」⇒ 能）：企业微信群机器人这条
    # 支持图片与文件，签完字顺手把附件也推过去；其它通道（钉钉/飞书/PushPlus/自建）**没有文件形态**，
    # 正文里已经列了附件清单，这里不假装送过。
    _fs = item.get("files") or []
    if _fs and ("qyapi.weixin" in u or "wecom" in u):
        mm = _wecom_media(str(url), _fs)
        return {"ok": True, "why": "已推送到你的设备/群" + (("；" + str(mm.get("why"))) if mm.get("why") else "")}
    return {"ok": True, "why": "已推送到你的设备/群"}


def deliver(item: dict) -> dict:
    """按顺序试通道；返回 {ok, via, why}。都不成 ⇒ ok=False 且**不改** sent_at（等重发）。

    顺序：自建中转 → **「推送到你」（群机器人 / PushPlus / 任意中转，用户零配置）** → SMTP。
    """
    cfg = _cfg()
    url = str(cfg.get("upload_url") or "").strip()
    hook = str(cfg.get("webhook_url") or "").strip()
    hook_token = str(cfg.get("webhook_token") or "").strip()
    to_list = [x.strip() for x in str(cfg.get("to") or "").replace(";", ",").split(",") if x.strip()]
    smtp_cfg = cfg.get("smtp") or {}
    tried = []
    if url:
        r = _post(url, {"type": "feedback", "item": item, "text": compose(item)})
        tried.append("网址中转：" + ("OK %s" % r.get("status") if r.get("ok") else str(r.get("why"))[:60]))
        if r.get("ok"):
            return {"ok": True, "via": "upload_url", "why": "已提交到 %s" % url, "tried": tried}
    if hook:
        rh = _post_webhook(hook, item, hook_token)
        tried.append("推送到你：" + ("OK" if rh.get("ok") else str(rh.get("why"))[:60]))
        if rh.get("ok"):
            return {"ok": True, "via": "webhook", "why": rh.get("why"), "tried": tried}
    if to_list and (smtp_cfg.get("user") and smtp_cfg.get("password")):
        r = _mail(item, to_list, smtp_cfg)
        tried.append("邮件：" + ("OK" if r.get("ok") else str(r.get("why"))[:60]))
        if r.get("ok"):
            return {"ok": True, "via": "smtp", "why": r.get("why"), "tried": tried}
    if not url and not hook and not (to_list and smtp_cfg.get("user") and smtp_cfg.get("password")):
        tried.append("没有任何可用通道（未填中转网址 / 推送地址 / 发件邮箱+授权码）")
    return {"ok": False, "via": "", "why": "；".join(tried) or "没有可用通道", "tried": tried}


def submit(kind: str, text: str, contact: str = "", env: dict | None = None, files=None) -> dict:
    """收下一条反馈：落盘（含附件）→ 立刻试投递 → 如实回报三态。

    `files`＝`[{name, data(base64)}]`（前端读好的）；`contact`＝**选填的联系邮箱**，写错当场退回去。
    """
    t = str(text or "").strip()
    _files_in = [f for f in list(files or []) if isinstance(f, dict)]
    if not t and not _files_in:
        return {"ok": False, "state": "error", "why": "内容不能为空（写点字，或者带一张图/一个文件）"}
    if len(t) > 4000:
        return {"ok": False, "state": "error", "why": "太长了（≤4000 字）"}
    _em = str(contact or "").strip()
    if _em and not clean_email(_em):
        return {"ok": False, "state": "error",
                "why": "联系邮箱写得不太对（像这样：xxx@qq.com）；不想留就清空它"}
    k = str(kind or "其他").strip()
    if k not in KINDS:
        k = "其他"
    fid = uuid.uuid4().hex[:12]
    _saved, _notes = save_attachments(fid, _files_in)
    if _files_in and not _saved:
        # 一个都没存下 ⇒ 别假装收到；把原因原样告诉用户，让他换个文件再来
        return {"ok": False, "state": "error",
                "why": "附件没能收下：" + ("；".join(_notes[:2]) or "内容读不出来")}
    if not t:
        t = "（只带了附件，没写文字）"
    item = {
        "id": fid,
        "kind": k,
        "text": t,
        "contact": clean_email(_em),
        "at": time.time(),
        "at_h": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "ver": _version(),
        "env": env or {},
        "files": _saved,
    }
    if _notes:
        item["attach_notes"] = _notes
    # ④ 防刷闸门（咽喉点）：超额/重复一律**不发、不落盘**，只留痕（见 rate_check 注释）
    _ok_rl, _why_rl = rate_check(t, names=[f.get("name") for f in _saved])
    if not _ok_rl:
        _reject(_why_rl, item)
        return {"ok": False, "state": "blocked", "why": _why_rl,
                "note": "被限流的反馈不会发出去、也不会存本机；内容还在你手上，稍后再发即可"}
    if not _append(item):
        return {"ok": False, "state": "error", "why": "本地保存失败（data/ 可写？）"}
    prune_media()                      # 会写盘就得有上限，每收一条顺手收拾一次
    rep = deliver(item)
    _extra = ("；" + "；".join(_notes[:2])) if _notes else ""
    if rep.get("ok"):
        _mark_sent(item["id"], rep.get("via"))
        return {"ok": True, "state": "sent", "id": item["id"], "via": rep.get("via"),
                "files": len(_saved), "why": str(rep.get("why") or "") + _extra}
    st = stats()
    return {"ok": True, "state": "queued", "id": item["id"], "files": len(_saved),
            "why": str(rep.get("why") or "") + _extra,
            "pending": st["pending"], "note": "已存在本机，配好通道后可一键补发"}


def _mark_sent(item_id: str, via: str) -> bool:
    items = _read_all()
    for it in items:
        if it.get("id") == item_id:
            it["sent_at"] = time.time()
            it["sent_h"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            it["via"] = via
    return _write_all(items)


def pending() -> list:
    return [it for it in _read_all() if not it.get("sent_at")]


def stats() -> dict:
    items = _read_all()
    pend = [it for it in items if not it.get("sent_at")]
    sent = [it for it in items if it.get("sent_at")]
    cfg = _cfg()
    has_mail = bool((cfg.get("smtp") or {}).get("user") and (cfg.get("smtp") or {}).get("password")
                    and str(cfg.get("to") or "").strip())
    has_url = bool(str(cfg.get("upload_url") or "").strip())
    has_hook = bool(str(cfg.get("webhook_url") or "").strip())
    _ch = []
    if has_url:
        _ch.append("上传网址")
    if has_hook:
        _ch.append("推送到你")
    if has_mail:
        _ch.append("邮件")
    return {"total": len(items), "pending": len(pend), "sent": len(sent),
            "enabled": bool(cfg.get("enabled", True)),
            "channel": "+".join(_ch) or "未配置",
            "can_send": bool(has_url or has_hook or has_mail),
            "last": (sorted(items, key=lambda x: x.get("at") or 0)[-1:] or [{}])[0].get("at_h", "")}


def flush() -> dict:
    """把排队中的反馈逐条补发（幂等：只挑没有 sent_at 的）。"""
    ok_n, bad = 0, []
    for it in pending():
        rep = deliver(it)
        if rep.get("ok"):
            _mark_sent(it["id"], rep.get("via"))
            ok_n += 1
        else:
            bad.append(rep.get("why"))
    st = stats()
    return {"ok": True, "sent": ok_n, "left": st["pending"],
            "why": ("补发 %d 条，剩 %d 条待发" % (ok_n, st["pending"])) + (("；最近一次失败原因：" + str(bad[-1])[:120]) if bad else "")}
