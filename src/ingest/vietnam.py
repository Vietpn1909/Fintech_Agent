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

# Khi dòng doanh thu KHÔNG phải "doanh thu bán hàng" thông thường thì phải ghi nhãn.
#
# Mỗi mục: tiêu đề tiếng Anh thật mà VCI trả về -> lời giải thích đi kèm số liệu.
#
# ⚠️ Vì sao cần bảng này chứ không chỉ một hằng số cho ngân hàng: khi mở rộng từ 30 mã
# VN30 ra toàn sàn, bảng báo cáo không còn hai khuôn mà là NĂM khuôn khác nhau — đo thật
# trên 19 mã thuộc các ngành khác nhau:
#
#     doanh nghiệp thường  BS122/IS25  ->  isa3  "Net sales"
#     ngân hàng            BS86/IS26   ->  isb*  "Total operating income"
#     công ty chứng khoán  BS208/IS79  ->  isa3  "Net sales"
#     doanh nghiệp bảo hiểm BS151/IS84 ->  isi64 "Net sales from insurance business"
#
# Nhánh bảo hiểm là chỗ nguy hiểm nhất và nó từng lọt lưới: `_resolve_fields` khớp kiểu
# "bắt đầu bằng", nên "Net sales from insurance business" khớp ứng viên "net sales" và
# con số vào thẳng cột doanh thu KHÔNG kèm lời giải thích nào. BVH năm 2024 ra 39.823 tỷ
# — đó là doanh thu thuần mảng bảo hiểm, không gồm thu nhập đầu tư tài chính, tức là một
# khái niệm khác hẳn doanh thu của một doanh nghiệp sản xuất. Xếp chung một cột mà không
# ghi nhãn thì mọi bảng xếp hạng đều so sai, và không có gì báo lỗi cả.
#
# So khớp theo tiền tố để bắt được cả các biến thể cách viết.
_REVENUE_BASIS: List[tuple] = [
    ("total operating income",
     "Tổng thu nhập hoạt động (báo cáo ngân hàng)"),
    ("net sales from insurance business",
     "Doanh thu thuần hoạt động kinh doanh bảo hiểm — KHÔNG gồm thu nhập đầu tư tài chính"),
    ("net operating profit before allowance for credit loss",
     "Lợi nhuận thuần từ hoạt động kinh doanh trước dự phòng rủi ro tín dụng (báo cáo ngân hàng)"),
]


def _revenue_basis(title: str) -> Optional[str]:
    """Lời giải thích đi kèm, nếu dòng doanh thu không phải doanh thu bán hàng thông thường."""
    low = (title or "").strip().lower()
    for prefix, note in _REVENUE_BASIS:
        if low.startswith(prefix):
            return note
    return None

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


# Sàn giao dịch được coi là "niêm yết".
#
# Đo trên toàn bộ danh sách VCI trả về (1.905 mã):
#     UPCOM 855 · HSX 430 · OTC 313 · HNX 301 · OTHER 2 · STOP 2 · (trống) 2
#
# Giữ ba sàn có giao dịch tập trung. Bỏ OTC vì đó là cổ phiếu chưa niêm yết, mua bán thỏa
# thuận, không có nghĩa vụ công bố thông tin định kỳ — số liệu có thì cũ và không ai soát.
# Bỏ STOP (đã ngừng giao dịch) và OTHER vì cùng lý do: đưa vào chỉ làm loãng vũ trụ tra
# cứu và tăng khả năng va chạm tên với doanh nghiệp Mỹ, mà không thêm câu trả lời nào.
LISTED_EXCHANGES = ("HSX", "HNX", "UPCOM")


