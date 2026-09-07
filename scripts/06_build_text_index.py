"""Bước 6: Lập chỉ mục văn bản 10-K cho một tập doanh nghiệp.

Đây là tầng TỐN KÉM VỪA PHẢI trong ba tầng phủ dữ liệu — tốn hơn tầng số liệu (chỉ cần
một file bulk) nhưng rẻ hơn rất nhiều tầng đồ thị (mỗi chunk một lần gọi LLM).

Chi phí thực đo trên máy 24 nhân, khoảng 350 chunk mỗi doanh nghiệp, ~17 chunk/giây:

        48 công ty (cụm hệ sinh thái)  ~17.000 chunk   ~20 phút
       500 công ty (vốn hóa lớn)      ~175.000 chunk   ~3,5 giờ
     6.074 công ty (toàn sàn)       ~2.100.000 chunk   ~1,5 ngày

Cách chọn tập:
    --tier ecosystem      48 công ty cụm bán dẫn/cloud/AI (dùng chung với tầng đồ thị)
    --top-revenue 500     500 doanh nghiệp doanh thu lớn nhất, lấy từ chính Neo4j
    --tickers NVDA,AMD    danh sách chỉ định

Script bỏ qua doanh nghiệp đã có trong index, nên chạy lại an toàn và cộng dồn được.

Chạy:  .venv/Scripts/python.exe scripts/06_build_text_index.py --tier ecosystem
"""

import argparse
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from src.graph.store import GraphStore
from src.ingest.on_demand import ingest_company_text, is_text_indexed
from src.ingest.universe import ECOSYSTEM_SEEDS
from src.vector.store import VectorStore

console = Console()


def top_revenue_tickers(n: int, min_year: int = 2023) -> List[str]:
    """N doanh nghiệp doanh thu lớn nhất, lấy từ tầng số liệu đã nạp trong Neo4j.

    Dùng dữ liệu thật thay vì danh sách S&P 500 chép tay: tự cập nhật, và bao gồm cả
    doanh nghiệp ngoài Mỹ niêm yết ADR (TSMC, Toyota, SAP...) vốn không nằm trong S&P.
    """
    store = GraphStore()
    rows = store.run(
        """
        MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
        WHERE fy.fiscal_year >= $min_year AND fy.revenue IS NOT NULL
        WITH c.ticker AS ticker, max(fy.revenue) AS peak
        RETURN ticker ORDER BY peak DESC LIMIT $n
        """,
        min_year=min_year, n=n,
    )
    store.close()
    return [r["ticker"] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["ecosystem"], default=None)
    ap.add_argument("--top-revenue", type=int, default=None)
    ap.add_argument("--tickers", default=None, help="danh sách mã, ngăn bằng dấu phẩy")
    ap.add_argument("--filings", type=int, default=1, help="số bản khai gần nhất mỗi công ty")
    ap.add_argument("--force", action="store_true", help="lập chỉ mục lại cả công ty đã có")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.top_revenue:
        tickers = top_revenue_tickers(args.top_revenue)
    elif args.tier == "ecosystem":
        tickers = list(ECOSYSTEM_SEEDS)
    else:
        console.print("[red]Cần chọn một trong --tier / --top-revenue / --tickers[/]")
        return

    store = VectorStore()

    if not args.force:
        before = len(tickers)
        tickers = [t for t in tickers if is_text_indexed(t, store) == 0]
        skipped = before - len(tickers)
        if skipped:
            console.print(f"[dim]Bỏ qua {skipped} công ty đã có trong index.[/]")

    if not tickers:
        console.print("[green]Không còn gì để làm — tất cả đã được lập chỉ mục.[/]")
        console.print(f"Qdrant hiện có {store.count():,} điểm.")
        return

    console.print(f"[cyan]Sẽ lập chỉ mục {len(tickers)} doanh nghiệp.[/]")
    started = time.time()
    ok, failed, total_chunks = [], [], 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Lập chỉ mục", total=len(tickers))
        for ticker in tickers:
            result = ingest_company_text(ticker, n_filings=args.filings, vector_store=store)
            if result.get("status") in {"indexed", "already_indexed"}:
                ok.append(ticker)
                total_chunks += result.get("chunks", 0)
            else:
                failed.append((ticker, result.get("status"), result.get("error", "")[:70]))
            progress.advance(task)

    elapsed = time.time() - started
    console.print(
        f"\n[green]Xong.[/] {len(ok)} công ty · {total_chunks:,} chunk · "
        f"{elapsed / 60:.1f} phút ({total_chunks / max(elapsed, 1):.1f} chunk/giây)"
    )

    if failed:
        console.print(f"\n[yellow]{len(failed)} công ty không lập chỉ mục được:[/]")
        for ticker, status, err in failed[:20]:
            console.print(f"   {ticker:<6} {status:<16} {err}")

    console.print(f"\nQdrant hiện có [bold]{store.count():,}[/] điểm.")

    # Đánh dấu mức phủ để agent biết công ty nào đã có văn bản
    if ok:
        graph = GraphStore()
        graph.set_tier(ok, "text")
        graph.close()


if __name__ == "__main__":
    main()
