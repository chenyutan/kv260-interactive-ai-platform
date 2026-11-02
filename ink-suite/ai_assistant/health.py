"""Bounded course-record facts and local next-practice choices, not diagnosis.

No image, raw pose, name, report path or source attribution is exported here.
Tracking time describes visibility only, never movement quality or completion.
Missing or weak observations never block the course's concrete practice steps.
"""
import math
import re
from .contracts import AIError,parse_json

COURSES=('baduanjin','taichi')
HEALTH_PRESETS=(
    dict(id='next',title='后续练习',description='根据已记录内容选择下一步'),
    dict(id='review',title='复习建议',description='回顾已有记录与示范'),
    dict(id='demo',title='先看示范',description='选择可以先学习的动作'))
IDENT=re.compile(r'^[a-f0-9]{32}$')

def course(value):
    from health.course_registry import get_course
    if not isinstance(value,str) or value not in COURSES:
        raise AIError('invalid_course','请选择八段锦或太极课程。')
    return get_course(value)

def number(value,maximum=86400):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=maximum:
        raise ValueError('Invalid bounded practice statistic')
    return round(float(value),3)

def count(value,maximum=10000):
    if type(value) is not int or not 0<=value<=maximum:raise ValueError('Invalid practice count')
    return value

def sanitize_report(report):
    """The host supplies a frozen engine report for an explicitly captured owner."""
    if not isinstance(report,dict):raise ValueError('Expected a saved course report')
    meta=course(report.get('course_id'))
    ident=report.get('session_id')
    if not isinstance(ident,str) or not IDENT.fullmatch(ident):raise ValueError('Invalid practice identity')
    if report.get('kind')!=meta['report_kind']:raise ValueError('Course/report mismatch')
    if report.get('report_save_state') not in (None,'saved') or report.get('report_save_error'):
        raise ValueError('Practice report must be saved before account indexing')
    raw=report.get('lessons')
    if not isinstance(raw,list) or len(raw)>len(meta['lessons']):raise ValueError('Invalid lesson summary')
    lessons=[];seen=set()
    for item in raw:
        if not isinstance(item,dict) or item.get('id') not in meta['by_id'] or item['id'] in seen:
            raise ValueError('Invalid or repeated lesson')
        seen.add(item['id']);lesson=meta['by_id'][item['id']]
        practice=number(item.get('practice_seconds',0))
        raw_tracked=number(item.get('tracked_seconds',0));raw_reps=count(item.get('observed_repetitions',0))
        tracked=min(practice,raw_tracked) if lesson['observable'] else 0.
        reps=raw_reps if meta['repetition_counting'] and lesson['observable'] else 0
        lessons.append(dict(id=item['id'],name=lesson['name'],practice_seconds=practice,
            tracked_seconds=tracked,observed_repetitions=reps,observable=bool(lesson['observable']),
            visits=count(item.get('visits',0))))
    timed=report.get('timer_finished_lessons',[])
    if not isinstance(timed,list) or len(timed)>len(meta['lessons']) or any(not isinstance(ident,str) for ident in timed):
        raise ValueError('Invalid timed lesson list')
    return dict(schema=1,practice_id=ident,course_id=meta['id'],mode=report.get('mode') if report.get('mode') in ('learn','follow','free') else 'learn',
                active_seconds=number(report.get('active_seconds',0)),lessons=lessons,
                timer_finished_lessons=[item['id'] for item in meta['lessons'] if item['id'] in timed],
                repetition_counting=meta['repetition_counting'])

def summarize(records,total,course_id):
    meta=course(course_id)
    combined={item['id']:dict(id=item['id'],name=item['name'],practice_seconds=0.,tracked_seconds=0.,
                              observed_repetitions=0,observable=bool(item['observable'])) for item in meta['lessons']}
    for report in records:
        for item in report['lessons']:
            if item['id'] not in combined:continue
            row=combined[item['id']]
            for key in ('practice_seconds','tracked_seconds','observed_repetitions'):row[key]+=item[key]
    recorded=[row for row in combined.values() if row['practice_seconds']>0]
    # Some lessons deliberately do not collect this statistic (all Tai Chi
    # forms, plus Baduanjin forms whose motion cannot be assessed in 2D).
    # Their stored zero must not be described as failed or weak detection.
    eligible=[row for row in recorded if row['observable']]
    observed=[row['id'] for row in eligible if row['tracked_seconds']>=1.]
    unobserved=[row['id'] for row in eligible if row['id'] not in observed]
    unsupported=[row['id'] for row in recorded if not row['observable']]
    title=meta['title']
    if not records:
        summary=(f'尚无归属当前用户的{meta["subtitle"]}跟练记录。'
                 '仍可按本课程的具体步骤开始练习；以下是课程指导，不是对尚未观测动作的判断。')
    else:
        summary=f'最近 {len(records)} 次已保存记录中，{len(recorded)} 式经过跟练阶段。'
        if eligible:
            summary+=f'其中 {len(observed)} 式有至少 1 秒可用关节观测。'
        if unsupported:
            summary+=('本课程有不自动评定的动作，相关记录未统计可用观测时长或完整动作次数；'
                      '零值不表示没有识别到动作。')
        if unobserved:
            summary+='可用观测较少的阶段仍提供本式步骤建议，不据此判断做错或未做。'
        elif not recorded:
            summary+='这批记录尚未进入跟练阶段，仍可按本式步骤开始练习。'
        summary+='可用观测不代表动作标准；计时经过也不证明动作完成。'
    first=_entry_lesson(meta)
    focus=next((meta['by_id'][ident] for ident in unobserved),
               next((meta['by_id'][row['id']] for row in recorded),first))
    help=dict(title=title+' · 记录说明',summary=summary,tips=[
        '以下是“'+focus['name']+'”的步骤建议；点位不清晰时也可参考，不据此断言动作有误。',
        *_steps(focus)])
    return dict(course_id=course_id,course_title=title,report_count=len(records),total_reports=total,
                records_present=bool(records),observations_ready=bool(observed),recorded_lessons=recorded,
                observed_lessons=observed,unobserved_lessons=unobserved,
                assessment_not_supported_lessons=unsupported,
                summary=summary,local_help=help)

