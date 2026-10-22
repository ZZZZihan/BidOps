import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, get, request } from './api';
import type { Identity } from './Enterprise';
import { NoticeEvidenceLocation, NoticeLocationContext } from './NoticeEvidenceLocation';
import './Reviews.css';

type VersionRef = {object_id: string; version_id: string; sha256: string};
type SourceState = {current: boolean; stale_reasons: string[]};
type Evidence = {evidence_id: string; side?: string; object_kind?: string; version: VersionRef; text_sha256?: string; quote: string; start: number; end: number; locator: string; page?: number | null; validity?: {kind: string; valid_from?: string | null; valid_until?: string | null; reason?: string | null}};
type Expression = {operator: string; text: string; operands?: Expression[]};
type Condition = {condition_id: string; original_text: string; hard_requirement: boolean; package_scope: {kind: string; package_ids: string[]; reason?: string | null}; expression: Expression; validity?: {kind: string; required_at?: string | null; required_until?: string | null; reason?: string | null}; notice_evidence: Evidence[]};
type Judgment = {condition_id: string; status: string; reason: string; notice_evidence: Evidence[]; company_evidence: Evidence[]; missing_items: string[]; conflicting_evidence_ids?: string[]};
type Decision = 'confirm' | 'reject' | 'request_materials';
type Draft = {decision: '' | Decision; reason: string; correcting?: string};
type Page<T> = {items: T[]; total: number; offset: number; limit: number};
type ReviewStatus = {enabled: boolean; configured: boolean; worker_available: boolean};
type Event = {event_id: string; event_kind: string; revision_id?: string | null; assessment_id?: string | null; condition_id?: string | null; actor_id: string; occurred_at: string | number; review_version: number; supersedes_event_id?: string | null; reason: string; review_event?: {command: {decision: Decision; reason: string; expected_result_revision: string; idempotency_key: string}; actor: {principal_id: string}; origin: string}};
type ReviewItem = {revision_id: string; assessment_id: string; condition_id: string; condition: Condition; agent_assessment: Judgment; latest_decision: Decision | null; latest_event: Event | null; effective: boolean; ineffective_reasons: string[]};
type Result = {run_id: string; result_revision: string; human_review_state: string; state: string; groups: {primary_notice_revision: VersionRef; assessment: {assessment_id: string; target_package_id: string | null; conditions: Condition[]; assessments: Judgment[]}}[]; inventory: {unresolved?: {document_id: string; reason: string}[]; missing_attachment_ids?: string[]}; missing_items: string[]; provenance: Record<string, unknown>};
type Manifest = {project_id: string; as_of: string; profile_revision: VersionRef; target_package_ids: string[]; material_revisions: VersionRef[]; notice_revisions: VersionRef[]; [key: string]: unknown};
type Revision = {revision_id: string; parent_revision_id: string | null; run_id: string; result_id: string; result_revision: string; inputs_manifest_sha256: string; created_at: string | number; manifest: Manifest; result: Result; provenance: Record<string, unknown>; source_state: SourceState; is_head: boolean};
type Successor = {run_id: string; parent_revision_id: string; trigger_event_id: string; link_event_id: string; inputs_manifest_sha256: string; created_at: string | number};
type CaseRow = {case_id: string; owner_id: string; head_revision_id: string; review_version: number; status: string; source_state: SourceState; allowed_actions: string[]};
type CandidateBatch = {result_id: string; result: {items: {unit_id: string; title: string; disposition: string; reasons: string[]; missing_fields?: string[]}[]; counts: {total_units: number; candidate: number; excluded: number; needs_verification: number}; filters?: Record<string, unknown>; as_of?: string; profile_revision?: VersionRef}; [key: string]: unknown};
type ReviewCase = CaseRow & {assignments: string[]; candidate_batch: CandidateBatch | null; revisions: Revision[]; items: ReviewItem[]; events: Event[]; successors: Successor[]; delivery?: {pending_count: number; frozen_count: number; worker_available: boolean; items: {event_id: string; kind: string; last_error_code: string | null}[]}};
type ProfileRow = {object_id: string; head_version: string};
type Profile = VersionRef & {payload: {display_name: string}};
type Material = VersionRef & {filename: string};

