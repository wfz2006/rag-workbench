"""Validated deployment settings; only an explicit allowlist is persisted by the UI."""

import json
import os
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rag_workbench.domain.errors import AppError


class Settings(BaseSettings):
    _deployment_env_file: object = PrivateAttr(default=".env")
    _environment_keys: set[str] = PrivateAttr(default_factory=set)
    _configuration_warnings: list[str] = PrivateAttr(default_factory=list)

    def __init__(self, **values):
        env_file = values.get("_env_file", self.model_config.get("env_file"))
        super().__init__(**values)
        self._deployment_env_file = env_file
        names = {key.upper() for key in os.environ}
        for path in env_file if isinstance(env_file, (list, tuple)) else [env_file]:
            if path is not None:
                names.update(key.upper() for key in dotenv_values(path))
        self._environment_keys = {key for key in EDITABLE_SETTINGS if f"RAG_{key.upper()}" in names}

    @property
    def configuration_warnings(self) -> list[str]:
        return list(self._configuration_warnings)

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    api_key: SecretStr = Field(default=SecretStr(""), validation_alias="DEEPSEEK_API_KEY")
    data_dir: Path = Path("data/production")
    model_cache: Path = Path(".cache/models")
    llm_model: str = Field(default="deepseek-flash", min_length=1, max_length=160)
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout: float = Field(default=60, ge=1, le=600)
    llm_max_tokens: int = Field(default=2048, ge=256, le=16384)
    deepseek_mode: bool = True
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_revision: str = "7999e1d3359715c523056ef9478215996d62a620"
    embedding_device: str = "cpu"
    query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_candidate_pool: int = Field(default=50, ge=1, le=500)
    retrieval_context_window: int = Field(default=0, ge=0, le=2)
    spoiler_max_page: int | None = Field(default=None, ge=1, le=100000)
    similarity_threshold: float = Field(default=0.55, ge=-1, le=1)
    lexical_min_coverage: float = Field(default=0.8, gt=0, le=1)
    chunk_size: int = Field(default=400, ge=20, le=4000)
    chunk_overlap: int = Field(default=60, ge=0, le=2000)
    allow_general: bool = True
    show_debug: bool = False
    teaching_profile_path: Path | None = None
    domain_profile_path: Path | None = None
    retrieval_expansions_path: Path | None = None
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    max_text_chars: int = Field(default=2_000_000, ge=1000, le=10_000_000)
    max_pdf_pages: int = Field(default=300, ge=1, le=2000)
    max_answer_claims: int | None = Field(default=None, ge=1, le=30)
    max_answer_chars: int | None = Field(default=None, ge=40, le=4000)

    @field_validator(
        "teaching_profile_path", "domain_profile_path", "retrieval_expansions_path", mode="before"
    )
    @classmethod
    def disabled_profile(cls, value):
        # A real nonempty environment value overrides .env, including Windows PowerShell.
        return (
            None
            if isinstance(value, str) and value.strip().casefold() in {"", "disabled"}
            else value
        )

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk Overlap 必须小于 Chunk Size")
        parsed = urlsplit(self.llm_base_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "https" and parsed.hostname:
            pass
        elif parsed.scheme != "http" or not loopback:
            raise ValueError("LLM Base URL 必须使用 HTTPS 或本机 HTTP")
        if not self.embedding_model.strip():
            raise ValueError("Embedding 模型不能为空")
        return self


EDITABLE_SETTINGS = frozenset(
    {
        "llm_model",
        "llm_base_url",
        "llm_timeout",
        "llm_max_tokens",
        "deepseek_mode",
        "embedding_model",
        "embedding_revision",
        "query_instruction",
        "top_k",
        "similarity_threshold",
        "lexical_min_coverage",
        "chunk_size",
        "chunk_overlap",
        "allow_general",
        "show_debug",
    }
)


def load_file_settings(
    path: Path, *, data_dir: Path | None = None, env_file: Path | str | None = ".env"
) -> Settings:
    """Explicit, non-secret JSON overrides; relative paths belong to the config file.

    Credentials still come only from environment/.env. Explicit file values win
    over both ambient environment and saved UI overrides for this invocation.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized")
        values = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(values, dict) or set(values) - (
            Settings.model_fields.keys() - {"api_key"}
        ):
            raise ValueError("unknown or secret setting")
        base = path.resolve().parent
        for key in (
            "data_dir",
            "model_cache",
            "teaching_profile_path",
            "domain_profile_path",
            "retrieval_expansions_path",
        ):
            value = values.get(key)
            if isinstance(value, str):
                if not value.strip() or value.strip().casefold() == "disabled":
                    if key in {"data_dir", "model_cache"}:
                        raise ValueError("invalid storage path")
                    values[key] = None
                else:
                    # A shared pack must not be able to read or write outside its own directory.
                    candidate = Path(value)
                    if candidate.is_absolute():
                        raise ValueError(f"{key} must be relative")
                    resolved = (base / candidate).resolve()
                    if not resolved.is_relative_to(base):
                        raise ValueError(f"{key} escapes the settings directory")
                    values[key] = resolved
        # Treat an explicit chunk setting as a pair; do not inherit an unrelated env sibling.
        if {"chunk_size", "chunk_overlap"} & values.keys():
            for key in ("chunk_size", "chunk_overlap"):
                values.setdefault(key, Settings.model_fields[key].default)
        if data_dir is not None:
            values["data_dir"] = data_dir
        result = Settings(_env_file=env_file, **values)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise AppError(
            "invalid_settings_file",
            "配置文件无效：请检查 UTF-8 JSON 格式、字段类型/范围和路径。"
            "只允许已知非密钥字段，最大 64 KiB；API Key 请使用环境变量。",
        ) from exc
    result._environment_keys |= set(values) & EDITABLE_SETTINGS
    return result
