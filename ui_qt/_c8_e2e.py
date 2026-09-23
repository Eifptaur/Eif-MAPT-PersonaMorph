# -*- coding: utf-8 -*-
"""丙-8 真后台 E2E（交付物 5）：面板徽章真实状态 + 保存落盘 + 体检按钮。

链路：进程内起**真 WebUI**（agent/webui.py ThreadingHTTPServer，非 mock）→
Qt 侧 config_io.get_json / agent_bridge.post_json 走真 HTTP →
  ① /api/status 真数据喂 badge_for，断言徽章真实状态；
  ② config_io.write_patch 真写盘，重新读 WX_AGENT_CONFIG 的 config.json 逐键比对；
  ③ POST /api/code-check → 轮询 progress → 真检测 result；
  ④ /api/personas 计数。
隔离纪律（2026-09-18 事故教训）：WX_AGENT_CONFIG 指临时配置；console_url_root
指临时目录 —— 绝不碰产品 logs/console.url。
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

TMP = Path(os.environ.get("TEMP", "/tmp")) / "c8-e2e"
TMP.mkdir(exist_ok=True)
CFG = TMP / "config.json"
CFG.write_text(json.dumps({
    "server": {"enabled": True, "host": "127.0.0.1", "port": 34321, "token": "e2e-token"},
    "store": {"context_tier": 1},
}), encoding="utf-8")
os.environ["WX_AGENT_CONFIG"] = str(CFG)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent.webui import WebUI  # noqa: E402

FAKE_STATUS = {
    "paused": False, "wechat_connected": True,
    "listen": {"groups": 2, "privates": 1},
    "model": {"configured": True, "name": "e2e-model"},
    "tts": {"ready": True},
    "image_gen": {"ready": False, "why": "e2e 未配"},
    "video_gen": {"ready": True},
    "web_search": {"provider": "e2e-search", "enabled": False},
    "tools": {"count": 3, "enabled": 3},
    "version_gate": {"allow": True, "level": "ok"},
    "version": {"wechat": "3.9.12"},
}

ui = WebUI(
    status_provider=lambda: dict(FAKE_STATUS),
    log_buffer=__import__("collections").deque(maxlen=50),
)
ui.console_url_root = str(TMP)          # 地址文件隔离：绝不碰产品 logs/console.url
port = ui.start()
assert port, "WebUI 没起来"

# E2E 隔离注入：agent_bridge.current_url 的权威链（agent.notify_ui.console_url →
# 产品 logs/console.url → 产品 config.json）在生产是守约束只读；隔离环境里把
# current_url 指到本进程 WebUI（仅本脚本内 monkeypatch，不动产品代码）。
# config_io.get_json / post_json / badge_for("server") 全是调用时 from-import
# → patch 模块属性即全部生效。
import agent_bridge  # noqa: E402

agent_bridge.current_url = lambda: f"http://127.0.0.1:{port}/?token=e2e-token"

out = [f"WebUI UP port={port}"]
fails = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global fails
    if not ok:
        fails += 1
    out.append(f"{'OK ' if ok else 'FAIL'}  {label}" + (f"  [{extra}]" if extra else ""))


# ── ① /api/status 真数据 → 徽章（Qt 侧 config_io 走真 HTTP）────────
import config_io  # noqa: E402
import panels_qt  # noqa: E402

st = config_io.get_json("/api/status", timeout=30.0)   # 首次请求含 handler 冷启动（实测 6s+）
check("GET /api/status 真数据", isinstance(st, dict) and st.get("paused") is False)
check("徽章 bot=ok 已就绪（真接口）", panels_qt.badge_for("bot", st)[:2] == ("ok", "已就绪"))
check("徽章 wechat=监听 3 个（真接口）",
      panels_qt.badge_for("wechat", st)[:2] == ("ok", "已连接 · 监听 3 个"))
check("徽章 model=e2e-model（真接口）", panels_qt.badge_for("model", st)[:2] == ("ok", "e2e-model"))
check("徽章 search 关着（真接口）", panels_qt.badge_for("search", st)[:2] == ("idle", "关着"))

# ── ② 保存落盘真验证（write_patch → 重读 config.json 逐键比对）────
patch = {"store.context_tier": 3, "wechat.bot_nickname": "E2E鲸"}
ok, msg = config_io.write_patch(patch)
check("write_patch 返回 ok", ok, msg)
disk = json.loads(CFG.read_text(encoding="utf-8"))


def dig(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


check("config.json 真写入 context_tier=3", dig(disk, "store.context_tier") == 3,
      str(dig(disk, "store.context_tier")))
check("config.json 真写入 bot_nickname", dig(disk, "wechat.bot_nickname") == "E2E鲸",
      str(dig(disk, "wechat.bot_nickname")))
check("GET /api/config 读回一致（内存已换新）",
      dig(config_io.get_json("/api/config", timeout=10.0) or {}, "store.context_tier") == 3)

# ── ③ 体检按钮：真跑代码检测（POST + 150ms 轮询，web 同款）─────────
from agent_bridge import post_json  # noqa: E402

r = post_json("/api/code-check", {"deps": False}, timeout=20.0)
check("POST /api/code-check 已受理", r is not None)
result = None
import time as _t  # noqa: PLC0415
for _i in range(300):                       # web 同款上限 300 轮
    pr = post_json("/api/code-check/progress", {}, timeout=10.0)
    if pr and pr.get("done"):
        result = pr.get("result")
        break
    _t.sleep(0.2)
check("代码检测真跑完成（result.summary）", bool(result and result.get("summary")),
      str((result or {}).get("summary"))[:80])
check("代码检测逐项（checks 非空）", bool(result and result.get("checks")),
      f"{len((result or {}).get('checks') or [])} 项")

# ── ④ /api/personas 计数 ──────────────────────────────────────────
rp = config_io.get_json("/api/personas", timeout=10.0)
pn = len(rp.get("personas") or []) if isinstance(rp, dict) else None
check("GET /api/personas 可用（persona 徽章数据源）", isinstance(pn, int), f"count={pn}")

# ── 收尾 ─────────────────────────────────────────────────────────
ui.stop()
out.append("")
out.append(f"FAILURES={fails}")
(HERE / "_c8_e2e_out.log").write_text("\n".join(out), encoding="utf-8")
print("written fails=", fails)
