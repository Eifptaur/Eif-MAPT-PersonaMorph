# -*- coding: utf-8 -*-
"""在线包打包器（群相）—— 只打"版本库里跟踪的代码"，并做**个人信息/开发资料扫描闸门**。

用法：
  py -3 scripts/pack_online.py                   # 打包 + 扫描（命中即拒绝出包 exit 3）
  py -3 scripts/pack_online.py --check           # 只扫描仓库（不出包）
  py -3 scripts/pack_online.py --slim            # 出"含依赖包"但**再剔掉用户用不到的包**（见 BLOAT）
  py -3 scripts/pack_online.py --with-wheels     # 只多带依赖、不做瘦身（保留给"要最全"的场合）
  py -3 scripts/pack_online.py --no-wheels       # 出"在线包"（首启联网拉依赖，只在网络可控时用）

口径：
  ① **正式发行形态＝"含依赖包"**：把 `offline/`（便携运行时 zip ＋ wheel）打进包，
     首装**零联网**（装依赖走本地轮子，≈30 秒；联网装实测 8 分 28 秒）。
     不带依赖的"在线包"只在网络条件明确可控时才用（`--no-wheels`）。
  ② 包里**不许有他的个人信息与开发资料**——家目录路径 / 密钥 / token / 聊天数据 /
     开发文档（AGENTS.md、docs/ 下的任务清单与 changelog 归档）；
  ③ 打包源＝`git ls-files`（未跟踪的草稿、报告、日志一律进不来）；
  ④ 扫描命中一律拒绝出包（要放行必须显式加进 ALLOW 并写明理由）；
  ⑤ **用户用不到的东西不进包**（命令行工具 / 测试夹具 / 开发机专属的大数据包），
     规则表在 `BLOAT`，命中即拒绝出包（不是"注意"）。

⛔ 三类文件的边界（出包闸门按这条分档，不是一刀切）：
  ① **我方程序文件**（`agent/ scripts/ ui_qt/ client/ assets/ …`）⇒ 随包发布，升级时
     **逐文件比对、有改动才替换**（实现见 `agent/update_apply.py` 的 `zip_tree` + `_touched`）；
  ② **依赖**（`offline/wheels` 的轮子、`offline/python` 的运行时 zip）⇒ 随包发布，
     升级时**按包级清单补齐**（`qt_bootstrap.ensure_pyside6` / `scripts/setup_python.ps1` /
     `scripts/setup_deps.py` 三处都支持"包里有就本地装"）；
  ③ **用户数据与用户装好的依赖**（`data/ logs/ runtime/ config.json wechatauto_logs/`）⇒
     **绝不覆盖**（写在 `agent/update_apply.py::NEVER_TOUCH`）。
  出包闸门负责前两类的**素材齐不齐**；第三类由更新器的 `NEVER_TOUCH` 兜底 —— 两边互补，
  缺任何一边都会出事（闸门只管"包里有"，更新器只管"别覆盖"）。

⚠️ （小鲸鱼挂件）：`whale-widget/` 原先**整目录排除**，理由是"另一个项目的素材"。
  但控制台右下角那个挂件**就是靠这个目录跑的**（`webui._whale_js_injected()` 读
  `whale-widget/client/widget.js`，`image.png`/`rua.gif`/音效走 `assets/`）⇒ 整目录排除等于
  **装上以后挂件是死的**（脚本 0 字节、图片取不到，界面上什么都没有 —— 只有侧栏那个徽章还在）。
  现在按"只发程序真正要用的那几样"收窄排除，并且**已获原
  代码 MIT，`assets/**` 不在 MIT 范围内、原文是"不授予再许可"；同意记录见 `whale-widget/PORT-NOTES.md`）。
"""
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.dirname(ROOT) # 仓库的上一级目录（例：C:\Users\<你>\Desktop\WX-chatbot）
# 产物名前缀。⛔ 别再叫"在线包"：正式形态是**含依赖**的安装包（首装零联网），
#   叫"在线包"会让人以为还要联网拉依赖（历史名，用户口径见文件头 ①）。
PKG_PREFIX = "群相-安装包-"
# 解压出来的**顶层文件夹名**
# ⚠️ 只影响压缩包的目录布局，不动任何程序逻辑：程序内部一律用"自己所在目录"定位（ROOT=文件位置），
#    仓库里也没有别处硬编码过仓库目录名（打包器自己那条注释除外）。
ZIP_TOP = "persona morph"

