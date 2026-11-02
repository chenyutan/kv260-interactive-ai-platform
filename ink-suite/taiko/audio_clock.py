"""GStreamer stream position is the master clock, shared by notes and judges.

Silent mode is explicit. Device failure freezes gameplay until retry or an
explicit silent-mode selection; it never silently fabricates an audio clock.
"""
import math
import time


class SilentClock:
    def __init__(self, now=time.monotonic):
        self.now=now;self.base=0.;self.anchor=now();self.playing=False
        self.error=None;self.eos=False;self.mode='silent';self.notice='无声模式 · 未验证扬声器'

    def position(self):
        return self.base + (max(0.,self.now()-self.anchor) if self.playing else 0.)

    def play(self):
        if self.error:return False
        if not self.playing:self.anchor=self.now();self.playing=True
        return True

    def pause(self):
        self.base=self.position();self.playing=False

    def seek(self,seconds):
        if not math.isfinite(seconds) or seconds<0:raise ValueError('Invalid seek')
        self.base=seconds;self.anchor=self.now();self.eos=False
        return True

    def poll(self):return not self.error
    def set_volume(self,value):
        if not math.isfinite(value) or not 0<=value<=1:raise ValueError('Invalid volume')
    def close(self):self.pause()


class GstClock:
    def __init__(self,path,sink='autoaudiosink',now=time.monotonic):
        import gi
        gi.require_version('Gst','1.0')
        from gi.repository import Gst
        Gst.init(None)
        self.Gst=Gst;self.path=path;self.sink=sink;self.now=now
        self.error=None;self.eos=False;self.playing=False;self.last=0.
        self.last_advance=now();self.requested='paused';self.closed=False
        self.startup_at=None;self.started=False;self.query_ok=None
        self.prepare_seconds=0.;self.prepare_position=None
        self.startup_seconds=0.
        self.mode='gstreamer-fakesink' if sink=='fakesink' else 'gstreamer-audio'
        self.notice='静音音频管线 · 非扬声器验收' if sink=='fakesink' else '音频主时钟 · 端到端延迟待校准'
        self.pipeline=Gst.ElementFactory.make('playbin','taiko-music')
        output=Gst.ElementFactory.make(sink,'taiko-audio-output')
        if self.pipeline is None or output is None:raise RuntimeError('GStreamer audio elements unavailable')
        self.output=output
        if output.find_property('sync'):output.set_property('sync',True)
        self.pipeline.set_property('audio-sink',output)
        self.pipeline.set_property('uri',path.resolve().as_uri())
        # WAV-only asset still explicitly disables video/text decode/render.
        self.pipeline.set_property('flags',2)
        self.bus=self.pipeline.get_bus()
        # Long corked Pulse preroll accumulates a clock offset on this board.
        # Keep no preroll/stream while idle; every play verifies a fresh seek.
        if self.pipeline.set_state(Gst.State.READY)==Gst.StateChangeReturn.FAILURE:
            self.error='音频设备初始化失败'

    def _release(self):
        self.playing=False;self.requested='paused';self.startup_at=None;self.started=False
        self.pipeline.set_state(self.Gst.State.READY)

    def _remember_position(self):
        ok,value=self.pipeline.query_position(self.Gst.Format.TIME)
        self.query_ok=bool(ok)
        if ok and value>=0:self.last=max(self.last,value/self.Gst.SECOND)

    def _prepare(self,seconds,deadline=None):
        """Verify native preroll and seek under one total two-second budget."""
        G=self.Gst;begin=self.now()
        if deadline is None:deadline=begin+2.
        self.prepare_position=None
        try:
            if self.pipeline.set_state(G.State.PAUSED)==G.StateChangeReturn.FAILURE:
                raise RuntimeError('音频设备初始化失败')
            for seeking in (False,True):
                timeout=int(max(0.,deadline-self.now())*G.SECOND)
                result,state,pending=self.pipeline.get_state(timeout)
                if (result!=G.StateChangeReturn.SUCCESS or state!=G.State.PAUSED
                        or pending!=G.State.VOID_PENDING):
                    raise RuntimeError('音频定位未完成，请重试音频')
                if not self.poll():return False
                if not seeking:
                    if not self.pipeline.seek_simple(G.Format.TIME,
                            G.SeekFlags.FLUSH|G.SeekFlags.ACCURATE,int(seconds*G.SECOND)):
                        raise RuntimeError('音频定位失败，请重试音频')
            ok,value=self.pipeline.query_position(G.Format.TIME)
            self.query_ok=bool(ok)
            if not ok or value<0 or abs(value/G.SECOND-seconds)>.05:
                raise RuntimeError('音频定位未确认，请重试音频')
            self.prepare_position=value/G.SECOND
            return True
        except RuntimeError as error:
            self.error=str(error);return False
        finally:
            self.prepare_seconds=max(0.,self.now()-begin)
            if self.error:self._release()

    def output_warning_failed(self,message,error):
        """Only output-resource failures imply a silent dummy-sink fallback.

        autoaudiosink can report failure to find/open a real output as WARNING
        and keep its dummy clock running. Ordinary stream/latency warnings do
        not mean the output failed, nor do resource warnings from file sources.
        """
        G=self.Gst
        if self.sink=='fakesink':return False
        failures=(G.ResourceError.NOT_FOUND,G.ResourceError.BUSY,
                  G.ResourceError.OPEN_WRITE,G.ResourceError.OPEN_READ_WRITE,
                  G.ResourceError.WRITE)
        if not any(error.matches(G.ResourceError.quark(),code) for code in failures):return False
        source=message.src
        while source is not None:
            if source==self.output:return True
            source=source.get_parent()
        return False

    def poll(self):
        G=self.Gst
        while True:
            message=self.bus.pop_filtered(G.MessageType.ERROR|G.MessageType.EOS|G.MessageType.WARNING)
            if message is None:break
            if message.type==G.MessageType.ERROR:
                error,_=message.parse_error();self.error=str(error.message)
                self._release()
            elif message.type==G.MessageType.WARNING:
                error,_=message.parse_warning()
                if self.output_warning_failed(message,error):
                    self.error=str(error.message)
                    self._release()
            elif message.type==G.MessageType.EOS:
                self._remember_position();self.eos=True;self._release()
        return not self.error

    def position(self):
        if not self.poll():return self.last
        if not self.playing:return self.last
        G=self.Gst
        result,state,pending=self.pipeline.get_state(0)
        if state!=G.State.PLAYING or pending!=G.State.VOID_PENDING:
            self.error='音频播放未就绪，请重试音频';self._release();return self.last
        ok,value=self.pipeline.query_position(self.Gst.Format.TIME)
        self.query_ok=bool(ok)
        if ok and value>=0:
            position=value/self.Gst.SECOND
            if position>self.last+.0001:self.last_advance=self.now()
            self.last=max(self.last,position)
        if self.playing and self.now()-self.last_advance>2.0:
            self.error='音频时钟停止推进，请重试音频';self.pause()
        return self.last

    def play(self):
        if getattr(self,'closed',False):return False
        if not self.poll():return False
        if self.playing:return True
        begin=self.now();deadline=begin+2.
        if not self._prepare(self.last,deadline):
            self.startup_seconds=max(0.,self.now()-begin);return False
        result=self.pipeline.set_state(self.Gst.State.PLAYING)
        if result==self.Gst.StateChangeReturn.FAILURE:
            self.error='音频播放失败';self._release()
        else:
            result,state,pending=self.pipeline.get_state(int(max(0.,deadline-self.now())*self.Gst.SECOND))
            self.playing=(result==self.Gst.StateChangeReturn.SUCCESS
                          and state==self.Gst.State.PLAYING and pending==self.Gst.State.VOID_PENDING)
            if not self.playing:self.error='音频播放未就绪，请重试音频';self._release()
            elif not self.poll():self._release()
            else:
                self.last_advance=self.now();self.startup_at=self.now();self.started=True
                self.requested='playing'
        self.startup_seconds=max(0.,self.now()-begin)
        return self.playing

    def pause(self):
        if self.playing and not self.error:self._remember_position()
        self._release()

    def seek(self,seconds):
        if not math.isfinite(seconds) or seconds<0:raise ValueError('Invalid seek')
        if getattr(self,'closed',False) or not self.poll():return False
        self.pause()
        success=self._prepare(seconds)
        if success:self.last=seconds;self.eos=False;self.last_advance=self.now()
        self._release()
        return success

    def close(self):
        self.playing=False;self.closed=True;self.requested='closed'
        self.startup_at=None;self.pipeline.set_state(self.Gst.State.NULL)

    def transport_status(self):
        """Local native state only: no Pulse RPC, file IO or waiting."""
        result,state,pending=self.pipeline.get_state(0)
        clock=self.pipeline.get_clock()
        name=lambda value:getattr(value,'value_nick',str(value))
        return dict(requested=getattr(self,'requested','playing' if self.playing else 'paused'),
                    actual_state=name(state),pending_state=name(pending),state_result=name(result),
                    clock_name=clock.get_name() if clock else None,last_position=self.last,
                    query_ok=getattr(self,'query_ok',None),playing=self.playing,
                    startup_pending=self.playing and not getattr(self,'started',False),
                    prepare_seconds=getattr(self,'prepare_seconds',0.),
                    prepare_position=getattr(self,'prepare_position',None),
                    startup_seconds=getattr(self,'startup_seconds',0.))

    def set_volume(self,value):
        if not math.isfinite(value) or not 0<=value<=1:raise ValueError('Invalid volume')
        self.pipeline.set_property('volume',float(value))
