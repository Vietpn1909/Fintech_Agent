"""Agent định tuyến bằng LangGraph.

LANGGRAPH LÀ GÌ (giải thích cho người chưa dùng bao giờ)

Hãy hình dung một sơ đồ khối. Mỗi KHỐI (node) là một hàm Python nhận vào một cuốn sổ
tay chung (state) và ghi thêm vào đó. Mỗi MŨI TÊN (edge) quyết định đi tiếp sang khối
nào. LangGraph lo việc chạy đúng thứ tự và truyền cuốn sổ đi.

Khác biệt so với một chuỗi hàm gọi nối tiếp là ở MŨI TÊN CÓ ĐIỀU KIỆN: khối "suy xét"
được phép quyết định quay lại khối "thực thi" để lấy thêm dữ liệu. Vòng lặp đó chính
là thứ biến một pipeline thành một agent.

SƠ ĐỒ CỦA HỆ NÀY

        [định tuyến] --> [thực thi công cụ] --> [suy xét]
                              ▲                    │
                              └──── thiếu dữ liệu ─┤
                                                   ▼
                                              [trả lời]

    định tuyến   LLM đọc câu hỏi, chọn công cụ nào cần gọi và điền tham số.
    thực thi     Mã Python thuần chạy các công cụ đó. Không có LLM ở đây.
    suy xét      LLM xem dữ liệu thu được đã đủ trả lời chưa. Chưa đủ thì quay lại.
    trả lời      LLM viết câu trả lời, bắt buộc dẫn nguồn.

BA NGUYÊN TẮC THIẾT KẾ

1. LLM CHỈ CHỌN, KHÔNG TỰ LẤY DỮ LIỆU. Mọi con số và trích dẫn đều do mã lệnh lấy về.
   LLM không bao giờ được phép "nhớ" doanh thu của một công ty.

2. GIỚI HẠN SỐ VÒNG LẶP. Tối đa 3 vòng thực thi. Model local mà thả tự do sẽ lặp vô
   hạn khi gặp câu hỏi nó không giải được, và người dùng ngồi chờ mãi không có gì.

3. THÀ NÓI KHÔNG BIẾT CÒN HƠN BỊA. Prompt trả lời yêu cầu nói rõ khi dữ liệu không đủ,
   và nói rõ phạm vi phủ (công ty này mới chỉ có số liệu, chưa có văn bản...).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from config.settings import settings
from src.agent import tools
from src.ingest.xbrl import METRIC_LABELS
from src.llm.client import chat, chat_json, chat_stream

MAX_ROUNDS = 3

# ---------------------------------------------------------------- khai báo công cụ

# Mô tả công cụ cho LLM. Viết bằng tiếng Anh vì model local hiểu chỉ dẫn tiếng Anh tốt
# hơn đáng kể, dù câu hỏi và câu trả lời cuối cùng đều bằng tiếng Việt.
TOOL_SPECS = {
    "lookup_financials": {
        "desc": "Exact financial figures for ONE company from SEC XBRL data. Use for ANY question about numbers.",
        "args": {"company": "str", "metrics": "list[str] optional", "years": "list[int] optional"},
    },
    "compare_financials": {
        "desc": "Compare one metric across SEVERAL companies. Use for 'which company has more X'.",
        "args": {"companies": "list[str]", "metric": "str", "year": "int optional"},
    },
    "screen_companies": {
        "desc": "Filter all ~6000 listed companies by numeric criteria. Use for 'which companies have revenue over X'.",
        "args": {"filters": "list of {metric, op(gt/gte/lt/lte/eq), value}", "fiscal_year": "int", "order_by": "str"},
    },
    "search_filings": {
        "desc": "Semantic search over 10-K text. Use for qualitative questions: strategy, risks, competition, what management said.",
        "args": {
            "query": "str — search in ENGLISH, the filings are English",
            "companies": "list[str] optional",
            "items": 'list of STRINGS optional, e.g. ["1A"] — "1"=Business, "1A"=Risk Factors, "7"=MD&A, "3"=Legal. Risk/competition questions -> use "1A". Never pass numbers.',
        },
    },
    "graph_neighbors": {
        "desc": "Entities directly connected to one entity in the knowledge graph (competitors, suppliers, segments).",
        "args": {"entity": "str", "relations": "list[str] optional"},
    },
    "graph_path": {
        "desc": "Find the chain of connections between TWO entities. Use for 'how is A related to B' questions.",
        "args": {"source": "str", "target": "str"},
    },
    "company_coverage": {
        "desc": "Check what data the system actually has about a company. Use when unsure whether a company is indexed.",
        "args": {"company": "str"},
    },
}

TOOL_FUNCTIONS = {
    "lookup_financials": tools.lookup_financials,
    "compare_financials": tools.compare_financials,
    "screen_companies": tools.screen_companies,
    "search_filings": tools.search_filings,
    "graph_neighbors": tools.graph_neighbors,
    "graph_path": tools.graph_path,
    "company_coverage": tools.company_coverage,
}

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOL_SPECS.keys())},
                    "args": {"type": "object"},
                },
                "required": ["tool", "args"],
            },
        },
    },
    "required": ["reasoning", "calls"],
}

_TOOL_MENU = "\n".join(
    f"- {name}({', '.join(f'{k}: {v}' for k, v in spec['args'].items())})\n    {spec['desc']}"
    for name, spec in TOOL_SPECS.items()
)

ROUTER_PROMPT = f"""You are the routing brain of a financial analysis agent over SEC filings.

