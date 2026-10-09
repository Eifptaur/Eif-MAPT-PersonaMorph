# -*- coding: utf-8 -*-
"""把「含依赖包」封成**一个安装程序 exe**（群相）。

产物：`群相-Setup-<版本>.exe` —— 双击就装，**全部依赖在该 exe 内部**、运行时零联网。

三步：
  ① 拿到「含依赖包」zip（`scripts/pack_online.py --slim` 的产物，含 `offline/` 全套）；
  ② 解压到 `installer/build/payload/`（Inno 的 `[Files]` 读这个目录）；
  ③ 调 `iscc` 编译 `installer/群相.iss` → setup.exe。

⛔ **iscc 是外部工具**（Inno Setup 6，开发机装一次，最终用户机器不需要）。
   找不到时会打印安装指引并以退出码 2 结束 —— 不假装成功、不静默降级。

用法：
  py -3 scripts/make_installer.py                      # 全自动：没有 zip 就先打包
  py -3 scripts/make_installer.py --zip <路径>          # 指定已有 zip
  py -3 scripts/make_installer.py --skip-pack          # 没 zip 就直接报错，不自动打包
  py -3 scripts/make_installer.py --version 2026.10.09 # 指定版本号（默认取日期）
"""
import argparse
import datetime
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INSTALLER_DIR = os.path.join(ROOT, "installer")
BUILD_DIR = os.path.join(INSTALLER_DIR, "build")
PAYLOAD_DIR = os.path.join(BUILD_DIR, "payload")
ISS = os.path.join(INSTALLER_DIR, "群相.iss")
LANG_FILE = os.path.join(INSTALLER_DIR, "ChineseSimplified.isl")
#: 出包目录＝仓库上一级（与 `pack_online.py` 的 OUT_DIR 同一处，产物放一起好找）
OUT_DIR = os.path.dirname(ROOT)

#: Inno Setup 6 的常见落点（依次找）
_ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
)


def find_iscc() -> str:
    """找 Inno Setup 编译器。找不到返回空串。"""
    p = shutil.which("iscc") or shutil.which("ISCC")
    if p:
        return p
    for c in _ISCC_CANDIDATES:
        if os.path.isfile(c):
            return c
    return ""


def _hint_no_iscc() -> None:
    print("找不到 Inno Setup 编译器（iscc）。", file=sys.stderr)
    print("", file=sys.stderr)
    print("装一次就行（最终用户机器**不需要**装）：", file=sys.stderr)
    print("  winget install --id JRSoftware.InnoSetup -e", file=sys.stderr)
    print("或从 https://jrsoftware.org/isdl.php 下载安装。", file=sys.stderr)
    print("", file=sys.stderr)
    print("装完重跑本脚本即可。", file=sys.stderr)


def find_package_zip() -> str:
    """仓库上一级最新的「含依赖包」zip。没有返回空串。"""
    pat = os.path.join(OUT_DIR, "群相-安装包-*.zip")
    cands = [p for p in glob.glob(pat) if os.path.isfile(p)]
    if not cands:
        return ""
    return max(cands, key=os.path.getmtime)


