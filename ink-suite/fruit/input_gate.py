"""Menu candidates suspend cutting input, never the fruit simulation clock."""

UNAVAILABLE=frozenset(('lost','camera_unavailable','camera_link_slow','error','failed',
                       'stopped','stopping','unavailable','service_unavailable',
                       'restarting','disconnected','offline','initializing','loading','starting','warming_up'))

def pose_allowed(state):
    if not isinstance(state,dict) or state.get('stale') or state.get('available') is False:
        return False
    if state.get('phase') in UNAVAILABLE:return False
    link=state.get('camera_link')
    if isinstance(link,dict) and link.get('present') is False:return False
    suite=state.get('suite')
    if isinstance(suite,dict):
        if suite.get('active') is False or suite.get('menu') or suite.get('control_active'):
            return False
        if suite.get('mode') not in (None,'fruit'):return False
    return True

def consume_pose(consumer,state):
    if not pose_allowed(state):
        consumer.reset()
        return []
    return consumer.consume(state)
