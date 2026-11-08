// Mesa do especialista: fila de transbordo, conversa e contexto com sugestão da IA e nota de DNA.
let me = null;
let selected = null;
let detail = null;
let lastRendered = -1;
let busy = false;

const queueBox = document.getElementById('queue');
const threadBox = document.getElementById('thread');
const reply = document.getElementById('reply');
const errorBox = document.getElementById('error');

function statusText(c) {
  if (c.status === 'aguardando_humano') return 'Aguardando especialista';
  if (c.status === 'em_atendimento') return c.assigned_agent === me ? 'Com você' : `Com ${c.assigned_agent}`;
  if (c.status === 'encerrada') return 'Encerrada';
  return 'Com a IA';
}

function queueItem(q) {
  const btn = el('button', { class: `q-item ${q.id === selected ? 'active' : ''}`, type: 'button',
    onclick: () => select(q.id) },
    el('div', { class: 'q-top' },
      q.awaiting_reply ? el('span', { class: 'dot', style: 'color: var(--red)', title: 'cliente aguardando resposta' }) : null,
      el('strong', { text: q.user_id }),
      el('span', { class: 'wait', text: fmtWait(q.waiting_s) })),
    el('div', { class: 'q-top' },
      el('span', { class: `tag ${q.reason === 'tentativas_esgotadas' ? 'warn' : 'red'}`, text: REASONS[q.reason] || q.reason }),
      q.theme ? el('span', { class: 'tag', text: q.theme }) : null),
    el('div', { class: 'q-msg', text: q.last_client_message || '' }));
  return btn;
}

async function loadQueue() {
  const { queue } = await api('GET', '/api/agent/queue');
  document.getElementById('q-count').textContent = queue.length;
  const groups = [
    ['Com você', queue.filter(q => q.status === 'em_atendimento' && q.assigned_agent === me)],
    ['Aguardando', queue.filter(q => q.status === 'aguardando_humano')],
    ['Com outros especialistas', queue.filter(q => q.status === 'em_atendimento' && q.assigned_agent !== me)],
  ];
  const nodes = [];
  for (const [title, items] of groups) {
    if (!items.length) continue;
    nodes.push(el('div', { class: 'q-group', text: `${title} (${items.length})` }), ...items.map(queueItem));
  }
  queueBox.replaceChildren(...(nodes.length ? nodes : [el('div', { class: 'empty', text: 'Nenhuma conversa na fila.' })]));
}

function renderThread(messages) {
  if (messages.length === lastRendered) return;
  lastRendered = messages.length;
  const nodes = messages.map(m => {
    if (m.author === 'sistema') return el('div', { class: 'bubble-row center' }, el('div', { class: 'bubble sistema', text: m.text }));
    const who = m.author === 'cliente' ? `Cliente ${detail.conversation.user_id}`
      : m.author === 'ia' ? 'Assistente Getnet (IA)' : `${m.author_name} (especialista)`;
    return el('div', { class: `bubble-row ${m.author === 'humano' ? 'right' : ''}` },
      el('span', { class: 'who', text: `${who} · ${fmtTime(m.created_at)}` }),
      renderRich(m.text, el('div', { class: `bubble ${m.author}` })));
  });
  threadBox.replaceChildren(...nodes);
  threadBox.parentElement.scrollTop = threadBox.parentElement.scrollHeight;
}

function pillarRows(ev) {
  return PILLARS.flatMap(([key, label]) => [
    el('div', { class: 'pillar' },
      el('span', { text: label }),
      el('div', { class: 'bar humano' }, el('span', { style: `width:${(ev[key] / 25) * 100}%` })),
      el('strong', { class: 'small', text: `${ev[key]}/25` })),
    el('p', { class: 'just', text: ev.justifications[key] }),
  ]);
}

