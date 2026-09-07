"""Bước 1: Tải báo cáo 10-K + dữ liệu XBRL từ SEC EDGAR.

Chạy:  .venv/Scripts/python.exe scripts/01_download.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import settings
from src.ingest.edgar import download_companyfacts, download_filing, list_filings

console = Console()


def main() -> None:
    table = Table(title="Dữ liệu tải về từ SEC EDGAR")
    table.add_column("Mã CK")
    table.add_column("Công ty")
    table.add_column("Loại")
    table.add_column("Năm TC")
    table.add_column("Ngày nộp")
    table.add_column("Kích thước")

    for ticker in settings.tickers:
        console.print(f"\n[bold cyan]{ticker}[/] — đang tra cứu bản khai...")
        try:
            refs = list_filings(ticker, settings.forms, settings.filings_per_company)
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]Lỗi:[/] {exc}")
            continue

        for ref in refs:
            path = download_filing(ref)
            size_mb = path.stat().st_size / 1_048_576
            table.add_row(
                ref.ticker,
                ref.company_name[:28],
                ref.form,
                ref.fiscal_year,
                ref.filing_date,
                f"{size_mb:.1f} MB",
            )
            console.print(f"  [green]✓[/] {ref.doc_id}  ({size_mb:.1f} MB)")

        facts = download_companyfacts(ticker)
        if facts:
            console.print(
                f"  [green]✓[/] companyfacts.json  "
                f"({facts.stat().st_size / 1_048_576:.1f} MB — dữ liệu XBRL có cấu trúc)"
            )

    console.print()
    console.print(table)


if __name__ == "__main__":
    main()
