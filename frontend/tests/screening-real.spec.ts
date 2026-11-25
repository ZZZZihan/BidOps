import { expect, request as newRequest, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Real HTTP, SQLite, notice attachments, enterprise ACL and screening runtime.
// Only OIDC's external transport and model answers are deterministic UNIT fixtures.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
type Ref = {object_id: string; version_id: string; sha256: string};
type Identity = {csrf_token: string; user: {subject_id: string}};
let backend: ChildProcess | undefined;
let preview: ChildProcess | undefined;
let origin = '';
let scratch = '';
let logs = '';

async function freePort() {
  const server = createServer();
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done));
  const address = server.address();
  const port = address && typeof address === 'object' ? address.port : 0;
  await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done()));
  return port;
}
async function ready() {
  for (let i = 0; i < 150; i++) {
    try { if ((await fetch(`${origin}/api/health`)).ok) return; } catch { /* Startup polling. */ }
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error(`Local screening fixture failed to start: ${logs}`);
}
async function login(api: APIRequestContext, sub: string, context?: BrowserContext): Promise<Identity> {
  const start = await api.get(`${origin}/api/auth/login`, {maxRedirects: 0});
  expect(start.status()).toBe(302);
  const fields = new URL(start.headers().location).searchParams;
  const callback = await api.get(`${origin}/api/auth/callback`, {maxRedirects: 0, params: {
    state: fields.get('state')!, code: JSON.stringify({sub, roles: ['analyst'], nonce: fields.get('nonce'), challenge: fields.get('code_challenge')}),
  }});
  expect(callback.status()).toBe(303); expect(callback.headers().location).toBe('/');
  if (context) await context.addCookies((await api.storageState()).cookies);
  const me = await api.get(`${origin}/api/auth/me`);
  const identity = await me.json(); expect(identity.authenticated).toBe(true);
  return identity;
}
function headers(identity: Identity) { return {'Origin': origin, 'X-CSRF-Token': identity.csrf_token}; }
async function jsonPost(api: APIRequestContext, identity: Identity, path: string, data: unknown) {
  const response = await api.post(`${origin}/api${path}`, {headers: headers(identity), data});
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(true);
  return response.json();
}
async function inputs(api: APIRequestContext, identity: Identity, name: string) {
  const refs: Ref[] = [];
  const evidence: Record<string, unknown>[] = [];
  for (const [index, source] of ['UNIT_CERTIFICATE engineering proof', 'UNIT_NEGATIVE and UNIT_EXCEPTION engineering proof'].entries()) {
    const upload = await api.post(`${origin}/api/company/materials`, {
      headers: {...headers(identity), 'Content-Type': 'text/plain', 'X-Filename-Encoded': encodeURIComponent(`UNIT 私有证明 ${name}-${index}.txt`)},
      data: Buffer.from(source),
    });
    expect(upload.ok(), await upload.text()).toBe(true);
    const material = await upload.json(); refs.push({object_id: material.object_id, version_id: material.version_id, sha256: material.sha256});
    evidence.push(await jsonPost(api, identity, `/company/materials/${material.object_id}/versions/${material.version_id}/citations`, {start: 0, end: source.length}));
  }
  const payload = {display_name: name, provenance_kind: 'engineering_fixture', facts: [{fact_id: 'UNIT_FACT', kind: 'qualification', label: 'UNIT qualification engineering fixture', value: 'UNIT fixed value', status: 'document_supported', evidence}]};
  const profile: Ref = await jsonPost(api, identity, '/company/profiles', {payload});
  return {profile, materials: refs, evidence, payload};
}
async function share(api: APIRequestContext, owner: Identity, subject: Identity, objects: Ref[]) {
  for (const object of objects) await jsonPost(api, owner, `/company/objects/${object.object_id}/grants`, {subject_id: subject.user.subject_id, actions: ['read']});
}
async function open(page: Page, profile: Ref, name: string) {
  await page.goto(origin);
  await page.getByRole('button', {name: '候选与资格核对', exact: true}).click();
  await page.getByRole('button', {name: `选择画像 ${profile.object_id.slice(0, 12)}`}).click();
  await expect(page.getByRole('heading', {name, exact: true})).toBeVisible();
}
async function countCalls() { return JSON.parse(await readFile(join(scratch, 'calls.json'), 'utf8')).length as number; }

test.describe.configure({mode: 'serial'});
test.beforeAll(async () => {
  const backendPort = await freePort(), previewPort = await freePort();
  origin = `http://127.0.0.1:${previewPort}`;
  scratch = await mkdtemp(join(tmpdir(), 'bidops-screening-browser-'));
  backend = spawn(join(root, '.venv/bin/python'), ['tests/fixtures/screening_browser.py'], {
    cwd: root,
    env: {...process.env, PYTHONPATH: join(root, 'backend'), BIDOPS_SCREENING_BROWSER_DATA: scratch, BIDOPS_SCREENING_BROWSER_ORIGIN: origin, BIDOPS_SCREENING_BROWSER_PORT: String(backendPort)},
  });
  backend.stderr?.on('data', value => {logs += String(value);});
  preview = spawn('npm', ['run', 'preview', '--', '--port', String(previewPort), '--strictPort'], {
    cwd: join(root, 'frontend'), env: {...process.env, BIDOPS_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`},
  });
  preview.stderr?.on('data', value => {logs += String(value);});
  await ready();
});
test.afterAll(async () => {
  const processes = [preview, backend].filter((value): value is ChildProcess => !!value);
  for (const process of processes) process.kill('SIGTERM');
  await Promise.all(processes.map(process => new Promise<void>(done => {
    if (process.exitCode !== null) return done();
    process.once('exit', () => done());
    setTimeout(() => {process.kill('SIGKILL'); done();}, 3000).unref();
  })));
  if (scratch) await rm(scratch, {recursive: true, force: true});
});

test('real candidate-to-checklist keeps five states, both source versions and owner isolation; rereads make no model calls', async ({page, context, request}) => {
  const alice = await newRequest.newContext();
  try {
    const owner = await login(alice, 'screening-alice');
    const bob = await login(request, 'screening-bob', context);
    const fixed = await inputs(alice, owner, 'UNIT_REAL_PRIVATE_PROFILE');
    await share(alice, owner, bob, [fixed.profile, ...fixed.materials]);
    await open(page, fixed.profile, fixed.payload.display_name);
    await expect(page.getByText('外部模型调用已关闭。', {exact: false})).toBeVisible();
    await page.getByRole('textbox', {name: '项目关键词'}).fill('UNIT_');
    const candidateResponse = page.waitForResponse(response => response.url().endsWith('/api/screening/candidates') && response.request().method() === 'POST');
    await page.getByRole('button', {name: '生成固定候选批次'}).click();
    const batchResponse = await candidateResponse; expect(batchResponse.status()).toBe(201);
    const batch = await batchResponse.json();
    expect(batch.profile_revision).toEqual(fixed.profile); expect(batch.qualification_checked).toBe(false);
    expect(batch.counts).toEqual({total_units: 3, candidate: 1, excluded: 1, needs_verification: 1});
    expect(batch.items.some((value: {disposition: string}) => value.disposition === 'excluded')).toBe(true);
    await expect(page.locator('.screening-counts')).toContainText('全批 3 个采购单元');
    expect(await countCalls()).toBe(0);
    const candidate = page.locator('.screening-candidates article').filter({has: page.getByRole('heading', {name: 'UNIT_SCREENING_PROJECT', exact: true})});
    await candidate.getByRole('button', {name: '选用此项目核对'}).click();
    const checklistResponse = page.waitForResponse(response => response.url().endsWith('/api/screening/checklists') && response.request().method() === 'POST');
    await page.getByRole('button', {name: '生成逐条资格清单'}).click();
    const response = await checklistResponse;
    expect(response.status(), await response.text()).toBe(201);
    const saved = await response.json();
    expect(saved.state, JSON.stringify(saved)).toBe('completed');
    expect(saved.human_review_state).toBe('unreviewed'); expect(saved.inventory.conditions).toHaveLength(5);
    expect(saved.manifest.profile_revision).toEqual(fixed.profile);
    expect(saved.inventory.documents.some((document: {object_kind: string}) => document.object_kind === 'attachment')).toBe(true);
    expect(saved.manifest.material_revisions).toEqual(expect.arrayContaining(fixed.materials));
    const statuses = saved.groups.flatMap((group: {assessment: {assessments: {status: string}[]}}) => group.assessment.assessments.map(item => item.status));
    expect(statuses.sort()).toEqual(['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'].sort());
    await expect(page.locator('.screening-condition')).toHaveCount(5);
    for (const status of ['满足', '不满足', '信息不足', '证据冲突', '不适用']) await expect(page.locator('.screening-condition .screening-badge').filter({hasText: new RegExp(`^${status}$`)})).toBeVisible();
    const first = page.locator('.screening-condition').filter({has: page.getByRole('heading', {name: 'UNIT_A 要求证明 A。', exact: true})});
    await first.getByText(/^公告引用/).click(); await first.getByText(/^企业引用/).click();
    await expect(first.locator('blockquote').filter({hasText: 'UNIT_A 要求证明 A。'})).toBeVisible();
    await expect(first.locator('blockquote').filter({hasText: 'UNIT_CERTIFICATE engineering proof'})).toBeVisible();
    await expect(first).toContainText(fixed.materials[0].version_id);
    await expect(first).toContainText(fixed.materials[0].sha256);
    const exception = page.locator('.screening-condition').filter({has: page.getByRole('heading', {name: 'UNIT_E 条款：要求 E，已列明豁免 UNIT_EXCEPTION。', exact: true})});
    const exceptionCitation = saved.inventory.conditions.find((condition: {original_text: string}) => condition.original_text.startsWith('UNIT_E')).notice_evidence.find((evidence: {quote: string}) => evidence.quote.startsWith('UNIT_E'));
    await exception.getByText(`公告引用 · 附件 · ${exceptionCitation.evidence_id}`, {exact: true}).click();
    await expect(exception.locator('blockquote').filter({hasText: 'UNIT_E 条款：要求 E，已列明豁免 UNIT_EXCEPTION。'})).toBeVisible();
    await expect(page.getByRole('button', {name: /批准|资格通过/})).toHaveCount(0);
    const calls = await countCalls(); expect(calls).toBe(8); expect(saved.provenance.model_calls).toBe(calls);
    const repeated = await request.get(`${origin}/api/screening/runs/${saved.run_id}`);
    expect(repeated.status()).toBe(200); expect((await repeated.json()).result_revision).toBe(saved.result_revision);
    await page.getByRole('button', {name: '读取已保存核对', exact: true}).click();
    await expect(page.locator('.screening-result')).toContainText(saved.result_revision);
    await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
    await expect(page.getByRole('status')).toHaveCount(0); expect(await countCalls()).toBe(calls);
    expect((await alice.get(`${origin}/api/screening/runs/${saved.run_id}`)).status()).toBe(404);
    expect((await alice.get(`${origin}/api/screening/candidates/${batch.batch_id}`)).status()).toBe(404);

    // A newer authorized profile marks the saved run stale without replacing its inputs.
    const update = await alice.put(`${origin}/api/company/profiles/${fixed.profile.object_id}`, {headers: headers(owner), data: {payload: {...fixed.payload, display_name: 'UNIT_NEW_PROFILE_HEAD'}, expected_version: fixed.profile.version_id}});
    expect(update.ok(), await update.text()).toBe(true);
    await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
    await expect(page.locator('.screening-result')).toContainText('企业画像已有新版本');
    await expect(page.locator('.screening-provenance')).toContainText(fixed.profile.version_id);
    await expect(page.locator('.screening-provenance')).toContainText(saved.provenance.prompt_version);
    expect(await countCalls()).toBe(calls);

    const revoke = await alice.delete(`${origin}/api/company/objects/${fixed.materials[0].object_id}/grants/${bob.user.subject_id}`, {headers: headers(owner)});
    expect(revoke.ok(), await revoke.text()).toBe(true);
    const remaining = await (await request.get(`${origin}/api/company/materials`)).json();
    expect(remaining.items.some((item: Ref) => item.object_id === fixed.materials[0].object_id)).toBe(false);
    expect((await request.get(`${origin}/api/company/materials/${fixed.materials[0].object_id}?version=${fixed.materials[0].version_id}`)).status()).toBe(404);
    expect((await request.get(`${origin}/api/screening/runs/${saved.run_id}`)).status()).toBe(404);
    await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
    await expect(page.getByRole('alert')).toContainText('404');
    await expect(page.locator('.screening-result')).toHaveCount(0);
    await expect(page.locator('.screening-batch')).toHaveCount(0);
    await expect(page.locator('.screening-pinned')).toHaveCount(0);
    await expect(page.getByText('UNIT_CERTIFICATE engineering proof', {exact: true})).toHaveCount(0);
    expect(await countCalls()).toBe(calls);
  } finally { await alice.dispose(); }
});

test('real selected-material revocation clears metadata despite a successful list, before creating a run', async ({page, context, request}) => {
  const alice = await newRequest.newContext();
  try {
    const owner = await login(alice, 'screening-selected-owner');
    const bob = await login(request, 'screening-selected-reader', context);
    const fixed = await inputs(alice, owner, 'UNIT_SELECTED_PRIVATE_PROFILE');
    await share(alice, owner, bob, [fixed.profile, ...fixed.materials]);
    await open(page, fixed.profile, fixed.payload.display_name);
    await page.getByRole('button', {name: '选择额外材料', exact: true}).click();
    await page.getByRole('checkbox', {name: /UNIT 私有证明 UNIT_SELECTED_PRIVATE_PROFILE-0/}).check();
    const before = await countCalls();
    const revoke = await alice.delete(`${origin}/api/company/objects/${fixed.materials[0].object_id}/grants/${bob.user.subject_id}`, {headers: headers(owner)});
    expect(revoke.ok()).toBe(true);
    expect((await request.get(`${origin}/api/company/materials`)).status()).toBe(200);
    await page.getByRole('button', {name: '刷新权限与运行结果'}).click();
    await expect(page.getByRole('alert')).toContainText('404');
    await expect(page.locator('.screening-pinned')).toHaveCount(0);
    await expect(page.getByRole('checkbox', {name: /UNIT 私有证明/})).toHaveCount(0);
    await expect(page.getByRole('textbox', {name: '项目编号', exact: true})).toHaveValue('');
    expect(await countCalls()).toBe(before);
  } finally { await alice.dispose(); }
});

test('real provider failure preserves every extracted condition as incomplete and exposes its error code', async ({page, context, request}) => {
  const identity = await login(request, 'screening-failure-owner', context);
  const fixed = await inputs(request, identity, 'UNIT_FORCE_FAILURE');
  const seed = JSON.parse(await readFile(join(scratch, 'seed.json'), 'utf8'));
  await open(page, fixed.profile, fixed.payload.display_name);
  await page.getByRole('textbox', {name: '项目编号', exact: true}).fill(seed.project_id);
  const pending = page.waitForResponse(response => response.url().endsWith('/api/screening/checklists') && response.request().method() === 'POST');
  await page.getByRole('button', {name: '生成逐条资格清单'}).click();
  const response = await pending; expect(response.status(), await response.text()).toBe(201);
  const saved = await response.json();
  expect(saved.state).toBe('failed'); expect(saved.error_code).toBeTruthy();
  expect(saved.inventory.conditions).toHaveLength(5); expect(saved.human_review_state).toBe('unreviewed');
  expect(saved.groups.flatMap((group: {assessment: {assessments: {status: string; reason: string}[]}}) => group.assessment.assessments).every((item: {status: string; reason: string}) => item.status === 'information_insufficient' && item.reason === 'assessment_not_completed')).toBe(true);
  await expect(page.locator('.screening-result').getByRole('alert')).toContainText(saved.error_code);
  await expect(page.locator('.screening-condition')).toHaveCount(5);
  await expect(page.locator('.screening-condition .screening-badge').filter({hasText: /^信息不足$/})).toHaveCount(5);
  const before = await countCalls();
  await page.getByRole('button', {name: '读取已保存核对', exact: true}).click();
  await expect(page.locator('.screening-result')).toContainText(saved.result_revision);
  expect(await countCalls()).toBe(before);
});
