"""Native AI assistant UI. Rendering/state only: no API, camera or worker IO."""
import io
import math
import re
import time
from bisect import bisect_right
from collections import OrderedDict
from copy import deepcopy

import gi
gi.require_version('Gtk','3.0');gi.require_version('Gdk','3.0');gi.require_version('PangoCairo','1.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Pango, PangoCairo
from menu_view import MenuView, cairo
from section_menu import SectionMenuView, rounded
from ai_assistant.catalog import ACTIONS, ELEMENTS, THEME_NAMES, resolved_theme
from ai_textflow import continuous_lines,utf8_line_starts,Reveal,ScrollWindow


DEFAULT_PRESETS={
    'guide':(
        dict(id='rules',title='玩法讲解',description='目标、规则与基本流程'),
        dict(id='controls',title='怎么操作',description='体感与手柄操作说明'),
        dict(id='tips',title='进阶建议',description='适合下一步尝试的方向')),
    'creation':(
        dict(id='bamboo',title='竹林与远山',description='清雅竹林，远山留白'),
        dict(id='plum',title='梅花小景',description='梅枝与花朵，疏密有致'),
        dict(id='landscape',title='山水画卷',description='层叠山峦，云雾相间'),
        dict(id='mixed',title='自由组合',description='让 AI 安排一幅完整构图')),
    'review':(
        dict(id='overview',title='复盘本次游玩',description='看看各项表现与亮点'),
        dict(id='improve',title='下一局怎么进步',description='给出具体、简短的建议')),
    'health':(
        dict(id='next',title='后续练习',description='根据自己的跟练记录'),
        dict(id='review',title='复习建议',description='回顾已经练过的动作'),
        dict(id='demo',title='先看示范',description='记录不足时先熟悉动作')),
}
STATUS_LABELS={'queued':'等待生成','running':'正在生成','ready':'已完成','error':'生成未完成'}
ID=re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_UNSET=object()
ACTION_NAMES=dict(A01='双臂展开',A02='单臂上举',A03='双臂上举',
                  A04='手柄选 A04',A05='手柄选 A05',A06='双手上托')
BG=(.025,.047,.085);PANEL=(.047,.082,.132);TEXT=(.90,.95,1.)
MUTED=(.56,.69,.81);CYAN=(.30,.86,.98);BODY_SIZE=18;LINE_HEIGHT=28


def clean(value,limit=600):
    """Model output remains plain text, including any literal markup."""
    if not isinstance(value,str):return ''
    text=''.join(c for c in value if c in '\n\t' or ord(c)>=32)
    return (text if limit is None else text[:limit]).strip()


def step_labels(step,index):
    action=clean(step.get('action'),80);theme=step.get('theme')
    title=f'{index+1}. {ACTION_NAMES.get(action,action)}'
    try:
        element=ELEMENTS[resolved_theme(theme,action)][ACTIONS.index(action)]
        subtitle=f'{THEME_NAMES[theme]} · {element} · {action}'
    except (KeyError,ValueError):subtitle=clean(step.get('instruction'),80)
    return title,subtitle


def _centered_origin(layout,x,y,width,height):
    # Pango logical line boxes include font ascent/descent and leading. Center
    # the actual shaped ink, including mixed Chinese/Latin and multiline text.
    ink,_=layout.get_pixel_extents()
    return x+(width-ink.width)/2-ink.x,y+(height-ink.height)/2-ink.y


def block(cr,value,x,y,width,height,size=13,color=(.18,.29,.25),bold=False,centered=False):
    layout=PangoCairo.create_layout(cr)
    desc=Pango.FontDescription('Noto Sans CJK SC')
    desc.set_absolute_size(size*Pango.SCALE)
    if bold:desc.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(desc);layout.set_text(clean(value,2000),-1)
    layout.set_width(max(1,round(width*Pango.SCALE)))
    layout.set_height(max(1,round(height*Pango.SCALE)))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR);layout.set_ellipsize(Pango.EllipsizeMode.END)
    if centered:layout.set_alignment(Pango.Alignment.CENTER)
    left,top=_centered_origin(layout,x,y,width,height) if centered else (x,y)
    cr.save();cr.rectangle(x,y,width,height);cr.clip()
    cr.set_source_rgb(*color);cr.move_to(left,top);PangoCairo.show_layout(cr,layout);cr.restore()
    return layout


