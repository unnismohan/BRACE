/* Bounded lists and operations. No build step or external dependencies. */
const Lists = {
  states: {}, tags: [], users: [], timers: {},
  fields: {cases:{q:'tc-search',status:'tc-status',tag:'tc-tag'},runs:{q:'runs-search',status:'runs-status',who:'runs-user',date_from:'runs-from',date_to:'runs-to'},group:{q:'grptc-search'}},
  state(kind) {
    const key = `${_user?.username}:${_curProj?.id}:${kind}`;
    if (!this.states[key]) {
      let saved={}; try { saved=JSON.parse(localStorage.getItem('brace_filters:'+key)||'{}'); } catch {}
      this.states[key]={offset:Math.max(0,Number(saved.offset)||0),limit:50,total:0,filters:saved.filters||{},sequence:0,restored:false,key};
    }
    return this.states[key];
  },
  reset(kind) { const s=this.state(kind); s.offset=0; s.restored=false; s.filters={}; },
  changed(kind) { clearTimeout(this.timers[kind]); this.timers[kind]=setTimeout(()=>{this.state(kind).offset=0;this.load(kind);},200); },
  async load(kind) {
    if (!_curProj) return;
    const project=_curProj.id,s=this.state(kind), seq=++s.sequence;
    try {
      if(kind==='cases') {const tags=await api('GET',`/projects/${project}/tags`);if(_curProj?.id!==project||seq!==s.sequence)return;this.tags=tags.map(t=>t.tag).sort();}
      if(kind==='cases') syncTCTagFilter();
      if(!s.restored || document.getElementById(Object.values(this.fields[kind])[0]).dataset.filterOwner!==s.key) { for(const [key,id] of Object.entries(this.fields[kind])) { const el=document.getElementById(id); if(el) {if(el.tagName==='SELECT' && s.filters[key] && !Array.from(el.options).some(o=>o.value===s.filters[key])) {const o=document.createElement('option');o.value=s.filters[key];o.textContent=s.filters[key];el.appendChild(o);}el.value=s.filters[key]||'';} } s.restored=true;document.getElementById(Object.values(this.fields[kind])[0]).dataset.filterOwner=s.key; }
      const params=new URLSearchParams({offset:s.offset,limit:s.limit});
      s.filters={}; for(const [key,id] of Object.entries(this.fields[kind])) {const value=document.getElementById(id)?.value||'';s.filters[key]=value;if(value)params.set(key,value);}
      if(kind==='group') params.set('exclude_group',_grpId);
      const result=await api('GET',`/projects/${project}/${kind==='runs'?'runs':'test-cases'}/page?${params}`);
      if(_curProj?.id!==project||seq!==s.sequence)return;
      s.total=result.total;
      if(s.offset>=s.total&&s.offset>0){s.offset=Math.max(0,Math.floor((s.total-1)/s.limit)*s.limit);return this.load(kind);}
      if(kind==='runs') {this.users=result.users;syncRunUserFilter();document.getElementById('runs-user').value=s.filters.who||'';_runs=result.items;renderRuns(true);}
      if(kind==='cases') {_tcs=result.items;renderTCs(true);onChkChange();}
      if(kind==='group') {_grpAvail=result.items;renderGrpTCList(true);}
      this.pager(kind,s);
      try {localStorage.setItem('brace_filters:'+s.key,JSON.stringify({offset:s.offset,filters:s.filters}));} catch {}
      const count=document.getElementById(kind==='cases'?'tc-filter-count':kind==='runs'?'runs-filter-count':'grptc-count');
      if(count)count.textContent=`${s.total} matching ${kind==='runs'?'runs':'cases'}`+(kind==='group'?` · ${_grpPicked.size} selected`:'');
      return result.items;
    } catch(e){toast(e.message,'e');}
  },
  pager(kind,s) {
    let el=document.getElementById('pager-'+kind);
    if(!el){el=document.createElement('div');el.id='pager-'+kind;el.className='list-pager';const anchor=document.getElementById(kind==='cases'?'tc-tbody':kind==='runs'?'runs-tbody':'grptc-list');(anchor.tagName==='TBODY'?anchor.closest('table'):anchor).insertAdjacentElement('afterend',el);}
    el.innerHTML=`<button class="btn btn-o btn-sm" ${s.offset===0?'disabled':''} onclick="Lists.move('${kind}',-1)">Previous</button> <span aria-live="polite">${s.total?s.offset+1:0}–${Math.min(s.offset+s.limit,s.total)} of ${s.total}</span> <button class="btn btn-o btn-sm" ${s.offset+s.limit>=s.total?'disabled':''} onclick="Lists.move('${kind}',1)">Next</button>`;
  },
  move(kind,direction){this.state(kind).offset+=direction*this.state(kind).limit;this.load(kind);}
};
const Operations = {
  profilesData: [], runPicked:new Set(), runOffset:0, runTotal:0,
  async quarantine(id) {
    const tc=_tcs.find(t=>t.id===id);if(!tc)return;
    let reason=''; if(!tc.quarantined){reason=await askInput('Quarantine reason',{msg:'Explain why this case is excluded from normal runs.',allowSlash:true});if(!reason?.trim())return;}
    try{await api('PUT',`/test-cases/${id}/quarantine`,{quarantined:!tc.quarantined,reason});await loadTCs();}catch(e){toast(e.message,'e');}
  },
  async profiles() {
    if(!_curProj)return;const project=_curProj.id;
    try{const data=await api('GET',`/projects/${project}/profiles`);if(_curProj?.id!==project)return;this.profilesData=data;document.getElementById('profiles-list').innerHTML=data.map(p=>`<p><strong>${esc(p.name)}</strong> · ${p.enabled?'Enabled':'Disabled'} · Secrets: ${esc(p.secret_names.join(', ')||'none')} ${can('manage')?`<button class="btn btn-sm btn-o" onclick="Operations.editProfile(${p.id})">Edit</button>`:''}</p>`).join('')||'<p>No profiles configured.</p>';}catch(e){toast(e.message,'e');}
  },
  async editProfile(id) {
    const profile = this.profilesData.find(value => value.id === id) || {name:'',environment:{},variables:{},secret_names:[],enabled:true};
    let modal = document.getElementById('modal-profile');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'modal-profile'; modal.className = 'overlay';
      modal.innerHTML = `<div class="modal"><div class="mhdr"><h3>Environment profile</h3><button class="mclose" onclick="closeModal('modal-profile')">×</button></div>
        <div class="fr"><label for="profile-name">Profile name</label><input id="profile-name" maxlength="100"></div>
        <div class="fr"><label for="profile-enabled"><input type="checkbox" id="profile-enabled"> Enabled for new runs</label></div>
        <div class="fr"><label for="profile-env">Environment variables</label><textarea id="profile-env" rows="3" placeholder="TARGET_REGION=qa"></textarea></div>
        <div class="fr"><label for="profile-vars">Robot variables</label><textarea id="profile-vars" rows="3" placeholder="BASE_URL=https://qa.example.test"></textarea><p class="fhint">One NAME=value per line. Values can include =. Use letters, numbers and underscores in names.</p></div>
        <div class="fr"><label for="profile-secrets">New or replacement secret variables</label><textarea id="profile-secrets" rows="2" autocomplete="off" placeholder="TEST_PASSWORD=your scoped testing credential"></textarea><p class="fhint">Leave blank to keep saved secrets. Test code can read and print selected secrets.</p><p id="profile-secret-names"></p></div>
        <div class="fr"><label for="profile-remove">Secret names to remove</label><input id="profile-remove" placeholder="NAME_1, NAME_2"></div>
        <div class="mfoot"><button class="btn btn-o" onclick="closeModal('modal-profile')">Cancel</button><button class="btn btn-p" onclick="Operations.saveProfile()">Save</button></div></div>`;
      document.body.appendChild(modal);
    }
    this.editingProfile = id;
    const lines = values => Object.entries(values).map(([name,value])=>name+'='+value).join('\n');
    document.getElementById('profile-name').value = profile.name;
    document.getElementById('profile-enabled').checked = !!profile.enabled;
    document.getElementById('profile-env').value = lines(profile.environment);
    document.getElementById('profile-vars').value = lines(profile.variables);
    document.getElementById('profile-secrets').value = '';
    document.getElementById('profile-remove').value = '';
    document.getElementById('profile-secret-names').textContent = 'Saved secret names: '+(profile.secret_names.join(', ')||'none');
    showModal('modal-profile');
  },
  profileValues(id) {
    const values = {};
    for (const line of document.getElementById(id).value.split(/\r?\n/)) {
      if (!line.trim()) continue;
      const separator = line.indexOf('=');
      if (separator < 1) throw new Error('Use NAME=value for each variable.');
      const name = line.slice(0,separator).trim();
      if (Object.hasOwn(values,name)) throw new Error('Duplicate variable: '+name);
      values[name] = line.slice(separator+1);
    }
    return values;
  },
  async saveProfile() {
    try {
      const body = {name:document.getElementById('profile-name').value.trim(), enabled:document.getElementById('profile-enabled').checked,
        environment:this.profileValues('profile-env'), variables:this.profileValues('profile-vars'), secret_variables:this.profileValues('profile-secrets'),
        remove_secrets:document.getElementById('profile-remove').value.split(',').map(name=>name.trim()).filter(Boolean)};
      await api(this.editingProfile?'PUT':'POST',`/projects/${_curProj.id}/profiles${this.editingProfile?'/'+this.editingProfile:''}`,body);
      closeModal('modal-profile'); await this.profiles(); toast('Profile saved','s');
    } catch(error) { toast(error.message,'e'); }
  },
  async loadRunProfiles() {
    const data=await api('GET',`/projects/${_curProj.id}/profiles`),sel=document.getElementById('run-profile');sel.innerHTML='<option value="">Default</option>';data.filter(p=>p.enabled).forEach(p=>{const option=document.createElement('option');option.value=p.id;option.textContent=p.name;sel.appendChild(option);});document.getElementById('run-retries').value=0;document.getElementById('run-quarantine').checked=false;
  },
  async openPicker(){this.runOffset=0;const search=document.getElementById('run-case-search');if(search)search.value='';},
  async runPage(direction=0) {
    const project=_curProj.id,sequence=this.runSequence=(this.runSequence||0)+1;
    this.runOffset=Math.max(0,this.runOffset+direction*50);
    const q=document.getElementById('run-case-search')?.value||'';
    const data=await api('GET',`/projects/${_curProj.id}/test-cases/page?offset=${this.runOffset}&limit=50&q=${encodeURIComponent(q)}`);if(_curProj?.id!==project||sequence!==this.runSequence)return;this.runTotal=data.total;
    const list=document.getElementById('run-tc-list');list.innerHTML=data.items.map(tc=>`<div class="tc-sitem"><input class="run-chk" type="checkbox" id="rtc-${tc.id}" ${this.runPicked.has(tc.id)?'checked':''} onchange="Operations.pick(${tc.id},this.checked)"><label for="rtc-${tc.id}">${esc(tc.tc_code)} ${esc(tc.name)}${tc.quarantined?' · quarantined':''}</label></div>`).join('')||'<p>No matching cases.</p>';
    let controls=document.getElementById('run-case-controls');if(!controls){controls=document.createElement('div');controls.id='run-case-controls';list.insertAdjacentElement('beforebegin',controls);controls.innerHTML='<label for="run-case-search">Search cases</label><input id="run-case-search" oninput="Operations.searchRun()"><div id="run-case-pages" class="list-pager"></div>';}
    document.getElementById('run-case-pages').innerHTML=`<button class="btn btn-o btn-sm" onclick="Operations.runPage(-1)" ${this.runOffset===0?'disabled':''}>Previous</button> ${this.runOffset+1}–${Math.min(this.runOffset+50,data.total)} of ${data.total}; <span id="run-selected-count">${this.runPicked.size}</span> selected <button class="btn btn-o btn-sm" onclick="Operations.runPage(1)" ${this.runOffset+50>=data.total?'disabled':''}>Next</button>`;
  },
  searchRun(){clearTimeout(this.searchTimer);this.searchTimer=setTimeout(()=>{this.runOffset=0;this.runPage();},200);},
  pick(id,on){on?this.runPicked.add(id):this.runPicked.delete(id);const count=document.getElementById('run-selected-count');if(count)count.textContent=this.runPicked.size;},
  attempts(it){if(!it.attempts?.length)return '';return `<p>${it.passed_after_retry?'<strong>Passed after retry — flaky result</strong>':'Attempt history'}</p>`+it.attempts.map(a=>(a.has_log||a.has_console)?`<a target="_blank" rel="noopener" href="/results/${_curProj.id}/${encodeURIComponent(_rd.runId)}/${encodeURIComponent(it.rf_run_id)}/${encodeURIComponent(a.artifact_dir)}/${a.has_log?'log.html':'console.log'}?token=${encodeURIComponent(_token)}">Attempt ${a.attempt}: ${esc(a.status)} (${Number(a.duration_sec).toFixed(1)}s)</a>`:`Attempt ${a.attempt}: ${esc(a.status)} (${Number(a.duration_sec).toFixed(1)}s); artifacts unavailable`).join(' · ');},
  failureHint(it){const text=(it.fail_detail||it.fail_summary||'').toLowerCase();let hint='';if(/timeout|timed out/.test(text))hint='Check service readiness and wait conditions before increasing timeouts.';else if(/locator|element|not found/.test(text))hint='Check the locator and page state; compare the captured DOM when available.';else if(/connection|refused|dns/.test(text))hint='Check the selected environment, network access, and service availability.';else if(/assert|should|expected/.test(text))hint='Compare expected and actual values, and confirm the test data.';return hint?`<p><strong>Suggested next check:</strong> ${esc(hint)}</p>`:'';},
  async overview() {
    const project=_curProj.id,el=document.getElementById('project-overview');el.textContent='Loading project health…';
    try{const d=await api('GET',`/projects/${project}/overview`);if(_curProj?.id!==project)return;el.innerHTML=`<div class="asection"><h3>Project health</h3><div class="overview-grid">${[['Cases',d.cases.total],['Executed',d.cases.executed||0],['Quarantined',d.cases.quarantined||0],['Passed after retry',d.passed_after_retry],['Queued',d.queued],['Running',d.running]].map(([label,value])=>`<div><strong>${value}</strong><br>${label}</div>`).join('')}</div></div><div class="asection"><h3>Recent failures</h3>${d.failures.map(f=>`<p><button class="btn btn-o btn-sm" onclick="viewRunDetail(${jsArg(f.run_id)})">${esc(f.tc_name)}</button> ${esc(f.fail_summary||'Open run details for the failure')}</p>`).join('')||'<p>No recorded failures.</p>'}</div><div class="asection"><h3>Upcoming schedules (${esc(d.timezone)})</h3>${d.schedules.map(s=>`<p>${esc(s.name)} · ${esc(s.next_run)} · ${esc(s.overlap_policy)}</p>`).join('')||'<p>No enabled schedules.</p>'}</div>`;}catch(e){el.textContent=e.message;}
  }
};

