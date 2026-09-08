# -*- coding: utf-8 -*-
"""wx-agent 自检脚本：验证 Python 环境、依赖、项目模块、纯逻辑与配置。

不需要微信已登录即可运行（微信接入部分只做"能不能导入"级别的检查）。
运行：python scripts/selftest.py   （或双击 一键启动.vbs，会自动执行本自检）
退出码：0 = 全部通过；1 = 有失败项
"""
from __future__ import annotations

import importlib
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
sys.stderr.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stderr, "reconfigure") else None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []   # (name, ok, detail)
warns = []     # 提示项（配置待办，不阻断启动）

# 自检说明：依赖/环境/模块是"能不能用"的硬项；API Key 等配置项缺失只提示不阻断
# （一键启动会继续拉起机器人并打开控制台，首次向导填写密钥）。


def section(title: str):
    print("\n==== %s ====" % title)


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, bool(ok), detail))
    mark = "OK  " if ok else "FAIL"
    print("%s  %s%s" % (mark, name, ("  -> " + detail) if detail else ""))


def warn(name: str, detail: str = ""):
    """配置提示项：计入总数但不作为失败（不阻断一键启动）。"""
    results.append((name, True, detail))
    warns.append(name)
    print("WARN  %s%s" % (name, ("  -> " + detail) if detail else ""))


# ── 1. Python 版本 ────────────────────────────────────────────────────────
section("1. Python 环境")
py_ver = sys.version_info
check("Python 版本 >= 3.10", py_ver >= (3, 10), "%d.%d.%d" % py_ver[:3])
check("Windows 平台", sys.platform == "win32", sys.platform)

# ── 2. 第三方依赖 ─────────────────────────────────────────────────────────
section("2. 第三方依赖")
THIRD_PARTY = [
    ("requests", "requests"),
    ("Pillow", "PIL"),
    ("cryptography", "cryptography"),
    ("zstandard", "zstandard"),
    ("uiautomation", "uiautomation"),
    ("comtypes", "comtypes"),
    ("pywin32", "win32api"),
    ("pyperclip", "pyperclip"),
    ("psutil", "psutil"),
    ("colorama", "colorama"),
    ("winsdk", "winsdk"),
    ("imageio-ffmpeg", "imageio_ffmpeg"),
    ("wechatauto-replica", "wechatauto"),
]
for label, mod in THIRD_PARTY:
    try:
        importlib.import_module(mod)
        check("依赖 " + label, True)
    except Exception as e:
        check("依赖 " + label, False, "%s" % type(e).__name__)

# ── 3. 项目模块 ───────────────────────────────────────────────────────────
try:
    from agent.wechat import dep_check as _dc
    _dep_ok = True
except Exception as _e:
    _dc = None
    _dep_ok = False
if _dc is not None:
    _rows, _all = _dc()
    _bad = [r[0] + " 已装" + (r[1] or "-") for r in _rows if not r[3]]
    check("关键依赖版本匹配(14项)", _all, ("全部满足" if _all else "不满足: " + ", ".join(_bad[:3])))
else:
    check("关键依赖版本匹配(14项)", False, "模块导入失败")
try:
    from agent.wechat import dep_check as _dc
    _rows, _all = _dc()
    _bad = [r[0] + " 已装" + (r[1] or "-") for r in _rows if not r[3]]
    check("关键依赖版本匹配(14项)", _all, ("全部满足" if _all else "不满足: " + ", ".join(_bad[:3])))
except Exception as _e:
    check("关键依赖版本匹配(14项)", False, str(_e)[:80])
section("3. 项目模块导入")
try:
    from agent.wechat import wechat_version_info as _wvi
    module_ok = True
    module_detail = ""
except Exception as e:
    _wvi = None
    module_ok = False
    module_detail = str(e)
check("agent.wechat（含版本检测）", module_ok, module_detail)
if _wvi is not None:
    _wi = _wvi()
    detail = _wi.get("detail") or ""
    check("微信版本检测", bool(_wi.get("found")) and bool(_wi.get("supported")),
          "微信 %s · 适配层 %s" % (_wi.get("version") or "未检测到", _wi.get("adapter") or "-"))
else:
    check("微信版本检测", False, "module import 失败")
PROJECT_MODULES = [
    "agent.config", "agent.util", "agent.llm", "agent.web_search",
    "agent.safe_fetch", "agent.store", "agent.memory", "agent.sender",
    "agent.persona", "agent.prompt", "agent.tools", "agent.wechat",
    "agent.webui", "agent.whale", "agent.ui_adapt", "agent.scoring",
    "agent.behavior", "agent.wechat_ui", "wx_agent",
]
for mod in PROJECT_MODULES:
    try:
        importlib.import_module(mod)
        check("导入 " + mod, True)
    except Exception as e:
        check("导入 " + mod, False, "%s: %s" % (type(e).__name__, e))

