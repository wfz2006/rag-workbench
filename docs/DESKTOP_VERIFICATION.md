# 桌面版实际验收记录（0.1.0 历史记录）

当前 0.1.1 的修复与验收以 `REVIEW_2026-10-24.md` 为准。

日期：2026-10-23，Windows x64，本机 WebView2 145，Python 3.12.13。

- `ruff check .`：通过。
- `pytest -q`：138 passed，1 skipped；跳过的是需显式开启的模型集成测试。
- 自包含运行时：Streamlit、pywebview、PyTorch、FAISS、sentence-transformers 与项目包均可导入。
- EXE 验收：真实 Edge Chromium WebView2 窗口，空知识库正常，四页导航通过，无页面异常。
- 移位验收：将软件完整移至 `D:\codexwork\RAG Desktop 移位验收` 后运行成功，含中文与空格。
- 环境隔离：只保留 Windows System32 的 PATH，并设置无效 PYTHONPATH，EXE 仍正常启动。
- 生命周期：强制终止测试启动器后，所属 Python 窗口进程与本地服务均退出，端口关闭；
  再次启动同一工作空间恢复成功，正常关闭后 session 状态文件清理。
- 随包真实 BGE 模型：设置 HF_HUB_OFFLINE=1，TXT/Markdown 导入、重复上传、FAISS 检索、
  重启加载、重建、删除、恢复空库全部通过。仅使用虚构验收资料，未调用远程付费 API。
- 分发隐私：构建使用白名单资源；封装阶段拒绝 user-data、开发 data、.env、SQLite 和日志。
- 图标：内置 imagegen 生成 PNG，导出 16/24/32/48/64/128/256px ICO，应用于 EXE、窗口与 favicon。

验收脚本：`scripts/desktop_backend_acceptance.py`、`scripts/desktop_lifecycle_acceptance.py`，
原始 JSON 记录留在项目 artifacts 内，不放入分发包。

未执行：全新 Windows 虚拟机安装、缺少 WebView2 的机器、所有 DPI/显卡组合、真实付费 API 回答。
此交付为未签名便携版，不是安装器或自动更新系统。
