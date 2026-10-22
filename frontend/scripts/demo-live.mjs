import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';

if (!process.argv.includes('--execute')) throw new Error('Real provider usage requires --execute');
const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1440, height: 1040}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const results = [];
const dir = new URL('../../data/artifacts/demo-browser/', import.meta.url);
await fs.mkdir(dir, {recursive: true});
try {
  await page.goto('http://127.0.0.1:8765');
  await expect(page.getByRole('textbox')).toBeEnabled();
  for (const question of ['TGPC-2026-A-0384 的预算是多少？', '它的采购单位是谁？']) {
    const response = page.waitForResponse(r => r.url().endsWith('/api/agent/chat') && r.request().method() === 'POST', {timeout: 70000});
    await page.getByRole('textbox').fill(question);
    await page.getByRole('button', {name: '发送问题'}).click();
    const http = await response;
    expect(http.status()).toBe(200);
    const body = await http.json();
    expect(body.provider_executed).toBe(true);
    expect(body.blocks[0].items[0].project_code).toBe('TGPC-2026-A-0384');
    expect(body.blocks[0].items[0].budget_yuan).toBe('1120000.00');
    results.push({run_id: body.run_id, model: body.model, mode: body.response_mode, tools: body.trace.map(t => t.tool), usage: body.usage, elapsed_ms: body.elapsed_ms});
  }
  await fs.writeFile(new URL('provider-turns.json', dir), JSON.stringify(results, null, 2)+'\n');
  await expect(page.locator('.result-values').last()).toContainText('1,120,000 元');
  await expect(page.getByRole('link', {name: '打开官方公告'})).toHaveAttribute('href', 'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510682.htm');
  await page.locator('.citation').filter({hasText: '预算金额'}).locator('summary').first().click();
  await expect(page.locator('.agent-evidence')).toContainText('天津市生态环境监测中心');
  await page.locator('.agent-evidence').evaluate(el => {el.scrollTop = 0;});
  await page.locator('.conversation-scroll').evaluate(el => {el.scrollTop = 0;});
  await page.screenshot({path: new URL('desktop.png', dir).pathname, fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path: new URL('mobile.png', dir).pathname, fullPage: true});
  await page.getByRole('button', {name: '公告档案', exact: true}).click();
  await expect(page.locator('tbody tr')).toHaveCount(6);
  expect(errors).toEqual([]);
  const report = {completed_at: new Date().toISOString(), passed: true, actual_provider_turns: 2, browser: browser.version(), viewport_checks: ['1440x1040','390x844'], source_link_verified: true, archive_rows: 6, page_errors: errors, runs: results};
  await fs.writeFile(new URL('../../reports/demo-browser-20261022.json', import.meta.url), JSON.stringify(report, null, 2)+'\n');
  console.log(JSON.stringify(report));
} finally {await browser.close();}
