"""Thread-safe interaction ownership. Inference continues in every experience."""
import threading
from circle_detector import CircleDetector

# environment remains accepted only for archived photo tools; the current menu
# and host launch only these five experiences.
MODES=('ink','sword','fight','fruit','taiko')
LEGACY_MODES=('environment',)

class SuiteControl:
    def __init__(self):
        self.lock=threading.RLock();self.detector=CircleDetector(hands=('right',))
        self.active=False;self.mode='ink';self.menu=False;self.control_active=False
        self.open_serial=0;self.hand=None;self.epoch=0
        self.preview=False
        self.circle_diagnostic={'reason':'inactive'}
    def snapshot(self):
        with self.lock:return dict(active=self.active,mode=self.mode,menu=self.menu,preview=self.preview,control_active=self.control_active,open_serial=self.open_serial,hand=self.hand,epoch=self.epoch,circle_diagnostic=dict(self.circle_diagnostic))
    def configure(self,command):
        with self.lock:
            mode=command.get('mode',self.mode);menu=command.get('menu',self.menu);active=command.get('active',self.active)
            preview=command.get('preview',False)
            if mode not in MODES+LEGACY_MODES or type(menu) is not bool or type(active) is not bool or type(preview) is not bool:raise ValueError('Invalid suite mode')
            if preview and not (active and menu):raise ValueError('Pose preview requires paused suite ownership')
            self.preview=preview
            self.mode=mode;self.menu=menu;self.active=active;self.control_active=False;self.detector.reset();self.epoch+=1
            self.circle_diagnostic={'reason':'inactive' if not active else 'menu_open' if menu else 'collecting'}
            if not active:self.mode='ink';self.menu=False;self.hand=None
            return self.snapshot()
    def observe(self,points,t,width,height):
        with self.lock:
            if not self.active or self.menu:return self.snapshot()
            result=self.detector.update(points,t,width,height)
            self.control_active=result['control_active']
            self.circle_diagnostic=dict(result.get('diagnostic') or {'reason':'unavailable'})
            if result['open']:
                self.menu=True;self.open_serial+=1;self.hand=result['hand'];self.epoch+=1
            return self.snapshot()
    def ink_allowed(self,epoch=None):
        with self.lock:
            return (epoch is None or epoch==self.epoch) and (not self.active or (self.mode=='ink' and not self.menu))
    def paint_deferred(self,epoch):
        with self.lock:
            return self.ink_allowed(epoch) and self.active and self.control_active
