# -*- coding: utf-8 -*-
"""在线包打包器（群相）—— 只打"版本库里跟踪的代码"，并做**个人信息/开发资料扫描闸门**。

用法：
  py -3 scripts/pack_online.py            # 打包 + 扫描（命中即拒绝出包 exit 3）
  py -3 scripts/pack_online.py --check    # 只扫描仓库（不出包）

口径（用户 2026-09-13 定）：
  ① 只有"在线包"：不含 `offline/`（运行时 + wheel），第一次运行由 `scripts/setup_python.ps1` 联网准备；
  ② 包里**不许有他的个人信息与开发资料**——家目录路径 / 密钥 / token / 聊天数据 /
     开发文档（AGENTS.md、docs/ 下的任务清单与 changelog 归档）；
  ③ 打包源＝`git ls-files`（未跟踪的草稿、报告、日志一律进不来）；
  ④ 扫描命中一律拒绝出包（要放行必须显式加进 ALLOW 并写明理由）。

⚠️ 2026-09-19 改（小鲸鱼挂件）：`whale-widget/` 原先**整目录排除**，理由是"另一个项目的素材"。
  但控制台右下角那个挂件**就是靠这个目录跑的**（`webui._whale_js_injected()` 读
  `whale-widget/client/widget.js`，`image.png`/`rua.gif`/音效走 `assets/`）⇒ 整目录排除等于
  **装上以后挂件是死的**（脚本 0 字节、图片取不到，界面上什么都没有 —— 只有侧栏那个徽章还在）。
  现在按"只发程序真正要用的那几样"收窄排除，并且**已获原作者同意**随包分发（上游 `PROVENANCE.md`：
  代码 MIT，`assets/**` 不在 MIT 范围内、原文是"不授予再许可"；同意记录见 `whale-widget/PORT-NOTES.md`）。
"""
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.dirname(ROOT)                      # 仓库的上一级目录（例：C:\Users\<你>\Desktop\WX-chatbot）
PKG_PREFIX = "群相-在线包-"
# 解压出来的**顶层文件夹名**（用户 2026-09-15 定：他要用户在压缩包里看到的就是这个名字）
# ⚠️ 只影响压缩包的目录布局，不动任何程序逻辑：程序内部一律用"自己所在目录"定位（ROOT=文件位置），
#    仓库里也没有别处硬编码过仓库目录名（打包器自己那条注释除外）。
ZIP_TOP = "persona morph"

# 不进包（相对仓库根的 posix 路径前缀 / 精确名）
EXCLUDE = (
    "AGENTS.md",          # 开发守则：含红线自述与既有口径：，不随包发
    "docs/",              # 开发资料：任务清单 / changelog 归档
    "whale-widget/upstream-0.3.9/",       # 上游原文留档（README/PROVENANCE/package.json）＝开发资料，不随包发
    "whale-widget/assets/DSniang02.png",  # 备用整图：我们的路由用不到（image.png 走 DSniang1.png）
    "whale-widget/assets/DSH2.png",       # 上游 README 展示图：程序不用（1.1MB，别白占包体积）
    # ⛔ 2026-09-21 加（第六轮 **V-R6-23**）：这四件**只被上游留档文档引用**，`agent/whale.py` 已把同族
    #   资源声明为不支持 ⇒ 死重约 **3.2MB ≈ 包体 22%**（挂件判据只查"仓库里在不在"，不查"包里在不在"）。
    "whale-widget/assets/bubble-money1.gif",
    "whale-widget/assets/bubble-petpet.gif",
    "whale-widget/assets/minecraft-exp-orb.wav",
    "whale-widget/assets/task-end-a.wav",
    "scripts/pack_online.py",   # 打包器自身：里面有扫描规则字面量（含用户名样本），不进包
    "persona-morph-manifest.json",  # 更新清单：它给的是"包内文件的哈希"，自己进包会**哈希自指**死循环
    "offline/",           # 离线运行时与 wheel（在线包不需要）
    "_scratch/", "报告/", "wechatauto_logs/", "data/", "runtime/", "logs/",
)

