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

import json
import os
import smtplib
import ssl
import time
import uuid
from email.header import Header
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_FILE = os.path.join(ROOT, "data", "feedback.jsonl")

#: 反馈类型（前端下拉与后端校验共用同一份，避免"界面能填、后端不认"）
KINDS = ("问题", "建议", "想法", "其他")


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


def compose(item: dict) -> str:
    """邮件正文＝**用户原话，一字不改**。

    口径变化（用户 2026-09-15 原话）：「没必要让程序帮我整理，反正他只要用邮箱发到我的邮箱就行」
    ⇒ 不再"把诉求改写成人类可读正文"，只在最上面留一行元信息（类型/时间/版本/联系方式）方便定位；
    正文原样贴用户写的内容。末尾照旧附环境摘要——那是**排障用的机器信息**，不是替他改话。
    """
    head = "群相反馈 · %s · %s · v%s" % (item.get("kind") or "其他", item.get("at_h", ""), item.get("ver", "?"))
    if item.get("contact"):
        head += " · 联系方式：%s" % item["contact"]
    out = head + "\n\n" + str(item.get("text") or "").strip()
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
    """SMTP 直发（465 SSL 优先；587 走 STARTTLS）。失败如实回原因。"""
    host = str(smtp_cfg.get("host") or "smtp.qq.com")
    port = int(smtp_cfg.get("port") or 465)
    user = str(smtp_cfg.get("user") or "")
    pwd = str(smtp_cfg.get("password") or "")
    if not (user and pwd and to_list):
        return {"ok": False, "why": "未配置发件邮箱或收件人"}
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


def deliver(item: dict) -> dict:
    """按顺序试通道；返回 {ok, via, why}。都不成 ⇒ ok=False 且**不改** sent_at（等重发）。"""
    cfg = _cfg()
    url = str(cfg.get("upload_url") or "").strip()
    to_list = [x.strip() for x in str(cfg.get("to") or "").replace(";", ",").split(",") if x.strip()]
    smtp_cfg = cfg.get("smtp") or {}
    tried = []
    if url:
        r = _post(url, {"type": "feedback", "item": item, "text": compose(item)})
        tried.append("网址中转：" + ("OK %s" % r.get("status") if r.get("ok") else str(r.get("why"))[:60]))
        if r.get("ok"):
            return {"ok": True, "via": "upload_url", "why": "已提交到 %s" % url, "tried": tried}
    if to_list and (smtp_cfg.get("user") and smtp_cfg.get("password")):
        r = _mail(item, to_list, smtp_cfg)
        tried.append("邮件：" + ("OK" if r.get("ok") else str(r.get("why"))[:60]))
        if r.get("ok"):
            return {"ok": True, "via": "smtp", "why": r.get("why"), "tried": tried}
    if not url and not (to_list and smtp_cfg.get("user") and smtp_cfg.get("password")):
        tried.append("没有任何可用通道（未填上传网址，也未配发件邮箱+授权码）")
    return {"ok": False, "via": "", "why": "；".join(tried) or "没有可用通道", "tried": tried}


def submit(kind: str, text: str, contact: str = "", env: dict | None = None) -> dict:
    """收下一条反馈：落盘 → 立刻试投递 → 如实回报三态。"""
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "state": "error", "why": "内容不能为空"}
    if len(t) > 4000:
        return {"ok": False, "state": "error", "why": "太长了（≤4000 字）"}
    k = str(kind or "其他").strip()
    if k not in KINDS:
        k = "其他"
    item = {
        "id": uuid.uuid4().hex[:12],
        "kind": k,
        "text": t,
        "contact": str(contact or "").strip()[:120],
        "at": time.time(),
        "at_h": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "ver": _version(),
        "env": env or {},
    }
    if not _append(item):
        return {"ok": False, "state": "error", "why": "本地保存失败（data/ 可写？）"}
    rep = deliver(item)
    if rep.get("ok"):
        _mark_sent(item["id"], rep.get("via"))
        return {"ok": True, "state": "sent", "id": item["id"], "via": rep.get("via"), "why": rep.get("why")}
    st = stats()
    return {"ok": True, "state": "queued", "id": item["id"], "why": rep.get("why"),
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
    return {"total": len(items), "pending": len(pend), "sent": len(sent),
            "enabled": bool(cfg.get("enabled", True)),
            "channel": ("上传网址" if has_url else "") + ("+邮件" if has_mail else "") or "未配置",
            "can_send": bool(has_url or has_mail),
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
