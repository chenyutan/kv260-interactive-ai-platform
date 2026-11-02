"""Game clocks depend on a live source, never on visible body joints.

This module never forwards controls or generates events. Missing poses must be
handled as neutral input by each game's existing detector. Explicit host/menu
and controller-disconnect pauses remain outside this source-only grace policy.
"""
import math
from collections import deque


def _number(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


class SourceGapPolicy:
    def __init__(self,timeout=2.5):
        value=_number(timeout)
        if value is None or value<=2.:raise ValueError('Source grace must exceed two seconds')
        self.timeout=value
        self.reset()

    def reset(self,now=None):
        self.started=_number(now)
        self.last_now=self.started
        self.last_source=None
        self.identity=None
        self.retired=deque(maxlen=8)
        self.blocked=False
        self.reason='source_grace'

    def update(self,state,now,offline=False):
        """Return True only for a sustained absence of fresh source progress."""
        now=_number(now)
        if now is None:
            self.blocked=True;self.reason='invalid_time';return True
        if self.last_now is not None and now<self.last_now:self.reset(now)
        if self.started is None:self.started=now
        self.last_now=now
        if offline:
            self.last_source=now;self.blocked=False;self.reason='offline';return False
        if isinstance(state,dict):
            pid=state.get('pid');stamp=_number(state.get('timestamp_seconds'))
            seq=_number(state.get('sequence'));count=_number(state.get('pose_count'))
            valid_pid=isinstance(pid,(int,str)) and not isinstance(pid,bool) and bool(pid)
            valid_seq=seq is not None and seq>=0 and seq.is_integer()
            valid_count=('pose_count' not in state or
                         (count is not None and count>=0 and count.is_integer()))
            if valid_pid and pid not in self.retired and stamp is not None and valid_seq and valid_count and -.10<=now-stamp<=self.timeout:
                old=self.identity
                progress=(old is None or pid!=old[0] or
                    (stamp>old[2] and ((count is not None and old[3] is not None and count>old[3]) or
                     ((count is None or old[3] is None) and seq>old[1]))))
                if progress:
                    if old is not None and pid!=old[0]:self.retired.append(old[0])
                    self.identity=(pid,seq,stamp,count);self.last_source=stamp
        reference=self.last_source if self.last_source is not None else self.started
        self.blocked=now-reference>self.timeout
        self.reason='source_timeout' if self.blocked else 'source_live' if self.last_source is not None else 'source_grace'
        return self.blocked

    def snapshot(self,now):
        now=_number(now)
        return dict(blocked=self.blocked,reason=self.reason,timeout=self.timeout,
                    source_age=None if now is None or self.last_source is None else max(0.,now-self.last_source))
