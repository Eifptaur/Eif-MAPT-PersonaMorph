# -*- coding: utf-8 -*-
"""丙-8 P0-A① 取证：面板徽章接线验证。

两层验证：
  ① badge_for 纯函数 —— 用假 /api/status 逐分支断言 web refreshBadges 口径；
  ② Shell._apply_badges 端到端 —— offscreen 建 Shell，喂同一份 status，
    检查 bot 徽章（st_panel）与各面板徽章（wrap._c8_badge）真的落位。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault(
    "WX_AGENT_CONFIG",
    str(Path(os.environ.get("TEMP", "/tmp")) / "qt-c8-probe-config.json"),
)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family  # noqa: E402

ensure_fonts()
t = THEMES["whale"]
fam = resolve_family(t.font_family)
if fam != t.font_family:
    for tk in THEMES.values():
        tk.font_family = fam
apply_font_to_app(app, t)

import panels_qt  # noqa: E402
from shell import BATCH_SECS  # noqa: E402

out: list[str] = []
fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    if not ok:
        fails += 1
    out.append(f"{'OK ' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")


# ── ① 纯函数分支 ──────────────────────────────────────────────
S1 = {
    "paused": False, "wechat_connected": True,
    "listen": {"groups": 3, "privates": 2},
    "model": {"configured": True, "name": "deepseek-chat-long-name"},
    "tts": {"ready": True},
    "image_gen": {"ready": False, "why": "没填 key"},
    "video_gen": {},
    "web_search": {"provider": "tavily-search-provider", "enabled": True},
    "tools": {"count": 12, "enabled": 5},
    "version_gate": {"allow": True, "level": "ok"},
    "version": {"wechat": "3.9.12"},
}
check("bot 运行中", panels_qt.badge_for("bot", S1)[:2], ("ok", "已就绪"))
check("wechat 已连接", panels_qt.badge_for("wechat", S1)[:2], ("ok", "已连接 · 监听 5 个"))
check("model 名截 10 字", panels_qt.badge_for("model", S1)[:2], ("ok", "deepseek-c"))
check("tts 可用", panels_qt.badge_for("tts", S1)[:2], ("ok", "可用"))
check("imggen 缺一步", panels_qt.badge_for("imggen", S1)[:2], ("warn", "缺一步"))
check("videogen 读取中", panels_qt.badge_for("videogen", S1)[:2], ("idle", "读取中"))
check("search provider 截 8 字", panels_qt.badge_for("search", S1)[:2], ("ok", "tavily-s"))
check("tools 计数", panels_qt.badge_for("tools", S1)[:2], ("ok", "5 / 12 开"))
check("vermat 已实测", panels_qt.badge_for("vermat", S1)[:2], ("ok", "已实测"))
check("media 聚合缺一步", panels_qt.badge_for("media", S1)[:2], ("warn", "缺一步"))
check("overview 交给面板自刷新", panels_qt.badge_for("overview", S1), None)
check("log 交给面板自刷新", panels_qt.badge_for("log", S1), None)
check("sessions 交给面板自刷新", panels_qt.badge_for("sessions", S1), None)

# 暂停 + 微信没连
S2 = {"paused": True, "wechat_connected": False}
check("bot 停着", panels_qt.badge_for("bot", S2)[:2], ("warn", "停着"))
check("wechat 没连上", panels_qt.badge_for("wechat", S2)[:2], ("err", "没连上"))

# 在线但零监听
S3 = {"paused": False, "wechat_connected": True, "listen": {"groups": 0, "privates": 0}}
check("wechat 要勾群", panels_qt.badge_for("wechat", S3)[:2], ("warn", "要勾群"))

# model 没填密钥 / 后端没给 configured
S4 = {"paused": False, "wechat_connected": True, "model": {"configured": False}}
check("model 没填密钥", panels_qt.badge_for("model", S4)[:2], ("err", "没填密钥"))
S5 = {"paused": False, "wechat_connected": True, "model": "deepseek"}
check("model 后端没给对象→读取中", panels_qt.badge_for("model", S5)[:2], ("idle", "读取中"))

# vermat 拦停 / 读不到
S6 = {"version_gate": {"allow": False}, "version": {"wechat": "3.9.12"}}
check("vermat 拦停", panels_qt.badge_for("vermat", S6)[:2], ("err", "拦停"))
S7 = {"version_gate": {}, "version": {}}
check("vermat 读不到", panels_qt.badge_for("vermat", S7)[:2], ("idle", "读不到"))

# tools 全关 → idle
S8 = {"tools": {"count": 12, "enabled": 0}}
check("tools 全关", panels_qt.badge_for("tools", S8)[:2], ("idle", "0 / 12 开"))

# search 关着
S9 = {"web_search": {"provider": "bing", "enabled": False}}
check("search 关着", panels_qt.badge_for("search", S9)[:2], ("idle", "关着"))

# status 非 dict → 全部 None（不动徽章）
check("空 status 不编数", panels_qt.badge_for("model", {})[:2], ("idle", "读取中"))

# ── ② 端到端分发 ──────────────────────────────────────────────
from PySide6.QtWidgets import QScrollArea  # noqa: E402

from shell import Shell  # noqa: E402

w = Shell(t)
w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
w.resize(1120, 720)
w.show()
app.processEvents()

# 丙-8 J 惰性构建：徽章挂在面板控件上，未建页没有 _c8_badge —— 先全量走一遍
# _go 把 27 页都建出来，等价旧全量构建语义，再验端到端分发不被惰性破坏
for _s in BATCH_SECS:
    w._go(_s, _s)
app.processEvents()
check("惰性预建后页栈全量就位", w.stack.count(), 27)

w._apply_badges(S1, 7)
check("端到端 bot 徽章", (w.st_panel.level, w.st_panel.text()), ("ok", "已就绪"))

got: dict[str, tuple[str, str]] = {}
for sec, idx in w._page_of.items():
    wd = w.stack.widget(idx)
    b = getattr(wd.widget() if isinstance(wd, QScrollArea) else wd, "_c8_badge", None)
    if b is not None:
        got[sec] = (b.level, b.text())
check("端到端 wechat", got.get("wechat"), ("ok", "已连接 · 监听 5 个"))
check("端到端 model", got.get("model"), ("ok", "deepseek-c"))
check("端到端 imggen", got.get("imggen"), ("warn", "缺一步"))
check("端到端 persona 计数", got.get("persona"), ("ok", "7 个人设"))
w._apply_badges(S1, 0)
wd = w.stack.widget(w._page_of["persona"])
b2 = getattr(wd.widget() if isinstance(wd, QScrollArea) else wd, "_c8_badge", None)
check("端到端 persona 零人设", (b2.level, b2.text()), ("warn", "还没有人设"))
# 本地语义面板不被轮询覆盖（badge_for None → 保持构建时「已加载」）
check("端到端 cursor 本地态不被覆盖", got.get("cursor"), ("info", "已加载"))

out.append("")
out.append(f"分发覆盖：{len(got)} 个面板徽章持有引用（27 面板 - overview/log/sessions 自管 - 无徽章页）")
out.append(f"FAILURES={fails}")

p = HERE / "_c8_badgeprobe_out.log"
p.write_text("\n".join(out), encoding="utf-8")
print("written", p, "fails=", fails)
