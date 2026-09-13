# -*- coding: utf-8 -*-
"""第 19 条判据：控制台「改完即生效 + 撤销修改」（不需要联网、不花 token）。

跑法： py -3 scripts\\autosave_ui_selftest.py      退出码 0=全过 / 1=有失败

分三层验：
  A. **服务端前提**（用真函数跑）：`/api/config` 是深合并 ⇒ 只 POST 一个键不会冲掉别的字段；
     打码值不覆盖真密钥（撤销/自动保存都不可能把 Key 写坏）。
  B. **前端接线**（源码级）：顶栏两个控件、document 上的委托监听、只 POST 单键、撤销栈上限与回推、
     保存前抓整份快照、程序性填表期间挡住监听器、自绘下拉的 change 必须冒泡（否则委托收不到）。
  C. **JS 语法**（抽 `<script>` 交给 `node --check`）+ 中文文案 + 阴性对照（判据不是恒真）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import console_html as CH          # noqa: E402
from agent import webui                       # noqa: E402
from agent.config import deep_merge           # noqa: E402

PASS, FAIL = [], []
HTML = CH.HTML
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def has(token, where=None):
    return token in (where if where is not None else SRC)


def main():
    print("== A. 服务端前提（真函数跑）==")
    full = {"api": {"base_url": "u", "model": "m", "temperature": 0.8, "api_key": "REALKEY"},
            "store": {"context_tier": 2, "recall": {"enabled": True, "window_sec": 180}},
            "persona": {"custom_rules": "x"}}
    m = deep_merge(full, {"api": {"temperature": 0.3}})
    ok("只传一个键也能合并（别的键原样保留）",
       m["api"]["temperature"] == 0.3 and m["api"]["model"] == "m" and m["api"]["api_key"] == "REALKEY")
    ok("别的段完全不受影响",
       m["store"] == full["store"] and m["persona"] == full["persona"])
    m2 = deep_merge(full, {"store": {"recall": {"enabled": False}}})
    ok("三层路径也只在叶子处覆盖",
       m2["store"]["recall"] == {"enabled": False, "window_sec": 180} and m2["api"] == full["api"])
    ok("合并返回深拷贝（不会原地改到旧配置）", m["api"] is not full["api"] and full["api"]["temperature"] == 0.8)

    real_get = webui.get_config
    webui.get_config = lambda: {"api": {"api_key": "REALKEY", "provider_keys": {"deepseek": "DKEY", "zhipu": "ZKEY"}}}
    try:
        c1 = {"api": {"api_key": "sk-••••1234", "provider_keys": {"deepseek": "sk-••••9999", "zhipu": "NEWKEY"}}}
        webui._protect_secrets(c1)
        ok("打码的 api_key 不覆盖真值（撤销写回打码值也不会毁 Key）", c1["api"]["api_key"] == "REALKEY")
        ok("provider_keys 里的打码值同样保护", c1["api"]["provider_keys"]["deepseek"] == "DKEY")
        ok("真新值正常写入", c1["api"]["provider_keys"]["zhipu"] == "NEWKEY")
    finally:
        webui.get_config = real_get

    print("== B. 前端接线（源码级）==")
    top = SRC[SRC.find('<div class="topbar">'): SRC.find('<div class="shell">')]
    ok("顶栏有「改完即生效」开关", 'id="autoApplyChk"' in top and 'id="autoChip"' in top)
    ok("顶栏有「撤销」按钮", 'id="undoBtn"' in top)
    ok("顶栏允许换行（多两个控件不会挤坏布局）", "flex-wrap:wrap" in SRC[:SRC.find('<div class="topbar">')])
    ok("开关文案写清了两种模式",
       '改完即生效' in top and '取消勾选＝回到手动保存模式' in top)
    ok("撤销按钮写了记多少步",
       '最多 10 步' in top or ('UNDO_MAX = 10' in SRC and '撤销上一步修改' in top))
    ok("委托监听挂在 document 上（含动态生成的字段）",
       "document.addEventListener('change'" in SRC and "el.dataset.cfg" in SRC)
    ok("程序性填表期间挡住监听器", "window._applying = true" in SRC and "window._applying = false" in SRC
       and "|| window._applying" in SRC)
    ok("api_key 不走自动保存（有自己的保存按钮）", "path === 'api.api_key'" in SRC)
    ok("自动生效只 POST 改动的那一个键", "await postCfg(oneKey(path, after))" in SRC)
    ok("撤销栈上限与回推都在", "undoStack.length > UNDO_MAX" in SRC and "undoStack.push(e)" in SRC)
    ok("手动「保存设置」前抓整份快照（撤销能整段写回）",
       "const snapAll = cfg ? JSON.parse(JSON.stringify(cfg))" in SRC
       and "pushUndo({kind:'all', snapshot:snapAll" in SRC)
    ok("撤销按类型分流（单键用 before，整份用 snapshot）",
       "e.kind === 'all' ? e.snapshot : oneKey(e.path, e.before)" in SRC)
    ok("打码值不进撤销栈（写回没意义会骗人）", "isMaskedVal(before)" in SRC and "isMaskedVal(v)" in SRC)
    ok("开关状态记在本机浏览器（刷新后保持）", "localStorage.getItem(AUTOAPPLY_KEY)" in SRC
       and "localStorage.setItem(AUTOAPPLY_KEY" in SRC)
    ok("自绘下拉的 change 带冒泡（否则委托收不到这一下）",
       "new Event('change', {bubbles:true})" in SRC)
    ok("省 token 卡片的监听器只接一次（老毛病：反复 syncToForm 会越积越多）",
       "if(!tb._wired)" in SRC and "tb._wired = true" in SRC)
    ok("中文文案齐（生效/撤销/兜底提示）",
       all(t in SRC for t in ("已生效：", "↩ 已撤销：", "没有可撤销的修改", "自动生效失败：")))

    print("== C. JS 语法（node --check）+ 阴性对照 ==")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.S)
    js_all = "\n;\n".join(blocks)
    tmp = os.path.join(tempfile.gettempdir(), "pm_autosave_check.js")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(js_all)
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60)
        ok("console_html 的 JS 过语法检查", r.returncode == 0,
           (r.stderr or "").strip().splitlines()[-1][:120] if r.returncode else "%d 字符" % len(js_all))
    except Exception as e:
        ok("console_html 的 JS 过语法检查", False, "%s: %s" % (type(e).__name__, e))

    # 阴性对照：把关键接线从源码里抹掉，判据必须能变红（证明这些断言不是恒真）
    broken = SRC.replace("id=\"autoApplyChk\"", "id=\"autoApplyChkX\"").replace("postCfg(oneKey(path, after))", "postCfg(cfg)")
    ok("阴性对照：抹掉开关与单键 POST 后，对应断言确实会失败",
       not has('id="autoApplyChk"', broken) and not has("await postCfg(oneKey(path, after))", broken)
       and has('id="autoApplyChk"') and has("await postCfg(oneKey(path, after))"))
    broken2 = SRC.replace("new Event('change', {bubbles:true})", "new Event('change')")
    ok("阴性对照：去掉冒泡后能检测出来（这条真会被漏掉）", "new Event('change', {bubbles:true})" not in broken2)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
