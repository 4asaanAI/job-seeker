// node render-cards.mjs <outDir> <captions.json>   (captions.json: {"name": "text", ...})
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { readFileSync } from "node:fs";
import path from "node:path";
const [outDir, capFile, outFile] = process.argv.slice(2);
const caps = JSON.parse(readFileSync(capFile, "utf8"));
const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
const still = async (id, out, props = {}, png = true) => {
  const composition = await selectComposition({ serveUrl, id, inputProps: props });
  await renderStill({ composition, serveUrl, output: path.join(outDir, out), inputProps: props,
    imageFormat: "png" });
  console.log("wrote", out);
};
await still("Intro", "intro.png");
await still("Outro", "outro.png");
for (const [name, text] of Object.entries(caps)) await still("Caption", `cap-${name}.png`, { text });

if (outFile) {
  const outs = JSON.parse(readFileSync(outFile, "utf8"));
  for (const [name, props] of Object.entries(outs)) await still("Output", `out-${name}.png`, props);
}
