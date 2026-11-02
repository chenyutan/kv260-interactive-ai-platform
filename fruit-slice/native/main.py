"""GTK3/Cairo native game. No HTML, WebKit, JavaScript or web server."""
import argparse,json,math,threading,time,urllib.request,uuid
from collections import deque,OrderedDict
from pathlib import Path
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib
from engine import Game,PoseInput,W,H,ORDINARY_FRUITS
from item_effects import ItemEffects
from cut_effects import CutEffects
from score_style import ScoreStyle

ART=Path(__file__).resolve().parents[1]/'art-v1'

class LatestPose:
    def __init__(self,url):
        self.url=url; self.value=None; self.received=0; self.enabled=False; self.closed=False
        threading.Thread(target=self.run,daemon=True).start()
    def run(self):
        last=None
        while not self.closed:
            if self.enabled:
                try:
                    with urllib.request.urlopen(self.url,timeout=.5) as r: s=json.load(r)
                    key=(s.get('pid'),s.get('sequence'),s.get('timestamp_seconds'))
                    if key!=last:
                        last=key; self.value=s; self.received=time.monotonic()
                except Exception: self.value=None
            time.sleep(.025)
    def get(self):
        return self.value if time.monotonic()-self.received<.3 else None

def resize(source,w,h,rect=None):
    out=cairo.ImageSurface(cairo.FORMAT_ARGB32,w,h); c=cairo.Context(out)
    sx,sy,sw,sh=rect or (0,0,source.get_width(),source.get_height())
    c.scale(w/sw,h/sh); c.set_source_surface(source,-sx,-sy); c.paint()
    return out

