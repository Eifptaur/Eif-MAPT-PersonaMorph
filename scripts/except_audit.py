#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`except` 宽窄审计（**纯读、零副作用、只报不改**）。

要治的病：**编程错误被宽 `except` 吞成"业务失败"**。
典型现场：一处代码引用了不存在的名字 ⇒ 抛 `NameError`/`AttributeError` ⇒ 被自己的
`except Exception` 吞掉 ⇒ 上层只看到"保存失败/发送失败" ⇒ **排查方向全错**。
这类问题比它引发的那个 bug 更贵 —— 它污染的是**归因**。

分三档（判据是机械的、写在下面，不靠肉眼"感觉宽不宽"）：
  ① **该宽**：捕获宽类型（`except:` / `Exception` / `BaseException`），**且有留痕/有交代** ——
     handler 里留了栈（`log.exception`/`traceback`/裸 `raise`），或 except 行带 `# noqa`
     （本项目用 `# noqa: BLE001` 显式声明"这是有意宽捕"），或**行内/上方 3 行注释**里写明了理由。
  ② **该窄候选**：捕获宽类型，**且** 无留痕、无交代、**且** 这个 `try` 体里**看不到任何
     会因外部因素失败的东西**（网络/文件/子进程/第三方/图形窗口…）
     ⇒ 那它吞掉的多半就是**自己代码的编程错误**。**这一档才是要看的清单。**
  ③ **待查**：其余（宽捕 + 在碰外部，但没留痕/没交代）。**只是没写交代 ≠ 有问题**，所以不单列清单。

⛔ 本工具**不改任何代码**：② 档清单要人工过一遍再决定动作 ——
   机械判据只做初筛，"该宽该窄"最终要人来定（改代码另开条，避免与发送链打架）。

⚠️ 判据自身的两个坑（已踩过，写这里防重踩）：
  1. "写明理由"的关键词**不能带短小写片段**（最初收了 `io` ⇒ 它命中 `option`/`version`/`region`
     这些词 ⇒ **93% 的 handler 都被判成"该宽"、② 档空** ⇒ 审计自己空转还挺好看）。
     ⇒ 短词一律**区分大小写**（`IO`/`noqa`/`B902`），中文词才放宽。
  2. 所以本工具有 `--self-test`：**先自证分档器真的能把"①/②/③"分开**（塞三个合成样本进去），
     再拿它去审真代码 —— 否则"② 档 0 条"到底是"真干净"还是"分档坏了"说不清。

跑法：`runtime\\python\\python.exe scripts\\except_audit.py`
     `runtime\\python\\python.exe scripts\\except_audit.py --self-test`
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception: # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 扫描面：产品与判据自己。⛔ 不含 `runtime/`（第三方库）、`vendor/`、`__pycache__`、备份目录。
SCAN_DIRS = ("agent", "scripts", "ui_qt", "launcher-src", "tools.d")
SKIP_DIR_PARTS = ("__pycache__", "vendor", "runtime", "_backup", "_recovered", "_lite_extract")

WIDE = {"Exception", "BaseException"}          # 捕获这些就等于"什么都接"

# 留栈的写法（handler 体内出现任一个 ⇒ 算有留痕）
TRACE_CALLS = ("log.exception", "logger.exception", "logging.exception", ".exception(",
               "print_exc", "format_exc", "traceback.")

# "交代了理由"的关键词。⛔ 只在这些**注释文本**里找（不扫代码，否则 "失败就…" 这类
#    会出现在字符串里 ⇒ 又一次过度匹配）；短英文词**区分大小写**（见文件头坑 1）。
WHY_CN = ("容错", "忽略", "宽", "网络", "外部", "降级", "兜底", "尽力", "不致命", "非致命",
          "可选", "理由", "有意", "故意", "别被", "崩", "管道", "编码", "兼容", "环境",
          "没装", "读不到", "拿不到", "当作", "当成", "视作", "不稳定", "平台", "尽力而为")
WHY_EN = ("noqa", "IO", "best effort", "best-effort", "BLE001", "B902")

# "会因外部因素失败"的调用痕迹。强证据才用于"抬档"。
STRONG_RISKY = (
    "requests.", "urllib", "socket.", "http.", "open(", "os.remove", "os.rename", "os.replace",
    "os.makedirs", "os.listdir", "os.stat", "os.walk", "os.path", "subprocess", "ctypes",
    "win32", "winreg", "json.load", "json.loads", "Image.open", "shutil.", "glob.", "time.sleep",
    "sqlite3", "Popen", "check_output", "clipboard.", "uia.", "gui.", "win32gui", "win32con",
    "psutil", "importlib", "urlopen", "connect(", "recv(", "sendall(",
)
# 弱证据：自己代码也常用 ⇒ 不足以判"该宽"，只在明细里带出来给人看。
WEAK_RISKY = ("下标取值", ".get(", "getattr(")


def _iter_files():
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [x for x in dns if x not in SKIP_DIR_PARTS and not x.startswith(".")]
            for fn in fns:
                if fn.endswith(".py"):
                    yield os.path.join(dp, fn)


