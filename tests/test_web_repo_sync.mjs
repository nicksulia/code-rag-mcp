// Dependency-free browser regression checks. Run with node; set CHROME_BIN if needed.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const web = new URL('../web/', import.meta.url);
const profile = await mkdtemp(path.join(os.tmpdir(), 'rag-sync-browser-'));
const chrome = spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
  '--disable-component-update', '--disable-sync', '--disable-extensions', 'about:blank'
], { stdio: ['ignore', 'ignore', 'pipe'] });
const server = http.createServer(async (request, response) => {
  const filename = request.url === '/' ? 'index.html' : request.url.slice(1);
  if (!['index.html', 'app.js', 'style.css'].includes(filename)) {
    response.writeHead(404).end();
    return;
  }
  try {
    let content = await readFile(new URL(filename, web), 'utf8');
    if (filename === 'index.html') content = content.replace(/<link[^>]*https:\/\/[^>]*>/g, '');
    response.setHeader('Content-Type', filename.endsWith('.js') ? 'application/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    response.end(content);
  } catch {
    response.writeHead(500).end();
  }
});
let socket;
try {
  const debugURL = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Chrome did not become ready')), 15000);
    chrome.on('error', reject);
    chrome.stderr.on('data', data => {
      output += data;
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = new URL(debugURL).port;
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await once(socket, 'open');
  let nextId = 1;
  const pending = new Map();
  const exceptions = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
    if (pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    localStorage.setItem('theme', 'light');
    window.confirm = () => true;
    localStorage.setItem('scope', JSON.stringify({repoIds:['first'],groupNames:['platform'],expand:'upstream',expandDepth:2}));
    window.__fixture = {
      repos: ['first','middle','last'].map((repo_id, i) => ({
        repo_id, name: i === 0 ? '<img id="name-injection" onerror="window.injected=true">' : repo_id,
        source_type: i === 1 ? 'local' : 'git', url_or_path: '/fixture',
        branch: 'main', total_files:1, total_chunks:1, total_symbols:1,
        status:'ready', error_message:null, last_synced_at:100
      })),
      outcomes: {}, requests: [], refreshRepo:false, refreshGraph:false, save:null, hold:false,
      keys: [{key_id:'key-a',name:'<img id="key-injection" onerror="window.injected=true"> Key A',
        private_key_path:'/server/key-a',fingerprint:'SHA256:fixture-a',algorithm:'ED25519',repo_ids:[]}],
      keyError:false, keyHold:false, keyFailAfterWrite:false, keyMalformed:false
    };
    window.fetch = async (url, options = {}) => {
      const f = window.__fixture;
      const pathname = new URL(url, window.location.href).pathname;
      const payload = options.body ? JSON.parse(options.body) : {};
      const method = options.method || 'GET';
      f.requests.push({pathname, method, payload});
      const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers:{'Content-Type':'application/json'}});
      const metrics = repo_id => ({repo_id,status:payload.local_only ? 'indexed_local' : 'synced',
        added_files:1,modified_files:0,deleted_files:0,total_chunks:1,total_symbols:1,embedding_dimension:64,elapsed_seconds:0.1});
      if (pathname.startsWith('/api/v1/ssh-keys')) {
        if (f.keyHold && method === 'GET') {
          f.keyHold = false;
          const snapshot = structuredClone(f.keys);
          await new Promise(resolve => { window.__releaseKeys = resolve; });
          return json({ssh_keys:snapshot});
        }
        if (f.keyError) return json({error:'SSH key management requires loopback.',code:'ssh_http_forbidden',status:403},403);
        if (method === 'GET') return json(f.keyMalformed ? {ssh_keys:f.keys, unexpected:true} : {ssh_keys:f.keys});
        const id = decodeURIComponent(pathname.split('/')[4] || '');
        if (method === 'POST') {
          const key = {key_id:'key-new',name:payload.name,private_key_path:payload.private_key_path,
            fingerprint:'SHA256:fixture-new',algorithm:'ED25519',repo_ids:[]};
          f.keys.push(key);
          if (f.keyFailAfterWrite) { f.keyFailAfterWrite=false; f.keyError=true; }
          return json({ssh_key:key},201);
        }
        const key = f.keys.find(key => key.key_id === id);
        if (method === 'PUT') {
          if (!payload.name || [...payload.name].length > 80) return json({error:'Use a 1-80 character name.',code:'ssh_key_invalid',status:400},400);
          key.name = payload.name; return json({ssh_key:key});
        }
        if (method === 'DELETE') {
          if (key.repo_ids.length) return json({error:'Key is assigned to repositories.',code:'ssh_key_in_use',status:409,repo_ids:key.repo_ids},409);
          f.keys = f.keys.filter(key => key.key_id !== id);
          return json({success:true,deleted_key_id:id});
        }
      }
      if (pathname.endsWith('/sync')) {
        const id = decodeURIComponent(pathname.split('/')[4]);
        if (f.hold) { f.hold = false; await new Promise(resolve => { window.__release = resolve; }); }
        const outcome = f.outcomes[id];
        if (outcome === 'network') throw new Error('not public');
        if (outcome === 'malformed') return new Response('<html>proxy error</html>', {status:500});
        if (outcome === 'invalid') return json({});
        if (outcome === 'error') {
          const message = '<img id="error-injection" onerror="window.injected=true"> Git authentication failed.';
          const repo = f.repos.find(repo => repo.repo_id === id);
          if (repo) { repo.status='error'; repo.error_message=message; }
          return json({error:message,status:500,code:'git_authentication_failed',phase:'fetch',repo_id:id}, 500);
        }
        return json(metrics(id));
      }
      if (pathname === '/api/v1/repos' && method === 'GET') {
        if (f.refreshRepo) throw new Error('refresh unavailable');
        return json({repositories:f.repos});
      }
      if (pathname === '/api/v1/groups') return json({groups:[{name:'platform',members:['first']}]});
      if (pathname.endsWith('/relations')) return json({groups:['platform'],dependencies:[],dependents:[]});
      if (pathname === '/api/v1/graph/cross-repo') {
        if (f.refreshGraph) return json({error:'refresh unavailable'}, 500);
        return json({cross_repo_links:[]});
      }
      if (method === 'POST' || method === 'PUT') {
        if (f.save === 'network') throw new Error('connection lost');
        const id = method === 'POST' ? payload.repo_id : decodeURIComponent(pathname.split('/')[4]);
        if (f.save === 'saved' || f.save === 'unsaved') {
          const saved = f.save === 'saved';
          if (saved && !f.repos.some(repo => repo.repo_id === id)) f.repos.push({...f.repos[0],repo_id:id,name:payload.name,url_or_path:payload.url_or_path,ssh_key_id:payload.ssh_key_id ?? null,status:'error',error_message:'Git authentication failed.'});
          return json({error:'Git authentication failed.',status:500,code:'git_authentication_failed',phase:'clone',repo_id:id,config_saved:saved}, 500);
        }
        const previous = f.repos.find(repo => repo.repo_id === id) || f.repos[0];
        const repo = {...previous,repo_id:id,name:payload.name || id,
          url_or_path:payload.url_or_path || previous.url_or_path,
          ssh_key_id: 'ssh_key_id' in payload ? payload.ssh_key_id : previous.ssh_key_id};
        f.repos = f.repos.filter(existing => existing.repo_id !== id).concat(repo);
        return json(method === 'POST' ? {repository:repo} : {repo,sync_result:metrics(id)}, method === 'POST' ? 201 : 200);
      }
      return json({});
    };
    window.alert = message => { window.__alert = message; };
  ` });
  await send('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  for (let i = 0; i < 100; i++) {
    if (await evaluate("typeof state !== 'undefined' && document.querySelectorAll('#repos-grid .repo-card').length === 3")) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(await evaluate("document.querySelectorAll('#repos-grid .repo-card').length"), 3);
  assert.equal(await evaluate("document.querySelectorAll('.btn-index-local').length"), 2);
  assert.equal(await evaluate("document.querySelector('#name-injection') === null && !window.injected"), true);
  assert.equal(await evaluate("document.querySelector('.repo-sync-time').textContent.startsWith('Last remote sync:')"), true);
  await evaluate("document.querySelector('[data-tab=\"tab-repos\"]').click()");
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  const statusLayout = await evaluate(`(() => {
    const status=document.querySelector('#repos-grid .repo-operation-status');
    const rows=[...status.children].filter(row=>getComputedStyle(row).display!=='none');
    return {
      height:status.getBoundingClientRect().height,
      whiteSpace:getComputedStyle(status).whiteSpace,
      gaps:rows.slice(1).map((row,index)=>row.getBoundingClientRect().top-rows[index].getBoundingClientRect().bottom),
      errorHidden:getComputedStyle(status.querySelector('.repo-error-text')).display==='none'
    };
  })()`);
  assert.equal(statusLayout.whiteSpace, 'normal');
  assert.equal(statusLayout.errorHidden, true);
  assert.ok(statusLayout.height <= 80, JSON.stringify(statusLayout));
  assert.ok(statusLayout.gaps.every(gap=>Math.abs(gap-4)<1), JSON.stringify(statusLayout));
  await evaluate("document.documentElement.dataset.theme='dark'");
  assert.equal(await evaluate("document.querySelector('.repo-operation-status').getBoundingClientRect().height"), statusLayout.height);
  await evaluate("document.documentElement.dataset.theme='light'");

  await evaluate(`(async () => {
    __fixture.outcomes.first = 'error';
    document.querySelector('.btn-sync-repo').click();
    while (repositoryOperations.busy) await new Promise(resolve => setTimeout(resolve, 0));
  })()`);
  assert.match(await evaluate("document.querySelector('#repo-operation-summary').textContent"), /0 succeeded, 1 failed, 0 unconfirmed/);
  assert.match(await evaluate("document.querySelector('.repo-error-text').textContent"), /Git authentication failed/);
  assert.equal(await evaluate("__fixture.requests.filter(request => request.pathname.endsWith('/sync')).length"), 1);
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.repo-error-text')).display"), 'block');
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.repo-error-text')).whiteSpace"), 'pre-wrap');
  assert.equal(await evaluate(`(() => {
    const error=document.querySelector('.repo-error-text');
    const previous=error.textContent;
    error.textContent='First line\\nSecond line';
    const twoLines=Math.abs(error.getBoundingClientRect().height-2*parseFloat(getComputedStyle(error).lineHeight))<1;
    error.textContent=previous;
    return twoLines;
  })()`), true);
  assert.equal(await evaluate("__fixture.requests.find(request => request.pathname.endsWith('/sync')).payload.local_only"), undefined);
  assert.equal(await evaluate("document.querySelector('#error-injection') === null && !window.injected"), true);
  assert.equal(await evaluate("document.querySelector('#btn-sync-all').disabled"), false);
  await evaluate("loadRepositories({strict:true})");
  assert.match(await evaluate("document.querySelector('.repo-error-text').textContent"), /Git authentication failed/);

  for (const outcome of ['network', 'malformed', 'invalid']) {
    await evaluate(`__fixture.outcomes.first = '${outcome}'; syncRepositories([state.repos[0]])`);
    assert.match(await evaluate("document.querySelector('#repo-operation-summary').textContent"), /0 succeeded, 0 failed, 1 unconfirmed/);
    assert.equal(await evaluate("document.querySelector('#btn-sync-all').disabled"), false);
  }
  const remoteTime = await evaluate("document.querySelector('.repo-sync-time').textContent");
  await evaluate(`(async () => {
    __fixture.outcomes = {}; __fixture.requests = []; __fixture.refreshGraph = true;
    document.querySelector('.btn-index-local').click();
    while (repositoryOperations.busy) await new Promise(resolve => setTimeout(resolve, 0));
  })()`);
  assert.equal(await evaluate("__fixture.requests.find(request => request.pathname.endsWith('/sync')).payload.local_only"), true);
  assert.match(await evaluate("document.querySelector('#repo-operation-results').textContent"), /no remote update performed/);
  assert.equal(await evaluate("document.querySelector('.repo-sync-time').textContent"), remoteTime);
  assert.match(await evaluate("document.querySelector('#repo-operation-summary').textContent"), /1 succeeded, 0 failed/);
  assert.match(await evaluate("document.querySelector('#repo-operation-refresh').textContent"), /Graph refresh failed/);
  assert.equal(await evaluate("document.querySelector('#btn-sync-all').disabled"), false);

  await evaluate(`(async () => {
    __fixture.requests = []; __fixture.outcomes = {middle:'error',last:'network'};
    __fixture.refreshRepo = true; __fixture.hold = true;
    window.__run = syncRepositories(state.repos);
  })()`);
  assert.equal(await evaluate("document.querySelector('#btn-sync-all').disabled && document.querySelector('.btn-edit-repo').disabled && document.querySelector('#btn-import-projects').disabled"), true);
  await evaluate("syncRepositories(state.repos)");
  await evaluate("document.querySelector('.btn-index-local').click(); document.querySelector('.btn-edit-repo').click(); document.querySelector('#btn-open-add-repo').click()");
  assert.equal(await evaluate("__fixture.requests.filter(request => request.pathname.endsWith('/sync')).length"), 1);
  await evaluate("__fixture.repos = __fixture.repos.slice(0,1); __release(); __run");
  assert.deepEqual(await evaluate("__fixture.requests.filter(request => request.pathname.endsWith('/sync')).map(request => request.pathname.split('/')[4])"), ['first', 'middle', 'last']);
  assert.equal(await evaluate("__fixture.requests.filter(request => request.pathname.endsWith('/sync')).every(request => !('local_only' in request.payload))"), true);
  assert.match(await evaluate("document.querySelector('#repo-operation-summary').textContent"), /1 succeeded, 1 failed, 1 unconfirmed/);
  assert.match(await evaluate("document.querySelector('#repo-operation-refresh').textContent"), /Repository refresh failed.*Graph refresh failed/);
  assert.equal(await evaluate("document.querySelector('#btn-sync-all').disabled || document.querySelector('#btn-import-projects').disabled"), false);

  await evaluate(`(async () => {
    __fixture.refreshRepo = false; __fixture.refreshGraph = false; __fixture.save = 'saved';
    document.querySelector('#modal-add-repo').classList.remove('hidden');
    await saveRepositoryOperation('add', 'new', {repo_id:'new',name:'New',url_or_path:'/fixture',source_type:'git',auto_sync:true});
  })()`);
  assert.match(await evaluate("document.querySelector('#add-repo-status').textContent"), /Configuration saved; sync\/update failed/);
  assert.equal(await evaluate("document.querySelector('#modal-add-repo').classList.contains('hidden')"), false);
  assert.equal(await evaluate("state.repos.some(repo => repo.repo_id === 'new')"), true);
  assert.equal(await evaluate("document.querySelector('#input-repo-id').readOnly"), true);
  assert.equal(await evaluate("document.querySelector('#btn-submit-repo').textContent"), 'Save & Retry');
  await evaluate("__fixture.save = null; saveRepositoryOperation('add', 'new', {name:'Corrected',url_or_path:'/fixture',auto_sync:true})");
  assert.equal(await evaluate("__fixture.requests.at(-4).method === 'PUT' || __fixture.requests.some(request => request.pathname === '/api/v1/repos/new' && request.method === 'PUT')"), true);
  assert.equal(await evaluate("document.querySelector('#modal-add-repo').classList.contains('hidden')"), true);
  assert.equal(await evaluate("document.querySelector('[name=\"source_type\"]').disabled"), false);

  await evaluate(`(async () => {
    __fixture.refreshRepo = false; __fixture.save = null;
    await loadSSHKeys();
    document.querySelector('[data-tab="tab-ssh-keys"]').click();
    await loadSSHKeys();
  })()`);
  assert.equal(await evaluate("document.querySelector('#key-injection') === null && !window.injected"), true);
  assert.match(await evaluate("document.querySelector('.ssh-key-card h3').textContent"), /<img/);
  assert.equal(await evaluate("document.querySelector('#ssh-key-path').type"), 'text');
  const registrationLayout = await evaluate(`(() => {
    const form=document.querySelector('#ssh-key-form');
    const button=form.querySelector('button').getBoundingClientRect();
    const inputs=[...form.querySelectorAll('input')].map(input=>input.getBoundingClientRect());
    const header=document.querySelector('#tab-ssh-keys .pane-header');
    return {
      aligned:inputs.every(input=>Math.abs(input.bottom-button.bottom)<1),
      sameHeight:inputs.every(input=>Math.abs(input.height-button.height)<1),
      inset:form.getBoundingClientRect().left-header.getBoundingClientRect().left,
      guidanceWords:document.querySelector('.ssh-key-guidance').textContent.trim().split(/\\s+/).length,
      detailsOpen:document.querySelector('.ssh-key-help').open
    };
  })()`);
  assert.equal(registrationLayout.aligned, true, JSON.stringify(registrationLayout));
  assert.equal(registrationLayout.sameHeight, true, JSON.stringify(registrationLayout));
  assert.equal(registrationLayout.inset, 24);
  assert.ok(registrationLayout.guidanceWords <= 25);
  assert.equal(registrationLayout.detailsOpen, false);
  await evaluate("document.documentElement.dataset.theme='dark'");
  assert.equal(await evaluate(`(() => {
    const button=document.querySelector('#btn-register-ssh-key').getBoundingClientRect();
    return [...document.querySelectorAll('#ssh-key-form input')].every(input=>Math.abs(input.getBoundingClientRect().bottom-button.bottom)<1);
  })()`), true);
  await evaluate("document.documentElement.dataset.theme='light'");
  await evaluate("document.querySelector('.ssh-key-help summary').click()");
  assert.equal(await evaluate("document.querySelector('.ssh-key-help').open"), true);
  assert.match(await evaluate("document.querySelector('.ssh-key-help').textContent"), /ssh-add.*SSH_AUTH_SOCK.*loopback/);
  await evaluate("document.querySelector('.ssh-key-help summary').click()");
  await send('Emulation.setDeviceMetricsOverride', { width: 800, height: 1000, deviceScaleFactor: 1, mobile: false });
  assert.equal(await evaluate(`(() => {
    const form=document.querySelector('#ssh-key-form').getBoundingClientRect();
    return [...document.querySelectorAll('#ssh-key-form input, #ssh-key-form button')].every(element=>{
      const rect=element.getBoundingClientRect();
      return rect.left>=form.left && rect.right<=form.right+1;
    });
  })()`), true);
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await evaluate(`mutateSSHKey('POST',null,{name:'<b id="key-new-injection">Fixture</b>',private_key_path:'/server/synthetic'})`);
  assert.equal(await evaluate("document.querySelector('#key-new-injection') === null"), true);
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /Reference registered.*Unlock encrypted/);
  await evaluate("mutateSSHKey('PUT','key-new',{name:'Renamed fixture'})");
  assert.equal(await evaluate("sshKeys.profiles.find(key => key.key_id === 'key-new').name"), 'Renamed fixture');
  await evaluate(`__fixture.keys.find(key => key.key_id === 'key-new').repo_ids = ['first']; mutateSSHKey('DELETE','key-new')`);
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /assigned/);
  await evaluate(`__fixture.keys.find(key => key.key_id === 'key-new').repo_ids = []; mutateSSHKey('DELETE','key-new')`);
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /key files are unchanged/);

  await evaluate(`window.__keyWrites = __fixture.requests.filter(request => request.method === 'POST' && request.pathname === '/api/v1/ssh-keys').length`);
  for (const [name, path] of [['Valid', 'relative/key'], ['x'.repeat(81), '/server/key'], ['Valid', '/server/' + 'x'.repeat(4097)]]) {
    await evaluate(`document.querySelector('#ssh-key-name').value=${JSON.stringify(name)};
      document.querySelector('#ssh-key-path').value=${JSON.stringify(path)};
      document.querySelector('#ssh-key-form').dispatchEvent(new Event('submit',{cancelable:true}))`);
    assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /1-80 character name.*server-side/);
  }
  assert.equal(await evaluate("__fixture.requests.filter(request => request.method === 'POST' && request.pathname === '/api/v1/ssh-keys').length"), await evaluate("__keyWrites"));
  await evaluate(`(async()=>{ __fixture.keyHold=true;
    document.querySelector('#ssh-key-name').value='Concurrent fixture';
    document.querySelector('#ssh-key-path').value='/server/synthetic';
    document.querySelector('#ssh-key-form').dispatchEvent(new Event('submit',{cancelable:true}));
    while (!window.__releaseKeys || __fixture.keyHold) await new Promise(resolve=>setTimeout(resolve,0)); })()`);
  assert.equal(await evaluate("document.querySelector('#btn-register-ssh-key').disabled && document.querySelector('#ssh-key-name').disabled && document.querySelector('#btn-import-projects').disabled"), true);
  await evaluate(`mutateSSHKey('POST',null,{name:'Duplicate',private_key_path:'/server/synthetic'})`);
  assert.equal(await evaluate("__fixture.requests.filter(request => request.method === 'POST' && request.pathname === '/api/v1/ssh-keys').length"), (await evaluate("__keyWrites")) + 1);
  await evaluate(`(async()=>{ __releaseKeys(); while(repositoryOperations.busy) await new Promise(resolve=>setTimeout(resolve,0)); })()`);
  assert.equal(await evaluate("document.querySelector('#btn-register-ssh-key').disabled || document.querySelector('#ssh-key-name').disabled"), false);
  await evaluate(`(async()=> {
    const form=[...document.querySelectorAll('.ssh-key-rename-form')].find(form=>form.querySelector('input').value==='Concurrent fixture');
    form.querySelector('input').value='x'.repeat(81);
    form.dispatchEvent(new Event('submit',{cancelable:true}));
    while(repositoryOperations.busy) await new Promise(resolve=>setTimeout(resolve,0));
  })()`);
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /1-80 character name/);
  await evaluate(`(async()=> {
    const card=[...document.querySelectorAll('.ssh-key-card')].find(card=>card.querySelector('h3').textContent==='Concurrent fixture');
    card.querySelector('.btn-ghost').click();
    while(repositoryOperations.busy) await new Promise(resolve=>setTimeout(resolve,0));
  })()`);
  assert.equal(await evaluate("__fixture.keys.some(key=>key.key_id==='key-new')"), false);
  await evaluate(`__fixture.keyFailAfterWrite=true; mutateSSHKey('POST',null,{name:'Saved fixture',private_key_path:'/server/synthetic'})`);
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /Key metadata saved, but refresh failed/);
  assert.equal(await evaluate("sshKeys.loaded"), false);
  assert.equal(await evaluate("document.querySelector('#btn-register-ssh-key').disabled"), false);
  await evaluate(`(async()=>{ __fixture.keyError=false; await loadSSHKeys(); await mutateSSHKey('DELETE','key-new'); })()`);
  await evaluate(`__fixture.keyMalformed=true; loadSSHKeys()`);
  assert.equal(await evaluate("sshKeys.loaded"), false);
  assert.equal(await evaluate("sshKeys.profiles[0].key_id"), 'key-a');
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /invalid metadata response/);
  await evaluate(`__fixture.keyMalformed=false; loadSSHKeys()`);

  await evaluate(`(async () => {
    __fixture.keyHold = true;
    window.__oldKeyLoad = loadSSHKeys();
    __fixture.keys[0].name = 'Latest key name';
    await loadSSHKeys();
    __releaseKeys(); await __oldKeyLoad;
  })()`);
  assert.equal(await evaluate("sshKeys.profiles[0].name"), 'Latest key name');
  await evaluate(`(async()=> {
    window.__knownKeys=structuredClone(__fixture.keys);
    __fixture.keys=[]; await loadSSHKeys();
  })()`);
  assert.match(await evaluate("document.querySelector('#ssh-key-list').textContent"), /No SSH key references/);
  await evaluate(`__fixture.keys=__knownKeys; loadSSHKeys()`);
  await evaluate(`(async()=> {
    window.__cancelWrites=__fixture.requests.filter(request=>request.method==='POST').length;
    __fixture.keyHold=true;
    document.querySelector('#btn-add-repo-hub').click();
    document.querySelector('#btn-cancel-modal').click();
    await loadSSHKeys();
    __releaseKeys();
    await new Promise(resolve=>setTimeout(resolve,0));
  })()`);
  assert.equal(await evaluate("document.querySelector('#modal-add-repo').classList.contains('hidden')"), true);
  assert.equal(await evaluate("__fixture.requests.filter(request=>request.method==='POST').length"), await evaluate("__cancelWrites"));
  assert.equal(await evaluate("sshKeys.profiles[0].name"), 'Latest key name');

  await evaluate(`(async () => {
    document.querySelector('#btn-add-repo-hub').click(); await loadSSHKeys();
    document.querySelector('#input-repo-id').value='ssh-first';
    document.querySelector('#input-repo-name').value='SSH first';
    document.querySelector('[name="source_type"][value="git"]').checked=true;
    document.querySelector('#input-repo-path').value='git@fixture.invalid:first';
    refreshSSHSelectors();
    const selector=document.querySelector('#input-repo-ssh-key');
    selector.value='key-a'; selector.dispatchEvent(new Event('change'));
    __fixture.save='saved';
    document.querySelector('#add-repo-form').dispatchEvent(new Event('submit',{cancelable:true}));
    while(repositoryOperations.busy) await new Promise(resolve=>setTimeout(resolve,0));
  })()`);
  assert.equal(await evaluate("__fixture.requests.findLast(request=>request.method==='POST' && request.pathname==='/api/v1/repos').payload.ssh_key_id"), 'key-a');
  assert.equal(await evaluate("repositoryOperations.savedAddId"), 'ssh-first');
  assert.equal(await evaluate("document.querySelector('#input-repo-ssh-key').value"), 'key-a');
  await evaluate(`(async () => {
    __fixture.save=null;
    document.querySelector('#add-repo-form').dispatchEvent(new Event('submit',{cancelable:true}));
    while(repositoryOperations.busy) await new Promise(resolve=>setTimeout(resolve,0));
  })()`);
  assert.equal(await evaluate("__fixture.requests.findLast(request=>request.method==='PUT' && request.pathname==='/api/v1/repos/ssh-first').payload.ssh_key_id"), 'key-a');
  assert.equal(await evaluate("document.querySelector('#repos-grid').textContent.includes('/server/key-a')"), false);
  await evaluate(`(async()=> {
    document.querySelector('.btn-edit-repo[data-id="ssh-first"]').click(); await loadSSHKeys();
    document.querySelector('#edit-repo-path').value='https://fixture.invalid/repo';
    refreshSSHSelectors();
    window.__protocolWrites=__fixture.requests.filter(request=>request.method==='PUT').length;
    document.querySelector('#edit-repo-form').dispatchEvent(new Event('submit',{cancelable:true}));
  })()`);
  assert.equal(await evaluate("__fixture.requests.filter(request=>request.method==='PUT').length"), await evaluate("__protocolWrites"));
  assert.match(await evaluate("document.querySelector('#edit-repo-status').textContent"), /explicitly select System SSH/);
  await evaluate(`document.querySelector('#edit-repo-path').value='git@fixture.invalid:first'; refreshSSHSelectors()`);

  await evaluate(`(async () => {
    document.querySelector('.btn-edit-repo[data-id="ssh-first"]').click(); await loadSSHKeys();
    const selector=document.querySelector('#edit-repo-ssh-key');
    selector.value=''; selector.dispatchEvent(new Event('change'));
    document.querySelector('#edit-repo-form').dispatchEvent(new Event('submit',{cancelable:true}));
    while(repositoryOperations.busy) await new Promise(resolve=>setTimeout(resolve,0));
  })()`);
  assert.equal(await evaluate("__fixture.requests.findLast(request=>request.method==='PUT' && request.pathname==='/api/v1/repos/ssh-first').payload.ssh_key_id"), null);
  await evaluate(`(async () => {
    const repo=__fixture.repos.find(repo=>repo.repo_id==='ssh-first'); repo.ssh_key_id='key-a';
    await loadRepositories({strict:true});
    document.querySelector('.btn-edit-repo[data-id="ssh-first"]').click(); await loadSSHKeys();
    __fixture.keyError=true; await loadSSHKeys();
    window.__keyRequestCount=__fixture.requests.filter(request=>request.method==='PUT').length;
    document.querySelector('#edit-repo-form').dispatchEvent(new Event('submit',{cancelable:true}));
  })()`);
  assert.equal(await evaluate("__fixture.requests.filter(request=>request.method==='PUT').length"), await evaluate("__keyRequestCount"));
  assert.match(await evaluate("document.querySelector('#edit-repo-status').textContent"), /catalog is unavailable/);
  assert.equal(await evaluate("document.querySelector('#edit-repo-ssh-key').value"), 'key-a');
  assert.match(await evaluate("document.querySelector('#ssh-key-status').textContent"), /unavailable.*loopback/);
  await evaluate("__fixture.keyError=false; loadSSHKeys()");

  for (const [mode, phrase] of [['unsaved', 'Configuration was not saved'], ['network', 'before repeating registration']]) {
    await evaluate(`__fixture.save = '${mode}'; document.querySelector('#modal-add-repo').classList.remove('hidden'); saveRepositoryOperation('add','unconfirmed',{repo_id:'unconfirmed',name:'Unconfirmed',source_type:'git',url_or_path:'/fixture'})`);
    assert.ok((await evaluate("document.querySelector('#add-repo-status').textContent")).includes(phrase));
    assert.equal(await evaluate("document.querySelector('#modal-add-repo').classList.contains('hidden')"), false);
    assert.equal(await evaluate("state.repos.some(repo => repo.repo_id === 'unconfirmed')"), false);
    assert.equal(await evaluate("document.querySelector('#btn-submit-repo').disabled"), false);
  }

  for (const [mode, phrase] of [['unsaved', 'Configuration was not saved'], ['network', 'check the catalog before retrying']]) {
    await evaluate(`__fixture.save = '${mode}'; document.querySelector('#modal-edit-repo').classList.remove('hidden'); saveRepositoryOperation('edit','first',{name:'First'})`);
    assert.ok((await evaluate("document.querySelector('#edit-repo-status').textContent")).includes(phrase));
    assert.equal(await evaluate("document.querySelector('#modal-edit-repo').classList.contains('hidden')"), false);
    assert.equal(await evaluate("document.querySelector('#btn-submit-edit-repo').disabled"), false);
  }
  await evaluate("__fixture.save = null; __fixture.refreshRepo = true; saveRepositoryOperation('edit','first',{name:'First'})");
  assert.match(await evaluate("document.querySelector('#edit-repo-status').textContent"), /operation completed.*refresh failed/);
  assert.match(await evaluate("document.querySelector('#repo-operation-summary').textContent"), /1 succeeded, 0 failed/);
  assert.equal(await evaluate("document.documentElement.dataset.theme"), 'light');
  assert.deepEqual(await evaluate("Array.from(state.scope.repoIds)"), ['first']);
  assert.deepEqual(await evaluate("Array.from(state.scope.groupNames)"), ['platform']);
  assert.equal(await evaluate("state.scope.expand"), 'upstream');
  assert.equal(await evaluate("document.querySelector('#btn-export-projects').disabled"), false);
  assert.equal(await evaluate("document.querySelector('#btn-confirm-import').disabled"), true);
  await evaluate("document.querySelector('#btn-theme-toggle').click()");
  assert.equal(await evaluate("document.documentElement.dataset.theme"), 'dark');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('Browser repository sync checks passed: single/bulk/cached/save failures, refreshes, controls, text safety, scope and theme.');
} finally {
  if (socket) socket.close();
  server.close();
  if (chrome.pid && chrome.exitCode === null && chrome.signalCode === null) {
    const exited = once(chrome, 'exit');
    chrome.kill();
    await exited;
  }
  await rm(profile, { recursive: true, force: true });
}
