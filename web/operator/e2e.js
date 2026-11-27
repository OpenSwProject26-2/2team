// 운영자 E2E: 지표 띠(곡선에서 고른 안의 주·보조 지표), 새 시뮬레이션 입력·진행률(실행 버튼), 확정 → 시민 경로.
// simulation.js(#59 시뮬레이션 화면)와는 이벤트로만 잇는다: 'pp:data'·'pp:select'를 듣고, window.ppRun + 'pp:reload'로 곡선을 바꾼다.
// 지표 정의는 confirm.json metrics와 같다(served_rate, equity_far, isolation, refused_rate, time_ratio_rode — 서버 d_pareto가 계산).
import {daysLabel, guName} from '/lab-panel/pareto_panel.js';

const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pct = (v, d = 1) => Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '—';
const pp = (a, b) => Number.isFinite(a) && Number.isFinite(b) ? `${a >= b ? '+' : '−'}${Math.abs((a - b) * 100).toFixed(1)}%p` : '—';
const day = d => `${+d.slice(4, 6)}/${+d.slice(6, 8)}`;
const mins = s => s == null ? '—' : s < 90 ? `${s}초` : `약 ${Math.round(s / 60)}분`;
async function api(path, body) {
  const r = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.message || r.statusText); e.data = d; throw e; }
  return d;
}

const st = {cfg: null, data: null, plan: null, region: null, run: '', runs: [], poll: 0, current: null};
const STAGE_KEYS = [['assemble', '조립'], ['generate', '생성'], ['allocate', '배분'], ['simulate', '시뮬레이션'], ['verify', '검증'], ['metrics', '지표']];

// ---------------------------------------------------------------- 지표 띠
function m07ok(p) { const [a, b] = String(p?.m07 || '').split('/'); return a && a === b; }
function renderStrip() {
  const p = st.plan, d = st.data, muni = d?.muni;
  const set = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
  $('metricStrip').classList.toggle('ms-empty', !p);
  if (!p) {
    set('msName', d ? '곡선에서 점을 고르세요' : '곡선 자료 없음');
    set('msSub', d ? `${esc(guName(d.gu))} ${esc(d.fleet)}대 · ${esc(daysLabel(d.eval_days))} · 안 ${(d.plans || []).length}개` :
      '이 구에는 연결된 충족률·형평성 곡선이 없습니다(새 시뮬레이션으로 만들기)');
    for (const id of ['msServed', 'msEquity', 'msRefused', 'msTime']) set(id, '—');
    set('msServedSub', muni ? `지자체안 ${pct(muni.served_rate)}` : '');
    set('msEquitySub', muni ? `지자체안 ${pct(muni.equity_far)}` : '');
    set('msRefusedSub', '45석이 차서 끝내 못 탄 비율');
    set('msM07', 'M07 —');
    $('msM07').className = 'ms-badge';
    $('msConfirm').disabled = true;
    $('msConfirm').title = '곡선에서 안을 먼저 고르세요';
    return;
  }
  const name = p.is_muni ? '지자체 셔틀안(비교용)' : (p.algo_name || p.id);
  set('msName', esc(name));
  set('msSub', [p.is_muni ? null : `형평 기준 ${esc((p.floors || []).join(', ') || '—')}`, p.rep ? esc(p.rep) : null,
    `${esc(guName(d.gu))} ${esc(d.fleet)}대`, esc(daysLabel(d.eval_days))].filter(Boolean).join(' · '));
  set('msServed', pct(p.served_rate));
  set('msServedSub', muni && !p.is_muni ? `지자체안 ${pct(muni.served_rate)} 대비 <b>${pp(p.served_rate, muni.served_rate)}</b>` : '지자체안 값');
  set('msEquity', pct(p.equity_far));
  set('msEquitySub', `소외율 ${pct(p.isolation)}` + (muni && !p.is_muni ? ` · 지자체안 ${pct(muni.equity_far)} 대비 <b>${pp(p.equity_far, muni.equity_far)}</b>` : ''));
  const rr = p.refused_rate;
  set('msRefused', Number.isFinite(rr) ? pct(rr) : '—');
  set('msRefusedSub', Number.isFinite(rr) ? `45석이 차서 끝내 못 탄 비율 · 하루 ${Math.round(p.refused_unserved)}명` : '자료 없음 · 45석이 차서 끝내 못 탄 비율');
  set('msTime', Number.isFinite(p.time_ratio_rode) ? `${p.time_ratio_rode.toFixed(2)}배` : '—');
  const ok = m07ok(p);
  set('msM07', `M07 검증 ${esc(p.m07 || '—')}`);
  $('msM07').className = `ms-badge ${ok ? 'ok' : 'bad'}`;
  const can = !p.is_muni && ok && Number.isFinite(p.served_rate) && st.cfg?.confirm;
  $('msConfirm').disabled = !can;
  $('msConfirm').title = can ? '확인 창 → 사전 검사 → 확정 묶음 → 시민 경로 적용' :
    p.is_muni ? '지자체안은 비교용이라 확정하지 않습니다' : !st.cfg?.confirm ? '확정 묶음 폴더가 설정되지 않았습니다' : 'M07을 통과한 안만 확정할 수 있습니다';
}
function renderHint() {
  const d = st.data, n = d?.excluded_count ?? (d?.excluded || []).length;
  const cur = st.current?.status === 'available' ? `적용 중: ${esc(st.current.algo_name)} (${esc((st.current.confirmed_at || '').slice(5, 16).replace('T', ' '))})` : '';
  const cz = st.current?.status === 'available' && st.citizen ? `<span class="ms-citizen" data-state="${esc(st.citizen.state)}">${esc(st.citizen.label)}</span>` : '';
  $('msHint').innerHTML = ['점을 누르면 지표가 바뀜', n ? `M07 미통과 제외 ${n}개` : '', cur, cz].filter(Boolean).join('<br>');
}