# 扫描规则：(名字, 正则, 是否致命)
SCAN = [
    ("家目录/用户名", r"ptmou", True),
    ("Windows 绝对路径", r"[A-Za-z]:\\+Users\\+[^\\\s\"']+", True),
    ("POSIX 家目录", r"/(?:home|Users)/[A-Za-z0-9._-]+/", True),
    ("API 密钥", r"sk-[A-Za-z0-9_\-]{10,}", True),
    ("Bearer 头", r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}", True),
    ("token 值", r"token[\"'\s:=]{1,4}[A-Za-z0-9]{16,}", True),
    # ⛔ 2026-09-21 加（第九轮 **V-R9-28**）：企微/钉钉 webhook 的 key **也是一种凭据** ——
    #   当时 `agent\config.py:566` 内置了产品自带的企业微信机器人 webhook（带 key）随包发布，
    #   而这三条规则都不认它（出包闸门实测"致命 0"）＋控制台把它打码 ⇒ 谁都看不见、一直留着。
    #   规则写**真实形态**（企微 key 是 UUID、钉钉 access_token 是长 hex）⇒ 判据里的假 fixtures
    #   （`key=K` / `access_token=x` / `FAKEWEBHOOKKEY0001`）不会误伤。
    ("企业微信 webhook key", r"webhook/send\?key=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-", True),
    ("钉钉 webhook token", r"robot/send\?access_token=[0-9a-fA-F]{32,}", True),
    ("长密钥参数", r"(?:key|access_token|secret)=[0-9a-fA-F]{32,}", True),
    ("微信账号/数据", r"wxid_[A-Za-z0-9]{6,}|MsgAttach|WeChat Files[/\\]", True),
    ("开发资料引用", r"_scratch[/\\]", False),   # 注：`wechatauto_logs/` 是本产品自己的运行日志目录，不算开发资料
    ("署名/仓库名", r"Eifptaur|Eif-MAPT", False),
]

ALLOW = (
    # .gitignore 里的 `_scratch/` 是"别把本地草稿提交进来"这条规则本身，属正常仓库配置
    (".gitignore", "开发资料引用"),
    # 这条是**脱敏自检的输入夹具**：那一行故意塞满假 PII（假手机号/假邮箱/假姓名/假身份证/`wxid_abc123`），
    # 用来断言 scrub_prompt 会把它们都抹掉。它是合成的样本，不是真实账号数据 ⇒ 显式放行并写明理由。
    ("scripts/image_gen_selftest.py", "微信账号/数据"),
    # 同上：`wechat_dir_selftest` 用 `wxid_judge0001` 造**假账号目录**（`tmp/.../db_storage/...`）来验
    # "微信数据目录"的校验与回落，是合成夹具，不含任何真实账号 ⇒ 显式放行并写明理由。
    ("scripts/wechat_dir_selftest.py", "微信账号/数据"),
    # 同上：`db_discovery_selftest`（2026-09-19 加）用**同一套合成夹具**（`wxid_judge0001` +
    # 临时目录里的假 `xwechat_files/db_storage`）验"有界深扫能不能找到嵌套的自定义数据目录"
    # ⇒ 合成夹具、不含任何真实账号 ⇒ 显式放行并写明理由。
    ("scripts/db_discovery_selftest.py", "微信账号/数据"),
)

SKIP_BIN = re.compile(r"\.(png|jpe?g|gif|ico|woff2?|ttf|mp4|zip|db|sqlite3?)$", re.I)


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return [p.decode("utf-8") for p in out.split(b"\x00") if p]


def excluded(rel):
    return any(rel == e or rel.startswith(e) for e in EXCLUDE)


