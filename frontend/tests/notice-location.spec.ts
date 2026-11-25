import { expect, test, type Page, type Route } from '@playwright/test';

// Protocol fixtures only: actual saved PDF receipts and the HTTP endpoint are
// covered separately by ingestion-real.spec.ts. No model/page claim is trusted.
const VERSION = {object_id: 'unit-attachment', version_id: 'unit-attachment-v1', sha256: 'a'.repeat(64)};
const RECEIPT = 'b'.repeat(64);
const EVIDENCE = {evidence_id: 'unit-pdf-ref', side: 'notice', object_kind: 'attachment', version: VERSION, text_sha256: 'c'.repeat(64), quote: 'UNIT 固定附件引文', start: 15, end: 28, locator: 'normalized_text_v2', page: 99, validity: {kind: 'not_required'}};
const COMPANY = {...EVIDENCE, evidence_id: 'unit-company-ref', side: 'company', object_kind: 'company_material', quote: 'UNIT 私有企业证明', version: {...VERSION, object_id: 'unit-company', version_id: 'unit-company-v1'}};
type View = 'screening' | 'jobs' | 'reviews';
const NAV: Record<View, string> = {screening: '候选与资格核对', jobs: '持久核对任务', reviews: '人工复核'};
const DETAIL: Record<View, string> = {screening: '.screening-result', jobs: '.jobs-detail', reviews: '.reviews-detail'};

function result(evidence = EVIDENCE) {
  const condition = {condition_id: 'unit-condition', original_text: 'UNIT PDF 条件', hard_requirement: true, package_scope: {kind: 'all', package_ids: []}, expression: {operator: 'atom', text: 'UNIT 条件', operands: []}, validity: {kind: 'not_required'}, notice_evidence: [evidence]};
  const judgment = {condition_id: condition.condition_id, status: 'information_insufficient', reason: 'UNIT 原判断', notice_evidence: [evidence], company_evidence: [COMPANY], missing_items: [], conflicting_evidence_ids: []};
  return {run_id: 'unit-result', result_revision: 'unit-result-v1', state: 'completed', human_review_state: 'unreviewed', recommendation: 'review_recommended', manifest: {as_of: '2026-10-23T00:00:00Z'}, inventory: {conditions: [condition], unresolved: []}, groups: [{primary_notice_revision: VERSION, assessment: {assessment_id: 'unit-assessment', target_package_id: null, conditions: [condition], assessments: [judgment]}}], missing_items: [], provenance: {prompt_version: 'unit-offline-prompt'}};
}

async function protocol(page: Page) {
  const control = {evidence: structuredClone(EVIDENCE), calls: [] as {body: unknown; csrf?: string; method: string}[], denial: 0, normalized: false, unavailable: false, hold: undefined as ((route: Route) => Promise<void>) | undefined};
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({json: {mode: 'enterprise', authenticated: true, organization: 'UNIT', user: {name: 'UNIT reviewer', roles: ['analyst', 'reviewer'], subject_id: 'unit-reader'}, permissions: ['catalog:read', 'agent:use', 'review:decide'], csrf_token: 'unit-location-csrf'}});
    if (path === '/api/notice-evidence/location') {
      control.calls.push({body: req.postDataJSON(), csrf: req.headers()['x-csrf-token'], method: req.method()});
      if (control.hold) return control.hold(route);
      if (control.denial) return route.fulfill({status: control.denial, json: {detail: 'current_identity_required'}});
      if (control.unavailable) return route.fulfill({status: 422, json: {detail: 'notice_version_or_evidence_unavailable'}});
      return route.fulfill({json: {evidence: control.evidence, location: control.normalized ? {kind: 'normalized_text_v2', start: 15, end: 28} : {kind: 'pdf_pages', pages: [2, 3], parser_receipt_sha256: RECEIPT}}});
    }
    if (path.endsWith('/status')) return route.fulfill({json: {enabled: !path.includes('/agent/'), configured: true, worker_available: false, backend: 'dbos', live_calls_enabled: false, model: 'offline-fixture', notice_count: 0}});
    if (path === '/api/screening/runs/unit-result') return route.fulfill({json: result(control.evidence)});
    if (path === '/api/jobs/runs/unit-job') return route.fulfill({json: {run_id: 'unit-job', status: 'completed', state_version: 1, progress: {phase: 'completed', completed: 1, total: 1}, created_at: 1792710000, updated_at: 1792710000, result_id: 'unit-result', result_revision: 'unit-result-v1', source_state: {current: true, stale_reasons: []}, usage: {model_calls: 0, confirmed_calls: 0, unknown_calls: 0, estimated_cost_cny: '0', reserved_cost_cny: '0', offline: true}, allowed_actions: [], result: result(control.evidence)}});
    if (path === '/api/reviews/cases/unit-case') {
      const saved = result(control.evidence), group = saved.groups[0].assessment;
      return route.fulfill({json: {case_id: 'unit-case', owner_id: 'unit-owner', head_revision_id: 'unit-revision', review_version: 1, status: 'open', source_state: {current: true, stale_reasons: []}, allowed_actions: [], assignments: ['unit-reader'], candidate_batch: null, revisions: [{revision_id: 'unit-revision', parent_revision_id: null, run_id: 'unit-job', result_id: saved.run_id, result_revision: saved.result_revision, inputs_manifest_sha256: 'd'.repeat(64), created_at: '2026-10-23T00:00:00Z', manifest: {...saved.manifest, project_id: 'unit-project'}, result: saved, provenance: saved.provenance, source_state: {current: true, stale_reasons: []}, is_head: true}], items: [{revision_id: 'unit-revision', assessment_id: group.assessment_id, condition_id: 'unit-condition', condition: group.conditions[0], agent_assessment: group.assessments[0], latest_decision: null, latest_event: null, effective: false, ineffective_reasons: []}], events: [], successors: []}});
    }
    return route.fulfill({json: {items: [], total: 0, offset: 0, limit: 20, has_more: false}});
  });
  return control;
}

