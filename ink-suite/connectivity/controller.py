"""Bounded background device operations. Never restart a healthy active service."""
from pathlib import Path
import json
import queue
import subprocess
import threading
import time
from urllib.request import build_opener,ProxyHandler

from .wifi import WifiManager
from .audio_device import AudioKeepalive
from startup_readiness import PipelineReadiness


def camera_inventory(video_root='/sys/class/video4linux',usb_root='/sys/bus/usb/devices'):
    """Physical camera presence, independent of a resetting UVC video node.

    A live USB video node establishes presence for generic webcams. During a
    driver reset, USB Video interfaces or the verified Camera3 identity can
    establish presence even while those nodes are absent. This is never an
    inference/capture readiness result; the host's frame gate remains separate.
    No video device is opened and no image or pose is read.
    """
    video_root,usb_root=Path(video_root),Path(usb_root)
    nodes=[];devices={}
    for p in video_root.glob('video*'):
        try:
            device=next((a for a in (p/'device').resolve(strict=True).parents
                         if (a/'idVendor').is_file()),None)
            if device is not None:
                nodes.append('/dev/'+p.name)
                devices[str(device.resolve())]=device
        except OSError:
            pass
    for device in usb_root.glob('*'):
        try:
            if not (device/'idVendor').is_file():continue
            vendor=(device/'idVendor').read_text().strip().lower()
            product_id=(device/'idProduct').read_text().strip().lower()
            product=(device/'product').read_text().strip() if (device/'product').is_file() else ''
            known=(vendor,product_id,product)==('0bda','5858','USB Camera3')
            if known:
                devices[str(device.resolve())]=device
                continue
            interfaces=list(usb_root.glob(device.name+':*'))+list(device.glob(device.name+':*'))
            for interface in interfaces:
                try:
                    if (interface/'bInterfaceClass').read_text().strip().lower()=='0e':
                        devices[str(device.resolve())]=device
                        break
                except OSError:
                    continue  # One disappearing interface is not proof of unplug.
        except OSError:
            continue
    connected=bool(devices)
    return dict(connected=connected,name='摄像头',nodes=sorted(nodes),
                video_nodes_available=bool(nodes),recovering=connected and not nodes,
                usb_devices=sorted(devices),
                detail=('已连接，等待识别服务就绪' if nodes else
                        '摄像头已连接，采集通道正在恢复' if connected else 'USB 摄像头未连接'))


def stream_metadata(timeout=1.):
    """Read only the counter-only local API; never open a frame or pose API."""
    opener=build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:8088/api/stream-metadata',timeout=timeout) as response:
        data=response.read(4097)
    if len(data)>4096:raise ValueError('Stream metadata exceeds limit')
    value=json.loads(data)
    if not isinstance(value,dict):raise ValueError('Invalid stream metadata')
    # Keep the consumer allowlist even if a future backend adds other fields.
    result={key:value[key] for key in ('pid','phase','sequence','timestamp_seconds','pose_count') if key in value}
    link=value.get('camera_link')
    if isinstance(link,dict):result['camera_link']={key:link[key] for key in ('present','ready_for_1080p60_yuyv') if key in link}
    return result


def reconnect_camera(runner=subprocess.run,metadata=stream_metadata,timeout=8.,
                     monotonic=time.monotonic,sleep=time.sleep):
    """Repair only inactive services, then prove a fresh advancing pipeline.

    Called by the existing action worker, never by the GTK thread. An active
    service is not evidence of usable capture or completed inference.
    """
    units=('bodyink-camera.service','bodyink-live.service')
    repaired=[]
    for unit in units:
        status=runner(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5)
        if status.returncode==0:continue
        if str(getattr(status,'stdout','')).strip() in ('activating','reloading','deactivating'):
            continue  # Let an in-flight service transition finish within the check window.
        result=runner(['sudo','-n','systemctl','restart',unit],capture_output=True,text=True,timeout=20)
        if result.returncode!=0:
            return dict(ok=False,restarted_services=repaired,message=('摄像头采集' if unit==units[0] else '人体识别')+'服务恢复失败，请检查连接后重试。')
        repaired.append(unit)
    gate=PipelineReadiness()
    deadline=monotonic()+max(0.,min(float(timeout),15.))
    while monotonic()<deadline:
        try:state=metadata(timeout=min(1.,max(.01,deadline-monotonic())))
        except (OSError,ValueError,TimeoutError):state=None
        # Production recovery always requires inference counts. The generic
        # startup gate also supports old synthetic APIs without this field.
        link=state.get('camera_link') if isinstance(state,dict) else None
        if (not isinstance(state,dict) or PipelineReadiness._counter(state.get('pose_count')) is None
                or not isinstance(link,dict) or link.get('present') is not True
                or link.get('ready_for_1080p60_yuyv') is not True):
            state=None
        if gate.update(state,monotonic()):
            active=all(runner(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5).returncode==0 for unit in units)
            if active:
                return dict(ok=True,restarted_services=repaired,message='摄像头采集与人体识别已恢复。')
            return dict(ok=False,restarted_services=repaired,message='采集或识别服务再次中断，请检查连接后重试。')
        remaining=deadline-monotonic()
        if remaining>0:sleep(min(.15,remaining))
    return dict(ok=False,pending=True,restarted_services=repaired,reason=gate.reason,
                message='采集或识别计数尚未连续更新，请检查 USB 连接后重试；当前尚未确认恢复。')


