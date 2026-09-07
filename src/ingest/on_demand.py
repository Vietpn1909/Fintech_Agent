"""Nạp dữ liệu theo yêu cầu — mở rộng độ phủ tới toàn bộ 8.001 doanh nghiệp.

VẤN ĐỀ NÀY GIẢI QUYẾT ĐIỀU GÌ

Lập chỉ mục trước văn bản 10-K của cả 8.001 doanh nghiệp tốn khoảng 16GB tải và một
ngày chạy liên tục, mà phần lớn trong số đó sẽ không bao giờ có ai hỏi tới. Ngược lại,
chỉ lập chỉ mục 500 công ty lớn thì hệ thống sẽ trả lời "tôi không có dữ liệu" cho một
câu hỏi hoàn toàn hợp lệ về công ty thứ 501.

Cách giải: lập chỉ mục sẵn nhóm hay được hỏi, và khi gặp công ty chưa có thì TỰ ĐI LẤY
ngay trong lúc trả lời. Mất 1-3 phút cho lần đầu, sau đó nằm luôn trong index.

Đây cũng chính là ranh giới giữa "RAG có thêm bộ định tuyến" và "agent thật": agent
nhận ra mình thiếu thông tin và tự hành động để bù đắp, thay vì trả lời rằng không biết.

BA MỨC PHỦ

    metrics  — có số liệu tài chính (mặc định, toàn bộ 8.001 doanh nghiệp)
    text     — có thêm văn bản 10-K trong vector store  <- module này nâng lên mức đó
    graph    — có thêm quan hệ trong đồ thị tri thức (tốn LLM, chỉ cụm trọng tâm)
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from qdrant_client.http import models as qm

from config.settings import settings
from src.graph.store import GraphStore
from src.ingest.edgar import download_companyfacts, download_filing, list_filings, load_ticker_map
from src.ingest.pipeline import process_filing
from src.vector.store import VectorStore

# Hậu tố pháp lý bỏ đi khi so khớp tên công ty do người dùng gõ
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|plc|co|company|holdings?|"
    r"group|sa|se|nv|ag|the)\b\.?",
    re.IGNORECASE,
)


def _simplify(text: str) -> str:
    text = _SUFFIX.sub("", (text or "").lower())
    return re.sub(r"[^a-z0-9 ]", " ", text).strip()


_graph_ticker_by_cik: Optional[Dict[str, str]] = None


def _canonical_ticker(cik: str, fallback: str) -> str:
    """Trả về mã chứng khoán mà ĐỒ THỊ đang dùng cho doanh nghiệp có CIK này.

    VÌ SAO CẦN BƯỚC NÀY

    Bảng mã của SEC có 10.391 mã cho 8.001 doanh nghiệp, vì một công ty có thể niêm yết
    nhiều hạng cổ phiếu: Alphabet có GOOG, GOOGL và GOOGM cùng trỏ về một CIK. Khi nạp
    đồ thị ta gộp theo CIK và chỉ giữ MỘT mã.

    Hậu quả nếu bỏ qua: người dùng hỏi "Alphabet", bộ phân giải trả về GOOG, nhưng node
    trong Neo4j lại mang mã GOOGL -> truy vấn trả về rỗng, và hệ thống báo "không có dữ
    liệu" về một trong những doanh nghiệp lớn nhất thế giới mà nó ĐANG CÓ đầy đủ dữ liệu.

    CIK là mã định danh duy nhất SEC cấp cho mỗi doanh nghiệp, không đổi theo hạng cổ
    phiếu. Neo nó vào CIK thì hai bên luôn khớp, bất kể đồ thị đã chọn mã nào.
    """
    global _graph_ticker_by_cik
    if _graph_ticker_by_cik is None:
        try:
            store = GraphStore()
            rows = store.run(
                "MATCH (c:Company) WHERE c.cik IS NOT NULL RETURN c.cik AS cik, c.ticker AS ticker"
            )
            store.close()
            _graph_ticker_by_cik = {r["cik"]: r["ticker"] for r in rows if r["ticker"]}
        except Exception:  # noqa: BLE001 - chưa nạp đồ thị thì cứ dùng mã của SEC
            _graph_ticker_by_cik = {}
    return _graph_ticker_by_cik.get(cik, fallback)


def resolve_ticker(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Chuyển tên công ty người dùng gõ thành mã chứng khoán.

    Người dùng gõ "Tesla", "Coca Cola", "nvidia" chứ hiếm khi gõ đúng mã. Hàm này xử lý
    theo ba mức, dừng ở mức nào có kết quả tốt:
      1. Khớp chính xác mã chứng khoán ("NVDA")
      2. Tên công ty chứa trọn cụm từ đã đơn giản hóa
      3. Độ tương tự chuỗi (bắt lỗi gõ sai: "microsft" -> Microsoft)
    """
    tmap = load_ticker_map()
    q = (query or "").strip()
    if not q:
        return []

    # Mức 1: đúng mã
    if q.upper() in tmap:
        info = tmap[q.upper()]
        return [{
            "ticker": _canonical_ticker(info["cik"], q.upper()),
            "name": info["name"], "cik": info["cik"], "match": "ticker",
        }]

    q_simple = _simplify(q)
    if not q_simple:
        return []

    # Tên viết tắt phổ biến -> tên đầy đủ. "TSMC" không phải mã chứng khoán (mã là TSM)
    # và cũng không xuất hiện trong tên chính thức "TAIWAN SEMICONDUCTOR MANUFACTURING
    # CO LTD", nên nếu không tra bảng này thì hỏi "TSMC" sẽ ra rỗng.
    from src.graph.schema import CANONICAL_COMPANIES

    alias = CANONICAL_COMPANIES.get(q_simple) or CANONICAL_COMPANIES.get(q.strip().lower())
    if alias:
        alias_simple = _simplify(alias)
        if alias_simple and alias_simple != q_simple:
            q_simple = alias_simple

    # Khớp theo RANH GIỚI TỪ, không phải chuỗi con thuần.
    # Nếu chỉ dùng chuỗi con, hỏi "Ford" sẽ trả về CRAWFORD & CO — vì "craw-ford" có
    # chứa "ford" và tên lại NGẮN HƠN "Ford Motor Co", nên thắng ở tiêu chí độ dài.
    word_re = re.compile(rf"(?<![a-z0-9]){re.escape(q_simple)}(?![a-z0-9])")

    scored = []
    for ticker, info in tmap.items():
        name_simple = _simplify(info["name"])
        if not name_simple:
            continue

        # Hạng càng nhỏ càng khớp sát. Trong cùng hạng thì tên ngắn hơn thắng.
        if name_simple == q_simple:
            rank = 0                                   # trùng khít: "alphabet"
        elif name_simple.startswith(q_simple + " "):
            rank = 1                                   # mở đầu bằng: "ford motor"
        elif word_re.search(name_simple):
            rank = 2                                   # đúng từ ở giữa tên
        elif q_simple in name_simple:
            rank = 3                                   # chuỗi con: "crawford"
        else:
            ratio = SequenceMatcher(None, q_simple, name_simple).ratio()
            if ratio <= 0.82:
                continue
            rank = 4                                   # gõ sai: "microsft"
            scored.append((rank, -ratio, ticker, info))
            continue

        scored.append((rank, len(name_simple), ticker, info))

    if not scored:
        return []

    scored.sort()

    # Một doanh nghiệp có thể có nhiều mã (Alphabet: GOOG, GOOGL, GOOGM). Gộp theo CIK
    # để không trả về ba dòng cho cùng một công ty.
    out, seen_cik = [], set()
    for rank, _, ticker, info in scored:
        if info["cik"] in seen_cik:
            continue
        seen_cik.add(info["cik"])
        out.append(
            {"ticker": _canonical_ticker(info["cik"], ticker),
             "name": info["name"], "cik": info["cik"],
             "match": ("exact", "prefix", "word", "substring", "fuzzy")[rank]}
        )
        if len(out) >= limit:
            break
    return out


