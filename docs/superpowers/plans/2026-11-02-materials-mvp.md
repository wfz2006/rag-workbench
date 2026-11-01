# Materials MVP Implementation Plan

**Goal:** 在现有工作台交付文献导入、带证据的实验候选、人工审核及 CSV/完整数据包导出。

**Architecture:** 材料模块独立 SQLite 与原件目录；文本解析沿用 pypdf，Docling 作为可选结构化解析器。抽取遍历主文及关联 SI 的所有文本块，模型只生成候选，批准前验证字段证据。

**Tech Stack:** Python 3.12、Pydantic、SQLite、pypdf、Streamlit、现有 ChatProvider；可选 Docling。

**Spec:** ../specs/2026-11-01-organic-semiconductor-workbench-design.md

## Global Constraints

- 用户已授权开始实施；仅本地开发，不提交/推送或发布，不改封存版本。
- 现有 dirty 工作已备份到 artifacts/materials-mvp-20261102/before.zip。
- 不改正式数据、.env、模型缓存；验证使用隔离目录和模拟接口，不调用付费 API。
- 尚无真实论文；测试合成 PDF 必须标明测试用途，不用于宣称科学准确率。
- 本轮交付首版提取/审核/导出，不实施监督预测、曲线数字化或分子图识别。

## Review Focus

1. 同内容重导入幂等，原件与元数据失败不会产生半成品。
2. 候选中的伪造引文、越库证据和缺少字段证据阻止批准。
3. 长文分批必须覆盖全部块，不静默截断或使用 Top-K。
4. 重跑不能覆盖已批准/拒绝数据；过时审核 revision 不覆盖新修改。
5. 导出仅含已批准记录，完整包包含所有被引用文档、块和字段证据。

## Task 1: 领域和存储（materials_storage）
- [ ] 创建 domain/materials.py、infrastructure/material_repository.py。
- [ ] 测试独立 schema、原件校验、重复、关联 SI、审核修订和幂等。
- [ ] 保存历史与抽取运行记录。

## Task 2: 文献处理与提取服务（root）
- [ ] 创建 infrastructure/literature.py：text/docling 解析，保留页码和可用坐标。
- [ ] 创建 application/materials.py：MaterialsService 的 ingest/extract/add_record/review/export_csv/export_bundle。
- [ ] 先写 tests/test_material_service.py；验证证据、长文分批、失败状态、保护审核、导出完整性。
- [ ] 真实模型操作由页面用户主动触发；只用假模型验证调用与数据路径。

## Task 3: 页面（materials_ui）
- [ ] 创建 ui/materials.py 并接入 app/shell。
- [ ] 无密钥可导入、查看、手工添加、审核和导出。
- [ ] 测试空页、流程、字段证据和下载；验证窄屏与现有导航。

## Task 4: 已知基础缺陷（baseline_fixes）
- [ ] 内容校验失败不释放 holdout，分类与服务错误记账一致。
- [ ] 所选包的实际 provider 与执行/报告模型设置一致。
- [ ] 测试实际生产路径，保持旧测试兼容。

## Task 5: 集成与交付（root）
- [ ] 相关单测、Ruff、既有完整测试；真实浏览器完成一次隔离的 PDF→审核→下载。
- [ ] 新旧页面导航、深浅色与700px布局；保留截图及本轮验证记录。
- [ ] 独立审查修复实质问题；记录尚未验证的可选解析器/真实论文/API路径。
- [ ] 更新本地使用说明，提供启动方式与材料页入口。

## Implementation rulings

- 为尽早形成可用闭环，首版用当前文本 ChatProvider 做显式结构化抽取并严格校验证据；LangExtract、OCR/结构模型留在可替换适配接口后的下一次样本对照，不强行升级主环境。
- 长任务首版同步执行，逐批持久化进度与错误；不冒称进程退出后会自动无损续跑。批量失败记录可供重试。

## 本轮完成记录

- Task 1–4 已完成；Task 5 的相关测试、完整回归、独立审查及隔离浏览器验收已完成。
- 完整 pytest：547 passed / 3 skipped（59.71s）；随后页签状态修复的 8 项 UI 测试再次通过。Ruff 通过。
- 浏览器最终 9 项检查、零 pageerror，包含真实合成 PDF、模拟抽取、批准、实际下载、第二页原文、深浅色、700px及侧栏收放。
- 审查发现并修复：完整数值与逐字段来源校验、单位大小写、重传恢复原件、历史证据闭包、同包 JSON/CSV 快照不一致；相关回归已先观察失败后通过。
- 使用说明：[材料模块](../../MATERIALS_MVP.md)。验证摘要：[首版验证记录](../../MATERIALS_MVP_VERIFICATION.md)；原始截图及测试产物保存在本机artifacts目录。
- 真实论文/真实模型提取精度及真实 Docling/OCR 尚未验收；没有实现 M2–M4 的全量能力。
