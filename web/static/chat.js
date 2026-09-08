/* FinGraph — trang trò chuyện.
 *
 * Ba việc chính:
 *   1. Đọc luồng SSE từ /api/ask và cập nhật trạng thái NGAY KHI từng bước xong.
 *   2. Dựng HTML từ Markdown mà mô hình trả về (tự viết bộ dựng nhỏ, xem bên dưới).
 *   3. Gấp dấu vết suy luận lại dưới mỗi câu trả lời — giữ được, mà không làm rối mắt.
 */

'use strict';

const $ = (s) => document.querySelector(s);
const nf = new Intl.NumberFormat('vi-VN');

/* Mọi thứ đi vào innerHTML đều phải qua đây trước.
 * Câu trả lời do mô hình sinh ra từ văn bản hồ sơ SEC — tức là dữ liệu ngoài tầm kiểm
 * soát. Nhét thẳng vào innerHTML là mở cửa cho XSS. Thoát trước, rồi mới cho phép đúng
 * những thẻ mình tự tạo ra ở bước dựng Markdown. */
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ══════════════════════════════════════════════ dựng Markdown

/* Ô bảng chứa một con số -> canh phải và dùng font đều, nếu không bảng tài chính không
 * đọc được. Phải chấp nhận cả đuôi nhiều chữ như "34,59 tỷ USD" hay "1.234 triệu TWD":
 * số liệu ở đây gần như luôn đi kèm bậc độ lớn RỒI mới tới đơn vị tiền tệ.
 *
 * Viết bằng regex literal chứ không ghép chuỗi: trong template literal của JavaScript,
 * `\s` và `\d` là escape không hợp lệ và bị rút gọn thành "s", "d" — regex vẫn chạy,
 * chỉ là khớp sai, âm thầm. */
const NUM_CELL =
  /^[\s$€¥₫()+\-−]*\d[\d.,\s]*(?:\s*(?:tỷ|ty|triệu|trieu|nghìn|nghin|bn|tr))?(?:\s*(?:USD|EUR|JPY|TWD|VND|KRW|CNY|GBP|CHF|SEK|COP|đ|\$|%))?[\s).%]*$/i;

/* VÌ SAO TỰ VIẾT THAY VÌ DÙNG THƯ VIỆN
 *
 * Câu trả lời của agent chỉ dùng một tập Markdown rất hẹp: đậm, danh sách, tiêu đề,
 * bảng, mã. Kéo về một thư viện 40 KB cho ngần ấy là không đáng, và mỗi phụ thuộc CDN
 * là thêm một thứ có thể chết đúng lúc đang demo.
 *
 * Thứ tự xử lý quan trọng: thoát ký tự TRƯỚC, rồi mới chèn thẻ. Làm ngược lại thì chính
 * thẻ mình vừa tạo sẽ bị thoát thành chữ. */
function md(src) {
  const lines = String(src || '').split('\n');
  const out = [];
  let list = null;      // 'ul' | 'ol' | null
  let table = null;     // mảng các hàng đang gom

  const isNum = (c) => NUM_CELL.test(c.trim());
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };

  const flushTable = () => {
    if (!table) return;
    // Hàng thứ hai của bảng Markdown là dòng gạch ngang ngăn cách — bỏ đi.
    const rows = table.filter((r) => !/^[\s|:\-]+$/.test(r));
    const cells = rows.map((r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
    if (cells.length) {
      const head = cells.shift();
      out.push('<table><thead><tr>' +
        head.map((c) => `<th${isNum(c) ? ' class="num"' : ''}>${inline(c)}</th>`).join('') +
        '</tr></thead><tbody>');
      for (const row of cells) {
        out.push('<tr>' + row.map((c) => `<td${isNum(c) ? ' class="num"' : ''}>${inline(c)}</td>`).join('') + '</tr>');
      }
      out.push('</tbody></table>');
    }
    table = null;
  };

  /* Mô hình đôi khi nhả cú pháp LaTeX ($\rightarrow$) dù prompt không hề yêu cầu — hay
   * gặp nhất ở câu hỏi bắc cầu, đúng câu người ta hay dùng để thử. Trang này không dựng
   * LaTeX, nên đổi vài macro mũi tên thành ký tự Unicode thay vì để lộ mã nguồn. */
  const delatex = (t) => String(t)
    .replace(/\$\s*\\(?:rightarrow|longrightarrow|Rightarrow|to)\s*\$/g, ' → ')
    .replace(/\\(?:rightarrow|longrightarrow|Rightarrow|to)\b/g, '→');

  const inline = (t) => esc(delatex(t))
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');

    if (/^\s*\|.*\|\s*$/.test(line)) { closeList(); (table ||= []).push(line.trim()); continue; }
    flushTable();

    if (!line.trim()) { closeList(); continue; }

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { closeList(); const lv = Math.min(h[1].length + 2, 5); out.push(`<h${lv}>${inline(h[2])}</h${lv}>`); continue; }

    const ul = line.match(/^\s*[-*•·]\s+(.*)$/);
    if (ul) { if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inline(ul[1])}</li>`); continue; }

    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) { if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(ol[1])}</li>`); continue; }

    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  flushTable();
  return out.join('');
}

