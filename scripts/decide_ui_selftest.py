# -*- coding: utf-8 -*-
"""版本不匹配「四选一」控制台接线 判据。

口径：「弹窗按你推荐的做」+「✕＝什么都不做」+「把弹窗切出来的那一秒，
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录的 `_srcmatch`
import _srcmatch as _sm # noqa: E402 空白容忍的源码断言
os.chdir(ROOT)

from agent import pending_decisions as PD # noqa: E402

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
H = open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()


def seg(src, start, end, what):
    i = src.find(start)
    if i < 0:
        print("  （切片失败：找不到 %s）" % what)
        return ""
    j = src.find(end, i + len(start)) # ⚠️ 从 start 之后找，否则 start 本身就把 end 命中了（切片成空）
    return src[i:j if j > 0 else i + 4000]


print("── A. 服务端：状态暴露 + 唯一表态入口 ──")
status_blk = seg(W, 'st["version_gate"] = _vg2.status()', 'st["wechat_install"]', "status 段")
ok("/api/status 暴露 pending_decisions", 'st["pending_decisions"]' in status_blk)
ok("带 open 计数 / summary / needed / item 四个字段",
   all(k in status_blk for k in ('"open"', '"summary"', '"needed"', '"item"')))
ok("只把**还开着**的单子给弹窗（已表态的不再弹）",
   _sm.has(status_blk, 'if str(_pit.get("status")) == "open"'))
ok("开单走 version_gate.pending（幂等）", "_vg2.pending()" in status_blk)
decide_blk = seg(W, 'elif path == "/api/decide"', "elif path ==", "decide 段")
if "_vg5.decide(" not in decide_blk:
    # ⛔ 这条路由的分支体已搬成 `_rapi_decide_post`（路由表里登记）
    #   ⇒ 段落改从**方法**取；没搬时仍走上面那一句（旧结构照样能跑）。
    decide_blk = seg(W, "def _rapi_decide_post(", "    def ", "decide 段（方法）")
ok("POST /api/decide 在位", bool(decide_blk))
ok("表态走 version_gate.decide（落台账 + 写回矩阵）", "_vg5.decide(" in decide_blk)
ok("读的是 JSON body 里的 id/choice", '"id"' in decide_blk and '"choice"' in decide_blk)
ok("异常如实回 500 + error（不静默成功）", _sm.has(decide_blk, '"ok": False, "error": str(e)}, 500'))
ok("服务端不做任何安装/降级动作",
   all(k not in decide_blk for k in ("subprocess", "pip", "uninstall", "downgrade")))

print("── B. 控制台：自绘多选一弹窗 + 数据驱动选项 ──")
ok("choiceBox 已定义", _sm.has(H, "function choiceBox("))
# [口径变更] 版本门默认 `_strict()=False`（未实测版本本来就**照发**）⇒ 默认**非模态**。
#   弹窗外壳因此分两档：`soft`（右下角浮卡，不挂 mask）与 `hard`（遮罩模态，只在 strict 下用）。
#   判据改成守"两种模式都在、且 non-strict 不许挂遮罩"这条真红线。
_cb = seg(H, "function choiceBox(", "/* 版本不匹配那张单", "choiceBox 全段")
ok("弹窗外壳分两档 soft/hard（soft 不挂遮罩、hard 才挂）",
   _sm.has(_cb, "const hard = (mode === 'hard')") and _sm.has(_cb, "hard ? 'mask' : 'choice-soft'")
   and _sm.has(_cb, "if(hard) maskOpen(m)"))
ok("openDecision 按后端 `strict` 选模式（非严格⇒soft 不挡路）",
   _sm.has(H, "const mode = item.strict ? 'hard' : 'soft'") and "mode)" in H)
ok("非模态卡片有样式（右下角浮卡，不铺满）", ".choice-soft{position:fixed;right:20px;bottom:20px" in H)
ok("弹窗用的是既有的 mask/box/果冻图标（显示层自研）",
   "m.className" in _cb and "ICON" in _cb)
cb = seg(H, "function choiceBox(", "/* 版本不匹配那张单", "choiceBox 全段")
ok("每个选项一个按钮、文案与说明都来自数据",
   "data-opt=" in cb and "o.key" in cb and "o.detail" in cb and "o.label" in cb)
ok("「什么都不做」是可点的选项（data-opt 空值）", 'data-opt=""' in cb and "什么都不做" in cb)
ok("模态下点遮罩也等于什么都不做", _sm.has(cb, "if(hard) m.onclick") and "fire('')" in cb)
ok("同一张单只回调一次（防连点）", _sm.has(cb, "if(fired) return; fired = true"))
# ⛔ 用户 指正"其他都有 bug，只能选什么都不做" ⇒ 后端 `disabled` 必须落到按钮上
ok("后端标了 disabled 的选项在页面上**真的置灰**并写明原因",
   "o.disabled" in cb and _sm.has(cb, "disabled title=") and ("opacity:.5" in cb or "not-allowed" in cb))
# [⑦d 口径变更] 页面里现在**允许**出现 choice key 与选项中文名 —— 它们要用于两处**显示/触发**：
#   ①「最近表态」把 key 翻成中文（nm 映射表）②面板按钮唤起对应动作（actUp→upgrade_adapter）。
#   这两处不是"第二份选项表"⇒ 自检改成守真正的红线：**选项三件套（key+label+detail）仍只由后端给**，
#   页面里不许出现只有后端才有的 detail 文案。
_detail_marks = ("把微信适配层升到与本机微信版本配套的那个版本", "跑一次依赖自愈与版本体检",
                 "给你一份排查步骤（怎么看微信版本")
ok("选项表仍由后端给（页面里没有第二份 key+label+detail 的选项对象）",
   not any(d in H for d in _detail_marks) and "o.detail" in cb)
ok("页面里的 key 只用于两处：表态历史的中文映射 + 面板按钮触发",
   _sm.has(H, "nm = {upgrade_adapter:") and _sm.has(H, "actBtn('actUp', 'upgrade_adapter'")
   and _sm.has(H, "actBtn('actHeal', 'update_host'"))
ok("后端确实带着这四个选项", len(PD.VERSION_OPTIONS) == 4 and
   [o["key"] for o in PD.VERSION_OPTIONS] == ["upgrade_adapter", "update_host", "allow_once", "wechat_side"])
# ⛔ 文案铁律（用户 当面指正）：两套 UI 的选项名必须**逐字一致**，且不许出现
#   "微信本身要处理"那种把「微信」当我家产品说的说法。
_lbs = [o["label"] for o in PD.VERSION_OPTIONS]
ok("选项名口径：四个 label 与页面映射表逐字一致",
   all(('"%s"' % lb) in H or ("'%s'" % lb) in H for lb in _lbs), "、".join(_lbs))
ok("页面里没有旧说法（仅本次允许 / 微信本身要处理）",
   "仅本次允许" not in H and "微信本身要处理" not in H)

print("── C. 自动弹一次 + 手动入口 ──")
ok("openDecision 用 POST /api/decide 提交", "openDecision" in H and "'/api/decide'" in H and _sm.has(H, "JSON.stringify({id: item.id, choice"))
ok("同一张单本次运行只自动弹一次", "__pdShown" in H and "window.__pdShown[pd.item.id]" in H)
ok("只有还开着的单子才自动弹", _sm.has(H, "pd.item && pd.item.id && !window.__pdShown"))
ok("自动弹延后一点（不抢正在输入的那一下）", _sm.has(H, "setTimeout(function(){ openDecision(pd.item); }"))
ok("面板里有手动入口按钮", 'id="pdOpen"' in H and "版本不匹配怎么办" in H)
ok("手动入口读 /api/status 拿当前这张单", "pdOpen" in H and _sm.has(H, "const pd = (s && s.pending_decisions) || {}"))
ok("待状态行在位", 'id="pdStat"' in H and "待" in H)
# [⑦d 口径变更] 「升级命令」不再只回显给用户抄：选这两项会**真去跑**（后台作业）。
#   [再改] 还要能**如实回执**：`ran:false`（依赖已齐全 / 拿不到命令）时不许说
#   "已经在后台跑了" —— 那就是**看起来点了没反应**（界面上没有任何可见反馈）。
ok("选了能修的两项真去跑（postVersionAction）", _sm.has(H, "postVersionAction(key, item.id)"))
ok("升级命令仍然看得见（从动作结果里回显）", "r2.cmd" in H or _sm.has(H, "result) || {}).cmd"))
ok("★没真动手时如实说（ran:false ⇒ 不说「已经在后台跑了」）",
   "!r2.ran" in H and "这一项现在不用做" in H)

print("── D. 文案口径（不讲黑话；讲清 ✕ 的含义与「微信」指谁）──")
hint = seg(H, 'id="pdOpen"', "</div></div>", "待提示")
ok("提示里讲清 ✕＝什么都不做、不再追问、**不影响发送**",
   "什么都不做" in hint and "不再追问" in hint and "不影响发送" in hint)
ok("提示里给出四条新选项名（用户看得到有哪些选择）",
   all(k in hint for k in _lbs), hint.replace("\n", " ")[:80])
ok("★提示与选项里「微信」只指客户端（不出现把产品当微信说的写法）",
   "微信本身要处理" not in hint and "微信窗口" not in hint)

print("── E. 真跑：整页 JS 过 node --check──")
from agent import console_html as CH # noqa: E402
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
