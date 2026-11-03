#!/usr/bin/env python3
"""Native single-photo ink studio with a low-rate, unfiltered viewfinder."""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request
import uuid

import numpy as np
from PIL import Image
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
from photo_job import PhotoJobs, RenderCancelled
from photo_state import PhotoSession


def emit(event, **fields):
    try:print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)
    except (BrokenPipeError, OSError):pass


@dataclass
class Frame:
    rgb: np.ndarray
    points: object
    version: int
    age_ms: float
    received_at: float
    capture_monotonic: object = None
    capture_sequence: object = None


def read_frame(response, photo=False):
    """Preview ages include transfer time; a captured photo has no live deadline."""
    started=time.monotonic()
    if response.status==204:
        if photo:raise ValueError('No photo was captured')
        return None
    if response.status!=200:raise ValueError('Unexpected RGB response')
    headers=response.headers
    width=int(headers['X-Preview-Width']);height=int(headers['X-Preview-Height'])
    stride=int(headers['X-Preview-Stride']);version=int(headers['X-Preview-Version'])
    max_width,max_height=(1920,1080) if photo else (1280,720)
    if not (16<=width<=max_width and 16<=height<=max_height and stride==width*3):raise ValueError('Invalid RGB geometry')
    raw=response.read(width*height*3+1)
    if len(raw)!=width*height*3:raise ValueError('Incomplete RGB frame')
    points=None
    try:
        value=json.loads(headers.get('X-Points17','null'));pose_age=float(headers.get('X-Pose-Age-Ms','-1'))
        if value is not None and math.isfinite(pose_age) and 0<=pose_age<=150:
            value=np.asarray(value,dtype=np.float32)
            if value.shape==(17,3):points=np.nan_to_num(value,nan=-1.,posinf=-1.,neginf=-1.)
    except (ValueError,TypeError):pass
    age=float(headers.get('X-Frame-Age-Ms','0'))
    if not math.isfinite(age) or age<0:raise ValueError('Invalid RGB frame age')
    received=time.monotonic();age+=(received-started)*1000
    if not photo and age>500:raise ValueError('Viewfinder frame is stale')
    capture_stamp=capture_sequence=None
    if photo:
        capture_stamp=float(headers['X-Capture-Monotonic']);capture_sequence=int(headers['X-Capture-Sequence'])
        if not math.isfinite(capture_stamp) or capture_stamp<=0 or capture_sequence<0:raise ValueError('Invalid photo source identity')
    return Frame(np.frombuffer(raw,np.uint8).reshape(height,width,3),points,version,age,received,capture_stamp,capture_sequence)


