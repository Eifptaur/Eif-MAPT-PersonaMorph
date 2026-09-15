# -*- coding: utf-8 -*-
"""原生工具集（OpenAI function calling 格式，移植自 qq-agent src/tools.js，适配微信）。

关键区别（相对原版 MCP 工具）：每个工具自动绑定本次运行对应的会话（chatKey），
模型物理上无法把消息发到别的群，安全性更强。QQ 专属工具（表情包/拍一拍/合并转发）已移除，
替换为微信可用的 @ 与图片查看能力。
"""
from __future__ import annotations

import json
import os as _os
import time

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
    defs = _builtin_tool_defs()
    # 用户自定义工具（声明式 HTTP）：总开关关着就一个都不加；**重名在加载时已被拒**
    try:
        from . import user_tools as _ut
        extra, _problems, _all = _ut.as_tool_defs(builtin_names=[d["name"] for d in defs])
        for d in extra:
            if d["name"] not in {x["name"] for x in defs}:
                defs.append(d)
    except Exception:
        pass
    return defs


def _builtin_tool_defs() -> list:
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
            "name": "send_random_image",
            "description": "随机发一张图（从机器人自己的图库随机挑，不用指定哪张）。想「随机来张图」活跃气氛时用；图库为空或功能没开时它会返回原因，照原因说明即可。",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "execute": _exec_send_random_image,
        },
        {
            "name": "transcribe_voice",
            "description": "把某条语音消息转成文字（本机离线识别，音频不出网、不上传）。message_id=[语音] 消息前的 #数字。没有可用识别引擎时它会返回原因，照原因说明即可，**绝不要猜语音内容**。",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"description": "语音消息的 id（聊天记录里的 #数字）"}},
                "required": ["message_id"],
            },
            "execute": _exec_transcribe_voice,
        },
        {
            "name": "download_media",
            "description": "把某条消息里的视频或文件下载到本机（只下载、不发送）。message_id=[视频] 或 [文件/链接/卡片] 消息前的 #数字；kind=video 或 file。返回本地路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"description": "消息 id（聊天记录里的 #数字）"},
                    "kind": {"description": "video 或 file"},
                },
                "required": ["message_id", "kind"],
            },
            "execute": _exec_download_media,
        },
        {
            "name": "forward_media",
            "description": "把某条消息里的视频/文件转发到当前会话。⚠️ 这一步要用系统「选择文件」对话框，**会短暂抢一次前台**，所以默认关闭；关闭时它返回原因，照原因告诉用户即可。**链接不需要它**——直接用 send_message 把链接发出去是纯后台的。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"description": "消息 id（聊天记录里的 #数字）"},
                    "kind": {"description": "video 或 file"},
                },
                "required": ["message_id", "kind"],
            },
            "execute": _exec_forward_media,
        },
        {
            "name": "send_voice_reply",
            "description": "把一句话合成成音频发到当前会话（本机 TTS，零下载；**发出去的是音频文件，不是微信语音条**）。适合「用语音回一句」。功能没开 / 文本太长 / 没合成引擎 / 发失败时它会返回原因，照原因如实说明即可。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"description": "要说的话（建议 ≤120 字，别带表情符号堆叠）"}},
                "required": ["text"],
            },
            "execute": _exec_send_voice_reply,
        },
        {
            "name": "send_image_search",
            "description": "**按关键词去找一张图并发到当前会话**（在线图源 + 同一套过滤链）。有人明确说「找张猫的图/发个风景图/来张赛博朋克」时用。关键词要具体（如 猫、风景、赛博朋克、星空）。功能没开、没通过过滤、图源取不到时会返回原因，照原因说明即可，不要假装发过图。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"description": "要找什么图（一句短词，中文或英文都可以）"}},
                "required": ["keyword"],
            },
            "execute": _exec_send_image_search,
        },
        {
            "name": "gen_image",
            "description": "**按群友的要求把图生成出来**（走生图链条：意图解析 → 挑后端 → 生成 → 过滤链）。群友说「画一张/生成一张/来张 xx 的图」且现有图源里没有合适的时候用。**没配生图后端、没过过滤链、或请求碰红线（真人换脸/成人内容）时它会返回原因**，照原因如实说即可，**绝不许假装生成过**。",
            "parameters": {
                "type": "object",
                "properties": {"request": {"description": "群友的原话要求（主体/风格/张数/尺寸），别自己加戏"}},
                "required": ["request"],
            },
            "execute": _exec_gen_image,
        },
        {
            "name": "find_local_file",
            "description": "**在本机用户配好的目录里找文件**（只读，不发送）。有人问「有没有 XX 文件 / 帮我找一下那个报告」时用；返回候选列表（路径/大小/时间）。没开功能或没配目录时它会返回原因，照实说，**不要编造文件**。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"description": "要搜的文件名（可以只写一部分，比如「周报」「.pdf」）"}},
                "required": ["name"],
            },
            "execute": _exec_find_local_file,
        },
        {
            "name": "send_local_file",
            "description": "把本机**已配目录里**的某个文件发到当前会话（会过一次系统「选择文件」对话框、**短暂抢一次前台**，所以默认关）。有人明确说「把 XX 文件发我」且 find_local_file 已定位到唯一文件时用；功能没开、文件不在允许目录、重名多个、超大小上限时它都会返回原因。",
            "parameters": {
                "type": "object",
                "properties": {"name_or_path": {"description": "文件名（唯一命中）或完整路径（必须在允许目录内）"}},
                "required": ["name_or_path"],
            },
            "execute": _exec_send_local_file,
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
            "name": "read_bilibili",
            "description": ("解析 B 站视频（BV 号 / av 号 / b23.tv 短链 / 视频页地址）：给出标题、UP、时长、"
                            "简介、分P 与字幕。群友丢 B 站链接问「这视频讲什么」时用它，**不要凭链接瞎猜内容**；"
                            "拿不到（视频没了/没字幕/网络不通）会如实说原因。只读公开信息，不需要登录。"),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "B 站链接或 BV 号（整句话丢进来也行，会自动找）"}},
                "required": ["url"],
            },
            "execute": _exec_read_bilibili,
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
            "name": "set_timer",
            "description": ("设一个定时提醒：到点后由你在**当前会话**发一条提醒（例如群友说「5 分钟后提醒我喝水」）。"
                            "只对当前这个会话生效，不能一次给多个会话设提醒；每条会话最多挂 3 条、全局最多 20 条；"
                            "时间范围 30 秒 ~ 7 天。设完把「几点提醒什么」告诉对方（别假装已经等到了）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "到点要提醒的内容（写清提醒谁、提醒什么）"},
                    "seconds": {"type": "integer", "description": "多少秒后提醒（与 minutes 二选一）"},
                    "minutes": {"type": "integer", "description": "多少分钟后提醒（与 seconds 二选一）"},
                },
                "required": ["note"],
            },
            "execute": _exec_set_timer,
        },
        {
            "name": "list_timers",
            "description": "看看当前会话还有哪些定时提醒没到点（返回编号与剩余秒数）。",
            "parameters": {"type": "object", "properties": {}},
            "execute": _exec_list_timers,
        },
        {
            "name": "cancel_timer",
            "description": "取消当前会话的定时提醒：传编号只取消那一条；不传编号取消本会话全部。",
            "parameters": {
                "type": "object",
                "properties": {"timer_id": {"description": "可选：list_timers 返回的编号；不传＝取消本会话全部"}},
            },
            "execute": _exec_cancel_timer,
        },
        {
            "name": "read_video",
            "description": ("读一条 [视频] 消息：抽几帧画面给你（按「带图」模型分流）+ 本机离线识别视频里的说话内容。"
                            "**没有 ffmpeg 或识别引擎时它会说明原因——照实说读不了，绝不假装看过**；"
                            "抽帧后你要像看图片一样描述你看到的画面，别编造。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"description": "视频消息的 id（聊天记录里 [视频] 前的 #数字）"},
                    "frames": {"description": "抽几帧，默认 4，最多 8"},
                },
                "required": ["message_id"],
            },
            "execute": _exec_read_video,
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

