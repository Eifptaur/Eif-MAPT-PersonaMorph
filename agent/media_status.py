# -*- coding: utf-8 -*-
"""媒体与语音的只读状态快照（控制台「媒体与语音」面板的唯一数据源）。

面板上每一句话都必须来自**实测**：引擎链是现场探测的，图库数量是现场数的，
转发开关是现场读配置的——**不许写死"支持/可用"**。字段与 `agent/console_html.py` 里
那段渲染一一对应（改字段名要同步改面板，判据见 `scripts/media_ui_selftest.py`）。

返回结构：
    {
      "voice":     agent.voice.status() 的结果（ok / why / decode / recognize / dir / enabled / cfg_engine）
      "voice_cfg": {"enabled": bool, "engine": "auto|sapi|off"}
      "image":     {"enabled", "mode", "dir"(绝对路径), "count"(图库里几张), "sources"}
      "forward":   {"optin": bool, "note": "为什么默认关"}
    }
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def image_dir() -> str:
    """本地图库目录（`image_reply.dir`，支持绝对路径）。"""
    try:
        from .config import get_config
        d = str((get_config().get("image_reply") or {}).get("dir") or "assets/anime")
    except Exception:
        d = "assets/anime"
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def image_count(path: str = "") -> int:
    """图库里有几张可用图（数不到就是 0，不抛异常）。"""
    p = path or image_dir()
    try:
        return len([f for f in os.listdir(p) if os.path.splitext(f)[1].lower() in IMG_EXT])
    except OSError:
        return 0


def snapshot() -> dict:
    from . import voice as V
    from .config import get_config
    cfg = get_config()
    vcfg = cfg.get("voice") or {}
    icfg = cfg.get("image_reply") or {}
    d = image_dir()
    return {
        "voice": V.status(),
        "voice_cfg": {"enabled": bool(vcfg.get("enabled")), "engine": str(vcfg.get("engine") or "auto")},
        "image": {"enabled": bool(icfg.get("enabled")), "mode": str(icfg.get("mode") or "local"),
                  "dir": d, "count": image_count(d), "sources": list(icfg.get("sources") or [])},
        "forward": {"optin": bool((cfg.get("send") or {}).get("file_forward_optin")),
                    "note": "转发视频/文件要过一次系统「选择文件」对话框，会短暂抢前台 ⇒ 默认关"},
    }


if __name__ == "__main__":                     # 直接跑这个文件看一眼（相对导入要靠包路径）
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, ROOT)                   # 让 `agent` 包可导入（脚本方式跑时）
    from agent.media_status import snapshot as _snap
    print(json.dumps(_snap(), ensure_ascii=False, indent=2))