def make_package() -> str:
    """跑打包器出「含依赖包」（走本地轮子，首装零联网）。返回 zip 路径或空串。"""
    cmd = [sys.executable, os.path.join(HERE, "pack_online.py"), "--slim"]
    print("[1/3] 正在打包（%s）…" % " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("打包失败（退出码 %d）" % r.returncode, file=sys.stderr)
        return ""
    return find_package_zip()


def unzip_payload(zip_path: str) -> bool:
    """把 zip 解到 payload 目录（整目录重建，避免上一轮的残留混进来）。"""
    if os.path.isdir(PAYLOAD_DIR):
        shutil.rmtree(PAYLOAD_DIR, ignore_errors=True)
    os.makedirs(PAYLOAD_DIR, exist_ok=True)
    print("[2/3] 正在解压 %s → %s" % (os.path.basename(zip_path), PAYLOAD_DIR))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(PAYLOAD_DIR)
    except Exception as e:  # noqa: BLE001
        print("解压失败：%s" % e, file=sys.stderr)
        return False
    return True


def _payload_root() -> str:
    """zip 里可能有顶层目录（如 `persona morph/`）⇒ 把真正的根找出来。

    Inno 的 `[Files] Source` 要指向"含 群相.exe 的那一层"，多套一层会装出
    `{app}\\persona morph\\...` ⇒ 快捷方式指不到 exe。
    """
    entries = [e for e in os.listdir(PAYLOAD_DIR)]
    # 只有一项且是目录 ⇒ 认定它是顶层包目录
    if len(entries) == 1 and os.path.isdir(os.path.join(PAYLOAD_DIR, entries[0])):
        return os.path.join(PAYLOAD_DIR, entries[0])
    return PAYLOAD_DIR


def make_exe(payload_root: str, version: str) -> str:
    """调 iscc 编译。返回产物路径或空串。"""
    iscc = find_iscc()
    if not iscc:
        _hint_no_iscc()
        return ""
    if not os.path.isfile(LANG_FILE):
        print("缺中文语言文件：%s" % LANG_FILE, file=sys.stderr)
        print("（Inno 6 官方包不自带中文，仓库里这份必须存在）", file=sys.stderr)
        return ""
    cmd = [iscc, ISS,
           "/DAppVersion=" + version,
           "/DPayloadDir=" + payload_root,
           "/DOutDir=" + BUILD_DIR]
    print("[3/3] 正在编译安装程序（%s）…" % os.path.basename(iscc))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("编译失败（退出码 %d）" % r.returncode, file=sys.stderr)
        return ""
    out = os.path.join(BUILD_DIR, "群相-Setup-%s.exe" % version)
    if not os.path.isfile(out):
        print("编译返回成功但没找到产物：%s" % out, file=sys.stderr)
        return ""
    # 复制到仓库上一级（与 zip 放一起，方便直接发）
    final = os.path.join(OUT_DIR, os.path.basename(out))
    try:
        shutil.copy2(out, final)
    except Exception:  # noqa: BLE001
        final = out
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description="把含依赖包封成一个安装程序 exe")
    ap.add_argument("--zip", default="", help="指定已有的含依赖包 zip")
    ap.add_argument("--skip-pack", action="store_true", help="没有 zip 时不自动打包")
    ap.add_argument("--version", default=datetime.date.today().strftime("%Y.%m.%d"),
                    help="版本号（默认取今天日期）")
    a = ap.parse_args()

    # ⛔ **先查编译器**再打包：没有 iscc 时早退，省得白打一个 281 MB 的包
    #    （打包本身要跑几分钟，白等最伤）。
    if not find_iscc():
        _hint_no_iscc()
        return 2

    zip_path = a.zip or find_package_zip()
    if not zip_path:
        if a.skip_pack:
            print("没有可用的含依赖包 zip，且指定了 --skip-pack。", file=sys.stderr)
            return 2
        zip_path = make_package()
    if not zip_path or not os.path.isfile(zip_path):
        print("拿不到含依赖包 zip。", file=sys.stderr)
        return 2
    print("使用包：%s（%.1f MB）" % (os.path.basename(zip_path),
                                   os.path.getsize(zip_path) / 1048576.0))

    if not unzip_payload(zip_path):
        return 3

    root = _payload_root()
    # 分母守卫：payload 里必须真的有程序与依赖，否则装出来的是空壳
    exe = os.path.join(root, "群相.exe")
    wheels = os.path.join(root, "offline", "wheels")
    if not os.path.isfile(exe):
        print("payload 里没有 群相.exe（%s）⇒ 包不对，停。" % exe, file=sys.stderr)
        return 3
    if not os.path.isdir(wheels):
        print("payload 里没有 offline\\wheels ⇒ 装完会联网取依赖，与口径不符，停。",
              file=sys.stderr)
        return 3

    out = make_exe(root, a.version)
    if not out:
        return 4
    print("")
    print("完成：%s（%.1f MB）" % (out, os.path.getsize(out) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
