"use strict";

const SOURCES = {
  bicimad: "BiciMAD",
  callejero: "Callejero",
  wikidata: "Wikidata",
  osrm: "OSRM",
  user: "Tú",
  local: "RDF local",
};
const GROUPS = { street: "Calles · Callejero", place: "Lugares · Wikidata", station: "Estaciones · BiciMAD" };
const SOL = [40.416775, -3.70379];
const EXAMPLES = ["Bravo Murillo 120", "Museo del Prado", "un parque", "Agustín de Foxá"];
const EXPLORE_EXAMPLES = ["Museo del Prado", "Alcalá", "un parque", "Metro Sol"];

// mode: "route" (origen y destino) o "explore" (estaciones cerca de un lugar, FR2).
// center es el lugar de Explorar y explore, la última respuesta de /api/explore.
const state = {
  mode: "route", from: null, to: null, plan: null, shown: null, whyOpen: false,
  center: null, explore: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const results = $("#results");

// ---------- utilidades ----------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function badge(source, text) {
  return `<span class="badge ${esc(source)}">${esc(text ?? SOURCES[source] ?? source)}</span>`;
}

function minutes(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 1) return "<1 min";
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function km(meters) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1).replace(".", ",")} km` : `${Math.round(meters / 10) * 10} m`;
}

function https(url) {
  return url ? url.replace(/^http:\/\//, "https://") : url;
}

function distance(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * rad) / 2) ** 2
    + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, value);
  }
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

function showStatus(text) {
  const status = $("#status");
  status.textContent = text;
  status.hidden = !text;
}

// ---------- mapa ----------

const map = L.map("map", { preferCanvas: true }).setView(SOL, 14);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const stationLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);

// Botones para usar un sitio: «Salir de aquí» e «Ir aquí» (modo Ruta) y, en Explorar, «Explorar aquí».
function actions(onChoose, extra = "") {
  const div = document.createElement("div");
  div.className = "popup-actions";
  const explore = state.mode === "explore" ? '<button type="button" data-act="explore">Explorar aquí</button>' : "";
  div.innerHTML = `${explore}<button type="button" data-act="from">Salir de aquí</button><button type="button" data-act="to">Ir aquí</button>${extra}`;
  div.addEventListener("click", (event) => {
    const act = event.target.dataset.act;
    if (!act) return;
    map.closePopup();
    onChoose(act);
  });
  return div;
}

// Filtros de estaciones: solo deciden qué marcadores se ven; el cálculo de rutas no los usa.
const stationMarkers = [];
const MIN_CAPACITY_OPTIONS = [10, 20, 25, 30];

const stationFilter = L.control({ position: "topright" });
stationFilter.onAdd = () => {
  const form = L.DomUtil.create("form", "station-filter");
  form.innerHTML = `<fieldset>
    <legend>Estaciones ${badge("bicimad")}</legend>
    <label><input type="checkbox" id="filter-usable"> Solo en servicio</label>
    <label>Mínimo de bases
      <select id="filter-capacity">
        <option value="0">Cualquiera</option>
        ${MIN_CAPACITY_OPTIONS.map((n) => `<option value="${n}">${n} o más</option>`).join("")}
      </select>
    </label>
    <p class="count" id="filter-count" aria-live="polite">Cargando estaciones…</p>
  </fieldset>`;
  L.DomEvent.disableClickPropagation(form);
  L.DomEvent.disableScrollPropagation(form);
  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("change", applyStationFilters);
  return form;
};
stationFilter.addTo(map);

function applyStationFilters() {
  const onlyUsable = $("#filter-usable").checked;
  const minCapacity = Number($("#filter-capacity").value);
  let visible = 0;
  for (const { station, marker } of stationMarkers) {
    if ((onlyUsable && !station.usable) || station.capacity < minCapacity) {
      stationLayer.removeLayer(marker);
    } else {
      stationLayer.addLayer(marker);
      visible += 1;
    }
  }
  $("#filter-count").textContent = `${visible} de ${stationMarkers.length} estaciones`;
}

async function loadStations() {
  const stations = await api("/api/stations");
  for (const s of stations) {
    const marker = L.circleMarker([s.lat, s.lon], {
      radius: 4.5,
      weight: 1,
      color: "#ffffff",
      fillColor: s.usable ? "#1a7f37" : "#9ca3af",
      fillOpacity: 0.95,
    })
      .bindPopup(() => {
        const div = document.createElement("div");
        div.innerHTML = `<strong>${esc(s.name)}</strong><br>${s.capacity} bases · ${s.usable ? "en servicio" : "fuera de servicio"}`;
        div.append(actions((act) => {
          if (act === "card") showStation(s.id);
          else usePlace(act, { ref: `station:${s.id}`, label: s.name });
        }, '<button type="button" data-act="card">Ver ficha</button>'));
        return div;
      });
    stationMarkers.push({ station: s, marker });
  }
  applyStationFilters();
}

map.on("click", (event) => {
  const { lat, lng } = event.latlng;
  const div = document.createElement("div");
  div.innerHTML = "<strong>Punto del mapa</strong>";
  const labels = { from: "Punto de salida", to: "Punto de llegada", explore: "Punto del mapa" };
  div.append(actions((act) => usePlace(act, {
    ref: `pt:${lat.toFixed(6)},${lng.toFixed(6)}`,
    label: labels[act],
    lat,
    lon: lng,
  })));
  L.popup().setLatLng(event.latlng).setContent(div).openOn(map);
});

function pin(latlng, letter, role, label) {
  L.marker(latlng, {
    icon: L.divIcon({ className: "", html: `<div class="pin ${role}">${letter}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] }),
    zIndexOffset: 1000,
  }).bindTooltip(esc(label)).addTo(routeLayer);
}

