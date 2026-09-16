# -*- coding: utf-8 -*-
"""版本不匹配「四选一」控制台接线 判据（2026-09-14，测机报告 ⑦ 控制台那一半）。

口径（用户 2026-09-14）：「弹窗按你推荐的做」+「✕＝什么都不做」+「把弹窗切出来的那一秒，
就应该立刻让它到后台」。本判据守**接线**这一层：
  ① `/api/status` 把待决单暴露给控制台（open/summary/needed/item）；
  ② `POST /api/decide` 是唯一入口，落台账 + 写回矩阵，任何一条选项都**不会**在这里装包/降级；
  ③ 控制台有自绘多选一弹窗 `choiceBox`，**选项来自后端数据**（不许在页面里写死四个选项）；
  ④ 同一张单本次运行只自动弹一次（`__pdShown`），且「什么都不做」也是一个可点的选项；
  ⑤ 手动入口（「版本不匹配怎么办」按钮）在能力矩阵面板里。
用法：py -3 scripts/decide_ui_selftest.py
"""
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import pending_decisions as PD    # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


W = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
H = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()


def seg(src, start, end, what):
    i = src.find(start)
    if i < 0:
        print("  （切片失败：找不到 %s）" % what)
        return ""
    j = src.find(end, i + len(start))          # ⚠️ 从 start 之后找，否则 start 本身就把 end 命中了（切片成空）
    return src[i:j if j > 0 else i + 4000]


print("── A. 服务端：状态暴露 + 唯一表态入口 ──")
status_blk = seg(W, 'st["version_gate"] = _vg2.status()', 'st["wechat_install"]', "status 段")
ok("/api/status 暴露 pending_decisions", 'st["pending_decisions"]' in status_blk)
ok("带 open 计数 / summary / needed / item 四个字段",
   all(k in status_blk for k in ('"open"', '"summary"', '"needed"', '"item"')))
ok("只把**还开着**的单子给弹窗（已表态的不再弹）",
   'if str(_pit.get("status")) == "open"' in status_blk)
ok("开单走 version_gate.pending（幂等）", "_vg2.pending()" in status_blk)
decide_blk = seg(W, 'elif path == "/api/decide"', "elif path ==", "decide 段")
ok("POST /api/decide 在位", bool(decide_blk))
ok("表态走 version_gate.decide（落台账 + 写回矩阵）", "_vg5.decide(" in decide_blk)
ok("读的是 JSON body 里的 id/choice", '"id"' in decide_blk and '"choice"' in decide_blk)
ok("异常如实回 500 + error（不静默成功）", '"ok": False, "error": str(e)}, 500' in decide_blk)
ok("服务端不做任何安装/降级动作",
   all(k not in decide_blk for k in ("subprocess", "pip", "uninstall", "downgrade")))

print("── B. 控制台：自绘多选一弹窗 + 数据驱动选项 ──")
ok("choiceBox 已定义", "function choiceBox(" in H)
ok("弹窗用的是既有的 mask/box/果冻图标（显示层自研）",
   "m.className='mask'" in seg(H, "function choiceBox(", "function openDecision", "choiceBox") and
   "ICON" in seg(H, "function choiceBox(", "function openDecision", "choiceBox"))
cb = seg(H, "function choiceBox(", "/* 版本不匹配那张单", "choiceBox 全段")
ok("每个选项一个按钮、文案与说明都来自数据",
   "data-opt=" in cb and "o.key" in cb and "o.detail" in cb and "o.label" in cb)
ok("「什么都不做」是可点的选项（data-opt 空值）", 'data-opt=""' in cb and "什么都不做" in cb)
ok("点遮罩也等于什么都不做", "ev.target === m" in cb and "fire('')" in cb)
ok("同一张单只回调一次（防连点）", "if(fired) return; fired = true" in cb)
# [⑦d 口径变更] 页面里现在**允许**出现 choice key 与选项中文名 —— 它们要用于两处**显示/触发**：
#   ①「最近表态」把 key 翻成中文（nm 映射表）②面板按钮唤起对应动作（actUp→upgrade_adapter）。
#   这两处不是"第二份选项表"⇒ 自检改成守真正的红线：**选项三件套（key+label+detail）仍只由后端给**，
#   页面里不许出现只有后端才有的 detail 文案。
_detail_marks = ("把适配层升到与本机微信配套的版本", "跑一次依赖自愈与版本体检", "只给排查指引")
ok("选项表仍由后端给（页面里没有第二份 key+label+detail 的选项对象）",
   not any(d in H for d in _detail_marks) and "o.detail" in cb)
