# -*- coding: utf-8 -*-
"""子进程窗口判据：起子进程必须带「不要窗口」，否则会闪控制台窗。

背景（2026-09-16 用户报「运行的时候极短时间内闪一个弹窗，而且经常闪」）：
  · `scripts/run_all_selftests.py` 拉 84 个子脚本时没带 ⇒ **连闪 84 次**（已修）；
  · 探针（`_scratch/win_watch.ps1`）抓到 4 个 `PseudoConsoleWindow`、其中两个明确是 ffmpeg
    ⇒ `voice.py` / `tts.py` / `voice_models.py` / `video_gen.py` / `bilibili.py` / `dep_heal.py`
    这些**起 ffmpeg / 第三方 exe** 的地方也没带（已修）。
项目里早有这条约定：`agent/video_read.py` 注释原话「Windows 下 `CREATE_NO_WINDOW`，不许弹黑框」，
生产代码 `persona_morph.py` 也一直带 `0x08000000`。

本判据＝用 `ast` 扫 `agent/` 与 `scripts/` 里所有 `subprocess.run/Popen/call/check_output/check_call`
调用，**要求该调用带 `creationflags`**。确实不需要窗口控制的少数几处，必须写进 `ALLOW`
并注明理由（不许默默放过）。

用法：runtime\\python\\python.exe -X utf8 -u scripts\\proc_window_selftest.py
"""
import ast
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


# 确实不需要"不要窗口"的调用：{(相对路径, 行号): 理由}
# 例：自检脚本里"故意要看到 cmd 行为"的那种才写进来；产品路径一律不许。
ALLOW = {}

CALLS = ("run", "Popen", "call", "check_output", "check_call")
bad = []
scanned = 0

for base in ("agent", "scripts"):
    for root, dirs, files in os.walk(os.path.join(ROOT, base)):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                tree = ast.parse(io.open(p, encoding="utf-8").read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id == "subprocess" and f.attr in CALLS):
                    continue
                scanned += 1
                kws = {k.arg for k in node.keywords if k.arg}
                if "creationflags" in kws:
                    continue
                if (rel, node.lineno) in ALLOW:
                    continue
                bad.append("%s:%d subprocess.%s" % (rel, node.lineno, f.attr))

ok("扫到了 subprocess 调用（数量不为 0，说明扫描真的跑了）", scanned > 0, "%d 处" % scanned)
ok("每一处都带了 creationflags（不许闪控制台窗）", not bad,
   ("；".join(bad[:10]) + ("…… 共 %d 处" % len(bad) if len(bad) > 10 else "")) if bad else "")

print("")
print("子进程窗口判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
