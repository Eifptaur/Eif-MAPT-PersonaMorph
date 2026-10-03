# -*- coding: utf-8 -*-
"""落盘原子化 —— **二进制/图片/任意字节流**的统一入口（P0-6）。

**为什么需要这个模块**：`persist.atomic_write_json` / `atomic_write_text` 只管 JSON 与文本。
抓图、媒体、下载物这些**二进制**落盘点原先各自 `直接写目标路径`：
写到一半被杀 ⇒ 目录里留下**半截 PNG**（下游 `imread` 拿到坏图或 None），
零字节文件被当成"截到了"、`except` 掉之后没人知道写没写成功 —— 与 JSON 那一族是同一个病。

**与 `persist` 的分工**（不要互相替代）：
- `persist`          —— JSON / 文本（带 `json.dump`、坏档隔离、`_sweep_own_orphans`）；
- `atomic_io`（本模块）—— 任意**字节流**（`writer(fh)` 自己往文件句柄里写），
  以及"从内存字节直接落档"（`atomic_write_bytes`）。
两处共用同一条配方与同一个临时名约定（`<path>.<pid>.<rand>.tmp`），
`persist._sweep_own_orphans` 因此也认本模块留下的孤儿。

**五道纪律**（缺一条就不是"原子写"）：
1. 临时名 `.${base}.partial-${pid}-${rand}${ext}` —— **点前缀隐藏**（不与正式产物混淆）
   ＋ **pid ＋ 随机**（两个写者不会打开同一个临时档、内容不会互相穿插）；
2. 写完 **`stat` 校验 `is_file() and size > 0`** —— **零字节不算成功**；
3. **查停止标志**（`control.is_paused()/is_stopped()`）—— **喊停就不 rename**，
   直接把临时档删掉，目标路径保持"没有这次产物"（宁可没有，不要半截）；
4. `persist.replace_into(tmp, final)` —— 复用那一套**有界退避重试**（Windows 共享冲突
   实测裸 `os.replace` 有 66%~71% 失败/静默丢写）；
5. 临时档**不进"已产出清单"**，清理放 `finally`（异常/喊停/校验失败都不留残留）。
"""
from __future__ import annotations

import logging
import os
import secrets

from . import persist

log = logging.getLogger("persona-morph")

#: 临时档的**点前缀**（隐藏）＋ 识别后缀。`persist._sweep_own_orphans` 目前只认它自己那套
#: `.<pid>.<rand>.tmp`；本模块用 `.<base>.partial-<pid>-<rand><ext>`，孤儿由本模块自清。
_PARTIAL_MARK = ".partial-"


def partial_path(final_path: str) -> str:
    """给目标路径算一个**唯一**的临时名。

    形态：`<dir>/.<base>.partial-<pid>-<rand><ext>`
    —— **点前缀**让它在资源管理器/`ls` 默认视图里隐藏（不会混进用户的产物清单），
    **pid ＋ 4 字节随机**保证同一目标被两个写者同时写时各写各的临时档。
    """
    d = os.path.dirname(os.path.abspath(final_path))
    base = os.path.basename(final_path)
    stem, ext = os.path.splitext(base)
    name = ".%s%s%d-%s%s" % (stem, _PARTIAL_MARK, os.getpid(), secrets.token_hex(4), ext)
    return os.path.join(d, name)


def _halted() -> str:
    """返回停止原因（没喊停则空串）。

    ⛔ 用 `try/except` 而不是硬导入：`control` 依赖 `data/` 目录与文件系统，
    在判据/工具脚本里可能不可用；**导入失败不当作"已喊停"**（否则工具脚本永远写不出文件），
    但要把失败记一行痕（本仓静默点棘轮只许降）。
    """
    try:
        from . import control
        if control.is_stopped():
            return "机器人已停止"
        if control.is_paused():
            return "机器人已暂停"
    except Exception as e:
        log.debug("停止标志不可读（按未喊停处理）：%s", e)
    return ""