def _comment_part(line: str) -> str:
    """只取一行的**注释部分**（`#` 之后）。⛔ 理由只在注释里找 —— 扫代码会把
    字符串里的"失败就…"当成理由（过度匹配，实测把 93% 的 handler 判成"该宽"）。"""
    i = line.find("#")
    return line[i + 1:] if i >= 0 else ""


def _why_context(raw: list, lineno: int, h) -> tuple:
    """`except` 行及上方 3 行 ＋ handler 体内前 6 行的**注释**，返回 (是否交代了理由, 命中的词)。"""
    lo = max(1, lineno - 3)
    parts = [_comment_part(raw[i - 1]) for i in range(lo, lineno + 1) if 1 <= i <= len(raw)]
    hi = min(len(raw), (h.end_lineno or h.lineno) + 0)
    parts += [_comment_part(raw[i - 1]) for i in range(h.lineno, min(hi, h.lineno + 6) + 1)
              if 1 <= i <= len(raw)]
    ctx = "\n".join(p for p in parts if p.strip())
    hit = [w for w in WHY_CN if w in ctx] + [w for w in WHY_EN if w in ctx]
    return bool(hit), ",".join(hit[:3])


def _handler_has_trace(h) -> bool:
    for node in ast.walk(h):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            try:
                txt = ast.unparse(node.func)
            except Exception: # noqa: BLE001
                continue
            if any(t in txt for t in TRACE_CALLS):
                return True
    return False


def _handler_uses_exc(h) -> bool:
    """handler 里**用到了捕获到的异常本身**（`as e` 之后拿 `e` 去组错误信息/返回值）⇒ 也算有交代。
    实例：`except Exception as e: return {"ok": False, "error": str(e)}` —— 错误是**上报出去**的，
    不是被吞掉，不该混进 ② 档。"""
    if not h.name:
        return False
    try:
        body = ast.unparse(h)
    except Exception: # noqa: BLE001
        return False
    return re.search(r"(?<![\w.])%s(?![\w])" % re.escape(h.name), body) is not None


def _handler_logs_exc(h) -> bool:
    """handler 里**记了日志**（任意级别）就算"有交代" —— `log.debug("...：%s", e)` 至少让
    现场在日志里留痕，与"彻底吞掉"是两回事，不该混进 ② 档。"""
    for node in ast.walk(h):
        if not isinstance(node, ast.Call):
            continue
        try:
            txt = ast.unparse(node.func)
        except Exception: # noqa: BLE001
            continue
        if re.search(r"\blog(ger)?\.(debug|info|warning|warn|error|critical|exception)\b", txt):
            return True
    return False


def _catch_names(h) -> str:
    if h.type is None:
        return "裸 except"
    try:
        return ast.unparse(h.type)
    except Exception: # noqa: BLE001
        return "?"


def _try_body_risky(node: ast.Try) -> tuple:
    strong, weak = [], []
    for sub in node.body:
        for n in ast.walk(sub):
            if isinstance(n, ast.Call):
                try:
                    txt = ast.unparse(n.func) + "("
                except Exception: # noqa: BLE001
                    continue
                for k in STRONG_RISKY:
                    if k in txt and k not in strong:
                        strong.append(k.rstrip("("))
                if ".get" in txt and ".get(" not in weak:
                    weak.append(".get(")
                if "getattr" in txt and "getattr(" not in weak:
                    weak.append("getattr(")
            if isinstance(n, ast.Subscript) and "下标取值" not in weak:
                weak.append("下标取值")
    return strong, weak


def classify(src: str, rel: str = "<mem>") -> list:
    """把一段源码里的每个 `except` 分档。返回 rows。"""
    raw = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        for h in node.handlers:
            names = _catch_names(h)
            wide = (h.type is None) or any(n in WIDE for n in re.findall(r"\w+", names))
            why, why_hit = _why_context(raw, h.lineno, h)
            trace = _handler_has_trace(h)
            logged = _handler_logs_exc(h)
            used = _handler_uses_exc(h)
            strong, weak = _try_body_risky(node)
            if not wide:
                grade = "①具体类型"
            elif trace or logged or used or why:
                grade = "①该宽"
            elif not strong:
                grade = "②该窄候选"
            else:
                grade = "③待查"
            rows.append({"file": rel, "line": h.lineno, "catch": names, "grade": grade,
                         "trace": trace, "logged": logged, "used": used, "why": why_hit,
                         "risky": ",".join(strong[:3]), "weak": ",".join(weak[:2]),
                         "keyed": "下标取值" in weak})
    return rows


