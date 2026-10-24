# Claude Desktop 风格 UI 升级

最新一次调整已改为参考 Code 会话工作台，而非本文初版的 Cowork 欢迎页；
布局与当前测试结果见 [UI_CODE_LAYOUT.md](UI_CODE_LAYOUT.md)。以下保留历史实施记录。

目标：在保留 Streamlit 和全部 RAG 能力的前提下，参考 Claude Code 桌面端的窗口结构、
侧栏密度、欢迎页、输入框与消息排版，优先使用已验证许可的开源复刻素材。

核查的开源项目：

- https://github.com/Caho1/open-claude-ui ：MIT，设计令牌和独立组件，适合移植。
- https://github.com/cdesktop-ai/cdesktop ：Apache-2.0，完整编码代理桌面端，体积和运行机制超出本次 UI 范围。
- https://github.com/JonniTech/Claude-Clone ：网页复刻且依赖外部认证，本次不采用。

采用 open-claude-ui，固定参考 commit `c1f923ae0051a871941b98a0e2e3183a545e6a85`。
从 `code-examples/cds-theme.css` 移植 :root 设计令牌到 UI assets；保留 MIT 版权与完整许可。
其 React 组件作为输入区和消息几何参考，未引入 Electron、编码代理、认证服务或假聊天。
保留 RAG Workbench 自身名称和真实模型/来源，不冒充 Claude 服务。

实施：独立样式文件与设计令牌 → 顶部实际页面切换 → 扁平导航与搜索/切换会话 →
欢迎页及内联大输入框 → 聊天气泡/来源 → 浅深主题与 390/1440px 浏览器验收。
所有新控件连接真实功能；不添加没有对应能力的麦克风、权限按钮或窗口控制。
原 UI 文件备份在 Git 忽略的 `artifacts/ui-before`。

## 实际验证

- 67 项 pytest 全部通过，包含真实 BGE；新增真实会话搜索/切换的 AppTest。
- Ruff 检查、格式检查、sdist/wheel 构建通过。
- 真实 Edge：1440px 与 390px、浅色与深色，未检测到页面横向溢出。
- 测试顶部切页、侧栏导航、输入区添加资料跳转、新建会话、输入发送与明确的缺密钥错误。
- 修复窄屏顶栏挡住侧栏收起按钮；浏览器测试等待侧栏动画完成后再截图，未用强制点击掩盖遮挡。
- 验证样式文件与完整 MIT 许可进入 wheel，源码包未包含 data / artifacts / .env / 模型缓存 / .venv。
- 默认正式知识库仍为空；界面操作测试使用隔离 `data/ui-redesign-qa`。
- 原始报告：`artifacts/ui-redesign-tests.log`、`artifacts/ui-redesign-browser.json`。
- 截图：`artifacts/chat-light-1440.png`、`chat-dark-1440.png`、`chat-light-390.png`、`chat-dark-390.png`。

仍保留 Streamlit 的控件语义和交互机制；没有重新实现 Claude 的原生窗口、编码代理、
语音、权限系统或专有字体。未对特定版本官方截图执行像素差分，不宣称 1:1 验证。

## 细节收敛（2026-10-23）

- 浅色改为暖灰纸面色，深色统一为低对比炭灰；正文继续使用系统字体，不新增字体下载。
- 输入区改用 14px 圆角与细边线，移除大范围浮起阴影和悬停加深阴影；聚焦只增加细描边。
- 发送按钮禁用时为中性色，输入有效内容后使用强调色。下方两个长按钮改为紧凑文字入口。
- 侧栏导航选中态铺满整行，搜索框降低填充感；统一会话、个人区、顶栏与消息的颜色和圆角。
- 当前轮实际检查：Ruff、格式检查与 sdist/wheel 构建通过；pytest 为 66 通过、1 个真实模型测试按默认配置跳过。
- Edge 实测 1440px / 390px 的浅深主题、四页导航、输入发送和缺密钥错误；没有横向溢出。
- 额外检查 1280×720 的输入区聚焦、启用发送按钮和侧栏收起；收起后会话标题跟随内容区对齐。未调用真实付费 API。
- 本轮操作使用隔离目录 `data/ui-polish-qa`；原界面备份在 `artifacts/ui-before-polish`。
- 验证报告 `artifacts/ui-polish-browser.json`，截图沿用 `artifacts/chat-*.png`。

