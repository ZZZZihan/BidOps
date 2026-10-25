import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, get, request } from './api';
import type { Identity } from './Enterprise';
import { NoticeEvidenceLocation, NoticeLocationContext } from './NoticeEvidenceLocation';
import './DemoWorkflow.css';

type VersionRef = {object_id: string; version_id: string; sha256: string};
export type DemoWorkflowContext = {
  kind: 'local_workflow'; label: string; description: string; seeded: boolean;
  profile_revision?: VersionRef; additional_material_revisions?: VersionRef[];
  reviewer_subject_id?: string; company_name?: string; materials?: (VersionRef & {label: string})[];
};
type Prepared = DemoWorkflowContext & {profile_revision: VersionRef; additional_material_revisions: VersionRef[]; reviewer_subject_id: string; company_name: string};
type SourceState = {current: boolean; stale_reasons: string[]};
type Candidate = {unit_id: string; project_id: string | null; package_id: string | null; title: string; package_label?: string | null; budget_yuan?: string | null; disposition: string; reasons: string[]; missing_fields: string[]};
type Batch = {batch_id: string; as_of: string; profile_revision: VersionRef; items: Candidate[]; total: number; offset: number; limit: number; has_more: boolean; counts: {candidate: number; excluded: number; needs_verification: number; total_units: number}; source_state?: SourceState};
type Evidence = {evidence_id: string; side?: string; object_kind?: string; version: VersionRef; quote: string; start: number; end: number; locator: string; text_sha256?: string; validity?: {kind: string; valid_from?: string | null; valid_until?: string | null; reason?: string | null}};
type Condition = {condition_id: string; original_text: string; hard_requirement: boolean; notice_evidence: Evidence[]; expression?: unknown; package_scope?: unknown; validity?: unknown};
type Judgment = {condition_id: string; status: string; reason: string; notice_evidence: Evidence[]; company_evidence: Evidence[]; missing_items: string[]; conflicting_evidence_ids?: string[]};
type Result = {result_revision: string; human_review_state: string; state: string; recommendation?: string; inventory: {conditions?: Condition[]; unresolved: {document_id: string; reason: string}[]; missing_attachment_ids?: string[]}; groups: {assessment: {assessment_id: string; conditions: Condition[]; assessments: Judgment[]}}[]; missing_items: string[]; provenance?: Record<string, unknown>};
type Job = {run_id: string; status: string; state_version: number; created_at?: number | string; updated_at?: number | string; progress?: {phase?: string; completed?: number; total?: number}; source_state?: SourceState; result?: Result | null; error_code?: string | null; usage?: {offline: boolean; estimated_cost_cny: string | number; reserved_cost_cny: string | number; usage_complete?: boolean}; allowed_actions: string[]};
type Decision = 'confirm' | 'reject' | 'request_materials';
type ReviewItem = {revision_id: string; assessment_id: string; condition_id: string; condition: Condition; agent_assessment: Judgment; latest_decision: Decision | null; latest_event?: {event_id: string; reason: string; actor_id: string}; effective: boolean; ineffective_reasons: string[]};
type Review = {case_id: string; head_revision_id: string; review_version: number; status: string; source_state: SourceState; allowed_actions: string[]; items: ReviewItem[]; revisions: {revision_id: string; result_revision: string; result: Result}[]; delivery?: {pending_count: number; frozen_count: number; worker_available: boolean}};
type Runtime = {enabled: boolean; configured: boolean; live_calls_enabled: boolean};
type JobInput = {profile_revision: VersionRef; project_id: string; target_package_ids: string[]; additional_material_revisions: VersionRef[]};
// Store references and operation keys only. Evidence, reasons, credentials and
// server results are always read again after reload, never cached in storage.
type Progress = {profile_version?: string; batch_id?: string; batch_offset?: number; unit_id?: string; run_id?: string; case_id?: string; job_attempt?: {key: string; input: JobInput}; case_key?: string};
type Draft = {decision: '' | Decision; reason: string};

