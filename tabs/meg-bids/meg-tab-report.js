// meg-tab-report.js
(function() {
  if (!window.megBids) return;

  // Override loadReport to include stats
  var originalLoadReport = megBids.loadReport;
  megBids.loadReport = async function() {
    try {
      var serverConfig = {
        project_name: megBids.config.project_name,
        raw_dir: megBids.config.raw_dir,
        bids_dir: megBids.config.bids_dir,
        tasks: megBids.config.tasks,
        conversion_file: megBids.config.conversion_file,
        overwrite: megBids.config.overwrite
      };

      var res = await fetch(megBids._p('/meg-get-report'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: serverConfig })
      });
      var data = await res.json();

      if (data.error) {
        var emptyEl = document.getElementById('megReportEmpty');
        if (emptyEl) emptyEl.textContent = data.error;
        var contentEl = document.getElementById('megReportContent');
        if (contentEl) contentEl.style.display = 'none';
        emptyEl.style.display = 'block';
        return;
      }

      var report = data.report || {};
      var summary = report['BIDS Summary'] || {};

      // Update stats dashboard
      var subjects = {};
      var sessions = {};
      var tasks = {};
      var entries = report['Report Table'] || [];
      for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        if (e.Participant) subjects[e.Participant] = true;
        if (e.Session) sessions[e.Session] = true;
        if (e.Task) tasks[e.Task] = true;
      }

      var statSubj = document.getElementById('megStatSubjects');
      var statSess = document.getElementById('megStatSessions');
      var statTask = document.getElementById('megStatTasks');
      var statComp = document.getElementById('megStatCompliance');

      if (statSubj) statSubj.textContent = Object.keys(subjects).length || summary['Total Subjects'] || '-';
      if (statSess) statSess.textContent = Object.keys(sessions).length || summary['Total Sessions'] || '-';
      if (statTask) statTask.textContent = Object.keys(tasks).length || summary['Total Tasks'] || '-';
      if (statComp) statComp.textContent = (summary['Compliance Rate (%)'] || 0) + '%';

      // Render summary
      var summaryEl = document.getElementById('megReportSummary');
      if (summaryEl) {
        summaryEl.innerHTML = '<div style="display:grid; grid-template-columns: 1fr 1fr; gap:0.5rem;">' +
          '<div>Total Files: <strong>' + (summary['Total Files'] || 0) + '</strong></div>' +
          '<div>Valid BIDS: <strong style="color:#4ec9b0;">' + (summary['Valid BIDS Files'] || 0) + '</strong></div>' +
          '<div>Invalid BIDS: <strong style="color:#f48771;">' + (summary['Invalid BIDS Files'] || 0) + '</strong></div>' +
          '<div>Compliance: <strong>' + (summary['Compliance Rate (%)'] || 0) + '%</strong></div>' +
          '</div>';
      }

      // Render findings
      var findingsEl = document.getElementById('megReportFindings');
      if (findingsEl) {
        var findings = report['QA Analysis'] ? report['QA Analysis'].findings || [] : [];
        if (findings.length > 0) {
          var html = '';
          for (var f = 0; f < findings.length; f++) {
            var finding = findings[f];
            var color = '#9e9e9e';
            if (finding.severity === 'error') color = '#f48771';
            else if (finding.severity === 'warning') color = '#cca700';
            html += '<div style="padding:0.5rem; border-bottom:1px solid #3c3c3c;">' +
              '<div style="color:' + color + ';">[' + (finding.severity || 'INFO').toUpperCase() + '] ' + (finding.issue || 'Unknown issue') + '</div>' +
              '<div style="font-size:0.8rem; color:#9e9e9e; margin-top:0.25rem;">' + (finding.suggestion || '') + '</div>' +
              '</div>';
          }
          findingsEl.innerHTML = html;
        } else {
          findingsEl.innerHTML = '<div style="color:#4ec9b0;">✓ No issues found</div>';
        }
      }

      var emptyEl2 = document.getElementById('megReportEmpty');
      var contentEl2 = document.getElementById('megReportContent');
      if (emptyEl2) emptyEl2.style.display = 'none';
      if (contentEl2) contentEl2.style.display = 'block';

    } catch (e) {
      var emptyEl3 = document.getElementById('megReportEmpty');
      if (emptyEl3) {
        emptyEl3.textContent = 'Failed to load report: ' + e.message;
        emptyEl3.style.display = 'block';
      }
    }
  };
})();
