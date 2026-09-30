"""Bước 22: So sánh một doanh nghiệp với nhóm cùng ngành / cùng đối thủ.

    .venv/Scripts/python.exe scripts/22_peers.py FPT
    .venv/Scripts/python.exe scripts/22_peers.py "Hòa Phát" --year 2024
    .venv/Scripts/python.exe scripts/22_peers.py NVDA --out so-sanh.md

⚠️ Đọc cột "trên bao nhiêu". Mẫu số đổi theo từng chỉ tiêu vì không phải doanh nghiệp
nào cũng có đủ chỉ tiêu — ngân hàng không có lợi nhuận gộp. Xem chú thích đầu
`src/agent/peers.py`.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent import peers as peers_mod
from src.obs import logs

console = Console()


def _money(value: Any, currency: str) -> str:
    """Rút gọn số tiền, chọn bậc theo ĐỘ LỚN chứ không cố định một bậc.

    Cố định bậc 'nghìn tỷ' làm trung vị của một ngành toàn doanh nghiệp nhỏ hiện ra là
    '0,00 nghìn tỷ' — trông như dữ liệu trống trong khi con số hoàn toàn có thật. Một ô
    bảng đọc ra số không khi giá trị khác không là thứ người đọc sẽ tin, nên phải tránh.
    """
    if value is None:
        return "—"
    if currency == "VND":
        if abs(value) >= 1e12:
            return f"{value / 1e12:,.2f} nghìn tỷ"
        if abs(value) >= 1e9:
            return f"{value / 1e9:,.1f} tỷ"
        return f"{value / 1e6:,.0f} triệu"
    unit = currency or ""
    if abs(value) >= 1e9:
        return f"{value / 1e9:,.2f} tỷ {unit}".strip()
    return f"{value / 1e6:,.1f} triệu {unit}".strip()


def render(result: Dict[str, Any]) -> str:
    if result.get("status") == "ambiguous":
        opts = "\n".join(f"- `{o['ticker']}` — {o['company']}"
                         for o in result.get("options", []))
        return f"# Tên không rõ\n\n{result.get('hint', '')}\n\n{opts}\n"
    if result.get("status") == "backend_unavailable":
        return ("# Không truy cập được cơ sở dữ liệu\n\nĐây là sự cố kỹ thuật tạm thời, "
                "KHÔNG phải hệ thống thiếu dữ liệu về doanh nghiệp này.\n")
    if result.get("status") == "khong_du_doanh_nghiep_cung_loai":
        return (f"# Không so sánh được — {result.get('company', '')}\n\n"
                f"Chỉ tìm được {result['tim_thay']} doanh nghiệp cùng loại, "
                f"cần ít nhất {result['can_it_nhat']}.\n\n{result.get('canh_bao', '')}\n\n"
                "*Một bảng xếp hạng trên hai ba doanh nghiệp không nói lên điều gì, nên "
                "hệ thống không dựng nó ra.*\n")
    if result.get("status") != "ok":
        return f"# Không có dữ liệu\n\n{result.get('ly_do') or result.get('hint', '')}\n"

    cur = result.get("currency") or ""
    out = [f"# So sánh ngành — {result['company']} (`{result['ticker']}`)", ""]
    out.append(f"**Nhóm so sánh:** {result['mo_ta_nhom']} · "
               f"{result['so_doanh_nghiep_cung_loai']} doanh nghiệp · "
               f"năm {result['nam']}")
    out.append("")
    out.append(f"> Chọn năm {result['nam']}: {result['ly_do_chon_nam']}.")
    if result.get("canh_bao"):
        out.append(f">")
        out.append(f"> {result['canh_bao']}")
    out.append("")

    out.append("## 1. Vị trí trong nhóm")
    out.append("")
    out.append("| Chỉ tiêu | Giá trị | Thứ hạng | Phân vị | Trung vị nhóm |")
    out.append("|---|---:|---:|---:|---:|")
    for info in result["xep_hang"].values():
        if info["la_ty_le"]:
            value = f"{info['gia_tri']:,.1f}%"
            median = f"{info['trung_vi_nhom']:,.1f}%"
        else:
            value = _money(info["gia_tri"], cur)
            median = _money(info["trung_vi_nhom"], cur)
        out.append(f"| {info['nhan']} | {value} | {info['hang']}/"
                   f"{info['so_doanh_nghiep_co_so_lieu']} | {info['phan_vi']:g} | {median} |")
    out.append("")
    out.append("*Phân vị 100 là tốt nhất nhóm. Mẫu số đổi theo chỉ tiêu vì không phải "
               "doanh nghiệp nào cũng công bố đủ.*")
    out.append("")

    out.append("## 2. Doanh nghiệp cùng nhóm, xếp theo doanh thu")
    out.append("")
    out.append("| Doanh nghiệp | Doanh thu | Biên LN ròng | ROE |")
    out.append("|---|---:|---:|---:|")
    # Chèn chính doanh nghiệp đang xét vào bảng để người đọc thấy nó nằm ở đâu, không
    # phải tự đối chiếu hai bảng.
    subject = result["doanh_nghiep_dang_xet"]
    table = sorted(result["bang_cung_loai"] + [subject],
                   key=lambda r: r.get("revenue") or 0, reverse=True)
    for row in table:
        mark = "**" if row["ticker"] == result["ticker"] else ""
        margin = (f"{row['bien_loi_nhuan_rong']:,.1f}%"
                  if row.get("bien_loi_nhuan_rong") is not None else "—")
        roe = f"{row['roe']:,.1f}%" if row.get("roe") is not None else "—"
        out.append(f"| {mark}{row['name']}{mark} | {_money(row.get('revenue'), cur)} | "
                   f"{margin} | {roe} |")
    if result.get("da_cat_bot"):
        out.append("")
        out.append(f"*Đã cắt bớt {result['da_cat_bot']} doanh nghiệp nhỏ hơn.*")
    out.append("")

    out.append("## 3. Điều so sánh này KHÔNG nói được")
    out.append("")
    for gap in result["gaps"]:
        out.append(f"- {gap}")
    out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="So sanh doanh nghiep voi nhom cung loai")
    ap.add_argument("company", help="ten hoac ma doanh nghiep")
    ap.add_argument("--year", type=int, default=None, help="nam so sanh (mac dinh tu chon)")
    ap.add_argument("--rows", type=int, default=10, help="so dong trong bang cung nhom")
    ap.add_argument("--out", default=None, help="ghi ra tep markdown")
    args = ap.parse_args()

    started = time.time()
    result = peers_mod.benchmark(args.company, year=args.year, limit_table=args.rows)
    markdown = render(result)
    elapsed = round(time.time() - started, 1)

    logs.log_ingest("peer_benchmark", result.get("status", "?"),
                    company=args.company, ticker=result.get("ticker"),
                    method=result.get("phuong_phap"),
                    peers=result.get("so_doanh_nghiep_cung_loai"), seconds=elapsed)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        console.print(f"[green]Đã ghi[/] {args.out} · {elapsed} giây")
    else:
        # In thô: bảng markdown có dấu ngoặc vuông mà rich sẽ nuốt mất.
        print(markdown)
        console.print(f"\n[dim]{elapsed} giây[/]")

    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
