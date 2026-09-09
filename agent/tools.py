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


def _image_parts(text, data_urls):
    parts = [{"type": "text", "text": text}]
    for url in data_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def build_tool_defs() -> list:
    return [
        {
            "name": "send_message",
            "description": "发送消息到当前会话。messages=字符串发一条；数组=分多条（更像真人，空格不是分句）。想引用对方最近一句就传 reply_to_message_id；点名某人传 at_user_id（wxid）。",
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
            "description": "往前翻当前会话更多历史消息。返回带 messageId（聊天记录里的 #数字），可用于引用或看图。",
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
            "description": "查看当前会话最近活跃的成员（wxid/名字/发言数），用于 @ 时找人。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10，最大 20"}},
            },
            "execute": _exec_get_members,
        },
        {
            "name": "get_message_detail",
            "description": "按消息 id（聊天记录里的 #数字）查看单条消息详情（完整文本、发送者、时间）。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "消息 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_get_detail,
        },
        {
            "name": "get_message_images",
            "description": "查看某条消息里的图片（能看懂图）。消息文本出现 [图片] 时用。message_idx=聊天记录里的 #数字。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "消息 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_get_images,
        },
        {
            "name": "send_image",
            "description": "把某条消息里的图片用鼠标操作转发/发到当前会话。messageId=带图消息的 #数字。适合「发一张图回应」。描述里写清你要发哪张图。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "带图消息的 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_send_image,
        },
        {
            "name": "collect_emoji",
            "description": "用鼠标把一条表情/图片消息收藏进微信表情库（右键气泡→添加到表情）。messageId=[表情] 或 [图片] 消息前的 #数字。最终由程序操作鼠标完成。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "[表情] 消息的 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_collect_emoji,
        },
        {
            "name": "list_emojis",
            "description": "查看微信/本地已收藏的表情（供发送时选择）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "execute": _exec_list_emojis,
        },
        {
            "name": "send_emoji",
            "description": "用鼠标发送一个已收藏的表情（程序点输入栏笑脸→爱心→点选表情→发送）。可选 nameOrId 指定表情（收藏时生成的概述/文件名）；不传则由模型按当前语境从已收藏概述里选最合适的。适合「发个表情回应」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_id": {"description": "可选：表情的概述/文件名（见 collect_emoji / list_emojis）"},
                    "context": {"description": "可选：当前语境一句话，帮模型从已收藏里挑最贴合的表情"}
                },
                "required": []
            },
            "execute": _exec_send_emoji,
        },
        {
            "name": "view_merge_forward",
            "description": "查看「合并转发聊天记录」的完整内容（程序解析子消息）。messageId=[合并转发] 前的 #数字。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "[合并转发] 消息的 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_view_merge,
        },
        {
            "name": "collect_message",
            "description": "用鼠标把某条消息收藏到微信收藏（右键→收藏）。messageId=聊天记录里的 #数字。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "要收藏的消息 id（#数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_collect_message,
        },
        {
            "name": "recall_message",
            "description": "用鼠标撤回自己最近发的一条消息（限 2 分钟内）。不传 messageId=撤回最近一条自己的。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "可选：要撤回的自己消息 id（#数字）；不填=最近的"}},
                "required": [],
            },
            "execute": _exec_recall_message,
        },
        {
            "name": "moments_like",
            "description": "用鼠标点赞朋友圈第 index 条（程序：点动态右下蓝点→「赞」；完成后自动关闭朋友圈窗口）。index 0 起。",
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "integer", "description": "点第几条的赞（0 起，默认 0=最新的）"}},
                "required": [],
            },
            "execute": _exec_moments_like,
        },
        {
            "name": "moments_comment",
            "description": "用鼠标评论朋友圈第 index 条（程序：点蓝点→「评论」→输入→发送；完成后自动关窗）。评论内容自己写，像真人的随口点评。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "评论第几条（0 起）"},
                    "text": {"description": "评论内容（4~60 字，随口点评，不要公告腔）"},
                },
                "required": ["text"],
            },
            "execute": _exec_moments_comment,
        },
        {
            "name": "moments_publish",
            "description": "用鼠标发一条纯文字朋友圈（程序：长按朋友圈左上角相机 2 秒→输入栏→输入→点发表→自动关窗）。text 像真人的日常随笔（8~120 字）。低频用。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"description": "朋友圈内容（8~120 字，日常画风，别像公告/广告）"}},
                "required": ["text"],
            },
            "execute": _exec_moments_publish,
        },
        {
            "name": "moments_surf",
            "description": "刷朋友圈：打开朋友圈→（可选滚动）→把当前视口截图给你看（图片在本工具返回里）。你看完再决定：赞（moments_like）/评论（moments_comment）/继续刷（再调一次）/结束。刷完程序自动关窗。",
            "parameters": {
                "type": "object",
                "properties": {"scroll": {"type": "integer", "description": "先滚几屏（0=不滚，默认 0）"}},
                "required": [],
            },
            "execute": _exec_moments_surf,
        },
        {
            "name": "send_poke",
            "description": "用鼠标拍一拍群成员（右键头像→拍一拍）。targetUserId=对方 wxid（用 get_active_members 查）。reason：reply=系统回拍时（通常系统已自动回，无需调）；request=群友明确要求拍；playful=偶尔皮一下（10%概率+每天3次，可能被拦）。失败如实说没拍上。",
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
            "description": "记一条对群友的长期印象（以后可见）。只记稳定信息：身份/关系、说话风格、梗、雷点、常聊话题。userId=对方 wxid；target=名字。",
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
            "description": "查看对群友的长期印象。不传 userId 返回全部。",
            "parameters": {
                "type": "object",
                "properties": {"user_id": {"description": "可选：只看这个 wxid 的印象"}},
            },
            "execute": _exec_memory_query,
        },
        {
            "name": "memory_remove",
            "description": "删除过时的群友印象。userId 按 wxid 删；target 按名字删；都不传=全删。",
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
            "description": "联网搜索（实时信息/新闻/梗/不确定的事实）。可换关键词搜 2~3 次；最相关的结果用 web_fetch 读正文。",
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
            "description": "结束本次处理（可选）。看完不打算说话时调用，summary 写一句不发言的理由；不调也可以，直接结束输出同样代表结束。",
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


def _exec_collect_emoji(ctx, args):
    """用鼠标收藏：右键表情气泡→「添加到表情」存入微信表情库（失败再本地截图兜底）。"""
    try:
        entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
        if not entry:
            return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
        media = [m for m in (entry.get("media") or [])
                 if m.get("local_id") and m.get("kind") in ("emoji", "image")]
        if not media:
            return _err("消息 %s 不是表情/图片（无法收藏）" % args.get("message_id"))
        # ① 鼠标真操作：右键气泡→添加到表情
        ok, msg = ctx["wechat"].collect_emoji_native(ctx["chat_id"], str(entry.get("text") or ""),
                                                      str(entry.get("sender_name") or ""))
        _text = str(entry.get("text") or "")
        _sender = str(entry.get("sender_name") or "")
        if ok:
            # 记入模型-程序协作表情库（概述+发送者语境；面板格序号由后续重扫描面板确定）
            try:
                from agent import emoji_lib as _el
                _el.record(_el.gen_summary(_text, _sender), -1, path="",
                           meta={"sender": _sender, "source": "native"})
            except Exception:
                pass
            return _ok({"collected": True, "note": "已用鼠标添加到微信表情库（右键→添加到表情）。"})
        # ② 本地兜底（截图/下载入收藏夹，send_emoji 面板可发）
        path = ctx["wechat"].collect_emoji(ctx["chat_id"], media[0]["local_id"])
        if not path:
            return _err("鼠标收藏失败（%s）；本地收藏也失败。" % msg)
        try:
            from agent import emoji_lib as _el
            _el.record(_el.gen_summary(_text, _sender), -1, path=path,
                       meta={"sender": _sender, "source": "local"})
        except Exception:
            pass
        return _ok({"collected": True, "path": path,
                    "note": "鼠标操作未成功（%s），已入本地收藏夹兜底。" % msg})
    except Exception as e:
        return _err(str(e))


def _exec_list_emojis(ctx, args):
    """列出收藏夹表情。"""
    try:
        emojis = ctx["wechat"].list_emojis()
        if not emojis:
            return _ok({"emojis": [], "note": "收藏夹为空：收到好玩的 [表情] 时可用 collect_emoji 收藏。"})
        return _ok({"emojis": emojis, "note": "用 send_emoji(name_or_id=文件名) 发送。"})
    except Exception as e:
        return _err(str(e))


def _exec_send_emoji(ctx, args):
    """发送收藏的表情：优先本地收藏夹直接发送（不受微信面板布局/用户预收藏顺序影响——
    面板格序号以微信收藏顺序计，用户先前收藏的表情会造成本地序号错位，故不再按格子序号点面板）。
    本地收藏夹没有匹配时，才用真实微信表情面板作兜底。"""
    import os as _os
    from agent import emoji_lib as _el
    name = str(args.get("name_or_id") or "").strip()
    emojis = ctx["wechat"].list_emojis()
    target = None
    if name:
        target = next((e for e in emojis if e["name"] == name
                       or e["path"].lower().endswith(name.lower())), None)
    idx = None
    if target is None:
        if name:
            for s in _el.list_summaries():
                if name in str(s["summary"]) or name in str(s.get("path") or ""):
                    idx = s["index"]
                    break
        if idx is None:
            idx = _el.pick(str(args.get("context") or ""))
        if idx is not None and idx >= 0:
            for s in _el.list_summaries():
                if s["index"] == idx and s.get("path") and _os.path.exists(s["path"]):
                    target = {"path": s["path"], "name": _os.path.basename(s["path"])}
                    break
        if target is None and idx is not None and idx >= 0 and idx < len(emojis):
            target = emojis[idx]
    if target is not None:
        ctx["sender"].send_image(ctx["chat_key"], target["path"])
        ctx["session"]["sent"].append({"type": "image", "text": "[表情]"})
        return _ok({"sent": True, "note": "已发送收藏表情（本地直发，不受微信面板布局/预收藏影响）。"})
    # 本地收藏夹无匹配 → 微信真实表情面板兜底（面板格序号仅对纯本地收藏序列有效）
    try:
        ok, msg = ctx["wechat"].emoji_panel_open()
        if not ok:
            return _err("没有匹配的收藏表情，面板打开也失败：%s（可先 collect_emoji 收藏后重试）" % msg)
        ok2, msg2 = ctx["wechat"].emoji_panel_send(idx if idx is not None and idx >= 0 else 0)
        if not ok2:
            return _err("表情面板发送失败：%s（已取消，未发送）" % msg2)
        ctx["session"]["sent"].append({"type": "image", "text": "[表情]"})
        return _ok({"sent": True, "index": idx, "note": msg2})
    except Exception as e:
        return _err(str(e))


def _exec_view_merge(ctx, args):
    """查看合并转发聊天记录内容。"""
    try:
        entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
        if not entry:
            return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
        merges = [m for m in (entry.get("media") or []) if m.get("kind") == "merge" and m.get("local_id")]
        if not merges:
            # 也允许直接看卡片类
            fc = ctx["wechat"].parse_forward_card(ctx["chat_id"], int(args.get("message_id") or 0))
            if fc.get("kind") == "merge":
                merges = [{"local_id": args.get("message_id")}]
            else:
                return _err("消息 %s 不是合并转发（可能是普通卡片/链接）" % args.get("message_id"))
        fc = ctx["wechat"].parse_forward_card(ctx["chat_id"], merges[0]["local_id"])
        if fc.get("kind") != "merge":
            return _err("合并转发内容解析失败")
        items = fc.get("items") or []
        lines = ["【合并转发聊天记录 · 共 %d 条】" % len(items)]
        for i, it in enumerate(items, 1):
            lines.append("%d. %s" % (i, it.get("title") or it.get("desc") or "（无标题）"))
        return _ok({"content": "\n".join(lines), "raw": fc.get("raw"), "note": "以上是合并转发里的消息。"})
    except Exception as e:
        return _err(str(e))


def _exec_collect_message(ctx, args):
    """收藏某条消息。"""
    try:
        entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
        if not entry:
            return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
        text = str(entry.get("text") or "")
        sender = str(entry.get("sender_name") or "")
        ok, msg = ctx["wechat"].collect_message(ctx["chat_id"], text, sender)
        return _ok({"collected": ok, "note": msg}) if ok else _err(msg)
    except Exception as e:
        return _err(str(e))


def _exec_recall_message(ctx, args):
    """撤回自己的消息（默认最近一条）。"""
    try:
        entry = None
        if args.get("message_id"):
            entry = ctx["store"].find_by_mid(ctx["chat_key"], args.get("message_id"))
            if not entry:
                return _err("当前会话找不到消息 %s。%s" % (args.get("message_id"), _mid_hint(ctx)))
        else:
            msgs = ctx["store"].recent(ctx["chat_key"], limit=20) or []
            for m in reversed(msgs):
                if m.get("self") and str(m.get("text") or "").strip():
                    entry = m
                    break
        if not entry:
            return _err("没找到可撤回的自己消息")
        ok, msg = ctx["wechat"].recall_message(ctx["chat_id"], str(entry.get("text") or ""),
                                               str(entry.get("sender_name") or ""))
        return _ok({"recalled": ok, "note": msg}) if ok else _err(msg)
    except Exception as e:
        return _err(str(e))


def _exec_moments_like(ctx, args):
    """点赞朋友圈（程序鼠标；低频由行为引擎把关）。"""
    try:
        from agent.behavior import decider
        if not decider.should("like_moments"):
            return _err("点赞被决策引擎拦截（概率/上限未达，或未启用；可在控制台调试区开启）")
        index = int(args.get("index") or 0)
        ok, msg = ctx["wechat"].moments_like(index)
        return _ok({"liked": ok, "note": msg}) if ok else _err(msg)
    except Exception as e:
        return _err(str(e))


def _exec_moments_comment(ctx, args):
    """评论朋友圈（程序鼠标）。"""
    try:
        from agent.behavior import decider
        if not decider.should("moments_comment"):
            return _err("评论被决策引擎拦截（概率/上限未达，或未启用；可在控制台调试区开启）")
        index = int(args.get("index") or 0)
        text = str(args.get("text") or "").strip()
        if len(text) < 4:
            return _err("评论至少 4 个字")
        ok, msg = ctx["wechat"].moments_comment(index, text)
        return _ok({"commented": ok, "note": msg}) if ok else _err(msg)
    except Exception as e:
        return _err(str(e))


def _exec_moments_surf(ctx, args):
    """刷朋友圈：滚动 + 截图给模型看（一次视口）。"""
    try:
        from agent.behavior import decider
        if not decider.should("moments_surf"):
            return _err("刷朋友圈被决策引擎拦截（频率/上限未达，或未启用；可在控制台调试区开启）")
        scroll = int(args.get("scroll") or 0)
        ok, msg = ctx["wechat"].moments_open()
        if not ok:
            return _err("朋友圈打开失败：%s" % msg)
        if scroll > 0:
            ctx["wechat"].moments_scroll(1, scroll)
            time.sleep(1.0)
        parts = ctx["wechat"].moments_screenshot()
        if not parts:
            ctx["wechat"].moments_close()
            return _err("朋友圈截图失败（窗口可能未加载）；窗口已关")
        return {"content": _image_parts("朋友圈当前视口：", parts)}
    except Exception as e:
        return _err(str(e))


def _ok_text(parts):
    return "（截图见下方图片）"


def _exec_moments_publish(ctx, args):
    """发纯文字朋友圈（程序鼠标：长按相机→输入→发表→关窗）。"""
    try:
        from agent.behavior import decider
        if not decider.should("moments_publish"):
            return _err("发朋友圈被决策引擎拦截（概率/上限未达，或未启用；可在控制台调试区开启）")
        text = str(args.get("text") or "").strip()
        if len(text) < 8:
            return _err("朋友圈内容至少 8 个字")
        if len(text) > 120:
            return _err("朋友圈内容别超过 120 字")
        ok, msg = ctx["wechat"].moments_publish_text(text)
        return _ok({"published": ok, "note": msg}) if ok else _err(msg)
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
