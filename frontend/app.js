"use strict";

const config = window.MARBET_CONFIG || {};
const API_BASE = (config.apiBaseUrl || localStorage.getItem("marbet_api_base_url") || "").replace(/\/$/, "");

const NAV_ITEMS = [
  { id: "inicio", label: "Início", icon: "layout-dashboard" },
  { id: "jogos", label: "Jogos", icon: "calendar-days" },
  { id: "prognosticos", label: "Palpites", icon: "chart-no-axes-combined" },
  { id: "resultados", label: "Resultados", icon: "history" },
  { id: "perfil", label: "Perfil", icon: "circle-user-round" },
];

const state = {
  route: "inicio",
  teams: [],
  fixtures: [],
  competitions: [],
  predictions: [],
  picks: [],
  performance: null,
  detail: {},
  history: [],
  user: null,
  selectedFixtureId: null,
  dataError: null,
};

const root = document.getElementById("viewRoot");
const apiState = document.getElementById("apiState");
const pageTitle = document.getElementById("pageTitle");
document.getElementById("todayLabel").textContent = new Intl.DateTimeFormat("pt-AO", {
  weekday: "long",
  day: "numeric",
  month: "long",
  timeZone: "Africa/Luanda",
}).format(new Date());

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function formatDate(value) {
  if (!value) return "Data não disponível";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Data indisponível";
  return new Intl.DateTimeFormat("pt-AO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Luanda" }).format(date);
}

function formatProbability(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "—";
}

function confidenceLabel(confidence, level) {
  // O nível vem do motor (critérios objetivos: dados, validação do mercado, calibração, concordância de modelos).
  if (level === "HIGH") return "Confiança elevada";
  if (level === "MODERATE") return "Confiança moderada";
  if (level === "LOW") return "Confiança baixa";
  const number = Number(confidence);
  if (number >= 0.75) return "Confiança alta";
  if (number >= 0.55) return "Confiança média";
  return "Confiança limitada";
}

function planLabel(plan) {
  return { FREE: "Gratuito", VIP: "VIP", ELITE: "Elite" }[plan] || "Gratuito";
}

function effectiveFixtureStatus(fixture) {
  const statistics = fixture?.statistics;
  const rawStatus = statistics?.fixture?.status?.short
    ?? statistics?.status;
  const normalizedRaw = String(rawStatus || "").trim().toUpperCase();

  const persistedStatus = String(fixture?.status || "").trim().toUpperCase();

  if (["FINISHED", "POSTPONED", "CANCELLED", "SUSPENDED"].includes(persistedStatus)) {
    return persistedStatus;
  }
  if (["HT", "HALF_TIME", "PAUSED", "INTERVAL"].includes(normalizedRaw)) return "INTERVAL";

  return persistedStatus || "SCHEDULED";
}

function statusLabel(status) {
  const normalized = String(status || "").trim().toUpperCase();
  return {
    SCHEDULED: "Agendado",
    LIVE: "A decorrer",
    INTERVAL: "Intervalo",
    HALF_TIME: "Intervalo",
    HT: "Intervalo",
    PAUSED: "Intervalo",
    FINISHED: "Terminado",
    POSTPONED: "Adiado",
    CANCELLED: "Cancelado",
    POSTPONED: "Adiado",
    SUSPENDED: "Suspenso",
    UNKNOWN: "Por confirmar",
  }[normalized] || "Por confirmar";
}

function marketLabel(market) {
  return { "1X2": "Resultado final", DOUBLE_CHANCE: "Dupla possibilidade", DRAW_NO_BET: "Empate anula aposta", TOTAL_GOALS: "Golos", BTTS: "Ambas marcam", TEAM_GOALS_HOME: "Golos da equipa da casa", TEAM_GOALS_AWAY: "Golos da equipa visitante" }[market] || market;
}

function selectionLabel(selection) {
  return {
    HOME: "Vitória da equipa da casa",
    DRAW: "Empate",
    AWAY: "Vitória da equipa visitante",
    "1X": "Casa ou empate",
    X2: "Empate ou visitante",
    "12": "Casa ou visitante",
    "OVER_0.5": "Mais de 0,5 golos",
    "OVER_1.5": "Mais de 1,5 golos",
    "OVER_2.5": "Mais de 2,5 golos",
    "OVER_3.5": "Mais de 3,5 golos",
    "UNDER_2.5": "Menos de 2,5 golos",
    "UNDER_3.5": "Menos de 3,5 golos",
    YES: "Sim",
    NO: "Não",
  }[selection] || selection;
}

