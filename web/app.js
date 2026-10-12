/**
 * Multi-Repository Code Search & Architecture Studio - Frontend Logic
 */

const API_BASE = window.location.origin;

// State
const state = {
  repos: [],
  groups: [],
  repoRelations: Object.create(null), // repo_id -> { groups: [], dependencies: [], dependents: [] }
  activeTab: 'tab-search',
  theme: localStorage.getItem('theme') || 'dark',
  scope: loadPersistedScope()
};

// -------------------------------------------------------------
// Shared Scope Persistence
// -------------------------------------------------------------
function loadPersistedScope() {
  const defaults = { repoIds: new Set(), groupNames: new Set(), expand: 'none', expandDepth: 1 };
  try {
    const raw = localStorage.getItem('scope');
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    return {
      repoIds: new Set(Array.isArray(parsed.repoIds) ? parsed.repoIds : []),
      groupNames: new Set(Array.isArray(parsed.groupNames) ? parsed.groupNames : []),
      expand: parsed.expand || 'none',
      expandDepth: parsed.expandDepth || 1
    };
  } catch (err) {
    console.error('Failed to load persisted scope:', err);
    return defaults;
  }
}

function persistScope() {
  localStorage.setItem('scope', JSON.stringify({
    repoIds: Array.from(state.scope.repoIds),
    groupNames: Array.from(state.scope.groupNames),
    expand: state.scope.expand,
    expandDepth: state.scope.expandDepth
  }));
}

// Drop scope references to repos/groups that no longer exist (e.g. deleted since last visit).
function pruneScopeAgainstLiveData() {
  const liveRepoIds = new Set(state.repos.map(r => r.repo_id));
  const liveGroupNames = new Set(state.groups.map(g => g.name));
  for (const id of Array.from(state.scope.repoIds)) {
    if (!liveRepoIds.has(id)) state.scope.repoIds.delete(id);
  }
  for (const name of Array.from(state.scope.groupNames)) {
    if (!liveGroupNames.has(name)) state.scope.groupNames.delete(name);
  }
}

// DOM Elements
const elements = {
  statRepos: document.getElementById('stat-repos'),
  statFiles: document.getElementById('stat-files'),
  statChunks: document.getElementById('stat-chunks'),
  statSymbols: document.getElementById('stat-symbols'),
  navTabs: document.querySelectorAll('.nav-tab'),
  tabPanes: document.querySelectorAll('.tab-pane'),
  repoFilterList: document.getElementById('repo-filter-list'),
  btnSelectAllRepos: document.getElementById('btn-select-all-repos'),
  groupFilterList: document.getElementById('group-filter-list'),
  expandSelect: document.getElementById('scope-expand-select'),
  expandDepthSelect: document.getElementById('scope-expand-depth'),
  scopeSummary: document.getElementById('scope-summary'),
  reposGrid: document.getElementById('repos-grid'),
  btnSyncAll: document.getElementById('btn-sync-all'),
  modalAddRepo: document.getElementById('modal-add-repo'),
  btnOpenAddRepo: document.getElementById('btn-open-add-repo'),
  btnAddRepoHub: document.getElementById('btn-add-repo-hub'),
  btnCloseModal: document.getElementById('btn-close-modal'),
  btnCancelModal: document.getElementById('btn-cancel-modal'),
  addRepoForm: document.getElementById('add-repo-form'),
  btnThemeToggle: document.getElementById('btn-theme-toggle'),
  codeDrawer: document.getElementById('code-drawer'),
  btnCloseDrawer: document.getElementById('btn-close-drawer'),
  drawerRepoBadge: document.getElementById('drawer-repo-badge'),
  drawerFilePath: document.getElementById('drawer-file-path'),
  drawerCodeContent: document.getElementById('drawer-code-content'),
  crossRepoApiTbody: document.getElementById('cross-repo-api-tbody'),
  symbolSearchInput: document.getElementById('symbol-search-input'),
  btnSearchSymbol: document.getElementById('btn-search-symbol'),
  symbolDetailView: document.getElementById('symbol-detail-view'),
  hybridSearchInput: document.getElementById('hybrid-search-input'),
  btnHybridSearch: document.getElementById('btn-hybrid-search'),
  hybridResultsList: document.getElementById('hybrid-results-list'),
  // Groups & Relation Elements
  modalManageGroups: document.getElementById('modal-manage-groups'),
  btnOpenGroupsModal: document.getElementById('btn-open-groups-modal'),
  btnCloseGroupsModal: document.getElementById('btn-close-groups-modal'),
  createGroupForm: document.getElementById('create-group-form'),
  newGroupName: document.getElementById('new-group-name'),
  groupsListContainer: document.getElementById('groups-list-container'),
  modalAddDependency: document.getElementById('modal-add-dependency'),
  btnCloseDepModal: document.getElementById('btn-close-dep-modal'),
  btnCancelDepModal: document.getElementById('btn-cancel-dep-modal'),
  addDepForm: document.getElementById('add-dep-form'),
  depSourceRepo: document.getElementById('dep-source-repo'),
  depSourceRepoDisplay: document.getElementById('dep-source-repo-display'),
  depTargetRepo: document.getElementById('dep-target-repo'),
  btnSubmitDep: document.getElementById('btn-submit-dep')
};

const transfer = { generation: 0, text: null, preview: null, importing: false };
const repositoryOperations = { busy: false, results: [], refreshErrors: [], controls: new Map(), savedAddId: null };
const sshKeys = { profiles: [], loaded: false, generation: 0, error: null };

function validSSHProfile(profile) {
  const fields = ['key_id', 'name', 'private_key_path', 'fingerprint', 'algorithm', 'repo_ids'];
  return profile && typeof profile === 'object' && !Array.isArray(profile)
    && Object.keys(profile).length === fields.length
    && fields.slice(0, -1).every(field => typeof profile[field] === 'string' && profile[field].length > 0)
    && Array.isArray(profile.repo_ids) && profile.repo_ids.every(id => typeof id === 'string' && id.length > 0)
    && new Set(profile.repo_ids).size === profile.repo_ids.length;
}

