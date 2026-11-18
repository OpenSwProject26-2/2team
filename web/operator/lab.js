/* routegen 노선 실험실. 데이터는 /api/lab/* 에서만 읽는다. */
const $ = (s) => document.querySelector(s);
const VARIANT_LABEL = {affected: '흡수 제외 없음', affected_maeul: '마을버스 흡수 제외', affected_gg: '경기버스 흡수 제외', affected_maeul_gg: '마을·경기 흡수 제외'};
const LAYERS = [
  ['busRoads', '버스 통행 가능 도로(=1)', '--road'], ['strike', '파업 노선', '--strike'], ['maeul', '마을버스', '--maeul'],
  ['gg', '경기버스', '--gg'], ['demand', '잔여 수요', '--strike', true], ['stations', '🚇 지하철역', '--ink', true], ['plan', '셔틀 안', '--plan'],
  ['planStops', '🚏 셔틀 정차 정류장', '--plan', true], ['bounds', '구 경계', '--ink-2'],
];
const DEF_AP = {maeul_cap: 35, maeul_load_ratio: 0.6, gg_cap: 60, gg_busy_ratio: 0.6, maeul_exact_stop: false, gg_exact_stop: true};
const S = {absorb: 'bundle', net: 'orig', fleet: null, visible: new Set(['busRoads', 'strike', 'demand', 'stations', 'plan', 'planStops', 'bounds']), live: null, hover: null, sel: null};
// 이모지를 그릴 수 없는 환경의 대체 아이콘(운영자 대시보드와 같은 그림)
const FALLBACK_SVG = {
  bus:`<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#fff" stroke="#405b77" stroke-width="3"/><rect x="12" y="12" width="24" height="22" rx="5" fill="#2b679c"/><rect x="15" y="15" width="18" height="9" rx="2" fill="#d9f4ff"/><path d="M12 26h24" stroke="#fff" stroke-width="2"/><circle cx="18" cy="30" r="2" fill="#fff"/><circle cx="30" cy="30" r="2" fill="#fff"/><path d="M17 34v3m14-3v3" stroke="#405b77" stroke-width="3"/></svg>`,
  metro:`<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#fff" stroke="#6334aa" stroke-width="3"/><rect x="13" y="11" width="22" height="24" rx="7" fill="#864ad7"/><rect x="16" y="16" width="16" height="9" rx="2" fill="#f3eaff"/><circle cx="18" cy="30" r="2" fill="#fff"/><circle cx="30" cy="30" r="2" fill="#fff"/><path d="m17 36-3 4m17-4 3 4" stroke="#6334aa" stroke-width="3"/></svg>`
};
const D = {};
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (x, d = 0) => x == null ? '—' : Number(x).toLocaleString('ko-KR', {maximumFractionDigits: d, minimumFractionDigits: d});
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3500); }
async function get(p) { const r = await fetch(p); const d = await r.json(); if (!r.ok) throw new Error(d.message || r.status); return d; }
async function post(p, body) { const r = await fetch(p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); const d = await r.json(); if (!r.ok) throw new Error(d.message || r.status); return d; }

function variant() { const m = $('#noMaeul').checked, g = $('#noGg').checked; return m && g ? 'affected_maeul_gg' : m ? 'affected_maeul' : g ? 'affected_gg' : 'affected'; }
function absorbParams() { return {maeul_cap: Number($('#aMc').value), maeul_load_ratio: Number($('#aMl').value), gg_cap: Number($('#aGc').value), gg_busy_ratio: Number($('#aGb').value), maeul_exact_stop: $('#aMx').checked, gg_exact_stop: true}; }
function absorbMode() { return variant() === 'affected' ? 'bundle' : S.absorb; }   // 흡수 제외가 없으면 두 방식이 같다
function apKey() { return absorbMode() === 'precise' ? JSON.stringify(absorbParams()) : ''; }
function precomputed() {
  const cv = $('#cover').checked, mode = absorbMode();
  if (mode === 'precise' && apKey() !== JSON.stringify(DEF_AP)) return null;          // 가정값을 바꾸면 사전 계산 없음
  return D.results.find((r) => r.net === S.net && r.variant === variant() && r.fleet === S.fleet && Boolean(r.cover) === cv && (r.absorb || 'bundle') === mode);
}
function shown() { return S.live && S.live.net === S.net && S.live.variant === variant() && Boolean(S.live.cover) === $('#cover').checked && (S.live.absorb || 'bundle') === absorbMode() && (S.live.apKey || '') === apKey() ? S.live : precomputed(); }

