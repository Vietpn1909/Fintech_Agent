"""Bước 20: Theo dõi doanh nghiệp và báo những gì đã đổi trong kho dữ liệu.

    .venv/Scripts/python.exe scripts/20_watch.py --add FPT --add "Hòa Phát"
    .venv/Scripts/python.exe scripts/20_watch.py --list
    .venv/Scripts/python.exe scripts/20_watch.py --check
    .venv/Scripts/python.exe scripts/20_watch.py --history --days 7

⚠️ Lần `--check` đầu tiên của một mã chỉ lập ảnh nền, không báo gì. Đó là chủ ý — xem
chú thích đầu `src/agent/watch.py`.

Lệnh `--check` được gọi tự động ở cuối mỗi lần chạy `scripts/18_refresh.py`, nên bình
thường không cần gõ tay. Gõ tay khi muốn kiểm tra ngay sau một lần nạp thủ công.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent import watch
from src.obs import logs

console = Console()

# Màu theo mức độ. "cao" là loại mà bỏ qua sẽ khiến câu trả lời của agent sai — số cũ bị
# sửa, cổ đông lớn đổi — nên nó phải nổi bật hẳn so với phần còn lại.
_COLOR = {"cao": "red", "vua": "yellow", "thap": "dim"}


def show_changes(ticker: str, company: str, changes: list) -> None:
    console.print(f"\n[bold]{ticker}[/] — {company}")
    if not changes:
        console.print("  [dim]không có thay đổi[/]")
        return
    for c in changes:
        color = _COLOR.get(c["severity"], "white")
        console.print(f"  [{color}]● {c['title']}[/]")
        if c["detail"]:
            console.print(f"    [dim]{c['detail']}[/]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Theo doi va canh bao thay doi du lieu")
    ap.add_argument("--add", action="append", default=[], help="them mot doanh nghiep")
    ap.add_argument("--remove", action="append", default=[], help="bo theo doi mot ma")
    ap.add_argument("--list", action="store_true", help="xem danh sach dang theo doi")
    ap.add_argument("--check", action="store_true", help="kiem tra toan bo danh sach")
    ap.add_argument("--only", default=None, help="chi kiem tra mot ma")
    ap.add_argument("--dry-run", action="store_true",
                    help="khong luu anh nen moi (xem lai cung mot khac biet nhieu lan)")
    ap.add_argument("--history", action="store_true", help="xem canh bao da ghi")
    ap.add_argument("--days", type=float, default=30.0, help="so ngay cho --history")
    args = ap.parse_args()

    if not any([args.add, args.remove, args.list, args.check, args.only, args.history]):
        ap.print_help()
        return

    for name in args.add:
        result = watch.add(name)
        if result.get("status") != "ok":
            # Không tự chọn khi tên mơ hồ — đúng nguyên tắc của cả hệ thống.
            console.print(f"[red]Không thêm được[/] {name}: {result.get('status')}")
            for opt in result.get("options", []):
                console.print(f"    [dim]{opt['ticker']} — {opt['company']}[/]")
            continue
        state = "đã có sẵn" if result["already_watched"] else "đã thêm"
        console.print(f"[green]{state}[/] {result['ticker']} — {result['company']}")

    for ticker in args.remove:
        result = watch.remove(ticker)
        console.print(f"[green]Đã bỏ[/] {ticker}" if result["removed"]
                      else f"[dim]{ticker} không có trong danh sách[/]")

    if args.list:
        rows = watch.watchlist()
        if not rows:
            console.print("[dim]Danh sách theo dõi đang trống. Thêm bằng --add[/]")
        else:
            table = Table(title=f"Đang theo dõi {len(rows)} doanh nghiệp")
            table.add_column("Mã"); table.add_column("Tên"); table.add_column("Kiểm tra lần cuối")
            for r in rows:
                import time as _t
                last = (_t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["last_checked"]))
                        if r["last_checked"] else "chưa lần nào")
                table.add_row(r["ticker"], r["company"], last)
            console.print(table)

    if args.only:
        result = watch.check(args.only, save=not args.dry_run)
        if result["status"] == "baseline":
            console.print(f"[yellow]{result['ticker']}[/]: {result['note']}")
        else:
            show_changes(result["ticker"], "", result["changes"])

    if args.check:
        summary = watch.check_all(save=not args.dry_run)
        for result in summary["results"]:
            if result["status"] == "baseline":
                console.print(f"\n[yellow]{result['ticker']}[/] — {result['company']}"
                              f"\n  [dim]{result['note']}[/]")
            else:
                show_changes(result["ticker"], result["company"], result["changes"])
        for fail in summary["failures"]:
            console.print(f"\n[red]{fail['ticker']} lỗi:[/] {fail['error']}")
        console.print(f"\n[bold]{summary['checked']} mã · "
                      f"{summary['changes_total']} thay đổi[/]")
        logs.log_ingest("watch_check", "ok", checked=summary["checked"],
                        changes=summary["changes_total"], failures=len(summary["failures"]))

    if args.history:
        alerts = watch.recent_alerts(ticker=args.only, days=args.days)
        if not alerts:
            console.print(f"[dim]Không có cảnh báo nào trong {args.days:g} ngày qua[/]")
            return
        import time as _t
        table = Table(title=f"{len(alerts)} cảnh báo trong {args.days:g} ngày qua")
        table.add_column("Khi"); table.add_column("Mã"); table.add_column("Mức")
        table.add_column("Nội dung", overflow="fold")
        for a in alerts:
            color = _COLOR.get(a["severity"], "white")
            table.add_row(_t.strftime("%m-%d %H:%M", _t.localtime(a["created_at"])),
                          a["ticker"], f"[{color}]{a['severity']}[/]", a["title"])
        console.print(table)


if __name__ == "__main__":
    main()
