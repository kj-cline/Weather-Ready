import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendPort = 5175;
const backendPort = 8001;
const currentDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDir, "..");
const useManagedWebServer = process.env.PLAYWRIGHT_USE_MANAGED_WEBSERVER !== "0";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  retries: 0,
  timeout: 180_000,
  expect: {
    timeout: 120_000,
  },
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    headless: true,
  },
  webServer: useManagedWebServer
    ? {
        command: `${path.join(projectRoot, ".venv", "bin", "python")} ${path.join(projectRoot, "scripts", "ops", "start_local_stack.py")} --ui --api-port ${backendPort} --frontend-port ${frontendPort} --init-db-if-missing`,
        cwd: projectRoot,
        env: {
          ...process.env,
          PYTHONPATH: path.join(projectRoot, "src"),
          STORMREADY_V3_SOURCE_MODE: "detailed_mock",
          STORMREADY_FRONTEND_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
        },
        url: `http://127.0.0.1:${frontendPort}`,
        reuseExistingServer: false,
        timeout: 120_000,
      }
    : undefined,
});
