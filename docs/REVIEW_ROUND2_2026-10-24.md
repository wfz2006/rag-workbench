# 0.1.2：第二轮外部审查核验与修复

日期：2026-10-24。工作目录 `D:\codexwork\rag-workbench`。无付费 API 调用，未改动用户 `.env`、正式知识库、模型或旧发行包。修改前源码备份：`artifacts/review-round2-before.zip`。

## 逐项处理

| 项目 | 当前代码核验与处理 |
| --- | --- |
| 1 追问识别 | 本轮开始时已经没有审查所述 `_FOLLOWUP_CUES` 子串实现；罗辑、古筝行动、程心、哈希表、快排五个换题示例原本通过。但“再生能源”“那些文件”仍被旧前缀规则误判，“他后来呢”遗漏。改为保守的语法提示，支持他/她指代、连续追问；检索与教学路由共用规则。 |
| 2 批量容错与快照 | 本轮原代码先验证整批再写原件，不会产生审查所述的前两个孤儿原件，但一个解析错误会阻断整批。现返回逐文件 `Document / ImportFailure`；解析拒绝项不落库、不写原件，合法文件一次发布。存量 ready 原件缺失、哈希损坏、超新限制或编码失败时中止发布，保留旧活动索引和 ready 状态。仅 pending/failed 内容错误允许隔离；模型不可用等全局错误仍明确报错。 |
| 3 配置持久化 | 先恢复旧的环境碰撞，再严格校验本次编辑；部分保存不会被旧碰撞卡住。分块参数编辑还校验不带环境变量时的数据库组合。若将产生 400/600 这类冲突，则拒绝本次保存并说明修复路径，不覆盖受环境控制的旧值，不返回虚假的成功。 |
| 4 邻接扩窗 | 所有邻接结果都保留 `original_page`。跨文档只补可信 source_group 相邻页的接壤边界块：前页末块、后页首块；按页距和文档内序号选择，不按全局稠密排序灌入整篇。各 primary 轮流分配剩余额度，总候选仍至多 20。 |
| 5 未保存回答 | 增加有独立 DOM key 的常显复制按钮，JS 严格接受数字消息 key 或 unsaved 数字 key。连续失败保留多条正文；后续提问不再抹去上一条。新建会话排除未保存回合，侧栏保留这类会话，切页后可返回复制。仅在当前浏览器会话存活，刷新/关闭前须复制。 |
| 6 撤回与引用 | `claims=[]` 将最终证据决策置为不足，保留标明“初次判断”的原理由，清空 supporting IDs；路由与调试不再冲突。正文中重复的合法 ID 仅在属于本条 claim.chunk_ids 时去除，由结构化引用统一附加；任何未授权的内联 ID 仍阻止整轮展示，只有 ID 而无正文也拒绝。 |
| 7 Windows 与三体脚本 | TCP 表缓冲不足 122 时有限重试，并校验 LISTEN 状态、行边界、PID 和端口。配置菜单捕获启动失败并提供手动打开路径。三体启动和在线评测共用独立 settings.json；disabled 显式覆盖 .env 的教学路径；在线评测不依赖终端预先导出变量，也不改 UI 数据库。README 改用 PowerShell 文件列表展开而非把 `*.md` 传给 Python。 |

## 未直接采用的建议

编辑器 breakaway 失败后重试普通 `Popen`，会把未保存的编辑器重新放入主程序关闭时终止的作业中。因此只在配置菜单上提供可见的手动编辑退路，不静默降低进程隔离。Windows 的层级作业行为依祖先设置而定，不能笼统理解成所有嵌套 Job 一定拒绝。

核对依据：[Microsoft GetExtendedTcpTable](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)、[Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)、[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。

## 验证记录

新增 `tests/test_review_round2.py`：最初 27 项用例中 20 失败、7 通过；修复后继续增加批量全拒绝、旧 ready 损坏阻断导入、单文件编码错误隔离、真实 PowerShell 参数与环境恢复，共 31 项。

已执行：

- Ruff 全项目检查与源码格式检查。
- 完整 pytest，包含真实 BGE 的离线检索、重启、删除、长问题与分块 token 上限测试。
- 真实 Edge 浏览器 + 独立失败注入入口：两条未保存回答均保留，复制后读取系统剪贴板确认正文，新建会话不复用、切换管理页后可返回复制。记录 `artifacts/review-round2-browser.json` 与同名截图。失败注入只在 `tests/browser_support/persistence_app.py`，正式入口无 Fake Provider。

完成记录：

| 验证 | 实际结果 |
| --- | --- |
| `RAG_RUN_MODEL_TEST=1`、`HF_HUB_OFFLINE=1`、`OMP_NUM_THREADS=4`，完整 `pytest -q` | 216 passed；真实 BGE 测试已启用，无跳过 |
| `ruff check .` | All checks passed |
| `ruff format --check src tests scripts app.py` | 82 files already formatted |
| Edge + 独立失败注入 UI | 剪贴板正文、连续未保存回答、跨页与新建会话恢复均通过；`artifacts/review-round2-browser-final.json` |
| 实际 EXE + 受限 PATH + WebView2 | 四页无异常、强制退出清理、重新启动恢复、正常退出清理、独立外部进程存活；`artifacts/review-round2-desktop/lifecycle-result.json` |
| 包内 Python 3.12.13 + 包内 BGE，断网模式 | 空库、TXT/Markdown、去重、真实 FAISS、重启、重建、删除通过；`artifacts/review-round2-desktop/backend-result.json` |

可复现命令（项目根目录）：

```powershell
$env:RAG_RUN_MODEL_TEST='1'
$env:HF_HUB_OFFLINE='1'
$env:OMP_NUM_THREADS='4'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/desktop_lifecycle_acceptance.py --bundle dist/RAG-Workbench-0.1.2-win-x64 --profile artifacts/round2-fresh-qa
& '.\dist\RAG-Workbench-0.1.2-win-x64\runtime\python.exe' -I -B scripts/desktop_backend_acceptance.py --bundle dist/RAG-Workbench-0.1.2-win-x64 --profile artifacts/round2-fresh-qa
.\.venv\Scripts\python.exe scripts/verify_desktop_archive.py dist/RAG-Workbench-0.1.2-win-x64.zip --report artifacts/review-round2-release.json
```

生命周期验收的 profile 必须为尚不存在的目录。ZIP 校验器会逐文件检查 SHA-256 与完整清单，验证没有用户工作空间、数据库或 `.env`，并生成旁边的 `.zip.sha256`；结果保存在独立 `artifacts/review-round2-release.json`。便携包不包含 tests、私有三体原文/索引或开发机 Key。

## 迁移与范围

- 0.1.1 → 0.1.2 不改变分块/Embedding 索引签名，无需仅因本次升级重建。0.1.0 的索引迁移说明继续有效。邻接候选变更可能影响评测，旧三体分数是历史结果，本轮未重跑付费评测或宣称新准确率。
- 未保存回合未写入数据库，也不会作为下一轮模型历史；没有持久化失败时自动重试或跨浏览器恢复。
- 追问识别仍是保守规则，未实现任意实体消歧或语义关系分析；模糊问题建议写出主语。
- 同页文档内部仍是句末/换行优先的字符分块和 tokenizer 安全切分，复杂表格、完整代码语法、OCR 不在本轮范围。
- 发行包仍是未签名便携版；本机真实 WebView2 与受限 PATH 验证不替代全新 Windows 虚拟机、所有祖先 Job 管理器或不同 DPI 的验收。