const ACTIVE = new Set(['queued', 'running', 'cancel_requested']);
const LABELS: Record<string, string> = {candidate: '候选', excluded: '已排除', needs_verification: '待核实', satisfied: '满足', not_satisfied: '不满足', information_insufficient: '信息不足', evidence_conflict: '证据冲突', not_applicable: '不适用', queued: '等待执行', running: '正在核对', waiting_review: '等待人工复核', completed: '核对已完成', failed: '核对失败或部分完成', timed_out: '核对超时', cancelled: '核对已取消', cancel_requested: '正在请求取消', interrupted: '核对已中断', confirm: '确认原判断', reject: '驳回原判断', request_materials: '要求补充材料', open: '正在复核', awaiting_materials: '等待补充材料', recomputing: '重新核对中', closed: '复核已关闭', review_recommended: '建议人工复核', needs_materials: '需要补充材料', resolve_conflict: '需要处理证据冲突', do_not_pursue: '建议不继续跟进', status_unknown: '项目状态待核实', not_evaluated: '尚未完成核对'};
const REASONS: Record<string, string> = {keyword_mismatch: '与关键词不匹配', below_min_budget: '预算低于下限', above_max_budget: '预算高于上限', budget_unknown: '预算尚未确认', project_status_unknown: '项目状态尚未确认', missing_project_identity: '项目关联尚未确认', profile_unknown: '企业画像信息不足', profile_facts_missing: '企业画像缺少事实', source_incomplete: '来源材料不完整', missing_attachment: '缺少公告附件', assessment_not_completed: '该条件尚未完成核对', server_reduced_unreviewed: '服务端已汇总，尚未人工复核', provider_timeout: '模型请求超时', provider_unavailable: '模型服务暂时不可用', screening_budget_exhausted: '累计估算预算不足，未完整执行', screening_usage_unconfirmed: '用量尚未确认，费用预留仍保留', screening_budget_review_required: '费用账本尚需核对，付费请求保持关闭', screening_model_failed: '模型步骤未完成，已保存内容仍需核实', screening_request_limit: '达到本次模型请求次数上限', screening_timeout: '资格核对超时', public_sources_stale: '公告为历史保存资料，当前新鲜度不足', profile_head_changed: '企业画像已有新版本', material_head_changed: '企业材料已有新版本', notice_versions_changed: '公告版本已有变化', notice_observation_updated: '公告观察记录已有更新', attachment_versions_changed: '附件已有变化', review_sources_changed: '来源已有变化，请重新核对', review_candidate_binding_mismatch: '筛选批次与核对输入不一致，请重新选择同一企业版本的项目', review_version_conflict: '复核版本已变化，请先刷新后重新核对', review_state_conflict: '复核版本已变化，请先刷新后重新核对', job_idempotency_conflict: '本次提交已关联另一份输入，请刷新核实原任务', current_identity_required: '本机登录已到期，请重新登录后继续', authentication_required: '请重新登录后继续', jobs_not_configured: '资格核对运行时尚未就绪', reviews_disabled: '人工复核尚未开启', source_changed: '来源依据已有变化', new_revision: '已有新的核对修订', review_epoch_changed: '案件恢复后需要重新复核', successor_pending: '补材料计算尚未接入', restore_quarantine: '案件恢复后尚需重新授权'};
const label = (value: string) => LABELS[value] || value;
const reason = (value: string) => value === 'matches_explicit_filters' ? '符合所选筛选条件' : REASONS[value] || value;
const itemKey = (value: ReviewItem) => `${value.revision_id}:${value.assessment_id}:${value.condition_id}`;
const date = (value?: string | number) => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN') : '尚未记录';
const ready = (value: DemoWorkflowContext): value is Prepared => !!value.profile_revision && Array.isArray(value.additional_material_revisions) && !!value.reviewer_subject_id && !!value.company_name;
function failureText(value: unknown) {
  if (value instanceof ApiError) {
    if (value.status === 401) return '本机登录已到期。重新登录后会按保存的引用继续，系统不会重新提交模型任务。';
    if (value.status === 403) return '当前身份或来源权限未通过检查。请重新登录并核实授权，已保存任务不会被重新提交。';
    if (value.status === 404) return '本次资料或任务当前不可访问，请确认仍在使用原数据目录和账户。';
    return REASONS[value.code] || `操作未完成（${value.code}），请刷新服务器状态后核实。`;
  }
  return value instanceof Error && value.message.startsWith('预算') ? value.message : '服务器应答未确认。请刷新核实进度；同一核对任务重试会沿用原提交标识。';
}
function SourceWarning({state}: {state?: SourceState}) {
  return state?.current === false ? <p className="workflow-warning">当前依据需重新核实：{state.stale_reasons.map(reason).join('；')}。保留的历史结果不代表当前仍有效。</p> : null;
}
function EvidenceList({title, items}: {title: string; items: Evidence[]}) {
  return <div className="workflow-evidence"><h4>{title}</h4>{!items.length && <p className="workflow-warning">尚无可核验引用，不能据此认定满足。</p>}{items.map((item, index) => <div key={`${item.evidence_id}-${index}`}><blockquote>{item.quote}</blockquote><details><summary>查看此引用的来源与定位</summary><dl><dt>来源对象 / 固定版本</dt><dd>{item.version.object_id} / {item.version.version_id}</dd><dt>原文位置</dt><dd>{item.locator} · 字符 [{item.start}, {item.end})</dd><dt>版本 SHA-256</dt><dd>{item.version.sha256}</dd>{item.text_sha256 && <><dt>文本 SHA-256</dt><dd>{item.text_sha256}</dd></>}{item.validity && <><dt>材料有效期</dt><dd>{item.validity.kind === 'interval' ? `${date(item.validity.valid_from || undefined)} 至 ${date(item.validity.valid_until || undefined)}` : item.validity.kind === 'unknown' ? '尚待核实' : item.validity.kind === 'perpetual' ? '长期有效声明' : '未要求特定期限'}{item.validity.reason && `；${item.validity.reason}`}</dd></>}</dl><NoticeEvidenceLocation evidence={item} /></details></div>)}</div>;
}
function Assessment({condition, judgment}: {condition: Condition; judgment?: Judgment}) {
  return <><div className="workflow-row"><h3>{condition.original_text}</h3><span className={`workflow-badge ${judgment?.status || 'information_insufficient'}`}>{judgment ? label(judgment.status) : '尚未完成核对'}</span></div><p>{judgment ? reason(judgment.reason) : '已保留条件，尚无完整判断。'}</p>{!!judgment?.missing_items.length && <p className="workflow-warning">缺失材料：{judgment.missing_items.map(reason).join('；')}</p>}{!!judgment?.conflicting_evidence_ids?.length && <p className="workflow-warning">冲突证据：{judgment.conflicting_evidence_ids.join('、')}</p>}<div className="workflow-evidence-grid"><EvidenceList title="公告要求依据" items={judgment?.notice_evidence || condition.notice_evidence} /><EvidenceList title="企业证明依据（虚构示例）" items={judgment?.company_evidence || []} /></div>{(condition.expression || condition.package_scope || condition.validity) && <details><summary>查看条件逻辑、适用范围与期限</summary><pre>{JSON.stringify({expression: condition.expression, package_scope: condition.package_scope, validity: condition.validity}, null, 2)}</pre></details>}</>;
}
function ResultGaps({result}: {result: Result}) {
  return <>{!!result.missing_items.length && <p className="workflow-warning">尚待处理：{result.missing_items.map(reason).join('；')}</p>}{!!result.inventory.unresolved.length && <ul className="workflow-warning">{result.inventory.unresolved.map((item, index) => <li key={index}>未解析来源 {item.document_id}：{reason(item.reason)}</li>)}</ul>}{!!result.inventory.missing_attachment_ids?.length && <p className="workflow-warning">缺少或无法解析的附件：{result.inventory.missing_attachment_ids.join('、')}</p>}</>;
}

