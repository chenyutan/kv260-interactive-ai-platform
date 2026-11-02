"""Native display trial dialog; human Keep and independent automatic rollback."""
import math
from pathlib import Path
import threading
import time
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import display_modes
from render_policy import bounded_window_size


class DisplaySettings(Gtk.Window):
    def __init__(self, parent=None, closed=None, state_dir=display_modes.DEFAULT_STATE,
                 inspect_fn=display_modes.inspect, trial_fn=display_modes.trial,
                 confirm_fn=display_modes.confirm, restore_fn=display_modes.restore_transaction,
                 clock=time.monotonic):
        super().__init__(title='墨境 · 显示设置')
        if parent:self.set_transient_for(parent);self.set_destroy_with_parent(True)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.closed_callback=closed;self.state_dir=Path(state_dir)
        self.inspect_fn=inspect_fn;self.trial_fn=trial_fn;self.confirm_fn=confirm_fn
        self.restore_fn=restore_fn;self.clock=clock
        self.pending=False;self.is_closed=False;self.attempt=None;self.timer=None
        self.last_result=None;self.last_error=None;self.close_delivered=False
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        root.set_border_width(18);self.add(root)
        title=Gtk.Label(label='低分辨率 · 大屏流畅模式');title.set_xalign(0)
        root.pack_start(title,False,False,0)
        self.current=Gtk.Label(label='正在读取显示模式…');self.current.set_xalign(0)
        self.current.set_line_wrap(True);root.pack_start(self.current,False,False,0)
        explanation=Gtk.Label(label='推荐 960×540（16:9）。试用后请确认画面完整可见；15秒内未选择保留，会自动恢复原模式。')
        explanation.set_line_wrap(True);explanation.set_max_width_chars(52)
        explanation.set_xalign(0);root.pack_start(explanation,False,False,0)
        self.progress=Gtk.ProgressBar();root.pack_start(self.progress,False,False,0)
        self.notice=Gtk.Label(label='尚未修改显示设置。');self.notice.set_line_wrap(True)
        self.notice.set_max_width_chars(55);self.notice.set_xalign(0)
        root.pack_start(self.notice,True,True,0)
        actions=Gtk.Box(spacing=10);root.pack_start(actions,False,False,0)
        self.try_button=Gtk.Button(label='试用 960×540');self.try_button.connect('clicked',self.try_mode)
        self.keep_button=Gtk.Button(label='画面正常，保留');self.keep_button.connect('clicked',self.keep_mode)
        self.restore_button=Gtk.Button(label='恢复原显示');self.restore_button.connect('clicked',self.restore_mode)
        for button in (self.try_button,self.keep_button,self.restore_button):actions.pack_start(button,True,True,0)
        self.close_button=Gtk.Button(label='关闭');self.close_button.connect('clicked',self.close)
        root.pack_start(self.close_button,False,False,0)
        self.connect('delete-event',lambda *_:(self.close() or True))
        self.connect('destroy',self.close)
        self.connect('hide',self.hidden)
        self.fit_screen();self.show_all();self.update_buttons()
        self.read_current()

    def fit_screen(self):
        screen=self.get_screen();parent=self.get_transient_for()
        window=parent.get_window() if parent else None
        monitor=screen.get_monitor_at_window(window) if window else screen.get_primary_monitor()
        area=screen.get_monitor_workarea(monitor)
        self.set_default_size(*bounded_window_size(area.width,area.height,(650,340)))
        self.resize(*bounded_window_size(area.width,area.height,(650,340)))

    def update_buttons(self):
        live=(self.attempt is not None and
              self.clock()<self.attempt['confirm_before_monotonic'])
        self.try_button.set_sensitive(not self.pending and not self.attempt and not self.is_closed)
        self.keep_button.set_sensitive(not self.pending and live and not self.is_closed)
        self.restore_button.set_sensitive(not self.pending and self.attempt is not None and not self.is_closed)

    def work(self,fn,done):
        self.pending=True;self.update_buttons()
        def background():
            result=error=None
            try:result=fn()
            except Exception as exc:error=str(exc)
            # Closing while trial() is still in its worker must not leave the
            # newly switched mode until a GUI callback runs. Roll back directly
            # in this worker; its independent watchdog is a second safety net.
            if self.is_closed and isinstance(result,dict) and result.get('transaction'):
                try:self.restore_fn(result['transaction'])
                except Exception:pass
            GLib.idle_add(self.completed,done,result,error)
        threading.Thread(target=background,daemon=True).start()

    def completed(self,done,result,error):
        self.pending=False;self.last_result=result;self.last_error=error
        if self.is_closed:
            self.deliver_close();return False
        done(result,error);self.update_buttons();return False

    def read_current(self):
        if self.pending or self.is_closed:return
        def loaded(result,error):
            if error:
                self.current.set_text('无法读取物理显示模式。');self.notice.set_text(error+'；内部流畅渲染仍可使用。')
                return
            outputs=[o for o in result.get('outputs',[]) if o.get('current_mode')]
            self.current.set_text('当前：'+' / '.join('%s · %s · %s Hz'%(o['name'],o['current_mode'],o.get('current_rate','?')) for o in outputs))
        self.work(self.inspect_fn,loaded)

    def try_mode(self,*_):
        if self.pending or self.attempt or self.is_closed:return
        self.notice.set_text('正在备份原模式、启动独立回退保护…')
        self.work(lambda:self.trial_fn('960x540',self.state_dir),self.trial_finished)

    def trial_finished(self,result,error):
        if error:
            self.notice.set_text('显示模式试用失败：'+error+'。保留原显示，使用内部流畅渲染。')
            return
        self.attempt=result;self.fit_screen()
        self.current.set_text('试用：960×540 · 名义60 Hz（实际刷新率以显示器回报为准）')
        if self.timer is None:self.timer=GLib.timeout_add(100,self.countdown)
        self.countdown()

    def countdown(self):
        if self.is_closed or self.attempt is None:
            self.timer=None;return False
        remaining=max(0.,self.attempt['confirm_before_monotonic']-self.clock())
        self.progress.set_fraction(min(1.,remaining/display_modes.ROLLBACK_SECONDS))
        self.notice.set_text('请确认画面完整可见：%d秒后自动恢复。'%math.ceil(remaining))
        self.update_buttons()
        if remaining<=0:
            self.timer=None;self.restore_mode();return False
        return True

    def stop_countdown(self):
        if self.timer is not None:GLib.source_remove(self.timer);self.timer=None

    def keep_mode(self,*_):
        if self.pending or self.attempt is None or self.is_closed:return
        if self.clock()>=self.attempt['confirm_before_monotonic']:
            self.restore_mode();return
        self.stop_countdown();token=self.attempt['token']
        self.notice.set_text('正在保存你确认可见的显示模式…')
        def saved(result,error):
            if error:
                self.notice.set_text('未能保留：'+error+'。正在恢复原显示…')
                # completed() releases the pending gate only after its callback,
                # so schedule this follow-up rather than reentering the worker.
                GLib.idle_add(self.restore_mode)
                return
            self.attempt=None;self.progress.set_fraction(0.)
            self.notice.set_text('已保存960×540偏好；下次仅为同一屏幕恢复，并保留启动回退保护。')
        self.work(lambda:self.confirm_fn(token,self.state_dir),saved)

    def restore_mode(self,*_):
        if self.pending or self.attempt is None:return False
        self.stop_countdown();path=self.attempt['transaction']
        self.notice.set_text('正在恢复试用前的显示模式…')
        def restored(result,error):
            self.attempt=None;self.progress.set_fraction(0.);self.fit_screen()
            status=(result or {}).get('status')
            if error or status=='rollback_failed':
                self.notice.set_text('恢复需要检查：'+str(error or result.get('rollback_error')))
            else:
                self.notice.set_text('已恢复原显示模式，内部流畅渲染继续可用。')
                self.current.set_text('已恢复试用前的显示模式。')
        self.work(lambda:self.restore_fn(path),restored);return False

    def hidden(self,*_):
        if not self.is_closed:self.close()

    def deliver_close(self):
        if self.close_delivered:return
        self.close_delivered=True
        if self.closed_callback:self.closed_callback()
        self.destroy()

    def close(self,*_):
        if self.is_closed:return
        self.is_closed=True;self.stop_countdown()
        attempt=self.attempt;self.attempt=None
        self.hide()
        if attempt:
            # The parent may be closing too. Restoration cannot depend on its
            # event loop; the watchdog remains alive if the process exits now.
            def restore():
                try:self.restore_fn(attempt['transaction'])
                finally:GLib.idle_add(self.deliver_close)
            threading.Thread(target=restore,daemon=True).start()
        elif not self.pending:self.deliver_close()


if __name__=='__main__':
    window=DisplaySettings(closed=Gtk.main_quit);Gtk.main()
