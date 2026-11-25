import { expect, request as newRequest, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Actual local review/Jobs API, DBOS, SQLite ACL and offline Pydantic AI. The
// people, qualifications and signed external-IdP transport are UNIT fixtures.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const JOB_READY_TIMEOUT_MS = 30_000;
const REVIEW_PROTOCOL_BUDGET_MS = 30_000;
type Ref = {object_id: string; version_id: string; sha256: string};
type Identity = {csrf_token: string; user: {subject_id: string}};
type ReviewEvent = {event_id: string; reason: string; review_event?: {command: {decision: string}}};
type ReviewCase = {case_id: string; review_version: number; head_revision_id: string; status: string; source_state: {current: boolean}; items: {revision_id: string; assessment_id: string; condition_id: string; latest_decision: string | null; latest_event: ReviewEvent | null; effective: boolean; agent_assessment: {status: string}}[]; events: ReviewEvent[]; revisions: {revision_id: string; run_id: string; result_revision: string; result: {human_review_state: string}}[]; successors: {run_id: string}[]};
type Fixed = {profile: Ref; materials: Ref[]; name: string; payload: Record<string, unknown>};
let backend: ChildProcess | undefined, preview: ChildProcess | undefined;
let origin = '', scratch = '', logs = '';
async function freePort() {const server = createServer(); await new Promise<void>(done => server.listen(0, '127.0.0.1', done)); const value = server.address(), port = value && typeof value === 'object' ? value.port : 0; await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done())); return port;}
async function ready() {for (let i = 0; i < 250; i++) {if (backend?.exitCode != null) throw new Error(`Review fixture exited: ${logs}`); try {if ((await fetch(`${origin}/api/health`)).ok) return;} catch {/* Local fixture startup. */} await new Promise(done => setTimeout(done, 100));} throw new Error(`Review fixture did not start: ${logs}`);}
async function login(api: APIRequestContext, subject: string, roles: string[], context?: BrowserContext): Promise<Identity> {
  const start = await api.get(`${origin}/api/auth/login`, {maxRedirects: 0}); expect(start.status()).toBe(302); const fields = new URL(start.headers().location).searchParams;
  const callback = await api.get(`${origin}/api/auth/callback`, {maxRedirects: 0, params: {state: fields.get('state')!, code: JSON.stringify({sub: subject, roles, nonce: fields.get('nonce'), challenge: fields.get('code_challenge')})}});
  expect(callback.status(), await callback.text()).toBe(303); if (context) await context.addCookies((await api.storageState()).cookies);
  const response = await api.get(`${origin}/api/auth/me`), value = await response.json(); expect(value.authenticated).toBe(true); return value;
}
function headers(identity: Identity) {return {'Origin': origin, 'X-CSRF-Token': identity.csrf_token};}
async function post(api: APIRequestContext, identity: Identity, path: string, data: unknown) {const response = await api.post(`${origin}/api${path}`, {headers: headers(identity), data}); expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(true); return response.json();}
async function inputs(api: APIRequestContext, identity: Identity, name: string): Promise<Fixed> {
  const materials: Ref[] = [], evidence: Record<string, unknown>[] = [];
  for (const [index, text] of ['UNIT_CERTIFICATE engineering proof', 'UNIT_NEGATIVE and UNIT_EXCEPTION engineering proof'].entries()) {
    const response = await api.post(`${origin}/api/company/materials`, {headers: {...headers(identity), 'Content-Type': 'text/plain', 'X-Filename-Encoded': encodeURIComponent(`UNIT 人审私有材料 ${name}-${index}.txt`)}, data: Buffer.from(text)});
    expect(response.status(), await response.text()).toBe(201); const value = await response.json(), ref = {object_id: value.object_id, version_id: value.version_id, sha256: value.sha256}; materials.push(ref);
    evidence.push(await post(api, identity, `/company/materials/${ref.object_id}/versions/${ref.version_id}/citations`, {start: 0, end: text.length}));
  }
  const payload = {display_name: name, provenance_kind: 'engineering_fixture', facts: [{fact_id: 'UNIT_REVIEW_FACT', kind: 'qualification', label: 'UNIT review engineering fixture', value: 'UNIT fixed fact', status: 'document_supported', evidence}]};
  return {profile: await post(api, identity, '/company/profiles', {payload}), materials, name, payload};
}
async function share(api: APIRequestContext, owner: Identity, subject: Identity, fixed: Fixed) {for (const ref of [fixed.profile, ...fixed.materials]) await post(api, owner, `/company/objects/${ref.object_id}/grants`, {subject_id: subject.user.subject_id, actions: ['read']});}
async function waitJob(api: APIRequestContext, id: string) {
  const started = performance.now(); let status = 'not_observed';
  try {
    await test.step(`native job ${id} reaches waiting_review within 30 seconds`, async () => {
      await expect.poll(async () => {
        const response = await api.get(`${origin}/api/jobs/runs/${id}`); expect(response.status(), await response.text()).toBe(200);
        const saved = await response.json(); status = saved.status;
        if (status === 'waiting_review') expect(saved.usage).toMatchObject({offline: true, unknown_calls: 0, estimated_cost_cny: '0', reserved_cost_cny: '0'});
        return status;
      }, {timeout: JOB_READY_TIMEOUT_MS, intervals: [100, 200, 500]}).toBe('waiting_review');
    }, {timeout: JOB_READY_TIMEOUT_MS});
  } finally {
    test.info().annotations.push({type: 'native-job-ready', description: `${id}: ${status} after ${Math.round(performance.now() - started)}ms (limit ${JOB_READY_TIMEOUT_MS}ms)`});
  }
}
async function seedCase(api: APIRequestContext, owner: Identity, fixed: Fixed) {
  const seed = JSON.parse(await readFile(join(scratch, 'seed.json'), 'utf8'));
  const batch = await post(api, owner, '/screening/candidates', {profile_revision: fixed.profile, filters: {keyword: 'UNIT_SCREENING_PROJECT'}});
  const response = await api.post(`${origin}/api/jobs/runs`, {headers: {...headers(owner), 'Idempotency-Key': crypto.randomUUID()}, data: {profile_revision: fixed.profile, project_id: seed.project_id, target_package_ids: [], additional_material_revisions: []}});
  expect(response.status(), await response.text()).toBe(202); const saved = await response.json(); await waitJob(api, saved.run_id); return {run: saved.run_id as string, batch: batch.batch_id as string, project: seed.project_id as string};
}
async function open(page: Page, id?: string) {await page.goto(origin); await page.getByRole('button', {name: '人工复核', exact: true}).click(); await expect(page.getByRole('heading', {name: '逐项人工复核', exact: true})).toBeVisible(); if (id) {await page.getByRole('textbox', {name: '已有复核编号', exact: true}).fill(id); await page.getByRole('button', {name: '读取复核', exact: true}).click(); await expect(page.locator('.reviews-case-id')).toHaveText(id);}}
async function getCase(api: APIRequestContext, id: string): Promise<ReviewCase> {const response = await api.get(`${origin}/api/reviews/cases/${id}`); expect(response.status(), await response.text()).toBe(200); return response.json();}
async function countCalls() {return JSON.parse(await readFile(join(scratch, 'calls.json'), 'utf8')).length as number;}
function condition(page: Page, index: number) {return page.locator('.reviews-condition').filter({has: page.getByRole('heading', {name: /^UNIT_[A-E]/})}).nth(index);}
async function decideUI(page: Page, index: number, decision: string, reason: string) {const card = condition(page, index); await card.getByRole('combobox', {name: '该条人工决定'}).selectOption(decision); await card.getByRole('textbox', {name: '该条独立审核理由'}).fill(reason); const response = page.waitForResponse(value => value.url().endsWith('/decisions') && value.request().method() === 'POST'); await card.getByRole('button', {name: '提交该条人工决定'}).click(); return response;}
async function readDownload(page: Page) {const event = page.waitForEvent('download'); await page.getByRole('button', {name: '导出受控 JSON 审核包'}).click(); const download = await event; return JSON.parse(await readFile((await download.path())!, 'utf8'));}

