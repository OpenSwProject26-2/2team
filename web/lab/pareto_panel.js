// 충족률·형평성 곡선 패널(파이프라인 pareto.json). 지도 위 왼쪽 아래에 띄우는 작은 패널.
// - 지도 그리기 코드를 모른다. 안을 고르면 onSelect(plan)만 부른다(plan.export_url = 읽을 운행안 주소).
// - 외부 파일을 받지 않는다(자체 SVG). 빌드 없이 <script type="module">로 넣는다.
//
//   import {mountParetoPanel} from './pareto_panel.js';
//   const panel = mountParetoPanel(map, {paretoUrl: 'run/pareto.json', onSelect: (plan) => ...});
//   panel.select(id); panel.setOpen(false); panel.getSelected(); panel.destroy();
// 지자체안 점은 운행안 파일이 있을 때만 고를 수 있다: pareto.json muni.export_path 또는 opts.muniExportPath(파레토 파일 기준 상대 경로).

export const BASE_COLOR = {sector: '#257de0', feeder: '#f47230', oneway_loop: '#00b894'};
export const BASE_NAME = {sector: '섹터 순회', feeder: '피더 순회', oneway_loop: '편도 노선 생성 후 순회'};
export const MUNI_COLOR = '#52514e';
const GU_KO = {gangnam: '강남구', gangdong: '강동구', gangbuk: '강북구', gangseo: '강서구', gwanak: '관악구', gwangjin: '광진구', guro: '구로구',
  geumcheon: '금천구', nowon: '노원구', dobong: '도봉구', dongdaemun: '동대문구', dongjak: '동작구', mapo: '마포구', seodaemun: '서대문구',
  seocho: '서초구', seongdong: '성동구', seongbuk: '성북구', songpa: '송파구', yangcheon: '양천구', yeongdeungpo: '영등포구', yongsan: '용산구',
  eunpyeong: '은평구', jongno: '종로구', jung: '중구', jungnang: '중랑구'};
const DEFAULT_KEY = 'lab.paretoPanel.open';
const EPS = 1e-9;

const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** 지표가 있는 우리 안(지자체안 제외). */
export function usable(plans) {
  return (plans || []).filter((p) => Number.isFinite(p?.served_rate) && Number.isFinite(p?.equity_far));
}

/** 형평성 기준(0~1) 이상인 안 중 충족률 최대. 동률이면 형평성 높은 쪽 → plan id 순. 없으면 null. */
export function pickByFloor(plans, floor) {
  const ok = usable(plans).filter((p) => p.equity_far >= floor - EPS);
  ok.sort((a, b) => b.served_rate - a.served_rate || b.equity_far - a.equity_far || byId(a, b));
  return ok[0] || null;
}

/** 경계 위 점(hull: true)을 형평성 오름차순으로. */
export function hullPoints(plans) {
  return usable(plans).filter((p) => p.hull).sort((a, b) => a.equity_far - b.equity_far || b.served_rate - a.served_rate || byId(a, b));
}

/** 경계 계단선 좌표 [[형평성, 충족률], …]: 점 i에서 아래로 다음 점의 충족률까지, 그다음 오른쪽으로 다음 점까지.
 *  (형평성 기준 x를 (e_i, e_{i+1}]에 두면 고르는 안이 점 i+1이라는 뜻과 같은 모양) */
export function hullSteps(hull) {
  const out = [];
  hull.forEach((p, i) => {
    if (i > 0) out.push([hull[i - 1].equity_far, p.served_rate]);
    out.push([p.equity_far, p.served_rate]);
  });
  return out;
}