function seg(id, items, cur, on) {
  const el = $(id);
  el.innerHTML = items.map(([v, l]) => `<button type="button" aria-pressed="${String(v) === String(cur)}" data-v="${v}">${l}</button>`).join('');
  el.onclick = (e) => { const b = e.target.closest('button'); if (b) on(b.dataset.v); };
}

function fc(features) { return {type: 'FeatureCollection', features}; }
function line(coords, props = {}, id) { return {type: 'Feature', id, properties: props, geometry: {type: 'LineString', coordinates: coords}}; }

function currentStopDemand() {
  const v = variant();
  if (absorbMode() !== 'precise') return D.demand[v];
  const p = shown();
  if (p && p === S.live && S.live.stop_demand) return S.live.stop_demand;
  return apKey() === JSON.stringify(DEF_AP) ? D.demand['precise_' + v] : (S.live?.apKey === apKey() && S.live?.stop_demand) || D.demand['precise_' + v];
}
function demandFeatures() {
  const cur = currentStopDemand(), all = D.demand.affected, out = [];
  for (const [sid, val] of Object.entries(all)) {
    const s = D.stops[sid]; if (!s) continue;
    const now = cur[sid] || 0;
    out.push({type: 'Feature', properties: {name: s[2], now, removed: Math.max(0, val - now)}, geometry: {type: 'Point', coordinates: [s[0], s[1]]}});
  }
  return fc(out);
}
function planStopFeatures() {
  const p = shown(); if (!p) return fc([]);
  const by = new Map();
  p.routes.forEach((r) => r.stops.forEach((sid) => { if (!by.has(sid)) by.set(sid, []); by.get(sid).push(r.id); }));
  return fc([...by.entries()].filter(([sid]) => D.stops[sid]).map(([sid, rs]) => ({type: 'Feature', properties: {sid, name: D.stops[sid][2], routes: rs.join(', ')}, geometry: {type: 'Point', coordinates: D.stops[sid].slice(0, 2)}})));
}
function selStopFeatures() {
  const p = shown(); if (!p || S.sel == null || !p.routes[S.sel]) return fc([]);
  return fc(p.routes[S.sel].stops.filter((sid) => D.stops[sid]).map((sid, k) => ({type: 'Feature', properties: {seq: k + 1, name: D.stops[sid][2]}, geometry: {type: 'Point', coordinates: D.stops[sid].slice(0, 2)}})));
}
function planFeatures() {
  const p = shown(); if (!p) return fc([]);
  return fc(p.routes.map((r, i) => line(r.coords, {i, id: r.id, live: p === S.live ? 1 : 0}, i + 1)));
}

