"""Minimal read-only Dropbox API client (no SDK; only `requests`).

Auth (first match wins):
  DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY + DROPBOX_APP_SECRET   (recommended; never expires)
  DROPBOX_ACCESS_TOKEN                                           (simple; short-lived unless a legacy long-lived token)
Scopes needed: files.metadata.read, files.content.read.
"""
import os
import re
import threading
import time

import requests

API = 'https://api.dropboxapi.com/2'
CONTENT = 'https://content.dropboxapi.com/2'
EXTS = ('.jpg', '.jpeg', '.png')


class DropboxClient:
    def __init__(self, folder=None):
        self.folder = folder if folder is not None else os.environ.get('DROPBOX_FOLDER', '/S+F/TOP Pictures')
        self._tok = None
        self._tok_exp = 0
        self._lock = threading.Lock()
        self._index = None
        self._index_ts = 0
        self.index_ttl = int(os.environ.get('INDEX_TTL_SECONDS', '900'))

    # ---- auth
    def _token(self):
        rt = os.environ.get('DROPBOX_REFRESH_TOKEN')
        if rt:
            with self._lock:
                if not self._tok or time.time() > self._tok_exp - 60:
                    r = requests.post('https://api.dropboxapi.com/oauth2/token', data={'grant_type': 'refresh_token', 'refresh_token': rt},
                                      auth=(os.environ['DROPBOX_APP_KEY'], os.environ['DROPBOX_APP_SECRET']), timeout=30)
                    r.raise_for_status()
                    j = r.json()
                    self._tok, self._tok_exp = j['access_token'], time.time() + int(j.get('expires_in', 14400))
                return self._tok
        t = os.environ.get('DROPBOX_ACCESS_TOKEN')
        if not t:
            raise RuntimeError('Dropbox is not configured: set DROPBOX_ACCESS_TOKEN (or DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY + DROPBOX_APP_SECRET).')
        return t

    def configured(self):
        return bool(os.environ.get('DROPBOX_ACCESS_TOKEN') or os.environ.get('DROPBOX_REFRESH_TOKEN'))

    def _hdr(self, extra=None):
        h = {'Authorization': 'Bearer ' + self._token()}
        h.update(extra or {})
        return h

    # ---- folder index: one paginated listing (metadata only, no file bytes)
    def index(self, force=False):
        """-> {lowercase filename: (path_lower, content_hash)}"""
        with self._lock:
            if self._index is not None and not force and time.time() - self._index_ts < self.index_ttl:
                return self._index
        idx = {}
        r = requests.post(API + '/files/list_folder', headers=self._hdr({'Content-Type': 'application/json'}),
                          json={'path': self.folder, 'recursive': False, 'limit': 2000}, timeout=60)
        while True:
            if r.status_code != 200:
                raise RuntimeError('Dropbox list_folder failed: %s %s' % (r.status_code, r.text[:300]))
            j = r.json()
            for e in j['entries']:
                if e.get('.tag') == 'file' and e['name'].lower().endswith(EXTS):
                    idx[e['name'].lower()] = (e['path_lower'], e.get('content_hash', ''))
            if not j.get('has_more'):
                break
            r = requests.post(API + '/files/list_folder/continue', headers=self._hdr({'Content-Type': 'application/json'}),
                              json={'cursor': j['cursor']}, timeout=60)
        with self._lock:
            self._index, self._index_ts = idx, time.time()
        return idx

    def find(self, sku):
        """-> (path, content_hash, kind) kind in {'exact','fallback'} or None"""
        idx = self.index()
        s = sku.lower()
        for e in EXTS:
            if s + e in idx:
                p, h = idx[s + e]
                return p, h, 'exact'
        # flagged fallback: SKU-1, SKU_2, SKUBQ, SKU BQ ...
        pat = re.compile(r'^' + re.escape(s) + r'(?:[ _-]\d{1,2}|[ _-]?bq\d*)\.(?:jpg|jpeg|png)$')
        cands = sorted(n for n in idx if pat.match(n))
        if cands:
            p, h = idx[cands[0]]
            return p, h, 'fallback'
        return None

    def download(self, path):
        import json
        for attempt in range(3):
            r = requests.post(CONTENT + '/files/download', timeout=120,
                              headers=self._hdr({'Dropbox-API-Arg': json.dumps({'path': path})}))
            if r.status_code == 200:
                return r.content
            if r.status_code == 429:
                time.sleep(int(r.headers.get('Retry-After', 2)))
                continue
            raise RuntimeError('Dropbox download failed for %s: %s' % (path, r.status_code))
        raise RuntimeError('Dropbox rate-limited: ' + path)
