# -*- coding: utf-8 -*-
"""self 成员存在性闸门：**`self.X` 用到/调用的名字，必须真的定义过**。

为什么要有它（一次真实的静默失败）
--------------------------------
搜索切会话主路那次改动的落盘是**半成品**：调用点 `self._try_search_first(...)` 写进去了，
**方法本身没写进文件**。于是走到那一行就是 `AttributeError`，而它被调用方那条宽 `except`
吞掉 ⇒ 现场表现只是"切会话没成"，**报错文本一个字都不留**。
⇒ 当时的判据链**一条都没抓到**（全绿），因为没有任何一条在问"这个被调用的方法存在吗"。

本闸门就是补这一条：把 `agent/` 里所有**本地类**（没有外部基类的那种）的 `self.<名字>`
读出来，逐个核对它在
  ① 本类里定义（方法 / 类属性 / 注解 / `__slots__` 条目）、或
  ② 在本类里被 `self.X = ...` 赋值、或
  ③ 在**全库任意类**里定义过（容忍跨模块 mixin 式复用）
三者之一出现；否则**报红**并列出 `文件:类 self.名字 行号`。

口径与边界（写在代码里，别靠记忆）
--------------------------------
· 扫描面 = `agent/*.py`（`__pycache__` 由 `*.py` 天然排除）；
· **不扫**有外部基类的类（`ctypes.Structure` / `Exception` / `http.client.*` 等）：
  它们的成员来自基类，静态看不见 ⇒ 强行扫会造一堆假红（实测 39 个这样的类）；
  其中 `WeChatAdapter` **本来就没有基类**，是本闸门最主要的目标 —— 所以另立一条断言
  钉住"它没有基类"，一旦有人给它加基类，本闸门的覆盖面会缩水 ⇒ 当场发现。
· 类里出现 `setattr(self, ...)` 的一律**跳过**（动态成员静态看不见）：当前实测 **0 个**，
  用 `EXEMPT_DYNAMIC_MAX` 钉住"这个豁免面不许长"。
· 判据**只看 AST 真实调用/属性**，注释与 docstring 天然不算（AST 不含注释）。
· 反向对照两条：① 拿一段"调不存在的方法"的合成类喂同一个分析函数 ⇒ 必须报出来；
  ② 把真文件里 `_try_search_first` 的 **def 行删掉**、再跑一遍 ⇒ 必须报出来
  （＝直接证明"当时那件事"放到今天会被拦住）。
"""
import ast
import glob
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _iso14  # noqa: E402
import _srcmatch as _sm  # noqa: E402 空白容忍的源码断言（新写源码锚一律用它）
_iso14.all_()  # 判据不许写产品的 data/ 与 logs/（本判据只扫源码，同样不给"我写了盘"的豁免）

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


#: 动态成员豁免（`setattr(self, ...)`）的上限
EXEMPT_DYNAMIC_MAX = 0


def _is_external_base(base, local_classes) -> bool:
    """基类是不是"静态看不到成员"的那种（外部类）。"""
    if isinstance(base, ast.Name):
        return base.id not in local_classes and base.id != "object"
    if isinstance(base, ast.Attribute):
        return True  # `ctypes.Structure` / `_ct.Structure` / `http.client.X`
    return True      # 其它形态（下标/调用）一律当外部，宁可不扫也不造假红


def _collect_class_names(tree):
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _class_defined_names(cls) -> set:
    """类体里"静态可见"的成员名：方法 / 赋值 / 注解 / `__slots__` 条目。"""
    out = set()
    for b in cls.body:
        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(b.name)
        elif isinstance(b, ast.Assign):
            for tg in b.targets:
                if isinstance(tg, ast.Name):
                    out.add(tg.id)
        elif isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
            out.add(b.target.id)
    # `__slots__ = (...)` ⇒ 条目就是成员名（否则 `setattr(self, k, ...)` 那种容器会被误判）
    try:
        for b in cls.body:
            if isinstance(b, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "__slots__" for t in b.targets):
                for el in ast.walk(b.value):
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        out.add(el.value)
    except Exception:
        pass
    return out


