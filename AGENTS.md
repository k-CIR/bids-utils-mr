# BIDS Utils MR - Agent Guidance

## Core Commands

- **Start the server**:
  ```bash
  python server.py
  ```
  - Runs on `http://localhost:{PORT}/?token={AUTH_TOKEN}` (token printed to console).
  - Port defaults to `8080` but auto-selects if occupied.

- **Authentication**:
  - Token is required for all endpoints except `/` and `/index.html`.
  - Token is auto-generated if not set via `AUTH_TOKEN` env var.


## Architecture & Conventions

- **Tab system**:
  - Tabs are dynamically loaded from `tabs/*/routes.py`.
  - Each tab must define `TAB_METADATA` (id, label, order) and a `register()` function.
  - Tabs are only visible if their `requires_path` exists in `/data/projects/{PROJECT_NAME}`.

- **Project root detection**:
  - Auto-detects `/data/projects/{PROJECT_NAME}` as the root, even if the repo is nested.
  - Falls back to the parent directory of the repo if not found.

- **Rate limiting**:
  - 75 requests per minute per IP (hardcoded in `server.py`).


## Key Files & Directories

- **`server.py`**: Entry point. Handles HTTP server, auth, and tab discovery.
- **`tabs/`**: Contains tab-specific logic. Each subdirectory must include:
  - `routes.py`: Defines endpoints and tab metadata.
  - `tab.html`: UI for the tab.
- **`index.html`**: Main UI. Dynamically loads tabs via `/api/tabs` and `/tab-content`.


## Operational Quirks

- **Generated files**:
  - `.gitignore` excludes:
    - `dcm2bids_config_mr.json` and `dcm2bids_config_pet.json` (auto-generated).
    - `tabs/*/dcm2bids_helper/` and `tabs/*/*.csv` (tab-specific temp files).
    - `.pet2bids-container-home/` (PET processing artifacts).

- **Environment**:
  - No build step or dependencies (pure Python + HTML/JS).
  - No tests or linting configured.


## SPICE-Specific Notes

- **Deployment**:
  - Designed to run on SPICE. Clone this repo into your project folder on SPICE.
  - Use `serve-mr-bids` locally to connect to the SPICE service.

- **BIDS conversion**:
  - All processing is done by `dcm2bids` on SPICE. This repo only provides a UI for config generation.
  - Config files (`dcm2bids_config_*.json`) are generated via the UI and excluded from Git.