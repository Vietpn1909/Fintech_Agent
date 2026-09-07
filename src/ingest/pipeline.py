"""Gắn kết các bước xử lý một bản khai: đọc file -> bóc mục -> cắt chunk.

Tách riêng ra đây để các script (đánh index vector, xây đồ thị, đánh giá) đều dùng
chung một đường đi, tránh mỗi nơi tự ghép lại một kiểu rồi lệch metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from config.settings import RAW_DIR
from src.ingest.chunker import Chunk, make_graph_chunks, make_vector_chunks
from src.ingest.parser import Section, parse_filing


def list_local_filings() -> List[Path]:
    """Tất cả file HTML đã tải về, sắp xếp ổn định để lần chạy nào cũng cùng thứ tự."""
    return sorted(RAW_DIR.rglob("*.html"))


def load_doc_meta(html_path: Path) -> Dict[str, str]:
    """Đọc file .meta.json đi kèm mà bước tải đã ghi ra."""
    meta_path = html_path.with_name(html_path.stem + ".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "doc_id": html_path.stem,
        "ticker": meta["ticker"],
        "company": meta["company_name"],
        "form": meta["form"],
        "fiscal_year": (meta["period_end"] or meta["filing_date"])[:4],
        "filing_date": meta["filing_date"],
        "accession": meta["accession"],
        "source_url": meta["url"],
    }


def process_filing(html_path: Path) -> Tuple[Dict[str, str], Dict[str, Section], List[Chunk], List[Chunk]]:
    """Xử lý một bản khai -> (metadata, các mục, chunk cho vector, chunk cho đồ thị)."""
    meta = load_doc_meta(html_path)
    sections = parse_filing(html_path)
    return meta, sections, make_vector_chunks(sections, meta), make_graph_chunks(sections, meta)