const UserList = {
  offset:0, total:0, sequence:0, owner:null,
  async load(direction=0){
    const key='brace_user_filters:'+_user?.username,input=document.getElementById('users-search');
    if(this.owner!==key){let state={};try{state=JSON.parse(localStorage.getItem(key)||'{}');}catch{}this.offset=state.offset||0;if(input)input.value=state.q||'';this.owner=key;}
    this.offset=Math.max(0,this.offset+direction*50);const seq=++this.sequence,q=input?.value||'';
    try{const page=await api('GET',`/users/page?offset=${this.offset}&limit=50&q=${encodeURIComponent(q)}`);if(seq!==this.sequence)return;if(this.offset>=page.total&&this.offset>0){this.offset=Math.max(0,Math.floor((page.total-1)/50)*50);return this.load();}this.total=page.total;_users=page.items;renderUsers(true);document.getElementById('users-count').textContent=page.total;
    let el=document.getElementById('users-pages');if(!el){el=document.createElement('div');el.id='users-pages';el.className='list-pager';document.getElementById('users-tbody').closest('table').insertAdjacentElement('afterend',el);}
    el.innerHTML=`<button class="btn btn-o btn-sm" ${this.offset===0?'disabled':''} onclick="UserList.load(-1)">Previous</button> ${page.total?this.offset+1:0}–${Math.min(this.offset+50,page.total)} of ${page.total} <button class="btn btn-o btn-sm" ${this.offset+50>=page.total?'disabled':''} onclick="UserList.load(1)">Next</button>`;
    try{localStorage.setItem(key,JSON.stringify({offset:this.offset,q}));}catch{}
    }catch(e){toast(e.message,'e');}
  }
};

