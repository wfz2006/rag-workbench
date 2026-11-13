# Windows 一键试用版验收 · 2026-11-02

基线：`cb0e2cc4c72071dc541c396343c2e4264cb26a10`，版本 0.1.10。
仅补齐打包所需的 preview 依赖、动态 EXE 版本、六页 smoke、公开虚构示例和说明。
未提交、未推送、未改 GitHub；开发预览和正式资料保持原位。

## 构建

`.venv/Scripts/python.exe scripts/build_desktop.py --include-model --output dist/RAG-Workbench-Trial-0.1.10-win-x64`

自包含 Python 3.12.13，uv.lock 锁定的 desktop + preview 生产依赖，原生 C# EXE。
内置 BAAI/bge-small-zh-v1.5 公开权重、第三方许可及双页 SYNTHETIC OFET PDF。
不携带 .env、user-data、正式数据库、私人原件或知识包。

## 本轮实际结果

- 构建/桌面相关单测：14 passed，1 skipped。开发 venv 未装 webview 的一项跳过；实际包内真实 EXE 验收通过。
- Ruff、`uv lock --check` 通过。
- `desktop_lifecycle_acceptance.py`：受限 PATH 启动、所属进程强制回收、端口释放、独立外部进程存活、同 profile 重启和正常退出通过。
- 真实 WebView2 六页导航：知识库管理、知识包、系统设置、检索调试、材料文献、AI 聊天；页面异常 0。
- `desktop_backend_acceptance.py`：包内 Python/模块、无网 BGE 与 FAISS 实际检索等 12 项通过；无付费调用。
- `desktop_materials_acceptance.py`：示例双页 PDF 导入、判重、哈希、实际渲染/缩放、字段证据、批准、持久草稿与旧版本保护、重开、CSV/ZIP 等 14 项通过；无付费调用。
- EXE 文件与产品版本均为 0.1.10.0。

首次生命周期检查遇到终止进程后 Windows 待处理 loopback 握手的释放时序，
进程已退出且无存活监听者。验收脚本改为与进程清理相同的有界端口释放等待，
重新执行完整生命周期通过；未因此改动产品关闭逻辑。

结果位于 `artifacts/desktop-trial-20261102/`：

- `lifecycle-profile2/smoke-result.json`、`lifecycle-result.json`、`backend-result.json`
- `materials-profile/materials-result.json`、`example-page-1.png`、`example-page-2.png`
- `materials-profile/materials-example-export.zip`

上述 QA profile 在分发目录之外，不进入试用 ZIP。
打包后运行 `scripts/verify_desktop_archive.py` 对 ZIP 清单、全部文件内容、SHA-256 和个人数据排除规则复核。

最终归档 `dist/RAG-Workbench-Trial-0.1.10-win-x64.zip`：382.0 MiB，解压约 1203.0 MiB。
归档内 31,395 个文件的完整清单与哈希核验通过，报告 `artifacts/desktop-trial-20261102/archive-result.json`。
ZIP SHA-256：`0dacce968ff4d8b42e553c1e75c19453509424a23678a5a62b171f0d04ca1f18`。
校验值另存于 ZIP 同目录的 `.zip.sha256` 文件。

## 验证边界

Windows 10/11 x64 便携目标；本机现有 WebView2 实测，并未完成干净虚拟机兼容矩阵。
材料功能为离线人工审核验收；六页导航为真实桌面 UI 检查。
没有调用远程模型，也没有真实论文准确率、OCR、图表数字化或性能预测验收。
