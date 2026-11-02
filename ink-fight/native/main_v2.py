"""Native InkFight V2: latest pose only, bounded art caches, no camera in benchmark."""
import argparse,json,math,threading,time,urllib.request
from pathlib import Path
from collections import deque
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib
from engine import Fight,Gesture,MOVES
from body_control import BodyControl
from input_gate import InputGate
from session_record import SessionRecord
from round_export import RoundExport
from scene import Scene,W,H
from presentation import FramePresenter
from game_source_policy import SourceGapPolicy

class Reader:
    def __init__(self):
        self.value=None;self.received=0;self.closed=False
        threading.Thread(target=self.run,daemon=True).start()
    def run(self):
        last=None
        while not self.closed:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8088/api/state',timeout=.5) as r:s=json.load(r)
                key=(s.get('pid'),s.get('sequence'),s.get('timestamp_seconds'))
                if key!=last:last=key;self.value=s;self.received=time.monotonic()
            except Exception:self.value=None
            time.sleep(.03)
    def get(self):return self.value if time.monotonic()-self.received<.3 else None

class Window(Gtk.Window):
    def __init__(self,args):
        super().__init__(title='墨武 · 山河对决 V3.0')
        self.args=args;self.game=Fight();self.gesture=Gesture();self.scene=Scene();self.presenter=FramePresenter()
        self.body_control=BodyControl();self.body_state={};self.manual_jump=False
        self.reader=Reader() if args.pose and not args.benchmark else None
        self.last=time.monotonic();self.frames=[];self.draw_ms=[];self.manual_guard=False
        self.keys=set();self.lost_since=None;self.tracking_pause=False;self.good_since=None;self.last_status='';self.help_open=False
        self.source_gap=SourceGapPolicy()
        self.moves_received={};self.moves_accepted={}
        self.input_gate=InputGate();self.input_feedback='';self.feedback_until=0.;self.input_events=deque(maxlen=600)
        self.recorder=SessionRecord(args.record_session) if args.record_session else None
        self.round_export=RoundExport(Path(__file__).resolve().parent/'rounds',enabled=not args.benchmark)
        self.round_save_retry=0.
        self.walk_in_until=None
        self.set_default_size(1024,600);self.set_size_request(640,400)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);self.add(box)
        bar=Gtk.Box(spacing=8);bar.set_border_width(5);box.pack_start(bar,False,False,0)
        for label,fn in [('开始 / 重来',self.start),('暂停 / 继续',self.pause),('招式谱',self.help),('全屏',lambda *_:self.fullscreen()),('退出',lambda *_:self.destroy())]:
            b=Gtk.Button(label=label);b.connect('clicked',fn);bar.pack_start(b,False,False,0)
        self.area=Gtk.DrawingArea();box.pack_start(self.area,True,True,0);self.area.connect('draw',self.draw)
        self.connect('destroy',self.close);self.connect('key-press-event',self.key);self.connect('key-release-event',self.key_up)
        self.connect('focus-out-event',self.focus_out);self.show_all();GLib.timeout_add(16,self.tick)
        if args.benchmark:self.start();self.game.countdown=0;self.fullscreen();GLib.timeout_add_seconds(args.benchmark,self.finish)
        elif args.live_test:self.start();self.fullscreen()
    def start(self,*_):
        recorder=getattr(self,'recorder',None)
        if recorder and recorder.started:return
        self.game.start();self.gesture.reset();self.tracking_pause=False;self.lost_since=None;self.good_since=None;self.help_open=False
        self.source_gap.reset()
        if hasattr(self,'body_control'):self.body_control.reset()
        self.round_export.start();self.round_save_retry=0.
        self.moves_received.clear();self.moves_accepted.clear()
        self.cancel_input();self.input_feedback='';self.feedback_until=0.;self.input_events.clear()
        if getattr(self.args,'live_test',False):self.walk_in_until=time.monotonic()+25.;self.game.countdown=25.
    def pause(self,*_):
        if self.help_open:return
        self.cancel_input()
        if hasattr(self,'body_control'):self.body_control.reset()
        if self.game.state in ('playing','countdown','finishing'):
            self.before_pause=self.game.state;self.game.state='paused';self.tracking_pause=False
        elif self.game.state=='paused':
            self.game.state=getattr(self,'before_pause','playing');self.tracking_pause=False;self.gesture.reset()
    def help(self,*_):
        self.cancel_input()
        if hasattr(self,'body_control'):self.body_control.reset()
        if self.help_open:self.help_open=False;self.game.state=self.help_state;self.gesture.reset()
        else:
            self.help_open=True;self.help_state=self.game.state
            if self.game.state in ('playing','countdown','finishing'):self.game.state='paused'
        self.area.queue_draw()
    def focus_out(self,*_):
        self.keys.clear();self.manual_guard=False
        self.cancel_input()
        if hasattr(self,'body_control'):self.body_control.reset()
        self.tracking_pause=False
        if not self.args.benchmark and self.game.state in ('playing','countdown','finishing'):self.pause()
        return False
    def cancel_input(self):
        self.input_gate.clear()
        self.body_state={};self.manual_jump=False
        if '接下一招' in self.input_feedback:
            self.input_feedback='';self.feedback_until=0.
    def perform(self,move):
        if self.help_open or self.game.state!='playing':return
        self.moves_received[move]=self.moves_received.get(move,0)+1
        self.feedback(move,self.input_gate.submit(self.game,move,time.monotonic()))
    def feedback(self,move,result):
        if result=='accepted':self.moves_accepted[move]=self.moves_accepted.get(move,0)+1
        name=MOVES.get(move,{}).get('name',move)
        endings={'accepted':'已出招','queued':'已识别，接下一招','busy':'已识别，上一招仍在收招','no_energy':f'已识别，真气不足 {round(self.game.energy)}/100','expired':'接招已超时，请重新出招'}
        if result in endings:
            self.input_feedback=name+' · '+endings[result];self.feedback_until=time.monotonic()+1.1
            self.input_events.append(dict(monotonic=time.monotonic(),game_time=round(self.game.time,3),move=move,result=result))
    def key(self,_,e):
        if e.keyval in self.keys:return True
        self.keys.add(e.keyval)
        if e.keyval==Gdk.KEY_Escape:
            if self.help_open:self.help()
            else:self.pause();self.unfullscreen()
        if e.keyval==Gdk.KEY_space:self.pause()
        if not self.reader:
            moves={Gdk.KEY_j:'left',Gdk.KEY_k:'right',Gdk.KEY_u:'uppercut_left',Gdk.KEY_i:'uppercut_right',Gdk.KEY_o:'palm',Gdk.KEY_p:'special',Gdk.KEY_z:'kick_left',Gdk.KEY_x:'kick_right'}
            if e.keyval in moves:self.perform(moves[e.keyval])
            if e.keyval==Gdk.KEY_l:self.manual_guard=True
            if e.keyval in (Gdk.KEY_w,Gdk.KEY_Up) and self.game.state=='playing':self.manual_jump=True
        return True
    def key_up(self,_,e):
        self.keys.discard(e.keyval)
        if e.keyval==Gdk.KEY_l:self.manual_guard=False
    def tick(self):
        now=time.monotonic();dt=now-self.last;self.last=now;guard=self.manual_guard
        recorder=getattr(self,'recorder',None)
        walk_in=getattr(self,'walk_in_until',None)
        if walk_in is not None:
            if self.game.state!='countdown' or self.help_open:
                self.walk_in_until+=dt;self.area.queue_draw();return True
            if now<walk_in:
                self.game.countdown=walk_in-now;self.last_status='入场倒计时 · 请按自己的习惯正常打一局';self.area.queue_draw();return True
            self.walk_in_until=None;self.game.countdown=0.;self.game.state='playing';self.gesture.reset();dt=0.
            if recorder:
                try:recorder.start()
                except OSError as error:
                    self.game.state='paused';self.last_status='记录启动失败，请退出后重试：'+str(error);self.area.queue_draw();return True
        pose=None;moves=[];body={};control={};menu_candidate=False
        if self.reader:
            pose=self.reader.get()
            if BodyControl.blocked(pose):
                # Act on the current API packet before asynchronous host IPC:
                # circle candidates must not accumulate punches or body edges.
                self.cancel_input();self.gesture.reset()
                if hasattr(self,'body_control'):self.body_control.consume(pose)
                suite=(pose.get('suite') or {}) if isinstance(pose,dict) else {}
                if suite.get('active') and (suite.get('menu') or suite.get('mode')!='fight'):
                    self.last_status='菜单已打开 · 格斗已暂停';self.area.queue_draw();return True
                menu_candidate=True
            moves=self.gesture.consume(pose);guard=self.gesture.guard;valid=self.gesture.valid
            if hasattr(self,'body_control'):
                body=self.body_control.consume(pose,guard=guard)
                self.body_state=body
                control=dict(move_axis=body['move_axis'],crouch_guard=body['crouch_guard'],jump='jump' in body['events'])
                kicks=[move for move in body['events'] if move.startswith('kick_')]
                if kicks:moves=kicks
            if menu_candidate:moves=[];guard=False;valid=False
            self.last_status='守势 · 看准敌方红字，举臂格挡' if guard else getattr(self.gesture,'hint','') or ('双肩、双肘、双腕需完整入镜' if not valid else '收拳再伸拳｜屈肘上挑｜双掌向前推｜气满双手高举')
            if menu_candidate:self.last_status='菜单手势候选 · 暂停出招'
            if not valid:
                self.cancel_input()
                guard=False
            else:
                if not getattr(self.gesture,'both_valid',True):self.cancel_input()
            if self.source_gap.update(pose,now):
                if self.game.state in ('playing','countdown'):
                    self.before_pause=self.game.state;self.game.state='paused';self.tracking_pause=True
            elif self.tracking_pause and not self.help_open:
                self.game.state=self.before_pause;self.tracking_pause=False;self.gesture.reset();moves=[]
                self.cancel_input();control={};body={};guard=False
                if hasattr(self,'body_control'):self.body_control.reset()
            if valid:
                for move in moves:self.perform(move)
            if not valid:control={}
            elif body.get('status')=='calibrating':self.last_status='自然站立片刻 · 校准进退与腿法'
            elif body.get('reason')=='legs_unavailable_upper_body_active':
                self.last_status+=' · 膝踝未入镜，拳法与横移可用'
            elif body.get('reason')=='scale_unstable_lateral_active':
                self.last_status+=' · 稳住上身后恢复腿法，横移可用'
        else:
            crouch=bool(self.keys.intersection((Gdk.KEY_s,Gdk.KEY_Down)))
            guard=guard or crouch
            control=dict(move_axis=float(bool(self.keys.intersection((Gdk.KEY_d,Gdk.KEY_Right))))-
                          float(bool(self.keys.intersection((Gdk.KEY_a,Gdk.KEY_Left)))),
                         crouch_guard=crouch,jump=getattr(self,'manual_jump',False))
            self.manual_jump=False
            self.last_status='A/D 进退 · W 跳 · S 蹲防 · Z/X 踢腿 · J/K 拳 · L 防御'
        if self.args.benchmark and self.game.state=='playing':
            step=int(self.game.time/1.1)
            if step!=getattr(self,'bench_step',-1):
                self.bench_step=step;move=('left','right','uppercut_left','palm','special')[step%5]
                if move=='special':self.game.energy=100
                self.perform(move)
            guard=step%3==0
        self.game.advance(dt,guard,control)
        if self.game.state=='over' and not self.round_export.saved and now>=self.round_save_retry:
            try:self.round_export.finish(self.game)
            except (OSError,ValueError):
                self.round_save_retry=now+5.;self.input_feedback='本局战绩保存失败，稍后自动重试';self.feedback_until=now+4.
        if self.game.state=='playing' and not self.help_open:
            queued_move=self.input_gate.pending['move'] if self.input_gate.pending else ''
            result=self.input_gate.flush(self.game,time.monotonic())
            if result in ('accepted','expired','no_energy'):self.feedback(queued_move,result)
        else:self.cancel_input()
        if recorder:
            recorder.observe(pose,self.game,moves,self.input_feedback,self.gesture.valid,self.gesture.both_valid)
            if recorder.started and not recorder.closed and (self.game.state=='over' or now-recorder.started_at>=120):
                recorder.finish('round_over' if self.game.state=='over' else 'wall_timeout',self.input_events)
                if self.game.state!='over':self.game.state='paused';self.tracking_pause=False
        if now<self.feedback_until and (not self.reader or self.gesture.valid):self.last_status=self.input_feedback
        if self.game.state=='finishing':self.last_status='胜负已定 · 看完这一招，再续山河'
        if self.args.benchmark and self.game.state=='over':self.game.start();self.game.countdown=0;self.bench_step=-1
        sig=(self.game.state,self.tracking_pause,self.help_open,math.ceil(getattr(self.game,'countdown',0)))
        if sig!=getattr(self,'sig',None):self.area.queue_draw()
        elif self.game.state in ('playing','finishing') and not self.help_open:
            w=self.area.get_allocated_width();h=self.area.get_allocated_height();s=min(w/W,h/H);ox=(w-W*s)/2;oy=(h-H*s)/2
            self.area.queue_draw_area(0,int(oy+134*s),w,math.ceil(492*s))
            hud=(self.game.hp,self.game.enemy_hp,int(getattr(self.game,'energy',0)),math.ceil(self.game.remaining))
            if hud!=getattr(self,'hud',None):self.area.queue_draw_area(0,0,w,math.ceil(oy+134*s))
            if self.last_status!=getattr(self,'footer',None):self.area.queue_draw_area(0,int(oy+626*s),w,math.ceil(90*s))
            self.hud=hud;self.footer=self.last_status
        self.sig=sig;return True
    def draw(self,_,target):
        begin=time.monotonic();w=self.area.get_allocated_width();h=self.area.get_allocated_height()
        surf=self.scene.paint(self.game,w,h,self.last_status,self.tracking_pause,self.help_open)
        surf=self.presenter.convert(surf,target.get_target(),self.area.get_visual())
        target.set_source_surface(surf,0,0);target.paint()
        self.frames.append(time.monotonic());self.draw_ms.append((time.monotonic()-begin)*1000)
        self.frames=self.frames[-3600:];self.draw_ms=self.draw_ms[-3600:]
    def finish(self):
        a=sorted(self.draw_ms);report=dict(camera_used=False,frames=len(a),fps=(len(self.frames)-1)/(self.frames[-1]-self.frames[0]),draw_p95_ms=a[int(len(a)*.95)],size=self.scene.size,moves_accepted=self.moves_accepted)
        Path(self.args.report).write_text(json.dumps(report,indent=2));self.scene.surface.write_to_png(str(Path(self.args.report).with_suffix('.png')))
        self.destroy();return False
    def close(self,*_):
        if self.reader:self.reader.closed=True
        if self.recorder:self.recorder.finish('window_closed',self.input_events)
        self.cancel_input()
        if not self.args.benchmark:
            try:
                Path(__file__).with_name('last-input-report.json').write_text(json.dumps(dict(version='3.0',moves_received=self.moves_received,moves_accepted=self.moves_accepted,events=list(self.input_events)),ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError:pass
        Gtk.main_quit()
def main():
    p=argparse.ArgumentParser();p.add_argument('--pose',action='store_true');p.add_argument('--benchmark',type=int,default=0);p.add_argument('--report',default='fight-v2-benchmark.json')
    p.add_argument('--live-test',action='store_true');p.add_argument('--record-session')
    a=p.parse_args()
    if (a.live_test or a.record_session) and (not a.pose or a.benchmark or not a.live_test or not a.record_session):p.error('Live recording requires --pose --live-test --record-session DIR, without benchmark')
    window=Window(a);Gtk.main()
if __name__=='__main__':main()
