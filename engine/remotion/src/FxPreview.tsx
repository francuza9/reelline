import {useMemo} from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import anchors from '@channel/assets/anchors.json';
import {Captions} from './Captions';
import {FX, FxBackdrop, FxOnTv, FxPopout} from './Fx';
import {Background, Screen, useDrift} from './Scene';
import screenSpec from './screen.json';
import {
	Side,
	blinkAmount,
	blinkStarts,
	breathScale,
	glanceBlinkStarts,
	glanceSchedule,
	pupilOffset,
	stalkAngleDeg,
} from './rig';
import {Anchors, CaptionChunk, FxData, FxEvent, FxPreviewProps} from './types';
import {EYE_WHITE_RATIO, Host, restMouth} from '@channel/remotion/Mascot';

// JSON arrays infer as number[], not tuples; the samples' template names as string.
const ANCHORS = anchors as unknown as Anchors;
const SAMPLES = FX.preview.samples as unknown as Record<string, {data: FxData; caption: string}>;

// The sample caption as one chunk on screen throughout, its words lit in turn from
// captionStartS until shortly before the end.
const sampleChunk = (caption: string, durationS: number): CaptionChunk => {
	const words = caption.split(/\s+/);
	const from = FX.preview.captionStartS;
	const step = (durationS - 0.3 - from) / words.length;
	return {
		chunk_start: 0,
		chunk_end: durationS + 1,
		lines: [words.map((text, i) => ({text, start: from + i * step, end: from + (i + 1) * step}))],
	};
};

// Design preview of one fx sample (fx.json preview.samples) in one placement, in the
// real scene: the night background, the wall TV (idle face), the host idling on his chair
// with the rest mouth and a sample caption. Rendered by scripts/render_fx_preview.py.
export const FxPreview: React.FC<FxPreviewProps> = ({sample: name, placement}) => {
	const frame = useCurrentFrame();
	const {fps, durationInFrames} = useVideoConfig();
	const P = FX.preview;
	const sample = SAMPLES[name];
	if (!sample) {
		throw new Error(`FxPreview: no sample "${name}" in fx.json preview.samples`);
	}
	const drift = useDrift(P.background);
	const durationS = durationInFrames / fps;
	const event: FxEvent = useMemo(
		() => ({data: sample.data, placement, start: P.fxStartS, end: P.fxEndS}),
		[sample, placement, P.fxStartS, P.fxEndS],
	);
	const captions = useMemo(() => [sampleChunk(sample.caption, durationS)], [sample, durationS]);

	// Idle: breathing, blinks, glances and the stalks' sway; no talking.
	const t = frame / fps;
	const glances = useMemo(() => glanceSchedule(durationS), [durationS]);
	const blinks = useMemo(() => [...blinkStarts(durationInFrames, fps), ...glanceBlinkStarts(glances, fps)], [durationInFrames, fps, glances]);
	const whiteRadius = ANCHORS.eyes.left.radius_inner * EYE_WHITE_RATIO;
	const angle: Record<Side, number> = {left: stalkAngleDeg('left', t), right: stalkAngleDeg('right', t)};

	return (
		<AbsoluteFill style={{backgroundColor: '#000000'}}>
			<Background src={P.background} drift={drift} />
			<Screen cards={[]} images={[]} drift={drift} spec={screenSpec}>
				<FxOnTv event={event} spec={screenSpec} />
			</Screen>
			<FxBackdrop event={event} />
			<Host
				anchors={ANCHORS}
				mouthImage={restMouth(ANCHORS, P.mood)}
				bodyScaleY={breathScale(t)}
				angle={angle}
				lid={blinkAmount(blinks, frame)}
				pupil={pupilOffset(glances, t, whiteRadius)}
			/>
			<FxPopout event={event} spec={screenSpec} drift={drift} />
			<Captions chunks={captions} />
		</AbsoluteFill>
	);
};
