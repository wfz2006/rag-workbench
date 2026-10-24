# 交互与真实流式输出

按用户已授权的「现在补全」实施，保持 Streamlit 与现有 RAG 分层。此前明确要求非关键细节自主决策，因此不另设设计审批。

采用：OpenAI 兼容 SSE → 独立后台任务 → Streamlit fragment 增量显示。按钮可取消任务；最终结果与会话在后台原子保存一次。
通用回答实时展示网络增量；知识库的结构化输出缓冲到整份引用校验通过后再展示，不将未经验证的资料回答发到界面，也不人为逐字延迟。

交互范围：

- [x] SDK 真实 stream、错误映射、正常结束标记与关闭连接。
- [x] 任务阶段、取消、切换会话与单次持久化。
- [x] 消息淡入、等待标记、光标、按压反馈与低动态偏好。
- [x] 跟随最新内容；上滚暂停跟随，提供「回到最新」。
- [x] 添加资料 / 模型原生弹出菜单，键盘与关闭反馈。
- [x] 复制回答与明确的复制结果。
- [x] 无付费 API 的单元测试、真实 SDK 本地 SSE 与 Edge 交互验收。

参考：

- open-claude-ui（MIT），固定现有 commit `c1f923ae0051a871941b98a0e2e3183a545e6a85`：
  `code-examples/useClaudeScrollPinning.ts`、`ClaudeMotionPresets.ts`、`ChatComposer.tsx`。
- assistant-ui（MIT）：https://www.assistant-ui.com/elements/scroll-anchor
  参考「上滚停止跟随、按钮返回底部」的交互规则；不复制或引入其 React 实现。
- DeepSeek SSE：https://api-docs.deepseek.com/api/create-chat-completion/
- Streamlit fragment：https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment
- 可信静态 JS 的入口：https://docs.streamlit.io/develop/api-reference/text/st.html

JS 仅使用项目自带静态脚本；文档、模型回复与问题不拼接进可执行脚本。
测试模拟服务只位于 tests 支持目录，需显式设置隔离数据目录和本机测试地址，不作为正式功能兜底。


## 本轮实际验证 · 2026-10-23

- 完整 pytest：121 passed、1 skipped（真实模型默认跳过），无真实付费 API。
- 独立真实 BGE 测试：1 passed；使用已缓存模型，显式 HF_HUB_OFFLINE=1。
- Ruff 检查与格式检查通过；JS 语法检查通过；sdist/wheel 构建与静态资源、私有数据排除检查通过。
- Edge 实测：输入法与 Shift+Enter、两个菜单的 Escape/点外部关闭、跳转设置，网络增量、上滚暂停、返回最新、复制内容一致、消息入场、取消、断流、429、减少动态偏好与深色菜单。
- 知识库使用真实 BGE/FAISS/SQLite 和显式本地 SSE 测试服务；核验后展示有效来源，虚构 Chunk ID 被阻止，来源展开后的文本与 ID 完整可见。
- 1440/390px 浅深主题、1280×720 / 390×720 / 1280×540 长输入与消息滚动通过。
- 1048×696、DPR 1.5 的展开→收起→展开对照通过：齐边侧栏286 CSS px，输入框高度48 CSS px，宽度706/768 CSS px，底部工具栏独立且无溢出。

报告位于 artifacts/：ui-motion-browser.json、ui-motion-grounded-browser.json、ui-reference-geometry.json、ui-reference-smoke.json、ui-reference-layout.json。
对照截图：reference-expanded.png / reference-collapsed.png。截图中的本机测试回答只存在于隔离 QA 数据目录。
本轮修复了收起侧栏仍占位、弹出菜单深色样式、顶部空容器引入额外留白等问题。
另据运行栈与官方配置，关闭源码监听和每次刷新后的强制全量 GC，避免 Transformers 可选模块遍历与 fragment 刷新阻塞；Python 自动 GC 仍启用。

## 已知边界

