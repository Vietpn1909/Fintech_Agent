"""Bước 9: Gộp các node Company bị tách đôi (entity resolution).

VẤN ĐỀ NÀY NGHIÊM TRỌNG HƠN VẺ NGOÀI CỦA NÓ

Đồ thị có hai nguồn tạo node Company:

    1. Bước nạp vũ trụ  -> lấy tên chính thức từ SEC, VIẾT HOA TOÀN BỘ: "MICROSOFT CORP"
    2. Bước trích xuất  -> lấy tên LLM đọc trong văn bản:              "Microsoft Corporation"

Lệnh MERGE khớp theo thuộc tính `name`, nên hai chuỗi khác nhau tạo ra HAI node cho cùng
một doanh nghiệp. Đo thật sau lần chạy đầu: 49 trên 346 node có cạnh bị tách đôi — 14%
đồ thị bị phân mảnh.

Hậu quả không phải "hơi lộn xộn" mà là ĐỨT MẠCH SUY LUẬN. Ví dụ có thật:

    Câu hỏi: "TSMC gián đoạn thì ảnh hưởng tới Microsoft qua đường nào?"
    Cạnh TSMC -[SUPPLIED_BY]-> NVIDIA CÓ tồn tại, nhưng nằm ở node
    "Taiwan Semiconductor Manufacturing Company Lim" (tên bị cụt), trong khi truy vấn
    lại khớp vào node "Taiwan Semiconductor Manufacturing Company".
    -> shortestPath không thấy đường đúng, phải vòng qua Intel bằng hai cạnh
       COMPETES_WITH, cho ra một chuỗi lý giải nghe hợp lý nhưng SAI BẢN CHẤT.

Đây đúng là kiểu lỗi tệ nhất của GraphRAG: không báo lỗi, vẫn trả lời được, nhưng trả
lời bằng một đường đi khác với đường đi thật.

CÁCH GỘP

Chuẩn hóa tên thành khóa (bỏ hậu tố pháp lý, bỏ dấu câu, hạ chữ thường) rồi gom nhóm.
Thêm một bước bắt tên bị CẮT CỤT: nếu khóa này là tiền tố của khóa kia và phần dư không
quá 5 ký tự, coi là một ("taiwansemiconductormanufacturing" và "...manufacturinglim").

Node được giữ lại là node có `cik` — tức node gốc từ SEC, mang sẵn mã chứng khoán và
liên kết tới toàn bộ số liệu tài chính. Nhưng TÊN HIỂN THỊ thì lấy biến thể đẹp nhất
(viết hoa chữ đầu), vì "Microsoft Corporation" dễ đọc hơn "MICROSOFT CORP".

Việc gộp dùng apoc.refactor.mergeNodes để chuyển toàn bộ cạnh sang node giữ lại một cách
an toàn, thay vì tự nối lại bằng tay và rất dễ sót.

Chạy:  .venv/Scripts/python.exe scripts/09_resolve_entities.py
       thêm --dry-run để xem trước mà không sửa gì
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from src.graph.store import GraphStore

console = Console()

_LEGAL = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|llc|ltd|limited|plc|holdings?|"
    r"group|technologies|technology|systems|international|worldwide|global|"
    r"n\.?v|s\.?a|a\.?s|ag|se|gmbh|kk|lp|llp|pte|bhd)\b\.?",
    re.IGNORECASE,
)


def norm_key(name: str) -> str:
    """Tên -> khóa so khớp: bỏ hậu tố pháp lý, bỏ mọi ký tự không phải chữ/số."""
    stripped = _LEGAL.sub("", name or "")
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def display_name(names: List[str]) -> str:
    """Chọn tên hiển thị đẹp nhất trong nhóm.

    Thứ tự ưu tiên:
      1. Tên chuẩn đã khai trong ontology (src/graph/schema.py) — luôn đúng và sạch.
      2. Tên không viết hoa toàn bộ: "Microsoft Corporation" hơn "MICROSOFT CORP".
      3. Tên dài nhất, vì thường đầy đủ hơn.

    Bước 1 là cần thiết chứ không phải làm đẹp: nếu chỉ lấy tên dài nhất, nhóm TSMC sẽ
    chọn "Taiwan Semiconductor Manufacturing Company Lim" — bản bị LLM cắt cụt — làm tên
    chính thức, và mọi truy vấn viết đúng tên đầy đủ về sau sẽ không khớp.
    """
    from src.graph.schema import CANONICAL_COMPANIES

    canonical_values = set(CANONICAL_COMPANIES.values())
    for n in names:
        if n in canonical_values:
            return n

    # Tên chuẩn có thể chưa nằm trong nhóm; thử tra bằng khóa đã chuẩn hóa
    for n in names:
        hit = CANONICAL_COMPANIES.get(norm_key(n))
        if hit:
            return hit

    def score(n: str) -> tuple:
        return (n.isupper(), -len(n))
    return sorted(names, key=score)[0]


def group_by_key(names: List[str]) -> Dict[str, List[str]]:
    """Gom tên theo khóa, có xử lý tên bị cắt cụt."""
    buckets: Dict[str, List[str]] = defaultdict(list)
    for n in names:
        k = norm_key(n)
        if k:
            buckets[k].append(n)

    # Gộp khóa bị cắt cụt vào khóa đầy đủ.
    # Điều kiện chặt để không gộp nhầm: khóa ngắn phải dài >= 12 ký tự và phần dư <= 5.
    keys = sorted(buckets, key=len)
    merged: Dict[str, str] = {}
    for i, short in enumerate(keys):
        if len(short) < 12:
            continue
        for long in keys[i + 1 :]:
            if long.startswith(short) and len(long) - len(short) <= 5:
                merged[short] = merged.get(long, long)
                break

    final: Dict[str, List[str]] = defaultdict(list)
    for k, names_in in buckets.items():
        final[merged.get(k, k)].extend(names_in)
    return final


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="chỉ xem, không sửa đồ thị")
    args = ap.parse_args()

    store = GraphStore()

    # ⚠️ PHẢI XÉT TOÀN BỘ NODE COMPANY, không chỉ node đã có cạnh tri thức.
    #
    # Bản đầu tiên lọc `WHERE EXISTS { (c)-[r]-() WHERE type(r) <> 'HAS_FINANCIALS' }` cho
    # nhanh. Nhưng đúng những node cần gộp nhất lại bị loại bởi chính điều kiện đó:
    #
    #   "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"  <- node gốc SEC, CHỈ có số liệu
    #   "Taiwan Semiconductor Manufacturing Company" <- node do LLM tạo, giữ cạnh cung ứng
    #
    # Node thứ nhất không có cạnh tri thức nên không lọt vào danh sách, và hai node cùng
    # một doanh nghiệp tồn tại song song: hỏi số liệu thì ra node này, hỏi chuỗi cung ứng
    # thì ra node kia, và không câu hỏi nào kết hợp được cả hai.
    #
    # Quét cả 6.000+ node mất thêm vài giây — rẻ hơn nhiều so với một đồ thị bị chia đôi.
    rows = store.run("MATCH (c:Company) RETURN c.name AS name, c.cik AS cik, c.ticker AS ticker")
    console.print(f"[cyan]{len(rows):,} node Company trong đồ thị.[/]")

    with_edges = {
        r["name"] for r in store.run(
            """
            MATCH (c:Company)
            WHERE EXISTS { MATCH (c)-[r]-() WHERE type(r) <> 'HAS_FINANCIALS' }
            RETURN c.name AS name
            """
        )
    }
    console.print(f"[cyan]{len(with_edges)} trong số đó có cạnh tri thức.[/]")

    by_name = {r["name"]: r for r in rows}

    # Chỉ gộp những nhóm có LIÊN QUAN tới đồ thị tri thức. Gộp các bản trùng thuần túy
    # trong danh sách SEC không mang lại gì mà lại tốn thời gian.
    groups = {
        k: v for k, v in group_by_key(list(by_name)).items()
        if len(v) > 1 and any(n in with_edges for n in v)
    }

    if not groups:
        console.print("[green]Không có node trùng lặp.[/]")
        store.close()
        return

    table = Table(title=f"{len(groups)} nhóm bị tách đôi")
    table.add_column("Giữ lại")
    table.add_column("Gộp vào")
    plans = []

    for key, names in sorted(groups.items()):
        # Node giữ lại phải là node có cik: nó mang mã chứng khoán và nối tới số liệu
        with_cik = [n for n in names if by_name[n].get("cik")]
        survivor = with_cik[0] if with_cik else display_name(names)
        pretty = display_name(names)
        losers = [n for n in names if n != survivor]
        plans.append({"survivor": survivor, "losers": losers, "pretty": pretty})
        table.add_row(f"{pretty}", ", ".join(l[:34] for l in losers))

    console.print(table)

    if args.dry_run:
        console.print("[yellow]--dry-run: không thay đổi gì.[/]")
        store.close()
        return

    merged_total = 0
    for plan in plans:
        # apoc.refactor.mergeNodes chuyển toàn bộ cạnh sang node đầu tiên trong danh sách.
        # mergeRels:true gộp các cạnh trùng loại thay vì để lại cạnh song song.
        store.run(
            """
            MATCH (survivor:Company {name: $survivor})
            MATCH (loser:Company) WHERE loser.name IN $losers
            WITH survivor, collect(loser) AS losers
            CALL apoc.refactor.mergeNodes([survivor] + losers,
                 {properties: 'discard', mergeRels: true}) YIELD node
            SET node.name = $pretty
            RETURN node
            """,
            survivor=plan["survivor"], losers=plan["losers"], pretty=plan["pretty"],
        )
        merged_total += len(plan["losers"])

    console.print(f"\n[green]Đã gộp {merged_total} node trùng vào {len(plans)} node chuẩn.[/]")

    remaining = store.run(
        """
        MATCH (c:Company)
        WHERE EXISTS { MATCH (c)-[r]-() WHERE type(r) <> 'HAS_FINANCIALS' }
        RETURN count(c) AS n
        """
    )
    console.print(f"Còn lại {remaining[0]['n']} node Company có cạnh tri thức.")
    store.close()


if __name__ == "__main__":
    main()