def _exec_set_timer(ctx, args):
    """定时提醒（第 12 条）：只能设到**当前会话**——参数里根本没有"发给谁"这一项，这是红线而不是疏忽。"""
    from . import timers
    from .config import get_config as _gc
    if not ctx.get("chat_key"):
        return _err("没有会话上下文，无法设提醒")
    try:
        cfg = _gc() or {}
    except Exception:
        cfg = {}
    if (cfg.get("timers") or {}).get("enabled") is False:
        return _err("定时提醒功能已在控制台关闭（timers.enabled=false）")
    res = timers.add(ctx["chat_key"], args.get("note"), seconds=args.get("seconds"),
                     minutes=args.get("minutes"), by=str(ctx.get("self_nickname") or ""))
    if not res.get("ok"):
        return _err(res.get("error") or "设置失败")
    mins = max(1, int(round(res["seconds"] / 60.0)))
    extra = "（时长超出范围，已按最近允许值调整）" if res.get("clamped") else ""
    return _ok("已记下：%d 分钟后在本会话提醒「%s」%s。本会话最多挂 %d 条、全局最多 %d 条。"
               % (mins, res["note"], extra, timers.MAX_PENDING_PER_CHAT, timers.MAX_PENDING_TOTAL))


def _exec_list_timers(ctx, args):
    from . import timers
    items = timers.list_all(ctx.get("chat_key"))
    if not items:
        return _ok("当前会话没有待触发的提醒。")
    lines = ["当前会话待触发提醒（最多 %d 条）：" % timers.MAX_PENDING_PER_CHAT]
    for it in items:
        lines.append("- #%s %d 秒后：%s" % (it.get("id"), int(it.get("left_seconds") or 0), it.get("note")))
    return _ok("\n".join(lines))


