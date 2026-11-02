#!/usr/bin/env python3
"""Native GTK/Cairo rhythm game. No camera, image endpoint or model access."""
import argparse
from collections import deque
from collections import Counter
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from action_dispatch import ActionDispatcher
from gamepad.adapters import create_gamepad
from toolbar_autohide import hide_embedded_toolbar
from audio.integration import ExperienceAudio,score_path
from taiko.audio_clock import GstClock,SilentClock
from taiko.input import DrumInput
from taiko.clap_input import ClapInput
from taiko.tracking import TrackingGate,MusicObservation
from taiko.session import Session
from taiko.renderer import Renderer
from sword.rgb565 import RGB565Presenter
from sword.metrics import LatestMetricsWriter


def emit(event,**values):
    try:print(json.dumps(dict(event=event,mode='taiko',**values),ensure_ascii=False),flush=True)
    except (BrokenPipeError,OSError):pass


def percentile(values,q):
    if not values:return 0.
    a=sorted(values);return a[min(len(a)-1,int((len(a)-1)*q))]


class PoseTiming:
    """Bounded scalar-only timing, enabled only for an announced audit.

    Never retain coordinates, pixels or arbitrary API values. Frozen pause
    contexts survive recovery and the asynchronous latest-only metrics writer.
    This observer has no influence on input validity or music continuity.
    """
    STAGES=('source_to_received_ms','snapshot_request_ms','pre_queue_ms',
            'primary_preprocess_ms','inference_queue_ms','inference_and_action_ms',
            'source_to_socket_ms','socket_validation_ms','frame_read_ms')

    def __init__(self):
        self.lock=threading.Lock();self.polls=deque(maxlen=64);self.ticks=deque(maxlen=96)
        self.events=deque(maxlen=2);self.distinct=deque(maxlen=2)
        self.counts=Counter();self.inflight=None;self.last_tick=None;self.last_key=None
        self.max_tick_gap_ms=0.

    @staticmethod
    def number(value):
        return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None

    @classmethod
    def project(cls,state):
        if not isinstance(state,dict):return dict(packet_valid=False)
        row=dict(packet_valid=True)
        for key in ('pid','sequence','timestamp_seconds','pose_count','capture_failures',
                    'latest_dropped','pipeline_ms','latency_ms','pose_fps','capture_fps'):
            row[key]=cls.number(state.get(key))
        for key in ('stale','available'):
            value=state.get(key);row[key]=value if isinstance(value,bool) else None
        stages=state.get('stage_times')
        row['stage_times']={key:cls.number(stages.get(key)) for key in cls.STAGES
                            if cls.number(stages.get(key)) is not None} if isinstance(stages,dict) else {}
        return row

    def start(self,now,generation):
        with self.lock:
            self.counts['poll_started']+=1
            identifier=self.counts['poll_started']
            self.inflight=dict(poll_id=identifier,started=now,generation=generation)
            return identifier

    def finish(self,identifier,started,received,state,error,accepted,replaced):
        row=dict(self.project(state),poll_id=identifier,started=started,received=received,
                 request_ms=(received-started)*1000,accepted=bool(accepted),replaced=bool(replaced),
                 error_type=type(error).__name__[:64] if error is not None else None)
        stamp=row.get('timestamp_seconds')
        row['source_age_at_receive']=received-stamp if stamp is not None else None
        with self.lock:
            if len(self.polls)==self.polls.maxlen:self.counts['poll_ring_overwrites']+=1
            self.inflight=None;self.polls.append(row);self.counts['poll_finished']+=1
            self.counts['poll_errors']+=error is not None
            self.counts['packets_replaced']+=bool(replaced and accepted)
            key=(row.get('pid'),row.get('sequence'),stamp)
            if accepted and row['packet_valid'] and key!=self.last_key:
                self.distinct.append(row);self.last_key=key
            for index,event in enumerate(self.events):
                inflight=event.get('inflight')
                update={}
                if inflight and inflight['poll_id']==identifier:update['inflight_result']=row
                if event.get('resume') is None:
                    update['post_polls']=(event.get('post_polls',[])+[row])[-6:]
                if update:self.events[index]=dict(event,**update)

    def consume(self,now,pending,state,control,decision,reason,before,after,running):
        received=pending[1] if pending is not None else None
        row=dict(monotonic=now,tick_gap_ms=(now-self.last_tick)*1000 if self.last_tick is not None else None,
                 pending_kind=('none' if pending is None else 'failure' if pending[0] is None else
                               'expired_delivery' if now-received>.24 else 'packet'),
                 poll_id=pending[2] if pending is not None and len(pending)>2 else None,
                 delivery_ms=(now-received)*1000 if received is not None else None,
                 state_sequence=self.number((state or {}).get('sequence')),
                 source_stamp=self.number(control.get('source_stamp')),
                 source_age=self.number(control.get('source_age')),fresh=bool(control.get('fresh')),
                 input_reason=control.get('reason'),decision=decision,reason=reason,
                 gate_source_before=before,gate_source_after=after,running_before=bool(running))
        with self.lock:
            self.max_tick_gap_ms=max(self.max_tick_gap_ms,row['tick_gap_ms'] or 0.)
            if len(self.ticks)==self.ticks.maxlen:self.counts['tick_ring_overwrites']+=1
            self.last_tick=now;self.ticks.append(row);self.counts['ticks']+=1
            if row['pending_kind']=='expired_delivery':self.counts['expired_delivery']+=1
            if self.events and self.events[-1].get('resume') is None:
                event=self.events[-1]
                update=dict(post_ticks=(event.get('post_ticks',[])+[row])[-6:])
                if decision=='resume':update['resume']=row
                self.events[-1]=dict(event,**update)
            if decision=='pause' and running:
                # Rows are detached projections and are never mutated after
                # publication. Copy all containers while holding this short lock.
                if len(self.events)==self.events.maxlen:self.counts['pause_ring_overwrites']+=1
                self.events.append(dict(monotonic=now,reason=reason,resume=None,
                    post_polls=[],post_ticks=[],inflight_result=None,
                    context_frozen_at=time.monotonic(),
                    polls=list(self.polls)[-16:],ticks=list(self.ticks)[-16:],
                    last_received=list(self.polls)[-2:],last_distinct=list(self.distinct),
                    inflight=dict(self.inflight) if self.inflight else None))
                self.counts['recorded_pauses']+=1

    def snapshot(self,now):
        with self.lock:
            return dict(enabled=True,counts=dict(self.counts),last_polls=list(self.polls)[-4:],
                        max_tick_gap_ms=self.max_tick_gap_ms,
                        last_ticks=list(self.ticks)[-4:],pause_contexts=list(self.events),
                        inflight=dict(self.inflight,age_ms=(now-self.inflight['started'])*1000)
                        if self.inflight else None,images_or_coordinates_retained=False)