function drawPlan() {
  routeLayer.clearLayers();
  const plan = state.plan;
  const showBike = state.shown === "bike" && plan.bike;
  const bounds = [];

  const draw = (leg, faded) => {
    const style = leg.mode === "bike"
      ? { color: "#d0112b", weight: 5, opacity: faded ? 0.25 : 0.9 }
      : { color: "#374151", weight: 4, dashArray: "1 8", lineCap: "round", opacity: faded ? 0.3 : 0.95 };
    L.polyline(leg.geometry, style).addTo(routeLayer);
    if (!faded) bounds.push(...leg.geometry);
  };
  if (plan.bike) plan.bike.legs.forEach((leg) => draw(leg, !showBike));
  draw({ ...plan.walk, mode: "walk" }, showBike);

  if (showBike) {
    for (const s of [plan.bike.from_station, plan.bike.to_station]) {
      L.circleMarker([s.lat, s.lon], { radius: 8, color: "#d0112b", weight: 3, fillColor: "#ffffff", fillOpacity: 1 })
        .bindTooltip(esc(s.name))
        .addTo(routeLayer);
    }
  }
  const legs = showBike ? plan.bike.legs : [plan.walk];
  const start = plan.origin.fixedStations ? legs[0].geometry[0] : [plan.origin.lat, plan.origin.lon];
  const end = plan.destination.fixedStations ? legs.at(-1).geometry.at(-1) : [plan.destination.lat, plan.destination.lon];
  pin(start, "A", "from", plan.origin.label);
  pin(end, "B", "to", plan.destination.label);
  bounds.push(start, end);
  map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40] });
}

// ---------- modos: Ruta y Explorar ----------

const tabs = [...document.querySelectorAll('.tabs [role="tab"]')];

function setMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  for (const tab of tabs) {
    const selected = tab.dataset.mode === mode;
    tab.setAttribute("aria-selected", selected);
    tab.tabIndex = selected ? 0 : -1;
    $(`#${tab.getAttribute("aria-controls")}`).hidden = !selected;
  }
  map.closePopup();
  if (mode === "explore") {
    routeLayer.clearLayers();
    if (state.explore) renderExplore();
    else exploreWelcome();
  } else if (state.plan) {
    renderPlan();
    drawPlan();
  } else {
    welcome();
  }
}

tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
  // Patrón de pestañas: las flechas pasan de una a otra.
  tab.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!step) return;
    const next = tabs[(i + step + tabs.length) % tabs.length];
    next.focus();
    setMode(next.dataset.mode);
  });
});

// Usar un sitio elegido en el mapa o en una ficha: como origen, destino o centro de Explorar.
function usePlace(act, place) {
  if (act === "explore") setCenter(place);
  else setEndpoint(act, place);
}

