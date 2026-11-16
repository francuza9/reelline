import {AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {GagCamera, GagLoop, gagCamera, gagFlash, loopPose} from './gag';
import gagsJson from './gags.json';
import {imagesFromFrame0} from './opening';
import {Background, Drift, SCENE, Screen} from './Scene';
import {GagPose, GagProps, ScreenSpec} from './types';

// gags.json as the drawing reads it (reelline/media/gags.py checks the file).
const GAGS = gagsJson as unknown as {timing: {holdFromS: number}; camera: GagCamera; gags: Record<string, {loop: GagLoop}>};

// The room holds still under the cold open (the push-in is its move): the drift's zoomed-out end.
const STILL: Drift = {scale: SCENE.drift.baseScale, x: 0, y: 0};

// The cold open (channel docs/gags.md), played in a Sequence before the reel (remotion/src/Reel.tsx): the
// room and its wall TV (the gag's picture, or the idle face), the action pose looping in two drawings
// under a slow push-in, then the caught pose with a zoom punch and a white flash, still until the cut.
// The audio (the gag's sound, then the reel's) is the composition's.
export const ColdOpen: React.FC<{gag: GagProps; background: string; screen: ScreenSpec}> = ({gag, background, screen}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const spec = GAGS.gags[gag.name];
	if (!spec) {
		throw new Error(`Unknown gag "${gag.name}" (remotion/src/gags.json gags)`);
	}
	const t = frame / fps;
	const timing = {durationS: gag.durationS, caughtS: gag.caughtS, holdFromS: GAGS.timing.holdFromS};
	const camera = gagCamera(GAGS.camera, timing, t, gag.view);
	const caught = t >= gag.caughtS;
	const pose: GagPose = caught ? gag.caught : gag.action;
	const loop = caught ? null : loopPose(spec.loop, t);
	const images = gag.screen ? imagesFromFrame0([gag.screen], screen.image.crossfadeFrames / fps) : [];
	return (
		<AbsoluteFill style={{backgroundColor: '#000000'}}>
			<AbsoluteFill style={{transform: `scale(${camera.scale})`, transformOrigin: camera.origin}}>
				<Background src={background} drift={STILL} />
				<Screen cards={[]} images={images} drift={STILL} spec={screen} />
				<Img
					src={staticFile(pose.src)}
					style={{
						position: 'absolute',
						left: pose.left,
						top: pose.top,
						width: pose.width,
						height: pose.height,
						maxWidth: 'none',
						transform: loop
							? `translate(${loop.x}px, ${loop.y}px) rotate(${loop.rotateDeg}deg) scale(${loop.scaleX}, ${loop.scaleY})`
							: undefined,
						transformOrigin: '50% 100%',
					}}
				/>
			</AbsoluteFill>
			<AbsoluteFill style={{backgroundColor: GAGS.camera.flashColor, opacity: gagFlash(GAGS.camera, timing, t)}} />
		</AbsoluteFill>
	);
};
