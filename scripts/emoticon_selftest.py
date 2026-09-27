#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表情包**离线解密**判据。

背景：微信 4.x 的表情在库里是加密数据、驱动库只认 3/34/43/49 ⇒ 47 号动画表情一直下不来，
之前只能"截最新一条消息的图"兜（还必须是最新那条、还得窗口可见）。现在：
    md5 → 本机表情文件 → `AES-128-CBC(key=IV=md5(f"{seed}{wxid}EMOTICON")[:16])` → 原图直出。

判据（全部离线、不需要微信在跑、不碰任何真账号数据）：
  ① 派生与解密：`derive_key` 稳定 16 字节；自己加密再解密能还原（PKCS7 去填充正确）；
  ② 魔数识别：GIF8 / PNG / JPEG / wxgf 四种都认得；
  ③ 按 md5 找文件：收藏目录优先、按月缓存兜底；
  ④ **key 缓存要校验**：缓存里的 key 必须先拿本地表情文件首块验证过才复用（校验不过就得重扫）；
  ⑤ 明文 → 视觉能看的图：PNG/JPEG 直出、GIF 动图取首帧、认不出返回空串；
  ⑥ 接线：工具层**先离线解原图、失败才退回截图**；提示词同步（不再写"只能截图"）。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import emoticon as em # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


