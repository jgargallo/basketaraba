// BasketAraba stats SPA. Hash-based router, fetches data/*.json on demand,
// renders pages into #view. Charts via Chart.js.

const BRAND = '#ff5722';
const BRAND_LIGHT = '#ffd2bd';
const INK_500 = '#5b6677';
const INK_200 = '#dde1ea';
const WIN = '#10b981';
const LOSS = '#f43f5e';

const $view = () => document.getElementById('view');
const cache = new Map();
let seasonIndex = {};   // full seasons dict from index.json
let groups = [];        // groups for currentSeason
let currentSeason = null;
let currentGroup = null;
const charts = [];

// ---------------- utilities ----------------
function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(e.dataset, v);
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    if (typeof c === 'string' || typeof c === 'number') e.appendChild(document.createTextNode(c));
    else e.appendChild(c);
  }
  return e;
}
function fmt(n, digits = 1) {
  if (n == null || Number.isNaN(n)) return '–';
  if (typeof n !== 'number') return String(n);
  return Number.isInteger(n) ? n.toString() : n.toFixed(digits);
}
function pct(n) {
  if (n == null) return '–';
  return (n * 100).toFixed(1) + '%';
}
function ddmmyyyy(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function loadJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const r = await fetch(path);
  if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
  const data = await r.json();
  cache.set(path, data);
  return data;
}

function groupPath(path) {
  const prefix = currentSeason ? `data/${currentSeason}/${currentGroup}` : `data/${currentGroup}`;
  return `${prefix}/${path}`;
}

async function loadGroups() {
  const data = await loadJSON('data/index.json');
  // New season-aware structure: {current_season, seasons: {...}}
  if (data.seasons) {
    seasonIndex = data.seasons;
    currentSeason = data.current_season || Object.keys(data.seasons).sort().reverse()[0] || null;
    const seasonData = currentSeason ? (data.seasons[currentSeason] || {}) : {};
    groups = (seasonData.groups || []).map(g => ({ id: g.slug, name: g.display_name }));
  } else {
    // Legacy flat structure fallback
    seasonIndex = {};
    currentSeason = null;
    groups = data.groups || [];
  }
  if (groups.length > 0 && !currentGroup) currentGroup = groups[0].id;
}

function _populateSeasonSelector() {
  const seasons = Object.keys(seasonIndex).sort().reverse();
  document.querySelectorAll('.season-select').forEach(sel => {
    sel.innerHTML = seasons.map(s =>
      `<option value="${s}"${s === currentSeason ? ' selected' : ''}>${escapeHtml(seasonIndex[s].label || s)}</option>`
    ).join('');
  });
}

function _populateGroupSelector() {
  const opts = groups.map(g =>
    `<option value="${g.id}"${g.id === currentGroup ? ' selected' : ''}>${escapeHtml(g.name)}</option>`
  ).join('');
  document.querySelectorAll('.group-select').forEach(sel => { sel.innerHTML = opts; });
}

async function setSeason(season) {
  currentSeason = season;
  const seasonData = seasonIndex[season] || {};
  groups = (seasonData.groups || []).map(g => ({ id: g.slug, name: g.display_name }));
  currentGroup = groups.length > 0 ? groups[0].id : null;
  _populateSeasonSelector();
  _populateGroupSelector();
  if (currentGroup) {
    location.hash = `#/group/${currentGroup}/league`;
  }
}

async function setGroup(slug) {
  currentGroup = slug;
  document.querySelectorAll('.group-select').forEach(sel => { sel.value = slug; });
  document.querySelectorAll('.nav-link[data-nav]').forEach(a => {
    a.setAttribute('href', `#/group/${currentGroup}/${a.dataset.nav}`);
  });
  const lg = await loadLeague();
  document.getElementById('group-name').textContent = lg.group.group_name;
  document.getElementById('group-stats').textContent =
    `${lg.totals.teams} equipos · ${lg.totals.players} jugadores · ${lg.totals.completed}/${lg.totals.games} partidos jugados`;
  const opts = '<option value="">Ir a equipo…</option>' +
    lg.teams.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
  document.querySelectorAll('.quick-team-select').forEach(sel => { sel.innerHTML = opts; });
}

async function loadLeague() {
  return loadJSON(groupPath('league.json'));
}

function destroyCharts() {
  while (charts.length) {
    try { charts.pop().destroy(); } catch (e) { /* ignore */ }
  }
}

function setLoading(label = 'Cargando…') {
  $view().innerHTML = '';
  $view().appendChild(el('div', { class: 'text-center text-ink-500 py-20' }, label));
}
function setError(msg) {
  $view().innerHTML = '';
  $view().appendChild(el('div', { class: 'text-center text-rose-600 py-20' }, msg));
}

