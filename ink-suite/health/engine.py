"""Monotonic local lesson controller and conservative observed-only feedback.

No GTK, numpy, inference, video, network, smoothing or pose interpolation. The
course clock continues through unobservable frames; such frames never count as
repetitions, tracked time, errors or completion of a technical exercise.
"""
import datetime,json,math,os,tempfile,time,uuid
from collections.abc import Mapping
from pathlib import Path
from .catalog import MODES,MODE_LABELS
from .course_registry import get_course
from .advice import AdviceObserver, lesson_guidance
from player_context import context_key

MISSING_FEEDBACK='暂无法判断，请让本式所需关节清晰入镜；可以继续跟练。'
MAX_FRAME_AGE=.8
CONFIDENCE=.15
DISPLAY_ARM_CONFIDENCE=.08
DISPLAY_TRUNK_CONFIDENCE=.35
RAISE_PARTIAL_MAX_GAP=.65
RAISE_REQUIRED=(5,6,9,10,11,12)
RAISE_CORE=(5,6,11,12)
_BLOCKED={'lost','error','failed','warming_up','initializing','camera_unavailable','camera_link_slow','offline','disconnected','unavailable'}

def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)):return None
    try:return float(value) if math.isfinite(value) else None
    except (OverflowError,ValueError):return None

def observed_points(state,now,*,allow_roi_history_reset=False):
    """Accept only normalized finite xyz-score points with a fresh board stamp."""
    if not isinstance(state,Mapping):return None,'missing',None
    if state.get('stale') or state.get('available') is False:return None,'stale',None
    phase=state.get('phase')
    if phase is not None and (not isinstance(phase,str) or phase in _BLOCKED):return None,'unavailable',None
    link=state.get('camera_link')
    if isinstance(link,Mapping) and link.get('present') is False:return None,'unavailable',None
    stamp=_number(state.get('timestamp_seconds'))
    if stamp is None or now-stamp>MAX_FRAME_AGE or now-stamp<-.1:return None,'stale',None
    if any(state.get(key) for key in ('motion_discontinuity','pose_discontinuity','motion_history_discontinuity','stream_reset')):return None,'discontinuity',None
    selection=state.get('roi_selection')
    if isinstance(selection,Mapping):
        continuity=selection.get('continuity')
        if isinstance(continuity,Mapping):
            if any(continuity.get(key) for key in ('motion_discontinuity','reset_motion_history','tracking_gap','stream_reset')):return None,'discontinuity',None
            if continuity.get('motion_history_discontinuity') and not allow_roi_history_reset:return None,'discontinuity',None
    raw=state.get('points17')
    if not isinstance(raw,(tuple,list)) or len(raw)!=17:return None,'missing',None
    points=[]
    for row in raw:
        if not isinstance(row,(tuple,list)) or len(row)<3:points.append(None);continue
        x,y,score=(_number(value) for value in row[:3])
        if (x is None or y is None or score is None or not 0<=x<=1 or not 0<=y<=1 or score<CONFIDENCE):points.append(None)
        else:points.append((x,y,score))
    sequence=state.get('sequence')
    if isinstance(sequence,bool) or not isinstance(sequence,int) or sequence<0:return None,'missing_identity',None
    pid=state.get('pid')
    if isinstance(pid,bool) or not isinstance(pid,(int,str)) or not pid:return None,'missing_identity',None
    return points,'observed',(pid,sequence,stamp)

def _geometry(points,required):
    if any(points[i] is None for i in required):return None
    ls,rs=points[5],points[6];lh,rh=points[11],points[12]
    shoulder=((ls[0]+rs[0])*.5,(ls[1]+rs[1])*.5)
    hip=((lh[0]+rh[0])*.5,(lh[1]+rh[1])*.5)
    width=math.dist(ls[:2],rs[:2]);torso=math.dist(shoulder,hip)
    if width<.025 or torso<.055 or torso>.75 or width>.8:return None
    # A grossly collapsed or inverted trunk is not a useful planar reference.
    if hip[1]-shoulder[1]<.025:return None
    return dict(shoulder=shoulder,hip=hip,width=width,torso=torso)

