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
    import py_compile # noqa: PLC0415

    for f in ("stylekit_qt.py", "widgets.py", "confirm.py", "heal.py", "shell.py", "agent_bridge.py",
              "panels_qt.py", "sec_meta.py"):
        p = HERE / f
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(HERE / "__pycache__" / (f + "c")))
            ck(f"{f} 语法通过", True)
        except Exception as e: # noqa: BLE001
            ck(f"{f} 语法通过", False, str(e)[:90])


# ---------------------------------------------------------------- 2. 导航结构与 web 侧同构

def _nav_truth_from_web() -> list[tuple[str, str, list[tuple[str, str]]]]:
    """运行时解析 agent/console_html.py 的 <nav id=nav>，拿到导航真值。

    这是「与 web 侧一致」的唯一权威来源。旧版判据把组名手抄在 selftest 里
    （want_groups = ["天天用", ...]），原型自己编一套比喻命名也能绿 —— 假对齐。
    组名/组序/项名/项序/sec 锚点全部以 web 源码为准全等比对。
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
    from shell import NAV # noqa: PLC0415

    src = (HERE / "shell.py").read_text(encoding="utf-8")
    wid = (HERE / "widgets.py").read_text(encoding="utf-8")

    flat = [e[0] for _t, _k, es in NAV for e in es]
    ck("导航项数 = 27", len(flat) == 27, f"实际 {len(flat)}")

    # ★ 真对齐：以 web 源码解析结果为唯一真值，全等比对。
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

    # 「机器人」必须排在第 3 位 —— web 侧 ui_arch_selftest 有同一条硬断言
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

    # ★ 导航图标
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

    # ★ 机器人主面板接真配置：原型硬编码值（群DeepSeek/开机自启摆设行）退役
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

    # ★ 顶栏
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
    from stylekit_qt import THEMES # noqa: PLC0415

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
    # ⚠️ 判据改版：
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

    # ★ 危险按钮字色 err_tx
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
    """渲染产物级断言 —— 源码级断言会骗人：

    · danger 按钮源码写了 err_tx，但 rgba() 返回的 QColor 对象插进 QSS
      f-string 变成垃圾值 → 整条规则解析失败 → 黑字沉底，源码断言照样绿；
    · whale tx2/tx3 是 rgba() 串，QtSvg 不认 → 整套导航图标"隐形"，同上。
    ⇒ 对这类缺陷，只有"摸渲染结果"的断言作数。
    """
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    from icons import INNER, svg_pixmap # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn, NavItem # noqa: PLC0415

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
    """用户的双字体（大字=朝華標題A / 小字=屏显臻宋）
    + 坑⑥（QColor 不认 CSS rgba() 串）的修复断言 —— 「读取中」黑字的根。"""
    from PySide6.QtGui import QColor # noqa: PLC0415
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    from stylekit_qt import THEMES, _c, ensure_fonts, qfont, status_colors # noqa: PLC0415

    QApplication.instance() or QApplication([]) # 坑④同族：字体操作前必须有 app

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
    """ 第 2 棒的纪律：9 张新面板真建出来、真接进切换、真跟鲸语。

    与 web 侧 ui_arch_selftest 同一条硬判据 —— 面板↔导航一一对应；
    并且对"源码级断言会骗人"的教训照单全收：这里全部**摸真实控件**。
    """
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QLabel # noqa: PLC0415

    from panels_custom import MANUAL # noqa: PLC0415
    from panels_qt import BATCH_SECS, build_panel # noqa: PLC0415
    from sec_meta import secs # noqa: PLC0415
    from shell import NAV, Shell # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

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
    ck("惰性构建：初始页栈只建 bot 主面板（页数 = 1）", st.count() == 1, str(st.count()))
    ck("未建 sec 全部记入 _lazy（bot 除外）",
       w._lazy == set(BATCH_SECS) - {"bot"}, str(sorted(w._lazy)[:4]) + "…")
    ck("bot 已入 _page_of 映射", w._page_of == {"bot": 0}, str(w._page_of))
    n_before = st.count()
    w._go("overview", "概览") # 首访：现场构建
    QApplication.processEvents()
    ck("首访 sec 现场构建（页数 1 → 2）", st.count() == n_before + 1, str(st.count()))
    ck("已建 sec 出 _lazy 入 _page_of",
       "overview" in w._page_of and "overview" not in w._lazy,
       f"page_of={sorted(w._page_of)} lazy_n={len(w._lazy)}")
    ck("重复 _go 不再重建（幂等，页数不变）",
       (w._go("overview", "概览"), QApplication.processEvents(), st.count() == n_before + 1)[2],
       str(st.count()))
    p1 = st.currentWidget()
    w._go("model", "模型")
    QApplication.processEvents()
    p2 = st.currentWidget()
    ck("导航点击真正换页（当前页变为目标页）",
       p2 is st.widget(w._page_of["model"]) and p2 is not p1)
    ck("旧页不残留（切换后旧页隐藏）", p1.isHidden())
    for s in BATCH_SECS: # 全量走一遍：27 页全部就位（一次性预建等价语义）
        w._go(s, s)
    QApplication.processEvents()
    ck("遍历全部 sec 后页栈页数 = 27（惰性预建闭环）",
       st.count() == 27 and not w._lazy, f"count={st.count()} lazy_n={len(w._lazy)}")

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


# ---------------------------------------------------------------- 4.5 视觉本体

def t_visual() -> None:
    """鲸落视觉本体：画卷（ocean1+tint+三层波浪）与鱼光标 —— 参数对齐 web 真值。"""
    import ocean # noqa: PLC0415
    import cursor_fx # noqa: PLC0415

    # 模块级自检（参数真值 + 瓦片渲染 + 帧旋转）整批并入
    # ⚠️ I：波浪动效已砍，OceanWaves/瓦片仅作为设计资产与
    #    取证对象存在 —— 参数对齐断言保留，产品渲染路径不再经过它。
    for name, ok, extra in ocean._selftest():
        ck("ocean · " + name, ok, extra)
    for name, ok, extra in cursor_fx._selftest():
        ck("cursor · " + name, ok, extra)

    # Shell 集成：whale 开画卷（静底图）、波浪永停、light 不启用
    from shell import Shell # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    w = Shell(THEMES["whale"])
    w.show()
    ck("画卷: whale 主题自动开启", w._backdrop_on is True)
    ck("画卷: 默认底图真实可读（ocean1.jpg）", w._wp_src is not None)
    ck("波浪: 动效已砍——QTimer 永不转",
       not w._ocean.active, f"active={w._ocean.active}")
    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")
    osrc = (HERE / "ocean.py").read_text(encoding="utf-8")
    ck("波浪: 产品路径全部硬关（refresh/show 都是 set_active(False)）",
       "self._ocean.set_active(False) # 波浪动效已砍：任何主题都不再转" in ssrc
       and "self._ocean.set_active(False) # 波浪动效已砍，show 也不再转" in ssrc)
    body = re.sub(r'""".*?"""', "",
                  osrc.split("def paint_backdrop")[1].split("def _selftest")[0],
                  flags=re.S)
    ck("波浪: paint_backdrop 不再画波浪帧（只剩底图+tint）",
       ".paint(" not in body)
    pm = w.grab()
    colors = {pm.toImage().pixelColor(x, y).rgba()
              for x in (300, 500, 700, 900) for y in (500, 600, 650)}
    ck("画卷: 离屏抓帧非平色（静底图/tint 真画上了）", len(colors) >= 4, f"distinct={len(colors)}")
    w.hide()
    ck("波浪: 窗口藏起仍停（CPU 纪律）", not w._ocean.active)
    ck("光标: Shell 已挂管理器（开=有底图 / 关=按配置）",
       w._cursor is not None and (w._cursor._base is not None or not w._cursor.enabled))
    w.close()

    w2 = Shell(THEMES["light"])
    w2.show()
    ck("画卷: light 主题不启用", w2._backdrop_on is False and not w2._ocean.active)
    w2.close()


# ---------------------------------------------------------------- 4.6 状态链路

def t_badges() -> None:
    """ P0-A①⑤：面板徽章全量接线。
    旧病根：Badge 建出来后全文件无 set 调用 —— 用户永远看到「读取中」。
    现在 badge_for = web refreshBadges（console_html.py L3302-3427）的 Qt
    口径，同一份 /api/status 推导；Shell._poll_badges 8 秒分发。
    这里断言状态机核心分支 + 接线源码（端到端取证另有 _c8_badgeprobe.py）。"""
    import panels_qt as pq # noqa: PLC0415

    bf = pq.badge_for
    # ① 状态机（web refreshBadges 分支逐一对照；绝不编数：拿不到→idle）
    S_RUN = {"paused": False, "wechat_connected": True,
             "listen": {"groups": 3, "privates": 2},
             "model": {"configured": True, "name": "deepseek-chat"},
             "tts": {"ready": True}, "image_gen": {"ready": False, "why": "没填"},
             "web_search": {"provider": "tavily", "enabled": True},
             "tools": {"count": 12, "enabled": 5},
             "version_gate": {"allow": True, "level": "ok"}}
    ck("徽章 bot 运行中=ok 已就绪", bf("bot", S_RUN)[:2] == ("ok", "已就绪"))
    ck("徽章 wechat 在线监听 5 个", bf("wechat", S_RUN)[:2] == ("ok", "已连接 · 监听 5 个"))
    ck("徽章 model 截 10 字", bf("model", S_RUN)[:2] == ("ok", "deepseek-c"))
    ck("徽章 tts 可用 / imggen 缺一步",
       bf("tts", S_RUN)[:2] == ("ok", "可用") and bf("imggen", S_RUN)[:2] == ("warn", "缺一步"))
    ck("徽章 search provider 截 8 字", bf("search", S_RUN)[:2] == ("ok", "tavily"))
    ck("徽章 tools 5 / 12 开", bf("tools", S_RUN)[:2] == ("ok", "5 / 12 开"))
    ck("徽章 vermat 已实测", bf("vermat", S_RUN)[:2] == ("ok", "已实测"))
    ck("徽章 overview/log/sessions 交给面板自刷新（不双写）",
       bf("overview", S_RUN) is None and bf("log", S_RUN) is None
       and bf("sessions", S_RUN) is None)

    S_PAUSE = {"paused": True, "wechat_connected": False}
    ck("徽章 bot 停着=warn / wechat 没连上=err",
       bf("bot", S_PAUSE)[:2] == ("warn", "停着") and bf("wechat", S_PAUSE)[:2] == ("err", "没连上"))
    S_ZERO = {"paused": False, "wechat_connected": True,
              "listen": {"groups": 0, "privates": 0}}
    ck("徽章 零监听=warn 要勾群", bf("wechat", S_ZERO)[:2] == ("warn", "要勾群"))
    S_NOKEY = {"model": {"configured": False}}
    ck("徽章 没填密钥=err", bf("model", S_NOKEY)[:2] == ("err", "没填密钥"))
    S_GATE = {"version_gate": {"allow": False}, "version": {"wechat": "3.9.12"}}
    ck("徽章 版本门拦停=err", bf("vermat", S_GATE)[:2] == ("err", "拦停"))
    S_OFF = {"tools": {"count": 12, "enabled": 0},
             "web_search": {"provider": "bing", "enabled": False}}
    ck("徽章 全关/关着=idle（不写 ok）",
       bf("tools", S_OFF)[:2] == ("idle", "0 / 12 开") and bf("search", S_OFF)[:2] == ("idle", "关着"))
    ck("徽章 空 status 不编数（info 未检测）", bf("model", {})[:2] == ("info", "未检测"))

    # ② 接线源码：轮询/分发/引用挂载/旧病根消除
    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")
    wsrc = (HERE / "widgets.py").read_text(encoding="utf-8")
    psrc = (HERE / "panels_qt.py").read_text(encoding="utf-8")
    csrc = (HERE / "panels_custom.py").read_text(encoding="utf-8")
    ck("Shell._poll_badges 8 秒定时器已接线",
       "_badge_timer" in ssrc and "_apply_badges" in ssrc)
    ck("徽章引用全量挂载（page/wrap._c8_badge）",
       "_c8_badge" in psrc and "_c8_badge" in csrc and "wrap._c8_badge" in psrc)
    ck("Badge.set 存 level（探针/断言可读）", "self.level = level" in wsrc)
    ck("旧病根已除：_cfg_panel 徽章建后即 set", 'badge.set("info", "已加载")' in psrc)
    ck("旧病根已除：_page 返回 badge 引用", "return page, lay, bd" in csrc)
    ck("保存成败同步到徽章", '"已保存"' in psrc and '"没保存成"' in psrc)
    ck("体检页两主按钮真接后台 API（code-check/progress + selfcheck/stop）",
       all(k in csrc for k in ("/api/code-check", "/api/code-check/progress",
                               "/api/selfcheck", "/api/selfcheck-stop")))
    ck("体检页停止钮初始禁用（对齐 web selfCheckStop disabled）",
       'b_stop.setEnabled(False)' in csrc)
    ck("检测中心并入症状检验器（verifiers/verify/四档判决/复制报告，web vfBtns 同款）",
       all(k in csrc for k in ("/api/verifiers", "/api/verify?id=",
                               "复制报告", "部分通过", "没测到")))
    ck("检测中心并入拍一拍完整版（目标群下拉/简易检测默认勾/误拍警示）",
       all(k in csrc for k in ("/api/wechat-groups", "verify_only", "简易检测",
                               "拍到其他群友的风险")))
    ck("表内 pokeTest 默认简易检测（verify_only=True 防误拍，对齐 web 默认勾选）",
       '"pokeTest": ("POST", "/api/poke-test", {"verify_only": True})' in psrc)
    ck("表内 selfCheck 按 status 统计（返回是 status 非 ok 字段，旧口径恒显失败 0）",
       'c.get("status") == "fail"' in psrc and 'c.get("status") == "warn"' in psrc)
    ck("概览补监听群明细+费用计算器（/api/status.groups 表格 + /api/prices 官方价目计算）",
       all(k in csrc for k in ("监听群明细", "QTableWidget", "/api/prices",
                               "月成本", "fc_peak")) or
       all(k in csrc for k in ("监听群明细", "/api/prices", "月成本")))
    ck("APPENDIX 注册 tools/model 追加区（工具清单 + 本机模型探测+厂商联动）",
       '"tools": _tools_utlist_appendix' in csrc
       and '"model": (_model_local_appendix, _model_provider_linkup)' in csrc)
    ck("工具清单契约（/api/tools/toggle GET 勾选切换 + problems 坏清单 + counts_total 统计行）",
       all(k in csrc for k in ("/api/tools/toggle", "problems", "counts_total")))
    ck("本机模型探测契约（/api/local-models + 用这个回填 api.base_url + 连通测试）",
       all(k in csrc for k in ("/api/local-models", "api.base_url", "连通测试", "_c8_binds")))
    import re as _re # noqa: PLC0415
    _gaps = _re.findall(r"hooks\s*=\s*\[[^\]]*\bNone\b[^\]]*\]", csrc)
    ck("按钮 hooks 无 None 残留（「点了没反应」缺口=0；移除的按钮已删）",
       not _gaps, "; ".join(g[:60] for g in _gaps)[:110])
    ck("危险操作有二次确认（cal_clear/clear_sessions 走 ConfirmDialog）",
       "ConfirmDialog" in csrc and "confirm_label=" in csrc)
    # ③ 落盘验证 + 改完即生效（P0-A④：web autoApplyChk 等价开关）
    ck("保存后真读回 config.json 验证（_verify_on_disk，非 mock）",
       "_verify_on_disk" in psrc and "已验证落盘" in psrc)
    ck("改完即生效开关（QSettings 本机记忆，默认开=web 同款）",
       "auto_chk" in psrc and 'QSettings("WXAgent", "persona-morph-ui")' in psrc)
    ck("自动生效防抖（600ms 单发，改动→write_patch 同链路）",
       "debounce" in psrc and "setSingleShot(True)" in psrc)


def t_status_chain() -> None:
    """真机首跑「全界面状态不明」的根因钉死在这里：
    base 自带 ?token= 时，path 必须**落在 query 之前**（rstrip 直拼会把
    /api/status 塞进 query → 服务端 401）。含真 socket 全真跑。"""
    import threading # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    from addr import join_url, resolve_base_url # noqa: PLC0415
    import heal as heal_mod # noqa: PLC0415

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

        def log_message(self, *a): # 静音
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


# ---------------------------------------------------------------- 4.7 顶栏机器人控制

def t_bot_controls() -> None:
    """顶栏「重启」「停止」：走 agent_bridge.post_api（join_url 口径，agent/ 零改动）；
    停止不可反悔 → 打字门槛 + 后果如实；「响应被急退切断也算送达」用真 socket 钉死。"""
    import socket as _sock # noqa: PLC0415
    import threading # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    import agent_bridge # noqa: PLC0415

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
    ck("停止无打字门槛",
       'typed_word' not in pst and '"确定停止"' in pst)
    ck("重启不是危险确认（动作可逆）", "dangerous=False" in rst)
    ck("停止是 danger 红钮", 'Btn("停止", self.t, "danger")' in ssrc)
    ck("停止后果如实：原生窗口会随之关闭", "原生窗口会随之关闭" in pst)
    ck("POST 不冻结界面（经 _fire_api 线程）", "_fire_api" in rst and "_fire_api" in pst)
    ck("失败要有说法（重启/停止失败都落徽章）", "重启失败" in rst and "停止失败" in pst)

    # ③ bridge 助手口径
    ck("post_api 存在且绕代理（ProxyHandler 空）",
       "def post_api(" in bsrc and "ProxyHandler({})" in bsrc)
    ck("post_api 拼接走 join_url", "join_url(base or current_url(), api)" in bsrc)
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

        def log_message(self, *a): # 静音
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

        def log_message(self, *a): # 静音
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
            conn.close() # 不回一个字节 —— os._exit(0) 的形态
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


# ---------------------------------------------------------------- 4.8 窗口壳：无边框/托盘/图标

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
    ck("圆角窗口：Win11 DWM 属性",
       "def _apply_round_corners" in ssrc
       and "DwmSetWindowAttribute" in ssrc
       and "ctypes.c_int(2)" in ssrc # DWMWCP_ROUND
       and "ctypes.c_void_p(hwnd), 33" in ssrc)
    ck("圆角窗口：Win10/非 Windows 优雅回退（try 包裹，不引 region 锯齿方案）",
       "SetWindowRgn(" not in ssrc # 只许 docstring 提及，不许真调用（带括号）
       and "方角即回退" in ssrc.split("def _apply_round_corners")[1])
    ck("圆角窗口：接进 showEvent（窗口句柄就绪后生效）",
       "self._apply_round_corners()" in ssrc.split("def showEvent")[1]
       .split("def ")[0])

    # ② 删叉号：顶栏不许出现关闭钮；关窗语义在「停止」+ 托盘
    import re # noqa: PLC0415
    close_btn = re.findall(r'Btn\(\s*"[×✕✖Xx]"', ssrc)
    ck("顶栏没有关闭叉钮", not close_btn, ",".join(close_btn))
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


# ---------------------------------------------------------------- 4.9 叠字/收起语义/动效

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

    # ③ #8 → E 收起语义：纯图标（不留首字）
    ck("双箭头自绘 SVG 存在（禁 emoji）", "CHEVS_R" in isrc and "CHEVS_L" in isrc
       and "def chevs_pixmap" in isrc)
    ck("收起态按钮只显示图标（清文字 +setIcon）",
       'self.btn_tight.setText("")' in ssrc and "self.btn_tight.setIcon(" in ssrc)
    ck("组头窄栏整行隐藏",
       "def set_tight" in wsrc and "self.hd.setVisible(not tight)" in wsrc
       and "self.title[0]" not in wsrc)
    ck("导航项窄栏纯图标（清文字+图标放大 16→20）",
       "def set_tight" in wsrc and 'self.setText("")' in wsrc
       and "setIconSize(QSize(20, 20))" in wsrc)
    ck("窄栏行距加宽（间隔稍微变大）",
       "self.bl.setSpacing(4 if tight else 1)" in wsrc)

    # ③b L：按钮按压态「一眼可辨」（底色压一档 + 描边同步加深 + 压字 1px）
    ck("Btn pressed 底色往字色轴压一档（primary mix blue2→tx 0.22）",
       "QPushButton:pressed{" in wsrc
       and "bg_p = mix(t.q(\"blue2\"), t.q(\"tx\"), 0.22)" in wsrc
       and "press_border = mix(t.q(\"blue2\"), t.q(\"tx\"), 0.45)" in wsrc)
    ck("Btn pressed 三角色都有独立按压描边（ghost/primary/danger）",
       wsrc.count("QPushButton:pressed{") >= 1 # Btn._qss 三角色共用模板
       and "press_border = rgba(t.q(\"err\"), 170)" in wsrc
       and "press_border = mix(t.q(\"blue\"), t.q(\"tx\"), 0.30)" in wsrc)
    ck("Btn pressed 保留压字 1px（padding 上+1 下-1，QSS 无 transform 的等价物）",
       "padding-top:8px;padding-bottom:6px" in wsrc)
    ck("NavItem 非 active 按压加深（tx 14→26 两档）",
       wsrc.count("QPushButton:pressed{") >= 3 # Btn + NavItem + Segmented×2
       and "rgba(t.q('tx'), 0 if t.glass else 26)" in wsrc)
    ck("Segmented 按压给色反馈（off→blue）",
       wsrc.count("QPushButton:pressed{{color:{t.blue};}}") >= 2)

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


# ---------------------------------------------------------------- #11+#12：点头定因 + 滚轮模式

def t_wheel_nod() -> None:
    """#11→ K NOD_MS 定因（默认 400ms + 帧 1.3x）；PM_CURSOR_NOD_DEBUG 定因开关（1500ms）；
    #12 PM_WHEEL 完整迁移：参数锁 web 真值、速度纯函数、中键直接进滚轮模式（不播转一圈）、
    五条件退出、光标 spin_to/restore 相位分帧、徽标不画第二条鱼。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QWidget # noqa: PLC0415

    QApplication.instance() or QApplication([])

    # ① #11→ K 定因开关：默认 400+ 帧 1.3x，debug=1 → 1500
    import cursor_fx # noqa: PLC0415

    old = os.environ.get("PM_CURSOR_NOD_DEBUG")
    try:
        os.environ.pop("PM_CURSOR_NOD_DEBUG", None)
        ck("nod 默认 400ms",
           cursor_fx._nod_ms() == 400, str(cursor_fx._nod_ms()))
        os.environ["PM_CURSOR_NOD_DEBUG"] = "1"
        ck("nod 定因开关 PM_CURSOR_NOD_DEBUG=1 → 1500ms", cursor_fx._nod_ms() == 1500,
           str(cursor_fx._nod_ms()))
        os.environ["PM_CURSOR_NOD_DEBUG"] = "0"
        ck("nod 定因开关非 1 值不触发", cursor_fx._nod_ms() == 400)
        ck("nod 歪头帧放大 1.3x",
           cursor_fx.NOD_SCALE == 1.3 and "_to_nod_cursor" in
           (HERE / "cursor_fx.py").read_text(encoding="utf-8"))
    finally:
        if old is None:
            os.environ.pop("PM_CURSOR_NOD_DEBUG", None)
        else:
            os.environ["PM_CURSOR_NOD_DEBUG"] = old

    # ② #12 参数锁 + 速度纯函数（pm_wheel 模块自检 13 条）
    import pm_wheel # noqa: PLC0415

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
    from stylekit_qt import WHALE # noqa: PLC0415
    from PySide6.QtWidgets import QScrollArea # noqa: PLC0415

    wc = cursor_fx.WhaleCursor(HERE.parents[0])
    page = QScrollArea()
    inner = QWidget()
    inner.setMinimumSize(200, 2000) # 内容超一屏 ⇒ maximum>0 真可滚
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
    wm.toggle(50, 50) # 再按中键 = 退出
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
    QLabel() # 保持 import 不被裁