async function sshKeyRequest(method, keyId = null, payload = null) {
  let response, data;
  try {
    response = await fetch(`${API_BASE}/api/v1/ssh-keys${keyId === null ? '' : `/${encodeURIComponent(keyId)}`}`, {
      method, ...(payload === null ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    });
    data = await response.json();
  } catch {
    throw new Error('Key request outcome unconfirmed. Refresh the key catalog before repeating registration.');
  }
  if (!response.ok) {
    throw new Error(typeof data?.error === 'string' ? data.error.slice(0, 512) : 'Key request failed with an invalid server response.');
  }
  const fields = method === 'DELETE' ? ['success', 'deleted_key_id'] : [method === 'GET' ? 'ssh_keys' : 'ssh_key'];
  const exactShape = data && typeof data === 'object' && !Array.isArray(data)
    && Object.keys(data).length === fields.length && fields.every(field => Object.hasOwn(data, field));
  const valid = exactShape && (method === 'GET'
    ? Array.isArray(data?.ssh_keys) && data.ssh_keys.every(validSSHProfile)
      && new Set(data.ssh_keys.map(profile => profile.key_id)).size === data.ssh_keys.length
    : method === 'DELETE' ? data?.success === true && data.deleted_key_id === keyId
      : validSSHProfile(data?.ssh_key));
  if (!valid) throw new Error('Key request outcome unconfirmed: invalid metadata response. Refresh before retrying.');
  return data;
}

async function loadSSHKeys({ strict = false } = {}) {
  const generation = ++sshKeys.generation;
  sshKeys.loaded = false;
  refreshSSHSelectors();
  try {
    const data = await sshKeyRequest('GET');
    if (generation !== sshKeys.generation) {
      if (strict) throw new Error('Key refresh was superseded.');
      return;
    }
    sshKeys.profiles = data.ssh_keys;
    sshKeys.loaded = true;
    sshKeys.error = null;
    renderSSHKeys();
    renderRepositories();
  } catch (error) {
    if (generation !== sshKeys.generation) {
      if (strict) throw error;
      return;
    }
    sshKeys.error = error.message;
    transferStatus(document.getElementById('ssh-key-status'), `SSH key management unavailable: ${error.message}`, true);
    if (strict) throw error;
  } finally {
    if (generation === sshKeys.generation) refreshSSHSelectors();
  }
}

function eligibleSSHRemote(source, address) {
  if (source !== 'git' || /[\u0000-\u0020\u007f]/.test(address)) return false;
  if (address.startsWith('ssh://')) {
    try {
      const remote = new URL(address);
      return !!remote.hostname && !remote.password && !remote.search && !remote.hash && remote.pathname.length > 1;
    } catch { return false; }
  }
  return !address.includes('://') && /^(?:[^@/:\\]+@)?[a-zA-Z0-9_.-]+:[^:][^\r\n]*$/.test(address);
}

function refreshSSHSelectors() {
  for (const kind of ['add', 'edit']) {
    const select = document.getElementById(kind === 'add' ? 'input-repo-ssh-key' : 'edit-repo-ssh-key');
    const selected = select.dataset.assignment || '';
    const source = kind === 'add'
      ? elements.addRepoForm.querySelector('[name="source_type"]:checked')?.value
      : state.repos.find(repo => repo.repo_id === document.getElementById('edit-repo-id').value)?.source_type;
    const address = document.getElementById(kind === 'add' ? 'input-repo-path' : 'edit-repo-path').value.trim();
    select.replaceChildren(new Option('System SSH (default)', ''));
    sshKeys.profiles.forEach(profile => select.add(new Option(profile.name, profile.key_id)));
    if (selected && !sshKeys.profiles.some(profile => profile.key_id === selected)) {
      select.add(new Option(`Unavailable profile (${selected})`, selected));
    }
    select.value = selected;
    const eligible = eligibleSSHRemote(source, address);
    select.disabled = repositoryOperations.busy || !sshKeys.loaded || (!eligible && !selected);
    document.getElementById(kind === 'add' ? 'add-ssh-key-guidance' : 'edit-ssh-key-guidance').textContent =
      !sshKeys.loaded ? 'Key catalog unavailable or loading. Existing assignments will not be cleared automatically.'
        : !eligible ? selected ? 'This source is not SSH. Explicitly select System SSH to clear its assignment before saving.' : 'Profiles are available only for SSH Git remotes.'
          : 'System SSH inherits the server agent/configuration. Selected keys never fall back to other target identities.';
  }
  lockRepositoryControls();
}

function repositorySSHAssignment(kind, payload) {
  const select = document.getElementById(kind === 'add' ? 'input-repo-ssh-key' : 'edit-repo-ssh-key');
  const selected = select.dataset.assignment || '';
  const repo = kind === 'edit' ? state.repos.find(repo => repo.repo_id === document.getElementById('edit-repo-id').value) : null;
  const source = kind === 'add' ? payload.source_type : repo?.source_type;
  if (!sshKeys.loaded) {
    if (selected || repo?.ssh_key_id || eligibleSSHRemote(source, payload.url_or_path || '')) {
      throw new Error('The key catalog is unavailable or loading. Refresh keys before changing SSH configuration.');
    }
    return;
  }
  if (selected && (!eligibleSSHRemote(source, payload.url_or_path || '') || !sshKeys.profiles.some(profile => profile.key_id === selected))) {
    throw new Error('Select an available key for an SSH Git remote, or explicitly select System SSH.');
  }
  payload.ssh_key_id = selected || null;
}

function renderSSHKeys() {
  const list = document.getElementById('ssh-key-list');
  list.replaceChildren();
  if (!sshKeys.profiles.length) {
    const empty = document.createElement('p');
    empty.className = 'text-muted';
    empty.textContent = 'No SSH key references registered.';
    list.append(empty);
  }
  sshKeys.profiles.forEach(profile => {
    const card = document.createElement('div');
    card.className = 'repo-card ssh-key-card';
    const title = document.createElement('h3');
    title.textContent = profile.name;
    card.append(title);
    for (const value of [profile.private_key_path, `${profile.algorithm} ${profile.fingerprint}`]) {
      const line = document.createElement('p');
      line.className = 'ssh-key-metadata';
      line.textContent = value;
      card.append(line);
    }
    const associations = document.createElement('div');
    associations.textContent = profile.repo_ids.length ? 'Assigned repositories: ' : 'No assigned repositories.';
    profile.repo_ids.forEach(id => {
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'btn-link';
      link.textContent = state.repos.find(repo => repo.repo_id === id)?.name || id;
      link.addEventListener('click', () => {
        switchTab('tab-repos');
        [...document.querySelectorAll('.repo-card')].find(repo => repo.dataset.repoId === id)?.scrollIntoView();
      });
      associations.append(link);
    });
    card.append(associations);
    const rename = document.createElement('form');
    rename.className = 'ssh-key-rename-form';
    const input = document.createElement('input');
    input.className = 'input-text';
    input.value = profile.name;
    input.maxLength = 160;
    input.required = true;
    input.setAttribute('aria-label', 'New reference name');
    const save = document.createElement('button');
    save.className = 'btn btn-secondary ssh-key-action';
    save.textContent = 'Rename';
    rename.append(input, save);
    rename.addEventListener('submit', event => {
      event.preventDefault();
      mutateSSHKey('PUT', profile.key_id, { name: input.value.trim() });
    });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-ghost ssh-key-action';
    remove.textContent = 'Remove reference';
    remove.addEventListener('click', () => {
      if (!repositoryOperations.busy && confirm('Remove this reference only? Your key files will not be deleted.')) mutateSSHKey('DELETE', profile.key_id);
    });
    card.append(rename, remove);
    list.append(card);
  });
  lockRepositoryControls();
}

async function mutateSSHKey(method, keyId = null, payload = null) {
  if (!beginRepositoryOperation()) return;
  const status = document.getElementById('ssh-key-status');
  let saved = false;
  try {
    await sshKeyRequest(method, keyId, payload);
    saved = true;
    await loadSSHKeys({ strict: true });
    await refreshRepositoryOperation();
    const message = method === 'DELETE' ? 'Reference removed; your key files are unchanged.'
      : method === 'PUT' ? 'Reference renamed.' : 'Reference registered. Unlock encrypted keys outside this application.';
    transferStatus(status, message + (repositoryOperations.refreshErrors.length ? ` ${repositoryOperations.refreshErrors.join(' ')}` : ''), repositoryOperations.refreshErrors.length > 0);
    if (method === 'POST') document.getElementById('ssh-key-form').reset();
  } catch (error) {
    transferStatus(status, `${saved ? 'Key metadata saved, but refresh failed. ' : ''}${error.message}`, true);
  } finally {
    endRepositoryOperation();
  }
}
const transferElements = {
  export: document.getElementById('btn-export-projects'),
  open: document.getElementById('btn-import-projects'),
  modal: document.getElementById('modal-import-projects'),
  file: document.getElementById('import-projects-file'),
  close: document.getElementById('btn-close-import'),
  cancel: document.getElementById('btn-cancel-import'),
  confirm: document.getElementById('btn-confirm-import'),
  status: document.getElementById('import-projects-status'),
  hubStatus: document.getElementById('project-transfer-status'),
  preview: document.getElementById('import-projects-preview')
};

function transferStatus(target, message, error = false) {
  target.textContent = message;
  target.classList.toggle('is-error', error);
}

function resetProjectImport() {
  if (transfer.importing) return;
  transfer.generation++;
  transfer.text = null;
  transfer.preview = null;
  transferElements.file.value = '';
  transferElements.preview.replaceChildren();
  transferElements.confirm.disabled = true;
  transferStatus(transferElements.status, '');
}

async function checkedJSON(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

class RepositoryRequestError extends Error {
  constructor(message, confirmed = false, data = {}) {
    super(message);
    this.confirmed = confirmed;
    this.data = data;
  }
}

async function repositoryRequest(path, payload, kind = 'sync', method = 'POST') {
  let response, data;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    data = await response.json();
  } catch {
    throw new RepositoryRequestError('Outcome unconfirmed: no valid server response. Refresh and check the catalog before retrying, especially before repeating registration.');
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new RepositoryRequestError('Outcome unconfirmed: invalid server response. Check the catalog before retrying.');
  }
  if (!response.ok) {
    if (typeof data.error !== 'string' || data.status !== response.status || typeof data.code !== 'string') {
      throw new RepositoryRequestError('Outcome unconfirmed: invalid error response. Check the catalog before retrying.');
    }
    const safe = { error: data.error.slice(0, 512), code: data.code };
    for (const field of ['phase', 'repo_id']) {
      if (typeof data[field] === 'string') safe[field] = data[field];
    }
    if (typeof data.config_saved === 'boolean') safe.config_saved = data.config_saved;
    throw new RepositoryRequestError(safe.error, true, safe);
  }
  const validSync = result => result && typeof result.repo_id === 'string'
    && ['synced', 'indexed_local'].includes(result.status)
    && ['added_files', 'modified_files', 'deleted_files', 'total_chunks', 'total_symbols', 'embedding_dimension', 'elapsed_seconds']
      .every(field => typeof result[field] === 'number' && Number.isFinite(result[field]) && result[field] >= 0);
  const validRepo = repo => repo && typeof repo.repo_id === 'string'
    && ['ready', 'indexing', 'error', 'dirty'].includes(repo.status)
    && ['total_files', 'total_chunks', 'total_symbols']
      .every(field => typeof repo[field] === 'number' && Number.isFinite(repo[field]) && repo[field] >= 0);
  const valid = kind === 'sync' ? validSync(data)
    : kind === 'add' ? validRepo(data.repository)
      : validRepo(data.repo)
        && (data.sync_result === null || validSync(data.sync_result));
  if (!valid) throw new RepositoryRequestError('Outcome unconfirmed: invalid operation result. Check the catalog before retrying.');
  return data;
}

function lockRepositoryControls() {
  if (!repositoryOperations.busy) return;
  const selector = [
    '#btn-sync-all', '#btn-open-add-repo', '#btn-add-repo-hub',
    '#btn-submit-repo', '#btn-submit-edit-repo', '#btn-import-projects', '#btn-confirm-import',
    '#input-repo-ssh-key', '#edit-repo-ssh-key', '#btn-register-ssh-key', '#btn-refresh-ssh-keys',
    '#ssh-key-form input', '.ssh-key-action', '.ssh-key-rename-form input',
    '.btn-sync-repo', '.btn-index-local', '.btn-edit-repo', '.btn-del-repo', '.btn-connect-first'
  ].join(', ');
  document.querySelectorAll(selector).forEach(control => {
    if (!repositoryOperations.controls.has(control)) repositoryOperations.controls.set(control, control.disabled);
    control.disabled = true;
  });
}

function beginRepositoryOperation() {
  if (repositoryOperations.busy || transfer.importing) return false;
  repositoryOperations.busy = true;
  repositoryOperations.results = [];
  repositoryOperations.refreshErrors = [];
  lockRepositoryControls();
  renderRepositoryResults();
  return true;
}

function endRepositoryOperation() {
  repositoryOperations.busy = false;
  repositoryOperations.controls.forEach((disabled, control) => { control.disabled = disabled; });
  repositoryOperations.controls.clear();
  refreshSSHSelectors();
}

function renderRepositoryResults() {
  const results = repositoryOperations.results;
  const counts = { succeeded: 0, failed: 0, unconfirmed: 0 };
  const list = document.getElementById('repo-operation-results');
  list.replaceChildren();
  for (const result of results) {
    counts[result.outcome]++;
    const item = document.createElement('li');
    item.textContent = `${result.repoId}: ${result.message}`;
    item.classList.toggle('is-error', result.outcome !== 'succeeded');
    list.append(item);
  }
  document.getElementById('repo-operation-summary').textContent = results.length
    ? `${counts.succeeded} succeeded, ${counts.failed} failed, ${counts.unconfirmed} unconfirmed.${repositoryOperations.busy ? ' Working...' : ''}`
    : repositoryOperations.busy ? 'Repository operation in progress...' : '';
  document.getElementById('repo-operation-refresh').textContent = repositoryOperations.refreshErrors.join(' ');
}

function recordRepositoryFailure(repoId, error) {
  const confirmed = error instanceof RepositoryRequestError && error.confirmed;
  const data = error instanceof RepositoryRequestError ? error.data : {};
  let message = error instanceof RepositoryRequestError ? error.message : 'Outcome unconfirmed. Check the catalog before retrying.';
  if (data.config_saved === true) message = `Configuration saved; sync/update failed. ${message} Correct the saved entry; do not register it again.`;
  if (data.config_saved === false) message = `Configuration was not saved. ${message}`;
  if (data.code) message += ` [${data.code}${data.phase ? ` / ${data.phase}` : ''}]`;
  repositoryOperations.results.push({ repoId, outcome: confirmed ? 'failed' : 'unconfirmed', message, data });
  renderRepositoryResults();
  return message;
}

async function refreshRepositoryOperation() {
  for (const [label, loader] of [['Repository', loadRepositories], ['Graph', loadCrossRepoLinks], ['SSH key', loadSSHKeys]]) {
    try {
      await loader({ strict: true });
    } catch {
      repositoryOperations.refreshErrors.push(`${label} refresh failed. Known operation results are unchanged; reload to view current state.`);
    }
  }
  renderScopeSummary();
  renderRepositoryResults();
}

async function syncRepositories(repos, localOnly = false) {
  if (!beginRepositoryOperation()) return;
  const snapshot = repos.map(repo => ({ repo_id: repo.repo_id }));
  try {
    for (const repo of snapshot) {
      try {
        const result = await repositoryRequest(`/api/v1/repos/${encodeURIComponent(repo.repo_id)}/sync`, localOnly ? { local_only: true } : {});
        repositoryOperations.results.push({
          repoId: repo.repo_id, outcome: 'succeeded',
          message: result.status === 'indexed_local'
            ? 'Existing checkout indexed; no remote update performed.'
            : 'Sync complete.',
          data: result
        });
        renderRepositoryResults();
      } catch (error) {
        recordRepositoryFailure(repo.repo_id, error);
      }
    }
    await refreshRepositoryOperation();
  } finally {
    endRepositoryOperation();
    renderRepositoryResults();
  }
}

async function saveRepositoryOperation(kind, repoId, payload) {
  if (!beginRepositoryOperation()) return;
  const status = document.getElementById(kind === 'add' ? 'add-repo-status' : 'edit-repo-status');
  transferStatus(status, 'Saving configuration and preparing the repository...');
  const savedAddId = kind === 'add' ? repositoryOperations.savedAddId : null;
  const operationKind = savedAddId ? 'edit' : kind;
  const id = savedAddId || repoId;
  let succeeded = false;
  try {
    const result = await repositoryRequest(
      operationKind === 'add' ? '/api/v1/repos' : `/api/v1/repos/${encodeURIComponent(id)}`,
      payload, operationKind, operationKind === 'add' ? 'POST' : 'PUT'
    );
    succeeded = true;
    const message = 'Configuration saved; requested repository operation completed.';
    repositoryOperations.results.push({ repoId: id, outcome: 'succeeded', message, data: result });
    transferStatus(status, message);
  } catch (error) {
    const message = recordRepositoryFailure(id, error);
    transferStatus(status, message, true);
    if (kind === 'add' && error instanceof RepositoryRequestError && error.data.config_saved === true) {
      repositoryOperations.savedAddId = error.data.repo_id || id;
      document.getElementById('input-repo-id').readOnly = true;
      elements.addRepoForm.querySelectorAll('[name="source_type"]').forEach(radio => { radio.disabled = true; });
    }
  } finally {
    try {
      await refreshRepositoryOperation();
      if (repositoryOperations.refreshErrors.length) {
        status.textContent += ` ${repositoryOperations.refreshErrors.join(' ')}`;
      } else if (succeeded) {
        document.getElementById(kind === 'add' ? 'modal-add-repo' : 'modal-edit-repo').classList.add('hidden');
        if (kind === 'add') {
          elements.addRepoForm.reset();
          document.getElementById('input-repo-ssh-key').dataset.assignment = '';
          repositoryOperations.savedAddId = null;
          document.getElementById('input-repo-id').readOnly = false;
          elements.addRepoForm.querySelectorAll('[name="source_type"]').forEach(radio => { radio.disabled = false; });
        }
      }
    } finally {
      endRepositoryOperation();
      document.getElementById('btn-submit-repo').textContent = repositoryOperations.savedAddId ? 'Save & Retry' : 'Index & Register';
      renderRepositoryResults();
    }
  }
}

async function exportProjects() {
  if (transferElements.export.disabled) return;
  transferElements.export.disabled = true;
  transferStatus(transferElements.hubStatus, 'Exporting configuration...');
  let objectURL;
  try {
    const response = await fetch(`${API_BASE}/api/v1/projects/export`);
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || `Export failed (${response.status})`);
    }
    const blob = await response.blob();
    objectURL = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectURL;
    anchor.download = 'projects.json';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    transferStatus(transferElements.hubStatus, 'Configuration exported as projects.json. Keep private names and paths secure.');
  } catch (error) {
    transferStatus(transferElements.hubStatus, `Export failed: ${error.message}`, true);
  } finally {
    if (objectURL) setTimeout(() => URL.revokeObjectURL(objectURL), 0);
    transferElements.export.disabled = false;
  }
}

