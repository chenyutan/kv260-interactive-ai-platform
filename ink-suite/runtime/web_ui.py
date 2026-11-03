"""Small standard-library HTTP viewer for the KV260 body ink runtime."""
from __future__ import annotations

import json
import struct
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs


class ViewerServer:
    def __init__(self, host, port, get_state, get_art_png, get_preview_jpeg, on_command, get_saved_png=None, get_preview_frame=None, get_preview_rgb_frame=None, get_art_scene=None,get_environment_frame=None,get_environment_photo=None):
        if host not in ('localhost', '127.0.0.1'):
            raise ValueError('The camera and control API requires a loopback address')
        self.get_state = get_state
        self.get_art_png = get_art_png
        self.get_preview_jpeg = get_preview_jpeg
        self.on_command = on_command
        self._thread = None
        self.stopping = threading.Event()
        static = Path(__file__).with_name("static")
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send(self, status, data, mime="application/json; charset=utf-8", headers=None):
                if not isinstance(data, bytes):
                    data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for key,value in (headers or {}).items():self.send_header(key,str(value))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def local_request(self):
                # Check HTTP authority as well as the listener to prevent DNS rebinding.
                host = self.headers.get("Host", "")
                port = self.server.server_port
                allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
                if port == 80:
                    allowed.update(("localhost", "127.0.0.1"))
                origin = self.headers.get("Origin")
                return host in allowed and (origin is None or origin == "http://" + host)

            def do_GET(self):
                if not self.local_request():
                    return self.send(403, {"message": "Local application only"})
                path = urlparse(self.path).path
                try:
                    if path in assets:
                        filename, mime = assets[path]
                        return self.send(200, (static/filename).read_bytes(), mime)
                    if path == "/api/art-scene" and get_art_scene:
                        return self.send(200,get_art_scene())
                    if path == "/environment.rgb" and get_environment_frame:
                        if self.client_address[0] not in ('127.0.0.1','::1'):return self.send(403,{'message':'Local application only'})
                        try:after=int(parse_qs(urlparse(self.path).query).get('after',['-1'])[0])
                        except (ValueError,IndexError):return self.send(400,{'message':'Invalid frame version'})
                        frame=get_environment_frame(after)
                        if frame is None:return self.send(204,b'',"application/octet-stream")
                        data,version,age,width,height,stride,points,pose_age=frame
                        return self.send(200,data,"application/octet-stream",{'X-Preview-Version':version,'X-Frame-Age-Ms':round(age,2),'X-Preview-Width':width,'X-Preview-Height':height,'X-Preview-Stride':stride,'X-Pose-Age-Ms':round(pose_age,2),'X-Points17':json.dumps(points,separators=(',',':'),allow_nan=False)})
                    if path == "/api/state":
                        return self.send(200, viewer.get_state())
                    if path == "/api/stream-metadata":
                        # Recovery checks need capture/inference progression only.
                        # Never send image, pose, owner identity or appearance data.
                        state=viewer.get_state()
                        result={key:state[key] for key in ('pid','phase','sequence','timestamp_seconds','pose_count') if key in state}
                        link=state.get('camera_link')
                        if isinstance(link,dict):
                            result['camera_link']={key:link[key] for key in ('present','ready_for_1080p60_yuyv') if key in link}
                        return self.send(200,result)
                    if path == "/preview.mjpg" and get_preview_frame:
                        self.connection.settimeout(2.)
                        self.send_response(200)
                        self.send_header('Content-Type','multipart/x-mixed-replace; boundary=bodyink')
                        self.send_header('Cache-Control','no-store')
                        self.end_headers()
                        version=-1
                        try:
                            while not viewer.stopping.is_set():
                                frame=get_preview_frame(version)
                                if frame is None:continue
                                data,version,age=frame
                                header=('--bodyink\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Preview-Version: %d\r\n\r\n'%(len(data),version)).encode('ascii')
                                self.wfile.write(header+data+b'\r\n');self.wfile.flush()
                        except (OSError,TimeoutError):pass
                        return
                    if path == "/art.png":
                        return self.send(200, viewer.get_art_png(), "image/png")
                    if re.fullmatch(r"/saved/ink-\d{8}-\d{6}-\d{6}\.png",path) and get_saved_png:
                        try:return self.send(200,get_saved_png(path.rsplit('/',1)[-1]),"image/png")
                        except FileNotFoundError:return self.send(404,{"message":"保存的作品不存在"})
                    if path == "/preview.rgbstream" and get_preview_rgb_frame:
                        self.connection.settimeout(2.)
                        self.send_response(200)
                        self.send_header('Content-Type','application/octet-stream')
                        self.send_header('Cache-Control','no-store')
                        self.end_headers()
                        version=-1
                        try:
                            while not viewer.stopping.is_set():
                                frame=get_preview_rgb_frame(version)
                                if frame is None:
                                    viewer.stopping.wait(.03);continue
                                data,version,age,width,height,stride=frame
                                header=struct.pack('!4sIIIQd',b'BRGB',width,height,len(data),version,age)
                                self.wfile.write(header);self.wfile.write(data);self.wfile.flush()
                        except (OSError,TimeoutError):pass
                        return
                    if path == "/preview.rgb" and get_preview_rgb_frame:
                        query=parse_qs(urlparse(self.path).query)
                        try:after=int(query.get('after',['-1'])[0])
                        except (ValueError,IndexError):return self.send(400,{"message":"Invalid preview version"})
                        frame=get_preview_rgb_frame(after)
                        if frame is None:return self.send(204,b'',"application/octet-stream")
                        data,version,age,width,height,stride=frame
                        return self.send(200,data,"application/octet-stream",{'X-Preview-Version':version,'X-Frame-Age-Ms':round(age,2),'X-Preview-Width':width,'X-Preview-Height':height,'X-Preview-Stride':stride})
                    if path == "/preview.jpg":
                        query=parse_qs(urlparse(self.path).query)
                        if get_preview_frame and 'after' in query:
                            try:after=int(query['after'][0])
                            except (ValueError,IndexError):return self.send(400,{"message":"Invalid preview version"})
                            frame=get_preview_frame(after)
                            if frame is None:return self.send(204,b'',"image/jpeg")
                            data,version,age=frame
                            return self.send(200,data,"image/jpeg",{'X-Preview-Version':version,'X-Frame-Age-Ms':round(age,2)})
                        data = viewer.get_preview_jpeg()
                        return self.send(200, data, "image/jpeg") if data else self.send(503, {"message": "等待摄像头画面"})
                    self.send(404, {"message": "页面不存在"})
                except Exception as exc:
                    self.send(503, {"message": "运行数据暂不可用", "detail": str(exc)})

            def do_POST(self):
                if not self.local_request():
                    return self.send(403, {'message': 'Local application only'})
                if urlparse(self.path).path == '/environment/photo.rgb' and get_environment_photo:
                    if self.client_address[0] not in ('127.0.0.1','::1'):return self.send(403,{'message':'Local application only'})
                    try:
                        length=int(self.headers.get('Content-Length','0'))
                        if not 0<length<=128:return self.send(400,{'message':'Invalid shutter request'})
                        if json.loads(self.rfile.read(length))!={}:return self.send(400,{'message':'Expected empty shutter request'})
                        data,version,age,width,height,stride,points,pose_age,stamp,sequence=get_environment_photo()
                        return self.send(200,data,'application/octet-stream',{'X-Preview-Version':version,'X-Frame-Age-Ms':round(age,2),'X-Preview-Width':width,'X-Preview-Height':height,'X-Preview-Stride':stride,'X-Pose-Age-Ms':round(pose_age,2),'X-Points17':json.dumps(points,separators=(',',':'),allow_nan=False),'X-Capture-Monotonic':stamp,'X-Capture-Sequence':sequence})
                    except (ValueError,UnicodeError):return self.send(400,{'message':'Invalid shutter request'})
                    except Exception as exc:return self.send(503,{'message':'Photo unavailable; please retry','detail':str(exc)})

                if urlparse(self.path).path != "/api/command":
                    return self.send(404, {"message": "接口不存在"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 4096:
                        return self.send(400, {"message": "命令长度无效"})
                    command = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(command, dict) or command.get("command") not in ("theme", "undo", "clear", "save", "camera", "suite", "gamepad_stroke"):
                        return self.send(400, {"message": "未知操作"})
                    if command["command"] == "theme" and command.get("theme") not in ("landscape", "bamboo", "plum", "mixed"):
                        return self.send(400, {"message": "未知主题"})
                    if command['command'] in ('suite','gamepad_stroke') and self.client_address[0] not in ('127.0.0.1','::1'):
                        return self.send(403,{'message':'Local application only'})
                    if command['command']=='gamepad_stroke':
                        from gamepad_stroke import validate_request
                        validate_request(command)
                    result = viewer.on_command(command)
                    self.send(200, result if result is not None else {"accepted": True})
                except (ValueError, UnicodeError) as exc:
                    self.send(400, {"message": "命令格式无效", "detail": str(exc)})
                except Exception as exc:
                    self.send(503, {"message": "操作未完成", "detail": str(exc)})

        self._server = ThreadingHTTPServer((host, int(port)), Handler)
        self._server.daemon_threads = True

    @property
    def address(self):
        return self._server.server_address

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            name="bodyink-viewer", daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self.stopping.set()
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._server.server_close()