function renderContext() {
  const c = detail.conversation, ctx = detail.context;
  const mine = detail.evaluations.filter(e => e.agent_name === me);
  const sections = [
    el('section', {},
      el('h3', { text: 'Motivo do transbordo' }),
      el('div', { class: 'chips' },
        el('span', { class: `tag ${c.handoff_reason === 'tentativas_esgotadas' ? 'warn' : 'red'}`, text: REASONS[c.handoff_reason] || '—' }),
        c.handoff_theme ? el('span', { class: 'tag', text: `tema: ${c.handoff_theme}` }) : null),
      el('div', { class: 'small muted', text: c.handoff_reason === 'tentativas_esgotadas'
        ? 'O cliente perguntou 4 vezes sobre o mesmo assunto: a IA não conseguiu resolver.'
        : 'Ao final do atendimento da IA, o cliente respondeu que o pedido não foi resolvido.' })),
    el('section', {},
      el('h3', { text: 'O que a IA já fez' }),
      el('div', { class: 'chips' },
        ...(ctx.agents.length ? ctx.agents.map(a => el('span', { class: 'tag ia', text: AGENTS[a] || a }))
          : [el('span', { class: 'small muted', text: 'Nenhum agente especializado acionado.' })]),
        ...ctx.tools.map(t => el('span', { class: 'tag ok', text: t }))),
      ...ctx.sources.slice(0, 5).map(s => el('div', { class: 'small' }, link(s, s.replace(/^https?:\/\//, ''))))),
  ];
  if (c.ai_suggestion) {
    sections.push(el('section', {},
      el('h3', { text: 'Sugestão da IA' }),
      el('div', { class: 'small muted', text: 'Resposta que a IA daria agora. Revise antes de usar.' }),
      renderRich(c.ai_suggestion, el('div', { class: 'suggestion' }))));
  }
  sections.push(el('section', {},
    el('h3', { text: 'Lembrete DNA Getnet' }),
    el('ul', { class: 'dna-list' },
      el('li', { text: 'Útil: traduza termos técnicos e facilite o caminho.' }),
      el('li', { text: 'Calmo: prazos e valores claros, sempre com o próximo passo.' }),
      el('li', { text: 'Amigável: frases curtas, tom de conversa.' }),
      el('li', { text: 'Empoderado: explique o porquê e mostre onde o cliente age sozinho.' }))));
  const evSection = el('section', {}, el('h3', { text: 'Sua nota de DNA nesta conversa' }));
  if (mine.length) {
    const last = mine[0];
    evSection.append(
      el('div', { style: 'display:flex;align-items:baseline;gap:8px' },
        el('span', { class: 'big', text: last.total }), el('span', { class: 'muted small', text: '/100 na última resposta' }),
        scorePill(last.total)),
      ...pillarRows(last),
      mine.length > 1 ? el('div', { class: 'small muted',
        text: `Média nas ${mine.length} respostas: ${Math.round(mine.reduce((s, e) => s + e.total, 0) / mine.length)}` }) : null);
  } else {
    evSection.append(el('div', { class: 'small muted', text: 'A avaliação aparece alguns segundos depois de cada resposta enviada.' }));
  }
  sections.push(evSection);
  document.getElementById('ctx').replaceChildren(...sections);
}

function renderHeader() {
  const c = detail.conversation;
  document.getElementById('c-user').textContent = `Cliente ${c.user_id}`;
  document.getElementById('c-status').textContent = statusText(c);
  const mineNow = c.status === 'em_atendimento' && c.assigned_agent === me;
  document.getElementById('btn-claim').hidden = c.status !== 'aguardando_humano';
  document.getElementById('btn-close').hidden = !mineNow;
  document.getElementById('btn-send').disabled = !mineNow;
  reply.disabled = !mineNow;
  reply.placeholder = mineNow ? 'Escreva sua resposta ao cliente'
    : c.status === 'aguardando_humano' ? 'Assuma o atendimento para responder' : 'Somente leitura';
  document.getElementById('btn-suggest').hidden = !c.ai_suggestion || !mineNow;
}

async function loadDetail() {
  if (!selected) return;
  detail = await api('GET', `/api/agent/conversations/${selected}`);
  renderHeader(); renderThread(detail.messages); renderContext();
}

async function select(id) {
  selected = id; lastRendered = -1; errorBox.textContent = ''; reply.value = '';
  document.getElementById('no-conv').hidden = true;
  document.getElementById('conv').hidden = false;
  await Promise.all([loadDetail(), loadQueue()]);
}

async function action(fn) {
  if (busy) return;
  busy = true; errorBox.textContent = '';
  try { await fn(); await Promise.all([loadDetail(), loadQueue()]); }
  catch (e) { errorBox.textContent = e.message; }
  finally { busy = false; }
}

document.getElementById('btn-claim').addEventListener('click', () => action(async () => {
  await api('POST', `/api/agent/conversations/${selected}/claim`, { agent_name: me });
  reply.focus();
}));
document.getElementById('btn-close').addEventListener('click', () => action(async () => {
  await api('POST', `/api/agent/conversations/${selected}/close`, { agent_name: me });
}));
document.getElementById('btn-suggest').addEventListener('click', () => {
  reply.value = detail.conversation.ai_suggestion; reply.focus();
});
document.getElementById('composer').addEventListener('submit', ev => {
  ev.preventDefault();
  const text = reply.value.trim();
  if (!text) { errorBox.textContent = 'Escreva uma resposta antes de enviar.'; return; }
  action(async () => {
    await api('POST', `/api/agent/conversations/${selected}/messages`, { agent_name: me, text });
    reply.value = '';
  });
});
reply.addEventListener('keydown', ev => {
  if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) document.getElementById('composer').requestSubmit();
});
document.getElementById('logout').addEventListener('click', () => { Store.del('agent_name'); location.reload(); });

(async function init() {
  markNav('/especialista');
  await gate({ title: 'Mesa do especialista', subtitle: 'Seu nome aparece para o cliente e nas notas de DNA.',
    fields: [{ key: 'agent_name', label: 'Seu nome', placeholder: 'Ex.: Ana' }] });
  me = Store.get('agent_name');
  document.getElementById('whoami').textContent = `Especialista: ${me}`;
  await loadQueue().catch(e => { queueBox.replaceChildren(el('div', { class: 'empty', text: e.message })); });
  setInterval(() => { if (!busy) { loadQueue().catch(() => {}); loadDetail().catch(() => {}); } }, 3000);
})();
