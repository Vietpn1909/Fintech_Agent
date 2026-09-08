"""Khởi động giao diện web.

    .venv/Scripts/python.exe run_web.py              # chỉ máy này truy cập được
    .venv/Scripts/python.exe run_web.py --lan        # cả máy khác trong mạng LAN
    .venv/Scripts/python.exe run_web.py --dev        # tự nạp lại khi sửa code

Script này chỉ là lớp bọc mỏng quanh uvicorn. Nó tồn tại vì hai lý do rất thực tế:

  1. Kiểm tra phụ thuộc TRƯỚC khi mở cổng. Nếu Docker chưa chạy hoặc LM Studio chưa bật
     server, người dùng sẽ thấy thông báo rõ ràng ngay ở terminal, thay vì mở trang lên
     rồi mới nhận một lỗi khó hiểu sau 30 giây chờ.
  2. Nhắc đúng lệnh cần chạy để sửa. Đây là dự án có bốn tiến trình rời (Neo4j, Qdrant,
     LM Studio, máy chủ web) — quên bật một cái là chuyện thường.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import settings  # chỉnh stdout sang UTF-8, phải import sớm


def check() -> bool:
    """Kiểm tra ba phụ thuộc, in ra cái nào thiếu. Trả về True nếu đủ cả ba."""
    ok = True

    def probe(name: str, fn, fix: str) -> None:
        nonlocal ok
        try:
            fn()
            print(f"  [OK]     {name}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [THIEU]  {name} — {str(exc)[:90]}")
            print(f"           => {fix}")

    print("Kiem tra phu thuoc:")

    probe(
        "Neo4j   (do thi tri thuc)",
        lambda: __import__("neo4j").GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        ).verify_connectivity(),
        "docker compose up -d",
    )
    probe(
        "Qdrant  (chi muc vector)",
        lambda: __import__("httpx").get(f"{settings.qdrant_url}/collections", timeout=5).raise_for_status(),
        "docker compose up -d",
    )
    probe(
        "LM Studio (mo hinh ngon ngu)",
        lambda: __import__("httpx").get(f"{settings.llm_base_url}/models", timeout=5).raise_for_status(),
        "Mo LM Studio -> tab Developer -> Start Server",
    )
    return ok


def lan_ip() -> str:
    """Địa chỉ LAN của máy này.

    Không gửi gói tin nào thật: chỉ tạo socket UDP tới một địa chỉ ngoài để hệ điều hành
    chọn giúp card mạng nào sẽ được dùng, rồi đọc địa chỉ của card đó. Đây là cách duy
    nhất đáng tin khi máy có nhiều card (Wi-Fi, Ethernet, WSL, Docker, VPN...).
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def main() -> None:
    ap = argparse.ArgumentParser(description="Chay giao dien web FinGraph")
    ap.add_argument("--lan", action="store_true", help="cho may khac trong mang LAN truy cap")
    ap.add_argument("--dev", action="store_true", help="tu nap lai khi sua code")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--skip-check", action="store_true", help="bo qua buoc kiem tra phu thuoc")
    args = ap.parse_args()

    if not args.skip_check:
        healthy = check()
        print()
        if not healthy:
            # Vẫn cho chạy: trang giới thiệu không cần cơ sở dữ liệu nào, và chấm dứt ở
            # đây sẽ chặn cả việc xem giao diện lúc chưa bật máy. Chỉ cảnh báo cho rõ.
            print("Thieu phu thuoc — trang van mo duoc nhung KHONG tra loi duoc cau hoi.\n")

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"  Truy cap:  http://localhost:{args.port}")
    if args.lan:
        print(f"  Trong LAN: http://{lan_ip()}:{args.port}")
    print(f"  API docs:  http://localhost:{args.port}/docs\n")

    import uvicorn

    uvicorn.run("web.server:app", host=host, port=args.port, reload=args.dev, log_level="info")


if __name__ == "__main__":
    main()
