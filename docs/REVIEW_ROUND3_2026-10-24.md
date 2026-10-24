# 0.1.3：第三轮外部审查核验与修复

日期：2026-10-24。修改前源码备份 `artifacts/review-round3-before.zip`。本轮不改用户密钥、正式文档/索引或历史发行包，不调用付费 LLM API。

## 逐项核验

| 项目 | 处理结果 |
| --- | --- |
| 1 提示与引用校验顺序 | 先验证每条 claim 的授权 ID，统一 hex 大小写、去重并剥离合法内联引用，再校验提示长度、问号与代码限制。未授权内联引用仍立即拒绝。审查引用的 120/260 长度与当前源码不同；当前仍遵循单条提示 180 字符约束。同步及流式路径都验证了合规提示不会多触发一次修复。 |
| 2 导入中止与错误码 | 全局模型异常、存量 ready 原件损坏、发布失败时，新 staged 文件保留为 pending，并标明“索引发布中止”；不会被错误归因为自身文档损坏。后续导入自动重新处理。文档自身编码等失败保留 failed 与原始 error_code；单文件 ingest 原样抛出该码。Document 新字段可读取旧 JSON，无 SQL 表迁移。 |
| 3 否定提示意图 | “不要给提示”“别给提示了”“不用/无需/不需要……”先判 explain；保留“先别给答案”等提示意图。仍为明确规则，不宣称理解任意复杂否定语句。 |
| 4 证据逻辑重试 | 对支持 ID 稳定去重；格式、授权集合与 answerable/支持列表一致性都位于两次调用上限内。首次无效后追加可信纠错指令；最终仍无效返回 invalid_evidence。网络/认证/余额/取消等异常不进入此内容重试。 |
| 5 邻接调试与恢复 | 邻接块填写与 primary 相同算法计算的 lexical_score / lexical_passed，保留真实稠密分数。恢复文档同时恢复原 source_group / source_page，并使来源缓存失效；回收副本在成功后再归档。 |
| 6 跨页任务与时间线 | 新 UI 状态模块在侧栏渲染前回收完成任务，有任务时才使用 Streamlit fragment 定期检查。非聊天页完成也会自动刷新忙状态并保留未保存正文。未保存回合使用提交时间排序、稳定复制 key，并可单独移除；不会随下一轮成功消息倒置。旧 tuple 状态及热重载实例兼容转换。 |
| 7 知识包 CLI | 新增通用 `--settings-file`，stats/search/evaluate/ingest 等均可显式加载相同 JSON。文件值优先于环境和 UI 保存值；不把知识包配置写入用户数据库。JSON 禁止 API Key/未知字段，大小限 64 KiB，文件内相对路径以配置目录为基准。三体启动器、在线评测与 CLI 共用该配置，README 的步骤 3/4 与 stats 都可独立执行。 |

没有采用“把知识包参数写入 ui_settings”的建议，以免把一次评测变成对用户工作空间默认设置的修改。通用命令不会按 data_dir 名称猜测知识包；需显式传入配置文件。

## 测试证据

最初新增 21 个复现用例，修复前全部失败。随后补充配置文件边界、优先级、任务幂等回收、旧状态迁移、重复导入触发 pending 重试，共新增 31 项回归用例。

已完成的源码验证：完整 `pytest -q` 启用 `RAG_RUN_MODEL_TEST=1`、`HF_HUB_OFFLINE=1`、`OMP_NUM_THREADS=4`，结果 **248 passed**（包含真实 BGE，零跳过）。`ruff check .` 通过；`ruff format --check src tests scripts app.py` 为 87 files already formatted。

浏览器验收入口只在 `tests/browser_support/` 中，使用本地 Fake Provider 和显式失败注入；正式源码与便携包没有测试 Provider，也不注入用户数据。实际 Edge 测试包括：

- 第一轮保存失败，第二轮保存成功，聊天按原时间顺序显示；读取系统剪贴板确认正文。
- 移除未保存记录后成功消息仍存在。
- 在系统设置页等待后台任务，测试控制文件释放任务后不操作 UI，任务自动回收，删除按钮恢复可用；返回聊天仍可查看、复制失败回合。
- 重跑第二轮浏览器回归，验证连续两条未保存回答与新建/返回会话仍可用。

证据文件：`artifacts/review-round3-browser.json`、同名截图、`artifacts/review-round3-legacy-browser.json`。周期检查依据 [Streamlit fragment 官方文档](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)，在主线程处理 UI 状态，后台生成线程不调用 Streamlit。

### 便携包实测

0.1.3.0 实际 EXE 已通过受限 PATH 的 WebView2 四页导航、主进程强制退出清理、恢复启动、正常关闭清理；独立外部测试进程在主窗口退出后仍存活。记录：`artifacts/review-round3-desktop/lifecycle-result.json` 与 `smoke-result.json`。

使用包内 Python 3.12.13、包内真实 BGE 模型并开启离线模式，完成空库、TXT/Markdown 导入、去重、FAISS 检索、程序重启、重建与删除验收。记录：`artifacts/review-round3-desktop/backend-result.json`。源码与包内模块路径分别核对，付费 API 调用为零。

复现桌面验收（profile 须为尚不存在的临时目录）：

```powershell
.\.venv\Scripts\python.exe scripts/desktop_lifecycle_acceptance.py --bundle dist/RAG-Workbench-0.1.3-win-x64 --profile artifacts/round3-fresh-qa
& '.\dist\RAG-Workbench-0.1.3-win-x64\runtime\python.exe' -I -B scripts/desktop_backend_acceptance.py --bundle dist/RAG-Workbench-0.1.3-win-x64 --profile artifacts/round3-fresh-qa
.\.venv\Scripts\python.exe scripts/verify_desktop_archive.py dist/RAG-Workbench-0.1.3-win-x64.zip --report artifacts/review-round3-release.json
```

ZIP 独立清单与 SHA-256 核验结果保存在 `artifacts/review-round3-release.json`；校验器同时检查用户数据、数据库与 `.env` 未进入发行包，并生成 `.zip.sha256`。未将私有知识包原文、索引或测试 Provider 放入便携版。

## CLI 使用

从项目根目录的新终端执行（无需先运行启动器或导出专属参数）：

```powershell
.\.venv\Scripts\python.exe -m rag_workbench.cli --settings-file knowledge_packs/santi/settings.json --data-dir data/santi stats
.\.venv\Scripts\python.exe -m rag_workbench.cli --settings-file knowledge_packs/santi/settings.json --data-dir data/santi evaluate knowledge_packs/santi/eval/v2/labels.holdout.jsonl
```

本轮实际执行第一条 stats，得到 156 文档、2236 块、ready、error=null。未重跑三体的付费问答评测或宣称新准确率。评测文件参数是位置参数，不是审查示例里的 `--dataset`。

## 迁移与边界

- 本轮修复不改变索引签名；0.1.1/0.1.2 无需仅因本轮升级重建，0.1.0 旧索引的迁移说明仍有效。
- pending 文档会在下一次导入或重建时重试，不是后台自动重建；自身内容失败的 failed 文档仍需修复后重传或显式重建。
- 未保存消息仍是当前浏览器的临时状态，刷新/关闭会丢失，需先复制；手动移除不等于保存，也不会进入 LLM 历史。旧版未记录时间的临时消息只能保留在转换时的位置；不能还原未知的历史时间。
- 未做干净 Windows 虚拟机、多种 DPI/显卡、所有第三方 Job 启动器或独立人工盲评；桌面包仍未签名。