// ---------------------------------------------------------------- 실행 목록·진행률
function regionNow() { const el = $('sim-region'); return el && el.value !== 'all' ? el.value : null; }
async function loadRuns() {
  const region = regionNow();
  const sel = $('msRun');
  if (!region) { sel.innerHTML = '<option value="">—</option>'; sel.disabled = true; return; }
  const got = await api(`/api/d/runs?gu=${encodeURIComponent(region)}`).catch(() => ({runs: []}));
  st.runs = got.runs || [];
  const opts = [['', '기본 곡선(서버 --pareto-root)']];
  for (const r of st.runs) {
    const i = r.input || {}, when = (r.created_at || '').slice(5, 16).replace('T', ' ');
    const label = r.precomputed ? `미리 계산된 결과(요인 실험) · ${i.fleet}대` :
      `${when} ${i.setting === 'quick' ? '빠른' : '전체'} · ${(i.eval_days || []).map(day).join('·')} · ${i.fleet}대`;
    const stt = r.state === 'done' ? '' : r.state === 'failed' ? ' (실패)' : r.state === 'cancelled' ? ' (취소)' : ' (진행 중)';
    opts.push([r.run_id, label + stt]);
  }
  sel.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('');
  sel.disabled = false;
  sel.value = st.run && opts.some(o => o[0] === st.run) ? st.run : '';
}
function pickRun(runId) {
  st.run = runId || '';
  const r = st.runs.find(x => x.run_id === st.run);
  clearTimeout(st.poll);
  if (r && r.state !== 'done') { showProgress(r); return; }
  hideProgress();
  window.ppRun = r?.has_pareto ? st.run : '';
  window.dispatchEvent(new CustomEvent('pp:reload'));
}
function hideProgress() { $('msProgress').hidden = true; $('metricStrip').classList.remove('ms-running'); }
async function showProgress(r) {
  $('msProgress').hidden = false;
  $('metricStrip').classList.add('ms-running');
  $('msName').textContent = '실행 중';
  $('msSub').textContent = '끝나면 이 실행의 곡선으로 바뀝니다';
  try { r = await api(`/api/d/runs/${encodeURIComponent(r.run_id)}`); } catch (e) { $('msPgTitle').textContent = `작업을 읽지 못했습니다: ${e.message}`; return; }
  const i = r.input || {};
  $('msPgTitle').innerHTML = `<b>${esc(guName(r.gu))} · ${i.setting === 'quick' ? '빠른' : '전체'} 실행</b> · ${esc(i.fleet)}대 · 평가 ${(i.eval_days || []).map(day).join('·')} · ${esc(r.stage_label || r.state)}`;
  const seen = new Map();
  for (const s of r.stages || []) {
    const k = s.key.replace(/2$/, ''), o = seen.get(k) || {state: 'pending', done: 0, total: 0};
    const rank = {pending: 0, skipped: 1, done: 2, running: 3, failed: 4};
    if ((rank[s.state] ?? 0) >= (rank[o.state] ?? 0) || o.state === 'done') seen.set(k, {state: s.state === 'skipped' && o.state === 'done' ? 'done' : s.state, done: s.done, total: s.total, note: s.note});
  }
  $('msPgStages').innerHTML = STAGE_KEYS.map(([k, lab]) => {
    const s = seen.get(k) || {state: 'pending'};
    const n = s.state === 'running' && s.total ? ` ${s.done}/${s.total}` : '';
    return `<span class="pg-stage ${s.state}">${esc(lab)}${n}</span>`;
  }).join('<i>›</i>');
  $('msPgBar').style.width = `${Math.round((r.progress || 0) * 100)}%`;
  const fail = r.state === 'failed' || r.state === 'cancelled';
  $('msPgEta').textContent = r.state === 'done' ? '완료' : fail ? (r.state === 'failed' ? `실패: ${r.error || ''}` : '취소됨') :
    `${Math.round((r.progress || 0) * 100)}% · 지난 시간 ${mins(r.elapsed_s)} · 남은 시간 ${mins(r.eta_s)}`;
  $('msPgLog').textContent = (r.log_tail || []).slice(-2).join('\n');
  $('msPgCancel').hidden = fail || r.state === 'done';
  $('msPgCancel').onclick = async () => { $('msPgCancel').disabled = true; await api(`/api/d/runs/${encodeURIComponent(r.run_id)}/cancel`, {}).catch(() => null); $('msPgCancel').disabled = false; showProgress(r); };
  $('msPgRetry').hidden = !fail;
  $('msPgRetry').onclick = () => submitRun(r.input);
  if (r.state === 'done') {
    await loadRuns(); $('msRun').value = r.run_id;
    setTimeout(() => pickRun(r.run_id), 900);
    return;
  }
  if (!fail) st.poll = setTimeout(() => { if (st.run === r.run_id) showProgress(r); }, 2500);
}