/** 축 범위(약간 여유)와 눈금 3~4개. 값은 0~1. */
export function axis(values, pad = 0.06) {
  let lo = Math.min(...values), hi = Math.max(...values);
  if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
  if (hi - lo < 0.02) { lo -= 0.01; hi += 0.01; }
  const span = hi - lo; lo = Math.max(0, lo - span * pad); hi = Math.min(1, hi + span * pad);
  // 눈금 3~4개가 되는 가장 촘촘한 간격(없으면 4개 이하 중 가장 많은 것)
  const make = (step) => { const t = []; for (let v = Math.ceil(lo / step - EPS) * step; v <= hi + EPS; v += step) t.push(Math.round(v * 1e4) / 1e4); return t; };
  const cands = [0.01, 0.02, 0.025, 0.04, 0.05, 0.08, 0.1, 0.2, 0.25, 0.5].map(make);
  const ticks = cands.find((t) => t.length >= 3 && t.length <= 4) || cands.filter((t) => t.length <= 4).sort((a, b) => b.length - a.length)[0] || [];
  return {lo, hi, ticks};
}

export function guName(id) { return GU_KO[id] || id || ''; }

export function daysLabel(days) {
  const d = (days || []).map(String).filter((x) => /^\d{8}$/.test(x)).sort();
  if (!d.length) return '';
  const md = (x) => `${Number(x.slice(4, 6))}/${Number(x.slice(6, 8))}`;
  if (d.length === 1) return md(d[0]);
  const last = d[d.length - 1];
  const end = last.slice(4, 6) === d[0].slice(4, 6) ? String(Number(last.slice(6, 8))) : md(last);
  return `${md(d[0])}~${end} (${d.length}일)`;
}

const pct = (v, n = 1) => `${(v * 100).toFixed(n)}%`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const SVG = 'http://www.w3.org/2000/svg';

function readOpen(key) {
  try { const v = localStorage.getItem(key); if (v === '1') return true; if (v === '0') return false; } catch (_) { /* 저장소 막힘 */ }
  return (window.innerWidth || 1024) >= 768;
}
function writeOpen(key, open) { try { localStorage.setItem(key, open ? '1' : '0'); } catch (_) { /* 무시 */ } }

/**
 * @param target MapLibre 지도(addControl 있음) 또는 HTML 요소
 * @param opts {paretoUrl | paretoData, onSelect(plan), initialPlanId, storageKey, position, muniExportPath, exportUrl(plan),
 *               autoSelect(기본 true: 처음에 해시 안 또는 경계 위 충족률 최대 안을 고름. false면 사용자가 고를 때까지 비워 둠)}
 */