def display_points(raw,accepted):
    """Expose weak *current* arm estimates separately from counted observations.

    Caller must already have accepted frame identity/freshness. No interpolation,
    previous points, corrected coordinates or altered confidence values are used.
    A coherent, confidently observed torso is needed before weak arms can be
    shown. Geometry bounds only reject obvious limb outliers, not certify pose.
    """
    result=list(accepted)
    if not isinstance(raw,(list,tuple)) or len(raw)!=17:return result
    core=(5,6,11,12)
    if any(accepted[i] is None or accepted[i][2]<DISPLAY_TRUNK_CONFIDENCE for i in core):return result
    geometry=_geometry(accepted,core)
    if geometry is None:return result
    for joint,parent in ((7,5),(8,6),(9,7),(10,8)):
        if result[joint] is not None:continue
        value=raw[joint]
        if not isinstance(value,(list,tuple)) or len(value)<3:continue
        x,y,confidence=(_number(v) for v in value[:3])
        if (x is None or y is None or confidence is None or
                not 0<=x<=1 or not 0<=y<=1 or
                not DISPLAY_ARM_CONFIDENCE<=confidence<CONFIDENCE):continue
        anchor=result[parent]
        if anchor is None:continue
        # Correct x for the camera aspect when bounding a limb's length.
        distance=math.hypot((x-anchor[0])*16/9,y-anchor[1])
        if distance>geometry['torso']*1.35:continue
        result[joint]=(x,y,confidence)
    return result

def _roi_history_reset(state):
    """An ROI metric-history reset alone is not proof of a changed person."""
    selection=state.get('roi_selection') if isinstance(state,Mapping) else None
    history=selection.get('continuity') if isinstance(selection,Mapping) else None
    return isinstance(history,Mapping) and bool(history.get('motion_history_discontinuity'))

def _raise_core(points,minimum=DISPLAY_TRUNK_CONFIDENCE):
    if points is None or any(points[i] is None or points[i][2]<minimum for i in RAISE_CORE):return None
    return _geometry(points,RAISE_CORE)

def _raise_coherent(points,anchor):
    """Compare current observed torso to a last strong torso, never fill limbs."""
    current=_raise_core(points,CONFIDENCE);before=_raise_core(anchor)
    if current is None or before is None:return False
    if not .65<=current['torso']/before['torso']<=1.55:return False
    if not .35<=current['width']/before['width']<=2.5:return False
    for feature in ('shoulder','hip'):
        a,b=current[feature],before[feature]
        if math.hypot((a[0]-b[0])*16/9,a[1]-b[1])>before['torso']*.45:return False
    return True

def _raise_partial_evidence(raw,points,anchor):
    """Qualify a short quarantine, not a new neutral, endpoint or repetition.

    A fresh partial frame needs finite raw estimates, some actual body input
    and a last strong torso. Weak/corrupt current geometry is quarantined until
    a coherent strong torso returns; it does not become a counted observation.
    """
    if not isinstance(raw,(list,tuple)) or len(raw)!=17 or _raise_core(anchor) is None:return False
    for joint in RAISE_REQUIRED:
        value=raw[joint]
        if not isinstance(value,(list,tuple)) or len(value)<3:return False
        x,y,score=(_number(v) for v in value[:3])
        if x is None or y is None or score is None or not 0<=x<=1 or not 0<=y<=1 or score<0:return False
        # A tiny numeric heatmap value is not current wrist evidence. Scores
        # above this support a bounded wait only, never phase confirmation.
        if joint in (9,10) and score<.02:return False
    if sum(points[i] is not None for i in RAISE_REQUIRED)<2:return False
    before=_raise_core(anchor)
    for joint,shoulder in ((9,5),(10,6)):
        value=raw[joint];origin=anchor[shoulder]
        if math.hypot((value[0]-origin[0])*16/9,value[1]-origin[1])>before['torso']*2.25:return False
    # A confidently observed different body is a hard boundary, not a gap.
    if _raise_core(points) is not None and not _raise_coherent(points,anchor):return False
    return True

def _movement_state(identifier,p,g):
    """Return neutral/endpoint/transition; no judgment of medical correctness."""
    sy=g['shoulder'][1];hy=g['hip'][1];scale=g['torso'];width=g['width'];cx=g['shoulder'][0]
    ly,ry=p[9][1],p[10][1]
    if identifier=='raise':
        if max(ly,ry)<sy-.18*scale:return 'endpoint'
        if min(ly,ry)>sy+.30*scale:return 'neutral'
    elif identifier=='bow':
        distance=abs(p[9][0]-p[10][0])/width
        reach=max(abs(p[9][0]-cx),abs(p[10][0]-cx))/width
        height_ok=max(abs(ly-sy),abs(ry-sy))<.65*scale
        if reach>1.15 and distance>1.35 and height_ok:return 'endpoint'
        if distance<.85:return 'neutral'
    elif identifier=='single':
        if min(ly,ry)<sy-.18*scale and max(ly,ry)>sy+.20*scale:return 'endpoint'
        if min(ly,ry)>sy+.30*scale:return 'neutral'
    elif identifier=='sway':
        lean=abs(g['shoulder'][0]-g['hip'][0])/scale
        if lean>.23:return 'endpoint'
        if lean<.10:return 'neutral'
    elif identifier=='bend':
        # Observable hand descent only: this does not infer lumbar flexion.
        relative=((ly+ry)*.5-hy)/scale
        if relative>.60:return 'endpoint'
        if relative<.16:return 'neutral'
    return 'transition'

