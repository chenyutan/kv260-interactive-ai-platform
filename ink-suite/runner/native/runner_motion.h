#ifndef RUNNER_MOTION_H
#define RUNNER_MOTION_H
// Presentation-only motion. No game clocks, input, collisions or world height are changed.
#include <cmath>
#include <algorithm>

namespace runner_motion {
constexpr float PI=3.14159265358979323846f;
inline float clamp(float x,float a,float b){return std::max(a,std::min(b,x));}
inline float ease(float x){x=clamp(x,0,1);return x*x*(3-2*x);}
inline float mix(float a,float b,float t){return a+(b-a)*t;}
inline float wrap(float p){return p-std::floor(p);}
struct Joint {float x=0,y=0,z=0;};
struct Pose {
 Joint joint[15];
 float pelvis_y=.75f,sway=0,phase=0,cadence=1.65f,jump_phase=-1;
 float height_cap=0,foot_lift=0; // Small gait-flight lift, never game jump/root motion.
 int mode=0; // 0 run/idle, 1 jump, 2 physical fall, 3 landing, 4 slide/recovery
};
struct Input {
 float distance=0,clock=0,player_x=1,player_y=0,jump_height=0,slide_phase=-1;
 bool supported=true;
 int state=1; // Existing ABI enum, running=1.
};
inline Pose blend(const Pose&a,const Pose&b,float t){
 Pose p=b;for(int i=0;i<15;++i){p.joint[i]={mix(a.joint[i].x,b.joint[i].x,t),mix(a.joint[i].y,b.joint[i].y,t),mix(a.joint[i].z,b.joint[i].z,t)};}
 p.pelvis_y=mix(a.pelvis_y,b.pelvis_y,t);p.sway=mix(a.sway,b.sway,t);p.foot_lift=mix(a.foot_lift,b.foot_lift,t);return p;
}
struct Foot {float z,y,pitch;};
inline Foot foot_path(float phase){
 // Touchdown -> load -> stance -> toe-off -> heel recovery -> knee drive -> contact.
 static const float t[]={0,.12f,.31f,.43f,.62f,.83f,1};
 static const Foot p[]={{.24f,.015f,-.15f},{.105f,0,0},{-.22f,.012f,.15f},
                       {-.29f,.12f,.48f},{-.13f,.29f,.12f},{.21f,.18f,-.18f},{.24f,.015f,-.15f}};
 phase=wrap(phase);int k=0;while(k<5&&phase>t[k+1])++k;
 float span=t[k+1]-t[k],u=(phase-t[k])/span;
 int prev=k? k-1:5,next=k<5?k+2:1;
 float tp=k?t[prev]:t[prev]-1,tn=k<5?t[next]:t[next]+1;
 auto hermite=[&](float before,float a,float b,float after){
  float ma=(b-before)/(t[k+1]-tp),mb=(after-a)/(tn-t[k]);
  return (2*u*u*u-3*u*u+1)*a+(u*u*u-2*u*u+u)*span*ma
       +(-2*u*u*u+3*u*u)*b+(u*u*u-u*u)*span*mb;
 };
 return {hermite(p[prev].z,p[k].z,p[k+1].z,p[next].z),
         std::max(0.f,hermite(p[prev].y,p[k].y,p[k+1].y,p[next].y)),
         hermite(p[prev].pitch,p[k].pitch,p[k+1].pitch,p[next].pitch)};
}
inline void leg_ik(Pose&p,int thigh,int shin,int ankle,Foot foot,float hip_y){
 constexpr float a=.36f,b=.30f;
 float dy=.095f+foot.y-hip_y,dz=foot.z;
 float rr=clamp(dy*dy+dz*dz,.01f,(a+b-.002f)*(a+b-.002f));
 float knee=std::acos(clamp((rr-a*a-b*b)/(2*a*b),-1,1));
 float hip=std::atan2(-dz,-dy)-std::atan2(b*std::sin(knee),a+b*std::cos(knee));
 p.joint[thigh].x=hip;p.joint[shin].x=knee;p.joint[ankle].x=foot.pitch-hip-knee;
}
inline Pose running_pose(float phase,float cadence,float lean){
 Pose p;p.phase=phase;p.cadence=cadence;
 float a=phase*2*PI,s=std::sin(a),contact=std::cos(2*a);
 p.pelvis_y=.754f-.016f*contact;p.sway=.013f*s;
 p.joint[0]={0,.055f*s,.025f*s-lean*.12f};
 // The pelvis/feet keep their validated path. The chest counter-rotates
 // slightly more so the two shoulder silhouettes read independently.
 p.joint[1]={.17f+.018f*contact,-.13f*s,-lean*.65f};
 // Keep the gaze near the horizon; lane lean gives only a small look lead.
 p.joint[3]={-p.joint[1].x+.012f,-p.joint[0].y-p.joint[1].y+lean*.18f,
             -.65f*(p.joint[0].z+p.joint[1].z)};
 p.joint[4]={.12f+.055f*std::sin(a-.6f),0,.075f*std::sin(a-1.1f)};
 Foot left=foot_path(phase),right=foot_path(phase+.5f);
 p.foot_lift=clamp(std::min(left.y,right.y)-.02f,0,.045f);
 leg_ik(p,7,8,9,left,p.pelvis_y-.06f);leg_ik(p,12,13,14,right,p.pelvis_y-.06f);
 // Back swing passes the waist instead of leaving both forearms forward.
 // Elbows close on the forward swing and open smoothly behind the torso.
 p.joint[5]={-.03f-2.15f*right.z,.015f,-.10f};
 p.joint[10]={-.03f-2.15f*left.z,-.015f,.10f};
 p.joint[6].x=-1.06f-.47f*ease((right.z+.29f)/.53f);
 p.joint[11].x=-1.06f-.47f*ease((left.z+.29f)/.53f);
 return p;
}
inline Pose idle_pose(){
 Pose p;p.pelvis_y=.765f;p.joint[1].x=.035f;p.joint[3].x=-.025f;
 leg_ik(p,7,8,9,{.02f,0,0},p.pelvis_y-.06f);
 leg_ik(p,12,13,14,{.02f,0,0},p.pelvis_y-.06f);
 p.joint[5]={-.06f,0,-.08f};p.joint[10]={-.06f,0,.08f};
 p.joint[6].x=p.joint[11].x=-.34f;return p;
}
inline Pose jumping_pose(float t,float phase,float lean){
 Pose p=running_pose(phase,1.65f,lean);p.mode=1;p.jump_phase=t;
 // Separate ascent, suspended knee recovery, reach for landing and heel preparation.
 float rise=ease(t/.28f),fall=ease((t-.48f)/.45f),tuck=rise*(1-fall);
 p.pelvis_y=.74f;p.sway*=.25f;p.foot_lift=0;
 p.joint[0]={0,.025f*(1-fall),-lean*.08f};
 p.joint[1]={mix(.20f,.04f,fall),-.035f*(1-fall),-lean*.35f};
 p.joint[3]={-.85f*p.joint[1].x+.01f,0,0};
 p.joint[7].x=-.18f-.68f*tuck;p.joint[12].x=.02f-.48f*tuck;
 p.joint[8].x=.24f+1.02f*tuck;p.joint[13].x=.26f+.92f*tuck;
 p.joint[9].x=-.10f-p.joint[7].x-p.joint[8].x;
 p.joint[14].x=-.08f-p.joint[12].x-p.joint[13].x;
 p.joint[5]={-.15f-.75f*rise*(1-fall),0,-.20f-.12f*fall};
 p.joint[10]={-.08f-.62f*rise*(1-fall),0,.20f+.12f*fall};
 p.joint[6].x=-.95f+.40f*fall;p.joint[11].x=-.95f+.40f*fall;
 p.joint[4]={.10f+.18f*tuck,0,.04f};
 // Opposing arm recovery and separated knees clarify the airborne silhouette.
 // This presentation envelope is zero at both jump ends; physical heights,
 // transition timing, landing states and collision volumes remain unchanged.
 p.joint[5].x-=.20f*tuck;p.joint[10].x+=1.05f*tuck;
 p.joint[6].x-=.10f*tuck;p.joint[11].x-=.20f*tuck;
 p.joint[7].x-=.16f*tuck;p.joint[8].x+=.22f*tuck;p.joint[9].x-=.06f*tuck;
 p.joint[12].x+=.32f*tuck;p.joint[13].x+=.12f*tuck;p.joint[14].x-=.44f*tuck;
 p.joint[1].y+=.06f*tuck;p.joint[3].y-=.04f*tuck;
 return p;
}
inline Pose sliding_pose(float phase,float lean){
 Pose p;p.phase=phase;p.mode=4;p.pelvis_y=.32f;p.sway=0;
 p.joint[0]={0,-.12f,-lean*.10f};p.joint[1]={-1.05f,0,-lean*.2f};
 p.joint[3]={.40f,0,0};p.joint[4]={.28f,0,.10f};
 p.joint[5]={.72f,0,-.38f};p.joint[10]={.58f,0,.32f};
 p.joint[6].x=-.35f;p.joint[11].x=-.48f;
 p.joint[7].x=-1.43f;p.joint[8].x=.22f;p.joint[9].x=.05f;
 p.joint[12].x=-1.72f;p.joint[13].x=2.36f;p.joint[14].x=-.38f;
 return p;
}
struct AnimatorState {
 bool initialized=false,was_air=false,was_slide=false;
 float last_clock=0,last_distance=0,last_x=1,last_y=0,last_jump=0,phase=0,speed=11,lean=0;
 float slide_exit=-1,landing=-1,air_age=0,slide_age=0,start_age=-1;
 int last_state=0;
 Pose pose{},transition{},start_pose{};
 void reset(){*this=AnimatorState{};}
 const Pose& update(const Input&i){
  bool rewind=initialized&&(i.distance+1e-4f<last_distance||i.clock+1e-4f<last_clock||
      std::abs(i.player_x-last_x)>.85f||std::abs(i.player_y-last_y)>1.f);
  if(rewind)reset();
  if(!initialized){initialized=true;last_clock=i.clock;last_distance=i.distance;last_x=i.player_x;last_y=i.player_y;last_jump=i.jump_height;
   pose=idle_pose();
   if(i.jump_height>.001f||(!i.supported&&i.player_y>.035f)){
    pose=jumping_pose(i.jump_height>.001f?std::asin(clamp(i.jump_height,0,1))/PI:.84f,0,0);
    if(i.jump_height<=.001f)pose.mode=2;was_air=true;transition=pose;air_age=.12f;
   }
   if(i.slide_phase>=0&&!was_air){pose=sliding_pose(0,0);pose.height_cap=.97f;was_slide=true;transition=pose;slide_age=.12f;}
  }
  float dt=clamp(i.clock-last_clock,0,.10f),travel=std::max(0.f,i.distance-last_distance);
  last_clock=i.clock;last_distance=i.distance;last_y=i.player_y;
  if(i.state!=1){last_x=i.player_x;last_jump=i.jump_height;last_state=i.state;return pose;}
  if(dt<=0)return pose;
  if(last_state==0){start_age=0;start_pose=pose;}last_state=i.state;
  float target=travel>0?clamp(travel/dt,0,24):speed;
  speed=mix(speed,target,1-std::exp(-dt*8));float cadence=clamp(1.65f+(speed-11)*.05f,1.55f,2.15f);
  float lane_velocity=(i.player_x-last_x)/dt;last_x=i.player_x;
  lean=mix(lean,clamp(lane_velocity*.055f,-.30f,.30f),1-std::exp(-dt*16));
  bool active_jump=i.jump_height>.001f;
  bool air=active_jump||(!i.supported&&i.player_y>.035f);
  bool slide=i.slide_phase>=0&&!air; // Real airborne motion takes precedence over slide timer.
  if(!air&&!slide)phase=wrap(phase+dt*cadence);
  Pose run=running_pose(phase,cadence,lean),next=run;
  if(air){
   slide_exit=-1;
   if(!was_air){transition=pose;air_age=0;landing=-1;}air_age+=dt;
   float h=clamp(i.jump_height,0,1),jp=std::asin(h)/PI;
   if(!active_jump)jp=.84f;
   else if(i.jump_height<last_jump-1e-5f || (std::abs(i.jump_height-last_jump)<1e-5f&&pose.jump_phase>.5f))jp=1-jp;
   next=jumping_pose(jp,phase,lean);if(!active_jump)next.mode=2;
   float air_transition=transition.height_cap>0?.25f:.12f;
   next=blend(transition,next,ease(air_age/air_transition));next.jump_phase=jp;next.foot_lift=0;
   // An immediate legal jump after sliding must not abruptly un-squash the body.
   if(transition.height_cap>0&&air_age<air_transition)
       next.height_cap=mix(transition.height_cap,2.25f,ease(air_age/air_transition));
  }else if(was_air){landing=0;transition=pose;phase=0;}
  if(!air&&landing>=0){
   landing+=dt;
   // Let the upper body accept weight slightly before releasing. Preserve
   // the validated pelvis/knee/ankle support envelope exactly.
   float support_compress=std::sin(PI*clamp(landing/.24f,0,1));
   float compress=landing<.10f?ease(landing/.10f):1-ease((landing-.10f)/.14f);
   Pose settle=running_pose(phase,cadence,lean);settle.mode=3;
   settle.pelvis_y-=.065f*support_compress;settle.foot_lift=0;
   // Knee flexion carries the pelvis down while the lowest sole remains on support.
   settle.joint[7].x-=.24f*support_compress;settle.joint[12].x-=.24f*support_compress;
   settle.joint[8].x+=.42f*support_compress;settle.joint[13].x+=.42f*support_compress;
   settle.joint[9].x-=.18f*support_compress;settle.joint[14].x-=.18f*support_compress;
   settle.joint[1].x+=.13f*compress;
   settle.joint[3].x-=.09f*compress;
   settle.joint[5].x-=.10f*compress;settle.joint[10].x-=.10f*compress;
   settle.joint[6].x+=.10f*compress;settle.joint[11].x+=.10f*compress;
   next=blend(transition,settle,ease(landing/.12f));
   if(landing>=.24f)landing=-1;
  }
  if(slide){
   if(!was_slide){transition=pose;slide_age=0;}slide_age+=dt;slide_exit=-1;
   next=blend(transition,sliding_pose(phase,lean),ease(slide_age/.12f));next.height_cap=.97f;next.foot_lift=0;next.mode=4;
  }else if(was_slide&&!air){slide_exit=0;transition=pose;}
  if(!slide&&!air&&slide_exit>=0){slide_exit+=dt;next=blend(transition,run,ease(slide_exit/.28f));next.mode=4;
   next.height_cap=mix(.97f,2.25f,ease(slide_exit/.28f));
   if(slide_exit>=.28f){slide_exit=-1;next.height_cap=0;}}
  if(start_age>=0){start_age+=dt;
   if(!air&&!slide&&landing<0&&slide_exit<0)next=blend(start_pose,next,ease(start_age/.14f));
   if(start_age>=.14f)start_age=-1;
  }
  pose=next;pose.cadence=cadence;pose.phase=phase;last_jump=i.jump_height;was_air=air;was_slide=slide;return pose;
 }
};
} // namespace runner_motion
#endif

