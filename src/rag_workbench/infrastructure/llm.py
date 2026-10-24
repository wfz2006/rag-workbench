from collections.abc import Iterator
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import ChatMessage
from rag_workbench.domain.ports import Cancellation


class OpenAIChatProvider:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings, self._client = settings, client

    def _get_client(self) -> Any:
        if self._client is None:
            key = self.settings.api_key.get_secret_value().strip()
            if not key:
                raise AppError(
                    "missing_api_key",
                    "尚未配置 DEEPSEEK_API_KEY。请在当前工作空间的 .env 中配置后刷新页面，"
                    "再重试问题。",
                )
            self._client = OpenAI(
                api_key=key,
                base_url=self.settings.llm_base_url,
                timeout=self.settings.llm_timeout,
                max_retries=0,
            )
        return self._client

    def _arguments(
        self, system: str, user: str, json_mode: bool, history: list[ChatMessage] | None
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                *[m.model_dump() for m in (history or [])],
                {"role": "user", "content": user},
            ],
            "max_tokens": self.settings.llm_max_tokens,
            "timeout": self.settings.llm_timeout,
        }
        if self.settings.deepseek_mode:
            arguments["extra_body"] = {"thinking": {"type": "disabled"}}
        if json_mode:
            arguments["response_format"] = {"type": "json_object"}
        return arguments

    def stream(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        history: list[ChatMessage] | None = None,
        cancellation: Cancellation | None = None,
    ) -> Iterator[str]:
        stream = None
        try:
            if cancellation:
                cancellation.raise_if_cancelled()
            arguments = self._arguments(system, user, json_mode, history)
            arguments["stream"] = True
            stream = self._get_client().chat.completions.create(**arguments)
            if cancellation:
                cancellation.add_cleanup(stream.close)
            stopped, received = False, False
            for chunk in stream:
                if cancellation:
                    cancellation.raise_if_cancelled()
                if not chunk.choices:
                    continue  # Usage-only terminal chunks are legal.
                choice = chunk.choices[0]
                if choice.finish_reason is not None:
                    if choice.finish_reason != "stop":
                        raise AppError("incomplete_response", "模型响应被截断或过滤，请重试。")
                    stopped = True
                content = choice.delta.content
                if isinstance(content, str) and content:
                    received = received or bool(content.strip())
                    yield content
            if cancellation:
                cancellation.raise_if_cancelled()
            if not stopped:
                raise AppError("incomplete_response", "模型连接提前结束，本轮回答未完成，请重试。")
            if not received:
                raise AppError("empty_response", "LLM 返回空内容，请重试。")
        except APITimeoutError as exc:
            raise AppError("timeout", "LLM 请求超时，请检查网络或增加超时时间后重试。") from exc
        except APIConnectionError as exc:
            raise AppError("connection", "无法连接 LLM 服务，请检查网络与 Base URL。") from exc
        except APIStatusError as exc:
            errors = {
                401: ("authentication", "LLM 认证失败，请检查 API Key。"),
                402: ("balance", "LLM 账户余额不足，请检查账户余额。"),
                403: ("permission", "LLM 服务拒绝访问，请检查模型权限。"),
                429: ("rate_limit", "LLM 请求受限，请稍后重试。"),
            }
            code, message = errors.get(
                exc.status_code,
                ("provider_error", f"LLM 服务返回 HTTP {exc.status_code}，请稍后重试。"),
            )
            raise AppError(code, message) from exc
        finally:
            if stream is not None:
                if cancellation:
                    cancellation.remove_cleanup(stream.close)
                stream.close()

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        history: list[ChatMessage] | None = None,
    ) -> str:
        try:
            client = self._get_client()
            arguments = self._arguments(system, user, json_mode, history)
            completion = client.chat.completions.create(**arguments)
            if not completion.choices:
                raise AppError("empty_response", "LLM 返回空响应，请重试。")
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise AppError(
                    "incomplete_response", "模型响应被截断或过滤，请增加输出上限或调整问题。"
                )
            content = choice.message.content
            if not isinstance(content, str) or not content.strip():
                raise AppError("empty_response", "LLM 返回空内容，请重试。")
            return content
        except APITimeoutError as exc:
            raise AppError("timeout", "LLM 请求超时，请检查网络或增加超时时间后重试。") from exc
        except APIConnectionError as exc:
            raise AppError("connection", "无法连接 LLM 服务，请检查网络与 Base URL。") from exc
        except APIStatusError as exc:
            errors = {
                401: ("authentication", "LLM 认证失败，请检查 API Key。"),
                402: ("balance", "LLM 账户余额不足，请检查账户余额。"),
                403: ("permission", "LLM 服务拒绝访问，请检查模型权限。"),
                429: ("rate_limit", "LLM 请求受限，请稍后重试。"),
            }
            code, message = errors.get(
                exc.status_code,
                (
                    "provider_error",
                    f"LLM 服务返回 HTTP {exc.status_code}，请检查模型配置或稍后重试。",
                ),
            )
            raise AppError(code, message) from exc