export function mountParetoPanel(target, opts = {}) {
  const key = opts.storageKey || DEFAULT_KEY;
  const uid = `pp${Math.random().toString(36).slice(2, 8)}`;
  const root = document.createElement('div');
  root.className = 'pp-root maplibregl-ctrl';
  root.innerHTML = `
    <section class="pp" id="${uid}-panel" aria-label="충족률·형평성 곡선">
      <div class="pp-head"><strong>충족률·형평성 곡선</strong>
        <button type="button" class="pp-toggle" aria-expanded="true" aria-controls="${uid}-panel" title="접기">접기</button></div>
      <div class="pp-meta"></div>
      <div class="pp-plot">
        <svg class="pp-svg" viewBox="0 0 300 168" role="img" aria-label="충족률·형평성 점. 점을 누르면 그 안을 지도에 그립니다. 형평성 기준 슬라이더로도 고를 수 있습니다."></svg>
        <div class="pp-tip" role="tooltip" hidden></div>
      </div>
      <label class="pp-floor"><span>형평성 ≥ <output></output></span><input type="range" aria-label="형평성 기준"></label>
      <div class="pp-msg" role="status" aria-live="polite"></div>
    </section>
    <button type="button" class="pp-open" aria-expanded="false" aria-controls="${uid}-panel" hidden>충족률·형평성 곡선</button>`;
  const $ = (s) => root.querySelector(s);
  const panel = $('.pp'), toggle = $('.pp-toggle'), opener = $('.pp-open'), svg = $('.pp-svg'), tip = $('.pp-tip');
  const range = $('.pp-floor input'), out = $('.pp-floor output'), msg = $('.pp-msg'), meta = $('.pp-meta');

  // 패널 위 조작은 지도로 보내지 않는다(휠 확대·끌기·두 번 누르기 확대 막기)
  const stop = (e) => e.stopPropagation();
  for (const t of ['wheel', 'mousedown', 'mouseup', 'pointerdown', 'pointerup', 'touchstart', 'touchmove', 'touchend', 'dblclick', 'click', 'contextmenu', 'keydown'])
    root.addEventListener(t, stop, {passive: t !== 'wheel' ? true : false});
  root.addEventListener('wheel', (e) => { if (!panel.contains(e.target) || !e.target.closest('.pp-scroll')) e.preventDefault(); }, {passive: false});

  let control = null;
  if (target && typeof target.addControl === 'function') {
    control = {onAdd: () => root, onRemove: () => root.remove()};
    target.addControl(control, opts.position || 'bottom-left');
  } else if (target instanceof Element) {
    target.appendChild(root);
  } else throw new Error('mountParetoPanel: 지도 또는 HTML 요소가 필요합니다');

  const S = {data: null, plans: [], hull: [], muni: null, selected: null, x: null, y: null, destroyed: false};

  function setOpen(open) {
    panel.hidden = !open; opener.hidden = open;
    toggle.setAttribute('aria-expanded', String(open)); opener.setAttribute('aria-expanded', String(open));
    writeOpen(key, open);
    if (open) render();
  }
  toggle.addEventListener('click', () => { setOpen(false); opener.focus(); });
  opener.addEventListener('click', () => { setOpen(true); toggle.focus(); });

  const W = 300, H = 168, M = {l: 34, r: 10, t: 10, b: 30};
  const px = (e) => M.l + (e - S.x.lo) / (S.x.hi - S.x.lo) * (W - M.l - M.r);
  const py = (s) => H - M.b - (s - S.y.lo) / (S.y.hi - S.y.lo) * (H - M.t - M.b);
  const el = (name, attrs, text) => { const n = document.createElementNS(SVG, name); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text != null) n.textContent = text; return n; };

  function render() {
    if (!S.data || panel.hidden) return;
    svg.replaceChildren();
    const g = el('g', {class: 'pp-axes'});
    for (const t of S.x.ticks) { g.append(el('line', {x1: px(t), x2: px(t), y1: M.t, y2: H - M.b, class: 'pp-grid'}), el('text', {x: px(t), y: H - M.b + 11, 'text-anchor': 'middle'}, `${Math.round(t * 100)}`)); }
    for (const t of S.y.ticks) { g.append(el('line', {x1: M.l, x2: W - M.r, y1: py(t), y2: py(t), class: 'pp-grid'}), el('text', {x: M.l - 4, y: py(t) + 3, 'text-anchor': 'end'}, `${Math.round(t * 100)}`)); }
    g.append(el('text', {x: W - M.r, y: H - 3, 'text-anchor': 'end', class: 'pp-axis-name'}, '형평성 → (%)'));
    g.append(el('text', {x: 2, y: M.t - 1, 'text-anchor': 'start', class: 'pp-axis-name'}, '충족률 ↑'));
    svg.append(g);
    if (S.hull.length) svg.append(el('polyline', {class: 'pp-hull', points: hullSteps(S.hull).map(([e, s]) => `${px(e)},${py(s)}`).join(' ')}));
    const pts = el('g', {});
    const onHull = new Set(S.hull.map((p) => p.id));
    for (const p of [...S.plans].sort((a, b) => onHull.has(a.id) - onHull.has(b.id))) {
      const c = BASE_COLOR[p.base] || '#64748b', h = onHull.has(p.id);
      pts.append(el('circle', {cx: px(p.equity_far), cy: py(p.served_rate), r: h ? 3.6 : 2.6, fill: c, 'fill-opacity': h ? 1 : 0.3, stroke: h ? '#fff' : 'none', 'stroke-width': 0.8}));
    }
    if (S.muni) {
      const x = px(S.muni.equity_far), y = py(S.muni.served_rate);
      pts.append(el('rect', {x: x - 3.6, y: y - 3.6, width: 7.2, height: 7.2, fill: MUNI_COLOR, stroke: '#fff', 'stroke-width': 0.8}));
      pts.append(el('text', {x: x + 6, y: y + 3, class: 'pp-label'}, '지자체안'));
    }
    svg.append(pts);
    const labels = el('g', {});
    const labeled = new Set();
    for (const p of S.plans) if (p.rep) { labeled.add(p.id); labels.append(label(p, p.rep, 'pp-rep')); }
    if (S.selected && S.selected !== S.muni && !labeled.has(S.selected.id)) labels.append(label(S.selected, S.selected.algo_name || S.selected.id, 'pp-label'));
    svg.append(labels);
    if (S.selected) svg.append(el('circle', {cx: px(S.selected.equity_far), cy: py(S.selected.served_rate), r: 7, class: 'pp-ring'}));
  }
  function label(p, text, cls) {
    const x = px(p.equity_far), y = py(p.served_rate), right = x > W * 0.62;
    return el('text', {x: right ? x - 8 : x + 8, y: y - 6, 'text-anchor': right ? 'end' : 'start', class: cls}, text);
  }

  function svgPoint(evt) {
    const m = svg.getScreenCTM(); if (!m) return null;
    const pt = new DOMPoint(evt.clientX, evt.clientY).matrixTransform(m.inverse());
    return pt;
  }
  function nearest(evt, maxPx = 10) {
    const pt = svgPoint(evt); if (!pt) return null;
    const scale = svg.getBoundingClientRect().width / W || 1;  // 화면 px → viewBox
    let best = null, bd = Infinity;
    for (const p of [...S.plans, ...(S.muni ? [S.muni] : [])]) {
      const d = Math.hypot(px(p.equity_far) - pt.x, py(p.served_rate) - pt.y);
      if (d < bd) { bd = d; best = p; }
    }
    return bd * scale <= maxPx ? best : null;
  }
  function showTip(p, anchorX, anchorY) {
    if (!p) { tip.hidden = true; return; }
    const name = p === S.muni ? '지자체안' : (p.algo_name || p.id);
    tip.innerHTML = `<b>${esc(name)}</b><br>충족률 ${pct(p.served_rate)} · 형평성 ${pct(p.equity_far)}`;
    tip.hidden = false;
    const box = svg.parentElement.getBoundingClientRect(), r = svg.getBoundingClientRect(), s = r.width / W;
    let left = (r.left - box.left) + anchorX * s + 10, top = (r.top - box.top) + anchorY * s - tip.offsetHeight - 6;
    left = Math.min(Math.max(2, left), box.width - tip.offsetWidth - 2);
    if (top < 2) top = (r.top - box.top) + anchorY * s + 10;
    tip.style.left = `${left}px`; tip.style.top = `${top}px`;
  }
  svg.addEventListener('pointermove', (e) => { const p = nearest(e); svg.style.cursor = p && (p !== S.muni || S.muni.export_url) ? 'pointer' : ''; showTip(p, p ? px(p.equity_far) : 0, p ? py(p.served_rate) : 0); });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; });
  svg.addEventListener('click', (e) => { const p = nearest(e, 12); if (p && (p !== S.muni || S.muni.export_url)) select(p.id, {moveSlider: true}); });

  function setSlider(v) { range.value = String(v); out.textContent = `${(Number(range.value) * 100).toFixed(1)}%`; }
  range.addEventListener('input', () => {
    setSlider(range.value);
    const p = pickByFloor(S.plans, Number(range.value));
    if (!p) { msg.textContent = '이 기준을 맞추는 안 없음'; msg.classList.remove('pp-err'); return; }
    msg.textContent = '';
    if (p.id !== S.selected?.id) select(p.id, {moveSlider: false});
  });

  function select(id, {moveSlider = true, silent = false} = {}) {
    const p = S.plans.find((x) => x.id === id) || (S.muni?.export_url && S.muni.id === id ? S.muni : null);
    if (!p) return false;
    S.selected = p; msg.textContent = ''; msg.classList.remove('pp-err');
    if (moveSlider) setSlider(Math.min(Number(range.max), Math.max(Number(range.min), Math.round(p.equity_far * 200) / 200)));
    render();
    if (!silent) opts.onSelect?.(p);
    return true;
  }

  function error(text) { msg.textContent = text; msg.classList.add('pp-err'); }

  async function load() {
    try {
      let data = opts.paretoData, base = location.href;
      if (!data) { const url = new URL(opts.paretoUrl, location.href); base = url.href; const r = await fetch(url); if (!r.ok) throw new Error(`${r.status}`); data = await r.json(); }
      else if (opts.paretoUrl) base = new URL(opts.paretoUrl, location.href).href;
      if (S.destroyed) return;
      S.data = data;
      S.plans = usable(data.plans).map((p) => ({...p, export_url: p.export_path ? (opts.exportUrl ? opts.exportUrl(p) : new URL(p.export_path, base).href) : null}));
      S.hull = hullPoints(S.plans);
      const mu = data.muni && Number.isFinite(data.muni.served_rate) && Number.isFinite(data.muni.equity_far) ? data.muni : null;
      const muPath = mu?.export_path || opts.muniExportPath || null;
      S.muni = mu ? {...mu, id: mu.id || 'muni', algo_name: '지자체안', is_muni: true, export_url: muPath ? (opts.exportUrl ? opts.exportUrl({...mu, export_path: muPath}) : new URL(muPath, base).href) : null} : null;
      const all = [...S.plans, ...(S.muni ? [S.muni] : [])];
      S.x = axis(all.map((p) => p.equity_far)); S.y = axis(all.map((p) => p.served_rate));
      const es = S.plans.map((p) => p.equity_far);
      range.min = String(Math.floor(Math.min(...es) * 200) / 200); range.max = String(Math.ceil(Math.max(...es) * 200) / 200); range.step = '0.005';
      const ex = (data.excluded || []).length;
      meta.textContent = [guName(data.gu), data.fleet != null ? `${data.fleet}대` : '', daysLabel(data.eval_days), `안 ${S.plans.length}개${ex ? ` · 제외 ${ex}` : ''}`].filter(Boolean).join(' · ');
      const init = opts.initialPlanId;
      const first = (init && (S.plans.find((p) => p.id === init) || (S.muni?.export_url && S.muni.id === init ? S.muni : null))) || [...S.hull].sort((a, b) => b.served_rate - a.served_rate || b.equity_far - a.equity_far || byId(a, b))[0] || S.plans[0];
      if (!S.plans.length) error('그릴 안이 없습니다');
      else if (opts.autoSelect === false && !(init && first?.id === init)) { setSlider(range.min); msg.textContent = '점을 누르거나 형평성 기준을 움직이면 그 안의 노선을 지도에 그립니다'; render(); }
      else if (first) select(first.id, {moveSlider: true});
    } catch (e) {
      error(`곡선 자료를 읽지 못했습니다 (${e.message})`);
    }
  }

  setOpen(readOpen(key));
  const ready = load();
  return {
    select: (id) => select(id, {moveSlider: true}),
    setOpen,
    getSelected: () => S.selected,
    clear() { S.selected = null; tip.hidden = true; render(); },
    error,
    ready,
    destroy() { S.destroyed = true; if (control && target.removeControl) target.removeControl(control); else root.remove(); },
  };
}