const LABELS: Record<string, string> = {satisfied: '满足', not_satisfied: '不满足', information_insufficient: '信息不足', evidence_conflict: '证据冲突', not_applicable: '不适用', confirm: '确认原判断', reject: '驳回原判断', request_materials: '要求补充材料', open: '正在复核', awaiting_materials: '等待补充材料', recomputing: '补材料后重新计算中', closed: '复核已关闭', unreviewed: '尚未人工复核'};
const STALE: Record<string, string> = {profile_head_changed: '企业画像已有新版本', material_head_changed: '证明材料已有新版本', notice_versions_changed: '公告版本已有变化', notice_observation_updated: '公告观察记录已有更新', attachment_versions_changed: '公告附件已有变化', public_sources_stale: '公告来源新鲜度不足', review_epoch_changed: '恢复或再授权后需重新审核', new_revision: '已由新的计算修订替代', source_changed: '来源依据已经变化', successor_pending: '补材料计算尚未接入', restore_quarantine: '恢复后的案件处于隔离状态'};
const ERRORS: Record<string, string> = {review_disabled: '人工复核功能尚未开启。', reviews_disabled: '人工复核功能尚未开启。', review_version_conflict: '复核版本已变化，可能有其他审核人先提交；请刷新后重新核对，原填写内容保留。', review_state_conflict: '复核版本已变化，可能有其他审核人先提交；请刷新后重新核对，原填写内容保留。', job_state_conflict: '关联任务状态已变化，请刷新后重新核对。', review_not_found: '当前复核不可访问。', review_backend_unavailable: '持久任务后端暂时不可用，已保存的人工事件仍待配送。', job_backend_unavailable: '计算 worker 暂时不可用。', review_job_version_incompatible: '原任务运行代码或依赖版本已变化，需要按当前版本另建计算。', review_successor_not_ready: '关联的新计算尚未就绪，请先检查任务结果。', current_identity_required: '身份已到期或账户已停用，请重新统一登录。'};
const label = (value: string) => LABELS[value] || value;
const readable = (value?: string | number | null) => value == null ? '尚未记录' : new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN');
const refOnly = (value: VersionRef): VersionRef => ({object_id: value.object_id, version_id: value.version_id, sha256: value.sha256});
const itemKey = (value: {assessment_id: string; condition_id: string}) => `${value.assessment_id}:${value.condition_id}`;
function failureText(value: unknown) {return value instanceof ApiError ? ERRORS[value.code] || `服务端拒绝本次操作（${value.status}，${value.code}）。` : '请求应答未确认，请先刷新核对；重试同一操作会保留原幂等键。';}
function Logic({value}: {value: Expression}) {
  const operators: Record<string, string> = {atom: '单项要求', all: '全部成立（AND）', any: '至少一项成立（OR）', not: '否定（NOT）', unless: '基础要求及例外（UNLESS）'};
  return <div className="reviews-agent"><strong>{operators[value.operator] || value.operator}</strong><p>{value.text}</p>{value.operands?.map((child, index) => <Logic key={index} value={child} />)}</div>;
}
function CitationList({title, items}: {title: string; items: Evidence[]}) {
  return <div><h4>{title}</h4>{!items.length && <p className="reviews-warning">尚无可核验引用，不能据此认定满足。</p>}{items.map((item, index) => <details key={`${item.evidence_id}-${index}`}><summary>{item.evidence_id} · {item.object_kind === 'attachment' ? '公告附件' : item.object_kind === 'company_material' ? '企业证明材料' : item.object_kind === 'company_fact' ? '企业画像事实' : '固定证据'}</summary><blockquote>{item.quote}</blockquote><dl><dt>来源对象</dt><dd><code>{item.version.object_id}</code></dd><dt>固定版本</dt><dd><code>{item.version.version_id}</code></dd><dt>版本 SHA-256</dt><dd><code>{item.version.sha256}</code></dd><dt>文本 SHA-256</dt><dd><code>{item.text_sha256 || '未提供'}</code></dd><dt>原文定位</dt><dd>{item.locator} · 字符 [{item.start}, {item.end})</dd>{item.validity && <><dt>证明有效期</dt><dd>{item.validity.kind === 'interval' ? `${readable(item.validity.valid_from)} — ${readable(item.validity.valid_until)}` : item.validity.kind === 'perpetual' ? '长期有效声明' : item.validity.kind === 'not_required' ? '未要求' : '待核实'}{item.validity.reason && ` · ${item.validity.reason}`}</dd></>}</dl><NoticeEvidenceLocation evidence={item} /></details>)}</div>;
}
function AgentJudgment({condition, judgment}: {condition: Condition; judgment: Judgment}) {
  return <><div className="reviews-agent"><div className="reviews-row"><strong>Agent 原判断</strong><span className={`reviews-badge ${judgment.status}`}>{label(judgment.status)}</span></div><p>{judgment.reason}</p><p className="reviews-help">此判断保持原样，人工决定另行追加记录。</p></div><p className="reviews-help">{condition.hard_requirement ? '硬性要求' : '一般要求'} · {condition.package_scope.kind === 'all' ? '全部采购包' : condition.package_scope.kind === 'specific' ? `采购包 ${condition.package_scope.package_ids.join('、')}` : '适用范围待核实'}{condition.package_scope.reason && ` · ${condition.package_scope.reason}`}</p>{!!judgment.missing_items?.length && <p className="reviews-warning">缺失材料：{judgment.missing_items.join('；')}</p>}{!!judgment.conflicting_evidence_ids?.length && <p className="reviews-warning">冲突证据：{judgment.conflicting_evidence_ids.join('、')}</p>}{condition.validity && condition.validity.kind !== 'not_required' && <p>条件有效时间：{condition.validity.kind === 'unknown' ? `待核实 · ${condition.validity.reason || '未提供完整时间依据'}` : `${readable(condition.validity.required_at)}${condition.validity.required_until ? ` — ${readable(condition.validity.required_until)}` : ''}`}</p>}<details><summary>条件逻辑、否定与例外</summary><Logic value={condition.expression} /></details><div className="reviews-evidence"><CitationList title="公告要求依据" items={judgment.notice_evidence || condition.notice_evidence || []} /><CitationList title="企业证明依据" items={judgment.company_evidence || []} /></div></>;
}

