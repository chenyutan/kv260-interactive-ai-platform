"""Native photo-import studio. Never requests a camera frame."""
import threading
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
from photo_navigation import PhotoControls, PhotoFileChooser
from photo_wait import PhotoWaitState, PhotoWaitPainter, STAGES


class PhotoStudio(Gtk.Box):
    def __init__(self, on_back, service=None, store=None, portal=None,on_ai=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.on_back=on_back;self.service=service;self.store=store;self.portal=portal;self.on_ai=on_ai
        self.active=False;self.closed=False;self.busy=False;self.selected=None
        self.job=None;self.image_key=None;self.pixbuf=None;self.image_bytes=None
        self.session_token=None;self.owner_session=None;self.share_url=None;self.dialog=None;self.epoch=0
        self.timer=None;self.pending=False
        self.effect_timer=None;self.wait_effect=PhotoWaitState();self.wait_painter=PhotoWaitPainter()
        self.preview_surface=None;self.preview_surface_key=None;self.preview_cache_builds=0
        self.connect('map',lambda *_:self.refresh_effect())
        self.connect('unmap',lambda *_:self.stop_effect())
        self.processing=False;self.notice=None;self.selection_notice=False;self.preview_error=None
        self.preview_retry_key=None;self.preview_retry_at=0.
        self.set_border_width(12)
        head=Gtk.Box(spacing=14)
        title=Gtk.Label();title.set_markup('<span font_family="Noto Serif CJK SC" size="19000" weight="bold">照片水墨</span>')
        head.pack_start(title,False,False,0)
        caption=Gtk.Label(label='导入照片，制作自己的作品');caption.set_xalign(0)
        caption.get_style_context().add_class('photo-caption')
        head.pack_start(caption,True,True,0)
        self.ai_button=None
        if on_ai:
            self.ai_button=Gtk.Button(label='AI 助手');self.ai_button.set_size_request(128,48)
            self.ai_button.connect('clicked',lambda *_:self.on_ai())
            self.ai_button.get_style_context().add_class('photo-quiet')
            head.pack_start(self.ai_button,False,False,0)
        back=Gtk.Button(label='返回创作');back.set_size_request(152,48);back.connect('clicked',lambda *_:self.on_back())
        back.get_style_context().add_class('photo-quiet')
        head.pack_end(back,False,False,0);self.pack_start(head,False,False,0)
        content=Gtk.Box(spacing=14);self.pack_start(content,True,True,0)
        side=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5);side.set_size_request(224,-1)
        side.get_style_context().add_class('photo-sidebar')
        content.pack_start(side,False,False,0)
        self.pick=Gtk.Button(label='从板卡 / U盘选择照片');self.pick.connect('clicked',self.choose_file)
        self.pick.get_style_context().add_class('photo-pick')
        side.pack_start(self.pick,False,False,0)
        self.filename=Gtk.Label(label='支持 JPG、PNG、WebP');self.filename.set_line_wrap(False)
        self.filename.set_max_width_chars(24);self.filename.set_ellipsize(3)
        self.filename.get_style_context().add_class('photo-file-caption')
        side.pack_start(self.filename,False,False,0)
        self.style=Gtk.ComboBoxText();self.style.append('color','青绿淡彩');self.style.append('ink','水墨')
        self.style.set_active_id('color');side.pack_start(self.style,False,False,0)
        self.generate=Gtk.Button(label='开始制作');self.generate.connect('clicked',self.submit)
        self.generate.get_style_context().add_class('photo-primary')
        side.pack_start(self.generate,False,False,0)
        separator=Gtk.Separator();side.pack_start(separator,False,False,4)
        phone=Gtk.Label(label='手机上传');phone.get_style_context().add_class('photo-phone-title')
        side.pack_start(phone,False,False,0)
        self.qr=Gtk.Image()
        qr_area=Gtk.Overlay();qr_area.add(self.qr);side.pack_start(qr_area,True,True,0)
        self.qr_hint=Gtk.DrawingArea();self.qr_hint.set_size_request(160,112)
        self.qr_hint.set_halign(Gtk.Align.CENTER);self.qr_hint.set_valign(Gtk.Align.CENTER)
        self.qr_hint.connect('draw',self.on_qr_hint)
        qr_area.add_overlay(self.qr_hint);qr_area.set_overlay_pass_through(self.qr_hint,True)
        self.qr.connect('notify::pixbuf',lambda *_:self.qr_hint.set_visible(self.qr.get_pixbuf() is None))
        self.network=Gtk.Label(label='连接同一个 Wi-Fi 后即可上传');self.network.set_line_wrap(True)
        self.network.set_max_width_chars(25);side.pack_start(self.network,False,False,0)
        self.network.get_style_context().add_class('photo-network')
        right=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);content.pack_start(right,True,True,0)
        tools=Gtk.Box(spacing=8)
        self.image_title=Gtk.Label(label='作品预览');self.image_title.set_xalign(0)
        self.image_title.get_style_context().add_class('photo-preview-title')
        tools.pack_start(self.image_title,True,True,0)
        self.original=Gtk.ToggleButton(label='查看原图');self.original.connect('toggled',lambda *_:self.load_image())
        self.original.get_style_context().add_class('photo-quiet')
        tools.pack_end(self.original,False,False,0);right.pack_start(tools,False,False,0)
        self.pad_help=Gtk.Label()
        self.pad_help.set_markup('<b>手柄</b>　↑↓ / 左摇杆 选择　←→ 画风 / 历史　A 确认 / 切换　B 返回\n选图：↑↓ 选择　A 打开　LB 上一级　RB 刷新　｜　右摇杆 鼠标　R3 点击')
        self.pad_help.set_xalign(0);self.pad_help.set_line_wrap(True)
        self.pad_help.get_style_context().add_class('photo-help')
        right.pack_start(self.pad_help,False,False,0)
        self.canvas=Gtk.DrawingArea();self.canvas.set_hexpand(True);self.canvas.set_vexpand(True)
        self.canvas.connect('draw',self.on_draw);right.pack_start(self.canvas,True,True,0)
        self.history=Gtk.ComboBoxText();self.history.connect('changed',self.choose_job)
        right.pack_start(self.history,False,False,0);self.history_ids=()
        right.reorder_child(self.pad_help,-1)
        self.status=Gtk.Label(label='选择一张照片，或用手机扫码上传。');self.status.set_xalign(0)
        self.status.set_line_wrap(True);self.status.set_max_width_chars(100)
        self.status.set_lines(2);self.status.set_ellipsize(3)
        self.status.get_style_context().add_class('photo-status')
        self.pack_start(self.status,False,False,0)
        self.actions=(self.pick,self.style,self.generate,self.original,self.history)+((self.ai_button,) if self.ai_button else ())+(back,)
        self.pad_controls=PhotoControls(self.actions)
        for action in self.actions:
            action.connect('focus-in-event',self.control_focused)
        css=Gtk.CssProvider();css.load_from_data(b'''
            .photo-studio {background:#f4f0e7;color:#203f39;font-family:"Noto Sans CJK SC";font-size:13px;}
            .photo-studio label {background:transparent;}
            .photo-studio button {background-image:none;background:#fcfaf4;color:#28483f;border:1px solid #d3d5c9;border-radius:7px;padding:5px 11px;min-height:36px;font-size:16px;box-shadow:none;}
            .photo-studio button:hover {background:#e8ede2;border-color:#91a79a;}
            .photo-studio button:disabled {background:#eeeae1;color:#a1aaa1;border-color:#e0ded4;}
            .photo-studio combobox {padding:0;}
            .photo-studio combobox button {min-height:36px;padding:5px 11px;}
            .photo-studio .photo-caption {font-size:12px;color:#7a8577;}
            .photo-studio .photo-quiet {font-size:16px;background:transparent;border-color:#cbd2c6;}
            .photo-studio .photo-quiet:checked {background:#2c5148;color:#f6f0df;}
            .photo-studio .photo-sidebar {background:#1e4039;color:#f5eedc;border-radius:12px;padding:11px 10px;}
            .photo-studio .photo-sidebar label {color:#d6dfcd;font-size:12px;}
            .photo-studio .photo-sidebar button {background:#31564b;color:#f1ecd9;border-color:#638074;}
            .photo-studio .photo-sidebar button label {font-size:16px;}
            .photo-studio .photo-sidebar combobox button {background:#f3eee1;color:#25463c;border-color:#f3eee1;}
            .photo-studio .photo-sidebar .photo-file-caption {font-size:11px;color:#b6c8b6;}
            .photo-studio .photo-sidebar .photo-primary {background:#b34c38;color:#fff8e9;border-color:#c5634c;font-weight:bold;min-height:36px;}
            .photo-studio .photo-sidebar .photo-primary:hover {background:#c45a42;}
            .photo-studio .photo-sidebar .photo-primary:disabled {background:#3a5548;color:#a4b1a0;border-color:#486355;}
            .photo-studio .photo-sidebar .photo-phone-title {color:#d9bf87;font-weight:bold;font-size:12px;}
            .photo-studio .photo-sidebar .photo-network {font-size:11px;color:#c3d1bd;}
            .photo-studio .photo-sidebar separator {background:#507064;min-height:1px;}
            .photo-studio .photo-preview-title {font-weight:bold;font-size:14px;color:#365247;}
            .photo-studio .photo-help {padding:5px 0;color:#64786a;font-size:12px;}
            .photo-studio .photo-status {background:#e9e9de;color:#526553;padding:6px 9px;border-left:3px solid #ae9160;border-radius:3px;font-size:12px;}
            .photo-studio .photo-pad-focus {box-shadow:inset 0 0 0 3px #2e8b77;background-image:none;background-color:#e3eee3;}
            .photo-studio .photo-sidebar .photo-pad-focus {box-shadow:inset 0 0 0 3px #d2b779;background:#f7efd8;color:#244b40;}
            .photo-studio .photo-sidebar .photo-pad-focus label {color:#244b40;}
            .photo-studio combobox.photo-pad-focus button {box-shadow:inset 0 0 0 2px #ae9160;background:#fbf5e7;}
            .photo-studio .photo-sidebar combobox.photo-pad-focus button {box-shadow:inset 0 0 0 3px #d2b779;background:#fff5dd;}
            .photo-studio .photo-sidebar .photo-primary:disabled label {color:#a4b1a0;}
        ''')
        self.get_style_context().add_class('photo-studio')
        self.get_style_context().add_provider(css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.generate.set_sensitive(False);self.original.set_sensitive(False)
        self.history.set_sensitive(False)

    def control_focused(self,widget,*_):
        # Mouse and keyboard users share the same visible action selection.
        if widget.is_sensitive():self.pad_controls.focus(widget)
        return False

    def bind(self,service,store,portal):
        self.service=service;self.store=store;self.portal=portal

    def refresh_controls(self):
        self.generate.set_sensitive(bool(self.active and self.selected and self.service and self.store and
                                         self.session_token and not self.busy and not self.processing))
        self.pad_controls.refresh()

    def clear_preview(self):
        self.image_key=None;self.pixbuf=None;self.preview_error=None
        self.preview_retry_key=None;self.preview_retry_at=0.
        self.canvas.queue_draw()

    def refresh_status(self):
        if self.notice:
            message=self.notice
        elif self.selection_notice:
            message='照片已选择。'+('已有作品正在制作，请等完成后再提交；可以先返回创作。' if self.processing or self.busy else '选择画风后点“开始制作”。')
        elif self.job:
            state=self.job.get('status');stage=self.job.get('stage',state)
            elapsed=max(0,int(self.job.get('elapsed_s',0)))
            if state=='completed':message='作品已保存，可在手机页面下载原图；结束游玩后仍会保留。'
            elif state=='failed':message=self.job.get('error') or '处理失败，请换一张照片重试。'
            else:message=STAGES.get(stage,'正在处理照片')+' · 已用 '+str(elapsed)+' 秒；返回创作不会中断制作。'
        elif self.busy:message='正在导入照片，离开页面后仍会继续。'
        else:message='选择一张照片，或用手机扫码上传。'
        if self.preview_error:message+='　预览暂时无法读取，正在重试；原图仍可从手机下载。'
        self.status.set_text(message)
        self.refresh_effect()

    def stop_effect(self):
        if self.effect_timer is not None:
            GLib.source_remove(self.effect_timer);self.effect_timer=None

    def refresh_effect(self):
        self.wait_effect.update(self.job,self.busy,
                                self.pixbuf is not None and not self.original.get_active())
        visible=self.active and not self.closed and self.get_mapped()
        if visible and self.wait_effect.animated():
            if self.effect_timer is None:self.effect_timer=GLib.timeout_add(50,self.animate)
        else:self.stop_effect()
        self.canvas.queue_draw()

    def animate(self):
        if not self.active or self.closed or not self.get_mapped() or not self.wait_effect.animated():
            self.effect_timer=None;self.canvas.queue_draw();return False
        self.canvas.queue_draw();return True

    def enter(self):
        self.active=True;self.epoch+=1;self.image_key=None;self.notice=None;self.selection_notice=False
        self.session_token=None;self.generate.set_sensitive(False)
        self.job=None;self.pixbuf=None;self.image_title.set_text('作品预览')
        self.wait_effect.reset();self.stop_effect()
        self.original.set_active(False);self.original.set_sensitive(False)
        self.pad_controls.focus(self.pick)
        self.canvas.queue_draw()
        if self.timer is None:self.timer=GLib.timeout_add(1000,self.tick)
        self.tick()

    def leave(self):
        self.active=False;self.epoch+=1
        self.stop_effect()
        if self.dialog:self.dialog.destroy();self.dialog=None
        # A submitted job belongs to the visitor, not to this page. It may
        # complete while the visitor browses the menu and is never resubmitted.

    def return_from_ai(self):
        """Resume this page without resetting a selected file or resubmitting it."""
        self.active=True;self.epoch+=1
        if self.timer is None:self.timer=GLib.timeout_add(1000,self.tick)
        if self.ai_button:self.pad_controls.focus(self.ai_button)
        self.refresh_controls();self.tick()

    def close(self):
        self.closed=True;self.leave()
        if self.timer is not None:GLib.source_remove(self.timer);self.timer=None

    def async_call(self,work,done,always=False):
        epoch=self.epoch
        def run():
            try:value=work();error=None
            except Exception as exc:value=None;error=str(exc)
            def deliver():
                if not self.closed and (always or epoch==self.epoch):done(value,error)
                return False
            GLib.idle_add(deliver)
        threading.Thread(target=run,daemon=True,name='photo-studio-io').start()

    def choose_file(self,*_):
        if self.dialog or self.closed:return
        epoch=self.epoch
        dialog=PhotoFileChooser(self.get_toplevel(),initial=self.selected.parent if self.selected else None)
        self.dialog=dialog
        def destroyed(window):
            if self.dialog is window:self.dialog=None
            if not self.closed and self.active:self.pad_controls.focus(self.pick)
        dialog.connect('destroy',destroyed)
        def response(window,result):
            filename=window.get_filename() if result==Gtk.ResponseType.OK else None
            self.dialog=None;window.destroy()
            self.pad_controls.focus(self.pick)
            if filename and not self.closed and epoch==self.epoch:
                self.selected=Path(filename);self.filename.set_text(self.selected.name)
                self.notice=None;self.selection_notice=True
                self.refresh_controls();self.refresh_status()
        dialog.connect('response',response);dialog.show_all()

    def submit(self,*_):
        if self.busy or self.processing or not self.selected or not self.service or not self.store:return
        token=self.session_token
        if not token:self.status.set_text('正在确认本次游玩，请稍候。');return
        path=self.selected;style=self.style.get_active_id() or 'color';epoch=self.epoch
        self.notice=None;self.selection_notice=False
        self.busy=True;self.generate.set_sensitive(False);self.status.set_text('正在导入照片…')
        self.refresh_effect()
        def complete(job,error):
            # Submission ownership outlives a page visit. Always release the
            # import lock, but never paint an old visitor's result into a new one.
            self.busy=False
            if job and token==self.session_token:self.processing=job.get('status') in ('queued','processing')
            if self.active:
                if error and epoch==self.epoch and token==self.session_token:self.notice=error
                self.refresh_controls();self.refresh_status();self.tick()
        self.async_call(lambda:self.service.submit_path(token,path,style),complete,always=True)

    def tick(self):
        if self.closed:self.timer=None;return False
        if not self.active or self.pending:return True
        if not self.service or not self.store:
            self.status.set_text('照片服务未启动，请返回主菜单后重试。');return True
        self.pending=True;epoch=self.epoch
        def work():
            session=self.store.current();jobs=self.service.list_jobs(session['token'])
            share=self.portal.share() if self.portal else {}
            return session,jobs,share
        def done(value,error):
            self.pending=False
            if error:
                self.status.set_text('暂时无法读取照片：'+error)
                self.refresh_controls();return
            session,jobs,share=value
            if self.owner_session is not None and self.owner_session!=session['token']:
                self.selected=None;self.filename.set_text('支持 JPG、PNG、WebP')
                self.notice=None;self.selection_notice=False
                if self.dialog:self.dialog.response(Gtk.ResponseType.CANCEL)
            self.owner_session=session['token']
            if self.session_token!=session['token']:
                self.session_token=session['token'];self.job=None;self.clear_preview()
                self.wait_effect.reset()
                self.original.set_active(False);self.original.set_sensitive(False)
            identifiers=tuple(job['id'] for job in jobs)
            self.history.set_sensitive(bool(identifiers))
            if identifiers!=self.history_ids:
                selected=self.history.get_active_id()
                if identifiers and (not self.history_ids or identifiers[0]!=self.history_ids[0]):selected=identifiers[0]
                self.history_ids=identifiers
                self.history.remove_all()
                for job in jobs:
                    label=('青绿淡彩' if job.get('style')=='color' else '水墨')+' · '+str(job.get('created',''))[11:19]
                    self.history.append(job['id'],label)
                self.history.set_active_id(selected if selected in identifiers else identifiers[0] if identifiers else None)
            selected=self.history.get_active_id()
            previous_id=self.job.get('id') if self.job else None
            self.job=next((job for job in jobs if job['id']==selected),jobs[0] if jobs else None)
            if previous_id!=(self.job.get('id') if self.job else None):
                self.clear_preview();self.original.set_active(False)
            if share.get('url')!=self.share_url:
                self.share_url=share.get('url');self.qr.clear()
                if share.get('qr'):
                    loader=GdkPixbuf.PixbufLoader.new_with_type('png');loader.write(share['qr']);loader.close()
                    self.qr.set_from_pixbuf(loader.get_pixbuf().scale_simple(112,112,GdkPixbuf.InterpType.NEAREST))
            self.network.set_text('手机与板卡连接同一个 Wi-Fi。\n扫码后在“照片水墨”上传。' if self.share_url else '尚未连接 Wi-Fi。\n可先从板卡或 U盘选择照片。')
            if self.job:
                self.original.set_sensitive(self.job.get('status')=='completed');self.load_image()
            else:
                self.clear_preview();self.original.set_active(False);self.original.set_sensitive(False)
                self.image_title.set_text('作品预览')
            self.processing=any(j['status'] in ('queued','processing') for j in jobs)
            self.refresh_controls();self.refresh_status()
        # pending must reset even when the page was left before delivery.
        def run():
            try:value=work();error=None
            except Exception as exc:value=None;error=str(exc)
            def apply():
                self.pending=False
                if self.active and not self.closed and epoch==self.epoch:done(value,error)
                return False
            GLib.idle_add(apply)
        threading.Thread(target=run,daemon=True,name='photo-studio-status').start()
        return True

    def choose_job(self,*_):
        self.image_key=None
        self.notice=None;self.selection_notice=False
        if self.active:self.tick()

    def load_image(self):
        if not self.job or not self.session_token or not self.service:return
        kind='source' if self.original.get_active() or self.job['status']!='completed' else 'result'
        key=(self.session_token,self.job['id'],kind)
        if key==self.image_key:return
        if key==self.preview_retry_key and time.monotonic()<self.preview_retry_at:return
        self.image_key=key;self.pixbuf=None;self.preview_error=None;self.canvas.queue_draw()
        self.image_title.set_text('原始照片' if kind=='source' else '水墨作品')
        def failed():
            self.image_key=None;self.preview_error=True;self.preview_retry_key=key
            self.preview_retry_at=time.monotonic()+1.
            self.refresh_status()
        def loaded(data,error):
            if key!=self.image_key:return
            if error:failed();return
            try:
                loader=GdkPixbuf.PixbufLoader.new_with_type('png')
                # Decode only a display-sized preview; the stored PNG remains original.
                def size_prepared(reader,width,height):
                    scale=min(1.,1000/width,650/height)
                    reader.set_size(max(1,round(width*scale)),max(1,round(height*scale)))
                loader.connect('size-prepared',size_prepared);loader.write(data);loader.close();self.pixbuf=loader.get_pixbuf()
                self.preview_error=None;self.preview_retry_key=None;self.canvas.queue_draw();self.refresh_status()
            except Exception:failed()
        self.async_call(lambda:self.service.read_image(key[0],key[1],key[2]),loaded)

    def on_draw(self,widget,cr):
        w,h=widget.get_allocated_width(),widget.get_allocated_height()
        # Scale/decode a photograph once per image or allocation, rather than
        # resampling it for every 20 Hz brush animation frame.
        key=(w,h,self.pixbuf)
        if self.preview_surface_key!=key:
            import cairo
            self.preview_surface=cairo.ImageSurface(cairo.FORMAT_RGB24,max(1,w),max(1,h))
            self.draw_preview(widget,cairo.Context(self.preview_surface))
            self.preview_surface_key=key;self.preview_cache_builds+=1
        cr.set_source_surface(self.preview_surface,0,0);cr.paint()
        self.wait_painter.draw(cr,w,h,self.wait_effect)
        return False

    def draw_preview(self,widget,cr):
        w,h=widget.get_allocated_width(),widget.get_allocated_height()
        cr.set_source_rgb(.956,.941,.908);cr.paint()
        # Cached mount: no continuously running animation when idle.
        cr.rectangle(1,1,max(0,w-2),max(0,h-2));cr.set_source_rgb(.997,.987,.956);cr.fill_preserve()
        cr.set_source_rgb(.835,.842,.794);cr.set_line_width(1.);cr.stroke()
        if self.pixbuf:
            pw,ph=self.pixbuf.get_width(),self.pixbuf.get_height();scale=min((w-36)/pw,(h-30)/ph)
            if scale<=0:return False
            x,y=(w-pw*scale)/2,(h-ph*scale)/2
            cr.rectangle(x+2,y+3,pw*scale,ph*scale);cr.set_source_rgba(.12,.20,.16,.09);cr.fill()
            cr.save();cr.translate((w-pw*scale)/2,(h-ph*scale)/2);cr.scale(scale,scale)
            Gdk.cairo_set_source_pixbuf(cr,self.pixbuf,0,0);cr.paint();cr.restore()
        else:
            from menu_view import text
            cx,cy=w/2,h*.35
            cr.set_source_rgb(.70,.75,.66);cr.set_line_width(1.3)
            cr.rectangle(cx-35,cy-23,70,46);cr.stroke()
            cr.move_to(cx-28,cy+15);cr.line_to(cx-8,cy-5);cr.line_to(cx+3,cy+5)
            cr.line_to(cx+17,cy-13);cr.line_to(cx+29,cy+15);cr.stroke()
            cr.arc(cx-16,cy-12,3,0,6.283185);cr.set_source_rgb(.69,.35,.25);cr.fill()
            text(cr,'预览你的水墨作品',w*.08,h*.52,17,(.29,.39,.32),width=w*.84,centered=True)
            text(cr,'选择照片，或扫描左侧二维码上传',w*.08,h*.68,11,(.48,.55,.46),width=w*.84,centered=True)
        return False

    def on_qr_hint(self,widget,cr):
        from menu_view import text
        w,h=widget.get_allocated_width(),widget.get_allocated_height()
        x,y=w/2-16,h/2-37
        cr.set_source_rgb(.53,.66,.56);cr.set_line_width(1.2)
        cr.rectangle(x,y,32,47);cr.stroke()
        cr.move_to(x+10,y+6);cr.line_to(x+22,y+6);cr.stroke()
        cr.arc(x+16,y+40,1.3,0,6.283185);cr.fill()
        text(cr,'联网后显示上传码',0,y+59,10,(.65,.76,.65),width=w,centered=True)
        return False

    def gamepad(self,snapshot):
        buttons=snapshot.get('pressed',())
        if 'B' in buttons or 'BACK' in snapshot.get('released',()) or 'GUIDE' in buttons:
            self.on_back();return
        self.pad_controls.update(snapshot)
