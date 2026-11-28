// Keep each grid cell at a fixed location while its measured value changes.
const LON_KM=111.32*Math.cos(37.5*Math.PI/180), LAT_KM=111.32;
const RADII_KM=[3.2,1.25];

export function aggregateBusCircles(features,{level=0,valueKey='board',date='',hour=''}={}){
  const radius=RADII_KM[level];
  if(!radius)throw new Error('Unsupported circle level');
  const buckets=new Map();let total=0;
  for(const feature of features||[]){
    const board=Number(feature.properties?.board||0),affected=Number(feature.properties?.affected_board||0);
    const n=valueKey==='available_board'?Math.max(0,board-affected):Number(feature.properties?.[valueKey]||0);
    const anchor=board+Number(feature.properties?.alight||0),point=feature.geometry?.coordinates;
    if(!Number.isFinite(n)||n<0||!Number.isFinite(anchor)||anchor<=0||!point||!Number.isFinite(point[0])||!Number.isFinite(point[1]))continue;
    const x=(point[0]-127)*LON_KM,y=(point[1]-37.5)*LAT_KM;
    const a=Math.floor(x/radius),b=Math.floor(y/radius),id=`${level}:${a}:${b}`;
    if(!buckets.has(id))buckets.set(id,{id,count:0,anchorTotal:0,lon:0,lat:0,stops:new Set()});
    const cell=buckets.get(id);cell.count+=n;cell.anchorTotal+=anchor;cell.lon+=anchor*point[0];cell.lat+=anchor*point[1];
    cell.stops.add(`${feature.properties?.mode||'unknown'}:${feature.properties?.stop_id||id}`);total+=n;
  }
  return {type:'FeatureCollection',features:[...buckets.values()].map(cell=>({
    type:'Feature',id:cell.id,geometry:{type:'Point',coordinates:[cell.lon/cell.anchorTotal,cell.lat/cell.anchorTotal]},
    properties:{passenger_count:cell.count,stop_count:cell.stops.size,date,hour,level},
  })),total};
}
