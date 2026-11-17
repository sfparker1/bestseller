"""Scent lookup from scents.csv (editable) + best-effort parse of descriptions for new SKUs."""
import csv
import os
import re

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scents.csv')


def csv_path():
    return os.environ.get('SCENTS_CSV') or DEFAULT_CSV

# words that describe size / vessel / color / pet line, not scent
NOISE = {
    'RED', 'GREEN', 'WHITE', 'BLK', 'BLACK', 'BROWN', 'BLUE', 'GREY', 'GRAY', 'GOLD', 'SILVER_', 'PINK', 'CREAM',
    'IVORY', 'TAN', 'DOG', 'CAT', 'PAW', 'PAWS', 'TOP', 'LID', 'JAR', 'TIN', 'CANDLE', 'VESSEL', 'GLASS', 'CERAMIC',
    'AMBER_', 'CLEAR', 'FROSTED', 'MATTE', 'SET', 'BQ', 'WICK', 'CW', 'TUMBLER', 'BOX', 'BOXED', 'GIFT', 'XL', 'LG',
    'SM', 'MED', 'MINI', 'SCENTED', 'SOY', 'WAX',
}
TYPOS = {
    'FRAZIER': 'Frasier', 'FRASER': 'Frasier', 'FRAZER': 'Frasier', 'LAV': 'Lavender', 'SILVERF': 'Silver Fir',
    'SILVERFIR': 'Silver Fir', 'VAN': 'Vanilla', 'CINN': 'Cinnamon', 'CRAN': 'Cranberry', 'MAGNOL': 'Magnolia',
    'SANDALWOO': 'Sandalwood', 'EVERGREE': 'Evergreen', 'GINGERBREA': 'Gingerbread', 'TEAKWOO': 'Teakwood',
    'MAHOGAN': 'Mahogany', 'CASHMER': 'Cashmere', 'SNICKERDOODL': 'Snickerdoodle', 'EUCALYPT': 'Eucalyptus',
    'EUCALYPTU': 'Eucalyptus', 'LAVENDE': 'Lavender', 'CEDARWOO': 'Cedarwood', 'JUNIPE': 'Juniper',
}
SMALL = {'and', 'of', 'the', 'with', '&'}


def load_scents(path=None):
    path = path or csv_path()
    """-> {SKU: (scent, guess_flag)}"""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            sku = (row.get('sku') or '').strip().upper()
            if sku:
                out[sku] = ((row.get('scent') or '').strip(), (row.get('guess') or '').strip().upper() in ('Y', 'YES', '1', 'TRUE', '?'))
    return out


def parse_description(desc):
    """Best-effort scent from a (25-char truncated) description. Returns '' if nothing usable."""
    if not desc:
        return ''
    t = re.sub(r'\b\d+(\.\d+)?\s*(OZ|Z)\b', ' ', str(desc).upper())
    t = t.replace('&', ' AND ')
    words = []
    for w in re.split(r'[\s/,_-]+', t):
        w = w.strip('.')
        if not w or w in NOISE:
            continue
        words.append(w)
    # first word of "BLK/DOG ..." style tokens that were noise is gone; fix typos
    out = []
    for w in words:
        out.append(TYPOS.get(w, w.title()))
    s = ' '.join(out)
    s = re.sub(r'\bAnd\b', 'and', s)
    s = re.sub(r'\b(Of|The|With)\b', lambda m: m.group(1).lower(), s)
    s = s.strip()
    if s.lower().startswith('and '):
        s = s[4:]
    if s.lower().endswith(' and'):
        s = s[:-4]
    return s.strip()


def append_new(rows, path=None):
    path = path or csv_path()
    """Append [(sku, scent, guess)] to scents.csv (only when the file is writable)."""
    new = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['sku', 'scent', 'guess'])
        for sku, scent, guess in rows:
            w.writerow([sku, scent, 'Y' if guess else ''])