def fetch_universe(exchanges: tuple = LISTED_EXCHANGES) -> List[Dict[str, Any]]:
    """Toàn bộ doanh nghiệp VCI theo dõi. MỘT lần gọi API cho cả sàn.

    Đây là thứ tương đương `company_tickers.json` của SEC, và là lý do có thể mở rộng từ
    30 mã lên gần 1.600 mà không phải viết tay danh sách nào.

    Trả về sẵn `organNameEn` — đã đối chiếu với `enOrganName` của endpoint từng mã trên 6
    doanh nghiệp thuộc 5 ngành, KHỚP TUYỆT ĐỐI. Nhờ vậy `fetch_year_rows` nhận tên truyền
    sẵn và bỏ được một lần gọi API cho mỗi mã — bớt gần 1.600 lượt gọi mỗi lần nạp lại.
    """
    data = _get(_BASE)
    rows = data if isinstance(data, list) else (data.get("items") or [])

    out: List[Dict[str, Any]] = []
    for row in rows:
        ticker = (row.get("ticker") or "").strip().upper()
        exchange = (row.get("exchange") or "").strip().upper()
        if not ticker or exchange not in exchanges:
            continue
        out.append({
            "symbol": ticker,
            # Ưu tiên tên tiếng Anh: bộ phân giải tên và model nhúng đều làm việc trên
            # tiếng Anh, còn tên tiếng Việt có dấu sẽ không khớp khi người dùng gõ không dấu.
            "name": (row.get("organNameEn") or row.get("organNameVi") or ticker).strip(),
            "name_vi": (row.get("organNameVi") or "").strip(),
            "exchange": exchange,
            "sector": (row.get("sectorNameLv1CustomEn") or "").strip(),
            "market_cap": row.get("marketCap"),
        })
    return out


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


def fetch_year_rows(symbol: str, company_name: Optional[str] = None) -> List[Dict[str, Any]]:
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
    # Tên truyền sẵn thì khỏi gọi lại. Endpoint vũ trụ (`fetch_universe`) đã trả về đúng
    # chuỗi mà `/company/{mã}` trả về — đã đối chiếu trên 6 mã thuộc 5 ngành, khớp tuyệt
    # đối. Bỏ được lần gọi này nghĩa là bớt 1.905 lượt khi nạp toàn sàn.
    company_name = company_name or fetch_company_name(symbol)

    # Dòng doanh thu có phải "doanh thu bán hàng" thông thường không? Nếu không thì ghi
    # nhãn để không ai đem so với doanh nghiệp sản xuất mà tưởng cùng khái niệm.
    income_titles = field_map.get("INCOME_STATEMENT", {})
    revenue_field = sections[0][1].get("revenue")
    basis = _revenue_basis(income_titles.get(revenue_field, "")) if revenue_field else None

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
        if basis:
            row["revenue_basis"] = basis

    # Suy ra lợi nhuận gộp khi VCI không khai riêng — chỉ khi có đủ hai vế.
    for row in rows:
        if row.get("gross_profit") is None and row.get("revenue") and row.get("cost_of_revenue"):
            row["gross_profit"] = row["revenue"] - row["cost_of_revenue"]
            row["derived"].append("gross_profit")
    return rows