# ── 4. 纯逻辑冒烟 ─────────────────────────────────────────────────────────
section("4. 纯逻辑冒烟")
try:
    from agent.util import md_to_plain, split_for_wx, slider_to_tier, normalize_message_list
    from agent.config import get_config
    from agent.prompt import resolve_context_tier, is_at_me, build_system_prompt, build_user_prompt
    from agent.store import ChatStore
    from agent.memory import MemoryStore

    check("md→纯文本", md_to_plain("**加粗**和`code`") == "加粗和code")
    check("超长切分", len(split_for_wx("中" * 2500, 2000)) == 2)
    check("滑条→档位", slider_to_tier(55)["tier"] == 3 and slider_to_tier(95)["tier"] == 4)
    check("消息列表归一", normalize_message_list('["a","b"]') == ["a", "b"])
    check("@ 识别", is_at_me("@群deepseek 你好", self_nickname="群deepseek", bot_name="小鲸鱼"))
    check("@ 识别(负例)", not is_at_me("今天天气不错", self_nickname="群deepseek", bot_name="小鲸鱼"))

    cfg = get_config()
    cfg["store"]["context_tier"] = 1
    r = resolve_context_tier([{"id": 1, "mid": 1, "ts": 1, "sender_id": "x", "sender_name": "u", "text": "闲聊", "self": False, "media": [], "reply": None}],
                             "群deepseek", "小鲸鱼", "self")
    check("1档未艾特不响应", not r["should_respond"])
    r = resolve_context_tier([{"id": 1, "mid": 1, "ts": 1, "sender_id": "x", "sender_name": "u", "text": "@群deepseek 在吗", "self": False, "media": [], "reply": None}],
                             "群deepseek", "小鲸鱼", "self")
    check("1档艾特响应", r["should_respond"] and r["tier"] == 1)

    st = ChatStore(0)
    st.append_incoming("group:g1", 101, 1000, "wxid_a", "张三", "你好")
    check("存档未读计数", st.unread_count("group:g1") == 1)
    check("存档取走未读", len(st.drain_unread("group:g1")) == 1 and st.unread_count("group:g1") == 0)

    mem = MemoryStore()
    mem.append("group:g1", "memberImpression", "爱玩梗", {"userId": "wxid_a", "target": "张三"})
    check("记忆写入", len(mem.query("group:g1")["memberImpression"]) == 1)

    sysp = build_system_prompt()
    check("系统提示含安全规则", "安全规则" in sysp and "send_message" in sysp)
    userp = build_user_prompt({
        "chat_key": "group:g1", "kind": "group", "chat_id": "g1", "chat_name": "测试群",
        "trigger_entries": [{"id": 1, "mid": 1, "ts": 1, "sender_id": "wxid_a", "sender_name": "张三", "text": "@群deepseek 你好", "self": False, "media": [], "reply": None}],
        "store": st, "memory": mem, "self_nickname": "群deepseek", "self_last_message_at": 0,
        "last_message_at": 1, "recent_count": 1, "run_seq": 1, "more_unread_during_run": False,
        "context_limit": 20, "session": {"past_state_count": 0},
    })
    check("用户提示含本次唤醒", "本次唤醒" in userp and "测试群" in userp)
except Exception as e:
    check("纯逻辑冒烟", False, "%s: %s" % (type(e).__name__, e))

# ── 5. 配置校验 ───────────────────────────────────────────────────────────
section("5. 配置校验")
try:
    from agent.config import load_config
    cfg = load_config()
    api = cfg.get("api", {})
    if api.get("base_url"):
        check("api.base_url 已配置", True, api["base_url"])
    else:
        check("api.base_url 已配置", False, "请填 config.json 的 api.base_url")
    key = str(api.get("api_key") or "")
    if key.startswith("sk-") and "在这里填" not in key:
        check("api.api_key 已配置", True, key[:6] + "…")
    else:
        warn("api.api_key 待配置", "打开控制台「模型 API」页填写真实密钥（当前为占位符）")
    if api.get("model"):
        check("api.model 已配置", True, api["model"])
    else:
        check("api.model 已配置", False, "请填模型 id")
    # 视觉模型提示（仅提示，不强制失败）
    model = str(api.get("model") or "").lower()
    is_vision = "vision" in model or "vl" in model or "omni" in model or "4o" in model or "gemini" in model
    if api.get("vision", True) is not False and not is_vision:
        check("api.model 疑似非视觉模型", True, "提示：当前模型名不含 vision，识图可能不可用，可换 deepseek-v4-flash-vision-exp")
    else:
        check("api.model 视觉能力", True, api["model"])
except Exception as e:
    check("配置校验", False, "%s: %s" % (type(e).__name__, e))

# ── 汇总 ──────────────────────────────────────────────────────────────────
section("汇总")
fails = [r for r in results if not r[1]]
print("\n共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(fails), len(fails)))
if warns:
    print("提示：%d 项配置待办（不影响启动，打开控制台处理即可）：%s"
          % (len(warns), "、".join(warns)))
if fails:
    print("失败项：")
    for name, _, detail in fails:
        print("  - %s  %s" % (name, detail))
    print("\n提示：依赖缺失请双击 一键启动.vbs（自动安装）；配置缺失请编辑 config.json。")
else:
    print("全部通过 ✔")
print("")
sys.exit(1 if fails else 0)