// ---------- origen y destino ----------

function setEndpoint(role, value) {
  setMode("route");
  state[role] = value;
  $(`#${role}-input`).value = value.label;
  if (state.from && state.to) computeRoute();
}

// Campo con autocompletado (/api/search). Al elegir una opción se llama a onChoose; por defecto,
// la opción pasa a ser el origen o el destino.
function setupField(role, onChoose = (item) => setEndpoint(role, { ref: item.ref, label: item.label })) {
  const field = $(`.field[data-role="${role}"]`);
  const input = $("input", field);
  const list = $(".suggestions", field);
  let items = [];
  let active = -1;
  let timer;
  let sequence = 0;

  const close = () => { list.hidden = true; };

  const render = () => {
    if (!items.length) {
      list.innerHTML = '<li class="empty">Sin resultados</li>';
      list.hidden = false;
      return;
    }
    const seen = new Set();
    let html = "";
    let last = null;
    items.forEach((item, i) => {
      if (item.kind !== last) {
        html += `<li class="group">${seen.has(item.kind) ? "Más " : ""}${GROUPS[item.kind]}</li>`;
        seen.add(item.kind);
        last = item.kind;
      }
      html += `<li class="option" role="option" data-i="${i}" aria-selected="${i === active}">
        <strong>${esc(item.label)}</strong><span>${esc(item.detail)}</span></li>`;
    });
    list.innerHTML = html;
    list.hidden = false;
  };

  const suggest = async () => {
    const q = input.value.trim();
    if (q.length < 2) return close();
    const mine = ++sequence;
    const near = role === "to" && state.from && state.from.lat !== undefined ? state.from : null;
    try {
      const data = await api("/api/search", { q, lat: near?.lat, lon: near?.lon });
      if (mine !== sequence) return;
      items = data.results;
      active = -1;
      render();
    } catch (error) {
      list.innerHTML = `<li class="empty error">${esc(error.message)}</li>`;
      list.hidden = false;
    }
  };

  const choose = (item) => {
    close();
    onChoose(item);
  };

  input.addEventListener("input", () => {
    state[role] = null;
    clearTimeout(timer);
    timer = setTimeout(suggest, 200);
  });
  input.addEventListener("focus", () => { if (input.value.trim().length >= 2 && !state[role]) suggest(); });
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (event) => {
    if (list.hidden || !items.length) return;
    if (event.key === "ArrowDown") active = Math.min(active + 1, items.length - 1);
    else if (event.key === "ArrowUp") active = Math.max(active - 1, 0);
    else if (event.key === "Enter") return choose(items[Math.max(active, 0)]);
    else if (event.key === "Escape") return close();
    else return;
    event.preventDefault();
    render();
  });
  list.addEventListener("mousedown", (event) => {
    const option = event.target.closest(".option");
    if (!option) return;
    event.preventDefault();
    choose(items[Number(option.dataset.i)]);
  });
}

$("#locate").addEventListener("click", () => {
  if (!navigator.geolocation) return showStatus("Tu navegador no permite obtener la ubicación.");
  showStatus("Buscando tu ubicación…");
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const { latitude: lat, longitude: lon } = position.coords;
      if (distance(lat, lon, ...SOL) > 20000) {
        return showStatus("Tu ubicación está fuera de Madrid: haz clic en el mapa para elegir el origen.");
      }
      showStatus("");
      setEndpoint("from", { ref: `pt:${lat.toFixed(6)},${lon.toFixed(6)}`, label: "Tu ubicación", lat, lon });
    },
    () => showStatus("No se ha podido obtener tu ubicación: haz clic en el mapa para elegir el origen."),
    { enableHighAccuracy: true, timeout: 10000 },
  );
});

$("#swap").addEventListener("click", () => {
  [state.from, state.to] = [state.to, state.from];
  $("#from-input").value = state.from?.label ?? "";
  $("#to-input").value = state.to?.label ?? "";
  if (state.from && state.to) computeRoute();
});

// ---------- resultados ----------

function optionCard(mode, title, option, recommended) {
  return `<button type="button" class="option-card ${state.shown === mode ? "active" : ""}" data-mode="${mode}">
    <div class="mode">${title}</div>
    <div class="time">${minutes(option.duration)}</div>
    <div class="meta">${km(option.distance)}${option.estimated ? ` · ${badge("warn", "estimado")}` : ""}</div>
    ${recommended ? '<span class="best">Más rápido</span>' : ""}
  </button>`;
}

