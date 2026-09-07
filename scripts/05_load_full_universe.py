"""Bước 5: Nạp số liệu tài chính của TOÀN BỘ ~8.000 doanh nghiệp vào Neo4j.

Bước này KHÔNG dùng một lần gọi LLM nào, và đó chính là điểm mạnh của nó.

Nguồn dữ liệu là file bulk companyfacts.zip (1,41GB) của SEC — chứa toàn bộ dữ liệu
XBRL do mọi doanh nghiệp khai báo. Script đọc THẲNG từng thành viên bên trong file zip,
không giải nén ra đĩa, nên chỉ tốn 1,41GB thay vì khoảng 18GB.

Kết quả: agent trả lời được câu hỏi tra số về bất kỳ doanh nghiệp niêm yết nào tại Mỹ,
kể cả các tập đoàn ngoài Mỹ có niêm yết ADR (TSMC, Toyota, SAP, Alibaba, Shell...).

Tải file bulk trước nếu chưa có:
    curl -L -H "User-Agent: Ten Ban email@cua.ban" \\
         -o data/raw/companyfacts.zip \\
         https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip

Chạy:  .venv/Scripts/python.exe scripts/05_load_full_universe.py
Thử nhanh: thêm --limit 300
"""

import argparse
import json
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config.settings import RAW_DIR
from src.graph.store import GraphStore
from src.ingest.universe import CompanyRef, load_full_universe
from src.ingest.xbrl import extract_facts_from_data, facts_to_year_rows

console = Console()
ZIP_PATH = RAW_DIR / "companyfacts.zip"

# Mỗi tiến trình con giữ một handle zip riêng. zipfile không an toàn khi dùng chung
# giữa nhiều luồng/tiến trình, nên mỗi worker phải tự mở lấy một handle.
_worker_zip: Optional[zipfile.ZipFile] = None


def _init_worker() -> None:
    global _worker_zip
    _worker_zip = zipfile.ZipFile(ZIP_PATH)


