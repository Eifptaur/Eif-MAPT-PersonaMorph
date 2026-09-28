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