test.describe.configure({mode: 'serial'});
test.beforeAll(async () => {
  const backendPort = await freePort(), previewPort = await freePort(); origin = `http://127.0.0.1:${previewPort}`; scratch = await realpath(await mkdtemp(join(tmpdir(), 'bidops-reviews-browser-')));
  backend = spawn('uv', ['run', '--frozen', '--no-sync', 'python', 'tests/fixtures/reviews_browser.py'], {cwd: root, detached: true, env: {...process.env, PYTHONPATH: join(root, 'backend'), UV_OFFLINE: '1', BIDOPS_NETWORK_ENABLED: 'false', BIDOPS_REVIEWS_BROWSER_DATA: scratch, BIDOPS_REVIEWS_BROWSER_ORIGIN: origin, BIDOPS_REVIEWS_BROWSER_PORT: String(backendPort)}});
  backend.stdout?.on('data', value => {logs += String(value);}); backend.stderr?.on('data', value => {logs += String(value);});
  preview = spawn('npm', ['run', 'preview', '--', '--host', '127.0.0.1', '--port', String(previewPort), '--strictPort'], {cwd: join(root, 'frontend'), detached: true, env: {...process.env, BIDOPS_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`}}); preview.stderr?.on('data', value => {logs += String(value);}); await ready();
});
test.afterAll(async () => {
  const children = [preview, backend].filter((value): value is ChildProcess => !!value); for (const child of children) if (child.pid && child.exitCode === null) process.kill(-child.pid, 'SIGTERM');
  await Promise.all(children.map(child => new Promise<void>(done => {if (child.exitCode !== null) return done(); child.once('exit', () => done()); setTimeout(() => {if (child.pid && child.exitCode === null) {try {process.kill(-child.pid, 'SIGKILL');} catch {/* Already exited. */}} done();}, 5000).unref();}))); if (scratch) await rm(scratch, {recursive: true, force: true});
});

test('actual offline review preserves five states and evidence, appends corrections, CAS-conflicts, recomputes all items and exports the full chain', async ({page, context}) => {
  // Two separately bounded native computations, plus the signed login, review,
  // supplement/attachment and export protocol. The default whole-test 30s would
  // truncate the successor's own 30s allowance after the first computation.
  test.setTimeout(2 * JOB_READY_TIMEOUT_MS + REVIEW_PROTOCOL_BUDGET_MS);
  const ownerApi = await newRequest.newContext(), reviewerApi = await newRequest.newContext();
  try {
    const owner = await login(ownerApi, 'UNIT-review-owner', ['analyst', 'reviewer'], context), reviewer = await login(reviewerApi, 'UNIT-review-second', ['reviewer']);
    const fixed = await inputs(ownerApi, owner, 'UNIT 人审工程闭环'), seeded = await seedCase(ownerApi, owner, fixed);
    await open(page); await page.getByRole('textbox', {name: '等待审核的任务编号', exact: true}).fill(seeded.run); await page.getByRole('textbox', {name: '固定候选批次编号', exact: true}).fill(seeded.batch); await page.getByRole('textbox', {name: '指定审核人主体编号（逗号或换行分隔）', exact: true}).fill(`${owner.user.subject_id}\n${reviewer.user.subject_id}`);
    const creating = page.waitForResponse(response => response.url().endsWith('/reviews/cases') && response.request().method() === 'POST'); await page.getByRole('button', {name: '创建复核案件'}).click(); const created = await creating; expect(created.status(), await created.text()).toBe(201); let current: ReviewCase = await created.json();
    await expect(page.locator('.reviews-case-id')).toHaveText(current.case_id); expect(current.items).toHaveLength(5); expect(new Set(current.items.map(value => value.agent_assessment.status))).toEqual(new Set(['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'])); const initialCalls = await countCalls(); expect(initialCalls).toBeGreaterThan(0);
    // An assignment does not grant the source objects. Give the reviewer actual
    // read ACLs only after proving the assigned case is still hidden without them.
    const hidden = await reviewerApi.get(`${origin}/api/reviews/cases/${current.case_id}`); expect(hidden.status()).toBe(404); await share(ownerApi, owner, reviewer, fixed);
    await condition(page, 0).locator('details').filter({hasText: '企业证明材料'}).first().locator('summary').click(); await expect(condition(page, 0).getByText(/UNIT_CERTIFICATE engineering proof/)).toBeVisible();
    const concurrentItem = current.items[0]; await post(reviewerApi, reviewer, `/reviews/cases/${current.case_id}/decisions`, {revision_id: concurrentItem.revision_id, assessment_id: concurrentItem.assessment_id, condition_id: concurrentItem.condition_id, expected_version: current.review_version, command: {decision: 'confirm', expected_result_revision: current.revisions[0].result_revision, reason: 'UNIT 第二审核人先保存', idempotency_key: crypto.randomUUID()}, supersedes_event_id: null});
    const conflict = await decideUI(page, 1, 'reject', 'UNIT 旧版本草稿需保留'); expect(conflict.status()).toBe(409); await expect(page.getByRole('alert')).toContainText('没有自动重试'); await expect(condition(page, 1).getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue('UNIT 旧版本草稿需保留'); await page.getByRole('button', {name: '刷新复核工作区'}).click(); current = await getCase(ownerApi, current.case_id); await expect(page.locator('.reviews-version')).toHaveText(String(current.review_version));
    await condition(page, 0).getByRole('button', {name: '追加更正该条理由与决定'}).click(); const correction = await decideUI(page, 0, 'reject', 'UNIT owner追加更正原理由'); expect(correction.status(), await correction.text()).toBe(200); current = await correction.json(); expect(current.events.filter(value => value.reason.includes('UNIT 第二审核人先保存'))).toHaveLength(1); expect(current.events.filter(value => value.reason.includes('UNIT owner追加更正'))).toHaveLength(1);
    const materials = await decideUI(page, 2, 'request_materials', 'UNIT 条件C需要新增证明'); expect(materials.status(), await materials.text()).toBe(200); current = await materials.json(); const trigger = current.items[2].latest_event!.event_id;
    const extraText = 'UNIT_C supplemental engineering proof'; const upload = await ownerApi.post(`${origin}/api/company/materials`, {headers: {...headers(owner), 'Content-Type': 'text/plain', 'X-Filename-Encoded': encodeURIComponent('UNIT 新增补充证明.txt')}, data: Buffer.from(extraText)}); expect(upload.status(), await upload.text()).toBe(201); const extra = await upload.json();
    await page.getByRole('combobox', {name: '触发补材料的人工事件'}).selectOption(trigger); await page.getByRole('button', {name: '选择新计算的画像版本'}).click(); await page.getByRole('button', {name: `选择画像 ${fixed.profile.object_id.slice(0, 12)}`}).click(); await page.getByRole('button', {name: '选择新增证明材料'}).click(); await page.getByRole('checkbox', {name: /UNIT 新增补充证明.txt/}).check(); await page.getByRole('textbox', {name: '新计算项目编号', exact: true}).fill(seeded.project); await page.getByRole('textbox', {name: '补材料与重算理由'}).fill('UNIT 新增材料后全量重算');
    const pending = page.waitForResponse(response => response.url().endsWith('/successors')); await page.getByRole('button', {name: '创建关联的新计算任务'}).click(); const accepted = await pending; expect(accepted.status(), await accepted.text()).toBe(202); current = await accepted.json(); expect(accepted.request().postDataJSON().request.additional_material_revisions[0]).toMatchObject({object_id: extra.object_id, version_id: extra.version_id, sha256: extra.sha256}); const nextJob = current.successors.at(-1)!.run_id; await waitJob(ownerApi, nextJob);
    await page.getByRole('textbox', {name: '已计算就绪的新任务编号'}).fill(nextJob); await page.getByRole('textbox', {name: '接入新修订理由'}).fill('UNIT 接入重算结果，每条重新审核'); const attaching = page.waitForResponse(response => response.url().endsWith('/revisions')); await page.getByRole('button', {name: '显式接入已完成的新修订'}).click(); const attached = await attaching; expect(attached.status(), await attached.text()).toBe(200); current = await attached.json(); expect(current.revisions).toHaveLength(2); expect(current.items.every(value => value.latest_decision === null)).toBe(true); expect(await countCalls()).toBeGreaterThan(initialCalls);
    await expect(condition(page, 0).locator('.reviews-human-decision')).toContainText('尚未人工决定'); for (let index = 0; index < current.items.length; index++) {const response = await decideUI(page, index, index === 1 ? 'reject' : 'confirm', `UNIT 新修订逐项理由 ${index + 1}`); expect(response.status(), await response.text()).toBe(200); current = await response.json();}
    const calls = await countCalls(); await page.reload(); await open(page, current.case_id); expect(await countCalls()).toBe(calls); await page.getByRole('textbox', {name: '关闭理由', exact: true}).fill('UNIT 内部是否跟进复核已结束'); const closing = page.waitForResponse(response => response.url().endsWith('/close')); await page.getByRole('button', {name: '关闭本次内部复核'}).click(); const closed = await closing; expect(closed.status(), await closed.text()).toBe(200); current = await closed.json(); expect(current.status).toBe('closed');
    const exported = await readDownload(page); expect(exported.format_version).toBe('bidops_review_v1'); expect(exported.automatic_bidding).toBe(false); expect(exported.case.revisions).toHaveLength(2); expect(exported.case.candidate_batch.result.items.length).toBeGreaterThan(0); expect(exported.case.events.some((value: ReviewEvent) => value.reason === 'UNIT 第二审核人先保存')).toBe(true); for (const revision of current.revisions) expect(revision.result.human_review_state).toBe('unreviewed');
    // An actual profile head change invalidates the human projection while the
    // old Agent result and append-only decisions remain available as history.
    const changed = await ownerApi.put(`${origin}/api/company/profiles/${fixed.profile.object_id}`, {headers: headers(owner), data: {payload: {...fixed.payload, display_name: 'UNIT 画像新版本导致旧复核失效'}, expected_version: fixed.profile.version_id}}); expect(changed.status(), await changed.text()).toBe(200); await page.getByRole('button', {name: '刷新复核工作区'}).click(); await expect(page.getByText('来源或输入已有变化', {exact: false})).toBeVisible(); const stale = await getCase(ownerApi, current.case_id); expect(stale.source_state.current).toBe(false); expect(stale.items.every(value => !value.effective)).toBe(true); expect((await readDownload(page)).current).toBe(false); expect(await countCalls()).toBe(calls); expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  } finally {await ownerApi.dispose(); await reviewerApi.dispose();}
});

test('actual source revocation clears an assigned reviewer page even when the visible-case list returns an empty 200', async ({page, context}) => {
  test.setTimeout(JOB_READY_TIMEOUT_MS + REVIEW_PROTOCOL_BUDGET_MS);
  const ownerApi = await newRequest.newContext(), reviewerApi = await newRequest.newContext();
  try {
    const owner = await login(ownerApi, 'UNIT-revoke-owner', ['analyst', 'reviewer']), reviewer = await login(reviewerApi, 'UNIT-revoked-reviewer', ['reviewer'], context); const fixed = await inputs(ownerApi, owner, 'UNIT 真实撤权案件'), seeded = await seedCase(ownerApi, owner, fixed); await share(ownerApi, owner, reviewer, fixed);
    const current: ReviewCase = await post(ownerApi, owner, '/reviews/cases', {run_id: seeded.run, candidate_batch_id: seeded.batch, reviewer_ids: [reviewer.user.subject_id], idempotency_key: crypto.randomUUID()}); await open(page, current.case_id); await condition(page, 0).locator('details').filter({hasText: '企业证明材料'}).first().locator('summary').click(); await expect(condition(page, 0).getByText(/UNIT_CERTIFICATE engineering proof/)).toBeVisible(); await condition(page, 0).getByRole('textbox', {name: '该条独立审核理由'}).fill('UNIT 需清除的私有理由');
    const response = await ownerApi.delete(`${origin}/api/company/objects/${fixed.materials[0].object_id}/grants/${reviewer.user.subject_id}`, {headers: headers(owner)}); expect(response.status(), await response.text()).toBe(200); const list = await reviewerApi.get(`${origin}/api/reviews/cases`); expect(list.status(), await list.text()).toBe(200); expect((await list.json()).items).toEqual([]); expect((await reviewerApi.get(`${origin}/api/reviews/cases/${current.case_id}`)).status()).toBe(404);
    await page.getByRole('button', {name: '刷新复核工作区'}).click(); await expect(page.getByRole('alert')).toBeVisible(); await expect(page.locator('.reviews-detail')).toHaveCount(0); await expect(page.getByRole('textbox', {name: '已有复核编号', exact: true})).toHaveValue(''); await expect(page.getByText(/UNIT_CERTIFICATE engineering proof/)).toHaveCount(0); await expect(page.getByText('UNIT 需清除的私有理由', {exact: true})).toHaveCount(0);
  } finally {await ownerApi.dispose(); await reviewerApi.dispose();}
});
