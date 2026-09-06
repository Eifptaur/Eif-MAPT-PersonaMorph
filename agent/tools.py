# -*- coding: utf-8 -*-
"""原生工具集（OpenAI function calling 格式，移植自 qq-agent src/tools.js，适配微信）。

关键区别（相对原版 MCP 工具）：每个工具自动绑定本次运行对应的会话（chatKey），
模型物理上无法把消息发到别的群，安全性更强。QQ 专属工具（表情包/拍一拍/合并转发）已移除，
替换为微信可用的 @ 与图片查看能力。
"""
from __future__ import annotations

import json

from .config import get_config
from .util import normalize_message_list, unquote_json_string
from .web_search import web_search, web_fetch


def _ok(payload):
    return {"content": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)}


def _err(message):
    return {"content": "错误：%s" % message, "is_error": True}


def _mid_hint(ctx) -> str:
    mids = []
    for m in ctx["store"].recent(ctx["chat_key"], limit=60):
        if m.get("mid") not in (None, ""):
            mids.append(str(m["mid"]))
    uniq = list(dict.fromkeys(mids))[-8:]
    return ("消息 id 只能用聊天记录里每条消息前的 #数字（最近可见：%s），不要自己编" % " ".join(uniq)) if uniq \
        else "聊天记录里还没有带 #id 的消息"


def _member_hint(ctx) -> str:
    members = ctx["store"].active_members(ctx["chat_key"], 8)
    if not members:
        return "当前没有可用的成员列表，请先等有群友发言后再试"
    lines = ["- %s：%s" % (m["name"], m["user_id"]) for m in members]
    return "请从当前会话成员里选一个 wxid 填进去：\n%s" % "\n".join(lines)


