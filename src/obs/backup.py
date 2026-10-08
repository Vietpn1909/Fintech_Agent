"""Sao lưu và khôi phục Neo4j + Qdrant.

VÌ SAO ĐÂY LÀ THỨ THIẾU QUAN TRỌNG NHẤT CỦA HỆ THỐNG

Mọi thứ khác trong dự án đều dựng lại được từ mã nguồn và dữ liệu nguồn — trừ một thứ:
hơn tám giờ chạy nhúng vector. 50.214 đoạn báo cáo thường niên tiếng Việt, 23.869 đoạn
10-K, 1.527 đoạn mô tả doanh nghiệp. Mất là phải chạy lại từ đầu, và máy phải bật suốt.

⚠️ TÁM GIỜ LÀ CHI PHÍ TẠO LẠI, KHÔNG PHẢI THỜI GIAN SAO LƯU

Đo thật trên chính máy này: Neo4j 879 MB nén còn 72 MB trong 6 giây, Qdrant 1,08 GB nén
còn 208 MB trong 13 giây. Toàn bộ một lần sao lưu dưới một phút. Nhầm hai con số này với
nhau là lý do người ta trì hoãn việc sao lưu.

⚠️ HAI KHO, HAI CÁCH, VÀ KHÔNG ĐƯỢC LÀM GIỐNG NHAU

    Qdrant   có API snapshot, chụp được KHI ĐANG CHẠY, không gián đoạn gì
    Neo4j    bản Community KHÔNG có sao lưu nóng — phải dừng container khoảng 20 giây

Chép thẳng thư mục dữ liệu Neo4j lúc nó đang chạy thì vẫn ra tệp, kích thước trông hợp
lý, không có lỗi nào báo ra. Nhưng nếu đúng lúc đó Neo4j đang ghi dở một trang thì bản
sao ấy hỏng, và chỉ phát hiện ra vào ngày cần khôi phục. Đúng loại lỗi im lặng mà cả dự
án này được dựng lên để chặn, nên ở đây dùng `neo4j-admin database dump` trên container
đã dừng — chậm hơn 20 giây, đổi lấy một bản chắc chắn đọc được.

⚠️ MỘT BẢN SAO LƯU CHƯA TỪNG KHÔI PHỤC THÀNH CÔNG THÌ CHƯA PHẢI BẢN SAO LƯU

Nó chỉ là một tệp nằm đó. Vì vậy mỗi bản đi kèm `manifest.json` ghi SỐ ĐẾM THẬT tại thời
điểm chụp — bao nhiêu doanh nghiệp, bao nhiêu năm tài chính, bao nhiêu đoạn vector mỗi
kho. Sau khi khôi phục, `verify_restore()` đếm lại và đối chiếu. Không có bước đó thì
"khôi phục xong" cũng chỉ là lời nói, y như "cập nhật xong" ở `freshness.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DEST = ROOT / "backups"

NEO4J_IMAGE = "neo4j:5.26-community"
NEO4J_VOLUME = "fintechagent_neo4j_data"
NEO4J_SERVICE = "neo4j"
# `system` giữ người dùng và vai trò. Nhỏ xíu (20 KB) nhưng thiếu nó thì bản khôi phục
# không phải là bản sao đầy đủ của máy chủ, chỉ là bản sao của dữ liệu.
NEO4J_DATABASES = ["neo4j", "system"]

QDRANT_URL = "http://localhost:6333"

# Chờ container khỏe lại sau khi bật. Không chờ thì lệnh đếm số ngay sau đó sẽ hỏng, và
# người dùng nhận một lỗi kết nối trông như sao lưu thất bại trong khi nó đã xong.
HEALTH_TIMEOUT = 180


def _run(args: List[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    """Chạy lệnh ngoài. Dùng danh sách tham số, KHÔNG dùng chuỗi shell.

    Đường dẫn dự án có dấu cách ("D:\\FinTech Agent"). Qua shell thì dấu cách ấy cắt
    tham số làm đôi; qua danh sách thì không có bước tách nào để sai.
    """
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def _docker_compose(*args: str) -> subprocess.CompletedProcess:
    return _run(["docker", "compose", *args], timeout=300)


def _neo4j_healthy() -> bool:
    out = _run(["docker", "ps", "--filter", "name=fintech-neo4j", "--format", "{{.Status}}"],
               timeout=60)
    return "healthy" in (out.stdout or "")


def _wait_healthy(timeout: int = HEALTH_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _neo4j_healthy():
            return True
        time.sleep(3)
    return False


def collections() -> List[str]:
    resp = requests.get(f"{QDRANT_URL}/collections", timeout=30)
    resp.raise_for_status()
    return [c["name"] for c in resp.json()["result"]["collections"]]


# --------------------------------------------------------------------------- sao lưu


def _dump_neo4j(dest: Path, log) -> Dict[str, Any]:
    """Dump Neo4j. Container PHẢI dừng — xem cảnh báo đầu tệp.

    ⚠️ KHỐI `finally` LÀ PHẦN QUAN TRỌNG NHẤT CỦA HÀM NÀY.

    Nếu dump hỏng giữa chừng mà không bật lại container thì cơ sở dữ liệu nằm chết, và
    người dùng chỉ phát hiện khi hỏi agent và nhận "backend_unavailable". Một lần sao lưu
    thất bại không được phép biến thành một hệ thống chết.
    """
    out: Dict[str, Any] = {"databases": {}}
    log("  dừng Neo4j...")
    stopped = _docker_compose("stop", NEO4J_SERVICE)
    if stopped.returncode != 0:
        raise RuntimeError(f"không dừng được Neo4j: {stopped.stderr[:300]}")

    try:
        for name in NEO4J_DATABASES:
            started = time.time()
            proc = _run([
                "docker", "run", "--rm",
                "-v", f"{NEO4J_VOLUME}:/data",
                # Docker trên Windows nhận đường dẫn kiểu Windows; truyền qua danh sách
                # tham số nên dấu cách trong "FinTech Agent" không bị cắt.
                "-v", f"{dest}:/backups",
                NEO4J_IMAGE,
                "neo4j-admin", "database", "dump", name,
                "--to-path=/backups", "--overwrite-destination=true",
            ])
            if proc.returncode != 0:
                raise RuntimeError(f"dump {name} hỏng: {(proc.stderr or proc.stdout)[:400]}")
            path = dest / f"{name}.dump"
            size = path.stat().st_size if path.exists() else 0
            out["databases"][name] = {"file": f"{name}.dump", "bytes": size}
            log(f"  {name}.dump · {size / 1e6:.1f} MB · {time.time() - started:.1f}s")
    finally:
        log("  bật lại Neo4j...")
        _docker_compose("start", NEO4J_SERVICE)
        out["healthy_after"] = _wait_healthy()
    return out


def _snapshot_qdrant(dest: Path, log) -> Dict[str, Any]:
    """Snapshot từng collection qua API, tải về, rồi XÓA bản trong container.

    ⚠️ PHẢI XÓA SAU KHI TẢI. Snapshot nằm lại trong volume của Qdrant, nên không xóa thì
    mỗi lần sao lưu lại thổi volume phồng thêm bằng đúng kích thước dữ liệu — và lần sao
    lưu sau sẽ chép cả đống snapshot cũ đó.
    """
    folder = dest / "qdrant"
    folder.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {"collections": {}}

    for name in collections():
        started = time.time()
        created = requests.post(f"{QDRANT_URL}/collections/{name}/snapshots", timeout=1800)
        created.raise_for_status()
        snap = created.json()["result"]["name"]
        try:
            path = folder / f"{name}.snapshot"
            with requests.get(f"{QDRANT_URL}/collections/{name}/snapshots/{snap}",
                              stream=True, timeout=1800) as resp:
                resp.raise_for_status()
                with path.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            size = path.stat().st_size
            out["collections"][name] = {"file": f"qdrant/{name}.snapshot", "bytes": size}
            log(f"  {name} · {size / 1e6:.1f} MB · {time.time() - started:.1f}s")
        finally:
            # Trong `finally`: tải hỏng thì vẫn phải dọn, nếu không volume phình dần mà
            # không ai thấy.
            requests.delete(f"{QDRANT_URL}/collections/{name}/snapshots/{snap}", timeout=300)
    return out


def create(dest: Optional[Path] = None, keep: int = 7, log=print) -> Dict[str, Any]:
    """Tạo một bản sao lưu đầy đủ. Trả về manifest."""
    from src.obs import freshness

    dest = Path(dest or DEFAULT_DEST)
    stamp = time.strftime("%Y-%m-%d_%H%M")
    folder = dest / stamp
    folder.mkdir(parents=True, exist_ok=True)

    started = time.time()
    log(f"Sao lưu vào {folder}")

    # ⚠️ ĐẾM TRƯỚC KHI CHỤP, không phải sau.
    #
    # Những con số này là thứ duy nhất chứng minh được bản khôi phục có đúng không. Đếm
    # sau khi đã dump thì chúng mô tả trạng thái sau, có thể đã khác.
    log("  đếm trạng thái hiện tại...")
    counts = freshness.snapshot()

    log("Qdrant (không gián đoạn):")
    qdrant = _snapshot_qdrant(folder, log)

    log("Neo4j (dừng khoảng 20 giây):")
    neo4j = _dump_neo4j(folder, log)

    manifest = {
        "stamp": stamp,
        "created_at": time.time(),
        "created_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.time() - started, 1),
        "counts": counts,
        "neo4j": neo4j,
        "qdrant": qdrant,
        "total_bytes": sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()),
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    removed = rotate(dest, keep)
    if removed:
        log(f"  xoay vòng: đã xóa {len(removed)} bản cũ ({', '.join(removed)})")

    log(f"Xong · {manifest['total_bytes'] / 1e6:.1f} MB · {manifest['seconds']} giây")
    return manifest


def rotate(dest: Path, keep: int) -> List[str]:
    """Giữ lại `keep` bản mới nhất. Trả về danh sách bản đã xóa.

    ⚠️ `keep` nhỏ hơn 1 bị từ chối. Một tham số gõ nhầm không được phép xóa sạch mọi bản
    sao lưu — đó là cách biến công cụ cứu hộ thành công cụ phá hoại.
    """
    if keep < 1:
        raise ValueError("keep phải từ 1 trở lên — từ chối xóa toàn bộ bản sao lưu")
    existing = sorted(listing(dest), key=lambda b: b["stamp"], reverse=True)
    removed = []
    for item in existing[keep:]:
        shutil.rmtree(item["path"])
        removed.append(item["stamp"])
    return removed


def listing(dest: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Các bản sao lưu đang có. Bản thiếu manifest bị coi là HỎNG, không im lặng bỏ qua."""
    dest = Path(dest or DEFAULT_DEST)
    if not dest.exists():
        return []
    out = []
    for folder in sorted(dest.iterdir()):
        if not folder.is_dir():
            continue
        path = folder / "manifest.json"
        if not path.exists():
            out.append({"stamp": folder.name, "path": folder, "broken": True,
                        "why": "thiếu manifest.json — bản sao lưu dở dang"})
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            out.append({"stamp": folder.name, "path": folder, "broken": True,
                        "why": f"manifest hỏng: {exc}"})
            continue
        out.append({"stamp": folder.name, "path": folder, "broken": False, **manifest})
    return out