// ---------------- router ----------------
const routes = [
  // Group-scoped routes
  { match: /^#\/group\/([^/]+)\/(league)?$/, handler: async (m) => { await setGroup(m[1]); return renderLeague(); } },
  { match: /^#\/group\/([^/]+)\/teams$/, handler: async (m) => { await setGroup(m[1]); return renderTeams(); } },
  { match: /^#\/group\/([^/]+)\/leaders$/, handler: async (m) => { await setGroup(m[1]); return renderLeaders(); } },
  { match: /^#\/group\/([^/]+)\/schedule$/, handler: async (m) => { await setGroup(m[1]); return renderSchedule(); } },
  { match: /^#\/group\/([^/]+)\/team\/([^/]+)$/, handler: async (m) => { await setGroup(m[1]); return renderTeam(m[2]); } },
  { match: /^#\/group\/([^/]+)\/player\/([^/]+)$/, handler: async (m) => { await setGroup(m[1]); return renderPlayer(m[2]); } },
  { match: /^#\/group\/([^/]+)\/game\/([^/]+)$/, handler: async (m) => { await setGroup(m[1]); return renderGame(m[2]); } },
  // Legacy routes → redirect to current group
  { match: /^#?\/?$/, handler: () => { location.hash = `#/group/${currentGroup}/league`; } },
  { match: /^#\/league$/, handler: () => { location.hash = `#/group/${currentGroup}/league`; } },
  { match: /^#\/teams$/, handler: () => { location.hash = `#/group/${currentGroup}/teams`; } },
  { match: /^#\/leaders$/, handler: () => { location.hash = `#/group/${currentGroup}/leaders`; } },
  { match: /^#\/schedule$/, handler: () => { location.hash = `#/group/${currentGroup}/schedule`; } },
  { match: /^#\/team\/([^/]+)$/, handler: (m) => { location.hash = `#/group/${currentGroup}/team/${m[1]}`; } },
  { match: /^#\/player\/([^/]+)$/, handler: (m) => { location.hash = `#/group/${currentGroup}/player/${m[1]}`; } },
  { match: /^#\/game\/([^/]+)$/, handler: (m) => { location.hash = `#/group/${currentGroup}/game/${m[1]}`; } },
];

async function route() {
  destroyCharts();
  closeMobileMenu();
  setLoading();
  const hash = location.hash || '#/';
  for (const r of routes) {
    const m = hash.match(r.match);
    if (m) {
      try {
        await r.handler(m);
      } catch (e) {
        console.error(e);
        setError(`Error: ${e.message}`);
      }
      updateNavActive();
      window.scrollTo({ top: 0, behavior: 'instant' });
      return;
    }
  }
  setError('Ruta no encontrada');
}
function updateNavActive() {
  const hash = location.hash || '';
  document.querySelectorAll('.nav-link').forEach(a => {
    const nav = a.dataset.nav;
    let active = false;
    if (nav === 'league') active = /\/league/.test(hash);
    else if (nav === 'teams') active = /\/teams$/.test(hash) || /\/team\//.test(hash);
    else if (nav === 'leaders') active = /\/leaders$/.test(hash);
    else if (nav === 'schedule') active = /\/schedule$/.test(hash);
    a.classList.toggle('active', active);
  });
}
window.addEventListener('hashchange', route);

// ---------------- shared chrome ----------------
async function bootstrapChrome() {
  await loadGroups();

  // Update nav link hrefs to group-scoped routes.
  document.querySelectorAll('.nav-link[data-nav]').forEach(a => {
    a.setAttribute('href', `#/group/${currentGroup}/${a.dataset.nav}`);
  });

  const lg = await loadLeague();
  document.getElementById('group-name').textContent = lg.group.group_name;
  document.getElementById('group-stats').textContent =
    `${lg.totals.teams} equipos · ${lg.totals.players} jugadores · ${lg.totals.completed}/${lg.totals.games} partidos jugados`;

  // Populate season selectors (only shown when more than one season exists).
  const seasonKeys = Object.keys(seasonIndex).sort().reverse();
  document.querySelectorAll('.season-select').forEach(sel => {
    if (seasonKeys.length <= 1) {
      sel.closest('.season-select-wrapper')?.classList.add('hidden');
      sel.classList.add('hidden');
    } else {
      sel.innerHTML = seasonKeys.map(s =>
        `<option value="${s}"${s === currentSeason ? ' selected' : ''}>${escapeHtml(seasonIndex[s].label || s)}</option>`
      ).join('');
      sel.addEventListener('change', () => {
        if (sel.value) setSeason(sel.value);
        closeMobileMenu();
      });
    }
  });

  // Populate group selectors.
  _populateGroupSelector();
  document.querySelectorAll('.group-select').forEach(sel => {
    sel.addEventListener('change', () => {
      if (sel.value) location.hash = `#/group/${sel.value}/league`;
      closeMobileMenu();
    });
  });

  // Populate both team selectors (desktop inline + mobile in dropdown).
  const opts = '<option value="">Ir a equipo…</option>' +
    lg.teams.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
  document.querySelectorAll('.quick-team-select').forEach(sel => {
    sel.innerHTML = opts;
    sel.addEventListener('change', () => {
      if (sel.value) location.hash = `#/group/${currentGroup}/team/${sel.value}`;
      sel.value = '';
      closeMobileMenu();
    });
  });

  setupMobileMenu();
}

function setupMobileMenu() {
  const btn = document.getElementById('menu-toggle');
  const menu = document.getElementById('mobile-menu');
  if (!btn || !menu) return;
  btn.addEventListener('click', () => {
    const open = menu.classList.toggle('hidden') === false;
    btn.setAttribute('aria-expanded', String(open));
    btn.querySelector('.menu-icon-open').classList.toggle('hidden', open);
    btn.querySelector('.menu-icon-close').classList.toggle('hidden', !open);
  });
  // Close on any nav link click inside the mobile menu.
  menu.querySelectorAll('a').forEach(a => a.addEventListener('click', closeMobileMenu));
}

function closeMobileMenu() {
  const btn = document.getElementById('menu-toggle');
  const menu = document.getElementById('mobile-menu');
  if (!menu || menu.classList.contains('hidden')) return;
  menu.classList.add('hidden');
  if (btn) {
    btn.setAttribute('aria-expanded', 'false');
    btn.querySelector('.menu-icon-open').classList.remove('hidden');
    btn.querySelector('.menu-icon-close').classList.add('hidden');
  }
}

// ---------------- League page ----------------
async function renderLeague() {
  const lg = await loadLeague();
  $view().innerHTML = '';
  const root = el('div', { class: 'fade-in space-y-6' });

  root.appendChild(el('div', { class: 'grid grid-cols-2 md:grid-cols-4 gap-3' }, [
    kpi('Equipos', lg.totals.teams),
    kpi('Jugadores', lg.totals.players),
    kpi('Partidos jugados', `${lg.totals.completed} / ${lg.totals.games}`),
    kpi('Líder', lg.standings[0]?.team_name || '–', lg.standings[0] ? `${lg.standings[0].wins}-${lg.standings[0].losses}` : ''),
  ]));

  // Standings + League leaders side-by-side on large
  const grid = el('div', { class: 'grid lg:grid-cols-3 gap-5' });
  // Standings (2 cols)
  const standingsCard = el('div', { class: 'lg:col-span-2 card' }, [
    el('div', { class: 'section-title-sm' }, 'Clasificación'),
    renderStandingsTable(lg.standings),
  ]);
  grid.appendChild(standingsCard);

  // Sidebar: top scorers preview
  const sideCard = el('div', { class: 'card' }, [
    el('div', { class: 'flex items-center justify-between mb-2' }, [
      el('div', { class: 'section-title-sm', html: '🏀 Máximos anotadores' }),
      el('a', { class: 'text-xs linkish', href: `#/group/${currentGroup}/leaders` }, 'Ver todos →'),
    ]),
    renderLeaderList(lg.leaders.ppg.slice(0, 8)),
  ]);
  grid.appendChild(sideCard);
  root.appendChild(grid);

  // Recent results
  const recent = lg.games.filter(g => g.status === 'FINALIZADO').slice(-10).reverse();
  if (recent.length) {
    root.appendChild(el('div', { class: 'section-title' }, '🗓️ Últimos resultados'));
    const cards = el('div', { class: 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3' });
    recent.forEach(g => cards.appendChild(renderGameMiniCard(g, lg)));
    root.appendChild(cards);
  }

  // Teams grid
  root.appendChild(el('div', { class: 'section-title' }, '🏟️ Equipos'));
  const teamsGrid = el('div', { class: 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3' });
  for (const t of lg.teams) teamsGrid.appendChild(renderTeamCardSmall(t, lg.standings));
  root.appendChild(teamsGrid);

  $view().appendChild(root);
}

function renderStandingsTable(standings) {
  const wrap = el('div', { class: 'scrollx' });
  const table = el('table', { class: 'stat-table' });
  table.innerHTML = `
    <thead><tr>
      <th class="no-sort">#</th><th class="no-sort">Equipo</th>
      <th class="head-num">PJ</th><th class="head-num">G</th><th class="head-num">P</th>
      <th class="head-num">PF</th><th class="head-num">PC</th><th class="head-num">±</th>
      <th class="head-num">PF/p</th><th class="head-num">PC/p</th><th class="head-num">%</th>
    </tr></thead>`;
  const tb = el('tbody');
  standings.forEach(s => {
    const tr = el('tr');
    tr.appendChild(el('td', { class: 'font-semibold text-ink-500' }, String(s.rank)));
    const teamCell = el('td');
    teamCell.appendChild(renderTeamInline(s));
    tr.appendChild(teamCell);
    tr.appendChild(el('td', { class: 'num' }, s.games_played));
    tr.appendChild(el('td', { class: 'num text-emerald-700 font-semibold' }, s.wins));
    tr.appendChild(el('td', { class: 'num text-rose-700' }, s.losses));
    tr.appendChild(el('td', { class: 'num' }, s.points_for));
    tr.appendChild(el('td', { class: 'num' }, s.points_against));
    tr.appendChild(el('td', { class: 'num font-semibold ' + (s.point_diff >= 0 ? 'text-emerald-700' : 'text-rose-700') },
      (s.point_diff >= 0 ? '+' : '') + s.point_diff));
    tr.appendChild(el('td', { class: 'num' }, fmt(s.avg_for)));
    tr.appendChild(el('td', { class: 'num' }, fmt(s.avg_against)));
    tr.appendChild(el('td', { class: 'num font-medium' }, pct(s.win_pct)));
    tr.addEventListener('click', () => location.hash = `#/group/${currentGroup}/team/${s.team_id}`);
    tr.style.cursor = 'pointer';
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  wrap.appendChild(table);
  return wrap;
}

function renderTeamInline(item) {
  return el('div', { class: 'flex items-center gap-2 linkish', onclick: () => location.hash = `#/group/${currentGroup}/team/${item.team_id}` }, [
    item.logo_url
      ? el('img', { class: 'team-logo', src: item.logo_url, width: 24, height: 24, loading: 'lazy', referrerpolicy: 'no-referrer' })
      : el('div', { class: 'w-6 h-6 rounded-full bg-ink-200' }),
    el('span', { class: 'font-medium' }, item.team_name),
  ]);
}

function renderLeaderList(rows) {
  const ul = el('ul', { class: 'divide-y divide-ink-100' });
  rows.forEach((r, i) => {
    const li = el('li', { class: 'py-2 flex items-center gap-3' }, [
      el('span', { class: 'w-5 text-xs text-ink-500 font-mono' }, String(i + 1).padStart(2, '0')),
      r.logo_url
        ? el('img', { class: 'team-logo', src: r.logo_url, width: 22, height: 22, loading: 'lazy', referrerpolicy: 'no-referrer' })
        : el('div', { class: 'w-5 h-5 rounded-full bg-ink-200' }),
      el('div', { class: 'flex-1 min-w-0' }, [
        el('div', { class: 'text-sm font-medium truncate linkish', onclick: () => location.hash = `#/group/${currentGroup}/player/${r.player_id}` }, r.player_name),
        el('div', { class: 'text-xs text-ink-500 truncate' }, r.team_name),
      ]),
      el('div', { class: 'text-right' }, [
        el('div', { class: 'font-semibold tabular-nums' }, fmt(r.primary.value)),
        el('div', { class: 'text-xs text-ink-500' }, r.primary.label),
      ]),
    ]);
    ul.appendChild(li);
  });
  return ul;
}

function renderGameMiniCard(g, lg) {
  const home = lg.teams.find(t => t.id === g.home_team_id) || {};
  const away = lg.teams.find(t => t.id === g.away_team_id) || {};
  const homeWin = g.home_score > g.away_score;
  const card = el('div', { class: 'card-tight transition' + (g.id ? ' cursor-pointer hover:shadow-md' : ' card-no-acta'), onclick: g.id ? () => location.hash = `#/group/${currentGroup}/game/${g.id}` : null }, [
    el('div', { class: 'text-xs text-ink-500 mb-2 flex items-center justify-between' }, [
      el('span', null, `J${g.jornada} · ${ddmmyyyy(g.date)}`),
      g.status === 'FINALIZADO'
        ? el('span', { class: 'badge badge-d' }, 'Final')
        : el('span', { class: 'badge badge-pending' }, g.status),
    ]),
    el('div', { class: 'flex items-center gap-2 mb-1' }, [
      home.logo_url ? el('img', { class: 'team-logo', src: home.logo_url, width: 22, height: 22, referrerpolicy: 'no-referrer' }) : el('div', { class: 'w-5 h-5 rounded-full bg-ink-200' }),
      el('span', { class: 'flex-1 truncate text-sm ' + (homeWin ? 'font-semibold' : '') }, home.name || g.home_team_id),
      el('span', { class: 'tabular-nums font-bold ' + (homeWin ? 'text-ink-900' : 'text-ink-500') }, g.home_score ?? ''),
    ]),
    el('div', { class: 'flex items-center gap-2' }, [
      away.logo_url ? el('img', { class: 'team-logo', src: away.logo_url, width: 22, height: 22, referrerpolicy: 'no-referrer' }) : el('div', { class: 'w-5 h-5 rounded-full bg-ink-200' }),
      el('span', { class: 'flex-1 truncate text-sm ' + (!homeWin ? 'font-semibold' : '') }, away.name || g.away_team_id),
      el('span', { class: 'tabular-nums font-bold ' + (!homeWin ? 'text-ink-900' : 'text-ink-500') }, g.away_score ?? ''),
    ]),
  ]);
  return card;
}

function renderTeamCardSmall(t, standings) {
  const st = standings.find(s => s.team_id === t.id);
  return el('a', { href: `#/group/${currentGroup}/team/${t.id}`, class: 'card-tight flex flex-col items-center text-center gap-2 hover:shadow-md transition group' }, [
    t.logo_url
      ? el('img', { class: 'team-logo', src: t.logo_url, width: 52, height: 52, loading: 'lazy', referrerpolicy: 'no-referrer' })
      : el('div', { class: 'w-[52px] h-[52px] rounded-full bg-ink-200' }),
    el('div', { class: 'text-sm font-medium leading-tight line-clamp-2 group-hover:text-brand-600 transition' }, t.name),
    st ? el('div', { class: 'text-xs text-ink-500' }, `#${standings.indexOf(st) + 1} · ${st.wins}-${st.losses}`) : null,
  ]);
}

// ---------------- Teams index ----------------
async function renderTeams() {
  const lg = await loadLeague();
  $view().innerHTML = '';
  const root = el('div', { class: 'fade-in space-y-4' });
  root.appendChild(el('div', { class: 'section-title' }, 'Equipos'));
  const grid = el('div', { class: 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4' });
  for (const t of lg.teams) {
    const st = lg.standings.find(s => s.team_id === t.id);
    grid.appendChild(el('a', { href: `#/group/${currentGroup}/team/${t.id}`, class: 'card flex items-center gap-4 hover:shadow-md transition' }, [
      t.logo_url
        ? el('img', { class: 'team-logo', src: t.logo_url, width: 64, height: 64, loading: 'lazy', referrerpolicy: 'no-referrer' })
        : el('div', { class: 'w-16 h-16 rounded-full bg-ink-200' }),
      el('div', { class: 'flex-1' }, [
        el('div', { class: 'font-semibold text-lg' }, t.name),
        st ? el('div', { class: 'text-sm text-ink-500 mt-1' }, [
          el('span', { class: 'badge badge-w mr-1' }, `${st.wins}V`),
          el('span', { class: 'badge badge-l mr-2' }, `${st.losses}D`),
          el('span', null, `· ${fmt(st.avg_for)} – ${fmt(st.avg_against)} pts/p`),
        ]) : null,
      ]),
      el('div', { class: 'text-right' }, [
        el('div', { class: 'text-3xl font-bold text-ink-300' }, st ? `#${lg.standings.indexOf(st) + 1}` : ''),
      ]),
    ]));
  }
  root.appendChild(grid);
  $view().appendChild(root);
}

// ---------------- League leaders page ----------------
async function renderLeaders() {
  const lg = await loadLeague();
  $view().innerHTML = '';
  const root = el('div', { class: 'fade-in space-y-6' });
  root.appendChild(el('div', { class: 'section-title' }, '🏆 Líderes de la liga'));
  const sections = [
    ['Máximos anotadores (PPG)', lg.leaders.ppg],
    ['Triples por partido', lg.leaders.tpg_3],
    ['Canastas de 2 por partido', lg.leaders.tpg_2],
    [`Tiro libre % (mín ${20} intentos)`, lg.leaders.ft_pct],
    ['Anotadores totales', lg.leaders.pts_total],
    ['Menos faltas / partido', lg.leaders.low_fp],
    ['Faltas personales / partido', lg.leaders.fp_personal_pg],
    ['Faltas técnicas (total)', lg.leaders.fp_technical],
    ['Faltas antideportivas (total)', lg.leaders.fp_anti],
  ];
  const grid = el('div', { class: 'grid md:grid-cols-2 lg:grid-cols-3 gap-5' });
  for (const [title, rows] of sections) {
    grid.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'section-title-sm' }, title),
      renderLeaderList(rows.slice(0, 10)),
    ]));
  }
  root.appendChild(grid);
  $view().appendChild(root);
}

// ---------------- Schedule page ----------------
async function renderSchedule() {
  const lg = await loadLeague();
  $view().innerHTML = '';
  const root = el('div', { class: 'fade-in space-y-4' });
  root.appendChild(el('div', { class: 'section-title' }, '🗓️ Calendario'));

  const byJornada = new Map();
  for (const g of lg.games) {
    if (!byJornada.has(g.jornada)) byJornada.set(g.jornada, []);
    byJornada.get(g.jornada).push(g);
  }
  const jornadas = [...byJornada.keys()].sort((a, b) => a - b);

  for (const j of jornadas) {
    const games = byJornada.get(j);
    const section = el('div', { class: 'card' }, [
      el('div', { class: 'flex items-center justify-between mb-2' }, [
        el('div', { class: 'font-semibold' }, `Jornada ${j}`),
        el('div', { class: 'text-xs text-ink-500' }, ddmmyyyy(games[0]?.date) || ''),
      ]),
      el('div', { class: 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3' },
        games.map(g => renderGameMiniCard(g, lg))),
    ]);
    root.appendChild(section);
  }
  $view().appendChild(root);
}

// ---------------- Team page (golden) ----------------
async function renderTeam(teamId) {
  const [lg, view] = await Promise.all([loadLeague(), loadJSON(groupPath(`teams/${teamId}.json`))]);
  if (!view) return setError('Equipo no encontrado');
  $view().innerHTML = '';
  const t = view.team, s = view.season || {};
  const root = el('div', { class: 'fade-in space-y-6' });

  // ---- Header ----
  root.appendChild(el('div', { class: 'card flex flex-col sm:flex-row items-center gap-5' }, [
    t.logo_url
      ? el('img', { class: 'team-logo', src: t.logo_url, width: 96, height: 96, referrerpolicy: 'no-referrer' })
      : el('div', { class: 'w-24 h-24 rounded-full bg-ink-200' }),
    el('div', { class: 'flex-1 text-center sm:text-left' }, [
      el('div', { class: 'text-xs uppercase tracking-wider text-ink-500' }, view.rank ? `Clasificación #${view.rank}` : ''),
      el('h1', { class: 'text-3xl font-extrabold tracking-tight' }, t.name),
      el('div', { class: 'mt-2 flex flex-wrap justify-center sm:justify-start gap-2' }, [
        el('span', { class: 'badge badge-w' }, `${s.wins ?? 0} victorias`),
        el('span', { class: 'badge badge-l' }, `${s.losses ?? 0} derrotas`),
        s.draws ? el('span', { class: 'badge badge-d' }, `${s.draws} empates`) : null,
        el('span', { class: 'chip' }, `${s.games_played ?? 0} partidos`),
        s.win_pct != null ? el('span', { class: 'chip chip-brand' }, `${(s.win_pct * 100).toFixed(0)}% victorias`) : null,
      ]),
    ]),
  ]));

  // ---- KPI row ----
  root.appendChild(el('div', { class: 'grid grid-cols-2 md:grid-cols-4 gap-3' }, [
    kpi('PF / partido', fmt(s.avg_points_for), `${s.points_for ?? 0} PTS totales`),
    kpi('PC / partido', fmt(s.avg_points_against), `${s.points_against ?? 0} PTS encajados`),
    kpi('Diferencial', (s.point_diff >= 0 ? '+' : '') + (s.point_diff ?? 0),
      `Media: ${(s.point_diff != null && s.games_played) ? (((s.point_diff / s.games_played) >= 0 ? '+' : '') + (s.point_diff / s.games_played).toFixed(1)) : '–'}`),
    kpi('Ranking', view.rank ? `#${view.rank}` : '–', `de ${lg.standings.length} equipos`),
  ]));

  // ---- Charts row ----
  const chartGrid = el('div', { class: 'grid lg:grid-cols-2 gap-5' });
  chartGrid.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'section-title-sm' }, 'Promedio por cuarto'),
    el('div', { class: 'chart-wrap' }, el('canvas', { id: 'chart-quarter' })),
  ]));
  chartGrid.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'section-title-sm' }, 'Resultados por jornada'),
    el('div', { class: 'chart-wrap' }, el('canvas', { id: 'chart-timeline' })),
  ]));
  root.appendChild(chartGrid);

  // ---- Highlights ----
  root.appendChild(el('div', { class: 'section-title' }, '⭐ Destacados'));
  root.appendChild(renderHighlights(view.highlights));

  // ---- Roster ----
  root.appendChild(el('div', { class: 'section-title' }, '👥 Plantilla'));
  root.appendChild(renderRosterTable(view.roster));

  // ---- Recent games ----
  root.appendChild(el('div', { class: 'section-title' }, '📅 Partidos'));
  root.appendChild(renderTeamGamesTable(view.games));

  $view().appendChild(root);

  // ---- Charts ----
  drawQuarterChart('chart-quarter', s.per_quarter);
  drawTimelineChart('chart-timeline', view.games);
}

function renderHighlights(h) {
  const items = [
    ['🥇 Máximo anotador', h.top_scorer],
    ['🎯 Mejor triplista', h.top_3pt],
    ['🏀 Mejor anotador de 2', h.top_2pt],
    ['🎯 Mejor en tiro libre', h.top_ft],
    ['💎 Mejor partido', h.best_single_game],
    ['🛡️ Menos faltas', h.most_disciplined],
  ].filter(([, v]) => v);
  return el('div', { class: 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3' },
    items.map(([title, v]) =>
      el('a', { href: `#/group/${currentGroup}/player/${v.player_id}`, class: 'card-tight hover:shadow-md transition flex items-center justify-between gap-3' }, [
        el('div', { class: 'min-w-0' }, [
          el('div', { class: 'text-xs uppercase tracking-wider text-ink-500' }, title),
          el('div', { class: 'font-semibold truncate' }, v.player_name),
          el('div', { class: 'text-xs text-ink-500' }, `${v.games_played} partidos`),
        ]),
        el('div', { class: 'text-right' }, [
          el('div', { class: 'text-2xl font-extrabold leading-none text-brand-600' }, fmt(v.value)),
          el('div', { class: 'text-xs text-ink-500 mt-1' }, v.label),
        ]),
      ])
    )
  );
}

function renderRosterTable(roster) {
  const cols = [
    { k: 'name', label: 'Jugador', sortable: true, sortKey: r => (r.name || '').toLowerCase(), cell: r => playerCell(r), align: 'left' },
    { k: 'dorsal', label: '#', sortable: true, sortKey: r => parseInt(r.dorsals?.[0] || '999'), cell: r => (r.dorsals || []).join(', '), align: 'right' },
    { k: 'gp', label: 'PJ', sortable: true, sortKey: r => r.games_played, cell: r => r.games_played, align: 'right' },
    { k: 'ppg', label: 'PTS/p', sortable: true, sortKey: r => r.averages?.pts || 0, cell: r => fmt(r.averages?.pts), align: 'right', bold: true },
    { k: 'pts', label: 'PTS', sortable: true, sortKey: r => r.totals?.pts || 0, cell: r => r.totals?.pts || 0, align: 'right' },
    { k: 't2', label: '2/p', sortable: true, sortKey: r => r.averages?.t2 || 0, cell: r => fmt(r.averages?.t2), align: 'right' },
    { k: 't3', label: '3/p', sortable: true, sortKey: r => r.averages?.t3 || 0, cell: r => fmt(r.averages?.t3), align: 'right' },
    { k: 'tl', label: 'TL/p', sortable: true, sortKey: r => r.averages?.tl_made || 0, cell: r => fmt(r.averages?.tl_made), align: 'right' },
    { k: 'ftpct', label: 'TL%', sortable: true, sortKey: r => r.ft_pct || 0, cell: r => r.ft_pct != null ? (r.ft_pct * 100).toFixed(1) + '%' : '–', align: 'right' },
    { k: 'fp', label: 'F/p', sortable: true, sortKey: r => r.averages?.fp || 0, cell: r => fmt(r.averages?.fp), align: 'right' },
    { k: 'max', label: 'Tope', sortable: true, sortKey: r => r.highs?.pts || 0, cell: r => r.highs?.pts || '–', align: 'right' },
  ];
  return sortableTable(roster, cols, { initialSort: 'ppg', initialDir: 'desc' });
}

function playerCell(r) {
  return el('span', { class: 'linkish font-medium', onclick: () => location.hash = `#/group/${currentGroup}/player/${r.id}` }, r.name);
}

function renderTeamGamesTable(games) {
  const cols = [
    { k: 'jor', label: 'J', cell: g => g.jornada, sortKey: g => g.jornada, align: 'right' },
    { k: 'date', label: 'Fecha', cell: g => g.date ? ddmmyyyy(g.date) : '–', sortKey: g => g.date || '' },
    { k: 'home', label: 'Local', cell: g => g.is_home ? 'Sí' : 'No', sortKey: g => g.is_home ? 1 : 0 },
    { k: 'opp', label: 'Rival', cell: g => opponentCell(g), sortKey: g => g.opponent_name || '' },
    { k: 'result', label: 'Resultado', cell: g => resultCell(g), sortKey: g => (g.for_pts - g.against_pts) || 0, align: 'right' },
    { k: 'res', label: '', cell: g => g.result ? el('span', { class: 'badge ' + (g.result === 'W' ? 'badge-w' : g.result === 'L' ? 'badge-l' : 'badge-d') }, g.result) : el('span', { class: 'badge badge-pending' }, g.status), sortKey: g => g.result || '' },
  ];
  return sortableTable(games, cols, { initialSort: 'jor', initialDir: 'asc',
    onRowClick: g => g.id && (location.hash = `#/group/${currentGroup}/game/${g.id}`),
  });
}
function opponentCell(g) {
  if (!g.opponent_id) return '–';
  return el('span', { class: 'flex items-center gap-2 linkish', onclick: (e) => { e.stopPropagation(); location.hash = `#/group/${currentGroup}/team/${g.opponent_id}`; } }, [
    g.opponent_logo ? el('img', { class: 'team-logo', src: g.opponent_logo, width: 22, height: 22, referrerpolicy: 'no-referrer' }) : el('div', { class: 'w-5 h-5 rounded-full bg-ink-200' }),
    el('span', null, g.opponent_name || g.opponent_id),
  ]);
}
function resultCell(g) {
  if (g.for_pts == null || g.against_pts == null) return '–';
  return el('span', { class: 'tabular-nums font-medium' }, `${g.for_pts} – ${g.against_pts}`);
}

// ---------------- Player page ----------------
async function renderPlayer(playerId) {
  const view = await loadJSON(groupPath(`players/${playerId}.json`));
  $view().innerHTML = '';
  const p = view.player, s = view.season;
  const root = el('div', { class: 'fade-in space-y-6' });

  root.appendChild(el('div', { class: 'card flex flex-col sm:flex-row items-center gap-5' }, [
    el('div', { class: 'w-20 h-20 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white grid place-items-center text-2xl font-extrabold' },
      (p.name?.[0] || '?') + (p.name?.split(' ').pop()?.[0] || '')),
    el('div', { class: 'flex-1 text-center sm:text-left' }, [
      el('div', { class: 'text-xs uppercase tracking-wider text-ink-500' }, '#' + (p.dorsals?.join(' / ') || '–')),
      el('h1', { class: 'text-3xl font-extrabold tracking-tight' }, p.name),
      el('a', { href: `#/group/${currentGroup}/team/${p.team_id}`, class: 'inline-flex items-center gap-2 mt-2 text-ink-700 hover:text-brand-600' }, [
        p.team_logo ? el('img', { class: 'team-logo', src: p.team_logo, width: 22, height: 22, referrerpolicy: 'no-referrer' }) : null,
        el('span', { class: 'font-medium' }, p.team_name || p.team_id),
      ]),
    ]),
  ]));

  if (!s) {
    root.appendChild(el('div', { class: 'card text-center text-ink-500' }, 'Sin partidos jugados.'));
    $view().appendChild(root);
    return;
  }

  root.appendChild(el('div', { class: 'grid grid-cols-2 md:grid-cols-4 gap-3' }, [
    kpi('Partidos', s.games_played),
    kpi('PTS / partido', fmt(s.averages.pts), `${s.totals.pts} totales`),
    kpi('Tope PTS', s.highs?.pts ?? '–'),
    kpi('TL%', s.ft_pct != null ? (s.ft_pct * 100).toFixed(1) + '%' : '–', `${s.totals.tl_made}/${s.totals.tl_att}`),
  ]));

  const chartGrid = el('div', { class: 'grid lg:grid-cols-2 gap-5' });
  chartGrid.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'section-title-sm' }, 'Puntos por partido'),
    el('div', { class: 'chart-wrap' }, el('canvas', { id: 'chart-player-games' })),
  ]));
  chartGrid.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'section-title-sm' }, 'Promedio por cuarto'),
    el('div', { class: 'chart-wrap' }, el('canvas', { id: 'chart-player-quarter' })),
  ]));
  root.appendChild(chartGrid);

  // Shooting distribution chart (doughnut)
  const distGrid = el('div', { class: 'grid lg:grid-cols-2 gap-5' });
  distGrid.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'section-title-sm' }, 'Distribución de anotación'),
    el('div', { class: 'chart-wrap' }, el('canvas', { id: 'chart-player-dist' })),
  ]));
  distGrid.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'section-title-sm' }, 'Totales temporada'),
    renderPlayerTotalsTable(s),
  ]));
  root.appendChild(distGrid);

  // Game log
  root.appendChild(el('div', { class: 'section-title' }, 'Partido a partido'));
  root.appendChild(renderPlayerGameLog(view.games));

  $view().appendChild(root);

  drawPlayerGamesChart('chart-player-games', view.games);
  drawQuarterAvgChart('chart-player-quarter', s.per_quarter_averages);
  drawPlayerShotDist('chart-player-dist', s.totals);
}

