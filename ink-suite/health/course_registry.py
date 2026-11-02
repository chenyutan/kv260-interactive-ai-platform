"""Local course metadata; legacy Baduanjin imports remain unchanged."""
import copy
from pathlib import Path

from . import catalog, taichi_catalog

COURSE_IDS = ('baduanjin', 'taichi')


def get_course(course_id='baduanjin'):
    """Return independent metadata, so a view cannot edit another session."""
    if course_id not in COURSE_IDS:
        raise ValueError('Unknown health course: ' + str(course_id))
    module = catalog if course_id == 'baduanjin' else taichi_catalog
    lessons = copy.deepcopy(module.COURSE)
    return dict(id=course_id,
                title='八段锦指导' if course_id == 'baduanjin' else '太极指导',
                subtitle='八段锦' if course_id == 'baduanjin' else '二十四式太极拳',
                report_kind=course_id + '_practice',
                report_prefix='' if course_id == 'baduanjin' else 'taichi-',
                video_directory=Path(__file__).parent / 'assets' /
                    ('video' if course_id == 'baduanjin' else 'taichi-video'),
                lessons=lessons, by_id={item['id']: item for item in lessons},
                reference_pose=module.reference_pose,
                reference_caption=module.REFERENCE_CAPTION,
                assessment_notice=module.ASSESSMENT_NOTICE,
                notice=module.COURSE_NOTICE,
                sources=copy.deepcopy(module.SOURCE_LINKS),
                repetition_counting=course_id == 'baduanjin')


def get_courses():
    return tuple(get_course(identifier) for identifier in COURSE_IDS)
