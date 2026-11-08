// Tela da qualidade: indicadores, DNA por pilar (IA x especialistas), avaliações e lote off-line.
let author = '';
let openEval = null;
let batchTimer = null;
let currentBatch = null;

const pct = v => (v === null || v === undefined ? '–' : `${String(v).replace('.', ',')}%`);
const num = v => (v === null || v === undefined ? '–' : String(v).replace('.', ','));

function kpi(label, value, hint) {
  return el('div', { class: 'card kpi' },
    el('span', { class: 'label', text: label }), el('span', { class: 'value', text: value }),
    el('span', { class: 'hint', text: hint || '' }));
}

function renderKpis(m) {
  const t = m.transbordo, a = m.assertividade, d = m.dna_getnet, r = m.resolucao;
  document.getElementById('kpis').replaceChildren(
    kpi('Atendimentos da IA', num(m.atendimentos), `latência média ${m.latencia_media_ms ? Math.round(m.latencia_media_ms / 100) / 10 + ' s' : '–'}`),
    kpi('Resolvido pela IA', pct(r.taxa_resolucao_confirmada), `confirmado pelo cliente: ${r.resolvidas} sim · ${r.nao_resolvidas} não`),
    kpi('Resolução sem recontato', pct(a.taxa_resolucao_sem_recontato), `${a.recontatos} recontato(s) em ${a.atendimentos_com_tema}`),
    kpi('DNA médio da IA', num(d.ia.media_total), `${d.ia.avaliacoes} avaliações · ${d.ia.abaixo_de_70} abaixo de 70`),
    kpi('DNA médio dos especialistas', num(d.humano.media_total), `${d.humano.avaliacoes} avaliações · ${d.humano.abaixo_de_70} abaixo de 70`),
    kpi('Taxa de transbordo', pct(t.taxa_transbordo), `${t.na_fila} na fila · ${t.em_atendimento} em atendimento`),
  );
}

function renderPillars(d) {
  const keys = { util: 'media_util', calmo: 'media_calmo', amigavel: 'media_amigavel', empoderado: 'media_empoderado' };
  const rows = PILLARS.map(([key, label]) => {
    const bar = (cls, v) => el('div', { class: 'pillar-bar' },
      el('div', { class: `bar ${cls}` }, el('span', { style: `width:${v ? (v / 25) * 100 : 0}%` })),
      el('span', { class: 'mini', text: v === null ? '–' : `${num(v)}/25` }));
    return el('div', { class: 'pillar-row' }, el('strong', { class: 'small', text: label }),
      el('div', { class: 'pillar-bars' }, bar('ia', d.ia[keys[key]]), bar('humano', d.humano[keys[key]])));
  });
  const alerts = el('p', { class: 'small muted', style: 'margin:10px 0 0',
    text: `Alertas: ${d.alertas_sem_fonte} resposta(s) sem fonte · ${d.alertas_dado_sensivel} com dado sensível` });
  document.getElementById('pillars').replaceChildren(...rows, alerts);
}

function statRows(entries, total, cls) {
  if (!entries.length) return [el('div', { class: 'empty', text: 'Sem dados ainda.' })];
  return entries.map(([label, value]) => el('div', { class: 'stat-row' },
    el('span', { text: label }),
    el('div', { class: `bar ${cls || ''}` }, el('span', { style: `width:${total ? (value / total) * 100 : 0}%` })),
    el('span', { class: 'mini', style: 'text-align:right', text: value })));
}

function renderFlow(m) {
  const t = m.transbordo;
  const triageLabels = { adquirencia: 'Adquirência', web: 'Busca web', dado_sensivel: 'Dado sensível',
    injection: 'Prompt injection', fora_de_escopo: 'Fora de escopo', humano: 'Pedido de atendente' };
  const triage = Object.entries(m.triagem).sort((a, b) => b[1] - a[1]);
  const triageTotal = triage.reduce((s, [, v]) => s + v, 0);
  const reasons = Object.entries(t.por_motivo).map(([k, v]) => [REASONS[k] || k, v]);
  document.getElementById('flow').replaceChildren(
    el('h3', { class: 'small muted', style: 'margin:0 0 4px', text: 'Motivo do transbordo' }),
    ...statRows(reasons, t.transbordadas || 0),
    el('p', { class: 'small muted', style: 'margin:6px 0 14px',
      text: `Espera média até o especialista assumir: ${t.espera_media_s === null ? '–' : fmtWait(Math.round(t.espera_media_s))}` }),
    el('h3', { class: 'small muted', style: 'margin:0 0 4px', text: 'Triagem das mensagens' }),
    ...statRows(triage.map(([k, v]) => [triageLabels[k] || k, v]), triageTotal, 'ia'),
  );
}

