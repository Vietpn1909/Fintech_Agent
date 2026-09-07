"""Tải báo cáo tài chính từ SEC EDGAR.

Hai loại dữ liệu được lấy về, phục vụ hai mục đích khác nhau:

1. Văn bản báo cáo (10-K dạng HTML)  -> dùng cho Vector Search và trích xuất đồ thị.
   Đây là phần "kể chuyện": ban lãnh đạo bàn về rủi ro, chiến lược, cạnh tranh.

2. Dữ liệu XBRL (companyfacts.json) -> dùng cho tra cứu SỐ LIỆU chính xác.
   Đây là điểm khác biệt quan trọng: mọi con số tài chính (doanh thu, lợi nhuận...)
   được lấy từ trường dữ liệu có cấu trúc do chính doanh nghiệp khai báo, KHÔNG
   để LLM đọc từ văn bản rồi đoán. Nhờ vậy agent không bao giờ bịa số.

Lưu ý về chính sách của SEC:
  - Bắt buộc gửi header User-Agent chứa tên + email thật, nếu không sẽ bị trả về 403.
  - Giới hạn tối đa 10 request/giây. Code dưới đây tự giãn nhịp để tuân thủ.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import RAW_DIR, settings

# SEC yêu cầu không quá 10 req/s. Dùng 0.15s cho an toàn (~6.6 req/s).
_MIN_INTERVAL = 0.15
_last_request_at = 0.0

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{document}"


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": settings.sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
    }


def _throttle() -> None:
    """Chờ đủ lâu giữa hai request liên tiếp để không vi phạm rate limit của SEC."""
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_at = time.time()


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
def _get(url: str) -> httpx.Response:
    _throttle()
    resp = httpx.get(url, headers=_headers(), timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    return resp


@dataclass
class FilingRef:
    """Một bản khai báo cáo cụ thể trên EDGAR."""

    ticker: str
    company_name: str
    cik: str  # dạng 10 chữ số có số 0 ở đầu, ví dụ "0000320193"
    form: str  # "10-K", "10-Q"...
    filing_date: str  # ngày nộp, "2024-11-01"
    period_end: str  # ngày kết thúc kỳ báo cáo
    accession: str  # số hiệu bản khai, "0000320193-24-000123"
    document: str  # tên file HTML chính
    url: str

    @property
    def fiscal_year(self) -> str:
        """Năm tài chính, suy ra từ ngày kết thúc kỳ báo cáo."""
        return (self.period_end or self.filing_date)[:4]

    @property
    def doc_id(self) -> str:
        """Định danh duy nhất, dùng làm tên file và làm khóa trong Neo4j/Qdrant."""
        return f"{self.ticker}_{self.form.replace('-', '')}_{self.fiscal_year}"


def load_ticker_map() -> Dict[str, dict]:
    """Lấy bảng ánh xạ mã cổ phiếu -> CIK. Cache lại vì file này ~1MB và ít đổi."""
    cache = RAW_DIR / "company_tickers.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        raw = _get(TICKER_MAP_URL).json()
        cache.write_text(json.dumps(raw), encoding="utf-8")

    # File gốc có dạng {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    return {
        entry["ticker"].upper(): {
            "cik": str(entry["cik_str"]).zfill(10),
            "name": entry["title"],
        }
        for entry in raw.values()
    }


def list_filings(ticker: str, forms: List[str], limit: int) -> List[FilingRef]:
    """Trả về danh sách các bản khai gần nhất của một công ty, mới nhất trước."""
    tmap = load_ticker_map()
    if ticker not in tmap:
        raise ValueError(f"Không tìm thấy mã '{ticker}' trong danh sách của SEC")

    cik = tmap[ticker]["cik"]
    data = _get(SUBMISSIONS_URL.format(cik10=cik)).json()
    company_name = data.get("name", tmap[ticker]["name"])

    recent = data["filings"]["recent"]
    results: List[FilingRef] = []

    # Các trường trong "recent" là những mảng song song cùng độ dài
    for i, form in enumerate(recent["form"]):
        if form.upper() not in forms:
            continue
        accession = recent["accessionNumber"][i]
        results.append(
            FilingRef(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                form=form.upper(),
                filing_date=recent["filingDate"][i],
                period_end=recent.get("reportDate", [""] * len(recent["form"]))[i],
                accession=accession,
                document=recent["primaryDocument"][i],
                url=ARCHIVE_URL.format(
                    cik_int=int(cik),
                    accession_nodash=accession.replace("-", ""),
                    document=recent["primaryDocument"][i],
                ),
            )
        )
        if len(results) >= limit:
            break

    return results


def download_filing(ref: FilingRef, force: bool = False) -> Path:
    """Tải file HTML của một bản khai về data/raw/<TICKER>/."""
    out_dir = RAW_DIR / ref.ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{ref.doc_id}.html"
    meta_path = out_dir / f"{ref.doc_id}.meta.json"

    if html_path.exists() and not force:
        return html_path

    resp = _get(ref.url)
    html_path.write_bytes(resp.content)
    meta_path.write_text(json.dumps(asdict(ref), indent=2), encoding="utf-8")
    return html_path


def download_companyfacts(ticker: str, force: bool = False) -> Optional[Path]:
    """Tải toàn bộ dữ liệu tài chính có cấu trúc (XBRL) của một công ty."""
    tmap = load_ticker_map()
    if ticker not in tmap:
        return None

    out_dir = RAW_DIR / ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "companyfacts.json"
    if path.exists() and not force:
        return path

    resp = _get(COMPANYFACTS_URL.format(cik10=tmap[ticker]["cik"]))
    path.write_bytes(resp.content)
    return path
