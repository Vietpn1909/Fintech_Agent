"""Bước 16: Nạp báo cáo thường niên BẢN SCAN bằng OCR — mã cuối cùng còn thiếu.

Chỉ chạy cho những mã mà bước 15 đã bó tay với lý do "bản scan — cần OCR". Ở thời điểm
viết, đó là đúng một mã: DGC. Cả bảy năm 2019–2025 của DGC đều là PDF ảnh, và cả hai
nguồn (kho tĩnh VietStock, trang doanh nghiệp) đều không có bản nào khác.

⚠️ VÌ SAO SCRIPT NÀY TÁCH RIÊNG CHỨ KHÔNG GỘP VÀO BƯỚC 15

OCR không cùng hạng tin cậy với chữ bóc từ PDF. Gộp vào bước 15 là để nó chạy lẫn mỗi
lần nạp, và dần dần không ai còn nhớ đoạn nào là chữ thật đoạn nào là chữ máy đoán. Tách
ra thì mỗi lần dùng OCR đều là một quyết định có ý thức, và `--symbols` bắt buộc phải ghi
rõ mã — không có mặc định, không chạy cả rổ.

Chi phí: khoảng 2–4 giây mỗi trang ở 200 DPI trên CPU. Một báo cáo 60–90 trang mất 3–6
phút, chưa kể nhúng vector.

Chạy thử:  .venv/Scripts/python.exe scripts/16_ocr_vietnam_reports.py --symbols DGC
Chạy thật: .venv/Scripts/python.exe scripts/16_ocr_vietnam_reports.py --symbols DGC --apply
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.graph.store import GraphStore
from src.ingest import vn_ir_site, vn_ocr
from src.ingest.vn_annual_report import chunk_report, download, find_reports, prose_pages
from src.vector.store import (
    VN_PROFILE_COLLECTION, VN_REPORT_COLLECTION, VN_REPORT_DIM, VN_REPORT_MODEL, VectorStore,
)

console = Console()
RAW_DIR = ROOT / "data" / "raw" / "vn_ar"


def main() -> None:
    ap = argparse.ArgumentParser(description="Nap bao cao ban scan bang OCR")
    ap.add_argument("--symbols", required=True,
                    help="danh sach ma, cach nhau bang dau phay (BAT BUOC, khong co mac dinh)")
    ap.add_argument("--apply", action="store_true", help="ghi vao Qdrant (mac dinh chay thu)")
    ap.add_argument("--years", default="2025,2024,2023",
                    help="cac nam thu theo thu tu uu tien")
    ap.add_argument("--dpi", type=int, default=vn_ocr.DEFAULT_DPI)
    args = ap.parse_args()

    ready, why = vn_ocr.check_ready()
    if not ready:
        console.print(f"[red]Chưa chạy OCR được:[/] {why}")
        raise SystemExit(1)

    years = tuple(int(y) for y in args.years.split(",") if y.strip())
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    graph = GraphStore()
    known = {
        r["symbol"]: r
        for r in graph.run(
            "MATCH (c:Company {market:'VN'}) WHERE c.symbol IN $syms "
            "RETURN c.symbol AS symbol, c.ticker AS ticker, c.name AS name, "
            "       c.exchange AS exchange", syms=symbols
        )
    }
    graph.close()
    missing = [s for s in symbols if s not in known]
    if missing:
        console.print(f"[yellow]Chưa có trong đồ thị, bỏ qua:[/] {', '.join(missing)}")
    symbols = [s for s in symbols if s in known]
    if not symbols:
        console.print("[red]Không còn mã nào để xử lý.[/]")
        raise SystemExit(1)

    console.print(f"[cyan]OCR {len(symbols)} mã[/] · {args.dpi} DPI · gói ngôn ngữ "
                  f"[dim]{vn_ocr.DEFAULT_LANG}[/] · thử các năm {years}")

    loaded, rejected, all_chunks = [], [], []
    started = time.time()
    for symbol in symbols:
        info = known[symbol]
        # Cùng hai nguồn như bước 15 — bản scan có thể nằm ở nguồn nào cũng được.
        site = vn_ir_site.find_reports(symbol)
        static = find_reports(symbol, info.get("exchange") or "HOSE", years)
        merged = {}
        for found in list(static) + list(site):
            merged[found["year"]] = found
        candidates = [merged[y] for y in sorted(merged, reverse=True)]
        if not candidates:
            rejected.append((symbol, "—", "không tìm thấy file nào"))
            continue

        found = candidates[0]   # bản mới nhất; OCR đắt nên không thử lần lượt nhiều năm
        src = found.get("source", "vietstock")
        path = RAW_DIR / f"{symbol}_{found['year']}{'_ir' if src == 'ir_site' else ''}.pdf"
        fetch = vn_ir_site.download if src == "ir_site" else download
        fetch(found["url"], path)

        with Progress(
            TextColumn(f"[progress.description]{symbol} {found['year']} — đọc ảnh"),
            BarColumn(), TextColumn("{task.completed}/{task.total} trang"),
            TimeElapsedColumn(), TimeRemainingColumn(), console=console,
        ) as progress:
            task = progress.add_task("ocr", total=1)

            def tick(done: int, total: int) -> None:
                progress.update(task, completed=done, total=total)

            pages = vn_ocr.ocr_document(path, dpi=args.dpi, progress=tick)

        ok, why, stats = vn_ocr.quality(pages)
        if not ok:
            rejected.append((symbol, str(found["year"]), why))
            continue

        chunks = chunk_report(info["ticker"], info["name"] or symbol, found["year"],
                              pages, ocr=True)
        if not chunks:
            rejected.append((symbol, str(found["year"]),
                             "OCR đọc được chữ nhưng không trang nào là văn xuôi"))
            continue
        all_chunks.extend(chunks)
        loaded.append({
            "symbol": symbol, "year": found["year"], "pages": len(pages),
            "prose": len(prose_pages(pages)), "chunks": len(chunks),
            "vi": stats.get("ty_le_co_dau", 0.0),
        })

    console.print(f"\n[green]Xong[/] trong {(time.time() - started) / 60:.1f} phút")

    if loaded:
        table = Table(title="Báo cáo nạp được qua OCR")
        for col in ("Mã", "Năm", "Trang", "Trang văn xuôi", "Đoạn", "Tỷ lệ có dấu"):
            table.add_column(col, justify="right" if col != "Mã" else "left")
        for row in loaded:
            table.add_row(row["symbol"], str(row["year"]), str(row["pages"]),
                          str(row["prose"]), f"{row['chunks']:,}", f"{row['vi']:.1%}")
        console.print(table)
        lens = [len(c.text) for c in all_chunks]
        console.print(f"[bold]{len(loaded)}/{len(symbols)} mã[/] · {len(all_chunks):,} đoạn "
                      f"· độ dài trung vị {int(statistics.median(lens))} ký tự")
        console.print("[dim]Mọi đoạn mang nhãn 'chữ do OCR từ bản scan' ngay trong tiêu đề "
                      "trích dẫn, nên nhãn hiện ra cùng câu trả lời.[/]")

    if rejected:
        console.print(f"\n[yellow]Không nạp {len(rejected)} mã[/] — nói rõ lý do:")
        for symbol, year, why in rejected:
            console.print(f"   {symbol:<5} {year:<6} {why}")

    if not all_chunks:
        console.print("[yellow]Không có đoạn nào để nạp.[/]")
        return
    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi vào Qdrant.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    console.print(f"\n[cyan]Nhúng {len(all_chunks):,} đoạn bằng {VN_REPORT_MODEL}[/]...")
    store = VectorStore(collection=VN_REPORT_COLLECTION, model_name=VN_REPORT_MODEL,
                        dim=VN_REPORT_DIM)
    store.ensure_collection()
    before = store.count()
    tickers = sorted({c.ticker for c in all_chunks})
    store.delete_by_tickers(tickers)
    base = store.count()
    if before != base:
        console.print(f"[dim]Dọn lần nạp trước: bỏ {before - base:,} điểm cũ[/]")

    written = store.upsert_chunks(all_chunks)
    after = store.count()
    console.print(f"Đã ghi {written:,} đoạn · kho báo cáo: {before:,} → {after:,}")
    console.print(f"Kho 10-K: {VectorStore().count():,} · kho mô tả: "
                  f"{VectorStore(collection=VN_PROFILE_COLLECTION).count():,} "
                  f"[dim](cả hai phải không đổi)[/]")

    if after != base + len(all_chunks):
        console.print(f"[red]LỆCH: kho có {after:,} điểm, lẽ ra {base + len(all_chunks):,}.[/]")
        raise SystemExit(1)
    console.print("[green]Đối chiếu khớp.[/]")


if __name__ == "__main__":
    main()
