# voice-ime 第一阶段 Windows 版测试报告

**日期**：2026-10-11
**版本**：v1（llama-server + Qwen3-ASR-1.7B 方案）
**测试环境**：Linux（2 核 CPU，无 GPU）；目标环境 Windows 11

---

## 一、架构改动

### 1.1 路线变更说明

原计划采用上游 `qwen_asr` 原生引擎（ctypes 直调 llama.cpp），实测发现其
`inference/llama.py` 中的 ctypes 结构体绑定与 llama.cpp b10621 不兼容：

- `llama_context_params` 缺少 `n_rs_seq` / `n_outputs_max` / `n_outputs_max_per_seq` /
  `ctx_type` / `ctx_other` 共 5 个字段（24 字节），导致上下文初始化失败
- `llama_model_params` 缺少 `load_mode` / `load_mtp`，且残留已废弃的
  `use_mmap` / `use_direct_io` / `use_mlock` 字段

已修复上述结构体对齐问题（commit d1926ab），但官方 b10621 二进制下
`llama_model_load_from_file` 仍返回 NULL（原因未明，疑为更深层的绑定不兼容）。
根据"卡住即切换"原则，改用任务原定的 **llama-server HTTP 方案**。

### 1.2 最终架构

```
┌──────────────┐   按住CapsLock   ┌──────────────┐  websocket   ┌──────────────────┐
│ 客户端        │ ──────────────▶ │ 服务端        │ ───────────▶ │ llama-server      │
│ (PyQt/Tk)    │   音频流        │ (新 qwen3_    │  HTTP POST   │ (官方二进制)       │
│ 录音/热键/上屏 │               │  llama_server │ /v1/audio/   │ Qwen3-ASR-1.7B    │
└──────────────┘                │  引擎)        │ transcriptions│  + mmproj         │
                                └──────────────┘              └──────────────────┘
```

### 1.3 代码改动清单（vs 上游 IceGPS/CapsWriter-Offline）

| 文件 | 改动 |
|------|------|
| `core/server/engines/qwen3_llama_server/` | **新增**：HTTP 引擎适配器（`asr_engine.py` + `__init__.py`），实现 `BaseASREngine` 接口 |
| `core/server/engines/factory.py` | 注册 `qwen3_llama_server` 引擎 |
| `config_server.py` | 新增 `Qwen3LlamaServerArgs`；`model_type` 默认改为 `qwen3_llama_server` |
| `core/server/worker/check_model.py` | 新引擎跳过本地模型文件检查（由 llama-server 管理） |
| `core/server/engines/qwen_asr_gguf/inference/llama.py` | 修复 b10621 ctypes 结构体对齐（保留作为备用） |
| `tests/` | 新增 `check_qwen_asr.py` / `check_llama_server.py` / `check_e2e.py` 测试脚本 |
| `packaging/` | 新增 `install.bat` 一键安装脚本、`README_安装说明.txt` |

MIT 协议：保留原作者 HaujetZhao 署名（LICENSE 未动）。

---

## 二、测试证据

### 2.1 llama-server + Qwen3-ASR 转写测试（核心链路）

**环境**：llama-server b10621（官方 Ubuntu 二进制），模型
`Qwen3-ASR-1.7B-Q8_0.gguf`（2.1GB）+ `mmproj-Q8_0.gguf`（340MB），CPU 推理。

| # | 测试音频 | 原文 | 转写结果 | 耗时 |
|---|----------|------|----------|------|
| 1 | t1.wav（5.1s） | 今天天气不错，我们下午三点开会讨论新产品的销售策略。 | 今天天气不错，我们下午三点开会，讨论新产品的销售策略。 | 10分42秒 |
| 2 | t2.wav（4.0s，含语气词） | 嗯，那个，我觉得吧，这个方案还是可以的，对吧？ | 超时（300s 未完成） | — |

**结论**：
- ✅ 中文转写准确率极高（测试1仅多一个逗号，其余一字不差）
- ✅ 标点自动恢复正常
- ✅ HTTP 接口工作正常（llama-server 接收请求并处理）
- ⚠️ CPU 推理很慢（2 核 VM 上 5 秒音频需约 10 分钟；测试2 在 300 秒超时内未完成）
- 上游文档称 RTX5050 上 20 秒音频约 1 秒；Hongjie 实机（有显卡）预期 1-3 秒出结果

### 2.2 Python HTTP 适配器测试