function EventCard({event}: {event: Event}) {
  return <article className="reviews-event"><strong>{event.review_event ? label(event.review_event.command.decision) : event.event_kind}</strong><p>{event.reason || event.review_event?.command.reason}</p><p className="reviews-help">审核人 <code>{event.actor_id}</code> · {readable(event.occurred_at)} · 复核版本 {event.review_version}</p><p className="reviews-help">事件 <code>{event.event_id}</code>{event.supersedes_event_id && <> · 更正原事件 <code>{event.supersedes_event_id}</code></>}</p>{event.review_event && <p className="reviews-help">固定结果修订 <code>{event.review_event.command.expected_result_revision}</code> · 身份来源 {event.review_event.origin === 'server_authenticated' ? '服务端认证人员' : event.review_event.origin}</p>}</article>;
}
function CandidateSummary({batch}: {batch: CandidateBatch}) {
  const names: Record<string, string> = {candidate: '候选', excluded: '已排除', needs_verification: '待核实'};
  const reasons: Record<string, string> = {keyword_mismatch: '与关键词不匹配', below_min_budget: '预算低于下限', above_max_budget: '预算高于上限', budget_unknown: '预算尚未确认', project_status_unknown: '项目状态尚未确认', profile_facts_missing: '画像事实不足'};
  return <section><h3>固定候选与未纳入依据</h3><p>批次 <code>{batch.result_id}</code> · 全批 {batch.result.counts.total_units} · 候选 {batch.result.counts.candidate} · 已排除 {batch.result.counts.excluded} · 待核实 {batch.result.counts.needs_verification}</p><ul>{batch.result.items.map(item => <li key={item.unit_id}><strong>{item.title || item.unit_id}</strong> · {names[item.disposition] || item.disposition}{item.reasons.length ? ` · ${item.reasons.map(value => reasons[value] || value).join('；')}` : ''}{!!item.missing_fields?.length && ` · 缺失：${item.missing_fields.join('、')}`}</li>)}</ul><details><summary>候选固定筛选、版本与批次依据</summary><pre>{JSON.stringify(batch, null, 2)}</pre></details></section>;
}

