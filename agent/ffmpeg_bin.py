# -*- coding: utf-8 -*-
"""ffmpeg 可执行文件的**唯一解析入口**（2026-09-16 立）。

为什么要有它：以前四处各写各的（`tts.py` / `video_read.py` / `voice_models.py` / `voice.py`），
而且**全都只查 `PATH`** ⇒ 干净机器上就是"没找到 ffmpeg"，于是语音合成转 wav、视频抽帧、
音频抽取全被卡住（用户那份环境检验报告里的「ffmpeg：没找到（合成要转 wav，必需）」就是这个）。
可 `requirements.txt` 里的 **`imageio-ffmpeg`** 本来就自带一份 ffmpeg 二进制（随 pip 包分发、
用户不用装任何东西）——以前没人用它。

解析顺序（第一个存在的就用）：
  ① 配置 `media.ffmpeg` / `tools.ffmpeg` / `video.ffmpeg`（用户自己指定）
  ② `PATH` 里的 `ffmpeg`
  ③ `imageio-ffmpeg` 自带的那份（`imageio_ffmpeg.get_ffmpeg_exe()`）
返回 `""` ＝ 真没有 ⇒ 调用方照旧**如实报错**，不许假装能用。
"""
import os
import shutil

_CACHE = {}


def _cfg_path() -> str:
    try:
        from .config import get_config
        c = get_config() or {}
        for seg in ("media", "tools", "video"):
            v = str((c.get(seg) or {}).get("ffmpeg") or "").strip()
            if v:
                return v
    except Exception:
        pass
    return ""


def bundled() -> str:
    """`imageio-ffmpeg` 自带的那份（没有 / 取不到就 ""）。"""
    try:
        import imageio_ffmpeg
        p = str(imageio_ffmpeg.get_ffmpeg_exe() or "")
        return p if p and os.path.exists(p) else ""
    except Exception:
        return ""


def path(refresh: bool = False) -> str:
    """按顺序解析；找到的路径会缓存（`refresh=True` 重算）。"""
    if not refresh and _CACHE.get("p"):
        return str(_CACHE["p"])
    for p in (_cfg_path(), shutil.which("ffmpeg") or "", bundled()):
        if p and os.path.exists(p):
            _CACHE["p"] = p
            return p
    return ""


def source() -> str:
    """这一份是从哪儿来的（给控制台如实显示用）：config / PATH / imageio-ffmpeg / 无。"""
    p = path()
    if not p:
        return ""
    if p == _cfg_path():
        return "config"
    try:
        if p == shutil.which("ffmpeg"):
            return "PATH"
    except Exception:
        pass
    return "imageio-ffmpeg"
