# 实际验收记录

日期：2026-10-23，Windows，本次新建项目专用 `.venv`，Python 3.12.13。
原目录的用户文件没有删除、迁移或覆盖；未找到指定教学文件可复用。

**后续 UI 升级记录**：Claude Desktop 风格改版后，全量测试为 **67 passed**，
包括真实 BGE；桌面/窄屏、浅深主题、会话操作、顶栏导航与输入区跳转通过浏览器检查。
下表保留初次交付记录。随后按 Code 会话工作台重排布局，本轮默认测试 **66 passed, 1 skipped**，
新增长输入与消息滚动的实际浏览器检查，见 [UI_CODE_LAYOUT.md](UI_CODE_LAYOUT.md)。
初次 UI 移植与许可记录见 [UI_REDESIGN.md](UI_REDESIGN.md)。

## 已执行

| 检查 | 实际结果 |
|---|---|
| `uv sync --python 3.12 --frozen --extra dev` 对应的项目环境安装 | 安装成功，锁文件可用；未更改全局 Python |
| `uv lock --check` | 通过 |
| `uv pip check --python .venv/Scripts/python.exe` | 84 个已安装包兼容 |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 全部 Python 文件已格式化，通过 |
| `python -m compileall -q src app.py` | 通过 |
| `python -m pytest -q` 默认运行 | 65 passed，1 个真实模型测试按默认策略跳过 |
| `RAG_RUN_MODEL_TEST=1 python -m pytest -q` | **66 passed，19.11 秒**，包含真实 BGE |
| `uv build` | sdist 与 wheel 构建成功 |
| `start.ps1 -Port 8517` | 真实 Streamlit 服务已启动，绑定 127.0.0.1 |

上述耗时是本次运行记录，不是性能基准或响应速度承诺。

## 自动化覆盖

TXT/Markdown 清洗、普通 PDF 页码、扫描 PDF 拒绝、类型与大小校验；分块边界/重叠/稳定 ID；
真实 SQLite 和 FAISS 持久化与重启；重复内容与同名异文；删除/恢复/重建；
索引缺失/损坏/篡改、不同模型同维度、分块配置不兼容、无效零向量；
写入失败重试、文件发布失败、SQLite 事务中途约束失败回滚；
空库、阈值未通过、语义相关但缺证据、无效证据 JSON、虚构引用、禁用兜底；
真实 SDK 的模拟 HTTP 401/402/403/429/500/422、网络断连/超时、输出截断；
配置优先级/大小写/有效配置组合、会话持久化/超长历史、新建会话、旧来源快照；
沙盒清除保护、CLI 编码与错误退出码、评估公式和四页 AppTest。

## 真实模型与浏览器

- 实际下载 `BAAI/bge-small-zh-v1.5`，缓存 revision 为
  `7999e1d3359715c523056ef9478215996d62a620`。
- 可选真实模型测试完成导入、语义排序、重启检索和删除后的检索，未使用假向量。
- 使用真实 Streamlit 页面，在 `data/browser-qa` 隔离库上传了真实 TXT、MD、PDF 字节，
  点击导入并使用真实 BGE 构建索引，3 份文档/3 个文本块 ready。
- 调试“样例记录保管多久？”返回实际 sample.txt，余弦分数约 0.6946；
  另两份候选低于默认阈值，展示了真实原文、Chunk ID 和分数。
- 未配置密钥时，调试页正确返回服务配置错误，仍能查看已完成的检索。
- 页面删除 interface.md 后，统计和索引为 2 份文档/2 个文本块，原件进入回收目录。
- Edge 无头浏览器实际验证桌面页面导航，以及 **1440px / 390px、Light / Dark**：
  无页面横向溢出，深色背景 `rgb(17,24,39)`、浅色背景 `rgb(255,255,255)`。
- 通过 `scripts/browser_smoke.py` 可重复检查；本次复用了已有的 Python 3.14 Playwright 工具环境
  运行浏览器，不改变项目的 Python 3.12 环境，也不要求核心运行依赖 Playwright。
- 已查看实际截图，修复移动端顶部被原生工具栏遮挡、主题入口缺失和上传统计刷新问题。
- `sample --clear` 已实际执行；清空的是 data/sandbox，清理后默认正式库仍为 0 文档/0 文本块。

原始本地验证材料在 Git 忽略的 `artifacts/`：`full-tests.log`、`real-embedding.log`、
`browser-results.json`、四种聊天截图、页面截图和样例检索报告。
样例指标仅用于检查评估工具流程，不作为系统业务准确率。

## 独立审查与修复

一次独立代码审查复现了 4 项 P2 问题，均先补充失败回归测试再修复：

1. 中断的原始文件临时写入阻止重试 → 唯一临时文件名 + finally 清理。
2. 环境变量和页面设置组合可导致重启后配置失效 → 保存前验证实际有效配置。
3. 小写 .env 键优先级不一致 → 环境键统一大小写后判断。
4. 超过 30000 字符的历史回答阻止后续提问 → 构造 ChatMessage 前应用历史预算。