function renderTransferSummary(summary) {
  transferElements.preview.replaceChildren();
  const category = (title, entries) => {
    const heading = document.createElement('h4');
    heading.textContent = `${title} (${entries.length})`;
    transferElements.preview.append(heading);
    const list = document.createElement('ul');
    for (const entry of entries) {
      const item = document.createElement('li');
      item.textContent = entry;
      list.append(item);
    }
    transferElements.preview.append(list);
  };
  category('New repositories', summary.additions.repositories.ids);
  category('New groups', summary.additions.groups.names);
  category('New memberships', summary.additions.memberships.entries.map(entry => `${entry.group}: ${entry.repo_id}`));
  category('New dependencies', summary.additions.dependencies.entries.map(entry => `${entry.repo_id} -> ${entry.depends_on_repo_id}`));
  category('Unchanged repositories', summary.unchanged_repositories);
  category('Conflicts', summary.conflicts.map(entry => `${entry.repo_id}: differing ${entry.fields.join(', ')}`));
  category('Local-path warnings', summary.warnings.map(entry => `${entry.repo_id}: machine-specific path; review with Edit / Branch before Sync.`));
}

function projectImportRequest(text, dryRun) {
  // Embed the original JSON so duplicate keys reach authoritative server validation.
  return checkedJSON(`${API_BASE}/api/v1/projects/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: `{"document":${text},"dry_run":${dryRun}}`
  });
}

async function previewProjectFile() {
  if (transfer.importing) return;
  const generation = ++transfer.generation;
  const file = transferElements.file.files[0];
  transfer.text = null;
  transfer.preview = null;
  transferElements.confirm.disabled = true;
  transferElements.preview.replaceChildren();
  transferStatus(transferElements.status, '');
  if (!file) return;
  if (file.size > 1_048_576) {
    transferStatus(transferElements.status, 'File exceeds the 1 MiB (1048576-byte) limit.', true);
    return;
  }
  transferStatus(transferElements.status, 'Validating configuration and preparing preview...');
  try {
    const text = await file.text();
    if (generation !== transfer.generation) return;
    const preview = await projectImportRequest(text, true);
    if (generation !== transfer.generation) return;
    transfer.text = text;
    transfer.preview = preview;
    renderTransferSummary(preview);
    transferElements.confirm.disabled = !preview.can_import;
    transferStatus(transferElements.status, preview.can_import
      ? 'Preview ready. Confirm Import to add this configuration without indexing.'
      : 'Repository conflicts block the entire import. Adjust the file and select it again.', !preview.can_import);
  } catch (error) {
    if (generation !== transfer.generation) return;
    transferStatus(transferElements.status, `Preview failed: ${error.message}`, true);
  }
}

async function confirmProjectImport() {
  if (repositoryOperations.busy || transfer.importing || !transfer.preview?.can_import || transfer.text === null) return;
  transfer.importing = true;
  transferElements.confirm.disabled = true;
  for (const control of [transferElements.file, transferElements.cancel, transferElements.close]) control.disabled = true;
  transferStatus(transferElements.status, 'Importing configuration...');
  let persisted = false;
  try {
    const summary = await projectImportRequest(transfer.text, false);
    persisted = true;
    transfer.text = null;
    transfer.preview = null;
    renderTransferSummary(summary);
    const additions = summary.additions;
    const message = `Configuration saved: ${additions.repositories.count} repositories, ${additions.groups.count} groups, ${additions.memberships.count} memberships, ${additions.dependencies.count} dependencies added. Import does not index code. Review local paths with Edit / Branch, then use Sync.`;
    transferStatus(transferElements.status, message);
    transferStatus(transferElements.hubStatus, message);
    await loadGroups({ strict: true });
    await loadRepositories({ strict: true });
    pruneScopeAgainstLiveData();
    persistScope();
    renderRepositories();
    renderGroupFilterList();
    renderGroupsModal();
    renderScopeSummary();
  } catch (error) {
    // A failed refresh must not suggest that a committed import was rolled back.
    const message = persisted
      ? `Configuration was saved, but refreshing the hub failed: ${error.message}. Reload the page to view it; do not repeat the import just to refresh.`
      : `Import request failed: ${error.message}. Preview the file again before retrying.`;
    transfer.text = null;
    transfer.preview = null;
    transferStatus(transferElements.status, message, true);
    transferStatus(transferElements.hubStatus, message, true);
  } finally {
    transfer.importing = false;
    for (const control of [transferElements.file, transferElements.cancel, transferElements.close]) control.disabled = false;
  }
}

// Initialize Application
async function init() {
  document.documentElement.setAttribute('data-theme', state.theme);
  setupEventListeners();
  await loadGroups();
  await loadSSHKeys();
  await loadRepositories();
  await loadCrossRepoLinks();
  pruneScopeAgainstLiveData();
  persistScope();
  renderRepositories();
  renderGroupFilterList();
  renderScopeSummary();
}

function setupEventListeners() {
  document.getElementById('ssh-key-form').addEventListener('submit', event => {
    event.preventDefault();
    const name = document.getElementById('ssh-key-name').value.trim();
    const private_key_path = document.getElementById('ssh-key-path').value;
    if (!name || [...name].length > 80 || /[\u0000-\u001f\u007f-\u009f]/.test(name + private_key_path)
        || new TextEncoder().encode(private_key_path).length > 4096
        || !(private_key_path.startsWith('/') || private_key_path.startsWith('~/'))) {
      transferStatus(document.getElementById('ssh-key-status'), 'Use a 1-80 character name and an absolute server-side or ~/ path of at most 4096 UTF-8 bytes, without controls.', true);
      return;
    }
    mutateSSHKey('POST', null, { name, private_key_path });
  });
  document.getElementById('btn-refresh-ssh-keys').addEventListener('click', () => {
    if (!repositoryOperations.busy) loadSSHKeys();
  });
  for (const id of ['input-repo-ssh-key', 'edit-repo-ssh-key']) {
    document.getElementById(id).addEventListener('change', event => {
      event.target.dataset.assignment = event.target.value;
      refreshSSHSelectors();
    });
  }
  for (const id of ['input-repo-path', 'edit-repo-path']) document.getElementById(id).addEventListener('input', refreshSSHSelectors);
  elements.addRepoForm.querySelectorAll('[name="source_type"]').forEach(radio => radio.addEventListener('change', refreshSSHSelectors));
  transferElements.export.addEventListener('click', exportProjects);
  transferElements.open.addEventListener('click', () => {
    if (repositoryOperations.busy || transfer.importing) return;
    resetProjectImport();
    transferElements.modal.classList.remove('hidden');
  });
  const cancelImport = () => {
    if (transfer.importing) return;
    resetProjectImport();
    transferElements.modal.classList.add('hidden');
  };
  transferElements.cancel.addEventListener('click', cancelImport);
  transferElements.close.addEventListener('click', cancelImport);
  transferElements.file.addEventListener('change', previewProjectFile);
  transferElements.confirm.addEventListener('click', confirmProjectImport);
  // Theme Toggle
  elements.btnThemeToggle.addEventListener('click', () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
    localStorage.setItem('theme', state.theme);
  });

  // Scope: Expansion Direction & Depth (shared sidebar controls)
  if (elements.expandSelect) {
    elements.expandSelect.value = state.scope.expand;
    elements.expandSelect.addEventListener('change', () => {
      state.scope.expand = elements.expandSelect.value;
      persistScope();
      renderScopeSummary();
    });
  }
  if (elements.expandDepthSelect) {
    elements.expandDepthSelect.value = String(state.scope.expandDepth);
    elements.expandDepthSelect.addEventListener('change', () => {
      state.scope.expandDepth = parseInt(elements.expandDepthSelect.value, 10);
      persistScope();
      renderScopeSummary();
    });
  }

  // Tab Navigation
  elements.navTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      switchTab(target);
    });
  });

  // Modal Open / Close (Add Repo)
  const openModal = () => {
    if (repositoryOperations.busy || transfer.importing) return;
    if (repositoryOperations.savedAddId) {
      elements.addRepoForm.reset();
      document.getElementById('input-repo-ssh-key').dataset.assignment = '';
      repositoryOperations.savedAddId = null;
      document.getElementById('input-repo-id').readOnly = false;
      elements.addRepoForm.querySelectorAll('[name="source_type"]').forEach(radio => { radio.disabled = false; });
      document.getElementById('btn-submit-repo').textContent = 'Index & Register';
    }
    document.getElementById('add-repo-status').textContent = '';
    elements.modalAddRepo.classList.remove('hidden');
    refreshSSHSelectors();
    loadSSHKeys();
  };
  const closeModal = () => {
    if (!repositoryOperations.busy) elements.modalAddRepo.classList.add('hidden');
  };
  elements.btnOpenAddRepo.addEventListener('click', openModal);
  elements.btnAddRepoHub.addEventListener('click', openModal);
  elements.btnCloseModal.addEventListener('click', closeModal);
  elements.btnCancelModal.addEventListener('click', closeModal);

  // Groups Modal Open / Close
  if (elements.btnOpenGroupsModal) {
    elements.btnOpenGroupsModal.addEventListener('click', () => {
      elements.modalManageGroups.classList.remove('hidden');
      renderGroupsModal();
    });
  }
  if (elements.btnCloseGroupsModal) {
    elements.btnCloseGroupsModal.addEventListener('click', () => {
      elements.modalManageGroups.classList.add('hidden');
    });
  }

  // Create Group Form
  if (elements.createGroupForm) {
    elements.createGroupForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = elements.newGroupName.value.trim();
      if (!name) return;
      try {
        const res = await fetch(`${API_BASE}/api/v1/groups`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, repo_ids: [] })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to create group');
        elements.newGroupName.value = '';
        await loadGroups();
        renderGroupsModal();
        await loadRepositories();
      } catch (err) {
        alert(`Error creating group: ${err.message}`);
      }
    });
  }

  // Dependency Modal Open / Close
  const closeDepModal = () => elements.modalAddDependency.classList.add('hidden');
  if (elements.btnCloseDepModal) elements.btnCloseDepModal.addEventListener('click', closeDepModal);
  if (elements.btnCancelDepModal) elements.btnCancelDepModal.addEventListener('click', closeDepModal);

  if (elements.addDepForm) {
    elements.addDepForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const sourceRepo = elements.depSourceRepo.value;
      const targetRepo = elements.depTargetRepo.value;
      if (!sourceRepo || !targetRepo) return;

      try {
        const res = await fetch(`${API_BASE}/api/v1/repos/${encodeURIComponent(sourceRepo)}/dependencies`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ depends_on: targetRepo })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to add dependency');

        closeDepModal();
        await loadRepositories();
      } catch (err) {
        alert(`Error adding dependency: ${err.message}`);
      }
    });
  }

  // Edit Modal Listeners
  const modalEditRepo = document.getElementById('modal-edit-repo');
  const btnCloseEditModal = document.getElementById('btn-close-edit-modal');
  const btnCancelEditModal = document.getElementById('btn-cancel-edit-modal');
  const editRepoForm = document.getElementById('edit-repo-form');

  const closeEditModal = () => {
    if (!repositoryOperations.busy) modalEditRepo.classList.add('hidden');
  };
  if (btnCloseEditModal) btnCloseEditModal.addEventListener('click', closeEditModal);
  if (btnCancelEditModal) btnCancelEditModal.addEventListener('click', closeEditModal);

  if (editRepoForm) {
    editRepoForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const repoId = document.getElementById('edit-repo-id').value;
      const payload = {
        name: document.getElementById('edit-repo-name').value.trim(),
        branch: document.getElementById('edit-repo-branch').value.trim(),
        url_or_path: document.getElementById('edit-repo-path').value.trim(),
        auto_sync: document.getElementById('edit-auto-sync').checked
      };

      try {
        repositorySSHAssignment('edit', payload);
        await saveRepositoryOperation('edit', repoId, payload);
      } catch (error) {
        transferStatus(document.getElementById('edit-repo-status'), error.message, true);
      }
    });
  }

  // Add Repo Submit
  elements.addRepoForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      repo_id: document.getElementById('input-repo-id').value.trim(),
      name: document.getElementById('input-repo-name').value.trim(),
      source_type: elements.addRepoForm.querySelector('[name="source_type"]:checked').value,
      url_or_path: document.getElementById('input-repo-path').value.trim(),
      branch: document.getElementById('input-repo-branch').value.trim() || 'main',
      auto_sync: true
    };

    try {
      repositorySSHAssignment('add', payload);
      await saveRepositoryOperation('add', payload.repo_id, payload);
    } catch (error) {
      transferStatus(document.getElementById('add-repo-status'), error.message, true);
    }
  });

  // Sync All
  elements.btnSyncAll.addEventListener('click', () => syncRepositories(state.repos));

  // Close Drawer
  elements.btnCloseDrawer.addEventListener('click', () => {
    elements.codeDrawer.classList.add('hidden');
  });

  // Symbol Search
  elements.btnSearchSymbol.addEventListener('click', handleSymbolSearch);
  elements.symbolSearchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleSymbolSearch();
  });

  // Hybrid Search
  elements.btnHybridSearch.addEventListener('click', handleHybridSearch);
  elements.hybridSearchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleHybridSearch();
  });
}

function switchTab(tabId) {
  state.activeTab = tabId;
  elements.navTabs.forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
  elements.tabPanes.forEach(p => p.classList.toggle('active', p.id === tabId));
  if (tabId === 'tab-ssh-keys') loadSSHKeys();
}

// -------------------------------------------------------------
// Load & Render Groups
// -------------------------------------------------------------
async function loadGroups({ strict = false } = {}) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/groups`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to load groups');
    state.groups = data.groups || [];
    renderGroupFilterList();
  } catch (err) {
    if (strict) throw err;
    console.error('Failed to load groups:', err);
  }
}

