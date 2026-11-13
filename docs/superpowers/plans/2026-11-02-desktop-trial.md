# Windows 一键试用包 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; no delegation is required for this packaging task.

**Goal:** 交付解压后双击 EXE 可启动的 Windows x64 便携试用 ZIP。

**Architecture:** 复用现有 C# 启动器、自包含 Python、pywebview 和独立 user-data。补齐 preview 可选依赖、当前版本信息和六页桌面验收；保留正式项目与开发预览，不携带个人配置、数据库或资料。

**Tech Stack:** Python 3.12、uv.lock、.NET Framework、WebView2、Streamlit、PDFium、BGE。

**Spec:** 本轮用户要求“打包一个一键试用版”；功能说明见 docs/MATERIALS_MVP.md。

## Global Constraints

- Windows 10/11 x64；需要 .NET Framework 4.6.2+ 与 WebView2。
- 不改 GitHub 封存版本，不自动提交或推送。
- 保留 .env、正式数据、模型缓存和既有构建；新输出目录拒绝覆盖。
- 内置公开 BGE 权重；AI 提取与回答需用户自行配置 API Key。
- 不预装论文或虚构实验数据库；示例文件必须标注 SYNTHETIC。

## Review Focus

- 未安装 Python 的受限 PATH：通过真正的 EXE 启动并清理所属后端。
- 新材料页 PDF 预览：包内必须含 PDFium，且验证本地示例 PDF 渲染。
- 已使用的工作空间：不得进入 ZIP，逐文件哈希验证归档。
- 关闭与重新打开：验证 Job Object、独立编辑器进程及同 profile 恢复。
- 无 API Key：不进行付费调用，说明可离线完成的操作与远程功能边界。

## Task 1: 构建配置和试用内容

**Files:** scripts/build_desktop.py、tests/test_desktop_packaging.py、src/rag_workbench/desktop.py、docs/DESKTOP.md、examples/materials_trial/。

- [x] 为生产依赖导出包含 desktop + preview、EXE 版本随 pyproject 同步补充回归测试。
- [x] 修复构建器；桌面 smoke 依次打开全部六页。
- [x] 更新使用说明，加入明确虚构的双页 OFET PDF 和手工核对步骤。
- [x] 运行相关 desktop/packaging 测试与 Ruff。

## Task 2: 构建并验收

**Files:** dist/ 新便携目录与 ZIP；artifacts/desktop-trial-20261102/ 验收结果。

- [x] 安装锁定生产依赖到独立 Python；复制公开模型，生成第三方与构建来源清单。
- [x] 使用包内运行时完成离线 BGE 和材料 PDF/候选/草稿/导出检查。
- [x] 运行真实 WebView2 六页 smoke、强制关闭后端回收与重启验收。
- [x] 封存原始便携目录为 ZIP，验证全部清单和哈希，输出校验文件与体积。
- [x] 报告可直接双击的路径及使用前提；不把本机验证表述为所有 Windows 系统兼容。