class Window(Gtk.Window):
    def __init__(self,args):
        super().__init__(title='彩墨切水果 · 原生版')
        self.set_default_size(1024,600); self.set_size_request(640,400)
        self.game=Game(); self.pose=PoseInput(self.game); self.reader=LatestPose(args.pose_url)
        self.score_style=ScoreStyle()
        self.item_fx=ItemEffects(); self.cut_fx=CutEffects(); self.mouse=False; self.use_pose=False; self.trails={}; self.halves=[]; self.splashes=[]
        self.last=time.monotonic(); self.render_times=deque(maxlen=600); self.ticks=deque(maxlen=600)
        self.rotations=OrderedDict(); self.background=None; self.cache_key=None; self.scale=1; self.origin=(0,0); self.frames=0; self.play_draws=deque(maxlen=3600)
        self.round_id=uuid.uuid4().hex; self.last_completed=None
        self.started=self.last; self.args=args; self.pose_packets=0; self.pose_valid=0; self._load()
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=0); self.add(box)
        bar=Gtk.Box(spacing=8); bar.set_border_width(6); box.pack_start(bar,False,False,0)
        for label,cb in [('开始 / 重来',self.start),('暂停 / 继续',self.pause),('全屏',self.full),('退出',lambda *_:self.destroy())]:
            b=Gtk.Button(label=label); b.connect('clicked',cb); bar.pack_start(b,False,False,0)
        toggle=Gtk.CheckButton(label='板卡双腕'); toggle.connect('toggled',self.mode); bar.pack_start(toggle,False,False,0)
        mirror=Gtk.CheckButton(label='镜像'); mirror.set_active(True); mirror.connect('toggled',self.mirror); bar.pack_start(mirror,False,False,0)
        self.mode_choice=Gtk.ComboBoxText(); self.mode_choice.append('arcade','进阶玩法'); self.mode_choice.append('classic','经典玩法'); self.mode_choice.set_active_id('arcade'); self.mode_choice.connect('changed',self.choose_rules); bar.pack_start(self.mode_choice,False,False,0)
        self.area=Gtk.DrawingArea(); box.pack_start(self.area,True,True,0)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK|Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect('draw',self.draw); self.area.connect('button-press-event',self.press); self.area.connect('button-release-event',self.release); self.area.connect('motion-notify-event',self.motion)
        self.connect('key-press-event',self.key); self.connect('focus-out-event',self.focus_out); self.connect('destroy',self.close)
        GLib.timeout_add(16,self.tick)
        self.show_all(); self.present()
        if args.pose: toggle.set_active(True)
        if args.live_test:
            toggle.set_active(True); self.fullscreen(); self.game.start(); self.game.countdown=25
            GLib.timeout_add_seconds(90,self.finish_live)
        if args.benchmark: self.fullscreen(); self.game.start(7); self.game.countdown=0; GLib.timeout_add_seconds(args.benchmark,self.finish_benchmark)

    def _load(self):
        self.raw={n:cairo.ImageSurface.create_from_png(str(ART/'sprites'/f)) for n,f in {
            'peach':'peach_whole.png','mandarin':'mandarin_whole.png','watermelon':'watermelon_whole.png',
            'double':'double_whole.png','ice':'ice_whole.png','bomb':'bomb.png','splash':'splash.png','background':'background.png','watermelon_cut':'watermelon_cut.png'}.items()}
        self.atlas=cairo.ImageSurface.create_from_png(str(ART/'review/cut_atlas_clean.png'))
        self.expansion=cairo.ImageSurface.create_from_png(str(ART/'sprites/fruit-expansion-v1.png'))

    def rebuild(self,w,h):
        self.scale=min(w/W,h/H); s=self.scale; self.origin=((w-W*s)/2,(h-H*s)/2)
        self.background=resize(self.raw['background'],max(1,round(W*s)),max(1,round(H*s)))
        size=max(1,round(120*s)); self.sprites={n:resize(self.raw[n],size,size) for n in ('peach','mandarin','watermelon','double','ice','bomb')}
        self.splash=resize(self.raw['splash'],round(170*s),round(170*s)); self.cuts={}
        cw,ch=self.atlas.get_width()/2,self.atlas.get_height()/4
        for row,n in enumerate(('peach','mandarin','double','ice')):
            self.cuts[n]=[resize(self.atlas,size,size,(col*cw,row*ch,cw,ch)) for col in (0,1)]
        self.cuts['watermelon']=[resize(self.raw['watermelon_cut'],size,size)]*2
        cw=self.expansion.get_width()/3
        rows=(0,420/1254,805/1254,1)
        for row,n in enumerate(ORDINARY_FRUITS[3:]):
            y=rows[row]*self.expansion.get_height(); ch=(rows[row+1]-rows[row])*self.expansion.get_height()
            self.sprites[n]=resize(self.expansion,size,size,(0,y,cw,ch))
            self.cuts[n]=[resize(self.expansion,size,size,(col*cw,y,cw,ch)) for col in (1,2)]
        scene=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h); sc=cairo.Context(scene)
        sc.set_source_rgb(.09,.14,.13); sc.paint(); sc.set_source_surface(self.background,round(self.origin[0]),round(self.origin[1])); sc.paint()
        scene.flush(); self.background_bytes=bytes(scene.get_data())
        self.rotations.clear(); self.cache_key=(w,h)

    def choose_rules(self,widget):
        if self.game.state in ('menu','over'):
            self.game.mode=widget.get_active_id(); self.area.queue_draw()
    def start(self,*_):
        self.round_id=uuid.uuid4().hex; self.last_completed=None
        self.game.start(); self.item_fx.clear(); self.cut_fx.clear(); self.pose.reset(); self.trails.clear(); self.halves.clear(); self.splashes.clear()
    def pause(self,*_):
        self.game.resume() if self.game.state=='paused' else self.game.pause()
        self.pose.reset(); self.trails.clear()
    def full(self,*_):
        if self.get_window().get_state() & Gdk.WindowState.FULLSCREEN:self.unfullscreen()
        else:self.fullscreen()
    def mode(self,b):
        self.use_pose=b.get_active(); self.reader.enabled=self.use_pose
        self.reader.value=None; self.reader.received=0; self.pose.reset(); self.game.paths.clear(); self.trails.clear()
    def mirror(self,b):self.pose.mirror=b.get_active(); self.pose.reset(); self.trails.clear()
    def key(self,_,e):
        if e.keyval==Gdk.KEY_Escape:self.game.pause(); self.unfullscreen()
        elif e.keyval==Gdk.KEY_space:self.pause(); return True
    def focus_out(self,*_):
        if not self.args.benchmark:self.game.pause(); self.pose.reset(); self.mouse=False
        return False
    def press(self,_,e):
        if e.button==1 and not self.use_pose:self.mouse=True; self.motion(_,e)
    def release(self,*_):self.mouse=False; self.game.paths.pop('mouse',None)
    def motion(self,_,e):
        if self.mouse and not self.use_pose:
            x=(e.x-self.origin[0])/self.scale; y=(e.y-self.origin[1])/self.scale
            self.game.input('mouse',x,y); self.trail('mouse',x,y)
    def trail(self,hand,x,y):
        now=time.monotonic(); path=self.trails.setdefault(hand,deque(maxlen=20))
        if path and (now-path[-1][2]>.24 or math.hypot(x-path[-1][0],y-path[-1][1])>520):path.clear()
        path.append((x,y,now))
    def tick(self):
        now=time.monotonic(); dt=now-self.last; self.last=now; self.game.advance(dt)
        self.mode_choice.set_sensitive(self.game.state in ('menu','over'))
        if self.game.state=='over' and self.last_completed is None:
            self.last_completed=dict(round_id=self.round_id,mode=self.game.mode,score=self.game.score,best_combo=self.game.best,fruits_cut=self.game.fruits_cut,bombs_hit=self.game.bombs_hit,missed=self.game.missed,elapsed=self.game.elapsed,completed=True)
            if not self.args.benchmark:
                folder=Path(__file__).resolve().parent/'rounds'; folder.mkdir(exist_ok=True)
                temp=folder/(self.round_id+'.tmp'); temp.write_text(json.dumps(self.last_completed,indent=2),encoding='utf-8'); temp.replace(folder/(self.round_id+'.json'))
        if self.use_pose:
            s=self.reader.get()
            points=self.pose.consume(s)
            if points:self.pose_valid+=1
            self.pose_packets+=1 if s else 0
            for hand,x,y in points:self.trail(hand,x,y)
            # Missing wrists fade naturally; no extrapolated collision input.
        if self.args.benchmark and self.game.state=='playing':
            # Synthetic input for rendering/load measurement only; no camera access.
            self.game.input('bench',640+420*math.sin(now*4),420+160*math.cos(now*3))
            if now-getattr(self,'bench_sample',0)>.067:
                self.bench_sample=now
                self.trail('left',640+420*math.sin(now*4),420+160*math.cos(now*3))
                self.trail('right',640+350*math.cos(now*3),350+180*math.sin(now*4))
        while self.game.events:
            e=self.game.events.popleft(); n=e['fruit'] if e['kind']=='normal' else e['kind']
            self.item_fx.trigger(e)
            self.cut_fx.trigger(e)
            if n not in ('double','ice','bomb'):self.splashes.append([e['x'],e['y'],.65,e['delta'],e['combo']])
            if n!='bomb':
                for i in (0,1):self.halves.append([n,i,e['x'],e['y'],135 if i else -135,-170,e['angle'],1.2])
        self.item_fx.advance(dt if self.game.state=='playing' else 0)
        self.cut_fx.advance(dt if self.game.state=='playing' else 0)
        self.halves=self.halves[-10:]; self.splashes=self.splashes[-5:]
        step=min(dt,.05) if self.game.state!='paused' else 0
        for v in self.halves:v[2]+=v[4]*step; v[3]+=v[5]*step; v[5]+=580*step; v[6]+=(1 if v[1] else -1)*step*1.7; v[7]-=step
        self.halves=[v for v in self.halves if v[7]>0]
        for v in self.splashes:v[2]-=step
        self.splashes=[v for v in self.splashes if v[2]>0]
        self.ticks.append(dt)
        for hand,path in self.trails.items():
            while path and now-path[0][2]>.32:path.popleft()
        menu_signature=(self.game.state,math.ceil(getattr(self.game,'countdown',0)),self.use_pose)
        if self.game.state!='playing' and menu_signature==getattr(self,'menu_signature',None) and self.cache_key:
            return True
        self.menu_signature=menu_signature
        if self.game.state!='playing' or self.cache_key is None or self.game.state!=getattr(self,'last_visual_state',None):
            self.area.queue_draw(); self.dirty_previous=[]
        else:
            bounds=[(o['x'],o['y'],95) for o in self.game.objects]
            bounds += [(v[2],v[3],100) for v in self.halves]
            bounds += [(v[0],v[1],125) for v in self.splashes]
            rects=[(x-r,y-r,2*r,2*r) for x,y,r in bounds]+self.item_fx.bounds()[:-1]+self.cut_fx.bounds()
            rects += [(v[0]-52,v[1]-124,264,100) for v in self.splashes]
            # Cover complete curves without refreshing an oversized square.
            for path in self.trails.values():
                if path:
                    xs=[v[0] for v in path]; ys=[v[1] for v in path]
                    rects.append((min(xs)-18,min(ys)-18,max(xs)-min(xs)+36,max(ys)-min(ys)+36))
            for x,y,w,h in rects+getattr(self,'dirty_previous',[]):
                self.area.queue_draw_area(int(self.origin[0]+x*self.scale),int(self.origin[1]+y*self.scale),math.ceil(w*self.scale)+2,math.ceil(h*self.scale)+2)
            banner_key=self.item_fx.banner_key()
            if banner_key!=getattr(self,'last_banner_key',None):
                self.area.queue_draw_area(int(self.origin[0]+390*self.scale),int(self.origin[1]+82*self.scale),math.ceil(500*self.scale)+2,math.ceil(92*self.scale)+2)
            self.last_banner_key=banner_key
            hud=(self.game.score,int(self.game.elapsed),int(self.game.double_until),int(self.game.freeze_until),self.use_pose,bool(self.reader.get()),self.game.charge,int(self.game.fury_until),self.game.combo)
            if hud!=getattr(self,'old_hud',None):
                self.area.queue_draw_area(0,0,self.area.get_allocated_width(),max(1,round(80*self.scale)))
                self.area.queue_draw_area(0,max(0,self.area.get_allocated_height()-80),self.area.get_allocated_width(),80)
            self.old_hud=hud; self.dirty_previous=rects
        self.last_visual_state=self.game.state
        return True

    def sprite(self,c,img,x,y,angle=0,alpha=1,flip=False):
        # Cache rotated surfaces; per-frame blits are integer-aligned, avoiding
        # repeated bilinear sampling of every semi-transparent fruit pixel.
        step=round(angle*48/math.tau)%48
        key=(id(img),step,flip)
        cached=self.rotations.get(key)
        if cached is None:
            size=math.ceil(math.hypot(img.get_width(),img.get_height()))+2
            cached=cairo.ImageSurface(cairo.FORMAT_ARGB32,size,size); rc=cairo.Context(cached)
            rc.translate(size/2,size/2); rc.rotate(step*math.tau/48)
            if flip:rc.scale(-1,1)
            rc.set_source_surface(img,-img.get_width()/2,-img.get_height()/2); rc.paint()
            self.rotations[key]=cached
            if len(self.rotations)>256:self.rotations.popitem(last=False)
        else:self.rotations.move_to_end(key)
        c.save(); c.set_source_surface(cached,round(self.origin[0]+x*self.scale-cached.get_width()/2),round(self.origin[1]+y*self.scale-cached.get_height()/2)); c.paint_with_alpha(max(0,min(1,alpha))); c.restore()
    def text(self,c,text,x,y,size=26):
        c.set_source_rgb(.13,.24,.20); c.select_font_face('Noto Sans CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD); c.set_font_size(size*self.scale)
        c.move_to(self.origin[0]+x*self.scale,self.origin[1]+y*self.scale); c.show_text(text)
    def draw(self,widget,target):
        w,h=self.area.get_allocated_width(),self.area.get_allocated_height()
        if not hasattr(self,'backbuffer') or (self.backbuffer.get_width(),self.backbuffer.get_height())!=(w,h):
            self.backbuffer=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        if (w,h)!=self.cache_key:self.rebuild(w,h)
        self.backbuffer.flush(); self.backbuffer.get_data()[:]=self.background_bytes; self.backbuffer.mark_dirty()
        self.paint(widget,cairo.Context(self.backbuffer))
        target.set_source_surface(self.backbuffer,0,0); target.paint()
    def paint(self,_,c):
        begin=time.monotonic(); w,h=self.area.get_allocated_width(),self.area.get_allocated_height()
        if (w,h)!=self.cache_key:self.rebuild(w,h)
        c.save(); c.rectangle(*self.origin,W*self.scale,H*self.scale); c.clip()
        for o in self.game.objects:
            n=o['fruit'] if o['kind']=='normal' else o['kind']; self.sprite(c,self.sprites[n],o['x'],o['y'],o['angle'])
        for n,i,x,y,vx,vy,a,life in self.halves:self.sprite(c,self.cuts[n][i],x,y,a,life*2,n=='watermelon' and i==1)
        c.save();c.translate(*self.origin);c.scale(self.scale,self.scale);self.cut_fx.paint(c);c.restore()
        for x,y,life,delta,combo in self.splashes:
            self.score_style.paint(c,self.origin[0]+(x-50)*self.scale,self.origin[1]+(y-100-22*(1-life/.65))*self.scale,
                                   f'{delta:+d}'+(f' · {combo}连切' if combo>=3 else ''),self.scale,alpha=min(1,life*6))
        now=time.monotonic()
        for hand,path in self.trails.items():
            p=list(path)
            if len(p)<2:continue
            c.save(); c.translate(*self.origin); c.scale(self.scale,self.scale)
            c.set_line_cap(cairo.LINE_CAP_ROUND); c.set_line_join(cairo.LINE_JOIN_ROUND)
            # Quadratic midpoint smoothing passes through the newest measured tip.
            for i in range(1,len(p)):
                a,b=p[i-1],p[i]
                start=((p[i-2][0]+a[0])/2,(p[i-2][1]+a[1])/2) if i>1 else a[:2]
                end=((a[0]+b[0])/2,(a[1]+b[1])/2) if i<len(p)-1 else b[:2]
                freshness=max(0,1-(now-b[2])/.32)
                width=(2+9*i/(len(p)-1))*freshness
                if width<.2:continue
                for extra,color in [(2,(.08,.23,.21,.95)),(-.72*width,(1,.93,.65,.95))]:
                    c.set_source_rgba(*color[:3],color[3]*freshness)
                    c.set_line_width(max(.6,width+extra))
                    c.move_to(*start)
                    c.curve_to(start[0]+2*(a[0]-start[0])/3,start[1]+2*(a[1]-start[1])/3,end[0]+2*(a[0]-end[0])/3,end[1]+2*(a[1]-end[1])/3,*end)
                    c.stroke()
            tip=p[-1]; alpha=max(0,1-(now-tip[2])/.32)
            c.set_source_rgba(1,.97,.83,alpha); c.arc(tip[0],tip[1],3.5,0,math.tau); c.fill(); c.restore()
        c.save();c.translate(*self.origin);c.scale(self.scale,self.scale);self.item_fx.paint(c);c.restore()
        self.score_style.paint(c,self.origin[0]+24*self.scale,self.origin[1],str(self.game.score),self.scale,hud=True)
        self.text(c,f'{max(0,math.ceil(60-self.game.elapsed))} 秒',1110,48)
        effects=[]
        if self.game.elapsed<self.game.double_until:effects.append(f'双倍 {math.ceil(self.game.double_until-self.game.elapsed)}s')
        if self.game.elapsed<self.game.freeze_until:effects.append(f'冰冻 {math.ceil(self.game.freeze_until-self.game.elapsed)}s')
        if self.game.furious:effects.append(f'狂热 {math.ceil(self.game.fury_until-self.game.elapsed)}s')
        self.text(c,'  '.join(effects),380,48,24)
        state=self.game.state
        if state in ('menu','paused','over','countdown'):
            c.set_source_rgba(.98,.95,.87,.88); c.rectangle(self.origin[0]+240*self.scale,self.origin[1]+220*self.scale,800*self.scale,260*self.scale); c.fill()
            title={'menu':'彩墨切水果','paused':'已暂停','over':f'本局 {self.game.score} 分 · 最长 {self.game.best} 连切','countdown':str(max(1,math.ceil(getattr(self.game,'countdown',0))))}[state]
            self.text(c,title,340,310,46); self.text(c,(f'切中 {self.game.fruits_cut} 个  ·  炸弹 {self.game.bombs_hit} 次  ·  漏果 {self.game.missed} 个' if state=='over' else '三连起加分 · 能量满进入狂热' if self.game.mode=='arcade' else '普通 +10 · 金梨翻倍 · 冰果减速 · 炸弹 −30'),285,380,24)
            self.text(c,'点击上方开始，挥动手腕切水果；Esc 暂停',320,430,23)
        status=('双腕已连接' if self.reader.get() else '等待腕点') if self.use_pose else '按住鼠标挥切'
        self.text(c,status,30,694,20)
        if self.game.mode=='arcade':
            self.text(c,f'第 {self.game.stage} 阶段 · '+('狂热中' if self.game.furious else f'能量 {self.game.charge}/12'),530,694,20)
            c.set_source_rgba(.17,.39,.31,.18); c.rectangle(self.origin[0]+530*self.scale,self.origin[1]+703*self.scale,300*self.scale,5*self.scale); c.fill()
            c.set_source_rgba(.77,.49,.16,.9); c.rectangle(self.origin[0]+530*self.scale,self.origin[1]+703*self.scale,300*self.scale*(1 if self.game.furious else self.game.charge/12),5*self.scale); c.fill()
        c.restore()
        self.render_times.append(time.monotonic()-begin); self.frames+=1
        if self.game.state=='playing': self.play_draws.append(time.monotonic())

    def finish_live(self):
        report={'mode':'live_wrists','score':self.game.score,'best_combo':self.game.best,'valid_wrist_updates':self.pose_valid,'fresh_state_ticks':self.pose_packets,'playing_draw_fps':(len(self.play_draws)-1)/(self.play_draws[-1]-self.play_draws[0]) if len(self.play_draws)>1 else 0}
        report.update(round_id=self.round_id,completed_round=self.last_completed)
        if self.last_completed:report['score']=self.last_completed['score']
        Path(self.args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
        return False

    def finish_benchmark(self):
        times=sorted(self.render_times)
        report={'native':True,'renderer':'GTK3/Cairo','camera_used':False,'size':list(self.cache_key),'seconds':time.monotonic()-self.started,'frames':self.frames,'playing_draw_fps':(len(self.play_draws)-1)/(self.play_draws[-1]-self.play_draws[0]) if len(self.play_draws)>1 else 0,'draw_ms_p50':times[len(times)//2]*1000,'draw_ms_p95':times[int(len(times)*.95)]*1000,'tick_fps':len(self.ticks)/sum(self.ticks)}
        if hasattr(self,'backbuffer'): self.backbuffer.write_to_png(str(Path(self.args.report).with_suffix('.png')))
        Path(self.args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
        self.destroy(); return False
    def close(self,*_):self.reader.closed=True; Gtk.main_quit()

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--pose-url',default='http://127.0.0.1:8088/api/state'); p.add_argument('--benchmark',type=int,default=0); p.add_argument('--live-test',action='store_true'); p.add_argument('--pose',action='store_true'); p.add_argument('--report',default='native-benchmark.json'); args=p.parse_args()
    window=Window(args); Gtk.main()
