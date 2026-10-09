# -*- coding: utf-8 -*-
"""资源站投稿与取件：**客户端这一侧的联网半边**。

对应网站方给的《客户端上传对接说明》（三步投稿 + 口令取件）。本模块是**唯一**
跟投稿接口说话的地方，界面侧只调这里的函数、不自己拼请求。

## 三步为什么不能少
   服务端规定「单个 JSON 请求体最大 1 MB」，而文件可以到 20 MB ⇒
   文件必须走独立直传通道（①换地址 ②往那个地址 PUT 裸字节 ③登记元数据）。
   ⛔ 不能把文件塞进 JSON 里"一步搞定"—— 一定会在 1 MB 处被拒。

## 几条容易踩的（都是文档里明写的，别自作聪明）
   · 判断成败**只看响应体的 `ok` 字段**，不看 HTTP 状态码（个别情况会用非 200 带 `ok:false`）；
   · 直传那一步要**原样带上第①步给的 `uploadHeaders`**（尤其 `content-length`，缺了会 411）；
   · 直传返回 403 `EXPIRED` 是**正常会遇到的**（15 分钟内没传完）⇒ 自动重走第①步，不是错误；
   · 登记返回 400 `INVALID_PARAM` 可能是**命中了隔离区** —— 提交没丢、只是要人工看，
     ⛔ **不许自动重试**（重试只会再进一次隔离区）；
   · 429 要读响应头 `retry-after`，等够了再试；
   · `formLoadedAt` 是**时间陷阱**：服务端看"提交时刻 − 它"是否 < 3000ms，小于就判机器人。
     所以要在**弹表单那一刻**取时间戳，提交时带上（用户不可能 3 秒填完）；
   · `website` 是**蜜罐字段**：正常提交**根本不要带**它。

## 口令
   审核通过后站长侧生成 6 位口令（不含 I/L/O/U）。客户端用「校验」问它在不在，
   用「取件」直接拿文件流。服务端**故意不区分**"口令不存在"和"口令存在但没文件"
   ⇒ 客户端统一按"口令无效"提示。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("persona-morph")

#: 投稿接口前缀。base 由 `market.site_url` 给（⛔ 不写死域名）。
API_PREFIX = "/api/v1"

#: 允许投稿的扩展名 → 直传时要声明的 content-type（照文档 §2 表，⛔ 不自造）。
ALLOWED_EXT = {
    ".zip": "application/zip",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
}

#: 服务端上限（文档 §2 / 附录）。本地先拦，省得白跑一趟网络。
MAX_BYTES = 20 * 1024 * 1024

#: 三栏 kind → 投稿接口认的 kind（照文档 §0 第 3 条：只有这三类能投）。
KIND_MAP = {
    "persona": "persona",
    "skill": "skillEntry",
    "tool": "tool",
}


class UploadError(Exception):
    """投稿过程中的失败。`code` 是接口给的机读码，`message` 是给用户看的那句。"""

    def __init__(self, code: str, message: str, *, retry_after: int = 0) -> None:
        super().__init__(message)
        self.code = str(code or "")
        self.message = str(message or "")
        self.retry_after = int(retry_after or 0)


def _base() -> str:
    """投稿服务端地址。没配 ⇒ 抛（调用方会先查 `site_ready`）。"""
    from . import market  # noqa: PLC0415 —— 循环 import 规避

    site = str((market._cfg() or {}).get("site_url") or "").strip()
    if not site:
        raise UploadError("site-not-ready", market.reason("site-not-ready"))
    return site.rstrip("/")


def sha256_file(path: str) -> str:
    """算文件的 SHA-256（十六进制小写）。**分块读**，别把 20 MB 一次吞进内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def precheck(path: str) -> tuple:
    """上传**之前**的本地检查：文件在不在、扩展名在不在白名单、有没有超 20 MB。

    返回 `(ok, code, message, size, ctype)`。⛔ 不合格就**别发请求**——
    文档 §9 第 2/3 条就是这么要求的（省一次配额）。
    """
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return False, "no-file", "没找到这个文件", 0, ""
    ext = os.path.splitext(p)[1].lower()
    if ext not in ALLOWED_EXT:
        return (False, "unsupported-file-type",
                "这种文件不能投稿，只能传：" + "、".join(sorted(ALLOWED_EXT)),
                0, "")
    try:
        size = os.path.getsize(p)
    except OSError as e:
        return False, "no-file", "读不到这个文件：%s" % e, 0, ""
    if size > MAX_BYTES:
        return (False, "payload-too-large",
                "文件 %.1f MB，超过 20 MB 上限" % (size / 1048576.0), size, "")
    return True, "ok", "", size, ALLOWED_EXT[ext]


