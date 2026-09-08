# -*- coding: utf-8 -*-
r"""一键依赖安装（带已装检测与跳过）：运行 py -3 -X utf8 scripts\setup_deps.py
（一键启动会自动调用本脚本）。

行为：
  · 依赖全部就绪且版本正确 → 打印"已满足，跳过安装"，直接建议下一步（运行自检）；
  · 有缺失/版本不符 → 自动识别离线（offline\wheels 存在）或联网模式安装，装完复查；
  · 微信安装包在 offline\wechat\（离线重装用），不随 pip 安装流程处理。
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.wechat import dep_check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    print("=" * 52)
    print(" wx-agent 依赖检查 / 安装")
    print("=" * 52)
    rows, ok = dep_check()
    for pkg, inst, req, good in rows:
        print("  %s %-20s 已装 %-12s 需 >= %s" % (
            "OK  " if good else "MISS", pkg, (inst or "-"), req))
    if ok:
        print("-" * 52)
        print("全部依赖已就绪且版本正确，跳过安装 ✔")
        print("下一步：双击 一键启动.vbs 即可（已装依赖会自动跳过）。")
        return 0

    py_exe = sys.executable or "py"
    wheels = os.path.join(ROOT, "offline", "wheels")
    offline = os.path.exists(os.path.join(wheels, "wechatauto_replica-1.1.5.1-py3-none-any.whl"))
    print("-" * 52)
    print("[%s] 开始安装缺失/需升级的依赖 ..." % ("离线" if offline else "联网"))
    try:
        if offline:
            cmd = [py_exe, "-m", "pip", "install", "--no-index", "--find-links", wheels,
                   "-r", os.path.join(ROOT, "requirements.txt")]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        else:
            # 联网：主源用国内镜像（快/稳），失败再回退官方 PyPI
            req = os.path.join(ROOT, "requirements.txt")
            indexes = ["https://pypi.tuna.tsinghua.edu.cn/simple",
                       "https://mirrors.aliyun.com/pypi/simple/",
                       "https://pypi.org/simple"]
            r = None
            for idx in indexes:
                cmd = [py_exe, "-m", "pip", "install", "-U", "--progress-bar", "on",
                       "-i", idx, "--timeout", "60", "-r", req]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                if r.returncode == 0:
                    break
                print("  镜像 %s 失败，切换下一个源..." % idx)
        out = (r.stdout or "")[-1200:] or (r.stderr or "")[-600:]
        print(out)
        if r.returncode != 0:
            print("[失败] 安装失败，见上方日志。")
            if "Building wheel" in out or "failed" in out.lower():
                print("提示：若失败于 winsdk 等本地编译，多为系统 Python 版本过新（需 3.10~3.12）；")
                print("      删除 logs\\python_path.txt 后重新双击「一键启动.vbs」，会自动改用内置绿色版 3.10。")
            return 1
    except Exception as e:
        print("[失败] 安装异常：%s" % e)
        return 1

    rows2, ok2 = dep_check()
    print("-" * 52)
    print("复查：" + ("全部满足 ✔" if ok2 else "仍有缺失: " + ", ".join(r[0] for r in rows2 if not r[3])))
    print("下一步：双击 一键启动.vbs 即可（已装依赖会自动跳过）。")
    return 0 if ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
