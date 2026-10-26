import { defineConfig } from '@playwright/test';

const port = Number(process.env.BIDOPS_BROWSER_PORT || 4173);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('BIDOPS_BROWSER_PORT must be a port between 1024 and 65535.');
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL, browserName: 'chromium',
    channel: process.env.BIDOPS_BROWSER_CHANNEL,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    // A different worktree may already own the default port. Never test its build.
    reuseExistingServer: false,
  },
});
