# Windows 桌面便携版

解压整个 `RAG-Workbench-0.1.8-win-x64` 文件夹到可写目录，例如 `D:\Apps\RAG-Workbench`，
双击 **RAG Workbench.exe**。请保留同目录的 runtime、resources、models 等文件夹。
不需要自己安装 Python，不需要终端，不需要管理员权限。

支持 Windows 10/11 x64，需要 .NET Framework 4.6.2+ 和 Microsoft Edge WebView2 Runtime。
Windows 11 通常已具备这些组件。缺少 WebView2 时请从微软官网安装：
https://developer.microsoft.com/microsoft-edge/webview2/ 。不回退到旧版 IE 引擎。
此版本尚未进行代码签名，Windows 下载保护可能显示未知发布者。

## API 与数据

首次启动创建软件目录下的 `user-data`，不会导入开发目录中的私人配置或资料。
在顶部 **工作空间 → 配置 API Key** 中打开独立 `.env`，填写：

```dotenv
DEEPSEEK_API_KEY=你的Key
```

保存并重新启动软件。API Key 仍由环境配置文件读取，不内置、不在应用页面明文显示。
空知识库可以正常打开；没有 Key 时可以管理资料和检索，生成回答会明确提示缺少配置。
被检索到的资料片段可能发给远程模型服务；离线语义检索不需要 DeepSeek API。

- `user-data/.env`：个人 API 配置，可加 RAG_LLM_MODEL、RAG_LLM_BASE_URL 等参数。
- `user-data/knowledge`：正式文档、SQLite 元数据、FAISS 索引及会话。
- `user-data/models`：模型缓存；完整分发版首次启动从公开模型种子复制。
- `user-data/browser`：WebView2 的页面设置、主题等本地浏览器数据。
- `user-data/.streamlit/config.toml`：默认主题、上传限制等配置，首次生成后不会覆盖。
- `user-data/desktop.log`：本次本地服务诊断。分享诊断前请检查是否含私人内容。

备份整个 `user-data` 即可保留资料、会话、配置和缓存。升级时先备份，将原 `user-data`
复制到新版文件夹；**分享软件只分享原始 ZIP，不要重新打包已经使用的 user-data**。
删除软件不会自动清理其他位置的数据。程序不自动迁移现有开发知识库，避免混淆工作空间。

如需迁移旧库：先关闭旧服务与桌面窗口、备份数据，将旧项目 `data/production` 的完整内容复制到
新版 `user-data/knowledge`，保持原 Embedding/分块参数，再在桌面版单独填写 Key。不要只复制 FAISS
文件，应一起保留 SQLite 元数据、原始文档和索引版本目录。

同一个工作空间一次只允许一个窗口；其他工作空间可通过快捷方式参数指定：

```powershell
& '.\RAG Workbench.exe' --profile 'D:\RAG-Data\personal'
```

关闭窗口会结束此窗口所属的本地服务。服务仅监听 127.0.0.1，不占用开发预览的 8517 端口。
关闭生成中的窗口会终止该次服务；已完成并保存的会话继续保留。

## 从源码复现打包

在 Windows 根目录运行，使用 uv 管理的 Python 3.12；不修改全局 Python：

```powershell
uv sync --python 3.12 --extra dev --extra desktop
.\.venv\Scripts\python.exe scripts\build_desktop.py --include-model --zip
```

`--include-model` 需要项目 `.cache/models` 已存在公开 BGE 缓存。没有缓存可省略此参数，
随后首次导入资料时按原逻辑下载模型。输出目录已存在时构建拒绝覆盖，可用 `--output` 指定新目录。
该包使用自包含 Python 发行版，并由 uv.lock 导出带哈希的生产依赖，不直接复制虚拟环境。
保留库的安装元数据/许可文件、Python LICENSE、BGE 模型说明，并生成第三方清单与 SHA256SUMS.json。
不会打包 `.env`、data、artifacts、示例/课程私人资料或开发源代码中的绝对路径配置。

图标为 imagegen 内置工具生成的原创书页与引号造型，暖白、陶土橙、深灰配色。
PNG 原图与 16–256px 多分辨率 ICO 在 `src/rag_workbench/ui/assets/brand`，用于 EXE、窗口和网页图标。
提示词见 `docs/ICON_PROMPT.txt`。本软件没有使用 Claude/Anthropic 的官方名称或标志作为软件身份。

打包参考官方文档：