function localDateKey(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Africa/Luanda", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

function uniqueFixtures(fixtures) {
  const seen = new Set();
  return fixtures.filter(fixture => {
    const key = String(fixture.id ?? fixture.provider_fixture_id ?? "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function teamBadge(teamId) {
  const team = state.teams.find(item => item.id === teamId);
  const crest = team?.crest_url;
  if (!crest || !/^https:\/\//i.test(crest)) return "";
  return `<img class="team-crest" src="${escapeHtml(crest)}" alt="" loading="lazy" decoding="async">`;
}

function gameCard(fixture, showAnalysis = true) {
  const status = effectiveFixtureStatus(fixture);
  const analysisButton = showAnalysis
    ? `<button class="text-button analysis-button" type="button" data-analysis="${escapeHtml(fixture.id)}">Ver análise</button>`
    : "";
  const score = status === "FINISHED" && fixture.home_score != null && fixture.away_score != null
    ? `<strong class="game-score">${fixture.home_score} – ${fixture.away_score}</strong>`
    : "";
  return `
    <article class="fixture-card">
      <div class="card-top"><span class="competition">${escapeHtml(competitionName(fixture.competition_id))}</span><span class="risk">${statusLabel(status)}</span></div>
      <div class="teams">
        <span class="team-name">${teamBadge(fixture.home_team_id)}${escapeHtml(teamName(fixture.home_team_id))}</span>
        ${score || `<span class="vs">VS</span>`}
        <span class="team-name">${teamBadge(fixture.away_team_id)}${escapeHtml(teamName(fixture.away_team_id))}</span>
      </div>
      <div class="market-row"><span>${formatDate(fixture.kickoff_at)}</span>${analysisButton}</div>
    </article>
  `;
}

function getToken() {
  return localStorage.getItem(config.tokenStorageKey || "marbet_access_token");
}

async function apiRequest(path, options = {}) {
  const retryAuth = options.retryAuth !== false;
  const requestOptions = { ...options };
  delete requestOptions.retryAuth;
  const headers = { "Accept": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...requestOptions, headers });
  } catch (_) {
    throw new Error("Não foi possível ligar ao servidor. Tente novamente.");
  }
  if (response.status === 401 && !/\/auth\/(login|register|refresh)$/.test(path) && retryAuth) {
    const refreshToken = localStorage.getItem(config.refreshStorageKey || "marbet_refresh_token");
    if (refreshToken) {
      try {
        const renewed = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (renewed.ok) {
          const tokens = await renewed.json();
          localStorage.setItem(config.tokenStorageKey || "marbet_access_token", tokens.access_token);
          localStorage.setItem(config.refreshStorageKey || "marbet_refresh_token", tokens.refresh_token);
          return apiRequest(path, { ...options, retryAuth: false });
        }
      } catch (_) {}
      localStorage.removeItem(config.tokenStorageKey || "marbet_access_token");
      localStorage.removeItem(config.refreshStorageKey || "marbet_refresh_token");
      state.user = null;
    }
  }
  if (!response.ok) {
    let detail = response.status === 401
      ? path.endsWith("/auth/login") ? "Email ou palavra-passe incorretos." : "A sessão terminou. Entre novamente."
      : "Não foi possível concluir o pedido. Tente novamente.";
    try {
      const payload = await response.json();
      if (response.status === 409) detail = "Este email já tem uma conta.";
      else if (response.status === 422) detail = "Verifique os dados introduzidos.";
      else if (response.status >= 500) detail = "O servidor está temporariamente indisponível.";
      else if (response.status === 403) detail = "Esta área não está disponível para a sua conta.";
    } catch (_) {}
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error("O servidor está temporariamente indisponível. Tente novamente.");
  }
  return response.json();
}

function setApiState(status, text) {
  apiState.className = `api-state ${status}`;
  apiState.innerHTML = `<i data-lucide="${status === "online" ? "wifi" : status === "offline" ? "wifi-off" : "radio-tower"}"></i><span>${escapeHtml(text)}</span>`;
  refreshIcons();
}

function refreshIcons() {
  if (!window.lucide?.createIcons) return;
  window.lucide.createIcons({
    attrs: { "aria-hidden": "true" },
  });
}

function buildNavigation() {
  const html = NAV_ITEMS.map(item => `
    <button class="nav-item ${state.route === item.id ? "active" : ""}" type="button" data-route="${item.id}">
      <i data-lucide="${item.icon}"></i><span>${item.label}</span>
    </button>
  `).join("");
  document.getElementById("desktopNav").innerHTML = html;
  document.getElementById("mobileNav").innerHTML = html;
  document.querySelectorAll("[data-route]").forEach(button => {
    button.addEventListener("click", () => navigate(button.dataset.route));
  });
}

function navigate(route) {
  state.route = route;
  const item = NAV_ITEMS.find(entry => entry.id === route) || NAV_ITEMS[0];
  pageTitle.textContent = {
    inicio: "Início",
    jogos: "Jogos",
    prognosticos: "Palpites",
    resultados: "Resultados",
    perfil: "Perfil",
  }[route];
  buildNavigation();
  location.hash = route;
  render();
}

function emptyState(title, text, icon = "database") {
  return `<section class="empty-state"><div><i data-lucide="${icon}"></i><h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p></div></section>`;
}

function teamName(teamId) {
  const team = state.teams.find(item => item.id === teamId);
  return team?.name || "Equipa por sincronizar";
}

function competitionName(competitionId) {
  const competition = state.competitions.find(item => item.id === competitionId);
  return competition?.name || "Competição por sincronizar";
}

function predictionFixture(prediction) {
  return state.fixtures.find(item => item.id === prediction.fixture_id);
}

function pct(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : null;
}

function num(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits).replace(".", ",") : null;
}

function formLine(label, rationale, side) {
  const form = rationale.form || {};
  const matches = Number(form[`${side}_matches`]);
  if (!Number.isFinite(matches) || matches <= 0) return `<li>${escapeHtml(label)}: forma recente não disponível.</li>`;
  const scored = num(form[`${side}_goals_for_avg`]);
  const conceded = num(form[`${side}_goals_against_avg`]);
  return `<li>${escapeHtml(label)}: nos últimos ${matches} jogos, ${form[`${side}_wins`]}V ${form[`${side}_draws`]}E ${form[`${side}_losses`]}D, ${scored} golos marcados e ${conceded} sofridos por jogo.</li>`;
}

function riskNotes(prediction) {
  const r = prediction.rationale || {};
  if (Array.isArray(r.risks) && r.risks.length) return r.risks;
  const notes = [];
  const fewest = Math.min(Number(r.home_matches) || 0, Number(r.away_matches) || 0);
  if (fewest > 0 && fewest < 8) notes.push(`Uma das equipas tem apenas ${fewest} jogos no histórico, por isso a estimativa é menos estável.`);
  if (Number(prediction.confidence) < 0.55) notes.push("Confiança limitada: poucos dados ou modelo ainda pouco validado.");
  if (Number.isFinite(Number(r.edge)) && Number(r.edge) < 0.06) notes.push("A vantagem sobre a média histórica é pequena.");
  if (r.calibrated === false) notes.push("As probabilidades ainda não foram calibradas com resultados suficientes.");
  notes.push("O futebol tem muita variância: mesmo uma probabilidade alta falha com frequência.");
  return notes;
}

function analysisDetails(prediction, home, away) {
  const r = prediction.rationale || {};
  const lines = [];
  const gh = num(r.expected_goals_home, 2);
  const ga = num(r.expected_goals_away, 2);
  if (gh !== null && ga !== null) {
    lines.push(`<li>Golos esperados pelo modelo: ${escapeHtml(home)} ${gh} – ${ga} ${escapeHtml(away)}${r.most_likely_score ? ` (resultado mais provável: ${escapeHtml(r.most_likely_score)})` : ""}.</li>`);
  }
  const base = pct(r.base_rate);
  if (base !== null) lines.push(`<li>Média histórica deste resultado nesta competição: ${base}. Estimativa do modelo: ${pct(prediction.probability)}.</li>`);
  lines.push(formLine(home, r, "home"));
  lines.push(formLine(away, r, "away"));
  const h2hMatches = Number(r.form?.h2h_matches ?? r.h2h_matches);
  if (Number.isFinite(h2hMatches) && h2hMatches > 0) {
    lines.push(`<li>Confrontos diretos disponíveis: ${h2hMatches} jogo(s).</li>`);
  }
  if (Number.isFinite(Number(r.finished_matches)) && Number(r.finished_matches) > 0) {
    lines.push(`<li>Calculado com ${r.finished_matches} jogos terminados (${r.home_matches ?? "?"} da equipa da casa e ${r.away_matches ?? "?"} da visitante).</li>`);
  }
  (Array.isArray(r.factors_for) ? r.factors_for : []).forEach(text => lines.push(`<li>${escapeHtml(text)}</li>`));
  if (r.analysed_at) lines.push(`<li>Análise feita em ${escapeHtml(formatDate(r.analysed_at))} com o modelo ${escapeHtml(r.model || "MarBet")}${Array.isArray(r.data_sources) && r.data_sources.length ? `; dados de ${escapeHtml(r.data_sources.join(", "))}` : ""}.</li>`);
  lines.push("<li>Odds: não disponíveis nas fontes de dados gratuitas, por isso não é avaliado valor de aposta, apenas probabilidade.</li>");
  if (!lines.length) return "<p>Ainda não há dados suficientes para apresentar uma explicação.</p>";
  return `<ul class="analysis-list">${lines.join("")}</ul><p class="analysis-title">Principais riscos</p><ul class="analysis-list">${riskNotes(prediction).map(note => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`;
}

function predictionCard(prediction, highlight = false) {
  const fixture = predictionFixture(prediction);
  if (!fixture) return "";
  const home = teamName(fixture.home_team_id);
  const away = teamName(fixture.away_team_id);
  const competition = competitionName(fixture.competition_id);
  const probability = formatProbability(prediction.probability);
  const percentage = `${Math.round(Number(prediction.probability || 0) * 100)}%`;
  const date = formatDate(fixture.kickoff_at);

  return `
    <article class="prediction-card ${highlight ? "highlight" : ""}">
      <div class="card-top"><span class="competition">${escapeHtml(competition)}</span><span class="risk">${confidenceLabel(prediction.confidence, prediction.rationale?.confidence_level)}</span></div>
      <div class="teams"><span class="team-name">${teamBadge(fixture.home_team_id)}${escapeHtml(home)}</span><span class="vs">VS</span><span class="team-name">${teamBadge(fixture.away_team_id)}${escapeHtml(away)}</span></div>
      <div class="market-row">
        <div>
          <small>Probabilidade estimada</small>
          <small>${escapeHtml(marketLabel(prediction.market))} · ${escapeHtml(selectionLabel(prediction.selection))}</small>
          <span>${escapeHtml(date)}</span>
        </div>
        <strong>${probability}</strong>
      </div>
      <div class="probability-bar"><span style="width:${percentage}"></span></div>
      <details class="analysis-details"><summary>Ver análise completa</summary>${analysisDetails(prediction, home, away)}</details>
      <button class="text-button analysis-button" type="button" data-analysis="${escapeHtml(fixture.id)}">Todas as estimativas deste jogo</button>
    </article>
  `;
}

function upcomingPicks() {
  const now = Date.now();
  return state.picks
    .filter(item => {
      const fixture = predictionFixture(item);
      return fixture && fixture.status === "SCHEDULED" && new Date(fixture.kickoff_at).getTime() > now;
    })
    .sort((a, b) => new Date(predictionFixture(a).kickoff_at) - new Date(predictionFixture(b).kickoff_at));
}

function ensureDetail(fixtureId) {
  if (state.detail[fixtureId]) return;
  state.detail[fixtureId] = "loading";
  apiRequest(`/api/v1/intelligence/fixtures/${encodeURIComponent(fixtureId)}/probabilities`)
    .then(data => { state.detail[fixtureId] = data.predictions || []; })
    .catch(() => { state.detail[fixtureId] = "error"; })
    .finally(() => { if (state.route === "prognosticos") render(); });
}

function marketTable(fixtureId) {
  const fixture = state.fixtures.find(item => item.id === fixtureId);
  if (fixture && effectiveFixtureStatus(fixture) === "FINISHED") {
    return `<article class="panel market-table"><h3>Estimativas indisponíveis</h3><p class="prediction-disclaimer">Este jogo já terminou. O MarBet não apresenta estimativas como previsão futura. A análise histórica é usada apenas para avaliar palpites publicados antes do início.</p></article>`;
  }
  const data = state.detail[fixtureId];
  if (data === "loading" || !data) return `<p class="prediction-disclaimer">A carregar as estimativas deste jogo...</p>`;
  if (data === "error") return `<p class="prediction-disclaimer">Não foi possível carregar as estimativas deste jogo.</p>`;
  if (!data.length) return `<p class="prediction-disclaimer">Sem estimativas para este jogo: não há dados suficientes das duas equipas.</p>`;
  const rows = data.filter(item => item.status !== "VOID").map(item => {
    const published = item.rationale?.published_pick ? ` <small class="published-tag">palpite publicado</small>` : "";
    return `<div class="row"><span>${escapeHtml(marketLabel(item.market))} · ${escapeHtml(selectionLabel(item.selection))}${published}</span><strong>${formatProbability(item.probability)}</strong></div>`;
  }).join("");
  return `<article class="panel market-table"><h3>Estimativas do modelo para este jogo</h3>${rows}<p class="prediction-disclaimer">Só um mercado por jogo é publicado como palpite, o que tiver maior vantagem sobre a média histórica.</p></article>`;
}

function renderInicio() {
  if (state.dataError) return emptyState("Dados indisponíveis", "Não foi possível carregar jogos e resultados. Tente novamente.", "wifi-off");
  const today = localDateKey(new Date());
  const now = Date.now();
  const allToday = uniqueFixtures(state.fixtures
    .filter(item => localDateKey(item.kickoff_at) === today)
    .sort((a, b) => new Date(a.kickoff_at) - new Date(b.kickoff_at)));
  const todayGames = allToday;
  const todayIds = new Set(todayGames.map(item => String(item.id)));
  const upcoming = uniqueFixtures(state.fixtures
    .filter(item => ["SCHEDULED", "LIVE", "INTERVAL"].includes(effectiveFixtureStatus(item)))
    .filter(item => localDateKey(item.kickoff_at) > today)
    .filter(item => new Date(item.kickoff_at).getTime() > now)
    .filter(item => !todayIds.has(String(item.id)))
    .sort((a, b) => new Date(a.kickoff_at) - new Date(b.kickoff_at)));
  const best = upcomingPicks()
    .sort((a, b) => Number(b.rationale?.edge || 0) - Number(a.rationale?.edge || 0))
    .slice(0, 3);
  const recent = uniqueFixtures(state.fixtures
    .filter(item => effectiveFixtureStatus(item) === "FINISHED")
    .filter(item => item.home_score != null && item.away_score != null)
    .sort((a, b) => new Date(b.kickoff_at) - new Date(a.kickoff_at)))
    .slice(0, 3);
  const greeting = new Date().getHours() < 12 ? "Bom dia" : new Date().getHours() < 18 ? "Boa tarde" : "Boa noite";
  return `
    <section class="welcome"><p class="eyebrow">${escapeHtml(greeting)}</p><h2>Veja os jogos e as análises de hoje.</h2></section>
    <section>
      <div class="section-head"><div><h2>Jogos de hoje</h2></div><button class="filter-button" type="button" data-route="jogos">Ver jogos</button></div>
      ${todayGames.length ? `<div class="fixture-grid">${todayGames.slice(0, 3).map(item => gameCard(item)).join("")}</div>` : emptyState("Sem jogos para hoje", "Os jogos confirmados para hoje aparecerão aqui.", "calendar-days")}
    </section>
    <section>
      <div class="section-head"><div><h2>Principais palpites</h2></div><button class="filter-button" type="button" data-route="prognosticos">Ver todos</button></div>
      ${best.length ? `<div class="prediction-grid">${best.map((item, index) => predictionCard(item, index === 0)).join("")}</div>` : emptyState("Sem palpites disponíveis", "Os palpites só aparecem quando há dados reais suficientes.", "chart-no-axes-combined")}
    </section>
    <section><div class="section-head"><div><h2>Próximos jogos</h2></div></div>${upcoming.length ? `<div class="fixture-grid">${upcoming.slice(0, 3).map(item => gameCard(item)).join("")}</div>` : emptyState("Sem próximos jogos", "Os próximos jogos aparecerão quando forem atualizados.", "calendar-days")}</section>
    <section><div class="section-head"><div><h2>Resultados recentes</h2></div><button class="filter-button" type="button" data-route="resultados">Ver resultados</button></div>${recent.length ? `<div class="fixture-grid">${recent.map(item => gameCard(item, false)).join("")}</div>` : emptyState("Sem resultados recentes", "Os resultados oficiais aparecerão depois dos jogos.", "history")}</section>
  `;
}

function renderJogos() {
  if (state.dataError) return emptyState("Jogos indisponíveis", "Não foi possível carregar os jogos. Tente novamente.", "wifi-off");
  if (!state.fixtures.length) return emptyState("Ainda não há jogos", "Quando os dados desportivos forem atualizados, os jogos reais aparecerão aqui.", "calendar-days");
  return `<section><div class="section-head"><div><h2>Jogos disponíveis</h2></div></div><div class="fixture-grid">${state.fixtures.slice(0, 60).map(item => gameCard(item)).join("")}</div></section>`;
}

function renderPrognosticos() {
  if (state.dataError) return emptyState("Análises indisponíveis", "Não foi possível carregar os palpites. Tente novamente.", "wifi-off");
  const picks = upcomingPicks();
  const selected = state.selectedFixtureId;
  const visible = selected ? picks.filter(item => item.fixture_id === selected) : picks;
  if (!selected && !visible.length) return emptyState("Sem palpites disponíveis", "Sem palpite recomendado — os dados disponíveis não demonstram uma vantagem estatística suficientemente clara.", "chart-no-axes-combined");
  const fixture = selected ? state.fixtures.find(item => item.id === selected) : null;
  const clear = selected ? `<button class="filter-button" type="button" data-clear-selection>Ver todos os palpites</button>` : "";
  if (selected) ensureDetail(selected);
  const empty = selected && !visible.length
    ? `${fixture ? gameCard(fixture, false) : ""}<p class="prediction-disclaimer">O MarBet analisou este jogo, mas não encontrou uma seleção que cumprisse os critérios estatísticos necessários para publicar um palpite. As probabilidades abaixo são apenas estimativas do modelo, não recomendações de aposta.</p>`
    : "";
  return `
    <div class="section-head"><div><h2>${selected ? "Análise do jogo" : "Análises dos jogos"}</h2></div>${clear}</div>
    <p class="prediction-disclaimer">As probabilidades são estimativas e não garantem resultados.</p>
    ${empty}
    ${visible.length ? `<div class="prediction-grid">${visible.slice(0, 40).map((item, index) => predictionCard(item, index === 0)).join("")}</div>` : ""}
    ${selected ? marketTable(selected) : ""}
  `;
}

function renderResultados() {
  if (state.dataError) return emptyState("Resultados indisponíveis", "Não foi possível carregar os resultados. Tente novamente.", "wifi-off");
  const correct = state.history.filter(item => item.status === "SETTLED" && item.is_correct === true).length;
  const incorrect = state.history.filter(item => item.status === "SETTLED" && item.is_correct === false).length;
  const pending = state.history.filter(item => item.status === "PENDING").length;
  const total = correct + incorrect;
  const perf = state.performance?.markets || {};
  const perfRows = Object.entries(perf).sort(([a], [b]) => (a === "TOTAL" ? -1 : b === "TOTAL" ? 1 : a.localeCompare(b))).map(([key, m]) => `<div class="row"><span>${key === "TOTAL" ? "Todos os mercados" : escapeHtml(marketLabel(key))} (${m.samples})</span><strong>Acerto ${Math.round(m.hit_rate * 100)}% · Brier ${num(m.brier_score, 3)} · Log Loss ${num(m.log_loss, 3)}</strong></div>`).join("");
  const accuracy = total ? `${Math.round(correct / total * 100)}%` : "—";
  const errorRate = total ? `${Math.round(incorrect / total * 100)}%` : "—";
  if (!state.history.length) return emptyState("Ainda não há resultados", "Os palpites publicados e os respetivos resultados confirmados aparecerão aqui.", "history");
  const historyCard = item => {
    const fixture = predictionFixture(item);
    const settled = item.status === "SETTLED";
    const resultLabel = !settled ? "PENDENTE" : item.is_correct ? "ACERTO" : "ERRO";
    const resultClass = !settled ? "" : item.is_correct ? "result-hit" : "result-miss";
    const score = fixture && fixture.home_score != null && fixture.away_score != null ? `${fixture.home_score} – ${fixture.away_score}` : "Resultado por confirmar";
    const probability = item.rationale?.first_probability != null ? formatProbability(item.rationale.first_probability) : formatProbability(item.probability);
    return `<article class="history-row"><div><strong>${escapeHtml(fixture ? `${teamName(fixture.home_team_id)} – ${teamName(fixture.away_team_id)}` : "Jogo")}</strong><small>${escapeHtml(fixture ? competitionName(fixture.competition_id) : "Competição")}</small><small>${escapeHtml(formatDate(fixture?.kickoff_at))}</small><small>Palpite: ${escapeHtml(marketLabel(item.market))} · ${escapeHtml(selectionLabel(item.selection))}</small><small>Probabilidade publicada: ${probability}</small><small>Resultado final: ${escapeHtml(score)}</small></div><strong class="${resultClass}">${resultLabel}</strong></article>`;
  };
  const hits = state.history.filter(item => item.status === "SETTLED" && item.is_correct === true);
  const misses = state.history.filter(item => item.status === "SETTLED" && item.is_correct === false);
  const pendingItems = state.history.filter(item => item.status === "PENDING");
  return `
    <section class="result-summary"><article><span>Total avaliados</span><strong>${total}</strong></article><article><span>Acertos</span><strong>${correct}</strong></article><article><span>Erros</span><strong>${incorrect}</strong></article><article><span>Taxa de acerto</span><strong>${accuracy}</strong></article><article><span>Taxa de erro</span><strong>${errorRate}</strong></article><article><span>Pendentes</span><strong>${pending}</strong></article></section>
    ${perfRows ? `<article class="panel market-table"><h3>Desempenho dos palpites publicados</h3>${perfRows}<p class="prediction-disclaimer">Brier e Log Loss mais baixos são melhores. Amostras pequenas variam muito.</p></article>` : ""}
    <div class="section-head"><div><h2>Palpites acertados</h2></div></div>
    ${hits.length ? hits.map(historyCard).join("") : emptyState("Sem acertos avaliados", "Os acertos aparecerão quando houver histórico terminado confirmado.", "circle-check")}
    <div class="section-head"><div><h2>Palpites errados</h2></div></div>
    ${misses.length ? misses.map(historyCard).join("") : emptyState("Sem erros avaliados", "Os erros aparecerão quando houver histórico terminado confirmado.", "circle-x")}
    <div class="section-head"><div><h2>Palpites pendentes</h2></div></div>
    ${pendingItems.length ? pendingItems.map(historyCard).join("") : emptyState("Sem palpites pendentes", "Não existem palpites publicados aguardando um resultado confirmado.", "clock")}
    <div class="section-head"><div><h2>Histórico completo</h2></div></div>
    ${state.history.map(historyCard).join("")}
  `;
}

function renderPerfil() {
  return `
    <div class="profile-grid">
      <article class="profile-card"><div class="avatar">${state.user ? escapeHtml(state.user.display_name?.[0]?.toUpperCase()) : "M"}</div><h2>${state.user ? escapeHtml(state.user.display_name) : "Visitante"}</h2><p class="eyebrow">${state.user ? planLabel(state.user.plan) : "Plano gratuito"}</p><button class="primary-button" id="profileAuthButton" type="button">${state.user ? "Terminar sessão" : "Entrar ou criar conta"}</button></article>
      <article class="panel"><h3>Dados da conta</h3><div class="row"><span>Email</span><strong>${state.user ? escapeHtml(state.user.email) : "Não autenticado"}</strong></div><div class="row"><span>Validade do plano</span><strong>Não indicada</strong></div><div class="row"><span>Favoritos</span><strong>Não configurados</strong></div><div class="row"><span>Notificações</span><strong>Não configuradas</strong></div></article>
    </div>
  `;
}

function render() {
  if (state.route === "inicio") root.innerHTML = renderInicio();
  if (state.route === "jogos") root.innerHTML = renderJogos();
  if (state.route === "prognosticos") root.innerHTML = renderPrognosticos();
  if (state.route === "resultados") root.innerHTML = renderResultados();
  if (state.route === "perfil") root.innerHTML = renderPerfil();
  root.querySelectorAll("[data-route]").forEach(button => button.addEventListener("click", () => navigate(button.dataset.route)));
  root.querySelectorAll("[data-analysis]").forEach(button => button.addEventListener("click", () => {
    state.selectedFixtureId = button.dataset.analysis;
    navigate("prognosticos");
  }));
  root.querySelectorAll("[data-clear-selection]").forEach(button => button.addEventListener("click", () => {
    state.selectedFixtureId = null;
    render();
  }));
  const profileButton = document.getElementById("profileAuthButton");
  if (profileButton) profileButton.addEventListener("click", state.user ? logout : openAuth);
  const accountName = state.user ? state.user.display_name : "Conta MarBet";
  document.querySelector("#profileMini strong").textContent = accountName;
  document.querySelector("#profileMini small").textContent = state.user ? planLabel(state.user.plan) : "Entrar";
  refreshIcons();
}

async function loadData() {
  if (!API_BASE) {
    state.dataError = "API não configurada";
    setApiState("offline", "Não foi possível ligar ao servidor. Tente novamente.");
    render();
    return;
  }

  state.dataError = null;
  setApiState("loading", "A carregar jogos e resultados...");
  try {
    await apiRequest("/health/live");

    const [teams, fixtures, competitions, picks, performance, history, providers] = await Promise.all([
      apiRequest("/api/v1/data/teams?limit=500"),
      apiRequest("/api/v1/data/fixtures?limit=500"),
      apiRequest("/api/v1/data/competitions"),
      apiRequest("/api/v1/intelligence/picks?limit=60"),
      apiRequest("/api/v1/intelligence/performance"),
      apiRequest("/api/v1/intelligence/history?limit=500"),
      apiRequest("/api/v1/data/providers/status"),
    ]);

    state.teams = teams;
    state.fixtures = fixtures;
    state.competitions = competitions;
    state.picks = picks;
    state.performance = performance;
    state.detail = {};
    state.history = history;
    if (state.selectedFixtureId && !fixtures.some(item => item.id === state.selectedFixtureId)) {
      state.selectedFixtureId = null;
    }

    if (getToken()) {
      try {
        state.user = await apiRequest("/api/v1/auth/me");
      } catch (error) {
        state.user = null;
        if (error.status !== 401) showToast(error.message);
      }
    }

    const statuses = Object.values(providers);
    const hasSuccessfulSync = statuses.some(item => item.latest_sync?.status === "SUCCESS");
    const hasFailedSync = statuses.some(item => item.latest_sync?.status === "FAILED");
    const hasConfiguredProvider = statuses.some(item => item.configured);
    if (hasSuccessfulSync && fixtures.length) setApiState("online", "Dados desportivos atualizados");
    else if (hasFailedSync && !fixtures.length) setApiState("offline", "Não foi possível atualizar os jogos. Tente novamente mais tarde.");
    else if (fixtures.length) setApiState("online", hasFailedSync ? "Dados disponíveis; algumas competições não foram atualizadas" : "Dados desportivos disponíveis");
    else if (hasSuccessfulSync) setApiState("loading", "Fontes consultadas; ainda não há jogos disponíveis");
    else if (hasConfiguredProvider) setApiState("loading", "Sistema de dados configurado; à espera da primeira atualização");
    else setApiState("offline", "Os dados desportivos ainda não estão configurados");
  } catch (error) {
    state.dataError = error.message;
    setApiState("offline", error.message);
  } finally {
    render();
  }
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2800);
}

const authDialog = document.getElementById("authDialog");
const authForm = document.getElementById("authForm");
let registerMode = false;

function openAuth() {
  registerMode = false;
  document.getElementById("authTitle").textContent = "Entrar";
  document.getElementById("authSubmit").textContent = "Entrar";
  document.getElementById("displayNameField").hidden = true;
  document.getElementById("displayName").required = false;
  document.getElementById("authError").textContent = "";
  authDialog.showModal();
}

function logout() {
  localStorage.removeItem(config.tokenStorageKey || "marbet_access_token");
  localStorage.removeItem(config.refreshStorageKey || "marbet_refresh_token");
  state.user = null;
  showToast("Sessão terminada");
  render();
}

document.getElementById("authModeButton").addEventListener("click", () => {
  registerMode = !registerMode;
  document.getElementById("authTitle").textContent = registerMode ? "Criar conta" : "Entrar";
  document.getElementById("authSubmit").textContent = registerMode ? "Criar conta" : "Entrar";
  document.getElementById("displayNameField").hidden = !registerMode;
  document.getElementById("displayName").required = registerMode;
  document.getElementById("password").autocomplete = registerMode ? "new-password" : "current-password";
  document.getElementById("authError").textContent = "";
});

authForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return authDialog.close();

  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const displayName = document.getElementById("displayName").value.trim();
  const errorElement = document.getElementById("authError");
  errorElement.textContent = "";

  try {
    if (registerMode) {
      await apiRequest("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ email, display_name: displayName, password }) });
      showToast("Conta criada. Entre com as credenciais.");
      registerMode = false;
      document.getElementById("authTitle").textContent = "Entrar";
      document.getElementById("authSubmit").textContent = "Entrar";
      document.getElementById("displayNameField").hidden = true;
      document.getElementById("displayName").required = false;
      document.getElementById("displayName").value = "";
      return;
    }

    const tokens = await apiRequest("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    localStorage.setItem(config.tokenStorageKey || "marbet_access_token", tokens.access_token);
    localStorage.setItem(config.refreshStorageKey || "marbet_refresh_token", tokens.refresh_token);
    state.user = await apiRequest("/api/v1/auth/me");
    authDialog.close();
    showToast("Sessão iniciada");
    render();
  } catch (error) {
    errorElement.textContent = error.message;
  }
});

document.getElementById("profileMini").addEventListener("click", () => state.user ? navigate("perfil") : openAuth());
document.getElementById("refreshButton").addEventListener("click", loadData);

const initialRoute = location.hash.replace("#", "");
if (NAV_ITEMS.some(item => item.id === initialRoute)) state.route = initialRoute;

buildNavigation();
render();
loadData();
window.addEventListener("load", refreshIcons);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
