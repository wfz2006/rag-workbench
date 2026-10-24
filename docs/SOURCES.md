# 官方来源与依赖核查

核查日期：2026-10-23。安装使用 Python 3.12.13，确切依赖见 `uv.lock`。

- DeepSeek [模型与参数](https://api-docs.deepseek.com/quick_start/pricing/)：本次默认使用
  `deepseek-flash`，Base URL `https://api.deepseek.com`；名称保留配置能力。
- DeepSeek [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)：通过
  OpenAI SDK `extra_body={"thinking":{"type":"disabled"}}` 关闭默认思考模式。
- DeepSeek [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)：使用
  `response_format={"type":"json_object"}`，提示词明确 json 和预期结构，再用 Pydantic 验证。
- DeepSeek [错误码](https://api-docs.deepseek.com/quick_start/error_codes/)：分别处理
  401、402、429 和服务错误，不把服务异常当作未命中。
- [OpenAI Python SDK](https://github.com/openai/openai-python)：兼容 Base URL、Chat Completions、
  timeout、`max_retries=0` 和标准异常类型。3.36.1 实际安装并通过 MockTransport 测试。
- [BGE 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)：中文 query instruction、
  归一化和 512 维输出；适配器不硬编码维度。
- [SentenceTransformer](https://sbert.net/docs/package_reference/sentence_transformer/model.html)：
  模型路径、revision、cache_folder、device、trust_remote_code 与 encode 参数。
- [FAISS cosine](https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances)：
  文档与查询同时归一化，IndexFlatIP 内积等价余弦相似度。
- [Streamlit API](https://docs.streamlit.io/develop/api-reference/chat/st.chat_input)：原生聊天输入、
  max_chars、缓存、会话和布局；实际版本 1.66.1。
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)：环境变量、
  dotenv、SecretStr、字段与模型校验。
- [pypdf 文本提取](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)：文本层提取，
  扫描文档需要 OCR，普通解析不能宣称识别扫描图像。
- [uv lock](https://docs.astral.sh/uv/concepts/projects/sync/)：锁文件与 frozen 安装。

实现时查询了以上依赖的官方 PyPI 元数据，最新主版本可用的依赖通过实际安装与测试确认。
`scripts/package_versions.py` 可重新查询；刷新版本不是自动升级指令，升级应重新测试。

