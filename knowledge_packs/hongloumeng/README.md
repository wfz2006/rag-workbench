# 红楼梦知识库（本地样例）

本地样例，2026-10-24 建立。收录人民文学出版社 2019 校注本《红楼梦》120 回正文；源 EPUB 将第十七、十八回合为一个文件，因此语料为 **119 个章节文档 + 1 个整理说明**。v2 增加了 pack 级人物别名/亲属词/关系表查询扩展；v3 已做真实 DeepSeek 在线验收。未做独立人工盲评。

## 版权与使用边界

- 原作《红楼梦》为公版作品；本包只收录正文、章节清单和评测元数据，不含校注、排版、来源清单和生成索引。
- `corpus/` 为公版正文的整理文本；校注、版式图片和出版信息未入库。
- `source_manifest.csv`、`data/`、`artifacts/` 只留在本机，不入库。

## 使用

```powershell
cd rag-workbench
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-hongloumeng.ps1
```

默认打开 `http://127.0.0.1:8505`，使用独立数据目录 `data/hongloumeng`。端口可改：`-Port 8510`。

本包不加载教学 profile，加载领域 profile `profile/story.md`。规则覆盖：

- 宝玉、黛玉、宝钗、凤姐等称谓/别号；
- 区分前 80 回与后 40 回，涉及作者/续书归属时区分“本书署名”和“学术推断”；
- 判词、诗词、梦境、太虚幻境不能直接当作已发生事实；
- 人物话语/传言与叙述层事实分开；
- 事实题先直接回答，不用相关事件代替答案；
- 证据不足时拒绝，不用影视改编、脂评或红学考证补写。

检索时还会加载 `retrieval_expansions.json`：把问题中的人物别名、亲属词和关系对象（如“宝玉的母亲”→王夫人）追加到检索查询；原始问题仍原样交给回答生成。扩展表只影响召回，不改变证据门槛。

## 覆盖

| 项目 | 数值 |
|---|---:|
| 回数 | 120 |
| 章节文档 | 119（第十七、十八回合并为一个源文件） |
| 整理说明 | 1（非原文，记录版本与范围） |
| 正文字符 | 875,995 |
| 文本块 | 2,101 |
| 分块 | 500 字符 / 60 重叠 |
| 检索 | Top-K 20，阈值 0.43，候选池 50，相邻窗口 2 |
| 查询扩展 | `retrieval_expansions.json`（人物别名/亲属词/关系表） |
| Embedding | BAAI/bge-small-zh-v1.5，revision `7999e1d3…` |
| 排除的校注脚注 | 3,417 条 |
| 版式缺字/字形图占位 | 144 处（`〔图〕`） |

语料只收叙述正文，排除封面、版权页、目录、序言、校注凡例、校注脚注、后折页和封底。页首 `〔第NNN回〕` 是整理标记，不是原文；`edition.md` 是整理说明，不是原文。

## 目录

```text
knowledge_packs/hongloumeng/
  README.md
  source_manifest.csv       # 1 个 EPUB 来源、SHA-256、选用记录
  settings.json             # 冻结验收配置（start-hongloumeng / CLI 共用）
  chapters.csv              # 119 条章节计划：回次、标题、EPUB href
  generated_manifest.jsonl  # 每个生成文件的 SHA-256、回次与来源 href
  corpus/                   # 唯一导入白名单（已 gitignore）
  profile/story.md          # 红楼梦领域规则，显式加载
  retrieval_expansions.json # 人物别名/亲属词/关系表，用于查询扩展
  eval/v1/                  # 首版 dev/holdout 题集（holdout 已用）
    author_labels.jsonl
    labels.dev.jsonl
    labels.holdout.jsonl
    labels_freeze.json
  eval/v2/                  # 查询扩展复验题集（holdout 已用）
    author_labels.jsonl
    labels.dev.jsonl
    labels.holdout.jsonl
    labels_freeze.json
  eval/v3/                  # 修复前在线验收题集（holdout 已用）
    author_labels.jsonl
    labels.dev.jsonl
    labels.holdout.jsonl
    labels_freeze.json
  eval/v4/                  # 上下文预算靶向回归题集（holdout 已用）
    author_labels.jsonl
    labels.dev.jsonl
    labels.holdout.jsonl
    labels_freeze.json
  eval/v5/                  # 普通分布题集（在线已完成）
    author_labels.jsonl
    labels.dev.jsonl
    labels.holdout.jsonl
    labels_freeze.json
  eval/v6/                  # 引文召回/解释直答题集（dev 已跑，holdout 未跑）
    author_labels.jsonl
    labels.dev.jsonl
    labels.holdout.jsonl
    labels_freeze.json
  review_notes.md           # 提取、清洗、排除与限制
data/hongloumeng/           # 索引、文档副本、会话（本地）
artifacts/hongloumeng/      # 构建、评测、配置冻结与 UI 冒烟报告
```

