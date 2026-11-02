"""Reuse the suite's original chase score and bounded asynchronous Cube route."""
from runner.audio import RunnerSound

class KartSound(RunnerSound):
    """The approved original runner composition is shared without a duplicate WAV."""
    def events(self,events):
        mapping={'pickup':'collect','item':'collect','drift_boost':'collect',
                 'hit':'hit','rail':'hit','shield_break':'shield_break','finished':'over',
                 'count':'lane','go':'power','lap':'power'}
        translated=[]
        for event in events:
            kind=mapping.get(event.get('kind'))
            if kind:translated.append(dict(event,kind=kind,item_kind='power'))
        super().events(translated)
