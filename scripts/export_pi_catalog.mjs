// Rebuild from the pinned official package without authentication or network calls.
// node scripts/export_pi_catalog.mjs --node-modules /path/to/node_modules
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const options = process.argv.slice(2);
if (options.length !== 2 || options[0] !== "--node-modules") {
  throw new Error("Usage: node scripts/export_pi_catalog.mjs --node-modules /path/to/node_modules");
}
const packageRoot = resolve(options[1], "@earendil-works/pi-ai");
const metadata = JSON.parse(await readFile(resolve(packageRoot, "package.json"), "utf8"));
if (metadata.name !== "@earendil-works/pi-ai" || metadata.version !== "1.4.0") {
  throw new Error("Expected the pinned @earendil-works/pi-ai 1.4.0 package");
}
const { builtinProviders, getBuiltinModels, getBuiltinModelDataGeneratedAt } = await import(
  pathToFileURL(resolve(packageRoot, "dist/providers/all.js")).href
);
const exclusions = {
  "amazon-bedrock": "Requires AWS authentication and a region beyond the single-key form.",
  azure: "Requires an Azure deployment endpoint beyond the built-in single-key form.",
  "cloudflare-ai-gateway": "Requires Cloudflare account and gateway identifiers.",
  "cloudflare-workers-ai": "Requires a Cloudflare account identifier.",
  "openai-codex": "Uses OAuth rather than the API-key configuration supported here.",
  typesafe: "Contains no chat models.",
};
const credentialLabels = {
  "github-copilot": "GitHub Copilot token",
  "google-vertex": "Google Cloud API key",
};
const notes = {
  "github-copilot": "Requires a Copilot token; an ordinary GitHub personal access token is not interchangeable.",
  "google-vertex": "This form uses the Google Cloud API-key path, not Application Default Credentials.",
};
const compare = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
const providers = [];
const excluded = [];
for (const provider of builtinProviders().sort(compare)) {
  const models = getBuiltinModels(provider.id).map(({ id, name }) => ({ id, name })).sort(compare);
  const reason = exclusions[provider.id] || (!provider.auth?.apiKey ? "No API-key authentication path." : "")
    || (!models.length ? "Contains no chat models." : "");
  if (reason) {
    excluded.push({ id: provider.id, name: provider.name, reason });
  } else {
    providers.push({ id: provider.id, name: provider.name,
      credential_label: credentialLabels[provider.id] || "API key", notes: notes[provider.id] || "", models });
  }
}
const result = {
  version: metadata.version,
  source: "https://github.com/earendil-works/pi/tree/v1.4.0/packages/ai/src/providers",
  source_license: "MIT",
  source_copyright: "Copyright (c) 2025 Mario Zechner",
  source_license_url: "https://github.com/earendil-works/pi/blob/v1.4.0/LICENSE",
  generated_at: new Date(getBuiltinModelDataGeneratedAt()).toISOString(),
  scope: "Bundled directory only; account access and live service availability are not tested.",
  providers,
  custom_apis: [{ id: "openai-completions", name: "OpenAI Chat Completions 兼容接口" }],
  excluded,
};
await writeFile(resolve("briefing/pi_model_catalog.json"), JSON.stringify(result, null, 2) + "\n", "utf8");
console.log(JSON.stringify({ version: result.version, providers: providers.length,
  models: providers.reduce((count, provider) => count + provider.models.length, 0), excluded: excluded.length }));
