"""Bước 2: Nhúng toàn bộ chunk và ghi vào Qdrant.

Chạy:  .venv/Scripts/python.exe scripts/02_build_vector_index.py
Yêu cầu: docker compose up -d (Qdrant phải đang chạy)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from src.ingest.pipeline import list_local_filings, process_filing
from src.vector.store import VectorStore

console = Console()


def main() -> None:
    filings = list_local_filings()
    if not filings:
        console.print("[red]Chưa có dữ liệu. Chạy scripts/01_download.py trước.[/]")
        return

    store = VectorStore()
    console.print(f"[cyan]Tạo lại collection '{store.collection}'...[/]")
    store.recreate()

    started = time.time()
    total = 0

    for html in filings:
        meta, _, vector_chunks, _ = process_filing(html)
        n = store.upsert_chunks(vector_chunks)
        total += n
        console.print(f"  [green]✓[/] {meta['doc_id']:<16} {n:>4} chunk")

    elapsed = time.time() - started
    console.print(
        f"\n[bold green]Xong.[/] {total} chunk trong {elapsed:.0f}s "
        f"({total / elapsed:.0f} chunk/giây). Qdrant đang giữ {store.count()} điểm."
    )


if __name__ == "__main__":
    main()
