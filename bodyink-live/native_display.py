"""Full-width ink canvas: animation and real inference state, no camera video."""
import json,threading,time,urllib.request,urllib.error,os,math
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GdkPixbuf,GLib
from creation_guide import THEMES,guide_text,stage_text
from PIL import Image
from animated_artwork import AnimatedArtwork
from animation_controller import AnimationController
from animation_scene import SceneFollower
from network_share import archive_saved,fetch_share,ShareUnavailable

BASE='http://127.0.0.1:8088'
def art_bounds(width,height):
    # Camera pixels are never requested or rendered on the creative canvas.
    return max(240,width-32),max(180,height-160)

class Display:
    def __init__(self,window_factory=None):
        self.stop=threading.Event();self.lock=threading.Lock();self.pending={};self.scheduled=False
        self.current_theme='mixed';self.enabled_actions=['A01','A02','A03','A04','A06']
        self.share_dialog=None;self.share_pending=False;self.share_queued=None
        self.window=window_factory() if window_factory else Gtk.Window(title='墨迹 · 身随墨动 · 初代');self.window.set_default_size(940,520)
        screen=self.window.get_screen();area=screen.get_monitor_workarea(screen.get_primary_monitor())
        self.art_bounds=art_bounds(area.width,area.height)
        self.window.maximize()
        self.window.connect('key-press-event',self.key_press)
        self.window.connect('destroy',self.close)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);box.set_border_width(8);self.window.add(box)
        header=Gtk.Box(spacing=8);box.pack_start(header,False,False,0)
        self.title=Gtk.Label(label='墨迹 · 身随墨动  |  混合主题  |  已落墨 0 次');self.title.set_xalign(0);header.pack_start(self.title,True,True,0)
        instruction=Gtk.Button(label='动作说明');instruction.connect('clicked',self.show_guide);header.pack_start(instruction,False,False,0)
        network_button=Gtk.Button(label='扫码作品');network_button.connect('clicked',lambda *_:self.share_work());header.pack_start(network_button,False,False,0)
        self.art=AnimatedArtwork(Image.new('RGB',(1280,720),(246,243,232)));box.pack_start(self.art,True,True,0)
        self.animation=AnimationController(self.art);self.animation_slots=threading.Semaphore(2)
        status_row=Gtk.Box(spacing=12);box.pack_start(status_row,False,False,0)
        self.status=Gtk.Label(label='等待运行数据');self.status.set_xalign(0);self.status.set_line_wrap(True);self.status.set_max_width_chars(48);status_row.pack_start(self.status,True,True,0)
        self.metrics=Gtk.Label();self.metrics.set_xalign(1);status_row.pack_start(self.metrics,False,False,0)
        self.step=Gtk.Label(label='自然做动作 → 识别即落墨 → 放下再做');self.step.set_xalign(0);box.pack_start(self.step,False,False,0)
        self.notice=Gtk.Label();self.notice.set_line_wrap(True);box.pack_start(self.notice,False,False,0)
        buttons=Gtk.Box(spacing=6);box.pack_start(buttons,False,False,0)
        for label,command,extra in [('混合','theme',{'theme':'mixed'}),('山水','theme',{'theme':'landscape'}),('竹','theme',{'theme':'bamboo'}),('梅','theme',{'theme':'plum'}),('撤销','undo',{}),('清空','clear',{}),('保存','save',{})]:
            button=Gtk.Button(label=label);button.connect('clicked',self.command,command,extra);buttons.pack_start(button,True,True,0)
        exit_button=Gtk.Button(label='退出');exit_button.connect('clicked',lambda b:self.window.destroy());buttons.pack_start(exit_button,True,True,0)
        self.window.show_all()
        for f in (self.fetch_state,self.fetch_animation):threading.Thread(target=f,daemon=True).start()
    def show_guide(self,*args):
        dialog=Gtk.Dialog(title='如何完成一幅水墨作品',transient_for=self.window,modal=True)
        dialog.set_default_size(650,440);dialog.add_button('返回创作',Gtk.ResponseType.CLOSE)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        label=Gtk.Label(label=guide_text(self.current_theme,self.enabled_actions));label.set_line_wrap(True);label.set_max_width_chars(48);label.set_xalign(0);label.set_margin_start(16);label.set_margin_end(16);label.set_margin_top(12);label.set_margin_bottom(12)
        scroll.add(label);dialog.get_content_area().pack_start(scroll,True,True,0)
        dialog.show_all();dialog.run();dialog.destroy()
    def key_press(self,widget,event):
        if event.keyval==Gdk.KEY_Escape:self.window.destroy();return True
        return False
    def close(self,*args):
        self.stop.set();self.share_queued=None
        if self.share_dialog:self.share_dialog.destroy();self.share_dialog=None
        self.animation.cancel();Gtk.main_quit()
    def request(self,path):
        return urllib.request.urlopen(BASE+path,timeout=3)
    def publish(self,key,value):
        with self.lock:
            self.pending[key]=value
            if not self.scheduled:self.scheduled=True;GLib.idle_add(self.update)
    def update(self):
        with self.lock:pending=self.pending;self.pending={};self.scheduled=False
        if self.stop.is_set():return False
        if 'state' in pending:
            s=pending['state'];self.status.set_text(s.get('message',''))
            self.current_theme=s.get('theme','mixed');self.enabled_actions=s.get('enabled_actions',self.enabled_actions)
            self.title.set_text('墨迹 · 身随墨动  |  '+THEMES.get(self.current_theme,'混合')+'主题  |  已落墨 '+str(s.get('event_count',0))+' 次')
            self.step.set_text(stage_text(s.get('phase'))+'  ·  自然做动作 → 识别即落墨 → 放下再做')
            def number(k):v=s.get(k);return '—' if v is None else '%.1f'%v
            self.metrics.set_text('识别 '+number('pose_fps')+' FPS  ·  延迟 '+number('latency_ms')+' ms')
        if 'notice' in pending:self.notice.set_text(pending['notice'])
        return False
    def fetch_state(self):
        revision=None;waiting=False
        while not self.stop.is_set():
            try:
                with self.request('/api/state') as f:s=json.load(f)
                self.publish('state',s)
                if waiting:self.publish('notice','');waiting=False
                revision=s.get('art_revision')
            except Exception:self.publish('notice','正在等待识别服务');waiting=True
            self.stop.wait(.3)
    def fetch_animation(self):
        follower=SceneFollower();previous=None
        while not self.stop.is_set():
            try:
                barrier=self.animation.barrier
                with self.request('/api/state') as f:state=json.load(f)
                key=(state.get('pid'),state.get('art_revision'),barrier)
                if key!=previous:
                    with self.request('/api/art-scene') as f:scene=json.load(f)
                    batch=follower.prepare(scene,barrier)
                    previous=key
                    if batch is not None:
                        while not self.stop.is_set():
                            if self.animation_slots.acquire(timeout=.2):
                                GLib.idle_add(self.deliver_animation,batch);break
            except Exception:
                previous=None;self.publish('notice','画作同步中，请稍候。')
            self.stop.wait(.15)
    def deliver_animation(self,batch):
        try:
            if not self.stop.is_set():self.animation.receive(batch)
        finally:self.animation_slots.release()
        return False
    def share_work(self,name=None):
        if self.stop.is_set():return
        if self.share_pending:
            # Keep at most the newest saved artwork. Repeated homepage clicks
            # cannot replace a pending artwork-specific download request.
            if name is not None:self.share_queued=(name,)
            return
        self.share_pending=True
        def fetch():
            try:
                if self.stop.is_set():return
                share=fetch_share(name)
                GLib.idle_add(self.show_share,share)
            except ShareUnavailable as error:
                self.publish('notice',('作品已保存；' if name else '')+str(error))
            finally:GLib.idle_add(self.share_finished)
        threading.Thread(target=fetch,daemon=True).start()
    def share_finished(self):
        self.share_pending=False
        queued=self.share_queued;self.share_queued=None
        if queued and not self.stop.is_set():self.share_work(queued[0])
        return False
    def show_share(self,share):
        if self.stop.is_set() or self.share_queued is not None:return False
        try:
            loader=GdkPixbuf.PixbufLoader.new_with_type('png')
            loader.write(share['png']);loader.close();pixbuf=loader.get_pixbuf()
            # Integer nearest-neighbour scaling keeps QR modules sharp.
            factor=max(1,math.ceil(240/max(pixbuf.get_width(),pixbuf.get_height())))
            if factor>1:pixbuf=pixbuf.scale_simple(pixbuf.get_width()*factor,pixbuf.get_height()*factor,GdkPixbuf.InterpType.NEAREST)
            if max(pixbuf.get_width(),pixbuf.get_height())>320:
                size=280;pixbuf=pixbuf.scale_simple(size,size,GdkPixbuf.InterpType.NEAREST)
            if self.share_dialog:self.share_dialog.destroy()
            dialog=Gtk.Dialog(title='扫码带走作品' if share['name'] else '手机访问墨迹工坊',transient_for=self.window,modal=False)
            self.share_dialog=dialog;dialog.add_button('返回',Gtk.ResponseType.CLOSE)
            box=dialog.get_content_area();box.set_spacing(8);box.set_border_width(14)
            title=Gtk.Label(label='扫码登录并关联本次游玩' if share['name'] else '扫码查看自己的作品与战绩')
            box.pack_start(title,False,False,0)
            box.pack_start(Gtk.Image.new_from_pixbuf(pixbuf),False,False,0)
            link=Gtk.Label(label=share['url']);link.set_selectable(True);link.set_line_wrap(True);link.set_max_width_chars(44)
            box.pack_start(link,False,False,0)
            hint=Gtk.Label(label='请连接展台 Wi-Fi，登录后即可查看并保存作品。');hint.set_line_wrap(True);box.pack_start(hint,False,False,0)
            dialog.connect('response',lambda d,_:d.destroy())
            dialog.connect('destroy',self.share_closed)
            dialog.show_all()
        except Exception:self.publish('notice','作品已保存；二维码显示失败，可稍后点击网络作品重试。')
        return False
    def share_closed(self,dialog):
        if self.share_dialog is dialog:self.share_dialog=None
    def command(self,button,command,extra):
        button.set_sensitive(False)
        if command in ("undo","clear"):self.animation.cancel()
        def send():
            try:
                request=urllib.request.Request(BASE+'/api/command',data=json.dumps(dict(command=command,**extra)).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=15) as f:r=json.load(f)
                if command=='save' and r.get('accepted'):
                    message='已保存到板卡 works/'+os.path.basename(r.get('path',''))+'（含事件 JSON）'
                    try:
                        archived=archive_saved(r['path'],marks=r.get('event_count',0))
                    except Exception as exc:
                        archived=None;message+='；游客作品集保存失败：'+str(exc)
                    if archived:GLib.idle_add(self.share_work,os.path.basename(r['path']))
                else:message=r.get('reason') or r.get('message') or ('操作完成' if r.get('accepted') else '操作未完成')
                self.publish('notice',message)
                if command in ("undo","clear"):GLib.idle_add(self.animation.cancel)
            except Exception:self.publish('notice','操作失败，请重试')
            finally:GLib.idle_add(button.set_sensitive,True)
        threading.Thread(target=send,daemon=(command!='save')).start()

if __name__=='__main__':Display();Gtk.main()
