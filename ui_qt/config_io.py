# -*- coding: utf-8 -*-
"""配置读写桥 —— Qt 面板 ⇆ agent/config.json 的唯一通道。

**写入口径与 web 控制台完全一致**（agent/webui.py `_rapi_config_post` L1886-1912
逐条对齐，配置字段语义不变）：
    1. 面板收集 {点路径: 新值} → 组装成嵌套 dict（部分字段保存不丢段）
    2. `new_cfg = deep_merge(get_config(), patch)` —— 新值优先、缺失键保留旧值；
       deep_merge 返回全新深拷贝，绝不原地改单例（webui L1891-1892 同一条血泪）
    3. `save_config(new_cfg)` **先落盘**（原子写，失败抛异常 → 如实报，不崩）
    4. `set_config(new_cfg)` 落盘成功**才**换内存（盘与内存要么一起新、要么一起旧）
    5. `_protect_secrets` 掩码保护 Qt 侧天然不需要：掩码只出现在 web GET 输出侧，
       Qt 直读 get_config() 拿到的本来就是真实值，不存在"掩码写脏真实密钥"。

`wechat.data_dir`（微信数据目录）Qt 侧不代持：web 对它有专用校验闸
`_wechat_dir_conflict`（webui L235/L1897），Qt 侧剔除该键并如实提示去网页控制台改。

读入口径：同进程直连 `get_config()`（指纹失效自动重载 —— 网页控制台同进程改的
配置，Qt 面板重建/刷新即拿到最新值）。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Qt 侧不代持的键：web 有专用校验/专用 API 的写入通道
_BLOCKED = {"wechat.data_dir"}


def read_path(dotpath: str, default=None):
    """按点路径读当前配置值；键缺失返回 default。"""
    if not dotpath:
        return default
    try:
        from agent.config import get_config # noqa: PLC0415

        cur = get_config()
        for part in dotpath.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur
    except Exception: # noqa: BLE001
        return default


def _nest(patch: dict[str, object]) -> dict:
    """{点路径: 值} → 嵌套 dict（与 web data-cfg 收集后的 POST body 同构）。"""
    out: dict = {}
    for dot, val in patch.items():
        parts = dot.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
            if not isinstance(cur, dict): # 键冲突（理论不该发生）→ 丢弃这段
                break
        else:
            cur[parts[-1]] = val
    return out


def write_patch(patch: dict[str, object]) -> tuple[bool, str]:
    """{点路径: 新值} → web 同款深合并落盘。返回 (成功?, 人话说明)。"""
    if not patch:
        return False, "没有可保存的改动"
    blocked = sorted(k for k in patch if k in _BLOCKED)
    clean = {k: v for k, v in patch.items() if k not in _BLOCKED}
    if not clean:
        return False, "该项请在网页控制台改（Qt 侧不代持）：" + "、".join(blocked)
    try:
        from agent.config import deep_merge, get_config, save_config, set_config

        new_cfg = deep_merge(get_config(), _nest(clean))
        save_config(new_cfg) # 先落盘（原子写；失败抛异常 → 如实报）
        set_config(new_cfg) # 落盘成功才换内存（webui L1906-1907 同款顺序）
        note = "已保存并生效"
        if blocked:
            note += "（未保存「微信数据目录」——该项请在网页控制台改）"
        return True, note
    except Exception as e: # noqa: BLE001
        return False, f"保存失败：{e}"


def write_full(raw_text: str) -> tuple[bool, str]:
    """配置格式面板：整份 JSON 校验 → 备份 → 落盘（"改前先备份"的承诺真兑现）。"""
    try:
        parsed = json.loads(raw_text)
    except Exception as e: # noqa: BLE001
        return False, f"JSON 解析失败，未写入：{e}"
    if not isinstance(parsed, dict):
        return False, "配置文件顶层必须是对象（{…}），未写入"
    from agent.config import CONFIG_FILE, set_config # noqa: PLC0415

    target = Path(CONFIG_FILE)
    try:
        if target.exists():
            bak = target.with_name(
                f"{target.stem}.bak-qt-{__import__('time').strftime('%Y%m%d-%H%M%S')}{target.suffix}"
            )
            bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        from agent.config import save_config # noqa: PLC0415

        save_config(parsed)
        set_config(parsed)
        return True, "已保存（原文件已备份在同目录）"
    except Exception as e: # noqa: BLE001
        return False, f"保存失败：{e}"


def get_json(api: str, timeout: float = 1.5, err_box: dict | None = None) -> dict | None:
    """GET 后端接口（/api/status 等）；连不上返回 None（调用方如实展示）。

    err_box 传 dict 时，异常原文写入 err_box["err"]（超时/拒绝连接可区分）；
    默认 None 保持旧行为：静默返回 None。
    """
    try:
        from addr import join_url # noqa: PLC0415
        from agent_bridge import current_url # noqa: PLC0415

        base = current_url()[0] if isinstance(current_url(), tuple) else current_url()
        # base 可能自带 ?token= —— 必须 join_url 让 api 落在 query 之前
        #
        req = urllib.request.Request(join_url(base, api),
                                     headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) # 绕代理（heal 同款）
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e: # noqa: BLE001
        if err_box is not None:
            err_box["err"] = str(e)
        return None


def _selftest() -> list[tuple[str, bool, str]]:
    """模块自检：nest 组装 / 点路径读 / 阻断键 / 全量写校验（不真写盘）。"""
    import sys # noqa: PLC0415

    sys.path.insert(0, str(ROOT)) # agent.config 需要项目根在 path

    out: list[tuple[str, bool, str]] = []

    def ck(name: str, cond: bool, extra: str = "") -> None:
        out.append((name, bool(cond), extra))

    n = _nest({"ui.whale_cursor": True, "ui.wave_fx.scale": 17, "bot.name": "x"})
    ck("config_io: 嵌套组装", n == {"ui": {"whale_cursor": True, "wave_fx": {"scale": 17}}, "bot": {"name": "x"}}, str(n))

    v = read_path("ui.whale_cursor")
    ck("config_io: 点路径读真配置", isinstance(v, bool), repr(v))
    ck("config_io: 缺键回默认", read_path("no.such.key", "D") == "D")

    ok, note = write_patch({"wechat.data_dir": "C:/x"})
    ck("config_io: 阻断键不代持且不写盘", not ok and "网页控制台" in note, note)
    ok, note = write_patch({})
    ck("config_io: 空 patch 拒绝", not ok, note)

    ok, note = write_full("{bad json")
    ck("config_io: 全量写挡坏 JSON", not ok and "未写入" in note, note)
    ok, note = write_full("[1,2]")
    ck("config_io: 全量写挡非对象顶层", not ok, note)
    return out


if __name__ == "__main__":
    import sys

    fails = 0
    for name, ok, extra in _selftest():
        print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
        fails += 0 if ok else 1
    raise SystemExit(1 if fails else 0)
