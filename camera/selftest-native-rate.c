#define main bodyink_capture_main
#include "bodyink-camera.c"
#undef main
#include <assert.h>
#include <math.h>
int main(void) {
    struct snapshot_request request={.fd=-1};
    struct stats s={0};
    double next=0,old_next=0;unsigned delivered=0,old_delivered=0;
    for(unsigned frame=0;frame<3600;++frame) {
        double now=10.+frame/60.+(frame%2 ? .0002 : 0.);
        begin_snapshot_request(&request,123,now,next,&s);
        if(snapshot_request_ready(&request,now)) {
            ++delivered;next=next_snapshot_deadline(now,60);
        }
        if(now>=old_next){++old_delivered;old_next=now+1./60.;}
    }
    assert(delivered==3600);assert(old_delivered<3600);
    for(unsigned fps=1;fps<60;++fps)
        assert(fabs(next_snapshot_deadline(10.,fps)-(10.+1./fps))<1e-12);
    printf("PASS: 3600 unique frame opportunities; new=%u old=%u; lower caps preserved\n",delivered,old_delivered);
    return 0;
}
