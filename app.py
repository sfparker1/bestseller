"""Sand + Fog weekly selling-report photo/scent filler (Flask). Run: gunicorn app:app"""
import hmac
import os
import secrets
import tempfile
import threading
import time
import uuid
from functools import wraps

from flask import (Flask, Response, abort, redirect, render_template, request, send_file, session, url_for)

import engine
from dropbox_client import DropboxClient

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)   # set SECRET_KEY so logins survive restarts
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER')), MAX_CONTENT_LENGTH=40 * 1024 * 1024)

DATA_DIR = os.environ.get('DATA_DIR') or os.path.join(tempfile.gettempdir(), 'sku-photo-data')
CACHE_DIR = os.path.join(DATA_DIR, 'crops')
JOBS_DIR = os.path.join(DATA_DIR, 'jobs')
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(JOBS_DIR, exist_ok=True)
# scents.csv: ship the repo copy; on a persistent disk keep an editable copy so team edits survive deploys
os.environ.setdefault('SCENTS_CSV', os.path.join(DATA_DIR, 'scents.csv'))
_REPO_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scents.csv')
if not os.path.exists(os.environ['SCENTS_CSV']) and os.path.exists(_REPO_CSV):
    import shutil
    shutil.copyfile(_REPO_CSV, os.environ['SCENTS_CSV'])

dbx = DropboxClient()
JOBS = {}


# ---------- auth: same model as packing-list-acumatica (APP_PASSWORD or per-user APP_USERS="a:pw1,b:pw2")
def _users():
    u = {}
    for pair in (os.environ.get('APP_USERS') or '').split(','):
        if ':' in pair:
            n, p = pair.split(':', 1)
            u[n.strip().lower()] = p.strip()
    return u


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get('user'):
            return redirect(url_for('login', next=request.path))
        return f(*a, **k)
    return w


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not os.environ.get('APP_PASSWORD') and not _users():
        return Response('Server misconfigured: set APP_PASSWORD or APP_USERS.', 500)
    err = ''
    if request.method == 'POST':
        name = (request.form.get('user') or '').strip().lower()
        pw = request.form.get('password') or ''
        users = _users()
        ok = False
        if users and name in users:
            ok = hmac.compare_digest(users[name].encode(), pw.encode())
        elif os.environ.get('APP_PASSWORD'):
            ok = hmac.compare_digest(os.environ['APP_PASSWORD'].encode(), pw.encode())
        if ok:
            session['user'] = name or 'staff'
            session.permanent = True
            return redirect('/')
        time.sleep(1)
        err = 'Wrong user or password.'
    return render_template('login.html', err=err, per_user=bool(_users()))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------- jobs
def _run_job(jid, src, out, name):
    j = JOBS[jid]

    def prog(msg, done=0, total=0):
        j.update(msg=msg, done=done, total=total)
    try:
        rep = engine.process(src, out, dbx, CACHE_DIR, progress=prog, save_new_scents=True)
        rcsv = os.path.join(JOBS_DIR, jid + '_report.csv')
        with open(rcsv, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(engine.report_csv(rep))
        j.update(state='done', report=rep, msg='Done')
    except Exception as e:
        j.update(state='error', msg=str(e))


@app.route('/')
@login_required
def index():
    return render_template('index.html', user=session['user'], dbx_ok=dbx.configured(), folder=dbx.folder)


@app.route('/run', methods=['POST'])
@login_required
def run():
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.xlsx'):
        return render_template('index.html', user=session['user'], dbx_ok=dbx.configured(), folder=dbx.folder,
                               err='Please choose an .xlsx workbook.'), 400
    if not dbx.configured():
        return render_template('index.html', user=session['user'], dbx_ok=False, folder=dbx.folder,
                               err='Dropbox token not configured on the server.'), 500
    jid = uuid.uuid4().hex[:12]
    base = os.path.splitext(os.path.basename(f.filename))[0]
    src = os.path.join(JOBS_DIR, jid + '_in.xlsx')
    out = os.path.join(JOBS_DIR, jid + '_out.xlsx')
    f.save(src)
    JOBS[jid] = dict(state='running', msg='Starting...', done=0, total=0, name=base, user=session['user'], started=time.time())
    threading.Thread(target=_run_job, args=(jid, src, out, base), daemon=True).start()
    _cleanup()
    return redirect(url_for('job', jid=jid))


@app.route('/job/<jid>')
@login_required
def job(jid):
    j = JOBS.get(jid) or abort(404)
    return render_template('job.html', jid=jid, j=j)


@app.route('/job/<jid>/status')
@login_required
def status(jid):
    j = JOBS.get(jid) or abort(404)
    return {k: j.get(k) for k in ('state', 'msg', 'done', 'total')}


@app.route('/job/<jid>/xlsx')
@login_required
def dl_xlsx(jid):
    j = JOBS.get(jid) or abort(404)
    if j['state'] != 'done':
        abort(404)
    return send_file(os.path.join(JOBS_DIR, jid + '_out.xlsx'), as_attachment=True, download_name=j['name'] + ' - WITH PHOTOS.xlsx')


@app.route('/job/<jid>/report.csv')
@login_required
def dl_report(jid):
    j = JOBS.get(jid) or abort(404)
    if j['state'] != 'done':
        abort(404)
    return send_file(os.path.join(JOBS_DIR, jid + '_report.csv'), as_attachment=True, download_name=j['name'] + ' - photo scent report.csv')


@app.route('/scents')
@login_required
def scents_download():
    """Download the live scents.csv (edit in Excel, then upload below)."""
    return send_file(os.environ['SCENTS_CSV'], as_attachment=True, download_name='scents.csv')


@app.route('/scents', methods=['POST'])
@login_required
def scents_upload():
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.csv'):
        abort(400)
    raw = f.read().decode('utf-8-sig')
    if not raw.lower().startswith('sku,'):
        return 'CSV must start with header: sku,scent,guess', 400
    tmp = os.environ['SCENTS_CSV'] + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as o:
        o.write(raw)
    os.replace(tmp, os.environ['SCENTS_CSV'])
    return redirect('/')


@app.route('/refresh-index', methods=['POST'])
@login_required
def refresh_index():
    dbx.index(force=True)
    return redirect('/')


def _cleanup(max_age=24 * 3600):
    now = time.time()
    for n in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, n)
        try:
            if now - os.path.getmtime(p) > max_age:
                os.remove(p)
        except OSError:
            pass


@app.route('/healthz')
def healthz():
    return 'ok'


if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', 8000)), debug=False)