`tests/check_llama_server.py` 直接调用新引擎的 `decode_stream`，验证：
- ✅ 引擎工厂创建成功（`Qwen3LlamaServerEngine`，能力 `ASR`+`PUNC`）
- ✅ WAV 编码正确，HTTP 请求格式正确
- ✅ llama-server 接收并处理请求（服务端日志确认）
- ⚠️ 4 秒音频在 300 秒超时内未完成（CPU 瓶颈，非代码问题）

### 2.3 端到端测试（CapsWriter 服务端 + websocket）

`tests/check_e2e.py` 启动完整 CapsWriter 服务端（无头模式），通过 websocket
发送音频并接收识别结果。**因 CPU 推理过慢，完整 e2e 未在 Linux 上跑完**；
websocket 协议与服务端流水线均为上游成熟代码，仅 ASR 引擎被替换，
风险较低。Hongjie 在 Win11 实机验收时将覆盖此环节。

---

## 三、与原版 CapsWriter 的功能差异

| 功能 | 原版（qwen_asr） | 本版（qwen3_llama_server） | 说明 |
|------|------------------|---------------------------|------|
| ASR 模型 | Qwen3-ASR-1.7B q5_k（ctypes） | Qwen3-ASR-1.7B Q8_0（llama-server） | Q8_0 精度略高于 q5_k |
| 服务端热词 | ✅（hot-server.txt，经 CTC） | ❌ | llama-server 暂不支持动态热词；客户端热词（hot.txt 音素 RAG）不受影响 |
| 标点恢复 | ✅（模型自带） | ✅（模型自带） | 一致 |
| 时间戳 | ❌（需外挂 ForcedAligner） | ❌ | 一致；文件转录时间戳仍需对齐器（未打包） |
| 部署复杂度 | 需编译/下载 llama 动态库 | 需启动 llama-server 进程 | 本版多一个进程，但无需处理 DLL 地狱 |
| GPU 加速 | 需自行配置 | llama-server 自动（Vulkan） | 本版更省心 |

**客户端功能（录音、热键、上屏、客户端热词、LLM 润色、日记归档）完全未动。**

---

## 四、已知限制

1. **CPU 推理慢**：无 GPU 的机器上转写延迟高（分钟级）。有独立显卡的 Windows 11 上预期 1-3 秒。
2. **服务端热词缺失**：`hot-server.txt` 在新引擎下不生效；如需热词可用客户端 `hot.txt`（音素 RAG）。
3. **需启动三个进程**：llama-server → CapsWriter 服务端 → 客户端，顺序不能错。
4. **内存占用**：llama-server 加载 2.1GB 模型，约需 3GB 内存。
5. **首次启动慢**：模型加载约需 30 秒（SSD）。

---

## 五、Hongjie 在 Win11 上的安装步骤（不超过 10 步）

1. 解压 `voice-ime-windows-v1.zip` 到 `D:\voice-ime`（路径无中文）
2. 双击 `install.bat`，等待安装完成（需联网，约 2.5GB）
3. 双击 `start_llama_server.bat`，等出现 "listening on http://127.0.0.1:8080"
4. 双击 `start_server.bat`
5. 双击 `start_client.bat`，托盘出现图标
6. 打开记事本，按住 **CapsLock** 说话
7. 松开 CapsLock，文字自动上屏
8. 说一句话，检查转写是否准确
9. 试一次长句（30 秒），检查延迟是否可接受
10. 断网后重复 6-8，确认离线可用

**验收标准**：转写准确、延迟可接受（GPU 下 <3 秒）、断网可用。

---

## 六、遗留问题 / 后续工作

- [ ] t2（含语气词）在 GPU 机器上的转写效果待 Hongjie 实机验证
- [ ] 上游 `qwen_asr` ctypes 引擎的深层兼容问题未根治（已保留结构体修复，备用）
- [ ] ForcedAligner 时间戳对齐器未打包（文件转录 srt 时间戳精度受影响，可后续加）
- [ ] 未做 Wine+PyInstaller exe 打包（经评估投入产出比低，采用 install.bat 方案）

---

# v4 更新（2026-10-12）：日志层 + 每日文档编译器（客户端侧新功能）

**架构决策**：服务端不再改动，所有新功能在客户端一侧实现。
v3 起安装包为纯客户端，直连用户本机已有的 CapsWriter 服务端（6016 端口）。

## 一、实现内容