function renderGroupFilterList() {
  if (!elements.groupFilterList) return;
  if (state.groups.length === 0) {
    elements.groupFilterList.innerHTML = '<div class="text-muted small">No groups created yet.</div>';
    return;
  }
  elements.groupFilterList.innerHTML = state.groups.map(() => `
    <label class="repo-filter-item">
      <input type="checkbox">
      <span></span>
    </label>
  `).join('');

  elements.groupFilterList.querySelectorAll('input').forEach((cb, index) => {
    const group = state.groups[index];
    cb.value = group.name;
    cb.checked = state.scope.groupNames.has(group.name);
    cb.nextElementSibling.textContent = `🏷️ ${group.name} (${group.members.length} repos)`;
    cb.addEventListener('change', () => {
      if (cb.checked) {
        state.scope.groupNames.add(cb.value);
      } else {
        state.scope.groupNames.delete(cb.value);
      }
      persistScope();
      renderScopeSummary();
    });
  });
}

// -------------------------------------------------------------
// Scope Summary (client-side, resolved from state.repos/state.groups)
// -------------------------------------------------------------
function renderScopeSummary() {
  if (!elements.scopeSummary) return;

  const repoIds = state.scope.repoIds;
  const groupNames = Array.from(state.scope.groupNames);
  const groupMemberIds = new Set();
  groupNames.forEach(name => {
    const group = state.groups.find(g => g.name === name);
    if (group && Array.isArray(group.members)) {
      group.members.forEach(m => groupMemberIds.add(m));
    }
  });

  const primarySet = new Set([...repoIds, ...groupMemberIds]);
  const usingAllRepos = repoIds.size === 0 && groupNames.length === 0;

  let summaryText;
  if (usingAllRepos) {
    const total = state.repos.length;
    summaryText = `All repositories (${total})`;
  } else {
    const parts = [];
    if (repoIds.size > 0) parts.push(`${repoIds.size} repo${repoIds.size === 1 ? '' : 's'}`);
    if (groupNames.length > 0) parts.push(`${groupNames.length} group${groupNames.length === 1 ? '' : 's'}`);
    summaryText = `${parts.join(' ∪ ')} (${primarySet.size} total)`;
  }

  if (state.scope.expand !== 'none') {
    summaryText += ` + ${state.scope.expand} deps (depth ${state.scope.expandDepth}) — resolved at query time`;
  }

  elements.scopeSummary.textContent = summaryText;
}

