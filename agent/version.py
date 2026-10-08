# -*- coding: utf-8 -*-
"""群相版本号 —— **唯一来源**。

为什么单开一个文件：清单（`base.version`）、控制台"当前版本"、更新检查、发布脚本
都从这一处取，避免多处写死对不上（启动器侧踩过"版本散落会打架"）。

⛔ **版本体系（现行）**：对外版本名用 **`V` + 卢恩字母** 的形式
（当前 `Vᛞ`）。符号代表数字几由 **`RUNE_VALUES` 这张人手写的对照表**指定
（`ᛞ` → 4），**不按卢恩表位次自动取**。这样版本号仍是**单调递增的整数序列**，
更新检查那套"比大小"照旧成立，而用户/公告/Release 看到的是这个有辨识度的名字。

  ⚠️ 序号**不是**"卢恩表里的第几个"，也**不是**"我发到第几个版本" —— 是表里给它赋的值。
  要发下一版就在 `RUNE_VALUES` 里**加一行**（值比上一版大即可），版本号写 `V` + 那枚符号。

旧体系（`YYYY.M.D.N` 日期式）是**换轨之前**用的；`vtuple()` 仍**兼容**解析它
（老用户手里的版本号是日期式，不能因为换轨就让他那边比不出大小）。

**为什么每次都要动版本号**
更新检查只比版本号 ⇒ 同版本换包对已装用户永远静默 ⇒ 修一个 bug 也要发一个版本号。
"""

VERSION = 'Vᛞ'

#: **符号 → 值** 的显式对照表（版本号里那枚符号代表数字几，**由人指定**）。
#
# ⛔ 为什么不按"卢恩表的第几位"自动取数：
#   ① 版本号里的符号是**给这个项目用的代号**，不是学术转写 —— 它该叫几号由我们定；
#   ② 自动按表取数会**把内部序号泄露在版本号里**（谁去查一下卢恩表就知道我们发到第几版了）；
#   ③ 自动取数还要求"表里的顺序恰好等于我想要的版本顺序"，这本身是个没必要的约束。
#   ⇒ 改成人手写：**加一行就是加了新版本**，值随便挑（只要比上一版大）。
#
# 用法：版本号写成 `V` + 表里任一符号；取值时查这张表。
#   当前：`ᛞ` → 4
RUNE_VALUES = {
    "ᛞ": 4,      # 当前版本所用符号（Vᛞ ＝ 第 4 版）
    # 下一版在这里加一行，例如：
    # "ᛟ": 5,
    # "ᚠ": 6,
}


def rune_value(ch: str) -> int:
    """符号 → 它被赋予的值；**没赋过值的返回 0**（＝"认不出"，绝不瞎猜一个数出来）。

    ⛔ 返回 0 表示"版本号里这枚符号还没登记" ⇒ 调用方必须**如实说比不出来**，
      不许拿它当 0 参与大小比较（那会让新版本被判成最旧）。
    """
    return int(RUNE_VALUES.get(str(ch or ""), 0) or 0)
# ⚡ **内容指纹**（打包时由 `scripts/pack_online.py` 写进来；开发树里留空）。
#
# 为什么需要：
#   更新检查**只比版本号字符串** ⇒ **同一个版本号换包，对已装用户永远静默**（他那边
#   `theirs == mine` ⇒ 直接判"已是最新"）。有了指纹：清单里带 `base.build`，用户侧一比就能看出
#   "版本号没变、但包的内容变了" ⇒ 照常提示更新。
# ⚠️ **它对已经在跑的老版无效**（老版没有这段代码，只认版本号）⇒ 老用户只能靠**版本号前进**触达；
#   这正是"修一个 bug 就发一个版本号"那条口径的由来（见上面 docstring）。
BUILD = '93a1336719d2'

import hashlib as _hashlib # noqa: E402
import os as _os # noqa: E402
import re as _re # noqa: E402
import logging

log = logging.getLogger("persona-morph")


