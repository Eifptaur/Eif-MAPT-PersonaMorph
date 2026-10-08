# -*- coding: utf-8 -*-
"""控制台**暴露面**回归判据（定调「前后端同做」的 B 方向：后端有、前端不显示）。

两个方向的分工（别混）：
  · **A 方向「前端有、后端不读」＝假开关** ⇒ 由 `cfg_keys_selftest.py` 的 A2 段常驻守（0 个假开关）。
  · **B 方向「后端有、前端不显示」＝欠账** ⇒ 就是本文件。

⛔ 本判据**故意不设"全库暴露率阈值"**：那会逼着给内部/环境键（`db_dir`、`media_dir`、
  `record_btn` 这类**落点标定值**）硬凑界面，把用户拉进"改坏了就点错地方"的坑。
欠账改用**逐段推进 + 做完就钉住**：`MUST_EXPOSE` 一次加一段，本判据保证**已做的不许退回去**。
⛔ 每加一段前，先把该段里"有意不暴露"的键**写明理由**（登记在计划的对应条目里）。

用法：runtime\\python\\python.exe scripts/console_expose_selftest.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ui_qt"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


#: 已做完的段（做完一段就往这里加一条）。格式：键路径 → 该段的"有意不暴露"清单（写明理由）
MUST_EXPOSE = (
    # 第 ① 段：真语音条机制。有意不暴露：record_btn / send_btn / toggle_x / toggle_y
    # —— 它们是落点标定值（比例坐标），给用户裸改会点到错位置。
    "voice_strip.enabled",
    "voice_strip.enter_via",
    "voice_strip.meter_guard",
    "voice_strip.real_click",
    "voice_strip.fallback_click",
    "voice_strip.find_by_frame",
    "voice_strip.confirm_timeout",
    "voice_strip.out_device",
    # `send` 段先钉住"过渡闸门"这一条（该段其余键待逐条判"该不该给用户"）
    "send.only_popup",
    # 第 ⑤ 段：更新提示（`update`）。有意不暴露：url / trust_custom_url / allow_local
    # —— 三个都是**供应链入口**（换了源就能从任意地址拉包），不给裸改口子。
    "update.muted",
    "update.skip_version",
    # 第 ⑥ 段：本机适配（`env_profile`）—— 默认开，必须让用户能看见也能关掉
    # （有意不暴露：该段只有这一个键）。
    "env_profile.auto_apply",
    # 第 ② 段：随机图（`image_reply`，28 键 ⇒ 暴露 19、有意不暴露 9）。
    # 有意不暴露的 9 个，分两类：
    #  · **列表型**：`sources`（图源顺序＝策略本身）/ `tag_blacklist`（**默认空＝用内置黑名单**，
    #    自填只会削弱过滤）—— 控制台没有列表控件，做它属另一件事；要动就编辑配置文件。
    #  · **调度型**：`meta_timeout_ms` / `download_timeout_ms` / `total_budget_ms` /
    #    `sources_per_try` / `download_race` / `source_cooldown_s` / `slow_cooldown_s`
    #    —— 七项互相牵制（改一个就会与其余失衡，用户无从判断合理值）；
    #    控制台在该段底部留了一行提示，写明"这组要动就改配置文件 image_reply 段"（不是静默缺口）。
    "image_reply.api_url",
    "image_reply.api_timeout_ms",
    "image_reply.tag",
    "image_reply.cache_fallback",
    "image_reply.allow_questionable",
    "image_reply.vision_filter",
    "image_reply.vision_fail_open",
    "image_reply.min_side",
    "image_reply.skin_max_ratio",
    "image_reply.max_mb",
    "image_reply.min_gap_seconds",
    "image_reply.avoid_recent",
    "image_reply.include_gif",
    # ⑦ 群活跃度自适应（`group_adaptive`，第 134 项）—— 让上面那几个「上限」跟着群的冷热缩放。
    #    有意不暴露（3 个）：`cold_ratio` / `hot_ratio` / `baseline_days`
    #    —— 它们是"多冷算冷、多热算热、拿多少天当基线"的**内部判据**，改错会把冷热判反（比不调更糟）。
    "group_adaptive.enabled",
    "group_adaptive.window_min",
    "group_adaptive.cold_scale",
    "group_adaptive.hot_scale",
    "group_adaptive.max_scale",
    "group_adaptive.min_cap",
    # ⑧ 生成侧 IP 闸门（`image_gen.ip_guard`，第 187 项）—— 用户必须能看见"这条闸门在不在"、
    #    也必须能关掉它（它是**内置名单**，不是用户填的）。
    #    有意不暴露：`extra_block`（补充名单）是**列表型**，控制台没有列表控件（与 `image_reply.sources`
    #    同一类理由）；要加名单就编辑配置文件。
    "image_gen.ip_guard.enabled",
    # ③ feedback —— 该段 14 键分两类，做完的是**非凭据那一半**（第 144 项第 ③ 段）：
    #    暴露 6 个：`enabled`（总开关）· `attach_compat`（**隐私开关**，用户真该能关掉"附带环境摘要"）·
    #    `limit.*` 四个（限频，纯偏好，防手滑连发）。
    #    ⛔ **有意不暴露 8 个**（不是"待做"）：`to` / `upload_url` / `webhook_url` / `webhook_token` /
    #    `smtp.{host,port,user,password}` —— 全是**投递凭据**（收件地址、群机器人地址与口令、邮箱授权码），
    #    把凭据摆进网页界面＝把钥匙贴在门上。要自建接收端的人手改 `config.json`（手改本身就是显式动作），
    #    控制台该节底部留了一行明确的只读提示（不是静默缺口）。
    "feedback.enabled",
    "feedback.attach_compat",
    "feedback.limit.per_minute",
    "feedback.limit.per_hour",
    "feedback.limit.per_day",
    "feedback.limit.dup_window_s",
)

HTML_PATH = os.path.join(ROOT, "assets", "console", "index.html")
HTML = io.open(HTML_PATH, encoding="utf-8").read()


def _web_keys(text=None):
    t = HTML if text is None else text
    return set(k for k in re.findall(r'data-cfg="([^"]+)"', t) if "'" not in k)


def _missing(text=None):
    keys = _web_keys(text)
    return [k for k in MUST_EXPOSE if k not in keys]


print("── A. 分母守卫 ──")
_n = len(_web_keys())
ok("控制台真的扫到了 ≥250 个 data-cfg 键（否则下面几条测的是空气）", _n >= 250, "%d 个" % _n)
ok("MUST_EXPOSE 不为空（清单被清空＝这条判据形同不存在）", len(MUST_EXPOSE) >= 8,
   "%d 条" % len(MUST_EXPOSE))

print("── B. 已做的段不许退回去（web 面）──")
_miss = _missing()
ok("MUST_EXPOSE 的每个键都还在控制台 HTML 里", not _miss, "、".join(_miss) if _miss else "0 个缺")

print("── C. 同一批键**在 Qt 面也认得出**（web 有、Qt 没有＝这一侧最难发现）──")
try:
    sys.path.insert(0, os.path.join(ROOT, "ui_qt"))
    import sec_meta  # noqa: E402  （只读 HTML 与 config.example.json，不碰产品写接口）

    _qt_keys = set()
    for _name, _sec in (sec_meta.secs() or {}).items():
        for _row in (getattr(_sec, "rows", None) or []):
            _k = getattr(_row, "cfg", None) or getattr(_row, "key", None)
            if _k:
                _qt_keys.add(_k)
    _qt_miss = [k for k in MUST_EXPOSE if k not in _qt_keys]
    ok("Qt 侧（sec_meta 解析 web 行）也能读出同一批键", not _qt_miss,
       "、".join(_qt_miss) if _qt_miss else "0 个缺")
    ok("分母守卫：Qt 侧解析出的键数 ≥250", len(_qt_keys) >= 250, "%d 个" % len(_qt_keys))
except Exception as _e:
    ok("Qt 侧检查跑不起来（照实报，不许静默跳过）", False, str(_e)[:120])

print("── D. 反向锚：判据自己抓得住「少了一条」──")
_fake = HTML.replace('data-cfg="voice_strip.enter_via"', 'data-cfg="voice_strip.__gone__"')
_hit = _missing(_fake)
ok("★把某个键从 HTML 里挪走 ⇒ 必须被抓出来",
   _hit == ["voice_strip.enter_via"], str(_hit))
ok("★反向锚的自检：原样 HTML 不该报缺（否则上一条是假阳性）", not _missing(HTML), "")

print("\n== 控制台暴露面判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
