"""Workbook processing: scents + cropped photos -> openpyxl (no Excel, OS-independent)."""
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.styles import Alignment, PatternFill
from openpyxl.utils.units import pixels_to_EMU

import scents as scents_mod

HEADER_SCAN_ROWS = 15
ROW_HEIGHT_PT = 80
PHOTO_COL_WIDTH = 24
SCENT_COL_WIDTH = 28
GUESS_FILL = PatternFill('solid', start_color='FFF2CC', end_color='FFF2CC')   # light yellow = prototype's Excel color 13431551
PX_W = int(PHOTO_COL_WIDTH * 7 + 5)          # ~173 px
PX_H = int(ROW_HEIGHT_PT * 96 / 72)          # ~106 px


class LocalSource:
    """Photo source reading a local folder (tests / offline use). Same interface as DropboxClient."""
    def __init__(self, folder):
        self.folder = folder
        self.idx = {n.lower(): n for n in os.listdir(folder)}

    def index(self):
        return self.idx

    def find(self, sku):
        s = sku.lower()
        for e in ('.jpg', '.jpeg', '.png'):
            if s + e in self.idx:
                n = self.idx[s + e]
                return os.path.join(self.folder, n), str(os.path.getmtime(os.path.join(self.folder, n))), 'exact'
        pat = re.compile(r'^' + re.escape(s) + r'(?:[ _-]\d{1,2}|[ _-]?bq\d*)\.(?:jpg|jpeg|png)$')
        c = sorted(n for n in self.idx if pat.match(n))
        if c:
            p = os.path.join(self.folder, self.idx[c[0]])
            return p, str(os.path.getmtime(p)), 'fallback'
        return None

    def download(self, path):
        with open(path, 'rb') as f:
            return f.read()


def find_sheets(wb):
    """Sheets with a PHOTO/SCENT/VIN header row. -> [(ws, header_row, col_map)]"""
    found = []
    for ws in wb.worksheets:
        for r in range(1, HEADER_SCAN_ROWS + 1):
            cols = {}
            for c in range(1, 12):
                v = ws.cell(r, c).value
                if isinstance(v, str):
                    k = v.strip().upper()
                    if k in ('PHOTO', 'SCENT', 'DESCRIPTION'):
                        cols[k] = c
                    elif k in ('VIN', 'SKU', 'ITEM', 'ITEM #'):
                        cols['VIN'] = c
            if 'PHOTO' in cols and 'VIN' in cols and 'SCENT' in cols:
                found.append((ws, r, cols))
                break
    return found


def get_crop(sku, source, cache_dir, log):
    """Return (jpeg_bytes|None, kind, note). Cache keyed by SKU; invalidated if source hash changes."""
    hit = source.find(sku)
    if not hit:
        return None, 'missing', 'no exact-name photo'
    path, h, kind = hit
    os.makedirs(cache_dir, exist_ok=True)
    cp, hp = os.path.join(cache_dir, sku + '.jpg'), os.path.join(cache_dir, sku + '.src')
    sig = '%s|%s' % (os.path.basename(path), h)
    if os.path.exists(cp) and os.path.exists(hp):
        try:
            if open(hp).read() == sig:
                return open(cp, 'rb').read(), kind, ('fallback photo: ' + os.path.basename(path)) if kind == 'fallback' else ''
        except OSError:
            pass
    try:
        raw = source.download(path)
        import crop_lib   # lazy: cv2/scipy only load when a photo needs cropping
        out = crop_lib.crop_product(raw)
    except Exception as e:  # network / decode errors shouldn't kill the whole run
        return None, 'failed', 'photo fetch/crop failed: %s' % e
    if not out:
        return None, 'failed', 'crop found no product in photo'
    with open(cp, 'wb') as f:
        f.write(out)
    with open(hp, 'w') as f:
        f.write(sig)
    return out, kind, ('fallback photo: ' + os.path.basename(path)) if kind == 'fallback' else ''


def _anchor(ws_row0, col0, w_px, h_px):
    w_px, h_px = min(w_px, PX_W - 4), min(h_px, PX_H - 4)
    ox, oy = (PX_W - w_px) // 2, (PX_H - h_px) // 2
    a = TwoCellAnchor(editAs='twoCell')
    a._from = AnchorMarker(col=col0, row=ws_row0, colOff=pixels_to_EMU(ox), rowOff=pixels_to_EMU(oy))
    a.to = AnchorMarker(col=col0, row=ws_row0, colOff=pixels_to_EMU(ox + w_px), rowOff=pixels_to_EMU(oy + h_px))
    return a


