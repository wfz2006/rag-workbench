# 蓝桥杯与数据结构课程学习助手

首批知识包与专用工作空间已建立，2026-10-24 完成扩充、混合检索优化与验收记录更新。

## 使用

当前本机入口：**http://127.0.0.1:8502**。

需要重新启动时，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-lanqiao.ps1
```

该入口使用 `data/lanqiao_ds`、显式教学配置、关闭通用兜底和相邻上下文窗口2。原通用入口仍是 `start.ps1`；两个入口使用不同数据目录。密钥只保存在本机 `.env`，已配置，不在本文展示。

可以这样提问：

- “课程如何区分栈顶与栈底？请给出处。”
- “二分课件如何计算mid，为什么要注意区间边界？”
- “分巧克力的输入范围和切割要求是什么？”
- “我在练习数组栈的判空，先别给答案，只给一个引导问题。”
- 接着说“再给一个提示”，或“现在请完整讲解，可以给关键代码”。

答案中的来源可以展开，查看实际片段。页文件名中的 `pNNN` 对应原PDF第NNN个物理页；Markdown来源当前没有结构化的Chunk.page，页面可能显示“页码—”，应结合文件名、片段页号和清单核对。

## 覆盖范围

| 类型 | 数量 | 内容 |
|---|---:|---|
| 课程知识单元 | 27 | 顺序表/vector、栈队列、排序与二分、递归搜索、基础DP与01背包、树/二叉树/堆、链表、哈希、红黑树/set/map |
| 课程页文件 | 199 | 从上述单元按物理页整理 |
| 蓝桥杯题目 | 10 | 穿越雷区、机器人繁殖、饮料换购、最大公共子串、日期问题、包子凑数、分巧克力、递增三元组、日志统计、全球变暖 |
| 源码示例 | 10 | 栈队列模拟与STL示例、插入/选择/冒泡/快速/归并排序、有效括号 |

合计219份入库文件，当前378个文本块。题目归属字段来自本地目录标签，未据此声称经过官方试卷核验；未知年份留空。

课程正文与源码来自 `D:\bite blue bridge` 及已有 `D:\ccode\算法\算法知识库`。所有49个已选来源及对应原件的哈希复核一致，原始资料未被修改。源码仍标记为教学参考，未进行编译、运行或在线判题。

2026-10-24 新增树结构批次：数据结构篇第 6 章树（p1–12）、第 7 章二叉树（p1–22）、第 8 章堆和 `priority_queue`（p1–28），共62个页文件。第 8 章 p29 仅剩 `}94` 提取残片，未选入；PDF 的康熙部首兼容字形在构建时按 NFKC 规范化，公式和图示版式仍需回看原页。

2026-10-24 第二批新增：数据结构篇第 3 章链表（p1–26）、第 10 章哈希表（p1–25）、第 9 章红黑树与 set/map（p1–18、20–32、34–47），共5个来源、96个页文件。第 9 章 p19/p33 原 PDF 没有可提取文本，但含 AVL/红黑树旋转过程插图；p48 仅为重复页脚 logo，未选入。该章按上/中/下三段登记（逻辑上仍计1个知识单元），原始正文未改。

## 内容和配置分离

- `corpus/`：唯一允许导入的正文目录。
- `source_manifest.csv`：来源路径、页码、输入/原件哈希、选用与排除记录。
- `profile/teaching.md`：显式加载的教学规则，不能靠上传文档启用。
- `eval/`：评测问题与评分要点，不进入知识库。
- `review_notes.md`：清洗、歧义和局限；源码前的“整理说明”是对实际源码逐句整理的说明，不冒充课件原文。

当前冻结配置：BGE-small-zh-v1.5，revision `7999e1d3359715c523056ef9478215996d62a620`；500字符/60重叠；Top-K=8；阈值0.50；相邻上下文窗口2；候选池50；DeepSeek `deepseek-flash`。检索采用稠密 Top-50 与 BM25 Top-50 的等权 RRF 混合排序；阈值和 `score` 仍按稠密相似度，混合顺序下分数不保证单调。

相邻上下文仅从已通过阈值的主片段所在文档补取，保留每个片段自己的相似度和真实ID，候选总量最多20。低于阈值的相邻片段会明确标识，不伪称独立通过阈值。

## 验证记录

- 扩充前索引（61 文档/107 块）的 30 题留出/回归：正样本20/20命中；路由30/30正确；负样本10/10报告资料不足；20个知识库答案引用均可追溯。该结果不适用于当前 219 文档索引，不能冒充扩充后的独立验收。
- 2026-10-24 树结构批次：trial 库 123 文档/219 块；旧 dev 30 题检索命中率 1.0、Recall@8 0.975、负例阈值命中 0.2、服务错误 0；树/二叉树/堆 9 个新问题检索 9/9 命中。
- 2026-10-24 链表/哈希/set-map 批次：trial 库 219 文档/378 块；旧 dev 30 题命中率 1.0、Recall@8 0.95、负例阈值命中 0.2、服务错误 0。新增内容使 dev_01/dev_16 的标注页掉出 Top-8；K=10/12 仍为 0.95，K=15 才回到 0.975，为控制上下文预算未改 K=8。链表/哈希/set-map 9 个新问题检索 9/9 命中。
- 2026-10-24 新内容正式在线验收：30 题（概念10/代码5/跨片段5/近主题负例5/无关5），标签冻结 SHA-256 `4619e04c…`；真实 DeepSeek `deepseek-flash` 运行约 68 秒；路由 30/30、引用 20/20、服务错误 0；检索命中率 1.0、Recall@8 1.0；近主题负例阈值命中 5/10，但 10 道负例全部正确拒答；逐题内容核对 20/20 知识库回答通过、10/10 拒答正确。内容核对由助手完成，不是独立人工盲评；new_16/new_20 的 reference_facts 含题面未要求的额外点，答案覆盖了所问内容。
- 2026-10-24 混合检索优化（无付费 API 的检索层验证）：在 trial/正式库同为 219 文档/378 块上，加入 BM25 与稠密向量的等权 RRF，Top-K 仍为 8、阈值 0.50、候选池 50。dev Recall@8 0.95→1.0、过阈值证据召回 0.95→0.975；test 由 0.975→1.0；holdout 保持 1.0；dev MRR 0.849→0.950、test 0.908→0.975、newcontent 0.867→0.908。负例阈值命中未恶化（dev 0.2、holdout 0.1、test 0.4、newcontent 0.5）。全量测试 143 通过、1 项可选真实模型测试跳过。证据见 [hybrid-retrieval-optimization.json](../../artifacts/lanqiao_ds/hybrid-retrieval-optimization.json) 和四份 `*-hybrid.json` 报告。
- 2026-10-24 混合检索后的在线复验：冻结 newcontent 30 题，真实 DeepSeek `deepseek-flash` 运行约 68 秒；路由 30/30、引用有效 20/20、服务错误 0；检索命中/Recall@8/过阈值证据召回均 1.0；近主题负例阈值命中 5/10，但 10/10 正确拒答。逐题核对 30/30 事实/拒答通过；new_14 出现俄语词“нельзя”且可选复杂度句缺 O(1)，记为语言质量瑕疵，不判为事实错误。报告见 [hybrid 路由报告](../../artifacts/lanqiao_ds/newcontent-k8-t0.5-c500-w2-hybrid-routing.json)、[逐题核对](../../artifacts/lanqiao_ds/newcontent-hybrid-review.jsonl) 和 [summary](../../artifacts/lanqiao_ds/newcontent-hybrid-review-summary.json)。该次是改动前提示的独立在线运行；之后因 new_14 被用于修改提示，newcontent 已降为回归集。
- 2026-10-24 生成提示质量约束（回归复验）：`GROUNDED_SYSTEM` 增加“与问题同语言、中文不混语言；只输出完整且由 evidence 直接支持的陈述，不补全截断句”。同题 3 次复测无俄语词/残缺句；完整 30 题回归路由 30/30、引用 20/20、服务错误 0，new_14 已修复；new_12 混入英文普通词 `container`，记为轻微质量备注。旧提示哈希 `c6ccea7f…`、新提示哈希 `d5f1e614…`，旧文件备份在 `artifacts/lanqiao_ds/prompts-before-quality-guard.py`；报告与状态见 `newcontent-k8-t0.5-c500-w2-hybrid-guard-routing.json`、`newcontent-hybrid-guard-review.jsonl`、`newcontent-status.json`。该 30/30 是回归证据，不是新的独立验收。
- 10组真实教学对话：每组提示→再提示→完整讲解，共30回合，本轮均正常返回知识库回答。20个提示回合均符合单问句/长度/不含完整代码等结构检查，并已检查本轮显示内容。
- 当前测试集 144 通过、1 项可选真实模型 pytest 跳过；验收时记录为 125 通过。混合检索层已验证，并已完成同一冻结集的 30 题真实 API 在线复验；随后针对 new_14 增加生成提示质量约束并做了完整回归（new_14 已修复，new_12 有轻微英文词备注）。因 new_14 被用于改提示，newcontent 已降为回归集，新的独立验收需另建留出题。未重跑真实浏览器验收。
- 扩充前的真实Edge检查：资料概览、提示、追问、来源展开、管理页、390px宽度无页面横向溢出。

上述是本知识包的小样本结果，不是对所有问题的准确率保证。内容核对由本次助手完成，不等同独立人工盲评。扩充前的失败与修复记录保留在 [验收报告](../../artifacts/lanqiao_ds/acceptance.md)，两批扩充证据见 [tree-batch-verification.json](../../artifacts/lanqiao_ds/tree-batch-verification.json) 和 [linked-hash-batch-verification.json](../../artifacts/lanqiao_ds/linked-hash-batch-verification.json)；新内容正式验收见 [newcontent 路由报告](../../artifacts/lanqiao_ds/newcontent-k8-t0.5-c500-w2-routing.json)、[逐题核对](../../artifacts/lanqiao_ds/newcontent-review.jsonl) 和 [冻结记录](../../artifacts/lanqiao_ds/newcontent-freeze.json)；混合检索证据见 [hybrid-retrieval-optimization.json](../../artifacts/lanqiao_ds/hybrid-retrieval-optimization.json)。

## 重新构建和评测

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_lanqiao_pack --source-root 'D:\ccode\算法\算法知识库' --manifest knowledge_packs/lanqiao_ds/source_manifest.csv --output knowledge_packs/lanqiao_ds/corpus --report artifacts/lanqiao_ds/build.json

# 不调用远程API。参数必须与专用索引配置一致。
.\.venv\Scripts\python.exe -m scripts.run_lanqiao_eval --split dev --data-dir data/lanqiao_ds --top-k 8 --threshold 0.5 --chunk-size 500 --context-window 2

# --with-routing 会调用真实远程API；留出集不能用于反复调参。
```