ok("页面里的 key 只用于两处：表态历史的中文映射 + 面板按钮触发",
   "nm = {upgrade_adapter:" in H and "actBtn('actUp', 'upgrade_adapter'" in H
   and "actBtn('actHeal', 'update_host'" in H)
ok("后端确实带着这四个选项", len(PD.VERSION_OPTIONS) == 4 and
   [o["key"] for o in PD.VERSION_OPTIONS] == ["upgrade_adapter", "update_host", "allow_once", "wechat_side"])

print("── C. 自动弹一次 + 手动入口 ──")
ok("openDecision 用 POST /api/decide 提交", "openDecision" in H and "'/api/decide'" in H and "JSON.stringify({id: item.id, choice" in H)
ok("同一张单本次运行只自动弹一次", "__pdShown" in H and "window.__pdShown[pd.item.id]" in H)
ok("只有还开着的单子才自动弹", "pd.item && pd.item.id && !window.__pdShown" in H)
ok("自动弹延后一点（不抢正在输入的那一下）", "setTimeout(function(){ openDecision(pd.item); }" in H)
ok("面板里有手动入口按钮", 'id="pdOpen"' in H and "版本不匹配怎么办" in H)
ok("手动入口读 /api/status 拿当前这张单", "pdOpen" in H and "const pd = (s && s.pending_decisions) || {}" in H)
ok("待拍板状态行在位", 'id="pdStat"' in H and "待拍板 " in H)
# [⑦d 口径变更] 「升级命令」不再只回显给用户抄：选这两项会**真去跑**（后台作业，778 行那条）。
#   自检改成守"命令仍然看得见 + 真的发出去了"两件事。
ok("选了能修的两项真去跑（postVersionAction）", "postVersionAction(key, item.id)" in H)
ok("升级命令仍然看得见（从动作结果里回显）", "a.result) || {}).cmd" in H or "result || {}).cmd" in H or "result.cmd" in H)

print("── D. 文案口径（不写解释性括号；讲清 ✕ 的含义）──")
hint = seg(H, 'id="pdOpen"', "</div></div>", "待拍板提示")
ok("提示里讲清 ✕＝什么都不做、不再追问", "什么都不做" in hint and "不再追问" in hint)
ok("提示里没有解释性括号", "（" not in hint, hint.replace("\n", " ")[:70])
ok("给出四条选项的名字（用户看得到有哪些选择）",
   all(k in hint for k in ("一键升级适配层", "更新本体", "仅本次允许", "微信本身要处理")))

print("── E. 真跑：整页 JS 过 node --check（手写弹窗最怕在这一层翻车）──")
from agent import console_html as CH       # noqa: E402
blocks = re.findall(r"<script[^>]*>(.*?)</script>", CH.HTML, re.S)
ok("抽到了 script 块", len(blocks) > 0, "%d 块" % len(blocks))
js_all = "\n;\n".join(blocks)
tmp = os.path.join(tempfile.gettempdir(), "pm_console_decide_check.js")
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(js_all)
try:
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tail = (r.stderr or "").strip().splitlines()
    ok("整页 JS 语法通过", r.returncode == 0, (tail[-1][:140] if r.returncode and tail else "%d 字符" % len(js_all)))
except Exception as e:
    ok("整页 JS 语法通过", False, "%s: %s" % (type(e).__name__, e))
ok("新函数确实进了页面（choiceBox / openDecision 各一处定义）",
   CH.HTML.count("function choiceBox(") == 1 and CH.HTML.count("function openDecision(") == 1)

print("\n通过 %d / 失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
