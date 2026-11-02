"""Restore a deployment checkpoint once, retaining its consumed source file."""
import copy,json,time
from pathlib import Path

def restore(renderer,path):
 path=Path(path)
 if not path.exists():return False
 scene=json.loads(path.read_text(encoding='utf-8'))
 if (scene['width'],scene['height'],scene['seed'])!=(renderer.width,renderer.height,renderer.seed):
  raise ValueError('Canvas checkpoint does not match renderer')
 if renderer.events:raise ValueError('Restore requires an empty renderer')
 for record in scene['events']:
  e=copy.deepcopy(record);renderer._paint(e);renderer._records.append(e)
 path.rename(path.with_name(path.name+'.consumed-'+str(time.monotonic_ns())))
 return True
