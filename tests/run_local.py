"""Offline test: python tests/run_local.py <input.xlsx> <local photo folder> <out.xlsx>"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import engine
src, folder, out = sys.argv[1:4]
t = time.time()
rep = engine.process(src, out, engine.LocalSource(folder), os.path.join(os.path.dirname(out) or '.', 'crop_cache'),
                     progress=lambda m, d=0, t=0: None)
print('sheets', rep['sheets'], 'rows', rep['rows'], 'placed', rep['placed'], 'secs %.0f' % (time.time() - t))
for k in ('missing_photo', 'fallback_photo', 'scent_review', 'scent_blank', 'photo_failed'):
    print(k, len(rep[k]))
print('new scent skus', rep['new_scent_skus'][:10])
