import * as maplibregl from "/vendor/maplibre-gl.mjs";
const $ = id => document.getElementById(id);
const state = {origin:null,destination:null,active:"origin",routes:[],selected:0,sortMode:"optimal",markers:[],routeMarkers:[],routeEndpoints:null};
const colors = {walk:"#a6b2b6",bus:"#2461ea",shuttle:"#182740",subway:"#8645dc"};
const busColors = {city:"#2461ea",gyeonggi:"#ff9d1e",village:"#0aa86c",unclassified:"#718198"};
const legColor=leg=>leg.kind==="bus"?(busColors[leg.bus_category]||busColors.unclassified):(colors[leg.kind]||"#546d68");
const map = new maplibregl.Map({container:"map",style:"/citizen-map-style.json",center:[126.97,37.54],zoom:11});
map.addControl(new maplibregl.NavigationControl(),"top-right");
map.on("error",()=>{$("map-error").hidden=false});
let mapPickPopup=null;
map.on("click",showMapPick);
$("departure").value=new Date(Date.now()+9*3600000).toISOString().slice(0,16);
function text(tag,content,cls){const node=document.createElement(tag);node.textContent=content;if(cls)node.className=cls;return node}
const visiblePlaceSource="citizen-osm-poi";
const emptyVisiblePlaces={type:"FeatureCollection",features:[]};
let visiblePlaceRequest=null;
function mapPickLayers(){
  const layers=map.getStyle()?.layers||[];
  return [...layers.filter(layer=>layer.type==="symbol" &&
    (layer.source===visiblePlaceSource||layer["source-layer"]==="poi")).map(layer=>layer.id),
    "building","building-3d"].filter(id=>map.getLayer(id));
}
function loadVisiblePlaces(){
  const source=map.getSource(visiblePlaceSource);
  if(!source)return;
  visiblePlaceRequest?.abort();
  const zoom=map.getZoom();
  if(zoom<11||zoom>=15.5){source.setData(emptyVisiblePlaces);return}
  const bounds=map.getBounds();
  const params=new URLSearchParams({
    west:bounds.getWest(),south:bounds.getSouth(),east:bounds.getEast(),north:bounds.getNorth(),zoom
  });
  const controller=new AbortController();
  visiblePlaceRequest=controller;
  fetch("/api/citizen-routing/map-places?"+params,{signal:controller.signal})
    .then(response=>response.ok?response.json():Promise.reject(new Error("map places")))
    .then(data=>{
      if(visiblePlaceRequest!==controller||!map.getSource(visiblePlaceSource))return;
      source.setData({type:"FeatureCollection",features:(data.places||[]).map(place=>({
        type:"Feature",id:place.id,
        properties:{name:place.name,kind:place.kind,category:place.category,
          icon:place.icon,priority:place.priority,address:place.address},
        geometry:{type:"Point",coordinates:[place.lon,place.lat]}
      }))});
    }).catch(error=>{
      if(error.name!=="AbortError"&&visiblePlaceRequest===controller)source.setData(emptyVisiblePlaces);
    });
}
map.on("load",()=>{
  for(const layer of map.getStyle().layers.filter(layer=>layer.id.startsWith("citizen-poi-")||
      layer.id.startsWith("citizen-osm-"))){
    map.on("mouseenter",layer.id,()=>{map.getCanvas().style.cursor="pointer"});
    map.on("mouseleave",layer.id,()=>{map.getCanvas().style.cursor=""});
  }
  loadVisiblePlaces();
});
map.on("moveend",loadVisiblePlaces);
function showMapPick(event){
  const pointPlace={name:"지도 선택 위치",lat:event.lngLat.lat,lon:event.lngLat.lng,source:"coordinate"};
  const layers=mapPickLayers();
  const hits=layers.length?map.queryRenderedFeatures(event.point,{layers}):[];
  const options=[];
  const names=new Set();
  for(const hit of hits){
    const props=hit.properties||{};
    const name=props["name:ko"]||props.name||props["name:nonlatin"]||props.name_en;
    if(typeof name!=="string"||!name.trim()||names.has(name.trim()))continue;
    names.add(name.trim());
    const coordinates=hit.geometry?.type==="Point"?hit.geometry.coordinates:null;
    options.push({name:name.trim(),lat:coordinates?.[1]??pointPlace.lat,lon:coordinates?.[0]??pointPlace.lon,kind:props.kind||props.class||"building",category:props.category,source:"map"});
    if(options.length>=4)break;
  }
  mapPickPopup?.remove();
  const root=document.createElement("div");root.className="map-place-picker";
  root.addEventListener("click",click=>click.stopPropagation());
  root.append(text("strong","\uc774 \uc704\uce58 \uc120\ud0dd"),text("small",options.length?"\ud45c\uc2dc\ub41c \uc7a5\uc18c\ub97c \uace0\ub974\uac70\ub098 \uc8fc\ubcc0 \uc7a5\uc18c\ub97c \ucc3e\ub294 \uc911\uc785\ub2c8\ub2e4.":"\uc8fc\ubcc0 \uc7a5\uc18c\ub97c \ucc3e\ub294 \uc911\uc785\ub2c8\ub2e4."));
  const addOption=(place,coordinate=false)=>{
    const row=document.createElement("div");row.className="map-place-option";
    row.append(text("span",coordinate?"\uc774 \uc9c0\uc810\uc758 \uc88c\ud45c":place.name));
    if(!coordinate){
      const label={school:"학교",worship:"교회·종교시설",public:"공공시설",shop:"상점"}[place.category];
      row.append(text("small",place.address||label||place.kind||"장소"));
    }
    const actions=document.createElement("div");actions.className="map-place-actions";
    for(const [which,label] of [["origin","\ucd9c\ubc1c\uc9c0\ub85c"],["destination","\ubaa9\uc801\uc9c0\ub85c"]]){
      const button=document.createElement("button");button.type="button";button.textContent=label;
      button.addEventListener("click",()=>setPlace(which,place));actions.append(button);
    }
    row.append(actions);root.append(row);return row;
  };
  for(const place of options)addOption(place);
  const coordinateRow=addOption(pointPlace,true);
  const popup=new maplibregl.Popup({closeOnClick:false,maxWidth:"320px",offset:12})
    .setLngLat(event.lngLat).setDOMContent(root).addTo(map);
  mapPickPopup=popup;
    const url="/api/citizen-routing/nearby?"+new URLSearchParams({lat:pointPlace.lat,lon:pointPlace.lon});
    fetch(url).then(response=>response.ok?response.json():Promise.reject(new Error("nearby")))
      .then(data=>{
        if(mapPickPopup!==popup||!popup.isOpen())return;
        const places=(data.places||[]).filter(place=>!names.has(place.name)).slice(0,5);
        root.querySelector("small").textContent=options.length+places.length?"\uc7a5\uc18c\ub97c \uace0\ub974\uac70\ub098 \uc88c\ud45c\ub85c \uc9c0\uc815\ud558\uc138\uc694.":"\uc8fc\ubcc0\uc5d0 \uc774\ub984 \uc788\ub294 \uc7a5\uc18c\uac00 \uc5c6\uc2b5\ub2c8\ub2e4. \uc88c\ud45c\ub85c \uc9c0\uc815\ud558\uc138\uc694.";
        for(const place of places){const row=addOption(place);root.insertBefore(row,coordinateRow)}
      }).catch(()=>{
        if(mapPickPopup===popup&&popup.isOpen())root.querySelector("small").textContent=options.length?"\ud45c\uc2dc\ub41c \uc7a5\uc18c\ub97c \uace0\ub974\uac70\ub098 \uc88c\ud45c\ub85c \uc9c0\uc815\ud558\uc138\uc694.":"\uc7a5\uc18c \uc815\ubcf4\ub97c \ubd88\ub7ec\uc624\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4. \uc88c\ud45c\ub85c \uc9c0\uc815\ud558\uc138\uc694.";
      });
}
function setPlace(which,place){
  mapPickPopup?.remove();
  state[which]=place;
  const coords=place.lat.toFixed(5)+", "+place.lon.toFixed(5);
  const coordinateOnly=place.source==="coordinate"||place.name==="\uc9c0\ub3c4 \uc120\ud0dd \uc704\uce58";
  $(which).value=coordinateOnly?coords:place.name;
  $(which+"-choice").textContent=coordinateOnly?"\uc88c\ud45c\ub85c \uc9c0\uc815\ud55c \uc704\uce58":place.address||"\uc7a5\uc18c \uc774\ub984\uc73c\ub85c \uc9c0\uc815\ub428";
  $(which+"-suggestions").replaceChildren();
  state.active=which==="origin"?"destination":"origin";
  $("pick-mode").textContent="\uc9c0\ub3c4 \uc120\ud0dd: "+(state.active==="origin"?"\ucd9c\ubc1c\uc9c0":"\ubaa9\uc801\uc9c0");
  drawMarkers();
}
function drawMarkers(){
  state.markers.forEach(marker=>marker.remove());state.markers=[];
  for(const [which,color] of [["origin","#0d775c"],["destination","#d35d42"]]){
    const place=state[which];if(!place)continue;
    const node=document.createElement("div");
    node.style.cssText="width:19px;height:19px;border:4px solid white;border-radius:50%;background:"+color+";box-shadow:0 1px 8px #0005";
    state.markers.push(new maplibregl.Marker({element:node}).setLngLat([place.lon,place.lat]).setPopup(new maplibregl.Popup().setText((which==="origin"?"출발":"도착")+" · "+place.name)).addTo(map));
  }
  if(state.origin&&state.destination)map.fitBounds([[Math.min(state.origin.lon,state.destination.lon),Math.min(state.origin.lat,state.destination.lat)],[Math.max(state.origin.lon,state.destination.lon),Math.max(state.origin.lat,state.destination.lat)]],{padding:80,maxZoom:14});
}
const placeKinds={building:"\uAC74\uBB3C",amenity:"\uC2DC\uC124",tourism:"\uAD00\uAD11\uC9C0",historic:"\uBB38\uD654\uC720\uC0B0",shop:"\uC0C1\uC810",healthcare:"\uC758\uB8CC\uC2DC\uC124",leisure:"\uC5EC\uAC00\uC2DC\uC124",office:"\uC0AC\uBB34\uC2E4",railway:"\uC5ED",public_transport:"\uAD50\uD1B5\uC2DC\uC124"};
const searchControllers={origin:null,destination:null};
async function suggest(which){
  const query=$(which).value.trim();state[which]=null;drawMarkers();
  const box=$(which+"-suggestions");box.replaceChildren();
  searchControllers[which]?.abort();
  if(query.length<2)return;
  const controller=new AbortController();searchControllers[which]=controller;
  try{
    const response=await fetch("/api/citizen-routing/places?q="+encodeURIComponent(query),{signal:controller.signal});
    const data=await response.json();
    if(!response.ok)throw new Error(data.message);
    if($(which).value.trim()!==query)return;
    for(const place of data.places||[]){
      const button=document.createElement("button");button.type="button";
      const category=place.kind.split(":")[0];
      const label=place.source==="gtfs"?(place.kind==="shuttle"?"\uB300\uCCB4 \uC154\uD2C0":"\uC815\uB958\uC7A5/\uC5ED"):(placeKinds[category]||"\uC7A5\uC18C");
      const detail=place.address||place.lat.toFixed(4)+", "+place.lon.toFixed(4);
      button.append(text("span",place.name),text("small",label+" \u00B7 "+detail));
      button.addEventListener("click",()=>setPlace(which,place));box.append(button);
    }
    if(!box.childElementCount)box.append(text("div","\uAC80\uC0C9 \uACB0\uACFC\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.","empty-suggestions"));
  }catch(error){if(error.name!=="AbortError")$("message").textContent="\uC7A5\uC18C \uAC80\uC0C9 \uC815\uBCF4\uB97C \uBD88\uB7EC\uC62C \uC218 \uC5C6\uC2B5\uB2C8\uB2E4."}
}
for(const which of ["origin","destination"]){
  let timer;
  $(which).addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>suggest(which),300)});
  $(which).addEventListener("focus",()=>{state.active=which;$("pick-mode").textContent="지도 선택: "+(which==="origin"?"출발지":"목적지")});
}
$("swap").addEventListener("click",()=>{
  const a=state.origin,b=state.destination;
  if(a&&b){setPlace("origin",b);setPlace("destination",a)}
});
async function loadStatus(){
  try{
    const response=await fetch("/api/citizen-routing/status"),data=await response.json();
    if(!response.ok)throw new Error(data.message);
    const shuttle=!!data.shuttle_stats;
    $("status").textContent="운행 자료 "+data.service_dates[0]+" ~ "+data.service_dates[1]+" · A 교통망 "+data.existing_stats.routes+"개 노선 · "+(shuttle?"셔틀 "+data.shuttle_stats.routes+"개 노선":"셔틀 자료 없음")+" · 제외 노선 "+data.excluded_route_ids.length+"개 · 버전 "+data.version;
    const selectedDate=$("departure").value.slice(0,10);
    if(selectedDate<data.service_dates[0]||selectedDate>data.service_dates[1])$("departure").value=data.service_dates[0]+"T07:00";
    if(!data.graph_ready){$("status").textContent+=" · OTP 그래프를 아직 구축하지 않았습니다.";$("status").classList.add("error")}
    if(data.graph_ready&&!data.otp_running){$("status").textContent+=" · OTP 서버가 실행 중이지 않습니다.";$("status").classList.add("error")}
    if(!data.places_ready)$("status").textContent+=" \u00B7 \uAC74\uBB3C/\uC2DC\uC124 \uAC80\uC0C9 \uC0C9\uC778 \uC5C6\uC74C";
    $("include-shuttle").checked=shuttle;$("include-shuttle").disabled=!shuttle;
  }catch(error){$("status").textContent=error.message;$("status").classList.add("error")}
}
const mins=seconds=>Math.round(seconds/60)+"분";
const sortKeys={
  optimal:route=>[route.duration_seconds+route.transfers*600+route.walk_seconds*.5,route.duration_seconds,route.transfers,route.walk_seconds],
  fastest:route=>[route.duration_seconds,route.transfers,route.walk_seconds],
  transfers:route=>[route.transfers,route.duration_seconds,route.walk_seconds],
  walking:route=>[route.walk_seconds,route.duration_seconds,route.transfers]
};
function compareRoutes(a,b,mode){
  const left=sortKeys[mode](a),right=sortKeys[mode](b);
  for(let index=0;index<left.length;index++)if(left[index]!==right[index])return left[index]-right[index];
  return (a.start||"").localeCompare(b.start||"")||String(a.id||"").localeCompare(String(b.id||""));
}
function sortRoutes(keepSelected){
  const selectedId=keepSelected?state.routes[state.selected]?.id:null;
  state.routes.sort((a,b)=>compareRoutes(a,b,state.sortMode));
  state.selected=selectedId?state.routes.findIndex(route=>route.id===selectedId):0;
  if(state.selected<0)state.selected=0;
}
const sortDropdown=$("sort-dropdown");
const sortTrigger=$("sort-trigger");
const sortMenu=$("sort-menu");
const sortOptions=[...sortMenu.querySelectorAll("[data-sort]")];
function renderSortMenu(){
  for(const option of sortOptions)option.setAttribute("aria-pressed",option.dataset.sort===state.sortMode?"true":"false");
  sortTrigger.querySelector(".sort-label").textContent=sortOptions.find(option=>option.dataset.sort===state.sortMode).textContent;
}
function setSortOpen(open){
  if(open){
    const rect=sortDropdown.getBoundingClientRect();
    const spaceBelow=window.innerHeight-rect.bottom;
    sortDropdown.classList.toggle("open-up",spaceBelow<sortMenu.scrollHeight+12&&rect.top>spaceBelow);
  }
  sortDropdown.classList.toggle("open",open);
  sortTrigger.setAttribute("aria-expanded",open?"true":"false");
  sortMenu.inert=!open;
}
sortTrigger.addEventListener("click",()=>setSortOpen(!sortDropdown.classList.contains("open")));
for(const option of sortOptions)option.addEventListener("click",()=>{
  if(state.sortMode!==option.dataset.sort){
    state.sortMode=option.dataset.sort;
    sortRoutes(true);renderResults();drawRoute();
  }
  renderSortMenu();setSortOpen(false);sortTrigger.focus();
});
document.addEventListener("pointerdown",event=>{
  if(!sortDropdown.contains(event.target))setSortOpen(false);
});
sortDropdown.addEventListener("keydown",event=>{
  if(event.key==="Escape"&&sortDropdown.classList.contains("open")){
    event.preventDefault();setSortOpen(false);sortTrigger.focus();
  }
  if((event.key==="ArrowDown"||event.key==="ArrowUp")&&sortDropdown.classList.contains("open")){
    event.preventDefault();
    const current=sortOptions.indexOf(document.activeElement);
    const step=event.key==="ArrowDown"?1:-1;
    sortOptions[current<0?(step>0?0:sortOptions.length-1):(current+step+sortOptions.length)%sortOptions.length].focus();
  }
});
const modeInfo={
  walk:{label:"\ub3c4\ubcf4",icon:"\ud83d\udeb6"},
  bus:{label:"\ubc84\uc2a4",icon:"\ud83d\ude8c"},
  subway:{label:"\uc9c0\ud558\ucca0",icon:"\ud83d\ude87"},
  shuttle:{label:"\ub300\uccb4 \uc154\ud2c0",icon:"\ud83d\ude8c"}
};
const busCategoryLabels={village:"\ub9c8\uc744\ubc84\uc2a4",city:"\uc2dc\ub0b4\ubc84\uc2a4",gyeonggi:"\uacbd\uae30\ubc84\uc2a4",unclassified:"\ubbf8\ubd84\ub958"};
const legLabel=leg=>leg.kind==="bus"?(busCategoryLabels[leg.bus_category]||busCategoryLabels.unclassified):(modeInfo[leg.kind]?.label||leg.kind);
const timeLabel=value=>new Date(value).toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"});
const routePlaceLabel=place=>place?.source==="coordinate"?place.lat.toFixed(5)+", "+place.lon.toFixed(5):place?.name||"\uc704\uce58 \ubbf8\uc9c0\uc815";
function renderResults(){
  const box=$("results");box.replaceChildren();
  $("result-count").textContent=state.routes.length+"\uac1c";
  const categories={optimal:"\ucd5c\uc801 \uacbd\ub85c",fastest:"\ucd5c\uc18c \uc2dc\uac04",transfers:"\ucd5c\uc18c \ud658\uc2b9",walking:"\ucd5c\uc18c \ub3c4\ubcf4"};
  const routeCategories=new Map();
  for(const [mode,label] of Object.entries(categories)){
    const best=state.routes.reduce((winner,route)=>!winner||compareRoutes(route,winner,mode)<0?route:winner,null);
    if(best)routeCategories.set(best,[...(routeCategories.get(best)||[]),label]);
  }
  state.routes.forEach((route,index)=>{
    const card=document.createElement("button");
    card.type="button";
    card.className="result"+(index===state.selected?" active":"");
    card.setAttribute("aria-pressed",index===state.selected?"true":"false");
    const routeNames=route.legs.filter(leg=>leg.kind!=="walk").map(leg=>legLabel(leg)+(leg.route_name?" "+leg.route_name:"")).join(" \u00b7 ")||"\ub3c4\ubcf4 \uacbd\ub85c";
    const header=text("div","","result-top");
    const rank=text("span","","result-rank");
    for(const label of routeCategories.get(route)||[])rank.append(text("span",label,"result-category"));
    header.append(rank,text("span",timeLabel(route.start)+" \ucd9c\ubc1c \u2192 "+timeLabel(route.end)+" \ub3c4\ucc29","result-times"));
    const duration=text("div","","result-duration");
    duration.append(text("strong",String(Math.round(route.duration_seconds/60))),text("span","\ubd84"),text("small",routeNames,"result-line-names"));
    card.append(header,duration);
    const progress=text("div","","result-progress");
    progress.setAttribute("role","img");
    progress.setAttribute("aria-label",route.legs.map(leg=>legLabel(leg)+" "+mins(leg.duration_seconds)).join(", "));
    const legCount=route.legs.length;
    const totalLegSeconds=route.legs.reduce((total,leg)=>total+Math.max(leg.duration_seconds,1),0);
    const minShare=Math.min(.15,.88/legCount);
    for(const leg of route.legs){
      const info=modeInfo[leg.kind]||{label:leg.kind,icon:"\u25cf"};
      const segment=text("div","","result-segment "+leg.kind+(leg.kind==="bus"?" "+(leg.bus_category||"unclassified"):""));
      const share=minShare+(1-minShare*legCount)*Math.max(leg.duration_seconds,1)/totalLegSeconds;
      segment.style.flexBasis=(share*100)+"%";
      segment.title=legLabel(leg)+" "+(leg.route_name||"")+" "+mins(leg.duration_seconds);
      segment.append(text("span",info.icon,"segment-icon"),text("span",mins(leg.duration_seconds),"segment-time"));
      progress.append(segment);
    }
    card.append(progress);
    const journey=text("div","","result-journey");
    const placeRow=(which,place)=>{
      const row=text("div","","result-place "+which);
      row.append(text("span",which==="origin"?"\ucd9c":"\ub3c4","place-dot"));
      const content=text("div","","place-copy");
      content.append(text("small",which==="origin"?"\ucd9c\ubc1c\uc9c0":"\ub3c4\ucc29\uc9c0"),text("strong",routePlaceLabel(place)));
      row.append(content);
      return row;
    };
    journey.append(placeRow("origin",state.routeEndpoints?.origin));
    journey.append(text("div",routeNames,"result-route-summary"));
    const steps=text("div","","result-steps");
    for(const [legIndex,leg] of route.legs.entries()){
      const info=modeInfo[leg.kind]||{label:leg.kind,icon:"\u25cf"};
      const fromName=legIndex===0&&leg.from.name==="Origin"?routePlaceLabel(state.routeEndpoints?.origin):leg.from.name;
      const toName=legIndex===route.legs.length-1&&leg.to.name==="Destination"?routePlaceLabel(state.routeEndpoints?.destination):leg.to.name;
      const row=text("div","","result-step "+leg.kind+(leg.kind==="bus"?" "+(leg.bus_category||"unclassified"):""));
      row.append(text("span",info.icon,"step-icon"));
      const copy=text("div","","step-copy");
      copy.append(text("strong",legLabel(leg)+(leg.route_name?" "+leg.route_name:"")+" \u00b7 "+mins(leg.duration_seconds)),
                  text("small",fromName+" \u2192 "+toName));
      row.append(copy);
      steps.append(row);
    }
    journey.append(steps,placeRow("destination",state.routeEndpoints?.destination));
    card.append(journey);
    const meta=text("div","","result-meta");
    meta.append(text("span","\ub3c4\ubcf4 "+mins(route.walk_seconds)),
                text("span","\ub300\uae30 "+mins(route.wait_seconds)),
                text("span","\ud658\uc2b9 "+route.transfers+"\ud68c"));
    card.append(meta);
    card.addEventListener("click",()=>{state.selected=index;renderResults();drawRoute()});
    box.append(card);
  });
}
const reducedMotion=window.matchMedia("(prefers-reduced-motion: reduce)");
let activeSlot=null;
let fadeTimer=null;
const layerNames=slot=>["route-"+slot+"-alternatives","route-"+slot+"-casing","route-"+slot+"-verified","route-"+slot+"-schematic"];
function clearSlot(slot){
  for(const id of layerNames(slot))if(map.getLayer(id))map.removeLayer(id);
  const source="route-lines-"+slot;
  if(map.getSource(source))map.removeSource(source);
}
function setSlotOpacity(slot,opacity){
  const targets=[opacity*.08,opacity*.9,opacity*.97,opacity*.97];
  layerNames(slot).forEach((id,index)=>{if(map.getLayer(id))map.setPaintProperty(id,"line-opacity",targets[index])});
}
function drawRoute(){
  if(!map.isStyleLoaded())return;
  if(fadeTimer){clearTimeout(fadeTimer);fadeTimer=null}
  const selected=state.routes[state.selected];
  if(!selected){clearSlot(0);clearSlot(1);activeSlot=null;state.routeMarkers.forEach(marker=>marker.remove());state.routeMarkers=[];return}
  const previous=activeSlot;
  const next=previous===0?1:0;
  clearSlot(next);
  const features=[];
  for(const [index,route] of state.routes.entries()){
    for(const leg of route.legs){
      if(!leg.geometry||leg.geometry.length<2)continue;
      features.push({type:"Feature",properties:{color:legColor(leg),selected:index===state.selected,schematic:leg.geometry_status==="stop_schematic"},geometry:{type:"LineString",coordinates:leg.geometry}});
    }
  }
  if(features.length){
    const source="route-lines-"+next;
    const duration=reducedMotion.matches?0:420;
    const transition={duration,delay:0};
    map.addSource(source,{type:"geojson",data:{type:"FeatureCollection",features}});
    map.addLayer({id:"route-"+next+"-alternatives",type:"line",source,filter:["==",["get","selected"],false],paint:{"line-color":"#52756e","line-width":2.5,"line-opacity":0,"line-opacity-transition":transition},layout:{"line-join":"round","line-cap":"round"}});
    map.addLayer({id:"route-"+next+"-casing",type:"line",source,filter:["==",["get","selected"],true],paint:{"line-color":"#fff","line-width":9,"line-opacity":0,"line-opacity-transition":transition},layout:{"line-join":"round","line-cap":"round"}});
    map.addLayer({id:"route-"+next+"-verified",type:"line",source,filter:["all",["==",["get","selected"],true],["==",["get","schematic"],false]],paint:{"line-color":["get","color"],"line-width":5,"line-opacity":0,"line-opacity-transition":transition},layout:{"line-join":"round","line-cap":"round"}});
    map.addLayer({id:"route-"+next+"-schematic",type:"line",source,filter:["all",["==",["get","selected"],true],["==",["get","schematic"],true]],paint:{"line-color":["get","color"],"line-width":5,"line-opacity":0,"line-opacity-transition":transition,"line-dasharray":[1.5,1.2]},layout:{"line-join":"round","line-cap":"round"}});
    requestAnimationFrame(()=>{if(activeSlot===next)setSlotOpacity(next,1)});
    const coords=features.filter(feature=>feature.properties.selected).flatMap(feature=>feature.geometry.coordinates);
    if(coords.length)map.fitBounds(coords.reduce((bounds,point)=>bounds.extend(point),new maplibregl.LngLatBounds(coords[0],coords[0])),{padding:75,maxZoom:15,duration:reducedMotion.matches?0:850});
  }
  for(const id of ["citizen-osm-worship","citizen-osm-civic","citizen-osm-major"])if(map.getLayer(id))map.moveLayer(id)
  activeSlot=next;
  if(previous!==null){
    setSlotOpacity(previous,0);
    fadeTimer=setTimeout(()=>{clearSlot(previous);fadeTimer=null},reducedMotion.matches?0:470);
  }
  const oldMarkers=state.routeMarkers;
  oldMarkers.forEach(marker=>{marker.getElement().style.opacity="0";setTimeout(()=>marker.remove(),reducedMotion.matches?0:350)});
  state.routeMarkers=[];
  for(const leg of selected.legs.filter(leg=>leg.kind!=="walk")){
    for(const [place,action,time] of [[leg.from,"탑승",leg.start],[leg.to,"하차",leg.end]]){
      if(!Number.isFinite(place.lon)||!Number.isFinite(place.lat))continue;
      const dot=document.createElement("div");
      dot.style.cssText="width:13px;height:13px;border:2px solid white;border-radius:50%;background:"+legColor(leg)+";box-shadow:0 1px 5px #0008;opacity:0;transition:opacity 350ms ease";
      requestAnimationFrame(()=>{dot.style.opacity="1"});
      const label=place.name+" · "+(legLabel(leg)+(leg.route_name?" "+leg.route_name:""))+" "+action+" · "+(time||"시간 정보 없음");
      state.routeMarkers.push(new maplibregl.Marker({element:dot}).setLngLat([place.lon,place.lat]).setPopup(new maplibregl.Popup().setText(label)).addTo(map));
    }
  }
  const schematic=selected.legs.some(leg=>leg.geometry_status==="stop_schematic");
  $("message").textContent=schematic?"선택한 경로를 지도에 표시했습니다. 점선은 정차점을 연결한 개략선이며 실제 주행 도로와 다를 수 있습니다.":"선택한 경로를 지도에 표시했습니다.";
}
map.on("load",drawRoute);
$("search-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(!state.origin||!state.destination){$("message").textContent="검색 결과를 선택하거나 지도에서 출발지와 목적지를 지정하세요.";return}
  const selectedEndpoints={origin:{...state.origin},destination:{...state.destination}};
  const params=new URLSearchParams({origin_lat:state.origin.lat,origin_lon:state.origin.lon,destination_lat:state.destination.lat,destination_lon:state.destination.lon,departure_at:$("departure").value+":00+09:00",include_shuttle:$("include-shuttle").checked});
  $("message").textContent="운행시간표를 확인하고 있습니다.";
  try{
    const response=await fetch("/api/citizen-routing/plan?"+params),data=await response.json();
    if(!response.ok)throw new Error(data.message||data.code);
    state.routes=data.routes;state.routeEndpoints=selectedEndpoints;sortRoutes(false);renderResults();
    $("message").textContent=data.routes.length?"실제 운행시간을 기준으로 계산한 추천 경로입니다.":"해당 시각에 이용 가능한 경로가 없습니다.";
    drawRoute();
  }catch(error){state.routes=[];renderResults();drawRoute();$("message").textContent=error.message}
});
loadStatus();
