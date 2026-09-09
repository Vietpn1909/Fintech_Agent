"""Bước 13: Nạp quan hệ SỞ HỮU cho doanh nghiệp Việt Nam — tầng đồ thị, không cần LLM.

VÌ SAO BƯỚC NÀY TỒN TẠI

Đồ thị phía Mỹ dựng bằng cách cho mô hình đọc từng đoạn 10-K rồi trích quan hệ: đắt,
chậm, và luôn có tỷ lệ sai. Phía Việt Nam không có văn bản để đọc — nhưng lại có thứ
phía Mỹ không cho sẵn: bảng cổ đông đã ở dạng có cấu trúc.

Đo trên rổ VN30 trước khi làm: 1.391 bản ghi cổ đông, 1.119 chủ sở hữu riêng biệt, và
85 chủ sở hữu nắm từ hai doanh nghiệp trở lên. Chính 85 cái tên đó là các CẠNH BẮC CẦU
— thứ làm cho câu hỏi nhiều bước có đường đi:

    State Capital Investment Corporation -> BID, BVH, FPT, MBB, SAB, VNM
    Norges Bank                          -> ACB, DGC, FPT, HPG, MWG, STB, VNM, VPB

Toàn bộ việc này không tốn một lần gọi LLM nào.

⚠️ SỞ HỮU KHÔNG PHẢI QUAN HỆ KINH DOANH — VÀ ĐÂY LÀ RỦI RO CHÍNH CỦA BƯỚC NÀY

Một quỹ ETF nắm cả FPT lẫn VNM KHÔNG có nghĩa hai doanh nghiệp đó làm ăn với nhau. Đó
chỉ là danh mục đầu tư. Nếu để lẫn với `PARTNERS_WITH` hay `SUPPLIED_BY`, agent sẽ dựng
ra những chuỗi suy luận nghe rất thuyết phục mà hoàn toàn vô nghĩa: "FPT liên quan tới
VNM qua quỹ Manulife". Ba lớp bảo vệ:

  1. Loại quan hệ riêng `OWNED_BY`, nằm trong `STRUCTURED_RELATIONS` chứ KHÔNG nằm trong
     bộ enum mà LLM được phép sinh ra khi trích xuất văn bản.
  2. Nhãn node riêng `:Person` / `:Organization`, không phải `:Company` — chủ sở hữu
     không niêm yết và không có số liệu, đếm họ vào "doanh nghiệp" là thổi phồng con số.
  3. Mỗi cạnh mang `percent` và `as_of` (ngày công bố). Tỷ lệ sở hữu thay đổi liên tục;
     một con số không kèm ngày là con số không kiểm chứng được.

NGƯỠNG 0,5%

Lấy hết thì ước tính ~59.000 cạnh, phần lớn là nhà đầu tư nhỏ lẻ được công bố lẻ tẻ —
nặng đồ thị mà không mở thêm đường đi nào. Từ 0,5% trở lên còn ~16.400 cạnh và giữ lại
toàn bộ cổ đông lớn, cổ đông nhà nước, quỹ ngoại và người sáng lập.

Chạy thử:  .venv/Scripts/python.exe scripts/13_load_vietnam_shareholders.py --limit 30
Chạy thật: .venv/Scripts/python.exe scripts/13_load_vietnam_shareholders.py --resume --apply
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config.settings import PROCESSED_DIR, settings  # noqa: F401 — chỉnh stdout sang UTF-8
from src.graph.store import GraphStore
from src.ingest.vietnam import fetch_shareholders

console = Console()

CACHE_PATH = PROCESSED_DIR / "vn_shareholders.jsonl"
ATTEMPTED_PATH = PROCESSED_DIR / "vn_shareholders_attempted.txt"


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
    ap = argparse.ArgumentParser(description="Nap quan he so huu doanh nghiep Viet Nam")
    ap.add_argument("--apply", action="store_true", help="ghi vao Neo4j (mac dinh chi chay thu)")
    ap.add_argument("--limit", type=int, default=None, help="chi lay N ma dau tien")
    ap.add_argument("--symbols", default=None, help="danh sach ma, cach nhau bang dau phay")
    ap.add_argument("--resume", action="store_true", help="bo qua ma da lay o lan truoc")
    ap.add_argument("--load-only", action="store_true", help="chi nap file jsonl vao Neo4j")
    ap.add_argument("--min-percent", type=float, default=0.5,
                    help="nguong ty le so huu, tinh theo PHAN TRAM (mac dinh 0.5)")
    args = ap.parse_args()

    min_pct = args.min_percent / 100.0

    # Chỉ lấy cổ đông cho doanh nghiệp ĐÃ CÓ trong đồ thị. Lấy cho mã chưa nạp số liệu là
    # vô nghĩa: `upsert_shareholders` MATCH theo ticker, không khớp thì cạnh rơi vào hư
    # không mà không báo lỗi gì.
    store = GraphStore()
    known = sorted({
        r["s"] for r in store.run(
            "MATCH (c:Company {market:'VN'}) WHERE c.symbol IS NOT NULL RETURN c.symbol AS s"
        )
    })
    console.print(f"[cyan]Doanh nghiệp Việt Nam đã có trong đồ thị:[/] {len(known):,}")
    if not known:
        console.print("[red]Chưa có doanh nghiệp Việt Nam nào.[/] Chạy scripts/12 trước.")
        raise SystemExit(1)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        missing = [s for s in symbols if s not in set(known)]
        if missing:
            console.print(f"[yellow]Chưa có số liệu, bỏ qua:[/] {', '.join(missing)}")
            symbols = [s for s in symbols if s not in set(missing)]
    else:
        symbols = known
    if args.limit:
        symbols = symbols[: args.limit]

    # --- Lấy dữ liệu ---
    if not args.load_only:
        done = load_attempted() if args.resume else set()
        if not args.resume:
            CACHE_PATH.unlink(missing_ok=True)
            ATTEMPTED_PATH.unlink(missing_ok=True)

        pending = [s for s in symbols if s not in done]
        console.print(
            f"[cyan]Cần lấy:[/] {len(pending):,}/{len(symbols):,} mã"
            + (f"  (bỏ qua {len(done):,} mã đã thử)" if done else "")
            + f"  ·  ngưỡng ≥{args.min_percent}%  ·  ước tính {len(pending) * 0.42 / 60:.0f} phút"
        )

        failed, empty = [], 0
        started = time.time()
        with CACHE_PATH.open("a", encoding="utf-8") as out, \
             ATTEMPTED_PATH.open("a", encoding="utf-8") as attempted, Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(), TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(), TimeRemainingColumn(), console=console,
             ) as progress:
            task = progress.add_task("Lấy cổ đông", total=len(pending))
            for symbol in pending:
                try:
                    rows = fetch_shareholders(symbol, min_percent=min_pct)
                except Exception as exc:  # noqa: BLE001
                    failed.append((symbol, f"{type(exc).__name__}: {str(exc)[:70]}"))
                    progress.advance(task)
                    continue
                attempted.write(f"{symbol}\n"); attempted.flush()
                if not rows:
                    empty += 1
                for row in rows:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                progress.advance(task)

        console.print(
            f"\n[green]Lấy xong[/] trong {(time.time() - started) / 60:.1f} phút · "
            f"{empty} mã không có cổ đông nào đạt ngưỡng · {len(failed)} mã lỗi"
        )
        for symbol, why in failed[:10]:
            console.print(f"  [yellow]{symbol}[/] {why}")

    # --- Tổng hợp ---
    rows = read_cache()
    if not rows:
        console.print("[yellow]Chưa có dữ liệu cổ đông nào.[/]")
        return

    owners = defaultdict(set)
    for r in rows:
        owners[r["owner"]].add(r["symbol"])
    bridging = {k: v for k, v in owners.items() if len(v) > 1}

    # Đếm theo CHỦ SỞ HỮU RIÊNG BIỆT, không phải theo dòng. Đếm theo dòng thì một quỹ nắm
    # 19 doanh nghiệp được tính 19 lần, và tổng hai nhóm sẽ vượt quá số chủ sở hữu mà nó
    # đang được xếp bên dưới — đúng kiểu cộng gộp hai đại lượng khác nhau vào một cột.
    owner_label = {}
    for r in rows:
        owner_label.setdefault(r["owner"], r["owner_label"])
    labels = Counter(owner_label.values())

    table = Table(title="Tổng kết")
    table.add_column("Chỉ số"); table.add_column("Giá trị", justify="right")
    table.add_row("Doanh nghiệp có cổ đông", f"{len({r['symbol'] for r in rows}):,}")
    table.add_row("Cạnh sở hữu", f"{len(rows):,}")
    table.add_row("Chủ sở hữu riêng biệt", f"{len(owners):,}")
    table.add_row("  · tổ chức", f"{labels.get('Organization', 0):,}")
    table.add_row("  · cá nhân", f"{labels.get('Person', 0):,}")
    table.add_row("Chủ sở hữu BẮC CẦU (≥2 DN)", f"{len(bridging):,}")
    console.print(); console.print(table)

    # Chỉ TỔ CHỨC mới bắc cầu được. Cá nhân đã bị gắn kèm mã doanh nghiệp vào tên nên
    # mỗi người chỉ thuộc đúng một doanh nghiệp — xem chú thích dài trong fetch_shareholders.
    org_bridge = {k: v for k, v in bridging.items() if owner_label.get(k) == "Organization"}
    console.print("\n[bold]Bắc cầu nhiều nhất[/] — đây là thứ mở ra câu hỏi nhiều bước:")
    for name, syms in sorted(org_bridge.items(), key=lambda kv: -len(kv[1]))[:12]:
        console.print(f"   {name[:44]:<46} {len(syms):>3} DN: {', '.join(sorted(syms)[:9])}"
                      + (" …" if len(syms) > 9 else ""))

    person_bridge = len(bridging) - len(org_bridge)
    if person_bridge:
        console.print(
            f"\n[red]LỖI THIẾT KẾ:[/] còn {person_bridge} cá nhân bắc cầu nhiều doanh "
            f"nghiệp. Tên cá nhân lẽ ra phải được gắn kèm mã doanh nghiệp."
        )
        raise SystemExit(1)
    console.print(
        "\n[dim]Cá nhân KHÔNG bắc cầu, và đó là cố ý. Nguồn không cấp mã định danh cho chủ\n"
        "sở hữu nên node phải gộp theo tên; với tên Việt Nam thì gộp theo tên là sai:\n"
        "đo được 121 trường hợp cùng tên tiếng Anh mà khác tên tiếng Việt — Nguyen Van\n"
        "Thanh gộp làm một từ Nguyễn Văn Thành, Nguyễn Văn Thạnh và Nguyễn Văn Thanh.\n"
        "Giữ nguyên thì đồ thị dựng ra 770 đường đi bịa giữa các doanh nghiệp.[/]"
    )

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi vào Neo4j.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    # --- Ghi vào Neo4j ---
    def counts() -> dict:
        return {
            "companies": store.run("MATCH (c:Company) RETURN count(*) AS n")[0]["n"],
            "owned_by": store.run("MATCH ()-[r:OWNED_BY]->() RETURN count(r) AS n")[0]["n"],
            "persons": store.run("MATCH (p:Person) RETURN count(*) AS n")[0]["n"],
            "orgs": store.run("MATCH (o:Organization) RETURN count(*) AS n")[0]["n"],
        }

    before = counts()
    console.print(f"\n[bold]Trước:[/] {before}")

    # ⚠️ XÓA KẾT QUẢ LẦN TRƯỚC RỒI DỰNG LẠI, KHÔNG ĐẮP CHỒNG.
    #
    # Cạnh sở hữu là ẢNH CHỤP tại một thời điểm, không phải dữ liệu cộng dồn. Một quỹ bán
    # hết cổ phần thì lần công bố sau đơn giản là không còn tên trong danh sách — MERGE
    # không bao giờ xóa được cạnh cũ, nên đắp chồng sẽ giữ mãi những cổ đông đã thoái vốn
    # từ lâu và không có gì báo ra.
    #
    # Chỉ xóa những gì bước này tạo (source='VCI'). Node :Person do LLM trích từ hồ sơ 10-K
    # phải giữ nguyên — đã có 68 node như vậy trước khi có bước này.
    wiped_edges = store.run(
        "MATCH ()-[r:OWNED_BY {source:'VCI'}]->() WITH r LIMIT 200000 "
        "DELETE r RETURN count(r) AS n"
    )
    wiped_nodes = store.run(
        "MATCH (n) WHERE (n:Person OR n:Organization) AND n.source = 'VCI' "
        "AND NOT (n)--() DETACH DELETE n RETURN count(n) AS n"
    )
    if (wiped_edges and wiped_edges[0]["n"]) or (wiped_nodes and wiped_nodes[0]["n"]):
        console.print(
            f"[dim]Dọn lần nạp trước: bỏ {wiped_edges[0]['n']:,} cạnh · "
            f"{wiped_nodes[0]['n']:,} node chủ sở hữu không còn cạnh nào[/]"
        )

    store.init_schema()
    store.upsert_shareholders(rows)
    upgraded, stale = store.sync_graph_tier()

    after = counts()
    console.print(f"[bold]Sau:[/]   {after}")

    # ĐỐI CHIẾU. Số node Company phải KHÔNG ĐỔI — bước này chỉ được tạo Person và
    # Organization. Nếu nó tăng nghĩa là chủ sở hữu đã lọt vào nhãn Company, và mọi con
    # số "bao nhiêu doanh nghiệp" trên giao diện lập tức sai.
    if after["companies"] != before["companies"]:
        console.print(
            f"[red]LỆCH: số node Company đổi từ {before['companies']:,} sang "
            f"{after['companies']:,}. Chủ sở hữu lẽ ra phải là :Person/:Organization.[/]"
        )
        raise SystemExit(1)

    # Cạnh sở hữu không được nhiều hơn số dòng đã nạp. Ít hơn là bình thường: cùng một
    # chủ sở hữu có thể xuất hiện hai dòng cho cùng một doanh nghiệp (công bố hai đợt),
    # và MERGE gộp chúng làm một cạnh.
    if after["owned_by"] > len(rows):
        console.print(f"[red]LỆCH: {after['owned_by']:,} cạnh OWNED_BY > {len(rows):,} dòng nạp.[/]")
        raise SystemExit(1)

    console.print(
        f"\n[green]Đối chiếu khớp.[/] Company giữ nguyên {after['companies']:,} · "
        f"thêm {after['owned_by'] - before['owned_by']:,} cạnh sở hữu · "
        f"{after['persons'] - before['persons']:,} cá nhân · "
        f"{after['orgs'] - before['orgs']:,} tổ chức"
    )
    if upgraded:
        console.print(f"Mức phủ: nâng {upgraded:,} doanh nghiệp lên 'graph' (nay có cạnh sở hữu).")
    if stale:
        console.print(f"[yellow]{stale} doanh nghiệp mang nhãn 'graph' mà không còn cạnh nào.[/]")

    store.close()


if __name__ == "__main__":
    main()
