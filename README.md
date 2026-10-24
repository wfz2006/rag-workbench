# RAG Workbench

## Windows 桌面便携版

当前版本 0.1.10，Windows 便携版见 [Releases](https://github.com/wfz2006/rag-workbench/releases)。上一轮记录见 [第九轮优化与发布记录](docs/REVIEW_ROUND9_2026-10-24.md)。本轮修复安全校验、删除回滚、增量索引、检索缓存、亮色对比度与窄屏布局；历史记录：[0.1.8](docs/REVIEW_ROUND8_2026-10-24.md)、[0.1.7](docs/REVIEW_ROUND7_2026-10-24.md)、[0.1.6](docs/REVIEW_ROUND6_2026-10-24.md)、[0.1.5](docs/REVIEW_ROUND5_2026-10-24.md)、[0.1.4](docs/REVIEW_ROUND4_2026-10-24.md)、[0.1.3](docs/REVIEW_ROUND3_2026-10-24.md)、[0.1.2](docs/REVIEW_ROUND2_2026-10-24.md)、[0.1.1](docs/REVIEW_2026-10-24.md)。

桌面版双击 RAG Workbench.exe 启动，无需终端或系统 Python；API Key 与资料保存在独立 user-data 中。
安装、配置和可复现打包命令见 [桌面版说明](docs/DESKTOP.md)。

本地、单用户、领域无关的智能知识工作台。正式知识库初始为空；导入任意主题的资料后，
系统独立完成解析、分块、向量+关键词混合检索、证据判断、回答生成和来源追踪。

**Python 3.12 · Streamlit · BGE-small-zh-v1.5 · FAISS · SQLite · DeepSeek**

已配置独立的[蓝桥杯与数据结构课程学习助手](knowledge_packs/lanqiao_ds/README.md)：
运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-lanqiao.ps1`，
默认打开本机端口8502，使用专用数据目录和显式教学配置。

《红楼梦》知识包已收录在 `knowledge_packs/hongloumeng/`（公版正文、章节清单与评测元数据；不含校注、来源清单和索引）。
《三体全集》样例因版权限制仅保留在本机，不包含在本仓库中。
本机已有对应知识包时，可分别运行 `.\start-hongloumeng.ps1`（端口 8505）和
`.\start-santi.ps1`（端口 8503）。

给其他 agent 建库时可参考[知识库开发标准与指南](docs/KNOWLEDGE_BASE_DEVELOPMENT_GUIDE.md)，
配套提供[任务模板](docs/templates/KNOWLEDGE_BASE_AGENT_TASK.md)和[验收交接模板](docs/templates/KNOWLEDGE_BASE_ACCEPTANCE.md)。

界面参考 Claude Code 桌面工作台，使用 [open-claude-ui](https://github.com/Caho1/open-claude-ui)
的 MIT 设计令牌适配 Streamlit：紧凑的会话侧栏、靠上的知识库概览、底部常驻输入区与双主题。
所有统计来自实际知识库，全部入口连接真实功能；保持 RAG Workbench 自身身份。
最新布局与验收见 [UI_CODE_LAYOUT.md](docs/UI_CODE_LAYOUT.md)，初次移植记录见
[UI_REDESIGN.md](docs/UI_REDESIGN.md)，许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
这是 Streamlit 的界面适配，不声称逐像素一致。

## Windows 快速启动

在项目根目录的 PowerShell 执行。已有 `.venv` 时，一条命令启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

打开终端显示的 `http://127.0.0.1:8501`。可用 `-Port 8517` 更换端口。
本项目只监听本机；首次启动不需要填写 Streamlit 邮箱。按 `Ctrl+C` 停止。

全新环境（先安装 Python 3.12 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)）：