def _exec_cancel_timer(ctx, args):
    from . import timers
    tid = args.get("timer_id")
    ok_flag = timers.cancel(ctx.get("chat_key"), tid)
    if not ok_flag:
        return _err("没有找到可取消的提醒（可能已经到点或已被取消）")
    return _ok("已取消本会话的提醒%s。" % ((" #%s" % tid) if tid is not None else "（全部）"))


def _exec_read_video(ctx, args):
    """读视频（第 20 条）：抽帧（交给视觉模型）+ 音频离线识别。做不到就如实说。"""
    from . import video_read
    from .config import get_config as _gc
    try:
        cfg = _gc() or {}
    except Exception:
        cfg = {}
    if (cfg.get("video_read") or {}).get("enabled") is False:
        return _err("视频读取功能已在控制台关闭（video_read.enabled=false）")
    items, bad = _media_items(ctx, args.get("message_id"), "video")
    if bad:
        return bad
    if not items:
        return _ok("消息 %s 里没有可读的视频（只有 [视频] 消息才有）" % args.get("message_id"))
    lim = (cfg.get("video_read") or {})
    try:
        n = int(args.get("frames") or lim.get("max_frames") or video_read.DEFAULT_FRAMES)
    except (TypeError, ValueError):
        n = video_read.DEFAULT_FRAMES
    secs = int(lim.get("max_seconds") or video_read.DEFAULT_MAX_SECONDS)
    res = video_read.read_message(ctx["wechat"], ctx["chat_id"], items[0]["local_id"],
                                  max_frames=n, max_seconds=secs)
    if not res.get("ok"):
        return _ok("这段视频我读不了：%s（别假装看过；可以如实告诉对方）" % (res.get("error") or "未知原因"))
    data_urls = []
    for p in res.get("frames") or []:
        try:
            with open(p, "rb") as f:
                import base64
                data_urls.append("data:image/png;base64," + base64.b64encode(f.read()).decode("ascii"))
        except Exception:
            continue
    video_read.cleanup(res.get("dir") or "")
    head = "视频内容（%s）：下面是按时间均匀抽出的 %d 帧画面，请直接看图描述你看到了什么）" % (
        res.get("note") or "", len(data_urls))
    if res.get("audio_ok") and res.get("audio_text"):
        head += "\n视频里的说话内容（本机离线识别，可能有错）：%s" % res["audio_text"][:300]
    elif res.get("audio_why"):
        head += "\n这段视频的音频没识别出来：%s" % res["audio_why"]
    head += "\n（抽帧是采样，不是完整视频；不确定的地方别猜。）"
    if not data_urls:
        return _ok(head)
    return {"content": _image_parts(head, data_urls)}


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