// ---------------------------------------------------------------- 새 시뮬레이션 입력
function openRunDialog() {
  const c = st.cfg, dlg = $('runDialog');
  if (!c || c.status !== 'available') { toast('실행 결과 폴더가 설정되지 않아 실행할 수 없습니다(config/local_paths.toml)'); return; }
  const region = regionNow() || 'guro';
  $('rdGu').innerHTML = c.gus.map(g => `<option value="${g.id}" ${g.enabled ? '' : 'disabled'}>${esc(guName(g.id))}${g.enabled ? '' : ' — 준비 중'}</option>`).join('');
  $('rdGu').value = c.gus.find(g => g.id === region && g.enabled) ? region : (c.gus.find(g => g.enabled)?.id || '');
  const g = c.gus.find(x => x.id === $('rdGu').value);
  $('rdFleet').value = g?.default_fleet || '';
  $('rdFleetNote').textContent = g?.default_fleet ? `기본 = 지자체안 대수 ${g.default_fleet}대` : '지자체안 대수를 몰라 직접 넣습니다';
  $('rdSeed').value = c.strike_seed;
  $('rdDemand').textContent = `${day(c.demand_days[0])}~${day(c.demand_days.at(-1))} (실험 주간, 파이프라인은 이 기간만 지원)`;
  $('rdDays').innerHTML = c.eval_choices.map(d => `<label><input type="checkbox" name="rdDay" value="${d}">${day(d)}</label>`).join('') +
    c.test_week.map(d => `<label class="off" title="테스트 주간 — 고를 수 없음"><input type="checkbox" disabled>${day(d)}</label>`).join('');
  syncSetting();
  $('rdMsg').textContent = '';
  dlg.showModal();
}
function syncSetting() {
  const quick = $('runDialog').querySelector('input[name=rdSetting]:checked').value === 'quick';
  const c = st.cfg, g = c?.gus.find(x => x.id === $('rdGu').value);
  for (const el of document.querySelectorAll('input[name=rdDay]')) el.checked = quick ? el.value === '20261028' : el.value !== '20261026';
  $('rdSourceRow').hidden = quick;
  const pre = $('runDialog').querySelector('input[name=rdSource][value=precomputed]');
  pre.disabled = !g?.precomputed;
  if (!g?.precomputed) $('runDialog').querySelector('input[name=rdSource][value=new]').checked = true;
  $('rdSettingNote').textContent = quick ? '피더 순회(0,0)·피더 순회(1,1)·섹터 순회(1,1)·편도 노선 생성 후 순회(0,0) × 형평 기준 4단계(none + lo~hi 3단계) × 평가 1일, 병렬 3. 목표 15분' :
    '12종 × 형평 기준(none + lo~hi 8단계). 미리 계산된 결과가 있으면 그것을 엽니다(다시 계산하지 않음). 새로 돌리면 수 시간 걸립니다';
}
async function submitRun(input) {
  const f = $('runDialog');
  const body = input || {
    gu: $('rdGu').value, fleet: Number($('rdFleet').value) || null,
    strike: f.querySelector('input[name=rdStrike]:checked').value === 'full' ? {mode: 'full'} : {mode: 'ratio', ratio: Number($('rdRatio').value), seed: Number($('rdSeed').value)},
    demand_days: st.cfg.demand_days, eval_days: [...document.querySelectorAll('input[name=rdDay]:checked')].map(x => x.value),
    setting: f.querySelector('input[name=rdSetting]:checked').value,
    source: f.querySelector('input[name=rdSetting]:checked').value === 'full' ? f.querySelector('input[name=rdSource]:checked').value : 'new'};
  $('rdMsg').textContent = '입력 자료를 조립하는 중…';
  let r;
  try { r = await api('/api/d/runs', body); } catch (e) {
    const miss = e.data?.details?.missing;
    $('rdMsg').innerHTML = `<b>실행할 수 없음</b> · ${esc(e.message)}` + (miss ? `<ul>${miss.slice(0, 6).map(m => `<li>${esc(m.item)} — ${esc(m.how)}</li>`).join('')}</ul>` : '');
    if (input) toast(e.message);
    return;
  }
  if (f.open) f.close();
  toast(r.reused ? '같은 입력의 이전 결과를 엽니다(이전 결과 재사용)' : r.resumed ? '이전 작업을 같은 폴더에서 이어 돌립니다' : r.in_progress ? '같은 입력의 작업이 이미 진행 중입니다' : '실행을 대기열에 넣었습니다');
  if (r.gu !== regionNow() && $('sim-region')) { $('sim-region').value = r.gu; $('sim-region').dispatchEvent(new Event('change')); }
  st.run = r.run_id;
  await loadRuns();
  $('msRun').value = r.run_id;
  pickRun(r.run_id);
}

