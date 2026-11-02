"""Deterministic native photo controls; no synthetic pointer or key events."""
import os
import time
from pathlib import Path

from gi.repository import Gtk, Pango, Gdk


PHOTO_SUFFIXES = frozenset(('.jpg', '.jpeg', '.png', '.webp'))


class DirectionRepeat:
    def __init__(self):
        self.direction = None
        self.next_repeat = 0.

    def update(self, snapshot, now=None):
        now = time.monotonic() if now is None else now
        buttons = set(snapshot.get('buttons', ())) | set(snapshot.get('pressed', ()))
        axes = snapshot.get('axes', {})
        x = -1 if 'LEFT' in buttons else 1 if 'RIGHT' in buttons else axes.get('lx', 0)
        y = -1 if 'UP' in buttons else 1 if 'DOWN' in buttons else axes.get('ly', 0)
        direction = ('RIGHT' if x > 0 else 'LEFT') if abs(x) > .5 and abs(x) >= abs(y) else ('DOWN' if y > 0 else 'UP') if abs(y) > .5 else None
        if direction is None:
            self.direction = None
            return None
        changed = direction != self.direction
        if not changed and now < self.next_repeat:
            return None
        self.direction = direction
        self.next_repeat = now + (.34 if changed else .16)
        return direction


class PhotoControls:
    """Explicit ordered controls. Combo boxes change value without a popup."""
    def __init__(self, actions):
        self.actions = tuple(actions)
        self.current = None
        self.repeat = DirectionRepeat()

    def eligible(self):
        return tuple(w for w in self.actions if w.get_visible() and w.is_sensitive())

    def focus(self, widget):
        if self.current is not None:
            self.current.get_style_context().remove_class('photo-pad-focus')
        self.current = widget
        if widget is not None:
            widget.get_style_context().add_class('photo-pad-focus')
            widget.grab_focus()

    def refresh(self):
        choices = self.eligible()
        if self.current not in choices:
            self.focus(choices[0] if choices else None)
        return choices

    @staticmethod
    def cycle(combo, step):
        model = combo.get_model()
        count = len(model) if model is not None else 0
        if count:
            active = combo.get_active()
            combo.set_active((active + step) % count if active >= 0 else 0)

    def update(self, snapshot, now=None):
        choices = self.refresh()
        if not choices:
            return
        direction = self.repeat.update(snapshot, now)
        if direction in ('LEFT', 'RIGHT') and isinstance(self.current, Gtk.ComboBox):
            self.cycle(self.current, -1 if direction == 'LEFT' else 1)
        elif direction:
            step = -1 if direction in ('LEFT', 'UP') else 1
            self.focus(choices[(choices.index(self.current) + step) % len(choices)])
        if 'A' in snapshot.get('pressed', ()):
            if isinstance(self.current, Gtk.ComboBox):
                self.cycle(self.current, 1)
            elif isinstance(self.current, Gtk.Button):
                self.current.clicked()