def _media_items(ctx, message_id, kind):
    """取某条消息里指定 kind 的媒体条目（找不到返回 (None, 错误结果)）。"""
    entry = ctx["store"].find_by_mid(ctx["chat_key"], message_id)
    if not entry:
        return None, _err("当前会话找不到消息 %s。%s" % (message_id, _mid_hint(ctx)))
    items = [m for m in (entry.get("media") or []) if m.get("kind") == kind and m.get("local_id")]
    return (items or None), None


def _exec_transcribe_voice(ctx, args):
    """语音转文字（本地离线）。引擎不可用就如实说缺哪一环，绝不编造语音内容。"""
    try:
        from .voice import status as _vstatus, transcribe_message
        st = _vstatus()
        if not st.get("ok"):
            return _ok("本机没有可用的语音识别引擎（%s）。把缺的那一环告诉用户，不要猜语音内容。" % st.get("why"))
        items, bad = _media_items(ctx, args.get("message_id"), "voice")
        if bad:
            return bad
        if not items:
            return _ok("消息 %s 不是语音（只有 [语音] 消息能转文字）" % args.get("message_id"))
        text, err, info = transcribe_message(ctx["wechat"], ctx["chat_id"], items[0]["local_id"])
        if err and not text:
            return _ok("这条语音没转出来：%s。（解码器=%s 引擎=%s）" % (err, info.get("decoder") or "-", info.get("engine") or "-"))
        return _ok({"voice_text": text,
                    "note": "这是本机转写的文字（可能有错别字），用来理解内容即可，不要原样复述给用户。"})
    except Exception as e:
        return _err(str(e))


def _exec_download_media(ctx, args):
    """把视频/文件下到本机（只下载，不发送）。"""
    try:
        kind = str(args.get("kind") or "").strip().lower()
        if kind not in ("video", "file"):
            return _err("kind 只能是 video 或 file")
        items, bad = _media_items(ctx, args.get("message_id"), kind)
        if bad:
            return bad
        if not items:
            return _ok("消息 %s 里没有可下载的%s（只有 [视频] / [文件/链接/卡片] 这类消息才有）"
                       % (args.get("message_id"), "视频" if kind == "video" else "文件"))
        path = ctx["wechat"].download_media(ctx["chat_id"], items[0]["local_id"], kind)
        if not path:
            return _ok("没下下来：微信本地缓存里可能已经没有这个%s了（让对方在微信里点开一次再试）"
                       % ("视频" if kind == "video" else "文件"))
        return _ok({"saved_to": path,
                    "note": "已下载到本机。要发出去的话：链接用 send_message 直接发（纯后台）；视频/文件要用户开启转发开关后才能用 forward_media。"})
    except Exception as e:
        return _err(str(e))