// ---------------------------------------------------------------- 확정
async function openConfirm() {
  const p = st.plan, d = st.data;
  if (!p || $('msConfirm').disabled) return;
  const dlg = $('confirmDialog');
  $('cfBody').innerHTML = '<p>확정 전 검사 중… (확정 묶음 다섯 파일을 모으고 시민 셔틀 내보내기 검사를 같은 코드로 돌립니다)</p>';
  $('cfGo').disabled = true; $('cfGo').hidden = false;
  $('cfResult').innerHTML = '';
  dlg.showModal();
  const q = `region=${encodeURIComponent(d.gu)}&id=${encodeURIComponent(p.id)}${st.run ? `&run=${encodeURIComponent(st.run)}` : ''}`;
  let pv;
  try { pv = await api(`/api/d/confirm/preview?${q}`); } catch (e) { $('cfBody').innerHTML = `<p class="cf-bad">검사할 수 없음: ${esc(e.message)}</p>`; return; }
  const M = pv.metrics || {};
  $('cfBody').innerHTML = `<dl class="cf-grid"><dt>안</dt><dd><b>${esc(pv.algo_name)}</b> · 형평 기준 ${esc((pv.floors || []).join(', '))}</dd>` +
    `<dt>지표</dt><dd>충족률 <b>${pct(M.served_rate)}</b> · 형평성 <b>${pct(M.equity_far)}</b> · 소외율 ${pct(M.isolation)} · 만차 거절률 ${pct(M.refused_rate)} · 이동시간 ${Number.isFinite(M.time_ratio_rode) ? M.time_ratio_rode.toFixed(2) + '배' : '—'}</dd>` +
    `<dt>대수</dt><dd>${esc(pv.vehicles)}대(가용 ${esc(pv.fleet)}대) · 노선 ${esc(pv.routes)}개</dd>` +
    `<dt>운행 시간</dt><dd>${esc(pv.service || '—')} (M05 시간표, ${esc(pv.service_date)} 기준)</dd>` +
    `<dt>운행안</dt><dd>${esc(pv.plan_id)}@${esc(pv.revision)} · ${esc((pv.plan_sha256 || '').slice(0, 12))} · M07 ${esc(pv.m07)}</dd></dl>` +
    `<ul class="cf-checks">${(pv.checks || []).map(c => `<li class="${c.ok ? 'ok' : 'bad'}">${c.ok ? '✓' : '✗'} ${esc(c.name)} <small>${esc(c.detail)}</small></li>`).join('')}</ul>` +
    (pv.ok ? '' : '<p class="cf-bad">검사에 실패해 확정할 수 없습니다.</p>') +
    '<p class="cf-note">확정 = 시민 길찾기의 파업 셔틀로 쓸 운행안을 정하는 것(실제 운행 승인 아님). 기록은 지우지 않고 쌓입니다.</p>';
  $('cfGo').disabled = !pv.ok;
  $('cfGo').onclick = async () => {
    $('cfGo').disabled = true;
    $('cfResult').innerHTML = '<p>확정 묶음을 쓰는 중…</p>';
    try {
      const r = await api('/api/d/confirm', {region: d.gu, run: st.run || null, id: p.id});
      const ui = r.citizen?.ui;
      $('cfResult').innerHTML = `<p class="cf-ok"><b>확정됨</b> · ${esc(r.confirm_id)}</p><p id="cfCitizen" data-state="${esc(r.citizen?.state || '')}">${esc(r.citizen?.label || '')}</p>` +
        (ui ? `<p><a class="cf-link" href="${esc(ui)}" target="_blank" rel="noopener">시민 경로 화면 열기 ↗</a></p>` : '<p class="cf-note">시민 경로 화면 주소가 설정되지 않았습니다(config [citizen] ui).</p>');
      $('cfGo').hidden = true;
      loadCurrent();
      if (r.citizen?.state === 'requested') watchCitizen(d.gu, r.confirm_id);
    } catch (e) { $('cfResult').innerHTML = `<p class="cf-bad">확정 실패: ${esc(e.message)}</p>`; $('cfGo').disabled = false; }
  };
}
async function loadCurrent() {
  const region = regionNow();
  st.current = region && st.cfg?.confirm ? await api(`/api/d/confirm/current?region=${encodeURIComponent(region)}`).catch(() => null) : null;
  st.citizen = st.current?.status === 'available' && st.cfg?.citizen_api ?
    await api(`/api/d/confirm/citizen?region=${encodeURIComponent(region)}&confirm_id=${encodeURIComponent(st.current.confirm_id)}`).catch(() => null) : null;
  renderHint();
}
// 확정 뒤 시민 경로 반영(파업 그래프 다시 빌드·교체, 수 분)을 따라간다. 완료 = 시민 쪽 활성 그래프의 확정 ID가 이 확정과 같을 때.
function watchCitizen(region, cid) {
  clearTimeout(st.citizenPoll);
  const tick = async () => {
    const c = await api(`/api/d/confirm/citizen?region=${encodeURIComponent(region)}&confirm_id=${encodeURIComponent(cid)}`).catch(() => null);
    const el = $('cfCitizen');
    if (c && el) { el.textContent = c.label; el.dataset.state = c.state; }
    if (c && st.current?.confirm_id === cid) { st.citizen = c; renderHint(); }
    if (!c || ['applying', 'pending'].includes(c.state)) st.citizenPoll = setTimeout(tick, 5000);
  };
  tick();
}

