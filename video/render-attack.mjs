// node render-attack.mjs <outDir> <spec.json>   (spec: {name: {head, sub, lines, tone, note}})
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { readFileSync } from "node:fs";
import path from "node:path";
const [outDir, specFile] = process.argv.slice(2);
const spec = JSON.parse(readFileSync(specFile, "utf8"));
const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
for (const [name, props] of Object.entries(spec)) {
  const composition = await selectComposition({ serveUrl, id: "Attack", inputProps: props });
  await renderStill({ composition, serveUrl, output: path.join(outDir, name + ".png"), inputProps: props, imageFormat: "png" });
  console.log("wrote", name);
}
