"""Gamepad controls for native utility dialogs, without application ownership.

The host owns dialog discovery and pointer processing. This helper only moves
native GTK focus, activates the focused control, and requests a safe close.
It never changes game pause state, dispatches a game action, or opens devices.
"""
import math
import time
import weakref
from gi.repository import Gtk


class DialogNavigation:
    def __init__(self, first_repeat=.32, repeat=.13, threshold=.5):
        self.first_repeat=max(.1,float(first_repeat))
        self.repeat=max(.05,float(repeat))
        self.threshold=max(.2,min(.9,float(threshold)))
        self._window=None
        self._direction=None
        self._next_repeat=0.

    @staticmethod
    def _eligible(widget):
        return bool(widget is not None and widget.get_visible() and
                    widget.get_mapped() and widget.is_sensitive() and
                    widget.get_can_focus())

    @classmethod
    def _candidates(cls, dialog):
        result=[]
        def visit(widget):
            if not widget.get_visible() or not widget.get_mapped() or not widget.is_sensitive():return
            before=len(result)
            if isinstance(widget,Gtk.Container):
                for child in widget.get_children():visit(child)
            # Prefer the actionable leaves over a scroll/notebook focus proxy.
            if len(result)==before and widget is not dialog and cls._eligible(widget):
                result.append(widget)
        visit(dialog)
        return result

    @classmethod
    def _focus(cls, dialog):
        focus=dialog.get_focus()
        if cls._eligible(focus):return focus
        candidates=cls._candidates(dialog)
        default=getattr(dialog,'get_default_widget',lambda:None)()
        wanted=default if cls._eligible(default) else candidates[0] if candidates else None
        if wanted is not None:wanted.grab_focus()
        return wanted

    @classmethod
    def _move_focus(cls, dialog, before, direction):
        candidates=cls._candidates(dialog)
        if not candidates:return None
        if before not in candidates:
            candidates[0].grab_focus();return candidates[0]
        def center(widget):
            at=widget.translate_coordinates(dialog,0,0)
            if at is None:return None
            a=widget.get_allocation()
            return at[0]+a.width/2.,at[1]+a.height/2.
        origin=center(before)
        dx,dy={'left':(-1,0),'right':(1,0),'up':(0,-1),'down':(0,1)}[direction]
        ranked=[]
        if origin:
            for i,widget in enumerate(candidates):
                point=center(widget)
                if point is None:continue
                x,y=point[0]-origin[0],point[1]-origin[1]
                along=x*dx+y*dy
                if along>.5:ranked.append((math.hypot(x,y)+1.8*abs(x*dy-y*dx),i))
        if ranked:wanted=candidates[min(ranked)[1]]
        else:
            step=-1 if direction in ('left','up') else 1
            wanted=candidates[(candidates.index(before)+step)%len(candidates)]
        wanted.grab_focus()
        return wanted

    def _axis(self, axes, key):
        try:value=float(axes.get(key,0.))
        except (TypeError,ValueError):return 0.
        return value if math.isfinite(value) else 0.

    def _wanted_direction(self, snapshot):
        buttons=set(snapshot.get('buttons',()))|set(snapshot.get('pressed',()))
        axes=snapshot.get('axes',{})
        x=-1. if 'LEFT' in buttons else 1. if 'RIGHT' in buttons else self._axis(axes,'lx')
        y=-1. if 'UP' in buttons else 1. if 'DOWN' in buttons else self._axis(axes,'ly')
        if abs(x)>self.threshold and abs(x)>=abs(y):return 'right' if x>0 else 'left'
        if abs(y)>self.threshold:return 'down' if y>0 else 'up'
        return None

    @staticmethod
    def _cancel(dialog):
        # ConnectionCenter is reusable and must run its closed callback.
        # Prefer its semantic action even though delete-event also protects it.
        if callable(getattr(dialog,'hide_center',None)):
            dialog.hide_center()
        elif callable(getattr(dialog,'response',None)):
            dialog.response(Gtk.ResponseType.CANCEL)
        elif callable(getattr(dialog,'close',None)):
            dialog.close()
        else:
            dialog.hide()

    def update(self, dialog, snapshot, now=None, back_release_consumed=False):
        """Return handled/action/moved/activated metadata for one host tick.

        `snapshot` is a GamepadReader snapshot. The host must process its
        DesktopPointer first and forward back_release_consumed so View+R3
        right-click does not also dismiss the dialog. A disconnected snapshot
        and an invisible dialog have no effects.
        """
        result=dict(handled=False,action=None,moved=False,activated=False)
        if not snapshot.get('connected') or not dialog.get_visible() or not dialog.get_mapped():
            self._direction=None
            return result
        old=self._window() if self._window else None
        if old is not dialog:
            self._window=weakref.ref(dialog)
            self._direction=None
            self._next_repeat=0.
        now=time.monotonic() if now is None else float(now)
        pressed=set(snapshot.get('pressed',()))
        released=set(snapshot.get('released',()))
        if 'B' in pressed or ('BACK' in released and not back_release_consumed):
            self._direction=None
            self._cancel(dialog)
            result.update(handled=True,action='cancel')
            return result
        if 'A' in pressed:
            focus=self._focus(dialog)
            if focus is not None:
                activated=bool(focus.activate())
                # Some GTK controls report False but still emit their public
                # activate signal. Never synthesize a second click here.
                result.update(handled=True,action='activate',activated=activated)
            return result
        direction=self._wanted_direction(snapshot)
        if direction is None:
            self._direction=None
            return result
        changed=direction!=self._direction
        if not changed and now<self._next_repeat:return result
        self._direction=direction
        self._next_repeat=now+(self.first_repeat if changed else self.repeat)
        before=dialog.get_focus()
        if not self._eligible(before):
            after=self._focus(dialog)
        else:
            after=self._move_focus(dialog,before,direction)
        result.update(handled=True,action='navigate',moved=after is not None and after is not before)
        return result
