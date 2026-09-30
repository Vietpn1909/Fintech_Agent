"""So sánh ngành tự động — đặt một doanh nghiệp cạnh những doanh nghiệp cùng loại.

VÌ SAO MỘT CON SỐ ĐƠN ĐỘC GẦN NHƯ VÔ NGHĨA

"Biên lợi nhuận ròng 11%" không nói lên điều gì cho tới khi biết những doanh nghiệp cùng
ngành đạt bao nhiêu. 11% là xuất sắc với bán lẻ và rất tệ với phần mềm. Hệ thống đã có
sẵn số liệu của 1.532 doanh nghiệp Việt Nam kèm trường ngành, tức là mẫu so sánh có sẵn —
chỉ là chưa ai ghép chúng lại.

⚠️ TÌM DOANH NGHIỆP CÙNG LOẠI BẰNG HAI CÁCH KHÁC HẲN NHAU, VÀ PHẢI NÓI RÕ DÙNG CÁCH NÀO

    ngành (sector)      doanh nghiệp Việt Nam có sẵn trường này, 13 ngành
    COMPETES_WITH       doanh nghiệp Mỹ không có trường ngành, nhưng có 215 cạnh đối thủ
                        do LLM trích từ chính hồ sơ 10-K

Hai cách cho ra hai thứ khác nhau về chất. Cách hai là đối thủ do CHÍNH DOANH NGHIỆP nêu
tên trong hồ sơ — chính xác hơn nhiều. Cách một chỉ là "cùng một ô phân loại".

⚠️ NGÀNH LÀ MỘT CÁI RỔ RẤT THÔ

Ngành "Basic Materials" trong dữ liệu chứa cả HPG (thép), GVR (cao su) và MSR (khai
khoáng). So biên lợi nhuận của thép với cao su rồi kết luận "đứng thứ 7/166" là một con
số đúng phép tính và sai về ý nghĩa. Vì vậy `phuong_phap` và `canh_bao` đi kèm mọi kết
quả, và với doanh nghiệp Mỹ thì danh sách đối thủ do chính họ nêu luôn được ưu tiên.

⚠️ TUYỆT ĐỐI KHÔNG SO GIỮA HAI ĐỒNG TIỀN

Doanh thu 138.855 tỷ VND và doanh thu 130 tỷ USD, đặt cạnh nhau trong một bảng xếp hạng,
sẽ cho ra thứ tự hoàn toàn bịa. Đây là loại lỗi không báo gì cả và trông rất thuyết phục.
Nên `_same_currency` lọc thẳng, và số doanh nghiệp bị loại vì khác đồng tiền được đếm và
nêu ra chứ không lọc im lặng.

⚠️ KHÔNG PHẢI CHỈ TIÊU NÀO NGÀNH NÀO CŨNG CÓ

Đo được: 28/28 ngân hàng Việt Nam KHÔNG có `gross_profit` — khái niệm ấy không tồn tại
với hoạt động tín dụng. Xếp hạng trên một chỉ tiêu mà nửa số doanh nghiệp không có sẽ cho
ra "đứng thứ 3" trong khi thực chất chỉ có 4 doanh nghiệp tham gia. Nên mỗi dòng xếp hạng
mang theo `so_doanh_nghiep_co_so_lieu`, và nó là mẫu số thật chứ không phải quy mô ngành.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agent.tools import _resolution_failure, _resolve, graph

# Chỉ tiêu thô lấy thẳng từ báo cáo. Xếp hạng theo những chỉ tiêu này đo QUY MÔ.
SIZE_METRICS = ["revenue", "net_income", "total_assets", "stockholders_equity"]

# Chỉ tiêu tỷ lệ — tính từ chỉ tiêu thô. Đây mới là thứ so sánh được giữa doanh nghiệp
# lớn và nhỏ, nên chúng quan trọng hơn hẳn nhóm trên trong một bảng so sánh ngành.
RATIO_METRICS = ["bien_loi_nhuan_gop", "bien_loi_nhuan_rong", "roe", "roa"]

METRIC_LABEL = {
    "revenue": "Doanh thu",
    "net_income": "Lợi nhuận sau thuế",
    "total_assets": "Tổng tài sản",
    "stockholders_equity": "Vốn chủ sở hữu",
    "bien_loi_nhuan_gop": "Biên lợi nhuận gộp",
    "bien_loi_nhuan_rong": "Biên lợi nhuận ròng",
    "roe": "ROE (lợi nhuận / vốn chủ)",
    "roa": "ROA (lợi nhuận / tổng tài sản)",
}

# Dưới mức này thì bảng xếp hạng không còn ý nghĩa thống kê nào — "đứng thứ 2 trong 3"
# là một câu vô dụng đội lốt một con số.
MIN_PEERS = 3

MAX_PEERS = 400


def _ratios(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Các tỷ lệ tài chính. None khi mẫu số thiếu hoặc bằng 0 — KHÔNG trả 0."""
    def div(num: Any, den: Any) -> Optional[float]:
        if num is None or not den:
            return None
        return num / den * 100.0

    return {
        "bien_loi_nhuan_gop": div(row.get("gross_profit"), row.get("revenue")),
        "bien_loi_nhuan_rong": div(row.get("net_income"), row.get("revenue")),
        "roe": div(row.get("net_income"), row.get("stockholders_equity")),
        "roa": div(row.get("net_income"), row.get("total_assets")),
    }


