# Benchtop ELN

An open-source, self-hosted electronic lab notebook for synthetic chemistry. It covers the core
workflow of commercial chemistry ELNs such as Revvity Signals Notebook: notebooks of
experiments, a structure-aware stoichiometry table, a materials registry with lot-level
inventory, and an author/witness e-signature workflow backed by a tamper-evident audit trail.

This is a clean-room implementation built from publicly documented ELN concepts. It contains
no code, assets or data formats from any proprietary product and is not affiliated with Revvity.

## What it does

| Area | Features |
|---|---|
| **Notebooks & experiments** | Notebooks hold experiments (`EXP-00042`). Each experiment is a stack of sections: rich text (sub/superscript, tables, chemistry symbols), reaction, data table, files. Tags, templates, duplicate, printable report and JSON export. |
| **Chemistry** | Draw structures and reactions in [Ketcher](https://github.com/epam/ketcher) (runs fully in the browser via WebAssembly). Drawing a reaction fills the stoichiometry table automatically, keeping counter-ions together (NaBH₄ stays one row). RDKit computes MW, formula, InChIKey and depictions server-side. |
| **Conditions** | Temperature (or rt/reflux), time, atmosphere and target concentration as real fields. Tick *fill to target M* on a solvent and its volume is calculated for you. |
| **Stoichiometry** | Enter mass, volume, mmol or equivalents in any row; the rest is calculated from the limiting reagent, accounting for purity, density (neat liquids) and molarity (solutions). Products get theoretical mass and % yield. The cell you typed is highlighted so you always know what's input vs. calculated. |
| **Work-up & characterisation** | Purification sections (method, stationary phase, eluent, Rf, isolated mass, appearance) and characterisation sections (¹H/¹³C NMR, HRMS, IR, mp, Rf, HPLC…), each item linkable to its data file. |
| **Spectra** | Drag spectrum files onto an experiment: JCAMP-DX (incl. compressed SQZ/DIF/DUP), zipped Bruker experiments (processed `pdata/*/1r`) and two-column CSV become characterisation items with technique, frequency and solvent filled in, and open already analysed. ¹H auto-analysis finds the signals, cuts out solvent/water/TMS lines (where they actually are), and scales integrals to the product formula's proton count; ¹³C gets a signal-to-noise peak list without the solvent. A note offers one-click referencing when the solvent residual is off. Viewing is inline or full-screen. Scroll to zoom at the cursor, shift-scroll to pan, live δ/Hz readout. Integrate by dragging: running integral curves are drawn over the peaks with values under the axis. Each ¹H signal is classified (s, d, t, q, p, dd, dt, td, m) with J in Hz, and you can override any multiplicity or J in the signals table. Type a proton count in any row to make it the reference. Click a peak to reference the axis (solvent residuals for CDCl₃, DMSO-d₆, CD₃OD, C₆D₆, D₂O, CD₂Cl₂, acetone-d₆, CD₃CN are marked and offered as one-click references). Integrated ¹H signals are labelled above the spectrum ("7.42 t, 2H") with leader lines to each multiplet, and integral values below the axis never overlap. Export a clean PNG for SI or slides. Everything is saved with the record. |
| **Spectrum checks** | Auto-analysis flags residual lab solvents (EtOAc, hexane, acetone, DCM, Et₂O, THF, MeOH, toluene, DMF, Et₃N, silicone grease and more, from the Fulmer 2010 tables for CDCl₃, DMSO-d₆ and CD₃OD) only when every one of their signals is present, and reports each as mol% and wt% with an overall purity estimate. A checks panel compares the spectrum with the drawn product: total ¹H integral vs. the formula (allowing for exchangeable OH/NH), number of signals vs. symmetry-distinct H environments, ¹³C peaks vs. unique carbons, and warns when a minor set of signals looks like a second compound. *Compare* overlays up to four other spectra (starting material, crude, a reference), stacked or overlaid, and the overlay is saved with the record. |
| **HRMS helper** | On HRMS/MS items, pick the adduct ([M+H]⁺, [M+Na]⁺, [M+K]⁺, [M+NH₄]⁺, M⁺, [M−H]⁻), get the calculated m/z from the product structure (monoisotopic, electron mass corrected), type the found value and see the error in ppm; *Use* writes the standard `calcd for … ; found …` line. |
| **Dashboard** | The home page is a grid of widgets you arrange yourself: work lists (waiting for my witness signature, returned to me with the reason, my open experiments, recently edited, pinned), quick actions, sticky notes and section headings, a yield-over-time chart (click a point to open the experiment), this month's activity, recent spectra, and inventory alerts (lots below 20 % of what was received, or expiring within 30 days). *Customise* lets you drag, resize, add and remove widgets; the layout is saved to your account. Pin any experiment from its page. On a phone the widgets stack in one column. |
| **Write-up drafts** | *Draft write-up* composes an SI-style experimental paragraph from the reaction table, conditions, work-up and characterisation (amounts with sensible significant figures, mol% for catalysts, yield). It never guesses: anything missing is listed instead. Copy as rich text or add it as a section. |
| **Obsidian export** | Every experiment as a Markdown note with Dataview-ready front matter (status, product, yield, temperature, time…), the scheme as SVG, tables and attachments. `./benchtop export-obsidian <folder>` or set `BENCHTOP_OBSIDIAN_DIR` to sync hourly; an index note includes a ready-made Dataview table. |
| **Reactions overview** | Every reaction across notebooks in one table: scheme, scale, reagents, conditions, yield, purification, data recorded. Filter by text, substructure, yield and temperature; sort; download CSV. |
| **Materials & inventory** | Structure-deduplicated registry (`MAT-00007`) with CAS, density, hazards. Lots/containers with barcode, location, supplier, purity. Every withdrawal is a ledger entry with unit conversion (g ↔ mL via density). One click deducts a reaction's reagents from their lots (after showing exactly what will be taken), and each deduction can be undone; one click registers an isolated product as a new material + lot linked back to the experiment. |
| **Search** | Full-text across titles and section content; **substructure** (SMILES/SMARTS) and **similarity** (Morgan/Tanimoto) search across reaction tables and the registry. |
| **Signing** | Author signs → record locks → a different user witnesses (or returns it with a reason). Signing requires password re-entry. Each signature stores a SHA-256 hash of the full record (all sections + attachment hashes); *Verify integrity* recomputes it and flags out-of-band edits. Records can be voided, never deleted. |
| **History & audit** | Every section save is a new version (view any past version). Every mutation, login and failed signature attempt is written to an append-only audit log where each event hashes the previous one; *Verify hash chain* detects any altered, removed or reordered event. Attachments are content-addressed and checked against their hash on download. |
| **Quick navigation** | ⌘K (Ctrl K) finds any experiment, material (by name or CAS), notebook or page, and runs actions such as "New experiment"; press N anywhere for a new experiment. Amount fields accept units: `1.2 g` in a mg cell, `250 µL` in mL, `0.5 mol`, `30 min`, `500 mM`. |
| **Users & accounts** | Admin, scientist and read-only viewer roles. A fresh install opens on a setup screen where you create your own administrator account (the built-in admin login is then retired). Sign-ins can last 30 days. Everyone has an account page to change their name, email, username and password, and preferences (light/dark theme, default notebook, open spectra full-screen) that follow them between computers. Signatures store the printed name at the moment of signing, so later name changes never alter signed records. Admins can edit any account. |

## Quick start (Mac)

Double-click **Benchtop.command** in the folder, or run:

```bash
./benchtop            # first run sets everything up, then opens your browser
```

Benchtop runs while that Terminal window is open; close it (or press Ctrl+C) to stop. On a fresh install it
asks you to create your own account (you become the administrator) and can add a demo notebook. If macOS
says Benchtop.command can't be opened because it was downloaded, right-click it and choose *Open* once.
Needs Python 3.11+ (python.org); Node is only needed if the bundled interface has to be rebuilt.

```bash
./benchtop status               # running? which version? where is the data?
./benchtop upgrade NEW.zip      # backs up, swaps in the new version, keeps your data
./benchtop backup | backups | restore FILE
./benchtop seed                 # demo data
./benchtop export-obsidian ~/Documents/Vault/Benchtop
```

Backups are automatic: one on start-up if the newest is more than a day old, then daily while running,
keeping the newest 14. Each is a single zip with a consistent database snapshot and every attachment.
Set `BENCHTOP_BACKUP_DIR` to a folder in iCloud Drive or Dropbox to keep copies off your Mac.

## Quick start (Docker)

```bash
docker compose up -d --build
docker compose exec app python -m app.seed   # optional demo data
open http://localhost:8000
```

Sign in as `admin` with the password in `BENCHTOP_ADMIN_PASSWORD` (default in compose: `change-me-now`).
The demo seed adds **alice** (author) and **bob** (witness), both `benchtop123`, a reagent
registry with lots, a template and a worked Suzuki coupling.

For a shared server, run Postgres instead of SQLite: `docker compose --profile postgres up -d`
and set `BENCHTOP_DATABASE_URL` as shown in `docker-compose.yml`. Put it behind HTTPS (Caddy,
nginx, Tailscale) before anyone uses it over a network.

## Development

```bash
# API (Python 3.11+)
cd backend
pip install -r requirements.txt pytest
python -m app.seed                       # creates ./data with demo content
uvicorn app.main:app --reload            # http://localhost:8000/docs for the OpenAPI explorer
pytest -q

# Web app (Node 20+)
cd frontend
npm install
npm run dev                              # http://localhost:5173, proxies /api to :8000
```

To serve the built app from the API (what the Docker image does):
`npm run build`, then start uvicorn with `BENCHTOP_STATIC_DIR=../frontend/dist`.

### Configuration

| Variable | Default | |
|---|---|---|
| `BENCHTOP_DATA_DIR` | `./data` | SQLite file, attachments, generated secret key |
| `BENCHTOP_DATABASE_URL` | SQLite in data dir | Any SQLAlchemy URL, e.g. `postgresql+psycopg://…` |
| `BENCHTOP_SECRET_KEY` | generated, stored in data dir | Signs session tokens |
| `BENCHTOP_ADMIN_USER` / `_PASSWORD` | `admin` / `admin` | First account, created only when the user table is empty |
| `BENCHTOP_TOKEN_TTL_HOURS` | `12` | Session length |
| `BENCHTOP_MAX_UPLOAD_MB` | `50` | Attachment size limit |
| `BENCHTOP_STATIC_DIR` | unset | Serve the built frontend from this directory |
| `BENCHTOP_BACKUP_DIR` | `<data dir>/backups` | Where backups are written |
| `BENCHTOP_BACKUP_KEEP` | `14` | Number of backups kept |
| `BENCHTOP_BACKUP_INTERVAL_HOURS` | `24` | `0` turns automatic backups off |
| `BENCHTOP_PORT` | `8000` | Port used by `./benchtop` |
| `BENCHTOP_OBSIDIAN_DIR` | unset | Vault folder to sync Markdown notes into every hour |

## Architecture

```
frontend/  React 18 + TypeScript + Vite
  pages/            Home, Notebook, Experiment, Materials, Inventory, Search, Templates, Admin
  components/       EntryCard (autosave + optimistic concurrency), ReactionEntry (stoichiometry grid),
                    ConditionsBar, PurificationEntry, CharacterizationEntry, SpectrumViewer (canvas),
                    TextEntry (TipTap), StructureEditor (Ketcher, lazy-loaded), SignDialog, …
  spectra.ts        JCAMP-DX / CSV reader, peak picking, integration
backend/   FastAPI + SQLModel + RDKit
  app/models.py     Notebook, Experiment, Entry, EntryVersion, Attachment, Signature,
                    Material, Lot, LotTransaction, AuditEvent, User
  app/chem.py       structure parsing, depiction, reaction → rows, stoichiometry engine, search
  app/audit.py      hash-chained audit log + verifier
  app/backup.py     snapshot/zip backups, pruning, restore (also a CLI)
  app/writeup.py    SI-style experimental paragraph composer
  app/export_md.py  Markdown/Obsidian export and vault sync (also a CLI)
  app/routers/      auth, experiments (+signing, export), materials, chemistry, search, reactions, admin
  tests/            API tests incl. signature tamper detection and audit-chain tamper detection
```

Design notes:

- **The server is the source of truth for calculations.** The grid posts rows to
  `/api/chem/stoichiometry` as you type and every saved reaction section is recomputed on save,
  so stored numbers can't drift from what the engine would produce.
- **Saves are versioned and conflict-checked.** Each section save sends the version it was based
  on; a stale save returns 409 instead of silently overwriting a colleague's edit.
- **Upgrades are in place.** On start-up, columns added by newer versions are added to an existing database automatically (always nullable, so existing rows are untouched).
- **Nothing is hard-deleted.** Sections are soft-deleted with a final version; lots go to
  `empty`/`disposed`; experiments are voided.

## API

Everything the UI does is available over REST with a bearer token; interactive docs at `/docs`.

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'content-type: application/json' \
  -d '{"username":"alice","password":"benchtop123"}' | jq -r .token)
curl -s localhost:8000/api/search -G -d q='c1ccccc1Br' -d mode=substructure -H "Authorization: Bearer $TOKEN"
```

## Regulatory note

Benchtop implements technical controls that 21 CFR Part 11 / EU Annex 11 expect (unique
accounts, password-backed e-signatures with meaning and timestamp, record locking, version
history, append-only audit trail with tamper evidence). Software alone is not compliance: a
regulated deployment still needs validation (IQ/OQ/PQ), SOPs, access reviews, backups and
time-source controls owned by your organisation. Treat this as a research ELN unless you do
that work.

## Roadmap ideas

- Plate/sample-set entities and a sample-tracking view
- Bruker/Varian raw FID processing (FT, phasing) and mzML for MS
- Instrument file drop-folders that auto-attach to experiments
- SSO (OIDC), per-notebook sharing, project-level permissions
- Witness assignment and email notifications
- PDF/A export with embedded signature manifest

## Licence

Apache-2.0. Bundles Ketcher (Apache-2.0, EPAM) and uses RDKit (BSD-3-Clause).
