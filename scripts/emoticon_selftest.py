#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表情包**离线解密**判据（2026-09-18 落地，算法来自开源项目 CN-Grace/Wechat-Emoticon-Parser）。

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

from agent import emoticon as em          # noqa: E402

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
    fh.write(aes_cbc_enc(key, b"GIF89a" + b"\x00" * 32))     # 首块解开就是 GIF8
ok("verify_key 对正确的 key 判 True", em.verify_key(key, sample) is True)
ok("verify_key 对错误的 key 判 False", em.verify_key(b"\x00" * 16, sample) is False)

# ⛔ V-R7-4：判据**不写产品** `data/emoticon_key.json`。把 key 文件指到临时目录再跑同一段
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
    em._AUTO_DB["db"] = type("D", (), {"account_dir": acct})()      # 假账号目录
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

print("\n==== 表情离线解密判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
