/**
 * Remotion render configuration.
 *
 * Determinism obligations live here (context.md §7): the browser build is
 * pinned, GPU rasterisation is off so raster output does not vary with the
 * host's graphics stack, and image quality settings are fixed.
 */
import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(90);
Config.setOverwriteOutput(true);

// Software rasterisation: the default "swiftshader" path is host-independent,
// where a real GPU would make frame output vary by machine.
Config.setChromiumOpenGlRenderer('swiftshader');

// Single-pass, no concurrency-dependent behaviour in output.
Config.setConcurrency(1);
