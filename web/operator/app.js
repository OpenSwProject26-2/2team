/* D 운영자 지도 개발 화면. API와 지도 데이터의 출처를 분리한다. */
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const colors = {strike:'#ed3d51', city:'#2563eb', gyeonggi:'#f39b28', village:'#17a56c', metro:'#864ad7', shuttle:'#0eaaad', unknown_bus:'#64748b', unknown:'#64748b'};
const labels = {strike:'파업 노선', city:'정상 시내버스', gyeonggi:'경기버스', village:'마을버스', metro:'지하철', shuttle:'대체 셔틀', unknown_bus:'미분류 버스', unknown:'미분류 노선'};
const demoNormalKinds = new Set(['city','gyeonggi','village','metro']);
const SEOUL_BOUNDS=[[126.764433,37.428542],[127.18377,37.701479]]; // districts_all.geojson의 25개 구 범위
const state = {mode:'demo', district:'all', extent:'inside', boundary:null, routeStops:null, observedDate:'2026-10-26', observedMode:false, listmode:'strike', selectedRouteId:null, hoveredStopId:null, hoverMarker:null, query:'', hour:'07', demoDate:'2026-11-13', demandMode:'total', visible:new Set(['strike','city','gyeonggi','village','metro','shuttle','shuttleStops','unknown_bus','unknown','busStops','metroStops','districtBoundary','observedDemand','unmetDemand']), operatingKinds:new Set(['city','gyeonggi','village','metro']), strikeRouteIds:new Set(), demoStrikeRouteIds:new Set(), demoStrikeInitialized:false, summary:null, routes:[], geo:null, demand:null, demandRequestSeq:0, loadRequestSeq:0, scenarioRoutes:null, scenarioResult:null, scenarioBefore:null, unmetValues:null, unmetAnimation:0, sources:null, map:null, maplibregl:null, popup:null, overviewBoundary:null, overviewRates:null, overviewBound:false};

