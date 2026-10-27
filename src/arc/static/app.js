// Webapp dos ar-condicionados. Sem build: módulos ES servidos direto.

const MODES = {
  IOT_COOL: { label: "Frio", color: "--cool" },
  IOT_HEAT: { label: "Calor", color: "--heat" },
  IOT_DRY: { label: "Seco", color: "--dry" },
  IOT_FAN: { label: "Ventilar", color: "--fan" },
  IOT_AUTO: { label: "Auto", color: "--auto" },
  IOT_10_HEATING: { label: "10° anti-gelo", color: "--heat" },
};

const FAN = [["5", "Auto"], ["3", "Baixo"], ["2", "Médio"], ["1", "Alto"]];
const VERTICAL = [["8", "Oscilar"], ["2", "Bem alto"], ["4", "Alto"], ["5", "Meio"], ["6", "Baixo"], ["7", "Bem baixo"]];
const HORIZONTAL = [["7", "Oscilar"], ["3", "Bem à esq."], ["4", "Esquerda"], ["0", "Centro"], ["5", "Direita"], ["6", "Bem à dir."]];
const TOGGLES = [
  ["silentSleepStatus", "Modo noturno", "Ajusta a temperatura aos poucos durante o sono"],
  ["muteStatus", "Silencioso", "Reduz o barulho (desliga o Turbo)"],
  ["rapidMode", "Turbo", "Chega mais rápido na temperatura (desliga o Silencioso)"],
  ["selfCleaningStatus", "Antimofo", "Seca a umidade interna ao desligar"],
  ["healthMode", "Ionizador", null],
  ["screenDisplayStatus", "Luz do painel", null],
];

// Sem atualização periódica: atualiza ao abrir/voltar para o app, no botão
// do topo e uma vez depois de cada comando, para confirmar o que o aparelho aceitou.
const CONFIRM_MS = 6000;
// Ao abrir a tela de um aparelho, recarrega se o que está na tela for mais velho que isso.
const STALE_MS = 60000;

// Turbo e Silencioso: ligar um desliga o outro (e o ventilador vai para o auto).
const EXCLUSIVE = { rapidMode: "muteStatus", muteStatus: "rapidMode" };
function exclusive(key, checked, caps) {
  const changes = { [key]: checked ? "1" : "0" };
  const other = EXCLUSIVE[key];
  if (checked && other && caps[other]) changes[other] = "0";
  return changes;
}
const TEMP_DEBOUNCE_MS = 700;

const state = {
  devices: [],
  drafts: {}, // id -> {programa, parametros}: edições com o aparelho desligado
  busy: new Set(),
  error: null,
  schedules: [],
  history: [],
  form: null, // agendamento em edição
};

const app = document.getElementById("app");

// --- API --------------------------------------------------------------------

