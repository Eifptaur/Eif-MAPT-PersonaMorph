# -*- coding: utf-8 -*-
"""窗口/控制台「外观统一」判据（2026-09-15 用户当面点的六条）

守的东西（都是**用户原话对应的可机械核对项**）：
  ① 边框太大 → `_ring` 必须是 3（不是 6），全屏时内边距必须为 0；
  ② 全屏 + ESC → 必须有 `ApplyChrome()`（最大化 ⇒ Padding(0)）与 `EscFilter`（WM_KEYDOWN+VK_ESCAPE
     退出全屏，用 IMessageFilter 而不是 KeyPreview——WebView2 是原生子窗）；
  ③ 顶栏三按钮不统一 → 三个按钮必须都是**自绘** `GlyphButton`（Min/Max/Close），尺寸一致（34×26），
     且**不许再出现** `—` / `✕` / `❐` 这类文本字形按钮；
  ④ 三个状态胶囊不统一 → `.chip` 必须统一高度（26px）、余额值走 `<b>`、复选框固定尺寸；
  ⑤ 滚动条太明显 → 不再有 8px 的 `var(--input-bd)` 那套，thumb 默认透明、父级悬停才显形；
  ⑥ 收起位置/按钮 → `.shell.tight` 栅格列宽跟着收、`.side.tight{width:100%}`、
     `.nav-tg` 是**整行**（position:static;width:100%）且文案是「收起 / 展开」。

用法：py -3 scripts/console_chrome_selftest.py
"""
import io
import os
import re
import socket
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP_N = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, why=""):
    global SKIP_N
    SKIP_N += 1
    print("  SKIP {}  [{}]".format(name, why))


def rd(p):
    return io.open(os.path.join(ROOT, p), encoding="utf-8", errors="replace").read()


lc = rd("launcher-src/launcher.cs")
wg_src = rd("launcher-src/wingliphs.cs")
sk = rd("launcher-src/stylekit.cs")
page = rd("agent/console_html.py")

print("== 窗口/控制台外观判据 ==")

print("\n── ① 边框与全屏内边距 ──")
ok("缩放环收成 3px（原 6px）", re.search(r"int _ring = 3;", lc) is not None)
ok("有 ApplyChrome()（最大化 ⇒ Padding(0)）",
   "void ApplyChrome()" in lc and "new Padding(0)" in lc and "FormWindowState.Maximized" in lc)
ok("OnLoad 里调用了 ApplyChrome（不再直接写死 Padding）",
   "_ring = r;" in lc and "ApplyChrome();" in lc)

print("\n── ② 全屏与 ESC ──")
ok("有 EscFilter（IMessageFilter）", "class EscFilter" in lc and "IMessageFilter" in lc)
ok("按键判定是 WM_KEYDOWN + VK_ESCAPE",
   "0x0100" in lc and "0x1B" in lc)
ok("过滤器已挂上（AddMessageFilter）", "AddMessageFilter(new EscFilter(this))" in lc)
ok("EscFilter 退出全屏后会还原内边距与按钮字形",
   "_f.ApplyChrome()" in lc and "GlyphKind.Max" in lc)

print("\n── ③ 顶栏三个按钮：自绘且统一 ──")
ok("新文件 wingliphs.cs 在", os.path.exists(os.path.join(ROOT, "launcher-src", "wingliphs.cs")))
ok("四种字形齐全（Min/Max/Restore/Close）",
   all(k in wg_src for k in ("Min", "Max", "Restore", "Close")) and "enum GlyphKind" in wg_src)
ok("同一支笔、同尺寸：笔宽按高度等比 + 字形边长统一",
   "float pen = Math.Max(1.2f, Height / 18f)" in wg_src and "float s = Math.Max(8f" in wg_src)
ok("悬停底色三按钮同一套", "_hover" in wg_src and "_down" in wg_src and wg_src.count("Color.FromArgb(") >= 2)
ok("三个按钮都是 GlyphButton（min/max/close）",
   lc.count("new GlyphButton()") >= 3 and 'GlyphKind.Min' in lc and 'GlyphKind.Max' in lc and 'GlyphKind.Close' in lc)
