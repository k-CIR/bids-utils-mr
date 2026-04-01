# bids-utils-mr
Utilities in the project folder on SPICE to support PET data checks,
HTML overview tools, and CSV-based PET QC progress tracking.

## What the app does

- Serves a local web UI for PET utilities.
- PET overview tab:
	- Lists top-level `.html` files in a selected PET folder.
	- Opens listed HTML files in the browser.
- PET QC overview tab:
	- Lists CSV files directly inside `derivatives`.
	- Loads a source CSV and manages a separate target QC tracker CSV.
	- Supports read-only mode, editable status dropdowns, and merge-from-completed workflow.
	- **Add rows from completed sessions**: Merge new rows from completed session files with automatic file discovery.

## CSV Tracker Workflow (PET QC overview tab)

The PET QC overview tab is designed around two files:

- Source CSV: `completed_sessions_*.csv` (reference input)
- Target CSV: `progress.csv` (QC tracker that you continue editing over time)

### Recommended file locations

- Source CSV: `BIDS_pet/derivatives/completed_sessions_*.csv`
- Target CSV: `BIDS_pet/derivatives/progress.csv`

Only CSV files directly inside `derivatives` are listed by the UI.

## How to use

1. Start the server.
2. Open the app in your browser and go to **PET QC overview**.
3. Set source CSV to your `completed_sessions_*.csv` file.
4. Set target CSV to `BIDS_pet/derivatives/progress.csv`.
5. Keep **Read-only** enabled by default.
6. Click **Initialize target from source** to create/update target columns and rows.
7. Click **Load target** when you want to continue work from `progress.csv`.
8. Disable **Read-only** only when you want to edit status fields.

## Keeping progress.csv up to date

When new sessions appear in completed session files:

1. Load your current `progress.csv` target.
2. Click **Add rows from completed sessions**.
3. A dialog appears with:
   - **Dropdown menu**: Automatically lists all CSV files containing the pattern `completed_pet_sessions` in the derivatives directory. Simply select from the list.
   - **Manual path entry**: Alternatively, manually enter the file path if needed.
4. Click **Merge** to append missing subject/session rows to `progress.csv`.

Missing subject/session rows are appended while existing tracker values are preserved.
