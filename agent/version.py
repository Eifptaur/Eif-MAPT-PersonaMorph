# -*- coding: utf-8 -*-
"""群相版本号 —— **唯一来源**。

为什么单开一个文件：清单（`base.version`）、控制台"当前版本"、更新检查、发布脚本
都从这一处取，避免多处写死对不上（启动器侧踩过"版本散落会打架"）。

版本号规则（2026-09-16 用户定，**加了第 5 段**）：
  · `YYYY.M.D.N`   —— 有**新功能 / 新用户可见行为**时 +1（N＝当天第几个功能版本）
  · `YYYY.M.D.N.M` —— 第 5 段＝该功能版本下的**小更新**（只修 bug／只改文案·自检·内部接线）时 +1
  例：`2026.9.16.16` 之后只修 bug ⇒ `2026.9.16.16.1` ⇒ 再修 ⇒ `2026.9.16.16.2`；出新功能 ⇒ `2026.9.16.17`。
  原话：「你把版本号做得细一点，不然每次都是十几、几十的，到时候都要到 100 去了。
  你可以多加一个点，用来表示版本号的小更新。」

**为什么小更新也要动版本号**（2026-09-16 改口径）：「如果修了 bug 本来就推送不了别人更新的
话，那还是修一个 bug 发一个版本号吧」——更新检查只比版本号字符串，同版本换包对已装用户永远静默。
⇒ **Release tag ＝ `v2.1.<N+1>`，有第 5 段时追加同一个 M**（`2026.9.16.16.1` ↔ `v2.1.17.1`）。
"""

VERSION = '2026.9.21.8'
# ⚡ 2026-09-18 晚加：**内容指纹**（打包时由 `scripts/pack_online.py` 写进来；开发树里留空）。
#
# 为什么需要（2026-09-16 用户一问逼出来：「那就没有办法让他们也接到更新提示吗」）：
#   更新检查**只比版本号字符串** ⇒ **同一个版本号换包，对已装用户永远静默**（他那边
#   `theirs == mine` ⇒ 直接判"已是最新"）。有了指纹：清单里带 `base.build`，用户侧一比就能看出
#   "版本号没变、但包的内容变了" ⇒ 照常提示更新。
# ⚠️ **它对已经在跑的老版无效**（老版没有这段代码，只认版本号）⇒ 老用户只能靠**版本号前进**触达；
#   这正是"修一个 bug 就发一个版本号"那条口径的由来（见上面 docstring）。
BUILD = 'bcee40ac6b31'

import hashlib as _hashlib          # noqa: E402
import os as _os                    # noqa: E402
import re as _re                    # noqa: E402


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

    ⚠️ 写完必须**清掉字节码缓存**（2026-09-18 深夜踩到真坑）：BUILD 前后都是 12 位十六进制
    ⇒ **文件大小一模一样**，若同一秒内改写，`__pycache__/version.*.pyc` 的 (mtime,size) 校验会
    认为缓存仍有效 ⇒ 后续 `from agent.version import BUILD` 读到**旧值**，于是清单里的
    `base.build` 与包里的实际 BUILD 不一致（用户侧会一直提示"有新包"）。⇒ 这里清缓存。
    """
    p = path or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "version.py")
    with open(p, "r", encoding="utf-8") as fh:
        s = fh.read()
    s2 = _re.sub(r"(?m)^BUILD\s*=.*$", "BUILD = '%s'" % str(value), s, count=1)
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
                        pass
    except Exception:
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
