# -*- coding: utf-8 -*-
"""Qt 原型自检 —— 不等窗口跑起来也能验的纪律，全在这里。

为什么要自检（而不是"我看一眼觉得行"）：
  这个原型的结论要拿去决定「要不要把 27 个面板重写一遍」。那种决定不能靠印象。
  凡是能用断言钉住的纪律，就钉住；钉不住的（观感、手感）才交给截图与人工判断。

对应 web 侧那批判据：
  scripts/nav_ui_selftest.py     ← 导航项数、图标、名字长度、折叠机制
  scripts/console_copy_selftest.py ← 无 emoji、无裸英文术语
  scripts/ui_arch_selftest.py    ← 面板↔导航一一对应、顺序一致
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROWS: list[tuple[str, bool, str]] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    ROWS.append((name, bool(cond), extra))


# ---------------------------------------------------------------- 1. 导入与语法

def t_syntax() -> None:
    import py_compile  # noqa: PLC0415

    for f in ("stylekit_qt.py", "widgets.py", "confirm.py", "heal.py", "shell.py", "agent_bridge.py",
              "panels_qt.py", "sec_meta.py"):
        p = HERE / f
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(HERE / "__pycache__" / (f + "c")))
            ck(f"{f} 语法通过", True)
        except Exception as e:  # noqa: BLE001
            ck(f"{f} 语法通过", False, str(e)[:90])


# ---------------------------------------------------------------- 2. 导航结构与 web 侧同构

def _nav_truth_from_web() -> list[tuple[str, str, list[tuple[str, str]]]]:
    """运行时解析 agent/console_html.py 的 <nav id=nav>，拿到导航真值。

    这是「与 web 侧一致」的唯一权威来源。旧版判据把组名手抄在 selftest 里
    （want_groups = ["天天用", ...]），原型自己编一套比喻命名也能绿 —— 假对齐。
    2026-09-23 改版：组名/组序/项名/项序/sec 锚点全部以 web 源码为准全等比对。
    """
    web_path = HERE.parents[0] / "agent" / "console_html.py"
    text = web_path.read_text(encoding="utf-8")
    m = re.search(r'<nav class="nav" id="nav">(.*?)</nav>', text, re.S)
    if m is None:
        raise AssertionError("console_html.py 里找不到 <nav id=nav> —— 解析器要跟版式走")
    out: list[tuple[str, str, list[tuple[str, str]]]] = []
    for chunk in re.split(r'<div class="nav-grp" data-grp="', m.group(1))[1:]:
        key = re.match(r'(\w+)"', chunk).group(1)
        gt = re.search(r'<span class="gt">([^<]+)</span>', chunk)
        pairs = re.findall(
            r'href="#sec-([\w-]+)".*?<span class="lb">([^<]+)</span>', chunk, re.S)
        if gt is None or not pairs:
            raise AssertionError(f"分组 {key} 解析不出组名或项 —— 版式变了要跟改")
        out.append((gt.group(1), key, pairs))
    return out


def t_nav() -> None:
    from shell import NAV  # noqa: PLC0415

    src = (HERE / "shell.py").read_text(encoding="utf-8")
    wid = (HERE / "widgets.py").read_text(encoding="utf-8")

    flat = [e[0] for _t, _k, es in NAV for e in es]
    ck("导航项数 = 27", len(flat) == 27, f"实际 {len(flat)}")

    # ★ 真对齐（2026-09-23 改版）：以 web 源码解析结果为唯一真值，全等比对。
    web = _nav_truth_from_web()
    mine = [(t, k, [e[0] for e in es], [e[2] for e in es]) for t, k, es in NAV]
    theirs = [(t, k, [lb for _s, lb in es], [s for s, _lb in es]) for t, k, es in web]
    ck("web 侧解析出 5 组 27 项（解析器没跟丢）",
       len(theirs) == 5 and sum(len(t[2]) for t in theirs) == 27,
       f"组 {len(theirs)} / 项 {sum(len(t[2]) for t in theirs)}")
    ck("组名与 web 侧逐字一致（顺序含内）",
       [m[0] for m in mine] == [t[0] for t in theirs],
       " > ".join(m[0] for m in mine))
    ck("组 key 与 web 侧 data-grp 一致",
       [m[1] for m in mine] == [t[1] for t in theirs],
       ",".join(m[1] for m in mine))
    bad_items = [(m[0], m[2], t[2]) for m, t in zip(mine, theirs) if m[2] != t[2]]
    ck("项名与项序与 web 侧逐字一致", not bad_items,
       "; ".join(f"{b[0]}: {b[1]} ≠ {b[2]}" for b in bad_items))
    bad_secs = [(m[0], m[3], t[3]) for m, t in zip(mine, theirs) if m[3] != t[3]]
    ck("sec 锚点与 web 侧一致", not bad_secs,
       "; ".join(f"{b[0]}: {b[1]} ≠ {b[2]}" for b in bad_secs))

    # 「机器人」必须排在第 3 位 —— web 侧 ui_arch_selftest 有同一条硬断言（用户点名要放前面）
    ck("「机器人」排在第 3 位（同 ui_arch 硬断言）", flat.index("机器人") + 1 == 3,
       f"实际第 {flat.index('机器人') + 1} 位")
    ck("首项是「概览」", flat[0] == "概览", flat[0])

    # 每项都有说明（导航搜索要按说明匹配，且"3 秒找到要改什么"依赖它）
    missing = [e[0] for _t, _k, es in NAV for e in es if len(e) < 3 or not e[1].strip()]
    ck("每个导航项都带一句话说明", not missing, ",".join(missing))

    # 无 emoji（红线）
    emo = re.compile("[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\u2B00-\\u2BFF\\uFE0F]")
    hits = [w for w in flat if emo.search(w)] + [
        t for t, _k, _e in NAV if emo.search(t)
    ]
    ck("导航文案无 emoji（红线）", not hits, ",".join(hits))

    # 名字 ≤5 字（对齐 web 侧 nav_ui_selftest）
    long_names = [w for w in flat if len(w) > 5]
    ck("导航名 ≤5 字", not long_names, ",".join(long_names))

    # 分组键唯一
    keys = [k for _t, k, _e in NAV]
    ck("分组键唯一", len(keys) == len(set(keys)), ",".join(keys))

    # 搜索框存在且三路匹配（名字/分组/说明）
    # ⚠️ 占位文案在 widgets.py（SearchBox 自己那行），不在 shell.py —— 判据要查对文件
    ck("有导航搜索框", "SearchBox(" in src and "class SearchBox" in wid and "找功能" in wid)
    ck("搜索框占位文案与 web 侧同口径",
       "找功能…（如：发消息 / 换模型）" in wid)
    ck("搜索三路匹配（名字/分组/说明）",
       "it.text() + \" \" + g.title" in src and "it.hint or" in src)

    # ★ 导航图标（2026-09-23 用户观察："每个分区还有功能项都做了图标设计，预览图里没有"）
    #   判据一：icons.INNER 覆盖全部 27 个 sec 锚点（与 web 同一份 path 数据）
    ico_src = (HERE / "icons.py").read_text(encoding="utf-8")
    secs = [e[2] for _t, _k, es in NAV for e in es]
    missing_ico = [s for s in secs if f'"{s}"' not in ico_src]
    ck("27 个导航图标与 sec 锚点一一对应（icons.py）", not missing_ico, ",".join(missing_ico))
    #   判据二：NavItem 真的把图标接上了（有 icon_key 参数 + 渲染调用），组头有 chevron
    ck("导航项图标已接线（icon_key → setIcon）",
       "icon_key" in wid and "nav_icon(" in wid and "_refresh_icon" in wid)
    ck("组头折叠 chevron 已接线（不再用文字箭头）",
       "chevron_pixmap(" in wid and '"▾' not in wid and '"▸' not in wid)

    # ★ 机器人主面板接真配置（丙-4）：原型硬编码值（群DeepSeek/开机自启摆设行）退役
    ck("机器人主面板四行接真键位（bot_nickname/self_nickname/context_tier/text_style）",
       all(k in src for k in (
           "wechat.bot_nickname", "persona.self_nickname",
           "store.context_tier", "ui.text_style",
       )) and "config_io.write_patch" in src)
    ck("响应档位四档文案与 web 同口径（1 档：仅艾特 → 4 档：全响应）",
       "1 档：仅艾特" in src and "4 档：全响应" in src)
    ck("保存行走 config_io（write_patch + 已保存/没保存成 回执）",
       "_save_bot_panel" in src and "已保存" in src and "没保存成" in src)
    ck("主题跟随只认外部变化（_theme_seen 基线 + persist=False 不回写）",
       "ui.theme" in src and "_theme_seen" in src and "_switch_theme(th, persist=False)" in src)
    ck("顶栏切主题持久化（_switch_theme 写 ui.theme，重启保持）",
       '"ui.theme": key' in src and "persist: bool = True" in src)
    ck("演示按钮 = 假装后台挂了（不用「模拟『服务死掉』」）",
       "假装后台挂了" in src and "模拟『服务死掉』" not in src)

    # ★ 顶栏（2026-09-23 用户观察四条）
    ck("顶栏鲸鱼徽章用真图（assets/icon-whale.png）",
       "icon-whale.png" in wid and "class WhaleBadge" in wid)
    ck("顶栏主题/文案两轴都是滑槽分段切换器（Segmented）",
       "Segmented(" in src and "class Segmented" in wid)
    ck("文案风格切换器已上顶栏（正常/鲸语）",
       '("normal", "正常")' in src and '("whale", "鲸语")' in src)
    #   鲸语桥读 agent/whale_text.py 真字典（单一来源，不另编一份）
    ck("鲸语接线读 agent/whale_text.py（单一来源）",
       "whale_text.py" in src and "_whale_tr" in src)


# ---------------------------------------------------------------- 3. 三套主题的差异是"结构级"的

def t_themes() -> None:
    from stylekit_qt import THEMES  # noqa: PLC0415

    ck("三套主题齐全", set(THEMES) == {"whale", "light", "dark"}, ",".join(THEMES))
    w, l, d = THEMES["whale"], THEMES["light"], THEMES["dark"]

    # 玻璃只在 whale —— 这是"设计思路层面拉开"的最小可验证判据
    ck("只有 whale 走玻璃质感", [k for k, t in THEMES.items() if t.glass] == ["whale"])

    # 明度阶梯：whale 深 → light 更浅 → dark 最暗。三套必须真的分开
    def lum(hexs: str) -> float:
        h = hexs.lstrip("#")[:6]
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255

    ck("底色明度三套分明（light > whale > dark）",
       lum(l.bg) > lum(w.bg) > lum(d.bg),
       f"light={lum(l.bg):.2f} whale={lum(w.bg):.2f} dark={lum(d.bg):.2f}")

    # 圆角阶梯：whale 最圆润、light 居中、dark 最紧 —— 对应三种语言的"性格"
    ck("圆角阶梯 whale > light > dark",
       w.radius_card > l.radius_card > d.radius_card,
       f"{w.radius_card} / {l.radius_card} / {d.radius_card}")

    # 字号/字距语气差异
    ck("dark 的字距比 light 更开（精密仪器感）", d.grp_spacing > l.grp_spacing,
       f"dark={d.grp_spacing} light={l.grp_spacing}")
    # ⚠️ 判据改版（2026-09-23 总调度定夺）：
    # 旧判据 "dark 正文比 whale 小" 是原型自己发明的差异化，不是控制台事实。
    # 控制台 body{font:14px/1.6}(L443) 三主题共用，button{font:inherit}(L660) 继承它 ⇒ 三主题正文都是 14。
    # dark 的"精密仪器感"靠字距(grp_spacing)体现，不靠字号 —— 上面那条字距断言已经覆盖了这个语气差异。
    ck("三主题正文字号一致（控制台未覆盖，都 = 14）",
       w.body_size == l.body_size == d.body_size == 14,
       f"whale={w.body_size} light={l.body_size} dark={d.body_size}")

    # 状态色四态齐全，且 err 与 ok 色相分离（色盲友好最低要求）
    for k, t in THEMES.items():
        for lvl in ("ok", "warn", "err", "info"):
            ck(f"{k} 有 {lvl} 状态色", bool(getattr(t, lvl, "")))

    # ★ 危险按钮字色 err_tx（2026-09-23 用户观察："危险操作那里，黑色跟深色背景混在一起"）
    #   dark 上主 err(#E5484D) 当字色对比不足 ⇒ 必须有更亮的 err_tx 档（web --err-tx 口径）。
    no_tx = [k for k, t in THEMES.items() if not getattr(t, "err_tx", "")]
    ck("err_tx（危险字色亮档）三主题齐备", not no_tx, ",".join(no_tx))
    ck("dark 的 err_tx 必须比主 err 亮", lum(d.err_tx) > lum(d.err),
       f"tx={d.err_tx} err={d.err}")
    ck("danger 按钮字色用的是 err_tx", "err_tx" in (HERE / "widgets.py").read_text(encoding="utf-8"))

    # 三套主题的 token 字段完全一致（换主题不能丢字段，否则重建时崩）
    fw, fl, fd = set(vars(w)), set(vars(l)), set(vars(d))
    ck("三套主题字段集完全一致", fw == fl == fd,
       ",".join(sorted((fw ^ fl) | (fw ^ fd))))


# ---------------------------------------------------------------- 3.5 运行时渲染取证

def t_runtime_render() -> None:
    """渲染产物级断言 —— 源码级断言会骗人（2026-09-23 实锤两案）：

    · danger 按钮源码写了 err_tx，但 rgba() 返回的 QColor 对象插进 QSS
      f-string 变成垃圾值 → 整条规则解析失败 → 黑字沉底，源码断言照样绿；
    · whale tx2/tx3 是 rgba() 串，QtSvg 不认 → 整套导航图标"隐形"，同上。
    ⇒ 对这类缺陷，只有"摸渲染结果"的断言作数。
    """
    import os  # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    from icons import INNER, svg_pixmap  # noqa: PLC0415
    from stylekit_qt import THEMES  # noqa: PLC0415
    from widgets import Btn, NavItem  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    # 1) 危险按钮：QSS 里不许有对象垃圾值；字色必须真的写成 err_tx
    for k, t in THEMES.items():
        sheet = Btn("清空全部记忆", t, "danger").styleSheet()
        ck(f"{k} danger QSS 无对象垃圾值（QColor/0x 不许出现）",
           "QColor" not in sheet and "0x" not in sheet,
           sheet[:60])
        ck(f"{k} danger 字色真写成 err_tx（{t.err_tx}）",
           f"color:{t.err_tx}" in sheet)

    # 2) whale 激活导航项底色同理（rgba→QColor 插值的地雷曾埋在同一路径）
    it = NavItem(THEMES["whale"], "概览", icon_key="overview")
    it.set_active(True)
    s = it.styleSheet()
    ck("whale 激活导航项 QSS 无对象垃圾值", "QColor" not in s and "0x" not in s, s[:60])

    # 3) 图标真渲染：whale 的 rgba() 喂进去，描边必须真实落像素
    def _opaque_px(pm) -> int:
        img = pm.toImage()
        n = 0
        for y in range(img.height()):
            for x in range(img.width()):
                if img.pixel(x, y) & 0xFF000000:
                    n += 1
        return n

    n_whale = _opaque_px(svg_pixmap(INNER["bot"], THEMES["whale"].tx2))
    ck("whale tx2 喂图标：描边真实落像素（>40px）", n_whale > 40, f"px={n_whale}")
    n_bad = _opaque_px(svg_pixmap(INNER["bot"], "not-a-color"))
    ck("非法色值回退灰：图标不消失", n_bad > 40, f"px={n_bad}")


# ---------------------------------------------------------------- 3.7 双字体 + 坑⑥修复

def t_fonts_rgba() -> None:
    """2026-09-23 用户拍板的双字体（大字=朝華標題A / 小字=屏显臻宋）
    + 坑⑥（QColor 不认 CSS rgba() 串）的修复断言 —— 「读取中」黑字的根。"""
    from PySide6.QtGui import QColor  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    from stylekit_qt import THEMES, _c, ensure_fonts, qfont, status_colors  # noqa: PLC0415

    QApplication.instance() or QApplication([])  # 坑④同族：字体操作前必须有 app

    d, b, e = ensure_fonts()
    ck("朝華標題A 注册成功（display 字体）", d == "ZhaohuaMinA", repr(d))
    ck("屏显臻宋 注册成功（body 字体）", bool(b), repr(b))
    ck("emoji 兜底字体挂上（鲸鱼 emoji 的归宿）", e == "Segoe UI Emoji", repr(e))

    w = THEMES["whale"]
    fh = qfont(w, 15, 600, display=True)
    ck("display=True → 字体链首位是朝華標題A",
       bool(fh.families()) and fh.families()[0] == "ZhaohuaMinA", str(fh.families()))
    fb = qfont(w, 14)
    ck("默认 qfont → 字体链首位是屏显臻宋",
       bool(fb.families()) and fb.families()[0] == b, str(fb.families()))
    ck("qfont 回退链含 emoji 兜底", e in fb.families(), str(fb.families()))

    c = _c("rgba(200,224,245,0.78)")
    ck("坑⑥修复：_c 认 rgba() 串（200,224,245,α=199）",
       c.isValid() and (c.red(), c.green(), c.blue(), c.alpha()) == (200, 224, 245, 199),
       c.name(QColor.NameFormat.HexArgb))
    fg = status_colors(w, "idle")[2]
    ck("「读取中」徽章字色 = 淡蓝白（不再是黑）",
       fg.isValid() and fg.name(QColor.NameFormat.HexArgb).lower() == "#c7c8e0f5",
       fg.name(QColor.NameFormat.HexArgb))

    wid = (HERE / "widgets.py").read_text(encoding="utf-8")
    src = (HERE / "shell.py").read_text(encoding="utf-8")
    ck("h2() 面板标题用 display 字体", "qfont(t, t.h2_size, t.h2_weight, display=True)" in wid)
    ck("顶栏标题用 display 字体", "qfont(self.t, 14, 600, display=True)" in src)


# ---------------------------------------------------------------- 4. 可用性纪律

def t_usability() -> None:
    src = (HERE / "shell.py").read_text(encoding="utf-8")
    wid = (HERE / "widgets.py").read_text(encoding="utf-8")
    cnf = (HERE / "confirm.py").read_text(encoding="utf-8")

    # 每个面板必须有 h2 + 一句话说明（ui_arch_selftest 的同口径）
    ck("面板有 h2", "h2(self.t," in src)
    ck("面板有一句话说明 desc", "desc(" in src)

    # 空态 / 失败态：不许"点了没反应"
    ck("状态徽章存在", "Badge(" in src and "class Badge" in wid)
    ck("徽章拿不到数据时落 idle（不许默认 ok）",
       'level: str = "idle"' in wid or '"idle", "读取中"' in src)

    # 危险操作二次确认 + 写清后果 + 打字门槛
    ck("危险操作走二次确认", "ConfirmDialog(" in src)
    ck("确认弹窗逐条列后果", "consequences" in cnf and "for c in consequences" in cnf)
    ck("最强危险操作带打字门槛", 'typed_word="清空"' in src)

    # 术语通俗化：界面上不许出现裸英文技术词
    ui_text = re.findall(r'"([^"\n]{2,40})"', src)
    bad = [
        s for s in ui_text
        if re.search(r"\b(token|JSON|API|WebView2|base64|DPI|OCR|UIA|port|localhost)\b", s)
    ]
    ck("界面文案无裸英文术语", not bad, " / ".join(bad[:4]))

    # 自愈：窗口能自己发现服务死活
    ck("原型自己做后台探活", "_probe_timer" in src and "probe_backend(" in src)
    ck("有『服务死掉』的演示入口（对应用户报的场景）",
       "_simulate_dead" in src and "Health.DEAD" in src)


# ---------------------------------------------------------------- 4.5 面板组（日常/智能）

def t_panels() -> None:
    """丙-2 第 2 棒的纪律：9 张新面板真建出来、真接进切换、真跟鲸语。

    与 web 侧 ui_arch_selftest 同一条硬判据 —— 面板↔导航一一对应；
    并且对"源码级断言会骗人"的教训照单全收：这里全部**摸真实控件**。
    """
    import os  # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QLabel  # noqa: PLC0415

    from panels_custom import MANUAL  # noqa: PLC0415
    from panels_qt import BATCH_SECS, build_panel  # noqa: PLC0415
    from sec_meta import secs  # noqa: PLC0415
    from shell import NAV, Shell  # noqa: PLC0415
    from stylekit_qt import THEMES  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    # ① 面板↔导航一一对应：BATCH_SECS + 机器人主面板 = 全部 27 项导航
    nav_secs = {s for _t, _k, es in NAV for _l, _h, s in es}
    ck("新面板清单与全部导航一一对应（机器人=主面板，共 26+1）",
       nav_secs == set(BATCH_SECS) | {"bot"},
       f"导航有而面板无: {sorted(nav_secs - set(BATCH_SECS) - {'bot'})} / "
       f"面板有而导航无: {sorted(set(BATCH_SECS) - nav_secs)}")
    ck("web 源码解析出了全部 26 个 sec 的元数据（手写件允许 0 行，但标题必须有）",
       all(s.title and (s.rows or s.key in MANUAL)
           for s in secs().values() if s.key in BATCH_SECS),
       ",".join(k for k in BATCH_SECS
                if not (secs()[k].rows or k in MANUAL)))
    t = THEMES["whale"]
    empty = []
    for sec in BATCH_SECS:
        wgt = build_panel(t, sec)
        if wgt is None or not wgt.findChildren(QLabel):
            empty.append(sec)
    ck("26 个 sec 都能构建出非空 QWidget（标题/行控件真实存在）", not empty, ",".join(empty))

    # ★ 生成器零手抄：配置键只许来自 sec_meta 的运行时解析（交接件硬规矩）
    pq = (HERE / "panels_qt.py").read_text(encoding="utf-8")
    leaked = [k for k in ("owner_accounts", "bot_nickname", "context_tier",
                          "max_results", "consolidate_enabled", "holyshits_upload_url") if k in pq]
    ck("面板生成器零手抄配置键（元数据驱动，不手抄几百个配置项）", not leaked, ",".join(leaked))

    # ② 切换：点导航真正换页，旧页不残留
    w = Shell(THEMES["light"])
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.show()
    QApplication.processEvents()
    st = w.stack
    ck("页栈页数 = 27（机器人主面板 + 26 张新面板）", st.count() == 27, str(st.count()))
    ck("每个导航 sec 都映射到了页栈页面", set(w._page_of) == set(BATCH_SECS) | {"bot"})
    w._go("overview", "概览")
    QApplication.processEvents()
    p1 = st.currentWidget()
    w._go("model", "模型")
    QApplication.processEvents()
    p2 = st.currentWidget()
    ck("导航点击真正换页（当前页变为目标页）",
       p2 is st.widget(w._page_of["model"]) and p2 is not p1)
    ck("旧页不残留（切换后旧页隐藏）", p1.isHidden())

    # ③ 鲸语切换对新面板文案同样生效（_orig_texts 收录新面板控件）
    w._go("model", "模型")
    page = st.widget(w._page_of["model"])
    lb = next(x for x in page.findChildren(QLabel) if x.text() == "接口地址")
    w._apply_text_style("whale")
    ck("鲸语切换翻译了新面板的行标签（接口地址 → 鲸语文案）",
       lb.text() != "接口地址", lb.text())
    ck("_orig_texts 收录了新面板的控件（不止主面板）",
       any(k for k in w._orig_texts
           if k in {id(x) for x in page.findChildren(QLabel)}))
    w._apply_text_style("normal")
    ck("切回正常后新面板文案恢复原文", lb.text() == "接口地址", lb.text())
    w.close()


# ---------------------------------------------------------------- 4.5 视觉本体（丙-4）

def t_visual() -> None:
    """鲸落视觉本体：画卷（ocean1+tint+三层波浪）与鱼光标 —— 参数对齐 web 真值。"""
    import ocean  # noqa: PLC0415
    import cursor_fx  # noqa: PLC0415

    # 模块级自检（参数真值 + 瓦片渲染 + 帧旋转）整批并入
    for name, ok, extra in ocean._selftest():
        ck("ocean · " + name, ok, extra)
    for name, ok, extra in cursor_fx._selftest():
        ck("cursor · " + name, ok, extra)

    # Shell 集成：whale 开画卷、窗口藏起就停、light 不启用
    from shell import Shell  # noqa: PLC0415
    from stylekit_qt import THEMES  # noqa: PLC0415

    w = Shell(THEMES["whale"])
    w.show()
    ck("画卷: whale 主题自动开启", w._backdrop_on is True)
    ck("画卷: 默认底图真实可读（ocean1.jpg）", w._wp_src is not None)
    ck("波浪: 30fps 上限（QTimer 33ms）在转", w._ocean.active and w._ocean._timer.interval() == 33)
    pm = w.grab()
    colors = {pm.toImage().pixelColor(x, y).rgba()
              for x in (300, 500, 700, 900) for y in (500, 600, 650)}
    ck("画卷: 离屏抓帧非平色（底图/波浪真画上了）", len(colors) >= 4, f"distinct={len(colors)}")
    w.hide()
    ck("波浪: 窗口藏起就停（CPU 纪律）", not w._ocean.active)
    ck("光标: Shell 已挂管理器（开=有底图 / 关=按配置）",
       w._cursor is not None and (w._cursor._base is not None or not w._cursor.enabled))
    w.close()

    w2 = Shell(THEMES["light"])
    w2.show()
    ck("画卷: light 主题不启用", w2._backdrop_on is False and not w2._ocean.active)
    w2.close()


# ---------------------------------------------------------------- 4.6 状态链路（丙-5 #0）

def t_status_chain() -> None:
    """真机首跑「全界面状态不明」的根因钉死在这里：
    base 自带 ?token= 时，path 必须**落在 query 之前**（rstrip 直拼会把
    /api/status 塞进 query → 服务端 401）。含真 socket 全真跑。"""
    import threading  # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: PLC0415

    from addr import join_url, resolve_base_url  # noqa: PLC0415
    import heal as heal_mod  # noqa: PLC0415

    # ① join_url 本体
    j = join_url("http://127.0.0.1:3210/?token=abc", "/api/status")
    ck("join_url: 带 token base → path 在 query 之前",
       j == "http://127.0.0.1:3210/api/status?token=abc", j)
    j2 = join_url("http://127.0.0.1:3210", "/api/status")
    ck("join_url: 无 token base 照常拼接",
       j2 == "http://127.0.0.1:3210/api/status", j2)

    # ② 两个真实拼接点都换用了 join_url；全 ui_qt 不许再留 rstrip 直拼
    hsrc = (HERE / "heal.py").read_text(encoding="utf-8")
    csrc = (HERE / "config_io.py").read_text(encoding="utf-8")
    ck("heal.probe_backend 走 join_url", "join_url(base, path)" in hsrc)
    ck("config_io.get_json 走 join_url", "join_url(base, api)" in csrc)
    bad = [p.name for p in HERE.glob("*.py")
           if re.search(r'rstrip\("/"\)\s*\+', p.read_text(encoding="utf-8", errors="replace"))]
    ck("ui_qt 全目录无 rstrip 直拼 URL 残留", not bad, ",".join(bad))

    # ③ 真 socket 全真跑：起真 HTTP 服务，probe_backend 用带 token 的 base 探它；
    #    服务端必须收到 path=/api/status、query=token=…（path 掉进 query 就是 401 现场）
    seen: dict = {}

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"] = self.path
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # 静音
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/?token=tk"
        pr = heal_mod.probe_backend(base, timeout=2.0)
        ck("真 socket: probe_backend 判 OK（path 正确落位）",
           pr.health.value == "ok", f"{pr.health.value} seen={seen.get('path')}")
        ck("真 socket: 服务端收到 /api/status?token=tk",
           seen.get("path") == "/api/status?token=tk", str(seen.get("path")))
    finally:
        srv.shutdown()

    # ④ resolve → join 链路：真 base（可能带 token）拼出的 URL，path 不进 query
    b, _src = resolve_base_url()
    j3 = join_url(b, "/api/status")
    ppos, qpos = j3.find("/api/status"), j3.find("?")
    ck("resolve→join 链路: path 在 query 之前",
       ppos != -1 and (qpos == -1 or ppos < qpos), j3)


# ---------------------------------------------------------------- 4.7 顶栏机器人控制（丙-5 #3）

def t_bot_controls() -> None:
    """顶栏「重启」「停止」：走 agent_bridge.post_api（join_url 口径，agent/ 零改动）；
    停止不可反悔 → 打字门槛 + 后果如实；「响应被急退切断也算送达」用真 socket 钉死。"""
    import socket as _sock  # noqa: PLC0415
    import threading  # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: PLC0415

    import agent_bridge  # noqa: PLC0415

    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")
    bsrc = (HERE / "agent_bridge.py").read_text(encoding="utf-8")

    def _method_src(src: str, name: str) -> str:
        i = src.find(f"def {name}(")
        if i < 0:
            return ""
        j = src.find("\n    def ", i + 1)
        return src[i:j if j > 0 else len(src)]

    # ① 走桥接助手，不手拼 URL；agent/ 一行不改
    #    （顶栏按钮经 _fire_api 间接调 post_api —— 助手内部统一走 post_api(api)）
    ck("顶栏重启走 _fire_api(/api/restart)→post_api", '_fire_api("/api/restart"' in ssrc)
    ck("顶栏停止走 _fire_api(/api/shutdown)→post_api", '_fire_api("/api/shutdown"' in ssrc)
    ck("_fire_api 统一走 post_api（不手拼 URL）", "post_api(api)" in ssrc)

    # ② 确认弹窗纪律：两个动作都过 ConfirmDialog；停止是危险钮 + 打字门槛
    rst = _method_src(ssrc, "_bot_restart")
    pst = _method_src(ssrc, "_bot_stop")
    ck("重启动作过 ConfirmDialog 且等 result_ok",
       "ConfirmDialog(" in rst and "result_ok" in rst)
    ck("停止动作过 ConfirmDialog 且等 result_ok",
       "ConfirmDialog(" in pst and "result_ok" in pst)
    ck("停止带打字门槛（不可一键反悔）", 'typed_word="停止"' in pst)
    ck("重启不是危险确认（动作可逆）", "dangerous=False" in rst)
    ck("停止是 danger 红钮", 'Btn("停止", self.t, "danger")' in ssrc)
    ck("停止后果如实：原生窗口会随之关闭", "原生窗口会随之关闭" in pst)
    ck("POST 不冻结界面（经 _fire_api 线程）", "_fire_api" in rst and "_fire_api" in pst)
    ck("失败要有说法（重启/停止失败都落徽章）", "重启失败" in rst and "停止失败" in pst)

    # ③ bridge 助手口径
    ck("post_api 存在且绕代理（ProxyHandler 空）",
       "def post_api(" in bsrc and "ProxyHandler({})" in bsrc)
    ck("post_api 拼接走 join_url（丙-5 #0 口径）", "join_url(base or current_url(), api)" in bsrc)
    ck("post_api 容忍急退断连（RemoteDisconnected 一族）", "RemoteDisconnected" in bsrc)

    # ④ 真 socket A：正常 200 —— 且用带 token 的 base 验 path 落位（401 现场）
    seen: dict = {}

    class _H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):  # 静音
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ok1, note1 = agent_bridge.post_api(
            "/api/restart", base=f"http://127.0.0.1:{srv.server_address[1]}/?token=tk", timeout=3.0)
        ck("真 socket: 正常 200 → 送达", ok1 and "200" in note1, note1)
        ck("真 socket: 服务端收到 /api/restart?token=tk",
           seen.get("path") == "/api/restart?token=tk", str(seen.get("path")))
    finally:
        srv.shutdown()

    # ④b 真 socket：401 鉴权被拒 → 必须判失败（动作没执行，不许骗「已送达」）
    class _H401(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):  # 静音
            pass

    srv401 = HTTPServer(("127.0.0.1", 0), _H401)
    threading.Thread(target=srv401.serve_forever, daemon=True).start()
    try:
        ok4, note4 = agent_bridge.post_api(
            "/api/restart", base=f"http://127.0.0.1:{srv401.server_address[1]}/", timeout=3.0)
        ck("真 socket: 401 被拒 → 判失败且带码", ok4 is False and "401" in note4, note4)
    finally:
        srv401.shutdown()

    # ⑤ 真 socket B：模拟 shutdown 急退 —— 后端读完请求、一个字节不回就断开
    #    （webui.py 的 shutdown_fn 是写完响应就 os._exit(0)，客户端几乎读不到完整响应）
    lst = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    lst.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    got: list = []

    def _cut() -> None:
        try:
            conn, _ = lst.accept()
            data = conn.recv(65536)
            got.append(bool(data) and data.startswith(b"POST /api/shutdown"))
            conn.close()   # 不回一个字节 —— os._exit(0) 的形态
        except Exception:
            got.append(False)

    threading.Thread(target=_cut, daemon=True).start()
    try:
        ok2, note2 = agent_bridge.post_api(
            "/api/shutdown", base=f"http://127.0.0.1:{lst.getsockname()[1]}/", timeout=3.0)
        ck("急退切断响应也算送达（ok=True）", ok2 is True, note2)
        ck("切断现场确实收到了 POST /api/shutdown", bool(got) and got[0], str(got))
    finally:
        lst.close()

    # ⑥ 拒绝连接 → 失败并给原因（bind 后立刻 close 的端口，理论上可能被抢，
    #    但毫秒级窗口内概率可忽略；真失败也说明环境有异常，值得报出来）
    dead = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    dead.bind(("127.0.0.1", 0))
    dport = dead.getsockname()[1]
    dead.close()
    ok3, note3 = agent_bridge.post_api("/api/restart", base=f"http://127.0.0.1:{dport}/", timeout=2.0)
    ck("拒绝连接 → 判失败并说明『没连上』", ok3 is False and "没连上" in note3, note3)


# ---------------------------------------------------------------- 4.8 窗口壳：无边框/托盘/图标（丙-5 #5 #6）

def t_window_chrome() -> None:
    """无边框窗口（对齐微信）：拖拽/resize/贴边走 WM_NCHITTEST 原生；
    删叉号 —— 「停止」承担关停、关窗=收进托盘（关窗≠停机）；
    任务栏图标 = 透明底完整鲸鱼。"""
    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")
    asrc = (HERE / "app.py").read_text(encoding="utf-8")

    # ① 无边框 + 原生手感
    ck("无边框已开（FramelessWindowHint）", "FramelessWindowHint" in ssrc)
    ck("WM_NCHITTEST 原生命中测试（0x0084）", "0x0084" in ssrc)
    ck("顶栏空白=HTCAPTION（原生拖拽/双击最大化/贴边）", '"caption"' in ssrc)
    ck("四边四角热区（原生 resize）", all(k in ssrc for k in
       ('"topleft"', '"topright"', '"bottomleft"', '"bottomright"')))
    ck("最小化/最大化钮已接线", "showMinimized" in ssrc and "def _toggle_max" in ssrc)

    # ② 删叉号：顶栏不许出现关闭钮；关窗语义在「停止」+ 托盘
    import re  # noqa: PLC0415
    close_btn = re.findall(r'Btn\(\s*"[×✕✖Xx]"', ssrc)
    ck("顶栏没有关闭叉钮（用户原话）", not close_btn, ",".join(close_btn))
    ck("停止钮存在（承担关停语义）", 'Btn("停止", self.t, "danger")' in ssrc)

    # ③ 关窗 ≠ 停机：closeEvent 拦截 → 托盘
    i = ssrc.find("def closeEvent(")
    j = ssrc.find("\n    def ", i + 1)
    cev = ssrc[i:j if j > 0 else len(ssrc)]
    ck("closeEvent 拦截（ev.ignore）", "ev.ignore()" in cev)
    ck("closeEvent 收进托盘（hide + 气泡）", "self.hide()" in cev and "showMessage" in cev)
    ck("托盘建立（QSystemTrayIcon）", "QSystemTrayIcon" in ssrc)
    ck("托盘菜单：显示主窗/停止", '"显示主窗"' in ssrc and '"停止"' in ssrc)
    ck("托盘停止走同一条确认流（_bot_stop）", "def _tray_stop" in ssrc and "_bot_stop()" in ssrc)
    ck("托盘不可用环境退回真关（不拦启动）", "ev.accept()" in cev)

    # ④ 任务栏图标（真机问题⑨）
    ck("Shell 窗口图标 = icon-whale.png", "icon-whale.png" in ssrc)
    ck("app 入口全局图标 = icon-whale.png",
       "setWindowIcon" in asrc and "icon-whale.png" in asrc)

    # ⑤ 海洋底全窗加固（真机问题⑥）：viewport/页面栈显式透明
    ck("画卷 viewport 显式透明加固", 'setStyleSheet("background:transparent;")' in ssrc
       and "viewport()" in ssrc)
    ck("页面栈显式透明加固", 'self.stack.setStyleSheet("background:transparent;")' in ssrc)


# ---------------------------------------------------------------- 4.9 叠字/收起语义/动效（丙-5 #7 #8 #9）

def t_dpi_motion() -> None:
    """#7 叠字根治 = 页面级滚动容器 + 行级 QFontMetrics 最小高；
    #8 收起态只显示图标（自绘 SVG）+ 组头首字缩略；
    #9 切换动效 150-180ms OutCubic、可打断、禁弹跳一族。"""
    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")
    wsrc = (HERE / "widgets.py").read_text(encoding="utf-8")
    isrc = (HERE / "icons.py").read_text(encoding="utf-8")

    # ① #7 根治：每页套 QScrollArea（web 版整页滚动的对齐物）
    ck("页面滚动容器存在（_wrap_scroll）", "def _wrap_scroll" in ssrc)
    ck("页栈所有页都套滚动容器", 'self.stack.addWidget(self._wrap_scroll(' in ssrc
       and "build_panel(self.t, sec, on_save=self._watch_config)" in ssrc)
    ck("滚动区 viewport 透明加固覆盖全部 QScrollArea",
       "self.findChildren(QScrollArea)" in ssrc)

    # ② #7 行级兜底：Field 最小行高按实测度量
    ck("Field 最小行高按 QFontMetrics 实测",
       "class Field" in wsrc and "QFontMetrics" in wsrc and "setMinimumHeight" in wsrc)
    ck("Segmented 度量取选中态 600 字重（高亮框内装的是它）",
       "fm_sel = QFontMetrics(qfont(t, 12.5, 600))" in wsrc)
    ck("Segmented 高度自适应行高（不写死 28）", "fm_sel.height() + 12" in wsrc)
    ck("Segmented knob 高随容器（不写死 24）", "self.height() - 4" in wsrc)

    # ③ #8 收起语义
    ck("双箭头自绘 SVG 存在（禁 emoji）", "CHEVS_R" in isrc and "CHEVS_L" in isrc
       and "def chevs_pixmap" in isrc)
    ck("收起态按钮只显示图标（清文字 +setIcon）",
       'self.btn_tight.setText("")' in ssrc and "self.btn_tight.setIcon(" in ssrc)
    ck("组头窄栏首字缩略（NavGroup.set_tight）",
       "def set_tight" in wsrc and "self.title[0]" in wsrc)

    # ④ #9 动效纪律
    ck("页面淡入 150ms", "setDuration(150)" in ssrc)
    ck("主题交叉淡入 180ms", "setDuration(180)" in ssrc)
    ck("缓动全库只用 OutCubic（禁弹跳/过冲一族）",
       "OutCubic" in ssrc and "Overshoot" not in ssrc and "OutBounce" not in ssrc
       and "OutElastic" not in ssrc)
    ck("交叉淡入可打断且不悬挂（veil 完成即清引用）",
       "def _crossfade_snapshot" in ssrc and "def _crossfade_play" in ssrc
       and "def _fade_veil_done" in ssrc)
    ck("淡入覆盖层事件穿透（不阻塞输入）", "WA_TransparentForMouseEvents" in ssrc)
    ck("页面 effect 用完即卸（离屏合成不留常驻成本）",
       'page.setGraphicsEffect(None)' in ssrc)


