import {Config} from '@remotion/cli/config';
import path from 'node:path';
import encode from './encode.json';


// The MP4's encoding: remotion/encode.json, shared with QA (reelline/pipeline/qa.py).
Config.setVideoImageFormat(encode.imageFormat as 'png' | 'jpeg');
if (encode.imageFormat === 'jpeg') {
	Config.setJpegQuality(encode.jpegQuality);
}
Config.setCodec(encode.codec as 'h264');
Config.setVideoBitrate(encode.videoBitrate);
Config.setEncodingMaxRate(encode.encodingMaxRate);
Config.setEncodingBufferSize(encode.encodingBufferSize);
Config.setX264Preset(encode.x264Preset as 'medium');
Config.setPixelFormat(encode.pixelFormat as 'yuv420p');
Config.setColorSpace(encode.colorSpace as 'bt709');
Config.setOverwriteOutput(true);
// Parallel frames; unset: Remotion's default (half the CPUs). The config's render.remotion_concurrency,
// passed by scripts/render_reel.py, so a render can be held to a server's memory and CPU caps.
const concurrency = Number(process.env.REELLINE_REMOTION_CONCURRENCY);
if (concurrency > 0) {
	Config.setConcurrency(concurrency);
}

// The channel's own files. `@channel/...` is resolved in the channel folder (REELLINE_CHANNEL_DIR, set by the
// renderer: graphics/*.json, assets/anchors.json, remotion/Mascot.tsx) and, for a file the channel doesn't
// have, in src/channel_default, so the project builds with no channel at all (tsconfig.json has the same
// paths for the type check). `@engine/...` is src/, for a channel's component that uses the engine's modules.
// (The renderer runs Remotion from this folder.)
const channelDir = process.env.REELLINE_CHANNEL_DIR;
Config.overrideWebpackConfig((config) => ({
	...config,
	resolve: {
		...config.resolve,
		alias: {
			...config.resolve?.alias,
			'@channel': [...(channelDir ? [path.resolve(channelDir)] : []), path.resolve(process.cwd(), 'src/channel_default')],
			'@engine': path.resolve(process.cwd(), 'src'),
		},
		// A channel's component imports remotion and react from this project's node_modules.
		modules: [path.resolve(process.cwd(), 'node_modules'), ...(config.resolve?.modules ?? ['node_modules'])],
	},
}));
