# -*- coding: utf-8 -*-
"""余额的**显示伪装**（2026-09-16 用户要求）。

用户原话：「还有没有一键隐藏剩余金额功能或者一键修改剩余金额功能，你在界面那个位置放这个功能，
可以在界面显示上把金额改掉，但是实际上还是那么多」

⇒ 这是**纯显示层**的一件事，三条硬口径：
  ① **只改返回给界面的数字**：真实余额照旧查询、照旧记账，任何路径都不写回真实值；
  ② **伪装值必须可被认出来**：加 `display_masked: True`（前端与排障都别把它当真实余额）；
  ③ 认不出的模式一律**退回 real**（显示层出错时宁可照实显示，也不许把数字弄丢）。

判据：`scripts/balance_view_selftest.py`。
"""
MODE_REAL = "real"
MODE_HIDE = "hide"
MODE_FAKE = "fake"
MODES = (MODE_REAL, MODE_HIDE, MODE_FAKE)

#: 会被替换掉的余额字段（DeepSeek 的返回里主要是这两个）
MONEY_KEYS = ("total_balance", "topped_up_balance", "granted_balance", "balance")


def normalize_mode(mode) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODES else MODE_REAL


def mask(balance: dict, mode: str = MODE_REAL, fake: str = "") -> dict:
    """按 `mode` 处理查询结果；**返回新 dict，不改传进来的那个**。

    `real` ⇒ 原样；`hide` ⇒ 只留一句"已隐藏"（附带 hidden 标记）；`fake` ⇒ 把金额字段换成 `fake`。
    `fake` 模式但没填数字 ⇒ 退回 `real`（宁可照实显示，也不许显示空白）。
    """
    out = dict(balance or {})
    m = normalize_mode(mode)
    if m == MODE_HIDE:
        return {"hidden": True, "display_masked": True,
                "error": "余额显示已隐藏（这是控制台里的显示设置，不是查询失败）"}
    if m == MODE_FAKE:
        f = str(fake or "").strip()
        if not f:
            return out
        for k in MONEY_KEYS:
            if k in out:
                out[k] = f
        out["display_masked"] = True
        out["display_note"] = "界面上的余额是**你自己设的显示值**，真实余额不受影响"
        return out
    return out
