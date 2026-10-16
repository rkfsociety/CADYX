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
  const meta={create_box:['Блок','model'],metric_thread:['Резьба','thread'],reconstruct_scan:['Реконструкция скана','model'],import_step:['Импорт STEP','model'],extrude_sketch:['Выдавливание','model'],revolve_sketch:['Вращение','model'],loft_sketches:['Переход по сечениям','model'],loft_profiles:['Переход по сечениям','model'],create_ruled_shell:['Линейчатая оболочка','model'],fuse_bodies:['Объединение тел','model'],cut_bodies:['Вырезание','cut'],sweep_sketch:['Протяжка','model'],fillet_edges:['Скругление','fillet'],variable_fillet_edges:['Переменное скругление','fillet'],chamfer_edges:['Фаска','model'],shell_body:['Оболочка','model'],offset_body:['Смещение поверхности','model'],draft_faces:['Уклон граней','model'],mirror_body:['Зеркальное отражение','model'],linear_pattern_body:['Линейный массив','model'],circular_pattern_body:['Круговой массив','model'],boss:['Выступ','model'],hole:['Отверстие','cut'],through_hole:['Сквозное отверстие','cut'],counterbore_hole:['Отверстие с цековкой','cut'],countersink_hole:['Отверстие с зенковкой','cut'],threaded_hole:['Резьбовое отверстие','cut'],pad_sketch_to_body:['Выдавливание','model'],rib_from_sketch:['Ребро жёсткости','model'],pocket_sketch:['Карман','cut'],extrude_profile_on_plane:['Выдавливание профиля','model'],resize_imported_thread:['Изменение резьбы','thread'],transform_imported_brep:['Преобразование тела','model']};
  const editable={create_box:['width','height','depth'],metric_thread:['major_diameter','pitch','length'],extrude_sketch:['depth'],revolve_sketch:['angle_degrees','outer_diameter','height'],sweep_sketch:['radius'],fillet_edges:['radius']};
  const pLabel={width:'Ширина',height:'Высота',depth:'Глубина',outer_diameter:'Наружный диаметр',major_diameter:'Номинальный диаметр',pitch:'Шаг',length:'Длина',angle_degrees:'Угол',radius:'Радиус',centered:'По центру',handedness:'Направление резьбы',diameter_delta:'Изменение диаметра',rotation_axis:'Ось вращения',translation:'Перемещение',section_offsets:'Смещения сечений',sewing_tolerance:'Допуск сшивания'};
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
.cad3-tree-node{grid-template-columns:20px minmax(0,1fr);gap:0 4px}.cad3-tree-parent-row{grid-template-columns:20px minmax(0,1fr);gap:4px}.cad3-tree-disclosure{width:20px;height:38px;color:#a5b0b6;font:500 16px/1 var(--ui-font)}.cad3-tree-disclosure:hover{border-color:#51331f;border-radius:5px;background:#ffffff08;color:#ff9653}.cad3-tree-disclosure:disabled{visibility:hidden}.cad3-tree-disclosure[aria-expanded=true] span{transform:rotate(45deg)}.cad3-tree-disclosure span{transition:transform .12s ease}.cad3-tree-parent-row .cad-tree-feature,.cad3-sketch-child{grid-template-columns:22px minmax(0,1fr) auto;gap:8px}.cad3-tree-parent-row .cad3-icon{color:#aeb9bf}.cad3-tree-children{grid-column:1/-1;display:grid;gap:4px;margin-left:24px;padding-left:9px;border-left:1px solid #32404a}.cad3-tree-children[hidden]{display:none!important}.cad3-sketch-child{color:#b8c3c9}.cad3-sketch-child .cad3-icon{color:#7ebdc5}
.cad-tree-feature{min-height:38px;padding:7px 10px;border-radius:6px;font-size:12px}.cad-tree-feature:hover{background:#ffffff09;color:#eef2f4}.cad-tree-feature[data-selected=true]{border-color:#a34c1c!important;background:linear-gradient(100deg,#3d1d0e,#21140e)!important;box-shadow:inset 0 0 0 1px #713817;color:#fff}
.cad3-tree-type{margin-left:auto;color:#647078;font-size:8px}
:root{--cadyx-rail-width:56px;--cadyx-tree-width:clamp(242px,17vw,292px);--cadyx-inspector-width:clamp(310px,21vw,372px)}
.topbar{padding-left:74px!important}.cad3-headrail{position:absolute;inset:0 auto 0 0;width:56px;display:grid;place-items:center;border-right:1px solid var(--line);background:#0a1014;color:var(--accent)}.cad3-headrail svg{width:21px}
.project-detail{grid-template-columns:56px var(--cadyx-tree-width) minmax(0,1fr)!important}.project-detail[data-tree-collapsed=true]{grid-template-columns:56px 48px minmax(0,1fr)!important}.workspace-navigation{grid-column:2/-1!important;padding-left:18px!important}.section-sidebar{grid-column:2!important}.project-main{grid-column:3!important}
.cad3-rail{grid-column:1;grid-row:1/3;display:flex;flex-direction:column;align-items:center;gap:7px;padding:10px 7px;border-right:1px solid var(--line);background:#0a1014;z-index:9}.cad3-rail button{display:grid;place-items:center;width:40px;height:40px;border:1px solid transparent;border-radius:5px;background:transparent;color:#87939b}.cad3-rail button svg,.cad3-icon svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.cad3-rail button[data-on=true]{color:var(--accent);background:#351a0d}.cad3-rail button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}.cad3-rail button:disabled{opacity:.35;cursor:not-allowed}
.cad-tree-icon{font-size:0!important}.cad-tree-icon svg,.cad3-icon svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.6}.cad3-tree-type{margin-left:auto;color:#647078;font-size:8px}.cad-tree-feature[data-selected=true]{border-color:#773917!important;background:linear-gradient(90deg,#45200e,#21140d)!important}.cad3-props{grid-row:2;min-height:0;overflow:auto;color:var(--muted);font:10px/1.5 var(--mono)}.cad3-props[hidden]{display:none!important}.cad3-empty{padding:18px;color:var(--dim)}.cad3-head{display:grid;grid-template-columns:34px 1fr;gap:9px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line-soft)}.cad3-head .cad3-icon{display:grid;place-items:center;width:32px;height:32px;border:1px solid #62391f;border-radius:5px;color:var(--accent);background:#201209}.cad3-head strong{display:block;color:#eef2f4;font-size:12px}.cad3-head small{color:var(--dim);font-size:8px}.cad3-sec{padding:12px 14px 14px;border-bottom:1px solid var(--line-soft)}.cad3-sec h3{margin:0 0 9px;color:#e1e6e9;font-size:10px}.cad3-grid{display:grid;grid-template-columns:minmax(98px,.9fr) minmax(0,1.1fr);border:1px solid #252f36;border-radius:3px;overflow:hidden}.cad3-grid dt,.cad3-grid dd{min-height:30px;margin:0;padding:7px 9px;border-bottom:1px solid #222c33}.cad3-grid dt{color:#89949b;background:#0b1217}.cad3-grid dd{color:#d7dde0;border-left:1px solid #222c33;background:#0d1419;word-break:break-word}.cad3-grid input{width:100%;height:24px;border:1px solid #35414a;border-radius:3px;background:#080e12;color:#eef1f3;font:9px var(--mono)}.cad3-apply{width:100%;height:30px;margin-top:9px;border:1px solid #7c3e19;border-radius:4px;background:#291409;color:#ff9653;font:700 9px var(--mono)}.cad3-dep{display:flex;width:100%;align-items:center;gap:8px;padding:7px;border:1px solid transparent;background:transparent;color:#b9c1c6;text-align:left}.cad3-dep:hover{border-color:#56351f;background:#ff741708}.cad3-dep small{margin-left:auto;color:#68747c}.cad3-valid{color:#63d89d}
.primary-grid[data-view=overview] #object-panel{position:absolute!important;width:1px!important;height:1px!important;opacity:0!important;pointer-events:none!important}.cad3-overlay{position:absolute;z-index:6;inset:0;width:100%;height:100%;pointer-events:none}.project-detail[data-cad-tool=orbit] #model-preview{cursor:grab}.project-detail[data-v2-selection=true] .model-panel:after{display:none!important}.cad-v2-selection-chip{display:none!important}
.cad3-tool{display:flex;align-items:center;gap:6px;height:30px;padding:0 8px;border:1px solid transparent;border-radius:4px;background:transparent;color:#a8b3b9;font:9px var(--mono)}.cad3-tool svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.7}.cad3-tool[data-on=true]{border-color:#83431d;color:var(--accent);background:#3a1b0d}.cad3-tool:disabled{opacity:.35}.cad3-views{position:relative}.cad3-views summary{list-style:none;cursor:pointer}.cad3-views #viewport-camera-presets{position:absolute;top:36px;left:0;display:grid!important;grid-template-columns:repeat(4,32px);padding:5px;border:1px solid var(--line);border-radius:5px;background:#0d1419;box-shadow:0 8px 24px #0008}.cad3-views:not([open]) #viewport-camera-presets{display:none!important}
@media(max-width:920px){.primary-grid[data-view=overview]>.inspector-column{display:none!important}}
body:has(#primary-grid[data-view="projects"]) .topbar{grid-template-columns:auto minmax(0,1fr) auto!important;grid-template-areas:"brand project sync"!important;gap:0;padding-right:22px!important;border-bottom:0!important}
body:has(#primary-grid[data-view="projects"]) .topbar-project{justify-self:end;min-width:max-content;align-items:center;gap:12px;padding:0;border:0}
body:has(#primary-grid[data-view="projects"]) .agent-owner-chip{display:inline-flex;height:32px;align-items:center;justify-content:center;box-sizing:border-box;line-height:1;white-space:nowrap}
body:has(#primary-grid[data-view="projects"]) .topbar-project>.project-light,body:has(#primary-grid[data-view="projects"]) .topbar-project>.topbar-project-copy{display:none!important}
body:has(#primary-grid[data-view="projects"]) .project-detail{grid-template-columns:56px minmax(0,1fr)!important}
body:has(#primary-grid[data-view="projects"]) .section-sidebar{display:none!important}
body:has(#primary-grid[data-view="projects"]) .project-main{grid-column:2!important}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation{position:fixed!important;z-index:24;top:0;right:0;left:192px;width:auto;height:58px;min-height:58px;align-items:stretch;gap:0;overflow-x:auto;overflow-y:hidden;padding:0;background:transparent;border:0;grid-column:auto!important;scrollbar-width:none}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-item{flex:0 0 auto;width:auto;min-width:0;height:58px;min-height:58px;justify-content:center;padding:0 18px;border:0;border-bottom:3px solid transparent;border-radius:0;background:transparent;text-decoration:none}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-copy strong{color:#c8d0d5;font:600 12px/1.2 var(--ui-font);letter-spacing:0;text-transform:none;white-space:nowrap}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation .nav-item:hover:not(:disabled) .nav-copy strong{color:#fff}
body:has(#primary-grid[data-view="projects"]) #workspace-navigation a[href="/scanner"]{border-bottom-color:var(--accent)}
body:has(#primary-grid[data-view="projects"]) .project-detail{grid-template-rows:0 minmax(0,1fr) var(--cadyx-ai-height)!important}
body:has(#primary-grid[data-view="projects"]) .section-sidebar,body:has(#primary-grid[data-view="projects"]) .project-main{grid-row:2!important}
.brand{gap:9px}
.brand-mark{display:block;width:25px;height:25px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
#projects-panel{grid-template-rows:auto minmax(0,1fr) auto!important;border:0;border-radius:0!important;background:transparent}
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
.project-group-cards{grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:16px}
.workspace-project-card{position:relative;grid-template-rows:208px auto auto auto;gap:13px;min-width:0;padding:13px;border:1px solid #26343e;border-radius:12px;background:linear-gradient(145deg,#121c23,#0f171d);box-shadow:0 8px 24px #0002;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
#projects-panel[data-layout="grid"] .workspace-project-card{aspect-ratio:1;grid-template-rows:minmax(140px,1fr) auto auto auto;min-height:0}
#projects-panel[data-layout="grid"] .workspace-project-thumbnail{height:auto;min-height:0;overflow:hidden}
.workspace-project-card:hover{border-color:#596b74;box-shadow:0 14px 32px #0004;transform:translateY(-1px)}
.workspace-project-card[data-active="true"]{border-color:#a85e32;background:linear-gradient(145deg,#201912,#12171b);box-shadow:0 0 0 1px #ff742718,0 10px 28px #0004}
.workspace-project-thumbnail{position:relative;min-height:0;height:208px;border:1px solid #26343d;border-radius:9px;background-color:#0a1217;background-image:linear-gradient(#26323838 1px,transparent 1px),linear-gradient(90deg,#26323838 1px,transparent 1px),radial-gradient(ellipse at 50% 78%,#1c39302b,transparent 58%);background-size:20px 20px,20px 20px,auto}
.workspace-project-thumbnail img{width:100%;height:100%;object-fit:contain}
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
.workspace-project-open:hover{border-color:#ff9a5e;background:#ff8a43;color:#17100c}
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
@media(max-width:680px){:root{--cadyx-rail-width:48px;--cadyx-tree-width:52px}.project-detail{grid-template-columns:48px 52px minmax(0,1fr)!important}.project-detail[data-tree-collapsed=true]{grid-template-columns:48px 48px minmax(0,1fr)!important}.cad3-rail{gap:4px;padding:8px 4px}.cad3-rail button{width:36px;height:36px}}
`;
    document.head.appendChild(style);
    if(!$('.cad3-headrail')){const h=document.createElement('div');h.className='cad3-headrail';h.innerHTML=I.model;$('.topbar')?.prepend(h)}
    if(!$('.cad3-rail')){const r=document.createElement('nav');r.className='cad3-rail';r.setAttribute('aria-label','Основная навигация');r.innerHTML=`<button data-view="geometry" title="Геометрия">${I.model}</button><button data-view="files" title="Файлы">${I.files}</button><button data-view="projects" title="Проекты">${I.projects}</button><button data-view="activity" title="Активность">${I.activity}</button>`;detail.prepend(r);const syncRail=()=>{$$('[data-view]',r).forEach(b=>{const view=b.dataset.view,src=nav.querySelector(`[data-workspace-view="${view}"]`),active=view===(grid.dataset.view||'overview');b.setAttribute('aria-label',b.title);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');b.dataset.on=String(active);b.disabled=!src||src.disabled})};r.addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(!b||b.disabled)return;nav.querySelector(`[data-workspace-view="${b.dataset.view}"]`)?.click()});new MutationObserver(syncRail).observe(grid,{attributes:true,attributeFilter:['data-view']});new MutationObserver(syncRail).observe(nav,{subtree:true,attributes:true,attributeFilter:['disabled','aria-current']});syncRail()}

    const oldList=$('#cad-tree-features'),oldSearch=$('.cad-tree-search input');let list=oldList,search=oldSearch;if(oldList){list=oldList.cloneNode(false);oldList.replaceWith(list)}if(oldSearch){search=oldSearch.cloneNode(true);oldSearch.replaceWith(search)}
    const staticIcons=$$('.cad-tree-static-row .cad-tree-icon');if(staticIcons[0])staticIcons[0].innerHTML=I.origin;staticIcons.slice(1).forEach(x=>x.innerHTML=I.plane);const rootIcon=$('.cad-tree-root-row .cad-tree-icon');if(rootIcon)rootIcon.innerHTML=I.model;
    let props=$('.cad3-props');if(!props){props=document.createElement('section');props.className='cad3-props cad-inspector-pane';inspector.append(props)}
    const overlay=document.createElement('canvas');overlay.className='cad3-overlay';panel.append(overlay);
    const state={objects:[],byId:new Map(),scene:null,labels:new Map(),selected:null,selectionCleared:false,rev:null,project:null};
    const opType=o=>meta[o?.operation]?.[0]||(o?.operation?`Операция: ${String(o.operation).replace(/_/g,' ')}`:'Элемент'), opIcon=o=>I[meta[o?.operation]?.[1]||'model'];
    function featureDetails(o){const p=o.parameters||{},fmt=v=>Number.isFinite(Number(v))?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(v)):null,mm=v=>{const n=fmt(v);return n===null?null:`${n} мм`},pos=Array.isArray(p.origin)?p.origin.slice(0,2).map((v,i)=>`${i?'Y':'X'}${fmt(v)}`).filter(x=>!x.endsWith('null')).join(', '):'';let d=[];switch(o.operation){case'create_box':d=[mm(p.width),mm(p.height),mm(p.depth)].filter(Boolean);return d.length?d.join(' × '):'';case'extrude_sketch':case'pad_sketch_to_body':case'rib_from_sketch':case'pocket_sketch':case'extrude_profile_on_plane':d=[mm(p.depth)];break;case'revolve_sketch':d=[p.angle_degrees==null?null:`${fmt(p.angle_degrees)}°`];break;case'boss':d=[p.diameter==null?null:`Ø${mm(p.diameter)}`,mm(p.depth)];break;case'hole':case'through_hole':case'counterbore_hole':case'countersink_hole':case'threaded_hole':d=[p.diameter==null?null:`Ø${mm(p.diameter)}`,p.hole_depth==null?null:mm(p.hole_depth),p.thread_size||null];break;case'fillet_edges':d=[p.radius==null?null:`R${mm(p.radius)}`];break;case'variable_fillet_edges':d=[p.start_radius==null?null:`R${mm(p.start_radius)}`,p.end_radius==null?null:`R${mm(p.end_radius)}`];break;case'chamfer_edges':d=[mm(p.distance)];break;case'shell_body':case'offset_body':d=[mm(p.thickness??p.offset)];break;case'draft_faces':d=[p.angle_degrees==null?null:`${fmt(p.angle_degrees)}°`];break;case'linear_pattern_body':case'circular_pattern_body':d=[p.count==null?null:`${fmt(p.count)} шт.`];break;default:return''}if(pos)d.push(pos);return d.filter(Boolean).join(' · ')}
    function makeLabels(){state.labels.clear();const count={sketch:0};const ops={};for(const o of state.objects){if(o.type==='sketch')state.labels.set(o.id,o.name&&o.name!=='Sketch'?o.name:`Эскиз ${String(++count.sketch).padStart(3,'0')}`);if(o.type==='feature'){const base=opType(o);ops[base]=(ops[base]||0)+1;const details=featureDetails(o);state.labels.set(o.id,o.name||`${base} ${String(ops[base]).padStart(3,'0')}${details?` · ${details}`:''}`)}}}
    const label=id=>state.labels.get(id)||state.byId.get(id)?.name||id?.split(':')[0]||'—';
    function consumed(){const s=new Set;for(const f of state.objects.filter(x=>x.type==='feature')){if(['fuse_bodies','cut_bodies'].includes(f.operation)){s.add(f.base_body_id);s.add(f.tool_body_id)}if(['fillet_edges','chamfer_edges','resize_imported_thread','transform_imported_brep'].includes(f.operation))s.add(f.source_body_id)}return s}
    const sceneBody=id=>state.scene?.objects?.find(x=>x.object_id===id);
    function deps(o){if(!o)return[];const d=[];const add=(id,role)=>{if(id&&state.byId.has(id))d.push({id,role})};add(o.source_sketch_id,'Профиль');(o.source_sketch_ids||[]).forEach(id=>add(id,'Сечение'));add(o.source_body_id,'Исходное тело');add(o.base_body_id,'Основное тело');add(o.tool_body_id,'Инструментальное тело');for(const x of state.objects){if(x===o||x.type!=='feature')continue;if(x.source_sketch_id===o.id||(x.source_sketch_ids||[]).includes(o.id)||x.source_body_id===o.body_id||x.base_body_id===o.body_id||x.tool_body_id===o.body_id)add(x.id,'Используется в')}return d}
    const expandedFeatures=new Set();
    function renderTree(){
      if(!list)return;
      const query=(search?.value||'').toLowerCase().trim();list.replaceChildren();
      const features=state.objects.filter(o=>o.type==='feature');
      for(const feature of features){
        const sketchIds=[...new Set([feature.source_sketch_id,...(feature.source_sketch_ids||[])].filter(id=>state.byId.get(id)?.type==='sketch'))];
        const sketchObjects=sketchIds.map(id=>state.byId.get(id));
        const featureName=label(feature.id),matchesFeature=!query||featureName.toLowerCase().includes(query);
        const matchingSketches=sketchObjects.filter(sk=>!query||label(sk.id).toLowerCase().includes(query));
        if(!matchesFeature&&matchingSketches.length===0)continue;
        const node=document.createElement('div');node.className='cad3-tree-node';
        const row=document.createElement('div');row.className='cad3-tree-parent-row';
        const expanded=expandedFeatures.has(feature.id)||(!matchesFeature&&matchingSketches.length>0);
        const disclosure=document.createElement('button');disclosure.type='button';disclosure.className='cad3-tree-disclosure';disclosure.disabled=!sketchObjects.length;disclosure.setAttribute('aria-label',expanded?'Свернуть исходные эскизы':'Показать исходные эскизы');disclosure.setAttribute('aria-expanded',String(expanded));disclosure.innerHTML='<span class="cad-tree-chevron">+</span>';
        disclosure.onclick=()=>{if(expandedFeatures.has(feature.id))expandedFeatures.delete(feature.id);else expandedFeatures.add(feature.id);renderTree()};
        const button=document.createElement('button');button.className='cad-tree-feature';button.type='button';button.dataset.id=feature.id;button.dataset.selected=String(feature.id===state.selected);button.innerHTML=`<span class="cad3-icon">${opIcon(feature)}</span><span class="cad-tree-label">${esc(featureName)}</span>`;button.onclick=()=>select(feature.id);
        row.append(disclosure,button);node.append(row);
        if(sketchObjects.length){const children=document.createElement('div');children.className='cad3-tree-children';children.hidden=!expanded;
          for(const sketch of sketchObjects){if(query&&!matchesFeature&&!matchingSketches.includes(sketch))continue;const child=document.createElement('button');child.className='cad-tree-feature cad3-sketch-child';child.type='button';child.dataset.id=sketch.id;child.dataset.selected=String(sketch.id===state.selected);child.innerHTML=`<span class="cad3-icon">${I.sketch}</span><span class="cad-tree-label">${esc(label(sketch.id))}</span>`;child.onclick=()=>select(sketch.id,true);children.append(child)}
          node.append(children);
        }
        list.append(node);
      }
      const empty=$('#cad-tree-empty');if(empty)empty.hidden=!!list.children.length;
    }
    search?.addEventListener('input',renderTree);
    function parameterHtml(o){const entries=Object.entries(o.parameters||{}).filter(([k])=>k!=='section_sketch_ids'&&k!=='section_sketch_groups');if(!entries.length)return'<dt>Параметры</dt><dd>Нет</dd>';return entries.map(([k,v])=>{const edit=(editable[o.operation]||[]).includes(k)&&typeof v==='number';const unit=k.includes('angle')?'°':typeof v==='number'?'мм':'';return`<dt>${esc(pLabel[k]||k)}</dt><dd>${edit?`<input data-p="${k}" data-original="${v}" value="${v}"> ${unit}`:esc(Array.isArray(v)?v.join(', '):v)}</dd>`}).join('')}
    function renderProps(o){if(!o){props.innerHTML='<div class="cad3-empty">Выберите элемент в дереве модели.</div>';return}if(o.type==='sketch'){props.innerHTML=`<div class="cad3-head"><span class="cad3-icon">${I.sketch}</span><div><strong>${esc(label(o.id))}</strong><small>Эскиз</small></div></div><section class="cad3-sec"><h3>Общие сведения</h3><dl class="cad3-grid"><dt>Плоскость</dt><dd>${esc(o.plane||'—')}</dd><dt>Состояние</dt><dd><span class="cad3-valid">● Корректно</span></dd></dl></section>`;return}const body=state.byId.get(o.body_id),mesh=sceneBody(o.body_id),m=o.source_metrics||{},sk=state.byId.get(o.source_sketch_id);const general=`<dt>Тип элемента</dt><dd>${esc(opType(o))}</dd><dt>Операция</dt><dd>${esc(opType(o))}</dd><dt>Состояние</dt><dd><span class="cad3-valid">● Корректно</span></dd><dt>Тело</dt><dd>${esc(body?.name||o.body_id||'—')}</dd>${sk?`<dt>Исходный эскиз</dt><dd>${esc(label(sk.id))}</dd><dt>Направление</dt><dd>Перпендикулярно плоскости ${esc(sk.plane||'эскиза')}</dd>`:''}`;const result=`<dt>Вершины</dt><dd>${fmt((mesh?.positions?.length||0)/3)}</dd><dt>Грани</dt><dd>${fmt((mesh?.triangles?.length||0)/3)}</dd>${Number.isFinite(m.volume)?`<dt>Объём</dt><dd>${fmt(m.volume)} мм³</dd>`:''}${Number.isFinite(m.surface_area)?`<dt>Площадь поверхности</dt><dd>${fmt(m.surface_area)} мм²</dd>`:''}`;const dependency=deps(o);props.innerHTML=`<div class="cad3-head"><span class="cad3-icon">${opIcon(o)}</span><div><strong>${esc(label(o.id))}</strong><small>${esc(opType(o))}</small></div></div><section class="cad3-sec"><h3>Общие сведения</h3><dl class="cad3-grid">${general}</dl></section><section class="cad3-sec"><h3>Параметры</h3><dl class="cad3-grid">${parameterHtml(o)}</dl>${(editable[o.operation]||[]).some(k=>typeof o.parameters?.[k]==='number')?'<button class="cad3-apply">ПРИМЕНИТЬ ПАРАМЕТРЫ</button>':''}</section><section class="cad3-sec"><h3>Результат</h3><dl class="cad3-grid">${result}</dl></section><section class="cad3-sec"><h3>Зависимости</h3>${dependency.length?dependency.map(d=>`<button class="cad3-dep" data-dep="${d.id}"><span>${esc(label(d.id))}</span><small>${esc(d.role)}</small></button>`).join(''):'<div class="cad3-empty">Нет зависимостей.</div>'}</section>`;props.querySelector('.cad3-apply')?.addEventListener('click',async()=>{const vals=[...props.querySelectorAll('input[data-p]')].map(i=>[i.dataset.p,{original:+i.dataset.original,next:+i.value}]).filter(([,v])=>Number.isFinite(v.next)&&v.next!==v.original);if(!vals.length)return;const fields=document.createElement('div');for(const [name,v] of vals){const i=document.createElement('input');i.dataset.parameter=name;i.dataset.originalValue=String(v.original);i.value=String(v.next);fields.append(i)}fields.append(document.createElement('button'));try{await applyGeometryDimensions(o.id,fields)}catch(e){console.error(e)}})}
    props.addEventListener('click',e=>{const b=e.target.closest('[data-dep]');if(b)select(b.dataset.dep,true)});
    const tabs=$('.cad-inspector-tabs');tabs?.addEventListener('click',()=>queueMicrotask(()=>{const active=$('.cad-inspector-tab[aria-selected=true]',tabs)?.dataset.cadInspector||'properties';props.hidden=active!=='properties'}));
    function select(id,reveal=false){if(!state.byId.has(id))return;const selected=state.byId.get(id);if(selected.type==='sketch'){const owner=state.objects.find(item=>item.type==='feature'&&(item.source_sketch_id===id||(item.source_sketch_ids||[]).includes(id)));if(owner)expandedFeatures.add(owner.id)}state.selected=id;state.selectionCleared=false;try{selectedObjectId=id}catch{}if(selected.type==='feature'&&['fillet_edges','variable_fillet_edges','chamfer_edges'].includes(selected.operation))modelViewer?.setSelectedFeature?.(selected.id);else modelViewer?.setSelectedBody?.(bodyFor(selected)?.id||null);renderTree();renderProps(selected);if(reveal)list?.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({block:'nearest'});detail.dataset.v2Selection='true';syncOverlay()}
    function clearSelection(){if(!state.selected&&state.selectionCleared)return;state.selected=null;state.selectionCleared=true;try{selectedObjectId=null}catch{}modelViewer?.setSelectedBody?.(null);renderTree();renderProps(null);detail.dataset.v2Selection='true';syncOverlay()}
    sidebar?.addEventListener('click',event=>{if(event.target.closest('button,input,select,textarea,a,[contenteditable="true"]'))return;clearSelection()},true);
    function bodyFor(o){if(!o)return null;if(o.type==='body')return o;return state.byId.get(o.body_id)||null}
    function projectSetup(){let viewer;try{viewer=modelViewer}catch{return null}const c=viewer?.getCamera?.();if(!c?.orientation||!state.scene)return null;const meshes=state.objects.filter(o=>o.type==='body').map(o=>sceneBody(o.id)).filter(Boolean);if(!meshes.length)return null;const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];for(const m of meshes)for(let i=0;i<m.positions.length;i+=3)for(let a=0;a<3;a++){min[a]=Math.min(min[a],m.positions[i+a]);max[a]=Math.max(max[a],m.positions[i+a])}const center=min.map((x,a)=>(x+max[a])/2);let radius=.001;for(const m of meshes)for(let i=0;i<m.positions.length;i+=3)radius=Math.max(radius,Math.hypot(m.positions[i]-center[0],m.positions[i+1]-center[1],m.positions[i+2]-center[2]));const W=canvas.width,H=canvas.height,dpr=Math.min(devicePixelRatio||1,2),scale=Math.min(W,H)*.43*c.zoom/radius,O=c.orientation;return{W,H,p:(x,y,z)=>{x-=center[0];y-=center[1];z-=center[2];return[W/2+(O[0]*x+O[1]*y+O[2]*z)*scale+c.panX*dpr,H/2-(O[6]*x+O[7]*y+O[8]*z)*scale+c.panY*dpr,O[3]*x+O[4]*y+O[5]*z]}}}
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
    function drawOverlay(){
      const dpr=Math.min(devicePixelRatio||1,2),rect=panel.getBoundingClientRect();
      overlay.width=Math.round(rect.width*dpr);overlay.height=Math.round(rect.height*dpr);
      const ctx=overlay.getContext('2d');ctx.clearRect(0,0,overlay.width,overlay.height);
      const o=state.byId.get(state.selected),P=projectSetup();
      if(o?.type==='sketch'&&P){drawSelectedSketch(o,P,ctx,dpr);return}
      const body=bodyFor(o),m=sceneBody(body?.id);
      if(!m||!P)return;
      ctx.save();
      const pts=m.positions,min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
      for(let i=0;i<pts.length;i+=3)for(let a=0;a<3;a++){min[a]=Math.min(min[a],pts[i+a]);max[a]=Math.max(max[a],pts[i+a])}
      const corners=[];for(const x of [min[0],max[0]])for(const y of [min[1],max[1]])for(const z of [min[2],max[2]])corners.push(P.p(x,y,z));
      const axisNames=['X','Y','Z'],center=min.map((value,axis)=>(value+max[axis])/2),center2d=P.p(...center);
      ctx.font=`700 ${10*dpr}px ui-monospace,monospace`;ctx.textAlign='center';ctx.textBaseline='middle';
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
    function syncOverlay(){let c;try{c=modelViewer?.getCamera?.()}catch{}const r=panel.getBoundingClientRect(),sig=state.selected&&c?.orientation?`${state.project}|${state.rev}|${state.selected}|${r.width|0}|${r.height|0}|${c.zoom}|${c.panX}|${c.panY}|${c.orientation.join(',')}`:'';if(sig===lastOverlaySignature)return;lastOverlaySignature=sig;drawOverlay()}
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
      const consumedBodies = consumed();
      let best = null;
      for (const body of state.objects.filter(item => item.type === 'body' && !consumedBodies.has(item.id))) {
        const mesh = sceneBody(body.id);
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
          if (depth !== null && (!best || depth > best.depth)) best = { body, depth };
        }
      }
      if (!best) return;
      const featureId = [...(best.body.feature_ids || [])].reverse()
        .find(id => state.byId.get(id)?.type === 'feature');
      if (featureId) select(featureId, true);
    }
    let down=null;
    canvas.addEventListener('pointerdown',e=>{if(e.button===0)down={x:e.clientX,y:e.clientY,pointerId:e.pointerId}},true);
    canvas.addEventListener('pointermove',e=>{if(down?.pointerId===e.pointerId&&Math.hypot(e.clientX-down.x,e.clientY-down.y)>=6)down=null},true);
    canvas.addEventListener('pointerup',e=>{if(down?.pointerId!==e.pointerId)return;const click=e.button===0&&Math.hypot(e.clientX-down.x,e.clientY-down.y)<6;down=null;if(click)pick(e)},true);
    canvas.addEventListener('pointercancel',()=>{down=null},true);
    const toolbar=$('.model-toolbar'),presets=$('#viewport-camera-presets'),fit=$('#viewport-fit'),four=$('#viewport-four-views'),divider=$('.viewport-toolbar-divider');if(toolbar&&presets&&fit&&four&&divider){detail.dataset.cadTool='orbit';fit.innerHTML=`${I.fit}<span class="toolbar-label">Вписать</span>`;fit.classList.add('cad3-tool');four.classList.add('cad3-tool');const views=document.createElement('details');views.className='cad3-views';views.innerHTML='<summary class="cad3-tool">Ракурсы ▾</summary>';views.append(presets);toolbar.replaceChildren(fit,four,divider,views)}
    function defaultSelection(){const cons=consumed(),b=[...state.objects].reverse().find(o=>o.type==='body'&&!cons.has(o.id));return [...(b?.feature_ids||[])].reverse().find(id=>state.byId.get(id)?.type==='feature')||[...state.objects].reverse().find(o=>o.type==='feature')?.id||null}
    async function refresh(){const id=new URLSearchParams(location.search).get('project_id');if(!id||detail.hidden)return;try{const [info,scene]=await Promise.all([fetch(`/api/v1/projects/${encodeURIComponent(id)}`,{cache:'no-store'}).then(r=>r.json()),fetch(`/api/v1/projects/${encodeURIComponent(id)}/viewport`,{cache:'no-store'}).then(r=>r.json())]);if(scene.revision!==info.document.revision)return;const followLatest=state.project!==id||state.rev!==info.document.revision;state.project=id;state.rev=info.document.revision;state.objects=info.document.objects||[];state.byId=new Map(state.objects.map(o=>[o.id,o]));state.scene=scene;makeLabels();if(followLatest||(!state.selected&&!state.selectionCleared)||(state.selected&&!state.byId.has(state.selected))){state.selected=defaultSelection();state.selectionCleared=false}renderTree();state.selected?select(state.selected):renderProps(null)}catch(e){console.debug('CAD semantic refresh',e)}}
    let timer;const schedule=()=>{clearTimeout(timer);timer=setTimeout(refresh,80)};['#document-revision','#document-title'].forEach(s=>{const e=$(s);if(e)new MutationObserver(schedule).observe(e,{childList:true,subtree:true,characterData:true})});new MutationObserver(schedule).observe(detail,{attributes:true,attributeFilter:['hidden']});schedule();setTimeout(refresh,600);
  }
  const boot=()=>setTimeout(enhance,0);if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
