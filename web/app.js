/**
 * Multi-Repository Code Search & Architecture Studio - Frontend Logic
 */

const API_BASE = window.location.origin;

// State
const state = {
  repos: [],
  groups: [],
  repoRelations: {}, // repo_id -> { groups: [], dependencies: [], dependents: [] }
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

// Initialize Application
async function init() {
  document.documentElement.setAttribute('data-theme', state.theme);
  setupEventListeners();
  await loadGroups();
  await loadRepositories();
  await loadCrossRepoLinks();
  pruneScopeAgainstLiveData();
  persistScope();
  renderRepositories();
  renderGroupFilterList();
  renderScopeSummary();
}

function setupEventListeners() {
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
  const openModal = () => elements.modalAddRepo.classList.remove('hidden');
  const closeModal = () => elements.modalAddRepo.classList.add('hidden');
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

  const closeEditModal = () => modalEditRepo.classList.add('hidden');
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

      const btnSubmit = document.getElementById('btn-submit-edit-repo');
      btnSubmit.disabled = true;
      btnSubmit.textContent = payload.auto_sync ? 'Switching & Syncing...' : 'Saving...';

      try {
        const res = await fetch(`${API_BASE}/api/v1/repos/${repoId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to update repository');

        closeEditModal();
        await loadRepositories();
        await loadCrossRepoLinks();
      } catch (err) {
        alert(`Error updating repository: ${err.message}`);
      } finally {
        btnSubmit.disabled = false;
        btnSubmit.textContent = 'Save & Sync';
      }
    });
  }

  // Add Repo Submit
  elements.addRepoForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(elements.addRepoForm);
    const payload = {
      repo_id: document.getElementById('input-repo-id').value.trim(),
      name: document.getElementById('input-repo-name').value.trim(),
      source_type: formData.get('source_type'),
      url_or_path: document.getElementById('input-repo-path').value.trim(),
      branch: document.getElementById('input-repo-branch').value.trim() || 'main',
      auto_sync: true
    };

    try {
      const btn = document.getElementById('btn-submit-repo');
      btn.disabled = true;
      btn.textContent = 'Indexing...';

      const res = await fetch(`${API_BASE}/api/v1/repos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to add repository');

      closeModal();
      elements.addRepoForm.reset();
      await loadRepositories();
      await loadCrossRepoLinks();
    } catch (err) {
      alert(`Error adding repository: ${err.message}`);
    } finally {
      const btn = document.getElementById('btn-submit-repo');
      btn.disabled = false;
      btn.textContent = 'Index & Register';
    }
  });

  // Sync All
  elements.btnSyncAll.addEventListener('click', async () => {
    elements.btnSyncAll.disabled = true;
    elements.btnSyncAll.textContent = 'Syncing...';
    for (const repo of state.repos) {
      try {
        await fetch(`${API_BASE}/api/v1/repos/${repo.repo_id}/sync`, { method: 'POST' });
      } catch (err) {
        console.error(err);
      }
    }
    await loadRepositories();
    await loadCrossRepoLinks();
    elements.btnSyncAll.disabled = false;
    elements.btnSyncAll.textContent = '🔄 Sync All';
  });

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
}

