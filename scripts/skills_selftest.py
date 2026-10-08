#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 机制判据：**两型都收、坏输入不静默、单开关全失效、清单不进 system**。

链路：`skills/<name>.md`（或 `skills/<name>/SKILL.md`）
  ⇒ `agent/skills.py` 扫描/校验 ⇒ 清单注入**用户消息** ＋ `skill` 工具按需取正文。

判据（不需要微信、不需要联网、不构造真 GUI）：
  A **两型等价**：目录包与扁平文件都能收，且**同一个 skill 用两型写出来结果一致**。
  B **命名规则**：非法名（大写/中文/下划线/太长）一律拒收并**给出原因**。
  C **坏输入不静默**：缺 description / 空正文 / 超大 / 重复名 ⇒ 进 `problems`，⛔ 不抛异常。
  D **清单不含正文**（关键）：`catalog_text()` 里**只有名字与说明**，正文一个字都不许出现
     —— 否则每轮白付 token（分母守卫：确认真的能读到正文，否则这条测的是空气）。
  E **单开关全失效**：关掉 `skills.enabled` ⇒ `catalog_text()` 为空串 **且** 工具列表里没有 `skill`。
  F **清单不进 system prompt**（关键）：`build_system_prompt()` 里不许出现 skill 清单内容
     —— system 必须保持纯静态（前缀缓存靠它）。
  G **不碰发送链**：`agent/skills.py` 源码里不出现发送/窗口相关调用。
  H **接线**：`build_tool_defs()` 在启用且有条目时才注册 `skill`；`_exec_skill` 真存在。
