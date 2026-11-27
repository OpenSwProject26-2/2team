/* 합성 DEMO·A GTFS·저장된 C/B 결과가 공유하는 D 노선 실험실 화면. */
import {mountParetoPanel, BASE_NAME as PP_BASE_NAME, daysLabel as ppDays} from '/lab-panel/pareto_panel.js';
import {showPlan as ppShowPlan, routeColor as ppRouteColor} from '/lab-panel/plan_layer.js';
const byId = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '미연결' : Number(value).toLocaleString('ko-KR',{maximumFractionDigits:1});
const pct = value => value == null ? '미연결' : `${Number(value).toFixed(2)}%`;
const sim = {active:false, busy:false, options:null, districts:null, districtGeojson:null, map:null, maplibregl:null, result:null, beforeResult:null, beforeResidual:null, transport:null, transportRequest:0, operating:new Set(['city','village','gyeonggi','metro']), renderedHour:null, residual:null, table:null, comparison:null, summary:null, runs:null, selectedRoute:null, hoveredPlanId:null, selectedPlanId:null, plotCandidates:[], markers:[], stopMarkers:[], request:0, hidden:new Map(), demandFrame:0, displayedDemand:[], absorb:null, absorbRegion:null, absorbShown:new Map(), absorbFrame:0, demandView:'absorb', busRoads:null, showBusRoads:true, showMuni:true, transit:null};
const MODE_COLOR={village:'#17a56c',gyeonggi:'#f39b28',city:'#ef3d5d'},MODE_NAME={village:'마을버스',gyeonggi:'경기버스',city:'시내버스'};
// 노선 실험실(lab)과 같은 지도 문법: 도로 위 노선(노선마다 색), 셔틀 정류장만 작은 점, 고른 노선만 정차 번호, 흡수 반영 수요 원
const ROUTE_COLORS=['#2563eb','#e8590c','#0f9d58','#9c36b5','#c2255c','#1098ad','#5c940d','#364fc7','#d9480f','#0b7285','#862e9c','#e67700'];
const routeColor=id=>{const i=(sim.result?.routes||[]).findIndex(r=>r.route_id===id);return ROUTE_COLORS[(i<0?0:i)%ROUTE_COLORS.length];};
const fcol=features=>({type:'FeatureCollection',features});
// 흡수 조건 키: 마을·경기버스가 운행하면 그 버스가 남는 정원만큼 태운 뒤 남는 수요(lab 정밀 흡수)
function absorbKey(){const m=sim.operating.has('village'),g=sim.operating.has('gyeonggi');return m&&g?'precise_affected_maeul_gg':m?'precise_affected_maeul':g?'precise_affected_gg':'affected';}
const fmtN=x=>x==null?'—':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:0});

