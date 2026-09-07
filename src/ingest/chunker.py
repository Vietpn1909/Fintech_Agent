"""Cắt văn bản thành chunk.

Điểm mấu chốt của thiết kế: dự án này cắt chunk theo HAI cách khác nhau, vì hai
mục đích tiêu thụ chunk hoàn toàn khác nhau.

┌────────────────┬───────────────┬──────────────────────────────────────────────┐
│                │ Kích thước    │ Lý do                                        │
├────────────────┼───────────────┼──────────────────────────────────────────────┤
│ Chunk cho      │ ~1.200 ký tự  │ Truy hồi cần độ chi tiết cao. Chunk càng nhỏ  │
│ VECTOR STORE   │ chồng lấn 200 │ thì vector càng "đậm đặc" về một ý, tránh     │
│                │               │ tình trạng một vector gánh 5 chủ đề.          │
├────────────────┼───────────────┼──────────────────────────────────────────────┤
│ Chunk cho      │ ~3.000 ký tự  │ Quan hệ giữa hai thực thể thường nằm cách xa  │
│ TRÍCH XUẤT     │ chồng lấn 300 │ nhau trong đoạn. Chunk quá nhỏ sẽ cắt đứt     │
│ ĐỒ THỊ         │               │ quan hệ. Ngoài ra chunk lớn giảm số lần gọi   │
│                │               │ LLM từ ~1.900 xuống ~500 — với model chạy     │
│                │               │ local, đây là khác biệt giữa 1 giờ và 6 giờ.  │
└────────────────┴───────────────┴──────────────────────────────────────────────┘

Chỉ những mục có nội dung TƯỜNG THUẬT mới đưa vào trích xuất đồ thị. Item 8/15 là
bảng số liệu — trích xuất quan hệ từ bảng số cho kết quả rác, và ta đã có số liệu
chính xác từ XBRL rồi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List

from src.ingest.parser import Section

# Các mục dùng để xây đồ thị tri thức: nơi ban lãnh đạo nói về đối tác, đối thủ,
# công ty con, nhà cung cấp, mảng kinh doanh.
# "0" = bản khai không tách được theo mục (xem parse_filing). Vẫn đưa vào trích
# xuất đồ thị, vì mất Intel khỏi đồ thị ngành bán dẫn tệ hơn là tốn thêm lượt gọi LLM.
GRAPH_ITEMS = {"0", "1", "1A", "7", "3"}

VECTOR_CHUNK_SIZE = 1200
VECTOR_OVERLAP = 200
GRAPH_CHUNK_SIZE = 3000
GRAPH_OVERLAP = 300


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str  # "NVDA_10K_2026"
    ticker: str
    company: str
    form: str
    fiscal_year: str
    item: str  # "1A"
    item_title: str  # "Risk Factors"
    text: str
    seq: int  # thứ tự chunk trong mục
    metadata: Dict[str, str] = field(default_factory=dict)


def _split_paragraphs(text: str) -> List[str]:
    """Tách theo đoạn văn. Cắt ở ranh giới đoạn giữ nguyên vẹn ý nghĩa hơn cắt giữa câu."""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _pack(paragraphs: List[str], size: int, overlap: int) -> Iterator[str]:
    """Gom các đoạn văn liên tiếp cho tới khi đạt kích thước mong muốn.

    Đoạn nào tự nó đã dài hơn `size` (thường là bảng biểu lớn) sẽ được cắt cứng
    theo ký tự, vì không còn ranh giới tự nhiên nào để bám vào.
    """
    buf: List[str] = []
    buf_len = 0

    for para in paragraphs:
        if len(para) > size:
            if buf:
                yield "\n\n".join(buf)
                buf, buf_len = [], 0
            for i in range(0, len(para), size - overlap):
                piece = para[i : i + size]
                if len(piece) > 100:  # bỏ mẩu vụn ở cuối
                    yield piece
            continue

        if buf_len + len(para) > size and buf:
            yield "\n\n".join(buf)
            # Giữ lại phần cuối làm phần chồng lấn để không đứt mạch ngữ cảnh
            tail: List[str] = []
            tail_len = 0
            for p in reversed(buf):
                if tail_len + len(p) > overlap:
                    break
                tail.insert(0, p)
                tail_len += len(p)
            buf, buf_len = tail, tail_len

        buf.append(para)
        buf_len += len(para)

    if buf:
        yield "\n\n".join(buf)


def chunk_sections(
    sections: Dict[str, Section],
    doc_meta: Dict[str, str],
    size: int,
    overlap: int,
    only_items: set[str] | None = None,
) -> List[Chunk]:
    """Cắt các Section thành Chunk kèm đầy đủ metadata truy vết."""
    chunks: List[Chunk] = []

    for item, section in sorted(sections.items(), key=lambda kv: kv[1].char_start):
        if only_items is not None and item not in only_items:
            continue

        paragraphs = _split_paragraphs(section.text)
        for seq, body in enumerate(_pack(paragraphs, size, overlap)):
            if len(body) < 150:  # chunk quá ngắn không mang thông tin
                continue
            chunk_id = f"{doc_meta['doc_id']}_item{item}_{seq:04d}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_meta["doc_id"],
                    ticker=doc_meta["ticker"],
                    company=doc_meta["company"],
                    form=doc_meta["form"],
                    fiscal_year=doc_meta["fiscal_year"],
                    item=item,
                    item_title=section.title,
                    text=body,
                    seq=seq,
                )
            )

    return chunks


def make_vector_chunks(sections, doc_meta) -> List[Chunk]:
    """Chunk nhỏ, phủ toàn bộ các mục — dùng để tìm kiếm ngữ nghĩa."""
    return chunk_sections(sections, doc_meta, VECTOR_CHUNK_SIZE, VECTOR_OVERLAP)


def make_graph_chunks(sections, doc_meta) -> List[Chunk]:
    """Chunk lớn, chỉ lấy các mục tường thuật — dùng để LLM trích xuất quan hệ."""
    return chunk_sections(sections, doc_meta, GRAPH_CHUNK_SIZE, GRAPH_OVERLAP, only_items=GRAPH_ITEMS)
