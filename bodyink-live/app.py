"""Live YUYV snapshots -> pretrained DPU pose -> causal actions -> ink events."""
import argparse,collections,io,json,logging,os,queue,signal,sys,threading,time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from movenet import MoveNet,YuyvPreprocessor,RoiSelector,body_rois
from hourglass import Hourglass
from hourglass_select import rois as hourglass_rois
from pose_pipeline import description,identity,infer_hourglass
from continuous_pose_pipeline import infer_continuous
from roi_continuity import ContinuousRoi
from action_roi_continuity import TemporalA02Continuity
from body_crop import BodyCrop
from snapshot import capture_snapshot
from action_engine import ACTIONS,quality_thresholds
from flow_action_engine import ActionEngine
from resume_art_once import restore as restore_art_once
from scene_store import SceneStore
from ink_renderer_fast import InkRenderer
from web_ui import ViewerServer
from camera_settings import CameraSettings
from camera_link import read_camera_link
from pose_overlay import draw_pose
from suite_control import SuiteControl
from environment_capture import EnvironmentCapture
from gamepad_stroke import GamepadStrokeGate,apply_controller_stroke,controller_allowed

ROOT=Path(os.environ.get('BODYINK_RUNTIME_ROOT',Path(__file__).resolve().parent))
EDGES=((5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16))
MESSAGES={
    'hold_a_prepared_start':'直接做动作，无需停顿。',
    'prepared_start_held':'已捕捉动作起势，继续动作。',
    'moving_preparation_rearmed':'已捕捉新的动作起势，继续动作。',
    'waiting_for_directed_motion':'继续自然完成动作。',
    'directed_motion_started':'动作进行中。',
    'continue_to_terminal_pose':'继续完成动作，识别到即落墨。',
    'terminal_pose_candidate':'正在确认动作轨迹。',
    'waiting_for_terminal_stability':'继续自然完成动作。',
    'return_to_neutral_before_next_action':'请先放下双手，回到自然站立。',
    'returned_to_neutral':'可以准备下一个动作。',
    'body_keypoints_not_reliable':'请站在取景框中央，让肩、肘、腕、髋清晰可见。',
    'body_out_of_frame':'身体关键部位已出框，请回到画面中央。',
    'tracking_lost_reset':'人体追踪中断，请重新准备动作。',
    'action_completed':'动作已识别，正在落墨。',
    'ambiguous_action':'动作还不够明确，请回到自然站立后重试。',
    'trajectory_outside_templates':'动作与已学习的范围不同，请按动作说明重试。',
    'execution_tracking_gap':'动作中有关键点丢失，请保持完整入镜后重试。',
}

