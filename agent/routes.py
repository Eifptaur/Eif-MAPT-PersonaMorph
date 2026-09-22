# -*- coding: utf-8 -*-
"""**唯一路由表**：每个 `/api/…` 路径允许哪些 HTTP 方法（`agent/webui.py` 里两条分派链的**声明**）。

**为什么要它**（对标 CowAgent，2026-09-22 第 ④ 项）：`webui.py` 有 `do_GET` / `_handle_body_request`
**两条互相独立的分派链**——同一个能力只注册在一条链里、而前端用另一个方法调，就会静默 404
（第十二轮 `/api/archive`、第十五轮 7 处，都是这个形状）。CowAgent 靠「结构上只有一张路由表」
让这类错误不存在。

**本表的地位（Phase A）**：它是**声明 + 对账**用的唯一清单，代码暂时仍是那两条链
（物理合并＝Phase B，见文末备注）。`scripts/route_table_selftest.py` 双向锁死：
表 == 代码（表里不许有代码没有的路由、代码里也不许有表外的路由），所以**加一条路由必须同时进本表**。

格式：`ROUTES[路径] = (允许的方法…)`；`PATTERNS` 是**非字面**分支（`path in (…, endswith…)` 那种），
无法按路径枚举，登记条件原文以便人工核对。
"""


#: 路径 → 允许的方法（GET / POST / PUT 都算 POST 链）
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

#: 两条链里**非字面**的分支（条件原文）：这些路径没法按字符串枚举，
#: 但它们也在表外 ⇒ 判据只要求「字面路径」两边一致。
PATTERNS = [
    'path.startswith("/wallpaper/")',
    'not self._auth_ok()',
    'path == "/" or path == "/index.html"',
    'path.startswith("/api/") or path.startswith("/dsh-whale/") \\ or path.s',
    '"/" not in path[1:]',
    'path.startswith("/dsh-whale/")',
    'length < 0 or length > _MAX_BODY',
]

# ── Phase B 备注（物理合并那一步的三个机械陷阱，2026-09-22 试过一版并回退）──────────
#   ① 分支体的结束行要用 **`body[-1].end_lineno`**，不能用 `If.end_lineno`（后者把 `orelse`
#      也算进去 ⇒ 最后一个 `elif` 的删除区间会吞掉链尾那个 `else:`，分派那句插不进去）；
#   ② 链尾 `else:` 那一行要**连缩进一起匹配**（`else:` 遍地都是，只看 strip 会匹配到前一个
#      分支里嵌套的 `else:`，而它在删除区间里 ⇒ 同样插不进去）；
#   ③ 新方法必须插在 **Handler 类的体内**：`class Handler` 不是 `WebUI` 的最后一个成员，
#      按「最后一个成员的 end_lineno」往后插会插进 `WebUI.stop()` 里（`self._dispatch` 就找不到了）。
#      正确做法＝插在 Handler 类体最后一行的**之前**，或插到类体末尾并保证缩进 12 空格落在类内。
