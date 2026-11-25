import { expect, test, type Page } from '@playwright/test';

// Browser protocol fixtures only. They verify presentation and request contracts,
// not extraction quality, real IdP integration or live model behavior.
const PROFILE = {object_id: 'a'.repeat(32), version_id: 'b'.repeat(32), sha256: '1'.repeat(64)};
const MATERIAL = {object_id: 'c'.repeat(32), version_id: 'd'.repeat(32), sha256: '2'.repeat(64)};
const NOTICE = {object_id: 'unit-notice', version_id: 'unit-notice-version', sha256: '3'.repeat(64)};
const COMPANY_NAME = 'UNIT 私有工程画像';
function citation(side: 'notice' | 'company', index: number) {
  const quote = side === 'notice' ? `UNIT 公告要求 ${index}` : `UNIT 私有证明 ${index} <img src=x onerror=alert(1)>`;
  return {evidence_id: `${side}-${index}`, side, object_kind: side === 'notice' ? 'notice' : 'company_material', version: side === 'notice' ? NOTICE : MATERIAL, text_sha256: '4'.repeat(64), quote, start: 0, end: Array.from(quote).length, locator: 'normalized_text_v2', validity: {kind: 'unknown', reason: '证明有效期尚待核实'}};
}
function result(state = 'completed') {
  const statuses = ['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'];
  const conditions = statuses.map((status, index) => ({condition_id: `condition-${index + 1}`, original_text: `UNIT 完整条件 ${index + 1}`, hard_requirement: true, package_scope: {kind: 'specific', package_ids: ['unit-package'], evidence_ids: [`notice-${index}`]}, expression: {node_id: `node-${index}`, operator: 'any', text: '资质 A 或资质 B', evidence_ids: [`notice-${index}`], operands: [{node_id: `node-${index}-a`, operator: 'atom', text: '资质 A', evidence_ids: [`notice-${index}`], operands: []}, {node_id: `node-${index}-b`, operator: 'not', text: '不得属于限制情形', evidence_ids: [`notice-${index}`], operands: [{node_id: `node-${index}-c`, operator: 'atom', text: '限制情形', evidence_ids: [`notice-${index}`], operands: []}]}]}, validity: {kind: 'not_required'}, notice_evidence: [citation('notice', index)]}));
  const assessments = conditions.map((condition, index) => ({condition_id: condition.condition_id, status: statuses[index], reason: `UNIT 判断理由 ${index + 1}`, notice_evidence: condition.notice_evidence, company_evidence: index === 2 ? [] : [citation('company', index)], missing_items: index === 2 ? ['UNIT 缺少证明'] : [], conflicting_evidence_ids: index === 3 ? ['company-3', 'notice-3'] : [], scope_evidence_ids: index === 4 ? ['company-4'] : [], validity_evidence_ids: []}));
  return {run_id: 'unit-run', result_revision: 'unit-result-v1', state, manifest: {as_of: '2026-10-23T01:00:00Z', profile_revision: PROFILE, notice_revisions: [NOTICE], material_revisions: [MATERIAL]}, inventory: {conditions, unresolved: state === 'completed' ? [] : [{document_id: 'UNIT 未解析文档', segment_id: 'unparsed-segment', start: 200, end: 250, reason: 'UNIT 条款提取超时'}], missing_attachment_ids: state === 'completed' ? [] : ['UNIT 未下载附件']}, groups: [{primary_notice_revision: NOTICE, assessment: {assessment_id: 'unit-assessment', target_package_id: 'unit-package', conditions, assessments}}], recommendation: 'needs_materials', human_review_state: 'unreviewed', missing_items: ['UNIT 缺少证明'], provenance: {code_sha: 'unit-code', lock_sha256: '5'.repeat(64), prompt_version: 'unit-prompt-v2', prompt_sha256: '6'.repeat(64), rule_version: 'unit-rule-v1', requested_model: 'offline-ui-protocol', actual_models: ['offline-ui-protocol'], model_calls: 2, input_tokens: 100, output_tokens: 50, estimated_cost_cny: '0.00', reserved_cost_cny: '0.10', usage_complete: state === 'completed', parameters: {}}, ...(state !== 'completed' ? {error_code: 'provider_timeout', partial: {saved_conditions: 5}} : {})};
}
function batch(offset = 0) {
  const items = Array.from({length: offset === 0 ? 20 : 1}, (_, index) => {const n = offset + index; return {unit_id: `unit-${n}`, project_id: `unit-project-${n}`, package_id: `unit-package-${n}`, title: `UNIT 候选工程记录 ${n}`, disposition: n === 1 ? 'excluded' : n === 2 ? 'needs_verification' : 'candidate', reasons: n === 1 ? ['keyword_mismatch'] : n === 2 ? ['budget_unknown'] : [], missing_fields: n === 2 ? ['预算金额'] : []};});
  return {profile_revision: PROFILE, filters: {keyword: 'SAVED_KEYWORD', min_budget_yuan: '0.01', max_budget_yuan: null}, batch_id: 'unit-batch', manifest_sha256: '7'.repeat(64), as_of: '2026-10-23T01:00:00Z', counts: {total_units: 21, candidate: 19, excluded: 1, needs_verification: 1}, total: 21, offset, limit: 20, has_more: offset === 0, truncated: true, items};
}
async function protocol(page: Page, options: {enabled?: boolean; configured?: boolean; state?: string; emptyGroups?: boolean; mode?: string; authenticated?: boolean; jobsEnabled?: boolean; jobsConfigured?: boolean} = {}) {
  const control = {enabled: options.enabled ?? true, jobsEnabled: options.jobsEnabled ?? false, jobsStatusCode: 200, denyChecklist: false, denySaved: 0, denyMaterial: false, stale: false, posts: [] as {path: string; body: Record<string, unknown>; csrf: string | undefined}[], companyReads: 0};
  await page.route('**/api/auth/me', route => route.fulfill({json: {mode: options.mode || 'enterprise', authenticated: options.authenticated ?? true, organization: 'UNIT', user: {name: 'UNIT analyst', roles: ['analyst']}, permissions: ['agent:use', 'catalog:read'], csrf_token: 'unit-csrf'}}));
  await page.route('**/api/jobs/status', route => route.fulfill({status: control.jobsStatusCode, json: control.jobsStatusCode === 200 ? {enabled: control.jobsEnabled, configured: options.jobsConfigured ?? false, backend: 'dbos', live_calls_enabled: false} : {detail: 'job_backend_unavailable'}}));
  await page.route('**/api/jobs/runs**', route => route.fulfill({json: {items: [], total: 0, offset: 0, limit: 20}}));
  await page.route('**/api/agent/status', route => route.fulfill({json: {enabled: false, configured: false, notice_count: 0}}));
  await page.route('**/api/sessions', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/company/profiles**', route => {control.companyReads++; return route.fulfill({json: new URL(route.request().url()).pathname.endsWith('/profiles') ? {items: [{object_id: PROFILE.object_id, head_version: 'list-head-does-not-supply-a-hash'}], total: 1, offset: 0, limit: 20, has_more: false} : {...PROFILE, payload: {display_name: COMPANY_NAME}}});});
  await page.route('**/api/company/materials**', route => {const list = new URL(route.request().url()).pathname.endsWith('/materials'); if (control.denyMaterial && !list) return route.fulfill({status: 404, json: {detail: 'company_object_not_found'}}); return route.fulfill({json: list ? {items: control.denyMaterial ? [] : [{...MATERIAL, filename: 'UNIT 额外材料.txt'}], total: control.denyMaterial ? 0 : 1, offset: 0, limit: 20, has_more: false} : {...MATERIAL, filename: 'UNIT 额外材料.txt'}});});
  await page.route('**/api/screening/**', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (path.endsWith('/status')) return route.fulfill({json: {enabled: control.enabled, configured: options.configured ?? true, model: 'offline-ui-protocol', live_calls_enabled: false}});
    if (request.method() === 'POST') {control.posts.push({path, body: request.postDataJSON(), csrf: request.headers()['x-csrf-token']}); if (path.endsWith('/checklists') && control.denyChecklist) return route.fulfill({status: 409, json: {detail: 'screening_use_durable_jobs'}}); return route.fulfill({json: path.endsWith('/candidates') ? batch() : result(options.state)});}
    if (control.denySaved) return route.fulfill({status: control.denySaved, json: {detail: control.denySaved === 401 ? 'current_identity_required' : 'screening_result_not_found'}});
    const saved = path.includes('/candidates/') ? batch(Number(url.searchParams.get('offset') || 0)) : {...result(options.state), ...(options.emptyGroups ? {groups: []} : {})}; return route.fulfill({json: {...saved, source_state: {current: !control.stale, stale_reasons: control.stale ? ['profile_head_changed', 'notice_versions_changed'] : []}}});
  });
  return control;
}
async function open(page: Page) { await page.goto('/'); await page.getByRole('button', {name: '候选与资格核对', exact: true}).click(); }
async function choose(page: Page) { await page.getByRole('button', {name: `选择画像 ${PROFILE.object_id.slice(0, 12)}`}).click(); await expect(page.getByRole('heading', {name: COMPANY_NAME})).toBeVisible(); }
async function run(page: Page) { await choose(page); await page.getByRole('textbox', {name: '项目编号', exact: true}).fill('unit-project'); await page.getByRole('button', {name: '生成逐条资格清单'}).click(); await expect(page.getByRole('heading', {name: '资格核对结果'})).toBeVisible(); }