const ReportHistory = {
  offset:0, total:0,
  async load(reset=false,direction=0) {
    if(reset)this.offset=0;else this.offset=Math.max(0,this.offset+direction*50);
    const dates=rptDateParams(), project=_curProj.id;
    try {
      const params=new URLSearchParams({...dates,offset:this.offset,limit:50});
      const page=await api('GET',`/projects/${project}/runs/page?${params}`);
      if(_curProj?.id!==project)return;
      this.total=page.total;renderRunHistory(page.items);
      let controls=document.getElementById('report-history-pages');
      if(!controls){controls=document.createElement('div');controls.id='report-history-pages';controls.className='list-pager';document.getElementById('reports-list').insertAdjacentElement('afterend',controls);}
      controls.innerHTML=`<button class="btn btn-o btn-sm" ${this.offset===0?'disabled':''} onclick="ReportHistory.load(false,-1)">Previous</button> ${page.total?this.offset+1:0}–${Math.min(this.offset+50,page.total)} of ${page.total} <button class="btn btn-o btn-sm" ${this.offset+50>=page.total?'disabled':''} onclick="ReportHistory.load(false,1)">Next</button>`;
    }catch(error){toast(error.message,'e');}
  }
};

const GroupList = {
  offset:0, project:null, membersOffsets:{},
  async load(direction=0) {
    if(!_curProj)return;
    const project=_curProj.id;if(this.project!==project){this.offset=0;this.membersOffsets={};this.project=project;}
    this.offset=Math.max(0,this.offset+direction*50);
    try {const page=await api('GET',`/projects/${project}/groups/page?offset=${this.offset}&limit=50`);if(_curProj?.id!==project)return;
      if(this.offset>=page.total&&this.offset>0){this.offset=Math.max(0,Math.floor((page.total-1)/50)*50);return this.load();}
      _groups=page.items;
      for(const group of _groups)if(_grpOpen[group.id])await this.members(group.id);
      renderGroups();let controls=document.getElementById('groups-pages');if(!controls){controls=document.createElement('div');controls.id='groups-pages';controls.className='list-pager';document.getElementById('groups-list').insertAdjacentElement('afterend',controls);}
      controls.innerHTML=`<button class="btn btn-o btn-sm" ${this.offset===0?'disabled':''} onclick="GroupList.load(-1)">Previous</button> ${page.total?this.offset+1:0}–${Math.min(this.offset+50,page.total)} of ${page.total} suites <button class="btn btn-o btn-sm" ${this.offset+50>=page.total?'disabled':''} onclick="GroupList.load(1)">Next</button>`;
    }catch(error){toast(error.message,'e');}
  },
  async members(id,direction=0) {
    const project=_curProj.id;this.membersOffsets[id]=Math.max(0,(this.membersOffsets[id]||0)+direction*50);
    try {const page=await api('GET',`/projects/${project}/groups/${id}/cases/page?offset=${this.membersOffsets[id]}&limit=50`);if(_curProj?.id!==project)return;if(this.membersOffsets[id]>=page.total&&this.membersOffsets[id]>0){this.membersOffsets[id]=Math.max(0,Math.floor((page.total-1)/50)*50);return this.members(id);}
      const group=_groups.find(group=>group.id===id);if(!group)return;group.test_cases=page.items;group.tc_count=page.total;renderGroups();}catch(error){toast(error.message,'e');}
  },
  memberPager(group){const offset=this.membersOffsets[group.id]||0;return group.tc_count>50?`<div class="list-pager"><button class="btn btn-sm btn-o" ${offset===0?'disabled':''} onclick="GroupList.members(${group.id},-1)">Previous cases</button> ${offset+1}–${Math.min(offset+50,group.tc_count)} of ${group.tc_count} <button class="btn btn-sm btn-o" ${offset+50>=group.tc_count?'disabled':''} onclick="GroupList.members(${group.id},1)">Next cases</button></div>`:'';}
};
const GroupPicker = {
  states:{},
  async attach(id){
    this.states[id]={offset:0};let controls=document.getElementById('suite-picker-'+id);
    if(!controls){controls=document.createElement('div');controls.id='suite-picker-'+id;document.getElementById(id).insertAdjacentElement('beforebegin',controls);controls.innerHTML=`<label for="suite-search-${id}">Search suites</label><input id="suite-search-${id}" oninput="GroupPicker.search('${id}')"><div id="suite-pages-${id}" class="list-pager"></div>`;}
    document.getElementById('suite-search-'+id).value='';await this.load(id);
  },
  search(id){clearTimeout(this.states[id].timer);this.states[id].timer=setTimeout(()=>{this.states[id].offset=0;this.load(id);},200);},
  async load(id,direction=0){
    const state=this.states[id];state.offset=Math.max(0,state.offset+direction*50);const project=_curProj.id;
    try{const page=await api('GET',`/projects/${project}/groups/page?offset=${state.offset}&limit=50&q=${encodeURIComponent(document.getElementById('suite-search-'+id).value)}`);if(_curProj?.id!==project)return;const select=document.getElementById(id);select.innerHTML='';page.items.forEach(group=>{const option=document.createElement('option');option.value=group.id;option.textContent=`${group.name} (${group.tc_count} TCs)`;select.appendChild(option);});
      document.getElementById('suite-pages-'+id).innerHTML=`<button class="btn btn-o btn-sm" ${state.offset===0?'disabled':''} onclick="GroupPicker.load('${id}',-1)">Previous suites</button> ${page.total?state.offset+1:0}–${Math.min(state.offset+50,page.total)} of ${page.total} <button class="btn btn-o btn-sm" ${state.offset+50>=page.total?'disabled':''} onclick="GroupPicker.load('${id}',1)">Next suites</button>`;
    }catch(error){toast(error.message,'e');}
  }
};
