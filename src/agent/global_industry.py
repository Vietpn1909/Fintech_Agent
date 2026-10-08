"""Tín hiệu ngành toàn cầu — doanh nghiệp Mỹ cùng ngành đang tăng hay giảm.

VÌ SAO DOANH NGHIỆP MỸ LIÊN QUAN TỚI GỢI Ý CHO DOANH NGHIỆP VIỆT NAM

Ngành ở hai nước chịu chung một chu kỳ: giá thép thế giới đi xuống thì Hòa Phát lẫn
Nucor cùng chịu; khách hàng Mỹ cắt ngân sách công nghệ thì FPT lẫn các hãng dịch vụ IT Mỹ
cùng mất đơn. Doanh nghiệp Mỹ công bố số liệu đầy đủ và nhanh qua SEC, nên tình hình của
họ là một chỉ dấu sớm cho ngành tương ứng ở Việt Nam.

⚠️ ĐÂY LÀ TÍN HIỆU NGÀNH, KHÔNG PHẢI SO SÁNH TRỰC TIẾP

Không đặt doanh thu Hòa Phát cạnh doanh thu Nucor — khác đồng tiền, khác quy mô, khác
thị trường. Chỉ dùng TỶ LỆ không có đơn vị (tăng trưởng doanh thu, thay đổi biên lợi
nhuận) và lấy TRUNG VỊ của cả nhóm, để vài doanh nghiệp bất thường không kéo lệch.

⚠️ BẢNG ÁNH XẠ NGÀNH LÀ GIẢ ĐỊNH, VÀ NÓ THÔ

13 ngành Việt Nam (theo VCI) được ánh xạ sang khoảng mã SIC của Mỹ bằng tay. Ngành
"Basic Materials" của Việt Nam gom cả thép, hóa chất, cao su — nên nhóm Mỹ tương ứng cũng
gom đủ ba thứ. Bảng công bố nguyên văn ở `SECTOR_SIC` để người đọc kiểm tra được.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.agent.tools import graph

# Ngành Việt Nam (trường `sector` của VCI) -> các khoảng mã SIC của Mỹ, hai đầu bao gồm.
SECTOR_SIC: Dict[str, List[Tuple[int, int]]] = {
    "Banks":              [(6020, 6029), (6035, 6036)],                 # ngân hàng thương mại, tiết kiệm
    "Financial Services": [(6200, 6211), (6282, 6282)],                 # môi giới chứng khoán, tư vấn đầu tư
    "Insurance":          [(6311, 6411)],                               # bảo hiểm các loại
    "Real Estate":        [(6500, 6553), (6798, 6798)],                 # bất động sản, REIT
    "Technology":         [(7370, 7379), (3570, 3579), (3670, 3679)],   # phần mềm/dịch vụ IT, máy tính, bán dẫn
    "Telecommunications": [(4810, 4899)],                               # viễn thông
    "Oil & Gas":          [(1311, 1311), (1381, 1389), (2911, 2911), (4922, 4924)],
    "Utilities":          [(4911, 4991)],                               # điện, nước, phân phối khí
    "Basic Materials":    [(3310, 3317), (3330, 3399), (2800, 2829), (2860, 2899),
                           (1000, 1099), (1400, 1499), (3010, 3069)],   # thép, kim loại, hóa chất, khai khoáng, cao su
    "Industrials":        [(1500, 1799), (3400, 3569), (3580, 3599), (3700, 3799),
                           (4000, 4789), (8700, 8748)],                 # xây dựng, máy móc, vận tải, kỹ thuật
    "Consumer Goods":     [(2000, 2199), (2200, 2399), (2840, 2844), (3140, 3149),
                           (3630, 3639)],                               # thực phẩm, đồ uống, dệt may, mỹ phẩm, đồ gia dụng
    "Consumer Services":  [(5200, 5999), (4512, 4513), (7000, 7099), (7800, 7999)],  # bán lẻ, hàng không, khách sạn, giải trí
    "Health Care":        [(2830, 2836), (3840, 3851), (8000, 8099)],   # dược, thiết bị y tế, dịch vụ y tế
}

# Dưới mức này thì trung vị không đáng tin — vài doanh nghiệp là kéo lệch được.
MIN_COMPANIES = 8

# Chỉ lấy năm tài chính kết thúc trong 24 tháng gần đây. Lấy năm cũ hơn thì "tình hình
# ngành" thực chất là tình hình của hai năm trước.
MAX_AGE_DAYS = 730

# Tăng trưởng doanh thu vượt ±150% gần như luôn là sáp nhập hoặc nền quá nhỏ, không phải
# tín hiệu ngành. Loại ra trước khi lấy trung vị — và đếm số bị loại.
MAX_ABS_GROWTH = 150.0


def _linear(value: float, worst: float, best: float) -> float:
    """Quy một giá trị về thang 0–100 theo đường thẳng giữa `worst` và `best`."""
    if best == worst:
        return 50.0
    return max(0.0, min(100.0, (value - worst) / (best - worst) * 100.0))


def _sic_filter(ranges: List[Tuple[int, int]]) -> str:
    return " OR ".join(f"(c.sic >= {lo} AND c.sic <= {hi})" for lo, hi in ranges)


def score(sector: Optional[str], today: Optional[date] = None) -> Dict[str, Any]:
    """Điểm 0–100 cho tình hình ngành tương ứng ở Mỹ. 50 là trung tính.

    Không tính được thì trả `status` khác "ok" — bên gọi phải loại nhóm này khỏi điểm
    tổng, không được coi như 50 trung tính. Xem lý do ở `macro.sector_score`.
    """
    ranges = SECTOR_SIC.get(sector or "")
    if not ranges:
        return {"status": "khong_co_anh_xa",
                "ly_do": f"Chưa có ánh xạ ngành '{sector}' sang mã SIC của Mỹ."}

    today = today or date.today()
    since = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()

    # Mã SIC là số nguyên do chính mã lệnh này viết sẵn — không có giá trị nào từ người
    # dùng được ghép vào câu truy vấn.
    rows = graph().run(
        f"""
        MATCH (c:Company)-[:HAS_FINANCIALS]->(f:FinancialYear)
        WHERE c.cik IS NOT NULL AND c.sic IS NOT NULL AND ({_sic_filter(ranges)})
          AND f.revenue IS NOT NULL AND f.revenue > 0 AND f.period_end IS NOT NULL
        WITH c, f ORDER BY f.period_end DESC
        WITH c, collect(f)[0..2] AS last2
        WHERE size(last2) = 2 AND last2[0].period_end >= $since
          AND last2[0].period_end <= $today
        RETURN c.ticker AS ticker,
               last2[0].revenue AS rev1, last2[1].revenue AS rev0,
               last2[0].net_income AS ni1, last2[1].net_income AS ni0,
               last2[0].period_end AS period_end
        """,
        since=since, today=today.isoformat(),
    )

    growth, margin_change, excluded = [], [], 0
    for r in rows:
        g = (r["rev1"] - r["rev0"]) / abs(r["rev0"]) * 100.0
        if abs(g) > MAX_ABS_GROWTH:
            excluded += 1
            continue
        growth.append(g)
        if r["ni1"] is not None and r["ni0"] is not None:
            margin_change.append(r["ni1"] / r["rev1"] * 100.0 - r["ni0"] / r["rev0"] * 100.0)

    if len(growth) < MIN_COMPANIES:
        return {"status": "qua_it_doanh_nghiep", "so_doanh_nghiep": len(growth),
                "can_it_nhat": MIN_COMPANIES,
                "ly_do": (f"Chỉ có {len(growth)} doanh nghiệp Mỹ cùng ngành có số liệu mới — "
                          f"quá ít để lấy trung vị tin cậy.")}

    med_growth = statistics.median(growth)
    med_margin = statistics.median(margin_change) if len(margin_change) >= MIN_COMPANIES else None

    # Tăng trưởng doanh thu quyết định chính; thay đổi biên lợi nhuận bổ sung. Biên lợi
    # nhuận thiếu thì dùng mỗi tăng trưởng — và ghi rõ.
    s_growth = _linear(med_growth, -10.0, 15.0)
    if med_margin is not None:
        s_margin = _linear(med_margin, -3.0, 3.0)
        diem = 0.6 * s_growth + 0.4 * s_margin
    else:
        s_margin, diem = None, s_growth

    return {
        "status": "ok",
        "nganh": sector,
        "diem": round(diem, 1),
        "so_doanh_nghiep_my": len(growth),
        "bi_loai_vi_bat_thuong": excluded,
        "trung_vi_tang_truong_doanh_thu_pct": round(med_growth, 1),
        "trung_vi_thay_doi_bien_rong_diem_pct": round(med_margin, 2) if med_margin is not None else None,
        "ky_so_lieu": f"năm tài chính kết thúc từ {since} tới nay",
        "y_nghia": ("Trung vị của các doanh nghiệp Mỹ cùng ngành (theo mã SIC) giữa hai năm "
                    "tài chính gần nhất. Chỉ dùng tỷ lệ không đơn vị nên không trộn đồng tiền."),
    }
