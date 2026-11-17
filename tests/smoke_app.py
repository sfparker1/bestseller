"""Flask smoke test with a local photo folder standing in for Dropbox (first 20 rows only)."""
import os, sys, time, io
os.environ.update(APP_PASSWORD='pw', DATA_DIR=os.path.join(os.environ.get('TEMP', '/tmp'), 'skuapp'), DROPBOX_ACCESS_TOKEN='x')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import app, engine
src, folder = sys.argv[1:3]
app.dbx = engine.LocalSource(folder); app.dbx.configured = lambda: True
c = app.app.test_client()
assert c.get('/').status_code == 302
assert c.post('/login', data={'password': 'bad'}).status_code == 200
assert c.post('/login', data={'password': 'pw'}).status_code == 302
r = c.post('/run', data={'file': (open(src, 'rb'), 'Sand and Fog 11.16.26.xlsx')}, content_type='multipart/form-data')
jid = r.headers['Location'].split('/')[-1]
for _ in range(900):
    s = c.get(f'/job/{jid}/status').json
    if s['state'] != 'running': break
    time.sleep(1)
print(s)
print(len(c.get(f'/job/{jid}/xlsx').data), c.get(f'/job/{jid}/report.csv').data[:200], c.get(f'/job/{jid}').status_code)