test('disabled screening is explicit and does not fetch company inputs or call models', async ({page}) => {
  const control = await protocol(page, {enabled: false});
  await open(page);
  await expect(page.getByRole('heading', {name: '此功能尚未开启'})).toBeVisible();
  await expect(page.getByText('页面不会发起模型调用。', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toHaveCount(0);
  expect(control.posts).toHaveLength(0); expect(control.companyReads).toBe(0);
});

for (const options of [{mode: 'local'}, {authenticated: false}]) test(`screening is unavailable outside an authenticated enterprise workspace ${JSON.stringify(options)}`, async ({page}) => {
  const control = await protocol(page, options); await page.goto('/');
  await expect(page.getByRole('button', {name: '候选与资格核对', exact: true})).toHaveCount(0);
  expect(control.posts).toHaveLength(0); expect(control.companyReads).toBe(0);
});

test('candidate requests pin the detail hash and fixed batch pagination preserves exclusions and unknowns', async ({page}) => {
  const control = await protocol(page); await open(page); await choose(page);
  await page.getByRole('textbox', {name: '项目关键词'}).fill('UNIT');
  await page.getByRole('textbox', {name: '预算下限（元）'}).fill('9007199254740993.01');
  await page.getByRole('button', {name: '生成固定候选批次'}).click();
  await expect(page.locator('.screening-counts')).toHaveText('全批 21 个采购单元 · 候选 19 · 已排除 1 · 待核实 1');
  await expect(page.getByText('与关键词不匹配', {exact: true})).toBeVisible();
  await expect(page.getByText('预算尚未确认', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '下一页候选'}).click();
  await expect(page.locator('.screening-page-range')).toContainText('第 21–21 项');
  await expect(page.locator('.screening-page-range')).toContainText('仅展示批次中的部分结果');
  await expect(page.getByRole('button', {name: '下一页候选'})).toBeDisabled();
  expect(control.posts).toHaveLength(1);
  expect(control.posts[0]).toMatchObject({csrf: 'unit-csrf', body: {profile_revision: PROFILE, filters: {keyword: 'UNIT', min_budget_yuan: '9007199254740993.01'}}});
  await expect(page.getByRole('button', {name: /批准|资格通过/})).toHaveCount(0);
});

test('all five conditions, both evidence sides, logic and recorded versions stay visible without human approval', async ({page}) => {
  const control = await protocol(page); await open(page); await choose(page);
  await page.getByRole('button', {name: '选择额外材料', exact: true}).click();
  await page.getByRole('checkbox', {name: /UNIT 额外材料.txt/}).check();
  await page.getByRole('textbox', {name: '项目编号', exact: true}).fill('unit-project');
  await page.getByRole('button', {name: '生成逐条资格清单'}).click();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  for (const text of ['满足', '不满足', '信息不足', '证据冲突', '不适用']) await expect(page.locator('.screening-condition .screening-badge').filter({hasText: new RegExp(`^${text}$`)})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'UNIT 完整条件 5'})).toBeVisible();
  const first = page.locator('.screening-condition').first();
  await first.getByText('查看条件逻辑与例外', {exact: true}).click();
  await expect(first.getByText('至少一项成立（OR）', {exact: true})).toBeVisible();
  await expect(first.getByText('否定（NOT）', {exact: true})).toBeVisible();
  await first.getByText('公告引用 · 公告正文 · notice-0', {exact: true}).click();
  await first.getByText('企业引用 · 材料 · company-0', {exact: true}).click();
  await expect(first.getByText('UNIT 公告要求 0', {exact: true})).toBeVisible();
  await expect(first.getByText('UNIT 私有证明 0 <img src=x onerror=alert(1)>', {exact: true})).toBeVisible();
  await expect(first.locator('blockquote img')).toHaveCount(0);
  await expect(first.getByText(MATERIAL.version_id, {exact: true})).toBeVisible();
  await expect(page.getByText('unit-prompt-v2', {exact: true})).toBeVisible();
  await expect(page.getByText('unit-rule-v1', {exact: true})).toBeVisible();
  await expect(page.locator('.screening-result .screening-badge').filter({hasText: '尚未人工复核'})).toBeVisible();
  expect(control.posts[0].body).toMatchObject({profile_revision: PROFILE, additional_material_revisions: [MATERIAL], project_id: 'unit-project', target_package_ids: []});
  await expect(page.getByRole('button', {name: /批准|确认满足|提交审核/})).toHaveCount(0);
});