class LessonSession:
    def __init__(self,mode='learn',clock=time.monotonic,course_id='baduanjin'):
        if mode not in MODES:raise ValueError('Unknown lesson mode')
        self.course=get_course(course_id);self.course_id=self.course['id']
        self.mode=mode;self.clock=clock;self.session_id=uuid.uuid4().hex
        self.stop_at_lesson_boundary=False
        self.created_at=datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.status='ready';self.phase='ready';self.lesson_index=0;self.phase_elapsed=0.;self.session_elapsed=0.
        self._last_now=None;self._resume_phase=None;self._last_state=None;self._last_identity=None;self._observed_at=None
        self._points=None;self._display_points=None;self._observable=False;self._feedback='选择模式后开始练习。';self._advice=AdviceObserver(course_id=self.course_id)
        self._armed=False;self._endpoint=False;self._candidate=None;self._candidate_since=None;self._previous_valid_stamp=None
        self._partial_raise_gap=False
        self._raise_anchor=None;self._raise_last_valid_now=None;self._counter_reason='ready'
        self._stats=[dict(id=l['id'],name=l['name'],visited_seconds=0.,practice_seconds=0.,tracked_seconds=0.,observed_repetitions=0,scheduled_cycles=0,visits=0) for l in self.course['lessons']]
        self._ended_at=None;self._end_reason=None;self._completed_lessons=set();self._clock_resets=0
    @property
    def lesson(self):return self.course['lessons'][self.lesson_index]
    def _now(self,now):
        result=_number(self.clock() if now is None else now)
        if result is None:raise ValueError('A finite monotonic time is required')
        return result
    def _reset_observation(self,feedback=None):
        self._advice.reset()
        self._last_identity=None;self._previous_valid_stamp=None;self._observed_at=None;self._points=None;self._display_points=None;self._observable=False
        self._armed=False;self._endpoint=False;self._candidate=None;self._candidate_since=None
        self._partial_raise_gap=False
        self._raise_anchor=None;self._raise_last_valid_now=None;self._counter_reason='observation_reset'
        if feedback is not None:self._feedback=feedback
    def _enter(self,index):
        self.lesson_index=max(0,min(len(self.course['lessons'])-1,index));self.phase_elapsed=0.
        self.phase='demo' if self.mode=='learn' else 'practice'
        self._stats[self.lesson_index]['visits']+=1
        self._reset_observation('先看示意，再按自己的舒适幅度练习。' if self.phase=='demo' else '缓慢跟练，保持自然呼吸。')
    def _raise_gap_within(self,stamp,now):
        # Source capture timestamps and consumer receipts have different frame
        # ages. Both windows must pass; neither clock can expand the other.
        return (self._previous_valid_stamp is not None and self._raise_last_valid_now is not None and
                0<stamp-self._previous_valid_stamp<=MAX_FRAME_AGE+1e-9 and
                0<=now-self._raise_last_valid_now<=RAISE_PARTIAL_MAX_GAP+1e-9)
    def start(self,now=None):
        now=self._now(now)
        if self.status=='ready':
            self.status='running';self._last_now=now;self._enter(self.lesson_index)
        elif self.status=='paused':self.action('play',now)
        return self._snapshot(now)
    def _duration(self):
        phase=self._resume_phase if self.phase=='paused' else self.phase
        if phase=='demo':return self.lesson['demo_seconds']
        return self.lesson['practice_seconds']
    def _tick(self,now):
        if self._last_now is None:self._last_now=now;return
        delta=now-self._last_now;self._last_now=now
        if delta<0:
            self._clock_resets+=1;self._reset_observation(MISSING_FEEDBACK);return
        if self.status!='running' or delta<=0:return
        # Even a long application stall only advances a timed course; it does
        # not manufacture observed movement, tracking time or repetitions.
        remaining=delta
        for _ in range(len(self.course['lessons'])*2+1):
            duration=self._duration()
            advance=remaining if self.mode=='free' and self.phase=='practice' else min(remaining,max(0.,duration-self.phase_elapsed))
            self.phase_elapsed+=advance;remaining-=advance
            self.session_elapsed+=advance
            stats=self._stats[self.lesson_index];stats['visited_seconds']+=advance
            if self.phase=='practice':stats['practice_seconds']+=advance
            if self.mode=='free' and self.phase=='practice':break
            if self.phase_elapsed+1e-9<duration:break
            if self.phase=='demo':
                self.phase='practice';self.phase_elapsed=0.;self._reset_observation('现在跟练：'+self.lesson['instructions'][0])
            else:
                stats['scheduled_cycles']+=self.lesson['cycles'];self._completed_lessons.add(self.lesson['id'])
                if self.lesson_index==len(self.course['lessons'])-1:
                    self._complete('course_timer_finished');break
                self._enter(self.lesson_index+1)
                # A teaching-video coordinator owns the next lesson.  A long
                # event-loop stall must not consume subsequent lessons before
                # it can open their videos; the paused interval is not practice.
                if self.stop_at_lesson_boundary:break
            if remaining<=1e-9:break
    def _complete(self,reason='manual_finish'):
        self.status='completed';self.phase='completed';self._ended_at=datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._end_reason=reason
        self._reset_observation('本次练习已结束，可以查看记录或返回菜单。')
    def action(self,name,now=None):
        if not isinstance(name,str):raise ValueError('A lesson action name is required')
        now=self._now(now);self._tick(now)
        if name in ('start','begin') or name in ('play','resume') and self.status=='ready':return self.start(now)
        if name.startswith('set_mode_'):
            mode=name[len('set_mode_'):]
            if mode not in MODES:raise ValueError('Unknown lesson mode')
            if self.status!='ready':raise ValueError('Create a new session to change mode after starting')
            self.mode=mode
        elif name in ('pause','toggle_pause') and self.status=='running':
            self._resume_phase=self.phase;self.status='paused';self.phase='paused';self._reset_observation('练习已暂停。')
        elif name in ('play','resume','toggle_pause') and self.status=='paused':
            self.status='running';self.phase=self._resume_phase or 'practice';self._last_now=now;self._reset_observation('继续练习，按自己的节奏完成。')
        elif name.startswith('select_'):
            identifier=name[len('select_'):]
            identifiers=tuple(item['id'] for item in self.course['lessons'])
            if identifier not in identifiers:raise ValueError('Unknown lesson selection')
            index=identifiers.index(identifier)
            if self.status=='completed':return self._snapshot(now)
            if self.status=='ready':self.lesson_index=index
            else:
                self._enter(index)
                if self.status=='paused':self._resume_phase=self.phase;self.phase='paused'
        elif name in ('next','previous','repeat'):
            if self.status in ('completed','ready'):
                if self.status=='completed':return self._snapshot(now)
                offset=1 if name=='next' else -1 if name=='previous' else 0
                self.lesson_index=max(0,min(len(self.course['lessons'])-1,self.lesson_index+offset))
            else:
                offset=1 if name=='next' else -1 if name=='previous' else 0
                self._enter(self.lesson_index+offset)
                if self.status=='paused':self._resume_phase=self.phase;self.phase='paused'
        elif name in ('finish','stop'):
            if self.status!='completed':self._complete()
        elif name not in ('play','resume','pause','toggle_pause') and not name.startswith('set_mode_'):
            raise ValueError('Unknown lesson action: '+str(name))
        return self._snapshot(now)
    def update(self,state,now=None):
        now=self._now(now);self._tick(now)
        if self.status!='running':return self._snapshot(now)
        source,_=context_key(state)
        old_source=getattr(self,'_source_context',None)
        self._source_context=source
        if old_source is not None and source!=old_source:
            self._reset_observation('识别来源或访客已切换，请继续跟练。')
            self._counter_reason='source_or_visitor_changed'
            self._last_state=state
            return self._snapshot(now)
        is_raise=self.course_id=='baduanjin' and self.lesson['id']=='raise'
        points,reason,key=observed_points(state,now,allow_roi_history_reset=is_raise)
        self._last_state=state
        if points is None:
            self._reset_observation(MISSING_FEEDBACK);self._counter_reason=reason;return self._snapshot(now)
        previous=self._last_identity
        if previous is not None:
            if key==previous:
                if self._observed_at is not None and now-self._observed_at>.4:
                    self._reset_observation(MISSING_FEEDBACK)
                    self._counter_reason='duplicate_expired'
                    self._last_identity=previous
                return self._snapshot(now)
            if key[0]!=previous[0] or key[1]<=previous[1] or key[2]<=previous[2]:
                self._reset_observation(MISSING_FEEDBACK)
                self._counter_reason='source_time_or_sequence_regression'
                self._last_identity=key
                return self._snapshot(now)
        # Raise/return compares wrists to shoulders and hips. Requiring elbows
        # here used to erase a visible wrist cycle when an unused elbow faded.
        required=RAISE_REQUIRED if is_raise else self.lesson['required_joints']
        geometry=_geometry(points,required)
        self._last_identity=key;self._points=points;self._display_points=display_points(state.get('points17'),points);self._observed_at=now
        self._advice.update(self.lesson['id'],points,key[2],phase=self.phase,required=required)
        self._observable=geometry is not None and self.lesson['observable'] and self.phase=='practice'
        history_reset=is_raise and _roi_history_reset(state)
        incoherent=(is_raise and self._raise_anchor is not None and geometry is not None and
                    not _raise_coherent(points,self._raise_anchor))
        waiting_for_strong=(is_raise and self._partial_raise_gap and _raise_core(points) is None)
        if geometry is None or history_reset or incoherent or waiting_for_strong:
            preserve=(is_raise and self.phase=='practice' and self._raise_gap_within(key[2],now) and
                      _raise_partial_evidence(state.get('points17'),points,self._raise_anchor))
            # A weak frame cannot prove a new phase, a repetition or elapsed
            # tracking time. Only confirmed neutral/endpoint state may survive.
            self._candidate=None;self._candidate_since=None
            self._partial_raise_gap=preserve
            self._observable=False
            if preserve:
                self._counter_reason='roi_history_wait' if history_reset else 'partial_raise_wait'
                self._feedback=('点位短暂不稳，保留已确认阶段，等待清晰且一致的观测；缓慢继续即可。' if self._armed or self._endpoint else
                                '点位短暂不稳，等待清晰且一致的观测；缓慢继续即可。')
            else:
                self._previous_valid_stamp=None;self._armed=False;self._endpoint=False
                self._raise_anchor=None;self._raise_last_valid_now=None
                self._counter_reason='raise_gap_rejected' if is_raise else 'required_missing'
                self._feedback=MISSING_FEEDBACK
        elif self.phase=='demo':self._feedback='请先看动作示意；示范结束后进入跟练。'
        elif not self.lesson['observable']:
            self._feedback=self.lesson['assessment_note'];self._previous_valid_stamp=None
        else:
            self._counter_reason='observed'
            if self._previous_valid_stamp is not None:
                gap=key[2]-self._previous_valid_stamp
                needs_restore=is_raise and (self._partial_raise_gap or gap>.4)
                coherent_return=(not needs_restore or
                                 _raise_core(points) is not None and _raise_coherent(points,self._raise_anchor))
                within=self._raise_gap_within(key[2],now) if is_raise else 0<gap<=.4
                if within and coherent_return:
                    if gap<=.4 and not self._partial_raise_gap:self._stats[self.lesson_index]['tracked_seconds']+=gap
                else:
                    self._armed=False;self._endpoint=False;self._candidate=None;self._candidate_since=None
                    self._counter_reason='raise_recovery_rejected' if is_raise else 'source_gap_rejected'
                if needs_restore:self._candidate=None;self._candidate_since=None
            self._partial_raise_gap=False
            if is_raise and _raise_core(points) is not None:
                self._raise_anchor=[tuple(points[i]) if i in RAISE_CORE else None for i in range(17)]
            self._previous_valid_stamp=key[2]
            if is_raise:self._raise_last_valid_now=now
            movement=_movement_state(self.lesson['id'],points,geometry)
            if movement!=self._candidate:self._candidate=movement;self._candidate_since=key[2]
            stable=key[2]-self._candidate_since>=.18
            if stable and movement=='neutral':
                if self._armed and self._endpoint:
                    self._stats[self.lesson_index]['observed_repetitions']+=1
                    self._feedback='已记录一次可见动作往返；按舒适幅度继续。'
                else:self._feedback='已看到回收位置，缓慢完成下一次动作。'
                self._armed=True;self._endpoint=False
            elif stable and movement=='endpoint' and self._armed:
                self._endpoint=True;self._feedback='已看到动作展开，缓缓收回即可。'
            elif movement=='transition':self._feedback='动作进行中，缓慢完成，不必追求极限幅度。'
            elif not self._armed:self._feedback='先缓缓回到本式起始位置，再完成往返。'
        return self._snapshot(now)
    def _snapshot(self,now):
        if self._observed_at is not None and now-self._observed_at>.8:
            self._reset_observation(MISSING_FEEDBACK)
        lesson=self.lesson
        cycle=lesson['cycle_seconds'];position=(self.phase_elapsed%cycle)/cycle
        duration=self._duration() if self.phase in ('demo','practice','paused') else lesson['practice_seconds']
        ratio=min(1.,self.phase_elapsed/max(.01,duration))
        bilateral=lesson.get('bilateral',lesson['id'] in ('bow','single','look','sway','fist'))
        local_position=(position*2)%1. if bilateral else position
        instruction_index=min(len(lesson['instructions'])-1,int(local_position*len(lesson['instructions'])))
        instruction=lesson['instructions'][instruction_index]
        if bilateral and position>=.5:instruction='换另一侧：'+instruction
        instruction_side=('first' if position<.5 else 'second') if bilateral else None
        phase_names={'ready':'尚未开始','demo':'动作示意','practice':'跟练','paused':'已暂停','completed':'练习结束'}
        advice=self._advice.snapshot()
        if self.status=='running' and self.phase=='practice' and not advice.get('text'):
            advice=lesson_guidance(self.course_id,lesson['id'])
        return dict(course_id=self.course_id,course_title=self.course['title'],report_kind=self.course['report_kind'],repetition_counting=self.course['repetition_counting'],status=self.status,phase=self.phase,phase_name=phase_names[self.phase],mode=self.mode,mode_name=MODE_LABELS[self.mode],lesson_index=self.lesson_index,lesson_count=len(self.course['lessons']),lesson_id=lesson['id'],lesson_name=lesson['name'],instruction=instruction,instruction_index=instruction_index,instruction_side=instruction_side,instructions=list(lesson['instructions']),elapsed=round(self.phase_elapsed,3),session_elapsed=round(self.session_elapsed,3),progress=ratio,lesson_progress=(self.lesson_index+ratio)/len(self.course['lessons']),cycle_progress=position,repetition=self._stats[self.lesson_index]['observed_repetitions'],feedback=self._feedback,advice=advice,observable=self._observable,assessment_note=lesson['assessment_note'],reference_pose=self.course['reference_pose'](lesson['id'],position),reference_caption=self.course['reference_caption'],pose_points=self._points or [None]*17,display_pose_points=self._display_points or [None]*17,counts={s['id']:s['observed_repetitions'] for s in self._stats},report_ready=self.status=='completed')
    def snapshot(self,now=None):
        now=self._now(now);self._tick(now);return self._snapshot(now)
    def report(self):
        self.snapshot()
        return dict(schema_version=1,kind=self.course['report_kind'],course_id=self.course_id,course_title=self.course['title'],repetition_counting=self.course['repetition_counting'],session_id=self.session_id,created_at=self.created_at,ended_at=self._ended_at,status=self.status,end_reason=self._end_reason,course_timer_finished=self._end_reason=='course_timer_finished',mode=self.mode,mode_name=MODE_LABELS[self.mode],active_seconds=round(self.session_elapsed,3),observed_seconds=round(sum(s['tracked_seconds'] for s in self._stats),3),observed_repetitions=sum(s['observed_repetitions'] for s in self._stats),timer_finished_lessons=sorted(self._completed_lessons),lessons=[{k:round(v,3) if isinstance(v,float) else v for k,v in stat.items()} for stat in self._stats],assessment_scope=self.course['assessment_notice'],demonstration=self.course['reference_caption'],professional_motion_validation=False,sources=list(self.course['sources']),clock_resets=self._clock_resets)
    def save_report(self,directory,report=None):
        if report is None:report=self.report()
        if not isinstance(report,dict) or report.get('session_id')!=self.session_id or report.get('kind')!=self.course['report_kind']:
            raise ValueError('Report does not belong to this lesson session')
        payload=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        basename=self.course['report_prefix']+self.session_id
        target=directory/(basename+'.json')
        handle,path=tempfile.mkstemp(prefix='.'+basename+'-',suffix='.tmp',dir=directory)
        try:
            with os.fdopen(handle,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
            os.replace(path,target)
        finally:
            try:os.unlink(path)
            except FileNotFoundError:pass
        return target

