# -*- coding: utf-8 -*-
"""投稿上传 / 口令取件 的假后端联调判据（群相）。

为什么要有这条：`agent/market_upload.py` 是**唯一联网半边**，对着网站方的
《客户端上传对接说明》写；网站还没上线，**不能真的去连**。所以这里起一个
**本地假后端**，只做两件事：
  ① 按文档把请求**原样收下来**，逐条断言"发出去的东西合不合规"；
  ② 造文档里写明的**失败场景**（403 EXPIRED / 400 隔离区 / 429 限流），
     断言客户端的反应符合口径（尤其"不许重试"那条）。

⛔ 这条判据**不碰真网络**：假后端绑 127.0.0.1 的随机端口，跑完立刻关。

用法：`py -3 scripts/market_upload_selftest.py`
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_OK = 0
_FAIL = 0


def ck(name: str, cond: bool, extra: str = "") -> None:
    global _OK, _FAIL
    if cond:
        _OK += 1
        print("  OK    " + name + (("   [%s]" % extra) if extra else ""))
    else:
        _FAIL += 1
        print("  FAIL  " + name + (("   [%s]" % extra) if extra else ""))


# ------------------------------------------------------------------ 假后端

class _Scn:
    """一次测试要假后端怎么表现。字段都是"第几次调某接口"级别的开关。"""

    def __init__(self) -> None:
        self.seen: list = []          # 全部收到的请求：("presign"/"put"/"commit", 详情)
        self.expire_once = False      # 第一次 PUT 回 403 EXPIRED
        self.isolated = False         # commit 回 400 INVALID_PARAM（命中隔离区）
        self.rate_limited = False     # commit 回 429
        self.dedup = False            # commit 回 dedup=true
        self.put_hits = 0
        self.force_filename = ""      # 非空 ⇒ download 的 content-disposition 用这个名字


def _make_handler(scn: _Scn):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音
            pass

        def _send(self, obj: dict, status: int = 200, headers: dict | None = None) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(str(k), str(v))
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict:
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                d = {}
            return d if isinstance(d, dict) else {}

        # ---------------- 路由

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.endswith("/submissions/presign"):
                d = self._body()
                scn.seen.append(("presign", d))
                return self._send({
                    "ok": True,
                    "uploadUrl": "http://127.0.0.1:%d/blob/%d" % (
                        self.server.server_address[1], len(scn.seen)),
                    "httpMethod": "PUT",
                    "uploadHeaders": {"content-type": "application/octet-stream"},
                    "submissionId": "sub_test_1",
                    "submitToken": "tk_test_1",
                })
            if path.endswith("/submissions"):
                d = self._body()
                scn.seen.append(("commit", d))
                if scn.rate_limited:
                    return self._send({"ok": False, "code": "RATE_LIMITED",
                                       "message": "太频繁了"}, status=429,
                                      headers={"retry-after": "42"})
                if scn.isolated:
                    return self._send({"ok": False, "code": "INVALID_PARAM",
                                       "message": "内容需要人工复核"}, status=400)
                return self._send({"ok": True, "dedup": scn.dedup,
                                   "submissionId": d.get("submissionId"),
                                   "status": "pending"})
            if path.endswith("/install-codes/verify"):
                d = self._body()
                scn.seen.append(("verify", d))
                cs = d.get("codes") or []
                return self._send({"ok": True, "results": [
                    {"code": str(c), "found": str(c) == "ABCDEF",
                     "title": "示例作品" if str(c) == "ABCDEF" else None,
                     "fileSize": 3 if str(c) == "ABCDEF" else None,
                     "sha256": hashlib.sha256(b"abc").hexdigest()
                     if str(c) == "ABCDEF" else None}
                    for c in cs
                ]})
            if path.endswith("/health"):
                scn.seen.append(("health", {}))
                return self._send({"ok": True, "service": "market"})
            return self._send({"ok": False, "code": "not-found"}, status=404)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if "/install-codes/" in path and path.endswith("/download"):
                code = path.split("/install-codes/", 1)[1].rsplit("/", 1)[0]
                scn.seen.append(("download", {"code": code}))
                if code != "ABCDEF":
                    return self._send({"ok": False, "code": "NOT_FOUND",
                                       "message": "口令无效"}, status=404)
                raw = b"abc"
                self.send_response(200)
                self.send_header("content-type", "application/zip")
                self.send_header("content-length", str(len(raw)))
                self.send_header("content-disposition",
                                 'attachment; filename="%s"'
                                 % (scn.force_filename or "demo.zip"))
                self.end_headers()
                self.wfile.write(raw)
                return
            if path.endswith("/health"):
                scn.seen.append(("health", {}))
                return self._send({"ok": True, "service": "market"})
            return self._send({"ok": False, "code": "not-found"}, status=404)

        def do_PUT(self):  # noqa: N802
            scn.put_hits += 1
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b""
            scn.seen.append(("put", {"len": len(raw),
                                     "ctype": self.headers.get("content-type"),
                                     "sha": hashlib.sha256(raw).hexdigest()}))
            if scn.expire_once and scn.put_hits == 1:
                return self._send({"ok": False, "code": "EXPIRED",
                                   "message": "上传地址已过期"}, status=403)
            return self._send({"ok": True})

    return H


class _Server:
    def __init__(self, scn: _Scn):
        self.scn = scn
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(scn))
        self.port = self.srv.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def close(self) -> None:
        try:
            self.srv.shutdown()
            self.srv.server_close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ 判据

def _point_at(base: str) -> None:
    """把 `market.site_url` 指到假后端 —— 走真配置（内存 + 落盘），不 monkeypatch 内部函数。

    ⛔ 不用 `config_io` 写：那个模块在 `ui_qt/` 下、且只有读接口；后端这一侧读的是
    `agent.config.get_config()["market"]`（见 `market._cfg`）⇒ 必须写进同一份。
    """
    from agent import config as _cfgmod
    cfg = dict(_cfgmod.get_config() or {})
    mk = dict(cfg.get("market") or {})
    mk["site_url"] = base
    mk["site_ready"] = bool(base)
    cfg["market"] = mk
    _cfgmod.set_config(cfg)
    _cfgmod.save_config(cfg)


def _mkfile(suffix: str, size: int = 64) -> str:
    fd, p = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(p, "wb") as f:
        f.write(b"x" * size)
    return p


def t_prereq() -> None:
    print("\n[market] 前置：模块与文档口径常量")
    from agent import market_upload as mu
    ck("能取到 agent/market_upload.py", bool(mu))
    ck("API 前缀是 /api/v1", mu.API_PREFIX == "/api/v1")
    ck("上限是 20 MB", mu.MAX_BYTES == 20 * 1024 * 1024,
       "%.0f MB" % (mu.MAX_BYTES / 1048576.0))
    ck("三类映射对得上（persona/skill/tool → persona/skillEntry/tool）",
       mu.KIND_MAP.get("persona") == "persona"
       and mu.KIND_MAP.get("skill") == "skillEntry"
       and mu.KIND_MAP.get("tool") == "tool")
    ck("白名单不含 exe/dll（可执行不能投稿）",
       ".exe" not in mu.ALLOWED_EXT and ".dll" not in mu.ALLOWED_EXT)
    ck("白名单含 .zip（本项目投稿就是 zip）", ".zip" in mu.ALLOWED_EXT)


def t_precheck_local() -> None:
    print("\n[market] 本地预检：不合格时**一个请求都不发**")
    from agent import market_upload as mu
    p_bad = _mkfile(".exe")
    try:
        ok, code, _msg, _sz, _ct = mu.precheck(p_bad)
        ck("exe 被本地拦下（不发请求）", ok is False and code == "unsupported-file-type")
    finally:
        os.unlink(p_bad)

    p_big = _mkfile(".zip", size=64)
    try:
        ok, code, _msg, _sz, _ct = mu.precheck(p_big)
        ck("合规 zip 放行", ok is True and code == "ok")
    finally:
        os.unlink(p_big)

    ok, code, _msg, _sz, _ct = mu.precheck("X:/nope/none.zip")
    ck("不存在的文件被拦", ok is False and code == "no-file")


def t_three_steps() -> None:
    print("\n[market] 三步直传：请求体/头逐条对齐文档")
    from agent import market_upload as mu
    scn = _Scn()
    srv = _Server(scn)
    p = _mkfile(".zip", size=128)
    try:
        _point_at(srv.base)
        r = mu.submit_file("persona", p, title="测试作品", author="甲",
                           form_loaded_at=1_700_000_000_000)
        ck("三步都成功、返回 ok", r.get("ok") is True, str(r.get("status")))
        kinds = [k for k, _ in scn.seen]
        ck("调用顺序是 presign → put → commit",
           kinds[:3] == ["presign", "put", "commit"], "→".join(kinds[:3]))

        pre = dict(scn.seen[0][1])
        ck("presign 用映射后的 kind（persona）", pre.get("kind") == "persona")
        ck("presign 带 sha256 且是小写十六进制",
           isinstance(pre.get("sha256"), str) and pre["sha256"].islower()
           and len(pre["sha256"]) == 64)
        ck("presign 的 fileSize 与真实文件一致", pre.get("fileSize") == 128,
           str(pre.get("fileSize")))
        ck("presign 的 filename 是 basename（不带路径）",
           "/" not in str(pre.get("filename")) and "\\" not in str(pre.get("filename")),
           str(pre.get("filename")))

        put = scn.seen[1][1]
        ck("PUT 的是**裸字节**（长度＝文件长度）", put.get("len") == 128,
           str(put.get("len")))
        ck("PUT 的 content-type 沿用第①步给的头",
           put.get("ctype") == "application/octet-stream", str(put.get("ctype")))
        ck("PUT 上去的字节 sha256 == 本地算的",
           put.get("sha") == mu.sha256_file(p))

        com = dict(scn.seen[2][1])
        ck("commit 带 submissionId / submitToken",
           com.get("submissionId") == "sub_test_1" and com.get("submitToken") == "tk_test_1")
        ck("commit 的 meta 是**字符串**（不是对象）",
           isinstance(com.get("meta"), str), type(com.get("meta")).__name__)
        m = json.loads(com.get("meta") or "{}")
        ck("meta 可被反解、且标题按口径填了", m.get("title") == "测试作品")
        ck("meta 的 sha256 与文件一致", m.get("sha256") == mu.sha256_file(p))
        ck("⛔ commit **不带蜜罐字段 website**", "website" not in com)
        ck("commit 带 formLoadedAt（时间陷阱字段）",
           com.get("formLoadedAt") == 1_700_000_000_000, str(com.get("formLoadedAt")))
    finally:
        srv.close()
        os.unlink(p)
        _point_at("")


def t_expired_retry() -> None:
    print("\n[market] 403 EXPIRED：自动重走第①步（文档说这是正常会遇到的）")
    from agent import market_upload as mu
    scn = _Scn()
    scn.expire_once = True
    srv = _Server(scn)
    p = _mkfile(".zip", size=64)
    try:
        _point_at(srv.base)
        r = mu.submit_file("tool", p, title="x")
        ck("过期后仍最终成功", r.get("ok") is True)
        kinds = [k for k, _ in scn.seen]
        ck("出现两次 presign（重走了第①步）",
           kinds.count("presign") == 2, "→".join(kinds))
        ck("PUT 被调了两次（第一次 403）", scn.put_hits == 2, str(scn.put_hits))
        ck("第二次 PUT 之后才 commit", kinds.index("commit") > 2)
    finally:
        srv.close()
        os.unlink(p)
        _point_at("")


def t_isolated_no_retry() -> None:
    print("\n[market] 400 INVALID_PARAM：判为命中隔离区 ⇒ **不许重试**")
    from agent import market_upload as mu
    scn = _Scn()
    scn.isolated = True
    srv = _Server(scn)
    p = _mkfile(".zip", size=64)
    try:
        _point_at(srv.base)
        err = None
        try:
            mu.submit_file("persona", p, title="x")
        except mu.UploadError as e:
            err = e
        ck("抛出 UploadError（不是静默成功）", err is not None)
        ck("错误码原样带出 INVALID_PARAM（界面靠它判'不许重试'）",
           getattr(err, "code", "") == "INVALID_PARAM", str(getattr(err, "code", "")))
        kinds = [k for k, _ in scn.seen]
        ck("⛔ 没有重试：presign 只有 1 次", kinds.count("presign") == 1,
           "→".join(kinds))
        ck("⛔ 没有重试：commit 只有 1 次", kinds.count("commit") == 1)
    finally:
        srv.close()
        os.unlink(p)
        _point_at("")


def t_rate_limited() -> None:
    print("\n[market] 429：把 retry-after 带出来（界面照着算等待）")
    from agent import market_upload as mu
    scn = _Scn()
    scn.rate_limited = True
    srv = _Server(scn)
    p = _mkfile(".zip", size=64)
    try:
        _point_at(srv.base)
        err = None
        try:
            mu.submit_file("persona", p, title="x")
        except mu.UploadError as e:
            err = e
        ck("抛出 UploadError", err is not None)
        ck("错误码是 RATE_LIMITED", getattr(err, "code", "") == "RATE_LIMITED",
           str(getattr(err, "code", "")))
        ck("retry-after 被读出来（42 秒）", int(getattr(err, "retry_after", 0)) == 42,
           str(getattr(err, "retry_after", 0)))
    finally:
        srv.close()
        os.unlink(p)
        _point_at("")


def t_dedup() -> None:
    print("\n[market] 重复投稿：dedup=true 如实回传")
    from agent import market_upload as mu
    scn = _Scn()
    scn.dedup = True
    srv = _Server(scn)
    p = _mkfile(".zip", size=64)
    try:
        _point_at(srv.base)
        r = mu.submit_file("persona", p, title="x")
        ck("dedup 标志被原样回传（不谎报新投稿）", r.get("dedup") is True)
    finally:
        srv.close()
        os.unlink(p)
        _point_at("")


def t_code_flow() -> None:
    print("\n[market] 口令：校验 + 取件")
    from agent import market_upload as mu
    scn = _Scn()
    srv = _Server(scn)
    try:
        _point_at(srv.base)
        rs = mu.verify_codes(["ABCDEF", "ZZZZZZ"])
        ck("校验返回两条结果", len(rs) == 2, str(len(rs)))
        ck("存在的口令 found=True", any(x.get("found") for x in rs))
        v = dict(scn.seen[0][1])
        ck("校验请求带 codes 数组", isinstance(v.get("codes"), list))
        ck("口令被规范成大写去空格（服务端字符集要求）",
           all(str(c).isupper() for c in v.get("codes") or []),
           str(v.get("codes")))

        data, fn, ct = mu.download_by_code("ABCDEF")
        ck("取回内容与假后端给的一致", data == b"abc", repr(data))
        ck("文件名从 content-disposition 取到", fn == "demo.zip", str(fn))
        ck("拿到了 content-type", "zip" in str(ct), str(ct))

        err = None
        try:
            mu.download_by_code("ZZZZZZ")
        except mu.UploadError as e:
            err = e
        ck("无效口令抛 UploadError", err is not None)
        ck("无效口令统一说法（不区分'不存在'与'没文件'）",
           "口令" in str(getattr(err, "message", "")),
           str(getattr(err, "message", "")))
    finally:
        srv.close()
        _point_at("")


def t_not_ready() -> None:
    print("\n[market] 站点没配 ⇒ 明确报错，不许瞎连")
    from agent import market_upload as mu
    _point_at("")
    err = None
    try:
        mu.presign("persona", "a.zip", 1, "0" * 64)
    except mu.UploadError as e:
        err = e
    ck("没配 site_url 时抛 site-not-ready",
       err is not None and getattr(err, "code", "") == "site-not-ready",
       str(getattr(err, "code", "")))


def t_path_traversal() -> None:
    print("\n[market] 取件落盘：basename 防路径穿越 + 哈希对不上就丢")

    # 落盘那段是 `WebUI.start()` 里注册的**闭包**（`_rapi_market_code_get_post`，8 空格缩进）
    # —— 运行时才生成，类上取不到 ⇒ 这里分两路验：
    #   ① **源码闸门**（AST 级）：那两句防护（拉 basename、比哈希）必须写在函数体里，
    #      且 `os.path.basename` 的结果必须**真的用在** join 上（不是取了不用）；
    #   ② **行为复现**：把同样的两步在本地跑一遍，证明"带 .. 的名字落不出去"。
    import ast
    import os as _os
    import shutil
    import tempfile as _tf

    src = _open(_os.path.join(_ROOT, "agent", "webui.py"))
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "_rapi_market_code_get_post":
            fn = node
            break
    ck("源码里能找到取件落盘那个闭包（分母守卫）", fn is not None,
       "_rapi_market_code_get_post")
    if fn is None:
        return
    body = ast.get_source_segment(src, fn) or ""
    ck("落盘前**拉了 basename**（防路径穿越）", "basename" in body)
    ck("落盘路径由 basename 参与拼（不是原样 join 服务端给的 name）",
       "os.path.join(save_dir, name)" in body)
    ck("落盘前**比过 sha256**（对不上就丢）",
       "sha256" in body and "hash-mismatch" in body)
    ck("makedirs 在写文件之前（目录不存在也不会炸）",
       body.index("makedirs") < body.index("open(dest"))

    # ② 行为复现：与源码里同口径的两步
    d = _tf.mkdtemp()
    try:
        name = _os.path.basename("../evil.zip")   # ← 与源码同一句
        dest = _os.path.join(d, name)
        ck("带 .. 的名字被拉平后落在目标目录内",
           _os.path.dirname(_os.path.abspath(dest)) == _os.path.abspath(d),
           dest)
        ck("拉平后文件名里没有 ..", ".." not in _os.path.basename(dest))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _open(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def main() -> int:
    # 判据要改真配置 ⇒ 先留底、跑完还原
    from agent import config as _cfgmod
    bak = dict(_cfgmod.get_config() or {}).get("market")
    try:
        t_prereq()
        t_precheck_local()
        t_three_steps()
        t_expired_retry()
        t_isolated_no_retry()
        t_rate_limited()
        t_dedup()
        t_code_flow()
        t_not_ready()
        t_path_traversal()
    finally:
        try:
            cfg = dict(_cfgmod.get_config() or {})
            if bak is None:
                cfg.pop("market", None)
            else:
                cfg["market"] = bak
            _cfgmod.set_config(cfg)
            _cfgmod.save_config(cfg)
        except Exception:  # noqa: BLE001
            pass
    print("\n%d 通过 / %d 失败" % (_OK, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
