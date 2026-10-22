import { expect, test, type APIRequestContext, type BrowserContext } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Real FastAPI/SQLite/ACL + Vite proxy. Only the external OIDC protocol is simulated,
// with real RSA signatures, PKCE checks and Authlib callback verification.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const backendSource = String.raw`
import base64, hashlib, json, os, time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs
import httpx2, uvicorn
from joserfc import jwt
from joserfc.jwk import RSAKey
from bidops.api import create_app
from bidops.config import Settings
from bidops.agent_demo.runtime import DemoSettings
from bidops.enterprise.config import EnterpriseSettings
from pydantic_ai import models
models.ALLOW_MODEL_REQUESTS=False
issuer='https://identity.test/realms/browser-fixture'
key=RSAKey.generate_key(2048,parameters={'kid':'browser-fixture'})
def provider(request):
    if request.url.path.endswith('openid-configuration'):
        return httpx2.Response(200,json={'issuer':issuer,'authorization_endpoint':issuer+'/auth','token_endpoint':issuer+'/token','jwks_uri':issuer+'/keys','id_token_signing_alg_values_supported':['RS256']})
    if request.url.path.endswith('/keys'):
        return httpx2.Response(200,json={'keys':[key.as_dict(private=False)]})
    fields=parse_qs(request.content.decode())
    code=json.loads(fields['code'][0])
    challenge=base64.urlsafe_b64encode(hashlib.sha256(fields['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
    assert challenge==code['challenge']
    claims={'iss':issuer,'sub':code['sub'],'aud':'browser-client','iat':int(time.time()),'exp':int(time.time())+300,'nonce':code['nonce'],'name':code['sub'],'bidops_roles':code['roles'],'sid':code['sub']+'-browser-session'}
    token=jwt.encode({'alg':'RS256','kid':'browser-fixture'},claims,key)
    return httpx2.Response(200,json={'access_token':'ONLY_LOCAL_PROTOCOL_FIXTURE','token_type':'Bearer','id_token':token})
data=Path(os.environ['BIDOPS_BROWSER_DATA'])
settings=Settings(data_dir=data,database_url='sqlite:///'+str(data/'browser.sqlite3'),source_registry=Path('config/sources.json'),network_enabled=False)
enterprise=EnterpriseSettings(enabled=True,public_url=os.environ['BIDOPS_BROWSER_ORIGIN'],oidc_issuer=issuer,oidc_client_id='browser-client',oidc_client_secret='ONLY_LOCAL_PROTOCOL_FIXTURE',cookie_secret='browser-fixture-secret-'*3,allow_insecure_loopback=True)
app=create_app(settings,DemoSettings(enabled=False,_env_file=None),enterprise)
original_lifespan=app.router.lifespan_context
@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        app.state.identity.oauth.enterprise.client_kwargs['transport']=httpx2.MockTransport(provider)
        yield
app.router.lifespan_context=lifespan
uvicorn.run(app,host='127.0.0.1',port=int(os.environ['BIDOPS_BROWSER_PORT']),log_level='warning')
`;
let backend: ChildProcess;
let preview: ChildProcess;
let origin: string;
let scratch: string;
let logs = '';
async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const port = address && typeof address === 'object' ? address.port : 0;
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}
async function ready(url: string) {
  for (let i = 0; i < 100; i++) {
    try { const response = await fetch(url); if (response.ok) return; } catch { /* Startup poll. */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Local browser fixture failed to start: ${logs}`);
}
async function login(api: APIRequestContext, context: BrowserContext, sub: string, roles = ['analyst']) {
  const start = await api.get(`${origin}/api/auth/login`, {maxRedirects: 0});
  expect(start.status()).toBe(302);
  const fields = new URL(start.headers().location).searchParams;
  const callback = await api.get(`${origin}/api/auth/callback`, {maxRedirects: 0, params: {
    state: fields.get('state')!, code: JSON.stringify({sub, roles, nonce: fields.get('nonce'), challenge: fields.get('code_challenge')}),
  }});
  expect(callback.headers().location).toBe('/');
  await context.addCookies((await api.storageState()).cookies);
  const me = await api.get(`${origin}/api/auth/me`);
  const identity = await me.json();
  expect(identity.authenticated).toBe(true);
  return identity;
}

test.describe.configure({mode: 'serial'});
test.beforeAll(async () => {
  const backendPort = await freePort(); const previewPort = await freePort();
  origin = `http://127.0.0.1:${previewPort}`;
  scratch = await mkdtemp(join(tmpdir(), 'bidops-company-browser-'));
  backend = spawn(join(root, '.venv/bin/python'), ['-c', backendSource], {cwd: root, env: {...process.env, BIDOPS_BROWSER_DATA: scratch, BIDOPS_BROWSER_ORIGIN: origin, BIDOPS_BROWSER_PORT: String(backendPort)}});
  backend.stderr?.on('data', chunk => {logs += String(chunk);});
  preview = spawn('npm', ['run', 'preview', '--', '--port', String(previewPort), '--strictPort'], {cwd: join(root, 'frontend'), env: {...process.env, BIDOPS_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`}});
  preview.stderr?.on('data', chunk => {logs += String(chunk);});
  await ready(`${origin}/api/health`);
});
test.afterAll(async () => {
  preview?.kill('SIGTERM'); backend?.kill('SIGTERM');
  await Promise.all([preview, backend].filter(Boolean).map(process => new Promise<void>(resolve => { if (process.exitCode !== null) resolve(); else {process.once('exit', () => resolve()); setTimeout(() => {process.kill('SIGKILL'); resolve();}, 3000).unref();} })));
  if (scratch) await rm(scratch, {recursive: true, force: true});
});

test('anonymous enterprise session does not expose private company controls', async ({page}) => {
  await page.goto(origin);
  await expect(page.getByRole('link', {name: '企业统一登录'})).toBeVisible();
  await expect(page.getByRole('button', {name: '企业画像与材料'})).toHaveCount(0);
  expect((await page.request.get(`${origin}/api/company/profiles`)).status()).toBe(401);
});

test('real backend saves profile versions, UTF8 materials, sources and bounded qualification results', async ({page, context, request}) => {
  await login(request, context, 'ui-alice');
  await page.goto(origin);
  await page.getByRole('button', {name: '企业画像与材料'}).click();
  await expect(page.getByRole('heading', {name: '企业画像与材料'})).toBeVisible();
  await page.getByLabel('企业名称', {exact: true}).fill('UNIT_BROWSER_PROFILE');
  await page.getByRole('button', {name: '添加事实', exact: true}).click();
  await page.getByLabel('事实名称', {exact: true}).fill('UNIT_QUALIFICATION');
  await page.getByRole('button', {name: '创建企业画像'}).click();
  await expect(page.getByRole('status').filter({hasText: '企业画像已保存新版本'})).toContainText('企业画像已保存新版本');
  const first = await (await request.get(`${origin}/api/company/profiles`)).json();
  const profileId = first.items[0].object_id;
  const firstVersion = first.items[0].head_version;
  await page.getByRole('button', {name: '核对已保存版本'}).click();
  await expect(page.locator('.company-check')).toContainText('信息不足');
  await expect(page.locator('.company-check')).toContainText('缺少证明材料');

  await page.getByLabel('选择材料文件').setInputFiles({name: '资质证明.txt', mimeType: 'text/plain', buffer: Buffer.from('UNIT_CERTIFICATE\n仅用于协议与页面工程测试。', 'utf8')});
  await page.getByRole('button', {name: '上传材料', exact: true}).click();
  await expect(page.getByRole('status').filter({hasText: '材料已保存新版本'})).toContainText('材料已保存新版本');
  await expect(page.getByRole('button', {name: '资质证明.txt', exact: true})).toBeVisible();
  await page.getByLabel('引用起始位置').fill('0');
  await page.getByLabel('引用结束位置').fill('16');
  await page.getByRole('button', {name: '生成材料引用'}).click();
  await expect(page.getByRole('status').filter({hasText: '材料引用已加入'})).toContainText('材料引用已加入');
  await page.getByLabel('证据状态').selectOption('document_supported');
  await page.getByLabel('事实内容').fill('UNIT_CERTIFICATE');
  await page.locator('.company-sources').getByRole('checkbox').check();
  // A committed write must be acknowledged independently of a slow list read.
  // Gate the real post-save refresh rather than increasing the assertion timeout.
  let releaseRefresh!: () => void;
  let sawRefresh!: () => void;
  const refreshGate = new Promise<void>(resolve => {releaseRefresh = resolve;});
  const refreshStarted = new Promise<void>(resolve => {sawRefresh = resolve;});
  const listUrl = '**/api/company/profiles?limit=20&offset=0';
  await page.route(listUrl, async route => {
    sawRefresh(); await refreshGate; await route.continue();
  });
  const saveResponse = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith(`/api/company/profiles/${profileId}`));
  let savedVersion = '';
  try {
    await page.getByRole('button', {name: '保存画像新版本'}).click();
    const response = await saveResponse;
    expect(response.status(), await response.text()).toBe(200);
    savedVersion = (await response.json()).version_id;
    expect(savedVersion).not.toBe(firstVersion);
    await refreshStarted;
    await expect(page.getByRole('status').filter({hasText: '企业画像已保存新版本'})).toContainText(savedVersion.slice(0, 12));
    await expect(page.getByRole('button', {name: '保存画像新版本'})).toBeDisabled();
  } finally {
    releaseRefresh();
  }
  await expect(page.getByRole('button', {name: '保存画像新版本'})).toBeEnabled();
  await page.unroute(listUrl);
  await expect(page.locator('.company-version').first()).toContainText(savedVersion);
  await page.getByRole('button', {name: '核对已保存版本'}).click();
  await expect(page.locator('.company-check')).toContainText('信息不足');
  await expect(page.locator('.company-check')).toContainText('材料有效期待核实');
  const current = await (await request.get(`${origin}/api/company/profiles/${profileId}`)).json();
  expect(current.version_id).not.toBe(firstVersion);
  expect(current.payload.facts[0].evidence[0].quote).toBe('UNIT_CERTIFICATE');
  await page.getByLabel('历史版本编号').fill(firstVersion);
  await page.getByRole('button', {name: '读取指定版本'}).click();
  await expect(page.getByLabel('证据状态')).toHaveValue('unknown');
  await page.getByRole('button', {name: '保存画像新版本'}).click();
  await expect(page.getByRole('alert')).toContainText('版本已被更新');
  const download = page.waitForEvent('download');
  await page.getByRole('button', {name: '下载当前材料版本'}).click();
  expect((await download).suggestedFilename()).toBe('资质证明.txt');
  await page.getByLabel('检索可访问材料').fill('UNIT_CERTIFICATE');
  await page.getByRole('button', {name: '检索材料', exact: true}).click();
  await expect(page.locator('.company-search-results')).toContainText('UNIT_CERTIFICATE');
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: test.info().outputPath('company-mobile-real-api.png'), fullPage: true});
});

test('real object grant and revoke cannot be bypassed by another account or an admin', async ({browser, request}) => {
  const alice = await browser.newContext(); const bob = await browser.newContext(); const admin = await browser.newContext();
  try {
    const aliceMe = await login(alice.request, alice, 'ui-owner');
    const bobMe = await login(bob.request, bob, 'ui-reader', ['viewer']);
    await login(admin.request, admin, 'ui-admin', ['admin']);
    const upload = await alice.request.post(`${origin}/api/company/materials`, {headers: {'Origin': origin, 'X-CSRF-Token': aliceMe.csrf_token, 'X-Filename': 'private.txt', 'Content-Type': 'text/plain'}, data: 'UNIT_PRIVATE_NEVER_ADMIN'});
    expect(upload.status()).toBe(201); const material = await upload.json();
    expect((await bob.request.get(`${origin}/api/company/materials/${material.object_id}/export`)).status()).toBe(404);
    expect((await admin.request.get(`${origin}/api/company/materials/${material.object_id}/export`)).status()).toBe(404);
    const page = await alice.newPage(); await page.goto(origin);
    await page.getByRole('button', {name: '企业画像与材料'}).click();
    await page.getByLabel('对象编号', {exact: true}).fill(material.object_id);
    await page.getByLabel('接收人工作台标识').fill(bobMe.user.subject_id);
    await page.getByRole('button', {name: '添加对象授权'}).click();
    await expect(page.getByRole('status').filter({hasText: '对象授权已由服务端更新'})).toContainText('对象授权已由服务端更新');
    expect((await bob.request.get(`${origin}/api/company/materials/${material.object_id}/export`)).status()).toBe(200);
    const bobPage = await bob.newPage(); await bobPage.goto(origin);
    await bobPage.getByRole('button', {name: '企业画像与材料'}).click();
    await bobPage.getByRole('button', {name: 'private.txt', exact: true}).click();
    await expect(bobPage.getByRole('button', {name: '下载当前材料版本'})).toBeVisible();
    await page.getByRole('button', {name: '撤销该人全部授权'}).click();
    await expect(page.getByRole('status').filter({hasText: '对象授权已撤销'})).toContainText('对象授权已撤销');
    await bobPage.getByRole('button', {name: '下载当前材料版本'}).click();
    await expect(bobPage.getByRole('alert')).toContainText('不可访问');
    await expect(bobPage.locator('.company-material-text')).toHaveCount(0);
    expect((await bob.request.get(`${origin}/api/company/materials/${material.object_id}/download`)).status()).toBe(404);
    expect((await request.get(`${origin}/api/company/materials/${material.object_id}/download`)).status()).toBe(401);
  } finally { await alice.close(); await bob.close(); await admin.close(); }
});

test('real 403 and refresh after revocation clear selected bodies, profile drafts, quotes and authorization metadata', async ({browser}) => {
  const owner = await browser.newContext(); const reader = await browser.newContext();
  try {
    const ownerMe = await login(owner.request, owner, 'ui-cache-owner');
    const readerMe = await login(reader.request, reader, 'ui-cache-reader');
    const writeHeaders = {'Origin': origin, 'X-CSRF-Token': ownerMe.csrf_token, 'Content-Type': 'application/json'};
    const upload = await owner.request.post(`${origin}/api/company/materials`, {headers: {
      ...writeHeaders, 'Content-Type': 'text/plain', 'X-Filename': 'cached-private.txt',
      'X-Provenance-Kind': 'authorized_real', 'X-Source-Attestation-Encoded': encodeURIComponent('UNIT_PRIVATE_AUTHORIZATION_内部授权说明'),
    }, data: 'UNIT_PRIVATE_CACHED_BODY'});
    expect(upload.status()).toBe(201); const material = await upload.json();
    const profileResponse = await reader.request.post(`${origin}/api/company/profiles`, {headers: {
      'Origin': origin, 'X-CSRF-Token': readerMe.csrf_token, 'Content-Type': 'application/json',
    }, data: {payload: {display_name: 'UNIT_PRIVATE_PROFILE', facts: [], provenance_kind: 'engineering_fixture'}}});
    expect(profileResponse.status()).toBe(201); const profile = await profileResponse.json();
    async function grant() { expect((await owner.request.post(`${origin}/api/company/objects/${material.object_id}/grants`, {headers: writeHeaders, data: {subject_id: readerMe.user.subject_id, actions: ['read']}})).status()).toBe(200); }
    async function revoke() { expect((await owner.request.delete(`${origin}/api/company/objects/${material.object_id}/grants/${readerMe.user.subject_id}`, {headers: writeHeaders})).status()).toBe(200); }
    await grant();
    const page = await reader.newPage();
    async function showPrivate() {
      await page.goto(origin); await page.getByRole('button', {name: '企业画像与材料'}).click();
      await page.getByRole('button', {name: `读取画像 ${profile.object_id.slice(0, 12)}`}).click();
      await page.getByRole('button', {name: 'cached-private.txt', exact: true}).click();
      await page.getByRole('button', {name: '生成材料引用'}).click();
      await page.getByRole('button', {name: '添加事实', exact: true}).click();
      await expect(page.locator('.company-sources')).toContainText('UNIT_PRIVATE_CACHED_BODY');
      await expect(page.getByLabel('材料来源授权说明')).toHaveValue('UNIT_PRIVATE_AUTHORIZATION_内部授权说明');
      await page.getByLabel('检索可访问材料').fill('UNIT_PRIVATE_CACHED_BODY');
      await page.getByRole('button', {name: '检索材料', exact: true}).click();
      await expect(page.locator('.company-search-results')).toContainText('UNIT_PRIVATE_CACHED_BODY');
    }
    async function expectCleared() {
      await expect(page.locator('.company-material-text')).toHaveCount(0);
      await expect(page.locator('.company-sources')).toHaveCount(0);
      await expect(page.locator('.company-search-results')).toHaveCount(0);
      await expect(page.getByLabel('企业名称', {exact: true})).toHaveValue('');
      await expect(page.getByLabel('材料来源授权说明')).toHaveCount(0);
      await expect(page.locator('.company-panel')).not.toContainText('UNIT_PRIVATE_CACHED_BODY');
    }
    await showPrivate();
    // Another tab completes SSO: the cookie rotates while this page retains its
    // old CSRF token. A real backend 403 must clear all cached private values.
    await login(reader.request, reader, 'ui-cache-reader');
    const forbidden = page.waitForResponse(response => response.url().endsWith('/citations') && response.request().method() === 'POST');
    await page.getByRole('button', {name: '生成材料引用'}).click();
    expect((await forbidden).status()).toBe(403); await expectCleared();
    expect((await reader.request.get(`${origin}/api/company/materials/${material.object_id}`)).status()).toBe(200);

    await showPrivate(); await revoke();
    const successfulList = page.waitForResponse(response => response.url().endsWith('/api/company/materials?limit=20&offset=0'));
    await page.getByRole('button', {name: '刷新可访问对象'}).click();
    expect((await successfulList).status()).toBe(200);
    await expect(page.getByRole('alert')).toContainText('不可访问'); await expectCleared();

    // The selected material can be closed while its quote remains in the draft.
    // Refresh must also recheck that source, even with no selected material.
    await grant(); await showPrivate();
    await page.getByRole('button', {name: '上传新材料', exact: true}).click();
    await expect(page.locator('.company-material-text')).toHaveCount(0);
    await expect(page.locator('.company-sources')).toContainText('UNIT_PRIVATE_CACHED_BODY');
    await revoke(); await page.getByRole('button', {name: '刷新可访问对象'}).click();
    await expect(page.getByRole('alert')).toContainText('不可访问'); await expectCleared();
  } finally { await owner.close(); await reader.close(); }
});

test('refresh keeps an authorized selected fixed version when its row is on another page', async ({browser}) => {
  const owner = await browser.newContext();
  try {
    const me = await login(owner.request, owner, 'ui-pagination-owner');
    for (let index = 0; index < 21; index++) {
      expect((await owner.request.post(`${origin}/api/company/materials`, {headers: {
        'Origin': origin, 'X-CSRF-Token': me.csrf_token, 'Content-Type': 'text/plain', 'X-Filename': `pagination-${index}.txt`,
      }, data: `UNIT_PAGINATION_BODY_${index}`})).status()).toBe(201);
    }
    const page = await owner.newPage(); await page.goto(origin);
    await page.getByRole('button', {name: '企业画像与材料'}).click();
    const first = page.locator('.company-object-list').nth(1).getByRole('button').first();
    const filename = await first.innerText(); await first.click();
    await expect(page.getByRole('button', {name: '下载当前材料版本'})).toBeVisible();
    const body = await page.locator('.company-material-text').textContent();
    await page.getByRole('button', {name: '下一页材料'}).click();
    await expect(page.locator('.company-object-list').nth(1).getByRole('button', {name: filename, exact: true})).toHaveCount(0);
    await expect(page.getByRole('button', {name: '下载当前材料版本'})).toBeVisible();
    await expect(page.locator('.company-material-text')).toHaveText(body!);
    await expect(page.getByRole('alert')).toHaveCount(0);
  } finally { await owner.close(); }
});

test('retention policies survive profile history pagination and unchanged material replacement', async ({browser}) => {
  const owner = await browser.newContext();
  try {
    const me = await login(owner.request, owner, 'ui-profile-retention-owner');
    const headers = {'Origin': origin, 'X-CSRF-Token': me.csrf_token, 'Content-Type': 'application/json'};
    const firstUntil = new Date(Date.now() + 3 * 86400000).toISOString();
    const currentDate = new Date(Date.now() + 7 * 86400000); currentDate.setUTCSeconds(37, 123);
    const currentUntil = currentDate.toISOString();
    for (let index = 0; index < 21; index++) {
      expect((await owner.request.post(`${origin}/api/company/profiles`, {headers, data: {
        payload: {display_name: `UNIT_RETENTION_${index}`, facts: [], provenance_kind: 'engineering_fixture'}, retention_until: firstUntil,
      }})).status()).toBe(201);
    }
    const firstPage = await (await owner.request.get(`${origin}/api/company/profiles?limit=20`)).json();
    const selected = firstPage.items[0];
    const secondResponse = await owner.request.put(`${origin}/api/company/profiles/${selected.object_id}`, {headers, data: {
      payload: {display_name: 'UNIT_RETENTION_CURRENT', facts: [], provenance_kind: 'engineering_fixture'},
      expected_version: selected.head_version, retention_until: currentUntil,
    }});
    expect(secondResponse.status()).toBe(200); const second = await secondResponse.json();
    const page = await owner.newPage(); await page.goto(origin);
    await page.getByRole('button', {name: '企业画像与材料'}).click();
    await page.getByRole('button', {name: `读取画像 ${selected.object_id.slice(0, 12)}`}).click();
    const displayedUntil = await page.evaluate(stamp => {
      const date = new Date(stamp); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }, currentUntil);
    await expect(page.getByLabel('画像留存截止时间')).toHaveValue(displayedUntil);
    await page.getByRole('button', {name: '下一页画像'}).click();
    await expect(page.locator('.company-object-list').first().getByRole('button', {name: `读取画像 ${selected.object_id.slice(0, 12)}`})).toHaveCount(0);
    await page.getByLabel('历史版本编号').fill(selected.head_version);
    await page.getByRole('button', {name: '读取指定版本'}).click();
    await expect(page.getByLabel('画像留存截止时间')).toHaveValue(displayedUntil);
    await page.getByRole('button', {name: '保存画像新版本'}).click();
    await expect(page.getByRole('alert')).toContainText('版本已被更新');
    await page.getByLabel('历史版本编号').fill(second.version_id);
    await page.getByRole('button', {name: '读取指定版本'}).click();
    await expect(page.getByLabel('画像留存截止时间')).toHaveValue(displayedUntil);
    await page.getByLabel('企业名称', {exact: true}).fill('UNIT_RETENTION_SAVED');
    const update = page.waitForRequest(req => req.method() === 'PUT' && req.url().endsWith(`/api/company/profiles/${selected.object_id}`));
    await page.getByRole('button', {name: '保存画像新版本'}).click();
    expect((await update).postDataJSON()).not.toHaveProperty('retention_until');
    await expect(page.getByRole('status').filter({hasText: '企业画像已保存新版本'})).toContainText('企业画像已保存新版本');
    const saved = await (await owner.request.get(`${origin}/api/company/profiles/${selected.object_id}`)).json();
    expect(saved.retention_until).toBe(Date.parse(currentUntil) / 1000);
    expect(saved.version_id).not.toBe(second.version_id);
    expect(saved.payload.display_name).toBe('UNIT_RETENTION_SAVED');
    const materialResponse = await owner.request.post(`${origin}/api/company/materials`, {headers: {
      ...headers, 'Content-Type': 'text/plain', 'X-Filename': 'precision-retention.txt', 'X-Retention-Until': currentUntil,
    }, data: 'UNIT_PRECISE_RETENTION_FIRST'});
    expect(materialResponse.status()).toBe(201); const material = await materialResponse.json();
    await page.getByRole('button', {name: '刷新可访问对象'}).click();
    await page.getByRole('button', {name: 'precision-retention.txt', exact: true}).click();
    await expect(page.getByLabel('材料留存截止时间')).toHaveValue(displayedUntil);
    await page.getByLabel('选择材料文件').setInputFiles({name: 'precision-retention.txt', mimeType: 'text/plain', buffer: Buffer.from('UNIT_PRECISE_RETENTION_SECOND')});
    const replace = page.waitForRequest(req => req.method() === 'PUT' && req.url().endsWith(`/api/company/materials/${material.object_id}`));
    await page.getByRole('button', {name: '保存材料新版本'}).click();
    expect((await replace).headers()).not.toHaveProperty('x-retention-until');
    await expect(page.getByRole('status').filter({hasText: '材料已保存新版本'})).toContainText('材料已保存新版本');
    const updatedMaterial = await (await owner.request.get(`${origin}/api/company/materials/${material.object_id}`)).json();
    expect(updatedMaterial.retention_until).toBe(Date.parse(currentUntil) / 1000);
    expect(updatedMaterial.version_id).not.toBe(material.version_id);
  } finally { await owner.close(); }
});
