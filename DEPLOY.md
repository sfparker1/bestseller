# Selling-report photo + scent filler: deploy guide

Flask app. Upload the weekly selling-report .xlsx, click Run, download the finished workbook plus a report CSV.
No Excel/COM: pictures are inserted with openpyxl, so it works for the all-Mac team.

## What it does
1. Finds tabs with `PHOTO` / `SCENT` / `VIN` headers (header row auto-detected in the first 15 rows, so it handles the weekly tabs and ignores BCF/OO tabs).
2. Lists `/S+F/TOP Pictures` once through the Dropbox API (metadata only; cached 15 min), then downloads **only the photos needed** (exact `<SKU>.jpg|.jpeg|.png`, case-insensitive). Fallback names (`SKU-1`, `SKUBQ`) are used only if no exact file exists, and are listed as "verify".
3. Crops each photo with GrabCut and caches the crop by SKU on the disk (re-downloaded only if the Dropbox file's content hash changes). First run on ~300 SKUs takes a few minutes; later weeks are fast.
4. Fills blank SCENT cells from `scents.csv`. SKUs not in the csv get a best-effort parse of the description, shown light-yellow, listed in the report, and appended to `scents.csv` with `guess=Y`.
5. Photo column A width 24, rows 80 pt, photo centered in the cell; Scent column width 28; Description column widened to fit.

## Environment variables (set in the Render dashboard, Environment tab)
| Name | Required | Notes |
|---|---|---|
| `DROPBOX_REFRESH_TOKEN`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET` | yes (preferred) | Never expires. |
| `DROPBOX_ACCESS_TOKEN` | alternative | Simpler, but Dropbox's generated tokens are short-lived; use only for testing. |
| `DROPBOX_FOLDER` | no | Default `/S+F/TOP Pictures`. |
| `APP_PASSWORD` | one of these two | Shared team password (same as packing-list-acumatica). |
| `APP_USERS` | one of these two | Per-user logins `alice:pw1,bob:pw2`. |
| `SECRET_KEY` | auto | `render.yaml` generates it; keeps logins valid across restarts. |
| `DATA_DIR` | set in yaml | `/var/data` (persistent disk: crop cache, scents.csv, job files). |
| `INDEX_TTL_SECONDS` | no | Folder listing cache, default 900. |

### Creating the Dropbox app (you do this; do not paste the token into chat)
1. https://www.dropbox.com/developers/apps -> Create app -> Scoped access -> **Full Dropbox** (the folder is in a team/shared space; if it is in the app folder you can pick App folder and set `DROPBOX_FOLDER=/`).
2. Permissions tab: tick **files.metadata.read** and **files.content.read** only (read-only), then Submit.
3. Copy App key / App secret. Get a refresh token once: open
   `https://www.dropbox.com/oauth2/authorize?client_id=<APP_KEY>&response_type=code&token_access_type=offline`,
   approve, copy the code, then
   `curl https://api.dropbox.com/oauth2/token -d code=<CODE> -d grant_type=authorization_code -u <APP_KEY>:<APP_SECRET>`
   and keep the `refresh_token` from the response.
4. If the S+F folder belongs to a Dropbox Team, the token needs a team member context; ask if the listing returns a path error.

## Deploy on Render
1. Put this folder in a GitHub repo (like `sfparker1/shipments`). Render -> New -> Blueprint -> pick the repo; it reads `render.yaml`.
2. Fill the `sync: false` variables above. Plan must be Starter or higher (persistent disk).
3. Open the URL, sign in, upload a workbook.

`render.yaml` start command: `gunicorn app:app --workers 1 --threads 8 --timeout 120`. Keep **1 worker** (job state is in memory).

## Editing scents
Home page -> Download scents.csv, edit in Excel (columns `sku,scent,guess`; clear `guess` once verified), upload it back. Edits live on the disk and survive deploys. The copy in the repo is only the seed used the first time the disk is empty.

## Local run / tests
```
pip install -r requirements.txt
set APP_PASSWORD=test & set DROPBOX_ACCESS_TOKEN=... & python app.py
python tests/run_local.py <in.xlsx> <local photo folder> <out.xlsx>   # offline, uses a synced folder instead of Dropbox
python tests/smoke_app.py <in.xlsx> <local photo folder>              # login + job flow
```

## Known limits
- openpyxl rewrites the workbook: cell formatting, column widths, conditional formats, formulas are kept, but pivot tables, slicers, or charts on the weekly tabs would not be. The sample workbook had none. Existing photos in column A on processed tabs are replaced on a re-run.
- Crops are resized (<=520 px) and GrabCut runs on a <=1400 px copy, so crops differ slightly from the Windows prototype's full-resolution crops.
- Description text is truncated at 25 characters in the source report; parsed scents for long names ("H52 Linen Wrap with", "of 8 Tapers" for non-candle SKUs) need human review, which is why they are flagged.
- The login is a password/session check like the existing Render apps, not Dropbox or Microsoft SSO.