ok("三个按钮尺寸一致（34×26）", lc.count("Size = new Size(34, 26)") >= 3, lc.count("Size = new Size(34, 26)"))
bad_glyph = [g for g in ("\"—\"", "\"✕\"", "\"❐\"") if g in lc]
ok("不再有文本字形按钮（— / ✕ / ❐）", not bad_glyph, bad_glyph)

print("\n── ④ 三个状态胶囊统一 ──")
ok(".chip 统一高度 26px + box-sizing", ".chip{display:inline-flex" in page and "height:26px;box-sizing:border-box" in page)
ok("余额值走 <b>（与「模型」同构）", '余额 <b id="balance-badge"' in page)
ok("复选框固定尺寸（不撑开胶囊）", "#autoChip input{width:14px;height:14px" in page)
ok("余额 JS 不再往胶囊里写「余额：」前缀",
   "el.textContent=b.error;" in page and "el.textContent = '未配置'" in page and "'余额：'" not in page)

print("\n── ⑤ 滚动条 ──")
# 只看**滚动条规则**里的宽度（`width:8px` 也可能是状态圆点那种无关元素——第一版自检就在这里误红了）
_bad_w = re.findall(r"scrollbar\{width:(\d+)px", page)
ok("没有 ≥8px 的滚动条（旧那套 8px + input-bd 已去掉）",
   all(int(w) <= 6 for w in _bad_w) and "background:var(--input-bd);border-radius:4px" not in page, _bad_w)
ok("thumb 默认透明、父级悬停才显形",
   "::-webkit-scrollbar-thumb{background:transparent" in page and ".side:hover::-webkit-scrollbar-thumb" in page)
ok("导航内部那条也改成悬停才显形", ".side .nav:hover::-webkit-scrollbar-thumb" in page)

print("\n── ⑥ 收起位置与按钮 ──")
ok("栅格列宽跟着收（.shell.tight）", ".shell.tight{grid-template-columns:64px 1fr" in page)
ok("收起态侧栏宽度跟列走（width:100%）", ".side.tight{width:100%}" in page)
ok("收起按钮是一整行（static + 100%）", ".side .nav-tg{position:static;width:100%" in page)
ok("文案是「收起 / 展开」", "‹ 收起" in page and "› 展开" in page)
ok("JS 同步切换 .shell.tight（不是只切 .side）",
   "shellEl.classList.toggle('tight', on)" in page and "shellEl.classList.toggle('tight', now)" not in page)

print("\n── ⑦ 真起控制台抓页面复核 ──")
_p = None
try:
    from agent import webui as W
    _orig = W.get_config
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    base = dict(W.get_config() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": free,
                      "token": "chrome-judge", "auto_open_browser": False}
    W.get_config = lambda: base
    w = W.WebUI(lambda: {}, [])
    port = w.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=chrome-judge" % port, timeout=8) as r:
            _p = r.read().decode("utf-8", "replace")
    finally:
        w.stop()
except Exception as e:
    skip("真起控制台", "起不了：%s" % e)
finally:
    try:
        W.get_config = _orig
    except Exception:
        pass

if _p is None:
    skip("页面复核", "没抓到页面")
else:
    ok("页面里有 .shell.tight 规则", ".shell.tight{grid-template-columns:64px 1fr" in _p)
    ok("页面里三个胶囊都是同一套 chip 结构",
       _p.count('class="chip"') >= 2 and 'class="chip" id="autoChip"' in _p)
    ok("页面里余额是 <b> 且无「余额：」硬编码", '余额 <b id="balance-badge"' in _p and "余额：" not in _p)
    ok("收起按钮文案在页面里", "‹ 收起" in _p)

print("\n── ⑧ 鲸鱼光标：图片 URL 必须稳定（2026-09-16 用户报「点击就直接变回普通的」）──")
ok("不再每次点击都拼 ?v=Date.now()（那会强制重新下载，加载期间回退系统箭头）",
   "setStyle(url + '?v=' + Date.now())" not in page
   and "setStyle(nodUrl + '?v=' + Date.now())" not in page)
ok("版本号只在 setCustom（图真的换了）里变一次", "ver = '?v=' + Date.now()" in page)
ok("启用时预加载两张光标图", "function preload()" in page and "preload(); apply();" in page)
ok("mousedown 仍然切点头帧、180ms 后换回",
   "applyNod();" in page and "nodTimer = setTimeout(apply, 180)" in page)