export function DemoWorkflow({identity, context: initial}: {identity: Identity; context: DemoWorkflowContext}) {
  const [context, setContext] = useState(initial);
  const [contextConfirmed, setContextConfirmed] = useState(false);
  const [seed, setSeed] = useState<Prepared | null>(null);
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [reviewEnabled, setReviewEnabled] = useState(false);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [keyword, setKeyword] = useState('');
  const [minimum, setMinimum] = useState('');
  const [maximum, setMaximum] = useState('');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [closeReason, setCloseReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [accessBlocked, setAccessBlocked] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const progress = useRef<Progress>({});
  const lifetime = useRef(0), controller = useRef<AbortController | null>(null), mutationBusy = useRef(false);
  const keys = useRef(new Map<string, string>());
  const jobLatest = useRef<Job | null>(null), reviewLatest = useRef<Review | null>(null);
  const storageKey = `bidops.local-workflow.v1:${identity.user?.subject_id || 'local'}`;
  const headers = {'Content-Type': 'application/json', 'X-CSRF-Token': identity.csrf_token || ''};
  const canCreate = identity.permissions.includes('agent:use');
  const canDecide = identity.permissions.includes('review:decide');

  function remember(patch: Partial<Progress>) {
    progress.current = {...progress.current, ...patch};
    try {localStorage.setItem(storageKey, JSON.stringify(progress.current));} catch {setNotice('浏览器无法保存进度引用；请在本页继续，任务仍由服务器保存。');}
  }
  function clearVisible() {
    setSeed(null); setBatch(null); setSelected(null); setJob(null); setReview(null); setDrafts({}); setCloseReason(''); jobLatest.current = null; reviewLatest.current = null;
  }
  function failed(value: unknown) {
    if (value instanceof ApiError && [401, 403, 404].includes(value.status)) {
      lifetime.current++; controller.current?.abort(); controller.current = null; mutationBusy.current = false;
      clearVisible(); setAccessBlocked(true); setBusy(false);
    }
    setError(failureText(value));
  }
  function applyJob(value: Job) {
    if (jobLatest.current?.run_id === value.run_id && jobLatest.current.state_version > value.state_version) return;
    jobLatest.current = value; setJob(value);
  }
  function applyReview(value: Review) {
    if (reviewLatest.current?.case_id === value.case_id && reviewLatest.current.review_version > value.review_version) return;
    if (reviewLatest.current && reviewLatest.current.head_revision_id !== value.head_revision_id) setDrafts({});
    reviewLatest.current = value; setReview(value);
  }
  async function perform(action: (signal: AbortSignal, valid: () => boolean) => Promise<void>) {
    if (mutationBusy.current) return;
    mutationBusy.current = true;
    controller.current?.abort(); const current = new AbortController(); controller.current = current;
    const epoch = lifetime.current, valid = () => !current.signal.aborted && lifetime.current === epoch && controller.current === current;
    setBusy(true); setError(''); setNotice('');
    try {await action(current.signal, valid);} catch (value) {if (valid()) failed(value);} finally {
      // Cleanup belongs to this controller, even if its request was aborted.
      // An older StrictMode setup must never unlock or update a newer request.
      if (controller.current === current) {mutationBusy.current = false; controller.current = null; if (lifetime.current === epoch) setBusy(false);}
    }
  }
  function idempotency(operation: string, body: unknown) {
    const fingerprint = JSON.stringify([operation, body]);
    let key = keys.current.get(fingerprint);
    if (!key) {key = crypto.randomUUID(); keys.current.set(fingerprint, key);} return key;
  }
  async function refresh() {
    await perform(async (signal, valid) => {
      setContextConfirmed(false);
      const next = await get<DemoWorkflowContext>('/demo/workflow/context', signal);
      if (!valid()) return;
      if (next.kind !== 'local_workflow') throw new ApiError(404, 'workflow_unavailable');
      const [jobs, reviews] = await Promise.all([get<Runtime>('/jobs/status', signal), get<{enabled: boolean; configured: boolean}>('/reviews/status', signal)]);
      if (!valid()) return;
      setContext(next); setRuntime(jobs); setReviewEnabled(reviews.enabled && reviews.configured);
      if (!ready(next)) {clearVisible(); setAccessBlocked(false); setContextConfirmed(true); return;}
      if (next.reviewer_subject_id !== identity.user?.subject_id) throw new ApiError(403, 'workflow_identity_mismatch');
      if (progress.current.profile_version && progress.current.profile_version !== next.profile_revision.version_id) {
        progress.current = {}; remember({profile_version: next.profile_revision.version_id}); clearVisible();
        setNotice('当前示例企业与保存进度不一致，已停止沿用旧引用。服务器历史记录保留。');
      }
      const saved = progress.current;
      // Recheck each private fixed input, not just a list or a browser pointer.
      await Promise.all([get(`/company/profiles/${encodeURIComponent(next.profile_revision.object_id)}?version=${encodeURIComponent(next.profile_revision.version_id)}`, signal), ...next.additional_material_revisions.map(item => get(`/company/materials/${encodeURIComponent(item.object_id)}?version=${encodeURIComponent(item.version_id)}`, signal))]);
      const [fixedBatch, fixedJob, fixedReview] = await Promise.all([
        saved.batch_id ? get<Batch>(`/screening/candidates/${encodeURIComponent(saved.batch_id)}?offset=${saved.batch_offset || 0}&limit=20`, signal) : null,
        saved.run_id ? get<Job>(`/jobs/runs/${encodeURIComponent(saved.run_id)}`, signal) : null,
        saved.case_id ? get<Review>(`/reviews/cases/${encodeURIComponent(saved.case_id)}`, signal) : null,
      ]);
      if (!valid()) return;
      setSeed(next); setAccessBlocked(false); setRefreshRequired(false); setContextConfirmed(true);
      if (fixedBatch) {
        if (fixedBatch.profile_revision.version_id !== next.profile_revision.version_id) throw new ApiError(409, 'review_candidate_binding_mismatch');
        setBatch(fixedBatch); setSelected(fixedBatch.items.find(item => item.unit_id === saved.unit_id) || null);
      }
      if (fixedJob) applyJob(fixedJob);
      if (fixedReview) applyReview(fixedReview);
    });
  }
  useEffect(() => {
    mutationBusy.current = false;
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(storageKey) || '{}');
      if (saved && typeof saved === 'object' && !Array.isArray(saved)) progress.current = saved as Progress;
    } catch {progress.current = {};}
    void refresh();
    return () => {lifetime.current++; controller.current?.abort(); controller.current = null; mutationBusy.current = false;};
  }, [storageKey]);
  useEffect(() => {
    if (!job || !ACTIVE.has(job.status) || accessBlocked) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    const pollController = new AbortController(), epoch = lifetime.current, runId = job.run_id;
    async function poll() {
      try {
        const value = await get<Job>(`/jobs/runs/${encodeURIComponent(runId)}`, pollController.signal);
        if (!stopped && lifetime.current === epoch && !mutationBusy.current) applyJob(value);
      } catch (value) {if (!stopped && lifetime.current === epoch) {failed(value); stopped = true;}}
      finally {if (!stopped) timer = setTimeout(() => void poll(), 2000);}
    }
    timer = setTimeout(() => void poll(), 1500);
    return () => {stopped = true; clearTimeout(timer); pollController.abort();};
  }, [job?.run_id, job?.status, accessBlocked]);

  async function prepare() {
    if (!contextConfirmed) return;
    await perform(async (signal, valid) => {
      const value = await request<DemoWorkflowContext>('/demo/workflow/context', {method: 'POST', headers, signal});
      if (!ready(value) || value.kind !== 'local_workflow' || value.reviewer_subject_id !== identity.user?.subject_id) throw new ApiError(403, 'workflow_identity_mismatch');
      if (!valid()) return;
      setContext(value); setSeed(value); remember({profile_version: value.profile_revision.version_id}); setNotice('虚构示例企业和证明材料已准备，可开始筛选。此步骤不调用模型。');
    });
  }
  async function candidates(event: FormEvent) {
    event.preventDefault(); if (!seed || progress.current.job_attempt) return;
    const filters: Record<string, string> = {};
    if (keyword.trim()) filters.keyword = keyword.trim();
    for (const [name, value] of [['min_budget_yuan', minimum], ['max_budget_yuan', maximum]]) {
      if (value && !/^\d+(\.\d+)?$/.test(value)) {setError('预算请填写非负十进制金额，单位为元。'); return;}
      if (value) filters[name] = value;
    }
    if (minimum && maximum && Number(minimum) > Number(maximum)) {setError('预算下限不能大于上限。'); return;}
    await perform(async (signal, valid) => {
      const value = await request<Batch>('/screening/candidates', {method: 'POST', headers, signal, body: JSON.stringify({profile_revision: seed.profile_revision, filters})});
      if (!valid()) return;
      setBatch(value); setSelected(null); setJob(null); setReview(null); jobLatest.current = null; reviewLatest.current = null; setDrafts({});
      progress.current = {profile_version: seed.profile_revision.version_id}; remember({batch_id: value.batch_id, batch_offset: 0});
      setNotice('筛选批次已保存。请选择要核对的项目；候选或待核实均不代表资格通过。');
    });
  }
  async function batchPage(offset: number) {
    if (!batch || progress.current.job_attempt) return;
    await perform(async (signal, valid) => {
      const value = await get<Batch>(`/screening/candidates/${encodeURIComponent(batch.batch_id)}?offset=${offset}&limit=20`, signal);
      if (!valid()) return; setBatch(value); setSelected(null); remember({batch_offset: offset, unit_id: undefined});
    });
  }
  function choose(item: Candidate) {
    if (progress.current.job_attempt) return;
    setSelected(item); remember({unit_id: item.unit_id, case_key: undefined}); setError('');
    requestAnimationFrame(() => document.getElementById('workflow-checklist')?.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'}));
  }
  async function submitJob() {
    if (!seed || !selected?.project_id || !canCreate || !runtime?.configured || !runtime.live_calls_enabled) return;
    const input: JobInput = {profile_revision: seed.profile_revision, project_id: selected.project_id, target_package_ids: selected.package_id ? [selected.package_id] : [], additional_material_revisions: seed.additional_material_revisions};
    const prior = progress.current.job_attempt;
    const attempt = prior && JSON.stringify(prior.input) === JSON.stringify(input) ? prior : {key: crypto.randomUUID(), input};
    remember({job_attempt: attempt});
    await perform(async (signal, valid) => {
      const value = await request<Job>('/jobs/runs', {method: 'POST', headers: {...headers, 'Idempotency-Key': attempt.key}, body: JSON.stringify(attempt.input), signal});
      if (!valid()) return;
      remember({run_id: value.run_id}); applyJob(value); setNotice('核对任务已由服务器接收。执行进度和费用以下方保存记录为准。');
    });
  }
  async function createReview() {
    if (!job || job.status !== 'waiting_review' || !batch || !seed || !canCreate) return;
    const key = progress.current.case_key || crypto.randomUUID(); remember({case_key: key});
    await perform(async (signal, valid) => {
      const value = await request<Review>('/reviews/cases', {method: 'POST', headers, signal, body: JSON.stringify({run_id: job.run_id, candidate_batch_id: batch.batch_id, reviewer_ids: [seed.reviewer_subject_id], idempotency_key: key})});
      if (!valid()) return;
      remember({case_id: value.case_id}); applyReview(value); setNotice('复核案件已保存，当前本机人员已显式指定为审核人。请逐项查看证据并独立决定。');
    });
  }
  async function transition(action: 'resume' | 'cancel') {
    const current = jobLatest.current;
    if (!current || !canCreate || !current.allowed_actions.includes(action) || (action === 'resume' && (current.status !== 'interrupted' || !runtime?.live_calls_enabled))) return;
    await perform(async (signal, valid) => {
      const value = await request<Job>(`/jobs/runs/${encodeURIComponent(current.run_id)}/${action}`, {method: 'POST', headers, signal, body: JSON.stringify({expected_state_version: current.state_version})});
      if (!valid()) return;
      applyJob(value); setNotice(action === 'resume' ? '服务器已接收继续原任务的请求，保留同一任务与固定输入。' : '服务器已记录取消请求。以刷新后的状态为准；在途模型请求仍可能产生费用。');
    });
  }
  function startAnother() {
    if (mutationBusy.current || (job && ACTIVE.has(job.status))) return;
    progress.current = {}; remember(seed ? {profile_version: seed.profile_revision.version_id} : {});
    setBatch(null); setSelected(null); setJob(null); setReview(null); setDrafts({}); setCloseReason(''); setError(''); setRefreshRequired(false);
    jobLatest.current = null; reviewLatest.current = null; keys.current.clear();
    setNotice('已开始另一轮筛选。上一轮服务器记录保留；只有再次点击开始核对才会新建模型任务。');
  }
  async function decide(item: ReviewItem) {
    const current = reviewLatest.current, draft = drafts[itemKey(item)];
    const head = current?.revisions.find(value => value.revision_id === current.head_revision_id);
    if (!current || !head || !draft?.decision || !draft.reason.trim() || !current.allowed_actions.includes('decide') || refreshRequired) return;
    const command = {decision: draft.decision, reason: draft.reason.trim(), expected_result_revision: head.result_revision};
    const body = {revision_id: item.revision_id, assessment_id: item.assessment_id, condition_id: item.condition_id, expected_version: current.review_version, command: {...command, idempotency_key: idempotency('decision', {case_id: current.case_id, expected_version: current.review_version, item: itemKey(item), command})}, supersedes_event_id: null};
    await perform(async (signal, valid) => {
      try {
        const value = await request<Review>(`/reviews/cases/${encodeURIComponent(current.case_id)}/decisions`, {method: 'POST', headers, signal, body: JSON.stringify(body)});
        if (!valid()) return; applyReview(value); setDrafts(old => {const next = {...old}; delete next[itemKey(item)]; return next;}); setNotice('该条人工决定已保存。原始模型判断保留不变。');
      } catch (value) {if (valid()) setRefreshRequired(true); throw value;}
    });
  }
  async function close() {
    const current = reviewLatest.current;
    if (!current || !closeReason.trim() || !current.allowed_actions.includes('close') || refreshRequired) return;
    const body = {expected_version: current.review_version, reason: closeReason.trim()};
    await perform(async (signal, valid) => {
      try {
        const value = await request<Review>(`/reviews/cases/${encodeURIComponent(current.case_id)}/close`, {method: 'POST', headers, signal, body: JSON.stringify({...body, idempotency_key: idempotency('close', {case_id: current.case_id, ...body})})});
        if (!valid()) return; applyReview(value); setCloseReason(''); setNotice('服务器已记录复核关闭；这不是正式资质认定或采购提交。');
      } catch (value) {if (valid()) setRefreshRequired(true); throw value;}
    });
  }
  async function exportReview() {
    if (!review) return;
    const caseId = review.case_id;
    await perform(async (signal, valid) => {
      const value = await get<Record<string, unknown>>(`/reviews/cases/${encodeURIComponent(caseId)}/export`, signal);
      if (!valid()) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'})), anchor = document.createElement('a');
      anchor.href = url; anchor.download = `bidops-review-${caseId}.json`; anchor.click(); URL.revokeObjectURL(url); setNotice('服务器已重新检查来源权限并导出审核包，包含模型原判断与独立人工记录。');
    });
  }
  const result = job?.result;
  const grouped = new Set(result?.groups.flatMap(group => group.assessment.conditions.map(item => item.condition_id)) || []);
  const head = review?.revisions.find(value => value.revision_id === review.head_revision_id);
  const recorded = review?.items.filter(item => item.effective && item.latest_decision).length || 0;
  const step = review ? 3 : selected || job ? 2 : 1;
  function projectCard(item: Candidate) {
    return <article key={item.unit_id} className={selected?.unit_id === item.unit_id ? 'workflow-project selected' : 'workflow-project'}><span className={`workflow-badge ${item.disposition}`}>{label(item.disposition)}</span><h3>{item.title || '项目标题待核实'}</h3>{item.package_label && <p>采购包：{item.package_label}</p>}<p>{item.budget_yuan ? `预算：${Number(item.budget_yuan).toLocaleString('zh-CN')} 元` : '预算待核实'}</p><p>{item.reasons.map(reason).join('；')}</p>{!!item.missing_fields.length && <p className="workflow-muted">缺失字段：{item.missing_fields.map(value => value === 'project_status' ? '项目状态' : value).join('、')}</p>}<button disabled={busy || !!progress.current.job_attempt || !item.project_id || item.disposition === 'excluded'} onClick={() => choose(item)}>{selected?.unit_id === item.unit_id ? '已选择此项目' : '选择此项目'}</button></article>;
  }
  return <NoticeLocationContext.Provider value={{csrfToken: identity.csrf_token, onAccessDenied: failed}}><main className="demo-workflow">
    <div className="workflow-heading"><div><p className="eyebrow">{context.label}</p><h1>从项目筛选到人工复核</h1><p>选择项目，核对企业证据，再逐项记录人工决定。关联进度自动保存，无需复制编号。</p></div><button disabled={busy} onClick={() => void refresh()}>刷新并继续</button></div>
    <aside className="workflow-disclosure"><strong>虚构企业示例 · 本机流程演示</strong><p>{context.description} 企业画像和证明材料不代表真实企业资质；公告来自已保存资料，最新项目状态仍需核实。</p></aside>
    <ol className="workflow-steps" aria-label="流程进度">{['筛选项目', '核对资格证据', '人工复核与导出'].map((name, index) => <li key={name} aria-current={step === index + 1 ? 'step' : undefined}><span>{index + 1}</span>{name}</li>)}</ol>
    {error && <div role="alert" className="workflow-error"><p>{error}</p>{accessBlocked && <a href="/api/auth/login">重新登录并继续</a>}{refreshRequired && <p>系统没有自动重试人工决定。请先点击“刷新并继续”，核实服务器已保存内容后再操作。</p>}</div>}
    {notice && <p role="status" className="workflow-notice">{notice}</p>}{busy && <p role="status">正在核对服务器记录，请稍候…</p>}
    {!accessBlocked && <fieldset className="workflow-content" disabled={!contextConfirmed}>
      <section className="workflow-card"><div className="workflow-section-heading"><span className="workflow-number">1</span><h2>筛选想跟进的项目</h2></div>
        {!seed ? <><p>先准备一份明确虚构的企业画像和证明材料。重复准备会沿用同一示例，不覆盖既有资料，也不调用模型。</p><button className="workflow-primary" disabled={busy || !canCreate || !contextConfirmed} onClick={() => void prepare()}>准备示例企业</button></> : <>
          <div className="workflow-company"><strong>{seed.company_name}</strong><span>虚构示例</span><p>{seed.materials?.map(item => item.label).join(' · ') || `已固定 ${seed.additional_material_revisions.length} 份示例证明材料`}</p><details><summary>查看本次固定企业输入</summary><pre>{JSON.stringify({profile_revision: seed.profile_revision, materials: seed.additional_material_revisions}, null, 2)}</pre></details></div>
          {!job && <form className="workflow-filter" onSubmit={event => void candidates(event)}><label>项目关键词<input value={keyword} maxLength={120} disabled={busy || !!progress.current.job_attempt} onChange={event => setKeyword(event.target.value)} placeholder="可留空，查看全部已保存项目" /></label><label>预算下限（元）<input inputMode="decimal" value={minimum} disabled={busy || !!progress.current.job_attempt} onChange={event => setMinimum(event.target.value)} /></label><label>预算上限（元）<input inputMode="decimal" value={maximum} disabled={busy || !!progress.current.job_attempt} onChange={event => setMaximum(event.target.value)} /></label><button className="workflow-primary" disabled={busy || !canCreate || !!progress.current.job_attempt}>筛选项目</button></form>}
        </>}
        {batch && <><p className="workflow-muted">固定批次时间：{date(batch.as_of)} · 共 {batch.counts.total_units} 项，候选 {batch.counts.candidate}，待核实 {batch.counts.needs_verification}，已排除 {batch.counts.excluded}。筛选不调用模型，也不判断资格通过。</p><SourceWarning state={batch.source_state} /><div className="workflow-projects">{batch.items.filter(item => item.disposition !== 'excluded').sort((left, right) => Number(left.disposition !== 'candidate') - Number(right.disposition !== 'candidate')).map(projectCard)}</div>{batch.items.some(item => item.disposition === 'excluded') && <details className="workflow-excluded"><summary>查看本页已排除项目（{batch.items.filter(item => item.disposition === 'excluded').length} 项；全批 {batch.counts.excluded} 项）</summary><div className="workflow-projects">{batch.items.filter(item => item.disposition === 'excluded').map(projectCard)}</div></details>}{!batch.items.length && <p>没有匹配的项目，请调整关键词或预算范围。</p>}{batch.total > batch.limit && <div className="workflow-actions"><button disabled={busy || !!progress.current.job_attempt || batch.offset === 0} onClick={() => void batchPage(Math.max(0, batch.offset - batch.limit))}>项目上一页</button><span>共 {batch.total} 项</span><button disabled={busy || !!progress.current.job_attempt || !batch.has_more} onClick={() => void batchPage(batch.offset + batch.limit)}>项目下一页</button></div>}</>}
      </section>
      {(selected || job) && <section id="workflow-checklist" className="workflow-card"><div className="workflow-section-heading"><span className="workflow-number">2</span><h2>核对企业资格证据</h2></div>{selected && <p className="workflow-project-title">{selected.title}{selected.package_label && ` · ${selected.package_label}`}</p>}
        {!job && <><p>核对会读取本次固定的公告、虚构企业画像及证明材料，生成逐项判断和引用。缺失、过期或冲突的证据必须保留，不能默认满足。</p><p className="workflow-muted">{runtime?.configured && runtime.live_calls_enabled ? '已开启真实模型调用，点击下方按钮才提交。费用按本数据目录的累计估算预算控制，不是供应商硬性扣费上限。' : '当前为预览，真实模型核对尚未开启。筛选与读取已有记录仍可使用。'}</p><button className="workflow-primary" disabled={busy || !canCreate || !runtime?.configured || !runtime.live_calls_enabled} onClick={() => void submitJob()}>{progress.current.job_attempt ? '重试同一资格核对' : '开始 AI 资格核对'}</button></>}
        {job && <><div className="workflow-row"><strong className="workflow-job-state">{label(job.status)}</strong><span className="workflow-muted">更新于 {date(job.updated_at)}</span></div>{ACTIVE.has(job.status) && <p role="status">正在自动刷新进度。离开或刷新页面后，可以继续读取同一任务，不会重新提交。</p>}{job.error_code && <p className="workflow-warning">{reason(job.error_code)}</p>}<SourceWarning state={job.source_state} />{job.usage && <p className="workflow-muted">{job.usage.offline ? '离线执行记录' : '模型执行记录'} · 本任务已确认估算 ¥{job.usage.estimated_cost_cny} · 保留费用预留 ¥{job.usage.reserved_cost_cny}{job.usage.usage_complete === false && ' · 用量尚未全部确认，预留继续保留'}</p>}
          {(job.allowed_actions.includes('cancel') && ACTIVE.has(job.status) || job.allowed_actions.includes('resume') && job.status === 'interrupted') && <div className="workflow-actions">{job.allowed_actions.includes('resume') && job.status === 'interrupted' && <button disabled={busy || !canCreate || !runtime?.configured || !runtime.live_calls_enabled} onClick={() => void transition('resume')}>继续原核对任务</button>}{job.allowed_actions.includes('cancel') && ACTIVE.has(job.status) && <button disabled={busy || !canCreate} onClick={() => void transition('cancel')}>请求取消核对</button>}</div>}
          {result ? <><p className="workflow-warning">以下为模型原判断，尚未人工复核。条件“满足”与任务完成都不代表正式资格通过。</p>{result.recommendation && <p>整体建议：{label(result.recommendation)}</p>}<ResultGaps result={result} />{!review && <>{result.groups.map(group => group.assessment.conditions.map(condition => <article className="workflow-condition" key={`${group.assessment.assessment_id}:${condition.condition_id}`}><Assessment condition={condition} judgment={group.assessment.assessments.find(item => item.condition_id === condition.condition_id)} /></article>))}{result.inventory.conditions?.filter(condition => !grouped.has(condition.condition_id)).map(condition => <article className="workflow-condition" key={condition.condition_id}><Assessment condition={condition} /></article>)}</>}{!result.groups.some(group => group.assessment.conditions.length) && <p className="workflow-warning">尚无完整资格条件，不能据此认定没有资格要求。</p>}</> : !ACTIVE.has(job.status) && <p className="workflow-warning">当前没有可读取的完整核对结果；请核实失败、缺件或中断原因。</p>}
          {!review && job.status === 'waiting_review' && <button className="workflow-primary" disabled={busy || !canCreate || !reviewEnabled} onClick={() => void createReview()}>进入人工复核</button>}
          <details className="workflow-run-details"><summary>查看运行依据与关联记录</summary><pre>{JSON.stringify({batch_id: batch?.batch_id, run_id: job.run_id, state_version: job.state_version, usage: job.usage, provenance: result?.provenance}, null, 2)}</pre></details>
          {!ACTIVE.has(job.status) && <div className="workflow-actions"><button disabled={busy} onClick={startAnother}>开始另一轮筛选</button><p className="workflow-muted">只清除本浏览器的流程引用，服务器历史与费用记录保留。不会自动重新调用模型。</p></div>}
        </>}
      </section>}
      {review && <section className="workflow-card"><div className="workflow-section-heading"><span className="workflow-number">3</span><h2>逐项人工复核</h2></div><div className="workflow-row"><strong>{label(review.status)}</strong><span>已记录有效人工决定 {recorded} / {review.items.length} 项</span></div><p>请根据公告要求和两侧证据独立判断。人工决定单独保存，不替换模型原判断；确认“信息不足”也不表示企业满足该条件。</p><SourceWarning state={review.source_state} />{head && <ResultGaps result={head.result} />}{review.items.map(item => {
        const draft = drafts[itemKey(item)] || {decision: '', reason: ''};
        const editable = !item.latest_event && canDecide && review.allowed_actions.includes('decide') && review.source_state.current;
        return <article className="workflow-condition" key={itemKey(item)}><Assessment condition={item.condition} judgment={item.agent_assessment} />{item.latest_decision && <div className="workflow-human"><strong>{label(item.latest_decision)} · {item.effective ? '当前有效' : '当前无效'}</strong><p>{item.latest_event?.reason}</p>{!!item.ineffective_reasons.length && <p>{item.ineffective_reasons.map(reason).join('；')}</p>}</div>}{editable ? <div className="workflow-decision"><label>该条人工决定<select value={draft.decision} disabled={busy || refreshRequired} onChange={event => setDrafts(old => ({...old, [itemKey(item)]: {...draft, decision: event.target.value as Draft['decision']}}))}><option value="">请选择，不默认确认</option><option value="confirm">确认原判断</option><option value="reject">驳回原判断</option><option value="request_materials">要求补充材料</option></select></label><label>该条独立审核理由<textarea value={draft.reason} maxLength={4000} disabled={busy || refreshRequired} onChange={event => setDrafts(old => ({...old, [itemKey(item)]: {...draft, reason: event.target.value}}))} placeholder="写明你核对了什么证据，为什么作出该决定" /></label><button className="workflow-primary" disabled={busy || refreshRequired || !draft.decision || !draft.reason.trim()} onClick={() => void decide(item)}>保存该条人工决定</button></div> : !item.latest_decision && <p className="workflow-muted">当前不能提交此项决定，请核实来源有效性、审核人及权限。</p>}</article>;
      })}{!review.items.length && <p className="workflow-warning">暂无可逐项复核内容，不能据此认定资格通过。</p>}{review.status === 'awaiting_materials' && <p className="workflow-warning">已记录补材料请求，本流程停留在待补证状态。请在高级“人工复核”页面处理后续材料，不能直接关闭为已完成。</p>}
        {review.allowed_actions.includes('close') && <div className="workflow-close"><label>关闭本次复核的理由<textarea value={closeReason} maxLength={4000} onChange={event => setCloseReason(event.target.value)} /></label><button disabled={busy || refreshRequired || !closeReason.trim()} onClick={() => void close()}>关闭本次复核</button></div>}
        {review.delivery && (review.delivery.pending_count > 0 || review.delivery.frozen_count > 0) && <p className="workflow-warning">人工记录已保存，后台仍有待处理事件（待处理 {review.delivery.pending_count}，冻结 {review.delivery.frozen_count}）；任务最终状态请刷新核实。</p>}
        <div className="workflow-actions"><button className="workflow-primary" disabled={busy || !review.allowed_actions.includes('export')} onClick={() => void exportReview()}>导出审核包</button><p className="workflow-muted">导出包含缺件、原判断和人工记录，不代表正式资质认定或外部提交。</p></div><details><summary>查看复核关联与固定版本</summary><pre>{JSON.stringify({case_id: review.case_id, review_version: review.review_version, head_revision_id: review.head_revision_id, result_revision: head?.result_revision, human_review_state: head?.result.human_review_state, delivery: review.delivery}, null, 2)}</pre></details>
      </section>}
    </fieldset>}
  </main></NoticeLocationContext.Provider>;
}