test('partial timeout exposes retained conditions, unresolved clauses, missing attachment and incomplete usage', async ({page}) => {
  await protocol(page, {state: 'timed_out'}); await open(page); await run(page);
  await expect(page.getByRole('alert')).toContainText('本次核对未完整成功');
  await expect(page.getByRole('alert')).toContainText('模型请求超时');
  await expect(page.getByText('UNIT 条款提取超时', {exact: false})).toBeVisible();
  await expect(page.getByText('缺失或不可解析附件：UNIT 未下载附件', {exact: true})).toBeVisible();
  await expect(page.getByText('0.00（用量尚未确认完整）', {exact: true})).toBeVisible();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
});

for (const status of [401, 403, 404]) test(`saved-result ${status} clears all private results and inputs on refresh`, async ({page}) => {
  const control = await protocol(page); await open(page); await run(page);
  await page.getByRole('button', {name: '生成固定候选批次'}).click();
  await expect(page.locator('.screening-batch')).toBeVisible();
  control.denySaved = status;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByRole('alert').first()).toBeVisible();
  await expect(page.locator('.screening-result')).toHaveCount(0);
  await expect(page.locator('.screening-batch')).toHaveCount(0);
  await expect(page.getByRole('heading', {name: COMPANY_NAME})).toHaveCount(0);
  await expect(page.getByText('UNIT 完整条件 1', {exact: true})).toHaveCount(0);
  if (status === 401) await expect(page.getByRole('link', {name: '重新统一登录'})).toBeVisible();
  else {await expect(page.getByRole('textbox', {name: '项目编号', exact: true})).toHaveValue(''); await expect(page.getByRole('textbox', {name: '已有核对记录编号'})).toHaveValue('');}
});

