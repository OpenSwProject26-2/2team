/* 합성 DEMO·A GTFS·저장된 C/B 결과가 공유하는 D 노선 실험실 화면. */
const byId = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '미연결' : Number(value).toLocaleString('ko-KR',{maximumFractionDigits:1});
const pct = value => value == null ? '미연결' : `${Number(value).toFixed(2)}%`;
const sim = {active:false, busy:false, options:null, districts:null, districtGeojson:null, map:null, maplibregl:null, result:null, beforeResult:null, beforeResidual:null, transport:null, transportRequest:0, renderedHour:null, residual:null, table:null, comparison:null, summary:null, runs:null, selectedRoute:null, markers:[], stopMarkers:[], request:0, hidden:new Map(), demandFrame:0, displayedDemand:[]};
let getBaseMode,setDistrict,isVisible,fitSeoul,refreshBaseVisibility;
const query = () => {
  const values=Object.fromEntries(['region','fleet','hour','variant','plan','period','date'].map(k=>[k,byId(`sim-${k}`).value]));
  if(values.region==='all')values.region=sim.options.regions[0].id;
  if(values.fleet==='all')values.fleet=sim.options.regions[0].fleets[0];
  return new URLSearchParams(values);
};
async function read(path) {const response=await fetch(path);const data=await response.json();if(!response.ok)throw Error(data.message||response.statusText);return data;}
function options(id, values, keep) {const node=byId(id);node.replaceChildren(...values.map(([value,label])=>{const el=document.createElement('option');el.value=value;el.textContent=label;return el;}));if(keep&&[...node.options].some(x=>x.value===keep))node.value=keep;}
// 상단 집계는 마을·경기 접근 수요를 제외한 저장 조건으로 고정한다. 체크는 이 값을 바꾸지 않는다.
function syncFleet() {const region=sim.options.regions.find(r=>r.id===byId('sim-region').value);const first=region||sim.options.regions[0];options('sim-fleet',region?region.fleets.map(n=>[String(n),`${n}대`]):[['all','구별 첫 차량 조건']],byId('sim-fleet').value);byId('sim-fleet').disabled=!region;const variants=first.variants||sim.options.variants;byId('sim-variant').value=variants.includes('affected_maeul_gg')?'affected_maeul_gg':variants.includes('affected')?'affected':variants[0]||'affected';options('sim-plan',(first.plans||[]).map(p=>[p.id,p.name||p.id]),byId('sim-plan').value);}
function syncDates() {const dates=sim.options.periods[byId('sim-period').value];options('sim-date',[['mean','5일 평균'],...dates.map(d=>[d,d])],byId('sim-date').value);}
function simulationNotice(message){byId('sim-transport-notice').textContent=message;}
const disabledModes=()=>['city','village','gyeonggi','metro'].filter(kind=>!isVisible(kind));
const storedReachVariant=()=>`affected${isVisible('village')?'_maeul':''}${isVisible('gyeonggi')?'_gg':''}`;
function changeTransportLayer(){
  if(!sim.active)return;
  if(byId('sim-region').value==='all'){
    simulationNotice('서울 전체 지도에는 경계와 저장된 충족률만 표시합니다. 자치구를 선택하면 교통수단 제외 조건의 정류장별 결과를 조회합니다.');
    return;
  }
  void loadTransportDemand();
}
async function loadTransportDemand(){
  const token=++sim.transportRequest;
  if(!sim.active||!sim.result||byId('sim-region').value==='all')return;
  const modes=disabledModes();
  if(!modes.length){
    sim.transport=null;drawDemand();
    simulationNotice('모든 교통수단 표시 · 기본 마을·경기 접근 조건의 결과를 표시합니다.');
    return;
  }
  simulationNotice('교통수단 조건별 수요를 조회 중입니다. 현재 원은 조회가 끝날 때까지 유지됩니다.');
  const params=query();params.set('disabled_modes',modes.join(','));
  if(sim.result.network_snapshot_id)params.set('network_snapshot_id',sim.result.network_snapshot_id);
  try{
    const data=await read(`/api/d/simulation/transport-demand?${params}`);
    if(token!==sim.transportRequest||!sim.active)return;
    if(data.status==='available'){
      sim.transport=data;drawDemand();
      simulationNotice(`${modes.join('·')} 제외 조건의 ${data.kind==='M05_unserved'?'M05 미충족':'셔틀 투입 전 잔여'} 수요 원을 표시합니다. 상단 집계는 기본 조건입니다.`);
      return;
    }
    if(data.status!=='snapshot_mismatch'&&(modes.includes('village')||modes.includes('gyeonggi'))&&sim.result.spatial_outcomes?.status!=='available'){
      const variant=storedReachVariant();
      const residualParams=query();residualParams.set('variant',variant);
      const residual=await read(`/api/d/simulation/residual?${residualParams}`);
      if(token!==sim.transportRequest||!sim.active)return;
      if(residual.status==='available'){
        const partial=modes.includes('city')||modes.includes('metro');
        sim.transport={status:'available',kind:'pre_shuttle_residual',features:residual.features,selection:{disabled_modes:modes},source:{input_bundle_id:residual.source,run_id:null},stored_variant:variant,partial};
        drawDemand();
        simulationNotice(`마을·경기 접근 조건 ${variant}의 C/B 셔틀 투입 전 잔여 수요를 표시합니다.${partial?' 시내버스·지하철 제외 효과는 자료가 없어 반영되지 않았습니다.':''} 상단 집계는 기본 조건입니다.`);
        return;
      }
    }
    sim.transport={status:'reference',selection:{disabled_modes:modes},message:data.message||'조건별 정류장 수요가 없습니다.'};drawDemand();
    simulationNotice(`${modes.join('·')} 제외 결과 미연결 · 빨간 원은 기본 조건 참고값이며 제외 효과를 반영하지 않습니다.`);
  }catch(error){
    if(token!==sim.transportRequest||!sim.active)return;
    sim.transport={status:'reference',selection:{disabled_modes:modes},message:error.message};drawDemand();
    simulationNotice(`교통수단 제외 결과 조회 실패 · 빨간 원은 기본 조건 참고값입니다: ${error.message}`);
  }
}
async function preferAvailableRunDate() {
  if(byId('sim-region').value==='all'||byId('sim-date').value==='mean')return;
  try {
    const runs=await read(`/api/d/simulation/runs?${query()}`);
    const dates=new Set((runs.runs||[]).filter(r=>r.status==='passed').map(r=>r.date));
    const field=byId('sim-date');
    if(!dates.has(field.value))field.value=[...dates][0]||'mean';
  }catch {byId('sim-date').value='mean';}
}
function mapLayers(active) {
  const map=sim.map;if(!map?.getSource('d-routes'))return;
  const seoulOverview=active&&byId('sim-region').value==='all';
  const baseOverview=!active&&getBaseMode()==='real'&&byId('districtSelect').value==='all';
  const hideNetwork=seoulOverview||baseOverview;
  const legend=document.querySelector('.map-legend');if(legend)legend.style.display=hideNetwork?'none':'';
  if(active)for(const id of ['overview-boundary','overview-percent-bg','overview-percent-label'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');
  for(const kind of ['strike','city','gyeonggi','village','metro','shuttle','unknown_bus','unknown']){
    for(const suffix of ['','-unverified','-halo','-unverified-halo']){const id=`route-${kind}${suffix}`;if(map.getLayer(id))map.setLayoutProperty(id,'visibility',hideNetwork?'none':isVisible(kind)?'visible':'none');}
  }
  for(const id of ['schematic-routes','schematic-strike-halo','schematic-strike','schematic-selected'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',hideNetwork?'none':'visible');
  for(const id of ['selected-route','selected-route-stops','selected-stop-labels'])if(map.getLayer(id)&&active)map.setLayoutProperty(id,'visibility','none');
  for(const [id,key] of [['bus-stops','busStops'],['metro-stops','metroStops'],['metro-stop-labels','metroStops']])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',hideNetwork?'none':isVisible(key)&&(!id.startsWith('metro-')||isVisible('metro'))?'visible':'none');
  if(map.getLayer('demand-circles'))map.setLayoutProperty('demand-circles','visibility',active||baseOverview?'none':isVisible('observedDemand')?'visible':'none');
  if(map.getLayer('demand-unmet'))map.setLayoutProperty('demand-unmet','visibility',active||baseOverview?'none':isVisible('unmetDemand')?'visible':'none');
  if(map.getLayer('sim-stops'))map.setLayoutProperty('sim-stops','visibility','none');
  if(map.getLayer('sim-stop-labels'))map.setLayoutProperty('sim-stop-labels','visibility','none');
  if(map.getLayer('sim-route-lines'))map.setLayoutProperty('sim-route-lines','visibility',active&&!seoulOverview&&isVisible('shuttle')?'visible':'none');
  if(map.getLayer('sim-demand'))map.setLayoutProperty('sim-demand','visibility',active&&!seoulOverview&&isVisible('unmetDemand')?'visible':'none');
  if(map.getLayer('sim-walk400-demand'))map.setLayoutProperty('sim-walk400-demand','visibility',active&&!seoulOverview&&isVisible('walk400Demand')?'visible':'none');
  if(map.getLayer('sim-district-boundary'))map.setLayoutProperty('sim-district-boundary','visibility',active&&isVisible('districtBoundary')?'visible':'none');
  sim.stopMarkers.forEach(marker=>{const el=marker.getElement();el.style.display=active&&!seoulOverview&&isVisible('shuttle')&&isVisible('shuttleStops')?'':'none';});
}
function ensureMapSource() {
  const map=sim.map;if(!map?.getSource('d-routes')){byId('map').dataset.simSource='waiting';return;}
  if(!map.getSource('sim-stops')){
    map.addSource('sim-stops',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-stops',type:'circle',source:'sim-stops',paint:{'circle-color':['case',['get','selected'],'#ed3d51','#0eaaad'],'circle-radius':['case',['get','selected'],9,5],'circle-stroke-color':'#fff','circle-stroke-width':2}});
    map.addLayer({id:'sim-stop-labels',type:'symbol',source:'sim-stops',filter:['==',['get','selected'],true],layout:{'text-field':['to-string',['get','order']],'text-size':11,'text-font':['Noto Sans Bold'],'text-offset':[0,0.8],'text-allow-overlap':true},paint:{'text-color':'#172238','text-halo-color':'#fff','text-halo-width':1.5}});
    map.on('click','sim-stops',event=>{const f=event.features?.[0];if(!f)return;selectRoute(f.properties.route_id);new sim.maplibregl.Popup().setLngLat(f.geometry.coordinates).setText(`${f.properties.order}. ${f.properties.name} (${f.properties.stop_id}) · ${f.properties.vehicles}대`).addTo(map);});
  }
  if(!map.getSource('sim-route-lines')){
    map.addSource('sim-route-lines',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-route-lines',type:'line',source:'sim-route-lines',paint:{'line-color':['case',['get','selected'],'#0eaaad','#315ea8'],'line-width':['case',['get','selected'],6,3],'line-opacity':['case',['get','selected'],.95,.55]},layout:{'line-cap':'round','line-join':'round'}},'bus-stops');
    map.on('click','sim-route-lines',event=>{const routeId=event.features?.[0]?.properties?.route_id;if(routeId)selectRoute(routeId);});
    map.on('mouseenter','sim-route-lines',()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave','sim-route-lines',()=>{map.getCanvas().style.cursor='';});
  }
  if(!map.getSource('sim-district-boundary')){
    map.addSource('sim-district-boundary',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-district-boundary',type:'line',source:'sim-district-boundary',paint:{'line-color':'#334155','line-width':2.2,'line-opacity':.8}},'bus-stops');
  }
  if(!map.getSource('sim-demand')){
    map.addSource('sim-demand',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-demand',type:'circle',source:'sim-demand',paint:{'circle-color':['case',['<',['coalesce',['get','delta_unserved'],0],0],'#0eaaad','#d43161'],'circle-radius':['interpolate',['linear'],['get','render_count'],0,0,5,5,20,9,50,15,100,21,250,30],'circle-opacity':.62,'circle-stroke-color':'#a21b4c','circle-stroke-width':['case',['!=',['coalesce',['get','delta_unserved'],0],0],3,1]}} ,'bus-stops');
    map.on('click','sim-demand',event=>{if(map.queryRenderedFeatures(event.point,{layers:['bus-stops','metro-stops','metro-stop-labels','selected-route-stops']}).length)return;const p=event.features?.[0]?.properties;if(!p)return;const comparison=p.other_unserved==null?'':` · ${p.comparison_label||'상대안'} ${num(p.other_unserved)}명 → 현재 ${num(p.passenger_count)}명 (${Number(p.delta_unserved)>0?'+':''}${num(p.delta_unserved)}명)`;const details=p.demand_kind==='M05_unserved'?` · 평가 수요 ${num(p.evaluated_total)}명 · 미충족률 ${num(p.unserved_rate)}% · M05 출발 정류장 집계`:' · C/B 입력의 셔틀 투입 전 잔여 수요 · 셔틀 운행 후 미충족 인원 아님';new sim.maplibregl.Popup().setLngLat(event.lngLat).setText(`${p.name} (${p.stop_id}) · ${p.demand_label} ${num(p.passenger_count)}명${comparison}${details}`).addTo(map);});
  }
  if(!map.getLayer('sim-walk400-demand'))map.addLayer({id:'sim-walk400-demand',type:'circle',source:'sim-demand',filter:['>', ['to-number',['get','walk_distance_m'],0],400],paint:{'circle-color':'#d43161','circle-radius':['interpolate',['linear'],['get','render_count'],0,0,5,7,20,12,50,19,100,26],'circle-opacity':.72,'circle-stroke-color':'#8f1742','circle-stroke-width':2}},'bus-stops');
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
  const transport=sim.transport;
  const activeTransport=transport?.status==='available'?transport:null;
  const spatial=activeTransport?(activeTransport.kind==='M05_unserved'?{status:'available',features:activeTransport.features}:null):sim.result?.spatial_outcomes;
  const residual=activeTransport?(activeTransport.kind==='pre_shuttle_residual'?{status:'available',features:activeTransport.features}:null):sim.residual;
  let features=[];let label='파업 잔여 수요';
  if(spatial?.status==='available'){
    label='M05 미충족';
    const other=activeTransport?sim.result?.spatial_outcomes:sim.beforeResult?.spatial_outcomes?.status==='available'?sim.beforeResult.spatial_outcomes:sim.table?.comparable?sim.table.rows.find(r=>r.plan!==sim.result.selection.plan)?.spatial_outcomes:null;
    const comparisonLabel=activeTransport?'기본 조건':sim.beforeResult?'이전 조건':'상대안';
    const otherByStop=new Map((other?.status==='available'?other.features:[]).map(f=>[f.properties.stop_id,f.properties]));
    features=spatial.features.map(f=>{const counts=hour==='all'?f.properties:f.properties.hours?.[hour];const otherCounts=hour==='all'?otherByStop.get(f.properties.stop_id):otherByStop.get(f.properties.stop_id)?.hours?.[hour];const comparable=otherCounts?.total>=5?otherCounts:null;return counts&&counts.total>=5&&counts.unserved>0?{...f,properties:{...f.properties,passenger_count:counts.unserved,demand_kind:'M05_unserved',demand_label:'M05 미충족',evaluated_total:counts.total,unserved_rate:Math.round(1000*counts.unserved/counts.total)/10,other_unserved:comparable?.unserved??null,delta_unserved:comparable?counts.unserved-comparable.unserved:null,comparison_label:comparisonLabel}}:null;}).filter(Boolean);
  }else if(residual?.status==='available'){
    const baseline=activeTransport?sim.residual:sim.beforeResidual;
    const earlier=baseline?.status==='available'?new Map(baseline.features.map(f=>[f.properties.stop_id,f.properties])):null;
    features=residual.features.map(f=>{
      const count=hour==='all'?f.properties.passenger_count:f.properties.hours?.[hour];
      if(!Number.isFinite(count)||count<5)return null;
      const old=earlier?.get(f.properties.stop_id);
      const previous=old&&(hour==='all'?old.passenger_count:old.hours?.[hour]);
      return {...f,properties:{...f.properties,passenger_count:count,demand_kind:'pre_shuttle_residual',demand_label:'셔틀 투입 전 잔여 수요',other_unserved:previous??null,delta_unserved:previous==null?null:count-previous,comparison_label:activeTransport?'기본 조건':'이전 수단 조건'}};
    }).filter(Boolean);
  }
  animateDemand(source,features);sim.renderedHour=hour;
  let note=byId('sim-demand-note');if(!note){note=document.createElement('div');note.id='sim-demand-note';note.className='sim-empty';byId('sim-source').before(note);}
  if(transport?.status==='reference'){
    note.textContent=`${transport.selection.disabled_modes.join('·')} 제외 결과 미연결 · 현재 원 ${features.length}곳은 기본 조건 참고값 · 제외 효과 미반영 · ${transport.message}`;
    return;
  }
  const compared=features.filter(f=>f.properties.delta_unserved!=null);
  const changes=compared.filter(f=>f.properties.delta_unserved!==0);
  const biggest=changes.reduce((best,f)=>!best||Math.abs(f.properties.delta_unserved)>Math.abs(best.properties.delta_unserved)?f:best,null);
  const biggestNote=biggest?` · 최대 변화 ${biggest.properties.name} ${biggest.properties.delta_unserved>0?'+':''}${num(biggest.properties.delta_unserved)}명`:'';
  const comparisonNote=compared.length?` · ${sim.beforeResult||sim.beforeResidual?'이전 조건':'상대안'} 대비 변화 정류장 ${changes.length}곳${biggestNote} · 청록=수요 감소, 굵은 테두리=변화`:' · 비교할 상대 공간 결과 미연결';
  note.textContent=activeTransport?`${activeTransport.selection.disabled_modes.join('·')} 제외 · ${label} 원 ${features.length}곳 · ${activeTransport.source.input_bundle_id} · ${activeTransport.source.run_id||'셔틀 투입 전'}${activeTransport.stored_variant?' · C/B '+activeTransport.stored_variant+' 접근 조건 근사':''}${activeTransport.partial?' · 시내버스·지하철 제외 효과 미반영':''} · 상단 지표는 기본 조건 · 5명 미만 시간칸은 숨김`: (features.length?`${label} 원 ${features.length}곳 · ${label==='M05 미충족'?'선택한 운행안·차량·날짜의 B 원본 출발 정류장 집계'+comparisonNote:'C/B 입력 번들 demand_bins.csv · '+(hour==='all'?'06:00~10:00 합계':'선택 시간대')+' · 셔틀 투입 전 잔여 수요'+(sim.beforeResidual?comparisonNote:'')+' · 차량/운행안별 수송 효과는 아님'} · 원 크기는 인원에 비례 · 5명 미만 시간칸은 숨겨 원의 합계가 전체 수요보다 작을 수 있음${sim.residual?.unmatched_rows?' · 좌표 미매칭 '+sim.residual.unmatched_rows+'행':''}`:spatial?.status==='available'?'선택한 조건에서 표시 가능한 정류장별 미충족 인원이 0명입니다.':sim.residual?.status==='available'?'선택한 시간에 5명 이상인 잔여 수요 출발지가 없습니다.':'C/B 입력 번들 잔여 수요와 B 정류장별 미충족 결과가 없습니다.');
}
function drawStops() {
  if(!sim.map?.getSource('sim-stops')||!sim.result)return;
  sim.stopMarkers.forEach(marker=>marker.remove());sim.stopMarkers=[];
  const features=sim.result.geojson.features.map(f=>({...f,properties:{...f.properties,selected:f.properties.route_id===sim.selectedRoute}}));
  sim.map.getSource('sim-stops').setData({type:'FeatureCollection',features});
  const grouped=new Map();
  for(const feature of features){const id=feature.properties.route_id;if(!grouped.has(id))grouped.set(id,[]);grouped.get(id).push(feature);}
  const lines=[];
  for(const route of sim.result.routes){const ordered=(grouped.get(route.route_id)||[]).sort((a,b)=>Number(a.properties.order)-Number(b.properties.order));if(ordered.length<2)continue;lines.push({type:'Feature',properties:{route_id:route.route_id,vehicles:route.vehicles,selected:route.route_id===sim.selectedRoute,geometry_status:'stop_points_only_unverified'},geometry:{type:'LineString',coordinates:ordered.map(f=>f.geometry.coordinates)}});}
  sim.map.getSource('sim-route-lines')?.setData({type:'FeatureCollection',features:lines});
  for(const feature of features){
    const p=feature.properties;const pin=document.createElement('button');pin.type='button';pin.className='sim-shuttle-stop-pin';pin.title=`${p.order}. ${p.name} (${p.stop_id}) · ${p.vehicles}대`;
    pin.setAttribute('aria-label',pin.title);pin.style.cssText=`position:relative;width:30px;height:30px;border:2px solid ${p.selected?'#e13c59':'#2875a8'};border-radius:50%;background:#fff;box-shadow:0 2px 7px #24364c66;display:grid;place-items:center;cursor:pointer;padding:0;font-size:16px;z-index:${p.selected?3:1}`;
    const icon=document.createElement('span');icon.textContent='🚌';icon.setAttribute('aria-hidden','true');pin.append(icon);
    const order=document.createElement('small');order.textContent=String(p.order);order.style.cssText='position:absolute;right:-6px;top:-7px;min-width:15px;height:15px;padding:0 2px;border-radius:8px;background:#1e4e80;color:#fff;font:700 9px/15px sans-serif;text-align:center;border:1px solid white';pin.append(order);
    pin.onclick=event=>{event.stopPropagation();selectRoute(p.route_id);new sim.maplibregl.Popup().setLngLat(feature.geometry.coordinates).setText(`${p.order}. ${p.name} (${p.stop_id}) · ${p.vehicles}대`).addTo(sim.map);};
    sim.stopMarkers.push(new sim.maplibregl.Marker({element:pin,anchor:'center'}).setLngLat(feature.geometry.coordinates).addTo(sim.map));
  }
  if(sim.selectedRoute){const selected=features.filter(f=>f.properties.selected);if(selected.length){const bounds=new sim.maplibregl.LngLatBounds();selected.forEach(f=>bounds.extend(f.geometry.coordinates));sim.map.fitBounds(bounds,{padding:80,maxZoom:14});}}
}
function updateLabBoundary() {
  if(!sim.districtGeojson||!sim.districts||!sim.map?.getSource('sim-district-boundary'))return;
  const region=sim.options.regions.find(r=>r.id===byId('sim-region').value);
  const district=sim.districts.districts.find(d=>d.id===region?.id);
  const feature=sim.districtGeojson.features.find(f=>String(f.properties?.sgg)===String(district?.code));
  sim.map.getSource('sim-district-boundary').setData({type:'FeatureCollection',features:region?(feature?[feature]:[]):sim.districtGeojson.features});
  mapLayers(sim.active);
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
    marker.onclick=()=>{byId('sim-region').value=region.id;void changeRegion();};
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
  const regionName=sim.options.regions.find(r=>r.id===result.selection.region)?.name||result.selection.region;
  byId('mapStatus').textContent=result.spatial_outcomes?.status==='available'
    ?`${regionName} · ${result.selection.fleet}대 M05 출발 정류장별 미충족 수요 원`
    :`${regionName} · ${result.selection.fleet}대 합계만 조회됨 · 정류장별 M05 결과가 없어 지도 원에는 차량 수 효과 미반영`;
  document.querySelector('.map-head-title').textContent='교통망 노선 실험실 · 시뮬레이션 결과';
  document.querySelector('.map-head p').textContent=`${getBaseMode()==='real'?'A GTFS 정상 교통망을 배경으로 표시 · ':''}실선=정류장 순서 연결 개략선(도로 경로 아님) · 수요 원은 실제 결과 출처를 표시`;
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
  byId('sim-route-detail').innerHTML=route?`<b>${safe(route.route_id)}</b> · ${num(route.vehicles)}대 · ${safe(route.origin||'출처 미제공')}<ol>${route.stops.map(s=>`<li>${safe(s.name||'이름 미연결')} (${safe(s.id)})</li>`).join('')}</ol>실선은 정류장 순서 연결 개략선입니다. 검증된 도로 geometry가 없어 실제 운행 도로 경로를 뜻하지 않습니다.`:'노선을 선택하면 정류장 순서와 지도상 위치를 강조합니다.';
}
function drawTable() {
  const table=sim.table;
  byId('sim-compare-note').textContent=`${table.reason} · ${table.source==='report_v4_5day_mean'?'보고서 5일 평균':'M05 일별 원본'} · C팀 근사값이 아닌 B팀 M1 충족/잔여 수요. 선택은 개별 날짜의 M05 성공·M07 통과 원본이 있을 때만 가능합니다.`;
  byId('sim-table').innerHTML=table.rows.map(row=>{
    const m=row.metrics;const v=row.verification;const eligible=Boolean(row.outcome?.status==='passed'&&row.plan_id&&row.revision!=null&&m?.source==='M05_metrics_day'&&row.execution.sim_rc===0&&v.source==='verifier_report.json'&&v.pass===true&&!(v.failed_checks||[]).length&&!(v.not_run_checks||[]).length);
    return `<tr><td><b>${safe(row.plan_name||row.plan)}</b>${row.baseline?' <span class="sim-muted">(비교 기준)</span>':''}<div class="sim-muted">${safe(row.method)}</div></td><td>${num(m?.served)}</td><td>${pct(m?.rate_percent)}</td><td>${num(m?.unserved)}</td><td>${row.routes.length?num(row.routes.reduce((n,r)=>n+r.vehicles,0)):'배분 미연결'} / ${num(m?.route_count)}</td><td>${v.source==='verifier_report.json'?(v.pass?(v.fully_verified?'통과':'통과·부분 검증'):'실패'):'개별 미확인'}</td><td><button data-select="${row.plan}" ${eligible?'':'disabled'}>선택</button></td></tr>`;
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
  byId('sim-chart').innerHTML=`<h3>운행안 비교 · 동일 조건</h3>${rows.map(r=>bar(r.plan_name||r.plan,r.metrics?.rate_percent)).join('')}`+
    `<h3>구별 · 차량 조건 다름</h3>${c.regions.map(r=>bar(`${sim.options.regions.find(x=>x.id===r.region).name} ${r.fleet}대`,r.rate_percent)).join('')}`+
    `<h3>차량 대수별 · 잔여 수요 분모 다름</h3>${c.fleets.map(r=>bar(`${r.fleet}대`,r.rate_percent)).join('')}`+
    `<h3>실험 / 테스트 · 날짜 다름</h3>${c.periods.map(r=>bar(r.period==='exp'?'10/26~30':'11/02~06',r.rate_percent)).join('')}`+
    '<p class="sim-muted">구·차량·기간 그래프는 각 5일 평균입니다(PR #41 보고서 또는 날짜별 M05 원본). 조건과 잔여 수요가 달라 단순 성능 순위로 해석할 수 없습니다.</p>';
  const reasons=sim.result.reasons;
  byId('sim-reasons').innerHTML=reasons?`<h2 style="margin-top:14px">미충족 사유</h2><p class="sim-muted">${sim.result.metrics?.source==='report_v4_5day_mean'?'PR #41 보고서 §6 · affected 실험 5일 평균':'M05 해당 날짜 원본'} · 막대는 잔여 수요 대비 비율</p>${Object.entries({no_connection:'노선 없음',service_end:'운행 종료',wait:'대기 초과',journey:'통행 초과'}).map(([k,label])=>bar(`${label} ${num(reasons[k])}명`,100*(reasons[k]||0)/(sim.result.metrics?.total||1))).join('')}<p class="sim-muted">만차 거절 ${num(reasons.refusal_events)}건은 최종 미충족 분류와 별개</p>`:'<div class="sim-empty">선택 조건의 M2 사유 원본 미연결</div>';
  const v=sim.result.verification;
  byId('sim-verification').textContent=v.source==='verifier_report.json'?`M05 실행 rc=${sim.result.execution.sim_rc??'미제공'} · M07 pass=${v.pass} · fully_verified=${v.fully_verified} · 실패 ${(v.failed_checks||[]).join(', ')||'없음'} · 미실행 ${(v.not_run_checks||[]).join(', ')||'없음'} · 미검증 ${(v.not_verified_checks||[]).join(', ')||'없음'} · 평가일 ${sim.result.selection.date} · 입력 ${sim.result.execution.input_bundle_id||'미제공'} · 망 ${sim.result.network_snapshot_id||'미제공'} · 검증 시각 ${v.generated_at||'미제공'}`:'보고서 전체 640회 통과 기록 · 선택 실행의 verifier_report.json 미연결 · V12/V15/V16 미검증';
}
async function refresh() {
  const seq=++sim.request;
  ++sim.transportRequest;
  byId('mapStatus').textContent='C/B 결과를 불러오는 중…';
  try {
    if(byId('sim-region').value==='all'){
      const summary=await read(`/api/d/simulation/seoul?${query()}`);
      if(seq!==sim.request||!sim.active)return;
      sim.summary=summary;sim.result=null;sim.residual=null;sim.transport=null;sim.beforeResult=null;sim.beforeResidual=null;sim.selected=null;sim.selectedRoute=null;
      sim.stopMarkers.forEach(marker=>marker.remove());sim.stopMarkers=[];
      for(const sourceId of ['sim-stops','sim-route-lines','sim-demand'])sim.map?.getSource(sourceId)?.setData({type:'FeatureCollection',features:[]});
      if(sim.demandFrame)cancelAnimationFrame(sim.demandFrame);sim.demandFrame=0;sim.displayedDemand=[];
      const aggregate=summary.aggregate;
      for(const [id,value] of Object.entries({total:aggregate.total,served:aggregate.served,unserved:aggregate.total-aggregate.served}))byId(`sim-${id}`).textContent=num(value);
      byId('sim-rate').textContent=pct(aggregate.rate_percent);byId('sim-vehicles').textContent='—';byId('sim-verify').textContent='—';
      byId('sim-result-status').textContent=`지도: 서울 전체 · 지표: 실행된 ${aggregate.region_count}개 구의 ${aggregate.scope}. 나머지 구의 수요·충족률은 미계산입니다.`;
      byId('sim-transport-notice').textContent='서울 전체 지도에는 25개 자치구 경계와 결과가 있는 구의 충족률만 표시합니다. 지하철 제외 조건의 M05 결과가 없어 수요 원과 충족률은 바뀌지 않습니다.';
      byId('mapStatus').textContent=`서울 전체 · 25개 구 경계 · 충족률 ${aggregate.region_count}개 구만 제공`;
      document.querySelector('.map-head-title').textContent='교통망 노선 실험실 · 서울 전체';
      byId('sim-source').textContent=`${aggregate.scope} · 실행 구만 합산 · 서울 25개 구 전체 실측 수요가 아닙니다.`;
      byId('sim-table').innerHTML='<tr><td colspan="7">자치구를 선택하면 해당 결과를 표시합니다.</td></tr>';
      for(const id of ['sim-routes','sim-route-detail','sim-verification','sim-chart','sim-reasons'])byId(id).textContent='자치구를 선택하면 해당 결과를 표시합니다.';
      byId('sim-compare-note').textContent='구별 결과를 비교하려면 자치구를 선택하세요.';
      const execution=byId('sim-execution');if(execution)execution.textContent='';
      const demandNote=byId('sim-demand-note');if(demandNote)demandNote.textContent='서울 전체 보기: 정류장별 수요 원은 자치구를 선택하면 표시됩니다.';
      drawSummary();drawRegionMarkers(summary.districts.filter(d=>d.region&&d.metrics).map(d=>({region:d.region,fleet:d.fleet,...d.metrics})));
      mapLayers(true);
      return;
    }
    const q=query();const [result,table,comparison,summary,selected,runs,residual]=await Promise.all([
      read(`/api/d/simulation/result?${q}`),read(`/api/d/simulation/table?${q}`),
      read(`/api/d/simulation/comparison?${q}`),read(`/api/d/simulation/seoul?${q}`),
      read('/api/d/simulation/selection'),read(`/api/d/simulation/runs?${q}`),
      read(`/api/d/simulation/residual?${q}`).catch(()=>null)]);
    if(seq!==sim.request||!sim.active)return;
    const old=sim.result,oldSelection=old?.selection,newSelection=result.selection;
    const comparableSpatial=old?.spatial_outcomes?.status==='available'&&result.spatial_outcomes?.status==='available'&&sim.renderedHour===byId('sim-hour').value&&oldSelection.region===newSelection.region&&oldSelection.variant===newSelection.variant&&oldSelection.period===newSelection.period&&oldSelection.date===newSelection.date&&old.network_snapshot_id===result.network_snapshot_id&&old.metrics?.total===result.metrics?.total&&(oldSelection.fleet!==newSelection.fleet||oldSelection.plan!==newSelection.plan);
    sim.beforeResult=comparableSpatial?old:null;
    sim.beforeResidual=null;
    sim.result=result;sim.residual=residual;sim.transport=null;sim.table=table;sim.comparison=comparison;sim.summary=summary;sim.selected=selected;sim.runs=runs;sim.selectedRoute=null;
    drawMetrics();drawRoutes();drawTable();drawSummary();drawCharts();drawExecution();drawRegionMarkers([]);drawStops();await loadTransportDemand();mapLayers(true);
  }catch(error){if(seq===sim.request){byId('mapStatus').textContent=`시뮬레이션 결과 조회 실패: ${error.message}`;simulationNotice(`조건 변경 실패: ${error.message}`);}}
}
function mountExplorerLayout() {
  const app=document.querySelector('.app');app.classList.add('lab-active');
  const controls=byId('simulationControls');
  const routeList=document.querySelector('.main-grid > .side'),details=document.querySelector('.main-grid > .right'),layers=byId('layerPanel');
  byId('labControlsSlot').append(controls);
  byId('labRoutesSlot').append(routeList);
  byId('labLayerSlot').append(layers);
  byId('labExtrasSlot').append(details);
  byId('labPanelToggle').onclick=()=>{const closed=app.classList.toggle('lab-panel-closed');byId('labPanelToggle').textContent=closed?'▶ 설정 열기':'◀ 설정 접기';byId('labPanelToggle').setAttribute('aria-expanded',String(!closed));setTimeout(()=>sim.map?.resize(),260);};
  sim.map?.resize();
}
async function changeRegion(){
  syncFleet();const region=byId('sim-region').value;
  if(region==='all')fitSeoul();
  else {const selected=sim.districts?.districts.find(d=>d.id===region);if(selected?.bbox)sim.map?.fitBounds([[selected.bbox[0],selected.bbox[1]],[selected.bbox[2],selected.bbox[3]]],{padding:45,duration:180});}
  updateLabBoundary();mapLayers(true);
  if(getBaseMode()==='real')void setDistrict(region).catch(error=>simulationNotice(`A GTFS 교통망 갱신 실패: ${error.message}`));
  await preferAvailableRunDate();await refresh();
}
async function activate() {
  if(sim.busy)return;
  if(sim.active)return;
  sim.busy=true;
  sim.active=true;document.querySelector('.app').classList.add('sim-active');
  fitSeoul();
  byId('simModeButton').textContent='시뮬레이션 결과 숨기기';byId('simModeButton').setAttribute('aria-pressed','true');
  ensureMapSource();updateLabBoundary();await preferAvailableRunDate();await refresh();
  sim.busy=false;
}
async function deactivate() {
  if(!sim.active||sim.busy)return;
  sim.busy=true;
  sim.active=false;sim.request++;document.querySelector('.app').classList.remove('sim-active');
  ++sim.transportRequest;sim.transport=null;
  sim.beforeResidual=null;
  if(sim.demandFrame)cancelAnimationFrame(sim.demandFrame);sim.demandFrame=0;sim.displayedDemand=[];
  sim.map?.getSource('sim-demand')?.setData({type:'FeatureCollection',features:[]});
  sim.map?.getSource('sim-stops')?.setData({type:'FeatureCollection',features:[]});
  sim.map?.getSource('sim-route-lines')?.setData({type:'FeatureCollection',features:[]});
  sim.map?.getSource('sim-district-boundary')?.setData({type:'FeatureCollection',features:[]});
  sim.markers.forEach(m=>m.remove());sim.markers=[];mapLayers(false);refreshBaseVisibility?.();sim.map?.resize();
  sim.stopMarkers.forEach(m=>m.remove());sim.stopMarkers=[];
  byId('simModeButton').textContent='시뮬레이션 결과 표시';byId('simModeButton').setAttribute('aria-pressed','false');
  document.querySelector('.map-head-title').textContent=`교통망 노선 실험실 · ${getBaseMode()==='real'?'A GTFS':'합성 DEMO'}`;
  byId('mapStatus').textContent=`${getBaseMode()==='real'?'A GTFS 교통망':'합성 DEMO 교통망'} · 지도와 설정은 노선 실험실에서 계속 사용 가능`;
  sim.busy=false;
}
export async function initSimulationMode({map,maplibregl,getBaseMode:mode,setDistrict:chooseDistrict,isVisible:layerVisible,fitSeoul:fitSeoulMap,refreshBaseVisibility:refreshVisibility}) {
  sim.map=map;sim.maplibregl=maplibregl;getBaseMode=mode;setDistrict=chooseDistrict;isVisible=layerVisible;fitSeoul=fitSeoulMap;refreshBaseVisibility=refreshVisibility;
  mountExplorerLayout();
  document.addEventListener('d-layer-visibility',()=>mapLayers(sim.active));
  document.addEventListener('d-base-data-ready',()=>{if(!sim.active)return;if(sim.result)drawMetrics();else{byId('mapStatus').textContent=`서울 전체 교통망 · 시뮬레이션 실행 ${sim.summary?.aggregate?.region_count||0}개 구`;document.querySelector('.map-head-title').textContent='교통망 노선 실험실 · 서울 전체';}});
  document.addEventListener('d-transport-layer-change',changeTransportLayer);
  document.addEventListener('d-map-ready',()=>{if(sim.active){ensureMapSource();updateLabBoundary();}});
  if(map){map.on('idle',()=>{if(sim.active&&!map.getSource('sim-demand')){ensureMapSource();updateLabBoundary();}});}
  sim.options=await read('/api/d/simulation/options');
  [sim.districts,sim.districtGeojson]=await Promise.all([read('/api/d/districts').catch(()=>null),read('/data/districts_all.geojson').catch(()=>null)]);
  options('sim-region',[['all','서울 전체'],...sim.options.regions.map(r=>[r.id,r.name])]);
  options('sim-hour',[['all','전체 (06:00~10:00)'],...[6,7,8,9].map(h=>[String(h).padStart(2,'0'),`${String(h).padStart(2,'0')}:00~${String(h+1).padStart(2,'0')}:00`])]);
  syncFleet();syncDates();
  byId('simModeButton').onclick=()=>sim.active?deactivate():activate();
  byId('sim-all-regions').onclick=()=>{byId('sim-region').value='all';void changeRegion();};
  byId('sim-region').onchange=changeRegion;
  document.addEventListener('d-district-selected',async event=>{const region=event.detail?.district;if(!sim.active||(region!=='all'&&!sim.options?.regions.some(item=>item.id===region)))return;byId('sim-region').value=region;syncFleet();if(region==='all')fitSeoul();updateLabBoundary();mapLayers(true);await preferAvailableRunDate();await refresh();});
  byId('sim-period').onchange=async()=>{syncDates();await preferAvailableRunDate();refresh();};
  for(const key of ['fleet','plan'])byId(`sim-${key}`).onchange=async()=>{await preferAvailableRunDate();refresh();};
  for(const key of ['hour','date'])byId(`sim-${key}`).onchange=refresh;
  if(location.pathname.startsWith('/simulation'))await activate();
}
