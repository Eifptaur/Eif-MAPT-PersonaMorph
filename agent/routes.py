# -*- coding: utf-8 -*-
"""**唯一路由表**（声明）+ **已搬成方法的那部分**（`HANDLERS`）。

`agent/webui.py` 原来有 `do_GET` / `_handle_body_request` **两条互相独立的分派链**
（共 145 个分支）—— 同一个能力只注册在一条链里、而前端用另一个方法调，
就会静默 404（第十二轮 `/api/archive`、第十五轮 7 处都是这个形状）。
CowAgent 靠「结构上只有一张路由表」让这类错误不存在。

两层结构（分批搬迁期间并存）：
  · `ROUTES`：**声明**——每个路径允许哪些方法（全量 103 条，由 Phase A 定下来）；
  · `HANDLERS`：**已物理搬过去的**：`路径 → {方法: Handler 上的函数名}`；
    `webui.py::Handler._dispatch` 只认这里成对存在的 (路径, 方法)，命中就调用；
    没搬的照旧走原来那条 `if/elif` 链（行为一个字不变）。

`scripts/route_table_selftest.py` 双向对账：声明 ↔（链里的字面分支 ＋ `HANDLERS`）；
并且**同一条路由不许两处都接**（链里还有分支就不该在 HANDLERS 里）。
"""

#: 路径 → 允许的方法（声明）
ROUTES = {
    "/": ("GET",),
    "/api/archive": ("GET", "POST"),
    "/api/archive/block": ("POST",),
    "/api/archive/delete": ("POST",),
    "/api/archive/unblock": ("POST",),
    "/api/balance": ("GET",),
    "/api/briefs": ("GET", "POST"),
    "/api/cloud/test": ("POST",),
    "/api/code-check": ("POST",),
    "/api/code-check/progress": ("POST",),
    "/api/community/export": ("POST",),
    "/api/community/upload": ("POST",),
    "/api/config": ("GET", "POST"),
    "/api/cursor/reset": ("POST",),
    "/api/cursor/upload": ("POST",),
    "/api/data/export": ("POST",),
    "/api/data/import": ("POST",),
    "/api/decide": ("POST",),
    "/api/emojis": ("GET",),
    "/api/emojis/delete": ("POST",),
    "/api/feedback": ("GET",),
    "/api/feedback/flush": ("POST",),
    "/api/feedback/submit": ("POST",),
    "/api/file_search/add": ("GET", "POST"),
    "/api/file_search/del": ("GET", "POST"),
    "/api/image_gen/local": ("GET",),
    "/api/image_gen/local/install": ("GET", "POST"),
    "/api/image_gen/local/preset": ("POST",),
    "/api/image_gen/local/progress": ("GET",),
    "/api/image_gen/local/start": ("GET", "POST"),
    "/api/image_gen/local/stop": ("GET", "POST"),
    "/api/image_gen/test": ("GET",),
    "/api/learning/evaluate": ("POST",),
    "/api/learning/start": ("POST",),
    "/api/local-models": ("GET",),
    "/api/logs": ("GET",),
    "/api/memory": ("GET", "POST"),
    "/api/memory/deep-profile": ("POST",),
    "/api/open-path": ("GET", "POST"),
    "/api/pause": ("POST",),
    "/api/persona/ai-enrich": ("POST",),
    "/api/persona/behavior-recommend": ("POST",),
    "/api/persona/cats": ("GET",),
    "/api/persona/cats/del": ("POST",),
    "/api/persona/cats/save": ("POST",),
    "/api/persona/score": ("POST",),
    "/api/persona/web-fetch": ("POST",),
    "/api/personas": ("GET",),
    "/api/personas/custom": ("GET", "POST"),
    "/api/personas/custom/del": ("POST",),
    "/api/personas/fav": ("POST",),
    "/api/personas/favs": ("GET", "POST"),
    "/api/personas/rate": ("POST",),
    "/api/personas/scores": ("GET",),
    "/api/poke-test": ("POST",),
    "/api/prices": ("POST",),
    "/api/prompt/preview": ("GET", "POST"),
    "/api/restart": ("POST",),
    "/api/resume": ("POST",),
    "/api/risk": ("POST",),
    "/api/scoring/import": ("POST",),
    "/api/scoring/stats": ("POST",),
    "/api/selfcheck": ("POST",),
    "/api/selfcheck-stop": ("GET", "POST"),
    "/api/sessions": ("GET", "POST"),
    "/api/sessions/delete": ("POST",),
    "/api/sessions/restore": ("POST",),
    "/api/shutdown": ("POST",),
    "/api/stats/cal": ("POST",),
    "/api/stats/cal_clear": ("POST",),
    "/api/stats/cal_delete": ("POST",),
    "/api/stats/cal_list": ("POST",),
    "/api/status": ("GET",),
    "/api/test-api": ("POST",),
    "/api/tools/new_manifest": ("GET", "POST"),
    "/api/tools/reload": ("GET",),
    "/api/tools/toggle": ("GET",),
    "/api/tts/test": ("GET",),
    "/api/ui-layout": ("GET",),
    "/api/ui/background": ("POST",),
    "/api/ui/recalibrate": ("GET", "POST"),
    "/api/ui_fingerprint/forget": ("GET", "POST"),
    "/api/ui_fingerprint/take": ("GET", "POST"),
    "/api/update": ("GET",),
    "/api/update_apply": ("POST",),
    "/api/update_reset": ("POST",),
    "/api/update_skip": ("POST",),
    "/api/verifiers": ("GET", "POST"),
    "/api/verify": ("GET", "POST"),
    "/api/version": ("GET",),
    "/api/version/action": ("POST",),
    "/api/version/allow": ("GET",),
    "/api/voice/probe": ("GET",),
    "/api/voice/test": ("GET",),
    "/api/voice/vc-probe": ("GET",),
    "/api/watermark/reset": ("POST",),
    "/api/wechat-groups": ("GET", "POST"),
    "/api/wechat/dir": ("GET", "POST"),
    "/api/wechat/recheck": ("GET",),
    "/dsh-whale/audio.json": ("POST",),
    "/dsh-whale/bubble.json": ("POST",),
    "/dsh-whale/size.json": ("POST",),
    "/index.html": ("GET",),
}

