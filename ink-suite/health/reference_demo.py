"""Reference-only teaching poses. Never supplies observations or repetition input.

Select performs bounded asset I/O outside draw; sample is a pure cached lookup.
Procedural fallbacks are explicitly independent teaching illustrations.
"""
import bisect
import hashlib
import json
import math
from pathlib import Path

from . import catalog, taichi_catalog

EDGES = catalog.POSE_EDGES


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def points(raw):
    if not isinstance(raw, (tuple, list)) or len(raw) != 17:
        raise ValueError('Expected 17 reference slots')
    result = []
    for point in raw:
        if point is None:
            result.append(None)
        elif (isinstance(point, (list, tuple)) and len(point) >= 3 and
              all(finite(v) for v in point[:3]) and all(0 <= v <= 1 for v in point[:3])):
            result.append(tuple(point[:3]))
        else:
            raise ValueError('Invalid reference joint')
    return tuple(result)


def fallback_pose(course_id, lesson_id, phase=0.):
    """Complete independent illustration; raise has distinct up/down paths."""
    phase = float(phase) % 1 if finite(phase) else 0.
    module = taichi_catalog if course_id == 'taichi' else catalog
    if lesson_id not in module.BY_ID:
        lesson_id = 'prepare'
    pose = module.reference_pose(lesson_id, phase)
    label, arrow, active = '按舒适节奏练习', None, ()
    if course_id == 'baduanjin' and lesson_id == 'raise':
        # Separate anatomical waypoints prevent the lowering stroke from
        # retracing the central upward stroke. Head/feet remain fixed.
        keys = (
            (0., {7:(.39,.43),8:(.61,.43),9:(.47,.52),10:(.53,.52)}, '放松起势', None),
            (.15, {7:(.36,.39),8:(.64,.39),9:(.47,.36),10:(.53,.36)}, '身前上举', 'up'),
            (.34, {7:(.36,.20),8:(.64,.20),9:(.47,.14),10:(.53,.14)}, '身前上举', 'up'),
            (.48, {7:(.40,.12),8:(.60,.12),9:(.47,.045),10:(.53,.045)}, '向上舒展', 'hold'),
            (.61, {7:(.40,.12),8:(.60,.12),9:(.47,.045),10:(.53,.045)}, '向上舒展', 'hold'),
            (.77, {7:(.29,.28),8:(.71,.28),9:(.19,.33),10:(.81,.33)}, '两侧回落', 'out_down'),
            (.90, {7:(.34,.43),8:(.66,.43),9:(.33,.57),10:(.67,.57)}, '两侧回落', 'out_down'),
            (1., {7:(.39,.43),8:(.61,.43),9:(.47,.52),10:(.53,.52)}, '放松回收', None))
        i = min(len(keys)-2, max(0, bisect.bisect_right([r[0] for r in keys], phase)-1))
        a,b=keys[i],keys[i+1];u=(phase-a[0])/(b[0]-a[0]);u=u*u*(3-2*u)
        pose = [[*catalog.NEUTRAL[j],1.] if j in catalog.NEUTRAL else None for j in range(17)]
        for j in a[1]:
            pose[j]=[a[1][j][0]*(1-u)+b[1][j][0]*u,a[1][j][1]*(1-u)+b[1][j][1]*u,1.]
        label,arrow=('放松起势',None) if phase<.08 else ('身前上举','up') if phase<.44 else ('向上舒展','hold') if phase<.61 else ('两侧回落','out_down') if phase<.91 else ('放松回收',None)
        active=(9,10)
    else:
        instructions=module.BY_ID[lesson_id]['instructions']
        label='动作方向示意'
        arrow='forward' if lesson_id in ('fist','brush','repulse','high_pat','grasp_left','grasp_right') else None
    return dict(points=points(pose), viewport=(.12,.0,.88,.99), aspect=1.5,
                mirror=False, phase_label=label, arrow_kind=arrow, active_joints=active,
                synced=False, caption='教学示意 · 独立节奏', provenance='procedural')