// Build the shared request payload (repo_ids, groups, expand, expand_depth) used by
// the Hybrid Search path.
function buildScopeRequestPayload() {
  const repoIds = Array.from(state.scope.repoIds);
  const groupNames = Array.from(state.scope.groupNames);
  return {
    repo_ids: repoIds.length > 0 ? repoIds : null,
    groups: groupNames.length > 0 ? groupNames : null,
    expand: state.scope.expand,
    expand_depth: state.scope.expandDepth
  };
}

function renderGroupsModal() {
  if (!elements.groupsListContainer) return;
  if (state.groups.length === 0) {
    elements.groupsListContainer.innerHTML = '<div class="text-muted text-center py-4">No groups created yet. Use the field above to create one.</div>';
    return;
  }

  elements.groupsListContainer.innerHTML = state.groups.map(g => `
    <div style="background:var(--bg-surface-raised); border:1px solid var(--border-subtle); border-radius:6px; padding:12px; margin-bottom:12px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <strong class="group-name" style="font-size:14px;"></strong>
        <button class="btn btn-ghost btn-sm text-muted btn-delete-group" title="Delete Group">🗑️ Delete</button>
      </div>
      <div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center;">
        <span class="text-muted small">Members:</span>
        ${g.members.length === 0 ? '<span class="text-muted small">None</span>' : ''}
        ${g.members.map(m => `
          <span class="tag-group-item">
            ${escapeHtml(m)}
            <button class="btn-tag-remove btn-remove-member" data-repo="${escapeHtml(m)}">✕</button>
          </span>
        `).join('')}
        <select class="select-sm select-add-member">
          <option value="">+ Add Repo...</option>
        </select>
      </div>
    </div>
  `).join('');
  Array.from(elements.groupsListContainer.children).forEach((card, index) => {
    const group = state.groups[index];
    card.querySelector('.group-name').textContent = `🏷️ ${group.name}`;
    card.querySelectorAll('button, select').forEach(control => { control.dataset.group = group.name; });
    const select = card.querySelector('.select-add-member');
    state.repos.filter(repo => !group.members.includes(repo.repo_id)).forEach(repo => {
      select.append(new Option(repo.name || repo.repo_id, repo.repo_id));
    });
  });
  elements.groupsListContainer.querySelectorAll('.btn-delete-group').forEach(button => {
    button.addEventListener('click', () => window.deleteGroup(button.dataset.group));
  });
  elements.groupsListContainer.querySelectorAll('.btn-remove-member').forEach(button => {
    button.addEventListener('click', () => window.removeMemberFromGroup(button.dataset.group, button.dataset.repo));
  });
  elements.groupsListContainer.querySelectorAll('.select-add-member').forEach(select => {
    select.addEventListener('change', () => {
      if (select.value) window.addMemberToGroup(select.dataset.group, select.value);
      select.value = '';
    });
  });
}