# ---------------------------------------------------------------- 丙-6 #11+#12：点头定因 + 滚轮模式

def t_wheel_nod() -> None:
    """#11 NOD_MS 可感知性修复（320ms）+ PM_CURSOR_NOD_DEBUG 定因开关（1500ms）；
    #12 PM_WHEEL 完整迁移：参数锁 web 真值、速度纯函数、中键直接进滚轮模式（不播转一圈）、
    五条件退出、光标 spin_to/restore 相位分帧、徽标不画第二条鱼。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    # ① #11 定因开关：默认 320（300-400 区间），debug=1 → 1500
    import cursor_fx  # noqa: PLC0415

    old = os.environ.get("PM_CURSOR_NOD_DEBUG")
    try:
        os.environ.pop("PM_CURSOR_NOD_DEBUG", None)
        ck("nod 默认 320ms（可感知性修复，300-400 区间）", cursor_fx._nod_ms() == 320,
           str(cursor_fx._nod_ms()))
        os.environ["PM_CURSOR_NOD_DEBUG"] = "1"
        ck("nod 定因开关 PM_CURSOR_NOD_DEBUG=1 → 1500ms", cursor_fx._nod_ms() == 1500,
           str(cursor_fx._nod_ms()))
        os.environ["PM_CURSOR_NOD_DEBUG"] = "0"
        ck("nod 定因开关非 1 值不触发", cursor_fx._nod_ms() == 320)
    finally:
        if old is None:
            os.environ.pop("PM_CURSOR_NOD_DEBUG", None)
        else:
            os.environ["PM_CURSOR_NOD_DEBUG"] = old

    # ② #12 参数锁 + 速度纯函数（pm_wheel 模块自检 13 条）
    import pm_wheel  # noqa: PLC0415

    for name, ok, extra in pm_wheel._selftest():
        ck("wheel · " + name, ok, extra)

    # ③ cursor_fx 委托（源码级）：中键直接进滚轮模式不播转一圈；活水事件全程喂
    csrc = (HERE / "cursor_fx.py").read_text(encoding="utf-8")
    ck("中键委托 WheelMode.toggle（裁决：不播转一圈）",
       "w.toggle(int(ev.globalPosition().x())" in csrc
       and 'self._mode != "spin"' in csrc)
    ck("无 WheelMode 时中键回退旧 spin()（兜底保留）", "if self._spin():" in csrc)
    ck("滚轮模式左键 = 退出 + 点头（web「按任意其它键退出」）",
       "elif w is not None and w.on:" in csrc and "w.stop()" in csrc)
    ck("MouseMove 全程喂 WheelMode（调速生命线）",
       "QEvent.Type.MouseMove and w is not None" in csrc and "w.on_move(" in csrc)
    ck("滚真实滚轮退出", "QEvent.Type.Wheel and w is not None" in csrc and "w.on_wheel()" in csrc)
    ck("Esc 退出", "Key_Escape" in csrc and "w.on_esc()" in csrc)
    ck("应用失焦退出（web window blur）",
       "QEvent.Type.ApplicationDeactivate" in csrc and "w.on_blur()" in csrc)
    ck("光标关了不接管（web enabled 边界）",
       'getattr(self._cursor, "enabled", False)' in (HERE / "pm_wheel.py").read_text(encoding="utf-8"))
    ck("光标关闭时滚轮模式一并退（干净语义）",
       "getattr(w, \"on\", False)" in csrc and "w.stop()" in csrc)

    # ④ 行为级：进场即滚 + 二次中键退出 + fallback 目标
    from stylekit_qt import WHALE  # noqa: PLC0415
    from PySide6.QtWidgets import QScrollArea  # noqa: PLC0415

    wc = cursor_fx.WhaleCursor(HERE.parents[0])
    page = QScrollArea()
    inner = QWidget()
    inner.setMinimumSize(200, 2000)              # 内容超一屏 ⇒ maximum>0 真可滚
    page.setWidget(inner)
    page.resize(200, 400)
    wm = pm_wheel.WheelMode(wc, WHALE, fallback=lambda: page)
    wm.start(50, 50)
    ck("滚轮模式进场即激活", wm.on is True)
    ck("widgetAt 不可用时回退主页面滚动区（web scrollerAt 兜底）", wm._target is page)
    wm._scroll(pm_wheel.BASE)
    ck("一 tick 即按 BASE 前进（一按就滚，不等鼠标动）",
       page.verticalScrollBar().value() >= 3,
       "value=%d" % page.verticalScrollBar().value())
    wm.toggle(50, 50)                            # 再按中键 = 退出
    ck("二次中键退出滚轮模式", wm.on is False)
    ck("退出后滚动目标清空（不残留半滚）", wm._target is None)
    ck("退出光标 restore 回底图（不残留半帧）",
       hasattr(cursor_fx.WhaleCursor, "restore") and hasattr(cursor_fx.WhaleCursor, "spin_to"))
    wm.on = True
    wm.on_wheel()
    ck("on_wheel 消费并退出", wm.on is False)
    wm.on = True
    wm.on_esc()
    ck("Esc 消费并退出", wm.on is False)
    wm.on = True
    wm.on_blur()
    ck("失焦退出", wm.on is False)

    # ⑤ 徽标：只标锚点+方向，不画第二条鱼（用户点单）
    bsrc = (HERE / "pm_wheel.py").read_text(encoding="utf-8")
    ck("徽标自绘圆盘 + 上下三角（web show 同款）",
       "drawEllipse" in bsrc and bsrc.count("drawPolygon") >= 2)
    ck("徽标不画第二条鱼（无 drawPixmap）", "drawPixmap" not in bsrc)
    ck("徽标颜色 token 派生（禁硬编码色值）",
       'self.t.q("blue")' in bsrc and 'self.t.q("bg")' in bsrc)
    ck("Shell 接线注入 WheelMode", "self._cursor.wheel = WheelMode(" in ssrc_shell())

    wc.set(False, custom=False)
    QLabel()  # 保持 import 不被裁


def ssrc_shell() -> str:
    return (HERE / "shell.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 丙-6 #13：更新公告条拆假接真

def t_updbar() -> None:
    """#13 假条拆除 + /api/update 四态机 + 三按钮真接线（join_url 口径）；
    #14 胶囊 + 下滑 popover 形态（丙-7）。"""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import updbar  # noqa: PLC0415

    # ① 四态机纯函数（web L817-837 逐条对照）
    n, w = updbar.decide({"status": "pending", "pending": ["a.py", "b.py", "c.py", "d.py"]})
    ck("updbar: pending 文案（半装补换 + 前 3 件名）",
       "4 件没换成" in n and "a.py" in n and "d.py" not in n and w is True, n[:50])
    n, w = updbar.decide({"status": "newer", "theirs": "2026.9.30", "mine": "2026.9.23",
                          "notes": ["修 A", "修 B"]})
    ck("updbar: newer 文案（真版本号 + notes · 连接）",
       "有新版本 2026.9.30" in n and "当前 2026.9.23" in n and "修 A · 修 B" in n and w is False, n[:50])
    n, w = updbar.decide({"status": "older", "theirs": "1.0"})
    ck("updbar: older 文案（源配错，warn）", "比本机旧" in n and w is True)
    n, w = updbar.decide({"status": "error", "why": "清单 404"})
    ck("updbar: error 文案（源异常，warn）", "更新源异常：清单 404" in n and w is True)
    n, _ = updbar.decide({"status": "current"})
    ck("updbar: current 隐藏（没有新版不出条，绝不放占位）", n == "")
    n, _ = updbar.decide({"status": "off"})
    ck("updbar: off（不再提醒生效）隐藏", n == "")
    n, _ = updbar.decide(None)
    ck("updbar: 拉取失败隐藏（不是假文案）", n == "")
    n, w = updbar.decide({"status": "current", "stateSaved": False, "stateSaveError": "盘满"})
    ck("updbar: 快照写失败独立追加（主态隐藏也出）",
       "没能写进快照" in n and "盘满" in n and w is True, n[:50])

    # ①.5 胶囊短文案纯函数（丙-7 #14：顶栏只留胶囊，无新版无异常不出现）
    p = updbar.pill({"status": "newer", "theirs": "9.9"})
    ck("pill: newer（真版本号）", p is not None and p[0] == "有新版本 9.9" and p[1] is False)
    p = updbar.pill({"status": "pending", "pending": ["x"]})
    ck("pill: pending（warn）", p is not None and "装了一半" in p[0] and p[1] is True)
    ck("pill: older（warn）", updbar.pill({"status": "older"}) is not None
       and updbar.pill({"status": "older"})[1] is True)
    ck("pill: error（warn）", updbar.pill({"status": "error"}) is not None
       and updbar.pill({"status": "error"})[1] is True)
    ck("pill: current/off 不出现", updbar.pill({"status": "current"}) is None
       and updbar.pill({"status": "off"}) is None and updbar.pill(None) is None)
    p = updbar.pill({"status": "current", "stateSaved": False})
    ck("pill: 快照失败照出（web 语义，读数没落盘必须看得见）",
       p is not None and "快照" in p[0] and p[1] is True)

    # ② 行为级：apply_state 落地 + 胶囊/面板填充 + skip 仅 newer + 稍后会话抑制
    from stylekit_qt import WHALE  # noqa: PLC0415

    bar = updbar.UpdateBar(WHALE)
    bar.apply_state({"status": "newer", "theirs": "9.9", "mine": "1.0",
                     "notes": ["修 A", "修 B"]})
    ck("updbar: newer 时胶囊可见（短文案）", bar.isVisible() and "9.9" in bar.txt.text())
    ck("updbar: 面板正文短版 + notes 逐条（明细不重复）",
       "9.9" in bar.detail.text() and "修 A" not in bar.detail.text()
       and "· 修 A" in bar.notes.text() and "· 修 B" in bar.notes.text())
    ck("updbar: 不再提醒仅 newer 允许（V-R4-1）", bar.btn_skip.isEnabled() is True)
    bar.apply_state({"status": "pending", "pending": ["x"]})
    ck("updbar: pending 时不再提醒被禁用", bar.btn_skip.isEnabled() is False)
    bar._on_later()
    ck("updbar: 稍后 → 胶囊隐藏", not bar.isVisible())
    bar.apply_state({"status": "pending", "pending": ["x"]})
    ck("updbar: 同版本稍后后不再弹（会话抑制）", not bar.isVisible())
    bar.apply_state({"status": "newer", "theirs": "10.0", "mine": "1.0"})
    ck("updbar: 出新版本照常弹（抑制不跨版本）", bar.isVisible())
    bar.apply_state(None)
    ck("updbar: 拉取失败 → 胶囊消失（不是灰着）", not bar.isVisible())
    bar._set_progress("正在下载 42%")
    ck("updbar: 进度双写（胶囊 + 面板同步）",
       bar.txt.text() == "正在下载 42%" and bar.prog.text() == "正在下载 42%"
       and not bar.prog.isHidden())   # isHidden：显式隐藏标志（面板未 show 时 isVisible 恒 False）
    # Popover 容器语义（工单 #14：220ms OutCubic 可打断；外点/Esc 收回由 Qt.Popup 白拿）
    psrc = (HERE / "popover.py").read_text(encoding="utf-8")
    ck("popover: Qt.Popup 旗标（点外/Esc 收回白拿）", "Qt.WindowType.Popup" in psrc)
    ck("popover: 220ms OutCubic（工单规格）", "DUR_MS = 220" in psrc
       and "OutCubic" in psrc and "QPropertyAnimation" in psrc)
    ck("popover: 动画可打断（重入 stop 旧动画，不叠两层透明度）",
       ".stop()" in psrc and "_op_anim" in psrc)

    # ③ 接线与口径（源码级）
    ssrc = ssrc_shell()
    ck("updbar: 假条文案已拆除（「有新版可用 · 3 项改进」不在壳源码）",
       "有新版可用 · 3 项改进" not in ssrc)
    ck("updbar: UpdateBar 接进顶栏（_rebuild 后 apply_state 恢复）",
       "self.updbar = UpdateBar(self.t)" in ssrc
       and 'apply_state(getattr(self, "_upd_state", None))' in ssrc)
    ck("updbar: 首拉挂 _upd_first_check（页面加载拉一次语义）",
       "def _upd_first_check" in ssrc and 'get_json("/api/update"' in ssrc)
    usrc = (HERE / "updbar.py").read_text(encoding="utf-8")
    ck("updbar: apply/skip 走 post_json（读回 JSON，why 逐字给用户）",
       'post_json("/api/update_apply"' in usrc and 'post_json("/api/update_skip"' in usrc)
    ck("updbar: 更新完成自动重启（web 同款）", 'post_api("/api/restart")' in usrc)
    ck("updbar: job 轮询 900ms（web 同款）", "setInterval(900)" in usrc)
    ck("updbar: 确认弹窗明说数据不动（可取消不破坏后台）",
       "ConfirmDialog" in usrc and "dangerous=False" in usrc and "not d.result_ok" in usrc)
    bsrc2 = (HERE / "agent_bridge.py").read_text(encoding="utf-8")
    ck("updbar: post_json 走 join_url 口径（丙-5 #0 教训，不手拼 base+path）",
       "def post_json" in bsrc2 and "join_url(base or current_url(), api)" in bsrc2)