test('turning the feature off removes saved private output and does not rerun a checklist', async ({page}) => {
  const control = await protocol(page); await open(page); await run(page); control.enabled = false;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByRole('heading', {name: '此功能尚未开启'})).toBeVisible();
  await expect(page.locator('.screening-result')).toHaveCount(0);
  expect(control.posts).toHaveLength(1);
});

test('missing model configuration leaves deterministic candidates usable and checklist disabled', async ({page}) => {
  const control = await protocol(page, {configured: false}); await open(page); await choose(page);
  await expect(page.getByText('尚未配置资格核对运行时，可以先筛选候选。', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeDisabled();
  await page.getByRole('button', {name: '生成固定候选批次'}).click();
  await expect(page.locator('.screening-batch')).toBeVisible();
  expect(control.posts.map(item => item.path)).toEqual(['/api/screening/candidates']);
});

test('saved run can be re-read without new model requests and mobile results do not overflow', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844}); const control = await protocol(page); await open(page);
  await page.getByRole('textbox', {name: '已有核对记录编号'}).fill('unit-run');
  await page.getByRole('button', {name: '读取已保存核对'}).click();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  expect(control.posts).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: test.info().outputPath('screening-mobile.png'), fullPage: true});
});


test('failed assessment retains every extracted condition without fabricating judgments', async ({page}) => {
  await protocol(page, {state: 'failed', emptyGroups: true}); await open(page);
  await page.getByRole('textbox', {name: '已有核对记录编号'}).fill('unit-run');
  await page.getByRole('button', {name: '读取已保存核对'}).click();
  await expect(page.getByRole('heading', {name: '尚无完整判断的已提取条件'})).toBeVisible();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  await expect(page.locator('.screening-condition .screening-badge').filter({hasText: '尚未完成核对'})).toHaveCount(5);
  await expect(page.locator('.screening-condition .screening-badge.satisfied')).toHaveCount(0);
});

test('source changes flag a stored result while preserving its original inputs and prompt version', async ({page}) => {
  const control = await protocol(page); await open(page); await run(page); control.stale = true;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByText('输入或来源状态已有变化', {exact: false})).toContainText('企业画像已有新版本');
  await expect(page.getByText('输入或来源状态已有变化', {exact: false})).toContainText('公告版本已有变化');
  await expect(page.locator('.screening-provenance').getByText(PROFILE.version_id, {exact: true})).toBeVisible();
  await expect(page.getByText('unit-prompt-v2', {exact: true})).toBeVisible();
  expect(control.posts).toHaveLength(1);
});


