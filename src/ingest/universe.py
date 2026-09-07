"""Quản lý "vũ trụ" doanh nghiệp — tập công ty mà hệ thống biết đến, chia theo ba tầng.

VÌ SAO PHẢI CHIA TẦNG

Ba loại tri thức trong hệ này có chi phí mở rộng chênh nhau hàng nghìn lần:

    Tầng SỐ LIỆU   8.001 công ty — 1 lần tải 1,41GB, không cần LLM.       Rẻ.
    Tầng VĂN BẢN   ~500 công ty  — tải 10-K + nhúng vector, vài giờ.      Vừa.
    Tầng ĐỒ THỊ    ~50 công ty   — mỗi chunk một lần gọi LLM, hàng giờ.   Đắt.

Trải đều nguồn lực cho cả ba tầng là sai lầm: phủ 8.001 công ty ở tầng đồ thị mất ~50
ngày GPU, trong khi phủ 8.001 công ty ở tầng số liệu chỉ mất một giờ và không có nhược
điểm nào. Module này giữ ranh giới ba tầng ở một chỗ để không lẫn lộn.

CÁCH CHỌN TẬP CÔNG TY CHO TẦNG VĂN BẢN

Không dùng danh sách S&P 500 chép tay (nhanh lạc hậu, và phải đi tìm nguồn). Thay vào
đó xếp hạng bằng chính dữ liệu ta đã có: sau khi nạp xong tầng số liệu, lấy N công ty
có doanh thu lớn nhất. Cách này tự cập nhật theo dữ liệu và bao gồm cả doanh nghiệp
ngoài Mỹ có niêm yết ADR (TSMC, Toyota, SAP, Alibaba...), vốn không nằm trong S&P 500.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from config.settings import RAW_DIR, settings
from src.ingest.edgar import _headers, _throttle

EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

# Hạt giống cho tầng đồ thị: cụm bán dẫn + cloud + AI.
# Chọn theo tiêu chí "các công ty này nhắc tên nhau trong báo cáo của chính mình" —
# đó mới là thứ tạo ra cạnh nối. Chọn 50 công ty ngẫu nhiên sẽ cho đồ thị rời rạc,
# nhiều node nhưng không có đường đi bắc cầu nào để suy luận.
ECOSYSTEM_SEEDS: List[str] = [
    # Thiết kế chip
    "NVDA", "AMD", "INTC", "QCOM", "AVGO", "MRVL", "ARM", "TXN", "ADI", "MCHP", "NXPI",
    # Sản xuất & thiết bị bán dẫn
    "TSM", "ASML", "AMAT", "LRCX", "KLAC", "TER", "ONTO", "UMC", "GFS",
    # Bộ nhớ & lưu trữ
    "MU", "WDC", "STX", "SNDK",
    # Điện toán đám mây & nền tảng
    "MSFT", "GOOGL", "AMZN", "META", "ORCL", "IBM", "CRM", "SNOW", "NOW", "DDOG",
    # Hạ tầng mạng & máy chủ
    "CSCO", "ANET", "SMCI", "DELL", "HPE", "JNPR", "CIEN",
    # Thiết bị đầu cuối & tiêu dùng
    "AAPL", "SONY", "HPQ", "LOGI",
    # Điện & làm mát cho trung tâm dữ liệu (mắt xích hay bị bỏ quên trong chuỗi AI)
    "VRT", "ETN", "PWR", "GEV",
]


@dataclass
class CompanyRef:
    ticker: str
    cik: str  # 10 chữ số
    name: str
    exchange: str = ""

    @property
    def cik_int(self) -> int:
        return int(self.cik)


def load_exchange_map(force: bool = False) -> Dict[str, str]:
    """Bảng mã chứng khoán -> sàn niêm yết (Nasdaq, NYSE, CBOE...).

    Dùng để loại các mã OTC / không niêm yết, vốn thường là công ty vỏ hoặc doanh nghiệp
    siêu nhỏ với báo cáo sơ sài — thêm vào chỉ làm loãng vector store.
    """
    cache = RAW_DIR / "company_tickers_exchange.json"
    if cache.exists() and not force:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        _throttle()
        resp = httpx.get(EXCHANGE_URL, headers=_headers(), timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        raw = resp.json()
        cache.write_text(json.dumps(raw), encoding="utf-8")

    # Định dạng: {"fields": ["cik","name","ticker","exchange"], "data": [[...], ...]}
    fields = raw["fields"]
    i_ticker, i_exchange = fields.index("ticker"), fields.index("exchange")
    out: Dict[str, str] = {}
    for row in raw["data"]:
        ticker = (row[i_ticker] or "").upper()
        if ticker:
            out[ticker] = row[i_exchange] or ""
    return out


def load_full_universe(listed_only: bool = True) -> List[CompanyRef]:
    """Toàn bộ doanh nghiệp có mã chứng khoán trong hệ thống SEC.

    Một công ty có thể có nhiều mã (cổ phiếu hạng A/C như Alphabet: GOOGL và GOOG).
    Ta gộp theo CIK và giữ mã đầu tiên để tránh xử lý trùng cùng một doanh nghiệp.
    """
    from src.ingest.edgar import load_ticker_map

    tmap = load_ticker_map()
    exchanges = load_exchange_map() if listed_only else {}

    by_cik: Dict[str, CompanyRef] = {}
    for ticker, info in tmap.items():
        exchange = exchanges.get(ticker, "")
        if listed_only and exchange not in {"Nasdaq", "NYSE", "CBOE", "NYSE American", "NYSE Arca"}:
            continue
        cik = info["cik"]
        if cik not in by_cik:
            by_cik[cik] = CompanyRef(ticker=ticker, cik=cik, name=info["name"], exchange=exchange)

    return sorted(by_cik.values(), key=lambda c: c.ticker)


def ecosystem_universe() -> List[CompanyRef]:
    """Tập công ty cho tầng đồ thị: cụm bán dẫn / cloud / AI."""
    universe = {c.ticker: c for c in load_full_universe(listed_only=False)}
    found = [universe[t] for t in ECOSYSTEM_SEEDS if t in universe]
    return found


def missing_seeds() -> List[str]:
    """Các mã trong hạt giống nhưng SEC không có — để biết mình đang thiếu gì."""
    universe = {c.ticker for c in load_full_universe(listed_only=False)}
    return [t for t in ECOSYSTEM_SEEDS if t not in universe]
