# 0.1.4：第四轮审查与界面修复

日期：2026-10-24。修改前备份 `artifacts/review-round4-before.zip`。本轮保留用户密钥、已有知识包、模型、正式数据库与旧发行包。新测试全部使用独立工作空间和虚构验收资料，未调用付费 API。

## 后端六项

| 项目 | 实现与边界 |
| --- | --- |
| 删除受缺失原件阻塞 | 从校验过的活动 FAISS 索引重建去除目标文档后的向量快照，SQLite 一次提交 chunks/documents/active。保留原签名，不因当前配置变化给旧向量贴新标签。不读取剩余原件、不加载 Embedding；无活动块的 pending/failed 只移除目录条目。发布失败不切换活动快照，提交后再归档原件。 |
| 回收恢复异常 | 缺少回收原件→original_missing；JSON/模型损坏→trash_corrupt；其他读写异常→restore_failed。原件缺失的回收记录无法凭空恢复。 |
| 生成中会话可达 | 侧栏纳入 answer_jobs；空会话有任务/未保存回答时，用首个问题显示标题并参与搜索。切换后仍能返回停止生成。 |
| JSON 配置边界 | Settings 验证也在异常转换范围内。显式单边分块配置使用默认兄弟值；可选 profile 的空白/disabled 为 None，存储目录空白/disabled 拒绝。非法值统一 invalid_settings_file，不泄露配置内容。 |
| 慢写入与重试 | 状态锁只决定取消/进入 saving 的顺序，磁盘保存移至锁外。saving 中 snapshot 和导航不等待写锁，拒绝迟到取消。保存错误单独记录，不覆盖 debug.reason；未保存回答按 show_debug 展示。 |
| 教学与证据 | 提示重试重新发布 generating_grounded，附可信纠错约束；证据 ID 统一 hex 大小写再去重/授权校验。按中英文问号/感叹号分句，最后明确意图优先。 |

## 六组界面问题

1. **来源可读性**：为固定输入区预留可滚动的底部空间。展开时观察面板实际动画高度，避免过早对齐；短面板完整滚入输入框上方，长面板从顶栏下方开始。手动滚动中断对齐；回到最新按钮移至正文右侧空隙。会话顶栏增加不透明底色。
2. **正文与复制**：来源编号行内显示，不再单独占一段；复制去掉系统附加的引用行与通用模式前缀，保留代码、空行和用户可见正文。通用回答上方仍明确标注“非知识库来源”。原始 AnswerResult、CLI 与引用快照保持可追溯。
3. **搜索与分屏**：常驻显示“筛选中”和清除按钮；高亮搜索入口。调整窗口尺寸或隐藏侧栏关闭浮层，避免悬空挡住正文。
4. **调试与失败恢复**：支持 ID 有明确标题，候选标注最终支持关系、BM25 和关键词准入；无检索/证据的错误不显示空调试框。失败回合支持重试、重填、移除，成功回合不受此移除操作影响。缺 Key 提示配置后刷新重试。
5. **设置反馈**：按钮附近显示反馈并弹 toast，必要时滚到反馈位置。即时变化明确无需重建，模型/分块变化提供立即重建；支持保存并重建、恢复页面默认覆盖值。环境控制项、密钥和数据不清除。
6. **目录与来源信息**：按上传时间从新到旧，同一时间按文件名，避免同批时间戳相同后仍按哈希乱序。状态汉化，表格只放短 ID，完整 ID 单独展开，日期/错误提前。没有固定页码的格式显示字符范围并明确无固定页码，不按文件名猜页码。

## 实际验证

首批新增复现用例 30 项，修复前 29 失败、1 通过；修复后全部通过。随后补充删除签名、成功历史保护、默认设置、代码空白复制等，`tests/test_review_round4.py` 共 **35 passed**。

完整测试启用 `RAG_RUN_MODEL_TEST=1`、`HF_HUB_OFFLINE=1`、`OMP_NUM_THREADS=4`，首轮 **302 passed**（35.08 秒）。封包前保留共享工作区新增的知识包改动后再次执行，最终 **314 passed**（34.91 秒，无跳过，含真实 BGE 离线模型）。Ruff 全仓检查通过，格式检查 **94 files already formatted**。测试总数包含工作区已有及同期知识包测试，不全归因于本轮修复；没有调用远程 LLM 或伪造准确率。