test('refresh rechecks a selected private material even when its list returns an empty successful page', async ({page}) => {
  const control = await protocol(page); await open(page); await choose(page);
  await page.getByRole('button', {name: '选择额外材料', exact: true}).click();
  await page.getByRole('checkbox', {name: /UNIT 额外材料.txt/}).check();
  control.denyMaterial = true;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByText('UNIT 额外材料.txt', {exact: false})).toHaveCount(0);
  await expect(page.getByRole('heading', {name: COMPANY_NAME})).toHaveCount(0);
});


test('saved batch exposes its own pinned profile and filters independently of the current form', async ({page}) => {
  const control = await protocol(page); await open(page); await choose(page);
  await page.getByRole('textbox', {name: '项目关键词'}).fill('NEW_UNSUBMITTED_INPUT');
  await page.getByRole('textbox', {name: '已有候选批次编号'}).fill('unit-batch');
  await page.getByRole('button', {name: '读取固定候选批次'}).click();
  await page.getByText('查看此批次的固定画像与筛选条件', {exact: true}).click();
  const saved = page.locator('.screening-batch-inputs');
  await expect(saved).toContainText(PROFILE.version_id); await expect(saved).toContainText(PROFILE.sha256);
  await expect(saved).toContainText('SAVED_KEYWORD'); await expect(saved).toContainText('0.01');
  await expect(saved).not.toContainText('NEW_UNSUBMITTED_INPUT');
  await expect(page.getByRole('textbox', {name: '项目关键词'})).toHaveValue('NEW_UNSUBMITTED_INPUT');
  expect(control.posts).toHaveLength(0);
});


for (const configured of [false, true]) test(`durable enabled blocks synchronous execution regardless of worker configured=${configured}`, async ({page}) => {
  const control = await protocol(page, {jobsEnabled: true, jobsConfigured: configured}); await open(page); await choose(page);
  await expect(page.getByText('新的资格核对统一进入持久队列', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeDisabled();
  await page.getByRole('button', {name: '生成固定候选批次'}).click();
  await expect(page.locator('.screening-batch')).toBeVisible();
  await page.getByRole('textbox', {name: '已有核对记录编号'}).fill('unit-run');
  await page.getByRole('button', {name: '读取已保存核对'}).click();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  expect(control.posts.map(item => item.path)).toEqual(['/api/screening/candidates']);
  await page.getByRole('button', {name: '前往持久核对任务', exact: true}).click();
  await expect(page.getByRole('heading', {name: '持久核对任务', exact: true})).toBeVisible();
  if (!configured) await expect(page.getByRole('button', {name: '提交持久核对任务'})).toBeDisabled();
});

test('a failed jobs status read disables sync while candidates and historical results remain readable', async ({page}) => {
  const control = await protocol(page); control.jobsStatusCode = 503; await open(page); await choose(page);
  await expect(page.getByText('尚未确认持久任务设置', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeDisabled();
  await page.getByRole('button', {name: '生成固定候选批次'}).click();
  await expect(page.locator('.screening-batch')).toBeVisible();
  await page.getByRole('textbox', {name: '已有核对记录编号'}).fill('unit-run');
  await page.getByRole('button', {name: '读取已保存核对'}).click();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  expect(control.posts.map(item => item.path)).toEqual(['/api/screening/candidates']);
  control.jobsStatusCode = 200;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeEnabled();
  control.jobsStatusCode = 503;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeDisabled();
  await expect(page.locator('.screening-condition')).toHaveCount(5);
});

for (const status of [401, 403, 404]) test(`jobs status ${status} clears cached private data and never admits a sync fallback`, async ({page}) => {
  const control = await protocol(page); await open(page); await run(page);
  control.jobsStatusCode = status;
  await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('.screening-result')).toHaveCount(0);
  await expect(page.getByRole('heading', {name: COMPANY_NAME})).toHaveCount(0);
  if (status !== 401) await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeDisabled();
  expect(control.posts).toHaveLength(1);
});

test('a server 409 after a stale disabled status switches to the durable entry without retrying', async ({page}) => {
  const control = await protocol(page); await open(page); await choose(page);
  control.denyChecklist = true;
  await page.getByRole('textbox', {name: '项目编号', exact: true}).fill('unit-project');
  await page.getByRole('button', {name: '生成逐条资格清单'}).click();
  await expect(page.getByRole('alert')).toContainText('持久任务已开启');
  await expect(page.getByRole('button', {name: '生成逐条资格清单'})).toBeDisabled();
  await expect(page.getByRole('button', {name: '前往持久核对任务', exact: true})).toBeVisible();
  await expect(page.locator('.screening-result')).toHaveCount(0);
  expect(control.posts).toHaveLength(1);
});
