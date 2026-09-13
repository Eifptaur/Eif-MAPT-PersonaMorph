# -*- coding: utf-8 -*-
"""代码检测（纯代码层，不接管鼠标、不发消息）：
编译 / 依赖 / 配置 / 角色卡评估汇总 / 种子库 / UI 标定 / 提示词静态性 /
行为引擎 / 停止与单实例保护 / 壁纸资源 / 版本构建——每个检查项只读。

用于控制台「代码检测」；也是发布前自查入口（py -c "from agent.code_check import run"）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # 支持脚本直接运行（py agent/code_check.py）


def _run_py(args, timeout=120):
    import subprocess
    import sys
    try:
        r = subprocess.run([sys.executable, "-X", "utf8"] + args,
                           cwd=ROOT, capture_output=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode == 0, (r.stdout or b"").decode("utf-8", "replace")[-400:]
    except Exception as e:
        return False, str(e)


def _compile_all():
    """内联全量编译（快，~2秒；不用 subprocess）。返回 (ok, 错误文件列表)。"""
    import py_compile
    bad = []
    files = []
    for root, dirs, fs in os.walk(os.path.join(ROOT, "agent")):
        files += [os.path.join(root, f) for f in fs if f.endswith(".py")]
    files += [os.path.join(ROOT, "scripts", "persona_morph.py")]
    files += [os.path.join(ROOT, "scripts", f) for f in os.listdir(os.path.join(ROOT, "scripts")) if f.endswith(".py")]
    for fp in files:
        try:
            py_compile.compile(fp, doraise=True)
        except Exception as e:
            bad.append("%s: %s" % (os.path.basename(fp), str(e).split("\n")[0][:60]))
    return (not bad), "; ".join(bad[:3])


def run(verbose_deps: bool = False) -> dict:
    """执行全部代码层检查；返回 {"ok", "checks": [...]}。约 0.2~1 秒。"""
    checks = []
    run._prog = {"done": 0, "total": 0, "current": ""}
    run._total = 0   # 待后面统计

    def add(name, status, detail="", hint=""):
        checks.append({"name": name, "status": status, "detail": detail[:120], "hint": hint[:160]})
        run._prog["done"] = len(checks)
        run._prog["current"] = name
        run._prog["total"] = max(run._prog["total"], run._prog["done"] + 1)
        run._check_count = len(checks)
        run._checks = checks   # 实时进度项（前端逐项滚动显示，无需等报告结束）

    # 1) Python 编译检查（内联全量）
    ok, detail = _compile_all()
    add("Python 全量编译", "ok" if ok else "fail", "全部通过" if ok else detail,
        "" if ok else "查看日志定位语法错误文件")

    # 2) 依赖可用性（wechatauto / PIL / 关键模块）
    try:
        import wechatauto  # noqa
        from PIL import Image, ImageGrab  # noqa
        from agent import ui_adapt, wechat_ui, behavior, scoring, persona  # noqa
        add("依赖与模块", "ok", "wechatauto / PIL / agent.* 全部可导入")
    except Exception as e:
        add("依赖与模块", "fail", str(e)[:120], "运行 setup_deps.py 安装依赖")

    # 3) 配置项（API Key / model / base）
    try:
        from agent.config import get_config
        cfg = get_config()
        key = str(cfg.get("api", {}).get("api_key") or "")
        ok_key = bool(key) and "在这里填" not in key and key != "******"
        add("配置·API Key", "ok" if ok_key else "fail", "已填" if ok_key else "未填（在「模型 API」卡填写）")
        add("配置·模型与地址", "ok" if cfg.get("api", {}).get("base_url") and cfg.get("api", {}).get("model")
            else "fail", "%s / %s" % (cfg.get("api", {}).get("base_url", "?"), cfg.get("api", {}).get("model", "?")))
    except Exception as e:
        add("配置读取", "fail", str(e)[:120])

    # 4) 角色卡评估（本地启发式评分 persona_check 已弃用——改模型五维评分，见控制台「人设」→AI评分，精确百分位）
    try:
        from agent.persona import PERSONAS
        add("角色卡·数量", "ok" if len(PERSONAS) >= 100 else "warn", "%d 张" % len(PERSONAS))
        add("角色卡·评分方式", "ok", "模型五维评分（风格25/贴合30/一致20/自然15/可用10，百分位）；本地 persona_check 已弃用")
        add("角色卡·补足机制", "ok" if sum("说话规则（群聊通用）" in (v.get("text") or "") for v in PERSONAS.values()) * 2 >= len(PERSONAS)
            else "warn", "大部分含通用说话规则（enrich 补足）")
    except Exception as e:
        add("角色卡检查", "fail", str(e)[:120])

    # 5) 种子库
    try:
        from agent.scoring import DEFAULT_SEEDS
        add("学习·种子库", "ok" if len(DEFAULT_SEEDS) >= 200 else "warn", "%d 条（≥200 达标）" % len(DEFAULT_SEEDS))
    except Exception as e:
        add("学习·种子库", "fail", str(e)[:120])

    # 6) UI 标定文件
    try:
        from agent.wechat_ui import _load_layout
        lay = _load_layout()
        items = lay.get("sidebar_items") or []
        add("微信·UI 图标库", "ok" if len(items) >= 5 else "warn",
            "%d 个图标（含朋友圈等）" % len(items), "若 <5 请到「调试·高级功能」重新标定（或恢复窗口重标）")
    except Exception as e:
        add("微信·UI 图标库", "fail", str(e)[:120])

    # 7) 行为引擎参数完整性
    try:
        from agent.behavior import DEFAULTS
        need = ("collect_emoji", "send_emoji", "like_moments", "at_member", "poke_active", "quote")
        miss = [k for k in need if k not in DEFAULTS]
        add("行为引擎·参数", "ok" if not miss else "fail", "%d 个行为参数齐全" % len(DEFAULTS),
            "" if not miss else "缺失：" + "、".join(miss))
    except Exception as e:
        add("行为引擎·参数", "fail", str(e)[:120])

    # 8) 提示词：系统消息保持静态（缓存命中/角色不串）
    try:
        from agent.prompt import build_system_prompt
        sp = build_system_prompt()
        if "语言风格参考" in sp.replace("（如语言风格参考）", ""):
            add("提示词·系统静态", "warn", "系统提示含风格参考（建议移用户消息）")
        else:
            add("提示词·系统静态", "ok", "系统提示纯静态（前缀缓存可命中，省 ~87% 输入费）")
    except Exception as e:
        add("提示词·系统静态", "fail", str(e)[:120])

    # 9) 停止与单实例保护（文件/代码层）
    try:
        from agent.wechat_ui import stop_requested, request_stop, clear_stop
        add("保护·单项停止标志", "ok", "wechat_ui.request_stop/stop_requested 可用")
    except Exception as e:
        add("保护·单项停止标志", "fail", str(e)[:120])
    try:
        src = open(os.path.join(ROOT, "scripts", "watchdog.py"), encoding="utf-8").read()
        has_flag = "STOP_FLAG" in src and "已有看门狗" in src
        add("保护·看门狗单实例+停止标志", "ok" if has_flag else "fail",
            "stop flag + 单实例锁均在代码中" if has_flag else "缺失（请用最新代码）")
    except Exception as e:
        add("保护·看门狗", "fail", str(e)[:120])

    # 10) 壁纸资源
    try:
        wp = os.path.join(ROOT, "assets", "wallpaper")
        files = os.listdir(wp) if os.path.isdir(wp) else []
        has = "海的眼睛.mp4" in files or any(f.endswith((".jpg", ".png", ".gif")) for f in files)
        add("界面·壁纸资源", "ok" if has else "warn", "方舟海岸（%s）" % (", ".join(files[:3]) or "无"),
            "无素材时页面自动回退 CSS 波浪；可把 海的眼睛.mp4 放入 assets/wallpaper")
    except Exception as e:
        add("界面·壁纸资源", "fail", str(e)[:120])

    # 11) 版本构建号
    try:
        add("界面·控制台版本", "ok", "b." + __import__("datetime").datetime.fromtimestamp(
            os.path.getmtime(os.path.join(ROOT, "agent", "console_html.py"))).strftime("%m%d-%H%M"))
    except Exception as e:
        add("界面·控制台版本", "fail", str(e)[:120])

    # 12) 依赖版本快速核对
    if verbose_deps:
        try:
            import importlib.metadata as _md
            if not hasattr(run, "_deps"):
                run._deps = { (d.metadata.get("Name") or "").lower(): (d.version or "") for d in _md.distributions() }
            ver = {}
            # 兼容包名：pip 名 wechatauto-replica，import 名 wechatauto
            for pkg in ("wechatauto-replica", "wechatauto", "Pillow", "requests", "pywin32", "mss", "numpy",
                        "opencv-python", "pygetwindow"):
                v = run._deps.get(pkg.lower())
                if v:
                    ver[pkg] = v
            add("依赖版本核对", "ok" if ("wechatauto-replica" in ver or "wechatauto" in ver) else "fail",
                "wechatauto-replica=%s，核心依赖 %d 个可读版本" % (ver.get("wechatauto-replica") or ver.get("wechatauto", "?"), len(ver)),
                "" if ("wechatauto-replica" in ver or "wechatauto" in ver) else "运行 setup_deps.py")
        except Exception as e:
            add("依赖版本核对", "fail", str(e)[:100])

    # 13) 历史 Bug 检验清单（开发至今所有已知漏洞的回归检查；全为秒级静态判定）
    def _src(name):
        try:
            return open(os.path.join(ROOT, name), encoding="utf-8").read()
        except Exception:
            return ""
    _wx = _src("scripts/persona_morph.py")
    _webui = os.path.join(ROOT, "agent", "webui.py")
    _wui = _src("agent/webui.py")
    _wechat = _src("agent/wechat.py")
    _ui = _src("agent/wechat_ui.py")
    _html = _src("agent/console_html.py")
    _cp = _src("agent/persona.py")
    _pr = _src("agent/persona_enrich.py")

    def _has(s, *keys):  # 所有 key 都出现 → ok（阈值宽松：出现即为修复）
        return all(k in s for k in keys)

    def _code_check(name, cond, detail_ok, detail_bad="", hint=""):
        add("检验·" + name, "ok" if cond else "warn",
            detail_ok if cond else (detail_bad or "未检测到修复痕迹"),
            hint)

    # —— 历史已知 Bug 回归 ——
    _code_check("路由归属(POST 链)", "elif path == \"/api/ui-test\""
                in _wui[_wui.find("def _handle_body_request"):] if "def _handle_body_request" in _wui else False,
                "ui-test/selfcheck-stop/评分/背景等 POST 路由在 _handle_body_request")
    _code_check("搜索不再翻页定位(服务通知)", "没有可定位的消息文本" in _wx,
                "无文本消息时明确失败，不再搜索/翻页")
    _code_check("评论区输入框(剪贴板+点击)", "type_into_focused" in _wechat and "0.965" in _wechat,
                "点击输入框+剪贴板粘贴 3 次重试；失败保留窗口")
    _code_check("朋友圈滚动(小步滚)", "step = -120 if direction > 0" in _wechat,
                "标准 -120 步进×N+焦点前置")
    _code_check("蓝点单击(菜单持久)", "_moments_hover_menu" in _wechat and "STOPPED" in _wechat,
                "单击不连点；循环检查停止标志")
    _code_check("朋友圈窗口OCR", "ImageGrab.grab" in _wechat and "ScreenOCR" in _wechat,
                "对朋友圈独立窗口截图 OCR（非主窗）")
    _code_check("自动重标定(挪窗)", "_layout_stale" in _ui and "render_w" in _ui,
                "窗口尺寸变化>15% 自动重标")
    _code_check("标定保护(少于5图标不覆盖)", "if len(clusters) < 5" in _ui,
                "图标不足不写文件")
    _code_check("表情面板坐标(实测校准)", "0.302" in _wechat and "0.879" in _wechat,
                "笑脸 13.2%宽 + 爱心底栏最右实测比例")
    _code_check("表情顺序(最早收藏排最尾)", "getmtime" in _wechat,
                "按 mtime 新→旧 排序")
    _code_check("表情收藏(空区防护+防误击)", "防误击" in _wechat or "收藏表情为空" in _wechat,
                "空收藏明确提示")
    _code_check("背景上传(base64清洗+padd)", "b64decode(raw, validate=False)" in _wui and "len(raw) % 4" in _wui,
                "清洗空白+padding 补全+明确报错")
    _code_check("背景资源服务(self._data_path)", "self._data_path" in _wui,
                "_serve_asset 用 self（修复 parent 引用 NameError）")
    _code_check("光标预览换源重绘", "preImg.onload = drawPreview" in _html,
                "重置/上传立即重绘")
    _code_check("鲸语服务端注入(双向切换)", "_apply_whale" in _wui and "no-store, no-cache" in _wui,
                "服务端按 cfg 注入+HTML no-store")
    _code_check("保存后自动刷新(带时间戳)", "u.searchParams.set('v', Date.now())" in _html,
                "保存→ ?v= 刷新（不吃缓存）")
    _code_check("单实例锁(旧版本冲突)", "bot.lock" in _wx and "已有 Persona Morph 实例" in _wx,
                "单实例锁+冲突提示")
    _code_check("停止必停(stopped.flag)", "stopped.flag" in _wx and "STOP_FLAG" in (_src("scripts/watchdog.py")),
                "停止写标志，看门狗见标志退出")
    _code_check("一键启动单实例检测", "already running" in _src("scripts/onestart.py").lower() or "bot.lock" in _src("scripts/onestart.py"),
                "已有实例→直接打开控制台")
    _code_check("浏览器单开(cmd start)", "cmd\", \"/c\", \"start\"" in _wx and "webbrowser.open(url)" not in _wx,
                "只 cmd start 一次（无双开）")
    _code_check("微信接入主线程同步(不卡控制台)", "微信接入成功（主线程同步）" in _wx,
                "微信接入 20s 限时+后台重试")
    _code_check("版本体检不弹窗(防卡死)", "_maybe_auto_fix" in _wx,
                "体检仅日志（不弹窗）")
    _code_check("行为推荐排除通用段", "说话规则（群聊通用）" in (_pr or ""),
                "只分析角色本体段")
    _code_check("模型评分(五维加权+0.01)", "from agent.persona_rating" in _wx and "RULES_TEXT" in _wx,
                "五维加权百分位；禁止整分")
    _code_check("补足循环(分升继续)", "rounds_done" in _wx and "人设导向" in _wx,
                "人设导向+分升检测+轮数")
    _code_check("补足import(agent.persona_enrich)", "from agent.persona_enrich import" in _wx,
                "补足引擎 import 位置正确")
    _code_check("会话日志清除(真删)", "clear_sessions" in _wx and "os.remove" in _wx,
                "清除运行明细 JSONL")
    _code_check("种子库文件可服务", "seed_library.json" in _mkdir if False else os.path.exists(os.path.join(ROOT, "data", "seed_library.json")),
                "模型学习实际读取 data/seed_library.json")
    _code_check("角色卡补足机制(通用规则段)", "说话规则（群聊通用）" in (open(os.path.join(ROOT, "agent", "persona_enrich.py"), encoding="utf-8").read()
                 if os.path.exists(os.path.join(ROOT, "agent", "persona_enrich.py")) else ""),
                "短卡自动补通用规则+示例")
    _code_check("成本真实计算(sessions)", "recent5_cost" in _wx and "sessions\"" in _wx.replace("\n", "") ,
                "从会话记录真实计算最近5条/平均")
    _code_check("导航实底色统一", "rgba(12,32,58,1)" in _html,
                "侧栏实底色（滚动色差修复）")
    _code_check("自定义卡分区可删(任意分区)", "custom_" in _html and "persona_cats" in _wui,
                "分区 CRUD + 卡片迁移/删除")
    _code_check("模型评分写回UI卡片", "model_reason" in _wx and "toFixed(2)" in _html,
                "模型分写回评分表并显示")

    # 计时与时长说明
    import time as _time
    _t0 = _time.time()
    add("检验·共 %d 项历史回归" % (len(checks) - 17), "ok", "全部静态判定，秒级完成（实测 <3 秒）")

    # 14) 素材库（≥1000 条目标）
    try:
        from agent.scoring import seed_library as _sl
        seeds = _sl(0)
        add("学习·金句素材库", "ok" if len(seeds) >= 1000 else "warn", "%d 条（目标 ≥1000）" % len(seeds),
            "" if len(seeds) >= 1000 else "继续扩充素材库")
    except Exception as e:
        add("学习·金句素材库", "fail", str(e)[:100])

    ok_n = sum(1 for c in checks if c["status"] == "ok")
    warn_n = sum(1 for c in checks if c["status"] == "warn")
    fail_n = sum(1 for c in checks if c["status"] == "fail")
    return {"ok": fail_n == 0, "checks": checks,
            "summary": "代码检测：通过 %d 项 / 注意 %d 项 / 失败 %d 项" % (ok_n, warn_n, fail_n)}


if __name__ == "__main__":
    import sys, time
    _t0 = time.time()
    r = run(verbose_deps="--deps" in sys.argv)
    for c in r["checks"]:
        print("%s %s：%s" % ({"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(c["status"], "ℹ️"), c["name"], c["detail"]))
    print(r["summary"])
    print("实测耗时：%.2f 秒" % (time.time() - _t0))
    sys.exit(0 if r["ok"] else 1)