def scan_file(abs_path, rel):
    """返回 [(规则名, 例子)]；二进制也扫（exe/dll 里可能嵌源码路径）"""
    if SKIP_BIN.search(rel):
        return []
    try:
        with open(abs_path, "rb") as fh:
            text = fh.read().decode("latin1", "ignore")
    except OSError:
        return []
    hits = []
    for name, pat, fatal in SCAN:
        found = [m.group(0) for m in re.finditer(pat, text)]
        if not found:
            continue
        if any((rel, name) in ALLOW or (rel.split("/")[-1], name) in ALLOW for _ in [0]):
            continue
        uniq = sorted(set(found))[:3]
        hits.append((name, fatal, uniq))
    return hits


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 控制台是 GBK：中文/符号别炸
    except Exception:
        pass
    check_only = "--check" in sys.argv
    files = [f for f in tracked() if not excluded(f)]
    print(f"跟踪 {len(tracked())} 个文件 → 进包候选 {len(files)} 个")

    fatal_total, warn_total = 0, 0
    for rel in files:
        for name, fatal, uniq in scan_file(os.path.join(ROOT, rel), rel):
            tag = "FATAL" if fatal else "warn "
            print(f"  {tag} [{name}] {rel} 例: {uniq}")
            fatal_total += 1 if fatal else 0
            warn_total += 0 if fatal else 1

    # ── 入口脚本行尾闸（2026-09-15 跨机实测加的，**致命**）─────────────────
    #   背景：两个 `.cmd` 曾经是 **UTF-8 无 BOM + 纯 LF**。跨机那台（ACP/OEMCP=936）上
    #   双击**一行都跑不动** —— cmd.exe 解析不了 LF 行尾的 `if ... goto` / `for /f` /
    #   括号块（对照实验：LF 三种编码全断、CRLF 三种全通），而包本身看着完全正常。
    #   ⇒ 出包前必检：随包的 `.cmd` 一律 CRLF（`.gitattributes` 也钉了 `*.cmd eol=crlf`）。
    crlf_bad = []
    for rel in files:
        if not rel.lower().endswith(".cmd"):
            continue
        with open(os.path.join(ROOT, rel), "rb") as _f:
            _b = _f.read()
        if _b.count(b"\n") != _b.count(b"\r\n"):
            crlf_bad.append(rel)
    if crlf_bad:
        print("❌ 这些 .cmd 的行尾不是 CRLF（在 GBK 机器上双击会一行都跑不动）：%s" % crlf_bad)
        print("   修法：把文件整体转成 CRLF（不要加 BOM，改完保持 `chcp 65001` 在第二行）")
        return 5

    if check_only:
        print(f"扫描结束：致命 {fatal_total} · 注意 {warn_total}")
        return 0 if fatal_total == 0 else 3

    if fatal_total:
        print(f"❌ 有 {fatal_total} 处致命命中 ⇒ 拒绝出包（先清干净或显式加入 ALLOW）")
        return 3

    stamp = datetime.now().strftime("%Y%m%d")
    out = os.path.join(OUT_DIR, f"{PKG_PREFIX}{stamp}.zip")
    # ⚡ 2026-09-18 晚：**打包前把内容指纹写进 `agent/version.py`**（清单里带 `base.build`，
    #   用户侧才能在"同一个版本号换了包"时看出来）。指纹只跟"进包的那些文件"有关，且算
    #   `agent/version.py` 时会先抹掉 BUILD 行 ⇒ 不会自指。开发树里 BUILD 留空，只有出包才写。
    try:
        sys.path.insert(0, ROOT)
        from agent import version as _ver
        _fp = _ver.build_fingerprint(files, root=ROOT)
        _ver.write_build(_fp)
        print(f"内容指纹（build）：{_fp} —— 已写入 agent/version.py（同名版本换包时用户侧靠它看出来）")
    except Exception as _e:
        print(f"❌ 内容指纹计算失败 ⇒ 拒绝出包：{_e}")
        return 6
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), "%s/%s" % (ZIP_TOP, rel))
    size = os.path.getsize(out) / 1024 / 1024

    # ── ⑥ 出厂初始状态断言（2026-09-15 既有口径：）────────────────────────
    #   把包里**实际写进去的条目**读回来核对三件事：①没有运行期数据（会话/记忆/日志/配置）
    #   ②没有个人痕迹（家目录、密钥、微信账号 —— 与 PII 扫描互为双保险）
    #   ③必需文件都在、且全部在顶层目录下。任一不满足 ⇒ 拒绝出包（exit 4）。
    import re as _re
    with zipfile.ZipFile(out) as _z:
        names = _z.namelist()
    rel_names = [n[len(ZIP_TOP) + 1:] for n in names if n.startswith(ZIP_TOP + "/")]
    BAD_STATE = _re.compile(r"(^|/)(data|logs|offline|runtime|_scratch|报告|wechatauto_logs)/"
                            r"|(^|/)config\.json$|(^|/)config\.json\."
                            r"|\.(db|sqlite3?|jsonl|log|zst|zstd)$", _re.I)
    bad_state = sorted(n for n in rel_names if BAD_STATE.search(n))
    # 只看**绝对**路径（带盘符）—— `collect_report.py` 里那个 `C:/Users/<名>` 是脱敏器自己的正则、
    # 是它的工作内容，不该按"泄漏"算。
    BAD_TRACE = _re.compile(r"ptmou|[A-Za-z]:[/\\]+Users[/\\][^/\\\s\"']+"
                            r"|sk-[A-Za-z0-9_\-]{10,}|wxid_[A-Za-z0-9]{6,}", _re.I)
    # 只查文本类文件（二进制素材里出现这几个字节串不说明问题）
    bad_trace = []
    with zipfile.ZipFile(out) as _z:
        for n in rel_names:
            if not _re.search(r"\.(py|js|json|md|txt|cmd|ps1|yml|yaml|ini|cfg)$", n, _re.I):
                continue
            try:
                t = _z.read(ZIP_TOP + "/" + n).decode("utf-8", "replace")
            except Exception:
                continue
            if BAD_TRACE.search(t):
                # 与既有 PII 扫描同一套白名单：脱敏器的测试夹具（假 id/假号码）显式放行并写理由
                # 凡是被 ALLOW 显式放行过的文件，这里不再重复拦截（同一个理由，别抄两遍）
                if any(k[0] in (n, n.split("/")[-1]) for k in ALLOW):
                    continue
                bad_trace.append(n)
    need = ["agent/__init__.py", "scripts/persona_morph.py", "config.example.json",
            "README.md", "一键启动.exe", "requirements.txt",
            # ⑤（2026-09-15）：控制台窗口靠 WebView2 显示。这四样缺任何一样，用户那台机器上
            # 要么窗口起不来（缺 DLL），要么"缺运行库又没浏览器"时**没有引导器可装**——
            # 只能看到"点了按钮没反应"。引导器是微软官方 Evergreen Bootstrapper（允许随应用分发）。
            "WebView2Loader.dll", "lib/Microsoft.Web.WebView2.Core.dll",
            "lib/Microsoft.Web.WebView2.WinForms.dll",
            "assets/webview2/MicrosoftEdgeWebview2Setup.exe",
            # 小鲸鱼挂件（控制台右下角那个）：宿主代码 `agent/whale.py` 一直在包里，但**前端脚本与
            # 素材**原先被整目录排除 ⇒ 装上以后挂件是死的（脚本 0 字节、图片取不到）。
            # 2026-09-19 起按"只发程序真正要用的那几样"收窄排除；这五样缺任何一样，挂件就起不来。
            "whale-widget/client/widget.js", "whale-widget/assets/DSniang1.png",
            "whale-widget/assets/rua.gif", "whale-widget/assets/Ya1.mp3",
            "whale-widget/LICENSE-原版.txt"]
    missing = [n for n in need if n not in rel_names]
    outside = [n for n in names if not n.startswith(ZIP_TOP + "/")]
    if bad_state or bad_trace or missing or outside:
        print("❌ 出厂初始状态断言未过 ⇒ 拒绝出包（exit 4）：")
        if bad_state:
            print("   · 含运行期数据：%s" % bad_state[:8])
        if bad_trace:
            print("   · 含个人痕迹（家目录/密钥/微信账号）：%s" % bad_trace[:8])
        if missing:
            print("   · 缺必需文件：%s" % missing)
        if outside:
            print("   · 有文件不在顶层目录下：%s" % outside[:5])
        return 4
    print(f"✅ 出包：{out}  {len(files)} 文件 / {size:.2f} MB  （注意项 {warn_total} 条，非致命）")
    print(f"   解压后顶层目录：{ZIP_TOP}/（用户看到的就是这个名字）")
    print(f"   出厂初始状态断言：通过（{len(rel_names)} 个条目全在顶层下 · 无运行期数据 · 无个人痕迹 · 必需文件齐）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