function renderPlayerTotalsTable(s) {
  const rows = [
    ['Puntos', s.totals.pts, s.averages.pts],
    ['Canastas 2', s.totals.t2, s.averages.t2],
    ['Triples', s.totals.t3, s.averages.t3],
    ['Tiros libres metidos', s.totals.tl_made, s.averages.tl_made],
    ['Tiros libres intentados', s.totals.tl_att, s.averages.tl_att],
    ['Faltas personales', s.totals.fp, s.averages.fp],
  ];
  const tb = el('tbody');
  rows.forEach(([label, tot, avg]) => {
    tb.appendChild(el('tr', null, [
      el('td', null, label),
      el('td', { class: 'num font-semibold' }, tot),
      el('td', { class: 'num text-ink-500' }, fmt(avg)),
    ]));
  });
  const t = el('table', { class: 'stat-table' });
  t.innerHTML = `<thead><tr><th class="no-sort">Estadística</th><th class="no-sort head-num">Total</th><th class="no-sort head-num">/ partido</th></tr></thead>`;
  t.appendChild(tb);
  return t;
}

function renderPlayerGameLog(rows) {
  const cols = [
    { k: 'jor', label: 'J', cell: g => g.jornada, sortKey: g => g.jornada || 0, align: 'right' },
    { k: 'date', label: 'Fecha', cell: g => g.date ? ddmmyyyy(g.date) : '–', sortKey: g => g.date || '' },
    { k: 'opp', label: 'Rival', cell: g => g.opponent_id ? el('span', { class: 'linkish', onclick: e => { e.stopPropagation(); location.hash = `#/group/${currentGroup}/team/${g.opponent_id}`; } }, (g.is_home ? '' : '@ ') + (g.opponent_name || g.opponent_id)) : '–', sortKey: g => g.opponent_name || '' },
    { k: 'team', label: 'Equipo', cell: g => g.team_for != null ? `${g.team_for} – ${g.team_against}` : '–', sortKey: g => (g.team_for - g.team_against) || 0, align: 'right' },
    { k: 'pts', label: 'PTS', cell: g => g.played ? g.pts : '–', sortKey: g => g.played ? g.pts : -1, align: 'right', bold: true },
    { k: 't2', label: '2', cell: g => g.played ? g.t2 : '–', sortKey: g => g.played ? g.t2 : -1, align: 'right' },
    { k: 't3', label: '3', cell: g => g.played ? g.t3 : '–', sortKey: g => g.played ? g.t3 : -1, align: 'right' },
    { k: 'tl', label: 'TL', cell: g => g.played ? `${g.tl_made}/${g.tl_att}` : '–', sortKey: g => g.played ? g.tl_made : -1, align: 'right' },
    { k: 'fp', label: 'F', cell: g => g.played ? g.fp : '–', sortKey: g => g.played ? g.fp : -1, align: 'right' },
    { k: 'q1', label: 'C1', cell: g => g.played ? (g.by_quarter?.P1?.pts ?? 0) : '–', sortKey: g => g.by_quarter?.P1?.pts || 0, align: 'right' },
    { k: 'q2', label: 'C2', cell: g => g.played ? (g.by_quarter?.P2?.pts ?? 0) : '–', sortKey: g => g.by_quarter?.P2?.pts || 0, align: 'right' },
    { k: 'q3', label: 'C3', cell: g => g.played ? (g.by_quarter?.P3?.pts ?? 0) : '–', sortKey: g => g.by_quarter?.P3?.pts || 0, align: 'right' },
    { k: 'q4', label: 'C4', cell: g => g.played ? (g.by_quarter?.P4?.pts ?? 0) : '–', sortKey: g => g.by_quarter?.P4?.pts || 0, align: 'right' },
  ];
  return sortableTable(rows, cols, { initialSort: 'jor', initialDir: 'asc',
    onRowClick: g => g.game_id && (location.hash = `#/group/${currentGroup}/game/${g.game_id}`),
    rowClass: g => g.played ? '' : 'opacity-50',
  });
}

