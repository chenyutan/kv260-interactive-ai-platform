"""Original sparse brush artwork, rasterized by Renderer caches only.

All coordinates are logical pixels. No image files, random state, or frame work.
"""
import math
import cairo


def paint_landscape(c):
    c.set_source_rgb(.957,.949,.916);c.paint()
    # Subtle warm paper fibres are deterministic and baked once on resize.
    c.set_line_width(.6)
    for i in range(430):
        x=(i*97.13)%1280;y=(i*53.71)%720
        c.set_source_rgba(.40,.36,.27,.018+(i%3)*.004)
        c.move_to(x,y);c.line_to(x+2+(i%6),y+.35);c.stroke()
    # Three overlapping washes along the margins; the middle remains open.
    def mountain(points,bottom,color):
        # Uneven, broken contour gives each ridge an ink-brush edge. The wash
        # tapers to paper at both ends, so no clipped vertical rectangle remains.
        points=[(points[0][0]-72,bottom)]+points+[(points[-1][0]+72,bottom)]
        ridge=[]
        for j,((x0,y0),(x1,y1)) in enumerate(zip(points,points[1:])):
            xp,yp=points[max(0,j-1)];xn,yn=points[min(len(points)-1,j+2)]
            ax,ay=x0+(x1-xp)*.14,y0+(y1-yp)*.14
            bx,by=x1-(xn-x0)*.14,y1-(yn-y0)*.14
            steps=max(5,int(math.hypot(x1-x0,y1-y0)/3))
            for k in range(steps):
                u=k/steps;v=1-u
                rough=math.sin(u*math.pi)*(.8*math.sin(k*1.77+j*2.4)+.5*math.sin(k*3.1))
                ridge.append((v**3*x0+3*v*v*u*ax+3*v*u*u*bx+u**3*x1,
                              v**3*y0+3*v*v*u*ay+3*v*u*u*by+u**3*y1+rough))
        ridge.append(points[-1])
        g=cairo.LinearGradient(0,min(y for x,y in points),0,bottom)
        g.add_color_stop_rgba(0,*color,.14)
        g.add_color_stop_rgba(.50,*color,.065)
        g.add_color_stop_rgba(1,*color,0)
        c.set_source(g);c.move_to(*ridge[0])
        for x,y in ridge[1:]:c.line_to(x,y)
        c.close_path();c.fill()
        # Sparse dry bristle breaks only along the ridge, never across the field.
        c.set_line_width(.7);c.set_source_rgba(*color,.055)
        for j in range(2,len(ridge)-4,5):
            x,y=ridge[j];xx,yy=ridge[j+2]
            c.move_to(x,y+1);c.line_to(xx,yy+2);c.stroke()
        c.set_line_width(.55);c.set_source_rgba(*color,.027)
        for j in range(10,len(ridge)-10,7):
            x,y=ridge[j]
            c.move_to(x,y+3);c.curve_to(x-3,y+15,x+6,y+28,x+4,y+43);c.stroke()
    mountain([(-30,256),(10,227),(40,192),(58,202),(86,155),(104,163),(130,211),(151,238),(176,214),(198,239),(228,272),(266,293),(320,320)],445,(.34,.42,.41))
    mountain([(890,283),(944,251),(973,204),(997,220),(1022,174),(1047,181),(1078,137),(1100,144),(1128,207),(1160,225),(1203,182),(1230,203),(1285,155)],404,(.32,.40,.39))
    mountain([(-25,330),(18,292),(47,302),(77,248),(98,269),(121,337),(147,355),(181,333),(226,372)],510,(.29,.38,.36))
    # Low distant foothills close the scroll without sitting behind the player.
    mountain([(-20,647),(40,624),(88,637),(127,605),(180,633),(214,622),(257,653),(332,661),(440,679),(570,688),(744,679),(856,659),(923,640),(975,656),(1038,612),(1076,628),(1139,588),(1183,607),(1239,581),(1300,611)],734,(.35,.43,.40))
    # A few broken mountain creases, softened to paper before reaching the field.
    c.set_line_width(.9);c.set_source_rgba(.30,.39,.36,.07)
    for x,y,dx in ((86,158,-24),(104,174,11),(1078,144,-25),(1100,164,22),(1203,190,-19),(77,258,-12)):
        c.move_to(x,y);c.line_to(x+dx,y+46);c.move_to(x+dx+3,y+57);c.line_to(x+dx*1.6,y+84);c.stroke()