Your ONLY job is to choose which tools to call and with what arguments.
You must NEVER answer from your own knowledge — you have no reliable memory of financial
figures. Every fact must come from a tool.

Available tools:
{_TOOL_MENU}

Valid metric names for financial tools:
{', '.join(METRIC_LABELS.keys())}

Routing rules:
- Question mentions a specific number, figure, revenue, profit, growth  -> lookup_financials
- Question compares named companies                                     -> compare_financials
- Question asks "which companies..." with numeric criteria              -> screen_companies
- Question about strategy, risk, competition, management commentary     -> search_filings
- Question asks how two things are connected, or asks about supply chain
  / competitors / partners as a network                                 -> graph_path or graph_neighbors
- A question can need SEVERAL tools. Issue them all at once.
- Keep it to at most 3 calls per round.
- search_filings: write the `query` in ENGLISH even when the user asks in Vietnamese —
  the filings and the embedding model are English. Pass `items` as strings like ["1A"].

Output JSON only."""

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "missing": {"type": "string"},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOL_SPECS.keys())},
                    "args": {"type": "object"},
                },
                "required": ["tool", "args"],
            },
        },
    },
    "required": ["sufficient"],
}

ANSWER_PROMPT = """Bạn là trợ lý phân tích doanh nghiệp và đầu tư.

Viết câu trả lời bằng TIẾNG VIỆT, dựa HOÀN TOÀN vào dữ liệu công cụ trả về bên dưới.

Quy tắc bắt buộc:
1. Không được đưa ra bất kỳ con số nào không có trong dữ liệu công cụ. Nếu dữ liệu
   không có, hãy nói thẳng là không có.
2. Mỗi con số phải ghi rõ năm tài chính. Số liệu lấy từ XBRL do doanh nghiệp khai báo
   với SEC — có thể nói rõ điều đó để người đọc biết mức độ tin cậy.
3. Khi trích nội dung từ báo cáo, ghi rõ mã công ty, năm và mục (ví dụ: NVDA FY2026,
   Item 1A). Khi đi qua đồ thị tri thức, nêu lại chuỗi liên kết và bằng chứng.
4. Nếu công cụ báo có doanh nghiệp thiếu chỉ tiêu (trường missing_metric), PHẢI nói rõ
   là so sánh không đầy đủ và thiếu ai. Không được lặng lẽ bỏ qua.
4b. LUÔN ghi rõ ĐỒNG TIỀN của mỗi con số (trường `currency`). Doanh nghiệp ngoài Mỹ báo
   cáo bằng tiền bản địa: Toyota bằng JPY, ASML bằng EUR, TSMC bằng TWD. Nếu công cụ trả
   về `mixed_currency_warning`, PHẢI nhắc lại cảnh báo đó và TUYỆT ĐỐI không xếp hạng hay
   so sánh trực tiếp các con số khác đồng tiền — hệ thống không có tỷ giá để quy đổi.
