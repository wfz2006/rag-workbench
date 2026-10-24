# 蓝桥杯与数据结构课程学习助手实施计划（已执行）

执行状态：用户已确认并于2026-10-23完成首版交付。原计划条目保留作设计记录，实际改动、失败与修复、验收数据和取舍以 `artifacts/lanqiao_ds/progress.md`、`artifacts/lanqiao_ds/acceptance.md` 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 以下是原计划设计；执行已完成，勿据未勾选的原始条目重复实施，应先查执行记录。

**Goal:** 将现有课程、源码和蓝桥杯题目整理成可追溯、可验证的知识库，支持中文概念讲解、C++ 示例解释、分步解题提示与资料引用。

**Architecture:** 先建设独立内容包，复用 RAG Workbench 的导入、检索、证据判断和来源展示。首批在独立数据目录验收；算法教学规则通过显式配置接入，领域内容不得硬编码进通用核心。资料质量、检索质量和教学效果分开验证。

**Tech Stack:** 复用项目 Python 3.12、Streamlit、BGE-small-zh-v1.5、FAISS、SQLite、DeepSeek、pytest、Ruff；内容使用 UTF-8 Markdown、CSV、JSONL。

**Spec:** 本轮对话中用户选择“蓝桥杯与数据结构课程学习助手，先做计划”；实现依据同时包括项目 `README.md`、`ARCHITECTURE.md`、`docs/EVALUATION.md`，以及本地算法知识库的 `README.md`、`AI教学提示词.md`、`课程索引.md`。本文中的规模、指标和目录是建议值，尚未经用户逐项确认。

## 一、目标与方案选择

第一版回答以下问题：

- 解释概念、适用条件、基本操作和复杂度，并指出课程依据。
- 按资料解释 C++ 示例的状态、不变量、关键语句与边界。
- 针对库中题目给出循序渐进的提示；用户要求完整解法后再展开。
- 指出材料未覆盖、题意不完整、提取质量不足或参考源码存在疑点。

比较的路线：

| 路线 | 收益 | 成本与限制 | 本计划建议 |
|---|---|---|---|
| 一次导入全部现有资料 | 覆盖广、导入准备快 | 重复资料、残缺题面、版式噪声会一起进入检索 | 暂缓 |
| 精选本地资料并建立评测，再逐批扩大 | 易追溯、易判断错误、能复用现有成果 | 需要人工核对首批资料 | 采用此路线编制草案 |
| 以公开教程重建主库 | 内容结构较一致 | 重新整理并处理多来源术语；与现有课程体系可能不同 | 后续查漏补缺 |

当前不做：自动判题平台、在线代码执行、多用户、学习画像、知识图谱、全题库覆盖、OCR、旧 Word 自动转换、界面重做。题目参考程序不默认视为标准答案；没有运行编译与测试时不得声称代码已验证。

## 二、现状核对（2026-10-23）

- 项目：`D:\codexwork\rag-workbench`，已有独立数据目录、CLI 导入/检索/评测和 UI。
- 原始资料：`D:\bite blue bridge`；已整理副本：`D:\ccode\算法\算法知识库`。
- 现有清单登记：72 份课程 PDF 文本、347 份示例源码、263 份 TXT 题目、1 份 DOCX 和 23 份旧 DOC 来源记录。登记数量不等于本次逐件质量验收通过数量。
- 已有教学约定：中文、C++、沿课程体系讲解、来源与页码、练习时逐级提示。
- 当前 TXT/Markdown 解析为一个 Section，分块采用字符窗口；Markdown 中写着页码不代表 Chunk.page 已保留页码。
- 当前默认分块 400 字符、重叠 60 字符；是否适合中文夹代码，需用实际模型 tokenizer 和评测确定。
- 当前来源模型没有任意学科元数据字段；CSV 清单只能辅助溯源，不能自动实现按题号或年份筛选。
- 当前文档内容不具备指令权限；上传 `AI教学提示词.md` 不会启用教学模式。
- 当前评测主要是文档级命中、召回、路由和引用有效性；不等于答案正确或证据完整。
- 抽样发现 PDF 文本有独立行号、兼容字形和代码版式问题；早期题目汇编中存在多题混排、题面与解答混合。不得直接全量导入。
- 本次没有重跑模型/API、浏览器或测试；工作目录的 Git 仓库检查未成功，执行前须重新确认实际版本管理边界，不假设存在可提交分支。

## 三、首版内容范围

| 单元 | 内容边界 | 配套练习选择原则 |
|---|---|---|
| 顺序表与常用 STL | vector 基本操作、适用条件、基础复杂度 | 数组、线性模拟 |
| 栈与队列 | LIFO/FIFO、基本操作、典型应用 | 括号匹配、简单模拟 |
| 排序与二分 | 基本排序思想、区间约定、二分边界 | 查找、边界判断 |
| 递归与基础搜索 | 递归终止、回溯状态、DFS/BFS 基础 | 枚举、简单路径或状态搜索 |
| 基础动态规划 | 状态、初始化、转移、遍历顺序 | 线性 DP、基础背包，以实际覆盖为准 |

