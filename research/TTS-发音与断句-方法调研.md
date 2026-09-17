# TTS 发音与断句：途径调研（群相 · 2026-09-17）

> 起因：用户听出「**行行行**」被念成了「行行**hang**行」，并指出它**不是三个字一顿**，而是「**语气偏快的连读，像那种拿你没办法的感觉**」。
> 我此前那条"连续同字自动插逗号"的做法**被证否**（插逗号＝把连读读成一字一顿，语气全丢）。本文只收**可落地的途径**，每条都带来源与"在我们这条链上能不能用"。

## 0. 先把问题拆成两半（别混）

| 症状 | 真实原因 | 该用什么手段 |
|---|---|---|
| 「行行**hang**行」＝中间那个字**读音错** | 文本前端的**分词/多音字**判断错（行 xíng/háng 是多音字，被当成了 háng） | **音素级标注**或**同音字替换**（不是标点） |
| 「三个字一顿」＝**语气/韵律**丢了 | 我插的逗号在引擎里就是**停顿**（每个「，」≈ 数百毫秒静音） | **改回原文（不插标点）** + **语速/参考音频**决定语气 |

结论口径：**读音问题用"标注/替换"解，语气问题用"语速/参考音频/停顿**解**，两者都不该用标点去糊。

## 1. edge 档（我们当前的默认）：**没有音素级控制，这是官方定论**

