"""Bước 12: Nạp tầng số liệu cho doanh nghiệp niêm yết tại Việt Nam.

ĐIỀU NÀY CHỨNG MINH ĐIỀU GÌ

Giới hạn được ghi thẳng trong README: hệ thống không có doanh nghiệp Việt Nam nào ngoài
VinFast, vì nó chỉ đọc hồ sơ nộp cho SEC. Script này cho thấy đó là giới hạn của NGUỒN
DỮ LIỆU chứ không phải của kiến trúc — tầng số liệu chỉ cần bốn thứ (doanh nghiệp, năm,
chỉ tiêu, giá trị kèm đồng tiền), và nguồn nào cấp đủ bốn thứ đó đều cắm vào được.

Sau khi chạy, cùng bộ công cụ đó trả lời được "doanh thu FPT năm 2024" mà không phải sửa
một dòng nào trong agent.

VÌ SAO KHÔNG CÒN VIẾT TAY DANH SÁCH VN30

Bản đầu tiên có đúng 30 mã viết cứng trong file này. Nó chứng minh được luận điểm nhưng
lại tạo ra một hiểu nhầm: người dùng tưởng nguồn dữ liệu chỉ có 30 doanh nghiệp. Thực ra
VCI có endpoint trả về TOÀN BỘ vũ trụ trong một lần gọi — 1.905 mã, lọc còn 1.586 mã đang
niêm yết trên ba sàn HSX/HNX/UPCOM. Thử 10 mã ngoài VN30 (REE, PNJ, DHG, HSG…) thì cả 10
đều có đủ 8 năm số liệu.

Nói cách khác, con số 30 là giới hạn của DANH SÁCH VIẾT TAY, không phải của nguồn. Nên
danh sách đó bị bỏ đi.

BA CƠ CHẾ BẢO VỆ MẺ CHẠY DÀI (cùng khuôn với script 04)

  --resume      Mỗi mã lấy xong được ghi ngay xuống data/processed/vn_metrics.jsonl.
                1.586 mã × 4 lần gọi API × 0,35 giây giãn nhịp ≈ 37 phút. Không có cờ này
                thì một lần mất mạng ở mã thứ 1.200 là mất sạch.
  --limit N     Chạy thử N mã trước để soi chất lượng.
  --load-only   Nạp file jsonl có sẵn vào Neo4j mà không gọi lại API.

PHẠM VI: CHỈ TẦNG SỐ LIỆU

Không làm tầng văn bản và tầng đồ thị cho Việt Nam. Lý do đầy đủ kèm số đo nằm ở
`docs/nguon_du_lieu_viet_nam.md`; tóm tắt: không cổng công bố thông tin nào của Việt Nam
có API công khai (HOSE, UBCKNN, VietStock đều là ứng dụng dựng bằng JavaScript), và model
nhúng đang dùng (`bge-small-en-v1.5`) chỉ hiểu tiếng Anh.

⚠️ KHÔNG QUY ĐỔI TỶ GIÁ. Số liệu Việt Nam lưu bằng VND, và `compare_financials` sẽ TỪ
CHỐI xếp hạng chung với số USD thay vì đoán bừa. Đây là hành vi đúng, không phải thiếu
sót — xem chú thích trong công cụ đó.

Chạy thử:  .venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py --limit 20
Chạy thật: .venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py --resume --apply
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config.settings import PROCESSED_DIR, settings  # noqa: F401 — chỉnh stdout sang UTF-8
from src.graph.store import GraphStore
from src.ingest.vietnam import LISTED_EXCHANGES, fetch_universe, fetch_year_rows

console = Console()

CACHE_PATH = PROCESSED_DIR / "vn_metrics.jsonl"

# Mã ĐÃ THỬ, kể cả mã không có số liệu nào.
#
# Cùng lỗi mà script 04 đã sửa: nếu xác định "đã xong" bằng cách đọc mã trong file kết
# quả, thì mọi mã không có dữ liệu sẽ bị thử lại từ đầu ở mỗi lần --resume. Trên UPCOM
# tỷ lệ mã rỗng không nhỏ, nên đây không phải chuyện nhỏ.
ATTEMPTED_PATH = PROCESSED_DIR / "vn_attempted.txt"


def load_attempted() -> set:
    if not ATTEMPTED_PATH.exists():
        return set()
    return {
        line.strip()
        for line in ATTEMPTED_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def read_cache() -> list:
    """Các dòng năm tài chính đã lấy được ở những lần chạy trước."""
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
    ap = argparse.ArgumentParser(description="Nap so lieu doanh nghiep Viet Nam")
    ap.add_argument("--apply", action="store_true", help="ghi vao Neo4j (mac dinh chi chay thu)")
    ap.add_argument("--limit", type=int, default=None, help="chi lay N ma dau tien")
    ap.add_argument("--symbols", default=None, help="danh sach ma, cach nhau bang dau phay")
    ap.add_argument("--resume", action="store_true", help="bo qua ma da lay o lan truoc")
    ap.add_argument("--load-only", action="store_true", help="chi nap file jsonl vao Neo4j")
    ap.add_argument("--exchanges", default=",".join(LISTED_EXCHANGES),
                    help=f"san giao dich, mac dinh {'/'.join(LISTED_EXCHANGES)}")
    args = ap.parse_args()

    exchanges = tuple(x.strip().upper() for x in args.exchanges.split(",") if x.strip())

    # --- Vũ trụ ---
    console.print("[cyan]Lấy danh sách doanh nghiệp niêm yết từ VCI...[/]")
    universe = fetch_universe(exchanges=exchanges)
    by_symbol = {u["symbol"]: u for u in universe}
    console.print(
        f"[green]{len(universe):,} mã[/] trên "
        + " · ".join(f"{ex} {sum(1 for u in universe if u['exchange'] == ex)}"
                     for ex in exchanges)
    )

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        # Mã gõ tay có thể không nằm trong vũ trụ (gõ nhầm, hoặc mã OTC). Báo ra chứ
        # không lặng lẽ bỏ qua — im lặng ở đây trông y hệt như "mã đó không có số liệu".
        unknown = [s for s in symbols if s not in by_symbol]
        if unknown:
            console.print(f"[yellow]Không có trong vũ trụ niêm yết:[/] {', '.join(unknown)}")
    else:
        symbols = [u["symbol"] for u in universe]

    if args.limit:
        symbols = symbols[: args.limit]

    # --- Lấy số liệu ---
    if not args.load_only:
        done = load_attempted() if args.resume else set()
        if not args.resume:
            CACHE_PATH.unlink(missing_ok=True)
            ATTEMPTED_PATH.unlink(missing_ok=True)

        pending = [s for s in symbols if s not in done]
        console.print(
            f"\n[cyan]Cần lấy:[/] {len(pending):,}/{len(symbols):,} mã"
            + (f"  (bỏ qua {len(done):,} mã đã thử)" if done else "")
            + f"  ·  ước tính {len(pending) * 1.45 / 60:.0f} phút"
        )

        failed, empty = [], 0
        started = time.time()

        with CACHE_PATH.open("a", encoding="utf-8") as out, \
             ATTEMPTED_PATH.open("a", encoding="utf-8") as attempted, Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
             ) as progress:
            task = progress.add_task("Lấy số liệu", total=len(pending))

            for symbol in pending:
                info = by_symbol.get(symbol, {})
                try:
                    rows = fetch_year_rows(symbol, company_name=info.get("name"))
                except Exception as exc:  # noqa: BLE001
                    # Cố ý KHÔNG ghi vào attempted: lỗi mạng thì nên thử lại lần sau.
                    failed.append((symbol, f"{type(exc).__name__}: {str(exc)[:70]}"))
                    progress.advance(task)
                    continue

                attempted.write(f"{symbol}\n")
                attempted.flush()

                if not rows:
                    empty += 1
                for row in rows:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()  # ghi ngay để Ctrl+C không mất dữ liệu
                progress.advance(task)

        console.print(
            f"\n[green]Lấy xong[/] trong {(time.time() - started) / 60:.1f} phút · "
            f"{empty} mã không có số liệu năm nào · {len(failed)} mã lỗi"
        )
        if failed:
            console.print("[yellow]Lỗi (chạy lại với --resume để thử tiếp):[/]")
            for symbol, why in failed[:15]:
                console.print(f"  {symbol:6} {why}")
            if len(failed) > 15:
                console.print(f"  [dim]… và {len(failed) - 15} mã nữa[/]")

    # --- Tổng hợp từ file ---
    all_rows = read_cache()
    if not all_rows:
        console.print("[yellow]Chưa có số liệu nào để nạp.[/]")
        return

    got = sorted({r["ticker"].removesuffix(".VN") for r in all_rows})
    companies = [
        {
            "ticker": f"{s}.VN",
            "symbol": s,
            "name": by_symbol.get(s, {}).get("name") or s,
            # Sàn thật, không phải "HOSE" cho tất cả. Ở mức 30 mã VN30 thì gán cứng HOSE
            # tình cờ đúng; ở mức 1.586 mã thì 855 mã UPCOM và 301 mã HNX sẽ bị ghi sai.
            "exchange": by_symbol.get(s, {}).get("exchange") or "HOSE",
            "sector": by_symbol.get(s, {}).get("sector") or None,
        }
        for s in got
    ]

    basis = Counter(r.get("revenue_basis") or "doanh thu bán hàng thông thường" for r in all_rows)
    years = Counter(r["fiscal_year"] for r in all_rows)

    table = Table(title="Tổng kết")
    table.add_column("Chỉ số"); table.add_column("Giá trị", justify="right")
    table.add_row("Doanh nghiệp có số liệu", f"{len(companies):,}")
    table.add_row("Bản ghi năm tài chính", f"{len(all_rows):,}")
    table.add_row("Khoảng năm", f"{min(years)}–{max(years)}")
    table.add_row("Có doanh thu", f"{sum(1 for r in all_rows if r.get('revenue')):,}")
    console.print(); console.print(table)

    console.print("\n[bold]Cơ sở tính doanh thu[/] (ghi nhãn để không trộn khái niệm):")
    for note, n in basis.most_common():
        console.print(f"   {n:>6,}  {note[:78]}")

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi vào Neo4j.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    # --- Ghi vào Neo4j ---
    store = GraphStore()
    before_all = store.run("MATCH (c:Company) RETURN count(*) AS n")[0]["n"]
    before_vn = store.run("MATCH (c:Company {market:'VN'}) RETURN count(*) AS n")[0]["n"]

    store.upsert_vn_companies(companies)
    written = store.upsert_financial_years(all_rows)

    after_all = store.run("MATCH (c:Company) RETURN count(*) AS n")[0]["n"]
    after_vn = store.run("MATCH (c:Company {market:'VN'}) RETURN count(*) AS n")[0]["n"]
    vn_years = store.run(
        "MATCH (:Company {market:'VN'})-[:HAS_FINANCIALS]->(fy) RETURN count(fy) AS n"
    )[0]["n"]

    console.print(f"\nĐã ghi {written:,} bản ghi năm tài chính.")
    console.print(f"Doanh nghiệp: {before_all:,} → {after_all:,} (thêm {after_all - before_all:,})")
    console.print(f"Thị trường VN: {before_vn:,} → [bold]{after_vn:,}[/] · {vn_years:,} bản ghi năm")

    # ĐỐI CHIẾU. Số node mới KHÔNG được vượt số doanh nghiệp vừa lấy — vượt nghĩa là
    # bước ghi đã tạo ra node ngoài dự tính, và đó đúng là lỗi `MERGE (c {cik: null})`
    # từng dồn toàn bộ doanh nghiệp Việt Nam vào một node duy nhất.
    if after_all - before_all > len(companies):
        console.print("[red]LỆCH: tạo ra nhiều node hơn số doanh nghiệp đã lấy.[/]")
        raise SystemExit(1)
    if after_vn != len(companies):
        console.print(
            f"[red]LỆCH: có {after_vn:,} node market='VN' nhưng chỉ nạp {len(companies):,} "
            f"doanh nghiệp.[/]"
        )
        raise SystemExit(1)
    console.print("[green]Đối chiếu khớp.[/]")

    store.close()


if __name__ == "__main__":
    main()