function endpointLine(role, endpoint) {
  return `<div class="endpoint"><b>${role}</b><div>
    <strong>${esc(endpoint.label)}</strong> ${badge(endpoint.source)}
    ${endpoint.approximate ? badge("warn", "aproximado") : ""}
    <p>${esc(endpoint.note)}</p></div></div>`;
}

function stepsCard(bike) {
  const steps = [];
  for (const leg of bike.legs) {
    const estimated = leg.estimated ? " · estimado" : "";
    if (leg.mode === "walk") {
      steps.push(`<li class="walk">Camina ${minutes(leg.duration)} hasta <strong>${esc(leg.to)}</strong>
        <span class="sub">${km(leg.distance)}${estimated}</span></li>`);
    } else {
      steps.push(`<li class="dock">Coge una bici en <strong>${esc(leg.from)}</strong><span class="sub">1 min para desanclarla</span></li>`);
      steps.push(`<li class="bike">Pedalea ${minutes(leg.duration)} hasta <strong>${esc(leg.to)}</strong>
        <span class="sub">${km(leg.distance)}${estimated}</span></li>`);
      steps.push(`<li class="dock">Deja la bici en <strong>${esc(leg.to)}</strong><span class="sub">1 min para anclarla</span></li>`);
    }
  }
  return `<div class="card"><h2>Itinerario ${badge("osrm")}</h2><ol class="steps">${steps.join("")}</ol></div>`;
}

function walkCard(plan) {
  const faster = plan.recommended === "walk" && plan.bike
    ? '<p class="sub">Para este trayecto es más rápido ir andando que coger y dejar una bici.</p>'
    : "";
  return `<div class="card"><h2>Itinerario ${badge("osrm")}</h2><ol class="steps">
    <li class="walk">Camina ${minutes(plan.walk.duration)} hasta <strong>${esc(plan.destination.label)}</strong>
    <span class="sub">${km(plan.walk.distance)}${plan.walk.estimated ? " · estimado" : ""}</span></li></ol>${faster}</div>`;
}

function stationCard(card, title) {
  let html = `<div class="card"><h2>${esc(title)}: ${esc(card.name)}</h2>`;
  for (const block of card.blocks) {
    html += `<div class="block ${esc(block.source)}"><div class="head"><span>${esc(block.title)}</span>${badge(block.source)}</div>`;
    if (block.items) {
      html += `<dl>${block.items.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>`;
    }
    if (block.description) html += `<p>${esc(block.description)}</p>`;
    if (block.image) html += `<img src="${esc(https(block.image))}" alt="${esc(block.title)}" loading="lazy">`;
    for (const person of block.namedAfter ?? []) {
      html += `<div class="person">${person.image ? `<img src="${esc(https(person.image))}" alt="" loading="lazy">` : ""}
        <div>Homenajea a <a href="${esc(person.link)}" target="_blank" rel="noopener">${esc(person.label)}</a>
        ${person.description ? `<br><span class="sub">${esc(person.description)}</span>` : ""}</div></div>`;
    }
    if (block.link) html += `<p><a href="${esc(block.link)}" target="_blank" rel="noopener">Ver en Wikidata ↗</a></p>`;
    html += "</div>";
  }
  return `${html}</div>`;
}

function nearbyCard(places, title) {
  const items = places.map((p) => `<li><a href="${esc(p.link)}" target="_blank" rel="noopener">${esc(p.label)}</a>
    <span>${esc(p.categories.join(", "))} · ${km(p.distance)}</span></li>`);
  return `<div class="card"><h2>${esc(title)} ${badge("wikidata")}</h2><ul class="places">${items.join("")}</ul></div>`;
}

function nearestStationsCard(stations, title = "Estaciones más cercanas") {
  const items = stations.map((s) => `<li><a href="#" data-station="${esc(s.id)}">${esc(s.name)}</a>
    <span>${s.usable ? "" : `${badge("muted", "fuera de servicio")} · `}${s.capacity} bases · ${km(s.distance)}</span></li>`);
  return `<div class="card"><h2>${esc(title)} ${badge("bicimad")}</h2><ul class="places">${items.join("")}</ul></div>`;
}