class LiveApp:
    def __init__(self,args):
        self.args=args;self.lock=threading.RLock();self.paint_lock=threading.RLock()
        self.stop=threading.Event();self.latest=queue.Queue(maxsize=1);self.failure=None;self.generation=0
        self.next_prepare_at=0.
        # One speculative packet may overlap the packet currently on the DPU.
        # The consumer grants the next preparation only after dequeuing one.
        self.prepare_credit=threading.Event();self.prepare_credit.set()
        self.limit_prefetch=getattr(args,'prefetch_credit','bounded')=='bounded'
        self.suite=SuiteControl();self.environment_capture=EnvironmentCapture(capture_snapshot,YuyvPreprocessor().resize,self.suite.snapshot,self.environment_pose,self.stop)
        self.input_mode='body';self.gamepad_strokes=GamepadStrokeGate()
        self.action_resets=collections.deque(maxlen=16);self.action_reset_count=0
        self.body_crop=BodyCrop() if os.environ.get("BODYINK_BODY_CROP")=="1" else None
        self.roi_policy=ContinuousRoi() if os.environ.get('BODYINK_CONTINUOUS_ROI')=='1' else None
        self.preview_changed=threading.Condition(self.lock);self.preview_version=0;self.preview_stamp=None
        self.notice='';self.notice_until=0.;self.reset_requested=False
        self.preview_queue=queue.Queue(maxsize=1);self.paint_queue=queue.Queue(maxsize=4)
        self.raw_preview=os.environ.get('BODYINK_RAW_PREVIEW')=='1'
        self.preview_rgb=None;self.jpeg_lock=threading.Lock();self.jpeg_cache=(-1,b'')
        self.preview=b'';self.phase='warming_up';self.last_progress=time.monotonic()
        self.renderer=InkRenderer();self.scene_store=SceneStore(ROOT/'works/autosave-scene.json')
        restored_checkpoint=restore_art_once(self.renderer,ROOT/'resume-art-once.json')
        if restored_checkpoint:self.suite.configure(dict(active=True,mode='ink',menu=True))
        self.autosave_status=(dict(restored=True,source='deployment_checkpoint') if restored_checkpoint else self.scene_store.restore(self.renderer))
        if self.autosave_status.get('warnings'):logging.warning('Artwork restore: %s',self.autosave_status)
        self.art=self.png();self.theme='mixed';self.art_revision=0
        self.animation_session=str(os.getpid())+':'+str(time.monotonic_ns())
        self.art_scene=self.make_art_scene(0)
        if restored_checkpoint:self.persist_scene(self.art_scene)
        self.state=dict(pid=os.getpid(),phase='warming_up',quality='正在初始化',message='正在加载人体模型',
            capture_fps=None,pose_fps=None,latency_ms=None,event_count=len(self.renderer.events),art_revision=0,
            theme='mixed',preview_has_overlay=True,model_name='AMD MoveNet · DPU',points17=None)
        self.started=time.monotonic();self.pose_count=0;self.capture_failures=0;self.latest_dropped=0
        self.camera_monitor_at=0.;self.camera_status={}
        self.state['camera_link']=read_camera_link()
        if getattr(args,'backend','movenet')=='hourglass':
            self.net=Hourglass(ROOT/'models/hourglass-pe_mpii.xmodel');self.rois=hourglass_rois;self.input_size=(256,256)
        else:
            self.net=MoveNet(ROOT/'models/movenet_ntd_pt.xmodel',ROOT/'models/movenet_ntd_pt.prototxt');self.rois=body_rois;self.input_size=(192,192)
        self.state['model_name']=self.net.info.get('name','人体姿态模型')+' · DPU'
        self.pre=YuyvPreprocessor();self.selector=RoiSelector()
        templates=np.load(args.templates) if args.templates.exists() else {}
        calibration=json.loads(args.calibration.read_text()) if args.calibration.exists() else {}
        if 'calibration' in calibration: calibration=calibration['calibration']
        report_path=args.templates.with_name('template_report.json')
        report={}
        if args.templates.exists():
            report=json.loads(report_path.read_text())
            if report.get('model_sha256')!=self.net.info['model_sha256']:
                raise ValueError('Action templates belong to a different pose model')
            if report.get('inference_pipeline')!=identity(description(self.net,self.rois(1920,1080))):
                raise ValueError('Action templates use a different pose preprocessing/ROI policy')
        engine_config=dict(report.get('engine_config') or {})
        # Only the explicit deployment option can limit the action set. A
        # diagnostic template report must not silently change live behaviour.
        engine_config['enabled_actions']=getattr(args,'enabled_actions',None)
        self.action_roi_continuity=TemporalA02Continuity()
        self.engine=ActionEngine(templates,calibration,config=engine_config)
        self.action_process=None
        if os.environ.get('BODYINK_ACTION_PROCESS','1')=='1':
            from action_process import ActionProcess
            engine_root=Path(os.environ.get('BODYINK_ACTION_ENGINE_ROOT',str(ROOT)))
            self.action_process=ActionProcess(engine_root,{k:templates[k].copy() for k in templates},calibration,engine_config)
        self.state['action_process_enabled']=self.action_process is not None
        self.overlay_thresholds=np.full(17,.3);self.overlay_thresholds[5:13]=quality_thresholds(self.engine.config)
        self.state.update(enabled_actions=list(self.engine.enabled_actions),
            disabled_actions=[a for a in ACTIONS if a not in self.engine.enabled_actions],
            readiness_errors=self.engine.readiness_errors,
            six_class_readiness_errors=report.get('readiness_errors',[]))
        self.camera_settings=CameraSettings(ROOT);self.camera_pid=None
        self.camera_restore_attempts=0;self.camera_restored=False
        controls=self.camera_settings.restore_saved()
        if controls.get('skipped'):controls=self.camera_settings.read()
        self.state['camera_controls']=controls
        self.viewer=ViewerServer(args.host,args.port,self.get_state,self.get_art,self.get_preview,self.command,self.get_saved_art,get_preview_frame=self.get_preview_frame,get_preview_rgb_frame=self.get_preview_rgb_frame,get_art_scene=self.get_art_scene,get_environment_frame=self.get_environment_frame,get_environment_photo=self.get_environment_photo)
        self.frames=collections.deque(maxlen=120);self.timings=collections.deque(maxlen=120)
        args.output.mkdir(parents=True,exist_ok=True)
        self.log_path=args.output/('events-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
        logging.info('model %s',json.dumps(self.net.info))

    def reset_action_state(self, preserve_a02_sides=(), preserve_a02_optional_sides=()):
        self.action_roi_continuity.reset()
        self.engine.reset(preserve_a02_sides=preserve_a02_sides,preserve_a02_optional_sides=preserve_a02_optional_sides)
        if self.action_process is not None:self.action_process.reset(preserve_a02_sides=preserve_a02_sides,preserve_a02_optional_sides=preserve_a02_optional_sides)

    def action_snapshot(self):
        if self.action_process is not None:return self.action_process.snapshot
        return dict(phase=self.engine.phase,last_rejection=self.engine.last_rejection,
            prepared_actions=sorted({v.action for b in self.engine._branches for v in b.start_candidates}))

    def suite_snapshot(self):
        with self.paint_lock:
            return dict(self.suite.snapshot(),input_mode=self.input_mode)

    def body_ink_allowed(self,epoch=None):
        return self.input_mode=='body' and self.suite.ink_allowed(epoch) and time.monotonic()-self.last_progress<=.3

    def paint_event_allowed(self,event,epoch):
        if event.get('origin')=='gamepad':
            return self.suite.ink_allowed(epoch) and controller_allowed(self.suite.snapshot(),self.input_mode)
        return self.body_ink_allowed(epoch)

    def drain_completed_action(self):
        if self.action_process is None:return
        completed=self.action_process.finish_ready()
        if completed is None:return
        result=completed['result'];context=completed['metadata']
        if not self.body_ink_allowed(context['suite_epoch']):return
        with self.lock:
            if context['generation']!=self.generation:return
        event=result.get('event')
        if event is not None:
            event['calibration']=event['speed_calibration']
            event['render_queued_monotonic']=time.monotonic()
            try:self.paint_queue.put_nowait((event,context['theme'],context['generation'],context['suite_epoch']))
            except queue.Full:logging.error('renderer queue full; action not drawn')
        with self.lock,self.suite.lock:
            if context['generation']!=self.generation or not self.body_ink_allowed(context['suite_epoch']):return
            self.state.update(action_timestamp_seconds=context['timestamp'],
                action_compute_ms=completed['cpu_ms'],action_result_age_ms=(time.monotonic()-context['timestamp'])*1000,
                phase=result['phase'],quality_detail=result['quality'],quality_detail_timestamp_seconds=context['timestamp'],
                reason=result.get('reason',''),prepared_actions=completed['snapshot']['prepared_actions'])
            if event is not None:self.state['message']=MESSAGES['action_completed']

    def png(self):
        output=io.BytesIO();self.renderer.image().save(output,format='PNG',compress_level=1);return output.getvalue()
    def get_state(self):
        with self.lock:
            result=dict(self.state);result['suite']=dict(self.suite.snapshot(),input_mode=self.input_mode)
            if time.monotonic()<self.notice_until:result['message']=self.notice
            if time.monotonic()-self.last_progress>.3:
                result['points17']=None
            if time.monotonic()-self.last_progress>3:
                result.update(phase='lost',message='人体识别暂未更新，正在恢复连接。',pose_fps=0,capture_fps=None,latency_ms=None,points17=None)
            link=result.get('camera_link',{})
            if link and not link.get('ready_for_1080p60_yuyv'):
                result.update(phase='camera_link_slow' if link.get('present') else 'camera_unavailable',
                    message=link['message'],quality='摄像头连接尚未就绪',pose_fps=0,capture_fps=None,latency_ms=None,points17=None)
            result['capture_failures']=self.capture_failures
            result['artwork_autosave']=dict(self.autosave_status)
            result['renderer_version']='pixel-exact-vectorized-png1'
            result['animation_protocol']=1
            if controller_allowed(result['suite'],self.input_mode):
                result.update(phase='controller',input_mode='gamepad',quality='手柄模式，无需入镜')
                if time.monotonic()>=self.notice_until:result['message']='手柄模式：LB/RB选择画法，按A落墨。'
            return result
    def make_art_scene(self,revision):
        return dict(schema=1,session=self.animation_session,generation=self.generation,revision=revision,width=self.renderer.width,height=self.renderer.height,seed=self.renderer.seed,events=self.renderer.events)
    def get_art_scene(self):
        with self.lock:return self.art_scene
    def get_art(self):
        with self.lock:return self.art
    def get_saved_art(self,name):
        return (self.args.output/name).read_bytes()
    def get_preview(self):
        frame=self.get_preview_frame(-1)
        return frame[0] if frame else b''

    def _preview_snapshot(self,after):
        with self.preview_changed:
            self.preview_changed.wait_for(lambda:self.preview_version!=after or self.stop.is_set(),timeout=1.)
            if self.stop.is_set() or self.preview_version==after or (not self.preview and self.preview_rgb is None) or time.monotonic()-self.last_progress>=3:
                return None
            return self.preview,self.preview_rgb,self.preview_version,self.preview_stamp

    def get_preview_rgb_frame(self,after):
        if not self.raw_preview:return None
        frame=self._preview_snapshot(after)
        if frame is None:return None
        _,rgb,version,stamp=frame
        if rgb is None:return None
        data,width,height=rgb
        return data,version,max(0.,(time.monotonic()-stamp)*1000),width,height,width*3

    def get_preview_frame(self,after):
        """JPEG compatibility, encoded on demand only for an RGB-mode consumer."""
        frame=self._preview_snapshot(after)
        if frame is None:return None
        data,rgb,version,stamp=frame
        if rgb is not None:
            with self.jpeg_lock:
                if self.jpeg_cache[0]!=version:
                    pixels,width,height=rgb
                    output=io.BytesIO();Image.frombytes('RGB',(width,height),pixels).save(output,format='JPEG',quality=80)
                    self.jpeg_cache=(version,output.getvalue())
                data=self.jpeg_cache[1]
        return data,version,max(0.,(time.monotonic()-stamp)*1000)
    def persist_scene(self,scene):
        try:self.autosave_status=self.scene_store.save(scene)
        except (OSError,ValueError) as exc:
            logging.exception("Artwork autosave failed")
            self.autosave_status=dict(saved=False,error=str(exc))
            self.notice="自动保存失败，请点击保存保留作品。";self.notice_until=time.monotonic()+8.

    def refresh_art(self):
        png=self.png()
        scene=self.make_art_scene(self.art_revision+1)
        self.persist_scene(scene)
        with self.lock:
            self.art=png;self.art_revision+=1;self.art_scene=scene
            self.state.update(art_revision=self.art_revision,event_count=len(self.renderer.events))
    def restore_camera_for_process(self,pid):
        if not pid:return
        if pid!=self.camera_pid:
            self.camera_pid=pid;self.camera_restore_attempts=0;self.camera_restored=False
        if self.camera_restored or self.camera_restore_attempts>=3:return
        self.camera_restore_attempts+=1
        controls=self.camera_settings.restore_saved()
        if controls.get('skipped'):controls=self.camera_settings.read()
        self.camera_restored=bool(controls.get('ok'))
        with self.lock:self.state['camera_controls']=controls
    def refresh_camera_status(self,now):
        # Runs even without frames, so missing/USB2 devices do not look like
        # an unexplained human-tracking failure in the page.
        if now-self.camera_monitor_at<5:return
        self.camera_monitor_at=now;link=read_camera_link();camera={}
        try:camera=json.loads(Path('/run/bodyink-camera/status.json').read_text())
        except (OSError,ValueError):pass
        fresh=now-camera.get('updated_monotonic_seconds',0)<20
        running=fresh and camera.get('state')=='running' and link.get('ready_for_1080p60_yuyv')
        if running:self.restore_camera_for_process(camera.get('pid'))
        with self.lock:
            self.camera_status=camera;self.state['camera_link']=link
            self.state['capture_fps']=camera.get('window_fps_timestamp') if running else None
    def environment_pose(self):
        with self.lock:return self.state.get('points17'),self.state.get('timestamp_seconds')

    def get_environment_frame(self,after):
        return self.environment_capture.preview(after)

    def get_environment_photo(self):
        return self.environment_capture.photograph()

    def command(self,command):
        if not isinstance(command,dict) or command.get('command') not in ('suite','camera','gamepad_stroke','theme','clear','undo','save'):
            return {'accepted':False,'reason':'unknown_command'}
        if command['command']=='suite':
            input_mode=command.get('input_mode','body')
            if input_mode not in ('body','gamepad'):raise ValueError('Invalid input mode')
            with self.paint_lock:
                # Legacy player_session/player_action fields are ignored.
                self.suite.configure(command)
                self.input_mode=input_mode
                with self.lock:self.reset_requested=True
                while True:
                    try:self.paint_queue.get_nowait()
                    except queue.Empty:break
            return {'accepted':True,'suite':self.suite_snapshot()}

        if command['command']=='gamepad_stroke':
            with self.paint_lock:
                return self.gamepad_strokes.enqueue(command,suite=self.suite.snapshot(),
                    input_mode=self.input_mode,theme=self.theme,generation=self.generation,target=self.paint_queue)

        if command['command']=='camera':
            result=self.camera_settings.apply(command.get('exposure_100us'),command.get('gain'),mode=command.get('mode','manual'))
            with self.lock:self.state['camera_controls']=result
            return {'accepted':result['ok'],'message':result['message'],'camera_controls':result}
        with self.paint_lock:
            op=command['command']
            if op=='theme':
                with self.lock:self.theme=command['theme'];self.state['theme']=self.theme
                return {'accepted':True}
            if op in ('clear','undo'):
                with self.lock:self.generation+=1;self.reset_requested=True
                if op=='clear':self.renderer.clear()
                else:self.renderer.undo()
            elif op=='save':
                filename=self.args.output/('ink-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.png')
                saved = self.renderer.save(filename)
                return {'accepted':True,'message':'作品已保存在板卡，并下载到浏览器','path':str(filename),
                        'download_url':'/saved/'+filename.name, 'event_count':saved['event_count']}
            self.refresh_art();return {'accepted':True}

    @staticmethod
    def replace_latest(q,value):
        dropped=False
        try:q.put_nowait(value)
        except queue.Full:
            try:q.get_nowait();dropped=True
            except queue.Empty:pass
            q.put_nowait(value)
        return dropped
    def capture(self):
        last_warning=-float('inf')
        while not self.stop.is_set():
            start=time.monotonic()
            self.refresh_camera_status(start)
            try:
                requested=time.monotonic()
                metadata,raw=capture_snapshot(timeout=2,copy=getattr(self.args,'snapshot_buffer','bytes')!='mmap')
                metadata['_trace']={'request':requested,'received':time.monotonic()}
                target=self.preprocess_queue if self.input_size==(256,256) and getattr(self.args,'preprocess_mode','prefetch')!='latest' else self.latest
                self.latest_dropped+=int(self.replace_latest(target,(metadata,raw)))
            except Exception:
                self.capture_failures+=1
                if start-last_warning>=10:
                    logging.exception('snapshot unavailable; waiting for camera');last_warning=start
                self.stop.wait(.5)
            # At native rate the blocking snapshot service already waits for
            # the next camera frame. A second 1/60 sleep aliases with it.
            if self.args.target_fps<60:
                self.stop.wait(max(0,1/self.args.target_fps-(time.monotonic()-start)))

    def recognition_timestamp(self,metadata):
        """Source PTS for causal ROI decisions; replay harness overrides this."""
        return float(metadata['timestamp_seconds'])

    def frame_rois(self,metadata):
        defaults=self.rois(metadata["width"],metadata["height"])
        if self.body_crop is None:return tuple(defaults)
        stamp=float(metadata["timestamp_seconds"])
        return self.body_crop.snapshot(metadata["width"],metadata["height"],stamp,defaults)

    def wait_prepare_credit(self):
        if not self.limit_prefetch:return not self.stop.is_set()
        while not self.stop.is_set():
            if self.prepare_credit.wait(.05):
                self.prepare_credit.clear()
                return not self.stop.is_set()
        return False

    def take_inference_packet(self,timeout=.5):
        packet=self.latest.get(timeout=timeout)
        # Return the credit even for an unchanged hardware-frame identity.
        # The duplicate check remains in run(), after this ownership transfer.
        self.prepare_credit.set()
        return packet

    def publish_prepared(self,packet):
        if not self.limit_prefetch:
            self.replace_latest(self.latest,packet)
            return True
        # Never discard completed PL work. A defensive bounded wait also
        # handles shutdown if a consumer fails before taking the packet.
        while not self.stop.is_set():
            try:self.latest.put(packet,timeout=.05);return True
            except queue.Full:pass
        return False

    def preprocess_worker(self):
        use_pl=getattr(self.args,'preprocess_backend','cpu')=='pl'
        if use_pl:
            bridge = ROOT / 'pl_bridge'
            if not (bridge / 'pl_preprocess.py').is_file():
                bridge = ROOT / 'buffer-bridge'
            sys.path.insert(0, str(bridge))
            from pl_adapter import PLAdapter
            pre=PLAdapter()
        else:pre=YuyvPreprocessor()
        try:
            while not self.stop.is_set():
                if not self.wait_prepare_credit():break
                try:metadata,raw=self.preprocess_queue.get(timeout=.5)
                except queue.Empty:
                    # Camera downtime must not consume the sole credit.
                    self.prepare_credit.set()
                    continue
                if getattr(self.args,'preprocess_mode','prefetch')=='timed':
                    if self.stop.wait(min(.08,max(0.,self.next_prepare_at-time.monotonic()))):break
                    # Keep metadata inseparable from the newest frame's pixels.
                    try:metadata,raw=self.preprocess_queue.get_nowait()
                    except queue.Empty:pass
                before=time.monotonic()
                metadata['_frame_rois']=self.frame_rois(metadata)
                roi=metadata['_frame_rois'][0]
                rgb=pre.resize(raw,metadata['width'],metadata['height'],metadata['stride'],size=self.input_size,roi=roi)
                finished=time.monotonic()
                metadata.setdefault('_trace',{}).update(pre_start=before,pre_end=finished)
                if use_pl:metadata['_pl_timing']=dict(pre.last_timing)
                prepared=(rgb,(finished-before)*1000)
                if use_pl:
                    # One owner prepares both inputs; the main thread still
                    # runs the second DPU job only under the unchanged quality
                    # fallback policy. Tensors own their memory across DMA reuse.
                    secondary_result=Future()
                    prepared=(rgb,prepared[1],secondary_result)
                    if not self.publish_prepared((metadata,raw,prepared)):
                        secondary_result.cancel()
                        break
                    try:
                        secondary_start=time.monotonic()
                        secondary=pre.resize(raw,metadata['width'],metadata['height'],metadata['stride'],
                                             size=self.input_size,roi=metadata['_frame_rois'][1])
                        secondary_ms=(time.monotonic()-secondary_start)*1000
                        secondary_result.set_result((secondary,secondary_ms,dict(pre.last_timing)))
                    except BaseException as exc:
                        secondary_result.set_exception(exc)
                        raise
                    continue
                if not self.publish_prepared((metadata,raw,prepared)):break
        finally:
            if use_pl:
                if pre.pp.active_buffers is not None:
                    # Stop consumers before waiting for the hardware's terminal status.
                    self.stop.set()
                pre.close()

    def preview_worker(self):
        pre=YuyvPreprocessor()
        width=getattr(self.args,'preview_width',384);height=width*9//16
        while not self.stop.is_set():
            try:metadata,raw,points,index=self.preview_queue.get(timeout=.5)
            except queue.Empty:continue
            mode=self.suite.snapshot()
            if mode['active'] and (mode['menu'] or mode['mode']!='ink') and not mode.get('preview'):continue
            rgb=pre.resize(raw,metadata['width'],metadata['height'],metadata['stride'],(width,height))
            im=Image.fromarray(rgb);draw=ImageDraw.Draw(im)
            rect=metadata['_frame_rois'][index]
            rx,ry,rw,rh=rect
            sx=width/metadata['width'];sy=height/metadata['height']
            draw.rectangle((rx*sx,ry*sy,(rx+rw)*sx,(ry+rh)*sy),outline=(154,158,120),width=1)
            draw_pose(draw,points,self.overlay_thresholds,EDGES,width,height)
            if self.raw_preview:
                rgb_frame=(im.tobytes(),width,height);jpeg=b''
            else:
                output=io.BytesIO();im.save(output,format='JPEG',quality=80)
                jpeg=output.getvalue();rgb_frame=None
            with self.preview_changed:
                self.preview=jpeg;self.preview_rgb=rgb_frame;self.preview_stamp=float(metadata['timestamp_seconds']);self.preview_version+=1
                self.preview_changed.notify_all()

    def paint_worker(self):
        while not self.stop.is_set():
            try:event,theme,generation,control_epoch=self.paint_queue.get(timeout=.5)
            except queue.Empty:continue
            # Preserve normal action evidence during an unconfirmed circle.
            # A failed candidate releases this event; opening the menu invalidates
            # its epoch. Never hold paint_lock while waiting for the decision.
            while self.suite.paint_deferred(control_epoch) and not self.stop.is_set():
                self.stop.wait(.025)
            if self.stop.is_set():break
            with self.paint_lock:
                if generation!=self.generation or not self.paint_event_allowed(event,control_epoch):continue
                paint_started=time.monotonic()
                result=(apply_controller_stroke(self.renderer,event,theme) if event.get('origin')=='gamepad' else self.renderer.add(event,theme))
                paint_finished=time.monotonic()
                if result.get('accepted'):self.refresh_art()
                art_finished=time.monotonic()
                render_timing=dict(queue_ms=(paint_started-event.get('render_queued_monotonic',paint_started))*1000,paint_ms=(paint_finished-paint_started)*1000,png_commit_ms=(art_finished-paint_finished)*1000,total_ms=(art_finished-event.get('render_queued_monotonic',paint_started))*1000)
                result['timing']=render_timing
                with self.lock:self.state['last_paint_timing']=render_timing
                with self.lock:
                    self.notice=result.get('reason') or result.get('message') or ('已完成一次落墨。' if result.get('accepted') else '本次动作未落墨。')
                    self.notice_until=time.monotonic()+4
                with self.log_path.open('a',encoding='utf-8') as f:
                    f.write(json.dumps({'event':event,'theme':theme,'render':result},ensure_ascii=False,allow_nan=False)+'\n')
                logging.info('ink_event action=%s accepted=%s',event['action'],result.get('accepted'))

    def worker(self,target):
        try:target()
        except Exception as exc:
            logging.exception('worker stopped: %s',target.__name__)
            self.failure=exc;self.stop.set()

    @staticmethod
    def stage_times(metadata,started,finished):
        t=metadata.get('_trace',{});stamp=metadata['timestamp_seconds']
        pairs={'source_to_received_ms':(stamp,t.get('received')),
               'snapshot_request_ms':(t.get('request'),t.get('received')),
               'pre_queue_ms':(t.get('received'),t.get('pre_start')),
               'primary_preprocess_ms':(t.get('pre_start'),t.get('pre_end')),
               'inference_queue_ms':(t.get('pre_end'),started),
               'inference_and_action_ms':(started,finished)}
        result={k:(b-a)*1000 for k,(a,b) in pairs.items() if a is not None and b is not None}
        ipc=metadata.get('_snapshot_trace',{})
        if ipc:
            result.update(source_to_socket_ms=(ipc['socket_received']-stamp)*1000,
                          socket_validation_ms=(ipc['read_started']-ipc['socket_received'])*1000,
                          frame_read_ms=(ipc['read_finished']-ipc['read_started'])*1000)
        return result

    @staticmethod
    def advance_preview_deadline(deadline,now,fps):
        period=1./fps
        return now+period if not deadline else deadline+(int(max(0.,now-deadline)/period)+1)*period

    def run(self):
        workers=[]
        self.preprocess_queue=queue.Queue(maxsize=1)
        targets=[self.capture,self.preview_worker,self.paint_worker]
        if self.input_size==(256,256) and getattr(self.args,'preprocess_mode','prefetch')!='latest':targets.append(self.preprocess_worker)
        for target in targets:
            owns_dma = target == self.preprocess_worker and getattr(self.args, 'preprocess_backend', 'cpu') == 'pl'
            worker = threading.Thread(target=self.worker, args=(target,), daemon=not owns_dma)
            worker.start()
            workers.append(worker)
        next_preview=last_report=0.;camera={};last_frame=None
        try:
            self.viewer.start()
            while not self.stop.is_set():
                try:packet=self.take_inference_packet(timeout=.5)
                except queue.Empty:
                    self.drain_completed_action();continue
                metadata,raw=packet[:2];prepared=packet[2] if len(packet)>2 else None
                # Snapshot requests can outrun the camera. Count only distinct
                # hardware frames; sequence may wrap/restart, timestamp disambiguates.
                frame_identity=(metadata['sequence'],metadata['timestamp_seconds'])
                if frame_identity==last_frame:
                    self.drain_completed_action();continue
                last_frame=frame_identity
                with self.lock:
                    if self.reset_requested:
                        self.reset_action_state();self.reset_requested=False
                    generation=self.generation;theme=self.theme
                started=time.monotonic();poses=[];parts=[];w=metadata['width'];h=metadata['height']
                if '_frame_rois' not in metadata:metadata['_frame_rois']=self.frame_rois(metadata)
                rois=metadata['_frame_rois']
                if self.input_size==(256,256):
                    provider=lambda i,roi:prepared[0] if i==0 and prepared is not None else (
                        (prepared[2].result(timeout=3)[0] if i==1 and prepared is not None and len(prepared)>2 else
                         self.pre.resize(raw,w,h,metadata['stride'],size=self.input_size,roi=roi)))
                    if self.roi_policy is None:
                        points,index,poses,parts,selection=infer_hourglass(self.net,provider,rois,w,h)
                    else:
                        points,index,poses,parts,selection=infer_continuous(self.net,provider,rois,w,h,
                            self.roi_policy,self.recognition_timestamp(metadata))
                    if prepared is not None:
                        for position,part in enumerate(parts):
                            roi_index=part.get('roi_index',position)
                            if roi_index==0:
                                part['preprocess_worker_ms']=prepared[1]
                                if '_pl_timing' in metadata:part['pl_preprocess']=metadata['_pl_timing']
                            elif len(prepared)>2:
                                secondary=prepared[2].result(timeout=3)
                                part['preprocess_worker_ms']=secondary[1]
                                part['pl_preprocess']=secondary[2]
                    scores=[float(np.min(p[5:13,2])) for p in poses]
                else:
                    for roi in rois:
                        rgb=self.pre.resize(raw,w,h,metadata['stride'],size=self.input_size,roi=roi)
                        p,t=self.net.predict(rgb);poses.append(p);parts.append(t)
                    points,index,scores=self.selector.select(poses,rois,w,h);selection={}
                if self.body_crop is not None:
                    self.body_crop.observe(points,float(metadata["timestamp_seconds"]),w,h)
                pose_finished=time.monotonic()
                self.last_selection=selection
                stamp=float(metadata['timestamp_seconds'])
                temporal=self.action_roi_continuity.observe(points,stamp,w,h,
                    discontinuity=bool(selection.get('motion_history_discontinuity')),
                    stream_reset=bool(selection.get('stream_reset')))
                if selection.get('motion_history_discontinuity'):
                    selection['temporal_a02_continuity']=temporal
                    self.action_reset_count+=1
                    reset_record=dict(timestamp_seconds=stamp,sequence=metadata["sequence"],phase_before=self.action_snapshot()["phase"],reason="roi_motion_history_discontinuity",selected_roi=index,continuity=dict(selection.get("continuity",{})),temporal_a02_continuity=temporal)
                    self.action_resets.append(reset_record)
                    logging.info("action_reset %s",json.dumps(reset_record,allow_nan=False))
                    # Stream/generation changes still reset every hypothesis.
                    # Only measured same-frame per-arm agreement can retain A02.
                    retain=() if selection.get('stream_reset') else selection.get('continuity',{}).get('preserve_a02_sides',())
                    retain=sorted(set(retain)|set(temporal['normal_sides']))
                    optional=temporal['optional_sides']
                    self.reset_action_state(preserve_a02_sides=retain,preserve_a02_optional_sides=optional)
                suite=self.suite.observe(points,stamp,w,h) if self.input_mode=='body' else self.suite.snapshot()
                if suite['epoch']!=getattr(self,'last_suite_epoch',-1):
                    self.reset_action_state();self.last_suite_epoch=suite['epoch']
                event_context=dict(theme=theme,generation=generation,suite_epoch=suite['epoch'],timestamp=stamp)
                action_compute_ms=None
                if self.body_ink_allowed():
                    if self.action_process is None:
                        result=self.engine.update(points,stamp,w,h)
                    else:
                        completed=self.action_process.finish()
                        self.action_process.submit(points,stamp,w,h,event_context)
                        if completed is None:
                            result=dict(phase='idle',quality={'valid':False},event=None,reason='awaiting_observation',engine='flow_process_warming_up')
                        else:
                            result=completed['result'];event_context=completed['metadata']
                            action_compute_ms=completed['cpu_ms']
                else:
                    result=dict(phase='idle',quality={'valid':True},event=None,reason='suite_interaction_paused',engine='suite_shared_pose')
                now=time.monotonic();self.last_progress=now;self.pose_count+=1;self.frames.append(now)
                self.next_prepare_at=now+max(0.,now-started-.020)
                timing={'inference_pipeline_ms':(now-started)*1000,'latency_ms':(now-stamp)*1000,'roi_timing':parts,
                        'pose_selection_ms':(pose_finished-started)*1000,'action_update_ms':(now-pose_finished)*1000}
                self.timings.append(timing)
                if result.get('event') and self.body_ink_allowed(event_context['suite_epoch']):
                    event=result['event'];event['calibration']=event['speed_calibration']
                    event['render_queued_monotonic']=time.monotonic()
                    try:self.paint_queue.put_nowait((event,event_context['theme'],event_context['generation'],event_context['suite_epoch']))
                    except queue.Full:logging.error('renderer queue full; action not drawn')
                if now>=next_preview:
                    self.replace_latest(self.preview_queue,(metadata,raw,points,index))
                    next_preview=self.advance_preview_deadline(next_preview,now,self.args.preview_fps)
                quality=result['quality'];reason=result.get('reason','')
                core=points[5:13]
                visible=(np.isfinite(core).all(axis=1) & (core[:,:2]>=0).all(axis=1)
                         & (core[:,:2]<=1).all(axis=1)
                         & (core[:,2]>=self.overlay_thresholds[5:13]))
                joint_names=('左肩','右肩','左肘','右肘','左腕','右腕','左髋','右髋')
                visibility=dict(visible_core_count=int(visible.sum()),required_core_count=8,
                    missing_core_joints=[joint_names[i] for i in range(8) if not visible[i]])
                with self.lock,self.suite.lock:
                    action_context_valid=(event_context['generation']==self.generation and
                        event_context['suite_epoch']==self.suite.epoch and
                        (action_compute_ms is None or self.body_ink_allowed(event_context['suite_epoch'])))
                    self.state.update(points17=points.tolist(),
                        quality='人体关键点清晰' if visible.all() else '关键关节不完整（%d/8）'%visible.sum(),
                        skeleton_visibility=visibility,
                        roi_index=index,roi_scores=scores,
                        roi_selection=selection,frame_rois=rois,body_crop_enabled=self.body_crop is not None,action_reset_count=self.action_reset_count,action_resets=list(self.action_resets),
                        roi_timing=parts,stage_times=self.stage_times(metadata,started,now),snapshot_buffer=metadata.get('buffer_mode','bytes'),
                        pose_fps=(len(self.frames)-1)/(self.frames[-1]-self.frames[0]) if len(self.frames)>1 and self.frames[-1]>self.frames[0] else None,
                        latency_ms=timing['latency_ms'],pipeline_ms=timing['inference_pipeline_ms'],
                        pose_selection_ms=timing['pose_selection_ms'],action_update_ms=timing['action_update_ms'],
                        pose_count=self.pose_count,uptime_seconds=now-self.started,
                        capture_failures=self.capture_failures,latest_dropped=self.latest_dropped,
                        timestamp_seconds=stamp,sequence=metadata['sequence'])
                    if action_context_valid:
                        self.state.update(phase=result['phase'],quality_detail=quality,
                            quality_detail_timestamp_seconds=event_context['timestamp'],reason=reason,
                            preparation_policy=result.get('engine','unknown'),
                            prepared_actions=self.action_snapshot()['prepared_actions'],
                            action_compute_ms=action_compute_ms,
                            action_timestamp_seconds=event_context['timestamp'] if action_compute_ms is not None else stamp,
                            action_result_age_ms=(now-event_context['timestamp'])*1000 if action_compute_ms is not None else 0.)
                        self.state['message']=('菜单已打开，请移动手腕选择功能。' if suite['menu'] else '功能切换手势识别中…' if suite['control_active'] else '共享人体识别正在运行。') if reason=='suite_interaction_paused' else MESSAGES.get(reason,'请回到自然站立，再准备动作。')
                        if reason in ('prepared_start_held','waiting_for_directed_motion') and self.state['prepared_actions']:
                            self.state['message']='动作候选：'+' / '.join(self.state['prepared_actions'])+'；自然完成动作，无需停顿。'
                        if reason=='body_keypoints_not_reliable' and visibility['missing_core_joints']:
                            self.state['message']='暂不能落墨：'+ '、'.join(visibility['missing_core_joints']) +'不可靠，请保持手臂完整入镜。'
                        elif reason=='return_to_neutral_before_next_action' and self.action_snapshot()['last_rejection'] in ('tracking_lost_cycle_cancelled','execution_tracking_gap','sample_stream_interrupted'):
                            self.state['message']='本次动作因追踪中断已取消，尚未落墨；请放下双手后重新准备。'
                        if self.engine.readiness_errors:self.state['message']='人体识别已运行，动作模板正在准备。'
                if now-last_report>=5:
                    with self.lock:camera=dict(self.camera_status)
                    report={'runtime':self.get_state(),'camera':camera,'model':self.net.info}
                    tmp=ROOT/'status.json.tmp';tmp.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False));tmp.replace(ROOT/'status.json')
                    logging.info('pose_count=%d pose_fps=%.2f pipeline_ms=%.2f capture_fps=%s',self.pose_count,self.state['pose_fps'] or 0,timing['inference_pipeline_ms'],self.state['capture_fps'])
                    last_report=now
        finally:
            self.stop.set();self.viewer.stop()
            if self.action_process is not None:self.action_process.close()
            for worker in workers:
                worker.join(timeout=3 if worker.daemon else None)
        if self.failure:raise RuntimeError('A required runtime worker failed') from self.failure

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',choices=('127.0.0.1','localhost'),default='127.0.0.1');p.add_argument('--port',type=int,default=8088)
    p.add_argument('--backend',choices=['hourglass','movenet'],default='hourglass')
    p.add_argument('--enabled-actions',nargs='+',choices=ACTIONS,help='Explicit preview action subset; omitted requires all six classes')
    p.add_argument('--target-fps',type=float,default=60);p.add_argument('--preview-fps',type=float,default=30)
    p.add_argument('--preview-width',type=int,choices=(384,640),default=384)
    p.add_argument('--preprocess-mode',choices=('prefetch','latest','timed'),default='prefetch',help='Candidate scheduling modes; benchmark before selecting')
    p.add_argument('--prefetch-credit',choices=('bounded','unlimited'),default='bounded',help='Bound speculative preprocessing to one unconsumed packet; unlimited is production scheduling for A/B')
    p.add_argument('--preprocess-backend',choices=('cpu','pl'),default='cpu',help='PL prepares primary and fallback ROIs using the BodyInk DMA firmware')
    p.add_argument('--snapshot-buffer',choices=('bytes','mmap'),default='bytes',help='Use an owned read-only sealed frame mapping to avoid a full-frame client copy')
    p.add_argument('--templates',type=Path,default=ROOT/'dataset/templates.npz')
    p.add_argument('--calibration',type=Path,default=ROOT/'dataset/speed_calibration.json')
    p.add_argument('--output',type=Path,default=ROOT/'works');args=p.parse_args()
    if not 5<=args.target_fps<=60 or not 1<=args.preview_fps<=60:p.error('Unsupported frame rate')
    if args.preprocess_backend=='pl' and (args.backend!='hourglass' or args.preprocess_mode=='latest'):
        p.error('PL preprocessing requires Hourglass and a prefetch/timed worker')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    app=LiveApp(args)
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:app.stop.set())
    app.run()

if __name__=='__main__':main()