def _process_one(args: Tuple[str, str, str]) -> Tuple[str, list]:
    """Đọc một công ty trong zip -> các bản ghi theo năm. Chạy trong tiến trình con."""
    ticker, cik, name = args
    try:
        raw = _worker_zip.read(f"CIK{cik}.json")
    except KeyError:
        return ticker, []  # doanh nghiệp không có dữ liệu XBRL (thường là quỹ, SPAC cũ)
    except Exception:  # noqa: BLE001
        return ticker, []

    try:
        data = json.loads(raw)
        facts = extract_facts_from_data(data, ticker)
        return ticker, facts_to_year_rows(facts, name=name)
    except Exception:  # noqa: BLE001
        return ticker, []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="chỉ xử lý N công ty đầu (để thử)")
    ap.add_argument("--workers", type=int, default=6, help="số tiến trình đọc/parse song song")
    ap.add_argument("--min-year", type=int, default=2015, help="bỏ các năm tài chính cũ hơn")
    ap.add_argument(
        "--include-otc", action="store_true",
        help="nạp cả mã OTC (mặc định chỉ lấy Nasdaq/NYSE/CBOE)",
    )
    args = ap.parse_args()

    if not ZIP_PATH.exists():
        console.print(f"[red]Không tìm thấy {ZIP_PATH}[/] — xem hướng dẫn tải ở đầu file này.")
        return

    universe: List[CompanyRef] = load_full_universe(listed_only=not args.include_otc)
    if args.limit:
        universe = universe[: args.limit]

    console.print(
        f"[cyan]Vũ trụ:[/] {len(universe):,} doanh nghiệp"
        f"{'' if args.include_otc else ' (đã loại mã OTC)'}   "
        f"[cyan]Tiến trình song song:[/] {args.workers}"
    )

    # --- Đọc và bóc tách song song ---
    started = time.time()
    payload = [(c.ticker, c.cik, c.name) for c in universe]
    all_rows: List[dict] = []
    empty = 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Đọc XBRL", total=len(payload))
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as pool:
            for ticker, rows in pool.map(_process_one, payload, chunksize=25):
                rows = [r for r in rows if r["fiscal_year"] >= args.min_year]
                if rows:
                    all_rows.extend(rows)
                else:
                    empty += 1
                progress.advance(task)

    parse_time = time.time() - started
    covered = len(universe) - empty
    console.print(
        f"[green]Bóc tách xong[/] trong {parse_time / 60:.1f} phút — "
        f"{covered:,} doanh nghiệp có số liệu, {empty:,} không có "
        f"({len(all_rows):,} bản ghi năm)."
    )

    # --- Ghi vào Neo4j ---
    store = GraphStore()
    store.init_schema()

    console.print(f"\n[cyan]Ghi {len(universe):,} node Company...[/]")
    # Mọi doanh nghiệp bắt đầu ở mức phủ "metrics". KHÔNG gán trước mức "text" hay
    # "graph" cho cụm hệ sinh thái: mức phủ phải phản ánh dữ liệu THỰC SỰ đã nạp, chứ
    # không phải dữ liệu dự định sẽ nạp. Gán trước rồi để bước sau ghi đè đã từng khiến
    # giao diện báo "4 doanh nghiệp có đồ thị" trong khi đồ thị chưa có cạnh nào.
    # Script 06 nâng lên "text", script 04 nâng lên "graph", và set_tier chỉ nâng, không hạ.
    store.upsert_companies(
        [
            {"ticker": c.ticker, "name": c.name, "cik": c.cik,
             "exchange": c.exchange, "tier": "metrics"}
            for c in universe
        ]
    )

    console.print(f"[cyan]Ghi {len(all_rows):,} node FinancialYear...[/]")
    write_started = time.time()
    store.upsert_financial_years(all_rows)
    console.print(f"[green]Ghi xong[/] trong {(time.time() - write_started) / 60:.1f} phút.")

    # --- Kiểm chứng ---
    stats = store.stats()
    table = Table(title="Đồ thị sau khi nạp toàn vũ trụ")
    table.add_column("Loại")
    table.add_column("Số lượng", justify="right")
    for row in stats["nodes"]:
        table.add_row(row["label"], f"{row['n']:,}")
    for row in stats["relationships"]:
        table.add_row(f"-[{row['type']}]->", f"{row['n']:,}")
    console.print(table)

    # ⚠️ PHẢI LỌC THEO ĐỒNG TIỀN TRƯỚC KHI XẾP HẠNG.
    #
    # Câu truy vấn này ban đầu xếp hạng theo giá trị thô rồi in kèm chữ "tỷ USD" cứng.
    # Sau khi hỗ trợ đa tiền tệ, nó cho ra bảng hoàn toàn sai: Ecopetrol (peso Colombia),
    # KEPCO (won Hàn Quốc) và Toyota (yên Nhật) đứng trên tất cả doanh nghiệp Mỹ, chỉ vì
    # con số danh nghĩa lớn hơn. Đây đúng là lỗi mà tầng công cụ đã có cơ chế chặn — và
    # nó vẫn lọt vào chính câu kiểm chứng.
    console.print("\n[cyan]Kiểm chứng — 10 doanh nghiệp doanh thu USD lớn nhất năm 2024:[/]")
    rows = store.run(
        """
        MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
        WHERE fy.fiscal_year = 2024 AND fy.revenue IS NOT NULL
          AND coalesce(fy.currency, 'USD') = 'USD'
        RETURN c.ticker AS ticker, c.name AS name,
               round(fy.revenue / 1000000000.0, 1) AS ty
        ORDER BY fy.revenue DESC LIMIT 10
        """
    )
    for i, r in enumerate(rows, 1):
        console.print(f"   {i:>2}. {r['ticker']:<6} {r['name'][:38]:<38} {r['ty']:>8.1f} tỷ USD")

    console.print("\n[cyan]Doanh nghiệp báo cáo bằng đồng tiền khác (không so sánh trực tiếp được):[/]")
    other = store.run(
        """
        MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
        WHERE fy.fiscal_year = 2024 AND fy.revenue IS NOT NULL
          AND coalesce(fy.currency, 'USD') <> 'USD'
        RETURN fy.currency AS currency, count(*) AS n
        ORDER BY n DESC LIMIT 8
        """
    )
    console.print("   " + " · ".join(f"{r['currency']}: {r['n']} DN" for r in other))

    store.close()


if __name__ == "__main__":
    main()
