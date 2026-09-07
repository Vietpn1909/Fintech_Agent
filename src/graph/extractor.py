"""Trích xuất bộ ba (thực thể → quan hệ → thực thể) từ văn bản bằng LLM.

BA CƠ CHẾ GIỮ CHẤT LƯỢNG, XẾP THEO MỨC ĐỘ QUAN TRỌNG

1. ÉP BẰNG JSON SCHEMA CÓ ENUM (mạnh nhất)
   Danh sách nhãn thực thể và loại quan hệ được nhúng thẳng vào schema dưới dạng enum.
   LM Studio ràng buộc quá trình sinh token theo schema, nghĩa là model KHÔNG THỂ sinh
   ra "COMPETITOR_OF" khi enum chỉ có "COMPETES_WITH". Đây là ràng buộc ở tầng giải mã,
   mạnh hơn hẳn việc năn nỉ trong prompt — nhất là với model 12-24B.

2. KIỂM TRA SAU KHI SINH
   Ngay cả khi server bỏ qua schema, mọi bộ ba vẫn phải qua: chuẩn hóa tên → chuẩn hóa
   quan hệ → kiểm tra ràng buộc nhãn nguồn/đích → loại tự trỏ chính mình → loại thực
   thể quá chung chung. Cái gì không đạt thì bỏ, không cố cứu.

3. BẮT BUỘC CÓ BẰNG CHỨNG
   Mỗi bộ ba phải kèm câu văn gốc trong đoạn. Yêu cầu này vừa neo model vào văn bản
   (giảm bịa đặt), vừa cho ta một cách kiểm tra rẻ tiền: bằng chứng nào không thật sự
   xuất hiện trong chunk thì loại luôn bộ ba đó.

VÌ SAO CÓ PROMPT RIÊNG CHO TỪNG MỤC
Item 1A (Rủi ro) nói về phụ thuộc và đe dọa; Item 1 (Kinh doanh) nói về sản phẩm và
mảng kinh doanh; Item 7 (MD&A) nói về động lực tăng trưởng. Hướng model tập trung đúng
loại quan hệ của từng mục cho kết quả sạch hơn nhiều so với một prompt chung.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from src.graph.schema import (
    ENTITY_TYPES,
    RELATION_NAMES,
    RELATION_SPEC,
    is_valid_triple,
    normalize_name,
    normalize_relation,
)
from src.ingest.chunker import Chunk
from src.llm.client import chat_json

# Những tên quá chung chung, tạo node rác và nối bừa cả đồ thị lại với nhau
STOP_ENTITIES = {
    "company", "the company", "we", "our company", "us", "customers", "customer",
    "suppliers", "supplier", "competitors", "competitor", "third parties",
    "third party", "government", "certain customers", "others", "other companies",
    "products", "services", "business", "industry", "market", "technology",
    # Danh từ chung về sản phẩm — quan sát từ mẻ chạy thử: model trích ra "accessories",
    # "solutions" làm node Product. Chúng nối bừa nhiều doanh nghiệp với nhau mà không
    # mang thông tin gì. Giữ lại các danh mục thị trường có nghĩa như "smartphones".
    "accessories", "solutions", "software", "hardware", "product", "service",
    "products and services", "equipment", "systems", "platforms", "applications",
    # Tên rủi ro chung chung. Quan sát thật: trong 182 cạnh EXPOSED_TO_RISK đầu tiên có
    # tới 135 tên khác nhau và chỉ 17% được nhiều hơn một doanh nghiệp nhắc tới — tức là
    # phần lớn là lá đơn độc, tốn GPU mà không tạo ra đường đi nào trong đồ thị.
    "adverse macroeconomic conditions", "macroeconomic conditions", "economic conditions",
    "global and regional economic conditions", "general economic conditions",
    "competitive pressures", "competition", "economic uncertainty", "market conditions",
    "inflation", "uncertainty", "geopolitical tensions", "regulatory changes",
    "cybersecurity risks", "supply chain disruption", "supply chain disruptions",
}

# Ký tự có dấu tiếng Việt. Model thỉnh thoảng DỊCH tên thực thể sang tiếng Việt — đã gặp
# thật: node rủi ro "gián đoạn chuỗi cung ứng" nằm cạnh các tên tiếng Anh khác. Cùng một
# khái niệm thành hai node rời, và đồ thị đứt mạch mà không có dấu hiệu gì.
_VIETNAMESE_CHARS = re.compile(
    r"[àáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]",
    re.IGNORECASE,
)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "triples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "source_type": {"type": "string", "enum": list(ENTITY_TYPES.keys())},
                    "relation": {"type": "string", "enum": RELATION_NAMES},
                    "target": {"type": "string"},
                    "target_type": {"type": "string", "enum": list(ENTITY_TYPES.keys())},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "source", "source_type", "relation",
                    "target", "target_type", "evidence", "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["triples"],
    "additionalProperties": False,
}

# Hướng dẫn riêng cho từng mục của 10-K
ITEM_FOCUS: Dict[str, str] = {
    "1": (
        "This is the Business section. Focus on: business segments the company reports, "
        "products and services it offers, technologies it builds on, and geographies where "
        "it operates. Prefer OPERATES_SEGMENT, OFFERS_PRODUCT, BELONGS_TO_SEGMENT, "
        "USES_TECHNOLOGY, OPERATES_IN."
    ),
    "1A": (
        "This is the Risk Factors section. Focus on: named competitors, named suppliers the "
        "company depends on, named customers, regulators, and specific risks. "
        "Prefer COMPETES_WITH, SUPPLIED_BY, CUSTOMER_OF, EXPOSED_TO_RISK, REGULATED_BY."
    ),
    "7": (
        "This is Management's Discussion and Analysis. Focus on: which segments and products "
        "drove results, partnerships, and acquisitions. "
        "Prefer OPERATES_SEGMENT, OFFERS_PRODUCT, PARTNERS_WITH, ACQUIRED."
    ),
    "3": (
        "This is Legal Proceedings. Focus on: regulators and counterparties in litigation. "
        "Prefer REGULATED_BY, COMPETES_WITH."
    ),
}

_RELATION_MENU = "\n".join(
    f"  {name}: ({spec[0]}) -> ({spec[1]}) — {spec[2]}"
    for name, spec in RELATION_SPEC.items()
)
_ENTITY_MENU = "\n".join(f"  {label}: {desc}" for label, desc in ENTITY_TYPES.items())

SYSTEM_PROMPT = f"""You extract a knowledge graph from SEC 10-K filings.