def _image_parts(text, data_urls):
    parts = [{"type": "text", "text": text}]
    for url in data_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def build_tool_defs() -> list:
    return [
        {
            "name": "send_message",
            "description": "发送消息到当前聊天（本工具只能发到本次会话对应的群/私聊）。messages 传字符串=发一条；传字符串数组=分多条发送（推荐，更像真人）。需要\"引用对方刚说的话再回\"时才传 reply_to_message_id（引用的是最近一条消息）；需要点名某人才传 at_user_id（wxid）。不要在字符串内部用空格分句。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messages": {"description": "要发送的内容：字符串=一条；数组=分多条",
                                 "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "reply_to_message_id": {"description": "要引用/回复的消息 id（#数字，可选；实际引用的是最近一条消息）"},
                    "at_user_id": {"description": "要 @ 的群成员 wxid（可选，与引用二选一，不要滥用）"},
                },
                "required": ["messages"],
            },
            "execute": _exec_send_message,
        },
        {
            "name": "get_recent_messages",
            "description": "往前翻当前会话的更多历史消息（提示词里只带了最近一段；需要更早的上下文时用）。返回带 messageId（就是聊天记录里的 #数字），可用于引用或看图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "最多返回条数，默认 30，最大 100"},
                    "offset": {"type": "integer", "description": "跳过最近 N 条，用于翻更早的消息"},
                },
            },
            "execute": _exec_get_recent,
        },
        {
            "name": "get_active_members",
            "description": "查看当前会话最近活跃的成员（wxid、名字、最近发言时间、发言数），用于 @ 时找人。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10，最大 20"}},
            },
            "execute": _exec_get_members,
        },
        {
            "name": "get_message_detail",
            "description": "按消息 id 查看单条消息详情（完整文本、发送者、时间）。id 用聊天记录里每条消息前的 #数字，不要自己编。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "消息 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_get_detail,
        },
        {
            "name": "get_message_images",
            "description": "查看某条消息里的图片（视觉模型可以直接看懂）。消息文本出现 [图片] 时可用。id 用聊天记录里每条消息前的 #数字。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "消息 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_get_images,
        },
        {
            "name": "send_image",
            "description": "把某条消息里的图片转发/发送到当前聊天。messageId 填那条带图消息前的 #数字。适合「发一张图回应」「把这张图发出去」等场景。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "带图消息的 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_send_image,
        },
        {
            "name": "send_poke",
            "description": "拍一拍群里的某位成员（右键对方头像 → 菜单选「拍一拍」）。targetUserId 填对方 wxid（不知道就先调 get_active_members 查）。reason 三选一：reply=回拍（对方刚拍了你，通常系统会自动回拍，无需重复调用）；request=群友明确要求拍某人（不设概率门）；playful=偶尔皮一下（受 10% 概率 + 每天 3 次限制，可能被拦）。实验性：靠屏幕 OCR 定位头像和菜单，可能失败。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_user_id": {"description": "要拍的群友 wxid"},
                    "reason": {"type": "string", "enum": ["reply", "request", "playful"],
                               "description": "拍一拍原因：reply/request/playful，默认 playful"}
                },
                "required": ["target_user_id"],
            },
            "execute": _exec_send_poke,
        },
        {
            "name": "memory_append",
            "description": "记一条对群友的长期印象（下次运行会自动看到）。只记\"以后和这个人打交道时用得上\"的稳定印象：身份/关系、说话风格、爱玩的梗、雷点、常聊话题。太临时的事情不要记。userId 填对方 wxid（不知道就先调 get_active_members / get_recent_messages 查）；target 填备注名/群名片/昵称。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["memberImpression"]},
                    "user_id": {"description": "对方 wxid"},
                    "target": {"type": "string", "description": "对方名字（备注名/群名片/昵称）"},
                    "content": {"type": "string", "description": "印象内容（≤120字，稳定、可跨多次聊天使用）"},
                },
                "required": ["category", "user_id", "content"],
            },
            "execute": _exec_memory_append,
        },
        {
            "name": "memory_query",
            "description": "查看当前会话里你对群友的长期印象。不传 userId 返回全部；传 userId 只看某一个人。",
            "parameters": {
                "type": "object",
                "properties": {"user_id": {"description": "可选：只看这个 wxid 的印象"}},
            },
            "execute": _exec_memory_query,
        },
        {
            "name": "memory_remove",
            "description": "删除一条过时/不再准确的对群友印象。userId 优先按 wxid 删；target 按名字删；两者都不传则删全部印象。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["memberImpression"]},
                    "user_id": {"description": "对方 wxid（优先）"},
                    "target": {"type": "string", "description": "对方名字（没有 wxid 时用）"},
                    "content": {"type": "string", "description": "可选：只删这条内容"},
                },
                "required": ["category"],
            },
            "execute": _exec_memory_remove,
        },
        {
            "name": "web_search",
            "description": "联网搜索，返回标题/URL/摘要列表。适用：实时信息、新闻热点、网络用语/梗的含义、自己不确定的事实。可以换关键词连续搜 2~3 次；对最相关的 1~2 个结果用 web_fetch 读正文，不要只看摘要。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索词"}},
                "required": ["query"],
            },
            "execute": _exec_web_search,
        },
        {
            "name": "web_fetch",
            "description": "只读抓取网页正文（≤2 万字符）。群友发来链接问\"写了什么\"时直接抓；配合 web_search 阅读搜索结果的详细内容。禁止访问内网/本机地址。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "要抓取的 http(s) URL"}},
                "required": ["url"],
            },
            "execute": _exec_web_fetch,
        },
        {
            "name": "report_feedback",
            "description": "向管理员（控制台）反馈你遇到的问题、困惑或需要人工介入的情况。不要用于聊天。",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["info", "warning", "error"]},
                    "message": {"type": "string"},
                },
                "required": ["message"],
            },
            "execute": _exec_report,
        },
        {
            "name": "finish",
            "description": "明确结束本次处理（表示你看完了、决定了下一步）。看完不打算说话时调用它（summary 写一句给自己看的理由）；说完话想收尾时也可以调用。不调用也可以——直接结束文本输出同样代表结束。",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "一句话说明你这次的决定（只记录给管理端看，不会发送）"}},
                "required": ["summary"],
            },
            "execute": _exec_finish,
        },
    ]


# ── 各工具执行 ───────────────────────────────────────────────────────────

def _exec_send_message(ctx, args):
    try:
        messages = normalize_message_list(args.get("messages"))
        if not messages:
            return _err("消息内容为空")
        # 引用时把被引用消息的原文 + 发送者名字带给发送层（用于定位头像/气泡，即便已滚出可视区）
        reply_text = ""
        reply_sender_name = ""
        rmid = args.get("reply_to_message_id")
        if rmid:
            entry = ctx["store"].find_by_mid(ctx["chat_key"], rmid)
            if entry:
                reply_text = str(entry.get("text") or "")
                reply_sender_name = str(entry.get("sender_name") or "")
        result = ctx["sender"].send_text_batch(
            ctx["chat_key"], messages,
            reply_to_mid=rmid,
            at_user_id=args.get("at_user_id"),
            reply_text=reply_text,
            reply_sender_name=reply_sender_name,
        )
        ctx["session"]["sent"].extend([{"type": "text", "text": s["text"], "at": s.get("at")} for s in result["sent"]])
        note = "已发送。不要输出\"已发送\"类汇报，继续思考下一步或直接结束。"
        if result["failed"]:
            note += "（另有 %d 条发送失败，成功的不需要重发，失败的请稍后再试或减少条数）" % len(result["failed"])
        return _ok({"sent": len(result["sent"]), "note": note})
    except Exception as e:
        return _err(str(e))