window.deleteGroup = async function(name) {
  if (!confirm(`Delete group '${name}'? (Repositories will not be deleted)`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/v1/groups/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete group');
    await loadGroups();
    renderGroupsModal();
    await loadRepositories();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
};

window.addMemberToGroup = async function(groupName, repoId) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/groups/${encodeURIComponent(groupName)}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_ids: [repoId] })
    });
    if (!res.ok) throw new Error('Failed to add member to group');
    await loadGroups();
    renderGroupsModal();
    await loadRepositories();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
};

window.removeMemberFromGroup = async function(groupName, repoId) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/groups/${encodeURIComponent(groupName)}/members/${encodeURIComponent(repoId)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to remove member from group');
    await loadGroups();
    renderGroupsModal();
    await loadRepositories();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
};

window.openAddDependencyModal = function(sourceRepoId) {
  elements.depSourceRepo.value = sourceRepoId;
  elements.depSourceRepoDisplay.value = sourceRepoId;
  elements.depTargetRepo.replaceChildren(...state.repos
    .filter(r => r.repo_id !== sourceRepoId)
    .map(r => new Option(r.name || r.repo_id, r.repo_id)));
  elements.modalAddDependency.classList.remove('hidden');
};

window.removeDependencyEdge = async function(sourceRepo, targetRepo) {
  if (!confirm(`Remove dependency: ${sourceRepo} -> ${targetRepo}?`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/v1/repos/${encodeURIComponent(sourceRepo)}/dependencies/${encodeURIComponent(targetRepo)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to remove dependency edge');
    await loadRepositories();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
};

// -------------------------------------------------------------
// Load & Render Repositories
// -------------------------------------------------------------
async function loadRepositories({ strict = false } = {}) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/repos`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to load repositories');
    const repos = data.repositories || [];
    const relations = Object.create(null);

    // Fetch relations for each repository in parallel
    const relationPromises = repos.map(async (r) => {
      try {
        const relRes = await fetch(`${API_BASE}/api/v1/repos/${encodeURIComponent(r.repo_id)}/relations`);
        const relData = await relRes.json();
        if (!relRes.ok) throw new Error(relData.error || 'Failed to load repository relations');
        relations[r.repo_id] = relData;
      } catch (err) {
        if (strict) throw err;
        console.error(`Failed to load relations for ${r.repo_id}:`, err);
      }
    });
    await Promise.all(relationPromises);
    state.repos = repos;
    state.repoRelations = relations;

    renderRepositories();
    updateStats();
  } catch (err) {
    if (strict) throw err;
    console.error('Failed to load repositories:', err);
  }
}

function renderRepositories() {
  // Update Filter Box
  if (state.repos.length === 0) {
    elements.repoFilterList.innerHTML = '<div class="text-muted small">No repositories indexed yet.</div>';
  } else {
    elements.repoFilterList.innerHTML = state.repos.map(r => `
      <label class="repo-filter-item">
        <input type="checkbox" value="${escapeHtml(r.repo_id)}" ${state.scope.repoIds.size === 0 || state.scope.repoIds.has(r.repo_id) ? 'checked' : ''}>
        <span></span>
      </label>
    `).join('');

    elements.repoFilterList.querySelectorAll('input').forEach((cb, index) => {
      cb.nextElementSibling.textContent = state.repos[index].name || state.repos[index].repo_id;
      cb.addEventListener('change', () => {
        if (cb.checked) {
          state.scope.repoIds.add(cb.value);
        } else {
          state.scope.repoIds.delete(cb.value);
        }
        persistScope();
        renderScopeSummary();
      });
    });
  }

  // Update Repos Grid
  if (state.repos.length === 0) {
    elements.reposGrid.innerHTML = `
      <div class="welcome-card" style="grid-column: 1 / -1;">
        <h3>No Code Repositories Connected</h3>
        <p>Connect your first local codebase or remote Git repo to enable multi-repository RAG.</p>
        <button class="btn btn-primary btn-connect-first">+ Connect Repository</button>
      </div>
    `;
    elements.reposGrid.querySelector('.btn-connect-first').addEventListener('click', () => {
      if (repositoryOperations.busy || transfer.importing) return;
      elements.modalAddRepo.classList.remove('hidden');
    });
    lockRepositoryControls();
    return;
  }

  elements.reposGrid.innerHTML = state.repos.map(r => {
    const relations = state.repoRelations[r.repo_id] || { groups: [], dependencies: [], dependents: [] };
    return `
      <div class="repo-card">
        <div class="repo-card-header">
          <div>
            <div class="repo-card-title"></div>
            <div class="repo-card-path"></div>
            ${r.branch ? '<div class="repo-branch-tag">🌿 <code></code></div>' : ''}
          </div>
          <span class="badge-openspec"></span>
        </div>
        <div class="repo-operation-status">
          <div class="repo-status-text"></div>
          <div class="repo-sync-time text-muted small"></div>
          <div class="repo-error-text is-error"></div>
          <div class="repo-ssh-label text-muted small"></div>
        </div>

        <div class="repo-metrics">
          <div class="repo-metric-item">
            <span class="text-muted small">Files</span>
            <span class="repo-metric-num">${r.total_files}</span>
          </div>
          <div class="repo-metric-item">
            <span class="text-muted small">Chunks</span>
            <span class="repo-metric-num">${r.total_chunks}</span>
          </div>
          <div class="repo-metric-item">
            <span class="text-muted small">Symbols</span>
            <span class="repo-metric-num">${r.total_symbols}</span>
          </div>
        </div>

        <!-- Repository Relations Section -->
        <div class="repo-relation-section">
          <div class="repo-relation-row">
            <span class="relation-label">🏷️ Groups:</span>
            ${relations.groups.length === 0 ? '<span class="text-muted small">None</span>' : ''}
            ${relations.groups.map(() => '<span class="tag-group-item"></span>').join('')}
          </div>
          <div class="repo-relation-row">
            <span class="relation-label">🔗 Depends On:</span>
            ${relations.dependencies.length === 0 ? '<span class="text-muted small">None</span>' : ''}
            ${relations.dependencies.map(d => `
              <span class="tag-dep-item">
                ${escapeHtml(d)}
                <button class="btn-tag-remove btn-remove-dependency" data-source="${escapeHtml(r.repo_id)}" data-target="${escapeHtml(d)}">✕</button>
              </span>
            `).join('')}
            <button class="btn-add-inline btn-add-dependency" data-source="${escapeHtml(r.repo_id)}">+ Add</button>
          </div>
          ${relations.dependents.length > 0 ? `
            <div class="repo-relation-row">
              <span class="relation-label">👥 Dependents:</span>
              ${relations.dependents.map(dp => `<span class="tag-dep-item">${escapeHtml(dp)}</span>`).join('')}
            </div>
          ` : ''}
        </div>

        <div class="repo-card-actions">
          <button class="btn btn-secondary btn-sm btn-edit-repo" data-id="${escapeHtml(r.repo_id)}">⚙️ Edit / Branch</button>
          <button class="btn btn-secondary btn-sm btn-sync-repo" data-id="${escapeHtml(r.repo_id)}">🔄 Sync</button>
          ${r.source_type === 'git' ? `<button class="btn btn-secondary btn-sm btn-index-local" data-id="${escapeHtml(r.repo_id)}" title="Index the existing checkout only. No remote update or branch switch.">Index existing checkout</button>` : ''}
          <button class="btn btn-ghost btn-sm text-muted btn-del-repo" data-id="${escapeHtml(r.repo_id)}">🗑️</button>
        </div>
      </div>
    `;
  }).join('');

  elements.reposGrid.querySelectorAll('.repo-card').forEach((card, index) => {
    const repo = state.repos[index];
    card.dataset.repoId = repo.repo_id;
    card.querySelector('.repo-card-title').textContent = repo.name || repo.repo_id;
    const path = card.querySelector('.repo-card-path');
    path.textContent = repo.url_or_path;
    path.title = repo.url_or_path;
    if (repo.branch) card.querySelector('.repo-branch-tag code').textContent = repo.branch;
    card.querySelector('.badge-openspec').textContent = repo.source_type;
    card.querySelector('.repo-status-text').textContent = `Index status: ${repo.status}`;
    card.querySelector('.repo-error-text').textContent = repo.error_message || '';
    card.querySelector('.repo-ssh-label').textContent = repo.source_type === 'git'
      ? `SSH authentication: ${repo.ssh_key_id ? sshKeys.profiles.find(profile => profile.key_id === repo.ssh_key_id)?.name || `Unavailable profile (${repo.ssh_key_id})` : 'System SSH'}`
      : 'Local source (no SSH authentication)';
    card.querySelector('.repo-sync-time').textContent = repo.last_synced_at
      ? `${repo.source_type === 'git' ? 'Last remote sync' : 'Last indexed'}: ${new Date(repo.last_synced_at * 1000).toLocaleString()}`
      : repo.source_type === 'git' ? 'Last remote sync: never' : 'Last indexed: never';
    const groups = state.repoRelations[repo.repo_id]?.groups || [];
    card.querySelectorAll('.tag-group-item').forEach((tag, groupIndex) => {
      tag.textContent = groups[groupIndex];
    });
  });

  elements.reposGrid.querySelectorAll('.btn-remove-dependency').forEach(button => {
    button.addEventListener('click', () => window.removeDependencyEdge(button.dataset.source, button.dataset.target));
  });
  elements.reposGrid.querySelectorAll('.btn-add-dependency').forEach(button => {
    button.addEventListener('click', () => window.openAddDependencyModal(button.dataset.source));
  });

  // Attach edit/sync/delete listeners
  elements.reposGrid.querySelectorAll('.btn-edit-repo').forEach(btn => {
    btn.addEventListener('click', () => {
      if (repositoryOperations.busy || transfer.importing) return;
      const repoId = btn.dataset.id;
      const repo = state.repos.find(x => x.repo_id === repoId);
      if (repo) {
        document.getElementById('edit-repo-id').value = repo.repo_id;
        document.getElementById('edit-repo-id-display').value = repo.repo_id;
        document.getElementById('edit-repo-name').value = repo.name || repo.repo_id;
        document.getElementById('edit-repo-branch').value = repo.branch || 'main';
        document.getElementById('edit-repo-path').value = repo.url_or_path || '';
        document.getElementById('edit-repo-status').textContent = '';
        document.getElementById('edit-repo-ssh-key').dataset.assignment = repo.ssh_key_id || '';
        document.getElementById('modal-edit-repo').classList.remove('hidden');
        refreshSSHSelectors();
        loadSSHKeys();
      }
    });
  });

  elements.reposGrid.querySelectorAll('.btn-sync-repo, .btn-index-local').forEach(btn => {
    btn.addEventListener('click', () => {
      const repo = state.repos.find(entry => entry.repo_id === btn.dataset.id);
      if (repo) return syncRepositories([repo], btn.classList.contains('btn-index-local'));
    });
  });

  elements.reposGrid.querySelectorAll('.btn-del-repo').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (repositoryOperations.busy || transfer.importing) return;
      const repoId = btn.dataset.id;
      if (confirm(`Are you sure you want to remove '${repoId}' and delete its index?`)) {
        if (!beginRepositoryOperation()) return;
        try {
          await checkedJSON(`${API_BASE}/api/v1/repos/${encodeURIComponent(repoId)}`, { method: 'DELETE' });
          await loadRepositories();
          await loadCrossRepoLinks();
        } catch (err) {
          alert(`Delete failed: ${err.message}`);
        } finally {
          endRepositoryOperation();
        }
      }
    });
  });
  lockRepositoryControls();
}

function updateStats() {
  elements.statRepos.textContent = state.repos.length;
  elements.statFiles.textContent = state.repos.reduce((sum, r) => sum + (r.total_files || 0), 0);
  elements.statChunks.textContent = state.repos.reduce((sum, r) => sum + (r.total_chunks || 0), 0);
  elements.statSymbols.textContent = state.repos.reduce((sum, r) => sum + (r.total_symbols || 0), 0);
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// -------------------------------------------------------------
// Code Drawer Inspector
// -------------------------------------------------------------
async function openFileInDrawer(repoId, filePath, startLine = 1, endLine = 1) {
  try {
    elements.drawerRepoBadge.textContent = repoId;
    elements.drawerFilePath.textContent = `${filePath}:${startLine}-${endLine}`;
    elements.drawerCodeContent.textContent = 'Loading file content...';
    elements.codeDrawer.classList.remove('hidden');

    const res = await fetch(`${API_BASE}/api/v1/file?repo_id=${encodeURIComponent(repoId)}&path=${encodeURIComponent(filePath)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to fetch file');

    elements.drawerCodeContent.textContent = data.content;
  } catch (err) {
    elements.drawerCodeContent.textContent = `Error: ${err.message}`;
  }
}

// -------------------------------------------------------------
// Cross-Repo Graph
// -------------------------------------------------------------
async function loadCrossRepoLinks({ strict = false } = {}) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/graph/cross-repo`);
    const data = await res.json();
    if (!res.ok) throw new Error('Failed to load cross-repository graph');
    const links = data.cross_repo_links || [];

    if (links.length === 0) {
      elements.crossRepoApiTbody.innerHTML = `
        <tr><td colspan="5" class="text-center text-muted py-4">No cross-repository API contracts discovered yet. Add frontend and backend repositories to trace contracts.</td></tr>
      `;
      return;
    }

    elements.crossRepoApiTbody.innerHTML = links.map(l => `
      <tr>
        <td><strong>${l.client_repo}</strong></td>
        <td><a href="javascript:void(0)" onclick="openFileInDrawer('${l.client_repo}', '${l.client_file}', ${l.client_line}, ${l.client_line})" class="btn-link">${l.client_file}:${l.client_line}</a></td>
        <td><code>${l.endpoint_path}</code></td>
        <td><strong>${l.server_repo}</strong></td>
        <td><a href="javascript:void(0)" onclick="openFileInDrawer('${l.server_repo}', '${l.server_file}', ${l.server_line}, ${l.server_line})" class="btn-link">${l.server_file}:${l.server_line}</a></td>
      </tr>
    `).join('');
  } catch (err) {
    if (strict) throw err;
    console.error(err);
  }
}

async function handleSymbolSearch() {
  const term = elements.symbolSearchInput.value.trim();
  if (!term) return;

  try {
    elements.symbolDetailView.innerHTML = '<div class="text-muted text-center py-4">Searching symbol graph...</div>';
    const res = await fetch(`${API_BASE}/api/v1/symbols/${encodeURIComponent(term)}`);
    const data = await res.json();

    const defs = data.definitions || [];
    const callers = data.callers || [];
    const callees = data.callees || [];

    elements.symbolDetailView.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:12px;">
        <div>
          <h4>Definitions (${defs.length})</h4>
          ${defs.length === 0 ? '<div class="text-muted small">No definition found for this identifier.</div>' : ''}
          ${defs.map(d => `
            <div style="background:var(--bg-surface-raised); padding:8px 12px; border-radius:6px; margin-top:6px;">
              <div style="display:flex; justify-content:space-between;">
                <strong>${d.name}</strong> <span class="badge-openspec">${d.kind}</span>
              </div>
              <div class="small text-muted">${d.repo_id} :: <a href="javascript:void(0)" onclick="openFileInDrawer('${d.repo_id}', '${d.file_path}', ${d.line_number}, ${d.line_number})" class="btn-link">${d.file_path}:${d.line_number}</a></div>
              ${d.signature ? `<pre class="small" style="margin-top:4px;"><code>${escapeHtml(d.signature)}</code></pre>` : ''}
            </div>
          `).join('')}
        </div>

        <div>
          <h4>Callers / Referrers (${callers.length})</h4>
          ${callers.length === 0 ? '<div class="text-muted small">No callers recorded in call graph.</div>' : ''}
          ${callers.map(c => `
            <div style="font-size:13px; margin-top:4px;">
              ↳ <strong>${c.source_repo}</strong>: <a href="javascript:void(0)" onclick="openFileInDrawer('${c.source_repo}', '${c.source_file}', ${c.line_number}, ${c.line_number})" class="btn-link">${c.source_file}:${c.line_number}</a> (${c.source_symbol})
            </div>
          `).join('')}
        </div>
      </div>
    `;
  } catch (err) {
    elements.symbolDetailView.innerHTML = `<div class="text-error">Error: ${err.message}</div>`;
  }
}

