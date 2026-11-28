// Visual separation only. Route and stop coordinates remain at their source positions.
const meters = point => [point[0] * 88000, point[1] * 111000];
const cell = (x, y) => `${Math.floor(x / 24)}:${Math.floor(y / 24)}`;
const linesOf = geometry => geometry?.type === 'LineString' ? [geometry.coordinates] : geometry?.type === 'MultiLineString' ? geometry.coordinates : [];
const kindOrder = ['city','gyeonggi','village','strike','shuttle','unknown_bus','unknown'];
const kindOffset = {city:-4,gyeonggi:0,village:4,strike:-8,shuttle:8,unknown_bus:0,unknown:0};

function distanceToSegment(point, segment) {
  const [x, y] = point, [ax, ay] = segment.a, [bx, by] = segment.b;
  const dx = bx - ax, dy = by - ay;
  const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(x - ax - t * dx, y - ay - t * dy);
}

export function separateRouteLines(features) {
  const segments = [], keptGrid = new Map(), allGrid = new Map();
  for (const feature of features) {
    const routeId = String(feature.properties.route_id || feature.id);
    const kind = feature.properties.kind;
    linesOf(feature.geometry).forEach((line, lineIndex) => {
      if(line.length<2)return;
      const first=meters(line[0]),last=meters(line.at(-1));
      const heading=Math.hypot(last[0]-first[0],last[1]-first[1])>10?[last[0]-first[0],last[1]-first[1]]:
        line.length>1?[meters(line[1])[0]-first[0],meters(line[1])[1]-first[1]]:[1,0];
      const lineSign=heading[0]>0||(heading[0]===0&&heading[1]>=0)?1:-1;
      for (let i = 0; i < line.length - 1; i++) {
        const a = meters(line[i]), b = meters(line[i + 1]);
        const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (length < .3) continue;
        const segment = {feature, routeId, kind, lineIndex, index:i, a, b, length, lineSign,
          patternIndex:feature.properties.pattern_indices?.[lineIndex],angle:Math.atan2(b[1]-a[1],b[0]-a[0])};
        segments.push(segment);
      }
    });
  }
  const samples = segment => [.25,.5,.75].map(t=>[segment.a[0]+(segment.b[0]-segment.a[0])*t,segment.a[1]+(segment.b[1]-segment.a[1])*t]);
  const nearby = (index,point) => {
    const cx=Math.floor(point[0]/24),cy=Math.floor(point[1]/24),found=new Set();
    for(let x=cx-1;x<=cx+1;x++)for(let y=cy-1;y<=cy+1;y++)for(const item of index.get(`${x}:${y}`)||[])found.add(item);
    return found;
  };
  const indexSegment = (index,segment) => {
    const steps=Math.max(1,Math.ceil(segment.length/15));
    for(let step=0;step<=steps;step++) {
      const t=step/steps,x=segment.a[0]+(segment.b[0]-segment.a[0])*t,y=segment.a[1]+(segment.b[1]-segment.a[1])*t;
      const key=cell(x,y);if(!index.has(key))index.set(key,[]);index.get(key).push(segment);
    }
  };
  for(const segment of segments)indexSegment(allGrid,segment);
  const patternCounts=new Map();
  for(const segment of segments)if(Number.isInteger(segment.patternIndex)){
    if(!patternCounts.has(segment.routeId))patternCounts.set(segment.routeId,new Set());
    patternCounts.get(segment.routeId).add(segment.patternIndex);
  }
  for(const segment of segments){
    if((patternCounts.get(segment.routeId)?.size||0)<2)continue;
    const matching=samples(segment).filter(point=>[...nearby(allGrid,point)].some(other=>
      other.routeId===segment.routeId&&other.patternIndex!==segment.patternIndex&&
      Math.abs(Math.cos(segment.angle-other.angle))>=.94&&distanceToSegment(point,other)<=18)).length;
    segment.directionVariant=matching<2;
  }
  // A shared road is shown once per transport kind, irrespective of route number.
  for(const segment of [...segments].sort((a,b)=>b.length-a.length)) {
    const covered=samples(segment).every(point=>[...nearby(keptGrid,point)].some(other=>
      other.kind===segment.kind&&Math.abs(Math.cos(segment.angle-other.angle))>=.94&&distanceToSegment(point,other)<=18));
    segment.hidden=covered;
    if(!covered)indexSegment(keptGrid,segment);
  }
  // Keep a short handoff on both sides of a shared run so two nearby traces meet visually.
  const byLine=new Map();
  for(const segment of segments){
    const key=`${segment.routeId}:${segment.lineIndex}`;
    if(!byLine.has(key))byLine.set(key,[]);
    byLine.get(key).push(segment);
  }
  for(const line of byLine.values()){
    line.sort((a,b)=>a.index-b.index);
    const hidden=line.map(segment=>segment.hidden);
    for(let i=0;i<line.length;i++)if(hidden[i]&&((i>0&&!hidden[i-1])||(i<line.length-1&&!hidden[i+1])))line[i].hidden=false;
  }
  const segmentOffsets=new Map();
  for(const segment of segments.filter(item=>!item.hidden)) {
    segmentOffsets.set(segment,(kindOffset[segment.kind]||0)*segment.lineSign);
  }
  const byFeature=new Map();
  for(const segment of segments){if(!byFeature.has(segment.feature))byFeature.set(segment.feature,new Map());byFeature.get(segment.feature).set(`${segment.lineIndex}:${segment.index}`,segment);}
  return features.flatMap(feature => {
    const routeId=String(feature.properties.route_id||feature.id),out=[];
    linesOf(feature.geometry).forEach((line,lineIndex) => {
      let run=[],offset=null,variant=null,part=0;
      const flush=()=>{if(run.length>1)out.push({...feature,id:`${routeId}:${lineIndex}:${part++}`,geometry:{type:'LineString',coordinates:run},properties:{...feature.properties,lane_offset:offset||0,direction_variant:Boolean(variant)}});};
      for(let i=0;i<line.length-1;i++) {
        const segment=byFeature.get(feature)?.get(`${lineIndex}:${i}`);
        if(!segment||segment.hidden){flush();run=[];offset=null;variant=null;continue;}
        const next=segmentOffsets.get(segment)||0;
        const nextVariant=Boolean(segment.directionVariant);
        if(offset!==null&&(next!==offset||nextVariant!==variant)){flush();run=[];}
        if(!run.length)run=[line[i]];
        run.push(line[i+1]);offset=next;variant=nextVariant;
      }
      flush();
    });
    return out;
  });
}

export function separateStopIcons(features, project) {
  const groups=[];
  for(const feature of features) {
    const p=project(feature.geometry.coordinates);
    let group=groups.find(group=>Math.hypot(group.x-p.x,group.y-p.y)<17);
    if(!group){group={x:p.x,y:p.y,features:[]};groups.push(group);}
    group.features.push(feature);
  }
  return groups.flatMap(group=>group.features.map(feature=>{
    const kinds=[...new Set(group.features.map(item=>item.properties.stop_kind))].sort((a,b)=>kindOrder.indexOf(a)-kindOrder.indexOf(b));
    const count=kinds.length,index=kinds.indexOf(feature.properties.stop_kind);
    const angle=count===2?Math.PI*index:2*Math.PI*index/count;
    const radius=count<2?0:count===2?28:count<=4?34:42;
    return {...feature,properties:{...feature.properties,icon_offset:[Math.round(Math.cos(angle)*radius),Math.round(Math.sin(angle)*radius)]}};
  }));
}
