"""Bước 15: Nạp BÁO CÁO THƯỜNG NIÊN Việt Nam — tầng văn bản thật cho doanh nghiệp VN.

Đây là thứ mà `docs/nguon_du_lieu_viet_nam.md` từng kết luận là "một dự án riêng". Kết
luận đó dựa trên việc mọi cổng công bố thông tin đều là ứng dụng JavaScript. Hóa ra
VietStock lưu file ở một máy chủ tĩnh có đường dẫn quy luật, nên không cần trình duyệt.
Chi tiết và các bẫy đi kèm nằm ở đầu `src/ingest/vn_annual_report.py`.

⚠️ VÌ SAO MẶC ĐỊNH CHỈ VN30 CHỨ KHÔNG PHẢI CẢ 1.532 MÃ

Mỗi báo cáo nặng trung vị 11,5 MB. Toàn sàn là khoảng 18 GB tải về, chưa kể thời gian
nhúng: model đa ngữ chạy CPU được 13 đoạn/giây, mà mỗi báo cáo cho ra hơn một nghìn đoạn.
VN30 là nhóm người dùng thật sự hỏi tới, và cũng là nhóm có báo cáo đầy đủ nhất. Muốn
thêm thì `--symbols` hoặc `--limit`, cơ chế giống hệt.

KHO RIÊNG, MODEL RIÊNG — KHÔNG ĐỤNG KHO 10-K

Báo cáo tiếng Việt cần model đa ngữ, còn kho 10-K đang dùng model chỉ hiểu tiếng Anh.
Thay vì đổi model cho cả hệ thống (và phải nhúng lại 23.869 đoạn), kho này có model
riêng. Xem chú thích VN_REPORT_COLLECTION trong `src/vector/store.py`.

Chạy thử:  .venv/Scripts/python.exe scripts/15_load_vietnam_annual_reports.py --limit 3
Chạy thật: .venv/Scripts/python.exe scripts/15_load_vietnam_annual_reports.py --apply
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
from src.ingest import vn_ir_site
from src.ingest.vn_annual_report import (
    chunk_report, download, extract_pages, find_reports, is_annual_report, prose_pages,
)
from src.vector.store import (
    VN_PROFILE_COLLECTION, VN_REPORT_COLLECTION, VN_REPORT_DIM, VN_REPORT_MODEL, VectorStore,
)

console = Console()
RAW_DIR = ROOT / "data" / "raw" / "vn_ar"

VN30 = [
    "ACB", "BCM", "BID", "BVH", "CTG", "DGC", "FPT", "GAS", "GVR", "HDB",
    "HPG", "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI",
    "STB", "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Nap bao cao thuong nien Viet Nam")
    ap.add_argument("--apply", action="store_true", help="ghi vao Qdrant (mac dinh chay thu)")
    ap.add_argument("--limit", type=int, default=None, help="chi xu ly N ma dau tien")
    ap.add_argument("--symbols", default=None, help="danh sach ma, cach nhau bang dau phay")
    # Vì sao lùi tới 2019 chứ không dừng ở 2023: tám mã VN30 từng bị loại vì "không có
    # file" hoặc "bản scan" hóa ra đều có báo cáo thật ở năm cũ hơn — SAB ở 2020, SHB ở
    # 2020, SSB ở 2019, TPB ở 2020. Báo cáo cũ vẫn đáng giá vì mọi trích dẫn đều ghi rõ
    # năm, nên người đọc tự biết mình đang đọc thông tin của năm nào.
    ap.add_argument("--years", default="2025,2024,2023,2022,2021,2020,2019",
                    help="cac nam thu theo thu tu uu tien (moi nhat truoc)")
    args = ap.parse_args()

    years = tuple(int(y) for y in args.years.split(",") if y.strip())

    graph = GraphStore()
    known = {
        r["symbol"]: r
        for r in graph.run(
            "MATCH (c:Company {market:'VN'}) WHERE c.symbol IS NOT NULL "
            "RETURN c.symbol AS symbol, c.ticker AS ticker, c.name AS name, "
            "       c.exchange AS exchange"
        )
    }
    graph.close()
    if not known:
        console.print("[red]Chưa có doanh nghiệp Việt Nam nào.[/] Chạy scripts/12 trước.")
        raise SystemExit(1)

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else list(VN30))
    missing = [s for s in symbols if s not in known]
    if missing:
        console.print(f"[yellow]Chưa có trong đồ thị, bỏ qua:[/] {', '.join(missing)}")
    symbols = [s for s in symbols if s in known]
    if args.limit:
        symbols = symbols[: args.limit]

    console.print(f"[cyan]Xử lý {len(symbols)} mã[/] · thử các năm {years} · "
                  f"file tải về: [dim]{RAW_DIR}[/]")

    loaded, rejected, all_chunks = [], [], []
    started = time.time()
    with Progress(
        TextColumn("[progress.description]{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
        TimeRemainingColumn(), console=console,
    ) as progress:
        task = progress.add_task("Tải & bóc chữ", total=len(symbols))
        for symbol in symbols:
            info = known[symbol]
            try:
                # HAI NGUỒN, ƯU TIÊN NGUỒN GỐC.
                #
                # Trang của chính doanh nghiệp là nguồn đáng tin nhất (không qua trung
                # gian) và thường mới hơn hẳn: HPG có 2025 ở hoaphat.com.vn trong khi
                # VietStock dừng ở 2023. Nhưng nó chỉ phủ được vài mã có trang dựng sẵn
                # ở máy chủ, nên VietStock vẫn là nguồn phủ rộng phía sau.
                #
                # Gộp rồi sắp theo năm giảm dần; cùng một năm thì giữ bản của doanh
                # nghiệp. Vẫn thử lần lượt vì bản nào cũng có thể là bản scan.
                site = vn_ir_site.find_reports(symbol)
                static = find_reports(symbol, info.get("exchange") or "HOSE", years)
                merged = {}
                for found in list(static) + list(site):   # site ghi đè static cùng năm
                    merged[found["year"]] = found
                candidates = [merged[y] for y in sorted(merged, reverse=True)]
                if not candidates:
                    rejected.append((symbol, "—", "không có file nào ở cả hai nguồn"))
                    continue

                # Thử lần lượt từ bản mới nhất. Bản mới nhất có thể là bản scan (bóc ra
                # rỗng) trong khi năm trước đó lại là PDF có lớp chữ — thà có báo cáo cũ
                # hơn một năm còn hơn không có gì, miễn là ghi rõ năm trong từng trích dẫn.
                picked = None
                for found in candidates:
                    src = found.get("source", "vietstock")
                    suffix = "_ir" if src == "ir_site" else ""
                    path = RAW_DIR / f"{symbol}_{found['year']}{suffix}.pdf"
                    fetch = vn_ir_site.download if src == "ir_site" else download
                    size = fetch(found["url"], path)
                    pages = extract_pages(path)
                    ok, why = is_annual_report(pages)
                    if ok:
                        picked = (found, size, pages)
                        break
                    rejected.append((symbol, str(found["year"]), why))
                if not picked:
                    continue
                found, size, pages = picked
                # Năm cũ đã thử và hỏng thì không còn là "lỗi" nữa khi năm khác đã dùng được
                rejected[:] = [r for r in rejected if r[0] != symbol]

                chunks = chunk_report(
                    info["ticker"], info["name"] or symbol, found["year"], pages
                )
                all_chunks.extend(chunks)
                loaded.append({
                    "symbol": symbol, "year": found["year"], "mb": size / 1e6,
                    "source": "doanh nghiệp" if found.get("source") == "ir_site" else "VietStock",
                    "pages": len(pages), "prose": len(prose_pages(pages)),
                    "chunks": len(chunks),
                })
            except Exception as exc:  # noqa: BLE001
                rejected.append((symbol, "—", f"{type(exc).__name__}: {str(exc)[:60]}"))
            finally:
                progress.advance(task)

    console.print(f"\n[green]Xong[/] trong {(time.time() - started) / 60:.1f} phút")

    if loaded:
        table = Table(title="Báo cáo nạp được")
        for col in ("Mã", "Năm", "Nguồn", "MB", "Trang", "Trang văn xuôi", "Đoạn"):
            table.add_column(col, justify="left" if col in ("Mã", "Nguồn") else "right")
        for row in loaded:
            table.add_row(row["symbol"], str(row["year"]), row.get("source", "VietStock"),
                          f"{row['mb']:.1f}",
                          str(row["pages"]), str(row["prose"]), f"{row['chunks']:,}")
        console.print(table)
        lens = [len(c.text) for c in all_chunks]
        console.print(
            f"[bold]{len(loaded)}/{len(symbols)} mã[/] · {len(all_chunks):,} đoạn · "
            f"độ dài đoạn trung vị {int(statistics.median(lens))} ký tự"
        )

    if rejected:
        console.print(f"\n[yellow]Không nạp {len(rejected)} mã[/] — nói rõ lý do chứ không "
                      f"im lặng bỏ qua:")
        for symbol, year, why in rejected:
            console.print(f"   {symbol:<5} {year:<6} {why}")

    if not all_chunks:
        console.print("[yellow]Không có đoạn nào để nạp.[/]")
        return

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi vào Qdrant.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    # --- Ghi vào Qdrant ---
    console.print(f"\n[cyan]Nhúng {len(all_chunks):,} đoạn bằng {VN_REPORT_MODEL}[/] "
                  f"(CPU, khoảng {len(all_chunks) / 13 / 60:.0f} phút)...")
    store = VectorStore(collection=VN_REPORT_COLLECTION, model_name=VN_REPORT_MODEL,
                        dim=VN_REPORT_DIM)
    store.ensure_collection()
    before = store.count()

    # Xóa sạch điểm cũ của đúng những mã sắp nạp, rồi mới ghi. Đây là ẢNH CHỤP chứ không
    # phải dữ liệu cộng dồn: đổi cách cắt đoạn là số đoạn đổi theo, và điểm cũ mang số
    # thứ tự cao hơn sẽ không bị ghi đè — xem chú thích trong `delete_by_tickers`.
    tickers = sorted({c.ticker for c in all_chunks})
    store.delete_by_tickers(tickers)
    base = store.count()
    if before != base:
        console.print(f"[dim]Dọn lần nạp trước: bỏ {before - base:,} điểm cũ của "
                      f"{len(tickers)} mã[/]")

    embed_started = time.time()
    written = store.upsert_chunks(all_chunks)
    after = store.count()
    console.print(f"Đã ghi {written:,} đoạn trong {(time.time() - embed_started) / 60:.1f} phút")
    console.print(f"Kho báo cáo thường niên: {before:,} → {after:,}")

    # ĐỐI CHIẾU. Hai kho kia PHẢI giữ nguyên — đó là toàn bộ lý do tách collection.
    main_count = VectorStore().count()
    profile_count = VectorStore(collection=VN_PROFILE_COLLECTION).count()
    console.print(f"Kho 10-K: {main_count:,} đoạn · kho mô tả: {profile_count:,} đoạn "
                  f"[dim](cả hai phải không đổi)[/]")

    # chunk_id cố định theo mã/năm/trang nên chạy lại ghi đè đúng điểm cũ, không nhân bản.
    if after != base + len(all_chunks):
        console.print(f"[red]LỆCH: kho có {after:,} điểm, lẽ ra phải là "
                      f"{base + len(all_chunks):,} ({base:,} của mã khác + "
                      f"{len(all_chunks):,} vừa nạp).[/]")
        raise SystemExit(1)
    console.print("[green]Đối chiếu khớp.[/]")


if __name__ == "__main__":
    main()