class ConnectivityController:
    """One Wi-Fi worker and an independent audio keeper; UI callbacks are injected."""
    def __init__(self,publish,result,dispatch, wifi=None,audio=None,camera=camera_inventory,period=3.):
        self.wifi=wifi or WifiManager();self.audio=audio or AudioKeepalive()
        self.publish=publish;self.result=result;self.dispatch=dispatch;self.camera=camera
        self.period=period;self.stop=threading.Event();self.requests=queue.Queue(maxsize=4)
        self.lock=threading.Lock();self.pending=set();self.serial=0;self.thread=None
        self.snapshot={};self.networks=[]

    def start(self):
        if self.thread is not None or self.stop.is_set():return
        self.audio.start();self.thread=threading.Thread(target=self._work,name='device-manager',daemon=True);self.thread.start()

    def request(self,operation,**data):
        if self.stop.is_set():return None
        # No simultaneous radio mutations, no queued repeated Connect clicks.
        with self.lock:
            if self.pending:return None
            self.serial+=1;token=self.serial;self.pending.add(token)
        try:self.requests.put_nowait((token,operation,data))
        except queue.Full:
            with self.lock:self.pending.discard(token)
            return None
        return token

    def _emit(self,fn,*args):
        if not self.stop.is_set():self.dispatch(fn,*args)

    def _inventory(self):
        try:w=self.wifi.inventory()
        except Exception as exc:w=dict(ok=False,available=False,error=str(exc))
        try:a=self.audio.status()
        except Exception as exc:a=dict(inventory={},error=str(exc))
        inv=a.get('inventory') or {}
        devices=inv.get('devices') or []
        cubes=[d for d in devices if d.get('is_cube') and d.get('playable',d.get('connected',False))]
        active=w.get('active') or {};active=active[0] if isinstance(active,list) and active else active
        try:camera=self.camera()
        except Exception:camera=dict(connected=False,name='摄像头',detail='无法读取摄像头状态，请重试')
        self.snapshot=dict(camera=camera,wifi=dict(connected=bool(w.get('available') and active),name='Wi‑Fi 网络',
            detail=('已连接 '+str(active.get('ssid','')) if active else
                    ('尚未连接网络，离线体验仍可使用' if w.get('available') else 'USB 无线网卡未连接或驱动未就绪')),
            inventory=w),audio=dict(connected=bool(cubes),name='音响',
            detail=('音响已连接 · '+('自动重连监测中' if a.get('state')=='active' else '正在准备声音输出')
                    if cubes else '请检查接收器并开启音响，白灯常亮后自动恢复'),inventory=inv,keeper=a))
        self._emit(self.publish,self.snapshot)

    def _work(self):
        due=0.
        while not self.stop.is_set():
            try:task=self.requests.get(timeout=max(.05,min(.25,due-time.monotonic())))
            except queue.Empty:task=None
            if self.stop.is_set():break
            if task:
                token,op,data=task
                try:
                    if op=='scan':out=self.wifi.scan()
                    elif op=='connect':out=self.wifi.connect(data['network'],data.get('password',''))
                    elif op=='wifi_retry':out=self.wifi.retry()
                    elif op=='audio_retry':self.audio.retry();out=dict(ok=True,message='正在重新连接音响')
                    elif op=='camera_retry':out=reconnect_camera()
                    else:raise ValueError('Unknown device operation')
                    if op in ('scan','wifi_retry') and out.get('networks') is not None:self.networks=out['networks']
                except Exception as exc:out=dict(ok=False,message='操作未完成：'+str(exc)[:160])
                finally:
                    with self.lock:self.pending.discard(token)
                self._emit(self.result,token,op,out)
                due=0.
            if time.monotonic()>=due:
                self._inventory();due=time.monotonic()+self.period

    def close(self):
        self.stop.set();self.audio.close()
        # Do not join a pending NetworkManager operation on the GTK thread.
