# -*- coding: utf-8 -*-
"""离线包校验脚本自测（不需要微信、不需要网络）。

跑法：runtime\\python\\python.exe scripts\\pack_offline_selftest.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pack_offline as P   # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, extra))


# ── 1 requirements 解析 ────────────────────────────────────────────────
reqs = dict(P.parse_requirements("""
# 注释行
wechatauto-replica==1.2.2.2
requests>=2.28.2
urllib3>=1.26.0
Pillow>=9.0.0   # 行尾注释
colorama
--extra-index-url https://example.com
PyAutoGUI>=0.9.54
"""))
check("解析出包名→版本", reqs.get("wechatauto-replica") == "==1.2.2.2", str(reqs))
check("解析 >= 规格", reqs.get("requests") == ">=2.28.2" and reqs.get("Pillow") == ">=9.0.0", str(reqs))
check("无版本规格解析为空串", reqs.get("colorama") == "", str(reqs))
check("跳过注释与 --选项行", "--extra-index-url" not in reqs and len(reqs) == 6, str(sorted(reqs)))

# ── 2 版本比较 ─────────────────────────────────────────────────────────
check("== 命中", P.satisfies("1.2.2.2", "==1.2.2.2") and not P.satisfies("1.1.5.1", "==1.2.2.2"))
check(">= 命中/不命中", P.satisfies("2.0.29", ">=2.0.18") and not P.satisfies("2.0.1", ">=2.0.18"))
check("空规格恒真", P.satisfies("0.0.1", ""))
check("段数不同也能比（1.2 vs 1.2.0）", P.satisfies("1.2", "==1.2.0"))
check("~= 兼容版", P.satisfies("1.2.9", "~=1.2.0"))

# ── 3 wheel 文件名解析 + 归一 ──────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="pack_off_")
for fn in ("PyAutoGUI-0.9.54-py3-none-any.whl",
           "wechatauto_replica-1.2.2.2-py3-none-any.whl",
           "numpy-2.2.6-cp310-cp310-win_amd64.whl",
           "opencv_python-5.0.0.93-cp37-abi3-win_amd64.whl",
           "pymsgbox-2.0.1-py3-none-any.whl",
           "MANIFEST.txt"):
    open(os.path.join(tmp, fn), "wb").write(b"x" * 10)
w = P.scan_wheels(tmp)
check("扫出 5 个 wheel（忽略非 whl）", len(w) == 5, str(list(w)))
check("名字归一：PyAutoGUI → pyautogui", "pyautogui" in w and w["pyautogui"]["version"] == "0.9.54", str(w.get("pyautogui")))
check("下划线名字归一：wechatauto_replica → wechatauto-replica",
      "wechatauto-replica" in w, str(list(w)))
check("带平台 tag 的版本解析正确", w["numpy"]["version"] == "2.2.6" and w["opencv-python"]["version"] == "5.0.0.93",
      str(w.get("numpy")))

# ── 4 覆盖判定（缺包 / 版本不够 / 满足）────────────────────────────────
ok, miss = P.coverage([("numpy", ">=2.2.6"), ("requests", ">=2.28.2"), ("pymsgbox", ">=2.0.1")], w)
check("满足的进 ok、缺的进 missing", [m[0] for m in miss] == ["requests"] and len(ok) == 2, str(miss))
ok2, miss2 = P.coverage([("numpy", ">=9.9.9")], w)
check("版本不够也算缺失", len(miss2) == 1 and "不满足" in miss2[0][2], str(miss2))

# ── 5 适配层版本从代码里读（离线包必须含实测版本）──────────────────────
pin = P.adapter_pin()
check("能从 replica_adapter 读出 KNOWN_GOOD", bool(pin) and pin[0][0] == "wechatauto-replica", str(pin))
check("钉的版本是 == 形式", pin and pin[0][1].startswith("=="), str(pin))

# ── 6 清单文本内容 ─────────────────────────────────────────────────────
P.WHEELS = tmp
man = P.build_manifest([("wechatauto-replica", "==1.2.2.2"), ("requests", ">=2.28.2")], w)
check("清单含缺失清单", "缺失" in man["text"] and "requests" in man["text"])
check("清单含补齐命令", "pip download" in man["text"])
check("清单含安装方式", "--no-index --find-links" in man["text"])
man2 = P.build_manifest([("numpy", ">=2.2.6")], w)
import re as _re  # noqa: E402
check("清单列出哈希（16 位十六进制）", bool(_re.search(r"[0-9a-f]{16}", man2["text"])), man2["text"][:120])
check("清单行含文件名与大小", "numpy-2.2.6" in man2["text"])
check("额外包以注释列出", "额外带的包" in man2["text"])

# ── 7 真仓库自检：requirements 必须被 offline/wheels 全覆盖 ────────────
P.WHEELS = os.path.join(ROOT, "offline", "wheels")
real_reqs = P.parse_requirements(open(P.REQ, "r", encoding="utf-8").read())
real_wheels = P.scan_wheels(P.WHEELS)
ok3, miss3 = P.coverage(P.adapter_pin() + real_reqs, real_wheels)
check("真仓库：requirements 全覆盖（缺包会在这里红）", not miss3,
      "缺失：" + "; ".join("%s %s (%s)" % m for m in miss3))
check("真仓库：wheel 文件数 ≥ 30", len(real_wheels) >= 30, str(len(real_wheels)))
check("真仓库：没有旧版适配层 wheel 残留", "wechatauto-replica" in real_wheels
      and real_wheels["wechatauto-replica"]["version"] == "1.2.2.2",
      str(real_wheels.get("wechatauto-replica")))
check("真仓库：清单文件已生成", os.path.exists(P.MANIFEST), P.MANIFEST)

shutil.rmtree(tmp, ignore_errors=True)
print("\n=== 离线包自测：%d PASS / %d FAIL ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