// D에서 직접 작성한 SVG 픽토그램. 타사 아이콘 패키지의 자산이 아니다.
const busStopSvg=(color,shape='circle')=>`<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">${shape==='diamond'?`<path d="M24 2 46 24 24 46 2 24Z" fill="#fff" stroke="${color}" stroke-width="3"/>`:`<circle cx="24" cy="24" r="22" fill="#fff" stroke="${color}" stroke-width="3"/>`}<rect x="12" y="12" width="24" height="22" rx="5" fill="${color}"/><rect x="15" y="15" width="18" height="9" rx="2" fill="#fff"/><path d="M12 26h24" stroke="#fff" stroke-width="2"/><circle cx="18" cy="30" r="2" fill="#fff"/><circle cx="30" cy="30" r="2" fill="#fff"/></svg>`;
const stopSvg = {
  bus:busStopSvg('#64748b'),city:busStopSvg('#2563eb'),gyeonggi:busStopSvg('#e88a18','diamond'),village:busStopSvg('#16a66c'),mixed:`<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#fff" stroke="#475569" stroke-width="3"/><rect x="12" y="12" width="24" height="22" rx="5" fill="#475569"/><rect x="15" y="15" width="18" height="9" rx="2" fill="#fff"/><circle cx="18" cy="30" r="2" fill="#fff"/><circle cx="30" cy="30" r="2" fill="#fff"/><circle cx="39" cy="10" r="8" fill="#fff" stroke="#475569" stroke-width="2"/><path d="M39 6v8m-4-4h8" stroke="#475569" stroke-width="2"/></svg>`,
  metro:`<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#fff" stroke="#6334aa" stroke-width="3"/><rect x="13" y="11" width="22" height="24" rx="7" fill="#864ad7"/><rect x="16" y="16" width="16" height="9" rx="2" fill="#f3eaff"/><circle cx="18" cy="30" r="2" fill="#fff"/><circle cx="30" cy="30" r="2" fill="#fff"/><path d="m17 36-3 4m17-4 3 4" stroke="#6334aa" stroke-width="3"/></svg>`
};
async function addStopIcon(map, kind) {
  const icon = new Image();
  icon.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(stopSvg[kind])}`;
  await icon.decode();
  map.addImage(`d-${kind}-icon`,icon,{pixelRatio:2});
}
function clearStopHover() { state.hoverMarker?.remove(); state.hoverMarker=null; state.hoveredStopId=null; }
function showStopHover(feature) {
  if(state.hoveredStopId===feature.id)return;
  clearStopHover();
  const kind=feature.properties.kind==='metro'?'metro':feature.properties.stop_kind||'bus';
  const element=document.createElement('div');element.className='stop-hover-marker';
  element.innerHTML=stopSvg[kind]+`<span class="name">${esc(feature.properties.name||feature.properties.stop_id)}</span>`;
  state.hoverMarker=new state.maplibregl.Marker({element,anchor:'left',offset:[-14,0]}).setLngLat(feature.geometry.coordinates).addTo(state.map);
  state.hoveredStopId=feature.id;
  requestAnimationFrame(()=>element.classList.add('active'));
}

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmt(value) { return value == null ? '미연결' : Number(value).toLocaleString('ko-KR'); }
function toast(message) { const node=$('#toast'); node.textContent=message; node.classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>node.classList.remove('show'),3400); }
function sourceText() { return state.mode==='demo' ? '합성 DEMO' : 'A GTFS · 근사 운행시각'; }
function effectiveKind(route) {if(state.mode==='real')return state.strikeRouteIds.has(route.route_id)?'strike':route.kind;return route.kind==='strike'&&!state.demoStrikeRouteIds.has(route.route_id)?'city':route.kind;}
function routeMatches(route,q=state.query.trim().toLocaleLowerCase()) {return !q||route.short_name.toLocaleLowerCase().includes(q)||route.agency.toLocaleLowerCase().includes(q)||route.route_id.toLocaleLowerCase().includes(q);}
function setStrikingRoutes(routeIds,striking) {
  for(const id of routeIds)striking?state.strikeRouteIds.add(id):state.strikeRouteIds.delete(id);
  $('#statStrike').textContent=fmt(state.strikeRouteIds.size);
  renderRoutes();renderDetail();renderDisruptionControls();updateMapData();
}
async function setDemoStrikingRoute(routeId,striking) {
  const previous=new Set(state.demoStrikeRouteIds);
  striking?state.demoStrikeRouteIds.add(routeId):state.demoStrikeRouteIds.delete(routeId);
  try{await recalculateScenario();}
  catch(error){state.demoStrikeRouteIds=previous;renderAll();updateMapData();toast(`파업 노선 변경 실패: ${error.message}`);}
}
async function get(path) { const response=await fetch(path); const data=await response.json(); if (!response.ok) throw new Error(`${data.code||response.status}: ${data.message||'요청 실패'}`); return data; }
async function post(path,payload) { const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(`${data.code||response.status}: ${data.message||'요청 실패'}`);return data; }
function url(path, params={}) { const query=new URLSearchParams({mode:state.mode,district:state.mode==='real'?state.district:'all',extent:state.extent,...params}); return `${path}?${query}`; }

async function loadData() {
  const seq=++state.loadRequestSeq;
  const mode=state.mode;
  $('#mapStatus').textContent='데이터를 불러오는 중…';
  const [summary, network, sources] = await Promise.all([
    get(url('/api/d/dashboard')), get(url('/api/d/network')), get('/api/d/sources')
  ]);
  if (seq!==state.loadRequestSeq || mode !== state.mode || summary.snapshot_id !== network.snapshot_id || summary.snapshot_id !== network.map.snapshot_id) return;
  state.summary=summary; state.routes=network.routes; state.geo=network.map; state.routeStops=network.route_stops; state.boundary=network.boundary; state.sources=sources;
  state.schematicBase=buildSchematicBase();
  const stopServices=new Map();
  for(const route of state.routes){if(!['city','gyeonggi','village'].includes(route.kind))continue;for(const id of route.all_stop_ids||route.representative_stop_ids||route.stop_ids||[]){const key=String(id);if(!stopServices.has(key))stopServices.set(key,new Set());stopServices.get(key).add(route.kind);}}
  state.stopServices=stopServices;
  if(mode==='demo'&&!state.demoStrikeInitialized){state.demoStrikeRouteIds=new Set(state.routes.filter(r=>r.kind==='strike').map(r=>r.route_id));state.demoStrikeInitialized=true;}
  if(mode==='demo'&&!state.scenarioRoutes)state.scenarioRoutes=state.routes.filter(r=>r.kind==='shuttle').map(r=>({route_id:r.route_id,stop_ids:r.stop_ids,vehicles:r.assigned_vehicles,active:true}));
  if(mode==='demo')restoreCustomRoutes();
  if (!state.routes.some(r=>r.route_id===state.selectedRouteId)) state.selectedRouteId=null;
  await loadDemand();
  if(seq!==state.loadRequestSeq)return;
  renderAll();
  updateMapData(true);
  document.dispatchEvent(new Event('d-base-data-ready'));
}
async function loadOverview(){
  const [boundary,summary,options]=await Promise.all([get('/data/districts_all.geojson'),get('/api/d/simulation/seoul?variant=affected_maeul_gg'),get('/api/d/simulation/options')]);
  const centers=new Map(options.regions.map(region=>[region.id,region.center]));
  state.overviewBoundary=boundary;
  state.overviewRates=summary.districts.filter(row=>row.region&&row.metrics?.rate_percent!=null&&centers.has(row.region)).map(row=>({type:'Feature',geometry:{type:'Point',coordinates:centers.get(row.region)},properties:{region:row.region,name:row.name,rate:row.metrics.rate_percent,label:`${Number(row.metrics.rate_percent).toFixed(1)}%`}}));
  state.overviewBound=false;updateOverview();
}
async function loadDemand() {
  const seq=++state.demandRequestSeq;
  const mode=state.mode;
  const snapshotId=state.summary?.snapshot_id;
  const activeNormalIds=state.routes.filter(r=>demoNormalKinds.has(r.kind)&&state.operatingKinds.has(r.kind)).map(r=>r.route_id);
  const activeShuttles=state.visible.has('shuttle')?state.scenarioRoutes?.filter(r=>r.active).map(({route_id,stop_ids,vehicles})=>({route_id,stop_ids,vehicles}))||[]:[];
  try {
    if(mode==='real'&&state.observedMode&&state.district!=='all'){
      const data=await get(`/api/d/transit/observed?${new URLSearchParams({district:state.district,date:state.observedDate,hour:state.hour})}`);
      if(seq!==state.demandRequestSeq||mode!==state.mode)return false;
      state.demand={status:'observed',...data};state.scenarioResult=null;return true;
    }
    const data=mode==='demo'&&state.demand?.snapshot_id===snapshotId&&state.demand.hour===state.hour&&state.demand.date===state.demoDate?state.demand:await get(url('/api/d/demand',{hour:state.hour,date:state.demoDate,demand_mode:state.demandMode}));
    if (seq!==state.demandRequestSeq||mode!==state.mode||data.snapshot_id!==snapshotId) return false;
    const result=mode==='demo'?await post('/api/d/demo/scenario/evaluate',{hour:state.hour,date:state.demoDate,strike_route_ids:[...state.demoStrikeRouteIds],active_normal_route_ids:activeNormalIds,routes:activeShuttles}):null;
    if (seq!==state.demandRequestSeq||mode!==state.mode) return false;
    state.demand=data;
    state.scenarioResult=result;
    return true;
  } catch (error) {
    if(seq!==state.demandRequestSeq)return;
    if (mode==='demo') throw error;
    state.demand=null;
    state.scenarioResult=null;
  }
}
function restoreCustomRoutes() {
  for(const entry of state.scenarioRoutes||[]) {
    const stops=entry.stop_ids.map(id=>state.geo.features.find(f=>f.properties.feature_type==='stop'&&f.properties.stop_id===id));
    if(stops.some(stop=>!stop))continue;
    const existing=state.routes.find(route=>route.route_id===entry.route_id);
    if(existing){existing.assigned_vehicles=entry.vehicles;if(entry.edited)Object.assign(existing,{stop_ids:entry.stop_ids,coordinates:stops.map(stop=>stop.geometry.coordinates),geometry_status:'synthetic_unverified'});continue;}
    state.routes.push({route_id:entry.route_id,short_name:entry.name||entry.route_id,agency:'사용자 가상 셔틀',kind:'shuttle',geometry_status:'synthetic_unverified',stop_ids:entry.stop_ids,assigned_vehicles:entry.vehicles,coordinates:stops.map(stop=>stop.geometry.coordinates)});
  }
}
async function recalculateScenario() { state.scenarioBefore=state.scenarioResult;if(!await loadDemand())return;renderAll();updateMapData(); }
async function editShuttle(entry,changes) {
  const before={stop_ids:[...entry.stop_ids],vehicles:entry.vehicles,edited:entry.edited};
  const route=state.routes.find(item=>item.route_id===entry.route_id);
  const previousRoute=route?{stop_ids:route.stop_ids,coordinates:route.coordinates,assigned_vehicles:route.assigned_vehicles,geometry_status:route.geometry_status}:null;
  const stops=new Map(pointFeatures().map(f=>[f.properties.stop_id,f.geometry.coordinates]));
  const next={...before,...changes};
  if(!Array.isArray(next.stop_ids)||next.stop_ids.length<2||next.stop_ids.length>10||new Set(next.stop_ids).size!==next.stop_ids.length||next.stop_ids.some(id=>!stops.has(id))){toast('정차 ID를 2~10개, 중복 없이 쉼표로 입력하세요.');return false;}
  if(!Number.isInteger(next.vehicles)||next.vehicles<1||next.vehicles>state.summary.fleet.available){toast('차량 수는 1대 이상, 가용 차량 이하로 입력하세요.');return false;}
  const assigned=state.scenarioRoutes.filter(item=>item.active).reduce((sum,item)=>sum+(item===entry?next.vehicles:item.vehicles),0);
  if(assigned>state.summary.fleet.available){toast(`배정 차량이 가용 ${state.summary.fleet.available}대를 초과합니다.`);return false;}
  const pathChanged=next.stop_ids.join('|')!==before.stop_ids.join('|');
  Object.assign(entry,{...next,edited:Boolean(before.edited||pathChanged)});
  if(route){Object.assign(route,{stop_ids:next.stop_ids,assigned_vehicles:next.vehicles});if(pathChanged)Object.assign(route,{coordinates:next.stop_ids.map(id=>stops.get(id)),geometry_status:'synthetic_unverified'});}
  try{await recalculateScenario();return true;}
  catch(error){Object.assign(entry,before);if(route)Object.assign(route,previousRoute);renderAll();updateMapData();toast(`셔틀 조건 변경 실패: ${error.message}`);return false;}
}
function renderAll() {
  const s=state.summary;
  if (!s) return;
  const hasRoadEstimate=state.mode==='demo'&&state.routes.some(route=>route.geometry_status==='osm_osrm_driving_estimate');
  $('.topnotice').textContent=state.mode==='demo'?(hasRoadEstimate?'OSM 자동차 도로망 추정 선형 · 정류장·수요는 가상':'도로 선형 미연결 · 정류장·수요는 가상'):'A GTFS 지도 자료 · 실제 운행 승인 정보 아님';
  $('.brand .tag').textContent=state.mode==='demo'?'INTERACTIVE DEMO':'A GTFS PREVIEW';
  $('#dataSourceChip').innerHTML=`● ${esc(sourceText())} <b>${esc(state.mode==='demo'?state.demoDate:s.service_date)}</b>`;
  $('.stat.violet small').textContent=state.mode==='demo'?'선택 시간대 가상 OD':state.demand?.status==='observed'?'관측 승차(k5·일부 제거)':'집계 OD 미연결';
  $('#statStrike').textContent=state.mode==='demo'?fmt(state.demoStrikeRouteIds.size):fmt(state.strikeRouteIds.size);
  $('.stat.blue strong').textContent=fmt(s.fleet?.available);
  $('#statDemand').textContent=state.mode==='demo'&&state.scenarioResult?fmt(state.scenarioResult.total):state.demand?.status==='observed'?fmt(state.demand.totals.board):state.demand?.status==='available'?fmt(state.demand.total):'미연결';
  const result=state.mode==='demo'?state.scenarioResult:null;
  $('#statServed').textContent=result?fmt(result.served):'미계산'; $('#statUnmet').textContent=result?fmt(result.unmet):'미계산';
  $('#statRate').textContent=result?`가상 정원 배분 · ${(result.coverage_rate*100).toFixed(1)}%`:'B/M07 평가 미연결';
  $('#coveragePct').textContent=result?`${(result.coverage_rate*100).toFixed(1)}% · 가상`:'미계산'; $('#coverageBar').style.width=result?`${result.coverage_rate*100}%`:'0%';
  $('.fleet-data').innerHTML=`<div><small>가용</small><strong>${fmt(s.fleet?.available)}</strong></div><div><small>배정</small><strong>${fmt(result?.assigned_vehicles)}</strong></div><div><small>미배정</small><strong>${s.fleet&&result?fmt(s.fleet.available-result.assigned_vehicles):'미연결'}</strong></div>`;
  $('.fleet-notice').textContent=s.fleet?`차량당 정원 ${s.fleet.capacity_per_vehicle}명 · ${s.fleet.window} · 가상 입력`:'A/B/C 차량 자료 미연결';
  const sourceRows=['A','B','C','M07','transport_demand'].map(team=>`<div style="display:flex;justify-content:space-between;gap:6px;margin-top:7px"><span>${team==='transport_demand'?'수단별 정류장 수요':team}</span><b>${state.sources?.[team]?.status==='ready'?'연결':'미연결'}</b></div>`).join('');
  renderScenarioControls(sourceRows);
  $('#mapStatus').textContent=state.mode==='real'&&state.district==='all'?`서울 전체 · 25개 구 경계 · 저장된 충족률 ${state.overviewRates?.length??0}개 구만 표시 (마을·경기 접근 조건)`:`${sourceText()} · ${s.snapshot_id} · 실제 운행/탑승 보장 아님`;
  $('.map-head-title').innerHTML=`교통망 지도 <span style="color:#9aa6b8;font-size:11px;font-weight:500">· 실제 서울 지도 배경 / ${esc(sourceText())}</span>`;
  $('.map-corner').innerHTML=`<span>◉ ${esc(sourceText())}</span><span>${state.mode==='demo'?(hasRoadEstimate?'실선=OSRM 자동차도로 추정 · 선형 미확인 노선도 실선 개략 표시 · 겹친 셔틀 선 투명':'실선=정차점 연결 개략도 · 도로 경로 미연결'):(state.routes.some(r=>r.geometry_status==='osrm_estimate')?'버스 옅은 실선=OSRM 도로 기반 추정 · ': '')+(state.routes.some(r=>r.geometry_status==='rail_gis_verified_corridor')?'지하철 실선=GIS 물리 선로 · ':'')+'실선=GTFS 정차 관계 개략도(검증 선형 미제공)'}</span>`;
  $('.map-source').textContent=`${sourceText()} · ${s.feed_version||'버전 미제공'}`;
  $('.report').innerHTML=`⚠ <b>출처 안내:</b> ${state.mode==='demo'?`서울 지도 배경은 OpenFreeMap/OSM, 버스 선형은 ${hasRoadEstimate?'OSRM/OSM 자동차 도로망 추정':'도로 미연결 정차점 연결선'}입니다. 정류장·OD·차량은 합성이고 수송률은 정차 순서·시간당 정원만 적용한 가상 배분입니다. 버스 통행 적합성·실제 운행은 검증되지 않았습니다.`:'A GTFS의 노선·정류장과 정차 관계를 표시합니다. 실선은 역·정류장을 이은 개략도일 수 있으며 실제 도로·철로가 아닙니다. OD·파업 범위·셔틀·평가는 미연결이고 버스 시간표는 근사 생성 자료일 수 있습니다.'}`;
  $('.note').textContent=s.warnings.join(' · ');
  $('.map-legend .legend-foot').textContent=state.demand?.status==='observed'?'정상일 관측 승차 k5 · 5명 미만 칸 제거 · 파업 잔여 수요와 다름':state.mode==='demo'?state.demandMode==='total'?'거점별 연한 원=전체 가상 OD · 진한 원=미충족 · 크기·진하기는 인원에 비례':state.demandMode==='unmet'?'거점별 진한 원=미충족 가상 OD · 크기·진하기는 인원에 비례':state.demandMode==='affected'?'파업 영향 수요는 미계산':'거점별 원=선택한 가상 수요 · 크기·진하기는 인원에 비례':'실제 파업 영향 수요·미충족 수요는 미계산';
  $$('.map-legend .leg-row').at(-1).innerHTML=`<span style="height:17px;width:17px;background:#e878ac66;border:1px solid #d85790;border-radius:50%"></span>${state.demand?.status==='observed'?'관측 승차 k5 원':'거점별 합성 수요 원'}`;
  $('#overlapRoutes').textContent=state.mode==='demo'?'겹친 셔틀 선 숨김':'중복 판정 불가';
  $('#overlapLabel').textContent=state.mode==='demo'?'지도 효과':'미분석';
  $('#overlapFill').style.width='0%';
  $('#overlapList').textContent=state.mode==='demo'?'보이는 정상 노선과 좌표가 같은 셔틀 선분만 투명 처리합니다. 해당 구간에 겹치는 모든 정상 노선을 끄면 다시 보입니다.':'방향별 검증 선형·중복 분석 결과가 필요합니다.';
  $('.overlap-list + p').textContent='합성 지도 표시 효과입니다. 실제 노선 중복률·도로 공유·버스 운행 가능성의 판정이 아닙니다.';
  $('#selectShuttle').disabled=state.mode!=='demo';
  renderRoutes(); renderDetail(); renderLayers(); renderDemandTable(); renderChart(); renderSourceControls();renderDisruptionControls();
}
function renderScenarioControls(sourceRows) {
  const panel=$('#scenarioControls');panel.replaceChildren();
  if(state.mode!=='demo') {panel.innerHTML=`<div class="smalltext">A GTFS에서는 노선 파업 지정과 교통수단 표시를 바꿀 수 있습니다. 실제 파업 OD·수단 전환·M05 수요 변화는 입력과 실행 결과가 없어 미계산입니다. 저장된 C/B 결과는 시뮬레이션 결과 표시에서 별도로 확인합니다.</div><div class="smalltext" style="border-top:1px solid #e6ebf3;margin-top:9px;padding-top:7px"><b>데이터 연결 상태</b>${sourceRows}</div>`;return;}
  const hint=document.createElement('p');hint.className='smalltext';hint.textContent=`합성 DEMO 운행 조건 · 전체 가상 OD는 파업 상태와 관계없이 유지합니다. 복구된 파업 노선 ${state.scenarioResult?.restored_strike_route_ids.length??0}개, 정상 노선 ${state.scenarioResult?.active_normal_route_ids.length??0}개, 셔틀 순서로 정차 방향과 가정 시간당 정원을 배분합니다. 운행 체크와 지도 표시 옵션의 교통수단 체크는 함께 동작합니다. B 시뮬레이션 결과가 아닙니다.`;panel.append(hint);
  const dateLabel=document.createElement('label');dateLabel.className='smalltext';dateLabel.textContent='합성 수요 날짜 ';
  const demoDateSelect=document.createElement('select');demoDateSelect.id='demoDate';demoDateSelect.className='district-date';
  for(const day of state.summary.demo_dates||[state.summary.service_date]){const option=document.createElement('option');option.value=day;option.textContent=day;demoDateSelect.append(option);}
  demoDateSelect.value=state.demoDate;demoDateSelect.onchange=async()=>{state.demoDate=demoDateSelect.value;state.scenarioBefore=null;if(await loadDemand()){renderAll();updateMapData();}};
  dateLabel.append(demoDateSelect);panel.append(dateLabel);
  const strikeControls=document.createElement('div');strikeControls.className='scenario-operating';
  for(const route of state.routes.filter(item=>item.kind==='strike')){const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.checked=state.demoStrikeRouteIds.has(route.route_id);input.onchange=()=>setDemoStrikingRoute(route.route_id,input.checked);label.append(input,document.createTextNode(`${route.short_name} 파업`));strikeControls.append(label);}
  panel.append(strikeControls);
  const modes=document.createElement('div');modes.className='scenario-operating';
  for(const [kind,name] of [['city','시내버스'],['village','마을버스'],['gyeonggi','경기버스'],['metro','지하철']]){
    const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.checked=state.operatingKinds.has(kind);
    input.onchange=async()=>{const was=state.operatingKinds.has(kind);input.checked?state.operatingKinds.add(kind):state.operatingKinds.delete(kind);input.checked?state.visible.add(kind):state.visible.delete(kind);try{await recalculateScenario();updateVisibility();}catch(error){was?state.operatingKinds.add(kind):state.operatingKinds.delete(kind);was?state.visible.add(kind):state.visible.delete(kind);input.checked=was;renderLayers();updateVisibility();toast(error.message);}};
    label.append(input,document.createTextNode(`${name} 운행`));modes.append(label);
  }
  panel.append(modes);
  for(const entry of state.scenarioRoutes) {
    const row=document.createElement('div');row.className='scenario-row';
    const route=state.routes.find(item=>item.route_id===entry.route_id);
    row.innerHTML=`<span class="scenario-swatch" style="background:#0eaaad"></span><span class="scenario-name"><b>${esc(entry.name||route?.short_name||entry.route_id)}</b><small>${entry.vehicles}대 · ${entry.stop_ids.length}개 정차점 · ${entry.active?'운행 가정':'시나리오 제외'}</small></span>`;
    const toggle=document.createElement('button');toggle.type='button';toggle.className='pill';toggle.textContent=entry.active?'제외':'다시 추가';toggle.onclick=async()=>{entry.active=!entry.active;try{await recalculateScenario();}catch(error){entry.active=!entry.active;toast(`셔틀 운행 변경 실패: ${error.message}`);}};row.append(toggle);panel.append(row);
    const edit=document.createElement('div');edit.className='smalltext';edit.style.cssText='display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:3px 0 10px 22px';
    const fleetInput=document.createElement('input');fleetInput.type='number';fleetInput.min='1';fleetInput.max=String(state.summary.fleet.available);fleetInput.value=String(entry.vehicles);fleetInput.style.width='53px';fleetInput.setAttribute('aria-label',`${entry.route_id} 투입 차량 수`);fleetInput.onchange=async()=>{if(!await editShuttle(entry,{vehicles:Number(fleetInput.value)}))fleetInput.value=String(entry.vehicles);};
    const pathInput=document.createElement('input');pathInput.type='text';pathInput.value=entry.stop_ids.join(', ');pathInput.style.width='180px';pathInput.setAttribute('aria-label',`${entry.route_id} 정차 ID 순서`);pathInput.title='정차 ID를 쉼표로 구분해 순서대로 입력';pathInput.onchange=async()=>{if(!await editShuttle(entry,{stop_ids:pathInput.value.split(',').map(id=>id.trim()).filter(Boolean)}))pathInput.value=entry.stop_ids.join(', ');};
    edit.append('차량',fleetInput,'정차 ID 순서',pathInput);panel.append(edit);
  }
  const form=document.createElement('form');form.className='scenario-form';
  const stops=state.geo.features.filter(f=>f.properties.feature_type==='stop');
  const options=stops.map(f=>`<option value="${esc(f.properties.stop_id)}">${esc(f.properties.name)} (${esc(f.properties.stop_id)})</option>`).join('');
  form.innerHTML=`<div class="smalltext"><b>가상 셔틀 만들기</b> · 정차 순서대로 선택</div><input name="name" aria-label="신규 셔틀 이름" placeholder="예: SH-02" maxlength="30" required><select name="start" aria-label="출발 정류장">${options}</select><select name="via" aria-label="경유 정류장"><option value="">경유 없음</option>${options}</select><select name="end" aria-label="도착 정류장">${options}</select><label class="smalltext">배정 차량 <input name="vehicles" type="number" min="1" max="8" value="1" required></label><button class="pill" type="submit">시나리오에 추가</button><div class="smalltext">신규 노선 선형은 미확인 정차점 연결선입니다. 실제 도로 경로가 아닙니다.</div>`;
  form.elements.end.value=stops.at(-1).properties.stop_id;
  form.onsubmit=async(event)=>{event.preventDefault();const fields=form.elements;const ids=[fields.start.value,fields.via.value,fields.end.value].filter(Boolean);if(new Set(ids).size!==ids.length){toast('출발·경유·도착 정류장은 서로 달라야 합니다.');return;}const vehicles=Number(fields.vehicles.value);const used=state.scenarioRoutes.filter(r=>r.active).reduce((sum,r)=>sum+r.vehicles,0);if(used+vehicles>state.summary.fleet.available){toast(`가용 차량 ${state.summary.fleet.available}대를 초과합니다.`);return;}const suffix=1+Math.max(0,...state.scenarioRoutes.map(r=>Number(r.route_id.match(/custom-(\d+)$/)?.[1]||0)));const entry={route_id:`demo-shuttle-custom-${suffix}`,name:fields.name.value.trim(),stop_ids:ids,vehicles,active:true};state.scenarioRoutes.push(entry);restoreCustomRoutes();try{await recalculateScenario();selectRoute(entry.route_id);}catch(error){state.scenarioRoutes.pop();state.routes=state.routes.filter(r=>r.route_id!==entry.route_id);toast(`셔틀 추가 실패: ${error.message}`);}};
  panel.append(form);
  if(state.scenarioBefore&&state.scenarioResult){const before=state.scenarioBefore,after=state.scenarioResult;const delta=document.createElement('div');delta.className='smalltext';delta.textContent=`직전 변경: 충족 ${after.served-before.served>=0?'+':''}${after.served-before.served}명 · 미충족 ${after.unmet-before.unmet>=0?'+':''}${after.unmet-before.unmet}명 · 충족률 ${((after.coverage_rate-before.coverage_rate)*100).toFixed(1)}%p · 차량 ${after.assigned_vehicles-before.assigned_vehicles>=0?'+':''}${after.assigned_vehicles-before.assigned_vehicles}대`;panel.append(delta);}
  if(state.scenarioResult){const effects=document.createElement('div');effects.className='smalltext';effects.textContent=`가상 흡수 인원: ${state.scenarioResult.route_effects.map(item=>`${item.route_id} ${item.served}명`).join(' · ')||'운행 노선 없음'}`;panel.append(effects);}
  const source=document.createElement('div');source.className='smalltext';source.style.cssText='border-top:1px solid #e6ebf3;margin-top:9px;padding-top:7px';source.innerHTML=`<b>데이터 연결 상태</b>${sourceRows}`;panel.append(source);
}
function renderSourceControls() {
  let controls=$('#dataModeControls');
  if (!controls) { controls=document.createElement('span'); controls.id='dataModeControls'; controls.style.display='inline-flex'; controls.style.gap='5px'; $('.right-head').prepend(controls); }
  controls.replaceChildren();
  for (const [mode,name] of [['demo','합성 DEMO'],['real','A GTFS']]) {
    const button=document.createElement('button'); button.className='chip'; button.textContent=name;
    button.disabled=mode==='real'&&state.sources?.A?.status!=='ready';
    button.style.background=state.mode===mode?'#e7efff':'';
    button.onclick=async()=>{ if (state.mode===mode) return; state.mode=mode; state.selectedRouteId=null; state.listmode=mode==='demo'?'strike':'all'; try { await loadData(); } catch(error) { toast(error.message); state.mode='demo'; await loadData(); } };
    controls.append(button);
  }
}
function renderRoutes() {
  const q=state.query.trim().toLocaleLowerCase();
  const routes=state.routes.filter(r=>(state.listmode==='all'||effectiveKind(r)===state.listmode) && routeMatches(r,q));
  $('#listCount').textContent=`${fmt(routes.length)}개 노선`;
  const list=$('#routeList'); list.replaceChildren();
  for (const route of routes.slice(0,500)) {
    const kind=effectiveKind(route);
    const button=document.createElement('button'); button.className=`route-item ${kind==='strike'?'route-strike':'normal'}${state.selectedRouteId===route.route_id?' selected':''}`;
    const active=route.kind!=='shuttle'||state.mode!=='demo'||state.scenarioRoutes?.find(entry=>entry.route_id===route.route_id)?.active;
    button.innerHTML=`<span class="bus-badge" style="color:${colors[kind]||colors.unknown}">▣</span><span class="route-info"><strong>${esc(route.short_name)}</strong><small>${esc(route.agency)} · ${esc(route.route_id)}</small></span><span class="status">${active?esc(labels[kind]||'미분류'):'제외'}</span>`;
    button.onclick=()=>selectRoute(route.route_id);
    list.append(button);
  }
  if (!routes.length) list.textContent='해당 조건의 노선이 없습니다.';
  if (routes.length>500) list.append(document.createTextNode('처음 500개만 표시합니다. 검색어를 좁혀 주세요.'));
  $$('.mode-tabs .pill').forEach(button=>button.classList.toggle('active',button.dataset.listmode===state.listmode));
  renderDisruptionControls();
}
function selectRoute(routeId) { state.selectedRouteId=routeId; renderRoutes(); renderDetail(); updateSelection(); focusSelected(); }
function renderDetail() {
  const route=state.routes.find(r=>r.route_id===state.selectedRouteId);
  const stopIds=route?.representative_stop_ids||route?.stop_ids||[];
  const allStopIds=route?.all_stop_ids||[];
  const fallbackGeometry=state.mode==='real'?'정차 관계 실선 개략도 · 실제 선형 미제공':'미제공·표시 안 함';
  const stops=new Map((state.routeStops?.features||state.geo?.features||[]).filter(f=>f.properties.feature_type==='stop').map(f=>[f.properties.stop_id,f]));
  const stopList=stopIds.length?`<details class="route-stops"><summary>${state.mode==='real'?'대표 운행편':'가상 노선'} 정차점 ${fmt(stopIds.length)}곳 · 순서 보기</summary><ol>${stopIds.map(id=>`<li><button type="button" data-route-stop="${esc(id)}">${esc(stops.get(id)?.properties.name||id)} <small>(${esc(id)})</small></button></li>`).join('')}</ol></details>`:'<p class="smalltext">이 노선의 정차점 순서가 제공되지 않았습니다.</p>';
  const allStopList=state.mode==='real'&&allStopIds.length?`<details class="route-stops"><summary>전체 활성 운행편 정차점 ${fmt(allStopIds.length)}곳 · 중복 제거</summary><p class="smalltext">상·하행과 변형 운행편을 합친 집합입니다. 아래 ID 정렬은 실제 운행 순서가 아닙니다.</p><ol>${allStopIds.map(id=>`<li><button type="button" data-route-stop="${esc(id)}">${esc(stops.get(id)?.properties.name||id)} <small>(${esc(id)})</small></button></li>`).join('')}</ol></details>`:'';
  $('#detailPanel').innerHTML=route?`<div class="detail-name"><b>${esc(route.short_name)}</b><span class="status-badge">${esc(labels[effectiveKind(route)]||'미분류')}</span></div><div class="detail-grid"><span class="k">route_id</span><span class="v">${esc(route.route_id)}</span><span class="k">A 분류</span><span class="v">${esc(route.route_desc||'미제공·합성')}</span><span class="k">공급자</span><span class="v">${esc(route.agency)}</span><span class="k">선형</span><span class="v">${route.geometry_status==='rail_gis_verified_corridor'?`GIS 실제 철도 선로 · GTFS 역 ${route.geometry_validation?.stops_within_250m}/${route.geometry_validation?.gtfs_stops_checked}개 250m 이내 · 분기·방향 미검증`:route.geometry_status==='official'?'공식 버스 경로 좌표':route.geometry_status==='osm_route'?'OSM 버스 노선 관계 · 최신 운행 검증 필요':route.geometry_status==='osrm_estimate'?'OSRM 도로 경로 추정 · 버스 통행 미검증':route.geometry_status==='gtfs_shape'?'GTFS shapes':route.geometry_status==='osm_osrm_driving_estimate'?`OSRM/OSM 자동차 도로망 추정 · ${fmt(route.road_distance_m)}m · 버스 통행 미검증`:route.geometry_status?.startsWith('synthetic')?'가상 정차점 연결선 · 도로 선형 미확인':fallbackGeometry}</span><span class="k">정차점 범위</span><span class="v">${route.representative_stop_ids?`대표 ${fmt(route.representative_stop_ids.length)}곳 · 전체 ${fmt(allStopIds.length)}곳`:'가상 노선 정차점'}</span><span class="k">출처</span><span class="v">${esc(route.geometry_source||sourceText())}</span></div>${stopList}${allStopList}`:`<p class="smalltext">노선을 선택하면 route_id와 정차점·자료 출처를 확인할 수 있습니다.</p>`;
  if(route&&state.mode==='real'&&route.kind!=='metro'){
    $('#detailPanel .status-badge').textContent=labels[effectiveKind(route)]||'미분류';
    const button=document.createElement('button');button.type='button';button.className='detail-btn';
    button.textContent=state.strikeRouteIds.has(route.route_id)?'파업 지정 해제':'이 노선 파업 지정';
    button.onclick=()=>setStrikingRoutes([route.route_id],!state.strikeRouteIds.has(route.route_id));
    $('#detailPanel').append(button);
  }
  $$('#detailPanel [data-route-stop]').forEach(button=>button.onclick=()=>{const feature=stops.get(button.dataset.routeStop);if(!feature||!state.map){toast('정류장 좌표를 사용할 수 없습니다.');return;}state.map.flyTo({center:feature.geometry.coordinates,zoom:15.3,duration:550});state.popup?.remove();state.popup=new state.maplibregl.Popup({closeOnMove:false}).setLngLat(feature.geometry.coordinates).setText(`${feature.properties.name} · ${feature.properties.stop_id} · ${sourceText()}`).addTo(state.map);});
}
function renderDisruptionControls(){
  const panel=$('#strikeControls');if(!panel)return;
  panel.style.display=state.mode==='real'?'block':'none';
  const q=state.query.trim().toLocaleLowerCase();
  const matching=q.length>=2?state.routes.filter(r=>r.kind!=='metro'&&routeMatches(r,q)):[];
  $('#strikeSearchCount').textContent=q.length>=2?`검색 일치 ${fmt(matching.length)}개 노선`:'노선번호를 2자 이상 검색하세요';
  const bulk=$('#strikeSearchApply');bulk.disabled=!matching.length;bulk.textContent=`검색 결과 파업 지정 (${fmt(matching.length)})`;bulk.onclick=()=>setStrikingRoutes(matching.map(r=>r.route_id),true);
  const clear=$('#strikeClear');clear.disabled=!state.strikeRouteIds.size;clear.onclick=()=>setStrikingRoutes([...state.strikeRouteIds],false);
}
function renderLayers() {
  const options=[['districtBoundary','자치구 경계','#334155'],['strike','파업','#ed3d51'],['city','시내버스','#2563eb'],['gyeonggi','경기버스','#f39b28'],['village','마을버스','#17a56c'],['metro','지하철','#864ad7'],['shuttle','셔틀안 노선','#0eaaad'],['shuttleStops','셔틀 정차 정류장','#0eaaad'],['unknown_bus','미분류 버스','#64748b'],['busStops','버스 정류장','#546a80'],['metroStops','지하철역','#864ad7'],['observedDemand','관측 수요 원(기존 모드)','#5695df'],['unmetDemand','잔여·미충족 수요 원','#d43161'],['walk400Demand','걸어서 400m 밖 수요 (셔틀안 기준) · 미연결','#d43161'],['municipalShuttle','지자체 셔틀안 (1월 실운행) · 노선자료 미연결','#148f9c']];
  const list=$('#layerList'); list.replaceChildren();
  for(const [id,name,color] of options) {
    const label=document.createElement('label'); label.dataset.layer=id; label.style.cssText='display:flex;align-items:center;gap:7px;margin:7px 0;font-size:11px;color:#4d5c72;cursor:pointer';
    const input=document.createElement('input'); input.type='checkbox'; input.checked=state.visible.has(id);input.disabled=['walk400Demand','municipalShuttle'].includes(id);if(input.disabled){label.title=id==='walk400Demand'?'실제 보행 네트워크 거리와 셔틀안 기준 수요 데이터가 연결되면 활성화됩니다.':'2026년 1월 지자체 셔틀 노선·정차점 원본 자료가 연결되면 활성화됩니다.';label.style.opacity='.58';}
    input.onchange=async()=>{
      const before=state.visible.has(id);
      input.checked?state.visible.add(id):state.visible.delete(id);
      updateVisibility();
      if(!demoNormalKinds.has(id)&&id!=='shuttle')return;
      if(document.querySelector('.app').classList.contains('sim-active')){
        if(demoNormalKinds.has(id))document.dispatchEvent(new CustomEvent('d-transport-layer-change',{detail:{kind:id,enabled:input.checked}}));
        return;
      }
      if(state.mode!=='demo')return;
      if(demoNormalKinds.has(id))input.checked?state.operatingKinds.add(id):state.operatingKinds.delete(id);
      try{await recalculateScenario();}
      catch(error){before?state.visible.add(id):state.visible.delete(id);if(demoNormalKinds.has(id))before?state.operatingKinds.add(id):state.operatingKinds.delete(id);renderLayers();updateVisibility();toast(`${name} 운행 조건 변경 실패: ${error.message}`);}
    };
    label.append(input); const dot=document.createElement('span'); dot.style.cssText=`width:9px;height:9px;border-radius:50%;background:${color}`; label.append(dot,document.createTextNode(name)); list.append(label);
  }
  const key=document.createElement('p');key.className='smalltext';key.textContent=state.mode==='demo'?'합성 DEMO: 시내·마을·경기·지하철·셔틀안 체크는 운행 가정과 지도 표시를 함께 바꾸며 수요를 다시 계산합니다. 파업 노선과 셔틀 차량·정차 순서는 모의 운행 조건에서 바꿉니다. 정류장·수요 원 체크는 표시 전용입니다.':'A GTFS: 교통수단 체크는 표시 전용입니다. 실제 파업 수요는 미연결입니다. 저장된 C/B 결과는 시뮬레이션 결과 표시에서 별도로 조회합니다.';list.append(key);
}
function renderDemandTable() {
  const data=state.demand;
  const modeLabel={total:'전체 수요',origin:'출발 수요',dest:'도착 수요',affected:'파업 영향 수요',unmet:'미충족 수요'}[state.demandMode];
  $('#topDemandHeading').textContent=`${data?.status==='observed'?'관측 승차(k5·부분 집계)':modeLabel}(명)`;
  $('#demandCaption').textContent=`${state.hour}:00~${String(Number(state.hour)+1).padStart(2,'0')}:00`;
  const body=$('#topDemand'); body.replaceChildren();
  const scenario=state.mode==='demo'?state.scenarioResult:null;
  if (data?.status!=='available'&&data?.status!=='observed'&&!scenario) { body.innerHTML='<tr><td colspan="3">평가/OD 자료가 없어 미계산</td></tr>'; $$('[data-demandmode]').forEach(button=>button.classList.toggle('active',button.dataset.demandmode===state.demandMode)); return; }
  const byStop=new Map();
  const valueKey=state.demandMode==='unmet'?'unmet':state.demandMode==='origin'?'origin_count':state.demandMode==='dest'?'destination_count':'total';
  for (const feature of scenario?scenario.features:data.features) byStop.set(feature.properties.stop_id,(byStop.get(feature.properties.stop_id)||0)+(scenario?feature.properties[valueKey]:feature.properties.passenger_count));
  const names=new Map(state.geo.features.filter(f=>f.properties.feature_type==='stop').map(f=>[f.properties.stop_id,f.properties.name]));
  [...byStop.entries()].sort((a,b)=>b[1]-a[1]).slice(0,5).forEach(([id,value],index)=>{const row=document.createElement('tr'); row.innerHTML=`<td>${index+1}</td><td>${esc(names.get(id)||id)}</td><td>${fmt(value)}</td>`;body.append(row);});
  $$('[data-demandmode]').forEach(button=>button.classList.toggle('active',button.dataset.demandmode===state.demandMode));
}
function renderChart() {
  const chart=$('#demandChart');
  chart.innerHTML='<div style="display:flex;align-items:end;gap:10px;height:115px;width:100%"></div>';
  const bar=chart.firstChild;
  if (state.mode!=='demo') { bar.textContent='실제 집계 OD 미연결'; return; }
  const factors={'06':.65,'07':1,'08':1.24,'09':.88,'18':1.13};
  for (const [hour,factor] of Object.entries(factors)) { const node=document.createElement('button'); node.className='time-pill'; node.style.cssText=`height:${Math.round(25+65*factor/1.24)}px;flex:1;background:${hour===state.hour?'#37b8a7':'#98dcd1'};color:#18344d;align-self:end`;node.textContent=hour+'시';node.title=`합성 수요 · 상대계수 ${factor}`;node.onclick=()=>chooseHour(hour);bar.append(node); }
}
async function chooseHour(hour) { state.hour=hour;state.scenarioBefore=null; $$('[data-time]').forEach(button=>button.classList.toggle('active',button.dataset.time===hour)); if(!await loadDemand())return; renderAll(); updateDemand(); }
async function chooseDemandMode(mode) { state.demandMode=mode; renderAll(); updateDemand(); }