def _exec_forward_media(ctx, args):
    """转发视频/文件（opt-in：会过一次系统「选择文件」对话框 ⇒ 短暂抢前台一次）。"""
    try:
        cfg = get_config()
        if not bool((cfg.get("send") or {}).get("file_forward_optin")):
            return _ok("转发视频/文件默认关闭：它必须过一次系统「选择文件」对话框，会**短暂抢一次前台**（与「不抢前台」的最高目标冲突），"
                       "需要用户在控制台把 send.file_forward_optin 打开才允许。**链接不受影响**——用 send_message 直接发链接是纯后台的。")
        kind = str(args.get("kind") or "").strip().lower()
        if kind not in ("video", "file"):
            return _err("kind 只能是 video 或 file")
        items, bad = _media_items(ctx, args.get("message_id"), kind)
        if bad:
            return bad
        if not items:
            return _ok("消息 %s 里没有可转发的%s" % (args.get("message_id"), "视频" if kind == "video" else "文件"))
        path = ctx["wechat"].download_media(ctx["chat_id"], items[0]["local_id"], kind)
        if not path:
            return _ok("没下下来（本地缓存可能已清理），转发不了")
        ok_flag, msg = ctx["wechat"].send_file_posted(ctx["chat_id"], path)
        if not ok_flag:
            return _ok("转发没成功：%s" % msg)
        return _ok({"sent": True, "note": "已转发（这一步短暂用过前台）。不要输出\"已发送\"类汇报。"})
    except Exception as e:
        return _err(str(e))


_VOICE_LAST = {}          # chat_key -> (ts, text)：同会话同内容的最小间隔（防刷屏）


def _exec_send_voice_reply(ctx, args):
    """文字 → 本机合成音频 → 发到当前会话（**形态是音频文件，不是微信语音条**）。"""
    try:
        from . import voice_models as _vm      # ③：合成通道统一走这层（默认系统声音，可选自带模型）
        vcfg = get_config().get("voice_reply") or {}
        if not vcfg.get("enabled"):
            return _ok("语音回复默认关闭：本机能把文字合成音频，但**发出去是音频文件（不是微信语音条）**，"
                       "所以要先在控制台「语音回复」里打开才允许发。")
        text = str(args.get("text") or "").strip()
        if not text:
            return _err("text 不能为空")
        mx = int(vcfg.get("max_chars") or 120)
        if len(text) > mx:
            return _ok("这条太长（%d 字，上限 %d）：语音回复请压到 %d 字以内。" % (len(text), mx, mx))
        st = _vm.status()
        if not st.get("ok"):
            return _ok("本机没有可用的语音合成引擎：%s（照实说明，别假装发过语音）" % st.get("why"))
        gap = int(vcfg.get("min_gap_seconds") or 30)
        key = str(ctx.get("chat_key") or "")
        last = _VOICE_LAST.get(key) or (0.0, "")
        if last[1] == text and (time.time() - last[0]) < gap:
            return _ok("刚发过一模一样的语音（%.0fs 内），这次不重复发。" % gap)
        path, err, info = _vm.make(text)
        if not path:
            return _ok("合成失败：%s" % (err or "未知原因"))
        ok_flag, msg = ctx["wechat"].send_file_posted(ctx["chat_id"], path)
        if not ok_flag:
            return _ok("语音没发出去：%s" % msg)
        _VOICE_LAST[key] = (time.time(), text)
        try:
            ctx["session"]["sent"].append({"type": "voice_file", "text": text})
        except Exception:
            pass
        out = {"sent": True, "voice": info.get("voice") or "", "fmt": info.get("fmt") or "",
               "note": "已发（形态：音频文件，不是语音条）。不要输出\"已发送\"类汇报。"}
        # 变声段（可选）：把实际走了没有、结果如何**如实**带回给模型
        if info.get("pipeline"):
            out["pipeline"] = info["pipeline"]
        if info.get("vc"):
            out["vc"] = info["vc"]
        if err:
            out["warn"] = err
            out["note"] = ("已发（形态：音频文件，不是语音条）——但**变声这一段失败了**：%s。"
                           "照实说明，别声称用的是目标音色。" % err)
        return _ok(out)
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


