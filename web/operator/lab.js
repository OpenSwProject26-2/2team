/* routegen 노선 실험실. 데이터는 /api/lab/* 에서만 읽는다. */
const $ = (s) => document.querySelector(s);
const VARIANT_LABEL = {affected: '흡수 제외 없음', affected_maeul: '마을버스 흡수 제외', affected_gg: '경기버스 흡수 제외', affected_maeul_gg: '마을·경기 흡수 제외'};
const LAYERS = [
  ['busRoads', '버스 통행 가능 도로(=1)', '--road'], ['strike', '파업 노선', '--strike'], ['maeul', '마을버스', '--maeul'],
  ['gg', '경기버스', '--gg'], ['demand', '잔여 수요', '--strike', true], ['stations', '역', '--ink', true], ['plan', '셔틀 안', '--plan'],
];
const S = {net: 'orig', fleet: null, visible: new Set(['busRoads', 'strike', 'demand', 'stations', 'plan']), live: null, hover: null};
const D = {};
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (x, d = 0) => x == null ? '—' : Number(x).toLocaleString('ko-KR', {maximumFractionDigits: d, minimumFractionDigits: d});
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3500); }
async function get(p) { const r = await fetch(p); const d = await r.json(); if (!r.ok) throw new Error(d.message || r.status); return d; }
async function post(p, body) { const r = await fetch(p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); const d = await r.json(); if (!r.ok) throw new Error(d.message || r.status); return d; }

function variant() { const m = $('#noMaeul').checked, g = $('#noGg').checked; return m && g ? 'affected_maeul_gg' : m ? 'affected_maeul' : g ? 'affected_gg' : 'affected'; }
function precomputed() { return D.results.find((r) => r.net === S.net && r.variant === variant() && r.fleet === S.fleet); }
function shown() { return S.live && S.live.net === S.net && S.live.variant === variant() ? S.live : precomputed(); }

function seg(id, items, cur, on) {
  const el = $(id);
  el.innerHTML = items.map(([v, l]) => `<button type="button" aria-pressed="${String(v) === String(cur)}" data-v="${v}">${l}</button>`).join('');
  el.onclick = (e) => { const b = e.target.closest('button'); if (b) on(b.dataset.v); };
}

function fc(features) { return {type: 'FeatureCollection', features}; }
function line(coords, props = {}, id) { return {type: 'Feature', id, properties: props, geometry: {type: 'LineString', coordinates: coords}}; }

function demandFeatures() {
  const v = variant(), cur = D.demand[v], all = D.demand.affected, out = [];
  for (const [sid, val] of Object.entries(all)) {
    const s = D.stops[sid]; if (!s) continue;
    const now = cur[sid] || 0;
    out.push({type: 'Feature', properties: {name: s[2], now, removed: Math.max(0, val - now)}, geometry: {type: 'Point', coordinates: [s[0], s[1]]}});
  }
  return fc(out);
}
function planFeatures() {
  const p = shown(); if (!p) return fc([]);
  return fc(p.routes.map((r, i) => line(r.coords, {i, id: r.id, live: p === S.live ? 1 : 0}, i + 1)));
}