def find_peers(ticker: str) -> Dict[str, Any]:
    """Tìm doanh nghiệp cùng loại. Hai cách, ưu tiên cách chính xác hơn.

    Đối thủ do chính doanh nghiệp nêu tên trong hồ sơ luôn thắng ngành phân loại: một bên
    là lời khai của người trong cuộc, bên kia là một cái ô trong bảng mã.
    """
    named = graph().run(
        """
        MATCH (c:Company {ticker: $ticker})-[:COMPETES_WITH]-(p:Company)
        WHERE (p)-[:HAS_FINANCIALS]->() AND p.ticker <> $ticker
        RETURN DISTINCT p.ticker AS ticker, coalesce(p.name, p.ticker) AS name
        LIMIT $limit
        """,
        ticker=ticker, limit=MAX_PEERS,
    )
    if len(named) >= MIN_PEERS:
        return {
            "phuong_phap": "doi_thu_tu_ho_so",
            "mo_ta": "đối thủ do chính doanh nghiệp nêu tên trong hồ sơ 10-K",
            "peers": named,
            "canh_bao": ("Danh sách này do doanh nghiệp tự nêu nên phản ánh cách HỌ nhìn "
                         "thị trường, có thể bỏ sót đối thủ hoặc nêu tên doanh nghiệp "
                         "không thực sự cạnh tranh trực tiếp."),
        }

    sector_rows = graph().run(
        "MATCH (c:Company {ticker: $ticker}) RETURN c.sector AS sector, c.market AS market",
        ticker=ticker,
    )
    sector = (sector_rows[0].get("sector") if sector_rows else None)
    if not sector:
        return {
            "phuong_phap": "khong_co",
            "peers": [],
            # Nói rõ VÌ SAO không có, chứ không chỉ nói là không có. Hai câu này dẫn tới
            # hai phản ứng khác nhau của người dùng.
            "canh_bao": (f"Doanh nghiệp này không có trường ngành trong đồ thị và chỉ có "
                         f"{len(named)} đối thủ được nêu tên trong hồ sơ (cần ít nhất "
                         f"{MIN_PEERS}). Dữ liệu ngành hiện chỉ có với doanh nghiệp niêm "
                         f"yết tại Việt Nam."),
        }

    rows = graph().run(
        """
        MATCH (p:Company {sector: $sector, market: $market})
        WHERE p.ticker <> $ticker AND (p)-[:HAS_FINANCIALS]->()
        RETURN p.ticker AS ticker, coalesce(p.name, p.ticker) AS name LIMIT $limit
        """,
        sector=sector, market=sector_rows[0].get("market"), ticker=ticker, limit=MAX_PEERS,
    )
    return {
        "phuong_phap": "cung_nganh",
        "nganh": sector,
        "mo_ta": f"cùng ngành {sector}",
        "peers": rows,
        # Nêu tên ngành THẬT của doanh nghiệp đang xét chứ không nêu một ví dụ cố định:
        # câu cảnh báo có ví dụ "Basic Materials" nằm trong bảng của một doanh nghiệp
        # ngành Technology đọc như một lỗi, và người đọc sẽ bỏ qua cả câu.
        "canh_bao": (f"Nhóm so sánh được lấy theo ngành '{sector}' — một cách phân loại "
                     f"RẤT THÔ. Một ngành trong dữ liệu này có thể gộp những doanh nghiệp "
                     f"có mô hình kinh doanh khác hẳn nhau (ngành 'Basic Materials' chẳng "
                     f"hạn gồm cả thép, cao su và khai khoáng), nên thứ hạng nên đọc như "
                     f"một chỉ dấu chứ không phải một kết luận."),
    }


