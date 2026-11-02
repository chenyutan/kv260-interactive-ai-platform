"""Launcher matching tests use a temporary proc tree, never running processes."""
import os,tempfile,unittest
from unittest.mock import Mock,patch
from pathlib import Path
from launch import find_displays,DISPLAY_SOURCE
import launch


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name)
    def tearDown(self):self.directory.cleanup()
    def proc(self,pid,args,display=':99'):
        p=self.root/str(pid);p.mkdir();(p/'cmdline').write_bytes(b'\0'.join(a.encode() for a in args)+b'\0')
        (p/'environ').write_bytes(('DISPLAY='+display+'\0').encode());(p/'stat').write_text(str(pid)+' (python3) '+' '.join(['S']+['0']*18+['234567']+['0']*8))
    def test_only_exact_standalone(self):
        self.proc(111,['/usr/bin/python3',DISPLAY_SOURCE])
        self.proc(112,['/usr/bin/python3','/home/ubuntu/ink-suite/embedded_runner.py','--source',DISPLAY_SOURCE])
        self.proc(113,['/usr/bin/python3',DISPLAY_SOURCE+'.backup'])
        matches=find_displays(self.root)
        self.assertEqual([m['pid'] for m in matches],[111]);self.assertEqual(matches[0]['identity'],'234567')
        self.assertEqual(matches[0]['env']['DISPLAY'],':99')
    def test_requires_graphical_session(self):
        self.proc(111,['/usr/bin/python3',DISPLAY_SOURCE],display='')
        self.assertEqual(find_displays(self.root),[])
    def test_foreign_owner_ignored(self):
        self.proc(111,['/usr/bin/python3',DISPLAY_SOURCE])
        self.assertEqual(find_displays(self.root,uid=os.getuid()+1000),[])
    def test_incomplete_proc_ignored(self):
        (self.root/'111').mkdir();self.assertEqual(find_displays(self.root),[])

    def test_host_gets_image_backing_before_gtk_import_even_if_similar_inherited(self):
        child=Mock();child.wait.return_value=0
        with patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.root),'GDK_RENDERING':'similar'}), \
             patch.object(launch.fcntl,'flock'),patch.object(launch.sys,'argv',['launch.py','--no-service-changes']), \
             patch.object(launch.subprocess,'Popen',return_value=child) as spawn, \
             patch.object(launch.signal,'signal'):
            self.assertEqual(launch.main(),0)
        self.assertEqual(spawn.call_args.kwargs['env']['GDK_RENDERING'],'image')
        self.assertEqual(spawn.call_args.args[0][1],str(launch.ROOT/'main.py'))

if __name__=='__main__':unittest.main()
