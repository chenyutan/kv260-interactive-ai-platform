#include <math.h>
#include <stddef.h>
// Targets are the same cached shoulder-relative float64 arrays used by NumPy.
// Each template is evaluated independently. Caller retains original NumPy
// decisions near a threshold to cover floating-point reduction differences.
int bodyink_start_errors(const double *pose,const double *targets,
                         const unsigned char *moving,const unsigned char *torso_class,
                         const double *angles,int count,double *errors) {
 if(!pose||!targets||!moving||!torso_class||!angles||!errors||count<1)return -1;
 double torso[2]={(pose[0]+pose[2])*.5,(pose[1]+pose[3])*.5};
 double relative[16];for(int j=0;j<16;++j)relative[j]=pose[j]-torso[j%2];
 double ax=pose[0]-pose[2],ay=pose[1]-pose[3];
 double norm=fmax(sqrt(ax*ax+ay*ay),.1);ax/=norm;ay/=norm;
 double left=(pose[8]-pose[0])*ax+(pose[9]-pose[1])*ay;
 double right=-((pose[10]-pose[2])*ax+(pose[11]-pose[3])*ay);
 double arm_limit=fmax(0.,fmax(.65-left,.65-right));
 double angle=atan2(torso[0],-torso[1]);
 for(int i=0;i<count;++i) {
   if(torso_class[i]){errors[i]=fmax(arm_limit,fabs(angle-angles[i]));continue;}
   double arm=0.,motion=0.;int number=0;
   for(int joint=0;joint<8;++joint)for(int axis=0;axis<2;++axis) {
     int j=joint*2+axis;double d=relative[j]-targets[i*16+j];double square=d*d;
     if(joint>=2&&joint<6)arm+=square;
     if(moving[i*8+joint]){motion+=square;++number;}
   }
   errors[i]=fmax(sqrt(arm/8.),sqrt(motion/(number?number:1)));
 }
 return 0;
}

static void sort_values(double *v,int n) {
 for(int i=1;i<n;++i){double x=v[i];int j=i;while(j>0&&v[j-1]>x){v[j]=v[j-1];--j;}v[j]=x;}
}
double bodyink_stability_radius(const double *poses,int n) {
 if(!poses||n<4||n>512)return NAN;
 double values[512],distances[512],center[16];
 for(int j=0;j<16;++j) {
   for(int i=0;i<n;++i)values[i]=poses[i*16+j];
   sort_values(values,n);
   center[j]=(n%2)?values[n/2]:(values[n/2-1]+values[n/2])*.5;
 }
 for(int i=0;i<n;++i) {
   double sum=0.;for(int j=0;j<16;++j){double d=poses[i*16+j]-center[j];sum+=d*d;}
   distances[i]=sqrt(sum/16.);
 }
 sort_values(distances,n);
 double rank=(n-1)*.9;int lo=(int)rank,hi=lo+1<n?lo+1:lo;
 double fraction=rank-lo,a=distances[lo],b=distances[hi];
 return fraction>=.5?b-(b-a)*(1-fraction):a+(b-a)*fraction;
}
