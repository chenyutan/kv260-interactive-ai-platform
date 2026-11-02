#!/usr/bin/env python3
"""Native painted-avatar stage and optional single-photo studio.

Consumes only the existing pose API. There is no camera handle, image inference,
browser, or frame FIFO in this process. --offline-preview never uses the API.
"""
import argparse
from copy import copy
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from avatar.rig import Rig, neutral_pose
from avatar.renderer import Renderer

SCENES = (('bamboo', '竹影清风'), ('mountains', '溪山远岫'), ('plum', '梅庭听春'))
CHARACTERS = (('scholar', '青衫墨客'),)
_PHOTO_MODULE = None


def photo_module():
    global _PHOTO_MODULE
    if _PHOTO_MODULE is None:
        sys.path.insert(0, str(ROOT / 'environment'))
        spec = importlib.util.spec_from_file_location('ink_photo_panel', ROOT / 'environment/main.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _PHOTO_MODULE = module
    return _PHOTO_MODULE


def emit(event, **fields):
    try:
        print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)
    except (BrokenPipeError, OSError):
        pass


def demo_state(now):
    """Explicit synthetic rehearsal; no camera or saved person's likeness."""
    wave = math.sin(now * 1.05)
    points = [[.5, .5, 0.] for _ in range(17)]
    for index, value in {0:(.5,.23),5:(.43,.38),6:(.57,.38),
            7:(.34,.39-wave*.13),8:(.66,.39-wave*.13),
            9:(.28,.34-wave*.20),10:(.72,.34-wave*.20),
            11:(.46,.66),12:(.54,.66),13:(.46,.79),14:(.54,.79),
            15:(.45,.94),16:(.55,.94)}.items():
        points[index] = [*value, .95]
    return dict(pid='offline-synthetic', sequence=int(now*30), timestamp_seconds=now, points17=points)


