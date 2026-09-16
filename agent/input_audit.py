# -*- coding: utf-8 -*-
"""输入审计：给 Win32 的输入/投递 API 包一层记录（只在 `WXAGENT_INPUT_AUDIT=1` 时生效）。

为什么需要它（2026-09-17）：用户连续四次报「机器人发消息那一刻，微信自己弹了截图（整屏压暗）」。
真因候选横跨三个来源，读代码已经区分不开：
  ① 我们产品的真键鼠（`wechat.py` 里 10 处 `gui._input.key(...)` / `SendKeys`）；
  ② 第三方驱动库 `wechatauto` 自己的真键鼠（`guia.py` 的 SendInput/keybd_event、`moment.py` 的 pyautogui）；
  ③ 我们产品的投递消息（`PostMessageW` 点"发送"按钮，坐标算错就可能落到工具栏 `✂` 上）。
低级键盘钩子（`_scratch/keylog.py`）在本机装不上（UIPI/会话限制，连自注入的按键都收不到），所以改从**进程内**取证。

做法：把 `ctypes.windll.user32` 上这几个函数替换成"先记一笔再原样调用"的包装 ——
因为所有来源最终都走这几个 API（ctypes 的函数是**调用时**才取属性，所以替换生效），
并且记录里带上**发起方的文件:行号:函数名**，谁干的、按了什么键，一次看清。

⚠️ 只在环境变量 `WXAGENT_INPUT_AUDIT=1` 时安装；生产默认不装（零开销、零行为改变）。
"""
import os
import time

_ON = False
_FH = None

#: 要盯的 API —— 名字 -> 是否值得记（都记，参数一目了然）
_WATCH = ("SendInput", "keybd_event", "mouse_event", "SetCursorPos",
          "PostMessageW", "SendMessageW", "SendMessageTimeoutW")


def _log(line):
    global _FH
    try:
        if _FH is None:
            d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
            os.makedirs(d, exist_ok=True)
            _FH = open(os.path.join(d, "input_audit.log"), "a", encoding="utf-8")
        _FH.write("%s %s\n" % (time.strftime("%H:%M:%S"), line))
        _FH.flush()
    except Exception:
        pass


def install():
    """装审计。返回实际包上的 API 名单（装失败/未开启则返回空列表）。"""
    global _ON
    if _ON:
        return []
    if os.environ.get("WXAGENT_INPUT_AUDIT") != "1":
        return []
    try:
        import ctypes
        import traceback
        u32 = ctypes.windll.user32
    except Exception as e:
        _log("审计安装失败（import）：%s" % e)
        return []
    done = []
    for name in _WATCH:
        try:
            orig = getattr(u32, name)
        except Exception:
            continue

        def _make(name_, orig_):
            def _wrapper(*a, **kw):
                try:
                    frames = traceback.extract_stack()[:-1]
                    # 取最近 4 层调用者，反转成"发起 → 当前"，一眼看出是谁点的
                    where = " < ".join(
                        "%s:%d:%s" % (os.path.basename(f.filename), f.lineno, f.name)
                        for f in reversed(frames[-4:]))
                    args = ", ".join(_fmt(v) for v in a)
                    _log("%s(%s)   ← %s" % (name_, args, where))
                except Exception:
                    pass
                return orig_(*a, **kw)
            return _wrapper

        try:
            setattr(u32, name, _make(name, orig))
            done.append(name)
        except Exception as e:
            _log("包 %s 失败：%s" % (name, e))
    _log("==== input_audit 安装：%s ====" % (", ".join(done) or "（一个都没装上）"))
    _ON = True
    return done


def _fmt(v):
    """把参数压成短字符串（指针/结构体只给个摘要）。"""
    try:
        if isinstance(v, int):
            return str(v) if abs(v) < 1 << 32 else "0x%X" % v
        if hasattr(v, "contents") and getattr(v, "contents", None) is not None:
            c = v.contents
            parts = []
            for f in ("pt", "x", "y", "dx", "dy", "dwFlags", "wVk", "wScan", "vkCode"):
                if hasattr(c, f):
                    try:
                        val = getattr(c, f)
                        if hasattr(val, "x"):
                            parts.append("(%d,%d)" % (val.x, val.y))
                        else:
                            parts.append("%s=%s" % (f, val))
                    except Exception:
                        pass
            return "{" + ",".join(parts) + "}" if parts else "<ptr>"
        return repr(v)[:40]
    except Exception:
        return "?"