def atomic_write_bytes(final_path: str, data: bytes, *, halt_check: bool = True) -> bool:
    """把一段内存字节**原子**落档。成功 True；任何一步不成立 ⇒ False（目标路径**未被触碰**）。

    `halt_check=False` 用于**不归属发送链**的落盘点（如体检报告、探针留证）：
    它们与"机器人暂停"无关，喊停不该让它们写不出来。
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        log.warning("原子写拒绝空内容（零字节不算成功）：%s", final_path)
        return False
    tmp = partial_path(final_path)

    def _w(fh) -> None:
        fh.write(bytes(data))

    return atomic_write(final_path, _w, halt_check=halt_check, _tmp=tmp)


def atomic_write(final_path: str, writer, *, halt_check: bool = True,
                 fsync: bool = True, _tmp: str = "") -> bool:
    """通用原子写：`writer(fh)` 往临时档写 → 校验非空 → 换档。返回是否成功。

    `writer` 收到的是**已打开的二进制文件句柄**，自己决定写什么（`cv2.imencode` 结果、
    `PIL` 的 `img.save(fh, ...)`、下载流的分块拷贝……）。

    **校验与清理的口径**：
      · 写完 `stat` 拿不到、或 `size == 0` ⇒ 判**失败**（临时档删掉）——
        "空文件"在下游会被当成正常产物（`imread` 返回 None 才算发现），这里提前拦住；
      · `halt_check` 且此刻已喊停 ⇒ 判**失败**（临时档删掉）——
        中途被暂停时，让"这一份产物不存在"比"留一份来路不明的半成品"更安全；
      · 换档走 `persist.replace_into`（失败**抛** `OSError`）⇒ 这里转成 `False`，
        并把原因记进 `persist.REPLACE_FAILURES` 供运维/判据核对。
    """
    tmp = _tmp or partial_path(final_path)
    try:
        d = os.path.dirname(os.path.abspath(final_path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "wb") as fh:
            writer(fh)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        st = os.stat(tmp)
        if not os.path.isfile(tmp) or int(st.st_size) <= 0:
            log.warning("原子写：临时档为空或不是文件，判失败（目标未动）：%s", final_path)
            return False
        _why = _halted() if halt_check else ""
        if _why:
            log.info("原子写：%s ⇒ 丢弃这一份（目标未动）：%s", _why, os.path.basename(final_path))
            return False
        persist.replace_into(tmp, final_path)
        return True
    except Exception as e:
        log.warning("原子写失败（目标未动）：%s ⇒ %s", final_path, e)
        return False
    finally:
        # 换档成功时 tmp 已不存在；失败/喊停/异常三种情况都要清掉自己的临时档
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception as e2:
            log.warning("原子写临时档删不掉（不影响目标档）：%s ⇒ %s", tmp, e2)


def save_image(final_path: str, image, *, fmt: str = "", halt_check: bool = True, **save_kw) -> bool:
    """把 PIL 图像**原子**落档（抓图/截图/裁剪块用这条）。

    `image` 需要提供 `save(fp, format, **kw)`（PIL 的 `Image` 即是）。`fmt` 留空时
    由调用方从扩展名推（`.png` → `PNG`）；**显式传 `fmt` 可避免"文件名没后缀"时存成默认格式**。
    """
    _fmt = str(fmt or "").strip().upper()
    if not _fmt:
        _fmt = os.path.splitext(final_path)[1].lstrip(".").upper() or "PNG"

    def _w(fh) -> None:
        image.save(fh, _fmt, **save_kw)

    return atomic_write(final_path, _w, halt_check=halt_check)


def list_partials(dir_path: str) -> list:
    """列出某目录下的**残留临时档**（判据/运维用：正常情况下应当恒为空）。

    只认本模块的命名形态（`<name>.partial-`），不会误报 `persist` 的 `.tmp` 与
    用户自己的文件。返回绝对路径列表（已排序，便于比对）。
    """
    out = []
    try:
        for nm in os.listdir(dir_path or "."):
            if _PARTIAL_MARK in nm:
                out.append(os.path.join(dir_path, nm))
    except OSError:
        return []
    return sorted(out)
