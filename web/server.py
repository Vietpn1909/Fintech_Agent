"""Máy chủ web cho trợ lý phân tích — thay thế Streamlit.

VÌ SAO BỎ STREAMLIT

Streamlit chạy lại TOÀN BỘ file mỗi lần người dùng chạm vào bất cứ thứ gì. Với dự án này
có ba hệ quả thực tế:

  1. Không stream được. `ask()` mất 18-78 giây, và Streamlit chỉ vẽ được sau khi hàm trả
     về. Người dùng ngồi nhìn màn hình trắng — đúng thứ khiến demo trông như bị treo.
  2. Không nhúng được vào trang giới thiệu. Streamlit chiếm trọn cửa sổ, không có chỗ
     cho phần PR/quảng bá.
  3. Deploy khó. Nó cần WebSocket riêng, phiên bám vào tiến trình, và không đặt sau
     CDN/reverse-proxy thông thường được.

Kiến trúc thay thế: FastAPI phục vụ (a) một trang tĩnh và (b) một API JSON. Trang tĩnh
đẩy lên CDN nào cũng được; API là HTTP thuần. Câu trả lời đi về qua SSE (Server-Sent
Events) nên người dùng THẤY agent đang làm gì ngay khi nó làm.

SSE LÀ GÌ

Một kết nối HTTP mà máy chủ giữ mở và đẩy dần từng dòng `data: {...}` xuống. Đơn giản
hơn WebSocket nhiều (một chiều là đủ cho ta), và đi qua mọi proxy vì bản chất nó vẫn
chỉ là một response HTTP dài.

Chạy:
    .venv/Scripts/python.exe -m uvicorn web.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Import này PHẢI đứng trước mọi thứ in ra console: nó chỉnh stdout sang UTF-8, nếu
# không thì mọi log tiếng Việt sẽ làm sập tiến trình trên Windows.
from config.settings import settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Agentic GraphRAG — Phân tích Doanh nghiệp & Đầu tư",
    description="API cho trợ lý phân tích trên dữ liệu SEC EDGAR.",
    version="1.0.0",
)

# Cho phép nhúng khung demo từ tên miền khác (trang giới thiệu tách rời máy chủ API).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ nạp chậm

# Nạp Neo4j/Qdrant/LangGraph mất vài giây và cần Docker đang chạy. Nếu nạp lúc import thì
# máy chủ không khởi động nổi khi Docker chưa lên — trang giới thiệu chết theo, dù nó
# chẳng cần cơ sở dữ liệu nào. Nạp chậm để trang tĩnh luôn phục vụ được.

_backend_lock = threading.Lock()
_backend: Dict[str, Any] = {}


def backend() -> Dict[str, Any]:
    with _backend_lock:
        if not _backend:
            from src.agent import tools
            from src.agent.graph_agent import build_agent

            _backend["agent"] = build_agent()
            _backend["tools"] = tools
            _backend["graph"] = tools.graph()
            _backend["vectors"] = tools.vectors()
        return _backend


# Model local phục vụ tuần tự: hai câu hỏi cùng lúc không chạy nhanh gấp đôi, chúng tranh
# nhau KV cache và làm chậm cả hai. Xếp hàng tường minh, và báo cho người dùng biết họ
# đứng thứ mấy thay vì để họ nhìn màn hình đứng im.
_llm_gate = threading.Semaphore(1)
_waiting = threading.Lock()
_queue_depth = {"n": 0}


# ------------------------------------------------------------------ thống kê

_stats_cache: Dict[str, Any] = {"at": 0.0, "data": None}


def collect_stats() -> Dict[str, Any]:
    """Số liệu độ phủ cho trang chủ. Cache 60 giây — đây là truy vấn đếm toàn đồ thị."""
    if _stats_cache["data"] and time.time() - _stats_cache["at"] < 60:
        return _stats_cache["data"]

    store = backend()["graph"]
    raw = store.stats()
    nodes = {r["label"]: r["n"] for r in raw["nodes"]}
    rels = {r["type"]: r["n"] for r in raw["relationships"]}
    tiers = {
        r["tier"]: r["n"]
        for r in store.run(
            "MATCH (c:Company) RETURN coalesce(c.tier,'metrics') AS tier, count(*) AS n"
        )
    }

    # HAS_FINANCIALS và FILED là cạnh hạ tầng (nối công ty với bản ghi năm / hồ sơ), không
    # phải tri thức trích xuất được. Gộp chúng vào sẽ thổi phồng con số lên hàng chục lần.
    infra = ("HAS_FINANCIALS", "FILED")

    # ⚠️ HAI LOẠI CẠNH NÀY KHÔNG ĐƯỢC CỘNG CHUNG.
    #
    # `knowledge_edges` là quan hệ mô hình ĐỌC RA từ hồ sơ 10-K — mỗi cạnh tốn một lần gọi
    # LLM và có tỷ lệ sai. `ownership_edges` là quan hệ sở hữu lấy từ dữ liệu đã có cấu
    # trúc của VCI — không tốn lần gọi nào và không có chỗ để sai.
    #
    # Cộng chung thì con số nhảy từ 2.153 lên gần 19.000 và trang chủ sẽ ngầm khoe rằng
    # đồ thị trích xuất được lớn gấp chín lần thực tế. Đó đúng là kiểu đếm gộp mà
    # `companies` vừa phải tách ra để sửa.
    ownership_edges = rels.get("OWNED_BY", 0)
    knowledge_edges = sum(
        n for t, n in rels.items() if t not in infra and t != "OWNED_BY"
    )

    # ⚠️ TỔNG SỐ NODE Company KHÔNG PHẢI LÀ "SỐ DOANH NGHIỆP NIÊM YẾT TẠI MỸ".
    #
    # Nhãn Company đang gộp ba thứ khác hẳn nhau: 6.074 doanh nghiệp đăng ký với SEC (có
    # CIK), 30 doanh nghiệp niêm yết tại Việt Nam (market='VN', lấy số từ VCI chứ không
    # phải EDGAR), và 166 tổ chức do bước trích xuất đẻ ra vì có tên trong hồ sơ — Samsung,
    # Huawei, OpenAI, Azure — vốn không niêm yết tại Mỹ và không có một dòng số liệu nào.
    #
    # Trang chủ trước đây in thẳng tổng này kèm chữ "doanh nghiệp niêm yết tại Mỹ", nên
    # vừa cộng nhầm doanh nghiệp Việt Nam vừa cộng nhầm cả những cái tên chỉ được nhắc tới.
    # Tách ra ở đây để câu chữ ngoài giao diện nói đúng cái mà nó đang đếm.
    us = store.run("MATCH (c:Company) WHERE c.cik IS NOT NULL RETURN count(*) AS n")
    vn = store.run("MATCH (c:Company) WHERE c.market = 'VN' RETURN count(*) AS n")

    # ⚠️ "CÓ ĐỒ THỊ" GIỜ CÓ HAI NGHĨA KHÁC HẲN NHAU, VÀ MỘT CON SỐ KHÔNG NÓI ĐƯỢC CẢ HAI.
    #
    #   từ hồ sơ    mô hình đọc 10-K rồi trích quan hệ — cạnh thưa nhưng giàu ngữ nghĩa
    #               (cạnh tranh với ai, phụ thuộc nhà cung cấp nào, chịu rủi ro gì)
    #   từ sở hữu   bảng cổ đông VCI — cạnh dày nhưng chỉ nói đúng một điều: ai nắm bao
    #               nhiêu phần trăm của ai
    #
    # Sau khi nạp cổ đông, `tiers.graph` nhảy từ 95 lên 1.619. In thẳng con số đó kèm chữ
    # "doanh nghiệp có đồ thị" là ngầm khoe rằng đồ thị trích xuất từ hồ sơ đã lớn gấp 17
    # lần, trong khi nó vẫn đúng 95. Cùng loại đếm gộp mà `companies` phải tách ra.
    from_filings = store.run(
        """
        MATCH (c:Company) WHERE c.ticker IS NOT NULL
        OPTIONAL MATCH (c)-[r]-() WHERE NOT type(r) IN $infra AND type(r) <> 'OWNED_BY'
        WITH c, count(r) AS n WHERE n > 0
        RETURN count(c) AS n
        """,
        infra=list(infra),
    )
    from_ownership = store.run(
        "MATCH (c:Company)-[:OWNED_BY]-() RETURN count(DISTINCT c) AS n"
    )

    data = {
        "companies": nodes.get("Company", 0),
        "companies_us": us[0]["n"] if us else 0,
        "companies_vn": vn[0]["n"] if vn else 0,
        # Doanh nghiệp NIÊM YẾT — tức là có số liệu tài chính tra được. Cố ý không dùng
        # "doanh nghiệp có số liệu" làm nhãn: một phần nhỏ trong vũ trụ SEC nộp hồ sơ mà
        # không kèm XBRL nên không bóc được năm nào, gọi tên như vậy sẽ hứa hơi quá.
        "companies_listed": (us[0]["n"] if us else 0) + (vn[0]["n"] if vn else 0),
        "financial_years": nodes.get("FinancialYear", 0),
        "text_chunks": backend()["vectors"].count(),
        "knowledge_edges": knowledge_edges,
        "ownership_edges": ownership_edges,
        "relation_types": len([t for t in rels if t not in infra and t != "OWNED_BY"]),
        "tiers": {
            "metrics": tiers.get("metrics", 0),
            "text": tiers.get("text", 0),
            "graph": tiers.get("graph", 0),
            # Hai nguồn đồ thị, đếm riêng — xem chú thích ở trên
            "graph_from_filings": from_filings[0]["n"] if from_filings else 0,
            "graph_from_ownership": from_ownership[0]["n"] if from_ownership else 0,
        },
        "model": settings.llm_reasoning_model,
    }
    _stats_cache.update({"at": time.time(), "data": data})
    return data


# ------------------------------------------------------------------ điểm cuối


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    """Kiểm tra ba phụ thuộc RIÊNG BIỆT để báo đúng cái nào hỏng.

    Gộp thành một cờ ok/không-ok là vô dụng khi gỡ lỗi: người dùng cần biết phải đi bật
    Docker hay đi bật LM Studio.
    """
    status: Dict[str, Any] = {"neo4j": False, "qdrant": False, "llm": False, "detail": {}}

    try:
        backend()["graph"].run("RETURN 1 AS ok")
        status["neo4j"] = True
    except Exception as exc:  # noqa: BLE001
        status["detail"]["neo4j"] = str(exc)[:200]

    try:
        backend()["vectors"].count()
        status["qdrant"] = True
    except Exception as exc:  # noqa: BLE001
        status["detail"]["qdrant"] = str(exc)[:200]

    try:
        import httpx

        resp = httpx.get(f"{settings.llm_base_url}/models", timeout=5)
        status["llm"] = resp.status_code == 200
        if not status["llm"]:
            status["detail"]["llm"] = f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        status["detail"]["llm"] = str(exc)[:200]

    # Số câu hỏi đang chờ hoặc đang chạy. Giữ lại vì chính con số này đã lộ ra lỗi rò
    # rỉ cổng: nó đứng yên ở 1 trong khi không còn luồng agent nào chạy.
    status["in_flight"] = _queue_depth["n"]
    status["ready"] = all((status["neo4j"], status["qdrant"], status["llm"]))
    return status


@app.get("/api/stats")
def stats() -> Dict[str, Any]:
    try:
        return collect_stats()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Không kết nối được cơ sở dữ liệu: {str(exc)[:200]}")


@app.get("/api/coverage")
def coverage(q: str) -> Dict[str, Any]:
    """Tra xem hệ thống đang có gì về một doanh nghiệp."""
    if not q or len(q.strip()) < 2:
        raise HTTPException(400, "Cần ít nhất 2 ký tự")
    try:
        return backend()["tools"].company_coverage(q.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, str(exc)[:200])


# Nhãn tiếng Việt cho từng công cụ. Đặt ở máy chủ chứ không ở trình duyệt vì đây là mô tả
# hành vi backend — thêm công cụ mới thì chỉ sửa một chỗ.
TOOL_META = {
    "lookup_financials": {"label": "Tra số liệu XBRL", "icon": "table", "source": "Neo4j · XBRL"},
    "compare_financials": {"label": "So sánh doanh nghiệp", "icon": "scale", "source": "Neo4j · XBRL"},
    "screen_companies": {"label": "Sàng lọc toàn thị trường", "icon": "filter", "source": "Neo4j · XBRL"},
    "search_filings": {"label": "Tìm trong báo cáo 10-K", "icon": "search", "source": "Qdrant · vector"},
    "graph_neighbors": {"label": "Duyệt đồ thị tri thức", "icon": "graph", "source": "Neo4j · đồ thị"},
    "graph_path": {"label": "Tìm chuỗi liên kết", "icon": "path", "source": "Neo4j · đồ thị"},
    "company_coverage": {"label": "Kiểm tra độ phủ", "icon": "info", "source": "Neo4j"},
}

STEP_META = {
    "định tuyến": {"phase": "route", "label": "Định tuyến", "desc": "LLM chọn công cụ cần gọi"},
    "thực thi": {"phase": "execute", "label": "Thực thi", "desc": "Chạy truy vấn — không có LLM ở bước này"},
    "suy xét": {"phase": "reflect", "label": "Suy xét", "desc": "Dữ liệu thu được đã đủ chưa?"},
    "trả lời": {"phase": "compose", "label": "Viết câu trả lời", "desc": "LLM tổng hợp, bắt buộc dẫn nguồn"},
}


def _enrich(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Gắn nhãn hiển thị vào một mục dấu vết thô."""
    meta = STEP_META.get(entry.get("step", ""), {"phase": "other", "label": entry.get("step", "?"), "desc": ""})
    out = {**entry, **meta}
    tool = entry.get("tool")
    if tool:
        out["tool_meta"] = TOOL_META.get(tool, {"label": tool, "icon": "tool", "source": ""})
    return out


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def run_agent_stream(question: str) -> Iterator[str]:
    """Chạy agent ở luồng riêng và đẩy mọi sự kiện xuống trình duyệt qua một hàng đợi.

    HAI LÝ DO PHẢI TÁCH LUỒNG

    1. Câu trả lời sinh dần bên trong khối "trả lời", tức là ở SÂU trong lời gọi
       agent.stream(). Generator đang kẹt trong đó thì không có cơ hội yield, nên không
       thể đẩy từng mẩu chữ ra ngay được.

    2. ⚠️ QUAN TRỌNG HƠN: GENERATOR KHÔNG PHẢI CHỖ ĐỂ GIỮ TÀI NGUYÊN.

       Bản trước giữ semaphore trong generator và nhả ở `finally`. Nghe thì đúng, nhưng
       một generator chỉ chạy tiếp khi có người gọi `next()`. Người dùng đóng tab giữa
       chừng thì Starlette ngừng gọi `next()`, generator nằm yên mãi ở câu `yield` cuối
       cùng, và `finally` KHÔNG BAO GIỜ chạy.

       Đo thật: ngắt kết nối sau 3 giây rồi theo dõi 70 giây tiếp theo —
           +2s   gate_free=0  queue=1  threads=['agent-run']
           +10s  gate_free=0  queue=1  threads=[]      <- agent xong rồi
           +70s  gate_free=0  queue=1  threads=[]      <- cổng vẫn kẹt
       Agent đã chạy xong từ lâu mà cổng vẫn bị giữ, nên MỌI câu hỏi sau đó đều xếp hàng
       vĩnh viễn. Một người đóng tab là cả máy chủ chết.

       Cách sửa: luồng agent tự giữ và tự nhả cổng. Luồng thường luôn chạy hết tới
       `finally` bất kể phía tiêu thụ còn nghe hay không. Generator ở đây không giữ gì
       cả — bỏ rơi nó lúc nào cũng an toàn.

    Vì sao không viết async: các thư viện bên dưới (neo4j driver, openai client,
    fastembed) đều đồng bộ. Bọc chúng trong `async def` mà không await gì sẽ chặn event
    loop và làm đứng toàn bộ máy chủ, kể cả trang tĩnh.
    """
    events: "queue.Queue[Optional[tuple]]" = queue.Queue()

    def worker() -> None:
        started = time.time()
        acquired = False
        counted = False
        try:
            with _waiting:
                _queue_depth["n"] += 1
                position = _queue_depth["n"]
            counted = True

            if position > 1:
                events.put(("queued", position - 1))

            acquired = _llm_gate.acquire(timeout=600)
            if not acquired:
                events.put(("fatal", "Máy chủ đang quá tải, thử lại sau."))
                return

            events.put(("start", question))

            try:
                agent = backend()["agent"]
            except Exception as exc:  # noqa: BLE001
                events.put(("fatal", f"Không khởi tạo được backend: {str(exc)[:200]}",
                            "Chạy `docker compose up -d` để bật Neo4j và Qdrant."))
                return

            sent = 0
            final: Dict[str, Any] = {}
            for update in agent.stream(
                {
                    "question": question, "round": 0, "observations": [], "trace": [],
                    "on_token": lambda piece: events.put(("token", piece)),
                },
                stream_mode="updates",
            ):
                for _node, delta in update.items():
                    if not isinstance(delta, dict):
                        continue
                    final = delta
                    trace = delta.get("trace") or []
                    while sent < len(trace):
                        events.put(("step", _enrich(trace[sent])))
                        sent += 1

            answer = final.get("answer") or ""
            if not answer:
                events.put(("fatal", "Agent không tạo được câu trả lời.",
                            "Thường do LLM trả về rỗng khi hết token. Thử hỏi ngắn gọn hơn."))
                return

            # Gửi lại toàn văn dù đã đẩy từng mẩu: trình duyệt dựng lại Markdown một lần
            # cuối từ bản đầy đủ, nên không lệ thuộc vào việc ghép các mẩu có chuẩn hay
            # không. Client cũ chỉ nghe `answer` cũng vẫn chạy đúng.
            events.put(("answer", answer))
            events.put(("done", round(time.time() - started, 1), final.get("round", 0)))

        except Exception as exc:  # noqa: BLE001
            events.put(("error", exc))
        finally:
            # Cả ba việc dọn dẹp đều nằm ở đây, trong một luồng thường — nơi `finally`
            # chắc chắn chạy, khác hẳn generator.
            if acquired:
                _llm_gate.release()
            if counted:
                with _waiting:
                    _queue_depth["n"] -= 1
            events.put(None)

    threading.Thread(target=worker, daemon=True, name="agent-run").start()

    while True:
        item = events.get()
        if item is None:
            break
        kind = item[0]

        if kind == "token":
            yield _sse({"type": "token", "text": item[1]})
        elif kind == "step":
            yield _sse({"type": "step", "data": item[1]})
        elif kind == "queued":
            yield _sse({"type": "queued", "position": item[1]})
        elif kind == "start":
            yield _sse({"type": "start", "question": item[1]})
        elif kind == "answer":
            yield _sse({"type": "answer", "text": item[1]})
        elif kind == "done":
            yield _sse({"type": "done", "seconds": item[1], "rounds": item[2]})
        elif kind == "fatal":
            yield _sse({"type": "error", "message": item[1],
                        "hint": item[2] if len(item) > 2 else ""})
        elif kind == "error":
            message = str(item[1])[:300]
            low = message.lower()
            hint = ""
            if any(k in low for k in ("connect", "refused", "timeout", "10061")):
                hint = ("Kiểm tra LM Studio đã bật server chưa (tab Developer → Start "
                        "Server), và Docker đã chạy `docker compose up -d` chưa.")
            yield _sse({"type": "error", "message": message, "hint": hint})


