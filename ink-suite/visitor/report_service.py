"""Cached PNG reports rendered in a lower priority child, outside GTK/game loops."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import zipfile


def _digest(report):
    from .report_image import report_fingerprint
    return report_fingerprint(report)


class ReportImages:
    def __init__(self,store):
        self.store=store;self.root=store.root/'report-images';self.root.mkdir(exist_ok=True)
        self.lock=threading.Lock()

    def manifest(self,token,report=None):
        from .report_image import report_manifest
        report=report or self.store.view(token)
        result=report_manifest(report)
        pages=result.get('pages',result.get('page_count',1))
        if isinstance(pages,list):pages=len(pages)
        return dict(pages=int(pages),urls=[f'/v/{token}/report.png?page={i+1}' for i in range(int(pages))],
                    download_all=f'/v/{token}/report-images.zip')

    def png(self,token,page=1,report=None):
        report=report or self.store.view(token);manifest=self.manifest(token,report)
        if not 1<=page<=manifest['pages']:raise ValueError('页码不存在')
        signature=_digest(report);directory=self.root/signature;path=directory/f'report-{page:02}.png'
        if path.is_file():return path.read_bytes()
        if not self.lock.acquire(False):raise OSError('另一份战绩图片正在生成，请稍后重试。')
        try:
            if path.is_file():return path.read_bytes()
            directory.mkdir(exist_ok=True)
            job=directory/'job.json';job.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
            # Only known owned art IDs are passed. Never accept file paths from HTTP input.
            art={}
            for item in report.get('artworks',[])[:3]:
                try:
                    row,data=self.store.artwork(token,item['id'])
                    owned=directory/(item['id']+'.png');owned.write_bytes(data);art[item['id']]=str(owned)
                except (KeyError,OSError,ValueError):pass
            mapping=directory/'art.json';mapping.write_text(json.dumps(art),encoding='utf-8')
            try:
                result=subprocess.run([sys.executable,'-m','visitor.report_service','--render',str(job),str(mapping),str(page),str(path)],
                    cwd=str(Path(__file__).resolve().parents[1]),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
            except subprocess.TimeoutExpired as exc:raise OSError('战绩图片生成超时，请重试。') from exc
            if result.returncode or not path.is_file():raise OSError('战绩图片生成失败：'+result.stderr.decode(errors='replace')[-300:])
            # Bound cache growth without deleting pending/current visitor data.
            all_dirs=sorted((p for p in self.root.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime,reverse=True)
            if len(all_dirs)>40:
                import shutil
                for old in all_dirs[40:]:
                    if old!=directory and not old.is_symlink() and old.resolve().parent==self.root.resolve():shutil.rmtree(old)
            return path.read_bytes()
        finally:self.lock.release()

    def archive(self,token,report=None):
        report=report or self.store.view(token);info=self.manifest(token,report);out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_STORED) as archive:
            for i in range(1,info['pages']+1):archive.writestr(f'墨境战绩-{i:02}.png',self.png(token,i,report))
            archive.writestr('manifest.json',json.dumps(info,ensure_ascii=False,indent=2))
        return out.getvalue()


if __name__=='__main__':
    if len(sys.argv)!=6 or sys.argv[1]!='--render':raise SystemExit('Invalid report renderer job')
    if hasattr(os,'nice'):os.nice(15)
    from .report_image import render_report_page
    report=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    owned=json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
    reader=lambda ident:Path(owned[ident]).read_bytes() if ident in owned else None
    png=render_report_page(report,page=int(sys.argv[4]),artwork_reader=reader)
    target=Path(sys.argv[5]);temporary=target.with_suffix('.tmp');temporary.write_bytes(png);temporary.replace(target)