def aes_cbc_enc(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad = 16 - (len(data) % 16)
    data = data + bytes([pad]) * pad
    e = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    return e.update(data) + e.finalize()


print("── A. 派生与解密（key=IV，PKCS7）──")
k1 = em.derive_key("123456789", "wxid_abc")
k2 = em.derive_key("123456789", "wxid_abc")
ok("派生稳定且 16 字节（AES-128）", k1 == k2 and len(k1) == 16, k1.hex())
ok("换 seed / 换 wxid 结果都变", em.derive_key("123456788", "wxid_abc") != k1
   and em.derive_key("123456789", "wxid_abd") != k1)
plain = b"GIF89a" + bytes(range(64))
ok("自己加密再解密能还原（含去填充）", em.decrypt_bytes(k1, aes_cbc_enc(k1, plain)) == plain)
ok("空数据不炸", em.decrypt_bytes(k1, b"") == b"")
ok("坏 key 解出来不是原文（阴性对照）", em.decrypt_bytes(em.derive_key("1", "x"), aes_cbc_enc(k1, plain)) != plain)

print("── B. 魔数识别 ──")
for head, want in ((b"GIF89a", "gif"), (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff\xe0", "jpg"),
                   (b"wxgf\x12\x00", "wxgf"), (b"hello", "")):
    ok("sniff(%r) → %r" % (head[:5], want), em.sniff(head) == want)

print("── C. 按 md5 找文件：收藏优先、按月缓存兜底 ──")
tmp = tempfile.mkdtemp(prefix="emoticontest")
acct = os.path.join(tmp, "wxid_test_abcd")
md5 = "a" * 32
p_persist = os.path.join(acct, "business", "emoticon", "Persist", md5[:2], md5)
os.makedirs(os.path.dirname(p_persist), exist_ok=True)
with open(p_persist, "wb") as fh:
    fh.write(b"x" * 64)
ok("收藏目录命中", em.find_sticker_file(md5, acct=acct) == p_persist)
os.remove(p_persist)
p_cache = os.path.join(acct, "cache", "2026-09", "Emoticon", md5[:2], md5)
os.makedirs(os.path.dirname(p_cache), exist_ok=True)
with open(p_cache, "wb") as fh:
    fh.write(b"y" * 64)
ok("收藏没有时按月缓存兜底", em.find_sticker_file(md5, acct=acct) == p_cache)
ok("md5 长度不对 ⇒ 空串（不乱找）", em.find_sticker_file("abc", acct=acct) == "")
_fake = type("D2", (), {"account_dir": acct})()
ok("账号目录名去掉尾部 _abcd ⇒ wxid", em.account_wxid(_fake) == "wxid_test", em.account_wxid(_fake))
ok("尾注别的写法也照样剥掉（如 _e7c2）",
   em.account_wxid(type("D3", (), {"account_dir": acct.replace("_abcd", "_e7c2")})()) == "wxid_test")

print("── D. key 缓存必须**校验过**才复用（安全关键）──")
key = em.derive_key("987654321", "wxid_test")
sample = os.path.join(acct, "business", "emoticon", "Persist", "zz", "z" * 32)
os.makedirs(os.path.dirname(sample), exist_ok=True)
with open(sample, "wb") as fh:
    fh.write(aes_cbc_enc(key, b"GIF89a" + b"\x00" * 32)) # 首块解开就是 GIF8
ok("verify_key 对正确的 key 判 True", em.verify_key(key, sample) is True)
ok("verify_key 对错误的 key 判 False", em.verify_key(b"\x00" * 16, sample) is False)

# ⛔ 判据**不写产品** `data/emoticon_key.json`。把 key 文件指到临时目录再跑同一段
#   逻辑（产品默认行为不变：`_key_file()` 本身没动，只是这里打桩；下面的 backup/restore 照旧）。
real_keyfile = os.path.join(tempfile.mkdtemp(prefix="emokey-"), "emoticon_key.json")
em._key_file = lambda: real_keyfile
backup = None
if os.path.exists(real_keyfile):
    backup = io.open(real_keyfile, encoding="utf-8").read()
try:
    os.makedirs(os.path.dirname(real_keyfile), exist_ok=True)
    with open(real_keyfile, "w", encoding="utf-8") as fh:
        json.dump({"wxid": "wxid_test", "seed": "987654321", "key": key.hex()}, fh)
    em._AUTO_DB["db"] = type("D", (), {"account_dir": acct})() # 假账号目录
    ok("缓存 key 通过首块校验 ⇒ 直接复用（不扫内存）",
       em.get_key(db=em._AUTO_DB["db"], sample=sample, allow_scan=False) == key)
    with open(real_keyfile, "w", encoding="utf-8") as fh:
        json.dump({"wxid": "wxid_test", "seed": "987654321", "key": ("00" * 16)}, fh)
    ok("缓存 key 校验不过 ⇒ 不复用（allow_scan=False 时返回空，交给调用方退回截图）",
       em.get_key(db=em._AUTO_DB["db"], sample=sample, allow_scan=False) == b"")
finally:
    em._AUTO_DB["db"] = None
    if backup is not None:
        io.open(real_keyfile, "w", encoding="utf-8").write(backup)
    elif os.path.exists(real_keyfile):
        os.remove(real_keyfile)

print("── E. 明文 → 视觉能看的图 ──")
out = os.path.join(tmp, "out")
p = em.to_viewable(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, out, "a")
ok("PNG 直出", p.endswith(".png") and os.path.exists(p))
p2 = em.to_viewable(b"\xff\xd8\xff\xe0" + b"\x00" * 32, out, "b")
ok("JPEG 直出", p2.endswith(".jpg") and os.path.exists(p2))
ok("认不出的明文 ⇒ 空串（不硬写文件）", em.to_viewable(b"????", out, "c") == "")

print("── F. 接线：工具层先离线解、失败才截图；提示词同步 ──")
_T = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
_W = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_P = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
ok("wechat 有 decode_emoji 入口", "def decode_emoji(" in _W)
ok("工具层先调 decode_emoji，再退回截图",
   _T.index("ctx[\"wechat\"].decode_emoji(") < _T.index("capture_newest_message_image(\n                            ctx"))
ok("工具描述改成『默认离线解原图』", "默认**离线解原图**" in _T)
ok("提示词不再写『只能截图』", "解密成原图" in _P and "走的是屏幕截图" not in _P)
_E = io.open(os.path.join(ROOT, "agent", "emoticon.py"), encoding="utf-8").read()
ok("模块头写明只读边界（不写微信任何东西）", "只读" in _E and "不写微信任何东西" in _E)
ok("算法出处写明（开源项目 + MIT）", "Wechat-Emoticon-Parser" in _E and "MIT" in _E)

print("── G. 动图：`wxgf` → 真动图 GIF；GIF 不再被降成首帧 ──")
import subprocess # noqa: E402

from PIL import Image as _Img # noqa: E402


def _nf(p): # noqa: ANN001, ANN202
    with _Img.open(p) as im:
        return int(getattr(im, "n_frames", 1))


_g = os.path.join(tmp, "a.gif")
# ⚠️ 四帧必须**肉眼可分**：内容一样的帧会被 PIL 合并成单帧，分母守卫就白设了。
_fr = [_Img.new("RGB", (32, 32), color=c) for c in
       ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))]