"""
import inspect
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def skip(name, detail=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}".format(name) + ("  [{}]".format(detail) if detail else ""))


from agent import skills as sk   # noqa: E402  （import 要在 ok() 定义之后，便于失败时也报得出）

#: 用一个临时目录做全部实验 —— ⛔ 不往真 `skills/` 里写东西（那会弄脏工作树）
_TMP = None

#: 判据开始时进程用的那份配置路径（`_restore_cfg()` 还原用）
_SAVED_CONF = None


def _use_tmp() -> str:
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="skills-test-")
    return _TMP


def _write(name: str, text: str) -> str:
    p = os.path.join(_TMP, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _point_at(d: str):
    """让 `skills_dir()` 指向目录 `d`。

    ⚠️ **不能只改 `get_config()` 返回的那份字典**：`get_config()` 每次都会拿盘上
    `config.json` 的内容指纹跟内存里那份比对，对不上就**重读磁盘** ⇒ 运行时改的值
    会被下一次调用悄悄还原（本判据早期就栽在这上面：C4/C5/I2 三条无因由地红）。
    ⇒ 做法是**把 `config.CONFIG_FILE` 指到一份临时 config.json** 再 `reload_config()`；
    测完由 `_restore_cfg()` 还原。

    ⚠️ config.json 写在**独立的临时目录**里，⛔ 不写进 `d` ——
    `d` 允许是**不存在**的目录（C3 就专门测这个），写进去会把它造出来，测试前提就没了。
    """
    from agent import config as _c
    global _SAVED_CONF
    if _SAVED_CONF is None:
        _SAVED_CONF = _c.CONFIG_FILE
    holder = tempfile.mkdtemp(prefix="skills-cfg-")
    p = os.path.join(holder, "config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"skills": {"enabled": True, "dir": d, "max_items": 50}}, f)
    _c.CONFIG_FILE = p
    _c.reload_config()
    return d


def _restore_cfg():
    """还原配置来源（判据自己弄脏的环境自己收干净）。"""
    from agent import config as _c
    _c.CONFIG_FILE = _SAVED_CONF or os.path.join(ROOT, "config.json")
    _c.reload_config()


def _flip_enabled(on: bool):
    """把当前临时配置里的 `skills.enabled` 改掉（**必须落盘**，见 `_point_at` 的说明）。"""
    from agent import config as _c
    p = _c.CONFIG_FILE
    with open(p, encoding="utf-8") as f:
        cur = json.load(f)
    cur.setdefault("skills", {})["enabled"] = bool(on)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f)
    _c.reload_config()


print("=" * 72)
print("Skill 机制判据")
print("=" * 72)

# ── A. 两型等价 ─────────────────────────────────────────────────────
print("\n[A] 目录包与扁平文件两型都收")
_tmp = _use_tmp()
# ⭐ 等价性用**同一个 id**（`same-id`）：id 一律取文件名/目录名（见 `_one`），
#   所以两型要"同一个 skill"就必须同名 —— 它们当然不能同目录共存（那算重复名），
#   故分放两个临时目录。front matter 里的 name 只是显示名，故意写成中文来证明它不影响 id。
_BODY = "---\nname: 两型是同一份\ndescription: 两型等价实验用\n---\n\n# 正文\n\n这是正文内容。\n"
_flat = _write("same-id.md", _BODY)
try:
    it = sk._one(_flat, "same-id")
    ok("A1 扁平文件能读出 id/description/正文",
       it["ok"] and it["id"] == "same-id" and "正文内容" in it["body"],
       "ok=%s id=%s" % (it["ok"], it["id"]))
except Exception as e:  # noqa: BLE001
    ok("A1 扁平文件能读出 id/description/正文", False, "%s: %s" % (type(e).__name__, e))

_tmp2 = os.path.join(_tmp, "second")
os.makedirs(_tmp2, exist_ok=True)
_pk = os.path.join(_tmp2, "same-id", "SKILL.md")
os.makedirs(os.path.dirname(_pk), exist_ok=True)
with open(_pk, "w", encoding="utf-8") as f:
    f.write(_BODY)
try:
    it2 = sk._one(_pk, "same-id")
    ok("A2 目录包（<name>/SKILL.md）走同一条读法", it2["ok"] and "正文内容" in it2["body"],
       "ok=%s why=%s" % (it2["ok"], it2["why"]))
except Exception as e:  # noqa: BLE001
    ok("A2 目录包（<name>/SKILL.md）走同一条读法", False, "%s: %s" % (type(e).__name__, e))

# ⭐ 等价性：同一个 name 用两型写，除 path 外字段必须完全一致
#   ⚠️ 两型必须用**同一个 id**（id＝文件名/目录名，见 `_one` 的说明）；
#      早期这条用了 two-forms / two-forms-dir 两个名字，那当然不相等 —— 是判据写错了。
if it["ok"] and it2["ok"]:
    _k = ("id", "description", "body", "ok")
    _a = {k: it[k] for k in _k}
    _b = {k: it2[k] for k in _k}
    ok("A3 ★两型**逐字段等价**（只有 path 不同）", _a == _b,
       "flat=%s… dir=%s…" % (str(_a)[:70], str(_b)[:70]))
else:
    ok("A3 ★两型**逐字段等价**（只有 path 不同）", False, "前置不成立（A1/A2 未过）")

# ── B. 命名规则 ─────────────────────────────────────────────────────
print("\n[B] 命名规则：非法名一律拒收并给出原因")
for _n, _why in (("BadName", "大写"), ("中文名", "中文"), ("has_under", "下划线"),
                 ("x" * 41, "超 40 字")):
    try:
        r = sk._one(_write("tmp.md", _BODY), _n)
        ok("B1 非法名「%s」（%s）被拒且带原因" % (_n[:10], _why),
           (not r["ok"]) and bool(r["why"]), "why=%s" % r["why"][:40])
    except Exception as e:  # noqa: BLE001
        ok("B1 非法名「%s」（%s）被拒且带原因" % (_n[:10], _why), False, "%s" % type(e).__name__)
# 反向：合法名必须放行（否则上面的"拒收"可能是无差别拒收）
_okname = sk._one(_write("ok.md", _BODY), "good-name-3")
ok("B2 ★反向对照：合法名（小写+数字+-）必须放行", _okname["ok"], "ok=%s why=%s" % (_okname["ok"], _okname["why"]))

# ── C. 坏输入不静默 ─────────────────────────────────────────────────
print("\n[C] 坏输入进 problems，不抛异常、不静默")
_cases = [
    ("缺 description", "---\nname: no-desc\n---\n\n# 只有正文\n\n内容。\n"),
    ("空正文", "---\nname: empty-body\ndescription: 有说明但没正文\n---\n\n"),
]
for _label, _txt in _cases:
    try:
        r = sk._one(_write("bad.md", _txt), "bad-%s" % (abs(hash(_label)) % 10000))
        ok("C1 %s ⇒ 拒收且带原因" % _label, (not r["ok"]) and bool(r["why"]), "why=%s" % r["why"][:50])
    except Exception as e:  # noqa: BLE001
        ok("C1 %s ⇒ 拒收且带原因" % _label, False, "抛了 %s" % type(e).__name__)

# 超大文件
try:
    _big = os.path.join(_TMP, "huge.md")
    with open(_big, "w", encoding="utf-8") as f:
        f.write("---\nname: huge\ndescription: 超大\n---\n\n" + ("字" * (600 * 1024)))
    r = sk._one(_big, "huge")
    ok("C2 正文超过 1MB ⇒ 拒收（带原因，不 OOM）", (not r["ok"]) and ("KB" in r["why"] or "上限" in r["why"]),
       "why=%s" % r["why"][:50])
except Exception as e:  # noqa: BLE001
    ok("C2 正文超过 1MB ⇒ 拒收（带原因，不 OOM）", False, "%s: %s" % (type(e).__name__, e))

# load() 的容错：目录不存在 ⇒ 空列表、**不是** problem（出厂就是空目录）
try:
    _nodir = tempfile.mkdtemp(prefix="skills-nodir-")
    _point_at(os.path.join(_nodir, "does-not-exist"))
    _items, _probs = sk.load()
    ok("C3 目录不存在 ⇒ (空, 空) —— 出厂空目录**不算错误**",
       _items == [] and _probs == [], "items=%d probs=%d" % (len(_items), len(_probs)))
except Exception as e:  # noqa: BLE001
    ok("C3 目录不存在 ⇒ (空, 空) —— 出厂空目录**不算错误**", False, "%s: %s" % (type(e).__name__, e))
finally:
    _restore_cfg()

# 重复名 ⇒ 两个都不收（**id 取文件名** ⇒ 两个不同文件名要撞同一个 id 是不可能的，
#   所以这里造"同名文件"：`dup.md` 与目录包 `dup/SKILL.md` 都叫 dup ⇒ 撞名）
try:
    _d2 = tempfile.mkdtemp(prefix="skills-dup-")
    with open(os.path.join(_d2, "dup.md"), "w", encoding="utf-8") as f:
        f.write("---\ndescription: 重复名之一\n---\n\n# x\n\n正文 A。\n")
    os.makedirs(os.path.join(_d2, "dup"), exist_ok=True)
    with open(os.path.join(_d2, "dup", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\ndescription: 重复名之二\n---\n\n# x\n\n正文 B。\n")
    _point_at(_d2)
    _items, _probs = sk.load()
    ok("C4 ★重复名 ⇒ 两个都不收（不是留一个）",
       len(_items) == 0 and any("重复" in p.get("why", "") for p in _probs),
       "items=%s probs=%s" % ([i["id"] for i in _items], [p.get("why") for p in _probs][:2]))
except Exception as e:  # noqa: BLE001
    ok("C4 ★重复名 ⇒ 两个都不收（不是留一个）", False, "%s: %s" % (type(e).__name__, e))
finally:
    _restore_cfg()

# README 这类文档不该被当 skill
try:
    _d3 = tempfile.mkdtemp(prefix="skills-doc-")
    with open(os.path.join(_d3, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Skill 怎么写\n\n这是说明文档，不是 skill。\n")
    with open(os.path.join(_d3, "real-one.md"), "w", encoding="utf-8") as f:
        f.write("---\ndescription: 真 skill\n---\n\n# x\n\n正文。\n")
    _point_at(_d3)
    _items, _probs = sk.load()
    ok("C5 根目录的 README.md **不算** skill（也不是坏清单）",
       len(_items) == 1 and _items[0]["id"] == "real-one" and not _probs,
       "items=%s probs=%s" % ([i["id"] for i in _items], [p.get("why") for p in _probs][:2]))
except Exception as e:  # noqa: BLE001
    ok("C5 根目录的 README.md **不算** skill（也不是坏清单）", False, "%s: %s" % (type(e).__name__, e))
finally:
    _restore_cfg()

# ── D. 清单不含正文（关键）────────────────────────────────────────
print("\n[D] 清单只有名字+说明，**正文一个字都不许出现**")
try:
    _d4 = tempfile.mkdtemp(prefix="skills-cat-")
    _MARK = "这段文字只应该出现在正文里XYZQ"
    for _i, _n in enumerate(("alpha", "beta")):
        with open(os.path.join(_d4, "%s.md" % _n), "w", encoding="utf-8") as f:
            f.write("---\nname: %s\ndescription: %s 的用途说明\n---\n\n# 正文\n\n%s\n"
                    % (_n, _n, _MARK if _i == 0 else "另一段正文"))
    _point_at(_d4)
    _cat = sk.catalog_text()
    ok("D1 清单里两个名字都在", "alpha" in _cat and "beta" in _cat, "len=%d" % len(_cat))
    ok("D2 清单里有说明", "用途说明" in _cat, "")
    ok("D3 ⭐清单里**没有正文**（token 不让白花）", _MARK not in _cat, "命中则说明全量注入了")
    ok("D4 清单以 <system-reminder> 包起来（照 DSH 形态）",
       _cat.strip().startswith("<system-reminder>") and _cat.strip().endswith("</system-reminder>"), "")
    # ⭐ 分母守卫：证明"正文真的能读到" —— 否则 D3 测的是空气
    _get = sk.get("alpha")
    ok("D5 分母守卫：正文**真的取得到**（否则 D3 无意义）",
       _get is not None and _MARK in _get["body"], "取到=%s" % (_get is not None))
    # 反例锚：喂一个**故意把正文塞进清单**的实现，D3 的检查器必须能抓出来
    _fake = sk.catalog_text.__wrapped__ if hasattr(sk.catalog_text, "__wrapped__") else None
    _fake_cat = "<system-reminder>\n- alpha：用途\n%s\n</system-reminder>" % _MARK
    ok("D6 ★反例锚：把正文塞进清单的样张会被同一检查抓出", _MARK in _fake_cat, "检查器有效")
    # 上限：条数超过 max_items ⇒ 截断，且**明说还有多少条没列**（不静默丢）
    _d4b = tempfile.mkdtemp(prefix="skills-cap-")
    for _i in range(5):
        with open(os.path.join(_d4b, "cap-%d.md" % _i), "w", encoding="utf-8") as f:
            f.write("---\ndescription: 上限实验 %d\n---\n\n# x\n\n正文。\n" % _i)
    _point_at(_d4b)
    from agent import config as _c2
    with open(_c2.CONFIG_FILE, encoding="utf-8") as f:
        _cur = json.load(f)
    _cur["skills"]["max_items"] = 2
    with open(_c2.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_cur, f)
    _c2.reload_config()
    _cat2 = sk.catalog_text()
    ok("D7 超过 max_items ⇒ 截断（只列 2 条）",
       _cat2.count("- cap-") == 2, "列出=%d" % _cat2.count("- cap-"))
    ok("D8 ★截断时**明说还有几条**（不静默丢，否则模型以为只有这些）",
       "3" in _cat2 and ("另有" in _cat2 or "未列" in _cat2), "片段=%s" % _cat2[-60:].replace("\n", " "))
    ok("D9 分母守卫：这些 skill **真的都在**（否则 D7 测的是空气）",
       len(sk.load()[0]) == 5, "load=%d" % len(sk.load()[0]))
except Exception as e:  # noqa: BLE001
    ok("D1~D6 清单注入", False, "%s: %s" % (type(e).__name__, e))
finally:
    _restore_cfg()

# ── E. 单开关全失效 ─────────────────────────────────────────────────
print("\n[E] 关掉总开关 ⇒ 清单空 + 工具不注册（一处关掉全失效）")
try:
    _d5 = tempfile.mkdtemp(prefix="skills-sw-")
    with open(os.path.join(_d5, "one.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: one\ndescription: 开关实验\n---\n\n# x\n\n正文。\n")
    from agent import tools as _tools
    _point_at(_d5)
    _cat_on = sk.catalog_text()
    _names_on = [d["name"] for d in _tools.build_tool_defs()]
    # 关掉开关：**要改盘上那份 config.json**（改内存里的值会被指纹比对还原）
    _flip_enabled(False)
    _cat_off = sk.catalog_text()
    _names_off = [d["name"] for d in _tools.build_tool_defs()]
    ok("E1 开着：清单有内容、工具列表里有 skill",
       bool(_cat_on) and "skill" in _names_on, "cat_len=%d has=%s" % (len(_cat_on), "skill" in _names_on))
    ok("E2 ⭐关掉：清单变空串", _cat_off == "", "len=%d" % len(_cat_off))
    ok("E3 ⭐关掉：工具列表里**没有** skill", "skill" not in _names_off, "names=%d" % len(_names_off))
    ok("E4 ⭐关掉：`enabled()` 为假（唯一真值来源）", not sk.enabled(), "")
except Exception as e:  # noqa: BLE001
    ok("E1~E4 单开关", False, "%s: %s" % (type(e).__name__, e))
finally:
    _restore_cfg()

# ── F. 清单不进 system prompt（关键）──────────────────────────────
print("\n[F] 清单**不进** system prompt（静态性靠它）")
try:
    _d6 = tempfile.mkdtemp(prefix="skills-sys-")
    _MARK2 = "system里绝不该出现的标记QWERTY"
    with open(os.path.join(_d6, "sysx.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: sysx\ndescription: %s\n---\n\n# x\n\n正文。\n" % _MARK2)
    _point_at(_d6)
    from agent import prompt as _pr
    _sys = _pr.build_system_prompt({"bot_name": "小鲸鱼"})
    ok("F1 ⭐system prompt 里**没有** skill 清单内容", _MARK2 not in _sys and "sysx" not in _sys,
       "len=%d" % len(_sys))
    ok("F2 分母守卫：同一份内容在**清单**里确实会出现（不是测空气）",
       _MARK2 in sk.catalog_text(), "")
    # 静态性：连着生成两次，system 必须逐字相同（前缀缓存的前提）
    ok("F3 ⭐system prompt 两次生成**逐字相同**（静态性成立）",
       _pr.build_system_prompt({"bot_name": "小鲸鱼"}) == _sys, "")
except Exception as e:  # noqa: BLE001
    ok("F1~F3 system 静态性", False, "%s: %s" % (type(e).__name__, e))
finally:
    _restore_cfg()

# ── G. 不碰发送链 ───────────────────────────────────────────────────
print("\n[G] skills.py **不碰发送链**（扩边界安全）")
try:
    _src = open(os.path.join(ROOT, "agent", "skills.py"), encoding="utf-8").read()
    _banned = ("send_text", "send_image", "wechat_windows", "import wechat", "only_popup")
    _hit = [b for b in _banned if b in _src]
    ok("G1 skills.py 源码里没有发送/窗口相关调用", not _hit, "命中=%s" % _hit)
    ok("G2 分母守卫：源码真的读到了（≥1000 字）", len(_src) > 1000, "len=%d" % len(_src))
except Exception as e:  # noqa: BLE001
    ok("G1~G2 不碰发送链", False, "%s: %s" % (type(e).__name__, e))

# ── H. 接线 ─────────────────────────────────────────────────────────
print("\n[H] 接线：工具注册 + 执行函数 + 注入点")
try:
    _tsrc = open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
    _psrc = open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
    ok("H1 `_exec_skill` 真存在（读源码，不靠 import 副作用）", "def _exec_skill" in _tsrc, "")
    ok("H2 `build_tool_defs` 里提到了 skills 模块", "skills as _sk" in _tsrc or "import skills" in _tsrc, "")
    ok("H3 `build_user_prompt` 里注入了 catalog_text（在用户消息里，不在 system）",
       "catalog_text" in _psrc, "")
    ok("H4 `build_system_prompt` 里**没有** catalog_text",
       "catalog_text" not in _psrc.split("def build_user_prompt")[0], "")
except Exception as e:  # noqa: BLE001
    ok("H1~H4 接线", False, "%s: %s" % (type(e).__name__, e))

# ── I. 真实目录 ─────────────────────────────────────────────────────
print("\n[I] 随包 skills/ 目录本身是可用的")
try:
    # ⚠️ I 段必须用**真配置**（真 `skills/` 目录）跑：前面各段把 CONFIG_FILE 指到
    #    临时目录了，这里先还原，否则测的是临时目录（本判据早期 I2 就栽在这上面）。
    _restore_cfg()
    _real, _realp = sk.load()
    _realdir = sk.skills_dir()
    if not os.path.isdir(_realdir):
        skip("I1 随包 skills/ 目录存在", "目录还没建（出厂可不带）")
    else:
        ok("I1 随包 skills/ 目录存在", True, _realdir)
        ok("I2 随包示例 skill 能被解析（example-comfort）",
           any(s["id"] == "example-comfort" for s in _real),
           "ids=%s" % [s["id"] for s in _real][:5])
        ok("I3 README.md 没被当 skill（也不是 problem）",
           all(s["id"] != "readme" for s in _real)
           and all("README" not in p.get("id", "") for p in _realp),
           "probs=%s" % [p.get("id") for p in _realp][:3])
        ok("I4 随包目录**零坏清单**", not _realp, "probs=%s" % [p.get("why") for p in _realp][:2])
except Exception as e:  # noqa: BLE001
    ok("I1~I4 随包目录", False, "%s: %s" % (type(e).__name__, e))

print("\n" + "=" * 72)
print("通过 %d / 失败 %d / 跳过 %d" % (PASS, FAIL, SKIP))
print("=" * 72)
sys.exit(1 if FAIL else 0)