export function Reviews({identity}: {identity: Identity}) {
  const [status, setStatus] = useState<ReviewStatus | null>(null);
  const [history, setHistory] = useState<Page<CaseRow> | null>(null);
  const [review, setReview] = useState<ReviewCase | null>(null);
  const [lookup, setLookup] = useState('');
  const [runId, setRunId] = useState('');
  const [batchId, setBatchId] = useState('');
  const [reviewerIds, setReviewerIds] = useState('');
  const [assignmentReason, setAssignmentReason] = useState('');
  const [selectedRevision, setSelectedRevision] = useState('');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [profiles, setProfiles] = useState<Page<ProfileRow> | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [materials, setMaterials] = useState<Page<Material> | null>(null);
  const [selectedMaterials, setSelectedMaterials] = useState<Material[]>([]);
  const [projectId, setProjectId] = useState('');
  const [packages, setPackages] = useState('');
  const [asOf, setAsOf] = useState('');
  const [trigger, setTrigger] = useState('');
  const [successorReason, setSuccessorReason] = useState('');
  const [attachId, setAttachId] = useState('');
  const [attachReason, setAttachReason] = useState('');
  const [closeReason, setCloseReason] = useState('');
  const [restoreVersion, setRestoreVersion] = useState('');
  const [restoreReason, setRestoreReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const lifetime = useRef(0), sequence = useRef(0), applied = useRef(0);
  const activeId = useRef(''), latest = useRef<ReviewCase | null>(null);
  const manualController = useRef<AbortController | null>(null), mutationController = useRef<AbortController | null>(null);
  const mutationBusy = useRef(false);
  const keys = useRef(new Map<string, string>());
  const canManage = identity.permissions.includes('agent:use');
  const canDecide = identity.permissions.includes('review:decide');
  const headers = {'Content-Type': 'application/json', 'X-CSRF-Token': identity.csrf_token || ''};
  const split = (value: string) => Array.from(new Set(value.split(/[\s,，]+/).filter(Boolean)));
  const allowed = (action: string) => !!review?.allowed_actions.includes(action);
  function clearPrivate() {
    lifetime.current++; manualController.current?.abort(); mutationController.current?.abort();
    activeId.current = ''; latest.current = null; applied.current = 0; keys.current.clear(); mutationBusy.current = false;
    setReview(null); setHistory(null); setLookup(''); setRunId(''); setBatchId(''); setReviewerIds(''); setAssignmentReason(''); setSelectedRevision(''); setDrafts({});
    setProfiles(null); setProfile(null); setMaterials(null); setSelectedMaterials([]); setProjectId(''); setPackages(''); setAsOf(''); setTrigger(''); setSuccessorReason(''); setAttachId(''); setAttachReason(''); setCloseReason(''); setRestoreVersion(''); setRestoreReason(''); setBusy(false); setLoading(false); setNotice('');
  }
  function failed(value: unknown) {
    if (value instanceof ApiError && [401, 403, 404].includes(value.status)) clearPrivate();
    setError(value instanceof ApiError && value.status === 409 ? `${failureText(value)} 请刷新复核版本后再决定；系统没有自动重试。` : failureText(value));
  }
  function apply(value: ReviewCase, order: number, focus = false) {
    if (focus && activeId.current !== value.case_id) {activeId.current = value.case_id; latest.current = null; applied.current = 0; setDrafts({}); setSelectedRevision(value.head_revision_id);}
    if (activeId.current !== value.case_id) return;
    if (latest.current && (value.review_version < latest.current.review_version || (value.review_version === latest.current.review_version && order < applied.current))) return;
    if (latest.current && latest.current.head_revision_id !== value.head_revision_id) {setDrafts({}); setSelectedRevision(value.head_revision_id); setTrigger('');}
    latest.current = value; applied.current = Math.max(applied.current, order); setReview(value); setLookup(value.case_id); setReviewerIds(value.assignments.join('\n'));
  }
  async function manual(action: (signal: AbortSignal, valid: () => boolean, order: number) => Promise<void>) {
    manualController.current?.abort(); const controller = new AbortController(); manualController.current = controller;
    const epoch = lifetime.current, order = ++sequence.current, valid = () => lifetime.current === epoch && !controller.signal.aborted;
    setLoading(true); setError('');
    try {await action(controller.signal, valid, order);} catch (value) {if (valid()) failed(value);} finally {if (valid()) setLoading(false);}
  }
  async function loadPage(offset = history?.offset || 0) {await manual(async (signal, valid) => {const value = await get<Page<CaseRow>>(`/reviews/cases?offset=${offset}&limit=20`, signal); if (valid()) setHistory(value);});}
  async function openCase(id: string) {
    if (!id.trim() || mutationBusy.current) return;
    const selected = id.trim(); activeId.current = selected; latest.current = null; applied.current = 0; setReview(null); setDrafts({}); setNotice(''); setProfile(null); setMaterials(null); setSelectedMaterials([]);
    await manual(async (signal, valid, order) => {const value = await get<ReviewCase>(`/reviews/cases/${encodeURIComponent(selected)}`, signal); if (valid()) {setSelectedRevision(value.head_revision_id); apply(value, order);}});
  }
  async function refresh() {await manual(async (signal, valid, order) => {
    const selected = activeId.current;
    const runtime = await get<ReviewStatus>('/reviews/status', signal);
    if (!valid()) return;
    if (!runtime.enabled || !runtime.configured) {clearPrivate(); setStatus(runtime); return;}
    const [page, value] = await Promise.all([get<Page<CaseRow>>(`/reviews/cases?offset=${history?.offset || 0}&limit=20`, signal), selected ? get<ReviewCase>(`/reviews/cases/${encodeURIComponent(selected)}`, signal) : null]);
    if (profile) await get(`/company/profiles/${profile.object_id}?version=${profile.version_id}`, signal);
    for (const material of selectedMaterials) await get(`/company/materials/${material.object_id}?version=${material.version_id}`, signal);
    if (valid()) {setStatus(runtime); setHistory(page); if (value) apply(value, order);}
  });}
  async function mutate(path: string, payload: Record<string, unknown>, success: string, onSuccess?: (value: ReviewCase) => void) {
    if (mutationBusy.current) return;
    mutationBusy.current = true; const controller = new AbortController(); mutationController.current = controller;
    const epoch = lifetime.current, order = ++sequence.current, valid = () => epoch === lifetime.current && !controller.signal.aborted;
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await request<ReviewCase>(path, {method: 'POST', headers, body: JSON.stringify(payload), signal: controller.signal});
      if (valid()) {apply(value, order, true); setNotice(success); onSuccess?.(value);}
    } catch (value) {if (valid()) failed(value);} finally {if (valid()) {mutationBusy.current = false; setBusy(false);}}
  }
  function idempotency(operation: string, payload: unknown) {
    const fingerprint = JSON.stringify([operation, payload]); let value = keys.current.get(fingerprint);
    if (!value) {value = crypto.randomUUID(); keys.current.set(fingerprint, value);} return value;
  }
  function create(event: FormEvent) {
    event.preventDefault(); const body = {run_id: runId.trim(), candidate_batch_id: batchId.trim(), reviewer_ids: split(reviewerIds)};
    void mutate('/reviews/cases', {...body, idempotency_key: idempotency('create', body)}, '服务器已保存复核案件及指定审核人。');
  }
  function decide(item: ReviewItem) {
    if (!review) return; const draft = drafts[itemKey(item)]; if (!draft?.decision || !draft.reason.trim()) return;
    const command = {decision: draft.decision, expected_result_revision: head!.result_revision, reason: draft.reason.trim()};
    const body = {revision_id: item.revision_id, assessment_id: item.assessment_id, condition_id: item.condition_id, expected_version: review.review_version, command: {...command, idempotency_key: idempotency('decide', {case_id: review.case_id, item: itemKey(item), expected_version: review.review_version, command, supersedes: draft.correcting || null})}, supersedes_event_id: draft.correcting || null};
    void mutate(`/reviews/cases/${review.case_id}/decisions`, body, draft.correcting ? '服务器已追加更正事件，原事件仍然保留。' : '服务器已保存该条人工决定；Agent 原判断保持原样。', () => setDrafts(value => {const copy = {...value}; delete copy[itemKey(item)]; return copy;}));
  }
  function management(action: string, reason: string, extra: Record<string, unknown> = {}, success = '服务器已保存本次操作。') {
    if (!review || !reason.trim()) return;
    const body = {expected_version: review.review_version, reason: reason.trim(), ...extra};
    void mutate(`/reviews/cases/${review.case_id}/${action}`, {...body, idempotency_key: idempotency(action, {case_id: review.case_id, ...body})}, success);
  }
  async function companyPage(kind: 'profiles' | 'materials', offset = 0) {await manual(async (signal, valid) => {
    if (kind === 'profiles') {const value = await get<Page<ProfileRow>>(`/company/profiles?offset=${offset}&limit=20`, signal); if (valid()) setProfiles(value);}
    else {const value = await get<Page<Material>>(`/company/materials?offset=${offset}&limit=20`, signal); if (valid()) setMaterials(value);}
  });}
  async function chooseProfile(value: ProfileRow) {await manual(async (signal, valid) => {const fixed = await get<Profile>(`/company/profiles/${value.object_id}?version=${value.head_version}`, signal); if (valid()) {setProfile(fixed); setMaterials(null); setSelectedMaterials([]);}});}
  async function successor(event: FormEvent) {
    event.preventDefault(); if (!review || !head || !profile || !trigger || !successorReason.trim()) return;
    const fixed: Record<string, unknown> = {profile_revision: refOnly(profile), project_id: projectId.trim(), target_package_ids: split(packages), additional_material_revisions: selectedMaterials.map(refOnly)};
    if (asOf) fixed.as_of = new Date(asOf).toISOString();
    management('successors', successorReason, {revision_id: head.revision_id, trigger_event_id: trigger, request: fixed}, '服务器已关联补材料的新任务；等待计算完成后显式接入新修订。');
  }
  async function exportCase() {if (!review) return; await manual(async (signal, valid) => {
    const value = await get<Record<string, unknown>>(`/reviews/cases/${review.case_id}/export`, signal);
    if (!valid()) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'})); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `bidops-review-${review.case_id}.json`; anchor.click(); URL.revokeObjectURL(url); setNotice('服务器已重新检查完整来源权限并生成 JSON 审核包。');
  });}
  useEffect(() => {
    void manual(async (signal, valid) => {const value = await get<ReviewStatus>('/reviews/status', signal); if (!valid()) return; setStatus(value); if (value.enabled && value.configured) {const page = await get<Page<CaseRow>>('/reviews/cases?offset=0&limit=20', signal); if (valid()) setHistory(page);}});
    return () => {lifetime.current++; manualController.current?.abort(); mutationController.current?.abort();};
  }, []);
  const head = review?.revisions.find(value => value.revision_id === review.head_revision_id);
  const revision = review?.revisions.find(value => value.revision_id === selectedRevision) || head;
  const isHead = !!revision && revision.revision_id === review?.head_revision_id;
  const owner = !!review && identity.user?.subject_id === review.owner_id;
  const closable = !!review?.source_state.current && !!review.items.length && review.items.every(item => item.effective && ['confirm', 'reject'].includes(item.latest_decision || ''));
  const updateDraft = (key: string, patch: Partial<Draft>) => setDrafts(value => ({...value, [key]: {...(value[key] || {decision: '', reason: ''}), ...patch}}));
  const pagination = (page: Page<unknown>, change: (offset: number) => void) => <div className="reviews-pagination"><button type="button" disabled={busy || loading || page.offset === 0} onClick={() => change(Math.max(0, page.offset - page.limit))}>上一页</button><span>共 {page.total} 项 · {page.total ? page.offset + 1 : 0}–{Math.min(page.offset + page.limit, page.total)}</span><button type="button" disabled={busy || loading || page.offset + page.limit >= page.total} onClick={() => change(page.offset + page.limit)}>下一页</button></div>;

  return <NoticeLocationContext.Provider value={{csrfToken: identity.csrf_token, onAccessDenied: failed}}><main className="reviews-panel"><div className="agent-heading"><div><p className="eyebrow">人工决定与 Agent 判断分别保存</p><h1>逐项人工复核</h1><p>审核决定用于内部是否继续跟进；不会代替正式资质认定或外部采购提交。</p></div>{status?.enabled && status.configured && <button disabled={busy || loading} onClick={() => void refresh()}>刷新复核工作区</button>}</div>
    {error && <p className="reviews-error" role="alert">{error}</p>}{notice && <p className="reviews-notice" role="status">{notice}</p>}{(busy || loading) && <p className="reviews-help" role="status">正在核对服务端状态…</p>}
    {!status && !error && <p role="status">正在确认人工复核配置…</p>}
    {status && (!status.enabled || !status.configured) && <section className="reviews-card"><h2>{!status.enabled ? '人工复核尚未开启' : '人工复核运行时尚未配置'}</h2><p>默认关闭，页面不会提交审核、创建任务或调用模型。</p></section>}
    {status?.enabled && status.configured && <>
      {!status.worker_available && <p className="reviews-warning">计算 worker 当前不可用。现有复核仍可按权限读取和保存人工决定；不能创建补材料计算任务。</p>}
      <div className="reviews-columns"><section className="reviews-card"><h2>从固定候选与等待审核任务建立复核</h2><p className="reviews-help">使用已有 waiting_review 任务和固定候选批次。审核人 assignment 不会授予画像或材料权限，owner 需另行明确授权全部来源。</p><form onSubmit={create}><label>等待审核的任务编号<input required value={runId} onChange={event => setRunId(event.target.value)} /></label><label>固定候选批次编号<input required value={batchId} onChange={event => setBatchId(event.target.value)} /></label><label>指定审核人主体编号（逗号或换行分隔）<textarea value={reviewerIds} onChange={event => setReviewerIds(event.target.value)} /></label><button className="reviews-primary" disabled={busy || loading || !canManage || !runId.trim() || !batchId.trim()}>创建复核案件</button></form>{!canManage && <p className="reviews-help">当前角色可按授权复核，不能创建或管理他人的案件。</p>}</section>
      <section className="reviews-card"><h2>读取已有复核</h2><form onSubmit={event => {event.preventDefault(); void openCase(lookup);}}><label>已有复核编号<input value={lookup} onChange={event => setLookup(event.target.value)} required /></label><button disabled={busy || loading || !lookup.trim()}>读取复核</button></form><ul className="reviews-history">{history?.items.map(value => <li key={value.case_id}><button disabled={busy || loading} onClick={() => void openCase(value.case_id)}>{value.case_id}</button><p>{label(value.status)} · 复核版本 {value.review_version}{value.source_state?.current === false && ' · 来源已变化'}</p></li>)}</ul>{history && pagination(history, offset => void loadPage(offset))}<details><summary>恢复后 owner 显式解除案件隔离</summary><p className="reviews-help">先核对最新身份、来源及底层任务授权。此操作只恢复案件可见性，不恢复旧批准或开始计算；需要明确案件编号和已知版本。</p><label>恢复案件的已知复核版本<input type="number" min="0" step="1" value={restoreVersion} onChange={event => setRestoreVersion(event.target.value)} /></label><label>案件再授权理由<textarea value={restoreReason} onChange={event => setRestoreReason(event.target.value)} /></label><button disabled={busy || loading || !canManage || !lookup.trim() || !restoreReason.trim() || !/^\d+$/.test(restoreVersion)} onClick={() => {const body = {expected_version: Number(restoreVersion), reason: restoreReason.trim()}; void mutate(`/reviews/cases/${encodeURIComponent(lookup.trim())}/reauthorize`, {...body, idempotency_key: idempotency('reauthorize', {case_id: lookup.trim(), ...body})}, '服务器已校验并解除案件隔离；旧决定是否有效仍以当前投影为准。');}}>请求案件再授权</button></details></section></div>
      {review && !revision && <section className="reviews-card reviews-detail"><h2>隔离中的复核案件 <code className="reviews-case-id">{review.case_id}</code></h2><p>已知复核版本 <strong className="reviews-version">{review.review_version}</strong></p><p className="reviews-warning">案件恢复隔离尚未解除，未加载任何材料、历史决定或引用。owner 可使用上方明确编号和版本再授权；旧 assignment 不自动恢复可见性。</p></section>}
      {review && revision && <section className="reviews-card reviews-detail"><div className="reviews-row"><h2>复核案件 <code className="reviews-case-id">{review.case_id}</code></h2><strong className="reviews-badge reviews-case-status">{label(review.status)}</strong></div><p>复核版本 <strong className="reviews-version">{review.review_version}</strong> · owner <code>{review.owner_id}</code></p><p>当前指定审核人：{review.assignments.length ? review.assignments.map(value => <code key={value}>{value} </code>) : '尚未指定'}</p>{review.source_state.current === false && <p className="reviews-warning">来源或输入已有变化，旧人工决定不能作为当前有效依据：{review.source_state.stale_reasons.map(value => STALE[value] || value).join('；')}。</p>}{!!review.delivery?.pending_count && <p className="reviews-warning reviews-delivery">人工事件已保存，仍有 {review.delivery.pending_count} 项等待持久任务消费。{review.delivery.worker_available ? '请刷新确认配送状态。' : '计算 worker 当前不可用，配送保持待处理。'}这不表示关联任务已完成。</p>}{!!review.delivery?.frozen_count && <p className="reviews-warning">有 {review.delivery.frozen_count} 项旧事件已冻结，不能用于继续执行或恢复旧批准。</p>}<details><summary>人工事件配送记录</summary>{review.delivery ? review.delivery.items.map(value => <p key={value.event_id}>事件 <code>{value.event_id}</code> · {value.kind}{value.last_error_code && ` · 暂未完成：${ERRORS[value.last_error_code] || value.last_error_code}`}</p>) : <p>尚无配送记录。</p>}</details>
        <label>查看固定修订<select value={revision.revision_id} onChange={event => setSelectedRevision(event.target.value)}>{review.revisions.map(value => <option key={value.revision_id} value={value.revision_id}>{value.is_head ? '当前' : '历史'} · {value.revision_id} · {value.run_id}</option>)}</select></label>{!isHead && <p className="reviews-warning">正在查看历史修订及当时决定，不能在旧修订提交新的审核；当前任务与输入未被覆盖。</p>}
        <div className="reviews-pinned"><h3>本修订的固定依据</h3><p>持久任务 <code>{revision.run_id}</code> · 结果 <code>{revision.result_id}</code> · 结果修订 <code>{revision.result_revision}</code></p><p>输入 SHA-256 <code>{revision.inputs_manifest_sha256}</code></p><p>Agent 人审状态：<strong>{revision.result.human_review_state === 'unreviewed' ? 'unreviewed（原始 Agent 结果尚未人审，保持不变）' : revision.result.human_review_state}</strong></p><p>核对时间 {readable(revision.manifest.as_of)} · 项目 <code>{revision.manifest.project_id}</code></p>{revision.source_state.current === false && <p className="reviews-warning">该修订来源已陈旧：{revision.source_state.stale_reasons.map(value => STALE[value] || value).join('；')}。</p>}<details><summary>画像、公告、证明版本与运行依据</summary><pre>{JSON.stringify({manifest: revision.manifest, provenance: revision.provenance}, null, 2)}</pre></details></div>
        {!!revision.result.missing_items?.length && <p className="reviews-warning">清单缺失项：{revision.result.missing_items.join('；')}</p>}{revision.result.inventory.unresolved?.map((value, index) => <p className="reviews-warning" key={index}>未解析来源 {value.document_id}：{value.reason}</p>)}{!!revision.result.inventory.missing_attachment_ids?.length && <p className="reviews-warning">缺少附件：{revision.result.inventory.missing_attachment_ids.join('、')}</p>}
        {isHead ? review.items.map(item => {const key = itemKey(item), draft = drafts[key] || {decision: '', reason: ''}; const writable = canDecide && allowed('decide') && (!item.latest_event || !!draft.correcting); return <article className="reviews-condition" key={key}><h3>{item.condition.original_text}</h3><p className="reviews-help">assessment <code>{item.assessment_id}</code> · 条件 <code>{item.condition_id}</code></p><AgentJudgment condition={item.condition} judgment={item.agent_assessment} /><div className="reviews-decision"><h4>人工复核投影</h4><p className="reviews-human-decision">{item.latest_decision ? label(item.latest_decision) : '尚未人工决定'} · {item.effective ? '当前有效' : item.latest_decision ? '当前无效，不作为有效决定' : '未形成有效决定'}</p>{!!item.ineffective_reasons.length && <p className="reviews-warning">失效依据：{item.ineffective_reasons.map(value => STALE[value] || value).join('；')}</p>}{item.latest_event && <><EventCard event={item.latest_event} />{canDecide && allowed('decide') && !draft.correcting && <button type="button" disabled={busy || loading} onClick={() => updateDraft(key, {decision: item.latest_decision || '', reason: '', correcting: item.latest_event!.event_id})}>追加更正该条理由与决定</button>}</>}{canDecide && allowed('decide') && <fieldset disabled={busy || loading || !writable}><legend>{draft.correcting ? `追加更正 · 原事件 ${draft.correcting}` : '独立填写该条决定与理由'}</legend><label>该条人工决定<select value={draft.decision} onChange={event => updateDraft(key, {decision: event.target.value as '' | Decision})}><option value="">请选择，未默认批准</option><option value="confirm">确认原判断</option><option value="reject">驳回原判断</option><option value="request_materials">要求补充材料</option></select></label><label>该条独立审核理由<textarea value={draft.reason} onChange={event => updateDraft(key, {reason: event.target.value})} /></label><button className="reviews-primary" type="button" disabled={!draft.decision || !draft.reason.trim()} onClick={() => decide(item)}>提交该条人工决定</button></fieldset>}</div><details><summary>该条全部事件（保留原理由）</summary>{review.events.filter(value => value.revision_id === item.revision_id && value.assessment_id === item.assessment_id && value.condition_id === item.condition_id).map(value => <EventCard key={value.event_id} event={value} />)}</details></article>;}) : revision.result.groups.flatMap(group => group.assessment.conditions.map(condition => {const judgment = group.assessment.assessments.find(value => value.condition_id === condition.condition_id); return judgment ? <article className="reviews-condition" key={`${group.assessment.assessment_id}:${condition.condition_id}`}><h3>{condition.original_text}</h3><AgentJudgment condition={condition} judgment={judgment} /><h4>本历史修订的人工事件</h4>{review.events.filter(value => value.revision_id === revision.revision_id && value.assessment_id === group.assessment.assessment_id && value.condition_id === condition.condition_id).map(value => <EventCard key={value.event_id} event={value} />)}</article> : null;}))}
        {!canDecide && <p className="reviews-help">逐项决定要求 review:decide 角色和明确 assignment，管理员角色本身不授予审批权。</p>}
        {review.candidate_batch && <CandidateSummary batch={review.candidate_batch} />}
        <details><summary>案件完整追加事件</summary>{review.events.map(value => <EventCard key={value.event_id} event={value} />)}</details>
        {owner && canManage && isHead && <><section className="reviews-condition"><h3>owner 设置审核人</h3><p className="reviews-help">仅更新 assignment，不授予材料或结果读取权限。</p><label>更新指定审核人主体编号<textarea value={reviewerIds} onChange={event => setReviewerIds(event.target.value)} /></label><label>审核人调整理由<textarea value={assignmentReason} onChange={event => setAssignmentReason(event.target.value)} /></label><button disabled={busy || loading || !allowed('assign') || !assignmentReason.trim()} onClick={() => management('assignments', assignmentReason, {reviewer_ids: split(reviewerIds)}, '服务器已保存审核人调整，材料 ACL 保持由对象授权管理。')}>保存审核人设置</button></section>
        <section className="reviews-condition"><h3>补材料并创建关联的新计算</h3><p>先在“企业画像与材料”上传新材料或更新画像，再选择实际保存的固定版本。新任务会全量重新计算，所有新条目必须重新人工审核。</p><form onSubmit={event => void successor(event)}><label>触发补材料的人工事件<select value={trigger} onChange={event => setTrigger(event.target.value)}><option value="">请选择对应的补材料事件</option>{review.events.filter(value => value.revision_id === review.head_revision_id && value.review_event?.command.decision === 'request_materials').map(value => <option key={value.event_id} value={value.event_id}>{value.condition_id} · {value.reason} · {value.event_id}</option>)}</select></label><button type="button" disabled={busy || loading || !allowed('successor')} onClick={() => void companyPage('profiles')}>选择新计算的画像版本</button>{profiles && <><ul className="reviews-history">{profiles.items.map(value => <li key={value.object_id}><button type="button" disabled={busy || loading} onClick={() => void chooseProfile(value)}>选择画像 {value.object_id.slice(0, 12)}</button></li>)}</ul>{pagination(profiles, offset => void companyPage('profiles', offset))}</>}{profile && <div className="reviews-pinned"><h4>{profile.payload.display_name}</h4><p>固定版本 <code>{profile.version_id}</code> · SHA-256 <code>{profile.sha256}</code></p></div>}<button type="button" disabled={busy || loading || !allowed('successor')} onClick={() => void companyPage('materials')}>选择新增证明材料</button>{materials && <>{materials.items.map(value => <label className="reviews-choice" key={value.version_id}><input type="checkbox" checked={selectedMaterials.some(material => material.version_id === value.version_id)} onChange={event => setSelectedMaterials(current => event.target.checked ? [...current, value] : current.filter(material => material.version_id !== value.version_id))} /><span>{value.filename} · {value.version_id}</span></label>)}{pagination(materials, offset => void companyPage('materials', offset))}</>}<p className="reviews-help">已选新增证明版本：{selectedMaterials.length ? selectedMaterials.map(value => value.version_id).join('、') : '无；不能据此标记缺失材料已补齐'}</p><label>新计算项目编号<input value={projectId} onChange={event => setProjectId(event.target.value)} required /></label><label>新计算采购包（逗号或空格分隔）<input value={packages} onChange={event => setPackages(event.target.value)} /></label><label>新计算核对时间（可留空，由服务器固定）<input type="datetime-local" value={asOf} onChange={event => setAsOf(event.target.value)} /></label><label>补材料与重算理由<textarea value={successorReason} onChange={event => setSuccessorReason(event.target.value)} required /></label><button className="reviews-primary" disabled={busy || loading || !allowed('successor') || !status.worker_available || !profile || !trigger || !projectId.trim() || !successorReason.trim()}>创建关联的新计算任务</button></form><h4>已关联的新任务</h4>{review.successors.map(value => <p key={value.run_id}>任务 <code>{value.run_id}</code> · 原修订 <code>{value.parent_revision_id}</code> · 触发事件 <code>{value.trigger_event_id}</code></p>)}<label>已计算就绪的新任务编号<input value={attachId} onChange={event => setAttachId(event.target.value)} /></label><label>接入新修订理由<textarea value={attachReason} onChange={event => setAttachReason(event.target.value)} /></label><button disabled={busy || loading || !allowed('attach') || !attachId.trim() || !attachReason.trim()} onClick={() => management('revisions', attachReason, {run_id: attachId.trim()}, '服务器已接入新修订，所有新条目均须重新审核；旧历史保持保存。')}>显式接入已完成的新修订</button></section>
        <section className="reviews-condition"><h3>显式关闭内部复核</h3><p>需要当前来源、所有条目已有有效确认或驳回，且没有待补材料项。关闭仅表示本次内部跟进复核结束。</p><label>关闭理由<textarea value={closeReason} onChange={event => setCloseReason(event.target.value)} /></label><button disabled={busy || loading || !allowed('close') || !closable || !closeReason.trim()} onClick={() => management('close', closeReason, {}, '服务器已关闭本次内部复核；没有执行外部采购提交。')}>关闭本次内部复核</button></section></>}
        <div className="reviews-actions"><button disabled={busy || loading || !allowed('export')} onClick={() => void exportCase()}>导出受控 JSON 审核包</button><span className="reviews-help">每次导出重新检查全部历史来源权限，不导出无权材料。</span></div>
      </section>}
    </>}
  </main></NoticeLocationContext.Provider>;
}