function renderAgents(d) {
  const box = document.getElementById('agents');
  if (!d.por_especialista.length) { box.replaceChildren(el('div', { class: 'empty', text: 'Nenhuma resposta de especialista avaliada ainda.' })); return; }
  const worst = r => PILLARS.reduce((w, [k, label]) => (r[k] < w[1] ? [label, r[k]] : w), ['', 99])[0];
  box.replaceChildren(el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, el('th', { text: 'Especialista' }), el('th', { class: 'num', text: 'Respostas' }),
      el('th', { class: 'num', text: 'DNA médio' }), el('th', { text: 'Pilar a desenvolver' }))),
    el('tbody', {}, ...d.por_especialista.map(r => el('tr', {},
      el('td', { text: r.especialista }), el('td', { class: 'num', text: r.avaliacoes }),
      el('td', { class: 'num' }, scorePill(Math.round(r.dna_medio))), el('td', { text: worst(r) }))))));
}

function renderThemes(a) {
  document.getElementById('window').textContent = a.janela_horas;
  const box = document.getElementById('themes');
  if (!a.por_tema.length) { box.replaceChildren(el('div', { class: 'empty', text: 'Sem atendimentos com tema ainda.' })); return; }
  box.replaceChildren(el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, el('th', { text: 'Tema' }), el('th', { class: 'num', text: 'Atendimentos' }),
      el('th', { class: 'num', text: 'Recontatos' }), el('th', { class: 'num', text: 'Resolução' }))),
    el('tbody', {}, ...a.por_tema.map(r => el('tr', {},
      el('td', { text: r.theme }), el('td', { class: 'num', text: r.atendimentos }),
      el('td', { class: 'num', text: r.recontatos }),
      el('td', { class: 'num', text: pct(Math.round(1000 * (1 - r.recontatos / r.atendimentos)) / 10) }))))));
}

function evalDetail(e) {
  return el('tr', { class: 'ev-detail' }, el('td', { colspan: 6 },
    el('div', { class: 'boxes' },
      el('div', {}, el('h4', { text: 'Mensagem do cliente' }), el('div', { class: 'text', text: e.message || '—' })),
      el('div', {}, el('h4', { text: 'Resposta avaliada' }), renderRich(e.response || '—', el('div', { class: 'text' })))),
    el('h4', { style: 'margin-top:12px', text: 'Justificativas do avaliador' }),
    el('table', { class: 'data' }, el('tbody', {}, ...PILLARS.map(([k, label]) => el('tr', {},
      el('td', { style: 'width:110px', text: label }), el('td', { class: 'num', style: 'width:60px', text: `${e[k]}/25` }),
      el('td', { text: e.justifications[k] })))))));
}

async function loadEvals() {
  const below = document.getElementById('below').checked ? '&below=70' : '';
  const { evaluations } = await api('GET', `/api/quality/evaluations?limit=40${author ? `&author=${author}` : ''}${below}`);
  const box = document.getElementById('evals');
  if (!evaluations.length) { box.replaceChildren(el('div', { class: 'empty', text: 'Nenhuma avaliação para este filtro.' })); return; }
  const rows = [];
  for (const e of evaluations) {
    const alerts = [e.alert_sem_fonte ? el('span', { class: 'tag warn', text: 'sem fonte' }) : null,
      e.alert_dado_sensivel ? el('span', { class: 'tag bad', text: 'dado sensível' }) : null];
    rows.push(el('tr', { class: 'ev-row', tabindex: 0, onclick: () => { openEval = openEval === e.id ? null : e.id; loadEvals(); } },
      el('td', { class: 'mini', text: fmtDateTime(e.created_at) }),
      el('td', {}, e.author === 'ia' ? el('span', { class: 'tag ia', text: 'IA' }) : el('span', { class: 'tag ok', text: e.agent_name || 'Especialista' })),
      el('td', { text: (e.message || '').slice(0, 70) }),
      el('td', { class: 'num' }, scorePill(e.total)),
      el('td', { class: 'mini', text: PILLARS.map(([k, l]) => `${l[0]} ${e[k]}`).join(' · ') }),
      el('td', {}, ...alerts)));
    if (openEval === e.id) rows.push(evalDetail(e));
  }
  box.replaceChildren(el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, el('th', { text: 'Quando' }), el('th', { text: 'Autor' }), el('th', { text: 'Mensagem' }),
      el('th', { class: 'num', text: 'Nota' }), el('th', { text: 'Pilares (Ú C A E)' }), el('th', { text: 'Alertas' }))),
    el('tbody', {}, ...rows)));
}