def save_art(rgb,directory,metadata=None):
    """Only the displayed stylized result and its photo identity reach disk."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    name='ink-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.png'
    target=directory/name;temporary=directory/('.'+name+'-'+uuid.uuid4().hex+'.tmp');sidecar=target.with_suffix('.json')
    record=dict(schema_version=1,theme='environment',title='眼前风景 · 水墨留影',
        created_at=datetime.now(timezone.utc).isoformat(),renderer='single-photo-color-ink',events=[],width=rgb.shape[1],height=rgb.shape[0])
    record.update(metadata or {})
    try:
        Image.fromarray(rgb,'RGB').save(temporary,format='PNG',compress_level=1);temporary.replace(target)
        temporary.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False),encoding='utf-8');temporary.replace(sidecar)
    finally:temporary.unlink(missing_ok=True)
    return target


def synthetic_rgb():
    y,x=np.mgrid[0:360,0:640];rgb=np.empty((360,640,3),np.uint8);rgb[:]=(227,223,211)
    rgb[y>270]=(113,128,111);rgb[(x-190)**2+(y-130)**2<65**2]=(63,91,77)
    rgb[(abs(x-410)<40)&(y>150)&(y<290)]=(85,108,126);rgb[(x-410)**2+(y-112)**2<35**2]=(181,142,113)
    return rgb


class EnvironmentPanel(Gtk.Box):
    def __init__(self,args,on_back=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        self.args=args;self.on_back=on_back;self.stop=threading.Event();self.wake=threading.Event();self.lock=threading.RLock()
        root=Path(__file__).resolve().parent
        self.photo_jobs=PhotoJobs(runtime=root/'runtime')
        self.photo_settings=dict(model=str(root/'models'/'figure-ink-v3.onnx'),long_side=640,renderer='figure-ink-v3-onnx')
        self.photo_cancel=None;self.progress_source=None
        self.session=PhotoSession();self.pending_preview=None;self.present_scheduled=False
        self.current=None;self.current_metadata=None;self.artifact_id=None;self.pixbuf=None
        self.share_dialog=None;self.countdown_source=None;self.saving_artifacts=set()
        self.preview_thread=None;self.photo_threads=[];self.save_threads=[];self.offline_sequence=0;self.preview_delivered=False;self.ui_signature=None
        self.set_border_width(14)
        self.get_style_context().add_class('photo-panel')
        header=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=14)
        title=Gtk.Label();title.set_markup('<span size="19000" weight="bold" foreground="#283a34">照片入墨</span>')
        header.pack_start(title,False,False,0)
        caption=Gtk.Label(label='选好画面，倒数三声，定格成画');caption.set_xalign(0)
        header.pack_start(caption,True,True,0);self.pack_start(header,False,False,0)
        self.overlay=Gtk.Overlay();self.canvas=Gtk.DrawingArea()
        self.canvas.set_hexpand(True);self.canvas.set_vexpand(True);self.canvas.connect('draw',self.draw)
        self.overlay.add(self.canvas);self.pack_start(self.overlay,True,True,0)
        self.center=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        self.center.set_valign(Gtk.Align.CENTER);self.center.set_halign(Gtk.Align.CENTER)
        self.center.set_no_show_all(True);self.center.get_style_context().add_class('capture-overlay');self.center.set_border_width(18)
        self.countdown=Gtk.Label();self.center.pack_start(self.countdown,False,False,0)
        self.spinner=Gtk.Spinner();self.center.pack_start(self.spinner,False,False,0);self.overlay.add_overlay(self.center)
        bottom=Gtk.Box(spacing=10);self.status=Gtk.Label();self.status.set_xalign(0);self.status.set_ellipsize(3)
        bottom.pack_start(self.status,True,True,0)
        self.capture_button=Gtk.Button(label='拍摄 · 3秒倒计时');self.capture_button.get_style_context().add_class('capture-button')
        self.capture_button.connect('clicked',lambda *_:self.begin_capture());bottom.pack_start(self.capture_button,False,False,0)
        self.retake_button=Gtk.Button(label='重新取景');self.retake_button.set_no_show_all(True)
        self.retake_button.connect('clicked',lambda *_:self.retake());bottom.pack_start(self.retake_button,False,False,0)
        self.save_button=Gtk.Button(label='保存 / 扫码');self.save_button.set_no_show_all(True)
        self.save_button.connect('clicked',self.save);bottom.pack_start(self.save_button,False,False,0)
        self.back_button=Gtk.Button(label='返回国画分身' if on_back else '返回场景');self.back_button.connect('clicked',lambda *_:self.request_menu());bottom.pack_start(self.back_button,False,False,0)
        self.pack_start(bottom,False,False,0)
        css=Gtk.CssProvider();css.load_from_data(b'.photo-panel, .photo-panel box {background:#eee9dc;color:#34453d} .photo-panel button {padding:10px 16px;background:#faf6eb;color:#394c42;border-radius:8px} .photo-panel label {background:transparent} .photo-panel .capture-button {font-size:18px;padding:12px 22px;background:#dce7df} .photo-panel .capture-overlay {background:rgba(243,237,221,0.94);border-radius:18px}')
        self.get_style_context().add_provider_for_screen(Gdk.Screen.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));self.offline_rgb=None;self.offline_preview_rgb=None
        if args.offline_preview:
            source=Image.open(args.offline_image).convert('RGB') if args.offline_image else Image.fromarray(synthetic_rgb())
            source.thumbnail((1920,1080));self.offline_rgb=np.array(source,dtype=np.uint8)
            self.offline_preview_rgb=np.array(source.resize((320,180)),dtype=np.uint8)
        if not args.embed_socket:self.session.resume()
        self.refresh_ui();self.preview_thread=threading.Thread(target=self.preview_worker,daemon=True);self.preview_thread.start()

    def close_share(self):
        if self.share_dialog:self.share_dialog.destroy()

    def cancel_countdown_timer(self):
        if self.countdown_source is not None:GLib.source_remove(self.countdown_source);self.countdown_source=None

    def cancel_progress_timer(self):
        if self.progress_source is not None:GLib.source_remove(self.progress_source);self.progress_source=None

    def progress_tick(self):
        with self.lock:busy=self.session.active and self.session.phase in ('capturing','processing')
        if not busy or self.stop.is_set():self.progress_source=None;return False
        self.refresh_ui();return True

    def operation(self,op):
        if self.stop.is_set():return False
        with self.lock:
            if op=='shutdown':self.session.shutdown();self.stop.set()
            elif op=='pause':self.session.pause()
            elif op=='resume':self.session.resume()
            else:return False
            if op!='resume':
                self.pending_preview=None
                if self.photo_cancel is not None:self.photo_cancel.set()
            if op=='resume' and self.session.phase!='result':self.pixbuf=None
        if op!='resume':self.cancel_countdown_timer();self.cancel_progress_timer();self.close_share()
        self.wake.set();self.canvas.queue_draw();self.refresh_ui()
        if op=='shutdown' and Gtk.main_level():Gtk.main_quit()
        return False

    def request_menu(self):
        self.operation('pause')
        if self.on_back is not None:self.on_back()
        else:emit('menu_requested')

    def key(self,_,event):
        if event.keyval in (Gdk.KEY_Escape,Gdk.KEY_Menu,Gdk.KEY_F1):self.request_menu();return True
        if event.keyval==Gdk.KEY_space:self.begin_capture();return True
        return False

    def begin_capture(self):
        with self.lock:
            if not self.session.start_countdown(time.monotonic()):return False
        self.close_share();self.wake.set();self.refresh_ui();self.countdown_source=GLib.timeout_add(50,self.countdown_tick)
        return True

    def countdown_tick(self):
        with self.lock:
            token=self.session.tick(time.monotonic());counting=self.session.phase=='countdown' and self.session.active
            if token is not None:self.photo_cancel=threading.Event();cancelled=self.photo_cancel
        self.refresh_ui()
        if token is not None:
            self.countdown_source=None;self.wake.set();self.progress_source=GLib.timeout_add(250,self.progress_tick)
            # Shutdown signals cancellation, then this non-daemon owner reaps the
            # child before Python exits (including normal SIGTERM shutdown).
            worker=threading.Thread(target=self.capture_photo,args=(token,cancelled),daemon=False)
            self.photo_threads=[t for t in self.photo_threads if t.is_alive()];self.photo_threads.append(worker);worker.start();return False
        if not counting:self.countdown_source=None;return False
        return True

    def retake(self):
        with self.lock:
            if not self.session.active:return False
            if self.photo_cancel is not None:self.photo_cancel.set()
            self.session.retake();self.pending_preview=None;self.current=self.current_metadata=self.artifact_id=self.pixbuf=None
        self.close_share();self.cancel_countdown_timer();self.cancel_progress_timer();self.wake.set();self.canvas.queue_draw();self.refresh_ui();return True

    def refresh_ui(self):
        if self.stop.is_set():return False
        with self.lock:
            phase=self.session.phase;active=self.session.active;remaining=self.session.remaining(time.monotonic());saving=self.artifact_id in self.saving_artifacts
            stage=self.session.stage;error=self.session.error;elapsed=self.session.elapsed(time.monotonic()) if phase in ('capturing','processing') else 0
        signature=phase,active,remaining,saving,stage,error,elapsed
        if signature==self.ui_signature:return False
        self.ui_signature=signature
        result=phase=='result';busy=phase in ('countdown','capturing','processing')
        self.capture_button.set_visible(not result);self.capture_button.set_sensitive(active and phase in ('viewfinder','error'))
        self.capture_button.set_label('重试拍摄 · 3秒' if phase=='error' else '拍摄 · 3秒倒计时')
        self.retake_button.set_visible(result or phase in ('capturing','processing','countdown'));self.retake_button.set_sensitive(active)
        self.retake_button.set_label('取消 / 重新取景' if busy else '重新取景')
        self.save_button.set_visible(result);self.save_button.set_sensitive(active and not saving)
        if busy:
            self.center.show();self.countdown.show()
            if phase=='countdown':
                self.countdown.set_markup('<span size="76000" weight="bold" foreground="#294e43">'+str(max(1,remaining))+'</span>');self.spinner.stop();self.spinner.hide()
            else:
                stages={'capturing':'正在拍下这一刻','starting_worker':'启动画作进程','loading_model':'载入国画模型','preparing_image':'铺设画面','painting':'绘制神经墨稿','finishing':'加深墨色与设色','finished':'呈现画作'}
                self.countdown.set_markup('<span size="17000" foreground="#294e43">'+stages.get(stage,'准备画作')+'…\n已等待 '+str(elapsed)+' 秒</span>');self.spinner.show();self.spinner.start()
        else:self.center.hide();self.spinner.stop()
        messages={'viewfinder':'选好画面后点击拍摄','countdown':'保持姿势，倒计时结束时拍摄','capturing':'正在拍摄，请稍候',
            'processing':'照片已拍好，正在创作水墨画','result':'作品已定格，可保存或重新取景','error':'拍摄未完成，请点击重试'}
        message='正在保存作品与准备二维码…' if result and saving else messages.get(phase,'')
        if phase=='error' and error:message='照片入墨未完成：'+error
        if phase=='processing':message='以最长边 640 像素创作 · 已等待 '+str(elapsed)+' 秒'
        if not active:message='已暂停 · 返回后继续'
        self.status.set_tooltip_text(message)
        if self.status.get_text()!=message:self.status.set_text(message)
        return False

    def preview_worker(self):
        after=-1;previous_generation=None;offline_version=0
        while not self.stop.is_set():
            with self.lock:enabled=self.session.active and self.session.phase in ('viewfinder','countdown');generation=self.session.generation
            if not enabled:self.wake.wait(.25);self.wake.clear();continue
            if generation!=previous_generation:after=-1;previous_generation=generation
            started=time.monotonic()
            try:
                if self.args.offline_preview:offline_version+=1;packet=Frame(self.offline_preview_rgb,None,offline_version,0.,time.monotonic())
                else:
                    with self.opener.open(self.args.base+'/environment.rgb?after='+str(after),timeout=1.2) as response:packet=read_frame(response)
                if packet is not None:
                    after=packet.version
                    with self.lock:
                        if self.session.active and self.session.phase in ('viewfinder','countdown') and generation==self.session.generation:self.pending_preview=(packet,generation)
                    self.schedule_preview()
            except Exception as error:print('viewfinder:',str(error),file=sys.stderr,flush=True);after=-1
            self.stop.wait(max(0.,1./min(5.,self.args.fps)-(time.monotonic()-started)))

    def schedule_preview(self):
        with self.lock:
            if self.present_scheduled or self.stop.is_set():return
            self.present_scheduled=True
        GLib.idle_add(self.present_preview)

    @staticmethod
    def make_pixbuf(rgb):
        return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgb.tobytes()),GdkPixbuf.Colorspace.RGB,False,8,rgb.shape[1],rgb.shape[0],rgb.shape[1]*3)

    def present_preview(self):
        with self.lock:
            self.present_scheduled=False;pending=self.pending_preview;self.pending_preview=None
            if self.stop.is_set() or not self.session.active or self.session.phase not in ('viewfinder','countdown') or pending is None:return False
            packet,generation=pending
            if generation!=self.session.generation:return False
        if packet.age_ms+(time.monotonic()-packet.received_at)*1000>500:return False
        self.pixbuf=self.make_pixbuf(packet.rgb);self.canvas.queue_draw()
        if not self.preview_delivered:self.preview_delivered=True;emit('frame_ready',width=packet.rgb.shape[1],height=packet.rgb.shape[0],kind='viewfinder')
        return False

    def valid_photo(self,token):
        with self.lock:return not self.stop.is_set() and self.session.active and token==self.session.generation and self.session.phase in ('capturing','processing')

    def capture_photo(self,token,cancelled):
        """One explicit countdown owns one POST and one cancellable process."""
        try:
            if not self.valid_photo(token):return
            self.photo_jobs.validate_resources(self.photo_settings)
            if cancelled.is_set() or not self.valid_photo(token):return
            if self.args.offline_preview:
                with self.lock:self.offline_sequence+=1;sequence=self.offline_sequence
                now=time.monotonic();packet=Frame(self.offline_rgb.copy(),None,sequence,0.,now,now,sequence)
            else:
                request=urllib.request.Request(self.args.base+'/environment/photo.rgb',data=b'{}',headers={'Content-Type':'application/json'})
                # This POST is deliberately never retried automatically.
                with self.opener.open(request,timeout=6.) as response:packet=read_frame(response,photo=True)
            with self.lock:
                if not self.session.processing(token) or self.stop.is_set():return
            GLib.idle_add(self.refresh_ui)
            started=time.monotonic();height,width=packet.rgb.shape[:2]
            art,render_metadata=self.photo_jobs.render(packet.rgb,self.photo_settings,cancelled,
                progress=lambda status:GLib.idle_add(self.photo_progress,token,status))
            elapsed=(time.monotonic()-started)*1000
            metadata=dict(capture_mode='single_photo',photo_source='offline' if self.args.offline_preview else 'shared_camera_photo',
                capture_sequence=packet.capture_sequence,capture_monotonic=packet.capture_monotonic,capture_width=width,capture_height=height,
                source_age_ms_at_receive=packet.age_ms,processing_ms=elapsed)
            metadata.update(render_metadata)
            art.setflags(write=False)
            if self.valid_photo(token):GLib.idle_add(self.deliver_photo,token,art,metadata)
        except RenderCancelled:pass
        except Exception as error:print('photo:',str(error),file=sys.stderr,flush=True);GLib.idle_add(self.photo_failed,token,str(error))

    def photo_progress(self,token,status):
        with self.lock:accepted=self.session.progress(token,status.get('stage'))
        if accepted:self.refresh_ui()
        return False

    def deliver_photo(self,token,art,metadata):
        if not self.valid_photo(token):return False
        pixbuf=self.make_pixbuf(art)
        with self.lock:
            if not self.session.complete(token):return False
            self.current=art;self.current_metadata=dict(metadata);self.artifact_id=uuid.uuid4().hex
            self.current_metadata['artifact_id']=self.artifact_id;self.pixbuf=pixbuf;self.pending_preview=None
        self.cancel_progress_timer();self.canvas.queue_draw();self.refresh_ui()
        emit('photo_ready',width=art.shape[1],height=art.shape[0],capture_sequence=metadata['capture_sequence'],processing_ms=metadata['processing_ms']);return False

    def photo_failed(self,token,error):
        with self.lock:accepted=self.session.fail(token,error)
        if accepted:self.cancel_progress_timer();self.refresh_ui();emit('photo_failed',retry_required=True,error=error)
        return False

    def draw(self,widget,cr):
        w,h=widget.get_allocated_width(),widget.get_allocated_height();cr.set_source_rgb(.975,.964,.935);cr.paint()
        if self.pixbuf is not None:
            pw,ph=self.pixbuf.get_width(),self.pixbuf.get_height();scale=min(w/pw,h/ph)
            cr.translate((w-pw*scale)/2,(h-ph*scale)/2);cr.scale(scale,scale);Gdk.cairo_set_source_pixbuf(cr,self.pixbuf,0,0);cr.paint()
        return False

    def result_matches(self,artifact_id,generation):
        with self.lock:return not self.stop.is_set() and self.session.active and self.session.phase=='result' and self.artifact_id==artifact_id and self.session.generation==generation

    def save(self,_button):
        with self.lock:
            if not self.session.active or self.session.phase!='result' or self.current is None or self.artifact_id in self.saving_artifacts:return
            rgb=self.current;metadata=dict(self.current_metadata);artifact_id=self.artifact_id;generation=self.session.generation;self.saving_artifacts.add(artifact_id)
        self.refresh_ui()
        def persist():
            try:
                path=save_art(rgb,self.args.works,metadata);emit('artwork_saved',name=path.name,capture_sequence=metadata['capture_sequence'])
            except Exception as exc:
                print('save:',str(exc),file=sys.stderr,flush=True)
                GLib.idle_add(self.save_finished,artifact_id,generation,None,None,str(exc));return
            if not self.args.offline_preview:
                try:
                    bridge=str(Path(__file__).resolve().parents[2]/'bodyink-live')
                    if bridge not in sys.path:sys.path.insert(0,bridge)
                    from network_share import archive_saved
                    archive_saved(path, '眼前风景 · 水墨写生')
                except Exception as exc:
                    GLib.idle_add(self.save_finished,artifact_id,generation,path,None,str(exc));return
            if self.args.offline_preview or not self.result_matches(artifact_id,generation):
                GLib.idle_add(self.save_finished,artifact_id,generation,path,None,None);return
            def share_work():
                share=error=None
                try:
                    from network_share import fetch_share
                    share=fetch_share(path.name)
                except Exception as exc:error=str(exc)
                GLib.idle_add(self.save_finished,artifact_id,generation,path,share,error)
            threading.Thread(target=share_work,daemon=True).start()
        # Finish the local PNG/sidecar on a normal process exit. QR networking
        # remains optional and cannot delay shutdown or lose the saved picture.
        worker=threading.Thread(target=persist,daemon=False)
        self.save_threads=[t for t in self.save_threads if t.is_alive()];self.save_threads.append(worker);worker.start()

    def save_finished(self,artifact_id,generation,path,share,error):
        with self.lock:self.saving_artifacts.discard(artifact_id)
        if self.stop.is_set():return False
        self.refresh_ui()
        if not self.result_matches(artifact_id,generation):return False
        if path is None:self.status.set_text('保存失败，请重试')
        elif share:self.status.set_text('作品已保存');self.show_share(share,artifact_id,generation)
        else:self.status.set_text(('作品已保存在板卡；'+error) if error else '作品已保存到本地')
        return False

    def show_share(self,share,artifact_id,generation):
        if not self.result_matches(artifact_id,generation):return False
        self.close_share();dialog=Gtk.Dialog(title='把眼前山水带走',transient_for=self.get_toplevel(),modal=False)
        dialog.add_button('返回作品',Gtk.ResponseType.CLOSE);box=dialog.get_content_area();box.set_border_width(16);box.set_spacing(10)
        box.pack_start(Gtk.Label(label='扫码登录并关联本次游玩'),False,False,0)
        loader=GdkPixbuf.PixbufLoader.new_with_type('png');loader.write(share['png']);loader.close();pix=loader.get_pixbuf();size=pix.get_width()
        if size<210:pix=pix.scale_simple(size*2,size*2,GdkPixbuf.InterpType.NEAREST)
        box.pack_start(Gtk.Image.new_from_pixbuf(pix),False,False,0)
        link=Gtk.Label(label=share['url']);link.set_selectable(True);box.pack_start(link,False,False,0)
        box.pack_start(Gtk.Label(label='手机与板卡需连接同一热点'),False,False,0)
        self.share_dialog=dialog;dialog.connect('response',lambda d,_:d.destroy())
        dialog.connect('destroy',lambda *_:setattr(self,'share_dialog',None));dialog.show_all();return False


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--embed-socket',type=int);parser.add_argument('--offline-preview',action='store_true')
    parser.add_argument('--offline-image',type=Path);parser.add_argument('--base','--base-url',dest='base',default='http://127.0.0.1:8088')
    parser.add_argument('--works',type=Path,default=Path('/home/ubuntu/bodyink-live/works'))
    parser.add_argument('--fps',type=float,default=4.,help='Raw viewfinder rate, at most five frames per second');args=parser.parse_args()
    if not 1<=args.fps<=5:parser.error('Viewfinder FPS must be within 1..5')
    if args.offline_image and not args.offline_preview:parser.error('--offline-image requires --offline-preview')
    window=Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='墨迹工坊 · 眼前山水')
    if not args.embed_socket:window.set_default_size(1024,600)
    panel=EnvironmentPanel(args);window.add(panel);window.connect('key-press-event',panel.key)
    window.connect('destroy',lambda *_:panel.operation('shutdown'));window.show_all();panel.refresh_ui()
    def commands():
        for line in sys.stdin:
            try:op=json.loads(line).get('op')
            except (ValueError,AttributeError):continue
            if op in ('pause','resume','shutdown'):GLib.idle_add(panel.operation,op)
        if args.embed_socket:GLib.idle_add(panel.operation,'shutdown')
    threading.Thread(target=commands,daemon=True).start()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:GLib.idle_add(panel.operation,'shutdown'))
    emit('ready',mode='environment');Gtk.main()
    panel.operation('shutdown')
    for worker in panel.photo_threads:worker.join()


if __name__=='__main__':main()