## 评测结果

### v1 基线（查询扩展前）

| 指标 | dev（20 题） | holdout（14 题） |
|---|---:|---:|
| 检索命中 | 15/15 | 6/10 |
| Recall@20 | 1.000 | 0.600 |
| 证据门槛内命中 | 15/15 | 6/10 |
| 负例阈值命中 | 4/5 | 3/4 |
| 服务错误 | 0 | 0 |

- 检索参数只在 dev 上调过：Top-K 10→20、阈值 0.45→0.43；随后冻结配置，v1 holdout 只跑一次。
- v1 holdout 的 4 个未命中正例：`h02` 黛玉前身、`h04` 宝钗的哥哥、`h06` 元春与宝玉关系、`h10` 袭人原名。它们是早期章节的亲属/身份类事实，检索更容易被后期情感场景挤占。

### v2 查询扩展复验

v2 使用新的 14 题 dev 与 12 题 holdout，不复用 v1 holdout。扩展表加入人物别名（如黛玉→绛珠草、袭人→珍珠）、亲属词（母亲/父亲/哥哥/原名/前身/关系）和主要人物关系边。

| 指标 | v2 dev（10 正/4 负） | v2 holdout（8 正/4 负） |
|---|---:|---:|
| 检索命中，未启用扩展 | 6/10 | — |
| 检索命中，启用扩展 | 7/10 | 8/8 |
| 证据门槛内命中，启用扩展 | 7/10 | 8/8 |
| 负例阈值命中 | 2/4 | 2/4 |
| 服务错误 | 0 | 0 |

- 改善：贾兰、探春、王熙凤等关系题的召回。
- 仍失败：`v2d02` 宝玉母亲、`v2d04` 宝钗母亲、`v2d08` 香菱原名；目标证据块的 RRF 排名仍在 28–64，Top-20 覆盖不到。
- v2 holdout 8/8 不能当作通用能力：8 个正例中 5 个落在第四回，且关系表覆盖了部分人物关系。它说明新增查询扩展在这组题上有效，不等于所有亲属关系都能召回。
- v1 与 v2 的 holdout 都已用于验收；后续改动必须新建未使用留出集。

### v3 在线验收（真实 DeepSeek）

v3 新建 12 题 dev（8 正/4 负）与 12 题 holdout（8 正/4 负），证据按原文核对；v3 holdout 一次性运行，未回头调参。

| 指标 | dev | holdout |
|---|---:|---:|
| 路由准确率 | 12/12 | 12/12 |
| 检索命中 | 8/8 | 7/8 |
| 引用结构有效 | 8/8 | 8/8 |
| 引用命中作者标注章节 | 8/8 | 6/8 |
| 负例拒答 | 4/4 | 4/4 |
| 服务错误 | 0 | 0 |
| 严格答案完整 | 7/8 | 5/8 |

