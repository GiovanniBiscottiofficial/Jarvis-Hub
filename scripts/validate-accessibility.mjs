import fs from "node:fs";

const htmlFiles = [
  "ha-config/www/jarvis-rooms.html",
  "ha-config/www/lifeos.html",
  "lifeos/app/static/index.html",
];
const safeDomFiles = [
  "ha-config/www/jarvis-rooms.html",
  "ha-config/www/lifeos.html",
];
const failures = [];

function fail(file, message) {
  failures.push(`${file}: ${message}`);
}

for (const file of htmlFiles) {
  const html = fs.readFileSync(file, "utf8");
  if (!/<html[^>]+lang=["'][^"']+["']/i.test(html)) {
    fail(file, "missing document language");
  }
  if (!/<meta[^>]+name=["']viewport["']/i.test(html)) {
    fail(file, "missing responsive viewport metadata");
  }

  const ids = [...html.matchAll(/\bid=["']([^"']+)["']/gi)].map((match) => match[1]);
  const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
  if (duplicates.length) fail(file, `duplicate IDs: ${[...new Set(duplicates)].join(", ")}`);

  for (const match of html.matchAll(/<img\b[^>]*>/gi)) {
    if (!/\balt=["'][^"']*["']/i.test(match[0])) fail(file, "image missing alt text");
  }
  for (const match of html.matchAll(/<iframe\b[^>]*>/gi)) {
    if (!/\btitle=["'][^"']+["']/i.test(match[0])) fail(file, "iframe missing title");
  }
  for (const match of html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/gi)) {
    const attributes = match[1];
    const text = match[2].replace(/<[^>]+>/g, "").trim();
    if (!text && !/\baria-label=["'][^"']+["']/i.test(attributes)) {
      fail(file, "button missing an accessible name");
    }
  }
}

for (const file of safeDomFiles) {
  const source = fs.readFileSync(file, "utf8");
  for (const pattern of [/\.innerHTML\s*=/, /insertAdjacentHTML\s*\(/, /document\.write\s*\(/]) {
    if (pattern.test(source)) fail(file, `unsafe DOM sink matched ${pattern}`);
  }
  if (/<[^>]+\son[a-z]+\s*=/i.test(source)) fail(file, "inline event handler detected");
}

if (failures.length) {
  failures.forEach((failure) => process.stderr.write(`${failure}\n`));
  process.exit(1);
}

process.stdout.write("Accessibility structure and safe DOM checks passed.\n");