def build_fingerprint(files, root: str = "") -> str:
    """对"会随包发出去的文件"算一个内容指纹（12 位十六进制），**打包前**调用。

    `files`＝相对仓库根的 posix 路径列表（`pack_online.tracked()` 过滤后的那一份）。
    规矩：算 `agent/version.py` 时**先把 BUILD 行抹掉**再哈希 —— 否则"改 BUILD ⇒ 指纹变 ⇒ BUILD 又变"
    自指死循环（判据里钉着这一条）。
    """
    h = _hashlib.sha256()
    base = root or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    for rel in sorted(str(x).replace("\\", "/") for x in (files or ())):
        p = _os.path.join(base, rel.replace("/", _os.sep))
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if rel.endswith("agent/version.py"):
            data = _re.sub(rb"(?m)^BUILD\s*=.*$", b"BUILD = ''", data)
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(_hashlib.sha256(data).digest())
    return h.hexdigest()[:12]


def write_build(value: str, path: str = "") -> str:
    """把 BUILD 写回 `agent/version.py`（打包前调用，返回写入后的值）。

    ⚠️ 写完必须**清掉字节码缓存**：BUILD 前后都是 12 位十六进制
    ⇒ **文件大小一模一样**，若同一秒内改写，`__pycache__/version.*.pyc` 的 (mtime,size) 校验会
    认为缓存仍有效 ⇒ 后续 `from agent.version import BUILD` 读到**旧值**，于是清单里的
    `base.build` 与包里的实际 BUILD 不一致（用户侧会一直提示"有新包"）。⇒ 这里清缓存。
    """
    p = path or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "version.py")
    # ⛔ 读的时候也必须 `newline=""`（与写的口径一致）—— 文本模式会把 CRLF 翻成 LF，
    #   而写用的是 `newline=""`（不翻译）⇒ **这一写就把整个文件的换行符换掉了**（实测 97 行 CRLF 全变 LF）：
    #   ① `git status` 里 `version.py` 永远显示"已修改"（判据/出包跑一次就脏一次工作树）；
    #   ② 指纹判据「改 BUILD 行 ⇒ 指纹不变」会红（换行符也是内容）。
    with open(p, "r", encoding="utf-8", newline="") as fh:
        s = fh.read()
    # ⚠️ 正则要**连行尾一起保住**：`.*$` 会把行尾那个 `\r` 吃掉 ⇒ 全文变成"CRLF 里夹一行 LF"
    #   （实测 CRLF 101 → 100），git 照样判"已修改"。改成前瞻 `(?=\r?$)`，替换段里不吞 `\r`。
    s2 = _re.sub(r"(?m)^BUILD(\s*=\s*).*?(?=\r?$)",
                 lambda m: "BUILD%s'%s'" % (m.group(1), str(value)), s, count=1)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(s2)
    try:
        import importlib as _il
        _il.invalidate_caches()
        _d = _os.path.join(_os.path.dirname(_os.path.abspath(p)), "__pycache__")
        if _os.path.isdir(_d):
            for _n in _os.listdir(_d):
                if _n.startswith("version.") and _n.endswith(".pyc"):
                    try:
                        _os.remove(_os.path.join(_d, _n))
                    except OSError:
                        pass  # 清理型：静默合法
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return str(value)


def read_build_from(path: str = "") -> str:
    """**直接读文件**解析 BUILD（不走 import ⇒ 不受字节码缓存影响；打包/发布链一律用这个）。"""
    p = path or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "version.py")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            m = _re.search(r"(?m)^BUILD\s*=\s*['\"]([^'\"]*)['\"]", fh.read())
        return str(m.group(1)) if m else ""
    except OSError:
        return ""

def tag(v: str = "") -> str:
    """把内部版本号映射成**对外显示名**（界面/Release 标题都用它）。

    现行体系：版本号本身就是对外名（`Vᛞ`）⇒ **原样返回**。
    旧体系（`2026.9.22.5` 日期式）：映射成 `v2.1.6`（有第 5 段小更新时追加同一个 M）——
    老用户手里的记录还是那个形状，界面得显示得回去。
    解析不出来（脏串）就**原样返回**，绝不编一个。
    """
    s = str(v or VERSION or "").strip()
    if not s:
        return s
    # 现行体系：以 `V` 开头的卢恩式版本名 ⇒ 它就是要显示的那个名字。
    if s.upper().startswith("V") and len(s) > 1 and rune_value(s[1]):
        return s
    # ↓ 兼容：旧日期式映射成旧的 GitHub tag 形状
    if s.startswith("v"):
        s = s[1:]
    p = s.split(".")
    if len(p) < 4:
        return s
    try:
        n = int(p[3])
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return s
    out = "v2.1.%d" % (n + 1)
    if len(p) >= 5 and str(p[4]).strip():
        out += "." + str(p[4]).strip()
    return out
