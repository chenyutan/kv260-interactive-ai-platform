// Included inside charactermesh. Original courier design; no imported game assets.
// Built once, then evaluated by the existing deduplicated character cache.
struct ClothRing { float y,rx,rz,z; };
inline void cloth_loft(Mesh& m,std::initializer_list<ClothRing> profile,Color color,int sides=12,V3 origin={0,0,0},bool caps=true){
 std::vector<ClothRing> p(profile);
 auto pos=[&](size_t j,int i){float a=2*PI*i/sides;return origin+V3{p[j].rx*std::cos(a),p[j].y,p[j].z+p[j].rz*std::sin(a)};};
 auto norm=[&](size_t j,int i){size_t lo=j?j-1:j,hi=std::min(j+1,p.size()-1);float a=2*PI*i/sides;
  return unit(cross(V3{(p[hi].rx-p[lo].rx)*std::cos(a),p[hi].y-p[lo].y,p[hi].z-p[lo].z+(p[hi].rz-p[lo].rz)*std::sin(a)},V3{-p[j].rx*std::sin(a),0,p[j].rz*std::cos(a)}));};
 auto emit=[&](size_t j,int i){float h=float(j)/(p.size()-1),ao=.94f+.06f*std::sin(h*PI*.8f);return vertex(m,pos(j,i),norm(j,i),{color.r*ao,color.g*ao,color.b*ao});};
 for(size_t j=0;j+1<p.size();++j)for(int i=0;i<sides;++i){uint16_t a=emit(j,i),b=emit(j+1,i),c=emit(j+1,i+1),d=emit(j,i+1);m.indices.insert(m.indices.end(),{a,b,c,a,c,d});}
 if(caps)for(int i=0;i<sides;++i){triangle(m,origin+V3{0,p.front().y,p.front().z},pos(0,i+1),pos(0,i),{0,-1,0},color);size_t j=p.size()-1;triangle(m,origin+V3{0,p.back().y,p.back().z},pos(j,i),pos(j,i+1),{0,1,0},color);}
}
inline void costume_band(Mesh&m,float y,float rx,float rz,float z,float height,Color c,int sides=12){cloth_loft(m,{{y,rx,rz,z},{y+height,rx,rz,z}},c,sides,{0,0,0},false);}
inline float courier_face_z(float x,float y){
 const ClothRing p[]={{-.035f,.060f,.070f,.035f},{.005f,.118f,.112f,.026f},{.075f,.171f,.147f,.018f},{.13f,.194f,.165f,.010f},{.225f,.190f,.165f,.006f},{.305f,.148f,.137f,0},{.365f,.057f,.056f,-.006f}};
 int k=0;while(k<5&&y>p[k+1].y)++k;float t=std::max(0.f,std::min(1.f,(y-p[k].y)/(p[k+1].y-p[k].y)));
 float rx=p[k].rx+(p[k+1].rx-p[k].rx)*t,rz=p[k].rz+(p[k+1].rz-p[k].rz)*t,z=p[k].z+(p[k+1].z-p[k].z)*t;
 return z+rz*std::sqrt(std::max(.001f,1-x*x/(rx*rx)));
}
inline void face_disc(Mesh&m,float x,float y,float rx,float ry,float offset,Color col,int sides=12){
 // Facial surfaces follow the cheek, rather than floating flat face decals.
 auto point=[&](float t,int i){float a=2*PI*i/sides,X=x+rx*t*std::cos(a),Y=y+ry*t*std::sin(a);return V3{X,Y,courier_face_z(X,Y)+offset+.005f*(1-t*t)};};
 for(int i=0;i<sides;++i){V3 a=point(0,0),b=point(1,i),c=point(1,i+1);uint16_t first=static_cast<uint16_t>(m.vertices.size());
  for(V3 p:{a,b,c})vertex(m,p,unit({p.x*.9f,(p.y-.15f)*.6f,1}),col);m.indices.insert(m.indices.end(),{first,static_cast<uint16_t>(first+1),static_cast<uint16_t>(first+2)});}
}
inline void courier_hair(Mesh&m,Color hair,Color sheen){
 const int sides=24,rows=6;
 auto point=[&](int j,int i){float a=2*PI*i/sides,back=std::max(0.f,-std::sin(a)),edge=1.60f-.50f*std::max(0.f,std::sin(a))+.22f*back+.06f*std::cos(a)+.10f*back*std::cos(5*a);float t=.07f+(edge-.07f)*j/rows;return V3{.211f*std::sin(t)*std::cos(a)-.013f*std::pow(std::max(0.f,std::cos(t)),2.f),.145f+.253f*std::cos(t),.010f+.193f*std::sin(t)*std::sin(a)};};
 auto emit=[&](int j,int i){V3 p=point(j,i),n=unit({p.x/.0445f,(p.y-.145f)/.0640f,(p.z-.010f)/.0372f});float h=std::max(0.f,n.y)*.25f;return vertex(m,p,n,{hair.r+(sheen.r-hair.r)*h,hair.g+(sheen.g-hair.g)*h,hair.b+(sheen.b-hair.b)*h});};
 for(int j=0;j<rows;++j)for(int i=0;i<sides;++i){uint16_t a=emit(j,i),b=emit(j+1,i),c=emit(j+1,i+1),d=emit(j,i+1);m.indices.insert(m.indices.end(),{a,c,b,a,d,c});}
 for(int i=0;i<sides;++i)triangle(m,{0,.398f,.010f},point(0,i),point(0,i+1),{0,1,0},hair);
 // A broad swept lock makes an asymmetric silhouette; no spherical fringe beads.
 for(int j=0;j<9;++j){float t=j/9.f,T=(j+1)/9.f;auto p=[&](float u,float side){float x=-.168f+.301f*u,y=.301f-.075f*u+.021f*std::sin(PI*u)+side*.046f*(1-.84f*u);return V3{x,y,courier_face_z(x,y)+.028f+.009f*std::sin(PI*u)};};
  Color c={hair.r+.027f*(1-t),hair.g+.033f*(1-t),hair.b+.043f*(1-t)};
  quad(m,p(t,-1),p(T,-1),p(T,1),p(t,1),{0,0,1},c);
 }
 for(int s:{-1,1})triangle(m,{s*.177f,.19f,.082f},{s*.194f,.19f,.035f},{s*.174f,.085f,.049f},{float(s),0,.35f},hair);
 // Three broad swept locks catch the key light in the gameplay back view.
 // They follow the skull, with a raised centre and tapered ends; no bead fringe.
 for(float az:{3.72f,4.58f,5.40f})for(int j=0;j<7;++j)for(int k=0;k<3;++k){
  auto p=[&](int row,int col){float u=row/7.f,v=col/3.f*2-1,t=.27f+1.58f*u,a=az+.30f*u+v*.26f*std::sin(PI*std::pow(u,.65f));float lift=.004f+.021f*std::sin(PI*u)*(1-v*v);V3 q={(.211f+lift)*std::sin(t)*std::cos(a)-.013f*std::pow(std::max(0.f,std::cos(t)),2.f),.145f+(.253f+lift)*std::cos(t),.010f+(.193f+lift)*std::sin(t)*std::sin(a)};return q;};
  auto emit=[&](int row,int col){V3 q=p(row,col),n=unit({q.x/.0445f,(q.y-.145f)/.0640f,(q.z-.010f)/.0372f});float u=row/7.f,v=col/3.f*2-1,h=.18f+.32f*std::sin(PI*u)*(1-std::abs(v));return vertex(m,q,n,{hair.r+(sheen.r-hair.r)*h,hair.g+(sheen.g-hair.g)*h,hair.b+(sheen.b-hair.b)*h});};
  uint16_t a=emit(j,k),b=emit(j+1,k),c=emit(j+1,k+1),d=emit(j,k+1);m.indices.insert(m.indices.end(),{a,c,b,a,d,c});
 }
}
inline Rig make_courier(){
 Rig r=make_runner();for(auto&n:r.nodes){n.mesh.vertices.clear();n.mesh.indices.clear();}
 const Color jade{.07f,.65f,.58f},jadeLight{.23f,.79f,.68f},red{.98f,.29f,.19f},ink{.085f,.16f,.25f},cream{1.f,.94f,.79f},gold{.97f,.64f,.20f},skin{1.f,.73f,.54f},skinShade{.84f,.46f,.33f},hair{.055f,.073f,.105f},hairSheen{.23f,.28f,.35f},sole{1.f,.96f,.84f};
 // Graph and all leg/foot pivots remain unchanged. Narrower shoulders expose
 // the longer chest rather than reproducing the old round toy shoulder pads.
 r.nodes[2].pivot={-.035f,.245f,-.145f};r.nodes[3].pivot={0,.505f,.005f};
 r.nodes[5].pivot={-.230f,.365f,0};r.nodes[10].pivot={.230f,.365f,0};
 cloth_loft(r.nodes[0].mesh,{{-.10f,.171f,.112f,0},{-.03f,.187f,.124f,0},{.08f,.179f,.120f,0}},ink,12);
 auto& torso=r.nodes[1].mesh;
 cloth_loft(torso,{{-.095f,.215f,.142f,.003f},{-.035f,.198f,.135f,0},{.060f,.169f,.123f,0},{.17f,.185f,.136f,0},{.30f,.214f,.149f,0},{.385f,.207f,.135f,0},{.448f,.116f,.089f,0}},jade,16);
 costume_band(torso,.025f,.179f,.132f,0,.048f,ink,16);
 // Two fitted coat tails leave a visible rear split and do not form a box skirt.
 for(int side:{-1,1}){V3 a={side*.025f,.025f,-.142f},b={side*.178f,.025f,-.102f},c={side*.205f,-.145f,-.133f},d={side*.055f,-.174f,-.160f};
  quad(torso,a,b,c,d,{0,0,-1},jade);quad(torso,{a.x,a.y,a.z+.004f},{d.x,d.y,d.z+.004f},{c.x,c.y,c.z+.004f},{b.x,b.y,b.z+.004f},{0,0,1},ink);
  quad(torso,d,c,{c.x,c.y+.013f,c.z-.002f},{d.x,d.y+.013f,d.z-.002f},{0,0,-1},cream);}
 const ClothRing chest[]={{.060f,.169f,.123f,0},{.17f,.185f,.136f,0},{.30f,.214f,.149f,0},{.385f,.207f,.135f,0},{.448f,.116f,.089f,0}};
 auto front=[&](float x,float y,float lift=.008f){int k=0;while(k<3&&y>chest[k+1].y)++k;float t=(y-chest[k].y)/(chest[k+1].y-chest[k].y),rx=chest[k].rx+(chest[k+1].rx-chest[k].rx)*t,rz=chest[k].rz+(chest[k+1].rz-chest[k].rz)*t;return V3{x,y,rz*std::sqrt(std::max(.001f,1-x*x/(rx*rx)))+lift};};
 // Broad cloth undercollar and a separate overlapping jade outer lapel.
 for(int side:{-1,1})for(int j=0;j<8;++j){float t=j/8.f,T=(j+1)/8.f,y=.437f-t*(side<0?.26f:.15f),Y=.437f-T*(side<0?.26f:.15f),x=side*(.095f-.18f*t),X=side*(.095f-.18f*T),w=side<0?.035f:.024f;quad(torso,front(x-w,y),front(X-w,Y),front(X+w,Y),front(x+w,y),{0,0,1},side<0?cream:jadeLight);}
 for(float y:{.19f,.25f,.31f})ellipsoid(torso,front(.079f,y,.012f),{.018f,.010f,.009f},gold,8,3);
 ellipsoid(torso,{.10f,.047f,.112f},{.037f,.029f,.011f},gold,10,4);
 for(int j=0;j<8;++j){float t=j/8.f,T=(j+1)/8.f;auto p=[&](float u,float edge){return V3{-.13f+.24f*u+edge*.017f,.41f-.34f*u,-.145f-.009f*std::sin(u*PI)};};quad(torso,p(t,-1),p(t,1),p(T,1),p(T,-1),{0,0,-1},ink);}
 costume_band(torso,.425f,.105f,.086f,0,.038f,red,14);
 auto& lamp=r.nodes[2].mesh;
 cloth_loft(lamp,{{-.135f,.059f,.050f,-.074f},{-.10f,.092f,.070f,-.074f},{-.03f,.119f,.087f,-.074f},{.055f,.120f,.088f,-.074f},{.12f,.092f,.070f,-.074f},{.15f,.055f,.044f,-.074f}},cream,12);
 costume_band(lamp,-.117f,.087f,.068f,-.074f,.020f,gold,12);costume_band(lamp,.12f,.094f,.071f,-.074f,.020f,gold,12);
 const ClothRing lp[]={{-.10f,.094f,.072f,-.074f},{-.03f,.121f,.089f,-.074f},{.055f,.122f,.090f,-.074f},{.12f,.094f,.072f,-.074f}};
 for(int i=0;i<8;++i){float a=i*2*PI/8;for(int j=0;j<3;++j){auto p=[&](int k,float da){return V3{lp[k].rx*std::cos(a+da),lp[k].y,lp[k].z+lp[k].rz*std::sin(a+da)};};quad(lamp,p(j,-.023f),p(j+1,-.023f),p(j+1,.023f),p(j,.023f),{std::cos(a),0,std::sin(a)},gold);}}
 ellipsoid(lamp,{0,-.159f,-.074f},{.022f,.031f,.022f},red,8,4);
 auto& head=r.nodes[3].mesh;
 cloth_loft(head,{{-.065f,.061f,.060f,.008f},{.005f,.064f,.062f,.008f}},skin,10);
 cloth_loft(head,{{-.035f,.060f,.070f,.035f},{.005f,.118f,.112f,.026f},{.075f,.171f,.147f,.018f},{.13f,.194f,.165f,.010f},{.225f,.190f,.165f,.006f},{.305f,.148f,.137f,0},{.365f,.057f,.056f,-.006f}},skin,20);
 // Low-frequency cheek warmth baked into vertex colour.
 for(auto&v:head.vertices){float blush=std::max(0.f,1-std::abs(v.y-.105f)/.085f)*std::max(0.f,1-std::abs(std::abs(v.x)-.14f)/.085f)*std::max(0.f,v.nz);v.g*=1-.08f*blush;v.b*=1-.09f*blush;}
 for(int s:{-1,1}){ellipsoid(head,{s*.187f,.145f,.006f},{.031f,.049f,.029f},skin,8,5);ellipsoid(head,{s*.204f,.145f,.023f},{.013f,.027f,.010f},skinShade,8,4);}
 courier_hair(head,hair,hairSheen);
 // Actual eye whites, irises, pupils, glints, brows and a mild asymmetric smile.
 for(int s:{-1,1}){float x=s*.077f,y=.181f+(s>0?.004f:0);
  face_disc(head,x,y,.049f,.025f,.004f,cream,16);face_disc(head,x+.004f,y,.021f,.023f,.012f,{.27f,.15f,.085f},12);face_disc(head,x+.005f,y,.0125f,.018f,.019f,{.025f,.020f,.025f},12);face_disc(head,x-.001f,y+.009f,.0055f,.006f,.026f,{1,.99f,.94f},8);
  for(int j=0;j<5;++j){float t=j/5.f,T=(j+1)/5.f;auto p=[&](float u,float edge){float X=s*(.035f+.086f*u),Y=.224f+.010f*std::sin(u*PI)+(s<0?.004f:0)+edge*.007f;return V3{X,Y,courier_face_z(X,Y)+.012f};};quad(head,p(t,-1),p(T,-1),p(T,1),p(t,1),{0,0,1},hair);}
 }
 ellipsoid(head,{0,.115f,.190f},{.027f,.036f,.028f},skin,10,5);
 for(int j=0;j<8;++j){float t=j/8.f,T=(j+1)/8.f;auto p=[&](float u,float edge,float lift){float x=-.060f+.130f*u,y=.047f+.022f*(2*u-1)*(2*u-1)+.009f*u+edge*.009f;return V3{x,y,courier_face_z(x,y)+lift};};quad(head,p(t,-1,.007f),p(T,-1,.007f),p(T,1,.007f),p(t,1,.007f),{0,0,1},{.27f,.09f,.07f});if(j>0&&j<7)quad(head,p(t,0,.010f),p(T,0,.010f),p(T,.64f,.010f),p(t,.64f,.010f),{0,0,1},cream);}
 auto& scarf=r.nodes[4].mesh;
 for(int s:{-1,1})for(int j=0;j<4;++j){float t=j/4.f,T=(j+1)/4.f;auto p=[&](float u,float edge){float width=.028f*(1-u*.65f);return V3{s*(.055f+.065f*u)+edge*width,.040f-.075f*u+.014f*std::sin(u*PI),-.03f-.24f*u};};quad(scarf,p(t,-1),p(t,1),p(T,1),p(T,-1),{0,1,-.2f},red);quad(scarf,p(t,1),p(t,-1),p(T,-1),p(T,1),{0,-1,.2f},red);}
 for(int s:{-1,1}){int arm=s<0?5:10,fore=arm+1,thigh=arm+2,shin=arm+3,shoe=arm+4;
  cloth_loft(r.nodes[arm].mesh,{{-.25f,.071f,.070f,0},{-.18f,.081f,.076f,0},{-.065f,.080f,.075f,0},{.018f,.059f,.065f,0},{.058f,.024f,.030f,0}},jade,12);
  // Sleeve crown bends into the clavicle, hiding a horizontal end cap and
  // avoiding the old separate circular shoulder/armour-pad appearance.
  for(auto&v:r.nodes[arm].mesh.vertices){float t=std::max(0.f,std::min(1.f,(v.y+.065f)/.123f));v.x-=s*.09f*t;
   if(v.y>=-.065f&&v.y<=.058f){V3 n=unit({v.nx,v.ny+s*(.09f/.123f)*v.nx,v.nz});v.nx=n.x;v.ny=n.y;v.nz=n.z;}}
  cloth_loft(r.nodes[fore].mesh,{{-.23f,.048f,.052f,0},{-.16f,.058f,.060f,0},{-.055f,.064f,.065f,0},{.018f,.068f,.067f,0}},skin,10);
  costume_band(r.nodes[fore].mesh,-.062f,.070f,.070f,0,.069f,cream,12);cloth_loft(r.nodes[fore].mesh,{{-.215f,.054f,.058f,0},{-.185f,.061f,.063f,0}},red,12,{0,0,0},false);
  ellipsoid(r.nodes[fore].mesh,{0,-.252f,.007f},{.058f,.069f,.052f},skin,10,5);ellipsoid(r.nodes[fore].mesh,{-s*.042f,-.239f,.035f},{.025f,.039f,.025f},skin,8,4);
  cloth_loft(r.nodes[thigh].mesh,{{-.36f,.070f,.073f,0},{-.27f,.078f,.083f,0},{-.09f,.095f,.099f,0},{.012f,.089f,.093f,0}},ink,12);
  cloth_loft(r.nodes[shin].mesh,{{-.30f,.054f,.058f,0},{-.24f,.059f,.064f,0},{-.09f,.072f,.076f,0},{.012f,.070f,.073f,0}},ink,12);cloth_loft(r.nodes[shin].mesh,{{-.285f,.061f,.066f,0},{-.221f,.067f,.071f,0}},cream,12,{0,0,0},false);
  // Same -0.09 contact sole and leg lengths; the existing support solver remains authoritative.
  cloth_loft(r.nodes[shoe].mesh,{{-.09f,.070f,.138f,.052f},{-.067f,.095f,.160f,.062f},{-.039f,.094f,.157f,.062f}},sole,14);
  cloth_loft(r.nodes[shoe].mesh,{{-.038f,.089f,.150f,.062f},{.006f,.086f,.139f,.056f},{.052f,.064f,.091f,.022f},{.079f,.049f,.054f,0}},jade,14);costume_band(r.nodes[shoe].mesh,-.039f,.095f,.157f,.062f,.015f,red,14);
  for(int j=0;j<5;++j){float a=-.82f+j*.328f,b=a+.328f;auto p=[&](float t,float z){return V3{.076f*std::sin(t),.066f-.020f*std::abs(std::sin(t)),z};};quad(r.nodes[shoe].mesh,p(a,.065f),p(b,.065f),p(b,.094f),p(a,.094f),{0,1,.2f},cream);}
 }
 // Large colour fields are authored for the normal rear camera, not only a
 // close-up. The warm yoke separates the head from the jacket and lantern.
 auto back=[&](float x,float y,float lift=.009f){
  const ClothRing p[]={{.17f,.185f,.136f,0},{.30f,.214f,.149f,0},{.385f,.207f,.135f,0},{.448f,.116f,.089f,0}};
  int k=0;while(k<2&&y>p[k+1].y)++k;float t=clamp01((y-p[k].y)/(p[k+1].y-p[k].y)),rx=p[k].rx+(p[k+1].rx-p[k].rx)*t,rz=p[k].rz+(p[k+1].rz-p[k].rz)*t;
  return V3{x,y,-rz*std::sqrt(std::max(.001f,1-x*x/(rx*rx)))-lift};
 };
 for(int s:{-1,1})for(int j=0;j<6;++j){float t=j/6.f,T=(j+1)/6.f;
  auto p=[&](float u,bool top){float x=s*(.012f+.177f*u),y=top?.423f-.055f*u:.357f-.050f*u;return back(x,y);};
  quad(torso,p(t,false),p(T,false),p(T,true),p(t,true),{0,.15f,-1},cream);
 }
 // A broad coral waist sash remains legible between the lantern and trouser.
 costume_band(torso,.025f,.181f,.134f,0,.049f,red,16);
 // Lantern glass is honey-coloured rather than an unshaded white egg. Keep
 // its existing rig node and animate with the same shoulder attachment.
 for(auto&v:lamp.vertices){v.x*=1.12f;v.z=-.074f+(v.z+.074f)*.88f;V3 n=unit({v.nx/1.12f,v.ny,v.nz/.88f});v.nx=n.x;v.ny=n.y;v.nz=n.z;
  if(v.r>.94f&&v.g>.80f){v.r=1.f;v.g=.77f;v.b=.30f;}
  else if(v.g>.40f&&v.b<.35f){v.r=.57f;v.g=.30f;v.b=.095f;}
 }
 // Slightly larger, expressive head/hand/foot proportions. Feet retain the
 // exact -0.09 contact height and IK pivots; only horizontal shoe shape grows.
 for(auto&v:head.vertices){v.x*=1.105f;v.y*=1.105f;v.z*=1.105f;}
 // Broad baked colour gradients describe cloth volume at normal gameplay size.
 // These are material colours before the runtime key light, with no extra pass.
 for(auto&v:torso.vertices)if(v.g>v.r*1.5f){
  float shoulder=clamp01((v.y-.12f)/.28f),side=clamp01((-v.x+.22f)/.44f);
  float shade=.82f+.12f*shoulder+.09f*side;
  v.r*=shade;v.g*=shade;v.b*=shade;
 }
 for(int arm:{5,10}){
  for(auto&v:r.nodes[arm+1].mesh.vertices)if(v.y<-.215f){v.x*=1.13f;v.z*=1.13f;V3 n=unit({v.nx/1.13f,v.ny,v.nz/1.13f});v.nx=n.x;v.ny=n.y;v.nz=n.z;}
  for(auto&v:r.nodes[arm+4].mesh.vertices){v.x*=1.25f;v.z*=1.14f;V3 n=unit({v.nx/1.25f,v.ny,v.nz/1.14f});v.nx=n.x;v.ny=n.y;v.nz=n.z;}
  // The dark outsole is a real underside, visible during foot recovery.
  auto&shoe=r.nodes[arm+4].mesh;
  cloth_loft(shoe,{{-.09f,.086f,.154f,.059f},{-.080f,.115f,.178f,.071f}},ink,14,{0,0,0},true);
  // A flexible-looking knee overlap closes the rigid-segment silhouette.
  ellipsoid(r.nodes[arm+3].mesh,{0,-.008f,0},{.072f,.067f,.076f},ink,12,6);
  // Heel counter, toe cap and two broad lace bars make a trainer readable
  // during recovery. All are original geometry, not branded shoe details.
  cloth_loft(shoe,{{.003f,.083f,.054f,-.050f},{.050f,.069f,.044f,-.038f}},red,12,{0,0,0},false);
  for(float z:{.067f,.102f}){
   quad(shoe,{-.053f,.066f,z},{.053f,.066f,z},{.053f,.066f,z+.013f},{-.053f,.066f,z+.013f},{0,1,0},cream);
  }
 }
 return r;
}