def _entry_lesson(meta):
    """Begin with an actionable form, rather than only camera preparation."""
    return meta['by_id']['raise'] if meta['id']=='baduanjin' else meta['lessons'][0]

def _steps(lesson):
    """Public course instructions, never inferred corrections or raw poses."""
    return [f'步骤 {index+1}：{text}' for index,text in enumerate(lesson['instructions'])]

def _plan(prefix,lesson,title,introduction):
    return dict(id=prefix+'_'+lesson['id'],title=title,tips=[introduction,*_steps(lesson)])

def evidence(snapshot,preset='next'):
    if preset not in ('next','review','demo'):raise AIError('invalid_preset','请选择后续练习、复习或先看示范。')
    meta=course(snapshot['course_id']);recorded={row['id'] for row in snapshot['recorded_lessons']}
    first=_entry_lesson(meta)
    next_lesson=(next((row for row in meta['lessons'] if row['id'] not in recorded),first)
                 if recorded else first)
    review_lesson=next((row for row in meta['lessons'] if row['id'] in snapshot['unobserved_lessons']),
                       next((row for row in meta['lessons'] if row['id'] in recorded),first))
    plans=[
        _plan('next',next_lesson,'下一式步骤建议',
              '在“选择动作”中打开“'+next_lesson['name']+'”，先看真人示范，再按以下步骤缓慢跟练。'),
        _plan('review',review_lesson,'本式练习建议',
              '以下为“'+review_lesson['name']+'”的课程步骤，供参考；点位不清晰也可继续获得建议，不表示你做错了。'),
        _plan('demo',next_lesson,'示范与分步练习',
              '使用“分解学习”查看“'+next_lesson['name']+'”；可逐段暂停看清，再按以下步骤尝试。')]
    facts=[dict(id='health.records',text=snapshot['summary']),
           dict(id='health.scope',text='这些是课程阶段和可见关节的记录，不是专业动作评分、身体检查或医疗诊断。')]
    if snapshot['recorded_lessons']:
        names='、'.join(row['name'] for row in snapshot['recorded_lessons'])
        facts.append(dict(id='health.lessons',text='跟练阶段有记录的动作：'+names+'。'))
    if snapshot['unobserved_lessons']:
        names='、'.join(meta['by_id'][ident]['name'] for ident in snapshot['unobserved_lessons'])
        facts.append(dict(id='health.visibility',text='以下动作的可用观测较少：'+names+'；不能据此判断做错或未做，仍按对应动作步骤提供建议。'))
    unsupported=snapshot.get('assessment_not_supported_lessons',
                             [row['id'] for row in snapshot['recorded_lessons'] if not row['observable']])
    if unsupported:
        names='、'.join(meta['by_id'][ident]['name'] for ident in unsupported)
        facts.append(dict(id='health.assessment_scope',text='以下动作未自动统计可见时长或完整次数：'+names+'；这不表示识别失败，仍提供课程步骤建议。'))
    return dict(course_id=meta['id'],title=meta['title'],focus=preset,facts=facts,plans=plans,
                preferred_plan=next(row['id'] for row in plans if row['id'].startswith(preset+'_')),
                limitations=['只选择给出的事实与建议，不编造训练分钟数、次数目标、动作正确性或健康效果。',
                             '没有病史、原始姿态或照片，不能诊断、治疗、康复或推荐运动强度。'])

def result(value,proof):
    value=parse_json(value)
    if set(value)!={'fact_ids','plan_id'}:raise AIError('invalid_health','练习建议格式不正确，请重试。',502)
    facts={row['id']:row['text'] for row in proof['facts']};plans={row['id']:row for row in proof['plans']}
    picked=value['fact_ids'];plan_id=value['plan_id']
    if (not isinstance(picked,list) or not 1<=len(picked)<=4 or len(picked)!=len(set(picked)) or
        any(not isinstance(ident,str) or ident not in facts for ident in picked) or
        not isinstance(plan_id,str) or plan_id not in plans):
        raise AIError('unsupported_health','建议缺少对应的本人课程记录，请重试。',502)
    plan=plans[plan_id]
    return dict(title=proof['title']+' · '+plan['title'],summary='\n'.join(facts[ident] for ident in picked),
                tips=list(plan['tips']),course_id=proof['course_id'],plan_id=plan_id)