- 在线链路稳定：24/24 路由、16/16 正例引用结构有效、8/8 负例拒答、0 服务错误。
- 严格完整 dev 7/8、holdout 5/8。主要缺口不是“没检索到章节”，而是 Top-K 20 已占满，`retrieval_context_window=2` 没有预算补相邻块：d07、h03、h07 的精确证据都在相邻 2 块内，却没进入证据。
- h01 用第一一九回“父子相见”证明贾琏之父是贾赦，属于有效替代证据；“引用命中作者标注章节”因此低估实际引用质量。
- h06 核心事实正确，但首句是凤姐告状的长引述，“拿毒药”是她的指控而非窗外原话；首句直答和人物话语标注仍不稳定。
- **修复后说明**：上下文预算已改为 `主片段数 + 20`，同一主片段的邻居按真实稠密分优先；单测覆盖了 Top-K 满额和“更相关邻居优先”两种场景。v3 离线诊断中 d07/h03/h07 的精确块均已进入 40 个候选中，但文档级检索指标不变。v3 holdout 已用于修复前验收，修复后的端到端结果见 v4。
- 详细逐题评审见 `artifacts/hongloumeng/v3-online-acceptance.md`。

### v4 上下文预算靶向验收（真实 DeepSeek）

v4 新建 10 题 dev + 10 题 holdout（各 7 正/3 负），其中 5 个正例的答案块位于相邻上下文（rank 8–29），专门验证修复后的窗口预算；holdout 一次性运行，未回头调参。

| 指标 | dev | holdout |
|---|---:|---:|
| 路由准确率 | 10/10 | 10/10 |
| 引用结构有效 | 7/7 | 7/7 |
| 引用命中作者标注章节 | 7/7 | 7/7 |
| 负例拒答 | 3/3 | 3/3 |
| 严格答案完整 | 7/7 | 7/7 |
| 服务错误 | 0 | 0 |

- 5 个相邻上下文目标（d03 rank21、d07 rank8、h01 rank24、h05 rank10、h06 rank29）全部被证据判断采纳并答对。
- v4 是靶向回归集，不能把 7/7 外推为通用准确率；v3 修复前混合集是 dev 7/8、holdout 5/8。
- 详细逐题评审见 `artifacts/hongloumeng/v4-online-acceptance.md`。

### v5 普通分布题集（dev/holdout 在线已完成）

v5 新建 15 题 dev + 15 题 holdout（各 10 正/5 负），按章节分层覆盖第 7–98 回，用于评估普通问答质量和回答简洁度；不是随机抽样，也没有按检索排名筛题。

离线检索：

| 指标 | dev | holdout |
|---|---:|---:|
| 文档级命中 | 10/10 | 10/10 |
| 证据门槛内命中 | 10/10 | 10/10 |
| 负例阈值命中 | 4/5 | 4/5 |
| 服务错误 | 0 | 0 |

在线 dev（真实 DeepSeek，最终提示）：

- 路由 15/15、引用结构 10/10、拒答 5/5、服务错误 0；人工严格正确 **10/10**。
- `v5d05` 已答出《寄生草》——标题块优先规则生效。
- 去掉引用后的正文中位数 26.5 字、均值 34.8 字、最大 82 字；无过长回答。

在线 holdout（真实 DeepSeek，一次性）：

- 路由 14/15、引用结构 9/9、拒答 5/5、服务错误 0；人工严格正确 **8/10**，核心事实正确 9/10。
- `v5h10` 黛玉临终原话拒答：精确原话块不在 40 个候选里（0/40）；`v5h08` 只答“只取其中之一”，没有直接说“只取黛玉”。
- 去掉引用后的正文中位数 56.5 字、均值 54.2 字、最大 78 字；长度控制通过。
- v5 holdout 已使用；后续改动需要 v6 未使用留出集。详见 `artifacts/hongloumeng/v5-online-dev-review.md`、`v5-online-holdout-review.md`。

### v6 引文召回/解释直答（dev 已跑，holdout 未跑）

v6 新建 12 题 dev + 12 题 holdout（各 8 正/4 负），每个半区 2 引文、2 解释、4 普通事实、4 负例，专门验证 h10 原话召回和 h08 解释直答。