// Los enlaces de las listas de estaciones abren la ficha de esa estación.
function bindStationLinks() {
  results.querySelectorAll("[data-station]").forEach((link) => link.addEventListener("click", (event) => {
    event.preventDefault();
    showStation(link.dataset.station);
  }));
}

function verdictClass(text) {
  if (text.startsWith("No")) return "no";
  if (text.startsWith("Sí")) return "yes";
  return "";
}

function whyPanel(contributions, queries) {
  let html = `<details class="why" ${state.whyOpen ? "open" : ""}><summary>¿Por qué ontología?</summary><div>`;
  if (contributions) {
    html += `<p class="sub">Qué aporta cada fuente a esta respuesta y qué se podría hacer solo con el CSV de BiciMAD.</p>
      <table><thead><tr><th>Dato</th><th>Fuente</th><th>Solo con el CSV</th></tr></thead><tbody>
      ${contributions.map((c) => `<tr><td>${esc(c.what)}<br><span class="sub">${esc(c.how)}</span></td>
        <td>${badge(c.source)}</td><td class="${verdictClass(c.csv)}">${esc(c.csv)}</td></tr>`).join("")}
      </tbody></table>`;
  }
  html += `<h3>Consultas ejecutadas (${queries.length})</h3>`;
  for (const q of queries) {
    const meta = [`${q.rows ?? 0} filas`, `${q.ms} ms`];
    if (q.cached) meta.push("caché");
    if (q.error) meta.push("error → estimación");
    html += `<details class="query"><summary>${badge(q.source)}<code>${esc(q.name)}</code>
      <span class="meta">${meta.join(" · ")}</span></summary>
      <pre>${esc(q.text)}${q.error ? `\n\n# ${esc(q.error)}` : ""}</pre></details>`;
  }
  return `${html}</div></details>`;
}

function bindWhy() {
  const why = $(".why", results);
  if (why) why.addEventListener("toggle", () => { state.whyOpen = why.open; });
}

function renderPlan() {
  const plan = state.plan;
  let html = '<div class="options">';
  if (plan.bike) html += optionCard("bike", "En BiciMAD", plan.bike, plan.recommended === "bike");
  html += optionCard("walk", "Andando", plan.walk, plan.recommended === "walk");
  html += "</div>";
  html += `<div class="card">${endpointLine("Desde", plan.origin)}${endpointLine("Hasta", plan.destination)}</div>`;
  html += state.shown === "bike" && plan.bike ? stepsCard(plan.bike) : walkCard(plan);
  plan.cards.forEach((card, i) => {
    html += stationCard(card, i ? "Llegada" : "Salida");
    if (card.nearestStations?.length) {
      html += nearestStationsCard(card.nearestStations, i ? "Alternativas a la llegada" : "Alternativas a la salida");
    }
  });
  if (plan.nearby.length) html += nearbyCard(plan.nearby, "Cerca del destino");
  html += whyPanel(plan.contributions, plan.queries);
  results.innerHTML = html;
  results.querySelectorAll(".option-card").forEach((button) => button.addEventListener("click", () => {
    state.shown = button.dataset.mode;
    renderPlan();
    drawPlan();
  }));
  bindStationLinks();
  bindWhy();
}

async function computeRoute() {
  results.innerHTML = '<p class="loading">Calculando la ruta…</p>';
  try {
    const plan = await api("/api/route", {
      from: state.from.ref,
      to: state.to.ref,
      fromLabel: state.from.label,
      toLabel: state.to.label,
    });
    state.plan = plan;
    state.shown = plan.recommended;
    state.from.lat = plan.origin.lat;
    state.from.lon = plan.origin.lon;
    renderPlan();
    drawPlan();
  } catch (error) {
    routeLayer.clearLayers();
    results.innerHTML = `<p class="error">${esc(error.message)}</p>`;
  }
}