function render() {
  const v = variant();
  seg('#netSeg', [['orig', '원래 망'], ['road', '도로 망']], S.net, (x) => { S.net = x; render(); });
  seg('#fleetSeg', D.meta.fleets.map((f) => [f, `${f}대`]), S.fleet, (x) => { S.fleet = Number(x); $('#pF').value = x; render(); });
  seg('#absorbSeg', [['bundle', '기존 흡수 판정'], ['precise', '정밀 흡수(정원·정차)']], S.absorb, (x) => { S.absorb = x; render(); });
  const tot = Object.values(currentStopDemand() || {}).reduce((a, b) => a + b, 0), all = Object.values(D.demand.affected).reduce((a, b) => a + b, 0);
  $('#demandHint').textContent = `셔틀 대상 하루 ${fmt(tot)}명${v !== 'affected' ? ` (흡수로 ${fmt(all - tot)}명 제외)` : ''} · ${VARIANT_LABEL[v]}`;
  const p = shown(), isLive = p && p === S.live;
  $('#planTitle').innerHTML = p ? `${isLive ? '<span class="tag live">직접 실행</span>' : '<span class="tag">사전 계산</span>'} ${S.net === 'road' ? '도로 망' : '원래 망'} · ${VARIANT_LABEL[v]}${absorbMode() === 'precise' ? '(정밀)' : ''} · ${p.fleet}대${p.cover ? ' · 역마다 1노선' : ''}` : (absorbMode() === 'precise' && apKey() !== JSON.stringify(DEF_AP) ? '가정값을 바꿨다 — 왼쪽 5번 노선 생성으로 직접 실행' : '안 없음');
  const sim = isLive ? S.live.sim : p?.exp;
  renderCoverage(p, isLive);
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
  if (S.sel != null && (!p || !p.routes[S.sel])) S.sel = null;
  $('#routes').innerHTML = p ? p.routes.map((r, i) => `<tr data-i="${i}" class="${S.sel === i ? 'sel' : ''}"><td>${r.id}</td><td>${r.kind === 'access' ? '→ ' : r.kind === 'egress' ? '← ' : '↔ '}${esc(r.hubs.join('·'))}</td><td class="n">${r.vehicles}</td><td class="n">${Math.round(r.cycle_s / 60)}분</td><td class="n">${fmt(r.approx)}</td></tr>`).join('') : '';
  const sr = p && S.sel != null ? p.routes[S.sel] : null;
  $('#stopSeqTitle').textContent = sr ? `${sr.id} 정류장 순서 · ${sr.stops.length}곳 (${sr.kind === 'access' ? '역으로' : '역에서'})` : '정류장 순서';
  $('#stopSeq').innerHTML = sr ? sr.stops.map((sid, k) => `<li><button type="button" data-sid="${esc(sid)}"><span class="seq">${k + 1}</span>${esc(D.stops[sid]?.[2] ?? sid)}${station(sid) ? ` <span class="hint">🚇 ${esc(station(sid))}</span>` : ''}</button></li>`).join('') : '<li class="hint">노선을 지도나 표에서 클릭하세요</li>';
  $('#runSim').disabled = !p;
  renderSchedule();
  $('#sub').textContent = `${D.meta.gu} · 망 ${D.meta.network_snapshot_id} · 도로 구간 +${fmt(D.meta.roadnet.added_segments)}개(버스 도로 기준)`;
  updateMap();
}

