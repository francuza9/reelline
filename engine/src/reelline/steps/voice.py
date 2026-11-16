"""voice: the script's display text spoken (reelline/media/tts.py synthesize()). Library use and
channels whose renderer takes a ready voice track; the host's render makes its own (per sentence, with
the visemes), so this step isn't in its chain."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult


def run(text: str, *, tts=None, voice: str | None = None, style: str | None = None):
    """The speech (providers.base.Speech) for a text; voice: the channel's stored voice id
    (models.tts.voice_id_env) by default; style: the channel's base delivery."""
    from reelline.media.tts import synthesize, tts_style
    from reelline.providers import get_tts

    if voice is None:
        from reelline.config.credentials import load_settings

        voice = load_settings().voice_id
    return synthesize(
        tts if tts is not None else get_tts("tts"), text, voice, style if style is not None else tts_style(None)
    )


class Voice(Step):
    name = "voice"
    needs = frozenset({Artifact.SCRIPT})
    makes = frozenset({Artifact.AUDIO})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.script import display_text

        script = ctx[Artifact.SCRIPT]
        text = script if isinstance(script, str) else display_text(script.script)
        return StepResult({Artifact.AUDIO: run(text, tts=ctx.clients.get("tts"), voice=ctx.state.get("voice"))})
