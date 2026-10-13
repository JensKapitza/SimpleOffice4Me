// Tauri's Android integration overrides target-specific Cargo rustflags.
// Use the higher-priority build environment, preserving caller flags and the
// Android libraries normally supplied by cargo-mobile2. No shell interpolation.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const environment = { ...process.env };
const flags = ["-Clink-arg=-landroid", "-Clink-arg=-llog", "-Clink-arg=-lOpenSLES",
  "-Clink-arg=-Wl,-z,max-page-size=16384"];
if (environment.CARGO_ENCODED_RUSTFLAGS) {
  environment.CARGO_ENCODED_RUSTFLAGS += "\x1f" + flags.join("\x1f");
} else {
  environment.RUSTFLAGS = [environment.RUSTFLAGS || "", ...flags].join(" ").trim();
}
const cli = fileURLToPath(new URL("./node_modules/@tauri-apps/cli/tauri.js", import.meta.url));
const result = spawnSync(process.execPath,
  [cli, "android", "build", "--apk", ...process.argv.slice(2)],
  { env: environment, stdio: "inherit" });
if (result.error) {
  console.error("Android build tool could not be started.");
}
process.exit(result.status ?? 1);