class AvatarPanel(Gtk.Box):
    def __init__(self, args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.args = args
        self.stop = threading.Event()
        self.active = not bool(args.embed_socket)
        self.page = 'avatar'
        self.lock = threading.Lock()
        self.latest = None
        self.rig = Rig(mirrored=True)
        self.pose = neutral_pose()
        self.renderer = Renderer()
        self.scene = 'bamboo'
        self.character = 'scholar'
        self.photo = None
        self.cache = {}
        self.backgrounds = {name: cairo.ImageSurface.create_from_png(str(ROOT/'avatar/assets'/(name+'.png')))
                            for name, _ in SCENES}
        self.save_threads = []
        self.save_busy = False
        self.share_dialog = None
        self.notice = ''
        self.notice_until = 0.
        self.generation = 0
        self.status_text = None
        self.last_draw_seconds = 0.
        self.frames_drawn = 0
        self.syncing_buttons = False
        self.get_style_context().add_class('avatar-shell')
        css = Gtk.CssProvider()
        css.load_from_data(b'.avatar-shell {background:#f0eadc;color:#263f38} .avatar-shell .avatar-bar {background:#f0eadc;padding:5px} .avatar-shell .avatar-bar button {padding:7px 12px;background:#fcf8ee;color:#29483d;border-radius:5px} .avatar-shell .avatar-bar button:checked {background:#294c40;color:#faf6ea} .avatar-shell label {background:transparent}')
        self.get_style_context().add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        bar = Gtk.Box(spacing=8)
        bar.get_style_context().add_class('avatar-bar')
        self.avatar_button = Gtk.ToggleButton(label='国画分身')
        self.avatar_button.set_active(True)
        self.avatar_button.connect('clicked', lambda *_: None if self.syncing_buttons else self.show_page('avatar'))
        self.photo_button = Gtk.ToggleButton(label='照片入墨')
        self.photo_button.connect('clicked', lambda *_: None if self.syncing_buttons else self.show_page('photo'))
        bar.pack_start(self.avatar_button, False, False, 0)
        bar.pack_start(self.photo_button, False, False, 0)
        title = Gtk.Label(label='万物入墨 · 人在画中')
        title.set_xalign(0)
        bar.pack_start(title, True, True, 0)
        home = Gtk.Button(label='四境菜单')
        home.connect('clicked', lambda *_: self.request_menu())
        bar.pack_start(home, False, False, 0)
        self.pack_start(bar, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.pack_start(self.stack, True, True, 0)
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.connect('draw', self.draw)
        stage.pack_start(self.canvas, True, True, 0)
        controls = Gtk.Box(spacing=7)
        controls.get_style_context().add_class('avatar-bar')
        self.scene_buttons = {}
        for name, label in SCENES:
            button = Gtk.ToggleButton(label=label)
            button.set_active(name == self.scene)
            button.connect('clicked', lambda _, key=name: None if self.syncing_buttons else self.choose_scene(key))
            controls.pack_start(button, False, False, 0)
            self.scene_buttons[name] = button
        self.status = Gtk.Label(label='固定国画角色 · 随动作舒展衣袖')
        self.status.set_xalign(0)
        self.status.set_ellipsize(3)
        controls.pack_start(self.status, True, True, 0)
        self.save_button = Gtk.Button(label='留影 / 扫码')
        self.save_button.connect('clicked', self.save)
        controls.pack_end(self.save_button, False, False, 0)
        stage.pack_start(controls, False, False, 0)
        self.stack.add_named(stage, 'avatar')
        self.stack.set_visible_child_name('avatar')
        # Rendering must yield to input, child commands and completed saves on
        # slower displays. A permanently-ready redraw must not starve idle work.
        self.timer = GLib.timeout_add(33, self.tick, priority=GLib.PRIORITY_DEFAULT_IDLE+10)
        self.poll_thread = None
        if not args.offline_preview:
            self.poll_thread = threading.Thread(target=self.poll, daemon=True)
            self.poll_thread.start()

    def choose_scene(self, name):
        if name not in dict(SCENES):
            return
        self.scene = name
        self.syncing_buttons = True
        try:
            for key, button in self.scene_buttons.items():
                button.set_active(key == name)
        finally:
            self.syncing_buttons = False
        self.canvas.queue_draw()

    def close_share(self):
        if self.share_dialog:
            self.share_dialog.destroy()

    def show_page(self, name):
        if self.stop.is_set():
            return
        if name not in ('avatar', 'photo'):
            return
        self.close_share()
        if name != self.page:
            self.generation += 1
            self.page = name
            with self.lock:
                self.latest = None
            self.rig.reset()
        if name == 'photo' and self.photo is None:
            args = copy(self.args)
            args.embed_socket = 1  # Parent controls activation, even standalone.
            self.photo = photo_module().EnvironmentPanel(args, on_back=lambda: self.show_page('avatar'))
            self.stack.add_named(self.photo, 'photo')
            self.photo.show_all()
        if self.photo:
            self.photo.operation('resume' if self.active and name == 'photo' else 'pause')
            self.photo.refresh_ui()
        self.stack.set_visible_child_name(name)
        self.syncing_buttons = True
        try:
            self.avatar_button.set_active(name == 'avatar')
            self.photo_button.set_active(name == 'photo')
        finally:
            self.syncing_buttons = False
        self.canvas.queue_draw()

    def request_menu(self):
        self.operation('pause')
        emit('menu_requested')

    def key(self, _, event):
        if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_Menu, Gdk.KEY_F1):
            self.request_menu()
            return True
        if self.page == 'photo' and self.photo:
            return self.photo.key(_, event)
        return False

    def operation(self, op):
        if op == 'shutdown':
            if self.stop.is_set():
                return False
            self.stop.set()
            self.active = False
            self.generation += 1
            self.close_share()
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            if self.photo:
                self.photo.operation('shutdown')
            if Gtk.main_level():
                Gtk.main_quit()
        elif op in ('pause', 'resume') and not self.stop.is_set():
            active = op == 'resume'
            if active != self.active:
                self.active = active
                self.generation += 1
                with self.lock:
                    self.latest = None
                self.rig.reset()
                self.pose = neutral_pose()
            if not active:
                self.close_share()
            if self.photo:
                self.photo.operation('resume' if active and self.page == 'photo' else 'pause')
        return False

    def poll(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.stop.is_set():
            if not self.active or self.page != 'avatar':
                self.stop.wait(.1)
                continue
            generation = self.generation
            try:
                with opener.open(self.args.base+'/api/state', timeout=.45) as response:
                    state = json.load(response)
            except Exception:
                state = None
            with self.lock:
                if generation == self.generation and self.active and self.page == 'avatar':
                    self.latest = state
            self.stop.wait(.018)

    def tick(self):
        if self.stop.is_set():
            return False
        if not self.active or self.page != 'avatar':
            return True
        now = time.monotonic()
        with self.lock:
            state = self.latest
        if self.args.offline_preview:
            state = demo_state(now)
        pose = self.rig.update(state, now)
        changed = pose.joints != self.pose.joints or pose.opacity != self.pose.opacity
        self.pose = pose
        prefix = '离线动作演示 · ' if self.args.offline_preview else ''
        value = self.notice if now < self.notice_until else prefix+self.pose.message
        if value != self.status_text:
            self.status.set_text(value)
            self.status_text = value
        if changed:
            self.canvas.queue_draw()
        return True

    def background(self, name, width, height):
        key = (name, width, height)
        if key not in self.cache:
            # A single cached raster per scene/size; resizing cannot grow forever.
            self.cache = {k:v for k,v in self.cache.items() if k[1:] == (width,height)}
            source = self.backgrounds[name]
            surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
            cr = cairo.Context(surface)
            cr.scale(width/source.get_width(), height/source.get_height())
            cr.set_source_surface(source)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            self.cache[key] = surface
        return self.cache[key]

    def paint(self, cr, width, height, pose=None):
        cr.set_source_surface(self.background(self.scene, width, height))
        cr.paint()
        self.renderer.render(cr, width, height, pose or self.pose, character=self.character)

    def draw(self, widget, cr):
        started = time.monotonic()
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_source_rgb(.92,.90,.85)
        cr.paint()
        scale = min(width/1280., height/720.)
        w, h = max(1,round(1280*scale)), max(1,round(720*scale))
        cr.save()
        cr.translate((width-w)/2, (height-h)/2)
        self.paint(cr,w,h)
        cr.restore()
        self.last_draw_seconds = time.monotonic()-started
        self.frames_drawn += 1
        return False

    def save(self, *_):
        if not self.active or self.page != 'avatar' or self.save_busy:
            return
        # Snapshot the rendered artwork, never the raw camera or the UI.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,1280,720)
        self.paint(cairo.Context(surface),1280,720)
        surface.flush()
        pixels = bytes(surface.get_data())
        metadata = dict(renderer='fixed-ink-avatar-v1',title=dict(SCENES)[self.scene]+' · '+dict(CHARACTERS)[self.character],
            scene=self.scene,character=self.character,pose_status=self.pose.status,
            source_kind='offline-demo' if self.args.offline_preview else 'pose-retargeting',
            source_key=self.pose.source_key)
        generation = self.generation
        save_art = photo_module().save_art
        self.save_busy = True
        self.save_button.set_sensitive(False)
        def persist():
            path = None
            error = None
            try:
                import numpy as np
                from PIL import Image
                image = Image.frombuffer('RGBA',(1280,720),pixels,'raw','BGRA',0,1).convert('RGB')
                path = save_art(np.array(image),self.args.works,metadata)
                emit('artwork_saved',name=path.name,kind='avatar')
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self.saved,path,error,generation,priority=GLib.PRIORITY_DEFAULT)
        worker = threading.Thread(target=persist,daemon=False)
        self.save_threads = [t for t in self.save_threads if t.is_alive()]
        self.save_threads.append(worker)
        worker.start()

    def saved(self,path,error,generation):
        self.save_busy = False
        if self.stop.is_set():
            return False
        self.save_button.set_sensitive(True)
        self.notice = '留影已保存' if path else '保存失败：'+str(error)[:80]
        self.notice_until = time.monotonic()+5
        if path and not self.args.offline_preview:
            def share():
                try:
                    sys.path.insert(0,'/home/ubuntu/bodyink-live')
                    from network_share import fetch_share
                    result = fetch_share(path.name)
                    GLib.idle_add(self.show_share,result,generation,priority=GLib.PRIORITY_DEFAULT)
                except Exception as exc:
                    print('avatar share: '+str(exc),file=sys.stderr)
            threading.Thread(target=share,daemon=True).start()
        return False

    def show_share(self,result,generation):
        if self.stop.is_set() or not self.active or self.page!='avatar' or self.generation!=generation:
            return False
        self.close_share()
        dialog = Gtk.Dialog(title='把画中身影带走',transient_for=self.get_toplevel(),modal=False)
        dialog.add_button('返回画卷',Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        box.set_border_width(16)
        box.set_spacing(10)
        box.pack_start(Gtk.Label(label='手机与板卡连接同一热点后，扫码下载'),False,False,0)
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(result['png'])
        loader.close()
        pix = loader.get_pixbuf()
        if pix.get_width()<210:
            pix = pix.scale_simple(pix.get_width()*2,pix.get_height()*2,GdkPixbuf.InterpType.NEAREST)
        box.pack_start(Gtk.Image.new_from_pixbuf(pix),False,False,0)
        url = Gtk.Label(label=result['url'])
        url.set_selectable(True)
        box.pack_start(url,False,False,0)
        self.share_dialog = dialog
        dialog.connect('response',lambda d,_:d.destroy())
        dialog.connect('destroy',lambda *_:setattr(self,'share_dialog',None))
        dialog.show_all()
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--embed-socket',type=int)
    parser.add_argument('--base','--base-url',dest='base',default='http://127.0.0.1:8088')
    parser.add_argument('--works',type=Path,default=Path('/home/ubuntu/bodyink-live/works'))
    parser.add_argument('--offline-preview',action='store_true')
    parser.add_argument('--offline-image',type=Path)
    parser.add_argument('--fps',type=float,default=4.,help='Optional photo viewfinder rate only')
    args = parser.parse_args()
    if not 1<=args.fps<=5:
        parser.error('Photo viewfinder rate must be 1..5')
    if args.offline_image and not args.offline_preview:
        parser.error('--offline-image requires --offline-preview')
    window = Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='万物入墨 · 国画分身')
    if not args.embed_socket:
        window.set_default_size(1024,600)
    panel = AvatarPanel(args)
    window.add(panel)
    window.connect('key-press-event',panel.key)
    window.connect('destroy',lambda *_:panel.operation('shutdown'))
    window.show_all()
    def commands():
        for line in sys.stdin:
            try:
                op = json.loads(line).get('op')
            except (ValueError,AttributeError):
                continue
            if op in ('pause','resume','shutdown'):
                GLib.idle_add(panel.operation,op,priority=GLib.PRIORITY_DEFAULT)
        if args.embed_socket:
            GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_DEFAULT)
    threading.Thread(target=commands,daemon=True).start()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_DEFAULT))
    emit('ready',mode='environment',experience='avatar')
    Gtk.main()
    panel.operation('shutdown')
    for worker in panel.save_threads:
        worker.join()
    if panel.photo:
        for worker in panel.photo.photo_threads+panel.photo.save_threads:
            worker.join()


if __name__ == '__main__':
    main()
