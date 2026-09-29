#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pmctl —— **不依赖图形界面的操作入口**（纯终端 CLI，走产品自带的本地 HTTP API）。

## 为什么有它
现场（2026-09-29 群相反馈 · 另一台机器）：**AMD ASP2400 这类无独显的老 CPU** 上，
浏览器/WebView2 版的网页控制台"卡到不能用" ⇒ 用户在低配服务器上**根本没有可用的操作界面**。
但产品本来就有一套完整 HTTP API（**109 条 `/api/` 路由**，网页控制台只是它的一个前端）
⇒ 这个 CLI 直接调那套 API：**不开浏览器、不跑 WebView2、不画任何界面**，纯文本进纯文本出。

## ⚠️ 能力边界（**如实说，不追求 1:1**）
控制台有 109 条路由、这里 19 条命令 ⇒ **两者不是完全等价**，分成三类：
  · **这两边都能做**：状态/体检/日志/配置/启停/自启/低配/症状自查/会话/接入诊断/标定/指纹/反馈/真值审核；
  · **只有图形界面做得舒服**（**本质需要屏幕**）：看图表与记忆图谱、拖拽挂件与背景裁剪、
    点屏幕取点的可视化标定、上传/预览图片（角色图/气泡图/背景）、进度条那种实时观感；
    ⇒ 这些**不做命令行版**（硬做只会更难用）；无界面的正解是把结果**落成文件**：
     体检报告、反馈包、待审真值包（`truth export`）都是这么给的。
  · **已落成文件、命令行也能拿到**：体检报告 · 反馈包（`pack`）· 待审真值包（`truth export`）。

## 用法（全部命令都不需要图形界面）
    python scripts/pmctl.py status              # 运行状态 / 监听目标 / 引擎 / OCR 档位
    python scripts/pmctl.py adapt               # 本机适配档案（只读）
    python scripts/pmctl.py verify              # 跑全部症状检验器
    python scripts/pmctl.py verify send_blocked # 只跑某一条
    python scripts/pmctl.py check               # 体检（可能耗时，会打印耗时）
    python scripts/pmctl.py sessions 20         # 最近 20 条会话
    python scripts/pmctl.py logs 80             # 日志尾部 80 行
    python scripts/pmctl.py cfg get wechat.poll_interval
    python scripts/pmctl.py cfg set wechat.poll_interval 6
    python scripts/pmctl.py wechat diag         # 接入诊断（连不上微信时先看这个）
    python scripts/pmctl.py fingerprint take    # 重取图标指纹（换了主题/DPI）
    python scripts/pmctl.py calibrate           # 重新标定（点不准）
    python scripts/pmctl.py truth list 20       # 真值审核：看最可疑的 20 条
    python scripts/pmctl.py truth export        # 导出待审包（清单 + 放大裁剪图，看图审）
    python scripts/pmctl.py pack                # 反馈包（一个 txt，直接发作者）
    python scripts/pmctl.py feedback "问题描述"  # 直接提反馈（发不出去会如实报错）
    python scripts/pmctl.py pause / resume
    python scripts/pmctl.py lowspec on|off|show # **省资源预设**（低配机器用这个）
    python scripts/pmctl.py doctor              # status+adapt+check 一把跑完（远程排查用）

## 口径
- **只调产品自己的 API**（与网页控制台同一套路由、同一套权限），不直连数据库、不改文件。
- 地址发现顺序：`--url` > 环境变量 `PMCTL_URL` > `logs/console.url`（拥有 token 的进程写出来的现成地址）
  > `config.json` 的 `server.port`。找不到就**明确报错并给出下一步**，绝不猜。
- 输出**不用 emoji**（本仓 P0 规则：图标一律 SVG/文字，邮件与终端同理），`--json` 给机器读。
- `lowspec` 只碰**白名单里的省资源键**，并且**打印改了哪几条、怎么恢复**；安全开关（只走后台等）一条不碰。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 省资源预设（**白名单**）：低配机器上把"画面好看但吃 CPU"的都关掉。
#: 只列**确认存在**的键（见 `agent/config.py` 的 DEFAULT_CONFIG）——写错键名等于偷偷造垃圾配置。
LOWSPEC = {
    "ui.whale_cursor": False,        # 桌面挂件/光标特效
    "ui.whale_anim.worm": 0,         # 三种挂件动画强度归零
    "ui.whale_anim.plane": 0,
    "ui.whale_anim.zap": 0,
    "ui.wave_fx.enabled": False,     # 波纹特效
    "ui.background": "",             # 自定义背景（大图渲染）
    "wechat.poll_interval": 6,       # 轮询从 3s 放宽到 6s（CPU 占用近似减半）
}
#: `lowspec on` 之前的原值快照放这里（`off` 时恢复；重启也会读）
SNAP_PATH = os.path.join(ROOT, "data", "pmctl_lowspec_snapshot.json")


def _warn(msg: str) -> None:
    """失败痕迹（默认安静，`PMCTL_DEBUG=1` 时打 stderr）。

    ⛔ 本仓静默点棘轮要求「不许空的 except」—— 兜底必须留痕，哪怕是"只在调试时可见"。
    """
    if os.environ.get("PMCTL_DEBUG"):
        sys.stderr.write("[pmctl] %s\n" % msg)


def _die(msg: str, code: int = 2):
    print("错误：%s" % msg)
    sys.exit(code)


def _discover(args) -> tuple:
    """返回 `(base_url, token)`；找不到就 `_die`（给出可照做的下一步）。"""
    url = (getattr(args, "url", "") or os.environ.get("PMCTL_URL") or "").strip()
    if not url:
        try:
            sys.path.insert(0, ROOT)
            from agent.util import read_console_url
            url = (read_console_url() or "").strip()
        except Exception: # noqa: BLE001
            p = os.path.join(ROOT, "logs", "console.url")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    url = (fh.read() or "").strip()
    if not url:
        try:
            sys.path.insert(0, ROOT)
            from agent.config import get_config
            port = int(((get_config() or {}).get("server") or {}).get("port") or 3210)
            url = "http://127.0.0.1:%d/" % port
        except Exception as e: # noqa: BLE001
            _die("拿不到控制台地址（logs/console.url 不存在，配置也读不到：%s）。\n"
                 "      请在产品目录里执行，或用 --url http://127.0.0.1:端口/ 指定。" % str(e)[:80])
    tok = (getattr(args, "token", "") or os.environ.get("PMCTL_TOKEN") or "").strip()
    if not tok:
        q = urllib.parse.urlparse(url).query
        tok = urllib.parse.parse_qs(q).get("token", [""])[0]
    if not tok:
        try:
            sys.path.insert(0, ROOT)
            from agent.config import get_config
            tok = str(((get_config() or {}).get("server") or {}).get("token") or "")
        except Exception: # noqa: BLE001
            tok = ""
    base = url.split("?", 1)[0].rstrip("/") + "/"
    return base, tok


