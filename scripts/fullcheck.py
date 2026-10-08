# -*- coding: utf-8 -*-
"""Persona Morph 全面检验清单（每次修改后必跑）

覆盖：
  A. 控制台可调项映射（每个 config 键在 console_html 有 data-cfg 或专用组件）
  B. 配置真实落盘（深合并 + key 掩码保护）
  C. 后端功能（价格/档位/评分/记忆隔离/黑名单）
  D. JS 语法 + 素材完整性（图标白主体/光标蓝鲸）
  E. 一键启动链（vbs/onestart.py/备份开关）
  F. 文档中文覆盖

用法：python scripts/fullcheck.py  （配合 scripts/selftest.py 一起跑）
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 取同目录的共用件（`_known.py`）
from _known import known, report_known # noqa: E402
fails = []
TOTAL = [0] # 实际执行的检查数（原来那行是**写死的分组加总**，
                      # 加了检查项却忘改数字 ⇒ 报告里的"112 项"和实际条数会悄悄对不上）


def check(name, cond, detail=""):
    TOTAL[0] += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


# ═══════════ A. 控制台映射审计 ═══════════
src = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
mapped = set(re.findall(r'data-cfg="([^"]+)"', src))
special_ui = {
    "wechat.group_name_white_list": "群列表 chips",
    "store.group_tier": "renderGroupTierBox 按群下拉",
    "ui.cursor_image": "光标设置上传组件",
    "community.export_dir": "导出目录输入",
    "api.model_prices": "模型 API 卡 JSON textarea",
    "poke.reply_probability": "拍一拍卡",
    "poke.cooldown_seconds": "拍一拍卡",
    "poke.active_probability": "拍一拍卡",
    "poke.active_daily_limit": "拍一拍卡",
    "stats.period": "服务器卡下拉",
    "api.model": "厂商/模型下拉",
    "api.base_url": "厂商预设自动带出",
    "api.api_key": "API Key 输入",
    "ui.whale_anim.worm": "光标设置 返回动画速度（蠕动）",
    "ui.whale_anim.plane": "光标设置 返回动画速度（纸飞机）",
    "ui.whale_anim.zap": "光标设置 返回动画速度（扎入）",
    "behavior.collect_emoji.probability": "微信卡 人性化行为 收藏表情概率",
    "behavior.send_emoji.probability": "微信卡 人性化行为 回发表情概率",
    "behavior.at_member.probability": "微信卡 人性化行为 @群友概率",
    "behavior.like_moments.enabled": "调试区 点赞开关（低频）",
    "behavior.like_moments.probability": "调试区 点赞概率",
    "behavior.moments_surf.enabled": "调试区 刷朋友圈开关",
    "behavior.moments_surf.probability": "调试区 刷-概率",
    "behavior.moments_comment.probability": "调试区 评-概率",
    "behavior.moments_publish.probability": "调试区 发-概率",
    "memory.shared_groups": "记忆共享卡 群多选框",
}
must_have = [
    "ui.theme", "ui.whale_cursor", "store.unified_tier", "store.group_blocklist",
    "store.sticker_level", "memory.share_across_groups", "scoring.enabled",
    "scoring.seed_library", "scoring.online_scoring", "scoring.heat_decay",
    "proactive.enabled", "community.holyshits_upload_url", "community.feedback_upload_url",
    "community.upload_enabled", "api.use_official_price", "api.thinking",
    "store.context_tier", "store.keywords", "store.random_percent", "wechat.start_paused",
    "persona.self_nickname", "persona.participation", "send.max_per_minute",
    "send.max_per_hour", "server.port", "server.token", "ui.coord_scale", "ui.clean_overlays",
]
for k in must_have:
    check("映射 " + k, k in mapped, "" if k in mapped else "缺 data-cfg")
for k, ui in special_ui.items():
    present = k in mapped or k in src
    check("专用UI " + k + " " + ui, present)
check("主题默认 whale（选择器第一项）", 'value="whale"' in src and "鲸落（默认" in src)
check("无「整活」标签", "整活" not in src)
check("向导厂商联动", 'id="obProvider"' in src and 'id="obModel"' in src and 'obRenderModels' in src)
check("拖拽扭动", "wiggle" in src)
check("拖拽时长公式", "whale-return" in src and "flyD" in src and "zapD" in src)

# ═══════════ B. 配置落盘 ═══════════
import agent.config as config
old_cfg = config._current_config
try:
    from agent.util import mask_secret
    probe = json.loads(json.dumps(old_cfg if old_cfg else config.load_config()))
    new_part = {"ui": {"theme": "whale"}, "poke": {"reply_probability": 0.42}}
    merged = config.deep_merge(probe, new_part)
    check("深合并不丢段（ui 含旧字段）", "coord_scale" in merged["ui"])
    check("深合并新值写入", merged["ui"]["theme"] == "whale")
    check("poke 节新增", merged["poke"]["reply_probability"] == 0.42)
    masked = dict(probe["api"]); masked["api_key"] = mask_secret(probe["api"]["api_key"])
    check("key 掩码格式", "••••" in masked["api_key"])
finally:
    config._current_config = old_cfg

# ═══════════ C. 后端功能 ═══════════
from agent.llm import match_official_price, _OFFICIAL_PRICES
check("价目表有规模（≥150 条）", len(_OFFICIAL_PRICES) >= 150,
      "当前 %d 条" % len(_OFFICIAL_PRICES))
# ⛔ 原来写死 `== 172`。加一条新模型（gpt-6-astra）就假红——**数字一变就要改自检，
# 是自检在制造维护负担**。改成守两件真事实：
#   ①表里有规模（≥150，防被误删空）②每条都有 in/out 两个数字（防塞半条进去）。
_bad_rows = [k for k, v in _OFFICIAL_PRICES.items()
             if not isinstance(v.get("in"), (int, float)) or not isinstance(v.get("out"), (int, float))]
check("价目表每条都带 in/out 两个数字", not _bad_rows, "缺字段的：%s" % (_bad_rows[:5] or "无"))
check("GPT-6 旗舰（gpt-6-astra）已进价目表", "gpt-6-astra" in _OFFICIAL_PRICES)
# `join_url(base, path)` 只是 `base.rstrip + path`，**不补斜杠** ⇒
# path 必须自己带前导斜杠。我在交付面自检里写成 `join_url(base, "models")`，
# 拼出 `https://api.deepseek.com/v1models` 直接 404（还差点当成"端点不支持 /models"报上去）。
from agent.llm import join_url as _join_url
check("join_url：path 带前导斜杠才拼得对（调用方必须带）",
      _join_url("https://x/v1", "/models") == "https://x/v1/models"
      and _join_url("https://x/v1", "models") == "https://x/v1models",
      "%s | %s" % (_join_url("https://x/v1", "/models"), _join_url("https://x/v1", "models")))
_cr = io.open(os.path.join(ROOT, "scripts", "collect_report.py"), encoding="utf-8").read()
check("交付面自检拉模型列表时带了前导斜杠", 'join_url(base, "/models")' in _cr)
check("交付面自检已经接进报告主流程（第六节）", "sec_delivery" in _cr and "六、投递发送实测" in _cr)
check("MiniMax-M3 命中", match_official_price("MiniMax-M3")["in"] == 2.1)
# ⛔ 原来写死 `out == 4.5`，而价目表已按官方 **闲时价**更新为 4.0
#   ⇒ 断言跟不上就假红。这里改成守"**有官方价映射且带出处**"这件事实，具体数字由价目表自己负责
#   （数字一变就要改自检，是自检在制造维护负担）。
_ds_price = match_official_price("deepseek-flash")
check("deepseek-flash（现行正名）有官方价映射（含出处）",
      isinstance(_ds_price.get("out"), (int, float)) and _ds_price["out"] > 0
      and "官方" in str(_ds_price.get("note") or ""),
      "out=%s note=%s" % (_ds_price.get("out"), str(_ds_price.get("note"))[:40]))
# 一改全改（用户最高准则）：官方现售的两个名字必须都在价目表里，且旧名要注明"已退役"
for _cur in ("deepseek-flash", "deepseek-v4-pro"):
    check("%s 在价目表里" % _cur, _cur in _OFFICIAL_PRICES)
for _old in ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "deepseek-chat"):
    check("%s 标注为已退役旧名" % _old, "已退役" in str(_OFFICIAL_PRICES.get(_old, {}).get("note") or "")
          or "老别名" in str(_OFFICIAL_PRICES.get(_old, {}).get("note") or ""))
from agent.prompt import resolve_context_tier
cfg2 = {"store": {"context_tier": 2, "unified_tier": True, "group_tier": {},
                  "group_blocklist": {}, "keywords": ["鲸鱼"], "random_percent": 60}}
# ⛔ ①：注入之前**必须先 prime `get_config()`** —— 否则 `_current_config` 会被
#   `get_config()` 的重载覆盖，判据读到的是真配置（context_tier=1）⇒ "默认2档关键词触发" 假红。
try:
    config.get_config()
except Exception:
    pass
config._current_config = cfg2
r = resolve_context_tier([{"sender_name": "小明", "sender_id": "x", "text": "聊鲸鱼"}],
                         wechat_nickname="小鲸鱼", chat_key="g", group_name="g")
check("默认2档关键词触发", r["should_respond"] and r["tier"] == 2)
config._current_config["store"]["group_blocklist"] = {"g": ["小明"]}
r = resolve_context_tier([{"sender_name": "小明", "sender_id": "x", "text": "聊鲸鱼"}],
                         wechat_nickname="小鲸鱼", chat_key="g", group_name="g")
check("黑名单剔除", not r["should_respond"])
from agent.scoring import _decay, HEAT_HALF_LIFE_MS
try:
    from agent.persona import PERSONAS
    check("角色卡 >= 50 且唯一", len(PERSONAS) >= 50 and len(set(v.get("name") for v in PERSONAS.values())) == len(PERSONAS))
    check("角色卡均含通用说话规则", all("说话规则（群聊通用）" in (v.get("text") or "") for v in PERSONAS.values()))
    # ⛔ **不要锚具体角色 id**：原来的 `PERSONAS["link_zelda"]` 在"删掉侵权风险角色"那轮
    #    被移出角色库 ⇒ 这条断言变成 `KeyError` ⇒ 整段被 `except` 吞成一条假红"角色卡"。
    #    正确锚法是锚**意图**，不锚某个角色存在。
    # ⚠️ 也不许拿字面"沉默"当分类器：全文散搜"沉默"会命中 37 张卡（多数只是叙事里提了一句），
    #    而真正的话少型是**角色设定本身**（小骑士那种"话极少、用动作代替语言"）。
    #    所以判据锚在**角色介绍段**里是否自述"话极少/几乎不说话"，再要求它带扩展。
    def _is_mute_card(_v):
        _t = _v.get("text") or ""
        _head = _t.split("## 口头禅")[0]          # 只看角色介绍，不看台词区（台词里"沉默"太多）
        return ("话极少" in _head) or ("几乎不说话" in _head) or ("不爱说话" in _head)

    _mute = [v for v in PERSONAS.values() if _is_mute_card(v)]
    check("话少型角色含必要对话扩展（哑巴角色也要能接话）",
          bool(_mute) and all("必要对话扩展" in (v.get("text") or "") for v in _mute),
          "话少型样本 %d 个" % len(_mute))
except Exception as e:
    check("角色卡", False, str(e))

try:
    from agent.scoring import DEFAULT_SEEDS as _DS
    check("种子库 >= 200", len(_DS) >= 200, "count=%d" % len(_DS))
except Exception as e:
    check("种子库", False, str(e))
check("半衰期衰减正确", abs(_decay(2.0, 0, HEAT_HALF_LIFE_MS) - 1.0) < 1e-9)
config._current_config = old_cfg

# ═══════════ D. JS 语法 + 素材 ═══════════
scripts = re.findall(r"<script>(.*?)</script>", src, re.S)
ok = True
for i, js in enumerate(scripts):
    p = os.path.join(tempfile.gettempdir(), "_chk_%d.js" % i)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(js)
    rr = subprocess.run(["node", "--check", p], capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if rr.returncode != 0:
        ok = False
        print("  JS block %d: %s" % (i, rr.stderr[:200]))
check("JS 语法", ok, "%d blocks" % len(scripts))
for asset in ("icon-whale.png", "logo-bg.png", "cursor.png"):
    p = os.path.join(ROOT, "assets", asset)
    check("素材 " + asset, os.path.exists(p) and os.path.getsize(p) > 1000)
from PIL import Image
import logging

log = logging.getLogger("persona-morph")
iw = Image.open(os.path.join(ROOT, "assets", "icon-whale.png")).convert("RGBA")
w, h = iw.size
px = iw.load()
white = sum(1 for y in range(0, h, 8) for x in range(0, w, 8)
            if px[x, y][3] > 200 and px[x, y][0] > 240)
trans = sum(1 for y in range(0, h, 8) for x in range(0, w, 8)
            if px[x, y][3] < 20)
check("icon-whale 白主体（白像素样本>0）", white > 50, "white=%d" % white)
check("icon-whale 有透明（眼睛）", trans > 10, "trans=%d" % trans)
cur = Image.open(os.path.join(ROOT, "assets", "cursor.png")).convert("RGBA")
cw, ch = cur.size
cpx = cur.load()
# ⛔ 原来是 `range(0,w,8)` 采样 + `blue>50`。cursor.png 只有 **64×64**
#   ⇒ 每 8 像素一采只剩 64 个样本，"blue>50" 等于要求 78% 的样本是蓝的（几乎不可能），实测 blue=11
#   被误判成"光标不是蓝鲸"。改成**按比例**判（步长 2、阈值 2%），并把数字打出来便于对账。
_samp = 0
_blue = 0
for y in range(0, ch, 2):
    for x in range(0, cw, 2):
        _samp += 1
        r, g, b, a = cpx[x, y]
        if a > 200 and b > 150 and b > r + 40:
            _blue += 1
check("cursor 蓝色鲸鱼（蓝像素占比>2%）", _blue > max(1, int(_samp * 0.02)),
      "blue=%d/%d（%.1f%%）" % (_blue, _samp, 100.0 * _blue / max(1, _samp)))

# ═══════════ E. 一键启动链═══════════
# 为什么改：原来断言 `一键启动.vbs` 在**根目录**，而 收口时把三个 vbs 全挪进了 `scripts\`
# （根目录只留 `一键启动.exe`/`一键关闭.exe`）⇒ 四条断言长期假红，是自检没跟上目录收口。
check("scripts\\一键启动.vbs 存在", os.path.exists(os.path.join(ROOT, "scripts", "一键启动.vbs")))
check("scripts\\一键关闭.vbs 存在", os.path.exists(os.path.join(ROOT, "scripts", "一键关闭.vbs")))
check("onestart.py 存在", os.path.exists(os.path.join(ROOT, "scripts", "onestart.py")))
check("bat 已移除（避免 cmd 弹窗/重复入口）", not os.path.exists(os.path.join(ROOT, "一键启动.bat"))
      and not os.path.exists(os.path.join(ROOT, "安装依赖.bat"))
      and not os.path.exists(os.path.join(ROOT, "自检.bat")))
try:
    _vbs = io.open(os.path.join(ROOT, "scripts", "一键启动.vbs"), "rb").read().decode("gbk", "ignore")
    check("一键启动.vbs 可读（GBK，含窗口与退出码逻辑）",
          len(_vbs) > 100 and "CreateObject" in _vbs)
except Exception as _e:
    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
    check("一键启动.vbs 可读", False, "vbs 读取失败")
check("结算器与备用启动器都在 scripts/",
      os.path.exists(os.path.join(ROOT, "scripts", "启动机器人.vbs")))
# ⛔ 启动器名**从唯一来源取**（`agent/autostart.py` 的 `LAUNCHER_EXE`）——
#   原来这里写死 `一键启动.exe` ⇒ 改名成 `群相.exe` 之后这条就假红了。
#   （新旧名都算通过：老安装里可能还留着老名。）
try:
    import re as _re_lex
    _as_src = io.open(os.path.join(ROOT, "agent", "autostart.py"), encoding="utf-8").read()
    _launcher_names = [_re_lex.search(r'LAUNCHER_EXE\s*=\s*"([^"]+)"', _as_src).group(1)] + \
                      _re_lex.findall(r'LEGACY_LAUNCHER_EXES\s*=\s*\(([^)]*)\)', _as_src)[0].replace('"', "").split(", ")
except Exception:  # noqa: BLE001
    _launcher_names = ["群相.exe"]
check("根目录放的是 exe 版入口（用户双击的就是它们）",
      any(os.path.exists(os.path.join(ROOT, _n)) for _n in _launcher_names if _n)
      and os.path.exists(os.path.join(ROOT, "一键关闭.exe")))
check("根目录不再散落 vbs（收口到 scripts/）",
      not os.path.exists(os.path.join(ROOT, "一键启动.vbs"))
      and not os.path.exists(os.path.join(ROOT, "启动机器人.vbs")))

# ═══════════ G. 工具集 & 行为引擎 ═══════════
try:
    from agent.tools import build_tool_defs as _btd
    _tool_names = [d["name"] for d in _btd()]
    check("工具注册 >= 22", len(_tool_names) >= 22, "count=%d" % len(_tool_names))
    for _t in ("collect_emoji", "send_emoji", "view_merge_forward", "collect_message",
               "recall_message", "moments_like", "moments_publish"):
        check("工具 " + _t, _t in _tool_names)
except Exception as e:
    check("工具注册", False, str(e))

# ═══════════ I. UI 图标库 & 行为引擎 ═══════════
try:
    from agent.wechat_ui import ICONS, _load_layout
    check("图标库元素 >= 11", len(ICONS) >= 11, "count=%d" % len(ICONS))
    check("图标库含搜索/发送/收藏/朋友圈(实测确认入口)", {"search.box", "input.send", "sidebar.collection", "sidebar.moments"} <= set(ICONS))
    check("图标库标定文件存在", bool(_load_layout().get("sidebar_items")))
except Exception as e:
    check("图标库", False, str(e))
try:
    from agent.persona_hint import suggest_from_role_text
    check("角色卡行为推荐（高冷→low）",
          suggest_from_role_text("高冷安静话少")["participation"] == "low")
    check("角色卡行为推荐（卖萌→表情3）",
          suggest_from_role_text("表情包爱好者每天斗图")["sticker_level"] == 3)
except Exception as e:
    check("角色卡行为推荐", False, str(e))
try:
    from agent.behavior import decider
    check("行为引擎 multipliers", "activity" in decider.multipliers())
except Exception as e:
    check("行为引擎", False, str(e))

# ═══════════ J. 「点击测试」必须全程后台═══════════
# ⇒ 自检改成"守删除"：面板/JS/后端入口/路由若任何一个回来，这里立刻变红。
try:
    _wx_src = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    _web_src = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    _html_src = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
    check("真鼠标一键检验已删除（后端入口）",
          "ui_test_fn" not in _wx_src and "_UI_TEST_LIST" not in _wx_src and "ui_stop_fn" not in _wx_src)
    check("真鼠标一键检验已删除（路由）", "/api/ui-test" not in _web_src)
    check("真鼠标一键检验已删除（控制台面板与按钮）",
          "UI_TESTS" not in _html_src and "uiTestBtn_" not in _html_src and "程序鼠标检验" not in _html_src)
    check("点击测试文案声明全程后台（不写「接管鼠标」）",
          "点击测试" in _html_src and "接管鼠标请勿动" not in _html_src)
except Exception as e:
    check("真鼠标一键检验已删除", False, str(e))

# ═══════════ H. 文档中文 ═══════════
for doc in ("README.md", "更新日志.md"):   # 使用说明.md 已合并进 README
    t = io.open(os.path.join(ROOT, doc), encoding="utf-8").read()
    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
    check(doc + " 中文比例", cjk / max(1, len(t)) > 0.08, "%.1f%%" % (100 * cjk / len(t)))

try:
    from agent.code_check import run as _cc
    _r = _cc()
    _fails = [c for c in _r.get("checks", []) if c["status"] == "fail"]
    check("代码检测（agent/code_check.py）", not _fails,
          "0 失败（%s）" % _r.get("summary", ""))
    # ── 代码检测的「注意」项 ⇒ 进 `known` 三态（**不计失败**、但必须打印、修好自动转通过）──
    #    为什么独立成档：`agent/code_check.py` 的历史回归项里有一批**标记串已随实现演进失效**，
    #    它们长期以"注意"存在。直接算失败 ⇒ 整份 fullcheck 常年红、所有人开始忽略红灯；
    #    直接删掉那条检验 ⇒ 问题再也看不见。`known` 正是第三种：不淹红灯、也不会被忘。
    #    ⛔ 但**必须登记**：与登记表对不上就报红 —— 否则新冒出来的注意项会悄悄溜进这一档
    #      （与 253 那条"清单要双向对账"是同一件事）。
    _KNOWN_SEEDS = {
        # 原来这 2 条（`表情面板坐标(实测校准)` / `表情收藏(空区防护+防误击)`）的**锚点随实现
        # 搬家而失效**，长期以"注意"挂着。第 258 项逐条核过 ⇒ **功能都做了、只是标记串没跟着改**
        # ⇒ 按"①重定位"处置（见 `agent/code_check.py` 那两条上面的注释）⇒ 现在**已修复、登记清空**。
        # ⛔ 保留这个空表是为了守住机制（见下面那条「注意项全部已登记」）：
        #    以后新出现的"注意"项必须**在这里登记 ＋ 写清为什么它算已知**，不许悄悄溜进这一档。
    }
    _warns = [c["name"] for c in _r.get("checks", []) if c["status"] == "warn"]
    _unreg = sorted(set(_warns) - set(_KNOWN_SEEDS))
    check("代码检测的「注意」项**全部已登记**（新冒出来的不许悄悄溜过）",
          not _unreg, ("未登记：%s" % _unreg) if _unreg else "%d 条已登记" % len(_warns))
    for _n, _note in _KNOWN_SEEDS.items():
        known(check, _n, _n not in _warns, _note)
except Exception as e:
    check("代码检测", False, str(e))

# ── config.example.json 必须与 config.DEFAULT_CONFIG 同键（否则示例会静默漂） ──
# 示例文件曾经只覆盖 20 个顶层键（真实默认 35 个），缺 voice_reply / feedback.limit /
# wechat.restore_minimized 等 ⇒ 拿示例当参照的人会以为这些功能不存在。现在由脚本生成 + 这条断言守着。
def _kp(d, pre=""):
    out = set()
    if isinstance(d, dict):
        for k, v in d.items():
            p = (pre + "." + k) if pre else k
            out.add(p)
            out |= _kp(v, p)
    return out


try:
    from agent.config import DEFAULT_CONFIG as _DEF
    _ex = json.loads(io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read())
    # ⚠️ `_` 开头的键是**注释键**，不是设置：示例用它们承载"这个开关到底管什么、默认怎么算"的
    #    长说明（如 `input._note_allow_real_fallback`），后端不读、控制台也不暴露 ⇒
    #    它们不参与"同键"比较。要别的**后端口径**必须用真键（会被上面那条死键扫描管着）。
    _is_note = lambda k: any(seg.startswith("_") for seg in k.split("."))  # noqa: E731
    _dk = {k for k in _kp(_DEF) if not _is_note(k)}
    _ek = {k for k in _kp(_ex) if not _is_note(k)}
    check("config.example.json 与默认配置同键", _dk == _ek,
          "示例 %d 键 / 默认 %d 键；缺=%s 多=%s"
          % (len(_ek), len(_dk), sorted(_dk - _ek)[:5], sorted(_ek - _dk)[:5]))
    # ⚠️ 断言里**不许写出真实的用户名/邮箱字面量**——否则自检自己就成了 PII 泄露源，
    # 打包闸门当场 FATAL。
    #    用"形状"判：任何 email 形状、任何 sk- 形状的密钥。
    _ex_txt = io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
    _hit = re.search(r"[\w.+-]+@[\w-]+\.[\w.]{2,}|sk-[A-Za-z0-9]{8,}", _ex_txt)
    check("config.example.json 里没有任何真实凭据/邮箱", _hit is None,
          (_hit.group(0)[:20] if _hit else ""))
except Exception as e:
    check("config.example.json 与默认配置同键", False, str(e)[:80])

# ── 同一份东西出现两处时，光"同键"不够：**类型也得同** ─────────────────────
#    键集合一致只挡住"少写/多写一个键"，挡不住"同一个键在两边是不同类型"。
#    实例：`image_gen.ip_guard.extra_block` 默认是列表（`agent/ip_guard.py` 按列表遍历），
#    示例里写成空串 —— 键集合完全一致，上面那条断言照样绿。
#    消费方按列表遍历时，字符串会被**逐字符**遍历（"角色A" 拆成 角/色/A），
#    既拦不住想拦的词，又可能被单个汉字误命中。
#    ⇒ 键路径 → 值的映射（容器路径也算一条：整段从 dict 变成 str 同样要报）。
def _kpv(d, pre=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            p = (pre + "." + k) if pre else k
            out[p] = v
            out.update(_kpv(v, p))
    return out


def _typ(o):
    # bool 是 int 的子类，必须在 int 之前判，否则 True 会被当成数字
    if isinstance(o, bool):
        return "bool"
    if isinstance(o, int) or isinstance(o, float):
        return "数字"        # JSON 不区分 1 与 1.0，这类差异不报（报了是噪声）
    if isinstance(o, str):
        return "字符串"
    if isinstance(o, list):
        return "列表"
    if isinstance(o, dict):
        return "对象"
    if o is None:
        return "空"
    return type(o).__name__


try:
    _dv = {k: v for k, v in _kpv(_DEF).items() if not _is_note(k)}
    _ev = {k: v for k, v in _kpv(_ex).items() if not _is_note(k)}
    _bad_t = ["%s：默认=%s / 示例=%s" % (k, _typ(_dv[k]), _typ(_ev[k]))
              for k in sorted(set(_dv) & set(_ev)) if _typ(_dv[k]) != _typ(_ev[k])]
    check("config.example.json 与默认配置同类型（照抄示例不能喂错类型）",
          not _bad_t, ("%d 处：%s" % (len(_bad_t), _bad_t[:5])) if _bad_t else "0 处")
except Exception as e:
    check("config.example.json 与默认配置同类型", False, str(e)[:80])

# ── 控制台绑定的键必须都在默认配置里 ─────────────────────────────────────
#    `data-cfg` 是控制台"一行配置"的锚点。它绑的键若在默认表里不存在：
#    首装（还没有 config.json）时回填不到默认值 ⇒ 控件是空的；
#    用户一保存，又往配置里写进一个默认表没有的键。
#    ⚠️ **只查这一个方向**：反方向（默认有、页面上没绑）噪声极大 ——
#       页面上有一批行是 JS 动态生成的，机械提取提不全，设成硬断言会常态假红。
try:
    _bound = set(re.findall(r'data-cfg="([A-Za-z0-9_.]+)"', src))
    _wild = sorted(_bound - set(_kpv(_DEF)))
    check("控制台 data-cfg 绑的键都在默认配置里",
          not _wild, ("%d 个野键：%s" % (len(_wild), _wild[:5])) if _wild else "%d 个键" % len(_bound))
except Exception as e:
    check("控制台 data-cfg 绑的键都在默认配置里", False, str(e)[:80])

# ── 鲸语导航表：键必须真出现在控制台的导航标签里 ─────────────────────────
#    `NAV`（导航短表）**不随 `__WHALE_TXT__` 下发前端**（下发的只有 `DICT`），
#    前端那次遍历兜不住它 ⇒ 键在页面上找不到就是**永远换不到**的死条目。
#    ⚠️ 先自证探针：导航标签的写法一变，语料集会变空 ⇒ 所有键都会"找不到"而假红。
#       所以先断言语料有规模，语料太小直接判**探针失效**，不许拿"全找不到"当结论。
try:
    from agent.whale_text import NAV as _NAV
    _lb = set(x.strip() for x in re.findall(r'<span class="lb">([^<]*)</span>', src))
    if len(_lb) < 20:
        check("鲸语导航表探针有效（导航语料取到规模）", False,
              "只取到 %d 个导航标签 ⇒ 探针失效，先修语料正则再判" % len(_lb))
    else:
        _dead = sorted(k for k in _NAV if k not in _lb)
        check("鲸语导航表没有死条目（语料 %d 个导航标签）" % len(_lb),
              not _dead, ("死条目：%s" % _dead[:5]) if _dead else "0 条")
except Exception as e:
    check("鲸语导航表没有死条目", False, str(e)[:80])

# ── 用量三分口径：只记"总 token"看不出钱花在哪，缓存/未命中/输出单价差 30~90 倍 ──
try:
    _st = io.open(os.path.join(ROOT, "agent", "stats.py"), encoding="utf-8").read()
    _pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    check("用量账本记三分（fresh / cached / output）",
          all(k in _st for k in ("fresh_tokens", "cached_tokens", "output_tokens"))
          and "fresh=_fresh" in _pm and "cached=_cached" in _pm and "output=_output" in _pm)
    check("三分口径有注释说明为什么要它（防后人删）",
          "差 30~90 倍" in _st or "30~90 倍" in _pm)
except Exception as e:
    check("用量账本记三分（fresh / cached / output）", False, str(e)[:60])

# ── 自检自身的可执行性：自检崩掉和自检红掉是两件事 ──
# 产品链走 `py -X utf8 onestart.py`（launcher.cs:285）所以产品侧没事；但自检脚本没这层——
# 一旦输出被重定向（`> out.txt` / 管道），Python 退回 locale 编码（本机 GBK），
# `print` 直接 UnicodeEncodeError，**整条自检崩在第一个 PASS 上**（rc=1、只跑半截）。
# 实测 5 条脚本（chat_ocr / clipboard / image_filter / image_lib / log_housekeeping）中招，
# 80 条断言从来没被真正跑到过 ⇒ 这里钉住：凡打印 ✔/✘ 的 selftest 必须有 UTF-8 垫片。
try:
    _sdir = os.path.join(ROOT, "scripts")
    _risky, _noshim = [], []
    for _n in sorted(os.listdir(_sdir)):
        if "selftest" not in _n or not _n.endswith(".py"):
            continue
        _t = io.open(os.path.join(_sdir, _n), encoding="utf-8").read()
        if "\u2714" in _t or "\u2718" in _t:
            _risky.append(_n)
            # ⛔ ③：垫片有两种**等价**写法 —— ① 直接 `sys.stdout.reconfigure(...)`；
            #   ② `for _s in (sys.stdout, sys.stderr): _s.reconfigure(...)`（本项目多条用这种）。
            #   老判据只认 ① ⇒ 把 ② 误报成"缺垫片"（假红：那两条实测在 GBK 子进程里不崩）。
            _shim = ("sys.stdout.reconfigure" in _t
                     or ("reconfigure(" in _t and re.search(r"for\s+\w+\s+in\s*\(\s*sys\.stdout", _t)))
            if not _shim:
                _noshim.append(_n)
    check("打印 ✔/✘ 的判据都带 UTF-8 垫片（重定向下不崩）", not _noshim,
          "缺垫片=%s（共 %d 条会打印 ✔/✘）" % (_noshim, len(_risky)))
    _rn = os.path.join(_sdir, "run_all_selftests.py")
    check("有「一把跑全部判据」的入口（71 条手工跑 = 数字没法复核）",
          os.path.exists(_rn) and "run_all_selftests" in io.open(_rn, encoding="utf-8").read()
          and "selftest" in io.open(_rn, encoding="utf-8").read())
except Exception as e:
    check("打印 ✔/✘ 的判据都带 UTF-8 垫片（重定向下不崩）", False, str(e)[:80])

# ── 第 189 项常驻闸：「写了没接线」的候选必须**全部已定档** ──
# 起因：项目里有 84 个函数"写好了却没人调用"。它们分三类，**必须逐项拍板**：
#   ① 该接上的（真缺口）② 该删的（死代码）③ 有意保留的（判据读口 / 反例存证 / 等前置项）。
# 只列清单不定档 ⇒ 下一轮还会有人重新数一遍、重新吵一遍。所以把"定档率"钉成常驻断言：
#   只要 `dead_code_triage.py` 里再冒出 `wire`（未定档），这条就红。
# ⚠️ 为什么在这里跑子进程而不是 import：triage 脚本是**独立入口**（`__main__` 才出结论），
#    直接 import 拿不到它的结论文本；而且它自己会扫全库，跑一次约 1~2 秒，可接受。
try:
    _tri = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "dead_code_triage.py")],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=180, check=False,
        creationflags=(0x08000000 if os.name == "nt" else 0),  # 不许闪控制台窗
    )
    # ⛔ 解码**不能**依赖 locale：重定向/管道下本机是 GBK，triage 输出含 ✔/─ 等宽字符
    #    会直接 UnicodeDecodeError（和上面那条 UTF-8 垫片是同一类坑）。
    _tout = _tri.stdout.decode("utf-8", "replace")
    _m_done = re.search(r"(\d+)/(\d+)\s*已定档", _tout)
    _m_wire = re.search(r"剩\s*(\d+)\s*个\s*'wire'", _tout)
    _done, _total = (int(_m_done.group(1)), int(_m_done.group(2))) if _m_done else (-1, -1)
    _wire = int(_m_wire.group(1)) if _m_wire else -1
    check("第 189 项：'写了没接线' 的候选**全部已定档**"
          "（接上／删掉／有意保留，不许只列不清）",
          _m_done is not None and _m_wire is not None
          and _wire == 0 and _done == _total and _total > 0,
          "已定档 %d/%d，剩 %s 个 'wire'%s"
          % (_done, _total, _wire,
             "" if _tri.returncode == 0 else "（triage 退出码 %d）" % _tri.returncode))
except Exception as e:
    check("第 189 项：'写了没接线' 的候选**全部已定档**"
          "（接上／删掉／有意保留，不许只列不清）", False, str(e)[:80])

# ═══════════ G. 注释卫生：注释/文档串里不许出现"人说的话 / 谁定的 / 日期 / 自述过程" ═══════════
# 为什么要有这条闸门：注释会随仓库、日志、截图一起流出去 ⇒ 把"谁说的话 / 谁拍板的 / 哪天改的"
# 写进注释＝把用户写进代码。这类内容只能出现在报告里，不能出现在代码里。
# ⛔ 扫描范围**只含注释 token 与文档串**：**不碰普通字符串字面量** ——
#    字符串是**代码的数据**（标点字符集、文案、夹具），往里伸规则会把功能改坏。
def _comment_offenders(root: str) -> list:
    import ast as _ast
    import io as _io
    import tokenize as _tk
    pats = [
        (re.compile(r"用户\s*(原话|拍板|指出|点名|明令|定死|要求|指令|自己填|手写|授权|确认|肉眼)"), "谁定的/谁说的"),
        (re.compile(r"用户\s*[：:，,]?\s*[「“\"'][^」”\"'\n]{0,120}[」”\"']"), "引述用户的话"),
        (re.compile(r"20\d\d-\d\d-\d\d"), "日期"),
        (re.compile(r"我(犯过|踩过|漏了?|曾|原以为|差点|以为)"), "自述开发过程"),
        (re.compile(r"第\s*\d+\s*(批|轮)[^0-9]"), "批次/轮次"),
    ]
    out = []
    for _d in ("agent", "ui_qt", "scripts"):
        _base = os.path.join(root, _d)
        for _fn in sorted(os.listdir(_base)):
            if not _fn.endswith(".py"):
                continue
            _p = os.path.join(_base, _fn)
            try:
                _src = _io.open(_p, encoding="utf-8").read()
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            _lines = _src.splitlines()
            # ① 文档串的行范围
            _doc = set()
            try:
                for _n in _ast.walk(_ast.parse(_src)):
                    if isinstance(_n, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                                      _ast.AsyncFunctionDef)):
                        _b = getattr(_n, "body", None) or []
                        if _b and isinstance(_b[0], _ast.Expr) \
                                and isinstance(_b[0].value, _ast.Constant) \
                                and isinstance(_b[0].value.value, str):
                            _e = _b[0].value
                            _doc.update(range(int(_e.lineno),
                                              int(getattr(_e, "end_lineno", _e.lineno)) + 1))
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass
            # ② 注释的位置
            _cmt = {}
            try:
                for _t in _tk.generate_tokens(_io.StringIO(_src).readline):
                    if _t.type == _tk.COMMENT:
                        _cmt.setdefault(_t.start[0], []).append(_t.start[1])
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass
            for _i, _ln in enumerate(_lines, 1):
                _text = None
                if _i in _cmt:
                    _text = " ".join(_ln[_c:] for _c in _cmt[_i])
                elif _i in _doc:
                    _text = _ln
                if not _text:
                    continue
                for _rx, _why in pats:
                    if _rx.search(_text):
                        out.append("%s/%s:%d[%s]" % (_d, _fn, _i, _why))
                        break
    return out


try:
    _off = _comment_offenders(ROOT)
    check("注释卫生：注释/文档串里**没有**「用户原话 / 谁定的 / 日期 / 自述过程」"
          "（这些东西只能进报告，不许进代码）",
          not _off, ("%d 处：" % len(_off)) + "、".join(_off[:6]) if _off else "0 处")
except Exception as e:
    check("注释卫生：注释/文档串里**没有**「用户原话 / 谁定的 / 日期 / 自述过程」", False, str(e)[:80])

# ═══════════ G2. 真实信息闸门：会发出去的那批里不许有"本机用户名 / 真实 wxid" ═══════════
# 为什么要有它：包是要发出去的。**本机用户名**（家目录路径里那一段）与**真实微信账号**
# 一旦进包，就是把开发者的机器与账号一起发出去。**合成占位值不算命中**（否则会把测试改坏）。
def _real_id_offenders(root: str) -> list:
    pats = [
        (re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!<用户>|<你>|<name>)[A-Za-z0-9._-]{2,}"), "本机用户名"),
        (re.compile(r"wxid_(?!sample|judge|other|abc|some|self|test|demo|FROM_STATUS)[a-z0-9]{5,}"),
         "真实 wxid"),
    ]
    out = []
    for _d in ("agent", "ui_qt", "assets", "scripts", "launcher-src", "whale-widget", "lib"):
        _base = os.path.join(root, _d)
        if not os.path.isdir(_base):
            continue
        for _dp, _dns, _fns in os.walk(_base):
            _dns[:] = [x for x in _dns if x not in (".git", "__pycache__", "node_modules")]
            for _fn in _fns:
                if not _fn.lower().endswith((".py", ".js", ".html", ".json", ".md", ".txt",
                                             ".ps1", ".cmd", ".vbs", ".bat", ".css")):
                    continue
                _p = os.path.join(_dp, _fn)
                try:
                    if os.path.getsize(_p) > 4_000_000:
                        continue
                    _s = io.open(_p, encoding="utf-8", errors="ignore").read()
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    continue
                for _rx, _why in pats:
                    if _rx.search(_s):
                        out.append("%s[%s]" % (_p, _why))
                        break
    return out


def _log_use_without_def(root, marker=None):
    """→ 用了 `log.` 却没定义 `log` 的 .py（相对路径）。

    `marker` 给了就**只查含该标记**的文件（零误报的精确口径）；不给则给出**全部**可疑文件
    （成因可能不同：局部变量、别名导入、桩对象…… ⇒ 只列出来给人看，不直接判失败）。

    ⛔ 为什么要有这条：往宽 `except` 里插 `log.debug(...)` 时，若该文件**没有** `log`，
    那行会在**出错路径**抛 `NameError` —— 而它正好包在同一段宽捕里 ⇒ **被吞掉** ⇒
    表现是"什么都没发生"（**装了留栈、实际没留，比不修更坏**）。
    这类错**跑判据不一定能发现**（那条路径没被走到）⇒ 只能靠静态检查。
    """
    out = []
    for sub in ("agent", "scripts", "ui_qt"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dp, dn, fns in os.walk(base):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                # ⛔ **自排除**：本检查器自己就"提到"标记串（常量 `_MARK_LOGGED`）⇒ 会自匹配。
                #    （`structure_guard` 也是这么做的：豁免面就自己一个、且不许扩大。）
                if os.path.relpath(p, root).replace("\\", "/") == "scripts/fullcheck.py":
                    continue
                try:
                    s = io.open(p, encoding="utf-8", errors="ignore").read()
                except Exception: # noqa: BLE001
                    continue
                if not re.search(r"(?<![\w.])log\.\w+\(", s):
                    continue
                if marker is not None and marker not in s:
                    continue
                if re.search(r"^\s*log\s*=\s*", s, re.M):
                    continue
                out.append(os.path.relpath(p, root).replace("\\", "/"))
    return out


try:
    _real = _real_id_offenders(ROOT)
    check("真实信息闸门：会发出去的那批里**没有**本机用户名 / 真实微信账号"
          "（合成占位值不算命中）",
          not _real, ("%d 处：" % len(_real)) + "、".join(_real[:5]) if _real else "0 处")
except Exception as e:
    check("真实信息闸门：会发出去的那批里**没有**本机用户名 / 真实微信账号", False, str(e)[:80])

# ── 裸 `\r` 闸：Python 把**裸 `\r` 也当换行** ⇒ 一旦出现，`ast` 行号就与"按 `\n` 切"的索引错位 ⇒
#    所有按行号定位的工具（`_srcslice.func_src`、判据里的行号锚）**全部切错/错位**，
#    而**文件照常能跑** ⇒ 属"最难查"的一类。（实测来源：批量补留栈时给 CRLF 行重复追加了 `\r`，
#    22 个文件、170 处；`_srcslice` 与 `except_audit` 的行号当场开始对不上。）
def _lone_cr_files(root):
    out = []
    for sub in ("agent", "scripts", "ui_qt"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dp, dn, fns in os.walk(base):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                try:
                    b = io.open(p, "rb").read()
                except Exception: # noqa: BLE001
                    continue
                if b.count(b"\r") != b.count(b"\r\n"):
                    out.append("%s(裸CR=%d)" % (os.path.relpath(p, root).replace("\\", "/"),
                                                b.count(b"\r") - b.count(b"\r\n")))
    return out


try:
    _lone = _lone_cr_files(ROOT)
    check("源码里没有**裸 `\\r`**（Python 把它也当换行 ⇒ ast 行号与按 `\\n` 的索引错位、按行号定位的工具全切错）",
          not _lone, ("%d 个：" % len(_lone)) + "、".join(_lone[:5]) if _lone else "0 个")
except Exception as e:
    check("源码里没有**裸 `\\r`**（Python 把它也当换行 ⇒ ast 行号与按 `\\n` 的索引错位、按行号定位的工具全切错）",
          False, str(e)[:80])

# ── `log` 的定义必须**存在**且**在使用之前**（AST 口径，不看注释/文档串）──
#    为什么：① 没有 `log` ⇒ 用到就 `NameError`，而它多半坐在 `except` 里 ⇒ **被宽捕吞掉**
#    （实测抓到两个真 bug：`agent/log_housekeeping.py` 的 `if log` 本身就炸、
#      `ui_qt/panels_custom.py` 的 `log.debug` 挂在 except 里静默失效）；
#    ② **有定义但位置太晚**也没用 —— 判据脚本是"边执行边走"的，
#    定义落在顶层代码之后 ⇒ 前面那行照样 `NameError`（实测 `scripts/verifiers_selftest.py` 第 300 行）。
def _log_def_offenders(root):
    """→ 「用了 `log.` 但没定义 `log`，或定义晚于首次使用」的文件（AST 口径）。

    `log` 的"定义"认三种：模块级 `log = ...` · `import logging as log` · `from x import log`。
    """
    out = []
    for sub in ("agent", "scripts", "ui_qt"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dp, dn, fns in os.walk(base):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                if os.path.relpath(p, root).replace("\\", "/") == "scripts/fullcheck.py":
                    continue                     # 自排除（本检查器自己会提到 `log.`）
                try:
                    tree = ast.parse(io.open(p, encoding="utf-8", errors="ignore").read())
                except Exception: # noqa: BLE001
                    continue
                _def_ln = None
                for n in tree.body:
                    if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "log":
                        _def_ln = n.lineno
                    elif isinstance(n, (ast.Import, ast.ImportFrom)) and \
                            any((a.asname or a.name) == "log" for a in n.names):
                        _def_ln = n.lineno
                _uses = sorted({n.lineno for n in ast.walk(tree)
                                if isinstance(n, ast.Attribute)
                                and isinstance(n.value, ast.Name) and n.value.id == "log"})
                if _uses and (_def_ln is None or _def_ln > min(_uses)):
                    out.append("%s(log@%s 首用@%d)" % (os.path.relpath(p, root).replace("\\", "/"),
                                                       _def_ln, min(_uses)))
    return out


try:
    _log_bad = _log_def_offenders(ROOT)
    check("`log` 的定义都在**使用之前**（AST 口径：没有定义、或定义落在使用之后，都会在运行期抛 `NameError` ⇒ 被宽捕吞掉）",
          not _log_bad, ("%d 个：" % len(_log_bad)) + "、".join(_log_bad[:5]) if _log_bad else "0 个")
except Exception as e:
    check("`log` 的定义都在**使用之前**（AST 口径：没有定义、或定义落在使用之后，都会在运行期抛 `NameError` ⇒ 被宽捕吞掉）",
          False, str(e)[:80])

# ── 「插了日志却没有 logger」闸（精确口径：只看含本批留栈标记的文件）──
_MARK_LOGGED = "宽捕处出错（留栈"
try:
    _bad_log = _log_use_without_def(ROOT, marker=_MARK_LOGGED)
    check("带留栈日志的文件都定义了自己的 `log`（否则出错路径抛 NameError、还被宽捕吞掉）",
          not _bad_log, ("%d 个：" % len(_bad_log)) + "、".join(_bad_log[:5]) if _bad_log else "0 个")
except Exception as e:
    check("带留栈日志的文件都定义了自己的 `log`（否则出错路径抛 NameError、还被宽捕吞掉）", False, str(e)[:80])

# 软口径：其余"用了 `log.` 却没定义"的文件 —— 只列出来（成因可能不同，要人看，不计失败）
try:
    _soft = [x for x in _log_use_without_def(ROOT) if x not in _bad_log]
    if _soft:
        print("  ℹ️ 另有 %d 个文件用了 `log.` 却没看到 `log = ...`（未判失败，供人工过目）："
              % len(_soft))
        for _x in _soft[:8]:
            print("      - " + _x)
except Exception as e: # noqa: BLE001
    print("  ℹ️ 软口径扫描跳过：%s" % str(e)[:60])

_n_known = report_known(print)   # 「已知未修」单列一档：不计失败，但必须看得见（详见 `_known.py`）
print("\n==== %d 项检查，%d 项失败 ====" % (TOTAL[0], len(fails)))
if _n_known:
    print("另有「已知未修」%d 条（不计失败，明细见上一节）" % _n_known)
# 失败时把**名字**打出来。以前只打数量，而这份检查器的输出在重定向/管道下
# 会被截断（只留最后几行）⇒ 数字说"1 项失败"却找不到是哪一项，白查一轮。
if fails:
    print("失败项：")
    for _n in fails:
        print("  - " + str(_n))
sys.exit(1 if fails else 0)
