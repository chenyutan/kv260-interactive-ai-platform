"""Temporary loopback-only DPU dataset extractor; no annotation/label input."""
import argparse,json,socket,struct,time,zlib
from pathlib import Path
import numpy as np
from movenet import MoveNet,RoiSelector,body_rois
from pose_pipeline import description,infer_hourglass

def recv_exact(sock,n):
    result=bytearray()
    while len(result)<n:
        part=sock.recv(n-len(result))
        if not part: raise EOFError()
        result.extend(part)
    return bytes(result)

def serve(backend='movenet',allow_coarse=False):
    root=Path(__file__).resolve().parent
    if backend=='hourglass':
        from hourglass import Hourglass
        from hourglass_select import rois as default_rois
        net=Hourglass(root/'models/hourglass-pe_mpii.xmodel',allow_coarse=allow_coarse);iw=ih=256
    else:
        net=MoveNet(root/'models/movenet_ntd_pt.xmodel',root/'models/movenet_ntd_pt.prototxt');iw=ih=192;default_rois=body_rois
    deadline=time.monotonic()+7200;debug_count=0
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind(('127.0.0.1',8767));server.listen(1);server.settimeout(30)
        print('READY '+json.dumps(net.info),flush=True)
        while time.monotonic()<deadline:
            try: sock,peer=server.accept()
            except socket.timeout: continue
            if peer[0] != '127.0.0.1':
                sock.close();continue
            with sock:
                sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                sock.settimeout(120);selector=RoiSelector();width=height=None
                while time.monotonic()<deadline:
                    try:
                        size=struct.unpack('!I',recv_exact(sock,4))[0]
                        if not 1<=size<=2400000: raise ValueError('Request size invalid')
                        data=recv_exact(sock,size)
                        if data[:1]==b'J':
                            header=json.loads(data[1:]);width=int(header['width']);height=int(header['height'])
                            if header.get('op')!='begin' or not 32<=min(width,height)<=4096 or max(width,height)>4096:
                                raise ValueError('Invalid clip geometry')
                            rois=header.get('rois') or default_rois(width,height)
                            if not 1<=len(rois)<=3:raise ValueError('Expected one to three ROIs')
                            for x,y,w,h in rois:
                                if not all(isinstance(v,int) for v in [x,y,w,h]) or min(x,y)<0 or min(w,h)<16 or x+w>width or y+h>height:
                                    raise ValueError('ROI outside image')
                            debug_save=header.get('debug_save_heatmaps') is True
                            force_all=header.get('probe_all_rois') is True
                            selector=RoiSelector();result={'ok':True,'model':net.info,
                                'pipeline':description(net,rois),'score_semantics':net.info.get('score_semantics','heatmap_sigmoid')}
                        elif data[:1]==b'F' and width:
                            expected=len(rois)*ih*iw*3
                            decompressor=zlib.decompressobj();raw=decompressor.decompress(data[1:],expected+1)
                            if len(raw)!=expected or not decompressor.eof or decompressor.unused_data: raise ValueError('Invalid RGB payload')
                            frames=np.frombuffer(raw,np.uint8).reshape(len(rois),ih,iw,3)
                            poses=[];timings=[];debug_paths=[]
                            def save_debug(index):
                                nonlocal debug_count
                                if debug_save and backend=='hourglass' and debug_count<16:
                                    directory=root/'validation/hourglass-raw';directory.mkdir(exist_ok=True)
                                    dest=directory/('heat-'+str(time.monotonic_ns())+'.i8')
                                    net.output.tofile(dest);debug_paths.append(str(dest));debug_count+=1
                            if backend=='hourglass':
                                points,index,poses,timings,diagnostic=infer_hourglass(net,lambda i,r:frames[i],rois,width,height,force_all=force_all,on_prediction=save_debug)
                                quality=[float(min(p[5:13,2])) for p in poses]
                            else:
                                for rgb in frames:
                                    p,timing=net.predict(rgb);poses.append(p);timings.append(timing)
                                points,index,quality=selector.select(poses,rois,width,height);diagnostic={}
                            result={'ok':True,'points17':points.tolist(),'roi_index':index,'roi_quality':quality,'roi_poses':[p.tolist() for p in poses],'timing':timings,'diagnostic':diagnostic,'debug_paths':debug_paths}
                        else: raise ValueError('Invalid request type or missing begin')
                        reply=json.dumps(result,allow_nan=False).encode();sock.sendall(struct.pack('!I',len(reply))+reply)
                    except (EOFError,ConnectionError,socket.timeout): break
                    except Exception as exc:
                        reply=json.dumps({'ok':False,'error':str(exc)}).encode()
                        sock.sendall(struct.pack('!I',len(reply))+reply);break

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=['movenet','hourglass'],default='movenet');parser.add_argument('--allow-coarse',action='store_true')
    args=parser.parse_args();serve(args.backend,args.allow_coarse)