# ⛔ 本机用户名**不许写死在源码里**：本仓库是公开的，写死等于把用户名一起公开出去
#   （这条规则以前就是靠写死那两个字来扫别人，结果自己成了泄漏源）。改成运行时推导 ——
#   Windows 取 `%USERNAME%`、其他平台取 `~` 的最后一段。取不到就返回空串，
#   调用方用「永不匹配」的模式代替（空正则 `""` 会匹配一切，正好是反向的坑）。
def me_name() -> str:
    n = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not n:
        n = os.path.basename(os.path.expanduser("~")) or ""
    return str(n).strip()


#: 本机用户名（扫「家目录/用户名」这一类泄漏用）；取不到＝空串
ME_NAME = me_name()

# 不进包（相对仓库根的 posix 路径前缀 / 精确名）
EXCLUDE = (
    "AGENTS.md", # 开发守则：含红线自述与既有口径：，不随包发
    "docs/", # 开发资料：任务清单 / changelog 归档
    "whale-widget/upstream-0.3.13/", # 上游原文留档（README/PROVENANCE/package.json）＝开发资料，不随包发
    "whale-widget/upstream-0.3.18/", # 同上（换留档后同步加一条；不出包的文件必须显式声明，否则出包会被拒）
    "ui_qt-probes/", # 一次性探针目录（同族历史探针都在 dev-workspace）＝开发资料，不随包发
    "whale-widget/assets/DSniang02.png", # 备用整图：我们的路由用不到（image.png 走 DSniang1.png）
    "whale-widget/assets/DSH2.png", # 上游 README 展示图：程序不用（1.1MB，别白占包体积）
    # ⛔ 这四件**只被上游留档文档引用**，`agent/whale.py` 已把同族
    #   资源声明为不支持 ⇒ 死重约 **3.2MB ≈ 包体 22%**（挂件判据只查"仓库里在不在"，不查"包里在不在"）。
    "whale-widget/assets/bubble-money1.gif",
    "whale-widget/assets/bubble-petpet.gif",
    "whale-widget/assets/minecraft-exp-orb.wav",
    "whale-widget/assets/task-end-a.wav",
    "scripts/pack_online.py", # 打包器自身：里面有扫描规则字面量（含用户名样本），不进包
    "scripts/fetch_wheels.py", # 取离线轮子的开发工具（同族：只在出"快速包"时用），不进包
    "scripts/subset_fonts.py", # 字体子集生成（开发工具：改了字体文件才用它重生成），不进包
    "persona-morph-manifest.json", # 更新清单：它给的是"包内文件的哈希"，自己进包会**哈希自指**死循环
    "offline/", # 离线运行时与 wheel（默认不放行；`--with-wheels` / `--slim` 才收，见 main()）
    "_scratch/", "报告/", "wechatauto_logs/", "data/", "runtime/", "logs/",
)

