# RAG Workbench Implementation Plan

**Goal:** 构建领域无关、可空库启动、可独立测试的本地单用户 RAG 系统。
**Architecture:** Presentation → Application → Domain；Infrastructure 实现领域端口。
**Tech Stack:** Python 3.12 / Streamlit / Pydantic / SQLite / FAISS / BGE / OpenAI SDK。
**Spec:** `../ARCHITECTURE.md` 与用户提供的验收清单。

## 约束与自主决策

- 在独立新目录开发，不移动、覆盖已有用户文件；当前目录未找到指定教学文件。
- 使用已安装的 Python 3.12.13 建立项目 .venv，不更改全局环境。
- 用户已明确要求自主设计并实施，故直接执行，不再设置设计审批停顿。
- 空库不加载模型、不创建假向量；无密钥允许管理文档，问答给出配置错误。
- 关闭通用兜底时增加 `insufficient_evidence` 状态，避免把拒答冒充知识库答案。
- 原始上传保留；删除从活动知识库移除，磁盘原件移到回收目录，提供恢复命令。
- 生成采用经结构验证的完整输出；暂不在 UI 流式展示未校验引用的内容。

## Review focus

索引发布失败/断电、同名不同内容、PDF 无文本、同维度不同模型、模型伪造引用、
判断服务故障冒充未命中、长文本被 tokenizer 静默截断、删除后的旧会话引用。

## 顺序与检查

- [x] A：建立配置、领域模型、端口；编写解析与分块边界测试，执行 RED 后实现。
  文件：`config.py`, `domain/*`, `infrastructure/parsing.py`, `chunking.py`。
- [x] B1：SQLite 元数据、原件存储、不可变索引快照；覆盖去重、删除、重建、损坏与发布失败。
  接口：`KnowledgeService.ingest/rebuild/delete/retrieve/stats`，供 UI 与 Pipeline 使用。
- [x] B2：BGE 懒加载与 OpenAI 兼容 Provider；Fake 只在测试中。
  接口：`Embedder.encode_documents/encode_query`、`ChatProvider.complete`。
- [x] B3：独立证据判断、回答生成、引用校验、模式路由；编写命中/未命中/异常测试。
  接口：`RagPipeline.inspect/answer`，返回 Pydantic 模型。
- [x] C：实现聊天、知识库管理、设置、检索调试；持久化会话与非敏感运行配置。
- [x] D：Ruff、pytest、真实 BGE/FAISS 的隔离样例验证、Streamlit AppTest 与浏览器检查。
- [x] E：README、架构、来源、评估脚本与验收记录；最终独立代码审查、修复与复测。

## 验证策略

每组功能先写行为测试；真实 SQLite 与 FAISS，不 Mock 本地持久化。
API 单元测试使用传输层 Mock，Pipeline 使用 Fake LLM/Embedding，付费 API 不自动调用。
真实 BGE 测试显式启用，独立目录运行，结果与 Mock 测试分别记录。
不报告未经实际评测的准确率、性能和真实 DeepSeek 问答成功。

