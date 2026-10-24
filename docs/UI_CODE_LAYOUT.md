# Code 桌面布局校准

> 上一轮历史记录。用户提供两张实际截图后，本轮已调整视觉目标，现状以 [截图校准](UI_SCREENSHOT_CALIBRATION.md) 和 [交互记录](UI_INTERACTIONS.md) 为准。

用户目标：继续接近 Claude Code 本身，保留上一轮克制的表面，不再沿用 Cowork 欢迎页结构。
依据：2026-10-23 查阅官方桌面文档与改版公告；截图只用于人工布局对照，不随应用分发。

选择沿用 Streamlit，重排 UI。继续小幅修改居中欢迎页无法解决结构差异；重写 React 前端会扩大此次范围。
根据用户此前「非关键技术细节自主决策，不要频繁询问」的要求，本轮直接实施这一可逆调整。

- 侧栏采用紧凑工作台导航，移除大字号 Rag 品牌与居中顶部模式切换，保留四个真实页面。
- 欢迎信息靠上、左对齐；概览只显示实际文档数、文本块数和索引状态，不生成虚构活动数据。
- 聊天输入区固定底部，知识库上下文在其上方；消息列表预留底部空间，窄屏单独布局。
- 搜索、会话切换、模型设置与资料跳转保持原服务与回调，不修改 RAG 或数据库逻辑。

实施与验证：

- [x] 调整 shell、欢迎页和输入区结构。
- [x] 校准浅深主题、1440/1280/390px 的布局与滚动。
- [x] 使用隔离知识库验证导航、长消息与缺密钥错误；原有会话恢复测试通过。
- [x] 完成最终 Ruff / 格式检查，正式预览已启动于 8517 且健康检查通过。

参考：https://code.claude.com/docs/en/desktop
https://claude.com/resources/articles/claude-code-desktop-redesign

实际验证：pytest 66 通过、1 个真实 Embedding 测试按默认配置跳过；本轮未修改模型或索引代码，未调用付费 API。
Edge 脚本覆盖 1440px 浅深主题、390px 浅深主题，以及 1280×720、1280×540、390×720
的输入展开与长消息滚动；最后一条错误回复没有被固定输入框遮挡。
所有界面交互使用 `data/ui-code-qa`，不写入正式知识库。
报告：`artifacts/ui-code-browser.json`、`artifacts/ui-code-layout-browser.json`。
截图：`artifacts/chat-*.png`、`artifacts/input-expanded-*.png`、`artifacts/conversation-scrolled-*.png`。

仍是 Streamlit 对 Code 桌面布局的适配；未实现原生窗口、编码代理或可拖动编辑器面板，不宣称逐像素复刻。