# ── 包内膨胀闸门（`--slim` / `--with-wheels` 都跑）────────────────────────
#   口径：**用户用不到的东西不进包** —— 命令行工具、测试夹具、开发机专属的大数据包。
#   ⛔ 以前这条只在"出包时靠 `EXCLUDE` 排除、靠人记得" ⇒ 轮子一升级就可能把
#     几百 MB 的命令行工具（连带它拖进来的整棵依赖树）一起塞进用户下载的包里。
#   ⇒ 改成规则表 + 出包断言：命中即拒绝（不是"注意项"）。
#   每条规则都写明"为什么用户用不到"，免得后人当成误伤删掉。
BLOAT = [
    # (规则, 说明)
    (r"/torch(-|/)|/torch[0-9._-]",
     "PyTorch 主包（开发机上 4.2GB）：程序只有 3 处 `import torch`，全在函数内部、且是本地画图的可选路径"),
    (r"/torchvision(-|/)|/torchaudio(-|/)",
     "torch 的兄弟包：全库引用数 0，纯属被 torch 拖进来"),
    (r"/(sympy|numba|llvmlite|transformers)(-|/)",
     "torch 的传递依赖：全库引用数 0，只有 torch 在时会一起被拖进来"),
    (r"/pyarrow(-|/)|/pandas(-|/)",
     "数据分析栈：产品里没有任何一处用到（历史误装）"),
    (r"/jupyter|/notebook(-|/)|/ipykernel(-|/)",
     "命令行/交互式开发工具：用户是双击图标用产品，不会开 notebook"),
    (r"/(youtube-dl|you-get)(-|/)",
     "命令行下载器：产品用的是 `yt-dlp` 的 Python API，不需要这两个命令行前端"),
    (r"/(pytest|nose|tox|coverage|flake8|pylint|mypy|black|isort|ruff)(-|/)",
     "测试/代码检查工具：只在开发机上跑，产品运行期不 import"),
    (r"/(tensorboard|tensorflow|keras|onnxruntime-training)(-|/)",
     "训练/可视化框架：产品从不训练模型（推理只用 `rapidocr_onnxruntime`）"),
    (r"/(scipy|matplotlib|seaborn)(-|/)",
     "科学计算/绘图：产品不画图不给用户看（本机画图那条路走的是可选 torch）"),
    (r"/(nvidia|cuda|cuDNN|cudnn|cublas|cufft|curand|cusolver|cusparse|nccl)([-_/]|$)",
     "CUDA 运行期库：产品全程 CPU 推理，装这些只是白占几百 MB"),
    (r"/sentencepiece(-|/)",
     "分词器：只服务 transformers（见上），产品不用"),
]

# 扫描规则：(名字, 正则, 是否致命)
SCAN = [
    ("家目录/用户名", re.escape(ME_NAME) if ME_NAME else r"(?!)", True),
    ("Windows 绝对路径", r"[A-Za-z]:\\+Users\\+[^\\\s\"']+", True),
    ("POSIX 家目录", r"/(?:home|Users)/[A-Za-z0-9._-]+/", True),
    ("API 密钥", r"sk-[A-Za-z0-9_\-]{10,}", True),
    ("Bearer 头", r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}", True),
    ("token 值", r"token[\"'\s:=]{1,4}[A-Za-z0-9]{16,}", True),
    # ⛔ 企微/钉钉 webhook 的 key **也是一种凭据** ——
    #   当时 `agent\config.py:566` 内置了产品自带的企业微信机器人 webhook（带 key）随包发布，
    #   而这三条规则都不认它（出包闸门实测"致命 0"）＋控制台把它打码 ⇒ 谁都看不见、一直留着。
    #   规则写**真实形态**（企微 key 是 UUID、钉钉 access_token 是长 hex）⇒ 判据里的假 fixtures
    #   （`key=K` / `access_token=x` / `FAKEWEBHOOKKEY0001`）不会误伤。
    ("企业微信 webhook key", r"webhook/send\?key=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-", True),
    ("钉钉 webhook token", r"robot/send\?access_token=[0-9a-fA-F]{32,}", True),
    ("长密钥参数", r"(?:key|access_token|secret)=[0-9a-fA-F]{32,}", True),
    ("微信账号/数据", r"wxid_[A-Za-z0-9]{6,}|MsgAttach|WeChat Files[/\\]", True),
    ("开发资料引用", r"_scratch[/\\]", False), # 注：`wechatauto_logs/` 是本产品自己的运行日志目录，不算开发资料
    ("署名/仓库名", r"Eifptaur|Eif-MAPT", False),
]

