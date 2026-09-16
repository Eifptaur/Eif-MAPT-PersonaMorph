# -*- coding: utf-8 -*-
r"""一键依赖安装（带已装检测与跳过）：运行 py -3 -X utf8 scripts\setup_deps.py
（一键启动会自动调用本脚本；两个 .cmd 入口也会调）。

行为：
  · 依赖全部就绪且版本正确 → 打印"已满足，跳过安装"，直接建议下一步；
  · 有缺失/版本不符 → 离线（`offline\wheels` 存在）或联网（清华→阿里→官方）安装，
    **pip 的输出实时转发**（2026-09-16 改：以前整段缓存到结束才打印，界面上十几分钟一片空白，
    用户以为卡死）；
  · **判定口径（2026-09-16 改）**：必需项（`dep_check("key")` 那 14 项）齐 ⇒ **通过**；
    **可选大件**（numpy / opencv / PyAutoGUI 系列 / edge-tts / pypinyin，合计约 100MB）缺了
    只**警告**、不阻断启动 —— 慢网或镜像抖动时不许因此让用户"一键启动失败"；
  · **不设总时长上限**，只在"连续 N 秒没有任何输出"时才判卡死（IDLE_LIMIT）；
  · 微信安装包在 `offline\wechat\`（离线重装用），不随 pip 安装流程处理。
"""
import os
import queue
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.wechat import dep_check          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 连续这么久**没有任何输出**才算卡死（**没有"总时长上限"**：慢网装十几分钟是正常的）
IDLE_LIMIT = 600
MIRRORS = ["https://pypi.tuna.tsinghua.edu.cn/simple",
           "https://mirrors.aliyun.com/pypi/simple/",
           "https://pypi.org/simple"]


def _run_stream(cmd, idle_limit=IDLE_LIMIT):
    """跑命令并**实时**把输出转发出来。返回 (rc, 尾部文本)。

    为什么用读者线程 + 队列（2026-09-16 判据抓出来的坑）：直接 `p.stdout.readline()` 会**阻塞**，
    主循环根本没机会判"空闲超时"（实测：子进程睡 30 秒，判据就真的等满 30 秒）。
    """
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        print("[失败] 命令起不来：%s" % e)
        return 1, str(e)
    q = queue.Queue()

    def _pump():
        try:
            for line in iter(p.stdout.readline, b""):
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    threading.Thread(target=_pump, daemon=True).start()
    tail = []
    last = time.time()
    while True:
        try:
            line = q.get(timeout=0.5)
        except queue.Empty:
            if p.poll() is not None:
                break
            if time.time() - last > idle_limit:
                try:
                    p.kill()
                except Exception:
                    pass
                print("\n[超时] 连续 %d 秒没有任何输出，已终止（再点一次「一键启动」会接着装，"
                      "已下完的不会重下）。" % idle_limit)
                return 1, "".join(tail)
            continue
        if line is None:
            break
        last = time.time()
        txt = line.decode("utf-8", "replace")
        tail.append(txt)
        if len(tail) > 400:
            tail = tail[-200:]
        try:
            sys.stdout.write(txt)
            sys.stdout.flush()
        except Exception:
            pass
    try:
        p.wait(timeout=10)
    except Exception:
        pass
    try:
        p.stdout.close()
    except Exception:
        pass
    return int(p.returncode or 0), "".join(tail)


def verdict(rows_key, rows_all):
    """安装之后算不算"过"：**必需项齐就算过**，可选缺只警告。返回 (rc, 说明)。"""
    miss_key = [r[0] for r in (rows_key or []) if not r[3]]
    miss_all = [r[0] for r in (rows_all or []) if not r[3]]
    if miss_key:
        return 1, ("必需依赖没装齐：%s ⇒ 再点一次「一键启动」会接着装（已下完的不会重下）；"
                   "反复失败多半是网络/镜像问题" % "、".join(miss_key[:6]))
    if miss_all:
        return 0, ("可选依赖还缺 %d 项（%s）——**不影响启动**，下次再点「一键启动」会接着装"
                   % (len(miss_all), "、".join(miss_all[:5])))
    return 0, "全部 %d 项就绪" % len(rows_all)


def main():
    print("=" * 52)
    print(" Persona Morph 依赖检查 / 安装")
    print("=" * 52)
    rows, ok = dep_check("all")
    for pkg, inst, req, good in rows:
        print("  %s %-20s 已装 %-12s 需 %s" % (
            "OK  " if good else "MISS", pkg, (inst or "-"), req))
    if ok:
        print("-" * 52)
        print("全部依赖已就绪且版本正确，跳过安装 ✔")
        print("下一步：双击 一键启动.vbs 即可（已装依赖会自动跳过）。")
        return 0

    py_exe = sys.executable or "py"
    wheels = os.path.join(ROOT, "offline", "wheels")
    offline = any(f.startswith("wechatauto_replica-") and f.endswith(".whl")
                  for f in os.listdir(wheels)) if os.path.isdir(wheels) else False
    print("-" * 52)
    print("首次安装需要下载约 100~150MB（opencv-python 约 42MB、imageio-ffmpeg 约 30MB 占大头）。")
    print("网速 1MB/s 上下时**十几分钟是正常的**，中途请不要关窗；装过的包不会重下。")
    print("[%s] 开始安装缺失/需升级的依赖 ..." % ("离线" if offline else "联网"))
    req = os.path.join(ROOT, "requirements.txt")
    last = (1, "")
    if offline:
        last = _run_stream([py_exe, "-m", "pip", "install", "--no-index",
                            "--find-links", wheels, "-r", req])
    else:
        for idx in MIRRORS:
            rc, tail = _run_stream([py_exe, "-m", "pip", "install", "-U",
                                    "--progress-bar", "off", "--timeout", "60",
                                    "--retries", "5", "-i", idx, "-r", req])
            last = (rc, tail)
            if rc == 0:
                break
            print("  镜像 %s 失败（exit %s），切换下一个源..." % (idx, rc))
            tail20 = "\n".join(tail.splitlines()[-20:])
            if tail20.strip():
                print("    这个源失败原因末尾：")
                print(tail20)
        else:
            print("  三个源都失败了 —— 再试一次第一个源（已下完的包不会重下）…")
            last = _run_stream([py_exe, "-m", "pip", "install", "-U",
                                "--progress-bar", "off", "--timeout", "60",
                                "--retries", "5", "-i", MIRRORS[0], "-r", req])

    rows2_all, _ok_all = dep_check("all")
    rows2_key, _ok_key = dep_check("key")
    rc2, msg = verdict(rows2_key, rows2_all)
    print("-" * 52)
    print("安装命令 exit=%s。%s" % (int(last[0] or 0), msg))
    if rc2 != 0:
        tail20 = "\n".join((last[1] or "").splitlines()[-20:])
        if tail20.strip():
            print("—— 末尾 20 行（失败原因通常就在这里）——")
            print(tail20)
        print("提示：若上面出现 cmake / ninja / Building wheel / pythoncore-3.1x，多半是**没在用本包自带的")
        print("      运行时**（系统 Python 太新：本项目依赖只提供到 cp312 的预编译轮子）。正确做法：")
        print("      ① 把包放在**纯英文路径**下，或 ② 删掉 logs\\python_path.txt 后重新双击「一键检验」，")
        print("      让它把内置的绿色版 Python 3.10 装回 runtime\\ 再用。")
        return 1
    print("下一步：双击 一键启动.vbs 即可（已装依赖会自动跳过）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
