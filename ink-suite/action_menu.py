"""Native in-experience actions using the existing menu brush/render path.

The host owns pause, action policy and confirmation. This view never sends a
game command or advances dwell. Choice order uses the first N existing ZONES;
help has one Back choice at slot 0 and five non-interactive information cards.
"""
import math

from menu_view import MenuView, RECTS, MODES, cairo, text, Pango, PangoCairo


def _items(items):
    if not isinstance(items, (tuple, list)) or len(items)>len(RECTS):
        raise ValueError('Action menu accepts at most six items')
    result=[];seen=set()
    for item in items:
        if not isinstance(item,dict):raise ValueError('Each action must be a mapping')
        identifier=item.get('id');title=item.get('title')
        if not isinstance(identifier,str) or not identifier.strip() or identifier in seen:
            raise ValueError('Action ids must be nonempty and unique')
        if not isinstance(title,str) or not title.strip():raise ValueError('Action title is required')
        subtitle=item.get('subtitle','')
        if not isinstance(subtitle,str):raise ValueError('Action subtitle must be text')
        result.append(dict(id=identifier,title=title,subtitle=subtitle,danger=bool(item.get('danger',False))))
        seen.add(identifier)
    return tuple(result)


def _block(cr,value,x,y,width,height,size,color=(.28,.35,.32),bold=False):
    """Wrap all text rather than replacing essential help with an ellipsis."""
    layout=PangoCairo.create_layout(cr)
    layout.set_text(value,-1);layout.set_width(max(1,int(width*Pango.SCALE)))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    desc=Pango.FontDescription('Noto Sans CJK SC')
    if bold:desc.set_weight(Pango.Weight.BOLD)
    # Static-cache work only. Short instructions normally use the first size.
    current=size
    while True:
        desc.set_size(max(1,int(current*Pango.SCALE)));layout.set_font_description(desc)
        if layout.get_pixel_size()[1]<=height or current<=size*.62:break
        current*=.92
    cr.set_source_rgb(*color);cr.move_to(x,y);PangoCairo.show_layout(cr,layout)


def _rounded(cr,x,y,w,h,r):
    r=min(r,w/2,h/2)
    cr.new_sub_path()
    for cx,cy,a,b in ((x+w-r,y+r,-math.pi/2,0),(x+w-r,y+h-r,0,math.pi/2),
                      (x+r,y+h-r,math.pi/2,math.pi),(x+r,y+r,math.pi,math.pi*1.5)):
        cr.arc(cx,cy,r,a,b)
    cr.close_path()


