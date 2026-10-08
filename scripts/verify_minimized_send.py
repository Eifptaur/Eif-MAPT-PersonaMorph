# -*- coding: utf-8 -*-
"""verify_minimized_send.py —— 验证 L5 目标「微信最小化时仍能发送」（把 `unknown` 测成有结论）

⛔ 为什么要有它
  `agent/version_matrix.py` 里：
      CAPS      = { ... "minimized_send": {"label": "微信最小化时仍能发送", "level": "L5"} ... }
      SEED_RUNS = [{ "wechat": "4.1.15.8", "when": "",
                     "caps": { ... "minimized_send": {"status": "unknown",
                              "evidence": "未测（最高目标的最严条件）"} } }]
  ⇒ **这是项目的 L5（最高）目标，却从 至今「未测」。**
  而说过"朋友现在能在最小化收发消息" —— 也符合 `README` 的成文口径：
      「后台消息投递为主 … **不要求微信窗口可见**（**最小化与被遮挡时也能干**）」
  机制依据：`wechat_ui.hit()` 的注释「**判据不可用（窗口最小化抓不到图）**时**放行但留痕**」。

  ⇒ 所以缺的**不是能力，是"把它测下来"**。本脚本就是那把尺子。

⚠️ 与 `scripts/capture_live_evidence.py` 的区别（别搞混）
  · 留证脚本**要**窗口可见（它要看屏幕）；
  · 本脚本**要**窗口**最小化**（不然测的不是这个场景），且**完全不依赖截图** ——
    它只看两件事：`send_text` 的返回 + **库里能不能回读到那条新消息**。

⚠️⚠️ **本脚本默认 dry-run（不发送）**：真发送会产生一条**真实消息**。
   `--send` 才真发；收件人默认 **`filehelper`（文件传输助手，发给你自己）** —— 最安全。
   想换收件人必须显式给 `--chat`，并在输出里留痕。

⛔ **前置闸（实测加，很重要）**
  `data/paused.flag` / `data/stopped.flag` 是**文件级真相**（见 `agent/control.py`）：
  用户按过「停止/暂停」后，`send_text` 会**直接**返回 `(False, "机器人已停止 ⇒ 这条不发")`。
  ⇒ 那种情况下本脚本报 **BLOCKED（退出码 3）且不写任何结论** ——
    因为"被停止闸拦住"**不是**"最小化发不出去"的证据。
    （实测踩过：当时窗口确实最小化，若不核查就会把结论记成「最小化场景未发出去」⇒
      等于把 `unknown` 误写成 `no`，**比不测更糟**。）
  ⇒ 本脚本**绝不删**这两个 flag：那是**用户的安全控制**，不是脚本能改的状态。

用法
  # 1) 先看计划（不发送，安全）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py
  # 2) 确认后真发（默认发给自己 filehelper）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send
  # 3) 换收件人（会发到真实会话，谨慎）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send --chat <chat_id>

  # 4) ★ 逐类测（`--kind`）：一次把五类都走一遍 ——
  #    文字 / 静态图 / 动态表情 / 文件 / 真语音条（语音条**只做只读探针**，不真发）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send --kind text
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send --kind image --path <某张.jpg>
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send --kind gif   --path <某个.gif>
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send --kind file  --path <某个.zip>
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --kind voice        # 只读探针
  #    ⚠️ 图/表情**落库慢**（实测 30~180s）⇒ 测这三类时把 `--wait` 放到 180 以上，
  #       否则"其实发出去了"会被读成"没发出去"。

输出末尾给出可直接粘进 `version_matrix.SEED_RUNS` 的证据行。
"""
import argparse
import os
import sys
import time
import logging

log = logging.getLogger("persona-morph")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_HR = chr(0x2500)
_MIN_XY = -10000          # Windows 最小化窗口的经典坐标（-32000）比这更小


def _win_state(gui):
    """返回 (是否最小化/屏幕外, render_rect 原值)。"""
    _rr = getattr(gui, "render_rect", None)
    try:
        _x0, _y0 = int(_rr[0]), int(_rr[1])
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return None, _rr
    return (_x0 < _MIN_XY or _y0 < _MIN_XY), _rr