// -------------------------------------------------------------
// Load & Render Groups
// -------------------------------------------------------------
async function loadGroups() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/groups`);
    const data = await res.json();
    state.groups = data.groups || [];
    renderGroupFilterList();
  } catch (err) {
    console.error('Failed to load groups:', err);
  }
}

function renderGroupFilterList() {
  if (!elements.groupFilterList) return;
  if (state.groups.length === 0) {
    elements.groupFilterList.innerHTML = '<div class="text-muted small">No groups created yet.</div>';
    return;
  }
  elements.groupFilterList.innerHTML = state.groups.map(g => `
    <label class="repo-filter-item">
      <input type="checkbox" value="${g.name}" ${state.scope.groupNames.has(g.name) ? 'checked' : ''}>
      <span>🏷️ ${g.name} (${g.members.length} repos)</span>
    </label>
  `).join('');

  elements.groupFilterList.querySelectorAll('input').forEach(cb => {
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
        <strong style="font-size:14px;">🏷️ ${g.name}</strong>
        <button class="btn btn-ghost btn-sm text-muted" onclick="deleteGroup('${g.name}')" title="Delete Group">🗑️ Delete</button>
      </div>
      <div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center;">
        <span class="text-muted small">Members:</span>
        ${g.members.length === 0 ? '<span class="text-muted small">None</span>' : ''}
        ${g.members.map(m => `
          <span class="tag-group-item">
            ${m}
            <button class="btn-tag-remove" onclick="removeMemberFromGroup('${g.name}', '${m}')">✕</button>
          </span>
        `).join('')}
        <select class="select-sm" onchange="if(this.value){ addMemberToGroup('${g.name}', this.value); this.value=''; }">
          <option value="">+ Add Repo...</option>
          ${state.repos.filter(r => !g.members.includes(r.repo_id)).map(r => `<option value="${r.repo_id}">${r.name || r.repo_id}</option>`).join('')}
        </select>
      </div>
    </div>
  `).join('');
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
  elements.depTargetRepo.innerHTML = state.repos
    .filter(r => r.repo_id !== sourceRepoId)
    .map(r => `<option value="${r.repo_id}">${r.name || r.repo_id}</option>`)
    .join('');
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
async function loadRepositories() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/repos`);
    const data = await res.json();
    state.repos = data.repositories || [];

    // Fetch relations for each repository in parallel
    const relationPromises = state.repos.map(async (r) => {
      try {
        const relRes = await fetch(`${API_BASE}/api/v1/repos/${encodeURIComponent(r.repo_id)}/relations`);
        if (relRes.ok) {
          const relData = await relRes.json();
          state.repoRelations[r.repo_id] = relData;
        }
      } catch (err) {
        console.error(`Failed to load relations for ${r.repo_id}:`, err);
      }
    });
    await Promise.all(relationPromises);

    renderRepositories();
    updateStats();
  } catch (err) {
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
        <input type="checkbox" value="${r.repo_id}" ${state.scope.repoIds.size === 0 || state.scope.repoIds.has(r.repo_id) ? 'checked' : ''}>
        <span>${r.name || r.repo_id}</span>
      </label>
    `).join('');

    elements.repoFilterList.querySelectorAll('input').forEach(cb => {
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
        <button onclick="document.getElementById('modal-add-repo').classList.remove('hidden')" class="btn btn-primary">+ Connect Repository</button>
      </div>
    `;
    return;
  }

  elements.reposGrid.innerHTML = state.repos.map(r => {
    const relations = state.repoRelations[r.repo_id] || { groups: [], dependencies: [], dependents: [] };
    return `
      <div class="repo-card">
        <div class="repo-card-header">
          <div>
            <div class="repo-card-title">${r.name || r.repo_id}</div>
            <div class="repo-card-path" title="${r.url_or_path}">${r.url_or_path}</div>
            ${r.branch ? `<div class="repo-branch-tag">🌿 <code>${r.branch}</code></div>` : ''}
          </div>
          <span class="badge-openspec">${r.source_type}</span>
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
            ${relations.groups.map(g => `<span class="tag-group-item">${g}</span>`).join('')}
          </div>
          <div class="repo-relation-row">
            <span class="relation-label">🔗 Depends On:</span>
            ${relations.dependencies.length === 0 ? '<span class="text-muted small">None</span>' : ''}
            ${relations.dependencies.map(d => `
              <span class="tag-dep-item">
                ${d}
                <button class="btn-tag-remove" onclick="removeDependencyEdge('${r.repo_id}', '${d}')">✕</button>
              </span>
            `).join('')}
            <button class="btn-add-inline" onclick="openAddDependencyModal('${r.repo_id}')">+ Add</button>
          </div>
          ${relations.dependents.length > 0 ? `
            <div class="repo-relation-row">
              <span class="relation-label">👥 Dependents:</span>
              ${relations.dependents.map(dp => `<span class="tag-dep-item">${dp}</span>`).join('')}
            </div>
          ` : ''}
        </div>

        <div class="repo-card-actions">
          <button class="btn btn-secondary btn-sm btn-edit-repo" data-id="${r.repo_id}">⚙️ Edit / Branch</button>
          <button class="btn btn-secondary btn-sm btn-sync-repo" data-id="${r.repo_id}">🔄 Sync</button>
          <button class="btn btn-ghost btn-sm text-muted btn-del-repo" data-id="${r.repo_id}">🗑️</button>
        </div>
      </div>
    `;
  }).join('');

  // Attach edit/sync/delete listeners
  elements.reposGrid.querySelectorAll('.btn-edit-repo').forEach(btn => {
    btn.addEventListener('click', () => {
      const repoId = btn.dataset.id;
      const repo = state.repos.find(x => x.repo_id === repoId);
      if (repo) {
        document.getElementById('edit-repo-id').value = repo.repo_id;
        document.getElementById('edit-repo-id-display').value = repo.repo_id;
        document.getElementById('edit-repo-name').value = repo.name || repo.repo_id;
        document.getElementById('edit-repo-branch').value = repo.branch || 'main';
        document.getElementById('edit-repo-path').value = repo.url_or_path || '';
        document.getElementById('modal-edit-repo').classList.remove('hidden');
      }
    });
  });

  elements.reposGrid.querySelectorAll('.btn-sync-repo').forEach(btn => {
    btn.addEventListener('click', async () => {
      const repoId = btn.dataset.id;
      btn.disabled = true;
      btn.textContent = 'Syncing...';
      try {
        await fetch(`${API_BASE}/api/v1/repos/${repoId}/sync`, { method: 'POST' });
        await loadRepositories();
        await loadCrossRepoLinks();
      } catch (err) {
        alert(`Sync failed: ${err.message}`);
      } finally {
        btn.disabled = false;
        btn.textContent = '🔄 Sync';
      }
    });
  });

  elements.reposGrid.querySelectorAll('.btn-del-repo').forEach(btn => {
    btn.addEventListener('click', async () => {
      const repoId = btn.dataset.id;
      if (confirm(`Are you sure you want to remove '${repoId}' and delete its index?`)) {
        try {
          await fetch(`${API_BASE}/api/v1/repos/${repoId}`, { method: 'DELETE' });
          await loadRepositories();
          await loadCrossRepoLinks();
        } catch (err) {
          alert(`Delete failed: ${err.message}`);
        }
      }
    });
  });
}

function updateStats() {
  elements.statRepos.textContent = state.repos.length;
  elements.statFiles.textContent = state.repos.reduce((sum, r) => sum + (r.total_files || 0), 0);
  elements.statChunks.textContent = state.repos.reduce((sum, r) => sum + (r.total_chunks || 0), 0);
  elements.statSymbols.textContent = state.repos.reduce((sum, r) => sum + (r.total_symbols || 0), 0);
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
async function loadCrossRepoLinks() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/graph/cross-repo`);
    const data = await res.json();
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