// ---------------- Game page ----------------
async function renderGame(gameId) {
  const view = await loadJSON(groupPath(`games/${gameId}.json`));
  $view().innerHTML = '';
  const g = view.game;
  const root = el('div', { class: 'fade-in space-y-6' });

  const homeWin = g.winner === 'home';
  const awayWin = g.winner === 'away';
  root.appendChild(el('div', { class: 'card' }, [
    el('div', { class: 'flex items-center justify-between text-xs text-ink-500 mb-3' }, [
      el('span', null, `Jornada ${g.jornada} · ${ddmmyyyy(g.date)}` + (g.venue ? ' · ' + g.venue : '')),
      g.status === 'FINALIZADO' ? el('span', { class: 'badge badge-d' }, 'Final') : el('span', { class: 'badge badge-pending' }, g.status),
    ]),
    el('div', { class: 'grid grid-cols-3 items-center gap-4' }, [
      teamScoreBlock(view.home, g.home_score, homeWin),
      el('div', { class: 'text-center text-3xl text-ink-300' }, '–'),
      teamScoreBlock(view.away, g.away_score, awayWin),
    ]),
    g.quarters?.length ? el('div', { class: 'mt-4 grid grid-cols-' + (g.quarters.length + 1) + ' gap-2 text-center text-sm' }, [
      el('div', { class: 'text-ink-500' }, ''),
      ...g.quarters.map((q, i) => el('div', { class: 'text-ink-500 text-xs uppercase' }, 'C' + (i + 1))),
      el('div', { class: 'font-semibold' }, view.home.team_name),
      ...g.quarters.map(q => el('div', { class: 'tabular-nums' }, q[0])),
      el('div', { class: 'font-semibold' }, view.away.team_name),
      ...g.quarters.map(q => el('div', { class: 'tabular-nums' }, q[1])),
    ]) : null,
  ]));

  // Box scores
  root.appendChild(el('div', { class: 'section-title' }, 'Box score'));
  root.appendChild(el('div', { class: 'grid lg:grid-cols-2 gap-5' }, [
    renderBoxScore(view.home),
    renderBoxScore(view.away),
  ]));

  // Play-by-play
  if (view.log && view.log.length) {
    root.appendChild(el('div', { class: 'section-title' }, 'Jugada a jugada'));
    root.appendChild(renderPlayByPlay(view));
  }

  $view().appendChild(root);
}

