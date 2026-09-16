"""Kiểm thử tầng số liệu Việt Nam.

Ba nhóm, mỗi nhóm khóa lại một hành vi khác nhau:

  1. NHẬN ĐÚNG      mã Việt Nam không trùng mã Mỹ phải ra đúng doanh nghiệp Việt Nam
  2. BÁO NHẬP NHẰNG mã trùng cả hai sàn thì TUYỆT ĐỐI không được tự chọn một bên
  3. KHÔNG LẪN LỘN  số liệu VND không được trộn vào bảng xếp hạng theo USD

Nhóm 2 là quan trọng nhất. 8/30 mã trong rổ VN30 trùng với mã của SEC và trỏ tới những
doanh nghiệp hoàn toàn khác nhau — ACB là Ngân hàng Á Châu ở Việt Nam nhưng là AURORA
CANNABIS ở Mỹ. Ưu tiên cứng bên nào cũng tái tạo đúng lỗi "trả về số của doanh nghiệp
khác mà không báo gì" đã mất công sửa.

Chạy:  .venv/Scripts/python.exe tests/test_vietnam.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent.tools import (
    company_coverage, compare_financials, lookup_financials, search_filings,
)
from src.ingest.on_demand import resolve_company

# Mã Việt Nam KHÔNG trùng mã SEC -> phải ra thẳng doanh nghiệp Việt Nam
MUST_RESOLVE_VN = ["FPT", "VNM", "HPG", "VIC", "VHM", "MBB", "VJC", "SSI", "STB", "DGC"]

# Mã trùng cả hai sàn -> phải báo nhập nhằng, không được tự chọn
MUST_BE_AMBIGUOUS = [
    ("ACB", "Ngân hàng Á Châu (VN) vs AURORA CANNABIS (Mỹ)"),
    ("MSN", "Tập đoàn Masan (VN) vs EMERSON RADIO (Mỹ)"),
    ("PLX", "Petrolimex (VN) vs Protalix BioTherapeutics (Mỹ)"),
    ("TPB", "TPBank (VN) vs Turning Point Brands (Mỹ)"),
    ("HDB", "HDBank (VN) vs HDFC BANK (Mỹ)"),
    # Hai ca dưới không trùng MÃ mà trùng TIỀN TỐ TÊN — vẫn phải báo nhập nhằng.
    # Chính hai ca này làm lộ ra rằng va chạm không chỉ xảy ra ở mã chứng khoán.
    ("GAS", "PV GAS (VN) vs GAS TRANSPORTER OF THE SOUTH (Mỹ, khớp tiền tố tên)"),
    ("SAB", "Sabeco (VN) vs SAB Biotherapeutics (Mỹ, khớp tiền tố tên)"),
    ("MWG", "Thế Giới Di Động (VN) vs Multi Ways Holdings (Mỹ)"),
]

# Mã Mỹ vẫn phải nguyên vẹn sau khi thêm dữ liệu Việt Nam
MUST_STAY_US = [("NVDA", "NVDA"), ("AAPL", "AAPL"), ("MSFT", "MSFT"), ("TSLA", "TSLA")]


def main() -> int:
    failures = []

    print("=" * 78)
    print("NHÓM 1 — mã Việt Nam riêng biệt, phải nhận đúng")
    print("=" * 78)
    for symbol in MUST_RESOLVE_VN:
        result = resolve_company(symbol)
        if result["status"] != "ok" or not result["best"]["ticker"].endswith(".VN"):
            failures.append(f"{symbol!r} lẽ ra là doanh nghiệp Việt Nam, nhận được {result}")
            print(f"  SAI   {symbol:6} -> {result.get('status')}")
            continue
        data = lookup_financials(company=symbol, metrics=["revenue"])
        year = (data.get("years") or [{}])[0]
        revenue = year.get("revenue")
        if data["status"] != "ok" or not revenue:
            failures.append(f"{symbol!r} phân giải được nhưng không có số liệu")
            print(f"  SAI   {symbol:6} -> {data['status']}, thiếu doanh thu")
            continue
        print(f"  đúng  {symbol:6} -> {result['best']['ticker']:9} "
              f"FY{year.get('fiscal_year')} {revenue / 1e12:8,.1f} nghìn tỷ VND")

    print()
    print("=" * 78)
    print("NHÓM 1b — TÊN THƯƠNG HIỆU và tên có dấu, thứ người dùng thật sự gõ")
    print("=" * 78)
    # Tên đầy đủ của Vinamilk là "Vietnam Dairy Products Joint Stock Company" — không
    # chứa chữ "Vinamilk" nào. Trước khi lưu thêm các dạng tên ngắn, cả bốn ca đầu đều
    # trả về not_found, còn "Sabeco" thì tệ hơn: nó khớp tiền tố với SABECO SONGTIEN
    # (SST.VN, một công ty UPCOM nhỏ) và trả về status ok kèm số liệu của doanh nghiệp
    # hoàn toàn khác — đúng lỗi Acer→Macerich, tái sinh trong vũ trụ Việt Nam.
    BRANDS = [
        ("Vinamilk", "VNM.VN"),
        ("Vietcombank", "VCB.VN"),
        ("Techcombank", "TCB.VN"),
        ("Sabeco", "SAB.VN"),
        ("VPBank", "VPB.VN"),
        # Có dấu: `_simplify` cũ biến "Hòa Phát" thành 'h a ph t', một chuỗi rác
        ("Hòa Phát", "HPG.VN"),
        ("Thế giới di động", "MWG.VN"),
        ("Tập đoàn Masan", "MSN.VN"),
    ]
    for query, want in BRANDS:
        result = resolve_company(query)
        got = result.get("best", {}).get("ticker") if result["status"] == "ok" else None
        if got == want:
            print(f"  đúng  {query:<18} -> {want}")
        else:
            failures.append(f"{query!r} lẽ ra là {want}, nhận được {got} ({result['status']})")
            print(f"  SAI   {query:<18} -> {result['status']} {got}")

    print()
    print("=" * 78)
    print("NHÓM 2 — mã trùng hai sàn, phải BÁO NHẬP NHẰNG chứ không tự chọn")
    print("=" * 78)
    for symbol, why in MUST_BE_AMBIGUOUS:
        result = resolve_company(symbol)
        if result["status"] != "ambiguous":
            got = result.get("best", {}).get("name", result.get("status"))
            failures.append(f"{symbol!r} lẽ ra phải báo nhập nhằng, nhưng tự chọn {got}")
            print(f"  SAI   {symbol:6} -> tự chọn {str(got)[:44]}")
            continue
        tool = lookup_financials(company=symbol)
        if tool["status"] != "ambiguous":
            failures.append(f"{symbol!r} công cụ nuốt mất thông tin nhập nhằng")
            print(f"  SAI   {symbol:6} -> công cụ trả về {tool['status']}")
            continue
        names = " | ".join(o["company"][:26] for o in tool["options"])
        print(f"  đúng  {symbol:6} -> nhập nhằng: {names}")
        print(f"        └ {why}")

    print()
    print("=" * 78)
    print("NHÓM 3 — dữ liệu Mỹ không bị ảnh hưởng")
    print("=" * 78)
    for query, want in MUST_STAY_US:
        result = resolve_company(query)
        got = result.get("best", {}).get("ticker") if result["status"] == "ok" else None
        if got != want:
            failures.append(f"{query!r} lẽ ra là {want}, nhận được {got}")
            print(f"  SAI   {query:6} -> {got}")
        else:
            print(f"  đúng  {query:6} -> {want}")

    print()
    print("=" * 78)
    print("NHÓM 3b — phải có cách thoát khỏi nhập nhằng theo CẢ HAI chiều")
    print("=" * 78)
    # Hậu tố `.VN` đã có từ đầu. `.US` là chiều còn thiếu, và thiếu nó thì nhập nhằng
    # thành ngõ cụt: agent hỏi "ý bạn là bên nào", người dùng đáp "bên Mỹ", agent không
    # có cách nào diễn đạt lại nên hỏi vòng vo mãi. Ở mức 30 mã thì hiếm; ở mức 1.586 mã
    # Việt Nam thì có 302 mã trùng doanh nghiệp Mỹ đang có dữ liệu.
    for symbol, _ in MUST_BE_AMBIGUOUS:
        us = resolve_company(f"{symbol}.US")
        vn = resolve_company(f"{symbol}.VN")
        ok_us = us["status"] == "ok" and not (us["best"].get("ticker") or "").endswith(".VN")
        ok_vn = vn["status"] == "ok" and (vn["best"].get("ticker") or "").endswith(".VN")
        if ok_us and ok_vn:
            print(f"  đúng  {symbol:6} .US -> {us['best']['name'][:32]:<34} "
                  f".VN -> {vn['best']['name'][:26]}")
        else:
            if not ok_us:
                failures.append(f"{symbol}.US lẽ ra ra doanh nghiệp Mỹ, nhận được {us}")
            if not ok_vn:
                failures.append(f"{symbol}.VN lẽ ra ra doanh nghiệp Việt Nam, nhận được {vn}")
            print(f"  SAI   {symbol:6} .US={us.get('status')} .VN={vn.get('status')}")

    print()
    print("=" * 78)
    print("NHÓM 4 — so sánh khác đồng tiền phải bị chặn")
    print("=" * 78)
    mixed = compare_financials(companies=["FPT", "Apple"], metric="revenue", year=2024)
    currencies = mixed.get("currencies") or []
    if mixed.get("mixed_currency_warning") and len(currencies) > 1:
        print(f"  đúng  FPT (VND) + Apple (USD) -> có cảnh báo, đồng tiền: {currencies}")
    else:
        failures.append("So sánh VND với USD mà KHÔNG có cảnh báo trộn đồng tiền")
        print(f"  SAI   thiếu cảnh báo, đồng tiền: {currencies}")

    print()
    print("=" * 78)
    print("NHÓM 5 — mức phủ phải nói đúng TỪNG tầng, không gộp làm một")
    print("=" * 78)
    # Sau khi nạp cổ đông, FPT từng hiện tier='graph' kèm text_chunks=0 — thang bậc hứa
    # "có đồ thị thì có văn bản", còn dữ liệu nói ngược lại. Giờ `tier` chỉ đo hồ sơ SEC,
    # và quan hệ sở hữu được đếm riêng.
    fpt = company_coverage(company="FPT")
    ok_fpt = (fpt.get("tier") == "metrics" and fpt.get("ownership_relations", 0) > 0
              and fpt.get("knowledge_relations") == 0 and fpt.get("text_chunks") == 0)
    print(f"  {'đúng' if ok_fpt else 'SAI '}  FPT  tier={fpt.get('tier')} · sở hữu={fpt.get('ownership_relations')}"
          f" · từ hồ sơ={fpt.get('knowledge_relations')} · 10-K={fpt.get('text_chunks')}")
    if not ok_fpt:
        failures.append(f"FPT lẽ ra tier=metrics, có quan hệ sở hữu, không có quan hệ từ hồ sơ: {fpt}")
    nvda = company_coverage(company="NVDA")
    ok_nvda = (nvda.get("tier") == "graph" and nvda.get("knowledge_relations", 0) > 0
               and nvda.get("text_chunks", 0) > 0)
    print(f"  {'đúng' if ok_nvda else 'SAI '}  NVDA tier={nvda.get('tier')} · từ hồ sơ={nvda.get('knowledge_relations')}"
          f" · 10-K={nvda.get('text_chunks')}")
    if not ok_nvda:
        failures.append(f"NVDA lẽ ra tier=graph, có quan hệ từ hồ sơ và văn bản 10-K: {nvda}")

    print()
    print("=" * 78)
    print("NHÓM 6 — mô tả doanh nghiệp Việt Nam: có khi được hỏi, VẮNG khi không")
    print("=" * 78)
    fpt_text = search_filings(query="What does the company do?", companies=["FPT"])
    items = {r.get("item") for r in fpt_text.get("results", [])}
    ok_any = (fpt_text.get("status") == "ok" and items
              and items <= {"AR", "PROFILE"} and fpt_text.get("vietnam_note"))
    print(f"  {'đúng' if ok_any else 'SAI '}  hỏi FPT làm gì -> {fpt_text.get('status')}, "
          f"mục {sorted(items)}")
    if not ok_any:
        failures.append("FPT lẽ ra trả về mục AR/PROFILE kèm vietnam_note "
                        f"(đã chạy scripts/14 và 15 chưa?): {str(fpt_text)[:200]}")

    # Câu hỏi RỦI RO chỉ trả lời được bằng báo cáo thường niên. Đoạn mô tả của VCI
    # không nói gì về rủi ro, nên nếu kết quả chỉ có PROFILE thì agent sẽ hoặc bịa,
    # hoặc nói không có — cả hai đều sai khi hệ thống ĐANG CÓ báo cáo.
    fpt_risk = search_filings(query="business risks and risk management", companies=["FPT"])
    ar = [r for r in fpt_risk.get("results", []) if r.get("item") == "AR"]
    ok_ar = fpt_risk.get("status") == "ok" and ar
    print(f"  {'đúng' if ok_ar else 'SAI '}  hỏi rủi ro FPT -> {len(ar)} đoạn báo cáo thường niên"
          + (f", vd {ar[0]['item_title']}" if ar else ""))
    if not ok_ar:
        failures.append("Hỏi rủi ro FPT lẽ ra phải ra đoạn báo cáo thường niên (mục AR); "
                        f"nhận được {sorted({r.get('item') for r in fpt_risk.get('results', [])})}")

    # Tìm không lọc công ty KHÔNG được lẫn mô tả Việt Nam — đó là lý do chúng nằm ở
    # collection riêng. Đoạn mô tả ngắn và chung chung dễ vượt mặt đoạn 10-K dài.
    open_q = search_filings(query="investment in AI infrastructure and data centers")
    leaked = [r["ticker"] for r in open_q.get("results", []) if str(r.get("ticker", "")).endswith(".VN")]
    print(f"  {'đúng' if not leaked else 'SAI '}  tìm không lọc -> {len(open_q.get('results', []))} kết quả, "
          f"{len(leaked)} của Việt Nam")
    if leaked:
        failures.append(f"Mô tả Việt Nam lọt vào tìm kiếm không lọc: {leaked}")
    print()
    print("=" * 78)
    print("NHÓM 7 — mã trùng hai sàn KHÔNG được báo là 'không có dữ liệu'")
    print("=" * 78)
    # ⚠️ NHẬP NHẰNG LÀ BIẾT MÀ CHƯA CHỌN ĐƯỢC; KHÔNG-CÓ-DỮ-LIỆU LÀ KHÔNG BIẾT.
    #
    # Đo thật trước khi sửa: hỏi "SAB (Sabeco) nêu những rủi ro chính nào?" thì agent
    # trả lời "Hệ thống không có dữ liệu về doanh nghiệp SAB (Sabeco)" — trong khi báo
    # cáo thường niên 2020 của Sabeco nằm sẵn trong kho. Nguyên nhân: search_filings
    # gộp `ambiguous` vào `unresolved`, rồi phần hint bảo thẳng agent nói là không có.
    #
    # Lỗi này tệ hơn tìm trượt vì nó KHẲNG ĐỊNH dữ liệu không tồn tại — người đọc không
    # có lý do nghi ngờ để hỏi lại bằng cách viết khác. Ảnh hưởng 374/1.532 mã Việt Nam
    # trùng mã với doanh nghiệp Mỹ, trong đó có SAB, ACB, MSN, PLX, TPB.
    for bare in ("SAB", "ACB", "MSN", "PLX", "TPB"):
        r = search_filings(query="rủi ro và chiến lược", companies=[bare], top_k=2)
        good = r.get("status") in ("company_ambiguous", "ok")
        print(f"  {'đúng' if good else 'SAI '}  mã trần {bare:<4} -> {r.get('status')}")
        if not good:
            failures.append(f"Mã trần {bare} lẽ ra phải báo nhập nhằng, nhận được "
                            f"{r.get('status')}")

    # Đối chứng: tên không có thật VẪN phải bị từ chối. Nếu ca này hỏng thì bản sửa đã
    # nới quá tay và biến mọi thứ thành 'có thể có' — mất luôn lớp chặn khớp sai.
    junk = search_filings(query="rủi ro", companies=["cong ty khong co that xyz"], top_k=2)
    ok_junk = junk.get("status") == "company_not_found"
    print(f"  {'đúng' if ok_junk else 'SAI '}  tên bịa -> {junk.get('status')}")
    if not ok_junk:
        failures.append(f"Tên bịa lẽ ra phải là company_not_found, nhận được {junk.get('status')}")

    print()
    print("=" * 78)
    total = len(MUST_RESOLVE_VN) + 8 + len(MUST_BE_AMBIGUOUS) * 2 + len(MUST_STAY_US) + 1 + 5 + 6
    if failures:
        print(f"THẤT BẠI: {len(failures)}/{total} ca sai")
        for item in failures:
            print(f"  · {item}")
        return 1
    print(f"TẤT CẢ {total} CA ĐỀU ĐÚNG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
