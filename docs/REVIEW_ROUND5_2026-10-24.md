# 0.1.5：第五轮审查修复记录

修改前备份：`artifacts/review-round5-before.zip`。本轮保留用户 `.env`、知识包原文、正式索引/数据库、模型与旧发行包；测试使用隔离目录和显式 Fake API，未调用付费模型。

## 六组问题处理

1. **Markdown 来源**：使用 markdown-it-py 检查块结构。普通段落仍用行内来源；围栏/缩进代码、表格、分隔线、标题与嵌套块后的来源独立显示，不再修改闭合行。纯正文复制保留代码/空行。新结果与旧记录均按首次引用编号，来源卡片使用相同顺序。核对 [CommonMark 围栏规则](https://spec.commonmark.org/0.31.2/#fenced-code-blocks)，浏览器实测 C++ 代码、后续普通正文、表格和剪贴板分离正确。
2. **失败恢复与标题**：移除首轮失败后恢复自动标题；有后续回合时以剩余第一问命名。通过重命名操作显式设置的标题保留，命名空会话不复用。重试/重新编辑提交时更新同一消息对，保存 ID 和时间位置，不追加重复记录。写入失败保留原失败回合；新答案进入未保存记录。重试旧问题只使用其之前的历史，后续回合保留。
3. **设置状态**：每次保存尝试清理旧反馈，验证失败显示最新错误，不残留旧成功框。旧“需要重建”警告按当前索引状态重新计算，跨页重建后不再显示过期按钮。保存并立即重建在 rerun 后使用缓存解析出的新 Services，不再手动创建临时 Services。浏览器计数证实新配置只构建一次；缓存行为参考 [Streamlit 官方说明](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource)。
4. **扩窗与词法准入**：工作区已有动态扩窗上限改动，本轮保留并补齐已有邻接候选标记逻辑。现在调试主候选保留，新增邻接预算单独最多 20 条，按主命中轮询分配。原问题及有上限的完整同义替换查询之间取最大覆盖率；用于稠密与 BM25 排序的扩展串不参与覆盖率分母。追问改写仍用于检索，但词法准入取当前原问题。没有降低阈值或绕过证据判断。
5. **配置与恢复**：三个可选路径在构造参数、环境与 `.env` 中统一支持空白/disabled→None。回收恢复只编码目标文档、沿用其他活动向量，保留原上传时间/来源元数据；发布失败不留下 pending 条目或新原件。回收副本在提交成功后归档；现存同 ID/活动索引不兼容时明确拒绝。
6. **窄窗与表格**：窄窗“回到最新”按钮进入输入区专用行，作为输入区高度参与滚动锚点；宽屏仍在右侧留白。表头简化为“上传时间”“短 ID”，列宽明确为 140/130px，完整日期含义、ID 与错误在帮助/详情查看，不再把空错误列放大。

## 验证

- `tests/test_review_round5.py`：26 项已通过。覆盖围栏/表格/分隔线/缩进、来源顺序、自动/手动标题、原位重试与写入失败保护、可选路径、原件缺失下恢复、失败发布清理、扩展词法准入及 Top-K=20 的既有邻居标记。
- 完整测试首轮含真实 BGE 离线模型：342 passed（42.87 秒）；最终源码检查 **342 passed（43.45 秒，无跳过）**。`ruff check .` 通过，格式检查 **98 files already formatted**。
- `scripts/browser_round5_regressions.py`：真实 Edge 1440×900 与 700×900，六组交互全部通过。显式测试入口 `tests/browser_support/round5_app.py`，模拟 API 与确定性向量，不进入生产包。记录 `artifacts/review-round5-browser.json`；截图 `review-round5-{markdown,table,invalid-settings,narrow}.png`。
- 浏览器检查实际消息数量、代码块文本、表格单元格、系统剪贴板、错误/成功提示、服务构建计数、跨页重建状态、窄窗按钮归属与几何范围。Windows 剪贴板会将 LF 转为 CRLF，测试只归一化换行，不更改正文。

复现核心测试：

```powershell
$env:RAG_RUN_MODEL_TEST='1'
$env:HF_HUB_OFFLINE='1'
$env:OMP_NUM_THREADS='4'
.\.venv\Scripts\python.exe -m pytest -q --basetemp artifacts/round5-fresh-tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts app.py
```

浏览器使用新的空目录，并另开已装 Playwright/Edge 的终端：

```powershell
$env:RAG_DATA_DIR="$PWD/artifacts/round5-fresh-ui"
.\.venv\Scripts\python.exe -m streamlit run tests/browser_support/round5_app.py --server.port 8531
python scripts/browser_round5_regressions.py --workspace artifacts/round5-fresh-ui
```

## 桌面便携包实测

实际 0.1.5.0 EXE、受限 PATH/无效 PYTHONPATH、隐藏的真实 WebView2，已通过知识库/设置/检索调试/聊天四页导航，强制退出清理、恢复启动、正常关闭，以及外部测试进程独立存活。记录：`artifacts/review-round5-desktop/lifecycle-result.json`、同目录 `smoke-result.json`。

使用包内 Python 3.12.13、随包真实 BGE 与 FAISS，在离线模式完成空库、TXT/Markdown、重复上传、检索、重启、重建、删除；额外真实验证“移走另一份活动文档原件后恢复回收文档”，结果成功且原上传时间保留。验收原件只在独立 profile 内暂存并恢复，不操作正式资料。记录：同目录 `backend-result.json`，付费 API 调用为零。

最终源码与包内模块逐字节比对后封包；模块/资源清单为 `artifacts/review-round5-packaged-source.json`。ZIP 文件逐项 SHA-256、清单与隐私检查结果为 `artifacts/review-round5-release.json`。包不包含 `.env`、用户数据库、正式知识包原文/索引或 Fake Provider。旧发行目录及 ZIP 保留。

```powershell
.\.venv\Scripts\python.exe scripts/desktop_lifecycle_acceptance.py --bundle dist/RAG-Workbench-0.1.5-win-x64 --profile artifacts/round5-fresh-desktop
& '.\dist\RAG-Workbench-0.1.5-win-x64\runtime\python.exe' -I -B scripts/desktop_backend_acceptance.py --bundle dist/RAG-Workbench-0.1.5-win-x64 --profile artifacts/round5-fresh-desktop
.\.venv\Scripts\python.exe scripts/verify_desktop_archive.py dist/RAG-Workbench-0.1.5-win-x64.zip --report artifacts/round5-release-recheck.json
```

## 兼容与边界

- 不改变向量索引签名，0.1.1–0.1.4 索引可继续使用；本轮检索准入变化不等于已经测得真实知识包召回率提升。未重跑付费知识包问答评测，不报告新准确率。
- 同义替换限最多 40 个完整查询，邻接最多追加 20 块；复杂同义组合可能受上限影响。证据判断与引用授权仍必须通过。
- 手动标题标志从本版重命名操作开始记录；旧版没有记录标题来源，无法完美还原历史意图。旧自动标题为空会话但不叫“新会话”的记录不再自动复用。
- 重试会替换失败内容；修改旧问题后不会自动改写后续已成功的对话。保存失败的新结果仍只在当前窗口暂存，刷新前需复制。
- 恢复需要被恢复文档的回收原件与可用活动索引；不会修复已损坏的活动向量或猜测丢失原件。提交后归档失败仍可能保留回收副本，目录中的文档不会再次被覆盖。
- 未测试干净 Windows 虚拟机、所有 DPI/显卡或付费 LLM；便携包未签名。