function renderCoverage(p, isLive) {
  // 전체 = 파업 노선 승객 중 걸어서 갈 수 없는 사람(affected, 하루 5일 평균).
  // 마을·경기가 맡는 몫 = 체크한 흡수로 빠지는 수요. 둘 다 체크하면 마을로 갈 수 있는 사람은 마을 몫, 나머지 중 경기로 갈 수 있는 사람은 경기 몫.
  const sum = (v) => Object.values(D.demand[v]).reduce((a, b) => a + b, 0);
  const A = sum('affected'), M = sum('affected_maeul'), G = sum('affected_gg'), MG = sum('affected_maeul_gg');
  const m = $('#noMaeul').checked, g = $('#noGg').checked;
  let maeul = m ? A - M : 0, gg = g ? (m ? M - MG : A - G) : 0;
  const ab = absorbMode() === 'precise' ? p?.absorbed : null;
  if (ab) { maeul = ab.maeul || 0; gg = ab.gg || 0; }
  const target = A - maeul - gg;
  const rate = isLive ? S.live.sim?.rate : p?.exp?.rate;
  const el = $('#coverage');
  if (!p || rate == null) { el.innerHTML = `<p class="hint">${isLive ? '하루 시뮬레이션을 돌리면 전체 충족률이 나옵니다.' : '시뮬레이션 결과가 없습니다.'}</p>`; return; }
  const shuttle = rate / 100 * target, unmet = target - shuttle;
  const pct = (x) => (100 * x / A).toFixed(1);
  const segs = [['마을버스가 맡음', maeul, 'var(--maeul)'], ['경기버스가 맡음', gg, 'var(--gg)'], ['대체 셔틀이 태움', shuttle, 'var(--plan)'], ['남은 미충족', unmet, '#e2e8f0']];
  const total = maeul + gg + shuttle;
  el.innerHTML = `<div class="cov-top"><b>전체 충족률 ${pct(total)}%</b><span>파업 노선 승객 하루 ${fmt(A)}명 기준${isLive ? ` · ${S.live.sim.date.slice(4, 6)}/${S.live.sim.date.slice(6)} 하루` : ' · 실험 5일 평균'}</span></div>
    <div class="cov-bar" role="img" aria-label="전체 충족 구성">${segs.filter(([, v]) => v > 0).map(([n, v, c]) => `<span style="width:${100 * v / A}%;background:${c}" title="${n} ${fmt(v)}명 (${pct(v)}%)"></span>`).join('')}</div>
    <div class="cov-rows">${segs.map(([n, v, c]) => `<span><i style="background:${c}"></i>${n}</span><span>${fmt(v)}명</span><span>${pct(v)}%</span>`).join('')}</div>
    <div class="cov-shuttle">대체 셔틀만 보면: 셔틀 대상 ${fmt(target)}명 중 <b>${rate.toFixed(2)}%</b> 충족</div>
    ${ab ? `<p class="hint">정밀 흡수: 마을 요청 ${fmt(ab.maeul_req)}명 중 ${fmt(ab.maeul)}명, 경기 요청 ${fmt(ab.gg_req)}명 중 ${fmt(ab.gg)}명을 남는 정원만큼 흡수(정원·혼잡은 가정값).</p>` : m || g ? '<p class="hint">마을·경기 몫은 그 노선으로 갈 수 있으면 모두 탄다는 가정이다(여유 정원 미확인). 왼쪽 2번에서 정밀 흡수로 바꿀 수 있다.</p>' : '<p class="hint">마을·경기 흡수를 체크하지 않으면 셔틀이 전체를 맡는다고 본다. 왼쪽 2번에서 체크하면 그 몫을 따로 보여 준다.</p>'}`;
}
const T0 = 21600, T1 = 39600, WEND = 36000;
const hhmm = (t) => `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t % 3600 / 60)).padStart(2, '0')}`;
function renderSchedule() {
  const p = shown(), live = p && p === S.live;
  const sc = live ? S.live.sim?.schedule : p?.schedule;
  const date = live ? S.live.sim?.date : p?.schedule_date;
  $('#schedSub').textContent = sc ? `· ${date.slice(4, 6)}/${date.slice(6)} 시뮬레이터 시간표 · 역별로 묶음` : '';
  if (!p || !sc) { $('#sched').innerHTML = `<p class="hint">${live ? '직접 실행한 안은 하루 시뮬레이션을 돌리면 동선이 나옵니다.' : '이 안의 시간표가 없습니다.'}</p>`; return; }
  const x = (t) => ((Math.min(Math.max(t, T0), T1) - T0) / (T1 - T0) * 1000).toFixed(1);
  const idx = new Map(p.routes.map((r, i) => [r.id, i]));
  const groups = new Map();
  for (const v of sc.vehicles) {
    const i = idx.get(v.route); if (i == null) continue;
    const r = p.routes[i], key = r.hubs.join('·');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({v, r, i});
  }
  const ticks = []; for (let t = T0; t <= T1; t += 1800) ticks.push(t);
  const axis = `<div class="axis"><span></span><svg viewBox="0 0 1000 18" preserveAspectRatio="none">${ticks.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="12" y2="18" stroke="#c3cad4"/>`).join('')}</svg><span></span></div>
    <div class="axis"><span></span><div style="position:relative;height:14px">${ticks.filter((t) => t % 3600 === 0).map((t) => `<span style="position:absolute;left:${x(t) / 10}%;transform:translateX(-50%)">${hhmm(t)}</span>`).join('')}</div><span></span></div>`;
  let html = axis;
  const order = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  for (const [name, rows] of order) {
    const pax = rows.reduce((a, {v}) => a + v.cycles.reduce((b, c) => b + c[3], 0), 0);
    const rids = new Set(rows.map(({r}) => r.id));
    html += `<div class="sg"><div class="sg-h">🚇 ${esc(name)}역 <small>노선 ${rids.size}개 · 차량 ${rows.length}대 · 하루 탑승 ${fmt(pax)}명</small></div>`;
    rows.sort((a, b) => a.i - b.i || a.v.vehicle.localeCompare(b.v.vehicle));
    for (const {v, r, i} of rows) {
      const n = v.cycles.reduce((b, c) => b + c[3], 0);
      const bars = v.cycles.map((c) => {
        const op = (0.3 + 0.7 * Math.min(1, c[4] / 45)).toFixed(2);
        const tip = `${v.vehicle} ${r.id} · ${hhmm(c[0])} 출발 → ${hhmm(c[1])} ${r.kind === 'access' ? '역 도착' : '종점 도착'} · 탑승 ${c[3]}명 · 최대 재차 ${c[4]}/45 · 복귀 끝 ${hhmm(c[2])}`;
        return `<rect x="${x(c[0])}" y="3" width="${Math.max(2, x(c[1]) - x(c[0]))}" height="12" rx="1.5" fill="#1e293b" fill-opacity="${op}"><title>${esc(tip)}</title></rect>`
          + `<rect x="${x(c[1])}" y="6" width="${Math.max(0, x(c[2]) - x(c[1]))}" height="6" fill="#cbd5e1"><title>${esc(`${v.vehicle} 빈 차 복귀 ${hhmm(c[1])} → ${hhmm(c[2])}`)}</title></rect>`;
      }).join('');
      const end = `<line x1="${x(WEND)}" x2="${x(WEND)}" y1="0" y2="18" stroke="#b91c1c" stroke-dasharray="3 2"/>`;
      html += `<div class="vrow${S.sel === i ? ' sel' : ''}" data-i="${i}"><span class="lab"><b>${v.vehicle}</b> · ${r.id} ${r.kind === 'access' ? '→ 역' : '← 역'}</span><svg viewBox="0 0 1000 18" preserveAspectRatio="none">${end}${bars}</svg><span class="num">${v.cycles.length}회 · ${fmt(n)}명</span></div>`;
    }
    html += '</div>';
  }
  $('#sched').innerHTML = html;
}
function station(sid) { const t = D.stations.find((x) => x.access === sid || x.egress === sid); return t ? t.name + '역' : null; }
function updateMap() {
  const m = D.map; if (!m || !m.getSource('demand')) return;
  m.getSource('demand').setData(demandFeatures());
  m.getSource('plan').setData(planFeatures());
  m.getSource('planStops').setData(planStopFeatures());
  m.getSource('selStops').setData(selStopFeatures());
  const live = shown() === S.live;
  m.setPaintProperty('plan', 'line-color', live ? css('--live') : css('--plan'));
  const vis = {busRoads: ['busRoads'], strike: ['strike'], maeul: ['maeul'], gg: ['gg'], demand: ['demand-now', 'demand-removed'], stations: ['stations', 'station-labels'], plan: ['plan', 'plan-casing'], planStops: ['plan-stops'], bounds: ['bounds', 'bounds-home', 'bounds-labels']};
  for (const [k, ids] of Object.entries(vis)) for (const id of ids) m.setLayoutProperty(id, 'visibility', S.visible.has(k) ? 'visible' : 'none');
  $('#status').textContent = shown() ? `${live ? '직접 실행 안' : '사전 계산 안'} 표시 중 · 노선 ${shown().routes.length}개` : '이 조합의 안이 없음';
}

function focus(i) {
  const m = D.map; if (!m) return;
  m.setPaintProperty('plan', 'line-opacity', i == null ? 0.9 : ['case', ['==', ['get', 'i'], i], 1, 0.15]);
  m.setPaintProperty('plan', 'line-width', i == null ? 3.5 : ['case', ['==', ['get', 'i'], i], 6, 3]);
  document.querySelectorAll('#routes tr').forEach((tr) => tr.classList.toggle('on', Number(tr.dataset.i) === i));
}

async function iconImage(emoji, svg) {
  // 흰 원 위에 이모지. 이모지 글꼴이 없어 아무것도 안 그려지면 SVG 그림으로 대신한다.
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
  g.fillStyle = '#ffffff'; g.beginPath(); g.arc(32, 32, 30, 0, 2 * Math.PI); g.fill();
  g.strokeStyle = '#1e293b'; g.lineWidth = 3; g.stroke();
  g.font = '38px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(emoji, 32, 35);
  const px = g.getImageData(12, 12, 40, 40).data; let colored = 0;
  for (let i = 0; i < px.length; i += 4) if (px[i + 3] > 0 && (Math.abs(px[i] - px[i + 1]) > 30 || Math.abs(px[i + 1] - px[i + 2]) > 30)) colored++;
  if (colored > 20) return g.getImageData(0, 0, 64, 64);
  const img = new Image(); img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`; await img.decode(); return img;
}
async function initMap() {
  const maplibregl = await import('https://unpkg.com/maplibre-gl@6.18.0/dist/maplibre-gl.mjs');
  const [w, s, e, n] = D.meta.bbox;
  const m = new maplibregl.Map({container: 'mapc', style: 'https://tiles.openfreemap.org/styles/liberty', bounds: [[w, s], [e, n]], fitBoundsOptions: {padding: 30}, attributionControl: {compact: true},
    localIdeographFontFamily: "'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans CJK KR', 'Noto Sans KR', sans-serif"});
  D.map = m;
  m.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-left');
  await new Promise((r) => m.on('load', r));
  m.addSource('bounds', {type: 'geojson', data: fc(D.bounds.map((b) => ({type: 'Feature', properties: {name: b.name, home: b.home ? 1 : 0}, geometry: {type: 'MultiLineString', coordinates: b.lines}})))});
  m.addLayer({id: 'bounds', type: 'line', source: 'bounds', paint: {'line-color': '#334155', 'line-width': 1.6, 'line-dasharray': [3, 2], 'line-opacity': 0.75}});
  m.addLayer({id: 'bounds-home', type: 'line', source: 'bounds', filter: ['==', ['get', 'home'], 1], paint: {'line-color': '#334155', 'line-width': 3, 'line-opacity': 0.9}});
  m.addSource('boundLabels', {type: 'geojson', data: fc(D.bounds.map((b) => ({type: 'Feature', properties: {name: b.name, home: b.home ? 1 : 0}, geometry: {type: 'Point', coordinates: b.label}})))});
  m.addLayer({id: 'bounds-labels', type: 'symbol', source: 'boundLabels', layout: {'text-field': ['get', 'name'], 'text-size': ['case', ['==', ['get', 'home'], 1], 16, 13], 'text-font': ['Noto Sans Bold'], 'text-allow-overlap': false},
    paint: {'text-color': '#334155', 'text-opacity': 0.75, 'text-halo-color': '#ffffff', 'text-halo-width': 2}});
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
  m.addImage('ic-bus', await iconImage('🚏', FALLBACK_SVG.bus), {pixelRatio: 2});
  m.addImage('ic-metro', await iconImage('🚇', FALLBACK_SVG.metro), {pixelRatio: 2});
  m.addSource('planStops', {type: 'geojson', data: planStopFeatures()});
  m.addLayer({id: 'plan-stops', type: 'symbol', source: 'planStops', layout: {'icon-image': 'ic-bus', 'icon-size': ['interpolate', ['linear'], ['zoom'], 11, 0.38, 15, 0.7], 'icon-allow-overlap': true,
    'text-field': ['step', ['zoom'], '', 14.5, ['get', 'name']], 'text-size': 11, 'text-offset': [0, 1.4], 'text-anchor': 'top', 'text-font': ['Noto Sans Regular'], 'text-optional': true},
    paint: {'text-color': '#1e293b', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5}});
  m.addSource('selStops', {type: 'geojson', data: selStopFeatures()});
  m.addLayer({id: 'sel-stops', type: 'circle', source: 'selStops', paint: {'circle-radius': 10, 'circle-color': '#f59e0b', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2}});
  m.addLayer({id: 'sel-stops-n', type: 'symbol', source: 'selStops', layout: {'text-field': ['to-string', ['get', 'seq']], 'text-size': 11, 'text-font': ['Noto Sans Bold'], 'text-allow-overlap': true,
    'icon-allow-overlap': true}, paint: {'text-color': '#1e293b'}});
  m.addLayer({id: 'sel-stops-name', type: 'symbol', source: 'selStops', layout: {'text-field': ['get', 'name'], 'text-size': 11.5, 'text-offset': [0, 1.5], 'text-anchor': 'top', 'text-font': ['Noto Sans Regular'], 'text-optional': true},
    paint: {'text-color': '#1e293b', 'text-halo-color': '#ffffff', 'text-halo-width': 1.6}});
  m.addSource('stations', {type: 'geojson', data: fc(D.stations.map((t) => ({type: 'Feature', properties: {name: t.name + '역', d: t.demand.affected}, geometry: {type: 'Point', coordinates: [t.lon, t.lat]}})))});
  m.addLayer({id: 'stations', type: 'symbol', source: 'stations', layout: {'icon-image': 'ic-metro', 'icon-size': ['interpolate', ['linear'], ['zoom'], 11, 0.45, 15, 0.8], 'icon-allow-overlap': true}});
  m.addLayer({id: 'station-labels', type: 'symbol', source: 'stations', layout: {'text-field': ['get', 'name'], 'text-size': 12, 'text-offset': [0, 1.5], 'text-anchor': 'top', 'text-font': ['Noto Sans Regular']}, paint: {'text-color': '#17202c', 'text-halo-color': '#ffffff', 'text-halo-width': 1.6}});
  const popup = new maplibregl.Popup({closeButton: false, closeOnClick: false});
  m.on('mousemove', 'plan', (e) => {
    const i = e.features[0].properties.i, r = shown().routes[i];
    focus(i);
    popup.setLngLat(e.lngLat).setHTML(`<b>${r.id}</b> ${r.kind === 'access' ? '→' : '←'} ${esc(r.hubs.join('·'))}<br>${r.vehicles}대 · 주기 ${Math.round(r.cycle_s / 60)}분 · 정류장 ${r.stops.length}개<br>근사 ${fmt(r.approx)}명/일`).addTo(m);
  });
  m.on('mouseleave', 'plan', () => { focus(S.sel); popup.remove(); });
  m.on('click', 'plan', (e) => select(e.features[0].properties.i));
  m.on('mousemove', 'plan-stops', (e) => { const p = e.features[0].properties; popup.setLngLat(e.lngLat).setHTML(`🚏 ${esc(p.name)}<br><span style="color:#667">${esc(p.sid)} · 노선 ${esc(p.routes)}</span>`).addTo(m); });
  m.on('mouseleave', 'plan-stops', () => popup.remove());
  for (const l of ['plan', 'plan-stops']) { m.on('mouseenter', l, () => { m.getCanvas().style.cursor = 'pointer'; }); m.on('mouseleave', l, () => { m.getCanvas().style.cursor = ''; }); }
  m.on('mousemove', 'demand-now', (e) => { if (m.queryRenderedFeatures(e.point, {layers: ['plan']}).length) return; const p = e.features[0].properties; popup.setLngLat(e.lngLat).setHTML(`${esc(p.name)}<br>잔여 수요 ${fmt(p.now, 1)}명/일${p.removed > 0.5 ? ` · 흡수 제외 ${fmt(p.removed, 1)}명` : ''}`).addTo(m); });
  m.on('mouseleave', 'demand-now', () => popup.remove());
  updateMap();
}

