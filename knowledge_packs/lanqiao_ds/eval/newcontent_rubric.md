# 新内容正式评测集说明

## 角色

这是新增内容（树/二叉树/堆、链表、哈希、红黑树/set/map）的独立验收集，共 30 题。标签在首次在线验收前冻结；没有用这批题调整过分块、Top-K、阈值或提示词。若后续用其中失败题修改实现或资料，必须把它降为开发/回归集，并另建未使用的新验收集。

> **2026-10-24 状态变更**：new_14 的失败被用于修改 `GROUNDED_SYSTEM`（增加语言/完整证据约束），因此本集已降为回归/开发集，不再算未触碰的独立验收集。guard 运行的 30/30 只能作为回归证据；新的独立验收必须另建未使用的留出题。状态记录见 `artifacts/lanqiao_ds/newcontent-status.json` 和 `prompt-quality-guard.json`。

## 构成

| 类别 | 题数 | 说明 |
|---|---:|---|
| concept | 10 | 树术语、二叉树、堆、链表、哈希、BST 的直接事实 |
| code | 5 | 树 DFS、二叉树 BFS、堆调整、单链表头插、哈希链地址插入 |
| cross | 5 | 顺序表/链表、树/二叉树、堆/BST、set/unordered_set、priority_queue/queue |
| related_negative | 5 | 红黑树代码、AVL 代码、负载因子、B+ 树、链表环；资料未覆盖 |
| unrelated | 5 | 手机号、教室、身份证、报名截止日、提交邮箱 |

正例 20 题 expected_mode=knowledge_base；负例 10 题 expected_mode=insufficient_evidence（课程入口关闭通用兜底）。

## 标签与证据

- 标签文件：`eval/labels.newcontent.jsonl`
- SHA-256：`4619e04c0e18d7c8d4cfdcdde2bd19fa8fe877a51ae6d18c1670017fac955a07`
- 运行时映射：`artifacts/lanqiao_ds/newcontent.runtime.jsonl`，SHA-256：`2be28d99039a06746a45d06817261a83343dec08abe9bb5aed2875ca10109c23`
- relevant_source_ids 使用逻辑 source_id + `@pNNN` 物理页；运行时通过生成清单/当前映射转成实际入库文件 SHA-256。
- reference_facts 是人工核对要点，只留在作者标签中，不进入运行时 EvalCase。

## 冻结与预检

- 冻结时间：2026-10-23T16:28:05.370690+00:00
- 配置：BGE-small-zh-v1.5 revision `7999e1d3359715c523056ef9478215996d62a620`；500/60 字符；Top-K=8；阈值 0.50；相邻上下文窗口 2；allow_general=false。
- 检索预检（不调用 API）：30 题、正例文档命中 1.0、Recall@8 1.0、负例阈值命中 0.5、服务错误 0。负例阈值命中是近主题候选的相似度高，最终是否拒答由证据判断与路由验收。
- 预检只用于修正题目措辞/页级标签，没有改变模型、分块、Top-K 或阈值。

## 评分

沿用 `docs/EVALUATION.md` 与 `evidence_rubric.md`：文档命中、Recall@K、负例阈值命中、路由、引用有效性、服务错误；事实支持与教学效果需要逐题核对，不能由文档命中代替。在线结果见 `artifacts/lanqiao_ds/newcontent-k8-t0.5-c500-w2-routing.json`。

## 实际在线结果（2026-10-24）

- 真实 DeepSeek `deepseek-flash`，正式 219 文档/378 块索引，固定 K=8、阈值 0.50、窗口 2；耗时约 68 秒。
- 路由 30/30；引用有效 20/20；服务错误 0；检索命中率 1.0、Recall@8 1.0；近主题负例阈值命中 5/10，但证据判断使 10/10 负例拒答。
- 逐题内容核对 30/30 通过：20 道知识库回答覆盖参考事实，10 道负例正确拒答；审核者为本次助手，不是独立人工盲评。未记录 token/费用。
- 报告：`artifacts/lanqiao_ds/newcontent-k8-t0.5-c500-w2-routing.json`；逐题记录：`artifacts/lanqiao_ds/newcontent-review.jsonl`。

## 混合检索改动后的状态（2026-10-24）

- 后续检索代码加入 BM25 + 稠密等权 RRF；标签与冻结配置未改。只读检索确认仍为 30/30 文档命中、Recall@8 1.0、过阈值证据召回 1.0、负例阈值命中 0.5；报告为 `artifacts/lanqiao_ds/newcontent-k8-t0.5-c500-w2-hybrid.json`。
- 2026-10-24 已完成混合检索后的在线复验：真实 DeepSeek `deepseek-flash` 约 68 秒；路由 30/30、引用 20/20、服务错误 0；检索/Recall/过阈值证据召回均 1.0；负例阈值命中 5/10 但 10/10 正确拒答。逐题核对 30/30 事实/拒答通过；new_14 出现俄语词“нельзя”且可选复杂度句缺 O(1)，记为语言质量瑕疵，不判为事实错误。报告在 `artifacts/lanqiao_ds/newcontent-k8-t0.5-c500-w2-hybrid-routing.json`，核对在 `newcontent-hybrid-review.jsonl`。改动前报告已备份到 `pre-hybrid-newcontent/`。
- 正式评测器现同时输出 `eligible_hit_rate` 与 `eligible_recall_at_k`，用于区分“文档被召回”和“有片段过阈值可进入证据”。

## 生成提示质量约束后的回归（2026-10-24）

- 针对 new_14 的俄语词和残缺复杂度句，`GROUNDED_SYSTEM` 增加：正文使用与问题相同语言、中文不混入其他语言；只输出 evidence 中完整、直接支持的陈述，不补全截断句、不加证据外可选常识。旧提示文件备份在 `artifacts/lanqiao_ds/prompts-before-quality-guard.py`，提示字符串哈希 `c6ccea7f…→d5f1e614…`。
- 同题 3 次 guard 复测均无俄语词、无残缺句；O(1) 出现时引用了包含该句的相邻片段。
- 完整 30 题回归（同一 newcontent 集，已不是独立验收）：路由 30/30、引用 20/20、服务错误 0、检索/过阈值证据召回 1.0、负例 10/10 拒答；new_14 已修复；new_12 混入英文普通词 `container`，记为轻微语言质量备注。
- 报告：`newcontent-k8-t0.5-c500-w2-hybrid-guard-routing.json`；核对：`newcontent-hybrid-guard-review.jsonl`；状态：`newcontent-status.json`。新独立验收需另建留出集，不能继续复用本集。
