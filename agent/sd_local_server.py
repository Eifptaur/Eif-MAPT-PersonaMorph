# -*- coding: utf-8 -*-
"""**轻量本地生图服务**（随产品发货的 A1111 兼容后端）—— 用户不需要装 ComfyUI/A1111。

为什么要有它：群相生图模块原生支持 `a1111` 协议（探测 `GET /sdapi/v1/sd-models`、
生成 `POST /sdapi/v1/txt2img` → `{"images":[base64]}`），但 ComfyUI/A1111 动辄 3~8GB 下载、
还要用户自己配工作流。这个服务用 `diffusers` + 一个本地模型文件把协议补齐：
**装完即用、不出网、用户零配置**。

由 `agent/sd_local.py` 负责起停（子进程 + pid 文件）；本文件也能单独跑：
    runtime\\python\\python.exe agent\\sd_local_server.py [端口]
环境变量：`SD_MODEL`＝模型文件；`SD_STEPS`＝默认步数（SDXL-Turbo 1~4）；`SD_PORT`＝端口。
"""
import base64
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")     # diffusers 取小配置走国内镜像

DEFAULT_MODEL = os.path.join("data", "sd_model", "sd_xl_turbo_1.0_fp16.safetensors")
MODEL = os.environ.get("SD_MODEL") or DEFAULT_MODEL
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("SD_PORT") or 7860)
DEFAULT_STEPS = int(os.environ.get("SD_STEPS") or 4)
#: 档位参数（2026-09-17 加多档）：
#:   SD_GUIDANCE＝引导强度（SDXL-Turbo 必须 0.0；配 4 步加速 LoRA 时用 1.5~2.0）
#:   SD_LORA＝4 步加速 LoRA 的本地路径（画质档用它把 SDXL 精调压到 4 步）
#:   SD_SPACING＝采样时间步间距（配 LoRA 时用 trailing，这是加速件官方推荐）
GUIDANCE = float(os.environ.get("SD_GUIDANCE") or 0.0)
LORA = os.environ.get("SD_LORA") or ""
SPACING = os.environ.get("SD_SPACING") or ("trailing" if LORA else "leading")
_lock = threading.Lock()
_pipe = None
_state = {"loading": False, "ready": False, "error": "", "model": os.path.basename(MODEL), "device": ""}


