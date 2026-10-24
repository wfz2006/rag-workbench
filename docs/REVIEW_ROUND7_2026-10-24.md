# 0.1.7：第七轮审查修复记录

修改前备份：`artifacts/review-round7-before.zip`。保留真实 `.env`、知识包正文/原件/活动索引/会话、模型与所有旧发行包。未执行付费 API 或完整 v6 评测。

## 修复与边界

| 报告问题 | 本轮处理 |
| --- | --- |
| 清空触发自动取消 | 删除输入为空时自动点击取消的监听。Ctrl+A 后 Backspace/Delete 清空、立即输入/粘贴都会保留编辑目标与横幅；显式取消、Escape、切页/会话才退出编辑。保留 IME 组合输入与弹窗 Escape 的优先处理。 |
| 容器围栏生成幽灵代码块 | 只补齐延伸到 claim 源码末尾的未闭合 fence；引用块/列表退出时已隐式关闭的 fence 不追加。普通段落保持行内来源，复制正文不增加末尾的空引用代码块。原始 AnswerResult 不改写。 |
| 查询特征未接线 | pipeline 使用共享 question_type/contains_title/contains_quote；去掉重复本地正则。解释题、引文题优先于名称题，补齐“表达什么”“骂了什么”“说的…话是什么”。evidence payload 标记并排序相应候选，证据提示强调逐字核对、引号只是定位提示。 |
| 引文候选未扩展 | 仅在 retrieval_context_window 非零且当前问题为 quote 时，从已有 eligible primary 的同一文档增加最多 4 个引文块；不引入其他文档，保留真实分数、词法信息、来源页码与剧透边界，计入额外 20 块总上下文预算。已有低于阈值的诊断候选也可在同一限制下标记为上下文。 |
| 追问覆盖率 | 从最近可识别的显式主语提取词法焦点，跳过纯代词追问；识别“那么林冲呢”，支持简单的“林冲是怎么…/宝玉为什么…/请介绍一下王伦的生平”。不把上一题谓语混入当前覆盖率分母；无法可靠识别时保留原问题，不猜人物。完整检索问题和证据判断问题仍保留。 |
| 旧回合自动滚到末尾 | 运行状态带生成标识与替换目标；持久/未保存回答带对应锚点。自动跟随使用当前重试回合，通过 Streamlit 节点替换间隙保持目标；消息总数不变也有入场动画。显式跳到末尾会暂停该中间回合的跟随，后续生成/收尾尊重用户位置。减少动态效果偏好仍有效。 |
| 设置反馈单向同步 | 每次渲染按最新实际索引状态双向同步。empty→ready 不残留空库文案，ready→incompatible/corrupt 可显示重建入口；尚未保存设置也显示异常索引提示。保存校验错误保留，不被成功状态覆盖。 |

这次修正了上一轮“清空草稿即取消编辑”的错误交互选择。有明确编辑横幅与取消入口后，清空只是重写草稿的正常步骤，不应销毁目标。

## 验证

- 新增 `tests/test_review_round7.py` 共 23 项参数化用例：修复前 **20 failed / 3 passed**，修复后通过；连同前几轮相关测试 **78 passed**。
- 最终完整测试（开启真实离线 BGE）：**393 passed / 55.23 秒**；结果：`artifacts/review-round7-tests.xml`。Ruff 检查和格式检查通过（106 个文件）。
- 真 Edge 浏览器：1440×900、700×900，六组检查通过。覆盖清空立即重输、Escape、就地生成/动画、收尾锚点、显式跳转选择、设置反馈双向变化/无保存历史、容器代码与真实剪贴板、窄窗输入区高度。API/向量为明确的测试模拟，不能据此声称真实模型回答正确。入口：`tests/browser_support/round7_app.py`；执行器：`scripts/browser_round7_regressions.py`。证据：`artifacts/review-round7-browser.json`、`review-round7-{edit,retry,markdown,narrow}.png`。
- 上一轮五组浏览器回归再次通过，单独保存为 `artifacts/review-round7-compat-browser.json`，未覆盖历史证据。执行器已将“清空自动取消”的旧预期改为显式 Escape，并在取消后等待 rerun 完成再提交下一题，避免自动化抢先填入下一轮草稿。
- 实际 EXE 验收通过：受限 PATH、四页切换、强制退出清理、恢复、正常退出清理、外部程序继续存活。证据：`artifacts/review-round7-desktop/lifecycle-result.json`。
- 包内 Python 3.12.13 + 真实 BGE/FAISS 离线验收通过：空库、TXT/Markdown、去重、检索、重启、重建、删除、其他原件缺失时的恢复与原上传时间保留。证据：`artifacts/review-round7-desktop/backend-result.json`。离线 Hub 提示仅表示使用已打包缓存，没有下载模型或调用付费 API。
- 真实 BGE + 现有红楼梦索引的一致性副本：目标原话块 `587e36c486f4…`（ordinal=7）已进入 40 个候选，context_for 非空且 eligible=true。证据：`artifacts/review-round7-real-retrieval.json`。这是一条离线召回复核，不是回答正确率、完整 v6 dev/holdout 或付费在线验收。
- 首次尝试直接比较原库文件 SHA 时，Repository 初始化的 PRAGMA 更新使 SQLite 文件头改变，字节不变断言失败；没有发布新索引或改写文档/消息。随后改用只读 SQLite 备份及索引副本，验收前后确认原数据库 SHA 不变。

```powershell
$env:RAG_RUN_MODEL_TEST='1'; $env:HF_HUB_OFFLINE='1'; $env:OMP_NUM_THREADS='4'
.\.venv\Scripts\python.exe -m pytest -q --basetemp artifacts/round7-fresh-tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts app.py
```

封包前生产源码/资源与包内安装版本逐文件 SHA-256 核对，结果保存到 `artifacts/review-round7-packaged-source.json`；ZIP 每个文件的清单/哈希及隐私目录排除验证保存到 `artifacts/review-round7-release.json`，ZIP 完整校验码另附 `.zip.sha256`。

## 兼容与限制

- 索引签名未改变，0.1.1–0.1.6 不需仅为升级重建；远程回答仍需用户自己的 API 配置。
- 引文扩展受文档/数量/剧透边界限制，可能仍漏掉未加引号、跨文档或不在合格主命中文档中的原话；不降低证据审查门槛。
- 追问主语是保守语法规则，不能解决任意自然语言、复杂多实体或歧义指代；不能将单个示例当成整体召回率提升。
- 未执行完整红楼梦 v6 题集、冻结或在线付费评测，也没有将本轮生产修复冒充那份计划的全部完成。
- 浏览器自动回归不能替代所有 Windows/DPI/显卡/输入法的人工验收；便携包未签名。未保存替换内容仍为当前浏览器临时状态，刷新/关闭前请复制。