#: 已搬成 Handler 方法的：路径 → {方法: 函数名}
HANDLERS = {
    "/api/archive": {
        "GET": "_rapi_archive",
    },
    "/api/balance": {
        "GET": "_rapi_balance",
    },
    "/api/briefs": {
        "GET": "_rapi_briefs",
    },
    "/api/config": {
        "GET": "_rapi_config",
    },
    "/api/emojis": {
        "GET": "_rapi_emojis",
    },
    "/api/feedback": {
        "GET": "_rapi_feedback",
    },
    "/api/file_search/add": {
        "GET": "_rapi_file_search_add",
    },
    "/api/file_search/del": {
        "GET": "_rapi_file_search_add",
    },
    "/api/image_gen/local": {
        "GET": "_rapi_image_gen_local",
    },
    "/api/image_gen/local/install": {
        "GET": "_rapi_image_gen_local_install",
    },
    "/api/image_gen/local/progress": {
        "GET": "_rapi_image_gen_local_progress",
    },
    "/api/image_gen/local/start": {
        "GET": "_rapi_image_gen_local_start",
    },
    "/api/image_gen/local/stop": {
        "GET": "_rapi_image_gen_local_stop",
    },
    "/api/image_gen/test": {
        "GET": "_rapi_image_gen_test",
    },
    "/api/local-models": {
        "GET": "_rapi_local_models",
    },
    "/api/logs": {
        "GET": "_rapi_logs",
    },
    "/api/memory": {
        "GET": "_rapi_memory",
    },
    "/api/open-path": {
        "GET": "_rapi_open_path",
    },
    "/api/persona/cats": {
        "GET": "_rapi_persona_cats",
    },
    "/api/personas": {
        "GET": "_rapi_personas",
    },
    "/api/personas/custom": {
        "GET": "_rapi_personas_custom",
    },
    "/api/personas/favs": {
        "GET": "_rapi_personas_favs",
    },
    "/api/personas/scores": {
        "GET": "_rapi_personas_scores",
    },
    "/api/prompt/preview": {
        "GET": "_rapi_prompt_preview",
    },
    "/api/selfcheck-stop": {
        "GET": "_rapi_selfcheck_stop",
    },
    "/api/sessions": {
        "GET": "_rapi_sessions",
    },
    "/api/status": {
        "GET": "_rapi_status",
    },
    "/api/tools/new_manifest": {
        "GET": "_rapi_tools_new_manifest",
    },
    "/api/tools/reload": {
        "GET": "_rapi_tools_reload",
    },
    "/api/tools/toggle": {
        "GET": "_rapi_tools_toggle",
    },
    "/api/tts/test": {
        "GET": "_rapi_tts_test",
    },
    "/api/ui-layout": {
        "GET": "_rapi_ui_layout",
    },
    "/api/ui/recalibrate": {
        "GET": "_rapi_ui_recalibrate",
    },
    "/api/ui_fingerprint/forget": {
        "GET": "_rapi_ui_fingerprint_forget",
    },
    "/api/ui_fingerprint/take": {
        "GET": "_rapi_ui_fingerprint_take",
    },
    "/api/verifiers": {
        "GET": "_rapi_verifiers",
    },
    "/api/verify": {
        "GET": "_rapi_verify",
    },
    "/api/version/allow": {
        "GET": "_rapi_version_allow",
    },
    "/api/voice/probe": {
        "GET": "_rapi_voice_probe",
    },
    "/api/voice/test": {
        "GET": "_rapi_voice_test",
    },
    "/api/voice/vc-probe": {
        "GET": "_rapi_voice_vc_probe",
    },
    "/api/wechat-groups": {
        "GET": "_rapi_wechat_groups",
    },
    "/api/wechat/dir": {
        "GET": "_rapi_wechat_dir",
    },
    "/api/wechat/recheck": {
        "GET": "_rapi_wechat_recheck",
    },
}

