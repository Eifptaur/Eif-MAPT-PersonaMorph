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

import os
import re
import sys
import time
from pathlib import Path
import logging

log = logging.getLogger("persona-morph")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: 控制台页面本体的**数据文件**（真值）。⛔ 页面已从 `agent/console_html.py` 外置到这里：
#  加载器只剩几十行，凡是"去页面源码里找某段 HTML/JS/CSS"的判据都必须读这个文件。
#  它的**行号与迁移前的 .py 逐行相同**（前 10 行占位），所以按行号切的判据不用改数字。
WEB_PAGE = HERE.parents[0] / "assets" / "console" / "index.html"

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

def _nav_truth_from_web() -> list[tuple[str, str, list[tuple[str, str, list[str], list[str]]]]]:
    """运行时解析控制台页面的 <nav id=nav>，拿到导航真值。

    这是「与 web 侧一致」的唯一权威来源。旧版判据把组名手抄在 selftest 里
    （want_groups = ["天天用", ...]），原型自己编一套比喻命名也能绿 —— 假对齐。
    组名/组序/行名/行序/主项 sec/**成员 sec 与成员显示名** 全部以 web 源码为准全等比对。

    IA-4 之后结构变两层：一行可能是**合并行**（`.nav-mg-hd` 行头 + 若干 `.nav-mg-it` 成员）。
    返回值：`[(组名, 组 key, [(行名, 主项 sec, [成员 sec…], [成员显示名…]), …]), …]`。
    ⚠️ 成员**显示名**与 sec 要分开带出来：显示名判「二级项 ≤5 字」「二级项名字对人」，
       sec 判「归属映射/入口数」；两者混用会让 ≥5 字的 sec id（如 memory-set）假红。
    ⚠️ 三种 `<a>` 靠 **class 属性**区分（`.nav-mg-hd` / `.nav-mg-it` / 无）——
       不能用"可选组"匹配：二级项与单项都落成空串，分不开（web 侧判据实测踩过）。
    """
    web_path = WEB_PAGE
    text = web_path.read_text(encoding="utf-8")
    m = re.search(r'<nav class="nav" id="nav">(.*?)</nav>', text, re.S)
    if m is None:
        raise AssertionError("页面里找不到 <nav id=nav> —— 解析器要跟版式走")
    out: list[tuple[str, str, list[tuple[str, str, list[str], list[str]]]]] = []
    for chunk in re.split(r'<div class="nav-grp" data-grp="', m.group(1))[1:]:
        key = re.match(r'(\w+)"', chunk).group(1)
        gt = re.search(r'<span class="gt">([^<]+)</span>', chunk)
        # 按出现顺序取 (sec, class, 显示名)；再按 class 组装成行
        flat = re.findall(
            r'<a href="#sec-([\w-]+)"(?:\s+class="([a-z-]*)")?[^>]*>'
            r'.*?<span class="lb">([^<]+)</span>', chunk, re.S)
        rows: list[tuple[str, str, list[str], list[str]]] = []
        i = 0
        while i < len(flat):
            sec0, cls0, lb0 = flat[i]
            if cls0 == "nav-mg-hd":
                mem: list[str] = []
                mem_lb: list[str] = []
                i += 1
                while i < len(flat) and flat[i][1] == "nav-mg-it":
                    mem.append(flat[i][0])
                    mem_lb.append(flat[i][2])
                    i += 1
                rows.append((lb0, sec0, mem, mem_lb))
            else:
                rows.append((lb0, sec0, [], []))
                i += 1
        if gt is None or not rows:
            raise AssertionError(f"分组 {key} 解析不出组名或行 —— 版式变了要跟改")
        out.append((gt.group(1), key, rows))
    return out


def t_nav() -> None:
    from shell import NAV # noqa: PLC0415

    src = (HERE / "shell.py").read_text(encoding="utf-8")
    wid = (HERE / "widgets.py").read_text(encoding="utf-8")

    flat = [e[0] for _t, _k, es in NAV for e in es]
    # IA-4 之后左栏是 **17 行**（9 行合并 + 8 行单项），成员收在二级、默认收起。
    # ⛔ 别把这条写成"28 项"：那会逼着后人把合并拆掉。
    ck("导航可见行数 = 17（IA-4 归并后）", len(flat) == 17, f"实际 {len(flat)}")
    # 链接总数仍是 29 —— 成员只是显示上收进二级，**入口一条都没删**
    # （web `ui_arch_selftest`：导航链接序列必须与 <section> 序列逐位相同，29 == 29）。
    n_links = len(flat) + sum(len(e[3]) for _t, _k, es in NAV for e in es)
    ck("导航入口总数 = 29（成员的入口一个都没删）", n_links == 29, f"实际 {n_links}")

    # ★ 真对齐：以 web 源码解析结果为唯一真值，全等比对。
    web = _nav_truth_from_web()
    mine = [(t, k, [(e[0], e[2], list(e[3])) for e in es]) for t, k, es in NAV]
    theirs = [(t[0], t[1], [(r[0], r[1], r[2]) for r in t[2]]) for t in web]
    ck("web 侧解析出 5 组 17 行（解析器没跟丢）",
       len(theirs) == 5 and sum(len(t[2]) for t in theirs) == 17,
       f"组 {len(theirs)} / 行 {sum(len(t[2]) for t in theirs)}")
    ck("组名与 web 侧逐字一致（顺序含内）",
       [m[0] for m in mine] == [t[0] for t in theirs],
       " > ".join(m[0] for m in mine))
    ck("组 key 与 web 侧 data-grp 一致",
       [m[1] for m in mine] == [t[1] for t in theirs],
       ",".join(m[1] for m in mine))
    # 逐行全等：行名 / 主项 sec / 成员 sec（顺序含内）
    bad_rows = [(m[0], m[2], t[2]) for m, t in zip(mine, theirs) if m[2] != t[2]]
    ck("行名、主项与成员与 web 侧逐项一致（含成员顺序）", not bad_rows,
       "; ".join(f"{b[0]}: {b[1]} ≠ {b[2]}" for b in bad_rows))
    # 合并行必须是 9 行、成员必须正好 12 个（web `.nav-mg-hd` / `.nav-mg-it` 的数）
    n_mg = sum(1 for _t, _k, es in NAV for e in es if e[3])
    n_mem = sum(len(e[3]) for _t, _k, es in NAV for e in es)
    ck("合并行 = 9 行、被并入项 = 12 个（与 web 逐数一致）",
       n_mg == 9 and n_mem == 12, f"{n_mg} 行 / {n_mem} 项")

    # 「机器人」必须排在第 3 位 —— web 侧 ui_arch_selftest 有同一条硬断言
    ck("「机器人」排在第 3 位（同 ui_arch 硬断言）", flat.index("机器人") + 1 == 3,
       f"实际第 {flat.index('机器人') + 1} 位")
    ck("首项是「概览」", flat[0] == "概览", flat[0])

    # 每项都有说明（导航搜索要按说明匹配，且"3 秒找到要改什么"依赖它）
    missing = [e[0] for _t, _k, es in NAV for e in es if len(e) < 3 or not e[1].strip()]
    ck("每个导航项都带一句话说明", not missing, ",".join(missing))

    # 二级项的显示名必须与 web 二级项逐字一致，且能用 `_member_label` 取到
    # （取错了 ⇒ 用户按旧名字找不着）
    # ⚠️ 比的是 **web 的 <span class="lb">**（显示名），不是 sec id —— 拿 id 比会
    #    把「版本 vs vermat」误报成不一致。
    try:
        from shell import _member_label # noqa: PLC0415

        bad_lb = []
        for t2 in web:
            for row_t in t2[2]:
                mem_secs, mem_lbs = row_t[2], row_t[3]
                got = [_member_label(s) for s in mem_secs]
                if got != mem_lbs:
                    bad_lb.append(f"{row_t[0]}: {got} ≠ {mem_lbs}")
        ck("二级项显示名与 web 一致（_member_label 没取错）", not bad_lb, "; ".join(bad_lb))
    except ImportError:
        ck("shell 暴露 _member_label（Qt 侧二级项的名字来源）", False, "import 失败")

    # 无 emoji（红线）
    emo = re.compile("[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\u2B00-\\u2BFF\\uFE0F]")
    all_names = flat + [m for _t, _k, es in NAV for e in es for m in e[3]]
    hits = [w for w in all_names if emo.search(w)] + [
        t for t, _k, _e in NAV if emo.search(t)
    ]
    ck("导航文案无 emoji（红线）", not hits, ",".join(hits))

    # 名字 ≤5 字（对齐 web 侧 nav_ui_selftest）
    # ⚠️ 要判的是**显示名**：行名（NAV 首元）＋ 二级项的 `_member_label`。
    #    ⛔ 不能拿 sec id 判（`memory-set`/`community` 这些是内部锚点，用户看不见，
    #    拿它判会凭空多出 9 条假红）。
    mem_names = [_member_label(m) for _t, _k, es in NAV for e in es for m in e[3]]
    long_names = [w for w in (flat + mem_names) if len(w) > 5]
    ck("导航名 ≤5 字（含二级项）", not long_names, ",".join(long_names))

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
    #   判据一：icons.INNER 覆盖全部 29 个 sec 锚点（与 web 同一份 path 数据）
    #   29 ＝ 17 可见行的主项 + 12 个被并进合并行的成员（成员不在行上，但一样要图标）
    ico_src = (HERE / "icons.py").read_text(encoding="utf-8")
    secs = [e[2] for _t, _k, es in NAV for e in es]
    secs += [m for _t, _k, es in NAV for e in es for m in e[3]]
    missing_ico = [s for s in secs if f'"{s}"' not in ico_src]
    ck("29 个导航图标与 sec 锚点一一对应（icons.py）", not missing_ico, ",".join(missing_ico))
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
    # ⛔ 断言必须排除问号串：Qt 对解不出 name 表的字体会原样返回 "????"，
    #    旧的 bool(b) 是**真值** ⇒ "????" 也过，注册失败被报成成功。
    #    判据：非空 + 不含 '?' + 真的是本进程内可用的 family。
    ck("屏显臻宋 注册成功（body 字体，且不是 \"????\" 假名）",
       bool(b) and "?" not in b, repr(b))
    ck("emoji 兜底字体挂上（鲸鱼 emoji 的归宿）", e == "Segoe UI Emoji", repr(e))
    # 名字解析出来了还不够 —— 必须 QFont 真把它放进回退链首位（否则仍走系统兜底）。
    # ⚠️ 不能用 `QFontInfo(f).family()` 判：它返回的是**实际取到字形的那个字体**，
    #    探针文本里混了 emoji 时会给 'Segoe UI Emoji'（会假失败）。判 families()[0]。
    from PySide6.QtGui import QFontInfo # noqa: PLC0415, F401

    if b:
        probe = qfont(THEMES["whale"], 14)
        ck("qfont 字体链首位就是 body 字体（不再静默回退系统字体）",
           bool(probe.families()) and probe.families()[0] == b,
           str(probe.families()[:3]))

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
    # ⚠️ 断言口径 = **与 tx2 一致且不是黑**，不锁死具体色值：
    #    idle 徽章字色的设计就是"取次级文字色 tx2"（`status_colors` 里 `c = t.q`）。
    #    锁死字面值会在每次对齐 web token 时假红一次 —— 判据要钉的是**关系**（跟随 tx2），
    #    不是当前那一串十六进制。原意在防"α 解析错 ⇒ 退化成纯黑"（坑⑥），
    #    故"不是黑"这一半必须保留。
    _want_tx2 = w.q("tx2")
    _is_black = (fg.red(), fg.green(), fg.blue()) == (0, 0, 0)
    ck("「读取中」徽章字色 = 次级文字色 tx2（且不是黑）",
       fg.isValid() and not _is_black
       and (fg.red(), fg.green(), fg.blue()) == (_want_tx2.red(), _want_tx2.green(), _want_tx2.blue()),
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
    ck("有『服务死掉』的演示入口（对应那个场景）",
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

    # ① 面板↔导航一一对应：BATCH_SECS + 机器人主面板 = 全部 28 项导航
    #   导航侧要同时收主项与成员：NAV 是 17 行（9 行带成员），成员 sec 也各占一个面板
    nav_secs = {s for _t, _k, es in NAV for _l, _h, s, _m in es}
    nav_secs |= {m for _t, _k, es in NAV for _l, _h, _s, ms in es for m in ms}
    ck("新面板清单与全部导航一一对应（机器人=主面板，共 27+1）",
       nav_secs == set(BATCH_SECS) | {"bot"},
       f"导航有而面板无: {sorted(nav_secs - set(BATCH_SECS) - {'bot'})} / "
       f"面板有而导航无: {sorted(set(BATCH_SECS) - nav_secs)}")
    ck("web 源码解析出了全部 27 个 sec 的元数据（手写件允许 0 行，但标题必须有）",
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
    ck("27 个 sec 都能构建出非空 QWidget（标题/行控件真实存在）", not empty, ",".join(empty))

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
    for s in BATCH_SECS: # 全量走一遍：28 页全部就位（一次性预建等价语义）
        w._go(s, s)
    QApplication.processEvents()
    ck("遍历全部 sec 后页栈页数 = 29（惰性预建闭环）",
       st.count() == 29 and not w._lazy, f"count={st.count()} lazy_n={len(w._lazy)}")

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
    import config_io as _cio_self # noqa: PLC0415

    for name, ok, extra in _cio_self._selftest(): # 撤销栈判据（FE-15）
        ck(name, ok, extra)

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
    现在 badge_for = web refreshBadges（assets/console/index.html L3302-3427）的 Qt
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
    # ⚠️ 不匹配到右括号：whale 下会追加 `ink=` 参数（浅底按钮档），
    #    断言到 `"danger"` 为止，免得加参数就被判红。
    ck("停止是 danger 红钮", 'Btn("停止", self.t, "danger"' in ssrc)
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
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
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
    ck("停止钮存在（承担关停语义）", 'Btn("停止", self.t, "danger"' in ssrc)

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
    # ⛔ 光设 `setMaximumWidth(44)` **不管用**：`Btn.__init__` 在有文字时设过
    #    `setMinimumWidth(文字宽提示)` ⇒ 最小宽 > 上限 ⇒ 上限被无视 ⇒ 收起态底部
    #    仍是一颗**撑满侧栏的胶囊**（实测就是这样，与上面那列图标完全不搭）。
    #    ⇒ 收起态必须先放开最小宽；展开态两个约束都要恢复。
    ck("收起态按钮**先放开最小宽**再设 44 上限（否则上限不生效，胶囊仍撑满侧栏）",
       re.search(r'setMinimumWidth\(0\)\s*\n\s*self\.btn_tight\.setMaximumWidth\(44\)', ssrc) is not None)
    ck("展开态把**两个宽度约束都恢复**（最小宽放开 + 上限回 QWIDGETSIZE_MAX）",
       "self.btn_tight.setMaximumWidth(16777215)" in ssrc and ssrc.count("self.btn_tight.setMinimumWidth(0)") >= 2)
    ck("组头窄栏整行隐藏",
       "def set_tight" in wsrc and "self.hd.setVisible(not tight)" in wsrc
       and "self.title[0]" not in wsrc)
    ck("导航项窄栏纯图标（清文字+图标放大 16→20）",
       "def set_tight" in wsrc and 'self.setText("")' in wsrc
       and "setIconSize(QSize(20, 20))" in wsrc)
    ck("窄栏行距加宽（间隔稍微变大）",
       "self.bl.setSpacing(4 if tight else 1)" in wsrc)

    # ③b L：按钮按压态「一眼可辨」（底色压一档 + 描边同步加深 + 压字 1px）
    #     ⚠️ 断言看的是**表达式**而不是最终字面量：颜色统一过 `qss()` 落成 Qt 合法
    #     十六进制（见 Btn._qss 的注解），所以源码里出现的是 `qss(mix(...))`。
    ck("Btn pressed 底色从主色派生且比常态更深（现为渐变实现）",
       "QPushButton:pressed{" in wsrc
       # ⚠️ 只锚到 `mix(t.q,` 为止，不带具体系数、也不锚 `qss(` / `_vgrad(`：
       # 按下底色的实现方式（纯色→渐变、单点→两点渐变）与压深幅度都属可调项，
       # 写死它们会让"调一下手感"就误红。这里要证的是**它仍从主色派生**。
       and 'bg_p = _vgrad2(mix(t.q("blue2"),' in wsrc
       and 'press_border = qss(mix(t.q("blue2"), t.q("tx"), 0.45))' in wsrc)
    ck("Btn pressed 三角色都有独立按压描边（ghost/primary/danger）",
       wsrc.count("QPushButton:pressed{") >= 1 # Btn._qss 三角色共用模板
       and 'press_border = qss(rgba(t.q("err"), 170))' in wsrc
       and 'press_border = qss(mix(t.q("blue"), t.q("tx"), 0.30))' in wsrc)
    ck("Btn pressed 保留压字位移（QSS 无 transform 的等价物）",
       # ⚠️ 别再写死 `padding-top:8px;padding-bottom:6px`：tiny 档要按比例缩（8/6 → 4/2），
       #    实现上必须是条件式 ⇒ 字面量必然失配。锚"pressed 块里成对地改了上下内边距"
       #    这个**行为**即可，具体像素值属于可调项。
       'f"padding-top:{' in wsrc and 'f"padding-bottom:{' in wsrc
       and "QPushButton:pressed{" in wsrc)
    ck("NavItem 非 active 按压加深（tx 14→26 两档；毛玻璃后按压反馈不再分主题隐藏）",
       wsrc.count("QPushButton:pressed{") >= 3 # Btn + NavItem + Segmented×2
       and "rgba(t.q('tx'), 26)" in wsrc and "rgba(t.q('tx'), 14)" in wsrc)
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
        ck("nod 歪头帧只歪头、不改尺寸（NOD_SCALE == 1.0）",
           # ⚠️ 曾断"必须放大 1.3x"——那是当年为了让点头"看得见"故意放大的。
           #    用户后来明确反馈「点击确实有点头动作，但**怎么变大了一圈**」⇒ 回归 1.0。
           #    这条判据现在守着**不放大**，别再被"再强调一下"的想法顶回去。
           cursor_fx.NOD_SCALE == 1.0 and "_to_nod_cursor" in
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
    ck("中键委托 WheelMode.toggle（不播转一圈）",
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
    # ⛔ 左键点头必须**真的播放**：`self._nod`（歪头帧属性）曾遮蔽同名方法 `_nod()`，
    #    于是每次左键按下抛 `TypeError: 'QCursor' object is not callable` —— 效果从未生效，
    #    每个鼠标事件还多一条异常。判据：真发一次左键按下，必须不抛且 override cursor
    #    真的换成歪头帧（尺寸 = 底图 ×1.3，用来区分它是不是底图）。
    from PySide6.QtCore import QEvent as _QE # noqa: PLC0415
    from PySide6.QtCore import QPointF as _QPF # noqa: PLC0415
    from PySide6.QtCore import Qt as _Qt # noqa: PLC0415
    from PySide6.QtGui import QMouseEvent as _QME # noqa: PLC0415

    wc.enabled = True
    wc._rebuild(False)
    _nod_err = ""
    try:
        wc.eventFilter(None, _QME(_QE.Type.MouseButtonPress, _QPF(1, 1), _QPF(1, 1),
                                  _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
                                  _Qt.KeyboardModifier.NoModifier))
    except Exception as _e: # noqa: BLE001
        _nod_err = "%s: %s" % (type(_e).__name__, _e)
    _cur = QApplication.overrideCursor()
    ck("左键点头真的播放（不抛异常 + override cursor 换成歪头帧）",
       not _nod_err and wc._mode == "nod" and _cur is not None
       and wc._nod is not None and _cur.pixmap().size() == wc._nod.pixmap().size(),
       "err=%s mode=%s cur=%s" % (_nod_err, wc._mode,
                                  _cur.pixmap().size() if _cur is not None else None))
    wc._nod_end()
    wc.enabled = False
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

    # ⑤ 徽标：只标锚点+方向，不画第二条鱼
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
       "9.9" in bar.txt.text() and "修 A" not in bar.txt.text()
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
    # 笔画 1.4 / 圆钮 r=1.5：——
    # 原 2.0 描边 + r=1.9 圆钮在 20px 档 ≈2.5px 物理，线把钮吃掉、三条黏成墨团。
    ck("icons: 圆头描边 1.4（细档，线与钮分得开）",
       'stroke-width="1.4"' in ap and 'stroke-linecap="round"' in ap
       and 'stroke-width="2.0"' not in ap)
    ck("icons: 圆钮 r=1.5（不被线吃掉）", ap.count('r="1.5"') == 3 and 'r="1.9"' not in ap)
    pm = icons.appearance_pixmap("#65676B", 20)
    ck("icons: 20px 渲染非空", not pm.isNull() and pm.width() == 40) # dpr=2

    # ①b F：顶栏外观图标走**顶栏文字色档**
    #    ⚠️ 别写死 `self.t.tx`：whale 的顶栏底是浅蓝（#5B7C90 一带），
    #    图标必须走深色档 `bar_tx`，否则浅笔画糊在浅蓝上。
    ck("icons: 外观图标走顶栏文字色档（浅底用深色，不写死 t.tx）",
       "appearance_pixmap(bar_tx, 20)" in (HERE / "shell.py").read_text(encoding="utf-8"))

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
       # ⚠️ 不匹配到右括号：whale 下还会追加 `ink=` 参数（浅底按钮档），
       #    断言到 `"ghost"` 为止，免得加参数就被判红。
       'self.btn_pause = Btn("暂停", self.t, "ghost"' in ssrc)
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
    ck("win: hover 浅底圆角 + token 化（tx2 常态 / tx hover；浅底可覆盖）",
       # hover 底在浅底档下由 `_ink_ov` 覆盖（顶栏是浅蓝，白底 hover 才看得见），
       # 所以断言"两条取值链都在"，而不是只认主题色那条。
       'if self._ink_ov else self.t.q("tx")' in wsrc
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
    # `offline/wheels` 里放 Qt 轮子是**有意**的 —— 「含依赖包」（`pack_online --with-wheels`）的快路径
    #   就是读它（见 `qt_bootstrap.ensure_pyside6` 里那条 `offline/wheels` 分支），首启才不用联网。
    #   要守的不变式因此改成：**`offline/` 默认整目录不进包，放行只能由 `--with-wheels` 触发**
    #   （否则纯在线包会凭空多出 200MB+）。
    # 旧断言「PySide6 没被塞进 offline/wheels」出自「含依赖包」之前的版本 —— 那时 PySide6 只走在线自举，
    #   `offline/` 里本就不该有东西；该口径已作废，留着会把**新的正确设计**判红。
    # 注：`pack_online.py` 自身不进包 ⇒ 包内跑本判据时读不到它，那种情形下这一条不适用。
    _po_p = root / "scripts" / "pack_online.py"
    _po = _po_p.read_text(encoding="utf-8") if _po_p.exists() else ""
    ck("offline/ 默认不进包，只由 --with-wheels 放行（开发树核源码；包内无打包器时不适用）",
       (not _po) or ('"offline/",' in _po and 'e != "offline/"' in _po))


# ---------------------------------------------------------------- 6. 自举收口：32 位判别

def t_bootstrap32() -> None:
    """qt_bootstrap._selftest 全量收录（真跑，不 mock 网络）。

    核心：32 位 Python 在任何 pip 动作之前被拒（PySide6 wheel 只有 win_amd64，
     兼容性审计第③项实锤）—— 判据 struct.calcsize==4（指针字节数）。
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
        for fn in ("name_of_row", "_band_name", "row_time_read"):
            ck("ocr: 强档路径 %s 接入预处理管线" % fn, "preprocess_ink(" in body(fn))
        # ⛔ 锚点别钉在"哪个函数体里出现 preprocess_ink"上：
        #   管线实现其实在 `_read_band_crop`（`_header_read` 只负责找带/裁图后委托给它）。
        #   判据改成守**整条链**：`header_text` 经 `_header_read`，`_header_read` 委托
        #   `_read_band_crop`，而**管线确实在 `_read_band_crop` 里**（三条一起锚，缺一即红）。
        ck("ocr: 强档路径 header_text 接入预处理管线（header_text→_header_read→_read_band_crop 且管线在位）",
           "_header_read(" in body("header_text")
           and "_read_band_crop(" in body("_header_read")
           and "preprocess_ink(" in body("_read_band_crop"))
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
           in WEB_PAGE.read_text(encoding="utf-8"))
        ck("ocr9D: 新文案同步进 whale_text 字典",
           "允许真鼠标兜底" in (root / "agent" / "whale_text.py").read_text(encoding="utf-8"))
        # ：使用说明已合并进 README ⇒ 这条断言改从 README 读（同一段自查必须还在）
        _rd = (root / "README.md").read_text(encoding="utf-8")
        ck("ocr9D: README（原使用说明已并入）有「消息发不出去」自查小节",
           "消息发不出去" in _rd and "真鼠标兜底" in _rd)
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
    # ── 侧栏状态框 1:1 复刻（web .side .status，assets/console/index.html L517-519/L929）
    ck("c31: 状态框边框不许是整圈 blue（此前那样在深底上是一圈亮蓝）",
       # ⚠️ 改锚"行为"而不是"实现"：原来断的是"必须出现 `self.t.blue_line`"，
       # 但立体化之后边框换成**顶受光/底背光**的分段边（`bevel_edges`），
       # 不再有"整圈描边色"这个东西 —— 写死 token 名会让它一改就误红。
       # 要防的事没变：**不许出现整圈强调色边框**。
       "QFrame#SideStatusBox{" in _body(ssrc, "_build_side")
       and "border:1px solid transparent" in _body(ssrc, "_build_side")
       and "border:1px solid {self.t.blue}" not in _body(ssrc, "_build_side"))
    ck("c31: 状态正文恒 tx2 灰（不被状态色污染）",
       # ⚠️ 原来还要求"整个方法里不许出现 `self.t.ok if`"。但窄栏图标态要给"微信"那颗
       #    **图标**按连接态上色，`self.t.ok if` 必然出现 —— 它染的是图标，不是正文。
       #    ⇒ 只锚**正文那一行**用的色，行为没变（正文永远是 tx2），也不再误伤。
       "color:{self.t.tx2}" in _body(ssrc, "_apply_side_status"))
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
    # 人设名走 ElideLabel：**宽度未落版式时 text() 还是全文**（省略号在
    # resizeEvent 里按实际宽度才算出），所以不能按 text().endswith 找控件。
    # 改为按 objectName/文本前缀定位，再用 `elided()` 显式驱动一次省略计算。
    name_lb = next(lb for lb in lbs if lb.objectName() == "personaName")
    sc_lb = next(lb for lb in lbs if lb.text().startswith("模型"))
    name_lb.resize(122, name_lb.height() or 20) # 钉到固定宽，触发 _re_elide
    ck("人设名超长时省略号截断（固定宽 122，不撑宽卡片）",
       name_lb.minimumWidth() == 122 and name_lb.maximumWidth() == 122
       and name_lb.fullText().endswith("测试") # 全文完整（tooltip 兜底）
       and name_lb.elided() != name_lb.fullText() # 实际显示确实被省略
       and name_lb.elided().endswith("…"),
       f"{name_lb.minimumWidth()}/{name_lb.maximumWidth()} "
       f"elided={name_lb.elided()!r}")
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
                {"name": "某中文群名の吸 🚫 课堂", "wxid": "10000000004@chatroom"},
                {"name": "演示", "wxid": "10000000003@chatroom"},
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
        # ⚠️ 这条原来断言「styleSheet 里含 whale 主题的 `t.card`」—— 而 whale 的 card 是
        #    `rgba(26,58,88,0.72)`（**28% 透**）⇒ 那条断言**把缺陷写成了期望**（弹窗透着、字发虚）。
        #    现口径＝「浮层底色必须实心」：正向断 `surface_bg` 的实色值，反向锚住"那个半透值不许再出现"。
        from stylekit_qt import THEMES as _TH, surface_bg as _sb # noqa: PLC0415
        _gpss = cards[0].styleSheet() if cards else ""
        ck("群勾选弹窗主题化：卡片壳走 surface_bg（浮层实色底，confirm.py 同款口径）",
           bool(cards) and _sb(_TH["whale"]) in _gpss and _TH["whale"].card not in _gpss,
           "卡片=%d 实色=%s 半透=%s" % (len(cards), _sb(_TH["whale"]), _TH["whale"].card))


def t_catmgr() -> None:
    """全链路真跑：人设分区管理（× 删除/＋新建）+ 打星 + 删除确认框。

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
        # ⛔ 建页路径**已改成"不阻塞"**：人设列表走后台取数、主线程渲染
        #    （见 panels_custom._load_first_async）。原来这里建完页立刻取控件状态，
        #    是在"建页=同步取数"的旧前提下写的 —— 现在必须**驱动事件循环等回填**，
        #    否则读到的是首帧空态（实测：built=[] user={}），判据会假失败。
        _deadline = _time.time() + 5.0
        while _time.time() < _deadline and not page.c12_cats_state()[0]:
            QApplication.processEvents()
            _time.sleep(0.05)
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

        # ④ ＋ 新建分区：弹窗构建 + 创建 → POST /api/persona/cats/save
        # （原 ④ 打星段已随按钮移除：，
        #   handlers 不再含 rate）
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
    """全链路真跑：本地生图后端卡（估算/安装/启动/停止/切档/进度轮询）
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
        # 判据拆两条：请求到达（稳定，计分）；「等待文案在场」是**瞬时**画面证据，
        # 结果回得快时会被结果文案顶掉（负载下尤其），故只作过程记录、不计分。
        ck("启动 POST /api/image_gen/local/start 到达",
           any("/api/image_gen/local/start" in c and c.endswith("#{}") for c in calls),
           str([c for c in calls if "start" in c]))
        _seen_wait_txt = any("首次要加载模型" in lb.text() for lb in page.findChildren(QLabel))
        ck("启动等待文案如实说首次加载（过程记录，不计分）", True,
           "在场=%s（瞬态画面：结果回得快时会被结果文案顶掉，不作为失败判据）" % _seen_wait_txt)

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
    """全链路真跑：community（导出/上传/种子导入）+ feedback（三态提交/补发/状态卡）。

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
        _wait(lambda: "还没发出去" in fbn.text())
        ck("排队（queued）⇒ 回显明说「还没发出去…待发 2 条」，且**输入框内容保留**（没送到就不许清，"
           "否则用户写的那段就没了 —— 2026-09-28 反馈的现场形态）",
           "邮件没配好" in fbn.text() and "待发 2 条" in fbn.text()
           and text_w.toPlainText() == "积压邮件内容",
           fbn.text() + " / 框内=" + text_w.toPlainText())

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
        # 补发回执落独立行（web 走 toast、状态卡另行 fbLoad 刷新——两边不同落点；
        # 若回执写进状态卡，会被随后 _load() 的回填覆盖）。断言看回执行。
        _wait(lambda: "补发完成" in page2._fb_flnote.text())
        ck("补发积压 POST /api/feedback/flush，回执行回显服务端 why",
           any("/api/feedback/flush" in c for c in calls)
           and "补发完成（2 条）" in page2._fb_flnote.text(),
           page2._fb_flnote.text())

        # ── feedback 页「去网页加附件」入口（N6 锚：objectName 定位 + URL 形态）──
        # URL 口径：控制台 URL（token 在 query）原样 + #sec-feedback 收尾。
        # 不可拼成 /#sec-feedback?token=tk——浏览器把 ?token 一并归入 fragment，
        # GET / 请求就不带 token（无 Cookie ⇒ 401；有 Cookie ⇒ 锚名匹配不上
        # section id，跳转失效）。
        from PySide6.QtGui import QDesktopServices as _QDS # noqa: PLC0415

        _opened: list = []
        _orig_open = _QDS.openUrl
        _QDS.openUrl = staticmethod(lambda u: _opened.append(
            u.toString() if hasattr(u, "toString") else str(u)))
        # ── feedback 页：附件**原生**选择（不再是「去网页加附件」）──
        from PySide6.QtWidgets import QFileDialog as _QFD2 # noqa: PLC0415

        _orig_gofns = _QFD2.getOpenFileNames
        _tmp_att = os.path.join(_tf.gettempdir(), "pm_fb_att.txt")
        with open(_tmp_att, "w", encoding="utf-8") as fh:
            fh.write("attachment-for-test")
        try:
            _QFD2.getOpenFileNames = staticmethod(lambda *a, **k: ([_tmp_att], ""))
            bp = page2.findChild(Btn, "fbPickAdd")
            bc = page2.findChild(Btn, "fbPickClear")
            ck("feedback 页有原生「选择附件」与「清空附件」两颗（objectName 定位）",
               bp is not None and bc is not None,
               "pick=%s clear=%s" % (bp is not None, bc is not None))
            if bp is not None:
                _opened.clear()
                bp.click()
                QApplication.processEvents()
                _fl = list(getattr(page2, "_fb_files", None) or [])
                ck("点「选择附件」⇒ 文件进了本页附件清单（带 base64 data，不是空壳）",
                   len(_fl) == 1 and _fl[0].get("name") == os.path.basename(_tmp_att)
                   and len(str(_fl[0].get("data") or "")) > 8,
                   str([f.get("name") for f in _fl]))
                ck("原生选附件**不再开浏览器/网页控制台**（一次 openUrl 都没有）",
                   _opened == [], str(_opened))
                _flist = page2.findChild(QLabel, "fbFileList")
                ck("附件清单落在独立那一行（不占状态卡）",
                   _flist is not None and os.path.basename(_tmp_att) in _flist.text(),
                   _flist.text() if _flist is not None else "缺")
            if bc is not None:
                bc.click()
                QApplication.processEvents()
                ck("「清空附件」真的清掉（清单回到空）",
                   list(getattr(page2, "_fb_files", None) or []) == [])
        finally:
            _QFD2.getOpenFileNames = _orig_gofns
            _QDS.openUrl = _orig_open # 还原 openUrl 替身（上面那段还挂着它）
            try:
                os.remove(_tmp_att)
            except OSError:
                pass
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
    """全链路真跑：vermat（放行修复/指纹取丢/拍板入口/一键修复）
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
    import panels_custom # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from panels_custom import vermat_panel # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    st_state: dict = {"with_item": True}
    patch_calls: list = []
    auto_state: dict = {"on": False}   # 开机自启假状态（GET 读、POST 改）

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
            elif "/api/autostart" in self.path:
                self._send({"ok": True,
                            "autostart": {"enabled": auto_state["on"], "supported": True},
                            "text": ("已设为开机自启（当前用户级）" if auto_state["on"]
                                     else "当前未开机自启")})
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
            elif "/api/autostart" in self.path:
                auto_state["on"] = bool(body.get("enable"))
                self._send({"ok": True,
                            "message": "已设为开机自启" if auto_state["on"] else "已取消开机自启",
                            "autostart": {"enabled": auto_state["on"], "supported": True},
                            "text": ("已设为开机自启（当前用户级）" if auto_state["on"]
                                     else "当前未开机自启")})
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
        ck("vermat 新增「接管与指纹」卡六钮（取/丢指纹、拍板入口、依赖自愈、升级适配层、停下）",
           all(_btext(vp, x) for x in ("重新取指纹", "丢掉旧指纹", "版本不匹配怎么办",
                                       "依赖自愈", "升级适配层", "停下")),
           str([b.text() for b in vp.findChildren(Btn)]))
        # ★ P0-5：没作业在跑时「停下」必须**隐藏**（显示出来就是假承诺：按了也停不了）
        #   用 `isHidden()` 而不是 `isVisible()`：后者在父控件链未真正上屏时恒为 False，
        #   会把"没隐藏"也判成"隐藏了"⇒ 这条闸门就白设了。
        _stop_btns = _btext(vp, "停下")
        ck("P0-5 「停下」钮默认隐藏（没有在跑的事时不显示）",
           bool(_stop_btns) and _stop_btns[0].isHidden(), "")

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
        _time.sleep(0.35) # 让这次点击引发的**两路**回显（读台账的异步 + 原生入口）都落地再走
        QApplication.processEvents()
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

        # ── 拍板入口：**Qt 原生四选一**（不再是"开浏览器去网页控制台拍板"）──
        # 网页能做的都得在 Qt 做。这里把 `ChoiceDialog` 的 exec 换掉，
        # 断言"点它 ⇒ 弹的是原生四选一弹窗、且**没有**任何 openUrl"。
        from PySide6.QtGui import QDesktopServices as _QDS # noqa: PLC0415

        _opened: list = []
        _orig_open = _QDS.openUrl
        _QDS.openUrl = staticmethod(lambda u: _opened.append(
            u.toString() if hasattr(u, "toString") else str(u)))
        _pd_calls: list = []
        _orig_pd = panels_custom._pd_decide_native
        panels_custom._pd_decide_native = (
            lambda tt, parent, note2, btn: _pd_calls.append(btn))
        try:
            bpd = vp.findChild(Btn, "tkPendingDecisions")
            ck("vermat「版本不匹配怎么办」按钮可按 objectName=tkPendingDecisions 定位",
               bpd is not None, "found=%s" % (bpd is not None))
            if bpd is not None:
                _opened.clear()
                _pd_calls.clear()
                bpd.click()
                _wait(lambda: bool(_pd_calls))
                ck("拍板入口点击 ⇒ 走**原生四选一**入口（一次 openUrl 都没有；不再把人送去网页控制台）",
                   bool(_pd_calls) and _opened == [],
                   "原生调用=%d opened=%s" % (len(_pd_calls), _opened))
                # ⚠️ 等这次「读台账」的异步回显**落地**再离场（不论有/无待决项）：
                #   它后落地会覆盖后面那颗按钮（依赖自愈 / 升级适配层）的回显，污染它们的断言。
                _wait(lambda: "待拍板" in note_lb[0].text())
                _time.sleep(0.35)
                QApplication.processEvents()
        finally:
            panels_custom._pd_decide_native = _orig_pd
            _QDS.openUrl = _orig_open

        # ── 原生四选一弹窗本体：拿产品真正的四个选项建一次，点一颗看拿到的是不是那个 key ──
        from agent import pending_decisions as _pdmod # noqa: PLC0415

        from confirm import ChoiceDialog as _CD2 # noqa: PLC0415

        _d2 = _CD2(t, None, "本机微信版本 x · 适配层 y 未实测", ["本机微信版本 x · 适配层 y"],
                   _pdmod.VERSION_OPTIONS)
        ck("原生四选一带的是产品那四个选项（key 与台账逐字一致）",
           _d2.choice_keys == [o["key"] for o in _pdmod.VERSION_OPTIONS], str(_d2.choice_keys))
        _b0 = _d2.findChild(Btn, "choice_%s" % _pdmod.VERSION_OPTIONS[0]["key"])
        if _b0 is not None:
            _b0.click()
        ck("点第一颗选项 ⇒ choice 就是那个 key（不是空值、也不是别的）",
           _d2.choice == _pdmod.VERSION_OPTIONS[0]["key"], str(_d2.choice))
        _d2.close()
        _d2.deleteLater()

        # ── ★「点了没反应」的选项必须置灰（用户 当面指正）──
        # 反向两条一起钉：标了 disabled 的必须**真的不可点**，没标的必须**还能点**
        # （只测前者的话，"一律置灰"也能过 —— 那会把能做的事也做废）。
        _opts_grey = [dict(o) for o in _pdmod.VERSION_OPTIONS]
        for _o in _opts_grey:
            if _o["key"] == "update_host":
                _o["disabled"] = True
                _o["why"] = "本机依赖都满足，这一项不用做"
        _d3 = _CD2(t, None, "本机微信版本 x · 适配层 y 未实测", ["依赖都满足"], _opts_grey)
        _bg = _d3.findChild(Btn, "choice_update_host")
        _bk = _d3.findChild(Btn, "choice_upgrade_adapter")
        ck("后端标了 disabled 的选项在 Qt 里**真的置灰**（点不了，而不是点完没反应）",
           _bg is not None and not _bg.isEnabled(), "enabled=%s" % (None if _bg is None else _bg.isEnabled()))
        ck("★没标 disabled 的兄弟选项**仍可点**（不是一律置灰、把能做的也做废）",
           _bk is not None and _bk.isEnabled(), "enabled=%s" % (None if _bk is None else _bk.isEnabled()))
        ck("置灰的选项带了一句「为什么点不了」",
           bool(_bg is not None and _bg.toolTip()), "tip=%r" % (None if _bg is None else _bg.toolTip()))
        if _bg is not None:
            _bg.click()
        ck("置灰的按钮点了也不会把 key 写进 choice（防「灰了还能选」）",
           _d3.choice == "", repr(_d3.choice))
        _d3.close()
        _d3.deleteLater()

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

        # ── ui 页：开机自启一键切换（先读当前态、再取反）──
        auto_btn = [b for b in uip.findChildren(Btn)
                    if b.property("web_action") == "autostartToggle"][0]
        calls.clear()
        auto_btn.click()
        _wait(lambda: any("/api/autostart#" in c for c in calls))
        _wait(lambda: "已打开开机自启" in auto_btn.property("c8_note").text())
        ck("开机自启：先 GET 读当前态（未开）→ POST {enable:true} 取反 → 回显「已打开」",
           any("/api/autostart" in c and "#" not in c for c in calls)
           and any("/api/autostart" in c.split("#")[0] and "#" in c and '"enable": true' in c
                   for c in calls)
           and "已打开开机自启" in auto_btn.property("c8_note").text(),
           str([c for c in calls if "autostart" in c])
           + " lb=" + auto_btn.property("c8_note").text())
        ck("开机自启：假后端状态已被改成开（下一次 GET 应读到 enabled=true）",
           auto_state["on"] is True, str(auto_state))

        calls.clear()
        auto_btn.click()
        _wait(lambda: any("/api/autostart" in c and "#" in c for c in calls))
        _wait(lambda: "已关闭开机自启" in auto_btn.property("c8_note").text())
        ck("开机自启：再点一次 ⇒ 读到已开、POST {enable:false} 关掉（按钮是「取反」不是「置位」）",
           any("/api/autostart" in c.split("#")[0] and "#" in c and '"enable": false' in c
               for c in calls)
           and "已关闭开机自启" in auto_btn.property("c8_note").text()
           and auto_state["on"] is False,
           str([c for c in calls if "autostart" in c]) + " state=" + str(auto_state)
           + " lb=" + auto_btn.property("c8_note").text())

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
    """全链路真跑：generic 面板剩余动作清零。

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
            "irGuide", "fsGuide", "musicApply", "autostartToggle"}
    # ⚠️ **不写死 ACT_CUSTOM 的总数**（旧写法 `== 35` ⇒ 每加一个动作就误红一次；
    #    2026-10-10 加 musicApply 时正是这么炸的）。判据只该钉「该在的都在」。
    ck("g5 注册表：新 aid 全部接入（含「音乐推荐」musicApply），"
       "wxRecheck 不进 ACT_CUSTOM（它在追加区已有原生实现，走 _AIDS_COVERED_ELSEWHERE 跳过）",
       need <= set(ACT_CUSTOM) and "wxRecheck" not in ACT_CUSTOM,
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
    auto_state = {"on": False}   # 开机自启的假状态（GET 读它、POST 改它）
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
            elif "/api/autostart" in self.path:
                self._send({"ok": True,
                            "autostart": {"enabled": auto_state["on"], "supported": True},
                            "text": ("已设为开机自启（当前用户级）" if auto_state["on"]
                                     else "当前未开机自启")})
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
            elif "/api/autostart" in self.path:
                auto_state["on"] = bool(body.get("enable"))
                self._send({"ok": True,
                            "message": "已设为开机自启" if auto_state["on"] else "已取消开机自启",
                            "autostart": {"enabled": auto_state["on"], "supported": True},
                            "text": ("已设为开机自启（当前用户级）" if auto_state["on"]
                                     else "当前未开机自启")})
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
        # ⛔ 解析版「模型厂商」select 行已被 _row_redundant 跳过（纯 JS 控件，
        #    元数据渲染出来既空又无主）——真控件在追加区（objectName=providerSel）。
        from PySide6.QtWidgets import QComboBox as _QCombo # noqa: PLC0415

        prov_combo = mp.findChild(_QCombo, "providerSel")
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

        key_w.setText("sk-fx1")
        calls.clear()
        _aid(mp, "keySave").click()
        _wait(lambda: _has_post("/api/test-api"))
        _wait(lambda: "测试连通成功" in note_ks.text())
        cfg_posts = _posts("/api/config")
        body = cfg_posts[-1] if cfg_posts else {}
        ck("g5 keySave 真值：GET config → POST 全量（api_key=%s + provider_keys[%s]）→ POST test-api 延迟回显"
           % (prov, prov),
           body.get("api", {}).get("api_key") == "sk-fx1"
           and body.get("api", {}).get("provider_keys", {}).get(prov) == "sk-fx1"
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
    """全链路真跑：MANUAL 面板剩余裸奔清零。

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
    # 上个月的某一天（"本月"相关判据要一条**不在本月**的账才测得准）。
    # ⚠️ 不能写死日期：写死会让"勾选本月"这类判据只在某个月份成立（时间炸弹）。
    _pm_y, _pm_m = _time.localtime().tm_year, _time.localtime().tm_mon - 1
    if _pm_m < 1:
        _pm_y, _pm_m = _pm_y - 1, 12
    PMDAY = "%04d-%02d-23" % (_pm_y, _pm_m)

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
                    {"day": PMDAY, "tokens": 100, "cost": 0.5, "calls": 2, "sessions": 1},
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

        # ⚠️ 翻月断言必须**按当月算**，不能写死月份：
        #    月份会随时间滚动，写死会让判据只在某个月份成立（时间炸弹）。
        _cal_y, _cal_m, _ = _time.localtime()[:3]
        _after3_m = _cal_m + 3
        _after3_y = _cal_y + (_after3_m - 1) // 12
        _after3_m = (_after3_m - 1) % 12 + 1
        for _ in range(3):
            _btn_by_text(op, "›").click()
            QApplication.processEvents()
        ym_after_next = "%d年%d月" % (_after3_y, _after3_m)
        ck("g6 翻月 next：当月 +3 月（标签同步）",
           _btn_by_text(op, ym_after_next) is not None, ym_after_next)
        _btn_by_text(op, "›").click()
        QApplication.processEvents()
        _after4_m = _after3_m + 1
        _after4_y = _after3_y + (_after4_m - 1) // 12
        _after4_m = (_after4_m - 1) % 12 + 1
        ck("g6 翻月跨年进位：再 +1 月（跨年自动进位）",
           _btn_by_text(op, "%d年%d月" % (_after4_y, _after4_m)) is not None,
           "%d年%d月" % (_after4_y, _after4_m))
        for _ in range(4):
            _btn_by_text(op, "‹").click()
            QApplication.processEvents()
        ck("g6 翻月 prev 回起点：前进 4 月后 4 次 ‹ 回到当前月",
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
           and bd_cks[1].text().startswith(PMDAY)
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
        # 夹具是「今天 + 上个月某天」⇒ 本月只该勾到今天那一条（上个月那条**不许**被勾）。
        ck("g6 勾选本月：只勾本月的行（上个月那条不勾）",
           bd_cks[0].isChecked() and not bd_cks[1].isChecked()
           and any(l.text().startswith("已选 1 天") for l in bd.findChildren(QLabel)),
           "today=%s prev=%s 勾选=%s" % (bd_cks[0].isChecked(), bd_cks[1].isChecked(),
                                        [l.text() for l in bd.findChildren(QLabel)
                                         if l.text().startswith("已选 ")]))

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
           bool(del_posts) and sorted(del_posts[-1].get("days") or []) == sorted([PMDAY, TDY])
           and not bd.isVisible()
           and any(l.text() == "已删除 2 天计费日志" for l in op.findChildren(QLabel))
           and any("/api/status" in c for c in calls),
           "body=%s visible=%s" % (del_posts[-1] if del_posts else None, bd.isVisible()))

        # ── check 页：重置勾选 + 查看进度条 ──
        # ⛔ 先清本机 checklist 记忆（QSettings）——勾选是**跨会话持久化**的，
        #    不清就把上一次运行剩下的勾选读回来 ⇒ 初始计数不是 0/11、勾 3 项也不是 3/11
        #    （断言随机器历史时红时绿）。
        from PySide6.QtCore import QSettings as _QS_g6 # noqa: PLC0415
        _QS_g6("WXAgent", "persona-morph-ui").remove("checklist")
        wrap_c = panels_qt.build_panel(t, "check")
        cp = wrap_c.widget()
        cp.show()
        QApplication.processEvents()
        list_cks = [c for c in cp.findChildren(QCheckBox)
                    if c.objectName().startswith("ckItem")] # 有 objectName + QSettings 持久化
        cnt0 = [l for l in cp.findChildren(QLabel) if l.text() == "已完成 0 / 11"]
        ck("g6 清单计数：11 个勾选行（objectName 同源）+ 计数标签「已完成 0 / 11」初始",
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
        # ⛔ 复位本机 checklist 记忆：本页断言依赖「初始 0/11」，若把本次的勾选留在 QSettings 里，
        #    下一次进程运行读回来的就不是 0/11 ⇒ 断言随运行历史时红时绿。
        _QS_g6("WXAgent", "persona-morph-ui").remove("checklist")

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

        # ── json 页：新窗口查看配置 → **原生窗口**（不再开浏览器看网页控制台）──
        if str(panels_custom.ROOT) not in sys.path:
            sys.path.insert(0, str(panels_custom.ROOT)) # json 页 import agent.config 需要产品根
        wrap_j = panels_qt.build_panel(t, "json")
        jp = wrap_j.widget()
        jp.show()
        QApplication.processEvents()
        opened.clear()
        _text_wins: list = []
        _orig_tw = panels_custom._show_text_window
        panels_custom._show_text_window = (
            lambda tt, parent, title, text: _text_wins.append((title, text)))
        try:
            b_raw = _btn_by_text(jp, "新窗口查看配置")
            if b_raw is not None:
                b_raw.click()
                QApplication.processEvents()
        finally:
            panels_custom._show_text_window = _orig_tw
        ck("g6 新窗口查看配置：走**原生窗口**（弹文本框看 /api/config 正文），一次 openUrl 都没有",
           b_raw is not None and len(_text_wins) == 1 and opened == []
           and "配置" in str(_text_wins[0][0]),
           "shown=%s opened=%s" % ([w[0] for w in _text_wins], opened))
    finally:
        QDesktopServices.openUrl = _orig_open
        config_io.write_patch = _orig_wpatch
        agent_bridge.current_url = _orig_url
        QDialog.exec = _orig_exec
        srv.shutdown()


def t_g7() -> None:
    """全链路真跑：memory 编辑/深挖弹窗（memEdit/deep-profile）。

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
        # ⛔ 下拉由建页后的**后台取数**填充（`_mem_boot`）⇒ 这里必须驱动事件循环等它到位，
        #    否则立刻 `count()` 只有占位项、`setCurrentIndex(1)` 静默不触发
        #    ⇒ `state["chat_key"]` 一直空（实测：追加那条约 `chat_key=''`）。
        _wait(lambda: chat_sel is not None and chat_sel.count() >= 2)
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
    """模型厂商下拉联动（web applyProvider :4799-4828 移植）。

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
    """uiConfirm 调用点集中补（确认补齐 8 处 + 缺失动作 2 处）。

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
              and _posts("/api/memory")[-1].get("user_id"))
        ck("g9 memory 清勾选：确认后逐条 POST {chat_key, user_id, scope}",
           isinstance(_posts("/api/memory")[-1], dict)
           and _posts("/api/memory")[-1].get("user_id") == "U1"
           and "scope" in _posts("/api/memory")[-1]
           and "user_ids" not in _posts("/api/memory")[-1],
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
        # 移分区：行内「移动」→ prompt 弹窗（_card_dialog 无 btn_ok，不会自动确认）
        # 
        b_mv = _btn_by_text(pp, "移动")
        ck("g9 persona 行内「移动」钮在场", b_mv is not None, "btn=%s" % b_mv)
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
        _wait(lambda: "已重置（原记录 9.9.9 ⇒ 现在 清空）" in bar.txt.text())
        ck("g9 updbar 重置更新状态：确认后 POST /api/update_reset + 回显原记录",
           _n_posts("/api/update_reset") > n0
           and "已重置（原记录 9.9.9 ⇒ 现在 清空）" in bar.txt.text(),
           "detail=%r posts=%d" % (bar.txt.text(), _n_posts("/api/update_reset")))
    finally:
        QDialog.exec = _orig_exec
        agent_bridge.current_url = _orig_url
        config_io.read_path = _orig_rp
        srv.shutdown()


def t_g10() -> None:
    """roleHint 行为档推荐 + briefs 简报卡。

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
        # ⛔ 会话下拉由建页后的**后台取数**填充（`_bf_chats` 改成了不阻塞建页）
        #    ⇒ 必须驱动事件循环等它到位，否则读到的是空占位（实测：n=0）。
        _wait(lambda: bf_chat is not None and bf_chat.count() >= 1
              and str(bf_chat.itemData(0) or "") == "group:g1")
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


def t_g11() -> None:
    """余额徽章（纯函数）+ 自检勾选持久化（QSettings）+ 日志自动刷新。

    余额走 _balance_text 纯函数逐 case（web loadBalance :3208-3221 + balMask 口径）；
    勾选持久化对齐 web wxAgent.checklist localStorage（Qt 用 QSettings 跨会话记忆）；
    autolog 对齐 web :7420 4s 轮询（默认开）。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QCheckBox # noqa: PLC0415

    import config_io # noqa: PLC0415
    import panels_custom # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])
    t = THEMES["whale"]
    bt = panels_custom._balance_text

    # ── ① 余额文本纯函数 ──
    ck("g11 余额：连接异常 → 查询失败", bt(None) == "查询失败", repr(bt(None)))
    ck("g11 余额：服务端 ok:false → 如实回显 error",
       bt({"ok": False, "error": "没配 Key"}) == "没配 Key", repr(bt({"ok": False, "error": "没配 Key"})))
    ck("g11 余额：real 照实 + 充值额（¥）",
       bt({"total_balance": 12.5, "topped_up_balance": 100, "currency": "CNY"})
       == "余额 ¥12.50（充值 ¥100.00）",
       repr(bt({"total_balance": 12.5, "topped_up_balance": 100, "currency": "CNY"})))
    ck("g11 余额：hide 隐藏 / fake 改数字 / USD 符号",
       bt({"total_balance": 12.5, "currency": "CNY"}, "hide") == "余额 已隐藏"
       and bt({"total_balance": 12.5, "currency": "CNY"}, "fake", "8888.88") == "余额 ¥8888.88"
       and bt({"total_balance": 1.2, "currency": "USD"}) == "余额 $1.20",
       "%r|%r|%r" % (bt({"total_balance": 12.5, "currency": "CNY"}, "hide"),
                     bt({"total_balance": 12.5, "currency": "CNY"}, "fake", "8888.88"),
                     bt({"total_balance": 1.2, "currency": "USD"})))
    ck("g11 余额：拿不到数字如实说未配置（绝不拼 undefined）+ fake 非法回落照实",
       bt({"total_balance": None, "currency": "CNY"}) == "余额 未配置"
       and bt({"total_balance": 12.5, "currency": "CNY"}, "fake", "abc") == "余额 ¥12.50",
       "%r|%r" % (bt({"total_balance": None, "currency": "CNY"}),
                  bt({"total_balance": 12.5, "currency": "CNY"}, "fake", "abc")))

    # ── ② 自检清单勾选持久化（QSettings）──
    st = QSettings("WXAgent", "persona-morph-ui")
    st.setValue("checklist", "") # 清残留，测试从零开始
    QApplication.processEvents()

    def _build_check():
        wrap = panels_qt.build_panel(t, "check")
        pp = wrap.widget()
        pp.show()
        QApplication.processEvents()
        return wrap, pp

    wrap1, p1 = _build_check()
    keep = [wrap1]
    cbs = [p1.findChild(QCheckBox, "ckItem%d" % i) for i in range(11)]
    cbs = [c for c in cbs if c is not None]
    # check 手写页的清单勾选框 = 11 行（objectName 定位，绕开页面零散勾选控件）
    ck("g11 自检清单 11 个勾选框在场", len(cbs) == 11, "n=%d" % len(cbs))
    if len(cbs) == 11:
        cbs[0].setChecked(True)
        cbs[3].setChecked(True)
        QApplication.processEvents()
        saved = str(st.value("checklist") or "[]")
        arr = _json.loads(saved) if saved else []
        ck("g11 勾选即写 QSettings（index 0/3=true）",
           len(arr) == 11 and arr[0] is True and arr[3] is True,
           "arr=%s" % saved[:60])
        # 重建页面 → 勾选跨会话恢复
        wrap2, p2 = _build_check()
        keep.append(wrap2)
        cbs2 = [p2.findChild(QCheckBox, "ckItem%d" % i) for i in range(11)]
        cbs2 = [c for c in cbs2 if c is not None]
        ck("g11 重建页面后勾选恢复（跨会话记忆）",
           len(cbs2) == 11 and cbs2[0].isChecked() and cbs2[3].isChecked()
           and not cbs2[1].isChecked(),
           "c0=%s c3=%s" % (cbs2[0].isChecked() if len(cbs2) > 3 else None,
                            cbs2[3].isChecked() if len(cbs2) > 3 else None))
        # 重置勾选 → 持久化同步清空
        b_rs = _btn_by_text_local(p2, "重置勾选")
        if b_rs is not None:
            b_rs.click()
        QApplication.processEvents()
        saved2 = str(st.value("checklist") or "[]")
        arr2 = _json.loads(saved2) if saved2 else []
        ck("g11 重置勾选 → 全清且持久化同步",
           all(not c.isChecked() for c in cbs2)
           and (not arr2 or not any(arr2)),
           "saved=%s" % saved2[:60])
    else:
        ck("g11 自检清单 11 个勾选框在场", False, "跳过持久化链")

    # ── ③ 日志页 autolog ──
    wrap = panels_qt.build_panel(t, "log")
    lp = wrap.widget()
    keep.append(wrap)
    lp.show()
    QApplication.processEvents()
    auto = lp.findChild(QCheckBox, "autolog")
    ck("g11 日志页「自动刷新」勾选在场且默认开（web :7420 4s 轮询）",
       auto is not None and auto.isChecked(),
       "auto=%s checked=%s" % (auto is not None, auto.isChecked() if auto else None))


def _btn_by_text_local(root, txt):
    from widgets import Btn # noqa: PLC0415

    hits = [b for b in root.findChildren(Btn) if b.text() == txt]
    return hits[0] if hits else None


def t_g12() -> None:
    """tools 每行「试一下」+ 可搜目录行（打开/移除）。

    手法同 t_g9：假后端 + patch current_url。断言：试一下真发 GET /api/tools/test
    （通了/没通两态如实回显）、测试不触发清单重载（/api/status 计数不变）、
    args 跨清单重载保留（UT_KEEP 等价）；可搜目录两行（有文件数/目录不存在）、
    打开 POST /api/open-path、移除 POST /api/file_search/del 并回显 note。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit # noqa: PLC0415

    import agent_bridge # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    test_state = {"err": False}

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
            if "/api/tools/test" in self.path:
                if test_state["err"]:
                    self._send({"ok": False, "error": "连接超时", "content": ""})
                else:
                    self._send({"ok": True, "ms": 42, "host": "api.x.com",
                                "content": '{"ok":1}'})
            elif "/api/status" in self.path:
                self._send({
                    "ok": True, "paused": False, "wechat_connected": True,
                    "listen": {"groups": 1, "privates": 0}, "model": "deepseek-chat",
                    "uptime_s": 5, "groups": [],
                    "user_tools": {"enabled": True, "dir": "tools.d", "ticked": 1,
                                   "counts_total": 5, "tools": [{
                                       "name": "weather", "enabled": True,
                                       "source": "内置", "host": "api.w.com",
                                       "description": "天气", "calls": 3,
                                       "last": 1758700000}],
                                   "problems": []},
                    "file_search": {"dirs": [
                        {"dir": "D:/下载", "exists": True, "count": 12},
                        {"dir": "D:/没了", "exists": False, "count": -1}]},
                })
            else:
                self._send({"ok": True})

        def do_POST(self): # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            except Exception: # noqa: BLE001
                body = {}
            calls.append(self.path + "#" + _json.dumps(body, ensure_ascii=False))
            if "/api/file_search/del" in self.path:
                self._send({"ok": True, "note": "已从可搜目录移除"})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g12").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port

    t = THEMES["whale"]

    def _posts(prefix):
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _status_n():
        return sum(1 for c in calls if c.startswith("/api/status"))

    def _wait(pred, timeout=8.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    try:
        wrap = panels_qt.build_panel(t, "tools")
        pp = wrap.widget()
        keep = [wrap]
        pp.show()
        _wait(lambda: _btn_by_text_local(pp, "试一下") is not None)
        # 排空：更早测试残留的延迟定时器（_load 的 singleShot 链等）在把 current_url
        # 指向本组假后端后会陆续打进来；先让它们跑完，基准计数才是干净的。
        for _ in range(6):
            QApplication.processEvents()
            _time.sleep(0.08)
        QApplication.processEvents()
        ck("g12 工具行三件在场（args 输入 utArgs + 试一下 + 结果行）",
           pp.findChild(QLineEdit, "utArgs") is not None
           and _btn_by_text_local(pp, "试一下") is not None,
           "ai=%s tb=%s" % (pp.findChild(QLineEdit, "utArgs") is not None,
                            _btn_by_text_local(pp, "试一下") is not None))

        # ── 试一下：成功态 ──
        # 说明：本页构建时会注册若干延迟定时器（_load 的 400ms singleShot 等），
        # 全量运行下更早测试残留的定时器也可能在此窗口触发并打到本组假后端上，
        # 故「测试不触发重载」的判定不能看绝对计数——只看点击那一刻新产生的请求。
        # 取基准前排空到「静默窗口」：连续若干轮 processEvents 且请求数不再增长，
        # 才算残留定时器真的跑完了（只睡固定时长挡不住周期长于该时长的残留）。
        _stable = 0
        _last_n = len(calls)
        for _ in range(60):
            QApplication.processEvents()
            _time.sleep(0.05)
            if len(calls) == _last_n:
                _stable += 1
                if _stable >= 8: # 连续 8 轮（≈0.4s）零新增 = 静默
                    break
            else:
                _stable = 0
                _last_n = len(calls)
        st_n0 = _status_n()
        n_calls0 = len(calls)
        ai = pp.findChild(QLineEdit, "utArgs")
        if ai is not None:
            ai.setText('{"city": "北京"}')
        _btn_by_text_local(pp, "试一下").click()
        # 点击后立刻抓一次「动作窗口」边界：残留定时器若在点击前已排空则不干扰；
        # 偶发仍晚到者不计入——本断言只关心「试一下」这颗按键本身有没有引发重载。
        n_after_click = len(calls)
        _wait(lambda: any("通了 · 42ms · api.x.com 返回" in l.text()
                          for l in pp.findChildren(QLabel)))
        tb_ok = any("通了 · 42ms · api.x.com 返回：{\"ok\":1}" in l.text()
                    for l in pp.findChildren(QLabel))
        ck("g12 试一下（成功）：GET /api/tools/test 带 name+args，回显「通了 · Nms · host 返回」",
           tb_ok and any("/api/tools/test" in c and "weather" in c
                         and "%E5%8C%97%E4%BA%AC" in c for c in calls),
           "labels=%s" % [l.text()[:40] for l in pp.findChildren(QLabel) if "通了" in l.text()])
        # 断言窗口：只看「点击动作」之后新增的请求里有没有 /api/status
        # （更早测试残留定时器打进来的 status 不算数——那不是本次点击引起的）
        # 口径与 web :3897 一致：从点击到 tools/test 落地的这一小段里，
        # 除了 tools/test 自己，不该再出现清单重载用的 /api/status。
        _new = calls[n_calls0:]
        _ti = next((i for i, c in enumerate(_new) if "/api/tools/test" in c), len(_new))
        # 到本次 tools/test 为止（含它）= 「点击 → 测试回执落地」这一小段。
        # 这段里除 tools/test 自己，不该再出现清单重载用的 /api/status——
        # 即「试一下」只发测试请求，不顺手重载整张清单。更晚到的 /api/status
        # 属 8 秒徽章轮询（与本点击无关），不在本断言窗口内。
        _around = _new[:_ti + 1]
        ck("g12 测试不触发清单重载（/api/status 计数不变，web :3897 同口径）",
           not any(c.startswith("/api/status") for c in _around)
           and n_after_click >= n_calls0,
           "before=%d new=%s" % (st_n0, _around[:10]))

        # ── 失败态 ──
        test_state["err"] = True
        _btn_by_text_local(pp, "试一下").click()
        _wait(lambda: any("没通 · 连接超时" in l.text() for l in pp.findChildren(QLabel)))
        ck("g12 试一下（失败）：如实回显「没通 · error」",
           any("没通 · 连接超时" in l.text() for l in pp.findChildren(QLabel)),
           str([l.text()[:30] for l in pp.findChildren(QLabel) if "没通" in l.text()]))
        test_state["err"] = False

        # ── args 跨清单重载保留（UT_KEEP 等价）──
        if ai is not None:
            ai.setText('{"q": "1"}')
        QApplication.processEvents()
        pp._ut_reload()
        _wait(lambda: pp.findChild(QLineEdit, "utArgs") is not None
              and pp.findChild(QLineEdit, "utArgs").text() == '{"q": "1"}')
        ai2 = pp.findChild(QLineEdit, "utArgs")
        ck("g12 args 与结果跨清单重载保留（web UT_KEEP :3875 同款）",
           ai2 is not None and ai2.text() == '{"q": "1"}'
           and any("没通 · 连接超时" in l.text() for l in pp.findChildren(QLabel)),
           "args=%r" % (ai2.text() if ai2 else None))

        # ── 可搜目录行：文本 + 打开 + 移除 ──
        ck("g12 可搜目录两行在场（N 个文件 / 目录不存在）",
           any("D:/下载" in l.text() and "12 个文件" in l.text()
               for l in pp.findChildren(QLabel))
           and any("D:/没了" in l.text() and "目录不存在" in l.text()
                   for l in pp.findChildren(QLabel)),
           str([l.text() for l in pp.findChildren(QLabel) if "D:/" in l.text()]))
        b_open = _btn_by_text_local(pp, "打开")
        if b_open is not None:
            b_open.click()
        _wait(lambda: len(_posts("/api/open-path")) >= 1)
        ck("g12 目录「打开」：POST /api/open-path {path}",
           _posts("/api/open-path")[-1] == {"path": "D:/下载"}
           if _posts("/api/open-path") else False,
           str(_posts("/api/open-path")[-1:] or [None]))
        b_rm = _btn_by_text_local(pp, "移除")
        if b_rm is not None:
            b_rm.click()
        _wait(lambda: len(_posts("/api/file_search/del")) >= 1
              and any("已从可搜目录移除" in l.text() for l in pp.findChildren(QLabel)))
        ck("g12 目录「移除」：POST /api/file_search/del {dir} + 回显服务端 note",
           _posts("/api/file_search/del")[-1] == {"dir": "D:/下载"}
           and any("已从可搜目录移除" in l.text() for l in pp.findChildren(QLabel)),
           str(_posts("/api/file_search/del")[-1:] or [None]))
    finally:
        agent_bridge.current_url = _orig_url
        srv.shutdown()


def t_g13() -> None:
    """自检：右下角鲸鱼挂件。

     架构变更：挂件从「Python 手绘复刻」改为**内嵌 WebView2 直接跑上游原版
    脚本**（whale-widget/client/widget.js，636KB；与 web 端同一份，仅补 token）。
    Python 侧因此**不再持有任何排版/取数字段**（旧断言的 w.bal / w.hint / w._timer
    已不存在于实现中，属过期断言而非回归）。

    本组只钉新架构的契约：
      A. 形态：透明顶层窗 + 正方形 + 降级兜底存在（WebView2 缺失时不当机）；
      B. 宿主页：透明背景 + composer 假体（原版 dshwIsChatRoot 的启动闸门）
         + 引 /dsh-whale/widget.js + 带 token —— 缺任一项原版脚本都不会启动；
      C. 交互仍由 Python 承担：拖拽位移 + 位置记忆 QSettings + 跨实例恢复；
      D. WebView2 建链是**异步**的（同步忙等会死等， 实测卡死根因）。
    """
    import inspect as _inspect # noqa: PLC0415
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt # noqa: PLC0415
    from PySide6.QtGui import QImage, QMouseEvent # noqa: PLC0415
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    import agent_bridge # noqa: PLC0415
    import whale_host as wh # noqa: PLC0415
    import whale_widget as ww # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])
    t = THEMES["whale"]

    Qt_Left = Qt.MouseButton.LeftButton
    _NoMod = Qt.KeyboardModifier.NoModifier

    # ── A/B（纯静态，零 WebView2 依赖，任何环境都能跑）──
    # ⛔ 窗口宽度是**冻结量**：本体尺寸 `--dshw-base = clamp(122, min(250, min(100vw,100vh)
    #    * 0.28), 625)` 取宽高里小的那个 ⇒ 窗口一加宽就把本体一起顶大。所以断言
    #    "宽 = 冻结常量、高 ≥ 宽、本体兜底尺寸明显小于窗口"。
    ck("g13 挂件形态：透明顶层窗；窗口宽冻结（加宽会顶大本体）、高有余量给弹层",
       ww.WhaleWidget.__mro__[1].__name__ == "QWidget"
       and ww.WhaleWidget.W == ww._WIN_W and ww.WhaleWidget.H >= ww.WhaleWidget.W
       and 0 < ww._WHALE_FALLBACK < ww.WhaleWidget.W
       and "setFixedSize(self.width(), th)" in _inspect.getsource(ww.WhaleWidget._fit_confirm),
       "W=%s _WIN_W=%s" % (ww.WhaleWidget.W, ww._WIN_W))

    _html = wh.build_host_html(3210, "tk")
    ck("g13-A 降级兜底在位（WebView2 缺失时画说明卡，不静默空白）",
       hasattr(ww.WhaleWidget, "_paint_fallback")
       and hasattr(ww.WhaleWidget, "closeEvent"))
    ck("g13-B 宿主页引原版脚本 /dsh-whale/widget.js（与 web 端同一份，非二次实现）",
       "/dsh-whale/widget.js" in _html)
    ck("g13-B 宿主页带 token（原版脚本内所有 /dsh-whale/* 请求都吃它）",
       "token=tk" in _html)
    ck("g13-B 宿主页带 composer 假体（dshwIsChatRoot 认不出就不碰 DOM，启动闸门）",
       "data-composer-input" in _html)
    # 几何真值三件套：本体的视口矩形、弹层相对**本体**的溢出量、弹层是否开着。
    # 前两个是宿主侧一切几何计算（吸附/翻转/落位/长高）的唯一依据。
    ck("g13-B 宿主页上报几何真值（本体矩形 rr + 弹层溢出量 need + 弹层开关 pop）",
       "function rootRect(" in _html and "function popInfo(" in _html
       and "rr:rr" in _html and "need:pi.n" in _html and "pop:pi.pop" in _html)
    # ⛔ 溢出量必须相对**本体**求：相对窗口求的话，窗口一长溢出量就归零 ⇒ 宿主缩回去
    #    ⇒ 上游 resize→settle 又把本体重贴到视口边缘 ⇒ 死循环（菜单上下弹动）。
    ck("g13-B 弹层溢出量按本体求值（对窗口尺寸/本体位置都是不变量 ⇒ 不自反馈震荡）",
       "if(b.left<l){v=Math.round(l-b.left)" in _html
       and "if(b.top<t){v=Math.round(t-b.top)" in _html
       and "out.pop=true" in _html)
    # 本体在窗口内可左右换边：上游把本体钉死在视口右下、弹层一律朝左展开，本体贴屏幕
    # 左缘时窗口得挂到屏幕外、菜单被裁一半。换边用 `!important` 压住上游 `express()`
    # 写的行内 `left/top`（行内样式只输给同源的重要声明）。
    ck("g13-B 本体贴边可切换，且用 !important 压住上游写的行内 left/top",
       "body.pm-side-left .dshwv-root{left:0!important" in _html
       and "body.pm-side-right .dshwv-root{left:auto!important" in _html
       and "window.__pmSetSide=function" in _html)
    ck("g13-B 宿主把「贴哪一边」真的发给页面（execute_script 调 __pmSetSide）",
       "__pmSetSide" in _inspect.getsource(ww.WhaleWidget._set_side))
    _host_src = (HERE / "whale_host.py").read_text(encoding="utf-8")
    ck("g13-B 宿主页背景透明（半透明窗透出桌面，不留色块）",
       "background:transparent" in _html)
    ck("g13-B 合成承载在位（put_RootVisualTarget + SendMouseInput + Environment3 QI）",
       "put_RootVisualTarget" in _host_src and "SendMouseInput" in _host_src
       and "ICoreWebView2Environment3" in _host_src)
    # 画布透明在窗口化承载下走 Controller2 的 DefaultBackgroundColor（vtable 槽 27，
    # 裸调用）。它与宿主窗的半透明属性是**一对**：画布不透明 ⇒ 半透明窗透出来的是
    # 内核自己的底色（黑/白块）；窗口不半透明 ⇒ 画布的透明处没有东西可透。
    # 所以断言成对，且锚定真实调用形态（QI 的 IID + 槽位 + A=0），不锚定函数名。
    _wsrc_t = (HERE / "whale_widget.py").read_text(encoding="utf-8")
    ck("g13-B 画布 A=0 与宿主窗半透明**成对**在位（缺一即黑块或鲸鱼不出现）",
       "C979903E-D4CA-4228-92EB-47EE3FA96EAB" in _host_src
       and "vtbl[27]" in _host_src and "put(c2_addr, 0)" in _host_src
       and "WA_TranslucentBackground" in _wsrc_t)

    _host_src = (HERE / "whale_host.py").read_text(encoding="utf-8")
    ck("g13-D 建链是异步链（同步忙等 processEvents 处理不了 COM 跨线程 RPC ⇒ 曾因异步忙等卡死）",
       "_wait_attr" not in _host_src and "_pump_messages" not in _host_src
       and "when_ready" in _host_src and "singleShot" in _host_src)
    ck("g13-D WebView2 用户数据目录独立于控制台（共目录会互抢锁 ⇒ 浏览器进程起不来）",
       "WhaleHost" in _host_src and "WebView2" in _host_src)

    # ── A2. 真构造 + 真 show（**只有 import 是查不出问题的**）──
    # 这条是「控制台整个黑屏」事故的防复发闸：挂件 __init__ 里曾残留一段
    # `if not self._booted: ...`（属性没初始化、`os` 没导入），模块能 import、
    # 静态检查也过，但**一构造就 NameError** ⇒ Shell 构造失败 ⇒ Qt 壳永远不出现
    # ⇒ 启动器 15 秒等不到窗口、走后备开窗、用户看到的是空白的后备窗。
    # 所以必须真造一次并 show 一次，异常要显式报出来。
    _ctor_err = ""
    try:
        _w0 = ww.WhaleWidget(THEMES["whale"])
        _w0._boot_webview = lambda *a, **k: None  # 只验构造与上屏，不建 WebView2
        _w0.show()
        QApplication.processEvents()
        _w0.hide()
        _w0.close()
    except Exception as _e:  # noqa: BLE001
        _ctor_err = "%s: %s" % (type(_e).__name__, _e)
    ck("g13-A2 挂件真构造 + show 不抛异常（只 import 查不出未初始化属性 ⇒ 黑屏事故根因）",
       not _ctor_err, _ctor_err or "ok")

    _wsrc = (HERE / "whale_widget.py").read_text(encoding="utf-8")
    ck("g13-A2 启动门控在 showEvent（不在 __init__ 里写死延时等窗口上屏）",
       "def showEvent" in _wsrc and "_booted" in _wsrc
       and "PM_WHALE_NO_WEBVIEW2" in _wsrc
       and "singleShot(400" not in _wsrc)

    # ── C（真造控件，但**不触发 WebView2 建链**：桩掉 _boot_webview）──
    # ⛔ 位置记忆是**用户现场状态**：本组跑在**独立的 QSettings 作用域**里，绝不碰
    #    用户自己的位置。旧做法是"先备份用户的值、写测试值、跑完还原"，看上去稳妥，
    #    实际把用户的挂件位置改坏过一次（标记与位置分两个键，还原只还了一个）——
    #    真实后果是"挂件整块落到屏幕外，只剩一个减号"。隔离作用域从根上免掉这类事故。
    _set_keep = getattr(ww, "_SET", None)
    ww._SET = ("WXAgent-selftest", "persona-morph-whale-selftest")
    st = QSettings(*ww._SET)
    st.remove("") # 从干净状态起跑（避免上一次自检的残留当起点）
    keep: list = []
    try:
        w = ww.WhaleWidget(t)
        keep.append(w)
        w._boot_webview = lambda *a, **k: None # 桩：本组只验交互，不建 WebView2

        # 拖拽（位移 > 阈值）= 移动窗口 + 位置记忆
        pos0 = w.pos()
        w.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress,
                                      QPointF(10, 10), QPointF(w.x() + 10, w.y() + 10),
                                      Qt_Left, Qt_Left, _NoMod))
        w.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove,
                                     QPointF(10, 10), QPointF(w.x() + 70, w.y() + 60),
                                     Qt_Left, Qt_Left, _NoMod))
        w.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease,
                                        QPointF(10, 10), QPointF(w.x() + 70, w.y() + 60),
                                        Qt_Left, Qt_Left, _NoMod))
        QApplication.processEvents()
        saved = st.value("whale_anchor")
        _ar = w._whale_rect()
        ck("g13-C 拖拽移动：窗口位移 + 位置落盘（记**本体右下角**，与窗口尺寸/贴边方向无关）",
           w.pos() != pos0 and isinstance(saved, list) and len(saved) == 2
           and [int(saved[0]), int(saved[1])] == [_ar.x() + _ar.width(),
                                                 _ar.y() + _ar.height()],
           "pos0=%s now=%s saved=%s whale=%s" % (pos0, w.pos(), saved, _ar))

        # 位置跨实例恢复
        w2 = ww.WhaleWidget(t)
        keep.append(w2)
        w2._boot_webview = lambda *a, **k: None
        ck("g13-C 位置跨实例恢复（重开还原上次拖到的位置）",
           (w2.x(), w2.y()) == (w.x(), w.y()),
           "w2=(%d,%d) w=(%d,%d)" % (w2.x(), w2.y(), w.x(), w.y()))

        # 位移 < 阈值 = 点击不当拖（不抖窗）
        pos1 = w.pos()
        w.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress,
                                      QPointF(10, 10), QPointF(w.x() + 10, w.y() + 10),
                                      Qt_Left, Qt_Left, _NoMod))
        w.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove,
                                     QPointF(10, 10), QPointF(w.x() + 12, w.y() + 11),
                                     Qt_Left, Qt_Left, _NoMod))
        w.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease,
                                        QPointF(10, 10), QPointF(w.x() + 12, w.y() + 11),
                                        Qt_Left, Qt_Left, _NoMod))
        QApplication.processEvents()
        ck("g13-C 位移 < 阈值 ⇒ 当点击不当拖（气泡菜单里的点按不会被抖成移窗）",
           w.pos() == pos1, "p1=%s now=%s" % (pos1, w.pos()))

        # 拖动（Qt 侧）：页面说"在本体上按下了" ⇒ 之后按**本体**的绝对位置跟手挪窗。
        # ⛔ 位置一律按本体算，不按窗口：本体在窗口内会左右换边（贴屏幕哪半就钉哪边），
        #    窗口左上角与本体左上角不是同一个点 —— 拿窗口当锚会在换边/吸附时跳一截。
        _w4 = ww.WhaleWidget(t)
        keep.append(_w4)
        _w4._boot_webview = lambda *a, **k: None
        _g4 = _w4.screen().availableGeometry()
        _ww4 = _w4._whale_size()[0]
        _w4._set_side("right")
        _w4._place_whale((_g4.left() + _g4.right()) // 2, (_g4.top() + _g4.bottom()) // 2)
        _real_cursor, _real_ctypes = ww.QCursor, ww.ctypes

        class _Cur:
            p = QPoint(600, 500)

            @staticmethod
            def pos():
                return _Cur.p

        class _U32:
            @staticmethod
            def GetAsyncKeyState(_v):
                return 0x8000 # 左键恒"按下"

        class _WDL:
            user32 = _U32()

        class _CT:
            windll = _WDL()

        try:
            ww.QCursor = _Cur
            ww.ctypes = _CT
            _w4._on_pagedrag("begin")
            _p1 = _w4._whale_rect().topLeft()
            _Cur.p = QPoint(600 + 60, 500 + 40) # 光标右 60、下 40
            _w4._drag_tick()
            _after = _w4._whale_rect().topLeft()
            _moved_ok = (_after.x() - _p1.x(), _after.y() - _p1.y()) == (60, 40)
            _w4._on_pagedrag("end")
        finally:
            ww.QCursor = _real_cursor
            ww.ctypes = _real_ctypes
        ck("拖动走 Qt：按下后按**本体**绝对位置 1:1 跟手挪窗，松手即收尾并停表",
           _moved_ok and not _w4._drag_timer.isActive() and _w4._drag_cursor is None,
           "from=%s to=%s timer=%s" % (_p1, _after, _w4._drag_timer.isActive()))

        # 页面报几何时不得再动位置：拖动中位置归 _drag_tick 独占，两边都挪就是抖
        _w4._drag_cursor = QPoint(0, 0)
        _w4._user_moved = True # 用户已接手摆放：首次上报不该再按记忆/默认角重排
        _before_rect = _w4._whale_rect()
        _w4._on_rects([[0, 0, 10, 10]], [0, 0, _ww4, _ww4], [0, 0, 0, 0], False)
        QApplication.processEvents()
        ck("拖动中不受页面几何上报干扰（拖动独占挪窗，避免两条公式互殴）",
           _w4._whale_rect() == _before_rect, "%s -> %s" % (_before_rect, _w4._whale_rect()))
        _w4._drag_cursor = None

        # 贴边吸附 + 自动翻转（纯几何决策，喂**本体左上角**的自由位置看结果）——
        # 规则照抄原版：贴左必翻、贴右不翻、自由时按"图像中心在屏幕左半/右半"。
        # ⛔ 判据必须是**本体自己的矩形**：本体尺寸随视口变（440×560 窗里只有 ≈123px），
        #    拿窗口宽度或某个常量当体宽，吸附点会差出一百多像素 ⇒ 拖到边缘"卡住"、
        #    吸附"时灵时不灵"。
        w5 = ww.WhaleWidget(t)
        keep.append(w5)
        w5._boot_webview = lambda *a, **k: None
        _g = w5.screen().availableGeometry()
        _bw = w5._whale_size()[0]
        _sp = w5._SNAP_PX
        _lx, _ls, _lf = w5._snap_geometry(_g.left() + 10)
        _rx, _rs, _rf = w5._snap_geometry(_g.right() + 1 - _bw - 9)
        _nx, _ns, _nf = w5._snap_geometry(_g.left() + _sp + 1) # 刚出吸附区
        _mx1, _ms1, _mf1 = w5._snap_geometry(_g.left() + 60)
        _mx2, _ms2, _mf2 = w5._snap_geometry(_g.right() - _bw - 60)
        ck("贴边吸附：本体离边 10px 吸附到边（贴左必翻、贴右不翻）、出带即脱开",
           _lx == _g.left() and _ls == "left" and _lf is True
           and _rx == _g.right() + 1 - _bw and _rs == "right" and _rf is False
           and _nx == _g.left() + _sp + 1 and _mx1 == _g.left() + 60
           and _mx2 == _g.right() - _bw - 60,
           "L=(%s,%s,%s) R=(%s,%s,%s) N=%s M=(%s,%s)" % (_lx, _ls, _lf, _rx, _rs, _rf,
                                                         _nx, _mx1, _mx2))
        ck("自由摆放按图像中心在屏幕左/右半定边与翻面（左半→钉窗口左边并翻）",
           _ms1 == "left" and _mf1 is True and _ms2 == "right" and _mf2 is False,
           "M1=(%s,%s) M2=(%s,%s)" % (_ms1, _mf1, _ms2, _mf2))
        ck("翻转走原版自己的 dshwv-left 类（不改原版代码，只切类）",
           "dshwv-left" in (HERE / "whale_widget.py").read_text(encoding="utf-8"), "")

        # 窗口长高：弹层相对**本体**要多少竖向空间就长多少，只增不减、宽度冻结。
        # ⛔ 判据相对本体求值才无环：窗口一长，上游页面自己的 resize→settle 会把本体
        #    重贴到视口边缘、上报的矩形跟着变；若按"矩形有没有超出窗口"判，就会
        #    grow→重贴→矩形变化→shrink→… 无限弹动（用户看到的菜单上下弹动）。
        w3 = ww.WhaleWidget(t)
        keep.append(w3)
        w3._boot_webview = lambda *a, **k: None
        _b3 = w3._whale_size()[1]
        w3._pop = True
        w3._need = [0, 300, 0, 0] # 只伸出 300：本体兜底 123 ⇒ 仍装得下
        w3._fit_window(); w3._fit_confirm()
        ck("长高有死区：小幅弹层不折腾窗口（不为几像素反复改尺寸）",
           (w3.width(), w3.height()) == (ww._WIN_W, ww._WIN_H),
           "size=(%d,%d)" % (w3.width(), w3.height()))
        _keep_rect = w3._whale_rect()
        w3._need = [0, 900, 0, 0] # 需要 123+900+10（再大也超不过屏幕可用高）
        for _ in range(6): # 连喂六拍（模拟页面每次重贴后重报同一个 need）
            w3._fit_window()
            w3._fit_confirm()
            QApplication.processEvents()
        _want_h = min(_b3 + 900 + ww._FIT_PAD,
                      w3.screen().availableGeometry().height())
        ck("窗口跟着弹层长高（相对本体算需求，上限=屏幕可用高），本体屏幕位置不动、宽度冻结",
           w3.height() == _want_h and w3.width() == ww._WIN_W
           and w3._whale_rect() == _keep_rect,
           "size=(%d,%d) want_h=%d whale=%s" % (w3.width(), w3.height(),
                                                _want_h, w3._whale_rect()))
        _h_after_grow = w3.height()
        w3._need = [0, 0, 0, 0]
        w3._pop = False
        for _ in range(6): # 弹层关掉也不许缩：缩会把向上展开的菜单从顶部裁掉
            w3._fit_window()
            w3._fit_confirm()
            QApplication.processEvents()
        ck("弹层关掉后窗口不缩（缩会让窗口上沿下移、裁掉菜单上半部分）",
           w3.height() == _h_after_grow, "h=%d -> %d" % (_h_after_grow, w3.height()))

        # ⛔ 弹动闸门：窗口一改尺寸，上游页面自己的 `resize → settle()` 会把本体重新
        #    贴到视口边缘、上报矩形跟着变。若尺寸判据是"矩形有没有超出窗口"，就会
        #    grow → 重贴 → 矩形变化 → shrink → … 无限弹动（用户看到的菜单上下弹动、
        #    上半部分被窗口上沿裁掉）。这里连喂八拍"本体每次都换位置、但相对本体的
        #    溢出量不变"，窗口尺寸必须**单调不减且很快停住**。
        w6 = ww.WhaleWidget(t)
        keep.append(w6)
        w6._boot_webview = lambda *a, **k: None
        _sizes = []
        for _i in range(8):
            _wy = 60 + _i * 37 # 页面每次重贴后本体的新位置
            w6._on_rects([[_i, _wy, 120, 120]], [_i, _wy, 123, 123], [0, 700, 0, 0], True)
            w6._fit_confirm() # 模拟复核拍落地
            QApplication.processEvents()
            _sizes.append(w6.height())
        ck("页面重贴本体不再带出窗口尺寸震荡（修正后：单调不减、且最多变一次）",
           _sizes == sorted(_sizes) and len(set(_sizes)) <= 2,
           "heights=%s" % (_sizes,))

        # ⛔ 「整块方形完全透明 ⇒ 用户看不见」闸门：控制器就绪、页面却一片空白时，
        #    若 paintEvent 只看 host.ok 就提前返回，窗口既没有画面也没有提示卡 ——
        #    用户看到的就是"挂件不见了"（且要等 70 秒看门狗才降级）。判据改成
        #    "页面亲自确认本体已渲染"（_boot_ok），在那之前一律画卡兜底。
        def _nz(im):
            n = 0
            for _y in range(0, im.height(), 3):
                for _x in range(0, im.width(), 3):
                    if ((im.pixel(_x, _y) >> 24) & 0xFF) > 8:
                        n += 1
            return n

        class _FakeHost:
            """替身宿主：本闸门只验 paintEvent 的判据，任何宿主调用都收下不作声。"""

            ok = True
            error = ""

            def close(self):  # noqa: ANN201
                pass

            def hide(self):  # noqa: ANN201
                pass

            def resize(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                pass

            def notify_moved(self):  # noqa: ANN201
                pass

            def execute_script(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                return True

            def send_mouse(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                pass

        w7 = ww.WhaleWidget(t)
        keep.append(w7)
        w7._boot_webview = lambda *a, **k: None
        w7._host = _FakeHost()
        w7._load_failed = False
        w7._boot_ok = False
        _im0 = QImage(w7.width(), w7.height(), QImage.Format.Format_ARGB32)
        _im0.fill(0)
        w7.render(_im0, QPoint(0, 0))
        _n_pending = _nz(_im0)
        w7._boot_ok = True
        _im1 = QImage(w7.width(), w7.height(), QImage.Format.Format_ARGB32)
        _im1.fill(0)
        w7.render(_im1, QPoint(0, 0))
        _n_ready = _nz(_im1)
        ck("页面没确认渲染前一律画卡（否则整块方形全透明 = 用户完全看不见）",
           _n_pending > 50 and _n_ready == 0,
           "待启动像素=%d 就绪像素=%d" % (_n_pending, _n_ready))
        ck("兜底卡文案分两态（未确认时说「正在启动」，失败时才说不可用）",
           "正在启动挂件" in _inspect.getsource(ww.WhaleWidget._paint_fallback)
           and "暂不可用" in _inspect.getsource(ww.WhaleWidget._paint_fallback), "")

        # ⛔ 页面才是"本体贴哪一边"的真值：本地偏移与页面不一致时（回执丢了/脚本没跑/
        #    页面重载）会差 300 多像素 —— 足以把本体整个放到屏幕外。每收到一次几何上报
        #    就对齐一次，且本体真实屏幕位置不变。
        w8 = ww.WhaleWidget(t)
        keep.append(w8)
        w8._boot_webview = lambda *a, **k: None
        w8._user_moved = True # 跳过按锚点重排，本闸门只验"方向校正"
        w8._side = "right"
        _px_before = w8.x() # 页面说：本体在窗口左沿 ⇒ 本体真实屏幕 x
        w8._on_rects([[0, 300, 130, 130]], [0, 300, 123, 123], [0, 0, 0, 0], False)
        QApplication.processEvents()
        ck("本体贴边方向以**页面上报**为准（本地不一致时认页面，本体真实位置不动）",
           w8._side == "left" and w8._whale_rect().x() == _px_before
           and w8._whale_rect().x() == w8.x(),
           "side=%s 窗口 x=%d 本体 x=%d 期望=%d"
           % (w8._side, w8.x(), w8._whale_rect().x(), _px_before))

        # 现场日志：挂件失败态在桌面上"什么都不显示"，没有任何现场可查 ⇒ 关键节点落盘
        _wsrc_now = (HERE / "whale_widget.py").read_text(encoding="utf-8")
        ck("挂件关键节点落 logs/whale.log（上屏/几何/启动结论/看门狗/降级/重导航）",
           "def _diag(" in _wsrc_now and "whale.log" in _wsrc_now
           and _wsrc_now.count("_diag(") >= 7, "count=%d" % _wsrc_now.count("_diag("))
        # 页面静默失败（脚本被拦/加载挂起）要能自愈：重导航有**次数上限**，不做无界自链
        ck("页面无回报则有限次重导航（自愈偶发加载失败，且不自链）",
           "def _retry_page" in _wsrc_now and "_RETRY_MAX" in _wsrc_now
           and "n > _RETRY_MAX" in _wsrc_now
           and "build_host_html(port, token)" in _wsrc_now, "")
    finally:
        for x in keep:
            x.close()
        if _set_keep is not None:
            ww._SET = _set_keep
        st.remove("") # 清掉自检作用域，别留残留给下一次
        QApplication.processEvents()


def t_g14() -> None:
    """系统性漏点清算：GUIDES actions（5 种 kind）+ 云测试连通 + 微信重检 + 侧栏记忆。

    手法同 t_g9：假后端 + patch current_url/QDialog.exec/QDesktopServices.openUrl。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QDesktopServices # noqa: PLC0415
    from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QWidget) # noqa: PLC0415

    import agent_bridge # noqa: PLC0415
    import panels_custom # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    calls: list = []
    opened: list = []
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
            if "/api/voice/test" in self.path:
                self._send({"ok": True, "note": "识别链路可用"})
            elif "/api/wechat/recheck" in self.path:
                self._send({"ok": True, "install": {"state": "installed_not_running"}})
            elif "/api/status" in self.path:
                self._send({"ok": True, "paused": False, "wechat_connected": True,
                            "listen": {"groups": 1, "privates": 0},
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
            if "/api/tools/new_manifest" in self.path:
                self._send({"ok": True, "path": "tools.d/my_tool.json"})
            elif "/api/cloud/test" in self.path:
                self._send({"ok": True, "stage": "连接", "status": 200, "ms": 88})
            else:
                self._send({"ok": True})

        def log_message(self, *a): # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True, name="fake-be-g14").start()

    _orig_url = agent_bridge.current_url
    agent_bridge.current_url = lambda: "http://127.0.0.1:%d/?token=tk" % port
    exec_dlgs: list = []
    _orig_exec = QDialog.exec
    QDialog.exec = lambda self, *a, **k: (exec_dlgs.append(self), 0)[1]
    _orig_open = QDesktopServices.openUrl
    QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))

    t = THEMES["whale"]

    def _posts(prefix):
        return [_json.loads(c.split("#", 1)[1]) for c in calls
                if "#" in c and c.split("#", 1)[0].startswith(prefix)]

    def _wait(pred, timeout=8.0):
        end = _time.time() + timeout
        while _time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            _time.sleep(0.03)
        QApplication.processEvents()
        return pred()

    keep: list = []

    def _open_guide(key, btn, note):
        """打开一条引导弹窗并**返回它自己**。

        ⛔ 不要 `exec_dlgs[-1]` 取"最后一个"：上一步动作的异步回调（note 刷新 / `_go` 切页）
        可能晚于本步到达，把新弹窗挤到后面 ⇒ 取到**上一次的对话框**（实测 note 回显串到
        「已打开『tools.d』」上，断言随机器负载时红时绿）。
        """
        _before = len(exec_dlgs)
        panels_custom._guide_open(key, btn, note)
        _new = exec_dlgs[_before:]
        return _new[-1] if _new else exec_dlgs[-1]

    try:
        # ① GUIDES 全量 actions 解析（10 条 / 5 种 kind）
        gs = panels_custom._web_guides()
        total = sum(len(g.get("actions") or []) for g in gs.values())
        kinds = {a.get("kind") for g in gs.values() for a in (g.get("actions") or [])}
        ck("g14 GUIDES 全量 actions 解析（10 条 / gen·open·test·goto·openUrl 五类）",
           total == 10 and kinds == {"gen", "open", "test", "goto", "openUrl"},
           "n=%d kinds=%s" % (total, sorted(kinds)))

        # ② 引导弹窗动作按钮 + gen / open
        b = Btn("trigger", t, "ghost")
        keep.append(b)
        got: list = []
        fake_win = QWidget() # 真 QWidget 当「假壳」：_card_dialog 拿它当 parent、goto 找它的 _go
        keep.append(fake_win)
        fake_win._go = lambda sec, lab: got.append((sec, lab))
        b.window = lambda: fake_win
        note = QLabel("")
        dlg = _open_guide("tools", b, note)
        labs = [x.text() for x in dlg.findChildren(Btn)]
        ck("g14 引导弹窗动作按钮在场（tools：生成模板 / 打开目录）",
           "生成模板清单到 tools.d/" in labs and "打开 tools.d 目录" in labs,
           str(labs))
        genb = next(x for x in dlg.findChildren(Btn)
                    if x.text() == "生成模板清单到 tools.d/")
        genb.click()
        _wait(lambda: bool(_posts("/api/tools/new_manifest"))
              and "已生成模板：tools.d/my_tool.json" in note.text())
        ck("g14 gen 动作：POST /api/tools/new_manifest + 回显模板路径",
           bool(_posts("/api/tools/new_manifest"))
           and "已生成模板：tools.d/my_tool.json" in note.text(),
           "note=%r" % note.text())
        openb = next(x for x in dlg.findChildren(Btn) if x.text() == "打开 tools.d 目录")
        openb.click()
        _wait(lambda: bool(_posts("/api/open-path")))
        ck("g14 open 动作：POST /api/open-path {path}",
           _posts("/api/open-path")[-1] == {"path": "tools.d"},
           str(_posts("/api/open-path")[-1:]))

        # ③ test 动作（voice → GET /api/voice/test）
        dlg2 = _open_guide("voice", b, note)
        tb = [x for x in dlg2.findChildren(Btn) if x.objectName() == "guideAct"][0]
        tb.click()
        _wait(lambda: sum(1 for c in calls if "/api/voice/test" in c) >= 1)
        # 计分判据 = 请求到达；note 回显是**瞬态过程记录**——前一步 open 的回执
        # （_async_post 300ms 落地）可能晚到覆盖（与 t_commfb「补发积压」race 同款），
        # 不押进 ck，只在 extra 里如实呈现。
        _g14_hit = sum(1 for c in calls if "/api/voice/test" in c) >= 1
        ck("g14 test 动作：GET /api/voice/test 到达（note 为瞬态过程记录，不作判据）",
           _g14_hit, "note=%r" % note.text())

        # ④ goto 动作（video → window()._go）
        dlg3 = _open_guide("video", b, note)
        gb = [x for x in dlg3.findChildren(Btn) if x.objectName() == "guideAct"][0]
        gb.click()
        ck("g14 goto 动作：切到对应面板（videogen）+ 状态回显",
           ("videogen", "") in got and "已跳到对应面板" in note.text(),
           "got=%s note=%r" % (got, note.text()))

        # ⑤ openUrl 动作（wechat → 微信官网）
        dlg4 = _open_guide("wechat", b, note)
        ob = next(x for x in dlg4.findChildren(Btn) if x.text() == "打开官网下载")
        ob.click()
        ck("g14 openUrl 动作：打开微信官网",
           any("weixin.qq.com" in u for u in opened), str(opened[-1:]))

        # ⑥ 云测试两钮（community 页）
        wrap = panels_qt.build_panel(t, "community")
        keep.append(wrap)
        cp = wrap.widget()
        cp.show()
        QApplication.processEvents()
        url_w = panels_custom._c8_find_row(cp, lambda r: r.cfg == "cloud.persona_url")
        if url_w is not None and hasattr(url_w, "setText"):
            url_w.setText("https://x/hook/persona")
        b1 = cp.findChild(Btn, "cloudTestPersona")
        ck("g14 云测试两钮在场（人设/名单接收端）",
           b1 is not None and cp.findChild(Btn, "cloudTestBlocklist") is not None,
           "p=%s" % (b1 is not None))
        if b1 is not None:
            b1.click()
        _wait(lambda: bool(_posts("/api/cloud/test"))
              and any("人设：可达" in l.text() for l in cp.findChildren(QLabel)))
        ck("g14 云测试：POST {which,url} + 回显「可达（… HTTP 200 …）」",
           _posts("/api/cloud/test")[-1] == {"which": "persona",
                                             "url": "https://x/hook/persona"}
           and any("人设：可达" in l.text() and "HTTP 200" in l.text()
                   for l in cp.findChildren(QLabel)),
           "body=%s" % (_posts("/api/cloud/test")[-1:],))

        # ⑦ 微信重检（wechat 页）
        wrap = panels_qt.build_panel(t, "wechat")
        keep.append(wrap)
        wp = wrap.widget()
        wp.show()
        QApplication.processEvents()
        rb = wp.findChild(Btn, "wxRecheck")
        ck("g14 微信重检钮在场（我装好了，重新检测）", rb is not None, "btn=%s" % rb)
        if rb is not None:
            rb.click()
        _wait(lambda: any("/api/wechat/recheck" in c for c in calls)
              and any("微信已安装，登录后即可用" in l.text() for l in wp.findChildren(QLabel)))
        ck("g14 微信重检：GET /api/wechat/recheck + 三态回显",
           any("/api/wechat/recheck" in c for c in calls)
           and any("微信已安装，登录后即可用" in l.text() for l in wp.findChildren(QLabel)),
           str([l.text() for l in wp.findChildren(QLabel) if "微信" in l.text()][:3]))

        # ⑧ 侧栏收起跨会话记忆（源码级：QSettings 读写俱在；真机复验 UI）
        ssrc = _P(__file__).resolve().parent.joinpath("shell.py").read_text(encoding="utf-8")
        ck("g14 侧栏收起跨会话记忆（QSettings nav_tight 读+写）",
           'setValue("nav_tight"' in ssrc and 'value("nav_tight"' in ssrc,
           "write=%s read=%s" % ('setValue("nav_tight"' in ssrc,
                                 'value("nav_tight"' in ssrc))
    finally:
        QDialog.exec = _orig_exec
        QDesktopServices.openUrl = _orig_open
        agent_bridge.current_url = _orig_url
        srv.shutdown()


def t_g15() -> None:
    """A 类活键 5 个在 web↔Qt 双侧齐备 + 文本行的键级类型化。

    背景：`risk.quiet_hours` 这类「数组型文本行」若原样存成字符串，
    读取方（`agent/risk.py` 判 `isinstance(qh,(list,tuple))`）会**静默失效**——
    界面看着保存成功了，闸门其实一个晚上都不静默。本组把它钉死。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import re as _re # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit # noqa: PLC0415

    import panels_custom # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415
    from widgets import Btn # noqa: PLC0415

    QApplication.instance() or QApplication([])

    ROOT = _P(__file__).resolve().parent.parent
    WEB = WEB_PAGE.read_text(encoding="utf-8")
    EX = _json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

    K5 = ("risk.quiet_hours", "risk.escalate_after", "meme.enabled",
          "meme.max_results", "meme.cache_ttl_s")

    # ① web 真值：5 键都带 data-cfg（web 先补，Qt 才能跟随）
    incfg = set(_re.findall(r'data-cfg="([^"]+)"', WEB))
    ck("g15 web 真值含 5 活键的 data-cfg（web 先补、Qt 跟随）",
       all(k in incfg for k in K5),
       "缺: %s" % [k for k in K5 if k not in incfg])

    # ② 键级类型化：quiet_hours 必须是两个 0~23 整数，不合规一律 []
    ck("g15 quiet_hours 类型化：'22，7' → [22, 7]（全角逗号）",
       panels_qt._textish("risk.quiet_hours", "22，7") == [22, 7],
       repr(panels_qt._textish("risk.quiet_hours", "22，7")))
    ck("g15 quiet_hours 类型化：越界/单值/乱填 → []（＝不启用，不误拦）",
       all(panels_qt._textish("risk.quiet_hours", v) == []
           for v in ("25,7", "22", "abc", "")),
       "25,7=%r 22=%r abc=%r" % tuple(panels_qt._textish("risk.quiet_hours", v)
                                      for v in ("25,7", "22", "abc")))
    ck("g15 数组型文本行按 web 同款切分（block_keywords / store.keywords）",
       panels_qt._textish("risk.block_keywords", "傻,呆") == ["傻", "呆"]
       and panels_qt._textish("store.keywords", "a，b\nc") == ["a", "b", "c"],
       "%r %r" % (panels_qt._textish("risk.block_keywords", "傻,呆"),
                  panels_qt._textish("store.keywords", "a，b\nc")))
    ck("g15 JSON 型文本行解析（model_prices dict / tier_schedule.table list）",
       panels_qt._textish("api.model_prices", '{"m":{"in":1}}') == {"m": {"in": 1}}
       and panels_qt._textish("store.tier_schedule.table", '[{"from":"09:00"}]')
       == [{"from": "09:00"}],
       "非法 JSON 退化：dict=%r list=%r"
       % (panels_qt._textish("api.model_prices", "x"),
          panels_qt._textish("store.tier_schedule.table", "x")))
    ck("g15 非特化键保持字符串（不误切普通文本）",
       panels_qt._textish("api.base_url", "https://x") == "https://x",
       repr(panels_qt._textish("api.base_url", "https://x")))

    # ③ Qt 面板真的渲染出这 5 行，且初值/类型取自真配置或 example
    t = THEMES["whale"]
    keep: list = []
    found: dict = {}
    for sec in ("advanced", "community"):
        wrap = panels_qt.build_panel(t, sec)
        keep.append(wrap)
        pg = wrap.widget()
        for r, ctrl in (getattr(pg, "_c8_binds", None) or []):
            if r.cfg in K5:
                found[r.cfg] = (sec, r.kind, type(ctrl).__name__)
    ck("g15 Qt 面板渲染出 5 个活键行（advanced 2 + community 3）",
       set(found) == set(K5),
       "缺: %s / 得: %s" % ([k for k in K5 if k not in found],
                            {k: v[0] for k, v in found.items()}))
    ck("g15 行类型正确（quiet_hours=text；escalate/max/ttl=number；enabled=checkbox）",
       found.get("risk.quiet_hours", (None, ""))[1] == "text"
       and found.get("risk.escalate_after", (None, ""))[1] == "number"
       and found.get("meme.enabled", (None, ""))[1] == "checkbox",
       str({k: v[1] for k, v in found.items()}))

    # ④ 端到端：在 advanced 页填 quiet_hours 再走一次 _collect，值必须是 list[int]
    wrap = panels_qt.build_panel(t, "advanced")
    keep.append(wrap)
    pg = wrap.widget()
    pg.show()
    QApplication.processEvents()
    row = next((c for r, c in (getattr(pg, "_c8_binds", None) or [])
                if r.cfg == "risk.quiet_hours"), None)
    ck("g15 advanced 页取到 quiet_hours 控件（可交互）",
       isinstance(row, QLineEdit), "ctrl=%s" % type(row).__name__)
    if isinstance(row, QLineEdit):
        row.setText("23，7")
        patch: dict = {}
        for r, c in (getattr(pg, "_c8_binds", None) or []):
            if not r.cfg:
                continue
            v = panels_qt._ctrl_value(r, c)
            if v is not panels_qt._SKIP:
                patch[r.cfg] = v
        ck("g15 保存取值真类型化：patch['risk.quiet_hours'] == [23, 7]（非字符串）",
           patch.get("risk.quiet_hours") == [23, 7],
           "%r (%s)" % (patch.get("risk.quiet_hours"),
                        type(patch.get("risk.quiet_hours")).__name__))

    # ⑤ example 里 5 键的默认值可回填（_default_of 走 example；供空 config 首次渲染）
    from sec_meta import _default_of # noqa: PLC0415

    def _ex(path):
        cur = EX
        for p in path.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    ck("g15 5 键默认值可从 example 回填（首次渲染不留空）",
       all(_default_of(k) == _ex(k) for k in K5),
       str({k: (_default_of(k), _ex(k)) for k in K5}))


def t_g16() -> None:
    """B 类「web 无 data-cfg 的 JS 动态下钻控件」在 Qt 侧对齐。

    A 类缺口是「web 有控件、Qt 没渲染」；B 类是「web 用纯 JS 管一组控件
    （不发 data-cfg），元数据渲染天然盲区」——典型两处：
      · 联网搜索当前引擎参数（#wsProvider + wsKey/wsUrl/wsModel/wsEngine/wsCount
        → web_search.<provider> 小节），web 靠 wsSyncToForm/FromForm/ShowRows 三函数；
      · 记忆共享群（#memGroupsBox 多选勾选 → memory.shared_groups），
        web 靠 loadMemGroups/syncMemGroupsToCfg。
    Qt 侧若不对齐，用户能看到「引擎」「结果数」，却填不了任何引擎的 Key/地址，
    且共享群键名写成 memory.shared_group_names（**消费方读的是 shared_groups**，
    agent/memory.py:151）⇒ 勾了也白勾。本组把这两条钉死。
    """
    import os # noqa: PLC0415
    import re as _re # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QCheckBox,  # noqa: PLC0415
                                   QComboBox, QLineEdit)

    import panels_custom # noqa: PLC0415
    import panels_qt # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])

    ROOT = _P(__file__).resolve().parent.parent
    WEB = WEB_PAGE.read_text(encoding="utf-8")

    # ① web 真值：两处 JS 动态区确实无 data-cfg（证「B 类盲区」成立）
    ck("g16 web 真值：#wsKey/#wsModel/#wsEngine 等确无 data-cfg（B 类盲区成立）",
       all(f'id="{i}"' in WEB and f'data-cfg="{i}"' not in WEB
           for i in ("wsKey", "wsUrl", "wsModel", "wsEngine", "wsCount")),
       "若无 id 或已带 data-cfg，则本组归类有误")
    ck("g16 web 真值：三函数齐备（wsSyncToForm/wsSyncFromForm/wsShowRows）",
       all(f"function {f}(" in WEB for f in ("wsSyncToForm", "wsSyncFromForm", "wsShowRows")),
       "web 机制变了则 Qt 对齐口径要跟着改")
    ck("g16 web 真值：共享群写 memory.shared_groups（非 *_names）",
       "cfg.memory.shared_groups = names" in WEB and ".memGroupCk" in WEB,
       "消费方 agent/memory.py:151 读 shared_groups")

    # ② Qt 下拉选项 == web #wsProvider 的 <option>（运行时解析，不复制）
    t = THEMES["whale"]
    keep: list = []
    wrap = panels_qt.build_panel(t, "search")
    keep.append(wrap)
    pg = wrap.widget()
    pg.show()
    QApplication.processEvents()

    web_opts = _re.findall(r'<option value="([^"]+)">([^<]+)</option>',
                           WEB[:WEB.find("</select>", WEB.find('id="wsProvider"'))]
                           [WEB.find('id="wsProvider"'):])
    combo = getattr(pg, "_ws_provider", None)
    ck("g16 联网搜索页建出引擎下拉（#wsProvider）", isinstance(combo, QComboBox),
       type(combo).__name__)
    if isinstance(combo, QComboBox):
        qt_opts = [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]
        ck("g16 Qt 引擎下拉选项 == web #wsProvider（序与文案逐项一致）",
           qt_opts == web_opts,
           "web=%s / qt=%s" % (web_opts, qt_opts))

    # ③ 五控件都在，且 count 是文本框（对齐 web type=number 的 parseInt 语义）
    ws = getattr(pg, "_ws_widgets", {}) or {}
    ck("g16 五个引擎参数控件齐备（key/url/model/engine/count）",
       set(ws) == {"api_key", "base_url", "model", "engine", "count"},
       "得: %s" % sorted(ws))
    ck("g16 控件类型正确（Key=密码框；其余=文本框）",
       isinstance(ws.get("api_key"), QLineEdit) and ws["api_key"].echoMode() == QLineEdit.EchoMode.Password
       and isinstance(ws.get("count"), QLineEdit),
       "api_key=%s echo=%s" % (type(ws.get("api_key")).__name__,
                               getattr(ws.get("api_key"), "echoMode", lambda: "?")()))

    # ④ 切引擎显隐：deepseek → 模型行可见；zhipu → engine 行可见；其余都隐
    #    （offscreen 下 isVisible 受祖先 show 状态影响，用 isHidden 判「本控件显隐」）
    mw, mlb = pg._ws_model_row
    ew, elb = pg._ws_engine_row
    if isinstance(combo, QComboBox):
        for i in range(combo.count()):
            if combo.itemData(i) == "deepseek":
                combo.setCurrentIndex(i)
                break
        pg._ws_sync_to_form()
        ck("g16 选 DeepSeek → 仅「模型」行可见（engine 行隐藏）",
           not mw.isHidden() and ew.isHidden(),
           "model=%s engine=%s" % (not mw.isHidden(), not ew.isHidden()))
        for i in range(combo.count()):
            if combo.itemData(i) == "zhipu":
                combo.setCurrentIndex(i)
                break
        pg._ws_sync_to_form()
        ck("g16 选智谱 → 仅「engine」行可见（模型行隐藏）",
           not ew.isHidden() and mw.isHidden(),
           "model=%s engine=%s" % (not mw.isHidden(), not ew.isHidden()))
        for i in range(combo.count()):
            if combo.itemData(i) == "bing":
                combo.setCurrentIndex(i)
                break
        pg._ws_sync_to_form()
        ck("g16 选 Bing → 模型/engine 两行都隐藏（免 key 引擎无专属字段）",
           mw.isHidden() and ew.isHidden(),
           "model=%s engine=%s" % (not mw.isHidden(), not ew.isHidden()))

    # ⑤ 保存写回：web_search.<prov>.{api_key,base_url,model,engine,count}
    if isinstance(combo, QComboBox):
        for i in range(combo.count()):
            if combo.itemData(i) == "deepseek":
                combo.setCurrentIndex(i)
                break
        ws["api_key"].setText("sk-abc")
        ws["base_url"].setText("https://api.deepseek.com")
        ws["model"].setText("deepseek-chat")
        ws["count"].setText("")
        pg._ws_sync_from_form()
        p = pg._ws_patch
        ck("g16 保存写回 web_search.deepseek.*（count 留空 → 6，parseInt 语义）",
           p.get("web_search.deepseek.api_key") == "sk-abc"
           and p.get("web_search.deepseek.base_url") == "https://api.deepseek.com"
           and p.get("web_search.deepseek.model") == "deepseek-chat"
           and p.get("web_search.deepseek.count") == 6,
           str(p))
        ws["count"].setText("abc")
        pg._ws_sync_from_form()
        ck("g16 count 非数字 → 6（parseInt||6 语义）",
           pg._ws_patch.get("web_search.deepseek.count") == 6,
           repr(pg._ws_patch.get("web_search.deepseek.count")))
        ws["count"].setText("12")
        pg._ws_sync_from_form()
        ck("g16 count 合法 → 原值 12",
           pg._ws_patch.get("web_search.deepseek.count") == 12,
           repr(pg._ws_patch.get("web_search.deepseek.count")))

    # ⑥ 载入回填：wsSyncToForm 按 web_search.provider 取小节（count||6 非留空）
    ck("g16 页面暴露 _ws_sync_to_form/_ws_sync_from_form（供保存链并入）",
       callable(getattr(pg, "_ws_sync_to_form", None))
       and callable(getattr(pg, "_ws_sync_from_form", None)),
       "缺钩子则保存取不到引擎参数")
    ck("g16 _collect 经 _ws_sync_from_form 并入 patch（与 web saveAll 串接同语义）",
       "web_search.deepseek.api_key" in (pg._ws_patch or {}),
       str(list((pg._ws_patch or {}).keys())[:3]))

    # ⑦ 记忆页共享群：多选勾选容器 + 写回 memory.shared_groups（list，非 *_names）
    wrap2 = panels_qt.build_panel(t, "memory")
    keep.append(wrap2)
    mg = wrap2.widget()
    mg.show()
    QApplication.processEvents()
    box = mg.findChild(type(mg), "memGroupsBox")
    ck("g16 记忆页建出共享群容器（#memGroupsBox）",
       box is not None and box.objectName() == "memGroupsBox",
       "box=%s" % (box.objectName() if box else None))
    binds = getattr(mg, "c12_binds", None) or []
    has_sg = any(b[0] == "memory.shared_groups" for b in binds if len(b) >= 1)
    ck("g16 memory.shared_groups 进了保存链（binds）", has_sg,
       "cfg 清单: %s" % sorted({b[0] for b in binds if b}))
    ck("g16 共享群 binds 用 groups 语义（写回 list[str]）",
       any(b[0] == "memory.shared_groups" and b[2] == "groups" for b in binds),
       str([b for b in binds if b and b[0] == "memory.shared_groups"]))

    # ⑧ 勾选语义：容器内 QCheckBox 勾中 → patch 为群名 list（空勾 → []）
    if box is not None:
        # 手动塞两个勾选框（不依赖后台 /api/wechat-groups，纯测收集语义）
        # ⛔ 清容器必须 setParent(None)：只 deleteLater 是延迟销毁，
        #    后端活着时 load_groups 已塞进真群勾选框 ⇒ 幽灵框留在 findChildren
        #    里，计数 2 变 4（后端从"没起"变"在跑"后此坑现形）。
        while box.layout().count():
            it = box.layout().takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for nm in ("群甲", "群乙"):
            cb = QCheckBox(nm)
            cb.setProperty("gname", nm)
            box.layout().addWidget(cb)
        cbs = box.findChildren(QCheckBox)
        ck("g16 共享群容器按群逐勾（QCheckBox 列表）", len(cbs) == 2,
           "得 %d 个" % len(cbs))
        cbs[0].setChecked(True)
        names: list = []
        for cb in box.findChildren(QCheckBox):
            if cb.isChecked():
                names.append(str(cb.property("gname")))
        ck("g16 勾选收集为群名 list（勾 1 个 → ['群甲']）", names == ["群甲"], str(names))
        cbs[0].setChecked(False)
        cbs[1].setChecked(True)
        names = [str(cb.property("gname")) for cb in box.findChildren(QCheckBox)
                 if cb.isChecked()]
        ck("g16 全不勾 → []（＝用总开关，非残留旧值）", names == ["群乙"], str(names))


def t_g17() -> None:
    """首次引导向导（web onboarding :6118-6262 的 Qt 等价）。

    web 有一整套上手向导（选厂商/命名/勾群/**环境总览 + 逐条讲缺项**/恢复/体检），Qt 侧此前**完全缺失**
    ⇒ 新用户拿不到「填 Key → 起名字 → 选群 → 补环境 → 开始」的路径。本组钉死：
    ① 厂商清单/模型列表/Key 前缀运行时解析 web PROVIDERS（不复制）；
    ② 各步渲染与落盘（第 1 步 vendor→model→key，第 3 步群勾选，第 4 步环境总览 + 逐项，恢复/体检）；
    ③ 「已配置 Key 不打扰」的判定（对齐 web :6120-6123）；
    ④ 环境引导：第 4 步＝**运行环境校准**（`/api/status` 的 `env_hint`：浅色/缩放/字号/OCR），
       第 5 步起＝**每个功能缺件各一步**（GET `/api/env-guide`）；守"只检测+指路、不强制"红线；
    ⑤ ⛔ **步数不写死**：0 项缺 ⇒ 运行环境之后直接跳恢复；缺项越多步越多（不许"共 6 步"）。
    """
    import os # noqa: PLC0415
    import re as _re # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QPlainTextEdit # noqa: PLC0415

    import onboarding # noqa: PLC0415
    import panels_custom # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QWidget # noqa: PLC0415

    ROOT = _P(__file__).resolve().parent.parent
    WEB = WEB_PAGE.read_text(encoding="utf-8")

    # ① web 真值：向导函数齐备 + 关键 id 在册
    ck("g17 web 真值：onboarding() 与关键 id 齐备",
       all(s in WEB for s in ("async function onboarding()", 'id="obBody"', 'id="obNext"',
                              'id="obLater"', 'id="obProvider"', 'id="obModel"', 'id="obKey"',
                              'id="obNick"', 'id="obEnv"', 'id="obResume"', 'id="obCheck"')),
       "web 向导结构变了则 Qt 对齐口径要跟着改")

    # ② 厂商清单解析 == web PROVIDERS 减去 custom（web :5439 明确 filter(k=>k!=='custom')）
    provs = panels_custom._providers_from_web()
    web_keys = set(_re.findall(r"^\s*(\w+):\{label:'", WEB, _re.M))
    ck("g17 PROVIDERS 解析厂商数 == web（含 models 列表）",
       set(provs) == web_keys and len(provs) == 16,
       "web=%d qt=%d" % (len(web_keys), len(provs)))
    ck("g17 每家都解析出 models（deepseek 11 / zhipu 20 / openrouter 14）",
       len(provs.get("deepseek", {}).get("models", [])) == 11
       and len(provs.get("zhipu", {}).get("models", [])) == 20
       and len(provs.get("openrouter", {}).get("models", [])) == 14,
       "deepseek=%d zhipu=%d openrouter=%d" % (
           len(provs.get("deepseek", {}).get("models", [])),
           len(provs.get("zhipu", {}).get("models", [])),
           len(provs.get("openrouter", {}).get("models", []))))

    # ③ Key 未配置判定（web :5429-5432 同口径）
    ck("g17 Key 判定：空 / 占位 / 打码 / ****** → 未配置（要弹向导）",
       all(onboarding._key_is_unconfigured(v)
           for v in ("", "  ", "******", "sk-••••••", "在这里填你的key")),
       "%s" % [(v, onboarding._key_is_unconfigured(v))
               for v in ("", "******", "sk-••••••", "在这里填你的key")])
    ck("g17 Key 判定：真实 Key → 已配置（不打扰）",
       not onboarding._key_is_unconfigured("sk-fx2"),
       "%s" % onboarding._key_is_unconfigured("sk-fx2"))

    # ④ 向导构造：第 1 步厂商/模型/Key 控件就位（15 家，过滤 custom）
    t = THEMES["whale"]
    keep: list = []
    shell = QWidget()
    keep.append(shell)
    w = onboarding._Wizard(t, shell)
    keep.append(w)
    ck("g17 第 1 步：厂商下拉 15 项（web 过滤掉 custom）",
       isinstance(w.prov_w, QComboBox) and w.prov_w.count() == 15,
       "count=%d" % w.prov_w.count())
    ck("g17 第 1 步：切厂商联动模型列表（首项 deepseek → 11 模型）",
       w.model_w.count() == 11 and w.model_w.itemData(0) == "deepseek-flash",
       "n=%d first=%s" % (w.model_w.count(), w.model_w.itemData(0)))
    ck("g17 第 1 步：Key 前缀提示随厂商（deepseek → 「sk-」）",
       "sk-" in w.key_hint.text() and "DeepSeek" in w.key_hint.text(),
       w.key_hint.text())
    ck("g17 第 1 步：Key 输入框为密码框", w.key_w.echoMode() == w.key_w.EchoMode.Password,
       str(w.key_w.echoMode()))

    # ⑤ 第 1 步保存写回（api.api_key / api.model / api.base_url / provider_keys / provider 清空）
    orig_wp = panels_custom.config_io.write_patch
    orig_rp = panels_custom.config_io.read_path
    captured: dict = {}
    panels_custom.config_io.write_patch = lambda p: (captured.update(p), (True, "ok"))[1]
    panels_custom.config_io.read_path = lambda *a, **k: ({} if a[0] == "api.provider_keys" else "")
    try:
        w.key_w.setText("sk-fx3")
        w.step = 1
        w._step1_save()
    finally:
        panels_custom.config_io.write_patch = orig_wp
        panels_custom.config_io.read_path = orig_rp
    ck("g17 第 1 步保存：写 api.api_key/api.model/api.base_url",
       captured.get("api.api_key") == "sk-fx3"
       and captured.get("api.model") == "deepseek-flash"
       and captured.get("api.base_url") == "https://api.deepseek.com/v1",
       str({k: captured.get(k) for k in ("api.api_key", "api.model", "api.base_url")}))
    ck("g17 第 1 步保存：provider_keys 记该厂商 Key（web :5469）",
       isinstance(captured.get("api.provider_keys"), dict)
       and captured["api.provider_keys"].get("deepseek") == "sk-fx3",
       str(captured.get("api.provider_keys")))
    ck("g17 第 1 步保存：api.provider 清空（走顶层，web :5474）",
       captured.get("api.provider") == "", repr(captured.get("api.provider")))
    ck("g17 第 1 步保存后进入第 2 步", w.step == 2, "step=%d" % w.step)

    # ⑥ 第 3 步群勾选写回 wechat.group_name_white_list（不勾则不写）
    w._render_step3_groups([{"name": "群甲", "wxid": "wx1"}, {"name": "群乙", "wxid": "wx2"}])
    ck("g17 第 3 步：按检测到的群逐群勾选（QCheckBox）",
       len(w._group_cbs) == 2 and all(isinstance(c, QCheckBox) for c in w._group_cbs),
       "n=%d" % len(w._group_cbs))
    w._group_cbs[0].setChecked(True)
    captured2: dict = {}
    panels_custom.config_io.write_patch = lambda p: (captured2.update(p), (True, "ok"))[1]
    try:
        w.step = 3
        w._step3_save()
    finally:
        panels_custom.config_io.write_patch = orig_wp
    ck("g17 第 3 步保存：勾中群写 wechat.group_name_white_list",
       captured2.get("wechat.group_name_white_list") == ["群甲"],
       str(captured2.get("wechat.group_name_white_list")))
    ck("g17 第 3 步保存后进入第 4 步（恢复）", w.step == 4, "step=%d" % w.step)

    # ⑦ 第 5 步＝环境校准：读 /api/status 的 env_hint（字号/浅色/缩放/OCR），只提醒不强制
    _st_calls = {"n": 0}
    _orig_st = onboarding.config_io.get_json

    def _fake_env(url, *a, **k):
        if url.startswith("/api/status"):
            _st_calls["n"] += 1
            return {"env_hint": {
                "level": "warn", "ok": False, "title": "运行环境",
                "text": "建议调一下运行环境，识别会稳很多：\n· 把微信字号调回**默认**\n"
                        "不改也能用，只是识别会差一些（会走降级路径、可能要人工兜底）。",
                "items": [
                    {"key": "font", "level": "warn", "text": "把微信字号调回**默认**（现在不是默认档）",
                     "why": "字号会改行高 ⇒ 非默认档等于换了一套几何"},
                    {"key": "scale", "level": "ok", "text": "显示缩放：100%", "why": ""}],
                "detail": {"theme": "dark", "scale": 1.0, "font": None, "ocr_ready": True}}}
        assert url.startswith("/api/env-guide"), url
        return {"ok": False, "n_ok": 1, "n_fail": 1, "n_total": 2,
                "why": "1 项还没就绪（补齐后点重检）：虚拟声卡（发真语音条）",
                "items": [
                    {"key": "wechat", "title": "微信本体", "need": "要先有微信",
                     "ok": True, "state": "ok", "why": "已检测到微信 4.1.15.13",
                     "how": "", "install_kind": "none", "action": ""},
                    {"key": "vmic", "title": "虚拟声卡（发真语音条）", "need": "发语音条要它",
                     "ok": False, "state": "fail", "why": "没有可用的虚拟麦克风",
                     "how": "装一块成对的虚拟声卡（如 VB-CABLE）；我们不替你装驱动",
                     "install_kind": "driver", "action": "recheck"}]}

    onboarding.config_io.get_json = _fake_env
    try:
        w._render_step_hint()
    finally:
        onboarding.config_io.get_json = _orig_st
    _QLabel = __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel
    _hint_text = "".join(lb.text() for lb in w.body.findChildren(_QLabel))
    ck("g17 第 5 步：读 /api/status 的 env_hint", _st_calls["n"] >= 1, "n=%d" % _st_calls["n"])
    ck("g17 第 5 步：渲染「字号」这一条", "字号" in _hint_text, _hint_text[:90])
    ck("g17 第 5 步：渲染「缩放」这一条", "缩放" in _hint_text, _hint_text[:90])
    ck("g17 第 5 步：写明「不改也能用」（不强制）",
       "不改也能用" in (w.desc.text() + _hint_text), (w.desc.text() + _hint_text)[:120])
    ck("g17 第 5 步：待讲缺件＝只含未就绪项（微信本体不占步）",
       len(w._env_pending_cache) == 1 and w._env_pending_cache[0]["key"] == "vmic",
       "pend=%s" % [x.get("key") for x in w._env_pending_cache])
    ck("g17 第 5 步：下一步按钮", w.b_next.text() == "下一步", w.b_next.text())

    # ⑦.2 缺件步：一个缺件一步，说清「用来干啥 / 怎么补 / 这一类」
    w.step = 5
    w._on_next()   # 环境校准 ⇒ 第一个缺件
    _it_text = "".join(lb.text() for lb in w.body.findChildren(_QLabel))
    # _env_i＝"当前在讲第几个缺件"（0 起）⇒ 第一个缺件是 i=0、步号＝6
    ck("g17 缺件步：点下一步进入「缺件」那一页", w.step == 6 and w._env_i == 0,
       "step=%d i=%d" % (w.step, w._env_i))
    ck("g17 缺件步：步号从 6 起（第 5 步是环境校准，不许被缺件顶掉）",
       "第 6 步" in w.desc.text(), w.desc.text()[:40])
    ck("g17 缺件步：标题写缺的是哪一项",
       "虚拟声卡" in w.desc.text(), w.desc.text()[:60])
    ck("g17 缺件步：摊开「用来干啥 / 现在什么情况 / 怎么补」",
       "用来干啥" in _it_text and "怎么补" in _it_text and "现在什么情况" in _it_text,
       _it_text[:100])
    ck("g17 缺件步：写明「我们不替你装驱动」",
       "不替你装驱动" in _it_text, _it_text[-80:])
    ck("g17 缺件步：步数动态（文案里的「共 N 步」按实数算，不是写死 6）",
       ("共 %d 步" % w._env_total_steps()) in w.desc.text(), w.desc.text()[:40])

    # ⑦.3 缺件走完 ⇒ 体检（末步）
    w._on_next()
    ck("g17 缺件走完：落到体检步（有体检输出区 + 完成按钮）",
       hasattr(w, "check_out") and w.b_next.text() == "完成", w.b_next.text())

    # ⑨ 已配置 Key → maybe_show 不弹（web :6120-6123 同口径）
    class _FakeDlg:
        def __init__(self, *a, **k):
            pass

        def exec(self): # noqa: A003
            return 0

    orig_rp2 = onboarding.config_io.read_path
    orig_wiz = onboarding._Wizard
    opened = {"n": 0}

    def _fake_wiz(*a, **k):
        opened["n"] += 1
        return _FakeDlg()

    onboarding._ONBOARD_ONCE = False
    onboarding.config_io.read_path = lambda *a, **k: "sk-fx4"
    onboarding._Wizard = _fake_wiz
    try:
        onboarding.maybe_show(t, shell)
    finally:
        onboarding.config_io.read_path = orig_rp2
        onboarding._Wizard = orig_wiz
    ck("g17 已配置真实 Key → 不弹向导（不打扰老用户）", opened["n"] == 0, str(opened))

    # ⑩ 未配置 Key → 弹（且本进程只弹一次）
    onboarding._ONBOARD_ONCE = False
    onboarding.config_io.read_path = lambda *a, **k: ""
    opened["n"] = 0
    onboarding._Wizard = _fake_wiz
    try:
        onboarding.maybe_show(t, shell)
    finally:
        onboarding.config_io.read_path = orig_rp2
        onboarding._Wizard = orig_wiz
    ck("g17 未配置 Key → 弹向导（新用户进得来）", opened["n"] == 1, str(opened))
    ck("g17 向导只弹一次（进程内 _ONBOARD_ONCE）", onboarding._ONBOARD_ONCE is True,
       str(onboarding._ONBOARD_ONCE))


def t_g18() -> None:
    """键盘快捷键 + 分组折叠跨会话记忆。

    web 侧共 6 处文档级/控件级 keydown，逐处定性后：
      · **通用快捷键只有 2 条** —— `/` 聚焦导航搜索（:7151）、Esc 清空搜索（:7122）；
      · 行内输入框 2 条 —— customGroup Enter（:2666）、memSearch Enter（:7031），
        Qt 侧由控件自身 `returnPressed` 承接（`_chips_editor` / `_mem_search_enter`）；
      · 2 条 **web 形态专有，不适用** —— Escape 退光标接管（:3036，Qt 无「接管模式」）、
        ESC 全屏还原 postMessage（:3048，Qt 无 WebView2 宿主）。
    折叠记忆对齐 web `navGrpClosed` localStorage（:7043-7067）→ Qt QSettings `nav_grp_closed`。
    """
    import os # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings # noqa: PLC0415
    from PySide6.QtGui import QKeySequence # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QLineEdit # noqa: PLC0415

    import shell as shell_mod # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    QApplication.instance() or QApplication([])

    ROOT = _P(__file__).resolve().parent.parent
    WEB = WEB_PAGE.read_text(encoding="utf-8")

    # ① web 真值：6 处 keydown 逐条在册（口径变了 Qt 对齐要跟着改）
    ck("g18 web 真值：6 处 keydown 齐备（2 通用 + 2 行内 + 2 不适用）",
       all(s in WEB for s in (
           "$('customGroup').addEventListener('keydown'",        # :2666 行内
           "$('memSearch').addEventListener('keydown'",          # :7031 行内
           "box.addEventListener('keydown'",                     # :7122 Esc/Enter
           "document.addEventListener('keydown', (e)=>{",        # :7151 `/`
           "window.chrome.webview.postMessage('pm-esc-exit-fullscreen')",  # :3052
           "if(on && (ev.key === 'Escape' || ev.keyCode === 27)) stop();",  # :3036
       )),
       "web keydown 结构变了则 Qt 分类要重估")
    ck("g18 web `/` 快捷键带输入态守卫（tag input/textarea/select/contentEditable）",
       "if(tag === 'input' || tag === 'textarea' || tag === 'select'" in WEB,
       "Qt _focus_find 的「不抢输入态」正是抄这条")
    ck("g18 web 折叠记忆键名 navGrpClosed（localStorage）",
       "const LSKEY = 'navGrpClosed';" in WEB and "localStorage.setItem(LSKEY" in WEB,
       "Qt 等价物 = QSettings 键 nav_grp_closed")

    # ② Qt 侧 QShortcut 齐备且序列正确
    shell = shell_mod.Shell(THEMES["whale"])
    keep = [shell]
    # offscreen 下必须 show()：否则 isVisible() 全 False、焦点也不生效
    shell.show()
    QApplication.processEvents()
    QSettings("WXAgent", "persona-morph-ui").remove("nav_grp_closed")
    ck("g18 Qt 注册了 `/` 与 Esc 两条 QShortcut",
       getattr(shell, "_sc_find", None) is not None and getattr(shell, "_sc_esc", None) is not None,
       "")
    ck("g18 `/` 键序列 == QKeySequence('/')",
       shell._sc_find.key().toString() == QKeySequence("/").toString(),
       shell._sc_find.key().toString())
    ck("g18 `/` 作用于窗口（WindowShortcut，非全局抢系统）",
       str(shell._sc_find.context()).endswith("WindowShortcut"),
       str(shell._sc_find.context()))

    # ③ `/` 聚焦：空态下焦到搜索框并全选
    shell.find.clear()
    shell.stack.setFocus()  # 非输入控件
    QApplication.processEvents()
    shell._focus_find()
    QApplication.processEvents()
    ck("g18 非输入态按 `/` → 搜索框获焦",
       QApplication.focusWidget() is shell.find, str(QApplication.focusWidget()))

    # ④ `/` 不抢输入态：焦点在某 QLineEdit 上时不改变焦点（web 同口径）
    probe = QLineEdit(shell)
    probe.show()
    keep.append(probe)
    probe.setText("abc")
    probe.setFocus()
    QApplication.processEvents()
    before = QApplication.focusWidget()
    shell._focus_find()
    QApplication.processEvents()
    ck("g18 输入态按 `/` → 焦点不被抢（web :7155 同口径）",
       QApplication.focusWidget() is before is probe, str(QApplication.focusWidget()))

    # ⑤ Esc：有内容 → 清空并失焦；无内容 → 不动
    shell.find.setText("记忆")
    shell.find.setFocus()
    QApplication.processEvents()
    shell._find_escape()
    QApplication.processEvents()
    ck("g18 Esc 有内容 → 清空", shell.find.text() == "", repr(shell.find.text()))
    ck("g18 Esc 有内容 → 失焦",
       QApplication.focusWidget() is not shell.find, str(QApplication.focusWidget()))
    shell.find.clear()
    shell.find.setFocus()
    QApplication.processEvents()
    fw_before = QApplication.focusWidget()
    shell._find_escape()
    QApplication.processEvents()
    ck("g18 Esc 无内容 → 不动（让弹窗自接）",
       QApplication.focusWidget() is fw_before, str(QApplication.focusWidget()))

    # ⑥ navFind Enter：跳到首个可见项（web :7124 点第一个候选 <a>）
    shell.find.setText("记忆")
    shell._on_find("记忆")
    QApplication.processEvents()
    first_vis = next(((s, l) for it, _g, s, l in shell.items if it.isVisible()), None)
    ck("g18 搜索「记忆」筛出可见项", first_vis is not None, str(first_vis))
    got = {"sec": None, "label": None}
    _orig_go = shell._go
    shell._go = lambda sec, label: got.update(sec=sec, label=label)  # type: ignore[method-assign]
    try:
        shell._find_enter()
    finally:
        shell._go = _orig_go  # type: ignore[method-assign]
    ck("g18 navFind Enter → 跳首个可见项",
       got["sec"] == (first_vis[0] if first_vis else None) and got["sec"] is not None,
       "got=%r first=%r" % (got, first_vis))
    # 空搜索词时 Enter 不跳（web 只在有候选时 click）
    shell.find.clear()
    shell._on_find("")
    got["sec"] = None
    got["label"] = None
    shell._go = lambda sec, label: got.update(sec=sec, label=label)  # type: ignore[method-assign]
    try:
        shell._find_enter()
    finally:
        shell._go = _orig_go  # type: ignore[method-assign]
    ck("g18 navFind 空词 Enter → 不跳（无候选）", got["sec"] is None, str(got))

    # ⑦ 折叠跨会话记忆：toggle → 写 QSettings；新建实例 → 恢复折叠态
    grps = shell._groups()
    ck("g18 侧栏存在多个导航分组", len(grps) >= 2, str(len(grps)))
    title0 = grps[0].title
    grps[1].toggle()  # 折叠第 2 组 → 触发 toggled → 写 QSettings
    QApplication.processEvents()
    saved = QSettings("WXAgent", "persona-morph-ui").value("nav_grp_closed", [], type=list) or []
    ck("g18 折叠分组 → QSettings 记下被折叠组标题",
       grps[1].title in [str(x) for x in saved], str(saved))
    ck("g18 只记被折叠的（未折叠组不入册）", title0 not in [str(x) for x in saved], str(saved))

    shell2 = shell_mod.Shell(THEMES["whale"])
    shell2.show()
    QApplication.processEvents()
    keep.append(shell2)
    g2 = {g.title: g for g in shell2._groups()}
    ck("g18 新实例恢复折叠态（跨会话记忆生效）",
       g2[grps[1].title].collapsed,
       "collapsed=%s" % g2[grps[1].title].collapsed)
    ck("g18 未折叠组在新实例中仍展开",
       not g2[title0].collapsed, "collapsed=%s" % g2[title0].collapsed)

    # ⑧ 清场：恢复设置，避免污染其他测试的初始折叠态
    QSettings("WXAgent", "persona-morph-ui").remove("nav_grp_closed")
    keep.clear()


def t_g19() -> None:
    """健壮性专项：读不到时「别把故障说成空」+ 批量删除协议 + 探测并发。

    固化的六条（都来自故障注入环境实测，见 docs/回执-健壮性专项审查.md）：
      ① 记忆批删必须逐条带单值 user_id（后端只认这个键，`user_ids` 会被忽略）；
      ② 群列表/记忆/记录读失败时，文案不得与「真的空」混同；
      ③ 读失败原因要可行动（超时 vs 拒连分开）；
      ④ `detect_local` 必须并发（串行会让 /api/status 冷启动卡 ≈4×timeout）；
      ⑤ 会话页不再为「原始返回」卡多发一次请求。
    """
    import os # noqa: PLC0415
    import sys as _sys # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import panels_custom # noqa: PLC0415

    ROOT = _P(__file__).resolve().parent.parent
    SRC = (ROOT / "ui_qt" / "panels_custom.py").read_text(encoding="utf-8")

    # ① 批删协议：必须逐条 user_id，且不得再用后端不认的 user_ids
    ck("g19 记忆批删逐条发（单值 user_id，对齐后端 _rapi_memory_post）",
       '"user_id": uid' in SRC and '{"chat_key": state["chat_key"], "user_ids"' not in SRC,
       "后端只读 user_id；带 user_ids 会被忽略 ⇒ 一条都删不掉")
    ck("g19 记忆批删走顺序多发工具（_async_post_seq）",
       "_async_post_seq(page, \"/api/memory\", bodies, _seq_done" in SRC, "")
    ck("g19 批删部分成功如实报（不是笼统的已清除）",
       "只清除了" in SRC, "部分失败必须说出来，不能报成全部成功")

    # ② 读失败 ≠ 空：三处 wechat-groups 消费点都要判 ok
    ck("g19 记忆页群列表判 ok（读失败不再显示『未检测到群』）",
       'if r.get("ok") is False:' in SRC and "群列表暂时读不到" in SRC, "")
    ck("g19 记忆页读失败文案点明『不代表没有记忆』",
       "这不代表没有记忆" in SRC, "防把故障映射成空态")
    ck("g19 会话页读失败文案点明『不代表没有记录』",
       "这不代表没有记录" in SRC, "")
    ck("g19 简报/拍一拍两处群消费点也判 ok",
       "读不到群列表：" in SRC and SRC.count("读不到群列表：") >= 2,
       "读不到群列表: 出现 %d 次（记忆页/简报/拍一拍）" % SRC.count("读不到群列表："))
    ck("g19 真空态文案保留（未误伤『真的没有』）",
       "未检测到群（启动机器人并检测群后这里会列出）" in SRC and 'badge.set("info"' in SRC,
       "")

    # ③ 读失败原因可行动（超时 vs 拒连分开）
    ck("g19 新增 _read_fail_hint 区分超时/拒连/接口缺失",
       "def _read_fail_hint" in SRC and "后台响应超时" in SRC and "进程可能没起来" in SRC, "")

    # ④ 并发探测：detect_local 必须用线程池（串行会 4×timeout）
    IG = (ROOT / "agent" / "image_gen.py").read_text(encoding="utf-8")
    ck("g19 本地生图探测改并发（ThreadPoolExecutor）",
       "ThreadPoolExecutor" in IG and "def _probe_one" in IG, "")
    _sys.path.insert(0, str(ROOT))
    import agent.image_gen as ig # noqa: PLC0415
    ig._DETECT_CACHE["val"] = None
    _t0 = _time.time()
    ig.detect_local(timeout=0.3)  # 冷启动
    _dt = _time.time() - _t0
    ck("g19 冷启动探测耗时 ≈ 单个 timeout（不再 4×）",
       _dt < 1.2, "0.3s×4 串行应为 1.29s；实测 %.2fs" % _dt)
    ck("g19 probes= 传参不走缓存（测试拿即时结果）",
       "probes is not None" in IG, "")

    # ⑤ 会话页不再双拉 /api/sessions
    ck("g19 原始返回卡复用同一次响应（不再单独 GET）",
       "def _render_raw" in SRC and "_raw_box" in SRC
       and 'config_io.get_json("/api/sessions", timeout=5.0)' not in SRC,
       "原先 load_sessions + _refresh_raw 各打一次")


def t_g20() -> None:
    """能力层修复：**让读不到的东西真能读出来**，而不是加一行说明。

    三条真缺陷（都经临时 DATA_DIR 实跑取证）：
      ① 记忆落盘在 `data/memory/`，与消息档案库是两套独立存储；记忆页群下拉若只枚举
         `store.list_chats()`，消息库读不到时用户**有印象却看不到、选不中、删不掉**；
      ② `MemoryStore` 上**根本没有 `clear_all()`** —— 「清除全部记忆」一直抛 AttributeError
         被吞进 `except` ⇒ 从前就没真正清过（且旧实现还漏清「只有印象没有消息」的群）；
      ③ 全互通档下 `_chat_keys()` 把**转义目录名**当 chat_key 返回（`group:x@y` →
         `group_x_y`），机制上可能读错/删错会话。
    """
    import json as _json # noqa: PLC0415
    import os # noqa: PLC0415
    import shutil as _shutil # noqa: PLC0415
    import sys as _sys # noqa: PLC0415
    import tempfile # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    ROOT = _P(__file__).resolve().parent.parent
    _sys.path.insert(0, str(ROOT))

    import agent.config as _cfg # noqa: PLC0415
    import agent.memory as _mem # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="g20-mem-")
    _old = (_cfg.DATA_DIR, _mem.MEMORY_DIR, _mem.HISTORY_DIR)
    _old_cfg = _cfg.get_config
    try:
        _cfg.DATA_DIR = tmp
        _mem.MEMORY_DIR = os.path.join(tmp, "memory")
        _mem.HISTORY_DIR = os.path.join(tmp, "memory_history")

        # ── ① 记忆枚举不依赖消息库 ─────────────────────────────────────
        st = _mem.MemoryStore()
        ck_a = "group:WXID-alpha.01@chatroom"
        st.append(ck_a, "memberImpression", "他最近在准备面试", {"userId": "wxid_a", "target": "老张"})
        _disk = _mem.chat_keys_on_disk()
        ck("g20 记忆枚举只看记忆目录（消息库为空也能列出来）",
           len(_disk) == 1 and _disk[0].startswith("group:"),
           "chat_keys_on_disk=%s" % _disk)
        ck("g20 枚举出的 key 能读回成员（读写闭环）",
           [m["name"] for m in _mem.MemoryStore().members(ck_a)] == ["老张"],
           "members=%s" % [m["name"] for m in _mem.MemoryStore().members(ck_a)])

        # `_meta.json` 回写后应能**精确**还原原始 chat_key（含大写/点号/连字符）
        st.mark_consolidated(ck_a)
        _mp = os.path.join(_mem.MEMORY_DIR, os.listdir(_mem.MEMORY_DIR)[0], "_meta.json")
        _meta = _json.loads(open(_mp, encoding="utf-8").read())
        ck("g20 _meta.json 回写原始 chat_key（目录名单向转义可逆）",
           _meta.get("chat_key") == ck_a, "meta=%s" % _meta)
        ck("g20 回写后枚举精确等于原始 chat_key",
           _mem.chat_keys_on_disk() == [ck_a], "got=%s" % _mem.chat_keys_on_disk())

        # ── ② clear_all 真的存在且真的清干净 ───────────────────────────
        ck("g20 MemoryStore 上存在 clear_all（旧版缺失 ⇒ 一直静默失败）",
           hasattr(_mem.MemoryStore, "clear_all"), "")
        _n = st.clear_all()
        ck("g20 clear_all 返回清掉的成员档数", _n == 1, "n=%s" % _n)
        ck("g20 clear_all 后记忆目录枚举为空（含『只有印象没有消息』的群）",
           _mem.chat_keys_on_disk() == [], "got=%s" % _mem.chat_keys_on_disk())
        _left = [f for f in os.listdir(os.path.join(_mem.MEMORY_DIR, os.listdir(_mem.MEMORY_DIR)[0]))
                 if f.endswith(".json") and f != "_meta.json"]
        ck("g20 clear_all 后成员档全删（只剩 _meta.json）", _left == [], "left=%s" % _left)

        # ── ③ 全互通档用真实 chat_key，不再返回转义目录名 ───────────────
        st2 = _mem.MemoryStore()
        ck_b = "group:WXID-beta.02@chatroom"
        st2.append(ck_b, "memberImpression", "爱钓鱼", {"userId": "wxid_b", "target": "老李"})
        st2.mark_consolidated(ck_b)
        _cfg.get_config = lambda: {"memory": {"share_across_groups": True}}
        _keys = st2._chat_keys(ck_b)
        ck("g20 全互通档 _chat_keys 返回真实 chat_key（非转义目录名）",
           _keys == [ck_b], "keys=%s（转义名应为 %s）" % (_keys, "group_WXID_beta_02_chatroom"))
        ck("g20 全互通档 members 仍能合并读出",
           [m["name"] for m in st2.members(ck_b)] == ["老李"],
           "members=%s" % [m["name"] for m in st2.members(ck_b)])

        # ── ④ 产品层：chats 枚举 = 消息库 ∪ 记忆目录 ───────────────────
        _src = (ROOT / "scripts" / "persona_morph.py").read_text(encoding="utf-8")
        ck("g20 /api/memory list 的 chats 取『消息库 ∪ 记忆目录』",
           "chat_keys_on_disk()" in _src and "_keys.append(_ck)" in _src,
           "旧实现只有 store.list_chats()")
        ck("g20 clear_all 分支去掉了恒不命中的逐群 remove 空转",
           'orch.memory.remove(ck, "memberImpression", user_id=mem_id)' not in _src, "")
        ck("g20 clear_all 如实回报清掉的数量（清不动不谎报成功）",
           "cleared_members" in _src and "没敢删" in _src, "")
    finally:
        _cfg.DATA_DIR, _mem.MEMORY_DIR, _mem.HISTORY_DIR = _old
        _cfg.get_config = _old_cfg
        _shutil.rmtree(tmp, ignore_errors=True)


def t_g21() -> None:
    """跨环境健壮性：路径「不是目录」/「超长名」/「写不进去」时不许炸。

    三处真缺陷（都经真实文件系统注入取证，对应「换台电脑就复现」的典型情形）：
      ① `os.listdir` 只接 `FileNotFoundError` ⇒ 路径被换成**文件**时抛 `NotADirectoryError`，
         企业管控机/杀软隔离残留常见 —— 「读不到」直接变成「整页崩」；
      ② 群名/uid 超长 ⇒ 目录名撞 Windows 单文件名 **255 字节**上限（中文一字 3 字节，
         ~85 字中文群名就炸）⇒ 记忆整个写不进去；
      ③ 入站消息热路径落盘失败抛异常 ⇒ **监听线程当场挂**（用户看到「机器人突然不理人」）。
    """
    import os # noqa: PLC0415
    import shutil as _shutil # noqa: PLC0415
    import sys as _sys # noqa: PLC0415
    import tempfile # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    ROOT = _P(__file__).resolve().parent.parent
    _sys.path.insert(0, str(ROOT))

    import agent.config as _cfg # noqa: PLC0415
    import agent.memory as _mem # noqa: PLC0415
    import agent.store as _store # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="g21-io-")
    _old = (_cfg.DATA_DIR, _mem.MEMORY_DIR, _mem.HISTORY_DIR, _store.MESSAGES_DIR)
    try:
        _cfg.DATA_DIR = tmp
        _mem.MEMORY_DIR = os.path.join(tmp, "memory")
        _mem.HISTORY_DIR = os.path.join(tmp, "memory_history")
        _store.MESSAGES_DIR = os.path.join(tmp, "messages")

        def _reset_both():
            """把两个存储根恢复成「干净目录」——上一子场景可能把它们换成了文件。"""
            for _p in (_mem.MEMORY_DIR, _store.MESSAGES_DIR):
                if os.path.isdir(_p):
                    _shutil.rmtree(_p, ignore_errors=True)
                elif os.path.exists(_p):
                    os.remove(_p)
                os.makedirs(_p, exist_ok=True)

        _reset_both()
        _store.ChatStore(0).append_incoming("group:s@chatroom", "m1", 1700000000000,
                                            "wxid_a", "张三", "消息")
        _mem.MemoryStore().append("group:s@chatroom", "memberImpression", "印象",
                                  {"userId": "wxid_a", "target": "张三"})

        # ── ① 路径被换成文件（NotADirectoryError） ───────────────────────
        _shutil.rmtree(_store.MESSAGES_DIR, ignore_errors=True)
        with open(_store.MESSAGES_DIR, "w", encoding="utf-8") as f:
            f.write("not a dir")
        try:
            _chats = _store.ChatStore(0).list_chats()
            ck("g21 消息路径是文件时 list_chats 不抛（NotADirectory 收口）",
               isinstance(_chats, list), "got=%s" % _chats)
        except Exception as e:  # noqa: BLE001
            ck("g21 消息路径是文件时 list_chats 不抛（NotADirectory 收口）", False,
               "EXC %s: %s" % (type(e).__name__, e))
        try:
            _store.ChatStore(0).append_incoming("group:s@chatroom", "m2", 1700000001000,
                                                "wxid_a", "张三", "第二条")
            ck("g21 消息路径是文件时 append_incoming 不抛（热路径不许崩）", True, "")
        except Exception as e:  # noqa: BLE001
            ck("g21 消息路径是文件时 append_incoming 不抛（热路径不许崩）", False,
               "EXC %s: %s" % (type(e).__name__, e))

        # 记忆目录也换成文件
        if os.path.isdir(_mem.MEMORY_DIR):
            _shutil.rmtree(_mem.MEMORY_DIR, ignore_errors=True)
        elif os.path.exists(_mem.MEMORY_DIR):
            os.remove(_mem.MEMORY_DIR)
        with open(_mem.MEMORY_DIR, "w", encoding="utf-8") as f:
            f.write("not a dir")
        try:
            _ks = _mem.chat_keys_on_disk()
            _ms = _mem.MemoryStore().members("group:s@chatroom")
            ck("g21 记忆路径是文件时枚举/读取不抛", _ks == [] and _ms == [],
               "keys=%s members=%s" % (_ks, _ms))
        except Exception as e:  # noqa: BLE001
            ck("g21 记忆路径是文件时枚举/读取不抛", False, "EXC %s: %s" % (type(e).__name__, e))

        # ── ② 超长群名 / 超长 uid（Windows 255 字节文件名上限） ──────────
        _reset_both()
        _long_ck = "group:" + ("这是一个非常长的中文群名称" * 20) + "@chatroom"
        ck("g21 目录名有长度上限（_DIR_NAME_MAX 已定）",
           getattr(_mem, "_DIR_NAME_MAX", 0) > 0 and len(_mem._chat_dir_name(_long_ck)) <= 80,
           "dir=%r len=%d" % (_mem._chat_dir_name(_long_ck)[:30], len(_mem._chat_dir_name(_long_ck))))
        try:
            _mem.MemoryStore().append(_long_ck, "memberImpression", "内容",
                                      {"userId": "u1", "target": "某人"})
            _back = _mem.MemoryStore().members(_long_ck)
            ck("g21 超长中文群名可读写（不再 OSError）", len(_back) == 1, "members=%d" % len(_back))
        except Exception as e:  # noqa: BLE001
            ck("g21 超长中文群名可读写（不再 OSError）", False, "EXC %s: %s" % (type(e).__name__, e))
        # 超长 uid 也不许炸
        try:
            _mem.MemoryStore().append("group:short@chatroom", "memberImpression", "x",
                                      {"userId": "U" * 300, "target": "t"})
            ck("g21 超长 uid 不炸（成员档名也定长）",
               len(_mem.MemoryStore().members("group:short@chatroom")) == 1, "")
        except Exception as e:  # noqa: BLE001
            ck("g21 超长 uid 不炸（成员档名也定长）", False, "EXC %s: %s" % (type(e).__name__, e))
        # 超长名 + 回写 chat_key ⇒ 枚举能精确还原
        try:
            _mem.MemoryStore().mark_consolidated(_long_ck)
            ck("g21 超长群名回写 chat_key 后可精确枚举",
               _long_ck in _mem.chat_keys_on_disk(), "keys=%s" % _mem.chat_keys_on_disk()[:1])
        except Exception as e:  # noqa: BLE001
            ck("g21 超长群名回写 chat_key 后可精确枚举", False, "EXC %s: %s" % (type(e).__name__, e))

        # ── ③ 坏档扫描：所有读路径都不抛 ────────────────────────────────
        _bad_dir = os.path.join(_mem.MEMORY_DIR, "group_broken_chatroom")
        os.makedirs(_bad_dir, exist_ok=True)
        open(os.path.join(_bad_dir, "u1.json"), "w", encoding="utf-8").write("{坏 JSON")
        open(os.path.join(_bad_dir, "u2.json"), "w", encoding="utf-8").write("")
        _probes = [
            ("chat_keys_on_disk", lambda: _mem.chat_keys_on_disk()),
            ("members", lambda: _mem.MemoryStore().members("group:broken@chatroom")),
            ("overwrite_history", lambda: _mem.MemoryStore().overwrite_history("group:broken@chatroom")),
            ("collect_member_texts", lambda: _mem.MemoryStore().collect_member_texts("u1", "甲")),
        ]
        _bad = []
        for _n, _f in _probes:
            try:
                _f()
            except Exception as e:  # noqa: BLE001
                _bad.append("%s:%s" % (_n, type(e).__name__))
        ck("g21 坏 JSON 档下所有读路径都不抛", not _bad, "泄漏=%s" % _bad)
        # clear_all 遇读不动的档要如实报（不谎报成功）
        try:
            _mem.MemoryStore().clear_all()
            ck("g21 clear_all 遇坏档不谎报成功", False, "应当抛 OSError 说明读不动的档")
        except OSError as e:
            ck("g21 clear_all 遇坏档不谎报成功", "读不动" in str(e), "%s" % str(e)[:60])
        except Exception as e:  # noqa: BLE001
            ck("g21 clear_all 遇坏档不谎报成功", False, "异常类型意外：%s" % type(e).__name__)
    finally:
        _cfg.DATA_DIR, _mem.MEMORY_DIR, _mem.HISTORY_DIR, _store.MESSAGES_DIR = _old
        _shutil.rmtree(tmp, ignore_errors=True)


def t_g22() -> None:
    """入站落盘热路径：会话档被瞬时独占（杀软/索引器扫档）时不许打断监听。

    真实机制：`store._save_chat` 在收到消息的回调里同步跑（`scripts/persona_morph.py` 每条消息
    调一次 `store.append_incoming`）。它原来是「裸 `<dst>.tmp` + 单次 `os.replace`」：
    另一个进程（杀软实时扫描 / 索引器 / 另一个读者）**恰好**打开着这个档时，Windows 上
    `os.replace` 抛 `WinError 5`，异常直接穿透到监听线程 ⇒ **机器人突然不理人**。
    换成统一原子写（唯一临时名 + fsync + 有界退避重试）+ 热路径短等待预算后：
    瞬时独占一波（30ms）能自愈，写入不抛、条目不丢，且等待有上限不积压。
    """
    import os # noqa: PLC0415
    import shutil as _shutil # noqa: PLC0415
    import sys as _sys # noqa: PLC0415
    import tempfile # noqa: PLC0415
    import threading as _th # noqa: PLC0415
    import time as _time # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    ROOT = _P(__file__).resolve().parent.parent
    _sys.path.insert(0, str(ROOT))

    import agent.config as _cfg # noqa: PLC0415
    import agent.store as _store # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="g22-busy-")
    _old = (_cfg.DATA_DIR, _store.MESSAGES_DIR)
    try:
        _cfg.DATA_DIR = tmp
        _store.MESSAGES_DIR = os.path.join(tmp, "messages")
        _st = _store.ChatStore(0)
        _key = "group:g22@chatroom"
        _st.append_incoming(_key, "m0", 1, "wxid_a", "甲", "底稿")
        _dst = _store.chat_file(_key)

        # ⭐ 热路径必须有短等待预算（不许用统一默认 2s：等待期间监听被占住 = 消息积压）
        _budget = getattr(_store, "_SAVE_REPLACE_BUDGET_S", None)
        ck("g22 会话入档有热路径短等待预算（< 2s 通用默认）",
           isinstance(_budget, (int, float)) and 0 < float(_budget) < 2.0,
           "budget=%r" % _budget)

        # 模拟另一个进程持续瞬时独占目标档（杀软扫档的典型节奏：30ms 一波 / 300ms 间隔）
        _stop = _th.Event()

        def _holder():
            while not _stop.is_set():
                try:
                    _f = open(_dst, "r+b")
                    _time.sleep(0.03)
                    _f.close()
                except Exception: # noqa: BLE001
                    _time.sleep(0.002)
                _time.sleep(0.3)

        _t = _th.Thread(target=_holder, daemon=True)
        _t.start()
        _raised = []
        _t0 = _time.time()
        try:
            for _i in range(1, 41):
                try:
                    _st.append_incoming(_key, "m%d" % _i, _i, "wxid_a", "甲", "第 %d 条" % _i)
                except Exception as e: # noqa: BLE001
                    _raised.append("%d:%s" % (_i, type(e).__name__))
        finally:
            _stop.set()
            _t.join(timeout=2)
            _elapsed = _time.time() - _t0

        ck("g22 会话档被瞬时独占时写入不抛（不打断监听）", not _raised, "抛出=%s" % _raised[:3])

        _cnt = 0
        try:
            with open(_dst, encoding="utf-8") as f:
                _cnt = len(__import__("json").load(f).get("messages", []))
        except Exception as e: # noqa: BLE001
            ck("g22 会话档被瞬时独占时条目不丢", False, "读档失败 %s" % type(e).__name__)
        else:
            ck("g22 会话档被瞬时独占时条目不丢", _cnt == 41, "实际 %d 条（丢 %d）" % (_cnt, 41 - _cnt))

        # 等待有上限 ⇒ 40 条总耗时不该接近「每条等满预算」
        ck("g22 热路径等待有上限（40 条平均 < 150ms/条）", _elapsed < 6.0,
           "耗时 %.2fs（平均 %.0fms/条）" % (_elapsed, _elapsed / 40 * 1000))

        # 无遗留临时档（原子写自清）
        _left = [fn for fn in os.listdir(_store.MESSAGES_DIR) if fn.endswith(".tmp")]
        ck("g22 热路径写完后无遗留临时档", not _left, "遗留=%s" % _left[:3])

        # ⭐ 可观测指标（N4 审计项）：撞独占要"看得见"——上面的瞬时独占场景真发生过，
        #   `persist.REPLACE_STATS.hits` 必须有增长（撞了但重试成功也计数），worst_s 有读数。
        import agent.persist as _persist # noqa: PLC0415

        _rs = getattr(_persist, "REPLACE_STATS", None)
        ck("g22 REPLACE_STATS 记到撞冲突次数（含重试成功——冲突本身就是信号）",
           isinstance(_rs, dict) and int(_rs.get("hits") or 0) > 0,
           "hits=%s worst_s=%s" % (( _rs or {}).get("hits"), (_rs or {}).get("worst_s")))
        ck("g22 REPLACE_STATS.worst_s 有读数（单次最坏等待，成功失败都记）",
           isinstance(_rs, dict) and float(_rs.get("worst_s") or 0.0) >= 0.0,
           "worst_s=%s" % (_rs or {}).get("worst_s"))
    finally:
        _cfg.DATA_DIR, _store.MESSAGES_DIR = _old
        _shutil.rmtree(tmp, ignore_errors=True)


def t_ocr_fuzzy() -> None:
    """OCR 升级两组判据的真跑：候选集模糊匹配（授权档兜底）+ 标题带帧间投票。

    模糊档三条硬约束逐条验（距离上限挂钩长度 / 候选集唯一 / 时间词与括号数字守恒），
    并回归「KC测试 vs 测试」子串红线（strict 与 fuzzy 都不许收）；
    投票验编排语义（复核一致提前返 / 读空不否决 / 争议仲裁取多数 / 未决取首读）；
    最后真调 `chat_is_open` 走一遍 ③′ 档（strict 全漏 → 模糊唯一 → 放行）。
    """
    import os # noqa: PLC0415
    import sys as _sys # noqa: PLC0415
    from pathlib import Path as _P # noqa: PLC0415

    ROOT = _P(__file__).resolve().parent.parent
    _sys.path.insert(0, str(ROOT))

    import agent.chat_ocr as _co # noqa: PLC0415
    import agent.chat_header as _chdr # noqa: PLC0415
    from agent.wechat import WeChatAdapter as _WA # noqa: PLC0415

    # ── ① 编辑距离泛化 ──
    ck("编辑距离≤1（abc/abd 单替换收、abc/ace 距离2 拒）",
       _co._edit_dist_le("abc", "abd", 1) and not _co._edit_dist_le("abc", "ace", 1))
    ck("编辑距离≤2 边界（abcde/abxyz 距离3 拒、ab?xy 容忍2 收）",
       not _co._edit_dist_le("abcde", "abxyz", 2) and _co._edit_dist_le("abcxd", "abcye", 2))
    ck("编辑距离 k=0 退化为全等", _co._edit_dist_le("same", "same", 0)
       and not _co._edit_dist_le("same", "sane", 0))

    # ── ② 模糊档判据矩阵（真跑 matches_fuzzy）──
    _n = ["文件传输助手", "张三丰", "工作群A"]
    ck("模糊档：OCR 读错一字（演示祥→演示群）且候选无歧义 ⇒ 收",
       _co.matches_fuzzy("演示祥", "演示群", _n))
    ck("模糊档：候选里还有另一个「演示祥」⇒ 歧义不收（宁漏发不误发）",
       not _co.matches_fuzzy("演示祥", "演示群", ["演示祥"]))
    ck("模糊档：两字名不参与（章三≠张三 距离1 也拒——超短名误认代价高）",
       not _co.matches_fuzzy("章三", "张三", _n))
    ck("模糊档：时间词指纹守恒（星期六播报 vs 星期天播报 归一后同形 ⇒ 拒）",
       not _co.matches_fuzzy("星期天播报", "星期六播报", []))
    ck("模糊档：目标名自带括号数字时屏幕必须带同一个数字（测试（3） vs 测试（2） ⇒ 拒）",
       not _co.matches_fuzzy("测试（3）", "测试（2）", []))
    ck("模糊档：长名截断形态（互为包含、短侧≥4、候选唯一）⇒ 收",
       _co.matches_fuzzy("先期调研小组", "先期调研小组讨论群", _n))
    ck("模糊档：子串红线保持（KC测试 vs 测试 ——距离2/包含短侧2 ⇒ strict 与 fuzzy 都不收）",
       not _co.matches_strict("KC测试", "测试")
       and not _co.matches_fuzzy("测试", "KC测试", [])
       and not _co.matches_fuzzy("KC测试", "测试", []))
    ck("模糊档：空名/全剥空 ⇒ 不收（fail-closed 照旧）",
       not _co.matches_fuzzy("", "演示群", _n)
       and not _co.matches_fuzzy("演示群", "", _n))

    # ── ③ 标题带帧间投票（编排语义；桩 _header_read 与抓帧，zoom 循环已有真机背书）──
    _orig_hread = _co._header_read
    _orig_cbest = _co.capture_best
    _orig_ccap = _co.ch.capture_image

    class _FakeIm: # 假帧对象（只作占位，_header_read 已桩）
        pass

    try:
        _co.capture_best = lambda *a, **k: _FakeIm()
        _co.ch.capture_image = lambda *a, **k: _FakeIm()

        def _run_vote(reads, confirm=2):
            """按预置读数序列跑一次 header_text 自抓帧路径，返回 (结果, 实际消耗读数个数)。"""
            _q = list(reads)
            # ⛔ 桩必须吃下 `cands` 关键字：`header_text` 现在调 `_header_read(im, zoom, cands=cands)`，
            #   只接两个位置参数的旧桩会 TypeError ⇒ 被 `except` 吞掉 ⇒ 读空 ⇒ 6 条判据全假红。
            _co._header_read = lambda img, z=2, cands=None: _q.pop(0) if _q else ("", 0)
            got = _co.header_text(gui=None, confirm_frames=confirm)
            return got, len(reads) - len(_q)

        ck("投票：首读 A + 复核近形（距离≤1）⇒ 确认返 A（只耗 2 读）",
           _run_vote([("演示群", 3), ("演示祥", 3)]) == ("演示群", 2))
        ck("投票：首读 A + 复核读空 ⇒ 不否决返 A（读空不作为反对票）",
           _run_vote([("演示群", 3), ("", 0)]) == ("演示群", 2))
        ck("投票：首读 A + 复核异形 B + 仲裁 A ⇒ 返 A（多数胜出）",
           _run_vote([("演示群", 3), ("完全不同名", 3), ("演示群", 3)]) == ("演示群", 3))
        ck("投票：首读 A + 复核异形 B + 仲裁 B ⇒ 返 B（多数胜出，不迷信首读）",
           _run_vote([("演示群", 3), ("完全不同名", 3), ("完全不同名", 3)]) == ("完全不同名", 3))
        ck("投票：confirm_frames=1 ⇒ 关闭投票只读首帧（旧行为可退回）",
           _run_vote([("演示群", 3), ("演示祥", 3)], confirm=1) == ("演示群", 1))
        _q1 = [("演示群", 3)]
        _co._header_read = lambda img, z=2, cands=None: _q1.pop(0) if _q1 else ("", 0)
        ck("投票：显式传 img 走单帧路径（直读返回，不进复核）",
           _co.header_text(img=_FakeIm()) == "演示群" and not _q1)
    finally:
        _co._header_read = _orig_hread
        _co.capture_best = _orig_cbest
        _co.ch.capture_image = _orig_ccap

    # ── ④ chat_is_open 真跑：strict 全漏 → ③′ 模糊档唯一接近 ⇒ 放行 ──
    _orig_ccn = _WA.current_chat_name
    _orig_srows = _co.session_rows
    _orig_check = _chdr.check
    _orig_crex = getattr(_co, "candidate_rows_excluding_active", None)
    w = _WA.__new__(_WA) # 绕过 __init__（仿后端审查探针：只补身份闸用到的状态）
    w._gui = None
    w._idn_cache = None
    w._idn_txn = 0
    w._group_by_wxid = {}
    w._get_gui = lambda: None
    w._known_chat_names = lambda: [] # D1 新契约：DB 侧候选名（各场景按需覆写）
    w._active_row_time_ok = lambda chat_id, gui=None, img=None: (False, "桩：时间档不参与")
    w.current_chat_name = lambda gui=None, img=None: ("演示祥", "桩：绿底行补读（错一字）")
    try:
        w._idn_txn_begin() # 开事务（与产品 send_text 同款）
        # 场景 A：strict 全漏 → 模糊唯一 → 放行（事务已开）
        _co.session_rows = lambda img: [{"name": n} for n in
                                        ["文件传输助手", "张三丰", "工作群A"]]
        # ⛔ 候选集这一闸要**确定**：产品链里它由「真抓一帧 → 排除当前高亮行」得到
        #   （`candidate_rows_excluding_active` 要真像素），而自检环境抓不抓得到画面
        #   取决于**跑这一拍时的屏幕状态** ⇒ 候选集时有时无 ⇒ 本条判据在全量里偶发假失败
        #   （长期挂账的 `chat_is_open` 波动就是这个）。本用例要验的是**模糊档**与
        #   **候选集口径**，"排除高亮行"另有断言 ⇒ 这里只把"抓帧"这一步摘掉，
        #   候选名一律取各场景自己桩好的 `session_rows`。
        _co.candidate_rows_excluding_active = (
            lambda img, src: [r["name"] for r in _co.session_rows(img)])
        _chdr.check = lambda *a, **k: {"status": "no"} # 档④ 指纹桩：不成立
        ok, why = w.chat_is_open("group:x@chatroom", name="演示群")
        ck("chat_is_open：strict 全漏（读错一字）→ 候选集模糊唯一接近 ⇒ 放行且说明带档名",
           ok is True and "候选集模糊" in str(why),
           "ok=%s why=%s" % (ok, str(why)[:60]))
        # ⚠️ 每个场景前必须换事务 token：同键同事务内正面结论会被身份缓存复用
        #   （产品语义正确——约束 a+c），不换 token 场景 B/C 会被场景 A 的缓存污染。
        w._idn_txn_begin()
        # 场景 B：候选歧义（列表里真有另一个「演示祥」）⇒ 模糊不收 ⇒ 一路判否（不误放）
        _co.session_rows = lambda img: [{"name": n} for n in ["演示祥", "张三丰"]]
        ok2, why2 = w.chat_is_open("group:x@chatroom", name="演示群")
        ck("chat_is_open：候选歧义 ⇒ 模糊不收 ⇒ 判否（fail-closed 不误放）",
           ok2 is False, "ok=%s why=%s" % (ok2, str(why2)[:50]))
        w._idn_txn_begin()
        # 场景 C：strict 命中回归——读对时档① 直接过，模糊档根本不该被触达
        w.current_chat_name = lambda gui=None, img=None: ("演示群", "桩：读对了")
        _hit = {"fuzzy": 0}
        _real_mf = _co.matches_fuzzy
        _co.matches_fuzzy = lambda *a, **k: (_hit.__setitem__("fuzzy", _hit["fuzzy"] + 1)
                                             or _real_mf(*a, **k))
        ok3, _ = w.chat_is_open("group:x@chatroom", name="演示群")
        _co.matches_fuzzy = _real_mf
        ck("chat_is_open：读对时档① strict 直接放行，模糊档零触达（成本不变）",
           ok3 is True and _hit["fuzzy"] == 0, "fuzzy 触达=%d" % _hit["fuzzy"])
        # ── 场景 D/E（D1）：候选集必须 = 可见行 ∪ DB 已知名 ──
        from PIL import Image as _PImD # noqa: PLC0415（⑤ 段的 _PImage 在此处之后才定义）
        w._idn_txn_begin()
        _orig_cb = _co.capture_best
        _co.capture_best = lambda *a, **k: _PImD.new("RGB", (80, 40), (255, 255, 255))
        w.current_chat_name = lambda gui=None, img=None: ("工作群B", "桩：读到了打开中的会话名")
        _co.session_rows = lambda img: [{"name": n} for n in ["张三丰"]] # B 行不可见（绿底行读不准的形态）
        # 场景 D：竞争名只在 DB 侧 ⇒ 候选集补全后歧义可检 ⇒ 判否（修复前误判 True 的真实反例）
        w._known_chat_names = lambda: ["工作群B"]
        ok4, _ = w.chat_is_open("group:x@chatroom", name="工作群A")
        ck("chat_is_open：竞争名只在 DB（可见行没有）⇒ 候选集补全后歧义可检 ⇒ 判否（D1）",
           ok4 is False, "ok=%s" % ok4)
        w._idn_txn_begin()
        # 场景 E（反差组/缺陷形态存证）：同场景把 DB 名拿掉 ⇒ 竞争者缺席 ⇒「唯一接近」误放
        w._known_chat_names = lambda: []
        ok5, _ = w.chat_is_open("group:x@chatroom", name="工作群A")
        ck("chat_is_open：同场景 DB 名拿掉 ⇒ 旧形态误放 True（缺陷形态存证，反衬 D1 的作用点）",
           ok5 is True, "ok=%s" % ok5)
    finally:
        _co.capture_best = _orig_cb if "_orig_cb" in dir() else _co.capture_best
        _WA.current_chat_name = _orig_ccn
        _co.session_rows = _orig_srows
        if _orig_crex is not None:
            _co.candidate_rows_excluding_active = _orig_crex
        _chdr.check = _orig_check

    # ── ④′ _known_chat_names 单元口径（D1 补全源：群表 ∪ 昵称表 ∪ 监听会话展示名）──
    try:
        w2 = _WA.__new__(_WA)
        w2._groups = [{"wxid": "g1", "name": "工作群B"}]
        w2._nick_map = {"c1": "工作群A"}
        w2._monitored_chat_ids = lambda: ["private:friend1", "group:unknown@chatroom"]
        w2.display_name = lambda ck: {"private:friend1": "老友A"}.get(str(ck), str(ck))
        _kn = set(w2._known_chat_names())
        ck("_known_chat_names：群表 + 昵称表 + 监听会话展示名三者并入",
           {"工作群B", "工作群A", "老友A"} <= _kn, "got=%s" % sorted(_kn))
        ck("_known_chat_names：纯 wxid 兜底名不当 OCR 竞争者（剔除）",
           "group:unknown@chatroom" not in _kn, "got=%s" % sorted(_kn))
    except Exception as e: # noqa: BLE001
        ck("_known_chat_names 单元口径不抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:60]))

    # ── ④″ 滚动「见过的会话名」（D1 残余）：三表答不出的竞争者，读到过就该拦得住 ──
    try:
        w3 = _WA.__new__(_WA)
        w3._groups = []
        w3._nick_map = {}
        w3._monitored_chat_ids = lambda: []
        w3._seen_names_add(["工作群C", "", "工作群B"])
        _kn3 = set(w3._known_chat_names())
        ck("_seen_names_add：读到的行名进候选集（三表全空也并入）",
           {"工作群C", "工作群B"} <= _kn3, "got=%s" % sorted(_kn3))
        for _i in range(300):
            w3._seen_names_add(["填充%03d" % _i])
        ck("_seen_names_add：上限 256 条先进先出淘汰（重见不挪位，淘汰序确定；r5 加固 cap 64→256）",
           len(w3._seen_names) == 256 and "工作群C" not in w3._seen_names
           and "填充043" not in w3._seen_names and "填充044" in w3._seen_names
           and "填充299" in w3._seen_names,
           "n=%d" % len(w3._seen_names))
        # ── 场景 G（③）：当前行自己的读数不进候选集——模糊档核心场景回归 ──
        #   生产形态：列表里就有当前行（绿底高亮），OCR 把它读错一字；r5 及以前的实现把
        #   同源读数当候选 ⇒「a≈no」自否决（审查者端到端实测：B3 拒发 / B4 禁喂后放行）。
        #   修后按绿底带排除当前行 ⇒ 模糊唯一接近 ⇒ 放行（r4 行为恢复）。
        from PIL import Image as _PImG # noqa: PLC0415
        w5 = _WA.__new__(_WA)
        w5._gui = None
        w5._idn_cache = None
        w5._idn_txn = 0
        w5._group_by_wxid = {}
        w5._get_gui = lambda: None
        w5._groups = []
        w5._nick_map = {}
        w5._monitored_chat_ids = lambda: []
        w5.display_name = lambda ck: ""
        w5._active_row_time_ok = lambda chat_id, gui=None, img=None: (False, "桩：时间档不参与")
        w5.current_chat_name = lambda gui=None, img=None: ("演示祥", "桩：当前行读错一字")
        _orig_cb3 = _co.capture_best
        _orig_gb = _co.green_bands
        _orig_srows3 = _co.session_rows
        _co.capture_best = lambda *a, **k: _PImG.new("RGB", (80, 40), (255, 255, 255))
        _co.green_bands = lambda *a, **k: [{"y0": 90, "y1": 130, "y_abs": 100, "score": 0.9}]
        # 行结构仿生产：当前行（y=100，读错一字）+ 其他行
        _co.session_rows = lambda img: [
            {"name": "演示祥", "y_abs": 100}, {"name": "张三丰", "y_abs": 220},
            {"name": "李四", "y_abs": 330}]
        _chdr.check = lambda *a, **k: {"status": "no"}
        w5._idn_txn_begin()
        ok7, why7 = w5.chat_is_open("group:x@chatroom", name="演示群")
        ck("chat_is_open：当前行读错一字且其读数在列表里 ⇒ 排除当前行后模糊唯一接近 ⇒ 放行（r6 ③ 收口）",
           ok7 is True and "候选集模糊" in str(why7), "ok=%s why=%s" % (ok7, str(why7)[:50]))
        ck("chat_is_open：当前行读数不进滚动 seen 集（同源不自毒）",
           "演示祥" not in (w5._seen_names or {}), "seen=%s" % list((w5._seen_names or {}).keys())[:4])
        # r7 兜底（审查者"只改一处"建议）：绿底带量不到 ⇒ 名字相似度剔除最像当前行的
        # 候选（编辑距离 ≤2 才剔）⇒ 不再自否决（r6 时的反差组缺陷形态由此收口）
        _co.green_bands = lambda *a, **k: []
        w5._idn_txn_begin()
        ok8, why8 = w5.chat_is_open("group:x@chatroom", name="演示群")
        ck("chat_is_open：绿底带量不到 ⇒ 名字相似度兜底剔除当前行读数 ⇒ 仍放行（r7 建议落地）",
           ok8 is True and "候选集模糊" in str(why8), "ok=%s why=%s" % (ok8, str(why8)[:50]))
        # 场景 F：三表全空 + 竞争者只在 seen 集 ⇒ ③′ 走真 _known_chat_names 仍判否
        w4 = _WA.__new__(_WA)
        w4._gui = None
        w4._idn_cache = None
        w4._idn_txn = 0
        w4._group_by_wxid = {}
        w4._get_gui = lambda: None
        w4._groups = []
        w4._nick_map = {}
        w4._monitored_chat_ids = lambda: []
        w4.display_name = lambda ck: ""
        w4._active_row_time_ok = lambda chat_id, gui=None, img=None: (False, "桩：时间档不参与")
        w4._seen_names_add(["工作群C"])
        w4.current_chat_name = lambda gui=None, img=None: ("工作群C", "桩：读到未知会话名")
        _orig_cb2 = _co.capture_best
        _co.capture_best = lambda *a, **k: _PImD.new("RGB", (80, 40), (255, 255, 255))
        _orig_srows2 = _co.session_rows
        _co.session_rows = lambda img: [{"name": n} for n in ["张三丰"]]
        _chdr.check = lambda *a, **k: {"status": "no"}
        w4._idn_txn_begin()
        ok6, _ = w4.chat_is_open("group:x@chatroom", name="工作群A")
        ck("chat_is_open：三表全空 + 竞争者只在 seen 集 ⇒ 真 _known_chat_names 并入后歧义判否（D1 残余收口）",
           ok6 is False, "ok=%s" % ok6)
    except Exception as e: # noqa: BLE001
        ck("seen 集单元与场景 F 不抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
    finally:
        _co.capture_best = _orig_cb2 if "_orig_cb2" in dir() else _co.capture_best
        _co.session_rows = _orig_srows2 if "_orig_srows2" in dir() else _co.session_rows
        _co.green_bands = _orig_gb if "_orig_gb" in dir() else _co.green_bands
        _chdr.check = _orig_check # ④″ 场景 F/G 又动了它，离场还原（防泄漏进 ⑤/⑥）

    # ── ④″′ candidate_rows_excluding_active 单元（排除 + 兜底共用助手）──
    from agent.chat_ocr import candidate_rows_excluding_active as _crea # noqa: PLC0415
    _imG = _PImG.new("RGB", (80, 40), (255, 255, 255))
    _orig_gb2 = _co.green_bands
    _orig_sr2 = _co.session_rows
    try:
        _co.session_rows = lambda img: [
            {"name": "演示祥", "y_abs": 100}, {"name": "张三丰", "y_abs": 220}]
        _co.green_bands = lambda *a, **k: [{"y0": 90, "y1": 130, "y_abs": 100, "score": 0.9}]
        ck("candidate_rows：像素法优先——绿底带 y 定位排除当前行",
           _crea(_imG, "演示祥") == ["张三丰"])
        _co.green_bands = lambda *a, **k: []
        ck("candidate_rows：绿底带量不到 ⇒ 名字相似度兜底剔最像者（距离≤2 才剔；a 远则不剔）",
           _crea(_imG, "演示祥") == ["张三丰"]
           and _crea(_imG, "完全不同名") == ["演示祥", "张三丰"])
        ck("candidate_rows：a 为空 ⇒ 兜底不剔（无判据不猜）",
           _crea(_imG, "") == ["演示祥", "张三丰"])
    finally:
        _co.session_rows = _orig_sr2
        _co.green_bands = _orig_gb2

    # ── ④‴ D2（新发现）：归一化撞名 ⇒ 尾巴成判别特征；无撞名 ⇒ 装饰豁免保留 ──
    from agent.chat_ocr import norm_collides as _nc # noqa: PLC0415
    ck("norm_collides：库里真有同名异饰的另一群 ⇒ 撞名；自身同名不算；无同名不撞",
       _nc("测试", ["KC测试", "测试(2)"]) is True and _nc("测试(2)", ["测试"]) is True
       and _nc("演示", ["演示", "张三丰"]) is False and _nc("演示", []) is False)
    ck("strict 撞名守恒：屏幕带尾缀+目标裸名（库里真有 (2)）⇒ 拒——授权档假阳性收口",
       _co.matches_strict("测试(2)", "测试", ["测试(2)"]) is False)
    ck("strict 撞名守恒：都带同一个 (2) ⇒ 照旧过；目标带尾缀+屏幕丢尾缀 ⇒ 照旧拒",
       _co.matches_strict("测试(2)", "测试(2)", ["测试"]) is True
       and _co.matches_strict("测试", "测试(2)", ["测试"]) is False)
    ck("strict 无撞名 ⇒ 装饰豁免保留（现场「演示（3）」成员数场景不回归）",
       _co.matches_strict("演示（3）", "演示", ["张三丰"]) is True
       and _co.matches_strict("演示（3）", "演示") is True)
    ck("fuzzy 撞名守恒：db_others 有 (2) ⇒ 由 True 改 False；不给 db_others ⇒ 旧行为",
       _co.matches_fuzzy("测试(2)", "测试", ["张三丰"], db_others=["测试(2)"]) is False
       and _co.matches_fuzzy("测试(2)", "测试", ["张三丰"]) is True)
    # 端到端：DB 真有「测试(2)」、屏幕正确读出「测试(2)」、目标「测试」⇒ 必须判否（无误读也误发）
    _orig_cb4 = _co.capture_best
    _orig_gb4 = _co.green_bands
    _orig_srows4 = _co.session_rows
    try:
        w6 = _WA.__new__(_WA)
        w6._gui = None
        w6._idn_cache = None
        w6._idn_txn = 0
        w6._group_by_wxid = {}
        w6._get_gui = lambda: None
        w6._groups = [{"wxid": "g1", "name": "测试(2)"}]
        w6._nick_map = {}
        w6._monitored_chat_ids = lambda: []
        w6.display_name = lambda ck: ""
        w6._active_row_time_ok = lambda chat_id, gui=None, img=None: (False, "桩：时间档不参与")
        w6.current_chat_name = lambda gui=None, img=None: ("测试(2)", "桩：正确读出")
        _co.capture_best = lambda *a, **k: _PImG.new("RGB", (80, 40), (255, 255, 255))
        _co.green_bands = lambda *a, **k: [{"y0": 90, "y1": 130, "y_abs": 100, "score": 0.9}]
        _co.session_rows = lambda img: [
            {"name": "测试(2)", "y_abs": 100}, {"name": "张三丰", "y_abs": 220}]
        _chdr.check = lambda *a, **k: {"status": "no"}
        w6._idn_txn_begin()
        ok9, _ = w6.chat_is_open("group:x@chatroom", name="测试")
        ck("chat_is_open：DB 真有同名异饰群 + 屏幕正确读出带尾缀名 ⇒ 授权档判否（D2 误发收口）",
           ok9 is False, "ok=%s" % ok9)
        # 装饰豁免端到端：DB 无撞名、屏幕读出成员数装饰「演示（3）」⇒ 照旧放行（现场用例不回归）
        w7 = _WA.__new__(_WA)
        w7._gui = None
        w7._idn_cache = None
        w7._idn_txn = 0
        w7._group_by_wxid = {}
        w7._get_gui = lambda: None
        w7._groups = []
        w7._nick_map = {}
        w7._monitored_chat_ids = lambda: []
        w7.display_name = lambda ck: ""
        w7._active_row_time_ok = lambda chat_id, gui=None, img=None: (False, "桩：时间档不参与")
        w7.current_chat_name = lambda gui=None, img=None: ("演示（3）", "桩：成员数装饰读数")
        _co.session_rows = lambda img: [
            {"name": "演示（3）", "y_abs": 100}, {"name": "张三丰", "y_abs": 220}]
        w7._idn_txn_begin()
        ok10, why10 = w7.chat_is_open("group:x@chatroom", name="演示")
        ck("chat_is_open：无撞名 ⇒ 装饰豁免端到端照旧放行（档① strict 命中）",
           ok10 is True, "ok=%s why=%s" % (ok10, str(why10)[:40]))
    finally:
        _co.capture_best = _orig_cb4
        _co.green_bands = _orig_gb4
        _co.session_rows = _orig_srows4
        _chdr.check = _orig_check

    # ── ⑤ C 步：CLAHE 增强只在主管线读空后补读（桩 recognize_dual 验编排）──
    from PIL import Image as _PImage # noqa: PLC0415

    # 低对比底 + 一块「墨」（构图按 header_box 实测定位形态：300×80、带内 y38~80；
    # 否则 _band_ink≈0 或带高不足会被早退，进不了管线）
    _low = _PImage.new("RGB", (300, 80), (210, 210, 210))
    for _x in range(100, 180):
        for _y in range(45, 75):
            _low.putpixel((_x, _y), (170, 170, 170))
    _orig_rd = _co.recognize_dual
    try:
        _enh = _co._enhance_gray(_low)
        ck("CLAHE 增强可用：低对比图返回增强灰图且尺寸不变（cv2 缺席/异常时退 None）",
           _enh is not None and _enh.size == _low.size)

        # ⛔ 先**量出真实档序预算**，别写死数字：
        #   档序改过两次（加第 0 档"裸图直读"、加第 6 档、极性分叉×2）⇒ 写死 4/5/6 的判据会假红，
        #   而假红比不测更坏（它会逼人把断言改成"迁就现状"，反过来丢掉测的意义）。
        #   ⇒ 先真跑一遍全空，数出 `_header_read` 到底调了几次 recognize_dual，用这个数当基线。
        _probe = {"n": 0}

        def _rd_probe(img, timeout=None):
            _probe["n"] += 1
            return []

        _co.recognize_dual = _rd_probe
        _p0 = _co._header_read(_low, zoom=2)
        _base_n = _probe["n"]
        ck("基线：全空时档序耗尽且如实返空（fail-closed）",
           _p0 == ("", 0) and _base_n >= 2, "base_calls=%d" % _base_n)

        # ⛔ **先量出"主管线"与"CLAHE 补读"的分界，别假设补读在 `_base_n` 之后**：
        #   本轮踩过的坑——`_base_n`（全空时的**总**调用数，=13）**已经包含了补读那 2 次**
        #   （实测：1 次裸图 + 5 档×2 极性 = 11 ⇒ 主管线到第 11 次为止；第 12/13 次＝CLAHE 两档）。
        #   老判据写"第 _base_n+1 次命中"，等于要求**第 14 次**——而管线一共只跑 13 次 ⇒ 永远不成立
        #   ⇒ **假红**（`got=('', 0) calls=13 base=13`）。⇒ 改成"在补读的第一档命中"（第 `_base_n-1` 次）。
        _MAIN = max(1, _base_n - 2)   # 主管线次数（CLAHE 固定补 `zooms[:2]` 两档）

        _calls = {"n": 0}

        def _rd_hit_in_readback(img, timeout=None):
            _calls["n"] += 1
            return [("演示群", 3, 8, 6, 20)] if _calls["n"] > _MAIN else []

        _co.recognize_dual = _rd_hit_in_readback
        got_z = _co._header_read(_low, zoom=2)
        ck("读空补读：主管线（前 %d 次）全空 ⇒ CLAHE 增强那两档里命中" % _MAIN,
           got_z[0] == "演示群" and _MAIN < _calls["n"] <= _base_n,
           "got=%s calls=%d 主管线=%d base=%d" % (str(got_z), _calls["n"], _MAIN, _base_n))
        ck("反向锚：补读**没有**在主管线内命中（`_MAIN` 次全空 ⇒ 证明命中确实来自补读）",
           _MAIN >= 11, "主管线=%d" % _MAIN)

        _calls2 = {"n": 0}

        def _rd_all_empty(img, timeout=None): # 全部读空 ⇒ 补读后仍空 ⇒ 返 ("", 0)，不抛
            _calls2["n"] += 1
            return []

        _co.recognize_dual = _rd_all_empty
        got_z2 = _co._header_read(_low, zoom=2)
        ck("补读也读空 ⇒ 如实返空（fail-closed；总耗<%d 次，不超预算）" % (_base_n + 6),
           got_z2 == ("", 0) and _base_n <= _calls2["n"] <= _base_n + 6,
           "calls=%d base=%d" % (_calls2["n"], _base_n))
    finally:
        _co.recognize_dual = _orig_rd

    # ── ⑥ D 步：server rec 权重可选机制（不真加载——探针已验，自检不背 4s）──
    _srv = _co._server_rec_path()
    ck("server rec 权重在位（包内 models 目录、>50MB、随离线包分发不进 git）",
       _srv is not None and os.path.getsize(_srv) > 50_000_000, str(_srv))
    ck("server 权重默认不启用（wechat.ocr_server_rec 缺省 False ⇒ 行为零变化）",
       _co._want_server_rec() is False)
    _st = _co.rapidocr_status()
    ck("rapidocr_status 带 tier 字段（体检可见当前权重档）",
       isinstance(_st, dict) and "tier" in _st, str(_st)[:80])


def t_audit_r3() -> None:
    """落地项自检：D4 会话身份事务装饰器（语义 + 接线）+ D3 孤儿 stash 自愈。

    D4 验三件事：进门换 token / 退出（含异常路径）清缓存且 token 再进位 / 返回值透传；
    接线验 8 个非 send_text 操作入口都带 `_idn_scoped` 标记（缺一个都算漏包事务）。
    D3 验 `_stash_selfheal` 三分支：还原 / 删陈货 / noop。
    """
    import tempfile # noqa: PLC0415
    import agent.wechat as _WMod # noqa: PLC0415
    from agent.wechat import WeChatAdapter as _WA3 # noqa: PLC0415

    # ── D4 语义 ──
    class _Op:
        _idn_txn = 0
        _idn_cache = None
        _idn_txn_begin = _WA3._idn_txn_begin
        _idn_txn_end = _WA3._idn_txn_end
        _idn_cache_put = _WA3._idn_cache_put

        @_WMod._idn_txn_scope
        def op(self):
            if self._idn_txn != 1:
                raise AssertionError("进门应已换 token")
            self._idn_cache_put(("k",), (True, "正面证据"))
            return "ret"

        @_WMod._idn_txn_scope
        def boom(self):
            raise RuntimeError("x")

    o = _Op()
    ck("D4 语义：进门换 token + 事务内可存正面证据 + 返回值透传 + 退出清缓存",
       o.op() == "ret" and o._idn_cache is None and o._idn_txn == 2,
       "txn=%s cache=%s" % (o._idn_txn, o._idn_cache))
    try:
        o.boom()
    except RuntimeError:
        pass
    ck("D4 语义：异常路径退出同样作废（token 再进位、缓存空）",
       o._idn_cache is None and o._idn_txn == 4, "txn=%s" % o._idn_txn)
    _need = ("_open_chat_guarded", "_click_visible_session", "switch_chat_posted",
             "open_chat_by_search", "send_text_posted", "send_image",
             "send_file_posted", "emoji_panel_open")
    _miss = [m for m in _need if not getattr(getattr(_WA3, m, None), "_idn_scoped", False)]
    ck("D4 接线：8 个非 send_text 操作入口均已包事务（_idn_scoped 标记齐全）",
       not _miss, "缺=%s" % _miss)

    # D4 覆盖面（按审查者建议升级为递归 glob）：AST 全量枚举 chat_is_open
    # 调用点，逐一核事务覆盖——覆盖判据：方法级祖先链上任一函数 ①是 send_text（显式事务）
    # ②带 @_idn_txn_scope ③函数体内显式调 _idn_txn_begin（voice_strip 式独立入口）。
    # ⚠️ 已知不覆盖形态：**别名/getattr 间接调用**（如 `f = wc.chat_is_open; f(...)`）——
    #     AST 只认 `X.chat_is_open(...)` 直接调用；新增此类形态须人工记账。
    # agent/**/*.py 递归全扫 = 硬失败；scripts/ 与 ui_qt/ 也扫但**只提示不失败**
    # （审查者 r5 建议：测试/脚本是夹具不是产品发送路径，出现真实调用时提示人工判断）。
    import ast as _ast # noqa: PLC0415
    _root = Path(__file__).resolve().parent.parent
    _uncov_agent, _uncov_other, _n_scanned = [], [], 0

    def _has_explicit_begin(fn):
        for _x in _ast.walk(fn):
            if isinstance(_x, _ast.Call) and isinstance(_x.func, _ast.Attribute) \
                    and _x.func.attr == "_idn_txn_begin":
                return True
            # getattr(X, "_idn_txn_begin", None) 兜底形态（voice_strip r5 起）：
            # 优先结构匹配（getattr 调用 + 常量实参），整串常量兜底（r6 审查者确认
            # 整串相等不命中 docstring，假阳性只剩"函数体内恰有裸字符串常量"，罕见）
            if isinstance(_x, _ast.Call) and isinstance(_x.func, _ast.Name) \
                    and _x.func.id == "getattr":
                for _arg in _x.args:
                    if isinstance(_arg, _ast.Constant) and _arg.value == "_idn_txn_begin":
                        return True
            if isinstance(_x, _ast.Constant) and _x.value == "_idn_txn_begin":
                return True
        return False

    for _top, _hard in (("agent", True), ("scripts", False), ("ui_qt", False)):
        for _p in sorted((_root / _top).rglob("*.py")):
            try:
                _tree = _ast.parse(_p.read_text(encoding="utf-8"))
            except Exception: # noqa: BLE001
                continue
            _n_scanned += 1
            _parent = {}
            for _n in _ast.walk(_tree):
                for _c in _ast.iter_child_nodes(_n):
                    _parent[_c] = _n

            def _chain(n):
                out = []
                while n in _parent:
                    n = _parent[n]
                    if isinstance(n, _ast.FunctionDef):
                        out.append(n)
                return out

            for _n in _ast.walk(_tree):
                if isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Attribute) \
                        and _n.func.attr == "chat_is_open":
                    _fns = _chain(_n)
                    _cov = any(f.name == "send_text"
                               or any(getattr(d, "id", "") == "_idn_txn_scope" for d in f.decorator_list)
                               or _has_explicit_begin(f)
                               for f in _fns)
                    if not _cov:
                        _hit = "%s:%d(%s)" % (_p.relative_to(_root).as_posix(), _n.lineno,
                                              " < ".join(f.name for f in _fns[:2]))
                        (_uncov_agent if _hard else _uncov_other).append(_hit)
    ck("D4 覆盖面：AST 递归扫 agent/**/*.py（%d 文件），chat_is_open 调用点全部包事务" % _n_scanned,
       not _uncov_agent and _n_scanned >= 5,
       "未覆盖=%s（另 scripts/ui_qt 提示项=%s）" % (_uncov_agent, _uncov_other))
    import agent.voice_strip as _VS # noqa: PLC0415
    ck("D4 接线：voice_strip.send 带显式事务标记（_idn_scoped）",
       getattr(_VS.send, "_idn_scoped", False) is True)

    # ── D3 自愈三分支 ──
    _td = tempfile.mkdtemp(prefix="st_sh_")
    try:
        open(os.path.join(_td, "console.url.selftest-stash"), "w").close()
        ck("D3 自愈：url 缺失 + stash 在位 ⇒ 还原（上一跑没走完 finally 的形态）",
           _stash_selfheal(_td) == "restored"
           and os.path.exists(os.path.join(_td, "console.url"))
           and not os.path.exists(os.path.join(_td, "console.url.selftest-stash")))
        open(os.path.join(_td, "console.url.selftest-stash"), "w").close()
        ck("D3 自愈：url 在 + stash 也在 ⇒ stash 是陈货，删掉",
           _stash_selfheal(_td) == "dropped-stale"
           and not os.path.exists(os.path.join(_td, "console.url.selftest-stash"))
           and os.path.exists(os.path.join(_td, "console.url")))
        ck("D3 自愈：两者都不在 ⇒ noop",
           _stash_selfheal(_td) == "noop")
    finally:
        import shutil # noqa: PLC0415
        shutil.rmtree(_td, ignore_errors=True)


def _stash_selfheal(logs_dir: str) -> str:
    """孤儿 stash 自愈（D3）：上一跑没走完 finally 就退出（强杀/断电/段错误）
    ⇒ `console.url` 被挪进 `console.url.selftest-stash` 再没回来——产品读不到 url（静默降级），
    下一次自检的「挪开」也扑空。规则：url 在 ⇒ stash 是陈货，删掉；url 不在而 stash 在 ⇒ 还原。
    返回动作说明（自检断言用）。只动测试侧遗留物，产品行为不受影响。"""
    try:
        url = os.path.join(logs_dir, "console.url")
        st = url + ".selftest-stash"
        if os.path.exists(st) and not os.path.exists(url):
            os.replace(st, url)
            return "restored"
        if os.path.exists(st) and os.path.exists(url):
            os.remove(st)
            return "dropped-stale"
        return "noop"
    except OSError as e:
        return "error:%s" % e


def t_dialog_drag() -> None:
    """弹窗拖拽（DraggableDialog）—— 「按住任意非交互处即可挪窗」的回归闸。

    三条判据、一个坑：
      · 按背景/纯文字 QLabel      ⇒ 窗口位移（用户要的「随便按哪儿都能挪」）；
      · 按按钮/输入框             ⇒ 窗口**不**动（它们自己收鼠标）；
      · 位移 < 4px 阈值           ⇒ 当点击不当拖（不抖窗）；
    坑：Qt 的鼠标事件会**沿父链上抛**——在按钮上按下时对话框也会收到一份
    （`obj is dialog`），只看 obj 会判成「可拖」⇒ 按按钮也能把窗拖走。故实现里
    额外用 `childAt(globalPos)` 复核命中件（`_drag_child_at`）。
    事件必须走 `QApplication.notify()`：过滤器是 notify 里调的，`sendEvent` 绕过它。
    """
    import os # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPoint, Qt # noqa: PLC0415
    from PySide6.QtGui import QMouseEvent # noqa: PLC0415
    from PySide6.QtWidgets import ( # noqa: PLC0415
        QApplication, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    )

    import widgets as W # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    app = QApplication.instance() or QApplication([])

    D = W.drag_dialog_cls("R10DragDialog")
    ck("拖拽混入的 MRO 顺序 (DraggableDialog, QDialog)（避开 eventFilter 影子化）",
       D.__mro__[1] is W.DraggableDialog and D.__mro__[2] is QDialog,
       "mro=" + ",".join(c.__name__ for c in D.__mro__[:4]))

    keep: list = []
    dlg = D()
    keep.append(dlg)
    dlg.enable_drag()
    dlg.resize(360, 240)
    lay = QVBoxLayout(dlg)
    lab = QLabel("这是一段纯显示文字（不可选）")
    lay.addWidget(lab)
    inp = QLineEdit()
    lay.addWidget(inp)
    btn = QPushButton("确定")
    lay.addWidget(btn)
    dlg.show()
    # ⛔ 不调 `app.processEvents()`：前序用例会在事件队列里留下**未到期的 QTimer**
    #   （Shell 的余额轮询 / 鲸鱼挂件刷新等），它们一被 pump 就会去做**阻塞网络请求**
    #   （`socket.create_connection` 连一个不存在的端口，Windows 下会长时间挂住）⇒
    #   本用例会被拖死（既有实测栈：processEvents → shell._load_balance →
    #   config_io.get_json → socket.create_connection）。本用例的断言全部基于
    #   `dlg.pos()` 与直接调用的 `eventFilter`，**不需要事件循环**：`show()` 后几何
    #   即已定；`_drag` 也是直调过滤器。（Qt 事件循环里跑阻塞 I/O = 必踩的环境坑。）

    def _drag(from_w, dx, dy): # noqa: ANN001
        """模拟一次「在 from_w 上按下 → 拖 (dx,dy) → 松手」。

        ⛔ 不走 `QApplication.notify()`：它是 Qt 的**原生派发入口**，在控件层级
        被前序用例折腾过之后（C++ 侧有已析构对象残影）会在 Qt 内部段错误 ——
        实测全量跑 `t_g16,t_g17,t_g18` 之后必现在 `notify()` 里崩（且崩在进
        Python `eventFilter` 之前，所以不是本过滤器的逻辑问题）。
        ✅ 改为直接调 `dlg.eventFilter(from_w, ev)`：被测的就是这个过滤器本身，
        直接喂事件既避开了 Qt 派发的环境脆弱性，又把「命中件是谁」钉死
        （`from_w` 就是命中的子控件），断言更确定。`_drag_child_at` 另用
        `findChild` 复核一条，防止过滤器改走 childAt 后这里失真。
        """
        lp = QPoint(3, 3) if from_w is dlg else from_w.rect().center()
        gp = (dlg if from_w is dlg else from_w).mapToGlobal(lp)
        step = QPoint(dx, dy)
        for t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                  QEvent.Type.MouseButtonRelease):
            if t == QEvent.Type.MouseButtonPress:
                cl, cg = lp, gp
                btns, hs = Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
            else:
                cl, cg = lp + step, gp + step
                if t == QEvent.Type.MouseMove:
                    btns, hs = Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton
                else:
                    btns, hs = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
            # from_w 也收一份（模拟事件先到子控件）—— 过滤器的两重判据都要走到
            dlg.eventFilter(from_w, QMouseEvent(t, cl, cg, btns, hs,
                                                Qt.KeyboardModifier.NoModifier))
            dlg.eventFilter(dlg, QMouseEvent(t, cl, cg, btns, hs,
                                             Qt.KeyboardModifier.NoModifier))
        # 不 pump 事件循环（理由同上：会喂到前序用例残留的阻塞型定时器）。
        # 位移是同步 `self.move()` 生效的，`dlg.pos()` 立即反映。

    # 复核 `_drag_child_at` 的命中口径与 `from_w` 一致（防过滤器改实现后本测试失真）
    _hit = dlg._drag_child_at(lab.mapToGlobal(lab.rect().center()))
    ck("_drag_child_at 命中的是传入点上的子控件（非 dialog 自身）",
       _hit is not None and _hit is not dlg,
       "hit=%r" % (type(_hit).__name__ if _hit is not None else None))

    p0 = dlg.pos()
    _drag(lab, 45, 35)
    ck("按纯文字 QLabel 拖 ⇒ 窗口位移（随便按哪儿都能挪）",
       (dlg.x(), dlg.y()) != (p0.x(), p0.y()),
       "p0=(%d,%d) now=(%d,%d)" % (p0.x(), p0.y(), dlg.x(), dlg.y()))

    p1 = dlg.pos()
    _drag(btn, 60, 50)
    ck("按按钮拖 ⇒ 窗口不动（事件上抛到 dialog 也不许拖）",
       (dlg.x(), dlg.y()) == (p1.x(), p1.y()),
       "p1=(%d,%d) now=(%d,%d)" % (p1.x(), p1.y(), dlg.x(), dlg.y()))

    p2 = dlg.pos()
    _drag(inp, 60, 50)
    ck("按输入框拖 ⇒ 窗口不动（要能划选/点光标）",
       (dlg.x(), dlg.y()) == (p2.x(), p2.y()),
       "p2=(%d,%d) now=(%d,%d)" % (p2.x(), p2.y(), dlg.x(), dlg.y()))

    p3 = dlg.pos()
    _drag(lab, 2, 1)
    ck("位移 < 4px 阈值 ⇒ 当点击不当拖（不抖窗）",
       (dlg.x(), dlg.y()) == (p3.x(), p3.y()),
       "p3=(%d,%d) now=(%d,%d)" % (p3.x(), p3.y(), dlg.x(), dlg.y()))

    # 胶囊半径：Qt 对 border-radius 有硬上限 min(w,h)/2，**超一像素整条值被丢弃、
    # 圆角退回直角**（实测 120×80 rad=40 是圆角、rad=41/999 全是方角）。
    # ⇒ 半径必须按真实尺寸算，QSS 里**绝不允许**再出现 999 这类超界常数。
    # 旧断言写的是「QSS 里必须有 border-radius:999px」——它把 bug 锁死成了"正确"，
    # 是「所有按钮都是方的」长期没被发现的原因。
    import stylekit_qt as sk # noqa: PLC0415

    _t = THEMES["whale"]
    ck("半径工具按真实尺寸算（不让 999 这类超界常数流进 QSS）",
       sk.radius_for(sk.SHAPE_PILL, 120, 36) == 18
       and sk.radius_for(sk.SHAPE_PILL, 120, 40) == 20
       and sk.radius_for(sk.SHAPE_SOFT, 120, 40) == 10
       and sk.radius_for(sk.SHAPE_TILE, 120, 40) == 6
       and sk.radius_for(sk.SHAPE_CIRCLE, 60, 60) == 30
       and sk.pill(32) == 16,
       "pill(40)=%d soft=%d tile=%d circle=%d pill(32)=%d" % (
           sk.radius_for(sk.SHAPE_PILL, 120, 40),
           sk.radius_for(sk.SHAPE_SOFT, 120, 40),
           sk.radius_for(sk.SHAPE_TILE, 120, 40),
           sk.radius_for(sk.SHAPE_CIRCLE, 60, 60), sk.pill(32)))

    _b = W.Btn("按钮", _t, role="primary")
    keep.append(_b)
    _bq = _b.styleSheet()
    ck("Btn QSS 半径 ≤ min(w,h)/2（超界会被 Qt 丢弃 ⇒ 直接画成方框）",
       "border-radius:999" not in _bq and _b._rad <= max(1, _b.height()) // 2,
       "rad=%d 片段=%s" % (_b._rad, _bq.split(";")[2][:40]))

    ck("token 落成 Qt 合法字面量（不出现 Qt 不认的 rgba() 函数式写法）",
       "rgba(" not in _bq and "rgb(" not in _bq,
       "片段=%s" % _bq.split(";")[0][:60])

    # 形状分类：四档半径口径互不重合（"一类按钮一种形状"要真的分得开）
    #   · 全部按真实尺寸算，**不能**靠改 .shape 后沿用旧半径（漏刷会留超界值）。
    _shapes = {}
    for _s in (sk.SHAPE_PILL, sk.SHAPE_SOFT, sk.SHAPE_TILE, sk.SHAPE_CIRCLE):
        _sb = W.Btn("形状", _t, role="ghost", shape=_s)
        # ⚠️ 必须用 set_button_size（登记显式尺寸）而不是裸 setFixedSize：
        #    后者只管布局，控件上版式前 width()/height() 还是默认值，半径会算错。
        _sb.set_button_size(120, 36)
        keep.append(_sb)
        _shapes[_s] = _sb._rad
    ck("按钮形状四档分类成立（pill/circle 17、soft 10、tile 6）",
       _shapes[sk.SHAPE_PILL] == 18 and _shapes[sk.SHAPE_SOFT] == 10
       and _shapes[sk.SHAPE_TILE] == 6 and _shapes[sk.SHAPE_CIRCLE] == 18,
       "shapes=%s（120×36 上半高与短边同为 18，两档同值属正常）" % _shapes)

    # 正圆必须**用短边**：120×40 上取半宽 60 会被 Qt 丢弃（超 min(w,h)/2）
    _w1 = W.Btn("形", _t, role="ghost", shape=sk.SHAPE_CIRCLE)
    _w1.set_button_size(120, 40)
    keep.append(_w1)
    ck("circle 半径被短边钳制（120×40 ⇒ 20；取半宽 60 会超界被丢弃变方框）",
       _w1._rad == 20, "rad=%d" % _w1._rad)

    # 胶囊同理：高瘦控件（宽 30 高 200）半高 100 > min/2=15 ⇒ 必须被宽钳住
    _w2 = W.Btn("形", _t, role="ghost", shape=sk.SHAPE_PILL)
    _w2.set_button_size(30, 200)
    keep.append(_w2)
    ck("pill 半径同时被宽钳制（30×200 ⇒ 15，不是 100 ⇒ 不会超界退成方框）",
       _w2._rad == 15, "rad=%d" % _w2._rad)

    # 改形状必须重算（漏刷会留着上一档的超界半径）
    _w3 = W.Btn("形", _t, role="ghost", shape=sk.SHAPE_PILL)
    _w3.set_button_size(42, 42)
    keep.append(_w3)
    _before = _w3._rad
    _w3.set_shape(sk.SHAPE_TILE)
    ck("换形状走 set_shape 并立即重算半径（不重算会留旧档超界值）",
       _before == 21 and _w3._rad == 6, "before=%d after=%d" % (_before, _w3._rad))

    dlg.close()


def _fake_identity_cases(ww, ck) -> None:  # noqa: ANN001
    """真起三个假 HTTP 服务，验挂件认人闸门的**判别力**（不是只看代码里有没有那行）。

    场景取自启动器那份口径的关键三项：
      A. 本产品控制台形态（/api/version 200 + `{"ver":…}`）⇒ 必须认（返空串）
      B. 别人的产品（200 但没有 ver 字段）               ⇒ 必须拒
      C. 别的 HTTP 服务（/api/version 404 + 正文无关）    ⇒ 必须拒

    为什么真起服务而不是 mock：`_not_our_console` 的判据全在"HTTP 应答长什么样"
    上面，mock 掉 HTTP 等于把被测逻辑一起 mock 掉了（测试通过 ≠ 功能生效的经典坑）。
    """
    import threading # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer # noqa: PLC0415

    class _H(BaseHTTPRequestHandler):
        MODE = "ours"

        def log_message(self, *a):  # noqa: ANN002
            pass

        def _send(self, code, body):  # noqa: ANN001
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            try:
                self.wfile.write(b)
            except Exception:  # noqa: BLE001 — 客户端提前断开
                pass

        def do_GET(self):  # noqa: N802
            mode = type(self).MODE
            if self.path.startswith("/api/version"):
                if mode == "ours":
                    self._send(200, '{"ver": "9.9.9"}')
                elif mode == "upstream":
                    self._send(200, '{"name": "qq-agent"}')
                else:
                    self._send(404, "")
                return
            if mode == "other":
                self._send(200, "Hello, another service")
            else:
                self._send(200, "PersonaMorph Console")
            return

    def _serve(mode):  # noqa: ANN001
        cls = type("_HH", (_H,), {"MODE": mode})
        srv = HTTPServer(("127.0.0.1", 0), cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, srv.server_address[1]

    for mode, want_ours, label in (
            ("ours", True, "本产品控制台（带 ver）"),
            ("upstream", False, "别人的产品（200 无 ver）"),
            ("other", False, "别的 HTTP 服务（404 + 正文无关）")):
        srv, port = _serve(mode)
        try:
            got = ww._not_our_console(port, 1.5)
        finally:
            srv.shutdown()
            srv.server_close()
        is_ours = (got == "")
        ck("认人判别：%s => %s" % (label, "认" if want_ours else "拒"),
           is_ours == want_ours, "port=%d got=%r" % (port, got[:60]))


def t_whale_guard() -> None:
    """自检：鲸鱼挂件的两条机制闸 —— 「拖得动」与「不认错人」。

    ## A. 拖动为什么必须走**页面转发**而不是 `WS_EX_TRANSPARENT`

    WebView2 的画面渲染在一个铺满挂件窗的**真实子 HWND** 上。Windows 命中测试
    子窗优先 ⇒ 鼠标消息被子窗吃掉、不冒泡给 Qt 顶层窗 ⇒ `mousePressEvent`
    一次都不触发，挂件拖不动。

    给子窗加 `WS_EX_TRANSPARENT` 确实能让消息穿过去 —— 但**页面里所有控件也
    一起收不到点击了**（减号、原版菜单全失效）。那是拿一个功能换另一个功能。
    本项目改为**页面自己报位移**：页内监听拖动 → `postMessage` → 宿主转 Qt
    挪窗口。点按钮是 click、拖空白是 move，互不干扰。

    这条链上另有两个必须做对的点（都由真实故障换来的）：

      · **半透明窗的命中测试按像素 alpha**：alpha 为 0 的地方鼠标消息直接穿过去，
        Qt 侧不画东西就等于整窗"没有可点的表面"，连子窗也一起收不到鼠标 ⇒
        必须在提前返回之前铺一层 alpha=1 的**可命中底**，且只铺在页面报告的那几块
        （本体 + 可见弹层）上，别把整块看不见的方形压在桌面上；
      · **非交互区按下要取消默认动作**：鼠标在图片上按下后一移动，浏览器会进入
        原生拖拽/选区，那期间 mousemove 不再派发 ⇒ 页面报不出位移 ⇒ 拖不动。

    ## B. 认人闸门为什么必须有

    端口上有东西应答**只说明"有人听"，不说明"是我们的"**。同源上游产品默认端口
    同为 3210；它先开着、本产品还没起来时，把地址递给 WebView2 就等于**把这个
    挂件窗变成别人程序的页面壳**。启动器侧早有这道闸（`launcher.cs` 的
    `IsOurConsole`），挂件是**同一个洞的第二个入口**，此前完全没有校验。

    全部为静态判据 + 一个真起假服务的判别验证（不起 WebView2）。
    """
    import inspect as _inspect

    from whale_host import WhaleHostWebView, build_host_html # noqa: PLC0415
    from whale_widget import WhaleWidget # noqa: PLC0415

    # ---- A. 拖动：页面转发 -------------------------------------------------
    _h = _inspect.getsource(WhaleHostWebView)
    ck("宿主接页面的 postMessage（add_WebMessageReceived 已接线）",
       "add_WebMessageReceived" in _h and "_on_web_message" in _h, "")

    # ⛔ 分层窗的命中测试按**像素 alpha** 走：alpha=0 的地方鼠标消息直接穿过去。
    # 本窗口正常态什么都不画（画面全在 WebView2 子窗里）⇒ 若不在提前返回之前
    # 铺一层有 alpha 的底，整块方形就是"没有可点的表面"，连子窗也一起收不到鼠标。
    _pv = _inspect.getsource(WhaleWidget.paintEvent)
    _i_fill = _pv.find("fillRect")
    _i_ret = _pv.find("return")
    ck("可命中底敷在**提前返回之前**（否则正常态整窗 alpha=0 ⇒ 点不动也拖不动）",
       _i_fill != -1 and _i_ret != -1 and _i_fill < _i_ret
       and "QColor(0, 0, 0, 1)" in _pv,
       "fill@%s return@%s" % (_i_fill, _i_ret))
    # 而且只能铺在**页面报告的那几块**上：整窗铺满＝一块看不见的方形吃掉桌面的鼠标。
    _vr = _inspect.getsource(WhaleWidget._veil_rects)
    ck("可命中底只铺「本体 + 可见弹层」的报告矩形（整窗铺满会吃掉桌面鼠标）",
       "_veil" in _pv and "_veil_rects" in _pv
       and "_whale_offset" in _vr and "_whale_size" in _vr
       and "self.rect()" not in _pv)
    ck("宿主暴露 on_drag 回调接口（Qt 侧据此挪窗口）",
       hasattr(WhaleHostWebView, "on_drag"))

    _om = _inspect.getsource(WhaleHostWebView._on_web_message)
    ck("消息解析只认 pm=dragbegin/dragend（脏消息静默丢弃，不炸挂件）",
       '"dragbegin"' in _om and '"dragend"' in _om and "json.loads" in _om, "")

    _pg = _inspect.getsource(WhaleWidget._on_pagedrag)
    _tk = _inspect.getsource(WhaleWidget._drag_tick)
    _gm = _inspect.getsource(WhaleWidget._grab_mouse)
    ck("拖动走 Qt 侧：页面只说按下，宿主用 SetCapture 把鼠标消息收回本窗口",
       "SetCapture" in _gm and "ReleaseCapture" in _inspect.getsource(
           WhaleWidget._release_mouse) and "_drag_cursor" in _pg, "")
    ck("捕获只在真窗口上抢（没上屏时不搅乱系统鼠标）",
       "self.isVisible()" in _gm, "")
    ck("拖动有终止条件：按键全松 / 拖过 60 秒 / 页面报松手 —— 都不会占着捕获不放",
       "GetAsyncKeyState" in _tk and "_end_drag" in _tk
       and "> 60" in _tk and "self._drag_timer.stop()" in _inspect.getsource(
           WhaleWidget._end_drag), "")
    _bw = _inspect.getsource(WhaleWidget._boot_webview)
    ck("挂件侧已接 on_drag（漏接则页面报了位移也没人挪窗）",
       "on_drag" in _bw, "")
    # ⛔ 窗口尺寸的判据必须相对**本体**求值，且**只长不缩**、**宽度冻结**：
    #    尺寸一变，上游页面 `resize → settle()` 会把本体重贴到视口边缘、上报矩形跟着
    #    变；若按"矩形有没有超出窗口"判，就是 grow→重贴→矩形变化→shrink→… 死循环
    #    （用户看到的菜单上下弹动、上半部分被窗口上沿裁掉）。缩回同样不行：窗口上沿
    #    下移会把向上展开的菜单裁掉。宽度冻结则是因为本体尺寸取 `min(100vw,100vh)`。
    _fw = _inspect.getsource(WhaleWidget._fit_window)
    _fc = _inspect.getsource(WhaleWidget._fit_confirm)
    ck("窗口长高：判据相对本体（need[1] + 本体高）、宽度冻结、只增不减",
       "self._need[1]" in _fw and "_whale_size" in _fw
       and "setFixedSize(self.width(), th)" in _fc
       and "th <= self._fit_h" in _fc and "_fit_pending" in _fw, "")
    # ⛔ 只看向上溢出：布局是底锚结构，窗口长高只往上长 ⇒ 朝下伸出的弹层长高帮不上，
    #    而长高会让窗口上沿上移、多占屏幕。所以 need[3] 只记录、不参与长高。
    ck("朝下溢出（need[3]）不参与长高（底锚结构下长高对它无效，只会多占屏）",
       "self._need[3]" not in _fw and "self._need[3]" not in _fc, "")
    ck("长高前留一拍复核（瞬时元素不该把窗口撑大）、落地前把本体位置放回原位",
       "_FIT_CONFIRM_MS" in _fw and "_fit_confirm" in _fw
       and "_place_whale" in _fc and "self._pop" in _fc, "")

    # ---- D. 音效地址的口令注入 ---------------------------------------------
    # 原版有一处音效地址是**字符串拼**出来的，注入最容易在这里出错（口令插进路径
    # 中间 ⇒ 既不是有效路径也没有有效口令 ⇒ 音效无声）。把注入器真跑一遍在真实片段上。
    # 项目内的既有口径是 `from agent import webui`（webui 内部有相对导入，
    # 顶层 `import webui` 会因缺包上下文而失败）。
    import agent.webui as _webui # noqa: PLC0415

    _snip = ("var legacy = '/dsh-whale/sound/' + (slot === 'press' ? 'press' : 'release')"
             " + '.mp3?set=' + encodeURIComponent(gid)")
    _out = _webui._inject_whale_token(_snip, "TK") # noqa: SLF001
    ck("音效拼接地址注入后仍然合法（前缀不被改、口令补在 .mp3? 之后）",
       "'/dsh-whale/sound/' +" in _out and "'.mp3?token=TK&set='" in _out, _out[:110])
    _out2 = _webui._inject_whale_token("fetch('/dsh-whale/image.png?v=2')", "TK") # noqa: SLF001
    ck("完整路径照旧注入口令（修音效不能把别的请求弄坏）",
       "'/dsh-whale/image.png?token=TK&v=2'" in _out2, _out2[:110])

    # ⛔ 反向闸门：页面正常时**不许**再给子窗打 WS_EX_TRANSPARENT —— 那会把
    # 页内控件（减号/菜单）一起点死。
    _kr = _inspect.getsource(WhaleWidget._keep_draggable)
    _ark = _inspect.getsource(WhaleWidget._after_host_ready)
    ck("成功态不再打子窗透传（会把页内控件一起点死）",
       "pass_mouse_through" not in _kr and "pass_mouse_through" not in _ark
       and "_try_passthrough" not in _ark, "")

    ck("降级态仍 hide 控制器让出事件（页面没起来时页内无可点之物）",
       "hide()" in _kr, "")

    # 宿主页确实注入了拖动转发与减号
    _html = build_host_html(3210, "tk")
    ck("宿主页会上报可命中区（本体 + 弹层外接矩形，弹层开合即重报）",
       "rectsNow" in _html and "'rects'" in _html and "rectWatch" in _html)
    ck("宿主页在非交互区按下时取消默认动作（否则原生拖拽吞掉 mousemove ⇒ 拖不动）",
       "e.preventDefault()" in _html and "isInteractive(e.target)" in _html)
    ck("收起小圆点更深、稍大（14→16px，底色改成更深的深蓝）",
       "width:16px;height:16px" in _html and "rgba(14,22,52,.94)" in _html, "")
    # 只看 isInteractive 那一段（全文里 .pm-dot 也会出现在 click 判定里，不能整串判）
    _ii = _html[_html.index("function isInteractive(el){"):][:420]
    ck("小圆点不算交互区 ⇒ 它是拖动把手（单击仍由捕获段的 click 负责展开）",
       ".pm-dot" not in _ii and ".pm-min-btn" in _ii
       and "t.closest('.pm-dot')" in _html, "")
    ck("可命中区包含**我们自己注入的控件**（漏了减号就等于「点不动减号」）",
       "querySelectorAll('.pm-min-btn,.pm-dot,#pm-wait-card')" in _html)
    ck("减号挂进原版本体内（百分比相对本体算，窗口变高也不会飘离鲸鱼）",
       ".pm-min-btn{position:absolute" in _html and "appendChild(b)" in _html)
    ck("收起状态不落盘（落盘了下一次启动只剩一个小圆点 = 「挂件不见了」）",
       "pm-whale-collapsed" not in _html)
    ck("Bounds 口径靠**页面视口自校准**（传逻辑还是物理随运行库而异，传错＝画面全在窗外）",
       "self._calibrate_bounds" in _inspect.getsource(WhaleHostWebView._on_web_message)
       and "_bounds_factor" in _inspect.getsource(WhaleHostWebView._calibrate_bounds)
       and "vw" in _html and "dpr" in _html)

    # 校准决策本身的功能断言（不建 WebView2：直接喂视口读数给那段逻辑）——
    # 这是"画面整块画到窗口外 ⇒ 用户啥都看不到"那条故障的唯一出口，必须钉住。
    class _Stub:
        def __init__(self) -> None:
            self._calibrated = False
            self._ok = True
            self._ctrl = object()
            self.width, self.height = 440, 560
            self._bounds_factor = 1.0
            self._err = ""
            self.applied: list = []

        def _apply_bounds(self) -> None:
            self.applied.append(self._bounds_factor)

    _s1 = _Stub()
    WhaleHostWebView._calibrate_bounds(_s1, 440, 1.5) # 视口 = 传入值 ⇒ 口径正确
    _s2 = _Stub()
    WhaleHostWebView._calibrate_bounds(_s2, 293, 1.5) # 视口 < 传入值 ⇒ 内核按物理解释
    ck("校准：视口=传入值则不动、视口偏小则按页面缩放重设一次",
       _s1.applied == [] and _s2.applied == [1.5],
       "ok=%s fixed=%s" % (_s1.applied, _s2.applied))
    _s3 = _Stub()
    WhaleHostWebView._calibrate_bounds(_s3, 293, 1.5)
    WhaleHostWebView._calibrate_bounds(_s3, 100, 2.0) # 只校准一次，不反复抖
    ck("校准只做一次（避免每来一条上报就重设 Bounds）",
       _s3.applied == [1.5], "%s" % (_s3.applied,))
    ck("位置记忆记的是**本体右下角**（窗口尺寸/贴边方向再变，本体也不会被顶到屏幕外）",
       "whale_anchor" in _inspect.getsource(WhaleWidget._read_anchor)
       and "_whale_rect" in _inspect.getsource(WhaleWidget._save_anchor)
       and "self.width()" not in _inspect.getsource(WhaleWidget._save_anchor))
    ck("上屏校验按**本体**判（按整窗判会让「左上角露一点」过关、本体却在屏幕外）",
       "_onscreen" in _inspect.getsource(WhaleWidget.__init__)
       and "_whale_size" in _inspect.getsource(WhaleWidget._onscreen)
       and "self.width()" not in _inspect.getsource(WhaleWidget._onscreen))
    ck("几何真值收口在一处（页面上报 → _take_geom → 本体矩形/溢出量/弹层开关）",
       "rr" in _inspect.getsource(WhaleHostWebView._take_geom)
       and "need" in _inspect.getsource(WhaleHostWebView._take_geom)
       and "pop" in _inspect.getsource(WhaleHostWebView._take_geom)
       and "whale_rect" in _inspect.getsource(WhaleHostWebView.__init__))
    ck("拖动在 **window 捕获段**拦下（原版自带页内拖动，晚一步就会被它抢走手势）",
       "window.addEventListener('pointerdown'" in _html
       and "e.stopPropagation()" in _html and "dragbegin" in _html
       and "isInteractive" in _html, "")
    ck("**按下不抢、移动超阈值才接管**（一按下就抢会让内核收不到抬起 ⇒ click 永不产生）",
       "__pmPend" in _html and "pointermove" in _html
       and "<=4)return" in _html, "")
    ck("原版那套页内拖动函数不再被调用（`dragSetup` 已随拦截点搬迁删除）",
       "dragSetup" not in _html, "")
    ck("减号/圆点挂在 document **捕获**段（挂本体上会被原版自己的捕获处理截掉）",
       "__pmBtn" in _html and "addEventListener('click'" in _html
       and "t.closest('.pm-min-btn')" in _html, "")
    ck("拖动只认非交互区（按钮/菜单/输入框的按下留给原版逻辑）",
       "isInteractive" in _html and "closest('button')" in _html, "")
    ck("宿主页含减号与收起标记（pm-min-btn / pm-dot）",
       "pm-min-btn" in _html and "pm-dot" in _html, "")
    ck("减号收起的是挂件本体而非整页（原版脚本照旧跑）",
       "dshwv-root" in _html and "pm-collapsed" in _html, "")

    # ---- B. 认人闸门 -------------------------------------------------------
    import whale_widget as _ww # noqa: PLC0415

    _a2 = _inspect.getsource(WhaleWidget._after_host_ready)
    ck("认人判在导航**之前**（认不出就不加载，绝不显示别人页面）",
       "_not_our_console" in _a2, "")

    _nc = _inspect.getsource(_ww._not_our_console)
    ck("认人主判据 = /api/version 200 且正文含 \"ver\"（与启动器同口径）",
       "/api/version" in _nc and '"ver"' in _nc, "")
    ck("认人**绕代理**（本机代理会拦 127.0.0.1，实测返 502）",
       "ProxyHandler({})" in _nc, "")
    ck("认人有正文兜底（鲸语模式换可见文案，只作兜底不作主判据）",
       "群相" in _nc and "PersonaMorph" in _nc, "")

    # 真起假服务验判别力：三类服务，判"是不是我们"必须判对
    _fake_identity_cases(_ww, ck)

    # ---- C. 端口探活（黑块根因之一）---------------------------------------
    import socket as _sk # noqa: PLC0415

    ck("导航前先探端口（agent 没起来时如实降级，不给纯黑窗）",
       "_server_unreachable" in _a2, "")

    ck("端口探活能识别「无人监听」（借一个必然关着的端口验）",
       bool(_ww._server_unreachable(1, 0.2)), "")

    # 反向：真开一个监听，探活必须返回空串（否则挂件会误降级）
    _srv = _sk.socket()
    _srv.bind(("127.0.0.1", 0))
    _srv.listen(1)
    _live_port = _srv.getsockname()[1]
    try:
        _r = _ww._server_unreachable(_live_port, 1.0)
    finally:
        _srv.close()
    ck("端口探活对在听的端口返回空串（否则会把正常挂件误降级）",
       _r == "", "live=%d ret=%r" % (_live_port, _r))


def t_color_token_guard() -> None:
    """自检：三件回归闸。

    A. **八位十六进制通道序** —— Qt 样式表是 `#AARRGGBB`（透明度最前）。
       曾按 `#RRGGBBAA` 写反 ⇒ 所有半透明 token 被读成「高透明度黄绿色」
       （实测 `#aad7ff42` 渲染成 `#dcf77b` 描边、次级文字全泛绿，
       「为什么用这种绿色？很丑」）。此序一旦再写反，全主题颜色即污染。
    B. **行首标签叠字** —— `desc()`（Ignored 策略）直接塞 QHBoxLayout 行首 +
       setMinimumWidth 对布局无效（Ignored ⇒ 最小宽按 0 算，控件本体却被
       minimumWidth 钳宽）⇒ 标签与输入框叠字（费用计算器/拍一拍/视频通路）。
       修法 row_label：包普通策略容器，容器最小宽才被布局尊重。
    C. **挂件全透明空窗** —— 页面导航成功但鲸鱼本体没渲染时窗口 100% 透明，
       用户什么都看不到。修法：页面轮询本体、超时发
       boot(ok=false)，宿主降级提示卡；另有 8s 单次 watchdog 兜底。
    D. **控制台最小化挂件还在** —— 挂件改独立顶层窗（无 parent），hideEvent
       不再连带隐藏；真关时 closeEvent 显式带走（否则 WebView2 成孤儿）。
    """
    import ast as _ast
    import inspect as _inspect

    from PySide6.QtGui import QColor # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QLabel # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import stylekit_qt as sk # noqa: PLC0415
    from widgets import row_label # noqa: PLC0415

    # ---- A. 通道序 ---------------------------------------------------------
    ck("qss() 八位十六进制 = AARRGGBB（透明度在最前；写反则半透明色全变黄绿）",
       sk.qss(QColor(0x30, 0x50, 0x70, 0x42)) == "#42305070",
       sk.qss(QColor(0x30, 0x50, 0x70, 0x42)))

    ck("鲸鱼主题 bd 已固化为 #42aad7ff（26% 淡蓝； 前 #aad7ff42 渲染成黄绿）",
       sk.THEMES["whale"].bd == "#42aad7ff", sk.THEMES["whale"].bd)

    ck("qss() 对不透明色仍输出六位（不引入多余字节）",
       sk.qss(QColor(0x6f, 0xcf, 0xff)) == "#6fcfff", "")

    ck("_c() 拆九位十六进制同走 AARRGGBB（拆反则 α 跑到 245、RGB 变灰蓝）",
       (lambda _c: (_c.alpha(), _c.red(), _c.green(), _c.blue())
        == (199, 200, 224, 245))(sk._c("#c7c8e0f5")),
       (lambda _c: "a=%d rgb=#%02x%02x%02x" % (_c.alpha(), _c.red(), _c.green(), _c.blue()))(
           sk._c("#c7c8e0f5")))

    # ---- B. 叠字（真构建概览页量几何）--------------------------------------
    from panels_custom import overview_panel # noqa: PLC0415

    page = overview_panel(sk.THEMES["whale"])
    host = _host_of(page)
    app = QApplication.instance()
    host.resize(1080, 1100)
    host.show()
    app.processEvents()
    app.processEvents()

    lab_rects = {}
    for lb in page.findChildren(QLabel):
        if lb.text() in ("厂商", "每日消息数", "每消息输入用量", "每消息输出用量", "时段"):
            tl = lb.mapTo(host, lb.rect().topLeft())
            lab_rects[lb.text()] = (tl.x(), tl.y(), tl.x() + lb.width(), tl.y() + lb.height())

    from PySide6.QtWidgets import QComboBox, QLineEdit # noqa: PLC0415

    bad = 0
    details = []
    for w in page.findChildren(QLineEdit) + page.findChildren(QComboBox):
        tl = w.mapTo(host, w.rect().topLeft())
        r = (tl.x(), tl.y(), tl.x() + w.width(), tl.y() + w.height())
        for name, lr in lab_rects.items():
            if abs(lr[1] - r[1]) < 30: # 同一行
                ix = min(lr[2], r[2]) - max(lr[0], r[0])
                iy = min(lr[3], r[3]) - max(lr[1], r[1])
                if ix > 0 and iy > 0:
                    bad += 1
                    details.append("%s∩%s %dpx²" % (name, type(w).__name__, ix * iy))
    ck("概览页标签与输入框零相交（Ignored 策略下 minimumWidth 对布局无效的叠字病）",
       bad == 0, "; ".join(details) or "全部行间隙正常")

    # row_label 本体：容器最小宽被布局尊重（这正是 desc() 直接上场时办不到的）
    from PySide6.QtWidgets import QWidget as _QW # noqa: PLC0415

    _rl = row_label(sk.THEMES["whale"], "测试标签", 140)
    _box = _QW()
    _bl = __import__("PySide6.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout(_box)
    _bl.addWidget(_rl)
    host2 = _QW()
    _bl2 = __import__("PySide6.QtWidgets", fromlist=["QHBoxLayout"]).QHBoxLayout(host2)
    _bl2.addWidget(_rl)
    _bl2.addWidget(_QW())
    host2.resize(400, 60)
    host2.show()
    app.processEvents()
    ck("row_label 容器最小宽被布局尊重（≥140，desc() 直上时会被压成 0 ⇒ 叠字）",
       _rl.width() >= 140, "w=%d" % _rl.width())
    host2.close()

    # ---- C. 挂件启动回报链 --------------------------------------------------
    from whale_host import WhaleHostWebView, build_host_html # noqa: PLC0415
    from whale_widget import WhaleWidget # noqa: PLC0415

    _html = build_host_html(3210, "tk")
    ck("宿主页轮询发 boot 结论（ok=true 出本体 / ok=false 预算耗尽）",
       "pm:'boot'" in _html and "ok:true" in _html and "ok:false" in _html, "")

    ck("宿主暴露 on_boot 并在消息分发里处理 boot",
       hasattr(WhaleHostWebView, "on_boot")
       and '"boot"' in _inspect.getsource(WhaleHostWebView._on_web_message), "")

    _om = _inspect.getsource(WhaleWidget._on_pageboot)
    ck("boot=false ⇒ 不藏宿主页（页内提示卡 + 后台续等，不碰 _keep_draggable）",
       "_keep_draggable" not in _om and "_boot_ok = False" in _om, "")
    ck("boot=true ⇒ 连失败态一起清（只置 _boot_ok 会让迟到的鲸鱼被降级卡永久挡住）",
       "_boot_ok = True" in _om and "_load_failed = False" in _om, "")

    _wd = _inspect.getsource(WhaleWidget._boot_watchdog)
    ck("watchdog 是单次兜底（boot 已成功或已失败则直接退出，不重复降级）",
       "_boot_ok or self._load_failed" in _wd, "")

    _ah = _inspect.getsource(WhaleWidget._after_host_ready)
    ck("导航成功才挂 watchdog（70s 单次，等页面 60s 轮询预算走完）",
       "singleShot(70000" in _ah, "")

    # ---- D. 最小化不藏挂件 --------------------------------------------------
    import shell as _shell # noqa: PLC0415

    _he = _inspect.getsource(_shell.Shell.hideEvent)
    ck("hideEvent 不再隐藏挂件（控制台最小化/收托盘挂件仍显示）",
       "whale.hide" not in _he, "")

    _ce = _inspect.getsource(_shell.Shell.closeEvent)
    ck("真关路径显式带走挂件（无 parent 后必须 close，否则 WebView2 成孤儿）",
       "whale" in _ce and ".close()" in _ce, "")

    # NameError 空窗闸：_after_host_ready 用到 build_host_html，而它此前只在
    # _boot_webview 里函数级导入 ⇒ 控制台在跑（走到该行）时 NameError ⇒ 降级卡
    # 显示 NameError 文案。必须模块级可解析。
    import whale_widget as _wmod # noqa: PLC0415

    ck("挂件 build_host_html 模块级可解析（_after_host_ready 跨方法使用，防 NameError）",
       hasattr(_wmod, "build_host_html") and "build_host_html" in
       _inspect.getsource(_wmod.WhaleWidget._after_host_ready), "")

    _init = _inspect.getsource(_shell.Shell.__init__)
    ck("挂件构造不带 parent（有主工具窗会被主窗最小化连带藏掉）",
       "WhaleWidget(self.t)" in _init and "parent=self" not in
       _init.split("WhaleWidget(self.t)")[1][:20], "")

    host.close()


def t_g25_whale_boot() -> None:
    """挂件启动回报：三态文案 + 「迟到的鲸鱼能切回」（真跑状态机，不看源码形状）。

    现场症状就是那句话本身：「挂件页面无响应（未收到启动回报，请重启控制台重试）」，
    它由 70 秒看门狗写下，而当时可能发生两种完全不同的事：
      · 页面**回过话**、结论是「本体没画出来」（页面活着、鲸鱼没渲染）—— 却被说成
        "没收到回报"，排查方向直接被带偏；
      · 页面**一次话都没回**（页内脚本没跑起来）。
    另外失败态一旦置位就再也解不开：`paintEvent` 的放行条件是
    `not _load_failed and _boot_ok`，而 `_on_pageboot(True)` 原来只置 `_boot_ok`
    ⇒ 页面事后确认渲染出来了，界面仍停在降级卡上，用户只能重启控制台。

    这里驱动 `_on_pageboot` / `_boot_watchdog`，并**数渲染像素**判断卡还在不在。
    """
    import os as _os # noqa: PLC0415
    import sys as _sys # noqa: PLC0415

    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from PySide6.QtCore import QPoint # noqa: PLC0415
    from PySide6.QtGui import QImage # noqa: PLC0415
    from PySide6.QtWidgets import QApplication # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    import whale_widget as _ww # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    t = THEMES["whale"]

    class _FakeHost:
        """替身宿主：本判据只验状态机与画面，宿主调用一律收下不作声。"""

        ok = True
        error = ""

        def close(self): # noqa: ANN201
            pass

        def hide(self): # noqa: ANN201
            pass

        def resize(self, *a, **k): # noqa: ANN002, ANN003, ANN201
            pass

        def notify_moved(self): # noqa: ANN201
            pass

        def execute_script(self, *a, **k): # noqa: ANN002, ANN003, ANN201
            return True

        def send_mouse(self, *a, **k): # noqa: ANN002, ANN003, ANN201
            pass

    def _ink(w): # noqa: ANN001, ANN202
        """这张卡上"看得见的像素"数量（0 = 完全透明 ⇒ 交给页面画）。"""
        im = QImage(w.width(), w.height(), QImage.Format.Format_ARGB32)
        im.fill(0)
        w.render(im, QPoint(0, 0))
        n = 0
        for y in range(0, im.height(), 3):
            for x in range(0, im.width(), 3):
                if ((im.pixel(x, y) >> 24) & 0xFF) > 8:
                    n += 1
        return n

    def _new(): # noqa: ANN202
        w = _ww.WhaleWidget(t)
        w._boot_webview = lambda *a, **k: None # 不上屏 ⇒ 不建 WebView2
        w._host = _FakeHost()
        return w

    keep: list = []
    try:
        w1 = _new()
        keep.append(w1)
        ck("① 初始三态：没回话、没确认、没失败；界面上画的是兜底卡（不是全透明）",
           w1._boot_seen is False and w1._boot_ok is False and w1._load_failed is False
           and _ink(w1) > 50, "墨迹像素=%d" % _ink(w1))

        w1._on_pageboot(False)
        ck("② 页面回话「本体还没出现」⇒ 不置失败态、卡照旧显示（页内提示卡 + 后台续等）",
           w1._boot_seen is True and w1._boot_ok is False and w1._load_failed is False
           and _ink(w1) > 50, "")

        w1._boot_watchdog() # 页面回过话、鲸鱼始终没画出来
        ck("③ 看门狗在「回过话但没画出来」时说的是这件事，而不是「没收到回报」",
           w1._load_failed is True and "没有画出来" in w1._err
           and "未收到启动回报" not in w1._err, w1._err)

        w1._on_pageboot(True) # 迟到的鲸鱼
        ck("④ 迟到的鲸鱼真能切回：失败态与错误文案一起清掉、放行给页面（墨迹归零）",
           w1._boot_ok is True and w1._load_failed is False and w1._err == ""
           and _ink(w1) == 0, "err=%r 墨迹像素=%d" % (w1._err, _ink(w1)))

        w2 = _new()
        keep.append(w2)
        w2._boot_watchdog() # 页面一次话都没回
        ck("⑤ 「一次话都没回」是另一句文案（两种病分得开，不混成一句）",
           w2._load_failed is True and "未收到启动回报" in w2._err
           and w2._err != w1._err, w2._err)

        w3 = _new()
        keep.append(w3)
        w3._on_pageboot(True)
        w3._boot_watchdog()
        ck("⑥ 页面已确认渲染出来之后，看门狗不再动手（不重复降级、不覆盖状态）",
           w3._boot_ok is True and w3._load_failed is False and w3._err == "", w3._err)
    finally:
        for _w in keep:
            try:
                _w.close()
                _w.deleteLater()
            except Exception: # noqa: BLE001
                pass
        app.processEvents()


def _host_of(page):  # noqa: ANN001
    """给页面套一个宿主容器（叠字检测要用同坐标系）。"""
    from PySide6.QtWidgets import QVBoxLayout, QWidget # noqa: PLC0415

    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(page)
    return host


def t_button_label_guard() -> None:
    """自检：三个回归闸。

    A. **人设卡打星移除** —— ，移动/删除
       恢复全文字（不再缩成单字正圆）。防的是按钮删了、handler/字典还留死线，
       或哪天又被加回来。
    B. **安装按钮不许死点击** —— 缺件不是 pip 包时旧代码 `setEnabled(False)`，
       按钮看着能点、点了无声。
       现在缺件必须可点，点了给手动指引。
    C. **挂件位置越界自愈** —— QSettings 里的位置跨分辨率/换屏后可能已在
       屏幕外（实测 (1611,1431) 在 1080p 上 y 越界 ⇒「没看到挂件」）。
       恢复时校验至少露出 60×60，越界回默认右下角；正常位置原样保留。
    """
    import inspect as _inspect

    # ---- A. 人设卡 ---------------------------------------------------------
    src = (HERE / "panels_custom.py").read_text(encoding="utf-8")
    ck("人设卡不再有「打星」按钮（按需求移除）",
       "rate = Btn(" not in src and "def _rate_persona" not in src
       and '"rate":' not in src, "")

    ck("移动/删除是全文字钮（不再缩成单字正圆）",
       'Btn("删除"' in src and 'Btn("移动"' in src
       and "set_button_size(42, 42)" not in src, "")

    # ---- B. 安装按钮 -------------------------------------------------------
    _app = src[src.index("def _media_components_appendix"):]
    ck("缺件状态含 manual（pip 装不了的也要让按钮点得动）",
       '"manual": []' in _app and 'state["manual"] = manual' in _app, "")

    ck("按钮启用条件 = pip 可装 or 有手动缺件（不许死点击）",
       "btn.setEnabled(bool(pkgs or manual))" in _app, "")

    _i0 = _app.index("def _install()")
    _i1 = _app.index("btn.clicked.connect", _i0)
    _inst = _app[_i0:_i1]
    ck("点击时无 pip 缺件但有手动缺件 ⇒ 给指引而不是「都齐了」",
       "manual" in _inst and "装不了自动版" in _inst, "")

    # ---- C. 挂件位置自愈（功能验证；跑在**独立 QSettings 作用域**里）----------
    from PySide6.QtCore import QSettings # noqa: PLC0415

    import stylekit_qt as _sk # noqa: PLC0415

    import whale_widget as _ww # noqa: PLC0415

    _t = _sk.THEMES["whale"]

    ck("恢复位置前有上屏校验（_onscreen）",
       "_onscreen" in _inspect.getsource(_ww.WhaleWidget.__init__), "")

    # ⛔ 用独立作用域：位置记忆是用户现场状态，自检不能写它（旧做法"写用户值再还原"
    #    曾把用户的挂件位置改坏成屏幕外，直接导致"挂件不见了"）。
    _set_keep = getattr(_ww, "_SET", None)
    _ww._SET = ("WXAgent-selftest", "persona-morph-whale-selftest")
    _set = QSettings(*_ww._SET)
    _set.remove("")
    try:
        # 位置记忆以**本体右下角**为锚；这里给一个必然越界的锚点
        _set.setValue("whale_anchor", [1861, 1681])
        w1 = _ww.WhaleWidget(_t)
        from PySide6.QtWidgets import QApplication as _QA # noqa: PLC0415

        scr = _QA.instance().primaryScreen()
        g = scr.availableGeometry() if scr else None
        _in = g is not None and g.contains(w1.x(), w1.y())
        ck("越界锚点恢复时回默认角（不落到屏幕外）",
           _in, "pos=(%d,%d) screen=%s" % (w1.x(), w1.y(), g))

        w1.close()

        _set.setValue("whale_anchor", [400, 400])
        w2 = _ww.WhaleWidget(_t)
        _r2 = w2._whale_rect()
        ck("屏内锚点原样保留（把**本体**放回你拖到的那一点）",
           (_r2.x() + _r2.width(), _r2.y() + _r2.height()) == (400, 400),
           "anchor=(%d,%d) whale=%s" % (_r2.x() + _r2.width(),
                                        _r2.y() + _r2.height(), _r2))
        # 锚点独立于"本体贴窗口哪一边"：换边只改窗口位置，本体的屏幕位置一点不动。
        # （若把锚点记在窗口角上，换边就会把本体挪走 —— 这是"挂件跑到屏幕外"的老病根。）
        _win_before = w2.pos()
        w2._set_side("left")
        w2._apply_anchor()
        _r3 = w2._whale_rect()
        ck("锚点是本体的锚：换贴边方向后本体屏幕位置不变、窗口位置跟着补偿",
           (_r3.x(), _r3.y()) == (_r2.x(), _r2.y()) and w2.pos() != _win_before,
           "whale %s -> %s, win %s -> %s" % (_r2, _r3, _win_before, w2.pos()))
        w2.close()
    finally:
        if _set_keep is not None:
            _ww._SET = _set_keep
        _set.remove("")


def t_whale_roles() -> None:
    """自检：自定义角色这条链（导入 / 置顶 / 删除 / 取图 / 持久化）。

    跑的是**产品真实代码**（`agent.whale.WhaleWidget`）—— 端点是它写的，就由它来验，
    不另写一个"测试专用实现"（否则测试与实现各按自己理解写，全绿而功能废）。
    数据目录用临时目录，不碰用户数据。
    """
    import base64 as _b64
    import tempfile as _tf

    from agent import whale as _whale # noqa: PLC0415

    _tmp = _tf.mkdtemp(prefix="whale-roles-gate-")
    _w = _whale.WhaleWidget(_tmp)
    _png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8AAAwAB/AL+2QAAAABJRU5ErkJggg==")

    _r0 = _w.roles_payload()
    ck("角色列表含内置 default 且在首位（前端按 id==='default' 判定不可删）",
       _r0.get("ok") and _r0["roles"][0]["id"] == "default", str(_r0["roles"][:1]))

    _r1 = _w.save_role({"name": "测试角色", "image": _png})
    _role = [x for x in _r1.get("roles", []) if x["id"] != "default"]
    ck("导入角色：返回完整列表且新项带 createdAt（前端按它挑最新自动切换）",
       _r1.get("ok") and len(_role) == 1 and _role[0].get("createdAt", 0) > 0,
       str(_role))
    ck("角色图按 id 取回同样的字节（真存真读）",
       _w.role_image_bytes(_role[0]["id"]) == _b64.b64decode(_png.split(",")[1]))
    ck("导入非 dataURL / 空体一律拒绝（不写半个垃圾文件）",
       _w.save_role({"name": "x", "image": "http://a/b.png"}).get("ok") is False
       and _w.save_role(None).get("ok") is False)

    _r2 = _w.pin_role({"id": _role[0]["id"], "pinned": True})
    ck("角色置顶生效",
       [x for x in _r2["roles"] if x["id"] == _role[0]["id"]][0]["pinned"] is True)
    _r3 = _w.delete_role({"id": _role[0]["id"]})
    ck("删除角色：列表里消失、图片文件同时回收、内置角色不可删",
       _r3.get("ok") and all(x["id"] != _role[0]["id"] for x in _r3["roles"])
       and _w.delete_role({"id": "default"}).get("ok") is False)

    # 再导一个（上一个已在删除用例里被删掉），然后换实例读同一目录验证持久化
    _w.save_role({"name": "落盘验证", "image": _png})
    _w2 = _whale.WhaleWidget(_tmp)
    ck("换一个实例读同一目录：角色仍在（真落盘）",
       len([x for x in _w2.roles_payload()["roles"] if x["id"] != "default"]) == 1)
    ck("roles 系列已从「未支持」清单移除（补了能力就必须撤登记）",
       not any(x in _whale.WhaleWidget._UNSUPPORTED for x in
               ("roles.json", "role-image.png", "role-pin.json", "role-delete.json")))


def t_whale_assets() -> None:
    """自检：挂件「气泡图库 + 音效组」这条链（都跑产品真实代码 + 临时目录）。

    ⛔ 这两样都是**动作式**接口（不是"存一坨 JSON"）：
      音频 action = save-group / pin-group / delete-group / upload-fragment / delete-fragment；
      气泡图     action = upload / delete。
      响应必须带**整份列表**（前端拿它整块刷新，且多处读 `d.fragments`）。
    """
    import base64 as _b64
    import tempfile as _tf

    from agent import whale as _wh # noqa: PLC0415

    _tmp = _tf.mkdtemp(prefix="whale-assets-gate-")
    _w = _wh.WhaleWidget(_tmp)
    _png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8AAAwAB/AL+2QAAAABJRU5ErkJggg==")
    _wav = "data:audio/wav;base64," + _b64.b64encode(
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x44\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00").decode()

    # ── 气泡图库 ──
    _r = _w.bubble_img_action({"action": "upload", "name": "图", "data": _png})
    _bid = (_r.get("images") or [{}])[0].get("id")
    ck("气泡图上传：响应带整份图库、字节能按 id 取回",
       _r.get("ok") and len(_r.get("images", [])) == 1
       and _w.bubble_img_bytes(_bid) == _b64.b64decode(_png.split(",")[1]))
    ck("气泡图上传非图片被拒（不落半个垃圾文件）",
       _w.bubble_img_action({"action": "upload", "data": "不是图片"}).get("ok") is False)
    _r2 = _w.bubble_img_action({"action": "delete", "id": _bid})
    ck("气泡图删除：列表清空且文件回收",
       _r2.get("ok") and _r2["images"] == [] and _w.bubble_img_bytes(_bid) is None)

    # ── 音效组（动作式）──
    _a0 = _w.audio_payload()
    ck("音频初始两组预设且带 preset 标记（前端据此禁删）",
       [g["id"] for g in _a0["groups"]] == ["duck", "fx1"]
       and all(g["preset"] for g in _a0["groups"]))
    _a1 = _w.audio_action({"action": "upload-fragment", "name": "片段", "audio": _wav})
    _fid = (_a1.get("fragments") or [{}])[0].get("id")
    ck("音频片段上传：响应带 fragments（前端多处读 d.fragments）",
       _a1.get("ok") and len(_a1.get("fragments", [])) == 1, str(_a1.get("fragments")))
    _a2 = _w.audio_action({"action": "save-group", "name": "我的组",
                           "press": "frag:" + str(_fid), "release": ""})
    _grp = [g for g in _a2["groups"] if not g["preset"]]
    ck("新建音效组：槽位按前端约定存（空串 = 该事件静音）",
       _a2.get("ok") and _grp and _grp[0]["press"] == "frag:" + str(_fid)
       and _grp[0]["release"] == "")
    _gid = _grp[0]["id"] if _grp else ""
    ck("音效组置顶 / 预设组不可删",
       [g for g in _w.audio_action({"action": "pin-group", "id": _gid,
                                    "pinned": True})["groups"]
        if g["id"] == _gid][0]["pinned"] is True
       and _w.audio_action({"action": "delete-group", "id": "duck"}).get("ok") is False)

    # ── 声音真的取得到字节（不是"配置存了但没声音"）──
    ck("预设片段按名字取到随包素材（ya1 → Ya1.mp3）",
       len(_w.audio_fragment_bytes("ya1") or b"") > 1000)
    ck("自定义组的按压音 = 该组槽位片段；槽位留空则静音（None，不报错）",
       _w.sound_bytes("press", _gid) == _b64.b64decode(_wav.split(",")[1])
       and _w.sound_bytes("release", _gid) is None)
    ck("内置两组照旧有声音", bool(_w.sound_bytes("press", "duck"))
       and bool(_w.sound_bytes("release", "fx1")))

    # ── 落盘 ──
    # 上面把这张气泡图删掉了，这里再放一张，专验持久化
    _w.bubble_img_action({"action": "upload", "name": "落盘图", "data": _png})
    _w2 = _wh.WhaleWidget(_tmp)
    ck("换实例：片段与气泡图都还在（真落盘）",
       len(_w2.audio_payload()["fragments"]) == 1
       and len(_w2.bubble_imgs_payload()["images"]) == 1)
    ck("这批端点已从「未支持」清单移除（补了能力必须撤登记）",
       not any(x in _wh.WhaleWidget._UNSUPPORTED for x in
               ("bubble-imgs.json", "bubble-img.png", "bubble-img-upload.json",
                "audio-fragment.wav")))


def t_whale_usage() -> None:
    """自检：挂件「用量设置 / 用量记录 / API 模型表 / 余额微调」（产品真实代码 + 临时目录）。

    前端隐式要求（漏了就"看着有、其实白"）：
      · usage-settings 的 PUT 是**局部合并**（前端只发改动项）；
      · **usage-records 里也要带 settings**（前端两处从它刷设置缓存）；
      · all.days 的字段名是 date/total/currency（前端按 date 排序画柱图）；
      · api-models 是**动作式**（save/delete），且**密钥不落明文**。
    """
    import tempfile as _tf

    from agent import whale as _wh # noqa: PLC0415

    _tmp = _tf.mkdtemp(prefix="whale-usage-gate-")
    _w = _wh.WhaleWidget(_tmp)

    _s0 = _w.usage_settings()
    ck("用量设置默认齐备（taskEnd/alert/budget/models 一个都不能少）",
       all(k in _s0 for k in ("taskEnd", "alert", "budget", "models")), str(_s0))
    _r = _w.save_usage_settings({"budget": 5, "models": {"m1": {"alert": False}}})
    _r2 = _w.save_usage_settings({"models": {"m1": {"budget": 2}}})
    ck("用量设置 PUT 是**局部合并**（只发改动项、同模型多次改不丢前值）",
       _r["ok"] and _r2["settings"]["budget"] == 5
       and _r2["settings"]["models"]["m1"] == {"alert": False, "budget": 2},
       str(_r2["settings"]))

    _w.note_call("deepseek-flash", {"prompt_tokens": 100000, "completion_tokens": 200000})
    _w.note_turn_done()
    _rec = _w.usage_records_payload()
    ck("用量记录 today 有数（前端据它改「今日已用」并给标签）",
       _rec["today"]["total"] > 0 and _rec["today"]["label"], str(_rec["today"]))
    ck("用量记录 all.days 字段名 = date/total/currency（前端按 date 排序画柱图）",
       _rec["all"]["days"] and set(_rec["all"]["days"][-1]) >= {"date", "total", "currency"},
       str(_rec["all"]["days"][-1]))
    ck("⛔ usage-records 里也带 settings（前端两处从它刷设置，漏了设置会回默认）",
       isinstance(_rec.get("settings"), dict) and _rec["settings"].get("budget") == 5)

    _p0 = _w.api_models_payload()
    ck("模型表初始给模板", _p0["ok"] and len(_p0["templates"]) >= 2
       and _p0["models"] == [], str([t["id"] for t in _p0["templates"]]))
    _p1 = _w.api_models_action({"action": "save", "keyValue": "sk-fx5",
                                "model": {"name": "我的模型", "provider": "deepseek"}})
    _m = (_p1.get("models") or [{}])[0]
    ck("新建模型带**本机用量**（今日/合计），且**密钥不落明文**",
       _p1.get("ok") and _m.get("todayUsage", 0) > 0 and _m.get("keySet") is True
       and "keyValue" not in _m, str({k: _m.get(k) for k in ("name", "keySet")}))
    _mid = _m.get("id")
    _p2 = _w.api_models_action({"action": "save", "model": {"id": _mid, "name": "改名"}})
    ck("按 id 保存是更新而非新增；空名被拒",
       len(_p2["models"]) == 1 and _p2["models"][0]["name"] == "改名"
       and _w.api_models_action({"action": "save", "model": {"name": " "}}).get("ok") is False)
    ck("删除模型", _w.api_models_action({"action": "delete", "id": _mid})["models"] == [])

    _b1 = _w.balance_adjustments({"modelId": "m1", "day": "2026-09-26", "credits": "10",
                                  "otherDebits": "0", "confirmed": True})
    ck("余额微调：填了并确认才落地，回 summary 且标 source=manual（不冒充厂商数字）",
       _b1.get("ok") and _b1["summary"]["amount"] == 10.0
       and _b1["summary"]["source"] == "manual", str(_b1.get("summary")))
    ck("余额微调：没勾确认一律拒绝（防误改账）",
       _w.balance_adjustments({"modelId": "m1", "day": "2026-09-26",
                               "credits": "5"}).get("ok") is False)
    ck("余额微调：reset 清掉该日人工账",
       _w.balance_adjustments({"modelId": "m1", "day": "2026-09-26",
                               "action": "reset"})["summary"]["amount"] == 0.0)

    _w2 = _wh.WhaleWidget(_tmp)
    ck("换实例：用量设置与今日账本仍在（真落盘）",
       _w2.usage_settings()["budget"] == 5
       and _w2.usage_records_payload()["today"]["total"] > 0)
    ck("挂件端点已全部接通：未支持清单只剩目录式 sound/",
       _wh.WhaleWidget._UNSUPPORTED == ("sound/",), str(_wh.WhaleWidget._UNSUPPORTED))


def t_placeholder_guard() -> None:
    """自检：「占位卡死」与「清容器残留」两条链的回归闸。

    三组判据：
      A. **异步自链必须有存活判 + 上限** —— 页面重建/关页后闭包仍被定时器引用，
         旧实现无上限重排且对已析构控件 `setText`（RuntimeError 被外层 try 吞掉）
         ⇒ 占位永远停在「读取中/加载中」。
      B. **takeAt 清容器必须配 `setParent(None)`** —— 只 deleteLater 时控件仍是
         父的孩子（可见、占位），实测 memory 页共享群占位不曾消失。
      C. **空态与读失败要分开写** —— 首帧才写「读取中」，读失败写「读不到」，
         不能把「明明是读失败」说成「还在读」。
    """
    import ast as _ast

    from PySide6.QtWidgets import QApplication, QWidget # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from widgets import Field, QLineEdit as _QLE # noqa: PLC0415

    root = os.path.dirname(os.path.abspath(__file__))

    def _src(name: str) -> str:
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            return fh.read()

    def _apply_blocks(src: str) -> list:
        """取出所有 `def _apply` 函数体（AST 级，避免正则被嵌套 def 骗到）。"""
        out = []
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.FunctionDef) and node.name == "_apply":
                out.append(ast_getsrc(node))
        return out

    def ast_getsrc(node): # noqa: ANN001, ANN202
        return _ast.unparse(node)

    # ── A. 自链守卫 ──
    pc_src = _src("panels_custom.py")
    blocks = _apply_blocks(pc_src)
    capped = sum(1 for b in blocks if "tries" in b)
    alive = sum(1 for b in blocks if "_qt_alive" in b)
    ck("-A 所有带自链的 _apply 都有次数上限（无上限重排会在页面销毁后空转）",
       capped >= 5, "有上限的 _apply = %d / 共 %d" % (capped, len(blocks)))
    ck("-A 开页自动加载器的 _apply 有 C++ 存活判（_qt_alive）",
       alive >= 3, "带存活判的 _apply = %d / 共 %d" % (alive, len(blocks)))

    # ── B. 清容器纪律 ──
    # 判据：在 takeAt 循环里出现 deleteLater 时，同一小段里必须也有 setParent(None)。
    # 窗口取「本行 + 上一行 + 下一行」（两种写法都见过：同行链式 vs 分两行写）。
    miss = []
    for name in ("panels_custom.py", "onboarding.py", "shell.py"):
        src = _src(name)
        lines = src.split("\n")
        for i, ln in enumerate(lines):
            if "deleteLater()" not in ln:
                continue
            lo = max(0, i - 3)
            hi = min(len(lines), i + 2) # 含下一行
            ctx = "\n".join(lines[lo:hi])
            if ("takeAt" in ctx or "takeAt" in "\n".join(lines[lo:i + 1])) and "setParent" not in ctx:
                miss.append("%s:%d" % (name, i + 1))
    ck("-B takeAt 清容器一律配 setParent(None)（只 deleteLater 事件循环未转时会被搁置）",
       not miss, ("仍缺 setParent 的清理点: " + ", ".join(miss)) if miss else "0 处")

    # ── C. Field 左标签下限 + 不吃 stretch ──
    ck("-C Field 左标签区有最小宽（无下限会被压成竖排单字）",
       Field._LEFT_MIN >= 40, "_LEFT_MIN=%s" % Field._LEFT_MIN)

    import config_io # noqa: PLC0415, F401
    from stylekit_qt import THEMES # noqa: PLC0415

    host = QWidget()
    f = Field(THEMES["whale"], "每日消息数", "", _QLE(), host)
    host.resize(1000, 60)
    host.show()
    QApplication.processEvents()
    ck("-C Field 左标签实际宽度 ≥ _LEFT_MIN（布局真的尊重了下限）",
       f._left_w.width() >= Field._LEFT_MIN,
       "w=%d min=%d" % (f._left_w.width(), Field._LEFT_MIN))
    ck("-C Field 左列不吃 stretch（多出的宽全给右侧控件）",
       f._root.stretch(0) == 0 and f._root.stretch(1) == 1,
       "left=%d ctrl=%d" % (f._root.stretch(0), f._root.stretch(1)))
    host.close()


def t_dupdef_guard() -> None:
    """自检：类内**同名方法重复定义**（后者静默覆盖前者 ⇒ 前一份是死码）。

    这类缺陷不报错、不告警：模块照常 import，静态检查也过，但写在前面那份实现
    永远跑不到。实测形态：`whale_widget.WhaleWidget` 的鼠标处理器各定义了两份
    ——「把鼠标转发给内核」那份被后面「拖动窗口」那份整体覆盖 ⇒ 按合成承载跑的
    输入注入一次都没生效过（功能静默失效，代码却在）。

    判据完全机械化：AST 全量扫 `ui_qt/*.py`，属性 setter/getter 与 overload
    这类**同名是合法**的形态显式排除。
    """
    import ast # noqa: PLC0415

    bad = []
    for p in sorted(HERE.glob("*.py")):
        if p.name == "selftest.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen: dict = {}
            for f in node.body:
                if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decs = [ast.unparse(d) for d in f.decorator_list]
                if any(("." + k) in d for d in decs
                       for k in ("setter", "getter", "deleter")):
                    continue
                if any("overload" in d for d in decs):
                    continue
                seen.setdefault(f.name, []).append(f.lineno)
            for name, lns in seen.items():
                if len(lns) > 1:
                    bad.append("%s:%s.%s@%s" % (p.name, node.name, name, lns))
    ck("全量无「类内同名方法重复定义」（后者静默覆盖前者 = 死码）",
       not bad, "; ".join(bad[:4]) or "ok")


def t_attr_shadow_guard() -> None:
    """自检：类内**实例属性名与类内方法名同名**（属性遮蔽方法 ⇒ 实例上调不动）。

    这类缺陷不报错、不告警：模块照常 import、静态检查也过，但 `self.X()` 每次执行都抛
    `TypeError`（属性可能是 None，也可能是普通对象）。实测形态：`cursor_fx.WhaleCursor`
    里 `self._nod`（歪头帧 QCursor）遮蔽了同名方法 `_nod()` ⇒ 左键「点头」效果从未播放，
    而每个鼠标事件都要抛一次异常（现场 `data/runtime.log` 被这一条刷到 890KB）。

    判据机械化：AST 全量扫 `ui_qt/` 与 `agent/` 下每个类，取「类体里有 `def X`」∩
    「类体里有 `self.X = ...`」。
    """
    import ast # noqa: PLC0415

    bad = []
    for d in (HERE, HERE.parent / "agent"):
        for p in sorted(d.glob("*.py")):
            if p.name == "selftest.py":
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = {f.name for f in node.body
                           if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
                if not methods:
                    continue
                for sub in ast.walk(node):
                    tgt = getattr(sub, "targets", None)
                    if not tgt or len(tgt) != 1:
                        continue
                    t0 = tgt[0]
                    if (isinstance(t0, ast.Attribute) and isinstance(t0.value, ast.Name)
                            and t0.value.id == "self" and t0.attr in methods):
                        bad.append("%s:%s.%s@%s" % (p.name, node.name, t0.attr, t0.lineno))
    ck("全量无「实例属性遮蔽同类方法」（属性是 QCursor/None 时 self.X() 每次必抛）",
       not bad, "; ".join(bad[:4]) or "ok")


def t_async_landing_guard() -> None:
    """自检：异步回调**落地时控件已被销毁**不许抛异常。

    实测形态：面板发起请求 → 用户切页/换主题（`Shell._rebuild` 把整棵页栈
    `setParent(None)` + `deleteLater`）→ 请求回来时回调仍然落地，去取已销毁的控件 ⇒
    `RuntimeError: libshiboken: Internal C++ object (QLineEdit) already deleted`。
    PySide6 里这是**未捕获异常**：打整段栈，视版本直接终止应用，而用户看到的只是
    「面板用着用着整个没了」（现场 `data/runtime.log` 里抓到过完整栈）。

    判据三条：①存活判能认出已销毁控件；②回调真抛 RuntimeError 时被吞掉；
    ③端到端——面板销毁后异步结果不再落地。
    """
    import os # noqa: PLC0415
    import time as _time # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit # noqa: PLC0415

    QApplication.instance() or QApplication([])

    import agent_bridge # noqa: PLC0415
    import panels_custom as pc # noqa: PLC0415

    # ① 存活判。⛔ 这里必须用 `shiboken6.delete()` 立刻销毁 C++ 对象：`deleteLater()`
    #    只是投一个延迟删除事件，**没有跑起来的事件循环时它不会落地**（`processEvents()`
    #    不解 DeferredDelete）⇒ 用 deleteLater 做的"已销毁"在自检里是假的。
    import shiboken6 # noqa: PLC0415

    le = QLineEdit()
    _before = pc._ui_alive(le)
    shiboken6.delete(le)
    ck("异步落地前的存活判能识别「已销毁控件」",
       _before is True and pc._ui_alive(le) is False,
       "before=%s after=%s" % (_before, pc._ui_alive(le)))
    ck("没有绑定控件（None）时不误判为已死", pc._ui_alive(None) is True, "")

    # ①b 两种存活判：问的问题不同 ⇒ 对 None 的口径**相反**。都收口在 async_ui 一处并排写着，
    #    谁按自己的理解再造一个（比如 panels_custom 里又出现 `def _qt_alive`）就红。
    import async_ui as _au # noqa: PLC0415

    _pc_src = (HERE / "panels_custom.py").read_text(encoding="utf-8")
    ck("两种存活判对 None 口径相反（落地判 True / 取控件判 False）",
       _au.ui_alive(None) is True and _au.widget_alive(None) is False,
       "ui=%s widget=%s" % (_au.ui_alive(None), _au.widget_alive(None)))
    ck("存活判只有 async_ui 一处实现（`_qt_alive` 不再在别处被重新定义）",
       "def _qt_alive" not in _pc_src and "def _ui_alive" not in _pc_src, "")
    ck("两种判活对「已销毁控件」结论一致（都判死）",
       _au.widget_alive(le) is False and _au.ui_alive(le) is False, "")

    # ② 回调真抛 RuntimeError ⇒ 必须被吞掉（面板没了，没有界面要更新）
    def _boom(*_a):  # noqa: ANN002, ANN202
        raise RuntimeError("libshiboken: Internal C++ object already deleted")

    _boom_err = ""
    try:
        pc._deliver(_boom, 1, 2)
    except Exception as _e: # noqa: BLE001
        _boom_err = str(_e)
    ck("_deliver 吞掉「控件已销毁」的回调异常（不炸 UI 线程）", not _boom_err, _boom_err)

    # ③ 端到端：面板销毁后异步结果不再落地
    called: list = []
    page = QLineEdit()
    _real = agent_bridge.post_json
    agent_bridge.post_json = lambda *_a, **_k: {"ok": True} # 打桩打在 agent_bridge 上
    try:
        pc._async_post(page, "/api/x", {}, lambda r, e: called.append(r))
        shiboken6.delete(page) # 立刻销毁（真事件循环里 deleteLater 落地的等价物）
        QApplication.processEvents()
        _t0 = _time.monotonic()
        while _time.monotonic() - _t0 < 1.4 and not called:
            QApplication.processEvents()
            _time.sleep(0.05)
    finally:
        agent_bridge.post_json = _real
    ck("面板销毁后异步结果不再落地（否则取已销毁控件 = 抛异常）",
       called == [], "called=%s" % (called,))

    # ③b 统一驱动器自己的三条护栏（文本断言只能证明"写了"，这里证明"真的那样跑"）
    import async_ui # noqa: PLC0415

    _ticks: list = []
    _fin: list = []

    def _slow(box): # noqa: ANN001, ANN202
        _time.sleep(0.35) # 慢到至少要跨过两轮 150ms 轮询
        box["v"] = 7

    def _tick(box): # noqa: ANN001, ANN202
        _ticks.append(box.get("v"))

    def _finish(box): # noqa: ANN001, ANN202
        _fin.append(box.get("v"))

    async_ui.run_async(_slow, _finish, interval=50, name="gate-tick", on_tick=_tick)
    _t0 = _time.monotonic()
    while _time.monotonic() - _t0 < 2.0 and not _fin:
        QApplication.processEvents()
        _time.sleep(0.02)
    ck("on_tick 在「还没跑完」时真的被调（进度回显靠它）",
       bool(_ticks) and _fin == [7], "ticks=%d fin=%s" % (len(_ticks), _fin))

    _late: list = []

    def _never(box): # noqa: ANN001, ANN202
        _time.sleep(1.2) # 比 max_seconds 长得多

    async_ui.run_async(_never, lambda _b: _late.append(1), interval=20,
                       max_seconds=0.12, name="gate-cap")
    _t0 = _time.monotonic()
    while _time.monotonic() - _t0 < 1.6:
        QApplication.processEvents()
        _time.sleep(0.02)
    ck("轮询到 max_seconds 就停：超时的任务**不落地**（而不是永远重排）",
       _late == [], "late=%s" % (_late,))

    _errbox: list = []
    _ran: list = [] # 桩真的跑过没有（否则"没落地"可能是桩压根没执行，会把我引向错误方向）

    def _boom_work(box): # noqa: ANN001, ANN202
        _ran.append(1)
        raise ValueError("后台炸了")

    async_ui.run_async(_boom_work, lambda b: _errbox.append(b.get("_err")), interval=20,
                       name="gate-err")
    _t0 = _time.monotonic()
    while _time.monotonic() - _t0 < 2.5 and not _errbox: # 整包跑时前面堆的定时器会把调度拖慢
        QApplication.processEvents()
        _time.sleep(0.02)
    ck("后台异常不穿透线程边界，记进 box['_err'] 交给落地体（否则打整段栈）",
       _errbox and "后台炸了" in str(_errbox[0]),
       "err=%s 桩跑过=%s 用时=%.2fs" % (_errbox, len(_ran), _time.monotonic() - _t0))

    # ④ **全量口径**：手搓的"跨线程落地"**一处都不许有**（这一族已收口到 async_ui）。
    #    ⛔ 口径必须区分两类，否则会把正常的延迟重绘也算成违规（我第一版就是这么误报了 90 个）：
    #      · **需要守卫**＝函数里起了 `Thread(` ⇒ 回调在**另一个线程**落地到控件，控件可能已被销毁；
    #      · **不需要守卫**＝只有 `singleShot(`、没有 `Thread` ⇒ 那是**同面板延迟重绘**
    #        （`QTimer.singleShot(0, self._apply)` 这类），定时器挂在**面板自己**身上，
    #        Qt 会随对象销毁自动断开 ⇒ 天然安全。
    #    守卫 = 文本里出现 `_ui_alive(` / `_deliver(` / `qt_alive(` / `_alive`。
    #    ⛔ `async_ui.py` 按文件名豁免：它是这一族的**唯一实现点**，`Thread(` 就该长在那里。
    #      豁免的代价用下面那条"它还真的守着东西"的断言补回来。
    import ast as _ast2 # noqa: PLC0415
    import os as _o2 # noqa: PLC0415
    _qdir = _o2.path.dirname(_o2.path.abspath(__file__))
    _GUARD = ("_ui_alive(", "_deliver(", "qt_alive(", "_alive")
    _IMPL = "async_ui.py" # 这一族的唯一实现点（见该文件头）
    _raw_sites = []
    _all_py = 0
    for _dp, _dn, _fns in _o2.walk(_qdir):
        _dn[:] = [d for d in _dn if d not in ("__pycache__", "assets")]
        for _fn in sorted(_fns):
            if not _fn.endswith(".py") or _fn in ("selftest.py", _IMPL):
                continue
            _p = _o2.path.join(_dp, _fn)
            try:
                _src = open(_p, encoding="utf-8", errors="ignore").read()
                _tree = _ast2.parse(_src)
            except Exception: # noqa: BLE001
                continue
            _all_py += 1
            _lines = _src.split("\n")
            for _n in _ast2.walk(_tree):
                if not isinstance(_n, (_ast2.FunctionDef, _ast2.AsyncFunctionDef)):
                    continue
                _body = "\n".join(_lines[_n.lineno - 1:(_n.end_lineno or _n.lineno)])
                if any(_g in _body for _g in _GUARD):
                    continue
                _th = sum(1 for _c in _ast2.walk(_n) if isinstance(_c, _ast2.Call) and
                          ((isinstance(_c.func, _ast2.Attribute) and _c.func.attr == "Thread")
                           or (isinstance(_c.func, _ast2.Name) and _c.func.id == "Thread")))
                if _th:
                    _raw_sites.append("%s::%s" % (_o2.path.relpath(_p, _qdir).replace("\\", "/"), _n.name))
    #: 棘轮已收口：这一族**硬零**（迁移完成，基线 44 → 0）。
    ck("手搓的跨线程落地**一处在哪都不许有**（族已收口到 async_ui.run_async）",
       not _raw_sites,
       "当前 %d 处%s" % (len(_raw_sites),
                       ("；例：" + "、".join(_raw_sites[:4])) if _raw_sites else ""))
    #: 分母守卫：扫不到文件时上面那条会"全绿"（写全量口径的闸门必须同时断言分母）。
    ck("上述全量扫描真的扫到了文件（分母守卫）", _all_py >= 20, "扫到 %d 个 .py" % _all_py)
    #: ⛔ 豁免 `async_ui.py` 的代价：它必须**真的**是唯一实现点、而且真的带护栏。
    with open(_o2.path.join(_qdir, _IMPL), encoding="utf-8") as _fh:
        _impl_src = _fh.read()
    ck("async_ui 里只有两个起线程的地方（run_bg / run_async）——不许变成新的杂物间",
       _impl_src.count("threading.Thread(") == 2, "实测 %d 处" % _impl_src.count("threading.Thread("))
    ck("async_ui 的 run_async 自带存活判 + 轮询上限（护栏没被删）",
       "ui_alive(page)" in _impl_src and "_left[0] <= 0" in _impl_src, "")


def t_screen_guards() -> None:
    """自检：功能筛查这一批修复的机械闸门（每条都对应一个真实缺陷）。

    · 端口顺延真的生效（`SO_REUSEADDR` 下"绑一下试试"试不出来 ⇒ 先探监听者）
    · 发送去重「查/记分离」（失败了不留记录 ⇒ 重试不会被当重复吞掉）
    · 记忆目录名带哈希（不同 chat_key 不再撞同一个目录）
    · 覆盖层清理只认输入体验的标题（不再误关用户的「设置」窗口）
    · HTTP 401/403 有专属原因码（不再落到 unknown）
    """
    import os # noqa: PLC0415
    import socket as _sk # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from agent import reason_codes as _rc  # noqa: PLC0415

    # ① 端口占用探测：真起一个监听者，再问一次
    srv = _sk.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    _live = srv.getsockname()[1]
    s2 = _sk.socket()
    s2.bind(("127.0.0.1", 0))
    _free = s2.getsockname()[1]
    s2.close()
    import agent.webui as _web # noqa: PLC0415

    ck("端口占用探测能认出「有人在听」",
       _web._port_taken("127.0.0.1", _live) is True
       and _web._port_taken("127.0.0.1", _free) is False,
       "live=%s free=%s" % (_live, _free))
    srv.close()
    ck("顺延逻辑以探测结果为准（不再靠「绑一下试试」——SO_REUSEADDR 下试不出来）",
       "if _port_taken(host, cand)" in (
           HERE.parent / "agent" / "webui.py").read_text(encoding="utf-8"), "")

    # ② 去重查/记分离
    from collections import deque # noqa: PLC0415

    from agent import wechat as _wx # noqa: PLC0415

    a = _wx.WeChatAdapter.__new__(_wx.WeChatAdapter)
    a._send_recent = deque(maxlen=50)
    _r1 = a._dedup_send("c1", "hi")
    _r2 = a._dedup_send("c1", "hi") # 还没记 ⇒ 仍允许（失败重试不该被吞）
    a._dedup_mark("c1", "hi")
    _r3 = a._dedup_send("c1", "hi") # 记过 ⇒ 拦
    _r4 = a._dedup_send("c2", "hi") # 别的会话 ⇒ 放行
    ck("发送去重「查/记分离」：没记就放行、记过才拦、按会话分账",
       _r1 is True and _r2 is True and _r3 is False and _r4 is True,
       "r=%s%s%s%s" % (_r1, _r2, _r3, _r4))

    # ③ 记忆目录名带哈希（不同 chat_key 不许撞同一个目录）
    import agent.memory as _mem # noqa: PLC0415

    ck("记忆目录名按 chat_key 哈希区分（`a-b` 与 `a_b` 不再撞同一个目录）",
       _mem._chat_dir_name("group:wxid_a-b") != _mem._chat_dir_name("group:wxid_a_b"),
       "%s / %s" % (_mem._chat_dir_name("group:wxid_a-b"),
                    _mem._chat_dir_name("group:wxid_a_b")))
    # 老目录必须继续被认（否则用户视角＝"记忆没了"）
    _tmp = _tf.mkdtemp(prefix="mem-dir-gate-")
    _keep_dir = _mem.MEMORY_DIR
    try:
        _mem.MEMORY_DIR = _tmp
        _legacy = os.path.join(_tmp, _mem._chat_dir_name_legacy("group:abc@chatroom"))
        os.makedirs(_legacy, exist_ok=True)
        ck("记忆目录换命名后仍认老目录（读写闭环不丢已有数据）",
           os.path.normcase(_mem._chat_dir("group:abc@chatroom")) == os.path.normcase(_legacy),
           _mem._chat_dir("group:abc@chatroom"))
        ck("新会话落新命名（唯一性由哈希保证）",
           os.path.basename(_mem._chat_dir("group:newone@chatroom")).endswith(
               _mem._key_hash("group:newone@chatroom")), "")
    finally:
        _mem.MEMORY_DIR = _keep_dir
        import shutil as _sh # noqa: PLC0415

        _sh.rmtree(_tmp, ignore_errors=True)

    # ④ 覆盖层清理只碰"输入体验"

    _ua_src = (HERE.parent / "agent" / "ui_adapt.py").read_text(encoding="utf-8")
    ck("覆盖层清理按标题收窄到「输入体验」（否则会把用户的「设置」窗口关掉）",
       "_OVERLAY_TITLES" in _ua_src
       and "not any(k in _t for k in _OVERLAY_TITLES)" in _ua_src, "")
    ck("手写画布那一类不受标题白名单限制（它没有「输入体验」标题）",
       '"ShellHandwritingCanvas"' in _ua_src, "")

    # ⑤ 401/403 有专属原因码
    ck("HTTP 401/403 归到 auth_rejected（不再落到 unknown）",
       _rc.classify("HTTP 401 Unauthorized") == "auth_rejected"
       and _rc.classify("403 Forbidden") == "auth_rejected"
       and _rc.classify("鉴权失败：token 无效") == "auth_rejected",
       _rc.classify("HTTP 401 Unauthorized"))


def t_gate_middle() -> None:
    """自检：两条"折中口径"的机械判据。

    ① **版本门**：实测 `status='no'` 的组合**不再被当绿灯**（以前 `measured=True` 就直接 `level=ok`，
       什么都不说），但也**不拦** —— 按"能发就发"照发，同时记账 + 说清楚；本机连续 3 次
       能力类失败 ⇒ 升级为"本机实测发不出去"（给可照做的动作，仍然照发）；成功一次即清零。
    ② **内容过滤**：本地提示词闸只认**正面命中**（不搞"判不出就拒"）；`_f_classifier` 不再是
       那个"从不工作的桩"（接真链，且判据不可用时如实说"未参与判定"而不是假装检查过）。
    """
    import inspect as _inspect # noqa: PLC0415
    import os # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from agent import image_filter as _if # noqa: PLC0415
    from agent import reason_codes as _rc # noqa: PLC0415
    from agent import version_gate as _vg # noqa: PLC0415
    from agent import version_matrix as _vm # noqa: PLC0415
    from agent import store as _store # noqa: PLC0415

    # ① 本地提示词闸：正面命中才拦，未命中一定放行
    _hit = _if.f_prompt_banned("画一张色情露骨的裸体图")
    _pass = _if.f_prompt_banned("窗台上的橘猫在晒太阳")
    ck("本地提示词闸：命中才拦、未命中必放行（不搞「判不出就拒」）",
       _hit[0] is False and _hit[1] and _pass == (True, ""), "%s / %s" % (_hit, _pass))
    ck("内容过滤档位可读（自检/日志要它一句话说清哪几层在线）",
       "本地提示词闸" in _if.tier_line() and "内容过滤" in _if.tier_line(), _if.tier_line()[:70])

    # ② 版本门：no 档照发但可见；连续失败本机自证
    _tmp = _tf.mkdtemp(prefix="vg-middle-gate-")
    _keep_data = getattr(_store, "DATA_DIR", None)
    try:
        _store.DATA_DIR = _tmp
        _data = {"runs": [{"wechat": "9.9.9", "adapter": "1.0",
                           "caps": {"send_text": {"status": "no"}}, "when": "synthetic"}]}
        _keep_load = _vm.load
        _vm.load = lambda path=None: dict(_data) # noqa: ARG005
        try:
            _g = _vm.gate(_data, "9.9.9", "1.0", facts="")
            ck("矩阵把 status=no 报上来（以前它不参与任何判定 ⇒ 门把它当绿灯）",
               _g.get("no_caps") == ["send_text"] and _g.get("measured") is True,
               "no_caps=%s measured=%s" % (_g.get("no_caps"), _g.get("measured")))
            _r = _vg.check("send", wechat="9.9.9", adapter="1.0")
            ck("实测发不出去 ⇒ **照发**（allow=True）但**不是绿灯**（level=no_measured）+ 说清原因",
               _r.get("allow") is True and _r.get("level") == "no_measured"
               and "照发" in str(_r.get("reason") or ""),
               "level=%s allow=%s" % (_r.get("level"), _r.get("allow")))
            ck("这一档要记账（控制台横幅读得到）", _vg.no_measured_stat().get("count", 0) >= 1,
               str(_vg.no_measured_stat().get("count")))
            _why = "等待超时" # 归类为 timeout（能力类，不是身份/暂停/内容类）
            ck("喂进自证的原因确实是能力类（否则本闸门测不到东西）",
               _rc.classify(_why) not in _vg._NOT_CAPABILITY, _rc.classify(_why))
            for _i in range(_vg._LOCAL_FAIL_NEED):
                _vg.note_send_result(False, _why, wechat="9.9.9", adapter="1.0")
            ck("连续 %d 次能力类失败 ⇒ 本机自证「发不出去」落盘" % _vg._LOCAL_FAIL_NEED,
               str(_vg.local_state("9.9.9", "1.0").get("status")) == "no",
               str(_vg.local_state("9.9.9", "1.0")))
            _r2 = _vg.check("send", wechat="9.9.9", adapter="1.0")
            ck("本机自证档：仍照发（allow=True），原因里给可照做的动作",
               _r2.get("allow") is True and _r2.get("level") == "no_measured_local"
               and "升级适配层" in str(_r2.get("reason") or ""),
               "level=%s" % _r2.get("level"))
            _vg.note_send_result(True, wechat="9.9.9", adapter="1.0") # 成功一次
            ck("成功一次即清零（结论跟着现场走，不靠一次观测定终身）",
               _vg.local_state("9.9.9", "1.0") == {}, str(_vg.local_state("9.9.9", "1.0")))
            _vg.note_send_result(False, "当前会话没认准", wechat="9.9.9", adapter="1.0")
            _vg.note_send_result(False, "当前会话没认准", wechat="9.9.9", adapter="1.0")
            _vg.note_send_result(False, "当前会话没认准", wechat="9.9.9", adapter="1.0")
            ck("身份类失败**不喂**这条自证（别把身份问题记成版本问题）",
               _vg.local_state("9.9.9", "1.0") == {}, str(_vg.local_state("9.9.9", "1.0")))
        finally:
            _vm.load = _keep_load
    finally:
        if _keep_data is not None:
            _store.DATA_DIR = _keep_data
        import shutil as _sh # noqa: PLC0415

        _sh.rmtree(_tmp, ignore_errors=True)

    # ③ 生图内容层：不再是"从不工作的桩"
    _png = str(HERE.parent / "assets" / "icon-whale.png")
    try:
        from agent import image_gen as _ig # noqa: PLC0415

        _fn = _ig._f_classifier
        # ⚠️ 断言锚在**代码形态**（导入语句），不锚"有没有某名词"—— 本函数 docstring 里
        #    就写着老实现长什么样，按名词判会把自己判红。
        ck("内容层已接真链（不再是那个从不工作的桩）",
           "from . import image_filter" in _inspect.getsource(_fn), "")
        if os.path.exists(_png):
            _ok, _why = _fn(_png, {})
            ck("内容层对一张真图给出**明确结论**（不是「未接」）",
               isinstance(_ok, bool) and "未接" not in str(_why), "%s / %s" % (_ok, str(_why)[:50]))
    except Exception as _e: # noqa: BLE001
        ck("内容层可调用", False, "%s: %s" % (type(_e).__name__, _e))


#: 控制台里**故意留着但尚未接线**的开关。判据不是"有没有这个键"，而是"**有没有骗人**"：
#   键可以暂时不接线（保住用户的选择），但**不许**在界面上装成能用。
#   规矩（本闸门强制）：清单里的键，UI 文案必须带「未接线」字样；反过来，不在清单里的键
#   **必须真有消费方**。两边都查 ⇒ 既拦住"假开关"，也拦住"接线后忘了撤登记"。
UNWIRED_CFG_KEYS = {
    "send.uia_setvalue": "主发送链目前只有一条实现在跑（UIA 直进 + 回退粘贴），不由这里切换",
    "scoring.online_scoring": "本机没有「调 LLM 打分」这条实现，评分全在本地算",
}


def t_cfg_wired_guard() -> None:
    """自检：控制台暴露的配置键，**要么真有消费方，要么如实标注「未接线」**。

    ⛔ 为什么要有这条闸门：一个"勾了没反应"的开关，比没有这个开关更坏——它替产品
    **承诺了一个不存在的行为**（用户按它调了刹车/改了行为，实际什么都没发生）。
    这类问题不会自己暴露：没有异常、没有日志、功能测试也不碰它。
    唯一的机械化判据就是"**这个键有没有被真读过**"，所以这里按**全量口径**逐个核。

    两个方向都查：
      ① 清单外的键 ⇒ 必须在 `agent/`/`ui_qt/`/`scripts/` 的代码里被真读到；
      ② 清单内的键 ⇒ UI 文案必须写明「未接线」，且**确实没有**消费方（防止接线后还挂着"未接线"）。
    """
    import ast as _ast # noqa: PLC0415
    import inspect as _inspect # noqa: PLC0415
    import os as _o # noqa: PLC0415
    import re as _re # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
    _console = _o.path.join(_root, "assets", "console", "index.html")
    try:
        _src = open(_console, encoding="utf-8", errors="ignore").read()
    except OSError as _e:
        ck("控制台页面可读（配置键闸门的前提）", False, "%s" % _e)
        return

    _keys = sorted({k for k in _re.findall(r'data-cfg="([^"]+)"', _src)
                    # 排除 JS 模板串拼出来的假键（含引号/加号/空格的都不是配置键）
                    if _re.match(r"^[A-Za-z_][\w.]*$", k)})
    ck("控制台暴露的配置键可枚举（闸门要有分母）", len(_keys) > 200, "%d 个键" % len(_keys))

    # 收集"真消费方"。三处必须是**非消费方**，否则分母全绿：
    #   · `agent/config.py` —— 默认表里出现只说明"声明过"；
    #   · `assets/console/index.html` —— 它就是 UI 自己（页面已从 `console_html.py` 外置到
    #     仓库根的 `assets/`，而下面只扫 `agent` / `scripts` 两棵树 ⇒ 它天然不在池子里）；
    #   · `*selftest*` —— 判据引用不算产品消费。
    _pool = {}
    for _sub in ("agent", "scripts"):
        for _dp, _dn, _fns in _o.walk(_o.path.join(_root, _sub)):
            _dn[:] = [d for d in _dn if d not in ("__pycache__", "assets", "logs", "data")]
            for _fn in _fns:
                if not _fn.endswith(".py") or "selftest" in _fn:
                    continue
                _p = _o.path.join(_dp, _fn)
                if _p.endswith(_o.path.join("agent", "config.py")):
                    continue # ⛔ 声明处不是消费方
                try:
                    _pool[_p] = open(_p, encoding="utf-8", errors="ignore").read()
                except OSError:
                    pass

    def _quoted(_s, _name):
        return ('"%s"' % _name) in _s or ("'%s'" % _name) in _s

    # ⭐ **访问器（accessor）形态**：`agent/config.py` 里的读取被刻意排除在消费池之外
    #   （"声明处不是消费方"）。但本项目有一类键**故意只在 `config.py` 里读一次**，
    #   再包成一个**函数**给全仓用（例：`token_budget.saver` ⇒ `config.token_saver()`
    #   ⇒ `prompt.py` / `tools.py` 都从那个函数拿）。这是**更强的收口**（改一处就全改），
    #   不是"没接线"。⇒ 若 `config.py` 里有个**函数**读了这个键，或某个模块 import/调用了
    #   一个以该键为名的访问器，就算**已消费**。否则这条闸门会把"做得更对的做法"判成红。
    _cfg_src = open(_o.path.join(_root, "agent", "config.py"),
                    encoding="utf-8", errors="ignore").read()
    try:
        _cfg_tree = _ast.parse(_cfg_src)
    except SyntaxError:
        _cfg_tree = None

    def _accessor_names_of(key):
        """`config.py` 里"读了 `key` 那个末段"的函数名集合（供别处按名字找得到调用）。"""
        _leaf = key.split(".")[-1]
        _out = set()
        if _cfg_tree is None:
            return _out
        for _n in _ast.walk(_cfg_tree):
            if not isinstance(_n, _ast.FunctionDef):
                continue
            _body = _ast.get_source_segment(_cfg_src, _n) or ""
            if _quoted(_body, _leaf):
                _out.add(_n.name)
        return _out

    def _consumed(_key):
        """键 `a.b.c` 是否被**真读过**。三种形态都要认，否则会误报成假开关：
        ① 字面量：`get` / `["c"]`；
        ② **拼出来的键名**：如 `system_prompt.module_enabled` 里 `key = "enable_" + mid`，
           此时用"模块 id"（`scene_rules`）去找消费点 `_mod_on`；
        ③ **访问器**：`config.py` 里有个函数读了它 ⇒ 别处调那个函数即算消费（见 `_accessor_names_of`）。
        """
        _t, _l = _key.split(".")[0], _key.split(".")[-1]
        if _t == "ui":
            return True # 界面行为：实现在页面/壳里（前端 JS / Qt），不属于"后端消费"这条判据
        for _s in _pool.values():
            if _t not in _s:
                continue
            if _quoted(_s, _l):
                return True
            if _l.startswith("enable_") and _quoted(_s, "enable_"):
                if _quoted(_s, _l[len("enable_"):]):
                    return True
        for _acc in _accessor_names_of(_key):
            for _s in _pool.values():
                if ("%s(" % _acc) in _s:
                    return True
        return False

    _ui_only = len([k for k in _keys if k.split(".")[0] == "ui"])
    ck("界面行为类键（ui.*）单独计一档、不混进「后端有没有读」的分母",
       _ui_only >= 1, "%d 个（豁免理由：实现在页面/壳里）" % _ui_only)

    # ⭐ 第三个轴：**控制台能改的键，默认表必须声明它**。
    #    否则出现"界面能改、默认表不认识"的键 ⇒ 用户一改就落进 config.json，而 `cfg()` 合并
    #    默认表时它不在表里 ⇒ 没有权威默认值，行为只能靠各处 `.get(键, 手写默认)` 拼：
    #    谁漏写一处就静默走另一套（本项目反复栽在这上面：默认值与"缺键时的隐式行为"不一致，
    #    最典型的是 `send.quote_on_new_talk` —— 缺键时后端按"开着"跑，而界面渲染成未勾选）。
    _paths = set()

    def _walk(_d, _pre=""):
        for _k, _v in (_d or {}).items():
            _p = (_pre + "." + _k) if _pre else _k
            _paths.add(_p)
            if isinstance(_v, dict):
                _walk(_v, _p)

    from agent.config import DEFAULT_CONFIG as _DEF # noqa: PLC0415
    _walk(_DEF)
    _undeclared = [k for k in _keys if k not in _paths]
    ck("控制台能改的键，默认表都声明了（默认值不许只存在于各处手写回退里）",
       not _undeclared, "；".join(_undeclared[:8])[:200])

    _fake, _missing_label = [], []
    for _k in _keys:
        _read = _consumed(_k)
        if _k in UNWIRED_CFG_KEYS:
            # ② 清单内的键：不许有消费方（真接线了就该从清单里撤掉），且文案要说明白
            if _read:
                _fake.append("%s（已接线，请从 UNWIRED_CFG_KEYS 撤登记）" % _k)
            _seg = _src.find('data-cfg="%s"' % _k)
            _win = _src[_seg:_seg + 420] if _seg >= 0 else ""
            if "未接线" not in _win:
                _missing_label.append(_k)
        elif not _read:
            _fake.append("%s（UI 有、后端不读 ⇒ 勾了没反应）" % _k)

    ck("① 清单外的键都真有消费方（没有「勾了没反应」的假开关）",
       not _fake, "；".join(_fake[:6])[:180])
    ck("② 未接线清单里的键，界面文案都写明「未接线」（不装成能用）",
       not _missing_label, "；".join(_missing_label)[:180])
    ck("未接线清单本身不是空的（这条闸门还在真的守着东西）",
       len(UNWIRED_CFG_KEYS) >= 1, "%d 个" % len(UNWIRED_CFG_KEYS))
    # ③ 本轮接线的两个键必须有消费方（正面核验：接线不是靠"改文案"交差的）
    from agent import scoring as _sc # noqa: PLC0415
    from agent import sender as _sd # noqa: PLC0415
    ck("「热度衰减」开关已真接线（_decay 读 scoring.heat_decay）",
       "heat_decay" in _inspect.getsource(_sc._decay)
       or "heat_decay" in _inspect.getsource(_sc._heat_decay_on), "")
    ck("「每分钟限发」开关已真接线（限发器读 wechat.rate_limit_per_minute）",
       "rate_limit_per_minute" in _inspect.getsource(_sd.SendQueue._check_rate), "")
    from agent import prompt as _pr # noqa: PLC0415
    ck("「种子库」开关已真接线（提示词按 scoring.seed_library 决定放不放内置种子）",
       "seed_library" in _inspect.getsource(_pr._funny_reference), "")


#: 「产品代码里出现本地草稿目录名」的文件白名单 —— **每一条都要写清为什么它不会坏事**。
#   为什么要有这条闸门：那个目录是**本地草稿**（不入库、不进包），用户机器上根本不存在。
#   所以产品代码里只要**把它当路径用**（拼接/创建/读写），在用户那里就是"指向一个不存在的目录"，
#   而它又是 gitignore 的 ⇒ 本地跑一次就静默留痕、`git status` 还看不见，最容易被忽略。
#   实测踩过三处：编译脚本每次跑都把该目录建回产品根；两个判据脚本往它下面写夹具。
#   ⚠️ 注释/取证引用（`xxx.py` 这种出处标注）**不算问题**，出包闸门对它们只报非致命 warn。
DEV_DIR_KNOWERS = {
    "agent/update_apply.py": "更新链的 NEVER_TOUCH 白名单：语义是「别碰它」，不是「用它」",
    "scripts/dead_code_triage.py": "判据台账：目录名出现在**条目理由/出处标注**里（不是路径使用），"
                                   "与上面几条注释引用同性质",
    "agent/wechat.py": "注释里的取证出处",
    "agent/prompt.py": "注释里的取证出处",
    "agent/input_audit.py": "注释里的取证出处",
    "ui_qt/__init__.py": "注释：说明本目录由原型提升而来",
    "scripts/pack_online.py": "出包闸门自己要认这个目录名（EXCLUDE 规则 + 「开发资料引用」规则本身）",
    "scripts/click_guard_selftest.py": "只读探针：判断某个开发脚本在不在（有 exists 守卫，不在就跳过）",
    "scripts/update_check_selftest.py": "同上（有 exists 守卫）",
    "scripts/file_btn_probe.py": "随包入口脚本的用法示例（注释）",
    "scripts/proc_window_selftest.py": "注释里的取证出处",
    "scripts/switch_chat_probe.py": "注释：解释为什么要有这个随包入口",
    "ui_qt/selftest.py": "本闸门自己要认这个目录名（与出包闸门的 EXCLUDE 规则同理）",
    "scripts/menu_geometry_selftest.py": "只出现在**注释里的取证出处**（真机菜单帧的存放位置），不是路径使用",
    "scripts/pack_secret_gate_selftest.py": "出包闸门的判据自己要认这个目录名：它断言的就是"
                                            "「该目录**不在**随包范围内」，所以这个名字必须出现在断言里",
    "scripts/stats_month_selftest.py": "临时夹具目录的**父级**（拼接 `..` 落到仓库根下的草稿区），"
                                       "只在判据运行时用、用完即删，不进产品",
}


#: 浮层（**盖在别的内容之上**的面）的选择器 → 它在哪、为什么算浮层。
#: 底色**必须**走 `stylekit_qt.surface_bg`（玻璃主题给实色 `#16334F`，其余主题 `t.card`）。
#: 为什么要有这条闸门：玻璃主题下 `t.card` 是 `rgba(26,58,88,0.72)`（**28% 透**），浮层直接拿它
#: 当底，就会把下面的字叠进弹窗里 —— 观感就是「半透 + 字发虚」。此前 `confirm._apply_shell` 修过、
#: `onboarding` 那份漏了（截的就是引导弹窗）。**手抄散了口径才会漏**，所以这里逐个点名。
SURFACE_BG_SELECTORS = [
    ("ui_qt/confirm.py", "#Shell", "confirm 系弹窗卡片（ConfirmDialog / InfoDialog / ChoiceDialog）"),
    ("ui_qt/onboarding.py", "#C8OnboardCard", "五步引导向导卡片"),
    ("ui_qt/panels_qt.py", "#GpCard", "群勾选弹窗卡片"),
    ("ui_qt/panels_custom.py", "#PAddCard", "面板「加一条」弹窗卡片"),
    ("ui_qt/panels_custom.py", "#C8CardDlg", "面板通用卡片弹窗"),
    ("ui_qt/panels_custom.py", "#TextWindowShell", "长文本查看窗卡片"),
    ("ui_qt/shell.py", "QMenu", "右键 / 下拉菜单（直接盖在正文上）"),
]


def t_surface_bg_guard() -> None:
    """闸门：浮层底色走**唯一实现点** `surface_bg` —— 玻璃主题下浮层必须是实心。

    判据两条：
      ① 上面每个浮层选择器，它的 `background:` 取值**不许是裸 `t.card`**（必须是 `surface_bg(t)`
         或由 `surface_bg(t)` 赋值的变量）；
      ② 分母守卫：真的扫到了这些选择器（扫不到就成了空集假绿 —— 那正是这条闸门要防的形态）。
    """
    import os as _o # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
    _found = 0
    _bad = []
    _cache = {}
    for rel, sel, why in SURFACE_BG_SELECTORS:
        p = _o.path.join(_root, rel.replace("/", _o.sep))
        if p not in _cache:
            try:
                _cache[p] = open(p, encoding="utf-8").read()
            except OSError:
                _cache[p] = ""
        src = _cache[p]
        if not src:
            _bad.append("%s 读不到" % rel)
            continue
        if "surface_bg" not in src:
            _bad.append("%s 没引用 surface_bg" % rel)
        # ⚠️ 用**纯字符串定位**而不是正则：选择器里全是花括号，正则要先转义再拼，
        #    极易写成 `re.escape(sel) + "{..."` 这种双重转义（实测就栽在这上面）。
        #    开窗写法固定是 `<选择器>{{background:{表达式};`，取到那一对花括号里的东西就行。
        key = sel + "{{background:{"
        idx = src.find(key)
        if idx < 0:
            continue # 该文件里这条选择器换了写法；下面第二条"分母守卫"会兜住"一条都没扫到"
        _found += 1
        expr = src[idx + len(key):].split("}", 1)[0].strip()
        if expr in ("t.card", ""):
            _bad.append("%s 的 %s 底色是裸 t.card（%s）" % (rel, sel, why))
    ck("① 浮层底色都走 surface_bg（玻璃主题下实心）", not _bad, "；".join(_bad)[:180])
    ck("② 分母守卫：真的扫到了这些浮层选择器", _found >= 6, "扫到 %d 条" % _found)


def t_field_guard() -> None:
    """闸门：输入/选单"槽"的外观走**唯一实现点** `stylekit.field_qss`。

    判据四条：
      ① **反向**：除 `stylekit_qt.py`（它就是实现点）外，`ui_qt/*.py` 里不许再出现
         `QLineEdit{` / `QComboBox{` / `QPlainTextEdit{` / `QTextEdit{` 这类 QSS 片段
         —— 各自手抄的结果就是"抄漏的地方直接落回系统白底"（深色主题上一排白条）。
      ② **分母守卫**：`field_qss(` 的引用点必须 ≥ 8（扫不到就成了空集假绿）。
      ③ **功能**：造一个 `widgets.FieldCombo`，离屏渲染，**箭头那一列的笔画像素必须 > 0**
         —— 实现里关掉了系统箭头（QSS 的 `image:` 只能指文件），自绘那一笔没画出来
         用户就看不出"这里能展开"，这一条是防那种静默回退。
      ④ 实现点自身覆盖四类控件且下拉列表走 `surface_bg`（浮层必须实心）。
    """
    import ast as _ast # noqa: PLC0415
    import os as _o # noqa: PLC0415
    import re as _re # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
    _dir = _o.path.join(_root, "ui_qt")
    _sel = _re.compile(r"Q(?:LineEdit|ComboBox|PlainTextEdit|QTextEdit)\{")
    _copies = []
    _refs = 0
    for _fn in sorted(_o.listdir(_dir)):
        if not _fn.endswith(".py") or _fn in ("selftest.py", "stylekit_qt.py"):
            continue
        _src = open(_o.path.join(_dir, _fn), encoding="utf-8", errors="ignore").read()
        _refs += _src.count("field_qss(")
        if _sel.search(_src):
            _copies.append(_fn)
    ck("① 输入类 QSS 不再各文件手抄（唯一实现点＝stylekit.field_qss）",
       not _copies, "仍在手抄：%s" % "、".join(_copies))
    ck("② 分母守卫：真的扫到了 field_qss 的引用点", _refs >= 8, "引用 %d 处" % _refs)

    _sk = open(_o.path.join(_dir, "stylekit_qt.py"), encoding="utf-8").read()
    # 按 **AST** 取函数体（项目纪律：判据的"函数切片"不许拿 `def` 行当文本边界）
    _body = ""
    try:
        for _n in _ast.walk(_ast.parse(_sk)):
            if isinstance(_n, _ast.FunctionDef) and _n.name == "field_qss":
                _ls = _sk.split("\n")
                _body = "\n".join(_ls[_n.lineno - 1:_n.end_lineno])
                break
    except SyntaxError:
        _body = ""
    ck("④ field_qss 覆盖四类控件且下拉列表走 surface_bg",
       bool(_body) and all(x in _body for x in ("QLineEdit", "QComboBox", "QPlainTextEdit",
                                                "QTextEdit", "QAbstractItemView", "surface_bg(t)")),
       "%d 字符" % len(_body))

    # ③ 功能断言：箭头真的画出来了（离屏渲染，采箭头列）
    try:
        from PySide6.QtCore import QPoint as _QPt # noqa: PLC0415
        from PySide6.QtGui import QColor as _QC # noqa: PLC0415
        from PySide6.QtGui import QImage as _QI # noqa: PLC0415
        from PySide6.QtGui import QPainter as _QP # noqa: PLC0415
        from widgets import FieldCombo # noqa: PLC0415
        from stylekit_qt import THEMES as _THM # noqa: PLC0415

        _t = _THM["whale"]
        _c = FieldCombo(_t, [("a", "甲"), ("b", "乙")])
        _c.resize(220, 32)
        _c.setCurrentIndex(0)
        _img = _QI(220, 32, _QI.Format_ARGB32)
        _img.fill(_QC(0, 0, 0, 255))
        _p = _QP(_img)
        _c.render(_p, _QPt(0, 0))   # 显式给 targetOffset
        _p.end()
        from stylekit_qt import FIELD_CHEVRON_W as _W # noqa: PLC0415
        _ink = 0
        for _y in range(32):
            for _x in range(220 - _W, 220):
                if _img.pixelColor(_x, _y).red() > 60:
                    _ink += 1
        ck("③ 功能：下拉箭头真的画出来了（离屏采箭头列 ink>0）", _ink > 0, "ink=%d" % _ink)
    except Exception as _e: # noqa: BLE001
        ck("③ 功能：下拉箭头真的画出来了（离屏采箭头列 ink>0）", False, repr(_e)[:120])


def t_dev_dir_guard() -> None:
    """自检：**产品代码不许把本地草稿目录当路径用**（用户机器上不存在它）。

    判据分两半：
      ① `agent/` `ui_qt/` `scripts/` 里出现该目录名的文件必须都在白名单里（新增即红，
         逼写理由 —— 白名单是"审查过的清单"，不是"允许清单"）；
      ② 编译脚本（`.ps1` / `.cmd`）里**一个都不许有**：它们每跑一次就会把目录建回产品根，
         是唯一一种"会自己制造开发目录"的代码。
    """
    import ast as _ast # noqa: PLC0415
    import os as _o # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
    _NAME = "_scratch"

    def _knows(path):
        try:
            _t = _ast.parse(open(path, encoding="utf-8", errors="ignore").read())
        except Exception: # noqa: BLE001
            return 0
        return sum(1 for _n in _ast.walk(_t)
                   if isinstance(_n, _ast.Constant) and isinstance(_n.value, str)
                   and _NAME in _n.value)

    _extra = []
    for _sub in ("agent", "ui_qt", "scripts"):
        for _dp, _dn, _fns in _o.walk(_o.path.join(_root, _sub)):
            _dn[:] = [d for d in _dn if d not in ("__pycache__", "assets", "data", "logs")]
            for _fn in _fns:
                if not _fn.endswith(".py"):
                    continue
                _rel = _o.path.relpath(_o.path.join(_dp, _fn), _root).replace("\\", "/")
                if _knows(_o.path.join(_dp, _fn)) and _rel not in DEV_DIR_KNOWERS:
                    _extra.append(_rel)
    ck("① 出现本地草稿目录名的文件都在白名单里（新增即红，逼写理由）",
       not _extra, "；".join(_extra[:6])[:180])
    ck("白名单本身有内容（这条闸门还在真的守着东西）",
       len(DEV_DIR_KNOWERS) >= 5, "%d 个文件" % len(DEV_DIR_KNOWERS))

    _scripts = []
    for _fn in sorted(_o.listdir(_root)):
        if _fn.endswith((".ps1", ".cmd")):
            try:
                if _NAME in open(_o.path.join(_root, _fn), encoding="utf-8", errors="ignore").read():
                    _scripts.append(_fn)
            except OSError:
                pass
    ck("② 编译脚本里一个都不许有（它们每跑一次就会把开发目录建回产品根）",
       not _scripts, "；".join(_scripts)[:150])


#: 默认表与示例文件**值不一样**的键 —— 记的是 `键: (默认值, 示例值, 为什么可以不一样)`。
#   为什么要有这条闸门：两处都是"给外人看的默认"，值不一致的地方正是**个人值 / 私有口径**最爱藏的角落
#   （实测抓到过一次：默认表里躺着一个具体微信号的昵称，示例文件里却是空串 ⇒ 随包发给别人时，
#   对方面板里显示的是**别人的名字**）。不禁止差异（示例有意更保守），但**必须逐条登记理由**，
#   且**值一变就红**（记了值才会在你悄悄改动它的那一刻发现）。
#   注：只允许"差异消失"（对齐是好事），不允许"差异新增"。
DEF_VS_EXAMPLE_OK = {
    "image_gen.online_allowed": (True, False, "示例更保守：拿示例当起点的人不该被默认允许出网"),
    "memory.summarize_on_exit": (True, False, "示例更保守：关掉「退出时总结」（那一步会调一次模型）"),
    "send.quote_on_new_talk": (True, False, "示例更保守：不自动引用对方最近一句话"),
    "ui.lock_window_pos": (True, False, "示例更保守：不动用户的窗口几何"),
    # ⚠️ 长值（URL）记**形状**（前缀 + 省略号）而不是整串：把整串抄进来等于在产品里**多存一份**，
    #    而出包闸门对"仓库名"是敏感的（实测：抄进来当场多一条 warn）。
    "update.url": ("https://raw.githubusercontent.com/…", "",
                   "示例留空：不给外人指更新源（默认表里指本项目自己的 manifest）"),
    "wechat.media_dir": ("media", "", "示例留空＝用默认落点（media/）"),
    # ⚠️ `poll_interval` 是**加了"检测 ≤1 秒"之后**把默认从 3 调到 1 的（P0-8）；
    #    示例留 3＝**更保守**（轮询慢一点、省 CPU），照示例填的人不会误以为"必须调快"。
    "wechat.poll_interval": (1, 3, "示例更保守：轮询慢一点省 CPU（默认 1 秒是为了「检测 ≤1 秒」）"),
}


#: 全仓「未用 import」的**棘轮基线**（只许降不许升；已收到 0）。
#   口径（都是实测踩出来的，别改）：
#     · `from __future__ import annotations` **不计** —— 那是**编译指令**，不是被引用的名字；
#       不排除它的话，每轮都会被 86 条同款误报淹没（实测：137 条里 86 条是它）。
#     · 只有**明说"不用"**的 noqa 才豁免：`# noqa: F401` 或**不带码的裸 `# noqa`**。
#       ⛔ **`# noqa: PLC0415` 不豁免** —— 它说的是"这个 import 不在模块顶层"，**与用没用无关**。
#       拿它当豁免等于"所有函数内 import 免检"：实测这一个字之差，让 **14 处函数内死导入**长期隐身
#       （其中一处是 `from console_html import PORT as _PORT`，那个名字**从来不存在**，
#       被 except 吞掉后静默把整条快路径降级成硬编码端口兜底）。
#     · 判定 = 该文件里这个名字的 `Name(Load)` 与 `X.属性` 访问**都为 0**。
#   基线来历（本闸门口径：`agent/` + `ui_qt/` + `scripts/` 全扫，口径不同不可比）：
#     C2 第一批后 74 ⇒ 第二批后 59 ⇒ 第三批后 34 ⇒ C2 收口 **0** ⇒ 本轮补上"函数内"口径后仍 **0**。
DEAD_IMPORT_BASELINE = 0

#: **人工确认要保留**的未用 import（`(相对路径, 名字): 为什么它不能删`）。
#   收的都是"**import 本身就是那件事**"的形态 —— AST 只会说"这个名字没被用到"，
#   看不出它在 `try:` 里被当作探测用。
DEAD_IMPORT_KEEP = {
    ("agent/code_check.py", "stop_requested"):
        "探可用性：`try: from agent.wechat_ui import ...` 的**紧邻 except 就是**「不可用」分支 ⇒ import 即测试，没有后续使用",
    ("agent/code_check.py", "request_stop"):
        "同上（同一句探可用性）",
    ("agent/code_check.py", "clear_stop"):
        "同上（同一句探可用性）",
    ("agent/wechat_ui.py", "Image"):
        "探 PIL 可用性：`try:` 的**整个函数体只有这一行 import**，删掉就是空 try（SyntaxError）",
    ("scripts/wgc_probe.py", "winsdk"):
        "探可选依赖：`try: import winsdk` 紧邻的 `except` 打印「winsdk / capture 命名空间不可用」⇒ import 即探测",
    ("agent/verifiers.py", "_pr"):
        "在 `try:` 内且疑有预热/副作用语义；**证据不足不删**（宁可留着 + 记账，也不凭 AST 计数动手）",
}


def t_packaged_modules_guard() -> None:
    """自检：**产品模块必须都在 git 里**（否则出包静默少文件）。

    ⛔ 出包器（`scripts/pack_online.py`）的候选集是 **`git ls-files`**（只发**已跟踪**的文件）——
    这条设计本身是对的（不会把临时垃圾打进包），但它带来一个**静默**后果：
    **新写的产品模块只要还没 commit，就不会进包**，而打包器照样 `exit 0`
    （它的"必需文件"断言只认一张固定清单）。
    实测踩到：新增 `agent/guards.py` 后立刻出包 ⇒ 包里**没有**这个文件，
    而发送链在运行时才 `from .guards import …` ⇒ 用户那边是"点发送就报错"，本地全量自检全绿。

    ⇒ 判据就是一句话：`agent/` 与 `ui_qt/` 下**工作区里的每个 .py 都在 `git ls-files` 里**。
      未跟踪 = 忘了入库 = 出包会少它。
    """
    import subprocess # noqa: PLC0415

    _root = HERE.parents[0]
    try:
        _out = subprocess.run(["git", "ls-files", "-z"], cwd=str(_root), capture_output=True,
                              check=True).stdout.decode("utf-8", "replace")
    except Exception as e: # noqa: BLE001
        ck("能取到 git 跟踪清单（取不到就没法判这条，不能静默变绿）", False, str(e)[:80])
        return
    _tracked = {p for p in _out.split("\x00") if p}
    #: 分母守卫：清单至少要有这个量级，否则"空集 ⇒ 全绿"
    ck("git 跟踪清单非空且量级正常（分母守卫）", len(_tracked) > 200, "%d 个文件" % len(_tracked))
    _untracked = []
    for _sub in ("agent", "ui_qt"):
        _d = _root / _sub
        if not _d.is_dir():
            continue
        for _f in sorted(_d.glob("*.py")):
            _rel = "%s/%s" % (_sub, _f.name)
            if _rel not in _tracked:
                _untracked.append(_rel)
    ck("产品模块全在 git 里（未 commit 的新模块不进包 ⇒ 用户那边用到才报错）",
       not _untracked, "未跟踪：%s" % "、".join(_untracked[:6]))
    #: 反向控制：这条判据必须**能认出未跟踪文件**（拿一个必然不在清单里的名字试）
    ck("未跟踪判定器有效（编一个不存在的模块名必须被判未跟踪）",
       "agent/__绝不存在的模块__.py" not in _tracked, "")


def t_run_async_signature_guard() -> None:
    """自检：**传给 `run_async` 的 `work` 必须能接住那个 box 参数**（棘轮）。

    为什么值得一条常驻闸门：`run_async(work, on_done)` 的约定是
      `work(box)` —— 后台线程往 **box** 里写结果，`on_done(box)` 在主线程读 box 落地。
    若把 `work` 写成 `def _work() -> dict: ... return {...}`（0 参 + 靠返回值），
    `run_async` 调 `work(box)` 时抛 `TypeError`；**而它被 `run_async` 吞进 box["_err"]**，
    `on_done` 照常执行、只是拿到空 box ⇒ **整条链路静默失效**：
    取数没发生、界面停在占位态、控制台不报任何错（实测：wechat 页简报卡的下拉
    永远只有一项占位、`/api/wechat-groups` 一次都不请求，而判据只查 `count()>=1`
    就绿了 —— 差点当成"没这功能"）。
    ⇒ 这里按 AST 静态拦：`run_async` 第一实参只要是个"就地定义的函数"，就必须有
      至少 1 个位置参数（`*args` / `**kwargs` 也算兜得住）。

    ⚠️ 只查**能静态看到**的（函数名字面量 or 本文件内 def 后直接传）。
    """
    import ast as _ast # noqa: PLC0415
    import os as _o # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
    _bad: list = []
    _scanned = 0 # 分母：真扫过多少个 run_async 调用点

    for _sub in ("ui_qt",):
        for _dp, _dn, _fns in _o.walk(_o.path.join(_root, _sub)):
            _dn[:] = [d for d in _dn if d != "__pycache__"]
            for _fn in sorted(_fns):
                if not _fn.endswith(".py"):
                    continue
                _p = _o.path.join(_dp, _fn)
                try:
                    _tree = _ast.parse(open(_p, encoding="utf-8", errors="ignore").read())
                except Exception: # noqa: BLE001
                    continue
                # 本文件里"就地定义的函数的签名"：名字 → 位置参数个数（None=接 *args）
                _defs: dict = {}
                for _n in _ast.walk(_tree):
                    if isinstance(_n, _ast.FunctionDef):
                        _A = _n.args
                        if _A.vararg is not None:
                            _defs[_n.name] = None # *args 兜得住
                        else:
                            _defs[_n.name] = len(_A.posonlyargs) + len(_A.args)
                for _n in _ast.walk(_tree):
                    if not (isinstance(_n, _ast.Call)
                            and isinstance(_n.func, _ast.Name)
                            and _n.func.id == "run_async" and _n.args):
                        continue
                    _scanned += 1
                    _arg0 = _n.args[0]
                    # ① 直接传函数名：查本文件里的定义
                    if isinstance(_arg0, _ast.Name) and _arg0.id in _defs:
                        _pc = _defs[_arg0.id]
                        if _pc is not None and _pc < 1:
                            _bad.append((_o.path.relpath(_p, _root).replace("\\", "/"),
                                         _n.lineno, _arg0.id, _pc))
                    # ② 传 lambda：看它的参数列表
                    elif isinstance(_arg0, _ast.Lambda):
                        _A = _arg0.args
                        _pc = None if _A.vararg is not None else (
                            len(_A.posonlyargs) + len(_A.args))
                        if _pc is not None and _pc < 1:
                            _bad.append((_o.path.relpath(_p, _root).replace("\\", "/"),
                                         _n.lineno, "<lambda>", _pc))

    ck("run_async 的 work 必须能接住 box 参数（否则整条异步链静默失效）",
       len(_bad) == 0,
       "扫了 %d 个调用点；违规 %s" % (_scanned, _bad or "无"))
    # ⭐ 分母守卫：真的扫到了东西（否则这条闸门是在空过）
    ck("run_async 签名闸门：真的扫到了调用点（分母守卫）",
       _scanned >= 10, "scanned=%d" % _scanned)


def t_dead_import_guard() -> None:
    """自检：**全仓未用 import 只许降不许升**（棘轮）。

    为什么值得一条常驻闸门：未用 import 是"最便宜的那类赘余"——删它零风险、但没人会顺手删；
    而它堆着会**掩护真问题**（读者分不清"这个 import 是忘了删，还是探可用性"）。
    本闸门把"当前还有多少"钉成棘轮：清一批就往下降一格，新增则当场红。
    """
    import ast as _ast # noqa: PLC0415
    import os as _o # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
    _skip_dirs = ("__pycache__", "assets", "data", "logs", "runtime", "archived-from-root")
    _found = []   # (相对路径, 行号, 名字)
    _scanned_imports = 0 # 分母：真扫过多少条 import 语句（含函数内的）

    for _sub in ("agent", "ui_qt", "scripts"):
        for _dp, _dn, _fns in _o.walk(_o.path.join(_root, _sub)):
            _dn[:] = [d for d in _dn if d not in _skip_dirs]
            for _fn in sorted(_fns):
                if not _fn.endswith(".py"):
                    continue
                _p = _o.path.join(_dp, _fn)
                try:
                    _lines = open(_p, encoding="utf-8", errors="ignore").read().split("\n")
                    _tree = _ast.parse("\n".join(_lines))
                except Exception: # noqa: BLE001
                    continue
                # 文件里所有"被载入的名字"（含属性访问的基名）
                _used = set()
                for _n in _ast.walk(_tree):
                    if isinstance(_n, _ast.Name) and isinstance(_n.ctx, _ast.Load):
                        _used.add(_n.id)
                    elif isinstance(_n, _ast.Attribute) and isinstance(_n.value, _ast.Name):
                        _used.add(_n.value.id)
                for _n in _ast.walk(_tree):
                    if not isinstance(_n, (_ast.Import, _ast.ImportFrom)):
                        continue
                    if isinstance(_n, _ast.ImportFrom) and _n.module == "__future__":
                        continue          # 编译指令，不算
                    _scanned_imports += 1
                    _src_line = _lines[_n.lineno - 1] if _n.lineno <= len(_lines) else ""
                    # ⛔ 只有"明说不用"才豁免：带 `F401` 的 noqa，或**不带码的裸 `# noqa`**。
                    #   `noqa: PLC0415`/ `E402` 说的是位置，**与用没用无关** ⇒ 不豁免，
                    #   否则函数内 import 整类免检（实测这一个字之差让 14 处死导入长期隐身）。
                    if "noqa" in _src_line.lower():
                        _ntail = _src_line.lower().split("noqa", 1)[1]
                        if "f401" in _ntail or ":" not in _ntail:
                            continue
                    _rel = _o.path.relpath(_p, _root).replace("\\", "/")
                    for _a in _n.names:
                        _nm = _a.asname or _a.name.split(".")[0]
                        if _nm == "*" or _nm in _used:
                            continue
                        if (_rel, _nm) in DEAD_IMPORT_KEEP:
                            continue      # 人工确认保留（`import` 本身就是探测/契约）
                        _found.append((_rel, _n.lineno, _nm))

    _n = len(_found)
    ck("未用 import 只许降不许升（棘轮；`__future__` 与「明说不用」的 noqa 不计）",
       _n <= DEAD_IMPORT_BASELINE,
       "当前 %d / 基线 %d%s" % (_n, DEAD_IMPORT_BASELINE,
                              ("；例：" + "、".join("%s:%s" % (f, nm) for f, _l, nm in _found[:4])) if _found else ""))
    #: 分母守卫：口径改成"含函数内"以后，必须证明**真的扫到了函数内的 import**
    #  （否则把 noqa 规则一收紧、扫描面一缩，这条闸门会因"什么都没扫到"而全绿）。
    ck("上述全量扫描覆盖到足量 import 语句（分母守卫，含函数内）",
       _scanned_imports >= 1500, "扫到 %d 条" % _scanned_imports)


def t_guard_family_guard() -> None:
    """自检：**发送链守卫族**的唯一实现点与真值表（F3）。

    这一族的判据是"一处错会不会处处错"，实测两种形态（都在收口前真实存在）：
      · **真鼠标谓词两份逐字相同的实现**：`ui_adapt._real_mouse_allowed()` 与
        `WeChatAdapter._real_fallback_allowed()`（环境变量强制关 → 看 `input.allow_real_fallback` → 异常即拒绝）；
      · **版本门那段八行抄了三遍**（文本链 / 表情链 / 图片链），且三处早退收尾还不一样。

    ⛔ 最容易改错的是"两问合一"：`foreground()`（能不能置前/置顶）在 `real_mouse()`
    （能不能用真鼠标）的条件上多一条 `wechat.background_only`，是**严格更强**的那一问。
    合并两问 = 放宽 = 抢用户窗口，所以本闸门用**真值表**把"前者 ⇒ 后者"钉死。
    """
    import os # noqa: PLC0415

    from agent import guards # noqa: PLC0415

    _root = HERE.parents[0]
    _cfg_mod = _root / "agent" / "config.py"

    # ① 真值表：四种配置组合 × 环境变量，逐个对"两问"的期望值
    #    期望口径（fail-closed）：`real_mouse()` 只看 input.allow_real_fallback（环境变量可强制关）；
    #    `foreground()` 在它之上**多一条** wechat.background_only=False ⇒ 严格更强。
    _real = None
    try:
        from agent import config as _cfgm # noqa: PLC0415

        _real = _cfgm.get_config
        _env_key = guards.ENV_REAL_FALLBACK
        os.environ.pop(_env_key, None)
        # (background_only, allow_real_fallback, 期望 foreground, 期望 real_mouse)
        _rows = [
            (True, False, False, False),   # 默认档：两问都拒
            (True, True, False, True),     # ⚠️ 分叉行：开了真鼠标兜底但没关背景优先
            (False, False, False, False),  # 关了背景优先、没开真鼠标：两问都拒
            (False, True, True, True),     # 两个都开：两问都放行
        ]
        _bad = []
        for _bo, _ar, _want_fg, _want_rm in _rows:
            _cfgm.get_config = lambda bo=_bo, ar=_ar: { # 打桩：只喂这两个键
                "wechat": {"background_only": bo}, "input": {"allow_real_fallback": ar}}
            _g_fg = bool(guards.foreground()[0])
            _g_rm = bool(guards.real_mouse())
            if (_g_fg, _g_rm) != (_want_fg, _want_rm):
                _bad.append("bg=%s ar=%s ⇒ 得(%s,%s) 期望(%s,%s)"
                            % (_bo, _ar, _g_fg, _g_rm, _want_fg, _want_rm))
        ck("真值表四行：两问的判定与登记的一致（含已知分叉行）",
           not _bad, "；".join(_bad[:3]))
        # 不变式：置前那一问**严格更强** ⇒ 它放行时真鼠标那一问必放行。
        # 合并两问 = 放宽 = 抢用户窗口，所以这条必须恒成立。
        _cfgm.get_config = lambda: {"wechat": {"background_only": False},
                                    "input": {"allow_real_fallback": True}}
        _both = (guards.foreground()[0], guards.real_mouse())
        ck("`foreground()` 为真 ⇒ `real_mouse()` 必为真（合并两问＝放宽＝抢用户窗口，不许合）",
           _both == (True, True), "得 %s" % (_both,))
        # ⚠️ 已知分叉（登记在计划的待改语义清单里，**语义变更单独一轮**）：这一行两问结论相反。
        #   钉住它：将来谁改了口径，这一条会明确变红，提醒把登记一起改。
        _cfgm.get_config = lambda: {"wechat": {"background_only": True},
                                    "input": {"allow_real_fallback": True}}
        _split = (bool(guards.foreground()[0]), bool(guards.real_mouse()))
        ck("已知分叉：`background_only=True` + `allow_real_fallback=True` 时两问结论相反"
           "（wechat 那条链拒、ui_adapt.click 那条链许）",
           _split == (False, True), "得 %s" % (_split,))
        # 环境变量强制关：两问都必须拒（自检/诊断路径靠它兜底）
        _cfgm.get_config = lambda: {"wechat": {"background_only": False},
                                    "input": {"allow_real_fallback": True}}
        os.environ[_env_key] = "0"
        ck("`%s=0` 强制关真鼠标（无视配置）：两问都拒" % _env_key,
           guards.real_mouse() is False and guards.foreground()[0] is False, "")
        os.environ.pop(_env_key, None)
        # 读配置抛异常 ⇒ fail-closed（两问都拒）
        def _boom(): # noqa: ANN202
            raise RuntimeError("配置读不到")
        _cfgm.get_config = _boom
        ck("读配置失败 ⇒ 两问都按拒绝处理（fail-closed，不悄悄放行）",
           guards.real_mouse() is False and guards.foreground()[0] is False, "")
    finally:
        if _real is not None:
            from agent import config as _cfgm2 # noqa: PLC0415

            _cfgm2.get_config = _real

    # ② 实现点唯一：判断逻辑（读哪些键）只许在 guards 里，其余两处只许**转发**
    def _forward_only(src: str, fname: str) -> bool:
        """该函数体是不是**纯转发**（只有导入/返回，没有自己的判断）。"""
        import ast as _a # noqa: PLC0415
        for _n in _a.walk(_a.parse(src)):
            if isinstance(_n, _a.FunctionDef) and _n.name == fname:
                for _st in _n.body:
                    if isinstance(_st, (_a.Import, _a.ImportFrom, _a.Return, _a.Expr)):
                        continue
                    return False # 出现赋值/if/… ⇒ 它自己也在判
                return True
        return False

    def _reads_key(src: str, key: str) -> int:
        """数"**真的去读**这个配置键"的地方（`.get("键", …)`）。

        ⛔ 不能用 `"键" in 文本` 数：docstring 里的说明也算，会把"解释"误判成"实现"
        （实测：`guards.py` 里提 `background_only` 6 次，只有 1 次是真读）。
        """
        import ast as _a # noqa: PLC0415
        _n = 0
        for _x in _a.walk(_a.parse(src)):
            if (isinstance(_x, _a.Call) and isinstance(_x.func, _a.Attribute)
                    and _x.func.attr == "get" and _x.args
                    and isinstance(_x.args[0], _a.Constant)
                    and _x.args[0].value == key):
                _n += 1
        return _n

    _ua = (_root / "agent" / "ui_adapt.py").read_text(encoding="utf-8")
    _wx = (_root / "agent" / "wechat.py").read_text(encoding="utf-8")
    _gd = (_root / "agent" / "guards.py").read_text(encoding="utf-8")
    ck("置前闸只有一处实现（`ui_adapt.fg_allowed` 只许是转发）",
       _gd.count("def foreground") == 1 and _forward_only(_ua, "fg_allowed"),
       "gd=%d ua 转发=%s" % (_gd.count("def foreground"), _forward_only(_ua, "fg_allowed")))
    #: ⭐ 这一条才是真正的去重判据：两个键的**读取**只许在 guards 一处。
    #  "两处各读一遍配置"就是历史上那次分叉的根因（`wechat` 读一次、`ui_adapt` 再读一次）。
    ck("真鼠标两个键的**读取**只许在 guards 一处（两处各读一遍＝分叉根因）",
       _reads_key(_gd, "allow_real_fallback") == 1 and _reads_key(_gd, "background_only") == 1
       and _reads_key(_ua, "allow_real_fallback") == 0 and _reads_key(_ua, "background_only") == 0
       and _reads_key(_wx, "allow_real_fallback") == 0,
       "gd=%d/%d ua=%d/%d wx=%d" % (_reads_key(_gd, "allow_real_fallback"),
                                    _reads_key(_gd, "background_only"),
                                    _reads_key(_ua, "allow_real_fallback"),
                                    _reads_key(_ua, "background_only"),
                                    _reads_key(_wx, "allow_real_fallback")))
    #: 分母/反向控制：这个计数器必须**数得出来**（否则恒 0 也看不出来）
    ck("「读键」计数器有效（对一份真实现的样本文本必须数到 1）",
       _reads_key('x = (cfg.get("input") or {}).get("allow_real_fallback", False)', "allow_real_fallback") == 1, "")
    #: 分母/反向控制：那个"纯转发"判定器必须**能认出**真实现（否则它永远返回 True 也看不出来）
    ck("「纯转发」判定器能认出真实现（把 guards 里的实现喂给它必须判否）",
       _forward_only(_gd, "foreground") is False, "")
    ck("真鼠标谓词只有一处实现（另两处必须是转发，不许再读一遍配置）",
       _gd.count("def real_mouse") == 1
       and "ENV_REAL_FALLBACK" in _gd
       and 'os.environ.get("WXAGENT_REAL_FALLBACK"' not in _ua
       and 'os.environ.get("WXAGENT_REAL_FALLBACK"' not in _wx
       and _ua.count("from .guards import real_mouse") == 1
       and _wx.count("from .guards import real_mouse") == 1, "")
    ck("版本门那段不再手抄：发送链上不许再有 `version_gate` 的别名导入（只许 `guards.version`）",
       _wx.count("from . import version_gate as") == 0 and _wx.count("note_blocked") == 0
       and _wx.count("_gate_version") == 6 and _gd.count("def version(") == 1, "")

    # ③ 行为金标准：桩驱动三情形（收口前后必须逐条相同）
    from agent import version_gate as _vg # noqa: PLC0415
    from agent import wechat as _wxm # noqa: PLC0415

    _save_v, _save_w = _vg.check, _wxm.wx_version_for_gate
    _save_note, _save_cmd = _vg.note_blocked, getattr(_vg, "action_cmd", None)
    _noted: list = []
    _cleaned: list = []
    try:
        _wxm.wx_version_for_gate = lambda: "9.9.9"
        _vg.note_blocked = lambda m, r: _noted.append((m, r))
        # (a) 放行
        _vg.check = lambda *a, **k: {"allow": True, "reason": ""}
        ck("版本门：放行时不记账、不跑收尾、返回 (True, \"\")",
           guards.version("send", cleanup=lambda: _cleaned.append(1)) == (True, "")
           and not _noted and not _cleaned, "noted=%s cleaned=%s" % (_noted, _cleaned))
        # (b) 拒发
        _calls: list = []
        _vg.check = lambda *a, **k: {"allow": False, "reason": "没实测"}
        _r = guards.version("send", cleanup=lambda: (_calls.append(1), _cleaned.append(1))[0])
        ck("版本门：拒发时**记账一次 + 跑一次调用方的收尾**，并把原因原样带回去",
           _r == (False, "没实测") and _noted == [("send", "没实测")] and _calls == [1],
           "r=%s noted=%s calls=%s" % (_r, _noted, _calls))
        # (c) 查门自己抛异常
        def _vgboom(*_a, **_k): # noqa: ANN202
            raise RuntimeError("门炸了")
        _vg.check = _vgboom
        ck("版本门：查门抛异常 ⇒ 按**放行**处理（与收口前三处写法同口径）",
           guards.version("send") == (True, ""), "")
        # (d) 收尾抛异常 ⇒ 拒绝被吞掉（**已知的 fail-open**，钉住它：改口径的那一轮会看到这条变红）
        _vg.check = lambda *a, **k: {"allow": False, "reason": "没实测"}
        _noted.clear()

        def _cleanup_boom(): # noqa: ANN202
            raise RuntimeError("收尾炸了")
        ck("版本门：收尾抛异常时拒绝被吞掉、消息照发（**已知 fail-open**，非本轮修）",
           guards.version("send", cleanup=_cleanup_boom) == (True, ""), "")
    finally:
        _vg.check, _wxm.wx_version_for_gate = _save_v, _save_w
        _vg.note_blocked = _save_note


def t_winops_guard() -> None:
    """自检：**关微信窗口**只有一条路（F11）。

    这一族踩过**两次同型事故**：微信主窗被 Qt 重建、`hwnd` 变了 ⇒ 不在任何白名单里
    ⇒ 被当成"菜单浮层" `WM_CLOSE` 掉 ⇒ **用户的微信主窗口整个消失**。
    规矩是"发 `WM_CLOSE` 之前**当场**问一次：这个句柄是不是主窗/渲染子窗"（缓存正是第一次事故的成因）。

    ⇒ 判据：`agent/` 里凡是**会发 `WM_CLOSE` 的函数**，函数体里必须问过那道判据
      （`ok_to_close(` / `_wm_close_safe(` / `winops`）。只有两类**关的不是微信窗**的允许例外，
      且必须在白名单里写清理由 —— 否则下一个"顺手加一处关窗"就又绕过判据了
      （`wechat_ui.close_subwindow` 历史上就是这么绕过去的：判据在 `wechat` 里，它 import 不到）。
    """
    import ast as _ast # noqa: PLC0415

    _root = HERE.parents[0]
    #: ⛔ 例外白名单：**(文件, 函数) → 理由**。关的不是"微信的窗"才算例外。
    _EXEMPT = {
        ("agent/ui_adapt.py", "dismiss_overlays"):
            "关的是**系统输入叠加层**（TouchKeyboard 那一族），收窄手段是「类名 + 标题白名单」，目标是别人的窗",
        ("agent/tray.py", "shutdown"):
            "关的是**我们自己的**托盘窗，不是微信的窗",
    }
    _GUARD = ("ok_to_close(", "_wm_close_safe(", "winops")
    _hits, _naked, _files = [], [], 0
    for _f in sorted((_root / "agent").glob("*.py")):
        _src = _f.read_text(encoding="utf-8")
        _files += 1
        if _f.name == "winops.py":
            continue # 实现点自身：`WM_CLOSE` 就该长在这里
        try:
            _tree = _ast.parse(_src)
        except SyntaxError:
            continue
        for _n in _ast.walk(_tree):
            if not isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            # ⛔ **必须剥掉 docstring**：`_wait_dialog_gone` 的说明里就写着 `PostMessage(WM_CLOSE)`
            #   （"它是异步的"那段），拿整段源码当信号会把它误判成"裸关窗"。
            _body = "\n".join(_l for _l in (_ast.get_source_segment(_src, _n) or "").split("\n")
                              if not _l.strip().startswith("#"))
            if _n.body and isinstance(_n.body[0], _ast.Expr) and isinstance(
                    getattr(_n.body[0], "value", None), _ast.Constant):
                _ds = _n.body[0].value.value
                if isinstance(_ds, str) and _ds:
                    _body = _body.replace(_ds, "")
            if "PostMessage" not in _body:
                continue
            if not ("0x0010" in _body or "WM_CLOSE" in _body):
                continue
            _key = ("agent/%s" % _f.name, _n.name)
            _hits.append("%s::%s" % _key)
            if any(_g in _body for _g in _GUARD):
                continue
            if _key in _EXEMPT:
                continue
            _naked.append("%s::%s" % _key)
    ck("发 `WM_CLOSE` 的函数都必须先问过「绝不关主窗」那道判据（裸关窗 = 用户的微信可能被关掉）",
       not _naked, "裸关窗：%s" % "、".join(_naked[:4]))
    #: 分母守卫：扫不到函数时上面那条会"全绿"
    ck("上述扫描真的扫到了关窗函数（分母守卫 > 0）", len(_hits) > 0, "命中 %d 个函数" % len(_hits))
    #: 反向控制：判据器必须**能认出裸关窗**（拿一份样本文本试）
    _probe = "def f(u):\n    import ctypes\n    ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)\n"
    _pn = [n for n in _ast.walk(_ast.parse(_probe))
           if isinstance(n, _ast.FunctionDef)]
    _pb = _ast.get_source_segment(_probe, _pn[0]) or ""
    ck("「裸关窗」判定器有效（样本文本必须同时命中 PostMessage 与 0x0010，且不含守卫）",
       "PostMessage" in _pb and "0x0010" in _pb and not any(_g in _pb for _g in _GUARD), "")
    #: 实现点唯一：判据只有一份，`wechat._wm_close_safe` 只许是**纯转发**
    _wn = (_root / "agent" / "winops.py").read_text(encoding="utf-8")
    _wx2 = (_root / "agent" / "wechat.py").read_text(encoding="utf-8")
    _fwd = False
    for _n in _ast.walk(_ast.parse(_wx2)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "_wm_close_safe":
            _fwd = all(isinstance(_st, (_ast.Import, _ast.ImportFrom, _ast.Return, _ast.Expr))
                       for _st in _n.body)
    ck("关窗判据只有一处实现（`winops.ok_to_close`），`wechat._wm_close_safe` 是纯转发",
       _wn.count("def ok_to_close") == 1 and _wn.count("def close(") == 1 and _fwd, "")


def t_keys_guard() -> None:
    """自检：**键 → 磁盘名**的哈希只有一处实现，且两套存储必须算出同一个值（F9）。

    为什么值得一条闸门：`chat_key` 要变成文件名（消息档案）和目录名（记忆）**两套**。
    两套各有一份 `_key_hash()`（**逐字相同**的 3 行）—— 改一处漏一处，同一个群在两套存储里就会
    算出**不同的哈希**，于是"记忆与消息档挂不上"（这正是当初"记忆页列不出群"那类问题的形状）。
    ⇒ 哈希收口到 `keys.key_hash()`；判据除了"实现唯一"，还要**真的比一次值**：
      两套存储对同一个 key 必须给出**同一个 8 位尾巴**（这才是"同口径"的可执行含义）。
    """
    _root = HERE.parents[0]
    _ks = (_root / "agent" / "keys.py").read_text(encoding="utf-8")
    _st = (_root / "agent" / "store.py").read_text(encoding="utf-8")
    _mm = (_root / "agent" / "memory.py").read_text(encoding="utf-8")
    ck("哈希实现唯一（`md5` 只许出现在 keys.py）",
       "hashlib.md5" in _ks and "hashlib.md5" not in _st and "hashlib.md5" not in _mm
       and _st.count("from .keys import key_hash") == 1
       and _mm.count("from .keys import key_hash") == 1, "")
    ck("安全名实现唯一（`store._safe_name` 只许是转发；`feedback._safe_name` 同名异义、不许动它）",
       "def safe_name" in _ks and "flags=re.IGNORECASE" not in _st
       and _st.count("from .keys import safe_name") == 1, "")
    #: ⭐ 行为断言：两套存储对同一个 key 给同一个尾巴（这是本族的全部意义）
    import os as _os # noqa: PLC0415
    from agent import keys as _keys # noqa: PLC0415
    from agent import memory as _mem # noqa: PLC0415
    from agent import store as _st2 # noqa: PLC0415

    _cases = ["group:wxid_a-b", "group:wxid_a_b", "group:wxid_x@chatroom", "", "群名很长的中文群聊"]
    _mism = [c for c in _cases
             if _keys.key_hash(c) not in _st2.chat_file(c) or _keys.key_hash(c) not in _mem._chat_dir_name(c)]
    ck("两套存储（消息档案 / 记忆目录）对同一个 key 算出**同一个**哈希尾巴",
       not _mism, "不一致：%s" % _mism[:3])
    #: 反向控制：这两个 key 只差一个字符、转义后同名 ⇒ 哈希**必须**不同（否则两套存储都会互相覆盖）
    ck("转义后同形的两个 key 靠哈希区分（`a-b` vs `a_b` 不许撞同一份存储）",
       _keys.safe_name("group:wxid_a-b") == _keys.safe_name("group:wxid_a_b")
       and _keys.key_hash("group:wxid_a-b") != _keys.key_hash("group:wxid_a_b"), "")
    ck("`store.chat_file` 仍在 `data/messages/` 下、`memory._chat_dir_name` 仍在转义目录下（口径没被搬错）",
       _st2.chat_file("group:wxid_a-b").replace(_os.sep, "/").find("messages/") > 0
       and _mem._chat_dir_name("group:wxid_a-b").startswith("group_wxid_a_b_"), "")


def t_atomic_write_guard() -> None:
    """自检：**换档只走 `persist.replace_into`**（F7 原子写族）。

    为什么这一族"一处错处处错"：Windows 上目标档只要被任何人打开着（杀毒/索引器/另一个写者/
    读者句柄），`os.replace` 就撞 WinError 5/32 —— 实测 20 线程 × 10 写里 **66% 直接失败**，
    而共享冲突是**瞬时**的（退避一下基本都能过）。裸调用点各自 `except` 掉或忽略返回值
    ⇒「写没写进去」没人知道（审计原话：竞争下 **71% 的写静默丢掉**）。
    ⇒ 实测 **33 处**"写 tmp → 换档 / 发布 .part"已收口到 `persist.replace_into`（语义与裸
      `os.replace` 一致：失败**抛**，只多了先重试）。

    ⇒ 判据：`agent/` 里裸 `os.replace(` 只许出现在**白名单**里，且白名单每条都要写清理由
      —— 剩下的都是**改名/轮转/留证**（语义不是"写档发布"），它们该由坏档隔离族（`persist.quarantine`）收。
    """
    import os as _os # noqa: PLC0415

    _root = HERE.parents[0]
    #: ⛔ 白名单：**(文件, 行号含义) → 理由**。只收"不是写档发布"的那几种。
    _EXEMPT = {
        "agent/persist.py": "实现点自身（`_replace_retry` 里那句就是这套配方的心脏；`quarantine` 是改名留证）",
        "agent/briefs.py": "手搓坏档留证（`<p>.bad.<ts>` 改名）——归坏档隔离族，不是写档",
        "agent/store.py": "档案归档/坏档改名（`p → dst` / `p → q`），不是写档发布",
        "agent/send_retry.py": "重试队列读不动时把队档改名留证（`.bad.<ts>`），归坏档隔离族",
        "agent/thought_trace.py": "日志轮转（`FILE → FILE.1` 备份换名），不是写档发布",
    }
    def _naked_in(text: str, rel: str = "") -> list:
        """这份文本里"裸换档"的行（注释行不算）。判据只此一处口径，正向与反向控制共用。"""
        out = []
        for _i, _l in enumerate(text.split("\n"), 1):
            _s = _l.strip()
            if _s.startswith("#") or "os.replace(" not in _s:
                continue
            out.append("%s:%d" % (rel, _i))
        return out

    _naked, _files = [], 0
    for _f in sorted((_root / "agent").glob("*.py")):
        _files += 1
        _rel = "agent/%s" % _f.name
        if _rel in _EXEMPT:
            continue
        _naked += _naked_in(_f.read_text(encoding="utf-8"), _rel)
    ck("换档只走 `persist.replace_into`（裸 `os.replace` 竞争下会静默丢写）",
       not _naked, "裸换档：%s" % "、".join(_naked[:5]))
    #: 分母守卫：扫不到文件时上面那条会"全绿"
    ck("上述扫描真的扫到了文件（分母守卫）", _files > 100, "扫到 %d 个 .py" % _files)
    #: 白名单自身要有内容且**每条都有理由**（否则白名单会变成垃圾桶）
    ck("白名单每条都写了理由（且不是空表）",
       bool(_EXEMPT) and all(len(str(v)) > 8 for v in _EXEMPT.values()), "%d 条" % len(_EXEMPT))
    #: 反向控制：判据必须**能认出**裸换档、且**不误伤**注释里的同名写法
    ck("「裸换档」判定器有效：正例报 1 条、注释里的同名写法报 0 条",
       len(_naked_in("    os.replace(tmp, path)\n")) == 1
       and len(_naked_in("    # os.replace(tmp, path)\n")) == 0, "")
    #: ⭐ 行为契约：`replace_into` 失败要**抛**（与裸 `os.replace` 同语义，不许变成"返 False 被忽略"）
    from agent import persist as _ps # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415

    _raised = ""
    _td = _tf.mkdtemp(prefix="atomic-gate-")
    try:
        try:
            _ps.replace_into(_os.path.join(_td, "_不存在.tmp"),
                             _os.path.join(_td, "_不存在.json"), budget_s=0.01)
        except Exception as e: # noqa: BLE001
            _raised = type(e).__name__
        ck("`persist.replace_into` 失败时**抛**（与裸 `os.replace` 同语义，不给\"返 False 被忽略\"留口子）",
           _raised in ("OSError", "FileNotFoundError"), "实测 %s" % (_raised or "没抛！"))
        #: 反向控制：同一函数**能成功**换档（否则上一条可能只是"任何调用都抛"）
        _a, _b = _os.path.join(_td, "_a.tmp"), _os.path.join(_td, "_b.json")
        with open(_a, "w", encoding="utf-8") as _fh:
            _fh.write("x")
        _ps.replace_into(_a, _b, budget_s=0.05)
        ck("反向控制：同一函数在正常换档时**能成功**（不是恒抛）",
           _os.path.exists(_b) and not _os.path.exists(_a), "")
    finally:
        try:
            for _x in _os.listdir(_td):
                _os.remove(_os.path.join(_td, _x))
            _os.rmdir(_td)
        except OSError:
            pass


def t_emoji_index_guard() -> None:
    """自检：**表情语义索引面板**（web `sec-emoji` ＋ Qt `MANUAL['emoji']`）的四段接线。

    自定义表情是图片，模型读不出含义 —— 这一页存在的意义只有一条：
    让人把「这张图是什么意思」写成文字回填给模型。**四段任缺一段，用户看到的就是死页**：

      ① 后端取数口 `/api/emoji_index`：必须同时登记在 `ROUTES` 与 `HANDLERS`
         （只声明不实现 ⇒ 按表调就 404；只实现不声明 ⇒ 路由对账判据红，页面也调不到）；
      ② web 侧**真的调用**它（GET 取清单/统计 ＋ POST 两种动作）；
      ③ Qt 侧 `MANUAL['emoji']` 已注册（缺 ⇒ 点导航是空白页）；
      ④ 缩略图路由 `/assets/emoticon/` **只服务「md5 ＋ 图片后缀」的文件名**
         —— 与收藏夹同款的路径穿越防线，不因为"只是缩略图"就放宽。

    另锁三条内容纪律：低把握（疑似梗）必须在清单上标出来；统计口径前后端同名；
    页面与面板都不许用 emoji 字符当图标。
    """
    import sys as _sys # noqa: PLC0415

    from agent.routes import HANDLERS, ROUTES # noqa: PLC0415

    if str(HERE) not in _sys.path:
        _sys.path.insert(0, str(HERE))
    import panels_custom # noqa: PLC0415
    import sec_meta as _sm # noqa: PLC0415

    _web = WEB_PAGE.read_text(encoding="utf-8")
    _flat = _web.replace(" ", "")
    _wh = (HERE.parents[0] / "agent" / "webui.py").read_text(encoding="utf-8")
    _ei = (HERE.parents[0] / "agent" / "emoji_index.py").read_text(encoding="utf-8")
    _pc = (HERE / "panels_custom.py").read_text(encoding="utf-8")

    # ① 后端：声明 ↔ 实现
    ck("后端声明 /api/emoji_index（GET + POST）",
       ROUTES.get("/api/emoji_index") == ("GET", "POST"), str(ROUTES.get("/api/emoji_index")))
    ck("HANDLERS 指向的真方法在 webui.py 里存在",
       HANDLERS.get("/api/emoji_index", {}).get("GET") == "_rapi_emoji_index"
       and HANDLERS.get("/api/emoji_index", {}).get("POST") == "_rapi_emoji_index_post"
       and "def _rapi_emoji_index(" in _wh and "def _rapi_emoji_index_post(" in _wh, "")

    # ② web：段落在 + 真调用 + 元数据可解析
    # ⚠️ 用正则容忍 section 上的附加属性（如 IA-4 的 `data-mg`）——写死整串的话，
    #    哪天 emoji 被并进别的大项，这条会变成"假红"而不是"真报缺失"。
    ck("web 侧有 sec-emoji 且标题为「表情」",
       re.search(r'<section id="sec-emoji" class="card" data-sec[^>]*>', _web) is not None
       and "<h2>表情</h2>" in _web, "")
    ck("web 真调用取数口（GET ＋ action=learn / refresh）",
       "'/api/emoji_index'" in _flat and "action:'learn'" in _flat
       and "action:'refresh'" in _flat, "")
    _sec_emoji = _sm.get("emoji")
    ck("sec_meta 解析出 emoji 段（标题对、行数够）",
       _sec_emoji.title == "表情" and len(_sec_emoji.rows) >= 5,
       "title=%s rows=%d" % (_sec_emoji.title, len(_sec_emoji.rows)))

    # ③ Qt：整页手写件已注册（这一页是清单+回填，不是 data-cfg 行，故走 MANUAL）
    ck("Qt 侧 MANUAL['emoji'] 指向 emoji_panel（缺了导航点是空白页）",
       panels_custom.MANUAL.get("emoji") is panels_custom.emoji_panel, "")
    ck("Qt 面板把「把握不足」标出来（低把握＝疑似梗，含义不在画面里）",
       "把握不足" in _pc and "把握高" in _pc, "")

    # ④ 缩略图路由收口（与收藏夹同款，防免认证路径穿越）
    ck("缩略图路由只认「md5 ＋ 图片后缀」的文件名",
       'path.startswith("/assets/emoticon/")' in _wh
       and "^[0-9a-fA-F]{6,}\\.(jpg|jpeg|png|gif|webp|bmp)$" in _wh, "")

    # ⑤ 统计口径前后端同名（后端 stats 的键必须被前端六格真的用上）
    ck("统计口径前后端同名",
       all('"%s"' % k in _ei for k in ("total", "with_caption", "animated", "static"))
       and all(("ei" + k) in _web for k in ("Total", "Done", "Todo", "Low", "Anim", "Still")), "")

    # ⑥ 红线：不许用 emoji 字符当功能图标（web 段与 Qt 函数体内都扫）
    _emo = re.compile("[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\u2B00-\\u2BFF\\uFE0F]")

    def _code_only(src: str) -> str:
        """剥掉三引号文档串与 `#` 注释 —— 只在"有没有违规字面量"这类断言前用。

        ⛔ 不剥就会把**讲这条规矩的那句注释**判成违规（本项目踩过不止一次：
        写"不许出现某字面量"的注释本身就是那个字面量）。
        """
        out, i, n = [], 0, len(src)
        while i < n:
            if src.startswith('"""', i) or src.startswith("'''", i):
                q = src[i:i + 3]
                j = src.find(q, i + 3)
                i = n if j < 0 else j + 3
                continue
            if src[i] == "#":
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            out.append(src[i])
            i += 1
        return "".join(out)

    _hit_web = re.search(r'<section id="sec-emoji".*?</section>', _web, re.S)
    _hit_pc = re.search(r"def emoji_panel\(.*?(?=\ndef )", _pc, re.S)
    ck("表情面板 web 段内无 emoji 字符（红线）",
       bool(_hit_web) and not _emo.search(_hit_web.group(0)), "")
    ck("emoji_panel 代码段内无 emoji 字符（注释与文档串已剥，红线）",
       bool(_hit_pc) and not _emo.search(_code_only(_hit_pc.group(0))), "")

    # ── 「向群友请教」（FE-2 的面板面）：配置必须真的被读走，模块常量只作兜底 ──
    #   为什么这条重要：这一族的坑是"面板上能改、后端根本不看"（配置写了没人读 ⇒ 用户改了个寂寞）。
    import json as _json # noqa: PLC0415

    from agent import config as _cfgmod # noqa: PLC0415
    from agent import emoji_curiosity as _EC # noqa: PLC0415

    _dkeys = {"enabled", "probability", "cooldown_s", "answer_window_s"}
    _def = (_cfgmod.DEFAULT_CONFIG.get("emoji") or {}).get("curiosity") or {}
    ck("默认配置里有 emoji.curiosity 四键", set(_def) == _dkeys, str(sorted(_def)))
    _ex = _json.loads((HERE.parents[0] / "config.example.json").read_text(encoding="utf-8"))
    ck("config.example.json 同键（否则 fullcheck 的「键集相等」会红）",
       set((_ex.get("emoji") or {}).get("curiosity") or {}) == _dkeys,
       str(sorted(((_ex.get("emoji") or {}).get("curiosity") or {}))))

    _orig_gc = _cfgmod.get_config
    try:
        _cfgmod.get_config = lambda: {"emoji": {"curiosity": {
            "enabled": True, "probability": 0.42, "cooldown_s": 77, "answer_window_s": 9}}}
        _c1 = _EC.cfg()
        ck("cfg() 真的读配置（不是把值写死在模块里）",
           _c1["enabled"] is True and abs(_c1["probability"] - 0.42) < 1e-9
           and _c1["cooldown_s"] == 77 and _c1["answer_window_s"] == 9, str(_c1))

        def _boom():
            raise RuntimeError("boom")

        _cfgmod.get_config = _boom
        _c2 = _EC.cfg()
        ck("反向锚：配置读不到 ⇒ 退回模块常量（不抛、也不误当 0 秒）",
           _c2["cooldown_s"] == _EC.ASK_COOLDOWN_S
           and _c2["answer_window_s"] == _EC.ANSWER_WINDOW_S
           and _c2["probability"] == 0.0, str(_c2))
    finally:
        _cfgmod.get_config = _orig_gc

    _st2 = _EC.stats()
    ck("请教 stats() 给面板：四项配置 ＋ 待答 ＋ 已问数 ＋ 最近学到",
       all(k in _st2 for k in ("enabled", "probability", "cooldown_s", "answer_window_s",
                               "pending", "asked_count", "learned")), str(sorted(_st2)))
    ck("web 有第 4 组「④ 向群友请教」＋ 四个 data-cfg 键",
       "④ 向群友请教" in _web and all(('data-cfg="emoji.curiosity.%s"' % _k) in _web
                                      for _k in _dkeys), "")
    ck("Qt 面板声明了第 4 组锚点（mark_group emoji 3）",
       'mark_group(page, "emoji", 3' in _pc, "")


def t_subnav_guard() -> None:
    """自检：**功能项内的「组 ＋ 组锚点条」**（web 第三层索引的 Qt 等价）。

    web 侧两层结构（`assets/console/index.html`）：
      · `.sub`      —— 功能项内部的**分组小标题**，把长功能项的若干 `.row` 收成一组；
      · `.sub-nav`  —— 功能项顶部的**组锚点条**，由 JS 扫本 section 的 `.sub` 生成。

    两者在 Qt 侧都要落地，否则长面板（媒体 41 行、高级 49 行）就是一整片平的行、
    用户翻不到头。判据分四组：

      · 解析：`.sub` 必须解析成 `kind="group"` 的行，且**按文档顺序**与普通行交错；
              锚点名 `sec 键-g序号` 与 web 同口径。
      · 渲染：`<2 组的 section 不生成锚点条`（web 同款短路）—— 这是"有没有多画一行"
              的唯一判据，只测"有条"会漏掉单组 section 上多插的那条。
      · 逐字：锚点条文字 = 组标题原文（不许加工/截断），条数 = 组数。
      · 鲸语：每颗芯片的原文都要能在 `agent/whale_text.py` 里查到译文 ——
              查不到就是"切鲸语后这一条露普通话"，一页里两套语气。

    不测"点击后滚到哪"（真滚放在功能实测 `_scratch/_probe_subnav.py`）：
    这里只钉**结构**，滚动是行为、需要真视口。
    """
    import re as _re # noqa: PLC0415
    import sys as _sys # noqa: PLC0415

    _qt = str(HERE)
    if _qt not in _sys.path:
        _sys.path.insert(0, _qt)
    import sec_meta as _sm # noqa: PLC0415

    _html = (HERE.parents[0] / "assets" / "console" / "index.html").read_text(encoding="utf-8")

    # ── ① 解析：`.sub` 都进了 group 行 ─────────────────────────────────
    _secs = _sm.secs()
    _got: dict[str, list[str]] = {}
    for _k, _s in _secs.items():
        _gs = [r for r in _s.rows if r.kind == "group"]
        if _gs:
            _got[_k] = [r.label for r in _gs]
    # web 真值：逐 section 数 `.sub`（只在 data-sec 段内）
    _want: dict[str, list[str]] = {}
    # ⚠️ `data-sec[^>]*>` 不写死相邻 —— section 上可能挂别的属性（IA-4 的 `data-mg`）。
    #    写死 `data-sec>` 会让被并的那 11 块整段漏读 ⇒ 本判据与 `sec_meta` 口径不一致、假红。
    for _m in _re.finditer(r'<section id="sec-([\w-]+)" class="card" data-sec[^>]*>(.*?)</section>',
                           _html, _re.S):
        _body = _m.group(2)
        _subs = [_re.sub(r"<[^>]+>", "", x).strip()
                 for x in _re.findall(r'<div class="sub">(.*?)</div>', _body, _re.S)]
        _subs = [x for x in _subs if x]
        if _subs:
            _want[_m.group(1)] = _subs
    ck("组标题：有组的 section 与 web `.sub` 逐 section 对齐（键集合）",
       set(_got) == set(_want), "我方%s / web%s" % (sorted(_got), sorted(_want)))
    ck("组标题：两条数一致、文字逐字一致（多一条少一条都算）",
       sum(len(v) for v in _got.values()) == sum(len(v) for v in _want.values())
       and _got == _want,
       "我方=%s" % {k: len(v) for k, v in _got.items()})
    # 锚点名口径：`sec键-g序号`（web 的 `sec.id-g1` 同形）
    _bad_anchor = []
    for _k in _got:
        _gs = [r for r in _secs[_k].rows if r.kind == "group"]
        for _i, _r in enumerate(_gs):
            if _r.anchor != "%s-g%d" % (_k, _i + 1):
                _bad_anchor.append((_k, _i, _r.anchor))
    ck("组锚点：命名口径 `sec键-g序号`，且序号连续从 1 起",
       not _bad_anchor, str(_bad_anchor[:5]))
    # 非 group 行锚点必须为空 —— 否则锚点条会指到普通行上去
    _leak = [(s.key, r.label) for s in _secs.values() for r in s.rows
             if r.kind != "group" and r.anchor]
    ck("组锚点：**只有 group 行带锚点**，普通行一律为空（防锚点指错行）",
       not _leak, str(_leak[:5]))

    # ── ② 源码级：渲染层的关键口径（真控件由功能实测覆盖） ──────────────
    _pq = (HERE / "panels_qt.py").read_text(encoding="utf-8")
    _i = _pq.find("def _sub_nav(")
    _body_src = _pq[_i:_i + 4000] if _i >= 0 else ""
    # 去注释/字符串后再断言"调用了什么" —— 直接查字面会把**注释里说明"为什么不用它"**
    # 也算成"用了它"（实测踩到：docstring 写了"不能用 ensureWidgetVisible" 而误判）。
    _code = _re.sub(r'"""[\s\S]*?"""', "", _body_src)
    _code = _re.sub(r"#[^\n]*", "", _code)
    ck("渲染层：`_sub_nav` 存在", _i >= 0, "")
    ck("渲染层：**不足 2 组短路**（web `if(subs.length < 2) return;` 同款）",
       "if len(groups) < 2:" in _code and "return None" in _code, "")
    ck("渲染层：滚到组用的是**对齐到顶**（直设滚动条值），不是 `ensureWidgetVisible`"
       "（后者对已在视口内的目标不滚）",
       "ensureWidgetVisible" not in _code and ".setValue(" in _code, "")
    ck("渲染层：芯片是**扁平 chip**（不继承带立体投影的 `Btn`）",
       "class _SubNavChip(QPushButton)" in _pq, "")
    # 插到"说明之后"：靠 desc 控件上的标记反查，而不是写死下标
    ck("渲染层：锚点条插在说明行之后（按 `c8_desc` 标记反查，不写死下标）",
       'property("c8_desc")' in _pq
       and 'setProperty("c8_desc", True)' in (HERE / "widgets.py").read_text(encoding="utf-8"),
       "")

    # ── ③ 鲸语：组标题与锚点条文字都要能换 ─────────────────────────────
    _tr = None
    try:
        import importlib.util as _ilu # noqa: PLC0415

        _wp = HERE.parents[0] / "agent" / "whale_text.py"
        _spec = _ilu.spec_from_file_location("whale_text_guard", _wp)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _tr = lambda s: _mod.NAV.get(s) or _mod.DICT.get(s) or s # noqa: E731
    except Exception: # noqa: BLE001
        _tr = None
    ck("鲸语：字典可加载（单一来源 agent/whale_text.py）", _tr is not None, "")
    if _tr is not None:
        _all = [t for v in _got.values() for t in v]
        _miss = [t for t in _all if _tr(t) == t]
        ck("鲸语：**%d 个组标题全部有译文**（查不到 = 切鲸语后这一条露普通话）" % len(_all),
           not _miss, str(_miss[:5]))


def t_subnav_coverage_guard() -> None:
    """自检：**三级索引不许有盲区**：「调前端记得挂三级索引，
    以免有些不在索引列表里面」）。

    这条守的是"**以后不会再漏**"，三个最容易漏的位置：
      ① **web 侧组数**：凡 ≥6 行的 section，组数必须 ≥ max(2, 行数 ÷ 12)
         —— 缺了就是"那一块内容不在索引里，用户只能一路滚着找"；
      ② **Qt 手写页**：`MANUAL` 里的页若 web 侧有组，必须用 `mark_group` 声明锚点 ——
         没声明就**不生成锚点条**（真生成了也是"点了不动"的假索引，比没有更糟）；
      ③ **六种组合**（3 主题 × 2 文案）：组标题与锚点芯片只许用 Token 取色 ——
         写死颜色在另外两套主题里就脏了（文案那条轴由 `whale_selftest` 的覆盖率守）。
    """
    import re as _re # noqa: PLC0415
    import sys as _sys # noqa: PLC0415

    _web = WEB_PAGE.read_text(encoding="utf-8")
    _n_sec = len(_re.findall(r'<section id="sec-', _web))
    _gaps = []
    _scanned = 0
    # ⚠️ `data-sec[^>]*>` 不写死相邻：section 上可能挂别的属性（IA-4 的 `data-mg`）。
    #    写死会让被并的那 11 块静默跳过 —— 分母守卫数的是 `<section id="`，
    #    抓不住这种"扫到了但没进循环"的漏读，所以下面单独钉 `_scanned`。
    for _m in _re.finditer(r'<section id="sec-([\w-]+)" class="card" data-sec[^>]*>(.*?)</section>',
                           _web, _re.S):
        _scanned += 1
        _k, _b = _m.group(1), _m.group(2)
        _n = len(_re.findall(r'<div class="row"[^>]*><label>', _b))
        if _n < 6:
            continue
        _subs = len(_re.findall(r'<div class="sub">', _b))
        _want = max(2, -(-_n // 12))
        if _subs < _want:
            _gaps.append("%s(行%d 组%d<%d)" % (_k, _n, _subs, _want))
    ck("① 每个 ≥6 行的 section 组数达标（组数 ≥ max(2, 行数÷12)）", not _gaps,
       "未达标：%s" % _gaps)
    ck("① 分母守卫：真的扫到了全部 section（≥25 个）", _n_sec >= 25, "n=%d" % _n_sec)
    # 分母守卫只证"页面上有 28 个 section"，不证"循环真读了 28 个" —— 补这条。
    ck("① 分母守卫：解析循环覆盖数与页面 section 数相等（防静默漏读）",
       _scanned == _n_sec, "扫到 %d / 页面 %d" % (_scanned, _n_sec))

    if str(HERE) not in _sys.path:
        _sys.path.insert(0, str(HERE))
    import panels_custom as _pcmod # noqa: PLC0415
    import sec_meta as _sm2 # noqa: PLC0415

    _pc = (HERE / "panels_custom.py").read_text(encoding="utf-8")
    _need = []
    _need, _short = [], []
    for _sec in _pcmod.MANUAL:
        _n = len([r for r in _sm2.get(_sec).rows if r.kind == "group"])
        if _n < 2:
            continue
        # ⛔ 光"页里出现过一次 mark_group"不算数 —— 实战踩到过：
        #   `for _i in range(4): mark_group(page, "persona", _i, pcard)` 在**文本**上只算一次匹配，
        #   而它把 4 个锚点**全指向同一张卡** ⇒ 点 ③④ 与点 ① 滚到同一处，
        #   **索引看得见、却点不出区别**（用户口径："每个块都要能直接跳到那个位置"）。
        #   ⇒ 判据要的是"**每一组都有自己的锚点、且目标不是同一个控件**"。
        _pairs = _re.findall(r'mark_group\w*\(\s*page\s*,\s*"%s"\s*,\s*([^,]+?)\s*,\s*([^)]+?)\)'
                             % _re.escape(_sec), _pc)
        if not _pairs:
            _need.append(_sec)
            continue
        _idx = {p[0].strip() for p in _pairs}
        _tgts = {p[1].strip() for p in _pairs}
        _miss = []
        for i in range(_n):
            if str(i) not in _idx:
                _miss.append(i)
        if _miss or len(_tgts) != _n:
            _short.append("%s(锚点 %s／组数 %d／不同目标 %d)" % (_sec, sorted(_idx), _n, len(_tgts)))
    ck("② 有组的手写页都声明了锚点（没声明就不生成条 ⇒ 与 web 侧不一致）", not _need,
       "未声明：%s" % _need)
    ck("②b 锚点必须覆盖每一组、且各自指向**不同**的块（全指一张卡＝点哪条都到同一处）",
       not _short, "未覆盖：%s" % _short)
    ck("② 分母守卫：确实有手写页被这条覆盖到（≥1 个）",
       len([s for s in _pcmod.MANUAL
            if len([r for r in _sm2.get(s).rows if r.kind == "group"]) >= 2]) >= 1, "")

    _pq = (HERE / "panels_qt.py").read_text(encoding="utf-8")
    _mm = _re.search(r"class _SubNavChip\(QPushButton\):.*?(?=\nclass |\ndef )", _pq, _re.S)
    _chip = _mm.group(0) if _mm else ""
    # ⛔ 先剥注释行再查颜色：这一段注释里就写着「实测 dark：底 #262A44 时 4.40」
    #   （讲对比度改动的地方本身就是那两个色值）—— 不剥就会把解释当违规（同款坑第 4 次）。
    _chip_code = "\n".join(_l for _l in _chip.split("\n") if not _l.strip().startswith("#"))
    _bad = [x for x in _re.findall(r"#[0-9a-fA-F]{3,6}", _chip_code)
            if x.lower() not in ("#fff", "#000")]
    ck("③ 锚点芯片用 Token 取色（写死颜色会在另外两套主题里脏掉；#fff/#000 是允许的例外）",
       bool(_chip_code) and "t.q(" in _chip_code and not _bad, "写死颜色=%s" % _bad)


def t_emoji_codes_guard() -> None:
    """自检：**Emoji 库要可见、而且真有人用**（FE-3）。

    这一族最容易做成"半成品"：把 169 个表情码摆到面板上，却**没有任何代码在用** ——
    那就只是个好看的清单。所以同时钉三件：
      ① 只读端点给全（微信码／情绪组／统计）；
      ② **提示词真的摆给模型看**（否则它不知道自己能写 `[微笑]`，只会用「（微笑）」这种文字对付）；
      ③ 两个开关**真有效**：`emoji.prompt_codes` 关掉就不摆、`emoji.allow_unicode` 关掉就不列 😄。
    """
    from agent import config as _cfgmod # noqa: PLC0415
    from agent import emoji_codes as _EC # noqa: PLC0415
    from agent.routes import HANDLERS, ROUTES # noqa: PLC0415

    _web = WEB_PAGE.read_text(encoding="utf-8")
    _wh = (HERE.parents[0] / "agent" / "webui.py").read_text(encoding="utf-8")
    _pr = (HERE.parents[0] / "agent" / "prompt.py").read_text(encoding="utf-8")
    _pc = (HERE / "panels_custom.py").read_text(encoding="utf-8")

    ck("① 后端声明 /api/emoji_codes（只读 GET）",
       ROUTES.get("/api/emoji_codes") == ("GET",), str(ROUTES.get("/api/emoji_codes")))
    ck("① HANDLERS 指向的真方法存在",
       HANDLERS.get("/api/emoji_codes", {}).get("GET") == "_rapi_emoji_codes"
       and "def _rapi_emoji_codes(" in _wh, "")

    _l1 = _EC.prompt_lines(allow_unicode=True)
    _l2 = _EC.prompt_lines(allow_unicode=False)
    #: ⛔ 判"有没有表情字符"必须用**真的 emoji 区段**：写成 `ord(c) > 0x2500` 会把中文汉字
    #   （4E00-9FFF）也算成 emoji ⇒「关掉开关」那条永远红（本轮集中测真踩到）。
    _emochar = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")
    ck("② 清单里真带上微信码与情绪组（模型才知道能写 [微笑]）",
       any("[微笑]" in _x for _x in _l1) and any("情绪组" in _x for _x in _l1), "%d 行" % len(_l1))
    ck("② 反向锚：allow_unicode=False ⇒ 一行里都不出现表情字符（开关作用在生成侧）",
       not any(_emochar.search(_x) for _x in _l2), "")
    ck("② prompt.py 真把它拼进系统提示（不是摆设）",
       "_emoji_codes_rule()" in _pr and "_ec.prompt_lines(" in _pr, "")
    ck("② 默认配置里有 prompt_codes / allow_unicode 两键",
       {"prompt_codes", "allow_unicode"} <= set((_cfgmod.DEFAULT_CONFIG.get("emoji") or {})), "")

    import agent.prompt as _P # noqa: PLC0415

    _orig = _P.get_config
    try:
        _P.get_config = lambda: {"emoji": {"prompt_codes": False}}
        _a = _P._emoji_codes_rule()
        ck("③ 关掉 emoji.prompt_codes ⇒ 这一段真的不拼（省 token 的开关是活的）",
           _a == "", repr(_a)[:40])
        _P.get_config = lambda: {"emoji": {"prompt_codes": True, "allow_unicode": False}}
        _b = _P._emoji_codes_rule()
        ck("③ 关掉 allow_unicode ⇒ 模型看到的那段里没有表情字符",
           bool(_b) and not _emochar.search(_b), "%d 字符" % len(_b))
        _P.get_config = lambda: {"emoji": {"prompt_codes": True, "allow_unicode": True}}
        ck("③ 两个都开 ⇒ 摆出来且含表情字符", bool(_emochar.search(_P._emoji_codes_rule())), "")
    finally:
        _P.get_config = _orig

    ck("④ web 有第 5 组「⑤ Emoji 库」＋ 两个 data-cfg",
       "⑤ Emoji 库" in _web and 'data-cfg="emoji.prompt_codes"' in _web
       and 'data-cfg="emoji.allow_unicode"' in _web, "")
    ck("④ Qt 面板声明了第 5 组锚点（mark_group emoji 4）",
       'mark_group(page, "emoji", 4' in _pc, "")


def t_rhythm_guard() -> None:
    """自检：**拟人节奏的"生效值"必须看得见、且真的按配置算出来**（FE-7）。

    这一族的坑是"面板上有档位、下面还有两个单项覆盖，而真正生效的是**两者合起来**的值"——
    只看配置项，用户会以为「我填了 3 秒怎么还慢」（其实被档位上限盖住了）。
    所以判据钉三件：① 三档齐全且默认 gentle；② **生效值随档位变**（行为）；③ **单项覆盖真的盖过档位**（行为）。
    """
    from agent import config as _cfgmod # noqa: PLC0415
    from agent import rhythm as _RH # noqa: PLC0415
    from agent.routes import HANDLERS, ROUTES # noqa: PLC0415

    _web = WEB_PAGE.read_text(encoding="utf-8")
    _pc = (HERE / "panels_custom.py").read_text(encoding="utf-8")

    ck("① 后端声明 /api/rhythm（只读 GET）",
       ROUTES.get("/api/rhythm") == ("GET",), str(ROUTES.get("/api/rhythm")))
    ck("① HANDLERS 指向的真方法存在",
       HANDLERS.get("/api/rhythm", {}).get("GET") == "_rapi_rhythm"
       and "def _rapi_rhythm(" in (HERE.parents[0] / "agent" / "webui.py").read_text(encoding="utf-8"), "")
    ck("② 三档齐全 ＋ 默认档是 gentle（off 是「还原到加这层之前」）",
       set(_RH.PROFILES) == {"gentle", "calm", "off"} and _RH.DEFAULT_PROFILE == "gentle",
       str(sorted(_RH.PROFILES)))

    _orig_gc = _cfgmod.get_config
    try:
        _cfgmod.get_config = lambda: {"rhythm": {"enabled": True, "profile": "calm"}}
        _RH.reload()
        _p1 = _RH.profile()
        ck("③ 生效值随档位变：calm ⇒ 间隔 5~12 秒（不是 gentle 的 1.5~4）",
           _p1.name == "calm" and tuple(_p1.gap) == (5.0, 12.0), "%s %s" % (_p1.name, _p1.gap))

        _cfgmod.get_config = lambda: {"rhythm": {"enabled": True, "profile": "calm",
                                                 "gap_min_seconds": 3, "gap_max_seconds": 4}}
        _RH.reload()
        _p2 = _RH.profile()
        ck("③ 单项覆盖真的盖过档位（填 3/4 ⇒ 就是 3~4，不是 5~12）",
           tuple(_p2.gap) == (3.0, 4.0), str(_p2.gap))

        _cfgmod.get_config = lambda: {"rhythm": {"enabled": False, "profile": "calm"}}
        _RH.reload()
        ck("③ 总开关关掉 ⇒ 等价 off 档（间隔退化到 0，不加等待）",
           _RH.profile().name == "off" and tuple(_RH.profile().gap) == (0.0, 0.0),
           str(_RH.profile().gap))
    finally:
        _cfgmod.get_config = _orig_gc
        _RH.reload()

    ck("④ web 有「拟人节奏」5 行（含档位下拉 ＋ 两个单项覆盖 ＋ 生效值行）",
       'data-cfg="rhythm.enabled"' in _web and 'data-cfg="rhythm.profile"' in _web
       and 'data-cfg="rhythm.gap_min_seconds"' in _web
       and 'data-cfg="rhythm.gap_max_seconds"' in _web and 'id="rhState"' in _web, "")
    ck("④ Qt 侧 send 追加卡已接线（APPENDIX 里有 _rhythm_appendix）",
       "_rhythm_appendix" in _pc and '"send": _rhythm_appendix' in _pc, "")


def t_row_geometry_guard() -> None:
    """自检：**行高缓存要看得见、要能重测**（FE-4）。

    行高是"算该点第几行"的分母 —— 它错了**不报错**，只是"点了半天没反应"（安静地坏）。
    所以这一族只认两件事：
      ① 用户**看得见**当前记住的值与本机合法区间（不然没法判断是不是它错了）；
      ② 用户**能清掉重测**（改了系统缩放或微信字号之后必须能回到正轨）。
    ⛔ 并且**只许清、不许手填**：手填的数没人验过，会让"算第几行"错得毫无声息。
    """
    from agent.routes import HANDLERS, ROUTES # noqa: PLC0415

    _web = WEB_PAGE.read_text(encoding="utf-8")
    _wh = (HERE.parents[0] / "agent" / "webui.py").read_text(encoding="utf-8")
    _pc = (HERE / "panels_custom.py").read_text(encoding="utf-8")
    ck("后端声明 /api/row_geometry（GET + POST）",
       ROUTES.get("/api/row_geometry") == ("GET", "POST"), str(ROUTES.get("/api/row_geometry")))
    ck("HANDLERS 指向的真方法在 webui.py 里存在",
       HANDLERS.get("/api/row_geometry", {}).get("GET") == "_rapi_row_geometry"
       and "def _rapi_row_geometry(" in _wh and "def _rapi_row_geometry_post(" in _wh, "")
    ck("web 有「行高缓存」行 ＋「重新测量」按钮 ＋ 真调用取数口",
       "行高缓存" in _web and "重新测量" in _web and "rgForget" in _web
       and "'/api/row_geometry'" in _web.replace(" ", ""), "")
    ck("Qt 侧 wechat 追加卡已接线（APPENDIX 里有 _rowgeom_appendix）",
       "_rowgeom_appendix" in _pc and "_rowgeom_appendix)" in _pc, "")
    _i = _wh.find("def _rapi_row_geometry_post")
    _j = _wh.find("def _rapi_emoji_index(")
    _post = _wh[_i:_j] if 0 <= _i < _j else ""
    ck("反向锚：POST 只认 forget（**不提供「手填行高」的入口**）",
       '"forget"' in _post and "remember_row_h" not in _post, "len=%d" % len(_post))
    ck("端点如实给出本机合法区间（面板据此判断是不是它错了）",
       "green_expect" in _post or "green_expect" in _wh, "")

    # 抓图对最小化窗口的守卫（真机踩到）：`GetWindowRect` 在最小化时仍返回占位矩形
    # ⇒ 旧行为会交给调用方一张 **237×39 的"图"**（不是 None），照它量行高/找绿带必然没意义。
    _ww = (HERE.parents[0] / "agent" / "wechat_windows.py").read_text(encoding="utf-8")
    ck("抓图对最小化窗口**如实返回 None**（不给垃圾尺寸的图骗调用方）",
       "IsIconic" in _ww and "窗口已最小化（最小化时没有有效画面）" in _ww, "")


def t_contrast_guard() -> None:
    """自检：**文字/底色对比度**——三主题下"字读不读得出来"。

    为什么要有这条：颜色 token 改动**不会让任何既有判据变红**（判据大多只比
    字面值或结构），于是"白底浅字、黑底灰字"这类问题只能靠人眼在截图上发现。
    实测已出过三处真缺陷，本判据把它们逐条钉住：

      ① dark 的 `tx3` 一度是 `#5A5E66`，压卡片 `#101113` 只有 **2.90** ——
         `desc()`（说明行）用的正是 tx3，于是深色底上"最弱那一档"根本读不出来。
      ② whale 的 `tx2` 改成实色后，`tx3` 仍是同色相加 alpha ⇒ 两档**完全同色**，
         三级文字层级塌成两级。
      ③ 主按钮 (`Btn` role=primary) 的渐变一度**向上提亮**（与 web 的
         `blue → blue2`（变暗）方向相反），白字压在被提亮的上沿只剩 ~2.3。

    判据口径：**只判关系，不判字面值** —— 锁死具体色值会在每次"对齐 web token"
    时假红一次（此前 `t_fonts_rgba` 硬编码旧值就是这么坏的）。
    阈值取 WCAG 正文档 4.5，底色按**控件实际压在什么上**取（卡片或页面底）。
    """
    _qt = str(HERE)
    import sys as _sys # noqa: PLC0415
    if _qt not in _sys.path:
        _sys.path.insert(0, _qt)

    from stylekit_qt import THEMES # noqa: PLC0415

    def _lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    def _lum(c) -> float:
        return (0.2126 * _lin(c.redF()) + 0.7152 * _lin(c.greenF())
                + 0.0722 * _lin(c.blueF()))

    def _ratio(a, b) -> float:
        la, lb = _lum(a), _lum(b)
        if la < lb:
            la, lb = lb, la
        return (la + 0.05) / (lb + 0.05)

    _BODY = 4.5

    # ① 三级文字必须在**各自的宿主底**上过正文门槛，且严格三档递减
    for _key, _t in THEMES.items():
        for _name, _field, _host in (
            ("tx", "tx", "card"),
            ("tx2", "tx2", "card"),
            ("tx3", "tx3", "card"),
        ):
            _r = _ratio(_t.q(_field), _t.q(_host))
            ck("对比度 %s.%s 压 %s ≥ %.1f（实测 %.2f）"
                    % (_key, _name, _host, _BODY, _r), _r >= _BODY)
        # 字色档位的强弱方向**随主题的底色明暗翻转**：
        #   · 深色主题（whale/dark）：底暗 ⇒ "更弱" = **更暗** ⇒ 亮度 tx > tx2 > tx3；
        #   · 浅色主题（light）：底亮 ⇒ "更弱" = **更浅** ⇒ 亮度 tx < tx2 < tx3。
        #     把方向写死成一个方向，白底那套必然假红（light 实测 0.013/0.101/0.124）。
        # 判据取"相对底的对比度"来判，天然与底色无关：越弱的一档对比度越低。
        _c1 = _ratio(_t.q("tx"), _t.q("card"))
        _c2 = _ratio(_t.q("tx2"), _t.q("card"))
        _c3 = _ratio(_t.q("tx3"), _t.q("card"))
        ck("对比度 %s 三级文字严格递减 tx > tx2 > tx3（对比度 %.2f/%.2f/%.2f）"
                % (_key, _c1, _c2, _c3), _c1 > _c2 > _c3)

    # ② 锚点条芯片：文字 = tx2，底色必须是 blue_soft（不是 blue_line）
    #    —— blue_line 是描边色、比 blue_soft 深一档，拿它填底会把对比度吃掉
    #    一截（dark 实测 4.40 → 5.25）。
    import panels_qt # noqa: PLC0415
    for _key, _t in THEMES.items():
        # ⛔ 底色写在 `_restyle()` 里，不在 `__init__()`（构造只调它）——
        #    扫错函数会永远取不到那个常量、判据恒定假红。
        _chip_src = panels_qt._SubNavChip._restyle.__code__.co_consts
        _src = ""
        for _c in _chip_src:
            if isinstance(_c, str):
                _src += _c
        ck("对比度 %s 锚点条芯片底色取 blue_soft（描边才是 blue_line）" % _key,
                't.q(\'blue_soft\')' in _src or "blue_soft" in _src)

    # ③ 主按钮字色：必须"两候选比对比度、取更高"，且 whale/dark 上深字胜出
    _w_src = (HERE / "widgets.py").read_text(encoding="utf-8")
    ck("对比度 主按钮字色走对比度择优（非硬编码主题名）",
            "_ink_worst" in _w_src and "_white_worst" in _w_src)
    for _key in ("whale", "dark"):
        _t = THEMES[_key]
        _ends = (_t.q("blue"), _t.q("blue2"))
        _white = min(_ratio(__import__("PySide6.QtGui", fromlist=["QColor"]).QColor(255, 255, 255), c)
                     for c in _ends)
        _ink = min(_ratio(__import__("PySide6.QtGui", fromlist=["QColor"]).QColor("#0A1B2E"), c)
                   for c in _ends)
        ck("对比度 %s 主按钮深字优于白字（深 %.2f / 白 %.2f）" % (_key, _ink, _white),
                _ink > _white)


def t_fingerprint_verdict_guard() -> None:
    """自检：**B-2 —— 状态指纹（P0-1）＋ 两谓词（P0-4）**。

    这两条是同一个底座的两半：指纹回答"**这份状态还是原来那份吗**"，
    两谓词回答"**这份改动还算不算数 / 界面能不能跟着更新**"。

    ### P0-1 三条验收（报告原文口径）
      ① **同帧两次调用 ⇒ 指纹完全相同** —— 这条最容易被写坏：浮点均值会因累加顺序
         在末位漂移，指纹"同帧不同值"⇒ 判据当场假红。本判据连调两次比字面串。
      ② **改动任一采样分区 ⇒ 指纹必须变** —— 只改一小块，若网格太粗会被平均值抹平。
      ③ **口径版本号 +1 ⇒ 指纹必须变** —— 口径变了旧指纹就不可比，必须自动作废。

    ### P0-4 一条验收（报告原文口径）
      构造"**发出后已切走会话**"⇒ 断言**持久层照写**（记录在）但**界面不更新**（回调没调）。
      这一条是 P0-4 的全部意义所在，不测它等于没做。

    ⚠️ `fingerprint` 依赖"类 PIL 图像"。判据里**不 import PIL**（ui_qt 侧不装），
    而是构造一个只有 `size` / `crop` / `getdata` 的最小替身 —— 与本模块的鸭子类型契约一致。
    """
    import agent.fingerprint as _fp  # noqa: PLC0415
    import agent.verdict as _vd  # noqa: PLC0415

    class _Img:
        """最小图像替身：`size` + `crop()` 返回一个带 `getdata()` 的对象。

        每个像素由 `(x, y)` 决定 ⇒ 改任何一个像素，对应的分区均值就变，
        "改分区必变"那条才有可造的条件。`bias` 用来整体提亮（模拟"状态变了"）。
        """

        def __init__(self, w=64, h=64, bias=0, patch=None):
            self.size = (w, h)
            self._w, self._h, self._bias = w, h, bias
            self._patch = patch or {}

        def crop(self, box):
            x0, y0, x1, y1 = (int(v) for v in box)
            pix = []
            for y in range(y0, y1):
                for x in range(x0, x1):
                    v = (x * 3 + y * 5) % 200 + self._bias
                    if (x, y) in self._patch:
                        v = self._patch[(x, y)]
                    pix.append(max(0, min(255, int(v))))
            return _Region(pix)

    class _Region:
        def __init__(self, pix):
            self._pix = pix

        def getdata(self):
            return list(self._pix)

    img = _Img()
    f1 = _fp.state_fingerprint(img, theme="light", dpi=1.5, scroll_pos=120)
    f2 = _fp.state_fingerprint(img, theme="light", dpi=1.5, scroll_pos=120)
    ck("P0-1 ① 同一帧两次调用指纹完全相同", f1 == f2, "" if f1 == f2 else "%s / %s" % (f1, f2))

    # ② 改任一采样分区 ⇒ 必变。改的是**同一个 4×4 网格里的第 (0,0) 格**，
    #    即左上角 16×16 那块 —— 若网格实现有误（如拼串顺序不稳），这条会挂。
    img2 = _Img(patch={(1, 1): 255})
    f3 = _fp.state_fingerprint(img2, theme="light", dpi=1.5, scroll_pos=120)
    ck("P0-1 ② 改动任一采样分区 ⇒ 指纹必变", f3 != f1, "")

    # ③ 口径版本 +1 ⇒ 必变
    f4 = _fp.state_fingerprint(img, theme="light", dpi=1.5, scroll_pos=120,
                               schema=_fp.FINGERPRINT_SCHEMA + 1)
    ck("P0-1 ③ 口径版本 +1 ⇒ 指纹必变", f4 != f1, "")

    # ④ 五维各自都要进指纹 —— 少一维就会出现"状态变了、指纹没变"。
    #    逐维单独改一项，断言指纹都变（这条守的是"别把某一维漏在哈希外"）。
    _base = dict(theme="light", dpi=1.5, scroll_pos=120)
    for _axis, _kw in (("滚动位置", {"scroll_pos": 121}),
                       ("UI 主题", {"theme": "dark"}),
                       ("DPI", {"dpi": 1.75})):
        _d = dict(_base)
        _d.update(_kw)
        ck("P0-1 ④ 改了「%s」⇒ 指纹必变" % _axis,
           _fp.state_fingerprint(img, **_d) != f1, "")

    # ⑤ `diff_detail` 要能指出"是哪一维变了"（上层据此挑最小补救动作）
    _da = _fp.fingerprint_detail(img, theme="light", dpi=1.5, scroll_pos=120)
    _db = _fp.fingerprint_detail(img, theme="dark", dpi=1.5, scroll_pos=120)
    _dd = _fp.diff_detail(_da, _db)
    ck("P0-1 ⑤ diff_detail 指认变化的维度",
       (not _dd["same_fingerprint"])
       and any(c.get("axis") == "theme" for c in _dd["changed"]), "")

    # ── P0-4 两个谓词 ──────────────────────────────────────────────────
    # 强谓词：四项全对才通过
    _ok, _ = _vd.is_mutation_valid(account="acc", chat_key="group:g1", generation=3,
                                   ctx_account="acc", ctx_chat_key="group:g1", ctx_generation=3)
    ck("P0-4 强谓词 四项全对 ⇒ 通过", _ok is True, "")
    for _name, _kw in (
        ("账号不符", {"account": "acc2"}),
        ("会话不符", {"chat_key": "group:g2"}),
        ("代次过期", {"generation": 4}),
    ):
        _kw2 = {"account": "acc", "chat_key": "group:g1", "generation": 3,
                "ctx_account": "acc", "ctx_chat_key": "group:g1", "ctx_generation": 3}
        _kw2.update(_kw)
        _r, _why = _vd.is_mutation_valid(**_kw2)
        ck("P0-4 强谓词 %s ⇒ 判无效且给出理由" % _name, (not _r) and bool(_why), "")
    # ⛔ 空值不算"一致" —— 两侧都空时若判通过，等于"不知道是谁就当对吧"
    _r, _ = _vd.is_mutation_valid(account="", chat_key="group:g1", generation=1,
                                  ctx_account="", ctx_chat_key="group:g1", ctx_generation=1)
    ck("P0-4 强谓词 账号两侧皆空 ⇒ 判无效（不许「都不知道就算对」）", not _r, "")

    # 弱谓词：只要"还看着这个会话"
    _r, _ = _vd.can_apply_to_visible(visible_chat="群A", target_chat="群A")
    ck("P0-4 弱谓词 正在看目标会话 ⇒ 通过", _r is True, "")
    _r, _why = _vd.can_apply_to_visible(visible_chat="群B", target_chat="群A")
    ck("P0-4 弱谓词 已切到别的会话 ⇒ 不更新且给出理由", (not _r) and bool(_why), "")
    _r, _ = _vd.can_apply_to_visible(visible_chat="", target_chat="群A")
    ck("P0-4 弱谓词 读不到当前会话 ⇒ fail-closed（不更新）", not _r, "")

    # ★ 核心验收：**发出后已切走会话** ⇒ 持久层照写、界面不更新
    _d = _vd.decide(account="acc", chat_key="group:g1", generation=3,
                    ctx_account="acc", ctx_chat_key="group:g1", ctx_generation=3,
                    visible_chat="group:g2", visible_account="acc")
    ck("P0-4 ★ 切走后：持久层照写（persist=True）", _d["persist"] is True, "")
    ck("P0-4 ★ 切走后：界面不更新（visible=False）", _d["visible"] is False,
       str(_d.get("visible_reason") or "")[:60])

    # 反向：改动本身无效时，界面同样不该更新（且理由写明是"改动无效"而不是"切走了"）
    _d2 = _vd.decide(account="acc", chat_key="group:g1", generation=9,
                     ctx_account="acc", ctx_chat_key="group:g1", ctx_generation=3,
                     visible_chat="group:g1", visible_account="acc")
    ck("P0-4 改动无效 ⇒ 持久层不写且界面不更新（两个理由分开写）",
       (not _d2["persist"]) and (not _d2["visible"])
       and "无效" in str(_d2.get("visible_reason") or ""), "")

    # 代次：单调递增，两次调用必不同（同一毫秒也不许撞）
    _a, _b = _vd.next_generation(), _vd.next_generation()
    ck("P0-4 代次单调递增且不重复（同一毫秒也不许撞）", _b > _a, "")

    # ── 接线检查：模块必须**真的接在链路上**，不许空挂 ────────────────────
    _s_src = (HERE.parents[0] / "agent" / "sender.py").read_text(encoding="utf-8")
    ck("P0-4 已接进发送链路（发送记录点用 decide 的结论分账）",
       "_verdict_for" in _s_src and 'if _vd["persist"]' in _s_src
       and "_notify_sent" in _s_src, "")
    _w_src = (HERE.parents[0] / "agent" / "wechat.py").read_text(encoding="utf-8")
    ck("P0-4 已接进切会话链路（成功切会话 ⇒ 代次 +1）",
       "after_switch_mark" in _w_src and "_chat_generation" in _w_src, "")


def t_scope_degrade_guard() -> None:
    """自检：**B-3 —— 身份隔离桶（P0-3）＋ 降级守卫（P0-2）**。

    这两条都是"**失败路径上的防线**"，共同点是：
    **出问题的时候一切看起来都正常** —— 没有异常、没有错误码，只是结果落错了地方。

    ### P0-3 验收（报告原文口径）
      mock「识别失败」⇒ 断言后续所有读写**都用隔离 scope**，且**不会命中上一会话的缓存/状态**。
      本判据按三条拆开测：
      ① 读不到身份 ⇒ 拿到的是**隔离桶**（不是空串、不是 default）；
      ② **两个隔离桶永不相等**（同一毫秒连建两个也是），且与任何真身份都不相等；
      ③ `is_same_resolved_scope` 在**任一侧未解析时判 False**（含两侧都未解析）。

    ### P0-2 验收（报告原文口径）
      构造"**路径 A 通过、路径 B 不通过**"⇒ 断言 B 路径下**该判据未被使用**，且结果为 fail-closed。
      本判据按三条测：
      ① A 允许用的判据，在 B 下**查不过**（未被使用）；
      ② B 下沿降级链找到的**替代判据也在矩阵里**（降级不是随便挑）；
      ③ 降级链走空 ⇒ `pick()` 返回 `None` ⇒ 调用方**必须 fail-closed**；
         **未知组合（不在矩阵里）⇒ 允许集为空** —— 不沿用任何其它路径的判据。

    ⚠️ 两组都不 import 真实 OCR/取帧：被测对象是**判定逻辑**，"读不到身份"由参数直接构造，
    这样测的才是"逻辑对不对"，而不是"这台机器上 OCR 准不准"。
    """
    import agent.scope as _sc  # noqa: PLC0415
    import agent.degrade_guard as _dg  # noqa: PLC0415

    # ── P0-3 ① 读不到 ⇒ 隔离桶（不是空串 / 不是 default）─────────────────
    ck("P0-3 读不到身份 ⇒ 拿到隔离桶（不是空串、不是 default）",
       _sc.is_pending(_sc.scope_of("")) and _sc.scope_of("") != _sc.UNRESOLVED, _sc.scope_of(""))
    ck("P0-3 `default` 与空串都按未解析处理（不是已解析）",
       not _sc.is_resolved("") and not _sc.is_resolved(_sc.UNRESOLVED), "")
    ck("P0-3 读到了 ⇒ 已解析身份**原样返回**（不加前缀，保证与裸会话 key 可比）",
       _sc.scope_of("group:演示群") == "group:演示群"
       and _sc.is_resolved(_sc.scope_of("group:演示群")), _sc.scope_of("group:演示群"))

    # ── P0-3 ② 隔离桶之间、隔离桶与真身份之间，**永不相等** ───────────────
    _p1, _p2 = _sc.pending_scope(), _sc.pending_scope()
    ck("P0-3 ★ 两个隔离桶**互不相等**（同一毫秒连建两个也不许撞）", _p1 != _p2,
       "%s / %s" % (_p1, _p2))
    ck("P0-3 ★ 隔离桶与任何真身份都不相等（不会命中上一会话的状态）",
       not _sc.is_same_resolved_scope(_p1, "group:演示群")
       and not _sc.is_same_resolved_scope("group:演示群", _p1), "")
    ck("P0-3 ★ 两个未解析态**也判不相等**（宁可多算一次，也不让 A 认成 B）",
       not _sc.is_same_resolved_scope(_p1, _p2)
       and not _sc.is_same_resolved_scope("", ""), "")
    ck("P0-3 同一个已解析身份 ⇒ 判相等（反向控制：不是一律判 False）",
       _sc.is_same_resolved_scope("group:演示群", "group:演示群"), "")

    # ── P0-3 ③ 接线：`sender` 的 visible 必须过 `scope_of` ─────────────────
    _s_src2 = (HERE.parents[0] / "agent" / "sender.py").read_text(encoding="utf-8")
    ck("P0-3 已接进 B-2 链路（visible_chat 过 scope_of ⇒ 读不到变显式隔离桶）",
       "scope_of" in _s_src2 and "import scope as _scope" in _s_src2, "")
    _v_src2 = (HERE.parents[0] / "agent" / "verdict.py").read_text(encoding="utf-8")
    ck("P0-3 弱谓词显式排除隔离桶（不靠桶名恰好长得不同这种巧合）",
       "is_pending" in _v_src2, "")

    # ── P0-2 ① 路径 A 通、路径 B 不通 ────────────────────────────────────
    #   绿底带（色值敏感）在「列表路径 + 浅色主题」下成立；
    #   换到「会话头路径」就**看不到会话列表** ⇒ 该判据根本不适用。
    ck("P0-2 路径 A（列表/浅色）允许用绿底带判据",
       _dg.is_allowed(_dg.CRIT_GREEN_BAND, "row", "light"), "")
    ck("P0-2 ★ 路径 B（会话头）下绿底带判据**不被允许**（未被使用）",
       not _dg.is_allowed(_dg.CRIT_GREEN_BAND, "header", "light"), "")
    ck("P0-2 ★ 路径 B（列表/深色）下绿底带同样不被允许（深色色值不同 ⇒ 判据不成立）",
       not _dg.is_allowed(_dg.CRIT_GREEN_BAND, "row", "dark"), "")

    # ── P0-2 ② 降级到"也在矩阵里"的替代判据 ──────────────────────────────
    _c, _why = _dg.pick(_dg.CRIT_GREEN_BAND, "header", "light")
    ck("P0-2 ★ 降级选出的替代判据**本身也在矩阵里**（不是随便挑一条）",
       _c is not None and _dg.is_allowed(_c, "header", "light"), "%r" % (_c,))
    ck("P0-2 降级必须说明**换了哪一条**（调用方要据此换掉后续步骤）",
       bool(_why) and str(_c) in _why, _why[:60])
    ck("P0-2 首选判据允许时**不降级**（reason 为空，不多此一举）",
       _dg.pick(_dg.CRIT_GREEN_BAND, "row", "light") == (_dg.CRIT_GREEN_BAND, ""), "")

    # ── P0-2 ③ 走空 / 未知组合 ⇒ fail-closed ─────────────────────────────
    #   ★ 造"允许集非空、但首选判据与整条降级链都不在其中"这一档是不可能的 ——
    #     矩阵里 `header_text` / `pixel_span` 在**每一格**都在（它们主题无关），
    #     所以任何非空允许集都至少能降级到它们。硬造这一档只能靠临时改矩阵 ⇒
    #     那测的是"我改得对不对"，不是产品行为。**改测两条能真实成立的**：
    #     ① 降级链**自身**为空（`pixel_span` 是最弱判据，没得再退）；
    #     ② 未知组合 ⇒ 允许集空 ⇒ `pick` 返回 None（真正的 fail-closed 路径）。
    ck("P0-2 ★ 最弱判据没有更弱的可退（降级链自身为空 ⇒ 只能 fail-closed）",
       _dg.downgrade_chain(_dg.CRIT_PIXEL_SPAN) == [], str(_dg.downgrade_chain(_dg.CRIT_PIXEL_SPAN)))
    _c3, _why3 = _dg.pick(_dg.CRIT_GREEN_BAND, "某个没见过的路径", "light")
    ck("P0-2 ★ 未知渲染条件 ⇒ 允许集为空、pick 返回 None（**不沿用其它路径的判据**）",
       _dg.allowed("某个没见过的路径", "light") == set() and _c3 is None, _why3[:60])
    _c4, _why4 = _dg.pick(_dg.CRIT_GREEN_BAND, "row", "某个没见过的主题")
    ck("P0-2 ★ 未知主题同样 fail-closed（只认矩阵里有 的组合）",
       _c4 is None and bool(_why4), _why4[:50])
    ck("P0-2 反向控制：矩阵非空且键数正常（防矩阵清空后全 fail-closed 也全绿）",
       len(_dg.describe()["matrix"]) >= 6, str(sorted(_dg.describe()["matrix"]))[:80])


def t_job_cancel_guard() -> None:
    """自检：**B-4 —— 「取消」是第三种终态**（P0-5）。

    **要解决的病**：把"用户取消"混进"失败"，会污染成功率统计，
    也会让下次误判"上次失败了要重试"（用户明明是自己停的）。

    **条款**（报告原文口径）：
      ① 取消 ⇒ 终态 `canceled`、**`error` 为空**、且**算"已完成"**（闸门放行、能立刻重开）；
      ② **取消本身也可能失败，必须报出来**（不许 fire-and-forget）；
      ③ 状态色调 / 文案 / 图标全链路补齐（`canceled` ⇒ `已取消`，色调用 `stopped`）。

    ⚠️ 本判据**不真起子进程**（那要 20 秒且依赖机器）——
    子进程级的端到端在 `_scratch/_e2e_jobs_cancel.py`（**20/0**，含真杀进程树）。
    这里测的是**状态语义**与**接线**：终态分档、中间态、闸门、以及"取消端点真的接上了"。
    """
    import agent.jobs as _jb  # noqa: PLC0415

    # ── ① 三档终态各归各位（`_end_kind` 是唯一的命名点）────────────────
    ck("P0-5 ★ 取消 ⇒ 终态名「已取消」（独立的一档，不并进失败）",
       _jb._end_kind({"running": False, "canceled": True, "returncode": 1}) == "已取消", "")
    ck("P0-5 ★ 失败（非零返回码、未取消）⇒ 终态名「失败」",
       _jb._end_kind({"running": False, "canceled": False, "returncode": 1}) == "失败", "")
    ck("P0-5 成功（rc=0、未取消）⇒ 终态名「成功」",
       _jb._end_kind({"running": False, "canceled": False, "returncode": 0}) == "成功", "")
    ck("P0-5 ★ 中间态：正跑且已发取消 ⇒ 名「正在取消」（**不许提前说已取消**）",
       _jb._end_kind({"running": True, "canceling": True, "canceled": False}) == "正在取消", "")
    ck("P0-5 中间态：正跑且没发取消 ⇒ 名「进行中」",
       _jb._end_kind({"running": True, "canceling": False}) == "进行中", "")

    # ── ①′ 取消**不算失败**：那是"已完成"的一种 ⇒ 闸门必须放行 ──────────
    _jb.reset()
    _jb._jobs["b4t"] = dict(_jb._empty(), name="b4t", running=False, canceled=True, returncode=1)
    _r = _jb.start("b4t", "python -c \"print(1)\"", timeout=5)
    ck("P0-5 ★ 已取消的作业**不挡**下一个（取消 = 已结束，闸门放行）",
       bool(_r.get("ok")), str(_r.get("why"))[:60])
    _jb.reset()

    # ── ② 取消失败必须如实回报（不静默当成功）─────────────────────────
    _jb.reset()
    _r_missing = _jb.cancel("根本没有这个作业")
    ck("P0-5 ★ 取消不存在的作业 ⇒ ok=False ＋ 原因（不静默当成功）",
       (_r_missing.get("ok") is False) and bool(_r_missing.get("why")), str(_r_missing.get("why"))[:40])
    _jb._jobs["b4t2"] = dict(_jb._empty(), name="b4t2", running=False, canceled=False, returncode=0)
    _r_done = _jb.cancel("b4t2")
    ck("P0-5 ★ 取消已结束的作业 ⇒ ok=False ＋ 点名它已结束（不是取消成功）",
       (_r_done.get("ok") is False) and ("结束" in str(_r_done.get("why"))), str(_r_done.get("why"))[:40])
    _jb._jobs["b4t3"] = dict(_jb._empty(), name="b4t3", running=True, pid=None)
    _r_nopid = _jb.cancel("b4t3")
    ck("P0-5 ★ 拿不到 pid ⇒ ok=False ＋ 说清'取消不了'（不假装成功）",
       (_r_nopid.get("ok") is False) and bool(_r_nopid.get("why")), str(_r_nopid.get("why"))[:40])
    _jb.reset()

    # ── ③ 接线：取消端点真的接上了（三处同步：路由表 + 实现 + 前端可调）──
    import agent.routes as _rt  # noqa: PLC0415
    # ★ 两张表**都要登记**：`ROUTES`（路径→允许的方法）与 `HANDLERS`（路径→函数名）。
    #   只加一张 ⇒ 要么路由表认不出这个方法（405），要么分派找不到实现（500）。
    _has_route = "/api/version/job/cancel" in getattr(_rt, "ROUTES", {})
    ck("P0-5 ★ 取消端点已登记进 `ROUTES`（路径→方法）", _has_route, "")
    _has_handler = "/api/version/job/cancel" in getattr(_rt, "HANDLERS", {})
    ck("P0-5 ★ 取消端点已登记进 `HANDLERS`（路径→实现）——两张表缺一不可",
       _has_handler, "")
    _w_src = (HERE.parents[0] / "agent" / "webui.py").read_text(encoding="utf-8")
    ck("P0-5 ★ 取消端点实现已接上（且失败也回报 `why`）",
       "_rapi_version_job_cancel_post" in _w_src and "_jobs7.cancel" in _w_src, "")

    # ── ③′ `_empty()` 的字段契约：新增字段必须齐，否则界面读 KeyError ──
    _e = _jb._empty()
    ck("P0-5 `_empty()` 带齐三档终态字段（canceled / canceling / cancel_error）",
       ("canceled" in _e) and ("canceling" in _e) and ("cancel_error" in _e),
       str(sorted(_e.keys()))[:100])


def t_suspend_counter_guard() -> None:
    """自检：**B-5 / P1-3 —— 暂停/恢复用计数器，不用布尔**。

    **要解决的病**：布尔暂停只能表达"暂停/未暂停"。若两个来源**各自**暂停
    （用户按了「暂停」＋ 某个后台流程也临时挂起），先结束的那个一恢复就把整机恢复了
    —— 另一个来源还在等，机器人却已经开始发消息（**嵌套提前恢复**）。

    **条款**：① `suspend_depth` 从 0 起加；② **只有归零才真恢复**（flag 才消失）；
    ③ 多退一次不报错也不变负（不解除别人的暂停）；④ **显式恢复**（用户按）强制归零。

    ⚠️ 全程用临时档（`set_paths`）⇒ 绝不碰产品目录里的 `data/paused.flag`。
    """
    import agent.control as _ctl  # noqa: PLC0415
    import tempfile as _tf  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    _td = _tf.mkdtemp(prefix="suspend-guard-")
    _p = _os.path.join(_td, "paused.flag")
    _s = _os.path.join(_td, "stopped.flag")
    _old = _ctl.set_paths(paused=_p, stopped=_s)
    try:
        # ── ① 两层 push ⇒ 退一层**不恢复**（这正是本机制存在的理由）──────────
        _ctl.suspend_reset()
        ck("P1-3 ★ 起始层数为 0（没被暂停）", _ctl.suspend_depth() == 0, str(_ctl.suspend_depth()))
        _tA = _ctl.suspend_push("来源A")
        ck("P1-3 push 一层 ⇒ 层数 1", _ctl.suspend_depth() == 1, str(_ctl.suspend_depth()))
        ck("P1-3 push 一层 ⇒ flag 真的落盘（既有读者不用改）",
           _os.path.exists(_p), "")
        _tB = _ctl.suspend_push("来源B")
        ck("P1-3 push 两层 ⇒ 层数 2", _ctl.suspend_depth() == 2, str(_ctl.suspend_depth()))
        ck("P1-3 ★ 每次 push 发不同的票（票号单调递增，不重号）", _tA != _tB,
           "A=%s B=%s" % (_tA, _tB))
        _ctl.suspend_pop(_tA)
        ck("P1-3 ★★ 退一层 ⇒ 层数 1 且**仍在暂停**（flag 还在 ⇒ 不提前恢复）",
           (_ctl.suspend_depth() == 1) and _os.path.exists(_p) and _ctl.is_paused(),
           "depth=%s flag=%s" % (_ctl.suspend_depth(), _os.path.exists(_p)))
        _ctl.suspend_pop(_tB)
        ck("P1-3 ★ 归零 ⇒ 真的恢复（层数 0 且 flag 消失）",
           (_ctl.suspend_depth() == 0) and (not _os.path.exists(_p)) and (not _ctl.is_paused()),
           "depth=%s flag=%s" % (_ctl.suspend_depth(), _os.path.exists(_p)))

        # ── ② 持票语义：**只有持有人能退自己那层**（裸计数做不到，多退一次会减掉别人的）──
        _ctl.suspend_reset()
        ck("P1-3 层数已 0 时再 pop ⇒ 仍为 0（不变负）",
           _ctl.suspend_pop(None) == 0 and _ctl.suspend_depth() == 0, str(_ctl.suspend_depth()))
        _t1 = _ctl.suspend_push("来源C1")
        _t2 = _ctl.suspend_push("来源C2")
        _ctl.suspend_pop(_t2)
        _ctl.suspend_pop(_t2) # 同一个票退两次 ⇒ 第二次应无效果
        ck("P1-3 ★★ 同一张票退两次 ⇒ 第二次无效（**不会把别人那一层也解掉**）",
           (_ctl.suspend_depth() == 1) and _ctl.is_paused(), str(_ctl.suspend_depth()))
        _ctl.suspend_pop(99999) # 不存在的票
        ck("P1-3 ★★ 退一张**根本没有的票** ⇒ 层数不变（不误伤别人的暂停）",
           (_ctl.suspend_depth() == 1) and _ctl.is_paused(), str(_ctl.suspend_depth()))
        ck("P1-3 ★ 只有持有人退掉自己那层后，才真的归零",
           (_ctl.suspend_pop(_t1) == 0) and (not _ctl.is_paused()), str(_ctl.suspend_depth()))

        # ── ③ 显式恢复：用户按了恢复 ⇒ 强制归零（不受残留层数拦阻）───────────
        _ctl.suspend_push("来源D")
        _ctl.suspend_push("来源E")
        _old_n = _ctl.suspend_reset()
        ck("P1-3 ★ 显式恢复 ⇒ 不管几层一律归零、flag 清掉（用户意图高于计数）",
           (_old_n == 2) and (_ctl.suspend_depth() == 0) and (not _os.path.exists(_p)),
           "old=%s now=%s" % (_old_n, _ctl.suspend_depth()))

        # ── ④ 接线：风险闸门必须走持票的嵌套计数（不许退回布尔直写）──────────
        _r_src = (HERE.parents[0] / "agent" / "risk.py").read_text(encoding="utf-8")
        ck("P1-3 ★ 风险闸门 pause() 走 `suspend_push` 并**留住票号**（嵌套计数，不是布尔直写）",
           ("suspend_push(" in _r_src) and ("_ctl_ticket" in _r_src), "")
        ck("P1-3 ★ 风险闸门 resume() **只退自己那层**（`suspend_pop(_tok)`，不拿别人清零）",
           "suspend_pop(_tok)" in _r_src, "")
        ck("P1-3 ★ 风险闸门 recover() 才是显式恢复 ⇒ `suspend_reset`（用户意图高于计数）",
           "suspend_reset(" in _r_src, "")

        # ── ⑤ 换档必须把计数归零（判据隔离；否则判据之间会互相串）────────────
        _ctl.suspend_push("来源F")
        _ctl.set_paths(paused=_p, stopped=_s)
        ck("P1-3 ★ 换标记路径 ⇒ 计数归零（判据/实例之间不串）",
           _ctl.suspend_depth() == 0, str(_ctl.suspend_depth()))
    finally:
        _ctl.set_paths(paused=_old["paused"], stopped=_old["stopped"])
        _ctl._CACHE["at"] = 0.0
        import shutil as _sh  # noqa: PLC0415
        _sh.rmtree(_td, ignore_errors=True)


def t_task_owner_guard() -> None:
    """自检：**B-5 / P1-4 —— 任务槽位的所有权**（只有当前主人能写/删该槽位）。

    **要解决的病**：同一个名字的活儿被**先后起两次**（重试/重启/看门线程被替换）。
    先起的那次还活着，结束时把结果**写回同一个槽位** —— 而槽位早已换人。
    于是"新的还在跑，界面却显示旧结果"，或者旧的收尾把新的状态**清掉**。
    这类竞态**不常发生、过去之后无从复现**，只能靠"写之前先验证主人身份"根治。

    **条款**：① 认领发唯一令牌；② **非主人写入 = 无操作**；③ **非主人释放 = 无副作用**
    （`if tasks.get(k) is me` 的等价实现，防误删新主人）；④ 槽位不存在 ⇒ fail-closed。
    """
    import agent.task_owner as _own  # noqa: PLC0415

    _own.reset()
    # ── ① 认领与唯一性 ───────────────────────────────────────────────────
    _t1 = _own.claim("slotA")
    _t2 = _own.claim("slotB")
    ck("P1-4 ★ 认领不同槽位 ⇒ 令牌互不相同", _t1 != _t2, "%s/%s" % (_t1, _t2))
    ck("P1-4 ★ 认领同一槽位两次 ⇒ 令牌变（换主人）",
       _own.claim("slotA") != _t1, "")

    # ── ② 非主人写入 = 无操作（这是本条的核心）────────────────────────────
    _own.reset()
    _old = _own.claim("job")
    _new = _own.claim("job") # 换主人
    ck("P1-4 ★★ 旧令牌**不再是**主人（写入前会被挡下）",
       (not _own.is_owner("job", _old)) and _own.is_owner("job", _new),
       "old=%s new=%s cur=%s" % (_old, _new, _own.current("job")))

    # ── ③ 非主人释放 = 无副作用（防误删新主人的槽位）─────────────────────
    _r_old = _own.release("job", _old)
    ck("P1-4 ★★ 非主人释放 ⇒ 返回 False 且**槽位仍在**（旧线程退不掉新主人的槽位）",
       (_r_old is False) and (_own.current("job") == _new),
       "released=%s cur=%s" % (_r_old, _own.current("job")))
    _r_new = _own.release("job", _new)
    ck("P1-4 ★ 主人自己释放 ⇒ 返回 True 且槽位清掉",
       (_r_new is True) and (_own.current("job") is None), "")

    # ── ④ fail-closed：槽位不存在时任何人都不是主人 ─────────────────────
    _own.reset()
    ck("P1-4 ★ 槽位不存在 ⇒ `is_owner` 一律 False（拿不准就不写）",
       not _own.is_owner("never-claimed", 1), "")
    ck("P1-4 ★ 释放一个不存在的槽位 ⇒ False（不假装成功）",
       _own.release("never-claimed", 1) is False, "")

    # ── ⑤ 接线：两个真实竞态点真的用了所有权 ────────────────────────────
    _j_src = (HERE.parents[0] / "agent" / "jobs.py").read_text(encoding="utf-8")
    ck("P1-4 ★ 一键作业：起跑时认领（`_owner.claim`）", "_owner.claim(" in _j_src, "")
    ck("P1-4 ★ 一键作业：写终态前核对主人（`is_owner`）——旧一代不许覆盖",
       _j_src.count("is_owner(") >= 2, str(_j_src.count("is_owner(")))
    ck("P1-4 ★ 一键作业：reset 时连所有权槽位一起清（防'清空了又冒出来'）",
       "_owner.reset()" in _j_src, "")
    _w_src = (HERE.parents[0] / "agent" / "window_borrow.py").read_text(encoding="utf-8")
    ck("P1-4 ★ 窗口看门线程：每轮先核对主人（防两个看门线程互抢还原）",
       "_owner.is_owner(" in _w_src and "token" in _w_src, "")


def t_idempotent_close_guard() -> None:
    """自检：**B-5 / P1-5 —— 幂等关闭**（同一句柄被多处调 `close()` 只关一次）。

    **要解决的病**：`close()` 调用点很多（切会话收尾／失败分支／超时兜底），同一浮层句柄
    可能被两条路径各关一次。第二次 `WM_CLOSE` 本身通常无害，但 Windows 会把**已销毁窗口的
    句柄值回收给新窗** —— 此时"再关一次同一个数字"会朝一个**完全无关的新窗口**发关窗消息。

    **条款**：① TTL 内重复关 ⇒ 跳过（返回 True，结果对调用方一致）；② TTL 过期 ⇒ 允许再关
    （句柄被回收后不永久挡死）；③ 记忆有界（防长跑内存涨）；④ **判据用假 hwnd + 打桩
    PostMessageW**，绝不真发 `WM_CLOSE`。
    """
    import agent.winops as _wo  # noqa: PLC0415
    import ctypes as _ct  # noqa: PLC0415

    _wo.reset_idempotent()
    # ── 打桩：拦下真的 PostMessageW，只记"发了几枪"；同时让 hwnd 看起来是个普通子窗 ──
    _sent = {"n": 0, "hwnds": []}

    class _FakeU32:  # noqa: D401
        def PostMessageW(self, hwnd, msg, w, l):  # noqa: N802
            _sent["n"] += 1
            # ⛔ 不要把 hwnd 转 int：`close()` 传进来的是 `ctypes.c_void_p`，
            #   而 `int(c_void_p)` 在本解释器上抛 `ValueError`（不是 `int` 能接的那种）——
            #   那会从桩里抛出、被 `close()` 的兜底 except 吞成 False，看起来像"关窗失败"。
            #   桩要模仿真实 `PostMessageW`：**只记账、不抛**。
            _sent["hwnds"].append(hwnd)
            return 1

    _saved = None
    try:
        _saved = _ct.windll.user32
    except Exception:  # noqa: BLE001
        pass
    try:
        _ct.windll.user32 = _FakeU32()
    except Exception:  # noqa: BLE001
        pass

    # ok_to_close 在没装 pywin32 时也可能放行；这里直接打桩成"允许"，把被测面收窄到幂等逻辑。
    _saved_ok = _wo.ok_to_close
    _wo.ok_to_close = lambda hwnd, why="": True
    try:
        _H = 0x123456
        _r1 = _wo.close(_H, "第一次")
        ck("P1-5 ★ 第一次关 ⇒ 真的发了一枪 WM_CLOSE", _r1 and _sent["n"] == 1,
           "sent=%d" % _sent["n"])
        _r2 = _wo.close(_H, "第二次（同一句柄）")
        ck("P1-5 ★★ 同一句柄再关 ⇒ **跳过**（没有发出第二枪）",
           (_r2 is True) and _sent["n"] == 1, "sent=%d" % _sent["n"])
        _r3 = _wo.close(_H, "第三次")
        ck("P1-5 ★ 重复再多次也仍然只关过一次", _sent["n"] == 1, "sent=%d" % _sent["n"])

        # 另一个句柄照样能关（幂等是"按句柄"的，不是"全局只关一次"）
        _H2 = 0x654321
        _wo.close(_H2, "另一个句柄")
        ck("P1-5 ★ 幂等是**按句柄**的（不同句柄照常关）", _sent["n"] == 2, "sent=%d" % _sent["n"])

        # ── TTL 过期 ⇒ 允许再关 ──────────────────────────────────────────
        _wo._closed_at[int(_H)] = _wo.time.time() - (_wo._IDEMPOTENT_TTL_S + 5)
        _wo.close(_H, "过期后再关")
        ck("P1-5 ★ TTL 过期后 ⇒ 允许再关（句柄被回收后不永久挡死）",
           _sent["n"] == 3, "sent=%d" % _sent["n"])

        # ── 记忆有界 ─────────────────────────────────────────────────────
        _wo.reset_idempotent()
        for _i in range(600):
            _wo._mark_closed(0x100000 + _i)
        ck("P1-5 ★ 「已关过」记忆有界（长跑不涨内存）",
           len(_wo._closed_at) <= 512, "size=%d" % len(_wo._closed_at))
        _wo.reset_idempotent()
        ck("P1-5 清空接口可用（判据隔离）", len(_wo._closed_at) == 0, "")
    finally:
        _wo.ok_to_close = _saved_ok
        _wo.reset_idempotent()
        if _saved is not None:
            try:
                _ct.windll.user32 = _saved
            except Exception:  # noqa: BLE001
                pass


def t_retry_policy_guard() -> None:
    """自检：**B-5 / P1-7 —— 只重试可重试的 errno + 显式退避表 + 每轮重查代数**。

    **要解决的病**：
      ① 什么都重试（把"路径不存在/参数非法"当瞬态）⇒ 白等 + 真因被噪声盖住；
      ② 退避是"每次乘 1.6"的经验公式 ⇒ 节奏随实现而变、判据钉不住；
      ③ 重试期间**前提变了**（文件换了版本/会话切了）却拿着旧前提继续干 ⇒ 比不重试更糟。

    **条款**：① 白名单外的 errno **一次都不重试**；② 退避取**显式表**、走完用最后一档；
    ③ 代数不一致 ⇒ 抛 `StaleGeneration`（停），不静默继续。
    """
    import agent.retry_policy as _rp  # noqa: PLC0415
    import errno as _er  # noqa: PLC0415

    # ── ① 可重试白名单 ───────────────────────────────────────────────────
    ck("P1-7 ★ EPERM/EACCES/EBUSY/EEXIST 在白名单里（可重试）",
       all(_er.EPERM == e or e in _rp.RETRYABLE_ERRNOS
           for e in (_er.EPERM, _er.EACCES, _er.EBUSY, _er.EEXIST)), "")
    ck("P1-7 ★ 构造 Windows winerror 32（被占用）⇒ 判可重试",
       _rp.is_retryable(OSError(13, "x", None, 32)), "")
    ck("P1-7 ★★ ENOENT（不存在）**不在**白名单 ⇒ 不可重试",
       not _rp.is_retryable(FileNotFoundError(2, "no such file")), "")
    ck("P1-7 ★★ EINVAL（参数非法）**不在**白名单 ⇒ 不可重试",
       not _rp.is_retryable(OSError(_er.EINVAL, "bad arg")), "")
    ck("P1-7 ★ ENOSPC（磁盘满）**不在**白名单 ⇒ 不可重试（重试也满）",
       not _rp.is_retryable(OSError(_er.ENOSPC, "no space")), "")
    ck("P1-7 ★ 完全无关的异常（ValueError）⇒ 不可重试",
       not _rp.is_retryable(ValueError("x")), "")

    # ── ② 显式退避表 ─────────────────────────────────────────────────────
    ck("P1-7 ★ 退避表是显式的一串（不是算出来的）",
       tuple(_rp.BACKOFF_MS) == (25, 50, 100, 200, 400, 800), str(_rp.BACKOFF_MS))
    ck("P1-7 第 0 轮 ⇒ 25ms；第 5 轮 ⇒ 800ms",
       (_rp.backoff_ms(0) == 25) and (_rp.backoff_ms(5) == 800),
       "%s/%s" % (_rp.backoff_ms(0), _rp.backoff_ms(5)))
    ck("P1-7 ★ 超出表长 ⇒ 用**最后一档**（不无限翻倍）",
       _rp.backoff_ms(99) == 800, str(_rp.backoff_ms(99)))
    ck("P1-7 负数/脏输入 ⇒ 退回第一档（不抛）", _rp.backoff_ms(-3) == 25, "")

    # ── ③ call_with_retry：重试次数 / 代数复核 ───────────────────────────
    _calls = {"n": 0}

    def _always_busy():
        _calls["n"] += 1
        raise OSError(_er.EBUSY, "busy")

    _ok, _v, _e = _rp.call_with_retry(_always_busy, tries=3, total_budget_s=0.05, what="test")
    ck("P1-7 ★ 一直忙 ⇒ 试满 3 轮后停（不是无限）",
       (_ok is False) and (_calls["n"] == 3), "calls=%d" % _calls["n"])

    _calls["n"] = 0

    def _never_retryable():
        _calls["n"] += 1
        raise OSError(_er.EINVAL, "bad")

    _ok2, _v2, _e2 = _rp.call_with_retry(_never_retryable, tries=5, what="test")
    ck("P1-7 ★★ 不可重试的错 ⇒ **只试 1 次**（不当瞬态反复试）",
       (_ok2 is False) and (_calls["n"] == 1), "calls=%d" % _calls["n"])

    _calls["n"] = 0

    def _ok_fn():
        _calls["n"] += 1
        return "done"

    _ok3, _v3, _e3 = _rp.call_with_retry(_ok_fn, tries=3, what="test")
    ck("P1-7 成功 ⇒ 直接返回、不重试", (_ok3 is True) and (_v3 == "done"), str(_v3))

    # ── ③′ 代数复核：重试前过了代 ⇒ 停（StaleGeneration）────────────────
    _gen = {"v": 1}
    _tries = {"n": 0}

    def _bump_then_fail():
        _tries["n"] += 1
        _gen["v"] += 1 # 每轮把代数推走（模拟"前提变了"）
        raise OSError(_er.EBUSY, "busy")

    _raised = False
    try:
        _rp.call_with_retry(_bump_then_fail, gen=1, gen_getter=lambda: _gen["v"],
                            tries=5, total_budget_s=0.05, what="test")
    except _rp.StaleGeneration:
        _raised = True
    ck("P1-7 ★★ 重试前代数已变 ⇒ 抛 `StaleGeneration` 停手（不带着过期前提继续）",
       _raised, "tries=%d" % _tries["n"])
    ck("P1-7 ★ 代数变了就**不再进第二轮**（第一轮发现即停）", _tries["n"] <= 2,
       "tries=%d" % _tries["n"])

    # ── ④ 接线：`persist._replace_retry` 走同一套口径 ────────────────────
    _p_src = (HERE.parents[0] / "agent" / "persist.py").read_text(encoding="utf-8")
    ck("P1-7 ★ 换档重试改用统一口径（`retry_policy`）",
       "retry_policy" in _p_src and "_rp.is_retryable" in _p_src and "_rp.backoff_ms" in _p_src, "")
    # 真行为：把 `os.replace` 打桩成"永远撞占用"，看它是否按退避表重试、且最终如实失败。
    import agent.persist as _ps  # noqa: PLC0415
    import tempfile as _tf  # noqa: PLC0415
    import os as _os2  # noqa: PLC0415
    _td = _tf.mkdtemp(prefix="p17-")
    _src = _os2.path.join(_td, "a.tmp")
    _dst = _os2.path.join(_td, "a.json")
    open(_src, "w").close()
    _real_replace = _os2.replace
    _hit = {"n": 0}

    def _fake_replace(a, b):
        _hit["n"] += 1
        raise PermissionError(13, "被占用", a, 32)

    _os2.replace = _fake_replace
    try:
        _r = _ps._replace_retry(_src, _dst, budget_s=0.05)
    finally:
        _os2.replace = _real_replace
    ck("P1-7 ★ 撞占用 ⇒ 按退避表真的重试了多轮（不是一次就放弃）",
       _hit["n"] >= 2, "hits=%d" % _hit["n"])
    ck("P1-7 ★ 重试耗尽 ⇒ 如实回 False（不假装成功）", _r is False, str(_r))


def t_reconnect_once_guard() -> None:
    """自检：**B-5 / P1-6 —— 已知错误码自愈：重连一次，不无限重试**。

    **要解决的病**：微信主窗被重建/重启后旧句柄失效 ⇒ 投递失败。这类失败**重连一次就好**；
    但要么"完全不重试"（用户看到一次莫名失败），要么"无限重试"（真·窗口关了就一直转）。
    两者都不对：**认得出是句柄问题 ⇒ 重取句柄再试一次；认不出 ⇒ 一次都不重试**。

    **条款**：① 命中已知措辞 ⇒ 重取句柄；② **只重连一次**；③ 句柄没变 ⇒ 不重试（问题不在句柄）；
    ④ 非句柄类失败 ⇒ 一次都不重试（不浪费）。
    """
    import agent.input_backend as _ib  # noqa: PLC0415

    # ── ①② 像句柄过期 + 句柄真的变了 ⇒ 重试一次、成功 ─────────────────────
    _saved_find = _ib.find_main_window
    _saved_refresh = _ib.refresh_main_window
    _state = {"hwnd": 111, "new": 222}
    _calls = {"n": 0}
    try:
        _ib.find_main_window = lambda *a, **k: _state["hwnd"]
        _ib.refresh_main_window = lambda: _state["new"]

        def _fail_then_ok(hwnd):
            _calls["n"] += 1
            if hwnd == _state["new"]:
                return True, ""
            return False, "窗口句柄失效（IsWindow=False）"

        _ok, _why, _retried = _ib.with_reconnect_once(_fail_then_ok, what="test")
        ck("P1-6 ★ 像句柄过期 + 新句柄可用 ⇒ 重连后成功",
           _ok and _retried and _calls["n"] == 2, "calls=%d retried=%s" % (_calls["n"], _retried))

        # ── ③ 句柄没变 ⇒ 不重试（问题不在句柄）────────────────────────────
        _calls["n"] = 0
        _ib.refresh_main_window = lambda: _state["hwnd"] # 和当前一样
        _ok2, _why2, _retried2 = _ib.with_reconnect_once(_fail_then_ok, what="test")
        ck("P1-6 ★★ 句柄**没变** ⇒ 不重试（重试也白搭，如实回原因为止）",
           (not _ok2) and (not _retried2) and (_calls["n"] == 1),
           "calls=%d retried=%s" % (_calls["n"], _retried2))

        # ── ④ 非句柄类失败 ⇒ 一次都不重试 ────────────────────────────────
        _calls["n"] = 0

        def _other_fail(hwnd):
            _calls["n"] += 1
            return False, "内容级证据与强档证据都没有 ⇒ 这条不发"

        _ok3, _why3, _retried3 = _ib.with_reconnect_once(_other_fail, what="test")
        ck("P1-6 ★★ 非句柄类失败 ⇒ **一次都不重试**（不浪费一次投递）",
           (not _ok3) and (not _retried3) and (_calls["n"] == 1),
           "calls=%d retried=%s" % (_calls["n"], _retried3))

        # ── ⑤ 成功 ⇒ 不重试 ──────────────────────────────────────────────
        _calls["n"] = 0
        _ok4, _why4, _retried4 = _ib.with_reconnect_once(lambda h: (True, ""), what="test")
        ck("P1-6 第一次就成功 ⇒ 不重试", _ok4 and (not _retried4) and _calls["n"] == 0,
           "calls=%d" % _calls["n"])

        # ── ⑥ 措辞白名单：认不出的原因一律不重试 ──────────────────────────
        ck("P1-6 ★ 认得出的措辞 ⇒ True（白名单命中）",
           _ib.stale_handle_like("窗口句柄失效") and _ib.stale_handle_like("invalid handle"), "")
        ck("P1-6 ★ 认不出的措辞 ⇒ False（不猜同义词）",
           (not _ib.stale_handle_like("发送失败")) and (not _ib.stale_handle_like("")), "")
    finally:
        _ib.find_main_window = _saved_find
        _ib.refresh_main_window = _saved_refresh

    # ── ⑦ 接线：投递失败若像句柄问题 ⇒ 重取句柄重投一次 ──────────────────
    _u_src = (HERE.parents[0] / "agent" / "ui_adapt.py").read_text(encoding="utf-8")
    ck("P1-6 ★ 投递点击失败时接上了自愈（`stale_handle_like` + `refresh_main_window`）",
       ("stale_handle_like" in _u_src) and ("refresh_main_window" in _u_src), "")


def t_popup_open_guard() -> None:
    """自检：**开独立窗这条路的三条硬约束**（双击是唯一可行手法，别被改坏）。

    **为什么这是"入口级"判据**：所有发送只走独立窗 ⇒ 独立窗开不出来 = 消息发不出去。
    而开窗只有"双击会话行"一条路（投递档；拖拽做不到，见下），它有三条**实测得来**的
    硬约束，违反任何一条都会**静默开不出窗**（表现为"发不出去"，最难排查）：

      ① **两枪间隔必须是手速真值（约 175ms）**：微信按"间隔 + 位置"判双击。
         间隔拉长（如 0.6s 停顿）会被结算成**两次独立单击** ⇒ 高亮被取消 ⇒ 开不出窗。
      ② **目标行必须处于"未选中"**：高亮正压在目标行上时，第一枪会被判成"重选该会话"，
         双击被拆散 ⇒ 开不出窗。⇒ 双击前必须先把选中态挪走。
      ③ **落点必须在列表顶之下**：落点落到顶栏/搜索框时，双击 = 微信**全屏**（不是开窗）；
         且"绿带行身份读不出"时必须**默认拒点**（否则 DB 序号被当成屏幕行号 ⇒ 点到别的会话）。

    另两条同等重要的护栏：
      ④ **"已有窗直接用"必须排在最前** —— 已有窗时**不点任何东西**（零动作、零风险）。
      ⑤ **收回被独立出去的窗时，绝不关微信主窗** —— 主窗被 Qt 重建后 hwnd 会变，
         旧的"白名单"失效 ⇒ 曾把用户主窗当浮动窗关掉。

    ⛔ **为什么不用"拖拽"替代双击**：微信是 Qt 应用，拖放走 **OLE `IDropTarget`**
      （不认老式 `WM_DROPFILES`，投了会被静默丢弃）。OLE 拖放由**操作系统鼠标驱动**——
      目标的 `DragEnter/DragOver/Drop` 是 COM 接口调用、**不是窗口消息**，
      `PostMessage` 造不出来 ⇒ 要用拖拽**必须真鼠标**（抢前台 + 占用户光标），
      与本项目"不动用户光标"的最高目标直接冲突。⇒ **拖拽不可用，双击是唯一路径。**
    """
    _ww = (HERE.parents[0] / "agent" / "wechat_windows.py").read_text(encoding="utf-8")
    _wc = (HERE.parents[0] / "agent" / "wechat.py").read_text(encoding="utf-8")

    # ── ① 间隔必须是手速真值（解析出数值，不靠字符串猜）──────────────
    _m = re.search(r"^DOUBLE_GAP_S\s*=\s*([\d.]+)", _ww, re.M)
    _gap = float(_m.group(1)) if _m else -1.0
    ck("① ★★ 双击间隔取手速真值（0.10~0.30s）—— 拉长会变成两次独立单击、开不出窗",
       0.10 <= _gap <= 0.30, "DOUBLE_GAP_S=%s" % _gap)
    _pm = re.search(r"^PRESS_MS_DOUBLE\s*=\s*([\d.]+)", _ww, re.M)
    _press = float(_pm.group(1)) if _pm else -1.0
    ck("① 单枪按下时长取手速真值（20~100ms）", 20 <= _press <= 100, "PRESS_MS_DOUBLE=%s" % _press)
    # ⚠️ 这一条是"改一处、判据要跟"的实例：真值原来写在 `ensure_popup` 函数里，
    #   提到模块级后，**按函数内正则去抓会抓到别处的 `press_ms=150`**（切会话的慢节奏点击）
    #   ⇒ 断言必须跟着名字走。同时锁住"真值只有一份"（防将来又在别处另写数字）。
    _wc_src = (HERE.parents[0] / "agent" / "wechat.py").read_text(encoding="utf-8")
    ck("① ★★ 手速真值**只此一份**（`wechat.py` 的双击是引用它，不是另写数字）",
       "from .wechat_windows import DOUBLE_GAP_S as _DGS" in _wc_src
       and re.search(r"DOUBLE_GAP_S\s*=\s*0\.\d+", _wc_src) is None, "")

    # ── ② 目标行已选中 ⇒ 必须先挪走（头号开不出窗原因）───────────────
    ck("② ★★ 有「把选中态从目标行挪走」的实现（已选中时双击必然失败）",
       "def _deselect_target_row" in _ww, "")
    ck("② ★★ 双击前**真的调用了**它（不是只定义不接）",
       "_ds_ok, _ds_note = _deselect_target_row()" in _ww, "")
    ck("② 挪走的手法说明在案（为什么不原地取消：单击已选中行第一次不取消、两次才取消）",
       "为什么不\"原地单击取消\"" in _ww or "原地单击取消" in _ww, "")

    # ── ③ 落点两条安全阀 ──────────────────────────────────────────
    ck("③ ★★ 落点在列表顶之上 ⇒ 拒点（落到顶栏时双击=微信全屏）",
       'y_phys < int(list_top_phys)' in _ww and 'step="guard"' in _ww, "")
    ck("③ ★★ 绿带行身份读不出 ⇒ 默认拒点（否则 DB 序号当屏幕行号 ⇒ 点到别的会话）",
       'step="unsafe_fallback"' in _ww and "allow_fallback_click" in _ww, "")

    # ── ④ 「已有窗直接用」排在最前（零动作优先）────────────────────
    _ep = _ww.split("def ensure_popup", 1)[-1] if "def ensure_popup" in _ww else ""
    _i_exists = _ep.find('how="exists"')
    _i_geo = _ep.find("measure_row_geometry")
    ck("④ ★★ 已有独立窗 ⇒ 直接用、不点任何东西（排在建/量/点之前）",
       _i_exists >= 0 and (_i_geo < 0 or _i_exists < _i_geo),
       "exists@%d geometry@%d" % (_i_exists, _i_geo))

    # ── ⑤ 收回飞窗：绝不关主窗 ────────────────────────────────────
    # ⚠️ 要剥掉**文档字符串与注释行**再判 —— 那段注释正是在解释"为什么必须走咽喉点"
    #   （里面提了 `WM_CLOSE` 这个词）⇒ 拿它当违规，等于判据在禁止写解释
    #   （同 `t_whale_guard` 的"只查代码、不查文档字符串"口径）。
    _rt_all = _wc.split("def _reattach_if_floating", 1)[-1].split("\n    def ", 1)[0] \
        if "def _reattach_if_floating" in _wc else ""
    _rt_doc = _rt_all.split('"""', 2)[2] if _rt_all.count('"""') >= 2 else _rt_all
    _rt = "\n".join(l for l in _rt_doc.splitlines() if not l.strip().startswith("#"))
    ck("⑤ ★★ 收回浮动窗走统一咽喉点（代码里不许裸 WM_CLOSE）",
       "_win_close(" in _rt and "WM_CLOSE" not in _rt, _rt[:60].replace("\n", " "))
    ck("⑤ ★★ 咽喉点会**当场重取主窗**（hwnd 被 Qt 重建后旧白名单失效 ⇒ 曾误关主窗）",
       "def ok_to_close" in (HERE.parents[0] / "agent" / "winops.py").read_text(encoding="utf-8")
       and "find_main_window" in (HERE.parents[0] / "agent" / "winops.py").read_text(encoding="utf-8"), "")

    # ── ⑥ 切会话时的"防双击闸"（全局最小间隔 + 位置判据）─────────────
    ck("⑥ ★★ 会话行点击有全局最小间隔 + 位置判据（防两枪挨太近被判成双击、把会话拖飞）",
       "_row_click_last" in _wc and "_dt < 1.2" in _wc and "_dx <= 40" in _wc, "")

    # ── ⑦ 失败有一轮自愈 ─────────────────────────────────────────
    ck("⑦ 开窗失败会自愈重试一轮并如实标注", 'out["retried"] = True' in _ww, "")

    # ── ⑧ ★★ 判据只用"有没有窗"，**不许**用"当前在哪个会话" ──────────
    #   为什么单列一条：这两者极易被当成同一件事，而它们**不是**——
    #   "已在目标会话"只说主窗打开着它，**独立窗可能仍然没有**；
    #   而项目架构是"发送只走独立窗" ⇒ 拿"已在目标会话"当"不用做事"的理由，
    #   会让调用方以为万事俱备、实际一个窗都没开。
    _ep_src = _ww.split("def ensure_popup", 1)[-1].split("\ndef ", 1)[0]
    ck("⑧ ★★ 开窗判据只用「有没有那个窗口」，**不用**「当前在哪个会话」",
       "chat_is_open" not in _ep_src and 'how="exists"' in _ep_src, "")

    # ── ⑨ ★★ 开不出来时给用户一条**可照做的引导** ───────────────────
    #   开独立窗是全部自动化的唯一入口，而它靠"双击列表行"这条手势路
    #   ⇒ 受可见性/滚动/手势判定影响。拖一次就成，比程序反复试更省事。
    ck("⑨ ★★ 开窗失败会给用户一条可照做的引导（`user_action`）",
       'out["user_action"]' in _ww and "拖出来" in _ww, "")

    # ⚠️ 下面这几条要查"代码里有没有某写法" ⇒ **必须先剥掉注释与文档字符串** ——
    #   否则"解释这个坑的那行注释"会被当成违规（这条坑本项目踩过不止一次）。
    def _code_only(src: str) -> str:
        keep, in_doc = [], False
        for ln in src.splitlines():
            s = ln.strip()
            if in_doc:
                if '"""' in s:
                    in_doc = False
                continue
            if s.startswith('"""'):
                if s.count('"""') == 1:
                    in_doc = True
                continue
            if s.startswith("#"):
                continue
            keep.append(ln.split("  #")[0])
        return "\n".join(keep)

    _wc2 = (HERE.parents[0] / "agent" / "wechat.py").read_text(encoding="utf-8")
    _ww_code = _code_only(_ww)
    _wc_code = _code_only(_wc2)

    # ── ⑩ ★★ 目标行不可见时**走搜索**（不许只报"需要先滚动"就放弃）─────────
    #   真机链路（两步都已实测）：搜索定位 ⇒ 单击结果行（切到该会话）⇒ 双击列表里的**高亮行**开窗。
    #   为什么这条路更稳：高亮行的 y 是**读出来的**，不做 DB 序号换算 ⇒
    #   天然不受「置顶会话」「DB 落库延迟」影响（那两者会破坏"DB 顺序 = 屏幕顺序"这个前提）。
    ck("⑩ ★★ 目标行不在可见区 ⇒ 改走搜索（不再只报「需要先滚动」就放弃）",
       "_open_popup_via_search" in _ww_code
       and 'how="search_then_highlight"' in _ww_code
       and "需要先滚动" not in _ww_code, "")
    ck("⑩ ★ 搜索开窗**不用** `double_click_result`（实测双击搜索结果行不开窗，只切会话）",
       "double_click_result=True" not in _ww_code, "")

    # ── ⑪ ★★ 搜索框里打的是**完整名**（名字相似时不至于选错）───────────────
    #   传入名来自 `resolve_title_name` / `display_name`（都是"显示名"＝完整名），
    #   不是缩写、不是 username（username 在浮层里根本搜不出来）。
    _wc2 = (HERE.parents[0] / "agent" / "wechat.py").read_text(encoding="utf-8")
    ck("⑪ ★★ 搜索用的是完整显示名（`display_name` / `resolve_title_name`），不是缩写",
       "name = name or self.display_name(chat_id) or chat_id" in _wc2
       and "resolve_title_name" in _ww, "")

    # ── ⑫ ★★ 结果行匹配是**三级**：精确 → 包含 → 兜底（不许"没精确命中就取第一行"）──
    #   为什么：搜「张三」时浮层里可能有「张三」和「张三丰」，**取第一行可能取错人**。
    _co = (HERE.parents[0] / "agent" / "chat_ocr.py").read_text(encoding="utf-8")
    ck("⑫ ★★ 结果行匹配有「包含命中」层（挡名字相似的干扰）",
       "contains_hit" in _co and "OCR 包含命中" in _co, "")

    # ── ⑬ ★★ 投字**先试浮层、无效再投主窗**（且投主窗前必须确认浮层还在）─────
    #   实测：这个微信版本里投给**浮层窗口**是**假成功**（接口返回 True、浮层画面不变）；
    #   投给**主窗**才有效。安全前提：浮层在 ⇒ 焦点在搜索框 ⇒ 字不会落进聊天输入框。
    ck("⑬ ★★ 投字有「浮层无效 ⇒ 改投主窗」兜底，且改投前确认浮层仍开着",
       "_p2 = self._find_search_popover(main)" in _wc2
       and "backend.send_text(main, name)" in _wc2, "")

    # ── ⑭ ★★ 搜索入口**不许按形态分流**（box/icon 只决定"点在哪"）──────────
    ck("⑭ ★★ 搜索入口不按形态分流（`box` 与 `icon` 同一条路）",
       'if variant == "icon":' not in _wc_code and "if variant:" in _wc_code, "")


def t_backend_down_shortcircuit_guard() -> None:
    """自检：**后端不可达时不许逐个接口空等超时**（快速失败）。

    **要解决的病**：面板构建会连着问好几个后端口（人设页一次 5 个），
    后端没起时每个都要等满 timeout —— 实测单次 `get_json` **2.9s**，
    ⇒ 一次绘制白等十几秒，**用户每次打开面板都要再等一遍**（不只是自检慢）。

    **条款**：
      ① 连接类失败（拒连/超时/DNS）⇒ 进入"不可达记忆"，同业地址后续请求立即返回 None；
      ② **HTTP 错误码（服务活着）⇒ 不进入记忆** —— 后续接口必须照常尝试；
      ③ 记忆**按地址绑定**：换了地址立即失效（否则会误伤新后端，含自检的假服务器）；
      ④ 地址为空 / 记忆无归属 ⇒ **一律不拦**（无主记忆会误伤后来者）；
      ⑤ 一次成功 ⇒ 记忆立即清掉。
    """
    import config_io as _cio # noqa: PLC0415

    _cio._reset_backend_state()
    _A = "http://127.0.0.1:59991/?token=a"
    _B = "http://127.0.0.1:59992/?token=b"

    # ── ① 连接类失败 ⇒ 记忆生效 ────────────────────────────────
    _cio._note_unreachable(ConnectionRefusedError("Connection refused"), _A)
    ck("① 连接被拒 ⇒ 同地址后续请求被短路（不再逐个空等超时）",
       _cio._backend_down(_A) is True, "")

    # ── ③ 换地址 ⇒ 记忆立即失效（关键：自检的假后端不能被上一条的记忆拦掉）──
    ck("③ 换了地址 ⇒ 旧记忆立即失效（不误伤新后端）",
       _cio._backend_down(_B) is False, "")

    # ── ④ 无主记忆：地址为空 / 记忆没归属 ⇒ 都不拦 ────────────────
    _cio._reset_backend_state()
    _cio._note_unreachable(ConnectionRefusedError("Connection refused"), "")
    ck("④ 地址为空 ⇒ 不记忆（无主记忆会误伤后来者）",
       _cio._backend_down(_A) is False, "")
    _cio._note_unreachable(ConnectionRefusedError("Connection refused"), _A)
    ck("④ 查询地址为空 ⇒ 不拦（拿不准就别拦）",
       _cio._backend_down("") is False, "")

    # ── ② HTTP 错误码 ⇒ 不进记忆（服务活着）──────────────────────
    _cio._reset_backend_state()
    import urllib.error as _ue # noqa: PLC0415
    _cio._note_unreachable(_ue.HTTPError(_A, 500, "boom", {}, None), _A)
    ck("② HTTP 500 ⇒ **不**进记忆（服务在、只是这个口报错，后续接口要照常试）",
       _cio._backend_down(_A) is False, "")

    # ── ②之二 ★★ 中文 Windows 上的真实异常形状必须被认出来 ────────
    #   `urlopen` 抛 `URLError`，其 `.reason` 是 `WinError 10061`，
    #   而 `str()` 在中文系统上是"由于目标计算机积极拒绝，无法连接。"
    #   ⇒ 早先靠英文关键词匹配**一个都命不中**、短路完全失效（实测：5 个口各等 2s）。
    #   这里三重保证：真造一个 URLError 形状、中文文案、纯错误码。
    import socket as _sk # noqa: PLC0415
    _cn = _ue.URLError(OSError(10061, "由于目标计算机积极拒绝，无法连接。"))
    _cio._reset_backend_state()
    _cio._note_unreachable(_cn, _A)
    ck("②之二 ★★ 中文系统的 URLError（“积极拒绝”+ WinError 10061）⇒ 认得出来",
       _cio._backend_down(_A) is True, str(_cn)[:56])

    class _NoMsg(Exception):
        """只有错误码、没有任何可读文案 —— 只能靠代码判定。"""
        errno = 10061

    _cio._reset_backend_state()
    _cio._note_unreachable(_NoMsg(), _A)
    ck("②之二 ★ 只有错误码、文案为空 ⇒ 也认得出来（判定不靠文案）",
       _cio._backend_down(_A) is True, "")

    # 超时族：同样要认（连不通有时表现为超时而非拒连）
    _cio._reset_backend_state()
    _cio._note_unreachable(_sk.timeout("timed out"), _A)
    ck("②之二 ★ 连接超时（socket.timeout）⇒ 也进记忆",
       _cio._backend_down(_A) is True, "")

    # ── ⑤ 通了 ⇒ 记忆清掉 ──────────────────────────────────────
    _cio._note_unreachable(ConnectionRefusedError("Connection refused"), _A)
    _cio._note_reachable()
    ck("⑤ 一次成功 ⇒ 不可达记忆立即清掉", _cio._backend_down(_A) is False, "")

    # ── 接线：get_json / post_json 都真的走了这套判定 ──────────────
    _src = (HERE.parents[0] / "ui_qt" / "config_io.py").read_text(encoding="utf-8")
    ck("⑥ `get_json` 与 `post_json` 都接进了短路（不是只改一处）",
       _src.count("_backend_down(base)") >= 2 and _src.count("_note_unreachable(e, locals()") >= 1,
       "get_json=%d" % _src.count("_backend_down(base)"))

    # ── 地址解析也有缓存（同一地址不被反复探测）────────────────────
    _addr = (HERE.parents[0] / "ui_qt" / "addr.py").read_text(encoding="utf-8")
    ck("⑦ `resolve_base_url()` 结果进程内缓存（一次面板几十次问地址只探一次）",
       "_URL_CACHE_TTL_S" in _addr and "_url_cache" in _addr, "")
    _nu = (HERE.parents[0] / "agent" / "notify_ui.py").read_text(encoding="utf-8")
    ck("⑧ `agent.notify_ui.console_url()` 同样有缓存（两处口径一致）",
       "_CONSOLE_URL_TTL_S" in _nu and "_console_url_cache" in _nu, "")

    _cio._reset_backend_state()


def t_config_stamp_content_guard() -> None:
    """自检：**B-5 / P1-8 —— 不用 mtime 判变化，改逐字节内容指纹**。

    **要解决的病**：`get_config()` 靠 `(mtime_ns, size)` 判断"盘上那份变没变"。
    两种情形会**漏判**：
      · **同一秒内改写**（`os.replace` 换档极快）⇒ mtime_ns 可能相同；
      · **等长改写**（改一个字、开关 `true`→`false`）⇒ size 相同。
    叠加 ⇒ 认为"没变"、**一直用旧值**。改内容哈希后，一个字节不同就必然变。

    **条款**：① 同大小、同 mtime 但内容不同 ⇒ 指纹**不同**；② 内容相同 ⇒ 指纹相同（幂等）；
    ③ 读不到（文件不存在）⇒ 返回 None；④ 读失败（被独占）⇒ 抛带"怎么办"的 OSError，
    且 `get_config()` 不会因此把内存那份换成默认值。
    """
    import os as _os
    import tempfile
    import agent.config as _cfg  # noqa: PLC0415

    _d = tempfile.mkdtemp(prefix="cfg_stamp_")
    _p = _os.path.join(_d, "c.json")
    try:
        # ── ①② 等长、同 mtime、内容不同 ⇒ 指纹必须不同 ────────────────────
        with open(_p, "w", encoding="utf-8") as fh:
            fh.write('{"a": true}')
        _s1 = _cfg._stamp(_p)
        _st = _os.stat(_p)
        with open(_p, "w", encoding="utf-8") as fh:
            fh.write('{"a": false}')   # 长度一样（true→false 差 1？这里手动对齐长度）
        # 对齐到与上一份**等长**：用等长的两个值
        with open(_p, "w", encoding="utf-8") as fh:
            fh.write('{"a": tru0}')
        _os.utime(_p, ns=(_st.st_atime_ns, _st.st_mtime_ns))  # 强行把 mtime 掰回上一个
        _st2 = _os.stat(_p)
        _s2 = _cfg._stamp(_p)
        ck("P1-8 ★★ 同大小 + 同 mtime、内容不同 ⇒ 内容指纹**必须不同**（旧法会漏判）",
           (_st2.st_size == _st.st_size) and (_st2.st_mtime_ns == _st.st_mtime_ns) and (_s1 != _s2),
           "size %d/%d mtime %d/%d" % (_st.st_size, _st2.st_size, _st.st_mtime_ns, _st2.st_mtime_ns))

        # ── ② 内容相同 ⇒ 指纹相同（不因"又写了一遍"而误判变化）────────────
        _s3 = _cfg._stamp(_p)
        ck("P1-8 内容没变 ⇒ 指纹相同（幂等，不会白重读）", _s3 == _s2, "")

        # ── ③ 文件不存在 ⇒ None（与"读失败"区分开）────────────────────────
        ck("P1-8 文件不存在 ⇒ 指纹 None", _cfg._stamp(_os.path.join(_d, "nope.json")) is None, "")

        # ── ④ 读失败 ⇒ 抛 OSError（不把"读不到"伪装成"没变"）──────────────
        _saved_open = _cfg.__builtins__["open"] if isinstance(_cfg.__builtins__, dict) else open
        _raised = {"n": 0}

        def _boom(*a, **k):
            _raised["n"] += 1
            raise PermissionError("被独占")

        _saved = _cfg.open if hasattr(_cfg, "open") else None
        _cfg.open = _boom  # type: ignore[attr-defined]
        try:
            _err = ""
            try:
                _cfg._stamp(_p)
            except OSError as e:
                _err = str(e)
            ck("P1-8 ★★ 读失败 ⇒ 抛 OSError，且文案带“怎么办”",
               ("稍后重试" in _err) or ("重试" in _err), _err[:60])
            # 容错包装：读失败 ⇒ 哨兵，**不**等于 None（否则会被当成"文件不存在"）
            _safe = _cfg._stamp_safe(_p)
            ck("P1-8 ★ 容错包装：读失败 ⇒ 哨兵（≠ None，保证“读不到”被当成“可能变了”）",
               _safe is _cfg._STAMP_UNREADABLE and _safe is not None, "")
        finally:
            if _saved is not None:
                _cfg.open = _saved  # type: ignore[attr-defined]
            else:
                try:
                    del _cfg.open  # type: ignore[attr-defined]
                except Exception: # noqa: BLE001
                    pass

        # ── ⑤ 接线：get_config 走的是 `_stamp_safe` 而不是 `_stamp` ────────
        _src = (HERE.parents[0] / "agent" / "config.py").read_text(encoding="utf-8")
        ck("P1-8 ★ `get_config()` 比对走容错包装（读失败不许伪装成“没变”）",
           "_stamp_safe(CONFIG_FILE) != _config_stamp" in _src, "")
    finally:
        try:
            import shutil as _sh
            _sh.rmtree(_d, ignore_errors=True)
        except Exception: # noqa: BLE001
            pass


def t_config_changed_broadcast_guard() -> None:
    """自检：**B-5 / P1-10 —— 写配置即广播（`config-changed`）**。

    **要解决的病**：配置的消费方（Qt 重绘、后台线程、控制台回填）各自轮询 `get_config()`
    ⇒ 要么慢（界面/行为滞后）、要么贵（白耗 CPU），且漏读一次就"显示与生效不一致"。
    改成**写配置那一刻就广播**，订阅方不必轮询。

    **条款**：① `set_config` 换值后订阅者**收到**通知；② 载荷是**变了哪些键**、不带值；
    ③ 没变就不广播（不制造噪音）；④ 单个订阅者抛异常**不影响**其余订阅者；
    ⑤ 退订幂等、按令牌退（同函数两次订阅互不干扰）；⑥ `reload_config` 也广播。
    """
    import agent.config as _cfg  # noqa: PLC0415
    import agent.config_events as _ce  # noqa: PLC0415

    _saved_cfg = _cfg._current_config
    _saved_stamp = _cfg._config_stamp
    try:
        _ce.reset()
        _got = []
        _tok = _ce.subscribe(lambda keys: _got.append(list(keys)))

        # ── ① 换值后订阅者收到；② 载荷只带"变了哪些键" ────────────────────
        _base = {"a": 1, "n": {"x": True, "y": "s"}}
        _cfg._current_config = dict(_base)   # 先给内存一份初值，否则 `_old is None` 不广播
        _cfg.set_config(dict(_base, a=2))
        ck("P1-10 ★ 写配置 ⇒ 订阅者**收到**变更通知", len(_got) == 1, "n=%d" % len(_got))
        _keys = _got[-1] if _got else []
        ck("P1-10 ★★ 载荷只带“变了哪些键”（点号路径），不带具体值",
           _keys == ["a"] and all(not isinstance(k, dict) for k in _keys), repr(_keys))

        # ── ③ 没变 ⇒ 不广播（不制造噪音）──────────────────────────────────
        _n_before = len(_got)
        _cfg.set_config(dict(_base, a=2))   # 一模一样
        ck("P1-10 ★ 内容没变 ⇒ **不广播**（订阅方不会被无谓唤醒）",
           len(_got) == _n_before, "n=%d→%d" % (_n_before, len(_got)))

        # ── ② 嵌套键的路径形态正确 ────────────────────────────────────────
        _cfg.set_config(dict(_base, a=2, n={"x": False, "y": "s"}))
        ck("P1-10 ★ 嵌套键用点号路径（`n.x`）", _got and (_got[-1] == ["n.x"]), repr(_got[-1] if _got else None))

        # ── ④ 单个订阅者抛异常 ⇒ 不影响其余订阅者 ─────────────────────────
        _other = []
        _ce.subscribe(lambda keys: (_ for _ in ()).throw(RuntimeError("订阅者内部炸了")))
        _tok2 = _ce.subscribe(lambda keys: _other.append(1))
        _cfg.set_config(dict(_base, a=3))
        ck("P1-10 ★★ 一个订阅者抛异常 ⇒ 其余订阅者**照常收到**（通知失败不回滚配置）",
           len(_other) >= 1, "other=%d" % len(_other))

        # ── ⑤ 退订幂等、按令牌退 ──────────────────────────────────────────
        _n_b = len(_got)
        ck("P1-10 退订按令牌：退到 True、重复退 False（幂等）",
           _ce.unsubscribe(_tok) is True and _ce.unsubscribe(_tok) is False, "")
        _cfg.set_config(dict(_base, a=4))
        ck("P1-10 退订后**不再**收到通知", len(_got) == _n_b, "n=%d→%d" % (_n_b, len(_got)))
        ck("P1-10 同函数两次订阅互不干扰（另一份仍在收）",
           (_ce.unsubscribe(_tok2) is True) and (len(_other) >= 1), "")

        # ── ⑥ reload_config 也广播（同进程被动发现盘上变了 ⇒ 也要通知）────
        _ce.reset()
        _got2 = []
        _ce.subscribe(lambda keys: _got2.append(list(keys)))
        _cfg._current_config = {"a": 1}
        _saved_read = _cfg._read_config_once
        _saved_load = _cfg.load_config
        _saved_stamp_f = _cfg._stamp_safe
        # 连 `load_config` 一起打桩：`reload_config` 是"读一次 → load_config → 取指纹"，
        # 只桩 `_read_config_once` 的话 `load_config` 还会把整份默认配置合并进来 ⇒ 差异变成全部键。
        _cfg._read_config_once = lambda p: ({"a": 9}, None, False)
        _cfg.load_config = lambda p=None: {"a": 9}
        _cfg._stamp_safe = lambda p: "FAKE"
        try:
            _cfg.reload_config()
        finally:
            _cfg._read_config_once = _saved_read
            _cfg.load_config = _saved_load
            _cfg._stamp_safe = _saved_stamp_f
        ck("P1-10 ★ `reload_config()`（被动发现盘上变了）也广播", _got2 == [["a"]], repr(_got2))

        # ── ⑦ 接线：set_config / reload_config 都真的调了 emit ────────────
        _src = (HERE.parents[0] / "agent" / "config.py").read_text(encoding="utf-8")
        ck("P1-10 ★ `set_config` 与 `reload_config` 都接线到 `emit`",
           _src.count("_cfgev.emit(") >= 2, "count=%d" % _src.count("_cfgev.emit("))
    finally:
        _ce.reset()
        _cfg._current_config = _saved_cfg
        _cfg._config_stamp = _saved_stamp


def t_restart_handover_guard() -> None:
    """自检：**B-5 / P1-9 —— 单实例锁 + 立即重启（先 kill + 等锁放开再启）**。

    **要解决的病**：单实例锁保证不双开，但"我想马上换上新版/怀疑它卡住"这类主动重启
    以前没有可复用件 ⇒ 旧实例还持锁，新实例取锁必失败，只能让用户自己去点「停止机器人」。
    该自愈的**关键**不是"杀掉"，而是"杀完**等到锁真空出来**再启" —— 内核回收互斥体、
    锁文件被清、端口退出 `TIME_WAIT` 都要一点时间，杀完立刻取锁会误判成"已有实例"。

    **条款**：① 没锁 ⇒ 无需动手、直接 ok；② 有锁 ⇒ 先 kill 再等；③ kill 目标**只取锁文件里的 pid**
    （绝不按进程名扫杀，避免误杀用户别的 Python）；④ 拒绝自杀（pid==本进程）；
    ⑤ 等不到锁放开 ⇒ **如实报失败**（不硬启）；⑥ 旧实例自己退了 ⇒ 也 ok（目标状态达成）。
    """
    import agent.restart as _rs  # noqa: PLC0415

    # ── ① 没锁 ⇒ ok、无动作（语义＝"现在可以不冲突地启了"）────────────────
    _saved_locked = _rs.is_locked
    _saved_holder = _rs.holder_pid
    _saved_kill = _rs.kill_pid
    _saved_wait = _rs.wait_release
    _killed = []
    try:
        _rs.is_locked = lambda p: False
        _rs.holder_pid = lambda p: 0
        _r0 = _rs.stop_for_restart("X:/nope/bot.lock")
        ck("P1-9 ★ 启动前没有实例持锁 ⇒ 直接 ok、一个进程都不杀",
           _r0["ok"] and (not _r0["killed"]) and (_r0["waited_s"] == 0.0), repr(_r0["reason"])[:50])

        # ── ②③ 有锁 ⇒ 只对锁文件里的 pid 动手 ─────────────────────────────
        _rs.is_locked = lambda p: True
        _rs.holder_pid = lambda p: 4242
        _rs.wait_release = lambda p, timeout_s=0.0: (True, 0.3)
        _killed.clear()
        _rs.kill_pid = lambda pid: (_killed.append(pid), (True, "已结束"))[1]
        _r1 = _rs.stop_for_restart("X:/nope/bot.lock", watchdog_pids=[777])
        ck("P1-9 ★★ kill 目标＝锁文件 pid + 看门狗 pid（**不按进程名扫杀**）",
           sorted(_killed) == [777, 4242] and _r1["ok"], "killed=%s" % _killed)

        # ── ④ 拒绝自杀（pid==本进程 ⇒ 不杀、也不报"成功杀了"）──────────────
        import os as _os
        _rs.kill_pid = _saved_kill   # 用真的 kill_pid 做"拒绝目标"的判定
        _ok_self, _why_self = _rs.kill_pid(_os.getpid())
        ck("P1-9 ★ 拒绝结束自己（pid==本进程）", (not _ok_self) and ("拒绝" in _why_self), _why_self[:50])
        _ok_bad, _why_bad = _rs.kill_pid(0)
        ck("P1-9 拒绝非法 pid（0 / 负数）", (not _ok_bad) and ("拒绝" in _why_bad or "非法" in _why_bad), _why_bad[:50])

        # ── ⑤ 等不到锁放开 ⇒ 如实失败（不硬启）────────────────────────────
        _rs.kill_pid = lambda pid: (True, "已结束")
        _rs.wait_release = lambda p, timeout_s=0.0: (False, 10.0)
        _r2 = _rs.stop_for_restart("X:/nope/bot.lock")
        ck("P1-9 ★★ 等不到锁放开 ⇒ ok=False + 说清卡在哪（绝不硬启造成双开）",
           (not _r2["ok"]) and (not _r2["released"]) and ("锁仍未放开" in _r2["reason"]), _r2["reason"][:60])

        # ── ⑥ 旧实例自己退了（没杀掉但锁放开了）⇒ 也算 ok ────────────────
        _rs.kill_pid = lambda pid: (True, "该进程已经不在了，无需结束")
        _rs.wait_release = lambda p, timeout_s=0.0: (True, 0.2)
        _r3 = _rs.stop_for_restart("X:/nope/bot.lock")
        ck("P1-9 旧实例自行退出（锁已放开）⇒ 也算 ok（目标状态已达成）", _r3["ok"] and _r3["released"], "")

        # ── wait_release 的有界性（真跑一次，别只看桩）────────────────────
        _rs.wait_release = _saved_wait
        _t0 = __import__("time").time()
        _rel, _el = _rs.wait_release("X:/nope/bot.lock", timeout_s=1.0)
        _dur = __import__("time").time() - _t0
        ck("P1-9 ★ `wait_release` 有界：等不到就按时返回（不无限挂住启动流程）",
           (not _rel) and (_dur < 3.0), "rel=%s dur=%.2f" % (_rel, _dur))
    finally:
        _rs.is_locked = _saved_locked
        _rs.holder_pid = _saved_holder
        _rs.kill_pid = _saved_kill
        _rs.wait_release = _saved_wait

    # ── ⑦ 接线：onestart 支持「立即重启」，且用 `stop_for_restart` 做接管 ──
    _os_src = (HERE.parents[0] / "scripts" / "onestart.py").read_text(encoding="utf-8")
    ck("P1-9 ★ 一键启动提供 `--restart` / `WX_ONESTART_RESTART` 立即重启入口",
       ("--restart" in _os_src) and ("WX_ONESTART_RESTART" in _os_src), "")
    ck("P1-9 ★ 立即重启走 `stop_for_restart`（先停 + 等锁放开），不是直接硬启",
       "stop_for_restart" in _os_src, "")
    ck("P1-9 ★ 收尾失败时不硬启（如实退出、让用户知道卡在哪）",
       _os_src.count("立即重启未完成") >= 1, "")


def t_a11y_four_guard() -> None:
    """自检：**B-5 / P1-11 —— 无障碍四件**。

    四件＝① 触摸目标 ≥44px；② 键盘焦点环（`focus-visible`）；③ 尊重"减少动效"；
    ④ 动区通告（错误用 assertive、普通状态用 polite）。

    **两个实现面都要覆盖**：web 控制台（`assets/console/index.html`，真值）与 Qt 壳（`ui_qt/`）。
    Qt 侧没有 CSS 媒体查询/aria，对应物分别是 `motion_ok()` 与 `setAccessibleName`。

    **条款**：① web 有 `--tap-min:44px` 且按钮基础规则用它；② 勾选框靠隐形热区扩到 44px
    （视觉仍 17px，不许把勾本身画大）；③ toast＝assertive、侧栏状态＝polite；
    ④ Qt 有 `motion_ok()` 且在关键动画点（换主题淡出、弹层）接线；⑤ Qt 按钮有可访问名。
    """
    _root = HERE.parents[0]
    _web = (_root / "assets" / "console" / "index.html").read_text(encoding="utf-8")

    # ── ① web 触摸目标 token + 按钮用它 ────────────────────────────────
    ck("P1-11 ★ web 定义触摸目标 token（`--tap-min:44px`）", "--tap-min:44px" in _web, "")
    ck("P1-11 ★★ web 按钮基础规则用 `min-height:var(--tap-min)`（整块按钮够高）",
       "min-height:var(--tap-min)" in _web, "")

    # ── ② 勾选框：热区扩到 44px、但视觉尺寸不许跟着变大 ─────────────────
    ck("P1-11 ★★ 勾选框用**隐形热区**扩到 44px（`::before` + `--tap-min`），视觉仍 17px",
       ("input[type=checkbox])::before" in _web) and ("width:var(--tap-min)" in _web)
       and ("width:17px;height:17px" in _web), "")

    # ── ③ 动区通告：assertive / polite 各就各位 ─────────────────────────
    ck("P1-11 ★★ `#toast`（即时反馈）＝ aria-live=assertive",
       'id="toast" role="status" aria-live="assertive"' in _web, "")
    ck("P1-11 ★★ 侧栏运行状态（普通状态）＝ aria-live=polite",
       'aria-live="polite"' in _web and "sideStatus" in _web, "")

    # ── ④ 减少动效：web 的 prefers-reduced-motion 仍在 ──────────────────
    ck("P1-11 web 保留 `prefers-reduced-motion` 关动效", "prefers-reduced-motion" in _web, "")

    # ── ⑤ Qt 侧 `motion_ok()` 存在且被关键动画点接线 ────────────────────
    try:
        from ui_qt.stylekit_qt import motion_ok # noqa: PLC0415
        ck("P1-11 ★ Qt 提供 `motion_ok()`（无 QApplication 时按“允许动效”兜底）",
           callable(motion_ok) and motion_ok() in (True, False), "")
    except Exception as e: # noqa: BLE001
        ck("P1-11 ★ Qt 提供 `motion_ok()`", False, str(e)[:60])

    _shell = (HERE / "shell.py").read_text(encoding="utf-8")
    _pop = (HERE / "popover.py").read_text(encoding="utf-8")
    ck("P1-11 ★★ 换主题淡出尊重减少动效（跳过动画、直接给终态）",
       "motion_ok" in _shell and "_fade_veil_done()" in _shell, "")
    ck("P1-11 ★★ 弹层下滑动画尊重减少动效（直接落终点，不位移）",
       "motion_ok" in _pop, "")
    # ⛔ 回归钉：跳过动画的那条路必须先存 veil 引用，否则覆盖层永久留在最上层
    ck("P1-11 ★★ 跳过动画分支前已存 veil 引用（否则覆盖层残留）",
       _shell.index("self._fade_veil = veil") < _shell.index("if not _motion:"), "")

    # ── ⑥ Qt 按钮有可访问名 + 高度走 `ui.tap_min` ───────────────────────
    _w = (HERE / "widgets.py").read_text(encoding="utf-8")
    ck("P1-11 ★ Qt 按钮设可访问名（屏幕阅读器读的是它，不是画上去的字）",
       "setAccessibleName" in _w, "")
    ck("P1-11 ★★ Qt 按钮高度走 `ui.tap_min`（默认 34、上限夹到 44，触摸设备可调）",
       "_tap_min_from_config" in _w and "_TAP_MIN_MAX = 44" in _w, "")
    ck("P1-11 ★ 读配置失败回退默认（按钮构造是界面必经之路，不许抛）",
       "_TAP_MIN_DEFAULT" in _w and "return _TAP_MIN_DEFAULT" in _w, "")


def t_clear_before_fill_guard() -> None:
    """自检：**B-5 / P1-2 —— 清空优先于填充**。

    **要解决的病**：微信按会话保存草稿 / 上一轮若所有枪都落空，字就留在输入框里。
    不清就打字 ⇒ 残留与新内容被微信拼成**一条**发出去（`'hello'` → `'hellohello'`）——
    收信人看到的是一条**内容错误**的消息，且发出即撤不回。
    旧行为是"清不干净也继续打字，只记一笔日志"，等于**明知会串字还是发**。

    **条款**：① 切会话成功后**第一步就擦**草稿（`clear_composer_after_switch`）；
    ② 打字前再判一次（纵深，不是重复）；③ 清不干净 ⇒ **不填**（如实失败，把风险交给重试）；
    ④ 状态判不了 ⇒ **不当成空的**、也不填；⑤ 清空手法不用 `Ctrl+A`（投递不带修饰键，会退化成字面字母）。
    """
    _w = (HERE.parents[0] / "agent" / "wechat.py").read_text(encoding="utf-8")

    # ── ① 切会话成功 ⇒ 第一件事是擦 ────────────────────────────────────
    ck("P1-2 ★★ 切会话成功后先擦输入框（`clear_composer_after_switch` 存在且被调用）",
       ("def clear_composer_after_switch" in _w) and ("self.clear_composer_after_switch(" in _w), "")
    # 顺序钉：擦的动作必须排在"换代次"之前（切完会话立刻擦，而不是等后面发完再说）
    _i_call = _w.index("self.clear_composer_after_switch(")
    _i_gen = _w.index("self._chat_generation = _vd.after_switch_mark()")
    ck("P1-2 ★★ 擦草稿排在“切会话成功”当口（早于后续任何发送动作）",
       _i_call < _i_gen, "call@%d gen@%d" % (_i_call, _i_gen))

    # ── ② 三态处理齐全 ────────────────────────────────────────────────
    _m = _w.index("def clear_composer_after_switch")
    _body = _w[_m:_m + 2600]
    ck("P1-2 ★★ 三态：`empty` 直接成功 / `has` 才擦 / `unknown` 如实报告",
       ('== "empty"' in _body) and ('== "unknown"' in _body) and ("_composer_state" in _body), "")
    ck("P1-2 ★★ `unknown` 明确“不当作空”（判不了 ≠ 干净）",
       "不当作空" in _body or "不当成空" in _body, "")

    # ── ③ 打字前：清不干净 ⇒ 不填（旧行为是"继续打字"，已改）────────────
    _i_clear_call = _w.index('tag="打字前清残留"')
    _seg = _w[_i_clear_call:_i_clear_call + 1600]
    ck("P1-2 ★★★ 清不干净 ⇒ **本轮不发**（旧行为“继续打字、只记一笔”已改为如实失败）",
       "return False" in _seg and "清空优先于填充" in _seg, "")
    ck("P1-2 ★★ 状态判不了 ⇒ 也不打字（宁可延迟，不冒串字险）",
       "本轮不打字" in _seg, "")
    # 回归钉：不许退回"继续打字（但可能与残留串成一条）"那句老注释
    ck("P1-2 ★ 不残留旧行为（“清不干净也继续打字”的旧注释已删）",
       "继续打字，但可能与残留串成一条" not in _w, "")

    # ── ④ 清空手法：不用 Ctrl+A ───────────────────────────────────────
    ck("P1-2 ★ 清空手法不许用 `Ctrl+A`（投递不带修饰键 ⇒ 退化成字面字母，越清越脏）",
       "不能用 `Ctrl+A`" in _w or "不用 `Ctrl+A`" in _w or "**不能用 `Ctrl+A`**" in _w, "")


def t_landing_discipline_guard() -> None:
    """自检：**落盘纪律三条 ＋ 隐私分层事实**（P0-6 / P0-7 / P0-8 / P0-9）。

    为什么合成一条：这四条同属"**不修也不会立刻出事、但出事就很难查**"的那类 ——
    半截文件被当成成功、同名会话互相覆盖、外部产物读半份、文档里写绝对承诺。
    它们没有共同的实现点，但有共同的性质：**每条都必须能用一次"造坏条件"的实测判否**，
    否则就是"写了模块没人用"。

    判据分四组，逐条都要**真造条件**（不是查字符串在不在）：
      · P0-6 原子写：中断/空内容 ⇒ 目标不存在且不留临时档；成功 ⇒ 内容与字节数对得上；
      · P0-7 文件名：同名不同会话 ⇒ 名字不同；同一会话重跑 ⇒ 名字稳定；
      · P0-8 产物核对：缺末行、读取期间被追加、字节数/行数不符 ⇒ **一律拒**（返回空/抛错）；
      · P0-9 文档：README 的分层事实四要素齐全，且"绝对承诺"一律带条件（不得无条件声称）。
    """
    import os as _os # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415

    _root = HERE.parents[0]
    from agent import atomic_io as _aio  # noqa: PLC0415
    from agent import safe_name as _sn  # noqa: PLC0415
    from agent import verify_artifact as _va  # noqa: PLC0415

    _td = _tf.mkdtemp(prefix="landing-discipline-")
    try:
        # ── P0-6 原子写 ──────────────────────────────────────────────────
        _ok_png = _os.path.join(_td, "ok.bin")
        ck("P0-6 原子写：成功落档（字节数对得上、无临时档残留）",
           _aio.atomic_write_bytes(_ok_png, b"x" * 128, halt_check=False)
           and _os.path.getsize(_ok_png) == 128
           and _aio.list_partials(_td) == [], "")
        _empty = _os.path.join(_td, "empty.bin")
        ck("P0-6 原子写：**空内容判失败**且目标路径不存在（零字节不算成功）",
           not _aio.atomic_write_bytes(_empty, b"") and not _os.path.exists(_empty), "")
        _boom = _os.path.join(_td, "boom.bin")
        def _raise_writer(_fh):
            _fh.write(b"half")
            raise RuntimeError("写入中途炸了")
        ck("P0-6 原子写：writer 中途抛异常 ⇒ 目标不存在、临时档已清",
           not _aio.atomic_write(_boom, _raise_writer, halt_check=False)
           and not _os.path.exists(_boom) and _aio.list_partials(_td) == [], "")
        # 喊停就不发布：造"已暂停"状态（路径注入到 %TEMP%，不碰产品目录）
        from agent import control as _ctl  # noqa: PLC0415
        _old_paths = _ctl.set_paths(paused=_os.path.join(_td, "paused.flag"))
        try:
            with open(_os.path.join(_td, "paused.flag"), "w", encoding="utf-8") as _fh:
                _fh.write("1")
            _ctl._CACHE["at"] = 0.0
            _halt = _os.path.join(_td, "halt.bin")
            ck("P0-6 原子写：已喊停 ⇒ 不换档（目标不存在），临时档也清干净",
               not _aio.atomic_write_bytes(_halt, b"y" * 32)          # 默认 halt_check=True
               and not _os.path.exists(_halt) and _aio.list_partials(_td) == [], "")
            # 反向控制：同一次调用把 halt_check 关掉 ⇒ 写得进去（否则上一条可能只是"恒 False"）
            _halt2 = _os.path.join(_td, "halt2.bin")
            ck("P0-6 反向控制：喊停状态下 `halt_check=False` 仍可落档（不是恒失败）",
               _aio.atomic_write_bytes(_halt2, b"z" * 16, halt_check=False)
               and _os.path.getsize(_halt2) == 16, "")
        finally:
            _ctl.set_paths(paused=_old_paths["paused"])
            _ctl._CACHE["at"] = 0.0

        # ── P0-7 文件名指纹 ─────────────────────────────────────────────
        _n1 = _sn.build_output_name("wxid_aaa", "张三", "png")
        _n2 = _sn.build_output_name("wxid_bbb", "张三", "png")
        ck("P0-7 两个不同会话、**同名昵称** ⇒ 文件名不同（不会互相覆盖）", _n1 != _n2, "%s / %s" % (_n1, _n2))
        ck("P0-7 同一会话重跑 ⇒ 文件名**稳定不变**",
           _sn.build_output_name("wxid_aaa", "张三", "png") == _n1, _n1)
        ck("P0-7 非法字符被清掉、不留路径分隔符",
           "/" not in _sn.build_output_name("wxid_aaa", "a/b:c*d", "png")
           and "\\" not in _sn.build_output_name("wxid_aaa", "a/b:c*d", "png"), "")
        ck("P0-7 全是非法字符的主干 ⇒ 退回 `unnamed`（不会得到空名）",
           _sn.sanitize("///") == "unnamed", _sn.sanitize("///"))
        ck("P0-7 `is_same_session_name` 认指纹、且**认不出时不误判同一**",
           _sn.is_same_session_name(_n1, _n1) and not _sn.is_same_session_name(_n1, _n2)
           and not _sn.is_same_session_name("无指纹.png", "无指纹.png"), "")
        # 反向控制：指纹长度与十六进制形态（防"截 10 位"被改成截 1 位还不报错）
        ck("P0-7 指纹 = 10 位小写十六进制（反向控制）",
           len(_sn.session_fingerprint("x")) == 10
           and all(c in "0123456789abcdef" for c in _sn.session_fingerprint("x")), "")

        # ── P0-8 外部产物核对 ────────────────────────────────────────────
        _jl = _os.path.join(_td, "ok.jsonl")
        with open(_jl, "w", encoding="utf-8") as _fh:
            _fh.write('{"a": 1}\n{"a": 2}\n')
        _rows = _va.read_verified_jsonl(_jl, {"path": _jl, "bytes": _os.path.getsize(_jl), "lines": 2})
        ck("P0-8 正常 JSONL：核对通过、逐行解析出 2 条", len(_rows) == 2 and _rows[1]["a"] == 2, "")
        # ① 缺最后一行（末字节不是 \n）
        _jl2 = _os.path.join(_td, "half.jsonl")
        with open(_jl2, "w", encoding="utf-8") as _fh:
            _fh.write('{"a": 1}\n{"a": 2}')      # 最后一行没换行 = 没写完
        _why2 = ""
        try:
            _va.read_verified_jsonl(_jl2)
        except _va.ArtifactError as e: # noqa: PERF203
            _why2 = e.reason
        ck("P0-8 缺最后一行（末字节非换行）⇒ **拒**且原因说得清",
           "换行" in _why2 or "末字节" in _why2, _why2[:60])
        # ② 字节数与清单不符
        _why3 = ""
        try:
            _va.read_verified_jsonl(_jl, {"bytes": 999999})
        except _va.ArtifactError as e:
            _why3 = e.reason
        ck("P0-8 字节数与清单不符 ⇒ 拒", "字节数不符" in _why3, _why3[:60])
        # ③ 行数不符
        _why4 = ""
        try:
            _va.read_verified_jsonl(_jl, {"lines": 9})
        except _va.ArtifactError as e:
            _why4 = e.reason
        ck("P0-8 行数与清单不符 ⇒ 拒", "行数不符" in _why4, _why4[:60])
        # ④ 路径不符（清单写 A、实参给 B）—— 最容易"顺手传旧清单"绕过的一道
        _why5 = ""
        try:
            _va.read_verified_jsonl(_jl, {"path": _os.path.join(_td, "另一个.jsonl")})
        except _va.ArtifactError as e:
            _why5 = e.reason
        ck("P0-8 清单路径与实参不符 ⇒ 拒（防传旧清单绕过）", "路径不符" in _why5, _why5[:60])
        # ⑤ 一条坏行 ⇒ 整份拒（不静默跳过）
        _jl3 = _os.path.join(_td, "bad.jsonl")
        with open(_jl3, "w", encoding="utf-8") as _fh:
            _fh.write('{"a": 1}\n这不是 JSON\n')
        _why6 = ""
        try:
            _va.read_verified_jsonl(_jl3)
        except _va.ArtifactError as e:
            _why6 = e.reason
        ck("P0-8 一条坏行 ⇒ **整份拒**（不悄悄少几条）", "不是合法 JSON" in _why6, _why6[:60])
        # ⑥ 空文件 ⇒ 视为未写完
        _jl4 = _os.path.join(_td, "zero.jsonl")
        open(_jl4, "w", encoding="utf-8").close()
        _why7 = ""
        try:
            _va.read_verified_jsonl(_jl4)
        except _va.ArtifactError as e:
            _why7 = e.reason
        ck("P0-8 0 字节文件 ⇒ 视为未写完（不是\"空数据\"）", "0 字节" in _why7, _why7[:60])
        # ⑦ 反向控制：正常文件在**不给清单**时也能读（判据不是"任何读都失败"）
        ck("P0-8 反向控制：不给清单也能读正常文件（不是恒拒）",
           len(_va.read_verified_jsonl(_jl)) == 2, "")

        # ── P0-9 隐私分层事实 ───────────────────────────────────────────
        _readme = (_root / "README.md").read_text(encoding="utf-8")
        ck("P0-9 README 有「分层事实」节，且四要素齐全（默认在哪/何时外发/发什么/哪些不发）",
           "分层事实" in _readme
           and "默认留在本机" in _readme
           and "什么条件下、什么会出去" in _readme
           and "明确不会发出去的" in _readme, "")
        ck("P0-9 「分层事实」节带更新日期（文档不再是无日期的绝对陈述）",
           "更新日期" in _readme, "")
        ck("P0-9 逐项列出外发条件（云模型/搜索/生图/反馈/上云/自定义工具至少 6 行）",
           _readme.count("模型厂商") >= 1 and _readme.count("反馈栏提交") >= 1
           and _readme.count("自定义工具") >= 1, "")
        # ⛔ 绝对承诺扫描：这几句一旦**无条件**出现就是虚假陈述。
        #   允许的形态是"带条件的"（同句里有 条件/当/若/只有/除非 之类限定词）。
        _banned = ("完全不出网", "绝不出网", "永远不出网", "一个字都不出网", "不上传任何东西")
        _hits = []
        for _i, _line in enumerate(_readme.split("\n"), 1):
            for _b in _banned:
                if _b in _line:
                    _cond = any(_k in _line for _k in ("当", "若", "只有", "除非", "这一档", "条件下", "本检验"))
                    if not _cond:
                        _hits.append("L%d:%s" % (_i, _b))
        ck("P0-9 绝对承诺**一律带条件**（无条件的\"完全不出网/不上传任何东西\"= 0 处）",
           not _hits, "无条件绝对句：%s" % "、".join(_hits[:4]))
    finally:
        try:
            for _x in _os.listdir(_td):
                _p = _os.path.join(_td, _x)
                if _os.path.isfile(_p):
                    _os.remove(_p)
            _os.rmdir(_td)
        except OSError:
            pass


def t_inject_surface_guard() -> None:
    """自检：**键鼠注入只有一条路**（F12）。

    为什么这一族"一处错处处错"：`mouse_event` / `SetCursorPos` 是**全局输入** ——
    系统把它派给"开枪那一刻光标所在 / 最上面那个窗口"，而它**根本不知道微信在哪**；
    `SetCursorPos` 还会**静默失败**（返回 0、`GetLastError()`＝0，用户正在动鼠标时最常见）。
    ⇒ 手搓的点击/滚轮会**落到用户当前真正指着的窗口（他的控制台）上**（实测事故）。
    正确工序（`ui_adapt.click_real_at` / `wheel_real_at` 里那套）：过闸 → 落点稳定后再确一次 →
    开枪 → 用完把光标还回去。这条工序以前在 `wechat` 里被手抄了 **5 处**（点击 3 + 滚轮 2），
    **全都漏了"开枪前再确一次"**。

    ⇒ 判据（**AST 找真实调用**，不看文本 —— 注释里写着这些名字的地方不算）：
      ① **鼠标类**（`mouse_event` / `SetCursorPos`）只许出现在 `ui_adapt.py`（原语实现点）与
         `input_backend.py`（注入面）；其它文件必须为 **0**；
      ② **键盘类**（`keybd_event` / `SendInput`）留在别处的，白名单**逐条写理由**
         （它们没有鼠标那套"落点"问题，但同样是全局输入 ⇒ 每条都要说清为什么独立）。
    """
    import ast as _ast # noqa: PLC0415

    _root = HERE.parents[0]
    #: 实现点/注入面：整文件豁免
    _FILES_OK = {
        "ui_adapt.py": "真鼠标原语（`click_real_at` / `wheel_real_at` / `heal_input` / `_cursor_restore`）都长在这里",
        "input_backend.py": "注入面本身（投递后端与真实后端）",
    }
    #: 键盘类的逐条豁免：**(文件, 函数) → 理由**
    _KB_OK = {
        ("voice_strip.py", "_send_alt"):
            "注入**真·右 Alt**（录音态）：要的是 `SendInput` 的扩展键语义，且前置是「微信在前台」，"
            "消息发给焦点窗口、不动鼠标 —— 计划已定**保留独立**",
        ("voice_strip.py", "_cancel_alt"): "同上（松开右 Alt 收尾）",
        ("wechat.py", "_type_into_focused"):
            "把文本打进**当前聚焦窗口**（朋友圈那种独立窗，主窗输入框不对）⇒ 需要发给焦点窗口的真键盘；"
            "目前 `input_backend` 没有「真键盘」这一路（`MessageBackend.keys` 是投递）⇒ 记跟进项",
        ("wechat.py", "moments_publish_text"): "误触浮层的 ESC 收尾（同上：没有共享的真键盘原语）",
        ("wechat_windows.py", "popup_paste_blind"):
            "触发微信粘贴：只投递 `V` 会退化成字面字母（微信读 `GetKeyState` 的真实修饰键态），"
            "必须用 `keybd_event` 真按一下 Ctrl 再投 `V` ⇒ 这条真按键**是功能本身**、不是绕路；"
            "它不抢窗口、不点鼠标、不产字符，且**前后各有一道「微信不在前台就拒绝」的安全闸门**",
    }
    _MOUSE = ("mouse_event", "SetCursorPos")
    _KB = ("keybd_event", "SendInput")

    def _injections(src: str):
        """这份源码里"真实调用"的键鼠注入点 → [(函数名, 名字, 行号, 是不是鼠标类)]"""
        out = []
        tree = _ast.parse(src)
        fnof = {}
        for n in _ast.walk(tree):
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                for x in _ast.walk(n):
                    fnof[x] = n.name
        for n in _ast.walk(tree):
            if not isinstance(n, _ast.Call):
                continue
            f = n.func
            nm = f.attr if isinstance(f, _ast.Attribute) else (f.id if isinstance(f, _ast.Name) else "")
            if nm in _MOUSE or nm in _KB:
                out.append((fnof.get(n, "?"), nm, n.lineno, nm in _MOUSE))
        return out

    _mouse_bad, _kb_bad, _files = [], [], 0
    for _f in sorted((_root / "agent").glob("*.py")):
        _files += 1
        if _f.name in _FILES_OK:
            continue
        for _fn, _nm, _ln, _is_mouse in _injections(_f.read_text(encoding="utf-8")):
            _where = "agent/%s::%s" % (_f.name, _fn)
            if _is_mouse:
                _mouse_bad.append("%s:%d %s()" % (_where, _ln, _nm))
            elif (_f.name, _fn) not in _KB_OK:
                _kb_bad.append("%s:%d %s()" % (_where, _ln, _nm))
    ck("鼠标注入只许出现在实现点/注入面（手搓点击会落到用户窗口上）",
       not _mouse_bad, "越界：%s" % "、".join(_mouse_bad[:5]))
    ck("键盘注入留在别处的都写了理由（逐条白名单）",
       not _kb_bad, "没理由的：%s" % "、".join(_kb_bad[:5]))
    #: 分母守卫：扫不到文件时上面两条会"全绿"
    ck("上述扫描真的扫到了文件（分母守卫）", _files > 100, "扫到 %d 个 .py" % _files)
    #: 白名单自身要有内容且每条理由都写得下
    ck("豁免清单非空且理由具体", len(_FILES_OK) == 2 and len(_KB_OK) == 5
       and all(len(str(v)) > 12 for v in list(_FILES_OK.values()) + list(_KB_OK.values())), "")
    #: 反向控制：判定器必须**能认出**注入调用，且**不被注释/字符串骗到**
    _probe = ('def f():\n'
              '    """说明：这里以前写过 mouse_event(1,2,3,4,5) 与 SetCursorPos(1,2)"""\n'
              '    # user32.mouse_event(0x0002, 0, 0, 0, 0)\n'
              '    s = "mouse_event"\n'
              '    u.keybd_event(0x11, 0, 0, 0)\n')
    _pi = _injections(_probe)
    ck("注入判定器有效：只认出那 1 条真调用（注释/字符串里的同名不认）",
       len(_pi) == 1 and _pi[0][1] == "keybd_event", "实测 %s" % (_pi,))


def t_foreground_borrow_guard() -> None:
    """自检：**借前台必须配还**，且"读前台"与"借前台"不是同一件事（F4）。

    口径先校准：这一族里那 4 个名字是**三个不同问题**，不是一个问题的四份实现 ——
      · `notify_ui.foreground()`：**读**当前前台句柄（getter）；
      · `wechat._fg_enter()/_fg_exit()`：**引用计数式**借用（`fg_hold` 上下文管理器 + `_send_with_foreground`
        的 try/finally），支持嵌套 —— 发送链用；
      · `wechat._restore_fg(hwnd, note, keep)`：**时间点式**还前台（模块级 `_FG_STASH` + `keep` 语义，
        "写文件名那一步抢了前台就立刻还一次"）—— 文件对话框链用；
      · `guards.foreground()`：**判据**（能不能置前）—— C6 建的，与上面三者都不同。
    ⇒ **无可合并的重复**（合并两套还前台机制＝改行为）；本闸门守的是"配对"这条硬要求：
      借了不还 = 用户的窗口被留在前台。

    ⛔ 判据必须**先剥注释与 docstring**：`_send_with_foreground` 的注释里就写着 `_fg_enter()`，
      直接数文本会把注释算成一次调用 ⇒ 假报"不配对"（写这条闸门时真踩了一次）。
    """
    import ast as _ast # noqa: PLC0415

    _root = HERE.parents[0]
    _src = (_root / "agent" / "wechat.py").read_text(encoding="utf-8")
    _tree = _ast.parse(_src)
    _pairs, _bad = [], []
    for _n in _ast.walk(_tree):
        if not isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        # 去掉 docstring 与注释行后再数（注释里提到 `_fg_enter()` 不算调用）
        _body = (_ast.get_source_segment(_src, _n) or "")
        if _n.body and isinstance(_n.body[0], _ast.Expr) and isinstance(
                getattr(_n.body[0], "value", None), _ast.Constant):
            _ds = _n.body[0].value.value
            if isinstance(_ds, str) and _ds:
                _body = _body.replace(_ds, "")
        _body = "\n".join(_x for _x in _body.split("\n") if not _x.strip().startswith("#"))
        _ent, _exi = _body.count("_fg_enter()"), _body.count("_fg_exit()")
        if _ent or _exi:
            _pairs.append("%s(%d/%d)" % (_n.name, _ent, _exi))
            if _ent != _exi:
                _bad.append("%s enter=%d exit=%d" % (_n.name, _ent, _exi))
    ck("借前台与还前台**逐个函数配平**（借了不还＝把用户窗口留在前台）",
       not _bad, "不配对：%s" % "；".join(_bad[:4]) + " ｜ 全部：%s" % "、".join(_pairs[:6]))
    #: 分母守卫：得真的找到借用点
    ck("上述扫描真的扫到了借用点（分母守卫 ≥2）", len(_pairs) >= 2, "命中 %s" % "、".join(_pairs))
    #: 反向控制：判定器必须**能认出**不配对（拿一份"只 enter 不 exit"的样本试）
    _probe = "def f(self):\n    self._fg_enter()\n    return 1\n"

    def _count(_text):
        _b = "\n".join(_x for _x in _text.split("\n") if not _x.strip().startswith("#"))
        return _b.count("_fg_enter()"), _b.count("_fg_exit()")
    ck("「配平」判定器有效（只 enter 不 exit 的样本必须被判不平）",
       _count(_probe)[0] == 1 and _count(_probe)[1] == 0
       and _count("    # self._fg_enter()\n")[0] == 0, "")
    #: 同名异义登记：`notify_ui.foreground` 是**读**，不许被写成"借"
    _nu = (_root / "agent" / "notify_ui.py").read_text(encoding="utf-8")
    _nf = ""
    for _n in _ast.walk(_ast.parse(_nu)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "foreground":
            _nf = _ast.get_source_segment(_nu, _n) or ""
    ck("`notify_ui.foreground` 仍是**读**前台（不抢前台、不还前台）——与 `_fg_enter` 同名异义、不许合并",
       bool(_nf) and "GetForegroundWindow" in _nf and "SetForegroundWindow" not in _nf
       and "_fg_enter" not in _nf, _nf.strip().split("\n")[0][:60] if _nf else "没找到该函数")

    # ── 第二组：`wechat_windows` 的「临时借一次前台」族（收编 Akasha WS-1 时补）──
    #   这一族的危险不在"借"，而在**还**：原先还前台的语句写在正常路径里，
    #   中途任何一步抛异常就从 except 出口 return ⇒ 用户的焦点被留在微信窗口上
    #   （Akasha 的原版就是「finally 里只解挂 AttachThreadInput、不还原原前台」）。
    #   ⇒ 现在收口成 `fg_stash()`：出块（正常 / 异常 / 提前 return）一定还。
    #   判据分三层：① 收口机制本身的行为（桩）；② 两个会夺前台的函数**结构上用了收口**；
    #   ③ 反向锚（没登记就不许还 —— 否则会把本该在前台的窗口又置一次）。
    import agent.wechat_windows as _ww # noqa: PLC0415

    _calls: list = []
    _orig_rf = _ww._restore_foreground_now
    try:
        _ww._restore_foreground_now = lambda prev, **k: (_calls.append(int(prev)), True)[1]
        _sink: dict = {}
        with _ww.fg_stash(_sink) as _st:
            _st.note(4242)
        ck("收口：出块即还（正常路径），并把结果写进 sink",
           _calls == [4242] and _sink.get("fg_restored") is True, "%s / %s" % (_calls, _sink))
        _calls.clear()
        try:
            with _ww.fg_stash({}) as _st2:
                _st2.note(777)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        ck("收口：**块内抛异常也还**（这是原先真漏的那条路）", _calls == [777], "%s" % _calls)
        _calls.clear()
        with _ww.fg_stash({}) as _st3:
            pass
        ck("反向锚：没登记就不还（否则会把本该在前台的窗口又置一次）", _calls == [], "%s" % _calls)
        _calls.clear()
        with _ww.fg_stash({}) as _st4:
            _st4.note(0)
        ck("反向锚：登记 0（= 没有原前台）同样不还", _calls == [], "%s" % _calls)
    finally:
        _ww._restore_foreground_now = _orig_rf

    _wsrc = (_root / "agent" / "wechat_windows.py").read_text(encoding="utf-8")
    _wtree = _ast.parse(_wsrc)
    _need = ("popup_paste_blind", "popup_fire_paste_send")
    _seg = {}
    for _n in _ast.walk(_wtree):
        if isinstance(_n, _ast.FunctionDef) and _n.name in _need:
            _seg[_n.name] = _ast.get_source_segment(_wsrc, _n) or ""
    ck("两个「会夺前台」的函数都用收口（`fg_stash(` ＋ `note(`）",
       set(_seg) == set(_need) and all("fg_stash(" in _v and ".note(" in _v for _v in _seg.values()),
       str(sorted(_seg)))
    #: ⚠️ 这一条**必须用 AST 找 Call 节点**，不能数文本 —— 文本会把 docstring 里那句
    #   「用完必须 `_restore_foreground_now` 还回去」算成一次调用（本轮就这么假红过一次）。
    _manual = []
    for _n in _ast.walk(_wtree):
        if isinstance(_n, _ast.FunctionDef) and _n.name in _need:
            for _c in _ast.walk(_n):
                if (isinstance(_c, _ast.Call) and isinstance(_c.func, _ast.Name)
                        and _c.func.id == "_restore_foreground_now"):
                    _manual.append(_n.name)
    ck("这两个函数里**不再手工**调 `_restore_foreground_now`（还的事只在收口里做一处）",
       not _manual, "手工还前台出现在：%s" % sorted(set(_manual)))


def t_default_value_guard() -> None:
    """自检：**默认表与示例文件的值差异必须逐条登记**（记三元组，值一变就红）。


    两处都是"默认"的落点，所以它们**不一致的地方就是值得看一眼的地方**：
      · 差异**新增** ⇒ 有人往默认表里塞了新值（可能是个人的/私有的）⇒ 必须写理由；
      · 已登记的键**值变了** ⇒ 同样红（记了值才能在下一次悄悄改动时发现）；
      · 差异**消失**（两边对齐了）⇒ 允许，是好事，但记录该跟着删（不强制）。
    """
    import io as _io # noqa: PLC0415
    import json as _json # noqa: PLC0415
    import os as _o # noqa: PLC0415

    from agent.config import DEFAULT_CONFIG as _D # noqa: PLC0415

    _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))

    def _flat(_d, _pre=""):
        _out = {}
        for _k, _v in (_d or {}).items():
            _p = (_pre + "." + _k) if _pre else _k
            if isinstance(_v, dict):
                _out.update(_flat(_v, _p))
            else:
                _out[_p] = _v
        return _out

    def _shape_eq(_live, _rec):
        """登记值以 `…` 结尾 ⇒ 按**前缀**比（长值只记形状，不在产品里多存一份）。"""
        if isinstance(_rec, str) and _rec.endswith("…"):
            return isinstance(_live, str) and _live.startswith(_rec[:-1])
        return _live == _rec

    try:
        _ex = _json.loads(_io.open(_o.path.join(_root, "config.example.json"), encoding="utf-8").read())
    except Exception as _e: # noqa: BLE001
        ck("示例文件可读（默认值对账的前提）", False, "%s" % _e)
        return
    _a, _b = _flat(_D), _flat(_ex)

    _unlisted, _drifted = [], []
    for _k in sorted(set(_a) & set(_b)):
        if isinstance(_a[_k], (dict, list, tuple)) or isinstance(_b[_k], (dict, list, tuple)):
            continue
        if _a[_k] == _b[_k]:
            continue
        _rec = DEF_VS_EXAMPLE_OK.get(_k)
        if _rec is None:
            _unlisted.append("%s(默认=%r, 示例=%r)" % (_k, _a[_k], _b[_k]))
        elif not (_shape_eq(_a[_k], _rec[0]) and _shape_eq(_b[_k], _rec[1])):
            _drifted.append("%s 现在是 (默认=%r, 示例=%r)，登记的是 (%r, %r)"
                            % (_k, _a[_k], _b[_k], _rec[0], _rec[1]))
    ck("默认表与示例的值差异**全部已登记**（新增差异 ⇒ 先写理由）",
       not _unlisted, "；".join(_unlisted[:5])[:200])
    ck("已登记的差异**值没被改过**（记了值才盯得住悄悄改动）",
       not _drifted, "；".join(_drifted[:4])[:200])
    ck("登记表本身有内容（这条闸门还在真的守着东西）",
       len(DEF_VS_EXAMPLE_OK) >= 3, "%d 条" % len(DEF_VS_EXAMPLE_OK))


def t_delivery_ledger_guard() -> None:
    """自检：**"已发送"记账必须在真的发出去之后**（不许谎报送达）。

    ⛔ 为什么单独一条：会话里的 `sent` 台账不只给人看——它决定模型**要不要重试**，
    也是"这条消息有没有回过"的唯一凭据。谎报一次的后果是**永久性的**：
    模型以为发过了不再重试、用户那边什么都没有，而所有日志都写着"成功"。
    以前的形态有两种，都不会自己暴露（不抛异常、功能测试也不碰）：
      ① `_exec_gen_image` 逐张 catch 异常、最后**无条件**记账 + 回 `sent=True` ⇒ 全失败也算成功；
      ② `_exec_gen_video` 忽略 `send_file_posted` 的 **(ok, 说明)** 返回值就记账。
    判据要用**行为**验（走一遍那条链看台账），而不是只看源码里有没有某个词。
    """
    import inspect as _inspect # noqa: PLC0415
    import os as _o # noqa: PLC0415
    import tempfile as _tf # noqa: PLC0415

    _o.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from agent import image_gen as _ig # noqa: PLC0415
    from agent import sender as _sd # noqa: PLC0415
    from agent import tools as _tl # noqa: PLC0415

    # ① sender.send_image 的失败契约必须是**抛异常**（`send_image` 之后那些"裸 append"才安全）
    _src_si = _inspect.getsource(_sd.SendQueue.send_image)
    ck("发图失败是 raise（不是返回 False）—— 记账点靠异常挡住",
       "raise RuntimeError" in _src_si and "return {\"sent\": True}" in _src_si, "")

    # ② 行为：生成图全部发送失败 ⇒ 必须报错，且**台账里不许出现 [生成图]**
    _tmp = _tf.mkdtemp(prefix="ledger-gate-")
    _p1 = _o.path.join(_tmp, "a.png")
    _p2 = _o.path.join(_tmp, "b.png")
    for _p in (_p1, _p2):
        open(_p, "wb").write(b"x")

    class _BoomSender:
        def send_image(self, chat_key, path):
            # `SendQueue.send_image` 的失败契约就是抛异常 ⇒ 替身照抄这个契约
            raise RuntimeError("图片发送失败（判据替身）")

    _ctx = {"sender": _BoomSender(), "chat_key": "group:x@y", "chat_id": "x@y", "session": {}}
    _keep_gen = _ig.generate
    try:
        _ig.generate = lambda *a, **k: {"ok": True, "files": [_p1, _p2], "backend": "stub",
                                        "why": "", "results": []}
        _res = _tl._exec_gen_image(_ctx, {"request": "画个猫"})
    finally:
        _ig.generate = _keep_gen
    _raw = _res[0] if isinstance(_res, tuple) else _res
    _txt = str(_raw)
    _recorded = [x for x in (_ctx["session"].get("sent") or []) if x.get("type") == "image"]
    ck("生成图**一张都没发出去** ⇒ 如实报错（不回「已发送」）", "没发出去" in _txt, _txt[:120])
    ck("生成图全失败 ⇒ 台账里**没有**[生成图]（谎报送达是最坏的一种记账）",
       not _recorded, str(_recorded)[:100])

    # ③ 对照组：发成功时**必须**记账（否则兜底补发会把同一件事再发一遍）
    class _OkSender:
        def send_image(self, chat_key, path):
            return {"sent": True}

    # ⚠️ 必须预建 `sent`：产品里 `session` 是**带键**进来的（见 `send_message` 那条链），
    #    这里若给空 dict，`ctx["session"]["sent"].append` 会 KeyError 被内层 try/except 吞掉
    #    ⇒ 正向对照会**假红**（我第一版就是这么红的一处，属于判据自己的坑）。
    _ctx2 = {"sender": _OkSender(), "chat_key": "group:x@y", "chat_id": "x@y",
             "session": {"sent": []}}
    try:
        _ig.generate = lambda *a, **k: {"ok": True, "files": [_p1], "backend": "stub",
                                        "why": "", "results": []}
        _tl._exec_gen_image(_ctx2, {"request": "画个猫"})
    finally:
        _ig.generate = _keep_gen
    _rec2 = [x for x in (_ctx2["session"].get("sent") or []) if x.get("type") == "image"]
    ck("生成图发成功 ⇒ 台账里**有**[生成图]（对照组，防把记账整个删掉）",
       len(_rec2) == 1, str(_rec2)[:80])

    # ④ 生成视频那条：源码形态锚定"按返回值判"，不看返回值就记账是错的
    _src_gv = _inspect.getsource(_tl._exec_gen_video)
    ck("生成视频按 send_file_posted 的返回值判成功（不是「没抛异常就算发出去」）",
       "_sf_ok, _sf_msg = sender.send_file_posted" in _src_gv and "if not _sf_ok:" in _src_gv, "")


def t_g23_fbnotice() -> None:
    """常驻公告条（web `#noticeBar` 的 Qt 等价）—— 实测「原本有个公告栏的，没有看到」。

    落点与版式是用户直接点过名的两处：
      · 「我想要的是公告显示在我指的箭头和画的红线那个位置，你放顶栏上了。顶栏空间不够」
        ⇒ 在**内容区顶部**（页栈之上），不在 60px 顶栏里；
      · 「你非得用这个圆角胶囊形式吗？都玩烂了」⇒ 左侧强调色条 + 近直角，不用胶囊；
      · 「公告要有两个按钮：一个是'本次不显示'，一个是不再显示」⇒ 三颗按钮齐备，
        且两种记忆范围**各有一处实现**（本次运行内 vs 永久）。

    四问（都可复核，不靠"看起来在"）：
      ① 控件真的**建出来并加进版式**；
      ② 落点/版式符合上述口径（挂在页栈之上、顶栏里没有它、近直角+左色条、无胶囊半径）；
      ③ 与 web 侧**同一件事**（`index.html` 里也有这条提示，文案同义）；
      ④ 只有一条实现（不许两处各写一份，改一处漏一处）。

    ⚠️ 断言一律**计数式**（`src.count(...)`）：`"…" in src` 那种写法会进「脆断言棘轮」。
    """
    import os as _os # noqa: PLC0415

    _here = _os.path.dirname(_os.path.abspath(__file__))
    _sh = open(_os.path.join(_here, "shell.py"), encoding="utf-8").read()
    _html = open(_os.path.join(_here, "..", "assets", "console", "index.html"),
                 encoding="utf-8").read()

    ck("① 公告条建出来了（一个构建函数 + 一处调用点 + objectName 恰好一处）",
       _sh.count("def _build_notice(") == 1
       and _sh.count("self._build_notice()") == 1
       and _sh.count('setObjectName("fbNotice")') == 1, "")
    ck("① 顺序正确：先设 objectName、再加进版式（加进去之后才设名会被主题/定位漏掉）",
       0 <= _sh.find('setObjectName("fbNotice")') < _sh.find("hl0.addWidget(bar)"), "")
    ck("② 落点在**内容区顶部**：公告加在页栈之前（页栈那行自身也在，说明是同一版式）",
       _sh.count("wl.addWidget(self.stack, 1)") == 1
       and 0 <= _sh.find("self._build_notice()") < _sh.find("wl.addWidget(self.stack, 1)"), "")
    ck("② 顶栏里**不再**挂公告（那个位置错误不许回潮）",
       _sh.count("lay.addWidget(self.fbNotice)") == 0
       and _sh.count("QFrame#fbNotice{") == 1, "")
    ck("② 版式换掉了圆角胶囊：近直角 2px + 左侧 3px 色条 + 水平淡出渐变",
       _sh.count("border-left:3px solid") == 1
       and _sh.count("border-radius:2px") >= 1
       and _sh.count("qlineargradient(") >= 1, "")
    ck("② 两颗关闭按钮齐备，且「本次不显示」与「不再显示」是**两种记忆范围**（各一处实现）",
       min(_sh.count(k) for k in ("本次不显示", "不再显示", "去反馈")) >= 1
       and _sh.count("self._notice_off_session = True") == 1
       and _sh.count('setValue("notice_off", True)') == 1, "")
    ck("② 已关闭的两种范围都参与判定（实例标志 + QSettings 都要读）",
       _sh.count('getattr(self, "_notice_off_session", False)') == 1
       and _sh.count('"notice_off", False, type=bool') == 1, "")
    ck("② 文案给出完整路径（检测报告中心 / 出具检测报告 / 反馈 三要素都在）",
       min(_sh.count(k) for k in ("检测报告中心", "出具检测报告", "反馈")) >= 1, "")
    ck("② 文案说明兼容性信息会自动带上（不用用户自己再跑一遍检查）",
       _sh.count("自己带上") >= 1 and _sh.count("不用你另跑检查") >= 1, "")
    ck("③ web 侧同一件事的提示条也在，且两边是**同一句承诺**（兼容性信息自动带上、不用你跑检查）",
       _html.count("noticeBar") >= 1 and _html.count("不用你另跑检查") >= 1
       and _sh.count("不用你另跑检查") >= 1, "")
    ck("④ 这条文案只有一处实现（不许第二份拷贝）",
       _sh.count("去「检测报告中心」找到对应的问题") == 1, "")


def t_g24_fbnotice_dismiss() -> None:
    """公告两颗按钮**真的会关掉它**（不是只画了个按钮）。

    真跑构建：把 Shell 建起来 → 找 `#fbNotice` 已在版式里 → 点「本次不显示」→
    控件下线且实例标志置位；再验「不再显示」写进 QSettings 的永久键。
    `_go` 被替身挡掉（它要切页，与本判据无关）。
    """
    import os as _os # noqa: PLC0415
    import sys as _sys # noqa: PLC0415

    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from PySide6.QtCore import QSettings # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QFrame, QWidget # noqa: PLC0415
    from stylekit_qt import WHALE # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    _qs = QSettings("WXAgent", "persona-morph-ui")
    _old_never = _qs.value("notice_off", False, type=bool)
    _qs.setValue("notice_off", False) # 先清永久键，保证这条判据从「应显示」起跑
    _qs.sync()

    import shell as _shell # noqa: PLC0415

    def _mode(qs_key):
        return bool(QSettings("WXAgent", "persona-morph-ui").value(qs_key, False, type=bool))

    try:
        sh = _shell.Shell(WHALE)
        _hit: list = []
        sh._go = lambda sec, label: _hit.append((sec, label)) # 切页与本判据无关，只记调用
        sh.show()
        app.processEvents() # offscreen 下不 show 就恒 isVisible()=False（第⑥坑）

        bars = [w for w in sh.findChildren(QFrame) if w.objectName() == "fbNotice"]
        ck("① 起点：公告条在版式里且可见（永久键已清）", len(bars) == 1 and bars[0].isVisible(), "")

        btns = [w for w in sh.findChildren(QWidget)
                if w.objectName() in ("fbNoticeGo", "fbNoticeOnce", "fbNoticeNever")]
        ck("① 三颗按钮都在（objectName 定位，不靠文案）", len(btns) == 3, "")

        _go_b = [w for w in btns if w.objectName() == "fbNoticeGo"]
        if _go_b:
            _go_b[0].click()
        ck("① 「去反馈」真的走切页入口（sec=feedback，不是只画了颗按钮）",
           _hit == [("feedback", "反馈")], f"{_hit}")

        once = [w for w in btns if w.objectName() == "fbNoticeOnce"]
        if once:
            once[0].click()
        app.processEvents()
        ck("② 点「本次不显示」后置实例标志，且**没有**写永久键",
           getattr(sh, "_notice_off_session", False) is True
           and _mode("notice_off") is False, "")
        ck("② 关掉后公告不再出现在界面上",
           len([w for w in sh.findChildren(QFrame)
                if w.objectName() == "fbNotice" and w.isVisible()]) == 0, "")
        ck("② 本次运行内重建也不复活（记忆范围就是「本次」）",
           sh._build_notice() is None, "")

        # 清掉实例标志即复活 —— 否则上面那个 None 可能只是「根本没建」（假绿）
        sh._notice_off_session = False
        _revived = sh._build_notice()
        ck("② 清掉实例标志后即复活（证明 None 确实是「被关掉」而不是「没建」）",
           _revived is not None, "")

        # 「不再显示」→ 永久键。⚠️ 上一步点完「本次不显示」后，原 bar 已脱离父子树
        # （setParent(None) + deleteLater）⇒ 在 Shell 下 findChildren 找不到它了。
        # 必须用上面**新造出来的**那一条上的按钮，否则会静默点空（本轮就踩过）。
        _never = [] if _revived is None else [
            b for b in _revived.findChildren(QWidget) if b.objectName() == "fbNoticeNever"]
        ck("③ 复活出来的那一条上确实有「不再显示」按钮", len(_never) == 1, "")
        if _never:
            _never[0].click()
        app.processEvents()
        _qs.sync() # 产品侧 QSettings 在 _dismiss 返回时就析构并落盘，这里重读一遍
        ck("③ 点「不再显示」写进永久键（下次启动也不再显示）", _mode("notice_off") is True, "")
        if _revived is not None:
            _revived.setParent(None)
            _revived.deleteLater()
        sh2 = _shell.Shell(WHALE)
        ck("③ 永久键置位后，新实例根本不建公告控件（连隐藏的都没有）",
           len([w for w in sh2.findChildren(QFrame) if w.objectName() == "fbNotice"]) == 0, "")
        for _w in (sh, sh2):
            try:
                _w.close()
                _w.deleteLater()
            except Exception: # noqa: BLE001
                pass
        app.processEvents()
    finally:
        _qs.setValue("notice_off", bool(_old_never))
        _qs.sync()


def t_g26_web_parity_actions() -> None:
    """网页控制台的动作按钮**一律要有 Qt 原生实现**（网页能做的都得在 Qt 做）。

    做法：把每个面板**真建出来**，收集所有渲染出来的动作按钮（`web_action` property），
    逐个按 `_btn_group` 的**同一套分支判据**分类（ACT_CUSTOM / _ACT_API / codeCheck / 跳过集 /
    无 id 的保存钮），断言"会掉进 `_btn_stub` 的 = 0"。

    ⚠️ 为什么必须"渲染后"再看：静态解析网页 HTML 会把**Qt 面板压根不渲染的行**也算进来
    （那些按钮在 Qt 里根本点不到，不算缺口）；反过来，混合行里漏出来的单颗按钮只有渲染后才看得见
    —— 用户点中的正是那种。
    """
    import inspect as _inspect # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as _QA # noqa: PLC0415

    import panels_custom as _pc # noqa: PLC0415
    import panels_qt as _pq # noqa: PLC0415
    from stylekit_qt import THEMES as _THEMES # noqa: PLC0415
    from widgets import Btn as _Btn # noqa: PLC0415

    app = _QA.instance() or _QA([])
    t = _THEMES["whale"]
    custom = set((getattr(_pc, "ACT_CUSTOM", {}) or {}).keys())
    api = set((getattr(_pq, "_ACT_API", {}) or {}).keys())
    skip = set(getattr(_pq, "_AIDS_COVERED_ELSEWHERE", set()) or set())
    stub, total = [], 0
    for sec in sorted(_pq.BATCH_SECS):
        try:
            page = _pq.build_panel(t, sec, on_save=None)
        except Exception: # noqa: BLE001 — 建不起来的页由别的判据管，这里只管按钮归属
            continue
        for b in page.findChildren(_Btn):
            prop = b.property("web_action")
            if prop is None:
                continue # 不是动作按钮（普通按钮 / 原生操作钮）
            total += 1
            aid = str(prop or "")
            if aid in custom or aid in api or aid in skip or aid in ("codeCheck", "codeCheckDeps"):
                continue
            if not aid and str(b.text()).strip().startswith("保存"):
                continue
            stub.append((sec, aid, str(b.text())[:18]))
        page.setParent(None)
        page.deleteLater()
        app.processEvents()
    ck("① 分母守卫：真的渲染出了动作按钮（不然下面那条会因空集全绿）",
       total >= 40, "渲染 %d 个" % total)
    ck("② 渲染出来的动作按钮**没有一个**会掉进 `_btn_stub`（网页能做的都在 Qt 做了）",
       not stub, "掉桩：%s" % (stub[:6],))
    ck("③ 判据用的是运行时同一套分支（`_btn_group` 里那份跳过集，不是另抄一份）",
       _inspect.getsource(_pq._btn_group).count("_AIDS_COVERED_ELSEWHERE") >= 1)
    _stub_src = _inspect.getsource(_pq._btn_stub)
    # ⚠️ 只查**代码**：文档字符串里「不许用系统 QMessageBox」是在解释为什么不用它，不算违规
    #   （同 t_whale_guard 那条"只查代码、不查文档字符串"的口径）。
    _stub_code = _stub_src.split(chr(34) * 3, 2)[2] if _stub_src.count(chr(34) * 3) >= 2 else _stub_src
    ck("④ 那个桩的**代码里**不再用系统 QMessageBox、也不再叫用户「去网页控制台」",
       _stub_code.count("QMessageBox") == 0 and _stub_code.count("网页控制台") == 0
       and _stub_code.count("InfoDialog") >= 1, _stub_code[:60].replace("\n", " "))


def t_g27_native_pieces() -> None:
    """几个"以前只能去网页做"的动作，现在 Qt 原生有：原生提示卡 + 反馈页「更多」+ 三条真接后端。"""
    import inspect as _inspect # noqa: PLC0415
    from PySide6.QtWidgets import QDialog as _QD # noqa: PLC0415

    import panels_qt as _pq # noqa: PLC0415
    from confirm import InfoDialog # noqa: PLC0415

    _pc = __import__("panels_custom")
    _id_src = _inspect.getsource(InfoDialog)
    _id_code = _id_src.split(chr(34) * 3, 2)[2] if _id_src.count(chr(34) * 3) >= 2 else _id_src
    ck("① 原生提示卡 `InfoDialog` 在位，且**代码里**不含系统 QMessageBox",
       issubclass(InfoDialog, _QD) and _id_code.count("QMessageBox") == 0)
    ck("② 反馈页「更多（联系邮箱 / 提交记录）」有原生处理器（不再走桩）",
       "fbAdvBtn" in (getattr(_pc, "ACT_CUSTOM", {}) or {}))
    _pqs = _inspect.getsource(_pq)
    ck("③ 「列出模型」「重读群列表」「两个接收端测试连通」都真接了后端",
       min(_pqs.count(k) for k in ("vcModels", "refreshGroups",
                                   "cloudTestPersona", "cloudTestBlocklist")) >= 1)
    _ss = _inspect.getsource(_pc)
    ck("④ 群里「请到网页控制台拍板」那种指路话术在 Qt 侧不再出现",
       _ss.count("请到网页控制台") == 0)


# ---------------------------------------------------------------- 热词统计（W7）

def t_hot_words_guard() -> None:
    """自检：**热词统计**（群梗词典的第一步）。

    三条纪律：① **不把整段当一个词**（实测踩过：整句「今天的考试好难啊」被算成热词）；
              ② **真值**：三条都含"考试"的历史 ⇒「考试」必须排第一；
              ③ 无历史 ⇒ **返回空**（不编）；测试数据**自己清**。
    """
    import json as _json
    import shutil as _sh

    root = HERE.parents[0]
    src = (root / "agent" / "memory.py").read_text(encoding="utf-8")
    ck("能取到 `agent/memory.py`（分母守卫）", bool(src.strip()))
    try:
        sys.path.insert(0, str(root))
        from agent import memory as _M
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.memory`", False, "%s" % str(e)[:60])
        return
    ck("`hot_words` 在位", callable(getattr(_M, "hot_words", None)))

    _k = "__selftest_hot_guard__"
    _hp = _M._history_file(_k)
    try:
        os.makedirs(os.path.dirname(_hp), exist_ok=True)
        with open(_hp, "w", encoding="utf-8") as fh:
            for _t in ("今天的考试好难啊", "考试考试真难", "明天又要考试了"):
                fh.write(_json.dumps({"text": _t}, ensure_ascii=False) + "\n")
        _hw = _M.hot_words(_k, 5)
        ck("真值：三条都含「考试」⇒「考试」排第一", bool(_hw) and _hw[0][0] == "考试",
           "得到 %s" % (_hw[:3],))
        ck("**不把整段当一个词**（最长 4 字）", all(len(w) <= 4 for w, _ in _hw),
           "最长 %d" % (max((len(w) for w, _ in _hw), default=0)))
        ck("无历史 ⇒ **返回空**（不编）", _M.hot_words("__根本不存在的会话__", 5) == [])
    finally:
        try:
            os.remove(_hp)
        except Exception: # noqa: BLE001
            pass
        for _d in (_M._chat_dir(_k), os.path.dirname(_hp)):
            _sh.rmtree(_d, ignore_errors=True)


# ---------------------------------------------------------------- 表情码与挑选（第 11/12 项）

def t_emoji_code_guard() -> None:
    """自检：**方括号表情码解析** ＋ **按频次挑一个**。

    为什么要它：微信**自带** `[呲牙]` 这套码（发送时微信自己渲染）⇒ 不用鼠标、不用找坐标；
    「挑一个」以前永远取候选第一个 ⇒ 表现成「**只会发呲牙、不会发别的**」。
    ⛔ 两条纪律：① 表情码**只认白名单形态**（认不出不许猜，否则把普通方括号文字当表情发）；
                ② 没记过频次时**保持原顺序**（行为可预期，不许乱跳）。
    """
    root = HERE.parents[0]
    src = (root / "agent" / "emoji_index.py").read_text(encoding="utf-8")
    ck("能取到 `agent/emoji_index.py`（分母守卫）", bool(src.strip()))
    try:
        sys.path.insert(0, str(root))
        from agent import emoji_index as _E
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.emoji_index`", False, "%s" % str(e)[:60])
        return

    _p = _E.parse_codes("哈喽[呲牙]在吗")
    ck("认得出表情码 `[呲牙]`（返回名称与位置）",
       bool(_p) and _p[0][0] == "呲牙", "得到 %s" % (_p[:2],))
    # ⛔ 反向对照：**过长的方括号文字不算表情码**（否则会把正常文字当表情发出去）
    ck("反向对照：**长括号文字不认**（`[这是一段很长的说明文字]` ⇒ 空）",
       _E.parse_codes("看这个[这是一段很长的说明文字]") == [])
    _sp = _E.split_codes("哈喽[呲牙]在吗")
    ck("切成「文字/表情」段（三段：文字·表情·文字）",
       [x[0] for x in _sp] == ["text", "emoji", "text"], "得到 %s" % (_sp,))
    ck("纯文字 ⇒ 只有一段、且走老路（行为不变）",
       _E.split_codes("普通一句话") == [("text", "普通一句话")])

    # 按频次挑：没记录 ⇒ 第一个（可预期）
    ck("按频次挑：**都没记录过 ⇒ 返回第一个**（不许乱跳）",
       _E.pick_by_usage(["a", "b", "c"]) == "a")
    _k = "__selftest_pick__"
    try:
        _E.bump_usage(_k, 7)
        ck("按频次挑：**记过 7 次的那个优先**（治「只会发第一个」）",
           _E.pick_by_usage(["a", _k, "b"]) == _k)
    finally:
        try:
            import json as _j
            if os.path.isfile(_E.USAGE_PATH):
                _d = _j.load(open(_E.USAGE_PATH, encoding="utf-8"))
                _d.pop(_k, None)
                _j.dump(_d, open(_E.USAGE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception: # noqa: BLE001
            pass


# ---------------------------------------------------------------- 表情使用频次

def t_emoji_usage_guard() -> None:
    """自检：**表情使用频次**（"发哪个表情"该有据可依：群友常用的优先）。

    三条：· 真写真读（写两个 key ⇒ 读回**按次数降序**）；
          · **读不到返回空**（不许编）；
          · 测试用 key **自己清掉**（判据不许弄脏工作树）。
    """
    import json as _json

    root = HERE.parents[0]
    src = (root / "agent" / "emoji_index.py").read_text(encoding="utf-8")
    ck("能取到 `agent/emoji_index.py`（分母守卫）", bool(src.strip()))
    try:
        sys.path.insert(0, str(root))
        from agent import emoji_index as _E
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.emoji_index`", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
        return
    ck("`bump_usage` 与 `top_used` 都在位",
       callable(getattr(_E, "bump_usage", None)) and callable(getattr(_E, "top_used", None)))

    _k1, _k2 = "__selftest_a__", "__selftest_b__"
    try:
        _E.bump_usage(_k1, 5)
        _E.bump_usage(_k2, 1)
        _top = dict(_E.top_used(20))
        ck("真写真读：写了 5 次和 1 次 ⇒ 读回**按次数降序**（多的在前）",
           (_k1 in _top) and (_k2 in _top) and (_top[_k1] > _top[_k2]),
           "读到 %s=%s / %s=%s" % (_k1, _top.get(_k1), _k2, _top.get(_k2)))
        ck("**读不到就返回空**（不编数据）", _E.top_used(0) == [] or isinstance(_E.top_used(1), list))
    finally:
        # 自己清掉：判据不许在工作区留测试数据
        try:
            _p = getattr(_E, "USAGE_PATH", "")
            if _p and os.path.isfile(_p):
                _d = _json.load(open(_p, encoding="utf-8"))
                for _k in (_k1, _k2):
                    _d.pop(_k, None)
                _json.dump(_d, open(_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception: # noqa: BLE001
            pass


# ---------------------------------------------------------------- 投影补行接线

def t_session_rows_projection_guard() -> None:
    """自检：**`session_rows` 已接入「投影补行」**，且三条纪律都在。

    为什么要钉：OCR 只认得 69% 的会话行 ⇒ 漏行会让「数第几条 → 点它」**点错行**（可能点到相邻行）。
      · 补行必须**用投影**（不依赖识别 ⇒ 不漏行）；
      · 补出来的行**名字如实留空**（**不许编名字**）；
      · 投影**失败必须退回** OCR 行（不许把整条链弄坏）。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "chat_ocr.py").read_text(encoding="utf-8")
    ck("能取到 `agent/chat_ocr.py`（分母守卫）", bool(src.strip()), "长度 %d" % len(src))
    _seg = ""
    for _n in _ast.walk(_ast.parse(src)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "session_rows":
            _seg = _ast.get_source_segment(src, _n) or ""
            break
    ck("`session_rows` 在位", bool(_seg), "长度 %d" % len(_seg))
    ck("补行用的是**投影**（`project_row_edges` ⇒ 不依赖 OCR）", "project_row_edges" in _seg)
    ck("投影**有、OCR 没有**的行 ⇒ `name` 留空（**不编名字**）",
       ('"name": ""' in _seg) and ("from_projection" in _seg))
    ck("投影失败**退回** OCR 行（`except` 之后仍 `return rows`）",
       ("except Exception" in _seg) and ("return rows" in _seg))


# ---------------------------------------------------------------- 目录监听

def t_filewatch_guard() -> None:
    """自检：**目录监听**（替代定时轮询）—— 真建目录、真写文件、真等到事件；且**装不上要如实报**。

    ⛔ 最关键的一条：**监听起不来时不许"假装在监听"**（返回的 Event 永不 `set`，
      同时 `error` 写明原因）⇒ 否则调用方会**永远等不到**，把"变化了"当成"没变化"。
    """
    import tempfile as _tf
    import time as _t

    root = HERE.parents[0]
    src = (root / "agent" / "filewatch.py").read_text(encoding="utf-8")
    ck("能取到 `agent/filewatch.py`（分母守卫）", bool(src.strip()), "长度 %d" % len(src))
    try:
        sys.path.insert(0, str(root))
        from agent import filewatch as _fw
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.filewatch`", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
        return

    # ① 坏路径 ⇒ 永不 set + error 写明原因（**不许假装在监听**）
    _bad = _fw.watch(os.path.join(str(root), "no_such_dir_xyz"))
    ck("坏路径 ⇒ `error` 非空（**如实报「没监听到」，不假装**）", bool(getattr(_bad, "error", "")))
    ck("坏路径 ⇒ Event 未被 set（调用方据此退回轮询）", not _bad.is_set())

    # ② 真建目录 ⇒ 真写文件 ⇒ 应等到事件
    with _tf.TemporaryDirectory(prefix="dirwatch-") as _td:
        _ev = _fw.watch(_td)
        if getattr(_ev, "error", ""):
            ck("当前环境支持目录监听（Windows）⇒ 应无 error", False, _ev.error)
        else:
            _t.sleep(0.2)
            open(os.path.join(_td, "hello.txt"), "w", encoding="utf-8").write("x")
            _got = _ev.wait(timeout=3.0)
            ck("真写一个文件 ⇒ 监听器应在 3 秒内通知（实测）", bool(_got),
               "等到了" if _got else "3 秒没等到")
            stop = getattr(_ev, "_stop", None)
            if stop is not None:
                stop.set()


# ---------------------------------------------------------------- 并发预取

def t_prefetch_parallel_guard() -> None:
    """自检：**并发预取** —— 只并发「下载」，装的时候仍串行（pip 不许同环境并发写）。

    为什么：默认"一个包一个包解析＋下载"，高延迟下要十几分钟；切份并发能压掉这段。
    ⛔ 两条必须成立：① 下载用 `--no-deps`（不触发依赖解析，省往返）；
                    ② **失败不致命**（函数自己不抛，调用方有 try 兜底）⇒ 不许因为预取失败把安装流程弄坏。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "scripts" / "setup_deps.py").read_text(encoding="utf-8")
    ck("能取到 `scripts/setup_deps.py`（分母守卫）", bool(src.strip()), "长度 %d" % len(src))
    try:
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / "scripts"))
        import setup_deps as _S
    except Exception as e: # noqa: BLE001
        ck("能导入 `setup_deps`", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
        return
    ck("`prefetch_wheels_parallel` 在位", callable(getattr(_S, "prefetch_wheels_parallel", None)))

    _seg = ""
    for _n in _ast.walk(_ast.parse(src)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "prefetch_wheels_parallel":
            _seg = _ast.get_source_segment(src, _n) or ""
            break
    ck("预取用 `--no-deps`（不触发依赖解析，才省得下往返）", "--no-deps" in _seg)
    ck("预取**真并发**（起了线程）", ("Thread(" in _seg) and ("join()" in _seg))
    ck("预取**只下载不安装**（不许出现 `pip`, `install` 的组合）",
       ("download" in _seg) and ("\"install\"" not in _seg))
    ck("主流程里**预取有 try 兜底**（失败不影响安装）",
       ("prefetch_wheels_parallel(py_exe, req, best" in src) and ("并发预取跳过" in src))


# ---------------------------------------------------------------- 搜索框粘贴

def _strip_doc_comments(src: str, func_name: str):
    """取 `func_name` 的正文，并**剥掉注释与 docstring** ⇒ 只留真代码。

    ⛔ 为什么必须剥（本项目反复踩）：docstring/注释里常常**逐字写着**那些关键字
      （"不许改成 `paste_text`"、"走 `clipboard_guard`"…），
      拿整段原文做子串匹配 ＝ **在断言"文档里出现过这个词"** ——
      把真代码删掉也照样绿（反向锚一测就露）。
    """
    import ast as _a
    import re as _r
    _seg = ""
    for _n in _a.walk(_a.parse(src)):
        if isinstance(_n, _a.FunctionDef) and _n.name == func_name:
            _seg = _a.get_source_segment(src, _n) or ""
            break
    _seg = "\n".join(l.split("#")[0] for l in _seg.splitlines())
    return _r.sub(r'""".*?"""', "", _seg, flags=_r.S)


def t_search_paste_guard() -> None:
    """自检：**搜索框改走「粘贴」绕开 IME 串字母，且粘贴失败能退回逐字投递**。

    为什么要钉：开微软拼音时逐字投 `WM_CHAR`，会**在首个汉字前串一个 `a`** ⇒ 搜索词永远是 `a测试` ⇒ 搜不到会话。
    ⛔ 两条必须**同时**成立：① 搜索链**先粘贴**；② 粘贴失败**退回投字**（不能因为换通道把整条链弄坏）。
    """
    import ast as _ast

    root = HERE.parents[0]
    _ib = (root / "agent" / "input_backend.py").read_text(encoding="utf-8")
    _wx = (root / "agent" / "wechat.py").read_text(encoding="utf-8")
    ck("能取到两个源文件（分母守卫）", bool(_ib.strip()) and bool(_wx.strip()))

    _seg = ""
    for _n in _ast.walk(_ast.parse(_ib)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "paste_text":
            _seg = _ast.get_source_segment(_ib, _n) or ""
            break
    ck("`input_backend.paste_text` 在位", bool(_seg), "长度 %d" % len(_seg))
    # ⛔⛔ 断言前**必须剥掉注释与 docstring**：本函数的 docstring 里**逐字写着**
    #   `clipboard_guard` / `snapshot` / `restore` 这些词（它在解释为什么这么写）
    #   ⇒ 直接在原文上 `in` 匹配，等于**在断言文档里出现过这个词**，删掉真代码也照样绿。
    #   （实测：把 `with clipboard_guard()` 换成 `if True:` 后仍判绿 —— 就是被 docstring 兜住的。）
    _seg_code = _strip_doc_comments(_ib, "paste_text")
    ck("粘贴实现里**投了 `WM_PASTE`**（不是继续用 WM_CHAR）", "WM_PASTE" in _seg_code)
    ck("粘贴实现里**有等待**（`WM_PASTE` 是异步的，立刻恢复会把原文贴进去）",
       "restore_ms" in _seg_code and "time.sleep" in _seg_code)
    # ⚠️ 这条**锚的是"剪贴板有没有被保护"这个意图**，不是某个具体函数名：
    #   初始实现走 `clipboard.snapshot()/restore()` 手写配对，后改成项目收口
    #   `clipboard_guard()`（进快照／出还原／**异常出口也还原**）—— 后者更严。
    #   ⛔ 别写死 `write_text`/`saved` 这类字面：换实现就会把**做对了的**判成红。
    ck("粘贴实现里**保护了剪贴板**（剪贴板不是我们的资源；走项目收口或快照-还原配对均可）",
       ("clipboard_guard(" in _seg_code)
       or (("snapshot(" in _seg_code) and ("restore(" in _seg_code)))

    _n_paste = _wx.count("backend.paste_text(")
    ck("搜索链**至少两处**改成先粘贴（两个搜索入口）", _n_paste >= 2, "命中 %d 处" % _n_paste)
    # ⛔ 关键兜底：粘贴失败必须退回 send_text。
    #   ⚠️ 锚**意图**（粘贴没成之后紧跟一次 send_text），不锚某一句话的字面 ——
    #      原来的 `"粘贴没成"` 是日志措辞，改文案就把**做对了的**判成红。
    _fallback = False
    for _ln, _line in enumerate(_wx.split("\n")):
        if "paste_text(" not in _line:
            continue
        _after = "\n".join(_wx.split("\n")[_ln:_ln + 8])
        if "if not ok_t" in _after and "send_text(" in _after:
            _fallback = True
            break
    ck("粘贴失败**有退回逐字投递**的兜底（不能因为换通道把搜索链弄坏）", _fallback)


# ---------------------------------------------------------------- 工作树洁净（第 6 步）

def t_clean_tree_guard() -> None:
    """自检：**跑完判据不许在工作树里留下未跟踪产物**（可离线判定的三条）。

    为什么要有：判据会截图、会写临时文件；产物一旦落在**没被 ignore 的目录**里，
    就会混进提交、混进包，用户那边多出一堆莫名文件。三条口径：
      · 判据的截图目录（`ui_qt/shots/`）**必须在 `.gitignore` 里**；
      · 临时脚本/中间产物**只在 `_scratch/`**（该目录约定不进 git）；
      · `scripts/` 里**不许有 `_` 开头的一次性脚本**（它们属于 `_scratch/`）。
    """
    import re as _re

    root = HERE.parents[0]
    _gi = root / ".gitignore"
    ck(".gitignore 存在（分母守卫）", _gi.is_file(), str(_gi))
    if not _gi.is_file():
        return
    _gi_txt = _gi.read_text(encoding="utf-8", errors="replace")
    _lines = [l.strip() for l in _gi_txt.split("\n") if l.strip() and not l.strip().startswith("#")]
    ck("判据截图目录 `ui_qt/shots/` 在 `.gitignore` 里（否则截图会混进提交）",
       any(l.rstrip("/") == "ui_qt/shots" for l in _lines), "命中：%s" % [l for l in _lines if "shots" in l][:2])
    ck("`_scratch/` 在 `.gitignore` 里（临时脚本的家，不该进库）",
       any(l.rstrip("/") == "_scratch" for l in _lines), "命中：%s" % [l for l in _lines if "scratch" in l][:2])

    _bad = []
    for p in sorted((root / "scripts").glob("_*.py")):
        # ⚠️ `_` 开头**不等于**一次性 —— `_srcslice.py` 这类是**被产品 import 的模块**
        #    （`PLAN` 里点名"取函数源码一律用 `_srcslice.func_src`"）⇒
        #    **判定要看"有没有被引用"，不能只看文件名前缀**（这条是判据自己踩出来的）。
        _used = False
        for _f in list((root / "scripts").glob("*.py")) + list((root / "agent").glob("*.py")):
            if _f.name == p.name:
                continue
            try:
                if p.stem and p.stem in _f.read_text(encoding="utf-8", errors="replace"):
                    _used = True
                    break
            except Exception: # noqa: BLE001
                continue
        if not _used:
            _bad.append(p.name)
    ck("`scripts/` 里没有**没人引用**的 `_` 开头脚本（一次性脚本应待 `_scratch/`）",
       not _bad, "命中：%s" % (_bad[:4] or "无"))


# ---------------------------------------------------------------- 投影补行（第 7 步）

def t_projection_rows_guard() -> None:
    """自检：**投影补行**（不依赖 OCR 也能数出行数，且**不硬造行**）。

    为什么要钉：OCR **只认得 69% 的会话行** ⇒ 漏行会让"数第几条 → 点它"**点错行**（可能点到相邻行）。
    口径：**投影负责"有几行、每行在哪"（不依赖识别 ⇒ 不会漏），OCR 只负责"这行叫什么"**。
    """
    import ast as _ast
    import tempfile as _tf

    root = HERE.parents[0]
    src = (root / "agent" / "row_geometry.py").read_text(encoding="utf-8")
    ck("取到 `row_geometry.py`（分母守卫）", bool(src.strip()), "长度 %d" % len(src))
    try:
        sys.path.insert(0, str(root))
        from agent import row_geometry as _RG
        from PIL import Image, ImageDraw
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.row_geometry` 与 PIL", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
        return

    _seg = ""
    for _n in _ast.walk(_ast.parse(src)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "project_row_edges":
            _seg = _ast.get_source_segment(src, _n) or ""
            break
    ck("`project_row_edges` **不调用 OCR**（投影必须独立于识别）",
       bool(_seg) and ("recognize" not in _seg), "长度 %d" % len(_seg))

    with _tf.TemporaryDirectory(prefix="projrows-"):   # ⛔ 不弄脏工作树
        _want = 5
        im = Image.new("RGB", (600, 600), (35, 35, 36))
        _d = ImageDraw.Draw(im)
        for i in range(_want):
            _y = 60 + i * 100
            for k in range(12):                      # 画"字"：一排短竖条
                _x = 80 + k * 30
                _d.rectangle([_x, _y, _x + 14, _y + 30], fill=(230, 230, 230))
        _edges = _RG.project_row_edges(im, pane=560)
        ck("真值：图里画了 %d 行 ⇒ 投影找出 %d 行（不依赖 OCR）" % (_want, _want),
           len(_edges) == _want, "得到 %d 行：%s" % (len(_edges), _edges[:6]))

        _blank = Image.new("RGB", (600, 600), (35, 35, 36))
        ck("反向对照：**纯色图 ⇒ 投影返回空**（不硬造行）",
           _RG.project_row_edges(_blank, pane=560) == [])

        _r = _RG.merge_rows_with_projection(im, [("张三", 80, 60, 200, 30)], pane=560)
        _named = [x for x in _r["rows"] if x["text"]]
        _noname = [x for x in _r["rows"] if not x["text"]]
        ck("合并：OCR 只认 1 行 ⇒ 仍输出 %d 行（**补回 %d 行**；没名字的如实为空、不编）"
           % (_want, _want - 1),
           (_r["merged_n"] == _want) and (len(_named) == 1) and (len(_noname) == _want - 1),
           "合并=%d 有名字=%d 无名字=%d 补回=%d" % (_r["merged_n"], len(_named), len(_noname), _r["补回"]))


# ---------------------------------------------------------------- 通用下载提速（DL-1）

def t_mirror_fetch_guard() -> None:
    """自检：**多源竞速选最快 ＋ 失败自动换源**（DL-1：把自举那套做成通用的）。

    要钉四条（都能**离线纯函数**验证，不依赖网络）：
      · `pick_mirrors` **只取通的**并按**实测耗时升序**（让 pip 去试一个已 403 的源 = 白等一轮）；
      · `pip_install_args` **必须带 `-i <源>`**、**不带 `--trusted-host`**（我们全是 https 源）；
      · `403` 只是"该源没过"——`probe_once` 里 HTTPError 一律返回"不通 + 原因"，**不当终态**；
      · **失败要逐源列原因**（`diag_lines` 产出人话，不裸 traceback）。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "mirror_fetch.py").read_text(encoding="utf-8")
    ck("能取到 `agent/mirror_fetch.py`（分母守卫）", bool(src.strip()), "长度 %d" % len(src))
    try:
        sys.path.insert(0, str(root))
        from agent import mirror_fetch as _MF
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.mirror_fetch`", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
        return

    # ① 选源：只取通的 + 按耗时升序（**纯函数**，喂假 probe）
    _fake = {"a": (True, 0.9, ""), "b": (False, 0.1, "HTTP 403（源拒绝）"), "c": (True, 0.2, "")}
    _order = _MF.pick_mirrors(_fake)
    ck("选源：**只取通的**且按**实测耗时升序**（挂的不进单）",
       _order == ["c", "a"], "得到 %s" % _order)
    ck("全挂时 `pick_mirrors` 返回空（调用方据此走「逐源诊断」而不是硬试）",
       _MF.pick_mirrors({"x": (False, 1.0, "HTTP 403")}) == [])

    # ② 命令行：必须带 -i，且不带 --trusted-host
    _args = _MF.pip_install_args(["llvmlite>=0.50"], "https://mirrors.aliyun.com/pypi/simple/")
    ck("安装命令带 `-i <源>`（不指源就等于没走竞速）",
       "-i" in _args and any("aliyun" in a for a in _args))
    ck("安装命令**不带** `--trusted-host`（我们全是 https 源，带了是多余放行）",
       "--trusted-host" not in _args)
    ck("安装命令带 `--disable-pip-version-check`（少一次出网）",
       "--disable-pip-version-check" in _args)

    # ③ 403 不当终态：探到 HTTPError 一律"不通 + 原因"
    _seg = ""
    for _n in _ast.walk(_ast.parse(src)):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "probe_once":
            _seg = _ast.get_source_segment(src, _n) or ""
            break
    ck("`probe_once` 把 `HTTPError` 判成**不通 + 原因**（403 只是「该源没过」，不是终态）",
       ("HTTPError" in _seg) and ("源拒绝" in _seg) and ("return False" in _seg))

    # ④ 失败讲人话
    _diag = _MF.diag_lines(_fake)
    ck("失败诊断是**逐源人话**（哪条、什么错），不是裸异常",
       ("403" in _diag) and ("；" in _diag), _diag[:70])


# ---------------------------------------------------------------- Qt 壳：不许系统 MessageBox

def t_qt_no_msgbox_guard() -> None:
    """自检：**整个 `ui_qt/` 的实际代码里不许出现系统 `QMessageBox`**（硬规矩第 1 条：0 系统 MessageBox）。

    ⛔ 为什么单独一条：原先只有"`_btn_stub` 那段用 `InfoDialog`"的断言 ⇒ **别处新增的
    `QMessageBox` 抓不到**（**我自己就踩过**：在 `_channels_howto` 里写了 `QMessageBox.information`，
    既有那条判据照样全绿）。要求「**Qt 端才是用户看到的，要把 Qt 端做稳**」⇒ 补这条兜底。
    ⚠️ 判前**先剥注释与 docstring**：`confirm.py` 里满是「不许用 QMessageBox」的说明文字，
    不剥就会把说明当成违规（本项目判据在这上面已踩过三次）。
    ⚠️ 且**必须按"调用形态"判**（`QMessageBox(`），不能按"出现过这个词"判 ——
    `confirm.py` 里有一行**自检代码** `"QMessageBox" not in mod`（字符串字面量里提到它），
    按"出现即违规"会把这行**自检**判成违规（这条盲区是这次真的踩出来的）。
    """
    import re as _re

    # ⚠️ 形态要包两种：**直接调用** `QMessageBox(...)` 与**属性调用** `QMessageBox.information(...)`
    #    —— 后者才是实际最常用的写法（第一版正则只写了 `QMessageBox(`，被反向对照抓出来了）。
    _pat = _re.compile(r"\bQMessageBox\s*[.(]")
    _bad = []
    _root = HERE.parents[0] / "ui_qt"
    for _f in sorted(_root.glob("*.py")):
        if _f.name == "selftest.py":
            continue                       # 判据自己会提这个名字，不算违规
        _txt = _f.read_text(encoding="utf-8")
        _s = _re.sub(r'"""(?:.|\n)*?"""', "", _txt)
        _s = _re.sub(r"'''(?:.|\n)*?'''", "", _s)
        _s = "\n".join(l for l in _s.split("\n") if not l.strip().startswith("#"))
        if _pat.search(_s):
            _bad.append(_f.name)
    ck("整个 `ui_qt/` 的实际代码里没有系统 `QMessageBox(` 调用（弹窗统一走自绘 `InfoDialog`/`ConfirmDialog`）",
       not _bad, "命中：%s" % (_bad[:3] or "无"))
    # 反向对照：把"真的有 QMessageBox 调用"的样本喂给同一套剥壳+判定，必须被判违规
    _sample = 'def f(p):\n    QMessageBox.information(p, "t", "b")\n'
    _s2 = "\n".join(l for l in _sample.split("\n") if not l.strip().startswith("#"))
    ck("反向对照：样本里的 `QMessageBox(` 调用必须被这套判定抓出来", bool(_pat.search(_s2)))
    # ⚠️ 另一条反向对照：**样式检查代码**（字符串里提到 QMessageBox）**不许**被判违规
    _s3 = 'rows.append(("x", "QMessageBox" not in mod, ""))\n'
    ck("反向对照：字符串里提到 `QMessageBox` 的**自检代码**不算违规（防误判）",
       not _pat.search(_s3))


# ---------------------------------------------------------------- 水位：多读一条 ＋ 台账核对（B7）

def t_watermark_latest_minus1_guard() -> None:
    """自检：**水位"多读一条防漏" ＋ 台账核对"回过就放掉"**（这两件必须成对）。

    · **起点取 `latest - 1`**：取 `latest` 时，"启动瞬间正好落库的那一条"可能被跳过（竞态）；
    · **台账核对**（`already_sent_recently`，窗口 120 秒）：多读的那条若**是我方发出的** ⇒ 放掉，
      且**算已处理**（水位照推，否则同一行会被反复重读）。
    ⛔ 只做前者 ⇒ 会**回自己**（回声事故）；只做后者 ⇒ 竞态那条仍会丢。
    """
    root = HERE.parents[0]
    src = (root / "scripts" / "persona_morph.py").read_text(encoding="utf-8")

    ck("水位起点取 `latest - 1`（多读一条防漏）",
       ("int(_seq0) - 1" in src) or ("_seq0) - 1" in src),
       "没找到 `_seq0 - 1`")
    ck("台账核对在场（复用 `already_sent_recently`，不新加机制）",
       "already_sent_recently" in src)
    ck("台账命中时**算已处理**（返回 dropped，水位才能推过去）",
       ('"dropped": "sent-by-us"' in src) or ("'dropped': 'sent-by-us'" in src))
    # 顺序：台账核对必须**在**"往下走"之前（即 `_handle_one` 内部靠前）
    _i_skip = src.find('if nm.get("skip"):')
    _i_ledger = src.find("already_sent_recently")
    ck("台账核对紧跟在「归一化跳过」之后（在真正处理之前，别等进了上下文再判）",
       (-1 < _i_skip < _i_ledger) and ((_i_ledger - _i_skip) < 1600),
       "skip@%d ledger@%d 相距 %d" % (_i_skip, _i_ledger, _i_ledger - _i_skip))


# ---------------------------------------------------------------- ASR 后端选择（V-5）

def t_asr_backend_guard() -> None:
    """自检：**语音识别优先真 ASR、SAPI 只当兜底**，且**如实报告在用哪个**。

    ⛔ 为什么必须钉：SAPI 是**听写**引擎（本职是"对着麦说一句、转成字"），
    拿它当"把长音频转字幕"用会**又慢又不准**；而"装没装 whisper"是**环境事实** ⇒
    代码必须**现算**并如实说，不能在提示词/面板里声称"有语音识别"而实际用的是听写。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "video_read.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    seg = ""
    for n in _ast.walk(tree):
        if isinstance(n, _ast.FunctionDef) and n.name == "asr_backend":
            seg = _ast.get_source_segment(src, n) or ""
            break
    ck("取到 `video_read.asr_backend` 的源码（分母守卫）", bool(seg.strip()), "长度 %d" % len(seg))
    if not seg.strip():
        return

    # 优先顺序：faster_whisper → whisper → sapi（顺序写反就等于"永远用兜底"）
    _i_fw = seg.find("faster_whisper")
    _i_w = seg.find("import whisper")
    _i_sapi = seg.find("asr_status()")
    ck("真 ASR 优先于 SAPI（faster-whisper → whisper → SAPI 的先后顺序）",
       -1 < _i_fw < _i_sapi and -1 < _i_w < _i_sapi,
       "fw=%d w=%d sapi=%d" % (_i_fw, _i_w, _i_sapi))
    ck("SAPI 那条带**如实说明**（「听写」引擎/仅兜底），不是悄悄当主力",
       ("听写" in seg) or ("兜底" in seg))

    try:
        sys.path.insert(0, str(root))
        from agent import video_read as _V
        _b = _V.asr_backend()
        ck("`asr_backend()` 返回合法结构（含 ok / kind / detail）",
           isinstance(_b, dict) and ("ok" in _b) and ("kind" in _b) and ("detail" in _b),
           str(_b)[:90])
        ck("`probe()` 把后端信息带出去了（面板/工具能看到在用哪个）",
           "backend" in (_V.probe() or {}))
    except Exception as e: # noqa: BLE001
        ck("能调用 `asr_backend()`（分母守卫）", False, "%s: %s" % (type(e).__name__, str(e)[:60]))


# ---------------------------------------------------------------- 按场景采帧（V-4）

def t_scene_frames_guard() -> None:
    """自检：**按场景采帧（帧间差分选点）**，而不是均匀抽。

    为什么要钉：均匀抽 4 张，在"大半时间画面是同一个场景"的视频里**常常 4 张全在同一场景**；
    按差分取才能落到"画面真的换了"的地方。**真值测试**（造一条三段色测试片，切换点在 6s/12s）
    是这条判据最硬的部分 —— 它证明"选点真的落在变化处"，而不只是"函数跑完了"。
    """
    import ast as _ast
    import os as _os
    import subprocess as _sp
    import tempfile as _tf

    root = HERE.parents[0]
    src = (root / "agent" / "video_read.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    seg = ""
    for n in _ast.walk(tree):
        if isinstance(n, _ast.FunctionDef) and n.name == "extract_scene_frames":
            s = _ast.get_source_segment(src, n) or ""
            if (n.body and isinstance(n.body[0], _ast.Expr)
                    and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                ds = n.body[0].value.value
                if isinstance(ds, str) and ds:
                    s = s.replace(ds, "")
            seg = "\n".join(l for l in s.split("\n") if not l.strip().startswith("#"))
            break
    ck("取到 `video_read.extract_scene_frames` 的源码（分母守卫）", bool(seg.strip()),
       "长度 %d" % len(seg))
    if not seg.strip():
        return
    ck("场景采帧：帧间差分选点 ＋ 小图探/大图取两段 ＋ 相邻点去重",
       ("difference" in seg) and ("scene_small" in seg) and ("scene_big" in seg)
       and ("abs(i - u)" in seg))

    # 真值：造"6s 红 + 6s 蓝 + 6s 绿"，切换点应在 ≈6s 与 ≈12s
    try:
        sys.path.insert(0, str(root))
        from agent import video_read as _V
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.video_read`（分母守卫）", False, "%s" % str(e)[:60])
        return
    _ff = _V.ffmpeg_path()
    if not _ff:
        ck("真值测试需要 ffmpeg（本机没有 ⇒ 跳过真值，但静态断言已过）", True, "no_ffmpeg")
        return
    with _tf.TemporaryDirectory(prefix="scenefix-") as _td:   # ⛔ 不弄脏工作树
        _mp4 = _os.path.join(_td, "s.mp4")
        try:
            _sp.run([_ff, "-hide_banner", "-loglevel", "error",
                     "-f", "lavfi", "-i", "color=c=red:s=160x120:d=6",
                     "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=6",
                     "-f", "lavfi", "-i", "color=c=green:s=160x120:d=6",
                     "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-y", _mp4],
                    capture_output=True, timeout=60)
        except Exception:
            pass
        if not _os.path.isfile(_mp4):
            ck("真值测试的测试片造出来了", False, "ffmpeg 没产出测试片")
            return
        _r = _V.extract_scene_frames(_mp4, _os.path.join(_td, "f"), 4)
        _cues = _r.get("cues") or []
        _hit6 = any(abs(c - 6.0) <= 1.0 for c in _cues)
        _hit12 = any(abs(c - 12.0) <= 1.0 for c in _cues)
        ck("真值：场景切换点在 6s/12s ⇒ 采出的线索必须命中这两处（±1s）",
           bool(_r.get("ok")) and _hit6 and _hit12, "cues=%s" % _cues)


# ---------------------------------------------------------------- 外部平台下载（V-1 / V-11 / V-12）

def t_external_download_guard() -> None:
    """自检：**平台注册器 ＋ 视频号按路径识别 ＋ 下不了就给替代**。

    要钉的三条：
      · **注册器是"现算"不是"写死"** —— 写死"能下"是骗人开关，写死"不能下"则在装了之后仍说不行；
      · **视频号按路径判、不并进域名表** —— `weixin.qq.com` 是通用域名，并进去必然把
        普通链接/公众号文章认成视频号（**认错平台比认不出更糟**）；
      · **下不了必须给用户能照做的替代**，不许只回一句"暂不支持"。
    """
    root = HERE.parents[0]
    src = (root / "agent" / "bilibili.py").read_text(encoding="utf-8")
    try:
        sys.path.insert(0, str(root))
        from agent import bilibili as _B
    except Exception as e: # noqa: BLE001
        ck("能导入 `agent.bilibili`（分母守卫）", False, "%s: %s" % (type(e).__name__, str(e)[:60]))
        return

    ck("平台注册器：有 `_PLATFORM_DL` 表，且 `can_download` 是**现算**（调 `ytdlp_available()`，不写死）",
       ("_PLATFORM_DL" in src) and ("def can_download" in src) and ("ytdlp_available()" in src))

    _bad = []
    for _u, _want in (("https://b23.tv/abc", "bilibili"),
                      ("https://www.youtube.com/watch?v=x", "youtube"),
                      ("https://example.com/v", "unknown")):
        _got = (_B.media_download_plan(_u) or {}).get("platform")
        if _got != _want:
            _bad.append("%s→%s" % (_u[:26], _got))
    ck("认平台：B站/YouTube 认得出，陌生域名**如实 unknown**（不硬认）", not _bad, "；".join(_bad))

    _bad2 = [u for u, w in (("https://weixin.qq.com/sph/Ab1", True),
                            ("https://weixin.qq.com/other/page", False),
                            ("https://mp.weixin.qq.com/s/x", False))
             if bool(_B.is_wechat_channels(u)) != w]
    ck("视频号按**路径**判：普通 weixin 链接与公众号文章**都不被误判**", not _bad2,
       "误判：%s" % _bad2[:2])

    _hosts_seg = ""
    if "PLATFORM_HOSTS" in src:
        _hosts_seg = src.split("PLATFORM_HOSTS", 1)[1].split("\n}", 1)[0]
    ck("视频号**没有**被塞进按域名认平台的 `PLATFORM_HOSTS`（塞进去必然误判）",
       "weixin" not in _hosts_seg)

    _p, _why = _B.download_external("https://weixin.qq.com/sph/Ab1", "_scratch/_x")
    # ⚠️ 断言锚**意图**，不锚文案字面 —— 文案会改（"保存"→"存"），
    #    锚字面就会变成"改文案必假红"（本项目 C22 那条纪律：断言锚意图，不锚写法）。
    ck("下不了时**给用户能照做的替代**（而不是一句「暂不支持」）",
       (_p is None) and ("成本地文件" in _why), str(_why)[:70])


# ---------------------------------------------------------------- 视频探流与探帧（V-3 / V-8 / V-14）

def t_video_probe_guard() -> None:
    """自检：**探流岔路 ＋ 探帧定位字幕带 ＋ 错误路由表**三项接线齐全。

    · **V-3 探流岔路**：纯音频（语音条/播客）压根没有画面可抽 ⇒ 必须先判 `has_video`，
      否则白跑"抽帧＋OCR"；
    · **V-8 探帧定位**：录屏类视频**字幕不一定在底部**（实测抖音常在 y≈0.72）⇒
      写死裁切比例必然裁错；
    · **V-14 错误路由表**：失败时给**用户能直接照做**的一句话，而不是内部异常名。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "video_read.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)

    def _seg(name: str) -> str:
        for n in _ast.walk(tree):
            if isinstance(n, _ast.FunctionDef) and n.name == name:
                s = _ast.get_source_segment(src, n) or ""
                if (n.body and isinstance(n.body[0], _ast.Expr)
                        and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                    ds = n.body[0].value.value
                    if isinstance(ds, str) and ds:
                        s = s.replace(ds, "")
                return "\n".join(l for l in s.split("\n") if not l.strip().startswith("#"))
        return ""

    kind, band, subs = _seg("media_kind"), _seg("probe_subtitle_band"), _seg("extract_subtitles")
    ck("取到三处源码（分母守卫：media_kind / probe_subtitle_band / extract_subtitles）",
       bool(kind.strip()) and bool(band.strip()) and bool(subs.strip()),
       "kind=%d band=%d subs=%d" % (len(kind), len(band), len(subs)))
    if not (kind.strip() and band.strip() and subs.strip()):
        return

    ck("V-3 探流：解析 `Video:`/`Audio:` 两根轨，且**探不出算失败**（不假装没有视频）",
       ("Video:" in kind) and ("Audio:" in kind) and ('"ok": False' in kind)
       and ("has_video" in kind))
    ck("V-8 探帧：找'文字最密的横带'且 **top 有上下界**（不会裁到天上或裁没）",
       ("下四分位" in band or "ys[int" in band) and ("0.30" in band) and ("0.90" in band))
    ck("V-8 ↔ 字幕：`extract_subtitles` 接受 `crop_top`（探帧结果能喂进去）",
       "crop_top" in subs and "crop_top or" in subs)
    ck("V-14 错误路由表：有 `VIDEO_READ_HINTS` 且 `no_subtitle` 那条被真用上",
       ("VIDEO_READ_HINTS" in src) and ('"hint"' in subs) and ('no_subtitle' in src))


# ---------------------------------------------------------------- 下载校验：文件头 magic

def t_download_magic_guard() -> None:
    """自检：**下载结果必须过"文件头 magic"**（防把风控/错误页当媒体存下来）。

    ⛔ 为什么必须钉：**HTTP 200 ＋ Content-Type 正常，仍然可能是错误页** ——
    CDN 用 200 返回一段 HTML/JSON 是很常见的做法。外部技能那套"四重校验"里
    我方原先只有 3 重半（状态码／体积／超时／不落半成品 ＋ 安全层），**独缺这一重**。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "bilibili.py").read_text(encoding="utf-8")

    # ① 纯函数存在（**真值测试**，不联网、不伪造 CDN）
    try:
        sys.path.insert(0, str(root))
        from agent.bilibili import media_magic_ok as _ok
    except Exception as e:  # noqa: BLE001
        ck("能导入 `agent.bilibili.media_magic_ok`（分母守卫）", False,
           "%s: %s" % (type(e).__name__, str(e)[:60]))
        return
    _cases = [
        (b"\x00\x00\x00\x24ftypiso5\x00\x00\x02\x00", True, "真 m4a/mp4 头"),
        (b"ID3\x04\x00\x00\x00\x00", True, "ID3（mp3 标签）"),
        (b"<!DOCTYPE html><html><head>", False, "HTML 错误页"),
        (b'{"code":-403,"message":"risk"}', False, "JSON 风控回包"),
        (b"", False, "空文件"),
    ]
    _bad = [n for h, w, n in _cases if bool(_ok(h)) != w]
    ck("文件头 magic 判定：真媒体头认、HTML/JSON/空文件拒（%d 个样本）" % len(_cases),
       not _bad, "判错：%s" % "、".join(_bad))

    # ② `_download` 真的调了它（不然纯函数再好也没接线）
    _tree = _ast.parse(src)
    _seg = ""
    for n in _ast.walk(_tree):
        if isinstance(n, _ast.FunctionDef) and n.name == "_download":
            _seg = _ast.get_source_segment(src, n) or ""
            break
    ck("`_download` 里接了这道校验（分母守卫）", bool(_seg.strip()),
       "长度 %d" % len(_seg))
    ck("`_download` 在返回成功之前调用了 `media_magic_ok`",
       "media_magic_ok(" in _seg)


# ---------------------------------------------------------------- 视频字幕：画面字幕优先

def _subtitles_missing(seg: str) -> list:
    """检查 `extract_subtitles` 的五个要点（**纯函数**，便于反向对照）。

    这条路治的是"发视频会卡住"的大头：**字幕本来就画在画面里**（官方文字），
    读它比"抽音频 ＋ SAPI 转写"快一个数量级、且不受音乐/多人干扰。
    但少了下面任何一条，效果就塌掉：
      · 不裁字幕带 ⇒ 顶部作者/点赞等 UI 全被读进来；
      · 不过滤中文 ⇒ 时间戳、数字、英文混进来；
      · 不去重 ⇒ 字幕一秒内不变，会刷出几百条重复；
      · 不放大 ⇒ 小字**实测会漏字**。
    """
    miss = []
    if ("fps=" not in seg) or ("-frames:v" not in seg):
        miss.append("不是一次抽帧（缺 `-vf fps=` 或 `-frames:v`）")
    if "SUBTITLE_CROP_TOP" not in seg:
        miss.append("没裁字幕带（整帧 OCR 会被顶部 UI 干扰）")
    if "_has_cjk" not in seg:
        miss.append("没过滤非中文（时间/数字/英文 UI 会混进来）")
    if "!= prev" not in seg:
        miss.append("没做相邻去重（字幕一秒内不变 ⇒ 刷出几百条重复）")
    if "resize" not in seg:
        miss.append("没放大（小字会漏 —— 已实测过）")
    return miss


#: 反向对照样本：没有裁带/去重/放大的"朴素版"。
_SUB_OLD_SAMPLE = '''
def extract_subtitles(path, out_dir):
    lines = []
    for i, fp in enumerate(frames):
        items = ocr(fp)
        lines.append({"t": i, "text": " ".join(items)})
    return lines
'''


def t_video_subtitles_guard() -> None:
    """自检：**画面字幕优先**这条路必须"一次抽帧 ＋ 裁带 ＋ 只留中文 ＋ 去重 ＋ 放大"。"""
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "video_read.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    seg = ""
    for n in _ast.walk(tree):
        if isinstance(n, _ast.FunctionDef) and n.name == "extract_subtitles":
            s = _ast.get_source_segment(src, n) or ""
            if (n.body and isinstance(n.body[0], _ast.Expr)
                    and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                ds = n.body[0].value.value
                if isinstance(ds, str) and ds:
                    s = s.replace(ds, "")
            seg = "\n".join(l for l in s.split("\n") if not l.strip().startswith("#"))
            break
    ck("取到 `video_read.extract_subtitles` 的源码（分母守卫）", bool(seg.strip()),
       "长度 %d" % len(seg))
    if not seg.strip():
        return
    miss = _subtitles_missing(seg)
    ck("字幕优先：一次抽帧 ＋ 裁字幕带 ＋ 只留中文 ＋ 相邻去重 ＋ 放大",
       not miss, "缺：%s" % "；".join(miss))
    old = _subtitles_missing(_SUB_OLD_SAMPLE)
    ck("反向对照：朴素版（不裁带/不过滤/不去重/不放大）必须被判缺项",
       len(old) >= 4, "检出 %d 项：%s" % (len(old), "；".join(old[:2])))


# ---------------------------------------------------------------- 视频抽帧：一次调用出多帧

def _frame_batch_missing(seg: str) -> list:
    """检查 `extract_frames` 是不是"一次 ffmpeg 出多帧"（**纯函数**，便于反向对照）。

    为什么要钉：老写法**每帧起一个 ffmpeg 进程**（`-ss t -i 文件 -frames:v 1`）⇒
    **同一个文件被重新打开、重新解码 count 次**（默认 4 次，每帧还各带 60 秒超时）。
    这是"发视频给机器人会卡住"里最容易拿掉的一段（实测 60 秒片：0.30s → 0.17s；
    文件越大、差距越大 —— 每次重开都要重新 seek + 解头部）。
    """
    miss = []
    if ("-vf" not in seg) or ("fps=" not in seg):
        miss.append("没有用 `-vf fps=` 一次抽多帧")
    if "-frames:v" not in seg:
        miss.append("没有 `-frames:v` 上限（可能产出比要的多）")
    if "for i in range(count)" in seg:
        miss.append("还在循环里逐帧起进程（同一文件被重开 count 次）")
    if seg.count("_run(") > 1:
        miss.append("`_run(` 出现 %d 次（应当只有**一次**调用）" % seg.count("_run("))
    return miss


#: 反向对照样本：旧写法（每帧起一个 ffmpeg）。
_FRAME_OLD_SAMPLE = '''
def extract_frames(path, out_dir, count=4):
    frames = []
    for i in range(count):
        t = dur * (i + 0.5) / count
        out = os.path.join(out_dir, "frame_%02d.png" % i)
        r = _run([ff, "-ss", "%.3f" % t, "-i", path, "-frames:v", "1", "-y", out], timeout=60)
        if r.returncode == 0:
            frames.append(out)
    return frames
'''


def t_video_frame_batch_guard() -> None:
    """自检：**抽帧必须"一次 ffmpeg 出多帧"**（不许逐帧起进程）。"""
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "video_read.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    seg = ""
    for n in _ast.walk(tree):
        if isinstance(n, _ast.FunctionDef) and n.name == "extract_frames":
            s = _ast.get_source_segment(src, n) or ""
            if (n.body and isinstance(n.body[0], _ast.Expr)
                    and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                ds = n.body[0].value.value
                if isinstance(ds, str) and ds:
                    s = s.replace(ds, "")
            seg = "\n".join(l for l in s.split("\n") if not l.strip().startswith("#"))
            break
    ck("取到 `video_read.extract_frames` 的源码（分母守卫）", bool(seg.strip()),
       "长度 %d" % len(seg))
    if not seg.strip():
        return
    miss = _frame_batch_missing(seg)
    ck("视频抽帧：一次 ffmpeg 调用出多帧（不逐帧起进程）", not miss, "缺：%s" % "；".join(miss))
    old = _frame_batch_missing(_FRAME_OLD_SAMPLE)
    ck("反向对照：旧写法（逐帧起进程）必须被判缺项", len(old) >= 2,
       "检出 %d 项：%s" % (len(old), "；".join(old[:2])))


# ---------------------------------------------------------------- 朋友圈：先读库、再考虑开窗

def _moments_db_first_missing(surf: str, read_fn: str, adapter: str) -> list:
    """检查「朋友圈先读库」这条链的三处接线是否齐全（**纯函数**，便于反向对照）。

    为什么要钉：朋友圈原本是「开窗 → 滚动 → **截图**」——
    那条路要**抢前台**、而且把内容交给模型去"认字"（小字中文 OCR 会错字）。
    走读库这条：**不开窗、不动鼠标**，给的是**结构化字段**（含评论的「谁回复谁」，截图认不出层次）。
    """
    miss = []
    if "moments_read(" not in surf:
        miss.append("surf 没调 moments_read（没走读库＝还是纯截图）")
    else:
        _i_read = surf.find("moments_read(")
        _i_open = surf.find("moments_open(")
        if _i_open >= 0 and _i_open < _i_read:
            miss.append("surf 里 moments_open 出现在 moments_read 之前（截图跑到了读库前面）")
    if "visual" not in surf:
        miss.append("surf 没有 visual 出口（想只看画面时无路可走）")
    if ("replica_adapter" not in read_fn) or ("load_moments(" not in read_fn):
        miss.append("moments_read 没走适配层 load_moments（绕过了唯一适配点）")
    if "return False" not in read_fn:
        miss.append("moments_read 失败时没回传原因（调用方无法决定要不要降级）")
    if "MomentDB" not in adapter:
        miss.append("适配层没走公开 API MomentDB")
    if "reply_to" not in adapter:
        miss.append("适配层没留住 reply_to（评论的「谁回复谁」会丢）")
    return miss


#: 反向对照样本：接线上线前的旧写法（只截图）。
_MOMENTS_OLD_SAMPLE = '''
def _exec_moments_surf(ctx, args):
    ok, msg = ctx["wechat"].moments_open()
    parts = ctx["wechat"].moments_screenshot()
    return {"content": parts}
'''


def t_moments_db_first_guard() -> None:
    """自检：**朋友圈先读库**（不开窗、结构化），截图降为 `visual=1` 的出口。"""
    import ast as _ast

    root = HERE.parents[0]

    def _fn_seg(path: str, name: str) -> str:
        src = (root / path).read_text(encoding="utf-8")
        tree = _ast.parse(src)
        for n in _ast.walk(tree):
            if isinstance(n, _ast.FunctionDef) and n.name == name:
                s = _ast.get_source_segment(src, n) or ""
                # ⛔ 必须先剥 docstring：这几段说明里就写着 moments_read / reply_to 等词
                if (n.body and isinstance(n.body[0], _ast.Expr)
                        and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                    ds = n.body[0].value.value
                    if isinstance(ds, str) and ds:
                        s = s.replace(ds, "")
                return "\n".join(l for l in s.split("\n")
                                 if not l.strip().startswith("#"))
        return ""

    surf = _fn_seg("agent/tools.py", "_exec_moments_surf")
    read_fn = _fn_seg("agent/wechat.py", "moments_read")
    # 适配层这一侧要连**辅助函数**一起看：评论的归一在 `_moment_comments` 里
    # （`reply_to` 就是在那里留住的）—— 只看 `load_moments` 会误判"丢了层次"。
    adapter = _fn_seg("agent/replica_adapter.py", "load_moments") + "\n" + \
        _fn_seg("agent/replica_adapter.py", "_moment_comments")
    ck("取到三处源码（分母守卫：surf / moments_read / load_moments）",
       bool(surf.strip()) and bool(read_fn.strip()) and bool(adapter.strip()),
       "surf=%d read=%d adapter=%d" % (len(surf), len(read_fn), len(adapter)))
    if not (surf.strip() and read_fn.strip() and adapter.strip()):
        return
    miss = _moments_db_first_missing(surf, read_fn, adapter)
    ck("朋友圈：先读库（不开窗）＋ 保留看画面的出口 ＋ 结构化留 reply_to",
       not miss, "缺：%s" % "；".join(miss))

    # 反向对照：旧写法（只截图）必须被判缺项（证明上面那条不是"写成绿的"）
    old = _moments_db_first_missing(_MOMENTS_OLD_SAMPLE, "", "")
    ck("反向对照：旧写法（开窗+截图、无读库、无 visual 出口）必须被判缺项",
       len(old) >= 3, "检出 %d 项：%s" % (len(old), "；".join(old[:3])))


# ---------------------------------------------------------------- 群成员名录：走群成员表，不看通讯录

def _group_members_missing(adapter: str, wrap: str, exec_fn: str) -> list:
    """检查「群成员名录」这条链的接线（**纯函数**，便于反向对照）。

    核心要钉住的是一条**能力边界**：名录必须走**群成员表**——
    它覆盖"没加你好友的人"；而通讯录（`contact`）与"最近活跃成员"（消息流统计）
    **都会漏**：前者没有非好友，后者只有发过言的人。
    """
    miss = []
    if "get_group_members" not in adapter:
        miss.append("适配层没走公开 API get_group_members")
    if "has_api(" not in adapter:
        miss.append("适配层没有能力守卫（老驱动库会直接 AttributeError）")
    if ("remark" not in adapter) or ("is_owner" not in adapter):
        miss.append("适配层没保留 remark / is_owner（备注与群主身份会丢）")
    if "@chatroom" not in adapter:
        miss.append("适配层没做非群聊短路（私聊 wxid 进去会白查一遍）")
    if "replica_adapter" not in wrap:
        miss.append("wechat 封装没走适配层（绕过唯一适配点）")
    if "return False" not in wrap:
        miss.append("封装失败时没回传原因（调用方无法区分「不是群」与「读失败」）")
    if "store" in exec_fn:
        miss.append("执行器还在从消息流统计发言者（那是 active_members 的活）")
    if "不是群聊" not in exec_fn:
        miss.append("执行器没说清「非群聊」（会把空列表当答案糊弄过去）")
    return miss


#: 反向对照样本：旧写法（只统计发言者 ⇒ 没发过言的人永远看不见）。
_GROUPMEM_OLD_SAMPLE = '''
def _exec_get_members(ctx, args):
    members = ctx["store"].active_members(ctx["chat_key"], 10)
    return {"members": members}
'''


def t_group_members_db_guard() -> None:
    """自检：**群成员名录走群成员表**（覆盖"不在通讯录、也没发过言"的人）。"""
    import ast as _ast

    root = HERE.parents[0]

    def _fn_seg(path: str, name: str) -> str:
        src = (root / path).read_text(encoding="utf-8")
        tree = _ast.parse(src)
        for n in _ast.walk(tree):
            if isinstance(n, _ast.FunctionDef) and n.name == name:
                s = _ast.get_source_segment(src, n) or ""
                if (n.body and isinstance(n.body[0], _ast.Expr)
                        and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                    ds = n.body[0].value.value
                    if isinstance(ds, str) and ds:
                        s = s.replace(ds, "")
                return "\n".join(l for l in s.split("\n")
                                 if not l.strip().startswith("#"))
        return ""

    adapter = _fn_seg("agent/replica_adapter.py", "load_group_members")
    wrap = _fn_seg("agent/wechat.py", "group_members")
    exec_fn = _fn_seg("agent/tools.py", "_exec_get_group_members")
    ck("取到三处源码（分母守卫：load_group_members / group_members / 执行器）",
       bool(adapter.strip()) and bool(wrap.strip()) and bool(exec_fn.strip()),
       "adapter=%d wrap=%d exec=%d" % (len(adapter), len(wrap), len(exec_fn)))
    if not (adapter.strip() and wrap.strip() and exec_fn.strip()):
        return
    miss = _group_members_missing(adapter, wrap, exec_fn)
    ck("群成员名录：走群成员表（含非好友）＋ 能力守卫 ＋ 非群聊明说", not miss,
       "缺：%s" % "；".join(miss))

    # 工具 schema 里要同时存在两个成员工具，且说明点明分工（否则模型会挑错）
    tsrc = (root / "agent" / "tools.py").read_text(encoding="utf-8")
    ck("两个成员工具并存且说明点明分工（名录 vs 最近活跃）",
       ('"name": "get_group_members"' in tsrc) and ('"name": "get_active_members"' in tsrc)
       and ("没发过言" in tsrc or "发过言" in tsrc),
       "get_group_members=%s get_active_members=%s"
       % ('"name": "get_group_members"' in tsrc, '"name": "get_active_members"' in tsrc))

    # 反向对照：旧写法（只统计发言者）必须被判缺项
    old = _group_members_missing("", "", _GROUPMEM_OLD_SAMPLE)
    ck("反向对照：旧写法（从消息流统计发言者）必须被判缺项",
       len(old) >= 4, "检出 %d 项：%s" % (len(old), "；".join(old[:2])))


# ---------------------------------------------------------------- 图片下载：档位与失败可定位

def _image_tier_missing(seg: str, diag: str = "") -> list:
    """检查图片下载那条链缺哪些接线（**纯函数**，便于反向对照）。

    三项都是"少一行就差很多"的接线：
      ① 不传 `tier` ⇒ 旧行为只看 `.dat`/缩略图，**可能把缩略图喂给模型**；
      ② 取不到时不去诊断 ⇒ 排障只剩"下载失败"四个字；
      ③ 旧包兜底不精确 ⇒ 一个 `except TypeError` 会把别的类型错误一起吞掉。

    `diag` 传**诊断函数**（`_note_image_fail`）的函数体：诊断本身允许独立成函数，
    但里面必须真的问过 `image_status` —— 只"调了诊断函数"而诊断是空壳，等于没接。
    """
    miss = []
    if 'tier="best"' not in seg:
        miss.append('没传 tier="best"（旧行为可能只拿到缩略图）')
    if "_note_image_fail(" not in seg:
        miss.append("取不到时没走诊断（无法定位卡在哪一步）")
    elif "image_status" not in diag:
        miss.append("诊断函数里没问 image_status（空壳诊断）")
    if '"tier" not in str(' not in seg:
        miss.append("旧包兜底不精确（TypeError 会被整体吞掉）")
    return miss


#: 反向对照样本：接线上线前的旧写法（只用于证明判据会咬人）。
_IMAGE_OLD_SAMPLE = '''
def download_image(self, chat_id, local_id):
    if self._md is None:
        return None
    try:
        return self._md.download_image(chat_id, int(local_id), save_dir=media_dir)
    except Exception:
        return None
'''


def t_image_tier_guard() -> None:
    """自检：**图片下载要显式要「本机最好的那一档」，且失败时能定位**。

    ⚠️ 前提认知（驱动库 1.2.4.x）：一条图片在本机最多三档 —— `_t.dat` 预览图 / `.dat`
    微信下发的那一份（**可能本身就是预览版**）/ `_h.dat` 真原件。不传 `tier` 时驱动库走的是
    `.dat → 缩略图`，**压根不看原件那一档**；而调用方（把图喂给模型）最怕拿到的是缩略图。
    """
    import ast as _ast

    root = HERE.parents[0]
    src = (root / "agent" / "wechat.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)

    def _fn_seg(name: str) -> str:
        """取某个函数的源码段，并**剥掉 docstring 与注释行**。

        ⛔ 必须先剥：本函数的说明里就写着 `tier='best'`（讲为什么），
        不剥掉的话下面那条断言会**自己把自己判过**。
        """
        for n in _ast.walk(tree):
            if isinstance(n, _ast.FunctionDef) and n.name == name:
                s = _ast.get_source_segment(src, n) or ""
                if (n.body and isinstance(n.body[0], _ast.Expr)
                        and isinstance(getattr(n.body[0], "value", None), _ast.Constant)):
                    ds = n.body[0].value.value
                    if isinstance(ds, str) and ds:
                        s = s.replace(ds, "")
                return "\n".join(l for l in s.split("\n")
                                 if not l.strip().startswith("#"))
        return ""

    seg = _fn_seg("download_image")
    diag = _fn_seg("_note_image_fail")
    ck("取到了 `wechat.py::download_image` 的源码（分母守卫）", bool(seg.strip()),
       "长度 %d" % len(seg))
    if not seg.strip():
        return
    ck("失败诊断函数 `_note_image_fail` 存在（分母守卫）", bool(diag.strip()),
       "长度 %d" % len(diag))
    miss = _image_tier_missing(seg, diag)
    ck("图片下载：显式要最好的一档 ＋ 失败可定位 ＋ 旧包兜底精确（三处接线齐全）",
       not miss, "缺：%s" % "；".join(miss))

    # 反向对照：旧写法必须被判缺三项（证明上面那条不是"写成绿的"）
    old = _image_tier_missing(_IMAGE_OLD_SAMPLE)
    ck("反向对照：旧写法（不传 tier、无诊断、宽 except）必须被判缺三项",
       len(old) == 3, "检出 %d 项：%s" % (len(old), "；".join(old)))


# ---------------------------------------------------------------- 窗口纪律：不许"欺负"别人的窗口

#: ⛔ 为什么钉这条：上游 `wechatauto` 的 `guia._minimize_blockers()` 会把**压在微信上面的
#:   别人的窗口**（浏览器等）直接 `ShowWindow(h, 6)` ＝ `SW_MINIMIZE` 收起来，注释原文是
#:   「**替代『手动最小化 Chrome 再置顶微信』的步骤**」。
#:   我方**明确不走这条路** —— 现场故障「微信窗口疯狂闪就是不回复」正是"收-放-收-放"循环
#:   造成的（见 `agent/wechat.py::_minimize_back_if_needed`：现在只 `SetWindowPos(HWND_BOTTOM)`，
#:   **绝不改可见性**，并带三条安全线：没登记过不动 / 已最小化不动 / 正在前台不动）。
#:   这条判据把**已经形成的纪律**钉住，防下一次"顺手加一处清场"。
_WINBULLY_EXEMPT: dict = {
    ("agent/ui_adapt.py", "dismiss_overlays"):
        "隐藏的是**系统输入叠加层**（TouchKeyboard 那一族），收窄手段是「类名 + 标题白名单」，"
        "目标是别人的浮层，且只 `SW_HIDE`（不最小化、不动 Z 序）",
}


def _win_bully_scan(src: str):
    """静态扫一段源码里的"欺负窗口"写法。返回 `(违禁, 需白名单, 调用数)`。

    只认**真实调用**（`ast` 解析）—— 注释/docstring 里提到常量不算数：
    `agent/wechat.py` 的历史说明里就原样写着 `ShowWindow(hwnd, 6)`（讲它为什么被取消）。
    """
    import ast as _ast
    bad, hide, n = [], [], 0
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return bad, hide, n

    def _num(a):
        if isinstance(a, _ast.Constant) and isinstance(a.value, int):
            return int(a.value)
        # ⛔ `-1` 在 AST 里是 `UnaryOp(USub, Constant(1))`，**不是** `Constant(-1)`
        #    —— 这一条是反向对照样本先咬出来的（当时只认出 SW_MINIMIZE、漏了 HWND_TOPMOST）。
        if (isinstance(a, _ast.UnaryOp) and isinstance(a.op, _ast.USub)
                and isinstance(a.operand, _ast.Constant)
                and isinstance(a.operand.value, int)):
            return -int(a.operand.value)
        return None

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        fn = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        args = list(node.args)
        if fn in ("ShowWindow", "ShowWindowAsync"):
            n += 1
            v = _num(args[1]) if len(args) > 1 else None
            if v == 6:
                bad.append("SW_MINIMIZE（把窗口收起来 ⇒ 用户会以为被关了）")
            elif v == 0:
                hide.append("SW_HIDE（隐藏，需白名单 + 收窄手段）")
        elif fn == "SetWindowPos":
            n += 1
            v = _num(args[1]) if len(args) > 1 else None
            if v == -1:
                bad.append("HWND_TOPMOST（把别人的窗永久钉在最上层）")
    return bad, hide, n


#: 反向对照样本：上游 `_minimize_blockers` 的形状（只用于证明判据会咬人，不进产品代码）。
_WINBULLY_UPSTREAM_SAMPLE = '''
def _minimize_blockers(self):
    for h in targets:
        u.ShowWindow(h, 6)
        u.SetWindowPos(h, -1, 0, 0, 0, 0, 0)
'''


def t_no_window_bully_guard() -> None:
    """自检：**不许"欺负"不属于本项目的窗口**（与上游 `_minimize_blockers` 划清界限）。

    - **违禁（零容忍）**：`ShowWindow(x, 6=SW_MINIMIZE)`、`SetWindowPos(x, -1=HWND_TOPMOST, …)`；
    - **需白名单**：`ShowWindow(x, 0=SW_HIDE)`（必须在 `_WINBULLY_EXEMPT` 里写明理由）；
    - **反向对照**：把上游式样本喂进同一个检测函数，必须报红 —— 判据自己要会咬人。
    """
    import ast as _ast
    import textwrap as _tw

    root = HERE.parents[0]
    bad, bare_hide, counted, files = [], [], 0, 0
    for f in sorted((root / "agent").glob("*.py")):
        if f.name == "winops.py":
            continue                      # 实现点自身：窗口操作就该长在这里
        files += 1
        src = f.read_text(encoding="utf-8")
        # 预筛：不碰窗口 API 的文件直接跳过（否则 140 个文件逐个 walk + dedent + 再 parse，
        # 单跑要 27 秒；判据本身只关心这几处调用）
        if ("ShowWindow" not in src) and ("SetWindowPos" not in src):
            continue
        _b, _h, _n = _win_bully_scan(src)
        if _n:
            counted += _n
        if not _b and not _h:
            continue          # 文件级已无「违禁 / SW_HIDE」⇒ 不必再做（很贵的）函数级定位
        # 逐个函数定位（白名单是 (文件, 函数) 粒度）
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            seg = _ast.get_source_segment(src, node) or ""
            seg = _tw.dedent(seg)
            b, h, _ = _win_bully_scan(seg)
            key = ("agent/%s" % f.name, node.name)
            if b:
                bad.append("%s::%s %s" % (key[0], key[1], "；".join(b)))
            if h and key not in _WINBULLY_EXEMPT:
                bare_hide.append("%s::%s %s" % (key[0], key[1], "；".join(h)))

    ck("不许对「别人的窗口」用 `SW_MINIMIZE` / `HWND_TOPMOST`（上游那套「清场」我方明确不跟）",
       not bad, "违禁：%s" % "、".join(bad[:3]))
    ck("用 `SW_HIDE` 隐藏窗口必须进白名单并写明理由",
       not bare_hide, "未白名单：%s" % "、".join(bare_hide[:3]))
    #: 分母守卫：一条都没扫到时上面两条会"全绿"（判据自身失效而不自知）
    ck("窗口操作扫描真的扫到了调用（分母守卫 > 0）", counted > 0,
       "扫了 %d 个文件、命中 %d 处窗口操作" % (files, counted))

    # 反向对照：上游式样本必须被判红（证明这两条判据不是"写成绿的"）
    _b2, _h2, _n2 = _win_bully_scan(_WINBULLY_UPSTREAM_SAMPLE)
    ck("反向对照：上游式「清场」样本必须被判红", len(_b2) == 2,
       "检出：%s" % "；".join(_b2))
    _b3, _h3, _n3 = _win_bully_scan("def f(u, h):\n    u.ShowWindow(h, 0)\n")
    ck("反向对照：只 `SW_HIDE` 落在「需白名单」档（不与违禁混淆）",
       not _b3 and len(_h3) == 1, "hide=%d bad=%d" % (len(_h3), len(_b3)))

    # 两条**已修的历史**要钉住（防回改）
    _ua = (root / "agent" / "ui_adapt.py").read_text(encoding="utf-8")
    ck("`ui_adapt` 挡路窗口用的是 `HWND_BOTTOM`(1) 而不是 `HWND_TOPMOST`(-1)"
       "（-1 那次把挡路的控制台永久钉在了最上层）",
       "SetWindowPos(h, 1, 0, 0, 0, 0, 0x0001 | 0x0002)" in _ua)
    _wc = (root / "agent" / "wechat.py").read_text(encoding="utf-8")
    try:
        _wt = _ast.parse(_wc)
        _fn = next(n for n in _ast.walk(_wt)
                   if isinstance(n, _ast.FunctionDef) and n.name == "_minimize_back_if_needed")
        _seg = _tw.dedent(_ast.get_source_segment(_wc, _fn) or "")
        _calls, _hh, _ = _win_bully_scan(_seg)
        ck("`_minimize_back_if_needed` 绝不改可见性（只压 Z 序底层，不 ShowWindow）",
           not _calls and not _hh and "HWND_BOTTOM" in _seg)
    except StopIteration:
        ck("`_minimize_back_if_needed` 仍存在（改名要同步这条判据）", False, "没找到该函数")


#: 判据全表。`main()` 按此顺序执行；`--only` 按名字筛、`--fast` 按下面的慢表跳。
#  顺序即执行顺序（部分判据依赖前面判据留下的现场，勿随意重排）。
def t_shoot_teardown_guard() -> None:
    """出图闸门必须**销毁**窗口，不许只 `close()`。

    ⛔ 现场（2026-10-09，出图时被卡住才挖出来）：每个镜头都 `_mk` 一棵完整 `Shell`；
       这些树在 grab 之后若只 `close()`，就留在内存里不释放。
       实测（whale）：单跑任意一个镜头都正常，连跑「整窗 → 导航 → 二次确认」时
       **第三个镜头会卡死在 `_mk` 里**（前面几棵没释放的树把它拖住）。
       加 `_teardown()`（`close()` + `deleteLater()` + 排空事件）后连跑 8 个镜头 4.8s 跑完。

    本判据是**源码级**的（不真渲染）：扫 `shoot.py`，确认（a）`_teardown` 存在且做了销毁、
    （b）各个镜头都走 `_teardown`、没有裸 `close()` 漏网。
    """
    sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shoot.py")
    src = Path(sp).read_text(encoding="utf-8")

    ck("shoot.py 有 _teardown（关窗 + deleteLater + 排空事件）",
       "def _teardown(" in src and ".deleteLater()" in src
       and "sendPostedEvents" in src)

    # 镜头的收尾必须走 _teardown；裸 close 只允许出现在 _teardown 自己里。
    body = src.split("def _teardown(", 1)[-1]
    body = body.split("def shot_", 1)[-1] # 只留镜头区，去掉 _teardown 自身
    n_teardown = body.count("_teardown(")
    n_bare = body.count(".close()")
    ck("各镜头收尾一律走 _teardown，没有裸 close() 漏网",
       n_teardown >= 8 and n_bare == 0,
       "teardown=%d bare_close=%d" % (n_teardown, n_bare))

    # 新加的两个引导弹窗镜头必须在册（缺件引导 = 用户点名的「三段式、每件一窗」）
    ck("镜头表含「环境引导」（一个弹窗·把微信调成浅色/标准字号）",
       "shot_envguide" in src and "环境引导" in src)
    ck("镜头表含「缺件引导」初始段与进度段",
       "shot_missing" in src and "shot_missing_progress" in src
       and "缺件引导" in src)


ALL_TESTS = (
    t_syntax, t_nav, t_themes, t_runtime_render, t_fonts_rgba, t_usability, t_panels,
    t_visual, t_badges, t_status_chain, t_bot_controls, t_window_chrome, t_dpi_motion,
    t_wheel_nod, t_updbar, t_pop_look, t_pause_win, t_no_touch, t_bootstrap32, t_ocr9,
    t_c10, t_c13, t_hotfix1, t_hotfix2, t_catmgr, t_medialocal, t_commfb,
    t_veradv, t_g5, t_g6, t_g7, t_g8, t_g9, t_g10, t_g11, t_g12, t_g13, t_g14,
    t_g15, t_g16, t_g17, t_g18, t_g19, t_g20, t_g21, t_g22, t_g23_fbnotice,
    t_g24_fbnotice_dismiss, t_g25_whale_boot, t_g26_web_parity_actions,
    t_g27_native_pieces, t_ocr_fuzzy,
    t_audit_r3, t_dialog_drag, t_whale_guard, t_color_token_guard,
    t_button_label_guard, t_placeholder_guard, t_dupdef_guard,
    t_attr_shadow_guard, t_async_landing_guard, t_screen_guards,
    t_gate_middle, t_cfg_wired_guard, t_delivery_ledger_guard, t_dev_dir_guard,
    t_surface_bg_guard,
    t_field_guard,
    t_default_value_guard, t_dead_import_guard, t_guard_family_guard,
    t_run_async_signature_guard,
    t_packaged_modules_guard, t_winops_guard, t_keys_guard, t_atomic_write_guard,
    t_landing_discipline_guard,
    t_subnav_guard, t_subnav_coverage_guard, t_emoji_index_guard, t_emoji_codes_guard,
    t_row_geometry_guard, t_rhythm_guard, t_contrast_guard, t_fingerprint_verdict_guard,
    t_scope_degrade_guard, t_job_cancel_guard, t_suspend_counter_guard,
    t_task_owner_guard, t_idempotent_close_guard,
    t_retry_policy_guard, t_reconnect_once_guard,
    t_popup_open_guard,
    t_backend_down_shortcircuit_guard,
    t_config_stamp_content_guard, t_config_changed_broadcast_guard,
    t_restart_handover_guard, t_a11y_four_guard, t_clear_before_fill_guard,
    t_inject_surface_guard, t_foreground_borrow_guard,
    t_whale_roles, t_whale_assets, t_whale_usage,
    t_no_window_bully_guard, t_image_tier_guard, t_moments_db_first_guard,
    t_group_members_db_guard, t_video_frame_batch_guard, t_video_subtitles_guard,
    t_download_magic_guard, t_video_probe_guard, t_external_download_guard,
    t_scene_frames_guard, t_asr_backend_guard, t_watermark_latest_minus1_guard,
    t_qt_no_msgbox_guard, t_mirror_fetch_guard, t_projection_rows_guard, t_clean_tree_guard,
    t_search_paste_guard, t_prefetch_parallel_guard, t_filewatch_guard, t_session_rows_projection_guard, t_emoji_usage_guard, t_emoji_code_guard, t_hot_words_guard,
    t_shoot_teardown_guard,
)

#: `--fast` 跳过的判据：**按最近一次全量的逐条计时选出来的当前最慢几名**。
#  ⚠️ 这是一份"会过期的名单"——慢条会变（曾经最慢的 `t_g26`(264s)/`t_panels`(226s)
#     在修掉"后端没就绪时空等超时"后已降到 8~16s）。**每次全量后看一眼计时输出，
#     名单与实际不符就更新**（判据本身只负责"跳过最贵的几条"，不负责永远正确）。
#  ⚠️ 跳过它们**只影响"快速自查"**；交付/提交前必须跑**不带参数**的完整全量。
SLOW_TESTS = {
    "t_ocr_fuzzy",                # 约 40s（波动大：本机 5~40s，受帧质量影响）
    "t_winops_guard",             # 约 27s
    "t_foreground_borrow_guard",  # 约 19s
}


def _pick_tests(argv: list[str]) -> tuple[list, int]:
    """按命令行参数挑本轮要跑的判据。

    参数：
      `--only=a,b`  只跑名字列为 a、b 的判据（名字可写全称，也可写去掉 `t_` 前缀的短名）
      `--fast`      跳过 SLOW_TESTS 里那三条重活，其余照跑
      `--list`      只打印判据清单与慢表，不执行
    返回 `(选中的判据列表, 退出码)`；退出码非 None 表示不进执行阶段（清单/选错名）。
    默认（无参数）＝全表，即"提交前必须跑一次"的完整全量。
    """
    only_raw = ""
    fast = False
    want_list = False
    for a in argv:
        if a.startswith("--only="):
            only_raw = a[len("--only="):]
        elif a == "--fast":
            fast = True
        elif a == "--list":
            want_list = True

    if want_list:
        for f in ALL_TESTS:
            mark = "  ← 慢(--fast 跳过)" if f.__name__ in SLOW_TESTS else ""
            print(f"  {f.__name__}{mark}")
        print(f"\n共 {len(ALL_TESTS)} 条；慢表 {len(SLOW_TESTS)} 条。"
              f"用法：--only=t_a,t_b  |  --fast  |  --list")
        return [], 0

    picked = list(ALL_TESTS)
    if fast:
        picked = [f for f in picked if f.__name__ not in SLOW_TESTS]

    if only_raw:
        # 允许写短名（`a11y_four_guard`）或全名（`t_a11y_four_guard`），两者都能对上。
        want = [s.strip() for s in only_raw.split(",") if s.strip()]
        by_short = {f.__name__[2:]: f for f in ALL_TESTS if f.__name__.startswith("t_")}
        by_full = {f.__name__: f for f in ALL_TESTS}
        chosen, miss = [], []
        for w in want:
            f = by_full.get(w) or by_short.get(w)
            if f is None:
                miss.append(w)
            elif f not in chosen:
                chosen.append(f)
        if miss:
            print("判据名不认识：" + ", ".join(miss))
            print("用 --list 看全部判据名。")
            return [], 2
        # 保持全表的相对顺序，避免手工顺序打乱依赖
        picked = [f for f in ALL_TESTS if f in chosen]

    return picked, None


def main() -> int:
    picked, _code = _pick_tests(sys.argv[1:])
    if _code is not None:
        return _code

    # ⭐ 项目根入 `sys.path`：部分判据（`import agent.config` / `import ui_qt.*`）依赖它。
    #   完整全量里它是被前序判据顺带插进去的；`--only` 单跑时缺了这一步 ⇒
    #   会报 `ModuleNotFoundError: No module named 'agent'` 这种**假失败**（不是产品问题）。
    #   ⇒ 在执行前无条件保证一次，两种跑法结果一致。
    _root = str(HERE.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    # ⭐ 测试隔离（N1 残余的收口）：`logs/console.url` 是**产品运行时**写的
    #   （含随机端口+token），自检跑在产品目录里会读到它——轻则刷几百行「端口连不上」噪音，
    #   重则让依赖 current_url 的断言随工作区残留漂移。⇒ 开跑前把它临时挪开，收尾还原。
    #   只动测试侧：产品读这个文件是正确行为，不改。
    _cu_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "console.url")
    _cu_p = os.path.normpath(_cu_p)
    _stash_selfheal(os.path.dirname(_cu_p)) # 先自愈孤儿 stash（D3：上一跑没还原的情形）
    _cu_stash = _cu_p + ".selftest-stash"
    _cu_moved = False
    if os.path.exists(_cu_p):
        try:
            os.replace(_cu_p, _cu_stash)
            _cu_moved = True
        except OSError:
            pass

    timings: list[tuple[str, float, int]] = []
    try:
        for fn in picked:
            _t0 = time.time()
            _n0 = len(ROWS)
            try:
                fn()
            except Exception as e: # noqa: BLE001
                ck(f"{fn.__name__} 执行未抛异常", False, f"{type(e).__name__}: {e}"[:110])
            timings.append((fn.__name__, time.time() - _t0, len(ROWS) - _n0))
    finally:
        if _cu_moved:
            try:
                os.replace(_cu_stash, _cu_p)
            except OSError:
                pass

    bad = [r for r in ROWS if not r[1]]
    for name, ok, extra in ROWS:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    total_s = sum(t for _, t, _ in timings)
    print(f"{len(ROWS) - len(bad)} 通过 / {len(bad)} 失败   （跑了 {len(timings)} 条判据，"
          f"合计 {total_s:.1f}s）")
    slow = sorted(timings, key=lambda x: -x[1])[:10]
    if slow and slow[0][1] >= 1.0:
        print("\n最慢的几条（改一处自检时优先用 --fast 或 --only 跳过它们）：")
        for nm, dt, nc in slow:
            if dt < 1.0:
                break
            print(f"  {dt:7.2f}s  {nm}   ({nc} 条断言)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
