// Tela do cliente: chat com a IA e, após o transbordo, com o especialista.
const EXAMPLES = [
  'Qual a diferença entre a Get Clássica e a Get Smart?',
  'Quando cai o dinheiro das vendas de ontem?',
  'Como funciona a antecipação?',
  'Minha maquininha não conecta na internet',
  'Quero pedir bobinas para a maquininha',
  'Qual o placar do último jogo do Corinthians?',
  'Como está a classificação do Brasileirão?',
  'Quero falar com um atendente',
];

const thread = document.getElementById('thread');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const errorBox = document.getElementById('error');
const backstage = document.getElementById('backstage');

let userId = null;
let conversationId = null;
let lastId = 0;
let status = 'ia';
let busy = false;
let pollTimer = null;

function storageKey() { return `conv:${userId}`; }

function setStatus(newStatus, specialist) {
  status = newStatus;
  const dot = document.getElementById('status-dot');
  const title = document.getElementById('status-title');
  const sub = document.getElementById('status-sub');
  dot.classList.remove('pulse');
  if (newStatus === 'aguardando_humano') {
    dot.style.color = 'var(--warn)'; dot.classList.add('pulse');
    title.textContent = 'Transferindo para um especialista';
    sub.textContent = 'Você continua por aqui; a resposta chega nesta tela.';
  } else if (newStatus === 'em_atendimento') {
    dot.style.color = 'var(--ok)';
    title.textContent = `${specialist || 'Especialista'} · Especialista Getnet`;
    sub.textContent = 'Você está falando com uma pessoa do nosso time.';
  } else if (newStatus === 'encerrada') {
    dot.style.color = 'var(--faint)';
    title.textContent = 'Atendimento encerrado';
    sub.textContent = 'Envie uma mensagem para começar uma nova conversa.';
  } else {
    dot.style.color = 'var(--ia)';
    title.textContent = 'Assistente virtual Getnet';
    sub.textContent = 'Respondo na hora, 24 horas por dia.';
  }
}

function renderFeedback(show) {
  document.getElementById('feedback')?.remove();
  if (!show) return;
  thread.append(el('div', { class: 'feedback', id: 'feedback' },
    el('span', { text: 'Consegui resolver?' }),
    el('button', { class: 'btn', type: 'button', text: 'Sim, resolveu', onclick: () => send('Sim') }),
    el('button', { class: 'btn', type: 'button', text: 'Não resolveu', onclick: () => send('Não') })));
}

function metaChips(meta) {
  const box = el('div', { class: 'meta backstage' });
  box.hidden = !backstage.checked;
  box.append(el('span', { class: 'tag', text: `triagem: ${meta.triage}` }));
  for (const a of meta.agents) box.append(el('span', { class: 'tag ia', text: AGENTS[a] || a }));
  if (meta.theme) box.append(el('span', { class: 'tag', text: `tema: ${meta.theme}` }));
  for (const t of meta.tools) box.append(el('span', { class: 'tag ok', text: t }));
  for (const s of meta.sources.slice(0, 4)) box.append(link(s, new URL(s).pathname.replace(/\/$/, '') || s));
  return box;
}

function renderMessage(m) {
  document.getElementById('welcome')?.remove();
  if (m.author === 'sistema') {
    thread.append(el('div', { class: 'bubble-row center' }, el('div', { class: 'bubble sistema', text: m.text })));
    return;
  }
  const right = m.author === 'cliente';
  const who = m.author === 'ia' ? 'Assistente Getnet'
    : m.author === 'humano' ? `${m.author_name} · Especialista Getnet` : 'Você';
  const bubble = renderRich(m.text, el('div', { class: `bubble ${m.author}` }));
  const row = el('div', { class: `bubble-row ${right ? 'right' : ''}` },
    el('span', { class: 'who', text: `${who} · ${fmtTime(m.created_at)}` }), bubble);
  if (m.meta) row.append(metaChips(m.meta));
  thread.append(row);
}

function scrollDown() { thread.scrollTop = thread.scrollHeight; }

