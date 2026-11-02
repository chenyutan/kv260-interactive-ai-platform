// Deterministic RGB colour-ink painting. No geometric resampling, capture or AI.
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <ctime>
#if defined(__aarch64__) && !defined(INK_FORCE_SCALAR)
#include <arm_neon.h>
#define INK_NEON 1
#endif

namespace {
double cpu_ms() {
    timespec t{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return t.tv_sec * 1000.0 + t.tv_nsec * 1e-6;
}
inline int clamp(int x, int lo, int hi) { return std::min(hi, std::max(lo, x)); }
#ifdef INK_NEON
inline uint8x16x4_t table64(const uint8_t* p) {
    return {{vld1q_u8(p),vld1q_u8(p+16),vld1q_u8(p+32),vld1q_u8(p+48)}};
}
inline int16x8_t lookup(const uint8_t* p,uint8x8_t i) {
    uint8x8_t v=vqtbl4_u8(table64(p),i);
    v=vqtbx4_u8(v,table64(p+64),vsub_u8(i,vdup_n_u8(64)));
    v=vqtbx4_u8(v,table64(p+128),vsub_u8(i,vdup_n_u8(128)));
    v=vqtbx4_u8(v,table64(p+192),vsub_u8(i,vdup_n_u8(192)));
    return vreinterpretq_s16_u16(vmovl_u8(v));
}
inline int16x8_t widen(uint8x8_t x) { return vreinterpretq_s16_u16(vmovl_u8(x)); }
inline int16x8_t bounded(int16x8_t x,int lo,int hi) {
    return vminq_s16(vdupq_n_s16(hi),vmaxq_s16(vdupq_n_s16(lo),x));
}
#endif
}

struct InkFilter {
    int w, h;
    cv::Mat gray, smooth, wash, face, texture;
    alignas(64) uint8_t paint[256], portrait[256];
    double timing[4] = {}, cpu_timing[4] = {};

    InkFilter(int width, int height)
        : w(width), h(height), gray(h,w,CV_8U), smooth(h,w,CV_8U),
          wash(h,w,CV_8U), face(h,w,CV_8U), texture(h,w,CV_8S) {
        cv::setNumThreads(1);
        cv::setUseOptimized(true);
        // Continuous transparent pigment washes: dark ink, luminous middle
        // tones and warm paper whites. All nonlinear work happens just once.
        const float xx[] = {0, 20, 48, 80, 112, 144, 176, 208, 240, 255};
        const float yy[] = {16, 31, 64, 113, 165, 203, 227, 241, 250, 252};
        for (int i=0; i<256; ++i) {
            int k=0;
            while (k<8 && i>xx[k+1]) ++k;
            float t=(i-xx[k])/(xx[k+1]-xx[k]);
            // A mostly linear transition avoids posterized skin or wall bands.
            t=.8f*t+.2f*t*t*(3.f-2.f*t);
            paint[i]=int(yy[k]+t*(yy[k+1]-yy[k])+.5f);
            portrait[i]=int(255*(.035f+.95f*std::pow(i/255.f,.92f))+.5f);
        }
        for(int y=0; y<h; ++y) for(int x=0; x<w; ++x) {
            uint32_t z=uint32_t(x*1973+y*9277+89173);
            z=(z^(z>>13))*1274126177u;
            // Stationary, low-amplitude paper grain. Never temporal noise.
            texture.at<int8_t>(y,x)=int8_t((z&255)<40 ? -1 : ((z&255)>215 ? 1 : 0));
        }
    }

    bool face_mask(const float* points) {
        if (!points) return false;
        const float *a=points+5*3, *b=points+6*3;
        for(int k=0;k<3;++k) if(!std::isfinite(a[k]) || !std::isfinite(b[k])) return false;
        if(a[2]<.035f || b[2]<.035f || a[0]<0 || a[0]>1 || b[0]<0 || b[0]>1 ||
           a[1]<0 || a[1]>1 || b[1]<0 || b[1]>1) return false;
        float sw=std::hypot((a[0]-b[0])*w,(a[1]-b[1])*h);
        if(sw<w*.075f || sw>w*.48f || std::abs(a[1]-b[1])*h>=sw*.6f) return false;
        face.setTo(0);
        cv::Point center(int((a[0]+b[0])*.5f*w),int((a[1]+b[1])*.5f*h-sw*.42f));
        cv::Size radius(int(sw*.37f),int(sw*.54f));
        cv::Rect region(center.x-radius.width-6,center.y-radius.height-6,
                        radius.width*2+13,radius.height*2+13);
        region&=cv::Rect(0,0,w,h);
        if(region.empty()) return false;
        cv::ellipse(face,center,radius,0,0,360,cv::Scalar(240),-1,cv::LINE_AA);
        // Only the head-sized ROI needs feathering, not all 230,400 pixels.
        cv::Mat roi=face(region);
        cv::blur(roi,roi,cv::Size(11,11));
        return true;
    }

