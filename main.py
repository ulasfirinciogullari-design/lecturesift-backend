import os,uuid,shutil,subprocess,json,traceback,threading,time,re
from pathlib import Path
import cv2,numpy as np
from fastapi import FastAPI,UploadFile,File,Form,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI

WORK=Path('/tmp/lecturesift');WORK.mkdir(parents=True,exist_ok=True)
KEY=os.environ.get('OPENAI_API_KEY','');client=OpenAI(api_key=KEY) if KEY else None
app=FastAPI(title='LectureSift Backend V3.1')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['*'],allow_headers=['*'])
JOBS={};LOCK=threading.Lock()
def js(j,**kw):
 with LOCK:JOBS.setdefault(j,{}).update(kw,updated=time.time())
@app.get('/')
def root():return {'ok':True,'service':'LectureSift Backend V3.1'}
@app.get('/health')
def health():return {'ok':True,'openai_key':bool(KEY),'slide_engine':'v3.1','study_pack':True,'async_jobs':True}
@app.get('/jobs/{j}')
def gj(j:str):
 d=JOBS.get(j)
 if not d:raise HTTPException(404,'Job not found')
 return {k:v for k,v in d.items() if k not in {'job_dir','result_path'}}
@app.get('/jobs/{j}/result')
def gr(j:str):
 d=JOBS.get(j)
 if not d:raise HTTPException(404,'Job not found')
 if d.get('status')!='done':raise HTTPException(409,'Job not finished')
 return json.loads((Path(d['job_dir'])/'result.json').read_text(encoding='utf-8'))
@app.get('/jobs/{j}/slide/{fn}')
def gs(j:str,fn:str):
 d=JOBS.get(j)
 if not d:raise HTTPException(404,'Job not found')
 if '/' in fn or '\\' in fn or '..' in fn:raise HTTPException(400,'Invalid filename')
 p=Path(d['job_dir'])/'slides'/fn
 if not p.exists():raise HTTPException(404,'Slide not found')
 return FileResponse(str(p),media_type='image/jpeg')
@app.get('/jobs/{j}/download')
def gd(j:str):
 d=JOBS.get(j)
 if not d:raise HTTPException(404,'Job not found')
 if d.get('status')!='done':raise HTTPException(409,'Job not finished')
 return FileResponse(d['result_path'],media_type='application/zip',filename='LectureSift_Study_Pack.zip')

def run(cmd):
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 if p.returncode!=0:raise RuntimeError(p.stderr[-8000:])
 return p

