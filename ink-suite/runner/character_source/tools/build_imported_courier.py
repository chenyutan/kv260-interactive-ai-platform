"""Offline CC0 Quaternius mesh -> original 15-node courier rig candidate.
This script emits fixed data only; no runtime importer or asset download.
"""
import sys,json,hashlib,argparse
from pathlib import Path
import numpy as np
from gltf_mesh import GLB,worlds,mesh
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=ROOT/'quaternius-hoodie/casual_hoodie.gltf')
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
args=parser.parse_args();SOURCE=args.source;DEST=args.output
g=GLB(SOURCE);nodes=g.j['nodes'];world=worlds(nodes);named={n['name']:i for i,n in enumerate(nodes)}
def pos(name):return world[named[name],:3,3]
def align(a,b):
 a=a/np.linalg.norm(a);b=b/np.linalg.norm(b);v=np.cross(a,b);c=np.dot(a,b)
 if c<-.9999:return np.diag([-1,-1,1])
 k=np.array([[0,-v[2],v[1]],[v[2],0,-v[0]],[-v[1],v[0],0]])
 return np.eye(3)+k+k@k/(1+c)
maps=[np.eye(4) for _ in range(15)];normalmaps=[np.eye(3) for _ in range(15)]
def setmap(i,p,A):
 maps[i][:3,:3]=A;maps[i][:3,3]=-A@p;normalmaps[i]=np.linalg.inv(A).T
pelvis=(pos('UpperLeg.L')+pos('UpperLeg.R'))*.5+np.array([0,.06,0])
setmap(0,pelvis,np.diag([1.24,1,1.15]));setmap(1,pelvis+[0,.10,0],np.diag([1.30,1.16,1.18]));setmap(3,pos('Head'),np.eye(3)*1.43)
for suffix in ['L','R']:
 arm=10 if pos('UpperArm.'+suffix)[0]>0 else 5;thigh=arm+2;shin=arm+3;shoe=arm+4
 for i,start,end,length in [(arm,'UpperArm.','LowerArm.',.25),(arm+1,'LowerArm.','Wrist.',.22),(thigh,'UpperLeg.','LowerLeg.',.36)]:
  p=pos(start+suffix);d=pos(end+suffix)-p;setmap(i,p,align(d,np.array([0,-1,0]))*(length/np.linalg.norm(d)))
 ankle=pos('Foot.'+suffix)+[0,.095,0];p=pos('LowerLeg.'+suffix);d=ankle-p
 setmap(shin,p,align(d,np.array([0,-1,0]))*(.30/np.linalg.norm(d)))
 # The glTF default display pose has asymmetric foot yaw. Thigh/shin
 # retargeting removes their display rotations; remove the foot's yaw too.
 # Local +Y of this authored foot bone points toward its toes. Project it
 # onto the ground, preserving sole height and intrinsic shoe shape.
 forward=world[named['Foot.'+suffix],:3,1];yaw=np.arctan2(forward[0],forward[2])
 c,s=np.cos(yaw),np.sin(yaw);unyaw=np.array([[c,0,-s],[0,1,0],[s,0,c]])
 setmap(shoe,ankle,np.diag([1.18,.82,1.08])@unyaw)
def target(name):
 if name in ['Root','Body','Hips']:return 0
 if name in ['Abdomen','Torso','Chest','Neck'] or name.startswith('Shoulder'):return 1
 if name=='Head':return 3
 if name.endswith(('.L','.R')):
  suffix=name[-1];arm=10 if pos('UpperArm.'+suffix)[0]>0 else 5
  if name.startswith('UpperArm'):return arm
  if name.startswith('UpperLeg'):return arm+2
  if name.startswith('LowerLeg'):return arm+3
  if name.startswith(('Foot','PT')):return arm+4
  return arm+1
 raise ValueError(name)
palette={'Skin':[1,.72,.53],'Purple':[.05,.68,.59],'White':[1,.95,.80],'Eyebrows':[.07,.07,.09],'Eye':[.035,.025,.022],'Hair':[.12,.07,.045],'LightBlue':[.08,.16,.25]}

import copy
hand_pose=copy.deepcopy(nodes)
def qmul(a,b):
 x,y,z,w=a;X,Y,Z,W=b
 return np.array([w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W,w*W-x*X-y*Y-z*Z])
for node in hand_pose:
 name=node.get('name','')
 if any(name.startswith(f) for f in ['Index','Middle','Ring','Pinky']):
  segment=int(name.split('.')[0][-1]);angle={1:-.10,2:-.95,3:-1.08,4:0}[segment]
  node['rotation']=qmul(node.get('rotation',[0,0,0,1]),[np.sin(angle/2),0,0,np.cos(angle/2)]).tolist()
