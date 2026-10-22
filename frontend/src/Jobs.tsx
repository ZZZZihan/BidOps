import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, get, request } from './api';
import type { Identity } from './Enterprise';
import './Jobs.css';

type VersionRef = {object_id: string; version_id: string; sha256: string};
type Page<T> = {items: T[]; total: number; offset: number; limit: number};
type ProfileRow = {object_id: string; head_version: string};
type Profile = VersionRef & {payload: {display_name: string}};
type Material = VersionRef & {filename: string};
type RuntimeStatus = {enabled: boolean; configured: boolean; backend: string; live_calls_enabled: boolean};
type SourceState = {current: boolean; stale_reasons: string[]};
type Evidence = {evidence_id: string; version: VersionRef; quote: string; start: number; end: number; locator?: string; text_sha256?: string; page?: number | null; validity?: {kind: string; valid_from?: string | null; valid_until?: string | null; reason?: string | null}};
type Expression = {operator: string; text: string; operands: Expression[]};
type Condition = {condition_id: string; original_text: string; hard_requirement: boolean; notice_evidence: Evidence[]; expression?: Expression; package_scope?: {kind: string; package_ids: string[]; reason?: string | null}; validity?: {kind: string; required_at?: string | null; required_until?: string | null; reason?: string | null}};
type Judgment = {condition_id: string; status: string; reason: string; notice_evidence: Evidence[]; company_evidence: Evidence[]; missing_items: string[]; conflicting_evidence_ids?: string[]};
type Checklist = {run_id: string; result_revision: string; state: string; human_review_state: string; recommendation: string; error_code?: string | null; inventory: {conditions: Condition[]; unresolved: {document_id: string; reason: string}[]; missing_attachment_ids?: string[]}; groups: {primary_notice_revision: VersionRef; assessment: {target_package_id: string | null; conditions: Condition[]; assessments: Judgment[]}}[]; missing_items: string[]; provenance?: Record<string, unknown>};
type Usage = {model_calls: number; confirmed_calls: number; unknown_calls: number; orphan_reservations?: number; usage_complete?: boolean; estimated_cost_cny: string | number; reserved_cost_cny: string | number; offline: boolean};
type Manifest = {project_id?: string; as_of?: string; profile_revision?: VersionRef; material_revisions?: VersionRef[]; notice_revisions?: VersionRef[]; target_package_ids?: string[]; [key: string]: unknown};
type Job = {run_id: string; status: string; state_version: number; progress: {phase?: string; completed?: number; total?: number}; created_at: string | number; updated_at: string | number; result_id: string | null; result_revision: string | null; error_code: string | null; source_state?: SourceState; usage: Usage; allowed_actions: string[]; created?: boolean; manifest?: Manifest; versions?: unknown; result?: Checklist | null};
type ChecklistInput = {profile_revision: VersionRef; project_id: string; target_package_ids: string[]; additional_material_revisions: VersionRef[]; as_of?: string};