async function read(page: Page, view: View) {
  await page.getByRole('button', {name: NAV[view], exact: true}).click();
  const labels = {screening: ['已有核对记录编号', '读取已保存核对', 'unit-result'], jobs: ['已有任务编号', '读取任务', 'unit-job'], reviews: ['已有复核编号', '读取复核', 'unit-case']}[view];
  await page.getByRole('textbox', {name: labels[0], exact: true}).fill(labels[2]);
  await page.getByRole('button', {name: labels[1], exact: true}).click();
  await expect(page.locator(DETAIL[view])).toBeVisible();
  await page.locator('summary').filter({hasText: 'unit-pdf-ref'}).first().click();
}

for (const view of ['screening', 'jobs', 'reviews'] as const) test(`${view}: explicitly checks the exact reference with CSRF and renders only saved PDF pages`, async ({page}) => {
  const control = await protocol(page); await page.goto('/'); await read(page, view);
  expect(control.calls).toHaveLength(0);
  await expect(page.locator('.notice-evidence-location')).toHaveCount(1);
  await expect(page.getByText('第 99 页', {exact: false})).toHaveCount(0);
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect(page.getByText('服务端核对页码：第 2、3 页', {exact: true})).toBeVisible();
  await expect(page.locator('.notice-evidence-location-result code')).toHaveText(RECEIPT);
  expect(control.calls).toEqual([{body: EVIDENCE, csrf: 'unit-location-csrf', method: 'POST'}]);
  await expect(page.locator(DETAIL[view])).toContainText('UNIT 原判断');
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
});

for (const [view, status] of [['screening', 401], ['jobs', 403], ['reviews', 404]] as const) test(`${view}: location ${status} clears private results and reference data`, async ({page}) => {
  const control = await protocol(page); control.denial = status;
  await page.goto('/'); await read(page, view);
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect(page.locator(DETAIL[view])).toHaveCount(0);
  await expect(page.getByRole('alert').first()).toBeVisible();
  await expect(page.getByText('UNIT 私有企业证明', {exact: true})).toHaveCount(0);
  await expect(page.locator('.notice-evidence-location')).toHaveCount(0);
});

test('older plain-text attachments report only character bounds without inventing PDF pages', async ({page}) => {
  const control = await protocol(page); control.normalized = true;
  await page.goto('/'); await read(page, 'screening');
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect(page.locator('.notice-evidence-location-result')).toContainText('没有已保存的 PDF 页定位');
  await expect(page.locator('.notice-evidence-location-result')).toContainText('[15, 28)');
  await expect(page.getByText('服务端核对页码', {exact: false})).toHaveCount(0);
  await expect(page.locator('.notice-evidence-location-result code')).toHaveCount(0);
});

test('unavailable fixed evidence shows an unconfirmed error and permits an explicit retry', async ({page}) => {
  const control = await protocol(page); control.unavailable = true;
  await page.goto('/'); await read(page, 'jobs');
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect(page.locator('.notice-evidence-location [role=alert]')).toContainText('未确认 PDF 页号');
  await expect(page.locator('.jobs-detail')).toBeVisible();
  control.unavailable = false;
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect(page.getByText('服务端核对页码：第 2、3 页', {exact: true})).toBeVisible();
  expect(control.calls).toHaveLength(2);
});

test('leaving a page aborts an in-flight lookup and does not retain its late receipt', async ({page}) => {
  const control = await protocol(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  control.hold = async route => {await held; await route.fulfill({json: {evidence: EVIDENCE, location: {kind: 'pdf_pages', pages: [7], parser_receipt_sha256: RECEIPT}}}).catch(() => {});};
  await page.goto('/'); await read(page, 'screening');
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect.poll(() => control.calls.length).toBe(1);
  await page.getByRole('button', {name: '持久核对任务', exact: true}).click(); release();
  await expect(page.getByRole('heading', {name: '持久核对任务', exact: true})).toBeVisible();
  control.hold = undefined; await read(page, 'screening');
  await expect(page.locator('.notice-evidence-location-result')).toHaveCount(0);
  await expect(page.getByRole('button', {name: '核对PDF页定位', exact: true})).toBeEnabled();
});

test('a reused condition with a different fixed reference discards the earlier page receipt', async ({page}) => {
  const control = await protocol(page); await page.goto('/'); await read(page, 'reviews');
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect(page.locator('.notice-evidence-location-result')).toBeVisible();
  control.evidence.version.version_id = 'unit-attachment-v2';
  await page.getByRole('button', {name: '刷新复核工作区', exact: true}).click();
  await expect(page.locator('.notice-evidence-location-result')).toHaveCount(0);
  await expect(page.getByRole('button', {name: '核对PDF页定位', exact: true})).toBeEnabled();
  await page.getByRole('button', {name: '核对PDF页定位', exact: true}).click();
  await expect.poll(() => control.calls.length).toBe(2);
  expect(control.calls[1].body).toEqual(control.evidence);
});
