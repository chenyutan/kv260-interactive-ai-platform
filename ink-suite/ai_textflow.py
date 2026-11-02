"""Lossless text ranges, bounded scrolling and a presentation-only reveal clock.

The host supplies validated, complete responses. This module never calls a
model or represents the reveal clock as network streaming.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TextLine:
    start:int
    end:int


@dataclass(frozen=True)
class TextPage:
    start:int
    end:int
    lines:tuple


def paginate(text,line_starts,lines_per_page):
    """Partition every character once, including spaces and newlines."""
    if not isinstance(text,str):raise TypeError('Text must be a string')
    if type(lines_per_page) is not int or lines_per_page<1:raise ValueError('Invalid page capacity')
    lines=continuous_lines(text,line_starts)
    return tuple(TextPage(part[0].start,part[-1].end,part)
                 for i in range(0,len(lines),lines_per_page)
                 for part in (lines[i:i+lines_per_page],))


def continuous_lines(text,line_starts):
    """Preserve every character without dividing the answer into pages."""
    if not isinstance(text,str):raise TypeError('Text must be a string')
    if not text:return ()
    starts=tuple(line_starts)
    if not starts or starts[0]!=0 or any(type(n) is not int for n in starts):raise ValueError('Invalid line offsets')
    if any(a>=b for a,b in zip(starts,starts[1:])) or starts[-1]>=len(text):raise ValueError('Invalid line ordering')
    ends=starts[1:]+(len(text),)
    return tuple(TextLine(a,b) for a,b in zip(starts,ends))


class ScrollWindow:
    """A viewport offset; memory usage never depends on document pixel height."""
    def __init__(self):self.offset=0.;self.target=0.;self.limit=0.;self.follow=False

    @property
    def moving(self):return abs(self.target-self.offset)>.01

    def reset(self,follow=False):self.offset=0.;self.target=0.;self.limit=0.;self.follow=bool(follow)

    def set_limit(self,value):
        self.limit=max(0.,float(value));self.target=min(self.target,self.limit);self.offset=min(self.offset,self.limit)

    def seek(self,value,manual=True,snap=False):
        if manual:self.follow=False
        previous=(self.offset,self.target)
        self.target=max(0.,min(self.limit,float(value)))
        if snap:self.offset=self.target
        return previous!=(self.offset,self.target)

    def move(self,delta,manual=True):return self.seek(self.target+delta,manual)

    def tick(self,dt):
        if not self.moving:return False
        before=self.offset;dt=max(0.,min(.1,float(dt)))
        self.offset+=(self.target-self.offset)*(1.-math.exp(-dt/.07))
        if abs(self.target-self.offset)<.2:self.offset=self.target
        return self.offset!=before


def utf8_line_starts(text,byte_starts):
    """Translate Pango byte offsets to Python character offsets exactly."""
    offsets={0:0};count=0
    for index,char in enumerate(text):
        count+=len(char.encode('utf8'));offsets[count]=index+1
    starts=[]
    for value in byte_starts:
        if value not in offsets:raise ValueError('Line splits a UTF-8 character')
        char=offsets[value]
        if char<len(text) and (not starts or starts[-1]!=char):starts.append(char)
    return tuple(starts)


class Reveal:
    def __init__(self,cps=60.):
        if not math.isfinite(cps) or not 1<=cps<=500:raise ValueError('Invalid reveal speed')
        self.cps=float(cps);self.total=0;self.shown=0;self.started=0.

    @property
    def active(self):return self.shown<self.total

    def begin(self,total,now,animated=True):
        self.total=max(0,int(total));self.shown=0 if animated else self.total;self.started=float(now)

    def advance(self,now):
        previous=self.shown
        self.shown=max(self.shown,min(self.total,int(max(0.,float(now)-self.started)*self.cps)))
        return self.shown!=previous

    def finish(self):
        changed=self.active;self.shown=self.total;return changed