function render() {
  const v = variant();
  seg('#netSeg', [['orig', '원래 망'], ['road', '도로 망']], S.net, (x) => { S.net = x; render(); });
  seg('#fleetSeg', D.meta.fleets.map((f) => [f, `${f}대`]), S.fleet, (x) => { S.fleet = Number(x); $('#pF').value = x; render(); });
  const tot = Object.values(D.demand[v]).reduce((a, b) => a + b, 0), all = Object.values(D.demand.affected).reduce((a, b) => a + b, 0);
  $('#demandHint').textContent = `셔틀 대상 하루 ${fmt(tot)}명${v !== 'affected' ? ` (흡수로 ${fmt(all - tot)}명 제외)` : ''} · ${VARIANT_LABEL[v]}`;
  const p = shown(), isLive = p && p === S.live;
  $('#planTitle').innerHTML = p ? `${isLive ? '<span class="tag live">직접 실행</span>' : '<span class="tag">사전 계산</span>'} ${S.net === 'road' ? '도로 망' : '원래 망'} · ${VARIANT_LABEL[v]} · ${p.fleet}대` : '안 없음';
  const sim = isLive ? S.live.sim : p?.exp;
  const cards = [];
  if (isLive) {
    cards.push(['하루 시뮬레이션', S.live.sim?.rate != null ? `${S.live.sim.rate.toFixed(2)}%` : S.live.sim?.reason ? '실패' : '미실행', S.live.sim?.date ? `${S.live.sim.date} · 충족 ${fmt(S.live.sim.served)}명` : '아래 버튼으로 실행']);
    cards.push(['근사 충족(하루)', fmt(p.approx), `${p.seconds}초 · 후보 ${p.candidates}개`]);
  } else if (p) {
    cards.push(['충족률 · 실험', p.exp?.rate != null ? `${p.exp.rate.toFixed(2)}%` : '—', p.exp ? `5일 평균 ${fmt(p.exp.served)}명` : '']);
    cards.push(['충족률 · 테스트', p.test?.rate != null ? `${p.test.rate.toFixed(2)}%` : '—', p.test ? `5일 평균 ${fmt(p.test.served)}명` : '']);
  }
  if (p) {
    cards.push(['후보 풀이 덮는 수요', `${(100 * p.pool_cover / p.demand_total).toFixed(1)}%`, `${fmt(p.pool_cover)} / ${fmt(p.demand_total)}명`]);
    cards.push(['노선 수', fmt(p.routes.length), `근사 ${fmt(p.approx)}명`]);
  }
  $('#cards').innerHTML = cards.map(([b, s, sm]) => `<div class="card"><b>${b}</b><span>${s}</span><small>${sm}</small></div>`).join('');
  const un = sim?.unserved;
  $('#reasonDays').textContent = sim ? (isLive ? `· ${sim.date ?? ''} 하루` : '· 실험 5일 평균') : '';
  $('#reasons').innerHTML = un ? [['노선 없음', un.no_connection], ['운행 종료', un.service_end], ['대기 초과', un.wait], ['통행 초과', un.journey], ['만차 거절(사건)', sim.refusals]]
    .map(([k, x]) => `<span>${k}</span><span>${fmt(x)}</span>`).join('') : '<span class="hint">시뮬레이션 결과 없음</span>';
  $('#routes').innerHTML = p ? p.routes.map((r, i) => `<tr data-i="${i}"><td>${r.id}</td><td>${r.kind === 'access' ? '→ ' : r.kind === 'egress' ? '← ' : '↔ '}${esc(r.hubs.join('·'))}</td><td class="n">${r.vehicles}</td><td class="n">${Math.round(r.cycle_s / 60)}분</td><td class="n">${fmt(r.approx)}</td></tr>`).join('') : '';
  $('#runSim').disabled = !p;
  $('#sub').textContent = `${D.meta.gu} · 망 ${D.meta.network_snapshot_id} · 도로 구간 +${fmt(D.meta.roadnet.added_segments)}개(버스 도로 기준)`;
  updateMap();
}

function updateMap() {
  const m = D.map; if (!m || !m.getSource('demand')) return;
  m.getSource('demand').setData(demandFeatures());
  m.getSource('plan').setData(planFeatures());
  const live = shown() === S.live;
  m.setPaintProperty('plan', 'line-color', live ? css('--live') : css('--plan'));
  const vis = {busRoads: ['busRoads'], strike: ['strike'], maeul: ['maeul'], gg: ['gg'], demand: ['demand-now', 'demand-removed'], stations: ['stations', 'station-labels'], plan: ['plan', 'plan-casing']};
  for (const [k, ids] of Object.entries(vis)) for (const id of ids) m.setLayoutProperty(id, 'visibility', S.visible.has(k) ? 'visible' : 'none');
  $('#status').textContent = shown() ? `${live ? '직접 실행 안' : '사전 계산 안'} 표시 중 · 노선 ${shown().routes.length}개` : '이 조합의 안이 없음';
}

function focus(i) {
  const m = D.map; if (!m) return;
  m.setPaintProperty('plan', 'line-opacity', i == null ? 0.9 : ['case', ['==', ['get', 'i'], i], 1, 0.15]);
  m.setPaintProperty('plan', 'line-width', i == null ? 3.5 : ['case', ['==', ['get', 'i'], i], 6, 3]);
  document.querySelectorAll('#routes tr').forEach((tr) => tr.classList.toggle('on', Number(tr.dataset.i) === i));
}

