"""Read authored GLB positions/weights and animations without external importer."""
import json,struct,sys,copy,argparse,base64
from pathlib import Path
import numpy as np
class GLB:
 def __init__(self,path):
  b=Path(path).read_bytes()
  if b[:4]==b'glTF':
   n,t=struct.unpack_from('<II',b,12);self.j=json.loads(b[20:20+n]);o=20+n;z,t=struct.unpack_from('<II',b,o);self.b=b[o+8:o+8+z]
  else:
   self.j=json.loads(b);uri=self.j['buffers'][0]['uri'];self.b=base64.b64decode(uri.split(',',1)[1]) if uri.startswith('data:') else (Path(path).parent/uri).read_bytes()
 def acc(self,i):
  a=self.j['accessors'][i];v=self.j['bufferViews'][a['bufferView']];dt={5126:'<f4',5125:'<u4',5123:'<u2',5121:'u1'}[a['componentType']];k={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
  offset=v.get('byteOffset',0)+a.get('byteOffset',0);stride=v.get('byteStride',np.dtype(dt).itemsize*k)
  return np.ndarray((a['count'],k),dtype=dt,buffer=self.b,offset=offset,strides=(stride,np.dtype(dt).itemsize)).copy()
def mat(n):
 if 'matrix' in n:return np.array(n['matrix']).reshape(4,4).T
 x,y,z,w=n.get('rotation',[0,0,0,1]);m=np.eye(4);m[:3,:3]=[[1-2*y*y-2*z*z,2*x*y-2*z*w,2*x*z+2*y*w],[2*x*y+2*z*w,1-2*x*x-2*z*z,2*y*z-2*x*w],[2*x*z-2*y*w,2*y*z+2*x*w,1-2*x*x-2*y*y]]
 m[:3,:3]*=np.array(n.get('scale',[1,1,1]))[None,:];m[:3,3]=n.get('translation',[0,0,0]);return m
def worlds(nodes):
 parents={c:i for i,n in enumerate(nodes) for c in n.get('children',[])};cache={}
 def world(i):
  if i not in cache:cache[i]=(world(parents[i]) if i in parents else np.eye(4))@mat(nodes[i])
  return cache[i]
 return np.array([world(i) for i in range(len(nodes))])
def animation_pose(g,anim,phase,clipname=None):
 nodes=copy.deepcopy(g.j['nodes']);lookup={n['name']:i for i,n in enumerate(nodes)};clip=next((c for c in anim.j['animations'] if c.get('name')==clipname),anim.j['animations'][-1])
 duration=max(float(anim.acc(s['input']).max()) for s in clip['samplers']);t=phase*duration
 for ch in clip['channels']:
  name=anim.j['nodes'][ch['target']['node']]['name'];path=ch['target']['path']
  if name not in lookup:continue
  sampler=clip['samplers'][ch['sampler']];times=anim.acc(sampler['input']).reshape(-1);values=anim.acc(sampler['output']);i=max(0,min(len(times)-2,int(np.searchsorted(times,t,side='right')-1)))
  u=np.clip((t-times[i])/max(1e-9,times[i+1]-times[i]),0,1);a=values[i];b=values[i+1]
  if path=='rotation' and np.dot(a,b)<0:b=-b
  v=a*(1-u)+b*u
  if path=='rotation':v/=np.linalg.norm(v)
  nodes[lookup[name]][path]=v.tolist()
 return nodes
def mesh(g,pose=None,stylize=False):
 nodes=g.j['nodes'];w=worlds(pose or nodes);parts=[]
 for ni,node in enumerate(nodes):
  if 'mesh' not in node:continue
  for pr in g.j['meshes'][node['mesh']]['primitives']:
   at=pr['attributes'];p=g.acc(at['POSITION']);n=g.acc(at['NORMAL']);uv=g.acc(at['TEXCOORD_0']) if 'TEXCOORD_0' in at else np.zeros((len(p),2));idx=g.acc(pr['indices']).reshape(-1);q=np.column_stack([p,np.ones(len(p))])
   if 'skin' in node:
    skin=g.j['skins'][node['skin']];ib=g.acc(skin['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1);m=w[skin['joints']]@ib;joint=g.acc(at['JOINTS_0']);weight=g.acc(at['WEIGHTS_0']);P=np.zeros((len(p),3));N=np.zeros((len(p),3))
    for k in range(4):
     mk=m[joint[:,k]];P+=np.einsum('nij,nj->ni',mk,q)[:,:3]*weight[:,k,None];N+=np.einsum('nij,nj->ni',mk[:,:3,:3],n)*weight[:,k,None]
   else:P=(q@w[ni].T)[:,:3];N=n@w[ni][:3,:3].T
   N/=np.maximum(np.linalg.norm(N,axis=1,keepdims=True),1e-9)
   color=np.array(g.j.get('materials',[{}])[pr.get('material',0)].get('pbrMetallicRoughness',{}).get('baseColorFactor',[1,1,1,1])[:3]);color=np.where(color<=.0031308,color*12.92,1.055*color**(1/2.4)-.055)
   if stylize:
    material=g.j['materials'][pr.get('material',0)]['name'];name=node['name']
    palette={'Skin':[1,.72,.53],'Purple':[.05,.68,.59],'White':[1,.95,.80],'Eyebrows':[.07,.07,.09],'Eye':[.035,.025,.022],'Hair':[.12,.07,.045],'LightBlue':[.08,.16,.25]}
    color=np.array(palette.get(material,color))
    if name=='Casual_Feet' and material=='Purple':color=np.array([.98,.28,.18])
    if name=='Casual_Head':
     neck=next(i for i,n in enumerate(nodes) if n['name']=='Neck');anchor=w[neck,:3,3];P=anchor+(P-anchor)*1.43
    # Weld only within this material primitive: cloth smooths, garment boundaries stay distinct.
    keys=np.round(P,5);_,inv=np.unique(keys,axis=0,return_inverse=True);sums=np.zeros((inv.max()+1,3))
    for tri in idx.reshape(-1,3):
     normal=np.cross(P[tri[1]]-P[tri[0]],P[tri[2]]-P[tri[0]])
     for vi in tri:sums[inv[vi]]+=normal
    N=sums[inv];N/=np.maximum(np.linalg.norm(N,axis=1,keepdims=True),1e-9)
   parts.append((P,N,uv,idx,color))
 return parts
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('model');parser.add_argument('output');parser.add_argument('--animation');parser.add_argument('--clip');parser.add_argument('--stylize',action='store_true');parser.add_argument('--phase',type=float,default=.19);a=parser.parse_args()
 g=GLB(a.model);parts=mesh(g);p=np.concatenate([part[0] for part in parts]);lo=p.min(0);hi=p.max(0);scale=1.95/(hi[1]-lo[1]);center=np.array([(lo[0]+hi[0])/2,lo[1],(lo[2]+hi[2])/2]);out=[];sun=np.array([-.4,.8,.45]);sun/=np.linalg.norm(sun)
 if a.animation:parts=mesh(g,animation_pose(g,GLB(a.animation),a.phase,a.clip),a.stylize)
 elif a.stylize:parts=mesh(g,stylize=True)
 for p,n,uv,idx,color in parts:
  p=(p-center)*scale;p[:,1]+=.012;light=.65+.35*np.maximum(0,n@sun);c=np.repeat(light[:,None],3,axis=1)*color;v=np.column_stack([p,c,np.ones(len(p)),uv]);out.append(v[idx])
 np.concatenate(out).astype('<f4').tofile(a.output);print(json.dumps({'triangles':sum(len(part[3])//3 for part in parts),'vertices':sum(len(part[0]) for part in parts),'bounds':[lo.tolist(),hi.tolist()],'scale':scale,'joints':len(g.j['skins'][0]['joints'])}))
