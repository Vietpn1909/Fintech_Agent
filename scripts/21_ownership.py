"""Bước 21: Phân tích mạng lưới sở hữu — ai thực sự đứng sau một doanh nghiệp.

    .venv/Scripts/python.exe scripts/21_ownership.py Vinamilk
    .venv/Scripts/python.exe scripts/21_ownership.py ACB.VN --depth 4
    .venv/Scripts/python.exe scripts/21_ownership.py FPT --common "FPT Retail"
    .venv/Scripts/python.exe scripts/21_ownership.py HPG.VN --out mang-luoi.md

⚠️ Đọc kỹ cột "Kiểm soát". Nó KHÔNG suy ra được từ cột phần trăm bên cạnh, và đó chính
là lý do cả bước này tồn tại — xem chú thích đầu `src/agent/ownership.py`.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent import ownership
from src.obs import logs

console = Console()


def _chain_rows(chains: List[Dict[str, Any]], holds: bool, subject: str) -> List[str]:
    """Một chuỗi sở hữu thành một dòng bảng markdown.

    ⚠️ `holds` QUYẾT ĐỊNH CHIỀU MŨI TÊN, VÀ ĐÓ KHÔNG PHẢI CHI TIẾT TRÌNH BÀY.

    Cả `upstream` lẫn `downstream` đều trả về chuỗi có phần tử đầu là doanh nghiệp đang
    xét — tiện cho việc đọc, nhưng nghĩa của hai chuỗi NGƯỢC NHAU. Dùng chung một mũi tên
    cho cả hai thì mục "doanh nghiệp này nắm gì" hiện ra thành "những ai nắm doanh nghiệp
    này", đảo trọn vẹn ý nghĩa mà bảng vẫn trông hoàn hảo.

    Đây đúng là họ lỗi đã cắn dự án này trước đây ("FPT Digital Retail nắm 46,54% FPT
    Corporation", đọc ngược chiều sở hữu) nên nó được viết ra đây thay vì tin vào trí nhớ.
    """
    arrow = " → " if holds else " ← "
    head = "Chuỗi (A → B: A nắm B)" if holds else "Chuỗi (A ← B: B nắm A)"
    lines = [f"| {head} | Từng tầng | Quyền lợi kinh tế | Kiểm soát |",
             "|---|---|---:|---|"]
    for c in chains:
        # Phần tử đầu luôn là chính doanh nghiệp đang xét; thay bằng mã cho bảng đỡ dài.
        path = arrow.join([subject] + list(c["chuoi"][1:]))
        steps = " × ".join(f"{s:g}%" for s in c["tung_tang_pct"])
        control = "**CÓ**" if c["chuoi_nay_kiem_soat"] else "không"
        lines.append(f"| {path} | {steps} | {c['quyen_loi_kinh_te_pct']:g}% | {control} |")
    return lines


def render(net: Dict[str, Any]) -> str:
    if net.get("status") != "ok":
        if net.get("status") == "ambiguous":
            opts = "\n".join(f"- `{o['ticker']}` — {o['company']}"
                             for o in net.get("options", []))
            return f"# Tên không rõ\n\n{net.get('hint', '')}\n\n{opts}\n"
        if net.get("status") == "backend_unavailable":
            return ("# Không truy cập được cơ sở dữ liệu\n\nĐây là sự cố kỹ thuật tạm "
                    "thời, KHÔNG phải hệ thống thiếu dữ liệu về doanh nghiệp này.\n")
        return f"# Không tìm thấy\n\n{net.get('hint', '')}\n"

    up = net["ai_nam_doanh_nghiep_nay"]
    down = net["doanh_nghiep_nay_nam_gi"]
    peers = net["cung_co_dong_chi_phoi"]

    out = [f"# Mạng lưới sở hữu — {net['company']} (`{net['ticker']}`)", ""]
    # Kết luận lên đầu: người đọc bảng dài rất dễ tự rút ra kết luận từ cột phần trăm,
    # mà cột đó KHÔNG nói về quyền kiểm soát.
    out.append(f"**Kết luận:** {net['ket_luan_kiem_soat']}")
    out.append("")
    out.append(f"> {net['luu_y_bat_buoc']}")
    out.append("")

    out.append("## 1. Ai nắm doanh nghiệp này")
    out.append("")
    if up["truc_tiep"]:
        out.append("### Trực tiếp")
        out.append("")
        out += _chain_rows(up["truc_tiep"], holds=False, subject=net["ticker"])
        out.append("")
    if up["gian_tiep"]:
        out.append("### Gián tiếp — qua pháp nhân trung gian")
        out.append("")
        out += _chain_rows(up["gian_tiep"], holds=False, subject=net["ticker"])
        out.append("")
    elif up["truc_tiep"]:
        out.append("*Không có chuỗi sở hữu gián tiếp nào vượt ngưỡng.*")
        out.append("")
    if up["bo_qua_vi_nho"] or up["bo_qua_vi_thieu_ty_le"]:
        out.append(f"*Đã bỏ qua {up['bo_qua_vi_nho']} chuỗi quá nhỏ và "
                   f"{up['bo_qua_vi_thieu_ty_le']} chuỗi thiếu tỷ lệ ở một mắt xích.*")
        out.append("")

    out.append("## 2. Doanh nghiệp này nắm những gì")
    out.append("")
    if down["truc_tiep"] or down["gian_tiep"]:
        out += _chain_rows(down["truc_tiep"] + down["gian_tiep"], holds=True,
                           subject=net["ticker"])
    else:
        out.append("*Doanh nghiệp này không đứng tên cổ đông ở đâu trong đồ thị.*")
    out.append("")

    out.append("## 3. Doanh nghiệp cùng cổ đông chi phối")
    out.append("")
    if peers["lien_quan"]:
        out.append(f"Cổ đông nắm từ {peers['nguong_pct']:g}% trở lên ở cả hai phía:")
        out.append("")
        for item in peers["lien_quan"]:
            vias = "; ".join(f"{v['co_dong']} ({v['nam_o_day_pct']:g}% / "
                             f"{v['nam_o_kia_pct']:g}%)" for v in item["qua_co_dong"])
            out.append(f"- **{item['ten']}** — qua {vias}")
    else:
        out.append("*Không có doanh nghiệp nào cùng cổ đông chi phối.*")
    out.append("")

    out.append("## 4. Điều phân tích này KHÔNG thấy")
    out.append("")
    for gap in net["gaps"]:
        out.append(f"- {gap}")
    out.append("")
    return "\n".join(out)


def render_common(result: Dict[str, Any]) -> str:
    direct = result.get("quan_he_so_huu_truc_tiep") or {}
    out = [f"# Quan hệ giữa `{result['a']}` và `{result['b']}`", ""]
    if direct.get("ket_luan"):
        out += [f"**Kết luận:** {direct['ket_luan']}", ""]

    # Quan hệ "bên này nắm bên kia" in TRƯỚC danh sách cổ đông chung — xem chú thích ở
    # `ownership.chain_between`.
    for key, label in (("a_nam_b", f"{result['a']} nắm {result['b']}"),
                       ("b_nam_a", f"{result['b']} nắm {result['a']}")):
        chains = direct.get(key) or []
        if not chains:
            continue
        out += [f"## {label}", ""]
        out += _chain_rows(chains, holds=True, subject=chains[0]["chuoi"][0])
        out.append("")

    out += ["## Cổ đông chung", "", result["y_nghia"], ""]
    if not result["cung_co_dong"]:
        out.append("*Không có cổ đông nào trùng nhau trong dữ liệu.*")
        return "\n".join(out)
    out.append("| Cổ đông | Loại | Tỷ lệ ở A | Tỷ lệ ở B | Chi phối cả hai |")
    out.append("|---|---|---:|---:|---|")
    for s in result["cung_co_dong"]:
        a = f"{s['ty_le_a_pct']:g}%" if s["ty_le_a_pct"] is not None else "—"
        b = f"{s['ty_le_b_pct']:g}%" if s["ty_le_b_pct"] is not None else "—"
        out.append(f"| {s['ten']} | {s['loai']} | {a} | {b} | "
                   f"{'**CÓ**' if s['chi_phoi_ca_hai'] else 'không'} |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phan tich mang luoi so huu")
    ap.add_argument("company", help="ten hoac ma doanh nghiep")
    ap.add_argument("--depth", type=int, default=3, help="so tang toi da (mac dinh 3)")
    ap.add_argument("--min-pct", type=float, default=ownership.MIN_ECONOMIC_PCT,
                    help="bo qua chuoi co quyen loi kinh te nho hon muc nay")
    ap.add_argument("--common", default=None,
                    help="so voi mot doanh nghiep khac de tim co dong chung")
    ap.add_argument("--out", default=None, help="ghi ra tep markdown")
    args = ap.parse_args()

    started = time.time()
    net = ownership.network(args.company, depth=args.depth, min_pct=args.min_pct)

    if args.common and net.get("status") == "ok":
        from src.agent.tools import _resolve
        other = _resolve(args.common)
        if other["status"] != "ok":
            console.print(f"[red]Không phân giải được[/] {args.common}: {other['status']}")
            raise SystemExit(1)
        result = ownership.common_holders(net["ticker"], other["best"]["ticker"])
        markdown = render_common(result)
    else:
        markdown = render(net)

    elapsed = round(time.time() - started, 1)
    logs.log_ingest("ownership_network", net.get("status", "?"),
                    company=args.company, ticker=net.get("ticker"),
                    depth=args.depth, seconds=elapsed)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        console.print(f"[green]Đã ghi[/] {args.out} · {elapsed} giây")
    else:
        # In thô: bảng markdown có dấu ngoặc vuông mà rich sẽ nuốt mất.
        print(markdown)
        console.print(f"\n[dim]{elapsed} giây[/]")

    if net.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
