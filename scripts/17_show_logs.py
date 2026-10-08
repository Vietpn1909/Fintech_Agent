"""Bước 17: Đọc nhật ký — để log là thứ dùng được chứ không phải tệp nằm đó.

Nhật ký ghi ra JSON Lines (`logs/app.jsonl`) nên tra được bằng `jq`, nhưng không phải ai
cũng có `jq` trên Windows và không ai nhớ được tên trường. Script này trả lời sẵn những
câu mà người vận hành sẽ hỏi khi có sự cố.

    --tail 20           hai mươi lượt hỏi–đáp gần nhất
    --errors            chỉ những lượt có lỗi hoặc có số không truy được về nguồn
    --trace <mã>        mọi dòng của CÙNG một câu hỏi, theo đúng thứ tự
    --tools             thống kê trạng thái từng công cụ — chỗ lỗi im lặng hiện ra
    --since 2h          chỉ tính trong 2 giờ gần nhất (h = giờ, d = ngày)

⚠️ VÌ SAO `--tools` LÀ MỤC ĐÁNG XEM NHẤT

Những lỗi nặng nhất của dự án này đều không ném ngoại lệ. Chúng hiện ra dưới dạng trạng
thái công cụ: `ambiguous` nhiều bất thường nghĩa là người dùng đang gõ tên mà hệ thống
không phân giải nổi; `company_not_found` tăng đột ngột nghĩa là một nhánh dữ liệu vừa
hỏng. Không đếm thì không thấy, vì từng lần riêng lẻ trông hoàn toàn bình thường.

Chạy:  .venv/Scripts/python.exe scripts/17_show_logs.py --tail 10
       .venv/Scripts/python.exe scripts/17_show_logs.py --tools --since 1d
"""

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.obs.logs import LOG_DIR

console = Console()


def load(since_seconds: float = 0.0):
    """Đọc mọi dòng, kể cả các tệp đã xoay vòng (app.jsonl.1, .2…), theo thứ tự thời gian."""
    files = sorted(LOG_DIR.glob("app.jsonl*"), reverse=True)
    rows = []
    cutoff = time.time() - since_seconds if since_seconds else 0
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # Dòng hỏng (ghi dở lúc tắt máy) thì bỏ qua, đừng làm sập công cụ đọc log.
                continue
            if cutoff:
                stamp = time.mktime(time.strptime(row.get("ts", ""), "%Y-%m-%dT%H:%M:%S"))
                if stamp < cutoff:
                    continue
            rows.append(row)
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


def parse_since(raw: str) -> float:
    if not raw:
        return 0.0
    m = re.fullmatch(r"(\d+)\s*([hdm])", raw.strip().lower())
    if not m:
        raise SystemExit("--since nhận dạng như 30m, 2h, 7d")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 60, "h": 3600, "d": 86400}[unit]


def show_turns(rows, limit: int, errors_only: bool) -> None:
    turns = [r for r in rows if r.get("event") == "agent.turn"]
    if errors_only:
        turns = [t for t in turns
                 if t.get("numbers_unverified") or "error" in t
                 or any(s in ("error", "bad_arguments") for s in (t.get("tool_status") or []))]
    turns = turns[-limit:]
    if not turns:
        console.print("[dim]Không có lượt nào khớp.[/]")
        return

    table = Table(title=f"{len(turns)} lượt hỏi–đáp" + (" CÓ VẤN ĐỀ" if errors_only else ""))
    for col in ("Thời điểm", "Mã vết", "Nguồn", "Câu hỏi", "Giây", "Công cụ", "Số chưa xác minh"):
        table.add_column(col, overflow="fold")
    for t in turns:
        bad = t.get("numbers_unverified") or []
        table.add_row(
            (t.get("ts") or "")[5:19].replace("T", " "),
            t.get("trace_id") or "",
            t.get("source") or "",
            (t.get("question") or "")[:52],
            f"{t.get('seconds', 0):.0f}",
            ", ".join(x for x in (t.get("tools") or []) if x)[:34],
            f"[red]{', '.join(bad)[:26]}[/]" if bad else "—",
        )
    console.print(table)


def show_trace(rows, trace_id: str) -> None:
    lines = [r for r in rows if r.get("trace_id") == trace_id]
    if not lines:
        console.print(f"[yellow]Không có dòng nào mang mã vết {trace_id}.[/]")
        return
    console.print(f"[cyan]{len(lines)} dòng của mã vết {trace_id}[/]\n")
    for r in lines:
        head = f"[dim]{(r.get('ts') or '')[11:19]}[/] {r.get('event'):<14}"
        rest = {k: v for k, v in r.items()
                if k not in ("ts", "event", "logger", "level", "trace_id") and v not in (None, [], {})}
        console.print(f"  {head} {json.dumps(rest, ensure_ascii=False)[:190]}")


def show_tools(rows) -> None:
    calls = [r for r in rows if r.get("event") == "tool.call"]
    if not calls:
        console.print("[dim]Chưa có lần gọi công cụ nào được ghi.[/]")
        return

    by_tool = defaultdict(Counter)
    times = defaultdict(list)
    for c in calls:
        by_tool[c.get("tool") or "?"][c.get("status") or "?"] += 1
        times[c.get("tool") or "?"].append(c.get("seconds") or 0)

    table = Table(title=f"{len(calls)} lần gọi công cụ")
    for col in ("Công cụ", "Lần", "Giây TB", "Trạng thái"):
        table.add_column(col, overflow="fold", justify="right" if col != "Công cụ" else "left")
    for tool, statuses in sorted(by_tool.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(statuses.values())
        # Trạng thái KHÁC "ok" mới là thứ cần nhìn — tô đỏ để không trôi qua mắt.
        parts = []
        for st, n in statuses.most_common():
            parts.append(f"{st}={n}" if st == "ok" else f"[red]{st}={n}[/]")
        table.add_row(tool, str(total),
                      f"{sum(times[tool]) / max(len(times[tool]), 1):.1f}",
                      "  ".join(parts))
    console.print(table)


def main() -> None:
    ap = argparse.ArgumentParser(description="Doc nhat ky he thong")
    ap.add_argument("--tail", type=int, default=15, help="so luot gan nhat")
    ap.add_argument("--errors", action="store_true", help="chi luot co van de")
    ap.add_argument("--trace", default=None, help="moi dong cua mot ma vet")
    ap.add_argument("--tools", action="store_true", help="thong ke trang thai cong cu")
    ap.add_argument("--since", default="", help="vd 30m, 2h, 7d")
    args = ap.parse_args()

    rows = load(parse_since(args.since))
    if not rows:
        console.print(f"[yellow]Chưa có nhật ký nào ở {LOG_DIR}.[/] "
                      f"Hỏi vài câu rồi chạy lại.")
        return

    span = f"{rows[0].get('ts', '')[5:16]} → {rows[-1].get('ts', '')[5:16]}"
    console.print(f"[dim]{len(rows):,} dòng · {span}[/]\n")

    if args.trace:
        show_trace(rows, args.trace)
    elif args.tools:
        show_tools(rows)
    else:
        show_turns(rows, args.tail, args.errors)


if __name__ == "__main__":
    main()