def _exec_get_recent(ctx, args):
    limit = min(100, max(1, int(args.get("limit") or 30)))
    offset = max(0, int(args.get("offset") or 0))
    past_count = ctx["session"].get("past_state_count") or 0
    messages = ctx["store"].recent(ctx["chat_key"], limit=limit, offset=offset + past_count)
    import time as _t
    return _ok({
        "count": len(messages),
        "messages": [{
            "messageId": m.get("mid"),
            "time": _t.strftime("%m-%d %H:%M", _t.localtime((m.get("ts") or 0) / 1000.0)),
            "sender": "我" if m.get("self") else m.get("sender_name"),
            "text": m.get("text"),
        } for m in messages],
    })


def _exec_get_members(ctx, args):
    members = ctx["store"].active_members(ctx["chat_key"], min(20, max(1, int(args.get("limit") or 10))))
    import time as _t
    return _ok({
        "members": [{
            "userId": m["user_id"],
            "name": m["name"],
            "lastSeen": _t.strftime("%m-%d %H:%M", _t.localtime((m["last_ts"] or 0) / 1000.0)),
            "recentCount": m["count"],
        } for m in members],
    })


def _exec_get_detail(ctx, args):
    entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
    if not entry:
        return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
    import time as _t
    return _ok({
        "messageId": entry.get("mid"),
        "time": _t.strftime("%m-%d %H:%M:%S", _t.localtime((entry.get("ts") or 0) / 1000.0)),
        "sender": "我" if entry.get("self") else entry.get("sender_name"),
        "senderId": entry.get("sender_id"),
        "text": entry.get("text"),
        "reply": entry.get("reply"),
    })


def _exec_get_images(ctx, args):
    try:
        entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
        if not entry:
            return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
        images = [m for m in (entry.get("media") or []) if m.get("kind") == "image" and m.get("local_id")]
        if not images:
            return _ok("消息 %s 没有可查看的图片" % args.get("message_id"))
        data_urls = []
        failed = []
        for img in images:
            path = ctx["wechat"].download_image(ctx["chat_id"], img["local_id"])
            if not path:
                failed.append("下载失败")
                continue
            b64 = ctx["wechat"].image_to_base64(path)
            if b64:
                data_urls.append(b64)
            else:
                failed.append("编码失败")
        if not data_urls:
            return _err("图片获取失败：%s" % "；".join(failed))
        note = ("（另有 %d 张获取失败）" % len(failed)) if failed else ""
        return {"content": _image_parts("消息 %s 的图片内容%s：" % (args.get("message_id"), note), data_urls)}
    except Exception as e:
        return _err(str(e))


def _exec_send_image(ctx, args):
    """转发/发送某条消息里的图片到当前聊天。"""
    try:
        entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
        if not entry:
            return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
        images = [m for m in (entry.get("media") or []) if m.get("kind") == "image" and m.get("local_id")]
        if not images:
            return _err("消息 %s 没有可发送的图片" % args.get("message_id"))
        path = ctx["wechat"].download_image(ctx["chat_id"], images[0]["local_id"])
        if not path:
            return _err("图片下载失败（可能本地缓存已清理，让对方在微信里点开这张图再试）")
        ctx["sender"].send_image(ctx["chat_key"], path)
        ctx["session"]["sent"].append({"type": "image", "text": "[图片]"})
        return _ok({"sent": True, "note": "图片已发送。不要输出\"已发送\"类汇报。"})
    except Exception as e:
        return _err(str(e))