def _read_json(resp) -> dict:
    raw = resp.read().decode("utf-8", "replace")
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        raise UploadError("bad-response", "服务器返回的不是 JSON（可能被网络中间层改过）") from None
    return d if isinstance(d, dict) else {}


def _retry_after(resp) -> int:
    try:
        return int(resp.headers.get("retry-after") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _post_json(url: str, body: dict, *, timeout: float = 30.0) -> dict:
    """POST 一个 JSON 并**按 `ok` 字段**判成败（⛔ 不看 HTTP 状态码）。

    失败一律抛 `UploadError`（带接口给的 code / message / retry-after）。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"content-type": "application/json; charset=utf-8",
                 "accept": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            d = _read_json(resp)
            if d.get("ok") is not True:
                raise UploadError(str(d.get("code") or "failed"),
                                  str(d.get("message") or "服务器拒绝了这次请求"),
                                  retry_after=_retry_after(resp))
            return d
    except urllib.error.HTTPError as e:
        # 非 200 也可能带合法的 {ok:false, code, message} —— 先试读，读不出再按 HTTP 说
        try:
            d = _read_json(e)
            raise UploadError(str(d.get("code") or ("http-%d" % e.code)),
                              str(d.get("message") or ("服务器返回 %d" % e.code)),
                              retry_after=_retry_after(e)) from None
        except UploadError:
            raise
        except Exception:  # noqa: BLE001
            raise UploadError("http-%d" % e.code, "服务器返回 %d" % e.code,
                              retry_after=_retry_after(e)) from None
    except urllib.error.URLError as e:
        raise UploadError("network", "连不上资源站：%s" % (getattr(e, "reason", e) or e)) from None


def presign(kind: str, filename: str, size: int, sha256: str) -> dict:
    """第①步：申请直传地址。返回 `{uploadUrl, httpMethod, uploadHeaders, submissionId, submitToken}`。"""
    k = KIND_MAP.get(str(kind or "").strip().lower())
    if not k:
        raise UploadError("kind-not-ready", "这一类资源还不能投稿")
    d = _post_json(_base() + API_PREFIX + "/submissions/presign", {
        "kind": k,
        "filename": str(filename or "")[:255],
        "fileSize": int(size),
        "sha256": str(sha256 or "").lower(),
    })
    for key in ("uploadUrl", "submissionId", "submitToken"):
        if not d.get(key):
            raise UploadError("bad-response", "服务器没给全上传信息（缺 %s）" % key)
    return d


def put_blob(upload_url: str, path: str, headers: dict, *, timeout: float = 120.0) -> dict:
    """第②步：把文件**裸字节** PUT 上去（⛔ 不包 JSON、不 multipart）。

    `headers` 用第①步给的 `uploadHeaders` **原样**——缺 `content-length` 会 411。
    """
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    with open(path, "rb") as f:
        data = f.read()
    # 兜底：服务端要求 content-length，urllib 对 bytes body 会自动加，但显式带上更稳
    hdrs.setdefault("content-length", str(len(data)))
    req = urllib.request.Request(upload_url, data=data, method="PUT", headers=hdrs)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            d = _read_json(resp)
            if d.get("ok") is not True:
                raise UploadError(str(d.get("code") or "failed"), "文件没传成功")
            return d
    except urllib.error.HTTPError as e:
        code = "http-%d" % e.code
        msg = "文件没传成功"
        try:
            d = _read_json(e)
            code = str(d.get("code") or code)
            msg = str(d.get("message") or msg)
        except Exception:  # noqa: BLE001
            pass
        raise UploadError(code, msg, retry_after=_retry_after(e)) from None
    except urllib.error.URLError as e:
        raise UploadError("network", "直传中断：%s" % (getattr(e, "reason", e) or e)) from None


def commit(submission_id: str, submit_token: str, sha256: str, meta: dict,
           *, form_loaded_at: int = 0, timeout: float = 30.0) -> dict:
    """第③步：登记这条提交。

    `meta` 是**对象**，这里负责 `json.dumps` 成字符串（接口要的是字符串，⛔ 不发对象）。
    ⛔ **不带 `website`**（蜜罐字段，带了非空会被判机器人）。
    """
    body = {
        "submissionId": str(submission_id or "")[:64],
        "submitToken": str(submit_token or "")[:4096],
        "sha256": str(sha256 or "").lower(),
        "meta": json.dumps(meta, ensure_ascii=False),
    }
    if form_loaded_at:
        body["formLoadedAt"] = int(form_loaded_at)
    return _post_json(_base() + API_PREFIX + "/submissions", body, timeout=timeout)


def submit_file(kind: str, path: str, *, title: str = "", author: str = "",
                extra_meta: dict | None = None, form_loaded_at: int = 0,
                on_step=None) -> dict:
    """**完整三步**：本地校验 → 申请直传 → PUT → 登记。

    `on_step(step, text)`：可选回调，给界面报进度（`step` 取
    `check` / `presign` / `put` / `commit`）。

    返回 `{ok: True, dedup, submissionId, status, title}`；
    失败抛 `UploadError`（界面照 `message` 显示，`code` 用来判"能不能重试"）。
    """
    def _say(step: str, text: str) -> None:
        if on_step is not None:
            try:
                on_step(step, text)
            except Exception:  # noqa: BLE001 —— 报进度失败不该影响上传
                pass

    _say("check", "正在检查文件…")
    ok, code, msg, size, _ctype = precheck(path)
    if not ok:
        raise UploadError(code, msg)

    name = os.path.basename(path)
    _say("check", "正在算文件指纹…")
    sha = sha256_file(path)

    meta = {"title": str(title or os.path.splitext(name)[0]).strip()[:80],
            "name": str(title or os.path.splitext(name)[0]).strip()[:80],
            "filename": name,
            "author": str(author or "").strip()[:40],
            "fileSize": size,
            "sha256": sha}
    if extra_meta:
        meta.update(extra_meta)

    # ① 申请直传地址
    _say("presign", "正在申请上传地址…")
    pre = presign(kind, name, size, sha)

    # ② 直传。403 EXPIRED 是"正常会遇到的" ⇒ 重走第①步一次
    _say("put", "正在上传文件…")
    try:
        put_blob(str(pre["uploadUrl"]), path, pre.get("uploadHeaders") or {})
    except UploadError as e:
        if e.code != "EXPIRED":
            raise
        _say("presign", "上传地址过期了，正在重新申请…")
        pre = presign(kind, name, size, sha)
        put_blob(str(pre["uploadUrl"]), path, pre.get("uploadHeaders") or {})

    # ③ 登记
    _say("commit", "正在登记投稿…")
    d = commit(str(pre["submissionId"]), str(pre["submitToken"]), sha, meta,
               form_loaded_at=form_loaded_at)
    return {"ok": True, "dedup": bool(d.get("dedup")),
            "submissionId": str(d.get("submissionId") or pre["submissionId"]),
            "status": str(d.get("status") or "pending"),
            "title": meta["title"], "filename": name}


# ------------------------------------------------------------------ 口令取件


def verify_codes(codes: list, *, timeout: float = 20.0) -> list:
    """批量校验口令（1~20 项）。返回接口原样的 `results` 数组。

    每项形如 `{code, found, kind?, id?, title?, version?, fileSize?, sha256?}`。
    """
    cs = [str(c or "").strip().upper() for c in (codes or []) if str(c or "").strip()]
    if not cs:
        return []
    d = _post_json(_base() + API_PREFIX + "/install-codes/verify", {"codes": cs[:20]},
                   timeout=timeout)
    rows = d.get("results")
    return rows if isinstance(rows, list) else []


def download_by_code(code: str, *, timeout: float = 300.0) -> tuple:
    """用口令直接取件。返回 `(bytes, filename, content_type)`。

    ⛔ 失败**统一按"口令无效"**处理 —— 服务端故意不区分"口令不存在"与"存在但没文件"
    （防枚举），客户端不必（也不该）去猜是哪一种。
    """
    c = str(code or "").strip().upper()
    if not c:
        raise UploadError("bad-code", "口令是空的")
    url = _base() + API_PREFIX + "/install-codes/" + urllib.parse.quote(c) + "/download"
    req = urllib.request.Request(url, method="GET", headers={"accept": "*/*"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = str(resp.headers.get("content-type") or "application/octet-stream")
            # 文件名优先取响应头里的建议名，没有就按口令 + 类型推一个
            disp = str(resp.headers.get("content-disposition") or "")
            fname = ""
            for piece in disp.split(";"):
                piece = piece.strip()
                if piece.lower().startswith("filename="):
                    fname = piece.split("=", 1)[1].strip().strip('"')
                    break
            return raw, fname, ctype
    except urllib.error.HTTPError as e:
        # 404 是文档里写的"口令无效"统一出口
        raise UploadError("code-not-found", "口令无效（或这条还没通过审核）",
                          retry_after=_retry_after(e)) from None
    except urllib.error.URLError as e:
        raise UploadError("network", "连不上资源站：%s" % (getattr(e, "reason", e) or e)) from None


def health(*, timeout: float = 8.0) -> dict:
    """探一下服务端在不在（启动时或上传前用）。"""
    url = _base() + API_PREFIX + "/health"
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            d = _read_json(resp)
            return {"ok": d.get("ok") is True, "version": str(d.get("version") or "")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "version": "", "error": str(e)}