function toast(text) { const t = $('toast'); if (!t) return; t.textContent = text; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 3500); }

// ---------------------------------------------------------------- 시작
async function init() {
  st.cfg = await api('/api/d/runs/config').catch(() => null);
  $('newRunButton').onclick = openRunDialog;
  $('newRunButton').title = st.cfg?.status === 'available' ? '입력 → 데이터 자동 조립 → 파이프라인 실행' : '실행 결과 폴더 미설정(연결 안 됨)';
  $('msConfirm').onclick = openConfirm;
  $('msRun').onchange = () => pickRun($('msRun').value);
  $('rdCancel').onclick = () => $('runDialog').close();
  $('rdGo').onclick = e => { e.preventDefault(); submitRun(); };
  $('rdGu').onchange = syncSetting;
  for (const el of document.querySelectorAll('input[name=rdSetting]')) el.onchange = syncSetting;
  for (const el of document.querySelectorAll('input[name=rdStrike]')) el.onchange = () => { $('rdRatio').disabled = $('rdSeed').disabled = el.value === 'full' && el.checked; };
  $('cfClose').onclick = () => $('confirmDialog').close();
  window.addEventListener('pp:data', e => { st.data = e.detail?.data || null; st.region = e.detail?.region; renderStrip(); renderHint(); });
  window.addEventListener('pp:select', e => { st.plan = e.detail?.plan || null; if (e.detail?.data) st.data = e.detail.data; renderStrip(); });
  const qs = new URLSearchParams(location.search);
  st.initialRun = qs.get('run') || '';                // 주소 ?run=<작업 ID>로 그 실행의 곡선을 바로 연다
  if (st.initialRun) window.ppRun = st.initialRun;
  const watchRegion = async () => {
    clearTimeout(st.poll); hideProgress();
    st.run = ''; await loadRuns(); loadCurrent();
    const want = st.initialRun;
    if (want && st.runs.some(r => r.run_id === want)) {       // ?run=은 그 작업의 구를 고른 뒤에 한 번만 쓴다
      st.initialRun = ''; $('msRun').value = want; pickRun(want);
    } else if (window.ppRun && !want) { window.ppRun = ''; window.dispatchEvent(new CustomEvent('pp:reload')); }
  };
  $('sim-region')?.addEventListener('change', watchRegion);
  document.addEventListener('d-district-selected', () => setTimeout(watchRegion, 50));
  let wasSim = false;
  new MutationObserver(() => {
    const on = document.querySelector('.app').classList.contains('sim-active');
    if (on && !wasSim) watchRegion();
    wasSim = on;
  }).observe(document.querySelector('.app'), {attributes: true, attributeFilter: ['class']});
  renderStrip();
}
init();