    template<bool HasFace>
    void render(const cv::Mat& src,uint8_t* out,int out_stride) {
        for(int y=0;y<h;++y) {
            const auto *s=src.ptr<uint8_t>(y), *g=gray.ptr<uint8_t>(y);
            const auto *sm=smooth.ptr<uint8_t>(y), *wa=wash.ptr<uint8_t>(y);
            const auto *f=face.ptr<uint8_t>(y);
            const auto *paper=texture.ptr<int8_t>(y);
            auto *d=out+y*out_stride;
            int x=0;
#ifdef INK_NEON
            for(;x+8<=w;x+=8) {
                uint8x8x3_t rgb=vld3_u8(s+x*3);
                int16x8_t r=widen(rgb.val[0]), gr=widen(rgb.val[1]), b=widen(rgb.val[2]);
                uint8x8_t y8=vld1_u8(g+x);
                int16x8_t lum=widen(y8), wide=widen(vld1_u8(wa+x));
                int16x8_t rg=vsubq_s16(r,gr), rb=vsubq_s16(r,b);
                int16x8_t protection=vminq_s16(vmulq_n_s16(vsubq_s16(rg,vdupq_n_s16(2)),16),
                    vmulq_n_s16(vsubq_s16(rb,vdupq_n_s16(3)),12));
                protection=vminq_s16(protection,vmulq_n_s16(vsubq_s16(gr,vdupq_n_s16(18)),8));
                protection=vminq_s16(protection,vmulq_n_s16(vsubq_s16(vdupq_n_s16(100),rg),8));
                protection=vminq_s16(protection,vmulq_n_s16(vsubq_s16(vdupq_n_s16(145),rb),8));
                protection=bounded(protection,0,240);
                if(HasFace) protection=vmaxq_s16(protection,widen(vld1_u8(f+x)));
                int16x8_t inv=vsubq_s16(vdupq_n_s16(256),protection);
                int16x8_t detail=bounded(vsubq_s16(widen(vld1_u8(sm+x)),lum),-10,10);
                int16x8_t broad=bounded(vsubq_s16(wide,lum),-18,18);
                int16x8_t mixed=vaddq_s16(vmulq_n_s16(detail,4),vmulq_n_s16(broad,7));
                // mixed*inv can exceed signed 16-bit at maximum wash strength.
                int16x8_t local=vaddq_s16(lum,vcombine_s16(
                    vshrn_n_s32(vmull_s16(vget_low_s16(mixed),vget_low_s16(inv)),12),
                    vshrn_n_s32(vmull_s16(vget_high_s16(mixed),vget_high_s16(inv)),12)));
                int16x8_t tone=lookup(paint,vqmovun_s16(local));
                tone=vaddq_s16(tone,vshrq_n_s16(vmulq_s16(vsubq_s16(lookup(portrait,y8),tone),protection),8));
                int16x8_t ink=bounded(vsubq_s16(vsubq_s16(wide,lum),vdupq_n_s16(8)),0,22);
                tone=vsubq_s16(tone,vshrq_n_s16(vmulq_s16(ink,inv),10));
                int16x8_t pigment=vsubq_s16(vdupq_n_s16(44),vshrq_n_s16(protection,2));
                int16x8_t grain=vshrq_n_s16(vmulq_s16(vmovl_s8(vld1_s8(paper+x)),
                    vaddq_s16(vdupq_n_s16(96),vshrq_n_s16(lum,1))),8);
                int16x8_t warmth=vmaxq_s16(vsubq_s16(tone,vdupq_n_s16(100)),vdupq_n_s16(0));
                int16x8_t base=vaddq_s16(tone,grain);
                int16x8_t cr=vsubq_s16(r,lum), cg=vsubq_s16(gr,lum), cb=vsubq_s16(b,lum);
                rgb.val[0]=vqmovun_s16(vaddq_s16(vaddq_s16(base,cr),vaddq_s16(vshrq_n_s16(vmulq_s16(cr,pigment),8),vshrq_n_s16(warmth,6))));
                rgb.val[1]=vqmovun_s16(vaddq_s16(vaddq_s16(base,cg),vshrq_n_s16(vmulq_s16(cg,pigment),8)));
                rgb.val[2]=vqmovun_s16(vsubq_s16(vaddq_s16(vaddq_s16(base,cb),vshrq_n_s16(vmulq_s16(cb,pigment),8)),vshrq_n_s16(warmth,5)));
                vst3_u8(d+x*3,rgb);
            }
#endif
            for(;x<w;++x) {
                int r=s[x*3], gr=s[x*3+1], b=s[x*3+2], lum=g[x];
                // Continuous, full 8-bit skin confidence avoids quantization
                // patches in neutral walls, cheeks or gradual lighting.
                int protection=clamp(std::min({(r-gr-2)*16,(r-b-3)*12,(gr-18)*8,
                    (100-r+gr)*8,(145-r+b)*8}),0,240);
                if(HasFace) protection=std::max(protection,int(f[x]));
                int detail=clamp(int(sm[x])-lum,-10,10);
                int broad=clamp(int(wa[x])-lum,-18,18);
                int local=clamp(lum+((detail*4+broad*7)*(256-protection)>>12),0,255);
                int tone=paint[local]+((int(portrait[lum])-paint[local])*protection>>8);
                int ink=clamp(int(wa[x])-lum-8,0,22)*(256-protection)>>10;
                tone-=ink;
                int pigment=300-(protection>>2);
                int grain=paper[x]*(96+(lum>>1))>>8;
                int warmth=std::max(tone-100,0);
                d[x*3]=uint8_t(clamp(tone+(((r-lum)*pigment)>>8)+(warmth>>6)+grain,0,255));
                d[x*3+1]=uint8_t(clamp(tone+(((gr-lum)*pigment)>>8)+grain,0,255));
                d[x*3+2]=uint8_t(clamp(tone+(((b-lum)*pigment)>>8)-(warmth>>5)+grain,0,255));
            }
        }
    }