- https://docs.astral.sh/python-build-standalone/ ：可迁移的 Python 运行环境。
- https://pywebview.flowrl.com/guide/installation.html ：Windows 依赖要求。
- https://pywebview.flowrl.com/api/ ：桌面窗口、图标、菜单、浏览器数据目录。
- https://docs.python.org/3.12/using/windows.html ：隔离 Python 路径与分发。

## 验证与限制

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
& '.\dist\RAG-Workbench-0.1.8-win-x64\RAG Workbench.exe' --smoke-test --profile 'D:\RAG-Desktop-QA'
```

`--smoke-test` 是显式验收模式：真实启动隐藏的 WebView2 窗口、真实 Streamlit 服务，
检查聊天输入区域与页面异常后自动关闭；结果保存在该验收 profile 的 smoke-result.json。
同时依次切换知识库管理、系统设置、检索调试和聊天四个页面。
不会发出付费 API 请求。普通双击不会运行这些验收操作。

交付版本为便携软件，不包含安装向导、自动更新、签名证书、OCR 或 macOS/Linux 桌面包。
依赖 PyTorch 的本地语义检索使体积明显大于纯网页壳。远程回答仍需要网络、有效 Key 和 API 余额。
本机验证不能替代所有 Windows 版本、干净虚拟机和不同显卡/DPI 的验收。

当前实际执行记录见源码项目 `docs/REVIEW_ROUND8_2026-10-24.md`；`docs/DESKTOP_VERIFICATION.md` 保留初版历史记录。

## 0.1.8 修复更新

旧回合重试完成后释放临时滚动目标，↓ 和手动滚到底部可正常使用；鼠标发送/重试与 Enter 的跟随行为一致。设置保存、重建和恢复默认的操作提示在同一索引状态下保留，外部状态变化仍会同步。补齐代词前置连接词与单轮多行引文问题的检索判断。

升级前请备份 user-data。0.1.1–0.1.7 的索引可继续使用；0.1.0 索引需重新建立。不要覆盖旧软件目录，解压到新目录后使用备份的工作空间；具体修复与验证边界见同目录 `修复说明.md`（源码中的 docs/REVIEW_ROUND8_2026-10-24.md）。

编辑失败问题时，输入区显示替换提示和取消按钮；清空旧问题后可继续重输，编辑状态保留；点击取消、按 Escape、切页或切换会话会退出编辑。中间回合的生成和未保存替换结果保持原时间线位置；移除未保存替换结果会重新显示数据库里的旧失败回合。未闭合代码围栏在单条 claim 的展示/复制边界补齐，不改写数据库原文。设置保存后的重建提示以当前活动索引状态为准。窄窗按钮浮在输入区上方，不再因显隐改变输入区高度。

本轮修正引用块/列表退出后的多余空代码块。旧回合重试自动跟随该回合；用户主动跳至末尾后，停止跟随该中间回合。引文题接入共享题型识别、有限同文档引文扩展与逐字证据核对，解释题不再仅因包含“曲文”误判成名称题。设置反馈随实际索引状态双向刷新。

本版保留代码围栏和表格结构，结构化内容后的来源单独显示；普通自然段仍使用行内来源，编号按首次引用排序。重试与重新编辑失败问题会原位替换原回合，保存失败保留旧记录；移除首轮失败后自动标题会重置，明确手动命名的会话保留。恢复回收文档只编码该文档并合并活动向量，保留原上传时间和来源信息；其他原件丢失不再阻塞恢复。

删除文档只剔除活动向量，不重算其他文档；即使其他原件丢失也能删除。缺少原件的回收记录仍无法恢复，请重新上传。数据库慢写入期间可以切换页面；进入“正在保存记录”后不能取消已经提交的回答。复制回答只带正文，知识库来源和通用模式标识仍在界面中单独保留。

失败回合可重试、重新编辑或单独移除；搜索会话后会显示筛选状态和清除入口。设置保存反馈出现在按钮附近并弹出通知，模型/分块变化时提供重建入口。“恢复页面默认设置”只清除页面覆盖值，不清除 API Key、部署环境、文档或会话。

回答落库失败时，正文和常显复制按钮暂存在当前浏览器会话，按时间顺序与成功消息一起展示，可新建会话或切页后返回，也可手动移除未保存记录。后台完成任务在所有页面都会回收。关闭或刷新浏览器会失去未保存回合，请先复制。受管启动器若拒绝独立启动记事本，菜单会显示 `.env` 路径供手动编辑，不会改用随主窗口关闭而被终止的编辑器进程。
