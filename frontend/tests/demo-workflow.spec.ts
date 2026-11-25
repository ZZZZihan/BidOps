import { expect, test, type Page, type Route } from '@playwright/test';
import { spawn } from 'node:child_process';

// Authored UI protocol fixtures. No procurement data, people, model quality,
// real authentication or paid provider calls are validated by this suite.
const OWNER = 'protocol-local-reviewer';
const PROFILE = {object_id: 'protocol-profile', version_id: 'protocol-profile-v1', sha256: 'a'.repeat(64)};
const MATERIAL = {object_id: 'protocol-material', version_id: 'protocol-material-v1', sha256: 'b'.repeat(64)};
const NOTICE = {object_id: 'protocol-notice', version_id: 'protocol-notice-v1', sha256: 'c'.repeat(64)};
const STORAGE = `bidops.local-workflow.v1:${OWNER}`;
const verdicts = ['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'];
const SOURCE = {current: true, stale_reasons: [] as string[]};
const COMPANY = '协议测试企业（虚构）', PROJECT = '协议测试采购项目';
function evidence(side: string, index: number) {
  const quote = side === 'notice' ? `协议测试公告要求 ${index + 1}` : `协议测试虚构证明 ${index + 1} <img src=x onerror=alert(1)>`;
  return {evidence_id: `${side}-${index}`, side, object_kind: side === 'notice' ? 'notice' : 'company_material', version: side === 'notice' ? NOTICE : MATERIAL, quote, start: 0, end: Array.from(quote).length, locator: 'normalized_text_v2', text_sha256: 'd'.repeat(64)};
}
function condition(index: number) {return {condition_id: `condition-${index}`, original_text: `协议测试资格条件 ${index + 1}`, hard_requirement: true, notice_evidence: [evidence('notice', index)], expression: {operator: 'atom', text: '协议测试原条件', operands: []}, package_scope: {kind: 'all', package_ids: []}, validity: {kind: 'unknown', reason: '期限依据缺失'}};}
function judgment(index: number) {return {condition_id: `condition-${index}`, status: verdicts[index], reason: `协议测试原判断理由 ${index + 1}`, notice_evidence: [evidence('notice', index)], company_evidence: index === 2 ? [] : [evidence('company', index)], missing_items: index === 2 ? ['协议测试尚缺证明'] : [], conflicting_evidence_ids: index === 3 ? ['conflict-a', 'conflict-b'] : []};}
function result() {
  return {result_revision: 'protocol-result-v1', human_review_state: 'unreviewed', state: 'completed', recommendation: 'review_recommended', inventory: {conditions: verdicts.map((_, index) => condition(index)), unresolved: [{document_id: 'protocol-unresolved', reason: '协议测试附件仍需核实'}], missing_attachment_ids: ['protocol-missing-attachment']}, groups: [{assessment: {assessment_id: 'protocol-assessment', conditions: verdicts.map((_, index) => condition(index)), assessments: verdicts.map((_, index) => judgment(index))}}], missing_items: ['协议测试尚缺证明'], provenance: {prompt_version: 'protocol-prompt-v1', model_calls: 2}};
}
function job() {
  return {run_id: 'protocol-job', status: 'waiting_review', state_version: 3, created_at: 1792972800, updated_at: 1792972900, progress: {phase: 'waiting_review'}, source_state: {...SOURCE}, result: result() as ReturnType<typeof result> | null, error_code: null as string | null, usage: {offline: false, estimated_cost_cny: '0.02', reserved_cost_cny: '0.25', usage_complete: false}, allowed_actions: [] as string[]};
}
function reviewCase() {
  return {case_id: 'protocol-case', head_revision_id: 'protocol-revision', review_version: 1, status: 'open', source_state: {...SOURCE}, allowed_actions: ['decide', 'export'], items: verdicts.map((_, index) => ({revision_id: 'protocol-revision', assessment_id: 'protocol-assessment', condition_id: `condition-${index}`, condition: condition(index), agent_assessment: judgment(index), latest_decision: null as string | null, latest_event: null as {event_id: string; reason: string; actor_id: string} | null, effective: false, ineffective_reasons: []})), revisions: [{revision_id: 'protocol-revision', result_revision: 'protocol-result-v1', result: result()}], delivery: {pending_count: 0, frozen_count: 0, worker_available: true}};
}
type Post = {path: string; body: any; key?: string; csrf?: string};
async function protocol(page: Page, options: {local?: boolean; prepared?: boolean; live?: boolean; stale?: boolean; identityDemo?: boolean; authenticated?: boolean} = {}) {
  const control = {prepared: options.prepared ?? false, job: job(), review: reviewCase(), posts: [] as Post[], denySaved: 0, hook: undefined as ((route: Route, post: Post) => Promise<boolean>) | undefined};
  if (options.stale) {
    control.job.source_state = {current: false, stale_reasons: ['public_sources_stale']};
    control.review.source_state = {current: false, stale_reasons: ['public_sources_stale']};
    control.review.allowed_actions = ['export'];
  }
  const context = () => ({kind: 'local_workflow', label: '本机完整流程', description: '使用真实服务与明确虚构的企业资料。', seeded: control.prepared, ...(control.prepared ? {profile_revision: PROFILE, additional_material_revisions: [MATERIAL], reviewer_subject_id: OWNER, company_name: COMPANY, materials: [{label: '协议测试虚构证明.txt', ...MATERIAL}]} : {})});
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (path === '/api/auth/me') return route.fulfill({json: {mode: 'enterprise', authenticated: options.authenticated ?? true, organization: '协议测试', user: {name: '本机测试人员', roles: ['analyst', 'reviewer'], subject_id: OWNER}, permissions: ['catalog:read', 'agent:use', 'review:decide'], csrf_token: 'protocol-csrf', ...((options.identityDemo ?? options.local !== false) ? {demo: {kind: 'local_workflow', label: '本机完整流程', description: '协议测试本机演示身份'}} : {})}});
    if (path === '/api/agent/status') return route.fulfill({json: {enabled: false, configured: false, notice_count: 0}});
    if (path === '/api/sessions') return route.fulfill({json: {items: []}});
    if (path === '/api/demo/workflow/context' && options.local === false) return route.fulfill({status: 404, json: {detail: 'not_found'}});
    if (req.method() === 'POST') {
      const post: Post = {path, body: req.postData() ? req.postDataJSON() : null, key: req.headers()['idempotency-key'], csrf: req.headers()['x-csrf-token']}; control.posts.push(post);
      if (control.hook && await control.hook(route, post)) return;
      if (path === '/api/demo/workflow/context') {control.prepared = true; return route.fulfill({json: context()});}
      if (path === '/api/screening/candidates') return route.fulfill({status: 201, json: batch()});
      if (path === '/api/jobs/runs') return route.fulfill({status: 202, json: control.job});
      if (path === '/api/reviews/cases') return route.fulfill({status: 201, json: control.review});
      if (path.endsWith('/decisions')) {
        const item = control.review.items.find(item => item.condition_id === post.body.condition_id)!;
        control.review.review_version++;
        item.latest_decision = post.body.command.decision; item.effective = true;
        item.latest_event = {event_id: `protocol-event-${control.review.review_version}`, reason: post.body.command.reason, actor_id: OWNER};
        if (item.latest_decision === 'request_materials') control.review.status = 'awaiting_materials';
        if (control.review.items.every(item => item.effective && ['confirm', 'reject'].includes(item.latest_decision || ''))) control.review.allowed_actions.push('close');
        return route.fulfill({json: control.review});
      }
      if (path.endsWith('/close')) {control.review.status = 'closed'; control.review.review_version++; control.review.allowed_actions = ['export']; return route.fulfill({json: control.review});}
      return route.fulfill({status: 400, json: {detail: 'unexpected_protocol_post'}});
    }
    if (path === '/api/demo/workflow/context') return route.fulfill({json: context()});
    if (path === '/api/jobs/status') return route.fulfill({json: {enabled: true, configured: options.live ?? true, live_calls_enabled: options.live ?? true, backend: 'dbos'}});
    if (path === '/api/reviews/status') return route.fulfill({json: {enabled: true, configured: true, worker_available: true}});
    if (path.startsWith('/api/company/profiles/')) return route.fulfill({json: {...PROFILE, payload: {display_name: COMPANY, provenance_kind: 'engineering_fixture'}}});
    if (path.startsWith('/api/company/materials/')) return route.fulfill({json: {...MATERIAL, filename: '协议测试虚构证明.txt'}});
    if (path.startsWith('/api/screening/candidates/')) return route.fulfill({json: batch()});
    if (path.startsWith('/api/jobs/runs/')) return control.denySaved ? route.fulfill({status: control.denySaved, json: {detail: 'current_identity_required'}}) : route.fulfill({json: control.job});
    if (path.endsWith('/export')) return route.fulfill({json: {case: control.review, schema: 'protocol-review-export'}});
    if (path.startsWith('/api/reviews/cases/')) return route.fulfill({json: control.review});
    return route.fulfill({json: {items: [], total: 0, limit: 20, offset: 0, has_more: false}});
  });
  return control;
}
function batch() {return {batch_id: 'protocol-batch', as_of: '2026-10-26T00:00:00Z', profile_revision: PROFILE, items: [{unit_id: 'protocol-unit', project_id: 'protocol-project', package_id: 'protocol-package', package_label: '第一包', title: PROJECT, budget_yuan: '100000', disposition: 'needs_verification', reasons: ['project_status_unknown'], missing_fields: ['project_status']}], total: 1, offset: 0, limit: 20, has_more: false, counts: {candidate: 0, excluded: 0, needs_verification: 1, total_units: 1}, source_state: SOURCE};}
async function open(page: Page) {await page.goto('/'); await expect(page.getByRole('heading', {name: '从项目筛选到人工复核'})).toBeVisible();}
async function selectProject(page: Page) {await page.getByRole('button', {name: '筛选项目', exact: true}).click(); await page.getByRole('button', {name: '选择此项目', exact: true}).click();}
async function startReview(page: Page) {await selectProject(page); await page.getByRole('button', {name: '开始 AI 资格核对', exact: true}).click(); await page.getByRole('button', {name: '进入人工复核', exact: true}).click(); await expect(page.getByRole('heading', {name: '逐项人工复核', exact: true})).toBeVisible();}
function itemCard(page: Page, index = 0) {return page.locator('.workflow-condition').filter({has: page.getByRole('heading', {name: `协议测试资格条件 ${index + 1}`, exact: true})});}