所有修复后执行了完整测试集，最终 66 项通过，没有保留已知的重要审查缺陷。

## 没有宣称通过的部分

- **初版未使用真实 DeepSeek API Key，没有付费远程问答的实际验收。**
  真实 LLM 的模型权限、账号余额、服务可用性、JSON 稳定性与回答质量需配置密钥后再验收。
  后续蓝桥课程知识包已在 2026-10-24 使用真实 `deepseek-flash` 完成 30 题在线验收；该结果属于
  混合检索改动前版本，详见 `knowledge_packs/lanqiao_ds/README.md`。
- 证据充分性和逐条陈述的语义真实性没有真实业务评测；模型仍可能误判。
- 初版没有正式知识库、标注集、业务准确率、性能/并发/长时间运行或断电硬件级测试；
  后续课程知识包只有 120 题小样本与 30 题在线验收，仍不构成业务准确率承诺。
- OCR、DOCX、cross-encoder reranker、多用户、分布式和自动旧快照清理未实现；
  BM25 混合检索已在 2026-10-24 追加。
- 流式展示及取消已在后续 UI 迭代实现，见下方追加记录。
- 在新机器按 README 从零安装尚未跨机器复测；本机使用了新建隔离环境和锁文件安装。



## UI 与流式交互追加验证 · 2026-10-23

以上初版截图、统计与耗时为历史验收。本轮当前完整测试集121通过、1项真实模型默认跳过；真实 BGE 独立测试1通过。
Ruff、格式与 JS 语法检查通过，构建成功且包内包含 CSS/JS/第三方许可，未包含 .env、私有数据或 artifacts。
Edge 真实浏览器验证了流式、取消、断流与限流、复制、滚动跟随、菜单、输入法、深浅主题、窄屏、长输入与引用展开。
使用真实 BGE/FAISS 的隔离知识库与本机模拟 LLM 验证了校验后展示、无效引用阻止；没有调用付费 API。
用户两张截图的侧栏展开/收起比例已校准，往返切换居中与布局测试通过。详见 [交互记录](UI_INTERACTIONS.md) 与 [截图校准决策](UI_SCREENSHOT_CALIBRATION.md)。

## BM25 混合检索追加验证 · 2026-10-24

- 完整测试集 **143 passed, 1 skipped**；新增 BM25/RRF 单元测试和混合检索集成测试，均先复现旧行为再接入。
- 正式课程库和 trial 库同为 219 文档/378 块；检索层先在 dev/holdout/test/newcontent 四套共 120 题上验证，未调用付费 API；随后用同一冻结 newcontent 集完成 30 题真实 API 在线复验。
- 采用等权 RRF（稠密 Top-50 + BM25 Top-50），Top-K 仍为 8、阈值 0.50、上下文窗口 2；阈值和 `score` 仍按稠密相似度。
- dev Recall@8 0.95→1.0、过阈值证据召回 0.95→0.975、MRR 0.849→0.950；test Recall@8 0.975→1.0、MRR 0.908→0.975；holdout 保持 1.0、MRR 0.771→0.838；newcontent 保持 1.0、MRR 0.867→0.908。负例阈值命中未恶化。
- newcontent 标签未修改；混合检索后的在线复验已完成：路由 30/30、引用 20/20、服务错误 0、检索/过阈值证据召回 1.0、负例 10/10 拒答；逐题核对 30/30 事实/拒答通过，new_14 有语言质量备注。改动前的 30/30 是历史结果，不能与本次混用。
- 证据见 `artifacts/lanqiao_ds/hybrid-retrieval-optimization.json`、四份 `*-hybrid.json` 报告、`newcontent-k8-t0.5-c500-w2-hybrid-routing*` 和 `newcontent-hybrid-review*`；第 9 章 p19/p33 的旋转插图未转录。

## 生成提示质量约束回归 · 2026-10-24

- 针对 new_14 的俄语词/残缺复杂度句，在 `GROUNDED_SYSTEM` 增加同语言、中文不混语言、只输出完整且 evidence 直接支持的陈述；旧提示备份在 `artifacts/lanqiao_ds/prompts-before-quality-guard.py`，提示字符串哈希 `c6ccea7f…→d5f1e614…`。
- 完整测试 **144 passed, 1 skipped**；同题 3/3 guard 复测无俄语词/残缺句。
- 同一 newcontent 30 题回归：路由 30/30、引用 20/20、服务错误 0；new_14 已修复，new_12 混入英文普通词 `container`，记为轻微质量备注。
- 因 new_14 被用于修改提示，newcontent 已按规则降为回归集；这次 30/30 不是新的独立验收，独立验收需另建未使用留出题。证据见 `prompt-quality-guard.json`、`newcontent-status.json`、`newcontent-k8-t0.5-c500-w2-hybrid-guard-routing*`。
