"""Bước 25: Gợi ý đầu tư cho doanh nghiệp Việt Nam — Nên mua / Theo dõi / Tránh.

    .venv/Scripts/python.exe scripts/25_suggest.py FPT
    .venv/Scripts/python.exe scripts/25_suggest.py --sector "ngân hàng" --top 10
    .venv/Scripts/python.exe scripts/25_suggest.py "Hòa Phát" --out goi-y.md

⚠️ Đây là gợi ý do AI đưa ra, cần cân nhắc kỹ trước khi thực hiện theo. Mức gợi ý KHÔNG
dựa trên giá cổ phiếu. Phương pháp chấm điểm công bố đầy đủ ở `src/agent/advisor.py`.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent import advisor
from src.obs import logs

console = Console()


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def render_one(r: Dict[str, Any]) -> str:
    out = [f"# Gợi ý đầu tư — {r.get('company', '')} (`{r.get('ticker', '')}`)", "",
           f"> {advisor.DISCLAIMER}", ""]

    if r.get("status") not in ("ok", "khong_xep_muc"):
        reason = r.get("ly_do") or r.get("hint") or r.get("status")
        out += [f"**Không đưa ra gợi ý.** {reason}", ""]
        for opt in r.get("options", []):
            out.append(f"- `{opt['ticker']}` — {opt['company']}")
        return "\n".join(out)

    if r["muc"]:
        out.append(f"## Mức gợi ý: **{r['muc']}** · {r['diem_tong']}/100 điểm")
    else:
        out.append("## Không xếp mức")
        for why in r["ly_do_khong_xep_muc"]:
            out.append(f"- {why}")
    out.append("")
    out.append(f"Ngành **{r['sector']}** · số liệu năm **{r['nam_so_lieu']}** · "
               f"tính được {r['do_phu_trong_so']:g}% trọng số")
    if r.get("chot_chan"):
        out += ["", f"⚠️ {r['chot_chan']}"]
    out.append("")

    out += ["## Điểm từng nhóm", "",
            "| Nhóm | Điểm | Trọng số gốc | Trọng số thực |", "|---|---:|---:|---:|"]
    for g in sorted(r["nhom"].values(), key=lambda g: -(g["diem"] or -1)):
        out.append(f"| {g['ten']} | {_fmt(g['diem'])} | {g['trong_so_goc']:g}% | "
                   f"{g['trong_so_thuc']:g}% |")
    out.append("")

    out += ["## Chi tiết", ""]
    for g in r["nhom"].values():
        if g["status"] != "ok":
            out.append(f"**{g['ten']}** — *không tính được: {g.get('ly_do', '')}*")
            out.append("")
            continue
        out.append(f"**{g['ten']}** — {g['diem']} điểm")
        for item in g.get("chi_tieu", []):
            note = f" · {item['giai_thich']}" if item.get("giai_thich") else ""
            out.append(f"- {item['ten']}: {_fmt(item['gia_tri'])} → {_fmt(item['diem'])} điểm{note}")
        detail = g.get("chi_tiet") or {}
        for c in detail.get("chi_so", []):
            out.append(f"- {c['ten']}: {c['gia_tri_dau']} ({c['tu_ngay']}) → "
                       f"{c['gia_tri_cuoi']} ({c['den_ngay']}), {c['thay_doi']:+g} {c['don_vi']} "
                       f"— {c['chieu']} với ngành")
        if "trung_vi_tang_truong_doanh_thu_pct" in detail:
            out.append(f"- {detail['so_doanh_nghiep_my']} doanh nghiệp Mỹ cùng ngành: trung vị "
                       f"tăng trưởng doanh thu {detail['trung_vi_tang_truong_doanh_thu_pct']}%, "
                       f"thay đổi biên ròng {detail['trung_vi_thay_doi_bien_rong_diem_pct']} điểm %")
        out.append("")

    out += ["## Những gì gợi ý này CHƯA xét", ""]
    out += [f"- {x}" for x in r["chua_xet"]]
    out += ["", "---", f"*{advisor.DISCLAIMER}*"]
    return "\n".join(out)


def render_list(r: Dict[str, Any]) -> str:
    out = [f"# Gợi ý đầu tư — ngành {r.get('nganh', r.get('query', ''))}", "",
           f"> {advisor.DISCLAIMER}", ""]
    if r.get("status") != "ok":
        out.append(f"**Không rõ ngành.** Các ngành có: {', '.join(r.get('cac_nganh', []))}")
        return "\n".join(out)

    dist = " · ".join(f"{k}: {v}" for k, v in r["phan_bo_muc"].items())
    out += [f"{r['so_duoc_xep_muc']} / {r['so_doanh_nghiep_trong_nganh']} doanh nghiệp được "
            f"xếp mức ({dist}).", ""]
    out += ["| # | Doanh nghiệp | Mức | Điểm | Điểm mạnh | Điểm yếu |",
            "|---:|---|---|---:|---|---|"]
    for i, d in enumerate(r["danh_sach"], 1):
        out.append(f"| {i} | {d['company']} (`{d['ticker']}`) | **{d['muc']}** | "
                   f"{d['diem_tong']} | {', '.join(d['diem_manh']) or '—'} | "
                   f"{', '.join(d['diem_yeu']) or '—'} |")
    out.append("")
    mac = r["boi_canh_nganh"]["vi_mo"]
    glo = r["boi_canh_nganh"]["nganh_toan_cau"]
    out.append("## Bối cảnh chung của ngành")
    out.append("")
    out.append(f"- Vĩ mô thế giới: {mac.get('diem', '—')} điểm"
               + ("" if mac.get("status") == "ok" else f" ({mac.get('ly_do', '')})"))
    out.append(f"- Doanh nghiệp Mỹ cùng ngành: {glo.get('diem', '—')} điểm"
               + (f" ({glo['so_doanh_nghiep_my']} DN, trung vị tăng trưởng "
                  f"{glo['trung_vi_tang_truong_doanh_thu_pct']}%)" if glo.get("status") == "ok"
                  else f" ({glo.get('ly_do', '')})"))
    out += ["", "## Những gì gợi ý này CHƯA xét", ""]
    out += [f"- {x}" for x in r["chua_xet"]]
    out += ["", "---", f"*{advisor.DISCLAIMER}*"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Goi y dau tu doanh nghiep Viet Nam")
    ap.add_argument("company", nargs="?", help="ten hoac ma doanh nghiep")
    ap.add_argument("--sector", default=None, help="xep hang ca mot nganh")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-revenue", type=float, default=advisor.DEFAULT_MIN_REVENUE,
                    help="doanh thu toi thieu (VND) cho che do nganh")
    ap.add_argument("--out", default=None, help="ghi ra tep markdown")
    args = ap.parse_args()

    if not args.company and not args.sector:
        ap.print_help()
        return

    started = time.time()
    if args.sector:
        result = advisor.suggest(args.sector, top=args.top, min_revenue=args.min_revenue)
        markdown = render_list(result)
    else:
        result = advisor.assess(args.company)
        markdown = render_one(result)
    elapsed = round(time.time() - started, 1)

    logs.log_ingest("investment_suggestion", result.get("status", "?"),
                    company=args.company, sector=args.sector,
                    level=result.get("muc"), seconds=elapsed)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        console.print(f"[green]Đã ghi[/] {args.out} · {elapsed} giây")
    else:
        print(markdown)
        console.print(f"\n[dim]{elapsed} giây[/]")


if __name__ == "__main__":
    main()
