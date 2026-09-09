"""Bước 12: Nạp tầng số liệu cho doanh nghiệp niêm yết tại Việt Nam.

ĐIỀU NÀY CHỨNG MINH ĐIỀU GÌ

Giới hạn được ghi thẳng trong README: hệ thống không có doanh nghiệp Việt Nam nào ngoài
VinFast, vì nó chỉ đọc hồ sơ nộp cho SEC. Script này cho thấy đó là giới hạn của NGUỒN
DỮ LIỆU chứ không phải của kiến trúc — tầng số liệu chỉ cần bốn thứ (doanh nghiệp, năm,
chỉ tiêu, giá trị kèm đồng tiền), và nguồn nào cấp đủ bốn thứ đó đều cắm vào được.

Sau khi chạy, cùng bộ công cụ đó trả lời được "doanh thu FPT năm 2024" mà không phải sửa
một dòng nào trong agent.

PHẠM VI: CHỈ TẦNG SỐ LIỆU

Không làm tầng văn bản và tầng đồ thị cho Việt Nam, và đây là quyết định có cân nhắc:

  · Báo cáo thường niên Việt Nam là PDF, không có cấu trúc Item cố định như 10-K. Phải
    viết lại toàn bộ phần bóc tách.
  · Model nhúng vector đang dùng (`bge-small-en-v1.5`) CHỈ hiểu tiếng Anh. Muốn tìm kiếm
    theo ý nghĩa trên văn bản tiếng Việt phải đổi sang model đa ngữ, mà `bge-m3` có 1.024
    chiều thay vì 384 — tức là phải nhúng lại toàn bộ 23.869 đoạn hiện có vào một
    collection khác.

Tầng số liệu là phần rẻ nhất và cũng là phần chứng minh được luận điểm.

⚠️ KHÔNG QUY ĐỔI TỶ GIÁ. Số liệu Việt Nam lưu bằng VND, và `compare_financials` sẽ TỪ
CHỐI xếp hạng chung với số USD thay vì đoán bừa. Đây là hành vi đúng, không phải thiếu
sót — xem chú thích trong công cụ đó.

Chạy thử:  .venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py --limit 3
Chạy thật: .venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py --apply
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.graph.store import GraphStore
from src.ingest.vietnam import fetch_year_rows

console = Console()

# VN30 — rổ 30 cổ phiếu vốn hóa lớn và thanh khoản cao nhất sàn HOSE. Chọn nhóm này vì
# đây là những doanh nghiệp người dùng Việt Nam thực sự hỏi tới, và cũng là nhóm có số
# liệu đầy đủ nhất.
VN30 = [
    "ACB", "BCM", "BID", "BVH", "CTG", "DGC", "FPT", "GAS", "GVR", "HDB",
    "HPG", "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI",
    "STB", "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Nap so lieu doanh nghiep Viet Nam")
    ap.add_argument("--apply", action="store_true", help="ghi vao Neo4j (mac dinh chi chay thu)")
    ap.add_argument("--limit", type=int, default=None, help="chi lay N ma dau tien")
    ap.add_argument("--symbols", default=None, help="danh sach ma, cach nhau bang dau phay")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else list(VN30)
    if args.limit:
        symbols = symbols[: args.limit]

    console.print(f"[cyan]Lấy số liệu {len(symbols)} doanh nghiệp từ VCI...[/]\n")

    companies, all_rows, failed = [], [], []
    for symbol in symbols:
        try:
            rows = fetch_year_rows(symbol)
        except Exception as exc:  # noqa: BLE001
            failed.append((symbol, str(exc)[:90]))
            console.print(f"  [red]{symbol}[/] {str(exc)[:90]}")
            continue

        if not rows:
            failed.append((symbol, "không có dữ liệu năm nào"))
            continue

        companies.append({
            "ticker": f"{symbol}.VN",
            "symbol": symbol,
            "name": rows[0].get("company") or symbol,
            "exchange": "HOSE",
        })
        all_rows.extend(rows)
        years = f"{rows[-1]['fiscal_year']}–{rows[0]['fiscal_year']}"
        latest = rows[0].get("revenue")
        console.print(
            f"  [green]{symbol}[/] {len(rows)} năm ({years})"
            + (f" · doanh thu {latest / 1e12:,.1f} nghìn tỷ VND" if latest else "")
        )

    table = Table(title="Tổng kết")
    table.add_column("Chỉ số"); table.add_column("Giá trị", justify="right")
    table.add_row("Doanh nghiệp lấy được", str(len(companies)))
    table.add_row("Bản ghi năm tài chính", str(len(all_rows)))
    table.add_row("Thất bại", str(len(failed)))
    console.print(); console.print(table)

    if failed:
        console.print("\n[yellow]Không lấy được:[/]")
        for symbol, why in failed:
            console.print(f"  {symbol:6} {why}")

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi vào Neo4j.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    store = GraphStore()
    before = store.run("MATCH (c:Company) RETURN count(*) AS n")[0]["n"]

    store.upsert_vn_companies(companies)
    written = store.upsert_financial_years(all_rows)

    after = store.run("MATCH (c:Company) RETURN count(*) AS n")[0]["n"]
    vn_count = store.run("MATCH (c:Company {market:'VN'}) RETURN count(*) AS n")[0]["n"]
    vn_years = store.run(
        "MATCH (:Company {market:'VN'})-[:HAS_FINANCIALS]->(fy) RETURN count(fy) AS n"
    )[0]["n"]

    console.print(f"\nĐã ghi {written} bản ghi năm tài chính.")
    console.print(f"Doanh nghiệp: {before} → {after} (thêm {after - before})")
    console.print(f"Trong đó thị trường VN: [bold]{vn_count}[/] doanh nghiệp · {vn_years} bản ghi năm")

    # ĐỐI CHIẾU: số node mới phải đúng bằng số doanh nghiệp chưa từng nạp.
    if after - before > len(companies):
        console.print("[red]LỆCH: tạo ra nhiều node hơn số doanh nghiệp đã lấy.[/]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