```powershell
cd rag-workbench
uv sync --python 3.12 --frozen --extra dev
Copy-Item .env.example .env
notepad .env
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

如果 uv 未找到 Python 3.12，可以 `uv python install 3.12`，或指定已有解释器路径。
环境与依赖只安装到本项目 `.venv`；不升级或替换全局 Python。首次依赖和模型下载需要网络。
`uv.lock` 固定本次验证的完整依赖版本；推荐 Python 3.12，不能把未测试的解释器版本视为已验证。

不使用 uv 时（安装范围仍限于项目）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

pip 按 pyproject 范围解析依赖，版本可能与锁文件不同；可复现安装优先用 uv。

## API Key 与隐私

在 `.env` 中只填自己的密钥：

```dotenv
DEEPSEEK_API_KEY=your-real-key
```

配置后重启程序。未配置密钥也能进入全部页面、导入文档、建立索引、使用 CLI 纯检索和运行测试；
需要 LLM 的操作会显示配置错误，不会伪装为“知识库没有答案”。空库且密钥可用时正常进入通用问答。
密钥不会在界面明文展示或写入 SQLite。不要把 `.env`、`data/` 或私有材料提交到 Git。

**远程证据判断和回答生成会发送检索到的文档片段及有限的必要对话。** 原始文件、索引与会话存本机。
默认不记录提示词、文档正文或密钥日志。调试页也可能调用付费 API。

## 五个工作页面

- **AI 聊天**：持久化会话、新建/切换会话、生成状态、回答模式、折叠来源与检索详情。
- **知识库管理**：上传 TXT、Markdown、普通 PDF、DOCX、EPUB、HTML；文件状态与统计、删除、重建索引。
- **知识包**：构建带来源清单、生成映射、冻结设置和检索自检的草稿包；支持 LLM 起草 profile/评测题、人工审核、版本化冻结、离线/在线评测与失败诊断。可切换、运行体检。
- **系统设置**：即时调整 LLM/Top-K/阈值/兜底，或修改模型与分块后明确要求重新索引。
- **检索调试**：查看全部候选、相似度、阈值、证据充分性、问答路由和结构化结果；不生成答案。

浅色/深色主题使用 Streamlit 右上角菜单的 System / Light / Dark，支持系统主题。
页面优先使用原生组件，窄窗口自动排列；长文与来源按需展开。

## 文档操作

上传文件后点击“导入并建立索引”。支持 UTF-8/常见 GB18030 文本、Markdown、有文本层的 PDF、DOCX、
EPUB 和 HTML；PDF 需要已有文本层。单文件默认 20 MB，最多 300 页 PDF、提取文本最多 200 万字符。
加密、损坏、空文档与无文本扫描 PDF 会明确报错，不生成无效索引；不提供 OCR，也不支持旧式 `.doc`。

同内容（SHA-256）不会重复入库；同名不同内容保留为不同文档。原件按安全 ID 保存。
首次索引会下载真实 BGE 模型到 `.cache/models`，后续进程和页面刷新复用缓存。
无密钥时向量检索依然可用，它不依赖 LLM API。

删除先生成新索引、原子提交元数据，再把原件移入 `data/production/trash`。
CLI 可恢复保留的原件（用完整文档 ID）：

```powershell
.\.venv\Scripts\python.exe -m rag_workbench.cli restore DOCUMENT_SHA256
```

失败上传留 `failed` 元数据和原件，排除故障后点“重新建立索引”。索引损坏或配置不兼容会阻止检索，
并提示重建；不会偷偷改成通用问答。旧索引快照保留供恢复分析，旧聊天保留当时来源文本。

## 知识包构建（P1 草稿）

“知识包”页把一批资料整理成可追溯、可体检的草稿知识包。构建器会：

1. 保留原件副本，登记 `source_manifest.csv`（来源、SHA-256、授权状态）；
2. 生成 `corpus/` 正文与 `generated_manifest.jsonl`（精确路径、哈希、来源映射）；
3. 写入冻结的 `settings.json`、`pack.json`（`status=draft`）、保守的 `profile/profile.md` 草案；
4. 在独立数据目录建立索引，并做一次检索自检 `eval/smoke.json`；
5. 写入 `review_notes.md`、`build_report.json` 和 `lint_report.*`。

所有产物默认是“自动草稿，未验收”：`eval/smoke.json` 只检查索引自洽，不是准确率、
事实正确性或独立留出验收；profile 需人工审核。授权未知/内部资料默认仅本机，不分发。
EPUB/DOCX/HTML 按来源合并为一个整理正文，章节标题保留但不拆成独立文档；PDF 页码来自
文本层解析。构建为同步执行，大资料包期间页面会等待。
构建成功后可在左侧切换到该知识包；切换使用独立的数据目录和冻结配置。知识包目录与运行数据
目录分离，复制包目录不会自动带上索引。

### 审核与评测（P2）

“知识包”页对选中的包提供三个标签：

- **Profile**：可用 LLM 起草 profile 草案，或直接手工编辑。草案不会自动替换当前 profile；
  采用/保存会写入历史、内容哈希和审核记录，并把 `profile_status` 标为 `reviewed`。
- **评测集**：可用 LLM 起草带真实 chunk 锚点的候选题目，确定性检查锚点归属、离线可达性和
  负例风险；人工编辑、批准/拒绝、分配 dev/holdout 后才能冻结。冻结会生成版本化
  `eval/freezes/fNNN/`、运行时四键 labels、profile/settings/正文清单/labels 哈希和
  `eval/current.json`。profile 未审核、正例不可达、负例被门槛放行、题目重复都会阻止冻结。
- **评测报告**：可运行离线（不调用 LLM）或在线（逐题调用模型）评测；在线必须勾选费用确认。
  holdout 运行会消耗一次性留出资格（在线全部服务错误时不消耗，可修复后重试）；冻结后
  profile/settings/正文清单变化会让旧冻结失效，必须重新冻结。报告包含检索/路由/引用结构和
  规则化失败诊断；答案事实正确性仍需人工逐条核对。

```powershell
.\.venv\Scripts\python.exe -m rag_workbench.cli --pack my-pack pack-eval --split dev
.\.venv\Scripts\python.exe -m rag_workbench.cli --pack my-pack pack-eval --split holdout --confirm-holdout
.\.venv\Scripts\python.exe -m rag_workbench.cli --pack my-pack pack-eval --split holdout --online --confirm-cost --confirm-holdout
```

P2 不自动调参、不自动认证验收；`eval/smoke.json` 仍只是索引自检。在线评测和 LLM 起草都
不会在未确认时调用远程 API。

```powershell
.\.venv\Scripts\python.exe -m rag_workbench.cli pack-list
.\.venv\Scripts\python.exe -m rag_workbench.cli pack-lint my-pack
.\.venv\Scripts\python.exe -m rag_workbench.cli --pack my-pack stats
```

## 检索、证据与回答模式

```text
问题 → BGE 向量 Top-50 + BM25 关键词 Top-50 → RRF 混合排序 → Top-K
     → 可调向量阈值或关键词覆盖门槛
     → LLM JSON 证据充分性判断 → 结构验证 → 有依据的逐条回答与真实 Chunk 引用
                                     └→ 明确不足时进入通用模式/拒答
