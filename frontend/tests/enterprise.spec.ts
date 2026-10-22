import { expect, test, type Page } from '@playwright/test';

async function identity(page: Page, role?: string) {
  const permissions = role === 'admin' ? ['catalog:read', 'agent:use', 'audit:read'] : role === 'analyst' ? ['catalog:read', 'agent:use'] : role ? ['catalog:read'] : [];
  await page.route('**/api/auth/me', route => route.fulfill({json: {mode: 'enterprise', authenticated: !!role, organization: 'Unit organization', user: role ? {name: 'Unit user', roles: [role]} : null, permissions, csrf_token: role ? 'unit-csrf' : null}}));
  await page.route('**/api/agent/status', route => route.fulfill({json: {configured: true, enabled: true, model: 'offline-ui-protocol', notice_count: 0, estimated_spend_cny: 0}}));
  await page.route('**/api/sessions', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/notices*', route => route.fulfill({json: {items: [], total: 0, limit: 50, offset: 0}}));
  await page.route('**/api/sources', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok', phase: 'enterprise_pilot', network_enabled: false}}));
}

test('unauthenticated enterprise only shows the SSO entry', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await identity(page);
  await page.goto('/');
  await expect(page.getByRole('link', {name: '企业统一登录'})).toHaveAttribute('href', '/api/auth/login');
  await expect(page.getByRole('textbox')).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Agent 助手'})).toHaveCount(0);
  await page.screenshot({path: test.info().outputPath('enterprise-login.png'), fullPage: true});
});

test('OIDC failure is explicit and does not open a workspace', async ({page}) => {
  await identity(page);
  await page.goto('/?login_error=1');
  await expect(page.getByRole('alert')).toContainText('登录未完成');
  await expect(page.getByRole('textbox')).toHaveCount(0);
});

test('viewer sees archive but no chat or audit controls', async ({page}) => {
  await identity(page, 'viewer');
  await page.goto('/');
  await expect(page.getByRole('button', {name: '公告档案'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Agent 助手'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '运行审计'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '退出工作台'})).toBeVisible();
});

test('analyst restores private history after reload without calling the model', async ({page}) => {
  await identity(page, 'analyst');
  await page.route('**/api/sessions', route => route.fulfill({json: {items: [{session_id: 'a'.repeat(32), updated_at: 1792600000, turns: 1}]}}));
  await page.route('**/api/sessions/*', route => route.fulfill({json: {session_id: 'a'.repeat(32), turns: [{question: 'UNIT_HISTORY_QUESTION', state: 'failed', error: 'request_interrupted', answer: null}]}}));
  let modelRequests = 0;
  await page.route('**/api/agent/chat', route => {modelRequests++; return route.fulfill({status: 500});});
  await page.goto('/');
  await page.getByLabel('我的会话').selectOption('a'.repeat(32));
  await expect(page.getByText('UNIT_HISTORY_QUESTION')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('未自动重跑');
  await page.reload();
  await page.getByLabel('我的会话').selectOption('a'.repeat(32));
  await expect(page.getByText('UNIT_HISTORY_QUESTION')).toBeVisible();
  expect(modelRequests).toBe(0);
});

test('network recovery preserves the request key and csrf header', async ({page}) => {
  await identity(page, 'analyst');
  await page.route('**/api/agent/status', route => route.fulfill({json: {configured: true, enabled: true, model: 'offline-ui-protocol', notice_count: 1, estimated_spend_cny: 0}}));
  const keys: string[] = [];
  await page.route('**/api/agent/chat', route => {
    const headers = route.request().headers();
    expect(headers['x-csrf-token']).toBe('unit-csrf');
    keys.push(headers['idempotency-key']);
    if (keys.length === 1) return route.abort();
    return route.fulfill({status: 503, json: {error: 'request_interrupted'}});
  });
  await page.goto('/');
  await page.getByRole('textbox').fill('UNIT_RECOVERY_QUESTION');
  await page.getByRole('button', {name: '发送问题'}).click();
  await page.getByRole('button', {name: '重取本次结果'}).click();
  await expect(page.getByRole('alert')).toContainText('未自动重跑');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toMatch(/^[a-f0-9]{32}$/);
  expect(keys[0]).toBe(keys[1]);
});

test('administrator audit shows metadata, remains usable on mobile', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await identity(page, 'admin');
  await page.route('**/api/audit?*', route => route.fulfill({json: {items: [{event_id: 1, observed_at: 1792600000, owner_id: null, event: 'request_completed', metadata_json: '{"run_id":"unit-run"}'}]}}));
  await page.goto('/');
  await page.getByRole('button', {name: '运行审计'}).click();
  await expect(page.getByRole('heading', {name: '运行审计'})).toBeVisible();
  await expect(page.getByText('request_completed')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: test.info().outputPath('enterprise-audit-mobile.png'), fullPage: true});
});

test('identity API failure does not silently downgrade to a local anonymous demo', async ({page}) => {
  await page.route('**/api/auth/me', route => route.fulfill({status: 503, json: {error: 'unavailable'}}));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('无法确认登录状态');
  await expect(page.getByRole('textbox')).toHaveCount(0);
});
