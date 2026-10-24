# 可复现的 RAG 评估方案

先固定原件、模型 revision、分块参数、Top-K、阈值与代码版本，再建立人工标注测试集。
将开发集用于调阈值，测试集留作独立验收；同一问题不能既调参又作为最终准确率证据。

## JSONL 数据格式

每行一个对象，使用 SHA-256 文档 ID（管理页或 CLI 导入输出可查）：

```json
{"id":"q001","question":"文档中某项要求是什么？","relevant_document_ids":["替换为实际文档SHA256"],"expected_mode":"knowledge_base"}
{"id":"q002","question":"资料没有涉及的某项事实是什么？","relevant_document_ids":[],"expected_mode":"general"}
```

知识库关闭通用兜底时，缺乏依据的标签应为 `insufficient_evidence`。
加入明确答案题、跨片段题、同主题但缺关键信息、完全无关、内部信息无法验证、提示注入和
边界文件；人工检查相关文档确实足以提供完整答案，而不只是主题一致。

## 执行

```powershell
# 不调用付费 API，只计算检索指标
.\.venv\Scripts\python.exe -m rag_workbench.cli evaluate path/to/labelled.jsonl
# 会调用远程 LLM，计算路由与来源指标
.\.venv\Scripts\python.exe -m rag_workbench.cli evaluate path/to/labelled.jsonl --with-routing
```

指定沙盒：在 `evaluate` 前添加 `--data-dir data/sandbox`。
输出 JSON 可保存为报告；包含问题与相关文档 ID，应按私有数据处理，默认不提交仓库。
可用 `scripts/create_sample_eval.py` 生成仅供检查工具流程的虚构样例标签。

## 指标定义

| 指标 | 分子 / 分母或定义 |
|---|---|
| retrieval_hit_rate | 正样本中 Top-K 至少包含一个标注文档的问题数 / 正样本数 |
| recall_at_k | 每个正样本 Top-K 命中的不同相关文档数 / 全部相关文档数，再求宏平均 |
| eligible_hit_rate | 正样本中 Top-K 至少有一个相关文档包含通过阈值（或明确相邻上下文）片段的问题数 / 正样本数 |
| eligible_recall_at_k | 每个正样本中，至少包含一个通过阈值（或明确相邻上下文）片段的相关文档数 / 全部相关文档数，再求宏平均 |
| irrelevant_threshold_hit_rate | 负样本中至少有一个片段通过阈值的问题数 / 负样本数 |
| routing_accuracy | 最终模式与标注相同的问题数 / 所有问题数，服务错误也算不正确 |
| citation_validity | 知识库答案来源非空，且所有来源属于通过阈值的主片段或明确标记的同文档相邻上下文，并在回答引用的问题数 / 知识库回答数 |
| service_errors | 发生服务或结构错误的问题数，独立列出，不能当作未命中 |

检索指标在阈值筛选前计算；错误命中指标单独应用阈值。`recall_at_k` 只说明文档被排序召回，
不等于该文档有片段可进入证据；`eligible_*` 进一步要求至少一个片段通过阈值或被明确标记为
同文档相邻上下文。当前 Recall 是文档级，不是 chunk/span 级。没有对应样本或未执行远程路由时
返回 null，不用 0 伪装为测量结果。启用相邻上下文时，它们不增加主Top-K之外的不同文档集合；
同一主片段的邻接块按真实稠密分优先补入；邻居预算固定为20个，总候选为 `主片段数 + 20`（Top-K 20 时最多40）。小 Top-K 部署会因此获得更多相邻上下文，需在各自题集复验。
保留真实score与context_for，不能把低分的邻接片段写成独立通过阈值。文档级命中仍不足以证明
实际片段包含完整证据。
单元测试中的 1.0 用来验证计算公式，不代表系统真实准确率。

引用有效性只衡量可追溯性，不衡量逐条陈述是否被原文蕴含。后者应人工盲评，或引入独立
事实核验，并记录证据充分性误判、遗漏关键条件、引用不支持陈述等错误。
当前检索为稠密与 BM25 的等权 RRF 混合排序；`RAG_RETRIEVAL_CANDIDATE_POOL` 控制每路候选池。
混合排序不改变阈值依据：`score` 仍是稠密相似度，阈值也按该分数判断。
课程项目已有 30 题 newcontent 与 dev/holdout/test 小样本，但不构成业务准确率、误命中率
或性能成绩，也不能外推为跨教材泛化。

