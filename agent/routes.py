# -*- coding: utf-8 -*-
"""**唯一路由表**（声明）+ **已搬成方法的那部分**（`HANDLERS`）。

`agent/webui.py` 原来有 `do_GET` / `_handle_body_request` **两条互相独立的分派链**
（共 145 个分支）—— 同一个能力只注册在一条链里、而前端用另一个方法调，
就会静默 404。
CowAgent 靠「结构上只有一张路由表」让这类错误不存在。

两层结构（分批搬迁期间并存）：
  · `ROUTES`：**声明**——每个路径允许哪些方法（全量 104 条，由 Phase A 定下来，
    了 `/api/tools/test`：Phase B 之后新增路由的标准动作就是「方法 + 这张表两行」）；
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
    "/api/tools/export": ("GET",),
    "/api/tools/import": ("POST",),
    "/api/tools/new_manifest": ("GET", "POST"),
    "/api/tools/reload": ("GET",),
    "/api/tools/test": ("GET",),
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
    # ⚠️ 下面 6 条是 `_handle_body_request` 里的**字面分支**，以前漏登记（判据 A1 抓出来的）。
    #    这里只声明 POST —— 与上面 3 条同一口径：本表覆盖的是 `do_GET` / `_handle_body_request`
    #    两条字面分派链；挂件那条 GET 链（`Handler._whale_get`，19 条）走的是
    #    `path.startswith("/dsh-whale/")` 的**非字面分支**，由本文件的 `PATTERNS` 兜底登记，
    #    不在这里逐条枚举（逐条枚举会与 PATTERNS 口径打架）。
    "/dsh-whale/api-models.json": ("POST",),
    "/dsh-whale/balance-adjustments.json": ("POST",),
    "/dsh-whale/bubble-img-upload.json": ("POST",),
    "/dsh-whale/role-delete.json": ("POST",),
    "/dsh-whale/role-pin.json": ("POST",),
    "/dsh-whale/roles.json": ("POST",),
    "/dsh-whale/usage-settings.json": ("POST",),
    "/index.html": ("GET",),
}

#: 已搬成 Handler 方法的：路径 → {方法: 函数名}
HANDLERS = {
    "/api/archive": {
        "GET": "_rapi_archive",
        "POST": "_rapi_archive_post",
    },
    "/api/archive/block": {
        "POST": "_rapi_archive_block_post",
    },
    "/api/archive/delete": {
        "POST": "_rapi_archive_block_post",
    },
    "/api/archive/unblock": {
        "POST": "_rapi_archive_block_post",
    },
    "/api/balance": {
        "GET": "_rapi_balance",
    },
    "/api/briefs": {
        "GET": "_rapi_briefs",
        "POST": "_rapi_briefs_post",
    },
    "/api/cloud/test": {
        "POST": "_rapi_cloud_test_post",
    },
    "/api/code-check": {
        "POST": "_rapi_code_check_post",
    },
    "/api/code-check/progress": {
        "POST": "_rapi_code_check_progress_post",
    },
    "/api/community/export": {
        "POST": "_rapi_community_export_post",
    },
    "/api/community/upload": {
        "POST": "_rapi_community_upload_post",
    },
    "/api/config": {
        "GET": "_rapi_config",
        "POST": "_rapi_config_post",
    },
    "/api/cursor/reset": {
        "POST": "_rapi_cursor_reset_post",
    },
    "/api/cursor/upload": {
        "POST": "_rapi_cursor_upload_post",
    },
    "/api/data/export": {
        "POST": "_rapi_data_export_post",
    },
    "/api/data/import": {
        "POST": "_rapi_data_import_post",
    },
    "/api/decide": {
        "POST": "_rapi_decide_post",
    },
    "/api/emojis": {
        "GET": "_rapi_emojis",
    },
    "/api/emojis/delete": {
        "POST": "_rapi_emojis_delete_post",
    },
    "/api/feedback": {
        "GET": "_rapi_feedback",
    },
    "/api/feedback/flush": {
        "POST": "_rapi_feedback_flush_post",
    },
    "/api/feedback/submit": {
        "POST": "_rapi_feedback_submit_post",
    },
    "/api/file_search/add": {
        "GET": "_rapi_file_search_add",
        "POST": "_rapi_file_search_add_post",
    },
    "/api/file_search/del": {
        "GET": "_rapi_file_search_add",
        "POST": "_rapi_file_search_add_post",
    },
    "/api/image_gen/local": {
        "GET": "_rapi_image_gen_local",
    },
    "/api/image_gen/local/install": {
        "GET": "_rapi_image_gen_local_install",
        "POST": "_rapi_image_gen_local_install_post",
    },
    "/api/image_gen/local/preset": {
        "POST": "_rapi_image_gen_local_preset_post",
    },
    "/api/image_gen/local/progress": {
        "GET": "_rapi_image_gen_local_progress",
    },
    "/api/image_gen/local/start": {
        "GET": "_rapi_image_gen_local_start",
        "POST": "_rapi_image_gen_local_start_post",
    },
    "/api/image_gen/local/stop": {
        "GET": "_rapi_image_gen_local_stop",
        "POST": "_rapi_image_gen_local_stop_post",
    },
    "/api/image_gen/test": {
        "GET": "_rapi_image_gen_test",
    },
    "/api/learning/evaluate": {
        "POST": "_rapi_learning_evaluate_post",
    },
    "/api/learning/start": {
        "POST": "_rapi_learning_start_post",
    },
    "/api/local-models": {
        "GET": "_rapi_local_models",
    },
    "/api/logs": {
        "GET": "_rapi_logs",
    },
    "/api/memory": {
        "GET": "_rapi_memory",
        "POST": "_rapi_memory_post",
    },
    "/api/memory/deep-profile": {
        "POST": "_rapi_memory_deep_profile_post",
    },
    "/api/open-path": {
        "GET": "_rapi_open_path",
        "POST": "_rapi_open_path_post",
    },
    "/api/pause": {
        "POST": "_rapi_pause_post",
    },
    "/api/persona/ai-enrich": {
        "POST": "_rapi_persona_ai_enrich_post",
    },
    "/api/persona/behavior-recommend": {
        "POST": "_rapi_persona_behavior_recommend_post",
    },
    "/api/persona/cats": {
        "GET": "_rapi_persona_cats",
    },
    "/api/persona/cats/del": {
        "POST": "_rapi_persona_cats_del_post",
    },
    "/api/persona/cats/save": {
        "POST": "_rapi_persona_cats_save_post",
    },
    "/api/persona/score": {
        "POST": "_rapi_persona_score_post",
    },
    "/api/persona/web-fetch": {
        "POST": "_rapi_persona_web_fetch_post",
    },
    "/api/personas": {
        "GET": "_rapi_personas",
    },
    "/api/personas/custom": {
        "GET": "_rapi_personas_custom",
        "POST": "_rapi_personas_custom_post",
    },
    "/api/personas/custom/del": {
        "POST": "_rapi_personas_custom_del_post",
    },
    "/api/personas/fav": {
        "POST": "_rapi_personas_fav_post",
    },
    "/api/personas/favs": {
        "GET": "_rapi_personas_favs",
        "POST": "_rapi_personas_favs_post",
    },
    "/api/personas/rate": {
        "POST": "_rapi_personas_rate_post",
    },
    "/api/personas/scores": {
        "GET": "_rapi_personas_scores",
    },
    "/api/poke-test": {
        "POST": "_rapi_poke_test_post",
    },
    "/api/prices": {
        "POST": "_rapi_prices_post",
    },
    "/api/prompt/preview": {
        "GET": "_rapi_prompt_preview",
        "POST": "_rapi_prompt_preview_post",
    },
    "/api/restart": {
        "POST": "_rapi_restart_post",
    },
    "/api/resume": {
        "POST": "_rapi_resume_post",
    },
    "/api/risk": {
        "POST": "_rapi_risk_post",
    },
    "/api/scoring/import": {
        "POST": "_rapi_scoring_import_post",
    },
    "/api/scoring/stats": {
        "POST": "_rapi_scoring_stats_post",
    },
    "/api/selfcheck": {
        "POST": "_rapi_selfcheck_post",
    },
    "/api/selfcheck-stop": {
        "GET": "_rapi_selfcheck_stop",
        "POST": "_rapi_selfcheck_stop_post",
    },
    "/api/sessions": {
        "GET": "_rapi_sessions",
        "POST": "_rapi_sessions_post",
    },
    "/api/sessions/delete": {
        "POST": "_rapi_sessions_delete_post",
    },
    "/api/sessions/restore": {
        "POST": "_rapi_sessions_restore_post",
    },
    "/api/shutdown": {
        "POST": "_rapi_shutdown_post",
    },
    "/api/stats/cal": {
        "POST": "_rapi_stats_cal_post",
    },
    "/api/stats/cal_clear": {
        "POST": "_rapi_stats_cal_clear_post",
    },
    "/api/stats/cal_delete": {
        "POST": "_rapi_stats_cal_delete_post",
    },
    "/api/stats/cal_list": {
        "POST": "_rapi_stats_cal_list_post",
    },
    "/api/status": {
        "GET": "_rapi_status",
    },
    "/api/test-api": {
        "POST": "_rapi_test_api_post",
    },
    "/api/tools/export": {
        "GET": "_rapi_tools_export",
    },
    "/api/tools/import": {
        "POST": "_rapi_tools_import_post",
    },
    "/api/tools/new_manifest": {
        "GET": "_rapi_tools_new_manifest",
        "POST": "_rapi_tools_new_manifest_post",
    },
    "/api/tools/reload": {
        "GET": "_rapi_tools_reload",
    },
    "/api/tools/test": {
        "GET": "_rapi_tools_test",
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
    "/api/ui/background": {
        "POST": "_rapi_ui_background_post",
    },
    "/api/ui/recalibrate": {
        "GET": "_rapi_ui_recalibrate",
        "POST": "_rapi_ui_recalibrate_post",
    },
    "/api/ui_fingerprint/forget": {
        "GET": "_rapi_ui_fingerprint_forget",
        "POST": "_rapi_ui_fingerprint_forget_post",
    },
    "/api/ui_fingerprint/take": {
        "GET": "_rapi_ui_fingerprint_take",
        "POST": "_rapi_ui_fingerprint_take_post",
    },
    "/api/update_apply": {
        "POST": "_rapi_update_apply_post",
    },
    "/api/update_reset": {
        "POST": "_rapi_update_reset_post",
    },
    "/api/update_skip": {
        "POST": "_rapi_update_skip_post",
    },
    "/api/verifiers": {
        "GET": "_rapi_verifiers",
        "POST": "_rapi_verifiers_post",
    },
    "/api/verify": {
        "GET": "_rapi_verify",
        "POST": "_rapi_verify_post",
    },
    "/api/version/action": {
        "POST": "_rapi_version_action_post",
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
    "/api/watermark/reset": {
        "POST": "_rapi_watermark_reset_post",
    },
    "/api/wechat-groups": {
        "GET": "_rapi_wechat_groups",
        "POST": "_rapi_wechat_groups_post",
    },
    "/api/wechat/dir": {
        "GET": "_rapi_wechat_dir",
        "POST": "_rapi_wechat_dir_post",
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

# ── Phase B 记账───────────
#   ① 分支体的删除区间用 **`body[-1].end_lineno`**：`If.end_lineno` 把 `orelse` 也算进去，
#      最后一个 `elif` 会把链尾那个 `else:` 一起吞掉 ⇒ 分派那句插不进去；
#   ② 链尾 `else:` 要**连缩进一起匹配**：`else:` 遍地都是，只比 `strip()` 会命中
#      前一个分支里嵌套的 `else:`（它在删除区间里）；
#   ③ 新方法必须落在 **Handler 类体内**（`class Handler` 在 `WebUI.__init__` 里、缩进 8）；
#      而插入下标必须算在**删完之后**的行号上（拿原行号当索引会越界 ⇒
#      新方法被追加到文件末尾、落进 `WebUI.stop()`，`self._dispatch` 就找不到）。
#   上面三条已写进迁移器 `_scratch\_migrate_get.py` 的自检（落盘前会 assert）。