// -------------------------------------------------------------
// Hybrid Search
// -------------------------------------------------------------
async function handleHybridSearch() {
  const query = elements.hybridSearchInput.value.trim();
  if (!query) return;

  try {
    elements.hybridResultsList.innerHTML = '<div class="text-muted text-center py-4">Running hybrid vector + BM25 search...</div>';
    const scopePayload = buildScopeRequestPayload();

    const res = await fetch(`${API_BASE}/api/v1/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: query,
        ...scopePayload,
        top_k: 12
      })
    });
    const data = await res.json();
    const results = data.results || [];

    if (results.length === 0) {
      elements.hybridResultsList.innerHTML = '<div class="text-muted text-center py-4">No matching code chunks found.</div>';
      return;
    }

    elements.hybridResultsList.innerHTML = results.map((r, i) => {
      const c = r.chunk;
      const isExpanded = r.repo_relation === 'expanded';
      return `
        <div class="search-result-item ${isExpanded ? 'citation-expanded' : ''}">
          <div class="search-result-header">
            <div>
              <strong>#${i + 1}</strong> [${c.repo_id}] 
              <a href="javascript:void(0)" onclick="openFileInDrawer('${c.repo_id}', '${c.file_path}', ${c.start_line}, ${c.end_line})" class="btn-link">${c.file_path}:L${c.start_line}-L${c.end_line}</a>
              ${c.symbol_name ? `<span style="color:var(--accent-purple); margin-left:6px;">${c.symbol_name}</span>` : ''}
              ${isExpanded ? `<span class="badge-expanded">⚡ Expanded (${r.relation_direction || 'rel'}, ${r.relation_hops || 1}h)</span>` : ''}
            </div>
            <div class="search-result-score">RRF Score: ${r.score}</div>
          </div>
          <pre style="background:var(--bg-app); border:1px solid var(--border-subtle); padding:10px; border-radius:6px; font-family:var(--font-mono); font-size:12px; max-height:160px; overflow-y:auto;"><code>${escapeHtml(c.raw_content)}</code></pre>
        </div>
      `;
    }).join('');
  } catch (err) {
    elements.hybridResultsList.innerHTML = `<div class="text-error">Error: ${err.message}</div>`;
  }
}

// Start app
document.addEventListener('DOMContentLoaded', init);