# ---------------------------------------------------------------- 丙-7 #14+#15：外观图标收纳

def t_pop_look() -> None:
    """#15 外观切换图标 + popover 收纳（文案/主题两轴搬出顶栏）。"""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import icons  # noqa: PLC0415

    # ① 新图标：三条横向调节滑杆 + 圆点钮（中条偏右 = 经典「调节」语义）
    ap = icons.APPEARANCE
    ck("icons: APPEARANCE 三条横滑杆 + 三圆点",
       ap.count("M2.4") == 3 and ap.count("<circle") == 3 and 'cx="10.6"' in ap)
    ck("icons: 中条圆点偏右（调节语义）", 'cx="10.6" cy="8"' in ap)
    ck("icons: 圆头描边 1.6（20px 档 ≈2px 物理）",
       'stroke-width="1.6"' in ap and 'stroke-linecap="round"' in ap)
    pm = icons.appearance_pixmap("#65676B", 20)
    ck("icons: 20px 渲染非空", not pm.isNull() and pm.width() == 40)   # dpr=2

    # ② 顶栏收纳（源码级）：Segmented 从顶栏布局搬进 Popover
    ssrc = ssrc_shell()
    ck("look: style_seg/theme_seg 不再直挂顶栏布局",
       "lay.addWidget(self.style_seg)" not in ssrc and "lay.addWidget(self.theme_seg)" not in ssrc)
    ck("look: 两个 Segmented 复用现有控件、挂进 popover",
       "self._look_pop.add(self.style_seg)" in ssrc and "self._look_pop.add(self.theme_seg)" in ssrc)
    ck("look: 外观图标按钮接线（appearance_pixmap + toggle_at）",
       "self.btn_look = QPushButton" in ssrc and "appearance_pixmap" in ssrc
       and 'self._look_pop.toggle_at(self.btn_look' in ssrc)
    ck("look: changed 信号仍接原链路（切换行为不变）",
       'self.style_seg.changed.connect(self._apply_text_style)' in ssrc
       and 'self.theme_seg.changed.connect(self._switch_theme)' in ssrc)