- 离线：dev/holdout 正例文档命中 8/8；v6h01 引文块以 context rank23 进入。
- 加硬上限后 dev 通过：路由 12/12、引用 8/8、拒答 4/4、服务错误 0；引文精确 2/2；解释核心点名 2/2；正文中位数 59 字、最大 96 字。
- 硬上限：`max_answer_claims=2`、`max_answer_chars=140`，只作用于 general/meaning；quote/title 不裁。
- 已知小瑕疵：v6d04 未逐字写“只在意黛玉”，但点明黛玉和宝玉心意；严格完整 7/8、核心 8/8。
- v6 holdout 已一次性运行：路由 12/12、引用 8/8、拒答 4/4、服务错误 0；引文 2/2、普通事实 4/4、长度最大 118 字通过；含义题 0/2 完全命中、2/2 部分，严格 6/8。
- 含义题门槛未过：h03 只复述诗句/创作意图，h04 解释为孤寂灰心而非无所牵挂/禅意。v6 holdout 已使用，后续改动需 v7。详见 `v6-online-dev-review.md`、`v6-online-holdout-review.md`。

### 构建与 UI

- 源 EPUB SHA-256：`566ea82c481aaf8ae22fe46227cabb1ff5e5405c397fc7b98890c260803202cf`。
- 同输入二次构建的 `generated_manifest.jsonl` 哈希一致：`01fe155dc59311f9ba94f679138ed68ddeb4968ea19ff66965ec18cc0f42c9d9`。
- UI 冒烟：知识库页显示 **120 文档 / 2,101 文本块 / 索引已就绪**，AI 聊天页正常加载，控制台错误 0；截图见 `artifacts/hongloumeng/ui-knowledge.png`、`ui-chat.png`。
- 工程回归：**413 passed / 2 skipped**（含构建器、查询扩展、引文/证据/解释与长度回归），Ruff 通过。

## 重建

源 EPUB 只读，不修改。构建脚本使用 Python 标准库解析 EPUB，不调用外部转换工具。

```powershell
# 1. 从 EPUB 目录生成可审阅的章节计划（119 个文档，覆盖 120 回）
.\.venv\Scripts\python.exe -m scripts.prepare_hongloumeng_pack `
  --epub '<本地EPUB路径>' `
  --manifest knowledge_packs/hongloumeng/source_manifest.csv `
  --write-plan knowledge_packs/hongloumeng/chapters.csv

# 2. 生成 corpus 与清单（已有不同内容时会拒绝覆盖；确认差异后才用 --replace）
.\.venv\Scripts\python.exe -m scripts.prepare_hongloumeng_pack `
  --epub '<本地EPUB路径>' `
  --manifest knowledge_packs/hongloumeng/source_manifest.csv `
  --chapters knowledge_packs/hongloumeng/chapters.csv `
  --corpus knowledge_packs/hongloumeng/corpus `
  --generated-manifest knowledge_packs/hongloumeng/generated_manifest.jsonl `
  --report artifacts/hongloumeng/build.json

# 3. 文件名映射为导入字节 SHA-256（v5 为当前普通分布题集；v1–v4 保留作历史）
.\.venv\Scripts\python.exe -m scripts.materialize_santi_eval `
  --labels knowledge_packs/hongloumeng/eval/v5/author_labels.jsonl `
  --manifest knowledge_packs/hongloumeng/generated_manifest.jsonl `
  --corpus knowledge_packs/hongloumeng/corpus `
  --output-dir knowledge_packs/hongloumeng/eval/v5