// ══════════════════════════════════════════════ câu hỏi gợi ý

const SAMPLES = [
  { tag: 'Số liệu', cls: 'tag-num', q: 'Doanh thu và lợi nhuận của NVIDIA năm tài chính 2026 là bao nhiêu?' },
  { tag: 'So sánh', cls: 'tag-num', q: 'So sánh chi phí R&D của Apple, Microsoft và Alphabet năm 2025' },
  { tag: 'Văn bản', cls: 'tag-text', q: 'NVIDIA nêu rủi ro gì về kiểm soát xuất khẩu chip sang Trung Quốc?' },
  { tag: 'Bắc cầu', cls: 'tag-graph', q: 'Nếu TSMC gián đoạn sản xuất thì ảnh hưởng tới Microsoft qua những mắt xích nào?' },
];

function renderSuggestions() {
  const box = $('#suggest');
  if (!box) return;
  box.innerHTML = '';
  SAMPLES.forEach((s) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'sg';
    b.innerHTML = `<span class="tag ${s.cls}">${esc(s.tag)}</span><span>${esc(s.q)}</span>`;
    b.addEventListener('click', () => { if (!busy) submit(s.q); });
    box.appendChild(b);
  });
}

// ══════════════════════════════════════════════ khung hội thoại

const thread = $('#thread');
const scroller = $('#scroller');
let busy = false;

/* Chỉ tự cuộn xuống khi người dùng ĐANG ở gần đáy. Nếu họ cuộn ngược lên đọc lại câu
 * trước, kéo họ về đáy mỗi lần có chữ mới là một trong những thứ khó chịu nhất mà một
 * khung chat có thể làm. */
function scrollDown(force) {
  const near = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 140;
  if (force || near) scroller.scrollTop = scroller.scrollHeight;
}

function addUser(text) {
  const el = document.createElement('div');
  el.className = 'msg m-user';
  el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  thread.appendChild(el);
  scrollDown(true);
}

const BOT_ICON = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v3M5.5 5.5 7.6 7.6M2 12h3M18.5 5.5 16.4 7.6M22 12h-3"/><rect x="6" y="10" width="12" height="10" rx="3"/></svg>`;

function addBot() {
  const el = document.createElement('div');
  el.className = 'msg m-bot';
  el.innerHTML = `<div class="avatar">${BOT_ICON}</div>
    <div class="body"><div class="working"><span class="spin"></span><em>đang định tuyến…</em></div></div>`;
  thread.appendChild(el);
  scrollDown(true);
  return el.querySelector('.body');
}

// ══════════════════════════════════════════════ dấu vết, gấp lại

function stepHTML(d) {
  const secs = d.seconds !== undefined ? `<span class="step-s">${d.seconds}s</span>` : '';
  let inner;

  if (d.phase === 'route') {
    inner = `<div class="step-t">Định tuyến${secs}</div>
             <div class="step-d">chọn công cụ: <b>${esc((d.calls || []).join(', ') || 'không có')}</b></div>`;
  } else if (d.phase === 'execute') {
    const m = d.tool_meta || { label: d.tool, source: '' };
    const args = d.args && Object.keys(d.args).length
      ? `<div class="args">${esc(JSON.stringify(d.args))}</div>` : '';
    inner = `<div class="step-t">${esc(m.label)}${secs}</div>
             ${m.source ? `<span class="src">${esc(m.source)}</span>` : ''}${args}`;
  } else if (d.phase === 'reflect') {
    const ok = d.sufficient !== false;
    inner = `<div class="step-t">Suy xét${secs}</div>
             <div class="step-d ${ok ? 'ok' : 'more'}">${ok ? '✓ đủ dữ liệu để trả lời' : '↻ cần lấy thêm — quay lại thực thi'}</div>`;
  } else if (d.phase === 'compose') {
    inner = `<div class="step-t">Viết câu trả lời${secs}</div>
             <div class="step-d">tổng hợp ${nf.format(d.payload_chars || 0)} ký tự dữ liệu</div>`;
  } else {
    inner = `<div class="step-t">${esc(d.label || d.step || '?')}${secs}</div>`;
  }

  const skip = String(d.status || '').startsWith('bỏ qua') ? ' skip' : '';
  return `<div class="step s-${d.phase || 'other'}${skip}">${inner}</div>`;
}