# ---------------------------------------------------------------- 丙-7 #16+#17：暂停回归 + 窗口按钮重绘

def t_pause_win() -> None:
    """#16 暂停/恢复（web 真值 L755/L4180/L5714 全语义）+ #17 窗口控制重绘。"""
    ssrc = ssrc_shell()

    # ① 暂停按钮归位（web pauseBtn 一直在顶栏，丙-3~6 漏了）
    ck("pause: 按钮在顶栏（ghost 档，暂停≠停止不需要危险确认）",
       'self.btn_pause = Btn("暂停", self.t, "ghost")' in ssrc)
    ck("pause: 顺序 = 状态徽章 → 暂停 → 重启 → 停止（用户目标形态）",
       0 < ssrc.index("self.btn_pause = Btn") < ssrc.index("self.btn_restart = Btn")
       < ssrc.index('self.btn_stop = Btn'))

    # ② 方向唯一依据 = paused 字段（web L4180 血泪注释：不许读按钮文字做依据）
    ck("pause: 方向 = not self._paused（wantPaused = 这一下想要的结果状态）",
       "want = not self._paused" in ssrc)
    ck("pause: 源码里没有读按钮文字判方向（web 血泪禁令）",
       "btn_pause.text()" not in ssrc and "btn_pause.text() ==" not in ssrc)
    ck("pause: busy 防重入 + 「暂停中…/恢复中…」",
       "self._pause_busy = True" in ssrc and "恢复中…" in ssrc and "暂停中…" in ssrc)

    # ③ API 链路 + 恢复补刀（V-R10-24 两把钥匙一起清）
    ck("pause: POST /api/pause 与 /api/resume 二选一",
       '"/api/pause" if want else "/api/resume"' in ssrc)
    ck("pause: 恢复路径补刀 /api/risk recover（web L5719-5721 同款）",
       '"/api/risk"' in ssrc and '"action": "recover"' in ssrc)
    ck("pause: 补刀失败不遮主结果（try 包裹）",
       "_recover" in ssrc and "except Exception" in ssrc)

    # ④ 状态来源：/api/status 轮询 + 徽章如实
    ck("pause: paused 从 /api/status 读（独立 8 秒轻轮询，web loadStatus 同款间隔）",
       'get_json("/api/status"' in ssrc and "setInterval(8000)" in ssrc
       and 'bool(s.get("paused"))' in ssrc)
    ck("pause: 成功就地翻转不等轮询（web 同款）",
       "self._paused = want" in ssrc)
    ck("pause: 失败恢复原状 + 如实报错（QToolTip = web toast 的 Qt 等价）",
       "self._apply_paused(self._paused)" in ssrc and "没切成：" in ssrc)
    ck("pause: 探活徽章感知暂停（进程活着但不回消息 → 「已暂停」）",
       'txt = "已暂停"' in ssrc)

    # ⑤ 窗口控制重绘（#17：更简约、笔画粗一点）
    wsrc = (HERE / "widgets.py").read_text(encoding="utf-8")
    ck("win: IconBtn 自绘（QAbstractButton 基类 + paintEvent）",
       "class IconBtn(QAbstractButton)" in wsrc and "def paintEvent" in wsrc)
    ck("win: 笔画 2px 圆头（工单规格）",
       "setWidthF(2.0)" in wsrc and "RoundCap" in wsrc)
    ck("win: hover 浅底圆角 + token 化（tx2 常态 / tx hover）",
       'rgba(self.t.q("tx"), 16)' in wsrc
       and 'self.t.q("tx") if self._hover else self.t.q("tx2")' in wsrc)
    ck("win: 最大化/还原双态（还原 = 双直角框交叠）",
       "isMaximized" in wsrc and "drawRect" in wsrc and "drawLine" in wsrc)
    ck("win: 旧文字按钮已拆（「—」「□」Btn 不在壳源码）",
       'Btn("—"' not in ssrc and 'Btn("□"' not in ssrc)
    ck("win: 新按钮接进顶栏（WM_NCHITTEST 分支不动）",
       "self.btn_min = IconBtn" in ssrc and "self.btn_max = IconBtn" in ssrc
       and "WM_NCHITTEST" in ssrc)
    ck("win: 最大化态切换跟随窗口（resize 钩子盖住双击顶栏的原生最大化）",
       "btn.update()" in ssrc and "def resizeEvent" in ssrc)


