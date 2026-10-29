// Only tools actually used by the agent appear as saved processing stages.
const cadyxCloudTools = {
  deduplicate: 'Удаление дубликатов',
  radius_filter: 'Фильтр изолированных точек',
  crop: 'Обрезка по границам',
  voxel_downsample: 'Прореживание по вокселям',
  remove_overlaps: 'Удаление совпадающих слоёв',
  isolation_filter: 'Удаление изолированных точек',
  simplify: 'Упрощение облака точек',
  smooth: 'Сглаживание облака точек',
};
function cadyxCloudStage(feature) {
  if (feature.operation === 'import_point_cloud') return {title:'Импорт облака точек', input:null, output:feature.stats?.point_count};
  if (feature.operation !== 'process_point_cloud') return null;
  return {title:cadyxCloudTools[feature.method] || 'Обработка облака точек', input:feature.stats?.input_points, output:feature.stats?.output_points};
}
function cadyxMeshStage(feature) {
  if(feature?.operation !== 'process_mesh') return null;
  const titles={clean:'Очистка сетки',orient:'Исправление ориентации сетки',repair_orientation:'Удаление конфликтующих треугольников',remove_small_components:'Удаление мелких фрагментов',smooth:'Сглаживание сетки'};
  return {title:titles[feature.method]||'Обработка сетки',input:feature.stats?.before?.triangles,output:feature.stats?.after?.triangles};
}
(() => {
  /* Keep folder PLY preview working even though this served slot now hosts the legacy app. */
  if (!window.CadyxPlyPreview) {
    const T={char:[1,'getInt8'],int8:[1,'getInt8'],uchar:[1,'getUint8'],uint8:[1,'getUint8'],short:[2,'getInt16'],int16:[2,'getInt16'],ushort:[2,'getUint16'],uint16:[2,'getUint16'],int:[4,'getInt32'],int32:[4,'getInt32'],uint:[4,'getUint32'],uint32:[4,'getUint32'],float:[4,'getFloat32'],float32:[4,'getFloat32'],double:[8,'getFloat64'],float64:[8,'getFloat64']};
    const read=(dv,t,o)=>{const x=T[t];if(!x)throw Error(`Unsupported PLY type: ${t}`);return dv[x[1]](o,...(x[0]>1?[true]:[]))};
    const parse=buffer=>{const bytes=new Uint8Array(buffer),head=new TextDecoder('ascii').decode(bytes.subarray(0,65536)),mark=head.indexOf('end_header'),end=head.indexOf('\n',mark);if(mark<0||end<0)throw Error('Invalid PLY header.');let format='',cur=null;const els=[];for(const line of head.slice(0,mark).split(/\r?\n/)){const f=line.trim().split(/\s+/);if(f[0]==='format')format=f[1];else if(f[0]==='element'){cur={name:f[1],count:+f[2],properties:[]};els.push(cur)}else if(f[0]==='property'&&cur)cur.properties.push(f[1]==='list'?{list:1,countType:f[2],itemType:f[3],name:f[4]}:{type:f[1],name:f[2]})}if(format!=='binary_little_endian')throw Error('Only binary little-endian PLY is supported.');const v=els[0],face=els.find(x=>x.name==='face');if(!v||v.name!=='vertex')throw Error('PLY vertices not found.');const dv=new DataView(buffer),off={x:null,y:null,z:null};let stride=0;for(const p of v.properties){if(p.list)throw Error('Vertex lists are not supported.');if(!T[p.type])throw Error('Unsupported PLY type.');if(p.name in off)off[p.name]={o:stride,t:p.type};stride+=T[p.type][0]}if(Object.values(off).some(x=>!x))throw Error('PLY x/y/z are required.');let pos=new Float32Array(v.count*3),cursor=end+1;const bounds={min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity]};for(let i=0;i<v.count;i++){for(let a=0;a<3;a++){const p=off[['x','y','z'][a]],x=read(dv,p.t,cursor+p.o);pos[i*3+a]=x;bounds.min[a]=Math.min(bounds.min[a],x);bounds.max[a]=Math.max(bounds.max[a],x)}cursor+=stride}const tri=[];let faceCount=0;if(face){const step=Math.max(1,Math.ceil(face.count/36000));for(let fi=0;fi<face.count;fi++){for(const p of face.properties){if(p.list){const n=read(dv,p.countType,cursor);cursor+=T[p.countType][0];const sample=p.name==='vertex_indices'&&fi%step===0;let first=null,prev=null;for(let j=0;j<n;j++){const idx=read(dv,p.itemType,cursor);cursor+=T[p.itemType][0];if(sample){if(j===0)first=prev=idx;else if(j===1)prev=idx;else{tri.push(first,prev,idx);prev=idx}}}}else cursor+=T[p.type][0]}faceCount++}}return{positions:pos,triangles:new Uint32Array(tri),bounds,vertexCount:v.count,faceCount,previewFaceCount:tri.length/3}};
    window.CadyxPlyPreview={parse};
  }

  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const svg=(d)=>`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  const I={model:svg('m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5M12 12v9'),sketch:svg('M5 18 17 6M7 6h10v10 M5 18h3M16 6h3'),fillet:svg('M5 19V9a4 4 0 0 1 4-4h10M9 19V11a2 2 0 0 1 2-2h8'),thread:svg('M6 5h12M6 9h12M6 13h12M6 17h12M9 3 7 21M17 3l-2 18'),cut:svg('M5 5h14v14H5zM9 5v10h10'),origin:svg('M12 3v18M3 12h18 M9 12a3 3 0 1 0 6 0 3 3 0 1 0-6 0'),plane:svg('m5 8 11-3 3 11-11 3-3-11Z'),files:svg('M4 7.5h6l2-2h8v13H4v-11Z'),projects:svg('M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z'),activity:svg('M4 12h4l2-5 4 10 2-5h4'),settings:svg('M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z M19 12l2-1-2-3-2 .5-1.5-1L15 5H9l-.5 2.5-1.5 1L5 8l-2 3 2 1v2l-2 1 2 3 2-.5 1.5 1L9 21h6l.5-2.5 1.5-1 2 .5 2-3-2-1v-2Z'),comment:svg('M5 5h14v11H9l-4 3V5Z M8 9h8M8 12h6'),cloud:svg('M7 18h10a4 4 0 0 0 .4-8A6 6 0 0 0 6.1 8.5 4.5 4.5 0 0 0 7 18Z'),select:svg('m5 3 12 10-6 1.2L9 20 5 3Z'),orbit:svg('M4 12a8 8 0 0 1 13.5-5.8L20 9M20 4v5h-5M20 12a8 8 0 0 1-13.5 5.8L4 15M4 20v-5h5'),fit:svg('M8 4H4v4M16 4h4v4M20 16v4h-4M8 20H4v-4M8 8h8v8H8z')};
  const meta={create_box:['Блок','model'],metric_thread:['Резьба','thread'],reconstruct_scan:['Реконструкция скана','model'],import_point_cloud:['Облако точек','cloud'],import_step:['Импорт STEP','model'],extrude_sketch:['Выдавливание','model'],revolve_sketch:['Вращение','model'],loft_sketches:['Переход по сечениям','model'],loft_profiles:['Переход по сечениям','model'],create_ruled_shell:['Линейчатая оболочка','model'],fuse_bodies:['Объединение тел','model'],cut_bodies:['Вырезание','cut'],sweep_sketch:['Протяжка','model'],fillet_edges:['Скругление','fillet'],variable_fillet_edges:['Переменное скругление','fillet'],chamfer_edges:['Фаска','model'],shell_body:['Оболочка','model'],offset_body:['Смещение поверхности','model'],draft_faces:['Уклон граней','model'],mirror_body:['Зеркальное отражение','model'],linear_pattern_body:['Линейный массив','model'],circular_pattern_body:['Круговой массив','model'],boss:['Выступ','model'],hole:['Отверстие','cut'],through_hole:['Сквозное отверстие','cut'],counterbore_hole:['Отверстие с цековкой','cut'],countersink_hole:['Отверстие с зенковкой','cut'],threaded_hole:['Резьбовое отверстие','cut'],pad_sketch_to_body:['Выдавливание','model'],rib_from_sketch:['Ребро жёсткости','model'],pocket_sketch:['Карман','cut'],extrude_profile_on_plane:['Выдавливание профиля','model'],resize_imported_thread:['Изменение резьбы','thread'],transform_imported_brep:['Преобразование тела','model']};
  const editable={create_box:['width','height','depth'],metric_thread:['major_diameter','pitch','length'],extrude_sketch:['depth'],revolve_sketch:['angle_degrees','outer_diameter','height'],sweep_sketch:['radius'],fillet_edges:['radius']};
  const pLabel={width:'Ширина',height:'Высота',depth:'Глубина',outer_diameter:'Наружный диаметр',major_diameter:'Номинальный диаметр',pitch:'Шаг',length:'Длина',angle_degrees:'Угол',radius:'Радиус',centered:'По центру',handedness:'Направление резьбы',diameter_delta:'Изменение диаметра',rotation_axis:'Ось вращения',translation:'Перемещение',section_offsets:'Смещения сечений',sewing_tolerance:'Допуск сшивания'};
  Object.assign(pLabel,{min_neighbors:'Минимум соседей',voxel_size:'Размер вокселя',min:'Минимальные XYZ',max:'Максимальные XYZ',min_triangles:'Минимум треугольников во фрагменте',iterations:'Итерации',strength:'Сила сглаживания'});
  const fmt=v=>Number.isFinite(Number(v))?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:3}).format(Number(v)):'—';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function enhance(){
    const detail=$('#project-detail'),grid=$('#primary-grid'),nav=$('#workspace-navigation'),sidebar=$('.section-sidebar'),inspector=$('.inspector-column'),panel=$('.model-panel'),canvas=$('#model-preview');
    if(!detail||!grid||!nav||!sidebar||!inspector||!panel||!canvas)return;
    const style=document.createElement('style');style.id='cadyx-semantic-v3';style.textContent=`
.cad-model-tree{grid-template-rows:58px 54px minmax(0,1fr)}
.cad-tree-heading{padding:0 18px}.cad-tree-heading strong{font-size:17px}.cad-tree-collapse{width:36px;height:36px;font-size:18px}.cad-tree-collapse:hover{border-color:#633817;background:#24150d}
.cad-tree-search{padding:8px 12px 9px}.cad-tree-search input{height:38px;padding:0 12px;border-radius:8px;font-size:12px}.cad-tree-scroll{padding:8px 10px 18px}
.cad-tree-root,.cad-tree-static,.cad-tree-features{gap:5px}.cad-tree-root-row,.cad-tree-static-row{min-height:38px;padding:6px 9px;grid-template-columns:20px 22px minmax(0,1fr);gap:7px;font-size:12px}.cad-tree-root-row{color:#eef2f4}.cad-tree-root-row .cad-tree-icon{color:var(--accent)}.cad-tree-root-row .cad-tree-icon svg{width:20px;height:20px}.cad-tree-static{margin-left:20px;padding-left:8px;border-left-color:#32404a}.cad-tree-features{margin-top:8px;gap:5px}.cad-tree-icon svg,.cad3-icon svg{width:18px;height:18px}
.cad3-tree-node{display:grid;grid-template-columns:20px minmax(0,1fr);align-items:start;gap:0 4px}.cad3-tree-parent-row{grid-column:1/-1;display:grid;grid-template-columns:20px minmax(0,1fr);align-items:center;gap:4px}.cad3-tree-disclosure{display:grid;place-items:center;width:20px;height:38px;padding:0;border:1px solid transparent;border-radius:4px;background:transparent;color:#a5b0b6;font:500 16px/1 var(--ui-font);cursor:pointer}.cad3-tree-disclosure:hover{border-color:#51331f;border-radius:5px;background:#ffffff08;color:#ff9653}.cad3-tree-disclosure:disabled{visibility:hidden}.cad3-tree-parent-row .cad-tree-feature,.cad3-sketch-child{grid-template-columns:22px minmax(0,1fr) auto;gap:8px}.cad3-tree-parent-row .cad3-icon{color:#aeb9bf}.cad3-tree-children{grid-column:1/-1;display:grid;gap:4px;margin-left:24px;padding-left:9px;border-left:1px solid #32404a}.cad3-tree-children[hidden]{display:none!important}.cad3-sketch-child{color:#b8c3c9}.cad3-sketch-child .cad3-icon{color:#7ebdc5}.cad3-model-tabs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;overflow:hidden;padding:7px 8px 9px;border-bottom:1px solid #29363e}.cad3-model-tab{width:100%;min-width:0;overflow:hidden;padding:7px 6px;border:1px solid #33424b;border-radius:7px;background:#10171c;color:#b7c2c8;font:600 11px/1.2 var(--ui-font);text-overflow:ellipsis;white-space:nowrap;cursor:pointer}.cad3-model-tab:hover{border-color:#76502f;color:#fff}.cad3-model-tab[aria-selected=true]{border-color:#a95822;background:#321d10;color:#fff}.cad3-model-tree{display:grid;gap:3px;padding:8px 7px}.cad3-model-title{padding:7px 9px 5px;color:#e8eef1;font-size:12px;font-weight:700}.cad3-model-note{padding:3px 9px 8px;color:#9eabb2;font-size:11px;line-height:1.4}
.cad-tree-feature{min-height:38px;padding:7px 10px;border-radius:6px;font-size:12px}.cad-tree-feature:hover{background:#ffffff09;color:#eef2f4}.cad-tree-feature[data-selected=true]{border-color:#a34c1c!important;background:linear-gradient(100deg,#3d1d0e,#21140e)!important;box-shadow:inset 0 0 0 1px #713817;color:#fff}
.cad3-tree-type{margin-left:auto;color:#647078;font-size:8px}
/* Match the reference hierarchy while keeping tree controls compact and aligned. */
.cad-model-tree{box-sizing:border-box;width:100%;height:100%;margin:0;grid-template-rows:58px 54px minmax(0,1fr);overflow:hidden;border:0;border-radius:0;background:transparent}
.cad-tree-heading{padding:0 18px;background:#0d151a;border-bottom-color:#2b3942}.cad-tree-heading strong{font-size:17px}.cad-tree-collapse{width:36px;height:36px;border-radius:5px;font-size:18px}.cad-tree-collapse:hover{border-color:#633817;background:#24150d}
.cad-tree-search{padding:8px 12px 9px}.cad-tree-search input{height:38px;padding:0 12px;border:1px solid #33424b;border-radius:8px;background:#0a1116;font-size:12px}.cad-tree-search input:focus{border-color:#92501f;box-shadow:0 0 0 2px #ff74171c}.cad-tree-scroll{padding:8px 10px 18px}
.cad-tree-root,.cad-tree-static,.cad-tree-features{gap:5px}.cad-tree-root-row,.cad-tree-static-row{min-height:38px;padding:6px 9px;grid-template-columns:20px 22px minmax(0,1fr);gap:7px;font-size:12px}.cad-tree-root-row{color:#eef2f4}.cad-tree-root-row .cad-tree-chevron,.cad-tree-static-row .cad-tree-chevron{color:#c2cbd0;font-size:15px}.cad-tree-root-row .cad-tree-label{font-size:13px;font-weight:650}.cad-tree-root-row .cad-tree-icon,.cad-tree-static .cad-tree-icon{color:var(--accent)}.cad-tree-root-row .cad-tree-icon svg{width:20px;height:20px}.cad-tree-static{margin-left:20px;padding-left:8px;border-left-color:#32404a}.cad-tree-features{margin-top:8px;gap:5px}.cad-tree-icon svg,.cad3-icon svg{width:18px;height:18px}
.cad3-tree-node{display:grid;grid-template-columns:20px minmax(0,1fr);align-items:start;gap:0 4px}.cad3-tree-parent-row{grid-column:1/-1;display:grid;grid-template-columns:20px minmax(0,1fr);align-items:center;gap:4px}.cad3-tree-disclosure{display:grid;place-items:center;width:20px;height:38px;padding:0;border:1px solid transparent;border-radius:4px;background:transparent;color:#a5b0b6;font:500 16px/1 var(--ui-font);cursor:pointer}.cad3-tree-disclosure:hover{border-color:#51331f;border-radius:5px;background:#ffffff08;color:#ff9653}.cad3-tree-disclosure[aria-expanded=true] span{transform:none!important}.cad3-tree-parent-row .cad-tree-feature,.cad3-sketch-child{grid-template-columns:22px minmax(0,1fr) auto;gap:8px}.cad3-tree-children{grid-column:1/-1;display:grid;gap:4px;margin-left:24px;padding-left:9px;border-left:1px solid #32404a}.cad-tree-feature{min-height:38px;padding:7px 10px;border-radius:6px;font-size:12px}.cad-tree-feature:hover{background:#ffffff09;color:#eef2f4}.cad-tree-feature[data-selected=true]{border-color:#a34c1c!important;background:linear-gradient(100deg,#3d1d0e,#21140e)!important;box-shadow:inset 0 0 0 1px #713817;color:#fff}
/* Match the approved tree mockup at the actual desktop sidebar scale. */
.cad-model-tree{width:calc(100% - 8px);height:calc(100% - 8px);margin:4px;border-radius:6px;grid-template-rows:46px 42px minmax(0,1fr)}
.cad-tree-heading{padding:0 14px}.cad-tree-heading strong{font-size:16px;letter-spacing:0}.cad-tree-collapse{width:28px;height:28px}
.cad-tree-search{position:relative;padding:6px 8px}.cad-tree-search input{height:30px;padding:0 9px 0 32px;border-radius:6px}.cad-tree-search::before{position:absolute;z-index:1;top:calc(50% - 7px);left:18px;width:11px;height:11px;border:1.5px solid #aeb9bf;border-radius:50%;content:"";pointer-events:none}.cad-tree-search::after{position:absolute;z-index:1;top:calc(50% + 4px);left:29px;width:6px;height:1.5px;transform:rotate(45deg);transform-origin:left center;background:#aeb9bf;content:"";pointer-events:none}
.cad-tree-scroll{padding:4px 8px 12px}.cad-tree-root,.cad-tree-static,.cad-tree-features{gap:2px}.cad-tree-root-row,.cad-tree-static-row{min-height:32px;padding:4px 7px;grid-template-columns:18px 19px minmax(0,1fr);gap:5px;font-size:11px}.cad-tree-root-row .cad-tree-label{font-size:12px}.cad-tree-root-row .cad-tree-chevron,.cad-tree-static-row .cad-tree-chevron{font-size:14px}.cad-tree-root-row .cad-tree-icon svg{width:17px;height:17px}.cad-tree-static{margin-left:18px;padding-left:5px}.cad-tree-icon svg,.cad3-icon svg{width:16px;height:16px}.cad-tree-features{margin-top:5px}
.cad3-tree-node{grid-template-columns:22px minmax(0,1fr);gap:0 2px}.cad3-tree-parent-row{grid-template-columns:22px minmax(0,1fr);gap:2px}.cad3-tree-disclosure{width:22px;height:32px;color:#b6c0c6;font-size:18px}.cad3-tree-disclosure .cad-tree-chevron{font-size:18px;line-height:1}.cad3-tree-disclosure:disabled{visibility:visible;opacity:1;cursor:default}.cad3-tree-disclosure[aria-expanded=true] span{transform:none!important}.cad3-tree-disclosure span{transition:none}.cad3-tree-parent-row .cad-tree-feature{min-height:32px;padding:5px 8px;border-radius:5px;font-size:11px}.cad3-tree-children{margin-left:24px;padding-left:6px;gap:2px}.cad3-sketch-child{min-height:30px;padding:4px 8px;font-size:11px}.cad-tree-feature[data-selected=true]{border-color:#d45a16!important}
:root{--cadyx-rail-width:56px;--cadyx-tree-width:clamp(320px,24vw,400px);--cadyx-inspector-width:clamp(310px,21vw,372px)}
.topbar{padding-left:74px!important}.cad3-headrail{position:absolute;inset:0 auto 0 0;width:56px;display:grid;place-items:center;border-right:1px solid var(--line);background:#0a1014;color:var(--accent)}.cad3-headrail svg{width:21px}
.project-detail{grid-template-columns:56px var(--cadyx-tree-width) minmax(0,1fr)!important}.project-detail[data-tree-collapsed=true]{grid-template-columns:56px clamp(280px,20vw,360px) minmax(0,1fr)!important}.workspace-navigation{grid-column:2/-1!important;padding-left:18px!important}.section-sidebar{grid-column:2!important}.project-main{grid-column:3!important}
.cad3-rail{grid-column:1;grid-row:1/3;display:flex;flex-direction:column;align-items:center;gap:7px;padding:10px 7px;border-right:1px solid var(--line);background:#0a1014;z-index:9}.cad3-rail button{display:grid;place-items:center;width:40px;height:40px;border:1px solid transparent;border-radius:5px;background:transparent;color:#87939b}.cad3-rail button svg,.cad3-icon svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.cad3-rail button[data-on=true]{color:var(--accent);background:#351a0d}.cad3-rail button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}.cad3-rail button:disabled{opacity:.35;cursor:not-allowed}
.cad-tree-icon{font-size:0!important}.cad-tree-icon svg,.cad3-icon svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.6}.cad3-tree-type{margin-left:auto;color:#647078;font-size:8px}.cad-tree-feature[data-selected=true]{border-color:#773917!important;background:linear-gradient(90deg,#45200e,#21140d)!important}.cad3-props{grid-row:2;min-height:0;overflow:auto;color:var(--muted);font:10px/1.5 var(--mono)}.cad3-props[hidden]{display:none!important}.cad3-empty{padding:18px;color:var(--dim)}.cad3-head{display:grid;grid-template-columns:34px 1fr;gap:9px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line-soft)}.cad3-head .cad3-icon{display:grid;place-items:center;width:32px;height:32px;border:1px solid #62391f;border-radius:5px;color:var(--accent);background:#201209}.cad3-head strong{display:block;color:#eef2f4;font-size:12px}.cad3-head small{color:var(--dim);font-size:8px}.cad3-sec{padding:12px 14px 14px;border-bottom:1px solid var(--line-soft)}.cad3-sec h3{margin:0 0 9px;color:#e1e6e9;font-size:10px}.cad3-grid{display:grid;grid-template-columns:minmax(98px,.9fr) minmax(0,1.1fr);border:1px solid #252f36;border-radius:3px;overflow:hidden}.cad3-grid dt,.cad3-grid dd{min-height:30px;margin:0;padding:7px 9px;border-bottom:1px solid #222c33}.cad3-grid dt{color:#89949b;background:#0b1217}.cad3-grid dd{color:#d7dde0;border-left:1px solid #222c33;background:#0d1419;word-break:break-word}.cad3-grid input{width:100%;height:24px;border:1px solid #35414a;border-radius:3px;background:#080e12;color:#eef1f3;font:9px var(--mono)}.cad3-apply{width:100%;height:30px;margin-top:9px;border:1px solid #7c3e19;border-radius:4px;background:#291409;color:#ff9653;font:700 9px var(--mono)}.cad3-dep{display:flex;width:100%;align-items:center;gap:8px;padding:7px;border:1px solid transparent;background:transparent;color:#b9c1c6;text-align:left}.cad3-dep:hover{border-color:#56351f;background:#ff741708}.cad3-dep small{margin-left:auto;color:#68747c}.cad3-valid{color:#63d89d}
.primary-grid[data-view=overview] #object-panel{position:absolute!important;width:1px!important;height:1px!important;opacity:0!important;pointer-events:none!important}.cad3-overlay{position:absolute;z-index:6;inset:0;width:100%;height:100%;pointer-events:none}.project-detail[data-cad-tool=orbit] #model-preview{cursor:grab}.project-detail[data-v2-selection=true] .model-panel:after{display:none!important}.cad-v2-selection-chip{display:none!important}
.cad3-tool{display:flex;align-items:center;gap:6px;height:30px;padding:0 8px;border:1px solid transparent;border-radius:4px;background:transparent;color:#a8b3b9;font:9px var(--mono)}.cad3-tool svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.7}.cad3-tool[data-on=true]{border-color:#83431d;color:var(--accent);background:#3a1b0d}.cad3-tool:disabled{opacity:.35}
@media(max-width:920px){.primary-grid[data-view=overview]>.inspector-column{display:none!important}}
body:has(#primary-grid[data-view="projects"]) .topbar{grid-template-columns:auto minmax(0,1fr) auto!important;grid-template-areas:"brand project sync"!important;gap:0;padding-right:22px!important;border-bottom:0!important}
body:has(#primary-grid[data-view="projects"]) .topbar-project{justify-self:end;min-width:max-content;align-items:center;gap:12px;padding:0;border:0}

body:has(#primary-grid[data-view="projects"]) .topbar-project>.project-light,body:has(#primary-grid[data-view="projects"]) .topbar-project>.topbar-project-copy{display:none!important}
body:has(#primary-grid[data-view="projects"]) .project-detail{grid-template-columns:56px minmax(0,1fr)!important}
body:has(#primary-grid[data-view="projects"]) .section-sidebar{display:none!important}
body:has(#primary-grid[data-view="projects"]) .project-main{grid-column:2!important}
body:has(#primary-grid[data-view="files"]) .project-detail{grid-template-columns:56px minmax(0,1fr)!important}
body:has(#primary-grid[data-view="files"]) .section-sidebar{display:none!important}
body:has(#primary-grid[data-view="files"]) .project-main{grid-column:2!important}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation{position:fixed!important;z-index:24;top:0;right:0;left:192px;width:auto;height:58px;min-height:58px;align-items:stretch;gap:0;overflow-x:auto;overflow-y:hidden;padding:0;background:transparent;border:0;grid-column:auto!important;scrollbar-width:none}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-item{flex:0 0 auto;width:auto;min-width:0;height:58px;min-height:58px;justify-content:center;padding:0 18px;border:0;border-bottom:3px solid transparent;border-radius:0;background:transparent;text-decoration:none}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-copy strong{color:#c8d0d5;font:600 12px/1.2 var(--ui-font);letter-spacing:0;text-transform:none;white-space:nowrap}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-item:hover:not(:disabled) .nav-copy strong{color:#fff}
body:has(#primary-grid[data-view="projects"]) .project-detail{grid-template-rows:0 minmax(0,1fr) var(--cadyx-ai-height)!important}
body:has(#primary-grid[data-view="projects"]) .section-sidebar,body:has(#primary-grid[data-view="projects"]) .project-main{grid-row:2!important}
.brand{gap:9px}
.brand-mark{display:block;width:25px;height:25px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
#projects-panel{grid-template-rows:auto auto minmax(0,1fr) auto!important;border:0;border-radius:0!important;background:transparent}
#projects-panel:has(#projects-selection-toolbar[hidden]){grid-template-rows:auto minmax(0,1fr) auto!important}
.projects-page-heading{display:flex;min-width:0;align-items:end;justify-content:space-between;gap:22px;padding:24px 24px 14px}
.projects-title-block{display:grid;min-width:220px;gap:3px}
.projects-eyebrow{color:var(--muted);font:12px/1.3 var(--ui-font)}
.projects-title-block h2{display:flex;align-items:center;gap:14px;margin:0;color:#f2f5f6;font:700 34px/1.12 var(--ui-font);letter-spacing:-.025em}
.projects-count{display:inline-grid;min-width:32px;height:32px;place-items:center;padding:0 8px;border:1px solid var(--line);border-radius:9px;background:#151e25;color:var(--muted);font:600 14px var(--ui-font);letter-spacing:0}
.projects-toolbar{display:flex;min-width:0;align-items:center;justify-content:flex-end;gap:10px}
.projects-toolbar label:not(.visually-hidden){color:var(--muted);font:12px/1.2 var(--ui-font);letter-spacing:0;white-space:nowrap}
.projects-toolbar input{width:clamp(190px,22vw,315px);height:44px;min-width:0;padding:0 14px;border:1px solid #2e3b44;border-radius:9px;background:#101920;color:var(--text);font:13px var(--ui-font)}
.projects-toolbar input::placeholder{color:#8d9ba3}
.projects-toolbar input:focus{border-color:#70818d;outline:2px solid #71828b25;outline-offset:1px}
.projects-toolbar select{width:clamp(130px,12vw,165px);height:42px;max-width:none;padding:0 12px;border:1px solid #2e3b44;border-radius:9px;background:#101920;color:var(--text);font:12px var(--ui-font)}
.projects-layout-toggle{display:flex;align-items:center;gap:2px;padding:3px;border:1px solid #2e3b44;border-radius:9px;background:#101920}
.projects-layout-button{display:grid;width:38px;height:34px;place-items:center;padding:7px;border:1px solid transparent;border-radius:6px;background:transparent;color:var(--muted);cursor:pointer}
.projects-layout-button[aria-pressed="true"]{border-color:#8a461e;background:#3a1b0d;color:var(--accent);box-shadow:0 0 14px #ff741722}
.projects-layout-button svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.projects-create-group{height:42px;min-height:42px;padding:0 17px;border-color:#ff7628;border-radius:9px;background:linear-gradient(180deg,#ff7a2e,#f45d12);box-shadow:0 4px 16px #ff681d21;color:#1b120e;font:700 12px var(--ui-font);white-space:nowrap}
.projects-create-group:hover:not(:disabled){border-color:#ff9a5e;background:#ff8a43;color:#17100c}
.projects-list{align-content:start;grid-template-columns:minmax(0,1fr);gap:18px;overflow:auto;padding:6px 24px 24px}
.project-group{gap:11px}
.project-group-heading{gap:9px;padding:0 0 10px;border-bottom:1px solid #26333c;color:#c2ccd1;font:600 12px var(--ui-font);letter-spacing:0;text-transform:none}
.project-group-heading span{color:var(--dim);font:11px var(--ui-font)}
.project-group-cards{grid-template-columns:repeat(auto-fill,minmax(min(100%,360px),1fr));gap:16px}
.workspace-project-card{position:relative;grid-template-rows:208px auto auto auto;gap:13px;min-width:0;padding:13px;border:1px solid #26343e;border-radius:12px;background:linear-gradient(145deg,#121c23,#0f171d);box-shadow:0 8px 24px #0002;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
#projects-panel[data-layout="grid"] .workspace-project-card{aspect-ratio:1;grid-template-rows:minmax(0,1fr) auto auto auto;min-height:0;max-width:none;justify-self:stretch}
#projects-panel[data-layout="grid"] .workspace-project-thumbnail{height:auto;min-height:0;overflow:hidden}
.workspace-project-card:hover{border-color:#596b74;box-shadow:0 14px 32px #0004;transform:translateY(-1px)}
.workspace-project-card[data-active="true"]{border-color:#a85e32;background:linear-gradient(145deg,#201912,#12171b);box-shadow:0 0 0 1px #ff742718,0 10px 28px #0004}
.workspace-project-thumbnail{position:relative;min-height:0;height:208px;border:1px solid #26343d;border-radius:9px;background-color:#0a1217;background-image:linear-gradient(#26323838 1px,transparent 1px),linear-gradient(90deg,#26323838 1px,transparent 1px),radial-gradient(ellipse at 50% 78%,#1c39302b,transparent 58%);background-size:20px 20px,20px 20px,auto}
.workspace-project-thumbnail img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.workspace-project-thumbnail.unavailable::after{color:#aab5bb;content:"Предпросмотр недоступен";font:12px var(--ui-font);letter-spacing:0}
.workspace-project-state{position:absolute;top:10px;right:10px;align-self:auto;padding:6px 10px;border:1px solid #34444d;border-radius:8px;background:#101a20eF;color:#e2e9ec;font:600 11px var(--ui-font);letter-spacing:0;box-shadow:0 4px 12px #0004}
.workspace-project-state::before{display:inline-block;width:8px;height:8px;margin-right:7px;border-radius:50%;background:#64df83;box-shadow:0 0 8px #64df8355;content:""}
.workspace-project-card[data-active="true"] .workspace-project-state{border-color:#8e5732;color:#ffbd91}
.workspace-project-card[data-active="true"] .workspace-project-state::before{background:var(--accent);box-shadow:0 0 8px #ff874055}
.workspace-project-card-heading{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:9px;min-height:28px}
.workspace-project-card h3{color:#f0f3f4;font:650 16px/1.3 var(--ui-font);letter-spacing:-.01em;overflow-wrap:anywhere}
.workspace-project-menu{position:relative;z-index:2}
.workspace-project-menu summary,.workspace-project-folder-menu summary{display:grid;width:36px;height:36px;place-items:center;border:1px solid transparent;border-radius:8px;background:transparent;color:#aab7be;font:700 22px/1 var(--ui-font);list-style:none;cursor:pointer}
.workspace-project-menu summary::-webkit-details-marker,.workspace-project-folder-menu summary::-webkit-details-marker{display:none}
.workspace-project-menu summary:hover,.workspace-project-menu[open] summary,.workspace-project-folder-menu summary:hover,.workspace-project-folder-menu[open] summary{border-color:var(--line);background:#18232a;color:#fff}
.workspace-project-menu-popover{position:absolute;top:calc(100% + 5px);right:0;z-index:8;min-width:170px;padding:5px;border:1px solid #34434b;border-radius:9px;background:#111a20;box-shadow:0 12px 28px #0008}
.workspace-project-delete{width:100%;min-height:36px;padding:7px 10px;border:0;border-radius:6px;background:transparent;color:#f19b92;font:12px var(--ui-font);text-align:left;cursor:pointer}
.workspace-project-delete:hover:not(:disabled){background:#2a1718;color:#ffb1aa}
.workspace-project-delete:disabled{opacity:.43;cursor:not-allowed}
.workspace-project-meta{display:flex;min-height:32px;align-items:center;justify-content:flex-start;gap:12px;padding-top:9px;border-top:1px solid #26333b;color:#aebbc2;font:12px var(--ui-font)}
.workspace-project-modified{display:inline-flex;min-width:0;align-items:center;gap:9px;white-space:nowrap}
.workspace-project-modified svg{width:17px;height:17px;flex:0 0 auto;fill:none;stroke:#9fb0ba;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.workspace-project-id{overflow:hidden;padding-left:12px;border-left:1px solid #40505a;color:#9dabb3;text-overflow:ellipsis;white-space:nowrap}
.workspace-project-id strong{color:#c7d0d5;font:12px var(--ui-font);letter-spacing:.02em}
.workspace-project-actions{display:flex;align-items:stretch;gap:8px}
.workspace-project-open{display:flex;min-width:0;min-height:42px;flex:1;justify-content:center;gap:10px;padding:8px 12px;border-color:#ff7628;border-radius:9px;background:linear-gradient(180deg,#ff7a2e,#f45d12);box-shadow:0 4px 15px #ff681d20;color:#1b120e;font:700 12px var(--ui-font);white-space:nowrap}
.workspace-project-open:hover:not(:disabled){border-color:#ff9a5e;background:#ff8a43;color:#17100c}
.workspace-project-open-new-tab{border-color:#34434b;background:#111a20;box-shadow:none;color:#c4cdd2}
.workspace-project-open-new-tab:hover:not(:disabled){border-color:#596b74;background:#18232a;color:#fff}
.workspace-project-open svg{width:18px;height:18px;flex:0 0 auto;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.workspace-project-open:focus-visible,.workspace-project-folder-menu summary:focus-visible,.workspace-project-menu summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.workspace-project-folder-menu{position:relative;z-index:2;flex:0 0 auto}
.workspace-project-folder-menu summary{width:42px;height:42px;border-color:#34434b;border-radius:9px;background:#111a20}
.workspace-project-folder-menu summary svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.workspace-project-folder-popover{position:absolute;right:0;bottom:calc(100% + 7px);z-index:8;display:grid;width:220px;gap:7px;padding:11px;border:1px solid #34434b;border-radius:9px;background:#111a20;box-shadow:0 12px 28px #0008}
.workspace-project-folder-popover label{color:var(--muted);font:12px var(--ui-font)}
.workspace-project-group-select{width:100%;min-height:38px;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:#0b1217;color:var(--text);font:12px var(--ui-font)}
.workspace-project-group-select:disabled{opacity:.43;cursor:not-allowed}
#projects-panel[data-layout="list"] .project-group-cards{grid-template-columns:minmax(0,1fr)}
#projects-panel[data-layout="list"] .workspace-project-card{grid-template-columns:minmax(190px,260px) minmax(0,1fr) auto;grid-template-rows:auto auto minmax(40px,1fr);align-items:center;gap:8px 16px;padding:12px}
#projects-panel[data-layout="list"] .workspace-project-thumbnail{grid-column:1;grid-row:1/4;height:150px}
#projects-panel[data-layout="list"] .workspace-project-card-heading{grid-column:2;grid-row:1;align-self:end}
#projects-panel[data-layout="list"] .workspace-project-meta{grid-column:2;grid-row:2;align-self:start}
#projects-panel[data-layout="list"] .workspace-project-actions{grid-column:3;grid-row:1/4;min-width:190px;flex-direction:column}
#projects-panel[data-layout="list"] .workspace-project-folder-menu summary{display:flex;width:100%;height:36px;justify-content:center;gap:8px}
#projects-panel[data-layout="list"] .workspace-project-folder-menu summary::after{content:"Папка";font:12px var(--ui-font)}
.projects-empty{align-self:center;justify-self:center;margin:0;padding:28px;color:var(--muted);font:14px var(--ui-font);text-align:center}
.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1380px){.projects-page-heading{align-items:flex-start;flex-direction:column}.projects-toolbar{width:100%;justify-content:flex-start}.projects-toolbar input{flex:1}}
@media(max-width:1040px){body:has(#primary-grid[data-view="projects"]) #workspace-navigation{left:192px}body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-item{padding:0 11px}.projects-toolbar{flex-wrap:wrap}}
@media(max-width:760px){.projects-page-heading{padding:18px 14px 10px}.projects-list{padding:6px 14px 16px}.projects-toolbar select{width:145px}.projects-create-group{margin-left:auto}#projects-panel[data-layout="list"] .workspace-project-card{grid-template-columns:minmax(110px,34%) minmax(0,1fr);grid-template-rows:auto auto auto}.primary-grid[data-view="projects"] #projects-panel[data-layout="list"] .workspace-project-thumbnail{grid-column:1;grid-row:1/4;height:140px}.primary-grid[data-view="projects"] #projects-panel[data-layout="list"] .workspace-project-card-heading,.primary-grid[data-view="projects"] #projects-panel[data-layout="list"] .workspace-project-meta{grid-column:2}.primary-grid[data-view="projects"] #projects-panel[data-layout="list"] .workspace-project-actions{grid-column:2;grid-row:3;min-width:0;flex-direction:row}}
 .projects-toolbar input{font:12px var(--ui-font)}.project-group-heading span{font-size:12px}
 .cad-tree-root-row .cad-tree-label{font-size:12px}.cad3-tree-type{font-size:10px}
 .cad3-model-tab{font:600 12px/1.2 var(--ui-font)}.cad3-props{font:11px/1.45 var(--ui-font)}.cad3-head small,.cad3-sec h3,.cad3-dep small{font-size:11px}
 .cad3-grid input,.cad3-apply{font:600 11px/1.2 var(--ui-font)}
 .model-toolbar .cad3-tool,.model-toolbar>.button{font:600 12px/1.2 var(--ui-font)}
 @media(max-width:680px){:root{--cadyx-rail-width:48px;--cadyx-tree-width:52px}.project-detail{grid-template-columns:48px 52px minmax(0,1fr)!important}.project-detail[data-tree-collapsed=true]{grid-template-columns:48px 48px minmax(0,1fr)!important}.cad3-rail{gap:4px;padding:8px 4px}.cad3-rail button{width:36px;height:36px}}
`;
    style.textContent += '.cad3-model-tabs[hidden]{display:none!important}.cad3-tree-node>.cad3-model-note{grid-column:1/-1;padding-left:32px}.cad3-scan-file{overflow-wrap:anywhere;padding-left:32px!important}.cad3-scan-file-button{width:100%;border:0;border-radius:4px;background:transparent;color:var(--muted);font:11px/1.4 var(--ui-font);text-align:left;cursor:pointer}.cad3-scan-file-button:hover{background:#ffffff08;color:var(--text)}.cad3-scan-file-button:focus-visible{outline:1px solid #ff8740;outline-offset:1px}.cad3-scan-file-button[aria-pressed="true"]{background:#ffffff0c;color:#ffe7d6;box-shadow:inset 2px 0 #ff8740}.cad3-scan-run-row{grid-template-columns:20px minmax(0,1fr) 28px}.cad3-scan-delete{display:grid;place-items:center;width:26px;height:30px;padding:0;border:1px solid transparent;border-radius:5px;background:transparent;color:#f07171;cursor:pointer}.cad3-scan-delete:hover:not(:disabled){border-color:#713131;background:#351919;color:#ff8888}.cad3-scan-delete:disabled{color:#805454;cursor:not-allowed;opacity:.65}.cad3-scan-delete svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-linecap:round;stroke-linejoin:round;stroke-width:1.8}.cad3-cloud-root-row{display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;gap:2px}.cad3-cloud-disclosure{display:grid;place-items:center;width:22px;height:32px;padding:0;border:1px solid transparent;border-radius:4px;background:transparent;color:#b6c0c6;font:500 18px/1 var(--ui-font);cursor:pointer}.cad3-cloud-disclosure:hover{border-color:#51331f;background:#ffffff08;color:#ff9653}.cad3-cloud-root{min-height:34px;grid-template-columns:18px minmax(0,1fr)!important;font-weight:650;color:#e8eef1}.cad3-cloud-stages{display:grid;gap:2px;margin:2px 0 3px 24px;padding-left:6px;border-left:1px solid #32404a}.cad3-cloud-stages[hidden]{display:none!important}.cad3-cloud-stages .cad3-tree-node{grid-template-columns:minmax(0,1fr);gap:0}.cad3-tree-leaf-row{grid-column:1/-1;min-width:0}.cad3-tree-leaf-row>.cad-tree-feature{width:100%;min-height:32px;padding:5px 8px;grid-template-columns:18px minmax(0,1fr) auto;gap:6px;font-size:11px}.cad3-cloud-stages .cad3-model-note{padding:2px 8px 5px}';
    style.textContent += '.cad3-tree-leaf-row>.cad-tree-feature{grid-template-columns:16px 22px minmax(0,1fr);gap:8px}.cad3-tree-leaf-row>.cad-tree-feature::before{content:""}';
    style.textContent += '.cad3-tree-parent-row{align-items:center}.cad3-tree-parent-row>.cad-tree-feature,.cad3-tree-disclosure{align-self:center}.cad-tree-feature>.cad3-icon{display:grid;place-items:center;align-self:center;line-height:0}.cad-tree-feature>.cad-tree-label{align-self:center;line-height:1.2}';
    style.textContent += '.cad3-assembly-tree{display:grid;gap:2px;padding:6px 0 10px;margin-bottom:7px;border-bottom:1px solid #28343b}.cad3-assembly-row{display:flex;align-items:center;gap:3px;padding-left:calc(var(--assembly-depth,0)*11px)}.cad3-assembly-select{flex:1;min-width:0}.cad3-assembly-joint-count{min-width:34px;color:#8e9aa1;font-size:9px}.cad3-assembly-state{width:25px;height:25px;padding:0;border:1px solid #334047;border-radius:5px;background:#171f23;color:#c6d0d4;cursor:pointer}.cad3-assembly-state:hover:not(:disabled){border-color:#b75c2b;color:#ffb27e}.cad3-assembly-state:disabled{opacity:.45;cursor:not-allowed}.cad3-assembly-state[data-active="true"]{border-color:#9e4c24;color:#ffb27e;background:#422413}';
    style.textContent += '.cad3-gizmo-root{position:absolute;z-index:7;inset:0;pointer-events:none}.cad3-gizmo-root[hidden]{display:none}.cad3-gizmo-root button{position:absolute;width:30px;height:30px;margin:-15px 0 0 -15px;border:1px solid #4e5c63;border-radius:50%;background:#10181de8;color:#e8f0f2;font:700 11px var(--ui-font);pointer-events:auto;touch-action:none;cursor:grab;box-shadow:0 2px 8px #0008}.cad3-gizmo-root button[data-gizmo-kind="translate"][data-axis="X"]{border-color:#f06c68;color:#ff928c}.cad3-gizmo-root button[data-gizmo-kind="translate"][data-axis="Y"]{border-color:#72c990;color:#91e0a9}.cad3-gizmo-root button[data-gizmo-kind="translate"][data-axis="Z"]{border-color:#70a8ff;color:#91baff}.cad3-gizmo-root button[data-gizmo-kind="rotate"]{border-radius:8px;width:34px;height:24px;margin:0;font-size:10px}.cad3-gizmo-root button:active{cursor:grabbing;background:#342116}';
    document.head.appendChild(style);
    if(!$('.cad3-headrail')){const h=document.createElement('div');h.className='cad3-headrail';h.innerHTML=I.model;$('.topbar')?.prepend(h)}
    if(!$('.cad3-rail')){const r=document.createElement('nav');r.className='cad3-rail';r.setAttribute('aria-label','Основная навигация');r.innerHTML=`<button data-view="geometry" title="Геометрия">${I.model}</button><button data-view="files" title="Файлы">${I.files}</button><button data-view="projects" title="Проекты">${I.projects}</button><button data-view="activity" title="Активность">${I.activity}</button>`;detail.prepend(r);const syncRail=()=>{$$('[data-view]',r).forEach(b=>{const view=b.dataset.view,src=nav.querySelector(`[data-workspace-view="${view}"]`),active=view===(grid.dataset.view||'overview');b.setAttribute('aria-label',b.title);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');b.dataset.on=String(active);b.disabled=!src||src.disabled})};r.addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(!b||b.disabled)return;nav.querySelector(`[data-workspace-view="${b.dataset.view}"]`)?.click()});new MutationObserver(syncRail).observe(grid,{attributes:true,attributeFilter:['data-view']});new MutationObserver(syncRail).observe(nav,{subtree:true,attributes:true,attributeFilter:['disabled','aria-current']});syncRail()}

    const oldList=$('#cad-tree-features'),oldSearch=$('.cad-tree-search input');let list=oldList,search=oldSearch,modelTabs;if(oldList){modelTabs=document.createElement('div');modelTabs.className='cad3-model-tabs';modelTabs.setAttribute('role','tablist');modelTabs.setAttribute('aria-label','Модели');oldList.before(modelTabs);list=oldList.cloneNode(false);list.classList.add('cad3-model-tree');oldList.replaceWith(list)}if(oldSearch){search=oldSearch.cloneNode(true);oldSearch.replaceWith(search)}
    let props=$('.cad3-props');if(!props){props=document.createElement('section');props.className='cad3-props cad-inspector-pane';inspector.append(props)}
    const overlay=document.createElement('canvas');overlay.className='cad3-overlay';panel.append(overlay);
    const gizmoRoot=document.createElement('div');gizmoRoot.className='cad3-gizmo-root';gizmoRoot.hidden=true;
    for(const axis of ['X','Y','Z']){const move=document.createElement('button');move.type='button';move.dataset.gizmoKind='translate';move.dataset.axis=axis;move.title=`Переместить по ${axis}`;move.textContent=axis;gizmoRoot.append(move);const rotate=document.createElement('button');rotate.type='button';rotate.dataset.gizmoKind='rotate';rotate.dataset.axis=axis;rotate.title=`Повернуть вокруг ${axis}`;rotate.textContent=`↻${axis}`;gizmoRoot.append(rotate)}
    panel.append(gizmoRoot);
    const state={objects:[],byId:new Map(),scene:null,labels:new Map(),selected:null,selectedInstanceId:null,selectedInstancePath:[],selectedOccurrenceId:null,selectedOccurrenceIds:new Set(),assemblyFacePickMode:false,assemblyPickKind:'face',assemblyFacePicks:[],selectionCleared:false,rev:null,project:null,parameters:{},bindings:[],assembly:null,lock:null,kinematics:null,assemblyCoordinateMode:'local',assemblyStep:1,assemblyAngleStep:5,snapEnabled:true,activeModelBodyId:null};
    const assemblyUndo=[],assemblyRedo=[];let assemblyLastRevision=null;
    const assemblyTopologyCache=new Map();
    let assemblyDrag=null;
    const opType=o=>o?.operation==='recognize_mesh'?'Опорная геометрия сетки':cadyxMeshStage(o)?.title||cadyxCloudStage(o||{})?.title||meta[o?.operation]?.[0]||(o?.operation?`Операция: ${String(o.operation).replace(/_/g,' ')}`:'Элемент'), opIcon=o=>cadyxCloudStage(o||{})?I.cloud:I[meta[o?.operation]?.[1]||'model'];
    function featureDetails(o){const p=o.parameters||{},fmt=v=>Number.isFinite(Number(v))?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(v)):null,mm=v=>{const n=fmt(v);return n===null?null:`${n} мм`},pos=Array.isArray(p.origin)?p.origin.slice(0,2).map((v,i)=>`${i?'Y':'X'}${fmt(v)}`).filter(x=>!x.endsWith('null')).join(', '):'';let d=[];switch(o.operation){case'create_box':d=[mm(p.width),mm(p.height),mm(p.depth)].filter(Boolean);return d.length?d.join(' × '):'';case'extrude_sketch':case'pad_sketch_to_body':case'rib_from_sketch':case'pocket_sketch':case'extrude_profile_on_plane':d=[mm(p.depth)];break;case'revolve_sketch':d=[p.angle_degrees==null?null:`${fmt(p.angle_degrees)}°`];break;case'boss':d=[p.diameter==null?null:`Ø${mm(p.diameter)}`,mm(p.depth)];break;case'hole':case'through_hole':case'counterbore_hole':case'countersink_hole':case'threaded_hole':d=[p.diameter==null?null:`Ø${mm(p.diameter)}`,p.hole_depth==null?null:mm(p.hole_depth),p.thread_size||null];break;case'fillet_edges':d=[p.radius==null?null:`R${mm(p.radius)}`];break;case'variable_fillet_edges':d=[p.start_radius==null?null:`R${mm(p.start_radius)}`,p.end_radius==null?null:`R${mm(p.end_radius)}`];break;case'chamfer_edges':d=[mm(p.distance)];break;case'shell_body':case'offset_body':d=[mm(p.thickness??p.offset)];break;case'draft_faces':d=[p.angle_degrees==null?null:`${fmt(p.angle_degrees)}°`];break;case'linear_pattern_body':case'circular_pattern_body':d=[p.count==null?null:`${fmt(p.count)} шт.`];break;default:return''}if(pos)d.push(pos);return d.filter(Boolean).join(' · ')}
    function makeLabels(){state.labels.clear();const count={sketch:0};const ops={};for(const o of state.objects){if(o.type==='sketch')state.labels.set(o.id,o.name&&o.name!=='Sketch'?o.name:`Эскиз ${String(++count.sketch).padStart(3,'0')}`);if(o.type==='feature'){const base=opType(o);ops[base]=(ops[base]||0)+1;const details=featureDetails(o);state.labels.set(o.id,o.name||`${base} ${String(ops[base]).padStart(3,'0')}${details?` · ${details}`:''}`)}}}
    const label=id=>state.labels.get(id)||state.byId.get(id)?.name||id?.split(':')[0]||'—';
    function consumed(){const s=new Set;for(const f of state.objects.filter(x=>x.type==='feature')){if(['fuse_bodies','cut_bodies'].includes(f.operation)){s.add(f.base_body_id);s.add(f.tool_body_id)}if(['fillet_edges','chamfer_edges','resize_imported_thread','transform_imported_brep'].includes(f.operation))s.add(f.source_body_id)}return s}
    const sceneBody=id=>state.scene?.objects?.find(x=>x.object_id===id);
    const assemblyInstances=()=>state.assembly?.instances||[];
    const assemblyInstance=id=>assemblyInstances().find(item=>item.id===id)||null;
    const assemblyTargetName=id=>state.assembly?.parts?.find(item=>item.id===id)?.name
      ||state.assembly?.subassemblies?.find(item=>item.id===id)?.name||id?.split(':')[0]||'Деталь';
    const occurrenceScene=id=>state.scene?.objects?.find(item=>item.type==='body'&&item.object_id===id&&item.instance_id)||null;
    function deps(o){if(!o)return[];const d=[];const add=(id,role)=>{if(id&&state.byId.has(id))d.push({id,role})};add(o.source_sketch_id,'Профиль');(o.source_sketch_ids||[]).forEach(id=>add(id,'Сечение'));add(o.source_body_id,'Исходное тело');add(o.base_body_id,'Основное тело');add(o.tool_body_id,'Инструментальное тело');for(const x of state.objects){if(x===o||x.type!=='feature')continue;if(x.source_sketch_id===o.id||(x.source_sketch_ids||[]).includes(o.id)||x.source_body_id===o.body_id||x.base_body_id===o.body_id||x.tool_body_id===o.body_id)add(x.id,'Используется в')}return d}
    const expandedFeatures=new Set();
    const collapsedClouds=new Set();
    const scanTreeExpanded=new Set();
    let scanTreeEntries=[];
    let scanTreeLoadedProject=null;
    let scanTreeRequest=0;
    let scanTreePreviewSelection=null;
    let scanTreePreviewRequest=0;
    let scanTreePreviewRestoreMode=null;
    let latestScanPreview=null;
    function saveModelTreeState(){
      const projectId=state.project;
      if(!projectId)return;
      try{localStorage.setItem(`cadyx-model-tree:${projectId}`,JSON.stringify({collapsedClouds:[...collapsedClouds],expandedFeatures:[...expandedFeatures]}))}catch{}
    }
    function restoreModelTreeState(projectId){
      collapsedClouds.clear();expandedFeatures.clear();
      try{const saved=JSON.parse(localStorage.getItem(`cadyx-model-tree:${projectId}`)||'{}');
        const bodyIds=new Set(state.objects.filter(o=>o.type==='body').map(o=>o.id));
        const featureIds=new Set(state.objects.filter(o=>o.type==='feature').map(o=>o.id));
        for(const id of Array.isArray(saved.collapsedClouds)?saved.collapsedClouds:[])if(bodyIds.has(id))collapsedClouds.add(id);
        for(const id of Array.isArray(saved.expandedFeatures)?saved.expandedFeatures:[])if(featureIds.has(id))expandedFeatures.add(id);
      }catch{}
    }
    const hasAssemblyStructure=()=>Boolean(state.assembly&&((state.assembly.instances||[]).length||(state.assembly.subassemblies||[]).length));
    function modelGroups(){
      const features=state.objects.filter(o=>o.type==='feature'),byBody=new Map(),consumed=new Set();
      for(const feature of features){if(!byBody.has(feature.body_id))byBody.set(feature.body_id,[]);byBody.get(feature.body_id).push(feature)}
      for(const feature of features)for(const id of [feature.source_body_id,feature.base_body_id,feature.tool_body_id])if(id)consumed.add(id);
      const roots=state.objects.filter(body=>body.type==='body'&&!consumed.has(body.id)&&byBody.has(body.id));
      return roots.map(body=>{const included=new Set();const visit=id=>{if(!id||included.has(id))return;included.add(id);for(const feature of byBody.get(id)||[]){visit(feature.source_body_id);visit(feature.base_body_id);visit(feature.tool_body_id)}};visit(body.id);return{body,features:features.filter(feature=>included.has(feature.body_id))}});
    }
    const scanRunPattern=/^revopoint_\d{8}T\d{6}Z$/i;
    const scanFileRunId=name=>/^(revopoint_\d{8}T\d{6}Z)_/i.exec(name)?.[1]||null;
    function scanRunTitle(runId){
      const match=/^revopoint_(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/i.exec(runId);
      if(!match)return runId;
      const [,year,month,day,hour,minute,second]=match;
      const date=new Date(Date.UTC(+year,+month-1,+day,+hour,+minute,+second));
      return `Сканирование · ${new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'medium'}).format(date)}`;
    }
    function scanFileSize(size){
      if(size<1024)return `${size} Б`;
      if(size<1024*1024)return `${new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(size/1024)} КБ`;
      return `${new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(size/(1024*1024))} МБ`;
    }
    function scanFileLabel(file,runId){
      return `${file.name.startsWith(`${runId}_`)?file.name.slice(runId.length+1):file.name} · ${scanFileSize(file.size)}`;
    }
    async function loadScanRunFiles(run,projectId){
      run.loading=!(run.files||[]).length;run.loadError=null;
      if(run.loading)renderScanTree();
      try{
        const payload=await semanticJson(`/api/v1/projects/${encodeURIComponent(projectId)}/folders/${encodeURIComponent(run.folderName)}/files`);
        if(state.project!==projectId)return;
        run.files=[...new Map([...run.files,...(payload.files||[]).filter(file=>!file.name.toLowerCase().endsWith('.json'))].map(file=>[file.name,file])).values()];
        run.folders=payload.folders||[];run.filesLoaded=true;
      }catch(error){run.loadError=error.message}
      finally{run.loading=false;if(state.project===projectId)renderScanTree()}
    }
    async function loadScanTree(projectId,force=false){
      const request=++scanTreeRequest;
      if(!projectId){scanTreeEntries=[];scanTreeLoadedProject=null;renderScanTree();return}
      const sameProject=scanTreeLoadedProject===projectId;
      if(sameProject&&!force)return;
      scanTreeLoadedProject=projectId;
      const previousRuns=new Map(sameProject?scanTreeEntries.map(run=>[run.id,run]):[]);
      if(!force){scanTreeEntries=[];renderScanTree('Загружаю серии сканирования…')}
      try{
        const root=await semanticJson(`/api/v1/projects/${encodeURIComponent(projectId)}/files`);
        const runs=new Map();
        for(const folder of root.folders||[]){
          if(!scanRunPattern.test(folder.name))continue;
          const previous=previousRuns.get(folder.name);
          runs.set(folder.name,previous?{...previous,modifiedAt:folder.modified_at_ns||''}:{id:folder.name,folderName:folder.name,files:[],rootFiles:[],filesLoaded:false,folders:[],modifiedAt:folder.modified_at_ns||''});
        }
        for(const file of root.files||[]){
          const id=scanFileRunId(file.name);if(!id)continue;
          if(!runs.has(id)){const previous=previousRuns.get(id);runs.set(id,previous?{...previous,folderName:null}:{id,folderName:null,files:[],rootFiles:[],filesLoaded:true,folders:[],modifiedAt:''})}
          runs.get(id).files.push(file);runs.get(id).rootFiles.push(file);
        }
        if(request!==scanTreeRequest||state.project!==projectId)return;
        scanTreeEntries=[...runs.values()].map(run=>({...run,files:[...new Map(run.files.map(file=>[file.name,file])).values()].sort((a,b)=>a.name.localeCompare(b.name)),rootFiles:[...new Map((run.rootFiles||[]).map(file=>[file.name,file])).values()]}))
          .sort((a,b)=>b.id.localeCompare(a.id));
        const saved=JSON.parse(localStorage.getItem(`cadyx-scan-tree:${projectId}`)||'[]');
        scanTreeExpanded.clear();
        if(Array.isArray(saved))for(const id of saved)if(scanTreeEntries.some(run=>run.id===id))scanTreeExpanded.add(id);
        renderScanTree();
        await Promise.all(scanTreeEntries.filter(run=>scanTreeExpanded.has(run.id)&&run.folderName).map(run=>loadScanRunFiles(run,projectId)));
      }catch(error){
        if(request!==scanTreeRequest||state.project!==projectId)return;
        scanTreeLoadedProject=null;
        renderScanTree(`Не удалось загрузить дерево сканирования (${error.message}).`);
      }
    }
    function renderScanTree(message=null){
      if(!list)return;
      list.replaceChildren();
      const query=(search?.value||'').toLowerCase().trim();
      let shown=0;
      for(const run of scanTreeEntries){
        const title=scanRunTitle(run.id);
        const files=(run.files||[]).filter(file=>/\.ply$/i.test(file.name));
        const matchingFiles=files.filter(file=>!query||file.name.toLowerCase().includes(query));
        if(query&&!title.toLowerCase().includes(query)&&!matchingFiles.length)continue;
        const container=document.createElement('div');container.className='cad3-tree-node';
        const row=document.createElement('div');row.className='cad3-tree-parent-row cad3-scan-run-row';
        const expanded=scanTreeExpanded.has(run.id)||Boolean(query&&matchingFiles.length);
        const childrenId=`scan-run-${run.id}`;
        const disclosure=document.createElement('button');disclosure.type='button';disclosure.className='cad3-tree-disclosure';
        disclosure.setAttribute('aria-label',expanded?'Свернуть серию сканирования':'Показать файлы серии сканирования');
        disclosure.setAttribute('aria-expanded',String(expanded));disclosure.setAttribute('aria-controls',childrenId);disclosure.innerHTML=`<span class="cad-tree-chevron">${expanded?'−':'+'}</span>`;
        disclosure.onclick=async()=>{
          const projectId=state.project;
          if(scanTreeExpanded.has(run.id)){scanTreeExpanded.delete(run.id);renderScanTree()}
          else{
            scanTreeExpanded.add(run.id);renderScanTree();
            if(run.folderName)await loadScanRunFiles(run,projectId);
          }
          try{localStorage.setItem(`cadyx-scan-tree:${state.project}`,JSON.stringify([...scanTreeExpanded]))}catch{}
          renderScanTree();
        };
        const button=document.createElement('button');button.className='cad-tree-feature';button.type='button';button.title=title;
        button.setAttribute('aria-expanded',String(expanded));button.setAttribute('aria-controls',childrenId);button.onclick=()=>disclosure.click();
        button.innerHTML=`<span class="cad3-icon">${I.cloud}</span><span class="cad-tree-label"></span><small>${run.filesLoaded?`${files.length} облаков`:'Открыть серию'}</small>`;
        button.querySelector('.cad-tree-label').textContent=title;
        const remove=document.createElement('button');remove.type='button';remove.className='cad3-scan-delete';remove.setAttribute('aria-label',`Удалить серию ${title}`);
        const canDelete=typeof canOpenFilesView==='function'&&canOpenFilesView(state.project);
        remove.title=canDelete?`Удалить серию «${title}»`:'Удаление доступно, когда проект свободен и состояние подтверждено';
        remove.disabled=!canDelete;
        remove.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-.8 13H6.8L6 7m4 3v7m4-7v7"/></svg>';
        remove.onclick=event=>{event.stopPropagation();deleteScanRun(run)};
        row.append(disclosure,button,remove);container.append(row);
        const children=document.createElement('div');children.id=childrenId;children.className='cad3-tree-children';children.hidden=!expanded;
        if(run.loading){const leaf=document.createElement('div');leaf.className='cad3-model-note cad3-scan-file';leaf.textContent='Загружаю файлы серии…';children.append(leaf)}
        else if(run.loadError){const leaf=document.createElement('div');leaf.className='cad3-model-note cad3-scan-file';leaf.textContent=`Не удалось загрузить файлы серии (${run.loadError}).`;children.append(leaf)}
        for(const file of matchingFiles){
          const leaf=document.createElement('button');leaf.type='button';leaf.className='cad3-model-note cad3-scan-file cad3-scan-file-button';
          leaf.setAttribute('aria-pressed',String(scanTreePreviewSelection===`${run.id}/${file.name}`));
          leaf.textContent=scanFileLabel(file,run.id);leaf.title=`Показать облако точек · ${file.name}`;leaf.setAttribute('aria-label',leaf.title);
          leaf.onclick=()=>previewScanFile(run,file,leaf);children.append(leaf);
        }
        if(expanded&&!run.loading&&!run.loadError&&!files.length){const leaf=document.createElement('div');leaf.className='cad3-model-note cad3-scan-file';leaf.textContent=run.folderName?'В серии нет облаков точек для предпросмотра.':'В серии нет облаков точек для предпросмотра.';children.append(leaf)}
        container.append(children);list.append(container);shown++;
      }
      const empty=$('#cad-tree-empty');
      if(empty){empty.hidden=shown>0;empty.textContent=message||(!state.project?'Выберите проект, чтобы увидеть серии сканирования.':query?'Совпадений нет.':'В проекте пока нет сохранённых серий сканирования.');}
    }
    function activeScanPreviewMode(){
      if(liveCamera?.getAttribute('aria-pressed')==='true')return 'live';
      if(scanMesh?.getAttribute('aria-pressed')==='true')return 'mesh';
      if(liveScan?.getAttribute('aria-pressed')==='true')return 'cloud';
      if(latestScanPreview?.projectId===state.project)return 'capture';
      return null;
    }
    function clearScanTreePreview({restorePrevious=false,preserveMode=false}={}){
      const restoreMode=scanTreePreviewRestoreMode;
      let restored=false;
      scanTreePreviewSelection=null;scanTreePreviewRequest++;
      document.querySelectorAll('.cad3-scan-file-button[aria-pressed="true"]').forEach(button=>button.setAttribute('aria-pressed','false'));
      if(scanViewport)delete scanViewport.dataset.scanTreePreview;
      scanView?.setOpen(false);scanView?.closeLivePreview();
      modelViewer?.load(currentModelViewportGeometry,{reset:false});
      if(restorePrevious&&grid?.dataset.view==='scanning'){
        if(restoreMode==='live'&&scanView){scanView.showLivePreview({open:true});restored=true}
        else if((restoreMode==='cloud'||restoreMode==='mesh')&&scanView){scanView.setMode(restoreMode);restored=true}
        else if(restoreMode==='capture'&&latestScanPreview?.projectId===state.project){
          scanView?.showCapture(latestScanPreview.scan,{open:true});restored=Boolean(scanView);
        }
      }
      if(!restored&&grid?.dataset.view==='scanning')scanView?.showTurntable();
      if(!preserveMode)scanTreePreviewRestoreMode=null;
      return restored;
    }
    async function previewScanFile(run,file,button){
      const selection=`${run.id}/${file.name}`;
      const status=$('#scanning-status');
      if(scanTreePreviewSelection===selection){const restored=clearScanTreePreview({restorePrevious:true});if(status)status.textContent=restored?'Предыдущий предпросмотр восстановлен':'Готово к съёмке';return}
      const projectId=state.project;
      if(!projectId){notify('Сначала выберите проект.',true);return}
      if(!scanTreePreviewSelection)scanTreePreviewRestoreMode=activeScanPreviewMode();
      clearScanTreePreview({preserveMode:true});
      scanTreePreviewSelection=selection;
      if(scanViewport)scanViewport.dataset.scanTreePreview='true';
      button?.setAttribute('aria-pressed','true');
      const request=++scanTreePreviewRequest;
      const filesPath=run.folderName
        ?`/api/v1/projects/${encodeURIComponent(projectId)}/folders/${encodeURIComponent(run.folderName)}/files`
        :`/api/v1/projects/${encodeURIComponent(projectId)}/files`;
      if(status)status.textContent=`Загружаю ${scanFileLabel(file,run.id)}…`;
      try{
        const response=await fetch(`${filesPath}/${encodeURIComponent(file.name)}`,{cache:'no-store'});
        if(!response.ok)throw new Error(`Не удалось открыть облако (HTTP ${response.status}).`);
        if(!window.CadyxPlyPreview?.parseStream)throw new Error('Предпросмотр PLY недоступен.');
        const geometry=await window.CadyxPlyPreview.parseStream(response.body,{
          maxPreviewPoints:125_000,totalBytes:Number(file.size)||undefined,
        });
        if(request!==scanTreePreviewRequest||projectId!==state.project)return;
        const pointCount=geometry.previewVertexCount??geometry.positions.length/3;
        const referenceOrigin=geometry.bounds.min.map((value,axis)=>(value+geometry.bounds.max[axis])/2);
        modelViewer?.load({...geometry,triangles:new Uint32Array(0),lines:[],
          pointCloudVertexCount:pointCount,referenceOrigin},{reset:true});
        if(status)status.textContent=`Предпросмотр ${scanFileLabel(file,run.id)} · ${new Intl.NumberFormat('ru-RU').format(pointCount)} точек`;
      }catch(error){
        if(request!==scanTreePreviewRequest)return;
        clearScanTreePreview({restorePrevious:true});
        if(status)status.textContent=error.message;
      }
    }
    async function deleteScanRun(run){
      const projectId=state.project;
      const title=scanRunTitle(run.id);
      if(!projectId||typeof canOpenFilesView!=='function'||!canOpenFilesView(projectId)){
        notify('Удаление доступно, когда проект свободен и состояние подтверждено.',true);return;
      }
      if(run.folderName&&(run.rootFiles||[]).length){
        notify('Серия хранится одновременно в папке и в корне проекта. Удаление остановлено, чтобы не оставить часть серии.',true);return;
      }
      const contents=run.folderName?'Папка серии и все файлы внутри неё будут удалены.':'Все файлы этой серии будут удалены из проекта.';
      if(!window.confirm(`Удалить серию «${title}»?\n\n${contents}\nЭто действие нельзя отменить.`))return;
      try{
        if(run.folderName){
          await api(`/api/v1/agent/projects/${encodeURIComponent(projectId)}/folders/${encodeURIComponent(run.folderName)}/delete`,{method:'POST',body:JSON.stringify({})});
        }else{
          const filenames=(run.rootFiles||run.files||[]).map(file=>file.name);
          if(!filenames.length)throw new Error('В серии не найдено файлов.');
          await api(`/api/v1/agent/projects/${encodeURIComponent(projectId)}/files/delete`,{method:'POST',body:JSON.stringify({filenames})});
        }
        if(scanTreePreviewSelection?.startsWith(`${run.id}/`))clearScanTreePreview({restorePrevious:true});
        scanTreeEntries=scanTreeEntries.filter(item=>item.id!==run.id);
        scanTreeExpanded.delete(run.id);
        try{localStorage.setItem(`cadyx-scan-tree:${projectId}`,JSON.stringify([...scanTreeExpanded]))}catch{}
        renderScanTree();
        notify(`Серия «${title}» удалена.`);
      }catch(error){notify(`Не удалось удалить серию «${title}»: ${error.message}`,true)}
    }
    function renderAssemblyTree(){
      if(!hasAssemblyStructure())return;
      const section=document.createElement('section');section.className='cad3-assembly-tree';
      const heading=document.createElement('div');heading.className='cad3-model-title';heading.textContent='Экземпляры сборки';section.append(heading);
      const children=new Map();for(const instance of assemblyInstances()){const key=instance.parent_id||null;if(!children.has(key))children.set(key,[]);children.get(key).push(instance)}
      for(const items of children.values())items.sort((a,b)=>a.name.localeCompare(b.name,'ru'));
      const mates=state.assembly?.mates||[],canEdit=state.lock?.state==='FREE'&&!state.lock?.recovery_required;
      let rendered=0;
      const addInstance=(instance,path,depth)=>{
        if(rendered>=4096)return;rendered++;
        const fullPath=[...path,instance.id],pathId=fullPath.join('/'),row=document.createElement('div');
        row.className='cad3-assembly-row';row.style.setProperty('--assembly-depth',String(Math.min(depth,16)));
        const button=document.createElement('button');button.type='button';button.className='cad-tree-feature cad3-assembly-select';
        button.dataset.selected=String(state.selectedOccurrenceIds.has(pathId));button.dataset.id=pathId;
        button.title=`${instance.name} · ${assemblyTargetName(instance.target_id)}`;
        button.innerHTML='<span class="cad3-icon">▰</span><span class="cad-tree-label"></span>';
        button.querySelector('.cad-tree-label').textContent=instance.name;
        button.onclick=event=>selectAssemblyOccurrence(pathId,instance.id,event);
        const linked=mates.filter(mate=>mate.a_instance_id===instance.id||mate.b_instance_id===instance.id).length;
        const joint=document.createElement('small');joint.className='cad3-assembly-joint-count';joint.textContent=linked?`${linked} сопр.`:'';
        const visibility=document.createElement('button');visibility.type='button';visibility.className='cad3-assembly-state';
        visibility.textContent=instance.visible?'◉':'○';visibility.title=instance.visible?'Скрыть экземпляр':'Показать экземпляр';
        visibility.setAttribute('aria-label',visibility.title);visibility.disabled=!canEdit;
        visibility.onclick=event=>{event.stopPropagation();void editAssemblyProperties(instance,{visible:!instance.visible})};
        const grounded=document.createElement('button');grounded.type='button';grounded.className='cad3-assembly-state';
        grounded.textContent=instance.grounded?'⚓':'◇';grounded.title=instance.grounded?'Снять закрепление':'Закрепить экземпляр';
        grounded.setAttribute('aria-label',grounded.title);grounded.disabled=!canEdit;
        grounded.onclick=event=>{event.stopPropagation();void editAssemblyProperties(instance,{grounded:!instance.grounded})};
        row.append(button,joint,visibility,grounded);section.append(row);
        for(const child of children.get(instance.target_id)||[])addInstance(child,fullPath,depth+1);
      };
      for(const instance of children.get(null)||[])addInstance(instance,[],0);
      if(rendered>=4096){const note=document.createElement('div');note.className='cad3-model-note';note.textContent='Дерево ограничено 4096 экземплярами.';section.append(note)}
      list.append(section);
    }
    function renderTree(){
      if(!list)return;
      const scanning=grid?.dataset.view==='scanning';
      const treeTitle=$('#cad-tree-title');
      if(treeTitle)treeTitle.textContent=scanning?'Дерево сканирования':'Дерево модели';
      const treeCollapse=$('.cad-tree-collapse');
      if(treeCollapse){const verb=$('.section-sidebar')?.dataset.collapsed==='true'?'Развернуть':'Свернуть';const label=`${verb} дерево ${scanning?'сканирования':'модели'}`;treeCollapse.title=label;treeCollapse.setAttribute('aria-label',label)}
      if(search){search.placeholder=scanning?'Поиск по сериям и файлам…':'Поиск по элементам…';search.setAttribute('aria-label',scanning?'Поиск по дереву сканирования':'Поиск по дереву модели')}
      if(scanning){modelTabs?.setAttribute('hidden','');if(scanTreeLoadedProject!==state.project)loadScanTree(state.project);else renderScanTree();return}
      modelTabs?.removeAttribute('hidden');
      list.replaceChildren();
      renderAssemblyTree();
      const query=(search?.value||'').toLowerCase().trim(),groups=modelGroups();
      if(!groups.some(group=>group.body.id===state.activeModelBodyId))state.activeModelBodyId=groups[0]?.body.id||null;
      const modelSelectorVisible=hasAssemblyStructure()&&groups.length>1;
      if(modelTabs)modelTabs.hidden=!modelSelectorVisible;
      modelTabs?.replaceChildren();
      if(modelSelectorVisible)for(const group of groups){const tab=document.createElement('button');tab.type='button';tab.className='cad3-model-tab';tab.setAttribute('role','tab');tab.dataset.bodyId=group.body.id;tab.setAttribute('aria-selected',String(group.body.id===state.activeModelBodyId));tab.title=group.body.name;tab.textContent=group.body.name;tab.onclick=()=>{state.activeModelBodyId=group.body.id;renderTree()};modelTabs?.append(tab)}
      const active=groups.find(group=>group.body.id===state.activeModelBodyId);
      const visibleGroups=modelSelectorVisible?(active?[active]:[]):groups;
      for(const group of visibleGroups){
        if(modelSelectorVisible||visibleGroups.length>1){const modelTitle=document.createElement('div');modelTitle.className='cad3-model-title';modelTitle.textContent=group.body.name;list.append(modelTitle)}
        const features=group.features;
        const cloudGroup=features.some(feature=>cadyxCloudStage(feature));
        let featureHost=list;
        if(cloudGroup){
          const container=document.createElement('div');container.className='cad3-cloud-group';
          const row=document.createElement('div');row.className='cad3-cloud-root-row';
          const expanded=!collapsedClouds.has(group.body.id);
          const disclosure=document.createElement('button');disclosure.type='button';disclosure.className='cad3-cloud-disclosure';
          disclosure.setAttribute('aria-label',expanded?'Свернуть облако точек':'Развернуть облако точек');disclosure.setAttribute('aria-expanded',String(expanded));
          disclosure.innerHTML=`<span class="cad-tree-chevron">${expanded?'−':'+'}</span>`;
          disclosure.onclick=()=>{if(collapsedClouds.has(group.body.id))collapsedClouds.delete(group.body.id);else collapsedClouds.add(group.body.id);saveModelTreeState();renderTree()};
          const root=document.createElement('button');root.className='cad-tree-feature cad3-cloud-root';root.type='button';root.dataset.id=group.body.id;
          root.dataset.selected=String(group.body.id===state.selected);root.title=group.body.name;root.innerHTML=`<span class="cad3-icon">${I.cloud}</span><span class="cad-tree-label">Облако точек</span>`;root.onclick=()=>select(group.body.id);
          row.append(disclosure,root);container.append(row);
          const stages=document.createElement('div');stages.className='cad3-cloud-stages';stages.hidden=!expanded;container.append(stages);
          list.append(container);featureHost=stages;
        }
        for(const feature of features){
          const sketchIds=[...new Set([feature.source_sketch_id,...(feature.source_sketch_ids||[])].filter(id=>state.byId.get(id)?.type==='sketch'))];
          const sketchObjects=sketchIds.map(id=>state.byId.get(id));
          const featureName=label(feature.id),matchesFeature=!query||featureName.toLowerCase().includes(query)||group.body.name.toLowerCase().includes(query);
          const matchingSketches=sketchObjects.filter(sk=>!query||label(sk.id).toLowerCase().includes(query));
          if(!matchesFeature&&matchingSketches.length===0)continue;
          const node=document.createElement('div');node.className='cad3-tree-node';
          const row=document.createElement('div');row.className=sketchObjects.length?'cad3-tree-parent-row':'cad3-tree-leaf-row';
          const expanded=expandedFeatures.has(feature.id)||(!matchesFeature&&matchingSketches.length>0);
          let disclosure=null;
          if(sketchObjects.length){disclosure=document.createElement('button');disclosure.type='button';disclosure.className='cad3-tree-disclosure';disclosure.setAttribute('aria-label',expanded?'Свернуть исходные эскизы':'Показать исходные эскизы');disclosure.setAttribute('aria-expanded',String(expanded));disclosure.innerHTML=`<span class="cad-tree-chevron">${expanded?'−':'+'}</span>`;
            disclosure.onclick=()=>{if(expandedFeatures.has(feature.id))expandedFeatures.delete(feature.id);else expandedFeatures.add(feature.id);saveModelTreeState();renderTree()};}
          const button=document.createElement('button');button.className='cad-tree-feature';button.type='button';button.title=featureName;button.dataset.id=feature.id;button.dataset.selected=String(feature.id===state.selected);button.innerHTML=`<span class="cad3-icon">${opIcon(feature)}</span><span class="cad-tree-label">${esc(featureName)}</span>`;button.onclick=()=>select(feature.id);
          if(disclosure)row.append(disclosure);row.append(button);node.append(row);
          if(feature.operation==='recognize_mesh'){const note=document.createElement('div');note.className='cad3-model-note';note.textContent=`Плоскости: ${feature.report?.planes?.length||0} · сечения: ${feature.report?.sections?.length||0} · проекции: ${feature.report?.projections?.length||0} · окружности: ${feature.report?.circles?.length||0}`;node.append(note)}
          const cloudStage=cadyxMeshStage(feature)||cadyxCloudStage(feature);
          if(cloudStage){const summary=document.createElement('div');summary.className='cad3-model-note';const unit=feature.operation==='process_mesh'?'треугольников':'точек';summary.textContent=cloudStage.input==null?`${fmt(cloudStage.output)} ${unit}`:`${fmt(cloudStage.input)} → ${fmt(cloudStage.output)} ${unit}`;node.append(summary)}
          if(sketchObjects.length){const children=document.createElement('div');children.className='cad3-tree-children';children.hidden=!expanded;
            for(const sketch of sketchObjects){if(query&&!matchesFeature&&!matchingSketches.includes(sketch))continue;const child=document.createElement('button');child.className='cad-tree-feature cad3-sketch-child';child.type='button';child.dataset.id=sketch.id;child.dataset.selected=String(sketch.id===state.selected);child.innerHTML=`<span class="cad3-icon">${I.sketch}</span><span class="cad-tree-label">${esc(label(sketch.id))}</span>`;child.onclick=()=>select(sketch.id,true);children.append(child)}
            node.append(children);
          }
          featureHost.append(node);
        }
        const hasSketches=features.some(feature=>feature.source_sketch_id||feature.source_sketch_ids?.length);
        if(!hasSketches&&!features.some(feature=>cadyxCloudStage(feature))){const note=document.createElement('div');note.className='cad3-model-note';note.textContent=features.some(feature=>feature.operation==='reconstruct_scan')?'Редактируемого эскиза нет: сетка построена по данным сканирования.':'Редактируемого эскиза нет.';list.append(note)}
      }
      const empty=$('#cad-tree-empty');if(empty)empty.hidden=hasAssemblyStructure()||visibleGroups.some(group=>group.features.length>0);
    }
    search?.addEventListener('input',renderTree);
    setInterval(()=>{if(document.visibilityState!=='hidden'&&grid?.dataset.view==='scanning'&&state.project)loadScanTree(state.project,true)},5000);
    new MutationObserver(()=>{scanTreeLoadedProject=null;renderTree()}).observe(grid,{attributes:true,attributeFilter:['data-view']});
    function parameterHtml(o){const entries=Object.entries(o.parameters||{}).filter(([k])=>k!=='section_sketch_ids'&&k!=='section_sketch_groups');if(!entries.length)return'<dt>Параметры</dt><dd>Нет</dd>';return entries.map(([k,v])=>{const edit=(editable[o.operation]||[]).includes(k)&&typeof v==='number';const unit=k.includes('angle')?'°':typeof v==='number'?'мм':'';return`<dt>${esc(pLabel[k]||k)}</dt><dd>${edit?`<input data-p="${k}" data-original="${v}" value="${v}"> ${unit}`:esc(Array.isArray(v)?v.join(', '):v)}</dd>`}).join('')}
    function modelParameterSection(){const entries=Object.entries(state.parameters||{});if(!entries.length)return'';const rows=entries.map(([name,record])=>{const expression=state.bindings.find(item=>(item.expression.match(/[A-Za-z_][A-Za-z0-9_]*/g)||[]).includes(name));return`<dt title="${esc(expression?.expression||'')} ">${esc(name)}</dt><dd><input data-model-parameter="true" data-parameter="${esc(name)}" data-original-value="${Number(record.value)}" value="${Number(record.value)}"> ${esc(record.unit||'мм')}</dd>`}).join('');return`<section class="cad3-sec cad3-model-parameters"><h3>Параметры модели</h3><dl class="cad3-grid">${rows}</dl><button class="cad3-apply-model">ПЕРЕСТРОИТЬ МОДЕЛЬ</button></section>`}
    function applyModelParameterEdits(){const vals=[...props.querySelectorAll('input[data-model-parameter]')].filter(i=>Number.isFinite(Number(i.value))&&Number(i.value)!==Number(i.dataset.originalValue));if(!vals.length)return;const fields=document.createElement('div');for(const input of vals){const copy=document.createElement('input');copy.dataset.modelParameter='true';copy.dataset.parameter=input.dataset.parameter;copy.dataset.originalValue=input.dataset.originalValue;copy.value=input.value;fields.append(copy)}fields.append(document.createElement('button'));return applyGeometryDimensions(null,fields)}
    function renderProps(o){if(!o){props.innerHTML='<div class="cad3-empty">Выберите элемент в дереве модели.</div>';if(modelParameterSection())props.insertAdjacentHTML('afterbegin',modelParameterSection());props.querySelector('.cad3-apply-model')?.addEventListener('click',applyModelParameterEdits);return}if(o.type==='sketch'){props.innerHTML=`<div class="cad3-head"><span class="cad3-icon">${I.sketch}</span><div><strong>${esc(label(o.id))}</strong><small>Эскиз</small></div></div><section class="cad3-sec"><h3>Общие сведения</h3><dl class="cad3-grid"><dt>Плоскость</dt><dd>${esc(o.plane||'—')}</dd><dt>Состояние</dt><dd><span class="cad3-valid">● Корректно</span></dd></dl></section>`;props.insertAdjacentHTML('afterbegin',modelParameterSection());props.querySelector('.cad3-apply-model')?.addEventListener('click',applyModelParameterEdits);return}const body=state.byId.get(o.body_id),mesh=sceneBody(o.body_id),m=o.source_metrics||{},sk=state.byId.get(o.source_sketch_id);const general=`<dt>Тип элемента</dt><dd>${esc(opType(o))}</dd><dt>Операция</dt><dd>${esc(opType(o))}</dd><dt>Состояние</dt><dd><span class="cad3-valid">● Корректно</span></dd><dt>Тело</dt><dd>${esc(body?.name||o.body_id||'—')}</dd>${sk?`<dt>Исходный эскиз</dt><dd>${esc(label(sk.id))}</dd><dt>Направление</dt><dd>Перпендикулярно плоскости ${esc(sk.plane||'эскиза')}</dd>`:''}`;const result=`<dt>Вершины</dt><dd>${fmt((mesh?.positions?.length||0)/3)}</dd><dt>Грани</dt><dd>${fmt((mesh?.triangles?.length||0)/3)}</dd>${Number.isFinite(m.volume)?`<dt>Объём</dt><dd>${fmt(m.volume)} мм³</dd>`:''}${Number.isFinite(m.surface_area)?`<dt>Площадь поверхности</dt><dd>${fmt(m.surface_area)} мм²</dd>`:''}`;const dependency=deps(o);props.innerHTML=`<div class="cad3-head"><span class="cad3-icon">${opIcon(o)}</span><div><strong>${esc(label(o.id))}</strong><small>${esc(opType(o))}</small></div></div><section class="cad3-sec"><h3>Общие сведения</h3><dl class="cad3-grid">${general}</dl></section><section class="cad3-sec"><h3>Параметры</h3><dl class="cad3-grid">${parameterHtml(o)}</dl>${(editable[o.operation]||[]).some(k=>typeof o.parameters?.[k]==='number')?'<button class="cad3-apply">ПРИМЕНИТЬ ПАРАМЕТРЫ</button>':''}</section><section class="cad3-sec"><h3>Результат</h3><dl class="cad3-grid">${result}</dl></section><section class="cad3-sec"><h3>Зависимости</h3>${dependency.length?dependency.map(d=>`<button class="cad3-dep" data-dep="${d.id}"><span>${esc(label(d.id))}</span><small>${esc(d.role)}</small></button>`).join(''):'<div class="cad3-empty">Нет зависимостей.</div>'}</section>`;props.insertAdjacentHTML('afterbegin',modelParameterSection());props.querySelector('.cad3-apply-model')?.addEventListener('click',applyModelParameterEdits);props.querySelector('.cad3-apply')?.addEventListener('click',async()=>{const vals=[...props.querySelectorAll('input[data-p]')].map(i=>[i.dataset.p,{original:+i.dataset.original,next:+i.value}]).filter(([,v])=>Number.isFinite(v.next)&&v.next!==v.original);if(!vals.length)return;const fields=document.createElement('div');for(const [name,v] of vals){const i=document.createElement('input');i.dataset.parameter=name;i.dataset.originalValue=String(v.original);i.value=String(v.next);fields.append(i)}fields.append(document.createElement('button'));try{await applyGeometryDimensions(o.id,fields)}catch(e){console.error(e)}})}
    const renderGeometryProps = renderProps;
    const massDensityByBody = new Map();
    const massMaterials = { pla: 1.24, petg: 1.27, abs: 1.04, asa: 1.07, aluminum: 2.70, steel: 7.80, stainless: 8.00, copper: 8.94 };
    renderProps = selected => {
      renderGeometryProps(selected);
      if(selected?.operation==='recognize_mesh'){
        const report=selected.report||{},groups=[['Плоскости','planes'],['Сечения','sections'],['Замкнутые контуры проекции','projections'],['Опорные окружности','circles'],['Предполагаемые оси цилиндров','cylinder_candidates']];
        const cards=groups.map(([title,key])=>`<h3>${title} · ${(report[key]||[]).length}</h3>${(report[key]||[]).map(record=>{const metrics=Object.entries(record).filter(([key,value])=>!['frame','bounds','support_faces','sketch_id','center'].includes(key)&&!Array.isArray(value)&&typeof value!=='object');return `<button class="cad3-dep" data-dep="${esc(record.sketch_id)}">${esc(label(record.sketch_id))}</button><dl class="cad3-grid">${metrics.map(([key,value])=>`<dt>${esc(({rms:'СКО подгонки',max_error:'Максимальная ошибка',radius:'Радиус',area:'Площадь опоры',coverage_degrees:'Угол измеренной дуги, °',support_count:'Опорных точек',sample_count:'Выборка',closed:'Контур замкнут',length:'Длина контура',point_count:'Точек контура',offset:'Смещение сечения',simplify_tolerance:'Допуск упрощения',join_tolerance:'Допуск стыковки',axis:'Ось сечений',status:'Статус',supported_length:'Длина опоры',max_section_error:'Ошибка сечений'})[key]||key)}</dt><dd>${esc(typeof value==='number'?fmt(value):value===true?'Да':value===false?'Нет':value==='candidate'?'Требует проверки':value)}</dd>`).join('')}</dl>`}).join('')}`).join('');
        props.insertAdjacentHTML('beforeend',`<section class="cad3-sec"><h3>Основа для твердотельной модели</h3><p>Измеренные опоры и редактируемые сечения. Исходная сетка сохранена; номинальные размеры не назначены.</p>${cards}<h3>Требует проверки</h3><ul>${(report.unresolved||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul><p>Отброшено ветвящихся контуров: ${report.branched_sections_rejected||0}</p></section>`);
      }
      if(selected?.recognition_role){
        const owner=state.objects.find(o=>o.operation==='recognize_mesh'&&o.source_sketch_ids?.includes(selected.id));
        const record=['planes','sections','projections','circles','cylinder_candidates'].flatMap(key=>owner?.report?.[key]||[]).find(item=>item.sketch_id===selected.id)||{};
        const frame=selected.frame||{},rows=[['Начало XYZ',frame.origin?.map(fmt).join(', ')],['Нормаль',frame.normal?.map(fmt).join(', ')],['Ось X плоскости',frame.x_axis?.map(fmt).join(', ')],['СКО исходной подгонки',record.rms],['Максимальная ошибка подгонки',record.max_error],['Шаг проекции',record.grid_spacing],['Проверка профиля',record.profile_validation?.solid_probe==='pass'?'Пробное выдавливание прошло':record.profile_validation?'Контур проверен':'' ]];
        props.insertAdjacentHTML('beforeend',`<section class="cad3-sec"><h3>Опорная геометрия по сетке</h3><dl class="cad3-grid">${rows.filter(([,value])=>value!=null&&value!=='').map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl><p>${selected.recognition_role==='projection'?'Проекция внешнего контура, не сечение. ':''}Масштаб не проверен. Погрешности относятся к исходной подгонке; правки эскиза не переизмеряют сетку.</p></section>`);
      }
      if(selected?.operation==='process_mesh'){
        const stats=selected.stats||{},before=stats.before||{},after=stats.after||{};
        const rows=[['Треугольники',`${before.triangles} → ${after.triangles}`],['Фрагменты',`${before.components} → ${after.components}`],['Открытые рёбра',`${before.boundary_edges} → ${after.boundary_edges}`],['Неманифолдные рёбра',after.nonmanifold_edges],['Максимальное смещение',stats.max_vertex_displacement],['Результат PLY',selected.artifact?.file],['Самопересечения','Не проверены']];
        props.insertAdjacentHTML('beforeend',`<section class="cad3-sec"><h3>Обработка сетки</h3><dl class="cad3-grid">${rows.map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl><button class="cad3-dep" data-dep="${esc(selected.previous_feature_id)}">Предыдущая операция</button></section>`);
      }
      const stage=selected&&cadyxCloudStage(selected);
      if(stage){
        const stats=selected.stats||{},source=selected.source?.project_file||selected.source?.file,artifact=selected.artifact?.file;
        const entries=[['Точек на входе',stage.input??stage.output],['Точек на выходе',stage.output],['Удалено точек',stats.removed_points??0],['Перемещено точек',stats.moved_points],['Исходник',source],['Результат PLY',artifact],['Масштаб','Не проверен']].filter(([,value])=>value!=null);
        props.insertAdjacentHTML('beforeend',`<section class="cad3-sec"><h3>История обработки облака</h3><dl class="cad3-grid">${entries.map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>${selected.previous_feature_id?`<button class="cad3-dep" data-dep="${esc(selected.previous_feature_id)}">Предыдущая операция</button>`:''}</section>`);
      }
      document.querySelector('#object-panel .cad3-mass-calculator')?.remove();
      if (!selected || selected.type === 'sketch') return;
      const body = selected.type === 'body' ? selected : state.byId.get(selected.body_id);
      const geometry = body?.geometry || selected.geometry;
      if (geometry?.kind === 'triangle_mesh') return;
      const volume = [geometry?.volume, selected.source_metrics?.volume]
        .find(value => typeof value === 'number' && Number.isFinite(value) && value > 0);
      if (volume === undefined) return;

      const section = document.createElement('section');
      section.className = 'cad3-sec cad3-mass-calculator';
      section.innerHTML = '<h3>Вес детали</h3><dl class="cad3-grid"><dt>Материал</dt><dd><select data-mass-material aria-label="Материал детали"><option value="">Выберите материал</option><option value="pla">PLA · 1,24 г/см³</option><option value="petg">PETG · 1,27 г/см³</option><option value="abs">ABS · 1,04 г/см³</option><option value="asa">ASA · 1,07 г/см³</option><option value="aluminum">Алюминий · 2,70 г/см³</option><option value="steel">Сталь · 7,80 г/см³</option><option value="stainless">Нержавеющая сталь · 8,00 г/см³</option><option value="copper">Медь · 8,94 г/см³</option><option value="custom">Другая плотность</option></select></dd><dt>Плотность, г/см³</dt><dd><input data-mass-density type="number" min="0" step="any" inputmode="decimal" placeholder="Введите плотность" aria-label="Плотность материала, граммов на кубический сантиметр" disabled></dd><dt>Расчётная масса</dt><dd data-mass-result>Выберите материал</dd></dl><small class="cad3-mass-note">Оценка для цельного тела. Заполнение при 3D-печати не учитывается.</small>';
      const visibleGeometryPanel = document.querySelector('#object-panel .geometry-table-scroll');
      (visibleGeometryPanel || props).prepend(section);

      const materialSelect = section.querySelector('[data-mass-material]');
      const densityInput = section.querySelector('[data-mass-density]');
      const massResult = section.querySelector('[data-mass-result]');
      const bodyId = body?.id || selected.body_id || selected.id;
      const savedMaterial = massDensityByBody.get(bodyId);
      if (savedMaterial) {
        materialSelect.value = savedMaterial.material;
        densityInput.value = String(savedMaterial.density);
        densityInput.disabled = savedMaterial.material !== 'custom';
      }
      const updateMass = () => {
        const density = Number(densityInput.value);
        if (!densityInput.value || !Number.isFinite(density) || density <= 0) {
          massDensityByBody.delete(bodyId);
          massResult.textContent = materialSelect.value === 'custom' ? 'Укажите плотность' : 'Выберите материал';
          return;
        }
        massDensityByBody.set(bodyId, { material: materialSelect.value, density });
        const grams = volume * density / 1000;
        massResult.textContent = grams < 1000
          ? `${fmt(grams)} г`
          : `${fmt(grams / 1000)} кг`;
      };
      materialSelect.addEventListener('change', () => {
        if (materialSelect.value === 'custom') {
          densityInput.disabled = false;
          densityInput.value = '';
        } else if (massMaterials[materialSelect.value]) {
          densityInput.value = String(massMaterials[materialSelect.value]);
          densityInput.disabled = true;
        } else {
          densityInput.value = '';
          densityInput.disabled = true;
        }
        updateMass();
      });
      densityInput.addEventListener('input', updateMass);
      if (savedMaterial) updateMass();
    };
    const setAssemblyStatus=message=>{const node=props.querySelector('[data-assembly-status]');if(node)node.textContent=message};
    function renderAssemblyProps(){
      const instance=assemblyInstance(state.selectedInstanceId);
      if(!instance){props.innerHTML='<div class="cad3-empty">Выберите экземпляр сборки в дереве или 3D-предпросмотре.</div>';return}
      const selectedCount=state.selectedOccurrenceIds.size;
      const mates=(state.assembly?.mates||[]).filter(mate=>mate.a_instance_id===instance.id||mate.b_instance_id===instance.id);
      const canEdit=state.lock?.state==='FREE'&&!state.lock?.recovery_required;
      const lockLabel=state.lock?.state==='AI_LOCKED'?'ИИ редактирует сборку':state.lock?.state==='HUMAN_LOCKED'?'Сборка редактируется':state.lock?.state==='SYSTEM_LOCKED'?'Core выполняет операцию':state.lock?.state==='FREE'&&!state.lock?.recovery_required?'Готово к редактированию':'Доступность не подтверждена';
      const kinematics=state.kinematics||{},mode=state.assemblyCoordinateMode,displayPose=selectedPose(instance,mode),translation=displayPose.translation,rotation=displayPose.rotation;
      const solverStatus=({fully_mated:'Полностью определена',underconstrained:'Есть свободные степени свободы',overconstrained:'Есть избыточные зависимости'})[kinematics.status]||'Не рассчитан';
      const row=(name,value,axis)=>`<dt>${name}</dt><dd><input data-assembly-value="${axis}" type="number" step="any" value="${Number(value)}"></dd>`;
      const facePicks=state.assemblyFacePicks,faceInstances=facePicks.map(pick=>assemblyInstance(pick.instance_id));
      const canCreateMate=facePicks.length===2&&facePicks[0].instance_id!==facePicks[1].instance_id
        &&faceInstances[0]?.parent_id===faceInstances[1]?.parent_id&&canEdit;
      const mateEditor=mate=>{
        const angleKind=['revolute','coaxial','cylindrical','angle'].includes(mate.kind),offsetKind=['slider','coaxial','cylindrical','distance'].includes(mate.kind),orientationKind=['spherical','coincident'].includes(mate.kind),limits=mate.limits||{};
        const limitInput=(field,label,value)=>`<dt>${label}</dt><dd><input data-mate-limit="${mate.id}:${field}" type="number" step="any" value="${value==null?'':Number(value)}" ${canEdit?'':'disabled'}></dd>`;
        const quaternionInput=(index,value)=>`<dt>Q${['w','x','y','z'][index]}</dt><dd><input data-mate-orientation="${mate.id}:${index}" type="number" step="any" value="${Number(value)}" ${canEdit?'':'disabled'}></dd>`;
        return `<div class="cad3-assembly-mate"><strong>${esc(mate.name)} · ${esc(mate.kind)}</strong><dl class="cad3-grid">${angleKind?`<dt>Угол, °</dt><dd><input data-mate-angle="${mate.id}" type="number" step="any" value="${Number(mate.angle_deg)}" ${canEdit?'':'disabled'}></dd>${limitInput('angle_min_deg','Мин. угол, °',limits.angle_min_deg)}${limitInput('angle_max_deg','Макс. угол, °',limits.angle_max_deg)}`:''}${offsetKind?`<dt>Смещение, мм</dt><dd><input data-mate-offset="${mate.id}" type="number" step="any" value="${Number(mate.offset_mm)}" ${canEdit?'':'disabled'}></dd>${limitInput('offset_min_mm','Мин. смещение, мм',limits.offset_min_mm)}${limitInput('offset_max_mm','Макс. смещение, мм',limits.offset_max_mm)}`:''}${orientationKind?(mate.orientation||[1,0,0,0]).map((value,index)=>quaternionInput(index,value)).join(''):''}</dl><div class="cad3-assembly-actions"><button type="button" data-mate-apply="${mate.id}" ${canEdit?'':'disabled'}>Применить</button><button type="button" data-mate-remove="${mate.id}" ${canEdit?'':'disabled'}>Удалить сопряжение</button></div><small>Геометрические ссылки: ${esc([mate.reference_a,mate.reference_b].filter(Boolean).map(reference=>reference.entity_key).join(' · ')||'нет')}</small></div>`;
      };
      props.innerHTML=`<div class="cad3-head"><span class="cad3-icon">▰</span><div><strong>${esc(instance.name)}</strong><small>${esc(assemblyTargetName(instance.target_id))}</small></div></div>
        <section class="cad3-sec"><h3>Состояние сборки</h3><dl class="cad3-grid"><dt>Редактор</dt><dd data-assembly-lock>${esc(lockLabel)}</dd><dt>Решатель</dt><dd>${esc(solverStatus)}</dd><dt>Степени свободы</dt><dd>${fmt(kinematics.global_dof??0)} глоб. · ${fmt(kinematics.internal_dof??0)} относ.</dd><dt>Избыточные</dt><dd>${fmt(kinematics.redundant_mate_ids?.length??0)}</dd><dt>Сопряжения</dt><dd>${mates.length}</dd><dt>Выделено</dt><dd>${selectedCount}</dd></dl></section>
        <section class="cad3-sec"><h3>Экземпляр</h3><label class="cad3-assembly-check"><input type="checkbox" data-assembly-visible ${instance.visible?'checked':''} ${canEdit?'':'disabled'}> Видимый</label><label class="cad3-assembly-check"><input type="checkbox" data-assembly-grounded ${instance.grounded?'checked':''} ${canEdit?'':'disabled'}> Закреплён</label></section>
        <section class="cad3-sec"><h3>Зависимость по геометрии</h3><label class="cad3-assembly-check">Выбирать <select data-assembly-pick-kind ${canEdit?'':'disabled'}><option value="face" ${state.assemblyPickKind==='face'?'selected':''}>Грани</option><option value="vertex" ${state.assemblyPickKind==='vertex'?'selected':''}>Вершины</option></select></label><button type="button" data-assembly-face-mode ${canEdit?'':'disabled'}>${state.assemblyFacePickMode?'Завершить выбор':'Выбирать геометрию в 3D'}</button><div class="cad3-model-note">${facePicks.length?facePicks.map((pick,index)=>`${index?'B':'A'}: ${esc(assemblyInstance(pick.instance_id)?.name||pick.instance_id)} · ${esc(pick.reference.entity_kind==='vertex'?'Вершина':pick.reference.surface)} · ${esc(pick.reference.entity_key)}`).join('<br>'):'Выберите элементы на двух деталях.'}</div><label class="cad3-assembly-check">Тип <select data-assembly-mate-kind ${canEdit?'':'disabled'}>${state.assemblyPickKind==='vertex'?'<option value="fixed">Жёсткое</option><option value="coincident">Совпадение точек</option><option value="distance">Расстояние между точками</option>':'<option value="fixed">Жёсткое</option><option value="revolute">Вращательное</option><option value="slider">Поступательное</option><option value="cylindrical">Цилиндрическое</option><option value="coaxial">Соосное</option><option value="spherical">Шаровое</option><option value="coincident">Совпадение опорных точек</option><option value="parallel">Параллельность осей</option><option value="perpendicular">Перпендикулярность осей</option><option value="distance">Расстояние между опорными точками</option><option value="angle">Угол между осями</option>'}</select></label><button type="button" data-assembly-create-mate ${canCreateMate?'':'disabled'}>Создать зависимость</button><button type="button" data-assembly-clear-face-picks ${facePicks.length?'':'disabled'}>Очистить выбор</button></section>
        <section class="cad3-sec"><h3>Положение</h3><label class="cad3-assembly-check">Координаты <select data-assembly-coordinate><option value="local" ${mode==='local'?'selected':''}>Локальные</option><option value="global" ${mode==='global'?'selected':''}>Глобальные</option></select></label><dl class="cad3-grid">${row('X, мм',translation[0],'tx')}${row('Y, мм',translation[1],'ty')}${row('Z, мм',translation[2],'tz')}${row('Qw',rotation[0],'qw')}${row('Qx',rotation[1],'qx')}${row('Qy',rotation[2],'qy')}${row('Qz',rotation[3],'qz')}</dl><div class="cad3-assembly-actions"><button type="button" data-assembly-transform ${canEdit?'':'disabled'}>Применить положение</button><button type="button" data-assembly-undo ${canEdit&&assemblyUndo.length?'':'disabled'}>Undo</button><button type="button" data-assembly-redo ${canEdit&&assemblyRedo.length?'':'disabled'}>Redo</button></div></section>
        <section class="cad3-sec"><h3>Манипуляторы · шаг и привязка</h3><div class="cad3-assembly-actions"><input data-assembly-step type="number" min="0.001" step="any" value="${state.assemblyStep}" aria-label="Шаг перемещения, мм"><span>мм</span><input data-assembly-angle-step type="number" min="0.1" step="any" value="${state.assemblyAngleStep}" aria-label="Шаг поворота, градусы"><span>°</span><label><input type="checkbox" data-assembly-snap ${state.snapEnabled?'checked':''}> Привязка</label></div><div class="cad3-assembly-actions">${['X','Y','Z'].map(axis=>`<button type="button" data-assembly-axis="${axis}" data-assembly-sign="-1" ${canEdit?'':'disabled'}>−${axis}</button><button type="button" data-assembly-axis="${axis}" data-assembly-sign="1" ${canEdit?'':'disabled'}>+${axis}</button>`).join('')}</div><div class="cad3-assembly-actions">${['X','Y','Z'].map(axis=>`<button type="button" data-assembly-rotate-axis="${axis}" data-assembly-sign="-1" ${canEdit?'':'disabled'}>↶${axis}</button><button type="button" data-assembly-rotate-axis="${axis}" data-assembly-sign="1" ${canEdit?'':'disabled'}>↷${axis}</button>`).join('')}</div></section>
        <section class="cad3-sec"><h3>Сопряжения</h3>${mates.length?mates.map(mateEditor).join(''):'<div class="cad3-empty">Нет сопряжений у выбранного экземпляра.</div>'}</section><div class="cad3-model-note" data-assembly-status aria-live="polite">${canEdit?'':esc(lockLabel+' · просмотр доступен')}</div>`;
      props.querySelector('[data-assembly-visible]')?.addEventListener('change',event=>void editAssemblyProperties(instance,{visible:event.target.checked}));
      props.querySelector('[data-assembly-grounded]')?.addEventListener('change',event=>void editAssemblyProperties(instance,{grounded:event.target.checked}));
      props.querySelector('[data-assembly-pick-kind]')?.addEventListener('change',event=>{state.assemblyPickKind=event.target.value;state.assemblyFacePicks=[];modelViewer?.setSelectedFaces?.([]);renderAssemblyProps()});
      props.querySelector('[data-assembly-face-mode]')?.addEventListener('click',()=>{state.assemblyFacePickMode=!state.assemblyFacePickMode;const geometryLabel=state.assemblyPickKind==='vertex'?'вершину':'грань';setAssemblyStatus(state.assemblyFacePickMode?`Кликните ${geometryLabel} на детали A, затем на детали B.`:'Выбор геометрии приостановлен.');renderAssemblyProps()});
      props.querySelector('[data-assembly-clear-face-picks]')?.addEventListener('click',()=>{state.assemblyFacePicks=[];modelViewer?.setSelectedFaces?.([]);renderAssemblyProps()});
      props.querySelector('[data-assembly-create-mate]')?.addEventListener('click',()=>void createAssemblyMateFromFaces(props.querySelector('[data-assembly-mate-kind]')?.value));
      props.querySelector('[data-assembly-coordinate]')?.addEventListener('change',event=>{state.assemblyCoordinateMode=event.target.value;renderAssemblyProps()});
      props.querySelector('[data-assembly-snap]')?.addEventListener('change',event=>{state.snapEnabled=event.target.checked});
      props.querySelector('[data-assembly-step]')?.addEventListener('input',event=>{const value=Number(event.target.value);if(Number.isFinite(value)&&value>0)state.assemblyStep=value});
      props.querySelector('[data-assembly-angle-step]')?.addEventListener('input',event=>{const value=Number(event.target.value);if(Number.isFinite(value)&&value>0)state.assemblyAngleStep=value});
      props.querySelector('[data-assembly-transform]')?.addEventListener('click',()=>void applyAssemblyTransform(instance));
      props.querySelector('[data-assembly-undo]')?.addEventListener('click',()=>void travelAssemblyHistory('undo'));
      props.querySelector('[data-assembly-redo]')?.addEventListener('click',()=>void travelAssemblyHistory('redo'));
      props.querySelectorAll('[data-assembly-axis]').forEach(button=>button.addEventListener('click',()=>void nudgeAssemblyInstance(instance,button.dataset.assemblyAxis,Number(button.dataset.assemblySign))));
      props.querySelectorAll('[data-assembly-rotate-axis]').forEach(button=>button.addEventListener('click',()=>void nudgeAssemblyInstance(instance,button.dataset.assemblyRotateAxis,Number(button.dataset.assemblySign),true)));
      props.querySelectorAll('[data-mate-apply]').forEach(button=>button.addEventListener('click',()=>void applyAssemblyMate(instance,button.dataset.mateApply)));
      props.querySelectorAll('[data-mate-remove]').forEach(button=>button.addEventListener('click',()=>void removeAssemblyMate(button.dataset.mateRemove)));
    }
    props.addEventListener('click',e=>{const b=e.target.closest('[data-dep]');if(b)select(b.dataset.dep,true)});
    const tabs=$('.cad-inspector-tabs');tabs?.addEventListener('click',()=>queueMicrotask(()=>{const active=$('.cad-inspector-tab[aria-selected=true]',tabs)?.dataset.cadInspector||'properties';props.hidden=active!=='properties'}));
    function select(id,reveal=false){if(!state.byId.has(id))return;state.selectedOccurrenceId=null;state.selectedOccurrenceIds.clear();state.selectedInstanceId=null;state.selectedInstancePath=[];state.assemblyFacePicks=[];state.assemblyFacePickMode=false;modelViewer?.setSelectedFaces?.([]);const selected=state.byId.get(id);if(selected.type==='sketch'){const owner=state.objects.find(item=>item.type==='feature'&&(item.source_sketch_id===id||(item.source_sketch_ids||[]).includes(id)));if(owner){expandedFeatures.add(owner.id);saveModelTreeState()}}const selectedBodyId=selected.type==='body'?selected.id:selected.body_id;const ownerGroup=modelGroups().find(group=>group.body.id===selectedBodyId||group.features.some(feature=>feature.id===id));if(ownerGroup)state.activeModelBodyId=ownerGroup.body.id;state.selected=id;state.selectionCleared=false;try{selectedObjectId=id}catch{}if(selected.type==='feature')modelViewer?.setSelectedFeature?.(selected.id);else modelViewer?.setSelectedBody?.(bodyFor(selected)?.id||null);renderTree();renderProps(selected);if(reveal)list?.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({block:'nearest'});detail.dataset.v2Selection='true';syncOverlay()}
    function selectAssemblyOccurrence(pathId,instanceId,event){
      if(!pathId||!instanceId)return;
      const multi=Boolean(event&&(event.ctrlKey||event.metaKey||event.shiftKey));
      if(!multi)state.selectedOccurrenceIds.clear();
      if(multi&&state.selectedOccurrenceIds.has(pathId))state.selectedOccurrenceIds.delete(pathId);
      else state.selectedOccurrenceIds.add(pathId);
      if(!state.selectedOccurrenceIds.size){clearSelection();return}
      const activePath=state.selectedOccurrenceIds.has(pathId)?pathId:[...state.selectedOccurrenceIds].at(-1)||pathId;
      state.selectedOccurrenceId=activePath;state.selectedInstanceId=state.selectedOccurrenceIds.has(pathId)?instanceId:activePath.split('/').at(-1);state.selectedInstancePath=activePath.split('/');state.selected=null;state.selectionCleared=false;
      const selectedIds=[...state.selectedOccurrenceIds];
      if(modelViewer?.setSelectedBodies)modelViewer.setSelectedBodies(selectedIds);
      else modelViewer?.setSelectedBody?.(pathId);
      renderTree();renderAssemblyProps();detail.dataset.v2Selection='true';syncOverlay();
    }
    function clearSelection(){if(!state.selected&&!state.selectedOccurrenceIds.size&&state.selectionCleared)return;state.selected=null;state.selectedOccurrenceId=null;state.selectedOccurrenceIds.clear();state.selectedInstanceId=null;state.selectedInstancePath=[];state.assemblyFacePicks=[];state.assemblyFacePickMode=false;state.selectionCleared=true;try{selectedObjectId=null}catch{}modelViewer?.setSelectedFaces?.([]);modelViewer?.setSelectedBodies?.([]);modelViewer?.setSelectedBody?.(null);renderTree();renderProps(null);detail.dataset.v2Selection='true';syncOverlay()}
    const cloneJson=value=>JSON.parse(JSON.stringify(value));
    const resetAssemblyHistory=()=>{assemblyUndo.length=0;assemblyRedo.length=0;assemblyLastRevision=null};
    async function executeAssemblyCommand(command,{history=null,historyAction=null}={}){
      const projectId=state.project,client=window.cadyxApi?.request;
      if(!projectId||!client)throw new Error('Командный API сборки недоступен.');
      const encoded=encodeURIComponent(projectId);let leaseId=null,result=null,commandError=null,releaseError=null,revision=null;
      setAssemblyStatus('Проверяю ревизию и блокировку…');
      try{
        const info=await client(`/api/v1/projects/${encoded}?view=browser`);
        if(projectId!==state.project)throw new Error('Выбран другой проект.');
        revision=info.document?.revision;
        if(!Number.isInteger(revision))throw new Error('Сервер не вернул ревизию документа.');
        if(state.rev!==null&&revision!==state.rev){resetAssemblyHistory();await window.cadyxApi.refresh();throw new Error('Сборка изменилась в другом сеансе. Данные обновлены; повторите действие.');}
        if(info.lock?.state!=='FREE'||info.lock?.recovery_required)throw new Error(info.lock?.state==='AI_LOCKED'?'ИИ сейчас изменяет сборку. Просмотр остаётся доступен.':'Сборка занята или требует восстановления блокировки.');
        if(historyAction&&assemblyLastRevision!==revision){resetAssemblyHistory();await window.cadyxApi.refresh();throw new Error('История Undo/Redo устарела после внешнего изменения сборки.');}
        const lease=await client(`/api/v1/projects/${encoded}/leases`,{method:'POST',body:JSON.stringify({owner_type:'human',owner_id:`assembly-ui:${crypto.randomUUID()}`})});
        leaseId=lease.lease?.lease_id;if(!leaseId)throw new Error('Core не выдал блокировку редактирования.');
        result=await client(`/api/v1/projects/${encoded}/commands`,{method:'POST',body:JSON.stringify({lease_id:leaseId,expected_revision:revision,command})});
      }catch(error){commandError=error}
      finally{if(leaseId){try{await client(`/api/v1/projects/${encoded}/leases/${encodeURIComponent(leaseId)}/release`,{method:'POST',body:'{}'})}catch(error){releaseError=error}}}
      if(commandError){setAssemblyStatus(commandError.message);throw commandError}
      const nextRevision=revision+1;
      if(typeof history==='function')history=history(result);
      if(historyAction==='undo'){assemblyUndo.pop();assemblyRedo.push(history)}
      else if(historyAction==='redo'){assemblyRedo.pop();assemblyUndo.push(history)}
      else if(history){if(assemblyLastRevision!==null&&assemblyLastRevision!==revision)resetAssemblyHistory();assemblyUndo.push(history);assemblyRedo.length=0}
      assemblyLastRevision=nextRevision;
      try{await window.cadyxApi.refresh()}catch{}
      if(releaseError){setAssemblyStatus(`Изменение сохранено, но блокировка не освобождена: ${releaseError.message}`);throw releaseError}
      setAssemblyStatus('Изменение сохранено.');
      return result;
    }
    async function pickAssemblyGeometryFace(mesh,faceIndex,event,hitDepth){
      const projectId=state.project,instance=assemblyInstance(mesh.instance_id),body=state.byId.get(mesh.source_body_id);
      if(!projectId||!instance||!body){setAssemblyStatus('Выбранная геометрия не принадлежит экземпляру сборки.');return}
      const range=(mesh.face_triangle_ranges||[]).find(item=>item.face_index===faceIndex);
      if(state.assemblyPickKind==='face'&&!range){setAssemblyStatus('На этой поверхности нет стабильной B-rep грани.');return}
      const cacheKey=`${projectId}:${state.rev}:${body.id}`;let topology=assemblyTopologyCache.get(cacheKey);
      try{
        if(!topology){topology=await window.cadyxApi.request(`/api/v1/agent/projects/${encodeURIComponent(projectId)}/topology?body_id=${encodeURIComponent(body.id)}`);if(projectId!==state.project||topology.revision!==state.rev)throw new Error('Геометрия изменилась. Обновите сборку и выберите элемент повторно.');assemblyTopologyCache.set(cacheKey,topology)}
        let reference,highlightFace=faceIndex;
        if(state.assemblyPickKind==='vertex'){
          const projection=projectSetup(),rect=canvas.getBoundingClientRect(),pixelRatio=Math.min(devicePixelRatio||1,2);
          if(!projection||!event)throw new Error('Не удалось определить положение выбранной вершины в viewport.');
          const clickX=(event.clientX-rect.left)*pixelRatio,clickY=(event.clientY-rect.top)*pixelRatio;
          const parent=parentPoseForPath(mesh.instance_path),worldPose=poseCompose(parent,instancePose(instance));
          const depthTolerance=Math.max(1e-6,14*pixelRatio/projection.scale),vertices=(topology.vertices||[]).filter(vertex=>vertex.semantic_key&&Array.isArray(vertex.position));
          const projected=vertices.map(vertex=>{const rotated=rotateVector(worldPose.rotation,vertex.position),point=worldPose.translation.map((value,index)=>value+rotated[index]),screen=projection.p(...point);return{vertex,screen,distance:Math.hypot(screen[0]-clickX,screen[1]-clickY)}}).filter(candidate=>candidate.distance<=14*pixelRatio&&candidate.screen[2]>=hitDepth-depthTolerance).sort((a,b)=>a.distance-b.distance);
          if(!projected.length)throw new Error('Рядом с выбранным местом не найдена видимая топологическая вершина.');
          if(projected.length>1&&Math.abs(projected[1].distance-projected[0].distance)<.1)throw new Error('Выбрано неоднозначное место. Увеличьте масштаб и повторите выбор вершины.');
          const vertex=projected[0].vertex;
          reference={body_id:body.id,entity_kind:'vertex',entity_key:vertex.semantic_key,entity_index:vertex.index,surface:'point',origin:vertex.position,direction:[0,0,1]};
        }else{
          const matches=(topology.faces||[]).filter(face=>face.index===faceIndex);
          if(matches.length!==1||!matches[0].semantic_key)throw new Error('Выбранную B-rep грань нельзя однозначно связать с топологией.');
          const face=matches[0],surface=face.surface,origin=['cylinder','cone'].includes(surface)?face.axis_origin:face.center,direction=surface==='plane'?face.normal:face.axis_direction;
          if(!['plane','cylinder','cone','sphere'].includes(surface)||!Array.isArray(origin)||!Array.isArray(direction))throw new Error(`Сопряжение по поверхности «${surface||'неизвестно'}» не поддерживается.`);
          reference={body_id:body.id,entity_kind:'face',entity_key:face.semantic_key,entity_index:face.index,surface,origin,direction};
        }
        const pick={instance_id:instance.id,instance_path:mesh.instance_path.slice(),object_id:mesh.object_id,reference};
        const existing=state.assemblyFacePicks.findIndex(item=>item.object_id===pick.object_id&&item.reference.entity_kind===reference.entity_kind&&item.reference.entity_index===reference.entity_index);
        if(existing>=0)state.assemblyFacePicks.splice(existing,1);else{state.assemblyFacePicks.push(pick);if(state.assemblyFacePicks.length>2)state.assemblyFacePicks.shift()}
        selectAssemblyOccurrence(mesh.object_id,instance.id);
        state.selectedInstancePath=mesh.instance_path.slice();
        modelViewer?.setSelectedFaces?.(state.assemblyPickKind==='vertex'&&Number.isInteger(highlightFace)?[{objectId:mesh.object_id,faceIndex:highlightFace}]:state.assemblyFacePicks.filter(item=>item.reference.entity_kind==='face').map(item=>({objectId:item.object_id,faceIndex:item.reference.entity_index})));
        setAssemblyStatus(`Выбрано элементов: ${state.assemblyFacePicks.length}/2`);renderAssemblyProps();
      }catch(error){setAssemblyStatus(error.message)}
    }
    async function createAssemblyMateFromFaces(kind){
      const picks=state.assemblyFacePicks;if(picks.length!==2){setAssemblyStatus('Для зависимости выберите два геометрических элемента.');return}
      const [a,b]=picks,instanceA=assemblyInstance(a.instance_id),instanceB=assemblyInstance(b.instance_id);
      if(!instanceA||!instanceB||instanceA.id===instanceB.id||instanceA.parent_id!==instanceB.parent_id){setAssemblyStatus('Выберите элементы двух разных экземпляров одного родительского узла.');return}
      const pointBased=a.reference.entity_kind==='vertex'||b.reference.entity_kind==='vertex';
      if(pointBased&&!['fixed','coincident','distance'].includes(kind)){setAssemblyStatus('Вершины поддерживают жёсткое, точечное сопряжение и расстояние.');return}
      if(kind==='spherical'&&(a.reference.surface!=='sphere'||b.reference.surface!=='sphere')){setAssemblyStatus('Для шарового сопряжения выберите две сферические грани.');return}
      if(['revolute','coaxial','cylindrical','slider'].includes(kind)&&![a.reference.surface,b.reference.surface].every(surface=>['plane','cylinder','cone'].includes(surface))){setAssemblyStatus('Для осевого сопряжения выберите плоские или цилиндрические грани.');return}
      if(['parallel','perpendicular','angle'].includes(kind)&&![a.reference.surface,b.reference.surface].every(surface=>['plane','cylinder','cone'].includes(surface))){setAssemblyStatus('Для угловой зависимости выберите плоские или осевые грани.');return}
      const command={type:'add_assembly_mate',kind,a_instance_id:a.instance_id,b_instance_id:b.instance_id,
        name:`${kind} · ${instanceA.name} — ${instanceB.name}`,reference_a:a.reference,reference_b:b.reference};
      try{
        await executeAssemblyCommand(command,{history:result=>{const mateId=result.history_entry?.result?.mate_id;if(!mateId)throw new Error('Core не вернул ID созданного сопряжения.');command.mate_id=mateId;return{undo:{type:'remove_assembly_mate',mate_id:mateId},redo:cloneJson(command)}}});
        state.assemblyFacePicks=[];state.assemblyFacePickMode=false;modelViewer?.setSelectedFaces?.([]);renderAssemblyProps();
      }catch(error){setAssemblyStatus(error.message)}
    }
    async function editAssemblyProperties(instance,fields){
      const command={type:'set_assembly_instance_properties',instance_id:instance.id,...fields};
      const inverse={type:'set_assembly_instance_properties',instance_id:instance.id};
      for(const key of Object.keys(fields))inverse[key]=instance[key];
      try{await executeAssemblyCommand(command,{history:{undo:inverse,redo:command}})}catch(error){setAssemblyStatus(error.message)}
    }
    function assemblyTransformTargets(instance,nextPose){
      const paths=[...state.selectedOccurrenceIds],representatives=[],covered=new Set();
      for(const pathId of paths){const path=pathId.split('/'),id=path.at(-1),component=assemblyComponentIds(id);if([...component].some(member=>covered.has(member)))continue;for(const member of component)covered.add(member);representatives.push({id,path})}
      if(!representatives.length)representatives.push({id:instance.id,path:state.selectedInstancePath.slice()});
      representatives.sort((a,b)=>Number(b.id===instance.id)-Number(a.id===instance.id));
      if(representatives.length===1)return[{instance_id:instance.id,path:state.selectedInstancePath.slice(),translation:nextPose.translation.slice(),rotation:nextPose.rotation.slice()}];
      const oldLocal=instancePose(instance),parent=parentPoseForPath(state.selectedInstancePath),oldWorld=poseCompose(parent,oldLocal),newWorld=poseCompose(parent,nextPose),worldDelta=poseCompose(newWorld,poseInverse(oldWorld)),localTranslationDelta=nextPose.translation.map((value,index)=>value-oldLocal.translation[index]),localRotationDelta=quatMultiply(quatInverse(oldLocal.rotation),nextPose.rotation);
      return representatives.map(({id,path})=>{const target=assemblyInstance(id),old=instancePose(target);if(id===instance.id)return{instance_id:id,path,translation:nextPose.translation.slice(),rotation:nextPose.rotation.slice()};if(state.assemblyCoordinateMode==='local')return{instance_id:id,path,translation:old.translation.map((value,index)=>value+localTranslationDelta[index]),rotation:quatMultiply(old.rotation,localRotationDelta)};const parentPose=parentPoseForPath(path),oldWorldPose=poseCompose(parentPose,old),newWorldPose=poseCompose(worldDelta,oldWorldPose),local=poseCompose(poseInverse(parentPose),newWorldPose);return{instance_id:id,path,translation:local.translation,rotation:local.rotation}});
    }
    async function commitAssemblyTransform(instance,nextPose){
      const transforms=assemblyTransformTargets(instance,nextPose).map(({path,...item})=>item);
      if(transforms.length>1){const command={type:'transform_assembly_instances',transforms},previous=transforms.map(item=>{const old=assemblyInstance(item.instance_id);return{instance_id:item.instance_id,translation:old.translation.slice(),rotation:old.rotation.slice()}});
        try{await executeAssemblyCommand(command,{history:{undo:{type:'transform_assembly_instances',transforms:previous},redo:command}})}
        catch(error){setAssemblyStatus(error.message);throw error}return;
      }
      const previous={translation:instance.translation.slice(),rotation:instance.rotation.slice()},command={type:'transform_assembly_instance',instance_id:instance.id,translation:nextPose.translation.slice(),rotation:nextPose.rotation.slice()};
      try{await executeAssemblyCommand(command,{history:{undo:{...command,...previous},redo:command}})}
      catch(error){setAssemblyStatus(error.message);throw error}
    }
    async function applyAssemblyTransform(instance){
      const values=key=>Number(props.querySelector(`[data-assembly-value="${key}"]`)?.value);
      let pose={translation:[values('tx'),values('ty'),values('tz')],rotation:[values('qw'),values('qx'),values('qy'),values('qz')]};
      if(!pose.translation.every(Number.isFinite)||!pose.rotation.every(Number.isFinite)){setAssemblyStatus('Введите конечные координаты и quaternion.');return}
      const norm=Math.hypot(...pose.rotation);if(Math.abs(norm-1)>1e-5){setAssemblyStatus('Quaternion должен иметь единичную длину.');return}
      const step=Number(props.querySelector('[data-assembly-step]')?.value);if(state.snapEnabled&&Number.isFinite(step)&&step>0)pose.translation=pose.translation.map(value=>Math.round(value/step)*step);
      pose=localPoseFromMode(pose,state.assemblyCoordinateMode);
      try{await commitAssemblyTransform(instance,pose)}catch{}
    }
    async function nudgeAssemblyInstance(instance,axis,sign,rotate=false){
      const index={X:0,Y:1,Z:2}[axis];if(index===undefined)return;
      const current={translation:instance.translation.slice(),rotation:instance.rotation.slice()},mode=state.assemblyCoordinateMode,shown=selectedPose(instance,mode),next={translation:shown.translation.slice(),rotation:shown.rotation.slice()};
      if(rotate){const degrees=Number(props.querySelector('[data-assembly-angle-step]')?.value);if(!Number.isFinite(degrees)||degrees<=0){setAssemblyStatus('Шаг поворота должен быть положительным.');return}const half=degrees*Math.PI/360*Math.sign(sign),delta=[Math.cos(half),...(index===0?[Math.sin(half),0,0]:index===1?[0,Math.sin(half),0]:[0,0,Math.sin(half)])];next.rotation=mode==='global'?quatMultiply(delta,shown.rotation):quatMultiply(shown.rotation,delta)}
      else{const amount=Number(props.querySelector('[data-assembly-step]')?.value);if(!Number.isFinite(amount)||amount<=0){setAssemblyStatus('Шаг перемещения должен быть положительным.');return}next.translation[index]+=amount*Math.sign(sign);if(state.snapEnabled)next.translation[index]=Math.round(next.translation[index]/amount)*amount}
      const local=localPoseFromMode(next,mode);
      try{await commitAssemblyTransform(instance,local)}catch{}
    }
    async function applyAssemblyMate(instance,mateId){
      const mate=(state.assembly?.mates||[]).find(item=>item.id===mateId);if(!mate)return;
      const command={type:'set_assembly_mate',mate_id:mateId},inverse={type:'set_assembly_mate',mate_id:mateId};
      const angle=props.querySelector(`[data-mate-angle="${CSS.escape(mateId)}"]`),offset=props.querySelector(`[data-mate-offset="${CSS.escape(mateId)}"]`);
      if(angle&&Number.isFinite(Number(angle.value))&&Number(angle.value)!==mate.angle_deg){command.angle_deg=Number(angle.value);inverse.angle_deg=mate.angle_deg}
      if(offset&&Number.isFinite(Number(offset.value))&&Number(offset.value)!==mate.offset_mm){command.offset_mm=Number(offset.value);inverse.offset_mm=mate.offset_mm}
      const oldLimits=mate.limits||{angle_min_deg:null,angle_max_deg:null,offset_min_mm:null,offset_max_mm:null},nextLimits={...oldLimits};let limitsChanged=false;
      for(const input of props.querySelectorAll(`[data-mate-limit^="${CSS.escape(mateId)}:"]`)){const field=input.dataset.mateLimit.split(':').at(-1),value=input.value.trim()===''?null:Number(input.value);if(value!==null&&!Number.isFinite(value)){setAssemblyStatus('Предел должен быть конечным числом или пустым.');return}if(value!==oldLimits[field]){nextLimits[field]=value;limitsChanged=true}}
      if(limitsChanged){command.limits=nextLimits;inverse.limits=cloneJson(oldLimits)}
      const quaternionInputs=[...props.querySelectorAll(`[data-mate-orientation^="${CSS.escape(mateId)}:"]`)];
      if(quaternionInputs.length===4){const orientation=quaternionInputs.sort((a,b)=>Number(a.dataset.mateOrientation.split(':').at(-1))-Number(b.dataset.mateOrientation.split(':').at(-1))).map(input=>Number(input.value));if(!orientation.every(Number.isFinite)){setAssemblyStatus('Введите конечные компоненты quaternion.');return}const norm=Math.hypot(...orientation);if(Math.abs(norm-1)>1e-5){setAssemblyStatus('Quaternion должен иметь единичную длину.');return}if(orientation.some((value,index)=>value!==mate.orientation[index])){command.orientation=orientation;inverse.orientation=mate.orientation.slice()}}
      if(Object.keys(command).length===2){setAssemblyStatus('Координаты сопряжения не изменились.');return}
      try{await executeAssemblyCommand(command,{history:{undo:inverse,redo:command}})}catch(error){setAssemblyStatus(error.message)}
    }
    async function removeAssemblyMate(mateId){
      const mate=(state.assembly?.mates||[]).find(item=>item.id===mateId);if(!mate)return;
      const {id,...record}=cloneJson(mate),command={type:'remove_assembly_mate',mate_id:id};
      const undo={type:'add_assembly_mate',mate_id:id,...record};
      try{await executeAssemblyCommand(command,{history:{undo,redo:command}})}catch(error){setAssemblyStatus(error.message)}
    }
    async function travelAssemblyHistory(direction){
      const source=direction==='undo'?assemblyUndo:assemblyRedo,entry=source[source.length-1];if(!entry)return;
      const command=entry[direction];if(!command)return;
      try{await executeAssemblyCommand(command,{history:entry,historyAction:direction})}catch(error){setAssemblyStatus(error.message)}
    }
    const quatMultiply=(a,b)=>[a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3],a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2],a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1],a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0]];
    const quatInverse=q=>[q[0],-q[1],-q[2],-q[3]];
    const rotateVector=(q,v)=>{const p=[0,...v],r=quatMultiply(quatMultiply(q,p),quatInverse(q));return r.slice(1)};
    const poseCompose=(a,b)=>({translation:rotateVector(a.rotation,b.translation).map((value,index)=>value+a.translation[index]),rotation:quatMultiply(a.rotation,b.rotation)});
    const poseInverse=pose=>{const rotation=quatInverse(pose.rotation);return{translation:rotateVector(rotation,pose.translation.map(value=>-value)),rotation}};
    const instancePose=instance=>({translation:instance.translation.slice(),rotation:instance.rotation.slice()});
    function parentPoseForPath(path){let pose={translation:[0,0,0],rotation:[1,0,0,0]};for(const id of path.slice(0,-1)){const instance=assemblyInstance(id);if(instance)pose=poseCompose(pose,instancePose(instance))}return pose}
    function selectedPose(instance,mode){const local=instancePose(instance);return mode==='global'?poseCompose(parentPoseForPath(state.selectedInstancePath),local):local}
    function localPoseFromMode(pose,mode){return mode==='global'?poseCompose(poseInverse(parentPoseForPath(state.selectedInstancePath)),pose):pose}
    function assemblyComponentIds(instanceId){const root=assemblyInstance(instanceId);if(!root)return new Set();const connected=new Set([instanceId]);let changed=true;while(changed){changed=false;for(const mate of state.assembly?.mates||[]){const a=assemblyInstance(mate.a_instance_id),b=assemblyInstance(mate.b_instance_id);if(!a||!b||a.parent_id!==root.parent_id||b.parent_id!==root.parent_id)continue;if(connected.has(a.id)&&!connected.has(b.id)){connected.add(b.id);changed=true}if(connected.has(b.id)&&!connected.has(a.id)){connected.add(a.id);changed=true}}}return connected}
    sidebar?.addEventListener('click',event=>{if(event.target.closest('button,input,select,textarea,a,[contenteditable="true"]'))return;clearSelection()},true);
    function bodyFor(o){if(!o)return null;if(o.type==='body')return o;return state.byId.get(o.body_id)||null}
    function projectSetup(){let viewer;try{viewer=modelViewer}catch{return null}const c=viewer?.getCamera?.();if(!c?.orientation||!state.scene)return null;const meshes=(state.scene.objects||[]).filter(item=>item.type==='body'&&item.positions?.length);if(!meshes.length)return null;const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];for(const m of meshes)for(let i=0;i<m.positions.length;i+=3)for(let a=0;a<3;a++){min[a]=Math.min(min[a],m.positions[i+a]);max[a]=Math.max(max[a],m.positions[i+a])}const center=min.map((x,a)=>(x+max[a])/2);let radius=.001;for(const m of meshes)for(let i=0;i<m.positions.length;i+=3)radius=Math.max(radius,Math.hypot(m.positions[i]-center[0],m.positions[i+1]-center[1],m.positions[i+2]-center[2]));const W=canvas.width,H=canvas.height,dpr=Math.min(devicePixelRatio||1,2),scale=Math.min(W,H)*.43*c.zoom/radius,O=c.orientation;return{W,H,scale,p:(x,y,z)=>{x-=center[0];y-=center[1];z-=center[2];return[W/2+(O[0]*x+O[1]*y+O[2]*z)*scale+c.panX*dpr,H/2-(O[6]*x+O[7]*y+O[8]*z)*scale+c.panY*dpr,O[3]*x+O[4]*y+O[5]*z]}}}
    function sketchWorldPoint(sketch,x,y){const axis=sketch.section_normal_axis,offset=sketch.section_offset;if(axis==='X'&&Number.isFinite(offset))return[offset,x,y];if(axis==='Y'&&Number.isFinite(offset))return[x,offset,y];if(axis==='Z'&&Number.isFinite(offset))return[x,y,offset];return sketch.plane==='XZ'?[x,0,y]:[x,y,0]}
    function drawSelectedSketch(sketch,P,ctx,dpr){
      const byId=state.byId,owned=state.objects.filter(item=>item.sketch_id===sketch.id&&!item.construction&&['line','circle','ellipse','spline'].includes(item.type));
      ctx.save();ctx.strokeStyle='#ff8d45';ctx.lineWidth=2.4*dpr;ctx.lineCap='round';ctx.lineJoin='round';ctx.shadowColor='#ff7a2a';ctx.shadowBlur=5*dpr;
      const strokePoints=(points,closed=false)=>{if(points.length<2)return;ctx.beginPath();for(let index=0;index<points.length;index++){const screen=P.p(...points[index]);if(index===0)ctx.moveTo(screen[0],screen[1]);else ctx.lineTo(screen[0],screen[1])}if(closed)ctx.closePath();ctx.stroke()};
      for(const item of owned){
        if(item.type==='line'){if(item.extent&&item.extent!=='segment')continue;const a=byId.get(item.start_id),b=byId.get(item.end_id);if(a&&b)strokePoints([sketchWorldPoint(sketch,a.x,a.y),sketchWorldPoint(sketch,b.x,b.y)]);continue}
        if(item.type==='circle'){const center=byId.get(item.center_id);if(!center)continue;const start=item.start_id?byId.get(item.start_id):null;const angle=start?Math.atan2(start.y-center.y,start.x-center.x):0,sweep=Number.isFinite(item.sweep_radians)?item.sweep_radians:Math.PI*2,steps=Math.max(16,Math.ceil(96*Math.abs(sweep)/(Math.PI*2))),points=[];for(let i=0;i<=steps;i++){if(i===steps&&Math.abs(sweep)>=Math.PI*2-1e-8)break;const a=angle+sweep*i/steps;points.push(sketchWorldPoint(sketch,center.x+item.radius*Math.cos(a),center.y+item.radius*Math.sin(a)))}strokePoints(points,Math.abs(sweep)>=Math.PI*2-1e-8);continue}
        if(item.type==='ellipse'){const sweep=Number.isFinite(item.sweep_radians)?item.sweep_radians:Math.PI*2,rotation=item.rotation_radians||0,steps=Math.max(24,Math.ceil(128*Math.abs(sweep)/(Math.PI*2))),points=[];for(let i=0;i<=steps;i++){if(i===steps&&Math.abs(sweep)>=Math.PI*2-1e-8)break;const angle=(item.start_angle_radians||0)+sweep*i/steps,c=Math.cos(angle),s=Math.sin(angle),x=item.center_x+item.major_radius*c*Math.cos(rotation)-item.minor_radius*s*Math.sin(rotation),y=item.center_y+item.major_radius*c*Math.sin(rotation)+item.minor_radius*s*Math.cos(rotation);points.push(sketchWorldPoint(sketch,x,y))}strokePoints(points,Math.abs(sweep)>=Math.PI*2-1e-8);continue}
        if(item.type==='spline'){const points=(item.point_ids||[]).map(id=>byId.get(id)).filter(Boolean).map(point=>sketchWorldPoint(sketch,point.x,point.y));strokePoints(points,item.mode==='closed')}
      }
      ctx.restore();
    }
    function drawAssemblyGizmo(ctx,P,dpr){
      gizmoRoot.hidden=true;
      const instance=assemblyInstance(state.selectedInstanceId);if(!instance||!state.selectedInstancePath.length)return;
      const mode=state.assemblyCoordinateMode,displayPose=assemblyDrag?.instance.id===instance.id?assemblyDrag.shownPose:selectedPose(instance,mode),worldPose=mode==='global'?displayPose:poseCompose(parentPoseForPath(state.selectedInstancePath),displayPose);
      const origin=P.p(...worldPose.translation),targets=assemblyTransformTargets(instance,instancePose(instance)),blocked=targets.some(target=>[...assemblyComponentIds(target.instance_id)].some(id=>assemblyInstance(id)?.grounded))||state.lock?.state!=='FREE'||state.lock?.recovery_required;
      gizmoRoot.hidden=false;
      const colors={X:'#f06c68',Y:'#72c990',Z:'#70a8ff'};
      ['X','Y','Z'].forEach((axis,index)=>{
        const basis=index===0?[1,0,0]:index===1?[0,1,0]:[0,0,1];
        const worldAxis=mode==='local'?rotateVector(worldPose.rotation,basis):basis;
        const projected=P.p(worldPose.translation.map((value,i)=>value+worldAxis[i]));
        let dx=projected[0]-origin[0],dy=projected[1]-origin[1],length=Math.hypot(dx,dy);
        if(length<1e-6){dx=[1,0,-1][index];dy=[0,-1,-1][index];length=Math.hypot(dx,dy)}
        dx/=length;dy/=length;const end=[origin[0]+dx*74*dpr,origin[1]+dy*74*dpr];
        ctx.save();ctx.strokeStyle=colors[axis];ctx.lineWidth=2*dpr;ctx.globalAlpha=.9;ctx.beginPath();ctx.moveTo(origin[0],origin[1]);ctx.lineTo(end[0],end[1]);ctx.stroke();ctx.restore();
        const move=gizmoRoot.querySelector(`[data-gizmo-kind="translate"][data-axis="${axis}"]`);
        if(move){move.style.left=`${end[0]/dpr}px`;move.style.top=`${end[1]/dpr}px`;move.disabled=blocked}
        const rotate=gizmoRoot.querySelector(`[data-gizmo-kind="rotate"][data-axis="${axis}"]`);
        if(rotate){const offset=[[42,-28],[4,-52],[-38,-30]][index];rotate.style.left=`${origin[0]/dpr+offset[0]}px`;rotate.style.top=`${origin[1]/dpr+offset[1]}px`;rotate.disabled=blocked}
      });
      ctx.save();ctx.fillStyle='#f1f5f6';ctx.beginPath();ctx.arc(origin[0],origin[1],4*dpr,0,Math.PI*2);ctx.fill();ctx.restore();
    }
    function drawOverlay(){
      const dpr=Math.min(devicePixelRatio||1,2),rect=panel.getBoundingClientRect();
      overlay.width=Math.round(rect.width*dpr);overlay.height=Math.round(rect.height*dpr);
      const ctx=overlay.getContext('2d');ctx.clearRect(0,0,overlay.width,overlay.height);
      const o=state.byId.get(state.selected),P=projectSetup();
      if(P){
        ctx.save();ctx.font=`600 ${11*dpr}px "Segoe UI Variable", "Segoe UI", Arial, sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';
        for(const {body} of modelGroups()){
          const mesh=sceneBody(body.id);if(!mesh?.positions?.length)continue;
          const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
          for(let i=0;i<mesh.positions.length;i+=3)for(let axis=0;axis<3;axis++){min[axis]=Math.min(min[axis],mesh.positions[i+axis]);max[axis]=Math.max(max[axis],mesh.positions[i+axis])}
          const anchor=P.p((min[0]+max[0])/2,(min[1]+max[1])/2,max[2]),text=body.name,padding=7*dpr,width=ctx.measureText(text).width+padding*2,height=22*dpr;
          const x=Math.max(width/2+4*dpr,Math.min(overlay.width-width/2-4*dpr,anchor[0]));
          const y=Math.max(height/2+4*dpr,anchor[1]-height);
          ctx.fillStyle='rgba(9,15,19,.88)';ctx.strokeStyle='rgba(255,145,75,.72)';ctx.lineWidth=dpr;
          ctx.beginPath();ctx.roundRect(x-width/2,y-height/2,width,height,5*dpr);ctx.fill();ctx.stroke();
          ctx.fillStyle='#fff0e5';ctx.fillText(text,x,y);
        }
        ctx.restore();
      }
      if(P)drawAssemblyGizmo(ctx,P,dpr);else gizmoRoot.hidden=true;
      if(o?.type==='sketch'&&P){drawSelectedSketch(o,P,ctx,dpr);return}
      const body=bodyFor(o),m=sceneBody(body?.id);
      if(!m||!P)return;
      ctx.save();
      const pts=m.positions,min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
      for(let i=0;i<pts.length;i+=3)for(let a=0;a<3;a++){min[a]=Math.min(min[a],pts[i+a]);max[a]=Math.max(max[a],pts[i+a])}
      const corners=[];for(const x of [min[0],max[0]])for(const y of [min[1],max[1]])for(const z of [min[2],max[2]])corners.push(P.p(x,y,z));
      const axisNames=['X','Y','Z'],center=min.map((value,axis)=>(value+max[axis])/2),center2d=P.p(...center);
      ctx.font=`700 ${11*dpr}px "Segoe UI Variable", "Segoe UI", Arial, sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.lineWidth=1*dpr;ctx.strokeStyle='#ffb27e';ctx.fillStyle='#ffb27e';ctx.globalAlpha=.96;
      const drawArrow=(tip,dx,dy)=>{
        const length=5*dpr,halfWidth=2.2*dpr,baseX=tip[0]+dx*length,baseY=tip[1]+dy*length;
        ctx.beginPath();ctx.moveTo(tip[0],tip[1]);ctx.lineTo(baseX-dy*halfWidth,baseY+dx*halfWidth);
        ctx.lineTo(baseX+dy*halfWidth,baseY-dx*halfWidth);ctx.closePath();ctx.fill();
      };
      for(let axis=0;axis<3;axis++){
        const value=max[axis]-min[axis];if(value<=1e-6)continue;
        const otherAxes=[0,1,2].filter(index=>index!==axis);let best=null;
        for(const firstSide of [min[otherAxes[0]],max[otherAxes[0]]])for(const secondSide of [min[otherAxes[1]],max[otherAxes[1]]]){
          const start=[...min],end=[...min];start[axis]=min[axis];end[axis]=max[axis];
          start[otherAxes[0]]=end[otherAxes[0]]=firstSide;start[otherAxes[1]]=end[otherAxes[1]]=secondSide;
          const a=P.p(...start),b=P.p(...end),dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
          if(length<8*dpr)continue;
          const middleX=(a[0]+b[0])/2,middleY=(a[1]+b[1])/2;
          const side=(dx*(middleY-center2d[1])-dy*(middleX-center2d[0]))/length;
          const score=Math.abs(side);
          if(!best||score>best.score)best={a,b,dx:dx/length,dy:dy/length,side,score};
        }
        if(!best)continue;
        const outwardX=-best.dy*Math.sign(best.side||1),outwardY=best.dx*Math.sign(best.side||1);
        const offset=(12+axis*6)*dpr;
        const start=[best.a[0]+outwardX*offset,best.a[1]+outwardY*offset];
        const end=[best.b[0]+outwardX*offset,best.b[1]+outwardY*offset];
        ctx.beginPath();
        ctx.moveTo(best.a[0],best.a[1]);ctx.lineTo(start[0]+outwardX*3*dpr,start[1]+outwardY*3*dpr);
        ctx.moveTo(best.b[0],best.b[1]);ctx.lineTo(end[0]+outwardX*3*dpr,end[1]+outwardY*3*dpr);
        ctx.moveTo(start[0],start[1]);ctx.lineTo(end[0],end[1]);ctx.stroke();
        drawArrow(start,best.dx,best.dy);drawArrow(end,-best.dx,-best.dy);
        const label=`${axisNames[axis]}  ${fmt(value)} mm`,labelX=(start[0]+end[0])/2+outwardX*8*dpr;
        const labelY=(start[1]+end[1])/2+outwardY*8*dpr,textWidth=ctx.measureText(label).width,padding=3*dpr;
        ctx.fillStyle='rgba(11,17,20,.94)';ctx.fillRect(labelX-textWidth/2-padding,labelY-7*dpr,textWidth+padding*2,14*dpr);
        ctx.fillStyle='#ffb27e';ctx.fillText(label,labelX,labelY);
      }
      const dia=Number(o?.parameters?.outer_diameter??o?.parameters?.major_diameter);
      if(Number.isFinite(dia)){
        const point=corners.reduce((best,candidate)=>candidate[1]<best[1]?candidate:best,corners[0]);ctx.textAlign='center';ctx.textBaseline='middle';
        const label=`Ø${fmt(dia)} mm`,textWidth=ctx.measureText(label).width,padding=3*dpr;
        ctx.fillStyle='rgba(11,17,20,.94)';ctx.fillRect(point[0]-textWidth/2-padding,point[1]-20*dpr,textWidth+padding*2,14*dpr);
        ctx.fillStyle='#ffb27e';ctx.fillText(label,point[0],point[1]-13*dpr);
      }
      ctx.restore();
    }
    let lastOverlaySignature=null;
    function syncOverlay(){let c;try{c=modelViewer?.getCamera?.()}catch{}const r=panel.getBoundingClientRect(),sig=state.scene&&c?.orientation?`${state.project}|${state.rev}|${state.selected||''}|${state.selectedOccurrenceId||''}|${state.assemblyCoordinateMode}|${state.lock?.state||''}|${r.width|0}|${r.height|0}|${c.zoom}|${c.panX}|${c.panY}|${c.orientation.join(',')}`:'';if(sig===lastOverlaySignature)return;lastOverlaySignature=sig;drawOverlay()}
    window.addEventListener('cadyx:viewport-rendered',syncOverlay);
    window.addEventListener('resize',syncOverlay);
    syncOverlay();
    function triangleDepth(px, py, ax, ay, az, bx, by, bz, cx, cy, cz) {
      const v0x = cx - ax, v0y = cy - ay;
      const v1x = bx - ax, v1y = by - ay;
      const v2x = px - ax, v2y = py - ay;
      const d00 = v0x * v0x + v0y * v0y;
      const d01 = v0x * v1x + v0y * v1y;
      const d02 = v0x * v2x + v0y * v2y;
      const d11 = v1x * v1x + v1y * v1y;
      const d12 = v1x * v2x + v1y * v2y;
      const denominator = d00 * d11 - d01 * d01;
      if (Math.abs(denominator) < 1e-12) return null;
      const u = (d11 * d02 - d01 * d12) / denominator;
      const v = (d00 * d12 - d01 * d02) / denominator;
      if (u < 0 || v < 0 || u + v > 1) return null;
      return (1 - u - v) * az + v * bz + u * cz;
    }
    function pick(e) {
      const projection = projectSetup(), rect = canvas.getBoundingClientRect();
      if (!projection || !rect.width) return;
      const x = (e.clientX - rect.left) * canvas.width / rect.width;
      const y = (e.clientY - rect.top) * canvas.height / rect.height;
      let best = null;
      for (const mesh of (state.scene?.objects||[]).filter(item=>item.type==='body')) {
        if (!mesh?.triangles?.length) continue;
        const projected = new Float64Array(mesh.positions.length);
        for (let vertex = 0; vertex < mesh.positions.length; vertex += 3) {
          const point = projection.p(mesh.positions[vertex], mesh.positions[vertex + 1], mesh.positions[vertex + 2]);
          projected[vertex] = point[0];
          projected[vertex + 1] = point[1];
          projected[vertex + 2] = point[2];
        }
        const triangles = mesh.triangles;
        for (let face = 0; face < triangles.length; face += 3) {
          const a = triangles[face] * 3, b = triangles[face + 1] * 3, c = triangles[face + 2] * 3;
          const ax = projected[a], ay = projected[a + 1];
          const bx = projected[b], by = projected[b + 1];
          const cx = projected[c], cy = projected[c + 1];
          if (x < Math.min(ax, bx, cx) || x > Math.max(ax, bx, cx)
              || y < Math.min(ay, by, cy) || y > Math.max(ay, by, cy)) continue;
          const depth = triangleDepth(x, y, ax, ay, projected[a + 2],
            bx, by, projected[b + 2], cx, cy, projected[c + 2]);
          if (depth !== null && (!best || depth > best.depth)) {
            const triangleIndex=Math.floor(face/3),faceRange=(mesh.face_triangle_ranges||[]).find(range=>triangleIndex>=range.start&&triangleIndex<range.end);
            best={mesh,depth,faceIndex:faceRange?.face_index};
          }
        }
      }
      if (!best) return;
      if(state.assemblyFacePickMode){if(best.mesh.instance_path?.length&&(state.assemblyPickKind==='vertex'||Number.isInteger(best.faceIndex)))void pickAssemblyGeometryFace(best.mesh,best.faceIndex,e,best.depth);else setAssemblyStatus('Выберите геометрию B-rep размещённой детали.');return}
      if(best.mesh.instance_path?.length){selectAssemblyOccurrence(best.mesh.object_id,best.mesh.instance_id,e);return}
      const body=state.byId.get(best.mesh.source_body_id||best.mesh.object_id);
      if(!body)return;
      const featureId = [...(body.feature_ids || [])].reverse()
        .find(id => state.byId.get(id)?.type === 'feature');
      if (featureId) select(featureId, true);else select(body.id,true);
    }
    function previewAssemblyTransform(instance,localPose){
      const affected=new Map();
      for(const target of assemblyTransformTargets(instance,localPose)){
        const moved=assemblyInstance(target.instance_id),path=target.path,parent=parentPoseForPath(path),oldWorld=poseCompose(parent,instancePose(moved)),nextWorld=poseCompose(parent,{translation:target.translation,rotation:target.rotation}),delta=poseCompose(nextWorld,poseInverse(oldWorld));
        const component=assemblyComponentIds(moved.id),prefix=path.slice(0,-1),memberIndex=prefix.length,parentId=moved.parent_id;
        for(const item of state.scene?.objects||[]){const occurrencePath=item.instance_path;if(item.type!=='body'||!Array.isArray(occurrencePath)||occurrencePath.length<=memberIndex||!component.has(occurrencePath[memberIndex]))continue;
          const sameParent=memberIndex===0?parentId===null:assemblyInstance(occurrencePath[memberIndex-1])?.target_id===parentId;
          if(sameParent)affected.set(item.object_id,delta)}
      }
      if(!affected.size||!currentModelViewportGeometry?.bodyVertexRanges)return;
      const positions=Float64Array.from(currentModelViewportGeometry.positions);
      for(const range of currentModelViewportGeometry.bodyVertexRanges){const delta=affected.get(range.objectId);if(!delta)continue;for(let vertex=range.start;vertex<range.end;vertex++){const offset=vertex*3,point=rotateVector(delta.rotation,[positions[offset],positions[offset+1],positions[offset+2]]);positions[offset]=point[0]+delta.translation[0];positions[offset+1]=point[1]+delta.translation[1];positions[offset+2]=point[2]+delta.translation[2]}}
      const bounds={min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity]};
      for(let offset=0;offset<positions.length;offset+=3)for(let axis=0;axis<3;axis++){bounds.min[axis]=Math.min(bounds.min[axis],positions[offset+axis]);bounds.max[axis]=Math.max(bounds.max[axis],positions[offset+axis])}
      const preview={...currentModelViewportGeometry,positions,bounds};const viewport=$('#viewport');if(viewport)viewport.dataset.assemblyPreview='true';
      modelViewer?.load(preview,{reset:false,smoothShading:true});for(const view of orthographicViewers)view.viewer.load(preview,{reset:false});
    }
    function clearAssemblyPreview(){const viewport=$('#viewport');if(viewport)delete viewport.dataset.assemblyPreview;const geometry=currentModelViewportGeometry;if(geometry){modelViewer?.load(geometry,{reset:false,smoothShading:true});for(const view of orthographicViewers)view.viewer.load(geometry,{reset:false})}assemblyDrag=null;lastOverlaySignature=null;syncOverlay()}
    function startAssemblyGizmoDrag(event){
      const button=event.target.closest('button[data-gizmo-kind]'),instance=assemblyInstance(state.selectedInstanceId);if(!button||!instance)return;
      if(button.disabled)return;event.preventDefault();event.stopPropagation();
      const targets=assemblyTransformTargets(instance,instancePose(instance));if(targets.some(target=>[...assemblyComponentIds(target.instance_id)].some(id=>assemblyInstance(id)?.grounded))){setAssemblyStatus('Выбранная группа содержит закреплённую деталь. Меняйте координату допустимого сопряжения.');return}
      const mode=state.assemblyCoordinateMode,shownPose=selectedPose(instance,mode),parent=parentPoseForPath(state.selectedInstancePath),world=poseCompose(parent,instancePose(instance)),projection=projectSetup();if(!projection)return;
      const kind=button.dataset.gizmoKind,axis=button.dataset.axis,index={X:0,Y:1,Z:2}[axis],basis=index===0?[1,0,0]:index===1?[0,1,0]:[0,0,1],worldAxis=mode==='local'?rotateVector(world.rotation,basis):basis;
      const origin=projection.p(...world.translation),end=projection.p(world.translation.map((value,i)=>value+worldAxis[i])),axisScreen=[end[0]-origin[0],end[1]-origin[1]],lengthSquared=axisScreen[0]**2+axisScreen[1]**2;
      if(kind==='translate'&&lengthSquared<1e-10)return;
      assemblyDrag={pointerId:event.pointerId,button,instance,mode,kind,axis,index,basis,worldAxis,
        startClientX:event.clientX,startClientY:event.clientY,origin,axisScreen,lengthSquared,dpr:Math.min(devicePixelRatio||1,2),shownPose,startShownPose:cloneJson(shownPose),
        currentLocal:instancePose(instance),nextLocal:instancePose(instance),moved:false};
      try{button.setPointerCapture(event.pointerId)}catch{}
    }
    function moveAssemblyGizmo(event){
      const drag=assemblyDrag;if(!drag||drag.pointerId!==event.pointerId)return;
      event.preventDefault();const dx=(event.clientX-drag.startClientX)*drag.dpr,dy=(event.clientY-drag.startClientY)*drag.dpr,next={translation:drag.startShownPose.translation.slice(),rotation:drag.startShownPose.rotation.slice()};
      if(drag.kind==='translate'){
        let amount=(dx*drag.axisScreen[0]+dy*drag.axisScreen[1])/drag.lengthSquared;
        const step=Number(props.querySelector('[data-assembly-step]')?.value);if(state.snapEnabled&&step>0)amount=Math.round(amount/step)*step;
        if(drag.mode==='local')next.translation[drag.index]+=amount;else next.translation[drag.index]+=amount;
      }else{
        let degrees=dx*0.5;const angleStep=Number(props.querySelector('[data-assembly-angle-step]')?.value);if(state.snapEnabled&&angleStep>0)degrees=Math.round(degrees/angleStep)*angleStep;
        const half=degrees*Math.PI/360,delta=[Math.cos(half),...(drag.index===0?[Math.sin(half),0,0]:drag.index===1?[0,Math.sin(half),0]:[0,0,Math.sin(half)])];
        next.rotation=drag.mode==='global'?quatMultiply(delta,drag.startShownPose.rotation):quatMultiply(drag.startShownPose.rotation,delta);
      }
      drag.nextLocal=localPoseFromMode(next,drag.mode);drag.shownPose=next;drag.moved=Math.hypot(dx,dy)>=2;
      if(drag.moved)previewAssemblyTransform(drag.instance,drag.nextLocal);
      lastOverlaySignature=null;syncOverlay();
    }
    async function finishAssemblyGizmo(event,commit){
      const drag=assemblyDrag;if(!drag||event.pointerId!==undefined&&drag.pointerId!==event.pointerId)return;
      assemblyDrag=null;
      const poseChanged=drag.nextLocal.translation.some((value,index)=>Math.abs(value-drag.currentLocal.translation[index])>1e-9)||drag.nextLocal.rotation.some((value,index)=>Math.abs(value-drag.currentLocal.rotation[index])>1e-9);
      if(commit&&drag.moved&&poseChanged){try{await commitAssemblyTransform(drag.instance,drag.nextLocal)}catch{}}
      clearAssemblyPreview();
    }
    gizmoRoot.addEventListener('pointerdown',startAssemblyGizmoDrag);
    gizmoRoot.addEventListener('pointermove',moveAssemblyGizmo);
    gizmoRoot.addEventListener('pointerup',event=>void finishAssemblyGizmo(event,true));
    gizmoRoot.addEventListener('pointercancel',event=>void finishAssemblyGizmo(event,false));
    let down=null;
    canvas.addEventListener('pointerdown',e=>{if(e.button===0)down={x:e.clientX,y:e.clientY,pointerId:e.pointerId}},true);
    canvas.addEventListener('pointermove',e=>{if(down?.pointerId===e.pointerId&&Math.hypot(e.clientX-down.x,e.clientY-down.y)>=6)down=null},true);
    canvas.addEventListener('pointerup',e=>{if(down?.pointerId!==e.pointerId)return;const click=e.button===0&&Math.hypot(e.clientX-down.x,e.clientY-down.y)<6;down=null;if(click)pick(e)},true);
    canvas.addEventListener('pointercancel',()=>{down=null},true);
    const toolbar=$('.model-toolbar'),presets=$('#viewport-camera-presets'),fit=$('#viewport-fit'),four=$('#viewport-four-views'),divider=$('.viewport-toolbar-divider'),liveScan=$('#viewport-live-scan'),scanMesh=$('#viewport-scan-mesh'),liveCamera=$('#viewport-live-camera'),scanViewport=$('#viewport'),scan360=$('#viewport-scan-360'),scanStatus=$('#viewport-scan-360-status');if(toolbar&&presets&&fit&&four&&divider){detail.dataset.cadTool='orbit';fit.innerHTML=`${I.fit}<span class="toolbar-label">Вписать</span>`;fit.classList.add('cad3-tool');four.classList.add('cad3-tool');liveScan?.classList.add('cad3-tool');scanMesh?.classList.add('cad3-tool');liveCamera?.classList.add('cad3-tool');toolbar.replaceChildren(fit,four,divider,presets,...(liveScan?[liveScan]:[]),...(scanMesh?[scanMesh]:[]),...(scan360?[scan360]:[]),...(liveCamera?[liveCamera]:[]),...(scanStatus?[scanStatus]:[]))}
    const scanParams=new URLSearchParams(location.search),scanMeshReady=scanParams.get('scan_mesh_ready')==='1'&&scanParams.get('scan_kind')!=='provisional_fusion';
    const scanView=liveScan&&scanMesh&&scanViewport?window.CadyxScanView?.initialize({cloudButton:liveScan,meshButton:scanMesh,viewport:scanViewport,viewer:modelViewer,getModelGeometry:()=>currentModelViewportGeometry,getProjectId:()=>window.cadyxSelectedProjectId?.(),hasPersistentPointCloud:file=>state.objects.some(o=>o.type==='feature'&&o.operation==='import_point_cloud'&&o.source?.project_file===file),meshReady:scanMeshReady}):null;
    const syncScanningTurntable=()=>{
      if(grid?.dataset.view==='scanning')scanView?.showTurntable();
      else{
        if(scanTreePreviewSelection)clearScanTreePreview();
        scanView?.hideTurntable();
      }
    };
    syncScanningTurntable();
    new MutationObserver(syncScanningTurntable).observe(grid,{attributes:true,attributeFilter:['data-view']});
    liveCamera?.addEventListener('click',()=>{if(scanTreePreviewSelection)clearScanTreePreview()},{capture:true});
    if(liveCamera&&scanView)window.CadyxScanView?.initializeLivePreview({button:liveCamera,view:scanView,captureButton:scan360});
    window.CadyxTurntableControls?.initialize();
    if(scan360&&scanStatus){
      let pollTimer=null,scanWasRunning=false;
      const projectId=()=>window.cadyxSelectedProjectId?.();
      const syncCaptureButton=()=>{
        const current=document.querySelector('#viewport-scan-360');
        if(!current)return;
        const disabled=current.dataset.scanCaptureBusy==='true'||current.dataset.scanCapturePending==='true'||current.dataset.livePreviewLock==='true'||current.dataset.turntableBusy==='true';
        current.dataset.captureLocked=String(disabled);
        current.setAttribute('aria-disabled',String(disabled));
      };
      window.cadyxSyncCaptureButton=syncCaptureButton;
      const setCaptureBusy=busy=>{scan360.dataset.scanCaptureBusy=String(busy);syncCaptureButton()};
      scan360.dataset.scanCaptureBusy='false';syncCaptureButton();
      const showCapturePreview=(scan,options)=>scanTreePreviewSelection?null:scanView?.showCapture(scan,options);
      const pollScan=async id=>{
        if(id!==projectId()){scanWasRunning=false;waitForProjectId();return}
        if(document.visibilityState==='hidden'||grid?.dataset.view!=='scanning'){
          pollTimer=setTimeout(()=>pollScan(id),2500);return;
        }
        try{
          const response=await fetch(`/api/v1/agent/projects/${encodeURIComponent(id)}/scans/360`,{cache:'no-store'});
          const payload=await response.json();
          if(id!==projectId()){scanWasRunning=false;waitForProjectId();return}
          if(!response.ok)throw new Error(payload.error?.message||payload.message||'Не удалось получить статус съёмки');
          const scan=payload.scan;
          if(scan?.preview_file)latestScanPreview={projectId:id,scan};
          const captureStarted=scan.state==='running'&&!scanWasRunning;
          scanWasRunning=scan.state==='running';
          if(!scanTreePreviewSelection)scanStatus.textContent=scan.state==='idle'||scan.state==='completed'?'':scan.message||`${scan.completed_frames}/${scan.frame_count}`;
          setCaptureBusy(scan.state==='running'||scan.state==='busy');
          if(scan.state==='running'){
            showCapturePreview(scan,{open:captureStarted});
            pollTimer=setTimeout(()=>pollScan(id),1200);
          }else if(scan.state==='completed'){
            showCapturePreview(scan);
            pollTimer=setTimeout(()=>pollScan(id),4000);
          }else if(scan.state==='failed'){
            if(scan.completed_frames>0)showCapturePreview(scan);
            pollTimer=setTimeout(()=>pollScan(id),4000);
          }else{
            pollTimer=setTimeout(()=>pollScan(id),2500);
          }
        }catch(error){
          syncCaptureButton();
          if(!scanTreePreviewSelection)scanStatus.textContent=error.message;
          pollTimer=setTimeout(()=>pollScan(id),4000);
        }
      };
      const waitForProjectId=()=>{const id=projectId();if(id)pollScan(id);else pollTimer=setTimeout(waitForProjectId,500)};
      scan360.addEventListener('click',async()=>{
        if(scan360.dataset.captureLocked==='true')return;
        const id=projectId();
        if(!id){scanStatus.textContent='Сначала выберите проект.';return}
        if(scanTreePreviewSelection)clearScanTreePreview();
        scan360.dataset.scanCapturePending='true';syncCaptureButton();scanStatus.textContent='Запускаю съёмку…';
        try{
          const response=await fetch(`/api/v1/agent/projects/${encodeURIComponent(id)}/scans/360`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rotation_period_seconds:window.CadyxDeviceSettings?.turntableRotationPeriodSeconds?.()??18,scanner_device_id:window.CadyxDeviceSettings?.scannerDeviceId?.(),turntable_device_id:window.CadyxDeviceSettings?.turntableDeviceId?.()})});
          const payload=await response.json();
          if(id!==projectId()){scanWasRunning=false;waitForProjectId();return}
          if(!response.ok)throw new Error(payload.error?.message||payload.message||'Не удалось запустить сканирование');
          showCapturePreview(payload.scan,{open:true});
          scanWasRunning=payload.scan.state==='running';
          setCaptureBusy(payload.scan.state==='running'||payload.scan.state==='busy');
          scanStatus.textContent='Кадр 0/12';
          clearTimeout(pollTimer);pollScan(id);
        }catch(error){
          setCaptureBusy(false);
          scanStatus.textContent=error.message;
        }finally{
          scan360.dataset.scanCapturePending='false';syncCaptureButton();
        }
      });
      waitForProjectId();
    }
    let semanticRequest = 0;
    const semanticProjectId = () => window.cadyxSelectedProjectId?.() || new URLSearchParams(location.search).get('project_id');
    async function semanticJson(url) {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
      return response.json();
    }
    async function refresh(){
      const request=++semanticRequest,id=semanticProjectId();if(!id||detail.hidden)return;
      try{
        const snapshot=window.cadyxModelSnapshot;if(!snapshot||snapshot.projectId!==id)return;
        const {info,scene}=snapshot;if(request!==semanticRequest||id!==semanticProjectId()||detail.hidden)return;
        state.lock=info.lock||state.lock;state.kinematics=info.assembly_kinematics||state.kinematics;
        if(state.project===id&&state.rev===info.document.revision){if(state.selectedInstanceId)renderAssemblyProps();return}
        if(scene.revision!==info.document.revision){schedule();return}
        const projectChanged=state.project!==id,revisionChanged=!projectChanged&&state.rev!==info.document.revision;state.project=id;state.rev=info.document.revision;
        if(projectChanged||revisionChanged){assemblyTopologyCache.clear();state.assemblyFacePicks=[];state.assemblyFacePickMode=false;modelViewer?.setSelectedFaces?.([])}
        state.objects=info.document.objects||[];state.parameters=info.document.model_parameters||{};
        state.bindings=info.document.parameter_bindings||[];state.assembly=info.document.assembly||null;
        state.byId=new Map(state.objects.map(o=>[o.id,o]));if(projectChanged)restoreModelTreeState(id);
        state.scene=scene;makeLabels();
        if(projectChanged||(state.selected&&!state.byId.has(state.selected))||(state.selectedInstanceId&&!assemblyInstance(state.selectedInstanceId))){clearSelection();return}
        renderTree();
        if(state.selected)select(state.selected);
        else if(state.selectedInstanceId){modelViewer?.setSelectedBodies?.([...state.selectedOccurrenceIds]);renderAssemblyProps()}
        else renderProps(null);
        syncOverlay();
      }catch(e){console.debug('CAD semantic refresh',e)}
    }
    let timer;const schedule=()=>{clearTimeout(timer);timer=setTimeout(refresh,80)};['#document-revision','#document-title'].forEach(s=>{const e=$(s);if(e)new MutationObserver(schedule).observe(e,{childList:true,subtree:true,characterData:true})});new MutationObserver(schedule).observe(detail,{attributes:true,attributeFilter:['hidden']});window.addEventListener('cadyx:model-tree-updated',refresh);window.addEventListener('cadyx:project-lock',event=>{if(event.detail?.projectId!==state.project)return;state.lock=event.detail.lock;if(state.lock?.state!=='FREE'||state.lock?.recovery_required){if(assemblyDrag){const pointerId=assemblyDrag.pointerId;void finishAssemblyGizmo({pointerId},false).then(()=>window.cadyxApi?.refresh?.()).catch(error=>setAssemblyStatus(`Не удалось обновить сборку после отмены: ${error.message}`))}}renderTree();if(state.selectedInstanceId)renderAssemblyProps()});schedule();
  }
  const boot=()=>setTimeout(enhance,0);if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