async function api(path, body, method) {
  const res = await fetch(`/api${path}`, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (res.status === 401) {
    location.replace("/entrar");
    throw new Error("Sessão expirada");
  }
  if (!res.ok) {
    let msg = `Erro ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

async function loadSchedules() {
  try {
    const data = await api("/agendamentos");
    state.schedules = data.agendamentos;
    state.history = data.historico;
  } catch (e) {
    toast(e.message);
  }
}

// Redesenhar com um campo em foco fecha o seletor do celular e perde o que foi digitado.
let renderPending = false;
function isEditing() {
  const el = document.activeElement;
  return el && app.contains(el) && (el.tagName === "SELECT" || (el.tagName === "INPUT" && el.type !== "checkbox"));
}
function renderWhenIdle() {
  if (isEditing()) renderPending = true;
  else render();
}
app.addEventListener("focusout", () => setTimeout(() => {
  if (renderPending && !isEditing()) { renderPending = false; render(); }
}, 0));

async function refresh() {
  state.refreshing = true;
  renderWhenIdle();
  loadSchedules().then(renderWhenIdle);
  try {
    const devices = await api("/aparelhos");
    // Não sobrescreve um aparelho com comando em andamento.
    state.devices = devices.map((d) => (state.busy.has(d.id) ? find(d.id) || d : d));
    state.loadedAt = Date.now();
    state.error = null;
  } catch (e) {
    state.error = e.message;
  }
  state.refreshing = false;
  renderWhenIdle();
}

let confirmTimer;
async function command(id, action, body = {}) {
  state.busy.add(id);
  render();
  try {
    const updated = await api(`/aparelhos/${encodeURIComponent(id)}/${action}`, body);
    replace(updated);
    if (action !== "ajustar") delete state.drafts[id];
    clearTimeout(confirmTimer);
    confirmTimer = setTimeout(refresh, CONFIRM_MS);
  } catch (e) {
    toast(e.message);
  } finally {
    state.busy.delete(id);
    render();
  }
}

// --- estado local -------------------------------------------------------------

const find = (id) => state.devices.find((d) => d.id === id);

function replace(device) {
  state.devices = state.devices.map((d) => (d.id === device.id ? device : d));
}

// Visão efetiva: estado da nuvem + rascunho (quando desligado).
function view(d) {
  const draft = state.drafts[d.id] || { programa: null, parametros: {} };
  const programa = draft.programa || d.programa || "IOT_COOL";
  const valores = { ...d.estado, ...draft.parametros };
  const caps = d.programas[programa] || {};
  return { programa, valores, caps };
}

function edit(d, changes, { programa } = {}) {
  if (d.ligado) {
    const body = { parametros: changes };
    if (programa) body.programa = programa;
    command(d.id, "ajustar", body);
    return;
  }
  const draft = (state.drafts[d.id] ||= { programa: null, parametros: {} });
  if (programa) draft.programa = programa;
  Object.assign(draft.parametros, changes);
  render();
}

// Temperatura: atualiza a tela na hora e envia só depois que o usuário para de tocar.
const tempTimers = {};
function bumpTemp(d, delta) {
  const { valores, caps } = view(d);
  const spec = caps.tempSel;
  if (!spec) return;
  const next = Math.min(spec.max, Math.max(spec.min, num(valores.tempSel, 22) + delta * spec.passo));
  if (!d.ligado) return edit(d, { tempSel: next });
  d.estado.tempSel = String(next);
  render();
  clearTimeout(tempTimers[d.id]);
  tempTimers[d.id] = setTimeout(() => command(d.id, "ajustar", { parametros: { tempSel: next } }), TEMP_DEBOUNCE_MS);
}

function turnOn(d) {
  const draft = state.drafts[d.id] || {};
  command(d.id, "ligar", { programa: draft.programa || d.programa || "IOT_COOL", parametros: draft.parametros || {} });
}

// --- render -------------------------------------------------------------------

const num = (v, fallback = null) => (v === null || v === undefined || v === "" || isNaN(+v) ? fallback : +v);
const deg = (v) => (num(v) === null ? "–" : `${String(+num(v).toFixed(1)).replace(".", ",")}°`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const modeVar = (programa) => `var(${(MODES[programa] || MODES.IOT_COOL).color})`;

const ICON_POWER = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/></svg>`;
const ICON_REFRESH = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/></svg>`;
const refreshButton = () => `<button class="icon-btn ${state.refreshing ? "busy" : ""}" data-refresh aria-label="Atualizar">${ICON_REFRESH}</button>`;
const ICON_BACK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>`;

function statusLine(d) {
  if (!d.online) return "Offline";
  if (!d.ligado) return "Desligado";
  const mode = MODES[d.programa]?.label || "Ligado";
  return d.programa === "IOT_FAN" ? mode : `${mode} · ${deg(d.estado.tempSel)}`;
}

function renderList() {
  const outdoor = state.devices.map((d) => num(d.estado.tempOutdoor)).find((t) => t !== null);
  const anyOn = state.devices.some((d) => d.ligado);
  return `
    <header class="top">
      <h1>Ar de Casa</h1>
      ${outdoor !== undefined ? `<span class="sub">Lá fora ${deg(outdoor)}</span>` : ""}
      ${refreshButton()}
    </header>
    ${state.error ? `<p class="note warn">${esc(state.error)}</p>` : ""}
    <div class="cards">
      ${state.devices.map((d) => `
        <div class="card ${d.ligado ? "on" : ""} ${d.online ? "" : "offline"} ${state.busy.has(d.id) ? "busy" : ""}"
             style="--mode:${modeVar(d.programa)}" data-open="${esc(d.id)}" role="button" tabindex="0">
          <div class="room" aria-label="Temperatura ambiente">${deg(d.estado.tempIndoor)}</div>
          <div class="info">
            <div class="name">${esc(d.nome)}</div>
            <div class="line">${statusLine(d)}</div>
          </div>
          <button class="power" data-power="${esc(d.id)}" ${d.online ? "" : "disabled"}
                  aria-label="${d.ligado ? "Desligar" : "Ligar"} ${esc(d.nome)}">${ICON_POWER}</button>
        </div>`).join("")}
    </div>
    ${anyOn ? `<div class="quick"><button class="main-action off" data-all-off style="grid-column: span 2">Desligar todos</button></div>` : ""}
    ${renderScheduleList()}
    <p class="footer"><button class="link" data-logout>Sair</button></p>
  `;
}

function renderDetail(d) {
  const { programa, valores, caps } = view(d);
  const busy = state.busy.has(d.id);
  const disabled = !d.online || busy ? "disabled" : "";
  const showTemp = caps.tempSel && programa !== "IOT_FAN" && programa !== "IOT_10_HEATING";
  const t = num(valores.tempSel, 22);
  const fanValues = caps.windSpeed?.valores || [];
  const toggles = TOGGLES.filter(([key]) => caps[key]);
  // Regras observadas no aparelho: turbo/silencioso travam o ventilador no auto,
  // e o silencioso é recusado com o turbo ligado.
  const turbo = caps.rapidMode && String(valores.rapidMode) === "1";
  const silent = caps.muteStatus && String(valores.muteStatus) === "1";
  const fanLock = turbo ? "Turbo" : silent ? "Silencioso" : null;

  const choices = (key, labels) => {
    if (!caps[key]) return "";
    return `<div class="seg">
      ${labels.filter(([v]) => caps[key].valores.includes(v)).map(([v, label]) => `
        <button class="chip ${String(valores[key]) === v ? "sel" : ""}" data-param="${key}" data-value="${v}" ${disabled}>${label}</button>`).join("")}
    </div>`;
  };

  return `
    <header class="top">
      <button class="icon-btn" data-back aria-label="Voltar">${ICON_BACK}</button>
      <h1>${esc(d.nome)}</h1>
      ${refreshButton()}
    </header>
    <div class="temps">
      <span class="pill">Dentro <b>${deg(d.estado.tempIndoor)}</b></span>
      <span class="pill">Fora <b>${deg(d.estado.tempOutdoor)}</b></span>
      <span class="pill"><b>${d.online ? (d.ligado ? "Ligado" : "Desligado") : "Offline"}</b></span>
    </div>
    ${!d.online ? `<p class="note warn">O aparelho não está conectado à nuvem.</p>` : ""}
    ${d.online && !d.ligado ? `<p class="note">Desligado: ajuste abaixo e toque em Ligar.</p>` : ""}

    <div class="${busy ? "busy" : ""}">
    ${showTemp ? `
      <div class="dial">
        <button class="step" data-temp="-1" aria-label="Diminuir" ${disabled || (t <= caps.tempSel.min ? "disabled" : "")}>−</button>
        <div class="value"><div class="big">${deg(t)}</div><div class="caption">temperatura alvo</div></div>
        <button class="step" data-temp="1" aria-label="Aumentar" ${disabled || (t >= caps.tempSel.max ? "disabled" : "")}>+</button>
      </div>` : programa === "IOT_10_HEATING" ? `<p class="note">Mantém o cômodo em torno de 10° para não congelar.</p>` : ""}

    <section>
      <h2>Modo</h2>
      <div class="seg">
        ${Object.keys(MODES).filter((p) => d.programas[p]).map((p) => `
          <button class="chip ${p === programa ? "sel" : ""}" data-mode="${p}" style="--mode:${modeVar(p)}" ${disabled}>${MODES[p].label}</button>`).join("")}
      </div>
    </section>

    ${fanValues.length ? `
    <section>
      <h2>Ventilador</h2>
      <div class="seg four">
        ${FAN.filter(([v]) => fanValues.includes(v)).map(([v, label]) => `
          <button class="chip ${String(valores.windSpeed) === v ? "sel" : ""}" data-param="windSpeed" data-value="${v}" ${disabled || (fanLock && v !== "5" ? "disabled" : "")}>${label}</button>`).join("")}
      </div>
      ${fanLock ? `<p class="note">Com o ${fanLock} ligado o ventilador fica no automático.</p>` : ""}
    </section>` : ""}

    ${caps.windDirectionVertical || caps.windDirectionHorizontal ? `
    ${caps.windDirectionVertical ? `<section><h2>Paleta vertical</h2>${choices("windDirectionVertical", VERTICAL)}</section>` : ""}
    ${caps.windDirectionHorizontal ? `<section><h2>Paleta horizontal</h2>${choices("windDirectionHorizontal", HORIZONTAL)}</section>` : ""}` : ""}

    ${toggles.length ? `
    <section>
      <h2>Funções</h2>
      <div class="list">
        ${toggles.map(([key, label, hint]) => `
          <label class="row">
            <span class="label">${label}${hint ? `<br><span class="hint">${hint}</span>` : ""}</span>
            <span class="switch"><input type="checkbox" data-toggle="${key}" ${String(valores[key]) === "1" ? "checked" : ""} ${disabled}><span></span></span>
          </label>`).join("")}
      </div>
    </section>` : ""}
    </div>

    <button class="main-action ${d.ligado ? "off" : ""}" data-power="${esc(d.id)}" ${disabled}>
      ${busy ? "Enviando…" : d.ligado ? "Desligar" : `Ligar no ${(MODES[programa] || MODES.IOT_COOL).label.toLowerCase()}`}
    </button>
  `;
}

function render() {
  const id = decodeURIComponent(location.hash.slice(1));
  if (id.startsWith("agenda/")) {
    if (!state.devices.length) {
      app.innerHTML = `<p class="loading">Carregando…</p>`;
      return;
    }
    app.style.setProperty("--mode", modeVar(state.form?.programa));
    if (!state.form) openForm(id.slice(7));
    app.innerHTML = state.form ? renderScheduleForm() : `<p class="loading">Carregando…</p>`;
    return;
  }
  state.form = null;
  const device = id && find(id);
  if (!state.devices.length) {
    app.innerHTML = state.error ? `<p class="empty warn">${esc(state.error)}</p>` : `<p class="loading">Carregando…</p>`;
    return;
  }
  // Mantém o foco do teclado/leitor de tela entre renders.
  const focused = document.activeElement?.closest("[data-mode],[data-param],[data-toggle],[data-temp],[data-power]");
  const focusKey = focused && ["data-mode", "data-param", "data-toggle", "data-temp", "data-power"]
    .map((a) => focused.hasAttribute(a) && `[${a}="${focused.getAttribute(a)}"]`).find(Boolean);

  app.style.setProperty("--mode", device ? modeVar(view(device).programa) : "");
  app.innerHTML = device ? renderDetail(device) : renderList();
  if (focusKey) app.querySelector(focusKey)?.focus();
}

// --- eventos --------------------------------------------------------------------

app.addEventListener("click", (e) => {
  const el = e.target.closest("button, [data-open]");
  if (!el || el.disabled) return;
  if (el.closest("[data-schedule-ui]")) return scheduleClick(el, e);
  const id = decodeURIComponent(location.hash.slice(1));
  const d = find(id);

  if (el.dataset.power) {
    e.stopPropagation();
    const target = find(el.dataset.power);
    return target.ligado ? command(target.id, "desligar") : turnOn(target);
  }
  if (el.hasAttribute("data-refresh")) return refresh();
  if (el.hasAttribute("data-logout")) {
    return fetch("/api/sair", { method: "POST" }).finally(() => location.replace("/entrar"));
  }
  if (el.hasAttribute("data-all-off")) {
    return state.devices.filter((x) => x.ligado && x.online).forEach((x) => command(x.id, "desligar"));
  }
  if (el.dataset.open) return (location.hash = encodeURIComponent(el.dataset.open));
  if (el.hasAttribute("data-back")) return (location.hash = "");
  if (!d) return;
  if (el.dataset.temp) return bumpTemp(d, +el.dataset.temp);
  if (el.dataset.mode) {
    const { programa } = view(d);
    if (el.dataset.mode !== programa) edit(d, {}, { programa: el.dataset.mode });
    return;
  }
  if (el.dataset.param && el.dataset.value) return edit(d, { [el.dataset.param]: el.dataset.value });
});

app.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.dataset?.open) {
    e.preventDefault();
    location.hash = encodeURIComponent(e.target.dataset.open);
  }
});