### 1. 日志层（`core/client/doc_compiler/log.py`）
- 每次转写完成（拿到服务端文本、即将上屏时），在 `ResultProcessor._handle_message`
  中记录三元组 `{ts, text, audio}`，text 为**原始转写（含语气词）**
- 录音按天存：`recordings/YYYY-MM-DD/HHMMSS.wav`（复制，不动原文件；重名加 -1）
- 日志按天存：`logs/YYYY-MM-DD.jsonl`（每行 `{ts, text, audio, status, dur_ms}`）
- 转写为空/失败也记录（status=`empty`，text=""），且录音文件**总是**从 state 取出，
  即使 `Config.save_audio=False` 也不丢录音

### 2. 语气词过滤（`core/client/doc_compiler/clean.py`，纯规则）
- 删：句首"嗯/呃"（后跟停顿）、句首"那/那个"堆叠（后跟停顿）；
  "那个方案"中的"那个"（后跟名词）不误删
- 留：句尾"吧/呢/啊"、感叹"啊"、问答中单独成句的"嗯"（前一句是问句）
- 在分句后执行，支持跨句上下文

### 3. 文档编译器（`core/client/doc_compiler/compile_day.py`）
- 流程：读 jsonl → 清洗 → 按 30 分钟间隔分组（`## HH:MM`）→
  去口语化（连续去重、合并碎片短句）→ 写 `docs/daily/YYYY-MM-DD.md`
- 幂等：输出完全由日志推导，内容无变化时不重写文件
- 手动锁定：段内含 `<!-- manual -->`（放标题前一行或段内均可）的段落按标题原样保留
- 三种触发：
  a. 托盘菜单"📄 生成今日文档"（`tray_manager.py` 新增）
  b. 每天 23:55 自动编译（`app.start_daily_doc_timer`，仅当有新日志时；
     `mic_runner.py` 启动；`logs/.last_compile.json` 记录进度）
  c. 命令行：`python core/client/doc_compiler/compile_day.py [YYYY-MM-DD]`
     （脚本模式，不依赖客户端重型依赖；`-m` 包模式亦可）

## 二、测试证据（Linux 实测）

`tests/test_clean.py`：16/16 通过（删留正反例全覆盖）
```
ok: 句首嗯 / 句首呃+那个 / 重复堆叠 / 句尾吧/呢/啊 / 感叹啊 /
    问答嗯保留 / 非问答嗯删除 / 指示代词那个/那里/那么不误删 ...
```

`tests/test_compile_day.py`：ALL PASS，关键输出示例：
```markdown
# 2026-10-12

## 08:31

今天天气不错，我们下午三点开会，讨论新产品的销售策略。我觉得吧，这个方案还是可以的，对吧？

## 14:05

你去吗？嗯。啊，太好了，我们走吧。
```
- 语气词"嗯/那那个那个"已删，"吧/呢/啊/问答嗯"保留，重复句去重
- 08:31/08:45（间隔 14 分钟）合并为一段，14:05 另起一段
- 幂等：连续跑两遍，文件字节一致
- 手动锁定：`<!-- manual -->` 段落重编后原样保留，其他段正常重编
- 无日志日期返回 None，不生成空文档

## 三、打包（`dist/voice-ime-windows-v4.zip`，7.8MB）
- 仍为纯客户端：无新增 pip 依赖，install.bat 不变（3 步）
- 目录结构展平（去掉 voice-ime/ 前缀），install.bat 与安装说明同时放在 zip 根目录，
  与 README"解压后双击 install.bat"一致
- 排除：models/（4.4GB）、.venv、.git、__pycache__、dist

## 四、已知限制
1. 日志钩子在 `_handle_message` 中：若服务端全程无响应（无 final 消息），
   该次录音不会进入日志层（极端断连场景）
2. 问答"嗯"的判断只看前一句是否为问句，跨条目上下文已支持，复杂对话场景靠规则兜底
3. 去口语化为轻量规则，未做语义级改写；小模型判别以后再说
4. 23:55 定时器要求客户端当时正在运行；关机则跳过，可手动或任务计划补跑

---

# v4.1：文档编译器分段规则升级（2026-10-12）

## 背景
Hongjie 在 Win11 上实测 v4，发现 30 分钟内的三条独立语音被合并成一个段落
（"现在试试语音输入。可以正常工作……我们下午三点开会……"挤在一行），
要求明确分段规则。规则经用户确认后实现。