- 维护者（rany2）在 edge-tts 仓库里明确：**自定义 XML/SSML 的功能被删掉了，而且不会再回来**——Edge 的接口会**只接受 Edge 浏览器自己会生成的那种 XML**；用了别处（Azure 付费档）的特性时**不报错、直接返回空音频**。原话见 [rany2/edge-tts issue #35](https://github.com/rany2/edge-tts/issues/35)：
  > "This function previously existed but was removed. It doesn't work properly due to Microsoft closing up the API… If you send XML that uses features not present in Edge browser the API will error out now." / "it just returned nothing"
- ⇒ edge 档**只有** `rate / pitch / volume` 三个整句级的旋钮（我们已接 `voice_reply.rate`），**拿不到 `phoneme`**。
- 推论（可直接落地）：edge 档要改读音，只剩 **同音字替换** 这条路（见 §4）。

## 2. Azure 语音（付费档）：SSML `<phoneme>` 是正规途径

- 官方文档：[语音合成标记语言 (SSML) 的发音 · Azure AI 语音](https://docs.azure.cn/zh-cn/ai-services/speech-service/speech-synthesis-markup-pronunciation)（内含 zh-* 的 **sapi 音标集**：一个字＝「拼音 + 空格 + 声调数字」，如 `花` → `hua 1`）。用法形如
  `<phoneme alphabet="sapi" ph="xing 2">行</phoneme>`。
- ⚠️ 已知坑：[Microsoft Q&A #5965930](https://learn.microsoft.com/en-ca/answers/questions/5965930/zh-tw-sapi-phoneme-docs-show-pinyin-examples-but-t) 实测 **zh-TW 的 sapi phoneme 按文档写会 HTTP 400**（文档与接口不一致），**zh-CN 要以本机实测为准**。
- 对我们的意义：**要密钥、要出网**，与「绝大部分不出网」的默认路径冲突 ⇒ 只当**可选档**（用户自带 Azure key 时）。

## 3. **本机实测（本篇最硬的一条）**：Windows 内置中文声音能用 `<phoneme>`，但**只有 .NET 那条路认**

同一台机器、同一个声音（`Microsoft Huihui Desktop`，zh-CN），同一段可见文本「银行」，**只换 `ph`**，比对波形差异（脚本 `_scratch/sapi_ssml_probe.py`）：

| 引擎入口 | `ph="hang 2"` vs `ph="xing 2"` | 判决 |
|---|---|---|
| .NET `System.Speech.SpeechSynthesizer.SpeakSsml` | 波形 **34.3% 采样不同**（`plain` 与 `hang` 只差 17.7%＝默认本来就念 háng，正好当对照） | ✅ **音标被采纳** |
| `SAPI.SpVoice` + `SVSFIsXML(=2)`（**我们产品现在的链路**） | 两份 WAV **逐字节相同**（55142 B = 55142 B，0.0% 不同） | ❌ **静默忽略 phoneme** |

同一轮还量到：`<prosody rate="+25%">`、`<break time="0ms"/300ms/>`（差 85.9% 采样）在**两条入口上都生效**；`alphabet="ipa"`、`ph="xing2"`（少空格）都直接报错 ⇒ **语法必须严格是「拼音 + 空格 + 声调」+ `alphabet="sapi"`**。

⇒ 落地含义：**如果要用拼音精确纠正读音，sapi 档得改走 .NET（`powershell -Command` 或 pythonnet），而不是现在的 SpVoice**；代价＝多一个子进程（我们已有"子进程一律 `CREATE_NO_WINDOW`"的纪律）。

## 4. **同音字替换**：全社区都在用的土办法，也是 edge 档唯一的出路

- GPT-SoVITS 仓库里同类问题（「换行」被念成「huang xing」，[issue #209](https://github.com/RVC-Boss/GPT-SoVITS/issues/209)）下面，社区给的第一个办法就是这个：
  > 「你先写成**换航**，我都是这么弄的」
  > 「多音字最好都用**同音字替换**」
- 优点：**零依赖、对任何引擎都有效**（edge / sapi / 用户自带的 HTTP 服务通吃）；缺点：要有人写那张表（可以我们预置常见条目）。
- 对本案的直接写法：`行行行` 的读音是 xíng → 用同音字 `形` ⇒ **`行行行=形形形`**；引擎自然快读三个 xíng，不停顿。
- 我们已有的 `voice_reply.pronounce`（一行一条 `原文=念法`）**正好是这张表的形状**——要改的只是**默认行为**：**不许自动往原文里插标点**，只按用户表替换。

## 5. 拼音/音素标注：本地模型里已经有一等公民

| 引擎 | 标注语法 | 来源 |
|---|---|---|
| **CosyVoice3**（阿里，可本地跑） | 用**拼音/音素括号**标注，如 `[h][ào]` 这类逐音节写法 | [CosyVoice3 多音字与音素标注教程](https://blog.csdn.net/weixin_33308579/article/details/159147383)、[`[h][ào]` 写法说明](https://blog.csdn.net/weixin_33245447/article/details/156495097) |
| **GPT-SoVITS**（用户本机已有） | 前端＝**g2pw** 多音字消歧；社区 PR 支持「中文后跟英文括号的 TONE3 拼音」，如 `角(jue2)色` | [PR #1728 支持自定义读音](https://github.com/RVC-Boss/GPT-SoVITS/pull/1728)，原话「实现逻辑：…custom_pinyin…用获取到的自定义拼音替换 g2pw 模型推理出的拼音」 |
| **ChatTTS** | 文本里插控制符：`[uv_break]`＝停顿、`[laugh]`＝笑；`skip_refine_text=True` 可**强制按自己标注合成**；`params_infer_code={'prompt':'[speed_5]'}` 调语速 | [pyVideoTrans · ChatTTS FAQ](https://pyvideotrans.com/chattts-faq) |

**为什么"语气"这半只能靠这些本地模型解**：他想要的「拿你没办法」是**情绪 + 快**，而情绪在语音克隆类模型里主要来自**参考音频**（reference audio 决定音色与说话方式）；edge 这种通用在线声音**给不了**特定情绪。⇒ 若要把语气做对，**把语音回复的音源切到用户本机的 GPT-SoVITS**（我们已接 http 通道 + RVC 变声）是最短路径。

## 6. 我们的落地建议（按性价比排序）

1. **立刻改（必修）**：**撤掉"连续同字自动插逗号"**（v2.1.27 引入，属我判断错误）；默认**不改用户一个字**。逗号只用于"真没标点的长串"（那是另一件事：一口气念 40 字）。
2. **立刻做（低风险）**：`念法表` 作为**唯一改写入口**，并在控制台把最实用的写法写明：
   - 同音字替换（通用）：`行行行=形形形`
   - 拼音标注（仅"支持音素的档"生效，界面如实标注）：`行=拼音:xing2`
3. **中期（可选，要用户点头）**：**音源换成他本机的 GPT-SoVITS**——多音字有 g2pw，语气由参考音频带出；需要他挑一段"无奈连读"的参考音频。
4. **sapi 档增强（成本低）**：要拼音就**改走 .NET `SpeakSsml`**（§3 已实测认音标），语法 `alphabet="sapi" ph="xing 2"`，并顺手用 `<break>`/`<prosody>` 控停顿与语速。

## 参考

- [rany2/edge-tts issue #35 — Pronunciation and other finer control（自定义 SSML 已被移除且不会恢复）](https://github.com/rany2/edge-tts/issues/35)
- [Azure AI 语音 · SSML 的发音（zh-* 的 sapi 音标集＝拼音 + 声调数字）](https://docs.azure.cn/zh-cn/ai-services/speech-service/speech-synthesis-markup-pronunciation)
- [Microsoft Q&A #5965930（zh-TW sapi phoneme 文档与接口不一致，实测 400）](https://learn.microsoft.com/en-ca/answers/questions/5965930/zh-tw-sapi-phoneme-docs-show-pinyin-examples-but-t)
- [RVC-Boss/GPT-SoVITS issue #209（换行→huang xing；社区答案：同音字替换）](https://github.com/RVC-Boss/GPT-SoVITS/issues/209)
- [RVC-Boss/GPT-SoVITS PR #1728（自定义读音 `角(jue2)色`，未合并）](https://github.com/RVC-Boss/GPT-SoVITS/pull/1728)
- [CosyVoice3 多音字与音素标注教程](https://blog.csdn.net/weixin_33308579/article/details/159147383)
- [pyVideoTrans · ChatTTS 常见问题（`[uv_break]` / `skip_refine_text` / 语速）](https://pyvideotrans.com/chattts-faq)
- 本机实测脚本与产物：`_scratch/sapi_ssml_probe.py`、`_scratch/s2_*.wav`、`_scratch/sp_*.wav`