5. Nếu hệ thống vừa tự đi lấy dữ liệu (trường just_ingested), hãy nói với người dùng.
6. Không đưa ra khuyến nghị mua/bán. Chỉ trình bày dữ kiện và phân tích.

Định dạng số — quan trọng, tránh gây hiểu nhầm:
· Quy đổi sang "tỷ USD" và làm tròn ĐÚNG HAI CHỮ SỐ thập phân: viết "215,94 tỷ USD",
  KHÔNG viết "215,938 tỷ USD" (ba chữ số sau dấu phẩy dễ bị đọc nhầm thành 215.938 tỷ).
· Dùng dấu phẩy làm dấu thập phân theo quy ước Việt Nam.
· Nếu muốn ghi thêm số đầy đủ, đặt trong ngoặc: "215,94 tỷ USD (215.938.000.000 USD)"."""


# ---------------------------------------------------------------- trạng thái


class AgentState(TypedDict, total=False):
    question: str
    round: int
    calls: List[Dict[str, Any]]
    observations: List[Dict[str, Any]]
    answer: str
    trace: List[Dict[str, Any]]
    reflection: str
    # Hàm nhận từng mẩu chữ của câu trả lời, do phía web truyền vào. Không truyền thì
    # khối trả lời chạy y như cũ, không stream.
    on_token: Any


def _truncate(obj: Any, limit: int = 3500) -> str:
    """Rút gọn kết quả công cụ trước khi đưa vào prompt.

    Cần thiết vì search_filings có thể trả về 6 chunk × 1.200 ký tự = 7.200 ký tự cho
    MỘT lần gọi. Model local với context 16k sẽ tràn sau vài vòng, và khi tràn thì
    LM Studio âm thầm cắt bớt phần đầu — tức là cắt mất chính chỉ dẫn hệ thống.
    """
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [đã cắt bớt, còn {len(text) - limit} ký tự]"


# ---------------------------------------------------------------- các khối xử lý


def node_route(state: AgentState) -> AgentState:
    """Khối 1 — LLM chọn công cụ."""
    started = time.time()
    result = chat_json(
        [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": f"User question (may be in Vietnamese):\n{state['question']}"},
        ],
        json_schema=ROUTER_SCHEMA,
        model=settings.llm_reasoning_model,
        max_tokens=1024,
        # Định tuyến là việc chọn trong danh sách, đã bị JSON schema ràng buộc — không
        # hưởng lợi từ chain-of-thought. Đo thật: tắt suy nghĩ nhanh gấp 7,5 lần.
        reasoning_effort="none",
    )

    calls = (result or {}).get("calls") or []
    # Không chọn được gì thì mặc định tìm kiếm văn bản — đoán sai vẫn hơn không làm gì
    if not calls:
        calls = [{"tool": "search_filings", "args": {"query": state["question"]}}]

    trace = state.get("trace", [])
    trace.append({
        "step": "định tuyến",
        "seconds": round(time.time() - started, 1),
        "reasoning": (result or {}).get("reasoning", ""),
        "calls": [c["tool"] for c in calls],
    })
    return {**state, "calls": calls[:3], "trace": trace}


def node_execute(state: AgentState) -> AgentState:
    """Khối 2 — chạy công cụ. Hoàn toàn không có LLM ở đây.

    ⚠️ BỘ ĐẾM VÒNG PHẢI TĂNG Ở ĐÂY, không phải ở khối định tuyến.

    Bản đầu tiên tăng `round` trong node_route. Nhưng node_route chỉ chạy MỘT LẦN ở đầu,
    còn vòng lặp thật là execute -> reflect -> execute. Hậu quả: `round` luôn bằng 1, điều
    kiện dừng MAX_ROUNDS không bao giờ đúng, và agent lặp cho tới khi LangGraph tự ngắt ở
    giới hạn đệ quy 25 — trả về RỖNG sau hơn hai phút.

    Đo thật trên bộ đánh giá: 7/34 câu hỏi chết vì lỗi này, toàn bộ nhóm định tính.

    Bộ đếm phải nằm trên chính cạnh mà vòng lặp đi qua.
    """
    observations = state.get("observations", [])
    trace = state.get("trace", [])
    current_round = state.get("round", 0) + 1

    # Không gọi lại công cụ với đúng tham số đã dùng.
    #
    # Khối suy xét hay bảo "chưa đủ" rồi đề nghị gọi lại chính công cụ vừa chạy. Dữ liệu
    # không mới thêm gì, nhưng ngữ cảnh thì phình gấp đôi — và với model local, ngữ cảnh
    # phình ra là thứ đắt nhất. Đo thật trên một câu bắc cầu: 25.500 ký tự dữ liệu dồn
    # lại khiến riêng khối trả lời mất 137 giây, gần như toàn bộ là thời gian nạp prompt.
    already_run = {
        (o.get("tool"), json.dumps(o.get("args"), sort_keys=True, default=str))
        for o in observations
    }

    for call in state.get("calls", []):
        name = call.get("tool")
        args = call.get("args") or {}

        signature = (name, json.dumps(args, sort_keys=True, default=str))
        if signature in already_run:
            trace.append({"step": "thực thi", "tool": name, "args": args,
                          "seconds": 0.0, "status": "bỏ qua — đã gọi với tham số này"})
            continue
        already_run.add(signature)

        func = TOOL_FUNCTIONS.get(name)
        started = time.time()

        if func is None:
            observations.append({"tool": name, "error": "công cụ không tồn tại"})
            continue

        try:
            result = func(**args)
        except TypeError as exc:
            # LLM điền sai tên tham số — ghi lại để khối suy xét biết mà sửa
            result = {"status": "bad_arguments", "error": str(exc)[:200], "expected": TOOL_SPECS[name]["args"]}
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "error": str(exc)[:200]}

        observations.append({"tool": name, "args": args, "result": result})
        trace.append({
            "step": "thực thi",
            "tool": name,
            "args": args,
            "seconds": round(time.time() - started, 1),
            "status": result.get("status") if isinstance(result, dict) else "ok",
        })

    return {**state, "observations": observations, "trace": trace, "round": current_round}


def _all_tools_succeeded(observations: List[Dict[str, Any]]) -> bool:
    """Mọi công cụ ở vòng vừa rồi đều trả về dữ liệu dùng được?"""
    if not observations:
        return False
    for obs in observations:
        result = obs.get("result")
        if not isinstance(result, dict):
            return False
        if result.get("status") not in ("ok", "already_indexed"):
            return False
    return True


def _company_not_in_universe(observations: List[Dict[str, Any]]) -> bool:
    """Mọi công cụ đều báo doanh nghiệp không có trong dữ liệu SEC?

    ⚠️ CÓ NHỮNG THẤT BẠI KHÔNG THỂ CỨU BẰNG CÁCH THỬ LẠI.

    Vòng lặp suy xét sinh ra để xử lý trường hợp "chọn nhầm công cụ, thử cái khác". Nhưng
    khi cái tên người dùng hỏi vốn KHÔNG nằm trong vũ trụ 6.255 doanh nghiệp SEC, thì
    không công cụ nào lấy được dữ liệu về nó — thử thêm bao nhiêu vòng cũng vậy.

    Đo thật trước khi có hàm này, với câu hỏi về "Acer" (niêm yết ở Đài Loan):
        định tuyến 5,5s -> tra số liệu not_found -> suy xét 9,2s -> kiểm tra độ phủ
        not_found -> tìm văn bản -> suy xét 105,7s -> tìm văn bản ...
    Hơn hai phút để cuối cùng vẫn phải nói "không có dữ liệu" — câu trả lời đã biết chắc
    ngay từ giây thứ sáu.

    Chỉ dừng khi TOÀN BỘ quan sát đều là thất bại phân giải. Nếu có bất kỳ công cụ nào
    lấy được dữ liệu thật, vòng lặp vẫn chạy bình thường.
    """
    if not observations:
        return False
    terminal = {"not_found", "company_not_found"}
    return all(
        isinstance(o.get("result"), dict) and o["result"].get("status") in terminal
        for o in observations
    )


def node_reflect(state: AgentState) -> AgentState:
    """Khối 3 — LLM xem dữ liệu đã đủ chưa.

    ⚠️ SUY XÉT PHẢI LÀ ĐƯỜNG NGOẠI LỆ, KHÔNG PHẢI ĐƯỜNG MẶC ĐỊNH.

    Bản đầu tiên cho mọi câu hỏi đi qua khối này. Đo thật: một câu tra số đơn giản, công
    cụ đã trả về `ok` ngay vòng đầu, vẫn tốn 11 giây để LLM kết luận "đủ rồi". Câu bắc
    cầu tốn 57 giây cho hai lần suy xét.

    Với model chạy local, mỗi lượt gọi LLM là 10-50 giây. Một lượt gọi chỉ để xác nhận
    điều đã hiển nhiên là lãng phí thuần túy — nhất là khi ngữ cảnh phải nạp lại từ đầu.

    Quy tắc mới: vòng đầu mà MỌI công cụ đều trả về `ok` thì bỏ qua suy xét, đi thẳng
    tới trả lời. Chỉ suy xét khi thực sự có vấn đề — công cụ lỗi, không tìm thấy dữ
    liệu, hoặc tham số sai. Đó đúng là lúc cần agent nghĩ lại.
    """
    if state.get("round", 0) >= MAX_ROUNDS:
        return {**state, "calls": [], "reflection": "đã đạt giới hạn số vòng"}

    # Cái tên không có trong vũ trụ SEC -> mọi vòng lặp thêm đều vô ích. Dừng ngay và
    # để khối trả lời nói thật, thay vì đốt hơn hai phút rồi vẫn kết luận y như vậy.
    if _company_not_in_universe(state.get("observations", [])):
        trace = state.get("trace", [])
        trace.append({
            "step": "suy xét", "seconds": 0.0, "sufficient": True,
            "missing": "doanh nghiệp không có trong dữ liệu SEC — thử thêm công cụ cũng vô ích",
            "next": [],
        })
        return {**state, "calls": [], "trace": trace}

    if state.get("round", 0) == 1 and _all_tools_succeeded(state.get("observations", [])):
        trace = state.get("trace", [])
        trace.append({
            "step": "suy xét", "seconds": 0.0, "sufficient": True,
            "missing": "bỏ qua — mọi công cụ đã trả về dữ liệu ngay vòng đầu", "next": [],
        })
        return {**state, "calls": [], "trace": trace}

    started = time.time()
    result = chat_json(
        [
            {"role": "system", "content":
                "Decide whether the tool results are enough to answer the question. "
                "If not, propose at most 2 more tool calls. Be strict: if the data is "
                "already there, say sufficient=true. Do not loop for polish.\n\n"
                f"Available tools:\n{_TOOL_MENU}"},
            {"role": "user", "content":
                f"Question: {state['question']}\n\n"
                f"Tool results so far:\n{_truncate(state.get('observations', []), 5000)}"},
        ],
        json_schema=REFLECT_SCHEMA,
        model=settings.llm_reasoning_model,
        max_tokens=768,
        reasoning_effort="none",
    )

    sufficient = (result or {}).get("sufficient", True)
    next_calls = [] if sufficient else ((result or {}).get("calls") or [])

    trace = state.get("trace", [])
    trace.append({
        "step": "suy xét",
        "seconds": round(time.time() - started, 1),
        "sufficient": sufficient,
        "missing": (result or {}).get("missing", ""),
        "next": [c["tool"] for c in next_calls],
    })
    return {**state, "calls": next_calls, "trace": trace,
            "reflection": (result or {}).get("missing", "")}


def node_answer(state: AgentState) -> AgentState:
    """Khối 4 — viết câu trả lời cuối, có dẫn nguồn.

    ⚠️ TẮT HẲN BƯỚC SUY NGHĨ Ở KHỐI NÀY — có số đo, không phải phỏng đoán.

    Ban đầu tôi giữ suy nghĩ cho khối này, lập luận rằng tổng hợp dữ liệu từ nhiều công cụ
    là việc hưởng lợi từ chain-of-thought. Đo thật thì ngược lại. Cùng một câu hỏi, gọi
    trực tiếp một lần:

        không gửi tham số   27,1s | 314 token sinh ra, trong đó 276 là token SUY NGHĨ
        reasoning_effort=none 4,5s |  41 token, 0 token suy nghĩ

    Model tiêu 88% ngân sách token để tự lẩm bẩm. Và khi so hai câu trả lời cạnh nhau,
    bản KHÔNG suy nghĩ còn trình bày tốt hơn — nó tự lập bảng markdown, phép tính chênh
    lệch chính xác y hệt.

    Tệ hơn nữa: khi ngữ cảnh lớn, phần suy nghĩ có thể ăn hết max_tokens rồi bị cắt trước
    khi kịp viết, khiến tầng dưới phải gọi lại. Đó là nguyên nhân đo được biến động 3,4
    lần trên CÙNG một câu hỏi (128s / 175s / 440s / 164s).

    Lưu ý: "minimal" và "low" KHÔNG giảm suy nghĩ với model này (đo được 333 và 312 token
    suy nghĩ, gần bằng mặc định). Chỉ "none" mới thực sự tắt.
    """
    observations = state.get("observations", [])
    payload_size = len(json.dumps(observations, ensure_ascii=False, default=str))
    effort = "none"

    started = time.time()
    messages = [
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content":
            f"Câu hỏi: {state['question']}\n\n"
            f"Dữ liệu công cụ trả về:\n{_truncate(state.get('observations', []), 9000)}"},
    ]
    common = dict(
        model=settings.llm_reasoning_model,
        max_tokens=4096,
        temperature=0.2,  # nhỉnh hơn 0 một chút cho câu văn tự nhiên, vẫn bám dữ liệu
        # Mức suy nghĩ do payload_size quyết định — xem chú thích ở đầu hàm.
        reasoning_effort=effort,
    )

    # Phía web truyền vào một hàm nhận từng mẩu chữ, để chữ hiện dần lên màn hình thay vì
    # đợi trọn 8-25 giây. Bộ đánh giá và mọi lời gọi khác KHÔNG truyền gì và đi đúng nhánh
    # cũ — nhờ vậy việc thêm chế độ stream không đụng tới con đường đã kiểm chứng 26/26.
    on_token = state.get("on_token")
    if callable(on_token):
        answer = chat_stream(messages, on_token, **common)
    else:
        answer = chat(messages, **common)
    trace = state.get("trace", [])
    trace.append({
        "step": "trả lời",
        "seconds": round(time.time() - started, 1),
        "reasoning": effort,
        "payload_chars": payload_size,
    })
    return {**state, "answer": answer, "trace": trace}


def should_continue(state: AgentState) -> str:
    """Mũi tên có điều kiện: còn công cụ cần gọi thì quay lại, không thì đi trả lời."""
    if state.get("calls") and state.get("round", 0) < MAX_ROUNDS:
        return "execute"
    return "compose"


# ---------------------------------------------------------------- lắp sơ đồ


def build_agent():
    """Lắp các khối thành đồ thị chạy được."""
    workflow = StateGraph(AgentState)

    workflow.add_node("route", node_route)
    workflow.add_node("execute", node_execute)
    workflow.add_node("reflect", node_reflect)
    # Tên khối KHÔNG được trùng với tên khóa trong AgentState. State đã có khóa "answer",
    # nên khối phải mang tên khác, nếu không LangGraph báo lỗi ngay lúc lắp đồ thị.
    workflow.add_node("compose", node_answer)

    workflow.add_edge(START, "route")
    workflow.add_edge("route", "execute")
    workflow.add_edge("execute", "reflect")
    workflow.add_conditional_edges("reflect", should_continue,
                                   {"execute": "execute", "compose": "compose"})
    workflow.add_edge("compose", END)

    return workflow.compile()


_agent = None


def ask(question: str, verbose: bool = False) -> Dict[str, Any]:
    """Điểm vào chính: đặt câu hỏi, nhận câu trả lời kèm dấu vết suy luận."""
    global _agent
    if _agent is None:
        _agent = build_agent()

    started = time.time()
    final = _agent.invoke({"question": question, "round": 0, "observations": [], "trace": []})

    return {
        "question": question,
        "answer": final.get("answer", ""),
        "trace": final.get("trace", []),
        "observations": final.get("observations", []) if verbose else None,
        "rounds": final.get("round", 0),
        "seconds": round(time.time() - started, 1),
    }