function addTrace(body, steps, secs, rounds) {
  if (!steps.length) return;
  const tools = steps.filter((s) => s.phase === 'execute').length;
  const el = document.createElement('details');
  el.className = 'trace';
  el.innerHTML = `
    <summary>
      <svg class="chev" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>
      <span>Trợ lý đã làm gì · <b>${tools} truy vấn</b> · ${secs}s · ${rounds} vòng</span>
    </summary>
    <div class="trace-body">${steps.map(stepHTML).join('')}</div>`;
  body.appendChild(el);
}

// ══════════════════════════════════════════════ vòng hỏi–đáp

async function submit(question) {
  if (busy) return;
  busy = true;
  $('#sendBtn').disabled = true;
  $('#welcome')?.remove();

  addUser(question);
  const body = addBot();
  const steps = [];

  const status = (txt) => {
    const t = body.querySelector('.working em');
    if (t) t.textContent = txt;
  };
  const fail = (msg, hint) => {
    body.innerHTML = `<div class="err"><b>Không hoàn thành được</b>
      <p>${esc(msg)}</p>${hint ? `<p>${esc(hint)}</p>` : ''}</div>`;
  };

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);

    /* Đọc SSE bằng tay thay vì dùng EventSource, vì EventSource CHỈ làm được GET — mà
     * câu hỏi phải đi bằng POST (dài, và không nên nằm trong URL hay log máy chủ). */
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let secs = 0, rounds = 0;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      // Mỗi sự kiện SSE kết thúc bằng một dòng trống. Phần đuôi chưa trọn vẹn thì giữ
      // lại chờ mảnh sau — TCP không đảm bảo cắt gói đúng ranh giới sự kiện.
      const parts = buf.split('\n\n');
      buf = parts.pop();

      for (const part of parts) {
        const line = part.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;

        let ev;
        try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }

        if (ev.type === 'queued') {
          status(`đang xếp hàng — còn ${ev.position} câu phía trước`);
        } else if (ev.type === 'start') {
          status('đang đọc câu hỏi…');
        } else if (ev.type === 'step') {
          const d = ev.data;
          steps.push(d);
          status(
            d.phase === 'route' ? 'đang truy vấn dữ liệu…' :
            d.phase === 'execute' ? `${d.tool_meta?.label || 'đang chạy'}…` :
            d.phase === 'reflect' ? (d.sufficient === false ? 'cần thêm dữ liệu, đang lấy tiếp…' : 'đang soạn câu trả lời…') :
            'đang soạn câu trả lời…'
          );
          scrollDown();
        } else if (ev.type === 'answer') {
          body.innerHTML = md(ev.text);
          scrollDown();
        } else if (ev.type === 'done') {
          secs = ev.seconds; rounds = ev.rounds;
          addTrace(body, steps, secs, rounds);
          scrollDown();
        } else if (ev.type === 'error') {
          fail(ev.message, ev.hint);
        }
      }
    }
  } catch (err) {
    fail(String(err.message || err),
      'Kiểm tra máy chủ còn chạy không, và LM Studio đã bật server chưa (tab Developer → Start Server).');
  } finally {
    busy = false;
    $('#sendBtn').disabled = false;
    scrollDown();
  }
}

// ══════════════════════════════════════════════ đèn trạng thái

async function loadHealth() {
  const box = $('#status');
  const paint = (cls, text, title) => {
    box.innerHTML = `<span class="dot ${cls}"></span><span>${text}</span>`;
    box.title = title || '';
  };
  try {
    const h = await (await fetch('/api/health')).json();
    if (h.ready) { paint('dot-ok', 'sẵn sàng', 'Neo4j, Qdrant và LM Studio đều phản hồi'); return; }

    const down = [];
    if (!h.neo4j) down.push('Neo4j');
    if (!h.qdrant) down.push('Qdrant');
    if (!h.llm) down.push('LM Studio');
    paint('dot-bad', 'thiếu: ' + down.join(', '),
      down.includes('LM Studio')
        ? 'Bật LM Studio → tab Developer → Start Server'
        : 'Chạy: docker compose up -d');
  } catch (_) {
    paint('dot-bad', 'máy chủ không phản hồi');
  }
}

// ══════════════════════════════════════════════ ô nhập

const input = $('#input');
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
});
// Enter gửi, Shift+Enter xuống dòng — quy ước quen thuộc của mọi khung chat.
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#composer').requestSubmit(); }
});

$('#composer').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q || busy) return;
  input.value = '';
  input.style.height = 'auto';
  submit(q);
});

$('#newBtn').addEventListener('click', () => { if (!busy) location.reload(); });

renderSuggestions();
loadHealth();
setInterval(loadHealth, 30000);
