import {defineConfig} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {networkInterfaces} from 'node:os';

// Fail closed if a future CI edit accidentally removes the namespace wrapper.
if (process.env.CI && Object.keys(networkInterfaces()).some(name => name !== 'lo')) {
  throw new Error('CI browser tests require a network namespace containing only loopback');
}
if (process.env.CI && process.getuid?.() === 0) {
  throw new Error('CI browser tests must run as the unprivileged runner, not root');
}

const directory = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  testDir: directory,
  testMatch: '*.spec.mjs',
  outputDir: `${directory}test-results`,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 30_000,
  expect: {timeout: 8_000},
  reporter: [
    ['line'],
    ['html', {outputFolder: `${directory}playwright-report`, open: 'never'}],
  ],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    browserName: 'chromium',
    headless: true,
    locale: 'en-US',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'off',
  },
  projects: ['light', 'dark'].flatMap(colorScheme => [
    {name: `desktop-${colorScheme}`, use: {colorScheme, viewport: {width: 1365, height: 900}}},
    {name: `mobile-${colorScheme}`, use: {colorScheme, viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}},
  ]),
  webServer: {
    command: `"${process.execPath}" "${directory}static-server.mjs"`,
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: false,
    timeout: 10_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