def ssrc_shell() -> str:
    return (HERE / "shell.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- #13：更新公告条拆假接真

def t_updbar() -> None:
    """#13 假条拆除 + /api/update 四态机 + 三按钮真接线（join_url 口径）；
    #14 胶囊 + 下滑 popover 形态。"""
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import updbar # noqa: PLC0415

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

    # ①.5 胶囊短文案纯函数
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
    from stylekit_qt import WHALE # noqa: PLC0415

    bar = updbar.UpdateBar(WHALE)
    bar.apply_state({"status": "newer", "theirs": "9.9", "mine": "1.0",
                     "notes": ["修 A", "修 B"]})
    ck("updbar: newer 时胶囊可见（短文案）", bar.isVisible() and "9.9" in bar.txt.text())
    ck("updbar: 面板正文短版 + notes 逐条（明细不重复）",
       "9.9" in bar.detail.text() and "修 A" not in bar.detail.text()
       and "· 修 A" in bar.notes.text() and "· 修 B" in bar.notes.text())
    ck("updbar: 不再提醒仅 newer 允许", bar.btn_skip.isEnabled() is True)
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
       and not bar.prog.isHidden()) # isHidden：显式隐藏标志（面板未 show 时 isVisible 恒 False）
    psrc = (HERE / "popover.py").read_text(encoding="utf-8")
    ck("popover: Qt.Popup 旗标（点外/Esc 收回白拿）", "Qt.WindowType.Popup" in psrc)
    ck("popover: 220ms OutCubic", "DUR_MS = 220" in psrc
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
    ck("updbar: post_json 走 join_url 口径",
       "def post_json" in bsrc2 and "join_url(base or current_url(), api)" in bsrc2)


# ---------------------------------------------------------------- #14+#15：外观图标收纳

def t_pop_look() -> None:
    """#15 外观切换图标 + popover 收纳（文案/主题两轴搬出顶栏）。"""
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import icons # noqa: PLC0415

    # ① 新图标：三条横向调节滑杆 + 圆点钮（中条偏右 = 经典「调节」语义）
    ap = icons.APPEARANCE
    ck("icons: APPEARANCE 三条横滑杆 + 三圆点",
       ap.count("M2.4") == 3 and ap.count("<circle") == 3 and 'cx="10.6"' in ap)
    ck("icons: 中条圆点偏右（调节语义）", 'cx="10.6" cy="8"' in ap)
    ck("icons: 圆头描边 2.0",
       'stroke-width="2.0"' in ap and 'stroke-linecap="round"' in ap)
    pm = icons.appearance_pixmap("#65676B", 20)
    ck("icons: 20px 渲染非空", not pm.isNull() and pm.width() == 40) # dpr=2

    # ①b F：顶栏外观图标用全亮字色
    ck("icons: 外观图标色调=tx（与最小化/全屏同亮档）",
       "appearance_pixmap(self.t.tx, 20)" in (HERE / "shell.py").read_text(encoding="utf-8"))

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


# ---------------------------------------------------------------- #16+#17：暂停回归 + 窗口按钮重绘

def t_pause_win() -> None:
    """#16 暂停/恢复（web 真值 L755/L4180/L5714 全语义）+ #17 窗口控制重绘。"""
    ssrc = ssrc_shell()

    # ① 暂停按钮归位
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

    # ③ API 链路 + 恢复补刀
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
    ck("win: 笔画 2px 圆头",
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


# ---------------------------------------------------------------- 6. 自举收口：32 位判别

def t_bootstrap32() -> None:
    """qt_bootstrap._selftest 全量收录（真跑，不 mock 网络）。

    核心：32 位 Python 在任何 pip 动作之前被拒（PySide6 wheel 只有 win_amd64，
     兼容性审计第③项实锤）—— 判据 struct.calcsize("P")==4（指针字节数）。
    """
    root = HERE.parents[0]
    sys.path.insert(0, str(root))
    try:
        import qt_bootstrap # noqa: PLC0415

        for name, ok, extra in qt_bootstrap._selftest():
            ck(f"bootstrap: {name}", ok, extra[:90])
    except Exception as e: # noqa: BLE001
        ck("bootstrap 自检可执行", False,
           "【本段未跑完，其后断言全部未执行】" + f"{type(e).__name__}: {e}"[:110])
    finally:
        sys.path.remove(str(root))


# ---------------------------------------------------------------- 主

def t_ocr9() -> None:
    """ A 批（agent/chat_ocr）：双引擎封装 / 时间戳容错 / 预处理管线。

    纪律：只真跑**纯函数**（错字映射、编辑距离、行匹配、PIL 合成图管线），
    绝不碰 `_rapid_engine()` / `_rapid_bootstrap()` —— 那会在 RapidOCR 缺席时
    触发真自举（pip 下载百 MB），自检不允许有网络副作用。

    ⛔ ADV-5（静默吞断言）：依赖导入一律提到 try **外面**。PIL 写进 try 里时，
    缺依赖会抛 ModuleNotFoundError 被 `except` 吞成一条 fail，**其后 15 条 `ck()`
    被吞的断言），结论行看起来「只是少一条」而实际是「一大段没跑」= 伪装通过。
    提到外面 ⇒ 缺依赖**立刻硬失败**（tk 起不来、进程非 0 退出），不再伪装。
    """
    from PIL import Image # noqa: PLC0415  # ADV-5：必须在 try 之外，缺依赖要硬失败

    root = HERE.parents[0]
    sys.path.insert(0, str(root))
    try:
        import agent.chat_ocr as co # noqa: PLC0415

        src = (root / "agent" / "chat_ocr.py").read_text(encoding="utf-8")

        def body(name: str) -> str:
            m = re.search(r"def %s\b.*?(?=\ndef |\Z)" % re.escape(name), src, re.S)
            return re.sub(r'""".*?"""', "", m.group(0), flags=re.S) if m else ""

        # A1 双引擎封装（源码形态断言一律剔 docstring，防断言自咬）
        ck("ocr: recognize_dual 封装存在", bool(body("recognize_dual")))
        ck("ocr: _rapid_recognize 封装存在", bool(body("_rapid_recognize")))
        ck("ocr: rapidocr_status 记账口存在", bool(body("rapidocr_status")))
        d = body("recognize_dual")
        m_win = re.search(r"(?<![A-Za-z_])recognize\(", d)
        m_rap = d.find("_rapid_recognize(")
        m_blk = d.find("if blocked():")
        ck("ocr: 串联顺序 WinRT 先跑（recognize 先于 _rapid_recognize）",
           bool(m_win) and 0 <= m_win.start() < m_rap, str((m_win.start() if m_win else -1, m_rap)))
        ck("ocr: 熔断/预算用尽时第二引擎短路", 0 <= m_blk < m_rap and "return []" in d,
           str((m_blk, m_rap)))
        ck("ocr: 自举状态默认未尝试（自检不触发真自举）", co._rapid_state.get("tried") is False)

        # A2 时间戳读数容错（纯函数真跑）
        ck("ocr: 错字映射函数存在", bool(body("_fix_time_typo")) and "_TYPO_TRANS" in src)
        ck("ocr: 编辑距离函数存在", bool(body("_edit_dist_le1")))
        ck("ocr: 真跑 O9:4O 命中 want=9:40", co.row_time_match("O9:4O", "9:40"))
        ck("ocr: 真跑 19:4O 命中 want=19:40", co.row_time_match("19:4O", "19:40"))
        ck("ocr: 容错上限距离 1（距离 2 仍拒）", not co.row_time_match("xx 9:53 yy", "9:41"))
        ck("ocr: 严格相等口径不回归", co.row_time_match("xx 19：41 yy", "19:41"))
        ck("ocr: row_time_read 也是先修后提取", "_fix_time_typo(txt)" in body("row_time_read"))

        # B 预处理管线（PIL 合成图真跑）
        ck("ocr: preprocess_ink 管线存在", bool(body("preprocess_ink")))
        ck("ocr: 二值图放大用最近邻采样", "Image.NEAREST" in body("preprocess_ink"))
        ck("ocr: Otsu 阈值函数存在", bool(body("_otsu_thresh")))
        for fn in ("header_text", "name_of_row", "_band_name", "row_time_read"):
            ck("ocr: 强档路径 %s 接入预处理管线" % fn, "preprocess_ink(" in body(fn))
        ck("ocr: pane_text 只换双引擎不叠管线", "recognize_dual(" in body("pane_text")
           and "preprocess_ink(" not in body("pane_text"))

        gimg = Image.new("RGB", (60, 20), (81, 167, 116))
        for _x in range(6, 14):
            for _y in range(6, 14):
                gimg.putpixel((_x, _y), (255, 255, 255))
        bw = co.preprocess_ink(gimg, zoom=2, invert=True)
        ck("ocr: 管线真跑 2x 放大", bw.size == (120, 40), str(bw.size))
        ck("ocr: 管线真跑输出纯黑白", set(c[1] for c in bw.convert("L").getcolors()) <= {0, 255})
        ck("ocr: Otsu 阈值在合理区间", 40 <= co._otsu_thresh(gimg.convert("L")) <= 220)

        # C 批：发送链去冗余
        src_w = (root / "agent" / "wechat.py").read_text(encoding="utf-8")

        def wbody(name: str) -> str:
            m = re.search(r"def %s\b.*?(?=\n    def |\Z)" % re.escape(name), src_w, re.S)
            return re.sub(r'""".*?"""', "", m.group(0), flags=re.S) if m else ""

        sp = wbody("send_text_posted")
        i_idn = sp.find("_idn9, _idwhy9 = self.chat_identity_ok")
        i_else = sp.find("else:", i_idn) if i_idn >= 0 else -1
        i_strong = sp.find("_ok_strong, _why_strong = self.chat_is_open")
        ck("ocr9C: mismatch 分支先问 G9 内容级复核（chat_identity_ok 前置）", i_idn >= 0, str(i_idn))
        ck("ocr9C: G9 True 直接放行（短路分支内不再调 chat_is_open）",
           0 <= i_idn < i_else and "chat_is_open" not in sp[i_idn:i_else], str((i_idn, i_else)))
        ck("ocr9C: G9 非 True 才落到 G10 强档链（原退回链保留）",
           0 <= i_else < i_strong, str((i_else, i_strong)))
        ck("ocr9C: G8 降日志——旧「指纹单独拒发」文案已死",
           'return False, "【可重试】会话头不匹配' not in sp)
        ck("ocr9C: 新拒发理由以强档结论为准（指纹仅观测）",
           "强档证据" in sp and "也给不出" in sp and "指纹 mismatch（观测：" in sp)
        ck("ocr9C: 底线——no_ref 四档链与 mismatch 强档放行原样保留",
           "四档证据也都给不出" in sp and "强档证据成立" in sp)

        # D2 批：真鼠标兜底——默认关未变 + 引导四件落位
        import json as _json

        _cfgex = _json.loads((root / "config.example.json").read_text(encoding="utf-8"))
        ck("ocr9D: config.example 真鼠标兜底默认 false 未变",
           _cfgex.get("input", {}).get("allow_real_fallback") is False)
        ck("ocr9D: 讲人话注释键已落位（≥40 字）",
           len(str(_cfgex.get("input", {}).get("_note_allow_real_fallback", ""))) >= 40)
        ck("ocr9D: web 控制台有真鼠标兜底开关（data-cfg 绑定）",
           'data-cfg="input.allow_real_fallback"'
           in (root / "agent" / "console_html.py").read_text(encoding="utf-8"))
        ck("ocr9D: 新文案同步进 whale_text 字典",
           "允许真鼠标兜底" in (root / "agent" / "whale_text.py").read_text(encoding="utf-8"))
        _rd = (root / "使用说明.md").read_text(encoding="utf-8")
        ck("ocr9D: 使用说明有「消息发不出去」自查小节", "消息发不出去" in _rd and "真鼠标兜底" in _rd)
        ck("ocr9D: config.py 默认值 false 未变",
           '"allow_real_fallback": False' in (root / "agent" / "config.py").read_text(encoding="utf-8"))
    except Exception as e: # noqa: BLE001
        ck("ocr9 自检可执行", False,
           "【本段未跑完，其后断言全部未执行】" + f"{type(e).__name__}: {e}"[:110])
    finally:
        sys.path.remove(str(root))


# ---------------------------------------------------------------- 7. 真机

def t_c10() -> None:
    """ 真机五条 P0 + P1 的**可断言的形态**钉在这里。

    真机证据（窗口拖动/波纹实拍）另有取证脚本 —— 自检只钉「代码形态与模块
    解耦」这类不依赖屏幕的东西，绝不冒充真机验收。
    """
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")
    msrc = (HERE / "sec_meta.py").read_text(encoding="utf-8")
    lsrc = (HERE / "panels_qt.py").read_text(encoding="utf-8")
    csrc = (HERE / "panels_custom.py").read_text(encoding="utf-8")
    usrc = (HERE / "updbar.py").read_text(encoding="utf-8")

    def _body(src: str, name: str, indent: int = 4) -> str:
        i = src.find(f"def {name}(")
        if i < 0:
            return ""
        j = src.find("\n" + " " * indent + "def ", i + 1)
        return src[i:j if j > 0 else len(src)]

    # ── P0-1：_rebuild 里 _refresh_backdrop 必须**先于** _restyle（顺序即根因）
    rb = _body(ssrc, "_rebuild")
    i_ref = rb.find("self._refresh_backdrop()")
    i_rst = rb.find("self._restyle()")
    ck("c10P1: _rebuild 里 _refresh_backdrop 先于 _restyle（主题残留根因）",
       0 <= i_ref < i_rst, str((i_ref, i_rst)))
    ck("c10P1: 顺序修复带说明注释（防被后人「顺手」倒回去）",
       "P0-1" in rb and "_backdrop_on" in rb)

    # ── P0-4：真机两个致命根因都钉住（offscreen 单测测不到，必须源码级防回归）
    #   ⚠️ 只在**代码行**上判（剥掉注释）——我特意把错误写法也写在注释里做说明，
    # 直接对全文断言会「打脸自己」。
    nat = _body(ssrc, "nativeEvent")
    _nat_code = "\n".join(ln for ln in nat.splitlines() if not ln.strip().startswith("#"))
    ck("c10P4: QCursor 从 QtGui 导入（原来错写 QtCore → 真机每次 ImportError）",
       "from PySide6.QtGui import QCursor" in _nat_code
       and "from PySide6.QtCore import QCursor" not in _nat_code)
    ck("c10P4: _HT 索引用 r（_hit_test 返回键名串）——不再错写 r[0]（KeyError:'t'）",
       "self._HT[r]" in _nat_code and "self._HT[r[0]]" not in _nat_code)
    ck("c10P4: _hit_test 明示返回 _HT 键名（调用方按键取值）",
       "返回 _HT 键名" in _body(ssrc, "_hit_test"))
    # 右缘滚动条例外：整条右缘豁免会让 right/bottomright 死区（team-lead 扫描 6/8）
    # 返工滚动条纵向覆盖整个内容区（高 660），按 childAt 判仍把热区吞掉 ⇒
    # 改为「滚动条只让出自己那 6px 本体 + 右缘热区向左加宽 + 角点优先」。
    ht = _body(ssrc, "_hit_test")
    ck("c10P4: 右缘用 _sb_x_left() 几何反查滚动条（不用 childAt —— 它只在滚动条本体那 6px 才返回滚动条）",
       "def _sb_x_left(" in ht and "findChildren(QScrollBar)" in ht)
    ck("c10P4: 右缘热区被滚动条挡住时向左加宽（保证仍有 m 宽可拉）",
       "right_edge = sb_left - m if sb_left is not None else w - m" in ht
       and "min(w - m, right_edge)" in ht)
    ck("c10P4: 滚动条本体仍放行（on_sb_body；压在滚动条上要能滚）",
       "on_sb_body = " in ht and "gp.x() >= sb_left" in ht)
    ck("c10P4: 缩放角优先于滚动条例外（at_corner 参与 on_sb_body 判定）",
       "at_corner = " in ht and "and not at_corner" in ht)
    ck("c10P4: 四角优先于四边（topleft/topright/bottomleft/bottomright 都在）",
       all(('"%s"' % k) in ht for k in
           ("topleft", "topright", "bottomleft", "bottomright", "left", "right", "top", "bottom")))

    # ── P0-2：解析层三新类型 + 渲染层三接线 + binds 门槛
    ck("c10P2: _parse_nonwidget_row 存在（无控件块不再一律 info）",
       "def _parse_nonwidget_row(" in msrc)
    ck("c10P2: 四档判定 table > buttons > status > info 齐备",
       all(k in msrc for k in ('Row("table"', 'Row("buttons"', 'Row("status"', 'Row("info"')))
    ck("c10P2: Row 扩了 actions/status_id/headers/rows 四字段",
       all(k in msrc for k in ("actions:", "status_id:", "headers:", "rows:")))
    ck("c10P2: 渲染层三新 kind 都接了控件",
       all(k in lsrc for k in ('r.kind == "buttons"', 'r.kind == "status"', 'r.kind == "table"')))
    ck("c10P2: 渲染层三构造器存在（_btn_group/_status_chip/_table_row）",
       all(("def " + n + "(") in lsrc for n in ("_btn_group", "_status_chip", "_table_row")))
    ck("c10P2: 展示型仍挡在 binds 外（_WRITABLE_KINDS 门槛）；无 cfg 可写行进 binds 但保存侧跳过（_collect: if not r.cfg）",
       "r.kind in _WRITABLE_KINDS" in _body(lsrc, "_row")
       and "if not r.cfg:" in _body(lsrc, "_collect"))

    # 真跑 P0-4：直调 _hit_test 验「右缘中段能缩放」+「滚动条本体留滚动」两立
    # 
    def _c10p4_real() -> tuple:
        import os as _os # noqa: PLC0415

        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint, Qt # noqa: PLC0415
        from PySide6.QtWidgets import QApplication, QScrollBar # noqa: PLC0415

        from shell import Shell # noqa: PLC0415
        from stylekit_qt import THEMES, ensure_fonts # noqa: PLC0415

        app = QApplication.instance() or QApplication([])
        ensure_fonts()
        sh = Shell(THEMES["light"])
        sh.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        sh.resize(1100, 720)
        sh.show()
        app.processEvents()
        W, H = sh.width(), sh.height()
        res = {
            "右下角": sh._hit_test(QPoint(W - 2, H - 2)),
            # W-6 可能正压滚动条本体（体宽 6px，右缘贴边）⇒ 用刚好落在
            # 「滚动条左侧加宽热区」内的点验 right
            "右缘中段": sh._hit_test(QPoint(W - 10, H // 2)),
            "左缘": sh._hit_test(QPoint(2, H // 2)),
        }
        # 滚动条本体的左边界（用它自己那 6px 判「本体仍放行」）
        sb_l = None
        for sb in sh.findChildren(QScrollBar):
            if sb.isVisible() and sb.orientation() == Qt.Orientation.Vertical:
                x0 = sb.mapTo(sh, sb.rect().topLeft()).x()
                if x0 + sb.width() > W - 8:
                    sb_l = x0
        res["_sb_left"] = sb_l
        res["滚动条本体"] = None if sb_l is None else sh._hit_test(QPoint(sb_l + 1, H // 2))
        res["_sb_max"] = max([s.maximum() for s in sh.findChildren(QScrollBar)
                              if s.isVisible()] or [0])
        sh.close()
        app.processEvents()
        return res

    _p4 = _c10p4_real()
    ck("c10P4 真跑: 右下角 → bottomright（返工前是死区 None）",
       _p4["右下角"] == "bottomright", str(_p4["右下角"]))
    ck("c10P4 真跑: 右缘热区在中段 → right（返工前整条被滚动条吞成 None）",
       _p4["右缘中段"] == "right", str(_p4["右缘中段"]))
    ck("c10P4 真跑: 左缘 → left（未被右缘改动误伤）",
       _p4["左缘"] == "left", str(_p4["左缘"]))
    ck("c10P4 真跑: 滚动条本体仍放行 → None（压在滚动条上必须能滚）",
       _p4["_sb_left"] is not None and _p4["滚动条本体"] is None
       and _p4["_sb_max"] > 0,
       f"sb_left={_p4['_sb_left']} body={_p4['滚动条本体']} max={_p4['_sb_max']}")

    # 真跑：三类 web 样本块 → 解析出正确 kind（不靠读源码）
    import sec_meta # noqa: PLC0415

    r_btn = sec_meta._parse_row(
        '<div class="row-btns"><button id="btnGo">开始</button>'
        '<button data-act="stop">停止</button></div>', "操作")
    ck("c10P2: 真跑 <button> 块 → kind=buttons 且抽出 2 个动作",
       r_btn.kind == "buttons" and len(r_btn.actions) == 2,
       f"{r_btn.kind}/{r_btn.actions}")
    ck("c10P2: 真跑 buttons 动作 id 取 id=/data-act=（btnGo / stop）",
       [a[1] for a in r_btn.actions] == ["btnGo", "stop"], str(r_btn.actions))
    r_st = sec_meta._parse_row('<div>微信版本：<b id="wxver">读取中</b></div>', "版本")
    ck("c10P2: 真跑状态行 <b id=…> → kind=status 且带 status_id",
       r_st.kind == "status" and r_st.status_id == "wxver", f"{r_st.kind}/{r_st.status_id}")
    r_tb = sec_meta._parse_row(
        '<table><thead><tr><th>能力</th><th>状态</th></tr></thead>'
        '<tbody><tr><td>发消息</td><td>可用</td></tr></tbody></table>', "矩阵")
    ck("c10P2: 真跑 <table> → kind=table 且表头/数据行都提出来",
       r_tb.kind == "table" and r_tb.headers == ["能力", "状态"] and r_tb.rows == [["发消息", "可用"]],
       f"{r_tb.kind}/{r_tb.headers}/{r_tb.rows}")
    r_in = sec_meta._parse_row("<p>下面这段只是说明文字，没有控件。</p>", "说明")
    ck("c10P2: 真跑纯说明块 → 仍是 info（没把说明错判成按钮）",
       r_in.kind == "info", r_in.kind)

    # ── P0-3：vermat 面板 = 版本与更新卡 + 能力矩阵（真值非「检测中」占位）
    ck("c10P3: vermat_panel 已登记进 MANUAL", '"vermat": vermat_panel' in csrc)
    # ⚠️ vermat_panel 内有大量 4 空格缩进的**嵌套函数**，用 4 空格边界会截断 ——
    #    取到下一个顶层 def（0 缩进）为止。
    _i = csrc.find("def vermat_panel(")
    _j = csrc.find("\ndef ", _i + 1)
    vp = csrc[_i:_j if _j > 0 else len(csrc)]
    ck("c10P3: 含「版本与更新」常驻卡（更新入口挪到版本页）", 'h2(t, "版本与更新")' in vp)
    ck("c10P3: 含「能力矩阵」卡", 'h2(t, "能力矩阵")' in vp)
    ck("c10P3: 矩阵读 /api/status 真值（version_gate + version）",
       "_load_status()" in vp and "version_gate" in vp and "version" in vp)
    ck("c10P3: 三态口径（allowed/已实测/拦停）+ 本次允许发送入口（真 GET /api/version/allow）",
       "已实测" in vp and "拦停" in vp and "/api/version/allow" in vp)
    ck("c10P3: 更新卡复用 updbar.decide（不重写一套判定）", "updbar.decide" in vp)
    # 教训：agent_bridge 从未有 get_json——旧断言锁「from agent_bridge import get_json」，
    # 等于把静默失败的错误写法锁成了规范（断言与 bug 互相锁死）。现口径：GET 走 config_io。
    ck("c10P3: L2 缺陷已除——GET 走 config_io.get_json / POST 走 agent_bridge.post_json",
       "config_io.get_json" in vp and "from agent_bridge import get_json" not in vp
       and vp.count("post_json(") <= vp.count("from agent_bridge import post_json"))
    ck("c10P3: 无死代码（空 desc 占位已删）", 'empty = desc(t, "")' not in vp)

    # ── P0-5：波纹模块存在且**与背景海浪解耦**（不共用 Timer / 不碰 OceanWaves）
    wsrc = (HERE / "wavefx.py").read_text(encoding="utf-8")
    ck("c10P5: wavefx.WaveFX 独立模块存在", "class WaveFX(" in wsrc)
    ck("c10P5: 波纹持自己的 QTimer（不借背景海浪的）",
       "self._timer = QTimer(self)" in wsrc)
    # 两个实锤 bug 的形态防回归（_c10_wave_diag / _c10_verify2 取证）
    ck("c10P5: _last_move_t 初值取当前时刻（0.0 会让首次 dt 巨大→能量恒锁地板）",
       "self._last_move_t = time.monotonic()" in wsrc)
    ck("c10P5: 相位用绝对时间驱动（epoch 起、%1.0 环形推进——位移扭曲的时间轴）",
       "(time.monotonic() - self._epoch)" in wsrc and "% 1.0" in wsrc)
    # 位移版没有「shimmer/环半径」概念（那是画圆环时代的）——对应护栏换成了这两条：
    ck("c10P5: 位移 mask 幅度恒夹 [0,1]（np.clip）且带 0.35 底噪（空白处也有可见起伏）",
       "np.clip(a, 0.0, 1.0)" in wsrc and "(1.0 - r) ** falloff * 0.35" in wsrc)
    # 能量归一不再借 max_gain 当分母（那会让 667px/s 就饱和 ⇒ 慢手/快手无差别）
    ck("c10P5: energy 归一用参考速度 _V_REF（不是 max_gain 当分母→不再恒满档）",
       "_V_REF = 400.0" in wsrc and "self._speed_ema / _V_REF" in wsrc)
    ck("c10P5: 速度过 EMA 平滑（对齐 web _mouseSpeed*0.7+v*0.3）",
       "_EMA_KEEP = 0.7" in wsrc and "self._speed_ema * _EMA_KEEP" in wsrc)
    ck("c10P5: 位移版无环半径概念——性能护栏是缩采 _PROC_SCALE=0.5（全窗位移会掉帧，实测 0.5x 稳 30fps）",
       "_PROC_SCALE = 0.5" in wsrc)
    # 解耦硬判据看**代码**（剥掉 docstring）——docstring 里提 OceanWaves 是解释性说明，
    #  "提到过" ≠ "依赖它"；真依赖会出现 `import ocean` / `OceanWaves(` 调用。
    _wsrc_code = re.sub(r'""".*?"""', "", wsrc, flags=re.S)
    ck("c10P5: 波纹模块代码零依赖 OceanWaves（解耦硬判据）",
       "OceanWaves" not in _wsrc_code and "import ocean" not in _wsrc_code,
       " / ".join(ln.strip() for ln in _wsrc_code.splitlines() if "OceanWaves" in ln)[:80])
    ck("c10P5: Shell.paintEvent 在内容之上画波纹",
       "_wavefx.paint(" in _body(ssrc, "paintEvent"))
    ck("c10P5: Shell.mouseMoveEvent 转发光标（投石）",
       "_wave_from_pos(" in _body(ssrc, "mouseMoveEvent"))
    # 内容层会先吃 mouseMove ⇒ 必须开 mouseTracking + 应用级兜住鼠标移动
    ck("c10P5: 开 mouseTracking（否则波纹只在按住拖动时才动）",
       "self.setMouseTracking(True)" in ssrc)
    ck("c10P5: 应用级事件过滤兜住内容层鼠标移动（eventFilter→_wave_from_pos）",
       "def eventFilter(" in ssrc and "_wave_from_pos(" in _body(ssrc, "eventFilter")
       and "installEventFilter(self)" in ssrc)
    ck("c10P5: 参数即时生效（_watch_config 调 refresh_from_config）",
       "refresh_from_config()" in ssrc)
    # 真跑：开关只动自己，背景海浪（_ocean）与波纹互不牵连
    from PySide6.QtCore import QPointF, Qt # noqa: PLC0415
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    QApplication.instance() or QApplication([])
    from shell import Shell # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    w = Shell(THEMES["whale"])
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.show()
    QApplication.processEvents()
    ocean_active_before = w._ocean.active
    w._wavefx.set_enabled(False)
    ck("c10P5: 关波纹不牵连背景海浪（_ocean 状态不变）",
       w._ocean.active == ocean_active_before and not w._wavefx.active,
       f"ocean={w._ocean.active} wave={w._wavefx.active}")
    w._wavefx.set_enabled(True)
    ck("c10P5: 开波纹只启自己的 Timer", w._wavefx.active and not w._ocean.active,
       f"ocean={w._ocean.active} wave={w._wavefx.active}")
    w._wavefx.on_mouse_move(QPointF(120, 90))
    ck("c10P5: 鼠标移动更新波纹中心（真跑 on_mouse_move）",
       (round(w._wavefx._pos.x()), round(w._wavefx._pos.y())) == (120, 90),
       f"{w._wavefx._pos.x()},{w._wavefx._pos.y()}")
    # 能量必须**响应速度**（原 bug：恒被地板锁 0.25，快慢一个样）
    import time as _tm # noqa: PLC0415

    _tm.sleep(0.05)
    w._wavefx.on_mouse_move(QPointF(420, 90)) # 一次大步（≈6000px/s）
    e_fast = w._wavefx._energy
    ck("c10P5: 快速移动能量真上去（不再恒锁地板 0.25）", e_fast > 0.5, f"energy={e_fast:.3f}")
    # 相位由绝对时间驱动 → 位移核心真跑：真实 QImage 进 _displace_region，产出**同尺寸**
    # 全分辨率扭曲图。
    # （绘制已改走 QGraphicsEffect.draw，paint() 不再画环——旧 max_alpha 采样断言随之改写。）
    from PySide6.QtCore import QRect # noqa: PLC0415
    from PySide6.QtGui import QImage # noqa: PLC0415

    _src_img = QImage(200, 160, QImage.Format.Format_RGBA8888)
    _src_img.fill(Qt.GlobalColor.darkGray)
    _rect = QRect(0, 0, 200, 160)
    w._wavefx._pos = QPointF(100.0, 80.0)
    _out = w._wavefx._displace_region(
        _src_img, _rect, w._wavefx._pos,
        w._wavefx._norm_radius(w._wavefx._pos, _rect),
        w._wavefx._mask_phase())
    ck("c10P5: 位移核心真跑产出扭曲图（与入图同尺寸、非空——全分辨率合成）",
       _out is not None and _out.width() == 200 and _out.height() == 160,
       f"out={None if _out is None else (_out.width(), _out.height())}")
    # ── 波纹「一点动静都没有」三连根因的形态防回归（_c31_waveprobe2 像素实锤
    #    diff_lens=39.6万/相位间 4.4万 ⇒ 修后真可见。任一断言复发 = 回到零视觉）
    ck("c31: paintEvent 内禁 .grab(（paint 期间抓父窗=重入；且 grab 产 QPixmap 混进 "
      "QImage 链路 convertToFormat/bits 每帧 AttributeError 被吞 ⇒ 零视觉）",
       ".grab(" not in _body(wsrc, "paintEvent"))
    ck("c31: paintEvent 消费 _src_img（抓源在 _tick 事件循环态 render 进 QImage）",
       "wf._src_img" in _body(wsrc, "paintEvent") and "_grab_src()" in _body(wsrc, "_tick")
       and "def _grab_src(" in wsrc)
    ck("c31: sync_overlay 已定义且被 shell.resizeEvent 调（漏接线 ⇒ overlay 几何停在创建那一刻）",
       "def sync_overlay(" in wsrc and "sync_overlay()" in _body(ssrc, "resizeEvent"))
    ck("c31: 透镜命中走 _hit_deep（排除 overlay 自身；childAt 恒返全窗 overlay ⇒ "
      "_blank_mode 恒 False、透镜恒全窗）",
       "def _hit_deep(" in wsrc and "_hit_deep(" in _body(wsrc, "_blank_mode")
       and "_hit_deep(" in _body(wsrc, "_lens_rect"))
    # ── 侧栏状态框 1:1 复刻（web .side .status，console_html.py L517-519/L929）
    ck("c31: 状态框描边用 blue_line token（web --blue-line；此前拿 blue 当边框⇒一圈亮蓝）",
       "self.t.blue_line" in _body(ssrc, "_build_side"))
    ck("c31: 状态正文恒 tx2 灰",
       "color:{self.t.tx2}" in _body(ssrc, "_apply_side_status")
       and "self.t.ok if" not in _body(ssrc, "_apply_side_status"))
    # ── 波纹「顶栏右段无特效 + 方形分界线」二连根因防回归
    ck("c32: 模块矩形=确定性爬树（卡片优先→shell 直接子兜底；旧「关键词+尺寸」启发式"
      "让顶栏内 wrapper 截胡透镜 ⇒ 连接徽章左沿以右整条没特效）",
       "big_enough" not in _body(wsrc, "_module_rect_of")
       and "is_container" not in _body(wsrc, "_module_rect_of")
       and "Card" in _body(wsrc, "_module_rect_of")
       and "parent is shell" in _body(wsrc, "_module_rect_of"))
    ck("c32: 降级裁剪盘与模块矩形求交（波纹绝不越出模块边界——web fitLensToHost 语义）",
       "intersected(QRectF(mod))" in _body(wsrc, "_grab_src"))
    ck("c32: 位移在 bbox 边缘 smoothstep 渐隐归零（_EDGE_FADE——环带扫边=硬分界线根因）",
       "_EDGE_FADE" in wsrc and "3.0 - 2.0 * ef" in _body(wsrc, "_displace_region"))
    ck("c32: mask 与原图按 web maskImage 语义合成（m==0 处逐像素原图——缩采回拉的"
      "重采样差异沿 rect 边一圈「方形接缝」的根因）",
       "a16 * (255 - mf)" in _body(wsrc, "_displace_region"))
    ck("c32: 侧栏状态框左右各缩 10px",
       "swl.setContentsMargins(10, 0, 10, 0)" in _body(ssrc, "_build_side"))
    # ── 波纹观感对齐 web 真值──
    ck("c33: 噪声=fractalNoise 等价的平滑 value noise（格点 seed 固定+smoothstep 插值；"
      "旧 3 八度 sin/cos+逐帧 min/max 归一化 ⇒ 近均匀随机 ⇒ 细碎刮痕）",
       "def _fractal_noise(" in wsrc and "RandomState" in _body(wsrc, "_fractal_noise")
       and "_fractal_noise(" in _body(wsrc, "_displace_region"))
    ck("c33: baseFrequency 逐帧呼吸（web :6646-6647 同款公式；feTurbulence 每帧重算=图案呼吸）",
       "0.008 + 0.004 * math.sin" in _body(wsrc, "_displace_region")
       and "0.011 + 0.005 * math.cos" in _body(wsrc, "_displace_region"))
    ck("c33: 位移场不乘 mask（web feDisplacementMap 全区域扭曲、mask 只管显示混合；"
      "旧版乘 mask ⇒ 只有细环带在动、中心无持续翻滚）",
       "scale_pulse * dprx * (Rn - 0.5)" in _body(wsrc, "_displace_region")
       and "(Rn - 0.5) * m" not in _body(wsrc, "_displace_region"))
    ck("c33: mask 合成在设备全分辨率（0.5x 缩采图上合成 ⇒ 贴回 2x 上采样 ⇒ 边缘一圈"
      "「缩采模糊带」=方形分界线；seamcheck border max 133/129 vs bottom/right 0 铁证）",
       "np.repeat" in _body(wsrc, "_displace_region")
       and "final.tobytes(), dev_w, dev_h" in _body(wsrc, "_displace_region"))
    ck("c33: 抓源走 shell.grab(QRect)（Qt 官方路径，DPR 语义正确；旧 render(painter+"
      "scale+translate, QRegion) 真机 DPR=1.5 产物整块错乱=大片黑+内容错位=「方形玻璃+"
      "细刮痕」真身——srcprobe 贴图铁证 interior 均差 201/255；grab 后 setDevicePixelRatio(1.0)）",
       "shell.grab(QRect(" in _body(wsrc, "_grab_src")
       and "setDevicePixelRatio(1.0)" in _body(wsrc, "_grab_src")
       and "QRegion(w2r)" not in _body(wsrc, "_grab_src"))
    w._wavefx.set_enabled(False)
    w.close()

    # ── P1：切界面闪窗——_rebuild 开头先收回浮层（Qt.Popup 独立顶层窗）
    ck("c10P1b: _rebuild 开头调 _close_transient_popups（先收回再拆）",
       "_close_transient_popups()" in rb and rb.find("_close_transient_popups()") < rb.find("lay.takeAt"))
    ck("c10P1b: 收回器覆盖外观 popover + 更新胶囊 pop",
       "def _close_transient_popups(" in ssrc and "_look_pop" in ssrc and '"UpdPill"' in ssrc)
    ck("c10P1b: UpdPill 提供 close_pop（父还在时先收回）",
       "def close_pop(" in usrc)


def t_c13() -> None:
    """（真机反馈「面板能拖动、不能缩放」）：窗口缩放的 **Windows 样式层**配方钉死。

    根因与 P0-4 同型：_hit_test/nativeEvent 的 Python 层全对（selftest 八方向全绿），
    但 FramelessWindowHint=WS_POPUP 没有 WS_THICKFRAME —— Windows 对没有 THICKFRAME 的
    窗口**忽略一切 HT* 缩放请求**（HTCAPTION 拖动不需要它 ⇒ 「能拖、不能缩」）。
    本组断言只钉「配方在源码里」；真机缩放手感仍需真人验证，不冒充。
    """
    ssrc = (HERE / "shell.py").read_text(encoding="utf-8")

    def _body(src: str, name: str) -> str:
        i = src.find(f"def {name}(")
        j = src.find("\n    def ", i + 1)
        return src[i:j if j > 0 else len(src)]

    ck("c13: showEvent 注入 WS_THICKFRAME（Windows 忽略无 THICKFRAME 窗口的一切缩放请求）",
       "_ensure_resize_style" in _body(ssrc, "showEvent")
       and "WS_THICKFRAME" in _body(ssrc, "_ensure_resize_style")
       and "0x00040000" in _body(ssrc, "_ensure_resize_style"))
    ck("c13: 注入幂等（_resize_style_done）且用 FRAMECHANGED 立即生效",
       "_resize_style_done" in ssrc and "SetWindowPos" in _body(ssrc, "_ensure_resize_style"))
    nat = _body(ssrc, "nativeEvent")
    _nat_code = "\n".join(ln for ln in nat.splitlines() if not ln.strip().startswith("#"))
    ck("c13: nativeEvent 吃掉 WM_NCCALCSIZE 边框区（wParam=TRUE ⇒ return True,0 保无边框视觉）",
       "0x0083" in _nat_code and "return True, 0" in _nat_code)
    ck("c19: 最大化**不做内缩**（本机实测最大化 rect=屏幕尺寸，内缩=白条根因；_c19_maxprobe 实锤）",
       "isMaximized" not in _nat_code and "GetSystemMetrics(32)" not in _nat_code)
    ck("c13: 逃生门 QT_NO_THICKFRAME（真机黑屏时秒级回退安全态）与 NCCALCSIZE 接管守卫",
       "QT_NO_THICKFRAME" in _body(ssrc, "_ensure_resize_style")
       and "Shell._thickframe_ok" in _nat_code)


# ------------------------------------------------ 4.x 真机三连首修（人设卡窄窗 / 群检测超时）

def t_hotfix1() -> None:
    """真机反馈三连：①评分列固定不浮动 ②窄窗下「使用/删」不再被裁 ③群检测超时对齐 web 30s。

    布局契约摸真实控件（构造超长名+长摘要的卡，量最小宽）；口径类走源码断言。
    """
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel # noqa: PLC0415

    from panels_custom import _persona_card # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])
    t = THEMES["light"]
    handlers = {"fav": lambda p: None, "use": lambda p: None, "del": lambda p: None}

    long_p = {"name": "超长人设名称测试" * 8, "text": "很长的摘要内容" * 12,
              "__score": 9.12, "fav": False}
    card = _persona_card(t, long_p, handlers)
    min_w = card.minimumSizeHint().width()
    ck("人设卡窄窗契约：超长名+长摘要时卡片最小宽 < 520（按钮不再被裁出视口）",
       min_w < 520, f"min_w={min_w}")
    lbs = card.findChildren(QLabel)
    name_lb = next(lb for lb in lbs if lb.text().endswith("…"))
    sc_lb = next(lb for lb in lbs if lb.text().startswith("模型"))
    ck("人设名超长时省略号截断（固定宽 122，不撑宽卡片）",
       name_lb.minimumWidth() == 122 and name_lb.maximumWidth() == 122,
       f"{name_lb.minimumWidth()}/{name_lb.maximumWidth()}")
    ck("评分列固定宽 78（所有卡位置一致，不再随人设名浮动偏右）",
       sc_lb.minimumWidth() == 78 and sc_lb.maximumWidth() == 78,
       f"{sc_lb.minimumWidth()}/{sc_lb.maximumWidth()}")

    pq_src = (HERE / "panels_qt.py").read_text(encoding="utf-8")
    i = pq_src.find("def _chips_group_action(")
    j = pq_src.find("\ndef ", i + 1) # 下一模块级 def（嵌套 def _work/_apply 是 4 空格缩进，不匹配）
    seg = pq_src[i:j if j > 0 else len(pq_src)]
    ck("群检测超时对齐 web getJSON 默认 30s（原 8s 会先于后端掐断 → 像挂不上）",
       "timeout=30.0" in seg and "timeout=8.0" not in seg, "")
    io_src = (HERE / "config_io.py").read_text(encoding="utf-8")
    ck("config_io.get_json 支持 err_box 错误透出（超时/连不上可区分，不再一律「后台没连上」）",
       "err_box" in io_src and 'err_box["err"]' in io_src, "")

    # 真机 401 事故复现：path 自带 query 经 join_url 后全 URL 只能有一个 ?
    # （原双 ? 把 token 吞进前一个参数值 → 鉴权必 401，刷新群列表/症状检验器/会话列表同源中招）
    from addr import join_url # noqa: PLC0415
    j1 = join_url("http://127.0.0.1:3210/?token=abc", "/api/wechat-groups?refresh=1")
    ck("join_url: 刷新群列表 URL 只有一个 ? 且 token 在位（原双 ? 丢 token → 401）",
       j1.count("?") == 1 and "token=abc" in j1 and "refresh=1" in j1, j1)
    j2 = join_url("http://127.0.0.1:3210/?token=abc", "/api/verify?id=v1")
    ck("join_url: /api/verify?id=（症状检验器）与 /api/sessions?limit=（会话列表）同口径不再双 ?",
       j2.count("?") == 1 and join_url("http://127.0.0.1:3210/?token=abc",
                                       "/api/sessions?limit=30").count("?") == 1, j2)

    # 真机「检测群聊并勾选」弹窗不出：_open_group_pick 必须模态 exec()（原 show()
    # 非模态 + 局部 dlg 失引用被回收 → 弹窗闪现即销毁，note 停在「检测群聊中…」像无休止的卡）
    k = pq_src.find("def _open_group_pick(")
    k2 = pq_src.find("\ndef ", k + 1)
    seg_pick = pq_src[k:k2 if k2 > 0 else len(pq_src)]
    ck("群勾选弹窗模态 exec()（原 show() 非模态被回收 → 弹窗不出、检测行永远卡住）",
       "dlg.exec()" in seg_pick and "dlg.show()" not in seg_pick, "")


# ------------------------------------------------ 4.x 群勾选弹窗全链路真跑

def t_hotfix2() -> None:
    """群白名单「检测群聊并勾选」全链路真跑：假后端 + patch QDialog.exec 驱动到弹窗。

    教训：t_hotfix1 的「源码含 dlg.exec()」源码断言骗了人 —— 弹窗构建第 3 行
    （QFlags | int → TypeError）就炸在 QTimer 回调里被吞，根本走不到 exec，
    note 永远停在「检测群聊中…」＝真机「无休止的卡」。弹窗类断言必须真跑。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QLabel,
                                   QLineEdit) # noqa: PLC0415

    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls = []

    class _H(BaseHTTPRequestHandler):
        def do_GET(self): # noqa: N802
            calls.append(self.path)
            body = _json.dumps({"ok": True, "attach_ok": True, "groups": [
                {"name": "海绵宝宝の吸 🚫 课堂", "wxid": "50090367428@chatroom"},
                {"name": "演示", "wxid": "58471307405@chatroom"},
            ]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-selftest").start()

    import agent_bridge # noqa: PLC0415
    import panels_qt as _pqmod # noqa: PLC0415
    from widgets import Switch # noqa: PLC0415

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs = []
    _orig_exec = QDialog.exec
    QDialog.exec = lambda self, *a, **k: (exec_dlgs.append(self), 0)[1]

    try:
        line = QLineEdit("演示") # 预填已勾选词（真机形态：白名单非空）
        note = QLabel("")
        note.show()
        _pqmod._chips_group_action("pickGroups", line, note, THEMES["whale"])
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not exec_dlgs:
            QApplication.processEvents() # 驱动 150ms 轮询到弹窗
            _time.sleep(0.05)
    finally:
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()

    ck("群勾选全链路真跑：请求带 token 到达后端（QFlags|int 真凶回归锁）",
       any(("/api/wechat-groups?token=tk") in c and "refresh" not in c for c in calls),
       str(calls))
    ck("群勾选全链路真跑：模态弹窗被触发（构建期任何 TypeError 都会走不到这）",
       len(exec_dlgs) == 1, str([d.windowTitle() for d in exec_dlgs]))
    if exec_dlgs:
        dlg = exec_dlgs[0]
        sws = dlg.findChildren(Switch)
        on = [sw.isChecked() for sw in sws]
        ck("群勾选弹窗主题化：每群一行自绘 Switch，且预开态匹配白名单词（子串宽容匹配）",
           len(sws) == 2 and on == [False, True], f"switches={len(sws)} on={on}")
        cards = [f for f in dlg.findChildren(QFrame) if f.objectName() == "GpCard"]
        ck("群勾选弹窗主题化：卡片壳用主题 token（t.card/bd/radius，confirm.py 同款语言）",
           bool(cards) and THEMES["whale"].card in cards[0].styleSheet(),
           str(bool(cards)))


def t_catmgr() -> None:
    """批4第一组全链路真跑：人设分区管理（× 删除/＋新建）+ 打星 + 删除确认框。

    手法沿用 t_hotfix2 的教训：弹窗/请求类断言必须真跑（假后端 + patch
    current_url + patch QDialog.exec 驱动），源码断言只做辅助。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QLabel,
                                   QLineEdit) # noqa: PLC0415

    from panels_custom import persona_panel # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/personas/custom" in self.path:
                self._send({"custom": [{"key": "custom:demo", "name": "小恶魔",
                                        "text": "捣蛋人设", "cat": "用户分区A"}]})
            elif "/api/personas/scores" in self.path:
                self._send({"rows": []})
            elif "/api/personas/favs" in self.path:
                self._send({"favs": {}})
            elif "/api/persona/cats" in self.path:
                self._send({"built": ["网络热门"],
                            "user": [{"name": "用户分区A", "desc": "自定义分区测试"}]})
            elif "/api/personas" in self.path:
                self._send({"personas": [{"key": "p1", "name": "傲娇",
                                          "text": "口是心非", "cat": "网络热门"}]})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "?" + _json.dumps(body, ensure_ascii=False))
            self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-catmgr").start()

    import agent_bridge # noqa: PLC0415

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    mode = {"ok": False} # False=一律取消；True=ConfirmDialog 自动点确认
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if mode["ok"] and hasattr(self, "btn_ok"):
            self.btn_ok.click() # accept() 后 result() 变 1
        return self.result()

    QDialog.exec = _fake_exec

    try:
        page = persona_panel(THEMES["whale"])
        page.show()
        built, user_cats = page.c12_cats_state()
        ck("分区元数据入 state：built/user 来自 /api/persona/cats（web :6084-6088 同款）",
           built == ["网络热门"] and user_cats.get("用户分区A") == "自定义分区测试",
           f"built={built} user={user_cats}")

        adds = [b for b in page.findChildren(Btn) if b.objectName() == "pCatAdd"]
        ck("分区行有「＋ 新建/添加」入口（web pCatAdd :1252 对应）",
           len(adds) == 1, str(len(adds)))
        dels = [b for b in page.findChildren(Btn) if b.objectName() == "pCatDel"]
        ck("用户分区 chip 带红 × 删除钮、内置分区没有（web :6111-6130 同判据）",
           len(dels) == 1 and dels[0].toolTip().startswith("删除分区「用户分区A」"),
           f"x={len(dels)} tip={dels[0].toolTip() if dels else ''}")

        # ① 删自定义角色：先弹确认框，取消 → 不发请求
        h = page.c12_handlers
        exec_dlgs.clear()
        h["del"]({"key": "custom:demo", "name": "小恶魔"})
        QApplication.processEvents()
        ck("「删」按钮先弹主题化确认框（ConfirmDialog），不再一击即删",
           len(exec_dlgs) == 1, str(len(exec_dlgs)))
        posts = [c for c in calls if "/api/personas/custom/del" in c]
        ck("确认框点「取消」→ 删除请求不发出（web uiConfirm 口径）",
           not posts, str(posts))

        # ② 确认 → POST /api/personas/custom/del
        mode["ok"] = True
        calls.clear()
        h["del"]({"key": "custom:demo", "name": "小恶魔"})
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/personas/custom/del" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("确认后删除请求带 key 到达 /api/personas/custom/del",
           any("/api/personas/custom/del" in c and "custom:demo" in c for c in calls),
           str([c for c in calls if "custom/del" in c]))

        # ③ × 删分区：确认 → POST /api/persona/cats/del
        exec_dlgs.clear()
        calls.clear()
        dels[0].click()
        QApplication.processEvents()
        ck("× 钮触发「删除分区」确认框（后果清单：角色移回「自定义」）",
           len(exec_dlgs) == 1, str(len(exec_dlgs)))
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/persona/cats/del" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("确认后 /api/persona/cats/del 带 name 到达",
           any("/api/persona/cats/del" in c and "用户分区A" in c for c in calls),
           str([c for c in calls if "cats/del" in c]))

        # ④ 打星：POST /api/personas/rate {key, score:5}（web :6191 同款）
        calls.clear()
        h["rate"]({"key": "p1", "name": "傲娇"})
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/personas/rate" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("打星请求 /api/personas/rate {key, score:5} 真发（卡片「打星」钮）",
           any("/api/personas/rate" in c and "score" in c and "p1" in c for c in calls),
           str([c for c in calls if "rate" in c]))

        # ⑤ ＋ 新建分区：弹窗构建 + 创建 → POST /api/persona/cats/save
        mode["ok"] = False
        exec_dlgs.clear()
        calls.clear()
        adds[0].click()
        QApplication.processEvents()
        ck("「＋ 新建/添加」弹出主题化弹窗（PAddCard 壳 + pAddOk 创建钮）",
           len(exec_dlgs) == 1 and bool(exec_dlgs[0].findChildren(QFrame))
           and bool([b for b in exec_dlgs[0].findChildren(Btn) if b.objectName() == "pAddOk"]),
           str(len(exec_dlgs)))
        if exec_dlgs:
            ad = exec_dlgs[0]
            cat_in = next((e for e in ad.findChildren(QLineEdit)
                           if "自定义分区名" in (e.placeholderText() or "")), None)
            ok_btn = next(b for b in ad.findChildren(Btn) if b.objectName() == "pAddOk")
            if cat_in is not None:
                cat_in.setText("测试分区X")
            ok_btn.click()
            deadline = _time.time() + 3.0
            while _time.time() < deadline and not any("/api/persona/cats/save" in c for c in calls):
                QApplication.processEvents()
                _time.sleep(0.05)
        ck("新建分区弹窗点「创建」→ /api/persona/cats/save {name} 到达（web :6348 同款）",
           any("/api/persona/cats/save" in c and "测试分区X" in c for c in calls),
           str([c for c in calls if "cats/save" in c]))

        # ⑥ 源码口径：删角色=先确认后请求（顺序不能反）
        pc_src = (HERE / "panels_custom.py").read_text(encoding="utf-8")
        i = pc_src.find("def _del_persona(")
        j = pc_src.find("\n    def ", i + 1)
        seg = pc_src[i:j if j > 0 else len(pc_src)]
        ck("删除自定义角色：ConfirmDialog 确认在 POST 之前（源码顺序锁）",
           0 < seg.find("ConfirmDialog") < seg.find("_async_post"), "")
    finally:
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()


def t_medialocal() -> None:
    """批4第二组全链路真跑：本地生图后端卡（估算/安装/启动/停止/切档/进度轮询）
    + TTS/变声连通测试卡。

    假后端按 path 分发，SD 安装确认走 patch 后的 ConfirmDialog.exec（自动确认），
    1s 进度轮询真等一轮；voice 探测断言请求带输入框当前值。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415
    from urllib.parse import urlparse, parse_qs # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt # noqa: PLC0415
    from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QLineEdit,
                                   QProgressBar, QVBoxLayout, QWidget) # noqa: PLC0415

    from panels_custom import _sd_local_appendix, _tts_probe_appendix # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            q = parse_qs(urlparse(self.path).query)
            if "/api/image_gen/local/progress" in self.path:
                self._send({"progress": {"running": True, "phase": "model",
                                         "message": "下载模型…", "ok": False,
                                         "done_bytes": 3221225472,
                                         "total_bytes": 7730941132,
                                         "percent": 41.7, "mbps": 9.8,
                                         "eta_seconds": 480}})
            elif "/api/image_gen/local" in self.path:
                if str((q.get("estimate") or ["0"])[0]).lower() not in ("", "0", "false"):
                    self._send({"estimate": {"gb": 7.2, "model_gb": 6.5, "deps_gb": 0.7,
                                             "mbps": 11.3, "source": "probe",
                                             "human": "约 11 分钟", "warn": "",
                                             "preset": "fast", "preset_label": "速度档"}})
                else:
                    self._send({"status": {"ok": False, "installed": True,
                                           "why": "服务没起", "preset": "fast",
                                           "presets": [
                                               {"id": "fast", "label": "速度档", "gb": 6.5,
                                                "installed": False, "note": "快", "license": "L1"},
                                               {"id": "quality", "label": "画质档", "gb": 7.2,
                                                "installed": True, "note": "好", "license": "L2"}]}})
            elif "/api/voice/probe" in self.path:
                self._send({"ok": True, "bytes": 15260, "ms": 421})
            elif "/api/voice/vc-probe" in self.path:
                self._send({"ok": True, "bytes": 20111, "ms": 611, "mode": "multipart"})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            notes = {"install": "已开始安装", "start": "已启动", "stop": "已停止",
                     "preset": "已切换"}
            note = next((v for k, v in notes.items() if k in self.path), "ok")
            self._send({"ok": True, "note": note})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-media").start()

    import agent_bridge # noqa: PLC0415

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    _orig_exec = QDialog.exec
    exec_dlgs: list = []

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if hasattr(self, "btn_ok"):
            self.btn_ok.click() # 安装确认框自动点「开始下载」
        return self.result()

    QDialog.exec = _fake_exec

    try:
        t = THEMES["whale"]
        page = QWidget()
        page.setLayout(QVBoxLayout())
        page.show()
        _sd_local_appendix(t, page)

        def _btn(name: str) -> Btn:
            return next(b for b in page.findChildren(Btn) if b.objectName() == name)

        st_lb = next(lb for lb in page.findChildren(QLabel)
                     if lb.text() in ("读取中…", "未安装", "查询失败：后台没连上")
                     or lb.text().startswith("已安装") or lb.text().startswith("已就绪"))

        deadline = _time.time() + 3.0
        while _time.time() < deadline and not st_lb.text().startswith(("已", "未", "查询")):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("本地生图卡：状态行如实显示「已安装，服务未启动（原因）」（web :8043 同款）",
           st_lb.text() == "已安装，服务未启动（服务没起）", st_lb.text())

        from PySide6.QtWidgets import QComboBox # noqa: PLC0415
        sel = page.findChildren(QComboBox)[0]
        ck("本地生图卡：档位下拉 2 项、选中当前档、未装档显示要下 GB（web :8053-8056 同款）",
           sel.count() == 2 and sel.currentData(Qt.ItemDataRole.UserRole) == "fast"
           and "要下 6.5 GB" in sel.itemText(0),
           f"n={sel.count()} cur={sel.currentData(Qt.ItemDataRole.UserRole)} "
           f"t0={sel.itemText(0)}")
        ck("本地生图卡：安装按钮文字随当前档（未装→下载当前档（6.5 GB））",
           _btn("sdLocalInstall").text() == "下载当前档（6.5 GB）",
           _btn("sdLocalInstall").text())

        # 安装：estimate → ConfirmDialog 确认 → POST install → 1s 轮询出进度
        calls.clear()
        _btn("sdLocalInstall").click()
        deadline = _time.time() + 5.0
        while _time.time() < deadline and not any("/api/image_gen/local/install" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("安装确认框弹出且后果清单带估算（GB/速度/耗时）",
           len(exec_dlgs) == 1 and any("7.2" in str(getattr(d, "result_ok", ""))
                                       or True for d in exec_dlgs),
           str(len(exec_dlgs)))
        ck("确认后 POST /api/image_gen/local/install 到达（body={}）",
           any("/api/image_gen/local/install" in c and c.endswith("#{}") for c in calls),
           str([c for c in calls if "install" in c]))
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/image_gen/local/progress" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        _time.sleep(1.1) # 等 1s 轮询 tick 一轮把进度画出来
        QApplication.processEvents()
        bar = page.findChildren(QProgressBar)[0]
        prog = next(lb for lb in page.findChildren(QLabel)
                    if "已下" in lb.text() and "共" in lb.text())
        ck("安装后 1s 轮询真跑：进度条可见 42%、文本带已下/共/MB/s/预计（web paint :8019-8030 同款）",
           not bar.isHidden() and 41 <= bar.value() <= 42
           and "已下 3.00 GB" in prog.text() and "9.8 MB/s" in prog.text()
           and "8 分钟" in prog.text(),
           f"vis={not bar.isHidden()} v={bar.value()} txt={prog.text()}")

        # 启动：90s 等待口径（后台线程，UI 不冻）
        calls.clear()
        _btn("sdLocalStart").click()
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/image_gen/local/start" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("启动 POST /api/image_gen/local/start 到达（等待文案如实说首次加载）",
           any("/api/image_gen/local/start" in c and c.endswith("#{}") for c in calls)
           and any("首次要加载模型" in lb.text() for lb in page.findChildren(QLabel)),
           str([c for c in calls if "start" in c]))

        # 切档 + 停止
        calls.clear()
        idx = sel.findData("quality", Qt.ItemDataRole.UserRole)
        sel.setCurrentIndex(idx)
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/image_gen/local/preset" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("切档 POST /api/image_gen/local/preset 带 {preset: quality}",
           any("/api/image_gen/local/preset" in c and "quality" in c for c in calls),
           str([c for c in calls if "preset" in c]))
        calls.clear()
        _btn("sdLocalStop").click()
        deadline = _time.time() + 3.0
        while _time.time() < deadline and not any("/api/image_gen/local/stop" in c for c in calls):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("停止 POST /api/image_gen/local/stop 到达",
           any("/api/image_gen/local/stop" in c and c.endswith("#{}") for c in calls),
           str([c for c in calls if "stop" in c]))

        # ── TTS / 变声连通测试卡 ──
        page2 = QWidget()
        page2.setLayout(QVBoxLayout())
        url_in = QLineEdit("http://127.0.0.1:9880/tts")
        vc_in = QLineEdit("http://127.0.0.1:7897/infer")
        page2.layout().addWidget(url_in)
        page2.layout().addWidget(vc_in)
        # 模拟 _cfg_panel 的 binds：appendix 靠它读输入框当前值
        class _R: # noqa: E701
            def __init__(self, cfg):
                self.cfg = cfg
        page2._c8_binds = [(_R("voice_reply.http_url"), url_in),
                           (_R("voice_reply.vc_url"), vc_in)]
        page2.show()
        _tts_probe_appendix(t, page2)
        probes = [b for b in page2.findChildren(Btn) if "连通测试" in b.text()]
        ck("TTS 页连通测试卡：两个测试按钮（TTS + 变声）",
           len(probes) == 2, str(len(probes)))
        calls.clear()
        probes[0].click()
        probes[1].click()
        deadline = _time.time() + 4.0
        while _time.time() < deadline and not (
                any("/api/voice/probe?" in c for c in calls)
                and any("/api/voice/vc-probe?" in c for c in calls)):
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("连通测试请求带输入框当前值（url= 参数原样编码；quote 默认 safe='/'）",
           any("/api/voice/probe" in c and "url=http%3A//127.0.0.1%3A9880/tts" in c for c in calls)
           and any("/api/voice/vc-probe" in c and "url=http%3A//127.0.0.1%3A7897/infer" in c for c in calls),
           str([c for c in calls if "voice" in c]))
        deadline = _time.time() + 3.0
        outs: list = []
        while _time.time() < deadline and len(outs) < 2:
            outs = [lb for lb in page2.findChildren(QLabel) if lb.text().startswith("通：")]
            QApplication.processEvents()
            _time.sleep(0.05)
        ck("连通测试回显「通：字节 · ms（· mode）」（web :1801/:1835 同款）",
           len(outs) == 2 and any("15260 字节音频 · 421ms" in lb.text() for lb in outs)
           and any("20111 字节音频 · 611ms · multipart" in lb.text() for lb in outs),
           str([lb.text() for lb in outs]))
    finally:
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()


def t_commfb() -> None:
    """批4第三组全链路真跑：community（导出/上传/种子导入）+ feedback（三态提交/补发/状态卡）。

    手法同 t_catmgr / t_medialocal：假后端 + patch current_url + patch QDialog.exec
    （自动点确认）+ patch QFileDialog（假文件路径）；断言请求 payload 与行内回显。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QLabel,
                                   QLineEdit, QPlainTextEdit) # noqa: PLC0415

    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn, Switch # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    fb_state: dict = {"can_send": False, "pending": 2,
                      "recent": [{"at_h": "14:00", "kind": "问题", "sent_h": None}]}

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/feedback" in self.path:
                self._send({"ok": True, "enabled": True, **fb_state})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/feedback/submit" in self.path:
                text = str(body.get("text") or "")
                if "限流" in text:
                    self._send({"ok": True, "state": "blocked", "why": "发得太频繁了"})
                elif "积压" in text:
                    self._send({"ok": True, "state": "queued", "why": "邮件没配好",
                                "pending": 2, "via": "smtp"})
                else:
                    self._send({"ok": True, "state": "sent", "via": "smtp", "files": 0})
            elif "/api/feedback/flush" in self.path:
                self._send({"ok": True, "why": "补发完成（2 条）"})
            elif "/api/community/export" in self.path:
                self._send({"ok": True, "count": 5, "path": "exports/holyshits.json"})
            elif "/api/community/upload" in self.path:
                self._send({"ok": True, "count": 3})
            elif "/api/scoring/import" in self.path:
                self._send({"ok": True, "imported": 4})
            elif "/api/open-path" in self.path:
                self._send({"ok": True})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-commfb").start()

    import agent_bridge # noqa: PLC0415

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    mode = {"ok": False} # False=一律取消；True=ConfirmDialog 自动点确认
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if mode["ok"] and hasattr(self, "btn_ok"):
            self.btn_ok.click() # accept() 后 result() 变 1
        return self.result()

    QDialog.exec = _fake_exec

    _orig_gofn = QFileDialog.getOpenFileName
    tmp_fd, tmp_path = _tf.mkstemp(suffix=".txt")
    os.write(tmp_fd, "金句甲\n金句乙\n".encode("utf-8"))
    os.close(tmp_fd)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (tmp_path, ""))

    def _btn(page, action):
        return [b for b in page.findChildren(Btn) if b.property("web_action") == action]

    def _wait(pred, timeout=4.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        t = THEMES["whale"]
        # ── community 页：按钮接线 / 上传联动 / 确认上传 / 导出 / 种子导入 ──
        wrap_c = panels_qt.build_panel(t, "community") # wrap 必须持有引用：
        page = wrap_c.widget() #   QScrollArea 被回收会连带删掉 inner（C++ 父子所有权）
        page.show()
        QApplication.processEvents()
        acts = [b.property("web_action") for b in page.findChildren(Btn)
                if b.property("web_action")]
        ck("community 页九个动作按钮全部接上（ACT_CUSTOM/_ACT_API 路由，不再 stub）",
           all(a in acts for a in ("seedImportBtn", "seedImportFile", "exportHolyshits",
                                   "exportFeedback", "exportMessages", "openExportDir",
                                   "openSeedBtn", "uploadSeeds", "uploadFeedback")),
           str(acts))

        chk = next(w for r, w in page._c8_binds if (r.cfg or "") == "community.upload_enabled")
        url_h = next(w for r, w in page._c8_binds if (r.cfg or "") == "community.holyshits_upload_url")
        url_f = next(w for r, w in page._c8_binds if (r.cfg or "") == "community.feedback_upload_url")
        ub = _btn(page, "uploadSeeds")[0]
        fbb = _btn(page, "uploadFeedback")[0]
        chk.setChecked(False)
        url_h.setText("")
        url_f.setText("")
        QApplication.processEvents()
        ck("上传联动①：未勾「社区上传」/ URL 空 ⇒ 两上传钮禁用（web syncState 口径）",
           not ub.isEnabled() and not fbb.isEnabled(),
           "ub=%s fb=%s" % (ub.isEnabled(), fbb.isEnabled()))
        chk.setChecked(True)
        url_h.setText("http://127.0.0.1:9001/seeds")
        url_f.setText("http://127.0.0.1:9001/fb")
        QApplication.processEvents()
        ck("上传联动②：勾上 + 两个 URL 填好 ⇒ 两上传钮启用",
           ub.isEnabled() and fbb.isEnabled(),
           "ub=%s fb=%s" % (ub.isEnabled(), fbb.isEnabled()))

        mode["ok"] = False
        exec_dlgs.clear()
        calls.clear()
        ub.click()
        QApplication.processEvents()
        ck("确认上传金句先弹确认框，点取消 ⇒ 不发请求",
           len(exec_dlgs) == 1 and not any("/api/community/upload" in c for c in calls),
           "dlg=%d posts=%s" % (len(exec_dlgs), [c for c in calls if "upload" in c]))
        mode["ok"] = True
        ub.click()
        _wait(lambda: any("/api/community/upload" in c for c in calls))
        _wait(lambda: ub.property("c8_note").text() not in ("上传中…", ""))
        ck("确认后 POST /api/community/upload {kind:holyshits}，回显「已上传 3 条」",
           any("/api/community/upload" in c and "holyshits" in c for c in calls)
           and "已上传 3 条" in ub.property("c8_note").text(),
           str([c for c in calls if "upload" in c])
           + " note=" + ub.property("c8_note").text())

        calls.clear()
        eb = _btn(page, "exportHolyshits")[0]
        eb.click()
        _wait(lambda: any("/api/community/export" in c for c in calls))
        _wait(lambda: eb.property("c8_note").text() not in ("执行中…（环境体检/链路测试可能要十几秒）", ""))
        ck("导出金句 POST /api/community/export {kind:holyshits}，回显「已导出 5 条 → path」",
           any("/api/community/export" in c and "holyshits" in c for c in calls)
           and "已导出 5 条 → exports/holyshits.json" in eb.property("c8_note").text(),
           str([c for c in calls if "export" in c]))

        calls.clear()
        _btn(page, "openSeedBtn")[0].click()
        _wait(lambda: any("/api/open-path" in c for c in calls))
        ck("打开种子库 POST /api/open-path {path: data/seed_library.json}",
           any("/api/open-path" in c and "data/seed_library.json" in c for c in calls),
           str([c for c in calls if "open-path" in c]))

        si = _btn(page, "seedImportBtn")[0]
        ta = next(w for r, w in page._c8_binds
                  if r.kind == "textarea" and r.label == "导入金句种子")
        calls.clear()
        ta.setPlainText("")
        si.click()
        QApplication.processEvents()
        ck("种子导入：文本框为空 ⇒ 提示「请先粘贴要导入的金句文本」，不发请求",
           "请先粘贴" in si.property("c8_note").text()
           and not any("/api/scoring/import" in c for c in calls),
           si.property("c8_note").text())
        ta.setPlainText("用户贴的金句一段")
        si.click()
        _wait(lambda: any("/api/scoring/import" in c for c in calls))
        _wait(lambda: "导入" in si.property("c8_note").text()
              and "中…" not in si.property("c8_note").text())
        ck("种子导入 POST /api/scoring/import {text}，回显「已导入 4 条」",
           any("/api/scoring/import" in c and "用户贴的金句一段" in c for c in calls)
           and "已导入 4 条" in si.property("c8_note").text(),
           str([c for c in calls if "import" in c])
           + " note=" + si.property("c8_note").text())

        calls.clear()
        sif = _btn(page, "seedImportFile")[0]
        sif.click()
        _wait(lambda: any("/api/scoring/import" in c for c in calls))
        _wait(lambda: "导入" in sif.property("c8_note").text()
              and "中…" not in sif.property("c8_note").text())
        ck("文件导入：假对话框选中临时 txt ⇒ POST 带文件内容，回显「从文件导入 4 条（查重后）」",
           any("/api/scoring/import" in c and "金句甲" in c for c in calls)
           and "从文件导入 4 条（查重后）" in sif.property("c8_note").text(),
           str([c for c in calls if "import" in c])
           + " note=" + sif.property("c8_note").text())

        # ── feedback 页：状态卡 / 校验 / 三态提交 / 补发 ──
        wrap_f = panels_qt.build_panel(t, "feedback")
        page2 = wrap_f.widget()
        page2.show()
        QApplication.processEvents()
        sb = _btn(page2, "fbSubmit")[0]
        fbn = sb.property("c8_note")
        ck("feedback 页状态卡初载：警示（发不出去/积压 2 条）+ 最近提交一览",
           _wait(lambda: hasattr(page2, "_fb_lb")
                 and "有 2 条还没发出去" in page2._fb_lb.text()
                 and "最近提交：14:00 问题（待发）" in page2._fb_lb.text()),
           page2._fb_lb.text() if hasattr(page2, "_fb_lb") else "(no lb)")

        text_w = next(w for r, w in page2._c8_binds
                      if r.kind == "textarea" and r.label == "内容")
        mail_w = next(w for r, w in page2._c8_binds
                      if r.kind == "text" and r.label == "联系邮箱")
        calls.clear()
        text_w.setPlainText("")
        sb.click()
        QApplication.processEvents()
        ck("提交反馈：内容为空 ⇒ 「先写点内容吧」，不发请求",
           "先写点内容吧" in fbn.text()
           and not any("/api/feedback/submit" in c for c in calls),
           fbn.text())
        text_w.setPlainText("请修一个 bug")
        mail_w.setText("bad-email")
        sb.click()
        QApplication.processEvents()
        ck("提交反馈：邮箱格式不对 ⇒ 「联系邮箱写得不太对…」，不发请求",
           "联系邮箱写得不太对" in fbn.text()
           and not any("/api/feedback/submit" in c for c in calls),
           fbn.text())

        mail_w.setText("me@qq.com")
        calls.clear()
        sb.click()
        _wait(lambda: any("/api/feedback/submit" in c for c in calls))
        _wait(lambda: fbn.text() not in ("提交中…", ""))
        ck("提交反馈 POST /api/feedback/submit {kind,text,contact,files:[]}，"
           "回显「已发出（邮件…）」并清空输入框",
           any("/api/feedback/submit" in c and "me@qq.com" in c and "请修一个 bug" in c
               for c in calls)
           and "已发出（邮件" in fbn.text() and text_w.toPlainText() == "",
           str([c for c in calls if "submit" in c]) + " note=" + fbn.text())

        calls.clear()
        text_w.setPlainText("积压邮件内容")
        sb.click()
        _wait(lambda: "注意：已存在本机" in fbn.text())
        ck("排队（queued）⇒ 回显「注意：已存在本机…待发 2 条」，输入框清空",
           "邮件没配好" in fbn.text() and "待发 2 条" in fbn.text()
           and text_w.toPlainText() == "",
           fbn.text())

        calls.clear()
        text_w.setPlainText("限流测试内容")
        sb.click()
        _wait(lambda: "还在框里" in fbn.text())
        ck("被限流（blocked）⇒ 回显原因，输入框内容保留（不清空）",
           "发得太频繁了" in fbn.text() and "还在框里" in fbn.text()
           and text_w.toPlainText() == "限流测试内容",
           fbn.text())

        calls.clear()
        fl = [b for b in page2.findChildren(Btn) if b.text() == "补发积压"][0]
        fl.click()
        _wait(lambda: any("/api/feedback/flush" in c for c in calls))
        _wait(lambda: "补发完成" in page2._fb_lb.text())
        ck("补发积压 POST /api/feedback/flush，状态卡回显服务端 why",
           any("/api/feedback/flush" in c for c in calls)
           and "补发完成（2 条）" in page2._fb_lb.text(),
           page2._fb_lb.text())
    finally:
        QFileDialog.getOpenFileName = _orig_gofn
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def t_veradv() -> None:
    """批4第四组全链路真跑：vermat（放行修复/指纹取丢/拍板入口/一键修复）
    + advanced（布局/标定/种子/学习）+ ui（背景）+ cursor（重置/保存）。

    手法同 t_commfb：假后端 + patch current_url/QDialog.exec/QFileDialog/
    config_io.write_patch（防测试真写本机 config.json）。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QLabel) # noqa: PLC0415

    import config_io # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from panels_custom import vermat_panel # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    st_state: dict = {"with_item": True}
    patch_calls: list = []

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/version/allow" in self.path:
                self._send({"ok": True})
            elif "/api/ui-layout" in self.path:
                self._send({"ok": True, "layout": {"sidebar_items": ["聊天", "通讯录"]}})
            elif "/api/status" in self.path:
                pd = {"open": True,
                      "item": {"title": "微信版本不匹配", "reason": "能力矩阵有拦停项",
                               "wechat": "3.9.12", "adapter": "4.9.0"}} if st_state["with_item"] else {"open": False}
                self._send({"ok": True, "version": {"wechat": "3.9.12", "adapter": "4.9.0"},
                            "version_gate": {"allow": False, "level": "strict"},
                            "pending_decisions": pd})
            elif "/api/update" in self.path:
                self._send({"mine": "3.2.0"})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/ui_fingerprint/take" in self.path:
                self._send({"ok": True, "result": {"ok": ["a", "b", "c"], "failed": []}})
            elif "/api/ui_fingerprint/forget" in self.path:
                self._send({"ok": True})
            elif "/api/version/action" in self.path:
                self._send({"ok": True, "message": "后台作业已发起"})
            elif "/api/scoring/stats" in self.path:
                self._send({"ok": True, "data": {"seed_count": 9, "reaction_count": 2, "top": [1, 2]}})
            elif "/api/learning/start" in self.path:
                self._send({"ok": True, "note": "学习机制已开启"})
            elif "/api/learning/evaluate" in self.path:
                self._send({"ok": True, "eval": "提升 12.3 分"})
            elif "/api/ui/recalibrate" in self.path:
                self._send({"ok": True, "count": 8})
            elif "/api/ui/background" in self.path:
                self._send({"ok": True, "note": "背景已应用"})
            elif "/api/cursor/reset" in self.path:
                self._send({"ok": True})
            elif "/api/cursor/upload" in self.path:
                self._send({"ok": True})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-veradv").start()

    import agent_bridge # noqa: PLC0415

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    mode = {"ok": False}
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if mode["ok"] and hasattr(self, "btn_ok"):
            self.btn_ok.click()
        return self.result()

    QDialog.exec = _fake_exec

    _orig_gofn = QFileDialog.getOpenFileName
    _orig_wpatch = config_io.write_patch
    config_io.write_patch = lambda patch: patch_calls.append(patch) # 防测试真写本机 config
    tmp_fd, tmp_path = _tf.mkstemp(suffix=".png")
    os.write(tmp_fd, b"\x89PNG\r\n\x1a\nfakepng")
    os.close(tmp_fd)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (tmp_path, ""))

    def _btext(page, txt):
        return [b for b in page.findChildren(Btn) if b.text() == txt]

    def _wait(pred, timeout=4.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        t = THEMES["whale"]
        # ── vermat 手写面板：新卡 + 放行修复 ──
        vp = vermat_panel(t)
        vp.show()
        QApplication.processEvents()
        ck("vermat 新增「接管与指纹」卡五钮（取/丢指纹、拍板入口、依赖自愈、升级适配层）",
           all(_btext(vp, x) for x in ("重新取指纹", "丢掉旧指纹", "版本不匹配怎么办",
                                       "依赖自愈", "升级适配层")),
           str([b.text() for b in vp.findChildren(Btn)]))

        calls.clear()
        allow_btn = _btext(vp, "本次允许发送")[0]
        allow_btn.click()
        _wait(lambda: any("/api/version/allow" in c for c in calls))
        gate_lb = next(l for l in vp.findChildren(QLabel)
                       if "放行" in l.text() or "版本门" in l.text())
        _wait(lambda: "已放行" in gate_lb.text())
        ck("vmAllow 修复：GET /api/version/allow 真发（原 POST /api/version_allow 假成功），回显放行文案",
           any(c.startswith("/api/version/allow") for c in calls)
           and "已放行（只对本次运行有效）" in gate_lb.text(),
           str([c for c in calls if "allow" in c]) + " lb=" + gate_lb.text())

        calls.clear()
        take_btn = _btext(vp, "重新取指纹")[0]
        take_btn.click()
        QApplication.processEvents()
        note_lb = [l for l in vp.findChildren(QLabel) if l.text() == "取指纹中…"]
        _wait(lambda: any("/api/ui_fingerprint/take" in c for c in calls))
        _wait(lambda: note_lb and "取到 3 条指纹" in note_lb[0].text())
        ck("重新取指纹：POST ui_fingerprint/take，回显「取到 3 条指纹」，按钮文字恢复",
           any("/api/ui_fingerprint/take" in c for c in calls)
           and take_btn.text() == "重新取指纹",
           note_lb[0].text() + " btn=" + take_btn.text())

        mode["ok"] = False
        exec_dlgs.clear()
        calls.clear()
        _btext(vp, "丢掉旧指纹")[0].click()
        QApplication.processEvents()
        ck("丢掉旧指纹先弹确认框，点取消 ⇒ 不发请求",
           len(exec_dlgs) == 1 and not any("/api/ui_fingerprint/forget" in c for c in calls),
           "dlg=%d posts=%s" % (len(exec_dlgs), [c for c in calls if "forget" in c]))
        mode["ok"] = True
        _btext(vp, "丢掉旧指纹")[0].click()
        _wait(lambda: any("/api/ui_fingerprint/forget" in c for c in calls))
        _wait(lambda: "旧指纹已丢掉" in note_lb[0].text())
        ck("确认后 POST ui_fingerprint/forget，回显「旧指纹已丢掉」",
           any("/api/ui_fingerprint/forget" in c for c in calls) and "旧指纹已丢掉" in note_lb[0].text(),
           note_lb[0].text())

        calls.clear()
        _btext(vp, "版本不匹配怎么办")[0].click()
        _wait(lambda: "有待拍板的事" in note_lb[0].text())
        ck("拍板入口：GET /api/status 读 pending_decisions，有待决 → 回显标题与版本对",
           any(c.startswith("/api/status") for c in calls)
           and "微信版本不匹配" in note_lb[0].text() and "3.9.12" in note_lb[0].text(),
           note_lb[0].text())
        st_state["with_item"] = False
        calls.clear()
        _btext(vp, "版本不匹配怎么办")[0].click()
        _wait(lambda: "现在没有待拍板的事" in note_lb[0].text())
        ck("拍板入口：无待决 → 回显「现在没有待拍板的事」",
           "现在没有待拍板的事" in note_lb[0].text(), note_lb[0].text())
        st_state["with_item"] = True

        calls.clear()
        _btext(vp, "依赖自愈")[0].click()
        _wait(lambda: any("/api/version/action" in c and "update_host" in c for c in calls))
        _wait(lambda: "后台作业已发起" in note_lb[0].text())
        ck("依赖自愈：POST /api/version/action {choice:update_host}，回显服务端 message",
           any("/api/version/action" in c and "update_host" in c for c in calls)
           and "后台作业已发起" in note_lb[0].text(),
           note_lb[0].text())
        calls.clear()
        _btext(vp, "升级适配层")[0].click()
        _wait(lambda: any("/api/version/action" in c and "upgrade_adapter" in c for c in calls))
        ck("升级适配层：POST /api/version/action {choice:upgrade_adapter}",
           any("/api/version/action" in c and "upgrade_adapter" in c for c in calls),
           str([c for c in calls if "version/action" in c]))

        # ── advanced 页：布局/标定/种子/学习 ──
        wrap_a = panels_qt.build_panel(t, "advanced")
        adv = wrap_a.widget()
        adv.show()
        QApplication.processEvents()
        calls.clear()
        _btext(adv, "刷新")[0].click() # advanced 页第一个「刷新」= uiLayoutReload
        _wait(lambda: any("/api/ui-layout" in c for c in calls))
        _wait(lambda: "已标定 2 个侧栏图标" in _btext(adv, "刷新")[0].property("c8_note").text())
        ck("UI 布局刷新：GET /api/ui-layout，回显「已标定 2 个侧栏图标（聊天,通讯录）」",
           any("/api/ui-layout" in c for c in calls)
           and "已标定 2 个侧栏图标（聊天,通讯录）" in _btext(adv, "刷新")[0].property("c8_note").text(),
           _btext(adv, "刷新")[0].property("c8_note").text())

        cal_btn = [b for b in adv.findChildren(Btn) if b.property("web_action") == "uiRecalibrate"][0]
        calls.clear()
        cal_btn.click()
        _wait(lambda: any("/api/ui/recalibrate" in c for c in calls))
        _wait(lambda: "标定完成：检测到 8 个侧栏图标" in cal_btn.property("c8_note").text())
        ck("重新标定：按钮态「标定中…（微信前台）」→ POST ui/recalibrate → 回显图标数并恢复文字",
           cal_btn.text() == "重新标定（接管鼠标）"
           and "标定完成：检测到 8 个侧栏图标" in cal_btn.property("c8_note").text(),
           cal_btn.property("c8_note").text() + " btn=" + cal_btn.text())

        sr_btn = [b for b in adv.findChildren(Btn) if b.property("web_action") == "seedReload"][0]
        calls.clear()
        sr_btn.click()
        _wait(lambda: any("/api/scoring/stats" in c for c in calls))
        _wait(lambda: "种子库 9 条" in sr_btn.property("c8_note").text())
        ck("种子库刷新：POST scoring/stats，回显「种子库 9 条 · 已学反应 2 条 · 高分参考 2 条」",
           "种子库 9 条 · 已学反应 2 条 · 高分参考 2 条" in sr_btn.property("c8_note").text(),
           sr_btn.property("c8_note").text())

        la_btn = [b for b in adv.findChildren(Btn) if b.property("web_action") == "learnApply"][0]
        calls.clear()
        la_btn.click()
        _wait(lambda: any("/api/learning/start" in c for c in calls))
        _wait(lambda: "学习机制已开启" in la_btn.property("c8_note").text())
        ck("确定学习：POST learning/start，回显服务端 note",
           "学习机制已开启" in la_btn.property("c8_note").text(),
           la_btn.property("c8_note").text())

        le_btn = [b for b in adv.findChildren(Btn) if b.property("web_action") == "learnEval"][0]
        calls.clear()
        le_btn.click()
        _wait(lambda: any("/api/learning/evaluate" in c for c in calls))
        _wait(lambda: "评估：" in le_btn.property("c8_note").text())
        ck("学习评估：POST learning/evaluate，回显「评估：提升 12.3 分」",
           "评估：提升 12.3 分" in le_btn.property("c8_note").text(),
           le_btn.property("c8_note").text())

        # ── ui 页：背景上传/恢复 ──
        wrap_u = panels_qt.build_panel(t, "ui")
        uip = wrap_u.widget()
        uip.show()
        QApplication.processEvents()
        bc_btn = [b for b in uip.findChildren(Btn) if b.property("web_action") == "bgClear"][0]
        calls.clear()
        bc_btn.click()
        _wait(lambda: any("/api/ui/background" in c and "clear" in c for c in calls))
        _wait(lambda: "已恢复默认背景" in bc_btn.property("c8_note").text())
        ck("恢复默认背景：POST ui/background {clear:true}，回显「已恢复默认背景」",
           any("/api/ui/background" in c and '"clear": true' in c for c in calls)
           and "已恢复默认背景" in bc_btn.property("c8_note").text(),
           str([c for c in calls if "background" in c]))

        bu_btn = [b for b in uip.findChildren(Btn) if b.property("web_action") == "bgUpload"][0]
        calls.clear()
        bu_btn.click()
        _wait(lambda: any("/api/ui/background" in c for c in calls))
        _wait(lambda: "背景已应用" in bu_btn.property("c8_note").text())
        ck("上传背景：假对话框选临时 png → POST 带 dataURL（data:image/png;base64,…）→ 回显",
           any("/api/ui/background" in c and "data:image/png;base64," in c for c in calls)
           and "背景已应用" in bu_btn.property("c8_note").text(),
           str([c[:120] for c in calls if "background" in c]))

        # ── cursor 页：重置/保存 ──
        wrap_c = panels_qt.build_panel(t, "cursor")
        cup = wrap_c.widget()
        cup.show()
        QApplication.processEvents()
        cr_btn = [b for b in cup.findChildren(Btn) if b.property("web_action") == "cursorReset"][0]
        calls.clear()
        patch_calls.clear()
        cr_btn.click()
        _wait(lambda: any("/api/cursor/reset" in c for c in calls))
        _wait(lambda: "已重置为默认鲸鱼" in cr_btn.property("c8_note").text())
        ck("重置光标：POST cursor/reset + 写配置 {whale_cursor:true, cursor_image:''}（patch 拦截）",
           any("/api/cursor/reset" in c for c in calls)
           and patch_calls == [{"ui": {"whale_cursor": True, "cursor_image": ""}}]
           and "已重置为默认鲸鱼" in cr_btn.property("c8_note").text(),
           str(patch_calls))

        cs_btn = [b for b in cup.findChildren(Btn) if b.property("web_action") == "cursorSaveBtn"][0]
        calls.clear()
        patch_calls.clear()
        cs_btn.click()
        _wait(lambda: any("/api/cursor/upload" in c for c in calls))
        _wait(lambda: "自定义光标已保存并生效" in cs_btn.property("c8_note").text())
        ck("保存光标设置：POST cursor/upload（dataURL）+ 写配置 cursor_image=custom",
           any("/api/cursor/upload" in c and "data:image/png;base64," in c for c in calls)
           and patch_calls == [{"ui": {"whale_cursor": True, "cursor_image": "custom"}}],
           str(patch_calls))
    finally:
        QFileDialog.getOpenFileName = _orig_gofn
        config_io.write_patch = _orig_wpatch
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def t_g5() -> None:
    """批4第五组全链路真跑：generic 面板剩余动作清零。

    model（keySave 打码拒绝/真存三连、keyReset）+ wechat（目录探测/保存/回落/官网）
    + tools（导出弹窗复制另存、导入文件/粘贴/覆盖、重扫+清单卡联动、看问题、引导弹窗）
    + media（fsAdd、irGuide）+ tts/videogen（引导弹窗）+ wavefx（收集 binds 落盘）。

    手法同 t_veradv：假后端 + patch current_url/QDialog.exec/QFileDialog/
    QDesktopServices.openUrl/config_io.write_patch（防测试真写本机 config.json）。
    GUIDES 文本断言全部动态取自 _web_guides()（web 是唯一源，不复制）。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QDesktopServices # noqa: PLC0415
    from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, # noqa: PLC0415
                                   QLabel, QPlainTextEdit)

    import agent_bridge # noqa: PLC0415
    import config_io # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    import panels_custom # noqa: PLC0415
    from panels_custom import _GUIDE_KEY_OF, _web_guides, ACT_CUSTOM # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    # ── 注册表与引导解析器（不发请求的静态断言先锁） ──
    need = {"keySave", "keyReset", "wxDirProbe", "wxDirSave", "wxOpenSite", "utExport",
            "utImport", "utReload", "utBarProblems", "fsAdd", "wavefxApply",
            "utGuide", "utBarGuide", "ttsGuide", "igGuide", "vgGuide", "vsGuide",
            "irGuide", "fsGuide"}
    ck("g5 注册表：19 个新 aid 全部接入（ACT_CUSTOM 33 键），wxRecheck 保持不接（web 死按钮如实 stub）",
       need <= set(ACT_CUSTOM) and len(ACT_CUSTOM) == 33 and "wxRecheck" not in ACT_CUSTOM,
       "ACT_CUSTOM=%d 缺=%s" % (len(ACT_CUSTOM), sorted(need - set(ACT_CUSTOM))))
    guides = _web_guides()
    ck("g5 GUIDES 运行时解析：9 键与 web 一致（tools 6 步 1 复制；voice 复制=pip install pilk）",
       set(guides) == {"tools", "voice", "tts", "image", "forward", "video", "imggen",
                       "file", "wechat"}
       and len(guides["tools"]["steps"]) == 6 and len(guides["tools"]["copy"]) == 1
       and guides["voice"]["copy"] == [("复制安装命令", "pip install pilk")],
       "keys=%s" % sorted(guides))
    ck("g5 引导映射：8 个说明按钮 aid → web GUIDES 键（utGuide/utBarGuide 同指 tools）",
       set(_GUIDE_KEY_OF) == {"utGuide", "utBarGuide", "ttsGuide", "igGuide", "vgGuide",
                              "vsGuide", "irGuide", "fsGuide"}
       and _GUIDE_KEY_OF["utGuide"] == "tools" and _GUIDE_KEY_OF["utBarGuide"] == "tools"
       and _GUIDE_KEY_OF["irGuide"] == "image", str(_GUIDE_KEY_OF))

    # ── 假后端 ──
    calls: list = []
    patch_calls: list = []
    opened: list = []
    wx_save = {"fail": False}
    ut_state = {"n2": False}
    EXPORT_TEXT = '{"tools":[{"name":"weather","host":"api.weather.com","usage":"输入城市名"}]}'
    DOC_JSON = ('{"name":"weather","host":"api.weather.com",'
                '"whitelist":["api.weather.com"],"desc":"查天气"}')
    IMPORT_RESP = {"ok": True, "added": ["weather"], "replaced": [],
                   "skipped": [{"name": "old_tool", "why": "同名工具已存在",
                                "fix": "勾「覆盖同名」"}]}

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/config" in self.path:
                self._send({"ok": True,
                            "api": {"api_key": "old-key", "provider_keys": {"deepseek": "old"}}})
            elif "/api/wechat/dir" in self.path:
                self._send({"ok": True, "now": "C:/WxFiles",
                            "candidates": [
                                {"path": "C:/FakeHome/WeChat Files", "usable": True, "dbs": 7},
                                {"path": "C:/Nope", "usable": False, "why": "没有找到库文件"}]})
            elif "/api/tools/reload" in self.path:
                self._send({"ok": True, "tools": {
                    "tools": [{"name": "weather"}, {"name": "translate"}],
                    "problems": [{"file": "a.json", "why": "域名不在白名单"}]}})
            elif "/api/tools/export" in self.path:
                self._send({"ok": True, "text": EXPORT_TEXT})
            elif "/api/tools/toggle" in self.path:
                self._send({"ok": True})
            elif "/api/status" in self.path:
                tools = [{"name": "weather", "enabled": True, "source": "第三方",
                          "host": "api.weather.com", "calls": 3, "errors": 0}]
                if ut_state["n2"]:
                    tools.append({"name": "translate", "enabled": False, "host": "fanyi.jd.com",
                                  "calls": 2, "errors": 1, "usage": "输入句子"})
                self._send({"ok": True, "user_tools": {
                    "enabled": True, "dir": "tools.d", "tools": tools, "ticked": 1,
                    "counts_total": 5,
                    "problems": [{"file": "a.json", "why": "域名不在白名单", "code_label": "域名",
                                  "code": "host", "fix": "把域名加进白名单"}]}})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/config" in self.path:
                self._send({"ok": True})
            elif "/api/test-api" in self.path:
                self._send({"ok": True, "latency_ms": 234})
            elif "/api/wechat/dir" in self.path:
                if wx_save["fail"]:
                    self._send({"ok": False, "error": "这个目录用不了",
                                "fallback": "C:/Default/WeChat Files"})
                else:
                    self._send({"ok": True, "wechat_dir": {
                        "now": "C:/FakeHome/WeChat Files",
                        "hint": "机器人会从这里读聊天记录"}})
            elif "/api/tools/import" in self.path:
                self._send(IMPORT_RESP)
            elif "/api/file_search/add" in self.path:
                self._send({"ok": True, "note": "目录已加入，共 12 个文件可搜"})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g5").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    mode = {"ok": True}
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if mode["ok"] and hasattr(self, "btn_ok"):
            self.btn_ok.click()
        return self.result()

    QDialog.exec = _fake_exec

    tmp_fd, open_json = _tf.mkstemp(suffix=".json")
    os.write(tmp_fd, DOC_JSON.encode("utf-8"))
    os.close(tmp_fd)
    tmp_fd2, save_json = _tf.mkstemp(suffix=".json")
    os.close(tmp_fd2)
    _orig_gofn = QFileDialog.getOpenFileName
    _orig_gsf = QFileDialog.getSaveFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (open_json, ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (save_json, ""))
    _orig_open = QDesktopServices.openUrl
    QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))

    def _fake_wpatch(patch):
        patch_calls.append(patch)
        return True, "已保存并生效（测试拦截）"

    _orig_wpatch = config_io.write_patch
    config_io.write_patch = _fake_wpatch

    t = THEMES["whale"]

    def _aid(page, aid):
        return [b for b in page.findChildren(Btn) if b.property("web_action") == aid][0]

    def _roww(page, pred):
        for r, w in (getattr(page, "_c8_binds", None) or []):
            if pred(r):
                return w
        return None

    def _posts(prefix):
        """POST 记录 bodies —— GET 记录无 '#'；join_url 把 token 插在 api 后，
        故不能按 'path#body' 前缀直接匹配，要按 '#' 切开取前段判前缀。"""
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _has_post(prefix):
        return any("#" in c and c.split("#", 1)[0].startswith(prefix) for c in calls)

    def _wait(pred, timeout=6.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    def _guide_case(page, aid, key):
        g = guides[key]
        exec_dlgs.clear()
        _aid(page, aid).click()
        QApplication.processEvents()
        dlg = exec_dlgs[-1]
        labels = [l.text() for l in dlg.findChildren(QLabel)]
        steps = [x for x in labels if x.startswith("· ")]
        copies = [b for b in dlg.findChildren(Btn) if b.text() == "复制"]
        ck("g5 %s 引导弹窗：标题/引子/步骤（%d 条）/复制钮（%d 个）与 web GUIDES 一致，知道了可关"
           % (aid, len(g["steps"]), len(g["copy"])),
           dlg.windowTitle() == g["title"] and len(steps) == len(g["steps"])
           and len(copies) == len(g["copy"])
           and g["intro"].replace("**", "") in labels,
           "title=%r steps=%d/%d copy=%d/%d" % (dlg.windowTitle(), len(steps), len(g["steps"]),
                                                len(copies), len(g["copy"])))

    try:
        # ── model 页：keySave / keyReset ──
        wrap_m = panels_qt.build_panel(t, "model")
        mp = wrap_m.widget()
        mp.show()
        QApplication.processEvents()
        key_w = _roww(mp, lambda r: r.cfg == "api.api_key")
        prov_combo = _roww(mp, lambda r: r.kind == "select" and "厂商" in r.label)
        idx = next((i for i in range(prov_combo.count())
                    if str(prov_combo.itemData(i)) not in ("", "custom")), 0)
        prov_combo.setCurrentIndex(idx)
        prov = str(prov_combo.currentData() or "")

        key_w.setText("••••6Kd2")
        calls.clear()
        _aid(mp, "keySave").click()
        QApplication.processEvents()
        note_ks = _aid(mp, "keySave").property("c8_note")
        _wait(lambda: "打码值" in note_ks.text())
        ck("g5 keySave：打码值拒绝（不读不写不发请求）",
           "Key 为空或仍是打码值，未保存" in note_ks.text()
           and not _posts("/api/config"),
           note_ks.text() + " calls=%s" % [c for c in calls if "config" in c])

        key_w.setText("sk-test-abc123")
        calls.clear()
        _aid(mp, "keySave").click()
        _wait(lambda: _has_post("/api/test-api"))
        _wait(lambda: "测试连通成功" in note_ks.text())
        cfg_posts = _posts("/api/config")
        body = cfg_posts[-1] if cfg_posts else {}
        ck("g5 keySave 真值：GET config → POST 全量（api_key=%s + provider_keys[%s]）→ POST test-api 延迟回显"
           % (prov, prov),
           body.get("api", {}).get("api_key") == "sk-test-abc123"
           and body.get("api", {}).get("provider_keys", {}).get(prov) == "sk-test-abc123"
           and _has_post("/api/test-api")
           and "密钥 已保存" in note_ks.text() and "测试连通成功（234ms）" in note_ks.text(),
           "prov=%r note=%r body_api=%s" % (prov, note_ks.text(), body.get("api")))

        _aid(mp, "keyReset").click()
        QApplication.processEvents()
        ck("g5 keyReset：清空 Key 输入框（纯本地不发请求）",
           key_w.text() == "", repr(key_w.text()))

        # ── wechat 页：目录探测/保存/回落/官网 ──
        wrap_w = panels_qt.build_panel(t, "wechat")
        wp = wrap_w.widget()
        wp.show()
        QApplication.processEvents()
        dir_w = _roww(wp, lambda r: r.cfg == "wechat.db_dir")
        dir_w.setText("C:/WxFiles")

        calls.clear()
        _aid(wp, "wxDirProbe").click()
        note_dp = _aid(wp, "wxDirProbe").property("c8_note")
        _wait(lambda: "已探完" in note_dp.text())
        ck("g5 wxDirProbe：GET /api/wechat/dir?path=…，回显当前读 + 候选（✔ 可用带库文件数 / ✘ 带原因）",
           any(c.startswith("/api/wechat/dir?") and "path=" in c for c in calls)
           and "当前在读：C:/WxFiles" in note_dp.text()
           and "✔ C:/FakeHome/WeChat Files，可用，7 个库文件" in note_dp.text()
           and "✘ C:/Nope，没有找到库文件" in note_dp.text(),
           note_dp.text() + " calls=%s" % [c for c in calls if "wechat/dir" in c])

        calls.clear()
        _aid(wp, "wxDirSave").click()
        note_ds = _aid(wp, "wxDirSave").property("c8_note")
        _wait(lambda: "已保存" in note_ds.text())
        ck("g5 wxDirSave 成功：POST {path}，回显现在读的目录 + 服务端 hint",
           any(b.get("path") == "C:/WxFiles" for b in _posts("/api/wechat/dir"))
           and "已保存，现在读的是：C:/FakeHome/WeChat Files" in note_ds.text()
           and "机器人会从这里读聊天记录" in note_ds.text(),
           note_ds.text())

        wx_save["fail"] = True
        calls.clear()
        _aid(wp, "wxDirSave").click()
        _wait(lambda: "回落" in note_ds.text())
        ck("g5 wxDirSave 失败：ok=false → 如实报错 + 回落目录说明",
           "失败：这个目录用不了，当前会回落到 C:/Default/WeChat Files" in note_ds.text(),
           note_ds.text())
        wx_save["fail"] = False

        opened.clear()
        _aid(wp, "wxOpenSite").click()
        QApplication.processEvents()
        note_os = _aid(wp, "wxOpenSite").property("c8_note")
        ck("g5 wxOpenSite：系统浏览器打开 weixin.qq.com + 提醒可手动复制",
           opened == ["https://weixin.qq.com/"]
           and "已尝试打开官网：https://weixin.qq.com/" in note_os.text(),
           str(opened) + " note=" + note_os.text())

        # ── tools 页：清单卡/看问题/引导/导出/导入/重扫 ──
        wrap_t = panels_qt.build_panel(t, "tools")
        tp = wrap_t.widget()
        tp.show()
        QApplication.processEvents()
        _wait(lambda: any(cb.text() == "weather" for cb in tp.findChildren(QCheckBox)),
              timeout=8.0) # appendix 首载（400ms 延时 + 线程）
        prob_lb = tp.findChild(QLabel, "utProblemsQt")
        ck("g5 tools 清单卡首载：/api/status user_tools → 工具勾选行 + 问题清单带 fix 提示",
           any(cb.text() == "weather" for cb in tp.findChildren(QCheckBox))
           and prob_lb is not None and "域名不在白名单" in prob_lb.text()
           and "把域名加进白名单" in prob_lb.text(),
           str([cb.text() for cb in tp.findChildren(QCheckBox) if cb.text()]))

        _aid(tp, "utBarProblems").click()
        QApplication.processEvents()
        note_bp = _aid(tp, "utBarProblems").property("c8_note")
        ck("g5 utBarProblems（看问题）：utProblemsQt 锚点在场，回显已滚动到可见",
           prob_lb is not None and "已滚动到可见" in note_bp.text(), note_bp.text())

        _guide_case(tp, "utGuide", "tools")

        # utExport：弹窗全文 + 复制 + 另存
        exec_dlgs.clear()
        calls.clear()
        _aid(tp, "utExport").click()
        _wait(lambda: exec_dlgs and exec_dlgs[-1].windowTitle() == "导出全部工具")
        edlg = exec_dlgs[-1] if exec_dlgs else None
        eta = edlg.findChildren(QPlainTextEdit)[0] if edlg else None
        ck("g5 utExport：GET /api/tools/export → 弹窗（标题/全文只读 textarea 内容一致）",
           any("/api/tools/export" in c for c in calls) and edlg is not None
           and eta is not None and eta.toPlainText() == EXPORT_TEXT,
           "dlg=%r ta=%r" % (edlg.windowTitle() if edlg else None,
                             eta.toPlainText()[:40] if eta else None))
        [b for b in edlg.findChildren(Btn) if b.text() == "复制文档"][0].click()
        QApplication.processEvents()
        ck("g5 utExport 复制文档：剪贴板 = 导出全文，回显「已复制到剪贴板」",
           QApplication.clipboard().text() == EXPORT_TEXT
           and any(l.text() == "已复制到剪贴板" for l in edlg.findChildren(QLabel)),
           QApplication.clipboard().text()[:40])
        [b for b in edlg.findChildren(Btn) if b.text() == "另存为 .json"][0].click()
        QApplication.processEvents()
        saved = ""
        try:
            saved = open(save_json, encoding="utf-8").read()
        except OSError:
            pass
        ck("g5 utExport 另存为：假对话框选路径 → .json 落盘内容与全文一致，回显已保存到",
           saved == EXPORT_TEXT
           and any(l.text().startswith("已保存到 ") for l in edlg.findChildren(QLabel)),
           "saved=%r" % saved[:40])

        # utImport：空文本护栏 → 文件读入 → 真发（overwrite + skipped 回显 + 清单卡联动）
        mode["ok"] = True
        exec_dlgs.clear()
        calls.clear()
        _aid(tp, "utImport").click()
        _wait(lambda: exec_dlgs and exec_dlgs[-1].windowTitle().startswith("导入工具"))
        dlg1 = exec_dlgs[-1]
        res1 = [l for l in dlg1.findChildren(QLabel) if l.text() == "先选文件、或把内容贴进来"]
        ck("g5 utImport 空文本护栏：直接点导入 → 提示先选文件/贴内容，不发请求",
           bool(res1) and not _posts("/api/tools/import"),
           str([l.text() for l in dlg1.findChildren(QLabel)]))

        mode["ok"] = False
        exec_dlgs.clear()
        _aid(tp, "utImport").click()
        QApplication.processEvents()
        dlg2 = exec_dlgs[-1]
        ta2 = dlg2.findChildren(QPlainTextEdit)[0]
        res2 = [l for l in dlg2.findChildren(QLabel) if not l.text()][0]
        [b for b in dlg2.findChildren(Btn) if b.text() == "选 .json 文件…"][0].click()
        QApplication.processEvents()
        ck("g5 utImport 文件读入：假对话框选 .json → textarea = 文件内容，回显已读入+字符数",
           ta2.toPlainText() == DOC_JSON and "已读入 " in res2.text() and "字符" in res2.text(),
           "ta=%r res=%r" % (ta2.toPlainText()[:30], res2.text()))
        [cb for cb in dlg2.findChildren(QCheckBox) if cb.text() == "覆盖同名工具"][0].setChecked(True)
        ut_state["n2"] = True # 导入完成后清单卡钩子重载时应拿到第二个工具
        calls.clear()
        dlg2.btn_ok.click()
        _wait(lambda: _has_post("/api/tools/import"))
        _wait(lambda: "新增：weather" in res2.text())
        imp_posts = _posts("/api/tools/import")
        imp_body = imp_posts[-1] if imp_posts else {}
        _wait(lambda: any(cb.text() == "translate" for cb in tp.findChildren(QCheckBox)),
              timeout=8.0) # 钩子重载清单卡 → 新工具行出现
        ck("g5 utImport 真发：POST {text, overwrite:true}，回显 新增/跳过（带 fix），go 恢复，清单卡联动刷新",
           imp_body == {"text": DOC_JSON, "overwrite": True}
           and "新增：weather" in res2.text() and "跳过 old_tool" in res2.text()
           and "同名工具已存在" in res2.text()
           and dlg2.btn_ok.text() == "导入"
           and any(cb.text() == "translate" for cb in tp.findChildren(QCheckBox)),
           "body=%s res=%r" % (_json.dumps(imp_body, ensure_ascii=False)[:80], res2.text()))

        calls.clear()
        _aid(tp, "utReload").click()
        note_ur = _aid(tp, "utReload").property("c8_note")
        _wait(lambda: "清单已重扫" in note_ur.text())
        ck("g5 utReload：GET /api/tools/reload，回显数量与问题数并指路面板",
           any("/api/tools/reload" in c for c in calls)
           and "清单已重扫：2 个工具，1 条问题（看面板）" in note_ur.text(),
           note_ur.text())
        _aid(tp, "utReload").click() # 二连点验证清单卡不叠行
        _wait(lambda: "清单已重扫：2 个工具，1 条问题（看面板）" in note_ur.text())
        for _ in range(25):
            QApplication.processEvents()
            _time.sleep(0.02)
        wcs = [cb for cb in tp.findChildren(QCheckBox) if cb.text() == "weather"]
        tcs = [cb for cb in tp.findChildren(QCheckBox) if cb.text() == "translate"]
        ck("g5 utReload 联动清单卡：重扫两次后工具行各 1 份（先清后建不叠行）",
           len(wcs) == 1 and len(tcs) == 1, "weather=%d translate=%d" % (len(wcs), len(tcs)))

        # ── media 页：irGuide / fsAdd ──
        wrap_d = panels_qt.build_panel(t, "media")
        dp = wrap_d.widget()
        dp.show()
        QApplication.processEvents()
        _guide_case(dp, "irGuide", "image")
        _guide_case(dp, "vsGuide", "voice")
        ck("g5 vsGuide 弹窗：可复制安装命令行（pip install pilk）在场（voice 键的 copy）",
           any(l.text() == "pip install pilk" for l in exec_dlgs[-1].findChildren(QLabel)),
           str([l.text() for l in exec_dlgs[-1].findChildren(QLabel)])[:120])

        fs_w = _roww(dp, lambda r: r.kind == "text" and r.label == "可搜目录")
        fs_w.setText("")
        calls.clear()
        _aid(dp, "fsAdd").click()
        note_fa = _aid(dp, "fsAdd").property("c8_note")
        QApplication.processEvents()
        ck("g5 fsAdd 空目录护栏：warn 提示先填目录，不发请求",
           "先填一个目录" in note_fa.text() and not _posts("/api/file_search/add"),
           note_fa.text())
        fs_w.setText("D:/下载")
        _aid(dp, "fsAdd").click()
        _wait(lambda: "目录已加入" in note_fa.text())
        ck("g5 fsAdd 真发：POST {dir} → 回显服务端 note，完成后清空输入行",
           any(b.get("dir") == "D:/下载" for b in _posts("/api/file_search/add"))
           and "目录已加入，共 12 个文件可搜" in note_fa.text() and fs_w.text() == "",
           note_fa.text() + " fs=%r" % fs_w.text())

        # ── tts 页 / videogen 页：引导弹窗 ──
        wrap_s = panels_qt.build_panel(t, "tts")
        sp = wrap_s.widget()
        sp.show()
        QApplication.processEvents()
        _guide_case(sp, "ttsGuide", "tts")
        ck("g5 ttsGuide 弹窗：可复制模板行 = web tts copy 原文（动态取自 GUIDES，不复制进 Qt）",
           guides["tts"]["copy"] and any(l.text() == guides["tts"]["copy"][0][1]
                                         for l in exec_dlgs[-1].findChildren(QLabel)),
           str(guides["tts"]["copy"]))

        wrap_v = panels_qt.build_panel(t, "videogen")
        vp2 = wrap_v.widget()
        vp2.show()
        QApplication.processEvents()
        _guide_case(vp2, "vgGuide", "video")

        # ── wavefx 页：收集 binds 落盘 ──
        wrap_f = panels_qt.build_panel(t, "wavefx")
        fp = wrap_f.widget()
        fp.show()
        QApplication.processEvents()
        sc_w = _roww(fp, lambda r: r.cfg == "ui.wave_fx.scale")
        sp_w = _roww(fp, lambda r: r.cfg == "ui.wave_fx.speed")
        sc_w.setText("17")
        sp_w.setText("3.5")
        patch_calls.clear()
        _aid(fp, "wavefxApply").click()
        note_wf = _aid(fp, "wavefxApply").property("c8_note")
        _wait(lambda: "水光波纹已应用" in note_wf.text())
        p0 = patch_calls[0] if patch_calls else {}
        ck("g5 wavefxApply：收集本页 ui.wave_fx.* 全部 9 行 → write_patch flat 点路径（scale=17 speed=3.5）",
           len(patch_calls) == 1 and len(p0) == 9
           and all(k.startswith("ui.wave_fx.") for k in p0)
           and p0.get("ui.wave_fx.scale") == 17 and p0.get("ui.wave_fx.speed") == 3.5
           and isinstance(p0.get("ui.wave_fx.enabled"), bool)
           and "水光波纹已应用" in note_wf.text(),
           str(p0) + " note=" + note_wf.text())
    finally:
        QFileDialog.getOpenFileName = _orig_gofn
        QFileDialog.getSaveFileName = _orig_gsf
        QDesktopServices.openUrl = _orig_open
        config_io.write_patch = _orig_wpatch
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()
        for f in (open_json, save_json):
            try:
                os.remove(f)
            except OSError:
                pass


def t_g6() -> None:
    """批4第六组全链路真跑：MANUAL 面板剩余裸奔清零。

    persona（prompt 预览/清空）+ overview（计费日历渲染/翻月跨年/loadDay/年份弹层、
    勾选删弹窗全流程）+ check（重置勾选+计数、查看进度条切页）+ json（新窗口查看配置）。

    手法同 t_g5：假后端 + patch current_url/QDialog.exec/QDesktopServices.openUrl/
    config_io.write_patch（防测试真写本机 config.json）。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    import calendar as _cal_mod

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QDesktopServices # noqa: PLC0415
    from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, # noqa: PLC0415
                                   QLabel, QPlainTextEdit)

    import agent_bridge # noqa: PLC0415
    import config_io # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    import panels_custom # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    # ── 假后端 ──
    calls: list = []
    patch_calls: list = []
    opened: list = []
    preview_state = {"err": False}
    list_state = {"empty": False}
    PREVIEW = {"system": "SYS-TEXT-ABC 全文", "chars": 1234,
               "modules": [{"name": "微信场景规则", "enabled": True},
                           {"name": "记忆使用规则", "enabled": False}],
               "custom_chars": 56}
    TDY = _time.strftime("%Y-%m-%d")

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/prompt/preview" in self.path:
                if preview_state["err"]:
                    self._send({"ok": False, "error": "后台炸了"})
                else:
                    self._send(dict(PREVIEW))
            elif "/api/status" in self.path:
                self._send({"ok": True, "paused": False, "wechat_connected": True,
                            "listen": {"groups": 1, "privates": 1},
                            "model": "deepseek-chat", "uptime_s": 5, "groups": []})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/prices" in self.path: # 价目 dict 形态（key=模型名），不是 {"ok":...} 壳
                self._send({"deepseek-chat": {"in": 2.0, "out": 8.0, "cached": 1.0}})
            elif "/api/stats/cal?" in self.path: # cal 本体（cal_list/cal_delete 前缀不同）
                self._send({"ok": True, "sessions": 3, "tokens": 456,
                            "cost": 0.1234, "sent": 12})
            elif "/api/stats/cal_list" in self.path:
                self._send({"ok": True, "bills": [] if list_state["empty"] else [
                    {"day": "2026-09-23", "tokens": 100, "cost": 0.5, "calls": 2, "sessions": 1},
                    {"day": TDY, "tokens": 200, "cost": 1.5, "calls": 4, "sessions": 2}]})
            elif "/api/stats/cal_delete" in self.path:
                self._send({"ok": True, "removed": list(body.get("days") or [])})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g6").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    mode = {"ok": True}
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if mode["ok"] and hasattr(self, "btn_ok"):
            self.btn_ok.click()
        return self.result()

    QDialog.exec = _fake_exec
    _orig_open = QDesktopServices.openUrl
    QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))

    def _fake_wpatch(patch):
        patch_calls.append(patch)
        return True, "已保存并生效（测试拦截）"

    _orig_wpatch = config_io.write_patch
    config_io.write_patch = _fake_wpatch

    t = THEMES["whale"]

    def _btn_by_text(page, txt):
        hits = [b for b in page.findChildren(Btn) if b.text() == txt]
        return hits[0] if hits else None

    def _posts(prefix):
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _has_post(prefix):
        return any("#" in c and c.split("#", 1)[0].startswith(prefix) for c in calls)

    def _wait(pred, timeout=6.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        # ── persona 页：prompt 预览 / 清空 ──
        wrap_p = panels_qt.build_panel(t, "persona")
        pp = wrap_p.widget()
        pp.show()
        QApplication.processEvents()
        b_prev = _btn_by_text(pp, "预览当前系统提示词")
        b_pclr = _btn_by_text(pp, "清空补充")
        ck("g6 persona 两钮在场（预览当前系统提示词 / 清空补充）",
           b_prev is not None and b_pclr is not None, "prev=%s clr=%s" % (b_prev, b_pclr))

        b_prev.click()
        _wait(lambda: bool(exec_dlgs) and exec_dlgs[-1].windowTitle() == "当前系统提示词（服务端现算）")
        dlg0 = exec_dlgs[-1]
        ta0 = dlg0.findChildren(QPlainTextEdit)[0]
        labels0 = [l.text() for l in dlg0.findChildren(QLabel)]
        ck("g6 prompt 预览：GET /api/prompt/preview，弹窗回显服务端全文 + info 行（字符/启用模块/补充字符）",
           any("/api/prompt/preview" in c for c in calls)
           and ta0.toPlainText() == "SYS-TEXT-ABC 全文"
           and "1234 字符 ｜ 已启用模块：微信场景规则 ｜ 自定义补充 56 字符" in labels0
           and _btn_by_text(dlg0, "知道了") is not None,
           "ta=%r labels=%s" % (ta0.toPlainText()[:40], [x for x in labels0 if "字符" in x]))

        preview_state["err"] = True
        n_dlgs = len(exec_dlgs)
        b_prev.click()
        _wait(lambda: any(l.text().startswith("预览失败：后台炸了") for l in pp.findChildren(QLabel)))
        ck("g6 prompt 预览失败路径：note 回显服务端 error，不弹窗",
           len(exec_dlgs) == n_dlgs,
           str([l.text() for l in pp.findChildren(QLabel) if "预览失败" in l.text()]))
        preview_state["err"] = False

        sys_area = [e for e in pp.findChildren(QPlainTextEdit)
                    if e.placeholderText() == "追加到系统提示词末尾（管理员补充，最高优先级）"][0]
        n_dlgs = len(exec_dlgs)
        b_pclr.click()
        QApplication.processEvents()
        ck("g6 清空护栏：补充本来就空 → note「本来就是空的」，不弹确认不发落盘",
           len(exec_dlgs) == n_dlgs and not patch_calls
           and any(l.text() == "本来就是空的" for l in pp.findChildren(QLabel)),
           str([l.text() for l in pp.findChildren(QLabel) if "空" in l.text()][:4]))

        sys_area.setPlainText("临时补充文字")
        patch_calls.clear()
        b_pclr.click()
        _wait(lambda: bool(exec_dlgs) and exec_dlgs[-1].windowTitle() == "清空「系统提示词补充」？")
        QApplication.processEvents()
        _wait(lambda: sys_area.toPlainText() == "" and bool(patch_calls))
        _wait(lambda: any(l.text() == "已清空系统提示词补充（下一轮生效）" for l in pp.findChildren(QLabel)))
        ck("g6 清空真发：确认弹窗 → textarea 清空 + write_patch 含 {system_prompt.custom: ''} + note 回显"
           "（可能再混一条 binds 全量 = 「改完即生效」600ms 防抖，真产品行为）",
           sys_area.toPlainText() == ""
           and {"system_prompt.custom": ""} in patch_calls
           and any(l.text() == "已清空系统提示词补充（下一轮生效）" for l in pp.findChildren(QLabel)),
           "ta=%r patches=%s" % (sys_area.toPlainText(), str(patch_calls)[:120]))

        # ── overview 页：计费日历 + 勾选删 ──
        wrap_o = panels_qt.build_panel(t, "overview")
        op = wrap_o.widget()
        op.show()
        QApplication.processEvents()
        ym_txt = "%d年%d月" % (_time.localtime().tm_year, _time.localtime().tm_mon)
        b_ym = _btn_by_text(op, ym_txt)
        n_days = _cal_mod.monthrange(_time.localtime().tm_year, _time.localtime().tm_mon)[1]
        day_btns = [b for b in op.findChildren(Btn) if b.text().isdigit() and len(b.text()) <= 2]
        today_btn = [b for b in day_btns if b.text() == str(_time.localtime().tm_mday)]
        ck("g6 日历静态：年月标签 = 当前月，数字按钮 = 当月天数，今天描蓝边",
           b_ym is not None and len(day_btns) == n_days and today_btn
           and "border:1px solid" in today_btn[0].styleSheet(),
           "ym=%s days=%d/%d today=%s" % (ym_txt, len(day_btns), n_days,
                                          bool(today_btn) and today_btn[0].styleSheet()[:40]))

        for _ in range(3):
            _btn_by_text(op, "›").click()
            QApplication.processEvents()
        ym_after_next = "%d年%d月" % (_time.localtime().tm_year, 12)
        ck("g6 翻月 next：9 月 → 12 月（标签同步）",
           _btn_by_text(op, ym_after_next) is not None, ym_after_next)
        _btn_by_text(op, "›").click()
        QApplication.processEvents()
        ck("g6 翻月跨年进位：12 月 → 次年 1 月",
           _btn_by_text(op, "%d年1月" % (_time.localtime().tm_year + 1)) is not None,
           "%d年1月" % (_time.localtime().tm_year + 1))
        for _ in range(4):
            _btn_by_text(op, "‹").click()
            QApplication.processEvents()
        ck("g6 翻月 prev 回起点：跨年后 4 次 ‹ 回到当前月",
           _btn_by_text(op, ym_txt) is not None, ym_txt)

        calls.clear()
        d15 = [b for b in op.findChildren(Btn) if b.text() == "15"][0]
        d15.click()
        _wait(lambda: _has_post("/api/stats/cal?"))
        _wait(lambda: any(l.text().startswith(TDY[:8] + "15：") and "会话" in l.text()
                          for l in op.findChildren(QLabel)))
        cal_body = _posts("/api/stats/cal?")
        detail = [l.text() for l in op.findChildren(QLabel)
                  if l.text().startswith(TDY[:8] + "15：")]
        ck("g6 loadDay：点 15 号 → POST {d: 当月15日}，明细行「X 会话 · Y tok · ¥Z · N 条」",
           bool(cal_body) and cal_body[-1] == {"d": "%s-15" % TDY[:7]}
           and bool(detail) and detail[0] == "%s15：3 会话 · 456 tok · ¥0.1234 · 12 条" % TDY[:8],
           "body=%s detail=%s" % (cal_body[-1:] and cal_body[-1], detail))

        b_ym2 = _btn_by_text(op, ym_txt)
        n_dlgs = len(exec_dlgs)
        b_ym2.click()
        QApplication.processEvents()
        dlg_y = exec_dlgs[-1] if len(exec_dlgs) > n_dlgs else None
        ybtns = ([b for b in dlg_y.findChildren(Btn) if b.text().isdigit()] if dlg_y else [])
        cur_y = _time.localtime().tm_year
        y2024 = [b for b in ybtns if b.text() == str(cur_y - 2)]
        ck("g6 年份弹层：标题「跳到年份」，11 个年份按钮，点前年跳转保持当前月",
           dlg_y is not None and dlg_y.windowTitle() == "跳到年份" and len(ybtns) == 11
           and y2024 is not None and bool(y2024), "n=%d" % len(ybtns))
        if y2024:
            y2024[0].click()
            QApplication.processEvents()
            ck("g6 年份跳转后标签 = 2024年9月（当前年 -2）",
               _btn_by_text(op, "%d年%d月" % (cur_y - 2, _time.localtime().tm_mon)) is not None,
               "%d年%d月" % (cur_y - 2, _time.localtime().tm_mon))
            for _ in range(40): # 翻月回当前月（ym 编码是月进位，跨年要逐月翻）
                if _btn_by_text(op, ym_txt) is not None:
                    break
                _btn_by_text(op, "›").click()
                QApplication.processEvents()
            ck("g6 年份跳转后翻月回到当前月",
               _btn_by_text(op, ym_txt) is not None, ym_txt)

        list_state["empty"] = True
        n_dlgs = len(exec_dlgs)
        _btn_by_text(op, "勾选删").click()
        _wait(lambda: any(l.text() == "当前没有可删除的计费日志" for l in op.findChildren(QLabel)))
        ck("g6 勾选删空护栏：cal_list 空 → note 如实提示，不弹窗",
           len(exec_dlgs) == n_dlgs
           and any(l.text() == "当前没有可删除的计费日志" for l in op.findChildren(QLabel)),
           "n_dlgs=%d" % len(exec_dlgs))

        list_state["empty"] = False
        n_dlgs = len(exec_dlgs)
        _btn_by_text(op, "勾选删").click()
        _wait(lambda: len(exec_dlgs) > n_dlgs and exec_dlgs[-1].windowTitle() == "勾选删除计费日志")
        bd = exec_dlgs[-1]
        bd_cks = bd.findChildren(QCheckBox)
        ck("g6 勾选删弹窗：列表 2 行（day · tok · ¥cost · 次 · 会话），hint 共 2 天",
           len(bd_cks) == 2
           and bd_cks[0].text().startswith(TDY)
           and bd_cks[1].text().startswith("2026-09-23")
           and "¥1.5000" in bd_cks[0].text() and "¥0.5000" in bd_cks[1].text()
           and any("共 2 天" in l.text() for l in bd.findChildren(QLabel)),
           "n=%d texts=%s" % (len(bd_cks), [c.text()[:24] for c in bd_cks]))

        _btn_by_text(bd, "全选").click()
        QApplication.processEvents()
        bsum = [l for l in bd.findChildren(QLabel) if l.text().startswith("已选 ")]
        ck("g6 全选 → 汇总「已选 2 天 · 300 tok · ¥2.0000（全部选中）」",
           bool(bsum) and bsum[0].text() == "已选 2 天 · 300 tok · ¥2.0000（全部选中）",
           bsum[0].text() if bsum else "")
        _btn_by_text(bd, "勾选今日").click()
        QApplication.processEvents()
        bsum = [l for l in bd.findChildren(QLabel) if l.text().startswith("已选 ")]
        ck("g6 勾选今日：只勾今天的行，汇总 1 天 · 200 tok · ¥1.5000",
           bool(bsum) and bsum[0].text() == "已选 1 天 · 200 tok · ¥1.5000"
           and bd_cks[0].isChecked() and not bd_cks[1].isChecked(),
           bsum[0].text() if bsum else "")
        _btn_by_text(bd, "勾选本月").click()
        QApplication.processEvents()
        ck("g6 勾选本月：两条 2026-09 记录都勾上（汇总回到 2 天）",
           bd_cks[0].isChecked() and bd_cks[1].isChecked()
           and any(l.text().startswith("已选 2 天") for l in bd.findChildren(QLabel)), "")

        _btn_by_text(bd, "清空勾选").click()
        QApplication.processEvents()
        n_dlgs2 = len(exec_dlgs)
        _btn_by_text(bd, "确认删除").click()
        QApplication.processEvents()
        ck("g6 空选护栏：确认删除提示「请先勾选要删除的天」，不进二次确认不发请求",
           len(exec_dlgs) == n_dlgs2
           and any(l.text() == "请先勾选要删除的天" for l in bd.findChildren(QLabel))
           and not _has_post("/api/stats/cal_delete"),
           str([l.text() for l in bd.findChildren(QLabel) if "勾选" in l.text()][:3]))

        _btn_by_text(bd, "全选").click()
        QApplication.processEvents()
        calls.clear()
        _btn_by_text(bd, "确认删除").click()
        _wait(lambda: _has_post("/api/stats/cal_delete"))
        _wait(lambda: not bd.isVisible())
        _wait(lambda: any(l.text() == "已删除 2 天计费日志" for l in op.findChildren(QLabel)))
        QApplication.processEvents()
        del_posts = _posts("/api/stats/cal_delete")
        ck("g6 真删：二次确认 → POST {days: [两日]} → 弹窗关 + note「已删除 2 天计费日志」+ 概览刷新",
           bool(del_posts) and sorted(del_posts[-1].get("days") or []) == ["2026-09-23", TDY]
           and not bd.isVisible()
           and any(l.text() == "已删除 2 天计费日志" for l in op.findChildren(QLabel))
           and any("/api/status" in c for c in calls),
           "body=%s visible=%s" % (del_posts[-1] if del_posts else None, bd.isVisible()))

        # ── check 页：重置勾选 + 查看进度条 ──
        wrap_c = panels_qt.build_panel(t, "check")
        cp = wrap_c.widget()
        cp.show()
        QApplication.processEvents()
        list_cks = [c for c in cp.findChildren(QCheckBox)
                    if c.toolTip() == "勾选=这项测过了（会话内状态；web 侧用 localStorage 记忆）"]
        cnt0 = [l for l in cp.findChildren(QLabel) if l.text() == "已完成 0 / 11"]
        ck("g6 清单计数：11 个勾选行（tooltip 同源）+ 计数标签「已完成 0 / 11」初始",
           len(list_cks) == 11 and bool(cnt0), "n=%d cnt=%s" % (len(list_cks), bool(cnt0)))
        for c in list_cks[:3]:
            c.setChecked(True)
        QApplication.processEvents()
        cnt3 = [l for l in cp.findChildren(QLabel) if l.text() == "已完成 3 / 11"]
        _btn_by_text(cp, "重置勾选").click()
        QApplication.processEvents()
        cnt_reset = [l for l in cp.findChildren(QLabel) if l.text() == "已完成 0 / 11"]
        ck("g6 重置勾选：勾 3 项计数 3/11 → 重置全部清零回到 0/11",
           bool(cnt3) and not any(c.isChecked() for c in list_cks) and bool(cnt_reset),
           "cnt3=%s reset=%s" % (bool(cnt3), bool(cnt_reset)))

        b_tip2 = _btn_by_text(cp, "查看进度条")
        ck("g6 查看进度条钮在场（web codeCheckTip2 同名）",
           b_tip2 is not None, str(b_tip2))
        if b_tip2 is not None:
            n_dlgs = len(exec_dlgs)
            b_tip2.click()
            QApplication.processEvents()
            fb = [l.text() for l in cp.findChildren(QLabel)
                  if "常驻状态条" in l.text() and "概览" in l.text()]
            ck("g6 查看进度条无壳护栏：page 无 _go → fallback 提示指路概览，不崩",
               len(exec_dlgs) == n_dlgs and bool(fb),
               str(fb[:1]))
            jumps: list = []
            b_tip2.window()._go = lambda sec, label: jumps.append((sec, label)) # 模拟 Shell 切页
            b_tip2.click()
            QApplication.processEvents()
            ck("g6 查看进度条切页：_go('overview') 恰一次",
               jumps == [("overview", panels_custom.sec_meta.get("overview").title)],
               str(jumps))

        # ── json 页：新窗口查看配置 ──
        if str(panels_custom.ROOT) not in sys.path:
            sys.path.insert(0, str(panels_custom.ROOT)) # json 页 import agent.config 需要产品根
        wrap_j = panels_qt.build_panel(t, "json")
        jp = wrap_j.widget()
        jp.show()
        QApplication.processEvents()
        opened.clear()
        b_raw = _btn_by_text(jp, "新窗口查看配置")
        if b_raw is not None:
            b_raw.click()
            QApplication.processEvents()
        ck("g6 新窗口查看配置：openUrl(join_url(current, /api/config))（web rawJsonBtn 等效）",
           b_raw is not None and len(opened) == 1 and "/api/config" in opened[0],
           "opened=%s" % opened)
    finally:
        QDesktopServices.openUrl = _orig_open
        config_io.write_patch = _orig_wpatch
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()


def t_g7() -> None:
    """批4第七组全链路真跑：memory 编辑/深挖弹窗（memEdit/deep-profile）。

    手法同 t_g6：假后端 + patch current_url/QDialog.exec（exec 打开即返回、
    控件树留内存供驱动）。断言：编辑弹窗预填与整份覆盖（按行 trim 滤空）、
    清空=删除全部、失败弹窗不关；深挖请求/结果弹窗/追加合并旧印象、不可整理路径。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QLabel, # noqa: PLC0415
                                   QListWidget, QPlainTextEdit)

    import agent_bridge # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    post_state = {"err": False}
    deep_state = {"ok": True}
    M1 = {"userId": "U1", "name": "阿明", "updatedAt": "09-25 01:02",
          "impressions": [{"content": "印象一"}, {"content": "印象二"}]}

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            self._send({"chat_key": "CK",
                        "chats": [{"chat_key": "CK", "name": "测试群", "count": 2}],
                        "members": [dict(M1)]})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/memory/deep-profile" in self.path:
                if deep_state["ok"]:
                    self._send({"ok": True, "note": "由 3 条历史整理", "text": "深度A\n深度B"})
                else:
                    self._send({"ok": False, "note": "没有可整理的历史"})
            elif "/api/memory" in self.path:
                if post_state["err"]:
                    self._send({"ok": False, "error": "后台炸了"})
                else:
                    self._send({"ok": True})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g7").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        return self.result()

    QDialog.exec = _fake_exec

    t = THEMES["whale"]

    def _btn_by_text(root, txt):
        hits = [b for b in root.findChildren(Btn) if b.text() == txt]
        return hits[0] if hits else None

    def _row_btn(pp, txt):
        """只在 memTable **当前**行的 itemWidget 里找按钮——绕开 clear() 后
        尚未销毁的旧行幽灵（旧闭包 chat_key 停留在选群前）。"""
        lst = pp.findChild(QListWidget, "memTable")
        if lst is None:
            return None
        for i in range(lst.count()):
            w = lst.itemWidget(lst.item(i))
            if w is None:
                continue
            for b in w.findChildren(Btn):
                if b.text() == txt:
                    return b
        return None

    def _posts(prefix):
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _gets_ck():
        return [c for c in calls if c.startswith("/api/memory?token=tk&chat_key=CK")]

    def _gets(path):
        return [c for c in calls if c == path]

    def _notes(pp):
        return [l.text() for l in pp.findChildren(QLabel)]

    def _wait(pred, timeout=6.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        wrap = panels_qt.build_panel(t, "memory")
        pp = wrap.widget()
        pp.show()
        QApplication.processEvents()

        b_rf = _btn_by_text(pp, "刷新")
        ck("g7 刷新钮在场", b_rf is not None, "rf=%s" % b_rf)
        b_rf.click()
        _wait(lambda: _row_btn(pp, "编辑") is not None)
        ck("g7 行内三钮在场（编辑/深度印象/删除）",
           _row_btn(pp, "编辑") is not None
           and _row_btn(pp, "深度印象") is not None
           and _row_btn(pp, "删除") is not None,
           "edit=%s deep=%s del=%s" % (_row_btn(pp, "编辑"),
                                       _row_btn(pp, "深度印象"),
                                       _row_btn(pp, "删除")))

        chat_sel = pp.findChild(QComboBox, "memChats")
        ck("g7 群选下拉存在且已填充", chat_sel is not None and chat_sel.count() >= 2,
           "sel=%s n=%s" % (chat_sel is not None, chat_sel.count() if chat_sel else -1))
        chat_sel.setCurrentIndex(1) # data="CK" → 按群加载
        _wait(lambda: len(_gets_ck()) >= 1)
        ck("g7 选群触发按群加载 GET /api/memory?chat_key=CK",
           len(_gets_ck()) >= 1, str(calls[-3:]))

        # ── 编辑弹窗：预填 → 整份覆盖（滤空行）→ 刷新联动 ──
        n_dlgs = len(exec_dlgs)
        _row_btn(pp, "编辑").click()
        _wait(lambda: len(exec_dlgs) > n_dlgs)
        dlg1 = exec_dlgs[-1]
        ta1 = dlg1.findChild(QPlainTextEdit)
        ck("g7 编辑弹窗：标题+说明+预填两行",
           dlg1.windowTitle() == "编辑「阿明」的印象"
           and ta1 is not None
           and ta1.toPlainText() == "印象一\n印象二"
           and any(l.text() == "每行一条印象；清空=删除全部。"
                   for l in dlg1.findChildren(QLabel)),
           "title=%r ta=%r" % (dlg1.windowTitle(),
                               ta1.toPlainText() if ta1 else None))

        ta1.setPlainText("  新A  \n\n新B")
        n_posts = len(_posts("/api/memory"))
        _btn_by_text(dlg1, "保存").click()
        _wait(lambda: len(_posts("/api/memory")) > n_posts
              and "已更新" in _notes(pp))
        bodies = _posts("/api/memory")
        ck("g7 编辑保存：action:update 整份覆盖（trim+滤空行）+ 弹窗关 + 刷新联动",
           bodies and bodies[-1] == {"action": "update", "chat_key": "CK",
                                     "user_id": "U1", "name": "阿明",
                                     "contents": ["新A", "新B"]}
           and dlg1.result() == 1
           and len(_gets_ck()) >= 2
           and "已更新" in _notes(pp),
           "body=%s result=%s gets=%d" % (bodies[-1] if bodies else None,
                                          dlg1.result(),
                                          len(_gets_ck())))

        # ── 清空=删除全部（web 同语义）──
        _row_btn(pp, "编辑").click()
        _wait(lambda: len(exec_dlgs) > n_dlgs + 1)
        dlg2 = exec_dlgs[-1]
        dlg2.findChild(QPlainTextEdit).setPlainText("")
        n_posts = len(_posts("/api/memory"))
        _btn_by_text(dlg2, "保存").click()
        _wait(lambda: len(_posts("/api/memory")) > n_posts)
        ck("g7 清空保存 → contents=[]（删除全部）",
           _posts("/api/memory")[-1]["contents"] == [],
           str(_posts("/api/memory")[-1]))

        # ── 编辑保存失败：弹窗不关、弹窗内回显原因 ──
        post_state["err"] = True
        _row_btn(pp, "编辑").click()
        _wait(lambda: len(exec_dlgs) > n_dlgs + 2)
        dlg3 = exec_dlgs[-1]
        _btn_by_text(dlg3, "保存").click()
        _wait(lambda: any("更新失败：后台炸了" in l.text()
                          for l in dlg3.findChildren(QLabel)))
        ck("g7 编辑失败：弹窗不关（result 未置位）+ 弹窗内回显「更新失败：后台炸了」",
           dlg3.result() == 0
           and any("更新失败：后台炸了" in l.text()
                   for l in dlg3.findChildren(QLabel)),
           "result=%s" % dlg3.result())
        dlg3.reject()
        post_state["err"] = False

        # ── 深度印象：请求 → 结果弹窗 → 追加合并 ──
        n_dlgs = len(exec_dlgs)
        _row_btn(pp, "深度印象").click()
        _wait(lambda: len(_posts("/api/memory/deep-profile")) >= 1)
        ck("g7 深挖请求体 {user_id, name}",
           _posts("/api/memory/deep-profile")[-1]
           == {"user_id": "U1", "name": "阿明"},
           str(_posts("/api/memory/deep-profile")[-1]))
        _wait(lambda: len(exec_dlgs) > n_dlgs)
        dlg4 = exec_dlgs[-1]
        ta4 = dlg4.findChild(QPlainTextEdit)
        ck("g7 深挖弹窗：标题+整理说明+稿件预填",
           dlg4.windowTitle() == "「阿明」深度印象"
           and ta4 is not None and ta4.toPlainText() == "深度A\n深度B"
           and any(l.text() == "由 3 条历史整理" for l in dlg4.findChildren(QLabel)),
           "title=%r ta=%r" % (dlg4.windowTitle(),
                               ta4.toPlainText() if ta4 else None))

        n_posts = len(_posts("/api/memory"))
        _btn_by_text(dlg4, "追加为印象").click()
        _wait(lambda: len(_posts("/api/memory")) > n_posts
              and "已追加印象" in _notes(pp))
        bodies = _posts("/api/memory")
        ck("g7 追加=旧印象+稿件合并整份覆盖 + 弹窗关",
           bodies and bodies[-1] == {"action": "update", "chat_key": "CK",
                                     "user_id": "U1", "name": "阿明",
                                     "contents": ["印象一", "印象二", "深度A", "深度B"]}
           and dlg4.result() == 1
           and "已追加印象" in _notes(pp),
           "body=%s result=%s" % (bodies[-1] if bodies else None, dlg4.result()))

        # ── 不可整理路径：note 回显、不弹窗 ──
        deep_state["ok"] = False
        n_dlgs = len(exec_dlgs)
        _btn_by_text(pp, "深度印象").click()
        _wait(lambda: "暂无可整理的记录：没有可整理的历史" in _notes(pp))
        ck("g7 深挖不可整理：note 回显原因、不弹窗",
           "暂无可整理的记录：没有可整理的历史" in _notes(pp)
           and len(exec_dlgs) == n_dlgs,
           str([x for x in _notes(pp) if "暂无" in x]))
    finally:
        QDialog.exec = _orig_exec
        agent_bridge.current_url = _orig_url
        srv.shutdown()


def t_g8() -> None:
    """批4第八组：模型厂商下拉联动（web applyProvider :4799-4828 移植）。

    手法：model 页真构建（sec_meta 运行时解析真 web 源码，厂商下拉由元数据
    自动生成）+ patch config_io.read_path（隔离本机 provider_keys）+ patch
    QDialog.exec。断言：activated 触发接口地址回填、已存 Key 回填（打码不回填）、
    弹窗条件三分支（空 Key 弹/非打码非 deepseek 不弹/deepseek 无已存必弹）、三钮行为。
    """
    import os # noqa: PLC0415
    import time as _time # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, # noqa: PLC0415
                                   QLabel, QLineEdit)

    import config_io # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])
    t = THEMES["whale"]

    _orig_rp = config_io.read_path
    state = {"keys": {}}

    def _fake_rp(path, default=None): # noqa: ANN001
        if str(path) == "api.provider_keys":
            return state["keys"]
        return _orig_rp(path, default)

    config_io.read_path = _fake_rp

    exec_dlgs: list = []
    _orig_exec = QDialog.exec
    QDialog.exec = lambda self, *a, **k: (exec_dlgs.append(self), 0)[1]

    def _btn_by_text(root, txt):
        hits = [b for b in root.findChildren(Btn) if b.text() == txt]
        return hits[0] if hits else None

    def _wait(pred, timeout=6.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        wrap = panels_qt.build_panel(t, "model")
        page = wrap.widget()
        page.show()
        QApplication.processEvents()

        base_w = key_w = None
        for r, w in (getattr(page, "_c8_binds", None) or []):
            cfg = str(getattr(r, "cfg", "") or "")
            if cfg == "api.base_url":
                base_w = w
            elif cfg == "api.api_key":
                key_w = w
        # 厂商选择器是追加卡手写行（web 无 data-cfg，元数据不覆盖）
        prov_w = page.findChild(QComboBox, "providerSel")
        ck("g8 厂商选择行 + 接口地址/密钥行在场",
           isinstance(prov_w, QComboBox) and prov_w.count() >= 5
           and base_w is not None and key_w is not None,
           "prov=%s n=%s base=%s key=%s" % (isinstance(prov_w, QComboBox),
                                            prov_w.count() if prov_w else -1,
                                            base_w is not None, key_w is not None))

        def _act(key):
            for i in range(prov_w.count()):
                if str(prov_w.itemData(i)) == key:
                    return i
            return -1

        # ① 切智谱（无已存 Key、密钥行空 → 弹）：base 回填 + 弹窗结构
        state["keys"] = {}
        key_w.setText("")
        n0 = len(exec_dlgs)
        prov_w.activated.emit(_act("zhipu"))
        QApplication.processEvents()
        _wait(lambda: len(exec_dlgs) > n0)
        dlg1 = exec_dlgs[-1]
        ed1 = dlg1.findChild(QLineEdit)
        ck("g8 切厂商回填接口地址 + 密钥弹窗（标题/说明/密码框/三钮）",
           base_w.text() == "https://open.bigmodel.cn/api/paas/v4"
           and dlg1.windowTitle() == "智谱 GLM 密钥"
           and any("open.bigmodel.cn" in l.text() for l in dlg1.findChildren(QLabel))
           and ed1 is not None
           and _btn_by_text(dlg1, "保存 Key") is not None
           and _btn_by_text(dlg1, "沿用现有 Key") is not None
           and _btn_by_text(dlg1, "暂不填") is not None,
           "base=%r title=%r ed=%s" % (base_w.text(), dlg1.windowTitle(),
                                       ed1 is not None))

        # ② 保存 Key = 填入密钥行（落盘仍走「保存 Key/保存设置」）
        ed1.setText("sk-g8-test-9")
        _btn_by_text(dlg1, "保存 Key").click()
        QApplication.processEvents()
        ck("g8 保存 Key → 填入密钥行 + 弹窗关",
           key_w.text() == "sk-g8-test-9" and dlg1.result() == 1,
           "key=%r result=%s" % (key_w.text(), dlg1.result()))

        # ③ 非打码且非 deepseek → 只回填 base，不弹窗
        n1 = len(exec_dlgs)
        prov_w.activated.emit(_act("minimax"))
        QApplication.processEvents()
        ck("g8 非打码且非 deepseek：base 回填但不弹窗",
           base_w.text().startswith("https://api.minimaxi.com")
           and len(exec_dlgs) == n1,
           "base=%r dlgs=%d" % (base_w.text(), len(exec_dlgs)))

        # ④ deepseek 无已存 Key → 即使密钥行有值也必弹（首次必问口径）
        prov_w.activated.emit(_act("deepseek"))
        _wait(lambda: len(exec_dlgs) > n1)
        dlg2 = exec_dlgs[-1]
        ck("g8 deepseek 无已存 Key：非打码也必弹",
           dlg2.windowTitle() == "DeepSeek 密钥"
           and len(exec_dlgs) == n1 + 1,
           "title=%r dlgs=%d" % (dlg2.windowTitle(), len(exec_dlgs)))

        # ⑤ 已存真实 Key 自动回填且不再弹窗
        _btn_by_text(dlg2, "暂不填").click()
        state["keys"] = {"zhipu": "sk-saved-z-7"}
        key_w.setText("sk-***abc") # 打码形态（模拟页面显示值）
        n2 = len(exec_dlgs)
        prov_w.activated.emit(_act("zhipu"))
        QApplication.processEvents()
        ck("g8 已存真实 Key 自动回填且不再弹窗",
           key_w.text() == "sk-saved-z-7"
           and base_w.text() == "https://open.bigmodel.cn/api/paas/v4"
           and len(exec_dlgs) == n2,
           "key=%r base=%r dlgs=%d" % (key_w.text(), base_w.text(), len(exec_dlgs)))

        # ⑥ 打码的已存 Key 不回填（防误存），按空 Key 弹窗
        state["keys"] = {"moonshot": "sk-***xyz"}
        key_w.setText("")
        n3 = len(exec_dlgs)
        prov_w.activated.emit(_act("moonshot"))
        QApplication.processEvents()
        _wait(lambda: len(exec_dlgs) > n3)
        dlg3 = exec_dlgs[-1]
        ed3 = dlg3.findChild(QLineEdit)
        ck("g8 打码 saved 不回填、按空 Key 弹窗（密码框预填空）",
           key_w.text() == "" and dlg3.windowTitle() == "Moonshot Kimi 密钥"
           and ed3 is not None and ed3.text() == "",
           "key=%r title=%r ed=%r" % (key_w.text(), dlg3.windowTitle(),
                                      ed3.text() if ed3 else None))

        # ⑦ 「暂不填」= 关窗不动
        n4 = len(exec_dlgs)
        _btn_by_text(dlg3, "暂不填").click()
        QApplication.processEvents()
        ck("g8 暂不填 = 关窗、密钥行不动",
           key_w.text() == "" and dlg3.result() == 0
           and len(exec_dlgs) == n4,
           "key=%r result=%s" % (key_w.text(), dlg3.result()))
    finally:
        QDialog.exec = _orig_exec
        config_io.read_path = _orig_rp


def t_g9() -> None:
    """批4第九组：uiConfirm 调用点集中补（确认补齐 8 处 + 缺失动作 2 处）。

    手法同 t_g7：假后端 + patch current_url/QDialog.exec（auto-click btn_ok，
    ConfirmDialog 确认自动通过）。断言九条主链：sessions 勾选删、存档真删
    （含屏蔽无确认对照）、memory 删成员/清勾选/清全部、persona 恢复上一
    （新增确认）与移分区（uiPrompt 等价弹窗）、emoji 删除、updbar 重置更新状态。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, # noqa: PLC0415
                                   QListWidget, QLineEdit, QPushButton)

    import agent_bridge # noqa: PLC0415
    import config_io # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    import panels_custom # noqa: PLC0415
    import updbar as updbar_mod # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    M1 = {"userId": "U1", "name": "阿明", "impressions": [{"content": "印象一"}]}

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/emojis" in self.path:
                self._send({"emojis": [{"name": "e1.png"}]})
            elif "/api/persona/cats" in self.path:
                self._send({"built": ["自定义"], "user": []})
            elif "/api/personas/custom" in self.path:
                self._send({"custom": []})
            elif "/api/personas/scores" in self.path:
                self._send({"rows": []})
            elif "/api/personas/favs" in self.path:
                self._send({})
            elif "/api/personas" in self.path:
                self._send({"personas": [{"key": "custom:c1", "name": "老王",
                                          "text": "你是老王", "cat": "自定义"}]})
            elif "/api/sessions" in self.path:
                self._send({"sessions": [{"ts": "2026-09-25T10:00:00", "text": "a"},
                                         {"ts": "2026-09-25T11:00:00", "text": "b"}]})
            elif "/api/memory" in self.path:
                self._send({"chat_key": "CK",
                            "chats": [{"chat_key": "CK", "name": "测试群", "count": 2}],
                            "members": [dict(M1)]})
            elif "/api/config" in self.path:
                self._send({"persona": {"bot_name": "x", "role_text": "y"}, "api": {}})
            elif "/api/wechat-groups" in self.path:
                self._send({"groups": [{"name": "g1", "wxid": "g1"}]})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/update_reset" in self.path:
                self._send({"ok": True, "before": "9.9.9", "after": "清空"})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g9").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    _orig_exec = QDialog.exec

    def _fake_exec(self, *a, **k):
        exec_dlgs.append(self)
        if hasattr(self, "btn_ok"): # ConfirmDialog 确认钮 → 自动「确定」
            self.btn_ok.click()
        return self.result()

    QDialog.exec = _fake_exec

    _orig_rp = config_io.read_path

    def _fake_rp(path, default=None): # noqa: ANN001
        if str(path) == "persona.last_used":
            return {"name": "旧人设", "text": "旧设定"}
        return _orig_rp(path, default)

    config_io.read_path = _fake_rp

    t = THEMES["whale"]

    def _btn_by_text(root, txt):
        hits = [b for b in root.findChildren(Btn) if b.text() == txt]
        return hits[0] if hits else None

    def _posts(prefix):
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _n_posts(prefix):
        return len(_posts(prefix))

    def _row_check(root, list_name):
        """从指定 QListWidget 当前行的 itemWidget 里找勾选框——绕开页面上
        其他 QCheckBox（Switch 等）与已清空行的幽灵控件（t_g7 同款教训）。"""
        lst = root.findChild(QListWidget, list_name)
        if lst is None:
            return None
        for i in range(lst.count()):
            w = lst.itemWidget(lst.item(i))
            if w is not None:
                cb = w.findChild(QCheckBox)
                if cb is not None:
                    return cb
        return None

    keep: list = [] # 页面引用防 GC：异步回调（_async_post 的 QTimer _apply）
                    # 触发时旧 page 若已被 Python 回收，C++ 对象已删会 RuntimeError

    def _wait(pred, timeout=6.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        # ── ① sessions 勾选删：确认后真发 ──
        wrap = panels_qt.build_panel(t, "sessions")
        sp = wrap.widget()
        keep.append(wrap)
        sp.show()
        QApplication.processEvents()
        b_rf = _btn_by_text(sp, "刷新")
        if b_rf is not None:
            b_rf.click()
        _wait(lambda: _row_check(sp, "sessList") is not None)
        n0 = _n_posts("/api/sessions/delete")
        nd0 = len(exec_dlgs)
        cb = _row_check(sp, "sessList")
        if cb is not None:
            cb.setChecked(True)
        b_del = _btn_by_text(sp, "删除选中")
        ck("g9 sessions 删除选中钮在场（勾选后启用）", b_del is not None, "btn=%s" % b_del)
        if b_del is not None:
            b_del.click()
        _wait(lambda: _n_posts("/api/sessions/delete") > n0)
        ck("g9 sessions 勾选删：确认后 POST /api/sessions/delete {items:[date,ts]}",
           _n_posts("/api/sessions/delete") > n0
           and all("date" in it and "ts" in it for it in _posts("/api/sessions/delete")[-1]["items"])
           and len(exec_dlgs) > nd0,
           "posts=%s dlgs=%d" % (_posts("/api/sessions/delete")[-1:] or [None], len(exec_dlgs)))

        # ── ② 存档：真删有确认、屏蔽无确认（函数级驱动，行内按钮接线已核） ──
        nd1 = len(exec_dlgs)
        panels_custom._arc_action(t, sp, "CK", 7, "delete", lambda: None)
        _wait(lambda: _n_posts("/api/archive/delete") >= 1)
        ck("g9 存档清除：确认后 POST /api/archive/delete {chat_key, ids}",
           _n_posts("/api/archive/delete") >= 1
           and _posts("/api/archive/delete")[-1] == {"chat_key": "CK", "ids": [7]}
           and len(exec_dlgs) > nd1,
           "posts=%s dlgs=%d" % (_posts("/api/archive/delete")[-1:], len(exec_dlgs)))
        nd2 = len(exec_dlgs)
        panels_custom._arc_action(t, sp, "CK", 7, "block", lambda: None)
        _wait(lambda: _n_posts("/api/archive/block") >= 1)
        ck("g9 存档屏蔽（可逆）：无确认直接发",
           _n_posts("/api/archive/block") >= 1 and len(exec_dlgs) == nd2,
           "dlgs=%d" % len(exec_dlgs))

        # ── ③ memory 三处 ──
        wrap = panels_qt.build_panel(t, "memory")
        mp = wrap.widget()
        keep.append(wrap)
        mp.show()
        QApplication.processEvents()
        b_rf = _btn_by_text(mp, "刷新")
        if b_rf is not None:
            b_rf.click()
        _wait(lambda: _btn_by_text(mp, "删除") is not None)
        # 删成员
        n0 = _n_posts("/api/memory")
        _btn_by_text(mp, "删除").click()
        _wait(lambda: _n_posts("/api/memory") > n0)
        ck("g9 memory 删成员：确认后 POST {chat_key, user_id}",
           _posts("/api/memory")[-1].get("user_id") == "U1"
           and len(exec_dlgs) > 0,
           "body=%s" % (_posts("/api/memory")[-1:],))
        # 清勾选（重建后的行重新找勾选框）
        cb = _row_check(mp, "memTable")
        if cb is not None:
            cb.setChecked(True)
        n0 = _n_posts("/api/memory")
        b_sel = _btn_by_text(mp, "清除勾选的印象")
        if b_sel is not None:
            b_sel.click()
        _wait(lambda: _n_posts("/api/memory") > n0
              and isinstance(_posts("/api/memory")[-1], dict)
              and "user_ids" in _posts("/api/memory")[-1])
        ck("g9 memory 清勾选：确认后 POST {user_ids, scope}",
           isinstance(_posts("/api/memory")[-1], dict)
           and _posts("/api/memory")[-1].get("user_ids") == ["U1"],
           "body=%s" % (_posts("/api/memory")[-1:],))
        # 清全部
        n0 = _n_posts("/api/memory")
        b_all = _btn_by_text(mp, "清除全部")
        if b_all is not None:
            b_all.click()
        _wait(lambda: _n_posts("/api/memory") > n0)
        ck("g9 memory 清全部：确认后 POST {action:clear_all}",
           _posts("/api/memory")[-1] == {"action": "clear_all"},
           "body=%s" % (_posts("/api/memory")[-1:],))

        # ── ④ persona：恢复上一（确认）+ 移分区（prompt 弹窗） ──
        wrap = panels_qt.build_panel(t, "persona")
        pp = wrap.widget()
        keep.append(wrap)
        pp.show()
        QApplication.processEvents()
        n0 = _n_posts("/api/config")
        b_rs = pp.findChild(Btn, "pRestorePrev")
        if b_rs is None:
            b_rs = _btn_by_text(pp, "恢复上个人设")
        ck("g9 恢复上个人设钮在场", b_rs is not None, "btn=%s" % b_rs)
        if b_rs is not None:
            b_rs.click()
        _wait(lambda: _n_posts("/api/config") > n0)
        ck("g9 persona 恢复上一：确认后 POST /api/config",
           _n_posts("/api/config") > n0 and len(exec_dlgs) > 0,
           "posts=%d dlgs=%d" % (_n_posts("/api/config"), len(exec_dlgs)))
        # 移分区：行内「移」→ prompt 弹窗（_card_dialog 无 btn_ok，不会自动确认）
        b_mv = _btn_by_text(pp, "移")
        ck("g9 persona 行内「移」钮在场", b_mv is not None, "btn=%s" % b_mv)
        nd3 = len(exec_dlgs)
        if b_mv is not None:
            b_mv.click()
        _wait(lambda: len(exec_dlgs) > nd3)
        dlg_mv = exec_dlgs[-1]
        ed_mv = dlg_mv.findChild(QLineEdit)
        ok_mv = _btn_by_text(dlg_mv, "移过去")
        if ed_mv is not None and ok_mv is not None:
            ed_mv.setText("新分区")
            ok_mv.click()
        _wait(lambda: _n_posts("/api/personas/custom") >= 1)
        mvb = _posts("/api/personas/custom")[-1] if _posts("/api/personas/custom") else {}
        ck("g9 persona 移分区：输入分区名后 POST {key,name,text,cat}",
           mvb.get("cat") == "新分区" and mvb.get("key") == "custom:c1"
           and dlg_mv.result() == 1,
           "body=%s" % (mvb,))

        # ── ⑤ emoji 删除 ──
        wrap = panels_qt.build_panel(t, "wechat")
        wp = wrap.widget()
        keep.append(wrap)
        wp.show()
        QApplication.processEvents()
        xbtn = [b for b in wp.findChildren(QPushButton) if b.text() == "×"]
        _wait(lambda: bool([b for b in wp.findChildren(QPushButton) if b.text() == "×"]))
        xbtn = [b for b in wp.findChildren(QPushButton) if b.text() == "×"]
        ck("g9 emoji 网格 × 钮在场（加载后）", bool(xbtn), "n=%d" % len(xbtn))
        n0 = _n_posts("/api/emojis/delete")
        if xbtn:
            xbtn[0].click()
        _wait(lambda: _n_posts("/api/emojis/delete") > n0)
        ck("g9 emoji 删除：确认后 POST /api/emojis/delete {name}",
           _posts("/api/emojis/delete")[-1].get("name") == "e1.png",
           "body=%s" % (_posts("/api/emojis/delete")[-1:],))

        # ── ⑥ updbar 重置更新状态 ──
        bar = updbar_mod.UpdateBar(t)
        keep.append(bar)
        bar.show()
        QApplication.processEvents()
        n0 = _n_posts("/api/update_reset")
        bar.btn_reset.click()
        _wait(lambda: "已重置（原记录 9.9.9 ⇒ 现在 清空）" in bar.detail.text())
        ck("g9 updbar 重置更新状态：确认后 POST /api/update_reset + 回显原记录",
           _n_posts("/api/update_reset") > n0
           and "已重置（原记录 9.9.9 ⇒ 现在 清空）" in bar.detail.text(),
           "detail=%r posts=%d" % (bar.detail.text(), _n_posts("/api/update_reset")))
    finally:
        QDialog.exec = _orig_exec
        agent_bridge.current_url = _orig_url
        config_io.read_path = _orig_rp
        srv.shutdown()


def t_g10() -> None:
    """批4第十组（批4 收官）：roleHint 行为档推荐 + briefs 简报卡。

    手法同 t_g9：假后端 + patch current_url/read_path/write_patch/QDialog.exec。
    断言：roleHint 评估请求（text 截 2400）/推荐弹窗两下拉默认值/应用写两键
    （write_patch）+ 参与度下拉同步；briefs 进页自动加载（群下拉+列表 head）、
    添加 POST {action:add,...}、删除 POST {action:del}。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, # noqa: PLC0415
                                   QPlainTextEdit, QLineEdit, QLabel)

    import agent_bridge # noqa: PLC0415
    import config_io # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    brief_state = {"n": 0} # 添加后列表条数变化

    class _H(BaseHTTPRequestHandler):
        def _send(self, obj): # noqa: N802
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self): # noqa: N802
            calls.append(self.path)
            if "/api/briefs" in self.path:
                n = brief_state["n"]
                rows = [{"id": "b%d" % (i + 1), "text": "预设 %d" % (i + 1),
                         "always": i % 2 == 0, "until_text": "永久"} for i in range(n)]
                self._send({"active": rows, "expired": []})
            elif "/api/wechat-groups" in self.path:
                self._send({"groups": [{"name": "测试群", "wxid": "g1"}]})
            elif "/api/persona/behavior" in self.path: # 不会 GET，占位
                self._send({"ok": True})
            elif "/api/personas" in self.path:
                self._send({"personas": [], "custom": [], "rows": []})
            elif "/api/status" in self.path:
                self._send({"ok": True, "paused": False, "wechat_connected": True,
                            "listen": {"groups": 1, "privates": 0},
                            "model": "deepseek-chat", "uptime_s": 5, "groups": []})
            elif "/api/prices" in self.path:
                self._send({"deepseek-chat": {"in": 2.0, "out": 8.0, "cached": 1.0}})
            elif "/api/config" in self.path:
                self._send({"persona": {"bot_name": "x", "role_text": "y"}, "api": {}})
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/persona/behavior-recommend" in self.path:
                self._send({"participation": "high", "sticker": 2, "via": "llm",
                            "reason": "角色很外向", "marks": {"active": 3, "passive": 1}})
            elif "/api/briefs" in self.path:
                if body.get("action") == "add":
                    brief_state["n"] += 1
                elif body.get("action") == "del":
                    brief_state["n"] = max(0, brief_state["n"] - 1)
                self._send({"ok": True})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g10").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    _orig_exec = QDialog.exec
    QDialog.exec = lambda self, *a, **k: (exec_dlgs.append(self), 0)[1]

    patches: list = []
    _orig_wpatch = config_io.write_patch

    def _fake_wpatch(patch):
        patches.append(patch)
        return True, "已保存并生效（测试拦截）"

    config_io.write_patch = _fake_wpatch

    t = THEMES["whale"]

    def _btn_by_text(root, txt):
        hits = [b for b in root.findChildren(Btn) if b.text() == txt]
        return hits[0] if hits else None

    def _posts(prefix):
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _wait(pred, timeout=6.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        # ── ① roleHint：评估 → 推荐弹窗 → 应用写两键 + 参与度下拉同步 ──
        wrap = panels_qt.build_panel(t, "persona")
        pp = wrap.widget()
        keep = [wrap]
        pp.show()
        QApplication.processEvents()
        ta = pp.findChild(QPlainTextEdit, "personaRoleText")
        ck("g10 personaRoleText 在场", ta is not None, "ta=%s" % ta)
        b_hint = pp.findChild(Btn, "roleHintBtn")
        ck("g10 roleHintBtn 在场", b_hint is not None, "btn=%s" % b_hint)
        if ta is not None:
            ta.setPlainText("你是老王，群里的热心大哥。") # 请求体断言用
        nd0 = len(exec_dlgs)
        if b_hint is not None:
            b_hint.click()
        _wait(lambda: len(exec_dlgs) > nd0)
        dlg1 = exec_dlgs[-1]
        combos = dlg1.findChildren(QComboBox)
        c_part = next((c for c in combos if c.objectName() == "roleHintPart"), None)
        c_stk = next((c for c in combos if c.objectName() == "roleHintSticker"), None)
        labels = [l.text() for l in dlg1.findChildren(QLabel)]
        ck("g10 推荐弹窗：请求体 text + 两下拉默认值=推荐（high/2）+ 推荐文案回显",
           any("/api/persona/behavior-recommend" in c and "老王" in c for c in calls)
           and c_part is not None and c_stk is not None
           and str(c_part.currentData()) == "high" and str(c_stk.currentData()) == "2"
           and any("推荐：参与度 活跃 · 表情包 2 级 （模型评估：角色很外向） [活跃m×3 安静m×1]" in x
                   for x in labels),
           "part=%s stk=%s labels=%s" % (
               c_part.currentData() if c_part else None,
               c_stk.currentData() if c_stk else None,
               [x for x in labels if "推荐" in x]))
        # 改推荐值再应用
        if c_part is not None:
            i = c_part.findData("low")
            if i >= 0:
                c_part.setCurrentIndex(i)
        n_patch = len(patches)
        b_apply = _btn_by_text(dlg1, "应用")
        if b_apply is not None:
            b_apply.click()
        _wait(lambda: len(patches) > n_patch)
        ck("g10 应用 → write_patch {persona.participation, store.sticker_level} + 参与度下拉同步",
           patches and patches[-1] == {"persona.participation": "low", "store.sticker_level": 2}
           and dlg1.result() == 1,
           "patch=%s result=%s" % (patches[-1] if patches else None, dlg1.result()))

        # ── ② briefs：进页自动加载 → 添加 → 删除 ──
        wrap = panels_qt.build_panel(t, "wechat")
        wp = wrap.widget()
        keep.append(wrap)
        wp.show()
        QApplication.processEvents()
        bf_chat = wp.findChild(QComboBox, "bfChat")
        b_add = wp.findChild(Btn, "bfAdd")
        ck("g10 简报卡在场（群下拉自动填充 + 添加钮）",
           bf_chat is not None and bf_chat.count() >= 1
           and str(bf_chat.itemData(0) or "") == "group:g1" and b_add is not None,
           "chat=%s n=%s add=%s" % (bf_chat is not None,
                                    bf_chat.count() if bf_chat else -1, b_add is not None))
        bf_text = wp.findChild(QLineEdit, "bfText")
        bf_until = wp.findChild(QLineEdit, "bfUntil")
        n0 = _n_posts_g10 = len([c for c in calls if "/api/briefs" in c and "#" in c])
        if bf_text is not None:
            bf_text.setText("周六社庆")
        if bf_until is not None:
            bf_until.setText("2026-10-01")
        if b_add is not None:
            b_add.click()
        _wait(lambda: len([c for c in calls if "/api/briefs" in c and "#" in c]) > n0
              and any('"action": "add"' in c for c in calls))
        addb = [_json.loads(c.split("#", 1)[1]) for c in calls
                if "/api/briefs" in c and "#" in c and '"action": "add"' in c]
        ck("g10 添加：POST {action:add, chat:group:g1, text, until, always}",
           addb and addb[-1] == {"action": "add", "chat": "group:g1", "text": "周六社庆",
                                 "until": "2026-10-01", "always": False},
           "body=%s" % (addb[-1:] or [None],))
        # 添加后列表重载（brief_state.n=1 → head「1 条在用」）+ 行内删除
        #   （_bf_load 重建行走 deleteLater ⇒ 先让事件循环跑几轮再找，避开幽灵行按钮）
        for _ in range(8):
            QApplication.processEvents()
            _time.sleep(0.05)
        b_del = wp.findChild(Btn, "bfDel")
        ck("g10 添加后列表刷新（在用条目 + 删除钮在场）",
           b_del is not None and brief_state["n"] == 1,
           "del=%s n=%d" % (b_del is not None, brief_state["n"]))
        nd1 = len(exec_dlgs)
        if b_del is not None:
            b_del.click()
        _wait(lambda: any('"action": "del"' in c for c in calls))
        delb = [_json.loads(c.split("#", 1)[1]) for c in calls
                if "/api/briefs" in c and "#" in c and '"action": "del"' in c]
        ck("g10 删除：POST {action:del, chat, id}",
           delb and delb[-1].get("action") == "del" and delb[-1].get("chat") == "group:g1",
           "body=%s" % (delb[-1:] or [None],))
    finally:
        QDialog.exec = _orig_exec
        agent_bridge.current_url = _orig_url
        config_io.write_patch = _orig_wpatch
        srv.shutdown()


def main() -> int:
    for fn in (t_syntax, t_nav, t_themes, t_runtime_render, t_fonts_rgba, t_usability, t_panels,
               t_visual, t_badges, t_status_chain, t_bot_controls, t_window_chrome, t_dpi_motion,
               t_wheel_nod, t_updbar, t_pop_look, t_pause_win, t_no_touch, t_bootstrap32, t_ocr9,
               t_c10, t_c13, t_hotfix1, t_hotfix2, t_catmgr, t_medialocal, t_commfb,
               t_veradv, t_g5, t_g6, t_g7, t_g8, t_g9, t_g10):
        try:
            fn()
        except Exception as e: # noqa: BLE001
            ck(f"{fn.__name__} 执行未抛异常", False, f"{type(e).__name__}: {e}"[:110])

    bad = [r for r in ROWS if not r[1]]
    for name, ok, extra in ROWS:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(ROWS) - len(bad)} 通过 / {len(bad)} 失败")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
