import fs from "node:fs";
import path from "node:path";

const roots = ["ha-config/www"];
const failures = [];

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === "community" ? [] : walk(target);
    return entry.isFile() && entry.name.endsWith(".html") ? [target] : [];
  });
}

for (const root of roots) {
  for (const file of walk(root)) {
    const html = fs.readFileSync(file, "utf8");
    const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
    scripts.forEach((match, index) => {
      try {
        Function(match[1]);
      } catch (error) {
        failures.push(`${file} embedded script ${index + 1}: ${error.message}`);
      }
    });
  }
}

if (failures.length) {
  failures.forEach((failure) => process.stderr.write(`${failure}\n`));
  process.exit(1);
}

process.stdout.write("Embedded browser JavaScript is syntactically valid.\n");