def _has_slots(cls) -> bool:
    """类里有没有 `__slots__`（有 ⇒ 成员名可枚举，`setattr(self, k, ...)` 也拦得住）。"""
    for b in cls.body:
        if isinstance(b, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__slots__" for t in b.targets):
            return True
    return False


def analyze(src: str):
    """→ `(扫描的类数, self 读取次数, 违规列表, 外部基类类列表, 动态类列表)`。"""
    tree = ast.parse(src)
    local = _collect_class_names(tree)
    # 全库视角由调用方并进来；这里只用本文件内的类（单文件调用时够用）
    global_names = set()
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        global_names |= _class_defined_names(cls)
    n_cls, n_self = 0, 0
    bad, ext, dyn = [], [], []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        if any(_is_external_base(b, local) for b in cls.bases):
            ext.append(cls.name)
            continue
        _seg = ast.get_source_segment(src, cls, padded=False) or ""
        # ⚠️ 只有"动态成员"才豁免，而且**有 `__slots__` 就不豁免**（成员名可枚举，照查）
        if ("setattr(self" in _seg.replace("\n", "")) and not _has_slots(cls):
            dyn.append(cls.name)
            continue
        n_cls += 1
        defined = _class_defined_names(cls)
        assigned = set()
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self" and isinstance(n.ctx, (ast.Store, ast.Del))):
                assigned.add(n.attr)
        known = defined | assigned | global_names
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self" and isinstance(n.ctx, ast.Load)):
                n_self += 1
                if n.attr not in known:
                    bad.append((cls.name, n.attr, n.lineno))
    return n_cls, n_self, bad, ext, dyn


# ── 扫描 agent/ 全库（全库成员名并成一张"已知"表，容忍跨模块复用）────────────────
FILES = sorted(glob.glob(os.path.join(ROOT, "agent", "*.py")))
TREES, PARSE_FAIL = {}, []
for _p in FILES:
    try:
        TREES[_p] = io.open(_p, encoding="utf-8", errors="ignore").read()
    except Exception as _e:
        PARSE_FAIL.append("%s: %s" % (os.path.basename(_p), _e))

#: 全库"任意类定义过"的名字（跨模块 mixin / 基类在本仓库的兄弟类里）
ALL_DEFINED = set()
for _src in TREES.values():
    try:
        _t = ast.parse(_src)
    except Exception:
        continue
    for _c in [n for n in ast.walk(_t) if isinstance(n, ast.ClassDef)]:
        ALL_DEFINED |= _class_defined_names(_c)

n_cls_total, n_self_total = 0, 0
#: 违规项一律用**元组**携带（`文件, 类, 成员名, 行号`），最后才拼成人看的字符串 ——
#  拼字符串再回切既易错、又会写成"源码脆断言"（见判据纪律）
BAD, EXT_CLASSES, DYN_CLASSES = [], [], []
for _p, _src in TREES.items():
    try:
        _nc, _ns, _bad, _ext, _dyn = analyze(_src)
    except SyntaxError as _e:
        PARSE_FAIL.append("%s: %s" % (os.path.basename(_p), _e))
        continue
    n_cls_total += _nc
    n_self_total += _ns
    BAD += [(os.path.basename(_p), _cls, _attr, _ln) for _cls, _attr, _ln in _bad]
    EXT_CLASSES += ["%s:%s" % (os.path.basename(_p), c) for c in _ext]
    DYN_CLASSES += ["%s:%s" % (os.path.basename(_p), c) for c in _dyn]

# 规则 ③：把"只在本文件里找不到、但全库别处定义过"的从违规里摘掉（跨模块复用）
BAD = [b for b in BAD if b[2] not in ALL_DEFINED]
_BAD_TXT = ["%s:%s self.%s 行 %d" % b for b in BAD]

ok("① 分母守卫：真的扫到了成规模的类与 `self.<名字>` 读取（否则下面那条测的是空气）",
   len(FILES) >= 100 and n_cls_total >= 20 and n_self_total >= 1500,
   "文件 %d · 类 %d · self 读取 %d" % (len(FILES), n_cls_total, n_self_total))
ok("①b 所有 `agent/*.py` 都解析成功（有语法错就说明这批文件根本没被扫到）",
   not PARSE_FAIL, "; ".join(PARSE_FAIL[:3]))
ok("② ⛔ `self.<名字>` 必须真的定义过 / 赋值过（调用不存在的方法会被宽 except 吞成静默失败）",
   not BAD, "; ".join(_BAD_TXT[:5]))
ok("③ 动态成员豁免面不许长（`setattr(self, ...)` 会让本闸门看不见 ⇒ 上限钉死）",
   len(DYN_CLASSES) <= EXEMPT_DYNAMIC_MAX, "%d 个：%s" % (len(DYN_CLASSES), DYN_CLASSES[:3]))
_WECHAT_SRC = TREES.get(os.path.join(ROOT, "agent", "wechat.py"), "x = 1")
ok("④ 目标类 `WeChatAdapter` **没有基类** ⇒ 它一定落在本闸门的扫描面内（加了基类就会缩水）",
   bool(TREES) and not any(
       isinstance(c, ast.ClassDef) and c.name == "WeChatAdapter" and bool(c.bases)
       for c in ast.walk(ast.parse(_WECHAT_SRC))))
ok("⑤「调用点写的那个方法真的在文件里」有独立锚：`_try_search_first` 既被调用、也有定义",
   _sm.has(_WECHAT_SRC, "self._try_search_first(") and _sm.has(_WECHAT_SRC, "def _try_search_first("))

# ── 反向对照 ①：合成一个"调不存在的方法"的类 ⇒ 必须报出来 ─────────────────────
_SEED = (
    "class Demo:\n"
    "    def __init__(self):\n"
    "        self._cfg = {}\n"
    "    def run(self):\n"
    "        return self._not_defined_anywhere(1)\n"
)
_nc, _ns, _bad, _ext, _dyn = analyze(_SEED)
ok("⑥ 反向对照：合成类里调一个**不存在**的方法 ⇒ 同一个分析函数必须报出来",
   len(_bad) == 1 and _bad[0][1] == "_not_defined_anywhere", str(_bad))
ok("⑥b 反向对照的分母：合成类被真的扫到了（类 1 个、self 读取 ≥1 次）",
   _nc == 1 and _ns >= 1, "类=%d self=%d" % (_nc, _ns))

# ── 反向对照 ②：把真文件里 `_try_search_first` 的 def 行删掉 ⇒ 必须报出来 ────────
_wp = os.path.join(ROOT, "agent", "wechat.py")
_w_src = TREES.get(_wp, "")
#: 先量一次**完好**文件的规模，做对照的分母（只跟它比，避免拿半个仓库的阈值去卡一个文件）
_nc_ok, _ns_ok, _bad_ok, _ext_ok, _dyn_ok = analyze(_w_src)
# 抹掉那个 def 行（用正则匹配，不用"整段源码字面量 in 行"那种脆写法）
import re as _re  # noqa: E402
_DEF_LINE = _re.compile(r"def\s+_try_search_first\s*\(")
_broken = "\n".join(l for l in _w_src.split("\n") if not _DEF_LINE.search(l))
_nc2, _ns2, _bad2, _ext2, _dyn2 = analyze(_broken)
_hit2 = ["%s self.%s 行 %d" % b for b in _bad2 if b[1] == "_try_search_first"]
ok("⑦ 反向对照（**真实事故重演**）：抹掉 `_try_search_first` 的 def ⇒ 判据当场报出来",
   bool(_hit2), "; ".join(_hit2[:2]))
ok("⑦b 这次对照的分母：抹掉后仍然扫到同样规模的类与 self 读取（不是把文件弄成扫不动）",
   _nc2 == _nc_ok and _ns2 >= _ns_ok * 0.9,
   "类 %d→%d · self %d→%d" % (_nc_ok, _nc2, _ns_ok, _ns2))
ok("⑦c 对照的**阴性侧**：完好文件里不许报出 `_try_search_first`（否则 ⑦ 里那次被抓到就没意义）",
   not [b for b in _bad_ok if b[1] == "_try_search_first"], str(_bad_ok[:2]))

print("\n结论：%d 通过 / %d 失败（外部基类豁免 %d 个类，仅报告不判负）"
      % (PASS[0], FAIL[0], len(EXT_CLASSES)))
sys.exit(1 if FAIL[0] else 0)
