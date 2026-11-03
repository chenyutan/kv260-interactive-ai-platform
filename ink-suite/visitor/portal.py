"""Same-Wi-Fi private visitor portal; phone clients cannot submit scores/end play."""
import io
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie,CookieError
from urllib.parse import urlsplit
from .store import TOKEN
from .accounts import Accounts,AccountError,COOKIE,MAX_AGE

STATIC=Path(__file__).with_name('static')
LAN_RANGES=tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))

def lan_address(value):
    address=ipaddress.ip_address(value)
    return address.version==4 and any(address in network for network in LAN_RANGES)

def local_url(value):
    """Legacy overrides can address local test servers, never public hosting."""
    if value is None:return None
    url=urlsplit(value)
    try:
        host=url.hostname
        local=host=='localhost' or ipaddress.ip_address(host).is_loopback or lan_address(host)
        valid_port=url.port is None or 0<url.port<=65535
    except (ValueError,TypeError):
        local=False;valid_port=False
    if not local or not valid_port or url.scheme not in ('http','https') or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
        raise ValueError('Visitor portal accepts local network URLs only')
    return value.rstrip('/')

def qr_png(url):
    try:
        import qrcode
    except ModuleNotFoundError as exc:
        if exc.name != 'qrcode':
            raise
        raise RuntimeError('生成二维码需要板端 Python 系统包 python3-qrcode') from exc
    output=io.BytesIO();qrcode.make(url,box_size=7,border=4).save(output,format='PNG');return output.getvalue()

class NetworkAddress:
    def __init__(self,port,public_url=None):self.port=port;self.local_url=local_url(public_url);self.cached=None;self.due=0.
    def snapshot(self,force=False):
        if self.cached and not force and time.monotonic()<self.due:return self.cached
        addresses=[]
        try:
            data=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','up'],timeout=2))
            for interface in data:
                name=interface['ifname']
                wireless=name.startswith(('wl','wlan')) or (Path('/sys/class/net')/name/'wireless').exists()
                if not wireless:continue
                for item in interface.get('addr_info',[]):
                    value=ipaddress.ip_address(item['local'])
                    if item.get('scope')=='global' and lan_address(str(value)):
                        addresses.append(dict(interface=name,address=str(value)))
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):pass
        addresses.sort(key=lambda item:item['interface'])
        address=addresses[0]['address'] if addresses else None
        base=self.local_url or (f'http://{address}:{self.port}' if address else None)
        self.cached=dict(base_url=base,address=address,wireless_connected=bool(address),scope='same-wifi',
            message='手机与板卡连接同一个 Wi-Fi 或手机热点后扫码。' if base else '尚未连接 Wi-Fi。请打开“设备 / Wi-Fi”连接手机热点，再让手机扫码。')
        self.due=time.monotonic()+5.;return self.cached

