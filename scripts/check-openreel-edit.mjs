#!/usr/bin/env node
/**
 * Verify an openreel-edit JSON with OpenReel's parsePortableEdit.
 *
 * Usage:
 *   node scripts/check-openreel-edit.mjs path/to/edit.json
 *   node scripts/check-openreel-edit.mjs edit.json --openreel /path/to/openreel-video
 *
 * Looks for the OpenReel checkout in --openreel, $OPENREEL_VIDEO,
 * or ../openreel-video relative to this repository.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");

function argValue(flag) {
  const idx = process.argv.indexOf(flag);
  if (idx >= 0 && process.argv[idx + 1]) return process.argv[idx + 1];
  return null;
}

const positional = process.argv.slice(2).filter((a) => !a.startsWith("--") && a !== argValue("--openreel"));
const editPath = positional[0] ? path.resolve(positional[0]) : null;
if (!editPath) {
  console.error("Usage: node scripts/check-openreel-edit.mjs path/to/edit.json [--openreel /path/to/openreel-video]");
  process.exit(2);
}
if (!existsSync(editPath)) {
  console.error(`Edit JSON not found: ${editPath}`);
  process.exit(2);
}

const openreel = path.resolve(
  argValue("--openreel")
    || process.env.OPENREEL_VIDEO
    || path.resolve(repoRoot, "..", "openreel-video"),
);
const coreDir = path.join(openreel, "packages", "core");
const config = path.join(coreDir, "vitest.config.ts");
const binName = process.platform === "win32" ? "vite-node.cmd" : "vite-node";
const viteNodeCandidates = [
  path.join(coreDir, "node_modules", ".bin", binName),
  path.join(openreel, "node_modules", ".bin", binName),
  path.join(openreel, "node_modules", ".pnpm", "node_modules", ".bin", binName),
];
const viteNode = viteNodeCandidates.find((candidate) => existsSync(candidate));
if (!existsSync(path.join(coreDir, "src", "editing", "portable-project.ts"))) {
  console.error(`OpenReel core not found at ${coreDir}`);
  console.error("Pass --openreel /path/to/openreel-video or set OPENREEL_VIDEO.");
  process.exit(2);
}
if (!viteNode || !existsSync(config)) {
  console.error(`vite-node or vitest.config.ts missing under ${openreel}.`);
  console.error("Install OpenReel dependencies first (pnpm install in that repo).");
  process.exit(2);
}

const work = mkdtempSync(path.join(tmpdir(), "story-cut-check-"));
const runner = path.join(work, "check-parse.ts");
writeFileSync(
  runner,
  `
import { readFileSync } from "node:fs";
import { parsePortableEdit } from "@openreel/core/editing/portable-project";

const json = readFileSync(${JSON.stringify(editPath)}, "utf8");
const doc = parsePortableEdit(json);
const clips = doc.project.timeline.tracks.flatMap((track) => track.clips);
const subs = doc.project.timeline.subtitles;
const markers = doc.project.timeline.markers;
console.log("parsePortableEdit: OK");
console.log("format=" + doc.format + " version=" + doc.version);
console.log("clips=" + clips.length + " subtitles=" + subs.length + " markers=" + markers.length);
console.log("duration=" + doc.project.timeline.duration);
console.log("media=" + doc.project.media.map((m) => m.name).join(", "));
`,
  "utf8",
);

const result = spawnSync(
  viteNode,
  ["--config", config, runner],
  { cwd: coreDir, stdio: "inherit", env: process.env, shell: process.platform === "win32" },
);
process.exit(result.status === null ? 1 : result.status);
