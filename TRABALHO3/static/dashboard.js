/**
 * dashboard.js — Lógica do dashboard P4 Telemetry
 * Conecta ao servidor via Socket.IO, recebe atualizações em tempo real
 * e atualiza a tabela, os gráficos (Chart.js) e o histórico.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Constantes de configuração
// ─────────────────────────────────────────────────────────────────────────────

const MAX_CHART_POINTS = 40;   // pontos máximos exibidos nos gráficos
const MAX_HISTORY_ROWS = 200;  // linhas máximas na tabela de histórico

// Cores para cada métrica nos gráficos
const CHART_COLORS = {
  packet_count: { line: '#4cda9a', fill: 'rgba(76,218,154,0.1)' },
  byte_count:   { line: '#00e5c4', fill: 'rgba(0,229,196,0.1)'  },
  icmp_count:   { line: '#f0a500', fill: 'rgba(240,165,0,0.1)'  },
  min_ttl:      { line: '#e05a5a', fill: 'rgba(224,90,90,0.1)'  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Estado da aplicação
// ─────────────────────────────────────────────────────────────────────────────

// { switchId (str) -> { charts: {metric -> Chart}, history: [] } }
const switchState = {};

// Buffer completo do histórico para a tabela de histórico
const allHistory = [];

// ─────────────────────────────────────────────────────────────────────────────
// Utilitários
// ─────────────────────────────────────────────────────────────────────────────

/** Formata número grande com separadores de milhar */
function fmt(n) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('pt-BR');
}

/** Cria ou retorna a linha da tabela de valores atuais para um switchId */
function getOrCreateTableRow(switchId) {
  const tbody = document.getElementById('metrics-tbody');
  let row = document.getElementById(`row-sw-${switchId}`);
  if (!row) {
    row = document.createElement('tr');
    row.id = `row-sw-${switchId}`;
    row.innerHTML = `
      <td class="switch-id"><span class="sw-badge">SW ${switchId}</span></td>
      <td class="val-packets big-num" id="pkts-${switchId}">—</td>
      <td class="val-bytes  big-num" id="bytes-${switchId}">—</td>
      <td class="val-icmp   big-num" id="icmp-${switchId}">—</td>
      <td class="val-ttl    big-num" id="ttl-${switchId}">—</td>
      <td class="val-ts"            id="ts-${switchId}">—</td>
    `;
    tbody.appendChild(row);
  }
  return row;
}

/** Pisca a linha da tabela para indicar atualização */
function flashRow(row) {
  row.classList.remove('row-updated');
  // força reflow para reiniciar a animação CSS
  void row.offsetWidth;
  row.classList.add('row-updated');
}

/** Atualiza o filtro de switch no seletor do histórico */
function updateFilterSelect(switchId) {
  const sel = document.getElementById('filter-switch');
  if (!sel.querySelector(`option[value="${switchId}"]`)) {
    const opt = document.createElement('option');
    opt.value = switchId;
    opt.textContent = `Switch ${switchId}`;
    sel.appendChild(opt);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Gráficos (Chart.js)
// ─────────────────────────────────────────────────────────────────────────────

/** Opções base compartilhadas por todos os gráficos */
function baseChartOptions(label) {
  return {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label,
        data: [],
        borderWidth: 2,
        borderColor: '#00e5c4',
        backgroundColor: 'rgba(0,229,196,0.08)',
        pointRadius: 2,
        pointHoverRadius: 4,
        fill: true,
        tension: 0.35,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#111620',
          borderColor: '#1e2a3a',
          borderWidth: 1,
          titleColor: '#00e5c4',
          bodyColor: '#c8d8e8',
          padding: 10,
          titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
          bodyFont:  { family: "'JetBrains Mono', monospace", size: 11 },
        },
      },
      scales: {
        x: {
          grid:  { color: 'rgba(30,42,58,0.7)' },
          ticks: { color: '#5a7490', font: { size: 10, family: "'JetBrains Mono', monospace" },
                   maxTicksLimit: 6 },
        },
        y: {
          grid:  { color: 'rgba(30,42,58,0.7)' },
          ticks: { color: '#5a7490', font: { size: 10, family: "'JetBrains Mono', monospace" } },
          beginAtZero: true,
        },
      },
    },
  };
}

const METRICS = [
  { key: 'packet_count', label: 'Pacotes',   unit: 'pkts' },
  { key: 'byte_count',   label: 'Bytes',     unit: 'B'    },
  { key: 'icmp_count',   label: 'ICMP',      unit: 'pkts' },
  { key: 'min_ttl',      label: 'TTL mín.',  unit: ''     },
];

