"""OCR downloaded official location/site-plan images for Jaipur Land Intelligence.
Run from project root: python .\\ocr_location_plans.py
Requires: python -m pip install pillow pytesseract; Tesseract OCR engine installed on Windows.
Writes separate outputs under data/location_research/ocr_v1; never edits DB/app/manifests.
"""
import csv, hashlib, json, re, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DOWNLOADS=ROOT/'data'/'location_research'/'downloads_v2'
OUT=ROOT/'data'/'location_research'/'ocr_v1'
IMG={'.jpg','.jpeg','.png','.tif','.tiff','.bmp','.webp'}
def norm(s): return re.sub(r'\s+',' ',s or '').strip()
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def main():
 if not DOWNLOADS.exists(): print('Missing downloads folder:',DOWNLOADS); sys.exit(1)
 try:
  from PIL import Image, ImageOps, ImageEnhance, ImageFilter
  import pytesseract
 except Exception as e:
  print('Install Python OCR dependencies: python -m pip install pillow pytesseract'); print(e); sys.exit(2)
 if not shutil.which('tesseract'):
  print('Tesseract OCR engine is not on PATH. Install Tesseract for Windows, reopen PowerShell, rerun.'); sys.exit(2)
 OUT.mkdir(parents=True,exist_ok=True)
 manifest=[]
 for mp in DOWNLOADS.rglob('document_manifest*.csv'):
  try:
   with mp.open(encoding='utf-8-sig',newline='') as f:
    for r in csv.DictReader(f): r['_manifest']=str(mp.relative_to(ROOT)); manifest.append(r)
  except Exception as e: print('Manifest read warning:',mp,e)
 def get(r,keys):
  for k,v in r.items():
   if k.startswith('_'): continue
   kk=k.lower().replace(' ','_')
   if any(x in kk for x in keys) and norm(str(v)): return norm(str(v))
  return ''
 lookup={}
 for r in manifest:
  name=get(r,['saved_path','local_path','file_path','output_path','filename','saved_file','download_path'])
  if name: lookup[Path(name).name.lower()]={'auction_id':get(r,['auction_id','auctionid','property_id']),'scheme':get(r,['scheme_name','scheme_label','scheme']),'doc_type':get(r,['document_type','document_name','link_text','title']),'manifest':r.get('_manifest','')}
 files=sorted(p for p in DOWNLOADS.rglob('*') if p.is_file() and p.suffix.lower() in IMG)
 if not files: print('No image files found under',DOWNLOADS); sys.exit(1)
 cache={}; rows=[]
 for i,p in enumerate(files,1):
  try:
   sha=digest(p); m=lookup.get(p.name.lower(),{})
   if sha in cache: txt=cache[sha]; status='reused_identical_image'
   else:
    im=ImageOps.exif_transpose(Image.open(p)).convert('RGB'); w,h=im.size
    scale=min(3,max(1,1800/max(w,h)))
    if scale>1: im=im.resize((int(w*scale),int(h*scale)))
    im=ImageEnhance.Contrast(im).enhance(1.45).filter(ImageFilter.SHARPEN)
    txt=pytesseract.image_to_string(im,config='--oem 3 --psm 11').replace('\x0c','').strip(); cache[sha]=txt
    status='ocr_complete' if txt else 'no_text_detected'
   coords=[]
   for pat in [r'(?<!\d)(?:[0-9]{1,3})\.\d{4,}\s*[,;/]\s*[0-9]{1,3}\.\d{4,}(?!\d)',r'\b\d{1,3}[°º]\s*\d{1,2}[′\']\s*\d{1,2}(?:\.\d+)?["″]?\s*[NSEW]\b',r'\b(?:latitude|longitude|lat\.?|long\.?)\s*[:=]?\s*[-+]?\d{1,3}(?:\.\d+)?']:
    coords += [norm(x) for x in re.findall(pat,txt,re.I)]
   place=re.findall(r'.{0,45}\b(?:road|marg|nagar|vihar|colony|sector|zone|village|gaon|tehsil|khasra|boundary|location plan|site plan|north|scale)\b.{0,65}',txt,re.I)
   plots=re.findall(r'\b(?:plot|shop|khasra|parcel|no\.?|number)\s*[-:#]?\s*[A-Z0-9/-]{1,24}\b',txt,re.I)
   rows.append({'file_path':str(p.relative_to(ROOT)),'filename':p.name,'sha256':sha,'auction_id':m.get('auction_id',''),'scheme_name_from_manifest':m.get('scheme',''),'document_type_from_manifest':m.get('doc_type',''),'manifest_path':m.get('manifest',''),'ocr_status':status,'ocr_characters':len(txt),'coordinate_like_strings':' | '.join(dict.fromkeys(coords)),'place_or_map_text_hits':' | '.join(dict.fromkeys(map(norm,place))),'plot_or_survey_id_hits':' | '.join(dict.fromkeys(map(norm,plots))),'ocr_text':txt})
  except Exception as e: rows.append({'file_path':str(p.relative_to(ROOT)),'filename':p.name,'sha256':'','auction_id':'','scheme_name_from_manifest':'','document_type_from_manifest':'','manifest_path':'','ocr_status':'error','ocr_characters':0,'coordinate_like_strings':'','place_or_map_text_hits':'','plot_or_survey_id_hits':'','ocr_text':f'ERROR: {type(e).__name__}: {e}'})
  if i%10==0 or i==len(files): print(f'[{i}/{len(files)}] processed')
 with (OUT/'ocr_results.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
 candidates=[]
 for r in rows:
  if r['ocr_status'] in ('error','no_text_detected'): continue
  candidates.append({'file_path':r['file_path'],'sha256':r['sha256'],'auction_id':r['auction_id'],'scheme_name_from_manifest':r['scheme_name_from_manifest'],'document_type_from_manifest':r['document_type_from_manifest'],'ocr_text':r['ocr_text'],'coordinate_like_strings':[x for x in r['coordinate_like_strings'].split(' | ') if x],'place_or_map_text_hits':[x for x in r['place_or_map_text_hits'].split(' | ') if x],'plot_or_survey_id_hits':[x for x in r['plot_or_survey_id_hits'].split(' | ') if x],'verification_status':'pending','note':'OCR evidence only; verify against actual plan and authoritative source before using map coordinates.'})
 payload={'generated_at':datetime.now(timezone.utc).isoformat(),'image_count':len(files),'unique_image_contents_ocrd':len(cache),'candidate_count':len(candidates),'coordinate_review_required':True,'candidates':candidates}
 (OUT/'location_evidence_candidates.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
 print('\nOCR complete. Images:',len(files),'Unique image contents:',len(cache),'Candidates:',len(candidates)); print('CSV:',OUT/'ocr_results.csv'); print('JSON:',OUT/'location_evidence_candidates.json'); print('Database, app, original manifests and deployment untouched.')
if __name__=='__main__': main()