def _req(args, path: str, method: str = "GET", body=None, timeout: float = 60.0):
    base, tok = _discover(args)
    data = None
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path.lstrip("/"), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        _die("HTTP %s：%s\n      地址：%s（地址不对就 --url 覆盖；服务没起就先启动产品）"
             % (e.code, raw[:200] or e.reason, base))
    except Exception as e: # noqa: BLE001
        _die("连不上 %s：%s\n      服务没起？或端口不对（用 --url 指定）" % (base, str(e)[:120]))
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception: # noqa: BLE001
        return {"_raw": raw}


def _kv(title: str, pairs) -> None:
    print("== %s ==" % title)
    for k, v in pairs:
        if v is None or v == "":
            continue
        print("   %-16s %s" % (k, v))


def cmd_status(a) -> int:
    d = _req(a, "/api/status")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    g = (lambda *ks: next((d.get(k) for k in ks if d.get(k) is not None), None))
    _kv("运行状态", [
        ("版本", g("version", "ver")), ("运行", g("running", "alive")), ("暂停", g("paused")),
        ("监听目标", g("targets", "watchers")), ("模型", g("model")),
        ("微信", g("wechat", "wx_ready")), ("消息库", g("db", "db_ok")),
    ])
    try:
        sys.path.insert(0, ROOT)
        from agent import chat_ocr as _o
        st = _o.rapidocr_status()
        es = _o.available()
        _kv("OCR", [("内置引擎", "可用" if es else "不可用"),
                    ("第二引擎", "%s（%s）" % (st.get("tier"), st.get("why") or "可用") if st.get("ok") else "不可用")])
    except Exception as e: # noqa: BLE001
        print("   （本机 OCR 档位读不到：%s）" % str(e)[:60])
    print("")
    print("   提示：完整状态里没打印的字段可以用 `--json` 看原始返回。")
    return 0


def cmd_version(a) -> int:
    d = _req(a, "/api/version")
    print(json.dumps(d, ensure_ascii=False, indent=1) if a.json else
          "当前版本：%s" % (d.get("version") or d.get("current") or d))
    return 0


def cmd_adapt(a) -> int:
    d = _req(a, "/api/verify?id=machine")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print(d.get("report") or json.dumps(d, ensure_ascii=False, indent=1))
    return 0


def _verify_ids(a) -> list:
    d = _req(a, "/api/verifiers")
    return [(str(v.get("id")), str(v.get("name"))) for v in (d.get("verifiers") or [])]


def cmd_verify(a) -> int:
    ids = _verify_ids(a)
    want = [x for x in (a.ids or [])] or [i for i, _n in ids]
    known = dict(ids)
    bad = [x for x in want if x not in known]
    if bad:
        _die("没有这些检验器：%s\n      可用的：%s" % ("、".join(bad), "、".join("%s(%s)" % (i, n) for i, n in ids)))
    for i in want:
        d = _req(a, "/api/verify?id=%s" % urllib.parse.quote(i), timeout=120.0)
        ok = d.get("ok")
        head = {True: "通过", False: "卡住", None: "没测到"}.get(ok, str(ok))
        print("── %s（%s）：%s" % (known[i], i, head))
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            print((d.get("report") or "").strip())
        print("")
    return 0


