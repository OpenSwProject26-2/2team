/* D 운영자 기존 MapLibre 지도에 C/B 결과를 얹는 로컬 서비스 모드. */
const byId = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '미연결' : Number(value).toLocaleString('ko-KR',{maximumFractionDigits:1});
const pct = value => value == null ? '미연결' : `${Number(value).toFixed(2)}%`;
const sim = {active:false, busy:false, previousMode:null, options:null, map:null, maplibregl:null, result:null, beforeResult:null, renderedHour:null, residual:null, table:null, comparison:null, summary:null, runs:null, selectedRoute:null, markers:[], request:0, hidden:new Map(), demandFrame:0, displayedDemand:[]};
let restoreD,enableA,getBaseMode,setDistrict,isVisible;
const query = () => new URLSearchParams(Object.fromEntries(['region','fleet','hour','variant','plan','period','date'].map(k=>[k,byId(`sim-${k}`).value])));
async function read(path) {const response=await fetch(path);const data=await response.json();if(!response.ok)throw Error(data.message||response.statusText);return data;}
function options(id, values, keep) {const node=byId(id);node.replaceChildren(...values.map(([value,label])=>{const el=document.createElement('option');el.value=value;el.textContent=label;return el;}));if(keep&&[...node.options].some(x=>x.value===keep))node.value=keep;}
function syncFleet() {const region=sim.options.regions.find(r=>r.id===byId('sim-region').value);options('sim-fleet',region.fleets.map(n=>[String(n),`${n}대`]),byId('sim-fleet').value);if(sim.active)sim.map?.flyTo({center:region.center,zoom:11.7});}
function syncDates() {const dates=sim.options.periods[byId('sim-period').value];options('sim-date',[['mean','5일 평균'],...dates.map(d=>[d,d])],byId('sim-date').value);}
async function preferAvailableRunDate() {
  try {
    const runs=await read(`/api/d/simulation/runs?${query()}`);
    const dates=new Set((runs.runs||[]).filter(r=>r.status==='passed').map(r=>r.date));
    const field=byId('sim-date');
    if(!dates.has(field.value))field.value=[...dates][0]||'mean';
  }catch {byId('sim-date').value='mean';}
}
function fitRegions() {if(!sim.map||!sim.options)return;const bounds=new sim.maplibregl.LngLatBounds();sim.options.regions.forEach(r=>bounds.extend(r.center));sim.map.fitBounds(bounds,{padding:85,maxZoom:11.1,duration:400});}
function mapLayers(active) {
  const map=sim.map;if(!map?.getSource('d-routes'))return;
  if(map.getLayer('demand-circles'))map.setLayoutProperty('demand-circles','visibility',active?'none':isVisible('observedDemand')?'visible':'none');
  if(map.getLayer('demand-unmet'))map.setLayoutProperty('demand-unmet','visibility',active?'none':isVisible('unmetDemand')?'visible':'none');
  if(map.getLayer('sim-stops'))map.setLayoutProperty('sim-stops','visibility',active&&isVisible('shuttle')?'visible':'none');
  if(map.getLayer('sim-demand'))map.setLayoutProperty('sim-demand','visibility',active&&isVisible('unmetDemand')?'visible':'none');
}
function ensureMapSource() {
  const map=sim.map;if(!map?.getSource('d-routes')){byId('map').dataset.simSource='waiting';return;}
  if(!map.getSource('sim-stops')){
    map.addSource('sim-stops',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-stops',type:'circle',source:'sim-stops',paint:{'circle-color':['case',['get','selected'],'#ed3d51','#0eaaad'],'circle-radius':['case',['get','selected'],9,6],'circle-stroke-color':'#fff','circle-stroke-width':2}});
    map.on('click','sim-stops',event=>{const f=event.features?.[0];if(!f)return;selectRoute(f.properties.route_id);new sim.maplibregl.Popup().setLngLat(f.geometry.coordinates).setText(`${f.properties.order}. ${f.properties.name} (${f.properties.stop_id}) · ${f.properties.vehicles}대`).addTo(map);});
  }
  if(!map.getSource('sim-demand')){
    map.addSource('sim-demand',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-demand',type:'circle',source:'sim-demand',paint:{'circle-color':['case',['<',['coalesce',['get','delta_unserved'],0],0],'#0eaaad','#d43161'],'circle-radius':['interpolate',['linear'],['get','render_count'],0,0,5,5,20,9,50,15,100,21,250,30],'circle-opacity':.62,'circle-stroke-color':'#a21b4c','circle-stroke-width':['case',['!=',['coalesce',['get','delta_unserved'],0],0],3,1]}} ,'sim-stops');
    map.on('click','sim-demand',event=>{const p=event.features?.[0]?.properties;if(!p)return;const comparison=p.other_unserved==null?'':` · ${p.comparison_label||'상대안'} ${num(p.other_unserved)}명 → 선택안 ${num(p.passenger_count)}명 (${Number(p.delta_unserved)>0?'+':''}${num(p.delta_unserved)}명)`;const details=p.demand_kind==='M05_unserved'?` · 평가 수요 ${num(p.evaluated_total)}명 · 미충족률 ${num(p.unserved_rate)}% · M05 출발 정류장 집계`:' · C/B 입력의 셔틀 투입 전 잔여 수요 · 셔틀 운행 후 미충족 인원 아님';new sim.maplibregl.Popup().setLngLat(event.lngLat).setText(`${p.name} (${p.stop_id}) · ${p.demand_label} ${num(p.passenger_count)}명${comparison}${details}`).addTo(map);});
  }
  byId('map').dataset.simSource='ready';mapLayers(sim.active);drawStops();drawDemand();
}
function animateDemand(source, features) {
  if(sim.demandFrame)cancelAnimationFrame(sim.demandFrame);
  const key=f=>`${f.properties.source_namespace||'C_region_network'}:${f.properties.stop_id}:${f.geometry.coordinates.join(',')}`;
  const previous=new Map(sim.displayedDemand.map(f=>[key(f),f]));
  const next=new Map(features.map(f=>[key(f),f]));
  const keys=new Set([...previous.keys(),...next.keys()]);
  const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  const render=(progress,keepExiting)=>{
    const eased=1-(1-progress)**3;
    const frames=[];
    for(const id of keys){
      const before=previous.get(id),after=next.get(id);
      if(!after&&!keepExiting)continue;
      const from=Number(before?.properties.render_count??before?.properties.passenger_count??0);
      const to=Number(after?.properties.passenger_count??0);
      const value=Math.max(0,from+(to-from)*eased);
      if(value<.05)continue;
      const base=after||before;
      frames.push({...base,properties:{...base.properties,render_count:value}});
    }
    sim.displayedDemand=frames;
    source.setData({type:'FeatureCollection',features:frames});
  };
  if(reduced||!sim.active||!isVisible('unmetDemand')){render(1,false);sim.demandFrame=0;return;}
  const start=performance.now();
  const step=now=>{if(sim.map?.getSource('sim-demand')!==source){sim.demandFrame=0;return;}const progress=Math.min(1,(now-start)/520);render(progress,progress<1);sim.demandFrame=progress<1?requestAnimationFrame(step):0;};
  sim.demandFrame=requestAnimationFrame(step);
}
function drawDemand() {
  const source=sim.map?.getSource('sim-demand');if(!source)return;
  const hour=byId('sim-hour').value;
  const spatial=sim.result?.spatial_outcomes;
  let features=[];let label='파업 잔여 수요';
  if(spatial?.status==='available'){
    label='M05 미충족';
    const other=sim.beforeResult?.spatial_outcomes?.status==='available'?sim.beforeResult.spatial_outcomes:sim.table?.comparable?sim.table.rows.find(r=>r.plan!==sim.result.selection.plan)?.spatial_outcomes:null;
    const comparisonLabel=sim.beforeResult?'이전 조건':'상대안';
    const otherByStop=new Map((other?.status==='available'?other.features:[]).map(f=>[f.properties.stop_id,f.properties]));
    features=spatial.features.map(f=>{const counts=hour==='all'?f.properties:f.properties.hours?.[hour];const otherCounts=hour==='all'?otherByStop.get(f.properties.stop_id):otherByStop.get(f.properties.stop_id)?.hours?.[hour];const comparable=otherCounts?.total>=5?otherCounts:null;return counts&&counts.total>=5&&counts.unserved>0?{...f,properties:{...f.properties,passenger_count:counts.unserved,demand_kind:'M05_unserved',demand_label:'M05 미충족',evaluated_total:counts.total,unserved_rate:Math.round(1000*counts.unserved/counts.total)/10,other_unserved:comparable?.unserved??null,delta_unserved:comparable?counts.unserved-comparable.unserved:null,comparison_label:comparisonLabel}}:null;}).filter(Boolean);
  }else if(sim.residual?.status==='available'){
    features=sim.residual.features.map(f=>{const count=hour==='all'?f.properties.passenger_count:f.properties.hours?.[hour];return count>=5?{...f,properties:{...f.properties,passenger_count:count,demand_kind:'pre_shuttle_residual',demand_label:'셔틀 투입 전 잔여 수요'}}:null;}).filter(Boolean);
  }
  animateDemand(source,features);sim.renderedHour=hour;
  let note=byId('sim-demand-note');if(!note){note=document.createElement('div');note.id='sim-demand-note';note.className='sim-empty';byId('sim-source').before(note);}
  const compared=features.filter(f=>f.properties.delta_unserved!=null);
  const changes=compared.filter(f=>f.properties.delta_unserved!==0);
  const biggest=changes.reduce((best,f)=>!best||Math.abs(f.properties.delta_unserved)>Math.abs(best.properties.delta_unserved)?f:best,null);
  const biggestNote=biggest?` · 최대 변화 ${biggest.properties.name} ${biggest.properties.delta_unserved>0?'+':''}${num(biggest.properties.delta_unserved)}명`:'';
  const comparisonNote=compared.length?` · ${sim.beforeResult?'이전 조건':'상대안'} 대비 변화 정류장 ${changes.length}곳${biggestNote} · 청록=미충족 감소, 굵은 테두리=변화`:' · 비교할 상대 공간 결과 미연결';
  note.textContent=(features.length?`${label} 원 ${features.length}곳 · ${label==='M05 미충족'?'선택한 운행안·차량·날짜의 B 원본 출발 정류장 집계'+comparisonNote:'C/B 입력 번들 demand_bins.csv · '+(hour==='all'?'06:00~10:00 합계':'선택 시간대')+' · 교통수단 조건에 따라 변화 · 셔틀 투입 전 잔여 수요이므로 차량/운행안별 수송 효과는 아님'} · 원 크기는 인원에 비례 · 5명 미만 시간칸은 숨겨 원의 합계가 전체 수요보다 작을 수 있음${sim.residual?.unmatched_rows?' · 좌표 미매칭 '+sim.residual.unmatched_rows+'행':''}`:spatial?.status==='available'?'선택한 조건에서 표시 가능한 정류장별 미충족 인원이 0명입니다.':sim.residual?.status==='available'?'선택한 시간에 5명 이상인 잔여 수요 출발지가 없습니다.':'C/B 입력 번들 잔여 수요와 B 정류장별 미충족 결과가 없습니다.')+' 지도 표시 체크는 수요 계산을 바꾸지 않습니다.';
}
function drawStops() {
  if(!sim.map?.getSource('sim-stops')||!sim.result)return;
  const features=sim.result.geojson.features.map(f=>({...f,properties:{...f.properties,selected:f.properties.route_id===sim.selectedRoute}}));
  sim.map.getSource('sim-stops').setData({type:'FeatureCollection',features});
  if(sim.selectedRoute){const selected=features.filter(f=>f.properties.selected);if(selected.length){const bounds=new sim.maplibregl.LngLatBounds();selected.forEach(f=>bounds.extend(f.geometry.coordinates));sim.map.fitBounds(bounds,{padding:80,maxZoom:14});}}
}
function drawRegionMarkers(rows) {
  sim.markers.forEach(m=>m.remove());sim.markers=[];
  if(!sim.active||!sim.map)return;
  for(const row of rows){
    const region=sim.options.regions.find(r=>r.id===row.region);
    const marker=document.createElement('button');marker.type='button';marker.className=`sim-marker ${row.region===byId('sim-region').value?'active':''}`;
    marker.style.width=`${Math.round(56+Math.sqrt(row.total)*.20)}px`;
    marker.style.height=marker.style.width;
    marker.title=`${region.name} · 잔여 수요 ${num(row.total)}명 · 충족 ${num(row.served)}명 · ${row.fleet}대 · 보고서 5일 평균`;
    marker.setAttribute('aria-label',marker.title);
    marker.innerHTML=`${pct(row.rate_percent)}<span>${safe(region.name)}</span>`;
    marker.onclick=()=>{byId('sim-region').value=region.id;syncFleet();refresh();};
    sim.markers.push(new sim.maplibregl.Marker({element:marker}).setLngLat(region.center).addTo(sim.map));
  }
}
function bar(label,value) {return `<div class="sim-bar"><span>${safe(label)}</span><div class="track"><i style="width:${Math.min(100,Math.max(0,value||0))}%"></i></div><b>${pct(value)}</b></div>`;}
const outcomeLabels={report_only:'보고서 5일 평균',data_incomplete:'원본 미연결',calculation_failed:'계산 실패',verification_unknown:'검증 미확인',verification_failed:'검증 실패',passed:'검증 통과'};
function drawExecution() {
  const result=sim.result, outcome=result.outcome||{};
  if(outcome.status==='calculation_failed')byId('sim-verify').textContent='계산 실패';
  else if(outcome.status==='verification_failed')byId('sim-verify').textContent='검증 실패';
  else if(outcome.status==='verification_unknown')byId('sim-verify').textContent='검증 미확인';
  let node=byId('sim-execution');
  if(!node){node=document.createElement('div');node.id='sim-execution';node.className='sim-empty';byId('sim-verification').after(node);}
  const entries=sim.runs?.runs||[];
  const history=sim.selected?.history||[];
  node.innerHTML=`<b>실행 상태: ${safe(outcomeLabels[outcome.status]||outcome.status||'미확인')}</b>`+
    `<p>실행일 ${safe(result.selection.date)} · 운행안 ${safe(result.plan_id||'원본 미연결')} / revision ${safe(result.revision??'미확인')} · 입력 ${safe(result.execution.input_bundle_id||'미확인')}</p>`+
    (outcome.reason?`<p>사유: ${safe(outcome.reason)}</p>`:'')+
    (outcome.error?`<p>오류 ${safe(outcome.error.code)} · 단계 ${safe(outcome.error.details?.stage_failed||'미확인')}</p>`:'')+
    `<h3>날짜별 B 실행 이력 (${entries.length}건)</h3>`+
    (entries.length?`<ul>${entries.map(r=>`<li>${safe(r.date)} · ${safe(outcomeLabels[r.status]||r.status)} · ${safe(r.reason||'')} · plan ${safe(r.plan_id||'미확인')} / ${safe(r.revision??'미확인')}</li>`).join('')}</ul>`:'<p>runs.json 원본 실행 이력이 연결되지 않았습니다.</p>')+
    `<h3>운행안 선택 이력 (${history.length}건)</h3>`+
    (history.length?`<ul>${history.slice(-5).reverse().map(h=>`<li>${safe(h.selected_at)} · ${safe(h.plan_id)} / ${safe(h.revision)} · ${safe(h.region)} · 실험 대안 선택 · 실제 운행 미확정</li>`).join('')}</ul>`:'<p>선택 이력이 없습니다. 실제 운행 확정 상태는 별도 승인 데이터가 필요합니다.</p>');
}
function drawMetrics() {
  const result=sim.result;const hour=byId('sim-hour').value;
  const m=result.outcome?.status==='calculation_failed'?null:hour==='all'?result.metrics:result.hourly?.find(x=>String(x.hour).padStart(2,'0')===hour);
  for(const [id,value] of Object.entries({total:m?.total,served:m?.served,unserved:m?.unserved??(m?m.total-m.served:null),vehicles:result.routes.length?result.routes.reduce((sum,r)=>sum+r.vehicles,0):null}))byId(`sim-${id}`).textContent=num(value);
  byId('sim-rate').textContent=pct(m?.rate_percent);
  byId('sim-verify').textContent=result.verification.source==='verifier_report.json'?(result.verification.pass?(result.verification.fully_verified?'통과':'통과·부분'):'실패'):result.verification.source==='report_v4_aggregate'?'개별 미확인':'미확인';
  byId('sim-result-status').textContent=m?.source==='report_v4_5day_mean'?'PR #41 보고서의 구 단위 5일 평균입니다. 지도 빨간 원은 C/B 입력 번들의 셔틀 투입 전 잔여 수요입니다. C팀 노선·차량 배분, 날짜별 M05 원본, M07 개별 검증, 셔틀 투입 후 정류장별 미충족 결과는 미연결입니다.':m?.source==='M05_metrics_day'?(result.execution.provenance==='local_recomputed'?'제공된 입력과 C/B/M07 코드로 로컬에서 다시 계산한 결과입니다. PR #41의 원본 실행 파일은 아닙니다. 선택한 날짜·차량·운행안의 정류장별 미충족 원이 표시됩니다.':'선택한 날짜의 M05 실행 파일 결과입니다. 노선·공간 수요와 M07 상태는 아래에서 각각 확인하세요.'):`선택한 ${result.selection.date==='mean'?'기간·시나리오':'날짜·시간대'} 조건의 M05 결과 파일이 없습니다. 지도 잔여 수요는 C/B 입력 번들에서 조회합니다.`;
  const runSource=result.execution.provenance==='local_recomputed'?'로컬 재계산 M05':'제공된 M05 실행 파일';
  byId('sim-source').textContent=`${hour==='all'?'전체 시간':'선택 시간 '+hour+'시'} · ${hour==='all'?(m?.source==='M05_metrics_day'?runSource:m?.source==='report_v4_5day_mean'?'PR #41 보고서 5일 평균':'원본 미연결'):(result.hourly?runSource+' 시간대':'시간대 원본 미연결')} · ${result.selection.date} · ${result.plan_id||'plan_id 미연결'} / revision ${result.revision??'미연결'} · ${result.map_status} · A GTFS 정상 교통망과 관측 수요는 B 평가와 별개입니다.`;
  byId('mapStatus').textContent=`${sim.options.regions.find(r=>r.id===result.selection.region).name} · 정류장 수요 원은 아래 출처 표시 참조`;
  document.querySelector('.map-head-title').textContent='시뮬레이션 결과 지도 · 선택 자치구';
  document.querySelector('.map-head p').textContent=`${getBaseMode()==='real'?'A GTFS 정류장·도시철도 정차 관계를 배경으로 표시 · ':''}빨간 원: 셔틀 투입 전 파업 잔여 수요 또는 M05 원본 미충족 수요`;
  document.querySelector('.map-source').textContent='C/B 입력 번들 잔여 수요 또는 M05 정류장별 미충족 · 출처 별도 표시';
}
function selectRoute(id) {sim.selectedRoute=id;drawRoutes();drawStops();}
function drawRoutes() {
  const routes=sim.result.routes;
  const other=sim.table?.rows.find(row=>row.plan!==sim.result.selection.plan);
  const otherStops=new Set((other?.routes||[]).map(r=>r.stop_ids.join('|')));
  byId('sim-routes').innerHTML=routes.length?routes.map(r=>`<button class="sim-route ${r.route_id===sim.selectedRoute?'active':''}" data-id="${safe(r.route_id)}">${safe(r.route_id)} · 후보 ${safe(r.candidate_id||'미연결')} · ${num(r.vehicles)}대 · ${r.stop_ids.length} 정차점 · ${!other?.routes.length?'상대안 미연결':otherStops.has(r.stop_ids.join('|'))?'상대안에도 같은 정차 순서':'선택 안 고유 정차 순서'}</button>`).join(''):'<div class="sim-empty">C팀 원본 노선·차량 배분 미연결. 보고서의 총 노선 수로 개별 노선을 만들지 않습니다.</div>';
  byId('sim-routes').querySelectorAll('button').forEach(b=>b.onclick=()=>selectRoute(b.dataset.id));
  const route=routes.find(r=>r.route_id===sim.selectedRoute);
  byId('sim-route-detail').innerHTML=route?`<b>${safe(route.route_id)}</b> · ${num(route.vehicles)}대 · ${safe(route.origin||'출처 미제공')}<ol>${route.stops.map(s=>`<li>${safe(s.name||'이름 미연결')} (${safe(s.id)})</li>`).join('')}</ol>도로 경로 선형 미검증`:'노선을 선택하면 출발·경유·도착 정류장 순서가 표시됩니다.';
}
function drawTable() {
  const table=sim.table;
  byId('sim-compare-note').textContent=`${table.reason} · ${table.source==='report_v4_5day_mean'?'보고서 5일 평균':'M05 일별 원본'} · C팀 근사값이 아닌 B팀 M1 충족/잔여 수요. 선택은 개별 날짜의 M05 성공·M07 통과 원본이 있을 때만 가능합니다.`;
  byId('sim-table').innerHTML=table.rows.map(row=>{
    const m=row.metrics;const v=row.verification;const eligible=Boolean(row.outcome?.status==='passed'&&row.plan_id&&row.revision!=null&&m?.source==='M05_metrics_day'&&row.execution.sim_rc===0&&v.source==='verifier_report.json'&&v.pass===true&&!(v.failed_checks||[]).length&&!(v.not_run_checks||[]).length);
    return `<tr><td><b>${row.plan==='ours'?'우리 안':'최단경로 피더안'}</b><div class="sim-muted">${safe(row.method)}</div></td><td>${num(m?.served)}</td><td>${pct(m?.rate_percent)}</td><td>${num(m?.unserved)}</td><td>${row.routes.length?num(row.routes.reduce((n,r)=>n+r.vehicles,0)):'배분 미연결'} / ${num(m?.route_count)}</td><td>${v.source==='verifier_report.json'?(v.pass?(v.fully_verified?'통과':'통과·부분 검증'):'실패'):'개별 미확인'}</td><td><button data-select="${row.plan}" ${eligible?'':'disabled'}>선택</button></td></tr>`;
  }).join('');
  byId('sim-table').querySelectorAll('[data-select]').forEach(button=>button.onclick=()=>choosePlan(button.dataset.select));
}
async function choosePlan(plan) {
  const row=sim.table.rows.find(x=>x.plan===plan);
  const payload={...sim.table.selection,plan,plan_id:row.plan_id,revision:row.revision,network_snapshot_id:row.network_snapshot_id};
  try {await readPost('/api/d/simulation/selection',payload);await refresh();} catch(error){byId('sim-selection').textContent=`선택 실패: ${error.message}`;}
}
async function readPost(path,payload) {const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.message||response.statusText);return data;}
function drawSummary() {
  const summary=sim.summary;
  byId('sim-seoul').innerHTML=summary.districts.map(d=>`<button data-region="${d.region||''}" data-status="${d.status}" title="${safe(d.region?`${d.fleet}대 · ${pct(d.metrics?.rate_percent)} · 비교안 대비 ${d.improvement_pp==null?'미연결':d.improvement_pp+'%p'} · ${d.selected?'선택됨':'미선택'}`:'미실행')}" ${d.region?'':'disabled'}>${safe(d.name)} · ${d.status==='report_only'?'보고서':d.status==='run'?'M05 원본':d.status==='data_incomplete'?'데이터 부족':'미실행'}${d.metrics?.rate_percent!=null?' · '+pct(d.metrics.rate_percent):''}${d.selected?' ✓':''}</button>`).join('');
  byId('sim-seoul').querySelectorAll('[data-region]').forEach(b=>b.onclick=()=>{if(!b.dataset.region)return;byId('sim-region').value=b.dataset.region;changeRegion();});
  const agg=summary.aggregate;
  byId('sim-seoul-aggregate').textContent=`서울 전체 아님 · ${agg.scope} · ${agg.region_count}개 구 범위 · 합산 충족 ${num(agg.served)} / 수요 ${num(agg.total)} · ${pct(agg.rate_percent)} · 구별 비율 단순 평균 아님`;
  byId('sim-selection').textContent=sim.selected?.selected?`선택됨: ${sim.selected.selected.plan_id} / revision ${sim.selected.selected.revision} · ${sim.selected.selected.region} · ${sim.selected.selected.selected_at} · 선택 이력 ${sim.selected.history.length}건 · 승인된 실제 운행 아님`:'선택된 실제 운행안 없음 · 보고서 집계만으로는 선택할 수 없습니다.';
}
function drawCharts() {
  const rows=sim.table.rows;const c=sim.comparison;
  byId('sim-chart').innerHTML=`<h3>우리 안 / 피더안 · 동일 조건</h3>${rows.map(r=>bar(r.plan==='ours'?'우리 안':'피더안',r.metrics?.rate_percent)).join('')}`+
    `<h3>4개 구 · 차량 조건 다름</h3>${c.regions.map(r=>bar(`${sim.options.regions.find(x=>x.id===r.region).name} ${r.fleet}대`,r.rate_percent)).join('')}`+
    `<h3>차량 대수별 · 잔여 수요 분모 다름</h3>${c.fleets.map(r=>bar(`${r.fleet}대`,r.rate_percent)).join('')}`+
    `<h3>실험 / 테스트 · 날짜 다름</h3>${c.periods.map(r=>bar(r.period==='exp'?'10/26~30':'11/02~06',r.rate_percent)).join('')}`+
    '<p class="sim-muted">구·차량·기간 그래프는 PR #41의 각 5일 평균입니다. 조건과 잔여 수요가 달라 단순 성능 순위로 해석할 수 없습니다.</p>';
  const reasons=sim.result.reasons;
  byId('sim-reasons').innerHTML=reasons?`<h2 style="margin-top:14px">미충족 사유</h2><p class="sim-muted">${sim.result.metrics?.source==='report_v4_5day_mean'?'PR #41 보고서 §6 · affected 실험 5일 평균':'M05 해당 날짜 원본'} · 막대는 잔여 수요 대비 비율</p>${Object.entries({no_connection:'노선 없음',service_end:'운행 종료',wait:'대기 초과',journey:'통행 초과'}).map(([k,label])=>bar(`${label} ${num(reasons[k])}명`,100*(reasons[k]||0)/(sim.result.metrics?.total||1))).join('')}<p class="sim-muted">만차 거절 ${num(reasons.refusal_events)}건은 최종 미충족 분류와 별개</p>`:'<div class="sim-empty">선택 조건의 M2 사유 원본 미연결</div>';
  const v=sim.result.verification;
  byId('sim-verification').textContent=v.source==='verifier_report.json'?`M05 실행 rc=${sim.result.execution.sim_rc??'미제공'} · M07 pass=${v.pass} · fully_verified=${v.fully_verified} · 실패 ${(v.failed_checks||[]).join(', ')||'없음'} · 미실행 ${(v.not_run_checks||[]).join(', ')||'없음'} · 미검증 ${(v.not_verified_checks||[]).join(', ')||'없음'} · 평가일 ${sim.result.selection.date} · 입력 ${sim.result.execution.input_bundle_id||'미제공'} · 망 ${sim.result.network_snapshot_id||'미제공'} · 검증 시각 ${v.generated_at||'미제공'}`:'보고서 전체 640회 통과 기록 · 선택 실행의 verifier_report.json 미연결 · V12/V15/V16 미검증';
}
async function refresh() {
  const seq=++sim.request;
  byId('mapStatus').textContent='C/B 결과를 불러오는 중…';
  try {
    const q=query();const [result,table,comparison,summary,selected,runs,residual]=await Promise.all([
      read(`/api/d/simulation/result?${q}`),read(`/api/d/simulation/table?${q}`),
      read(`/api/d/simulation/comparison?${q}`),read(`/api/d/simulation/seoul?${q}`),
      read('/api/d/simulation/selection'),read(`/api/d/simulation/runs?${q}`),
      read(`/api/d/simulation/residual?${q}`).catch(()=>null)]);
    if(seq!==sim.request||!sim.active)return;
    const old=sim.result,oldSelection=old?.selection,newSelection=result.selection;
    const comparableSpatial=old?.spatial_outcomes?.status==='available'&&result.spatial_outcomes?.status==='available'&&sim.renderedHour===byId('sim-hour').value&&oldSelection.region===newSelection.region&&oldSelection.variant===newSelection.variant&&oldSelection.period===newSelection.period&&oldSelection.date===newSelection.date&&old.network_snapshot_id===result.network_snapshot_id&&old.metrics?.total===result.metrics?.total&&(oldSelection.fleet!==newSelection.fleet||oldSelection.plan!==newSelection.plan);
    sim.beforeResult=comparableSpatial?old:null;
    sim.result=result;sim.residual=residual;sim.table=table;sim.comparison=comparison;sim.summary=summary;sim.selected=selected;sim.runs=runs;sim.selectedRoute=null;
    drawMetrics();drawRoutes();drawTable();drawSummary();drawCharts();drawExecution();drawRegionMarkers([]);drawStops();drawDemand();mapLayers(true);
  }catch(error){if(seq===sim.request)byId('mapStatus').textContent=`시뮬레이션 결과 조회 실패: ${error.message}`;}
}
async function changeRegion(){syncFleet();await setDistrict(byId('sim-region').value);mapLayers(true);await preferAvailableRunDate();await refresh();}
async function activate() {
  if(sim.active||sim.busy)return;
  sim.busy=true;sim.previousMode=getBaseMode();
  try {await enableA();} catch(error) {byId('mapStatus').textContent=`A GTFS 전환 실패: ${error.message}`;}
  sim.active=true;document.querySelector('.app').classList.add('sim-active');
  await setDistrict(byId('sim-region').value);
  byId('simModeButton').textContent='기존 DEMO / A GTFS';
  byId('dataSourceChip').textContent=`● C/B 시뮬레이션 · ${getBaseMode()==='real'?'A GTFS 지도':'지도 배경만'}`;
  sim.map?.resize();ensureMapSource();await preferAvailableRunDate();refresh();
  sim.busy=false;
}
async function deactivate() {
  if(!sim.active||sim.busy)return;
  sim.busy=true;
  sim.active=false;sim.request++;document.querySelector('.app').classList.remove('sim-active');
  if(sim.demandFrame)cancelAnimationFrame(sim.demandFrame);sim.demandFrame=0;sim.displayedDemand=[];
  sim.map?.getSource('sim-demand')?.setData({type:'FeatureCollection',features:[]});
  sim.markers.forEach(m=>m.remove());sim.markers=[];mapLayers(false);await restoreD(sim.previousMode);sim.map?.resize();
  byId('simModeButton').textContent='시뮬레이션 결과';
  sim.busy=false;
}
export async function initSimulationMode({map,maplibregl,restore,enableA:connectA,getBaseMode:mode,setDistrict:chooseDistrict,isVisible:layerVisible}) {
  sim.map=map;sim.maplibregl=maplibregl;restoreD=restore;enableA=connectA;getBaseMode=mode;setDistrict=chooseDistrict;isVisible=layerVisible;
  document.addEventListener('d-layer-visibility',()=>mapLayers(sim.active));
  document.addEventListener('d-map-ready',()=>{if(sim.active)ensureMapSource();});
  if(map){map.on('idle',()=>{if(sim.active&&!map.getSource('sim-demand'))ensureMapSource();});}
  sim.options=await read('/api/d/simulation/options');
  options('sim-region',sim.options.regions.map(r=>[r.id,r.name]));
  options('sim-hour',[['all','전체 (06:00~10:00)'],...[6,7,8,9].map(h=>[String(h).padStart(2,'0'),`${String(h).padStart(2,'0')}:00~${String(h+1).padStart(2,'0')}:00`])]);
  const variantLabels={affected:'기본 파업 잔여 (affected)',affected_maeul:'마을버스 접근 수요 제외',affected_gg:'경기버스 접근 수요 제외',affected_maeul_gg:'마을·경기 접근 수요 제외'};
  options('sim-variant',sim.options.variants.map(v=>[v,variantLabels[v]||v]));syncFleet();syncDates();
  byId('simModeButton').onclick=()=>sim.active?deactivate():activate();
  byId('sim-all-regions').onclick=fitRegions;
  byId('sim-region').onchange=changeRegion;
  byId('sim-period').onchange=async()=>{syncDates();await preferAvailableRunDate();refresh();};
  for(const key of ['fleet','variant','plan'])byId(`sim-${key}`).onchange=async()=>{await preferAvailableRunDate();refresh();};
  for(const key of ['hour','date'])byId(`sim-${key}`).onchange=refresh;
  if(location.pathname.startsWith('/simulation'))await activate();
}