## 分段规则（R1–R4）
- **R1 默认独立成段**：每次语音输入（jsonl 里的一条）独立成一段，段间空一行
- **R2 碎片合并**（同时满足才合并）：(a) 与上一条间隔 < 60 秒；
  (b) 上一条"没说完"（清洗后字数 < 8，或结尾不是 。！？!?）；
  合并后单段超过 200 字则不合并，另起一段
- **R3 时间分节不变**：30 分钟一个 `## HH:MM` 小节，跨节必分段
- **R4 空条跳过**：清洗后变空的条目不输出、不占段落

## 改动
- `core/client/doc_compiler/compile_day.py`：删除旧 `assemble()`（整节合并一段），
  新增 `_is_unfinished()` / `_dedup_sentences()` / `build_paragraphs()`；
  `compile_day()` 按节调用 `build_paragraphs()`，段间以空行分隔。
  幂等、`<!-- manual -->` 锁定、三种触发方式不变。
- `tests/test_compile_day.py`：新增 `test_paragraph_rules()`（7 组断言）。
- `dist/voice-ime-windows-v4.zip`：仅替换其中 `core/client/doc_compiler/compile_day.py`。
- `dist/compile_day.py`：更新后的单文件，用户可直接覆盖到
  `<安装目录>\core\client\doc_compiler\compile_day.py`，无需重装。

## 测试证据（Linux 实测）
`tests/test_compile_day.py`：23/23 通过（含新增 7 项）。
用户真实场景复现（13:01:40 / 13:01:57 / 13:02:22 三条原文）输出为三段独立段落；
碎片 case（"记得把会议纪要，" + 20 秒后 "发到群里。"）正确合并为一段。

# v5：基于本地大模型的每日文档内容整理与总结（2026-10-12）

## 背景
Hongjie 要求对每日文档做"基于内容的整理和总结"（主题聚类、去重、提炼要点）。
模型已定：Qwen3.8-27B Q4_K_M GGUF（用户 PC 路径
`C:\AI\models\gguf\lmstudio-community\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q4_K_M.gguf`），
llama-server + Vulkan 后端，独立实例跑在端口 8081（ASR 服务端 6016 不动）。

## 实现内容
- `core/client/doc_compiler/llm_client.py`（新增，仅标准库 urllib）：
  极简 OpenAI-compatible 客户端 `chat(messages, base_url, model, timeout)`，
  调 `/v1/chat/completions`；不可达/超时/异常响应时抛 `LLMUnavailable`。
- `core/client/doc_compiler/summarize.py`（新增）：
  输入 `docs/daily/YYYY-MM-DD.md` → 按 `## HH:MM` 节切分 →
  超 20000 字则分块 → LLM 整理（主题分节/归拢/去重/每节 1-3 条要点）→
  再调一次 LLM 写 `## 今日总结`（3-5 句）→
  输出 `docs/daily/YYYY-MM-DD.summary.md`。
  幂等：`logs/.last_summary.json` 记录原文档哈希，无变化不重跑。
  命令行：`python summarize.py [日期] [--base-dir DIR]`。
- 托盘菜单新增"📝 生成内容总结"（`tray_manager.py`）。
- 23:55 自动流程（`app.py`）：编译文档之后尝试自动总结；
  LLM 不可达则记一条日志静默跳过，不打扰用户。
- `packaging/start_llm_server.bat`（新增，纯 ASCII 零中文）：
  检查/下载 llama-server Vulkan 版 → 检查模型文件 → 启动
  `llama-server -m <MODEL_PATH> --alias qwen --port 8081 -c 32768 -ngl 99`。
  模型路径为文件顶部变量，方便修改。

## 测试证据（Linux 实测）
- `tests/test_llm_client.py`：5/5 通过（mock HTTP 服务：正常解析/请求构造/
  不可达抛 LLMUnavailable/坏 JSON/异常响应结构）。
- `tests/test_summarize.py`：ALL PASS（分节解析、短文档单块、超长按节分块、
  fake LLM 端到端写入、原文档不受影响、needs_summary 幂等、
  LLMUnavailable 向上传播）。
- 回归：`tests/test_clean.py` 21/21，`tests/test_compile_day.py` ALL PASS。
- **如实注明**：LLM 调用链在 Linux 上用 mock 验证，未做真实大模型端到端测试，
  需用户在 Win11 上用 Qwen3.8-27B 实测（prompt 效果、总结质量、耗时）。
