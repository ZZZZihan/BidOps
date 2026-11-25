import { expect, test, type Page, type Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';

// Authored protocol fixtures exercise UI decisions, not real people, IdP, models
// or qualification quality. The separate real suite uses actual HTTP/SQLite ACL.
const CASE = 'unit-review-case', OWNER = 'unit-owner', REVISION = 'unit-review-v1';
const PROFILE = {object_id: 'unit-profile', version_id: 'unit-profile-v1', sha256: 'a'.repeat(64)};
const MATERIAL = {object_id: 'unit-material', version_id: 'unit-material-v2', sha256: 'b'.repeat(64)};
const NOTICE = {object_id: 'unit-notice', version_id: 'unit-notice-v1', sha256: 'c'.repeat(64)};
const ACTIONS = ['assign', 'decide', 'successor', 'attach', 'close', 'export'];
const statuses = ['satisfied', 'not_satisfied', 'information_insufficient', 'evidence_conflict', 'not_applicable'];
type Decision = 'confirm' | 'reject' | 'request_materials';
function evidence(side: string, index: number) {
  const quote = side === 'notice' ? `UNIT 公告要求 ${index}` : `UNIT 私有证明 ${index} <img src=x onerror=alert(1)>`;
  return {evidence_id: `${side}-${index}`, side, object_kind: side === 'notice' ? 'notice' : 'company_material', version: side === 'notice' ? NOTICE : MATERIAL, text_sha256: 'd'.repeat(64), quote, start: 0, end: Array.from(quote).length, locator: 'normalized_text_v2'};
}
function condition(index: number) {return {condition_id: `condition-${index}`, original_text: `UNIT 原条件 ${index + 1}`, hard_requirement: true, expression: {operator: 'any', text: '资质 A 或例外', operands: [{operator: 'atom', text: '资质 A', operands: []}, {operator: 'not', text: '不得属于限制情形', operands: []}]}, package_scope: {kind: 'specific', package_ids: ['unit-package']}, validity: {kind: 'unknown', reason: 'UNIT 期限依据缺失'}, notice_evidence: [evidence('notice', index)]};}
function judgment(index: number) {return {condition_id: `condition-${index}`, status: statuses[index], reason: `UNIT 原判断理由 ${index + 1}`, notice_evidence: [evidence('notice', index)], company_evidence: index === 2 ? [] : [evidence('company', index)], missing_items: index === 2 ? ['UNIT 尚缺证明'] : [], conflicting_evidence_ids: index === 3 ? ['company-3', 'company-conflict'] : []};}
function event(index: number, decision: Decision = 'confirm', version = 2, reason = 'UNIT 原审核理由') {
  return {event_id: `unit-event-${index}-${version}`, event_kind: 'decision', revision_id: REVISION, assessment_id: 'unit-assessment', condition_id: `condition-${index}`, actor_id: 'unit-reviewer', occurred_at: '2026-10-23T01:00:00Z', review_version: version, supersedes_event_id: null as string | null, reason, review_event: {command: {decision, reason, expected_result_revision: 'unit-result-v1', idempotency_key: `unit-key-${index}-${version}`}, actor: {principal_id: 'unit-reviewer'}, origin: 'server_authenticated'}};
}
type SavedEvent = ReturnType<typeof event>;
function savedCase() {
  const conditions = statuses.map((_, index) => condition(index)), assessments = statuses.map((_, index) => judgment(index));
  const manifest = {project_id: 'unit-project', as_of: '2026-10-23T00:00:00Z', profile_revision: PROFILE, target_package_ids: ['unit-package'], material_revisions: [MATERIAL], notice_revisions: [NOTICE]};
  const result = {run_id: 'unit-result', result_revision: 'unit-result-v1', state: 'completed', human_review_state: 'unreviewed', inventory: {unresolved: [{document_id: 'UNIT 未解析附件', reason: 'UNIT 仍需核实'}], missing_attachment_ids: ['unit-missing-attachment']}, groups: [{primary_notice_revision: NOTICE, assessment: {assessment_id: 'unit-assessment', target_package_id: 'unit-package', conditions, assessments}}], missing_items: ['UNIT 尚缺证明'], provenance: {prompt_version: 'unit-prompt-v1', rule_version: 'unit-rule-v1'}};
  return {case_id: CASE, owner_id: OWNER, head_revision_id: REVISION, review_version: 1, status: 'open', source_state: {current: true, stale_reasons: [] as string[]}, allowed_actions: [...ACTIONS], assignments: [OWNER, 'unit-reviewer'], candidate_batch: {result_id: 'unit-batch', result: {manifest_sha256: 'e'.repeat(64), filters: {keyword: 'UNIT 原筛选'}, counts: {total_units: 2, candidate: 1, excluded: 1, needs_verification: 0}, items: [{unit_id: 'unit-candidate', title: 'UNIT 纳入项目', disposition: 'candidate', reasons: []}, {unit_id: 'unit-excluded', title: 'UNIT 排除项目', disposition: 'excluded', reasons: ['keyword_mismatch']}]}}, revisions: [{revision_id: REVISION, parent_revision_id: null as string | null, run_id: 'unit-waiting-job', result_id: 'unit-result', result_revision: 'unit-result-v1', inputs_manifest_sha256: 'f'.repeat(64), created_at: '2026-10-23T00:00:00Z', manifest, result, provenance: result.provenance, source_state: {current: true, stale_reasons: [] as string[]}, is_head: true}], items: statuses.map((_, index) => ({revision_id: REVISION, assessment_id: 'unit-assessment', condition_id: `condition-${index}`, condition: condition(index), agent_assessment: judgment(index), latest_decision: null as Decision | null, latest_event: null as SavedEvent | null, effective: false, ineffective_reasons: [] as string[]})), events: [] as SavedEvent[], successors: [] as {run_id: string; parent_revision_id: string; trigger_event_id: string; link_event_id: string; inputs_manifest_sha256: string; created_at: string}[]};
}
type Posted = {path: string; body: Record<string, any>; csrf?: string};
type Control = {enabled: boolean; current: ReturnType<typeof savedCase>; deny: number; historyEmpty: boolean; reads: number; posts: Posted[]; hook?: (route: Route, post: Posted) => Promise<boolean>; exportDenied: boolean; quarantine: boolean};
async function protocol(page: Page, options: {enabled?: boolean; configured?: boolean; worker?: boolean; mode?: string; authenticated?: boolean; owner?: string; permissions?: string[]; pages?: boolean; candidatePages?: boolean} = {}) {
  const control: Control = {enabled: options.enabled ?? true, current: savedCase(), deny: 0, historyEmpty: false, reads: 0, posts: [], exportDenied: false, quarantine: false};
  if (options.candidatePages) {
    const item = control.current.candidate_batch.result.items[0];
    control.current.candidate_batch.result.items = Array.from({length: 41}, (_, index) => ({...item, unit_id: `unit-candidate-${index}`, title: `UNIT 分页项目 ${index}`}));
    control.current.candidate_batch.result.counts = {total_units: 41, candidate: 41, excluded: 0, needs_verification: 0};
  }
  await page.route('**/api/auth/me', route => route.fulfill({json: {mode: options.mode || 'enterprise', authenticated: options.authenticated ?? true, organization: 'UNIT', user: {name: 'UNIT 审核人', roles: ['reviewer'], subject_id: options.owner || OWNER}, permissions: options.permissions || ['catalog:read', 'agent:use', 'review:decide'], csrf_token: 'unit-csrf'}}));
  await page.route('**/api/agent/status', route => route.fulfill({json: {enabled: false, configured: false, notice_count: 0}}));
  await page.route('**/api/sessions', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/notices?**', route => route.fulfill({json: {items: [], total: 0, offset: 0, limit: 20, has_more: false}}));
  await page.route('**/api/sources', route => route.fulfill({json: {items: []}}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok'}}));
  await page.route('**/api/company/profiles**', route => route.fulfill({json: new URL(route.request().url()).pathname.endsWith('/profiles') ? {items: [{object_id: PROFILE.object_id, head_version: PROFILE.version_id}], total: 1, offset: 0, limit: 20} : {...PROFILE, payload: {display_name: 'UNIT 新计算画像'}}}));
  await page.route('**/api/company/materials**', route => route.fulfill({json: new URL(route.request().url()).pathname.endsWith('/materials') ? {items: [{...MATERIAL, filename: 'UNIT 新材料.txt'}], total: 1, offset: 0, limit: 20} : {...MATERIAL, filename: 'UNIT 新材料.txt'}}));
  await page.route('**/api/reviews/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (path.endsWith('/status')) return route.fulfill({json: {enabled: control.enabled, configured: options.configured ?? true, worker_available: options.worker ?? true}});
    if (req.method() === 'POST') {
      const post: Posted = {path, body: req.postDataJSON(), csrf: req.headers()['x-csrf-token']}; control.posts.push(post);
      if (control.hook && await control.hook(route, post)) return;
      if (path.endsWith('/decisions')) {
        const body = post.body, item = control.current.items.find(value => value.assessment_id === body.assessment_id && value.condition_id === body.condition_id)!;
        const saved = {...event(Number(body.condition_id.split('-')[1]), body.command.decision, control.current.review_version + 1, body.command.reason), assessment_id: body.assessment_id, supersedes_event_id: body.supersedes_event_id};
        control.current.events.push(saved); item.latest_event = saved; item.latest_decision = saved.review_event.command.decision; item.effective = true;
      }
      if (path.endsWith('/assignments')) control.current.assignments = post.body.reviewer_ids;
      if (path.endsWith('/successors')) {control.current.status = 'recomputing'; control.current.successors.push({run_id: 'unit-successor-job', parent_revision_id: REVISION, trigger_event_id: post.body.trigger_event_id, link_event_id: 'unit-link', inputs_manifest_sha256: '9'.repeat(64), created_at: '2026-10-23T02:00:00Z'});}
      if (path.endsWith('/revisions')) {
        control.current.revisions[0].is_head = false; const next = structuredClone(control.current.revisions[0]);
        next.revision_id = 'unit-review-v2'; next.parent_revision_id = REVISION; next.result_revision = 'unit-result-v2'; next.result.result_revision = next.result_revision; next.is_head = true; next.run_id = post.body.run_id;
        control.current.revisions.push(next); control.current.head_revision_id = next.revision_id; control.current.status = 'open';
        control.current.items = control.current.items.map(value => ({...value, revision_id: next.revision_id, latest_decision: null, latest_event: null, effective: false, ineffective_reasons: []}));
      }
      if (path.endsWith('/close')) {control.current.status = 'closed'; control.current.allowed_actions = ['export'];}
      if (path.endsWith('/reauthorize')) control.quarantine = false;
      if (!path.endsWith('/cases')) control.current.review_version++;
      return route.fulfill({status: path.endsWith('/cases') ? 201 : path.endsWith('/successors') ? 202 : 200, json: control.current});
    }
    if (path.endsWith('/export')) return control.exportDenied ? route.fulfill({status: 403, json: {detail: 'review_export_forbidden'}}) : route.fulfill({json: {case: control.current, export_schema: 'unit-review-export-v1'}});
    control.reads++;
    if (path.endsWith('/cases')) {
      const offset = Number(url.searchParams.get('offset') || 0), count = options.pages ? (offset ? 1 : 20) : 1;
      return route.fulfill({json: {items: control.historyEmpty ? [] : Array.from({length: count}, (_, index) => ({...control.current, case_id: index + offset ? `unit-case-${offset + index}` : CASE})), total: control.historyEmpty ? 0 : options.pages ? 21 : 1, offset, limit: 20}});
    }
    if (control.deny) return route.fulfill({status: control.deny, json: {detail: control.deny === 401 ? 'current_identity_required' : 'review_not_found'}});
    if (control.quarantine) return route.fulfill({json: {...control.current, allowed_actions: ['reauthorize'], candidate_batch: null, items: [], revisions: [], events: [], assignments: [], successors: [], source_state: {current: false, stale_reasons: ['restore_quarantine']}}});
    if (options.candidatePages) {
      const offset = Number(url.searchParams.get('candidate_offset') || 0), limit = Number(url.searchParams.get('candidate_limit') || 20);
      const value = structuredClone(control.current), total = value.candidate_batch.result.items.length;
      value.candidate_batch.result.items = value.candidate_batch.result.items.slice(offset, offset + limit);
      return route.fulfill({json: {...value, candidate_batch: {...value.candidate_batch, page: {offset, limit, total, has_more: offset + limit < total}}}});
    }
    return route.fulfill({json: control.current});
  });
  return control;
}
async function open(page: Page) {await page.goto('/'); await page.getByRole('button', {name: '人工复核', exact: true}).click();}
async function read(page: Page) {await page.getByRole('textbox', {name: '已有复核编号', exact: true}).fill(CASE); await page.getByRole('button', {name: '读取复核', exact: true}).click(); await expect(page.locator('.reviews-case-id')).toHaveText(CASE);}
function card(page: Page, index = 0) {return page.locator('.reviews-condition').filter({has: page.getByRole('heading', {name: `UNIT 原条件 ${index + 1}`, exact: true})}).first();}
async function fillDecision(page: Page, index = 0, decision = 'confirm', reason = 'UNIT 独立审核理由') {const item = card(page, index); await item.getByRole('combobox', {name: '该条人工决定'}).selectOption(decision); await item.getByRole('textbox', {name: '该条独立审核理由'}).fill(reason); return item;}
function setDecision(control: Control, index = 0, decision: Decision = 'request_materials') {const value = event(index, decision); control.current.events.push(value); Object.assign(control.current.items[index], {latest_decision: decision, latest_event: value, effective: true}); control.current.review_version = 2;}

test('default closed feature makes no private case reads or submissions', async ({page}) => {
  const control = await protocol(page, {enabled: false}); await open(page); await expect(page.getByRole('heading', {name: '人工复核尚未开启'})).toBeVisible(); expect(control.reads).toBe(0); expect(control.posts).toHaveLength(0);
});
for (const options of [{mode: 'local'}, {authenticated: false}]) test(`reviews require authenticated enterprise mode ${JSON.stringify(options)}`, async ({page}) => {const control = await protocol(page, options); await page.goto('/'); await expect(page.getByRole('button', {name: '人工复核', exact: true})).toHaveCount(0); expect(control.reads).toBe(0);});
test('owner creation uses the fixed job, batch and explicit reviewers with CSRF', async ({page}) => {
  const control = await protocol(page); await open(page); await page.getByRole('textbox', {name: '等待审核的任务编号', exact: true}).fill('unit-waiting-job'); await page.getByRole('textbox', {name: '固定候选批次编号', exact: true}).fill('unit-batch'); await page.getByRole('textbox', {name: '指定审核人主体编号（逗号或换行分隔）', exact: true}).fill('unit-reviewer， unit-second\nunit-reviewer'); await page.getByRole('button', {name: '创建复核案件', exact: true}).click();
  await expect(page.locator('.reviews-case-id')).toHaveText(CASE); expect(control.posts[0]).toMatchObject({csrf: 'unit-csrf', body: {run_id: 'unit-waiting-job', candidate_batch_id: 'unit-batch', reviewer_ids: ['unit-reviewer', 'unit-second']}}); expect(control.posts[0].body.idempotency_key).toMatch(/^[a-f0-9-]{36}$/); await expect(page.getByRole('status').filter({hasText: '服务器已保存复核案件'})).toBeVisible();
});
test('all five immutable Agent judgments, both evidence sides, versions and gaps are readable', async ({page}) => {
  await protocol(page); await open(page); await read(page); for (const text of ['满足', '不满足', '信息不足', '证据冲突', '不适用']) await expect(page.locator('.reviews-agent .reviews-badge').filter({hasText: new RegExp(`^${text}$`)})).toHaveCount(1);
  await expect(page.getByText('缺失材料：UNIT 尚缺证明', {exact: true})).toBeVisible(); await expect(page.getByText('冲突证据：company-3、company-conflict')).toBeVisible(); await expect(page.getByText('未解析来源 UNIT 未解析附件：UNIT 仍需核实')).toBeVisible();
  const first = card(page); await first.getByText('company-0 · 企业证明材料', {exact: true}).click(); await expect(first.getByText('UNIT 私有证明 0 <img src=x onerror=alert(1)>', {exact: true})).toBeVisible(); await expect(first.getByText(MATERIAL.sha256, {exact: true})).toBeVisible(); await expect(first.locator('img')).toHaveCount(0);
  await expect(page.getByText('unreviewed（原始 Agent 结果尚未人审，保持不变）', {exact: true})).toBeVisible(); await expect(page.getByRole('button', {name: /全选|一键批准|正式资格/})).toHaveCount(0); expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
});
test('each decision starts blank, needs its own reason and does not replace the Agent result', async ({page}) => {
  const control = await protocol(page); await open(page); await read(page); const first = card(page), second = card(page, 1);
  await expect(first.getByRole('combobox', {name: '该条人工决定'})).toHaveValue(''); await expect(first.getByRole('button', {name: '提交该条人工决定'})).toBeDisabled(); await first.getByRole('combobox').selectOption('confirm'); await expect(first.getByRole('button', {name: '提交该条人工决定'})).toBeDisabled();
  await fillDecision(page); await expect(second.getByRole('combobox')).toHaveValue(''); await expect(second.getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue(''); await first.getByRole('button', {name: '提交该条人工决定'}).click();
  await expect(first.locator('.reviews-human-decision')).toHaveText('确认原判断 · 当前有效'); expect(control.posts[0]).toMatchObject({body: {revision_id: REVISION, assessment_id: 'unit-assessment', condition_id: 'condition-0', expected_version: 1, command: {decision: 'confirm', expected_result_revision: 'unit-result-v1', reason: 'UNIT 独立审核理由'}, supersedes_event_id: null}}); await expect(first.getByText('UNIT 原判断理由 1')).toBeVisible(); expect(control.current.revisions[0].result.human_review_state).toBe('unreviewed');
});
test('same condition IDs in two procurement assessments keep independent drafts and commands', async ({page}) => {
  const control = await protocol(page); control.current.items.push({...structuredClone(control.current.items[0]), assessment_id: 'unit-other-package-assessment', condition: {...condition(0), original_text: 'UNIT 另一采购包同编号条件'}}); await open(page); await read(page); await fillDecision(page, 0, 'reject', 'UNIT 仅驳回第一包');
  const other = page.locator('.reviews-condition').filter({has: page.getByRole('heading', {name: 'UNIT 另一采购包同编号条件', exact: true})}); await expect(other.getByRole('combobox')).toHaveValue(''); await other.getByRole('combobox').selectOption('request_materials'); await other.getByRole('textbox', {name: '该条独立审核理由'}).fill('UNIT 第二包补证'); await other.getByRole('button', {name: '提交该条人工决定'}).click();
  expect(control.posts[0].body).toMatchObject({assessment_id: 'unit-other-package-assessment', condition_id: 'condition-0', command: {reason: 'UNIT 第二包补证'}}); await expect(card(page).getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue('UNIT 仅驳回第一包');
});
test('correction appends a superseding event and retains the original reason and actor', async ({page}) => {
  const control = await protocol(page); setDecision(control, 0, 'confirm'); const original = structuredClone(control.current.events[0]); await open(page); await read(page); const first = card(page); await first.getByRole('button', {name: '追加更正该条理由与决定'}).click(); await expect(first.getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue(''); await fillDecision(page, 0, 'reject', 'UNIT 更正为驳回的新理由'); await first.getByRole('button', {name: '提交该条人工决定'}).click();
  expect(control.posts[0].body.supersedes_event_id).toBe(original.event_id); expect(control.current.events[0]).toEqual(original); expect(control.current.events).toHaveLength(2); await first.getByText('该条全部事件（保留原理由）', {exact: true}).click(); await expect(first.getByText('UNIT 原审核理由', {exact: true})).toBeVisible(); await expect(first.getByText('UNIT 更正为驳回的新理由', {exact: true}).first()).toBeVisible();
});
test('a second reviewer CAS conflict preserves draft and requires an explicit refreshed resubmission', async ({page}) => {
  const control = await protocol(page); let conflict = true; control.hook = async (route, post) => {if (!post.path.endsWith('/decisions') || !conflict) return false; conflict = false; control.current.review_version = 2; await route.fulfill({status: 409, json: {detail: 'review_version_conflict'}}); return true;}; await open(page); await read(page); const first = await fillDecision(page, 0, 'reject', 'UNIT 并发独立理由'); await first.getByRole('button', {name: '提交该条人工决定'}).click();
  await expect(page.getByRole('alert')).toContainText('没有自动重试'); await expect(first.getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue('UNIT 并发独立理由'); expect(control.posts).toHaveLength(1); await page.getByRole('button', {name: '刷新复核工作区'}).click(); await expect(page.locator('.reviews-version')).toHaveText('2'); await expect(first.getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue('UNIT 并发独立理由'); await first.getByRole('button', {name: '提交该条人工决定'}).click(); await expect.poll(() => control.posts.length).toBe(2); expect(control.posts[1].body.expected_version).toBe(2);
});
test('an unconfirmed decision response shows no success and retry retains the exact idempotency key', async ({page}) => {
  const control = await protocol(page); let first = true; control.hook = async (route, post) => {if (!post.path.endsWith('/decisions') || !first) return false; first = false; await route.abort('failed'); return true;}; await open(page); await read(page); const item = await fillDecision(page); await item.getByRole('button', {name: '提交该条人工决定'}).click(); await expect(page.getByRole('alert')).toContainText('应答未确认'); await expect(page.getByRole('status').filter({hasText: '已保存该条人工决定'})).toHaveCount(0); await item.getByRole('button', {name: '提交该条人工决定'}).click(); await expect(item.locator('.reviews-human-decision')).toContainText('当前有效'); expect(control.posts[1].body).toEqual(control.posts[0].body);
});
test('admin management permission alone does not expose a human decision control', async ({page}) => {
  await protocol(page, {permissions: ['catalog:read', 'agent:use', 'audit:read']}); await open(page); await read(page); await expect(page.getByRole('button', {name: '提交该条人工决定'})).toHaveCount(0); await expect(page.getByText('逐项决定要求 review:decide 角色和明确 assignment', {exact: false})).toBeVisible();
});
test('assigned reviewer can decide but cannot manage owner assignment, new tasks or closure', async ({page}) => {
  await protocol(page, {owner: 'unit-reviewer', permissions: ['catalog:read', 'review:decide']}); await open(page); await read(page); await expect(card(page).getByRole('combobox')).toBeEnabled(); for (const name of ['保存审核人设置', '创建关联的新计算任务', '关闭本次内部复核']) await expect(page.getByRole('button', {name, exact: true})).toHaveCount(0);
});
test('assignment is explicit and never sends material ACL grants', async ({page}) => {
  const control = await protocol(page); await open(page); await read(page); await page.getByRole('textbox', {name: '更新指定审核人主体编号', exact: true}).fill('unit-new-reviewer'); await page.getByRole('textbox', {name: '审核人调整理由'}).fill('UNIT 明确转交复核'); await page.getByRole('button', {name: '保存审核人设置'}).click(); await expect(page.locator('.reviews-version')).toHaveText('2'); expect(control.posts).toHaveLength(1); expect(control.posts[0]).toMatchObject({path: `/api/reviews/cases/${CASE}/assignments`, body: {expected_version: 1, reviewer_ids: ['unit-new-reviewer'], reason: 'UNIT 明确转交复核'}}); expect(control.posts[0].body).not.toHaveProperty('grants');
});
test('source stale and ineffective human projection remain separate from the original Agent judgment', async ({page}) => {
  const control = await protocol(page); setDecision(control, 0, 'confirm'); control.current.source_state = {current: false, stale_reasons: ['notice_versions_changed']}; control.current.revisions[0].source_state = control.current.source_state; control.current.items[0].effective = false; control.current.items[0].ineffective_reasons = ['source_changed']; control.current.allowed_actions = ['export']; await open(page); await read(page); await expect(page.getByText('来源或输入已有变化', {exact: false})).toBeVisible();
  await expect(card(page).locator('.reviews-human-decision')).toHaveText('确认原判断 · 当前无效，不作为有效决定'); await expect(card(page).locator('.reviews-agent .reviews-badge')).toHaveText('满足'); await expect(page.getByRole('button', {name: '提交该条人工决定'})).toHaveCount(0);
});
test('supplemental materials pin real refs and create a linked successor without overwriting old inputs', async ({page}) => {
  const control = await protocol(page); setDecision(control); const original = structuredClone(control.current.revisions[0]); await open(page); await read(page); await page.getByRole('combobox', {name: '触发补材料的人工事件'}).selectOption(control.current.events[0].event_id); await page.getByRole('button', {name: '选择新计算的画像版本'}).click(); await page.getByRole('button', {name: `选择画像 ${PROFILE.object_id.slice(0, 12)}`}).click(); await expect(page.getByRole('heading', {name: 'UNIT 新计算画像'})).toBeVisible(); await page.getByRole('button', {name: '选择新增证明材料'}).click(); await page.getByRole('checkbox', {name: /UNIT 新材料.txt/}).check(); await page.getByRole('textbox', {name: '新计算项目编号', exact: true}).fill('unit-project'); await page.getByRole('textbox', {name: '新计算采购包（逗号或空格分隔）'}).fill('unit-package'); await page.getByRole('textbox', {name: '补材料与重算理由'}).fill('UNIT 已补证明，另建全量重算'); await page.getByRole('button', {name: '创建关联的新计算任务'}).click();
  await expect(page.locator('.reviews-case-status')).toHaveText('补材料后重新计算中'); expect(control.posts[0]).toMatchObject({body: {expected_version: 2, revision_id: REVISION, trigger_event_id: control.current.events[0].event_id, request: {profile_revision: PROFILE, project_id: 'unit-project', target_package_ids: ['unit-package'], additional_material_revisions: [MATERIAL]}}}); expect(control.current.revisions[0]).toEqual(original); await expect(page.getByText('unit-successor-job', {exact: true})).toBeVisible();
});
test('attaching a computed successor resets every item and preserves selectable read-only old history', async ({page}) => {
  const control = await protocol(page); setDecision(control, 0, 'confirm'); await open(page); await read(page); await fillDecision(page, 1, 'reject', 'UNIT 不得带入新修订的草稿'); await page.getByRole('textbox', {name: '已计算就绪的新任务编号'}).fill('unit-new-ready-job'); await page.getByRole('textbox', {name: '接入新修订理由'}).fill('UNIT 接入新计算，逐条重审'); await page.getByRole('button', {name: '显式接入已完成的新修订'}).click(); await expect(page.getByRole('combobox', {name: '查看固定修订'})).toHaveValue('unit-review-v2'); for (const item of control.current.items) expect(item.latest_decision).toBeNull(); await expect(card(page, 1).getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue(''); await expect(card(page).locator('.reviews-human-decision')).toHaveText('尚未人工决定 · 未形成有效决定');
  await page.getByRole('combobox', {name: '查看固定修订'}).selectOption(REVISION); await expect(page.getByText('正在查看历史修订及当时决定', {exact: false})).toBeVisible(); await expect(page.getByRole('button', {name: '提交该条人工决定'})).toHaveCount(0); await expect(page.getByText('UNIT 原审核理由', {exact: true}).first()).toBeVisible(); expect(control.current.revisions[0].result.human_review_state).toBe('unreviewed');
});
test('missing calculation worker still allows human decisions and ready-result attachment', async ({page}) => {
  await protocol(page, {worker: false}); await open(page); await read(page); await expect(page.getByText('计算 worker 当前不可用', {exact: false})).toBeVisible(); const item = await fillDecision(page); await expect(item.getByRole('button', {name: '提交该条人工决定'})).toBeEnabled(); await expect(page.getByRole('button', {name: '创建关联的新计算任务'})).toBeDisabled(); await page.getByRole('textbox', {name: '已计算就绪的新任务编号'}).fill('unit-ready'); await page.getByRole('textbox', {name: '接入新修订理由'}).fill('UNIT 接入已有结果'); await expect(page.getByRole('button', {name: '显式接入已完成的新修订'})).toBeEnabled();
});
test('closing is an explicit reasoned case command, never a fake job completion or blanket approval', async ({page}) => {
  const control = await protocol(page); for (let index = 0; index < 5; index++) setDecision(control, index, index % 2 ? 'reject' : 'confirm'); await open(page); await read(page); await expect(page.getByRole('button', {name: '关闭本次内部复核'})).toBeDisabled(); await page.getByRole('textbox', {name: '关闭理由', exact: true}).fill('UNIT 内部复核结束'); await page.getByRole('button', {name: '关闭本次内部复核'}).click(); await expect(page.locator('.reviews-case-status')).toHaveText('复核已关闭'); expect(control.posts[0]).toMatchObject({body: {expected_version: 2, reason: 'UNIT 内部复核结束'}}); expect(control.posts[0].body).not.toHaveProperty('decision'); await expect(page.getByRole('status').filter({hasText: '没有执行外部采购提交'})).toBeVisible();
});
test('export uses the server-controlled JSON closure and a denied export clears private detail', async ({page}) => {
  const control = await protocol(page); setDecision(control, 0, 'reject'); await open(page); await read(page); const download = page.waitForEvent('download'); await page.getByRole('button', {name: '导出受控 JSON 审核包'}).click(); const artifact = await download; expect(artifact.suggestedFilename()).toBe(`bidops-review-${CASE}.json`); const saved = JSON.parse(await readFile((await artifact.path())!, 'utf8')); expect(saved.case.events[0].reason).toBe('UNIT 原审核理由'); expect(saved.case.revisions[0].result.human_review_state).toBe('unreviewed'); expect(saved.case.candidate_batch.result.items[1].disposition).toBe('excluded'); await expect(page.getByRole('status').filter({hasText: '重新检查完整来源权限'})).toBeVisible(); control.exportDenied = true; await page.getByRole('button', {name: '导出受控 JSON 审核包'}).click(); await expect(page.getByRole('alert')).toContainText('403'); await expect(page.locator('.reviews-detail')).toHaveCount(0); await expect(page.getByRole('textbox', {name: '已有复核编号', exact: true})).toHaveValue('');
});
for (const status of [401, 403, 404]) test(`fixed case denial ${status} clears all private cached content even after an empty 200 history`, async ({page}) => {
  const control = await protocol(page); await open(page); await read(page); await fillDecision(page, 0, 'confirm', 'UNIT 私有审核草稿'); control.historyEmpty = true; control.deny = status; await page.getByRole('button', {name: '刷新复核工作区'}).click(); await expect(page.getByRole('alert')).toBeVisible(); await expect(page.locator('.reviews-detail')).toHaveCount(0); await expect(page.getByText('UNIT 私有审核草稿', {exact: true})).toHaveCount(0); await expect(page.getByRole('textbox', {name: '等待审核的任务编号', exact: true})).toHaveValue(''); await expect(page.getByRole('textbox', {name: '指定审核人主体编号（逗号或换行分隔）', exact: true})).toHaveValue('');
});
test('feature being disabled on refresh erases private state without fetching more cases', async ({page}) => {const control = await protocol(page); await open(page); await read(page); const before = control.reads; control.enabled = false; await page.getByRole('button', {name: '刷新复核工作区'}).click(); await expect(page.locator('.reviews-detail')).toHaveCount(0); await expect(page.getByRole('heading', {name: '人工复核尚未开启'})).toBeVisible(); expect(control.reads).toBe(before);});
test('history pagination exposes the final case beyond the first twenty', async ({page}) => {await protocol(page, {pages: true}); await open(page); const history = page.locator('.reviews-card').filter({has: page.getByRole('heading', {name: '读取已有复核', exact: true})}); await expect(history.getByText('共 21 项 · 1–20')).toBeVisible(); await history.getByRole('button', {name: '下一页', exact: true}).click(); await expect(history.getByText('共 21 项 · 21–21')).toBeVisible(); await expect(history.getByRole('button', {name: 'unit-case-20', exact: true})).toBeVisible(); await expect(history.getByRole('button', {name: '下一页', exact: true})).toBeDisabled();});
test('restored case requires explicit ID, known CAS version and reason, with no automatic approval', async ({page}) => {
  const control = await protocol(page); await open(page); await page.getByRole('textbox', {name: '已有复核编号', exact: true}).fill(CASE); await page.getByText('恢复后 owner 显式解除案件隔离', {exact: true}).click(); await page.getByRole('spinbutton', {name: '恢复案件的已知复核版本'}).fill('7'); await page.getByRole('textbox', {name: '案件再授权理由'}).fill('UNIT 已核对最新授权记录'); await page.getByRole('button', {name: '请求案件再授权'}).click(); await expect(page.locator('.reviews-detail')).toBeVisible(); expect(control.posts[0]).toMatchObject({path: `/api/reviews/cases/${CASE}/reauthorize`, body: {expected_version: 7, reason: 'UNIT 已核对最新授权记录'}}); for (const value of control.current.items) expect(value.latest_decision).toBeNull();
});
test('mobile review remains readable without horizontal overflow', async ({page}) => {await page.setViewportSize({width: 390, height: 844}); await protocol(page); await open(page); await read(page); await card(page).getByText('company-0 · 企业证明材料', {exact: true}).click(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true); await expect(card(page).getByRole('button', {name: '提交该条人工决定'})).toBeVisible();});

test('a restored quarantine response renders only metadata and no private evidence or old assignment', async ({page}) => {const control = await protocol(page); control.quarantine = true; await open(page); await read(page); await expect(page.getByText('案件恢复隔离尚未解除', {exact: false})).toBeVisible(); await expect(page.locator('.reviews-version')).toHaveText('1'); await expect(page.getByText('UNIT 原条件 1', {exact: true})).toHaveCount(0); await expect(page.getByRole('button', {name: '提交该条人工决定'})).toHaveCount(0); await expect(page.getByRole('button', {name: '导出受控 JSON 审核包'})).toHaveCount(0);});

test('persisted human decisions and closed case do not imply native task delivery has finished', async ({page}) => {const control = await protocol(page, {worker: false}); control.current.status = 'closed'; control.current.allowed_actions = ['export']; Object.assign(control.current, {delivery: {pending_count: 1, frozen_count: 1, worker_available: false, items: [{event_id: 'unit-pending-close', kind: 'closed', last_error_code: 'review_backend_unavailable'}]}}); await open(page); await read(page); await expect(page.locator('.reviews-delivery')).toContainText('人工事件已保存'); await expect(page.locator('.reviews-delivery')).toContainText('这不表示关联任务已完成'); await expect(page.getByText('有 1 项旧事件已冻结', {exact: false})).toBeVisible(); await expect(page.getByRole('button', {name: '提交该条人工决定'})).toHaveCount(0);});


test('candidate paging reaches the last item and full export preserves every page', async ({page}) => {
  await protocol(page, {candidatePages: true}); await open(page); await read(page);
  const candidates = page.locator('.reviews-candidates');
  await expect(candidates.locator('li')).toHaveCount(20);
  await expect(candidates).toContainText('共 41 项 · 1–20');
  await fillDecision(page, 0, 'reject', 'UNIT 翻页保留未提交草稿');
  await candidates.getByRole('button', {name: '候选下一页'}).click();
  await expect(candidates).toContainText('共 41 项 · 21–40');
  await candidates.getByRole('button', {name: '候选下一页'}).click();
  await expect(candidates.locator('li')).toHaveCount(1);
  await expect(candidates).toContainText('UNIT 分页项目 40');
  await expect(candidates).toContainText('共 41 项 · 41–41');
  await expect(candidates.getByRole('button', {name: '候选下一页'})).toBeDisabled();
  await expect(card(page).getByRole('textbox', {name: '该条独立审核理由'})).toHaveValue('UNIT 翻页保留未提交草稿');
  const download = page.waitForEvent('download');
  await page.getByRole('button', {name: '导出受控 JSON 审核包'}).click();
  const artifact = await download;
  const saved = JSON.parse(await readFile((await artifact.path())!, 'utf8'));
  expect(saved.case.candidate_batch.result.items).toHaveLength(41);
  await candidates.getByRole('button', {name: '候选上一页'}).click();
  await expect(candidates).toContainText('共 41 项 · 21–40');
});

for (const status of [401, 403, 404]) test(`candidate page denial ${status} clears the previous private page and draft`, async ({page}) => {
  const control = await protocol(page, {candidatePages: true}); await open(page); await read(page);
  await fillDecision(page, 0, 'confirm', 'UNIT 翻页撤权后的私有草稿');
  control.deny = status;
  await page.getByRole('button', {name: '候选下一页'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('.reviews-detail')).toHaveCount(0);
  await expect(page.locator('.reviews-candidates')).toHaveCount(0);
  await expect(page.getByRole('textbox', {name: '已有复核编号', exact: true})).toHaveValue('');
});