ALLOW = (
    # 年报判据里的 `wxid_abc12345` 是**合成夹具**：用来验「群消息 content 头上的 `wxid_xxx:` 前缀会被剥掉」
    #   ⇒ 它必须长得像真 wxid 才能测到那条分支。**不是真实账号**（不连着任何真实会话）。
    ("scripts/report_card_selftest.py", "微信账号/数据"),
    # .gitignore 里的 `_scratch/` 是"别把本地草稿提交进来"这条规则本身，属正常仓库配置
    (".gitignore", "开发资料引用"),
    # ⭐ **出厂反馈接收端**：`assets/feedback_endpoint.json`（含作者自己的群机器人地址）。
    #   它**不进仓库**（.gitignore 里那条）⇒ 只在本机存在、出包时显式收进包 —— 产品必须自带
    #   一个接收端，"用户点一下就把反馈发到作者那儿"才成立（的真 bug：产品不自带接收端，
    #   网友在控制台提交了、作者一条都没收到）。
    #   代价如实写明：**下载包的人能提取这个地址往那个群推消息** ⇒ 泄露了就轮换
    #   （改本机 config.json 里的 webhook_url + 重新出一版即可；因为不进仓库，**不用改 git 历史**）。
    #   这里显式放行而不是绕过闸门：`main()` 每次出包都会打印一行"已收进出厂反馈接收端"。
    ("assets/feedback_endpoint.json", "企业微信 webhook key"),
    ("assets/feedback_endpoint.json", "长密钥参数"),
    # 这条是**脱敏自检的输入夹具**：那一行故意塞满假 PII（假手机号/假邮箱/假姓名/假身份证/`wxid_abc123`），
    # 用来断言 scrub_prompt 会把它们都抹掉。它是合成的样本，不是真实账号数据 ⇒ 显式放行并写明理由。
    ("scripts/image_gen_selftest.py", "微信账号/数据"),
    # 同上：`wechat_dir_selftest` 用 `wxid_judge0001` 造**假账号目录**（`tmp/.../db_storage/...`）来验
    # "微信数据目录"的校验与回落，是合成夹具，不含任何真实账号 ⇒ 显式放行并写明理由。
    ("scripts/wechat_dir_selftest.py", "微信账号/数据"),
    # 同上：`db_discovery_selftest`用**同一套合成夹具**（`wxid_judge0001` +
    # 临时目录里的假 `xwechat_files/db_storage`）验"有界深扫能不能找到嵌套的自定义数据目录"
    # ⇒ 合成夹具、不含任何真实账号 ⇒ 显式放行并写明理由。
    ("scripts/db_discovery_selftest.py", "微信账号/数据"),
    # 同上：`compat_selftest` 的 F8 **反向锚**往状态文件里塞「群名 + 用户目录」，用来断言
    # `compat.attach_text()` 会把它们脱敏掉；那条 `C:\Users\某个人\Documents\xwechat_files`
    # 是**故意造的假路径**（连用户名都是占位词「某个人」），不是任何真实用户的目录
    # ⇒ 合成夹具 ⇒ 显式放行并写明理由。
    ("scripts/compat_selftest.py", "Windows 绝对路径"),
    # 同上：`popover_empty_shell_selftest` 用 `wxid_sample0007` 这类**合成假 id** 当"会话 key"
    # 验弹层/空壳判定（与上面几个 selftest 同一套口径）⇒ 不含真实账号 ⇒ 显式放行并写明理由。
    ("scripts/popover_empty_shell_selftest.py", "微信账号/数据"),
    # 同上，且**这条非放行不可**：`prompt_pii_selftest` 守的正是"**模型输入里不许出现 wxid**"，
    # 它必须构造 `wxid_…` 形状的样本才验得出来（否则判据本身没法证明任何事）。
    # 样本用公认的合成前缀 `wxid_sample…`，不含任何真实账号 ⇒ 显式放行并写明理由。
    ("scripts/prompt_pii_selftest.py", "微信账号/数据"),
    ("scripts/prompt_pii_selftest.py", "真实 wxid"),
)

SKIP_BIN = re.compile(r"\.(png|jpe?g|gif|ico|woff2?|ttf|mp4|zip|db|sqlite3?)$", re.I)


