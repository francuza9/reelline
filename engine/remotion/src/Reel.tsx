import {useMemo} from 'react';
import {AbsoluteFill, Html5Audio, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {AngleRig} from './AngleRig';
import {FRONT, angleArt, angleAt, rigOf} from './angles';
import {cameraAt, cameraTransform, checkShots} from './camera';
import {Captions} from './Captions';
import {ColdOpen} from './ColdOpen';
import {gagFrames} from './gag';
import {CARD, EndCard} from './EndCard';
import {FxBackdrop, FxPopout} from './Fx';
import {
	NEUTRAL_POSE,
	OPENING,
	openerAnimation,
	burstPose,
	burstPunch,
	firstWordS,
	imagesFromFrame0,
	openingMouth,
	pushInScale,
} from './opening';
import {
	MOTION,
	NO_MOTION,
	bounceAt,
	freeGlances,
	leanAt,
	leanPose,
	powerOnWash,
	reactionAt,
	reactionLean,
	reactionPop,
	reactionPupil,
	stalkLean,
	stalksAt,
	tvGlanceAt,
	zoomScale,
} from './motion';
import {AngledScreen, Background, FaceWash, Screen, useDrift} from './Scene';
import {ShareArrow} from './ShareArrow';
import {
	RIG,
	Side,
	blinkAmount,
	blinkStarts,
	bounceCurve,
	bounceScale,
	breathScale,
	glanceBlinkStarts,
	glanceSchedule,
	mouthAt,
	pupilOffset,
	stalkAngleDeg,
} from './rig';
import {ReelProps} from './types';
import {EYE_WHITE_RATIO, Host, restMouth} from '@channel/remotion/Mascot';

const BACKGROUND = '#87CEFA';

// The video: the audio (with a cold open, the gag's sound and then the reel's mix: reelline/media/gags.py
// with_gag()), the cold open (remotion/src/ColdOpen.tsx) on its frames, and the reel from the next one.
// The reel plays in a Sequence there, so its frames, its times and its length (useVideoConfig()) are
// what they are without a gag: captions, cards, the camera and its angles, the end card and the share
// arrow all start with it.
export const Reel: React.FC<ReelProps> = (props) => {
	const {fps, durationInFrames} = useVideoConfig();
	const gag = props.gag ?? null;
	const audio = <Html5Audio src={staticFile(props.audioSrc)} />;
	if (!gag) {
		return (
			<>
				{audio}
				<ReelBody {...props} />
			</>
		);
	}
	const n = gagFrames(gag.durationS, fps);
	return (
		<AbsoluteFill style={{backgroundColor: BACKGROUND}}>
			{audio}
			<Sequence durationInFrames={n} name="ColdOpen">
				<ColdOpen gag={gag} background={props.background} screen={props.screen} />
			</Sequence>
			<Sequence from={n} durationInFrames={Math.max(1, durationInFrames - n)} name="Reel">
				<ReelBody {...props} />
			</Sequence>
		</AbsoluteFill>
	);
};

const ReelBody: React.FC<ReelProps> = ({
	mouthCues,
	anchors,
	mood,
	loudness,
	captions,
	background,
	screen,
	cards,
	screenImages,
	outro,
	opening,
	fx = [],
	motion = NO_MOTION,
	share = null,
}) => {
	const frame = useCurrentFrame();
	const {fps, durationInFrames, width, height} = useVideoConfig();

	const drift = useDrift(background);

	// Captions must be gone when the end card opens (reelline/media/outro.py places it so).
	const lateCaption = outro ? captions.find((c) => c.chunk_end > outro.start + 1e-6) : undefined;
	if (outro && lateCaption) {
		throw new Error(`Caption chunk ending at ${lateCaption.chunk_end}s overlaps the outro starting at ${outro.start}s`);
	}

	// While a fact graphic's card is out the captions are one line (scripts/captions.py
	// one_line), and no card is out once the end card opens (reelline/media/fx.py placed()).
	const tallCaption = captions.find((c) => c.lines.length > 1 && fx.some((e) => c.chunk_start < e.end && c.chunk_end > e.start));
	if (tallCaption) {
		throw new Error(`Caption chunk ${tallCaption.chunk_start}-${tallCaption.chunk_end}s has ${tallCaption.lines.length} lines while a fact graphic is out`);
	}
	const lateFx = outro ? fx.find((e) => e.end > outro.start + 1e-6) : undefined;
	if (outro && lateFx) {
		throw new Error(`Fact graphic ending at ${lateFx.end}s overlaps the outro starting at ${outro.start}s`);
	}
	// The frame-0 card (reelline/media/fx.py planned()) is drawn settled from its start: it must be frame 0.
	const lateFrame0 = fx.find((e) => e.frame0 && e.start > 1e-6);
	if (lateFrame0) {
		throw new Error(`The frame-0 card (paragraph ${lateFrame0.paragraph ?? '?'}) starts at ${lateFrame0.start}s, not 0`);
	}
	// The camera's shots (remotion/src/camera.ts): known, alternating, only cardShots (and
	// cardAngles) while a card is out, arrowAngles while the share arrow is up, every one inside
	// motion.json framing.safe, an angle the background has art for.
	useMemo(
		() => checkShots(motion.shots, fx, anchors, screen, width, height, share, background),
		[motion.shots, fx, anchors, screen, width, height, share, background],
	);

	const t = frame / fps;
	// The motion pass (remotion/src/motion.ts): a reaction pose after a punchline, a glance up
	// at the TV, a push-in on the punchline, an eye-stalk reaction, a lean-in, a bounce.
	const reaction = reactionAt(motion.reactions, t);
	const w = reaction?.weight ?? 0;
	const look = tvGlanceAt(motion.glances, t);
	const stalk = stalksAt(motion.stalks, t);
	const sw = stalk?.weight ?? 0;
	const held = Math.min(1, sw);
	const lean = leanPose(leanAt(motion.leans, t));
	const squash = bounceAt(motion.bounces, t);
	const camera = cameraAt(motion.shots, t);
	// The camera's angle (remotion/src/angles.ts): another rig, and for an angled one the room's 3/4 art.
	const angle = angleAt(motion.shots, t);
	const rig = angle === FRONT ? null : rigOf(angle);
	const art = rig?.scene === 'angled' ? angleArt(background, angle) : null;

	const inOutro = outro !== null && t >= outro.start;
	// Once the end card has opened over the whole frame, the scene under it isn't drawn.
	const covered = outro !== null && t >= outro.start + CARD.reveal.durationS + 1 / fps;
	// No lip movement in the outro: the mood's rest mouth.
	const mouth = inOutro ? 'X' : openingMouth(mouthCues, t, mouthAt(mouthCues, t));
	// A reaction's rest mouth while it's mostly in (never over the end card: plan()).
	const reactionMouth = reaction && w > 0.5 ? reaction.pose.mouth : null;
	const mouthImage = mouth === 'X' ? (reactionMouth ?? restMouth(anchors, mood)) : mouth;

	const bounce = useMemo(() => bounceCurve(loudness, mouthCues, fps), [loudness, mouthCues, fps]);
	const bodyScaleY =
		breathScale(t) * bounceScale(bounce, frame) * (reaction ? reactionPop(reaction) : 1) * (1 + squash) * lean.bodyScaleY;
	const bodyScaleX = (1 - squash * MOTION.bounce.widthRatio) * lean.bodyScaleX;

	const glances = useMemo(
		() => freeGlances(glanceSchedule(durationInFrames / fps), motion, RIG.glance.returnS + RIG.glance.settleS),
		[durationInFrames, fps, motion],
	);
	const blinks = useMemo(
		() => [...blinkStarts(durationInFrames, fps), ...glanceBlinkStarts(glances, fps)],
		[durationInFrames, fps, glances],
	);
	const lid = Math.max(blinkAmount(blinks, frame), reaction ? w * reaction.pose.lid : 0, stalk ? held * stalk.pose.lid : 0);

	const whiteRadius = anchors.eyes.left.radius_inner * EYE_WHITE_RATIO;
	const base = pupilOffset(glances, t, whiteRadius);
	// The eyes go where the reaction, else the stalk reaction, else the glance up at the TV takes them.
	const target = reaction ? reactionPupil(reaction) : stalk ? stalk.pose.pupil : (rig?.tvPupil ?? MOTION.tvGlance.pupil);
	const k = reaction ? w : stalk ? held : look;
	const pupil: [number, number] = [
		base[0] + k * (target[0] * whiteRadius - base[0]),
		base[1] + k * (target[1] * whiteRadius - base[1]),
	];
	const stalkAngle: Record<Side, number> = {
		left: stalkAngleDeg('left', t) + (reaction ? reactionLean(reaction, 'left') : 0) + (stalk ? stalkLean(stalk, 'left') : 0),
		right: stalkAngleDeg('right', t) + (reaction ? reactionLean(reaction, 'right') : 0) + (stalk ? stalkLean(stalk, 'right') : 0),
	};
	const stalkLift =
		(1 + look * MOTION.tvGlance.stalkStretch + w * (reaction?.pose.stalkStretch ?? 0) + sw * (stalk?.pose.stretch ?? 0)) *
		lean.stalkStretch;
	const eyeGrow = (1 + sw * (stalk?.pose.eyeGrow ?? 0)) * lean.eyeScale;
	const pupilShrink = 1 + held * ((stalk?.pose.pupilScale ?? 1) - 1);

	// The opening (remotion/src/opening.ts): the first picture from frame 0, the
	// push-in, and the burst on the first word.
	const images = useMemo(() => imagesFromFrame0(screenImages, screen.image.crossfadeFrames / fps), [screenImages, screen, fps]);
	const t0 = useMemo(() => firstWordS(captions, mouthCues), [captions, mouthCues]);
	const animation = useMemo(() => openerAnimation(opening), [opening]);
	const pose = animation ? burstPose(t, t0, animation) : NEUTRAL_POSE;
	const punch = animation ? burstPunch(t, t0, fps, animation) : {x: 0, y: 0, scale: 1};
	const sceneTransform = `translate(${punch.x}px, ${punch.y}px) scale(${pushInScale(t) * punch.scale})`;

	return (
		<AbsoluteFill style={{backgroundColor: BACKGROUND}}>
			{/* The camera's shot: a crop of the scene (not the text, the cards or the end card). */}
			<AbsoluteFill style={{...cameraTransform(camera), visibility: covered ? 'hidden' : undefined}}>
				{/* The scene (not the text) takes the push-in and the burst's punch. */}
				<AbsoluteFill style={{transform: sceneTransform, transformOrigin: OPENING.pushIn.origin}}>
					{/* The punchline push-in, on the host's face (plan() keeps it clear of fx and the end card). */}
					<AbsoluteFill style={{transform: `scale(${zoomScale(motion.zooms, t)})`, transformOrigin: MOTION.zoom.origin}}>
						<Background src={background} drift={drift} file={art?.file} />
						{art ? (
							<AngledScreen images={images} drift={drift} spec={screen} art={art}>
								<FaceWash spec={screen} color={MOTION.tvPowerOn.color} opacity={powerOnWash(t)} />
							</AngledScreen>
						) : (
							<Screen cards={cards} images={images} drift={drift} spec={screen}>
								<FaceWash spec={screen} color={MOTION.tvPowerOn.color} opacity={powerOnWash(t)} />
							</Screen>
						)}
						{fx.map((e) => (
							<FxBackdrop key={`dim-${e.start}`} event={e} />
						))}
						{rig ? (
							<AngleRig
								name={angle}
								rig={rig}
								mouthImage={mouthImage}
								bodyScaleY={bodyScaleY * pose.bodyScaleY}
								stalkStretch={pose.stalkStretch * stalkLift}
								angle={stalkAngle}
								lid={Math.max(lid, pose.lid)}
								pupil={[pupil[0] / whiteRadius, pupil[1] / whiteRadius]}
								pupilScale={pose.pupilScale * pupilShrink}
								pupilCross={held * (stalk?.pose.cross ?? 0)}
							/>
						) : (
							<Host
								anchors={anchors}
								mouthImage={mouthImage}
								bodyScaleY={bodyScaleY * pose.bodyScaleY}
								bodyScaleX={bodyScaleX * pose.bodyScaleX}
								stalkStretch={pose.stalkStretch * stalkLift}
								eyeScale={pose.eyeScale * eyeGrow}
								pupilScale={pose.pupilScale * pupilShrink}
								pupilCross={held * (stalk?.pose.cross ?? 0) * whiteRadius}
								angle={stalkAngle}
								lid={Math.max(lid, pose.lid)}
								pupil={pupil}
							/>
						)}
					</AbsoluteFill>
				</AbsoluteFill>
			</AbsoluteFill>
			{fx.map((e) => (
				<FxPopout key={`fx-${e.start}`} event={e} spec={screen} drift={drift} anchors={anchors} camera={camera} />
			))}
			<Captions chunks={captions} fromFrame0={OPENING.captionFromFrame0} />
			{outro ? (
				<Sequence from={Math.ceil(outro.start * fps)} name="EndCard">
					<EndCard facts={outro.facts} />
					{/* Outro sting SFX hook: the sting is mixed into audioSrc at this Sequence's first frame
					    (reelline/media/sfx/events.py outro_hook), so nothing plays here. */}
				</Sequence>
			) : null}
			<ShareArrow share={share} />
		</AbsoluteFill>
	);
};