def _exec_send_random_image(ctx, args):
    """随机发一张图（从本地图库随机挑，零出网；图库没图/功能关闭时讲清原因）。"""
    try:
        from . import image_lib as _il
        cfg = get_config()
        if not (cfg.get("image_reply") or {}).get("enabled"):
            return _err("随机图功能没开（控制台「随机图」面板打开后可用）；想发群里已有的图请用 send_image")
        path, why = _il.next_image(cfg, chat_id=ctx.get("chat_id") or "")
        if not path:
            return _err(why)
        ctx["sender"].send_image(ctx["chat_key"], path)
        ctx["session"]["sent"].append({"type": "image", "text": "[图片]"})
        return _ok({"sent": True, "file": _os.path.basename(path),
                    "note": "%s。不要输出『已发送』类汇报。" % why})
    except Exception as e:
        return _err(str(e))


_IMG_SEARCH_LAST = {}     # chat_key -> ts：按关键词找图的会话级冷却（防连发）


def _exec_gen_image(ctx, args):
    """群友要图 → 生图链条（`agent/image_gen.py`）：意图解析 → 红线 → 挑后端 → 生成 → 过滤链 → 发送。

    ⚠️ 本机**还没配生图后端**（接本地 ComfyUI 还是在线 API 待用户拍板）⇒ 现在的默认行为是
    "明确说没后端"，**绝不许假装生成过**。红色请求（真人换脸/成人内容）连尝试都不尝试。
    """
    try:
        from . import image_gen as _ig
        req = str((args or {}).get("request") or "").strip()
        if not req:
            return _err("request 不能为空（要说清想要什么图）")
        res = _ig.generate(str(ctx.get("chat_id") or ""), req)
        if not res.get("ok"):
            return _ok("这次没生成出图：%s（照实说明，不要假装生成过）" % res.get("why"))
        sent = []
        for p in (res.get("files") or []):
            try:
                ctx["sender"].send_image(ctx["chat_key"], p)
                sent.append(_os.path.basename(p))
            except Exception as e:
                sent.append("%s(发送异常 %s)" % (_os.path.basename(p), type(e).__name__))
        try:
            ctx["session"]["sent"].append({"type": "image", "text": "[生成图]"})
        except Exception:
            pass
        return _ok({"sent": True, "files": sent, "backend": res.get("backend"),
                    "note": "已生成并通过过滤链后发出（%d 张）。不要输出『已发送』类汇报。" % len(sent)})
    except Exception as e:
        return _err("生图工具异常：%s" % type(e).__name__)


def _exec_send_image_search(ctx, args):
    """按关键词找一张图并发出去（在线图源 + 三段过滤；功能没开/没通过就照实说）。"""
    try:
        from . import image_lib as _il
        cfg = get_config()
        conf = cfg.get("image_reply") or {}
        if not conf.get("enabled"):
            return _err("随机图/找图功能没开（控制台「随机图」面板打开后可用）；想发群里已有的图请用 send_image")
        kw = str(args.get("keyword") or "").strip()
        if not kw:
            return _err("keyword 不能为空（比如「猫」「风景」）")
        gap = int(conf.get("min_gap_seconds") or 20)
        key = str(ctx.get("chat_key") or "")
        last = float(_IMG_SEARCH_LAST.get(key) or 0)
        if last and (time.time() - last) < gap:
            return _ok("刚发过图（%.0fs 内），先缓一下再说。" % gap)
        path, why = _il.search_image(cfg, kw, chat_id=ctx.get("chat_id") or "")
        if not path:
            return _ok("这次没找到能发的图：%s" % why)
        ctx["sender"].send_image(ctx["chat_key"], path)
        _IMG_SEARCH_LAST[key] = time.time()
        try:
            ctx["session"]["sent"].append({"type": "image", "text": "[图片]"})
        except Exception:
            pass
        return _ok({"sent": True, "keyword": kw, "file": _os.path.basename(path),
                    "note": "已找到并发出一张「%s」的图（过滤链全程生效）。不要输出『已发送』类汇报。" % kw})
    except Exception as e:
        return _err(str(e))


