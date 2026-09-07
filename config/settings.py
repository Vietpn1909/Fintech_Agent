"""Cấu hình tập trung cho toàn dự án.

Mọi tham số đọc từ file .env. Không hardcode thông tin kết nối ở bất kỳ đâu khác,
để bạn đổi model / đổi database chỉ bằng cách sửa .env, không phải sửa code.
"""

import sys
from pathlib import Path
from typing import List

# Console Windows mặc định dùng bảng mã cp1252, không in được tiếng Việt.
# Ép stdout/stderr sang UTF-8 ngay khi settings được import (mọi module đều import nó).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - môi trường không hỗ trợ thì bỏ qua
            pass

from pydantic_settings import BaseSettings, SettingsConfigDict

# Thư mục gốc của dự án = thư mục cha của config/
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- SEC ---
    sec_user_agent: str = "Anonymous anonymous@example.com"

    # --- LLM ---
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_extraction_model: str = "google/gemma-3-12b"
    llm_reasoning_model: str = "google/gemma-3-27b"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    llm_timeout: int = 300
    # Mức suy nghĩ mặc định cho model có reasoning (Gemma 4, Qwen3, DeepSeek-R1...).
    # "none" tắt hẳn; "default" giữ nguyên hành vi model; low/medium/high nếu model hỗ trợ.
    llm_reasoning_effort: str = "none"

    # --- Embedding ---
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "fintech123"
    neo4j_database: str = "neo4j"

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "sec_filings"

    # --- Phạm vi dữ liệu ---
    target_tickers: str = "AAPL,MSFT,NVDA,GOOGL"
    filing_types: str = "10-K"
    filings_per_company: int = 2

    @property
    def tickers(self) -> List[str]:
        return [t.strip().upper() for t in self.target_tickers.split(",") if t.strip()]

    @property
    def forms(self) -> List[str]:
        return [f.strip().upper() for f in self.filing_types.split(",") if f.strip()]


settings = Settings()

# Tạo sẵn các thư mục dữ liệu để phần code sau không phải kiểm tra
for _d in (RAW_DIR, PROCESSED_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
