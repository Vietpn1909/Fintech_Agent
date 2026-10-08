"""Bước 19: Sinh hồ sơ phân tích tự động cho một doanh nghiệp.

Khác với hỏi–đáp: người dùng không phải biết trước phải hỏi gì. Đưa một cái tên, agent tự
chạy sáu bước thu thập rồi dựng thành hồ sơ có dẫn nguồn từng dòng.

    .venv/Scripts/python.exe scripts/19_company_brief.py FPT
    .venv/Scripts/python.exe scripts/19_company_brief.py "Hòa Phát" --out ho-so.md
    .venv/Scripts/python.exe scripts/19_company_brief.py NVDA --no-narrate   # thuần dữ liệu

`--no-narrate` bỏ hẳn phần LLM viết lời: nhanh hơn mười lần và hoàn toàn xác định, dùng
khi chỉ cần bảng số và trích dẫn.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent.brief import collect
from src.agent.brief_render import render
from src.obs import logs

console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(description="Sinh ho so phan tich doanh nghiep")
    ap.add_argument("company", help="ten hoac ma doanh nghiep")
    ap.add_argument("--years", type=int, default=5, help="so nam so lieu (mac dinh 5)")
    ap.add_argument("--no-narrate", action="store_true", help="bo phan LLM viet loi")
    ap.add_argument("--out", default=None, help="ghi ra tep markdown")
    args = ap.parse_args()

    started = time.time()
    brief = collect(args.company, years=args.years)
    markdown = render(brief, narrate=not args.no_narrate)
    elapsed = round(time.time() - started, 1)

    logs.log_ingest("company_brief", brief.get("status", "?"),
                    company=args.company, ticker=brief.get("ticker"),
                    seconds=elapsed, narrated=not args.no_narrate)

    if args.out:
        path = Path(args.out)
        path.write_text(markdown, encoding="utf-8")
        console.print(f"[green]Đã ghi[/] {path} · {len(markdown):,} ký tự · {elapsed} giây")
    else:
        # In thô, không qua rich markup: hồ sơ có dấu ngoặc vuông trong trích dẫn nguồn
        # và rich sẽ hiểu nhầm chúng là thẻ định dạng rồi nuốt mất.
        print(markdown)
        console.print(f"\n[dim]{elapsed} giây[/]")

    if brief.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