async function initMap() {
  const maplibregl = await import('https://unpkg.com/maplibre-gl@6.18.0/dist/maplibre-gl.mjs');
  const [w, s, e, n] = D.meta.bbox;
  const m = new maplibregl.Map({container: 'mapc', style: 'https://tiles.openfreemap.org/styles/liberty', bounds: [[w, s], [e, n]], fitBoundsOptions: {padding: 30}, attributionControl: {compact: true},
    localIdeographFontFamily: "'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans CJK KR', 'Noto Sans KR', sans-serif"});
  D.map = m;
  m.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-left');
  await new Promise((r) => m.on('load', r));
  m.addSource('strike', {type: 'geojson', data: fc(D.strike.map((r) => line(r.coords, {name: r.name})))});
  m.addLayer({id: 'strike', type: 'line', source: 'strike', paint: {'line-color': css('--strike'), 'line-width': 3, 'line-opacity': 0.18}});
  m.addSource('busRoads', {type: 'geojson', data: fc([D.busRoads])});
  m.addLayer({id: 'busRoads', type: 'line', source: 'busRoads', paint: {'line-color': css('--road'), 'line-width': 1.4, 'line-opacity': 0.75}});
  for (const mode of ['maeul', 'gg']) {
    m.addSource(mode, {type: 'geojson', data: fc(D.absorbing.filter((r) => r.mode === mode && r.coords.length).map((r) => line(r.coords, {name: r.name})))});
    m.addLayer({id: mode, type: 'line', source: mode, paint: {'line-color': css('--' + mode), 'line-width': 2, 'line-opacity': 0.7}});
  }
  m.addSource('demand', {type: 'geojson', data: demandFeatures()});
  m.addLayer({id: 'demand-removed', type: 'circle', source: 'demand', filter: ['>', ['get', 'removed'], 0.5],
    paint: {'circle-radius': ['interpolate', ['linear'], ['sqrt', ['+', ['get', 'now'], ['get', 'removed']]], 0, 0, 20, 16], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#8a94a3', 'circle-stroke-width': 1.2}});
  m.addLayer({id: 'demand-now', type: 'circle', source: 'demand', filter: ['>', ['get', 'now'], 0.5],
    paint: {'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'now']], 0, 0, 20, 16], 'circle-color': '#e3508e', 'circle-opacity': 0.35, 'circle-stroke-color': '#c2306f', 'circle-stroke-width': 0.6}});
  m.addSource('plan', {type: 'geojson', data: planFeatures()});
  m.addLayer({id: 'plan-casing', type: 'line', source: 'plan', paint: {'line-color': '#ffffff', 'line-width': 6.5, 'line-opacity': 0.85}, layout: {'line-cap': 'round', 'line-join': 'round'}});
  m.addLayer({id: 'plan', type: 'line', source: 'plan', paint: {'line-color': css('--plan'), 'line-width': 3.5, 'line-opacity': 0.9}, layout: {'line-cap': 'round', 'line-join': 'round'}});
  m.addSource('stations', {type: 'geojson', data: fc(D.stations.map((t) => ({type: 'Feature', properties: {name: t.name, d: t.demand.affected}, geometry: {type: 'Point', coordinates: [t.lon, t.lat]}})))});
  m.addLayer({id: 'stations', type: 'circle', source: 'stations', paint: {'circle-radius': 5, 'circle-color': '#ffffff', 'circle-stroke-color': '#17202c', 'circle-stroke-width': 2}});
  m.addLayer({id: 'station-labels', type: 'symbol', source: 'stations', layout: {'text-field': ['get', 'name'], 'text-size': 12, 'text-offset': [0, 1.1], 'text-font': ['Noto Sans Regular']}, paint: {'text-color': '#17202c', 'text-halo-color': '#ffffff', 'text-halo-width': 1.6}});
  const popup = new maplibregl.Popup({closeButton: false, closeOnClick: false});
  m.on('mousemove', 'plan', (e) => {
    const i = e.features[0].properties.i, r = shown().routes[i];
    focus(i);
    popup.setLngLat(e.lngLat).setHTML(`<b>${r.id}</b> ${r.kind === 'access' ? '→' : '←'} ${esc(r.hubs.join('·'))}<br>${r.vehicles}대 · 주기 ${Math.round(r.cycle_s / 60)}분 · 정류장 ${r.stops.length}개<br>근사 ${fmt(r.approx)}명/일`).addTo(m);
  });
  m.on('mouseleave', 'plan', () => { focus(null); popup.remove(); });
  m.on('mousemove', 'demand-now', (e) => { if (m.queryRenderedFeatures(e.point, {layers: ['plan']}).length) return; const p = e.features[0].properties; popup.setLngLat(e.lngLat).setHTML(`${esc(p.name)}<br>잔여 수요 ${fmt(p.now, 1)}명/일${p.removed > 0.5 ? ` · 흡수 제외 ${fmt(p.removed, 1)}명` : ''}`).addTo(m); });
  m.on('mouseleave', 'demand-now', () => popup.remove());
  updateMap();
}

function layersPanel() {
  $('#layers').innerHTML = LAYERS.map(([k, l, c, dot]) => `<label class="chk"><input type="checkbox" data-k="${k}" ${S.visible.has(k) ? 'checked' : ''}><i class="${dot ? 'dot' : ''}" style="background:var(${c})"></i>${l}</label>`).join('');
  $('#layers').onchange = (e) => { const k = e.target.dataset.k; e.target.checked ? S.visible.add(k) : S.visible.delete(k); updateMap(); };
}

async function runGen() {
  const b = $('#runGen'); b.disabled = true; b.textContent = '생성 중… (수 초~수십 초)';
  try {
    const params = {radius_m: Number($('#pR').value) * 1000, max_stops: $('#pN').value === 'none' ? null : Number($('#pN').value), max_detour: Number($('#pD').value), l_max_min: Number($('#pL').value)};
    const res = await post('/api/lab/generate', {net: S.net, variant: variant(), fleet: Number($('#pF').value), params});
    S.live = {...res, sim: null};
    toast(`노선 ${res.routes.length}개 · 근사 ${fmt(res.approx)}명 · ${res.seconds}초`);
    render();
  } catch (error) { toast(`생성 실패: ${error.message}`); }
  finally { b.disabled = false; b.textContent = '노선 생성'; }
}
async function runSim() {
  const p = shown(); if (!p) return;
  const b = $('#runSim'); b.disabled = true; b.textContent = '시뮬레이션 중… (10~30초)';
  try {
    const res = await post('/api/lab/simulate', {net: S.net, variant: variant(), fleet: p.fleet, date: $('#simDate').value, routes: p.routes.map((r) => ({stops: r.stops, vehicles: r.vehicles}))});
    if (res.verify_pass !== true) { toast(`실행 실패: ${res.reason || res.stage_failed}`); return; }
    if (p === S.live) S.live.sim = res;
    toast(`${res.date} 충족률 ${res.rate.toFixed(2)}% (충족 ${fmt(res.served)} / ${fmt(res.total)}명) · M07 pass · ${res.seconds}초`);
    render();
  } catch (error) { toast(`시뮬레이션 실패: ${error.message}`); }
  finally { b.disabled = false; b.textContent = '표시 중인 안 하루 시뮬레이션'; }
}

async function main() {
  const [meta, stops, stations, demand, busRoads, strike, absorbing, results] = await Promise.all(
    ['meta', 'stops', 'stations', 'demand', 'bus_roads', 'strike_routes', 'absorbing_routes', 'results'].map((n) => get(n === 'meta' ? '/api/lab/meta' : `/api/lab/layer/${n}`)));
  Object.assign(D, {meta, stops, stations, demand, busRoads, strike, absorbing, results});
  S.fleet = meta.fleets[0]; $('#pF').value = S.fleet;
  $('#simDate').innerHTML = meta.dates_exp.map((d) => `<option value="${d}" ${d === meta.dates_exp[2] ? 'selected' : ''}>${d.slice(4, 6)}/${d.slice(6)}</option>`).join('');
  layersPanel();
  for (const id of ['#noMaeul', '#noGg']) $(id).onchange = render;
  $('#runGen').onclick = runGen; $('#runSim').onclick = runSim;
  $('#routes').onmouseover = (e) => { const tr = e.target.closest('tr'); if (tr) focus(Number(tr.dataset.i)); };
  $('#routes').onmouseleave = () => focus(null);
  render();
  await initMap();
}
main().catch((e) => { $('#status').textContent = `불러오기 실패: ${e.message}`; });
