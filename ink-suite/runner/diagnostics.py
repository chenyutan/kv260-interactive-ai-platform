"""Explicit, bounded runner capture of pose/decision/execution metadata.

No networking or image capture. JSON serialization and file writes run on one
daemon worker; the render/input thread only submits bounded immutable rows.
The diagnostic path is opt-in and never writes visitor reports or leaderboards.
"""
from collections import Counter
import json
import math
from pathlib import Path
import queue
import threading
import time


def _clean(value, depth=0):
    if isinstance(value,str):return value[:512]
    if value is None or isinstance(value,(bool,int)):return value
    if isinstance(value,float):return value if math.isfinite(value) else None
    if depth<6 and isinstance(value,(tuple,list)):return [_clean(v,depth+1) for v in value[:64]]
    if depth<6 and isinstance(value,dict):return {str(k):_clean(v,depth+1) for k,v in list(value.items())[:48]}
    return None


def source_packet(packet, now):
    """Whitelist only replayable input metadata, never visitor/account data."""
    if not isinstance(packet,dict):return None
    names=('pid','sequence','timestamp_seconds','phase','available','stale','latency_ms',
           'pose_count','pose_fps','capture_fps','action_reset_count','stream_reset',
           'motion_history_discontinuity')
    data={key:packet[key] for key in names if key in packet}
    points=packet.get('points17')
    data['points17']=([list(p[:3]) if isinstance(p,(list,tuple)) else None for p in points[:17]]
                      if isinstance(points,(list,tuple)) else None)
    stamp=packet.get('timestamp_seconds')
    data['source_age_seconds']=now-stamp if isinstance(stamp,(int,float)) and not isinstance(stamp,bool) else None
    suite=packet.get('suite')
    if isinstance(suite,dict):data['suite']={key:suite[key] for key in ('active','menu','control_active','mode','input_mode','open_serial') if key in suite}
    roi=packet.get('roi_selection')
    if isinstance(roi,dict):
        data['roi_selection']={key:roi[key] for key in ('stream_reset','motion_history_discontinuity','selected_index','reason') if key in roi}
        continuity=roi.get('continuity')
        if isinstance(continuity,dict):data['roi_selection']['continuity']={key:continuity[key] for key in ('stream_reset','motion_history_discontinuity','reason') if key in continuity}
    return _clean(data)


def tracker_snapshot(tracker):
    body=getattr(tracker,'body',None)
    names=('anchor','calibration','progress','lane_target','lane_armed','neutral_since','jump_armed',
           'crouched','airborne','jump_at','slide_armed','upright_since')
    return _clean(dict(body={key:getattr(body,key,None) for key in names},
        body_geometry=getattr(tracker,'_body_geometry',None),
        jump_armed=getattr(tracker,'jump_armed',None),last_seen=getattr(tracker,'last_seen',None),
        source_stamp=getattr(tracker,'stamp',None),source_sequence=getattr(tracker,'sequence',None)))


def game_snapshot(game):
    names=('state','player_lane','player_x','jump_remaining','slide_remaining','player_y',
           '_grounded','_jump_buffer','lives','elapsed','distance')
    result={key:getattr(game,key,None) for key in names}
    result.update(jumping=bool(game.jumping),sliding=bool(game.sliding))
    return _clean(result)


def maintain_practice(game):
    """Only the opt-in caller uses this; normal engine rules stay untouched."""
    restored=game.state=='over'
    if restored:game.state='running'
    game.lives=max(3,game.lives);game.invulnerable_time=max(5.,game.invulnerable_time)
    # Flight/spring buffs would legitimately suppress or alter jump physics,
    # confounding a test whose only purpose is camera-to-action reliability.
    game.entities[:]=[item for item in game.entities if item.kind not in game.POWERUPS]
    for name in ('jetpack_time','boots_time','magnet_time','coinboost_time','shield_time'):setattr(game,name,0.)
    return restored