/**
 * Cria o card de gráficos para um switch, com abas para trocar a métrica.
 * Cada aba mostra um gráfico de linha diferente.
 */
function createChartCard(switchId) {
  const chartsArea = document.getElementById('charts-area');

  const card = document.createElement('div');
  card.className = 'chart-card';
  card.id = `chart-card-sw-${switchId}`;

  // Cabeçalho com abas
  const tabsHtml = METRICS.map((m, i) =>
    `<button class="tab-btn ${i === 0 ? 'active' : ''}"
             data-metric="${m.key}"
             data-sw="${switchId}"
             onclick="switchTab(this)">
       ${m.label}
     </button>`
  ).join('');

  card.innerHTML = `
    <div class="chart-card-header">
      <h3>Switch ${switchId}</h3>
      <div class="chart-tabs">${tabsHtml}</div>
    </div>
    <div class="chart-body">
      ${METRICS.map((m, i) =>
        `<canvas id="chart-${switchId}-${m.key}"
                 style="display:${i===0?'block':'none'};position:absolute;inset:1rem 1.25rem 1.25rem;">
         </canvas>`
      ).join('')}
    </div>
  `;

  chartsArea.appendChild(card);

  // Inicializa Chart.js para cada métrica
  const charts = {};
  METRICS.forEach((m) => {
    const canvas = document.getElementById(`chart-${switchId}-${m.key}`);
    const cfg    = baseChartOptions(`${m.label} (${m.unit})`);
    const c      = cfg.data.datasets[0];
    c.borderColor      = CHART_COLORS[m.key].line;
    c.backgroundColor  = CHART_COLORS[m.key].fill;
    charts[m.key] = new Chart(canvas, cfg);
  });

  return charts;
}

/** Alterna a aba ativa de um card de gráfico */
function switchTab(btn) {
  const sw     = btn.dataset.sw;
  const metric = btn.dataset.metric;
  const card   = document.getElementById(`chart-card-sw-${sw}`);

  // Desativa todas as abas e esconde todos os canvas
  card.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  card.querySelectorAll('canvas').forEach(c => c.style.display = 'none');

  btn.classList.add('active');
  document.getElementById(`chart-${sw}-${metric}`).style.display = 'block';
}

// expõe globalmente para o onclick inline
window.switchTab = switchTab;

/**
 * Adiciona um ponto de dado ao gráfico de um switch/métrica.
 * Remove o ponto mais antigo quando MAX_CHART_POINTS é atingido.
 */
function pushChartPoint(switchId, metric, timestamp, value) {
  const chart = switchState[switchId]?.charts[metric];
  if (!chart) return;

  const { labels, datasets } = chart.data;
  labels.push(timestamp);
  datasets[0].data.push(value);

  if (labels.length > MAX_CHART_POINTS) {
    labels.shift();
    datasets[0].data.shift();
  }

  chart.update('none'); // sem animação para performance
}

// ─────────────────────────────────────────────────────────────────────────────
// Tabela de histórico
// ─────────────────────────────────────────────────────────────────────────────

function addHistoryRow(metrics) {
  allHistory.unshift(metrics); // mais recente no topo
  if (allHistory.length > MAX_HISTORY_ROWS) allHistory.pop();
  renderHistory();
}