const STATES: Record<string, string> = {queued: '等待执行', running: '正在执行', waiting_review: '等待人工审核', cancel_requested: '取消请求已记录', cancelled: '已取消', failed: '失败或部分完成', completed: '执行已完成', timed_out: '执行超时', interrupted: '执行已中断'};
const PHASES: Record<string, string> = {queued: '等待执行', extraction: '抽取公告条件', assessment: '逐条核对证据', completed: '结果已保存', waiting_review: '等待人工审核', cancelled: '任务已取消', interrupted: '执行已中断', starting: '准备固定输入', preparing: '准备固定输入', pending: '等待执行', finalizing: '保存核对结果'};
const VERDICTS: Record<string, string> = {satisfied: '满足', not_satisfied: '不满足', information_insufficient: '信息不足', evidence_conflict: '证据冲突', not_applicable: '不适用', review_recommended: '建议人工复核', needs_materials: '需要补充材料', resolve_conflict: '需要处理证据冲突', do_not_pursue: '建议不继续跟进', status_unknown: '项目状态待核实', not_evaluated: '尚未完成核对'};
const ERRORS: Record<string, string> = {screening_busy: '当前执行槽位繁忙，请稍后重试。', screening_timeout: '资格核对超时，已有内容仍需人工复核。', screening_request_limit: '已达到模型请求次数上限。', screening_budget_exhausted: '累计预算不足，任务未完整执行。', screening_budget_review_required: '恢复后的累计费用尚未核对，付费请求保持关闭。', screening_usage_unconfirmed: '模型用量尚未确认，费用预留继续保留。', screening_model_failed: '模型步骤失败，已保存部分内容不代表完整结果。', assessment_not_completed: '该条件尚未完成核对', job_idempotency_conflict: '幂等键已关联其他输入，请先查询原任务；确需另建时显式开始另一项任务。', job_state_conflict: '任务状态版本已变化', job_transition_forbidden: '当前任务状态不允许此操作', job_not_executable: '固定输入或当前任务状态不满足执行要求', job_application_version_changed: '运行代码或依赖版本已有变化，不能按原任务自动恢复', job_settings_changed: '执行配置已有变化，需要重新核实原任务', job_inputs_changed: '固定输入已有变化，需要重新核实原任务', job_resume_forbidden: '当前任务不能恢复', job_restored_needs_resume: '恢复后的任务已重新授权，请核对原固定输入后显式恢复执行', job_checkpoint_not_resumable: '原任务检查点不能继续执行，请保留历史并按新输入版本另建任务', job_capacity_exhausted: '持久任务队列已满，请稍后重试', job_integrity_failed: '任务保存内容完整性检查失败，请联系管理员核对', jobs_disabled: '持久任务尚未开启。', jobs_not_configured: '持久任务运行时尚未配置。', current_identity_required: '身份已到期或账户已停用，请重新统一登录。'};
const ACTIVE = new Set(['queued', 'running', 'cancel_requested']);
const STALE_REASONS: Record<string, string> = {profile_head_changed: '企业画像已有新版本', material_head_changed: '企业证明材料已有新版本', notice_versions_changed: '公告版本已有变化', notice_observation_updated: '公告观察记录已有更新', attachment_versions_changed: '公告附件已有变化', public_sources_stale: '公告来源新鲜度不足'};
const refOnly = (value: VersionRef): VersionRef => ({object_id: value.object_id, version_id: value.version_id, sha256: value.sha256});
const reason = (value: string) => ERRORS[value] || value;
const timeText = (value: string | number) => new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN');
function failureText(value: unknown) { return value instanceof ApiError ? ERRORS[value.code] || `服务端拒绝操作（${value.status}，${value.code}）。` : '应答未确认，请刷新任务状态或按相同输入重试。'; }
function SourceWarning({state}: {state?: SourceState}) { return state?.current === false ? <p className="jobs-warning">输入或来源已有变化，保留结果仍使用原固定版本，需重新核实：{state.stale_reasons.map(value => STALE_REASONS[value] || value).join('；')}。</p> : null; }
function Logic({expression}: {expression: Expression}) {
  const operators: Record<string, string> = {atom: '原子条件', all: '全部成立（AND）', any: '至少一项成立（OR）', not: '否定（NOT）', unless: '基础要求及例外（UNLESS）'};
  return <div><p>{operators[expression.operator] || expression.operator}：{expression.text}</p>{!!expression.operands.length && <ul>{expression.operands.map((item, index) => <li key={index}><Logic expression={item} /></li>)}</ul>}</div>;
}
function References({title, items}: {title: string; items: Evidence[]}) { return <div><h4>{title}</h4>{!items.length && <p>尚无可核验引用，不能据此认定满足。</p>}{items.map((item, i) => <details key={`${item.evidence_id}-${i}`}><summary>{item.evidence_id}</summary><blockquote>{item.quote}</blockquote><dl><dt>来源对象</dt><dd><code>{item.version.object_id}</code></dd><dt>固定版本</dt><dd><code>{item.version.version_id}</code></dd><dt>版本 SHA-256</dt><dd><code>{item.version.sha256}</code></dd>{item.text_sha256 && <><dt>文本 SHA-256</dt><dd><code>{item.text_sha256}</code></dd></>}<dt>原文范围</dt><dd>{item.locator || '规范化文本'}{item.page ? ` · 第 ${item.page} 页` : ''} · [{item.start}, {item.end})</dd>{item.validity && <><dt>证明有效期</dt><dd>{item.validity.kind === 'interval' ? `${item.validity.valid_from || '起点待核实'} 至 ${item.validity.valid_until || '终点待核实'}` : item.validity.kind === 'perpetual' ? '已记录长期有效声明' : item.validity.kind === 'unknown' ? '有效期尚待核实' : '未要求特定期限'}{item.validity.reason && `；${item.validity.reason}`}</dd></>}</dl></details>)}</div>; }
function SavedResult({result}: {result: Checklist}) {
  const grouped = new Set(result.groups.flatMap(group => group.assessment.conditions.map(item => item.condition_id)));
  function conditionCard(condition: Condition, judgment?: Judgment) { return <article className="jobs-condition" key={condition.condition_id}><div className="jobs-row"><h4>{condition.original_text}</h4><strong className={`jobs-badge ${judgment?.status || 'information_insufficient'}`}>{judgment ? VERDICTS[judgment.status] || judgment.status : '尚未完成核对'}</strong></div><p>条件 <code>{condition.condition_id}</code> · {condition.hard_requirement ? '硬性要求' : '一般要求'}</p><p>{judgment ? reason(judgment.reason) : '已保留抽取条件，尚无完整判断。'}</p>{!!judgment?.missing_items.length && <p className="jobs-warning">缺失：{judgment.missing_items.map(reason).join('；')}</p>}{!!judgment?.conflicting_evidence_ids?.length && <p className="jobs-warning">冲突引用：{judgment.conflicting_evidence_ids.join('、')}</p>}{(condition.expression || condition.package_scope || condition.validity) && <details><summary>查看条件逻辑、适用范围与期限</summary>{condition.expression && <Logic expression={condition.expression} />}{condition.package_scope && <p>适用范围：{condition.package_scope.kind === 'specific' ? `指定采购包 ${condition.package_scope.package_ids.join('、')}` : condition.package_scope.kind === 'all' ? '项目全部采购包' : '范围尚待核实'}{condition.package_scope.reason && `；${condition.package_scope.reason}`}</p>}{condition.validity && <p>条件期限：{condition.validity.kind === 'not_required' ? '本条件未要求特定期限' : condition.validity.kind === 'unknown' ? '期限尚待核实' : condition.validity.kind === 'at' ? `在 ${condition.validity.required_at || '时点待核实'} 有效` : `${condition.validity.required_at || '起点待核实'} 至 ${condition.validity.required_until || '终点待核实'} 持续有效`}{condition.validity.reason && `；${condition.validity.reason}`}</p>}</details>}<div className="jobs-evidence"><References title="公告要求依据" items={judgment?.notice_evidence || condition.notice_evidence} /><References title="企业证明依据" items={judgment?.company_evidence || []} /></div></article>; }
  return <section className="jobs-saved-result"><h3>已保存的资格核对内容</h3><p className="jobs-warning">尚未人工审核。执行完成和条件“满足”均不代表资格已通过。</p>{result.human_review_state !== 'unreviewed' && <p className="jobs-warning">服务端返回此页面不支持的审核状态，请重新读取或联系管理员。</p>}<p>建议：{VERDICTS[result.recommendation] || result.recommendation} · 核对执行：{STATES[result.state] || result.state}</p><p>结果固定版本 <code>{result.result_revision}</code></p>{result.error_code && <p className="jobs-warning">部分内容或失败原因：{reason(result.error_code)}</p>}{!!result.missing_items.length && <p className="jobs-warning">缺失与待处理：{result.missing_items.map(reason).join('；')}</p>}{!!result.inventory.unresolved.length && <section><h4>未解析或未完成条款</h4><ul>{result.inventory.unresolved.map((item, i) => <li key={i}>{item.document_id}：{reason(item.reason)}</li>)}</ul></section>}{!!result.inventory.missing_attachment_ids?.length && <p>缺失或不可解析附件：{result.inventory.missing_attachment_ids.join('、')}</p>}{result.groups.map((group, i) => <section className="jobs-result-group" key={`${group.primary_notice_revision.version_id}-${i}`}><h4>采购包：{group.assessment.target_package_id || '适用范围待核实'}</h4><p>公告固定版本 <code>{group.primary_notice_revision.version_id}</code></p>{group.assessment.conditions.map(condition => conditionCard(condition, group.assessment.assessments.find(item => item.condition_id === condition.condition_id)))}</section>)}{result.inventory.conditions.filter(item => !grouped.has(item.condition_id)).map(item => conditionCard(item))}{!result.inventory.conditions.length && <p className="jobs-warning">尚无完整资格条件，不能据此认定项目没有资格要求。</p>}{result.provenance && <p>原提示版本：{String(result.provenance.prompt_version || '尚未记录')} · 原规则版本：{String(result.provenance.rule_version || '尚未记录')}</p>}{result.provenance && <details><summary>查看原核对的提示、模型与运行依据</summary><pre>{JSON.stringify(result.provenance, null, 2)}</pre></details>}</section>;
}