def hasaudio(p):
 r=subprocess.run(['ffprobe','-v','error','-select_streams','a','-show_entries','stream=index','-of','csv=p=0',str(p)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 return bool(r.stdout.strip())
def dh(f):
 g=cv2.resize(cv2.cvtColor(f,cv2.COLOR_BGR2GRAY),(17,16),interpolation=cv2.INTER_AREA);return (g[:,1:]>g[:,:-1]).flatten()
def ham(a,b):return float(np.count_nonzero(a!=b))/len(a)
def slide_score(f):
 h=max(1,int(f.shape[0]*320/f.shape[1]));s=cv2.resize(f,(320,h));g=cv2.cvtColor(s,cv2.COLOR_BGR2GRAY);e=cv2.Canny(g,70,160);edge=np.count_nonzero(e)/e.size;lap=cv2.Laplacian(g,cv2.CV_32F);flat=np.mean(np.abs(lap)<8);hsv=cv2.cvtColor(s,cv2.COLOR_BGR2HSV);sat=np.mean(hsv[:,:,1])/255.0
 score=(2 if edge>.035 else 0)+(1 if edge>.06 else 0)+(2 if flat>.47 else 0)+(1 if sat<.55 else 0)
 return score,{'edge_density':round(float(edge),4),'flat_ratio':round(float(flat),4),'saturation':round(float(sat),4)}
def slides(video,out,j):
 out.mkdir(parents=True,exist_ok=True);cap=cv2.VideoCapture(str(video))
 if not cap.isOpened():raise RuntimeError('Video could not be opened')
 fps=cap.get(cv2.CAP_PROP_FPS) or 25;total=cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0;dur=total/fps if fps else 0;step=1.5 if dur<=600 else (2.5 if dur<=3600 else 4.0);prev=None;c=[];t=0.0;last=-999;i=0;n=max(1,int(dur/step)+1)
 while t<=dur:
  cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read()
  if ok and f is not None:
   hh=max(1,int(f.shape[0]*160/f.shape[1]));g=cv2.cvtColor(cv2.resize(f,(160,hh)),cv2.COLOR_BGR2GRAY)
   if prev is None:c.append((t,f.copy()));last=t
   else:
    diff=float(np.mean(cv2.absdiff(g,prev)))/255.0
    if diff>.085 or (diff<.022 and t-last>=10):c.append((t,f.copy()));last=t
   prev=g
  i+=1
  if i%8==0:js(j,percent=18+int(18*i/n),stage='scene_scan')
  t+=step
 cap.release();js(j,percent=38,stage='slide_detection');flt=[]
 for t,f in c:
  sc,m=slide_score(f)
  if sc>=5:flt.append({'time':t,'frame':f,'hash':dh(f),'score':sc,'metrics':m})
 groups=[]
 for it in flt:
  if not groups:groups.append([it]);continue
  p=groups[-1][-1]
  if it['time']-p['time']<=14 and ham(it['hash'],p['hash'])<.20:groups[-1].append(it)
  else:groups.append([it])
 reps=[g[-1] for g in groups];uniq=[]
 for it in reps:
  if not any(ham(it['hash'],o['hash'])<.055 for o in uniq):uniq.append(it)
 man=[]
 for k,it in enumerate(uniq,1):
  sec=it['time'];fn=f'slide_{k:03d}_{int(sec//60):02d}m{int(sec%60):02d}s.jpg';cv2.imwrite(str(out/fn),it['frame']);man.append({'file':fn,'second':round(sec,1),'slide_score':it['score'],**it['metrics']})
 return man,{'duration_seconds':round(dur,1),'fast_candidates':len(c),'presentation_candidates':len(flt),'final_unique_slides':len(man)}
def audio(video,job):
 p=job/'audio.mp3';run(['ffmpeg','-y','-i',str(video),'-vn','-ac','1','-ar','16000','-b:a','32k',str(p)]);return p
def transcribe(p,lang):
 if not client:raise RuntimeError('OPENAI_API_KEY is not configured')
 with open(p,'rb') as f:
  kw={'model':'gpt-4o-mini-transcribe','file':f}
  if lang and lang!='auto':kw['language']=lang
  r=client.audio.transcriptions.create(**kw)
 return getattr(r,'text',str(r)).strip()
LANG={'tr':'Turkish','en':'English','de':'German','fr':'French','es':'Spanish','it':'Italian','pt':'Portuguese','ru':'Russian','ar':'Arabic','zh':'Chinese','ja':'Japanese','ko':'Korean','hi':'Hindi'}
def pack(txt,lang,style,qc,fc):
 if not client:raise RuntimeError('OPENAI_API_KEY is not configured')
 sm={'short':'very concise','standard':'balanced and structured','detailed':'detailed and explanatory','exam':'exam-focused'}.get(style,'balanced and structured')
 prompt=f'''Use ONLY this lecture transcript. Output language: {LANG.get(lang,'English')}. Style: {sm}. Return valid JSON only with keys title, summary, key_points, notes, quiz, flashcards. notes items: heading,content,bullets. quiz items: question,options(4),answer_index,explanation. flashcards: front,back. Make up to {qc} quiz questions and {fc} flashcards. Transcript: {txt[:90000]}'''
 r=client.chat.completions.create(model='gpt-4o-mini',messages=[{'role':'user','content':prompt}],response_format={'type':'json_object'},temperature=.2)
 return json.loads(r.choices[0].message.content)
def process(j,video,opt):
 job=Path(JOBS[j]['job_dir']);sd=job/'slides';st=time.time()
 try:
  js(j,status='working',percent=12,stage='preparing',started=st);sl,diag=slides(video,sd,j);js(j,percent=50,stage='audio')
  txt=''
  if hasaudio(video):txt=transcribe(audio(video,job),opt['source_language'])
  (job/'transcript.txt').write_text(txt or 'No audio track detected.',encoding='utf-8');js(j,percent=70,stage='study_notes')
  pk=pack(txt,opt['output_language'],opt['summary_style'],opt['quiz_count'],opt['flashcard_count']) if txt.strip() else {'title':'LectureSift','summary':'No audio track detected.','key_points':[],'notes':[],'quiz':[],'flashcards':[]}
  js(j,percent=88,stage='packaging');res={'job_id':j,'options':opt,'slides':sl,'diagnostics':diag,'transcript':txt,**pk};(job/'result.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
  out=job/'pack';out.mkdir();
  for fn,data in [('summary.txt',pk.get('summary','')),('notes.txt',json.dumps(pk.get('notes',[]),ensure_ascii=False,indent=2)),('quiz.json',json.dumps(pk.get('quiz',[]),ensure_ascii=False,indent=2)),('flashcards.json',json.dumps(pk.get('flashcards',[]),ensure_ascii=False,indent=2)),('slides.json',json.dumps(sl,ensure_ascii=False,indent=2)),('result.json',json.dumps(res,ensure_ascii=False,indent=2)),('transcript.txt',txt)]: (out/fn).write_text(data,encoding='utf-8')
  if sd.exists():shutil.copytree(sd,out/'slides')
  zb=job/'LectureSift_Study_Pack';shutil.make_archive(str(zb),'zip',root_dir=out);js(j,status='done',percent=100,stage='done',elapsed_seconds=round(time.time()-st,1),result_path=str(zb)+'.zip')
 except Exception as e:traceback.print_exc();js(j,status='error',percent=0,stage='error',error=str(e)[:1000])
def opts(sl,ol,ss,qc,fc):return {'source_language':sl,'output_language':ol,'summary_style':ss,'quiz_count':max(3,min(int(qc),30)),'flashcard_count':max(5,min(int(fc),60))}
@app.post('/jobs')
async def create(file:UploadFile=File(...),source_language:str=Form('auto'),output_language:str=Form('tr'),summary_style:str=Form('standard'),quiz_count:int=Form(10),flashcard_count:int=Form(20)):
 ext=Path(file.filename or 'video.mp4').suffix.lower();j=str(uuid.uuid4());job=WORK/j;job.mkdir(parents=True,exist_ok=True);video=job/('input'+ext)
 with open(video,'wb') as o:
  while True:
   c=await file.read(1024*1024)
   if not c:break
   o.write(c)
 op=opts(source_language,output_language,summary_style,quiz_count,flashcard_count);JOBS[j]={'job_id':j,'status':'queued','percent':10,'stage':'queued','created':time.time(),'job_dir':str(job),'options':op};threading.Thread(target=process,args=(j,video,op),daemon=True).start();return {'job_id':j,'status':'queued'}
def dlurl(url,job):
 if not url.startswith(('http://','https://')):raise RuntimeError('Invalid URL')
 p=subprocess.run(['yt-dlp','--no-playlist','--merge-output-format','mp4','-f','bv*+ba/b','-o',str(job/'source.%(ext)s'),url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 if p.returncode!=0:raise RuntimeError(p.stderr[-8000:])
 fs=list(job.glob('source.*'))
 if not fs:raise RuntimeError('Video could not be downloaded')
 fs.sort(key=lambda x:(x.suffix.lower()!='.mp4',-x.stat().st_size));return fs[0]
@app.post('/jobs/url')
def create_url(video_url:str=Form(...),source_language:str=Form('auto'),output_language:str=Form('tr'),summary_style:str=Form('standard'),quiz_count:int=Form(10),flashcard_count:int=Form(20)):
 j=str(uuid.uuid4());job=WORK/j;job.mkdir(parents=True,exist_ok=True);op=opts(source_language,output_language,summary_style,quiz_count,flashcard_count);JOBS[j]={'job_id':j,'status':'working','percent':3,'stage':'downloading','created':time.time(),'job_dir':str(job),'options':op}
 def w():
  try:video=dlurl(video_url,job);js(j,percent=10,stage='queued');process(j,video,op)
  except Exception as e:traceback.print_exc();js(j,status='error',percent=0,stage='error',error=str(e)[:1000])
 threading.Thread(target=w,daemon=True).start();return {'job_id':j,'status':'working'}