parts=mesh(g,hand_pose);partindex=0;records=[];indices=[];lookup={};footcount=0
for ni,node in enumerate(nodes):
 if 'mesh' not in node:continue
 for pr in g.j['meshes'][node['mesh']]['primitives']:
  P,N,uv,idx,_=parts[partindex];partindex+=1;matname=g.j['materials'][pr.get('material',0)]['name'];color=np.array(palette[matname]);at=pr['attributes'];J=g.acc(at['JOINTS_0']);W=g.acc(at['WEIGHTS_0']);skin=g.j['skins'][node['skin']]
  if node['name']=='Casual_Feet' and matname=='Purple':color=np.array([.98,.28,.18])
  # Palette adaptation: exposed lower-leg mesh takes a dark legging colour.
  # This is not newly modelled trouser cloth; original short hems remain.
  if node['name']=='Casual_Legs' and matname=='Skin':color=np.array([.10,.18,.27])
  for vi,p in enumerate(P):
   amount=0.;anchor=np.zeros(3)
   for joint,weight in zip(J[vi],W[vi]):
    name=nodes[skin['joints'][joint]]['name']
    if name.startswith(('Wrist','Thumb','Index','Middle','Ring','Pinky')):
     amount+=float(weight);anchor+=pos('Wrist.'+name[-1])*weight
   if amount>1e-6:
    anchor/=amount;P[vi]=anchor+(p-anchor)*(1-.16*amount)
  _,inv=np.unique(np.round(P,5),axis=0,return_inverse=True);sums=np.zeros((inv.max()+1,3))
  for tri in idx.reshape(-1,3):
   normal=np.cross(P[tri[1]]-P[tri[0]],P[tri[2]]-P[tri[0]])
   for vi in tri:sums[inv[vi]]+=normal
  N=sums[inv];N/=np.maximum(np.linalg.norm(N,axis=1,keepdims=True),1e-9)
  remap=[]
  for vi,(p,n) in enumerate(zip(P,N)):
   weights={}
   for joint,weight in zip(J[vi],W[vi]):
    if weight<1e-6:continue
    ti=target(nodes[skin['joints'][joint]]['name']);weights[ti]=weights.get(ti,0)+float(weight)
   if node['name']=='Casual_Head':weights={3:1.}
   pairs=sorted(weights.items(),key=lambda z:-z[1]);total=sum(x[1] for x in pairs);pairs=[(i,w/total) for i,w in pairs]
   assert 1<=len(pairs)<=4
   flag=(1 if node['name']=='Casual_Feet' else 0)|(2 if node['name']=='Casual_Head' and matname in ['Skin','Eye','Eyebrows'] else 0)
   pairs+= [(0,0)]*(4-len(pairs));bones=[x[0] for x in pairs];weights=[x[1] for x in pairs]
   vertex_color=color.copy()
   if node['name']=='Casual_Body' and matname=='Purple':
    # Broad cream back yoke remains readable at the game's small actor size.
    if 1.285<p[1]<1.39 and p[2]<.045:vertex_color=np.array([1.,.94,.78])
    # Ribbed jacket hem is a clear coral separation from dark trousers.
    if p[1]<1.035:vertex_color=np.array([.97,.30,.22])
   values=np.concatenate([p,n,vertex_color,weights]);key=tuple(np.round(values,6))+tuple(bones)+(flag,)
   if key not in lookup:lookup[key]=len(records);records.append((values,bones,flag))
   remap.append(lookup[key])
  indices.extend(remap[i] for i in idx)
assert len(records)<65535
assert any(flag&1 for _,_,flag in records), 'foot reduction requires foot vertices'
for values,bones,flag in records:
 assert np.isfinite(values).all() and all(0<=b<15 for b in bones)
 assert min(values[9:])>=0 and abs(sum(values[9:])-1)<1e-6
assert all(0<=i<len(records) for i in indices)
fmt=lambda x: ('%.8g'%float(x))+('f' if ('.' in ('%.8g'%float(x)) or 'e' in ('%.8g'%float(x))) else '.0f')
with DEST.open('w',encoding='utf-8',newline='\n') as f:
 f.write('// Generated by runner/character_source/tools/build_imported_courier.py.\n// CC0 mesh: Quaternius Ultimate Modular Men, Casual_Hoodie.\n// Source SHA256 '+hashlib.sha256(SOURCE.read_bytes()).hexdigest()+'\n')
 f.write('#ifndef RUNNER_IMPORTED_DATA_H\n#define RUNNER_IMPORTED_DATA_H\nnamespace importedcourier {\nstruct Vertex {float p[3],n[3],c[3],w[4];unsigned char bone[4],flags;};\n')
 for name,arr in [('bind',np.array(maps)[:,:3,:]),('normal_bind',np.array(normalmaps))]:
  flat=arr.reshape(15,-1);f.write('static const float '+name+'[15]['+str(flat.shape[1])+']={\n')
  for row in flat:f.write('{'+','.join(map(fmt,row))+'},\n')
  f.write('};\n')
 f.write('static const Vertex vertices[]={\n')
 for v,b,flag in records:f.write('{{'+','.join(map(fmt,v[:3]))+'},{'+','.join(map(fmt,v[3:6]))+'},{'+','.join(map(fmt,v[6:9]))+'},{'+','.join(map(fmt,v[9:]))+'},{'+','.join(map(str,b))+'},'+str(flag)+'},\n')
 f.write('};\nstatic const unsigned short indices[]={\n')
 for i in range(0,len(indices),36):f.write(','.join(map(str,indices[i:i+36]))+',\n')
 f.write('};\n}\n#endif\n')
report={'source':str(SOURCE),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'output':str(DEST),'output_sha256':hashlib.sha256(DEST.read_bytes()).hexdigest(),'triangles':len(indices)//3,'vertices':len(records),'runtime_bones':15,'max_weights':4,'license':'CC0-1.0','foot_mapping':'Foot local +Y projected to ground removes authored default-pose yaw before segment scaling; no runtime pose change.','geometry_note':'Source authored hoodie; offline material remap, head proportions, segment fit and normal smoothing.'}
args.report.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report))