任何服务或结构校验失败 → error，不能当作未命中
```

模式为 `knowledge_base`、`general`、`error`；关闭兜底时增加 `insufficient_evidence`。
“相近”不等于“可回答”。默认阈值 0.55 只是起点，必须用你的真实资料和问题校准。
当前检索使用 BM25 与稠密向量的等权 RRF 融合，`RAG_RETRIEVAL_CANDIDATE_POOL`（默认50）
控制每路候选池；`result.score` 保持稠密余弦分，混合排序后的列表分数不保证单调。
词项覆盖达到 `RAG_LEXICAL_MIN_COVERAGE`（默认0.8）的候选也可进入证据判断，不直接批准答案。
证据判断不能默认批准：无效 JSON、虚构 ID、不一致布尔值和无效回答引用均返回明确错误。
显示的来源只取自实际候选且被批准支持的片段。生成正文要求与问题同语言（中文问题不混入
其他语言），只陈述 evidence 中完整、直接支持的内容；不补全截断句，也不加入证据外常识。
可选的 `RAG_RETRIEVAL_CONTEXT_WINDOW`
（0～2，默认0）可补入通过阈值的主片段在同文档中的相邻片段；它们保留各自真实分数、
ID和“相邻上下文”标记，不伪称通过阈值。候选总量最多20。

模型仍可能误判充分性或生成不被证据完全蕴含的表述；来源有效性检查不能证明答案事实正确。
高要求场景应加入人工审核、回答后事实验证与独立评估，不应只看相似度或引用数量。

## 配置和替换模型

配置优先级：进程环境变量 > `.env` > 页面保存的非敏感配置 > 内置默认值。
`.env.example` 的可编辑参数默认注释；取消注释会让环境配置优先，页面会显示受环境控制的字段。
如果手动修改数据库导致设置无效，可运行
`python -m rag_workbench.cli reset-settings` 清除页面覆盖值；文档和会话保留。

| 配置 | 默认 | 生效方式 |
|---|---|---|
| `RAG_LLM_MODEL` | `deepseek-flash` | 下一次请求 |
| `RAG_LLM_BASE_URL` | `https://api.deepseek.com` | 下一次请求 |
| `RAG_LLM_TIMEOUT` | 60 秒 | 下一次请求 |
| `RAG_LLM_MAX_TOKENS` | 2048 | 下一次请求 |
| `RAG_TOP_K` / `RAG_SIMILARITY_THRESHOLD` | 5 / 0.55 | 下一次检索 |
| `RAG_RETRIEVAL_CANDIDATE_POOL` | 50 | 下一次检索 |
| `RAG_ALLOW_GENERAL` / `RAG_SHOW_DEBUG` | true / false | 下一次交互 |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | 400 / 60 字符 | 保存后重新索引 |
| `RAG_EMBEDDING_MODEL` / revision / query instruction | BGE small 中文 | 保存后重新索引 |
| `RAG_DATA_DIR` / `RAG_MODEL_CACHE` | `data/production` / `.cache/models` | 重启 |

