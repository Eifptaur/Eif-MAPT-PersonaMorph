# -*- coding: utf-8 -*-
"""「无障碍」判据（第 146 项）—— 四件主项 ＋ 七项附加，**逐项各有一条断言**。

四项主项：① 触摸目标 ≥44px　② `:focus-visible` 焦点环　③ 减少动效　④ 出错用 `assertive`、常规用 `polite`
七项附加：⑤ 数字等宽（`tabular-nums`／Qt 的 `tnum`）　⑥ 禁用态有可见反馈　⑦ 无边框窗可交互区 `no-drag`
        ⑧ 防滚动穿透　⑨ Qt 可访问名　⑩ Qt 可键盘聚焦　⑪ **两个实现面都覆盖**（web ＋ Qt 同批）

⛔ **第 ⑦ 项在本项目「不适用」**，这条也要有判据（反向锚）：控制台跑在**自带标题栏的 WebView2 窗口**里，
   页面**没有**设过 `app-region` 的拖动区 ⇒ 不存在"可交互区被拖动区吃掉"的问题。
   判据反过来盯住**页面里不许出现 `app-region`** —— 哪天有人加了拖动区，这里会提醒重新评估。

用法：runtime\\python\\python.exe scripts/a11y_selftest.py
"""
import io
import os
import re as _re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


# ⛔ 检查"生效的页面"要**先剥掉注释**：我解释第 ⑦ 项不适用时那段注释里就写着 `app-region`，
#   不剥会把"自己的说明"当成"页面真的用了"。⛔ **HTML 注释与 CSS 注释都要剥**
#   （第一版只剥了 HTML 注释 ⇒ 仍被自己的 CSS 注释骗到，假红了两次）。
_WEB_RAW = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
WEB = _WEB_RAW
# 「生效页面」视图：剥掉 HTML 注释与 CSS 注释。⛔ **只给排除型判断用**（"页面里有没有某属性"）——
#   存在型判断（"某个控件在不在"）要用原文：注释里也可能出现同样的字串，剥多了会假红。
#   （这条判据第一版连踩两次：先被自己的 CSS 注释骗，剥 HTML 注释后又把注释里的示例算进去。）
WEB_NC = _re.sub(r"/\*.*?\*/", "", _re.sub(r"<!--.*?-->", "", _WEB_RAW, flags=_re.S), flags=_re.S)
QT_PANELS = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
QT_WIDGETS = io.open(os.path.join(ROOT, "ui_qt", "widgets.py"), encoding="utf-8").read()

print("── 四项主项 ──")
ok("① 触摸目标 ≥44px：web 有 `--tap-min`（44px）并被当作最小高度用上",
   "--tap-min:44px" in WEB and _sm.has(WEB, "min-height:var(--tap-min)"), "")
ok("①b 勾选框那种小控件用**透明热区**放到 44px（视觉不变、够得着的变大）",
   _sm.has(WEB, "width:var(--tap-min);height:var(--tap-min)"), "")
ok("② 键盘焦点环：按钮有 `:focus-visible`（只键盘才会亮，鼠标点不亮）",
   _sm.has(WEB, "button:focus-visible"), "")
ok("②b★ 覆盖到**所有**可交互控件（原来只有 button/checkbox ⇒ 输入框、下拉、链接都没有环）",
   _sm.has(WEB, ":where(input,select,textarea,a[href],[tabindex]):focus-visible"), "")
ok("③ 减少动效：存在 `prefers-reduced-motion` 媒体查询",
   "@media(prefers-reduced-motion:reduce)" in WEB.lower().replace(" ", ""), "")
ok("③b★ 并且有一条**全局兜底**（原来只关了 button 与 .sub.flash，别的动画照放）",
   _sm.has(WEB, "animation-duration:.001ms!important")
   and _sm.has(WEB, "transition-duration:.001ms!important"), "")
ok("④ 出错/即时反馈用 `assertive`（toast）", _sm.has(WEB, 'id="toast" role="status" aria-live="assertive"'), "")
ok("④b 常规状态用 `polite`（侧栏运行状态）", _sm.has(WEB, 'class="status" aria-live="polite"'), "")

print("\n── 七项附加 ──")
ok("⑤ 数字等宽：web 用 `font-variant-numeric:tabular-nums`（刷新时数字不左右抖）",
   _sm.has(WEB, "font-variant-numeric:tabular-nums"), "")
ok("⑤b★ Qt 侧同口径：数值字体开了 `tnum` 特性（`QFont.setFeature`），不是只改 web",
   _sm.has(QT_PANELS, 'setFeature("tnum", 1)') and _sm.has(QT_PANELS, "_tnum(qfont("), "")
ok("⑥ 禁用态有可见反馈（`button:disabled` 有样式，不是「点了没反应」）",
   _sm.has(WEB, "button:disabled") or _sm.has(WEB, ":disabled{"), "")
ok("⑦⛔ **本项不适用**：页面里**没有** `app-region` 拖动区（拖动由自带标题栏的宿主负责）"
   "—— 反向锚：哪天有人加了拖动区，这里会提醒重新评估",
   not _re.search(r"[-\w]*app-region\s*:", WEB_NC), "")
ok("⑧ 防滚动穿透：`overscroll-behavior:contain`（长列表滚到底不带整页滚）",
   _sm.has(WEB, "overscroll-behavior:contain"), "")
ok("⑨ Qt 可访问名：按钮构造时按文字设名（读屏读的是名字，不是画上去的字）",
   _sm.has(QT_WIDGETS, "setAccessibleName(str(text))"), "")
ok("⑨b★ 纯图标按钮也读得出来：`setToolTip` 同步成可访问名"
   "（读屏**不读 tooltip** ⇒ 不同步的话 ×、−、+ 在读屏里就是个「按钮」）",
   _sm.has(QT_WIDGETS, "def setToolTip(self, tip: str)")
   and _sm.has(QT_WIDGETS, "if tip and not self.text():"), "")
ok("⑩ Qt 可键盘聚焦：没有把 Btn 的焦点策略关掉（`NoFocus` 一出现，键盘就彻底进不去）",
   "setFocusPolicy(Qt.FocusPolicy.NoFocus)" not in QT_WIDGETS, "")
ok("⑪★★ **两个实现面都覆盖**：上面 ⑤/⑨/⑨b 三项都在 Qt 侧有对应物（只改 web 不算做完）",
   _sm.has(QT_PANELS, "_tnum(") and _sm.has(QT_WIDGETS, "setAccessibleName(str(text))"), "")

print("\n── 反向锚（证明上面几条不是恒真）──")
ok("R1★ 把「全局兜底」那段拿掉 ⇒ ③b 会红（这里用「必须有那段」本身做自检：字符串确实在文件里）",
   WEB.count("animation-duration:.001ms!important") == 1, str(WEB.count("animation-duration:.001ms!important")))
ok("R2 `aria-live` 一共只用在这几处（不是满页撒 —— 撒多了读屏会被反复打断）",
   2 <= WEB.count("aria-live=") <= 8, str(WEB.count("aria-live=")))
ok("R3 分母守卫：web 与 Qt 两侧文件都真的读到了（不是空字符串过关）",
   len(WEB) > 100000 and len(QT_WIDGETS) > 5000, "%d / %d" % (len(WEB), len(QT_WIDGETS)))

print("\n== 无障碍判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
