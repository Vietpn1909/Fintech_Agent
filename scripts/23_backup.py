"""Bước 23: Sao lưu và khôi phục Neo4j + Qdrant.

    .venv/Scripts/python.exe scripts/23_backup.py --backup
    .venv/Scripts/python.exe scripts/23_backup.py --list
    .venv/Scripts/python.exe scripts/23_backup.py --check
    .venv/Scripts/python.exe scripts/23_backup.py --restore 2026-10-01_0920

⚠️ Thứ duy nhất trong dự án không dựng lại được từ mã nguồn là hơn tám giờ chạy nhúng
vector. Sao lưu thì mất chưa tới một phút — xem chú thích đầu `src/obs/backup.py`.

⚠️ `--backup` dừng Neo4j khoảng 20 giây. Qdrant không gián đoạn. Đừng chạy lúc đang hỏi
agent, nhưng cũng đừng vì thế mà không chạy.

⚠️ `--restore` GHI ĐÈ dữ liệu hiện tại và không hoàn tác được. Phải gõ đúng mốc thời
gian để xác nhận.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.obs import backup, logs

console = Console()


def show_list(dest: Path) -> None:
    items = backup.listing(dest)
    if not items:
        console.print(f"[dim]Chưa có bản sao lưu nào trong {dest}[/]")
        console.print("Tạo bản đầu tiên: [cyan]--backup[/]")
        return

    table = Table(title=f"{len(items)} bản sao lưu trong {dest}")
    for col in ("Mốc thời gian", "Dung lượng", "Doanh nghiệp", "Năm tài chính",
                "Đoạn vector", "Trạng thái"):
        table.add_column(col)
    for item in sorted(items, key=lambda b: b["stamp"], reverse=True):
        if item.get("broken"):
            table.add_row(item["stamp"], "—", "—", "—", "—", f"[red]{item['why']}[/]")
            continue
        counts = item.get("counts", {})
        chunks = sum(v for k, v in counts.items() if k.startswith("chunks_"))
        companies = (counts.get("companies_us", 0) or 0) + (counts.get("companies_vn", 0) or 0)
        table.add_row(
            item["stamp"],
            f"{item.get('total_bytes', 0) / 1e6:,.0f} MB",
            f"{companies:,}",
            f"{counts.get('financial_years', 0):,}",
            f"{chunks:,}",
            "[green]lành[/]",
        )
    console.print(table)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sao luu va khoi phuc Neo4j + Qdrant")
    ap.add_argument("--backup", action="store_true", help="tao mot ban sao luu moi")
    ap.add_argument("--list", action="store_true", help="liet ke cac ban dang co")
    ap.add_argument("--check", action="store_true", help="doi chieu tep voi manifest")
    ap.add_argument("--restore", default=None, metavar="MOC", help="khoi phuc mot ban")
    ap.add_argument("--yes", action="store_true",
                    help="bo qua buoc go xac nhan khi --restore (dung cho script)")
    ap.add_argument("--keep", type=int, default=7, help="so ban giu lai (mac dinh 7)")
    ap.add_argument("--dest", default=None, help="thu muc luu (mac dinh backups/)")
    args = ap.parse_args()

    dest = Path(args.dest) if args.dest else backup.DEFAULT_DEST

    if not any([args.backup, args.list, args.check, args.restore]):
        ap.print_help()
        return

    if args.backup:
        try:
            manifest = backup.create(dest=dest, keep=args.keep,
                                     log=lambda m: console.print(m))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Sao lưu HỎNG:[/] {exc}")
            logs.log_ingest("backup", "failed", error=str(exc)[:300])
            raise SystemExit(1)
        logs.log_ingest("backup", "ok", stamp=manifest["stamp"],
                        bytes=manifest["total_bytes"], seconds=manifest["seconds"])
        console.print()
        # ⚠️ Nhắc ngay tại đây, không giấu trong tài liệu. Bản sao lưu nằm cùng ổ đĩa với
        # dữ liệu gốc chỉ chống được xóa nhầm và hỏng dữ liệu, KHÔNG chống được hỏng ổ.
        console.print("[yellow]Lưu ý:[/] bản sao lưu đang nằm cùng ổ đĩa với dữ liệu gốc. "
                      "Nó chống được xóa nhầm và hỏng dữ liệu, nhưng [bold]không chống "
                      "được hỏng ổ đĩa[/]. Nên chép thư mục này sang ổ ngoài hoặc đám mây.")

    if args.list:
        show_list(dest)

    if args.check:
        items = [b for b in backup.listing(dest) if not b.get("broken")]
        if not items:
            console.print("[dim]Không có bản nào để kiểm.[/]")
        for item in items:
            problems = backup.check_files(item["path"])
            if problems:
                console.print(f"[red]HỎNG[/] {item['stamp']}")
                for p in problems:
                    console.print(f"   · {p}")
            else:
                console.print(f"[green]lành[/] {item['stamp']} · "
                              f"{item.get('total_bytes', 0) / 1e6:,.0f} MB")

    if args.restore:
        stamp = args.restore
        console.print(f"[red bold]Khôi phục {stamp} sẽ GHI ĐÈ toàn bộ dữ liệu hiện tại "
                      f"của Neo4j và Qdrant. Không hoàn tác được.[/]")
        if not args.yes:
            # Bắt gõ lại đúng mốc thời gian chứ không phải gõ "y". Gõ "y" là phản xạ;
            # gõ lại một chuỗi ngày giờ thì buộc phải đọc xem mình đang khôi phục bản nào.
            typed = input(f"Gõ lại chính xác '{stamp}' để xác nhận: ").strip()
            if typed != stamp:
                console.print("[yellow]Đã hủy — chuỗi xác nhận không khớp.[/]")
                raise SystemExit(1)

        try:
            result = backup.restore(stamp, dest=dest, log=lambda m: console.print(m))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Khôi phục HỎNG:[/] {exc}")
            logs.log_ingest("restore", "failed", stamp=stamp, error=str(exc)[:300])
            raise SystemExit(1)

        console.print(f"\n[cyan]Đối chiếu số đếm trước/sau[/] — đây mới là phép kiểm thật, "
                      f"không phải việc tệp có tồn tại hay không.")
        check = backup.verify_restore(result["expected_counts"])
        table = Table()
        for col in ("Chỉ tiêu", "Trước khi sao lưu", "Sau khi khôi phục", ""):
            table.add_column(col)
        for row in check["rows"]:
            mark = "[green]khớp[/]" if row["khop"] else "[red]LỆCH[/]"
            table.add_row(row["chi_tieu"], f"{row['truoc_khi_sao_luu']:,}"
                          if isinstance(row["truoc_khi_sao_luu"], int) else str(row["truoc_khi_sao_luu"]),
                          f"{row['sau_khi_khoi_phuc']:,}"
                          if isinstance(row["sau_khi_khoi_phuc"], int) else str(row["sau_khi_khoi_phuc"]),
                          mark)
        console.print(table)
        logs.log_ingest("restore", "ok" if check["ok"] else "mismatch", stamp=stamp,
                        seconds=result["seconds"])
        if not check["ok"]:
            console.print("[red]Số đếm sau khi khôi phục KHÔNG khớp với lúc chụp.[/] "
                          "Đừng coi lần khôi phục này là thành công.")
            raise SystemExit(1)
        console.print(f"[green]Khôi phục xong và đã kiểm chứng[/] · {result['seconds']} giây")


if __name__ == "__main__":
    main()