function teamScoreBlock(team, score, isWinner) {
  return el('div', { class: 'flex flex-col items-center gap-2 text-center' }, [
    team.logo_url ? el('img', { class: 'team-logo', src: team.logo_url, width: 72, height: 72, referrerpolicy: 'no-referrer' }) : el('div', { class: 'w-[72px] h-[72px] rounded-full bg-ink-200' }),
    el('a', { href: `#/group/${currentGroup}/team/${team.team_id}`, class: 'font-semibold linkish' }, team.team_name),
    el('div', { class: 'text-4xl font-extrabold tabular-nums ' + (isWinner ? 'text-brand-600' : 'text-ink-700') }, score ?? '–'),
  ]);
}

function renderBoxScore(side) {
  const cols = [
    { k: 'd', label: '#', cell: r => r.dorsal || '–', sortKey: r => parseInt(r.dorsal || '999'), align: 'right' },
    { k: 'name', label: 'Jugador', cell: r => el('span', { class: 'linkish font-medium', onclick: () => location.hash = `#/group/${currentGroup}/player/${r.player_id}` }, r.name), sortKey: r => r.name },
    { k: 'pts', label: 'PTS', cell: r => r.pts, sortKey: r => r.pts, align: 'right', bold: true },
    { k: 't2', label: '2', cell: r => r.t2, sortKey: r => r.t2, align: 'right' },
    { k: 't3', label: '3', cell: r => r.t3, sortKey: r => r.t3, align: 'right' },
    { k: 'tl', label: 'TL', cell: r => `${r.tl_made}/${r.tl_att}`, sortKey: r => r.tl_made, align: 'right' },
    { k: 'fp', label: 'F', cell: r => r.fp, sortKey: r => r.fp, align: 'right' },
  ];
  return el('div', { class: 'card' }, [
    el('div', { class: 'flex items-center gap-2 mb-3' }, [
      side.logo_url ? el('img', { class: 'team-logo', src: side.logo_url, width: 32, height: 32, referrerpolicy: 'no-referrer' }) : null,
      el('div', { class: 'font-semibold' }, side.team_name),
    ]),
    sortableTable(side.players, cols, { initialSort: 'pts', initialDir: 'desc',
      rowClass: r => r.played ? '' : 'opacity-40',
    }),
  ]);
}

