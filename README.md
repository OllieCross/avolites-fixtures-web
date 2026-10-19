# Avolites Fixture Files

Login-free website to download individual Avolites Titan / Tiger Touch fixture personalities (`.d4`), instead of the full ~1 GB `TitanFixtureLibrary` installer.

- **updater** container: once a day (default 03:30 UTC) reads `personalities.avolites.com/Downloads.asp`, picks the newest `TitanFixtureLibraryV*.exe`, and `HEAD`s it. Only if ETag / Last-Modified / size changed does it download (~1.1 GB), unpack the NSIS installer with 7-Zip, extract `*.d4`, hash every file and diff against the index:
  - new file → **New** badge (30 days)
  - changed content → **Updated** badge (30 days)
  - gone upstream → kept, marked **Removed**
  - first import is a baseline (no flood of "new")
- **web** container: FastAPI + vanilla JS. Search by model, filter by manufacturer, single `.d4` download, multi-select → ZIP (max 300), changelog tab.
- Shared named volume `data`: `fixtures.db` (SQLite), `files/*.d4`.

## Run

```bash
cp .env.example .env     # set Traefik network / entrypoint / certresolver
docker compose up -d --build
docker compose logs -f updater   # first import takes a few minutes
```

Force a re-import: `docker compose exec updater python -m app.updater --once --force`

Transient disk need during an import: ~2.5 GB inside the volume (installer + extraction), freed afterwards.

## Notes

Unofficial mirror. Fixture files are © Avolites Ltd. and contributors.