function allRouteFeatures() {
  const features=(state.geo?.features.filter(f=>f.properties.feature_type==='route'&&!(state.mode==='demo'&&f.properties.kind==='shuttle'&&!state.scenarioRoutes?.find(r=>r.route_id===f.properties.route_id)?.active))||[]).map(feature=>{
    if(state.mode!=='demo')return feature;
    if(feature.properties.kind==='strike'&&!state.demoStrikeRouteIds.has(feature.properties.route_id))return {...feature,properties:{...feature.properties,kind:'city'}};
    const entry=state.scenarioRoutes?.find(item=>item.route_id===feature.properties.route_id);
    if(feature.properties.kind==='shuttle'&&entry?.edited){const stops=new Map(pointFeatures().map(f=>[f.properties.stop_id,f.geometry.coordinates]));return {...feature,geometry:{type:'LineString',coordinates:entry.stop_ids.map(id=>stops.get(id))},properties:{...feature.properties,geometry_status:'synthetic_unverified'}};}
    return feature;
  });
  if(state.mode==='demo')for(const route of state.routes.filter(r=>r.kind==='shuttle'&&r.geometry_status==='synthetic_unverified'&&state.scenarioRoutes?.find(entry=>entry.route_id===r.route_id)?.active))features.push({type:'Feature',id:route.route_id,geometry:{type:'LineString',coordinates:route.coordinates},properties:{feature_type:'route',route_id:route.route_id,kind:'shuttle',geometry_status:route.geometry_status,source:'synthetic'}});
  return state.mode==='real'?features.map(feature=>state.strikeRouteIds.has(feature.properties.route_id)?{...feature,properties:{...feature.properties,kind:'strike'}}:feature):features;
}
// 합성 지도에서 같은 좌표의 선분만 가리는 표시 규칙. 실제 도로 공유 판정이 아니다.
function segmentKey(a,b) {
  const point=p=>`${Number(p[0]).toFixed(6)},${Number(p[1]).toFixed(6)}`;
  const left=point(a),right=point(b);
  return left<right?`${left}|${right}`:`${right}|${left}`;
}
function maskSharedSegments(coordinates,shared) {
  const runs=[];let run=[];let hidden=0;
  for(let i=0;i<coordinates.length-1;i++) {
    const a=coordinates[i],b=coordinates[i+1];
    if(shared.has(segmentKey(a,b))) {hidden++;if(run.length>1)runs.push(run);run=[];continue;}
    if(!run.length)run=[a];
    run.push(b);
  }
  if(run.length>1)runs.push(run);
  if(!hidden)return {geometry:{type:'LineString',coordinates},hidden:0};
  if(!runs.length)return {geometry:null,hidden};
  return {geometry:runs.length===1?{type:'LineString',coordinates:runs[0]}:{type:'MultiLineString',coordinates:runs},hidden};
}
function routeFeatures() {
  const features=allRouteFeatures();
  if(state.mode!=='demo')return features;
  const shared=new Set();
  for(const feature of features)if(demoNormalKinds.has(feature.properties.kind)&&state.visible.has(feature.properties.kind)&&feature.geometry.type==='LineString') {
    const points=feature.geometry.coordinates;
    for(let i=0;i<points.length-1;i++)shared.add(segmentKey(points[i],points[i+1]));
  }
  return features.flatMap(feature=>{
    if(feature.properties.kind!=='shuttle'||feature.geometry.type!=='LineString')return [feature];
    const masked=maskSharedSegments(feature.geometry.coordinates,shared);
    return masked.geometry?[{...feature,geometry:masked.geometry,properties:{...feature.properties,visual_overlap_hidden_segments:masked.hidden}}]:[];
  });
}
function pointFeatures() { return state.geo?.features.filter(f=>f.properties.feature_type==='stop')||[]; }
function featureCollection(features) { return {type:'FeatureCollection',features}; }
// A GTFS stop_times의 인접 정차 관계만 한 번 준비한다. 실제 도로·철로 선형이나 거리로 사용하지 않는다.
function buildSchematicBase() {
  if(state.mode!=='real')return [];
  const positions=new Map(pointFeatures().map(f=>[f.properties.stop_id,f.geometry.coordinates]));
  return state.routes.flatMap(route=>{
    if(route.coordinates)return [];
    const links=route.kind==='metro'?route.schematic_stop_links||[]:(route.representative_stop_ids||[]).slice(1).map((id,index)=>[route.representative_stop_ids[index],id]);
    const lines=links.map(([a,b])=>[positions.get(a),positions.get(b)]).filter(([a,b])=>a&&b);
    return lines.length?[{type:'Feature',id:`schematic-${route.route_id}`,geometry:{type:'MultiLineString',coordinates:lines},properties:{route_id:route.route_id,kind:route.kind,schematic:true}}]:[];
  });
}
function schematicFeatures() {if(state.district==='all')return [];return (state.schematicBase||[]).flatMap(feature=>{const kind=state.strikeRouteIds.has(feature.properties.route_id)?'strike':feature.properties.kind;if(!state.visible.has(kind))return [];return [{...feature,properties:{...feature.properties,kind,selected:feature.properties.route_id===state.selectedRouteId}}];});}
function updateSchematic() { state.map?.getSource('d-schematic')?.setData(featureCollection(schematicFeatures())); }
function boundsOf(features) { const coordinates=[];for(const f of features) { if(f.geometry.type==='LineString') coordinates.push(...f.geometry.coordinates); else if(f.geometry.type==='MultiLineString')for(const line of f.geometry.coordinates)coordinates.push(...line); else if(f.geometry.type==='Point') coordinates.push(f.geometry.coordinates); }if(!coordinates.length) return null;return [[Math.min(...coordinates.map(p=>p[0])),Math.min(...coordinates.map(p=>p[1]))],[Math.max(...coordinates.map(p=>p[0])),Math.max(...coordinates.map(p=>p[1]))]]; }
function fitSeoul() {state.map?.fitBounds(SEOUL_BOUNDS,{padding:35,maxZoom:11.3,duration:180});}
function fitAll() { if(state.district==='all'){fitSeoul();return;}if(!state.map||!state.geo) return; const features=allRouteFeatures();const bounds=boundsOf(features.length?features:pointFeatures()); if(bounds) state.map.fitBounds(bounds,{padding:45,maxZoom:14,duration:220}); }
function selectedStopFeatures() {
  const route=state.routes.find(r=>r.route_id===state.selectedRouteId);
  const ids=route?.representative_stop_ids?.length?route.representative_stop_ids:route?.stop_ids||[];
  const byId=new Map((state.routeStops?.features||pointFeatures()).filter(f=>f.properties.feature_type==='stop').map(f=>[f.properties.stop_id,f]));
  return ids.flatMap((id,index)=>byId.has(id)?[{...byId.get(id),properties:{...byId.get(id).properties,order:index+1}}]:[]);
}
function focusSelected() {
  if(!state.map)return;
  const feature=allRouteFeatures().find(f=>f.properties.route_id===state.selectedRouteId);
  const points=feature?[feature]:selectedStopFeatures();
  const bounds=boundsOf(points);
  if(bounds)state.map.fitBounds(bounds,{padding:80,maxZoom:15,duration:650});
  else if(state.selectedRouteId)toast('이 노선은 표시할 선형과 정차점 좌표가 없습니다.');
}
function updateSelection() {
  const map=state.map;if(!map||!map.getLayer('selected-route'))return;
  map.setFilter('selected-route',['==',['get','route_id'],state.selectedRouteId||'__none__']);
  const route=state.routes.find(r=>r.route_id===state.selectedRouteId);
  map.getSource('d-selected-stops')?.setData(featureCollection(selectedStopFeatures()));
  updateSchematic();
}
function updateMapData(reframe=false) {
  const map=state.map;if(!map||!map.getSource('d-routes')||!state.geo)return;
  map.getSource('d-routes')?.setData(featureCollection(routeFeatures()));
  map.getSource('d-district')?.setData(featureCollection(state.boundary?[state.boundary]:[]));
  updateVisibleStops();
  updateDemand(); updateSelection(); updateVisibility();
  if(!reframe)return;
  if(state.district==='all')fitSeoul();
  else if(state.mode==='real'&&state.boundary){const g=state.boundary.geometry;const b=g.type==='MultiPolygon'?g.coordinates.flat(2):g.coordinates.flat(1);const xs=b.map(p=>p[0]),ys=b.map(p=>p[1]);if(xs.length)map.fitBounds([[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]],{padding:40,duration:220});}
  else fitAll();
}
function updateVisibleStops() {
  const map=state.map;if(!map||!map.getSource('d-stops')||!state.geo)return;
  const bounds=map.getBounds();
  const visible=state.mode==='real'?pointFeatures().filter(f=>bounds.contains(f.geometry.coordinates)):pointFeatures();
  const features=visible.map(f=>{const kinds=[...(state.stopServices?.get(String(f.properties.stop_id))||[])];const stopKind=f.properties.kind==='metro'?'metro':kinds.length>1?'mixed':kinds[0]||'bus';return {...f,properties:{...f.properties,stop_kind:stopKind,served_kinds:kinds.join(',')}};});
  map.getSource('d-stops').setData(featureCollection(features));
}
// API의 80개 가상 OD 셀은 유지하고 지도 표시만 정류장 거점별로 합산한다.
function demandHubs(features,valueKey,label) {
  const positions=new Map(pointFeatures().map(f=>[f.properties.stop_id,f]));
  const buckets=new Map();
  for(const feature of features) {
    const id=feature.properties.stop_id;
    if(!id)continue;
    if(!buckets.has(id))buckets.set(id,{count:0,coordinates:positions.get(id)?.geometry.coordinates||feature.geometry.coordinates,name:positions.get(id)?.properties.name||id});
    buckets.get(id).count+=Number(feature.properties[valueKey]||0);
  }
  return [...buckets].map(([id,item])=>({type:'Feature',id,geometry:{type:'Point',coordinates:item.coordinates},properties:{stop_id:id,name:item.name,passenger_count:item.count,demand_label:label,source:'synthetic_aggregated_demo'}}));
}
function updateDemand() {
  const map=state.map;if(!map||!map.getSource('d-demand'))return;
  const result=state.mode==='demo'?state.scenarioResult:null;
  const mode=state.demandMode;
  const outer=state.demand?.status==='observed'?state.demand.features.map(f=>({...f,properties:{...f.properties,demand_label:'관측 승차 k5'}})):result&&['total','origin','dest'].includes(mode)?demandHubs(result.features,mode==='origin'?'origin_count':mode==='dest'?'destination_count':'total',mode==='origin'?'출발':mode==='dest'?'도착':'전체'):[];
  const target=result&&(mode==='total'||mode==='unmet')?demandHubs(result.features,'unmet','미충족'):[];
  const beforeByStop=new Map(state.scenarioBefore&&result&&state.scenarioBefore.hour===result.hour&&state.scenarioBefore.date===result.date?demandHubs(state.scenarioBefore.features,'unmet','직전 미충족').map(f=>[f.id,f.properties.passenger_count]):[]);
  const totalByStop=new Map(result?demandHubs(result.features,'total','평가 수요').map(f=>[f.id,f.properties.passenger_count]):[]);
  for(const feature of target){const current=feature.properties.passenger_count,previous=beforeByStop.get(feature.id),total=totalByStop.get(feature.id);Object.assign(feature.properties,{previous_unmet:previous??null,delta_unmet:previous==null?null:current-previous,evaluated_total:total??null,unmet_rate:total?100*current/total:null,previous_unmet_rate:previous!=null&&total?100*previous/total:null});}
  map.getSource('d-demand').setData(featureCollection(outer));
  const before=state.unmetValues;
  const token=++state.unmetAnimation;
  const draw=(progress)=>{
    const animated=target.map(f=>{
      const previous=before?.get(f.id)??f.properties.passenger_count;
      const count=Math.max(0,Math.round(previous*(1-progress)+f.properties.passenger_count*progress));
      return {...f,properties:{...f.properties,passenger_count:count}};
    });
    map.getSource('d-unmet')?.setData(featureCollection(animated));
  };
  if(!before||!result){draw(1);state.unmetValues=result?new Map(target.map(f=>[f.id,f.properties.passenger_count])):null;return;}
  const started=performance.now();
  const frame=(now)=>{if(token!==state.unmetAnimation)return;const progress=Math.min(1,(now-started)/220);draw(progress);if(progress<1)requestAnimationFrame(frame);};
  requestAnimationFrame(frame);
  state.unmetValues=new Map(target.map(f=>[f.id,f.properties.passenger_count]));
}
function updateOverview(){
  const map=state.map;if(!map?.getSource('d-overview-boundary'))return;
  if(!state.overviewBound&&state.overviewBoundary&&state.overviewRates){
    map.getSource('d-overview-boundary').setData(state.overviewBoundary);
    map.getSource('d-overview-percent').setData(featureCollection(state.overviewRates));
    state.overviewBound=true;
  }
  const visible=state.mode==='real'&&state.district==='all'&&!document.querySelector('.app').classList.contains('sim-active');
  for(const id of ['overview-boundary','overview-percent-bg','overview-percent-label'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',visible?'visible':'none');
  if(visible&&state.overviewRates)$('#mapStatus').textContent=`서울 전체 · 25개 구 경계 · 저장된 충족률 ${state.overviewRates.length}개 구만 표시 (마을·경기 접근 조건)`;
}
function updateVisibility() {
  const map=state.map;if(!map||!map.getSource('d-routes'))return;
  const overview=state.mode==='real'&&state.district==='all'&&!document.querySelector('.app').classList.contains('sim-active');
  $('.map-legend').style.display=overview?'none':'';
  map.getSource('d-routes').setData(featureCollection(routeFeatures()));
  for (const kind of Object.keys(colors))for(const suffix of ['','-unverified','-halo','-unverified-halo'])if(map.getLayer('route-'+kind+suffix))map.setLayoutProperty('route-'+kind+suffix,'visibility',!overview&&state.visible.has(kind)?'visible':'none');
  for(const [layer,key] of [['bus-stops','busStops'],['metro-stops','metroStops'],['metro-stop-labels','metroStops'],['demand-circles','observedDemand'],['demand-unmet','unmetDemand']]) if(map.getLayer(layer))map.setLayoutProperty(layer,'visibility',!overview&&state.visible.has(key)&&(!layer.startsWith('metro-')||state.visible.has('metro'))?'visible':'none');
  if(map.getLayer('district-boundary'))map.setLayoutProperty('district-boundary','visibility',state.visible.has('districtBoundary')?'visible':'none');
  const selected=state.routes.find(r=>r.route_id===state.selectedRouteId);if(map.getLayer('selected-route'))map.setLayoutProperty('selected-route','visibility',!overview&&selected&&state.visible.has(effectiveKind(selected))?'visible':'none');
  for(const id of ['selected-route-stops','selected-stop-labels','schematic-routes','schematic-strike-halo','schematic-strike','schematic-selected'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',overview?'none':'visible');
  updateOverview();
  updateSchematic();
  document.dispatchEvent(new Event('d-layer-visibility'));
}
function addSourcesAndLayers(map) {
  map.addSource('d-district',{type:'geojson',data:featureCollection([])});
  map.addLayer({id:'district-boundary',type:'line',source:'d-district',paint:{'line-color':'#263852','line-width':2.5}});
  map.addSource('d-overview-boundary',{type:'geojson',data:featureCollection([])});
  map.addSource('d-overview-percent',{type:'geojson',data:featureCollection([])});
  map.addLayer({id:'overview-boundary',type:'line',source:'d-overview-boundary',paint:{'line-color':'#334155','line-width':2.3,'line-opacity':.82}});
  map.addSource('d-routes',{type:'geojson',data:featureCollection([])});
  map.addSource('d-schematic',{type:'geojson',data:featureCollection([])});
  map.addSource('d-stops',{type:'geojson',data:featureCollection([])});
  map.addSource('d-demand',{type:'geojson',data:featureCollection([])});
  map.addSource('d-unmet',{type:'geojson',data:featureCollection([])});
  map.addSource('d-selected-stops',{type:'geojson',data:featureCollection([])});
  for(const [kind,color] of Object.entries(colors)) {
    const typeFilter=['==',['get','kind'],kind];
    const uncertain=['any',['==',['get','geometry_status'],'synthetic'],['==',['get','geometry_status'],'synthetic_unverified']];
    const verifiedFilter=['all',typeFilter,['!',uncertain]],unverifiedFilter=['all',typeFilter,uncertain];
    if(kind==='strike')map.addLayer({id:'route-strike-halo',type:'line',source:'d-routes',filter:verifiedFilter,layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#ff7898','line-width':12,'line-opacity':.27}});
    map.addLayer({id:'route-'+kind,type:'line',source:'d-routes',filter:verifiedFilter,layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':kind==='strike'?'#e93d60':color,'line-width':kind==='strike'?5.5:kind==='shuttle'?5:3.5,'line-opacity':kind==='strike'?0.68:['case',['==',['get','geometry_status'],'osrm_estimate'],.58,.79]}});
    if(kind==='strike')map.addLayer({id:'route-strike-unverified-halo',type:'line',source:'d-routes',filter:unverifiedFilter,layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#ff7898','line-width':12,'line-opacity':.27}});
    map.addLayer({id:'route-'+kind+'-unverified',type:'line',source:'d-routes',filter:unverifiedFilter,layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':kind==='strike'?'#e93d60':color,'line-width':kind==='strike'?5.5:kind==='shuttle'?5:3.5,'line-opacity':kind==='strike'?0.68:0.79}});
  }
  map.addLayer({id:'selected-route',type:'line',source:'d-routes',filter:['==',['get','route_id'],'__none__'],paint:{'line-color':['match',['get','kind'],'strike','#e93d60','city',colors.city,'gyeonggi',colors.gyeonggi,'village',colors.village,'metro',colors.metro,'shuttle',colors.shuttle,colors.unknown],'line-width':8,'line-opacity':['case',['==',['get','kind'],'strike'],.72,1]}});
  const schematicColor=['match',['get','kind'],'strike',colors.strike,'metro',colors.metro,'city',colors.city,'village',colors.village,'gyeonggi',colors.gyeonggi,'unknown_bus',colors.unknown_bus,colors.unknown];
  map.addLayer({id:'schematic-routes',type:'line',source:'d-schematic',filter:['!=',['get','kind'],'strike'],paint:{'line-color':schematicColor,'line-width':['interpolate',['linear'],['zoom'],9,1,12,1.8,15,2.5],'line-opacity':.32}});
  map.addLayer({id:'schematic-strike-halo',type:'line',source:'d-schematic',filter:['==',['get','kind'],'strike'],layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#ff7898','line-width':9,'line-opacity':.25}});
  map.addLayer({id:'schematic-strike',type:'line',source:'d-schematic',filter:['==',['get','kind'],'strike'],layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#e93d60','line-width':4.5,'line-opacity':.72}});
  map.addLayer({id:'schematic-selected',type:'line',source:'d-schematic',filter:['==',['get','selected'],true],paint:{'line-color':schematicColor,'line-width':4,'line-opacity':['case',['==',['get','kind'],'strike'],.72,.9]}});
  for(const id of ['route-strike-halo','route-strike','route-strike-unverified-halo','route-strike-unverified','selected-route'])map.moveLayer(id);
  map.addLayer({id:'demand-circles',type:'circle',source:'d-demand',paint:{'circle-color':'#e55a89','circle-opacity':['interpolate',['linear'],['get','passenger_count'],0,0,20,.17,50,.28,100,.43,170,.6,250,.76],'circle-stroke-color':'#b53a68','circle-stroke-opacity':['interpolate',['linear'],['get','passenger_count'],0,0,20,.2,100,.5,250,.8],'circle-stroke-width':1.5,'circle-radius':['interpolate',['linear'],['get','passenger_count'],0,0,20,8,50,13,100,19,170,27,250,35]}});
  map.addLayer({id:'demand-unmet',type:'circle',source:'d-unmet',paint:{'circle-color':'#d43161','circle-opacity':['interpolate',['linear'],['get','passenger_count'],0,0,20,.24,50,.4,100,.58,170,.74,250,.88],'circle-stroke-color':'#a21b4c','circle-stroke-opacity':['interpolate',['linear'],['get','passenger_count'],0,0,20,.28,100,.65,250,.9],'circle-stroke-width':1,'circle-radius':['interpolate',['linear'],['get','passenger_count'],0,0,20,5,50,9,100,15,170,22,250,28]}});
  map.addLayer({id:'overview-percent-bg',type:'circle',source:'d-overview-percent',paint:{'circle-color':'#ffffff','circle-opacity':.96,'circle-radius':27,'circle-stroke-color':'#334155','circle-stroke-width':2}});
  map.addLayer({id:'overview-percent-label',type:'symbol',source:'d-overview-percent',layout:{'text-field':['get','label'],'text-size':13,'text-font':['Noto Sans Bold'],'text-allow-overlap':true},paint:{'text-color':'#18324d'}});
  map.addLayer({id:'bus-stops',type:'symbol',source:'d-stops',filter:['!=',['get','kind'],'metro'],minzoom:10,layout:{'icon-image':['match',['get','stop_kind'],'city','d-city-icon','gyeonggi','d-gyeonggi-icon','village','d-village-icon','mixed','d-mixed-icon','d-bus-icon'],'icon-size':['interpolate',['linear'],['zoom'],10,.40,12,.54,15,.72],'icon-allow-overlap':false,'icon-ignore-placement':false}});
  map.addLayer({id:'metro-stops',type:'symbol',source:'d-stops',filter:['==',['get','kind'],'metro'],minzoom:9,layout:{'icon-image':'d-metro-icon','icon-size':['interpolate',['linear'],['zoom'],9,.48,12,.67,15,.84],'icon-allow-overlap':true,'icon-ignore-placement':true}});
  map.addLayer({id:'metro-stop-labels',type:'symbol',source:'d-stops',filter:['==',['get','kind'],'metro'],minzoom:9,layout:{'text-field':['coalesce',['get','name'],['get','stop_id']],'text-size':['interpolate',['linear'],['zoom'],9,9,13,12,16,14],'text-offset':[0,1.55],'text-anchor':'top','text-allow-overlap':true,'text-ignore-placement':true},paint:{'text-color':'#28334a','text-halo-color':'#fff','text-halo-width':2.5}});
  map.addLayer({id:'selected-route-stops',type:'circle',source:'d-selected-stops',paint:{'circle-color':'#ffda4b','circle-stroke-color':'#263852','circle-stroke-width':1.5,'circle-radius':['interpolate',['linear'],['zoom'],9,4,13,6,16,8]}});
  map.addLayer({id:'selected-stop-labels',type:'symbol',source:'d-selected-stops',minzoom:11,layout:{'text-field':['concat',['to-string',['get','order']],' · ',['get','name']],'text-size':11,'text-offset':[0,1.3],'text-anchor':'top','text-allow-overlap':false},paint:{'text-color':'#172238','text-halo-color':'#fff','text-halo-width':2}});
  for(const layer of ['schematic-routes','schematic-strike','schematic-selected']) {
    map.on('click',layer,event=>{const routeId=event.features?.[0]?.properties.route_id;if(routeId)selectRoute(routeId);});
    map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor='';});
  }
  for(const layer of ['overview-percent-bg','overview-percent-label']){
    map.on('click',layer,event=>{const region=event.features?.[0]?.properties?.region;if(!region)return;$('#districtSelect').value=region;$('#districtSelect').dispatchEvent(new Event('change'));});
    map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor='';});
  }
  for(const kind of Object.keys(colors))for(const suffix of ['','-unverified']) {
    const layer='route-'+kind+suffix;
    map.on('click',layer,event=>{if(map.queryRenderedFeatures(event.point,{layers:['bus-stops','metro-stops','metro-stop-labels','selected-route-stops']}).length)return;const feature=event.features?.[0];if(feature)selectRoute(feature.properties.route_id);});
    map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor='';});
  }
  for(const layer of ['bus-stops','metro-stops','metro-stop-labels','selected-route-stops','demand-circles','demand-unmet']) {
    map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor='';});
    map.on('click',layer,event=>{const f=event.features?.[0];if(!f)return;if(['demand-circles','demand-unmet'].includes(layer)&&map.queryRenderedFeatures(event.point,{layers:['bus-stops','metro-stops','metro-stop-labels','selected-route-stops']}).length)return;const p=f.properties;const comparison=layer==='demand-unmet'&&p.previous_unmet!=null?` · 직전 ${fmt(p.previous_unmet)}명 → 현재 ${fmt(p.passenger_count)}명 (${Number(p.delta_unmet)>=0?'+':''}${fmt(p.delta_unmet)}명) · 미충족률 ${Number(p.previous_unmet_rate).toFixed(1)}% → ${Number(p.unmet_rate).toFixed(1)}%`:'';const label=layer==='demand-circles'||layer==='demand-unmet'?`${p.name||p.stop_id} · ${p.demand_label} ${fmt(p.passenger_count)}명${comparison}${layer==='demand-unmet'?` · 평가 수요 ${fmt(p.evaluated_total)}명 · 합성 DEMO`:''}`:`${p.name||p.stop_id} · ${p.stop_id}`;state.popup?.remove();state.popup=new state.maplibregl.Popup({closeOnMove:true}).setLngLat(event.lngLat).setText(label).addTo(map);});
  }
  for(const layer of ['bus-stops','metro-stops','metro-stop-labels']) {
    map.on('mousemove',layer,event=>{
      const feature=event.features?.[0]; if(!feature)return;
      showStopHover(feature);
      $('#mapStatus').textContent=`${feature.properties.name} · ${feature.properties.stop_id} · ${sourceText()}`;
    });
    map.on('mouseleave',layer,()=>{
      clearStopHover();$('#mapStatus').textContent=`${sourceText()} · ${state.summary?.snapshot_id||''} · 실제 운행/탑승 보장 아님`;
    });
  }
  state.overviewBound=false;updateOverview();updateMapData(true);
}
async function initMap() {
  try {
    state.maplibregl=await import('/vendor/maplibre-gl.mjs');
    const map=new state.maplibregl.Map({container:'map',style:'/vendor/openfreemap-liberty.json',center:[126.984,37.546],zoom:12.2,attributionControl:true});
    state.map=map;
    map.once('style.load',async()=>{try {await Promise.all(['bus','city','gyeonggi','village','mixed','metro'].map(kind=>addStopIcon(map,kind)));addSourcesAndLayers(map);$('#map').dataset.layersReady='true';$('#mapError').style.display='none';document.dispatchEvent(new Event('d-map-ready'));}catch(error){$('#map').dataset.layersReady='false';$('#mapError').textContent=`지도 레이어 초기화 실패: ${error.message}${location.search.includes('debug')?' · '+error.stack:''}`;$('#mapError').style.display='flex';}});
    map.on('moveend',updateVisibleStops);
    map.on('error',event=>{if(map.getSource('d-routes')){$('#mapStatus').textContent='배경 타일 연결이 불안정합니다. 노선·수요 레이어는 계속 표시합니다.';}else{$('#mapError').textContent=`지도 스타일 로딩 실패: ${event.error?.message||'연결 상태 확인 필요'}`;$('#mapError').style.display='flex';}});
    setTimeout(()=>{if(!map.getSource('d-routes')) {$('#mapError').textContent='지도 스타일 로딩이 지연됩니다. 로컬 지도 파일과 브라우저 WebGL 상태를 확인하세요.';$('#mapError').style.display='flex';}},20000);
  } catch(error) { $('#mapError').textContent=`MapLibre 로딩 실패: ${error.message}. 네트워크 연결을 확인하세요.`;$('#mapError').style.display='flex'; }
}
function bindUI() {
  const strikeControls=document.createElement('div');strikeControls.id='strikeControls';strikeControls.className='smalltext';strikeControls.style.cssText='padding:8px 12px;border-top:1px solid #e6ebf3';strikeControls.innerHTML='<div id="strikeSearchCount"></div><div style="display:flex;gap:6px;margin:6px 0"><button class="pill" id="strikeSearchApply" type="button"></button><button class="pill" id="strikeClear" type="button">파업 지정 모두 해제</button></div><span>A GTFS에 실제 버스 회사명이 없어 노선번호로 선택합니다. 파업 지정은 이 화면의 가정이며 서버에는 저장되지 않습니다.</span>';
  $('#routeList').before(strikeControls);
  $('#districtSelect').onchange=async event=>{state.district=event.target.value;state.selectedRouteId=null;if(state.district!=='all'&&state.mode!=='real'){state.mode='real';state.listmode='all';}state.observedMode=state.district!=='all';$('#observedToggle').checked=state.observedMode;$('#districtStatus').textContent=`${event.target.selectedOptions[0].textContent} · 교통망 갱신 중…`;document.dispatchEvent(new CustomEvent('d-district-selected',{detail:{district:state.district}}));const requested=state.district;await loadData();if(requested===state.district)$('#districtStatus').textContent=state.district==='all'?'서울 전체 교통망':`${$('#districtSelect').selectedOptions[0].textContent} · 공간 교차 노선 ${state.routes.length}개 · ${state.extent==='inside'?'구 내부 구간':'노선 전체'}`;};
  $('#districtExtent').onchange=async event=>{state.extent=event.target.value;state.selectedRouteId=null;await loadData();};
  const observed=document.createElement('label');observed.className='district-field observed-field';observed.innerHTML='<input id="observedToggle" type="checkbox"> 관측 승차(k5)';$('#districtStatus').before(observed);
  const date=document.createElement('select');date.id='observedDate';date.className='district-date';for(const day of ['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-11-02']){const option=document.createElement('option');option.value=day;option.textContent=day;date.append(option);}observed.after(date);
  $('#observedToggle').onchange=async event=>{state.observedMode=event.target.checked;await loadDemand();renderAll();updateDemand();};
  date.onchange=async event=>{state.observedDate=event.target.value;await loadDemand();renderAll();updateDemand();};
  $('#routeSearch').addEventListener('input',event=>{state.query=event.target.value;renderRoutes();});
  $$('.mode-tabs .pill').forEach(button=>button.onclick=()=>{state.listmode=button.dataset.listmode;renderRoutes();});
  $$('[data-time]').forEach(button=>button.onclick=()=>chooseHour(button.dataset.time));
  $$('[data-demandmode]').forEach(button=>button.onclick=()=>chooseDemandMode(button.dataset.demandmode));
  const affected=document.createElement('button');affected.className='pill';affected.dataset.demandmode='affected';affected.textContent='파업 영향 수요';affected.onclick=()=>chooseDemandMode('affected');$('[data-demandmode="dest"]').after(affected);
  $('#showAll').onclick=fitAll; $('#focusSelected').onclick=focusSelected;
  $('#zoomIn').onclick=()=>state.map?.zoomIn();$('#zoomOut').onclick=()=>state.map?.zoomOut();$('#zoomReset').onclick=fitAll;
    $('#allLayers').onclick=async()=>{Object.keys(colors).forEach(key=>state.visible.add(key));['busStops','metroStops','districtBoundary','shuttleStops','observedDemand','unmetDemand'].forEach(key=>state.visible.add(key));renderLayers();updateVisibility();if(document.querySelector('.app').classList.contains('sim-active')){document.dispatchEvent(new CustomEvent('d-transport-layer-change',{detail:{kind:'all',enabled:true}}));}else if(state.mode==='demo'){demoNormalKinds.forEach(kind=>state.operatingKinds.add(kind));try{await recalculateScenario();}catch(error){toast(error.message);}}};
  $('#selectShuttle').onclick=()=>{const shuttle=state.routes.find(r=>r.kind==='shuttle');if(shuttle)selectRoute(shuttle.route_id);};
  $('#resetAll').onclick=async()=>{state.selectedRouteId=null;state.query='';state.hour='07';state.demandMode='total';$('#routeSearch').value='';if(state.mode==='real') {try {const response=await fetch('/api/d/sources/refresh',{method:'POST'});const result=await response.json();if(!response.ok)throw new Error(result.message);}catch(error){toast('새로고침 실패 · 이전 정상본 유지: '+error.message);}}await loadData();};
}
bindUI();
try {const districtData=await get('/api/d/districts');for(const d of districtData.districts){const option=document.createElement('option');option.value=d.id;option.textContent=d.name;$('#districtSelect').append(option);}}catch(error){$('#districtStatus').textContent=`경계 자료 미연결: ${error.message}`;}
try {const sources=await get('/api/d/sources');if(sources.A?.status==='ready'){state.mode='real';state.listmode='all';}}catch { /* DEMO는 별도로 계속 표시한다. */ }
await Promise.all([initMap(),loadOverview().catch(error=>{toast('서울 전체 비율 연결 실패: '+error.message);}),loadData().catch(error=>{toast(error.message);$('#mapStatus').textContent='데이터 연결 실패: '+error.message;})]);
try {
  const {initSimulationMode}=await import('/simulation.js');
  await initSimulationMode({
    map:state.map,maplibregl:state.maplibregl,
    getBaseMode:()=>state.mode,
    fitSeoul,
    refreshBaseVisibility:()=>{updateVisibility();for(const id of ['selected-route-stops','selected-stop-labels'])if(state.map?.getLayer(id))state.map.setLayoutProperty(id,'visibility','visible');},
    setDistrict:async district=>{state.district=district;state.extent='inside';state.observedMode=false;$('#districtSelect').value=district;$('#districtExtent').value='inside';$('#observedToggle').checked=false;state.selectedRouteId=null;await loadData();},
    isVisible:key=>state.visible.has(key),
  });
} catch(error) {
  toast(`시뮬레이션 모드 연결 실패: ${error.message}${location.search.includes('debug')?' · '+error.stack:''}`);
}
