(() => {
  const css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = '/assets/cad-reference-v2.css';
  document.head.appendChild(css);

  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => [...r.querySelectorAll(s)];
  const icon = (name) => ({
    menu:'<svg viewBox="0 0 24 24"><path d="M5 6h14M5 12h14M5 18h14"/></svg>',
    model:'<svg viewBox="0 0 24 24"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5M12 12v9"/></svg>',
    geometry:'<svg viewBox="0 0 24 24"><path d="M5 18V7l7-4 7 4v10l-7 4-7-3Z M5 7l7 4 7-4M12 11v10"/></svg>',
    files:'<svg viewBox="0 0 24 24"><path d="M4 7.5h6l2-2h8v13H4v-11Z"/></svg>',
    projects:'<svg viewBox="0 0 24 24"><path d="M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z"/></svg>',
    activity:'<svg viewBox="0 0 24 24"><path d="M4 12h4l2-5 4 10 2-5h4"/></svg>',
    settings:'<svg viewBox="0 0 24 24"><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z M19 12l2-1-2-3-2 .5-1.5-1L15 5h-6l-.5 2.5-1.5 1L5 8l-2 3 2 1v2l-2 1 2 3 2-.5 1.5 1L9 21h6l.5-2.5 1.5-1 2 .5 2-3-2-1v-2Z"/></svg>',
    cloud:'<svg viewBox="0 0 24 24"><path d="M7 18h10a4 4 0 0 0 .4-8A6 6 0 0 0 6.1 8.5 4.5 4.5 0 0 0 7 18Z"/></svg>',
    snapshot:'<svg viewBox="0 0 24 24"><path d="M5 7h3l1.2-2h5.6L16 7h3v11H5V7Z"/><circle cx="12" cy="12.5" r="3.5"/></svg>',
    comment:'<svg viewBox="0 0 24 24"><path d="M5 5h14v11H9l-4 3V5Z M8 9h8M8 12h6"/></svg>',
    body:'<svg viewBox="0 0 24 24"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5M12 12v9"/></svg>',
    sketch:'<svg viewBox="0 0 24 24"><path d="M5 18 17 6M7 6h10v10"/><circle cx="6" cy="18" r="1.5"/><circle cx="18" cy="6" r="1.5"/></svg>',
    hole:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3"/></svg>',
    fillet:'<svg viewBox="0 0 24 24"><path d="M5 19V9a4 4 0 0 1 4-4h10M9 19V11a2 2 0 0 1 2-2h8"/></svg>',
    thread:'<svg viewBox="0 0 24 24"><path d="M6 5h12M6 9h12M6 13h12M6 17h12M9 3 7 21M17 3l-2 18"/></svg>',
    pattern:'<svg viewBox="0 0 24 24"><circle cx="8" cy="8" r="2.5"/><circle cx="16" cy="8" r="2.5"/><circle cx="8" cy="16" r="2.5"/><circle cx="16" cy="16" r="2.5"/></svg>',
    mirror:'<svg viewBox="0 0 24 24"><path d="M12 3v18M9 6 4 10v4l5 4M15 6l5 4v4l-5 4"/></svg>',
    cut:'<svg viewBox="0 0 24 24"><path d="M5 5h14v14H5zM9 5v10h10"/></svg>',
    plane:'<svg viewBox="0 0 24 24"><path d="m5 8 11-3 3 11-11 3-3-11Z"/></svg>',
    origin:'<svg viewBox="0 0 24 24"><path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="3"/></svg>'
  })[name] || '';
  const iconName = (label) => {
    const v = label.toLowerCase();
    if(v.includes('sketch')||v.includes('эскиз')) return 'sketch';
    if(v.includes('hole')||v.includes('отвер')) return 'hole';
    if(v.includes('fillet')||v.includes('скруг')) return 'fillet';
    if(v.includes('thread')||v.includes('резьб')) return 'thread';
    if(v.includes('pattern')||v.includes('массив')) return 'pattern';
    if(v.includes('mirror')||v.includes('зерк')) return 'mirror';
    if(v.includes('cut')||v.includes('pocket')||v.includes('вырез')) return 'cut';
    return 'body';
  };
  const inferType = (label) => {
    const v = label.toLowerCase();
    if(v.includes('эскиз')||v.includes('sketch')) return 'Sketch';
    if(v.includes('резьб')||v.includes('thread')) return 'Thread';
    if(v.includes('скруг')||v.includes('fillet')) return 'Fillet';
    if(v.includes('отвер')||v.includes('hole')) return 'Hole';
    if(v.includes('вращ')||v.includes('revol')) return 'Revolve';
    if(v.includes('выдав')||v.includes('extrud')) return 'Extrude';
    if(v.includes('loft')) return 'Loft';
    if(v.includes('sweep')||v.includes('протяж')) return 'Sweep';
    return 'Body / Feature';
  };

  const loadLegacy = () => new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/assets/app-legacy.js';
    s.async = false;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });

  const enhance = () => {
    const detail=q('#project-detail'), grid=q('#primary-grid'), nav=q('#workspace-navigation'), sidebar=q('.section-sidebar'), inspector=q('.inspector-column'), rows=q('#geometry-rows'), modelPanel=q('.model-panel');
    if(!detail||!grid||!nav||!sidebar||!inspector||!rows||!modelPanel) return;

    if(!q('.cad-v2-header-rail')){
      const el=document.createElement('div');el.className='cad-v2-header-rail';el.innerHTML=icon('menu');q('.topbar')?.prepend(el);
    }
    if(!q('.cad-v2-utilities')){
      const el=document.createElement('div');el.className='cad-v2-utilities';el.innerHTML=`<button class="cad-v2-utility" id="cad-v2-sync" type="button" title="Состояние синхронизации">${icon('cloud')}</button><button class="cad-v2-utility" type="button" title="Viewport snapshot передаётся агенту автоматически" disabled>${icon('snapshot')}</button><button class="cad-v2-utility" id="cad-v2-comments" type="button" title="Комментарии">${icon('comment')}</button><button class="cad-v2-utility" type="button" title="Настройки — planned" disabled>${icon('settings')}</button><span class="cad-v2-avatar">A</span>`;q('.topbar-end')?.append(el);
      q('#cad-v2-comments')?.addEventListener('click',()=>q('[data-cad-inspector="comments"]')?.click());
      const apiLight=q('#api-light'), sync=q('#cad-v2-sync');
      const syncApi=()=>{if(sync) sync.style.color=apiLight?.classList.contains('active')?'#ff9653':'#7c878e'};syncApi();if(apiLight)new MutationObserver(syncApi).observe(apiLight,{attributes:true,attributeFilter:['class']});
    }

    if(!q('.cad-v2-global-rail')){
      const rail=document.createElement('nav');rail.className='cad-v2-global-rail';rail.setAttribute('aria-label','Глобальная навигация CADYX');rail.innerHTML=`<button class="cad-v2-rail-button" data-v2-view="overview" data-active="true" title="Model">${icon('model')}</button><button class="cad-v2-rail-button" data-v2-view="geometry" title="Geometry">${icon('geometry')}</button><button class="cad-v2-rail-button" data-v2-view="files" title="Files">${icon('files')}</button><button class="cad-v2-rail-button" data-v2-view="projects" title="Projects">${icon('projects')}</button><button class="cad-v2-rail-button" data-v2-view="activity" title="Activity">${icon('activity')}</button><span class="cad-v2-rail-spacer"></span><button class="cad-v2-rail-button" disabled title="Settings — planned">${icon('settings')}</button>`;detail.prepend(rail);
      rail.addEventListener('click',e=>{const b=e.target.closest('[data-v2-view]');if(!b||b.disabled)return;const src=q(`[data-workspace-view="${b.dataset.v2View}"]`,nav);if(src&&!src.disabled)src.click()});
      const syncRail=()=>{const active=grid.dataset.view||'overview';qa('[data-v2-view]',rail).forEach(b=>{b.dataset.active=String(b.dataset.v2View===active);const src=q(`[data-workspace-view="${b.dataset.v2View}"]`,nav);if(src)b.disabled=src.disabled})};syncRail();new MutationObserver(syncRail).observe(grid,{attributes:true,attributeFilter:['data-view']});new MutationObserver(syncRail).observe(nav,{subtree:true,attributes:true,attributeFilter:['disabled']});
    }

    const staticIcons=qa('.cad-tree-static-row .cad-tree-icon');
    if(staticIcons[0])staticIcons[0].innerHTML=icon('origin');
    staticIcons.slice(1).forEach(el=>el.innerHTML=icon('plane'));
    const rootIcon=q('.cad-tree-root-row .cad-tree-icon');if(rootIcon)rootIcon.innerHTML=icon('body');

    let props=q('.cad-v2-properties',inspector);
    if(!props){props=document.createElement('section');props.className='cad-v2-properties';props.innerHTML='<div class="cad-v2-prop-empty">Выберите feature в Model Tree.</div>';inspector.appendChild(props)}
    const comments=q('#cad-comments-pane');if(comments&&!q('.cad-v2-comments-note',comments)){comments.innerHTML='<div class="cad-v2-comments-note"><h3>Comments</h3><p>Панель подготовлена для контекстных комментариев к feature, грани или ребру. Хранилище комментариев пока не подключено.</p><textarea disabled placeholder="Add a comment…"></textarea></div>'}
    const chip=q('.cad-v2-selection-chip')||(()=>{const el=document.createElement('div');el.className='cad-v2-selection-chip';modelPanel.appendChild(el);return el})();

    let selectedKey=null;
    const metric=id=>q(id)?.textContent?.trim()||'—';
    const renderProps=(row,label)=>{
      if(!row){props.innerHTML='<div class="cad-v2-prop-empty">Выберите feature в Model Tree.</div>';chip.dataset.visible='false';detail.dataset.v2Selection='false';return}
      const type=inferType(label), bodyId=row.title||'—', current=row.querySelectorAll('td')[0]?.textContent?.replace(/\s+/g,' ').trim()||'—';
      const originals=qa('input[data-parameter]',row);
      const pars=originals.length?originals.map((input,i)=>`<dt>${input.title||input.dataset.parameter}</dt><dd><span class="cad-v2-input-wrap"><input class="cad-v2-input" data-i="${i}" value="${String(input.value).replace(/"/g,'&quot;')}"><span class="cad-v2-unit">${input.dataset.parameter==='angle_degrees'?'°':'mm'}</span></span></dd>`).join(''):'<dt>Editable</dt><dd>No editable parameters</dd>';
      props.innerHTML=`<div class="cad-v2-prop-head"><span class="cad-v2-prop-icon">${icon(iconName(label))}</span><div><strong></strong><small>${type}</small></div></div><section class="cad-v2-prop-section"><h3>General</h3><dl class="cad-v2-prop-grid"><dt>Feature Type</dt><dd>${type}</dd><dt>Status</dt><dd><span class="cad-v2-valid">● Valid</span></dd><dt>Body</dt><dd title="${bodyId}">${bodyId==='—'?'—':bodyId.slice(0,12)}</dd></dl></section><section class="cad-v2-prop-section"><h3>Parameters</h3><dl class="cad-v2-prop-grid">${pars}</dl>${originals.length?'<button class="cad-v2-apply">APPLY PARAMETERS</button>':''}</section><section class="cad-v2-prop-section"><h3>Result</h3><dl class="cad-v2-prop-grid"><dt>Dimensions</dt><dd>${current}</dd><dt>Objects</dt><dd>${metric('#viewport-object-count')}</dd><dt>Vertices</dt><dd>${metric('#viewport-vertex-count')}</dd><dt>Faces</dt><dd>${metric('#viewport-face-count')}</dd></dl></section>`;
      q('.cad-v2-prop-head strong',props).textContent=label;
      q('.cad-v2-apply',props)?.addEventListener('click',()=>{qa('.cad-v2-input',props).forEach(i=>{const original=originals[Number(i.dataset.i)];if(original)original.value=i.value});row.querySelector('.geometry-apply')?.click()});
      chip.textContent=label;chip.dataset.visible='true';detail.dataset.v2Selection='true';
    };

    const list=q('#cad-tree-features');
    const search=q('.cad-tree-search input');
    const rebuild=()=>{
      if(!list)return;const query=(search?.value||'').trim().toLowerCase();const sourceRows=qa('tr',rows);list.replaceChildren();let selected=null;
      sourceRows.forEach((row,index)=>{const label=(row.querySelector('th,td')?.textContent||'').replace(/\s+/g,' ').trim();if(!label||(query&&!label.toLowerCase().includes(query)))return;const key=row.title||`${index}:${label}`;const b=document.createElement('button');b.type='button';b.className='cad-tree-feature';b.dataset.key=key;b.dataset.selected=String(key===selectedKey);b.innerHTML=`<span class="cad-tree-chevron">›</span><span class="cad-v2-feature-icon">${icon(iconName(label))}</span><span class="cad-tree-label"></span>`;q('.cad-tree-label',b).textContent=label;b.addEventListener('click',()=>{selectedKey=key;qa('.cad-tree-feature',list).forEach(x=>x.dataset.selected=String(x===b));renderProps(row,label)});list.appendChild(b);if(key===selectedKey)selected={row,label}}
      );const empty=q('#cad-tree-empty');if(empty)empty.hidden=list.children.length>0;if(!selectedKey&&list.firstElementChild)list.firstElementChild.click();else if(selected)renderProps(selected.row,selected.label);else if(!list.children.length){selectedKey=null;renderProps(null,'')}
    };
    if(list){const obs=new MutationObserver(()=>{if(!obs._busy){obs._busy=true;queueMicrotask(()=>{rebuild();obs._busy=false})}});obs.observe(rows,{childList:true,subtree:true,characterData:true});search?.addEventListener('input',rebuild);rebuild()}

    const tabs=q('.cad-inspector-tabs');
    if(tabs){const syncTabs=()=>{const active=q('.cad-inspector-tab[aria-selected="true"]',tabs)?.dataset.cadInspector||'properties';props.hidden=active!=='properties'};tabs.addEventListener('click',()=>queueMicrotask(syncTabs));syncTabs()}

    const toolbar=q('.model-toolbar'), presets=q('#viewport-camera-presets'), fit=q('#viewport-fit'), four=q('#viewport-four-views'), divider=q('.viewport-toolbar-divider'), display=q('#viewport-wireframe');
    if(toolbar&&presets&&fit&&four&&divider&&display){toolbar.replaceChildren(presets,fit,four,divider,display);const label=q('#viewport-mode-label');if(label)label.textContent='Shaded + Edges';display.title='Display mode: Shaded + Edges'}
  };

  document.addEventListener('DOMContentLoaded', async () => {
    try { await loadLegacy(); } catch (error) { console.error('CADYX legacy app failed to load', error); return; }
    enhance();
  }, { once:true });
})();