def process(src_xlsx, out_xlsx, source, cache_dir, scents_path=None, progress=None, workers=int(os.environ.get('CROP_WORKERS', '1')), save_new_scents=False):
    """Fill workbook. Returns report dict."""
    from PIL import Image
    progress = progress or (lambda *a, **k: None)
    scent_map = scents_mod.load_scents(scents_path) if scents_path else scents_mod.load_scents()
    wb = openpyxl.load_workbook(src_xlsx)
    sheets = find_sheets(wb)
    if not sheets:
        raise ValueError('No tab with PHOTO / SCENT / VIN headers found in the first %d rows.' % HEADER_SCAN_ROWS)

    # collect rows
    rows = []   # (ws, r, sku, desc)
    for ws, hr, cols in sheets:
        for r in range(hr + 1, ws.max_row + 1):
            v = ws.cell(r, cols['VIN']).value
            if v is None or not str(v).strip():
                continue
            sku = str(v).strip().upper()
            desc = ws.cell(r, cols['DESCRIPTION']).value if 'DESCRIPTION' in cols else ''
            rows.append((ws, hr, cols, r, sku, desc))
    skus = sorted({x[4] for x in rows})
    progress('Looking up %d unique SKUs in Dropbox...' % len(skus), 0, len(skus))
    source.index()

    crops, done = {}, [0]

    def work(sku):
        res = get_crop(sku, source, cache_dir, None)
        done[0] += 1
        progress('Fetching / cropping photos (%d of %d)' % (done[0], len(skus)), done[0], len(skus))
        return sku, res
    with ThreadPoolExecutor(workers) as ex:
        for sku, res in ex.map(work, skus):
            crops[sku] = res

    report = {'sheets': [s[0].title.strip() for s in sheets], 'rows': len(rows), 'placed': 0,
              'missing_photo': [], 'fallback_photo': [], 'scent_review': [], 'scent_blank': [], 'photo_failed': []}
    new_scents, seen_new = [], set()

    # clear old photos in column A of processed sheets (re-runs on an already-photo'd file)
    for ws, hr, cols in sheets:
        ws._images = [im for im in ws._images if not (hasattr(im.anchor, '_from') and im.anchor._from.col == cols['PHOTO'] - 1 and im.anchor._from.row >= hr)]
        ws.column_dimensions[openpyxl.utils.get_column_letter(cols['PHOTO'])].width = PHOTO_COL_WIDTH
        ws.column_dimensions[openpyxl.utils.get_column_letter(cols['SCENT'])].width = SCENT_COL_WIDTH

    maxdesc = {}
    for ws, hr, cols, r, sku, desc in rows:
        tab = ws.title.strip()
        # --- scent
        sc = ws.cell(r, cols['SCENT'])
        existing = sc.value if sc.value is not None and str(sc.value).strip() else None
        if existing is None:
            if sku in scent_map and scent_map[sku][0]:
                scent, guess = scent_map[sku]
            else:
                scent, guess = scents_mod.parse_description(desc), True
                if sku not in seen_new:
                    seen_new.add(sku)
                    new_scents.append((sku, scent, True))
            if scent:
                sc.value = scent
                sc.alignment = Alignment(vertical='center', wrap_text=False)
                if guess:
                    sc.fill = GUESS_FILL
                    report['scent_review'].append((tab, r, sku, scent, str(desc or '')))
            else:
                report['scent_blank'].append((tab, r, sku, '', str(desc or '')))
        # --- photo
        data, kind, note = crops[sku]
        if data is None:
            (report['photo_failed'] if kind == 'failed' else report['missing_photo']).append((tab, r, sku, note))
        else:
            if kind == 'fallback':
                report['fallback_photo'].append((tab, r, sku, note))
            im = XLImage(io.BytesIO(data))
            w, h = im.width, im.height
            s = min((PX_W - 4) / w, (PX_H - 4) / h)
            im.anchor = _anchor(r - 1, cols['PHOTO'] - 1, int(w * s), int(h * s))
            ws.add_image(im)
            ws.row_dimensions[r].height = ROW_HEIGHT_PT
            report['placed'] += 1
        if 'DESCRIPTION' in cols and desc:
            maxdesc[(ws.title, cols['DESCRIPTION'])] = max(maxdesc.get((ws.title, cols['DESCRIPTION']), 0), len(str(desc)))

    for ws, hr, cols in sheets:
        if 'DESCRIPTION' in cols:
            n = maxdesc.get((ws.title, cols['DESCRIPTION']), 10)
            letter = openpyxl.utils.get_column_letter(cols['DESCRIPTION'])
            ws.column_dimensions[letter].width = max(ws.column_dimensions[letter].width or 0, min(60, n * 1.15 + 2))

    wb.save(out_xlsx)
    if save_new_scents and new_scents:
        scents_mod.append_new(new_scents, scents_path)
    report['new_scent_skus'] = new_scents
    return report


def report_csv(report):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['issue', 'tab', 'row', 'sku', 'scent / photo', 'description or note'])
    for t, r, s, n in report['missing_photo']:
        w.writerow(['NO EXACT PHOTO', t, r, s, '', n])
    for t, r, s, n in report['photo_failed']:
        w.writerow(['PHOTO FAILED', t, r, s, '', n])
    for t, r, s, n in report['fallback_photo']:
        w.writerow(['FALLBACK PHOTO (verify)', t, r, s, '', n])
    for t, r, s, sc, d in report['scent_review']:
        w.writerow(['SCENT GUESSED (verify)', t, r, s, sc, d])
    for t, r, s, sc, d in report['scent_blank']:
        w.writerow(['SCENT BLANK', t, r, s, '', d])
    return buf.getvalue()

