import { expect, test } from '@playwright/test';

test('archive sends full-catalog search and resets pagination', async ({ page }) => {
  await page.route('**/api/auth/me', route => route.fulfill({json: {mode: 'local', authenticated: false, organization: 'BidOps', user: null, permissions: [], csrf_token: null}}));
  await page.route('**/api/agent/status', route => route.fulfill({json: {configured: false, enabled: false, notice_count: 0}}));
  await page.route('**/api/sources', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok'}}));
  const queries: URL[] = [];
  await page.route('**/api/notices?*', route => {
    const url = new URL(route.request().url());
    queries.push(url);
    const filtered = !!url.searchParams.get('q');
    return route.fulfill({json: {items: [], total: filtered ? 0 : 507, limit: 20, offset: Number(url.searchParams.get('offset')), has_more: !filtered, truncated: !filtered}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '公告档案'}).click();
  await page.getByRole('button', {name: '下一页'}).click();
  await expect.poll(() => queries.at(-1)?.searchParams.get('offset')).toBe('20');
  await page.getByRole('textbox', {name: '搜索项目、标题或采购单位'}).fill('UNIT-PAGE-0506');
  await page.getByRole('button', {name: '搜索全部公告'}).click();
  await expect.poll(() => queries.at(-1)?.searchParams.get('q')).toBe('UNIT-PAGE-0506');
  expect(queries.at(-1)?.searchParams.get('offset')).toBe('0');
  await expect(page.getByText('没有匹配的公告')).toBeVisible();
  await expect(page.getByRole('button', {name: '下一页'})).toHaveCount(0);
});
