"""Tầng số liệu cho doanh nghiệp niêm yết tại Việt Nam (HOSE / HNX / UPCOM).

VÌ SAO CÓ FILE NÀY

Giới hạn lớn nhất của hệ thống là nó chỉ biết doanh nghiệp nộp hồ sơ cho SEC — tức là
không có doanh nghiệp Việt Nam nào ngoài VinFast. Đó là giới hạn của NGUỒN DỮ LIỆU chứ
không phải của kiến trúc: tầng số liệu chỉ cần bốn thứ — doanh nghiệp, năm, chỉ tiêu,
giá trị kèm đồng tiền. Nguồn nào cung cấp đủ bốn thứ đó đều cắm vào được.

File này chứng minh điều đó: cùng một lược đồ Neo4j, cùng bộ công cụ của agent, chỉ thay
nguồn nạp.

VÌ SAO GỌI THẲNG API THAY VÌ DÙNG THƯ VIỆN vnstock

Đã thử `vnstock`. Nó chạy được, nhưng ba vấn đề:

  1. Nó kéo theo gói `vnai` — thu thập `machine_id` và gửi ra ngoài. Dự án này bán điểm
     "chạy hoàn toàn trên máy, không gửi dữ liệu đi đâu"; thêm một gói telemetry là tự
     mâu thuẫn với chính mình.
  2. Nó kéo theo matplotlib, seaborn, wordcloud và nâng cấp numpy — bốn thứ dự án không
     dùng, chỉ để lấy vài con số.
  3. Bản cộng đồng giới hạn 4 kỳ báo cáo. Gọi thẳng API thì lấy được đầy đủ từ 2018.

Còn dự án vốn đã gọi thẳng API của SEC bằng httpx rồi, nên làm y như vậy ở đây là nhất
quán chứ không phải phát minh thêm.

CẤU TRÚC DỮ LIỆU CỦA VCI

API trả về tên chỉ tiêu dưới dạng MÃ (`isa1`, `isa5`, `bsa53`...), không phải tên người
đọc được. Bảng ánh xạ nằm ở một endpoint riêng `/financial-statement/metrics`, kèm cả
tiêu đề tiếng Anh. Nên phải gọi hai lần: một lần lấy bảng tên, một lần lấy số.

Đây chính là chỗ dễ sai âm thầm nhất — nếu ánh xạ nhầm mã thì con số vẫn hợp lệ, vẫn
hiển thị đẹp, chỉ là gắn sai chỉ tiêu. Nên bảng ánh xạ ở dưới khớp theo TIÊU ĐỀ TIẾNG
ANH chứ không hard-code mã số, và mọi mã tra được đều ghi lại để kiểm chứng.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx

_BASE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://iq.vietcap.com.vn/",
}

# Đồng tiền báo cáo. Doanh nghiệp niêm yết tại Việt Nam khai bằng VND, và hệ thống KHÔNG
# quy đổi tỷ giá — xem `compare_financials`, nó sẽ từ chối xếp hạng chung với số USD.
CURRENCY = "VND"

# Ánh xạ chỉ tiêu của dự án -> tiêu đề tiếng Anh mà VCI dùng.
#
# Khớp theo tiêu đề chứ không theo mã: mã `isa5` hôm nay là "Gross profit" nhưng VCI có
# thể chèn thêm dòng và đánh số lại, còn tiêu đề thì ổn định hơn nhiều. Mỗi mục là danh
# sách các cách viết chấp nhận được, lấy cái khớp đầu tiên.
_INCOME_TITLES: Dict[str, List[str]] = {
    # Ứng viên cuối của `revenue` và `operating_income` là dành cho NGÂN HÀNG.
    #
    # Ngân hàng không có "doanh thu bán hàng": báo cáo của họ dùng bộ chỉ tiêu riêng
    # (mã `isb*` thay vì `isa*`), bắt đầu từ thu nhập lãi thuần. Không xử lý riêng thì
    # 13/30 mã trong VN30 — toàn bộ nhóm ngân hàng — sẽ trống trơn phần doanh thu.
    #
    # Quy ước của ngành là lấy "Tổng thu nhập hoạt động" làm tương đương doanh thu. Nó
    # KHÔNG phải cùng một khái niệm với doanh thu bán hàng, nên khi nhánh này được dùng,
    # dòng dữ liệu sẽ mang thêm trường `revenue_basis` để nói rõ. Thà ghi nhãn còn hơn
    # để hai khái niệm khác nhau nằm chung một cột mà không ai biết.
    "revenue": ["net sales", "revenue", "total operating income"],
    "cost_of_revenue": ["cost of sales", "cost of goods sold"],
    "gross_profit": ["gross profit"],
    "operating_income": [
        "operating profit/loss", "operating profit", "operating income",
        "net operating profit before allowance for credit loss",
    ],
    "net_income": [
        "attributable to parent company",
        "net profit/loss after tax",
        "profit after tax",
    ],
    "rnd_expense": ["research and development", "r&d expenses"],
}

# Tiêu đề cho biết con số doanh thu đến từ báo cáo ngân hàng chứ không phải doanh nghiệp
# sản xuất/dịch vụ thông thường.
_BANK_REVENUE_TITLE = "total operating income"

# Tên chỉ tiêu PHẢI trùng khóa trong METRIC_LABELS của module XBRL. Đặt tên khác đi thì
# số vẫn vào Neo4j nhưng công cụ của agent không tìm thấy — dữ liệu nằm đó mà vô hình.
_BALANCE_TITLES: Dict[str, List[str]] = {
    "total_assets": ["total assets"],
    "total_liabilities": ["total liabilities", "liabilities"],
    "stockholders_equity": ["owner's equity", "owners' equity", "total equity"],
    "cash_and_equivalents": ["cash and cash equivalents"],
}

_CASHFLOW_TITLES: Dict[str, List[str]] = {
    "operating_cash_flow": [
        "net cash inflows/outflows from operating activities",
        "net cash flow from operating activities",
    ],
}

_last_call = 0.0


def _throttle(min_gap: float = 0.35) -> None:
    """Giãn nhịp gọi API.

    Đây là API công khai của một công ty chứng khoán, không phải dịch vụ trả tiền. Bắn
    liên tục vừa dễ bị chặn IP vừa là hành vi xấu. Cùng lý do với `_throttle` trong
    module SEC, chỉ khác con số.
    """
    global _last_call
    gap = time.time() - _last_call
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _last_call = time.time()


def _get(url: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    _throttle()
    resp = httpx.get(url, params=params, headers=_HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("successful", True):
        raise RuntimeError(f"VCI trả lỗi: {str(payload.get('msg'))[:120]}")
    return payload.get("data") or {}


def fetch_field_map(symbol: str) -> Dict[str, Dict[str, str]]:
    """Bảng tra: mã trường -> tiêu đề, cho từng loại báo cáo."""
    data = _get(f"{_BASE}/{symbol}/financial-statement/metrics")
    out: Dict[str, Dict[str, str]] = {}
    for section, rows in data.items():
        mapping = {}
        for row in rows or []:
            field = (row.get("field") or "").lower()
            title = (row.get("fullTitleEn") or row.get("titleEn") or "").strip()
            if field and title:
                mapping[field] = title
        out[section] = mapping
    return out


def _resolve_fields(field_titles: Dict[str, str], wanted: Dict[str, List[str]]) -> Dict[str, str]:
    """Chọn mã trường cho từng chỉ tiêu, khớp theo tiêu đề tiếng Anh.

    Khớp CHÍNH XÁC trước, sau đó mới tới khớp bắt đầu bằng. Không dùng "chứa chuỗi" —
    đó đúng là nhánh đã gây ra lỗi Acer→Macerich ở bộ phân giải tên, và ở đây hậu quả
    còn khó phát hiện hơn vì kết quả là một con số trông hoàn toàn hợp lệ.
    """
    lower = {field: title.lower() for field, title in field_titles.items()}
    chosen: Dict[str, str] = {}
    for metric, candidates in wanted.items():
        for candidate in candidates:
            hit = next((f for f, t in lower.items() if t == candidate), None)
            if hit:
                chosen[metric] = hit
                break
        if metric in chosen:
            continue
        for candidate in candidates:
            hit = next((f for f, t in lower.items() if t.startswith(candidate)), None)
            if hit:
                chosen[metric] = hit
                break
    return chosen


def fetch_year_rows(symbol: str) -> List[Dict[str, Any]]:
    """Một dòng cho mỗi năm tài chính, cùng dạng với `facts_to_year_rows` của SEC.

    Nhờ trùng dạng, phần nạp vào Neo4j và toàn bộ công cụ của agent dùng lại được nguyên
    vẹn — đây chính là điều chứng minh kiến trúc không phụ thuộc nguồn.
    """
    symbol = symbol.strip().upper()
    field_map = fetch_field_map(symbol)

    sections = (
        ("INCOME_STATEMENT", _resolve_fields(field_map.get("INCOME_STATEMENT", {}), _INCOME_TITLES)),
        ("BALANCE_SHEET", _resolve_fields(field_map.get("BALANCE_SHEET", {}), _BALANCE_TITLES)),
        ("CASH_FLOW", _resolve_fields(field_map.get("CASH_FLOW", {}), _CASHFLOW_TITLES)),
    )

    by_year: Dict[int, Dict[str, Any]] = {}
    company_name = fetch_company_name(symbol)

    # Doanh thu lấy từ "Tổng thu nhập hoạt động" nghĩa là đây là báo cáo ngân hàng.
    income_titles = field_map.get("INCOME_STATEMENT", {})
    revenue_field = sections[0][1].get("revenue")
    is_bank = bool(
        revenue_field
        and income_titles.get(revenue_field, "").lower() == _BANK_REVENUE_TITLE
    )

    for section, fields in sections:
        data = _get(f"{_BASE}/{symbol}/financial-statement", {"section": section})
        for record in (data.get("years") or []):
            year = record.get("yearReport")
            if not year:
                continue
            row = by_year.setdefault(int(year), {
                # `key` và `ticker` phải đúng khuôn của bên SEC thì upsert_financial_years
                # mới ghi được: nó MATCH theo ticker và MERGE theo key.
                "key": f"{symbol}.VN|{year}",
                "ticker": f"{symbol}.VN",
                "company": symbol,
                "fiscal_year": int(year),
                "period_end": f"{year}-12-31",
                "currency": CURRENCY,
                "accession": f"VCI:{symbol}:{year}",
                "derived": [],
            })
            for metric, field in fields.items():
                value = record.get(field)
                if value is not None:
                    # Chi phí trong báo cáo Việt Nam ghi số ÂM (giá vốn -14.490 tỷ).
                    # Dự án lưu chi phí là số dương, giống quy ước của XBRL Mỹ, nếu không
                    # thì mọi phép so sánh chi phí sẽ đảo dấu.
                    if metric in ("cost_of_revenue", "rnd_expense"):
                        value = abs(float(value))
                    row[metric] = float(value)

    rows = [by_year[y] for y in sorted(by_year, reverse=True)]
    for row in rows:
        if company_name:
            row["company"] = str(company_name).strip()
        if is_bank:
            row["revenue_basis"] = "Tổng thu nhập hoạt động (báo cáo ngân hàng)"

    # Suy ra lợi nhuận gộp khi VCI không khai riêng — chỉ khi có đủ hai vế.
    for row in rows:
        if row.get("gross_profit") is None and row.get("revenue") and row.get("cost_of_revenue"):
            row["gross_profit"] = row["revenue"] - row["cost_of_revenue"]
            row["derived"].append("gross_profit")
    return rows


def fetch_company_name(symbol: str) -> str:
    """Tên đầy đủ của doanh nghiệp.

    Bản ghi báo cáo tài chính KHÔNG chứa tên — chỉ có `organCode` và `ticker`. Tên nằm ở
    endpoint gốc `/company/{mã}`, trường `enOrganName` ("FPT Corporation").

    Đây là chi tiết dễ bỏ qua và hậu quả thì âm thầm: thiếu nó, mọi doanh nghiệp Việt Nam
    vào đồ thị dưới cái tên là chính mã của nó ("GAS", "SAB"), và người dùng gõ
    "Vinamilk" hay "Hòa Phát" sẽ không tìm ra gì.
    """
    symbol = symbol.strip().upper()
    try:
        data = _get(f"{_BASE}/{symbol}")
        name = (data.get("enOrganName") or data.get("organName")
                or data.get("enOrganShortName") or "")
        if name:
            return str(name).strip()
    except Exception:  # noqa: BLE001
        pass
    return symbol
