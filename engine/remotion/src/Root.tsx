import {ALL_FORMATS, Input, UrlSource} from 'mediabunny';
import {CalculateMetadataFunction, Composition, Still, staticFile} from 'remotion';
import anchors from '@channel/assets/anchors.json';
import screenSpec from './screen.json';
import {Cover} from './Cover';
import {EndCardStill} from './EndCard';
import {FX} from './Fx';
import {FxPreview} from './FxPreview';
import {Reel} from './Reel';
import {ReelProps} from './types';

const FPS = 30;

const calculateMetadata: CalculateMetadataFunction<ReelProps> = async ({props}) => {
	const input = new Input({
		source: new UrlSource(staticFile(props.audioSrc)),
		formats: ALL_FORMATS,
	});
	const seconds = await input.computeDuration();
	// The outro may run past the audio (no music under a voice-only track); a cold open comes before
	// the reel (its times are the reel's own: remotion/src/Reel.tsx).
	return {durationInFrames: Math.ceil(Math.max(seconds, (props.outro?.end ?? 0) + (props.gag?.durationS ?? 0)) * FPS)};
};

// Studio preview defaults only: renders always pass the background and its screen spec
// (reelline/media/backgrounds.py). JSON arrays infer as number[], not tuples.
const ANCHORS = anchors as unknown as ReelProps['anchors'];

export const RemotionRoot: React.FC = () => {
	return (
		<>
			<Composition
				id="Reel"
				component={Reel}
				width={1080}
				height={1920}
				fps={FPS}
				// Replaced by calculateMetadata from the audio length (and the outro).
				durationInFrames={1}
				defaultProps={{
					audioSrc: 'smoke.wav',
					mouthCues: [],
					anchors: ANCHORS,
					mood: 'smug',
					loudness: [],
					captions: [],
					background: 'bedroom_night.png',
					screen: screenSpec,
					cards: [],
					screenImages: [],
					outro: null,
					opening: null,
				}}
				calculateMetadata={calculateMetadata}
			/>
			<Still
				id="Cover"
				component={Cover}
				width={1080}
				height={1920}
				defaultProps={{
					anchors: ANCHORS,
					mood: 'smug',
					background: 'bedroom_night.png',
					screen: screenSpec,
					image: null,
					headline: 'უფასო AI აკადემია თბილისში',
					units: ['უფასო', 'AI', 'აკადემია', 'თბილისში'],
					debugSafeArea: false,
				}}
			/>
			{/* The end card as it holds (scripts/render_endcard_preview.py), for a design check. */}
			<Still
				id="EndCard"
				component={EndCardStill}
				width={1080}
				height={1920}
				defaultProps={{
					facts: [
						{field: 'name', text: 'უფასო AI აკადემია'},
						{field: 'what', text: '3 თვე ისწავლი, როგორ ააწყო AI პროდუქტი'},
						{field: 'who', text: '18-29 წლის სტუდენტები'},
						{field: 'deadline', text: '15 ნოემბრამდე'},
						{field: 'cost', text: 'უფასო'},
						{field: 'apply', text: 'edu.aris.ge'},
					],
					debugSafeArea: false,
				}}
			/>
			{/* Fx design preview (scripts/render_fx_preview.py); not part of the reel yet. */}
			<Composition
				id="FxPreview"
				component={FxPreview}
				width={1080}
				height={1920}
				fps={FX.preview.fps}
				durationInFrames={Math.round(FX.preview.durationS * FX.preview.fps)}
				defaultProps={{sample: 'deadline', placement: 'popout'}}
			/>
		</>
	);
};