def _fetch_year(tickers: List[str], year: int) -> Dict[str, Dict[str, Any]]:
    """Số liệu một năm cho một loạt mã. Một truy vấn cho tất cả, không lặp từng mã."""
    rows = graph().run(
        """
        UNWIND $tickers AS t
        MATCH (c:Company {ticker: t})-[:HAS_FINANCIALS]->(f:FinancialYear)
        WHERE f.fiscal_year = $year
        RETURN t AS ticker, coalesce(c.name, c.ticker) AS name, f AS data
        """,
        tickers=tickers, year=year,
    )
    out = {}
    for row in rows:
        data = dict(row["data"])
        out[row["ticker"]] = {
            "ticker": row["ticker"], "name": row["name"],
            "currency": data.get("currency"),
            **{m: data.get(m) for m in SIZE_METRICS + ["gross_profit"]},
            **_ratios(data),
        }
    return out


def _pick_year(ticker: str, peer_tickers: List[str],
               year: Optional[int]) -> Dict[str, Any]:
    """Chọn năm so sánh — và nói rõ vì sao chọn năm đó.

    ⚠️ NĂM MỚI NHẤT CỦA DOANH NGHIỆP ĐANG XÉT THƯỜNG KHÔNG PHẢI NĂM SO SÁNH ĐƯỢC.

    Doanh nghiệp công bố sớm sẽ có năm 2025 trong khi phần lớn ngành còn dừng ở 2024. So
    một mình nó với ba doanh nghiệp cũng công bố sớm rồi gọi đó là "xếp hạng ngành" là
    sai lệch nặng, mà bảng kết quả trông vẫn bình thường. Nên ở đây chọn năm mới nhất mà
    doanh nghiệp đang xét CÓ SỐ LIỆU và ít nhất một nửa số doanh nghiệp cùng loại cũng có.
    """
    if year:
        return {"year": year, "ly_do": "do người dùng chỉ định"}

    own_years = [r["y"] for r in graph().run(
        "MATCH (c:Company {ticker: $t})-[:HAS_FINANCIALS]->(f) "
        "WHERE f.fiscal_year >= 1990 AND f.fiscal_year <= 2100 "
        "RETURN DISTINCT f.fiscal_year AS y ORDER BY y DESC LIMIT 6", t=ticker)]
    if not own_years:
        return {"year": None, "ly_do": "doanh nghiệp không có số liệu tài chính nào"}

    counts = {r["y"]: r["n"] for r in graph().run(
        "UNWIND $tickers AS t MATCH (c:Company {ticker: t})-[:HAS_FINANCIALS]->(f) "
        "WHERE f.fiscal_year IN $years RETURN f.fiscal_year AS y, count(*) AS n",
        tickers=peer_tickers, years=own_years)}

    need = max(MIN_PEERS, len(peer_tickers) // 2)
    for candidate in own_years:                      # đã sắp giảm dần
        if counts.get(candidate, 0) >= need:
            return {"year": candidate,
                    "ly_do": f"năm mới nhất mà cả doanh nghiệp và {counts[candidate]}/"
                             f"{len(peer_tickers)} doanh nghiệp cùng loại đều có số liệu"}

    newest = own_years[0]
    return {"year": newest,
            "ly_do": f"năm mới nhất của doanh nghiệp; chỉ {counts.get(newest, 0)}/"
                     f"{len(peer_tickers)} doanh nghiệp cùng loại có số liệu năm này, "
                     f"nên thứ hạng dưới đây KÉM tin cậy"}


def _same_currency(subject: Dict[str, Any],
                   peers: Dict[str, Dict[str, Any]]) -> tuple:
    """Loại doanh nghiệp khác đồng tiền, và ĐẾM số bị loại. Xem chú thích đầu tệp."""
    want = subject.get("currency")
    if not want:
        return peers, 0
    kept = {t: p for t, p in peers.items() if p.get("currency") == want}
    return kept, len(peers) - len(kept)


def _rank(subject_value: Optional[float], values: List[float],
          higher_is_better: bool = True) -> Optional[Dict[str, Any]]:
    """Thứ hạng của một giá trị trong một dãy. None khi doanh nghiệp không có chỉ tiêu đó.

    Mẫu số là SỐ DOANH NGHIỆP CÓ SỐ LIỆU CHỈ TIÊU NÀY, không phải quy mô ngành — xem cảnh
    báo cuối chú thích đầu tệp.
    """
    if subject_value is None or len(values) < MIN_PEERS:
        return None
    pool = sorted(values + [subject_value], reverse=higher_is_better)
    position = pool.index(subject_value) + 1
    total = len(pool)
    median = sorted(pool)[total // 2]
    return {
        "hang": position,
        # ⚠️ ĐÃ BAO GỒM CHÍNH DOANH NGHIỆP ĐANG XÉT.
        #
        # "hạng 3/25" nghĩa là trong 25 doanh nghiệp có số liệu chỉ tiêu này — 24 doanh
        # nghiệp cùng loại CỘNG chính nó. Không nói rõ thì lời văn sinh ra câu "so với 25
        # doanh nghiệp cùng ngành", lệch một đơn vị so với con số ghi ở tiêu đề mục, và
        # người đọc không biết tin con số nào.
        "so_doanh_nghiep_co_so_lieu": total,
        "bao_gom_chinh_no": True,
        # Phân vị: 100 là tốt nhất trong nhóm. Dễ đọc hơn "hạng 7/166" khi quy mô nhóm
        # thay đổi giữa các chỉ tiêu.
        "phan_vi": round((total - position) / (total - 1) * 100.0, 1) if total > 1 else None,
        "trung_vi_nhom": median,
    }


def benchmark(company: str, year: Optional[int] = None,
              limit_table: int = 10) -> Dict[str, Any]:
    """So sánh một doanh nghiệp với nhóm cùng loại. Danh sách bước CỐ ĐỊNH, số do mã tính."""
    resolved = _resolve(company)
    if resolved["status"] != "ok":
        return _resolution_failure(company, resolved)

    best = resolved["best"]
    ticker = best["ticker"]
    peer_info = find_peers(ticker)
    peer_tickers = [p["ticker"] for p in peer_info["peers"]]

    if len(peer_tickers) < MIN_PEERS:
        return {"status": "khong_du_doanh_nghiep_cung_loai", "ticker": ticker,
                "company": best["name"], "tim_thay": len(peer_tickers),
                "can_it_nhat": MIN_PEERS, "phuong_phap": peer_info["phuong_phap"],
                "canh_bao": peer_info.get("canh_bao", "")}

    chosen = _pick_year(ticker, peer_tickers, year)
    if not chosen["year"]:
        return {"status": "no_data", "ticker": ticker, "company": best["name"],
                "ly_do": chosen["ly_do"]}

    rows = _fetch_year([ticker] + peer_tickers, chosen["year"])
    subject = rows.get(ticker)
    if not subject:
        return {"status": "no_data", "ticker": ticker, "company": best["name"],
                "ly_do": f"không có số liệu năm {chosen['year']}"}

    peers_data = {t: r for t, r in rows.items() if t != ticker}
    peers_data, dropped_currency = _same_currency(subject, peers_data)

    if len(peers_data) < MIN_PEERS:
        return {"status": "khong_du_doanh_nghiep_cung_loai", "ticker": ticker,
                "company": best["name"], "tim_thay": len(peers_data),
                "can_it_nhat": MIN_PEERS,
                "canh_bao": (f"Chỉ còn {len(peers_data)} doanh nghiệp sau khi loại "
                             f"{dropped_currency} doanh nghiệp khác đồng tiền và những "
                             f"doanh nghiệp không có số liệu năm {chosen['year']}.")}

    rankings = {}
    for metric in SIZE_METRICS + RATIO_METRICS:
        values = [p[metric] for p in peers_data.values() if p.get(metric) is not None]
        result = _rank(subject.get(metric), values)
        if result:
            rankings[metric] = {
                "nhan": METRIC_LABEL[metric],
                "gia_tri": (round(subject[metric], 2) if metric in RATIO_METRICS
                            else subject[metric]),
                "la_ty_le": metric in RATIO_METRICS,
                **result,
            }

    # Bảng doanh nghiệp cùng loại, xếp theo quy mô doanh thu. Cắt ngắn để không đổ 166
    # dòng vào ngữ cảnh của LLM — và nói rõ đã cắt.
    table = sorted(
        [p for p in peers_data.values() if p.get("revenue") is not None],
        key=lambda p: p["revenue"], reverse=True,
    )

    return {
        "status": "ok",
        "ticker": ticker, "company": best["name"],
        "nam": chosen["year"], "ly_do_chon_nam": chosen["ly_do"],
        "currency": subject.get("currency"),
        "phuong_phap": peer_info["phuong_phap"],
        "mo_ta_nhom": peer_info["mo_ta"],
        "nganh": peer_info.get("nganh"),
        "so_doanh_nghiep_cung_loai": len(peers_data),
        "loai_vi_khac_dong_tien": dropped_currency,
        "doanh_nghiep_dang_xet": subject,
        "xep_hang": rankings,
        "bang_cung_loai": table[:limit_table],
        "da_cat_bot": max(0, len(table) - limit_table),
        "canh_bao": peer_info.get("canh_bao", ""),
        "gaps": _gaps(peer_info, rankings, dropped_currency, chosen),
    }


def _gaps(peer_info: Dict[str, Any], rankings: Dict[str, Any],
          dropped_currency: int, chosen: Dict[str, Any]) -> List[str]:
    """Điều bảng so sánh này KHÔNG nói được — sinh từ chính kết quả, không viết tay."""
    out: List[str] = []
    missing = [METRIC_LABEL[m] for m in SIZE_METRICS + RATIO_METRICS
               if m not in rankings]
    if missing:
        out.append("Không xếp hạng được các chỉ tiêu: " + ", ".join(missing) +
                   " — doanh nghiệp không có chỉ tiêu này, hoặc quá ít doanh nghiệp cùng "
                   "loại có để so. Ngân hàng chẳng hạn không có khái niệm lợi nhuận gộp.")
    if dropped_currency:
        out.append(f"{dropped_currency} doanh nghiệp bị loại khỏi so sánh vì báo cáo bằng "
                   "đồng tiền khác — không quy đổi, vì tỷ giá tại ngày nào cũng là một "
                   "lựa chọn làm đổi kết quả.")
    if "KÉM tin cậy" in chosen["ly_do"]:
        out.append("Năm so sánh có rất ít doanh nghiệp cùng loại công bố số liệu; thứ "
                   "hạng ở trên dựa trên một mẫu nhỏ và lệch về phía doanh nghiệp công "
                   "bố sớm.")
    out.append("So sánh chỉ dựa trên báo cáo tài chính năm. Không có giá cổ phiếu nên "
               "không tính được P/E, P/B hay bất kỳ chỉ số định giá nào.")
    # Nói rõ nhóm so sánh được dựng bằng cách nào: hai cách cho ra hai thứ khác nhau về
    # chất, và người đọc cần biết mình đang xem cái nào.
    if peer_info.get("phuong_phap") == "cung_nganh":
        out.append(f"Nhóm so sánh dựng theo ngành '{peer_info.get('nganh')}', không phải "
                   "theo đối thủ cạnh tranh thật sự — hệ thống không có dữ liệu đối thủ "
                   "cho doanh nghiệp niêm yết tại Việt Nam.")
    elif peer_info.get("phuong_phap") == "doi_thu_tu_ho_so":
        out.append("Nhóm so sánh là đối thủ do CHÍNH doanh nghiệp nêu tên trong hồ sơ "
                   "10-K, nên nó phản ánh cách họ nhìn thị trường và có thể bỏ sót.")

    out.append("Thứ hạng là mô tả dữ liệu, KHÔNG phải đánh giá đầu tư: doanh nghiệp đứng "
               "đầu về biên lợi nhuận vẫn có thể là khoản đầu tư tệ, và ngược lại.")
    return out
