"""GTK connection center. No I/O in widget callbacks or paint handlers."""
import math
import time
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk
from render_policy import bounded_window_size


class ConnectionCenter(Gtk.Window):
    def __init__(self,parent,operation,dismiss,closed):
        super().__init__(title='墨境 · 设备与联网')
        self.get_style_context().add_class('connection-center')
        css=Gtk.CssProvider();css.load_from_data(b'''
            .connection-center button {font-size:16px;min-height:36px;padding:5px 10px;}
            .connection-center button.connection-key {font-size:14px;min-height:20px;padding:3px 5px;}
            .connection-center entry {font-size:16px;min-height:36px;padding:5px 8px;}
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.set_transient_for(parent);self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_size_request(320,240);self.fit_screen(default=True)
        self.operation=operation;self.dismiss=dismiss;self.closed=closed
        self.pending=None;self.rows=[];self.networks=[];self.selected=None;self.snapshot={};self.hand_target=None;self.hand_since=0.;self.hand_key=None
        self.hand_time=None;self.pointer=None;self.hand_progress=0.
        overlay=Gtk.Overlay();self.add(overlay)
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);root.set_border_width(12);overlay.add(root)
        self.cursor_layer=Gtk.DrawingArea();self.cursor_layer.set_hexpand(True);self.cursor_layer.set_vexpand(True)
        overlay.add_overlay(self.cursor_layer);overlay.set_overlay_pass_through(self.cursor_layer,True);self.cursor_layer.connect('draw',self.draw_cursor)
        top=Gtk.Box(spacing=10);root.pack_start(top,False,False,0)
        title=Gtk.Label(label='设备与联网');title.set_xalign(0);top.pack_start(title,True,True,0)
        self.buttons=[];self.back=self.button('返回体验菜单',self.hide_center);top.pack_start(self.back,False,False,0)
        self.tabs=Gtk.Notebook();root.pack_start(self.tabs,True,True,0)
        top.pack_start(self.button('设备状态',lambda:self.tabs.set_current_page(0)),False,False,0)
        top.pack_start(self.button('Wi‑Fi 连接',lambda:self.tabs.set_current_page(1)),False,False,0)
        status=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14);status.set_border_width(15)
        self.labels={};self.retries={}
        for key,name in (('camera','摄像头'),('audio','音响'),('wifi','Wi‑Fi 网络')):
            row=Gtk.Box(spacing=14);status.pack_start(row,False,False,0)
            label=Gtk.Label(label=name+' · 正在检查…');label.set_xalign(0);label.set_line_wrap(True);label.set_max_width_chars(52)
            row.pack_start(label,True,True,0);self.labels[key]=label
            b=self.button('重连',lambda key=key:self.act(key+'_retry'));row.pack_start(b,False,False,0);self.retries[key]=b
            b=self.button('保持未连接',lambda key=key:self.ignore(key));row.pack_start(b,False,False,0)
        hint=Gtk.Label(label='未联网时仍可离线体验。接回设备后自动检测；暂不连接不会反复弹窗。');hint.set_line_wrap(True)
        status.pack_start(hint,False,False,0)
        self.tabs.append_page(self.scroll_page(status),Gtk.Label(label='设备状态'))
        self.build_wifi();self.message=Gtk.Label(label='请选择设备操作。');self.message.set_line_wrap(True);root.pack_start(self.message,False,False,0)
        self.connect('delete-event',lambda *_:(self.hide_center() or True))
        self.connect('key-press-event',self.key)

    def fit_screen(self,default=False):
        screen=self.get_screen();parent=self.get_transient_for()
        window=parent.get_window() if parent else None
        monitor=screen.get_monitor_at_window(window) if window else screen.get_primary_monitor()
        area=screen.get_monitor_workarea(monitor)
        size=bounded_window_size(area.width,area.height)
        if default:self.set_default_size(*size)
        else:self.resize(*size)
        return size

    def scroll_page(self,content):
        # The on-screen keyboard must remain reachable on 540p and 450p
        # outputs. A scrollable page prevents its natural size from enlarging
        # the transient window beyond the real monitor work area.
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(100);scroll.add(content)
        return scroll

    def button(self,label,callback):
        b=Gtk.Button(label=label);b.set_size_request(-1,48);b.connect('clicked',lambda *_:callback());self.buttons.append(b);return b

    def build_wifi(self):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);box.set_border_width(10)
        tools=Gtk.Box(spacing=8);box.pack_start(tools,False,False,0)
        self.scan=self.button('搜索可用 Wi‑Fi',lambda:self.act('scan'));tools.pack_start(self.scan,False,False,0)
        self.wifi_status=Gtk.Label(label='无需退出应用即可联网。');self.wifi_status.set_xalign(0);self.wifi_status.set_ellipsize(3);tools.pack_start(self.wifi_status,True,True,0)
        self.list=Gtk.ListBox();self.list.set_selection_mode(Gtk.SelectionMode.SINGLE);self.list.connect('row-selected',self.select_row)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC);scroll.set_min_content_height(65);scroll.add(self.list);box.pack_start(scroll,True,True,0)
        controls=Gtk.Box(spacing=8);box.pack_start(controls,False,False,0)
        self.ssid=Gtk.Entry();self.ssid.set_placeholder_text('选择上方网络，或输入隐藏网络名称');controls.pack_start(self.ssid,True,True,0)
        self.password=Gtk.Entry();self.password.set_visibility(False);self.password.set_placeholder_text('Wi‑Fi 密码（开放网络留空）');controls.pack_start(self.password,True,True,0)
        self.buttons.extend((self.ssid,self.password))
        self.connect_button=self.button('连接',self.connect_wifi);controls.pack_start(self.connect_button,False,False,0)
        self.keyboard_button=self.button('屏幕键盘',self.toggle_keyboard);controls.pack_start(self.keyboard_button,False,False,0)
        self.keyboard=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=1)
        self.key_buttons=[];self.symbol_mode=False
        self.keyboard_target=self.password;self.ssid.connect('focus-in-event',lambda *_:self.target(self.ssid));self.password.connect('focus-in-event',lambda *_:self.target(self.password))
        for chars in ('1234567890','qwertyuiop','asdfghjkl','zxcvbnm'):
            row=Gtk.Box(spacing=2)
            for char in chars:
                key=self.button(char,lambda:None);key.connect('clicked',lambda b:self.type_char(b.get_label()));self.key_buttons.append(key);key.get_style_context().add_class('connection-key');key.set_size_request(-1,28);row.pack_start(key,True,True,0)
            self.keyboard.pack_start(row,False,False,0)
        row=Gtk.Box(spacing=3);self.shift=False
        for label,fn in (('大小写',self.toggle_shift),('空格',lambda:self.type_char(' ')),('符号',self.symbols),('退格',self.backspace)):
            key=self.button(label,fn);key.get_style_context().add_class('connection-key');key.set_size_request(-1,32)
            row.pack_start(key,True,True,0)
        self.keyboard.pack_start(row,False,False,0);box.pack_start(self.keyboard,False,False,0)
        self.keyboard.set_no_show_all(True);self.keyboard.hide();self.tabs.append_page(self.scroll_page(box),Gtk.Label(label='Wi‑Fi 连接'))

    def target(self,entry):self.keyboard_target=entry;return False
    def toggle_shift(self):self.shift=not self.shift;self.message.set_text('屏幕键盘：'+('大写' if self.shift else '小写'))
    def type_char(self,c):
        e=self.keyboard_target
        start,end=e.get_selection_bounds() or (e.get_position(),e.get_position())
        value=e.get_text();text=c.upper() if self.shift else c;e.set_text(value[:start]+text+value[end:]);e.set_position(start+len(text))
    def backspace(self):
        e=self.keyboard_target;value=e.get_text();a,b=e.get_selection_bounds() or (e.get_position(),e.get_position())
        if a==b:a=max(0,a-1)
        e.set_text(value[:a]+value[b:]);e.set_position(a)
    def symbols(self):
        self.symbol_mode=not self.symbol_mode
        chars=('!@#$%^&*()-_+=.:/\\?[]{}<>~`|;,\'"' if self.symbol_mode else '1234567890qwertyuiopasdfghjklzxcvbnm')
        for i,b in enumerate(self.key_buttons):b.set_label(chars[i%len(chars)])
    def toggle_keyboard(self):
        if self.keyboard.get_visible():self.keyboard.hide()
        else:self.keyboard.set_no_show_all(False);self.keyboard.show_all();self.keyboard.set_no_show_all(True)
        self.fit_screen()
    def key(self,_,event):
        if event.keyval==Gdk.KEY_Escape:self.hide_center();return True
        return False
    def hide_center(self):self.hide();self.reset_hand();self.closed()
    def open(self,wifi=False):
        self.fit_screen()
        self.show_all()
        if self.keyboard.get_no_show_all():self.keyboard.hide()
        self.tabs.set_current_page(1 if wifi else 0);self.present();self.reset_hand()
        if wifi:self.act('scan')
    def ignore(self,key):self.dismiss(key);self.message.set_text('已保持未连接；设备接回后会自动检查。')
    def act(self,op,**data):
        if self.pending is not None:return
        token=self.operation(op,**data)
        if token is None:self.message.set_text('已有连接操作正在进行，请稍候。');return
        self.pending=token;self.busy(True);self.message.set_text('正在搜索可用网络…' if op=='scan' else '正在连接，请稍候…')
    def busy(self,value):
        for b in (self.scan,self.connect_button,*self.retries.values()):b.set_sensitive(not value)
    def complete(self,token,op,result):
        if token!=self.pending:return
        self.pending=None;self.busy(False)
        message=result.get('message') or result.get('error') or ('操作完成' if result.get('ok') else '操作未完成，请重试')
        if result.get('ok') and op in ('scan','wifi_retry'):message='已找到 %d 个可用网络，请选择后连接。'%len(result.get('networks',[]))
        if result.get('ok') and op=='connect':
            message='已连接 '+str((result.get('network') or {}).get('ssid',self.ssid.get_text()))
            if result.get('autoconnect') is True:message+=' · 已保存，下次自动连接'
        if not result.get('ok'):
            prefix={'no-adapter':'未发现无线网卡，请接好后重试。','radio-disabled':'无线网络暂不可用，请检查网卡或飞行模式。',
                    'timeout':'连接超时，请检查热点、密码和无线网卡后重试。','invalid':'请检查网络名称和密码。'}.get(result.get('error_code'),'操作未完成，请检查设备、网络名称或密码后重试。')
            message=prefix+' '+str(message)
        self.message.set_text(str(message)[:240])
        if 'networks' in result:self.set_networks(result['networks'])
        if op=='connect' and result.get('ok'):self.password.set_text('')
    def select_row(self,_,row):
        self.selected=row.network if row is not None else None
        if self.selected:self.ssid.set_text(self.selected.get('ssid',''));self.password.set_text('')
    def set_networks(self,networks):
        self.networks=list(networks);self.rows=[]
        for row in self.list.get_children():self.list.remove(row)
        for network in self.networks:
            row=Gtk.ListBoxRow();row.network=network
            label=Gtk.Label(label='%s  ·  信号 %s%%  ·  %s%s'%(network.get('ssid') or '隐藏网络',network.get('signal',0),network.get('security') or '开放', '  · 已连接' if network.get('connected') else ''))
            label.set_xalign(0);label.set_ellipsize(3);label.set_margin_top(7);label.set_margin_bottom(7);row.add(label);self.list.add(row);self.rows.append(row)
        self.list.show_all()
        if not networks:self.message.set_text('未发现可用网络。请确认无线网卡、手机热点和 Wi‑Fi 已开启，再重新搜索。')
    def connect_wifi(self):
        ssid=self.ssid.get_text()
        network=dict(self.selected or {})
        if network.get('ssid')!=ssid:network=dict(ssid=ssid,security='WPA2',hidden=True)
        if not ssid:self.message.set_text('请先选择或输入 Wi‑Fi 名称。');return
        self.act('connect',network=network,password=self.password.get_text())
    def update_snapshot(self,snapshot):
        self.snapshot=snapshot
        for key,label in self.labels.items():label.set_text(str(snapshot.get(key,{}).get('name',key))+' · '+str(snapshot.get(key,{}).get('detail','正在检查…')))
        self.wifi_status.set_text(str(snapshot.get('wifi',{}).get('detail','正在检查…')))
    def reset_hand(self):
        self.hand_target=None;self.hand_since=0.;self.hand_key=None;self.hand_time=None;self.pointer=None;self.hand_progress=0.;self.cursor_layer.queue_draw()
    def draw_cursor(self,_,cr):
        if self.pointer is None:return False
        x,y=self.pointer;w,h=self.get_size();x*=w;y*=h
        cr.set_source_rgba(.08,.22,.20,.85);cr.arc(x,y,8,0,math.tau);cr.fill()
        cr.set_source_rgba(.75,.35,.10,.95);cr.set_line_width(3);cr.arc(x,y,14,-math.pi/2,-math.pi/2+math.tau*self.hand_progress);cr.stroke();return False
    def update_hand(self,result,key,now=None):
        """Use the same normalized right-hand pointer; dwell only on fresh observations."""
        now=time.monotonic() if now is None else now
        cursor=result.get('cursor')
        if not self.get_visible() or cursor is None:self.reset_hand();return
        if key==self.hand_key:return
        if self.hand_time is not None and now-self.hand_time>.22:self.reset_hand()
        self.hand_time=now;self.pointer=tuple(cursor)
        self.hand_key=key
        x,y=cursor;w,h=self.get_size();target=None
        for b in self.buttons+self.rows:
            if not b.get_mapped() or not b.get_sensitive():continue
            translated=b.translate_coordinates(self,0,0)
            if translated is None:continue
            a=b.get_allocation();bx,by=translated
            if bx<=x*w<=bx+a.width and by<=y*h<=by+a.height:target=b;break
        if target is not self.hand_target:self.hand_target=target;self.hand_since=now
        self.hand_progress=min(1.,(now-self.hand_since)/2.) if target is not None else 0.;self.cursor_layer.queue_draw()
        if target is not None and now-self.hand_since>=2.:
            self.reset_hand()
            if isinstance(target,Gtk.ListBoxRow):self.list.select_row(target)
            elif isinstance(target,Gtk.Entry):target.grab_focus();self.target(target)
            else:target.clicked()
