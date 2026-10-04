"""Bước 24: Gắn mã ngành SIC cho doanh nghiệp Mỹ.

    .venv/Scripts/python.exe scripts/24_load_us_sectors.py --apply

Cần cho chức năng gợi ý đầu tư: doanh nghiệp Mỹ cùng ngành là TÍN HIỆU NGÀNH TOÀN CẦU
khi đánh giá một doanh nghiệp Việt Nam. Xem chú thích đầu `src/ingest/us_sectors.py`.

Khoảng 6.000 lần gọi SEC, giãn nhịp theo chính sách Fair Access ≈ 15 phút. Chạy lại thì
chỉ lấy tiếp mã còn thiếu hoặc lỗi lần trước.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.graph.store import GraphStore
from src.ingest import us_sectors
from src.obs import logs

console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(description="Gan ma nganh SIC cho doanh nghiep My")
    ap.add_argument("--apply", action="store_true", help="ghi vao Neo4j")
    ap.add_argument("--limit", type=int, default=None, help="chi lay N ma (chay thu)")
    args = ap.parse_args()

    g = GraphStore()
    try:
        ciks = [r["cik"] for r in g.run(
            "MATCH (c:Company) WHERE c.cik IS NOT NULL RETURN c.cik AS cik ORDER BY cik")]
    finally:
        g.close()
    if args.limit:
        ciks = ciks[: args.limit]

    console.print(f"[cyan]{len(ciks):,} doanh nghiệp Mỹ[/]")

    def progress(i, total, row):
        if i % 250 == 0 or i == total:
            console.print(f"  {i:,}/{total:,}")

    cache = us_sectors.fetch_all(ciks, progress=progress)
    rows = [cache[str(c).zfill(10)] for c in ciks if str(c).zfill(10) in cache]

    ok = [r for r in rows if r.get("sic")]
    errors = [r for r in rows if "error" in r]
    no_sic = [r for r in rows if "error" not in r and not r.get("sic")]
    console.print(f"\nCó SIC: [green]{len(ok):,}[/] · không có SIC: {len(no_sic):,} · "
                  f"lỗi: [red]{len(errors):,}[/]")
    top = Counter(r["sic_desc"] for r in ok).most_common(8)
    for desc, n in top:
        console.print(f"   {n:5}  {desc}")

    if args.apply:
        written = us_sectors.write_to_graph(ok)
        console.print(f"[green]Đã gắn SIC cho {written:,} doanh nghiệp[/]")
        logs.log_ingest("us_sectors", "ok", with_sic=len(ok), errors=len(errors))
    else:
        console.print("[yellow]CHẠY THỬ — thêm --apply để ghi vào Neo4j.[/]")


if __name__ == "__main__":
    main()