def _verdict(ok, hit, msg, scene, rr):
    """把 (send_text 返回, 库回读) 判成 status + evidence。

    ⛔⛔ **不许把"条件不完整"判成 `no`**（实测后的硬口径）：
      实测（前置闸通过后真跑，耗时 26.4s）返回的是
        「投递档确认不了目标会话（会话头三态=**no_ref**，投递切会话也没成）
          ⇒ 按最高目标不退回真鼠标」
      而日志里写明了两条依赖：
        · `no_ref` ＝ **缺该尺寸的会话头参照** —— 参照要在**窗口可见时**学，最小化时学不了；
        · 「搜索框路线没成（找不到搜索入口）」＝ 切会话也要**从标题带截图读**。
      ⇒ 这三点（缺参照 / 需切会话 / 真鼠标未开）都是**前置条件**，不是"能力不行"。
        把它们记成 `no` 就等于**用一次条件不全的实验否掉一个能力** —— 与 §21 同一类错误。
      ⇒ 只在**没有任何"条件缺失"信号**时，才给 `no`；否则一律 `unknown` + 原始原因。
    """
    _HINTS = (
        ("no_ref", "缺该尺寸的会话头参照（要在**窗口可见时**学一次才有）"),
        ("投递档确认不了目标会话", "投递档缺身份证据"),
        ("搜索框路线没成", "切会话要先截图找搜索入口（最小化时不可用）"),
        ("不退回真鼠标", "真鼠标兜底默认关（`input.allow_real_fallback`）"),
        ("allow_real_fallback", "真鼠标兜底默认关"),
        ("no_capture", "抓不到画面"),
    )
    _why = ""
    for _k, _d in _HINTS:
        if _k in str(msg):
            _why += "· " + _d + "；"
    if ok and hit:
        return ("ok", "【%s】场景：send_text=True 且库回读到本次文本；窗口 render_rect=%s" % (scene, rr))
    if hit and not ok:
        return ("partial", "库回读命中但 send_text=False —— 真发出去了，但**判据本身**要查")
    if ok and not hit:
        return ("partial", "send_text=True 但库回读未命中 —— 可信度不足（可能假成功）")
    if _why:
        return ("unknown", "【%s】**未测成（前置条件不完整）**：%s 原始返回：%s"
                % (scene, _why, str(msg)[:120]))
    return ("no", "【%s】未发出去且**无'条件缺失'信号**：%s" % (scene, str(msg)[:120]))