class RunnerDiagnostic:
    def __init__(self,path,seconds=90.,*,started=None,entry_until=None,capacity=256,seed=None):
        if not isinstance(seconds,(int,float)) or isinstance(seconds,bool) or not math.isfinite(seconds) or not 1<=seconds<=180:
            raise ValueError('diagnostic duration must be between 1 and 180 seconds')
        if not isinstance(capacity,int) or not 1<=capacity<=1024:raise ValueError('invalid diagnostic queue capacity')
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.started=time.monotonic() if started is None else float(started)
        self.entry_until=self.started if entry_until is None else float(entry_until)
        self.seconds=float(seconds);self.deadline=self.entry_until+self.seconds
        self.queue=queue.Queue(maxsize=capacity);self.stop=threading.Event()
        self.submitted=self.written=self.dropped=0;self.error=None;self.close_reason='shutdown'
        self.detected='—';self.executed='—';self.reason='waiting';self.actions=Counter();self.events=Counter()
        # One startup open, never a per-frame open/flush/fsync on the GUI thread.
        self.stream=self.path.open('x',encoding='utf8',buffering=64*1024)
        self.thread=threading.Thread(target=self._write,name='runner-diagnostic-writer',daemon=True)
        self.thread.start()
        self.submit(dict(kind='session_start',protocol='runner-input-diagnostic-v1',
            started_monotonic=self.started,started_wall_seconds=time.time(),entry_until_monotonic=self.entry_until,duration_seconds=seconds,
            seed=seed,practice=True,visitor_reporting=False,raw_coco17=True))

    def submit(self,row):
        if self.stop.is_set() or self.error:return False
        try:self.queue.put_nowait(row);self.submitted+=1;return True
        except queue.Full:self.dropped+=1;return False

    def _write(self):
        last_flush=time.monotonic()
        try:
            while not self.stop.is_set() or not self.queue.empty():
                try:row=self.queue.get(timeout=.1)
                except queue.Empty:continue
                self.stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');self.written+=1
                if time.monotonic()-last_flush>=.5:self.stream.flush();last_flush=time.monotonic()
            self.stream.write(json.dumps(dict(kind='writer_summary',submitted=self.submitted,written=self.written,
                dropped=self.dropped,reason=self.close_reason,recognized=dict(self.actions),executed=dict(self.events)))+'\n')
            self.stream.flush()
        except (OSError,ValueError,TypeError) as error:self.error=type(error).__name__+': '+str(error)[:160]
        finally:
            try:self.stream.close()
            except (OSError,ValueError) as error:self.error=type(error).__name__+': '+str(error)[:160]

    def expired(self,now):return now>=self.deadline

    def begin_entry(self,now,delay):
        self.entry_until=now+delay;self.deadline=self.entry_until+self.seconds
        self.submit(dict(kind='ready',monotonic=now,entry_delay_seconds=delay,
            entry_until_monotonic=self.entry_until,deadline_monotonic=self.deadline))

    def record(self,*,now,dt,source,raw_action,effective_action,tracker_before,tracker_after,
               game_before,game_after,events,entered,tracking_paused,generation,practice_restored=False):
        names=[]
        if raw_action:
            if raw_action.get('move'):names.append('左' if raw_action['move']<0 else '右')
            for key,label in (('jump','跳'),('slide','蹲'),('confirm','确认')):
                if raw_action.get(key):names.append(label)
            if names:self.detected='+'.join(names);self.actions.update(names)
        current_reason=(effective_action or {}).get('reason','waiting')
        if current_reason!='duplicate_pose':self.reason=str(current_reason)
        done=[]
        for event in events:
            kind=event.get('kind')
            if kind in ('lane','jump','slide'):
                label=('左' if event.get('direction',0)<0 else '右') if kind=='lane' else '跳' if kind=='jump' else '蹲'
                done.append(label);self.events[kind]+=1
        if done:self.executed='+'.join(done)
        return self.submit(_clean(dict(kind='input_tick',monotonic=now,elapsed=now-self.started,
            test_elapsed=now-self.entry_until,dt=dt,generation=generation,entered=entered,
            tracking_paused=tracking_paused,source=source_packet(source,now),
            tracker_before=tracker_before,tracker_after=tracker_after,raw_action=raw_action,
            action=effective_action,game_before=game_before,game_after=game_after,game_events=events,
            practice_restored=practice_restored)))

    def hud(self):
        if self.error:return '诊断记录失败：'+self.error[:34]
        return f'诊断 识别:{self.detected} 执行:{self.executed} | {self.reason[:26]}'

    def close(self,reason='shutdown',timeout=.8):
        self.close_reason=reason;self.stop.set()
        if threading.current_thread() is not self.thread:self.thread.join(timeout)
        return not self.thread.is_alive() and self.error is None