真实 Edge 浏览器测试使用 `tests/browser_support/round4_app.py` 显式 Fake Provider、Fake Embedding 与失败/慢写入注入，和生产入口分离。`scripts/browser_round4_regressions.py` 已通过九组验收：

- 缺 Key 的重填、重试、移除与通用正文剪贴板；
- 文档排序、短 ID 与汉化状态；
- 行内来源、正文剪贴板、动画展开、底部可滚动空间与顶栏底色；
- 设置反馈在视口内、区分重建、恢复默认值；
- 搜索状态、清除和 700px 分屏关闭浮层；
- 正在生成的首轮会话可切回、停止；
- 慢保存期间可以新建会话；
- 未保存回答仍可查看证据 ID、支持候选、BM25，复制正文；
- 深色主题渲染。

浏览器尺寸 1440×900 与 700×900。记录：`artifacts/review-round4-browser.json`；截图同目录 `review-round4-{kb,sources,settings,narrow,dark}.png`。这是真实浏览器交互验证，语义回答由显式测试 Provider 模拟；真实 BGE/FAISS 独立测试。

复现（项目根目录，使用新的测试目录避免混入历史记录）：

```powershell
$env:RAG_DATA_DIR = "$PWD/artifacts/round4-fresh-ui"
.\.venv\Scripts\python.exe -m streamlit run tests/browser_support/round4_app.py --server.port 8529
# 另一终端，使用已安装 Playwright 与 Edge 的 Python
python scripts/browser_round4_regressions.py --workspace artifacts/round4-fresh-ui
```

## 便携包实测

最终 0.1.4.0 EXE 在受限 PATH 和无效 PYTHONPATH 下，通过真实隐藏 WebView2 的四页导航、强制退出进程/端口清理、相同工作空间恢复启动、正常关闭清理；独立外部测试进程在退出后仍存活。记录：`artifacts/review-round4-desktop-final/lifecycle-result.json`、同目录 `smoke-result.json`。

包内独立 Python 3.12.13 和随包真实 BGE 在离线模式下通过空库、TXT/Markdown、重复导入、FAISS 检索、重启加载、重建与删除。实际模块路径来自 `dist/RAG-Workbench-0.1.4-win-x64/runtime/Lib/site-packages/rag_workbench`，没有借用源码环境。记录：同目录 `backend-result.json`，付费 API 为零。

最终封包前逐字节比对源码/资源与安装模块，保存 `artifacts/review-round4-packaged-source.json`；ZIP 独立清单、隐私文件排除与 SHA-256 校验记录为 `artifacts/review-round4-release.json`。`.env`、正式知识包原文/索引、数据库与测试 Provider 不进入发行包。旧版目录和 ZIP 保留。

```powershell
.\.venv\Scripts\python.exe scripts/desktop_lifecycle_acceptance.py --bundle dist/RAG-Workbench-0.1.4-win-x64 --profile artifacts/round4-fresh-desktop
& '.\dist\RAG-Workbench-0.1.4-win-x64\runtime\python.exe' -I -B scripts/desktop_backend_acceptance.py --bundle dist/RAG-Workbench-0.1.4-win-x64 --profile artifacts/round4-fresh-desktop
.\.venv\Scripts\python.exe scripts/verify_desktop_archive.py dist/RAG-Workbench-0.1.4-win-x64.zip --report artifacts/round4-release-recheck.json
```

## 兼容与限制

- 不改变索引签名，已有 0.1.1–0.1.3 索引无需仅因升级而重建；改变模型/分块仍需重建。
- 活动索引本身损坏时，不能仅靠缺失的原件重建向量；需要恢复原件/索引备份。已删除但没有原件的回收记录会给明确错误。
- saving 是提交边界，保存阶段不能停止；这避免已写入的结果被迟到取消覆盖。后台 SQLite 超时仍可能等待，但不持有前端读取状态的锁。
- 未保存记录仍为当前浏览器会话的临时状态，硬刷新/关闭会丢失，请先复制。移除失败/未保存回合不等于持久化。
- 来源布局通过真实 Edge 验证，不宣称覆盖所有浏览器、DPI 和窗口尺寸。未做干净 Windows 虚拟机、付费 LLM、人工事实准确率评测；便携包未签名，不含 OCR 或安装向导。