async function showStation(id) {
  results.innerHTML = '<p class="loading">Cargando la estación…</p>';
  try {
    const data = await api(`/api/station/${encodeURIComponent(id)}`);
    const s = data.station;
    let html = state.plan ? '<button type="button" class="back">← Volver a la ruta</button>' : "";
    html += stationCard(s, "Estación");
    html += '<div class="card" id="station-actions"></div>';
    if (data.nearestStations.length) html += nearestStationsCard(data.nearestStations);
    if (data.nearby.length) html += nearbyCard(data.nearby, "Lugares cercanos");
    html += whyPanel(null, data.queries);
    results.innerHTML = html;
    $("#station-actions").append(actions((act) => usePlace(act, { ref: `station:${s.id}`, label: s.name })));
    bindStationLinks();
    $(".back", results)?.addEventListener("click", () => { renderPlan(); drawPlan(); });
    bindWhy();
    map.setView([s.lat, s.lon], Math.max(map.getZoom(), 16));
  } catch (error) {
    results.innerHTML = `<p class="error">${esc(error.message)}</p>`;
  }
}

function exampleButtons(examples) {
  return `<div class="popup-actions">${examples.map((e) => `<button type="button" data-example="${esc(e)}">${esc(e)}</button>`).join("")}</div>`;
}

function welcome() {
  results.innerHTML = `<div class="card"><h2>¿A dónde vas?</h2>
    <p class="sub">Elige el origen con tu ubicación o haciendo clic en el mapa, y escribe el destino:
    una calle (el número se ignora), un lugar, un tipo de lugar o una estación.</p>
    <p class="sub">Ejemplos desde la Puerta del Sol:</p>${exampleButtons(EXAMPLES)}</div>`;
}

function exploreWelcome() {
  results.innerHTML = `<div class="card"><h2>¿Qué estaciones hay cerca?</h2>
    <p class="sub">Escribe una calle, un lugar, un tipo de lugar o una estación, o haz clic en el mapa,
    y verás las estaciones BiciMAD que tiene alrededor, de la más cercana a la más lejana.</p>
    <p class="sub">Ejemplos:</p>${exampleButtons(EXPLORE_EXAMPLES)}</div>`;
}

// Los ejemplos escriben en el campo del modo activo y muestran sus sugerencias.
results.addEventListener("click", (event) => {
  const example = event.target.dataset.example;
  if (!example) return;
  let input = $("#center-input");
  if (state.mode === "route") {
    state.from = { ref: `pt:${SOL[0]},${SOL[1]}`, label: "Puerta del Sol", lat: SOL[0], lon: SOL[1] };
    $("#from-input").value = state.from.label;
    input = $("#to-input");
  }
  input.value = example;
  input.focus();
  input.dispatchEvent(new Event("input"));
});

// ---------- Explorar ----------

let exploreSequence = 0;

function setCenter(place) {
  setMode("explore");
  state.center = place;
  $("#center-input").value = place.label;
  exploreAround();
}

async function exploreAround() {
  const mine = ++exploreSequence;
  results.innerHTML = '<p class="loading">Buscando estaciones…</p>';
  try {
    const data = await api("/api/explore", { ref: state.center.ref, label: state.center.label });
    if (mine !== exploreSequence) return;
    state.explore = data;
    if (state.mode === "explore") renderExplore();
  } catch (error) {
    if (mine !== exploreSequence) return;
    state.explore = null;
    results.innerHTML = `<p class="error">${esc(error.message)}</p>`;
  }
}

function centerCard(center) {
  return `<div class="card center-card"><p class="kicker">Lugar</p>
    <h2>${esc(center.label)} ${badge(center.source)} ${center.approximate ? badge("warn", "aproximado") : ""}</h2>
    <p class="sub">${esc(center.note)}</p></div>`;
}

function renderExplore() {
  const data = state.explore;
  results.innerHTML = centerCard(data.center) + whyPanel(data.contributions, data.queries);
  bindWhy();
}

async function checkStatus() {
  try {
    const status = await api("/api/status");
    if (status.places === "ready") return showStatus("");
    if (status.places === "error") {
      return showStatus("No se han podido descargar los lugares de Wikidata: la búsqueda de lugares no está disponible.");
    }
    showStatus("Descargando lugares de Wikidata…");
    setTimeout(checkStatus, 3000);
  } catch {
    setTimeout(checkStatus, 3000);
  }
}

setupField("from");
setupField("to");
setupField("center", (item) => setCenter({ ref: item.ref, label: item.label }));
welcome();
loadStations();
checkStatus();
