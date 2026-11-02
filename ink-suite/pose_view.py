"""Independent camera + backend skeleton view, not an ink/game renderer."""
import threading
import time
import urllib.request
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GdkPixbuf,GLib
from pose_stream import read_frame,fresh_frame
from radial_layout import observed_pose
from render_policy import RenderPolicy,present

BACK_ZONE=((.754,.889,.992,.985),)


class PoseView(Gtk.Box):
    choices=('pose_back',)
    @property
    def rectangles(self):
        # GTK allocates the footer independently from the video canvas. Use the
        # actual displayed button for hand selection at every screen size.
        if not hasattr(self,'return_button') or not self.get_mapped():return BACK_ZONE
        width,height=self.get_allocated_width(),self.get_allocated_height()
        origin=self.return_button.translate_coordinates(self,0,0)
        if origin is None or width<=1 or height<=1:return BACK_ZONE
        x,y=origin;w=self.return_button.get_allocated_width();h=self.return_button.get_allocated_height()
        return ((x/width,y/height,(x+w)/width,(y+h)/height),)
    def __init__(self,base,back,enabled=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,spacing=0)
        self.base=base;self.back=back;self.enabled=enabled
        self.epoch=0;self.stop_event=None;self.response=None;self.frame=None;self.received=None;self.timer=None
        self.lock=threading.Lock();self.pending=None;self.scheduled=False;self.metrics_text='识别 — FPS  ·  延迟 — ms'
        self.policy=RenderPolicy.from_environment();self.buffer=None
        self.canvas=Gtk.DrawingArea();self.canvas.set_hexpand(True);self.canvas.set_vexpand(True)
        self.canvas.connect('draw',self.draw);self.pack_start(self.canvas,True,True,0)
        footer=Gtk.Box(spacing=12);footer.set_border_width(8)
        self.info=Gtk.Label(label='摄像头 + 人体骨架 · 正在连接…');self.info.set_xalign(0);self.info.set_ellipsize(3)
        footer.pack_start(self.info,True,True,0)
        self.return_button=Gtk.Button(label='返回主菜单');self.return_button.set_size_request(228,52)
        self.return_button.get_style_context().add_class('pose-return')
        css=Gtk.CssProvider();css.load_from_data(b'.pose-return {font-size:16px;padding:6px 14px;}')
        self.return_button.get_style_context().add_provider(css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.return_button.connect('clicked',lambda *_:self.back());footer.pack_start(self.return_button,False,False,0)
        self.pack_start(footer,False,False,0)
        self.connect('map',lambda *_:self.start_preview())
        self.connect('unmap',lambda *_:self.stop_preview())
        self.connect('destroy',lambda *_:self.stop_preview())
    def start_preview(self):
        if not self.enabled or self.stop_event is not None:return
        self.epoch+=1;epoch=self.epoch;stop=threading.Event();self.stop_event=stop
        self.frame=None;self.received=None;self.info.set_text('摄像头 + 人体骨架 · 正在连接…')
        self.timer=GLib.timeout_add(100,self.expire)
        def run():
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while not stop.is_set():
                try:
                    with opener.open(self.base+'/preview.rgbstream',timeout=3) as response:
                        if stop.is_set():return
                        with self.lock:self.response=(epoch,response)
                        while not stop.is_set():
                            frame=read_frame(response)
                            if not fresh_frame(frame):
                                GLib.idle_add(self.preview_status,epoch,'摄像头画面已过期，正在等待新画面…')
                                continue
                            with self.lock:
                                if epoch!=self.epoch:return
                                self.pending=(epoch,frame)
                                if not self.scheduled:
                                    self.scheduled=True;GLib.idle_add(self.deliver)
                except (Exception,):
                    if not stop.is_set():
                        GLib.idle_add(self.preview_status,epoch,'摄像头画面暂不可用，正在自动重连…')
                        stop.wait(.5)
                finally:
                    with self.lock:
                        if self.response and self.response[0]==epoch:self.response=None
        threading.Thread(target=run,daemon=True,name='pose-camera-preview').start()
    def preview_status(self,epoch,message):
        if epoch==self.epoch and self.get_mapped() and self.frame is None:
            self.info.set_text(message)
        return False
    def stop_preview(self):
        self.epoch+=1
        if self.stop_event:self.stop_event.set();self.stop_event=None
        if self.timer:GLib.source_remove(self.timer);self.timer=None
        with self.lock:old=self.response;self.response=None;self.pending=None
        # Closing a socket must not block GTK; a read also has a 3-second bound.
        if old:threading.Thread(target=old[1].close,daemon=True).start()
        self.frame=None;self.received=None
    def deliver(self):
        with self.lock:sample=self.pending;self.pending=None;self.scheduled=False
        if not sample or sample[0]!=self.epoch or not self.get_mapped():return False
        data,w,h,version,age=sample[1]
        self.frame=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data),GdkPixbuf.Colorspace.RGB,False,8,w,h,w*3)
        self.received=time.monotonic();self.canvas.queue_draw();self.info.set_text(self.metrics_text+'  ·  右手停留右下角返回')
        return False
    def update_state(self,state):
        _,fps,latency,_=observed_pose(state)
        self.metrics_text='识别 '+('—' if fps is None else '%.1f'%fps)+' FPS  ·  延迟 '+('—' if latency is None else '%.1f'%latency)+' ms'
        if self.frame is not None:self.info.set_text(self.metrics_text+'  ·  右手停留右下角返回')
    def update_selection(self,result):
        progress=result.get('progress',0) if result.get('hover')==0 else 0
        label='返回主菜单' if progress<=0 else '返回主菜单 · %d%%'%round(progress*100)
        if self.return_button.get_label()!=label:self.return_button.set_label(label)
    def expire(self):
        if self.stop_event is None:return False
        if self.frame is not None and self.received is not None and time.monotonic()-self.received>.75:
            self.frame=None;self.canvas.queue_draw();self.info.set_text('摄像头画面暂不可用，正在自动重连…')
        return True
    def draw(self,widget,target):
        ow,oh=widget.get_allocated_width(),widget.get_allocated_height()
        if ow<1 or oh<1:return False
        layout=self.policy.layout(ow,oh);w,h=layout.render_size
        if self.buffer is None or (self.buffer.get_width(),self.buffer.get_height())!=(w,h):
            self.buffer=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.buffer);cr.set_source_rgb(.08,.12,.11);cr.paint()
        if self.frame is not None:
            sw,sh=self.frame.get_width(),self.frame.get_height();scale=min(w/sw,h/sh)
            cr.translate((w-sw*scale)/2,(h-sh*scale)/2);cr.scale(scale,scale)
            Gdk.cairo_set_source_pixbuf(cr,self.frame,0,0);cr.get_source().set_filter(cairo.FILTER_NEAREST);cr.paint()
        present(target,self.buffer,layout);return False