BGE 中文 query 指令只添加到问题，文档正文不添加。向量维度从真实模型输出推断；
模型/revision/指令/分块指纹与索引绑定，即使维度相同，换模型后也不能混用旧向量。
建议用固定 Hugging Face commit revision，或设置本地模型目录。换非 BGE 模型时按其官方说明
修改 query instruction（可设空），再重建；`Embedder` 端口也允许自定义适配器。
字符窗口是软上限，入库前按真实 tokenizer 再拆分，保留原文偏移；长查询分段编码后聚合向量。
没有静默截断文档或问题；检索指令本身超过模型预算等异常仍会明确报错。

切换其他 OpenAI 兼容服务：配置 `RAG_LLM_BASE_URL`、模型名、对应 Key（环境变量名仍为
`DEEPSEEK_API_KEY`），并设置 `RAG_DEEPSEEK_MODE=false`，避免发送 DeepSeek 专属参数。
该服务需要支持 Chat Completions 和 `response_format=json_object`。
API 模型名称和参数查阅记录见 [SOURCES.md](docs/SOURCES.md)。

## 独立示例与清除

样例从不自动进入正式知识库：

```powershell
.\.venv\Scripts\python.exe -m rag_workbench.cli sample
.\.venv\Scripts\python.exe -m rag_workbench.cli --data-dir data/sandbox search "样例记录保管多久？"
# 一键清空专用样例库，不碰 production
.\.venv\Scripts\python.exe -m rag_workbench.cli sample --clear
```

`sample --clear` 只删除项目 `data/sandbox` 下带隔离标识、且不是链接/联接的目录。
不要把私人材料放入可清空沙盒。`examples/` 原文件保留，可再次导入。
在独立终端设置 `$env:RAG_DATA_DIR='data/sandbox'` 后启动可查看样例 UI；退出该终端即可恢复默认。
沙盒不要与正在运行的服务器同时清空。普通业务删除不会销毁原件或旧索引。

