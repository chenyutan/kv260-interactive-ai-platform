"""Bounded, local watercolor hit effects; visual only, never affects collision."""
import math

COLORS={'peach':(.91,.36,.40),'mandarin':(.98,.52,.10),'watermelon':(.88,.23,.29),
        'apple':(.80,.18,.18),'strawberry':(.89,.20,.31),'dragonfruit':(.83,.17,.52)}

class CutEffects:
    def __init__(self):self.items=[]
    def clear(self):self.items.clear()
    def trigger(self,event):
        if event['kind']!='normal':return
        self.items.append(dict(x=event['x'],y=event['y'],angle=event.get('cut_angle',0),
                               color=COLORS[event['fruit']],age=0,combo=event['combo']))
        self.items=self.items[-5:]
    def advance(self,dt):
        for e in self.items:e['age']+=max(0,dt)
        self.items=[e for e in self.items if e['age']<.58]
    def bounds(self):return [(e['x']-145,e['y']-145,290,290) for e in self.items]
    def paint(self,c):
        for e in self.items:
            t=e['age'];fade=max(0,1-t/.58);color=e['color']
            c.save();c.translate(e['x'],e['y']);c.rotate(e['angle'])
            # Pigment flecks move predominantly away from the actual cut plane.
            for i in range(10):
                a=i*2.399963; travel=(26+(i%4)*12)*(1-math.exp(-7*t))
                x=math.cos(a)*travel; y=math.sin(a)*travel*1.5+48*t*t
                c.save();c.translate(x,y);c.rotate(a);c.scale(1.8,1)
                c.arc(0,0,(3+i%3)*fade+.5,0,math.tau)
                c.set_source_rgba(*(v*.55 for v in color),.95*fade);c.fill_preserve()
                c.set_source_rgba(.16,.10,.12,.8*fade);c.set_line_width(.7);c.stroke();c.restore()
            if t<.23:
                a=(1-t/.23);length=68+45*(t/.23)
                # Two tapered brush blades with a warm, bright central cut.
                for width,rgb,opacity in ((16,(.10,.15,.16),1),(10,tuple(v*.7 for v in color),1),(2.5,(1,.91,.55),1)):
                    c.set_source_rgba(*rgb,a*opacity)
                    c.move_to(-length,0);c.curve_to(-30,-width,34,-width,length,0)
                    c.curve_to(35,width*.4,-25,width*.4,-length,0);c.fill()
            if e['combo']>=3 and t<.32:
                c.set_source_rgba(.48,.24,.06,(1-t/.32));c.set_line_width(3)
                for sign in (-1,1):
                    c.move_to(sign*(30+90*t),-18);c.line_to(sign*(47+90*t),-24);c.stroke()
            c.restore()