def fetch_shareholders(symbol: str, min_percent: float = 0.005) -> List[Dict[str, Any]]:
    """Danh sách cổ đông của một mã, đã lọc và chuẩn hóa.

    ĐÂY LÀ TẦNG ĐỒ THỊ CHO VIỆT NAM, VÀ NÓ KHÔNG TỐN MỘT LẦN GỌI LLM NÀO.

    Đồ thị phía Mỹ dựng bằng cách cho mô hình đọc từng đoạn 10-K rồi trích quan hệ — đắt,
    chậm, và luôn có tỷ lệ sai. Phía Việt Nam thì không có văn bản để đọc, nhưng lại có
    thứ phía Mỹ không cho sẵn: bảng cổ đông đã có cấu trúc. Đo trên rổ VN30: 1.391 bản ghi,
    1.119 chủ sở hữu riêng biệt, và 85 chủ sở hữu nắm từ hai doanh nghiệp trở lên — tức là
    có đường đi bắc cầu thật giữa các doanh nghiệp Việt Nam.

    ⚠️ QUAN HỆ SỞ HỮU KHÔNG PHẢI QUAN HỆ KINH DOANH.

    Một quỹ ETF nắm cả FPT lẫn VNM không có nghĩa hai doanh nghiệp đó làm ăn với nhau —
    đó chỉ là danh mục đầu tư. Vì vậy cạnh sinh ra ở đây mang loại riêng `OWNED_BY`, tách
    hẳn khỏi `PARTNERS_WITH` hay `SUPPLIED_BY`, và tuyệt đối không được để agent suy ra
    quan hệ kinh doanh từ việc hai bên chung cổ đông. Nhóm thật sự có ý nghĩa phân tích là
    cổ đông nhà nước (SCIC), cổ đông chiến lược là doanh nghiệp, và người sáng lập.

    `min_percent` mặc định 0,5%: dưới ngưỡng đó phần lớn là nhà đầu tư nhỏ lẻ được công bố
    lẻ tẻ, thêm vào chỉ làm đồ thị nặng mà không mở ra đường đi nào.
    """
    symbol = symbol.strip().upper()
    data = _get(f"{_BASE}/{symbol}/shareholder")
    rows = data if isinstance(data, list) else (data.get("items") or [])

    out: List[Dict[str, Any]] = []
    for row in rows:
        # Ưu tiên tên tiếng Anh cho khớp với phần còn lại của đồ thị; tên tiếng Việt có
        # dấu sẽ không gộp được với node do bên Mỹ sinh ra ("Norges Bank" chẳng hạn).
        name = (row.get("ownerNameEn") or row.get("ownerName") or "").strip()
        pct = row.get("percentage")
        if not name or pct is None:
            continue
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        if pct < min_percent:
            continue

        kind = (row.get("ownerType") or "").strip().upper()
        is_person = kind == "INDIVIDUAL"

        # ⚠️ CÁ NHÂN PHẢI ĐƯỢC GIỚI HẠN TRONG TỪNG DOANH NGHIỆP, TỔ CHỨC THÌ KHÔNG.
        #
        # Nguồn không cấp mã định danh cho chủ sở hữu, nên node phải gộp theo TÊN. Với tổ
        # chức thì ổn — tên riêng và dài ("Norges Bank", "PYN Elite Fund", "Tổng Công ty
        # Đầu tư và Kinh doanh vốn Nhà nước"). Với cá nhân thì hỏng nặng.
        #
        # Đo trên 10.772 bản ghi cổ đông thật của 1.522 doanh nghiệp:
        #
        #     bắc cầu ≥2 doanh nghiệp   tổ chức 515   cá nhân 771
        #     GHÉP NHẦM chứng minh được tổ chức   2   cá nhân 121
        #
        # "Ghép nhầm chứng minh được" = cùng một tên tiếng Anh nhưng ứng với nhiều tên
        # tiếng Việt khác nhau, tức chắc chắn là những người khác nhau:
        #
        #     Nguyen Van Thanh  ->  Nguyễn Văn Thành / Nguyễn Văn Thạnh / Nguyễn Văn Thanh
        #     Nguyen Thi Thuy   ->  Nguyễn Thị Thuỷ / Nguyễn Thị Thùy / Nguyễn Thị Thủy
        #
        # Và 121 mới chỉ là CẬN DƯỚI: hai người trùng cả cách viết tiếng Việt thì không có
        # cách nào phát hiện. "Nguyen Van Thanh" đang đứng tên ở 12 doanh nghiệp.
        #
        # Nạp nguyên như vậy thì đồ thị sẽ khẳng định "FPT liên quan tới VNM qua ông Nguyễn
        # Văn Thành" — một đường đi nghe rất thuyết phục và hoàn toàn bịa. Đúng loại lỗi
        # "khớp sai một cách im lặng" mà `resolve_company` được viết ra để chặn.
        #
        # Nên tên hiển thị của cá nhân được gắn kèm mã doanh nghiệp. Cái giá phải trả là
        # mất vài cầu nối CÓ THẬT (ông Nguyễn Duy Hưng đúng là chủ tịch cả SSI lẫn PAN),
        # nhưng đổi lại tránh được 770 cầu nối BỊA. Đánh đổi đó không cần cân nhắc lâu.
        display = f"{name} ({symbol})" if is_person else name

        out.append({
            "ticker": f"{symbol}.VN",
            "symbol": symbol,
            "owner": display,
            "owner_raw": name,
            "owner_vi": (row.get("ownerName") or "").strip(),
            # Cố ý KHÔNG dùng nhãn Company cho bên nắm giữ: phần lớn là quỹ đầu tư và cá
            # nhân, không niêm yết và không có số liệu. Gộp chúng vào Company sẽ thổi phồng
            # đúng con số "doanh nghiệp" mà trang chủ vừa phải sửa cho khỏi đếm nhầm.
            "owner_label": "Person" if is_person else "Organization",
            "owner_kind": kind or "UNKNOWN",
            "percent": pct,
            "shares": row.get("quantity"),
            "position": (row.get("positionNameEn") or row.get("positionName") or "").strip(),
            # Ngày công bố = bằng chứng thời điểm. Tỷ lệ sở hữu thay đổi liên tục, nên một
            # con số không kèm ngày là con số không kiểm chứng được.
            "as_of": (row.get("publicDate") or row.get("updateDate") or "").strip(),
        })

    out.sort(key=lambda r: -r["percent"])
    return out


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