function layersPanel() {
  $('#layers').innerHTML = LAYERS.map(([k, l, c, dot]) => `<label class="chk"><input type="checkbox" data-k="${k}" ${S.visible.has(k) ? 'checked' : ''}><i class="${dot ? 'dot' : ''}" style="background:var(${c})"></i>${l}</label>`).join('');
  $('#layers').onchange = (e) => { const k = e.target.dataset.k; e.target.checked ? S.visible.add(k) : S.visible.delete(k); updateMap(); };
}

function select(i) {
  S.sel = S.sel === i ? null : i;
  if (S.sel != null && window.scrollY > 80) window.scrollTo({top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  render(); focus(S.sel);
  const p = shown(); const r = p && S.sel != null ? p.routes[S.sel] : null;
  if (r && D.map) { const xs = r.coords.map((c) => c[0]), ys = r.coords.map((c) => c[1]); D.map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], {padding: 80, maxZoom: 15.5, duration: 600}); }
}
async function runGen() {
  const b = $('#runGen'); b.disabled = true; b.textContent = '생성 중… (수 초~수십 초)';
  try {
    const params = {radius_m: Number($('#pR').value) * 1000, max_stops: $('#pN').value === 'none' ? null : Number($('#pN').value), max_detour: Number($('#pD').value), l_max_min: Number($('#pL').value)};
    const res = await post('/api/lab/generate', {net: S.net, variant: variant(), fleet: Number($('#pF').value), params, cover: $('#cover').checked, absorb: absorbMode(), absorb_params: absorbParams()});
    S.live = {...res, sim: null, apKey: apKey()}; S.sel = null;
    toast(`노선 ${res.routes.length}개 · 근사 ${fmt(res.approx)}명 · ${res.seconds}초`);
    render();
  } catch (error) { toast(`생성 실패: ${error.message}`); }
  finally { b.disabled = false; b.textContent = '노선 생성'; }
}
async function runSim() {
  const p = shown(); if (!p) return;
  const b = $('#runSim'); b.disabled = true; b.textContent = '시뮬레이션 중… (10~30초)';
  try {
    const res = await post('/api/lab/simulate', {net: S.net, variant: variant(), fleet: p.fleet, date: $('#simDate').value, routes: p.routes.map((r) => ({stops: r.stops, vehicles: r.vehicles})), absorb: absorbMode(), absorb_params: absorbParams()});
    if (res.verify_pass !== true) { toast(`실행 실패: ${res.reason || res.stage_failed}`); return; }
    if (p === S.live) S.live.sim = res;
    toast(`${res.date} 충족률 ${res.rate.toFixed(2)}% (충족 ${fmt(res.served)} / ${fmt(res.total)}명) · M07 pass · ${res.seconds}초`);
    render();
  } catch (error) { toast(`시뮬레이션 실패: ${error.message}`); }
  finally { b.disabled = false; b.textContent = '표시 중인 안 하루 시뮬레이션'; }
}

