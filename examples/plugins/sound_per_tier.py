"""Play a different sound file per threat tier -- example EVE Alert
plugin (v2 API).

EVE Alert's own alarm sound is fixed per alarm type (Enemy/Faction); this
plugin adds a SECOND sound cue whose choice depends on the computed
threat score (#141), so a CRITICAL (score 7-10) pull sounds different
from a CAUTION one, without touching EVE Alert's own Settings > Sounds.

Uses the same sounddevice + soundfile combination EVE Alert itself uses
(already a dependency, no extra install needed) since ctx doesn't expose
its own play_sound() -- see docs/PLUGINS.md's packaging notes.

Setup: copy this file into your plugins folder, replace the three .wav
paths below with your own short sound files, restart EVE Alert.
"""

__version__ = "1.0"

SOUND_BY_LABEL = {
    "CAUTION": "C:/sounds/caution.wav",
    "HIGH": "C:/sounds/high.wav",
    "CRITICAL": "C:/sounds/critical.wav",
}


def on_threat_score(ctx, assessment):
    path = SOUND_BY_LABEL.get(assessment.label)
    if not path:
        return
    try:
        import soundfile as sf
        import sounddevice as sd

        data, samplerate = sf.read(path, dtype="float32")
        sd.play(data, samplerate)
    except Exception as exc:
        ctx.log(f"sound_per_tier: could not play {path}: {exc}")