def check_files(folder: Path) -> List[str]:
    """Đối chiếu tệp trên đĩa với manifest. Trả về danh sách vấn đề (rỗng là lành).

    Không mở dữ liệu ra đọc — chỉ kiểm tệp có đủ và đúng kích thước không. Đây là phép
    kiểm rẻ chạy được mỗi ngày; phép kiểm thật là khôi phục rồi đếm lại.
    """
    path = folder / "manifest.json"
    if not path.exists():
        return ["thiếu manifest.json"]
    manifest = json.loads(path.read_text(encoding="utf-8"))

    problems = []
    entries = list(manifest.get("neo4j", {}).get("databases", {}).items())
    entries += list(manifest.get("qdrant", {}).get("collections", {}).items())
    for name, info in entries:
        target = folder / info["file"]
        if not target.exists():
            problems.append(f"{name}: thiếu tệp {info['file']}")
        elif target.stat().st_size != info["bytes"]:
            problems.append(f"{name}: kích thước lệch "
                            f"({target.stat().st_size} ≠ {info['bytes']} trong manifest)")
    if not manifest.get("neo4j", {}).get("healthy_after", True):
        problems.append("Neo4j không khỏe lại sau lần sao lưu này — bản dump có thể dở dang")
    return problems


# ------------------------------------------------------------------------ khôi phục


