import { expect, test, type Page } from '@playwright/test';

// UI protocol fixtures only. These tests do not insert or invent procurement notices.
async function status(page: Page, configured: boolean, count: number) {
  await page.route('**/api/agent/status', route => route.fulfill({json: {
    configured, enabled: true, model: 'offline-ui-protocol', notice_count: count, estimated_spend_cny: 0,
  }}));
}

test('missing credential is explicit; model calls are disabled', async ({ page }) => {
  await status(page, false, 0);
  await page.goto('/');
  await expect(page.getByText('尚未配置 DeepSeek。', {exact: false})).toBeVisible();
  await expect(page.getByRole('textbox', {name: '向公告库提问'})).toBeDisabled();
  await expect(page.getByRole('button', {name: '发送问题'})).toBeDisabled();
});

test('empty corpus cannot be presented as a successful demo', async ({ page }) => {
  await status(page, true, 0);
  await page.goto('/');
  await expect(page.getByText('公告库为空。', {exact: false})).toBeVisible();
  await expect(page.getByRole('textbox')).toBeDisabled();
});

test('provider error is readable and retry remains possible', async ({ page }) => {
  await status(page, true, 1);
  await page.route('**/api/agent/chat', route => route.fulfill({status: 503, json: {error: 'provider_timeout'}}));
  await page.goto('/');
  const input = page.getByRole('textbox');
  await input.fill('接口错误展示单元测试');
  await input.press('Enter');
  await expect(page.getByRole('alert')).toContainText('本轮请求超时');
  await expect(input).toBeEnabled();
  await page.getByRole('button', {name: '新对话'}).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('no browser interpretation of user HTML; Shift+Enter does not send', async ({ page }) => {
  await status(page, true, 1);
  let requests = 0;
  await page.route('**/api/agent/chat', route => {requests++;return route.fulfill({status: 503, json: {error: 'provider_unavailable'}});});
  await page.goto('/');
  const input = page.getByRole('textbox');
  await input.fill('<img src=x onerror=alert(1)>');
  await input.press('Shift+Enter');
  expect(requests).toBe(0);
  await page.getByRole('button', {name: '发送问题'}).click();
  await expect(page.locator('.user-question')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('.user-question img')).toHaveCount(0);
});

test('mobile layout has no horizontal overflow and usable input', async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  await status(page, true, 1);
  await page.goto('/');
  await expect(page.getByRole('textbox')).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole('button', {name: '新对话'})).toBeVisible();
});