You may ONLY use these entity types:
{_ENTITY_MENU}

You may ONLY use these relationship types, and you MUST respect the source/target types:
{_RELATION_MENU}

Rules — violating any of these makes the extraction useless:
1. Never invent a relationship type. Choose from the list above or output nothing.
2. Resolve "we", "our", "the Company" to the actual filing company named in the context.
3. Only extract entities that are NAMED in the text. Never create entities like
   "customers", "competitors", or "third parties" — they are meaningless as graph nodes.
4. Every triple must include `evidence`: a short verbatim quote from the passage that
   supports it. Do not paraphrase the evidence.
5. `confidence` is 0.0-1.0. Use below 0.6 when the text is vague or hedged.
6. If the passage contains no clear relationship, return an empty list. An empty result
   is far better than a made-up one.

7. PRIORITISE relationships between two NAMED ORGANISATIONS: COMPETES_WITH, SUPPLIED_BY,
   CUSTOMER_OF, PARTNERS_WITH, ACQUIRED, SUBSIDIARY_OF. These are the relationships that
   make the graph useful. Extract every single one you can find.

8. Be RESTRICTIVE with EXPOSED_TO_RISK. Only use it when the risk is a specific, named
   event or regulation that other companies would describe with the same words — for
   example "US export controls on advanced semiconductors" or "Taiwan Strait tensions".
   Never use it for vague phrasing such as "adverse macroeconomic conditions",
   "competitive pressures", or "general economic uncertainty". A risk node that only one
   company ever mentions adds nothing to the graph.

