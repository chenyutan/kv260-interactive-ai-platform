"""QR and download instructions drawn in the existing native Cairo menu."""
import io
from action_menu import ActionMenu,_block
from menu_view import cairo,text
from .layout import qr_box

class VisitorView(ActionMenu):
    def __init__(self,choose):
        super().__init__(choose);self.info={};self.qr=None
        self.set_actions('我的画卷与成绩',[
            dict(id='visitor_back',title='返回主菜单',subtitle='继续探索，各境成绩自动记录'),
            dict(id='visitor_refresh',title='刷新二维码',subtitle='先连接同一 Wi-Fi 或手机热点'),
            dict(id='visitor_finish',title='结束游玩',subtitle='归档作品、清空当前画卷，迎接下一位',danger=True)])
    def set_info(self,info):
        signature=(info.get('url'),info.get('name'),info.get('message'),info.get('status'))
        old=tuple(self.info.get(k) for k in ('url','name','message','status'))
        if signature==old:return
        self.info=info
        self.qr=cairo.ImageSurface.create_from_png(io.BytesIO(info['qr'])) if info.get('qr') else None
        self.cache_key=None;self.queue_draw()
    def build_static(self,w,h):
        super().build_static(w,h);cr=cairo.Context(self.static_surface);s=min(w/1024,h/550)
        if self.qr:
            box=qr_box(w,h,self.qr.get_width()//7);side,x,y=box['side'],box['x'],box['y']
            cr.save();cr.translate(x,y);cr.scale(side/self.qr.get_width(),side/self.qr.get_height())
            cr.set_source_surface(self.qr,0,0);cr.get_source().set_filter(cairo.FILTER_NEAREST);cr.paint();cr.restore()
        else:_block(cr,'连接 Wi-Fi 后，此处出现本次游玩的专属二维码。',w*.355,h*.61,w*.29,h*.23,17*s)
        _block(cr,'扫码后注册或登录，绑定本次游玩，下载作品和成绩。\n结束游玩后，仍可登录自己的账户查看记录。',w*.04,h*.59,w*.28,h*.27,13*s)
        _block(cr,self.info.get('message','正在准备作品与报告…'),w*.67,h*.59,w*.29,h*.14,13*s)
        _block(cr,self.info.get('url') or '请从“设备 / Wi-Fi”连接手机热点。',w*.67,h*.75,w*.29,h*.14,10*s)