def get_pipe():
    global _pipe
    if _pipe is not None:
        return _pipe
    with _lock:
        if _pipe is not None:
            return _pipe
        _state["loading"] = True
        t0 = time.time()
        import torch
        from diffusers import StableDiffusionXLPipeline
        print("加载模型 %s …" % MODEL, flush=True)
        p = StableDiffusionXLPipeline.from_single_file(MODEL, torch_dtype=torch.float16,
                                                      use_safetensors=True, safety_checker=None)
        try:
            p.set_progress_bar_config(disable=True)
        except Exception:
            pass
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        p = p.to(dev)
        # 显存纪律（2026-09-17 实测教训）：本机 12GB 卡上 SDXL fp16 跑 1024² 时显存 11.87/12.23 GB
        #   几乎占满、GPU 利用率只有 2% ⇒ 已经在往内存里换页，单张从 24 秒掉到 186 秒。
        #   切片是"少占显存换一点速度"，慢十倍的时候这点代价完全值。
        for fn in ("enable_vae_slicing", "enable_attention_slicing", "enable_vae_tiling"):
            try:
                getattr(p, fn)()
            except Exception:
                pass
        try:
            torch.backends.cuda.matmul.allow_tf32 = True
        except Exception:
            pass
        if LORA and os.path.exists(LORA):                # 画质档：挂 4 步加速件（SDXL 精调也能 4 步出图）
            try:
                p.load_lora_weights(LORA, adapter_name="lightning")
                p.set_adapters(["lightning"], adapter_weights=[1.0])
                try:
                    p.fuse_lora()
                except Exception:
                    pass
                print("已挂 4 步加速件：%s" % os.path.basename(LORA), flush=True)
            except Exception as e:
                print("加速件没挂上（按原步数慢跑）：%s" % str(e)[:120], flush=True)
        _pipe = p
        _state.update({"ready": True, "loading": False, "device": dev,
                       "load_seconds": round(time.time() - t0, 1)})
        print("模型就绪（%s，用时 %.1f 秒）" % (dev, time.time() - t0), flush=True)
        return _pipe


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _deny(self):
        """统一门禁（V-R3-8）：`Host` 必须是回环 + 必须带本机口令。**每条路由都过这一关**。

        口径与实现见 `agent/local_guard.py`（与控制台共用同一份，别再各写一套）。
        客户端（`agent/image_gen.py` / `agent/sd_local.py`）对回环地址会自动带 `X-PM-Token`。
        """
        try:
            import local_guard as lg
        except Exception:                                   # 直接以脚本方式跑（cwd=ROOT）
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import local_guard as lg
        _ok, code, why = lg.check(self, PORT)
        if not _ok:
            self._send({"detail": why}, code)
            return False
        return True

    def do_GET(self):
        if not self._deny():
            return
        p = self.path.split("?")[0]
        if p.startswith("/sdapi/v1/sd-models"):
            return self._send([{"title": "sdxl-turbo (群相 本地轻量后端)", "model_name": os.path.basename(MODEL)}])
        if p.startswith("/sdapi/v1/progress"):
            return self._send({"progress": 1.0 if _state["ready"] else 0.0,
                               "state": {"sampling_step": 0, "sampling_steps": 0}})
        if p in ("/", "/internal/ping") or p.startswith("/sdapi/v1/options"):
            return self._send({"ok": True, **_state})
        return self._send({"detail": "not found"}, 404)

    def do_POST(self):
        if not self._deny():
            return
        if not self.path.split("?")[0].startswith("/sdapi/v1/txt2img"):
            return self._send({"detail": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception as e:
            return self._send({"detail": "坏请求：%s" % e}, 400)
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return self._send({"detail": "prompt 为空"}, 400)
        w = int(body.get("width") or 1024)
        h = int(body.get("height") or 1024)
        steps = max(1, min(8, int(body.get("steps") or DEFAULT_STEPS)))
        bs = max(1, min(4, int(body.get("batch_size") or 1)))
        neg = str(body.get("negative_prompt") or "")
        t0 = time.time()
        try:
            kw = dict(prompt=prompt, num_inference_steps=steps, guidance_scale=GUIDANCE,
                      width=w, height=h, num_images_per_prompt=bs)
            if SPACING:                                  # 加速件用 trailing（官方推荐）
                kw["timestep_spacing"] = SPACING
            if neg:
                kw["negative_prompt"] = neg
            out = get_pipe()(**kw).images
        except Exception as e:
            _state["error"] = str(e)[:200]
            print("生成失败：%s" % e, flush=True)
            return self._send({"detail": "生成失败：%s" % str(e)[:200]}, 500)
        imgs = []
        for im in out:
            buf = io.BytesIO()
            im.save(buf, "PNG")
            imgs.append(base64.b64encode(buf.getvalue()).decode("ascii"))
        try:                                         # 出完图把显存还给系统（别一直攥着不放到爆）
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        print("出图 %d 张 %dx%d steps=%d 用时 %.1f 秒 ｜ %s" % (len(imgs), w, h, steps, time.time() - t0, prompt[:40]), flush=True)
        return self._send({"images": imgs, "parameters": {"steps": steps, "width": w, "height": h}})


if __name__ == "__main__":
    if not os.path.exists(MODEL):
        print("模型文件不存在：%s" % MODEL, flush=True)
        sys.exit(2)
    try:
        import local_guard as _lg
    except Exception:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import local_guard as _lg
    _tok = _lg.token()
    if not _tok:
        print("本机口令建立失败（logs 目录写不了）—— 拒绝起一个没有门禁的监听面", flush=True)
        sys.exit(3)
    print("群相本地生图服务：http://127.0.0.1:%d（模型 %s；本机口令见 %s）"
          % (PORT, os.path.basename(MODEL), _lg.token_path()), flush=True)
    if os.environ.get("SD_PRELOAD", "1") == "1":
        try:
            get_pipe()
        except Exception as e:
            print("预加载失败（仍会启动，首次请求再试）：%s" % e, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