# ---------------------------------------------------------------- 5. 纪律：不碰产品代码

def t_no_touch() -> None:
    root = HERE.parents[0]
    # 落位版（ui_qt/）：断言换成「正式壳目录在产品根下」（原型版钉的是「在 _scratch 下」）
    ck("落位目录在产品根下（ui_qt）", HERE.name == "ui_qt" and (root / "agent").is_dir())
    ck("没有 import 产品的写接口（只读桥接）",
       "save_config" not in (HERE / "agent_bridge.py").read_text(encoding="utf-8"))
    # offline/wheels 不许被污染 —— 打包是离线优先的
    whl = root / "offline" / "wheels"
    has_pyside = any("side" in p.name.lower() or "qt" in p.name.lower() for p in whl.glob("*.whl")) if whl.exists() else False
    ck("PySide6 没被塞进 offline/wheels（不进打包）", not has_pyside)


# ---------------------------------------------------------------- 主

def main() -> int:
    for fn in (t_syntax, t_nav, t_themes, t_runtime_render, t_fonts_rgba, t_usability, t_panels,
               t_visual, t_status_chain, t_bot_controls, t_window_chrome, t_dpi_motion,
               t_wheel_nod, t_updbar, t_pop_look, t_pause_win, t_no_touch):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            ck(f"{fn.__name__} 执行未抛异常", False, f"{type(e).__name__}: {e}"[:110])

    bad = [r for r in ROWS if not r[1]]
    for name, ok, extra in ROWS:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(ROWS) - len(bad)} 通过 / {len(bad)} 失败")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