class ActionMenu(MenuView):
    radial=False
    @property
    def rectangles(self):return RECTS
    def __init__(self,on_select):
        super().__init__(on_select)
        self.title='体验菜单';self.actions=();self.help_lines=()
        self.message='体验已暂停 · 右手移动，停留 2 秒选择操作'
        self._hand_serial=0

    @property
    def choices(self):
        """Pass these ids to DwellSelector.set_choices; blank slots stay inert."""
        return tuple(item['id'] for item in self.actions)

    def _page_changed(self):
        self.clear_pointer();self.hover=None;self.progress=0.
        self.previous_hover=None;self.previous_progress=0.;self.selection_dirty=True
        self.cache_key=None;self.queue_draw()

    def set_actions(self,title,items,message=None):
        actions=_items(items)
        title=str(title or '体验菜单')
        changed=(title!=self.title or actions!=self.actions or bool(self.help_lines))
        self.title=title;self.actions=actions;self.help_lines=()
        if message is not None and str(message)!=self.message:
            self.message=str(message);changed=True
        if changed:self._page_changed()

    def set_help(self,title,lines,back_id='back'):
        """Up to five full-text rule cards; only top-left Back is selectable."""
        if not isinstance(lines,(tuple,list)) or len(lines)>5 or not all(isinstance(line,str) for line in lines):
            raise ValueError('Help accepts at most five text sections')
        lines=tuple(lines)
        self.set_actions(title,[dict(id=back_id,title='返回',subtitle='读完说明，回到操作菜单')],
                         '右手停留“返回”继续 · 说明卡片不会触发操作')
        self.help_lines=lines;self._page_changed()

    def set_briefing(self,title,lines,message):
        """Two explicit entry choices and four inert instruction cards."""
        if not isinstance(lines,(tuple,list)) or len(lines)!=4 or not all(isinstance(line,str) and line.strip() for line in lines):
            raise ValueError('Entry help requires four complete sections')
        self.set_actions(title,[dict(id='intro_start',title='开始',subtitle='已了解操作，进入游戏'),
                                dict(id='intro_back',title='返回',subtitle='重新选择控制方式')],message)
        self.help_lines=tuple(lines);self._page_changed()

    def action_at(self,index):
        if isinstance(index,int) and not isinstance(index,bool) and 0<=index<len(self.actions):
            return self.actions[index]['id']
        return None

    def action_for_mode(self,mode):
        """Compatibility for a legacy selector returning fixed MODES tokens."""
        if not self.selection_enabled or mode not in MODES:return None
        return self.action_at(MODES.index(mode))

    def hit_test(self,point):
        if not self.selection_enabled:return None
        try:
            x,y=point
            if not all(math.isfinite(float(v)) for v in (x,y)):return None
            x,y=float(x),float(y)
        except (ValueError,TypeError):return None
        for index,(a,b,c,d) in enumerate(RECTS[:len(self.actions)]):
            if a<=x<=c and b<=y<=d:return self.action_at(index)
        return None

    def click(self,_,event):
        if event.button!=1:return False
        item=self.hit_test((event.x/max(1,self.get_allocated_width()),event.y/max(1,self.get_allocated_height())))
        if item is None:return False
        self.choose(item);return True

    def update_hand(self,point,hover=None,progress=0.,sample_key=None):
        """Call for an observed hand sample; use update_selection for cached feeds."""
        if sample_key is None:
            self._hand_serial+=1;sample_key=('action-hand',self._hand_serial)
        self.update_selection(dict(cursor=point,hover=hover,progress=progress,sample_key=sample_key))

    def update_selection(self,state):
        state=dict(state or {})
        if not self.selection_enabled:
            super().update_selection({});return
        hover=state.get('hover')
        if self.action_at(hover) is None:state.update(hover=None,progress=0.)
        try:
            progress=float(state.get('progress',0.))
            state['progress']=max(0.,min(1.,progress)) if math.isfinite(progress) else 0.
        except (ValueError,TypeError):state['progress']=0.
        pointer=state.get('cursor')
        try:
            if len(pointer)!=2 or not all(math.isfinite(float(v)) and 0<=float(v)<=1 for v in pointer):
                state['cursor']=None
        except (TypeError,ValueError):state['cursor']=None
        super().update_selection(state)

    def set_availability(self,ready,message):
        old=self.selection_enabled
        super().set_availability(ready,message)
        if old!=self.selection_enabled:self.cache_key=None

    def unmapped(self,*args):
        super().unmapped(*args)
        self.hover=None;self.progress=0.;self.previous_hover=None
        self.previous_progress=0.;self.selection_dirty=True

    def build_static(self,w,h):
        self.static_surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.static_surface);s=min(w/960,h/540)
        ink=(.10,.23,.20);muted=(.40,.45,.41);red=(.65,.25,.18)
        paper=cairo.LinearGradient(0,0,w,h)
        paper.add_color_stop_rgb(0,.975,.968,.946);paper.add_color_stop_rgb(1,.941,.938,.899)
        cr.set_source(paper);cr.paint()
        # All paper, rule and card work is cached. Pointer motion does not
        # rebuild this surface, and focus/dwell keep their established geometry.
        cr.set_source_rgba(.21,.31,.27,.018);cr.set_line_width(s)
        for yy in range(0,h,max(1,round(13*s))):cr.move_to(0,yy);cr.line_to(w,yy);cr.stroke()
        cr.set_source_rgb(*red);_rounded(cr,w*.04,h*.045,4*s,31*s,2*s);cr.fill()
        text(cr,self.title,w*.06,h*.035,24*s,ink,bold=True,width=w*.78)
        text(cr,'玩法说明' if self.help_lines else '选择操作',w*.06,h*.105,10.5*s,muted)
        text(cr,'墨境',w*.875,h*.049,13*s,ink,bold=True,width=w*.07,centered=True)
        cr.set_source_rgba(*ink,.14);cr.set_line_width(s)
        cr.move_to(w*.04,h*.16);cr.line_to(w*.96,h*.16);cr.stroke()
        for index,rect in enumerate(RECTS):
            item=self.actions[index] if index<len(self.actions) else None
            help_index=index-len(self.actions)
            info=self.help_lines[help_index] if self.help_lines and 0<=help_index<len(self.help_lines) else None
            if item is None and info is None:continue
            a,b,c,d=rect;x,y,cw,ch=a*w,b*h,(c-a)*w,(d-b)*h
            danger=bool(item and item['danger'])
            primary=bool(item and item['id'] in ('intro_start','resume','continue','start'))
            color=red if danger else ink
            cr.set_source_rgba(.12,.24,.20,.055);_rounded(cr,x,y+3*s,cw,ch,12*s);cr.fill()
            _rounded(cr,x,y,cw,ch,12*s)
            if danger:cr.set_source_rgb(.982,.940,.906)
            elif item:cr.set_source_rgb(.995,.989,.968)
            else:cr.set_source_rgba(.99,.983,.960,.60)
            cr.fill_preserve();cr.set_source_rgba(*color,.19 if item else .09)
            cr.set_line_width(s);cr.stroke()
            if item:
                # A recommended action is not the current selection. Keep its
                # large background neutral; the shared dynamic hover owns the
                # only selection border/fill. This small warm rule is cached.
                if primary and self.selection_enabled:
                    cr.set_source_rgb(.65,.51,.29);cr.set_line_width(1.5*s)
                    cr.move_to(x+cw-46*s,y+21*s);cr.line_to(x+cw-20*s,y+21*s);cr.stroke()
                text(cr,f'{index+1:02}',x+20*s,y+14*s,9.5*s,(.55,.51,.37),bold=True)
                if danger:text(cr,'需确认',x+cw-72*s,y+14*s,9*s,red,bold=True)
                title_color=color if self.selection_enabled else (.48,.49,.46)
                _block(cr,item['title'],x+20*s,y+43*s,cw-40*s,ch*.35,20*s,title_color,True)
                _block(cr,item['subtitle'],x+20*s,y+ch*.65,cw-40*s,ch*.28,10*s,muted)
            else:
                text(cr,f'说明 {help_index+1}',x+20*s,y+14*s,9.5*s,(.53,.48,.34),bold=True)
                _block(cr,info,x+20*s,y+43*s,cw-40*s,ch-55*s,12*s,ink)
        cr.set_source_rgba(*ink,.10);cr.move_to(w*.04,h*.913);cr.line_to(w*.96,h*.913);cr.stroke()
        text(cr,self.message,w*.045,h*.94,9.5*s,muted,width=w*.91)
        # MenuView.paint uses this key. Page changes invalidate it explicitly,
        # while hand motion continues to reuse the static opaque background.
        self.cache_key=(w,h,self.message);self.cache_builds+=1