async function refresh() {
  const m = await api('GET', '/api/quality/metrics');
  renderKpis(m); renderPillars(m.dna_getnet); renderFlow(m); renderAgents(m.dna_getnet); renderThemes(m.assertividade);
  await loadEvals();
  document.getElementById('updated').textContent = `Atualizado às ${new Date().toLocaleTimeString('pt-BR')}`;
}

// --- lote off-line ---------------------------------------------------------------
function batchSummary(b) {
  const s = b.summary;
  const items = [
    kpi('Linhas avaliadas', `${s.avaliadas}/${s.linhas}`, b.mode === 'replay' ? 'modo replay' : 'modo respostas já dadas'),
    kpi('DNA médio', num(s.dna_medio), `${s.abaixo_de_70} abaixo de 70`),
  ];
  if (s.acuracia_rota !== undefined) items.push(kpi('Acurácia de rota', pct(s.acuracia_rota), 'triagem e agentes esperados'));
  items.push(kpi('Alertas', String(s.alertas_sem_fonte + s.alertas_dado_sensivel),
    `${s.alertas_sem_fonte} sem fonte · ${s.alertas_dado_sensivel} dado sensível`));
  const pillars = el('p', { class: 'small muted', text: 'Média por pilar: ' +
    PILLARS.map(([k, l]) => `${l} ${num(s.por_pilar[k])}`).join(' · ') +
    (s.por_especialista ? ' · Por especialista: ' + Object.entries(s.por_especialista).map(([k, v]) => `${k} ${num(v)}`).join(', ') : '') });
  return [el('div', { class: 'kpis', style: 'margin-top:14px' }, ...items), pillars];
}

function batchTable(b) {
  const replay = b.mode === 'replay';
  const head = ['#', 'Mensagem', replay ? 'Rota obtida' : 'Especialista', replay ? 'Rota ok' : null, 'Nota', 'Pilares (Ú C A E)', 'Resposta']
    .filter(Boolean);
  return el('div', { style: 'overflow-x:auto' }, el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, ...head.map(h => el('th', { text: h })))),
    el('tbody', {}, ...b.rows.map(r => {
      const d = r.dna;
      const okCell = r.rota_ok === undefined ? '–' : r.rota_ok ? el('span', { class: 'tag ok', text: 'sim' }) : el('span', { class: 'tag bad', text: 'não' });
      return el('tr', {},
        el('td', { class: 'mini', text: r.linha }),
        el('td', { text: r.mensagem }),
        replay ? el('td', { class: 'mini', text: `${r.triagem || 'erro'}${r.agentes && r.agentes.length ? ' → ' + r.agentes.map(a => AGENTS[a] || a).join(' + ') : ''}` })
          : el('td', { text: r.especialista || '–' }),
        replay ? el('td', {}, okCell) : null,
        el('td', { class: 'num' }, d ? scorePill(d.total) : el('span', { class: 'tag bad', text: 'erro' })),
        el('td', { class: 'mini', text: d ? PILLARS.map(([k, l]) => `${l[0]} ${d[k]}`).join(' · ') : (r.erro || '') }),
        el('td', { class: 'small', title: r.resposta || '', text: (r.resposta || '').slice(0, 90) + ((r.resposta || '').length > 90 ? '…' : '') }));
    }))));
}

