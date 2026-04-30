// meg-tab-editor.js
(function() {
  var m = window.megBids;
  if (!m) return;

  m.populateFilters = function() {
    if (!m.tableData || m.tableData.length === 0) return;
    var subjects = {};
    var tasks = {};
    for (var i = 0; i < m.tableData.length; i++) {
      var row = m.tableData[i];
      if (row.participant_from) subjects[row.participant_from] = true;
      if (row.task) tasks[row.task] = true;
    }
    
    var subjSel = document.getElementById('megSubjectFilter');
    if (subjSel) {
      var opts = '<option value="">All Subjects</option>';
      var keys = Object.keys(subjects).sort();
      for (var s = 0; s < keys.length; s++) {
        opts += '<option value="' + keys[s] + '">' + keys[s] + '</option>';
      }
      subjSel.innerHTML = opts;
    }
    
    var taskSel = document.getElementById('megTaskFilter');
    if (taskSel) {
      var opts2 = '<option value="">All Tasks</option>';
      var keys2 = Object.keys(tasks).sort();
      for (var t = 0; t < keys2.length; t++) {
        opts2 += '<option value="' + keys2[t] + '">' + keys2[t] + '</option>';
      }
      taskSel.innerHTML = opts2;
    }
  };

  m.filterTable = function() {
    var searchEl = document.getElementById('megSearchInput');
    var subjectEl = document.getElementById('megSubjectFilter');
    var taskEl = document.getElementById('megTaskFilter');
    var statusEl = document.getElementById('megStatusFilter');
    
    var search = searchEl ? searchEl.value.toLowerCase() : '';
    var subject = subjectEl ? subjectEl.value : '';
    var task = taskEl ? taskEl.value : '';
    var status = statusEl ? statusEl.value : '';
    
    var rows = document.querySelectorAll('#megTableBody tr');
    for (var r = 0; r < rows.length; r++) {
      var tr = rows[r];
      var idx = tr.getAttribute('data-idx');
      if (!idx) continue;
      var row = m.tableData[idx];
      if (!row) continue;
      
      var show = true;
      if (search && JSON.stringify(row).toLowerCase().indexOf(search) === -1) show = false;
      if (subject && row.participant_from !== subject) show = false;
      if (task && row.task !== task) show = false;
      if (status && row.status !== status) show = false;
      
      tr.style.display = show ? '' : 'none';
    }
  };

  m.updateSelectionCount = function() {
    var boxes = document.querySelectorAll('#megTableBody input[type="checkbox"]:checked');
    var count = boxes.length;
    var batchDiv = document.getElementById('megBatchActions');
    var countEl = document.getElementById('megSelectedCount');
    if (batchDiv) batchDiv.style.display = count > 0 ? 'block' : 'none';
    if (countEl) countEl.textContent = count + ' selected';
  };

  m.batchUpdateStatus = function() {
    var statusEl = document.getElementById('megBatchStatus');
    if (!statusEl) return;
    var newStatus = statusEl.value;
    if (!newStatus) return;
    
    var boxes = document.querySelectorAll('#megTableBody input[type="checkbox"]:checked');
    for (var c = 0; c < boxes.length; c++) {
      var cb = boxes[c];
      var idx = parseInt(cb.dataset.row);
      if (m.tableData[idx]) {
        m.tableData[idx].status = newStatus;
      }
    }
    if (typeof m.saveTable === 'function') m.saveTable();
  };
})();