let getBaseMode,setDistrict,isVisible,fitSeoul,refreshBaseVisibility;
const query = () => {
  const values=Object.fromEntries(['region','fleet','hour','variant','plan','period','date'].map(k=>[k,byId(`sim-${k}`).value]));
  if(values.region==='all')values.region=sim.options.regions[0].id;
  if(values.fleet==='all'||!values.fleet)values.fleet=sim.options.regions[0].fleets[0];
  if(!values.plan)values.plan=sim.options.regions[0].plans[0]?.id||'ours';
  return new URLSearchParams(values);
};
async function read(path) {const response=await fetch(path);const data=await response.json();if(!response.ok)throw Error(data.message||response.statusText);return data;}
function options(id, values, keep) {const node=byId(id);node.replaceChildren(...values.map(([value,label])=>{const el=document.createElement('option');el.value=value;el.textContent=label;return el;}));if(keep&&[...node.options].some(x=>x.value===keep))node.value=keep;}
// 상단 집계는 마을·경기 접근 수요를 제외한 저장 조건으로 고정한다. 체크는 이 값을 바꾸지 않는다.
function syncFleet() {const found=sim.options.regions.find(r=>r.id===byId('sim-region').value);const region=found&&sim.options.artifacts_connected?.[found.id]?found:null;const first=region||sim.options.regions[0];options('sim-fleet',region?region.fleets.map(n=>[String(n),`${n}대`]):[],byId('sim-fleet').value);byId('sim-fleet').disabled=!region;const variants=first?.variants||sim.options.variants;byId('sim-variant').value=variants.includes('affected_maeul_gg')?'affected_maeul_gg':variants.includes('affected')?'affected':variants[0]||'affected';options('sim-plan',(region?.plans||[]).map(p=>[p.id,p.name||p.id]),byId('sim-plan').value);byId('sim-plan').disabled=!region;}
function syncDates() {options('sim-date',[['mean','월~금 평균']],'mean');const label=byId('sim-date').closest('label');if(label)label.hidden=true;}   // 하루를 고르지 않고 평일 5일 평균만 본다
function simulationNotice(message){byId('sim-transport-notice').textContent=message;}
const disabledModes=()=>['city','village','gyeonggi','metro'].filter(kind=>!sim.operating.has(kind));
const storedReachVariant=()=>`affected${sim.operating.has('village')?'_maeul':''}${sim.operating.has('gyeonggi')?'_gg':''}`;
function changeTransportLayer(event){
  if(event?.detail?.kind==='all')sim.operating=new Set(['city','village','gyeonggi','metro']);
  else if(event?.detail?.kind){event.detail.enabled?sim.operating.add(event.detail.kind):sim.operating.delete(event.detail.kind);}
  if(!sim.active)return;
  if(sim.absorb&&byId('sim-region').value!=='all'){const before=new Map(sim.absorbShown);drawAbsorb();renderSimLab();mapLayers(true);flashAbsorb(event?.detail,before);return;}
  if(byId('sim-region').value==='all'){
    const modes=disabledModes();
    sim.transport=modes.length?{status:'reference',selection:{disabled_modes:modes},message:'서울 전체의 수단 제외 M05 결과가 없습니다.'}:null;
    simulationNotice(modes.length?'서울 전체의 수단 제외 공간 결과가 없어 기본 저장 지표를 유지합니다. 조건별 미충족은 미계산입니다.':'서울 전체 · 저장된 충족률');
    mapLayers(true);
    return;
  }
  void loadTransportDemand().then(()=>mapLayers(true));
}
async function loadTransportDemand(){
  const token=++sim.transportRequest;
  if(sim.absorb&&byId('sim-region').value!=='all'){sim.transport=null;drawDemand();drawAbsorb();renderSimLab();simulationNotice(`운행 조건(마을·경기버스)을 바꾸면 그 버스가 채우는 만큼 수요 원이 작아집니다 · 회색 테두리 = 흡수 없을 때 크기`);return;}
  if(!sim.active||byId('sim-region').value==='all')return;
  const modes=disabledModes();
  if(!sim.result){
    sim.transport=modes.length?{status:'reference',selection:{disabled_modes:modes},message:'선택 지역의 저장된 시뮬레이션 결과가 없습니다.'}:null;
    simulationNotice(modes.length?'저장된 시뮬레이션 결과 미연결 · 관측 승차 원과 운행 중단 영향 원을 표시합니다.':'저장된 시뮬레이션 결과가 없습니다.');
    mapLayers(true);
    return;
  }
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
        simulationNotice(`C/B ${variant} 셔틀 투입 전 잔여 수요를 표시합니다. k5 관측 승차와 합산하지 않습니다. 시내버스 제외 조건은 이 저장 결과에 반영되지 않았습니다.`);
        return;
      }
    }
    sim.transport={status:'reference',selection:{disabled_modes:modes},message:data.message||'조건별 정류장 수요가 없습니다.'};drawDemand();
    simulationNotice(`${modes.join('·')} 제외 M05 결과 미연결 · 지도에는 기본 저장 결과를 유지합니다. 이번 조건의 수요 변화는 미계산입니다.`);
  }catch(error){
    if(token!==sim.transportRequest||!sim.active)return;
    sim.transport={status:'reference',selection:{disabled_modes:modes},message:error.message};drawDemand();
    simulationNotice(`교통수단 제외 결과 조회 실패 · 기본 저장 결과를 유지합니다: ${error.message}`);
  }
}
async function preferAvailableRunDate() {
  if(byId('sim-region').value==='all'||!sim.options?.artifacts_connected?.[byId('sim-region').value]||byId('sim-date').value==='mean')return;   // 평균만 쓰므로 실제로는 항상 여기서 끝난다
  try {
    const runs=await read(`/api/d/simulation/runs?${query()}`);
    const dates=new Set((runs.runs||[]).filter(r=>r.status==='passed').map(r=>r.date));
    const field=byId('sim-date');
    if(!dates.has(field.value))field.value=[...dates][0]||'mean';
  }catch {byId('sim-date').value='mean';}
}
function mapLayers(active) {
  const map=sim.map;if(!map?.getSource('d-routes'))return;
  const hasResults=Object.values(sim.options?.artifacts_connected||{}).some(Boolean);
  const seoulOverview=active&&hasResults&&byId('sim-region').value==='all';
  const disconnected=active&&(!hasResults||byId('sim-region').value!=='all'&&!sim.options?.artifacts_connected?.[byId('sim-region').value]);
  const baseOverview=!active&&getBaseMode()==='real'&&byId('districtSelect').value==='all';
  const exposure=disconnected&&!sim.absorb;
  const hideNetwork=seoulOverview||baseOverview||(getBaseMode()==='real'&&byId('sim-region').value==='all');
  const legend=document.querySelector('.map-legend');if(legend)legend.style.display=hideNetwork?'none':'';
  if(active)for(const id of ['overview-boundary','overview-percent-bg','overview-percent-label'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');
  if(active)for(const id of ['district-fill','district-boundary-glow','district-boundary'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');
  if(active&&map.getLayer('boundary_3'))map.setLayoutProperty('boundary_3','visibility',byId('sim-region').value==='all'?'visible':'none');
  if(hideNetwork){
    for(const kind of ['strike','city','gyeonggi','village','metro','shuttle','unknown_bus','unknown']){
      for(const suffix of ['','-unverified','-halo','-unverified-halo']){const id=`route-${kind}${suffix}`;if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');}
    }
    for(const id of [...['strike','city','gyeonggi','village','metro','shuttle','unknown_bus','unknown'].map(kind=>`schematic-routes-${kind}`),'schematic-strike-halo','schematic-strike','schematic-selected'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');
  }
  for(const id of ['selected-route','selected-route-stops','selected-stop-numbers','selected-stop-labels'])if(map.getLayer(id)&&active)map.setLayoutProperty(id,'visibility','none');
  for(const [id,key] of [['bus-stops','busStops'],['metro-stops','metroStops'],['metro-stop-labels','metroStops']])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',hideNetwork||(active&&byId('sim-region').value!=='all'&&id==='bus-stops')||(getBaseMode()==='real'&&byId('districtSelect').value==='all')?'none':isVisible(key)&&(!id.startsWith('metro-')||isVisible('metro'))?'visible':'none');
  if(map.getLayer('demand-circles'))map.setLayoutProperty('demand-circles','visibility',(active&&!exposure)||baseOverview?'none':isVisible('observedDemand')?'visible':'none');
  const demandLegend=byId('demandLegend');if(demandLegend)demandLegend.hidden=(active&&!exposure)||baseOverview||getBaseMode()!=='real'||!isVisible('observedDemand');
  if(map.getLayer('metro-demand-circles'))map.setLayoutProperty('metro-demand-circles','visibility',(active&&!exposure)||baseOverview?'none':isVisible('observedDemand')?'visible':'none');
  if(map.getLayer('impact-all'))map.setLayoutProperty('impact-all','visibility',(active&&!exposure)||baseOverview?'none':isVisible('observedDemand')?'visible':'none');
  if(map.getLayer('demand-unmet'))map.setLayoutProperty('demand-unmet','visibility',active||baseOverview?'none':isVisible('unmetDemand')?'visible':'none');
  if(map.getLayer('sim-stops'))map.setLayoutProperty('sim-stops','visibility','none');
  if(map.getLayer('sim-stop-labels'))map.setLayoutProperty('sim-stop-labels','visibility','none');
  if(map.getLayer('sim-route-lines'))map.setLayoutProperty('sim-route-lines','visibility',active&&hasResults&&!hideNetwork&&!sim.selectedPlanId&&isVisible('shuttle')&&!sim.result?.route_lines?.features?.length?'visible':'none');
  const ppOn=Boolean(sim.pp?.current);
  const road=active&&!hideNetwork&&!sim.selectedPlanId&&!ppOn&&isVisible('shuttle')&&sim.result?.route_lines?.features?.length;
  for(const id of ['sim-road-casing','sim-road','sim-road-closing'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',road?'visible':'none');
  if(map.getLayer('sim-muni'))map.setLayoutProperty('sim-muni','visibility',active&&!seoulOverview&&sim.showMuni&&sim.result?.baseline_lines?.features?.length&&sim.result.selection.plan!==sim.result.baseline_lines.plan?'visible':'none');
  if(map.getLayer('sim-busroads'))map.setLayoutProperty('sim-busroads','visibility',active&&!seoulOverview&&sim.showBusRoads&&sim.busRoads?'visible':'none');
  for(const id of ['sim-plan-stops','sim-plan-stop-labels'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',active&&!seoulOverview&&!ppOn&&sim.result&&isVisible('shuttle')&&isVisible('shuttleStops')?'visible':'none');
  for(const id of ['sim-sel-stops','sim-sel-stops-n','sim-sel-stops-name'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',active&&!seoulOverview&&sim.selectedRoute&&!sim.pp?.current?'visible':'none');
  const absorbOn=active&&!seoulOverview&&sim.absorb&&(sim.demandView==='absorb'||!sim.result)&&isVisible('unmetDemand');
  const region=active&&!seoulOverview&&byId('sim-region').value!=='all';
  for(const mode of ['city','gyeonggi','village'])if(map.getLayer('sim-tr-'+mode))map.setLayoutProperty('sim-tr-'+mode,'visibility',region&&sim.transit&&(mode==='city'?isVisible('strike'):sim.operating.has(mode))?'visible':'none');
  if(region&&sim.transit)for(const kind of ['city','gyeonggi','village','strike','unknown_bus'])for(const suffix of ['','-unverified','-halo','-unverified-halo']){const id=`route-${kind}${suffix}`;if(map.getLayer(id))map.setLayoutProperty(id,'visibility','none');}
  for(const id of ['sim-absorb-gg','sim-absorb-maeul','sim-absorb-now'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',absorbOn?'visible':'none');
  for(const id of ['sim-plan-preview','sim-plan-selected'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',active&&!hideNetwork?'visible':'none');
  if(map.getLayer('sim-demand'))map.setLayoutProperty('sim-demand','visibility',active&&!seoulOverview&&!exposure&&isVisible('unmetDemand')&&(!sim.absorb||sim.demandView==='m05')?'visible':'none');
  if(map.getLayer('sim-walk400-demand'))map.setLayoutProperty('sim-walk400-demand','visibility',active&&!seoulOverview&&isVisible('walk400Demand')?'visible':'none');
  for(const id of ['sim-district-fill','sim-district-glow','sim-district-boundary'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',active&&isVisible('districtBoundary')?'visible':'none');
  sim.stopMarkers.forEach(marker=>{const el=marker.getElement();el.style.display=active&&!seoulOverview&&!sim.pp?.current&&isVisible('shuttle')&&isVisible('shuttleStops')?'':'none';});
  const ppVis=active&&!seoulOverview&&sim.pp?.current?'visible':'none';
  for(const id of sim.pp?.drawn?.layers||[])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',ppVis);
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
    map.addLayer({id:'sim-route-lines',type:'line',source:'sim-route-lines',paint:{'line-color':['case',['get','selected'],'#0eaaad','#315ea8'],'line-width':['case',['get','selected'],3.5,1.8],'line-opacity':['case',['get','selected'],.95,.55]},layout:{'line-cap':'round','line-join':'round'}},'bus-stops');
    map.on('click','sim-route-lines',event=>{const routeId=event.features?.[0]?.properties?.route_id;if(routeId)selectRoute(routeId);});
    map.on('mouseenter','sim-route-lines',()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave','sim-route-lines',()=>{map.getCanvas().style.cursor='';});
  }
  if(!map.getSource('sim-busroads')){
    // lab과 같은 버스 통행 가능 도로(셔틀 노선이 다닐 수 있는 도로). 가장 아래에 옅게.
    map.addSource('sim-busroads',{type:'geojson',data:fcol([])});
    map.addLayer({id:'sim-busroads',type:'line',source:'sim-busroads',paint:{'line-color':'#4b5563','line-width':['interpolate',['linear'],['zoom'],11,.8,13,1.2,16,2],'line-opacity':.45},layout:{'line-cap':'round','line-join':'round'}},'sim-route-lines');
  }
  for(const [id,color,width,opacity] of [['sim-plan-preview','#31a5ba',5,.45],['sim-plan-selected','#1b6f9d',6,.9]]){
    if(map.getSource(id))continue;
    map.addSource(id,{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id,type:'line',source:id,paint:{'line-color':color,'line-width':width,'line-opacity':opacity},layout:{'line-cap':'round','line-join':'round'}},'bus-stops');
  }
  if(!map.getSource('sim-district-boundary')){
    map.addSource('sim-district-boundary',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-district-fill',type:'fill',source:'sim-district-boundary',paint:{'fill-color':'#466b97','fill-opacity':.035}},'bus-stops');
    map.addLayer({id:'sim-district-glow',type:'line',source:'sim-district-boundary',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#e7f2ff','line-width':7,'line-opacity':.9}},'bus-stops');
    map.addLayer({id:'sim-district-boundary',type:'line',source:'sim-district-boundary',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#111111','line-width':2.2,'line-opacity':.9}},'bus-stops');
  }
  if(!map.getSource('sim-demand')){
    map.addSource('sim-demand',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id:'sim-demand',type:'circle',source:'sim-demand',paint:{'circle-color':['case',['<',['coalesce',['get','delta_unserved'],0],0],'#0eaaad','#d43161'],'circle-radius':['interpolate',['linear'],['get','render_count'],0,0,5,5,20,9,50,15,100,21,250,30],'circle-opacity':.62,'circle-stroke-color':'#a21b4c','circle-stroke-width':['case',['!=',['coalesce',['get','delta_unserved'],0],0],3,1]}} ,'bus-stops');
    map.on('click','sim-demand',event=>{if(map.queryRenderedFeatures(event.point,{layers:['bus-stops','metro-stops','metro-stop-labels','selected-route-stops']}).length)return;const p=event.features?.[0]?.properties;if(!p)return;const comparison=p.other_unserved==null?'':` · ${p.comparison_label||'상대안'} ${num(p.other_unserved)}명 → 현재 ${num(p.passenger_count)}명 (${Number(p.delta_unserved)>0?'+':''}${num(p.delta_unserved)}명)`;const details=p.demand_kind==='M05_unserved'?` · 평가 수요 ${num(p.evaluated_total)}명 · 미충족률 ${num(p.unserved_rate)}% · M05 출발 정류장 집계`:' · C/B 입력의 셔틀 투입 전 잔여 수요 · 셔틀 운행 후 미충족 인원 아님';new sim.maplibregl.Popup().setLngLat(event.lngLat).setText(`${p.name} (${p.stop_id}) · ${p.demand_label} ${num(p.passenger_count)}명${comparison}${details}`).addTo(map);});
  }
  if(!map.getSource('sim-absorb')){
    // lab 수요 원: 채운 원 = 지금 조건의 잔여 수요, 회색 테두리 = 흡수 없을 때 크기(그 차이가 마을·경기버스가 채운 몫)
    map.addSource('sim-absorb',{type:'geojson',data:fcol([])});
    const r=(v,o=0)=>['interpolate',['linear'],['zoom'],11,['+',o,['interpolate',['linear'],['sqrt',v],0,0,10,11,30,33]],14,['+',o,['interpolate',['linear'],['sqrt',v],0,0,10,17,30,51]]];   // zoom 보간은 식의 맨 바깥이어야 한다
    // 바깥 크기 = 파업 영향 수요 전체(고정). 버스를 켜면 그 버스가 채운 몫이 바깥부터 초록·주황으로 바뀌고 빨간 원이 줄어든다.
    map.addLayer({id:'sim-absorb-gg',type:'circle',source:'sim-absorb',filter:['>',['get','all'],0.5],paint:{'circle-radius':r(['get','all']),'circle-color':MODE_COLOR.gyeonggi,'circle-opacity':.3,'circle-stroke-color':'#ffffff','circle-stroke-width':.6}},'bus-stops');
    map.addLayer({id:'sim-absorb-maeul',type:'circle',source:'sim-absorb',filter:['>',['get','all'],0.5],paint:{'circle-radius':r(['+',['get','now'],['get','pm']]),'circle-color':MODE_COLOR.village,'circle-opacity':.3}},'bus-stops');
    map.addLayer({id:'sim-absorb-now',type:'circle',source:'sim-absorb',filter:['>',['get','all'],0.5],paint:{'circle-radius':r(['get','now']),'circle-color':'#dc2626','circle-opacity':.4,'circle-stroke-color':'#b91c1c','circle-stroke-width':.6}},'bus-stops');
    map.on('mousemove','sim-absorb-gg',e=>{if(map.queryRenderedFeatures(e.point,{layers:['sim-road','sim-plan-stops','sim-sel-stops'].filter(id=>map.getLayer(id))}).length)return;const p=e.features[0].properties;simPopup().setLngLat(e.lngLat).setHTML(`${safe(p.name)} · 파업 영향 ${num(p.all)}명/일<br><span style="color:#dc2626">■</span> 셔틀이 맡을 잔여 <b>${num(p.now)}</b>명${p.pm>0.5?`<br><span style="color:${MODE_COLOR.village}">■</span> 마을버스가 채움 ${num(p.pm)}명`:''}${p.pg>0.5?`<br><span style="color:${MODE_COLOR.gyeonggi}">■</span> 경기버스가 채움 ${num(p.pg)}명`:''}`).addTo(map);});
    map.on('mouseleave','sim-absorb-gg',()=>simPopup().remove());
    // 켜고 끌 때 원이 바뀐 정류장을 그 버스 색으로 잠깐 깜빡인다
  }
  if(!map.getSource('sim-road')){
    // 노선 실험실과 같은 도로 좌표(버스 통행 도로). 노선마다 색, 고른 노선만 진하게.
    map.addSource('sim-road',{type:'geojson',data:fcol([])});
    map.addLayer({id:'sim-road-casing',type:'line',source:'sim-road',filter:['==',['get','part'],'service'],paint:{'line-color':'#ffffff','line-width':4,'line-opacity':.8},layout:{'line-cap':'round','line-join':'round'}},'bus-stops');
    map.addLayer({id:'sim-road',type:'line',source:'sim-road',filter:['==',['get','part'],'service'],paint:{'line-color':['get','color'],'line-width':2.4,'line-opacity':.9},layout:{'line-cap':'round','line-join':'round'}},'bus-stops');
    map.addLayer({id:'sim-road-closing',type:'line',source:'sim-road',filter:['==',['get','part'],'closing'],paint:{'line-color':['get','color'],'line-width':1.8,'line-opacity':.6,'line-dasharray':[1.4,1.2]},layout:{'line-join':'round'}},'bus-stops');
    for(const id of ['sim-road','sim-road-closing']){
      map.on('mousemove',id,e=>{const rid=e.features[0].properties.route_id,r=(sim.result?.routes||[]).find(x=>x.route_id===rid);if(!r)return;focusRoute(rid);const st=[...new Set(r.stops.map(x=>x.station).filter(Boolean))];simPopup().setLngLat(e.lngLat).setHTML(`<b style="color:${routeColor(rid)}">${safe(rid)}</b> · ${num(r.vehicles)}대 · 정류장 ${r.stop_ids.length}곳${st.length?`<br>🚇 ${safe(st.join('·'))}`:''}${e.features[0].properties.part==='closing'?'<br><span style="color:#667">점선 = 마지막 정류장에서 첫 정류장으로 돌아가는 구간(순환)</span>':''}`).addTo(map);});
      map.on('mouseleave',id,()=>{focusRoute(sim.selectedRoute);simPopup().remove();map.getCanvas().style.cursor='';});
      map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer';});
      map.on('click',id,e=>selectRoute(e.features[0].properties.route_id));
    }
  }
  if(!map.getSource('sim-muni')){
    // 지자체 셔틀안(기준안)을 같은 지도에 점선으로 겹쳐 본다(lab muni 레이어)
    map.addSource('sim-muni',{type:'geojson',data:fcol([])});
    map.addLayer({id:'sim-muni',type:'line',source:'sim-muni',paint:{'line-color':'#0891b2','line-width':1.8,'line-opacity':.8,'line-dasharray':[2,1.2]},layout:{'line-join':'round'}},'sim-road-casing');
    map.on('mousemove','sim-muni',e=>{if(map.queryRenderedFeatures(e.point,{layers:['sim-road']}).length)return;const p=e.features[0].properties;simPopup().setLngLat(e.lngLat).setHTML(`<b style="color:#0891b2">지자체 셔틀 ${safe(p.route_id)}</b><br><span style="color:#667">${safe(sim.result?.baseline_lines?.plan_name||'지자체안')} · 점선</span>`).addTo(map);});
    map.on('mouseleave','sim-muni',()=>simPopup().remove());
  }
  if(!map.getSource('sim-tr')){
    // 이 구를 지나는 마을·경기버스(흡수 노선, 운행 체크)와 파업 버스 동선(lab strike_routes, '파업' 체크).
    map.addSource('sim-tr',{type:'geojson',data:fcol([])});
    for(const [mode,w,o] of [['city',1.2,.28],['gyeonggi',1.4,.6],['village',1.4,.6]]){
      map.addLayer({id:'sim-tr-'+mode,type:'line',source:'sim-tr',filter:['==',['get','mode'],mode],paint:{'line-color':MODE_COLOR[mode],'line-width':['interpolate',['linear'],['zoom'],11,w*.7,15,w*1.6],'line-opacity':o},layout:{'line-cap':'round','line-join':'round','visibility':'none'}},'sim-muni');
      map.on('mousemove','sim-tr-'+mode,e=>{if(map.queryRenderedFeatures(e.point,{layers:['sim-road','sim-absorb-gg'].filter(id=>map.getLayer(id))}).length)return;const p=e.features[0].properties;simPopup().setLngLat(e.lngLat).setHTML(`<b style="color:${MODE_COLOR[mode]}">${mode==='city'?'파업 버스':MODE_NAME[mode]} ${safe(p.name||p.id)}</b><br><span style="color:#667">${mode==='city'?'파업 전 이 버스가 다니던 동선(지금은 운행 안 함)':'남는 정원만큼 파업 승객을 태우는 노선(lab 흡수 가정)'} · 정류장 ${num(p.stops)}곳</span>`).addTo(map);});
      map.on('mouseleave','sim-tr-'+mode,()=>simPopup().remove());
    }
  }
  if(!map.getSource('sim-plan-stops')){
    map.addSource('sim-plan-stops',{type:'geojson',data:fcol([])});
    map.addLayer({id:'sim-plan-stops',type:'circle',source:'sim-plan-stops',paint:{'circle-radius':['interpolate',['linear'],['zoom'],11,3,15,6],'circle-color':'#ffffff','circle-stroke-color':['get','color'],'circle-stroke-width':2.2}});
    map.addLayer({id:'sim-plan-stop-labels',type:'symbol',source:'sim-plan-stops',minzoom:14.5,layout:{'text-field':['get','name'],'text-size':11,'text-offset':[0,1.1],'text-anchor':'top','text-font':['Noto Sans Regular'],'text-optional':true},paint:{'text-color':'#1e293b','text-halo-color':'#fff','text-halo-width':1.5}});
    map.on('mousemove','sim-plan-stops',e=>{const p=e.features[0].properties;simPopup().setLngLat(e.lngLat).setHTML(`🚏 ${safe(p.name)}${p.station?` · 🚇 ${safe(p.station)}`:''}<br><span style="color:#667">${safe(p.stop_id)} · 노선 ${safe(p.routes)}</span>`).addTo(map);});
    map.on('mouseleave','sim-plan-stops',()=>simPopup().remove());
    map.on('click','sim-plan-stops',e=>{const first=String(e.features[0].properties.routes).split(', ')[0];if(first)selectRoute(first);});
    map.addSource('sim-sel-stops',{type:'geojson',data:fcol([])});
    map.addLayer({id:'sim-sel-stops',type:'circle',source:'sim-sel-stops',paint:{'circle-radius':10,'circle-color':'#f59e0b','circle-stroke-color':'#fff','circle-stroke-width':2}});
    map.addLayer({id:'sim-sel-stops-n',type:'symbol',source:'sim-sel-stops',layout:{'text-field':['to-string',['get','seq']],'text-size':11,'text-font':['Noto Sans Bold'],'text-allow-overlap':true},paint:{'text-color':'#1e293b'}});
    map.addLayer({id:'sim-sel-stops-name',type:'symbol',source:'sim-sel-stops',layout:{'text-field':['get','name'],'text-size':11.5,'text-offset':[0,1.5],'text-anchor':'top','text-font':['Noto Sans Regular'],'text-optional':true},paint:{'text-color':'#1e293b','text-halo-color':'#fff','text-halo-width':1.6}});
  }
  if(!map.getLayer('sim-walk400-demand'))map.addLayer({id:'sim-walk400-demand',type:'circle',source:'sim-demand',filter:['>', ['to-number',['get','walk_distance_m'],0],400],paint:{'circle-color':'#d43161','circle-radius':['interpolate',['linear'],['get','render_count'],0,0,5,7,20,12,50,19,100,26],'circle-opacity':.72,'circle-stroke-color':'#8f1742','circle-stroke-width':2}},'bus-stops');
  byId('map').dataset.simSource='ready';mapLayers(sim.active);drawStops();drawDemand();drawAbsorb();renderSimLab();
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
    note.textContent=`${transport.selection.disabled_modes.join('·')} 제외 조건의 저장된 공간 결과가 없습니다. 지도 원은 기본 저장 결과를 유지하며 이번 운행 변경 효과는 반영하지 않았습니다. ${transport.message}`;
    return;
  }
  if(activeTransport?.kind==='pre_shuttle_residual'&&getBaseMode()==='real'&&disabledModes().length){
    note.textContent='지도에는 같은 날짜·시간의 통합 k5 승차 영향 원을 표시합니다. C/B 셔틀 투입 전 잔여 수요는 범위와 모집단이 달라 합산하지 않았습니다. 경기버스 승차 자료는 A 피드에 없습니다.';
    return;
  }
  const compared=features.filter(f=>f.properties.delta_unserved!=null);
  const changes=compared.filter(f=>f.properties.delta_unserved!==0);
  const biggest=changes.reduce((best,f)=>!best||Math.abs(f.properties.delta_unserved)>Math.abs(best.properties.delta_unserved)?f:best,null);
  const biggestNote=biggest?` · 최대 변화 ${biggest.properties.name} ${biggest.properties.delta_unserved>0?'+':''}${num(biggest.properties.delta_unserved)}명`:'';
  const comparisonNote=compared.length?` · ${sim.beforeResult||sim.beforeResidual?'이전 조건':'상대안'} 대비 변화 정류장 ${changes.length}곳${biggestNote} · 청록=수요 감소, 굵은 테두리=변화`:' · 비교할 상대 공간 결과 미연결';
  note.textContent=activeTransport?`${activeTransport.selection.disabled_modes.join('·')} 제외 · ${label} 원 ${features.length}곳 · ${activeTransport.source.input_bundle_id} · ${activeTransport.source.run_id||'셔틀 투입 전'}${activeTransport.stored_variant?' · C/B '+activeTransport.stored_variant+' 접근 조건 근사':''}${activeTransport.partial?' · 시내버스·지하철 제외 효과 미반영':''} · 상단 지표는 기본 조건 · 5명 미만 시간칸은 숨김`: (features.length?`${label} 원 ${features.length}곳 · ${label==='M05 미충족'?'선택한 운행안·차량·날짜의 B 원본 출발 정류장 집계'+comparisonNote:'C/B 입력 번들 demand_bins.csv · '+(hour==='all'?'06:00~10:00 합계':'선택 시간대')+' · 셔틀 투입 전 잔여 수요'+(sim.beforeResidual?comparisonNote:'')+' · 차량/운행안별 수송 효과는 아님'} · 원 크기는 인원에 비례 · 5명 미만 시간칸은 숨겨 원의 합계가 전체 수요보다 작을 수 있음${sim.residual?.unmatched_rows?' · 좌표 미매칭 '+sim.residual.unmatched_rows+'행':''}`:spatial?.status==='available'?'선택한 조건에서 표시 가능한 정류장별 미충족 인원이 0명입니다.':sim.residual?.status==='available'?'선택한 시간에 5명 이상인 잔여 수요 출발지가 없습니다.':'C/B 입력 번들 잔여 수요와 B 정류장별 미충족 결과가 없습니다.');
}
function simPopup(){return sim.popup||(sim.popup=new sim.maplibregl.Popup({closeButton:false,closeOnClick:false,maxWidth:'320px'}));}
function focusRoute(id){
  const m=sim.map;if(!m?.getLayer('sim-road'))return;
  m.setPaintProperty('sim-road','line-opacity',id?['case',['==',['get','route_id'],id],1,.16]:.92);
  m.setPaintProperty('sim-road','line-width',id?['case',['==',['get','route_id'],id],4,1.8]:2.4);
  m.setPaintProperty('sim-road-closing','line-opacity',id?['case',['==',['get','route_id'],id],.85,.1]:.6);
  m.setPaintProperty('sim-plan-stops','circle-opacity',id?['case',['in',`, ${id},`,['concat',', ',['get','routes'],',']],1,.35]:1);
  m.setPaintProperty('sim-plan-stops','circle-stroke-opacity',id?['case',['in',`, ${id},`,['concat',', ',['get','routes'],',']],1,.35]:1);
  document.querySelectorAll('#simRouteTable tr[data-id]').forEach(tr=>tr.classList.toggle('on',tr.dataset.id===id));
}
function drawRegionLayers(){
  sim.map?.getSource('sim-busroads')?.setData(fcol(sim.busRoads?.features||[]));
  sim.map?.getSource('sim-tr')?.setData(fcol(sim.transit?.features||[]));
}
function drawStops() {
  drawRegionLayers();
  if(!sim.map?.getSource('sim-stops')||!sim.result)return;
  sim.stopMarkers.forEach(marker=>marker.remove());sim.stopMarkers=[];   // 정류장마다 큰 핀을 띄우던 방식은 쓰지 않는다
  const features=sim.result.geojson.features.map(f=>({...f,properties:{...f.properties,selected:f.properties.route_id===sim.selectedRoute}}));
  sim.map.getSource('sim-stops').setData({type:'FeatureCollection',features});
  // 도로 좌표가 없는 결과(다른 팀 자료)만 정류장 순서 개략선을 쓴다
  const grouped=new Map();
  for(const feature of features){const id=feature.properties.route_id;if(!grouped.has(id))grouped.set(id,[]);grouped.get(id).push(feature);}
  const lines=[];
  for(const route of sim.result.routes){const ordered=(grouped.get(route.route_id)||[]).sort((a,b)=>Number(a.properties.order)-Number(b.properties.order));if(ordered.length<2)continue;lines.push({type:'Feature',properties:{route_id:route.route_id,vehicles:route.vehicles,selected:route.route_id===sim.selectedRoute,geometry_status:'stop_points_only_unverified'},geometry:{type:'LineString',coordinates:ordered.map(f=>f.geometry.coordinates)}});}
  sim.map.getSource('sim-route-lines')?.setData({type:'FeatureCollection',features:lines});
  const road=sim.result.route_lines?.features||[];
  sim.map.getSource('sim-road')?.setData(fcol(road.map(f=>({...f,properties:{...f.properties,color:routeColor(f.properties.route_id)}}))));
  sim.map.getSource('sim-muni')?.setData(fcol(sim.result.baseline_lines?.features||[]));
  const by=new Map();
  for(const r of sim.result.routes)for(const st of r.stops){if(st.lon==null)continue;if(!by.has(st.id))by.set(st.id,{st,routes:[]});const e=by.get(st.id);if(!e.routes.includes(r.route_id))e.routes.push(r.route_id);}
  sim.map.getSource('sim-plan-stops')?.setData(fcol([...by.values()].map(({st,routes})=>({type:'Feature',properties:{stop_id:st.id,name:st.name||st.id,station:st.station||'',routes:routes.join(', '),color:routeColor(routes[0])},geometry:{type:'Point',coordinates:[st.lon,st.lat]}}))));
  const sel=sim.result.routes.find(r=>r.route_id===sim.selectedRoute);
  sim.map.getSource('sim-sel-stops')?.setData(fcol(sel?sel.stops.map((st,k)=>st.lon==null?null:{type:'Feature',properties:{seq:k+1,name:st.name||st.id},geometry:{type:'Point',coordinates:[st.lon,st.lat]}}).filter(Boolean):[]));
  focusRoute(sim.selectedRoute);
}
function absorbParts(){
  // 정류장마다 [셔틀이 맡을 잔여, 마을버스가 채움, 경기버스가 채움] — 합은 파업 영향 수요(affected)
  const m=sim.operating.has('village'),g=sim.operating.has('gyeonggi'),out=new Map();
  for(const f of sim.absorb?.features||[]){
    const p=f.properties,A=p.affected||0,pM=p.precise_affected_maeul??A,pG=p.precise_affected_gg??A,pMG=p.precise_affected_maeul_gg??A;
    const now=m&&g?pMG:m?pM:g?pG:A,pm=m?Math.max(0,A-pM):0,pg=Math.max(0,A-now-pm);
    out.set(p.stop_id,[now,pm,pg]);
  }
  return out;
}
function flashAbsorb(detail,before){
  const kind=detail?.kind;if(!sim.absorb||!['village','gyeonggi','city'].includes(kind))return;
  const after=absorbParts();let moved=0,stops=0;
  for(const [sid,[n]] of after){const d=(before.get(sid)?.[0]??n)-n;if(Math.abs(d)>=0.5){moved+=d;stops++;}}
  const name=MODE_NAME[kind],on=detail.enabled;
  const text=kind==='city'?`시내버스 ${on?'켬':'끔'} · 이 시나리오는 시내버스 전면 파업이라 운행하는 시내버스가 없어 원이 바뀌지 않습니다. 파업 버스 동선은 '파업' 체크로 봅니다`:
    stops?`${name} ${on?'켬':'끔'} → 하루 ${fmtN(Math.abs(moved))}명을 ${name}가 ${on?'태움':'더 이상 못 태움'} · 원 ${stops}곳이 ${on?'줄어듦':'커짐'}`:`${name} ${on?'켬':'끔'} · 이 구에서 ${name}가 채우는 수요가 없습니다`;
  let el=byId('simFlash');if(!el){el=document.createElement('div');el.id='simFlash';el.className='sim-flash';byId('map').append(el);}
  el.textContent=text;el.style.borderColor=MODE_COLOR[kind];el.classList.remove('show');void el.offsetWidth;el.classList.add('show');
  clearTimeout(sim.flashTimer);sim.flashTimer=setTimeout(()=>el.classList.remove('show'),4200);
  simulationNotice(text);
}

// ── 충족률·형평성 곡선 패널(파이프라인 pareto.json) ── 지도 왼쪽 아래. 점을 누르면 그 안의 노선을 지도에 그린다.
// 패널(web/lab/pareto_panel.js)과 지도 그리기(web/lab/plan_layer.js)는 onSelect로만 잇는다.
sim.pp={region:null,run:'',panel:null,cache:new Map(),current:null,drawn:null,fitted:false,seq:0};
// 운영자 실행(e2e.js)과 잇는 이벤트: window.ppRun = 고른 실행 작업 ID(없으면 기본 곡선), 'pp:reload'로 다시 읽기,
// 곡선 자료가 바뀌면 'pp:data', 안을 고르거나 내리면 'pp:select'(detail.plan = null이면 내림)
const ppEmit=(name,detail)=>window.dispatchEvent(new CustomEvent(name,{detail}));
window.addEventListener('pp:reload',()=>{sim.pp.region=null;void syncPareto();});
async function syncPareto(){
  const region=sim.active?byId('sim-region').value:'all',run=window.ppRun||'';
  if(sim.pp.region===region&&sim.pp.run===run&&(sim.pp.panel||region==='all'))return;
  const seq=++sim.pp.seq;
  sim.pp.panel?.destroy();sim.pp.panel=null;sim.pp.drawn?.remove();sim.pp.drawn=null;sim.pp.current=null;sim.pp.fitted=false;sim.pp.cache=new Map();sim.pp.region=region;sim.pp.run=run;
  if(region==='all'||!sim.map){renderSimLab();ppEmit('pp:data',{data:null,region,run});ppEmit('pp:select',{plan:null,region,run});return;}
  const q=`region=${encodeURIComponent(region)}${run?`&run=${encodeURIComponent(run)}`:''}`;
  const data=await read(`/api/d/simulation/pareto?${q}`).catch(()=>null);
  if(seq!==sim.pp.seq||!sim.active)return;
  if(data?.status!=='available'){sim.pp.region=region;sim.pp.data=null;renderSimLab();ppEmit('pp:data',{data:null,region,run});ppEmit('pp:select',{plan:null,region,run});return;}
  sim.pp.data=data;
  sim.pp.panel=mountParetoPanel(sim.map,{paretoData:data,autoSelect:false,storageKey:'d.paretoPanel.open',
    exportUrl:p=>`/api/d/simulation/pareto-export?${q}&id=${encodeURIComponent(p.id)}`,
    onSelect:plan=>showParetoPlan(plan,region)});
  placePareto();
  ppEmit('pp:data',{data,region,run});ppEmit('pp:select',{plan:null,data,region,run});
}
// 왼쪽 조작 패널이 지도 위에 떠 있으므로, 펼쳐져 있으면 곡선 패널을 그 오른쪽으로 민다
function ppLeft(){
  const sb=byId('labSidebar'),closed=document.querySelector('.app')?.classList.contains('lab-panel-closed');
  if(!sb||closed||!sb.offsetParent)return 12;
  const r=sb.getBoundingClientRect(),m=byId('map').getBoundingClientRect();
  return r.right>m.left&&r.left<m.left+40?Math.round(r.right-m.left+12):12;
}
function placePareto(){const el=sim.map?.getContainer().querySelector('.pp-root');if(el)el.style.setProperty('margin-left',`${ppLeft()}px`,'important');}
window.addEventListener('resize',()=>placePareto());
async function showParetoPlan(plan,region){
  const my=++sim.pp.seq;
  let ex=sim.pp.cache.get(plan.id);
  if(!ex){
    try{ex=await read(plan.export_url);if(ex?.status!=='available')throw new Error('운행안 없음');sim.pp.cache.set(plan.id,ex);}
    catch(e){if(my===sim.pp.seq)sim.pp.panel?.error(`운행안을 읽지 못했습니다(${e.message}) · 지도는 이전 그대로`);return;}
  }
  if(my!==sim.pp.seq||region!==sim.pp.region)return;
  sim.pp.current={plan,ex};
  ppEmit('pp:select',{plan,ex,data:sim.pp.data,region,run:sim.pp.run});
  sim.pp.drawn=ppShowPlan(sim.map,ex,{prefix:'pp',width:3.4,fit:!sim.pp.fitted,padding:{top:50,bottom:50,left:ppLeft()+40,right:50}});   // 화면 범위는 처음 한 번만 맞춘다
  sim.pp.fitted=true;sim.selectedRoute=null;sim.popup?.remove();
  mapLayers(true);renderSimLab();
}
function clearParetoPlan(){
  if(!sim.pp.current&&!sim.pp.drawn)return;
  sim.pp.seq++;sim.pp.drawn?.remove();sim.pp.drawn=null;sim.pp.current=null;sim.pp.panel?.clear();
  ppEmit('pp:select',{plan:null,data:sim.pp.data,region:sim.pp.region,run:sim.pp.run});
  mapLayers(true);renderSimLab();
}
function renderParetoInfo(){
  const box=byId('simParetoBlock');if(!box)return;
  const cur=sim.pp?.current;box.hidden=!cur;if(!cur)return;
  const p=cur.plan,ex=cur.ex,P=v=>Number.isFinite(v)?`${(v*100).toFixed(1)}%`:'—';
  const rows=(ex.routes||[]).map((r,i)=>{const st=[...new Set((r.stops||[]).filter(s=>s.kind==='station').map(s=>s.name))];return `<tr><td><i style="background:${ppRouteColor(r,i)}"></i>${safe(r.route_id)}</td><td>${safe(st.length?'🚇 '+st.join('·'):(r.stops?.[0]?.name||'')+' ↻')}</td><td>${safe(r.vehicles)}</td><td>${r.headway_min==null?'—':Number(r.headway_min).toFixed(1)+'분'}</td></tr>`;}).join('');
  byId('simParetoInfo').innerHTML=`<p class="sim-muted"><b style="color:#172238">${safe(p.is_muni?'지자체 셔틀안(비교용)':(p.algo_name||p.id))}</b>${p.is_muni?'':`${PP_BASE_NAME[p.base]&&PP_BASE_NAME[p.base]!==p.algo_name?` · ${safe(PP_BASE_NAME[p.base])}`:''} · 형평 기준 ${safe((p.floors||[]).join(', '))}`}${p.rep?` · <b>${safe(p.rep)}</b>`:''}</p>`+
    `<p class="sim-muted">충족률 <b>${P(p.served_rate)}</b> · 형평성(역 800m 밖) <b>${P(p.equity_far)}</b> · 소외율 ${P(p.isolation)} · 이동시간 ${Number.isFinite(p.time_ratio_rode)?p.time_ratio_rode.toFixed(2)+'배':'—'} · M07 ${safe(p.m07||'—')}</p>`+
    `<table class="sim-lab-routes"><thead><tr><th>노선</th><th>지나는 역</th><th>대</th><th>배차</th></tr></thead><tbody>${rows}</tbody></table>`+
    `<p class="sim-muted">곡선 지표: 파이프라인 평가 ${safe(ppDays(sim.pp.data?.eval_days))} · 충족률은 도보 반영 탑승률이라 아래 M05 탑승 수 기준 충족률과 조금 다릅니다. 지도 원(수요)은 원래 고른 안 기준 그대로입니다.</p>`+
    `<div class="sim-pp-actions"><button type="button" id="simPpFit">노선에 맞추기</button><button type="button" id="simPpClear">지도에서 내리기(원래 셔틀안 보기)</button></div>`;
  byId('simPpFit').onclick=()=>{const b=sim.pp.drawn?.bounds;if(b)sim.map.fitBounds(b,{padding:{top:50,bottom:50,left:ppLeft()+40,right:50},duration:300});};
  byId('simPpClear').onclick=clearParetoPlan;
}
async function loadRegionLayers(region){
  if(!region||region==='all'||sim.absorbRegion===region)return;
  const get=path=>read(`/api/d/simulation/${path}?region=${encodeURIComponent(region)}`).then(d=>d.status==='available'?d:null).catch(()=>null);
  const [absorb,roads,transit]=await Promise.all([get('absorb-demand'),get('bus-roads'),get('transit-routes')]);
  if(byId('sim-region').value!==region)return;
  sim.absorb=absorb;sim.busRoads=roads;sim.transit=transit;sim.absorbRegion=region;sim.absorbShown=new Map();
}
function drawAbsorb(){
  const src=sim.map?.getSource('sim-absorb');if(!src)return;
  if(sim.absorbFrame)cancelAnimationFrame(sim.absorbFrame);sim.absorbFrame=0;
  if(!sim.absorb){src.setData(fcol([]));sim.absorbShown=new Map();return;}
  if(!sim.absorbShown.size)for(const [sid,v] of absorbParts())sim.absorbShown.set(sid,[v[0]+v[1]+v[2],0,0]);   // 처음엔 흡수 없는 크기에서 줄어들며 시작
  const from=new Map(sim.absorbShown),target=absorbParts(),t0=performance.now();
  const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  const frame=now=>{
    const k=reduced?1:Math.min(1,(now-t0)/900),e=1-(1-k)**3,out=[];
    for(const f of sim.absorb.features){const p=f.properties,t=target.get(p.stop_id),a=from.get(p.stop_id)||t,v=t.map((x,i)=>a[i]+(x-a[i])*e);sim.absorbShown.set(p.stop_id,v);out.push({type:'Feature',geometry:f.geometry,properties:{stop_id:p.stop_id,name:p.name,all:p.affected||0,now:v[0],pm:v[1],pg:v[2]}});}
    src.setData(fcol(out));sim.absorbFrame=k<1?requestAnimationFrame(frame):0;
  };
  sim.absorbFrame=requestAnimationFrame(frame);
}
function meanLabel(){
  const d=sim.result?.mean_days||[],wd=['일','월','화','수','목','금','토'];
  if(!d.length)return '평일 평균';
  const names=d.map(x=>wd[new Date(x+'T12:00:00').getDay()]);
  return d.length===5&&names.join('')==='월화수목금'?'월~금 평균':`${names.join('·')} ${d.length}일 평균(나머지 날 실행 없음)`;
}
function coverageHtml(){
  const T=sim.absorb?.totals;if(!T)return '<p class="sim-muted">흡수 조건별 정류장 수요가 연결되지 않아 마을·경기버스 몫을 나눌 수 없습니다.</p>';
  const A=T.affected,m=sim.operating.has('village'),g=sim.operating.has('gyeonggi');
  const maeul=m?A-T.precise_affected_maeul:0,gg=g?(m?T.precise_affected_maeul-T.precise_affected_maeul_gg:A-T.precise_affected_gg):0,left=T[absorbKey()]??A-maeul-gg;
  const pc=(x,d)=>(100*x/d).toFixed(1);
  const segs=[['마을버스가 채움',maeul,MODE_COLOR.village],['경기버스가 채움',gg,MODE_COLOR.gyeonggi],['셔틀이 맡을 잔여',left,'#dc2626']];
  const M=sim.result?.metrics,allOn=m&&g;
  const shuttle=M?.total?`<div class="cov-top" style="margin-top:9px"><b>대체 셔틀 ${pct(M.rate_percent)}</b><span>M05 ${sim.result?.selection.date==='mean'?meanLabel():sim.result?.selection.date}</span></div>`+
    `<div class="cov-bar"><span style="width:${M.rate_percent}%;background:#2563eb"></span></div>`+
    `<div class="cov-rows"><span><i style="background:#2563eb"></i>셔틀이 태움</span><span>${fmtN(M.served)}명</span><span>${pc(M.served,M.total)}%</span><span><i style="background:#e2e8f0"></i>남은 미충족</span><span>${fmtN(M.total-M.served)}명</span><span>${pc(M.total-M.served,M.total)}%</span></div>`+
    `<p class="sim-muted">셔틀 대상 ${fmtN(M.total)}명은 이 실험의 M05 평가 수요입니다(마을·경기 모두 운행 조건${allOn?'':' — 지금 체크와 다름'}). 위 버스 흡수 몫은 lab 정밀 흡수 수요(${fmtN(T.precise_affected_maeul_gg)}명, 월~금 평균)로 따로 계산해 두 값을 한 막대에 합치지 않았습니다.</p>`:'';
  return `<div class="cov-top"><b>버스가 채우는 몫</b><span>파업 노선 승객 하루 ${fmtN(A)}명 · 월~금 평균</span></div>`+
    `<div class="cov-bar">${segs.filter(([,v])=>v>0).map(([n,v,c])=>`<span style="width:${100*v/A}%;background:${c}" title="${n} ${fmtN(v)}명 (${pc(v,A)}%)"></span>`).join('')}</div>`+
    `<div class="cov-rows">${segs.map(([n,v,c])=>`<span><i style="background:${c}"></i>${n}</span><span>${fmtN(v)}명</span><span>${pc(v,A)}%</span>`).join('')}</div>`+
    (sim.operating.has('city')?'':'<p class="sim-muted">시내버스: 이 시나리오는 전면 파업이라 운행하는 시내버스가 없어 채워 주는 몫이 없습니다.</p>')+
    '<p class="sim-muted">지도 원: 크기 = 파업 영향 수요(고정) · <b style="color:#dc2626">빨강</b> = 셔틀이 맡을 잔여 · <b style="color:#17a56c">초록</b>/<b style="color:#f39b28">주황</b> = 마을/경기버스가 채운 몫. 마을·경기 몫은 그 버스로 갈 수 있는 승객을 남는 정원만큼 태운다는 lab 정밀 흡수 가정(마을 35석·부하 0.6, 경기 60석·혼잡 0.6).</p>'+(sim.transit?`<p class="sim-muted">지도 노선: 마을버스 ${sim.transit.counts.village}개 · 경기버스 ${sim.transit.counts.gyeonggi}개 · 파업 버스 동선 ${sim.transit.counts.city}개(빨강, '파업' 체크) · 체크한 것만 표시</p>`:'')+(sim.result?shuttle:'<p class="sim-muted">이 구는 셔틀 운행안 평가 결과가 아직 없어 버스 흡수까지만 보여 줍니다.</p>');
}
function renderSimLab(){
  const el=byId('simLabPanel');if(!el)return;
  el.hidden=!sim.active||!(sim.result||sim.absorb)||byId('sim-region').value==='all';if(el.hidden)return;
  for(const id of ['simRoutesBlock'])byId(id).hidden=!sim.result;
  const routes=sim.result?.routes||[],sel=routes.find(r=>r.route_id===sim.selectedRoute);
  byId('simCoverage').innerHTML=coverageHtml();
  renderParetoInfo();
  const views=byId('simDemandView');views.hidden=!sim.absorb||!sim.result;
  views.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.v===sim.demandView)));
  const road=sim.result?.route_lines?.features?.length;
  const B=sim.result?.baseline_lines,isBase=B?.plan&&B.plan===sim.result?.selection.plan;
  byId('simOverlayNote').textContent=(B?.features?.length?(isBase?'지금 고른 안이 지자체안이라 점선을 따로 겹치지 않습니다.':`점선 = ${B.plan_name||'지자체안'} 노선(같은 도로망 경로)`):'지자체안 도로 경로 자료 없음')+' · '+(sim.busRoads?`회색 = 버스 통행 가능 도로 ${num(sim.busRoads.edges)}구간(노선 생성·운행에 쓰는 도로망)`:'버스 통행 가능 도로 자료 없음');
  byId('simRouteHint').textContent=road?'선 = 버스 통행 도로 위 경로 · 점선 = 첫 정류장으로 돌아가는 구간':'도로 경로 자료 없음 · 정류장 순서를 직선으로 이음(실제 도로 아님)';
  byId('simRouteTable').innerHTML=routes.map(r=>{const st=[...new Set(r.stops.map(x=>x.station).filter(Boolean))];const ends=`${r.stops[0]?.name||r.stop_ids[0]} ↻`;return `<tr data-id="${safe(r.route_id)}" class="${r.route_id===sim.selectedRoute?'sel':''}"><td><i style="background:${routeColor(r.route_id)}"></i>${safe(r.route_id)}</td><td>${safe(st.length?'🚇 '+st.join('·'):ends)}${r.road_gaps?` <span title="도로 경로를 찾지 못한 정류장 쌍">⚠${r.road_gaps}</span>`:''}</td><td class="n">${num(r.vehicles)}</td><td class="n">${r.stop_ids.length}</td></tr>`;}).join('');
  byId('simStopSeqTitle').textContent=sel?`${sel.route_id} 정류장 순서 · ${sel.stop_ids.length}곳 (순환)`:'정류장 순서';
  byId('simStopSeq').innerHTML=sel?sel.stops.map((st,k)=>`<li><button type="button" data-k="${k}"><span class="seq">${k+1}</span>${safe(st.name||st.id)}${st.station?` <span class="sim-muted">🚇 ${safe(st.station)}</span>`:''}</button></li>`).join('')+`<li class="sim-muted">↻ ${safe(sel.stops[0]?.name||'')}(으)로 돌아감</li>`:'<li class="sim-muted">노선을 지도나 표에서 클릭하세요</li>';
}
function mountSimLabPanel(){
  if(byId('simLabPanel'))return;
  const panel=document.createElement('section');panel.id='simLabPanel';panel.className='sim-lab-panel';panel.hidden=true;
  panel.innerHTML=`<div id="simParetoBlock" hidden><h3>충족률·형평성 곡선에서 고른 안</h3><div id="simParetoInfo"></div></div><h3>파업 수요 · 누가 맡나</h3><div id="simCoverage"></div><div class="seg" id="simDemandView" role="group" aria-label="지도 수요 원 기준"><button type="button" data-v="absorb">셔틀 전 잔여(버스 흡수 반영)</button><button type="button" data-v="m05">셔틀 후 미충족(M05·월~금 평균)</button></div>`+
    `<h3>지도에 함께 보기</h3><div class="sim-lab-toggles"><label><input type="checkbox" id="simShowMuni" checked> <i class="lg-muni"></i>지자체 셔틀안(점선)</label><label><input type="checkbox" id="simShowBusRoads" checked> <i class="lg-road"></i>버스 통행 가능 도로</label></div><p class="sim-muted" id="simOverlayNote"></p>`+
    `<div id="simRoutesBlock"><h3>대체 셔틀 노선</h3><p class="sim-muted" id="simRouteHint"></p><table class="sim-lab-routes"><thead><tr><th>노선</th><th>지나는 역</th><th>대</th><th>정류장</th></tr></thead><tbody id="simRouteTable"></tbody></table>`+
    `<h3 id="simStopSeqTitle">정류장 순서</h3><ol id="simStopSeq" class="sim-stopseq"></ol></div>`;
  const anchor=document.querySelector('#labSidebar .float-map-options');(anchor||byId('labSidebar')).after(panel);
  panel.querySelector('#simDemandView').onclick=e=>{const b=e.target.closest('button');if(!b)return;sim.demandView=b.dataset.v;renderSimLab();mapLayers(true);if(sim.demandView==='m05'&&sim.result?.spatial_outcomes?.status!=='available')simulationNotice('이 안의 정류장별 셔틀 후 미충족(월~금 평균) 결과가 아직 연결되지 않았습니다.');};
  panel.querySelector('#simShowMuni').onchange=e=>{sim.showMuni=e.target.checked;mapLayers(true);};
  panel.querySelector('#simShowBusRoads').onchange=e=>{sim.showBusRoads=e.target.checked;mapLayers(true);};
  const table=panel.querySelector('#simRouteTable');
  table.onclick=e=>{const tr=e.target.closest('tr[data-id]');if(tr)selectRoute(tr.dataset.id);};
  table.onmouseover=e=>{const tr=e.target.closest('tr[data-id]');if(tr)focusRoute(tr.dataset.id);};
  table.onmouseleave=()=>focusRoute(sim.selectedRoute);
  panel.querySelector('#simStopSeq').onclick=e=>{const b=e.target.closest('button[data-k]');const r=sim.result?.routes.find(x=>x.route_id===sim.selectedRoute);const st=b&&r?.stops[Number(b.dataset.k)];if(st?.lon!=null)sim.map?.flyTo({center:[st.lon,st.lat],zoom:16,duration:600});};
}
function updateLabBoundary() {
  if(!sim.districtGeojson||!sim.districts||!sim.map?.getSource('sim-district-boundary'))return;
  const selectedId=byId('sim-region').value;
  const district=sim.districts.districts.find(d=>d.id===selectedId);
  const feature=sim.districtGeojson.features.find(f=>String(f.properties?.sgg)===String(district?.code));
  sim.map.getSource('sim-district-boundary').setData({type:'FeatureCollection',features:selectedId==='all'?sim.districtGeojson.features:feature?[feature]:[]});
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
  document.querySelector('.map-head p').textContent=result.route_lines?.features?.length?'선=버스 통행 도로 위 셔틀 경로(노선마다 색) · 점선=첫 정류장으로 돌아가는 구간 · 원=셔틀이 맡을 잔여 수요(회색 테두리=마을·경기버스가 채운 몫)':`${getBaseMode()==='real'?'A GTFS 정상 교통망을 배경으로 표시 · ':''}실선=정류장 순서 연결 개략선(도로 경로 아님) · 수요 원은 실제 결과 출처를 표시`;
  document.querySelector('.map-source').textContent='C/B 입력 번들 잔여 수요 또는 M05 정류장별 미충족 · 출처 별도 표시';
}
function selectRoute(id) {
  sim.selectedRoute=sim.selectedRoute===id?null:id;drawRoutes();drawStops();renderSimLab();mapLayers(true);
  const r=sim.result?.routes.find(x=>x.route_id===sim.selectedRoute);if(!r||!sim.map)return;
  const pts=(sim.result.route_lines?.features||[]).filter(f=>f.properties.route_id===r.route_id).flatMap(f=>f.geometry.coordinates);
  const all=pts.length?pts:r.stops.filter(x=>x.lon!=null).map(x=>[x.lon,x.lat]);if(!all.length)return;
  const xs=all.map(c=>c[0]),ys=all.map(c=>c[1]);sim.map.fitBounds([[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]],{padding:80,maxZoom:15.5,duration:600});
}
function drawRoutes() {
  const routes=sim.result.routes;
  const other=sim.table?.rows.find(row=>row.plan!==sim.result.selection.plan);
  const otherStops=new Set((other?.routes||[]).map(r=>r.stop_ids.join('|')));
  byId('sim-routes').innerHTML=routes.length?routes.map(r=>`<button class="sim-route ${r.route_id===sim.selectedRoute?'active':''}" data-id="${safe(r.route_id)}">${safe(r.route_id)} · 후보 ${safe(r.candidate_id||'미연결')} · ${num(r.vehicles)}대 · ${r.stop_ids.length} 정차점 · ${!other?.routes.length?'상대안 미연결':otherStops.has(r.stop_ids.join('|'))?'상대안에도 같은 정차 순서':'선택 안 고유 정차 순서'}</button>`).join(''):'<div class="sim-empty">C팀 원본 노선·차량 배분 미연결. 보고서의 총 노선 수로 개별 노선을 만들지 않습니다.</div>';
  byId('sim-routes').querySelectorAll('button').forEach(b=>b.onclick=()=>selectRoute(b.dataset.id));
  const route=routes.find(r=>r.route_id===sim.selectedRoute);
  byId('sim-route-detail').innerHTML=route?`<b>${safe(route.route_id)}</b> · ${num(route.vehicles)}대 · ${safe(route.origin||'출처 미제공')}<ol>${route.stops.map(s=>`<li>${safe(s.name||'이름 미연결')} (${safe(s.id)})</li>`).join('')}</ol>${route.geometry_status?.startsWith('bus_road_network')?'지도 선은 routegen 도로망(버스 통행 도로) 위 경로입니다'+(route.road_gaps?` · 도로 경로를 못 찾은 정류장 쌍 ${route.road_gaps}곳은 비워 둠`:'')+'.':'실선은 정류장 순서 연결 개략선입니다. 검증된 도로 geometry가 없어 실제 운행 도로 경로를 뜻하지 않습니다.'}`:'노선을 선택하면 정류장 순서와 지도상 위치를 강조합니다.';
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
function comparisonCandidates(){
  const rows=sim.table?.rows||[];
  return rows.filter(row=>row.metrics?.source==='M05_metrics_day'&&row.metrics.rate_percent!=null&&Number.isFinite(Number(row.metrics.rate_percent))&&row.equity?.definition&&typeof row.equity.score==='number'&&Number.isFinite(row.equity.score)&&row.plan_id&&row.revision!=null&&Array.isArray(row.preview_geojson?.features)&&row.preview_geojson.features.length&&row.preview_geojson.features.every(f=>['LineString','MultiLineString'].includes(f.geometry?.type)&&f.properties?.geometry_status));
}
function planFeatures(id){
  const row=sim.plotCandidates.find(item=>item.plan_id===id);
  return {type:'FeatureCollection',features:row?.preview_geojson.features||[]};
}
function drawPlanOverlays(){
  sim.map?.getSource('sim-plan-preview')?.setData(planFeatures(sim.hoveredPlanId));
  sim.map?.getSource('sim-plan-selected')?.setData(planFeatures(sim.selectedPlanId));
  mapLayers(sim.active);
}
function renderComparisonPlot(){
  const plot=byId('comparisonPlot');
  const all=comparisonCandidates();
  const definition=all[0]?.equity.definition;
  sim.plotCandidates=all.filter(row=>row.equity.definition===definition);
  if(!sim.plotCandidates.some(row=>row.plan_id===sim.selectedPlanId))sim.selectedPlanId=null;
  if(!sim.plotCandidates.some(row=>row.plan_id===sim.hoveredPlanId))sim.hoveredPlanId=null;
  drawPlanOverlays();
  if(!sim.plotCandidates.length){plot.classList.remove('has-points');plot.textContent='이 조건의 충족률·형평성 점수와 셔틀 노선 좌표가 함께 검증된 후보가 없습니다. C팀 운행안과 B팀 평가 결과 연결이 필요합니다.';return;}
  plot.classList.add('has-points');
  const scores=sim.plotCandidates.map(row=>Number(row.equity.score));
  const low=Math.min(...scores),high=Math.max(...scores),span=high-low||1;
  const axis=`<svg viewBox="0 0 340 205" role="img" aria-label="가로축 충족률, 세로축 ${safe(definition)}"><path d="M44 12V162H322" fill="none" stroke="#aab6c9" stroke-width="1"/><text x="160" y="198" font-size="11" fill="#5b6981">충족률 (%)</text><text x="10" y="95" font-size="10" fill="#5b6981" transform="rotate(-90 10 95)">${safe(definition)}</text><text x="43" y="178" font-size="9" fill="#8a97a9">0</text><text x="300" y="178" font-size="9" fill="#8a97a9">100</text>${sim.plotCandidates.map(row=>{const x=44+278*Number(row.metrics.rate_percent)/100,y=155-136*(Number(row.equity.score)-low)/span;const selected=row.plan_id===sim.selectedPlanId;return `<circle data-plan-id="${safe(row.plan_id)}" cx="${x}" cy="${y}" r="${selected?8:6}" fill="${selected?'#493aa7':'#5d57c6'}" stroke="#fff" stroke-width="2" tabindex="0"><title>${safe(row.plan_name||row.plan_id)} · 충족률 ${pct(row.metrics.rate_percent)} · ${safe(definition)} ${num(row.equity.score)}</title></circle>`;}).join('')}</svg>`;
  plot.innerHTML=axis;
  plot.querySelectorAll('[data-plan-id]').forEach(point=>{
    const id=point.dataset.planId;
    point.addEventListener('mouseenter',()=>{sim.hoveredPlanId=id;drawPlanOverlays();});
    point.addEventListener('mouseleave',()=>{sim.hoveredPlanId=null;drawPlanOverlays();});
    point.addEventListener('click',()=>{sim.selectedPlanId=sim.selectedPlanId===id?null:id;drawPlanOverlays();renderComparisonPlot();});
    point.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();point.dispatchEvent(new MouseEvent('click'));}});
  });
}
async function refresh() {
  const seq=++sim.request;
  ++sim.transportRequest;
  byId('mapStatus').textContent='C/B 결과를 불러오는 중…';
  try {
    if(!Object.values(sim.options?.artifacts_connected||{}).some(Boolean)||byId('sim-region').value!=='all'&&!sim.options?.artifacts_connected?.[byId('sim-region').value]){
      sim.result=null;sim.table=null;sim.transport=null;sim.selectedRoute=null;sim.selectedPlanId=null;sim.hoveredPlanId=null;
      for(const id of ['sim-stops','sim-route-lines','sim-demand'])sim.map?.getSource(id)?.setData({type:'FeatureCollection',features:[]});
      await loadRegionLayers(byId('sim-region').value);
      if(seq!==sim.request||!sim.active)return;
      drawStops();drawAbsorb();renderComparisonPlot();renderSimLab();mapLayers(true);
      byId('mapStatus').textContent=sim.absorb?'선택 자치구 · 파업 수요(버스 흡수 반영)·마을/경기/파업 노선 · 셔틀 운행안 평가 결과 없음':'선택 자치구 지도·관측 수요 제공 · C/B 운행안 평가 자료 미연결';
      simulationNotice(sim.absorb?'이 구는 셔틀 운행안 평가 결과가 아직 없습니다. 마을·경기버스를 켜고 끄면 그 버스가 채우는 만큼 빨간 원이 줄어듭니다.':'이 자치구의 C팀 운행안 및 B팀 평가 결과가 연결되지 않았습니다. 지도와 관측 수요만 표시합니다.');
      return;
    }
    if(byId('sim-region').value==='all'){
      const summary=await read(`/api/d/simulation/seoul?${query()}`);
      if(seq!==sim.request||!sim.active)return;
      sim.summary=summary;sim.result=null;sim.residual=null;sim.table=null;sim.transport=disabledModes().length?{status:'reference',selection:{disabled_modes:disabledModes()},message:'서울 전체의 수단 제외 M05 결과가 없습니다.'}:null;sim.beforeResult=null;sim.beforeResidual=null;sim.selected=null;sim.selectedRoute=null;renderComparisonPlot();renderSimLab();
      sim.stopMarkers.forEach(marker=>marker.remove());sim.stopMarkers=[];
      for(const sourceId of ['sim-stops','sim-route-lines','sim-demand'])sim.map?.getSource(sourceId)?.setData({type:'FeatureCollection',features:[]});
      if(sim.demandFrame)cancelAnimationFrame(sim.demandFrame);sim.demandFrame=0;sim.displayedDemand=[];
      const aggregate=summary.aggregate;
      for(const [id,value] of Object.entries({total:aggregate.total,served:aggregate.served,unserved:aggregate.total-aggregate.served}))byId(`sim-${id}`).textContent=num(value);
      byId('sim-rate').textContent=pct(aggregate.rate_percent);byId('sim-vehicles').textContent='—';byId('sim-verify').textContent='—';
      byId('sim-result-status').textContent=`지도: 서울 전체 · 지표: 실행된 ${aggregate.region_count}개 구의 ${aggregate.scope}. 나머지 구의 수요·충족률은 미계산입니다.`;
      byId('sim-transport-notice').textContent=disabledModes().length?'서울 전체 · 관측 승차 원과 운행 중단 영향 원을 표시합니다. 저장된 M05 충족률은 기본 조건이며 실제 미충족은 미계산입니다.':'서울 전체 · 25개 자치구 경계와 결과가 있는 구의 충족률을 표시합니다.';
      byId('mapStatus').textContent=`서울 전체 · 25개 구 경계 · 충족률 ${aggregate.region_count}개 구만 제공`;
      document.querySelector('.map-head-title').textContent='교통망 노선 실험실 · 서울 전체';
      byId('sim-source').textContent=`${aggregate.scope} · 실행 구만 합산 · 서울 25개 구 전체 실측 수요가 아닙니다.`;
      byId('sim-table').innerHTML='<tr><td colspan="7">자치구를 선택하면 해당 결과를 표시합니다.</td></tr>';
      for(const id of ['sim-routes','sim-route-detail','sim-verification','sim-chart','sim-reasons'])byId(id).textContent='자치구를 선택하면 해당 결과를 표시합니다.';
      byId('sim-compare-note').textContent='구별 결과를 비교하려면 자치구를 선택하세요.';
      const execution=byId('sim-execution');if(execution)execution.textContent='';
      const demandNote=byId('sim-demand-note');if(demandNote)demandNote.textContent=disabledModes().length?'서울 전체 보기: 관측 승차 원과 운행 중단 영향 원을 표시합니다. 실제 미충족은 미계산입니다.':'서울 전체 보기: 정류장별 수요 원은 자치구를 선택하면 표시됩니다.';
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
    const region=newSelection.region;
    await loadRegionLayers(region);
    if(seq!==sim.request||!sim.active)return;
    sim.result=result;sim.residual=residual;sim.transport=null;sim.table=table;sim.comparison=comparison;sim.summary=summary;sim.selected=selected;sim.runs=runs;sim.selectedRoute=null;
    drawMetrics();drawRoutes();drawTable();drawSummary();drawCharts();drawExecution();drawRegionMarkers([]);drawStops();drawAbsorb();renderSimLab();renderComparisonPlot();await loadTransportDemand();mapLayers(true);
  }catch(error){if(seq===sim.request){byId('mapStatus').textContent=`시뮬레이션 결과 조회 실패: ${error.message}`;simulationNotice(`조건 변경 실패: ${error.message}`);}}
}
function mountExplorerLayout() {
  const app=document.querySelector('.app');app.classList.add('lab-active');
  const controls=byId('simulationControls');
  const routeList=document.querySelector('.main-grid > .side'),details=document.querySelector('.main-grid > .right'),layers=byId('layerPanel');
  byId('operationSlot').append(controls);
  mountSimLabPanel();
  byId('labRoutesSlot').append(routeList);
  byId('labLayerSlot').append(layers);
  byId('labExtrasSlot').append(details);
  byId('labPanelToggle').addEventListener('click',()=>setTimeout(placePareto,320));byId('labPanelToggle').onclick=()=>{const closed=app.classList.toggle('lab-panel-closed');byId('labSidebar').inert=closed;byId('labPanelToggle').querySelector('.panel-toggle-label').textContent=closed?'설정 열기':'설정 접기';byId('labPanelToggle').setAttribute('aria-expanded',String(!closed));setTimeout(()=>sim.map?.resize(),280);};
  sim.map?.resize();
}
function clearSimulationRegion(){
  if(sim.demandFrame)cancelAnimationFrame(sim.demandFrame);
  sim.demandFrame=0;sim.displayedDemand=[];
  const empty={type:'FeatureCollection',features:[]};
  for(const id of ['sim-demand','sim-stops','sim-route-lines','sim-plan-preview','sim-plan-selected','sim-road','sim-plan-stops','sim-sel-stops','sim-absorb','sim-muni','sim-busroads','sim-tr'])sim.map?.getSource(id)?.setData(empty);sim.busRoads=null;sim.transit=null;
  if(sim.absorbFrame)cancelAnimationFrame(sim.absorbFrame);sim.absorbFrame=0;sim.absorb=null;sim.absorbRegion=null;sim.absorbShown=new Map();sim.selectedRoute=null;
  sim.stopMarkers.forEach(marker=>marker.remove());sim.stopMarkers=[];
  sim.markers.forEach(marker=>marker.remove());sim.markers=[];
}
async function changeRegion(){
  syncFleet();const region=byId('sim-region').value;
  clearSimulationRegion();void syncPareto();
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
  sim.operating=new Set(['city','village','gyeonggi','metro']);
  sim.active=true;document.querySelector('.app').classList.add('sim-active');
  fitSeoul();
  byId('simModeButton').textContent='지도·관측으로 돌아가기';byId('simModeButton').setAttribute('aria-pressed','true');
  ensureMapSource();updateLabBoundary();await preferAvailableRunDate();await refresh();void syncPareto();
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
  for(const id of ['sim-road','sim-plan-stops','sim-sel-stops','sim-absorb','sim-muni','sim-busroads','sim-tr'])sim.map?.getSource(id)?.setData({type:'FeatureCollection',features:[]});sim.busRoads=null;sim.transit=null;
  sim.absorb=null;sim.absorbRegion=null;sim.absorbShown=new Map();sim.popup?.remove();void syncPareto();renderSimLab();
  sim.map?.getSource('sim-district-boundary')?.setData({type:'FeatureCollection',features:[]});
  sim.markers.forEach(m=>m.remove());sim.markers=[];mapLayers(false);refreshBaseVisibility?.();sim.map?.resize();
  sim.stopMarkers.forEach(m=>m.remove());sim.stopMarkers=[];
  byId('simModeButton').textContent='시뮬레이션 결과 화면 보기';byId('simModeButton').setAttribute('aria-pressed','false');
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
  options('sim-region',[['all','서울 전체'],...(sim.districts?.districts||sim.options.regions).map(r=>[r.id,r.name])]);
  options('sim-hour',[['all','전체 (06:00~10:00)'],...[6,7,8,9].map(h=>[String(h).padStart(2,'0'),`${String(h).padStart(2,'0')}:00~${String(h+1).padStart(2,'0')}:00`])]);
  syncFleet();syncDates();
  const compare=byId('comparisonFloat'),toggle=byId('compareToggle');
  const showCompare=show=>{compare.hidden=!show;toggle.setAttribute('aria-expanded',String(show));if(show)renderComparisonPlot();};
  toggle.onclick=()=>showCompare(compare.hidden);
  byId('compareClose').onclick=()=>showCompare(false);
  const compareHead=compare.querySelector('.comparison-head'),compareWrap=byId('mapWrap');
  const placeCompare=(left,top)=>{const maxX=Math.max(0,compareWrap.clientWidth-compare.offsetWidth),maxY=Math.max(0,compareWrap.clientHeight-compare.offsetHeight);compare.style.left=`${Math.min(maxX,Math.max(0,left))}px`;compare.style.top=`${Math.min(maxY,Math.max(0,top))}px`;compare.style.right='auto';compare.style.bottom='auto';};
  let drag=null;
  compareHead.addEventListener('pointerdown',event=>{if(event.target.closest('button')||compare.hidden)return;const wrap=compareWrap.getBoundingClientRect(),rect=compare.getBoundingClientRect();drag={x:event.clientX,y:event.clientY,left:rect.left-wrap.left,top:rect.top-wrap.top};compareHead.setPointerCapture(event.pointerId);compareHead.classList.add('dragging');event.preventDefault();});
  compareHead.addEventListener('pointermove',event=>{if(drag)placeCompare(drag.left+event.clientX-drag.x,drag.top+event.clientY-drag.y);});
  const endDrag=()=>{drag=null;compareHead.classList.remove('dragging');};
  compareHead.addEventListener('pointerup',endDrag);compareHead.addEventListener('pointercancel',endDrag);
  compareHead.addEventListener('keydown',event=>{const delta={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];if(!delta||event.target!==compareHead)return;const wrap=compareWrap.getBoundingClientRect(),rect=compare.getBoundingClientRect();placeCompare(rect.left-wrap.left+delta[0],rect.top-wrap.top+delta[1]);event.preventDefault();});
  byId('compareExpand').onclick=()=>{compare.classList.toggle('expanded');if(compare.style.left)placeCompare(parseFloat(compare.style.left),parseFloat(compare.style.top));};
  window.addEventListener('resize',()=>{if(compare.style.left)placeCompare(parseFloat(compare.style.left),parseFloat(compare.style.top));});
  renderComparisonPlot();
  byId('simModeButton').onclick=()=>sim.active?deactivate():activate();
  byId('sim-all-regions').onclick=()=>{byId('sim-region').value='all';void changeRegion();};
  byId('sim-region').onchange=changeRegion;
  document.addEventListener('d-district-selected',async event=>{const region=event.detail?.district;if(!sim.active)return;byId('sim-region').value=region;clearSimulationRegion();syncFleet();if(region==='all')fitSeoul();updateLabBoundary();mapLayers(true);await preferAvailableRunDate();await refresh();});
  byId('sim-period').onchange=async()=>{syncDates();await preferAvailableRunDate();refresh();};
  for(const key of ['fleet','plan'])byId(`sim-${key}`).onchange=async()=>{clearParetoPlan();await preferAvailableRunDate();refresh();};
  for(const key of ['hour','date'])byId(`sim-${key}`).onchange=refresh;
  if(location.pathname.startsWith('/simulation'))await activate();
}
