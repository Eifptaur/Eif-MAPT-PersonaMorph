# -*- coding: utf-8 -*-
"""V-R3-3 判据：控制台「原始 JSON」（`GET /api/config`）里**不许有明文凭据**。

复现（改前，一行；只打印长度与布尔，不回显 URL 本体）：
    runtime\\python\\python.exe -c "import sys;sys.path.insert(0,'.');from agent import webui as W;
    from agent.config import get_config;c=get_config();m=W.WebUI.__new__(W.WebUI).masked_config();
    u=str((c.get('feedback') or {}).get('webhook_url') or '');
    v=str((m.get('feedback') or {}).get('webhook_url') or '');
    print('长度=%d 原样回显=%s'%(len(u),v==u))"
  ⇒ 改前：`长度=89 原样回显=True`（URL 里带 `?key=`，**URL 即凭据**）。

本判据是**扫描式**的（不是照着固定名单逐条断言 —— V8/V-R3-3 两次漏都是因为名单是人手写的）：
  A 通用：把一份"塞满各种形态凭据"的假配置递归展开，凡
    「键名像凭据（key/token/secret/password/sign…）」或「值是带凭据参数的 URL」的字段，
    断言 `masked_config()` 的对应值**不等于原值**（且打码串含 `••••`，恢复侧认得）；
  B 对称：把打码后的配置原样提交回来 ⇒ `_protect_secrets()` 必须**还原成真值**（两侧同源）；
  C 阳：用户真改了值 ⇒ 新值照常落盘（别把"保护"做成"锁死"）；
  D 表驱动：`CRED_FIELDS` 是掩码侧与恢复侧**共用的一份**（源码级 + 行为级双重钉住）；
  E 阴：配置里没有的字段不许被凭空造出来（`_protect_secrets` 不建结构）。

⛔ 纪律：全部用假配置（`W.get_config` 打桩），**不读也不写产品 `config.json`**；只打印长度与布尔，
   **不回显任何凭据**。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import webui as W          # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [%s]" % detail) if detail else ""))


# 假凭据：一律是明显的假值，长度照真实形态给（这样"掩了没掩"看得出来，而真值一个都不出现）
FAKE = {
    "api": {"api_key": "FAKE-KEY-1", "provider_keys": {"deepseek": "FAKE-KEY-2"}},
    "cloud": {"token": "FAKE-PEER"},
    "feedback": {"webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=FAKEWEBHOOKKEY0001",
                 "webhook_token": "FAKE-PUSH",
                 "smtp": {"password": "FAKEAUTHCODE1234", "user": "me@example.com"}},
    "server": {"token": "FAKE-CONSOLE"},
    "persona": {"name": "群相", "custom_rules": ""},
}
_CRED_KEY_RE = re.compile(r"(?i)(key|token|secret|passwo?r?d|sign|webhook)")


def walk(d, path=()):
    """递归展开配置 → [(路径元组, 值)]。"""
    out = []
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            out += walk(v, path + (k,))
        else:
            out.append((path + (k,), v))
    return out


def main():
    _real = W.get_config
    W.get_config = lambda: FAKE
    try:
        m = W.WebUI.__new__(W.WebUI).masked_config()
        print("== A. 通用扫描：键名像凭据 / 值是带凭据参数的 URL ⇒ 必须打码 ==")
        for path, val in walk(FAKE):
            s = str(val or "")
            if not s:
                continue
            _by_name = bool(_CRED_KEY_RE.search(path[-1]))
            _by_url = ("?" in s and bool(_CRED_Q_RE_HAS(s)))
            if not (_by_name or _by_url):
                continue
            _got = m
            for k in path:
                _got = (_got or {}).get(k) if isinstance(_got, dict) else None
            if path[-1] == "server" or (path and path[0] == "server"):
                continue                          # server.token 是**有意不掩**的（控制台自己的钥匙）
            ok("A1 %s 被打了码（改前 webhook_url 原样回显）" % ".".join(path),
               str(_got or "") != s, "长度 %d→%d" % (len(s), len(str(_got or ""))))
            ok("A1b %s 的打码串含 ••••（恢复侧认得出）" % ".".join(path),
               W._is_masked(str(_got or "")), str(_got or "")[:12])
        ok("A2 server.token **有意不掩**（用户得能在面板上看到自己的控制台钥匙）",
           m["server"]["token"] == FAKE["server"]["token"], "")
        ok("A3 无害字段原样保留（别把面板用得上的值一起掩掉）",
           m["persona"]["name"] == "群相" and m["feedback"]["smtp"]["user"] == "me@example.com", "")
        ok("A4 原配置对象没被写脏（落盘仍是真值）",
           FAKE["feedback"]["webhook_url"].startswith("https://qyapi")
           and "••••" not in FAKE["feedback"]["webhook_url"], "")
        ok("A5 provider_keys（名字→密钥的字典）照旧打码",
           "••••" in m["api"]["provider_keys"]["deepseek"], "")

        print("\n== B. 对称：把打码值原样提交回来 ⇒ 必须还原真值（两侧同源）==")
        _post = {"api": {"api_key": m["api"]["api_key"], "provider_keys": dict(m["api"]["provider_keys"])},
                 "cloud": {"token": m["cloud"]["token"]},
                 "feedback": {"webhook_url": m["feedback"]["webhook_url"],
                              "webhook_token": m["feedback"]["webhook_token"],
                              "smtp": {"password": m["feedback"]["smtp"]["password"]}}}
        W._protect_secrets(_post)
        for path, val in walk(FAKE):
            if path[0] == "server" or not str(val or ""):
                continue
            if not _CRED_KEY_RE.search(path[-1]):
                continue
            _got = _post
            for k in path:
                _got = (_got or {}).get(k) if isinstance(_got, dict) else None
            ok("B1 %s 恢复成了真值（掩码值不许覆盖真值）" % ".".join(path), _got == val,
               "长度 %d" % len(str(_got or "")))
        ok("B2 webhook_url 恢复后**不是**打码串（否则用户一点保存就把真 URL 写没了）",
           "••••" not in str(_post["feedback"]["webhook_url"]), "")

        print("\n== C. 阳：用户真改了值 ⇒ 新值照常落盘 ==")
        _post2 = {"feedback": {"webhook_url": "https://example.com/hook?key=BRANDNEWKEY0001",
                               "webhook_token": "BRANDNEWTOKEN"}}
        W._protect_secrets(_post2)
        ok("C1 新 URL 不被旧值覆盖", _post2["feedback"]["webhook_url"].endswith("BRANDNEWKEY0001"),
           _post2["feedback"]["webhook_url"][:32])
        ok("C2 新 token 不被旧值覆盖", _post2["feedback"]["webhook_token"] == "BRANDNEWTOKEN", "")

        print("\n== D. 表驱动：掩码侧与恢复侧共用一份 CRED_FIELDS ==")
        _src = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        ok("D1 表里有 feedback.webhook_url（V-R3-3 本体）",
           (("feedback", "webhook_url"), "url") in W.CRED_FIELDS, str(W.CRED_FIELDS))
        ok("D2 masked_config 遍历表（不再逐字段手写）",
           "for path, kind in CRED_FIELDS" in _src and "_mask_path(cfg, path, kind)" in _src, "")
        ok("D3 _protect_secrets 遍历**同一张**表",
           "for path, _kind in CRED_FIELDS" in _src and "_restore_path(new_cfg, old, path)" in _src, "")
        _masked_src = _src[_src.index("def masked_config"):]
        _masked_src = _masked_src[:_masked_src.index("def _safe_join")]
        ok("D4 掩码侧不再残留「手写枚举」的痕迹（table 驱动 + URL 专门脱敏都在）",
           "_mask_path" in _masked_src and "mask_url_credential" in _src, "")
        ok("D5 URL 类字段走专门的 URL 脱敏（不是当普通密钥首尾截断）",
           "mask_url_credential" in _src and W.mask_url_credential(
               "https://x/hook?key=ABCDEFGHIJK") != "https://x/hook?key=ABCDEFGHIJK", "")

        print("\n== E. 阴：配置里没有的字段不许被凭空造出来 ==")
        _post3 = {"feedback": {"webhook_token": m["feedback"]["webhook_token"]}}
        _before = set((_post3["feedback"] or {}).keys())
        W._protect_secrets(_post3)
        ok("E1 _protect_secrets 不新建结构（只还原已存在的键）",
           set(_post3["feedback"].keys()) == _before, str(sorted(_post3["feedback"].keys())))
        _post4 = {"api": {"api_key": "sk-••••1234"}}
        W._protect_secrets(_post4)
        ok("E2 段不存在（没有 feedback/cloud）也不抛", isinstance(_post4, dict), "")
        try:
            W._protect_secrets({})
            _e3 = True
        except Exception as e:                     # noqa: BLE001
            _e3 = "抛了：%r" % (e,)
        ok("E3 空配置不抛", _e3 is True, str(_e3))
    finally:
        W.get_config = _real

    print("\n==== 配置凭据脱敏判据（V-R3-3）：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


def _CRED_Q_RE_HAS(s):
    """这个字符串里有没有"带凭据名的查询参数"（与产品同一份正则）。"""
    return bool(W._CRED_Q_RE.search(s))


if __name__ == "__main__":
    sys.exit(main())
