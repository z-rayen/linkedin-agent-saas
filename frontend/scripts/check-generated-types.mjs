// Fails when src/api/schema.gen.ts is not what openapi.json generates today. CI runs it: a changed API without a
// regenerated and committed schema file fails the build, so the typed client cannot drift from the server.
import { execSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const COMMITTED = "src/api/schema.gen.ts";
const dir = mkdtempSync(join(tmpdir(), "schema-"));
const generated = join(dir, "schema.gen.ts");

try {
  execSync(`npx --yes openapi-typescript@7.13.0 openapi.json -o "${generated}"`, {
    stdio: "inherit",
  });
  if (readFileSync(generated, "utf8") !== readFileSync(COMMITTED, "utf8")) {
    console.error(
      `${COMMITTED} is out of date with openapi.json: run "npm run gen:types" and commit the result.`,
    );
    process.exitCode = 1;
  } else {
    console.log(`${COMMITTED} is up to date.`);
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
