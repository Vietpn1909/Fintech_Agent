"""Bước 14: Mô tả doanh nghiệp Việt Nam — tầng văn bản HẠNG NHẸ, không đổi model nhúng.

VÌ SAO CÓ BƯỚC NÀY

Doanh nghiệp Việt Nam có số liệu (bước 12) và quan hệ sở hữu (bước 13), nhưng chưa có
một dòng văn bản nào. Hỏi "FPT làm gì" thì `search_filings` trả về rỗng.

Tầng văn bản THẬT — đọc báo cáo thường niên — là một dự án riêng: không cổng công bố
thông tin nào của Việt Nam có API (phải dùng trình duyệt thật), báo cáo là PDF nhiều cột
không có mốc "Item 1A" để cắt, và model nhúng đang dùng chỉ hiểu tiếng Anh nên muốn nhúng
tiếng Việt phải nhúng lại toàn bộ 23.869 đoạn 10-K. Xem docs/nguon_du_lieu_viet_nam.md.

Nhưng VCI có sẵn một thứ nhỏ hơn mà dùng ngay được: trường `enProfile`, mô tả doanh
nghiệp bằng TIẾNG ANH. Tiếng Anh nghĩa là nhúng được bằng đúng model hiện tại, không đụng
gì tới kho 10-K.

⚠️ NÓI THẲNG NÓ LÀ GÌ VÀ KHÔNG LÀ GÌ

Trung vị khoảng 850 ký tự — MỘT đoạn văn mỗi doanh nghiệp. Nó trả lời được "doanh nghiệp
này làm ngành gì, thành lập năm nào, hoạt động ở đâu". Nó KHÔNG trả lời được "doanh nghiệp
nêu rủi ro gì" — đó là nội dung báo cáo thường niên. Ba lớp để agent không nhầm hai thứ:

  1. Nằm ở collection RIÊNG (VN_PROFILE_COLLECTION), không trộn vào kho 10-K — nên tìm
     kiếm không lọc công ty vẫn cho đúng kết quả như trước, không bị 1.532 đoạn mô tả
     ngắn chen vào.
  2. `item` = "PROFILE", kèm ghi chú nguồn trong mỗi kết quả trả về.
  3. Quy tắc 4d trong ANSWER_PROMPT: kết quả PROFILE chỉ được dùng để nói doanh nghiệp
     làm gì, không được trình bày như nội dung báo cáo.

Mức phủ (`tier`) KHÔNG đổi: nó đo hồ sơ SEC, và một đoạn mô tả không phải hồ sơ.

Chạy thử:  .venv/Scripts/python.exe scripts/14_load_vietnam_profiles.py --limit 30
Chạy thật: .venv/Scripts/python.exe scripts/14_load_vietnam_profiles.py --resume --apply
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config.settings import PROCESSED_DIR, settings  # noqa: F401 — chỉnh stdout sang UTF-8
from src.graph.store import GraphStore
from src.ingest.chunker import Chunk
from src.ingest.vietnam import fetch_profile
from src.vector.store import VN_PROFILE_COLLECTION, VectorStore

console = Console()

CACHE_PATH = PROCESSED_DIR / "vn_profiles.jsonl"
ATTEMPTED_PATH = PROCESSED_DIR / "vn_profiles_attempted.txt"

ITEM = "PROFILE"
ITEM_TITLE = "Company profile (VCI) — not an annual report"


def load_attempted() -> set:
    if not ATTEMPTED_PATH.exists():
        return set()
    return {ln.strip() for ln in ATTEMPTED_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()}


def read_cache() -> list:
    if not CACHE_PATH.exists():
        return []
    rows = []
    with CACHE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Nap mo ta doanh nghiep Viet Nam vao Qdrant")
    ap.add_argument("--apply", action="store_true", help="ghi vao Qdrant (mac dinh chi chay thu)")
    ap.add_argument("--limit", type=int, default=None, help="chi lay N ma dau tien")
    ap.add_argument("--symbols", default=None, help="danh sach ma, cach nhau bang dau phay")
    ap.add_argument("--resume", action="store_true", help="bo qua ma da lay o lan truoc")
    ap.add_argument("--load-only", action="store_true", help="chi nap file jsonl vao Qdrant")
    args = ap.parse_args()

    # Chỉ lấy mô tả cho doanh nghiệp ĐÃ CÓ trong đồ thị: mô tả của một mã không có số liệu
    # thì agent phân giải không tới, nằm trong kho mà không bao giờ được đọc.
    graph = GraphStore()
    companies = {
        r["symbol"]: {"ticker": r["ticker"], "name": r["name"]}
        for r in graph.run(
            "MATCH (c:Company {market:'VN'}) WHERE c.symbol IS NOT NULL "
            "RETURN c.symbol AS symbol, c.ticker AS ticker, c.name AS name"
        )
    }
    graph.close()
    console.print(f"[cyan]Doanh nghiệp Việt Nam trong đồ thị:[/] {len(companies):,}")
    if not companies:
        console.print("[red]Chưa có doanh nghiệp Việt Nam nào.[/] Chạy scripts/12 trước.")
        raise SystemExit(1)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        missing = [s for s in symbols if s not in companies]
        if missing:
            console.print(f"[yellow]Chưa có trong đồ thị, bỏ qua:[/] {', '.join(missing)}")
        symbols = [s for s in symbols if s in companies]
    else:
        symbols = sorted(companies)
    if args.limit:
        symbols = symbols[: args.limit]

    # --- Lấy mô tả ---
    if not args.load_only:
        done = load_attempted() if args.resume else set()
        if not args.resume:
            CACHE_PATH.unlink(missing_ok=True)
            ATTEMPTED_PATH.unlink(missing_ok=True)

        pending = [s for s in symbols if s not in done]
        console.print(
            f"[cyan]Cần lấy:[/] {len(pending):,}/{len(symbols):,} mã"
            + (f"  (bỏ qua {len(done):,} mã đã thử)" if done else "")
            + f"  ·  ước tính {len(pending) * 0.42 / 60:.0f} phút"
        )

        failed, empty = [], 0
        started = time.time()
        with CACHE_PATH.open("a", encoding="utf-8") as out, \
             ATTEMPTED_PATH.open("a", encoding="utf-8") as attempted, Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(), TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(), TimeRemainingColumn(), console=console,
             ) as progress:
            task = progress.add_task("Lấy mô tả", total=len(pending))
            for symbol in pending:
                try:
                    text = fetch_profile(symbol)
                except Exception as exc:  # noqa: BLE001
                    # Cố ý KHÔNG ghi vào attempted: lỗi mạng thì nên thử lại lần sau.
                    failed.append((symbol, f"{type(exc).__name__}: {str(exc)[:70]}"))
                    progress.advance(task)
                    continue
                attempted.write(f"{symbol}\n")
                attempted.flush()
                if not text:
                    empty += 1
                else:
                    out.write(json.dumps({"symbol": symbol, "text": text}, ensure_ascii=False) + "\n")
                    out.flush()
                progress.advance(task)

        console.print(
            f"\n[green]Lấy xong[/] trong {(time.time() - started) / 60:.1f} phút · "
            f"{empty} mã không có mô tả tiếng Anh · {len(failed)} mã lỗi"
        )
        for symbol, why in failed[:10]:
            console.print(f"  [yellow]{symbol}[/] {why}")

    # --- Tổng hợp ---
    rows = [r for r in read_cache() if r.get("symbol") in companies and r.get("text")]
    if not rows:
        console.print("[yellow]Chưa có mô tả nào.[/]")
        return

    lengths = [len(r["text"]) for r in rows]
    table = Table(title="Tổng kết")
    table.add_column("Chỉ số"); table.add_column("Giá trị", justify="right")
    table.add_row("Doanh nghiệp có mô tả tiếng Anh", f"{len(rows):,} / {len(companies):,}")
    table.add_row("Độ dài ngắn nhất", f"{min(lengths):,} ký tự")
    table.add_row("Độ dài trung vị", f"{int(statistics.median(lengths)):,} ký tự")
    table.add_row("Độ dài dài nhất", f"{max(lengths):,} ký tự")
    console.print(); console.print(table)
    console.print(f"[dim]Ví dụ ({rows[0]['symbol']}): {rows[0]['text'][:180]}…[/]")

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi vào Qdrant.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    # --- Ghi vào Qdrant, collection RIÊNG ---
    chunks = [
        Chunk(
            chunk_id=f"{companies[r['symbol']]['ticker']}_PROFILE",
            doc_id=f"{companies[r['symbol']]['ticker']}_PROFILE",
            ticker=companies[r["symbol"]]["ticker"],
            company=companies[r["symbol"]]["name"],
            form=ITEM,
            # Không phải tài liệu của năm tài chính nào. 0 thay vì năm hiện tại để không ai
            # đọc nhầm thành "trích từ báo cáo năm 2026".
            fiscal_year="0",
            item=ITEM,
            item_title=ITEM_TITLE,
            text=r["text"],
            seq=0,
        )
        for r in rows
    ]

    store = VectorStore(collection=VN_PROFILE_COLLECTION)
    store.ensure_collection()
    before = store.count()
    written = store.upsert_chunks(chunks)
    after = store.count()

    # Kho 10-K chính KHÔNG được đổi một điểm nào — đó là lý do tách collection.
    main_count = VectorStore().count()

    console.print(f"\nĐã ghi {written:,} đoạn vào [cyan]{VN_PROFILE_COLLECTION}[/]")
    console.print(f"Collection mô tả: {before:,} → {after:,}")
    console.print(f"Kho 10-K chính: {main_count:,} đoạn (phải giữ nguyên 23.869)")

    # ĐỐI CHIẾU. chunk_id cố định theo mã nên chạy lại ghi đè đúng điểm cũ: số điểm sau
    # cùng phải đúng bằng số mô tả, không hơn. Hơn nghĩa là có điểm trùng.
    if after != len(chunks):
        console.print(f"[red]LỆCH: collection có {after:,} điểm nhưng chỉ có {len(chunks):,} mô tả.[/]")
        raise SystemExit(1)
    console.print("[green]Đối chiếu khớp.[/]")


if __name__ == "__main__":
    main()
