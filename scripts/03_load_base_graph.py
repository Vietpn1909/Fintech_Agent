"""Bước 3: Nạp phần "xương sống" của đồ thị — công ty, bản khai, số liệu tài chính.

Bước này KHÔNG cần LLM. Toàn bộ dữ liệu ở đây đến từ nguồn có cấu trúc của SEC, nên nó
chính xác tuyệt đối và chạy trong vài giây. Phần do LLM trích xuất (quan hệ cạnh tranh,
chuỗi cung ứng, rủi ro) được đắp thêm lên bộ xương này ở bước 04.

Chạy:  .venv/Scripts/python.exe scripts/03_load_base_graph.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import RAW_DIR, settings
from src.graph.store import GraphStore
from src.ingest.edgar import load_ticker_map
from src.ingest.pipeline import list_local_filings, load_doc_meta
from src.ingest.xbrl import extract_facts

console = Console()


def main() -> None:
    store = GraphStore()
    console.print("[cyan]Tạo ràng buộc và index...[/]")
    store.init_schema()

    # --- Công ty ---
    tmap = load_ticker_map()
    companies = []
    for ticker in settings.tickers:
        facts_path = RAW_DIR / ticker / "companyfacts.json"
        if not facts_path.exists():
            continue
        companies.append(
            {"ticker": ticker, "name": tmap[ticker]["name"], "cik": tmap[ticker]["cik"]}
        )
    store.upsert_companies(companies)
    console.print(f"  [green]✓[/] {len(companies)} công ty")

    # --- Bản khai ---
    filings = [load_doc_meta(p) for p in list_local_filings()]
    store.upsert_filings(filings)
    console.print(f"  [green]✓[/] {len(filings)} bản khai")

    # --- Số liệu tài chính từ XBRL ---
    total_facts = 0
    for ticker in settings.tickers:
        facts_path = RAW_DIR / ticker / "companyfacts.json"
        if not facts_path.exists():
            continue
        facts = [f.to_dict() for f in extract_facts(facts_path, ticker)]
        store.upsert_metrics(facts)
        total_facts += len(facts)
        console.print(f"  [green]✓[/] {ticker}: {len(facts)} số liệu")

    console.print(f"\n[bold]Tổng {total_facts} số liệu tài chính đã vào đồ thị.[/]")

    # --- Báo cáo trạng thái ---
    stats = store.stats()
    table = Table(title="Trạng thái đồ thị")
    table.add_column("Loại node")
    table.add_column("Số lượng", justify="right")
    for row in stats["nodes"]:
        table.add_row(row["label"], str(row["n"]))
    for row in stats["relationships"]:
        table.add_row(f"[dim]-[{row['type']}]->[/]", str(row["n"]))
    console.print(table)

    # --- Kiểm chứng bằng một truy vấn thật ---
    console.print("\n[cyan]Kiểm chứng — doanh thu 3 năm gần nhất, truy vấn bằng Cypher:[/]")
    rows = store.run(
        """
        MATCH (c:Company)-[:REPORTED]->(m:Metric {metric: 'revenue'})
        WHERE m.fiscal_year >= 2024
        RETURN c.ticker AS ticker, m.fiscal_year AS year,
               round(m.value / 1000000000.0, 2) AS ty_usd
        ORDER BY ticker, year
        """
    )
    for r in rows:
        console.print(f"   {r['ticker']:<6} FY{r['year']}  {r['ty_usd']:>8.2f} tỷ USD")

    store.close()


if __name__ == "__main__":
    main()