建议首批为 15～25 个独立课程知识单元、10～15 道完整题目、5～10 份配套源码说明。按知识单元统计；为保留页码而产生的多个页文件不重复计算知识单元。数量服从质量，资料缺失时缩减覆盖并登记，不为凑数编写题面、数据范围或标准答案。

蓝桥杯题目优先选有明确届次/年份、组别、题号或标题、约束、输入输出及来源的材料；缺失字段记为 unknown。不得从路径或题名猜出组别。首版使用哪些组别由清单核验结果决定，不声称覆盖最新赛制。

## 四、目录与职责

计划新增以下内容；此时尚未创建：

```text
knowledge_packs/lanqiao_ds/
  README.md                      # 覆盖范围、使用方法与版本
  source_manifest.csv            # 原始来源、页码、哈希、审核状态
  review_notes.md                # 歧义、排除项、改动依据
  syllabus.md                    # 学习顺序与前置知识，不作为主体证据
  corpus/
    lessons/                     # 有来源的课程单元/页文件
    problems/                    # 一题一文件，题面与解析分开
    examples/                    # 代码围栏、来源和必要说明
  profile/teaching.md             # 待应用层显式加载的教学规则
  eval/
    labels.dev.jsonl              # 人工标签，使用稳定 source_id
    labels.test.jsonl             # 留出验收标签
    evidence_rubric.md            # 参考事实、证据位置与评分规则
scripts/
  prepare_lanqiao_pack.py          # 只读来源，构建审核后的导入包
  materialize_lanqiao_eval.py      # 来源 ID 映射为实际上传文件 SHA-256
artifacts/lanqiao_ds/              # 本地审核、评测与验收报告
data/lanqiao_ds_trial/             # 独立试验索引和会话
data/lanqiao_ds/                   # 验收后的专用运行目录
```

`corpus/` 是唯一入库白名单；清单、教学配置、评测答案、审核笔记均不入库。默认不将含课程正文的内容包、原件、索引或会话发布到远端；实际版本管理边界确认后，区分可提交工具/文档与本地内容。

## Global Constraints

- 不修改或删除 `D:\bite blue bridge` 和 `D:\ccode\算法\算法知识库` 的原件、已有副本、索引与教学配置。
- 不改写现有 `data/production`；试验数据使用 `data/lanqiao_ds_trial`，不用可一键清空的 `data/sandbox`。
- RAG 核心保持领域无关；算法专属内容只存在内容包和显式选择的配置中。
- 不把检索文档里的教学指令提升为系统指令；教学配置由用户选择并由应用显式加载。
- 不自动下载外部材料、不新增全局依赖；先使用现有本地资料与项目虚拟环境。
- 中文、默认 C++；引用课程名和真实页码，题目引用届次/组别/题号等已核实字段。
- 页码来源是原 PDF 的物理页序，不自行等同于课件印刷页码。
- 调参只使用开发集；留出集若因失败而被用于调参，必须重新留出独立样本后再验收。
- 本文给出的数值为验收目标，尚无真实成绩；不能将模拟测试或引用 ID 有效视为教学正确。

## Review Focus

1. 同名题和重复材料：用来源标识与内容哈希区分，题目身份不足时询问或说明，不能猜题。
2. 代码与公式损坏：不盲删数字行、不全局替换运算符；无法可靠恢复的资料隔离。
3. 页码和证据丢失：每个可检索单元可回溯到原件；长单元须验证内部 Chunk 也保留上下文。
4. “先别给答案”及后续追问：首次只提示，不泄露完整代码；明确要求完整解法后可切换。
5. 无依据与知识兜底：课程特有事实不得由模型补齐；服务错误不能归类为材料不足。

## 五、分阶段任务

### Task 1：资料盘点与首批选集

**Files:** 新增 `knowledge_packs/lanqiao_ds/source_manifest.csv`、`review_notes.md`、`syllabus.md`。

**Interfaces:** 清单每行一个可审核来源单元；必需列为 `source_id,kind,topic,title,original_path,prepared_path,source_sha256,page_start,page_end,year,group,problem_id,status,notes`。status 为 selected/needs_review/excluded；不确定字段留空并说明。source_id 首次赋值后保持稳定，内容变化由哈希记录。

- [ ] 只读检查清单对应的文件、大小、哈希；对首批候选逐件抽取或打开核对正文。
- [ ] 按五个单元筛选课程、题目与示例；标注前置知识和覆盖缺口。
- [ ] 去除重复题、工具安装说明、空文档、只有目录的文件和题面残缺项。
- [ ] 检查同名题、年份/组别歧义和题面/答案混排；记入审核记录，不自动认定标准答案。
- [ ] 输出选集及暂缓清单，每个 selected 项有实际来源和审核记录。

