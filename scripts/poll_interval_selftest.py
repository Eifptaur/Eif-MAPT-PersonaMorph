# -*- coding: utf-8 -*-
"""poll_interval_selftest.py —— 消息轮询间隔判据（P0-8）

目标（诊断书 P0-8）：**消息检测 ≤1 秒**。
诊断书实测（当时 `poll_interval=5`）：整条链最靠前那段耗时就是"消息检测 ≤5 秒"，
而它**纯等**（`time.sleep`）⇒ 降下来直接把检测压到 ≤1 秒。

⛔ **反向锚**：
  · 断言 `config.json` 的 `wechat.poll_interval` **在 1..3 的合理区间内**
    （<1 无意义，因为 `scripts/persona_morph.py` 里有 `max(1.0, …)` 下限；
      >3 则不满足"检测 ≤1 秒"的目标）；
  · 断言 `scripts/persona_morph.py` **确实读了** `wechat.poll_interval`
    —— 若有人把配置读法删掉，本段必红（否则"改配置"是个死配置，改了也不生效）。

用法：./runtime/python/python.exe -u scripts/poll_interval_selftest.py
"""
import json
import logging
import os
import re
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILS = []
OKS = []


def chk(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name, ("  ← " + extra) if (extra and not cond) else ""))


def main():
    cfg_path = os.path.join(_ROOT, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    v = (cfg.get("wechat") or {}).get("poll_interval")

    print("== A. 运行配置 ==")
    chk("config.json 里有 wechat.poll_interval", v is not None, "缺项 ⇒ 会吃默认值")
    if v is not None:
        try:
            v = float(v)
        except Exception:
            v = None
    eff = max(1.0, float(v)) if v is not None else None
    chk("生效值满足 ≤1 秒（P0-8 目标）", eff is not None and eff <= 1.0, "实际 %s" % eff)
    chk("生效值不小于下限 1.0（再小无意义）", eff is not None and eff >= 1.0, "实际 %s" % eff)

    print("== B. 默认值（新用户/无配置场景） ==")
    src_cfg = open(os.path.join(_ROOT, "agent", "config.py"), encoding="utf-8").read()
    m = re.search(r'"poll_interval":\s*([0-9.]+)', src_cfg)
    chk("config.py 有默认值", bool(m))
    if m:
        dv = float(m.group(1))
        chk("默认值也满足 ≤1 秒", dv <= 1.0, "默认 %s" % dv)

    print("== C. 反向锚：配置确实被读（不是死配置） ==")
    src_main = open(os.path.join(_ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    chk("persona_morph.py 读了 wechat.poll_interval",
        'get("wechat", {}).get("poll_interval")' in src_main, "读法被删 ⇒ 改配置不生效")
    chk("该值确实用于 sleep（轮询节奏）",
        "time.sleep(poll_interval)" in src_main, "没用于 sleep ⇒ 间隔改了也没用")
    chk("存在下限保护 max(1.0, …)", "max(1.0," in src_main, "缺下限 ⇒ 可能被配成 0 导致空转")

    print()
    print("== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
    for f in FAILS:
        print("   FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
