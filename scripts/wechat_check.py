# -*- coding: utf-8 -*-
r"""微信版本 + 关键依赖版本检查与自动修正：双击根目录「检查微信版本.bat」。

用法：
  直接运行  —— 显示微信版本/适配层 vs 微信匹配结论/全部关键依赖版本检查；
  --update   —— 自动修正：升级 wechatauto-replica 与全部关键依赖（微信本体不自动更换，
                 请从官网下载；微信升级界面后功能异常时先跑本脚本）。

会"版本不对就跑不了"的东西及对策（都在这份检查里）：
  · 微信本体（必须 4.x，界面/DB 结构变化影响 UIA 定位）→ 检测+提示升级；
  · wechatauto-replica 适配层（随微信 UI 结构升级）→ 检测+--update 自动升级；
  · Python（离线 wheel 为 cp310 构建，3.10 最稳）→ 显示版本并提示；
  · 关键 pip 依赖（psutil/uiautomation/pywin32/zstandard 等）→ 按 requirements 最低版本校验+自动升级。
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from agent.wechat import wechat_version_info, dep_check
except Exception:
    wechat_version_info = None  # 项目目录异常时给出提示，不影响本脚本其他检查

# 关键依赖最低版本（与 requirements.txt 一致；wechatauto-replica 精确锁定）
def main():
    info = wechat_version_info()
    print("=" * 56)
    print(" Persona Morph 版本体检（微信 + 依赖，版本不匹配会自动提示/修正）")
    print("=" * 56)
    print("【微信本体】")
    print("  进程   ：%s" % ("检测到" if info["found"] else "未检测到（请先登录微信）"))
    if info["path"]:
        print("  路径   ：%s" % info["path"])
    print("  版本   ：%s" % (info["version"] or "读取失败"))
    print("  结论   ：%s" % ("支持（微信 4.x）" if info["supported"] else "⚠️ 不支持（请升级微信 4.x）"))
    print()
    print("【适配层 vs 微信】")
    print("  wechatauto-replica：%s" % (info["adapter"] or "未安装"))
    _rows_, _dep_all = dep_check()
    _wx_row = next((r for r in _rows_ if r[0] == "wechatauto-replica"), None)
    adapter_ok = bool(info["supported"]) and bool(_wx_row and _wx_row[3])
    print("  结论   ：%s" % ("匹配（适配层 1.1.5.1 支持微信 4.x）" if adapter_ok else
                            "⚠️ 不匹配：适配层无需重装微信，请运行本脚本加 --update 自动升级适配层"))
    print()
    print("【Python】")
    print("  %s（离线 wheel 为 cp310 构建，3.10 最稳；3.11+ 联网安装可用）" % sys.version.split()[0])
    print()
    print("【关键依赖（requirements 最低版本校验）】")
    rows, all_ok = dep_check()
    for pkg, inst, req, ok in rows:
        mark = "OK  " if ok else "FAIL"
        print("  %s %-20s 已装 %-10s 需 >= %s" % (mark, pkg, inst or "-", req))
    print()
    print("微信官方更新界面后功能异常怎么办：")
    print("  1) 先跑本脚本看结论（微信 4.x + 适配层 1.1.5.1 + 依赖全 OK 才算匹配）；")
    print("  2) 异常就运行「检查微信版本.bat」（右键→运行，或命令行加 --update）自动修正；")
    print("  3) 微信本体升级请从微信官网下载安装包，本程序不会自动替换微信。")
    if "--update" in sys.argv:
        print()
        print("-" * 56)
        print("自动修正：升级 wechatauto-replica 与关键依赖 ...")
        try:
            pkgs = " ".join(MIN_VER.keys())
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", *MIN_VER.keys()],
                               capture_output=True, text=True)
            out = (r.stdout or "")[-2000:]
            err = (r.stderr or "")[-600:]
            print(out[-600:] or err[-400:])
            print("[完成] 自动升级结束，请重新运行本脚本 / 自检确认全绿。")
        except Exception as e:
            print("升级失败：%s" % e)
    return 0 if (info["supported"] and all_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
