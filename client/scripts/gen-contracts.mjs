// Contracts → TypeScript (issue E1.2). Reads one bundled JSON Schema on stdin, writes TS to stdout.
// Run through `make contracts` (python -m sbam_contracts.gen), never on its own: the Python side owns
// the header, the source hash and the file write.
import { compile } from "json-schema-to-typescript";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const schema = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const ts = await compile(schema, "SbamContracts", {
  bannerComment: "",
  additionalProperties: false, // no `[k: string]: unknown` unless the schema allows extra keys
  unknownAny: true, // untyped JSON values become `unknown`, never `any`
  strictIndexSignatures: true,
  unreachableDefinitions: true,
  declareExternallyReferenced: true,
  format: true,
});

process.stdout.write(ts.replace(/\r\n/g, "\n"));