app.addEventListener("change", (e) => {
  if (e.target.closest("[data-schedule-ui]")) return scheduleChange(e.target);
  const d = find(decodeURIComponent(location.hash.slice(1)));
  if (!d) return;
  if (e.target.dataset.toggle) return edit(d, exclusive(e.target.dataset.toggle, e.target.checked, view(d).caps));
});

app.addEventListener("input", (e) => {
  const key = e.target.dataset?.fInput;
  if (key && state.form) state.form[key] = e.target.value;
});

let currentHash = location.hash;
window.addEventListener("hashchange", () => {
  if (currentHash.startsWith("#agenda/") && location.hash !== currentHash && isDirty()) {
    state.form.confirmLeave = true;
    location.hash = currentHash; // volta para o formulário; a barra pergunta o que fazer
    return;
  }
  currentHash = location.hash;
  render();
  window.scrollTo(0, 0);
  const id = decodeURIComponent(location.hash.slice(1));
  if (find(id) && Date.now() - (state.loadedAt || 0) > STALE_MS) refresh();
});

// Recarregar ou fechar a página com alterações: o navegador pede confirmação.
window.addEventListener("beforeunload", (e) => {
  if (isDirty()) {
    e.preventDefault();
    e.returnValue = "";
  }
});

let toastTimer;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 4000);
}