async function main() {
  const [meta, stops, stations, demand, busRoads, strike, absorbing, results, bounds] = await Promise.all(
    ['meta', 'stops', 'stations', 'demand', 'bus_roads', 'strike_routes', 'absorbing_routes', 'results', 'boundaries'].map((n) => get(n === 'meta' ? '/api/lab/meta' : `/api/lab/layer/${n}`)));
  Object.assign(D, {meta, stops, stations, demand, busRoads, strike, absorbing, results, bounds});
  S.fleet = meta.fleets[0]; $('#pF').value = S.fleet;
  $('#simDate').innerHTML = meta.dates_exp.map((d) => `<option value="${d}" ${d === meta.dates_exp[2] ? 'selected' : ''}>${d.slice(4, 6)}/${d.slice(6)}</option>`).join('');
  layersPanel();
  for (const id of ['#noMaeul', '#noGg', '#cover', '#aMx']) $(id).onchange = render;
  for (const id of ['#aMc', '#aMl', '#aGc', '#aGb']) $(id).onchange = render;
  $('#runGen').onclick = runGen; $('#runSim').onclick = runSim;
  $('#routes').onmouseover = (e) => { const tr = e.target.closest('tr'); if (tr) focus(Number(tr.dataset.i)); };
  $('#routes').onmouseleave = () => focus(S.sel);
  $('#routes').onclick = (e) => { const tr = e.target.closest('tr'); if (tr) select(Number(tr.dataset.i)); };
  $('#leftToggle').onclick = () => {
    const closed = document.body.classList.toggle('left-closed');
    $('#leftToggle').textContent = closed ? '▶ 조작 열기' : '◀ 조작 닫기';
    $('#leftToggle').setAttribute('aria-expanded', String(!closed));
    setTimeout(() => D.map?.resize(), 260);
  };
  $('#sched').onclick = (e) => { const row = e.target.closest('.vrow'); if (row) select(Number(row.dataset.i)); };
  $('#schedToggle').onclick = () => { const c = $('#schedPanel').classList.toggle('closed'); $('#schedToggle').textContent = c ? '펼치기' : '접기'; };
  $('#stopSeq').onclick = (e) => { const b = e.target.closest('button[data-sid]'); const s = b && D.stops[b.dataset.sid]; if (s && D.map) D.map.flyTo({center: s.slice(0, 2), zoom: 16, duration: 600}); };
  render();
  await initMap();
}
main().catch((e) => { $('#status').textContent = `불러오기 실패: ${e.message}`; });