def tracked():
    """仓库里**实际存在于盘上**的受版本控制文件（相对仓库根的 posix 路径）。

    ⛔ **必须过滤"已删除但未提交"的条目**：`git ls-files` 会把它们照样列出来
      （文件已被删、改动还没提交），而它们在盘上并不存在 ⇒ 后面 `zipfile.write` / `os.stat`
      会抛 `FileNotFoundError`，**整个出包中断**（实测：一个乱码名旧文件就卡住整条链）。
    ⛔ 解码固定用 utf-8：用系统默认编码（中文 Windows 上是 GBK）会把 UTF-8 文件名
      解成乱码 ⇒ 同样 `FileNotFoundError`。
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    rels = [p.decode("utf-8") for p in out.split(b"\x00") if p]
    return [r for r in rels if os.path.exists(os.path.join(ROOT, r))]


def excluded(rel):
    return any(rel == e or rel.startswith(e) for e in EXCLUDE)


#: 「该进包」的根：这些目录下的 `.py` 等文件**必须入库**，否则不出包（见 `untracked_ship_files`）。
#  ⛔ 只列**随包发布**的目录；`_scratch/`、`_recovered_stash_*/`、`data/`、`logs/`、`runtime/` 不在内。
SHIP_ROOTS = ("agent/", "scripts/", "ui_qt/", "client/", "assets/", "tools.d/", "whale-widget/")
#: 根目录下**本身就会进包**的文件后缀（散落在仓库根的那些入口/说明/配置）
SHIP_ROOT_EXT = (".py", ".cmd", ".bat", ".ps1", ".json", ".txt", ".md", ".exe", ".dll", ".ico")


def untracked_ship_files():
    """**该进包却没入库**的文件 ⇒ 列表；拿不到 git 状态 ⇒ `None`（调用方据此区别对待）。

    ⛔ 为什么必须有它：`tracked()` 只认 `git ls-files`（受版本控制的文件），
      而**新建了但没 `git add` 的文件不会进包，出包却照样报成功** ——
      现场：`agent/tree_snapshot.py` 与 9 个 `scripts/*_selftest.py` 都静静躺在工作区、
      一个都不在包里，而"出厂断言"只看包内条目，看不见这种缺。
      ⇒ 这条链上"少了文件"必须**出不了包**，不能靠人记得 `git add`。
    只收**该进包的位置**（`SHIP_ROOTS` 下的、以及仓库根下带 `SHIP_ROOT_EXT` 的散文件），
    并**尊重 `EXCLUDE`**（明确声明不发的就不算漏）。
    """
    try:
        out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"],
                             cwd=ROOT, capture_output=True, check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001
        return None
    bad = []
    for ent in out.decode("utf-8", "replace").split("\x00"):
        if not ent.startswith("?? "):
            continue
        rel = ent[3:].replace("\\", "/")
        if excluded(rel):
            continue
        if "/" in rel:
            if any(rel.startswith(p) for p in SHIP_ROOTS):
                bad.append(rel)
        elif rel.lower().endswith(SHIP_ROOT_EXT):
            bad.append(rel)   # 仓库根的散文件（入口/说明/配置）
    return sorted(bad)


#: 出包时**额外**收进包、但**不在仓库里**的文件（相对仓库根的 posix 路径）。
#:
#: ⛔ 这份清单必须与 `make_manifest.py` 共用同一句：更新器（`agent/update_apply.zip_tree`）
#:    是从**包内全部条目**重算文件树哈希、再与清单 `base.sha256` 对齐才允许安装 ——
#:    进包清单与清单哈希用的文件集只要差一个文件，用户点更新就会被判"包与清单不自洽"。
def shipped_extra() -> list:
    ep = "assets/feedback_endpoint.json"   # 出厂反馈接收端（见 ALLOW 里的说明）
    return [ep] if os.path.exists(os.path.join(ROOT, ep)) else []


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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace") # 控制台是 GBK：中文/符号别炸
    except Exception:
        pass
    check_only = "--check" in sys.argv
    # ⚡ **正式发行形态＝"含依赖包"**：把离线依赖（`offline/wheels` 的轮子 + `offline/python` 的运行时 zip）
    #   一起打进去 ⇒ 用户装的时候**一次都不用联网**（实测：联网装依赖 8 分 28 秒 → 本地装 ≈30 秒）。
    #   代价＝包体积多约 200MB（本轮实测 `offline/wheels` 199MB + 运行时 zip 8.6MB）。
    #   ⛔ 为什么默认改成"含依赖"：用户口径——"那联网下的那 100~150MB 不能直接打进 setup 里面吗，
    #     十几分钟，用户还不如直接下整包呢" ⇒ 让用户干等十几分钟去拉依赖是更差的选择；
    #     一次下整包（多 200MB，但下载不会卡在"装到一半失败"）。**"在线包"降级为可选档**（`--no-wheels`）。
    #   `--slim`：含依赖，但把 `BLOAT` 命中的用户用不到的包剔出去（见文件头 ⑤）。
    no_wheels = "--no-wheels" in sys.argv
    slim = "--slim" in sys.argv
    # `--with-wheels` 保留为显式档（不做瘦身），与 `--slim` 等价但不跑 BLOAT 剔除
    explicit_wheels = "--with-wheels" in sys.argv
    with_wheels = (not no_wheels)
    if no_wheels and (slim or explicit_wheels):
        print("✘ `--no-wheels` 与 `--slim`/`--with-wheels` 互斥（一个不要依赖、一个要带依赖）")
        return 2
    global EXCLUDE # noqa: PLW0603
    if with_wheels:
        EXCLUDE = tuple(e for e in EXCLUDE if e != "offline/") # 放行 offline/（下面只收 wheels 与 python zip）
    files = [f for f in tracked() if not excluded(f)]
    # ⛔⛔ **不许静默漏文件**：`tracked()` 只认 `git ls-files` ⇒ **新建了但没入库的文件不会进包，
     #   而且出包照样报成功**（现场：本轮新增的 `agent/tree_snapshot.py` 与 **9 个 `scripts/*_selftest.py`**
     #   都在工作区里、都不在包里；"出厂断言"只看包内条目，看不见这种缺）。
     #   ⇒ 出包前先查一遍"该进包却没入库"的文件，**命中就拒绝出包**（宁可不出，不许悄少）。
     #   处理办法两种：① 真的要发 ⇒ `git add`；② 本来就不该发 ⇒ 写进 `EXCLUDE` 并写明理由。
    _unt = untracked_ship_files()
    if _unt is None:
        print("⚠️  拿不到 git 状态 ⇒ **无法核对'新建文件是否入包'**（继续出包，但这次别当成已核对）")
    elif _unt:
        print("✘ 拒绝出包：工作区里有 **%d 个该进包却没入库**的文件（未 tracked ⇒ 不会进包，且不报错）："
              % len(_unt))
        for _x in _unt[:25]:
            print("    ", _x)
        if len(_unt) > 25:
            print("     ……还有 %d 个" % (len(_unt) - 25))
        print("   ⇒ 要发就先 `git add`；本来不该发就写进 EXCLUDE（并写明理由）。")
        return 3
    if with_wheels:
        # ⛔ `tracked()` 只认 `git ls-files`，而 `offline/` 下的轮子是**构建产物、没入库**
        #    ⇒ 必须显式扫目录补进来（只补这两类，别的仍然不收）。
        extra = []
        for sub in ("wheels", "python"):
            base = os.path.join(ROOT, "offline", sub)
            for dp, dn, fns in os.walk(base):
                dn[:] = [d for d in dn if d != "__pycache__"]
                for f in fns:
                    if sub == "python" and not f.lower().endswith(".zip"):
                        continue # 只发运行时 zip，不发解压出来的树
                    extra.append(os.path.relpath(os.path.join(dp, f), ROOT).replace("\\", "/"))
        # ── `--slim`：把用户用不到的包（`BLOAT`）从依赖里剔出去 ──────────────
        #   ⛔ 只对 `offline/wheels/**` 生效（那才是"我们主动带上、要给用户装"的东西）；
        #     `tracked()` 那批是源码，不受影响。剔除**必须打印**，不许静默少文件。
        if slim:
            _bloat_re = re.compile("|".join("(?:%s)" % p for p, _ in BLOAT), re.I)
            dropped = [f for f in extra if _bloat_re.search("/" + f.lower())]
            if dropped:
                _mb = 0.0
                for _f in dropped:
                    try:
                        _mb += os.path.getsize(os.path.join(ROOT, _f)) / 1024 / 1024
                    except OSError:
                        pass
                print("--slim：剔掉 %d 个用户用不到的依赖（约 %.1f MB）：" % (len(dropped), _mb))
                for _f in dropped[:12]:
                    print("     -", os.path.basename(_f))
                if len(dropped) > 12:
                    print("     ……还有 %d 个" % (len(dropped) - 12))
                extra = [f for f in extra if f not in set(dropped)]
            else:
                print("--slim：依赖里没有命中 BLOAT 规则的包（已核过 %d 个依赖文件）" % len(extra))
        files = sorted(set(files) | set(extra))
    # ── 出厂反馈接收端（`assets/feedback_endpoint.json`）──────────────
    #   它**不在 git 里**（含真实凭据，.gitignore 挡着）⇒ 这里显式收进包。
    #   缺了必须**大声说**：没有它，本机没配通道的器上、作者收不到。
    _extra_shipped = shipped_extra()
    if _extra_shipped:
        files = sorted(set(files) | set(_extra_shipped))
        print("已收进出厂反馈接收端：%s（用户零配置即可提交；仓库里没有这个文件）" % _extra_shipped[0])
    else:
        print("⚠️ 没找到 assets/feedback_endpoint.json ⇒ 用户本机没配通道时，"
              "反馈只会落在他自己机器上（作者收不到）")
    print(f"跟踪 {len(tracked())} 个文件 → 进包候选 {len(files)} 个"
          + ("（含离线依赖）" if with_wheels else ""))

    fatal_total, warn_total = 0, 0
    for rel in files:
        for name, fatal, uniq in scan_file(os.path.join(ROOT, rel), rel):
            tag = "FATAL" if fatal else "warn "
            print(f"  {tag} [{name}] {rel} 例: {uniq}")
            fatal_total += 1 if fatal else 0
            warn_total += 0 if fatal else 1

    # ── 入口脚本行尾闸─────────────────
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
    _suffix = ("-在线" if no_wheels else ("-含依赖-瘦身" if slim else "-含依赖"))
    out = os.path.join(OUT_DIR, f"{PKG_PREFIX}{stamp}{_suffix}.zip")
    # ⚡ **打包前把内容指纹写进 `agent/version.py`**（清单里带 `base.build`，
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

    # ── ⑥ 出厂初始状态断言────────────────────────
    #   把包里**实际写进去的条目**读回来核对三件事：①没有运行期数据（会话/记忆/日志/配置）
    #   ②没有个人痕迹（家目录、密钥、微信账号 —— 与 PII 扫描互为双保险）
    #   ③必需文件都在、且全部在顶层目录下。任一不满足 ⇒ 拒绝出包（exit 4）。
    import re as _re
    with zipfile.ZipFile(out) as _z:
        names = _z.namelist()
    rel_names = [n[len(ZIP_TOP) + 1:] for n in names if n.startswith(ZIP_TOP + "/")]
    BAD_STATE = _re.compile(
        # ⚡ `--with-wheels`（快速包）时 `offline/wheels` 与 `offline/python/*.zip` 是**故意带上的素材**
        #   （不是运行期数据）⇒ 这条闸门里把 `offline` 拿掉，别的（data/logs/runtime/配置/库/日志）照旧禁。
        (r"(^|/)(data|logs|runtime|_scratch|报告|wechatauto_logs)/" if with_wheels
         else r"(^|/)(data|logs|offline|runtime|_scratch|报告|wechatauto_logs)/")
        + r"|(^|/)config\.json$|(^|/)config\.json\."
        r"|\.(db|sqlite3?|jsonl|log|zst|zstd)$", _re.I)
    bad_state = sorted(n for n in rel_names if BAD_STATE.search(n))
    # 只看**绝对**路径（带盘符）—— `collect_report.py` 里那个 `C:/Users/<名>` 是脱敏器自己的正则、
    # 是它的工作内容，不该按"泄漏"算。
    _me_pat = (_re.escape(ME_NAME) + "|") if ME_NAME else "" # 同上：本机用户名运行时推导，不写死
    BAD_TRACE = _re.compile(_me_pat + r"[A-Za-z]:[/\\]+Users[/\\][^/\\\s\"']+"
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
            # ⛔ 启动器的真名是 **`群相.exe`**（用户口径：启动器的名字就是产品的名字）。
            #   这里原先写的是历史名 `一键启动.exe` ⇒ 编译产物（`群相.exe`）对不上，
            #   `--check` 与真打包都会以"缺必需文件"拒绝出包 —— 属"一改全改"漏掉的最后一处
            #   （其余各处 `dead_entry_selftest` / `fullcheck` / `launcher_event_contract_selftest`
            #   在改名时都已同步）。⛔ 别再改回旧名。
            "README.md", "群相.exe", "requirements.txt",
            # ⑤：控制台窗口靠 WebView2 显示。这四样缺任何一样，用户那台机器上
            # 要么窗口起不来（缺 DLL），要么"缺运行库又没浏览器"时**没有引导器可装**——
            # 只能看到"点了按钮没反应"。引导器是微软官方 Evergreen Bootstrapper（允许随应用分发）。
            "WebView2Loader.dll", "lib/Microsoft.Web.WebView2.Core.dll",
            "lib/Microsoft.Web.WebView2.WinForms.dll",
            "assets/webview2/MicrosoftEdgeWebview2Setup.exe",
            # 小鲸鱼挂件（控制台右下角那个）：宿主代码 `agent/whale.py` 一直在包里，但**前端脚本与
            # 素材**原先被整目录排除 ⇒ 装上以后挂件是死的（脚本 0 字节、图片取不到）。
            # 起按"只发程序真正要用的那几样"收窄排除；这五样缺任何一样，挂件就起不来。
            "whale-widget/client/widget.js", "whale-widget/assets/DSniang1.png",
            "whale-widget/assets/rua.gif", "whale-widget/assets/Ya1.mp3",
            "whale-widget/LICENSE-原版.txt"]
    missing = [n for n in need if n not in rel_names]
    outside = [n for n in names if not n.startswith(ZIP_TOP + "/")]
    # ③ **用户数据与用户装好的依赖不随版本变**：
    #    包里不许有 `config.json`、`data/`（用户数据，更新器的 `NEVER_TOUCH` 也要兜住这一类）；
    #    `runtime/` 也一样 —— 那是"用户机器上装好的依赖"，包里带它的 zip
    #    （走 `offline/python/*.zip`）是**素材**，不是"装好的树"。
    #    ⛔ 注意这里**不是**"不许有 runtime"：用户口径是"为啥不能有 runtime，就跟微信那种形式，
    #      setup 直接安在本地不行吗" ⇒ 依赖要跟着包走（`offline/`），但**装好之后那棵树**（`runtime/`）
    #      属于用户机器上的东西，不许被版本包覆盖（否则就是用户抱怨的"更新完又装一遍"）。
    forbidden = sorted(n for n in rel_names
                       if n == "config.json" or n.startswith(("runtime/", "data/")))
    # ④ **包内膨胀断言**：`BLOAT` 命中的包一律不许出现在包里（不是"注意项"）。
    #    对 `--slim` 这是"剔过之后确认真没进去"；对 `--with-wheels`（不瘦身）这是**如实报警**：
    #    它允许带，但必须在输出里说得清清楚楚，免得有人以为"含依赖包"天然就该有 torch。
    _bloat_re2 = _re.compile("|".join("(?:%s)" % p for p, _ in BLOAT), _re.I)
    bloat = sorted(n for n in rel_names if _bloat_re2.search("/" + n.lower()))
    if bad_state or bad_trace or missing or outside or forbidden:
        print("❌ 出厂初始状态断言未过 ⇒ 拒绝出包（exit 4）：")
        if bad_state:
            print("   · 含运行期数据：%s" % bad_state[:8])
        if bad_trace:
            print("   · 含个人痕迹（家目录/密钥/微信账号）：%s" % bad_trace[:8])
        if missing:
            print("   · 缺必需文件：%s" % missing)
        if outside:
            print("   · 有文件不在顶层目录下：%s" % outside[:5])
        if forbidden:
            print("   · 含不该随包分发的用户数据/装好的依赖（这两类要留在用户机器上）：%s" % forbidden[:8])
        return 4
    if bloat:
        print("❌ 包内膨胀断言未过 ⇒ 拒绝出包（exit 7）：包里含用户用不到的包（%d 个）：" % len(bloat))
        for _n in bloat[:10]:
            print("   ·", _n)
        print("   ⇒ `--slim` 应剔掉它们；若确实要带，先把对应规则从 BLOAT 删掉并写明理由。")
        return 7
    print(f"✅ 出包：{out}  {len(files)} 文件 / {size:.2f} MB  （注意项 {warn_total} 条，非致命）")
    print(f"   解压后顶层目录：{ZIP_TOP}/（用户看到的就是这个名字）")
    print(f"   出厂初始状态断言：通过（{len(rel_names)} 个条目全在顶层下 · 无运行期数据 · 无个人痕迹 · 必需文件齐）")
    print(f"   包内膨胀断言：通过（没带用户用不到的包；规则表 BLOAT 共 {len(BLOAT)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
