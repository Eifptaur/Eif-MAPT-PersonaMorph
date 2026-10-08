# -*- coding: utf-8 -*-
"""**无界面对等判据**：控制台有的能力，无界面（`pmctl`）会不会做？差距**只许缩小不许扩大**。

## 为什么要量化
"无界面能不能做到和有界面一样全面"这句话不能用感觉答：
  · 控制台：`agent/routes.py` 里有 **109 条 `/api/` 路由**；
  · 无界面：`scripts/pmctl.py` 里 **19 条子命令**。
⇒ **不追求 1:1**（有些能力本质需要屏幕：看图表、拖拽、上传图片、可视化取点）；
  但我们要求：**无显示器机器上真正需要能做的事，必须有命令行路径**，且**差距只许缩小**。

## 三道闸
  P1 **规模棘轮**：两边的数量与"关键能力覆盖率"记下来，**只许涨**。
  P2 ★ **关键能力清单**：逐条断言"无界面侧有对应命令"（漏一条就红）。
  P3 ★ **不许吹等价**：`pmctl` 的文档里必须写明"哪些做不到、为什么"（诚实口径）。
"""
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


# ── 现状基线（**只许涨**；涨了就把这里改大并说明）──────────────────────────
BASE_API_ROUTES = 109       # 控制台 /api/ 路由
BASE_PMCTL_CMDS = 20        # pmctl 子命令（19 → 20：新增 `corpus`，本机真实截图入库/喂真值）
#: ★ **关键能力**：无显示器机器上真正需要能做的事 ⇒ 每条都必须有命令行路径
KEY_CAPS = {
    "运行状态": "status",
    "本机适配档案": "adapt",
    "体检": "check",
    "一把跑完（status+adapt+check）": "doctor",
    "症状自查（发不出去/不回复…）": "verify",
    "日志尾部": "logs",
    "配置读写": "cfg",
    "暂停": "pause",
    "恢复": "resume",
    "开机自启": "autostart",
    "低配模式": "lowspec",
    "会话列表": "sessions",
    "接入诊断/微信目录": "wechat",
    "图标指纹（取/清）": "fingerprint",
    "重新标定": "calibrate",
    "提反馈": "feedback",
    "反馈包": "pack",
    "真值审核": "truth",
    "语料（本机真实截图入库/喂真值）": "corpus",
    "版本": "version",
}

print("── P1：规模与棘轮（只许涨）──")
try:
    from agent import routes as _rt
    _api = [p for p in _rt.ROUTES if p.startswith("/api/")]
except Exception as e:  # noqa: BLE001
    _api = []
    print("   （读不到路由表：%s）" % str(e)[:60])
_pm = io.open(os.path.join(ROOT, "scripts", "pmctl.py"), encoding="utf-8").read()
_cmds = sorted(set(re.findall(r'sub\.add_parser\("([a-z0-9\-]+)"', _pm)))
ok("P1a 控制台 /api/ 路由数不少于基线（能力面只许增）", len(_api) >= BASE_API_ROUTES,
   "现在 %d 条（基线 %d）" % (len(_api), BASE_API_ROUTES))
ok("P1b pmctl 子命令数不少于基线（无界面这侧只许增）", len(_cmds) >= BASE_PMCTL_CMDS,
   "现在 %d 条（基线 %d）：%s" % (len(_cmds), BASE_PMCTL_CMDS, "、".join(_cmds)))

print("\n── P2：★ 关键能力必须有无界面路径（漏一条就红）──")
_missing = ["%s（%s）" % (cap, cmd) for cap, cmd in KEY_CAPS.items() if cmd not in _cmds]
ok("P2 关键能力 %d 项，无界面对应命令齐全" % len(KEY_CAPS), not _missing,
   ("缺：%s" % "、".join(_missing)) if _missing else "%d/%d 有对应" % (len(KEY_CAPS), len(KEY_CAPS)))

print("\n── P3：每条命令真的起得来（不是空壳）──")
_env = dict(os.environ, PM_NO_WINDOW_TOUCH="1")
_bad = []
for c in _cmds:
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "pmctl.py"), c, "--help"],
                           capture_output=True, text=True, timeout=60, env=_env,
                           encoding="utf-8", errors="replace",
                           # ⛔ 判据自己**不许弹窗口**：少这个 flag，19 次 `--help` 会开 19 个黑框
                           #    （产品的"不许开窗"判据 `proc_window_selftest` 当场红）
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or "usage:" not in (r.stdout or ""):
            _bad.append("%s(rc=%s)" % (c, r.returncode))
    except Exception as e:  # noqa: BLE001
        _bad.append("%s(%s)" % (c, str(e)[:24]))
ok("P3 所有子命令 `--help` 正常（没有注册了却没实现的空壳）", not _bad, str(_bad[:4]))
ok("P3b 每个子命令都绑了实现（`set_defaults(func=...)` 与 parser 一一对应）",
   len(re.findall(r"set_defaults\(func=", _pm)) >= len(_cmds),
   "parser %d 个 · set_defaults %d 处" % (len(_cmds), len(re.findall(r"set_defaults\(func=", _pm))))
ok("P3c 菜单项与子命令同步（菜单里也要能按到关键动作）",
   all(t in _pm for t in ("真值审核", "接入诊断", "重新标定", "取指纹", "直接提反馈")))

print("\n── P4：★ 不许吹等价（诚实口径）──")
ok("P4 文档写明「不追求 1:1 / 有些能力本质需要屏幕」（不许宣称完全等价）",
   any(k in _pm for k in ("本质需要屏幕", "做不到", "不是完全等价")))
_md = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
ok("P4b README 里无界面操作台的介绍与菜单对上（用户找得到这条路）",
   "无界面操作台" in _md and "一键操作（双击我）.cmd" in _md)
_has_menu = "def menu" in _pm and "_menu_fixwechat" in _pm and "_menu_truth" in _pm
ok("P4c 菜单里有接入/标定/指纹 与 真值审核 两条子流程（不是只有命令）", _has_menu)

print("\n无界面对等判据：%d 通过 / %d 失败" % (PASS, FAIL))
print("（现状：控制台 %d 条 /api/ 路由 · 无界面 %d 条命令；关键能力 %d 项全覆盖）"
      % (len(_api), len(_cmds), len(KEY_CAPS)))
sys.exit(1 if FAIL else 0)
