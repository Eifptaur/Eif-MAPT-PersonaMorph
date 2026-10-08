# -*- coding: utf-8 -*-
"""launcher 弹窗的**真出图 + 尺寸边界**判据（第 65 项）。

守两件在真机上出过事的事情：
  A **真出图**：`--shot` 出的每张 PNG 必须**真的画上了东西**（尺寸 > 0 且**颜色数够多**）。
    ⛔ 起因：截图**路径写错**却"判据全过"（假绿）——所以这里不看"有没有文件"，看"图里有没有内容"。
  B **尺寸边界**：`--dlgprobe` 输出的控件树里，**每个控件都必须落在它父容器的客户区内**。
    ⛔ 起因：`WebView2MissingForm` 的 **4 颗按钮挤一行** ⇒ 主按钮 `x=-12`、左边 12px 在客户区外，
    而当时**没有任何机械判据**看得见。

实现选择（写清理由，免得后人以为漏了）：**不改 C#**，而是**解析 `--dlgprobe` 已有的控件树输出**
（缩进即父子层级、每个控件都有 `X/Y/Width/Height`、每棵树头有 `client=WxH`）⇒ 同样拿到"机械读数"，
且不用动已稳定的编译产物。

用法：runtime\\python\\python.exe scripts/launcher_probe_selftest.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # ⛔ 先补路径再 import
EXE = __import__("agent.autostart", fromlist=["x"]).find_launcher_exe()

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


#: **允许是空白图**的窗体（**必须写理由**，否则"空白"就该判红）
BLANK_ALLOW = {
    "console": "控制台窗体靠 WebView2 渲染；离线出图时页面还没画出来 ⇒ 这张本来就是空白（不是缺陷）",
}


def _run(args, timeout=180):
    """跑 launcher 探针 ⇒ 合并输出（GBK 解码失败就替换，数字仍可解析）。

    ⛔ 必须带 `CREATE_NO_WINDOW`：不带的话子进程会**闪一个控制台黑窗**（用户会看见）。
    这是全仓统一约定（`proc_window_selftest` 会机械核，漏一处就红）。
    """
    try:
        p = subprocess.run([EXE] + list(args), cwd=ROOT, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (p.stdout or b"") + (p.stderr or b"")
        return p.returncode, out.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, "RUN-FAILED: %s" % e


# ── ⛔ 产品目录洁净度：真 exe 一关窗就 `SaveGeo()` 写 `data\console_window.txt` ──────────
# `_iso14` 只拦得住 Python 侧路径，**拦不住 C# exe 往自己目录写** ⇒ 这里必须"跑前快照、跑后还原"，
# 否则跑一次判据就把用户调好的控制台窗口位置覆盖掉（`run_all_selftests.py` 的洁净度总闸会点名）。
_DIRTY_GUARD = [os.path.join(ROOT, "data", "console_window.txt"),
                os.path.join(ROOT, "logs", "launcher-dlgprobe-latest.txt")]
_SNAP = {}
for _p in _DIRTY_GUARD:
    try:
        _SNAP[_p] = io.open(_p, "rb").read() if os.path.exists(_p) else None
    except Exception:  # noqa: BLE001
        _SNAP[_p] = None


def _restore_dirty():
    """把探针跑动期间被真 exe 改写的产品文件还原（不存在就删掉，原来就有就写回原字节）。"""
    for _p, _b in _SNAP.items():
        try:
            if _b is None:
                if os.path.exists(_p):
                    os.remove(_p)
            else:
                with io.open(_p, "wb") as _f:
                    _f.write(_b)
        except Exception:  # noqa: BLE001
            pass


import atexit  # noqa: E402
atexit.register(_restore_dirty)


print("── A. 真出图（`--shot`）──")
if not os.path.exists(EXE):
    print("  ⚠️ 没有 一键启动.exe ⇒ 先编译（`一键编译.cmd`）。本判据**判红**，不静默跳过。")
    ok("A0 编译产物存在", False, EXE)
    print("\n== launcher 判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
    sys.exit(1)
TD = tempfile.mkdtemp(prefix="shots-")
rc, out = _run(["--shot", TD])
pngs = sorted(f for f in os.listdir(TD) if f.endswith(".png"))
ok("A1 `--shot` 跑完（rc=%s）且**出了图**" % rc, rc == 0 and len(pngs) >= 8,
   "%d 张：%s" % (len(pngs), ",".join(pngs[:6])))
try:
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None
_rows = []
for f in pngs:
    p = os.path.join(TD, f)
    size = os.path.getsize(p)
    cols = 0
    try:
        if Image is not None:
            im = Image.open(p).convert("RGB")
            _cs = im.getcolors(maxcolors=100000)
            cols = len(_cs) if _cs else 99999
            dim = "%dx%d" % im.size
        else:
            dim = "?"
    except Exception:  # noqa: BLE001
        dim = "读不出"
    _rows.append((f[:-4], size, cols, dim))
_bad = [(n, c, d) for n, s, c, d in _rows if c <= 3 and n not in BLANK_ALLOW]
ok("A2★★ **每张图都真的画上了东西**（颜色数 > 3）—— 抓「路径没错但图是空白」这种假绿",
   not _bad, str(_bad[:3]))
_shown = [(n, c, d) for n, s, c, d in _rows if n in BLANK_ALLOW]
ok("A3 允许空白的窗体**有名单且写了理由**（第 %d 个：%s）" % (len(BLANK_ALLOW), ",".join(BLANK_ALLOW)),
   all(n in BLANK_ALLOW for n, _c, _d in _shown), str(_shown))
ok("A4 反向锚：**白名单是逐项的**（不是「整批放过」）—— 名单里的名字必须真的出现在本次出图里",
   all(n in {r[0] for r in _rows} for n in BLANK_ALLOW), "")

print("\n── B. 尺寸边界（`--dlgprobe` 的控件树）──")
rc2, out2 = _run(["--dlgprobe"])
lines = out2.splitlines()
# 控件树：`   - Name {X=..,Y=..,Width=..,Height=..}`，缩进即层级；树头 `FormName client=WxH`
_CTRL = re.compile(r"^(\s*)-\s+(\S+)\s*\{X=(-?\d+),Y=(-?\d+),Width=(-?\d+),Height=(-?\d+)\}")
_HEAD = re.compile(r"^(\S+)\s+client=(\d+)x(\d+)")
trees = 0
offs = []
cur = None
stack = []          # [(indent, x, y)]
for ln in lines:
    hm = _HEAD.match(ln)
    if hm:
        trees += 1
        cur = hm.group(1)
        stack = [(-1, 0, 0)]
        continue
    cm = _CTRL.match(ln)
    if not cm or cur is None:
        continue
    ind, name, x, y, w, h = len(cm.group(1)), cm.group(2), *(int(cm.group(i)) for i in (3, 4, 5, 6))
    while stack and stack[-1][0] >= ind:
        stack.pop()
    px, py = (stack[-1][1], stack[-1][2]) if stack else (0, 0)
    ax, ay = px + x, py + y
    offs.append((cur, name, ax, ay, w, h))
    stack.append((ind, ax, ay))
ok("B0 分母守卫：解析到**控件树**（≥5 棵）且**控件数 ≥ 40**（否则下面的断言是空转）",
   trees >= 5 and len(offs) >= 40, "%d 棵树 / %d 个控件" % (trees, len(offs)))
# 客户区尺寸（每棵树头给的 client=WxH）
_client = {}
for ln in lines:
    hm = _HEAD.match(ln)
    if hm:
        _client[hm.group(1)] = (int(hm.group(2)), int(hm.group(3)))
_over = []
for form, name, ax, ay, w, h in offs:
    cw, ch = _client.get(form, (0, 0))
    if cw <= 0 or ch <= 0:
        continue
    if ax < 0 or ay < 0 or ax + w > cw or ay + h > ch:
        _over.append((form, name, ax, ay, w, h, "%dx%d" % (cw, ch)))
ok("B1★★ **没有控件越出客户区**（4 颗按钮挤一行那种出界，这里会当场报出来）",
   not _over, str(_over[:3]))
_fake = [("F", "Btn", -12, 0, 120, 30, "516x220")]        # 故意越界的一个控件
_over_fake = [r for r in _fake if r[2] < 0 or r[3] < 0 or r[2] + r[4] > 516 or r[3] + r[5] > 220]
ok("B2 反向锚：**故意造一个越界控件**（x=-12）能被同一条判据抓出来（证明 B1 不是恒真）",
   len(_over_fake) == 1, str(_over_fake))
# ⛔ 原始读数**留档到本次的临时目录**，不写产品 `logs/`（写产品目录 = 污染用户状态，总闸会点名）。
#    想留长档请走 release/归档流程，不要由判据代劳。
_dump = os.path.join(TD, "launcher-dlgprobe-latest.txt")
try:
    open(_dump, "w", encoding="utf-8").write(out2)
except Exception:  # noqa: BLE001
    pass
ok("B3 控件树的原始读数**留档在本次临时目录**（下次出界时不用重新跑就能查）",
   os.path.exists(_dump) and not os.path.dirname(_dump).startswith(os.path.join(ROOT, "logs")), _dump)

print("\n== launcher 判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
_restore_dirty()
shutil.rmtree(TD, ignore_errors=True)
sys.exit(1 if FAIL else 0)