export function Jobs({identity}: {identity: Identity}) {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [profiles, setProfiles] = useState<Page<ProfileRow> | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [materials, setMaterials] = useState<Page<Material> | null>(null);
  const [selectedMaterials, setSelectedMaterials] = useState<Material[]>([]);
  const [projectId, setProjectId] = useState('');
  const [packages, setPackages] = useState('');
  const [asOf, setAsOf] = useState('');
  const [history, setHistory] = useState<Page<Job> | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [activeId, setActiveId] = useState('');
  const [lookupId, setLookupId] = useState('');
  const [pollEnabled, setPollEnabled] = useState(true);
  const [manualBusy, setManualBusy] = useState(false);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [notice, setNotice] = useState('');
  const [expired, setExpired] = useState(false);
  const lifetime = useRef(0);
  const manualSequence = useRef(0);
  const mutationSequence = useRef(0);
  const pollSequence = useRef(0);
  const detailSequence = useRef(0);
  const appliedDetailSequence = useRef(0);
  const manualController = useRef<AbortController | null>(null);
  const mutationController = useRef<AbortController | null>(null);
  const pollController = useRef<AbortController | null>(null);
  const selectedId = useRef('');
  const latestJob = useRef<Job | null>(null);
  const submission = useRef<{key: string; serialized: string} | null>(null);
  const canCreate = identity.permissions.includes('agent:use');
  const headers = {'Content-Type': 'application/json', 'X-CSRF-Token': identity.csrf_token || ''};

  function clearPrivate() {
    lifetime.current++; manualSequence.current++; mutationSequence.current++; pollSequence.current++;
    manualController.current?.abort(); mutationController.current?.abort(); pollController.current?.abort();
    selectedId.current = ''; latestJob.current = null; appliedDetailSequence.current = 0; submission.current = null;
    setProfiles(null); setProfile(null); setMaterials(null); setSelectedMaterials([]); setHistory(null); setJob(null); setActiveId(''); setLookupId(''); setProjectId(''); setPackages(''); setAsOf(''); setNotice(''); setPollError(''); setManualBusy(false); setMutationBusy(false);
  }
  function reportFailure(failure: unknown, background = false) {
    if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
      clearPrivate(); if (failure.status === 401) setExpired(true); setError(failureText(failure));
    } else if (background) setPollError(`自动刷新未完成：${failureText(failure)} 已显示内容尚未重新确认，请手动刷新。`);
    else setError(failureText(failure));
  }
  function applyJob(value: Job, sequence: number, focus = false) {
    if (focus) { if (selectedId.current !== value.run_id) {latestJob.current = null; appliedDetailSequence.current = 0;} selectedId.current = value.run_id; setActiveId(value.run_id); setLookupId(value.run_id); }
    if (selectedId.current !== value.run_id || (latestJob.current && value.state_version < latestJob.current.state_version)) return;
    // Usage receipts can change without a state transition. Across manual reads,
    // polls and mutation replies, an older request cannot replace a newer read
    // of the same state version. Higher persisted state versions still win.
    if (latestJob.current && value.state_version === latestJob.current.state_version && sequence < appliedDetailSequence.current) return;
    appliedDetailSequence.current = Math.max(appliedDetailSequence.current, sequence);
    const next = {...latestJob.current, ...value}; latestJob.current = next; setJob(next);
  }
  async function readJob(id: string, signal: AbortSignal) {
    const sequence = ++detailSequence.current;
    const value = await get<Job>(`/jobs/runs/${encodeURIComponent(id)}`, signal);
    return {value, sequence};
  }
  async function manual(action: (signal: AbortSignal, valid: () => boolean) => Promise<void>) {
    const sequence = ++manualSequence.current, epoch = lifetime.current;
    manualController.current?.abort(); const controller = new AbortController(); manualController.current = controller;
    const valid = () => !controller.signal.aborted && sequence === manualSequence.current && epoch === lifetime.current;
    setManualBusy(true); setError('');
    try { await action(controller.signal, valid); } catch (failure) { if (valid()) reportFailure(failure); }
    finally { if (valid()) setManualBusy(false); }
  }
  async function refresh(profileOffset = profiles?.offset || 0, historyOffset = history?.offset || 0) {
    await manual(async (signal, valid) => {
      const status = await get<RuntimeStatus>('/jobs/status', signal);
      if (!valid()) return;
      if (!status.enabled) { clearPrivate(); setRuntime(status); return; }
      const pinned = profile, fixedMaterials = [...selectedMaterials], id = selectedId.current;
      const [nextProfiles, nextHistory, fixedProfile, fixedJob, nextMaterials] = await Promise.all([
        get<Page<ProfileRow>>(`/company/profiles?offset=${profileOffset}&limit=20`, signal),
        get<Page<Job>>(`/jobs/runs?offset=${historyOffset}&limit=20`, signal),
        pinned ? get<Profile>(`/company/profiles/${encodeURIComponent(pinned.object_id)}?version=${encodeURIComponent(pinned.version_id)}`, signal) : null,
        id ? readJob(id, signal) : null,
        materials ? get<Page<Material>>(`/company/materials?offset=${materials.offset}&limit=20`, signal) : null,
        ...fixedMaterials.map(item => get<Material>(`/company/materials/${encodeURIComponent(item.object_id)}?version=${encodeURIComponent(item.version_id)}`, signal)),
      ]);
      if (!valid()) return;
      setRuntime(status); setProfiles(nextProfiles); setHistory(nextHistory); if (fixedProfile) setProfile(fixedProfile); if (fixedJob) applyJob(fixedJob.value, fixedJob.sequence); if (nextMaterials) setMaterials(nextMaterials); setPollError('');
    });
  }
  useEffect(() => { void refresh(0, 0); return () => {lifetime.current++; manualController.current?.abort(); mutationController.current?.abort(); pollController.current?.abort();}; }, []);
  useEffect(() => {
    if (!activeId || !runtime?.enabled || !pollEnabled || expired) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const sequence = ++pollSequence.current, epoch = lifetime.current, controller = new AbortController(); pollController.current = controller;
      try {
        const saved = await readJob(activeId, controller.signal);
        if (!stopped && !controller.signal.aborted && sequence === pollSequence.current && epoch === lifetime.current && selectedId.current === activeId) {applyJob(saved.value, saved.sequence); setPollError('');}
      } catch (failure) {if (!stopped && !controller.signal.aborted && sequence === pollSequence.current && epoch === lifetime.current) reportFailure(failure, true);}
      finally {if (!stopped && selectedId.current === activeId) timer = setTimeout(() => void poll(), 2000);}
    }
    void poll();
    return () => {stopped = true; clearTimeout(timer); pollController.current?.abort();};
  }, [activeId, runtime?.enabled, pollEnabled, expired]);

  async function chooseProfile(row: ProfileRow) {
    await manual(async (signal, valid) => {
      const value = await get<Profile>(`/company/profiles/${encodeURIComponent(row.object_id)}?version=${encodeURIComponent(row.head_version)}`, signal);
      if (!valid()) return; setProfile(value); setSelectedMaterials([]); setMaterials(null); setNotice('');
    });
  }
  async function loadMaterials(offset = 0) { await manual(async (signal, valid) => {const value = await get<Page<Material>>(`/company/materials?offset=${offset}&limit=20`, signal); if (valid()) setMaterials(value);}); }
  async function chooseMaterial(value: Material, checked: boolean) {
    if (!checked) {setSelectedMaterials(items => items.filter(item => item.version_id !== value.version_id)); return;}
    await manual(async (signal, valid) => {const fixed = await get<Material>(`/company/materials/${encodeURIComponent(value.object_id)}?version=${encodeURIComponent(value.version_id)}`, signal); if (valid()) setSelectedMaterials(items => [...items.filter(item => item.version_id !== fixed.version_id), fixed]);});
  }
  async function openRun(id: string) {
    const value = id.trim(); if (!value) return;
    selectedId.current = value; latestJob.current = null; appliedDetailSequence.current = 0; setActiveId(''); setJob(null); setNotice('');
    await manual(async (signal, valid) => {const saved = await readJob(value, signal); if (valid()) applyJob(saved.value, saved.sequence, true);});
  }
  async function mutate(action: (signal: AbortSignal, valid: () => boolean) => Promise<void>, stateTransition = false) {
    if (mutationController.current && mutationBusy) return;
    const sequence = ++mutationSequence.current, epoch = lifetime.current, controller = new AbortController(); mutationController.current = controller;
    const valid = () => !controller.signal.aborted && sequence === mutationSequence.current && epoch === lifetime.current;
    setMutationBusy(true); setError(''); setNotice('');
    try {await action(controller.signal, valid);} catch (failure) {
      if (!valid()) return;
      if (stateTransition && failure instanceof ApiError && failure.status === 409) {
        setError(`服务端拒绝本次状态操作（${reason(failure.code)}）；正在重新读取状态，请检查后再决定，没有自动重试。`);
        if (selectedId.current) try {const saved = await readJob(selectedId.current, controller.signal); if (valid()) applyJob(saved.value, saved.sequence);} catch (refreshFailure) {if (valid()) reportFailure(refreshFailure);}
      } else reportFailure(failure);
    } finally {if (valid()) setMutationBusy(false);}
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!profile || !canCreate) return;
    let input: ChecklistInput;
    try {
      input = {profile_revision: refOnly(profile), project_id: projectId.trim(), target_package_ids: [...new Set(packages.split(/[,，\s]+/).filter(Boolean))], additional_material_revisions: selectedMaterials.map(refOnly), ...(asOf ? {as_of: new Date(asOf).toISOString()} : {})};
      if (!input.project_id) throw new Error();
    } catch {setError('请填写项目编号和有效时间。'); return;}
    const serialized = JSON.stringify(input);
    if (!submission.current || submission.current.serialized !== serialized) submission.current = {key: crypto.randomUUID(), serialized};
    const attempt = submission.current;
    await mutate(async (signal, valid) => {
      const sequence = ++detailSequence.current;
      const accepted = await request<Job>('/jobs/runs', {method: 'POST', signal, headers: {...headers, 'Idempotency-Key': attempt.key}, body: attempt.serialized});
      if (!valid()) return;
      applyJob(accepted, sequence, true); setNotice(accepted.created === false ? '服务器已返回同一输入的已有任务，任务编号保持不变。' : '服务器已接收持久任务。请按任务状态查看进度，接收不代表资格已通过。');
    });
  }
  async function transition(action: 'cancel' | 'resume') {
    const current = latestJob.current;
    if (!current || !current.allowed_actions.includes(action) || (action === 'resume' && current.status !== 'interrupted')) return;
    await mutate(async (signal, valid) => {
      const sequence = ++detailSequence.current;
      const value = await request<Job>(`/jobs/runs/${encodeURIComponent(current.run_id)}/${action}`, {method: 'POST', signal, headers, body: JSON.stringify({expected_state_version: current.state_version})});
      if (!valid()) return; applyJob(value, sequence); setNotice(action === 'cancel' ? '服务器已记录取消请求。以任务状态确认为准；已在途请求仍可能产生费用。' : '服务器已接收恢复操作，继续使用原任务编号和固定输入。');
    }, true);
  }

  return <main className="jobs-panel"><div className="agent-heading"><div><h1>持久核对任务</h1><p>按固定画像与公告执行，可查看进度和已保存结果。候选或完成状态均不代表资格通过。</p></div><button disabled={manualBusy || mutationBusy || expired} onClick={() => void refresh()}>刷新任务与权限</button></div>
    {error && <p role="alert" className="jobs-error">{error}</p>}{notice && <p role="status" className="jobs-notice">{notice}</p>}{pollError && <p role="alert" className="jobs-warning">{pollError}</p>}
    {expired ? <section className="jobs-card"><h2>需要重新确认企业身份</h2><a href="/api/auth/login">重新统一登录</a></section> : !runtime ? <p role="status">正在读取持久任务配置…</p> : !runtime.enabled ? <section className="jobs-card"><h2>持久任务尚未开启</h2><p>默认关闭，页面不会提交任务或发起模型调用。</p></section> : <>
      <p className="jobs-runtime">运行后端：{runtime.backend} · {runtime.live_calls_enabled ? '外部模型调用须受服务端授权和预算约束' : '外部模型调用关闭；离线工程运行仍可由服务端配置提供'}。</p>
      {!runtime.configured && <p className="jobs-warning">持久任务运行时尚未配置，可以读取仍有权限的历史；当前不能创建任务。</p>}
      <div className="jobs-columns"><section className="jobs-card"><h2>创建固定输入任务</h2>{!canCreate && <p>当前角色可以读取自己的授权历史，没有创建任务权限。</p>}<h3>选择企业画像</h3><ul className="jobs-profile-list">{profiles?.items.map(row => <li key={row.object_id}><button disabled={manualBusy || mutationBusy} onClick={() => void chooseProfile(row)}>选择画像 {row.object_id.slice(0, 12)}</button><small>当前版本 {row.head_version}</small></li>)}</ul>{profiles && !profiles.items.length && <p>没有可访问的画像，请先到企业画像与材料中创建或取得授权。</p>}{profiles && <div className="jobs-pagination"><button disabled={manualBusy || profiles.offset === 0} onClick={() => void refresh(Math.max(0, profiles.offset - profiles.limit))}>上一页画像</button><span>画像 {profiles.total ? profiles.offset + 1 : 0}–{Math.min(profiles.offset + profiles.items.length, profiles.total)} / {profiles.total}</span><button disabled={manualBusy || profiles.offset + profiles.limit >= profiles.total} onClick={() => void refresh(profiles.offset + profiles.limit)}>下一页画像</button></div>}
        {profile && <div className="jobs-pinned"><h3>{profile.payload.display_name}</h3><p>已固定画像版本 <code>{profile.version_id}</code></p><small>SHA-256 <code>{profile.sha256}</code></small></div>}
        <form onSubmit={event => void submit(event)}><label>项目编号<input value={projectId} onChange={event => setProjectId(event.target.value)} required disabled={mutationBusy} /></label><label>目标采购包（逗号或空格分隔，可留空）<input value={packages} onChange={event => setPackages(event.target.value)} disabled={mutationBusy} /></label><label>核对时点（本机时区，可留空）<input type="datetime-local" value={asOf} onChange={event => setAsOf(event.target.value)} disabled={mutationBusy} /></label>
          <button type="button" disabled={!profile || manualBusy || mutationBusy} onClick={() => void loadMaterials()}>选择额外证明材料</button>{materials && <fieldset><legend>额外固定材料</legend>{materials.items.map(item => <label className="jobs-choice" key={item.version_id}><input type="checkbox" checked={selectedMaterials.some(value => value.version_id === item.version_id)} disabled={manualBusy || mutationBusy} onChange={event => void chooseMaterial(item, event.target.checked)} /><span>{item.filename}<small>{item.version_id}</small></span></label>)}{!materials.items.length && <p>当前页无可访问材料。</p>}<div className="jobs-pagination"><button type="button" disabled={manualBusy || materials.offset === 0} onClick={() => void loadMaterials(Math.max(0, materials.offset - materials.limit))}>上一页材料</button><span>共 {materials.total} 份</span><button type="button" disabled={manualBusy || materials.offset + materials.limit >= materials.total} onClick={() => void loadMaterials(materials.offset + materials.limit)}>下一页材料</button></div></fieldset>}
          {!!selectedMaterials.length && <p>已选固定材料：{selectedMaterials.map(item => item.filename).join('、')}（{selectedMaterials.length} 份）</p>}<p className="jobs-help">应答不明时，相同输入重试沿用原幂等键。重新打开页面后请先读历史或按任务编号查询，不要通过再次提交查进度。</p><div className="jobs-actions"><button className="jobs-primary" disabled={!canCreate || !runtime.configured || !profile || !projectId.trim() || mutationBusy || manualBusy}>提交持久核对任务</button><button type="button" disabled={mutationBusy} onClick={() => {submission.current = null; setNotice('下一次提交将使用新的幂等键，请确认确实需要另建任务。');}}>开始另一项任务</button></div></form>
      </section><section className="jobs-card"><h2>自己的任务历史</h2><form onSubmit={event => {event.preventDefault(); void openRun(lookupId);}}><label>已有任务编号<input value={lookupId} onChange={event => setLookupId(event.target.value)} /></label><button disabled={manualBusy || mutationBusy || !lookupId.trim()}>读取任务</button></form><ul className="jobs-history">{history?.items.map(item => <li key={item.run_id}><button disabled={manualBusy || mutationBusy} onClick={() => void openRun(item.run_id)} aria-label={`打开任务 ${item.run_id}`}><code>{item.run_id}</code></button><span className={`jobs-badge ${item.status}`}>{STATES[item.status] || item.status}</span><small>{timeText(item.updated_at)}</small></li>)}</ul>{history && !history.items.length && <p>当前页没有可访问的任务。</p>}{history && <div className="jobs-pagination"><button disabled={manualBusy || history.offset === 0} onClick={() => void refresh(undefined, Math.max(0, history.offset - history.limit))}>上一页任务</button><span className="jobs-history-range">第 {history.total ? history.offset + 1 : 0}–{Math.min(history.offset + history.items.length, history.total)} 项，共 {history.total} 项</span><button disabled={manualBusy || history.offset + history.limit >= history.total} onClick={() => void refresh(undefined, history.offset + history.limit)}>下一页任务</button></div>}</section></div>
      {job && <section className="jobs-card jobs-detail"><div className="jobs-row"><h2>任务状态与结果</h2><strong className={`jobs-badge jobs-task-state ${job.status}`}>{STATES[job.status] || job.status}</strong></div><p>稳定任务编号 <code className="jobs-run-id">{job.run_id}</code></p><p>状态版本 <span className="jobs-state-version">{job.state_version}</span> · {(job.progress.phase && (PHASES[job.progress.phase] || job.progress.phase)) || '等待进度更新'} · 已完成 {job.progress.completed ?? 0} / {job.progress.total ?? '待确认'}</p><p>创建：{timeText(job.created_at)} · 更新：{timeText(job.updated_at)}</p><label className="jobs-choice"><input type="checkbox" checked={pollEnabled} onChange={event => setPollEnabled(event.target.checked)} />自动刷新当前任务（每 2 秒）</label>{ACTIVE.has(job.status) && <p>刷新只读取任务，不会重新提交模型运行。</p>}<SourceWarning state={job.source_state} />{job.error_code && <p className="jobs-warning">运行原因：{reason(job.error_code)}</p>}
        {job.status === 'cancel_requested' && <p className="jobs-warning">取消请求已经持久记录，正在等待执行侧确认；在途模型请求仍可能计费。</p>}{job.status === 'cancelled' && <p>任务已取消，不能恢复。需要重新执行时应显式另建任务并核对输入和预算。</p>}{job.status === 'waiting_review' && <p className="jobs-warning">等待人工审核。审核事件由 B06 接入，当前页面不能跳过审批或代签。</p>}
        <div className="jobs-actions">{job.allowed_actions.includes('cancel') && job.status !== 'cancelled' && <button disabled={!canCreate || mutationBusy} onClick={() => void transition('cancel')}>请求取消任务</button>}{job.status === 'interrupted' && job.allowed_actions.includes('resume') && <button disabled={!canCreate || mutationBusy} onClick={() => void transition('resume')}>恢复中断任务</button>}<button disabled={manualBusy || mutationBusy} onClick={() => void refresh()}>刷新当前任务</button></div>
        {job.manifest && <section className="jobs-fixed-inputs"><h3>此任务固定输入</h3><p>以下是本任务保存的输入，与上方用于下次提交的表单分别记录。</p>{job.manifest.project_id && <p>任务项目：<code>{job.manifest.project_id}</code></p>}{job.manifest.as_of && <p>原核对时点：{timeText(job.manifest.as_of)}</p>}{job.manifest.profile_revision && <dl><dt>任务画像固定版本</dt><dd><code>{job.manifest.profile_revision.version_id}</code></dd><dt>任务画像 SHA-256</dt><dd><code>{job.manifest.profile_revision.sha256}</code></dd></dl>}<p>公告版本 {job.manifest.notice_revisions?.length ?? '待确认'} 份 · 企业材料版本 {job.manifest.material_revisions?.length ?? '待确认'} 份</p>{job.manifest.target_package_ids && <p>固定采购包：{job.manifest.target_package_ids.join('、') || '项目整体／范围待核实'}</p>}</section>}<section className="jobs-usage"><h3>调用与费用确认</h3><p>模型请求 {job.usage.model_calls} · 已确认 {job.usage.confirmed_calls} · <strong>用量未知 {job.usage.unknown_calls}</strong></p>{!!job.usage.orphan_reservations && <p className="jobs-warning">另有 {job.usage.orphan_reservations} 项预留尚未关联调用回执，费用仍需核对。</p>}<p>已确认估算 ¥{job.usage.estimated_cost_cny} · 保留预留 ¥{job.usage.reserved_cost_cny}</p>{(job.usage.unknown_calls > 0 || !!job.usage.orphan_reservations || job.usage.usage_complete === false) && <p className="jobs-warning">用量尚未确认，不能按零费用处理，预留不会因取消或重启自动释放。</p>}<p>{job.usage.offline ? '离线工程模型，不代表真实模型质量或供应商账单。' : '估算与预留不是供应商账单的绝对硬上限。'}</p></section>
        {job.result_id && <p>已保存结果编号 <code className="jobs-result-id">{job.result_id}</code>{job.result_revision && <> · 版本 <code>{job.result_revision}</code></>}</p>}{job.result ? <SavedResult result={job.result} /> : <p>当前没有可读取的完整结果；未完成、取消或缺少证明均不能当作满足。</p>}{!!(job.manifest || job.versions) && <details className="jobs-manifest"><summary>查看本任务固定输入与版本</summary><pre>{JSON.stringify({manifest: job.manifest, versions: job.versions}, null, 2)}</pre></details>}
      </section>}
    </>}
  </main>;
}
