#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能挂载 hook 的判据（可复跑）—— 覆盖"配置正确 + 输出正确 + 不会锁死"三组。

背景（为什么需要它）：
  会话开始/压缩后"忘了加载技能"这件事，靠"我记得"是不可靠的，必须由 hook 机制兜底。
  而 hook 一旦失效，**是静默失效**（不报错、不提示）⇒ 必须有判据才能发现。

  三类历史失效（本判据即为防它们）：
    · 输出字段用错：官方定义 `systemMessage` 只给用户看、**不注入 Agent**
      ⇒ 用 `systemMessage` 做注入 = Agent 永远收不到。
    · 事件挂错：官方 7 事件里没有 `PostCompact` ⇒ 挂了也永不触发。
    · 事件语义用错：`PreCompact` 的 stdout 只能作"压缩指导"，不是"开工提醒"。

用法： py -3 scripts/hook_mount_selftest.py
"""
import io
import json
import os
import subprocess
import sys
import logging

log = logging.getLogger("persona-morph")

PY = sys.executable
HOOKS_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "hooks")
SCRIPT = os.path.join(HOOKS_DIR, "session_reminder.py")
SETTINGS = os.path.join(os.path.expanduser("~"), ".workbuddy", "settings.json")

#: 引擎实际支持的事件枚举（取自引擎代码 `codebuddy-lite-wb.mjs` 的事件常量表；
#: 它比 CLI 文档里写的"7 事件"更全，例如含 PostCompact / ConfigChange / InstructionsLoaded）。
#: 用于校验"不许乱挂引擎不认识的事件名"。
OFFICIAL_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification",
    "UserPromptSubmit", "Stop", "StopFailure", "SubagentStop", "SubagentStart",
    "PreCompact", "PostCompact", "SessionStart", "SessionEnd",
    "WorktreeCreate", "WorktreeRemove", "ConfigChange", "InstructionsLoaded",
}

_pass = 0
_fail = 0


def ck(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  PASS  %s" % name)
    else:
        _fail += 1
        print("  FAIL  %s   %s" % (name, detail))


def run(event, extra=None):
    """喂一个 hook stdin，返回 (退出码, stdout 文本)。"""
    payload = {"hook_event_name": event, "session_id": "t", "cwd": os.getcwd(),
               "transcript_path": ""}
    if extra:
        payload.update(extra)
    p = subprocess.run([PY, SCRIPT], input=json.dumps(payload).encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=(0x08000000 if os.name == "nt" else 0))
    return p.returncode, p.stdout.decode("utf-8", "replace")


def parsed(event, extra=None):
    rc, out = run(event, extra)
    try:
        return rc, json.loads(out), out
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return rc, None, out


def main():
    print("=" * 74)
    print("Hook 挂载判据：配置 / 输出 / 安全性")
    print("=" * 74)
    print("脚本：%s" % SCRIPT)

    # ── A 组：脚本存在且语法可解析 ────────────────────────────────────────
    print("\n── A. 脚本本体 ──")
    ck("A1 脚本存在", os.path.isfile(SCRIPT), SCRIPT)
    try:
        import ast
        ast.parse(io.open(SCRIPT, encoding="utf-8").read())
        ck("A2 语法可解析", True)
    except Exception as e:
        ck("A2 语法可解析", False, str(e)[:80])

    # ── B 组：配置正确 ───────────────────────────────────────────────────
    print("\n── B. settings.json 配置 ──")
    try:
        cfg = json.load(io.open(SETTINGS, encoding="utf-8"))
        hooks = cfg.get("hooks") or {}
        ck("B1 有 hooks 段", bool(hooks))
        ck("B2 UserPromptSubmit 已挂（回合开始注入）", "UserPromptSubmit" in hooks,
           "缺它 ⇒ 每个新回合都收不到注入")
        ck("B3 SessionStart 已挂（新会话注入）", "SessionStart" in hooks)
        ck("B4 PreCompact 已挂（压缩指导）", "PreCompact" in hooks)
        # ★ PostCompact：CLI 文档的"7 事件"里没有它，但**引擎枚举里有**
        #   （POST_COMPACT，见 codebuddy-lite-wb.mjs 的事件枚举）⇒ 挂上作压缩后兜底。
        ck("B5 PostCompact 已挂（引擎枚举支持，作压缩后兜底）", "PostCompact" in hooks)
        bad = set(hooks.keys()) - OFFICIAL_EVENTS
        ck("B6 挂载事件都在引擎事件枚举内", not bad, "越界事件：%s" % sorted(bad))
        # ★★ 关键防回退（实测踩过）：一旦给某事件写了 matcher，就只匹配那一个取值。
        #    SessionStart 的匹配值是 source（startup/resume/compact）⇒ 写 "startup"
        #    会让 compact（压缩后）与 resume 全部被跳过；PreCompact 同理（trigger=manual/auto）。
        #    ⇒ 配置里**一律不许出现 matcher**。
        _mats = []
        for ev, groups in hooks.items():
            for g in groups:
                if g.get("matcher") not in (None, "", "*"):
                    _mats.append("%s=%r" % (ev, g.get("matcher")))
        ck("B7 所有事件都未写限定性 matcher（否则会跳过 compact/resume）",
           not _mats, "违规：%s" % _mats)
        # 每条都指向本脚本
        targets = []
        for ev, groups in hooks.items():
            for g in groups:
                for h in (g.get("hooks") or []):
                    targets.append((ev, "session_reminder.py" in (h.get("command") or "")))
        ck("B8 每条挂载都指向 session_reminder.py",
           all(t[1] for t in targets) and bool(targets),
           str([t for t in targets if not t[1]]))
    except Exception as e:
        ck("B* 读取 settings.json", False, str(e)[:80])

    # ── C 组：输出结构（关键 —— 必须走 additionalContext）─────────────────
    print("\n── C. 输出结构（注入通道） ──")
    for ev, want_kind in (("SessionStart", "开工提醒"),
                          ("UserPromptSubmit", "开工提醒"),
                          ("PreCompact", "压缩指导")):
        rc, d, raw = parsed(ev)
        if d is None:
            ck("C[%s] 输出是合法 JSON" % ev, False, raw[:60])
            continue
        hs = d.get("hookSpecificOutput") or {}
        ctx = hs.get("additionalContext") or ""
        ck("C[%s] hookEventName 正确" % ev, hs.get("hookEventName") == ev,
           "实际 %s" % hs.get("hookEventName"))
        ck("C[%s] additionalContext 非空（这是唯一会注入 Agent 的字段）" % ev, len(ctx) > 50,
           "长度 %d" % len(ctx))
        # 语义分派：两种文案必须不同，且各自含关键词
        if want_kind == "开工提醒":
            ck("C[%s] 是开工提醒文案" % ev, "开工提醒" in ctx and "加载" in ctx)
        else:
            ck("C[%s] 是压缩指导文案（不是开工提醒）" % ev,
               "压缩指导" in ctx and "保留" in ctx and "开工提醒" not in ctx)
        # 绝不能把"本回合结束才产出的东西"写成前置条件（自指死锁）
        ck("C[%s] 无「前置条件/需证明」类死锁措辞" % ev,
           "前置条件" not in ctx.replace("不构成任何前置条件", "")
           and "必须先证明" not in ctx)

    # ── C2 组：事件识别必须"双路" ───────────────────────────────────────────
    # 背景：实测出现过"hook 已挂载且确已触发，但引擎传入的 stdin 里按
    # `hook_event_name` 取不到事件名" ⇒ 旧脚本落到兜底分支，注入丢失（只回了
    # 一个裸的 continue:true）。因此本组断言**不依赖 hook_event_name** 也能识别。
    print("\n── C2. 事件识别（不依赖 hook_event_name，按载荷形状推断） ──")
    _shape = (
        ("只有 prompt（推断＝提交输入）", '{"prompt":"hi"}', "UserPromptSubmit", "开工提醒"),
        ("只有 trigger（推断＝压缩前）", '{"trigger":"auto"}', "PreCompact", "压缩指导"),
        ("只有 source（推断＝会话启动）", '{"source":"startup"}', "SessionStart", "开工提醒"),
        ("全空对象（无任何线索）", "{}", "UserPromptSubmit", "开工提醒"),
        ("空 stdin（无任何线索）", "", "UserPromptSubmit", "开工提醒"),
    )
    for label, payload, want_ev, want_kind in _shape:
        try:
            p = subprocess.run([PY, SCRIPT], input=payload.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=(0x08000000 if os.name == "nt" else 0))
            out = p.stdout.decode("utf-8", "replace")
        except Exception as e:
            ck("C2[%s]" % label, False, str(e)[:60])
            continue
        try:
            d = json.loads(out)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            ck("C2[%s] 输出合法 JSON" % label, False, out[:60])
            continue
        hs = d.get("hookSpecificOutput") or {}
        ctx = hs.get("additionalContext") or ""
        ck("C2[%s] 推断出 %s" % (label, want_ev), hs.get("hookEventName") == want_ev,
           "实际 %s" % hs.get("hookEventName"))
        ck("C2[%s] 注入 %s 文案（非空）" % (label, want_kind),
           len(ctx) > 50 and (want_kind in ctx), "len=%d" % len(ctx))

    # ── D 组：安全性（机制上不可能锁死会话）──────────────────────────────
    print("\n── D. 安全性（不可能锁死） ──")
    for label, payload in (("空 stdin", ""),
                           ("非法 JSON", "not json{{{"),
                           ("未知事件", '{"hook_event_name":"NoSuchEvent"}'),
                           ("残留的 PostCompact", '{"hook_event_name":"PostCompact"}')):
        try:
            p = subprocess.run([PY, SCRIPT], input=payload.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=(0x08000000 if os.name == "nt" else 0))
            rc, out = p.returncode, p.stdout.decode("utf-8", "replace")
        except Exception as e:
            rc, out = -1, str(e)
        ck("D[%s] 退出码 = 0" % label, rc == 0, "实际 %s" % rc)
        ck("D[%s] 无 permissionDecision / continue=false" % label,
           "permissionDecision" not in out and '"continue": false' not in out
           and '"continue":false' not in out)
        if out.strip():
            try:
                json.loads(out)
                ck("D[%s] 有输出时仍是合法 JSON" % label, True)
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                ck("D[%s] 有输出时仍是合法 JSON" % label, False, out[:60])

    print("\n" + "=" * 74)
    print("结果：%d PASS / %d FAIL" % (_pass, _fail))
    print("=" * 74)
    print("⚠️ 引擎在**启动时**快照 hook 配置 ⇒ 改完必须**完全退出并重新打开**应用；")
    print("   仅关闭窗口不够（后台守护进程仍在，配置不会重载）。")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
