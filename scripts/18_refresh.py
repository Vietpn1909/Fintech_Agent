"""Bước 18: Cập nhật dữ liệu định kỳ — một lệnh duy nhất, tự biết cái gì đã cũ.

VÌ SAO CẦN

Mười bảy script trước đều chạy tay, và không chỗ nào trả lời câu "doanh nghiệp vừa công
bố báo cáo quý mới thì làm sao hệ thống biết". Với dữ liệu tài chính thì đó không phải
tiện nghi mà là yêu cầu bắt buộc: dữ liệu cũ dần là cách một sản phẩm như thế này chết
âm thầm — nó vẫn trả lời trôi chảy, vẫn dẫn nguồn đầy đủ, chỉ là dẫn về số liệu của hai
năm trước.

BỐN NGUYÊN TẮC

1. MỖI NGUỒN MỘT NHỊP RIÊNG. VCI cập nhật theo quý, báo cáo thường niên ra khoảng tháng
   Tư, hồ sơ SEC thì bất cứ lúc nào. Chạy tất cả mỗi ngày là tải nặng vô ích cho máy chủ
   của người khác; chạy tất cả mỗi tháng là lỡ mất số liệu quý.

2. ĐO DỮ LIỆU THẬT, KHÔNG TIN SỔ GHI CHÉP. Script chạy xong không có nghĩa dữ liệu vào
   được — nó có thể chạy ở chế độ thử, có thể hỏng giữa chừng. Nên trước và sau mỗi bước
   đều chụp lại số đếm thật từ Neo4j/Qdrant, và báo cáo phần CHÊNH LỆCH.

3. CHẠY THỬ LÀ MẶC ĐỊNH. `--apply` mới ghi thật, y như mọi script nạp khác trong dự án.

4. KIỂM CHẤT LƯỢNG MỖI LẦN. Cập nhật đều đặn mà không kiểm thì chỉ là tích thêm rác đều
   đặn. Bước cuối luôn quét các bất thường không hiện ra trong phép đếm tổng.

⚠️ VÌ SAO GỌI SCRIPT CON BẰNG TIẾN TRÌNH RIÊNG

Có thể import rồi gọi `main()` của từng script, nhưng như vậy một lỗi ở bước bốn sẽ giết
cả lượt cập nhật và ba bước trước coi như phí. Chạy tiến trình riêng thì mỗi bước hỏng
độc lập, mã thoát nói rõ đúng/sai, và bước sau vẫn chạy được.

CHẠY ĐỊNH KỲ TRÊN WINDOWS

    schtasks /Create /TN "FinGraph refresh" /SC DAILY /ST 02:00 ^
      /TR "\"D:\\FinTech Agent\\.venv\\Scripts\\python.exe\" \"D:\\FinTech Agent\\scripts\\18_refresh.py\" --apply"

Chạy thử:  .venv/Scripts/python.exe scripts/18_refresh.py
Chạy thật: .venv/Scripts/python.exe scripts/18_refresh.py --apply
Chỉ 1 bước: .venv/Scripts/python.exe scripts/18_refresh.py --only vn_metrics --apply --force
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.obs import freshness, logs

console = Console()
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")

# Nhịp cập nhật của từng nguồn, tính bằng ngày.
#
# Các con số này đến từ nhịp công bố THẬT của từng nguồn, không phải chọn cho tròn:
#   VCI       cập nhật số liệu theo quý -> 7 ngày là đủ bắt kịp, mà không tra vô ích
#   cổ đông   thay đổi vài lần một năm  -> 30 ngày
#   mô tả DN  gần như không đổi         -> 90 ngày
#   BCTN      công bố khoảng tháng Tư   -> 30 ngày; tra sớm chỉ tốn HEAD request
#   SEC       hồ sơ nộp bất cứ lúc nào  -> 1 ngày
STEPS: List[Dict[str, Any]] = [
    {
        "name": "vn_metrics",
        "every_days": 7,
        "label": "Số liệu tài chính Việt Nam (VCI)",
        "cmds": [["scripts/12_load_vietnam_metrics.py", "--apply"]],
        "watch": ["financial_years", "vn_latest_year"],
    },
    {
        "name": "vn_shareholders",
        "every_days": 30,
        "label": "Cổ đông lớn Việt Nam",
        "cmds": [["scripts/13_load_vietnam_shareholders.py", "--apply"]],
        "watch": ["ownership_edges"],
    },
    {
        "name": "vn_profiles",
        "every_days": 90,
        "label": "Mô tả doanh nghiệp Việt Nam",
        "cmds": [["scripts/14_load_vietnam_profiles.py", "--apply"]],
        "watch": ["chunks_vn_profile"],
    },
    {
        "name": "vn_reports",
        "every_days": 30,
        "label": "Báo cáo thường niên Việt Nam",
        # `--no-browser` cho lượt tự động: Playwright nặng và vài trang chập chờn, mà
        # lượt định kỳ phải chạy được không người trông. Muốn quét cả trang JavaScript
        # thì chạy tay bước 15 không kèm cờ này.
        "cmds": [["scripts/15_load_vietnam_annual_reports.py", "--apply", "--no-browser"]],
        "watch": ["chunks_vn_reports"],
    },
    {
        "name": "sec_filings",
        "every_days": 1,
        "label": "Hồ sơ SEC mới",
        # ⚠️ HAI LỆNH, KHÔNG PHẢI MỘT. Bản đầu chỉ chạy bước tải về rồi báo "ok" —
        # nhưng tải file xuống đĩa KHÔNG có nghĩa là hệ thống dùng được nó. Hồ sơ phải
        # qua bước lập chỉ mục mới vào kho vector; thiếu bước hai thì lượt cập nhật báo
        # thành công mỗi ngày trong khi agent vẫn không thấy hồ sơ mới.
        #
        # Bước 06 bỏ qua những bản khai đã lập chỉ mục (trừ khi có `--force`), nên chạy
        # lại hằng ngày là rẻ.
        "cmds": [
            ["scripts/01_download.py"],
            ["scripts/06_build_text_index.py"],
        ],
        "watch": ["latest_filing_date", "chunks_10k"],
    },
]

STEP_BY_NAME = {s["name"]: s for s in STEPS}


def due(step: Dict[str, Any], force: bool) -> tuple:
    """(có đến hạn không, lý do) — lý do luôn nói rõ, kể cả khi bỏ qua."""
    if force:
        return True, "bị ép chạy"
    age = freshness.age_days(step["name"])
    if age is None:
        return True, "chưa chạy lần nào"
    if age >= step["every_days"]:
        return True, f"lần cuối cách đây {age:.1f} ngày (ngưỡng {step['every_days']})"
    return False, f"mới chạy {age:.1f} ngày trước (ngưỡng {step['every_days']})"


def run_step(step: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """Chạy các script con của một bước, lần lượt, dừng ngay khi có lệnh hỏng."""
    started = time.time()
    tails = []
    for cmd_parts in step["cmds"]:
        one = _run_one(cmd_parts, timeout)
        tails.append(one["tail"])
        if not one["ok"]:
            return {**one, "seconds": time.time() - started,
                    "tail": "\n".join(t for t in tails if t)}
    return {"ok": True, "code": 0, "seconds": time.time() - started,
            "tail": "\n".join(t for t in tails if t), "error": ""}


def _run_one(cmd_parts: List[str], timeout: int) -> Dict[str, Any]:
    """Một lệnh, một tiến trình riêng. Không bao giờ ném ngoại lệ ra ngoài."""
    started = time.time()
    cmd = [PYTHON, str(ROOT / cmd_parts[0])] + list(cmd_parts[1:])
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        ok = proc.returncode == 0
        # Giữ phần ĐUÔI của đầu ra: các script trong dự án này in kết luận và dòng đối
        # chiếu ở cuối, còn phần đầu chỉ là thanh tiến trình.
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-6:])
        err = "\n".join((proc.stderr or "").strip().splitlines()[-3:])
        return {"ok": ok, "code": proc.returncode, "seconds": time.time() - started,
                "tail": tail, "error": "" if ok else err}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "seconds": time.time() - started,
                "tail": "", "error": f"quá {timeout}s, đã dừng"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": -1, "seconds": time.time() - started,
                "tail": "", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def diff(before: Dict[str, Any], after: Dict[str, Any], keys: List[str]) -> str:
    """Phần thay đổi của những con số mà bước này lẽ ra phải động tới."""
    parts = []
    for k in keys:
        a, b = before.get(k), after.get(k)
        if a == b:
            parts.append(f"{k}: không đổi")
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            parts.append(f"{k}: {a:,} → {b:,} ({b - a:+,})")
        else:
            parts.append(f"{k}: {a} → {b}")
    return " · ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cap nhat du lieu dinh ky")
    ap.add_argument("--apply", action="store_true", help="chay that (mac dinh chi chay thu)")
    ap.add_argument("--only", default=None, help="chi mot buoc, vd vn_metrics")
    ap.add_argument("--force", action="store_true", help="chay ca khi chua den han")
    ap.add_argument("--timeout", type=int, default=3600, help="gioi han giay moi buoc")
    args = ap.parse_args()

    steps = STEPS
    if args.only:
        if args.only not in STEP_BY_NAME:
            raise SystemExit(f"Không có bước {args.only!r}. Có: {', '.join(STEP_BY_NAME)}")
        steps = [STEP_BY_NAME[args.only]]

    console.print(f"[cyan]Cập nhật dữ liệu[/] · {len(steps)} bước"
                  + ("" if args.apply else " · [yellow]CHẠY THỬ[/]"))

    before = freshness.snapshot()
    results = []

    for step in steps:
        should, why = due(step, args.force)
        if not should:
            console.print(f"  [dim]bỏ qua[/] {step['label']:<38} {why}")
            results.append({"step": step, "ran": False, "why": why})
            continue

        console.print(f"  [cyan]chạy[/]   {step['label']:<38} {why}")
        if not args.apply:
            results.append({"step": step, "ran": False, "why": "chạy thử"})
            continue

        outcome = run_step(step, args.timeout)
        after = freshness.snapshot()
        changed = diff(before, after, step["watch"])
        before = after

        status = "ok" if outcome["ok"] else "failed"
        freshness.record_run(step["name"], status, seconds=round(outcome["seconds"], 1),
                             changed=changed)
        logs.log_ingest(step["name"], status, seconds=round(outcome["seconds"], 1),
                        changed=changed, error=outcome["error"] or None)
        results.append({"step": step, "ran": True, "outcome": outcome, "changed": changed})

        mark = "[green]xong[/]" if outcome["ok"] else "[red]HỎNG[/]"
        console.print(f"           {mark} trong {outcome['seconds'] / 60:.1f} phút · {changed}")
        if not outcome["ok"]:
            console.print(f"           [red]{outcome['error'][:200]}[/]")

    # ---- Bảng tổng kết ----
    table = Table(title="Kết quả cập nhật")
    for col in ("Bước", "Đã chạy", "Kết quả", "Thay đổi"):
        table.add_column(col, overflow="fold")
    for r in results:
        if not r["ran"]:
            table.add_row(r["step"]["name"], "không", f"[dim]{r['why']}[/]", "—")
        else:
            ok = r["outcome"]["ok"]
            table.add_row(r["step"]["name"], "có",
                          "[green]ok[/]" if ok else f"[red]lỗi {r['outcome']['code']}[/]",
                          r["changed"])
    console.print()
    console.print(table)

    # ---- Kiểm chất lượng: luôn chạy, kể cả khi mọi bước đều bỏ qua ----
    bad = freshness.anomalies()
    issues = []
    if bad["implausible_fiscal_year"]:
        rows = bad["implausible_fiscal_year"]
        issues.append(f"{len(rows)} bản ghi có năm tài chính vô lý "
                      f"(vd {rows[0]['ticker']}={rows[0]['year']})")
    if bad["vn_years_without_currency"]:
        issues.append(f"{bad['vn_years_without_currency']} bản ghi Việt Nam thiếu đồng tiền")
    if bad["orphan_financial_years"]:
        issues.append(f"{bad['orphan_financial_years']} FinancialYear mồ côi")

    if issues:
        console.print("\n[yellow]Kiểm chất lượng — có vấn đề cần biết:[/]")
        for item in issues:
            console.print(f"   · {item}")
        console.print("[dim]Đây là dữ liệu doanh nghiệp tự khai, không phải lỗi nạp. "
                      "Ghi ra để không ai dùng max(fiscal_year) mà không biết.[/]")
    else:
        console.print("\n[green]Kiểm chất lượng: không có bất thường.[/]")
    logs.log_ingest("quality_check", "ok" if not issues else "warn", issues=issues)

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa động vào dữ liệu.[/] "
                      "Thêm [cyan]--apply[/] để chạy thật.")

    failed = [r for r in results if r["ran"] and not r["outcome"]["ok"]]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
