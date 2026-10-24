# 0.1.8：第八轮审查修复记录

修改前备份：`artifacts/review-round8-before.zip`。保留真实环境配置、知识包/原件/活动索引/会话、模型和旧发行包。测试目录均位于 artifacts；没有调用付费 API。

## 修复

1. **重试锚点按生成任务进入、完成后释放。** 仅在新的 generation key 出现时设置替换目标，不在每帧重新接管。运行节点卸载到完成节点挂载之间仍保留目标；完成节点挂载后做至多一次收尾对齐并清空目标。显式 ↓、手动回到底部、Enter、鼠标发送和鼠标重试均能更新跟随状态。永久存在的 turn-anchor 只负责定位，不再成为永久滚动目标。
2. **操作反馈与索引状态分开保存。** saved_feedback 与重建结果保存对应的 index_status；相同状态下原样保留“设置已保存”“索引重建完成”“已恢复页面默认设置”等文字，仅在实际状态变化时刷新通用索引提示。保存错误仍保留，异常索引在无保存历史时仍提供重建入口。
3. **共享前置词归一化。** is_followup 与 coverage_focus 使用同一个 followup_core，仅在连接词/时间词/“为什么”后确为代词时去掉前缀，支持“那他…”“后来他…”“为什么他…”及组合形式；不将“后来林冲…”“为什么罗辑…”等明确新主语的独立问题强行拼入旧话题。完整问题仍用于检索/证据判断，词法焦点不改变最终事实约束。
4. **单轮多行问题保留全文题型。** 无 coverage_question 时 retrieve 按完整 question 判断 quote，不再只读最后一行；多轮则继续按显式传入的当前问题判断，与 pipeline payload 一致。

报告中“这些前置词问句已被 is_followup 判为追问”不完全符合当前代码：本轮实测三类示例在 0.1.7 中均未命中，因此同步修了上下文路由和覆盖率层，而非只修改覆盖率。

## 验证证据

- 新增 `tests/test_review_round8.py` 共 14 项：修复前 **11 failed / 3 passed**，修复后通过；连同第 5、7 轮相关测试 **63 passed**。浏览器也在修复前复现了重试完成后点击 ↓ 的失败。
- 最终完整真实离线 BGE 测试 **408 passed / 52.09 秒**，Ruff 检查及格式检查通过（109 个文件）。结果：`artifacts/review-round8-tests.xml`。总数还包含同期新增的一条已有 prompts 模块测试，没有把所有增量归为本轮新增。
- 真实 Microsoft Edge：1440×900、700×900，九组通过。覆盖原位编辑、生成/收尾位置、完成后 ↓、真实滚轮回到底部、鼠标发送/重试、操作反馈前缀、跨状态同步、容器代码/剪贴板与窄窗高度。API/向量为明确模拟。记录：`artifacts/review-round8-browser.json`；截图 `review-round8-{after-jump,edit,retry,markdown,narrow}.png`。
- 完成后 ↓ 的尾部锚点误差约 **-0.17px**；鼠标发送后的尾部约在锚点上方 **5.23px**，处于可视范围。浏览器回归按内容可见位置核对，不要求中间内容高度改变后 scrollTop 数值仍完全相同，也不把预留滚动空间当成回答必须贴住的底边。
- 真实 BGE 与红楼梦既有索引的隔离副本复验：单行“黛玉临终前最后喊的是什么？”及再加一行“请结合原文说明。”均召回原话块 `587e36c486f4…`，context_for 非空且 eligible=true。证据：`artifacts/review-round8-real-retrieval.json`。未改动正式知识库，此结果只说明这条离线召回路径，不代表回答准确率或完整 v6 评测。
- 实际 EXE 验收通过：受限 PATH、四页切换、强制退出清理/恢复、正常退出清理、主窗口退出后外部程序继续存活。证据：`artifacts/review-round8-desktop/lifecycle-result.json`。
- 包内 Python 3.12.13 + 真实 BGE/FAISS 离线验收通过：空库、TXT/Markdown、去重、检索、重启、重建、删除、其他原件缺失时恢复及保留上传时间。证据：`artifacts/review-round8-desktop/backend-result.json`。离线 Hub 提示仅表示复用包内缓存，没有下载或付费调用。

```powershell
$env:RAG_RUN_MODEL_TEST='1'; $env:HF_HUB_OFFLINE='1'; $env:OMP_NUM_THREADS='4'
.\.venv\Scripts\python.exe -m pytest -q --basetemp artifacts/round8-fresh-tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts app.py
```

封包前生产源码/静态资源与包内已安装版本逐文件核对 SHA-256，记录 `artifacts/review-round8-packaged-source.json`；ZIP 的清单/逐文件哈希和隐私目录排除检查记录 `artifacts/review-round8-release.json`，完整 ZIP 校验码另附 `.zip.sha256`。

## 边界

- 没有改变索引签名，0.1.1–0.1.7 无需仅因升级重建。未调用付费 API、运行完整 v6 评测或宣称回答准确率提升。
- 连接词/指代仍是保守语法规则，不支持任意复杂主语、省略或多实体消歧。引文扩展仍受同文档、4 块上限、上下文预算与剧透边界约束，最终需核验证据。
- 浏览器模拟不能替代真实 API、全部输入法/DPI/显卡或干净 Windows 虚拟机的验收；便携包未签名。