function renderHistory() {
  const filter = document.getElementById('filter-switch').value;
  const tbody  = document.getElementById('history-tbody');
  const rows   = allHistory.filter(m =>
    filter === 'all' || String(m.switch_id) === filter
  );

  tbody.innerHTML = rows.map(m => `
    <tr>
      <td class="val-ts">${m.timestamp}</td>
      <td class="switch-id"><span class="sw-badge">SW ${m.switch_id}</span></td>
      <td class="val-packets">${fmt(m.packet_count)}</td>
      <td class="val-bytes">${fmt(m.byte_count)}</td>
      <td class="val-icmp">${fmt(m.icmp_count)}</td>
      <td class="val-ttl">${m.min_ttl}</td>
    </tr>
  `).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Atualização principal — chamada a cada evento do Socket.IO
// ─────────────────────────────────────────────────────────────────────────────

function processMetrics(switchId, metrics) {
  const sid = String(switchId);

  // Primeira vez que vemos este switch → inicializa estruturas
  if (!switchState[sid]) {
    switchState[sid] = {
      charts: createChartCard(sid),
    };
    updateFilterSelect(sid);
    document.getElementById('no-data-msg').classList.add('hidden');
    document.getElementById('table-section').classList.remove('hidden');
    document.getElementById('history-section').classList.remove('hidden');

    // Atualiza contador de switches no header
    document.getElementById('switch-count').textContent = Object.keys(switchState).length;
  }

  // Atualiza tabela de valores atuais
  const row = getOrCreateTableRow(sid);
  document.getElementById(`pkts-${sid}`).textContent  = fmt(metrics.packet_count);
  document.getElementById(`bytes-${sid}`).textContent = fmt(metrics.byte_count);
  document.getElementById(`icmp-${sid}`).textContent  = fmt(metrics.icmp_count);
  document.getElementById(`ttl-${sid}`).textContent   = metrics.min_ttl;
  document.getElementById(`ts-${sid}`).textContent    = metrics.timestamp;
  flashRow(row);

  // Alimenta gráficos
  METRICS.forEach(m => {
    pushChartPoint(sid, m.key, metrics.timestamp, metrics[m.key]);
  });

  // Adiciona ao histórico
  addHistoryRow(metrics);

  // Atualiza badge de status
  const badge = document.getElementById('status-badge');
  badge.classList.add('active');
  document.getElementById('status-text').textContent = 'Recebendo';
}

// ─────────────────────────────────────────────────────────────────────────────
// Decisões do controlador
// ─────────────────────────────────────────────────────────────────────────────

function processPolicy(switchId, policy) {
  if (!policy) return;

  const section = document.getElementById('policy-section');
  section.classList.remove('hidden');

  document.getElementById('policy-limit').textContent = policy.limit;
  document.getElementById('policy-rate').textContent = policy.pkts_per_sec;

  const statusEl = document.getElementById('policy-status');
  const blockedList = document.getElementById('blocked-list');
  const logList = document.getElementById('action-log-list');

  if (policy.blocked) {
    statusEl.textContent = 'Bloqueado';
    statusEl.className = 'status-blocked';
    blockedList.innerHTML = `<li>10.0.0.1 (SW${switchId})</li>`;
  } else {
    statusEl.textContent = 'Normal';
    statusEl.className = 'status-normal';
    blockedList.innerHTML = '<li class="empty">Nenhum IP bloqueado</li>';
  }

  if (policy.action) {
    const actionText = policy.action === 'block' ? 'BLOQUEIO' : 'DESBLOQUEIO';
    const li = document.createElement('li');
    li.textContent = `[${new Date().toLocaleTimeString()}] ${actionText} em SW${switchId} @ ${policy.pkts_per_sec} pkts/s`;
    logList.prepend(li);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Socket.IO
// ─────────────────────────────────────────────────────────────────────────────

const socket = io({
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 1000,
});

function setConnectionStatus(text, isError = false) {
  const badge = document.getElementById('status-badge');
  const statusText = document.getElementById('status-text');
  statusText.textContent = text;
  badge.classList.remove('active');
  if (isError) {
    badge.classList.add('error');
  } else {
    badge.classList.remove('error');
  }
}

socket.on('connect', () => {
  setConnectionStatus('Conectado');
  console.log('[Socket.IO] Conectado ao servidor');
});

socket.on('disconnect', (reason) => {
  setConnectionStatus(`Desconectado (${reason})`, true);
  console.warn('[Socket.IO] Desconectado:', reason);
});

socket.on('connect_error', (err) => {
  setConnectionStatus('Erro de conexão', true);
  console.error('[Socket.IO] Erro de conexão:', err.message);
});

/** Estado inicial: servidor envia snapshot de todos os switches conhecidos */
socket.on('initial_state', (snapshot) => {
  console.log('[Socket.IO] Estado inicial:', snapshot);
  Object.entries(snapshot).forEach(([sid, data]) => {
    // Replay do histórico para popular os gráficos
    if (data.history && data.history.length > 0) {
      data.history.forEach(m => processMetrics(m.switch_id, m));
    } else if (data.metrics) {
      processMetrics(sid, data.metrics);
    }
    if (data.policy) {
      processPolicy(sid, data.policy);
    }
  });
});

/** Atualização em tempo real: um pacote de telemetria chegou */
socket.on('telemetry_update', (payload) => {
  console.debug('[Telemetry]', payload);
  processMetrics(payload.switch_id, payload.metrics);
  processPolicy(payload.switch_id, payload.policy);
});

// ─────────────────────────────────────────────────────────────────────────────
// Controles do histórico
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById('filter-switch').addEventListener('change', renderHistory);

document.getElementById('clear-history-btn').addEventListener('click', () => {
  allHistory.length = 0;
  document.getElementById('history-tbody').innerHTML = '';
});
