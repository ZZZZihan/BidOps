import { expect, request as newRequest, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Real HTTP, DBOS, SQLite, current enterprise ACL and Pydantic AI. Only the
// external OIDC transport and authored UNIT model answers are offline fixtures.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
type Ref = {object_id: string; version_id: string; sha256: string};
type Identity = {csrf_token: string; user: {subject_id: string}};
type SavedJob = {run_id: string; state_version: number; status: string; result_id: string; result_revision: string; manifest: {profile_revision: Ref; material_revisions: Ref[]}; usage: {model_calls: number; unknown_calls: number; estimated_cost_cny: string; reserved_cost_cny: string; offline: boolean}; result: {human_review_state: string; inventory: {conditions: {original_text: string; notice_evidence: {evidence_id: string}[]}[]}; groups: {assessment: {assessments: {status: string; notice_evidence: {evidence_id: string}[]; company_evidence: {evidence_id: string}[]}[]}}[]}};
let backend: ChildProcess | undefined;
let preview: ChildProcess | undefined;
let origin = '';
let scratch = '';
let logs = '';

async function freePort() {
  const server = createServer();
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done));
  const address = server.address(), port = address && typeof address === 'object' ? address.port : 0;
  await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done()));
  return port;
}
async function ready() {
  for (let i = 0; i < 200; i++) {
    if (backend?.exitCode !== null && backend?.exitCode !== undefined) throw new Error(`Jobs fixture exited: ${logs}`);
    try {if ((await fetch(`${origin}/api/health`)).ok) return;} catch { /* Local startup polling. */ }
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error(`Local Jobs fixture did not start: ${logs}`);
}
async function login(api: APIRequestContext, subject: string, context?: BrowserContext): Promise<Identity> {
  const start = await api.get(`${origin}/api/auth/login`, {maxRedirects: 0}); expect(start.status()).toBe(302);
  const fields = new URL(start.headers().location).searchParams;
  const callback = await api.get(`${origin}/api/auth/callback`, {maxRedirects: 0, params: {
    state: fields.get('state')!, code: JSON.stringify({sub: subject, roles: ['analyst'], nonce: fields.get('nonce'), challenge: fields.get('code_challenge')}),
  }});
  expect(callback.status(), await callback.text()).toBe(303); expect(callback.headers().location).toBe('/');
  if (context) await context.addCookies((await api.storageState()).cookies);
  const me = await api.get(`${origin}/api/auth/me`), identity = await me.json(); expect(identity.authenticated).toBe(true);
  return identity;
}
function headers(identity: Identity) {return {'Origin': origin, 'X-CSRF-Token': identity.csrf_token};}
async function post(api: APIRequestContext, identity: Identity, path: string, data: unknown) {
  const response = await api.post(`${origin}/api${path}`, {headers: headers(identity), data});
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(true); return response.json();
}
async function inputs(api: APIRequestContext, identity: Identity, name: string) {
  const materials: Ref[] = [], evidence: Record<string, unknown>[] = [];
  for (const [index, source] of ['UNIT_CERTIFICATE engineering proof', 'UNIT_NEGATIVE and UNIT_EXCEPTION engineering proof'].entries()) {
    const upload = await api.post(`${origin}/api/company/materials`, {headers: {...headers(identity), 'Content-Type': 'text/plain', 'X-Filename-Encoded': encodeURIComponent(`UNIT 持久私有证明 ${name}-${index}.txt`)}, data: Buffer.from(source)});
    expect(upload.status(), await upload.text()).toBe(201);
    const value = await upload.json(), ref = {object_id: value.object_id, version_id: value.version_id, sha256: value.sha256}; materials.push(ref);
    evidence.push(await post(api, identity, `/company/materials/${ref.object_id}/versions/${ref.version_id}/citations`, {start: 0, end: source.length}));
  }
  const profile: Ref = await post(api, identity, '/company/profiles', {payload: {display_name: name, provenance_kind: 'engineering_fixture', facts: [{fact_id: 'UNIT_DURABLE_FACT', kind: 'qualification', label: 'UNIT durable engineering fixture', value: 'UNIT fixed value', status: 'document_supported', evidence}]}});
  return {profile, materials, name};
}
async function share(api: APIRequestContext, owner: Identity, subject: Identity, refs: Ref[]) {
  for (const ref of refs) await post(api, owner, `/company/objects/${ref.object_id}/grants`, {subject_id: subject.user.subject_id, actions: ['read']});
}
async function open(page: Page, fixed?: {profile: Ref; name: string}) {
  await page.goto(origin); await page.getByRole('button', {name: '持久核对任务', exact: true}).click();
  await expect(page.getByRole('heading', {name: '创建固定输入任务'})).toBeVisible();
  if (fixed) {await page.getByRole('button', {name: `选择画像 ${fixed.profile.object_id.slice(0, 12)}`}).click(); await expect(page.getByRole('heading', {name: fixed.name, exact: true})).toBeVisible();}
}
async function countCalls() {return JSON.parse(await readFile(join(scratch, 'calls.json'), 'utf8')).length as number;}
async function waitForReview(api: APIRequestContext, id: string): Promise<SavedJob> {
  let last: SavedJob | undefined;
  await expect.poll(async () => {
    const response = await api.get(`${origin}/api/jobs/runs/${id}`); expect(response.status(), await response.text()).toBe(200);
    last = await response.json(); return last?.status;
  }, {timeout: 20000, intervals: [100, 200, 500], message: 'Actual DBOS workflow should reach its native review wait'}).toBe('waiting_review');
  return last!;
}
async function create(page: Page, project: string, material?: Ref) {
  await page.getByRole('textbox', {name: '项目编号', exact: true}).fill(project);
  if (material) {
    await page.getByRole('button', {name: '选择额外证明材料', exact: true}).click();
    const choice = page.locator('.jobs-choice').filter({hasText: material.version_id}).getByRole('checkbox'); await choice.click(); await expect(choice).toBeChecked();
  }
  const pending = page.waitForResponse(response => response.url() === `${origin}/api/jobs/runs` && response.request().method() === 'POST');
  await page.getByRole('button', {name: '提交持久核对任务', exact: true}).click(); const response = await pending;
  expect(response.status(), await response.text()).toBe(202);
  const saved = await response.json(); expect(response.request().headers()['idempotency-key']).toMatch(/^[a-f0-9-]{36}$/);
  return {accepted: saved as SavedJob, submitted: response.request().postDataJSON()};
}

test.describe.configure({mode: 'serial'});
test.beforeAll(async () => {
  const backendPort = await freePort(), previewPort = await freePort();
  origin = `http://127.0.0.1:${previewPort}`; scratch = await realpath(await mkdtemp(join(tmpdir(), 'bidops-jobs-browser-')));
  backend = spawn('uv', ['run', '--frozen', '--no-sync', 'python', 'tests/fixtures/jobs_browser.py'], {
    cwd: root, detached: true,
    env: {...process.env, PYTHONPATH: join(root, 'backend'), UV_OFFLINE: '1', BIDOPS_NETWORK_ENABLED: 'false', BIDOPS_JOBS_BROWSER_DATA: scratch, BIDOPS_JOBS_BROWSER_ORIGIN: origin, BIDOPS_JOBS_BROWSER_PORT: String(backendPort)},
  });
  backend.stdout?.on('data', value => {logs += String(value);}); backend.stderr?.on('data', value => {logs += String(value);});
  preview = spawn('npm', ['run', 'preview', '--', '--host', '127.0.0.1', '--port', String(previewPort), '--strictPort'], {
    cwd: join(root, 'frontend'), detached: true, env: {...process.env, BIDOPS_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`},
  });
  preview.stderr?.on('data', value => {logs += String(value);}); await ready();
});
test.afterAll(async () => {
  const children = [preview, backend].filter((child): child is ChildProcess => !!child);
  for (const child of children) if (child.pid && child.exitCode === null) process.kill(-child.pid, 'SIGTERM');
  await Promise.all(children.map(child => new Promise<void>(done => {
    if (child.exitCode !== null) return done();
    child.once('exit', () => done());
    setTimeout(() => {if (child.pid && child.exitCode === null) {try {process.kill(-child.pid, 'SIGKILL');} catch {/* Already exited. */}} done();}, 5000).unref();
  })));
  if (scratch) await rm(scratch, {recursive: true, force: true});
});

test('real Jobs 202 reaches native review wait; reload and GET retain ID, result and zero fees, then cancellation is terminal', async ({page, context, request}) => {
  const alice = await newRequest.newContext();
  try {
    const owner = await login(alice, 'jobs-owner-main'), bob = await login(request, 'jobs-runner-main', context);
    const fixed = await inputs(alice, owner, 'UNIT_REAL_DURABLE_PROFILE'); await share(alice, owner, bob, [fixed.profile, ...fixed.materials]);
    const seed = JSON.parse(await readFile(join(scratch, 'seed.json'), 'utf8')), baseline = await countCalls();
    await open(page, fixed); await expect(page.getByText('外部模型调用关闭', {exact: false})).toBeVisible();
    const creation = await create(page, seed.project_id, fixed.materials[0]);
    expect(creation.submitted).toMatchObject({profile_revision: fixed.profile, project_id: seed.project_id, additional_material_revisions: [fixed.materials[0]]});
    const saved = await waitForReview(request, creation.accepted.run_id);
    await expect(page.locator('.jobs-run-id')).toHaveText(saved.run_id); await expect(page.locator('.jobs-task-state')).toHaveText('等待人工审核');
    expect(saved.result.human_review_state).toBe('unreviewed'); expect(saved.result.inventory.conditions).toHaveLength(5);
    expect(saved.manifest.profile_revision).toEqual(fixed.profile); expect(saved.manifest.material_revisions).toEqual(expect.arrayContaining(fixed.materials));
    const statuses = saved.result.groups.flatMap(group => group.assessment.assessments.map(item => item.status));
    expect(statuses.sort()).toEqual(['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'].sort());
    await expect(page.locator('.jobs-condition')).toHaveCount(5); for (const name of ['满足', '不满足', '信息不足', '证据冲突', '不适用']) await expect(page.locator('.jobs-condition .jobs-badge').filter({hasText: new RegExp(`^${name}$`)})).toBeVisible();
    const first = page.locator('.jobs-condition').filter({has: page.getByRole('heading', {name: 'UNIT_A 要求证明 A。', exact: true})});
    const judgment = saved.result.groups.flatMap(group => group.assessment.assessments).find(item => item.status === 'satisfied')!;
    await first.getByText(judgment.notice_evidence[0].evidence_id, {exact: true}).click(); await first.getByText(judgment.company_evidence[0].evidence_id, {exact: true}).click();
    await expect(first.locator('blockquote').filter({hasText: 'UNIT_A 要求证明 A。'})).toBeVisible(); await expect(first.locator('blockquote').filter({hasText: 'UNIT_CERTIFICATE engineering proof'})).toBeVisible(); await expect(first).toContainText(fixed.materials[0].version_id); await expect(first).toContainText(fixed.materials[0].sha256);
    const calls = await countCalls(); expect(calls - baseline).toBe(8); expect(saved.usage).toMatchObject({model_calls: 8, unknown_calls: 0, offline: true, estimated_cost_cny: '0', reserved_cost_cny: '0'});
    expect((await alice.get(`${origin}/api/jobs/runs/${saved.run_id}`)).status()).toBe(404);
    expect((await alice.get(`${origin}/api/jobs/runs`)).ok()).toBe(true);
    await page.reload(); await page.getByRole('button', {name: '持久核对任务', exact: true}).click(); await page.getByRole('button', {name: `打开任务 ${saved.run_id}`, exact: true}).click();
    await expect(page.locator('.jobs-run-id')).toHaveText(saved.run_id); await expect(page.locator('.jobs-result-id')).toHaveText(saved.result_id); await expect(page.locator('.jobs-detail')).toContainText(saved.result_revision);
    await page.getByRole('button', {name: '刷新当前任务', exact: true}).click(); expect(await countCalls()).toBe(calls);
    await expect(page.getByRole('button', {name: /批准|跳过审批|确认通过/})).toHaveCount(0);
    const cancellation = page.waitForResponse(response => response.url().endsWith(`/api/jobs/runs/${saved.run_id}/cancel`) && response.request().method() === 'POST'); await page.getByRole('button', {name: '请求取消任务', exact: true}).click(); const cancelledResponse = await cancellation;
    expect(cancelledResponse.status(), await cancelledResponse.text()).toBe(200); const cancelled = await cancelledResponse.json(); expect(cancelled.status).toBe('cancelled');
    await expect(page.locator('.jobs-task-state')).toHaveText('已取消'); await expect(page.getByRole('button', {name: '恢复中断任务'})).toHaveCount(0);
    const resume = await request.post(`${origin}/api/jobs/runs/${saved.run_id}/resume`, {headers: headers(bob), data: {expected_state_version: cancelled.state_version}}); expect(resume.status()).toBe(409); expect((await resume.json()).detail).toBe('job_resume_forbidden');
    const read = await request.get(`${origin}/api/jobs/runs/${saved.run_id}`), terminal = await read.json(); expect(terminal.run_id).toBe(saved.run_id); expect(terminal.status).toBe('cancelled'); expect(terminal.usage.model_calls).toBe(8); expect(await countCalls()).toBe(calls);
  } finally {await alice.dispose();}
});

test('real source revocation produces a 200 empty history and 404 fixed job, clearing displayed private body and input', async ({page, context, request}) => {
  const alice = await newRequest.newContext();
  try {
    const owner = await login(alice, 'jobs-owner-revoked'), bob = await login(request, 'jobs-runner-revoked', context);
    const fixed = await inputs(alice, owner, 'UNIT_PRIVATE_REVOKED_DURABLE_PROFILE'); await share(alice, owner, bob, [fixed.profile, ...fixed.materials]);
    const seed = JSON.parse(await readFile(join(scratch, 'seed.json'), 'utf8')); await open(page, fixed); const creation = await create(page, seed.project_id, fixed.materials[1]);
    const saved = await waitForReview(request, creation.accepted.run_id); await expect(page.locator('.jobs-condition')).toHaveCount(5); await page.getByRole('checkbox', {name: '自动刷新当前任务（每 2 秒）'}).uncheck();
    const first = page.locator('.jobs-condition').filter({has: page.getByRole('heading', {name: 'UNIT_A 要求证明 A。', exact: true})});
    const judgment = saved.result.groups.flatMap(group => group.assessment.assessments).find(item => item.status === 'satisfied')!; await first.getByText(judgment.company_evidence[0].evidence_id, {exact: true}).click(); await expect(first.locator('blockquote').filter({hasText: 'UNIT_CERTIFICATE engineering proof'})).toBeVisible();
    const calls = await countCalls();
    const revoked = await alice.delete(`${origin}/api/company/objects/${fixed.materials[0].object_id}/grants/${bob.user.subject_id}`, {headers: headers(owner)}); expect(revoked.status(), await revoked.text()).toBe(200);
    const list = await request.get(`${origin}/api/jobs/runs`); expect(list.status()).toBe(200); expect((await list.json()).items).toEqual([]);
    const denied = await request.get(`${origin}/api/jobs/runs/${saved.run_id}`); expect(denied.status()).toBe(404); expect(await denied.text()).not.toContain('UNIT_CERTIFICATE');
    await page.getByRole('button', {name: '刷新任务与权限', exact: true}).click(); await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.locator('.jobs-detail')).toHaveCount(0); await expect(page.locator('.jobs-pinned')).toHaveCount(0); await expect(page.locator('.jobs-history li')).toHaveCount(0); await expect(page.locator('blockquote')).toHaveCount(0); await expect(page.getByText(fixed.name, {exact: true})).toHaveCount(0); await expect(page.getByRole('textbox', {name: '项目编号', exact: true})).toHaveValue(''); await expect(page.getByText('UNIT 持久私有证明', {exact: false})).toHaveCount(0);
    expect(await countCalls()).toBe(calls);
  } finally {await alice.dispose();}
});
