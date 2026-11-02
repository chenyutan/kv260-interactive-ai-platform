"""Optional, bounded cloud assistance; local gameplay never depends on it."""
from .contracts import AIError
from .service import AIService

__all__ = ('AIService', 'AIError')
