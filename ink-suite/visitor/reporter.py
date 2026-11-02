"""Trusted local game reporter; bounded async DB writes outside rendering."""
import os
import queue
import sys
import threading
import time
import uuid
from .store import VisitorStore,FIELDS,now,summary,dumps

class GameReporter:
    def __init__(self,store,session_id,game):
        self.store=store;self.session_id=session_id;self.game=game
        self.round_id=None;self.started=None;self.last_time=None;self.last_data=None;self.finished=False
        self.jobs=queue.Queue();self.due=0.;self.closed=False;self.game_identity=None
        self.thread=threading.Thread(target=self.worker,daemon=True,name='visitor-results');self.thread.start()

    @classmethod
    def from_environment(cls,game,disabled=False):
        root=os.environ.get('INKSUITE_VISITOR_DATA');session=os.environ.get('INKSUITE_VISITOR_SESSION')
        if disabled or not root or not session:return None
        return cls(VisitorStore(root),session,game)

    def worker(self):
        while True:
            job=self.jobs.get()
            try:
                if job is None:return
                retry_path=self.store.root/('pending-'+job[1]+'.json') if job[4]!='active' else None
                # Retry the same immutable job; terminal results are never superseded.
                for attempt in range(3):
                    try:
                        self.store.record(*job)
                        if retry_path:retry_path.unlink(missing_ok=True)
                        break
                    except (OSError,ValueError,__import__('sqlite3').Error) as error:
                        if attempt==2:print('visitor result save failed: '+str(error),file=sys.stderr,flush=True)
                        else:time.sleep(.1*(attempt+1))
            except (OSError,ValueError) as error:print('visitor durable result save failed: '+str(error),file=sys.stderr,flush=True)
            finally:self.jobs.task_done()

    def submit(self,data,status):
        job=(self.session_id,self.round_id,self.game,data,status,self.started)
        if status!='active':
            # A rare terminal/exit write must be durable BEFORE it enters the
            # queue. An older progress DB write can wait on SQLite for seconds.
            retry_path=self.store.root/('pending-'+self.round_id+'.json');temp=retry_path.with_suffix('.tmp')
            with temp.open('w',encoding='utf-8') as stream:stream.write(dumps(job));stream.flush();os.fsync(stream.fileno())
            temp.replace(retry_path)
        self.jobs.put(job)

    def observe(self,game,phase=None,running=None):
        if self.closed:return
        # Read only scalar counters, never build the game's scene snapshot.
        data={key:getattr(game,key) for key in FIELDS if hasattr(game,key)}
        if self.game in ('platformer','runner','kart'):data.update(game.report())
        if self.game=='taiko':
            from taiko.engine import GRADE_RULES
            judged=sum(game.counts.values())
            data['accuracy']=100*sum(GRADE_RULES[k]['accuracy']*v for k,v in game.counts.items())/judged if judged else 0.
            data['rank']='--' if not judged else 'S' if data['accuracy']>=95 else 'A' if data['accuracy']>=85 else 'B' if data['accuracy']>=70 else 'C' if data['accuracy']>=50 else 'D'
            data['total_notes']=len(game.notes)
        if 'best_combo' not in data and hasattr(game,'best'):data['best_combo']=game.best
        data=summary(data);phase=phase or getattr(game,'phase',getattr(game,'state','ready'))
        terminal=phase in ('won','lost','over','finished') or getattr(game,'finished',False)
        if self.game in ('sword','koi','platformer','runner','kart') and terminal:data['result']=phase
        if self.game=='koi':data['grade']='金鳞化龙' if phase=='won' and data.get('lives',0)>=4 else '乘浪成龙' if phase=='won' else '待越龙门'
        elapsed=float(data.get('time',data.get('elapsed',0.)))
        active=bool(running) if running is not None else (phase in ('playing','countdown','finishing','transforming')
            or self.game in ('runner','kart') and phase=='running')
        if self.game_identity is not None and self.game_identity!=id(game):self.reset()
        self.game_identity=id(game)
        if self.round_id and not self.finished and self.last_time is not None and elapsed<self.last_time-.25:
            self.submit(self.last_data,'interrupted');self.round_id=None
        if self.round_id and (phase in ('ready','menu') or self.finished and not terminal):self.round_id=None
        if self.round_id is None:
            if not active:return
            self.round_id=uuid.uuid4().hex;self.started=now();self.finished=False;self.due=0.
        self.last_time=elapsed;self.last_data=data
        if terminal and not self.finished:
            self.submit(data,'completed');self.finished=True
        elif not self.finished and time.monotonic()>=self.due:
            self.due=time.monotonic()+1.
            # At most one pending progress job; finish jobs remain lossless.
            if self.jobs.qsize()<2:self.submit(data,'active')

    def close(self,game=None,phase=None,running=None):
        if self.closed:return
        if game is not None:self.observe(game,phase,running)
        self.closed=True
        if self.round_id and not self.finished and self.last_data:self.submit(self.last_data,'interrupted')
        self.jobs.put(None);self.thread.join(3.)

    def reset(self):
        if self.round_id and not self.finished and self.last_data:self.submit(self.last_data,'interrupted')
        self.round_id=None;self.last_time=None;self.last_data=None

def attach(panel,mode,GLib,disabled=False):
    reporter=GameReporter.from_environment(mode,disabled)
    if reporter is None:return None
    def read():
        if mode=='taiko':return panel.session.game,panel.phase(),panel.session.running
        return panel.game,None,None
    def tick():
        try:reporter.observe(*read())
        except Exception as exc:print('visitor observation failed: '+str(exc),file=sys.stderr)
        return not reporter.closed
    timer=GLib.timeout_add(200,tick)
    original=panel.operation
    def operation(op,*args,**kwargs):
        if op=='shutdown':reporter.close(*read())
        return original(op,*args,**kwargs)
    panel.operation=operation
    original_restart=panel.restart
    def restart(*args,**kwargs):
        reporter.observe(*read());result=original_restart(*args,**kwargs)
        if result is not False or mode=='koi':reporter.reset()
        return result
    panel.restart=restart
    return reporter
