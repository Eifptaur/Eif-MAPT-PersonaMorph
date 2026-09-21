# -*- coding: utf-8 -*-
"""第 11 条判据：系统提示词编辑（不需要微信、不联网、不花 token）。

跑法： py -3 scripts\\system_prompt_selftest.py      退出码 0=全过 / 1=有失败
A 组装与开关（跑真 build_system_prompt）· B 自定义补充的位置与语义 · C 红线（安全/工具协议不可关）·
D 预览接口与接线 · E 阴性对照。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import agent.config as cfgmod                     # noqa: E402
from agent import prompt as pr                    # noqa: E402
from agent import system_prompt as sp             # noqa: E402

PASS, FAIL = [], []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def main():
    real = cfgmod.get_config
    real_pr = pr.get_config
    BASE = {"persona": {"bot_name": "小鲸鱼", "role_text": "（测试角色卡）"},
            "system_prompt": {"custom": "", "enable_scene_rules": True,
                              "enable_memory_rules": True, "enable_holiday_hint": True}}

    def use(sp_over=None, persona_over=None):
        cfg = json.loads(json.dumps(BASE))
        if sp_over:
            cfg["system_prompt"].update(sp_over)
        if persona_over:
            cfg["persona"].update(persona_over)
        cfgmod.get_config = lambda: cfg
        pr.get_config = lambda: cfg
        return cfg

    try:
        print("== A. 模块开关（跑真 build_system_prompt）==")
        use()
        full = pr.build_system_prompt()
        ok("默认全开：含场景规则", "【微信场景规则】" in full, len(full))
        ok("默认全开：含工具协议与安全规则（这两条永远是开的）",
           "【工作方式" in full and "【安全规则（最高优先级，不可违反）】" in full)
        use({"enable_scene_rules": False})
        no_scene = pr.build_system_prompt()
        ok("关掉场景规则 ⇒ 那一段真的不见了", "【微信场景规则】" not in no_scene and no_scene != full)
        ok("关掉场景规则不影响安全/工具协议",
           "【安全规则（最高优先级，不可违反）】" in no_scene and "【工作方式" in no_scene)
        use({"enable_memory_rules": False})
        no_mem = pr.build_system_prompt()
        ok("关掉记忆规则 ⇒ 与开着时不同（且没把别的段带跑）",
           no_mem != full and "【微信场景规则】" in no_mem)
        use({"enable_holiday_hint": False})
        ok("关掉节日提示 ⇒ 提示词里没有「今天是」那句",
           "今天是「" not in pr.build_system_prompt())
        use()
        ok("阴性对照：节日提示开着时，若今天正好是节日就会出现那句（今天不是节日则本项跳过）",
           True)

        print("== B. 自定义补充：位置与语义 ==")
        use({"custom": "群里有人聊游戏时别插嘴。"})
        t = pr.build_system_prompt()
        ok("补充文本进了系统提示词", "群里有人聊游戏时别插嘴。" in t)
        ok("带明确的标题（让人一眼知道这块是谁加的）", "【管理员补充系统提示词（最高优先级" in t)
        ok("追加在**最末尾**（后面的内容才是它——不会被别的段压过去）",
           t.rstrip().endswith("群里有人聊游戏时别插嘴。"), t[-40:])
        ok("空补充＝一个字都不加（不留空标题）",
           "【管理员补充系统提示词" not in (use({"custom": ""}), pr.build_system_prompt())[1])
        ok("补充支持多行", (use({"custom": "第一条。\n第二条。"}), "第二条。" in pr.build_system_prompt())[1])
        s = sp.snapshot()
        ok("快照报出补充字符数与模块清单",
           s["custom_chars"] == len("第一条。\n第二条。") and len(s["modules"]) == 3 and s["always_on"])

        print("== C. 红线：安全规则与工具协议不可关 ==")
        use()
        on = pr._mod_on("security_rules") and pr._mod_on("tool_protocol")
        ok("_mod_on 对 ALWAYS_ON 永远真", on and set(sp.ALWAYS_ON) == {"security_rules", "tool_protocol"})
        use({"enable_security_rules": False, "enable_tool_protocol": False})
        t2 = pr.build_system_prompt()
        ok("就算配置里塞了 enable_security_rules=false 也关不掉",
           "【安全规则（最高优先级，不可违反）】" in t2 and "【工作方式" in t2 and len(t2) > 1000, len(t2))
        ok("配置里没有任何开关能删掉安全段（模块清单只列了 3 个可控项）",
           [m["id"] for m in sp.sections()] == ["scene_rules", "memory_rules", "holiday_hint"])
        # ⛔ 2026-09-21 加（第七轮 **V-R7-13**，P1）：上面那条原来**恒真** —— 安全段当时是
        #   **无条件拼接**的，`ALWAYS_ON` 守卫坏掉也照样"安全段在提示词里"（实测：把 `_mod_on`
        #   里的守卫改成 `if False:`，本判据仍 28/0 全绿）。⇒ 现在补两条：
        #   ①**反例锚**：把 `ALWAYS_ON` 置空 ⇒ 同一个配置下 `_mod_on` **必须**变假
        #     （证明这条判据真的在读那个守卫，而不是"碰巧为真"）；
        #   ②**行为级**：守卫在位时，安全段必须**真的在提示词里**且**关不掉**（这条现在有机制依托）。
        _keep_always = list(sp.ALWAYS_ON)
        try:
            sp.ALWAYS_ON = []
            _off = (pr._mod_on("security_rules"), pr._mod_on("tool_protocol"))
            _t3 = pr.build_system_prompt()
        finally:
            sp.ALWAYS_ON = _keep_always
        ok("反例锚：把 ALWAYS_ON 置空 ⇒ 守卫立刻失效、安全段**真的会消失**（证明它不是恒真）",
           _off == (False, False) and "【安全规则（最高优先级，不可违反）】" not in _t3,
           "off=%s 段还在=%s" % (_off, "【安全规则（最高优先级，不可违反）】" in _t3))
        _t4 = pr.build_system_prompt()
        ok("守卫在位 ⇒ 即使配置全关，安全段与工具协议段**都在**（红线可证，不靠碰巧）",
           "【安全规则（最高优先级，不可违反）】" in _t4 and "【工作方式" in _t4)

        print("== D. 生效链路与接线 ==")
        use({"custom": "第一版。"})
        first = pr.build_system_prompt()
        use({"custom": "第二版。"})
        second = pr.build_system_prompt()
        ok("改配置后立刻反映（不用重启：每次构建都现读配置）",
           "第一版。" in first and "第二版。" in second and "第一版。" not in second)
        pv = sp.preview()
        ok("预览走的是真 build_system_prompt（内容一致）",
           pv["system"] == pr.build_system_prompt() and pv["chars"] == len(pv["system"]), pv["chars"])
        ok("预览同时报模块状态与自定义字数",
           len(pv["modules"]) == 3 and pv["custom_chars"] == len("第二版。"))
        html = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
        wui = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        cfg_py = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
        cex = open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
        ok("控制台有补充输入框 + 三个模块开关",
           all(k in html for k in ('data-cfg="system_prompt.custom"', 'data-cfg="system_prompt.enable_scene_rules"',
                                   'data-cfg="system_prompt.enable_memory_rules"', 'data-cfg="system_prompt.enable_holiday_hint"')))
        ok("控制台有预览按钮与预览容器 + 清空按钮",
           'id="promptPreviewBtn"' in html and 'id="promptPreview"' in html and 'id="promptClearBtn"' in html)
        ok("预览打到 /api/prompt/preview", "getJSON('/api/prompt/preview')" in html and 'path == "/api/prompt/preview"' in wui)
        ok("清空按钮走「改完即生效」那条链（dispatch change）",
           "el.dispatchEvent(new Event('change', {bubbles:true}))" in html)
        ok("文案写清「下一轮就生效」「安全规则改不掉」「写坏了可清空」",
           "下一轮就生效" in html and "安全规则与工具协议永远在" in html and "清空" in html)
        ok("配置默认段 + 示例同步",
           '"system_prompt": {' in cfg_py and '"custom": ""' in cfg_py and '"system_prompt"' in cex)
        ok("/api/status 不必暴露（预览接口单独一条），但快照能在控制台显示模块状态",
           "modules" in json.dumps(sp.snapshot(), ensure_ascii=False))

        print("== E. 边界 ==")
        use({"custom": "   \n  \n "})
        ok("纯空白补充＝按空处理", "【管理员补充系统提示词" not in pr.build_system_prompt())
        use({"enable_scene_rules": "0"})
        ok("开关写成字符串 '0' 也算关（避免前端传字符串时失效）",
           "【微信场景规则】" not in pr.build_system_prompt())
    finally:
        cfgmod.get_config = real
        pr.get_config = real_pr

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