function renderPlayByPlay(view) {
  const periods = [...new Set(view.log.map(e => e.period))];
  const kinds = [
    { v: 'all', l: 'Todo' },
    { v: 'made_2', l: '2 puntos' },
    { v: 'made_3', l: '3 puntos' },
    { v: 'ft_made', l: 'TL ✓' },
    { v: 'ft_missed', l: 'TL ✗' },
    { v: 'foul', l: 'Faltas' },
    { v: 'timeout', l: 'Tiempos' },
  ];

  const filterBar = el('div', { class: 'flex flex-wrap items-center gap-2 mb-3' }, [
    el('div', { class: 'flex flex-wrap gap-1' }, periods.map(p =>
      el('button', { class: 'chip', dataset: { period: p }, onclick: e => { filterPP(); document.querySelectorAll('[data-period]').forEach(b => b.classList.remove('chip-brand')); e.currentTarget.classList.add('chip-brand'); } }, p))),
    el('div', { class: 'mx-2 text-ink-300' }, '·'),
    el('select', { id: 'pp-kind', class: 'border border-ink-200 rounded-md text-sm px-2 py-1.5', onchange: filterPP },
      kinds.map(k => el('option', { value: k.v }, k.l))),
    el('button', { class: 'chip ml-auto', onclick: () => { document.querySelectorAll('[data-period]').forEach(b => b.classList.remove('chip-brand')); document.getElementById('pp-kind').value = 'all'; filterPP(); } }, 'Limpiar'),
  ]);

  const list = el('div', { class: 'space-y-1 max-h-[600px] overflow-auto pr-1' });
  view.log.forEach(e => list.appendChild(renderPPEntry(e)));

  function filterPP() {
    const activePeriodBtn = document.querySelector('[data-period].chip-brand');
    const period = activePeriodBtn?.dataset.period;
    const kind = document.getElementById('pp-kind').value;
    list.querySelectorAll('[data-pp]').forEach(node => {
      const entry = JSON.parse(node.dataset.pp);
      const periodOk = !period || entry.period === period;
      let kindOk = kind === 'all';
      if (kind === 'foul') kindOk = (entry.event_kind || '').startsWith('foul_');
      else if (kind !== 'all') kindOk = entry.event_kind === kind;
      node.style.display = (periodOk && kindOk) ? '' : 'none';
    });
  }

  return el('div', { class: 'card' }, [filterBar, list]);
}

