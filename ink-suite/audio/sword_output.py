"""Compatibility entry point for 御剑's verified Cube output."""
import time

from .game_output import CubeGameAudioDirector


class SwordAudioDirector(CubeGameAudioDirector):
    """Keep the existing sword constructor and stream identity."""
    def __init__(self, *, enabled=True, asset_dir=None, inventory_fn=None,
                 physical_checker=None, backend_factory=None, clock=time.monotonic,
                 retry_interval=3.0, server_provider=None, max_voices=6, queue_limit=32):
        super().__init__('sword', enabled=enabled, asset_dir=asset_dir,
            inventory_fn=inventory_fn, physical_checker=physical_checker,
            backend_factory=backend_factory, clock=clock, retry_interval=retry_interval,
            server_provider=server_provider, max_voices=max_voices, queue_limit=queue_limit)
