"""Bước 4: Dùng LLM trích xuất quan hệ và đắp lên đồ thị.

Đây là bước tốn thời gian nhất (544 chunk). Ba cơ chế bảo vệ mẻ chạy dài:

  --resume     Mỗi bộ ba trích được ghi ngay xuống data/processed/triples.jsonl. Nếu
               máy sập hoặc bạn bấm Ctrl+C ở chunk 400, chạy lại với --resume sẽ bỏ qua
               các chunk đã xong. Không có cờ này thì một lần treo máy = mất 3 tiếng.

  --limit N    Chạy thử N chunk trước để soi chất lượng, rồi mới chạy toàn bộ. Luôn làm
               bước này trước khi để máy chạy qua đêm.

  --load-only  Nạp file triples.jsonl có sẵn vào Neo4j mà không gọi lại LLM.

Chạy thử:   .venv/Scripts/python.exe scripts/04_build_knowledge_graph.py --limit 10
Chạy thật:  .venv/Scripts/python.exe scripts/04_build_knowledge_graph.py --resume
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config.settings import PROCESSED_DIR, settings
from src.graph.extractor import extract_from_chunk
from src.graph.selector import score_chunk, select_chunks
from src.graph.store import GraphStore
from src.ingest.pipeline import list_local_filings, process_filing

console = Console()
TRIPLES_PATH = PROCESSED_DIR / "triples.jsonl"

# ⚠️ File này tồn tại để sửa một lỗi thật trong cơ chế --resume.
#
# Bản đầu tiên xác định "chunk đã xong" bằng cách đọc chunk_id trong triples.jsonl.
# Nhưng chunk nào KHÔNG trích được quan hệ nào thì không ghi dòng nào — và trong thực tế
# đó là phần lớn số chunk. Hậu quả: mỗi lần chạy lại với --resume, toàn bộ chunk rỗng bị
# xử lý lại từ đầu. Không sai kết quả, nhưng lãng phí đúng phần đắt nhất của pipeline.
#
# Ghi riêng danh sách chunk ĐÃ THỬ, bất kể có kết quả hay không.
ATTEMPTED_PATH = PROCESSED_DIR / "attempted_chunks.txt"


def collect_graph_chunks():
    chunks = []
    for html in list_local_filings():
        _, _, _, graph_chunks = process_filing(html)
        chunks.extend(graph_chunks)
    return chunks


def load_done_chunk_ids() -> set:
    """Các chunk đã THỬ ở lần chạy trước — kể cả chunk không trích được gì.

    Đọc từ attempted_chunks.txt. Vẫn gộp thêm chunk_id trong triples.jsonl để tương thích
    ngược với dữ liệu sinh ra trước khi có file này.
    """
    done = set()

    if ATTEMPTED_PATH.exists():
        done.update(
            line.strip() for line in ATTEMPTED_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    if TRIPLES_PATH.exists():
        with TRIPLES_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["chunk_id"])
                except (json.JSONDecodeError, KeyError):
                    continue

    return done


def read_all_triples() -> list:
    if not TRIPLES_PATH.exists():
        return []
    rows = []
    with TRIPLES_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="chỉ xử lý N chunk đầu")
    ap.add_argument("--resume", action="store_true", help="bỏ qua chunk đã xong")
    ap.add_argument("--load-only", action="store_true", help="chỉ nạp file jsonl vào Neo4j")
    ap.add_argument("--model", default=None, help="ghi đè model trích xuất")
    ap.add_argument("--all", action="store_true",
                    help="bỏ bộ lọc, chạy toàn bộ chunk (chậm hơn ~6 lần)")
    ap.add_argument("--min-score", type=int, default=7,
                    help="ngưỡng điểm của bộ chọn chunk (xem src/graph/selector.py)")
    args = ap.parse_args()

    model = args.model or settings.llm_extraction_model

    if not args.load_only:
        all_chunks = collect_graph_chunks()

        if args.all:
            chunks = all_chunks
            console.print(f"[yellow]Chạy toàn bộ {len(chunks):,} chunk (không lọc).[/]")
        else:
            chunks = select_chunks(all_chunks, min_score=args.min_score)
            console.print(
                f"[cyan]Bộ lọc:[/] giữ {len(chunks):,}/{len(all_chunks):,} chunk "
                f"({len(chunks) / max(len(all_chunks), 1) * 100:.0f}%) — "
                f"chỉ những đoạn có nhắc tên tổ chức khác, xếp theo điểm giảm dần "
                f"nên chunk giá trị nhất chạy trước."
            )

        done = load_done_chunk_ids() if args.resume else set()

        # Không dùng --resume nghĩa là làm lại từ đầu -> xóa file cũ để tránh trộn dữ liệu
        if not args.resume:
            TRIPLES_PATH.unlink(missing_ok=True)
            ATTEMPTED_PATH.unlink(missing_ok=True)

        pending = [c for c in chunks if c.chunk_id not in done]
        if args.limit:
            pending = pending[: args.limit]

        console.print(
            f"[cyan]Model:[/] {model}   "
            f"[cyan]Chunk cần xử lý:[/] {len(pending)}/{len(chunks)}"
            + (f"  (bỏ qua {len(done)} chunk đã xong)" if done else "")
        )

        empty = 0
        started = time.time()

        with TRIPLES_PATH.open("a", encoding="utf-8") as out,              ATTEMPTED_PATH.open("a", encoding="utf-8") as attempted, Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Đang trích xuất", total=len(pending))
            written = 0

            for chunk in pending:
                try:
                    triples = extract_from_chunk(chunk, model=model)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]Lỗi ở {chunk.chunk_id}:[/] {str(exc)[:100]}")
                    progress.advance(task)
                    continue  # cố ý KHÔNG ghi vào attempted: lỗi thì nên thử lại

                attempted.write(f"{chunk.chunk_id}\n")
                attempted.flush()

                if not triples:
                    empty += 1
                for t in triples:
                    out.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")
                    written += 1
                out.flush()  # ghi ngay để Ctrl+C không mất dữ liệu
                progress.advance(task)

        elapsed = time.time() - started
        console.print(
            f"\n[green]Trích xuất xong.[/] {written} bộ ba từ {len(pending)} chunk "
            f"trong {elapsed / 60:.1f} phút. {empty} chunk không có quan hệ nào."
        )

    # --- Nạp vào Neo4j ---
    rows = read_all_triples()
    if not rows:
        console.print("[yellow]Chưa có bộ ba nào để nạp.[/]")
        return

    console.print(f"\n[cyan]Nạp {len(rows)} bộ ba vào Neo4j...[/]")
    store = GraphStore()
    store.init_schema()
    store.upsert_relations(rows)

    # --- Báo cáo ---
    rel_counts = Counter(r["relation"] for r in rows)
    table = Table(title="Quan hệ trích xuất được")
    table.add_column("Loại quan hệ")
    table.add_column("Số lượng", justify="right")
    for rel, n in rel_counts.most_common():
        table.add_row(rel, str(n))
    console.print(table)

    stats = store.stats()
    console.print("\n[bold]Đồ thị sau khi nạp:[/]")
    for row in stats["nodes"]:
        console.print(f"   {row['label']:<14} {row['n']:>6} node")
    for row in stats["relationships"]:
        console.print(f"   [dim]-[{row['type']}]->[/] {row['n']:>6}")

    store.close()


if __name__ == "__main__":
    main()
