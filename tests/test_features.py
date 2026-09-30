"""Kiểm ba chức năng vượt ra ngoài hỏi–đáp: theo dõi, mạng lưới sở hữu, so sánh ngành.

VÌ SAO PHẦN LỚN CA Ở ĐÂY LÀ HÀM THUẦN

Ba chức năng này đều có một điểm chung nguy hiểm: kết quả của chúng TRÔNG HỢP LÝ kể cả
khi sai. Một bảng xếp hạng vẫn là một bảng xếp hạng dù mẫu số sai; một chuỗi sở hữu vẫn
đọc trôi chảy dù mũi tên ngược chiều; một cảnh báo "không có thay đổi" vẫn là một câu
tiếng Việt đúng ngữ pháp dù hệ thống chưa hề nhìn.

Không có gì trong ba thứ đó tự báo lỗi. Nên phép kiểm phải nhắm thẳng vào phần TÍNH TOÁN,
và phần tính toán đã được tách khỏi phần đọc cơ sở dữ liệu đúng để kiểm được mà không cần
Neo4j chạy: `watch.compare`, `ownership._chain_from`, `peers._rank`, `peers._ratios`.

Nhóm 7 cần dịch vụ chạy thật; nó tự bỏ qua khi không kết nối được, và nói rõ là đã bỏ qua
chứ không lặng lẽ báo đúng.

Chạy:  .venv/Scripts/python.exe tests/test_features.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent import ownership, peers, watch

failures = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  đúng  {name}")
    else:
        failures.append(f"{name}{' — ' + detail if detail else ''}")
        print(f"  SAI   {name}")
        if detail:
            print(f"        └ {detail}")


# ======================================================================================
# NHÓM 1 — Theo dõi: phát hiện đúng loại thay đổi
# ======================================================================================

def group_1() -> None:
    print("\n" + "=" * 78)
    print("NHÓM 1 — Theo dõi phát hiện đúng loại thay đổi")
    print("=" * 78)

    base = {
        "financials": {"2023": {"revenue": 1000.0, "net_income": 100.0}},
        "ownership": {"Ông A": {"percent": 0.20, "as_of": "2024-01-01"}},
        "text_chunks": 100,
    }

    # Năm mới xuất hiện
    after = {**base, "financials": {**base["financials"],
                                    "2024": {"revenue": 1200.0, "net_income": 150.0}}}
    kinds = [c["kind"] for c in watch.compare(base, after)]
    check("năm tài chính mới sinh cảnh báo nam_moi", "nam_moi" in kinds, str(kinds))

    # ⚠️ Ca quan trọng nhất: số CŨ bị sửa. Đây là loại thay đổi mà không thứ gì khác
    # trong hệ thống báo ra, và nó làm sai mọi câu trả lời đã đưa trước đó.
    revised = {**base, "financials": {"2023": {"revenue": 1100.0, "net_income": 100.0}}}
    changes = watch.compare(base, revised)
    revision = [c for c in changes if c["kind"] == "so_cu_bi_sua"]
    check("số liệu năm cũ bị sửa sinh cảnh báo so_cu_bi_sua", bool(revision),
          str([c["kind"] for c in changes]))
    check("cảnh báo số cũ bị sửa xếp mức CAO",
          bool(revision) and revision[0]["severity"] == "cao")

    # Sai số làm tròn KHÔNG được coi là sửa số — nếu không thì mỗi lần kiểm tra lại nổ
    # một tràng cảnh báo giả và người dùng sẽ tắt tính năng này đi.
    noise = {**base, "financials": {"2023": {"revenue": 1000.0000001, "net_income": 100.0}}}
    check("sai số làm tròn KHÔNG sinh cảnh báo",
          not [c for c in watch.compare(base, noise) if c["kind"] == "so_cu_bi_sua"])

    # Tỷ lệ sở hữu: phải quy đổi phân số sang điểm phần trăm
    moved = {**base, "ownership": {"Ông A": {"percent": 0.28, "as_of": "2024-06-01"}}}
    changes = watch.compare(base, moved)
    pct_change = [c for c in changes if c["kind"] == "ty_le_so_huu_doi"]
    check("tỷ lệ sở hữu đổi 8 điểm phần trăm được phát hiện", bool(pct_change),
          str([c["kind"] for c in changes]))
    check("cảnh báo ghi ĐIỂM PHẦN TRĂM chứ không ghi phân số",
          bool(pct_change) and "20.00% → 28.00%" in pct_change[0]["detail"],
          pct_change[0]["detail"] if pct_change else "")

    # Dao động nhỏ dưới ngưỡng thì im lặng
    tiny = {**base, "ownership": {"Ông A": {"percent": 0.2001, "as_of": "2024-06-01"}}}
    check("dao động 0,01 điểm phần trăm KHÔNG sinh cảnh báo",
          not [c for c in watch.compare(base, tiny) if c["kind"] == "ty_le_so_huu_doi"])

    # Cổ đông đến và đi
    swapped = {**base, "ownership": {"Bà B": {"percent": 0.30, "as_of": "2024-06-01"}}}
    kinds = [c["kind"] for c in watch.compare(base, swapped)]
    check("cổ đông mới được phát hiện", "co_dong_moi" in kinds, str(kinds))
    check("cổ đông biến mất được phát hiện", "co_dong_bien_mat" in kinds, str(kinds))

    # Văn bản GIẢM là bất thường, phải ở mức cao
    lost = {**base, "text_chunks": 40}
    changes = watch.compare(base, lost)
    check("văn bản giảm xếp mức CAO (nghi nạp hỏng giữa chừng)",
          bool(changes) and changes[0]["kind"] == "van_ban_mat"
          and changes[0]["severity"] == "cao", str(changes))

    # Không đổi gì thì tuyệt đối không được sinh cảnh báo nào
    check("hai ảnh chụp giống hệt nhau KHÔNG sinh cảnh báo nào",
          watch.compare(base, dict(base)) == [], str(watch.compare(base, dict(base))))


# ======================================================================================
# NHÓM 2 — Mạng lưới sở hữu: quyền lợi kinh tế KHÁC quyền kiểm soát
# ======================================================================================

def group_2() -> None:
    print("\n" + "=" * 78)
    print("NHÓM 2 — Quyền lợi kinh tế khác quyền kiểm soát")
    print("=" * 78)

    # Ví dụ kinh điển ở đầu `ownership.py`: 51% × 51%.
    chain = ownership._chain_from(["C", "B", "A"], [0.51, 0.51])
    check("51% × 51% cho quyền lợi kinh tế 26,01%",
          chain and abs(chain["quyen_loi_kinh_te_pct"] - 26.01) < 0.01,
          str(chain))
    check("51% × 51% VẪN là chuỗi kiểm soát (mọi mắt xích trên 50%)",
          chain and chain["chuoi_nay_kiem_soat"] is True)

    # Chiều ngược lại: tỷ lệ cộng dồn lớn nhưng một mắt xích yếu -> KHÔNG kiểm soát.
    weak = ownership._chain_from(["C", "B", "A"], [0.95, 0.40])
    check("38% qua một mắt xích 40% KHÔNG phải chuỗi kiểm soát",
          weak and weak["chuoi_nay_kiem_soat"] is False, str(weak))
    check("mắt xích yếu nhất được nêu đúng",
          weak and abs(weak["mat_xich_yeu_nhat_pct"] - 40.0) < 0.01)

    # Đúng 50% KHÔNG phải đa số — ngưỡng là NGHIÊM NGẶT lớn hơn.
    half = ownership._chain_from(["B", "A"], [0.50])
    check("nắm đúng 50% KHÔNG được coi là kiểm soát",
          half and half["chuoi_nay_kiem_soat"] is False, str(half))

    # ⚠️ Thiếu tỷ lệ ở một mắt xích -> trả None, KHÔNG đoán. Tích của một dãy có lỗ hổng
    # không phải ước lượng dè dặt, nó là một con số bịa.
    check("chuỗi thiếu tỷ lệ ở một mắt xích trả None chứ không đoán",
          ownership._chain_from(["C", "B", "A"], [0.51, None]) is None)
    check("chuỗi có tỷ lệ rỗng trả None",
          ownership._chain_from(["C", "B", "A"], [0.51, ""]) is None)

    # Một tầng: quyền lợi kinh tế bằng đúng tỷ lệ, không nhân thêm gì.
    direct = ownership._chain_from(["B", "A"], [0.4654])
    check("chuỗi một tầng giữ nguyên tỷ lệ 46,54%",
          direct and abs(direct["quyen_loi_kinh_te_pct"] - 46.54) < 0.01, str(direct))

    # Lọc chuỗi nhỏ phải ĐẾM được, không lọc im lặng.
    rows = [
        {"names": ["C", "B"], "percents": [0.60], "lb": ["Company"], "n": "B", "t": "B"},
        {"names": ["C", "X"], "percents": [0.001], "lb": ["Person"], "n": "X", "t": None},
        {"names": ["C", "Y"], "percents": [None], "lb": ["Person"], "n": "Y", "t": None},
    ]
    out = ownership._assemble(rows, "n", "t", "lb", min_pct=0.5, limit=10)
    check("chuỗi quá nhỏ bị loại nhưng được ĐẾM", out["bo_qua_vi_nho"] == 1, str(out))
    check("chuỗi thiếu tỷ lệ bị loại nhưng được ĐẾM",
          out["bo_qua_vi_thieu_ty_le"] == 1, str(out))
    check("chỉ còn lại chuỗi hợp lệ", out["tong_chuoi"] == 1)
    check("loại chủ sở hữu được nhận đúng là doanh nghiệp",
          out["truc_tiep"][0]["loai"] == "doanh nghiệp")

    # Chuỗi kiểm soát phải đứng trước chuỗi tỷ lệ cao hơn nhưng không kiểm soát.
    rows = [
        {"names": ["C", "A"], "percents": [0.45], "lb": ["Company"], "n": "A", "t": "A"},
        {"names": ["C", "B"], "percents": [0.55], "lb": ["Company"], "n": "B", "t": "B"},
    ]
    out = ownership._assemble(rows, "n", "t", "lb", min_pct=0.5, limit=10)
    check("chuỗi kiểm soát xếp trước chuỗi tỷ lệ thấp hơn ngưỡng",
          out["truc_tiep"][0]["ten"] == "B", str([c["ten"] for c in out["truc_tiep"]]))


# ======================================================================================
# NHÓM 3 — So sánh ngành: mẫu số, đồng tiền, chỉ tiêu thiếu
# ======================================================================================

def group_3() -> None:
    print("\n" + "=" * 78)
    print("NHÓM 3 — So sánh ngành: mẫu số, đồng tiền, chỉ tiêu thiếu")
    print("=" * 78)

    # Tỷ lệ: mẫu số bằng 0 hoặc thiếu -> None, KHÔNG phải 0. Một biên lợi nhuận 0% và
    # một biên lợi nhuận không tính được là hai điều khác hẳn nhau.
    ratios = peers._ratios({"revenue": 0, "net_income": 50})
    check("doanh thu bằng 0 cho biên lợi nhuận None chứ không phải 0",
          ratios["bien_loi_nhuan_rong"] is None, str(ratios))
    ratios = peers._ratios({"revenue": 1000, "net_income": 150, "gross_profit": 400,
                            "stockholders_equity": 500, "total_assets": 2000})
    check("biên lợi nhuận ròng tính đúng 15%",
          abs(ratios["bien_loi_nhuan_rong"] - 15.0) < 1e-9, str(ratios))
    check("ROE tính đúng 30%", abs(ratios["roe"] - 30.0) < 1e-9)
    check("ROA tính đúng 7,5%", abs(ratios["roa"] - 7.5) < 1e-9)

    # Xếp hạng: mẫu số PHẢI tính cả chính doanh nghiệp đang xét.
    rank = peers._rank(50.0, [10.0, 20.0, 30.0, 40.0])
    check("hạng 1 khi giá trị cao nhất", rank and rank["hang"] == 1, str(rank))
    check("mẫu số tính cả chính doanh nghiệp (5 chứ không phải 4)",
          rank and rank["so_doanh_nghiep_co_so_lieu"] == 5, str(rank))
    check("mẫu số được đánh dấu là đã gồm chính nó",
          rank and rank["bao_gom_chinh_no"] is True)
    check("phân vị 100 khi đứng đầu", rank and rank["phan_vi"] == 100.0)

    rank = peers._rank(5.0, [10.0, 20.0, 30.0, 40.0])
    check("hạng cuối khi giá trị thấp nhất", rank and rank["hang"] == 5, str(rank))
    check("phân vị 0 khi đứng cuối", rank and rank["phan_vi"] == 0.0)

    # ⚠️ Quá ít doanh nghiệp thì KHÔNG xếp hạng. "Đứng thứ 2 trong 3" là câu vô dụng
    # đội lốt một con số.
    check("dưới ngưỡng tối thiểu thì không xếp hạng",
          peers._rank(10.0, [5.0, 8.0]) is None)
    check("doanh nghiệp không có chỉ tiêu thì không xếp hạng",
          peers._rank(None, [1.0, 2.0, 3.0, 4.0]) is None)

    # ⚠️ Đồng tiền: lọc và ĐẾM. So VND với USD trong một bảng cho thứ tự hoàn toàn bịa.
    subject = {"currency": "VND"}
    pool = {"A": {"currency": "VND"}, "B": {"currency": "USD"}, "C": {"currency": "VND"},
            "D": {"currency": "EUR"}}
    kept, dropped = peers._same_currency(subject, pool)
    check("chỉ giữ doanh nghiệp cùng đồng tiền", set(kept) == {"A", "C"}, str(kept))
    check("số doanh nghiệp khác đồng tiền được ĐẾM chứ không lọc im lặng", dropped == 2)

    # Không biết đồng tiền của chính mình thì đừng lọc — lọc sẽ bỏ nhầm cả nhóm.
    kept, dropped = peers._same_currency({"currency": None}, pool)
    check("không rõ đồng tiền thì giữ nguyên nhóm thay vì lọc bừa",
          len(kept) == 4 and dropped == 0)

    # Mục "điều không nói được" phải nêu tên chỉ tiêu không xếp hạng được.
    gaps = peers._gaps({}, {"revenue": {}}, 0, {"ly_do": "x"})
    check("chỉ tiêu không xếp hạng được phải được nêu tên trong phần thiếu sót",
          any("Biên lợi nhuận gộp" in g for g in gaps), str(gaps[:1]))
    gaps = peers._gaps({}, {"revenue": {}}, 3, {"ly_do": "x"})
    check("số doanh nghiệp bị loại vì khác đồng tiền phải được nói ra",
          any("khác" in g and "3" in g for g in gaps))


# ======================================================================================
# NHÓM 4 — Chạy thật, cần Neo4j. Tự bỏ qua khi không có dịch vụ.
# ======================================================================================

def group_4() -> None:
    print("\n" + "=" * 78)
    print("NHÓM 4 — Chạy thật trên dữ liệu (cần Neo4j)")
    print("=" * 78)

    try:
        net = ownership.network("Vinamilk", depth=3)
    except Exception as exc:  # noqa: BLE001
        # Nói rõ đã BỎ QUA, không lặng lẽ tính là đúng.
        print(f"  BỎ QUA — không kết nối được cơ sở dữ liệu: {str(exc)[:80]}")
        return

    check("dựng được mạng lưới sở hữu Vinamilk", net.get("status") == "ok",
          str(net.get("status")))
    if net.get("status") != "ok":
        return

    check("kết quả luôn mang theo lời nhắc kinh tế ≠ kiểm soát",
          "KHÔNG phải mức độ kiểm soát" in net.get("luu_y_bat_buoc", ""))

    # Chuỗi Vinamilk -> Vilico (68,94%) -> Mocchau (59,3%): kiểm soát dù chỉ 40,88%
    # quyền lợi kinh tế. Đây là ca thật, không phải ví dụ dựng.
    chains = net["doanh_nghiep_nay_nam_gi"]["gian_tiep"]
    controlling = [c for c in chains if c["chuoi_nay_kiem_soat"] and c["so_tang"] == 2]
    check("tìm được chuỗi kiểm soát hai tầng có thật", bool(controlling),
          str([(c["tung_tang_pct"], c["chuoi_nay_kiem_soat"]) for c in chains]))
    if controlling:
        c = controlling[0]
        check("chuỗi kiểm soát đó có quyền lợi kinh tế DƯỚI 50%",
              c["quyen_loi_kinh_te_pct"] < 50.0,
              f"{c['quyen_loi_kinh_te_pct']}% — nếu ≥50% thì ca kiểm thử này không còn "
              f"chứng minh được điều nó định chứng minh")

    # So sánh ngành trên ngân hàng: phải TỰ BỎ biên lợi nhuận gộp và nói rõ vì sao.
    bank = peers.benchmark("ACB.VN")
    check("so sánh được ngân hàng ACB", bank.get("status") == "ok", str(bank.get("status")))
    if bank.get("status") == "ok":
        check("ngân hàng KHÔNG bị xếp hạng biên lợi nhuận gộp",
              "bien_loi_nhuan_gop" not in bank["xep_hang"],
              str(list(bank["xep_hang"])))
        check("lý do bỏ chỉ tiêu được nói ra trong phần thiếu sót",
              any("Biên lợi nhuận gộp" in g for g in bank["gaps"]))
        check("mỗi chỉ tiêu mang mẫu số riêng của nó",
              all("so_doanh_nghiep_co_so_lieu" in v for v in bank["xep_hang"].values()))

    # Tên mơ hồ phải hỏi lại, KHÔNG tự chọn — luật cũ của cả hệ thống, áp cho chức năng mới.
    ambiguous = peers.benchmark("ACB")
    check("mã trùng giữa hai sàn vẫn phải hỏi lại thay vì tự chọn",
          ambiguous.get("status") == "ambiguous", str(ambiguous.get("status")))

    # Không đủ doanh nghiệp cùng loại là một KẾT QUẢ có lý do, không phải lỗi im lặng.
    thin = peers.benchmark("Tesla")
    check("thiếu nhóm so sánh thì nói rõ lý do thay vì dựng bảng giả",
          thin.get("status") == "khong_du_doanh_nghiep_cung_loai"
          and bool(thin.get("canh_bao")), str(thin.get("status")))


# ======================================================================================
# NHÓM 5 — Hồi quy: kết luận kiểm soát phải nằm SẴN trong dữ liệu, không để mô hình suy
# ======================================================================================

def group_5() -> None:
    """Ca hồi quy cho một lỗi đo được, không phải lỗi giả định.

    Hỏi "Vinamilk kiểm soát Mộc Châu Milk ở mức nào", agent trả lời "Vinamilk không kiểm
    soát" — vì nó đọc `chuoi_nay_kiem_soat: false` của chuỗi trực tiếp 8,85% trong số 14
    chuỗi, bỏ qua chuỗi qua Vilico có `true`. Sự thật ngược lại: Vinamilk nắm 68,94% của
    Vilico, Vilico nắm 59,3% của Mộc Châu, cả hai đều trên 50% nên đây là kiểm soát.

    Bài học đã rút: lời nhắc trong prompt là thứ mô hình CÓ THỂ bỏ qua; một trường dữ
    liệu chỉ chứa kết luận thì không. Nên phép kiểm ở đây nhắm vào DỮ LIỆU trả về, không
    nhắm vào câu chữ của mô hình.
    """
    print("\n" + "=" * 78)
    print("NHÓM 5 — Kết luận kiểm soát nằm sẵn trong dữ liệu (hồi quy)")
    print("=" * 78)

    try:
        net = ownership.network("MCM.VN")
        pair = ownership.common_holders("VNM.VN", "MCM.VN")
    except Exception as exc:  # noqa: BLE001
        print(f"  BỎ QUA — không kết nối được cơ sở dữ liệu: {str(exc)[:80]}")
        return

    check("kết quả mạng lưới có sẵn câu kết luận về kiểm soát",
          bool(net.get("ket_luan_kiem_soat")), str(net.get("ket_luan_kiem_soat")))
    check("kết luận nêu đích danh Vinamilk là bên kiểm soát Mộc Châu",
          "Vietnam Dairy Products" in net.get("ket_luan_kiem_soat", ""),
          net.get("ket_luan_kiem_soat", ""))

    only_control = net.get("cac_chuoi_kiem_soat_doanh_nghiep_nay") or []
    check("danh sách chuỗi kiểm soát được tách riêng khỏi phần còn lại",
          bool(only_control), str(only_control))
    check("mọi chuỗi trong danh sách ấy đều thật sự là chuỗi kiểm soát",
          all(c["chuoi_nay_kiem_soat"] for c in only_control))
    check("danh sách tách riêng NHỎ HƠN hẳn tổng số chuỗi (tách được nhiễu)",
          len(only_control) < net["ai_nam_doanh_nghiep_nay"]["tong_chuoi"],
          f"{len(only_control)} / {net['ai_nam_doanh_nghiep_nay']['tong_chuoi']}")

    # Hỏi về HAI doanh nghiệp phải trả lời "bên nào nắm bên nào" TRƯỚC, không chỉ trả lời
    # "hai bên có cổ đông chung không" — đó là câu hỏi khác hẳn.
    direct = pair.get("quan_he_so_huu_truc_tiep") or {}
    check("so hai doanh nghiệp có tìm chuỗi sở hữu giữa hai bên", bool(direct.get("a_nam_b")),
          str(list(direct)))
    check("kết luận cho cặp doanh nghiệp khẳng định có kiểm soát",
          direct.get("a_kiem_soat_b") is True and "KIỂM SOÁT" in direct.get("ket_luan", ""),
          direct.get("ket_luan", ""))
    check("tổng quyền lợi kinh tế cộng cả đường trực tiếp lẫn gián tiếp",
          direct.get("a_nam_b_tong_uoc_tinh_pct", 0) > 45.0,
          str(direct.get("a_nam_b_tong_uoc_tinh_pct")))
    check("tổng ước tính đi kèm lời nói rõ nó là ước tính",
          "ước tính" in direct.get("luu_y_tong", ""))
    check("chiều ngược lại đúng là rỗng (Mộc Châu không nắm Vinamilk)",
          not direct.get("b_nam_a"), str(direct.get("b_nam_a")))


def main() -> int:
    group_1()
    group_2()
    group_3()
    group_4()
    group_5()

    print("\n" + "=" * 78)
    if failures:
        print(f"THẤT BẠI: {len(failures)} ca sai")
        for item in failures:
            print(f"  · {item}")
        return 1
    print("TẤT CẢ CÁC CA ĐỀU ĐÚNG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
