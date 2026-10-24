"""Frozen Santi evaluation settings; explicit values win over shell exports."""

from pathlib import Path

from rag_workbench.config import Settings, load_file_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "knowledge_packs" / "santi" / "settings.json"


def santi_settings(data_dir: Path, *, env_file: str | Path | None = ".env") -> Settings:
    return load_file_settings(CONFIG_PATH, data_dir=Path(data_dir), env_file=env_file)