来源或生成内容发生变化时工具会拒绝覆盖已有不同输出；先审核差异并备份旧包，再显式构建新版本。改分块后需明确加 `--rebuild`，不混用旧索引。树结构、链表、哈希、红黑树/set/map 已纳入扩充；外部公开教程、树图进阶专题、OCR和旧Word转换留作后续扩充。

## 已知限制

PDF图示和公式版式没有可靠恢复；对依赖图示的内容回看原页。新增树/二叉树/堆、链表、哈希、红黑树/set/map 课程页同样保留提取行号，公式/图示版式未复原；第 9 章 p19/p33 是 AVL/红黑树旋转插图、p48 仅为页脚 logo，均未入库。课程代码未编译、未运行、未在线判题。模型的充分性判断、分级提示和生成仍可能波动；校验保证来源ID合法，不能数学证明答案正确。生成提示质量约束后的 30 题回归由助手核对，不是独立人工盲评；new_14 的俄语/残缺句已修复，但 new_12 仍有轻微英文词备注。newcontent 已被用于改进提示，因此已降为回归集，不能继续冒充独立验收；独立验收需另建未使用留出题。提示模式会最多重新生成一次，仍不合格则报错，不展示被拦截的输出。

尚未处理工具中未来标题含方括号时的glob转义，本批已审标题不含该字符。课程正文和来源清单设为本地数据，不默认提交到远端。