document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

refresh();

// --- agendamentos ------------------------------------------------------------------

const DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const FREQ = [["uma_vez", "Uma vez"], ["diario", "Todo dia"], ["semanal", "Dias da semana"]];

const deviceName = (mac) => find(mac)?.nome || mac;

function scheduleSummary(s) {
  if (!s.programa) return "Desligar";
  const p = s.parametros || {};
  const parts = [MODES[s.programa]?.label || s.programa];
  if (p.tempSel !== undefined && s.programa !== "IOT_FAN" && s.programa !== "IOT_10_HEATING") parts.push(deg(p.tempSel));
  if (p.silentSleepStatus === "1") parts.push("noturno");
  if (p.muteStatus === "1") parts.push("silencioso");
  if (p.rapidMode === "1") parts.push("turbo");
  if (p.screenDisplayStatus === "0") parts.push("luz apagada");
  return parts.join(" · ");
}

function whenLabel(iso) {
  const d = new Date(iso);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 864e5);
  const hhmm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `hoje ${hhmm}`;
  if (d.toDateString() === tomorrow.toDateString()) return `amanhã ${hhmm}`;
  return `${d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" })} ${hhmm}`;
}

function renderScheduleList() {
  const items = state.schedules.map((s) => `
    <div class="row sched ${s.ativo ? "" : "inactive"}">
      <button class="sched-open" data-sched-open="${esc(s.id)}">
        <span class="label">${esc(s.nome || s.aparelhos.map(deviceName).join(", "))}</span>
        <span class="hint">${esc(s.descricao)} · ${esc(scheduleSummary(s))}</span>
        ${s.proximo ? `<span class="hint">Próximo: ${s.proximo.acao} ${whenLabel(s.proximo.quando)}</span>` : ""}
      </button>
      <span class="switch"><input type="checkbox" aria-label="Ativo" data-sched-active="${esc(s.id)}" ${s.ativo ? "checked" : ""}><span></span></span>
    </div>`).join("");
  const recent = state.history.slice(0, 5).map((h) => {
    const ok = Object.values(h.resultado).every((r) => r === "ok");
    const detail = ok ? "ok" : Object.entries(h.resultado).map(([mac, r]) => `${mac === "*" ? "" : deviceName(mac) + ": "}${r}`).join("; ");
    return `<div class="row"><span class="label">${h.acao} · ${whenLabel(h.previsto)}<br><span class="hint ${ok ? "" : "warn"}">${esc(h.nome || "")} ${esc(detail)}</span></span></div>`;
  }).join("");
  return `
    <section data-schedule-ui>
      <h2>Agendamentos</h2>
      ${items ? `<div class="list">${items}</div>` : `<p class="note">Nenhum agendamento ainda.</p>`}
      <button class="main-action secondary" data-sched-new>Novo agendamento</button>
    </section>
    ${recent ? `<section data-schedule-ui><h2>Últimas execuções</h2><div class="list">${recent}</div>
      <button class="link danger" data-clear-history>${state.confirmClear ? "Toque de novo para apagar o histórico" : "Apagar histórico"}</button>
    </section>` : ""}`;
}