#: 两条链里**非字面**的分支（条件原文）：这些路径没法按字符串枚举。
PATTERNS = [
    'path.startswith("/wallpaper/")',
    'not self._auth_ok()',
    'path == "/" or path == "/index.html"',
    'path.startswith("/api/") or path.startswith("/dsh-whale/") \\ or path.s',
    '"/" not in path[1:]',
    'path.startswith("/dsh-whale/")',
    'length < 0 or length > _MAX_BODY',
]

# ── Phase B 记账（2026-09-22，第一版试搬时连撞三个机械陷阱，已全部修好）───────────
#   ① 分支体的删除区间用 **`body[-1].end_lineno`**：`If.end_lineno` 把 `orelse` 也算进去，
#      最后一个 `elif` 会把链尾那个 `else:` 一起吞掉 ⇒ 分派那句插不进去；
#   ② 链尾 `else:` 要**连缩进一起匹配**：`else:` 遍地都是，只比 `strip()` 会命中
#      前一个分支里嵌套的 `else:`（它在删除区间里）；
#   ③ 新方法必须落在 **Handler 类体内**（`class Handler` 在 `WebUI.__init__` 里、缩进 8）；
#      而插入下标必须算在**删完之后**的行号上（拿原行号当索引会越界 ⇒
#      新方法被追加到文件末尾、落进 `WebUI.stop()`，`self._dispatch` 就找不到）。
#   上面三条已写进迁移器 `_scratch\_migrate_get.py` 的自检（落盘前会 assert）。
