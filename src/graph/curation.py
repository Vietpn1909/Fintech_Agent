"""Bảng dọn thực thể do người duyệt, và cách áp dụng nó.

VÌ SAO PHẢI LÀ MỘT MODULE DÙNG CHUNG

Bước nạp trong `04_build_knowledge_graph.py` ghi lại TOÀN BỘ `triples.jsonl` mỗi lần
chạy, kể cả những dòng đã có từ lần trước. Nghĩa là mọi việc dọn dẹp làm trực tiếp trên
Neo4j đều bị xóa sổ ở lần trích xuất kế tiếp.

Đã xảy ra thật, và đây là con số đo được:

    dọn xong                              170 node không CIK · 1.908 cạnh
    chạy lại script 04 với 50 chunk mới   312 node không CIK · 3.376 cạnh

Toàn bộ tổ chức từ thiện, đại lý chuyển nhượng cổ phiếu và các biến thể tên trùng đã bị
xóa đều quay lại. Không có lỗi nào báo ra — số liệu thống kê chỉ đơn giản là phình lên.

Cách sửa đúng là lọc NGAY TẠI BƯỚC NẠP chứ không phải dọn sau. Module này giữ phần logic
đó ở một chỗ, để script trích xuất và script gộp cùng đọc một bảng.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "entity_merges.json"

_cache: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    """Đọc bảng dọn. Thiếu file thì trả về bảng rỗng — dọn dẹp là tùy chọn."""
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _cache = {}
    return _cache


def alias_map() -> Dict[str, str]:
    """Tên biến thể -> tên chuẩn. So khớp không phân biệt hoa thường."""
    return {
        (item["from"] or "").strip().lower(): item["into"]
        for item in load_config().get("aliases", [])
        if item.get("from") and item.get("into")
    }


def noise_names() -> set:
    """Những tên KHÔNG phải đối tác kinh doanh, phải loại khỏi đồ thị."""
    return {
        (item["node"] or "").strip().lower()
        for item in load_config().get("remove", [])
        if item.get("node")
    }


def apply_curation(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, int]:
    """Đổi tên biến thể và bỏ bộ ba dính node nhiễu.

    Trả về (danh sách đã dọn, số đầu mút đã đổi tên, số bộ ba đã bỏ).

    Bỏ cả bộ ba khi MỘT TRONG HAI đầu là nhiễu — quan hệ "NVIDIA tài trợ Boys & Girls
    Clubs" không còn ý nghĩa khi đã quyết định rằng các tổ chức từ thiện không thuộc phạm
    vi phân tích đầu tư. Giữ lại một nửa còn tệ hơn: cạnh trỏ vào hư không.
    """
    aliases = alias_map()
    noise = noise_names()
    if not aliases and not noise:
        return rows, 0, 0

    kept: List[Dict[str, Any]] = []
    renamed = dropped = 0

    for row in rows:
        source = (row.get("source") or "").strip()
        target = (row.get("target") or "").strip()

        if source.lower() in noise or target.lower() in noise:
            dropped += 1
            continue

        new_source = aliases.get(source.lower())
        new_target = aliases.get(target.lower())
        if new_source:
            row = {**row, "source": new_source}
            renamed += 1
        if new_target:
            row = {**row, "target": new_target}
            renamed += 1

        # Sau khi đổi tên, hai đầu có thể trùng nhau ("GF" và "Global Foundries" cùng
        # thành một). Cạnh nối một node với chính nó không mang thông tin gì.
        if (row.get("source") or "").strip().lower() == (row.get("target") or "").strip().lower():
            dropped += 1
            continue

        kept.append(row)

    return kept, renamed, dropped