def _exec_find_local_file(ctx, args):
    """在本机配好的目录里找文件（只读）。"""
    try:
        from . import file_search as _fs
        hits, why = _fs.search(args.get("name"))
        if not hits:
            return _ok("没找到：%s（要搜别的目录，去控制台「媒体与语音 → 本地文件」里加）" % why)
        return _ok({"count": len(hits),
                    "files": [{"name": h["name"], "path": h["path"], "kb": int(h["size"] / 1024),
                               "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(h["mtime"]))}
                              for h in hits[:10]],
                    "note": "这是候选清单（只读）。用户确认要发哪个之后，再调 send_local_file。"})
    except Exception as e:
        return _err(str(e))


def _exec_send_local_file(ctx, args):
    """把允许目录里的文件发到当前会话（复用发文件那条链 + opt-in 闸）。"""
    try:
        from . import file_search as _fs
        cfg = get_config()
        conf = cfg.get("file_search") or {}
        if not conf.get("enabled"):
            return _ok("「找文件」功能默认关闭（控制台「媒体与语音 → 本地文件」里打开后才可用）。")
        if not (cfg.get("send") or {}).get("file_forward_optin"):
            return _ok("发文件默认关闭：这一步要过一次系统「选择文件」对话框、**会短暂抢一次前台**，"
                       "需要在控制台把「转发视频/文件」打开（链接不受影响）。")
        path, why = _fs.resolve(args.get("name_or_path"))
        if not path:
            if "不在允许目录内" in str(why):
                return _err("安全闸：%s" % why)     # 安全类拒绝标成 error，防模型反复试
            return _ok("没能确定要发哪个文件：%s" % why)
        if not _fs.is_inside(path):
            return _err("安全闸：文件不在允许目录内，拒绝发送（%s）" % path)
        try:
            mb = _os.path.getsize(path) / (1024.0 * 1024.0)
        except OSError:
            return _ok("读取文件失败（可能已被移走）")
        if mb > float(conf.get("max_mb") or 100):
            return _ok("文件太大（%.1fMB > 上限 %.0fMB），不发。" % (mb, float(conf.get("max_mb") or 100)))
        ok_flag, msg = ctx["wechat"].send_file_posted(ctx["chat_id"], path)
        if not ok_flag:
            return _ok("没发出去：%s" % msg)
        _fs.note_sent(path, ctx.get("chat_id") or "")
        try:
            ctx["session"]["sent"].append({"type": "file", "text": _os.path.basename(path)})
        except Exception:
            pass
        return _ok({"sent": True, "file": _os.path.basename(path), "mb": round(mb, 2),
                    "note": "已发出（这一步短暂用过前台）。不要输出\"已发送\"类汇报。"})
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


def _exec_read_bilibili(ctx, args):
    """解析 B 站视频（BV/av/b23 短链）⇒ 标题/UP/时长/简介/分P/字幕。

    2026-09-15 用户重新点名「解析B站视频」这件丢掉的活。三条口径：只读公开接口、不带凭据；
    拿不到就如实说原因（视频没了 / 没字幕 / 接口不通），**绝不编造标题或视频内容**。
    """
    try:
        from . import bilibili as _bili
        v, why = _bili.info(str(args.get("url") or args.get("text") or ""))
        if not v:
            return _err("解析不了这条 B 站链接：%s" % why)
        return _ok({"bvid": v.get("bvid"), "title": v.get("title"), "up": v.get("up"),
                    "duration": v.get("duration"), "desc": v.get("desc"),
                    "parts": len(v.get("pages") or []),
                    "has_subtitle": bool(v.get("subtitle")),
                    "subtitle_why": v.get("subtitle_why") or "",
                    "url": v.get("url"), "text": _bili.to_text(v)})
    except Exception as e:
        return _err("解析 B 站链接失败：%s" % e)


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


# ── 按「能力是否存在」裁剪工具表（省 token，且不让模型白调一轮）──────────────
# 用户 2026-09-15：「**省 token 不仅是你的事，也是群相的事。所有要用模型的地方都要省 token，
#   尽量给用户省钱**（当然还是在不影响效果的前提下）」。
# 实测体积（`_scratch/tools_size.py`）：37 个工具 = 11626 字符 ≈ **7324 token**，
#   比整份系统提示（6256 字符 / 3941 token）还大 —— 是每次请求最大的单块。
# 口径（关键）：**只裁"当前配置/场景下调用必然失败"的工具**。裁掉不损失任何可达效果——
#   那工具本来只会回一句"功能没开"，现在只是不再占着 200~300 字的位置；
#   顺带省掉模型"看到一个工具就去调它 → 拿回错误 → 再想一轮"的那次往返。
#   **绝不裁"模型可能用不上"的**——那属于效果，不是省法。
# 与工具的可用性判定同源：每条规则读的就是工具内部自己查的那个配置键。
def _cfg_at(cfg, path, default=None):
    cur = cfg if isinstance(cfg, dict) else {}
    for k in str(path).split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _prune_reason(name, cfg, kind=None) -> str:
    """返回"该工具当前不可能生效"的原因；None＝保留。"""
    n = str(name or "")
    # ① 场景裁：私聊里没有群成员概念
    if n == "get_active_members" and kind and kind != "group":
        return "只有群聊才有成员列表（当前是私聊）"
    # ② 开关裁：功能总开关关着 ⇒ 调了只会返回"没开"
    if n == "get_message_images" and _cfg_at(cfg, "api.vision", True) is False:
        return "api.vision 关着"
    if n in ("web_search", "web_fetch") and _cfg_at(cfg, "web_search.enabled", True) is False:
        return "web_search.enabled 关着"
    if n in ("send_random_image", "send_image_search") and not _cfg_at(cfg, "image_reply.enabled", False):
        return "image_reply.enabled 关着（随机图/找图没开）"
    if n == "send_image_search" and _cfg_at(cfg, "image_reply.allow_search", True) is False:
        return "image_reply.allow_search 关着（只允许本地图库）"
    if n == "gen_image" and not _cfg_at(cfg, "image_gen.enabled", False):
        return "image_gen.enabled 关着（没接生图后端）"
    if n in ("find_local_file", "send_local_file"):
        if not _cfg_at(cfg, "file_search.enabled", False):
            return "file_search.enabled 关着"
        if not (_cfg_at(cfg, "file_search.dirs", []) or []):
            return "file_search.dirs 没配目录（搜不到任何文件）"
    if n == "send_voice_reply" and not _cfg_at(cfg, "voice_reply.enabled", False):
        return "voice_reply.enabled 关着"
    if n == "moments_like" and not _cfg_at(cfg, "behavior.like_moments.enabled", False):
        return "behavior.like_moments.enabled 关着"
    if n == "moments_comment" and not _cfg_at(cfg, "behavior.moments_comment.enabled", False):
        return "behavior.moments_comment.enabled 关着"
    if n == "moments_publish" and not _cfg_at(cfg, "behavior.moments_publish.enabled", False):
        return "behavior.moments_publish.enabled 关着"
    if n == "moments_surf" and not _cfg_at(cfg, "behavior.moments_surf.enabled", False):
        return "behavior.moments_surf.enabled 关着"
    return None


def visible_defs(defs: list, cfg=None, kind=None):
    """按能力裁剪后的工具表 ⇒ `(留下, [{"name","why"}])`。

    每次唤醒现算（控制台「改完即生效」⇒ 开关一开，下一次唤醒工具就回来），
    不缓存、不落盘（避免"配置改了但表是旧的"这种静默失配）。
    """
    kept, dropped = [], []
    for d in (defs or []):
        why = _prune_reason(d.get("name"), cfg, kind)
        if why:
            dropped.append({"name": d.get("name"), "why": why})
        else:
            kept.append(d)
    return kept, dropped


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
        res = def_obj["execute"](ctx, args)
    except Exception as e:
        res = {"content": "错误：%s" % e, "is_error": True}
    # 按工具名记一笔（唯一分发点 ⇒ 内置与自定义都覆盖；只记名字/次数，不记参数内容）
    try:
        from . import tool_stats as _ts
        _ts.note(name, ok=not (isinstance(res, dict) and res.get("is_error")))
    except Exception:
        pass
    return res