def project_reference_pose(pose, box):
    """One fixed whole-cycle viewport; motion never changes displayed scale."""
    left,top,right,bottom=box;x0,y0,x1,y1=pose['viewport'];aspect=pose['aspect']
    scale=min((right-left)*.94/((x1-x0)*aspect),(bottom-top)*.94/(y1-y0))
    cx,cy=(x0+x1)/2,(y0+y1)/2
    sign=-1 if pose.get('mirror') else 1
    return tuple(None if p is None else
                 ((left+right)/2+sign*(p[0]-cx)*aspect*scale,
                  (top+bottom)/2+(p[1]-cy)*scale,p[2]) for p in pose['points'])


class ReferenceLibrary:
    """Only current clip is retained; call select from view state acceptance."""
    def __init__(self, asset_dir=None):
        self.asset_dir=Path(asset_dir) if asset_dir else Path(__file__).parent/'assets/reference'
        self._key=None;self._data=None;self._times=();self.error=None

    def select(self, course_id, lesson_id, clip_path=None):
        key=(course_id,lesson_id,str(clip_path or ''))
        if key==self._key:return self._data is not None
        self._key=key;self._data=None;self._times=();self.error=None
        if not clip_path:return False
        candidate=self.asset_dir/('{}-{}.json'.format(course_id,lesson_id))
        if not candidate.is_file() and course_id=='baduanjin':candidate=self.asset_dir/(lesson_id+'.json')
        try:
            if candidate.stat().st_size>4*1024*1024:raise ValueError('Reference asset too large')
            data=json.loads(candidate.read_text(encoding='utf-8'))
            if data.get('schema_version')!=1 or data.get('course_id')!=course_id or data.get('lesson_id')!=lesson_id:
                raise ValueError('Reference identity mismatch')
            expected=(candidate.parent/data['clip_path']).resolve()
            supplied=Path(clip_path).resolve()
            if expected!=supplied:raise ValueError('Reference clip path mismatch')
            digest=hashlib.sha256()
            with supplied.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=data['clip_sha256']:raise ValueError('Reference clip hash mismatch')
            bbox=data.get('viewport_bbox',(.0,.0,1.,1.))
            if len(bbox)!=4 or not all(finite(v) for v in bbox) or not (bbox[0]<bbox[2] and bbox[1]<bbox[3]):
                raise ValueError('Invalid reference viewport')
            aspect=data.get('source_aspect',16/9)
            if not finite(aspect) or not .2<aspect<5:raise ValueError('Invalid source aspect')
            frames=data['frames']
            if not 2<=len(frames)<=20000:raise ValueError('Invalid reference length')
            parsed=[]
            for frame in frames:
                if not finite(frame['t']) or frame['t']<0:raise ValueError('Invalid reference time')
                parsed.append((float(frame['t']),points(frame['joints'])))
            if any(a[0]>=b[0] for a,b in zip(parsed,parsed[1:])):raise ValueError('Reference times unordered')
            phases=data.get('phases',())
            for phase in phases:
                if not all(finite(phase.get(k)) for k in ('start','end')) or phase['start']>=phase['end']:
                    raise ValueError('Invalid phase interval')
            data.update(frames=tuple(parsed),viewport_bbox=tuple(bbox),source_aspect=aspect,clip_resolved=str(supplied))
            self._data=data;self._times=tuple(row[0] for row in parsed)
            return True
        except (OSError,ValueError,KeyError,TypeError,OverflowError) as error:
            self.error=str(error);return False

    def sample(self, pts_seconds, clip_path=None, generation=None):
        """No I/O. Caller supplies the timestamp of the currently drawn frame."""
        data=self._data
        if data is None or not finite(pts_seconds):return None
        # Do not resolve a path here: sample remains free of filesystem calls.
        if clip_path is not None and str(clip_path)!=self._key[2]:return None
        t=float(pts_seconds)
        if t<self._times[0]-.15 or t>self._times[-1]+.15:return None
        i=max(0,min(len(self._times)-1,bisect.bisect_right(self._times,t)-1))
        pose=data['frames'][i][1]
        phase=next((p for p in data.get('phases',()) if p['start']<=t<p['end']),{})
        return dict(points=pose,viewport=data['viewport_bbox'],aspect=data['source_aspect'],
                    mirror=bool(data.get('mirror',False)),phase_label=str(phase.get('label','跟随真人示范')),
                    arrow_kind=phase.get('arrow_kind'),active_joints=tuple(phase.get('active_joints',(9,10))),
                    synced=True,caption='参考轨迹 · 随视频同步',provenance=data.get('provenance','offline'),
                    generation=generation,t=t)