class AIView(SectionMenuView):
    """Host routes every confirmation through select(choice).

    Local ai_kind/ai_page/ai_job choices change only this view. External ids:
    ai_preset:<id>, ai_review:<id>, ai_apply:<jobid>:<step_index>, ai_refresh, ai_back.
    choices and rectangles contain only enabled actions in matching order.
    """
    def __init__(self,on_action,characters_per_second=60.):
        self.on_action=on_action;self.kind='creation';self.configured=False
        self.capabilities={};self.presets=deepcopy(DEFAULT_PRESETS)
        from ai_assistant.projects import project_catalog
        self.projects=tuple(project_catalog());self.project='ink'
        self.return_label='返回主菜单';self.project_picker=False;self.clear_confirm=False
        self.local_help=False;self.submitting=False;self.clearing=False;self.history_cleared=False
        self.health_summary={};self._thinking_phase=0;self._thinking_masks=OrderedDict()
        self.jobs=();self.job_index=0;self.preset_page=0;self.step_index=0
        self.notice=''
        self.share_url=None;self.qr=None;self._qr_key=None;self._revision=0
        self._items=();self._actions=();self._pad_serial=0
        self.phone_open=False;self.full_text='';self.text_lines=();self._line_starts=();self.step_offsets=()
        self.scroll=ScrollWindow();self._scroll_time=None;self._drag_y=None
        self._fx_level=0;self._fx_until=0.
        self.reveal=Reveal(characters_per_second);self._seen_ready={};self._snapshot_seen=False
        self._document_key=None;self.document_builds=0;self._body_key=None;self._body_surface=None;self.body_builds=0
        self._caption_key=None;self._caption_surface=None;self.caption_builds=0
        self._line_layouts=OrderedDict()
        super().__init__(self.select)
        self.add_events(Gdk.EventMask.SCROLL_MASK|Gdk.EventMask.SMOOTH_SCROLL_MASK|
                        Gdk.EventMask.POINTER_MOTION_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.connect('scroll-event',self.mouse_scroll)
        self.connect('motion-notify-event',self.drag_motion)
        self.connect('button-release-event',self.drag_release)
        self.message='手柄：方向键选择 · A 确认 · B 返回　｜　体感：右手停留确认'
        self._refresh()

    @property
    def choices(self):return tuple(item['id'] for item in self._actions)

    @property
    def rectangles(self):
        return tuple((x/960,y/540,(x+w)/960,(y+h)/540)
                     for x,y,w,h in (item['rect'] for item in self._actions))

    @property
    def current_jobs(self):return tuple(job for job in self.jobs if job['kind']==self.kind and
        (self.kind not in ('guide','health') or job.get('project')==self.project))

    @property
    def current_job(self):
        jobs=self.current_jobs
        return jobs[min(self.job_index,len(jobs)-1)] if jobs else None

    @property
    def busy(self):return self.clearing or self.submitting or any(job['status'] in ('queued','running') for job in self.jobs)

    @property
    def overlay_open(self):return self.phone_open or self.project_picker or self.clear_confirm

    @property
    def waiting(self):
        job=self.current_job
        return bool(not self.local_help and (self.submitting or job and job['status'] in ('queued','running')))

    @property
    def showing_local(self):return self.kind in ('guide','health') and not self.waiting and self.local_help

    @property
    def project_title(self):return next((p['title'] for p in self.projects if p['id']==self.project),'项目指南')

    @property
    def course_summary(self):
        return self.health_summary if self.health_summary.get('course_id')==self.project else {}

    def set_return_label(self,text):
        text=clean(text,16) or '返回主菜单'
        if text==self.return_label:return False
        self.return_label=text;self._refresh();return True

    def set_submitting(self,value):
        value=bool(value)
        if value==self.submitting:return False
        self.submitting=value
        if value:self.local_help=False;self.reveal.finish()
        self._refresh();return True

    def clear_finished(self):
        self.clear_confirm=False;self.submitting=False;self.clearing=False;self.local_help=False
        self.history_cleared=True
        self.jobs=();self.job_index=0;self.step_index=0;self._seen_ready.clear()
        self.reveal.finish();self._refresh()

    def set_clearing(self,value):
        value=bool(value)
        if value==self.clearing:return False
        self.clearing=value;self._refresh();return True

    @property
    def steps(self):
        job=self.current_job
        if self.kind!='creation' or not job or job['status']!='ready':return ()
        result=job['result'] or {};values=result.get('steps')
        return tuple(step for step in values[:4] if isinstance(step,dict)) if isinstance(values,list) else ()

    def set_context(self,kind=None,capabilities=None,share_url=_UNSET,qr=_UNSET,project=None,return_label=None,health_summary=_UNSET):
        kind=self.kind if kind is None else kind
        if kind not in DEFAULT_PRESETS:raise ValueError('Unknown AI context')
        before=(self.kind,self.project,self.return_label,self.configured,deepcopy(self.presets),self.projects,self.share_url,self._qr_key,self.phone_open,self.project_picker,self.clear_confirm,self.submitting,self.local_help,deepcopy(self.health_summary))
        if kind=='health' and project is None and self.project not in ('baduanjin','taichi'):project='baduanjin'
        if kind=='health' and project is not None and project not in ('baduanjin','taichi'):raise ValueError('Choose a health course')
        if project is not None and project not in {p['id'] for p in self.projects}:raise ValueError('Unknown AI project')
        changed=kind!=self.kind or project is not None and project!=self.project
        if project is not None:self.project=project
        if return_label is not None:self.return_label=clean(return_label,16) or '返回主菜单'
        if health_summary is not _UNSET:self.health_summary=deepcopy(health_summary) if isinstance(health_summary,dict) else {}
        if changed:
            self.reveal.finish();self.kind=kind;self.job_index=0;self.preset_page=0;self.step_index=0
            self.submitting=False;self.local_help=False;self.clear_confirm=False;self.project_picker=False
            # A deliberate content/course change rearms body dwell. The
            # controller preserves its action identity independently; data
            # polling without a content change must leave observed input alone.
            self.clear_pointer();self.sample_key=None;self.hover=None;self.progress=0.
            self.previous_hover=None;self.previous_progress=0.
        if share_url is None and qr is None:
            self.phone_open=False;self.project_picker=False;self.clear_confirm=False
            self.submitting=False;self.local_help=False;self._snapshot_seen=False;self.reveal.finish()
        if capabilities is not None:
            caps=capabilities if isinstance(capabilities,dict) else {}
            self.capabilities=dict(caps);self.configured=caps.get('configured') is True
            available=caps.get('presets') or {}
            for key in DEFAULT_PRESETS:
                values=available.get(key) if isinstance(available,dict) else None
                if not isinstance(values,(list,tuple)):continue
                parsed=[];seen=set()
                for item in values[:24]:
                    if not isinstance(item,dict):continue
                    ident=item.get('id');title=clean(item.get('title'),80)
                    if not isinstance(ident,str) or not ID.fullmatch(ident) or not title or ident in seen:continue
                    parsed.append(dict(id=ident,title=title,description=clean(item.get('description'),160)))
                    seen.add(ident)
                self.presets[key]=tuple(parsed) or DEFAULT_PRESETS[key]
        # Polls update capabilities without repeating QR bytes. Omission keeps
        # the current visitor's share data; explicit None clears it on entry.
        next_url=self.share_url if share_url is _UNSET else clean(share_url,2048) or None
        update_qr=(qr is not _UNSET or next_url!=self.share_url or share_url is None)
        self.share_url=next_url
        if qr is _UNSET:qr=None
        qr_key=((self.share_url,bytes(qr) if isinstance(qr,(bytes,bytearray)) else id(qr) if qr is not None else None)
                if update_qr else self._qr_key)
        if qr_key!=self._qr_key:
            self._qr_key=qr_key;self.qr=None
            try:
                if isinstance(qr,GdkPixbuf.Pixbuf):self.qr=qr
                elif isinstance(qr,(bytes,bytearray)):
                    self.qr=cairo.ImageSurface.create_from_png(io.BytesIO(qr))
                elif self.share_url:
                    from visitor.portal import qr_png
                    self.qr=cairo.ImageSurface.create_from_png(io.BytesIO(qr_png(self.share_url)))
            except (ImportError,ValueError,TypeError,cairo.Error):self.qr=None
        if before==(self.kind,self.project,self.return_label,self.configured,self.presets,self.projects,self.share_url,self._qr_key,self.phone_open,self.project_picker,self.clear_confirm,self.submitting,self.local_help,self.health_summary):return False
        self._refresh();return True

    def set_jobs(self,jobs):
        old=self.current_job;old_id=old['id'] if old else None
        old_status={job['id']:job['status'] for job in self.jobs}
        values=[];seen=set()
        for job in (jobs if isinstance(jobs,(list,tuple)) else ())[:40]:
            if not isinstance(job,dict):continue
            ident=job.get('id');kind=job.get('kind');status=job.get('status')
            if not isinstance(ident,str) or not ID.fullmatch(ident) or ident in seen or kind not in DEFAULT_PRESETS or status not in STATUS_LABELS:continue
            result=deepcopy(job.get('result')) if isinstance(job.get('result'),dict) else None
            project=clean(job.get('project'),64)
            if kind=='guide' and project not in {p['id'] for p in self.projects}:continue
            if kind=='health' and project not in ('baduanjin','taichi'):continue
            values.append(dict(id=ident,kind=kind,project=project,status=status,result=result,
                error=clean(job.get('error'),400),preset=clean(job.get('preset'),64)))
            seen.add(ident)
        if tuple(values)==self.jobs:return False
        self.jobs=tuple(values)
        if values:self.history_cleared=False
        # Keep an explicitly browsed older result selected while polling.
        if self.job_index and old_id:
            self.job_index=next((i for i,j in enumerate(self.current_jobs) if j['id']==old_id),0)
        else:self.job_index=0
        if not old or not self.current_job or old['id']!=self.current_job['id']:self.step_index=0
        current=self.current_job
        animate=bool(current and current['status']=='ready' and current['id'] not in self._seen_ready
            and (old_status.get(current['id']) in ('queued','running') or self._snapshot_seen)
            and self.get_mapped() and not self.overlay_open and not self.local_help)
        if values:self._snapshot_seen=True
        self._refresh(animated=animate)
        for item in values:
            if item['status']=='ready':self._seen_ready[item['id']]=None
        while len(self._seen_ready)>512:self._seen_ready.pop(next(iter(self._seen_ready)))
        return True

    def set_notice(self,message=''):
        notice=clean(message,600)
        if notice==self.notice:return False
        self.notice=notice;self._revision+=1;self.cache_key=None;self.queue_draw();return True

    def _ensure_timer(self):
        if self.get_mapped() and self.timer is None:
            self.timer=GLib.timeout_add(40,self.animate)

    def mapped(self,*args):
        self.last_tick=None;self._scroll_time=None;self.selection_dirty=True;self._ensure_timer()

    def animate(self,now=None):
        if not self.get_mapped():self.timer=None;return False
        now=time.monotonic() if now is None else now
        previous=(self._visible_signature(),self._caption_state())
        dt=.04 if self._scroll_time is None else max(0.,min(.1,now-self._scroll_time))
        self._scroll_time=now
        if not self.overlay_open and self.reveal.active and self.reveal.advance(now):
            self._update_scroll_limit()
            if self.scroll.follow:self.scroll.seek(self.scroll.limit,manual=False)
            self._fx_until=now+.16
        self._fx_level=(3 if self.reveal.active else max(0,min(3,math.ceil((self._fx_until-now)/.16*3))))
        self.scroll.tick(dt)
        if previous!=(self._visible_signature(),self._caption_state()):self._invalidate_answer()
        thinking=self.waiting and not self.overlay_open
        if thinking:
            phase=int(now*12)%36
            if phase!=self._thinking_phase:
                self._thinking_phase=phase
                w,h=self.get_allocated_width(),self.get_allocated_height()
                self.invalidate((278*w/960,214*h/540,640*w/960,68*h/540))
        SectionMenuView.animate(self,now)
        if not thinking and not self.reveal.active and not self.scroll.moving and not self._fx_level and self.target is None and not self.trail and not self.selection_dirty:
            self.timer=None;return False
        return True

    @property
    def body_rect(self):return (278,202,640,196 if self.kind=='creation' else 252)

    def _prepare_document(self,animated=False):
        job=self.current_job;result=job['result'] if job and job['status']=='ready' and not self.submitting else None
        local=self.showing_local
        if local:
            if self.kind=='health':
                result=self.course_summary.get('local_help')
                if not result:
                    from ai_assistant.health import summarize
                    result=summarize([],0,self.project)['local_help']
            else:
                from ai_assistant.projects import project_help
                result=project_help(self.project)
        parts=[];offsets=[]
        if result:
            parts=[clean(result.get('title'),None),clean(result.get('summary'),None)]
            if self.kind=='creation':
                for index,step in enumerate(self.steps):
                    offsets.append(len('\n\n'.join(parts))+2)
                    title,subtitle=step_labels(step,index)
                    parts.append(title+' · '+subtitle+'\n'+clean(step.get('instruction'),None))
            else:
                tips=result.get('tips') if isinstance(result.get('tips'),list) else []
                parts += [f'{i+1}. '+clean(tip,None) for i,tip in enumerate(tips)]
        text='\n\n'.join(parts)
        key=(job['id'] if job else None,self.kind,self.project,local,text)
        if key==self._document_key:return
        self._document_key=key;self.full_text=text;self.step_offsets=tuple(offsets)
        self.text_lines=();self._line_starts=()
        if text:
            layout=PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_RGB24,1,1)))
            desc=Pango.FontDescription('Noto Sans CJK SC');desc.set_absolute_size(BODY_SIZE*Pango.SCALE)
            layout.set_font_description(desc);layout.set_width(self.body_rect[2]*Pango.SCALE)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR);layout.set_text(text,-1)
            starts=utf8_line_starts(text,[line.start_index for line in layout.get_lines_readonly()])
            self.text_lines=continuous_lines(text,starts);self._line_starts=starts
        self.reveal.begin(len(text),time.monotonic(),animated and not local)
        self.scroll.reset(follow=animated);self._update_scroll_limit()
        self._line_layouts.clear()
        self._fx_level=0;self._fx_until=0.;self._drag_y=None;self._scroll_time=None
        self.document_builds+=1;self._body_key=None

    def _update_scroll_limit(self):
        count=bisect_right(self._line_starts,self.reveal.shown-1) if self.reveal.shown else 0
        self.scroll.set_limit(max(0,count*LINE_HEIGHT-self.body_rect[3]))

    def _invalidate_answer(self):
        w,h=self.get_allocated_width(),self.get_allocated_height()
        self.invalidate((260*w/960,152*h/540,676*w/960,310*h/540))

    def scroll_by(self,delta,manual=True):
        try:delta=float(delta)
        except (TypeError,ValueError):return False
        if not math.isfinite(delta) or self.overlay_open or not self.text_lines or self.waiting:return False
        self._update_scroll_limit();was_follow=self.scroll.follow
        changed=self.scroll.move(delta,manual)
        if changed or was_follow!=self.scroll.follow:self._invalidate_answer();self._ensure_timer()
        return changed

    def scroll_follow(self):
        if self.overlay_open or not self.text_lines or self.waiting:return False
        self._update_scroll_limit();changed=not self.scroll.follow;self.scroll.follow=True
        changed=self.scroll.seek(self.scroll.limit,manual=False) or changed
        if changed:self._invalidate_answer();self._ensure_timer()
        return changed

    def controller_scroll(self,snapshot,dt):
        if self.overlay_open or not self.text_lines or self.waiting or not isinstance(snapshot,dict):return False
        buttons=set(snapshot.get('buttons') or ());pressed=set(snapshot.get('pressed') or ())
        keys=(buttons|pressed)&{'LB','RB'}
        if not keys:return False
        if keys=={'LB','RB'}:return True
        direction=1 if 'RB' in keys else -1
        try:dt=float(dt)
        except (TypeError,ValueError):dt=0.
        if not math.isfinite(dt):dt=0.
        delta=210*max(0.,min(.05,dt)) if keys&buttons else LINE_HEIGHT
        self.scroll_by(direction*delta);return True

    def _refresh(self,animated=False):
        old_choice=self.choices[self.hover] if self.hover in range(len(self.choices)) else None
        self.preset_page=min(self.preset_page,max(0,(len(self.presets[self.kind])-1)//3))
        self.job_index=min(self.job_index,max(0,len(self.current_jobs)-1))
        self.step_index=min(self.step_index,max(0,len(self.steps)-1))
        self._prepare_document(animated)
        items=[]
        def add(ident,title,rect,subtitle='',enabled=True,active=False,role='action'):
            items.append(dict(id=ident,title=title,subtitle=subtitle,rect=rect,enabled=enabled,active=active,role=role))
        add('ai_back',self.return_label,(816,20,120,50),role='tool')
        if self.clear_confirm:
            add('ai_clear_cancel','取消',(278,376,240,56))
            add('ai_clear_confirm','确认清空',(546,376,240,56))
        elif self.project_picker:
            projects=tuple(p for p in self.projects if self.kind!='health' or p['id'] in ('baduanjin','taichi'))
            for index,project in enumerate(projects):
                row,column=divmod(index,4)
                add('ai_project:'+project['id'],project['title'],(36+column*224,160+row*72,212,64),
                    {'entertainment':'娱乐','health':'健康','creation':'创作','system':'技术与应用'}.get(project['section'],''),active=project['id']==self.project)
            add('ai_project_close','返回回答',(24,478,212,50))
        elif self.phone_open:
            add('ai_phone_close','返回回答',(24,478,212,50))
            add('ai_refresh','刷新结果',(724,478,212,50))
        else:
            # Content destinations form one navigation row. Operational tools
            # live in the header, with a quieter style and their own hit areas.
            add('ai_refresh','刷新',(456,20,96,50),role='tool')
            add('ai_phone','手机输入',(564,20,96,50),role='tool')
            add('ai_clear','正在清空…' if self.clearing else '清空全部建议',(672,20,132,50),
                '全终端 · 保留作品',enabled=not self.clearing,role='tool')
            destinations=(('ai_kind:guide','项目指南',self.kind=='guide' and self.project!='system'),
                          ('ai_kind:creation','创作方案',self.kind=='creation'),
                          ('ai_kind:review','成绩复盘',self.kind=='review'),
                          ('ai_kind:health','健康训练',self.kind=='health'),
                          ('ai_system','项目介绍',self.kind=='guide' and self.project=='system'))
            for index,(ident,title,active) in enumerate(destinations):
                add(ident,title,(24+index*186,86,168,50),active=active,role='section')
            if self.kind=='guide' and self.project=='system':
                from ai_assistant.projects import SYSTEM_PRESETS
                presets=SYSTEM_PRESETS
            else:presets=self.presets[self.kind]
            offset=self.preset_page*3
            if self.kind in ('guide','health'):add('ai_projects','选择课程' if self.kind=='health' else '选择项目',(36,198,196,56),self.project_title)
            for row,item in enumerate(presets[offset:offset+3]):
                prefix={'creation':'ai_preset:','review':'ai_review:','guide':'ai_guide:','health':'ai_health:'}[self.kind]
                add(prefix+item['id'],item['title'],(36,(262 if self.kind in ('guide','health') else 198)+row*64,196,56),item['description'],self.configured and not self.busy)
            if len(presets)>3:
                add('ai_page:next',f'更多主题  {self.preset_page+1}/{(len(presets)+2)//3}',(36,396,196,50))
            add('ai_job:previous','上一条',(24,478,104,50),enabled=self.job_index>0)
            add('ai_job:next','下一条',(140,478,104,50),enabled=self.job_index+1<len(self.current_jobs))
            # Stable controls even at either end or after revealing;
            # typing cannot change the controller or dwell target ordering.
            add('ai_scroll:up','上滚',(260,478,88,50),enabled=bool(self.text_lines))
            add('ai_scroll:down','下滚',(358,478,88,50),enabled=bool(self.text_lines))
            add('ai_scroll:follow','跟随',(456,478,88,50),enabled=bool(self.text_lines))
            add('ai_reveal','显示全文',(554,478,136,50),enabled=bool(self.text_lines))
            if self.kind=='creation':
                for index,step in enumerate(self.steps):
                    title,_=step_labels(step,index)
                    add('ai_step:'+str(index),title,(278+index*164,406,148,48),active=index==self.step_index)
                add('ai_apply_selected',f'采用第 {self.step_index+1} 步' if self.steps else '采用方案',
                    (700,478,236,50),enabled=bool(self.steps))
            elif self.kind in ('guide','health'):
                add('ai_local_help','返回 AI 回答' if self.local_help and self.current_job else '固定说明 · 非AI历史',
                    (700,478,236,50),active=self.showing_local)
        self._items=tuple(items);self._actions=tuple(item for item in items if item['enabled'])
        self.mode_choices=self.choices;self._revision+=1
        if old_choice in self.choices:
            # Rendering, arrival of a result and button availability changes
            # do not fabricate fresh input or erase a live user's focus.
            self.hover=self.choices.index(old_choice)
        else:
            self.clear_pointer();self.sample_key=None;self.hover=None;self.progress=0.
            self.previous_hover=None;self.previous_progress=0.
        self.selection_dirty=True
        self.cache_key=None;self.queue_draw();self._ensure_timer()

    def select(self,choice):
        if choice not in self.choices:return False
        if choice.startswith('ai_kind:'):
            self.set_context(kind=choice.split(':',1)[1])
        elif choice=='ai_system':
            self.set_context(kind='guide',project='system');self.local_help=True;self._refresh()
        elif choice=='ai_projects':self.project_picker=True;self.reveal.finish();self._refresh()
        elif choice=='ai_project_close':self.project_picker=False;self._refresh()
        elif choice.startswith('ai_project:'):
            self.project_picker=False;self.set_context(kind='health' if self.kind=='health' else 'guide',project=choice.split(':',1)[1])
            self.local_help=self.kind=='guide' and self.current_job is None;self._refresh()
        elif choice=='ai_local_help':
            self.local_help=not self.local_help if self.current_job else True
            self.reveal.finish();self._refresh()
        elif choice=='ai_clear':self.on_action('ai_clear')
        elif choice=='ai_clear_cancel':self.clear_confirm=False;self._refresh()
        elif choice=='ai_clear_confirm':
            self.on_action('ai_clear')
        elif choice=='ai_page:next':
            self.preset_page=(self.preset_page+1)%((len(self.presets[self.kind])+2)//3);self._refresh()
        elif choice.startswith('ai_job:'):
            self.local_help=False;self.reveal.finish();self.job_index+=-1 if choice.endswith('previous') else 1;self.step_index=0;self._refresh()
        elif choice.startswith('ai_step:'):
            self.reveal.finish();self.step_index=int(choice.split(':',1)[1])
            offset=self.step_offsets[self.step_index]
            self._update_scroll_limit();self._fx_level=0;self._fx_until=0.
            self.scroll.seek(max(0,bisect_right(self._line_starts,offset)-1)*LINE_HEIGHT,manual=True)
            self._refresh()
        elif choice=='ai_scroll:follow':self.scroll_follow()
        elif choice.startswith('ai_scroll:'):self.scroll_by((-1 if choice.endswith('up') else 1)*LINE_HEIGHT*3)
        elif choice=='ai_reveal':
            self.reveal.finish();self._fx_level=0;self._fx_until=0.;self._update_scroll_limit()
            if self.scroll.follow:self.scroll.seek(self.scroll.limit,manual=False)
            self._invalidate_answer();self._ensure_timer()
        elif choice in ('ai_phone','ai_phone_close'):
            self.reveal.finish();self._fx_level=0;self._fx_until=0.;self._update_scroll_limit()
            self.phone_open=choice=='ai_phone';self._refresh()
        elif choice=='ai_apply_selected':
            self.set_notice('')
            self.on_action(f"ai_apply:{self.current_job['id']}:{self.step_index}")
        else:
            if choice.startswith(('ai_preset:','ai_review:','ai_apply:','ai_guide:','ai_health:')):
                self.local_help=False;self.set_notice('')
            self.on_action(choice)
        return True

    def hit_index(self,x,y):
        try:x,y=float(x),float(y)
        except (TypeError,ValueError):return None
        if not math.isfinite(x) or not math.isfinite(y):return None
        return next((i for i,(a,b,c,d) in enumerate(self.rectangles) if a<=x<c and b<=y<d),None)

    def click(self,_,event):
        if event.button!=1:return False
        if self._in_body(event.x,event.y):
            self.scroll_by(0);self._drag_y=event.y;return True
        index=self.hit_index(event.x/max(1,self.get_allocated_width()),event.y/max(1,self.get_allocated_height()))
        return self.select(self.choices[index]) if index is not None else False

    def _in_body(self,x,y):
        if self.overlay_open or not self.text_lines or self.waiting:return False
        left,top,width,height=self.body_rect
        x=x*960/max(1,self.get_allocated_width());y=y*540/max(1,self.get_allocated_height())
        return left<=x<left+width and top<=y<top+height

    def mouse_scroll(self,_,event):
        if not self._in_body(event.x,event.y):return False
        if event.direction==Gdk.ScrollDirection.SMOOTH:
            ok,_,dy=event.get_scroll_deltas()
            if not ok:return False
        else:dy=-1 if event.direction==Gdk.ScrollDirection.UP else 1 if event.direction==Gdk.ScrollDirection.DOWN else 0
        self.scroll_by(dy*LINE_HEIGHT*3);return True

    def drag_motion(self,_,event):
        if self._drag_y is None:return False
        delta=(self._drag_y-event.y)*540/max(1,self.get_allocated_height());self._drag_y=event.y
        self.scroll_by(delta);return True

    def drag_release(self,_,event):
        active=self._drag_y is not None;self._drag_y=None;return active

    def set_availability(self,ready,message=''):
        # Camera readiness controls only hand input. Mouse/controller return,
        # local presets and phone instructions remain usable without a camera.
        self.selection_enabled=bool(ready)
        if message and message!=self.message:self.message=clean(message,240);self.cache_key=None;self.queue_draw()
        if not ready:self.update_selection({})

    def set_gamepad_focus(self,index):
        if isinstance(index,bool) or not isinstance(index,int) or index not in range(len(self.choices)):return False
        a,b,c,d=self.rectangles[index];self._pad_serial+=1
        self.update_selection(dict(cursor=((a+c)/2,(b+d)/2),hover=index,progress=0.,sample_key=('ai-pad',self._pad_serial)))
        return True

    def unmapped(self,*args):
        self.reveal.finish();self._body_key=None;self._fx_level=0;self._fx_until=0.;self._drag_y=None
        self.submitting=False
        self._update_scroll_limit();self.scroll.seek(self.scroll.target,manual=False,snap=True)
        MenuView.unmapped(self,*args)
        self.hover=None;self.progress=0.;self.previous_hover=None;self.previous_progress=0.
        self.selection_dirty=True

    def update_selection(self,state):
        state=dict(state or {});pointer=state.get('cursor')
        try:
            if len(pointer)!=2 or not all(math.isfinite(float(v)) and 0<=float(v)<=1 for v in pointer):pointer=None
        except (TypeError,ValueError):pointer=None
        index=state.get('hover')
        if isinstance(index,bool) or not isinstance(index,int) or index not in range(len(self.choices)):index=None
        try:
            progress=float(state.get('progress',0.));progress=max(0.,min(1.,progress)) if math.isfinite(progress) else 0.
        except (TypeError,ValueError):progress=0.
        if pointer is not None and 'sample_key' not in state:self._pad_serial+=1;state['sample_key']=('ai-direct',self._pad_serial)
        state.update(cursor=pointer,hover=index,progress=progress if index is not None else 0.)
        MenuView.update_selection(self,state)
        self._ensure_timer()

    @staticmethod
    def _pixel_rect(rect,size):
        x,y,width,height=rect;w,h=size
        left,top=math.floor(x*w/960),math.floor(y*h/540)
        return left,top,math.ceil((x+width)*w/960)-left,math.ceil((y+height)*h/540)-top

    @classmethod
    def _region_image(cls,rect,size):
        """Rasterize at the target size once, retaining logical text metrics."""
        left,top,width,height=cls._pixel_rect(rect,size)
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height);cr=cairo.Context(image)
        cr.set_source_rgb(*PANEL);cr.paint()
        w,h=size;x,y,_,_=rect
        # Preserve the exact logical origin, including fractional pixels at
        # 720p. Stable frames only blit this native-size surface without scaling.
        cr.translate(x*w/960-left,y*h/540-top);cr.scale(w/960,h/540)
        return image,cr

    @staticmethod
    def _blit_region(cr,image,rect):
        x,y,width,height=rect
        cr.save();cr.rectangle(x,y,width,height);cr.clip()
        cr.set_source_surface(image,x,y);cr.paint();cr.restore()

    def _visible_signature(self):
        if not self.text_lines:return ()
        top=self.scroll.offset;height=self.body_rect[3]
        first=max(0,min(len(self.text_lines)-1,math.floor(top/LINE_HEIGHT)))
        last=min(len(self.text_lines),math.ceil((top+height)/LINE_HEIGHT))
        end=max(self.text_lines[first].start,min(self.text_lines[last-1].end,self.reveal.shown))
        recent=max(0,self.reveal.shown-7)
        effect=self._fx_level if recent<self.text_lines[last-1].end and self.reveal.shown>self.text_lines[first].start else 0
        return first,last,round(top,2),end,recent if effect else 0,effect

    def _body_image(self,size=(960,540)):
        if not self.text_lines:return None
        signature=self._visible_signature();first,last,top,end,recent,effect=signature
        key=(self._document_key,signature,tuple(size))
        if key==self._body_key:return self._body_surface
        image,cr=self._region_image(self.body_rect,size)
        desc=Pango.FontDescription('Noto Sans CJK SC');desc.set_absolute_size(BODY_SIZE*Pango.SCALE)
        cursor_line=bisect_right(self._line_starts,self.reveal.shown-1)-1
        for index in range(first,last):
            line=self.text_lines[index]
            value=self.full_text[line.start:min(line.end,end)].rstrip('\r\n')
            if not value:continue
            line_key=(tuple(size),index,value)
            layout=self._line_layouts.pop(line_key,None)
            if layout is None:
                layout=PangoCairo.create_layout(cr);layout.set_font_description(desc);layout.set_text(value,-1)
            self._line_layouts[line_key]=layout
            while len(self._line_layouts)>24:self._line_layouts.popitem(last=False)
            # Only viewport lines are rasterized. No document-height bitmap is
            # allocated, and full-response boundaries stay fixed while typing.
            y=index*LINE_HEIGHT-top
            cr.set_source_rgb(*TEXT);cr.move_to(0,y)
            PangoCairo.show_layout(cr,layout)
            if effect and line.end>recent and line.start<self.reveal.shown:
                start=max(0,recent-line.start)
                left=layout.index_to_pos(len(value[:start].encode('utf8'))).x/Pango.SCALE
                right=layout.get_pixel_size()[0]
                cr.save();cr.rectangle(left,y,max(0,right-left)+2,LINE_HEIGHT);cr.clip()
                cr.move_to(0,y);PangoCairo.layout_path(cr,layout)
                cr.set_source_rgba(*CYAN,.08*effect);cr.set_line_width(2.1);cr.stroke()
                cr.move_to(0,y);cr.set_source_rgba(*CYAN,.16*effect);PangoCairo.show_layout(cr,layout);cr.restore()
                if index==cursor_line:
                    cr.set_source_rgba(*CYAN,.3*effect)
                    cr.rectangle(min(self.body_rect[2]-3,right+2),y+4,2,20);cr.fill()
        self._body_surface=image;self._body_key=key;self.body_builds+=1
        return image

    def _caption_state(self):return self.reveal.active,self.scroll.follow if self.reveal.active else False,self.showing_local

    def _caption_image(self,size):
        key=(tuple(size),self._caption_state())
        if key==self._caption_key:return self._caption_surface
        image,cr=self._region_image((690,168,228,25),size)
        caption='固定说明 · 非AI历史' if self.showing_local else (
            ('逐字呈现 · 自动跟随' if self.scroll.follow else '逐字呈现 · 手动阅读') if self.reveal.active else 'DeepSeek · 完整回答')
        block(cr,caption,0,0,228,25,13,CYAN)
        self._caption_surface=image;self._caption_key=key;self.caption_builds+=1
        return image

    @staticmethod
    def _panel(cr,x,y,width,height):
        rounded(cr,x,y,width,height,12);cr.set_source_rgb(*PANEL);cr.fill_preserve()
        cr.set_source_rgba(*CYAN,.22);cr.set_line_width(1);cr.stroke()
        cr.set_source_rgba(*CYAN,.55);cr.set_line_width(2)
        cr.move_to(x+15,y);cr.line_to(x+55,y);cr.stroke()

    def _phone(self,cr):
        self._panel(cr,24,100,912,362)
        block(cr,'用手机补充你的想法',60,124,810,38,25,TEXT,True)
        if self.qr is not None:
            side=232;cr.save();cr.translate(66,188)
            cr.set_source_rgb(1,1,1);cr.rectangle(-10,-10,side+20,side+20);cr.fill()
            if isinstance(self.qr,GdkPixbuf.Pixbuf):
                cr.scale(side/self.qr.get_width(),side/self.qr.get_height());Gdk.cairo_set_source_pixbuf(cr,self.qr,0,0)
            else:
                cr.scale(side/self.qr.get_width(),side/self.qr.get_height());cr.set_source_surface(self.qr,0,0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST);cr.paint();cr.restore()
        else:
            rounded(cr,60,180,252,252,10);cr.set_source_rgb(.07,.13,.20);cr.fill()
            block(cr,'手机输入已就绪\n请从主菜单扫码' if self.share_url else '连接同一 Wi-Fi\n后显示二维码',
                82,247,208,104,22,MUTED,True)
        block(cr,'01  手机与板卡连接同一网络\n\n02  扫码后选择项目，或输入你的问题\n\n03  回到这里，查看 DeepSeek 的完整建议',
              354,184,532,191,20,TEXT)
        block(cr,'文字由 DeepSeek 联网生成；画面与动作由板端完成。',354,389,532,48,16,MUTED)

    def _empty_result(self,cr):
        job=self.current_job
        if self.waiting:
            block(cr,'DeepSeek 正在准备回答。\n可先返回其他体验，稍后再查看。',280,296,620,88,19,MUTED)
            block(cr,'联网暂不可用时，请选择“本地说明”查看操作。' if self.kind=='guide' else
                '请求结束后自动显示结果；网络不可用会显示提示。',280,398,620,45,15,MUTED)
            return
        if job and job['status']!='ready':
            title=STATUS_LABELS[job['status']]
            message=job['error'] if job['status']=='error' else 'DeepSeek 正在生成建议。\n你可以先返回其他体验，稍后再查看。'
            block(cr,title,280,222,620,44,26,TEXT,True)
            block(cr,message or '本次请求未完成，请稍后重试。',280,288,620,114,19,MUTED)
        else:
            if self.kind=='health':
                summary=self.course_summary
                title='还没有生成训练建议' if not self.history_cleared else 'AI 历史已清空'
                detail=(clean(summary.get('summary'),400) or '尚无本人的练习记录，仍可获得当前课程的具体练习步骤。')
                detail+='\n\n选择左侧方向查看练习建议；点位不清晰时仍提供本式步骤，相关观察仅供参考。'
            elif self.history_cleared:
                title='AI 历史已清空'
                detail='所有账户与旧匿名会话的生成建议已清空。\n作品、成绩和课程记录仍保留。\n\n“固定说明”是操作资料，不属于 AI 历史；可以按需打开。'
            else:
                title='让想法成为下一步' if self.kind=='creation' else '还没有生成建议'
                detail={'creation':'选择左侧主题，或用手机输入你的想法。\n完整回答会在这里连续显示。',
                        'review':'选择左侧复盘方式。\n根据已记录的游戏成绩，给出简短总结和练习建议。',
                        'guide':'选择左侧讲解方向，让 DeepSeek 解答。\n“固定说明”无需联网，不属于生成的 AI 历史。'}[self.kind]
            block(cr,title,280,222,620,44,26,TEXT,True)
            block(cr,detail,280,282,620,167,18,MUTED)

    def _thinking(self,cr,size):
        """Small animated gradient mask. Text is shaped once, not per frame."""
        w,h=size;rect=(278,214,640,68);left,top,width,height=self._pixel_rect(rect,size)
        key=tuple(size);mask=self._thinking_masks.pop(key,None)
        if mask is None:
            mask=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height);mc=cairo.Context(mask)
            mc.scale(w/960,h/540)
            block(mc,'正在思考',0,4,600,48,28,(1.,1.,1.),True)
        self._thinking_masks[key]=mask
        while len(self._thinking_masks)>2:self._thinking_masks.popitem(last=False)
        phase=self._thinking_phase/36.;alpha=.63+.28*(.5+.5*math.sin(phase*2*math.pi))
        gradient=cairo.LinearGradient(left-width*.35+width*phase,top,left+width*.45+width*phase,top)
        gradient.add_color_stop_rgba(0,.20,.58,1.,alpha)
        gradient.add_color_stop_rgba(.5,.62,.98,1.,1.)
        gradient.add_color_stop_rgba(1,.27,.60,1.,alpha)
        cr.save();cr.rectangle(left,top,width,height);cr.clip();cr.set_source(gradient);cr.mask_surface(mask,left,top)
        cr.set_source_rgba(*CYAN,alpha*.42);cr.rectangle(left,top+57*h/540,178*w/960,2*h/540);cr.fill();cr.restore()

    def build_static(self,w,h):
        self.static_surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.static_surface);cr.scale(w/960,h/540)
        gradient=cairo.LinearGradient(0,0,960,540)
        gradient.add_color_stop_rgb(0,.025,.047,.085);gradient.add_color_stop_rgb(1,.035,.08,.13)
        cr.set_source(gradient);cr.paint()
        cr.set_source_rgba(.25,.65,.9,.025);cr.set_line_width(1)
        for x in range(0,960,40):cr.move_to(x,0);cr.line_to(x,540)
        for y in range(0,540,40):cr.move_to(0,y);cr.line_to(960,y)
        cr.stroke()
        SectionMenuView.deepseek_mark(cr,24,18,46,34)
        block(cr,'DeepSeek',82,13,230,37,28,TEXT,True)
        cr.set_source_rgb(*CYAN);cr.rectangle(250,22,2,26);cr.fill()
        block(cr,'AI 助手',266,21,150,28,19,CYAN,True)
        status=('正在清空全部 AI 建议…' if self.clearing else
                '固定资料 · 无需联网 · 非AI历史' if self.showing_local else
                'AI服务待配置，可先看本地说明' if not self.configured else
                '正在生成，可先返回其他体验' if self.waiting else
                '另一个项目正在处理，请稍候' if self.busy else
                'LB/RB 滚动 · Y 跟随 · 选择左侧方向生成建议')
        block(cr,self.notice or status,26,57,418,22,12,MUTED)
        if self.clear_confirm:
            self._panel(cr,184,152,632,310)
            block(cr,'清空全部 AI 建议？',220,188,560,46,28,TEXT,True)
            block(cr,'清空当前账户全部项目、各次游玩的 AI 建议。\n未绑定账户时只清空本次建议；作品、成绩和课程记录保留。',220,260,560,90,19,MUTED)
        elif self.project_picker:
            block(cr,'选择训练课程' if self.kind=='health' else '选择要了解的项目',36,111,850,40,25,TEXT,True)
            block(cr,'建议只基于本人的课程记录，不把关节未观测当作动作错误。' if self.kind=='health' else
                  '每项都有固定说明；“项目介绍”包含技术原理与应用场景。',36,447,850,26,16,MUTED)
        elif self.phone_open:
            self._phone(cr)
        else:
            rounded(cr,18,80,924,62,12);cr.set_source_rgba(.15,.30,.40,.20);cr.fill()
            self._panel(cr,24,152,220,310);self._panel(cr,260,152,676,310)
            block(cr,{'guide':'项目指南','creation':'快捷创作','review':'复盘方向','health':'训练方向'}[self.kind],40,163,188,29,17,CYAN,True)
            block(cr,self.project_title if self.kind=='guide' else self.project_title+' · 训练建议' if self.kind=='health' else
                  '创作方案' if self.kind=='creation' else '本次成绩复盘',278,162,370,31,20,TEXT,True)
            if self.kind=='review':
                block(cr,'基于已记录成绩\n不修改分数或排行榜',40,357,188,78,15,MUTED)
            if not self.text_lines or self.waiting:self._empty_result(cr)
            if self.kind=='review':block(cr,'建议由 DeepSeek 生成',738,488,196,34,14,MUTED)
        for item in self._items:
            x,y,bw,bh=item['rect'];enabled=item['enabled'];active=item['active']
            primary=item['id']=='ai_apply_selected' and enabled
            tool=item['role']=='tool';section=item['role']=='section'
            rounded(cr,x,y,bw,bh,9)
            cr.set_source_rgb(*((.045,.23,.30) if active or primary else (.045,.075,.112) if tool else (.065,.12,.185) if enabled else (.05,.075,.11)))
            cr.fill_preserve();cr.set_source_rgba(*CYAN,.85 if active or primary else .17 if tool else .35 if enabled else .12)
            cr.set_line_width(1.6 if active else 1);cr.stroke()
            if section and active:
                cr.set_source_rgb(*CYAN);cr.rectangle(x+22,y+bh-4,bw-44,2);cr.fill()
            subtitle=item['subtitle'];step=item['id'].startswith('ai_step:')
            font=15 if tool else 18 if section else 16 if step else 17 if bw<155 else 19
            title_y=y+(bh-46)/2 if subtitle else y
            block(cr,item['title'],x+10,title_y,bw-20,27 if subtitle else bh,font,TEXT if enabled else (.35,.45,.55),True,centered=True)
            if subtitle:block(cr,subtitle,x+10,title_y+27,bw-20,19,12,MUTED if enabled else (.35,.45,.55),centered=True)
        self.cache_key=(w,h,self._revision,self.message);self.cache_builds+=1

    def paint(self,cr,size=None):
        w,h=size or (self.get_allocated_width(),self.get_allocated_height())
        if w<1 or h<1:return False
        if self.cache_key!=(w,h,self._revision,self.message):self.build_static(w,h)
        cr.set_source_surface(self.static_surface,0,0);cr.paint()
        if self.text_lines and not self.overlay_open and not self.waiting:
            self._blit_region(cr,self._body_image((w,h)),self._pixel_rect(self.body_rect,(w,h)))
            self._blit_region(cr,self._caption_image((w,h)),self._pixel_rect((690,168,228,25),(w,h)))
            _,by,_,bh=self.body_rect;sy=h/540;sx=w/960
            if self.scroll.limit>0:
                thumb=max(22,bh*bh/(self.scroll.limit+bh))
                y=by+(bh-thumb)*self.scroll.offset/self.scroll.limit
                cr.set_source_rgba(*CYAN,.13);cr.rectangle(925*sx,by*sy,3*sx,bh*sy);cr.fill()
                cr.set_source_rgba(*CYAN,.75);cr.rectangle(925*sx,y*sy,3*sx,thumb*sy);cr.fill()
        if self.waiting and not self.overlay_open:self._thinking(cr,(w,h))
        s=min(w/960,h/540)
        if self.hover in range(len(self.rectangles)):
            a,b,c,d=self.rectangles[self.hover];x,y,bw,bh=a*w,b*h,(c-a)*w,(d-b)*h
            rounded(cr,x+s,y+s,bw-2*s,bh-2*s,9*s);cr.set_source_rgba(*CYAN,.11);cr.fill_preserve()
            cr.set_source_rgb(*CYAN);cr.set_line_width(2.4*s);cr.stroke()
            cr.set_source_rgb(.55,.89,1.);cr.rectangle(x+8*s,y+bh-5*s,(bw-16*s)*self.progress,3*s);cr.fill()
        self.paint_cursor(cr,w,h,s);return False