- 不是原生 Claude 客户端，不宣称逐像素复刻。自有品牌、模型名、知识库菜单与引用保留真实含义，不提供无实现的麦克风/分享按钮。
- 通用回答才即时展示增量。知识库回答等待整份结构与来源通过校验；不人为延时模拟打字。
- 硬刷新不恢复实时任务句柄；后台完成后已持久化的记录仍可查看。首次连接或本地模型计算只能在检查点/超时后响应停止。
- 未使用真实 DeepSeek 账户验收服务权限、余额、网络质量与业务回答质量。模型首次下载需要网络；本轮浏览器回归使用既有缓存，不能代替新机器首次下载验收。

参考设置：https://docs.streamlit.io/develop/api-reference/configuration/config.toml
GC 行为：https://github.com/streamlit/streamlit/blob/develop/lib/streamlit/config.py


## 图标与焦点修正 · 2026-10-23

修正折叠、添加资料、搜索会话三个图标按钮的居中和字标重叠。
原生 popover 为下拉箭头保留了 -5px 边距，隐藏箭头后仍会偏移；图标按钮现单独重置布局，隐藏文字保留可访问名称。
主滚动区原生 tabindex=0 的 focus-visible::before 会绘制整页内描边，仅移除此描边，保留输入框/按钮焦点、菜单键盘操作及键盘滚动。
回归脚本 scripts/browser_icon_focus.py 覆盖浅深主题和 100%/125%/150% 像素缩放；修复前稳定复现，修复后通过。
本轮四页 AppTest 4 通过，Ruff 通过；Edge 页面导航、窄屏与长输入滚动回归通过，无付费 API 调用。
报告：artifacts/ui-icon-focus-before.json、ui-icon-focus-after.json、ui-icon-focus-smoke.json、ui-icon-focus-layout.json。


## 原版菜单与输入区细节 · 2026-10-23

根据用户追加的原版图，菜单使用 #30302f 中灰表面、#4a4a48 悬停行、左对齐列表及当前配置模型的选中标记。
弹层在 stApp 外部渲染，新增轻量主题同步，跟随应用手动选择的主题；每次安装断开上一个主题观察器。
回车图标使用24px矢量画布，清除原生内边距和 flex 压缩，校准右下位置并提高空输入时的可见度。
欢迎标题、说明、真实知识库状态及入口统一居中；空库不重复展示0计数，知识库管理页保留完整统计。
移除欢迎说明和菜单标题的叠加透明度，确保次级文字可读。

验证：四页 AppTest 4通过；Ruff与JS语法通过；21项输入区/菜单/主题检查及36项图标/焦点回归通过。
Edge回归覆盖1440/1280/390px、长输入、菜单导航、真实SDK流式/停止/断流/限流、真实BGE检索的引用展示与无效引用阻止。
LLM使用隔离的本机模拟服务，无付费API调用。
报告：artifacts/ui-composer-*.json；截图：artifacts/composer-menu-*.png。


菜单标题遮挡修正（2026-10-23）：原生 caption 的 -16px 下边距与自定义零段落边距/3px行距叠加，导致首行悬停背景覆盖标题。
仅重置菜单 caption 的外边距，保留完整标题高度；新增标题与首行按钮边界检查。
修复前在1440/1280/390px均复现；修复后27项菜单/主题/布局检查通过，四页AppTest4通过，正式8517在150%像素缩放实测无重叠。
报告：artifacts/ui-menu-heading-before.json、ui-menu-heading-after.json；截图：menu-heading-fixed-150.png。


输入区控件修正（2026-10-23）：字数提示和绝对定位的发送按钮重叠，已为计数和操作各保留独立位置，并为正文预留安全右侧间距；空输入恢复完整占位文字宽度。
停止按钮统一为32px操作位，使用居中10px实心方块，保留可访问名称和真实取消回调。
新增 browser_composer_controls.py；修复前复现字数与按钮重叠，修复后短文本/1999字/多行、150%像素缩放、390px窄屏及取消后恢复输入共30项检查通过。
另在无API Key的独立QA服务验证3种长输入/滚动布局；四页AppTest4通过，Ruff通过。正式8517的字数提示与回车按钮已实测分离。
报告：artifacts/ui-composer-controls-before.json、ui-composer-controls-after.json、ui-controls-layout.json；截图：formal-counter-fixed.png、composer-stop-*.png。