def _exec_send_poke(ctx, args):
    """拍一拍某位群友：按 reason 走不同门控（回拍 90% / 要求直拍 / 皮一下 10%+每天3次）。"""
    user_id = str(args.get("target_user_id") or "").strip()
    if not user_id:
        return _err("target_user_id 不能为空")
    reason = str(args.get("reason") or "playful").strip().lower()
    if reason not in ("reply", "request", "playful"):
        reason = "playful"
    name = ctx["wechat"].member_name(ctx["chat_id"], user_id)
    if not name or name == user_id:
        for m in ctx["store"].active_members(ctx["chat_key"], 20):
            if m["user_id"] == user_id and m.get("name"):
                name = m["name"]
                break
    chat_id = ctx["chat_id"]
    if reason == "reply":
        ok_flag, msg = ctx["wechat"].try_send_poke_back(chat_id, name or user_id, user_id)
    elif reason == "request":
        ok_flag, msg = ctx["wechat"].send_poke(chat_id, name or user_id, user_id)
    else:
        ok_flag, msg = ctx["wechat"].try_send_poke_active(chat_id, name or user_id, user_id)
    if ok_flag:
        ctx["session"]["sent"].append({"type": "poke", "text": "[拍一拍]"})
        return _ok({"poked": True, "note": "已拍。不要输出\"已拍\"类汇报。"})
    return _err("没拍：%s" % msg)


def _exec_memory_append(ctx, args):
    user_id = str(args.get("user_id") or "").strip()
    if not user_id or len(user_id) > 64 or " " in user_id:
        return _err("userId 必须是有效的 wxid（收到：%s）。先用 get_active_members 查准确 wxid 再记。" % (args.get("user_id")))
    entry = ctx["memory"].append(ctx["chat_key"], "memberImpression", str(args.get("content") or ""),
                                 {"userId": user_id, "target": str(args.get("target") or "").strip()})
    return _ok({"saved": True, "entry": entry})


def _exec_memory_query(ctx, args):
    mem = ctx["memory"].query(ctx["chat_key"])
    user_id = str(args.get("user_id") or "").strip()
    lst = [e for e in mem["memberImpression"] if str(e.get("userId")) == user_id] if user_id else mem["memberImpression"]
    return _ok({"memberImpression": lst})


def _exec_memory_remove(ctx, args):
    removed = ctx["memory"].remove(ctx["chat_key"], "memberImpression",
                                   user_id=str(args.get("user_id") or "").strip(),
                                   target=str(args.get("target") or "").strip(),
                                   content=str(args.get("content") or "").strip())
    return _ok({"removed": removed})


def _exec_web_search(ctx, args):
    try:
        result = web_search(str(args.get("query") or ""))
        if not result["results"]:
            return _ok({"query": result["query"], "results": [], "note": "没有搜到结果，试试换关键词或更具体的说法。"})
        return _ok(result)
    except Exception as e:
        return _err("搜索失败：%s" % e)


def _exec_web_fetch(ctx, args):
    try:
        result = web_fetch(str(args.get("url") or ""))
        body = str(result.get("body") or "")
        return _ok({
            "url": result.get("url"),
            "statusCode": result.get("status_code"),
            "truncated": result.get("truncated") or len(body) > 20000,
            "content": body[:20000],
        })
    except Exception as e:
        return _err("抓取失败：%s" % e)


def _exec_report(ctx, args):
    level = args.get("level") if args.get("level") in ("info", "warning", "error") else "info"
    msg = str(args.get("message") or "")[:500]
    ctx["session"].setdefault("feedbacks", []).append({"level": level, "message": msg})
    ctx["emit"]("feedback", {"chat_key": ctx["chat_key"], "level": level, "message": msg})
    return _ok({"reported": True})


def _exec_finish(ctx, args):
    ctx["session"]["finish_reason"] = str(args.get("summary") or "")[:300]
    return _ok({"finished": True})


# ── OpenAI tools 格式转换 / 执行 ──────────────────────────────────────────

def to_openai_tools(defs: list) -> list:
    return [{"type": "function", "function": {"name": d["name"], "description": d["description"],
                                              "parameters": d["parameters"]}} for d in defs]


def execute_tool(defs: list, ctx, name: str, args_json: str):
    """找到并执行一个工具。返回 {content, is_error}。"""
    name = str(name or "").strip()
    def_obj = None
    for d in defs:
        if d["name"] == name or name.endswith(d["name"]):
            def_obj = d
            break
    if not def_obj:
        return {"content": "错误：未知工具 %s" % name, "is_error": True}
    try:
        args = json.loads(args_json or "{}")
    except Exception:
        return {"content": "错误：工具 %s 的参数不是合法 JSON：%s" % (name, str(args_json)[:200]), "is_error": True}
    if not isinstance(args, dict):
        args = {}
    try:
        return def_obj["execute"](ctx, args)
    except Exception as e:
        return {"content": "错误：%s" % e, "is_error": True}