class PhotoFileChooser(Gtk.Dialog):
    """Small native image browser whose entire flow is controller accessible.

    Directory enumeration runs outside the GTK loop and never opens a photo.
    The result has the familiar get_filename()/response() dialog interface.
    """
    gamepad_owns_shoulders = True

    def __init__(self, parent=None, initial=None):
        super().__init__(title='选择风景照片', transient_for=parent, modal=True)
        self.set_default_size(760, 440)
        self.set_destroy_with_parent(True)
        self.filename = None
        self.folder = None
        self.paths = []
        self.pending_selection = None
        self.scan_epoch = 0
        self.destroyed = False
        self.repeat = DirectionRepeat()
        self.focus_area = 'files'
        self._focus_restore_pending = False
        self.connect('destroy', self._destroyed)
        content = self.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)
        toolbar = Gtk.Box(spacing=8)
        self.up = Gtk.Button(label='上一级  LB')
        self.up.connect('clicked', lambda *_: self.open_folder(self.folder.parent) if self.folder else None)
        toolbar.pack_start(self.up, False, False, 0)
        self.location = Gtk.ComboBoxText()
        homes = (('U盘 / 外部存储', Path('/media/ubuntu')), ('其他外部存储', Path('/media')),
                 ('挂载目录', Path('/mnt')), ('图片', Path.home()/'Pictures'),
                 ('主目录', Path.home()), ('文件系统', Path('/')))
        self.locations = tuple((name, path) for name, path in homes if path.is_dir())
        for index, (name, _) in enumerate(self.locations):
            self.location.append(str(index), name)
        self.location.set_active(0)
        self.location.set_tooltip_text('常用位置：↑↓ 或 A 切换')
        self.location.connect('changed', self._location_changed)
        toolbar.pack_start(self.location, False, False, 0)
        self.path_label = Gtk.Label()
        self.path_label.set_xalign(0)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        toolbar.pack_start(self.path_label, True, True, 0)
        content.pack_start(toolbar, False, False, 0)
        self.listbox = Gtk.ListBox()
        # An empty list must own initial focus while its rows load off-thread.
        self.listbox.set_can_focus(True)
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect('row-selected', self._selected)
        self.listbox.connect('row-activated', lambda _, row: self.activate_row(row))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.listbox)
        content.pack_start(scroll, True, True, 0)
        self.message = Gtk.Label(label='↑↓ / 左摇杆选文件 · A 打开目录或选照片 · ←→ 选择按钮 · LB 上一级 · B 取消')
        self.message.set_xalign(0)
        self.message.set_line_wrap(True)
        self.message.set_lines(2);self.message.set_ellipsize(Pango.EllipsizeMode.END)
        content.pack_start(self.message, False, False, 0)
        self.cancel = self.add_button('取消  B', Gtk.ResponseType.CANCEL)
        self.accept = self.add_button('选择照片  A', Gtk.ResponseType.OK)
        for control in (self.up,self.location,self.cancel,self.accept):
            control.set_size_request(-1,48)
        self.accept.set_sensitive(False)
        self.controls = (self.up, self.location, self.listbox, self.cancel, self.accept)
        css = Gtk.CssProvider()
        self.get_style_context().add_class('photo-file-chooser')
        css.load_from_data(b'''
            .photo-file-chooser button {font-size:16px;min-height:36px;padding:5px 11px;}
            .photo-file-chooser list row {min-height:48px;}
            .photo-file-chooser list label {font-size:16px;}
            .photo-pad-focus {box-shadow:inset 0 0 0 3px #168676;background-image:none;background-color:#d7ece4;}
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for widget in self.controls:
            # GTK assigns initial focus asynchronously. That is not a user
            # navigation decision and must never select "up" for the first A.
            widget.connect('focus-in-event', self._native_focus_changed)
            widget.connect('button-press-event', self._mouse_focus)
        # set-focus also fires for an inactive window, where X11 does not yet
        # send focus-in-event. Keep the visual and logical selection aligned.
        self.connect('set-focus',lambda _,widget:self._native_focus_changed(widget))
        self.connect('key-press-event',self.key_press)
        self.show_all()
        target = Path(initial) if initial else next((p for _, p in self.locations if p != Path('/')), Path.home())
        self.open_folder(target)
        self.set_area(self.listbox)

    def _destroyed(self, *_):
        self.destroyed = True
        self.scan_epoch += 1

    def _mouse_focus(self, widget, *_):
        self.set_area(widget, grab=False)
        return False

    def _native_focus_changed(self, widget, *_):
        from gi.repository import GLib
        if self.destroyed or widget is None or widget is self.focus_area:
            return False
        if isinstance(self.focus_area,Gtk.Widget) and widget.is_ancestor(self.focus_area):
            return False
        if not self._focus_restore_pending:
            self._focus_restore_pending = True
            def restore():
                self._focus_restore_pending = False
                if not self.destroyed and self.focus_area in self.controls:
                    self.set_area(self.focus_area)
                return False
            GLib.idle_add(restore)
        return False

    def get_filename(self):
        return str(self.filename) if self.filename else None

    def get_current_folder(self):
        return str(self.folder) if self.folder else None

    def set_current_folder(self, folder):
        self.open_folder(folder)
        return True

    def set_filename(self, filename):
        path = Path(filename).absolute()
        if not path.is_file() or path.suffix.lower() not in PHOTO_SUFFIXES:
            return False
        self.pending_selection = path
        self.open_folder(path.parent)
        return True

    def _location_changed(self, combo):
        index = combo.get_active()
        if index >= 0:
            self.open_folder(self.locations[index][1])

    def _selected(self, _, row):
        path = getattr(row, 'photo_path', None)
        # The scan worker already identified the kind. A disconnected USB
        # filesystem must not cause a fresh stat() in the GTK event callback.
        self.filename = path if getattr(row,'photo_is_file',False) else None
        self.accept.set_sensitive(self.filename is not None)

    def set_area(self, widget, grab=True):
        for control in self.controls:
            control.get_style_context().remove_class('photo-pad-focus')
        self.focus_area = widget
        widget.get_style_context().add_class('photo-pad-focus')
        if grab:
            row = self.listbox.get_selected_row() if widget is self.listbox else None
            target = row or widget
            if target.get_can_focus():self.set_focus(target)
            target.grab_focus()

    def open_folder(self, folder):
        import threading
        from gi.repository import GLib
        folder = Path(folder).absolute()
        self.scan_epoch += 1
        epoch = self.scan_epoch
        self.folder = folder
        self.paths = []
        self.path_label.set_text(str(folder))
        self.up.set_sensitive(folder.parent != folder)
        self.filename = None
        self.accept.set_sensitive(False)
        self.message.set_text('正在读取目录…')
        for row in self.listbox.get_children():
            self.listbox.remove(row)

        def work():
            try:
                entries = []
                with os.scandir(folder) as listing:
                    for item in listing:
                        if item.name.startswith('.'):
                            continue
                        is_dir = item.is_dir()
                        if is_dir or Path(item.name).suffix.lower() in PHOTO_SUFFIXES:
                            entries.append((not is_dir, item.name.casefold(), item.name))
                entries.sort()
                error = None
            except OSError as exc:
                entries = []
                error = str(exc)

            def deliver():
                if self.destroyed or epoch != self.scan_epoch:
                    return False
                self.paths = [folder / name for _, _, name in entries]
                for is_file, _, name in entries:
                    row = Gtk.ListBoxRow()
                    row.photo_path = folder/name
                    row.photo_is_file = bool(is_file)
                    row.connect('focus-in-event',self._native_focus_changed)
                    row.connect('button-press-event',lambda *_:self._row_focus())
                    label = Gtk.Label(label=('照片  ' if is_file else '目录  ')+name)
                    label.set_xalign(0)
                    label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                    label.set_margin_start(10)
                    label.set_margin_top(7)
                    label.set_margin_bottom(7)
                    row.add(label)
                    self.listbox.add(row)
                self.listbox.show_all()
                selected = next((r for r in self.listbox.get_children() if r.photo_path == self.pending_selection), self.listbox.get_row_at_index(0))
                self.pending_selection = None
                if selected:
                    self.listbox.select_row(selected)
                self.message.set_text(('无法读取目录。请检查U盘，按 RB 重试，或按 B 取消。') if error else
                                      ('此目录没有可用照片。LB 返回上一级，RB 刷新；支持 JPG / PNG / WebP。' if not entries else
                                       '↑↓ 选文件 · A 打开目录或选照片 · ←→ 选择按钮 · LB 上一级 · RB 刷新 · B 取消'))
                if self.focus_area is self.listbox:
                    self.set_area(self.listbox)
                return False
            GLib.idle_add(deliver)
        threading.Thread(target=work, daemon=True, name='photo-file-browser').start()

    def _row_focus(self):
        self.set_area(self.listbox,grab=False)
        return False

    def activate_row(self, row):
        path = getattr(row, 'photo_path', None)
        if path is None:
            return
        if not getattr(row,'photo_is_file',False):
            self.open_folder(path)
        elif path.suffix.lower() in PHOTO_SUFFIXES:
            self.filename = path
            self.response(Gtk.ResponseType.OK)

    def key_press(self,_,event):
        # Native Tab would otherwise be mistaken for an automatic GTK focus
        # change. Make keyboard navigation an explicit owner just like the pad.
        key={Gdk.KEY_Up:'UP',Gdk.KEY_Down:'DOWN',Gdk.KEY_Left:'LEFT',Gdk.KEY_Right:'RIGHT',
             Gdk.KEY_Return:'A',Gdk.KEY_KP_Enter:'A',Gdk.KEY_Escape:'B',Gdk.KEY_BackSpace:'LB',
             Gdk.KEY_F5:'RB'}.get(event.keyval)
        if event.keyval in (Gdk.KEY_Tab,Gdk.KEY_ISO_Left_Tab):
            eligible=tuple(w for w in self.controls if w.is_sensitive())
            step=-1 if event.keyval==Gdk.KEY_ISO_Left_Tab or event.state&Gdk.ModifierType.SHIFT_MASK else 1
            current=self.focus_area if self.focus_area in eligible else self.listbox
            self.set_area(eligible[(eligible.index(current)+step)%len(eligible)])
            return True
        if key:
            self.gamepad(dict(pressed=(),buttons=(),axes={}))
            self.gamepad(dict(pressed=(key,),buttons=(),axes={}))
            return True
        return False

    def gamepad(self, snapshot):
        pressed = snapshot.get('pressed', ())
        if 'B' in pressed or 'BACK' in snapshot.get('released', ()):
            self.response(Gtk.ResponseType.CANCEL)
            return
        if 'LB' in pressed:
            if self.up.is_sensitive():self.up.clicked()
            return
        if 'RB' in pressed:
            if self.folder:self.open_folder(self.folder)
            return
        direction = self.repeat.update(snapshot)
        eligible = tuple(w for w in self.controls if w.is_sensitive())
        if self.focus_area not in eligible:
            self.set_area(self.listbox)
        current = self.focus_area
        if direction in ('LEFT', 'RIGHT'):
            step = -1 if direction == 'LEFT' else 1
            self.set_area(eligible[(eligible.index(current)+step) % len(eligible)])
        elif direction in ('UP', 'DOWN'):
            step = -1 if direction == 'UP' else 1
            if current is self.listbox:
                row = self.listbox.get_selected_row()
                index = max(0, (row.get_index() if row else 0)+step)
                target = self.listbox.get_row_at_index(index)
                if target:
                    self.listbox.select_row(target)
                    self.set_area(self.listbox)
            elif current is self.location:
                PhotoControls.cycle(self.location, step)
            else:
                self.set_area(eligible[(eligible.index(current)+step) % len(eligible)])
        if 'A' in pressed:
            current = self.focus_area
            if current is self.listbox:
                self.activate_row(self.listbox.get_selected_row())
            elif current is self.location:
                PhotoControls.cycle(self.location, 1)
            elif current.is_sensitive():
                current.clicked()