def restore(stamp: str, dest: Optional[Path] = None, log=print) -> Dict[str, Any]:
    """Khôi phục một bản sao lưu. GHI ĐÈ dữ liệu hiện tại.

    ⚠️ ĐÂY LÀ THAO TÁC PHÁ HỦY VÀ KHÔNG HOÀN TÁC ĐƯỢC.

    Hàm này không tự hỏi xác nhận — việc đó thuộc về lớp giao diện (`scripts/23_backup.py`
    bắt gõ đúng mốc thời gian). Đặt câu hỏi xác nhận ở đây thì mọi lần gọi tự động đều
    treo chờ một câu trả lời không bao giờ tới.
    """
    dest = Path(dest or DEFAULT_DEST)
    folder = dest / stamp
    if not folder.exists():
        raise FileNotFoundError(f"không có bản sao lưu {stamp} trong {dest}")

    problems = check_files(folder)
    if problems:
        # Khôi phục từ một bản đã biết là hỏng sẽ phá dữ liệu đang chạy để lấy về một
        # thứ còn tệ hơn. Dừng lại trước khi động vào gì.
        raise RuntimeError("bản sao lưu không toàn vẹn, KHÔNG khôi phục: "
                           + "; ".join(problems))

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    started = time.time()

    # --- Neo4j: load cần container dừng, y như dump ---
    log("  dừng Neo4j...")
    _docker_compose("stop", NEO4J_SERVICE)
    try:
        for name in manifest.get("neo4j", {}).get("databases", {}):
            proc = _run([
                "docker", "run", "--rm",
                "-v", f"{NEO4J_VOLUME}:/data",
                "-v", f"{folder}:/backups",
                NEO4J_IMAGE,
                "neo4j-admin", "database", "load", name,
                "--from-path=/backups", "--overwrite-destination=true",
            ])
            if proc.returncode != 0:
                raise RuntimeError(f"load {name} hỏng: {(proc.stderr or proc.stdout)[:400]}")
            log(f"  đã nạp lại {name}")
    finally:
        log("  bật lại Neo4j...")
        _docker_compose("start", NEO4J_SERVICE)
        healthy = _wait_healthy()
    if not healthy:
        raise RuntimeError("Neo4j không khỏe lại sau khi khôi phục")

    # --- Qdrant: nạp snapshot qua API, không cần dừng ---
    for name, info in manifest.get("qdrant", {}).get("collections", {}).items():
        path = folder / info["file"]
        with path.open("rb") as fh:
            resp = requests.post(
                f"{QDRANT_URL}/collections/{name}/snapshots/upload?priority=snapshot",
                files={"snapshot": (path.name, fh)}, timeout=3600,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"nạp lại {name} hỏng: {resp.text[:300]}")
        log(f"  đã nạp lại {name}")

    return {"stamp": stamp, "seconds": round(time.time() - started, 1),
            "expected_counts": manifest.get("counts", {})}


def verify_restore(expected: Dict[str, Any]) -> Dict[str, Any]:
    """Đếm lại sau khi khôi phục và đối chiếu với số đã ghi lúc chụp.

    Xem cảnh báo đầu tệp: không có bước này thì "khôi phục xong" chỉ là lời nói.
    """
    from src.obs import freshness

    actual = freshness.snapshot()
    rows, ok = [], True
    for key, want in expected.items():
        got = actual.get(key)
        match = (got == want)
        ok = ok and match
        rows.append({"chi_tieu": key, "truoc_khi_sao_luu": want,
                     "sau_khi_khoi_phuc": got, "khop": match})
    return {"ok": ok, "rows": rows}
