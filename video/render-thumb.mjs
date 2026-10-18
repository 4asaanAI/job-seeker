// node render-thumb.mjs <out.png> "<line1>" "<line2>"
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import path from "node:path";
const [out, line1, line2] = process.argv.slice(2);
const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
const props = { line1, line2 };
const composition = await selectComposition({ serveUrl, id: "Thumb", inputProps: props });
await renderStill({ composition, serveUrl, output: out, inputProps: props, imageFormat: "png" });
console.log("wrote", out);
