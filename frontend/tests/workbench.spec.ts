import { expect, test, type Page } from '@playwright/test';

// UI protocol fixtures only. These tests do not insert or invent procurement notices.
async function status(page: Page, configured: boolean, count: number) {
  await page.route('**/api/auth/me', route => route.fulfill({json: {mode: 'local', authenticated: false, organization: 'BidOps', user: null, permissions: [], csrf_token: null}}));
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

test('read pages expose full count and truncation including the last page', async ({ page }) => {
  await status(page, true, 101);
  const card = {project_code: 'UNIT-MULTI', title: '工程界面夹具', buyer_name: null, budget_yuan: '43', bid_deadline_at: null, published_at: null, document_deadline_at: null, package_count: 0, packages: [], eligibility: [], source_url: 'https://example.invalid/unit', source_observed_at: '2026-10-22T00:00:00Z', citations: []};
  await page.route('**/api/agent/chat', route => route.fulfill({json: {
    run_id: 'unit-pagination', session_id: 'unit-session', response_mode: 'review_required', focus: 'budget', message: '历史版本需要人工核对。',
    blocks: [{id: 'r1', operation: 'read', total: 101, offset: 100, limit: 100, has_more: false, truncated: true, items: [card], metrics: {}, missing_project_codes: []}],
    trace: [], usage: {input_tokens: 0, output_tokens: 0, estimated_cost_cny_peak: 0}, elapsed_ms: 0, model: 'offline-ui-protocol', warnings: [],
  }}));
  await page.goto('/');
  await page.getByRole('textbox').fill('查看历史版本末页');
  await page.getByRole('button', {name: '发送问题'}).click();
  await expect(page.locator('.result-count')).toContainText('匹配 101 条');
  await expect(page.locator('.result-count')).toContainText('展示第 101–101 条（1 条）');
  await expect(page.locator('.result-count')).toContainText('结果已截断');
  await expect(page.locator('.result-count')).toContainText('读取其他页');
});
