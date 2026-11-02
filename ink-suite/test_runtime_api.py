"""In-process fake-frame HTTP checks; no model or camera is loaded."""
import importlib.util,json,sys,threading,unittest
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parent))
from suite_control import SuiteControl
spec=importlib.util.spec_from_file_location('suite_web',Path(__file__).resolve().parent/'runtime/web_ui.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)

class RuntimeApiTests(unittest.TestCase):
    def setUp(self):
        self.control=SuiteControl();self.frames=[];self.shots=0
        def frame(after):
            s=self.control.snapshot()
            if not s['active'] or s['menu'] or s['mode']!='environment':return None
            if after==7:return None
            return bytes([100,130,150])*32*18,7,12.5,32,18,96,[[-1,-1,0]]*17,0.
        def command(value):return dict(accepted=True,suite=self.control.configure(value))
        def photo():
            self.shots+=1
            return frame(-1)+(1234.5,88)
        self.server=web.ViewerServer('127.0.0.1',0,lambda:dict(suite=self.control.snapshot()),lambda:b'',lambda:b'',command,get_environment_frame=frame,get_environment_photo=photo).start()
        self.base='http://127.0.0.1:'+str(self.server.address[1]);self.http=build_opener(ProxyHandler({}))
    def tearDown(self):self.server.stop()
    def setmode(self,**kw):
        request=Request(self.base+'/api/command',data=json.dumps(dict(command='suite',**kw)).encode(),headers={'Content-Type':'application/json'})
        return json.load(self.http.open(request,timeout=3))
    def test_environment_is_dormant_outside_its_active_page(self):
        self.assertEqual(self.http.open(self.base+'/environment.rgb').status,204)
        self.setmode(active=True,mode='environment',menu=True)
        self.assertEqual(self.http.open(self.base+'/environment.rgb').status,204)
    def test_rgb_frame_headers_and_no_duplicate_frame(self):
        self.setmode(active=True,mode='environment',menu=False)
        with self.http.open(self.base+'/environment.rgb') as r:
            self.assertEqual(len(r.read()),32*18*3);self.assertEqual(r.headers['X-Preview-Version'],'7')
            self.assertEqual(len(json.loads(r.headers['X-Points17'])),17)
            self.assertEqual(r.headers['X-Preview-Stride'],'96')
        self.assertEqual(self.http.open(self.base+'/environment.rgb?after=7').status,204)
    def test_modes_pause_ink_without_destroying_api(self):
        for mode in ('fight','fruit','environment'):
            result=self.setmode(active=True,mode=mode,menu=False)
            self.assertEqual(result['suite']['mode'],mode);self.assertFalse(self.control.ink_allowed())
        self.setmode(mode='ink');self.assertTrue(self.control.ink_allowed())
        state=json.load(self.http.open(self.base+'/api/state'));self.assertEqual(state['suite']['mode'],'ink')
    def test_bad_commands_are_rejected_without_state_change(self):
        for command in (dict(mode='nonsense'),dict(menu='false')):
            with self.assertRaises(HTTPError) as e:self.setmode(**command)
            self.assertEqual(e.exception.code,400)
        self.assertFalse(self.control.snapshot()['active'])
    def test_explicit_post_delivers_one_photo_with_provenance(self):
        self.setmode(active=True,mode='environment',menu=False)
        request=Request(self.base+'/environment/photo.rgb',data=b'{}',headers={'Content-Type':'application/json'})
        with self.http.open(request) as response:
            self.assertEqual(response.headers['X-Capture-Monotonic'],'1234.5')
            self.assertEqual(response.headers['X-Capture-Sequence'],'88')
            self.assertEqual(len(response.read()),32*18*3)
        self.assertEqual(self.shots,1)
    def test_get_invalid_payload_and_foreign_origin_cannot_trigger_shutter(self):
        self.setmode(active=True,mode='environment',menu=False)
        for request in (Request(self.base+'/environment/photo.rgb'),
                        Request(self.base+'/environment/photo.rgb',data=b'{"repeat":true}'),
                        Request(self.base+'/environment/photo.rgb',data=b'{}',headers={'Origin':'http://example.invalid'})):
            with self.assertRaises(HTTPError):self.http.open(request)
        self.assertEqual(self.shots,0)

if __name__=='__main__':unittest.main()