class TaikoPanel(Gtk.Box):
    def __init__(self,args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.args=args;self.stop=threading.Event();self.lock=threading.Lock()
        self.active=not bool(args.embed_socket);self.generation=0;self.latest=None;self.state=None
        self.pose_timing=PoseTiming() if os.environ.get('INK_TAIKO_POSE_TIMING')=='1' else None
        self.tracker=DrumInput();self.clap=ClapInput();self.renderer=Renderer();self.presenter=RGB565Presenter()
        self.gamepad=create_gamepad('taiko',disabled=args.offline_preview)
        self.audio=self.make_audio(args.audio_sink)
        self.session=Session(self.audio,args.difficulty,args.input_offset_ms,practice=args.practice,
                             judgment=getattr(args,'judgment','relaxed'))
        self.sound=ExperienceAudio('taiko',enabled=not args.offline_preview,sink=args.audio_sink if args.audio_sink!='silent' else 'autoaudiosink')
        self.sound.bind(self);self.sound.apply_music_level()
        if self.active:self.sound.resume()
        self.notice='';self.overlay='准备入场';self.control=dict(valid=False,events=[],hands={})
        self.entry_until=time.monotonic()+args.entry_delay;self.recovery_until=0.
        self.last_valid=None;self.input_hold=True;self.host_resume=True
        self.music_observer=MusicObservation()
        self.tracking=TrackingGate(source_grace=.90,observation_age=MusicObservation.MAX_AGE)
        self.tracking_counts=Counter();self.tracking_events=deque(maxlen=64)
        self.observed_poses=deque(maxlen=300)
        self.frames=0;self.draw_ms=deque(maxlen=600);self.draw_at=deque(maxlen=600)
        self.compose_ms=deque(maxlen=600);self.present_ms=deque(maxlen=600)
        self.tick_ms=deque(maxlen=600);self.backbuffer=None;self.fatal_error=None
        self.started_at=time.monotonic();self.final_at=None;self.synthetic_seen=set()
        self.metrics=LatestMetricsWriter(args.metrics) if args.metrics else None;self.next_metrics=0.
        css=Gtk.CssProvider();css.load_from_data(b'.taiko-toolbar {background:#eae2cd;padding:4px;} .taiko-toolbar button {padding:4px 10px;color:#203c35;background:#f6edda;}')
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        toolbar=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
        self.toolbar=toolbar
        toolbar.get_style_context().add_class('taiko-toolbar')
        bar=Gtk.Box(spacing=6)
        for label,callback in [('返回菜单',self.menu),('再奏一曲',self.restart),('重试音频',self.retry_audio),('无声练习',self.silent),('全屏',self.fullscreen)]:
            button=Gtk.Button(label=label);button.connect('clicked',lambda _,cb=callback:cb());bar.pack_start(button,False,False,0)
        toolbar.pack_start(bar,False,False,0)
        settings=Gtk.Box(spacing=8)
        self.difficulty=Gtk.ComboBoxText()
        self.difficulty.append('easy','简单');self.difficulty.append('normal','普通');self.difficulty.append('hard','困难')
        self.difficulty.set_active_id(args.difficulty);self.difficulty.connect('changed',self.change_difficulty)
        settings.pack_start(self.difficulty,False,False,0)
        self.practice=Gtk.ComboBoxText()
        for value,label in (('full','完整演奏'),('intro','左右入门 · 分段'),('alternate','交替合击 · 分段'),('finale','高潮 · 分段')):
            self.practice.append(value,label)
        self.practice.set_active_id(args.practice);self.practice.connect('changed',self.change_practice)
        settings.pack_start(self.practice,False,False,0)
        self.judgment_combo=Gtk.ComboBoxText()
        self.judgment_combo.append('relaxed','体感判定');self.judgment_combo.append('strict','严谨判定')
        self.judgment_combo.set_active_id(self.session.judgment)
        self.judgment_combo.connect('changed',self.change_judgment)
        settings.pack_start(self.judgment_combo,False,False,0)
        self.timing_spin=Gtk.SpinButton.new_with_range(-500,500,25)
        self.timing_spin.set_digits(0);self.timing_spin.set_value(args.input_offset_ms)
        self.timing_spin.set_tooltip_text('击鼓补偿毫秒：负值推后、正值提前。暂停时可调。')
        self.timing_spin.connect('value-changed',self.change_timing)
        settings.pack_start(self.timing_spin,False,False,0)
        self.timing_label=Gtk.Label();self.update_timing_label()
        settings.pack_end(self.timing_label,False,False,4)
        toolbar.pack_start(settings,False,False,0);self.pack_start(toolbar,False,False,0)
        hide_embedded_toolbar(self.toolbar,bool(getattr(args,'embed_socket',None)))
        self.canvas=Gtk.DrawingArea();self.canvas.set_hexpand(True);self.canvas.set_vexpand(True)
        self.canvas.connect('draw',self.draw);self.pack_start(self.canvas,True,True,0)
        self.timer=GLib.timeout_add(16,self.scheduled_tick)
        self.poll_thread=None
        if not args.offline_preview and self.gamepad is None:
            self.poll_thread=threading.Thread(target=self.poll,name='taiko-latest-pose',daemon=True);self.poll_thread.start()

    def make_audio(self,sink):
        if sink=='silent':return SilentClock()
        try:return GstClock(score_path(Path(__file__).with_name('audio')/'qinglan-guyin.wav'),sink)
        except Exception as e:
            clock=SilentClock();clock.error='音频不可用：'+str(e);clock.notice=clock.error
            # Explicit silent button is needed; no automatic silent scoring.
            return clock

    def clear_input(self):
        self.tracker.reset();self.state=None;self.last_valid=None;self.control=dict(valid=False,events=[],hands={})
        if getattr(self,'gamepad',None):self.gamepad.reset()
        if getattr(self,'clap',None):self.clap.reset()
        self.tracking.reset()
        if getattr(self,'music_observer',None):self.music_observer.reset()
        with self.lock:self.latest=None

    def operation(self,op):
        if op=='shutdown':
            if self.stop.is_set():return False
            with self.lock:self.stop.set();self.active=False;self.generation+=1
            self.session.close()
            if getattr(self,'gamepad',None):self.gamepad.close()
            if getattr(self,'sound',None):self.sound.close()
            if self.timer is not None:GLib.source_remove(self.timer);self.timer=None
            self.write_metrics(force=True)
            if self.metrics:self.metrics.close()
            if Gtk.main_level():Gtk.main_quit()
            emit('shutdown',game_state=self.phase(),game_clock=self.session.game.time)
            return False
        if self.stop.is_set() or op not in ('pause','resume'):return False
        active=op=='resume'
        if self.active==active:return False
        with self.lock:self.active=active;self.generation+=1
        self.session.pause();self.clear_input();self.input_hold=True
        if getattr(self,'sound',None):self.sound.pause() if not active else self.sound.resume()
        self.entry_until=time.monotonic()+max(3.,self.args.entry_delay) if active else 0.
        self.recovery_until=0.
        self.overlay='准备继续' if active else '已暂停'
        self.canvas.queue_draw()
        self.write_metrics(force=True)
        emit('resumed' if active else 'paused',game_state=self.phase(),game_clock=self.session.game.time)
        return False

    def submit_operation(self,op):
        GLib.idle_add(self.operation,op,priority=GLib.PRIORITY_DEFAULT)

    def phase(self):
        return 'finished' if self.session.game.finished else 'playing' if self.session.running else 'paused'

    def menu(self):
        if not self.args.embed_socket and not self.active:self.operation('resume');return
        self.operation('pause');emit('menu_requested')

    def fullscreen(self):
        if self.args.embed_socket:emit('fullscreen_requested');return
        w=self.get_toplevel()
        if w.get_window().get_state()&Gdk.WindowState.FULLSCREEN:w.unfullscreen()
        else:w.fullscreen()

    def handle_action(self,request):
        if not hasattr(self,'action_dispatcher'):
            def send(event,**fields):
                fields.pop('mode',None);emit(event,**fields)
            self.action_dispatcher=ActionDispatcher('taiko',
                lambda:not self.active and not self.stop.is_set(),self.execute_action,send)
        return self.action_dispatcher.dispatch(request)

    def execute_action(self,action,done):
        if action.startswith('audio_'):
            if action=='audio_retry':
                self.retry_audio()
                if self.session.error or self.audio.error:
                    done(False,self.session.error or self.audio.error);return
            else:self.sound.handle(action)
            self.sound.apply_music_level()
            done(True,'声音设置已更新',audio=self.sound.director.status());return
        reason=''
        difficulties=('difficulty_easy','difficulty_normal','difficulty_hard')
        practices=('practice_full','practice_intro','practice_alternate','practice_finale')
        judgments=('judgment_relaxed','judgment_strict')
        if action in difficulties:
            level=action.removeprefix('difficulty_')
            self.session.difficulty=level;self.difficulty.set_active_id(level)
        if action in practices:
            value=action.removeprefix('practice_')
            self.session.practice=value;self.practice.set_active_id(value)
        if action in ('start','restart',*difficulties,*practices):
            if not self.restart(while_paused=True):done(False,self.session.error or 'restart_failed');return
        elif action in judgments:
            if not self.switch_judgment(action.removeprefix('judgment_'),while_paused=True,force_restart=True):
                done(False,self.session.error or 'judgment_change_failed');return
            reason='已切换为'+self.session.game.snapshot()['judgment_label']+'，当前曲段重新开始'
        elif action in ('timing_earlier','timing_later','timing_reset','timing_preset_m100','timing_preset_m150'):
            current=self.session.offset*1000
            presets={'timing_reset':0.,'timing_preset_m100':-100.,'timing_preset_m150':-150.}
            value=presets.get(action,max(-500.,min(500.,current+(25. if action=='timing_earlier' else -25.))))
            self.session.set_offset_ms(value);self.args.input_offset_ms=value
            self.update_timing_label()
            reason='击鼓补偿已设为 %+d 毫秒'%value
        elif action=='retry_audio':
            self.retry_audio()
            if self.session.error or self.audio.error:done(False,self.session.error or self.audio.error);return
        elif action=='silent':
            self.silent()
            if self.session.error:done(False,self.session.error);return
        elif action not in ('pause','continue'):done(False,'unsupported_action');return
        self.session.pause();self.canvas.queue_draw()
        done(True,reason,game_state=self.phase(),difficulty=self.session.difficulty,
             practice=self.session.practice,judgment=self.session.judgment,input_offset_ms=self.session.offset*1000)

    def restart(self,*,while_paused=False):
        if not self.active and not while_paused:return False
        if not self.session.restart():return False
        if getattr(self,'sound',None):self.sound.restart()
        self.clear_input();self.synthetic_seen.clear();self.final_at=None
        self.input_hold=True;self.recovery_until=0.;self.entry_until=time.monotonic()+3.
        return True

    def change_difficulty(self,combo):
        value=combo.get_active_id()
        if value and value!=self.session.difficulty:
            self.session.difficulty=value;self.restart()

    def change_practice(self,combo):
        value=combo.get_active_id()
        if value and value!=self.session.practice:
            self.session.practice=value;self.restart()

    def switch_judgment(self,value,*,while_paused=False,force_restart=False):
        if value not in ('strict','relaxed'):return False
        if value==self.session.judgment and not force_restart:return True
        if not self.restart(while_paused=while_paused):return False
        self.session.set_judgment(value);self.args.judgment=value
        combo=getattr(self,'judgment_combo',None)
        if combo is not None:combo.set_active_id(value)
        self.update_timing_label();return True

    def change_judgment(self,combo):
        value=combo.get_active_id()
        if value and value!=self.session.judgment:
            if not self.switch_judgment(value,while_paused=not self.active):
                combo.set_active_id(self.session.judgment)

    def change_timing(self,spin):
        value=spin.get_value()
        if self.session.running:
            if value!=self.session.offset*1000:spin.set_value(self.session.offset*1000)
            return
        self.session.set_offset_ms(value);self.args.input_offset_ms=value
        self.update_timing_label()

    def update_timing_label(self):
        self.timing_label.set_text('补偿 %+d ms · 负值推后'%(self.session.offset*1000))
        spin=getattr(self,'timing_spin',None)
        if spin is not None and spin.get_value()!=self.session.offset*1000:spin.set_value(self.session.offset*1000)

    def retry_audio(self):
        clock=self.make_audio(self.args.audio_sink)
        self.session.replace_clock(clock);self.audio=clock
        if getattr(self,'sound',None):
            self.sound.director.retry();self.sound.apply_music_level()
        self.clear_input();self.input_hold=True;self.recovery_until=0.;self.entry_until=time.monotonic()+3.

    def silent(self):
        self.session.replace_clock(SilentClock());self.audio=self.session.clock
        if getattr(self,'sound',None):
            self.sound.director.pause();self.sound.apply_music_level()
        self.clear_input();self.input_hold=True;self.recovery_until=0.;self.entry_until=time.monotonic()+3.

    def key(self,_,event):
        if event.keyval in (Gdk.KEY_Escape,Gdk.KEY_Menu,Gdk.KEY_F1):self.menu();return True
        if event.keyval==Gdk.KEY_F11:self.fullscreen();return True
        if self.args.offline_preview and self.session.running:
            hands=('L',) if event.keyval in (Gdk.KEY_f,Gdk.KEY_F) else ('R',) if event.keyval in (Gdk.KEY_j,Gdk.KEY_J) else ('C',) if event.keyval in (Gdk.KEY_k,Gdk.KEY_K) else ('L','R') if event.keyval==Gdk.KEY_space else ()
            for hand in hands:self.session.game.strike(hand,self.audio.position(),('key',event.time,hand))
            if getattr(self,'sound',None):self.sound.tick()
            return bool(hands)
        return False

    def poll(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url=self.args.pose_url.rstrip('/')
        if not url.endswith('/api/state'):url+='/api/state'
        while not self.stop.is_set():
            with self.lock:active,generation=self.active,self.generation
            if not active:self.stop.wait(.06);continue
            timing=getattr(self,'pose_timing',None);started=time.monotonic();error=None
            identifier=None
            if timing:
                try:identifier=timing.start(started,generation)
                except Exception as exc:
                    self.pose_timing=None;self.pose_timing_error=type(exc).__name__;timing=None
            try:
                with opener.open(url,timeout=.40) as response:
                    raw=response.read(262145)
                if len(raw)>262144:raise ValueError('Oversized pose response')
                state=json.loads(raw)
                if not isinstance(state,dict):state=None
            except Exception as error_value:state=None;error=error_value
            received=time.monotonic()
            with self.lock:
                accepted=self.active and generation==self.generation and not self.stop.is_set()
                replaced=self.latest is not None
                if timing:
                    try:timing.finish(identifier,started,received,state,error,accepted,replaced)
                    except Exception as exc:
                        self.pose_timing=None;self.pose_timing_error=type(exc).__name__;timing=None
                if accepted:self.latest=(state,received,identifier) if timing else (state,received)
            self.stop.wait(.025 if state is not None else .12)

    def tick(self):
        if self.stop.is_set():return False
        now=time.monotonic();begin=time.perf_counter()
        if self.args.quit_after and now-self.started_at>=self.args.quit_after:self.operation('shutdown');return False
        if not self.active:return True
        if getattr(self,'gamepad',None):return self.tick_gamepad(now,begin)
        pending=None
        music_control=None
        if self.args.offline_preview:
            self.control=dict(valid=True,fresh=True,source_fresh=True,source_stamp=now,body_present=True,body_stamp=now,
                              events=[],hands={'L':.2,'R':.2},status='离线合成/键盘练习')
        else:
            with self.lock:pending,self.latest=self.latest,None
            if pending is not None:self.state=pending[0] if now-pending[1]<=.24 else None
            observer=getattr(self,'music_observer',None)
            if observer is not None:
                music_control=observer.consume(self.state,now,self.tracker._presence)
            # Retired identities / reordered observations must not enter the
            # strict motion chain even while the music lease is still alive.
            # A bounded late valid source is passed unchanged to DrumInput,
            # which independently retains its original 450ms strike limit.
            allowed=music_control is None or music_control.get('reason') not in ('invalid_source','out_of_order_observation')
            self.control=self.tracker.consume(self.state if allowed else None,now)
            clap=getattr(self,'clap',None)
            if clap is not None:
                clap_control=clap.consume(self.state if allowed else None,now,validated=self.control)
                self.control['clap']={key:value for key,value in clap_control.items() if key!='events'}
                if clap_control['events'] or clap_control.get('closing'):
                    # The same closing pair must not also score downward drums.
                    self.control['events']=clap_control['events']
                    self.control['status']='胸前拍掌' if clap_control['events'] else '双手合拢 · 拍掌准备'
                elif self.control.get('valid'):
                    self.control['status']='左右下挥敲鼓 · 胸前合拢拍掌'
        valid=self.control.get('valid',False)
        suite=(self.state or {}).get('suite') or {}
        blocked=isinstance(suite,dict) and bool(suite.get('menu') or (suite.get('active') and suite.get('mode')!='taiko'))
        candidate=isinstance(suite,dict) and bool(suite.get('control_active'))
        if self.control.get('fresh'):
            self.observed_poses.append(self.control.get('source_stamp',now))
            self.tracking_counts['poses']+=1
            self.tracking_counts['body_present']+=bool(self.control.get('body_present'))
            self.tracking_counts['strike_eligible']+=bool(valid)
            self.tracking_counts['reason_'+self.control.get('reason','unknown')]+=1
            for hand in self.control.get('hands',{}):self.tracking_counts['visible_'+hand]+=1
            for event in self.control.get('events',[]):self.tracking_counts['downstroke_'+event['hand']]+=1
            for reason in self.control.get('rejections',{}).values():self.tracking_counts['reject_'+reason]+=1
        before=self.tracking.last_source
        if music_control is None:music_control=self.control
        music_control['valid']=valid
        decision,reason=self.tracking.update(music_control,now,confirmed_menu=blocked,candidate=candidate,
            entering=now<self.entry_until,offline=self.args.offline_preview,source_state=self.state)
        timing=getattr(self,'pose_timing',None)
        if timing:
            try:timing.consume(now,pending,self.state,self.control,decision,reason,before,
                               self.tracking.last_source,self.session.running)
            except Exception as exc:self.pose_timing=None;self.pose_timing_error=type(exc).__name__
        if decision=='pause':
            if self.session.running:self.record_tracking('pause',reason,now)
            self.session.pause();self.input_hold=True
            if reason=='identity_discontinuity':
                self.tracker.reset(minimum_stamp=now)
                if getattr(self,'clap',None):self.clap.reset(minimum_stamp=now)
            self.overlay=('菜单手势 · 击鼓暂停' if blocked else '准备入场 · %d'%math.ceil(self.entry_until-now) if now<self.entry_until else '追踪暂停 · 请回到镜头内')
        elif decision=='hold':
            self.overlay='等待新的身体姿态' if reason=='waiting_body' else '准备击鼓'
        elif decision=='resume':
            self.tracker.reset(minimum_stamp=now);self.control['events']=[]
            if getattr(self,'clap',None):self.clap.reset(minimum_stamp=now)
            self.input_hold=False
            if not (self.session.error or self.audio.error):
                if self.session.resume():self.record_tracking('resume','fresh_body',now)
            # Failed audio remains frozen until an explicit retry action; do
            # not turn body recovery into repeated native playback attempts.
            self.overlay=''
        else:self.overlay=''
        if self.session.running:
            events=self.control.get('events',[]) if valid and not blocked and not candidate else []
            if self.args.autoplay:
                # Explicit diagnostic autoplay is unrelated to the pose detector.
                at=self.audio.position()
                for note in self.session.game.notes:
                    if note['id'] not in self.synthetic_seen and at>=note['time']:
                        self.synthetic_seen.add(note['id'])
                        for hand in (('L','R') if note['lane']=='B' else (note['lane'],)):
                            events.append(dict(hand=hand,timestamp=now-(at-note['time']),id=('autoplay',note['id'],hand)))
            self.session.tick(now,events)
        if self.session.error or self.audio.error:
            self.overlay='音频已暂停 · 菜单可重试或选无声练习'
            self.notice=str(self.session.error or self.audio.error)[:70]
        else:
            self.notice='离线演示 · F左 / J右 / K拍掌 / 空格双鼓' if self.args.offline_preview else self.control.get('status','')
        if self.session.game.finished and self.final_at is None:self.final_at=now
        if getattr(self,'sound',None):self.sound.tick()
        spin=getattr(self,'timing_spin',None)
        if spin is not None:spin.set_sensitive(not self.session.running)
        self.canvas.queue_draw();self.tick_ms.append((time.perf_counter()-begin)*1000)
        self.write_metrics();return True

    def tick_gamepad(self,now,begin):
        # The real GStreamer clock and Session judge remain the only timebase.
        # A controller has no body visibility lease and needs no camera packets.
        self.control=self.gamepad.taiko(now)
        valid=self.control['valid']
        entering=now<self.entry_until
        if not valid or entering:
            self.session.pause();self.input_hold=True
            self.overlay=(self.control['reason'] if not valid else '准备入场 · %d'%math.ceil(self.entry_until-now))
        else:
            resumed=False
            if not self.session.running and not (self.session.error or self.audio.error or self.session.game.finished):
                resumed=self.session.resume()
            self.input_hold=False;self.overlay=''
            if self.session.running:self.session.tick(now,() if resumed else self.control['events'])
        if self.session.error or self.audio.error:
            self.overlay='音频已暂停 · 菜单可重试或选无声练习'
            self.notice=str(self.session.error or self.audio.error)[:70]
        else:self.notice=self.control['status']
        if self.session.game.finished and self.final_at is None:self.final_at=now
        if getattr(self,'sound',None):self.sound.tick()
        if getattr(self,'timing_spin',None):self.timing_spin.set_sensitive(not self.session.running)
        self.canvas.queue_draw();self.tick_ms.append((time.perf_counter()-begin)*1000)
        self.write_metrics();return True

    def record_tracking(self,event,reason,now):
        self.tracking_counts[event+'_'+reason]+=1
        self.tracking_events.append(dict(event=event,reason=reason,monotonic=now,
            song_position=self.audio.position()))
        emit('tracking_'+event,reason=reason,song_position=self.audio.position())

    def scheduled_tick(self):
        self.timer=None;begin=time.monotonic()
        try:keep=self.tick()
        except Exception as error:
            self.fatal_error=str(error);emit('error',message=self.fatal_error);self.operation('shutdown');return False
        if keep and not self.stop.is_set():self.timer=GLib.timeout_add(max(1,math.ceil(16-(time.monotonic()-begin)*1000)),self.scheduled_tick)
        return False

    def draw(self,widget,cr):
        if self.stop.is_set():return False
        from render_policy import RenderPolicy, present
        start=time.monotonic()
        if not hasattr(self,'render_policy'):self.render_policy=RenderPolicy.from_environment()
        layout=self.render_policy.layout(max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height()))
        w,h=layout.render_size
        if self.backbuffer is None or (self.backbuffer.get_width(),self.backbuffer.get_height())!=(w,h):
            self.backbuffer=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        ui=dict(notice=self.notice,overlay=self.overlay,audio_notice=self.audio.notice,
                control_mode='gamepad' if self.gamepad else 'body',
                hands=self.control.get('hands',{}),clap=self.control.get('clap',{}),finale_age=start-self.final_at if self.final_at else 0.)
        self.renderer.draw(cairo.Context(self.backbuffer),w,h,self.session.game.snapshot(),ui)
        composed=time.monotonic()
        visual=widget.get_visual();target=cr.get_target()
        is565=(target.get_format()==cairo.FORMAT_RGB16_565 if isinstance(target,cairo.ImageSurface) else
               visual is not None and visual.get_depth()==16 and tuple(visual.get_red_pixel_details())==(0xf800,11,5)
               and tuple(visual.get_green_pixel_details())==(0x07e0,5,6) and tuple(visual.get_blue_pixel_details())==(0x001f,0,5))
        surface=self.presenter.convert(self.backbuffer) if is565 else self.backbuffer
        present(cr,surface,layout)
        self.frames+=1;self.draw_ms.append((time.monotonic()-start)*1000);self.draw_at.append(start)
        self.compose_ms.append((composed-start)*1000);self.present_ms.append((time.monotonic()-composed)*1000)
        return False

    def timing_snapshot(self,now):
        timing=getattr(self,'pose_timing',None)
        if timing:
            try:return timing.snapshot(now)
            except Exception as exc:self.pose_timing=None;self.pose_timing_error=type(exc).__name__
        return dict(enabled=False,error_type=getattr(self,'pose_timing_error',None))

    def performance_snapshot(self):
        d=list(self.draw_at);s=self.session.game.snapshot()
        observed=list(self.observed_poses);now=time.monotonic()
        transport=getattr(self.audio,'transport_status',None)
        return dict(mode='taiko',pid=os.getpid(),started_monotonic=self.started_at,monotonic=now,
            game_state=self.phase(),game_clock=s['time'],audio_position=self.audio.position(),
            score=s['score'],counts=s['counts'],combo=s['combo'],total_notes=s['total_notes'],
            strikes=s['strikes'],empty_strikes=s['empty_strikes'],input_diagnostics=self.session.input_snapshot(),
            difficulty=s['difficulty'],judgment=s['judgment'],judgment_label=s['judgment_label'],
            judgment_windows_ms=s['judgment_windows_ms'],hit_window_ms=s['hit_window_ms'],
            simultaneous_ms=s['simultaneous_ms'],practice=s.get('practice','full'),start_time=s.get('start_time',0),duration=s['duration'],
            accuracy=s.get('accuracy',0),rank=s.get('rank','--'),
            groove=s.get('groove',0),fever_active=s.get('fever_active',False),bonus_score=s.get('bonus_score',0),
            audio_mode=self.audio.mode,audio_error=self.audio.error,
            audio_transport=transport() if callable(transport) else None,input_offset_ms=self.args.input_offset_ms,
            offline=self.args.offline_preview,synthetic_autoplay=self.args.autoplay,camera_images_read=False,
            draw_frames=self.frames,draw_mean_ms=sum(self.draw_ms)/max(1,len(self.draw_ms)),draw_p95_ms=percentile(self.draw_ms,.95),
            compose_mean_ms=sum(self.compose_ms)/max(1,len(self.compose_ms)),present_mean_ms=sum(self.present_ms)/max(1,len(self.present_ms)),
            draw_callback_fps=(len(d)-1)/(d[-1]-d[0]) if len(d)>1 and d[-1]>d[0] else 0.,
            tick_mean_ms=sum(self.tick_ms)/max(1,len(self.tick_ms)),physical_scanout_measured=False,
            renderer_cache_allocations=self.renderer.allocations,active=self.active,fatal_error=self.fatal_error,
            tracking=self.tracking.snapshot(now),tracking_counts=dict(self.tracking_counts),
            music_continuity=dict(getattr(getattr(self,'music_observer',None),'control',{})),
            tracking_events=list(self.tracking_events),input_reason=self.control.get('reason'),
            pose_timing=self.timing_snapshot(now),
            input_hands=list(self.control.get('hands',{})),input_fresh=self.control.get('fresh',False),
            input_source_age=self.control.get('source_age'),input_rejections=self.control.get('rejections',{}),
            input_decisions=self.control.get('hand_diagnostics',{}),
            clap_input=self.control.get('clap',{}),
            observed_pose_fps=(len(observed)-1)/(observed[-1]-observed[0]) if len(observed)>1 and observed[-1]>observed[0] else 0.,
            presentation_backend=self.presenter.backend,native_presentation_calls=self.presenter.calls,
            alpha_backend=self.renderer.blitter.backend if self.renderer.blitter else 'cairo',
            alpha_native_calls=self.renderer.blitter.native_calls if self.renderer.blitter else 0,
            canvas=[self.canvas.get_allocated_width(),self.canvas.get_allocated_height()])

    def write_metrics(self,force=False):
        now=time.monotonic()
        if self.metrics and (force or now>=self.next_metrics):
            self.next_metrics=now+1;self.metrics.submit(self.performance_snapshot())


def read_commands(panel,stream):
    try:
        for line in stream:
            try:value=json.loads(line);op=value.get('op') if isinstance(value,dict) else None
            except (ValueError,TypeError):continue
            if op=='action':GLib.idle_add(panel.handle_action,value,priority=GLib.PRIORITY_DEFAULT);continue
            if op in ('pause','resume','shutdown'):
                panel.submit_operation(op)
                if op=='shutdown':return
    except (OSError,ValueError):pass
    panel.submit_operation('shutdown')


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--embed-socket',type=int);p.add_argument('--pose-url',default='http://127.0.0.1:8088/api/state')
    p.add_argument('--offline-preview',action='store_true');p.add_argument('--entry-delay',type=float,default=3.)
    p.add_argument('--metrics',type=Path);p.add_argument('--fullscreen',action='store_true')
    p.add_argument('--difficulty',choices=('easy','normal','hard'),default='easy')
    p.add_argument('--practice',choices=('full','intro','alternate','finale'),default='full')
    p.add_argument('--judgment',choices=('relaxed','strict'),default='relaxed')
    p.add_argument('--audio-sink',choices=('autoaudiosink','fakesink','silent'),default=None)
    p.add_argument('--input-offset-ms',type=float,default=0.,help='Positive: judge strikes earlier; preserve source timestamp correction')
    p.add_argument('--autoplay',action='store_true',help='Diagnostic only, requires --offline-preview')
    p.add_argument('--quit-after',type=float,default=0.,help='Diagnostic bounded lifetime, seconds')
    a=p.parse_args(argv)
    # Always retain the current process's lightweight counters, even when the
    # user enters after a bounded acceptance recorder has finished. RAM storage
    # and the single-pending asynchronous writer do not block GTK rendering.
    if a.metrics is None and not a.offline_preview:
        a.metrics=Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'))/('ink-suite-taiko-%d.json'%os.getuid())
    if a.audio_sink is None:a.audio_sink='fakesink' if a.offline_preview else 'autoaudiosink'
    url=urlsplit(a.pose_url)
    if url.scheme not in ('http','https') or not url.netloc or url.query or url.fragment or url.path not in ('','/','/api/state'):
        p.error('--pose-url must address only /api/state')
    if a.autoplay and not a.offline_preview:p.error('--autoplay requires --offline-preview')
    if any(not math.isfinite(v) or v<0 for v in (a.entry_delay,a.quit_after)):p.error('Nonnegative finite durations required')
    if not math.isfinite(a.input_offset_ms) or abs(a.input_offset_ms)>500:p.error('Calibration range: -500 .. 500 ms')
    return a


def main(argv=None):
    args=parse_args(argv)
    window=Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='鼓动山河')
    if not args.embed_socket:window.set_default_size(1024,600);window.set_size_request(640,400)
    panel=TaikoPanel(args)
    from visitor.reporter import attach
    attach(panel,'taiko',GLib,disabled=args.offline_preview)
    window.add(panel)
    window.connect('destroy',lambda *_:panel.operation('shutdown'));window.connect('key-press-event',panel.key)
    window.show_all()
    if args.fullscreen and not args.embed_socket:window.fullscreen()
    if args.embed_socket:threading.Thread(target=read_commands,args=(panel,sys.stdin),daemon=True,name='taiko-host-commands').start()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:panel.submit_operation('shutdown'))
    emit('ready',game_state=panel.phase(),game_clock=0.,offline=args.offline_preview,audio_mode=panel.audio.mode,
         metrics_path=str(args.metrics) if args.metrics else None)
    try:Gtk.main()
    finally:
        panel.operation('shutdown');window.destroy()
        if panel.poll_thread:panel.poll_thread.join(.6)
    return 1 if panel.fatal_error else 0


if __name__=='__main__':raise SystemExit(main())