test('ordinary app has no workflow entry when the private context is absent', async ({page}) => {
  const control = await protocol(page, {local: false}); await page.goto('/');
  await expect(page.getByRole('button', {name: 'Agent 助手', exact: true})).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', {name: '完整流程', exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '企业画像与材料', exact: true})).toBeVisible(); expect(control.posts).toHaveLength(0);
});
test('fictional preparation and filtering are explicit; preview never submits a model job', async ({page}) => {
  const control = await protocol(page, {live: false}); await open(page); expect(control.posts).toHaveLength(0);
  await expect(page.getByText('虚构企业示例 · 本机流程演示', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '准备示例企业'}).click(); await selectProject(page);
  await expect(page.getByRole('button', {name: '开始 AI 资格核对'})).toBeDisabled();
  expect(control.posts.map(post => post.path)).toEqual(['/api/demo/workflow/context', '/api/screening/candidates']);
  expect(control.posts.every(post => post.csrf === 'protocol-csrf')).toBe(true);
  await expect(page.getByRole('button', {name: '授权更新与导入', exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Agent 助手', exact: true}).click(); await expect(page.getByRole('button', {name: 'Agent 助手', exact: true})).toHaveAttribute('aria-current', 'page');
});
test('guided flow binds saved references, all verdicts, independent reasons and controlled export', async ({page}) => {
  const control = await protocol(page, {prepared: true}); await open(page); await startReview(page);
  const submitted = control.posts.find(post => post.path === '/api/jobs/runs')!, created = control.posts.find(post => post.path === '/api/reviews/cases')!;
  expect(submitted).toMatchObject({csrf: 'protocol-csrf', body: {profile_revision: PROFILE, project_id: 'protocol-project', target_package_ids: ['protocol-package'], additional_material_revisions: [MATERIAL]}});
  expect(submitted.key).toMatch(/^[a-f0-9-]{36}$/); expect(created.body).toMatchObject({run_id: 'protocol-job', candidate_batch_id: 'protocol-batch', reviewer_ids: [OWNER]});
  for (const name of ['满足', '不满足', '信息不足', '证据冲突', '不适用']) await expect(page.locator('.workflow-condition .workflow-badge').filter({hasText: new RegExp(`^${name}$`)})).toHaveCount(1);
  await expect(page.getByRole('textbox', {name: /编号/})).toHaveCount(0);
  await expect(page.locator('.workflow-condition img')).toHaveCount(0);
  await expect(page.getByText('冲突证据：conflict-a、conflict-b')).toBeVisible();
  for (let index = 0; index < verdicts.length; index++) {
    const card = itemCard(page, index), reason = `协议测试独立理由 ${index + 1}`;
    await expect(card.getByRole('combobox')).toHaveValue(''); await expect(card.getByRole('button', {name: '保存该条人工决定'})).toBeDisabled();
    await card.getByRole('combobox').selectOption('confirm'); await expect(card.getByRole('button', {name: '保存该条人工决定'})).toBeDisabled();
    await card.getByRole('textbox', {name: '该条独立审核理由'}).fill(reason); await card.getByRole('button', {name: '保存该条人工决定'}).click();
    await expect(card.locator('.workflow-human')).toContainText(reason);
  }
  const decision = control.posts.find(post => post.path.endsWith('/decisions'))!;
  expect(decision.body).toMatchObject({revision_id: 'protocol-revision', assessment_id: 'protocol-assessment', condition_id: 'condition-0', expected_version: 1, command: {decision: 'confirm', expected_result_revision: 'protocol-result-v1', reason: '协议测试独立理由 1'}, supersedes_event_id: null});
  expect(control.review.revisions[0].result.human_review_state).toBe('unreviewed');
  await expect(page.getByRole('button', {name: '关闭本次复核'})).toBeDisabled(); await page.getByRole('textbox', {name: '关闭本次复核的理由'}).fill('协议测试逐项记录已核实'); await page.getByRole('button', {name: '关闭本次复核'}).click();
  await expect(page.getByText('复核已关闭', {exact: true})).toBeVisible();
  const download = page.waitForEvent('download'); await page.getByRole('button', {name: '导出审核包'}).click(); expect((await download).suggestedFilename()).toBe('bidops-review-protocol-case.json');
  const saved = await page.evaluate(key => localStorage.getItem(key)!, STORAGE);
  expect(saved).toContain('protocol-case'); expect(saved).not.toContain('独立理由'); expect(saved).not.toContain('原判断理由'); expect(saved).not.toContain('protocol-csrf');
});
test('refresh resumes server records without recreating the batch, model task or review', async ({page}) => {
  const control = await protocol(page, {prepared: true}); await open(page); await startReview(page); const posts = control.posts.length;
  await page.reload(); await expect(page.getByRole('heading', {name: '逐项人工复核', exact: true})).toBeVisible(); expect(control.posts).toHaveLength(posts);
  await expect(page.getByText(COMPANY, {exact: true})).toBeVisible(); await expect(itemCard(page).getByText('协议测试公告要求 1', {exact: true})).toBeVisible();
});
test('uncertain model submission keeps its key across reload and locks changed inputs', async ({page}) => {
  const control = await protocol(page, {prepared: true}); let first = true;
  control.hook = async (route, post) => {if (post.path !== '/api/jobs/runs' || !first) return false; first = false; await route.abort('failed'); return true;};
  await open(page); await selectProject(page); await page.getByRole('button', {name: '开始 AI 资格核对'}).click(); await expect(page.getByRole('alert')).toContainText('应答未确认');
  await expect(page.getByRole('button', {name: '筛选项目', exact: true})).toBeDisabled(); await expect(page.getByRole('button', {name: '已选择此项目'})).toBeDisabled();
  const before = control.posts.find(post => post.path === '/api/jobs/runs')!; await page.reload();
  await page.getByRole('button', {name: '重试同一资格核对'}).click(); await expect(page.locator('.workflow-job-state')).toHaveText('等待人工复核');
  const attempts = control.posts.filter(post => post.path === '/api/jobs/runs'); expect(attempts).toHaveLength(2); expect(attempts[1].key).toBe(before.key); expect(attempts[1].body).toEqual(before.body);
});
test('CAS conflict does not retry a human decision and requires refresh before saving again', async ({page}) => {
  const control = await protocol(page, {prepared: true}); let conflict = true;
  control.hook = async (route, post) => {if (!post.path.endsWith('/decisions') || !conflict) return false; conflict = false; control.review.review_version = 2; await route.fulfill({status: 409, json: {detail: 'review_version_conflict'}}); return true;};
  await open(page); await startReview(page); const first = itemCard(page);
  await first.getByRole('combobox').selectOption('reject'); await first.getByRole('textbox', {name: '该条独立审核理由'}).fill('协议测试版本冲突后仍需人工核对'); await first.getByRole('button', {name: '保存该条人工决定'}).click();
  await expect(page.getByRole('alert')).toContainText('系统没有自动重试人工决定'); await expect(first.getByRole('button', {name: '保存该条人工决定'})).toBeDisabled();
  expect(control.posts.filter(post => post.path.endsWith('/decisions'))).toHaveLength(1);
  await page.getByRole('button', {name: '刷新并继续'}).click(); await expect(first.getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue('协议测试版本冲突后仍需人工核对');
  await first.getByRole('button', {name: '保存该条人工决定'}).click(); expect(control.posts.filter(post => post.path.endsWith('/decisions'))[1].body.expected_version).toBe(2);
});
test('expired identity clears visible private data but retains pointers for login and continuation', async ({page}) => {
  const control = await protocol(page, {prepared: true}); await open(page); await startReview(page); control.denySaved = 401;
  await page.getByRole('button', {name: '刷新并继续'}).click(); await expect(page.getByRole('link', {name: '重新登录并继续'})).toHaveAttribute('href', '/api/auth/login');
  await expect(page.getByText(COMPANY, {exact: true})).toHaveCount(0); await expect(page.getByText('协议测试公告要求 1', {exact: true})).toHaveCount(0);
  const saved = await page.evaluate(key => localStorage.getItem(key), STORAGE); expect(saved).toContain('protocol-case'); expect(saved).toContain('protocol-job');
  control.denySaved = 0; const posts = control.posts.length; await page.reload(); await expect(page.getByRole('heading', {name: '逐项人工复核', exact: true})).toBeVisible(); expect(control.posts).toHaveLength(posts);
});
test('historical stale sources remain explicit and cannot acquire a human approval', async ({page}) => {
  const control = await protocol(page, {prepared: true, stale: true}); await open(page); await startReview(page);
  await expect(page.getByText(/公告为历史保存资料，当前新鲜度不足/).first()).toBeVisible();
  await expect(page.getByRole('button', {name: '保存该条人工决定'})).toHaveCount(0); await expect(page.getByRole('button', {name: '关闭本次复核'})).toHaveCount(0);
  expect(control.posts.filter(post => post.path.endsWith('/decisions'))).toHaveLength(0);
  await page.setViewportSize({width: 390, height: 844}); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
test('interrupted jobs resume and cancel only through allowed versioned operations', async ({page}) => {
  const control = await protocol(page, {prepared: true}); control.job.status = 'interrupted'; control.job.allowed_actions = ['resume']; control.job.result = null;
  control.hook = async (route, post) => {
    if (post.path.endsWith('/resume')) {control.job.status = 'running'; control.job.state_version++; control.job.allowed_actions = ['cancel']; await route.fulfill({json: control.job}); return true;}
    if (post.path.endsWith('/cancel')) {control.job.status = 'cancelled'; control.job.state_version++; control.job.allowed_actions = []; await route.fulfill({json: control.job}); return true;}
    return false;
  };
  await open(page); await selectProject(page); await page.getByRole('button', {name: '开始 AI 资格核对'}).click();
  expect(control.posts.filter(post => post.path.endsWith('/resume'))).toHaveLength(0);
  await page.getByRole('button', {name: '继续原核对任务'}).click(); await expect(page.locator('.workflow-job-state')).toHaveText('正在核对');
  expect(control.posts.find(post => post.path.endsWith('/resume'))!.body).toEqual({expected_state_version: 3});
  await expect(page.getByRole('button', {name: '开始另一轮筛选'})).toHaveCount(0);
  await page.getByRole('button', {name: '请求取消核对'}).click(); await expect(page.locator('.workflow-job-state')).toHaveText('核对已取消');
  expect(control.posts.find(post => post.path.endsWith('/cancel'))!.body).toEqual({expected_state_version: 4});
  expect(control.posts.filter(post => post.path === '/api/jobs/runs')).toHaveLength(1);
});
for (const status of ['failed', 'timed_out', 'interrupted']) test(`${status} can explicitly start another screening without deleting or rerunning history`, async ({page}) => {
  const control = await protocol(page, {prepared: true}); control.job.status = status; control.job.result = null;
  await open(page); await selectProject(page); await page.getByRole('button', {name: '开始 AI 资格核对'}).click();
  await expect(page.getByRole('button', {name: '进入人工复核'})).toHaveCount(0);
  await page.getByRole('button', {name: '开始另一轮筛选'}).click(); await expect(page.getByRole('button', {name: '筛选项目', exact: true})).toBeEnabled();
  await expect(page.locator('.workflow-job-state')).toHaveCount(0); expect(control.job.status).toBe(status); expect(control.posts.filter(post => post.path === '/api/jobs/runs')).toHaveLength(1);
  expect(await page.evaluate(key => localStorage.getItem(key), STORAGE)).not.toContain('protocol-job');
  await selectProject(page); await expect(page.getByRole('button', {name: '开始 AI 资格核对'})).toBeEnabled(); expect(control.posts.filter(post => post.path === '/api/jobs/runs')).toHaveLength(1);
});
for (const status of [403, 409]) test(`confirmed local capability keeps a visible ${status} context error without recreating seed`, async ({page}) => {
  const control = await protocol(page, {identityDemo: true});
  await page.route('**/api/demo/workflow/context', route => route.fulfill({status, json: {detail: status === 409 ? 'workflow_seed_manifest_invalid' : 'workflow_seed_access_denied'}}));
  await open(page); await expect(page.getByRole('button', {name: '完整流程', exact: true})).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('alert')).toBeVisible(); expect(control.posts).toHaveLength(0);
  if (status === 403) await expect(page.getByRole('link', {name: '重新登录并继续'})).toBeVisible();
  else await expect(page.getByRole('button', {name: '准备示例企业'})).toBeDisabled();
});
test('anonymous local login names the fixed demo identity and keeps normal enterprise copy elsewhere', async ({page}) => {
  await protocol(page, {identityDemo: true, authenticated: false}); await page.goto('/');
  await expect(page.getByRole('heading', {name: '进入本机演示'})).toBeVisible(); await expect(page.getByRole('link', {name: '进入本机演示'})).toHaveAttribute('href', '/api/auth/login');
  await expect(page.getByText('使用本机固定示例身份体验完整流程，非公司 SSO。', {exact: false})).toBeVisible();
  await expect(page.getByRole('link', {name: '企业统一登录'})).toHaveCount(0);
});
test('development StrictMode abort cleanup permits the second initialization to finish', async ({page}) => {
  const child = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '4273', '--strictPort'], {cwd: process.cwd(), stdio: 'ignore'});
  try {
    await expect.poll(async () => {try {return (await fetch('http://127.0.0.1:4273')).status;} catch {return 0;}}, {timeout: 10000}).toBe(200);
    const control = await protocol(page, {prepared: true, identityDemo: true});
    await page.goto('http://127.0.0.1:4273'); await expect(page.getByText(COMPANY, {exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: '筛选项目', exact: true})).toBeEnabled(); await selectProject(page);
    expect(control.posts.filter(post => post.path === '/api/jobs/runs')).toHaveLength(0);
  } finally {child.kill('SIGTERM');}
});
