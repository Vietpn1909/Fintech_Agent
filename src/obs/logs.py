"""Nhật ký hệ thống — để khi có chuyện, còn thứ để lần lại.

VÌ SAO CẦN, VÀ VÌ SAO KHÔNG PHẢI `print`

Trước tệp này, `grep -rn "import logging" src/ web/` trả về RỖNG. Mỗi câu trả lời có kèm
`trace` (các bước agent đã đi) nhưng nó chỉ sống trong một lần gọi rồi biến mất. Nghĩa là
khi có người dùng thật và một câu trả lời sai, không còn gì để lần lại xem agent đã gọi
công cụ nào với tham số gì.

Đó là vấn đề thật vì phần lớn lỗi nặng của dự án này đều IM LẶNG: hỏi "Sabeco" ra một
công ty UPCOM, hỏi cổ đông "SAB" ra một công ty Mỹ, "FPT Corp" báo không tìm thấy. Không
cái nào ném ngoại lệ. Chúng chỉ lộ ra khi có ai đó ngồi đọc từng câu trả lời — mà đó
không phải cách vận hành được.

GHI RA JSON LINES, KHÔNG PHẢI CHỮ TỰ DO

Mỗi dòng một đối tượng JSON, nên trả lời được những câu kiểu "trong tuần qua có bao nhiêu
câu hỏi bị lớp đối chiếu số bắt lỗi" bằng một lệnh `jq` thay vì đọc mắt. Chữ tự do đọc
thì dễ nhưng lọc thì không.

⚠️ BA THỨ CỐ Ý KHÔNG GHI

1. NỘI DUNG CÂU TRẢ LỜI. Nó dài (vài nghìn ký tự mỗi câu) và đã nằm sẵn trong
   `data/chat.db`. Ghi lại lần nữa chỉ làm log phình ra rồi không ai mở nữa.
2. KHÓA VÀ MẬT KHẨU. `settings` có `neo4j_password`, `llm_api_key`, `sec_user_agent`
   (chứa email). Không hàm nào ở đây nhận nguyên đối tượng settings.
3. TOÀN BỘ VĂN BẢN TÀI LIỆU lấy về. Chỉ ghi số lượng đoạn và mã doanh nghiệp.

Câu hỏi của người dùng thì CÓ ghi — không lần lại được câu hỏi thì không tái hiện được
lỗi, mà đó là toàn bộ lý do tệp này tồn tại. Ai không muốn vậy thì đặt
`LOG_QUESTIONS=false`.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "logs"

# Xoay vòng theo dung lượng chứ không theo ngày: máy này chạy theo đợt (vài trăm câu hỏi
# trong một buổi, rồi im vài ngày), nên xoay theo ngày sẽ đẻ ra một đống tệp rỗng.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


LOG_QUESTIONS = _bool_env("LOG_QUESTIONS", True)


class JsonLineFormatter(logging.Formatter):
    """Mỗi bản ghi một dòng JSON.

    Trường `extra` của `logging` được trải phẳng vào đối tượng, nên gọi
    `log.info("agent.answer", extra={"seconds": 4.2})` là đủ để có trường truy vấn được.
    """

    # Những khóa mà chính `logging` đặt vào bản ghi — không phải dữ liệu của ta.
    _BUILTIN = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
        "message", "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in self._BUILTIN and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            # Chỉ giữ dòng cuối của traceback: nó mang đúng loại lỗi và thông điệp, còn
            # toàn bộ ngăn xếp thì làm dòng log dài gấp mười mà hiếm khi cần tới.
            payload["error"] = self.formatException(record.exc_info).strip().split("\n")[-1]
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup(level: str = "INFO", to_console: bool = False) -> None:
    """Bật ghi log. Gọi nhiều lần cũng chỉ cấu hình một lần.

    `to_console=False` là mặc định có chủ đích: các script nạp dữ liệu đang dùng `rich`
    để vẽ bảng và thanh tiến trình, thêm dòng log vào đó sẽ phá nát màn hình.
    """
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.jsonl", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter())

    root = logging.getLogger("fingraph")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    # Không đẩy lên logger gốc: uvicorn cũng gắn handler ở đó, và mỗi dòng sẽ bị in hai lần.
    root.propagate = False

    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(console)

    _configured = True


def get(name: str = "app") -> logging.Logger:
    setup()
    return logging.getLogger(f"fingraph.{name}")


def new_trace_id() -> str:
    """Mã để nối các dòng log của CÙNG một câu hỏi lại với nhau.

    Không có nó thì log của hai câu hỏi chạy song song trộn vào nhau và không cách nào
    tách ra — mà web server có hẳn hàng đợi cho nhiều câu hỏi cùng lúc.
    """
    return uuid.uuid4().hex[:12]


def log_turn(question: str, result: Dict[str, Any], trace_id: str,
             session_id: Optional[str] = None, source: str = "api") -> None:
    """Ghi lại MỘT lượt hỏi–đáp: đi qua công cụ nào, mất bao lâu, số liệu có sạch không.

    Đây là dòng log quan trọng nhất của cả hệ thống. Nó phải trả lời được ba câu mà người
    vận hành sẽ hỏi khi có sự cố: agent đã gọi gì, mất bao lâu, và lớp đối chiếu số có
    kêu không.
    """
    log = get("agent")
    check = result.get("number_check") or {}
    steps = result.get("trace") or []
    unverified = check.get("unverified") or []

    log.info("agent.turn", extra={
        "trace_id": trace_id,
        "session_id": session_id,
        "source": source,
        "question": question if LOG_QUESTIONS else None,
        "seconds": result.get("seconds"),
        "rounds": result.get("rounds"),
        "tools": [s.get("tool") for s in steps if s.get("step") == "thực thi"],
        "tool_status": [s.get("status") for s in steps if s.get("step") == "thực thi"],
        "answer_chars": len(result.get("answer") or ""),
        "numbers_checked": check.get("checked"),
        # Con số nào không truy được về nguồn thì ghi CHUỖI của nó, không ghi cả câu trả
        # lời — đủ để tìm lại trong `chat.db`, mà không nhân đôi dữ liệu.
        "numbers_unverified": [u.get("text") for u in unverified],
        "unverified_reasons": sorted({u.get("reason") for u in unverified}),
    })


def log_tool(name: str, args: Dict[str, Any], status: str, seconds: float,
             trace_id: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    """Một lần gọi công cụ. `status` là thứ đáng giá nhất ở đây.

    Chính các trạng thái `ambiguous` / `company_not_found` / `no_data` là dấu vết của
    những lỗi im lặng: chúng không phải ngoại lệ, không ai thấy, nhưng đếm chúng theo
    thời gian là cách sớm nhất để phát hiện một nhánh dữ liệu đang hỏng.
    """
    get("tool").info("tool.call", extra={
        "trace_id": trace_id, "tool": name, "status": status,
        "seconds": round(seconds, 2),
        # ⚠️ KHÔNG đặt tên trường là "args": `logging.LogRecord` đã giữ tên đó cho tham
        # số định dạng của chính nó, và ghi đè sẽ ném KeyError ngay lúc gọi. Cùng họ với
        # "name", "module", "message" — những tên trông vô hại nhưng đã có chủ.
        #
        # Tham số thường ngắn (mã, năm, chỉ tiêu) nhưng `query` có thể dài — cắt bớt.
        "tool_args": {k: (v[:120] if isinstance(v, str) else v)
                      for k, v in (args or {}).items()},
        **(extra or {}),
    })


def log_ingest(step: str, status: str, **fields: Any) -> None:
    """Một bước nạp dữ liệu. Dùng cho các script trong `scripts/`."""
    get("ingest").info(f"ingest.{step}", extra={"status": status, **fields})
