// Copyright 2025-2026 Eric Allen
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Parse every ```mermaid block in the given Markdown files.
//
// GitHub renders these diagrams and nothing in the Python toolchain looks at
// them, so a syntax error ships silently and is visible only once the page is
// published. Mermaid's own parser is the only authority on its syntax, so this
// asks it rather than approximating with a regular expression.
//
// Mermaid sanitises through DOMPurify and so wants a DOM before it is
// imported; jsdom supplies one. Run it through `just check-diagrams`, which
// installs both into a scratch directory.
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
// Node 24 exposes navigator as a getter-only property on globalThis.
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});

const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

const files = process.argv.slice(2);
if (files.length === 0) {
  console.error("usage: check_mermaid.mjs <markdown file>...");
  process.exit(2);
}

let checked = 0;
let failed = 0;
for (const file of files) {
  const blocks = [...readFileSync(file, "utf8").matchAll(/```mermaid\n([\s\S]*?)```/g)];
  for (const [index, match] of blocks.entries()) {
    checked++;
    try {
      await mermaid.parse(match[1]);
    } catch (error) {
      failed++;
      const detail = String(error?.message ?? error).split("\n").slice(0, 3).join(" ");
      console.error(`${file}: diagram ${index + 1} does not parse: ${detail}`);
    }
  }
}
console.log(`${checked} diagram(s) checked, ${failed} broken`);
process.exit(failed === 0 ? 0 : 1);