## 测试与评估

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest -q
# 显式真实模型测试；会下载 BGE，无付费 API
$env:RAG_RUN_MODEL_TEST='1'
.\.venv\Scripts\python.exe -m pytest tests/test_real_embedding.py -q
Remove-Item Env:\RAG_RUN_MODEL_TEST
```

自动化默认使用测试目录内的 Fake LLM/Embedding，FAISS 和 SQLite 用真实实现；
API 适配器使用真实 OpenAI SDK 和模拟 HTTP 传输，既不联网计费，也不会伪造真实 API 验收。
真实模型测试与普通测试分开报告。实际验收见 [VALIDATION.md](docs/VALIDATION.md)。

评估脚本及指标定义见 [EVALUATION.md](docs/EVALUATION.md)，先收集独立标注问题，不能拿开发样例
结果宣称业务准确率。默认评估只做检索；`--with-routing` 会执行证据判断和生成，可能产生 API 费用。

## 项目结构

```text
rag-workbench/
├── app.py                    # Streamlit 入口
├── start.ps1                 # Windows 启动
├── pyproject.toml / uv.lock   # 依赖与可复现锁文件
├── .env.example / .streamlit/config.toml
├── src/rag_workbench/
│   ├── config.py / bootstrap.py / cli.py
│   ├── domain/               # 输入输出模型、错误和端口
│   ├── application/          # 知识库管理、知识包构建/审核/评测、RAG、提示词
│   ├── infrastructure/       # SQLite、FAISS、BGE、LLM、解析与分块
│   └── ui/                   # 五页、来源与调试组件
├── tests/                    # 离线回归与可选真实模型测试
├── examples/                 # 独立虚构样例 TXT / MD / PDF
├── scripts/                  # 开发辅助工具
├── docs/                     # 计划、评估、验证与官方来源
└── data/                     # 不提交：原件、元数据、索引、回收与会话
```

核心设计与发布协议见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 聊天交互

通用回答直接显示 API 的 SSE 增量；知识库回答先接收并验证完整 JSON 与引用，再展示正文。
生成时显示实际阶段与耗时，可停止本轮；切换会话后可回到正在生成的会话。
支持上滚暂停跟随、圆形按钮返回最新、复制回答、资料与模型菜单、输入法组合输入和减少动态效果偏好。
视觉按用户提供的侧栏展开/收起截图校准，保持 RAG 自有品牌与实际功能。
实现与验证记录见 [UI_INTERACTIONS.md](docs/UI_INTERACTIONS.md)。

运行中任务保存在本次浏览器会话内，硬刷新不恢复实时输出；后台完成的结果仍会保存。
停止会关闭已建立的流连接；首次连接和本地 Embedding 运算需到下个检查点或超时才结束。
默认关闭 Streamlit 源码文件监听，以避免其遍历 Transformers 的可选模块；修改 Python 后请重启服务。
开发时可显式使用 `--server.fileWatcherType auto` 恢复监听。
配置也关闭每次刷新后的强制全量垃圾回收，避免模型加载后阻塞 fragment；Python 自动回收仍正常启用。

## 局限与下一步

暂未提供 OCR、旧式 `.doc`、向量增量更新、cross-encoder reranker、
多用户认证或分布式部署。已提供 BM25 混合检索（稠密+关键词 RRF）；
PDF 复杂表格/多栏阅读顺序可能需要人工检查；分块优先换行/标点并满足实际 token 预算。
知识包构建与审核支持 LLM 起草、人工审核、版本化冻结和在线评测，但不做自动调参或自动合规签字；
LLM 生成内容只有人工采用后才生效，答案事实正确性仍需人工核对。
历史按完整回合保留最近至多 12 条、最多 16000 字符；错误回合不进入模型，但界面仍保留记录。
模型缓存最多两个配置；旧快照与回收原件不自动清理，长期使用需制定备份/保留策略。
网络首次下载与模型推理可能有等待；重建期间同库读写串行，不适合大规模并发。

优先优化顺序：真实评测集与阈值校准 → 更大候选池/重排器（按实际延迟和收益决定）→
更好的结构分块与表格解析 → 回答后事实验证 → 任务恢复与大库性能。当前没有业务准确率或性能承诺。

