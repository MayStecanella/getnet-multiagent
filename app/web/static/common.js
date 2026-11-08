// Utilidades compartilhadas pelas telas: armazenamento, chamadas à API, acesso e renderização segura.
const Store = {
  get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* armazenamento indisponível */ } },
  del(k) { try { sessionStorage.removeItem(k); } catch (e) { /* idem */ } },
};

let CONFIG = null;
async function loadConfig() {
  if (!CONFIG) {
    try { CONFIG = await fetch('/api/config').then(r => r.json()); }
    catch (e) { CONFIG = { access_code_required: false, offline_mode: false }; }
  }
  const tag = document.getElementById('offline-tag');
  if (tag && CONFIG.offline_mode) tag.hidden = false;
  return CONFIG;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Access-Code': Store.get('access_code') || '' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* sem corpo */ }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = 'dados inválidos';
    const err = new Error(detail || res.statusText || 'erro na requisição');
    err.status = res.status;
    if (res.status === 401) Store.del('access_code');
    throw err;
  }
  return data;
}

// Cria elementos com texto sempre via textContent (nada de HTML vindo do servidor)
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function link(href, label) {
  return el('a', { href, target: '_blank', rel: 'noopener noreferrer', text: label });
}

// Converte [texto](url), URLs soltas e **negrito** em elementos, sem interpretar HTML
function renderRich(text, node) {
  const re = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()"]*[^\s<>()".,;:!?])|\*\*([^*]+)\*\*/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) node.append(text.slice(last, m.index));
    if (m[1]) node.append(link(m[2], m[1]));
    else if (m[3]) node.append(link(m[3], m[3]));
    else if (m[4]) node.append(el('strong', { text: m[4] }));
    last = re.lastIndex;
  }
  if (last < text.length) node.append(text.slice(last));
  return node;
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}
function fmtDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function fmtWait(s) {
  if (s === null || s === undefined) return '';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}`;
}
function scoreClass(total) { return total >= 80 ? 'good' : total >= 70 ? 'mid' : 'low'; }
function scorePill(total) {
  return el('span', { class: `score ${scoreClass(total)}`, text: total ?? '–' });
}
const REASONS = { nao_resolvido: 'Cliente: não resolveu', tentativas_esgotadas: '4ª tentativa no tema',
  pedido_do_cliente: 'Pedido do cliente', tema_repetido: 'Tema repetido' };
const AGENTS = { knowledge: 'Knowledge', self_service: 'Autosserviço', support: 'Support', web_search: 'Busca web' };
const PILLARS = [['util', 'Útil'], ['calmo', 'Calmo'], ['amigavel', 'Amigável'], ['empoderado', 'Empoderado']];

// Portão de acesso: pede o código (se a publicação exigir) e campos extras de cada tela
async function gate({ title, subtitle, fields = [] }) {
  const cfg = await loadConfig();
  const needCode = cfg.access_code_required;
  const missing = fields.filter(f => !Store.get(f.key));
  if (needCode && Store.get('access_code')) {
    try { await api('GET', '/api/auth/check'); } catch (e) { Store.del('access_code'); }
  }
  if ((!needCode || Store.get('access_code')) && missing.length === 0) return;

  return new Promise(resolve => {
    const inputs = {};
    const error = el('div', { class: 'error-text', role: 'alert' });
    const form = el('form', { class: 'card' },
      el('h2', { text: title }),
      subtitle ? el('p', { class: 'muted small', style: 'margin:0', text: subtitle }) : null,
      ...fields.map(f => {
        inputs[f.key] = el('input', { id: `gate-${f.key}`, value: Store.get(f.key) || f.value || '',
          placeholder: f.placeholder || '', required: true, maxlength: f.max || 60, autocomplete: 'off' });
        return el('div', { class: 'field' }, el('label', { for: `gate-${f.key}`, text: f.label }), inputs[f.key]);
      }),
      needCode ? el('div', { class: 'field' },
        el('label', { for: 'gate-code', text: 'Código de acesso' }),
        (inputs.access_code = el('input', { id: 'gate-code', type: 'password', required: true, autocomplete: 'off' }))) : null,
      error,
      el('button', { class: 'btn primary', type: 'submit', text: 'Entrar' }),
    );
    const overlay = el('div', { class: 'gate' }, form);
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      error.textContent = '';
      for (const f of fields) {
        const v = inputs[f.key].value.trim();
        if (!v) { error.textContent = `Preencha: ${f.label}`; return; }
        Store.set(f.key, v);
      }
      if (needCode) {
        Store.set('access_code', inputs.access_code.value.trim());
        try { await api('GET', '/api/auth/check'); }
        catch (e) { error.textContent = 'Código de acesso inválido.'; return; }
      }
      overlay.remove();
      resolve();
    });
    document.body.append(overlay);
    (fields.length ? inputs[fields[0].key] : inputs.access_code).focus();
  });
}

function markNav(path) {
  document.querySelectorAll('.topbar nav a').forEach(a => {
    if (a.getAttribute('href') === path) a.classList.add('active');
  });
}
