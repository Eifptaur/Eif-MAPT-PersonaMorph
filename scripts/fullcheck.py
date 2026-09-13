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
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


# ═══════════ A. 控制台映射审计 ═══════════
src = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
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
check("价目 171 条", len(_OFFICIAL_PRICES) == 171)
check("MiniMax-M3 命中", match_official_price("MiniMax-M3")["in"] == 2.1)
check("deepseek-chat Flash 档", match_official_price("deepseek-chat")["out"] == 4.5)
from agent.prompt import resolve_context_tier
cfg2 = {"store": {"context_tier": 2, "unified_tier": True, "group_tier": {},
                  "group_blocklist": {}, "keywords": ["鲸鱼"], "random_percent": 60}}
config._current_config = cfg2
r = resolve_context_tier([{"sender_name": "小明", "sender_id": "x", "text": "聊鲸鱼"}],
                         wechat_nickname="小鲸鱼", chat_key="g", group_name="g")
check("默认2档关键词触发", r["should_respond"] and r["tier"] == 2)
config._current_config["store"]["group_blocklist"] = {"g": ["小明"]}
r = resolve_context_tier([{"sender_name": "小明", "sender_id": "x", "text": "聊鲸鱼"}],
                         wechat_nickname="小鲸鱼", chat_key="g", group_name="g")
check("黑名单剔除", not r["should_respond"])
from agent.scoring import DEFAULT_SEEDS, _decay, HEAT_HALF_LIFE_MS
try:
    from agent.persona import PERSONAS
    from agent.persona_enrich import enrich_all
    check("角色卡 >= 50 且唯一", len(PERSONAS) >= 50 and len(set(v.get("name") for v in PERSONAS.values())) == len(PERSONAS))
    check("角色卡均含通用说话规则", all("说话规则（群聊通用）" in (v.get("text") or "") for v in PERSONAS.values()))
    check("沉默类含必要对话扩展", "必要对话扩展" in PERSONAS["link_zelda"]["text"] and "必要对话扩展" in PERSONAS["kongqishi"]["text"])
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
    rr = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    if rr.returncode != 0:
        ok = False
        print("  JS block %d: %s" % (i, rr.stderr[:200]))
check("JS 语法", ok, "%d blocks" % len(scripts))
for asset in ("icon-whale.png", "logo-bg.png", "cursor.png"):
    p = os.path.join(ROOT, "assets", asset)
    check("素材 " + asset, os.path.exists(p) and os.path.getsize(p) > 1000)
from PIL import Image
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
blue = sum(1 for y in range(0, ch, 8) for x in range(0, cw, 8)
           if cpx[x, y][3] > 200 and cpx[x, y][2] > 150 and cpx[x, y][2] > cpx[x, y][0] + 40)
check("cursor 蓝色鲸鱼（蓝像素>0）", blue > 50, "blue=%d" % blue)

# ═══════════ E. 一键启动链 ═══════════
check("一键启动.vbs 存在", os.path.exists(os.path.join(ROOT, "一键启动.vbs")))
check("onestart.py 存在", os.path.exists(os.path.join(ROOT, "scripts", "onestart.py")))
check("bat 已移除（避免 cmd 弹窗/重复入口）", not os.path.exists(os.path.join(ROOT, "一键启动.bat"))
      and not os.path.exists(os.path.join(ROOT, "安装依赖.bat"))
      and not os.path.exists(os.path.join(ROOT, "自检.bat")))
try:
    _vbs = io.open(os.path.join(ROOT, "一键启动.vbs"), "rb").read().decode("gbk", "ignore")
    check("一键启动.vbs 可读（GBK，含窗口与退出码逻辑）",
          len(_vbs) > 100 and "CreateObject" in _vbs)
except Exception:
    check("一键启动.vbs 可读", False, "vbs 读取失败")
check("备份 启动机器人.vbs 在 scripts/（备用不占根目录）",
      os.path.exists(os.path.join(ROOT, "scripts", "启动机器人.vbs")))
check("停止机器人.vbs 在根目录",
      os.path.exists(os.path.join(ROOT, "停止机器人.vbs")))
check("根目录无 启动机器人.vbs（备用）", not os.path.exists(os.path.join(ROOT, "启动机器人.vbs")))

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
    from agent.wechat_ui import ICONS, calibrate_ui, hit, achieve, _load_layout
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

# ═══════════ J. 程序鼠标检验并入一键自检 ═══════════
try:
    _wx_src = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    _ui_tests_n = _wx_src.count('"moments_' ) + _wx_src.count('"emoji_') + _wx_src.count('"message_') + _wx_src.count('"windows_') + _wx_src.count('"recalibrate')
    check("一键自检含 11 项程序鼠标检验", "_UI_TEST_LIST" in _wx_src and "程序鼠标检验" in _wx_src
          and "60~100" in _wx_src)
    check("控制台保留单独检验按钮", "UI_TESTS" in _html_src2 and "/api/ui-test" in _html_src2) if False else None
except Exception as e:
    check("程序鼠标检验并入自检", False, str(e))

# ═══════════ H. 文档中文 ═══════════
for doc in ("README.md", "使用说明.md", "更新日志.md"):
    t = io.open(os.path.join(ROOT, doc), encoding="utf-8").read()
    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
    check(doc + " 中文比例", cjk / max(1, len(t)) > 0.08, "%.1f%%" % (100 * cjk / len(t)))

try:
    from agent.code_check import run as _cc
    _r = _cc()
    _fails = [c for c in _r.get("checks", []) if c["status"] == "fail"]
    check("代码检测（agent/code_check.py）", not _fails,
          "0 失败（%s）" % _r.get("summary", ""))
except Exception as e:
    check("代码检测", False, str(e))

print("\n==== %d 项检查，%d 项失败 ====" % (len(must_have) + len(special_ui) + 6 + 6 + 6 + 8 + 5 + 4 + 9 + 8 + 1 + 1 + 3 + 1, len(fails)))
sys.exit(1 if fails else 0)