# 4. 独立数据目录导入（PowerShell）
$files = @(Get-ChildItem -LiteralPath knowledge_packs/hongloumeng/corpus -Filter '*.md' -File | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m rag_workbench.cli --settings-file knowledge_packs/hongloumeng/settings.json --data-dir data/hongloumeng ingest @files

# 5. 离线检索评测（不调用付费 API；v1–v5 holdout 都已使用，改动后需 v6）
.\.venv\Scripts\python.exe -m rag_workbench.cli --settings-file knowledge_packs/hongloumeng/settings.json --data-dir data/hongloumeng evaluate knowledge_packs/hongloumeng/eval/v5/labels.holdout.jsonl

# 6. 真实 DeepSeek 在线验收（会产生付费调用；需要 DEEPSEEK_API_KEY 和用户授权）
.\.venv\Scripts\python.exe -m scripts.run_hongloumeng_online_eval `
  --labels knowledge_packs/hongloumeng/eval/v5/labels.holdout.jsonl `
  --author-labels knowledge_packs/hongloumeng/eval/v5/author_labels.jsonl `
  --output artifacts/hongloumeng/v5-online-holdout.jsonl
```

## 已知限制

- 亲属/身份类问法仍受 chunk 级排名限制：v1 holdout 6/10，v2 查询扩展后 dev 7/10；`v2d02`/`v2d04`/`v2d08` 的目标块 RRF 排名仍在 28–64。继续提升需要章节级/分层检索或带出处的关系索引，不能只加同义词。
- 上下文预算已修复：总候选上限为 `主片段数 + 20`（Top-K 20 时最多 40），同一主片段的邻居按真实稠密分优先。v4 靶向集 5 个相邻上下文目标全部答对；v5 普通集 dev 10/10、holdout 8/10。
- 在线答案严格完整：v3 修复前混合集 dev 7/8、holdout 5/8；v4 靶向集均为 7/7；v5 普通集 dev 10/10、holdout 8/10（核心 9/10）。v4 不代表普通准确率；v5 holdout 的 h10 是精确原话块漏召回，h08 是推理表述不够直接。
- 关系扩展表只覆盖主要人物和常见关系；未覆盖的人物/称谓仍退回纯文本检索。v2 holdout 8/8 不代表通用关系能力。
- 校注脚注全部排除；需要注释才能回答的典故、名物问题可能拒答。
- 144 处内嵌字形图被标成 `〔图〕`；涉及这些缺字的原文无法逐字复原（例如第 78 回标题中的罕见字）。
- 第十七、十八回在源 EPUB 中合为一个文件，因此章节文档是 119 个而不是 120 个。
- 没有章节级硬性剧透过滤；领域 profile 只做“不主动展开后续剧情”的软约束。
- 在线验收是单次运行，存在模型随机性；未做独立人工盲评，也未做多轮对话验收。
- v5 普通分布题集 dev/holdout 均已完成在线；dev 严格 10/10，holdout 严格 8/10、核心 9/10。长度控制通过（holdout 最长 78 字）；剩余缺口是精确原话块漏召回（h10）和推理答案表述不够直接（h08）。v5 holdout 已使用，后续改动需 v6。
- 只允许本机使用；不得提交正文、来源清单或索引到公开仓库。

## 报告

- `artifacts/hongloumeng/build.json`、`ingest.json`、`config-freeze.json`、`config-freeze-v2.json`、`config-freeze-v3.json`、`config-freeze-v4.json`
- `artifacts/hongloumeng/eval-dev.json`、`eval-holdout.json`、`eval-dev-review.md`、`eval-holdout-review.md`
- `artifacts/hongloumeng/eval-v2-dev*.json`、`eval-v2-holdout*.json`、`eval-v2-dev-review.md`、`eval-v2-holdout-review.md`
- `artifacts/hongloumeng/settings-baseline.json`、`v2-retrieval-acceptance.md`
- `artifacts/hongloumeng/v3-online-dev*.json*`、`v3-online-holdout*.json*`、`v3-online-acceptance.md`
- `artifacts/hongloumeng/v4-offline-dev.json`、`v4-offline-holdout.json`、`v4-online-dev*.json*`、`v4-online-holdout*.json*`、`v4-online-acceptance.md`
- `artifacts/hongloumeng/v5-offline-dev.json`、`v5-offline-holdout.json`、`v5-offline-report.md`、`v5-online-dev-v4*.json*`、`v5-online-dev-review.md`、`v5-online-holdout-final*.json*`、`v5-online-holdout-review.md`、`config-freeze-v5.json`
- `artifacts/hongloumeng/v5-online-holdout-invalid-drift.*`（并行代码漂移时的作废运行，未纳入验收）
- `artifacts/hongloumeng/v6-offline-dev.json`、`v6-offline-holdout.json`、`v6-offline-report.md`、`v6-online-dev-v2*.json*`、`v6-online-dev-review.md`、`config-freeze-v6.json`
- `artifacts/hongloumeng/ui-knowledge.png`、`ui-chat.png`、`ui-smoke.json`