**验收：** 所有 selected 来源存在且可打开；无空文件；未知字段已说明；每个首批单元至少有一份可用主体资料，否则明确缩减范围。

### Task 2：整理可追溯的导入包

**Files:** 新增 `scripts/prepare_lanqiao_pack.py`、`tests/test_lanqiao_pack.py`、内容包 `corpus/` 与 `README.md`。

**Interfaces:** `build_pack(source_root: Path, manifest_path: Path, output_root: Path) -> dict`；只读来源，仅处理 selected 条目。输出文件列表、哈希、审核失败项与数量，不加载模型或调用 API。拒绝输出覆盖来源目录；已有输出变化先报差异，不自动清空目录。

- [ ] 先为缺失来源、重复来源、同名不同题、来源/输出路径重叠建立失败测试。
- [ ] 将课件按页或连续小节整理；文件名包含稳定 source_id、课程短名与页范围。页边界必须可追溯，不能仅在整章开头写一次来源。
- [ ] 一题一文件，保留原题条件、样例和来源；解析、参考代码单列并标明验证状态。
- [ ] 代码优先采用源码文件，使用 fenced code block；从 PDF 恢复代码必须逐行对照。只移除经核实的页眉页脚或行号。
- [ ] 用当前 splitter 检查实际 Chunk：来源是否可辨、题目条件是否被割裂、代码是否过长；长代码按函数或逻辑段拆解并附必要上下文。
- [ ] 为重复构建确定性、代码运算符保留、原页号保留、只输出白名单资料补充断言并运行测试。

**运行：** `.\.venv\Scripts\python.exe -m pytest tests/test_lanqiao_pack.py -q`，预期全部通过。

**验收：** 每个输出文件都有清单映射；正文/页码抽检通过；未通过审核的题目和代码不入库；未改动来源文件。

### Task 3：先标注评测，再建立检索基线

**Files:** 新增内容包 `eval/`、`scripts/materialize_lanqiao_eval.py`、`tests/test_lanqiao_eval.py`；本地报告写入 `artifacts/lanqiao_ds/`。

**Interfaces:** 人工标签使用 `id,question,relevant_source_ids,expected_mode`；独立评分表记录参考事实与具体证据位置。`materialize_cases(labels_path: Path, manifest_path: Path, corpus_root: Path, output_path: Path) -> int` 将来源映射为实际导入字节的 SHA-256，并输出满足现有 `EvalCase` 的 JSONL。未知 source_id、重复问题 ID、缺失文件必须失败；额外评分字段不得传给禁止 extra 字段的 EvalCase。

- [ ] 在调参前标注 60 题：开发集 30、留出集 30；按来源和问题意图分组分割，避免仅改写措辞的近重复跨集。
- [ ] 每集建议 10 道概念/边界题、5 道代码解释题、5 道跨片段题、5 道同主题缺证据题、5 道无关或缺题目条件的问题。
- [ ] 人工确认正样本有完整证据；负样本确实无法由首批库回答。主验收关闭通用兜底，负样本 expected_mode 为 insufficient_evidence。
- [ ] 测试标签映射的哈希、未知来源拒绝、严格 EvalCase 兼容及问题 ID 唯一性。
- [ ] 将白名单文件导入 `data/lanqiao_ds_trial`；记录实际模型 revision、分块参数、Top-K、阈值及文档哈希。
- [ ] 从当前 400/60、Top-K=5、阈值=0.55 建立基线；只针对已观察到的问题试较小分块或 Top-K=8 等少量配置。每轮保留报告，不能预先宣布最佳值。
- [ ] 除文档级分数外，人工记录所需条件/转移/代码是否出现在实际候选片段，避免“大文档命中”掩盖证据缺失。

**检索命令模板：**

```powershell
# 在项目根目录运行；清单生成的精确文件路径作为 ingest 参数，禁止递归扫入整个内容包。
.\.venv\Scripts\python.exe -m rag_workbench.cli --data-dir data/lanqiao_ds_trial ingest <审核后的文件路径列表>
.\.venv\Scripts\python.exe -m rag_workbench.cli --data-dir data/lanqiao_ds_trial evaluate artifacts/lanqiao_ds/dev.runtime.jsonl
```

上面含占位符的是后续执行模板，不能原样运行。CLI 逐文件导入会逐次重建完整索引；首批小规模先复用，不将批量性能优化混入首版。

**基线验收：** 无服务/索引错误；开发集正样本文档命中率目标 ≥85%；同时报告人工证据完整率，不因单一指标达标而通过。调参结束后冻结配置。