def is_text_indexed(ticker: str, store: Optional[VectorStore] = None) -> int:
    """Số chunk của công ty này đang có trong vector store. 0 nghĩa là chưa lập chỉ mục."""
    store = store or VectorStore()
    result = store.client.count(
        collection_name=store.collection,
        count_filter=qm.Filter(
            must=[qm.FieldCondition(key="ticker", match=qm.MatchValue(value=ticker.upper()))]
        ),
        exact=True,
    )
    return result.count


def ingest_company_text(
    ticker: str,
    n_filings: int = 1,
    forms: Optional[List[str]] = None,
    vector_store: Optional[VectorStore] = None,
) -> Dict:
    """Tải và lập chỉ mục văn bản 10-K của một công ty ngay tại thời điểm cần.

    Trả về dict mô tả kết quả để agent nói lại với người dùng một cách trung thực
    ("tôi vừa lập chỉ mục báo cáo FY2024 của công ty này, mất 84 giây").
    """
    ticker = ticker.upper()
    forms = forms or ["10-K", "20-F"]  # 20-F là form của doanh nghiệp ngoài Mỹ niêm yết ADR
    started = time.time()
    store = vector_store or VectorStore()

    existing = is_text_indexed(ticker, store)
    if existing:
        return {
            "ticker": ticker, "status": "already_indexed",
            "chunks": existing, "seconds": 0.0,
        }

    try:
        refs = list_filings(ticker, forms, n_filings)
    except ValueError as exc:
        return {"ticker": ticker, "status": "unknown_ticker", "error": str(exc)}

    if not refs:
        return {
            "ticker": ticker, "status": "no_filings",
            "error": f"SEC không có bản khai {'/'.join(forms)} nào cho {ticker}",
        }

    total_chunks, indexed = 0, []
    for ref in refs:
        try:
            path = download_filing(ref)
            _, _, vector_chunks, _ = process_filing(path)
            if not vector_chunks:
                continue
            total_chunks += store.upsert_chunks(vector_chunks)
            indexed.append(ref.doc_id)
        except Exception as exc:  # noqa: BLE001
            return {
                "ticker": ticker, "status": "error",
                "error": f"{ref.doc_id}: {str(exc)[:160]}",
                "chunks": total_chunks,
            }

    # Số liệu XBRL của công ty này cũng nên có sẵn cho lần hỏi sau
    try:
        download_companyfacts(ticker)
    except Exception:  # noqa: BLE001
        pass

    # Ghi nhận công ty đã lên mức phủ "text"
    try:
        graph = GraphStore()
        graph.set_tier([ticker], "text")
        graph.close()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ticker": ticker,
        "status": "indexed" if total_chunks else "no_content",
        "chunks": total_chunks,
        "documents": indexed,
        "seconds": round(time.time() - started, 1),
    }


def ensure_text_available(company_or_ticker: str) -> Dict:
    """Điểm vào cho agent: nhận tên hoặc mã, đảm bảo văn bản đã sẵn sàng để tìm kiếm."""
    candidates = resolve_ticker(company_or_ticker)
    if not candidates:
        return {"status": "not_found", "query": company_or_ticker}

    best = candidates[0]
    result = ingest_company_text(best["ticker"])
    result["company"] = best["name"]
    result["alternatives"] = [c["ticker"] for c in candidates[1:4]]
    return result
