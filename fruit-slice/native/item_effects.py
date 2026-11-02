"""Bounded native item feedback. Visual-only; never changes game scoring."""
import math

COLORS={'double':(.86,.57,.10),'ice':(.16,.62,.80),'bomb':(.77,.16,.13)}

class ItemEffects:
    def __init__(self):self.items=[]
    def clear(self):self.items.clear()
    def trigger(self,event):
        kind=event['kind']
        if kind not in COLORS:return
        self.items=[v for v in self.items if v['kind']!=kind]
        self.items.append(dict(kind=kind,x=event['x'],y=event['y'],delta=event['delta'],age=0.,duration=1.2))
    def advance(self,dt):
        for v in self.items:v['age']+=max(0,dt)
        self.items=[v for v in self.items if v['age']<v['duration']]
    def bounds(self):
        if not self.items:return []
        return [(v['x']-135,v['y']-135,270,270) for v in self.items]+[(390,82,500,92)]
    def banner_key(self):
        if not self.items:return None
        v=next((v for v in reversed(self.items) if v['kind']=='bomb'),self.items[-1])
        return (id(v),v['kind'],v['delta'])
    def paint(self,c):
        """Caller provides logical coordinates and the game-area clip."""
        if not self.items:return
        c.save()
        for v in self.items:
            t=v['age']/v['duration']; fade=min(1,(1-t)*3)
            rgb=COLORS[v['kind']]; x,y=v['x'],v['y']; radius=38+70*min(1,t*2)
            c.set_source_rgba(*rgb,.85*fade); c.set_line_width(4*(1-t)+1)
            if v['kind']=='bomb':
                # A single ink burst, no repeated full-screen flashes or shake.
                for i in range(12):
                    a=i*math.tau/12; inner=radius*.60; outer=radius*(1 if i%2 else 1.18)
                    c.move_to(x+math.cos(a)*inner,y+math.sin(a)*inner)
                    c.line_to(x+math.cos(a)*outer,y+math.sin(a)*outer)
                c.stroke()
                c.set_source_rgba(.13,.09,.08,.8*fade);c.arc(x,y,18*(1-t)+5,0,math.tau);c.fill()
            else:
                c.arc(x,y,radius,0,math.tau);c.stroke()
                c.set_source_rgba(*rgb,.35*fade);c.set_line_width(2);c.arc(x,y,radius*.78,0,math.tau);c.stroke()
                for i in range(6):
                    a=i*math.tau/6; px=x+math.cos(a)*radius; py=y+math.sin(a)*radius
                    c.move_to(px-5,py);c.line_to(px+5,py);c.move_to(px,py-5);c.line_to(px,py+5)
                c.stroke()
            label='×2' if v['kind']=='double' else '冰' if v['kind']=='ice' else str(v['delta'])
            self.label(c,label,x,y+15,46,rgb,fade)
        # A bomb takes visual priority; other effects remain visible at their hit points.
        v=next((v for v in reversed(self.items) if v['kind']=='bomb'),self.items[-1])
        alpha=1; rgb=COLORS[v['kind']]
        c.set_source_rgba(*rgb,.94*alpha);c.rectangle(400,88,480,78);c.fill()
        c.set_source_rgba(1,.96,.82,.8*alpha);c.set_line_width(2);c.rectangle(405,93,470,68);c.stroke()
        label={'double':'金梨 ×2  ·  得分翻倍','ice':'冰冻  ·  水果减速','bomb':f'炸弹  {v["delta"]} 分'}[v['kind']]
        self.label(c,label,640,139,32,(1,.98,.9),alpha)
        c.restore()
    @staticmethod
    def label(c,text,x,y,size,rgb,alpha):
        c.select_font_face('Noto Sans CJK SC',0,1);c.set_font_size(size)
        ext=c.text_extents(text);c.set_source_rgba(*rgb,alpha);c.move_to(x-ext[2]/2-ext[0],y);c.show_text(text)