    int process(const uint8_t* rgb,int stride,const float* points,uint8_t* out,int out_stride) {
        if(!rgb || !out || stride<w*3 || out_stride<w*3) return -2;
        cv::Mat src(h,w,CV_8UC3,const_cast<uint8_t*>(rgb),stride);
        auto t0=std::chrono::steady_clock::now(); double c0=cpu_ms();
        cv::cvtColor(src,gray,cv::COLOR_RGB2GRAY);
        auto t1=std::chrono::steady_clock::now(); double c1=cpu_ms();
        // Native full-resolution colour and details remain the source. These
        // small luminance guides only move background tones by <=11 levels;
        // their output is never fed back, resized, or repeatedly blurred.
        cv::blur(gray,smooth,cv::Size(3,3));
        cv::blur(gray,wash,cv::Size(7,7));
        auto t2=std::chrono::steady_clock::now(); double c2=cpu_ms();
        bool has_face=face_mask(points);
        auto t3=std::chrono::steady_clock::now(); double c3=cpu_ms();
        if(has_face) render<true>(src,out,out_stride); else render<false>(src,out,out_stride);
        auto t4=std::chrono::steady_clock::now(); double c4=cpu_ms();
        timing[0]=std::chrono::duration<double,std::milli>(t1-t0).count();
        timing[1]=std::chrono::duration<double,std::milli>(t2-t1).count();
        timing[2]=std::chrono::duration<double,std::milli>(t3-t2).count();
        timing[3]=std::chrono::duration<double,std::milli>(t4-t3).count();
        cpu_timing[0]=c1-c0; cpu_timing[1]=c2-c1; cpu_timing[2]=c3-c2; cpu_timing[3]=c4-c3;
        return 0;
    }
};

extern "C" {
void* ink_create(int width,int height) {
    if(width<16 || height<16 || width>2560 || height>1440) return nullptr;
    try { return new InkFilter(width,height); } catch(...) { return nullptr; }
}
int ink_process(void* handle,const uint8_t* rgb,int stride,const float* points,uint8_t* out,int out_stride) {
    if(!handle) return -1;
    try { return static_cast<InkFilter*>(handle)->process(rgb,stride,points,out,out_stride); } catch(...) { return -3; }
}
void ink_destroy(void* handle) { delete static_cast<InkFilter*>(handle); }
void ink_timing(void* handle,double* result) { if(handle&&result) std::copy_n(static_cast<InkFilter*>(handle)->timing,4,result); }
void ink_cpu_timing(void* handle,double* result) { if(handle&&result) std::copy_n(static_cast<InkFilter*>(handle)->cpu_timing,4,result); }
const char* ink_version() { return "color-ink-rgb-v3"; }
}