print("\n── ⑨ 暂停/恢复：不许靠按钮文字决定接口，且**方向不能反**（2026-09-16 用户报「很不灵敏」+「点暂停显示已恢复」）──")
ok("不再用按钮文字判断该调哪个接口（文字是轮询刷新的，会调反）",
   "textContent.includes('暂停')?'/api/pause'" not in page)
ok("有状态变量作唯一依据", "window.__pausedNow" in page)
ok("**方向正确**：想要暂停 ⇒ 调 /api/pause（我第一版曾整个调反）",
   "wantPaused ? '/api/pause' : '/api/resume'" in page)
ok("**文字方向正确**：想要暂停 ⇒ 按钮/运行状态写「已暂停」、下一步写「恢复」",
   "wantPaused ? '恢复' : '暂停'" in page
   and "wantPaused ? '已暂停' : '运行中'" in page)
ok("**提示方向正确**：想要暂停 ⇒ toast 说「已暂停」",
   "wantPaused ? '已暂停：" in page)
ok("点击后立刻进入处理中态（禁用 + 改字）",
   "btn.disabled = true" in page and "暂停中…" in page and "恢复中…" in page)
ok("失败要恢复原状并如实报错", "btn.textContent = oldText" in page)

# 真值表复算：把上面那段 JS 的判定用 Python 等价写一遍，断言"点一下"的结果符合直觉。
# 上一版自检只查了「有状态变量」，**没查方向**，所以没拦住我把两个分支写反（点暂停发 resume）。
def _pause_step(paused_now):
    want_paused = not paused_now
    return {"call": "/api/pause" if want_paused else "/api/resume",
            "afterPaused": want_paused,
            "btn": "恢复" if want_paused else "暂停",
            "run": "已暂停" if want_paused else "运行中"}


_t0 = _pause_step(False)
ok("真值表：运行中点一下 ⇒ 暂停（调 pause、状态变已暂停、按钮变恢复）",
   _t0["call"] == "/api/pause" and _t0["afterPaused"] is True
   and _t0["btn"] == "恢复" and _t0["run"] == "已暂停", _t0)
_t1 = _pause_step(True)
ok("真值表：暂停中点一下 ⇒ 恢复（调 resume、状态变运行中、按钮变暂停）",
   _t1["call"] == "/api/resume" and _t1["afterPaused"] is False
   and _t1["btn"] == "暂停" and _t1["run"] == "运行中", _t1)

print("\n── 顶栏状态行（2026-09-16 待拍板三件之一：暂停态 / 监听 N 个会话 / 主人登记 N 项）──")
_page = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_pm = open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("后台真的给了监听数（listen.groups / listen.privates）",
   '"listen": {"groups"' in _pm and '"privates"' in _pm)
ok("私聊数取自 list_private_targets（与监听目标同一来源，不另算一份）",
   "len(wechat.list_private_targets())" in _pm)
ok("顶栏有两个状态位（监听 / 主人登记）",
   'id="listen-badge"' in _page and 'id="owner-badge"' in _page)
ok("拿不到数据时显示 ? 而不是猜 0（猜 0 会让人以为没在监听）",
   "lb.textContent = '?'" in _page and "ob.textContent = '?'" in _page)
ok("暂停态复用既有 runText chip（不另起一个说法）",
   "$('runText').textContent = s.paused ? '已暂停' : '运行中'" in _page)
ok("主人数取自 /api/status 的 owner.count（与「微信」面板同一份登记）",
   "s.owner.count" in _page and 'st["owner"]' in open(
       os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read())

# ── 2026-09-17（网友报「我把群勾选了，然后保存设置刷新之后又没了」）──
ok("群白名单 chips 容器带 `data-cfg`（否则各分区「保存设置」收集不到它、刷新就丢）",
   'id="wlChips" data-cfg="wechat.group_name_white_list"' in _page)
ok("「检测群聊并勾选」的确定按钮**当场落盘**（POST /api/config），不再只改页面变量",
   "setPath(cfg,'wechat.group_name_white_list', wlList.slice());" in _page
   and _page.count("wechat.group_name_white_list") >= 4)
ok("群白名单保存失败会如实报（不许静默丢）", "群白名单保存失败" in _page)

print("")
print("窗口/控制台外观判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP_N))
sys.exit(1 if FAIL else 0)