def paint_sword(c,w,h,alpha=1.):
    # Sword points right before the renderer's cached rotation. Broad silhouette,
    # pale steel edge, dark spine, a single cinnabar tassel; no bevel or glow.
    c.save();c.translate(0,-h*.21);c.set_source_rgba(.13,.22,.23,alpha)
    c.move_to(w*.18,h*.48);c.line_to(w*.84,h*.34);c.line_to(w,h*.48)
    c.line_to(w*.84,h*.62);c.close_path();c.fill()
    c.set_source_rgba(.95,.95,.89,alpha)
    c.move_to(w*.27,h*.47);c.line_to(w*.84,h*.40);c.line_to(w*.95,h*.48);c.close_path();c.fill()
    c.set_source_rgba(.56,.49,.32,alpha);c.set_line_width(max(1.2,h*.09))
    c.move_to(w*.22,h*.21);c.line_to(w*.22,h*.77);c.stroke()
    c.set_source_rgba(.19,.27,.25,alpha);c.set_line_width(max(2,h*.12))
    c.move_to(w*.07,h*.49);c.line_to(w*.22,h*.49);c.stroke()
    c.set_source_rgba(.66,.22,.17,alpha*.90);c.set_line_width(max(1,h*.055))
    c.move_to(w*.08,h*.51);c.curve_to(w*.04,h*.65,w*.11,h*.80,w*.01,h*.92);c.stroke()
    c.restore()


def paint_seal(c,r,boss=False):
    # Weathered woodblock, angular outer silhouette instead of a luminous orb.
    c.set_source_rgb(.17,.23,.23)
    c.move_to(-r*.96,-r*.88);c.line_to(r*.89,-r*.98);c.line_to(r*.99,r*.87)
    c.line_to(-r*.89,r*.99);c.close_path();c.fill()
    c.set_source_rgb(.59,.22,.18) if boss else c.set_source_rgb(.24,.25,.24)
    c.rectangle(-r*.76,-r*.76,r*1.52,r*1.52);c.fill()
    c.set_source_rgba(.82,.48,.34,.85);c.set_line_width(1.3)
    c.move_to(-r*.64,-r*.61);c.line_to(r*.53,-r*.61)
    c.move_to(r*.64,-r*.54);c.line_to(r*.64,r*.61)
    c.move_to(r*.55,r*.64);c.line_to(-r*.64,r*.64)
    c.move_to(-r*.64,r*.52);c.line_to(-r*.64,-r*.49);c.stroke()
    # Abstract split-ridge carving: recognition comes from silhouette and
    # pigment, not arbitrary decorative characters.
    c.set_source_rgba(.94,.91,.80,.9);c.set_line_width(max(2,r*.08))
    c.move_to(-r*.46,r*.28);c.line_to(-r*.20,-r*.28);c.line_to(-r*.03,r*.05);c.stroke()
    c.move_to(r*.08,-r*.13);c.line_to(r*.23,-r*.39);c.line_to(r*.46,r*.28);c.stroke()
    c.set_line_width(max(1,r*.045));c.move_to(-r*.37,r*.41);c.line_to(r*.30,r*.36);c.stroke()
    c.set_source_rgba(.94,.92,.84,.32)
    for x,y in ((-.72,-.31),(.64,.41),(.25,-.73),(-.48,.69)):
        c.rectangle(x*r,y*r,r*.09,r*.025);c.fill()
