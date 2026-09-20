#!/usr/bin/env python3
import argparse
import fcntl
import json
import re
import time
import unicodedata
from pathlib import Path
import requests
from jellyfin_media_verify import load_config, jellyfin_headers, verify_path

BASE=Path('/home/sibilla-cumana/jellyfin-novita-agent')
MANIFEST=BASE/'data/movie_quality_replacements.json'
LOCK=BASE/'data/movie_quality_replacement_watcher.lock'
LOG=BASE/'out/movie_quality_replacement_watcher.log'
VIDEO_EXTS={'.mkv','.mp4','.avi','.m4v','.mov','.webm','.ts'}
ROOTS=(Path('/mnt/origin_media'),Path('/mnt/media_cachefirst'))

def norm(value):
 s=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().lower()
 return ' '.join(re.findall(r'[a-z0-9]+',s))

def save_manifest(data):
 tmp=MANIFEST.with_suffix('.json.tmp')
 tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
 tmp.replace(MANIFEST)
def actual_path(path):
 p=Path(str(path or ''))
 try:
  if p.is_file(): return p
 except OSError: pass
 if str(p).startswith('/media/'):
  rel=Path(str(p)[len('/media/'):])
  for root in ROOTS:
   q=root/rel
   try:
    if q.is_file(): return q
   except OSError: pass
 return None

def same_old(candidate,old_path):
 c=str(candidate); o=str(old_path)
 if c==o: return True
 if o.startswith('/mnt/origin_media/'):
  return c==o.replace('/mnt/origin_media/','/mnt/media_cachefirst/',1)
 if o.startswith('/mnt/media_cachefirst/'):
  return c==o.replace('/mnt/media_cachefirst/','/mnt/origin_media/',1)
 return False

def movie_paths(item):
 vals=[item.get('Path')]
 vals += [x.get('Path') for x in (item.get('MediaSources') or []) if isinstance(x,dict)]
 return [x for x in dict.fromkeys(vals) if x]
def jellyfin_movies(cfg):
 base=cfg['jellyfin_url'].rstrip('/')
 params={'Recursive':'true','IncludeItemTypes':'Movie','Fields':'Path,MediaSources,ProductionYear','Limit':'10000'}
 r=requests.get(base+'/Items',params=params,headers=jellyfin_headers(cfg),timeout=20)
 r.raise_for_status()
 return r.json().get('Items',[])

def find_replacement(rec,movies):
 wanted=norm(rec.get('title')); year=rec.get('year'); old=rec.get('old_path')
 found=[]
 for item in movies:
  if norm(item.get('Name'))!=wanted: continue
  iy=item.get('ProductionYear')
  if year and iy and int(year)!=int(iy): continue
  for raw in movie_paths(item):
   p=actual_path(raw)
   if not p or same_old(p,old) or str(p).lower().endswith('.strm'): continue
   verify=verify_path(str(p),sample_seconds=6)
   if not verify.get('ok'): continue
   found.append((p.stat().st_size,p,verify))
 if not found: return None
 found.sort(reverse=True,key=lambda x:x[0])
 return found[0][1],found[0][2]

def delete_old_copies(old_path):
 p=Path(old_path); targets={p}
 for src,dst in (('/mnt/origin_media/','/mnt/media_cachefirst/'),('/mnt/media_cachefirst/','/mnt/origin_media/')):
  if str(p).startswith(src): targets.add(Path(str(p).replace(src,dst,1)))
 deleted=[]
 for t in targets:
  if t.suffix.lower() not in VIDEO_EXTS: continue
  try:
   if t.exists(): t.unlink(); deleted.append(str(t))
  except OSError: pass
 return deleted
def refresh_jellyfin(cfg):
 try:
  r=requests.post(cfg['jellyfin_url'].rstrip('/')+'/Library/Refresh',headers=jellyfin_headers(cfg),timeout=10)
  return r.status_code
 except Exception:
  return None

def run_once(cfg):
 data=json.loads(MANIFEST.read_text(encoding='utf-8'))
 movies=jellyfin_movies(cfg); changed=False; completed=[]
 for rec in data.get('items',[]):
  if rec.get('status')!='pending': continue
  found=find_replacement(rec,movies)
  if not found: continue
  replacement,verify=found
  deleted=delete_old_copies(rec['old_path'])
  rec.update({'status':'replaced','replacement_path':str(replacement),'replacement_verify':verify,'deleted_paths':deleted,'replaced_at':time.time()})
  completed.append(rec['title']); changed=True
 if changed:
  save_manifest(data); refresh_jellyfin(cfg)
 LOG.parent.mkdir(parents=True,exist_ok=True)
 with LOG.open('a',encoding='utf-8') as f:
  f.write(json.dumps({'ts':time.time(),'completed':completed,'pending':sum(1 for x in data.get('items',[]) if x.get('status')=='pending')},ensure_ascii=False)+'\n')
 return completed, sum(1 for x in data.get('items',[]) if x.get('status')=='pending')

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--once',action='store_true'); ap.add_argument('--interval',type=int,default=300); args=ap.parse_args()
 LOCK.parent.mkdir(parents=True,exist_ok=True)
 lock=LOCK.open('w'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 cfg=load_config(str(BASE/'config.json'))
 while True:
  completed,pending=run_once(cfg)
  print(json.dumps({'completed':completed,'pending':pending},ensure_ascii=False),flush=True)
  if args.once or pending==0: return 0
  time.sleep(max(60,args.interval))

if __name__=='__main__':
 raise SystemExit(main())