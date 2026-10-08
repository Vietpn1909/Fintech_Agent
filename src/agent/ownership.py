"""Mạng lưới sở hữu — ai thực sự đứng sau một doanh nghiệp, qua bao nhiêu tầng.

VÌ SAO ĐÂY LÀ THỨ CHỈ ĐỒ THỊ LÀM ĐƯỢC

Danh sách cổ đông trực tiếp thì trang nào cũng có. Thứ không tra được ở đâu là câu hỏi
tầng hai: "ngoài những cái tên in trên bản công bố, còn ai nắm doanh nghiệp này qua một
pháp nhân trung gian?" Muốn trả lời phải đi ngược chuỗi sở hữu, và đó đúng là phép toán
mà cơ sở dữ liệu quan hệ làm rất tệ còn đồ thị làm rất tự nhiên.

Đo được trong đồ thị hiện tại: 891 cạnh sở hữu giữa hai doanh nghiệp, 511 chuỗi hai tầng,
283 chuỗi ba tầng. Đủ để có thứ thật mà phân tích, không phải ví dụ giả.

⚠️ HAI KHÁI NIỆM KHÁC HẲN NHAU MÀ AI CŨNG GỘP: QUYỀN LỢI KINH TẾ ≠ QUYỀN KIỂM SOÁT

Đây là chỗ quan trọng nhất của cả tệp này.

    A nắm 51% của B, B nắm 51% của C.

    Quyền lợi kinh tế của A trong C là 51% × 51% = 26,01%. Đây là phần lãi A thực nhận.

    Quyền KIỂM SOÁT thì khác hẳn: A quyết được mọi việc ở B, mà B quyết được mọi việc ở
    C, nên A kiểm soát C HOÀN TOÀN. Không phải 26%.

Nhân phần trăm rồi gọi kết quả là "mức độ kiểm soát" là sai lầm kinh điển, và nó sai theo
hướng nguy hiểm: nó làm một quan hệ kiểm soát tuyệt đối trông như một khoản đầu tư nhỏ.
Chiều ngược lại cũng sai không kém — A nắm 1% của X, X nắm 43% của Y, thì A chẳng kiểm
soát gì cả dù chuỗi có tồn tại.

Nên mỗi chuỗi ở đây mang HAI con số, và chúng được đặt tên khác nhau rõ ràng:

    quyen_loi_kinh_te   tích các tỷ lệ — phần lãi thực nhận
    chuoi_nay_kiem_soat     True chỉ khi MỌI mắt xích đều trên 50%

⚠️ CÙNG CỔ ĐÔNG KHÔNG PHẢI CÙNG TẬP ĐOÀN

`common_holders` tìm hai doanh nghiệp có chung cổ đông. Phần lớn kết quả sẽ là các quỹ
đầu tư nắm vài phần trăm ở hàng chục mã — Dragon Capital có mặt ở khắp nơi. Điều đó
KHÔNG nói lên quan hệ nào giữa hai doanh nghiệp, nó chỉ nói cả hai đều nằm trong danh mục
của một quỹ. Vì vậy hàm trả về `y_nghia` nói thẳng điều này, và xếp cổ đông chi phối
(trên 20% ở cả hai bên) lên đầu vì chỉ loại đó mới thật sự đáng chú ý.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agent.tools import _resolution_failure, _resolve, graph

# Ngưỡng coi là "kiểm soát" một mắt xích. Trên 50% thì nắm đa số biểu quyết, quyết được
# mọi việc thông thường. Đây là quy ước phổ biến; luật doanh nghiệp Việt Nam còn có các
# ngưỡng 36% và 65% cho từng loại quyết định, nhưng đưa cả ba vào chỉ làm rối mà không
# thêm được gì cho một hệ thống không đọc điều lệ từng doanh nghiệp.
CONTROL_THRESHOLD = 50.0

# Bỏ qua những chuỗi có quyền lợi kinh tế nhỏ hơn mức này. Không có nó, một doanh nghiệp
# sẽ trả về hàng trăm chuỗi kiểu "0,004%" — đúng về mặt toán học, vô dụng với người đọc.
MIN_ECONOMIC_PCT = 0.5

MAX_DEPTH = 4


def _pct(value: Any) -> Optional[float]:
    """Đổi `percent` trên cạnh (PHÂN SỐ, 0,4654) sang phần trăm. None khi thiếu."""
    try:
        return float(value) * 100.0
    except (TypeError, ValueError):
        return None


def _chain_from(names: List[str], percents: List[Any]) -> Optional[Dict[str, Any]]:
    """Dựng một chuỗi sở hữu từ danh sách tên và tỷ lệ dọc đường đi.

    Trả None khi có mắt xích thiếu tỷ lệ: tích của một dãy có lỗ hổng không phải là ước
    lượng dè dặt, nó là một con số bịa. Thà không nói còn hơn nói một con số không biết
    từ đâu ra — đúng nguyên tắc của cả hệ thống.
    """
    steps = [_pct(p) for p in percents]
    if not steps or any(s is None for s in steps):
        return None

    economic = 1.0
    for s in steps:
        economic *= s / 100.0

    return {
        "chuoi": names,
        "so_tang": len(steps),
        "tung_tang_pct": [round(s, 2) for s in steps],
        # Tích các tỷ lệ — phần lãi thực nhận. Xem chú thích đầu tệp.
        "quyen_loi_kinh_te_pct": round(economic * 100.0, 3),
        # ⚠️ TÊN TRƯỜNG CÓ CHỮ "NÀY" LÀ CỐ Ý, KHÔNG PHẢI THỪA.
        #
        # Nó đúng cho RIÊNG chuỗi này, không phải cho cả quan hệ giữa hai doanh nghiệp.
        # Khi có nhiều đường sở hữu song song, đọc boolean của một đường rồi kết luận
        # chung là sai — và đã sai thật: hỏi "Vinamilk kiểm soát Mộc Châu ở mức nào", mô
        # hình đọc `false` của đường trực tiếp 8,85% rồi kết luận không có quyền kiểm
        # soát, trong khi đường qua Vilico có `true`.
        #
        # Kết luận cho cả quan hệ nằm ở `ket_luan` của `chain_between`.
        "chuoi_nay_kiem_soat": all(s > CONTROL_THRESHOLD for s in steps),
        # Mắt xích yếu nhất: chuỗi chỉ mạnh bằng chỗ mỏng nhất của nó.
        "mat_xich_yeu_nhat_pct": round(min(steps), 2),
    }


def upstream(ticker: str, depth: int = 3, min_pct: float = MIN_ECONOMIC_PCT,
             limit: int = 40) -> Dict[str, Any]:
    """Ai đứng sau doanh nghiệp này — trực tiếp và qua trung gian.

    Chiều cạnh trong đồ thị là (doanh nghiệp)-[:OWNED_BY]->(chủ sở hữu), nên đi XUÔI theo
    chiều cạnh là đi NGƯỢC lên phía chủ sở hữu. Nhầm chiều ở đây cho ra một kết quả trông
    hoàn toàn hợp lý và sai hoàn toàn — lỗi này đã từng xảy ra thật trong dự án, xem chú
    thích ở `GraphStore.upsert_shareholders`.
    """
    depth = max(1, min(depth, MAX_DEPTH))
    rows = graph().run(
        f"""
        MATCH p = (c:Company {{ticker: $ticker}})-[:OWNED_BY*1..{depth}]->(owner)
        WHERE ALL(n IN nodes(p) WHERE single(m IN nodes(p) WHERE m = n))
        RETURN [n IN nodes(p) | coalesce(n.name, n.ticker, '?')] AS names,
               [r IN relationships(p) | r.percent] AS percents,
               labels(owner) AS owner_labels,
               coalesce(owner.name, owner.ticker, '?') AS owner_name,
               owner.ticker AS owner_ticker
        LIMIT 500
        """,
        ticker=ticker,
    )
    return _assemble(rows, "owner_name", "owner_ticker", "owner_labels", min_pct, limit)


def downstream(ticker: str, depth: int = 3, min_pct: float = MIN_ECONOMIC_PCT,
               limit: int = 40) -> Dict[str, Any]:
    """Doanh nghiệp này nắm những gì — trực tiếp và qua công ty con.

    Đi NGƯỢC chiều cạnh. Chỉ ra kết quả khi doanh nghiệp đứng tên cổ đông của doanh
    nghiệp khác, tức là phần cấu trúc tập đoàn — thứ mà tầng số liệu không thể hiện.
    """
    depth = max(1, min(depth, MAX_DEPTH))
    rows = graph().run(
        f"""
        MATCH p = (held:Company)-[:OWNED_BY*1..{depth}]->(c:Company {{ticker: $ticker}})
        WHERE ALL(n IN nodes(p) WHERE single(m IN nodes(p) WHERE m = n))
        RETURN [n IN reverse(nodes(p)) | coalesce(n.name, n.ticker, '?')] AS names,
               [r IN reverse(relationships(p)) | r.percent] AS percents,
               labels(held) AS owner_labels,
               coalesce(held.name, held.ticker, '?') AS owner_name,
               held.ticker AS owner_ticker
        LIMIT 500
        """,
        ticker=ticker,
    )
    return _assemble(rows, "owner_name", "owner_ticker", "owner_labels", min_pct, limit)


def _assemble(rows: List[Dict[str, Any]], name_key: str, ticker_key: str,
              label_key: str, min_pct: float, limit: int) -> Dict[str, Any]:
    """Gom các đường đi thô thành danh sách chuỗi đã tính toán, kèm những gì bị loại ra.

    ⚠️ ĐẾM CẢ PHẦN BỊ LOẠI VÀ NÓI RA.

    Lọc bỏ chuỗi nhỏ và chuỗi thiếu tỷ lệ là đúng, nhưng lọc im lặng thì người đọc thấy
    "3 chủ sở hữu gián tiếp" và tưởng đó là toàn bộ. Hai con số `bo_qua_vi_nho` và
    `bo_qua_vi_thieu_ty_le` giữ cho câu trả lời thành thật về chính phạm vi của nó.
    """
    chains, skipped_small, skipped_missing = [], 0, 0
    for row in rows:
        chain = _chain_from(row["names"], row["percents"])
        if chain is None:
            skipped_missing += 1
            continue
        if chain["quyen_loi_kinh_te_pct"] < min_pct:
            skipped_small += 1
            continue
        labels = row.get(label_key) or []
        chains.append({
            **chain,
            "ten": row[name_key],
            "ma": row.get(ticker_key),
            # Cổ đông là cá nhân, tổ chức hay chính một doanh nghiệp niêm yết — ba loại
            # có ý nghĩa rất khác nhau khi đọc cấu trúc sở hữu.
            "loai": ("doanh nghiệp" if "Company" in labels
                     else "cá nhân" if "Person" in labels else "tổ chức"),
        })

    # Chuỗi kiểm soát lên trước, rồi tới quyền lợi kinh tế lớn. Một chuỗi kiểm soát 26%
    # đáng chú ý hơn một khoản đầu tư 30% không kiểm soát, nên không thể xếp thuần theo số.
    chains.sort(key=lambda c: (not c["chuoi_nay_kiem_soat"], -c["quyen_loi_kinh_te_pct"]))

    return {
        "status": "ok",
        "truc_tiep": [c for c in chains if c["so_tang"] == 1][:limit],
        "gian_tiep": [c for c in chains if c["so_tang"] > 1][:limit],
        "tong_chuoi": len(chains),
        "bo_qua_vi_nho": skipped_small,
        "bo_qua_vi_thieu_ty_le": skipped_missing,
    }


def chain_between(ticker_a: str, ticker_b: str, depth: int = MAX_DEPTH) -> Dict[str, Any]:
    """Bên này có nắm bên kia không — trực tiếp hay qua trung gian, theo CẢ HAI chiều.

    ⚠️ HÀM NÀY SINH RA TỪ MỘT LỖI ĐO ĐƯỢC, KHÔNG PHẢI TỪ SUY ĐOÁN.

    Hỏi "Vinamilk kiểm soát Mộc Châu Milk ở mức nào", agent chỉ trả về 8,9% — phần nắm
    TRỰC TIẾP — và bỏ mất chuỗi Vinamilk → Vilico (68,94%) → Mộc Châu (59,3%), tức 40,88%
    quyền lợi kinh tế VÀ quyền kiểm soát thật sự. Câu trả lời đọc đầy đủ, dẫn nguồn đúng,
    và sai ở đúng chỗ người hỏi cần biết.

    Nguyên nhân: câu hỏi về HAI doanh nghiệp rơi vào nhánh `common_holders`, mà hàm ấy chỉ
    hỏi "hai bên có cổ đông chung không" — một câu hỏi khác hẳn. Khi một bên nắm bên kia
    thì đó mới là quan hệ cần nêu, và nó phải được nêu TRƯỚC.
    """
    depth = max(1, min(depth, MAX_DEPTH))
    out: Dict[str, List[Dict[str, Any]]] = {"a_nam_b": [], "b_nam_a": []}

    # Chiều cạnh là (bị nắm)-[:OWNED_BY]->(chủ sở hữu), nên "a nắm b" là đường đi từ b
    # tới a. Viết ngược ở đây là đảo trọn ý nghĩa mà kết quả vẫn trông hợp lý.
    for key, (start, end) in (("a_nam_b", (ticker_b, ticker_a)),
                              ("b_nam_a", (ticker_a, ticker_b))):
        rows = graph().run(
            f"""
            MATCH p = (s:Company {{ticker: $start}})-[:OWNED_BY*1..{depth}]->(e:Company {{ticker: $end}})
            WHERE ALL(n IN nodes(p) WHERE single(m IN nodes(p) WHERE m = n))
            RETURN [n IN reverse(nodes(p)) | coalesce(n.name, n.ticker, '?')] AS names,
                   [r IN reverse(relationships(p)) | r.percent] AS percents
            LIMIT 100
            """,
            start=start, end=end,
        )
        chains = [c for c in (_chain_from(r["names"], r["percents"]) for r in rows) if c]
        chains.sort(key=lambda c: (not c["chuoi_nay_kiem_soat"], -c["quyen_loi_kinh_te_pct"]))
        out[key] = chains

    # Tổng quyền lợi kinh tế khi có NHIỀU đường: các đường rời nhau thì cộng được. Đây là
    # ước lượng, vì hai đường có thể đi qua cùng một phần vốn — nên nói rõ là ước lượng.
    def total(chains: List[Dict[str, Any]]) -> Optional[float]:
        return round(sum(c["quyen_loi_kinh_te_pct"] for c in chains), 3) if chains else None

    a_ctrl = any(c["chuoi_nay_kiem_soat"] for c in out["a_nam_b"])
    b_ctrl = any(c["chuoi_nay_kiem_soat"] for c in out["b_nam_a"])

    # ⚠️ KẾT LUẬN VIẾT SẴN BẰNG LỜI, KHÔNG ĐỂ MÔ HÌNH TỰ SUY.
    #
    # Mô hình đọc một danh sách chuỗi, mỗi chuỗi một boolean, rồi phải tự tổng hợp thành
    # một câu. Đo được: nó lấy boolean của chuỗi ĐẦU TIÊN nó đọc kỹ và kết luận theo đó.
    # Câu kết luận nằm sẵn ở đây thì không còn phép suy nào để làm sai.
    if a_ctrl or b_ctrl:
        owner = ticker_a if a_ctrl else ticker_b
        owned = ticker_b if a_ctrl else ticker_a
        conclusion = (f"{owner} KIỂM SOÁT {owned}: có ít nhất một chuỗi sở hữu mà mọi mắt "
                      f"xích đều trên 50%. Quyền chi phối ở đây lớn hơn nhiều so với con "
                      f"số phần trăm quyền lợi kinh tế, và KHÔNG được mô tả bằng con số "
                      f"ấy. Đừng đọc trường chuoi_nay_kiem_soat của một chuỗi riêng lẻ "
                      f"rồi kết luận ngược lại câu này.")
    elif out["a_nam_b"] or out["b_nam_a"]:
        owner = ticker_a if out["a_nam_b"] else ticker_b
        owned = ticker_b if out["a_nam_b"] else ticker_a
        conclusion = (f"{owner} có nắm cổ phần của {owned} nhưng KHÔNG kiểm soát: không "
                      f"chuỗi nào nắm đa số ở mọi mắt xích.")
    else:
        conclusion = "Không bên nào nắm cổ phần của bên kia trong dữ liệu."

    return {
        "ket_luan": conclusion,
        "a_nam_b": out["a_nam_b"],
        "b_nam_a": out["b_nam_a"],
        "a_nam_b_tong_uoc_tinh_pct": total(out["a_nam_b"]),
        "b_nam_a_tong_uoc_tinh_pct": total(out["b_nam_a"]),
        "a_kiem_soat_b": a_ctrl,
        "b_kiem_soat_a": b_ctrl,
        "luu_y_tong": ("Tổng quyền lợi kinh tế là ước tính bằng cách cộng các đường sở "
                       "hữu rời nhau; nếu hai đường đi qua cùng một phần vốn thì con số "
                       "này cao hơn thực tế."),
    }


def common_holders(ticker_a: str, ticker_b: str, limit: int = 20) -> Dict[str, Any]:
    """Cổ đông mà hai doanh nghiệp cùng có, VÀ quan hệ sở hữu trực tiếp giữa hai bên.

    Xem cảnh báo đầu tệp: phần lớn cổ đông chung là quỹ đầu tư, và điều đó không nói lên
    quan hệ nào giữa hai doanh nghiệp. Quan hệ đáng nói hơn hẳn — một bên NẮM bên kia —
    nằm ở khóa `quan_he_so_huu_truc_tiep`, và nó được đặt lên trước trong `y_nghia`.
    """
    rows = graph().run(
        """
        MATCH (a:Company {ticker: $a})-[r1:OWNED_BY]->(o)<-[r2:OWNED_BY]-(b:Company {ticker: $b})
        RETURN coalesce(o.name, o.ticker, '?') AS holder, labels(o) AS labels,
               r1.percent AS pct_a, r2.percent AS pct_b
        ORDER BY coalesce(r1.percent, 0) + coalesce(r2.percent, 0) DESC
        LIMIT $limit
        """,
        a=ticker_a, b=ticker_b, limit=limit,
    )
    shared = []
    for row in rows:
        pa, pb = _pct(row["pct_a"]), _pct(row["pct_b"])
        shared.append({
            "ten": row["holder"],
            "loai": ("doanh nghiệp" if "Company" in (row["labels"] or [])
                     else "cá nhân" if "Person" in (row["labels"] or []) else "tổ chức"),
            "ty_le_a_pct": round(pa, 2) if pa is not None else None,
            "ty_le_b_pct": round(pb, 2) if pb is not None else None,
            # Chỉ cổ đông lớn ở CẢ HAI bên mới là tín hiệu đáng chú ý. Dưới ngưỡng đó thì
            # đây gần như chắc chắn chỉ là trùng danh mục đầu tư.
            "chi_phoi_ca_hai": bool(pa and pb and pa >= 20.0 and pb >= 20.0),
        })
    strong = [s for s in shared if s["chi_phoi_ca_hai"]]
    direct = chain_between(ticker_a, ticker_b)

    # Quan hệ "bên này nắm bên kia" luôn quan trọng hơn "hai bên có chung cổ đông", nên
    # khi có thì nó chiếm chỗ đầu của `y_nghia` — thứ tự ở đây quyết định agent viết gì
    # vào câu đầu tiên của câu trả lời.
    if direct["a_nam_b"] or direct["b_nam_a"]:
        if direct["a_nam_b"]:
            owner, owned = ticker_a, ticker_b
            total = direct["a_nam_b_tong_uoc_tinh_pct"]
            controls = direct["a_kiem_soat_b"]
        else:
            owner, owned = ticker_b, ticker_a
            total = direct["b_nam_a_tong_uoc_tinh_pct"]
            controls = direct["b_kiem_soat_a"]
        meaning = (
            f"{owner} NẮM CỔ PHẦN của {owned} — trực tiếp hoặc qua pháp nhân trung gian. "
            f"Tổng quyền lợi kinh tế ước tính {total}%"
            + (", và có ít nhất một chuỗi KIỂM SOÁT (mọi mắt xích trên 50%), nghĩa là "
               "quyền chi phối lớn hơn nhiều so với con số phần trăm ở trên."
               if controls else
               ", nhưng KHÔNG có chuỗi nào nắm đa số ở mọi mắt xích nên đây không phải "
               "quan hệ kiểm soát.")
            + " Đây là quan hệ quan trọng nhất giữa hai doanh nghiệp, phải nêu TRƯỚC "
              "phần cổ đông chung bên dưới."
        )
    elif strong:
        meaning = (f"Hai doanh nghiệp không bên nào nắm bên nào, nhưng có {len(strong)} "
                   "cổ đông nắm từ 20% trở lên ở CẢ HAI — dấu hiệu của quan hệ sở hữu "
                   "thật sự.")
    else:
        meaning = ("Không bên nào nắm cổ phần của bên kia, và không có cổ đông nào nắm từ "
                   "20% trở lên ở cả hai. Những cái tên trùng nhau dưới đây phần lớn là "
                   "quỹ đầu tư có danh mục rộng — chúng KHÔNG cho thấy quan hệ nào giữa "
                   "hai doanh nghiệp.")

    return {
        "status": "ok", "a": ticker_a, "b": ticker_b,
        "quan_he_so_huu_truc_tiep": direct,
        "cung_co_dong": shared, "so_luong": len(shared),
        "co_dong_chi_phoi_ca_hai": strong,
        "y_nghia": meaning,
    }


def co_invested(ticker: str, min_pct: float = 5.0, limit: int = 15) -> Dict[str, Any]:
    """Các doanh nghiệp khác mà cổ đông lớn của doanh nghiệp này cũng nắm.

    `min_pct` lọc theo tỷ lệ ở CẢ HAI phía để loại các quỹ có danh mục rộng. Để mặc định
    5% thì kết quả còn lại gần như toàn cổ đông chi phối — tức là cấu trúc tập đoàn thật,
    không phải trùng danh mục.
    """
    rows = graph().run(
        """
        MATCH (a:Company {ticker: $ticker})-[r1:OWNED_BY]->(o)<-[r2:OWNED_BY]-(b:Company)
        WHERE a <> b AND r1.percent >= $frac AND r2.percent >= $frac
        RETURN coalesce(b.name, b.ticker) AS company, b.ticker AS ticker,
               collect({holder: coalesce(o.name, o.ticker),
                        pct_here: r1.percent, pct_there: r2.percent}) AS via
        ORDER BY size(via) DESC LIMIT $limit
        """,
        ticker=ticker, frac=min_pct / 100.0, limit=limit,
    )
    out = []
    for row in rows:
        via = [{"co_dong": v["holder"],
                "nam_o_day_pct": round(_pct(v["pct_here"]) or 0, 2),
                "nam_o_kia_pct": round(_pct(v["pct_there"]) or 0, 2)}
               for v in row["via"]]
        out.append({"ten": row["company"], "ma": row["ticker"], "qua_co_dong": via})
    return {"status": "ok", "nguong_pct": min_pct, "lien_quan": out, "so_luong": len(out)}


def network(company: str, depth: int = 3,
            min_pct: float = MIN_ECONOMIC_PCT) -> Dict[str, Any]:
    """Toàn cảnh mạng lưới sở hữu quanh một doanh nghiệp. Danh sách bước là CỐ ĐỊNH.

    Cùng nguyên tắc với `brief.collect`: mã lệnh quyết định chạy gì và tính mọi con số,
    LLM chỉ viết lời.
    """
    resolved = _resolve(company)
    if resolved["status"] != "ok":
        return _resolution_failure(company, resolved)

    best = resolved["best"]
    ticker = best["ticker"]
    up = upstream(ticker, depth=depth, min_pct=min_pct)
    down = downstream(ticker, depth=depth, min_pct=min_pct)
    peers = co_invested(ticker)

    # Cùng lý do với `ket_luan` của `chain_between`: đưa sẵn câu kết luận thay vì để mô
    # hình tự tổng hợp từ một danh sách boolean.
    controlled = [c for c in down["truc_tiep"] + down["gian_tiep"]
                  if c["chuoi_nay_kiem_soat"]]
    controllers = [c for c in up["truc_tiep"] + up["gian_tiep"]
                   if c["chuoi_nay_kiem_soat"]]
    verdict = []
    if controllers:
        verdict.append(f"{best['name']} BỊ KIỂM SOÁT bởi: "
                       + ", ".join(dict.fromkeys(c["ten"] for c in controllers)))
    if controlled:
        verdict.append(f"{best['name']} KIỂM SOÁT: "
                       + ", ".join(dict.fromkeys(c["ten"] for c in controlled)))
    if not verdict:
        verdict.append("Không có quan hệ kiểm soát nào (không chuỗi nào nắm đa số ở mọi "
                       "mắt xích) trong phạm vi dữ liệu hiện có.")

    return {
        "status": "ok",
        "ticker": ticker, "company": best["name"],
        "ket_luan_kiem_soat": " · ".join(verdict),
        # ⚠️ TÁCH TÍN HIỆU RA KHỎI NHIỄU BẰNG CẤU TRÚC, KHÔNG BẰNG LỜI NHẮC.
        #
        # Mộc Châu Milk có 14 chuỗi sở hữu, đúng 2 chuỗi là kiểm soát. Đưa cả 14 kèm một
        # câu dặn "đọc kết luận, đừng đọc boolean từng chuỗi" thì mô hình vẫn bám vào
        # chuỗi nó đọc kỹ nhất — đo được: nó lấy chuỗi trực tiếp 8,85% (ks=False) rồi
        # viết "Vinamilk không kiểm soát Mộc Châu", ngược hẳn kết luận nằm ngay trên đó.
        #
        # Hai danh sách riêng dưới đây bỏ hẳn phép lọc mà mô hình phải tự làm. Lời nhắc
        # là thứ mô hình có thể bỏ qua; một danh sách chỉ chứa chuỗi kiểm soát thì không.
        "cac_chuoi_kiem_soat_doanh_nghiep_nay": controllers,
        "cac_chuoi_doanh_nghiep_nay_kiem_soat": controlled,
        "do_sau": depth,
        "ai_nam_doanh_nghiep_nay": up,
        "doanh_nghiep_nay_nam_gi": down,
        "cung_co_dong_chi_phoi": peers,
        "gaps": _gaps(up, down, peers),
        # Đi kèm MỌI lần trả về. Xem chú thích đầu tệp — đây là hiểu nhầm tốn kém nhất
        # mà một bản phân tích sở hữu có thể gây ra.
        "luu_y_bat_buoc": (
            "quyen_loi_kinh_te_pct là TÍCH các tỷ lệ dọc chuỗi — phần lãi thực nhận. Nó "
            "KHÔNG phải mức độ kiểm soát. Quyền kiểm soát nằm ở trường chuoi_nay_kiem_soat "
            "(True khi mọi mắt xích đều trên 50%): A nắm 51% của B, B nắm 51% của C thì "
            "A kiểm soát C hoàn toàn dù quyền lợi kinh tế chỉ 26%."
        ),
    }


def _gaps(up: Dict[str, Any], down: Dict[str, Any], peers: Dict[str, Any]) -> List[str]:
    """Những gì bản phân tích này KHÔNG nhìn thấy — sinh từ chính dữ liệu, không viết tay."""
    out: List[str] = []
    if not up.get("truc_tiep") and not up.get("gian_tiep"):
        out.append("Không có dữ liệu cổ đông cho doanh nghiệp này.")
    if up.get("bo_qua_vi_thieu_ty_le"):
        out.append(f"{up['bo_qua_vi_thieu_ty_le']} chuỗi sở hữu bị bỏ qua vì có mắt xích "
                   "không công bố tỷ lệ — không tính được quyền lợi kinh tế nếu thiếu "
                   "một mắt xích.")
    if not down.get("truc_tiep") and not down.get("gian_tiep"):
        out.append("Doanh nghiệp này không đứng tên cổ đông ở doanh nghiệp nào khác trong "
                   "đồ thị — không dựng được cấu trúc công ty con.")
    if not peers.get("lien_quan"):
        out.append("Không tìm thấy doanh nghiệp nào cùng cổ đông chi phối.")

    # ⚠️ Giới hạn lớn nhất, và nó không sinh từ dữ liệu mà từ chính NGUỒN dữ liệu. Phải
    # nói ra, vì nó quyết định mức tin cậy của toàn bộ phần trên.
    out.append("Chỉ thấy được phần sở hữu ĐƯỢC CÔNG BỐ. Cổ đông dưới ngưỡng phải công bố, "
               "sở hữu qua pháp nhân nước ngoài hoặc qua người đứng tên hộ đều không có "
               "trong dữ liệu — một mạng lưới trông thưa không có nghĩa là nó thưa thật.")
    out.append("Tỷ lệ sở hữu là số tại ngày công bố gần nhất của nguồn, không phải số "
               "thời gian thực.")
    return out