function renderPPEntry(e) {
  const cls = 'pp-entry ' + (e.side === 'home' ? 'home' : e.side === 'away' ? 'away' : 'neutral');
  const kindIcon = ({ made_2: '🟠', made_3: '🎯', ft_made: '✓', ft_missed: '✗', timeout: '⏸', period_end: '⏹',
    foul_personal: '⚠️', foul_technical: '⚠️', foul_unsportsmanlike: '⚠️', foul_disqualifying: '⛔' })[e.event_kind] || '•';
  const text = (e.player_name ? `#${e.player_dorsal || ''} ${e.player_name} — ` : '') + (e.event || '');
  const score = (e.score_home != null && e.score_away != null) ? `${e.score_home}-${e.score_away}` : '';
  const node = el('div', { class: cls, dataset: { pp: JSON.stringify({ period: e.period, event_kind: e.event_kind }) } }, [
    el('span', { class: 'pp-time' }, `${e.period} · ${e.clock || ''}`),
    el('span', { class: 'pp-text flex-1' }, `${kindIcon} ${text}`),
    el('span', { class: 'pp-score' }, score),
  ]);
  return node;
}

// ---------------- Generic sortable table ----------------
function sortableTable(rows, cols, opts = {}) {
  let dir = opts.initialDir || 'desc';
  let sortKey = opts.initialSort || cols[0].k;
  const wrap = el('div', { class: 'scrollx' });
  const table = el('table', { class: 'stat-table' });
  const thead = el('thead');
  const headRow = el('tr');
  cols.forEach(c => {
    const th = el('th', { class: c.sortable === false ? 'no-sort' : '' }, c.label);
    if (c.align === 'right') th.classList.add('head-num');
    if (c.sortable !== false) th.addEventListener('click', () => {
      if (sortKey === c.k) dir = dir === 'asc' ? 'desc' : 'asc';
      else { sortKey = c.k; dir = c.align === 'right' ? 'desc' : 'asc'; }
      render();
    });
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tb = el('tbody');
  table.appendChild(tb);

  function render() {
    tb.innerHTML = '';
    headRow.querySelectorAll('th').forEach((th, i) => {
      th.classList.remove('sorted-asc', 'sorted-desc');
      if (cols[i].k === sortKey) th.classList.add(dir === 'asc' ? 'sorted-asc' : 'sorted-desc');
    });
    const col = cols.find(c => c.k === sortKey);
    const sorted = [...rows];
    if (col && col.sortKey) {
      sorted.sort((a, b) => {
        const va = col.sortKey(a), vb = col.sortKey(b);
        if (va < vb) return dir === 'asc' ? -1 : 1;
        if (va > vb) return dir === 'asc' ? 1 : -1;
        return 0;
      });
    }
    for (const r of sorted) {
      const tr = el('tr');
      if (opts.rowClass) {
        const c = opts.rowClass(r);
        if (c) tr.className = c;
      }
      if (opts.onRowClick) { tr.style.cursor = 'pointer'; tr.addEventListener('click', () => opts.onRowClick(r)); }
      cols.forEach(c => {
        const td = el('td');
        if (c.align === 'right') td.classList.add('num');
        if (c.bold) td.classList.add('font-semibold');
        const v = c.cell(r);
        if (v && typeof v === 'object' && 'nodeType' in v) td.appendChild(v);
        else td.textContent = v == null ? '' : String(v);
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    }
  }
  render();
  wrap.appendChild(table);
  return wrap;
}

function kpi(label, value, hint) {
  return el('div', { class: 'kpi' }, [
    el('div', { class: 'kpi-label' }, label),
    el('div', { class: 'kpi-value' }, value == null ? '–' : String(value)),
    hint ? el('div', { class: 'kpi-hint' }, hint) : null,
  ]);
}

// ---------------- Charts ----------------
function drawQuarterChart(canvasId, perQuarter) {
  if (!perQuarter || Object.keys(perQuarter).length === 0) return;
  const periods = Object.keys(perQuarter).sort();
  const forVals = periods.map(p => perQuarter[p].avg_for);
  const againstVals = periods.map(p => perQuarter[p].avg_against);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts.push(new Chart(ctx, {
    type: 'bar',
    data: {
      labels: periods,
      datasets: [
        { label: 'A favor', data: forVals, backgroundColor: BRAND, borderRadius: 6 },
        { label: 'En contra', data: againstVals, backgroundColor: '#94a3b8', borderRadius: 6 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } },
      scales: { y: { beginAtZero: true, grid: { color: INK_200 } }, x: { grid: { display: false } } },
    },
  }));
}

function drawTimelineChart(canvasId, games) {
  const finals = games.filter(g => g.for_pts != null && g.against_pts != null);
  if (!finals.length) return;
  const labels = finals.map(g => `J${g.jornada}`);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts.push(new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'A favor', data: finals.map(g => g.for_pts), borderColor: BRAND, backgroundColor: BRAND_LIGHT, tension: 0.35, fill: true, pointRadius: 3 },
        { label: 'En contra', data: finals.map(g => g.against_pts), borderColor: '#64748b', backgroundColor: 'transparent', tension: 0.35, borderDash: [4, 4], pointRadius: 2 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' }, tooltip: {
        callbacks: { afterLabel: (ctx) => {
          const g = finals[ctx.dataIndex];
          return g.is_home ? `vs ${g.opponent_name}` : `@ ${g.opponent_name}`;
        }}
      }},
      scales: { y: { beginAtZero: true, grid: { color: INK_200 } }, x: { grid: { display: false } } },
    },
  }));
}

