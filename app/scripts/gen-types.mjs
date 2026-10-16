// Generate src/contracts.ts from ../contracts/*.schema.json (the source of truth).
//   node scripts/gen-types.mjs           write the file
//   node scripts/gen-types.mjs --check   fail if the committed file is stale
import { compile } from "json-schema-to-typescript";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const contracts = join(here, "..", "..", "contracts");
const out = join(here, "..", "src", "contracts.ts");

const files = {
  state: "state.schema.json",
  intent: "intent.schema.json",
  ipcRequest: "ipc-request.schema.json",
  ipcResponse: "ipc-response.schema.json",
  ipcEvent: "ipc-event.schema.json",
  takeHeader: "take-header.schema.json",
  takeEvent: "take-event.schema.json",
  fixturesFile: "fixtures.schema.json",
  consoleFile: "console.schema.json",
  takesList: "ipc-response.schema.json#/$defs/takes_list",
  takeFull: "ipc-response.schema.json#/$defs/take_full",
  takeExport: "ipc-response.schema.json#/$defs/take_export",
};

// One synthetic root so shared definitions are declared exactly once.
const root = {
  title: "ChromaContracts",
  type: "object",
  additionalProperties: false,
  properties: Object.fromEntries(Object.entries(files).map(([k, f]) => [k, { $ref: f }])),
};

const banner = "/* eslint-disable */\n// GENERATED from contracts/*.schema.json by scripts/gen-types.mjs — do not edit.\n// Run `npm run gen:types` after changing a schema.\n";
const ts = await compile(root, "ChromaContracts", {
  cwd: contracts,
  bannerComment: banner,
  additionalProperties: false,
  unreachableDefinitions: false,
  ignoreMinAndMaxItems: true, // fixed-length arrays (24 channels, 8 cues) as plain arrays, not tuples
  style: { singleQuote: false, printWidth: 110 },
});

// Extra named exports for definitions the UI uses directly.
const extras = `
export type Channel = ChannelState;
export type BankName = State["bank"];
export type FaderFn = State["fader_fn"];
export type Who = State["who"];
export type ArtMode = State["art_mode"];
export type RecordMode = RecState["mode"];
`;
const result = ts + extras;

if (process.argv.includes("--check")) {
  const current = existsSync(out) ? readFileSync(out, "utf8") : "";
  if (current !== result) {
    console.error("src/contracts.ts is stale — run `npm run gen:types`");
    process.exit(1);
  }
  console.log("contracts.ts up to date");
} else {
  writeFileSync(out, result);
  console.log(`wrote ${out}`);
}