_fr[0].save(_g, save_all=True, append_images=_fr[1:], duration=120, loop=0)
ok("分母守卫：合成的 GIF 本身就是 4 帧（不然下面两条会因单帧全绿）", _nf(_g) == 4, str(_nf(_g)))
_plain_gif = io.open(_g, "rb").read()
_an = em.to_animated(_plain_gif, out, "gif_an")
ok("to_animated：GIF 原样落盘且**仍是动图**（收藏夹要的是会动的那个）",
   _an.endswith(".gif") and _nf(_an) == 4, "%s 帧=%s" % (_an, _nf(_an) if _an else "-"))
ok("is_animated_file 认得出动图", em.is_animated_file(_an) is True)
ok("is_animated_file 对静态图判 False（不把静的说成动的）",
   em.is_animated_file(em.to_viewable(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, out, "st")) is False)
_v = em.to_viewable(_plain_gif, out, "gif_vis")
ok("to_viewable 仍按老口径给**首帧静态图**（视觉接口多数不吃 GIF）",
   _v.endswith("_first.png") and _nf(_v) == 1, _v)

_exe = em.ffmpeg_exe()
ok("ffmpeg 找得到（PATH 优先，其次产品自带的 imageio-ffmpeg）", bool(_exe), _exe)
ok("垃圾输入 ⇒ wxgf_to_gif 返回空串（不倒出半个文件）",
   em.wxgf_to_gif(b"wxgf\x00\x01\x02", os.path.join(tmp, "bad.gif")) == "")
ok("wxgf_stream 从第一个起始码切起（前面是容器头）",
   em.wxgf_stream(b"wxgf\x10\x00AAAA\x00\x00\x00\x01rest") == b"\x00\x00\x00\x01rest")

if _exe:
    # 自造样本：用 ffmpeg 自己编一段 5 帧 HEVC 当"wxgf 的内容"——
    # 这样这条判据**不依赖用户本机的表情文件**，也不碰他的微信数据。
    _hevc = os.path.join(tmp, "s.hevc")
    _r = subprocess.run([_exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                         "-i", "testsrc=size=64x64:rate=10:duration=0.5",
                         "-c:v", "libx265", "-f", "hevc", _hevc], capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if _r.returncode == 0 and os.path.exists(_hevc):
        _fake = b"wxgf" + b"\x00" * 12 + io.open(_hevc, "rb").read()
        _got = em.wxgf_to_gif(_fake, os.path.join(tmp, "w.gif"))
        ok("wxgf（HEVC 裸流）→ GIF 拿到**多帧**：这是「能解成动图」的核心证据",
           bool(_got) and _nf(_got) >= 3, "%s 帧=%s" % (_got, _nf(_got) if _got else "-"))
    else:
        print("  · 本机 ffmpeg 编不出 HEVC 样本（rc=%s）⇒ 「自造样本转码」这一条本次不计分"
              % _r.returncode)

print("── H. 动图不许被发送前的压缩毁掉 ──")
from agent import img_compress as _ic # noqa: E402

_big = os.path.join(tmp, "big.gif")
_bg = [_Img.new("RGB", (2000, 2000), color=(i * 40, 0, 0)) for i in range(3)]
_bg[0].save(_big, save_all=True, append_images=_bg[1:], duration=100, loop=0)
_p, _note = _ic.compress_if_needed(_big, out_dir=os.path.join(tmp, "cmp"))
ok("超尺寸的动图**原样发**（压缩链会抽首帧 ⇒ 动效就没了），且回执说明原因",
   _p == _big and "动图" in _note, _note)

print("── I. 接线：收藏走 animated；发送侧**动图走文件通道**（真机：微信按动画表情收）──")
ok("collect_emoji 走 sticker_image(animated=True)",
   _W.count("sticker_image(self._db, chat_id, int(local_id), animated=True)") == 1)
ok("send_image_posted：动图改放**文件**(CF_HDROP)、静态图仍放位图（两条都在，各一处）",
   _W.count("_cb.set_files([local_path])") == 1 and _W.count("_cb.set_image(local_path)") == 1
   and _W.count("_emo.is_animated_file(local_path)") == 1)
ok("回执写明这条走的是文件通道、对方收到会动的表情",
   _T.count("文件通道") >= 1 and _T.count("会动的表情") >= 1)
ok("自己发的动画表情也要登记成「自己发的」（否则机器人会回自己刚发的表情）",
   _W.count("动画表情") >= 1 and _W.count("emoticon") >= 1)

print("\n==== 表情离线解密判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
