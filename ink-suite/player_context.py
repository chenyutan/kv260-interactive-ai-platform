"""Local visitor/source boundaries, independent of pose-person metadata.

This adapter never masks model points or changes their availability. A backend
restart or explicit local visitor change resets movement history; original
freshness, geometry and joint-confidence checks remain with their consumers.
"""

def _key(state,visitor_session=None):
    pid=state.get('pid')
    if isinstance(pid,bool) or not isinstance(pid,(int,str)) or not pid:pid=None
    session=visitor_session if visitor_session is not None else state.get('visitor_session')
    if not isinstance(session,str) or not 1<=len(session)<=128:session=None
    return (pid,session) if pid is not None or session is not None else None

def input_context(state,previous=None,visitor_session=None):
    if not isinstance(state,dict):return state,previous,False
    key=_key(state,visitor_session)
    changed=key!=previous
    result=state
    if visitor_session is not None and key is not None and key[1] is not None:
        if state.get('visitor_session')!=key[1]:result=dict(state,visitor_session=key[1])
    if changed:
        if result is state:result=dict(state)
        result['motion_history_discontinuity']=True
    return result,key,changed

def context_key(state):
    """A lesson never joins observations across backend/local visitor changes."""
    return (_key(state) if isinstance(state,dict) else None),True
