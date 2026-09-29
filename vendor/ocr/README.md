# RapidOCR 离线轮子（随包发，用户**零安装**即得高精度中文 OCR）

## 为什么放这里

调研结论（见 `docs/OCR精度-调研与改造-2026-09-29.md`）：
Windows 内置 OCR 中文约 **75~83%**，而 **RapidOCR（PaddleOCR PP-OCRv4 导 ONNX + onnxruntime）
中文约 98.7%、CPU 0.28s/图、内存 ~180MB**。用户要求"**效果至上**，包大小不是问题
（在线包 ≤200MB 即可）" ⇒ 与其让用户自己去 pip（还依赖网络），不如把轮子随包带上、首启**离线装**。

## 代价（实测）

| 轮子 | 大小 |
| --- | --- |
| `onnxruntime`（CPU） | 12.84 MB |
| `rapidocr_onnxruntime`（**含 PP-OCRv4 检测/方向/识别模型**） | 14.22 MB |
| `pyclipper` | 0.10 MB |
| `shapely` | 1.64 MB |
| **合计** | **≈ 28.8 MB** |

其余依赖（numpy / opencv / pillow）在 `offline/wheels/` 里已有；在线包带上这 4 个后约
**48 MB**（原 19.7 MB），仍远低于 200 MB 上限。

## 口径

- 装法：`agent/chat_ocr._rapid_bootstrap()` **先用本目录离线装**（`--no-index --find-links`），
  装不动才回落在线镜像；都失败 ⇒ 退回 Windows 内置 OCR（**绝不因此让功能不可用**）。
- 红线不变：RapidOCR 只是**多一个识别引擎**，不改变任何判定与安全闸门；两引擎全挂仍 fail-closed。

---

## 2026-09-29 更新：带上 **PP-OCRv6**（第二引擎换代）

**为什么换**（实测，`scripts/ocr_bench_web.py` 29 条**核过的**真值 · 6 张不同应用的界面）：
v4(mobile) 与 v6 **精度持平**（都 96.6% 量级），但 **v6 快 1.7×**（19.8s vs 32.9s / 6 张大图）；
微信真机列表两者都 21/21；合成极小字 v4 34/36、v6 33/36（同量级）。⇒ 换 v6（更快，精度不亏）。

**包内清单（13 个文件 ≈ 56MB）**：

| 轮子 | 大小 | 作用 |
| --- | --- | --- |
| `rapidocr-3.9.2` | **26.01 MB** | **v6 引擎，自带 `PP-OCRv6_det_small.onnx` + `PP-OCRv6_rec_small.onnx`**（不用联网拉模型） |
| `rapidocr_onnxruntime-1.4.4` | 14.22 MB | v4 兜底（依赖少，老环境更稳） |
| `onnxruntime-1.23.2` | 12.84 MB | 推理后端 |
| `shapely` / `pyclipper` / `pyyaml` / `requests` / `tqdm` / `omegaconf` / `six` / `colorlog` / `antlr4-python3-runtime` | ≈ 2.3 MB | v6 的依赖链 |

⛔ **两条踩过的坑（都写进代码注释了）**：
1. **必须整栈一起装**：`rapidocr` 不自带 `omegaconf`/`antlr4` ⇒ 只装它一个＝"装上了但 import 就炸"的假成功；
2. **两个 find-links 都要给**：还依赖 `opencv_python`/`numpy`/`Pillow`，它们在 `offline/wheels/`；
   只给 `vendor/ocr` 会在 `--no-index` 下解析不到 opencv 直接失败。
3. `antlr4-python3-runtime==4.9.3` 在 PyPI **只有源码包**（omegaconf 卡 `==4.9.*`）⇒ 随包带的是 `.tar.gz`，
   安装时用 `--no-build-isolation`（本机 setuptools 能直接构建，不需要联网）。

**体积账**：在线包 19.7MB → 约 **75MB**（含这 56MB 轮子），仍远低于 200MB 上限。
