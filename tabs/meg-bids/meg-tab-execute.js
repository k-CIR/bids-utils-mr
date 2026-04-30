// meg-tab-execute.js
(function() {
  if (!window.megBids) return;

  // Override runBidsify with enhanced output
  var originalRunBidsify = megBids.runBidsify;
  megBids.runBidsify = async function() {
    var verboseEl = document.getElementById('megVerboseCheck');
    var verbose = verboseEl ? verboseEl.checked : false;

    document.getElementById('megRunBidsifyBtn').disabled = true;
    document.getElementById('megProgressSection').style.display = 'block';
    megBids.setStatus('megConversionStatus', 'Running BIDS conversion...');

    var output = document.getElementById('megOutput');
    if (output) output.textContent = 'Starting conversion...\n';

    try {
      var serverConfig = {
        project_name: megBids.config.project_name,
        raw_dir: megBids.config.raw_dir,
        bids_dir: megBids.config.bids_dir,
        tasks: megBids.config.tasks,
        conversion_file: megBids.config.conversion_file,
        overwrite: megBids.config.overwrite
      };

      var res = await fetch(megBids._p('/meg-run-bidsify'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: serverConfig, verbose: verbose })
      });
      var data = await res.json();

      if (data.error) {
        if (output) output.textContent += '\nError: ' + data.error;
        megBids.setStatus('megConversionStatus', 'Conversion failed', 'warn');
      } else {
        if (output) output.textContent += '\n' + (data.message || 'Conversion completed!');
        megBids.setStatus('megConversionStatus', 'Conversion completed successfully');
        setTimeout(function() { megBids.switchStep(4); }, 1000);
      }
    } catch (e) {
      if (output) output.textContent += '\nFailed: ' + e.message;
      megBids.setStatus('megConversionStatus', 'Conversion failed: ' + e.message, 'warn');
    } finally {
      document.getElementById('megRunBidsifyBtn').disabled = false;
    }
  };
})();
