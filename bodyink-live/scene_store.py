"""Atomic artwork checkpoints, outside the inference thread."""
import copy,json,os,shutil,threading
from pathlib import Path

class SceneStore:
 def __init__(self,path):
  self.path=Path(path);self.backup=self.path.with_suffix(self.path.suffix+'.previous');self.lock=threading.RLock()

 def save(self,scene):
  payload=json.dumps(scene,ensure_ascii=False,allow_nan=False).encode('utf-8')
  with self.lock:
   self.path.parent.mkdir(parents=True,exist_ok=True)
   temporary=self.path.with_suffix(self.path.suffix+'.pending')
   with temporary.open('wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
   if self.path.exists():
    # Never overwrite the last valid backup with an already corrupt current.
    try:json.loads(self.path.read_text(encoding='utf-8'))
    except (ValueError,OSError):pass
    else:
     old=self.backup.with_suffix(self.backup.suffix+'.pending')
     shutil.copyfile(self.path,old);os.replace(old,self.backup)
   os.replace(temporary,self.path)
   if os.name=='posix':
    fd=os.open(str(self.path.parent),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
  return dict(saved=True,event_count=len(scene['events']))

 def restore(self,renderer):
  errors=[]
  with self.lock:
   for path in (self.path,self.backup):
    if not path.exists():continue
    try:
     scene=json.loads(path.read_text(encoding='utf-8'))
     if scene.get('schema')!=1 or (scene['width'],scene['height'],scene['seed'])!=(renderer.width,renderer.height,renderer.seed):raise ValueError('Canvas checkpoint mismatch')
     if not isinstance(scene['events'],list):raise ValueError('Events must be a list')
     if renderer.events:raise ValueError('Restore requires an empty renderer')
     # Validate and render the whole checkpoint before changing the live canvas.
     staged=type(renderer)(renderer.width,renderer.height,renderer.seed)
     for record in scene['events']:
      event=copy.deepcopy(record);staged._paint(event);staged._records.append(event)
     with renderer._lock:
      renderer._image=staged._image;renderer._parents=staged._parents;renderer._records=staged._records
     return dict(restored=True,source=path.name,event_count=len(renderer.events),warnings=errors)
    except (ValueError,KeyError,TypeError,OSError,IndexError) as exc:
     errors.append(path.name+': '+str(exc))
  return dict(restored=False,event_count=0,warnings=errors)
