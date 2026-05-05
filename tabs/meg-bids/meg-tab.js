// MEG BIDS Unified Module
// Handles configuration, editor with virtual scrolling, and advanced modal editing
(function() {
  'use strict';

  // Utility functions
  const Utils = {
    escapeHtml: function(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    debounce: function(func, wait) {
      let timeout;
      return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
      };
    },

    getToken: function() {
      const params = new URLSearchParams(window.location.search);
      return params.get('token') || (window.authToken || '');
    },

    apiPath: function(path) {
      const token = Utils.getToken();
      return path + (token ? (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token) : '');
    }
  };

  // Help text for BIDS fields
  const HelpText = {
    subject: "BIDS subject ID (e.g., '01'). No 'sub-' prefix needed.",
    session: "Session identifier (e.g., '01'). No 'ses-' prefix needed.",
    task: "Task name (e.g., 'rest', 'motor'). Will be lowercased.",
    acquisition: "Acquisition parameters label (e.g., 'highres')",
    run: "Run number. Auto-formatted as 2 digits (1 → '01')",
    processing: "Processing label (e.g., 'preproc', 'cleaned')",
    split: "For split .fif files. Auto-formatted as 2 digits.",
    suffix: "File suffix: meg, channels, events, etc.",
    extension: "File extension: .fif, .json, .tsv, etc.",
    datatype: "Data type: meg, eeg, anat, etc.",
    space: "Coordinate space for anatomical/sensor files",
    recording: "Recording name",
    description: "Description for derivative data",
    tracking_system: "Tracking system entity",
    status: "Conversion status: run, check, processed, skip, missing"
  };

  // Main MEG BIDS module
  window.megBids = {
    currentStep: 1,
    projectRoot: '',
    config: {
      project_name: 'MEG Dataset',
      raw_dir: 'raw/natmeg',
      bids_dir: 'BIDS',
      tasks: [],
      conversion_file: 'logs/bids_conversion.tsv',
      config_file: 'meg_bids_config.json',
      overwrite: false
    },

    // Table state
    tableData: [],
    originalData: [],
    tableSearchIndex: [],
    tableFile: null,
    modifiedRows: new Set(),
    selectedRows: new Set(),

    // Filter state
    filters: {
      subjects: new Set(),
      tasks: new Set(),
      statuses: new Set(),
      sessions: new Set(),
      runs: new Set(),
      datatypes: new Set(),
      rawNames: new Set()
    },

    // Sort state
    sort: {
      column: null,
      direction: 'none' // 'asc', 'desc', 'none'
    },

    // Virtual scrolling state
    scroll: {
      rowHeight: 36,
      bufferRows: 5,
      visibleStart: 0,
      visibleEnd: 0,
      containerHeight: 0,
      lastStart: -1,
      lastEnd: -1,
      lastVisibleLength: -1,
      renderPending: false
    },

    // Modal state
    modal: {
      isOpen: false,
      currentRowIndex: null,
      visibleRowIndices: [],
      tempData: null
    },

    // Initialize module
    init: async function() {
      await this.loadProjectRoot();
      this.loadFromLocalStorage();
      this.Editor.init();
      this.switchStep(1);
    },

    // Load project root from server
    loadProjectRoot: async function() {
      try {
        const res = await fetch(Utils.apiPath('/meg-get-project-root'));
        const data = await res.json();
        this.projectRoot = data.project_root || '/data/projects/unknown';

        // Update UI
        const rootPrefix = this.projectRoot + '/';
        const rootDisplay = document.getElementById('megProjectRootDisplay');
        if (rootDisplay) rootDisplay.textContent = this.projectRoot;

        ['megRootPrefixRaw', 'megRootPrefixBids', 'megRootPrefixConv', 'megRootPrefixConfig', 'megRootPrefixTable'].forEach(id => {
          const el = document.getElementById(id);
          if (el) el.textContent = rootPrefix;
        });

        const tablePrefix = document.getElementById('megRootPrefixTable');
        if (tablePrefix) tablePrefix.textContent = rootPrefix;

        if (data.project_name) {
          this.config.project_name = data.project_name;
          const nameDisplay = document.getElementById('megProjectNameDisplay');
          if (nameDisplay) nameDisplay.textContent = data.project_name;
        }
      } catch (e) {
        console.error('Failed to load project root:', e);
      }
    },

    // Step navigation
    switchStep: function(step) {
      this.currentStep = step;

      document.querySelectorAll('#meg-bids-container .nav-item').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.step) === step);
      });

      document.querySelectorAll('.meg-step').forEach(el => {
        el.classList.toggle('active', el.id === 'meg-step-' + step);
      });
    },

    // Load config from localStorage
    loadFromLocalStorage: function() {
      const key = 'meg-bids-config-' + (this.projectRoot || 'default');
      const saved = localStorage.getItem(key);
      if (saved) {
        try {
          const savedConfig = JSON.parse(saved);
          this.config = { ...this.config, ...savedConfig };
          this.updateFormFromConfig();
          this.updateJsonDisplay();
          this.setAutoSaveStatus('saved');
        } catch (e) {
          console.error('Failed to load from localStorage:', e);
          // Fall through to default display
          this.setDefaultConfig();
        }
      } else {
        // No saved config - show defaults in JSON viewer
        this.setDefaultConfig();
      }
    },

    // Set default configuration
    setDefaultConfig: function() {
      // Config already has defaults, just ensure JSON display is updated
      this.updateFormFromConfig();
      this.renderTasks();
      this.updateJsonDisplay();
      this.validateAllPaths();
      this.setAutoSaveStatus('saved');
    },

    // Save config to localStorage
    saveToLocalStorage: function() {
      const key = 'meg-bids-config-' + (this.projectRoot || 'default');
      localStorage.setItem(key, JSON.stringify(this.config));
    },

    // Update form fields from config
    updateFormFromConfig: function() {
      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = val || '';
      };

      setVal('megCfgRawDir', this.config.raw_dir);
      setVal('megCfgBidsDir', this.config.bids_dir);
      setVal('megCfgConversionFile', this.config.conversion_file);
      setVal('megCfgConfigFile', this.config.config_file);
      setVal('megTablePath', this.config.conversion_file);
      this.renderTasks();

      const overwriteEl = document.getElementById('megCfgOverwrite');
      if (overwriteEl) overwriteEl.checked = this.config.overwrite || false;
    },

    // Set status message
    setStatus: function(elementId, message, type) {
      const el = document.getElementById(elementId);
      if (el) {
        el.textContent = message;
        el.className = type === 'warn' ? 'warn' : 'muted';
    }
    },

    // Validation timeouts
    validationTimeouts: {},
    autoSaveTimeout: null,
    jsonMode: 'view',

    // Sync form to JSON (live)
    syncFormToJson: function() {
      this.config.raw_dir = document.getElementById('megCfgRawDir')?.value || 'raw/natmeg';
      this.config.bids_dir = document.getElementById('megCfgBidsDir')?.value || 'BIDS';
      this.config.conversion_file = document.getElementById('megCfgConversionFile')?.value || 'logs/bids_conversion.tsv';
      this.config.config_file = document.getElementById('megCfgConfigFile')?.value || 'meg_bids_config.json';
      this.config.overwrite = document.getElementById('megCfgOverwrite')?.checked || false;
      this.config.tasks = this.config.tasks || [];

      this.updateJsonDisplay();
      this.debouncedValidatePaths();
      this.debouncedAutoSave();
      this.setAutoSaveStatus('unsaved');
    },

    // Sync JSON to form (on edit blur)
    syncJsonToForm: function() {
      try {
        const jsonText = document.getElementById('megJsonEdit')?.value || '';
        const parsed = JSON.parse(jsonText);

        // Update config (project_name is server-defined, preserve it)
        const savedProjectName = this.config.project_name;
        this.config = { ...this.config, ...parsed };
        this.config.project_name = savedProjectName;
        this.config.tasks = Array.isArray(this.config.tasks) ? this.config.tasks : [];

        // Update form fields
        document.getElementById('megCfgRawDir').value = parsed.raw_dir || '';
        document.getElementById('megCfgBidsDir').value = parsed.bids_dir || '';
        document.getElementById('megCfgConversionFile').value = parsed.conversion_file || '';
        document.getElementById('megCfgConfigFile').value = parsed.config_file || 'meg_bids_config.json';
        document.getElementById('megCfgOverwrite').checked = parsed.overwrite || false;
        const tablePath = document.getElementById('megTablePath');
        if (tablePath) tablePath.value = this.config.conversion_file || '';
        this.renderTasks();

        this.updateJsonDisplay();
        this.validateAllPaths();
        this.debouncedAutoSave();
        this.showJsonValidation('Valid JSON', 'success');
      } catch (e) {
        this.showJsonValidation('Invalid JSON: ' + e.message, 'error');
      }
    },

    // Update JSON display
    updateJsonDisplay: function() {
      const jsonText = JSON.stringify(this.config, null, 2);
      const viewEl = document.getElementById('megJsonView');
      const editEl = document.getElementById('megJsonEdit');
      if (viewEl) viewEl.textContent = jsonText;
      if (editEl) editEl.value = jsonText;
    },

    // JSON mode toggle
    setJsonMode: function(mode) {
      this.jsonMode = mode;

      const viewBtn = document.getElementById('megJsonViewBtn');
      const editBtn = document.getElementById('megJsonEditBtn');
      const viewEl = document.getElementById('megJsonView');
      const editEl = document.getElementById('megJsonEdit');

      if (viewBtn) viewBtn.classList.toggle('active', mode === 'view');
      if (editBtn) editBtn.classList.toggle('active', mode === 'edit');
      if (viewEl) viewEl.style.display = mode === 'view' ? 'block' : 'none';
      if (editEl) editEl.style.display = mode === 'edit' ? 'block' : 'none';

      if (mode === 'edit' && editEl) {
        editEl.focus();
      }
    },

    // Tasks management
    handleTasksKeydown: function(event) {
      const input = document.getElementById('megCfgTasksInput');
      if (!input) return;

      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        const value = input.value.trim();
        if (value && !(this.config.tasks || []).includes(value)) {
          this.config.tasks = this.config.tasks || [];
          this.config.tasks.push(value);
          input.value = '';
          this.renderTasks();
          this.syncFormToJson();
        }
      } else if (event.key === 'Backspace' && !input.value && (this.config.tasks || []).length > 0) {
        this.config.tasks.pop();
        this.renderTasks();
        this.syncFormToJson();
      }
    },

    removeTask: function(index) {
      this.config.tasks = this.config.tasks || [];
      this.config.tasks.splice(index, 1);
      this.renderTasks();
      this.syncFormToJson();
    },

    renderTasks: function() {
      const container = document.getElementById('megTasksList');
      if (!container) return;

      this.config.tasks = this.config.tasks || [];
      container.innerHTML = this.config.tasks.map((task, idx) => `
        <span class="tag">
          ${Utils.escapeHtml(task)}
          <span class="tag-remove" onclick="megBids.removeTask(${idx})" style="cursor:pointer; margin-left:4px;">&times;</span>
        </span>
      `).join('');
    },

    // Path validation
    debouncedValidatePaths: function() {
      clearTimeout(this.validationTimeouts.all);
      this.validationTimeouts.all = setTimeout(() => this.validateAllPaths(), 300);
    },

    validateAllPaths: async function() {
      const paths = [
        { id: 'raw', path: this.config.raw_dir },
        { id: 'bids', path: this.config.bids_dir },
        { id: 'conv', path: this.config.conversion_file }
      ];

      // Mark all as checking
      paths.forEach(p => {
        const el = document.getElementById('val-' + p.id);
        if (el) el.className = 'path-validation checking';
      });

      try {
        const res = await fetch(Utils.apiPath('/meg-validate-paths'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paths: paths })
        });
        const data = await res.json();

        Object.entries(data.results || {}).forEach(([id, result]) => {
          const el = document.getElementById('val-' + id);
          if (el) {
            el.className = 'path-validation ' + (result.exists ? 'valid' : 'invalid');
            el.title = result.resolved || '';
          }
        });
      } catch (e) {
        console.error('Validation failed:', e);
      }
    },

    // Auto-save
    debouncedAutoSave: function() {
      clearTimeout(this.autoSaveTimeout);
      this.autoSaveTimeout = setTimeout(() => this.saveToLocalStorage(), 2000);
    },

    setAutoSaveStatus: function(status) {
      const el = document.getElementById('megAutoSaveStatus');
      if (!el) return;
      if (status === 'saved') {
        el.textContent = 'Auto-saved ✓';
        el.classList.remove('unsaved');
      } else {
        el.textContent = 'Unsaved changes';
        el.classList.add('unsaved');
      }
    },

    showJsonValidation: function(message, type) {
      const el = document.getElementById('megJsonValidation');
      if (!el) return;
      el.textContent = message;
      el.className = 'validation-status ' + type;
      setTimeout(() => {
        el.textContent = '';
      }, 3000);
    },

    // Actions
    resetDefaults: function() {
      if (!confirm('Reset all configuration to defaults?')) return;

      const savedProjectName = this.config.project_name;
      this.config = {
        project_name: savedProjectName,
        raw_dir: 'raw/natmeg',
        bids_dir: 'BIDS',
        tasks: [],
        conversion_file: 'logs/bids_conversion.tsv',
        config_file: 'meg_bids_config.json',
        overwrite: false
      };

      document.getElementById('megCfgRawDir').value = 'raw/natmeg';
      document.getElementById('megCfgBidsDir').value = 'BIDS';
      document.getElementById('megCfgConversionFile').value = 'logs/bids_conversion.tsv';
      document.getElementById('megCfgConfigFile').value = 'meg_bids_config.json';
      document.getElementById('megCfgOverwrite').checked = false;

      this.renderTasks();
      this.updateJsonDisplay();
      this.validateAllPaths();
      this.saveToLocalStorage();
    },

    loadConfigFromFile: async function() {
      const path = prompt('Enter config file path:', 'meg_bids_config.json');
      if (!path) return;

      try {
        const res = await fetch(Utils.apiPath('/meg-load-config?path=' + encodeURIComponent(path)));
        const data = await res.json();

        if (data.error) {
          alert('Error: ' + data.error);
          return;
        }

        // Convert from server config format to minimal format
        const serverConfig = data.config;
        const savedProjectName = this.config.project_name;
        this.config = {
          project_name: savedProjectName,
          raw_dir: serverConfig.Raw || 'raw/natmeg',
          bids_dir: serverConfig.BIDS || 'BIDS',
          tasks: serverConfig.Tasks || [],
          conversion_file: serverConfig.Conversion_file || 'logs/bids_conversion.tsv',
          config_file: serverConfig.config_file || 'meg_bids_config.json',
          overwrite: serverConfig.overwrite || false
        };

        // Update form
        document.getElementById('megCfgRawDir').value = this.config.raw_dir;
        document.getElementById('megCfgBidsDir').value = this.config.bids_dir;
        document.getElementById('megCfgConversionFile').value = this.config.conversion_file;
        document.getElementById('megCfgConfigFile').value = this.config.config_file;
        document.getElementById('megCfgOverwrite').checked = this.config.overwrite;

        this.renderTasks();
        this.updateJsonDisplay();
        this.validateAllPaths();
        this.saveToLocalStorage();
        alert('Config loaded from: ' + path);
      } catch (e) {
        alert('Failed to load config: ' + e.message);
      }
    },

    saveConfigToFile: async function() {
      // Convert to server format
      const serverConfig = {
        project_name: this.config.project_name,
        raw_dir: this.config.raw_dir,
        bids_dir: this.config.bids_dir,
        tasks: this.config.tasks,
        conversion_file: this.config.conversion_file,
        config_file: this.config.config_file,
        overwrite: this.config.overwrite
      };

      const configFileName = this.config.config_file || 'meg_bids_config.json';

      try {
        const res = await fetch(Utils.apiPath('/meg-save-config'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            config: serverConfig,
            config_file: configFileName
          })
        });

        const data = await res.json();

        if (data.error) {
          alert('Error saving config: ' + data.error);
        } else {
          this.setAutoSaveStatus('saved');
          alert('Config saved to: ' + data.full_path);
        }
      } catch (e) {
        alert('Failed to save config: ' + e.message);
      }
    },

    // Editor module
    Editor: {
      init: function() {
        this.setupEventListeners();
        this.setupVirtualScroll();
      },

      setupEventListeners: function() {
        // Search
        const searchEl = document.getElementById('megSearchInput');
        if (searchEl) {
          searchEl.addEventListener('input', Utils.debounce(() => this.applyFilters(), 200));
        }

        this.setupHeaderFilterPickers();

        // Clear filters
        const clearBtn = document.getElementById('megClearFiltersBtn');
        if (clearBtn) {
          clearBtn.addEventListener('click', () => this.clearFilters());
        }

        // Batch apply
        const batchBtn = document.getElementById('megBatchApplyBtn');
        if (batchBtn) {
          batchBtn.addEventListener('click', () => this.batchUpdateStatus());
        }

        const batchTaskBtn = document.getElementById('megBatchApplyTaskBtn');
        if (batchTaskBtn) {
          batchTaskBtn.addEventListener('click', () => this.batchUpdateTask());
        }

        // Analyze button
        const analyzeBtn = document.getElementById('megAnalyzeBtn');
        if (analyzeBtn) {
          analyzeBtn.addEventListener('click', () => this.analyze());
        }

        // Save button
        const saveBtn = document.getElementById('megSaveTableBtn');
        if (saveBtn) {
          saveBtn.addEventListener('click', () => this.saveTable());
        }

        // Sync editor table path back to config/json.
        const tablePathInput = document.getElementById('megTablePath');
        if (tablePathInput) {
          tablePathInput.addEventListener('input', () => {
            megBids.config.conversion_file = tablePathInput.value || 'logs/bids_conversion.tsv';
            const cfgConv = document.getElementById('megCfgConversionFile');
            if (cfgConv) cfgConv.value = megBids.config.conversion_file;
            megBids.updateJsonDisplay();
            megBids.debouncedValidatePaths();
          });
        }

        // Select all visible rows
        const selectAll = document.getElementById('megSelectAll');
        if (selectAll) {
          selectAll.addEventListener('change', () => {
            const shouldSelect = selectAll.checked;
            megBids.modal.visibleRowIndices.forEach(dataIdx => {
              if (shouldSelect) {
                megBids.selectedRows.add(dataIdx);
              } else {
                megBids.selectedRows.delete(dataIdx);
              }
            });
            this.updateBatchActions();
            this.renderVisibleRows();
          });
        }

        // Sortable headers
        document.querySelectorAll('#megConversionTable th[data-column]').forEach(th => {
          th.addEventListener('click', () => this.handleSortClick(th.dataset.column));
        });

        // Delegate row and checkbox events so we don't rebind on every scroll render
        const tbody = document.getElementById('megTableBody');
        if (tbody) {
          tbody.addEventListener('click', (e) => {
            if (e.target.type === 'checkbox') return;
            const tr = e.target.closest('tr[data-row-index]');
            if (!tr) return;
            const dataIdx = parseInt(tr.dataset.rowIndex, 10);
            this.openModal(dataIdx);
          });

          tbody.addEventListener('change', (e) => {
            if (!e.target.matches('input[data-select-row]')) return;
            const dataIdx = parseInt(e.target.dataset.rowIndex, 10);
            if (e.target.checked) {
              megBids.selectedRows.add(dataIdx);
            } else {
              megBids.selectedRows.delete(dataIdx);
            }
            this.updateBatchActions();
            this.updateSelectAllState();
          });
        }

        // Modal buttons
        const modalClose = document.getElementById('megModalClose');
        if (modalClose) {
          modalClose.addEventListener('click', () => this.closeModal());
        }

        const modalCancel = document.getElementById('megModalCancel');
        if (modalCancel) {
          modalCancel.addEventListener('click', () => this.closeModal());
        }

        const modalSave = document.getElementById('megModalSave');
        if (modalSave) {
          modalSave.addEventListener('click', () => this.saveModal());
        }

        const modalPrev = document.getElementById('megModalPrev');
        if (modalPrev) {
          modalPrev.addEventListener('click', () => this.navigateModal(-1));
        }

        const modalNext = document.getElementById('megModalNext');
        if (modalNext) {
          modalNext.addEventListener('click', () => this.navigateModal(1));
        }

        // Advanced section toggle
        const advancedToggle = document.getElementById('megAdvancedToggle');
        if (advancedToggle) {
          advancedToggle.addEventListener('click', () => this.toggleAdvancedSection());
        }

        // Modal input change handlers for live preview
        const modalInputs = document.querySelectorAll('#megEditModal input[data-bids-field], #megEditModal select[data-bids-field]');
        modalInputs.forEach(input => {
          input.addEventListener('input', () => this.updateLivePreview());
          input.addEventListener('change', () => this.updateLivePreview());
        });

        this.setupHelpTooltips();
      },

      setupHelpTooltips: function() {
        const tooltip = document.getElementById('megHelpTooltip');
        if (!tooltip) return;

        const showTooltip = (target) => {
          const text = target.getAttribute('title');
          if (!text) return;
          target.dataset.originalTitle = text;
          target.removeAttribute('title');
          tooltip.textContent = text;
          tooltip.style.display = 'block';

          const rect = target.getBoundingClientRect();
          const top = rect.top - tooltip.offsetHeight - 8;
          tooltip.style.top = `${Math.max(8, top)}px`;
          tooltip.style.left = `${Math.min(window.innerWidth - tooltip.offsetWidth - 8, Math.max(8, rect.left - 12))}px`;
        };

        const hideTooltip = (target) => {
          if (!tooltip) return;
          tooltip.style.display = 'none';
          if (target?.dataset?.originalTitle) {
            target.setAttribute('title', target.dataset.originalTitle);
            delete target.dataset.originalTitle;
          }
        };

        document.querySelectorAll('.help-icon').forEach(icon => {
          icon.addEventListener('mouseenter', () => showTooltip(icon));
          icon.addEventListener('mouseleave', () => hideTooltip(icon));
          icon.addEventListener('focus', () => showTooltip(icon));
          icon.addEventListener('blur', () => hideTooltip(icon));
        });
      },

      setupHeaderFilterPickers: function() {
        const filterTypes = ['subjects', 'tasks', 'statuses', 'sessions', 'runs', 'datatypes', 'rawNames'];

        filterTypes.forEach(type => {
          const btn = document.getElementById(`megFilterBtn-${type}`);
          const menu = document.getElementById(`megFilterMenu-${type}`);
          const search = document.getElementById(`megFilterSearch-${type}`);

          if (btn && menu) {
            btn.addEventListener('click', (e) => {
              e.preventDefault();
              e.stopPropagation();
              this.toggleHeaderFilterMenu(type);
            });
          }

          if (search) {
            search.addEventListener('click', (e) => e.stopPropagation());
            search.addEventListener('input', () => {
              this.renderHeaderFilterOptions(type);
            });
          }

          if (menu) {
            menu.addEventListener('click', (e) => e.stopPropagation());
          }
        });

        document.addEventListener('click', () => this.closeAllHeaderFilterMenus());
      },

      toggleHeaderFilterMenu: function(type) {
        const targetMenu = document.getElementById(`megFilterMenu-${type}`);
        if (!targetMenu) return;

        const isOpen = targetMenu.style.display !== 'none';
        this.closeAllHeaderFilterMenus();
        targetMenu.style.display = isOpen ? 'none' : 'block';
      },

      closeAllHeaderFilterMenus: function() {
        ['subjects', 'tasks', 'statuses', 'sessions', 'runs', 'datatypes', 'rawNames'].forEach(type => {
          const menu = document.getElementById(`megFilterMenu-${type}`);
          if (menu) menu.style.display = 'none';
        });
      },

      setupVirtualScroll: function() {
        const container = document.getElementById('megTableContainer');
        if (!container) return;

        // Calculate container height and visible rows
        const updateViewport = () => {
          const rect = container.getBoundingClientRect();
          megBids.scroll.containerHeight = rect.height;
          this.renderVisibleRows(true);
        };

        // Initial calculation
        updateViewport();

        // Update on scroll
        container.addEventListener('scroll', () => {
          if (megBids.scroll.renderPending) return;
          megBids.scroll.renderPending = true;
          requestAnimationFrame(() => {
            megBids.scroll.renderPending = false;
            this.renderVisibleRows(false);
          });
        });

        // Update on resize
        window.addEventListener('resize', Utils.debounce(updateViewport, 100));
      },

      // Run analysis to generate conversion table
      analyze: async function() {
        const btn = document.getElementById('megAnalyzeBtn');
        if (btn) btn.disabled = true;
        megBids.setStatus('megTableStatus', 'Analyzing raw data...');

        const overwriteAnalysis = document.getElementById('megAnalyzeOverwriteCheck')?.checked || false;

        try {
          const serverConfig = {
            project_name: megBids.config.project_name,
            raw_dir: megBids.config.raw_dir,
            bids_dir: megBids.config.bids_dir,
            tasks: megBids.config.tasks,
            conversion_file: megBids.config.conversion_file,
            config_file: megBids.config.config_file,
            overwrite: megBids.config.overwrite,
            overwrite_conversion: overwriteAnalysis
          };

          const res = await fetch(Utils.apiPath('/meg-run-analysis'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: serverConfig, force_scan: overwriteAnalysis })
          });

          const data = await res.json();

          if (data.error) {
            megBids.setStatus('megTableStatus', 'Error: ' + data.error, 'warn');
            return;
          }

          megBids.tableData = data.table || [];
          megBids.originalData = JSON.parse(JSON.stringify(data.table || []));
          megBids.tableSearchIndex = megBids.tableData.map(row => this.buildRowSearchText(row));
          megBids.tableFile = data.file;
          if (data.file) {
            megBids.config.conversion_file = data.file;
            const pathInput = document.getElementById('megTablePath');
            if (pathInput) pathInput.value = data.file;
            const cfgConv = document.getElementById('megCfgConversionFile');
            if (cfgConv) cfgConv.value = data.file;
            megBids.updateJsonDisplay();
          }
          megBids.modifiedRows.clear();
          megBids.selectedRows.clear();

          this.populateFilters();
          this.applyFilters();

          const container = document.getElementById('megTableContainer');
          if (container) container.scrollTop = 0;

          const saveBtn = document.getElementById('megSaveTableBtn');
          if (saveBtn) saveBtn.disabled = true;

          megBids.setStatus('megTableStatus', `Found ${data.row_count} files. Click a row to edit.`);
        } catch (e) {
          megBids.setStatus('megTableStatus', 'Failed: ' + e.message, 'warn');
        } finally {
          if (btn) btn.disabled = false;
        }
      },

      // Populate filter dropdowns
      populateFilters: function() {
        const subjects = new Set();
        const tasks = new Set();
        const statuses = new Set();
        const sessions = new Set();
        const runs = new Set();
        const datatypes = new Set();
        const rawNames = new Set();

        megBids.tableData.forEach(row => {
          if (row.participant_to) subjects.add(row.participant_to);
          if (row.task) tasks.add(row.task);
          if (row.status) statuses.add(row.status);
          if (row.session_to) sessions.add(row.session_to);
          if (row.run) runs.add(row.run);
          if (row.datatype) datatypes.add(row.datatype);
          if (row.raw_name) rawNames.add(row.raw_name);
        });

        this.availableFilterValues = { subjects, tasks, statuses, sessions, runs, datatypes, rawNames };
        this.renderHeaderFilterOptions('subjects');
        this.renderHeaderFilterOptions('tasks');
        this.renderHeaderFilterOptions('statuses');
        this.renderHeaderFilterOptions('sessions');
        this.renderHeaderFilterOptions('runs');
        this.renderHeaderFilterOptions('datatypes');
        this.renderHeaderFilterOptions('rawNames');
        this.updateFilterButtonStates();
      },

      renderHeaderFilterOptions: function(type) {
        const container = document.getElementById(`megFilterOptions-${type}`);
        const searchEl = document.getElementById(`megFilterSearch-${type}`);
        if (!container) return;

        const sourceSet = (this.availableFilterValues && this.availableFilterValues[type]) ? this.availableFilterValues[type] : new Set();
        const query = (searchEl?.value || '').toLowerCase().trim();
        const values = Array.from(sourceSet).sort().filter(val => !query || String(val).toLowerCase().includes(query));

        if (!values.length) {
          container.innerHTML = '<div style="font-size:0.8rem; color:#9e9e9e;">No matches</div>';
          return;
        }

        container.innerHTML = values.map(val => {
          const checked = megBids.filters[type].has(val) ? 'checked' : '';
          return `
            <label class="header-filter-option">
              <input type="checkbox" data-filter-type="${type}" data-filter-value="${Utils.escapeHtml(val)}" ${checked}>
              <span>${Utils.escapeHtml(val)}</span>
            </label>
          `;
        }).join('');

        container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
          cb.addEventListener('change', (e) => {
            const filterType = e.target.dataset.filterType;
            const value = e.target.dataset.filterValue;
            if (e.target.checked) {
              megBids.filters[filterType].add(value);
            } else {
              megBids.filters[filterType].delete(value);
            }
            this.updateFilterButtonStates();
            this.applyFilters();
          });
        });
      },

      updateFilterButtonStates: function() {
        ['subjects', 'tasks', 'statuses', 'sessions', 'runs', 'datatypes', 'rawNames'].forEach(type => {
          const btn = document.getElementById(`megFilterBtn-${type}`);
          if (btn) {
            btn.classList.toggle('active', megBids.filters[type].size > 0);
          }
        });
      },

      // Apply filters and update visible rows
      applyFilters: function() {
        const searchEl = document.getElementById('megSearchInput');

        const search = (searchEl?.value || '').toLowerCase().trim();

        // Build visible row indices
        megBids.modal.visibleRowIndices = [];

        megBids.tableData.forEach((row, idx) => {
          // Search filter
          if (search) {
            const rowText = megBids.tableSearchIndex[idx] || '';
            if (rowText.indexOf(search) === -1) return;
          }

          // Subject filter
          if (megBids.filters.subjects.size && !megBids.filters.subjects.has(row.participant_to || '')) return;

          // Task filter
          if (megBids.filters.tasks.size && !megBids.filters.tasks.has(row.task || '')) return;

          // Status filter
          if (megBids.filters.statuses.size && !megBids.filters.statuses.has(row.status || '')) return;

          // Session filter
          if (megBids.filters.sessions.size && !megBids.filters.sessions.has(row.session_to || '')) return;

          // Run filter
          if (megBids.filters.runs.size && !megBids.filters.runs.has(row.run || '')) return;

          // Datatype filter
          if (megBids.filters.datatypes.size && !megBids.filters.datatypes.has(row.datatype || '')) return;

          // Raw name filter
          if (megBids.filters.rawNames.size && !megBids.filters.rawNames.has(row.raw_name || '')) return;

          megBids.modal.visibleRowIndices.push(idx);
        });

        // Apply sorting if active
        if (megBids.sort.column && megBids.sort.direction !== 'none') {
          this.applySorting();
        }

        // Clear selection when filters change
        megBids.selectedRows.clear();
        this.updateBatchActions();
        this.renderFilterPills();
        this.renderVisibleRows(true);
      },

      // Apply sorting to visible rows
      applySorting: function() {
        const col = megBids.sort.column;
        const dir = megBids.sort.direction;

        megBids.modal.visibleRowIndices.sort((a, b) => {
          const rowA = megBids.tableData[a];
          const rowB = megBids.tableData[b];
          let valA = rowA[col] || '';
          let valB = rowB[col] || '';

          // Numeric sort for specific columns
          if (['participant_to', 'run', 'split'].includes(col)) {
            const numA = parseInt(valA, 10);
            const numB = parseInt(valB, 10);
            if (!isNaN(numA) && !isNaN(numB)) {
              return dir === 'asc' ? numA - numB : numB - numA;
            }
          }

          // String sort
          valA = String(valA).toLowerCase();
          valB = String(valB).toLowerCase();
          if (valA < valB) return dir === 'asc' ? -1 : 1;
          if (valA > valB) return dir === 'asc' ? 1 : -1;
          return 0;
        });
      },

      // Handle column header click for sorting
      handleSortClick: function(column) {
        if (megBids.sort.column === column) {
          // Cycle: asc -> desc -> none
          if (megBids.sort.direction === 'asc') {
            megBids.sort.direction = 'desc';
          } else if (megBids.sort.direction === 'desc') {
            megBids.sort.direction = 'none';
            megBids.sort.column = null;
          } else {
            megBids.sort.direction = 'asc';
          }
        } else {
          megBids.sort.column = column;
          megBids.sort.direction = 'asc';
        }

        // Update header UI
        document.querySelectorAll('#megConversionTable th').forEach(th => {
          th.classList.remove('sort-asc', 'sort-desc');
        });

        if (megBids.sort.column && megBids.sort.direction !== 'none') {
          const th = document.querySelector(`#megConversionTable th[data-column="${column}"]`);
          if (th) th.classList.add('sort-' + megBids.sort.direction);
        }

        this.applyFilters();
      },

      // Render filter pills
      renderFilterPills: function() {
        const container = document.getElementById('megFilterPills');
        if (!container) return;

        const pills = [];

        megBids.filters.subjects.forEach(val => {
          pills.push(this.createPill('Subject', val, 'subjects'));
        });

        megBids.filters.tasks.forEach(val => {
          pills.push(this.createPill('Task', val, 'tasks'));
        });

        megBids.filters.statuses.forEach(val => {
          pills.push(this.createPill('Status', val, 'statuses'));
        });

        megBids.filters.sessions.forEach(val => {
          pills.push(this.createPill('Session', val, 'sessions'));
        });

        megBids.filters.runs.forEach(val => {
          pills.push(this.createPill('Run', val, 'runs'));
        });

        megBids.filters.datatypes.forEach(val => {
          pills.push(this.createPill('Datatype', val, 'datatypes'));
        });

        megBids.filters.rawNames.forEach(val => {
          pills.push(this.createPill('Raw File', val, 'rawNames'));
        });

        container.innerHTML = pills.join('');

        // Add click handlers to remove pills
        container.querySelectorAll('.filter-pill-remove').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const target = e.currentTarget;
            const type = target.dataset.filterType;
            const value = decodeURIComponent(target.dataset.filterValue || '');
            megBids.filters[type].delete(value);
            this.renderHeaderFilterOptions(type);
            this.updateFilterButtonStates();
            this.applyFilters();
          });
        });
      },

      createPill: function(label, value, type) {
        const encodedValue = encodeURIComponent(value);
        return `
          <span class="filter-pill">
            <span class="filter-pill-label">${Utils.escapeHtml(label)}:</span>
            <span class="filter-pill-value">${Utils.escapeHtml(value)}</span>
            <button class="filter-pill-remove" data-filter-type="${type}" data-filter-value="${encodedValue}" title="Remove filter">×</button>
          </span>
        `;
      },

      clearFilters: function() {
        megBids.filters.subjects.clear();
        megBids.filters.tasks.clear();
        megBids.filters.statuses.clear();
        megBids.filters.sessions.clear();
        megBids.filters.runs.clear();
        megBids.filters.datatypes.clear();
        megBids.filters.rawNames.clear();

        const searchEl = document.getElementById('megSearchInput');
        if (searchEl) searchEl.value = '';

        ['subjects', 'tasks', 'statuses', 'sessions', 'runs', 'datatypes', 'rawNames'].forEach(type => {
          const searchEl = document.getElementById(`megFilterSearch-${type}`);
          if (searchEl) searchEl.value = '';
          this.renderHeaderFilterOptions(type);
        });

        this.updateFilterButtonStates();
        this.applyFilters();
      },

      buildRowSearchText: function(row) {
        return [
          row.status,
          row.participant_to,
          row.session_to,
          row.task,
          row.run,
          row.datatype,
          row.raw_name,
          row.raw_path,
          row.acquisition,
          row.processing,
          row.split,
          row.suffix,
          row.extension,
          row.recording,
          row.space,
          row.description,
          row.tracking_system
        ].map(v => String(v || '')).join(' ').toLowerCase();
      },

      // Render visible rows for virtual scrolling
      renderVisibleRows: function(force) {
        force = !!force;
        const container = document.getElementById('megTableContainer');
        const table = document.getElementById('megConversionTable');
        const tbody = document.getElementById('megTableBody');
        const empty = document.getElementById('megTableEmpty');

        if (!container || !tbody) return;

        if (megBids.modal.visibleRowIndices.length === 0) {
          if (table) table.style.display = 'none';
          if (empty) empty.style.display = 'block';
          tbody.innerHTML = '';
          return;
        }

        if (table) table.style.display = 'table';
        if (empty) empty.style.display = 'none';

        // Recompute viewport height on every render to avoid stale values when step visibility changes.
        megBids.scroll.containerHeight = container.clientHeight || megBids.scroll.containerHeight || 0;
        const headerHeight = table?.querySelector('thead')?.getBoundingClientRect().height || 0;
        const effectiveViewportHeight = Math.max(120, megBids.scroll.containerHeight - headerHeight);

        // Calculate visible range based on scroll position
        const scrollTop = container.scrollTop;
        const startIdx = Math.max(0, Math.floor(scrollTop / megBids.scroll.rowHeight) - megBids.scroll.bufferRows);
        const visibleCount = Math.max(20, Math.ceil(effectiveViewportHeight / megBids.scroll.rowHeight) + 2 * megBids.scroll.bufferRows);
        const endIdx = Math.min(megBids.modal.visibleRowIndices.length, startIdx + visibleCount);

        // Update scroll state
        megBids.scroll.visibleStart = startIdx;
        megBids.scroll.visibleEnd = endIdx;

        // Skip DOM work if viewport window has not changed.
        if (!force &&
            startIdx === megBids.scroll.lastStart &&
            endIdx === megBids.scroll.lastEnd &&
            megBids.modal.visibleRowIndices.length === megBids.scroll.lastVisibleLength) {
          return;
        }

        megBids.scroll.lastStart = startIdx;
        megBids.scroll.lastEnd = endIdx;
        megBids.scroll.lastVisibleLength = megBids.modal.visibleRowIndices.length;

        // Render rows
        let html = '';
        for (let i = startIdx; i < endIdx; i++) {
          const dataIdx = megBids.modal.visibleRowIndices[i];
          const row = megBids.tableData[dataIdx];
          const isModified = megBids.modifiedRows.has(dataIdx);
          const isSelected = megBids.selectedRows.has(dataIdx);

          html += this.renderRow(row, dataIdx, i, isModified, isSelected);
        }

        // Add spacer for virtual scrolling
        const totalHeight = megBids.modal.visibleRowIndices.length * megBids.scroll.rowHeight;
        const topSpacer = startIdx * megBids.scroll.rowHeight;
        const bottomSpacer = totalHeight - (endIdx * megBids.scroll.rowHeight);
        const colCount = document.querySelectorAll('#megConversionTable thead th').length || 8;

        tbody.innerHTML = `
          <tr style="height: ${topSpacer}px;"><td colspan="${colCount}"></td></tr>
          ${html}
          <tr style="height: ${bottomSpacer}px;"><td colspan="${colCount}"></td></tr>
        `;

        this.updateSelectAllState();

        // Update row count display
        const countEl = document.getElementById('megRowCount');
        if (countEl) {
          countEl.textContent = `${megBids.modal.visibleRowIndices.length} of ${megBids.tableData.length} rows`;
        }
      },

      renderRow: function(row, dataIdx, visibleIdx, isModified, isSelected) {
        const modifiedClass = isModified ? 'row-modified' : '';
        const selectedClass = isSelected ? 'row-selected' : '';

        return `
          <tr class="${modifiedClass} ${selectedClass}" data-row-index="${dataIdx}" style="height: ${megBids.scroll.rowHeight}px;">
            <td class="checkbox-cell"><input type="checkbox" data-select-row="true" data-row-index="${dataIdx}" ${isSelected ? 'checked' : ''}></td>
            <td>${Utils.escapeHtml(row.status || '')}</td>
            <td>${Utils.escapeHtml(row.participant_to || '')}</td>
            <td>${Utils.escapeHtml(row.session_to || '')}</td>
            <td>${Utils.escapeHtml(row.task || '')}</td>
            <td>${Utils.escapeHtml(row.run || '')}</td>
            <td>${Utils.escapeHtml(row.datatype || '')}</td>
            <td title="${Utils.escapeHtml(row.raw_path || '')}/${Utils.escapeHtml(row.raw_name || '')}">${Utils.escapeHtml(row.raw_name || '')}</td>
          </tr>
        `;
      },

      // Open modal for editing a row
      openModal: function(dataIdx) {
        const row = megBids.tableData[dataIdx];
        if (!row) return;

        megBids.modal.isOpen = true;
        megBids.modal.currentRowIndex = dataIdx;
        megBids.modal.tempData = { ...row };

        // Update modal header
        const visibleIdx = megBids.modal.visibleRowIndices.indexOf(dataIdx);
        const headerEl = document.getElementById('megModalHeader');
        if (headerEl) {
          headerEl.textContent = `Row ${visibleIdx + 1} of ${megBids.modal.visibleRowIndices.length}`;
        }

        // Populate form fields
        this.setModalValue('megEditSubject', row.participant_to);
        this.setModalValue('megEditSession', row.session_to);
        this.setModalValue('megEditTask', row.task);
        this.setModalValue('megEditAcquisition', row.acquisition);
        this.setModalValue('megEditRun', row.run);
        this.setModalValue('megEditProcessing', row.processing);
        this.setModalValue('megEditSplit', row.split);
        this.setModalValue('megEditRecording', row.recording);
        this.setModalValue('megEditSpace', row.space);
        this.setModalValue('megEditDescription', row.description);
        this.setModalValue('megEditTrackingSystem', row.tracking_system);
        this.setModalValue('megEditSuffix', row.suffix);
        this.setModalValue('megEditExtension', row.extension);
        this.setModalValue('megEditDatatype', row.datatype);
        this.setModalValue('megEditStatus', row.status);

        // Update read-only source info
        const sourceEl = document.getElementById('megEditSource');
        if (sourceEl) {
          sourceEl.textContent = `${row.raw_path || ''}/${row.raw_name || ''}`;
        }

        // Update live preview
        this.updateLivePreview();

        // Reset advanced section
        const advancedContent = document.getElementById('megAdvancedContent');
        if (advancedContent) advancedContent.style.display = 'none';

        // Show modal
        const modal = document.getElementById('megEditModal');
        if (modal) modal.style.display = 'block';

        // Update nav buttons
        this.updateModalNavButtons();
      },

      setModalValue: function(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = value || '';
      },

      // Update live BIDS filename preview
      updateLivePreview: function() {
        const parts = [];

        const subject = document.getElementById('megEditSubject')?.value;
        const session = document.getElementById('megEditSession')?.value;
        const task = document.getElementById('megEditTask')?.value;
        const acquisition = document.getElementById('megEditAcquisition')?.value;
        const run = document.getElementById('megEditRun')?.value;
        const processing = document.getElementById('megEditProcessing')?.value;
        const split = document.getElementById('megEditSplit')?.value;
        const recording = document.getElementById('megEditRecording')?.value;
        const space = document.getElementById('megEditSpace')?.value;
        const description = document.getElementById('megEditDescription')?.value;
        const suffix = document.getElementById('megEditSuffix')?.value;
        const extension = document.getElementById('megEditExtension')?.value;

        if (subject) parts.push(`sub-${subject}`);
        if (session) parts.push(`ses-${session}`);
        if (task) parts.push(`task-${task.toLowerCase()}`);
        if (acquisition) parts.push(`acq-${acquisition}`);
        if (run) parts.push(`run-${String(run).padStart(2, '0')}`);
        if (processing) parts.push(`proc-${processing}`);
        if (split) parts.push(`split-${String(split).padStart(2, '0')}`);
        if (recording) parts.push(`recording-${recording}`);
        if (space) parts.push(`space-${space}`);
        if (description) parts.push(`desc-${description}`);
        if (suffix) parts.push(suffix);

        let filename = parts.join('_');
        if (extension) filename += extension;

        const previewEl = document.getElementById('megFilenamePreview');
        if (previewEl) previewEl.textContent = filename || 'No filename generated yet';
      },

      // Toggle advanced section
      toggleAdvancedSection: function() {
        const content = document.getElementById('megAdvancedContent');
        const toggle = document.getElementById('megAdvancedToggle');
        if (!content || !toggle) return;

        const isVisible = content.style.display !== 'none';
        content.style.display = isVisible ? 'none' : 'block';
        toggle.textContent = isVisible ? '▶ Advanced Entities' : '▼ Advanced Entities';
      },

      // Navigate modal to prev/next row
      navigateModal: function(direction) {
        const currentVisibleIdx = megBids.modal.visibleRowIndices.indexOf(megBids.modal.currentRowIndex);
        const newVisibleIdx = currentVisibleIdx + direction;

        if (newVisibleIdx < 0 || newVisibleIdx >= megBids.modal.visibleRowIndices.length) return;

        const newDataIdx = megBids.modal.visibleRowIndices[newVisibleIdx];
        this.openModal(newDataIdx);
      },

      updateModalNavButtons: function() {
        const currentVisibleIdx = megBids.modal.visibleRowIndices.indexOf(megBids.modal.currentRowIndex);
        const prevBtn = document.getElementById('megModalPrev');
        const nextBtn = document.getElementById('megModalNext');

        if (prevBtn) prevBtn.disabled = currentVisibleIdx <= 0;
        if (nextBtn) nextBtn.disabled = currentVisibleIdx >= megBids.modal.visibleRowIndices.length - 1;
      },

      // Save modal changes
      saveModal: function() {
        const dataIdx = megBids.modal.currentRowIndex;
        if (dataIdx === null) return;

        const row = megBids.tableData[dataIdx];

        // Update row with modal values
        row.participant_to = document.getElementById('megEditSubject')?.value || '';
        row.session_to = document.getElementById('megEditSession')?.value || '';
        row.task = document.getElementById('megEditTask')?.value || '';
        row.acquisition = document.getElementById('megEditAcquisition')?.value || '';
        row.run = document.getElementById('megEditRun')?.value || '';
        row.processing = document.getElementById('megEditProcessing')?.value || '';
        row.split = document.getElementById('megEditSplit')?.value || '';
        row.recording = document.getElementById('megEditRecording')?.value || '';
        row.space = document.getElementById('megEditSpace')?.value || '';
        row.description = document.getElementById('megEditDescription')?.value || '';
        row.tracking_system = document.getElementById('megEditTrackingSystem')?.value || '';
        row.suffix = document.getElementById('megEditSuffix')?.value || '';
        row.extension = document.getElementById('megEditExtension')?.value || '';
        row.datatype = document.getElementById('megEditDatatype')?.value || '';
        row.status = document.getElementById('megEditStatus')?.value || '';

        megBids.tableSearchIndex[dataIdx] = this.buildRowSearchText(row);

        // Mark as modified
        megBids.modifiedRows.add(dataIdx);

        // Enable save button
        const saveBtn = document.getElementById('megSaveTableBtn');
        if (saveBtn) saveBtn.disabled = false;
        this.renderVisibleRows(true);
        this.updateLivePreview();
      },

      // Close modal
      closeModal: function() {
        megBids.modal.isOpen = false;
        megBids.modal.currentRowIndex = null;
        megBids.modal.tempData = null;

        const modal = document.getElementById('megEditModal');
        if (modal) modal.style.display = 'none';
      },

      // Update batch actions visibility
      updateBatchActions: function() {
        const batchDiv = document.getElementById('megBatchActions');
        const countEl = document.getElementById('megSelectedCount');

        const count = megBids.selectedRows.size;

        if (batchDiv) batchDiv.style.display = count > 0 ? 'flex' : 'none';
        if (countEl) countEl.textContent = count + ' selected';
      },

      updateSelectAllState: function() {
        const selectAll = document.getElementById('megSelectAll');
        if (!selectAll) return;

        const visible = megBids.modal.visibleRowIndices;
        if (!visible.length) {
          selectAll.checked = false;
          selectAll.indeterminate = false;
          return;
        }

        const selectedVisibleCount = visible.filter(idx => megBids.selectedRows.has(idx)).length;
        selectAll.checked = selectedVisibleCount === visible.length;
        selectAll.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < visible.length;
      },

      // Batch update status for selected rows
      batchUpdateStatus: function() {
        const statusEl = document.getElementById('megBatchStatus');
        if (!statusEl) return;

        const newStatus = statusEl.value;
        if (!newStatus) return;

        megBids.selectedRows.forEach(dataIdx => {
          megBids.tableData[dataIdx].status = newStatus;
          megBids.tableSearchIndex[dataIdx] = this.buildRowSearchText(megBids.tableData[dataIdx]);
          megBids.modifiedRows.add(dataIdx);
        });

        // Clear selection
        megBids.selectedRows.clear();
        this.updateBatchActions();

        // Enable save button
        const saveBtn = document.getElementById('megSaveTableBtn');
        if (saveBtn) saveBtn.disabled = false;

        this.renderVisibleRows(true);
      },

      // Batch rename task for selected rows
      batchUpdateTask: function() {
        const taskEl = document.getElementById('megBatchTask');
        if (!taskEl) return;

        const newTask = (taskEl.value || '').trim();
        if (!newTask) return;

        megBids.selectedRows.forEach(dataIdx => {
          megBids.tableData[dataIdx].task = newTask;
          megBids.tableSearchIndex[dataIdx] = this.buildRowSearchText(megBids.tableData[dataIdx]);
          megBids.modifiedRows.add(dataIdx);
        });

        // Clear selection and task field
        megBids.selectedRows.clear();
        taskEl.value = '';
        this.updateBatchActions();

        // Enable save button
        const saveBtn = document.getElementById('megSaveTableBtn');
        if (saveBtn) saveBtn.disabled = false;

        this.populateFilters();
        this.applyFilters();
      },

      // Save table to server
      saveTable: async function() {
        if (!megBids.tableFile) return;

        const btn = document.getElementById('megSaveTableBtn');
        if (btn) btn.disabled = true;

        try {
          const res = await fetch(Utils.apiPath('/meg-save-conversion-table'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              table: megBids.tableData,
              file: megBids.tableFile
            })
          });

          const data = await res.json();

          if (data.error) {
            alert('Error saving table: ' + data.error);
            megBids.setStatus('megTableStatus', 'Error: ' + data.error, 'warn');
            if (btn) btn.disabled = false;
          } else {
            megBids.modifiedRows.clear();
            if (data.path) {
              megBids.tableFile = data.path;
              megBids.config.conversion_file = data.path;
              const pathInput = document.getElementById('megTablePath');
              if (pathInput) pathInput.value = data.path;
              const cfgConv = document.getElementById('megCfgConversionFile');
              if (cfgConv) cfgConv.value = data.path;
              megBids.updateJsonDisplay();
            }
            megBids.setStatus('megTableStatus', `Table saved: ${data.rows} rows`);
            this.renderVisibleRows(true);
          }
        } catch (e) {
          alert('Failed to save table: ' + e.message);
          megBids.setStatus('megTableStatus', 'Failed to save: ' + e.message, 'warn');
          if (btn) btn.disabled = false;
        }
      },

      // Step 3: Run BIDS conversion
      runBidsify: async function() {
        const verbose = document.getElementById('megVerboseCheck')?.checked || false;

        const btn = document.getElementById('megRunBidsifyBtn');
        if (btn) btn.disabled = true;

        const progressSection = document.getElementById('megProgressSection');
        if (progressSection) progressSection.style.display = 'block';

        megBids.setStatus('megConversionStatus', 'Running BIDS conversion...');

        const output = document.getElementById('megOutput');
        if (output) output.textContent = 'Starting conversion...\n';

        try {
          const serverConfig = {
            project_name: megBids.config.project_name,
            raw_dir: megBids.config.raw_dir,
            bids_dir: megBids.config.bids_dir,
            tasks: megBids.config.tasks,
            conversion_file: megBids.config.conversion_file,
            config_file: megBids.config.config_file,
            overwrite: megBids.config.overwrite
          };

          const res = await fetch(Utils.apiPath('/meg-run-bidsify'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: serverConfig, verbose: verbose })
          });

          const data = await res.json();

          if (data.error) {
            if (output) output.textContent += '\nError: ' + data.error;
            megBids.setStatus('megConversionStatus', 'Conversion failed', 'warn');
          } else {
            if (output) output.textContent += '\n' + (data.message || 'Conversion completed!');
            megBids.setStatus('megConversionStatus', 'Conversion completed successfully');
          }
        } catch (e) {
          if (output) output.textContent += '\nFailed: ' + e.message;
          megBids.setStatus('megConversionStatus', 'Conversion failed: ' + e.message, 'warn');
        } finally {
          if (btn) btn.disabled = false;
        }
      },

      // Step 4: Generate report
      generateReport: async function() {
        const btn = document.getElementById('megGenerateReportBtn');
        if (btn) btn.disabled = true;

        const loadingEl = document.getElementById('megReportLoading');
        if (loadingEl) loadingEl.style.display = 'block';

        const emptyEl = document.getElementById('megReportEmpty');
        if (emptyEl) emptyEl.style.display = 'none';

        try {
          const serverConfig = {
            project_name: megBids.config.project_name,
            raw_dir: megBids.config.raw_dir,
            bids_dir: megBids.config.bids_dir,
            tasks: megBids.config.tasks,
            conversion_file: megBids.config.conversion_file,
            config_file: megBids.config.config_file,
            overwrite: megBids.config.overwrite
          };

          const res = await fetch(Utils.apiPath('/meg-run-report'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: serverConfig })
          });

          const data = await res.json();

          if (data.error) {
            const emptyEl2 = document.getElementById('megReportEmpty');
            if (emptyEl2) {
              emptyEl2.textContent = 'Error: ' + data.error;
              emptyEl2.style.display = 'block';
            }
          } else {
            megBids.Editor.loadReport();
          }
        } catch (e) {
          const emptyEl2 = document.getElementById('megReportEmpty');
          if (emptyEl2) {
            emptyEl2.textContent = 'Failed: ' + e.message;
            emptyEl2.style.display = 'block';
          }
        } finally {
          if (btn) btn.disabled = false;
          const loadingEl2 = document.getElementById('megReportLoading');
          if (loadingEl2) loadingEl2.style.display = 'none';
        }
      },

      loadReport: async function() {
        try {
          const serverConfig = {
            project_name: megBids.config.project_name,
            raw_dir: megBids.config.raw_dir,
            bids_dir: megBids.config.bids_dir,
            tasks: megBids.config.tasks,
            conversion_file: megBids.config.conversion_file,
            config_file: megBids.config.config_file,
            overwrite: megBids.config.overwrite
          };

          const res = await fetch(Utils.apiPath('/meg-get-report'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: serverConfig })
          });

          const data = await res.json();

          if (data.error) {
            const emptyEl = document.getElementById('megReportEmpty');
            if (emptyEl) {
              emptyEl.textContent = data.error;
              emptyEl.style.display = 'block';
            }
            const contentEl = document.getElementById('megReportContent');
            if (contentEl) contentEl.style.display = 'none';
            return;
          }

          const report = data.report || {};
          const summary = report['BIDS Summary'] || {};

          // Update stats
          const statSubj = document.getElementById('megStatSubjects');
          const statSess = document.getElementById('megStatSessions');
          const statTask = document.getElementById('megStatTasks');
          const statComp = document.getElementById('megStatCompliance');

          if (statSubj) statSubj.textContent = summary['Total Subjects'] || '-';
          if (statSess) statSess.textContent = summary['Total Sessions'] || '-';
          if (statTask) statTask.textContent = summary['Total Tasks'] || '-';
          if (statComp) statComp.textContent = (summary['Compliance Rate (%)'] || 0) + '%';

          // Update summary
          const summaryEl = document.getElementById('megReportSummary');
          if (summaryEl) {
            summaryEl.innerHTML = `
              <div style="display:grid; grid-template-columns: 1fr 1fr; gap:0.5rem;">
                <div>Total Files: <strong>${summary['Total Files'] || 0}</strong></div>
                <div>Valid BIDS: <strong style="color:#4ec9b0;">${summary['Valid BIDS Files'] || 0}</strong></div>
                <div>Invalid BIDS: <strong style="color:#f48771;">${summary['Invalid BIDS Files'] || 0}</strong></div>
                <div>Compliance: <strong>${summary['Compliance Rate (%)'] || 0}%</strong></div>
              </div>
            `;
          }

          // Update findings
          const findingsEl = document.getElementById('megReportFindings');
          if (findingsEl) {
            const findings = report['QA Analysis']?.findings || [];
            if (findings.length > 0) {
              findingsEl.innerHTML = findings.map(f => `
                <div style="padding:0.5rem; border-bottom:1px solid #3c3c3c;">
                  <div style="color:${f.severity === 'error' ? '#f48771' : f.severity === 'warning' ? '#cca700' : '#9e9e9e'};">
                    [${(f.severity || 'INFO').toUpperCase()}] ${f.issue || 'Unknown issue'}
                  </div>
                  <div style="font-size:0.8rem; color:#9e9e9e; margin-top:0.25rem;">${f.suggestion || ''}</div>
                </div>
              `).join('');
            } else {
              findingsEl.innerHTML = '<div style="color:#4ec9b0;">✓ No issues found</div>';
            }
          }

          const emptyEl2 = document.getElementById('megReportEmpty');
          if (emptyEl2) emptyEl2.style.display = 'none';
          const contentEl = document.getElementById('megReportContent');
          if (contentEl) contentEl.style.display = 'block';
        } catch (e) {
          const emptyEl = document.getElementById('megReportEmpty');
          if (emptyEl) {
            emptyEl.textContent = 'Failed to load report: ' + e.message;
            emptyEl.style.display = 'block';
          }
        }
      }
    }
  };

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => megBids.init());
  } else {
    megBids.init();
  }
})();