class Portal:
    def __init__(self,store,host='0.0.0.0',port=8091,public_url=None,network=None,photos=None,ai=None):
        self.store=store;self.photos=photos;self.ai=ai;self.thread=None;self.rate={};self.rate_lock=threading.Lock();self.qr_cache=(None,None)
        self.accounts=Accounts(store)
        from .report_service import ReportImages
        self.images=ReportImages(store)
        # This release serves visitors on the local network only. Old cloud
        # configuration/history is preserved and never loaded by this service.
        portal=self
        class Server(ThreadingHTTPServer):
            daemon_threads=True;allow_reuse_address=True
            def __init__(self,address):self.slots=threading.BoundedSemaphore(8);super().__init__(address,Handler)
            def process_request(self,request,address):
                if not self.slots.acquire(False):request.close();return
                try:super().process_request(request,address)
                except Exception:self.slots.release();raise
            def process_request_thread(self,request,address):
                try:super().process_request_thread(request,address)
                finally:self.slots.release()
        class Handler(BaseHTTPRequestHandler):
            server_version='InkVisitor/1.0'
            def setup(self):super().setup();self.connection.settimeout(8.)
            def log_message(self,*_):pass
            def send(self,status,data,mime='application/json; charset=utf-8',download=None,cookie=None):
                if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
                self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
                self.send_header('X-Content-Type-Options','nosniff')
                if cookie is not None:self.send_header('Set-Cookie',cookie)
                self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
                if download:self.send_header('Content-Disposition','attachment; filename="'+download+'"')
                self.end_headers()
                try:
                    if self.command!='HEAD':self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_HEAD(self):self.do_GET()
            def browser_token(self):
                jar=SimpleCookie()
                try:jar.load(self.headers.get('Cookie',''))
                except CookieError:return None
                item=jar.get(COOKIE)
                return item.value if item else None
            def account(self):return portal.accounts.resolve(self.browser_token())
            def require_account(self):
                account=self.account()
                if account is None:raise AccountError('请先登录账户。',401,'login_required')
                return account
            def private_path(self,path):
                # The HTML QR landing page is only an empty application shell.
                # Every data or media URL requires a browser login plus owner.
                match=re.match(r'^/api/visitor/([A-Za-z0-9_-]{43})(?:/|$)',path)
                if match is None:match=re.match(r'^/v/([A-Za-z0-9_-]{43})/',path)
                if match:portal.accounts.authorize(self.account(),match[1])
            def do_GET(self):
                try:self.get_route()
                except AccountError as exc:self.send(exc.status,{'message':exc.message,'code':exc.code})
                except KeyError:self.send(404,{'message':'链接或作品不存在，请使用自己的二维码。'})
                except (OSError,ValueError,TypeError,sqlite3.Error) as exc:self.send(503,{'message':'暂时无法读取，请稍后重试。'})
            def get_route(self):
                path=urlsplit(self.path).path
                self.private_path(path)
                if path=='/api/account':return self.send(200,portal.accounts.state(self.account()))
                if path=='/api/projects':
                    from ai_assistant.projects import project_catalog
                    from ai_assistant.catalog import presets
                    return self.send(200,dict(projects=project_catalog(),presets=presets()))
                if path=='/api/account/sessions':
                    account=self.require_account();query=parse_qs(urlsplit(self.path).query)
                    try:offset=int(query.get('offset',['0'])[0]);limit=int(query.get('limit',['50'])[0])
                    except (ValueError,IndexError):raise AccountError('记录页码无效。') from None
                    return self.send(200,portal.accounts.sessions(account['id'],offset,limit))
                if path=='/health':return self.send(200,{'ok':True,'version':1,'camera_used':False})
                if path in ('/','/leaderboard') or re.fullmatch(r'/v/[A-Za-z0-9_-]{43}',path):
                    if path.startswith('/v/'):portal.store.by_token(path.split('/')[2])
                    return self.send(200,(STATIC/'index.html').read_bytes(),'text/html; charset=utf-8')
                if path in ('/static/app.js','/static/style.css','/static/landscape.webp','/static/favicon.svg','/static/deepseek-whale.svg'):
                    file=STATIC/path.rsplit('/',1)[1];return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'text/plain')
                if path=='/api/leaderboards':
                    self.require_account()
                    return self.send(200,{'boards':portal.store.leaderboard(registered_only=True)})
                if path=='/api/network':return self.send(200,portal.network.snapshot())
                match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})/ai(?:/([a-f0-9]{32}))?',path)
                if match:return self.get_ai(match[1],match[2])
                match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})/health',path)
                if match:
                    course=parse_qs(urlsplit(self.path).query).get('course',['baduanjin'])[0]
                    try:return self.send(200,{'health_summary':portal.store.health_summary(match[1],course)})
                    except ValueError:return self.send(400,{'message':'请选择八段锦或太极课程。'})
                match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})/photos',path)
                if match and portal.photos is not None:
                    return self.send(200,{'jobs':portal.photos.list_jobs(match[1])})
                match=re.fullmatch(r'/v/([A-Za-z0-9_-]{43})/photos/([a-f0-9]{32})/(source|result)\.png',path)
                if match and portal.photos is not None:
                    image=portal.photos.read_image(match[1],match[2],match[3])
                    query=parse_qs(urlsplit(self.path).query)
                    download='ink-photo-'+match[2]+'.png' if query.get('download')==['1'] else None
                    return self.send(200,image,'image/png',download)
                match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})',path)
                if match:
                    view=portal.store.view(match[1]);view['report_images']=portal.images.manifest(match[1],view)
                    return self.send(200,view)
                match=re.fullmatch(r'/v/([A-Za-z0-9_-]{43})/art/([a-f0-9]{32})\.png',path)
                if match:
                    _,png=portal.store.artwork(match[1],match[2]);return self.send(200,png,'image/png','ink-'+match[2]+'.png')
                match=re.fullmatch(r'/v/([A-Za-z0-9_-]{43})/report\.png',path)
                if match:
                    query=parse_qs(urlsplit(self.path).query);page=int(query.get('page',['1'])[0])
                    png=portal.images.png(match[1],page)
                    return self.send(200,png,'image/png',None if query.get('preview')==['1'] else f'ink-report-{page:02}.png')
                match=re.fullmatch(r'/v/([A-Za-z0-9_-]{43})/report-images\.zip',path)
                if match:return self.send(200,portal.images.archive(match[1]),'application/zip','ink-report-images.zip')
                match=re.fullmatch(r'/v/([A-Za-z0-9_-]{43})/report\.(html|csv|json|zip)',path)
                if match:
                    kind=match[2];data=portal.store.report(match[1],kind)
                    mime={'html':'text/html; charset=utf-8','csv':'text/csv; charset=utf-8','json':'application/json; charset=utf-8','zip':'application/zip'}[kind]
                    return self.send(200,data,mime,'ink-report.'+kind)
                self.send(404,{'message':'页面不存在'})
            def do_POST(self):
                try:self.post_route()
                except AccountError as exc:self.send(exc.status,{'message':exc.message,'code':exc.code})
                except KeyError:self.send(404,{'message':'记录或二维码不存在，请重新扫码。'})
                except (OSError,ValueError,TypeError,sqlite3.Error):self.send(503,{'message':'操作暂时未完成，请稍后重试。'})
            def post_route(self):
                path=urlsplit(self.path).path
                if path.startswith('/api/account/'):
                    return self.post_account(path)
                self.private_path(path)
                ai_match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})/ai(/clear)?',path)
                if ai_match:return self.post_ai(ai_match[1],clear=bool(ai_match[2]))
                photo_match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})/photos',path)
                if photo_match and portal.photos is not None:return self.post_photo(photo_match[1])
                match=re.fullmatch(r'/api/visitor/([A-Za-z0-9_-]{43})/name',path)
                if not match:return self.send(404,{'message':'接口不存在'})
                origin=self.headers.get('Origin')
                if (origin and urlsplit(origin).netloc!=self.headers.get('Host')) or self.headers.get('X-Ink-Visitor')!='1':
                    return self.send(403,{'message':'请从自己的游客页面提交名字'})
                try:
                    if not portal.allow_name(self.client_address[0],match[1]):return self.send(429,{'message':'操作太频繁，请稍后重试'})
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=512:return self.send(400,{'message':'名字请求长度无效'})
                    if not self.headers.get('Content-Type','').startswith('application/json'):return self.send(415,{'message':'请提交 JSON'})
                    value=json.loads(self.rfile.read(size))
                    if not isinstance(value,dict) or set(value)!={'name'}:raise ValueError('名字格式无效')
                    portal.accounts.rename(self.require_account(),value['name'])
                    self.send(200,portal.store.rename(match[1],value['name']))
                except KeyError:self.send(404,{'message':'游客链接不存在'})
                except (ValueError,UnicodeError) as exc:self.send(400,{'message':str(exc)})
                except OSError:self.send(503,{'message':'保存失败，请重试'})
            def post_account(self,path):
                if path not in ('/api/account/register','/api/account/login','/api/account/logout','/api/account/claim','/api/account/ai/clear'):
                    return self.send(404,{'message':'接口不存在。'})
                origin=self.headers.get('Origin')
                if (origin and urlsplit(origin).netloc!=self.headers.get('Host')) or self.headers.get('X-Ink-Visitor')!='1':
                    raise AccountError('请从本网站提交操作。',403,'same_origin_required')
                if self.headers.get('Transfer-Encoding'):raise AccountError('请求格式无效。')
                try:size=int(self.headers.get('Content-Length','0'))
                except ValueError:raise AccountError('请求长度无效。') from None
                if not 0<size<=2048:raise AccountError('请求长度无效。')
                if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':
                    raise AccountError('请提交JSON格式。',415,'json_required')
                raw=self.rfile.read(size)
                if len(raw)!=size:raise AccountError('请求不完整。')
                try:value=json.loads(raw)
                except (ValueError,UnicodeError,RecursionError):raise AccountError('请求格式无效。') from None
                if path in ('/api/account/register','/api/account/login'):
                    if not portal.allow_auth(self.client_address[0]):
                        raise AccountError('登录尝试过多，请稍后再试。',429,'auth_rate_limited')
                    account=(portal.accounts.register(value) if path.endswith('/register') else portal.accounts.login(value))
                    portal.accounts.logout(self.browser_token())
                    token=portal.accounts.issue(account['id'])
                    cookie=f'{COOKIE}={token}; Path=/; Max-Age={MAX_AGE}; HttpOnly; SameSite=Lax'
                    return self.send(201 if path.endswith('/register') else 200,portal.accounts.state(account),cookie=cookie)
                if path=='/api/account/logout':
                    if value!={} or not isinstance(value,dict):raise AccountError('退出请求格式无效。')
                    portal.accounts.logout(self.browser_token())
                    return self.send(200,{'authenticated':False},cookie=f'{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
                account=self.require_account()
                if path=='/api/account/claim':
                    if not isinstance(value,dict) or set(value)!={'token'}:raise AccountError('关联请求格式无效。')
                    result=portal.accounts.claim(account,value['token'])
                    return self.send(200,dict(result,**portal.accounts.state(account)))
                if value!={} or not isinstance(value,dict):raise AccountError('清空请求格式无效。')
                if portal.ai is None:return self.send(503,{'message':'AI助手尚未配置。'})
                sessions=portal.accounts.sessions(account['id'])['sessions']
                if not sessions:return self.send(200,dict(cleared=0,jobs=[]))
                try:result=portal.ai.clear_owner(sessions[0]['token'])
                except Exception as exc:
                    from ai_assistant.contracts import AIError
                    if isinstance(exc,AIError):return self.send(exc.status,{'message':exc.message,'code':exc.code})
                    raise
                return self.send(200,result)
            def get_ai(self,token,job_id=None):
                # Resolve the visitor even when AI is unavailable: capability
                # and history endpoints are private, just like the report.
                visitor=portal.store.by_token(token)
                health={course:portal.store.health_summary(token,course) for course in ('baduanjin','taichi')}
                if portal.ai is None:
                    if job_id:return self.send(404,{'message':'建议记录不存在。'})
                    return self.send(200,{'capabilities':{'configured':False,'presets':{},'message':'AI 助手尚未配置。'},'jobs':[],'active':visitor['status']=='active','health_summary':health})
                try:
                    if job_id:return self.send(200,{'job':portal.ai.get(token,job_id)})
                    return self.send(200,{'capabilities':portal.ai.capabilities(),'jobs':portal.ai.list_jobs(token),'active':visitor['status']=='active','health_summary':health})
                except KeyError:raise
                except Exception:
                    # SDK, network and storage exception details may include
                    # server-only configuration. Never serialize them to phones.
                    return self.send(503,{'message':'暂时无法读取 AI 建议，请稍后刷新。'})
            def post_ai(self,token,clear=False):
                origin=self.headers.get('Origin')
                if (origin and urlsplit(origin).netloc!=self.headers.get('Host')) or self.headers.get('X-Ink-Visitor')!='1':
                    return self.send(403,{'message':'请从自己的游客页面发送。'})
                try:
                    visitor=portal.store.by_token(token)
                    archived_health=(visitor['status']=='archived' and hasattr(portal.store,'has_account_owner') and portal.store.has_account_owner(token))
                    if visitor['status']!='active' and not archived_health:return self.send(409,{'message':'本次游玩已结束，可阅读已有建议。请重新扫码开始。'})
                    if portal.ai is None:return self.send(503,{'message':'AI 助手尚未配置。'})
                    if self.headers.get('Transfer-Encoding'):return self.send(400,{'message':'请求格式无效。'})
                    try:size=int(self.headers.get('Content-Length','0'))
                    except ValueError:return self.send(400,{'message':'请求长度无效。'})
                    if size>2048:return self.send(413,{'message':'请求过长，请精简后发送。'})
                    if size<=0:return self.send(400,{'message':'请选择一种建议。'})
                    if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':return self.send(415,{'message':'请提交 JSON。'})
                    data=self.rfile.read(size)
                    if len(data)!=size:return self.send(400,{'message':'请求不完整，请重试。'})
                    try:value=json.loads(data)
                    except (ValueError,UnicodeError,RecursionError):return self.send(400,{'message':'请求格式无效。'})
                    if visitor['status']!='active' and (clear or not isinstance(value,dict) or value.get('kind')!='health'):
                        return self.send(409,{'message':'本次游玩已结束，仅可生成基于已保存记录的后续练习建议。'})
                    if clear:
                        if value!={} or not isinstance(value,dict):return self.send(400,{'message':'清空请求格式无效。'})
                        try:result=portal.ai.clear(token)
                        except Exception as exc:
                            from ai_assistant.contracts import AIError
                            if isinstance(exc,AIError):return self.send(exc.status,{'message':exc.message,'code':exc.code})
                            if isinstance(exc,KeyError):raise
                            return self.send(503,{'message':'暂时无法清空 AI 建议，请稍后重试。'})
                        return self.send(200,result)
                    if not isinstance(value,dict) or set(value)-{'kind','preset','text','project'} or value.get('kind') not in ('creation','review','guide','health'):
                        return self.send(400,{'message':'请选择项目讲解、创作建议、成绩复盘或健康训练。'})
                    preset=value.get('preset','');text=value.get('text','')
                    project=value.get('project','')
                    if not isinstance(preset,str) or not isinstance(text,str) or not isinstance(project,str) or len(text)>280 or len(preset)>64 or len(project)>32:
                        return self.send(400,{'message':'补充说明最多 280 字。'})
                    from ai_assistant.projects import PROJECT_IDS
                    if value['kind']=='health' and not project:project='baduanjin'
                    if ((value['kind']=='guide' and project not in PROJECT_IDS) or
                        (value['kind']=='health' and project not in ('baduanjin','taichi')) or
                        (value['kind'] not in ('guide','health') and project)):
                        return self.send(400,{'message':'请选择对应的项目。'})
                    try:
                        # Old creation/review providers keep their four-argument
                        # contract. Only the new project guide carries project.
                        if value['kind'] in ('guide','health'):job=portal.ai.submit(token,value['kind'],preset,text,project=project)
                        else:job=portal.ai.submit(token,value['kind'],preset,text)
                    except Exception as exc:
                        # Only the service's deliberate public error is safe.
                        from ai_assistant.contracts import AIError
                        if isinstance(exc,AIError):return self.send(exc.status,{'message':exc.message,'code':exc.code})
                        if isinstance(exc,KeyError):raise
                        return self.send(503,{'message':'AI 助手暂时不可用，请稍后重试。'})
                    return self.send(202,{'job':job})
                except KeyError:self.send(404,{'message':'游客链接不存在，请使用自己的二维码。'})
                except (OSError,ValueError):self.send(503,{'message':'发送失败，请稍后重试。'})
            def post_photo(self,token):
                from .photo_service import MAX_UPLOAD, PhotoError
                origin=self.headers.get('Origin')
                if (origin and urlsplit(origin).netloc!=self.headers.get('Host')) or self.headers.get('X-Ink-Visitor')!='1':
                    return self.send(403,{'message':'请从自己的游客页面上传照片。'})
                try:
                    # Authenticate and reject archived sessions before reading a
                    # large request. The service checks again at commit time.
                    visitor=portal.store.by_token(token)
                    if visitor['status']!='active':raise PhotoError('本次游玩已结束，请扫描主菜单的新二维码上传。',409)
                    if self.headers.get('Transfer-Encoding'):raise PhotoError('请使用普通照片上传。')
                    try:size=int(self.headers.get('Content-Length','0'))
                    except ValueError:raise PhotoError('照片请求长度无效。')
                    if size>MAX_UPLOAD:raise PhotoError('照片不能超过 16 MB。',413)
                    if size<=0:raise PhotoError('请选择一张照片。')
                    mime=self.headers.get('Content-Type','').split(';')[0].strip().lower()
                    if mime not in ('image/jpeg','image/png','image/webp','application/octet-stream'):
                        raise PhotoError('请上传 JPG、PNG 或 WebP 照片。',415)
                    data=self.rfile.read(size)
                    if len(data)!=size:raise PhotoError('照片上传不完整，请重试。')
                    style=parse_qs(urlsplit(self.path).query).get('style',['color'])[0]
                    return self.send(202,{'job':portal.photos.submit_bytes(token,data,style)})
                except KeyError:self.send(404,{'message':'游客链接不存在，请使用自己的二维码。'})
                except PhotoError as exc:self.send(exc.status,{'message':str(exc)})
                except (OSError,ValueError):self.send(503,{'message':'照片保存失败，请稍后重试。'})
        self.server=Server((host,port));self.port=self.server.server_address[1]
        self.network=network or NetworkAddress(self.port,public_url)
    def allow_name(self,address,token):
        stamp=time.monotonic();key=(address,token)
        with self.rate_lock:
            recent=[v for v in self.rate.get(key,[]) if stamp-v<60]
            if len(recent)>=10:return False
            recent.append(stamp);self.rate[key]=recent
            if len(self.rate)>2000:self.rate={k:v for k,v in self.rate.items() if v and stamp-v[-1]<60}
            return True
    def allow_auth(self,address):
        stamp=time.monotonic();key=('account-auth',address)
        with self.rate_lock:
            recent=[v for v in self.rate.get(key,[]) if stamp-v<300]
            if len(recent)>=30:return False
            recent.append(stamp);self.rate[key]=recent
            if len(self.rate)>2000:self.rate={k:v for k,v in self.rate.items() if v and stamp-v[-1]<300}
            return True
    def share(self):
        session=self.store.current();network=self.network.snapshot()
        url=network['base_url']+'/v/'+session['token'] if network['base_url'] else None
        if url and self.qr_cache[0]!=url:self.qr_cache=(url,qr_png(url))
        return dict(network,url=url,name=session['name'] or '游客',status=session['status'],qr=self.qr_cache[1] if url else None)
    def start(self):
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True,name='visitor-portal');self.thread.start();return self
    def close(self):
        if self.thread:self.server.shutdown();self.thread.join(3.);self.thread=None
        self.server.server_close()
        if self.photos is not None:self.photos.close()