@app.post("/api/ask")
def ask(req: AskRequest) -> StreamingResponse:
    return StreamingResponse(
        run_agent_stream(req.question.strip()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx mặc định gom buffer response, khiến SSE chỉ tới nơi khi đã xong hết —
            # tức là mất sạch ý nghĩa của streaming. Header này tắt hành vi đó.
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/ask-sync")
def ask_sync(req: AskRequest) -> Dict[str, Any]:
    """Bản không streaming — cho tích hợp máy-với-máy và cho script chấm điểm."""
    from src.agent.graph_agent import ask as agent_ask

    with _llm_gate:
        try:
            result = agent_ask(req.question.strip())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, str(exc)[:300])
    result["trace"] = [_enrich(t) for t in result.get("trace", [])]
    return result


# ------------------------------------------------------------------ trang tĩnh

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Hai trang, hai việc rời nhau. Trang chủ giới thiệu hệ thống và tri thức nó đang có;
# trang /chat chỉ để hỏi đáp, không có gì khác trên màn hình. Gộp cả hai vào một trang
# khiến người mới vào phải đọc qua tài liệu mới tới được ô nhập, còn người quay lại lần
# thứ hai thì phải cuộn qua phần họ đã đọc rồi.


# Trang HTML KHÔNG được cache.
#
# Đã mất công tìm ra lỗi này: sau khi đổi trang chủ từ một-trang sang hai-trang, trình
# duyệt vẫn hiện bản cũ vì nó giữ HTML trong cache và không hỏi lại máy chủ. Người dùng
# thấy giao diện y hệt cũ và tưởng thay đổi chưa được áp dụng.
#
# File tĩnh (CSS/JS) cache được vì đổi tên là xong; còn HTML là điểm vào, nó phải luôn
# mới. no-store mạnh hơn no-cache: không lưu bản nào, kể cả để đối chiếu.
NO_CACHE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)


@app.get("/chat")
def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "chat.html", headers=NO_CACHE)
