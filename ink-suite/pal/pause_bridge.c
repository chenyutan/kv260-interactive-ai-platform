/* Private PAL-only SDL 1.2 adapter. SPDX-License-Identifier: MIT
 * No system library is replaced. LD_PRELOAD is set only for the PAL child.
 * Shared words: desired state (run=0,pause=1,quit=2), acknowledged, mute,
 * packed pending size (width in low 16 bits, height in high 16 bits),
 * game ticks, directed keys, applied audio pause, observed keys.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <string.h>

static volatile uint32_t *control;
static uint64_t paused_total, pause_start;
static int caller_audio_pause = 1, applied_audio = -1;
static void (*real_audio)(int);
static uint32_t (*real_ticks)(void);
static void (*real_delay)(uint32_t);
static int (*real_poll)(void *);
static uint8_t *(*real_keys)(int *);

static uint64_t now_ms(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+(uint64_t)t.tv_nsec/1000000;
}
__attribute__((constructor)) static void init(void) {
    const char *path=getenv("INK_PAL_CONTROL");
    if(path && *path) {
        int fd=open(path,O_RDWR|O_CLOEXEC|O_NOFOLLOW);
        if(fd>=0) {
            void *p=mmap(NULL,32,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
            if(p!=MAP_FAILED) control=p;
            close(fd);
        }
    }
    real_audio=dlsym(RTLD_NEXT,"SDL_PauseAudio");
    real_ticks=dlsym(RTLD_NEXT,"SDL_GetTicks");
    real_delay=dlsym(RTLD_NEXT,"SDL_Delay");
    real_poll=dlsym(RTLD_NEXT,"SDL_PollEvent");
    real_keys=dlsym(RTLD_NEXT,"SDL_GetKeyState");
}
static void audio_state(void) {
    int desired=caller_audio_pause || (control && (control[0] || control[2]));
    if(real_audio && desired!=applied_audio) { real_audio(desired); applied_audio=desired; }
    if(control) control[6]=(uint32_t)desired;
}
void SDL_PauseAudio(int pause) { caller_audio_pause=!!pause; applied_audio=-1; audio_state(); }
static int gate(void) {
    if(!control) return 0;
    if(real_ticks) control[4]=real_ticks()-(uint32_t)paused_total;
    audio_state();
    if(control[0]==1) {
        pause_start=now_ms(); control[1]=1;
        while(control[0]==1) { usleep(10000); audio_state(); }
        paused_total+=now_ms()-pause_start; pause_start=0;
    }
    control[1]=control[0]; audio_state();
    return control[0]==2;
}
uint32_t SDL_GetTicks(void) {
    uint32_t raw=real_ticks?real_ticks():0;
    uint64_t offset=paused_total+(pause_start?now_ms()-pause_start:0);
    return raw-(uint32_t)offset;
}
void SDL_Delay(uint32_t ms) {
    if(gate()) return;
    /* Bound the response to host pause/shutdown during long PAL delays. */
    while(ms) { uint32_t part=ms>10?10:ms; if(real_delay) real_delay(part); ms-=part; if(gate()) return; }
}
int SDL_PollEvent(void *event) {
    if(gate()) { if(event) { memset(event,0,24); *(uint8_t*)event=12; } return 1; }
    if(control && event) {
        /* GTK only replaces this aligned shared word; the game thread owns
         * consumption and SDL surface changes. exchange does not erase a newer
         * allocation arriving after it, and a NULL poll never consumes one.
         * Keep the 32-byte protocol compatible: old hosts leave word 3 zero.
         */
        uint32_t size=__atomic_exchange_n(&control[3],0,__ATOMIC_ACQ_REL);
        int32_t width=(int32_t)(size&0xffffU), height=(int32_t)(size>>16);
        if(width>=320 && width<=3840 && height>=200 && height<=2160) {
            /* SDL 1.2 SDL_ResizeEvent: type at 0, native int w/h at 4/8.
             * PAL's own event path calls VIDEO_Resize. Do not touch SDL from
             * the GTK process, enqueue events repeatedly, or restart PAL.
             */
            memset(event,0,24); *(uint8_t*)event=16;
            memcpy((uint8_t*)event+4,&width,sizeof(width));
            memcpy((uint8_t*)event+8,&height,sizeof(height));
            return 1;
        }
    }
    return real_poll?real_poll(event):0;
}
uint8_t *SDL_GetKeyState(int *number) {
    int count=0; uint8_t *keys=real_keys?real_keys(&count):NULL;
    static uint8_t merged[1024];
    if(number) *number=count;
    if(control && keys && count>0 && count<=(int)sizeof(merged)) {
        const int codes[]={273,274,276,275,13,27,32,280,281}; uint32_t mask=0;
        memcpy(merged,keys,(size_t)count);
        /* Original keyboard state is never mutated. Only nine known PAL
         * actions are added to a private snapshot, never to the X11 server. */
        for(unsigned i=0;i<sizeof(codes)/sizeof(codes[0]);++i)
            if(codes[i]<count) {
                if(control[5]&(1U<<i)) merged[codes[i]]=1;
                if(merged[codes[i]]) mask|=1U<<i;
            }
        control[7]=mask;
        return merged;
    }
    return keys;
}