9. Entity names must be copied VERBATIM IN ENGLISH from the passage. Never translate a
   name into another language, and never paraphrase it. Two spellings of the same thing
   become two disconnected nodes and silently break the graph.
"""


@dataclass
class Triple:
    source: str
    source_type: str
    relation: str
    target: str
    target_type: str
    evidence: str
    confidence: float
    chunk_id: str
    doc_id: str
    ticker: str
    fiscal_year: str
    item: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_user_prompt(chunk: Chunk) -> str:
    focus = ITEM_FOCUS.get(chunk.item, "")
    return f"""Filing company: {chunk.company} (ticker {chunk.ticker})
Document: {chunk.form} for fiscal year {chunk.fiscal_year}
Section: Item {chunk.item} — {chunk.item_title}

{focus}

PASSAGE:
\"\"\"
{chunk.text}
\"\"\"

Extract the knowledge graph triples from this passage."""


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def validate_triples(raw: List[dict], chunk: Chunk) -> List[Triple]:
    """Lọc và chuẩn hóa kết quả thô của LLM. Bộ ba nào không đạt thì loại thẳng."""
    chunk_text_norm = _normalize_ws(chunk.text)
    out: List[Triple] = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        source_type = item.get("source_type", "")
        target_type = item.get("target_type", "")
        relation = normalize_relation(item.get("relation", ""))
        if relation is None:
            continue

        source = normalize_name(item.get("source", ""), source_type)
        target = normalize_name(item.get("target", ""), target_type)
        if not source or not target:
            continue

        # Loại thực thể chung chung và tự trỏ chính mình
        if source.lower() in STOP_ENTITIES or target.lower() in STOP_ENTITIES:
            continue
        if source.lower() == target.lower():
            continue
        if len(source) < 2 or len(target) < 2:
            continue

        # Loại tên thực thể đã bị dịch sang tiếng Việt (xem _VIETNAMESE_CHARS)
        if _VIETNAMESE_CHARS.search(source) or _VIETNAMESE_CHARS.search(target):
            continue

        # Ràng buộc nhãn nguồn/đích của ontology
        if not is_valid_triple(source_type, relation, target_type):
            continue

        # Bằng chứng phải thật sự có trong đoạn văn (so khớp sau khi chuẩn hóa khoảng trắng).
        # Đây là hàng rào chống bịa rẻ nhất và hiệu quả nhất.
        evidence = (item.get("evidence") or "").strip()
        evidence_norm = _normalize_ws(evidence)
        if len(evidence_norm) < 15 or evidence_norm[:120] not in chunk_text_norm:
            continue

        key = (source.lower(), relation, target.lower())
        if key in seen:
            continue
        seen.add(key)

        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        out.append(
            Triple(
                source=source,
                source_type=source_type,
                relation=relation,
                target=target,
                target_type=target_type,
                evidence=evidence[:500],
                confidence=max(0.0, min(1.0, confidence)),
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                ticker=chunk.ticker,
                fiscal_year=chunk.fiscal_year,
                item=chunk.item,
            )
        )

    return out


def extract_from_chunk(chunk: Chunk, model: Optional[str] = None) -> List[Triple]:
    """Trích xuất bộ ba từ một chunk. Trả về danh sách rỗng nếu LLM lỗi hoặc không có gì."""
    parsed = chat_json(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(chunk)},
        ],
        json_schema=EXTRACTION_SCHEMA,
        model=model,
        max_tokens=2048,
        # Trích xuất là việc điền JSON theo schema cố định. Đo thật trên Gemma 4 26B:
        # tắt bước suy nghĩ giảm thời gian chạy 544 chunk từ 5,3 giờ xuống 0,7 giờ,
        # và còn trích được NHIỀU quan hệ hơn (8 so với 6 trên đoạn văn mẫu).
        reasoning_effort="none",
    )

    if not isinstance(parsed, dict):
        # Một số model trả thẳng mảng thay vì bọc trong object
        if isinstance(parsed, list):
            parsed = {"triples": parsed}
        else:
            return []

    raw = parsed.get("triples") or parsed.get("relations") or []
    if not isinstance(raw, list):
        return []

    return validate_triples(raw, chunk)
