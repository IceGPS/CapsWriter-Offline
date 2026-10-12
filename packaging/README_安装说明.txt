# voice-ime Windows 客户端安装说明（v5：仅客户端 + 本地大模型内容总结）

## 这是什么

voice-ime 第一阶段 Windows **客户端**：按住 CapsLock 说话，松开自动转写上屏。
本版本**只装客户端**，语音识别复用你本机已有的 CapsWriter 服务端
（Qwen3-ASR-1.7B），不重复下载模型。

## 系统要求

- Windows 11（64 位）
- 本机已安装 CapsWriter 且服务端可用（Qwen3-ASR-1.7B）
- Python 3.12（没有会自动装）

## 安装步骤（共 3 步）

1. 解压 `voice-ime-windows-v1.zip` 到任意目录（路径不要含中文，例：`D:\voice-ime`）
2. 双击 `install.bat`，按提示装好客户端依赖（只需联网几分钟）
3. 使用（每次）：
   - 确保 CapsWriter **服务端**正在运行（你原来那个，不用动）
   - 右键托盘**退出原版 CapsWriter 客户端**（避免两个客户端抢 CapsLock 导致重复上屏）
   - 双击 `start_client.bat`（托盘出现 "voice-ime" 图标即就绪）
   - 打开记事本/Word/微信，按住 **CapsLock** 说话，松开即自动上屏

## 每日文档（v4 新增）

你说的每一句话都会被记录下来，每天整理成一篇文档。

- **原始记录（日志层）**：`logs\YYYY-MM-DD.jsonl`（转写文字，含语气词）+
  `recordings\YYYY-MM-DD\`（录音文件），都在程序目录下
- **整理后的文档（文档层）**：`docs\daily\YYYY-MM-DD.md`，每天一份。
  自动做的事：去句首语气词（嗯/呃/那个）、按时间段分节、去口语化；
  句尾的"吧/呢/啊"、感叹的"啊"、问答中的"嗯"会保留

**三种生成方式**：
1. 托盘菜单点"📄 生成今日文档"——随时手动生成
2. 每天 23:55 自动生成（仅当有新内容时；电脑关机则跳过）
3. 命令行：在程序目录运行 `python core\client\doc_compiler\compile_day.py`
   （可加入 Windows 任务计划程序定时执行）

**手动锁定段落**：文档是自动重编的，你手改的内容下次重编会被覆盖。
如需锁定某段不被覆盖，在该段二级标题前面加一行 `<!-- manual -->`
（或写在段落内），重编时会原样保留。

## 内容总结（v5 新增）

在每日文档的基础上，用本地大模型做"基于内容的整理"：
按主题重新分节、同一主题归拢、删除重复和测试语句、每节提炼要点，
文末生成"今日总结"。输出为 `docs\daily\YYYY-MM-DD.summary.md`，
原文档和日志层不受影响。

**模型要求**：需要本机运行 Qwen 大模型（Qwen3-32B 或 Qwen3.8-27B，
Q4/Q8 量化均可；128GB 统一内存的机器无压力）。本功能通过 llama-server
调用（端口 8081），与语音识别的服务端（端口 6016）互不干扰。

**启动大模型服务（只需做一次，之后每天要用时保持开着）**：
1. 双击 `start_llm_server.bat`（程序目录下）
   - 首次运行会自动下载 llama-server（Vulkan 版，约 33MB）
   - 默认模型路径：`C:\AI\models\gguf\lmstudio-community\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q4_K_M.gguf`
     如你的模型在别处，用记事本打开 bat 改顶部 `MODEL_PATH` 一行
2. 看到 llama-server 启动日志后，保持该窗口开着（关闭即停止服务）

**两种触发方式**：
1. 托盘菜单点"📝 生成内容总结"——随时手动生成
2. 每天 23:55 自动流程：在"生成今日文档"之后自动尝试；
   若大模型服务没开则静默跳过（只记日志，不打扰）

**注意**：内容总结依赖大模型服务；v4 的每日文档不依赖它，
不开大模型也能正常用。

## 常见问题

**Q: 按住 CapsLock 没反应？**
A: 确认服务端在运行（原版 CapsWriter 的服务端窗口开着），且 `start_client.bat` 已启动、托盘有 "voice-ime" 图标。另外 CapsLock 键不要被其他软件占用。

**Q: 打出来的字不对/延迟高？**
A: 转写由服务端负责，请先确认原版 CapsWriter 客户端直连同一服务端时是否正常，以区分是服务端问题还是本客户端问题。

**Q: 想换热词？**
A: 编辑 `hot.txt`（客户端热词），保存后自动生效。

**Q: 如何开机自启？**
A: 把 `start_client.bat` 的快捷方式放入启动文件夹（Win+R 输入 `shell:startup`）。注意服务端也要自启（原版 CapsWriter 自带开机自启选项）。

## 文件说明

| 文件 | 说明 |
|------|------|
| install.bat | 一键安装脚本（仅客户端依赖） |
| start_client.bat | 启动 voice-ime 客户端 |
| start_llm_server.bat | 启动大模型服务（v5 内容总结用，端口 8081） |
| config_client.py | 客户端配置（服务端地址端口、快捷键等） |
| hot.txt | 客户端热词 |
| logs\ | 每日转写日志（jsonl，按天） |
| recordings\ | 每日录音（按天） |
| docs\daily\ | 每日整理文档（Markdown，按天） |

## 卸载

删除解压目录即可。