def cmd_corpus(a) -> int:
    """**语料（本机真实截图）**：把用户自己屏幕上的画面收进来、喂进真值流水线。

    子命令：
      `scan`    —— 只扫（**只读**用户目录，列出会收哪些、为什么不合格；不落盘）
      `add`     —— 扫并入语料库（**只在语料目录写副本**，绝不改动用户原目录）
      `feed`    —— 把已入库的真实截图喂进真值流水线（产待审候选）
      `check`   —— 体检已入库的素材（口径与网图一致：≥3 行 / ≥20 字 / ≥80000 px）
      `status`  —— 只看真值流水线现状（不喂；等价内层 `--report`）

    ⛔ 与 `truth` 的分工：`corpus` 管"**素材**"（有没有、干不干净），`truth` 管"**真值**"
       （读得对不对、要不要人看）。素材入库**不等于**有了真值 —— 真值只有人核过才算。
    """
    what = str(getattr(a, "what", "") or "scan").lower()
    import subprocess as _sp
    _py = sys.executable or "python"
    _script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           {"scan": "ocr_corpus_local.py",
                            "add": "ocr_corpus_local.py",
                            "feed": "local_truth_feed.py",
                            "status": "local_truth_feed.py",
                            "check": "ocr_corpus_local.py"}[what])
    argv = [_py, _script]
    if what == "scan":
        argv.append("--dry-run")
    elif what == "check":
        argv.append("--check-only")
    elif what == "status":
        # ⛔ 等价性坑：内层 `--report`（只看不喂）原来**没有任何无界面入口** ——
        #    想只看现状的人只能自己敲内层脚本名（等于无界面这条路少了一档）。
        argv.append("--report")
    _src = str(getattr(a, "dir", "") or "").strip()
    if _src and what in ("scan", "add"):
        argv += ["--dir", _src]
    if what == "feed" and int(getattr(a, "index", 0) or 0):
        argv += ["--limit", str(int(a.index or 0))]
    # ⛔ 等价性坑 2：内层 `--include-flagged`（连隐私标记图也喂）原来也接不上 ——
    #    默认跳过是**安全**的，但"明知有风险仍要喂"这条路必须存在（否则用户被默认值锁死）。
    if what == "feed" and bool(getattr(a, "include_flagged", False)):
        argv.append("--include-flagged")
    print("执行：%s" % " ".join(os.path.basename(x) if x.endswith(".py") else x for x in argv))
    try:
        # ⛔ 与全仓一致：子进程**不弹窗**（否则无界面路径反而弹出一堆黑框）。
        # ⛔⛔ 但 `CREATE_NO_WINDOW` 会把子进程的标准输出**摘掉**（实测：脚本确实输出了 7040 字节，
        #    终端上一个字都看不到 —— 无界面命令"跑了但看不见"，等于没跑）⇒ **必须**显式把
        #    子进程的 stdout/stderr 接到我们自己这里，才能既"不弹窗"又"看得见输出"。
        kw = {"stdout": sys.stdout, "stderr": sys.stderr}
        if os.name == "nt":
            kw["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0)
        return int(_sp.call(argv, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            **kw))
    except Exception as e:  # noqa: BLE001
        _die("跑不起来：%s" % str(e)[:80])
        return 1


def cmd_truth(a) -> int:
    """**真值审核**：看最可疑的那一撮、确认/否决、导出待审包（看图审，不是看字串猜）。

    子命令：`stats`（省了多少人工）· `list [n]`（按可疑度降序）· `show <n>`（第 n 条详情）·
    `ok <n>`（确认对）· `no <n>`（判定错）· `export [n]`（导出清单 + 放大裁剪图）。
    """
    what = str(getattr(a, "what", "") or "stats").lower()
    n = int(getattr(a, "index", 0) or 0)

    def _items(limit: int = 0):
        d = _req(a, "/api/truth/list?limit=%d" % (limit or 20))
        if not d.get("ok"):
            _die("读不到待审队列：%s" % (d.get("error") or d))
        return list(d.get("items") or []), (d.get("saving") or {}), (d.get("stats") or {})

    if what == "stats":
        d = _req(a, "/api/truth/stats")
        if not d.get("ok"):
            _die("读不到真值统计：%s" % (d.get("error") or d))
        r = d.get("result") or {}
        s = d.get("saving") or {}
        _kv("真值库", [("人核过（**只有它进硬指标**）", r.get("human")),
                       ("机器自动确认（不进硬指标）", r.get("auto")),
                       ("待审候选", r.get("proposed")),
                       ("合计", r.get("total"))])
        print("")
        print("  逐条人看口径：合计 %s 条 ⇒ **必须逐条看 %s 条**（可疑度 ≥ %s）"
              % (s.get("total"), s.get("must_review"), s.get("threshold")))
        if s.get("saved_ratio") is not None:
            print("  ⇒ 省下 **%.0f%%** 的逐条人工（其余：两引擎一致的可直接接受，低风险的快速处理）"
                  % (100.0 * s["saved_ratio"]))
        return 0

    if what == "export":
        d = _req(a, "/api/truth/export?limit=%d" % (n or 200), timeout=180.0)
        if not d.get("ok"):
            _die("导出失败：%s" % (d.get("error") or d))
        print("已导出 %d 条待审（裁剪图 %d 张）到：" % (d.get("count", 0), d.get("crops", 0)))
        print("  %s" % d.get("out_dir"))
        print("  清单：%s" % d.get("list"))
        print("  ⇒ 打开 `crops\\` 逐张看图，然后 `truth ok <序号>` / `truth no <序号>`。")
        return 0

    items, _sv, _st = _items(max(n, 20))
    if not items:
        print("待审队列是空的。（没有素材进过流水线时就是这样：先对素材跑一遍候选提取。）")
        return 0
    if what in ("list", ""):
        print("待审候选（按可疑度降序，共显示 %d 条）：" % len(items))
        for i, it in enumerate(items, 1):
            b = it.get("box") or []
            print("  %2d. [%s] `%s`  ← %s %s" % (
                i, it.get("uncertainty"), str(it.get("value"))[:22],
                os.path.basename(str(it.get("src") or "")) or it.get("ref"),
                ("@%s,%s" % (b[0], b[1])) if len(b) >= 2 else ""))
        print("")
        print("  想看某条为什么可疑：`truth show <序号>`；导出看图审：`truth export`。")
        return 0
    if what == "show":
        if n < 1 or n > len(items):
            _die("序号超范围（1~%d）" % len(items))
        it = items[n - 1]
        _kv("第 %d 条" % n, [("可疑度", it.get("uncertainty")), ("字串", it.get("value")),
                             ("来源图", os.path.basename(str(it.get("src") or "")) or it.get("ref")),
                             ("坐标", it.get("box")), ("为什么可疑", it.get("note")),
                             ("谁产出的", it.get("produced_by"))])
        print("  ⚠️ 审核要**看图**：先 `truth export` 再打开 `crops\\p%d.png`。" % n)
        return 0
    if what in ("ok", "no"):
        if n < 1 or n > len(items):
            _die("序号超范围（1~%d）" % len(items))
        it = items[n - 1]
        body = {"kind": it.get("kind") or "ocr_text", "ref": it.get("ref"),
                "value": it.get("value"), "note": "pmctl 确认"}
        d = _req(a, "/api/truth/%s" % ("confirm" if what == "ok" else "reject"), "POST", body)
        if not d.get("ok"):
            _die("提交失败：%s" % (d.get("error") or d))
        print("已%s：`%s`（%s）" % ("确认对" if what == "ok" else "判定为错",
                                   str(it.get("value"))[:24], it.get("ref")))
        print("  现在人核真值 %s 条（只有它能进硬指标）。" % (d.get("stats") or {}).get("human"))
        return 0
    _die("不认识的子命令：%s（可用：stats / list / show / ok / no / export）" % what)
    return 2


def cmd_wechat(a) -> int:
    """**接入诊断 / 数据库目录**（无界面机器上"连不上微信"时的第一条路）。

    子命令：`diag`（逐行诊断，默认）· `dir`（看/设聊天文件目录）· `recheck`（重新检测）。
    """
    what = str(getattr(a, "what", "") or "diag").lower()
    if what == "dir":
        d = _req(a, "/api/wechat/dir")
        if not d.get("ok"):
            _die("读不到微信目录设置：%s" % (d.get("error") or d))
        r = d.get("result") if isinstance(d.get("result"), dict) else d
        _kv("微信聊天文件目录", [(k, v) for k, v in r.items() if not isinstance(v, (dict, list))][:8])
        print("  改目录：控制台「微信」面板，或 `cfg set wechat.dir <路径>`（改完 `wechat recheck`）。")
        return 0
    # ⛔ 只有 `/api/wechat/recheck` 与 `/api/wechat/dir` 两条真路由（我第一版猜了个 `/api/wechat`，
    #    被 `pmctl_selftest` 的 E1「CLI 引用的路径必须在路由表里」当场抓住）⇒ diag 就走 recheck。
    path = "/api/wechat/recheck"
    d = _req(a, path, timeout=120.0)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    rep = d.get("report") or d.get("result") or d
    if isinstance(rep, dict):
        for k, v in list(rep.items())[:16]:
            print("  %-22s %s" % (k, str(v)[:70]))
    else:
        print(str(rep).strip()[:2000])
    print("")
    print("  ⚠️ 哪一行写着「卡住」，就照那一行写的做（详见 README「出问题怎么办」第 1 条）。")
    return 0 if d.get("ok", True) else 1


def cmd_fingerprint(a) -> int:
    """**图标指纹**（点之前"对不上就不点"的依据）：看状态 / 取指纹 / 清指纹。

    子命令：`status`（默认）· `take`（重取）· `forget [key]`（清掉，换环境后用）。
    """
    what = str(getattr(a, "what", "") or "status").lower()
    if what == "take":
        d = _req(a, "/api/ui_fingerprint/take", timeout=180.0)
        if not d.get("ok"):
            _die("取指纹失败：%s" % (d.get("error") or d))
        r = d.get("result") or {}
        _okn = len(r.get("ok") or [])
        _bad = list(r.get("failed") or [])
        print("已取指纹：成功 %d 个" % _okn)
        if _bad:
            print("  失败 %d 个：%s" % (len(_bad), "；".join(str(x) for x in _bad)[:160]))
        print("  环境键：%s" % r.get("key"))
        return 0
    if what == "forget":
        k = str(getattr(a, "index", "") or "")
        body = {"key": k} if k else {}
        d = _req(a, "/api/ui_fingerprint/forget", "POST", body)
        if not d.get("ok"):
            _die("清指纹失败：%s" % (d.get("error") or d))
        print("已清指纹：%s" % (d.get("result") or "-"))
        return 0
    d = _req(a, "/api/ui_fingerprint/take")       # GET 只看状态（不重取）
    _kv("图标指纹", [("本环境有没有记录", "看下面 status" if d.get("ok") else d.get("error"))])
    print("  取指纹：`fingerprint take`；清掉：`fingerprint forget`。")
    print("  说明：指纹是「点之前先比一眼」的依据，对不上就不点（宁可漏点也不点错）。")
    return 0


def cmd_calibrate(a) -> int:
    """重新标定（比例算错会点到别处；标定完**自动取一次指纹**）。"""
    d = _req(a, "/api/ui/recalibrate", "POST", {}, timeout=180.0)
    if not d.get("ok"):
        _die("标定失败：%s（微信窗口要先恢复显示、别最小化）" % (d.get("error") or d))
    r = d.get("result") or {}
    print("标定完成：%s" % (str(r)[:200] if r else "已提交"))
    d2 = _req(a, "/api/ui_fingerprint/take", timeout=180.0)
    print("  顺带取指纹：%s" % ("成功 %d 个" % len((d2.get("result") or {}).get("ok") or [])
                              if d2.get("ok") else d2.get("error")))
    return 0


def cmd_feedback(a) -> int:
    """**提反馈**（无界面机器上出问题的正路）：把内容连同兼容性摘要发出去；发不出去会**如实报错**。"""
    txt = str(getattr(a, "text", "") or "").strip()
    if not txt:
        txt = _ask("请简述问题（回车取消）：", "")
    if not txt:
        print("（没填内容，已取消。）")
        return 0
    d = _req(a, "/api/feedback/submit", "POST",
             {"type": str(getattr(a, "kind", "") or "问题反馈"), "content": txt}, timeout=90.0)
    if not d.get("ok"):
        # ⛔ 发不出去**如实说**，并把内容留在屏幕上让用户自己保存（不留假成功）
        print("❌ 没发出去：%s" % (d.get("error") or d))
        print("   内容已保留在下面，可复制保存：")
        print("   %s" % txt)
        return 1
    print("✅ 已提交反馈（%s）" % str(d.get("result") or "ok")[:80])
    return 0


def cmd_check(a) -> int:
    t0 = time.time()
    d = _req(a, "/api/selfcheck", method="POST", body={}, timeout=300.0)
    dt = time.time() - t0
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print("== 体检（%.1fs）==" % dt)
    print("   %s" % (d.get("summary") or d.get("why") or "（没有摘要）"))
    for c in (d.get("checks") or [])[:40]:
        print("   [%s] %s：%s" % ("OK" if c.get("ok") else "!!", c.get("name"), str(c.get("detail") or "")[:110]))
    return 0


def cmd_sessions(a) -> int:
    d = _req(a, "/api/sessions")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    rows = d.get("sessions") or d.get("rows") or []
    print("== 最近 %d 条会话 ==" % min(a.n, len(rows)))
    for r in rows[:a.n]:
        print("   %-28s %s" % (str(r.get("name") or r.get("title") or "?")[:28],
                               str(r.get("last") or r.get("time") or "")[:20]))
    return 0


def cmd_logs(a) -> int:
    d = _req(a, "/api/logs")
    lines = d.get("lines") or []
    if isinstance(lines, str):
        lines = lines.splitlines()
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    for ln in lines[-a.n:]:
        print(str(ln).rstrip())
    return 0


def _get_path(cfg: dict, path: str):
    o = cfg
    for k in str(path).split("."):
        if not isinstance(o, dict) or k not in o:
            return None
        o = o[k]
    return o


def cmd_cfg(a) -> int:
    if a.action == "get":
        cfg = _req(a, "/api/config")
        v = _get_path(cfg, a.key)
        print("%s = %s" % (a.key, json.dumps(v, ensure_ascii=False) if v is not None else "（没这个键）"))
        return 0 if v is not None else 1
    if a.action != "set":
        _die("cfg 只支持 get / set")
    val: object = a.value
    if val in ("true", "false"):
        val = (val == "true")
    else:
        try:
            val = int(val)
        except ValueError as e_int:
            try:
                val = float(val)
            except ValueError as e_float:
                _warn("值既不是整数也不是小数，按字符串写入：%s / %s" % (type(e_int).__name__, type(e_float).__name__))
    node = body = {}
    ks = str(a.key).split(".")
    for k in ks[:-1]:
        node[k] = {}
        node = node[k]
    node[ks[-1]] = val
    body["_touched"] = []          # ⛔ 声明"没碰安全开关" ⇒ 后端守卫会挡住意外的连带翻转
    _req(a, "/api/config", method="POST", body=body)
    print("已写入 %s = %s（这是局部提交：只改这一个键，其它键保持原值）" % (a.key, val))
    return 0


def cmd_pause(a) -> int:
    want = (a.mode == "pause")
    _req(a, "/api/pause", method="POST", body={"paused": want})
    print("已%s。" % ("暂停" if want else "恢复"))
    return 0


def cmd_lowspec(a) -> int:
    cfg = _req(a, "/api/config")
    if a.action == "show":
        print("== 省资源预设当前值 ==")
        for k, v in LOWSPEC.items():
            print("   %-26s 现在=%s  低配目标=%s" % (k, json.dumps(_get_path(cfg, k), ensure_ascii=False), json.dumps(v)))
        return 0
    if a.action == "on":
        snap = {k: _get_path(cfg, k) for k in LOWSPEC}
        try:
            os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
            with open(SNAP_PATH, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, ensure_ascii=False, indent=1)
        except Exception as e: # noqa: BLE001
            print("（原值快照没存下：%s；`off` 时会用内置默认值恢复）" % str(e)[:60])
        body = {"_touched": []}
        changed = []
        for k, v in LOWSPEC.items():
            if _get_path(cfg, k) == v:
                continue
            node = body
            ks = k.split(".")
            for kk in ks[:-1]:
                node = node.setdefault(kk, {})
            node[ks[-1]] = v
            changed.append(k)
        if len(body) <= 1:
            print("低配模式：本来就是省资源状态，没改任何键。")
            return 0
        _req(a, "/api/config", method="POST", body=body)
        print("已开低配模式，改了 %d 个键：" % len(changed))
        for k in changed:
            print("   %s -> %s" % (k, json.dumps(LOWSPEC[k], ensure_ascii=False)))
        print("")
        print("   恢复：python scripts/pmctl.py lowspec off")
        print("   另外建议（这几条没有配置键，得手动）：")
        print("     1) 别再开网页控制台/桌面窗口 —— 用这个 CLI；")
        print("     2) 不用「本地图片生成 / 本地 SD」（image_gen / local_sd），那是最吃 CPU 的；")
        print("     3) 不跑「UI 标定」这类一次性重活。")
        return 0
    if a.action == "off":
        snap = {}
        if os.path.exists(SNAP_PATH):
            try:
                snap = json.load(open(SNAP_PATH, encoding="utf-8"))
            except Exception as e: # noqa: BLE001
                print("（快照读不出来：%s）" % str(e)[:60])
        body = {"_touched": []}
        n = 0
        for k, v in LOWSPEC.items():
            orig = snap.get(k, None)
            if orig is None or orig == v:
                continue
            node = body
            ks = k.split(".")
            for kk in ks[:-1]:
                node = node.setdefault(kk, {})
            node[ks[-1]] = orig
            n += 1
        if n == 0:
            print("没找到低配前的快照（或值没变）⇒ 不改任何键。要手动改：cfg set <键> <值>。")
            return 0
        _req(a, "/api/config", method="POST", body=body)
        print("已恢复低配前的 %d 个键。" % n)
        return 0
    _die("lowspec 只支持 on / off / show")


def cmd_doctor(a) -> int:
    """**诊断块**：输出一段**固定格式、可直接整段粘贴**给作者的文本。

    为什么固定格式（用户要求）：反馈现场最痛的是"信息不全/格式各异" ⇒ 这里把定位问题要用的
    东西按固定字段一次性打齐（版本/时间/机器/显示/微信/OCR/运行/本机适配/体检/检验器逐条一行），
    用户**不需要懂**任何字段，复制整段即可。
    `--json` 仍是原始 JSON；`--no-verify` 跳过逐条检验器（省时间）。
    """
    import datetime
    st = _req(a, "/api/status")
    tt = (a.token or "").strip()
    base, _tok = _discover(a)

    def _fmt(v, dash="未知"):
        if v is None or v == "" or v == [] or v == {}:
            return dash
        if isinstance(v, bool):
            return "是" if v else "否"
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    lines = ["===== 群相诊断（整段复制发给作者即可）====="]
    lines.append("版本: %s" % _fmt(st.get("version") or st.get("ver")))
    lines.append("时间: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("入口: CLI pmctl（无界面）· 控制台地址: %s" % base)
    # 机器/显示/微信/消息库：优先取本机 compat 快照（只读），拿不到就如实写"未知"
    try:
        sys.path.insert(0, ROOT)
        from agent import compat as _cp
        for ln in (_cp.lines() or []):
            s = str(ln).strip()
            if s.startswith("AXIS|"):
                parts = s.split("|")
                if len(parts) >= 5:
                    lines.append("%s: %s" % (parts[1].strip(), parts[2].strip().replace("ok", "正常")))
            elif s:
                lines.append(s)
    except Exception as e: # noqa: BLE001
        _warn("本机兼容快照没取到（不影响其余字段）：%s" % str(e)[:60])
        lines.append("本机快照: 未知（%s）" % type(e).__name__)
    try:
        sys.path.insert(0, ROOT)
        from agent import chat_ocr as _o
        _rs = _o.rapidocr_status()
        lines.append("OCR: 内置引擎=%s · 第二引擎=%s" % (
            "可用" if _o.available() else "不可用",
            ("%s（%s）" % (_rs.get("tier"), _rs.get("why") or "可用")) if _rs.get("ok") else "不可用"))
    except Exception as e: # noqa: BLE001
        lines.append("OCR: 未知（%s）" % type(e).__name__)
    lines.append("运行: 在听=%s · 暂停=%s · 监听目标=%s · 模型=%s" % (
        _fmt(st.get("alive", st.get("running"))), _fmt(st.get("paused")),
        _fmt(st.get("targets") or st.get("watchers")), _fmt(st.get("model"))))
    try:
        _ad = _req(a, "/api/verify?id=machine")
        _rp = str(_ad.get("report") or "")
        _keep = [l.strip() for l in _rp.splitlines()
                 if l.strip().startswith(("机器指纹", "输入档位", "OCR", "档案", "判决"))][:6]
        lines.append("本机适配: %s" % ("；".join(_keep) if _keep else "（空）"))
    except SystemExit:
        lines.append("本机适配: 取不到（服务未响应）")
    try:
        _ck = _req(a, "/api/selfcheck", method="POST", body={}, timeout=300.0)
        _cs = _ck.get("checks") or []
        _bad = [c for c in _cs if c.get("ok") is False]
        lines.append("体检: 共 %d 项 · 通过 %d · 未通过 %d · 摘要=%s" % (
            len(_cs), len([c for c in _cs if c.get("ok") is True]), len(_bad),
            str(_ck.get("summary") or "")[:60]))
        for c in _bad[:5]:
            lines.append("   [未通过] %s：%s" % (c.get("name"), str(c.get("detail") or "")[:90]))
    except SystemExit:
        lines.append("体检: 取不到（服务未响应）")
    if not getattr(a, "no_verify", False):
        lines.append("--- 检验器（逐条一行）---")
        try:
            for _i, _n in _verify_ids(a):
                _d = _req(a, "/api/verify?id=%s" % urllib.parse.quote(_i), timeout=120.0)
                _ok = _d.get("ok")
                lines.append("[%s] %s（%s）：%s" % (
                    {True: "通过", False: "卡住", None: "没测到"}.get(_ok, "?"), _n, _i,
                    str(_d.get("verdict") or "")[:50]))
        except SystemExit:
            lines.append("（检验器清单取不到）")
    else:
        lines.append("检验器: 已跳过（--no-verify）")
    lines.append("===== 结束（以上内容不含聊天记录）=====")
    print("\n".join(lines))
    return 0


def _ask(prompt: str, default: str = ""):
    """读一行输入。

    ⛔ **流结束(EOF) 必须返回 `None`**，不能返回默认值 —— 否则"喂完输入的菜单"会
       一直用默认值选第 1 项、**无限循环**（判据实测卡死在 90s 超时）。
       回车（空输入）才回默认值。
    """
    try:
        s = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("")
        return None
    s = (s or "").strip()
    return s if s else default


def cmd_pack(a) -> int:
    """**反馈包**：把"定位问题要用的东西"一次性收进一个 txt，用户只要把文件发出去。

    零基础用户不需要懂日志/接口：一条命令（或菜单里一项）产出一个文件。
    ⛔ 内容只取**状态/档案/体检摘要/日志尾部**，不做任何脱敏之外的加工；
       落盘在 `logs/`（运行期数据，不进包）。
    """
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(ROOT, "logs", "pmctl-反馈包-%s.txt" % stamp)
    parts = []
    parts.append("== 群相 · 反馈包（自动生成，直接把这个文件发给作者即可）==")
    parts.append("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        sys.path.insert(0, ROOT)
        from agent.version import VERSION as _V
        parts.append("产品版本：%s" % _V)
    except Exception as e: # noqa: BLE001
        _warn("读不到产品版本（不影响反馈包）：%s" % str(e)[:60])
    for title, fn in (("运行状态", cmd_status), ("本机适配", cmd_adapt), ("体检", cmd_check),
                      ("日志（尾部 60 行）", lambda _a: cmd_logs(_a))):
        parts.append("")
        parts.append("── %s ──" % title)
        try:
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                if title.startswith("日志"):
                    _a = argparse.Namespace(**vars(a)); _a.n = 60
                    fn(_a)
                else:
                    fn(a)
            finally:
                sys.stdout = old
            parts.append(buf.getvalue().rstrip())
        except SystemExit as e: # noqa: PERF203
            parts.append("（这一项没取到：退出码 %s）" % getattr(e, "code", "?"))
        except Exception as e: # noqa: BLE001
            parts.append("（这一项没取到：%s）" % str(e)[:120])
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts) + "\n")
    except Exception as e: # noqa: BLE001
        _die("反馈包写不出来（%s）。可以改用菜单里的「看运行状态」把屏幕内容复制出去。" % str(e)[:80])
    print("已生成反馈包：")
    print("   %s" % out)
    print("")
    print("   把这个文件发给作者即可（里面是版本、状态、体检和日志尾部，没有你的聊天内容）。")
    return 0


def _mk(a, **kw):
    d = {"json": False, "url": getattr(a, "url", ""), "token": getattr(a, "token", ""),
         "ids": [], "n": 10, "action": "", "key": "", "value": None, "mode": ""}
    d.update(kw)
    return argparse.Namespace(**d)


def _guide() -> None:
    print("")
    print("说明：这个操作台不用开浏览器，也不会动你的微信；")
    print("      它只是把产品的运行情况读出来给你看。看不懂的就选 2 或 8。")
    print("")


def menu(a) -> int:
    """**中文数字菜单**（双击即用）：不需要记任何命令。"""
    items = [
        ("看运行状态（推荐先看这个）", lambda: cmd_status(_mk(a))),
        ("一键体检（推荐：自动查一遍有没有问题）", lambda: cmd_check(_mk(a))),
        ("看「本机适配」档案（这台机器学到了什么）", lambda: cmd_adapt(_mk(a))),
        ("查问题（按症状查：发不出去 / 不回复 / 表情看不到…）", lambda: cmd_verify(_mk(a))),
        ("看最近在跟谁聊 / 看日志", lambda: _menu_sessions_logs(a)),
        ("真值审核（素材→真值：看最可疑的那一撮）", lambda: _menu_truth(a)),
        ("语料：收本机截图/喂真值（只用你自己的屏幕画面）", lambda: _menu_corpus(a)),
        ("低配模式（老机器卡：一键关掉吃 CPU 的特效）", lambda: _menu_lowspec(a)),
        ("开机自动启动（可选，默认不开）", lambda: _menu_autostart(a)),
        ("暂停 / 恢复机器人", lambda: _menu_pause(a)),
        ("生成反馈包（出问题就选这个，把文件发作者）", lambda: cmd_pack(_mk(a))),
        ("接入诊断 / 重新标定 / 取指纹（连不上或点不准）", lambda: _menu_fixwechat(a)),
        ("直接提反馈（连反馈包都懒得生成时）", lambda: cmd_feedback(_mk(a))),
    ]
    while True:
        print("")
        print("==============================")
        print(" 群相 · 无界面操作台")
        print("  （直接按数字 + 回车就行）")
        print("==============================")
        for i, (title, _f) in enumerate(items, 1):
            print("  %d. %s" % (i, title))
        print("  0. 退出")
        _guide()
        c = _ask("请输入数字后回车（直接回车＝1）：", "1")
        if os.environ.get("PMCTL_DEBUG"):
            sys.stderr.write("[dbg] 菜单读到 %r（isatty=%s）\n" % (c, sys.stdin.isatty()))
        if c is None:                       # 输入流结束（脚本/管道喂完）⇒ 干净退出，不许死循环
            print("（输入结束了，退出。）")
            return 0
        if c in ("0", "q", "Q", "退出", "exit"):
            print("已退出。")
            return 0
        if c == "?":
            for i, (title, _f) in enumerate(items, 1):
                print("  %d) %s" % (i, title))
            continue
        try:
            idx = int(c)
        except ValueError:
            print("（没看懂「%s」——请输入 0 到 %d 之间的数字）" % (c, len(items)))
            continue
        if idx < 1 or idx > len(items):
            print("（%s 超出范围了——请输入 0 到 %d 之间的数字）" % (c, len(items)))
            continue
        print("")
        try:
            items[idx - 1][1]()
        except SystemExit as e: # noqa: PERF203
            # 菜单里某个操作失败（比如服务没起/端口不对）**不许把整个菜单带崩**：
            # `_die` 的提示已经打在屏幕上了，这里只补一句"可以继续选别的"。
            if getattr(e, "code", 0) not in (0, None):
                print("")
                print("（上面这个操作没成功——按它的提示处理；也可以选 8 生成反馈包。）")
        except Exception as e: # noqa: BLE001
            print("（这个操作出错了：%s）" % str(e)[:150])
            print("（可以选 8 生成反馈包，把文件发给作者。）")
        _ask("看完按回车回到菜单…", "")


def _menu_fixwechat(a) -> int:
    """菜单里的"接入/标定/指纹"三连：无界面机器上最常用的救急动作。"""
    print("")
    print("  1. 接入诊断（连不上微信：先看这个）      2. 看/改聊天文件目录")
    print("  3. 重新标定（点不准）                    4. 重取图标指纹（换了主题/DPI）")
    print("  0. 返回")
    c = _ask("请输入数字后回车（直接回车＝0）：", "0") or "0"
    if c == "1":
        return cmd_wechat(_mk(a, what="diag"))
    if c == "2":
        return cmd_wechat(_mk(a, what="dir"))
    if c == "3":
        return cmd_calibrate(_mk(a))
    if c == "4":
        return cmd_fingerprint(_mk(a, what="take"))
    return 0


def _menu_corpus(a) -> int:
    """菜单里的"语料"：先**只扫**（只读，给个预览），要收再收，收完可直接喂真值流水线。"""
    print("  说明：本机截图是**你自己的屏幕画面** —— 网图再多也不能代表这台机器的字体渲染/DPI。")
    print("       扫描阶段**只读**，绝不动你的原图；入库只在语料目录写副本。")
    print("")
    cmd_corpus(_mk(a, what="scan"))
    print("")
    print("  1. 收进语料库（写副本，不改原图）   2. 把已入库的喂进真值流水线（产待审候选）")
    print("  3. 体检已入库的                    4. 只看真值流水线现状（不喂）      0. 返回")
    c = _ask("请输入数字后回车（直接回车＝0）：", "0") or "0"
    if c == "1":
        return cmd_corpus(_mk(a, what="add"))
    if c == "2":
        return cmd_corpus(_mk(a, what="feed"))
    if c == "3":
        return cmd_corpus(_mk(a, what="check"))
    if c == "4":
        return cmd_corpus(_mk(a, what="status"))
    return 0


def _menu_truth(a) -> int:
    """菜单里的"真值审核"：先给统计，再让你按序号确认/否决，或导出待审包（看图审）。"""
    cmd_truth(_mk(a, what="stats"))
    print("")
    print("  1. 看最可疑的 20 条        2. 导出待审包（清单 + 放大裁剪图，**看图审**）")
    print("  3. 确认第 N 条对           4. 判定第 N 条为错          0. 返回")
    c = _ask("请输入数字后回车（直接回车＝0）：", "0") or "0"
    if c == "1":
        return cmd_truth(_mk(a, what="list", index=20))
    if c == "2":
        return cmd_truth(_mk(a, what="export", index=200))
    if c in ("3", "4"):
        # ⛔ 先让用户**看完图**再定：这里必须显式提醒"去打开 crops 图"，不给"凭字串猜"的机会
        print("  ⚠️ 请先 `truth export` 并打开 `crops\pN.png` **看图**，再回来填序号。")
        idx = _ask("第几条（序号）？", "")
        if not idx or not str(idx).strip().isdigit():
            print("  （没填有效序号，已返回。）")
            return 0
        return cmd_truth(_mk(a, what=("ok" if c == "3" else "no"), index=int(idx)))
    return 0


def _menu_sessions_logs(a) -> int:
    c = _ask("  1=最近会话   2=看日志（默认 1）：", "1")
    if c == "2":
        n = _ask("  看最后多少行（默认 80）：", "80")
        try:
            nn = max(1, min(2000, int(n)))
        except ValueError:
            nn = 80
        return cmd_logs(_mk(a, n=nn))
    n = _ask("  看最近几条（默认 10）：", "10")
    try:
        nn = max(1, min(200, int(n)))
    except ValueError:
        nn = 10
    return cmd_sessions(_mk(a, n=nn))


def _menu_lowspec(a) -> int:
    print("  低配模式＝把「好看但吃 CPU」的画面特效关掉（挂件动画、波纹、自定义背景、轮询放宽）。")
    print("  只改这几个设置，不动安全开关；关掉后随时能一键恢复。")
    c = _ask("  1=开（老机器卡就选这个）  2=关（恢复原样）  3=先看看现在的值（默认 1）：", "1")
    if c == "3":
        return cmd_lowspec(_mk(a, action="show"))
    return cmd_lowspec(_mk(a, action="on" if c != "2" else "off"))


def _menu_pause(a) -> int:
    c = _ask("  1=暂停（机器人先别干活）  2=恢复（继续干活）（默认 1）：", "1")
    return cmd_pause(_mk(a, mode="pause" if c != "2" else "resume"))


def cmd_autostart(a) -> int:
    """开机自启：`show` 看状态 / `on` 开 / `off` 关。

    走产品自己的 `/api/autostart`（与网页控制台同一个开关），**默认关**。
    只做**当前用户**级（启动文件夹放一个快捷方式），免管理员；关掉＝删掉那个文件。
    """
    if a.action == "show":
        d = _req(a, "/api/autostart")
        print(d.get("text") or json.dumps(d, ensure_ascii=False))
        return 0
    want = (a.action == "on")
    d = _req(a, "/api/autostart", method="POST", body={"enable": want})
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(d.get("message") or ("已设置" if d.get("ok") else "没设置成功"))
        _t = d.get("text")
        if _t:
            print("   %s" % _t)
    return 0 if d.get("ok") else 1


def _menu_autostart(a) -> int:
    print("  开机自动启动＝开机后自动运行它（**可选**，默认不开）。")
    print("  只在**你当前的用户**下生效（不用管理员），随时可关；关掉就是删掉启动文件夹里那个文件。")
    c = _ask("  1=开启  2=关闭  3=先看看现在的状态（默认 3）：", "3")
    if c == "1":
        return cmd_autostart(_mk(a, action="on"))
    if c == "2":
        return cmd_autostart(_mk(a, action="off"))
    return cmd_autostart(_mk(a, action="show"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pmctl", description="不依赖图形界面的操作入口（走产品本地 API）")
    ap.add_argument("--url", default="", help="控制台地址（默认从 logs/console.url 发现）")
    ap.add_argument("--token", default="", help="访问令牌（默认从地址/配置里取）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON（给脚本用）")
    ap.add_argument("--menu", action="store_true", help="进中文数字菜单（不给子命令时默认就是菜单）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="运行状态").set_defaults(func=cmd_status)
    sub.add_parser("version", help="版本").set_defaults(func=cmd_version)
    sub.add_parser("adapt", help="本机适配档案（只读）").set_defaults(func=cmd_adapt)
    sub.add_parser("doctor", help="status + adapt + check 一把跑完").set_defaults(func=cmd_doctor)
    p = sub.add_parser("verify", help="跑症状检验器（默认全部）"); p.add_argument("ids", nargs="*"); p.set_defaults(func=cmd_verify)
    p = sub.add_parser("check", help="体检"); p.set_defaults(func=cmd_check)
    p = sub.add_parser("wechat", help="接入诊断 / 数据库目录（连不上微信时先看这个）")
    p.add_argument("what", nargs="?", default="diag", choices=("diag", "dir", "recheck"))
    p.set_defaults(func=cmd_wechat)
    p = sub.add_parser("fingerprint", help="图标指纹：看状态 / 重取 / 清掉")
    p.add_argument("what", nargs="?", default="status", choices=("status", "take", "forget"))
    p.add_argument("index", nargs="?", default="")
    p.set_defaults(func=cmd_fingerprint)
    p = sub.add_parser("calibrate", help="重新标定（标定完自动取一次指纹）")
    p.set_defaults(func=cmd_calibrate)
    p = sub.add_parser("feedback", help="提反馈（发不出去会如实报错并保留内容）")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--kind", default="问题反馈")
    p.set_defaults(func=cmd_feedback)
    p = sub.add_parser("truth", help="真值审核（看最可疑的那一撮 / 确认 / 导出待审包）")
    p.add_argument("what", nargs="?", default="stats",
                   choices=("stats", "list", "show", "ok", "no", "export"))
    p.add_argument("index", nargs="?", type=int, default=0)
    p.set_defaults(func=cmd_truth)
    p = sub.add_parser("corpus", help="语料（本机真实截图）：扫描 / 入库 / 喂真值流水线 / 体检 / 看现状")
    p.add_argument("what", nargs="?", default="scan",
                   choices=("scan", "add", "feed", "status", "check"))
    p.add_argument("index", nargs="?", type=int, default=0, help="feed 时的张数上限")
    p.add_argument("--dir", default="", help="源目录（只读；默认 ~/Pictures/Screenshots）")
    p.add_argument("--include-flagged", action="store_true", dest="include_flagged",
                   help="feed 时连隐私标记的图也喂（默认跳过，安全）")
    p.set_defaults(func=cmd_corpus)
    p = sub.add_parser("sessions", help="最近会话"); p.add_argument("n", nargs="?", type=int, default=10); p.set_defaults(func=cmd_sessions)
    p = sub.add_parser("logs", help="日志尾部"); p.add_argument("n", nargs="?", type=int, default=60); p.set_defaults(func=cmd_logs)
    p = sub.add_parser("cfg", help="读/写单个配置键"); p.add_argument("action", choices=("get", "set"))
    p.add_argument("key"); p.add_argument("value", nargs="?"); p.set_defaults(func=cmd_cfg)
    p = sub.add_parser("pause", help="暂停"); p.set_defaults(func=cmd_pause, mode="pause")
    p = sub.add_parser("resume", help="恢复"); p.set_defaults(func=cmd_pause, mode="resume")
    p = sub.add_parser("autostart", help="开机自启（默认关；当前用户级，免管理员）")
    p.add_argument("action", nargs="?", default="show", choices=("on", "off", "show")); p.set_defaults(func=cmd_autostart)
    p = sub.add_parser("pack", help="生成反馈包（出一个 txt，直接发作者）")
    p.set_defaults(func=cmd_pack)
    p = sub.add_parser("lowspec", help="省资源预设"); p.add_argument("action", nargs="?", default="show",
                                                                 choices=("on", "off", "show")); p.set_defaults(func=cmd_lowspec)

    a = ap.parse_args(argv)
    if getattr(a, "menu", False) or not getattr(a, "func", None):
        # ⭐ 没给子命令（例如**双击启动器**、或直接跑 pmctl.py）⇒ 进中文数字菜单：
        #    零基础用户不需要记任何命令，按数字就行。
        return menu(a)
    return int(a.func(a) or 0)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as e: # noqa: BLE001
        _warn("stdout 改 UTF-8 失败（中文可能显示成乱码，不影响功能）：%s" % str(e)[:60])
    sys.exit(main())
