"""Duyệt các node Company trong đồ thị chưa gắn được mã CIK.

VẤN ĐỀ

Mô hình trích tên doanh nghiệp ra từ báo cáo, nhưng không biết mã CIK của chúng. Kết quả
là đồ thị tri thức và tầng số liệu thành HAI THẾ GIỚI RỜI NHAU: node "Cognizant" mà mô
hình trích từ hồ sơ ServiceNow không hề nối với bản ghi Cognizant (CTSH) trong 6.255
doanh nghiệp SEC. Hỏi số liệu thì có, hỏi quan hệ thì có, nhưng hai bên không biết nhau.

Script này CHỈ ĐỌC. Nó in ra ba nhóm để người duyệt bằng mắt:

    ĐÃ DUYỆT      đã nằm trong config/entity_merges.json
    ỨNG VIÊN      bộ phân giải khớp chắc chắn, nhưng CẦN NGƯỜI ĐỌC CÂU VĂN GỐC
    NHIỄU         không khớp gì — sản phẩm, tổ chức từ thiện, công ty tư nhân

⚠️ VÌ SAO PHẢI CÓ NGƯỜI DUYỆT

Khớp tên tự động sai khoảng 30% ở đây, và sai theo kiểu không luật nào bắt được:

    IBM -> International Business Machines    khớp mã chính xác, ĐÚNG
    GF  -> New Germany Fund                   khớp mã chính xác, SAI (phải là GlobalFoundries)

Hai trường hợp giống hệt nhau về mặt hình thức. Chỉ có câu văn gốc mới phân biệt được —
"hợp đồng cung ứng wafer với GF" thì GF không thể là một quỹ đầu tư Đức. Nên script in
kèm câu văn, và việc quyết định thuộc về người đọc.

Chạy:  .venv/Scripts/python.exe scripts/10_review_graph_entities.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent.tools import graph
from src.graph.schema import INFRA_RELATIONS
from src.ingest.on_demand import resolve_company

console = Console()
MERGE_FILE = ROOT / "config" / "entity_merges.json"


def load_approved() -> dict:
    if not MERGE_FILE.exists():
        return {}
    data = json.loads(MERGE_FILE.read_text(encoding="utf-8"))
    return {m["node"]: m for m in data.get("merges", [])}


def evidence_for(store, name: str, limit: int = 2) -> list:
    return store.run(
        """
        MATCH (c:Company {name: $n})-[r]-(o)
        WHERE NOT type(r) IN $infra
        RETURN type(r) AS rel, startNode(r).name AS s, endNode(r).name AS t,
               left(coalesce(r.evidence, ''), 130) AS ev
        LIMIT $limit
        """,
        n=name, infra=INFRA_RELATIONS, limit=limit,
    )


def main() -> None:
    store = graph()
    approved = load_approved()

    rows = store.run(
        """
        MATCH (c:Company) WHERE c.cik IS NULL
        OPTIONAL MATCH (c)-[r]-() WHERE NOT type(r) IN $infra
        RETURN c.name AS name, count(r) AS deg
        ORDER BY deg DESC, name
        """,
        infra=INFRA_RELATIONS,
    )

    done, candidates, noise = [], [], []
    for row in rows:
        if row["name"] in approved:
            done.append(row)
            continue
        result = resolve_company(row["name"])
        if result["status"] == "ok":
            candidates.append((row, result["best"]))
        else:
            noise.append(row)

    console.print(f"\n[bold]Node Company chưa có CIK:[/] {len(rows)}")
    console.print(f"  đã duyệt để gộp : [green]{len(done)}[/]")
    console.print(f"  ứng viên cần đọc: [yellow]{len(candidates)}[/]")
    console.print(f"  nhiễu           : [dim]{len(noise)}[/]\n")

    if candidates:
        console.print("[bold yellow]ỨNG VIÊN — đọc câu văn rồi tự quyết, đừng tin mỗi cái tên[/]\n")
        for row, best in candidates:
            console.print(f"[bold]{row['name']}[/]  ({row['deg']} cạnh)"
                          f"  →  [cyan]{best['ticker']}[/] {best['name']}  ({best['match']})")
            for ev in evidence_for(store, row["name"]):
                console.print(f"    {ev['s'][:26]} -{ev['rel']}→ {ev['t'][:26]}")
                console.print(f"    [dim]\"{ev['ev']}\"[/]")
            console.print()

    console.print("[bold]NHIỄU — 25 node nhiều cạnh nhất, không khớp doanh nghiệp SEC nào[/]")
    for row in noise[:25]:
        console.print(f"  [dim]{row['deg']:3} cạnh[/]  {row['name'][:60]}")

    console.print(
        f"\nMuốn gộp thêm: mở [cyan]{MERGE_FILE.relative_to(ROOT)}[/] thêm dòng "
        "{\"node\": ..., \"ticker\": ..., \"why\": ...}, rồi chạy "
        "[cyan]scripts/11_merge_graph_entities.py[/]"
    )


if __name__ == "__main__":
    main()