async function fetchMessages() {
  if (!conversationId) return;
  const data = await api('GET',
    `/api/conversations/${conversationId}/messages?user_id=${encodeURIComponent(userId)}&after=${lastId}`);
  if (data.messages.length) document.getElementById('feedback')?.remove();
  for (const m of data.messages) { renderMessage(m); lastId = Math.max(lastId, m.id); }
  if (data.messages.length) {
    renderFeedback(data.conversation.awaiting_feedback && data.conversation.status === 'ia');
    scrollDown();
  }
  setStatus(data.conversation.status, data.conversation.specialist);
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => { if (!busy) fetchMessages().catch(() => {}); }, 2500);
}

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true; sendBtn.disabled = true; errorBox.textContent = '';
  input.value = '';
  document.getElementById('feedback')?.remove();
  // eco imediato da mensagem; a versão gravada (já mascarada) substitui na sequência
  const pending = el('div', { class: 'bubble-row right pending' },
    el('span', { class: 'who', text: 'Você · enviando' }), el('div', { class: 'bubble cliente', text: text }));
  document.getElementById('welcome')?.remove();
  thread.append(pending);
  const typing = status === 'ia' ? el('div', { class: 'bubble-row' },
    el('div', { class: 'bubble ia typing', 'aria-label': 'digitando' }, el('span'), el('span'), el('span'))) : null;
  if (typing) thread.append(typing);
  scrollDown();
  try {
    const body = await api('POST', '/chat',
      { message: text, user_id: userId, conversation_id: conversationId, channel: 'web' });
    if (body.conversation_id !== conversationId) {
      if (conversationId) thread.append(el('div', { class: 'divider', text: 'Nova conversa' }));
      conversationId = body.conversation_id; lastId = 0;
      Store.set(storageKey(), conversationId);
    }
    pending.remove(); typing?.remove();
    await fetchMessages();
  } catch (e) {
    pending.remove(); typing?.remove();
    input.value = text;
    errorBox.textContent = e.status === 401 ? 'Código de acesso expirado. Recarregue a página.' : e.message;
  } finally {
    busy = false; sendBtn.disabled = false; input.focus();
  }
}

function newConversation() {
  conversationId = null; lastId = 0;
  Store.del(storageKey());
  thread.replaceChildren(el('div', { class: 'welcome', id: 'welcome' },
    el('h2', { text: 'Olá! Como posso ajudar?' }),
    el('p', { text: 'Pergunte sobre maquininhas, recebimentos, Pix, antecipação ou peça um serviço.' })));
  setStatus('ia');
}

document.getElementById('composer').addEventListener('submit', ev => { ev.preventDefault(); send(input.value); });
input.addEventListener('keydown', ev => {
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send(input.value); }
});
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${input.scrollHeight}px`; });
document.getElementById('btn-new').addEventListener('click', newConversation);

// pergunta aleatória do pool (o mesmo usado na tela da qualidade); não repete até esgotar
let pool = null;
let poolLeft = [];
function parsePool(text) {
  const rows = [];
  for (const line of text.replace(/^\uFEFF/, '').split(/\r?\n/).slice(1)) {
    if (!line.trim()) continue;
    const m = line.match(/^[^,]*,(?:"((?:[^"]|"")*)"|([^,]*))/);
    if (m) rows.push((m[1] ?? m[2]).replace(/""/g, '"'));
  }
  return rows;
}
document.getElementById('btn-random').addEventListener('click', async () => {
  try {
    if (!pool) pool = parsePool(await (await fetch('/static/exemplos/pool_perguntas.csv')).text());
    if (!poolLeft.length) poolLeft = [...pool];
    const [q] = poolLeft.splice(Math.floor(Math.random() * poolLeft.length), 1);
    input.value = q; input.dispatchEvent(new Event('input')); input.focus();
  } catch (e) { errorBox.textContent = 'Não consegui carregar o pool de perguntas.'; }
});
backstage.addEventListener('change', () => {
  Store.set('backstage', backstage.checked ? '1' : '');
  document.querySelectorAll('.backstage').forEach(b => { b.hidden = !backstage.checked; });
});

(async function init() {
  markNav('/cliente');
  backstage.checked = Store.get('backstage') === '1';
  const chips = document.getElementById('chips');
  for (const ex of EXAMPLES) chips.append(el('button', { class: 'chip', type: 'button', text: ex, onclick: () => send(ex) }));
  await gate({
    title: 'Atendimento Getnet',
    subtitle: 'Para a demonstração, use o cliente de teste cliente1988 (tem vendas, recebimentos e máquina na base simulada).',
    fields: [{ key: 'user_id', label: 'Código do cliente', value: 'cliente1988', max: 64 }],
  });
  userId = Store.get('user_id');
  document.getElementById('whoami').textContent = `Cliente: ${userId}`;
  conversationId = Store.get(storageKey());
  if (conversationId) {
    try { await fetchMessages(); } catch (e) { newConversation(); }
  }
  startPolling();
  input.focus();
})();