const DEFAULT_DURATION_H = 3;
const DEFAULT_LEAD_MIN = 10;

const hhmm = (date) => date.toTimeString().slice(0, 5);
const localDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

function plusHours(time, hours) {
  const [h, m] = time.split(":").map(Number);
  const total = (h * 60 + m + hours * 60) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function openForm(id) {
  if (id === "novo") {
    const first = state.devices[0];
    // Começa daqui a alguns minutos: dá tempo de ajustar sem disparar ao salvar.
    const start = new Date(Date.now() + DEFAULT_LEAD_MIN * 60000);
    state.form = {
      nome: "", aparelhos: first ? [first.id] : [], acao: "ligar", programa: "IOT_COOL",
      parametros: { tempSel: 23, screenDisplayStatus: "0" }, frequencia: "uma_vez", dias: [], data: localDate(start),
      inicio: hhmm(start), fim: plusHours(hhmm(start), DEFAULT_DURATION_H), ativo: true, confirmDelete: false,
    };
    state.form.original = JSON.stringify(formBody(state.form));
    return;
  }
  const s = state.schedules.find((x) => x.id === id);
  if (!s) return;
  state.form = {
    ...structuredClone(s), acao: s.programa ? "ligar" : "desligar", programa: s.programa || "IOT_COOL",
    data: s.data || localDate(new Date()), confirmDelete: false,
  };
  state.form.original = JSON.stringify(formBody(state.form));
}

const isDirty = () => !!state.form && JSON.stringify(formBody(state.form)) !== state.form.original;

// Capacidades comuns a todos os aparelhos escolhidos, no modo escolhido.
function formCaps(f) {
  const devices = f.aparelhos.map(find).filter(Boolean);
  if (!devices.length) return {};
  const [first, ...rest] = devices;
  const caps = { ...(first.programas[f.programa] || {}) };
  for (const d of rest) for (const k of Object.keys(caps)) if (!d.programas[f.programa]?.[k]) delete caps[k];
  return caps;
}

function formModes(f) {
  const devices = f.aparelhos.map(find).filter(Boolean);
  return Object.keys(MODES).filter((p) => devices.length && devices.every((d) => d.programas[p]));
}

function renderScheduleForm() {
  const f = state.form;
  const caps = formCaps(f);
  const p = f.parametros;
  const on = f.acao === "ligar";
  const showTemp = on && caps.tempSel && f.programa !== "IOT_FAN" && f.programa !== "IOT_10_HEATING";
  const t = num(p.tempSel, 23);
  const turbo = caps.rapidMode && p.rapidMode === "1";
  const silent = caps.muteStatus && p.muteStatus === "1";
  const fanLock = turbo || silent;
  const fanValues = caps.windSpeed?.valores || [];
  const toggles = TOGGLES.filter(([key]) => caps[key] && key !== "screenDisplayStatus" && key !== "healthMode");

  return `
  <div data-schedule-ui>
    <header class="top">
      <button class="icon-btn" data-back aria-label="Voltar">${ICON_BACK}</button>
      <h1>${f.id ? "Agendamento" : "Novo agendamento"}</h1>
    </header>

    <section>
      <h2>Aparelhos</h2>
      <div class="seg">
        ${state.devices.map((d) => `<button class="chip ${f.aparelhos.includes(d.id) ? "sel" : ""}" data-f-device="${esc(d.id)}">${esc(d.nome)}</button>`).join("")}
      </div>
    </section>

    <section>
      <h2>O que fazer</h2>
      <div class="seg two">
        <button class="chip ${on ? "sel" : ""}" data-f-acao="ligar">Ligar</button>
        <button class="chip ${on ? "" : "sel"}" data-f-acao="desligar">Só desligar</button>
      </div>
    </section>

    ${on ? `
    <section>
      <h2>Modo</h2>
      <div class="seg">
        ${formModes(f).map((m) => `<button class="chip ${m === f.programa ? "sel" : ""}" data-f-mode="${m}" style="--mode:${modeVar(m)}">${MODES[m].label}</button>`).join("")}
      </div>
    </section>
    ${showTemp ? `
    <div class="dial" style="margin-top:22px">
      <button class="step" data-f-temp="-1" aria-label="Diminuir" ${t <= caps.tempSel.min ? "disabled" : ""}>−</button>
      <div class="value"><div class="big">${deg(t)}</div><div class="caption">temperatura</div></div>
      <button class="step" data-f-temp="1" aria-label="Aumentar" ${t >= caps.tempSel.max ? "disabled" : ""}>+</button>
    </div>` : ""}
    ${fanValues.length ? `
    <section>
      <h2>Ventilador</h2>
      <div class="seg four">
        ${FAN.filter(([v]) => fanValues.includes(v)).map(([v, label]) => `
          <button class="chip ${String(p.windSpeed ?? "5") === v ? "sel" : ""}" data-f-fan="${v}" ${fanLock && v !== "5" ? "disabled" : ""}>${label}</button>`).join("")}
      </div>
      ${fanLock ? `<p class="note">Com o ${turbo ? "Turbo" : "Silencioso"} ligado o ventilador fica no automático.</p>` : ""}
    </section>` : ""}
    <section>
      <h2>Funções</h2>
      <div class="list">
        ${toggles.map(([key, label]) => `<label class="row"><span class="label">${label}</span>
            <span class="switch"><input type="checkbox" data-f-toggle="${key}" ${p[key] === "1" ? "checked" : ""}><span></span></span></label>`).join("")}
        ${caps.screenDisplayStatus ? `<label class="row"><span class="label">Luz do painel<br><span class="hint">O modo noturno não apaga a luz sozinho</span></span>
          <span class="switch"><input type="checkbox" data-f-light ${p.screenDisplayStatus === "0" ? "" : "checked"}><span></span></span></label>` : ""}
      </div>
    </section>` : ""}

    <section>
      <h2>Quando</h2>
      <div class="seg">
        ${FREQ.map(([v, label]) => `<button class="chip ${f.frequencia === v ? "sel" : ""}" data-f-freq="${v}">${label}</button>`).join("")}
      </div>
      ${f.frequencia === "semanal" ? `<div class="seg seven" style="margin-top:8px">
        ${DAYS.map((label, i) => `<button class="chip ${f.dias.includes(i) ? "sel" : ""}" data-f-day="${i}" aria-pressed="${f.dias.includes(i)}">${label}</button>`).join("")}
      </div>` : ""}
      <div class="list" style="margin-top:8px">
        ${f.frequencia === "uma_vez" ? `<label class="row"><span class="label">Data</span><input type="date" data-f-input="data" value="${esc(f.data)}"></label>` : ""}
        <label class="row"><span class="label">${on ? "Liga às" : "Desliga às"}</span><input type="time" data-f-input="inicio" value="${esc(f.inicio)}"></label>
        ${on ? `<label class="row"><span class="label">Desligar depois</span>
          <span class="switch"><input type="checkbox" data-f-has-end ${f.fim ? "checked" : ""}><span></span></span></label>
        ${f.fim ? `<label class="row"><span class="label">Desliga às</span><input type="time" data-f-input="fim" value="${esc(f.fim)}"></label>` : ""}` : ""}
      </div>
    </section>

    <section>
      <h2>Nome (opcional)</h2>
      <div class="list"><label class="row"><input class="wide" data-f-input="nome" placeholder="Ex.: Dormir" value="${esc(f.nome)}" maxlength="40"></label></div>
    </section>

    <div class="sticky-actions">
      ${f.confirmLeave ? `
        <p class="leave-msg">Você tem alterações não salvas.</p>
        <div class="seg">
          <button class="chip" data-f-stay>Continuar</button>
          <button class="chip danger" data-f-discard>Descartar</button>
          <button class="chip sel" data-f-save>Salvar</button>
        </div>` : `<button class="main-action" data-f-save>${isDirty() ? "Salvar alterações" : "Salvar"}</button>`}
    </div>
    ${f.id ? `
      <button class="main-action secondary" data-f-test>${on ? "Testar agora (liga de verdade)" : "Testar agora (desliga de verdade)"}</button>
      <button class="main-action off" data-f-delete>${f.confirmDelete ? "Toque de novo para excluir" : "Excluir agendamento"}</button>` : ""}
  </div>`;
}

function formBody(f) {
  const on = f.acao === "ligar";
  const caps = formCaps(f);
  // Só manda o que o modo aceita (o servidor recusaria o resto).
  const parametros = on ? Object.fromEntries(Object.entries(f.parametros).filter(([k]) => caps[k])) : {};
  return {
    nome: f.nome.trim(), aparelhos: f.aparelhos, programa: on ? f.programa : null, parametros,
    frequencia: f.frequencia, dias: f.dias, data: f.frequencia === "uma_vez" ? f.data : null,
    inicio: f.inicio, fim: on ? f.fim : null, ativo: f.ativo,
  };
}

async function scheduleClick(el, e) {
  const f = state.form;
  const d = el.dataset;
  if (d.schedOpen) return (location.hash = `agenda/${d.schedOpen}`);
  if ("schedNew" in d) return (location.hash = "agenda/novo");
  if ("clearHistory" in d) {
    if (!state.confirmClear) {
      state.confirmClear = true;
      setTimeout(() => { state.confirmClear = false; renderWhenIdle(); }, 4000);
      return render();
    }
    state.confirmClear = false;
    try {
      await api("/agendamentos/historico", undefined, "DELETE");
      state.history = [];
      toast("Histórico apagado");
    } catch (err) {
      toast(err.message);
    }
    return render();
  }
  if ("back" in d) {
    if (isDirty()) {
      f.confirmLeave = true;
      return render();
    }
    return (location.hash = "");
  }
  if (!f) return;
  if ("fStay" in d) {
    f.confirmLeave = false;
    return render();
  }
  if ("fDiscard" in d) {
    state.form = null;
    return (location.hash = "");
  }
  if (d.fDevice) {
    f.aparelhos = f.aparelhos.includes(d.fDevice) ? f.aparelhos.filter((x) => x !== d.fDevice) : [...f.aparelhos, d.fDevice];
    if (!formModes(f).includes(f.programa)) f.programa = formModes(f)[0] || "IOT_COOL";
  }
  if (d.fAcao) f.acao = d.fAcao;
  if (d.fMode) f.programa = d.fMode;
  if (d.fTemp) {
    const spec = formCaps(f).tempSel;
    f.parametros.tempSel = Math.min(spec.max, Math.max(spec.min, num(f.parametros.tempSel, 23) + +d.fTemp * spec.passo));
  }
  if (d.fFan) f.parametros.windSpeed = d.fFan;
  if (d.fFreq) f.frequencia = d.fFreq;
  if (d.fDay) {
    const day = +d.fDay;
    f.dias = f.dias.includes(day) ? f.dias.filter((x) => x !== day) : [...f.dias, day].sort();
  }
  if ("fSave" in d) {
    el.disabled = true;
    try {
      const body = formBody(f);
      await (f.id ? api(`/agendamentos/${f.id}`, body, "PUT") : api("/agendamentos", body));
      await loadSchedules();
      toast("Agendamento salvo");
      state.form = null;
      location.hash = "";
    } catch (err) {
      toast(err.message);
      el.disabled = false;
    }
    return;
  }
  if ("fTest" in d) {
    el.disabled = true;
    try {
      const { resultado } = await api(`/agendamentos/${f.id}/testar?acao=${f.acao}`, {});
      const erros = Object.entries(resultado).filter(([, r]) => r !== "ok");
      toast(erros.length ? erros.map(([mac, r]) => `${deviceName(mac)}: ${r}`).join("; ") : "Executado. Confira o aparelho.");
      refresh();
    } catch (err) {
      toast(err.message);
    }
    el.disabled = false;
    return;
  }
  if ("fDelete" in d) {
    if (!f.confirmDelete) {
      f.confirmDelete = true;
    } else {
      try {
        await api(`/agendamentos/${f.id}`, undefined, "DELETE");
        await loadSchedules();
        toast("Agendamento excluído");
        state.form = null;
        location.hash = "";
      } catch (err) {
        toast(err.message);
      }
      return;
    }
  }
  render();
}

async function scheduleChange(input) {
  const f = state.form;
  const d = input.dataset;
  if (d.schedActive) {
    const s = state.schedules.find((x) => x.id === d.schedActive);
    const body = { ...s, ativo: input.checked };
    for (const k of ["id", "descricao", "proximo"]) delete body[k];
    try {
      await api(`/agendamentos/${s.id}`, body, "PUT");
    } catch (err) {
      toast(err.message);
    }
    await loadSchedules();
    return render();
  }
  if (!f) return;
  if (d.fInput) {
    f[d.fInput] = input.value;
    return; // campos de texto/hora não re-renderizam (mantém o foco)
  }
  if (d.fToggle) {
    Object.assign(f.parametros, exclusive(d.fToggle, input.checked, formCaps(f)));
    // Turbo e Silencioso travam o ventilador no automático.
    if (EXCLUSIVE[d.fToggle] && input.checked) f.parametros.windSpeed = "5";
  }
  if ("fLight" in d) f.parametros.screenDisplayStatus = input.checked ? "1" : "0";
  if ("fHasEnd" in d) f.fim = input.checked ? plusHours(f.inicio, DEFAULT_DURATION_H) : null;
  render();
}