# ── 判据自证：塞三个合成样本，确认分档器真能把 ①/②/③ 分开 ─────────────────────
_SELF_SAMPLES = [
    ("②该窄候选", # 宽捕 + 无留痕 + 无交代 + 纯内存操作 ⇒ 吞掉的只可能是编程错误
     "def f(d):\n    try:\n        return d['a'] + 1\n    except Exception:\n        return 0\n"),
    ("①该宽", # 同一个抛点，但**交代了理由** ⇒ 该宽
     "def f(d):\n    try:\n        return d['a'] + 1\n    except Exception:   # 容错：缺字段当 0\n        return 0\n"),
    ("③待查", # 宽捕 + 无交代 + 在调外部接口 ⇒ 只是没写交代
     "def f(d):\n    try:\n        return open(d).read()\n    except Exception:\n        return ''\n"),
    ("①该宽", # 宽捕 + 留了栈 ⇒ 该宽
     "import logging\nlog = logging.getLogger('x')\n"
     "def f(d):\n    try:\n        return d['a']\n    except Exception:\n        log.exception('坏了')\n        return 0\n"),
    ("①该宽", # 宽捕 + **记了日志**（不是彻底吞掉）⇒ 也不该进 ② 档
     "import logging\nlog = logging.getLogger('x')\n"
     "def f(d):\n    try:\n        return d['a']\n    except Exception as e:\n        log.debug('读不到：%s', e)\n        return 0\n"),
]


def self_test() -> int:
    ok = 0
    bad = 0
    for want, src in _SELF_SAMPLES:
        got = classify(src)
        g = got[0]["grade"] if got else "（没分出来）"
        good = (g == want)
        ok += 1 if good else 0
        bad += 0 if good else 1
        print("  %s 期望=%-8s 实得=%-8s" % ("OK  " if good else "FAIL", want, g))
    print("自证：%d 通过 / %d 失败（分母 %d 个合成样本）" % (ok, bad, len(_SELF_SAMPLES)))
    if bad == 0 and len(_SELF_SAMPLES) < 3:
        print("⚠️ 样本不足 3 个 ⇒ 分档器不算自证过")
        return 1
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30, help="每档最多打印多少条")
    ap.add_argument("--self-test", action="store_true", help="先自证分档器有效，再（可选）审代码")
    ap.add_argument("--dump-wide", action="store_true", help="把 ① 档也列出来（默认只报数量）")
    a = ap.parse_args()

    if a.self_test:
        print("── 判据自证：分档器能不能把 ①/②/③ 分开 ──")
        rc = self_test()
        if rc:
            print("⛔ 自证没过 ⇒ 下面的统计不可信，先修分档器")
            return rc
        print()

    rows = []
    for p in _iter_files():
        try:
            src = io.open(p, encoding="utf-8", errors="ignore").read()
        except Exception: # noqa: BLE001
            continue
        rows += classify(src, os.path.relpath(p, ROOT).replace("\\", "/"))

    cnt = Counter(r["grade"] for r in rows)
    print("扫描面：%s" % "、".join(SCAN_DIRS))
    print("handler 总数：%d" % len(rows))
    for g in ("①具体类型", "①该宽", "②该窄候选", "③待查"):
        print("  %-9s %d" % (g, cnt.get(g, 0)))
    print()

    def dump(grade, title):
        rs = [r for r in rows if r["grade"] == grade]
        if not rs:
            return
        print("── %s（%d 条%s）──" % (title, len(rs),
                                    "，只列前 %d 条" % a.top if len(rs) > a.top else ""))
        for r in rs[:a.top]:
            print("  %s:%d   捕获=%-22s 留痕=%-5s 记日志=%-5s 交代=%-5s 外部风险=%s"
                  % (r["file"], r["line"], r["catch"], r["trace"], r["logged"],
                     r["why"] or "无", r["risky"] or "（看不到）"))
        print()

    if a.dump_wide:
        dump("①该宽", "① 该宽（有留痕/记了日志/交代了理由）")

    # ── ② 档：宽捕 ＋ 无留痕 ＋ 无交代 ＋ try 体里看不到外部风险 ⇒ 吞掉的多半是编程错误 ──
    #    ⭐ 其中**含下标/键取值**（`d["a"]`）的最可疑：那正是 `KeyError`/`IndexError` 的抛点，
    #      被吞掉之后上层只会看到"读不到/当空处理"。⇒ 默认**先列这一子集**，再看全量。
    _b = [r for r in rows if r["grade"] == "②该窄候选"]
    _b_keyed = [r for r in _b if r["keyed"]]
    print("── ② 该窄候选（共 %d 条；其中**含下标/键取值**的 %d 条 —— 这一子集最可疑）──"
          % (len(_b), len(_b_keyed)))
    for r in _b_keyed[:a.top]:
        print("  %s:%d   捕获=%-22s try体其他线索=%s" % (r["file"], r["line"], r["catch"],
                                                   r["weak"] or "无"))
    if len(_b_keyed) > a.top:
        print("  …（还有 %d 条）" % (len(_b_keyed) - a.top))
    _rest = len(_b) - len(_b_keyed)
    if _rest:
        print("  另有「不含下标」的 %d 条（纯转发/纯赋值，风险较低）" % _rest)
    print()
    dump("③待查", "③ 待查（宽捕＋在碰外部，只是没留痕/没交代）")
    print("⛔ 只报不改：「该宽该窄」要人工定，改动另开条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