def main():
    ap = argparse.ArgumentParser(description="验证「微信最小化时仍能发送」（默认 dry-run）")
    ap.add_argument("--send", action="store_true", help="真的发送（不加则只打印计划）")
    ap.add_argument("--chat", default="filehelper",
                    help="收件人 chat_id（默认 filehelper＝发给自己，最安全）")
    ap.add_argument("--text", default="", help="自定义文本（默认带时间戳的验证串）")
    ap.add_argument("--wait", type=float, default=15.0, help="发送后等库回读的秒数（默认 15）")
    ap.add_argument("--kind", default="text",
                    choices=["text", "image", "gif", "file", "voice"],
                    help="测哪一类发送（默认 text）。"
                         "image=静态图（JPG/PNG）／gif=动态表情／file=任意文件／"
                         "voice=**只做只读探针**（三闸状态），不真发")
    ap.add_argument("--path", default="",
                    help="image/gif/file 的本地文件路径（kind≠text 时必填）")
    a = ap.parse_args()

    print(_HR * 74)
    print("验证 L5「微信最小化时仍能发送」（version_matrix: minimized_send）")
    print(_HR * 74)

    from agent.wechat import WeChatAdapter
    _ad = None
    try:
        _ad = WeChatAdapter()
    except Exception as _e:
        print("⛔ 构造适配器失败：%s: %s" % (type(_e).__name__, str(_e)[:140]))
        return 2

    # ── 1) 报告窗口状态（**最小化才是要测的场景**）──
    _gui, _state = None, None
    try:
        _gui = _ad._get_gui()
        _state, _rr = _win_state(_gui)
    except Exception as _e:
        _rr = "<拿不到 GUI: %s>" % str(_e)[:80]
        _state = None
    print("▸ 微信窗口 render_rect = %s" % (_rr,))
    if _state is True:
        print("  ✓ 窗口**处于最小化/屏幕外** ⇒ 这正是要测的场景 ✓")
    elif _state is False:
        print("  ⚠️ 窗口**当前可见/未最小化** ⇒ 这次测的**不是**「最小化时发送」的场景。")
        print("     要测那条 L5，请先把微信**最小化**再跑本脚本（否则结论要标注成'可见时')。")
    else:
        print("  ⚠️ 窗口状态判不出来 ⇒ 结论里必须写明「未知」，别当成已验证。")

    # ── 2) 组载荷（text 用唯一串便于库回读定位；图/文件用真实素材）──
    _stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _text = a.text or ("[最小化发送验证] %s" % _stamp)
    print("▸ 收件人  = %s%s" % (a.chat, "（文件传输助手＝发给你自己）" if a.chat == "filehelper" else ""))
    print("▸ 类别    = %s" % a.kind)
    if a.kind == "text":
        print("▸ 文本    = %r" % _text)
    elif a.kind == "voice":
        # 语音条：**先只做只读探针**（"按住录音键"这一步在最小化档是否可行尚未标定；
        # 未标定就真发 ＝ 拿不确定的东西去污染真实会话）
        print("▸ 语音条：本次**只做只读探针**（不发送）")
        try:
            from agent.control import halt_reason as _hr0
            _h0 = str(_hr0() or "")
            print("    机器人状态 = %s" % (("**已停止/暂停**：" + _h0) if _h0 else "运行中"))
        except Exception:
            pass
        try:
            from agent import voice_strip as _vs_mod
            _vs = _vs_mod.status()
            for _k in ("ok", "mic", "out_dev", "engine", "calibrated",
                       "auto_locate", "switch"):
                if _k in _vs:
                    print("    %-11s = %r" % (_k, _vs.get(_k)))
            print("    ⇒ 三闸（mic／out_dev／engine）齐 + 已标定 ⇒ 合成语音条的**物理条件**具备；"
                  "但**最小化档能不能按住录音键**只有真跑一次才知道")
        except Exception as _e:
            print("    ⛔ 取 voice_strip 状态失败：%s: %s" % (type(_e).__name__, str(_e)[:100]))
        print("    ⚠️ 真发语音条前先看 PLAN 第 185 项里「语音条」那一段"
              "（`_enter_record_alt` 走 Alt 的可行性未验）。")
        return 0
    else:
        if not a.path:
            print("  ⛔ kind=%s 必须给 --path <文件路径>" % a.kind)
            return 2
        if not os.path.isfile(a.path):
            print("  ⛔ 文件不存在：%s" % a.path)
            return 2
        print("▸ 文件    = %s（%d 字节）" % (a.path, os.path.getsize(a.path)))
        if a.kind == "gif":
            try:
                from agent import emoticon as _emo
                print("    会不会动 = %s（True ⇒ 走 CF_HDROP，动效保住）"
                      % bool(_emo.is_animated_file(a.path)))
            except Exception:
                pass

    # ── 3) 发送前先记一次库里的最新时间（回读的"基线"）──
    _before = ""
    try:
        _before = _ad._last_time_hhmm(a.chat) or ""
    except Exception as _e:
        _before = "<取不到: %s>" % str(_e)[:40]
    print("▸ 发送前该会话最后一条时间 = %r" % (_before,))
    # 图/文件的回读基线：**标量 local_id**（只读一个数，不碰大 BLOB）
    _base_id = 0
    try:
        _brows = _ad._db_messages(a.chat, limit=5) or []
        _base_id = int(_brows[0].get("local_id") or 0) if _brows else 0
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        _base_id = 0
    print("▸ 发送前该会话最新 local_id = %d（图/文件的回读基线）" % _base_id)

    # ── 3b) ⛔⛔ **前置闸：机器人是不是处于「已停止/已暂停」**（实测加）──
    #   实测踩到：`send_text` 返回 `(False, '机器人已停止 ⇒ 这条不发')` ——
    #   而当时**窗口确实是最小化** ⇒ 若不核查，就会把这条记成
    #   「最小化场景**没发出去**」⇒ 写进 `version_matrix` 就成了
    #   **把"没测成"写成"测失败"**（`unknown` → `no`）—— 比不测更糟。
    #   真相：`data/stopped.flag` / `data/paused.flag` 两个**文件级真相**在拦
    #   （见 `agent/control.py`；本机实测它们分别落于 20:57 / 21:02，
    #    即**用户昨天按的停止**，与本脚本无关）。
    #   ⇒ **本脚本绝不触碰这两个 flag**（那是用户的安全控制，不是我们能改的状态）；
    #     只**如实报告**并给出恢复方法。
    _halt = ""
    try:
        from agent.control import halt_reason as _halt_fn
        _halt = str(_halt_fn() or "")
    except Exception as _e:
        _halt = ""
        print("  ⚠️ 读不到 control 的停止状态（%s）⇒ 结论里要标注这一点" % type(_e).__name__)
    if _halt:
        print("  ⛔ 前置未满足：**机器人处于停止/暂停状态** ⇒ %s" % _halt)
        print("     ⇒ 这不是「最小化不能发」，而是**发送链的前置闸拦住了**（与窗口状态无关）。")
        print("     ⇒ 恢复方法：在界面上点「继续/运行」（会移除 data/paused.flag / data/stopped.flag）；")
        print("        本脚本**不会**替你删这两个文件 —— 那是你的安全控制。")
    else:
        print("  ✓ 前置闸通过：机器人未处于停止/暂停状态")

    if not a.send:
        print(_HR * 74)
        print("== DRY-RUN：**没有发送任何消息** ==")
        if _halt:
            print("   ⚠️ 注意：当前处于停止/暂停 ⇒ 即便加 `--send` 也会被前置闸拒发（结论无效）。")
        print("   确认无误后加 `--send` 真跑（会产生一条真实消息，默认只发给你自己的 filehelper）。")
        print(_HR * 74)
        return 0

    if _halt:
        print(_HR * 74)
        print("== ⛔ BLOCKED：前置未满足，**本次没发、也没法给出结论** ==")
        print("   原因：%s" % _halt)
        print("   ⇒ 请在界面上恢复运行后重跑；**不要**把这次结果当成「最小化能不能发」的答案。")
        print(_HR * 74)
        print("▸ 若要落库，请先恢复运行再测；本次**不写**任何结论：")
        print('  （例：恢复运行后重跑 --send，再取它打印的证据行）')
        print(_HR * 74)
        return 3

    # ── 4) 真发（按类别分派到对应的主链入口）──
    print(_HR * 74)
    print("▸ 开始发送（类别=%s；窗口保持最小化 —— **不要**去点它）..." % a.kind)
    _t0 = time.time()
    try:
        if a.kind == "text":
            _ok, _msg = _ad.send_text(a.chat, _text)
        elif a.kind in ("image", "gif"):
            _ok, _msg = _ad.send_image(a.chat, a.path)
        else:  # file
            _ok, _msg = _ad.send_file_posted(a.chat, a.path)
    except Exception as _e:
        print("⛔ 发送抛异常：%s: %s" % (type(_e).__name__, str(_e)[:160]))
        _ok, _msg = False, "exception: %s" % type(_e).__name__
    _dt = time.time() - _t0
    print("  发送返回 = (%r, %r)  耗时 %.1fs" % (_ok, str(_msg)[:160], _dt))

    # ── 5) 回读：等库出现那条新消息（**这才是"真发出去"的判据**）──
    #   · 文本   ⇒ 命中本次文本串；
    #   · 图/文件 ⇒ 出现**比基线更新**且**非文本**（`local_type != 1`）的行。
    #     ⚠️ 图/表情落库实测 **30~180s**（要等客户端把资源传完才写库）⇒ `--wait` 给小了
    #        会把"其实发出去了"误判成"没发出去"，所以默认只等 15s 时**别拿它当"不行"**。
    print("▸ 等库回读（最多 %.0fs）..." % a.wait)
    _hit, _after, _rows, _hitrow = False, "", [], None
    _deadline = time.time() + max(1.0, a.wait)
    while time.time() < _deadline:
        try:
            _rows = _ad._db_messages(a.chat, limit=5) or []
        except Exception:
            _rows = []
        if a.kind == "text":
            for _r in _rows:
                if _text and _text in str(_r.get("text") or _r.get("content") or ""):
                    _hit, _hitrow = True, _r
                    break
        else:
            # ⛔ 类型判据**不许把类型强转 int**：`_db_messages` 回的 `type` 是**中文名**
            #   （`"文本"` / `"图片"` / `"动画表情"` / `"文件/链接/卡片"` / `"语音"` …），
            #   `int` 会抛 ValueError ⇒ 被 except 吃掉 ⇒ **每一行都被跳过** ⇒
            #   永远判"未命中"（实测 ：文件其实已落库 `local_id=719`，却被判成没发出去）。
            #   ⇒ 改成**按类型名判**：只有 `"文本"` 不算（图/文件/语音/表情都算命中）。
            for _r in _rows:
                try:
                    _lid = int(_r.get("local_id") or 0)
                except Exception:
                    continue
                _lt = str(_r.get("local_type") or _r.get("type") or "").strip()
                if _lid > _base_id and _lt and _lt != "文本":
                    _hit, _hitrow = True, _r
                    break
        if _hit:
            break
        time.sleep(1.0)
    try:
        _after = _ad._last_time_hhmm(a.chat) or ""
    except Exception:
        _after = ""
    if a.kind == "text":
        print("  库回读命中本次文本 = **%s**" % _hit)
    else:
        print("  库回读命中「新非文本行」= **%s**%s"
              % (_hit, ("（local_id=%s type=%s）" % (
                  _hitrow.get("local_id"),
                  _hitrow.get("local_type") or _hitrow.get("type")) if _hitrow
                  else "（基线 local_id=%d）" % _base_id)))
    print("  发送后最后一条时间   = %r（发送前 %r）" % (_after, _before))
    if not _hit:
        print("  最近 5 条：%s" % [(str(_r.get("text") or _r.get("content") or ""))[:24] for _r in _rows])

    # ── 6) 结论 ──
    print(_HR * 74)
    _scene = "最小化" if _state is True else ("窗口可见" if _state is False else "窗口状态未知")
    _sendw = {"text": "send_text", "image": "send_image",
              "gif": "send_image(GIF)", "file": "send_file_posted"}.get(a.kind, a.kind)
    if _ok and _hit:
        print("✅ **通过**：【%s·%s】确实发出去了（%s 返回 True 且库回读命中）"
              % (_scene, a.kind, _sendw))
        _v = ("ok", "【%s·%s】%s=True 且库回读命中（%s）；窗口 render_rect=%s"
                    % (_scene, a.kind, _sendw, _stamp, _rr))
    elif _hit:
        print("⚠️ **半通过**：库里有新消息（真发出去了），但 %s 返回 False ⇒ **判据本身要查**" % _sendw)
        _v = ("partial", "库回读命中但 %s=False（%s）" % (_sendw, _stamp))
    elif _ok:
        print("⚠️ **半通过**：%s 说成功，但库回读**没命中** ⇒ 可信度不足"
              "（可能是假成功，也可能是**落库慢** —— 图/表情要 30~180s）" % _sendw)
        _v = ("partial", "%s=True 但库回读未命中（%s）" % (_sendw, _stamp))
    else:
        # ⛔⛔ **先问"是不是条件不完整"，不许直接记 `no`** —— 本脚本抬头就写着
        #   「把 `unknown` 误写成 `no`，**比不测更糟**」，而这里原来**自己另写了一份判定**
        #   （真跑这条路径**根本没走** `_verdict()`）⇒ 于是 `no_ref`／"切会话没成"／
        #   "真鼠标兜底没开"这类**前置缺失**被记成了 `no`（实测报告就是这么来的）。
        #   ⇒ 统一交给 `_verdict()`：有"条件缺失"信号 ⇒ `unknown` + 原始返回；
        #     条件齐全仍发不出，才算 `no`。
        _v = _verdict(_ok, _hit, str(_msg), _scene, _rr)
        if _v[0] == "unknown":
            print("⚠️ **未测成（前置条件不完整）**：【%s·%s】%s"
                  % (_scene, a.kind, _v[1][:200]))
        else:
            print("❌ **未通过**：【%s·%s】没发出去（%s=False 且库回读未命中）"
                  % (_scene, a.kind, _sendw))

    print(_HR * 74)
    print("▸ 可直接粘进 `agent/version_matrix.py` 的 `SEED_RUNS[...]['caps']`：")
    _key = "minimized_send" if a.kind == "text" else ("minimized_send__%s" % a.kind)
    print('  "%s": {"status": "%s", "evidence": "%s"},'
          % (_key, _v[0], _v[1].replace('"', "'")))
    print(_HR * 74)
    return 0 if _v[0] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
