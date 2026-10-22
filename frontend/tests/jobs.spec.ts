import { expect, test, type Page, type Route } from '@playwright/test';

// Synthetic browser protocol checks. These exercise request ordering and UI
// contracts, not DBOS recovery, a real IdP, extraction quality or paid models.
const PROFILE = {object_id: 'a'.repeat(32), version_id: 'b'.repeat(32), sha256: '1'.repeat(64)};
const MATERIAL = {object_id: 'c'.repeat(32), version_id: 'd'.repeat(32), sha256: '2'.repeat(64)};
const NOTICE = {object_id: 'unit-notice', version_id: 'unit-notice-v1', sha256: '3'.repeat(64)};
const COMPANY = 'UNIT 私有持久任务画像';
const JOB_ID = 'unit-durable-job';
const STATE_NAMES: Record<string, string> = {queued: '等待执行', running: '正在执行', waiting_review: '等待人工审核', cancel_requested: '取消请求已记录', cancelled: '已取消', failed: '失败或部分完成', completed: '执行已完成', timed_out: '执行超时', interrupted: '执行已中断'};
function evidence(side: 'notice' | 'company', index: number) {
  const quote = side === 'notice' ? `UNIT 公告要求 ${index}` : `UNIT 私有证明 ${index} <img src=x onerror=alert(1)>`;
  return {evidence_id: `${side}-${index}`, version: side === 'notice' ? NOTICE : MATERIAL, quote, start: 0, end: Array.from(quote).length, locator: 'normalized_text_v2'};
}
function savedResult() {
  const statuses = ['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'];
  const conditions = statuses.map((_, index) => ({condition_id: `unit-condition-${index}`, original_text: `UNIT 完整条件 ${index + 1}`, hard_requirement: true, expression: {operator: 'any', text: '资质 A 或资质 B', operands: [{operator: 'atom', text: '资质 A', operands: []}, {operator: 'not', text: '不得属于限制情形', operands: [{operator: 'atom', text: '限制情形', operands: []}]}]}, package_scope: {kind: 'specific', package_ids: ['unit-package']}, validity: {kind: 'unknown', reason: 'UNIT 期限待核实'}, notice_evidence: [evidence('notice', index)]}));
  return {run_id: 'unit-screening-result', result_revision: 'unit-result-v1', state: 'failed', human_review_state: 'unreviewed', recommendation: 'needs_materials', error_code: 'screening_request_limit', inventory: {conditions, unresolved: [{document_id: 'UNIT 未解析附件', reason: 'UNIT 原文仍需核对'}], missing_attachment_ids: ['unit-missing-attachment']}, groups: [{primary_notice_revision: NOTICE, assessment: {target_package_id: 'unit-package', conditions, assessments: conditions.map((condition, index) => ({condition_id: condition.condition_id, status: statuses[index], reason: `UNIT 判断理由 ${index + 1}`, notice_evidence: condition.notice_evidence, company_evidence: index === 2 ? [] : [evidence('company', index)], missing_items: index === 2 ? ['UNIT 证明缺失'] : []}))}}], missing_items: ['UNIT 证明缺失'], provenance: {prompt_version: 'unit-prompt-v1', rule_version: 'unit-rule-v1', actual_models: ['offline-ui-protocol']}};
}
function job(status = 'queued', stateVersion = 1, withResult = false) {
  return {run_id: JOB_ID, status, state_version: stateVersion, progress: {phase: status === 'running' ? 'assessment' : status, completed: status === 'running' ? 2 : 0, total: 5}, created_at: 1792710000, updated_at: 1792710010 + stateVersion, result_id: withResult ? 'unit-screening-result' : null as string | null, result_revision: withResult ? 'unit-result-v1' : null as string | null, error_code: null as string | null, source_state: {current: true, stale_reasons: [] as string[]}, usage: {model_calls: 3, confirmed_calls: 2, unknown_calls: 1, orphan_reservations: 0, usage_complete: false, estimated_cost_cny: '0.01', reserved_cost_cny: '0.10', offline: true}, allowed_actions: status === 'interrupted' ? ['resume'] : ['queued', 'running', 'waiting_review'].includes(status) ? ['cancel'] : [], manifest: {project_id: 'unit-saved-project', as_of: '2026-10-23T01:00:00Z', target_package_ids: ['unit-saved-package'], profile_revision: PROFILE, notice_revisions: [NOTICE], material_revisions: [MATERIAL]}, versions: {prompt_version: 'unit-prompt-v1'}, result: withResult ? savedResult() : null};
}
type Posted = {path: string; body: Record<string, unknown>; key?: string; csrf?: string};
type Control = {enabled: boolean; current: ReturnType<typeof job>; denySaved: number; denyMaterial: boolean; emptyHistory: boolean; companyReads: number; detailCalls: number; posts: Posted[]; detailHook?: (route: Route) => Promise<boolean>; postHook?: (route: Route, post: Posted) => Promise<boolean>};
async function protocol(page: Page, options: {enabled?: boolean; configured?: boolean; mode?: string; authenticated?: boolean; status?: string; withResult?: boolean; pages?: boolean; permissions?: string[]} = {}) {
  const control: Control = {enabled: options.enabled ?? true, current: job(options.status, 1, options.withResult), denySaved: 0, denyMaterial: false, emptyHistory: false, companyReads: 0, detailCalls: 0, posts: []};
  await page.route('**/api/auth/me', route => route.fulfill({json: {mode: options.mode || 'enterprise', authenticated: options.authenticated ?? true, organization: 'UNIT', user: {name: 'UNIT analyst', roles: ['analyst']}, permissions: options.permissions || ['agent:use', 'catalog:read'], csrf_token: 'unit-csrf'}}));
  await page.route('**/api/agent/status', route => route.fulfill({json: {enabled: false, configured: false, notice_count: 0}}));
  await page.route('**/api/sessions', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/notices?**', route => route.fulfill({json: {items: [], total: 0, offset: 0, limit: 20, has_more: false}}));
  await page.route('**/api/sources', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok'}}));
  await page.route('**/api/company/profiles**', route => {
    control.companyReads++; const url = new URL(route.request().url()), offset = Number(url.searchParams.get('offset') || 0), list = url.pathname.endsWith('/profiles');
    return route.fulfill({json: list ? {items: [{object_id: offset ? 'other-profile' : PROFILE.object_id, head_version: PROFILE.version_id}], total: options.pages ? 21 : 1, offset, limit: 20} : {...PROFILE, payload: {display_name: COMPANY}}});
  });
  await page.route('**/api/company/materials**', route => {
    const list = new URL(route.request().url()).pathname.endsWith('/materials');
    if (!list && control.denyMaterial) return route.fulfill({status: 404, json: {detail: 'company_object_not_found'}});
    return route.fulfill({json: list ? {items: control.denyMaterial ? [] : [{...MATERIAL, filename: 'UNIT 固定材料.txt'}], total: control.denyMaterial ? 0 : 1, offset: 0, limit: 20} : {...MATERIAL, filename: 'UNIT 固定材料.txt'}});
  });
  await page.route('**/api/jobs/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (path.endsWith('/status')) return route.fulfill({json: {enabled: control.enabled, configured: options.configured ?? true, backend: 'dbos', live_calls_enabled: false}});
    if (request.method() === 'POST') {
      const post = {path, body: request.postDataJSON(), key: request.headers()['idempotency-key'], csrf: request.headers()['x-csrf-token']}; control.posts.push(post);
      if (control.postHook && await control.postHook(route, post)) return;
      if (path.endsWith('/cancel')) control.current = {...control.current, status: 'cancel_requested', state_version: control.current.state_version + 1, allowed_actions: []};
      if (path.endsWith('/resume')) control.current = {...control.current, status: 'queued', state_version: control.current.state_version + 1, allowed_actions: ['cancel']};
      return route.fulfill({status: path.endsWith('/runs') ? 202 : 200, json: {...control.current, created: true}});
    }
    if (path.endsWith('/runs')) {
      const offset = Number(url.searchParams.get('offset') || 0);
      const items = control.emptyHistory ? [] : options.pages ? Array.from({length: offset ? 1 : 20}, (_, i) => ({...control.current, run_id: offset + i ? `unit-job-${offset + i}` : JOB_ID})) : [control.current];
      return route.fulfill({json: {items, total: control.emptyHistory ? 0 : options.pages ? 21 : 1, offset, limit: 20}});
    }
    control.detailCalls++;
    if (control.denySaved) return route.fulfill({status: control.denySaved, json: {detail: control.denySaved === 401 ? 'current_identity_required' : 'job_not_found'}});
    if (control.detailHook && await control.detailHook(route)) return;
    return route.fulfill({json: control.current});
  });
  return control;
}
async function open(page: Page) {await page.goto('/'); await page.getByRole('button', {name: '持久核对任务', exact: true}).click();}
async function choose(page: Page) {await page.getByRole('button', {name: `选择画像 ${PROFILE.object_id.slice(0, 12)}`}).click(); await expect(page.getByRole('heading', {name: COMPANY, exact: true})).toBeVisible();}
async function fill(page: Page) {await choose(page); await page.getByRole('textbox', {name: '项目编号', exact: true}).fill('unit-project');}
async function read(page: Page, poll = false) {
  await page.getByRole('textbox', {name: '已有任务编号'}).fill(JOB_ID); await page.getByRole('button', {name: '读取任务', exact: true}).click();
  await expect(page.locator('.jobs-detail')).toBeVisible();
  if (!poll) await page.getByRole('checkbox', {name: '自动刷新当前任务（每 2 秒）'}).uncheck();
}

test('default-disabled page fetches neither private inputs nor job submissions', async ({page}) => {
  const control = await protocol(page, {enabled: false}); await open(page);
  await expect(page.getByRole('heading', {name: '持久任务尚未开启'})).toBeVisible();
  await expect(page.getByText('默认关闭，页面不会提交任务或发起模型调用。')).toBeVisible();
  expect(control.companyReads).toBe(0); expect(control.posts).toHaveLength(0);
});
for (const options of [{mode: 'local'}, {authenticated: false}]) test(`jobs require an authenticated enterprise workspace ${JSON.stringify(options)}`, async ({page}) => {
  const control = await protocol(page, options); await page.goto('/');
  await expect(page.getByRole('button', {name: '持久核对任务', exact: true})).toHaveCount(0);
  expect(control.companyReads).toBe(0); expect(control.posts).toHaveLength(0);
});
test('unconfigured runtime disables creating jobs while authorized history remains readable', async ({page}) => {
  const control = await protocol(page, {configured: false}); await open(page); await fill(page);
  await expect(page.getByRole('button', {name: '提交持久核对任务', exact: true})).toBeDisabled();
  await read(page); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID); expect(control.posts).toHaveLength(0);
});
test('read-only enterprise roles can inspect a task but cannot submit, cancel or resume it', async ({page}) => {
  const control = await protocol(page, {permissions: ['catalog:read'], status: 'interrupted'}); control.current.allowed_actions = ['cancel', 'resume']; await open(page); await fill(page); await read(page);
  for (const name of ['提交持久核对任务', '请求取消任务', '恢复中断任务']) await expect(page.getByRole('button', {name, exact: true})).toBeDisabled();
  expect(control.posts).toHaveLength(0); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID);
});
test('202 pins profile and material hashes with CSRF, then GET refresh and reopening preserve the stable ID', async ({page}) => {
  const control = await protocol(page); await open(page); await fill(page);
  await page.getByRole('button', {name: '选择额外证明材料', exact: true}).click(); await page.getByRole('checkbox', {name: /UNIT 固定材料.txt/}).click(); await expect(page.getByRole('checkbox', {name: /UNIT 固定材料.txt/})).toBeChecked();
  await page.getByRole('textbox', {name: '目标采购包（逗号或空格分隔，可留空）'}).fill('unit-p1，unit-p2 unit-p1');
  await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click();
  await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID);
  await expect(page.getByRole('status').filter({hasText: '服务器已接收持久任务'})).toBeVisible();
  expect(control.posts).toHaveLength(1); expect(control.posts[0]).toMatchObject({csrf: 'unit-csrf', body: {profile_revision: PROFILE, project_id: 'unit-project', target_package_ids: ['unit-p1', 'unit-p2'], additional_material_revisions: [MATERIAL]}}); expect(control.posts[0].key).toMatch(/^[a-f0-9-]{36}$/);
  await page.getByRole('button', {name: '刷新当前任务', exact: true}).click(); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID);
  await page.reload(); await page.getByRole('button', {name: '持久核对任务', exact: true}).click(); await read(page);
  expect(control.posts).toHaveLength(1); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
});
test('an unconfirmed POST cannot display success and retry reuses its key; changed inputs or explicit new job use new keys', async ({page}) => {
  const control = await protocol(page); let first = true;
  control.postHook = async (route, post) => {if (!post.path.endsWith('/runs')) return false; if (first) {first = false; await route.abort('failed');} else await route.fulfill({status: 202, json: {...control.current, created: false}}); return true;};
  await open(page); await fill(page); await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('应答未确认'); await expect(page.locator('.jobs-detail')).toHaveCount(0); await expect(page.getByRole('status').filter({hasText: '服务器已接收'})).toHaveCount(0);
  await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click(); await expect(page.getByRole('status').filter({hasText: '已有任务'})).toBeVisible();
  expect(control.posts[1].key).toBe(control.posts[0].key); expect(control.posts[1].body).toEqual(control.posts[0].body);
  await page.getByRole('textbox', {name: '项目编号', exact: true}).fill('another-project'); await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click();
  await expect.poll(() => control.posts.length).toBe(3); expect(control.posts[2].key).not.toBe(control.posts[1].key);
  await page.getByRole('button', {name: '开始另一项任务', exact: true}).click(); await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click();
  await expect.poll(() => control.posts.length).toBe(4); expect(control.posts[3].key).not.toBe(control.posts[2].key);
});
test('a creation idempotency conflict is reported accurately without falsely acknowledging or retrying it', async ({page}) => {
  const control = await protocol(page);
  control.postHook = async (route, post) => {if (!post.path.endsWith('/runs')) return false; await route.fulfill({status: 409, json: {detail: 'job_idempotency_conflict'}}); return true;};
  await open(page); await fill(page); await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('幂等键已关联其他输入'); await expect(page.getByRole('status').filter({hasText: '服务器已接收'})).toHaveCount(0); await expect(page.locator('.jobs-detail')).toHaveCount(0);
  expect(control.posts).toHaveLength(1); expect(control.detailCalls).toBe(0);
});
for (const [status, label] of Object.entries(STATE_NAMES)) test(`execution state ${status} is explicit with only server-authorized actions`, async ({page}) => {
  const control = await protocol(page, {status}); await open(page); await read(page);
  await expect(page.locator('.jobs-task-state')).toHaveText(label);
  await expect(page.getByRole('button', {name: '恢复中断任务', exact: true})).toHaveCount(status === 'interrupted' ? 1 : 0);
  await expect(page.getByRole('button', {name: '请求取消任务', exact: true})).toHaveCount(control.current.allowed_actions.includes('cancel') ? 1 : 0);
  await expect(page.getByRole('button', {name: /批准|跳过审批|确认通过/})).toHaveCount(0);
  if (status === 'waiting_review') await expect(page.getByText('等待人工审核。审核事件由 B06 接入，当前页面不能跳过审批或代签。')).toBeVisible();
  if (status === 'cancelled') await expect(page.getByText('任务已取消，不能恢复。', {exact: false})).toBeVisible();
});
test('unexpected resume permissions cannot revive cancelled or waiting-review jobs, and no allowed action means no resume', async ({page}) => {
  const control = await protocol(page, {status: 'cancelled'}); control.current.allowed_actions = ['resume', 'cancel']; await open(page); await read(page);
  await expect(page.getByRole('button', {name: '恢复中断任务'})).toHaveCount(0); await expect(page.getByRole('button', {name: '请求取消任务'})).toHaveCount(0);
  control.current = {...job('waiting_review', 2), allowed_actions: ['resume']}; await page.getByRole('button', {name: '刷新当前任务', exact: true}).click(); await expect(page.locator('.jobs-state-version')).toHaveText('2');
  await expect(page.getByRole('button', {name: '恢复中断任务'})).toHaveCount(0);
  control.current = {...job('interrupted', 3), allowed_actions: []}; await page.getByRole('button', {name: '刷新当前任务', exact: true}).click(); await expect(page.locator('.jobs-state-version')).toHaveText('3');
  await expect(page.getByRole('button', {name: '恢复中断任务'})).toHaveCount(0); expect(control.posts).toHaveLength(0);
});
test('a late poll cannot overwrite the persisted cancel response and cancelled has no resume action', async ({page}) => {
  const control = await protocol(page, {status: 'running'}); let release!: () => void; const held = new Promise<void>(resolve => {release = resolve;}); let holding = false, oldDelivered = false;
  control.detailHook = async route => {if (control.detailCalls !== 2) return false; holding = true; await held; await route.fulfill({json: job('running', 1)}); oldDelivered = true; return true;};
  await open(page); await read(page, true); await expect.poll(() => holding).toBe(true);
  await page.getByRole('button', {name: '请求取消任务', exact: true}).click();
  await expect(page.locator('.jobs-task-state')).toHaveText('取消请求已记录'); await expect(page.locator('.jobs-state-version')).toHaveText('2');
  expect(control.posts[0]).toMatchObject({csrf: 'unit-csrf', body: {expected_state_version: 1}}); release();
  await expect.poll(() => oldDelivered).toBe(true); await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))); await expect(page.locator('.jobs-state-version')).toHaveText('2');
  control.current = {...job('cancelled', 3), allowed_actions: []}; await expect(page.locator('.jobs-task-state')).toHaveText('已取消');
  await expect(page.getByRole('button', {name: '恢复中断任务'})).toHaveCount(0); expect(control.posts).toHaveLength(1);
});
test('an older poll cannot erase newer usage and reservation warnings when the state version is unchanged', async ({page}) => {
  const control = await protocol(page, {status: 'running'});
  control.current.usage = {...control.current.usage, model_calls: 0, confirmed_calls: 0, unknown_calls: 0, usage_complete: true, estimated_cost_cny: '0', reserved_cost_cny: '0'};
  const old = structuredClone(control.current);
  let release!: () => void, holding = false, oldDelivered = false;
  const held = new Promise<void>(resolve => {release = resolve;});
  control.detailHook = async route => {if (control.detailCalls !== 2) return false; holding = true; await held; await route.fulfill({json: old}); oldDelivered = true; return true;};
  await open(page); await read(page, true); await expect.poll(() => holding).toBe(true);
  control.current.usage = {...control.current.usage, model_calls: 1, unknown_calls: 1, usage_complete: false, reserved_cost_cny: '0.45'};
  await page.getByRole('button', {name: '刷新当前任务', exact: true}).click();
  await expect(page.locator('.jobs-state-version')).toHaveText('1');
  await expect(page.locator('.jobs-usage')).toContainText('用量未知 1');
  await expect(page.locator('.jobs-usage')).toContainText('保留预留 ¥0.45');
  await expect(page.getByText('用量尚未确认，不能按零费用处理', {exact: false})).toBeVisible();
  release(); await expect.poll(() => oldDelivered).toBe(true);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  // Read immediately so a later fresh poll cannot hide a transient regression.
  expect(await page.locator('.jobs-state-version').textContent()).toBe('1');
  expect(await page.locator('.jobs-usage').textContent()).toContain('用量未知 1');
  expect(await page.locator('.jobs-usage').textContent()).toContain('保留预留 ¥0.45');
  expect(await page.getByText('用量尚未确认，不能按零费用处理', {exact: false}).count()).toBe(1);
  expect(control.posts).toHaveLength(0);
});
test('resume uses the displayed state version; a 409 re-reads but never silently retries a mutation', async ({page}) => {
  const control = await protocol(page, {status: 'interrupted'}); control.current.state_version = 7;
  control.postHook = async (route, post) => {if (!post.path.endsWith('/resume')) return false; control.current = job('cancelled', 8); await route.fulfill({status: 409, json: {detail: 'job_state_conflict'}}); return true;};
  await open(page); await read(page); await page.getByRole('button', {name: '恢复中断任务', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('没有自动重试'); await expect(page.locator('.jobs-task-state')).toHaveText('已取消');
  expect(control.posts).toHaveLength(1); expect(control.posts[0].body).toEqual({expected_state_version: 7});
});
test('polling stays independent while a mutation is in flight and its older acknowledgement cannot regress the task', async ({page}) => {
  const control = await protocol(page, {status: 'running'}); let release!: () => void, held = false, delivered = false;
  const gate = new Promise<void>(resolve => {release = resolve;});
  control.postHook = async (route, post) => {
    if (!post.path.endsWith('/cancel')) return false;
    held = true; await gate; await route.fulfill({json: job('cancel_requested', 2)}); delivered = true; return true;
  };
  await open(page); await read(page, true); await page.getByRole('button', {name: '请求取消任务', exact: true}).click();
  await expect.poll(() => held).toBe(true); await expect(page.getByRole('status').filter({hasText: '服务器已记录取消请求'})).toHaveCount(0);
  control.current = job('cancelled', 3); await expect(page.locator('.jobs-state-version')).toHaveText('3');
  release(); await expect.poll(() => delivered).toBe(true); await expect(page.getByRole('status').filter({hasText: '服务器已记录取消请求'})).toBeVisible();
  await expect(page.locator('.jobs-task-state')).toHaveText('已取消'); await expect(page.locator('.jobs-state-version')).toHaveText('3'); expect(control.posts).toHaveLength(1);
});
test('an older duplicate creation response cannot regress an already-polled instance of the same stable task', async ({page}) => {
  const control = await protocol(page, {status: 'running'}); await open(page); await fill(page);
  await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click(); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID);
  control.current = job('cancel_requested', 5); await expect(page.locator('.jobs-state-version')).toHaveText('5');
  control.postHook = async (route, post) => {if (!post.path.endsWith('/runs')) return false; await route.fulfill({status: 202, json: {...job('running', 1), created: false}}); return true;};
  await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click(); await expect(page.getByRole('status').filter({hasText: '已有任务'})).toBeVisible();
  await expect(page.locator('.jobs-state-version')).toHaveText('5'); await expect(page.locator('.jobs-task-state')).toHaveText('取消请求已记录'); expect(control.posts[1].key).toBe(control.posts[0].key);
});
test('permission denial on the first read after 202 removes the acknowledgement and every private field', async ({page}) => {
  const control = await protocol(page);
  control.postHook = async (route, post) => {if (!post.path.endsWith('/runs')) return false; control.denySaved = 403; await route.fulfill({status: 202, json: {...control.current, created: true}}); return true;};
  await open(page); await fill(page); await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click();
  await expect(page.getByRole('alert')).toContainText('403'); await expect(page.locator('.jobs-detail')).toHaveCount(0); await expect(page.locator('.jobs-pinned')).toHaveCount(0); await expect(page.getByRole('status').filter({hasText: '服务器已接收'})).toHaveCount(0); expect(control.posts).toHaveLength(1);
});
test('resume acknowledgement keeps the original task ID and cannot create a second job', async ({page}) => {
  const control = await protocol(page, {status: 'interrupted'}); await open(page); await read(page);
  await page.getByRole('button', {name: '恢复中断任务', exact: true}).click(); await expect(page.getByRole('status').filter({hasText: '已接收恢复操作'})).toBeVisible();
  await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID); await expect(page.locator('.jobs-task-state')).toHaveText('等待执行');
  expect(control.posts).toHaveLength(1); expect(control.posts[0].path).toBe(`/api/jobs/runs/${JOB_ID}/resume`);
});
for (const status of [401, 403, 404]) test(`a ${status} fixed-run response clears private results, citations, inputs and history even when the list is 200`, async ({page}) => {
  const control = await protocol(page, {status: 'failed', withResult: true}); await open(page); await fill(page); await read(page);
  await expect(page.locator('.jobs-condition')).toHaveCount(5); control.denySaved = status; control.emptyHistory = true;
  await page.getByRole('button', {name: '刷新任务与权限', exact: true}).click(); await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('.jobs-detail')).toHaveCount(0); await expect(page.locator('.jobs-pinned')).toHaveCount(0); await expect(page.locator('.jobs-history li')).toHaveCount(0); await expect(page.locator('blockquote')).toHaveCount(0);
  await expect(page.getByRole('textbox', {name: '项目编号', exact: true})).toHaveCount(status === 401 ? 0 : 1);
  if (status !== 401) await expect(page.getByRole('textbox', {name: '项目编号', exact: true})).toHaveValue(''); else await expect(page.getByRole('link', {name: '重新统一登录'})).toBeVisible();
  expect(control.posts).toHaveLength(0);
});
test('a selected material revoked after a 200 empty list clears the private fixed inputs', async ({page}) => {
  const control = await protocol(page); await open(page); await fill(page);
  await page.getByRole('button', {name: '选择额外证明材料'}).click(); await page.getByRole('checkbox', {name: /UNIT 固定材料.txt/}).click(); await expect(page.getByRole('checkbox', {name: /UNIT 固定材料.txt/})).toBeChecked();
  control.denyMaterial = true; await page.getByRole('button', {name: '刷新任务与权限', exact: true}).click(); await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('.jobs-pinned')).toHaveCount(0); await expect(page.getByText('UNIT 固定材料.txt', {exact: false})).toHaveCount(0); await expect(page.getByRole('textbox', {name: '项目编号', exact: true})).toHaveValue('');
});
test('history and profile pagination retain independently authorized pinned details even when absent from the new page', async ({page}) => {
  const control = await protocol(page, {pages: true}); await open(page); await fill(page); await read(page);
  await page.getByRole('button', {name: '下一页任务', exact: true}).click(); await expect(page.locator('.jobs-history-range')).toHaveText('第 21–21 项，共 21 项');
  await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID); await expect(page.getByRole('button', {name: '下一页任务', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: '下一页画像', exact: true}).click(); await expect(page.getByRole('button', {name: '选择画像 other-profil'})).toBeVisible();
  await expect(page.getByRole('heading', {name: COMPANY, exact: true})).toBeVisible(); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID); expect(control.posts).toHaveLength(0);
});
test('partial saved results keep all five states, both evidence versions, stale sources and unknown cost visible without approval', async ({page}) => {
  const control = await protocol(page, {status: 'failed', withResult: true}); control.current.source_state = {current: false, stale_reasons: ['notice_versions_changed']}; await open(page); await read(page);
  await expect(page.locator('.jobs-condition')).toHaveCount(5);
  for (const name of ['满足', '不满足', '信息不足', '证据冲突', '不适用']) await expect(page.locator('.jobs-condition .jobs-badge').filter({hasText: new RegExp(`^${name}$`)})).toBeVisible();
  await expect(page.getByText('尚未人工审核。执行完成和条件“满足”均不代表资格已通过。')).toBeVisible(); await expect(page.getByText('已达到模型请求次数上限。', {exact: false})).toBeVisible();
  await expect(page.getByText('UNIT 原文仍需核对', {exact: false})).toBeVisible(); await expect(page.getByText('输入或来源已有变化', {exact: false})).toBeVisible(); await expect(page.getByText('用量尚未确认，不能按零费用处理', {exact: false})).toBeVisible();
  const first = page.locator('.jobs-condition').first(); await first.getByText('查看条件逻辑、适用范围与期限', {exact: true}).click(); await expect(first.getByText('至少一项成立（OR）：资质 A 或资质 B')).toBeVisible(); await expect(first.getByText('否定（NOT）：不得属于限制情形')).toBeVisible(); await expect(first.getByText('条件期限：期限尚待核实；UNIT 期限待核实')).toBeVisible(); await expect(page.locator('.jobs-saved-result > p').filter({hasText: '原提示版本：unit-prompt-v1'})).toBeVisible(); await first.getByText('notice-0', {exact: true}).click(); await first.getByText('company-0', {exact: true}).click();
  await expect(first.getByText('UNIT 公告要求 0', {exact: true})).toBeVisible(); await expect(first.getByText('UNIT 私有证明 0 <img src=x onerror=alert(1)>', {exact: true})).toBeVisible(); await expect(first.getByText(MATERIAL.version_id, {exact: true})).toBeVisible(); await expect(first.locator('blockquote img')).toHaveCount(0);
  await expect(page.getByRole('button', {name: /批准|提交审核|确认通过/})).toHaveCount(0); expect(control.posts).toHaveLength(0);
});
test('saved task inputs remain distinct from a new form and unlinked fee reservations are never presented as zero unknown cost', async ({page}) => {
  const control = await protocol(page, {status: 'interrupted'});
  control.current.manifest.profile_revision = {...PROFILE, version_id: 'unit-old-profile-version', sha256: '8'.repeat(64)};
  control.current.usage = {...control.current.usage, unknown_calls: 0, orphan_reservations: 2};
  await open(page); await fill(page); await read(page);
  await expect(page.locator('.jobs-pinned')).toContainText(PROFILE.version_id);
  await expect(page.locator('.jobs-fixed-inputs')).toContainText('unit-old-profile-version'); await expect(page.locator('.jobs-fixed-inputs')).toContainText('unit-saved-project'); await expect(page.locator('.jobs-fixed-inputs')).toContainText('unit-saved-package');
  await expect(page.getByText('另有 2 项预留尚未关联调用回执，费用仍需核对。')).toBeVisible(); await expect(page.getByText('用量尚未确认，不能按零费用处理', {exact: false})).toBeVisible();
});
test('a transient poll failure labels cached content unconfirmed and a later GET recovers without a new submission', async ({page}) => {
  const control = await protocol(page, {status: 'running'}); control.detailHook = async route => {if (control.detailCalls !== 2) return false; await route.fulfill({status: 503, json: {detail: 'temporarily_unavailable'}}); return true;};
  await open(page); await read(page, true); await expect(page.getByRole('alert')).toContainText('已显示内容尚未重新确认'); await expect(page.locator('.jobs-run-id')).toHaveText(JOB_ID);
  await page.getByRole('button', {name: '刷新当前任务', exact: true}).click(); await expect(page.getByRole('alert')).toHaveCount(0); expect(control.posts).toHaveLength(0);
});
test('a mobile viewport keeps long IDs, fixed hashes and condition evidence within the viewport', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844}); await protocol(page, {status: 'waiting_review', withResult: true}); await open(page); await fill(page); await read(page);
  await expect(page.locator('.jobs-condition')).toHaveCount(5); await page.getByText('查看本任务固定输入与版本', {exact: true}).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path: test.info().outputPath('jobs-mobile.png'), fullPage: true});
});