function drawPlayerGamesChart(canvasId, games) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const labels = games.map(g => `J${g.jornada}`);
  const ptsData = games.map(g => g.played ? g.pts : 0);
  const colors = games.map(g => g.played ? BRAND : INK_200);
  charts.push(new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'PTS', data: ptsData, backgroundColor: colors, borderRadius: 6 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: {
        callbacks: {
          title: (ctx) => `${games[ctx[0].dataIndex].is_home ? 'vs' : '@'} ${games[ctx[0].dataIndex].opponent_name}`,
          label: (ctx) => games[ctx.dataIndex].played ? `${ctx.parsed.y} PTS` : 'No jugó',
        }
      }},
      scales: { y: { beginAtZero: true, grid: { color: INK_200 } }, x: { grid: { display: false } } },
    },
  }));
}

function drawQuarterAvgChart(canvasId, perQuarterAvgs) {
  if (!perQuarterAvgs) return;
  const periods = Object.keys(perQuarterAvgs).sort();
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts.push(new Chart(ctx, {
    type: 'radar',
    data: {
      labels: periods,
      datasets: [{
        label: 'PTS / cuarto',
        data: periods.map(p => perQuarterAvgs[p].pts),
        backgroundColor: BRAND_LIGHT,
        borderColor: BRAND, pointBackgroundColor: BRAND,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { r: { beginAtZero: true, grid: { color: INK_200 }, angleLines: { color: INK_200 } } },
    },
  }));
}

function drawPlayerShotDist(canvasId, totals) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const fromT2 = totals.t2 * 2;
  const fromT3 = totals.t3 * 3;
  const fromFT = totals.tl_made;
  charts.push(new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Canastas de 2', 'Triples', 'Tiros libres'],
      datasets: [{ data: [fromT2, fromT3, fromFT], backgroundColor: [BRAND, '#3b82f6', '#10b981'], borderWidth: 0 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } },
      cutout: '60%',
    },
  }));
}

// ---------------- bootstrap ----------------
bootstrapChrome().then(route).catch(e => {
  console.error(e);
  setError(`No se pudo cargar la liga: ${e.message}`);
});