function downloadCsv(b) {
  const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const header = ['linha', 'mensagem', 'resposta', 'triagem', 'agentes', 'rota_ok', 'especialista', 'total', 'util', 'calmo', 'amigavel', 'empoderado'];
  const lines = b.rows.map(r => [r.linha, r.mensagem, r.resposta, r.triagem, (r.agentes || []).join('+'), r.rota_ok,
    r.especialista, r.dna?.total, r.dna?.util, r.dna?.calmo, r.dna?.amigavel, r.dna?.empoderado].map(esc).join(';'));
  const blob = new Blob(['﻿' + [header.join(';'), ...lines].join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = el('a', { href: URL.createObjectURL(blob), download: `avaliacao_${b.name.replace(/\W+/g, '_')}.csv` });
  document.body.append(a); a.click(); a.remove();
}

function renderBatch(b) {
  currentBatch = b;
  const status = document.getElementById('batch-status');
  const result = document.getElementById('batch-result');
  if (b.status === 'executando') {
    status.replaceChildren(el('div', { style: 'display:flex;gap:10px;align-items:center;margin-top:12px' },
      el('span', { class: 'small', text: `Avaliando ${b.name}: ${b.done}/${b.total}` }),
      el('div', { class: 'progress' }, el('span', { style: `width:${(b.done / b.total) * 100}%` }))));
    result.replaceChildren();
    return;
  }
  status.replaceChildren(el('div', { style: 'display:flex;gap:10px;align-items:center;margin-top:12px' },
    el('strong', { text: b.name }), el('span', { class: `tag ${b.status === 'concluido' ? 'ok' : 'bad'}`, text: b.status }),
    b.status === 'concluido' ? el('button', { class: 'btn', type: 'button', text: 'Baixar resultado (CSV)', onclick: () => downloadCsv(currentBatch) }) : null));
  result.replaceChildren(...(b.status === 'concluido' ? [...batchSummary(b), batchTable(b)]
    : [el('p', { class: 'error-text', text: b.error || 'Falha no lote.' })]));
}

async function pollBatch(id) {
  clearTimeout(batchTimer);
  const b = await api('GET', `/api/quality/batch/${id}`);
  renderBatch(b);
  if (b.status === 'executando') batchTimer = setTimeout(() => pollBatch(id).catch(() => {}), 1500);
  else { document.getElementById('batch-run').disabled = false; loadHistory(); }
}

async function loadHistory() {
  const { batches } = await api('GET', '/api/quality/batch');
  const box = document.getElementById('batch-history');
  if (!batches.length) { box.replaceChildren(); return; }
  box.replaceChildren(el('div', { class: 'small muted', style: 'margin-bottom:6px', text: 'Lotes anteriores' }),
    el('div', { class: 'runs' }, ...batches.map(b => el('button', { class: 'btn', type: 'button',
      onclick: () => pollBatch(b.id) },
      `${b.name} · ${fmtDateTime(b.created_at)}`,
      b.summary ? el('span', { class: `score ${scoreClass(b.summary.dna_medio || 0)}`, text: num(b.summary.dna_medio) }) : null))));
}

document.getElementById('batch-form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const error = document.getElementById('batch-error');
  error.textContent = '';
  const file = document.getElementById('batch-file').files[0];
  if (!file) { error.textContent = 'Escolha um arquivo CSV.'; return; }
  const btn = document.getElementById('batch-run');
  btn.disabled = true;
  try {
    const csv = await file.text();
    const started = await api('POST', '/api/quality/batch', { csv, name: file.name });
    await pollBatch(started.id);
  } catch (e) {
    error.textContent = e.message; btn.disabled = false;
  }
});

document.getElementById('pool-form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const error = document.getElementById('batch-error');
  error.textContent = '';
  const n = Math.max(1, Math.min(50, parseInt(document.getElementById('pool-n').value, 10) || 10));
  const btns = [document.getElementById('pool-run'), document.getElementById('batch-run')];
  btns.forEach(b => { b.disabled = true; });
  try {
    const csv = await (await fetch('/static/exemplos/pool_perguntas.csv', { cache: 'no-store' })).text();
    const started = await api('POST', '/api/quality/batch', { csv, name: `${n} perguntas aleatórias`, sample: n });
    await pollBatch(started.id);
  } catch (e) {
    error.textContent = e.message;
  } finally {
    document.getElementById('pool-run').disabled = false;
  }
});

document.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.seg button').forEach(x => x.classList.toggle('on', x === b));
  author = b.dataset.author; openEval = null; loadEvals();
}));
document.getElementById('below').addEventListener('change', () => { openEval = null; loadEvals(); });

(async function init() {
  markNav('/qualidade');
  await gate({ title: 'Qualidade do atendimento' });
  await refresh().catch(e => { document.getElementById('kpis').replaceChildren(el('div', { class: 'empty', text: e.message })); });
  loadHistory().catch(() => {});
  setInterval(() => refresh().catch(() => {}), 10000);
})();