### Task 4：显式接入可配置教学模式

**Files:** 计划新增 `src/rag_workbench/application/teaching.py`、`tests/test_teaching.py`、内容包 `profile/teaching.md`；按需修改现有 `config.py`、`bootstrap.py`、`application/pipeline.py`、`application/prompts.py` 和 `tests/test_pipeline.py`。只在现有界面确有需要时增加模式入口，不重做布局。

**Interfaces:** 以通用可选 `teaching_profile_path: Path | None` 配置选择受信规则，默认 None 保持通用行为。`load_teaching_profile(path: Path | None) -> str | None` 只读显式指定的 UTF-8 配置文件；缺失/无效配置报错，不从知识库正文搜索教学指令。教学规则通过单独 system 消息供生成和通用提示使用，不能覆盖证据来源或 claims JSON 契约。具体配置实现细节在执行前对齐现有 Settings 与 LLM 端口。

- [ ] 先测试默认未启用行为、显式加载、坏路径报错、检索正文无法启用配置，以及现有 JSON/引用校验保持有效。
- [ ] 写入已存在教学约定：中文/C++、先直观例子，再状态/正确性/复杂度/代码/边界；回答长度跟随用户问题，不强制每次完整模板。
- [ ] 练习默认先提示；“先别给答案”时不贴完整解法；要求完整代码后允许展开。课程事实与讲解用的推导示例分清，推导不得伪装为原文。
- [ ] 提示模式、完整解法、多轮追问、缺少题号或数据范围，分别建立对话验收用例；模型实际行为用人工验收确认，不能仅断言提示词包含某句话。
- [ ] 若资料未支持用户所求结论，保留 insufficient_evidence；原有 API 失败仍为 error。

**运行：** `.\.venv\Scripts\python.exe -m pytest tests/test_teaching.py tests/test_pipeline.py -q`。

**验收：** 通用模式回归通过；规则由显式配置生效；实际对话能遵守分级提示并保持来源。若受限于当前 claims 协议不能可靠讲解，记录具体失败例后再决定最小改动，不直接放松证据约束。

### Task 5：留出验收、专用运行目录与扩充

**Files:** 新增 `artifacts/lanqiao_ds/acceptance.md`、内容包 `README.md` 使用说明；必要时新增只设置数据目录与教学配置的专用启动脚本 `start-lanqiao.ps1`，复用 `start.ps1`。

- [ ] 固定内容包/模型/参数；在留出 30 题上执行真实检索和真实 LLM 路由验收，记录 API 错误与费用相关运行范围。
- [ ] 自动化测试只验证工具和协议；课程事实、代码解释、提示泄露与证据支持由人工逐条核对。
- [ ] 记录首版建议目标：正样本检索命中 ≥85%，路由正确 ≥90%，人工回答事实正确 ≥90%，知识库回答引用可追溯 100%。逐条证据是否支持结论另列，不与引用 ID 有效合并。
- [ ] 留出集不足证据题共 10 道，目标全部正确报告不足；教学多轮验收至少 10 组，目标无完整答案提前泄露。全部报告分子/分母、模型版本、运行配置，分项未达标则明确写未通过。
- [ ] 在真实 Streamlit 页面验证上传状态、来源展开、中文和代码排版、多轮提示、重启后检索、错误提示；浏览器验收与单元测试分别记录。
- [ ] 在独立 `data/lanqiao_ds` 导入冻结后的同一包并复验；启动入口显式设置该目录，不能通过替换通用 production 完成切换。运行时文档 ID 由该包重建，禁止盲拷贝数据库。
- [ ] 输出报告：覆盖主题、排除材料、配置与哈希、每项结果、已知问题、演示问题。所有既有数据目录保留；通过恢复原启动入口即可回到原工作台。
- [ ] 第一批通过后再逐批添加树/堆/哈希表、图论、数论和进阶 DP；每批重复资料审核与回归，外部公开教程仅用于已确认缺口。

**代码改动后的常规检查：**

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest -q
```

测试结果、真实 BGE 检索结果、真实 API 回答和浏览器验收分别报告，不互相替代。

## 六、完成顺序与交付边界

1. Task 1～2：交付可核对的首批资料包；不调用付费 API。
2. Task 3：交付检索基线和固定评测集；此时只能称“课程知识库查询”，尚未验收分级教学。
3. Task 4～5：交付能启动的课程学习助手、真实验收记录和后续扩充清单。

若首批真实检索证据不足，优先回到资料质量、单元划分和上下文检查；只有失败案例证明需要时，才另行计划分块器、来源元数据或混合检索的通用改进。

原计划阶段仅编写本文；之后已完成内容包、独立索引、教学配置、真实模型/API、浏览器验收。执行后的使用入口与复现命令见 `knowledge_packs/lanqiao_ds/README.md`。
