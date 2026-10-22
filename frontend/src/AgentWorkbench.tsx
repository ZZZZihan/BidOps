import { useEffect, useRef, useState } from 'react';
import { get } from './api';
import type { Identity } from './Enterprise';

type Citation = { id: string; field: string; quote: string; start: number; end: number; locator: string; source_url: string; sha256: string };
type Card = {
  project_code: string; title: string; buyer_name: string | null; budget_yuan: string | null;
  bid_deadline_at: string | null; published_at: string | null; document_deadline_at: string | null;
  package_count: number; packages: {label: string; budget_yuan: string | null}[];
  eligibility: string[]; source_url: string; source_observed_at: string; citations: Citation[];
};
type Block = { id: string; operation: string; total: number; items: Card[]; metrics: Record<string, string>; missing_project_codes: string[]; offset?: number; limit?: number; has_more?: boolean; truncated?: boolean };
type Answer = {
  run_id: string; session_id: string; response_mode: string; focus: string; message: string;
  blocks: Block[]; trace: {tool: string; arguments: object; matches: number}[];
  usage: {input_tokens: number; output_tokens: number; model_requests: number; estimated_cost_cny_peak: number};
  model: string; elapsed_ms: number; warnings: string[]; as_of: string;
};
type AgentStatus = { configured: boolean; enabled: boolean; model: string; notice_count: number; estimated_spend_cny: number };
type Turn = { question: string; answer?: Answer; error?: string; requestId?: string; originalSession?: string; recover?: boolean };
type SavedSession = {session_id: string; updated_at: number; turns: number};
const labels: Record<string, string> = {
  budget: '预算金额', title: '项目名称', project_code: '项目编号', buyer_name: '采购单位',
  published_at: '公告时间', bid_deadline_at: '投标截止', notice_kind: '公告类型', eligibility: '资格原文', packages: '采购包',
  lowest_budget_project: '最低预算项目', highest_budget_project: '最高预算项目', budget_difference_yuan: '预算差额（元）',
  budget_sum_yuan: '预算合计（元）', earliest_deadline_project: '最早截止项目', latest_deadline_project: '最晚截止项目', total_packages: '采购包总数',
  read_notice: '读取公告', search_notices: '筛选公告', compare_notices: '比较项目',
};
const errors: Record<string, string> = {
  deepseek_key_missing: '尚未配置 DeepSeek Key。请按 README 设置本地凭证后重启服务。',
  agent_disabled: 'Agent 调用已关闭。请检查本地配置。',
  provider_auth_failed: 'DeepSeek 身份验证失败，请检查本地 API Key。',
  provider_quota_exhausted: 'DeepSeek 可用额度不足。请检查账户额度。',
  provider_rate_limited: 'DeepSeek 暂时限流，请稍后重试。',
  provider_timeout: '本轮请求超时。可以缩小问题范围后重试。',
  provider_unavailable: '模型服务暂时不可用，请稍后重试。',
  session_expired: '会话已过期，请点击“新对话”重新开始。',
  conversation_limit_reached: '本轮对话已达长度上限，请开启新对话。',
  empty_catalog: '还没有公告正文，请先导入真实样本包。',
  source_integrity_failed: '原始数据或证据校验失败，请重新校验导入包。',
  budget_limit_reached: '已达到本地评测/试用预算上限，请检查用量记录。',
  credential_in_message: '请勿在提问中填写 API Key。凭证只在本地服务配置中使用。',
  agent_output_invalid: '模型未能返回可验证的结果，请换一种表述重试。',
  run_usage_limit: '本轮工具调用达到限制，请缩小问题范围后重试。',
  agent_busy: '助手正在处理其他请求，请稍后重试。',
  session_not_found: '找不到该会话，或你没有访问权限。',
  session_busy: '这个会话有一轮请求尚未结束，请稍后再试。',
  request_in_progress: '原请求仍在处理中。稍后可重取本次结果，不会重复调用模型。',
  request_interrupted: '这轮请求曾被中断，未自动重跑。请核对历史记录后发起新问题。',
  user_rate_limit: '提问过于频繁，请稍后再试。',
  idempotency_conflict: '请求标识与原问题不一致，请开启新对话。',
};
const currency = (value: string | null) => value === null ? '未知' : `${new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(Number(value))} 元`;
const date = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short', hour12: false}).format(new Date(value)) : '未知 · 尚未确认';
const examples = ['哪些项目预算不超过 120 万元？', 'TGPC-2026-A-0324 有几个采购包？', '比较 A-0384 和 A-0343 的预算', '云资源项目的中小企业条件是什么？'];

export function AgentWorkbench({identity}: {identity: Identity}) {
  const enterprise = identity.mode === 'enterprise';
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [input, setInput] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [session, setSession] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Card | null>(null);
  const [saved, setSaved] = useState<SavedSession[]>([]);
  const end = useRef<HTMLDivElement>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    get<AgentStatus>('/agent/status', abort.signal).then(setStatus).catch((e: Error) => {if (!abort.signal.aborted) setConnectionError(e.message);});
    if (enterprise) get<{items: SavedSession[]}>('/sessions', abort.signal).then(result => setSaved(result.items)).catch(() => {if (!abort.signal.aborted) setConnectionError('无法读取会话历史。');});
    return () => {abort.abort(); controller.current?.abort();};
  }, [enterprise]);
  useEffect(() => {end.current?.scrollIntoView({block: 'nearest'});}, [turns, busy]);
  async function restore(id: string) {
    if (!id || busy) return;
    setBusy(true); setConnectionError('');
    try {
      const history = await get<{turns: (Turn & {state: string})[]}>(`/sessions/${id}`);
      setTurns(history.turns.map(turn => ({...turn, error: turn.error ? errors[turn.error] || '这轮请求未完成。' : turn.state === 'started' ? '这轮请求仍在处理，或服务曾被中断。稍后刷新历史。' : undefined})));
      setSession(id); setSelected(null);
    } catch {setConnectionError('无法恢复会话，请确认登录状态后重试。');}
    finally {setBusy(false);}
  }
  async function send(question = input, recovery?: Turn) {
    question = question.trim();
    if (!question || busy || !status?.configured || !status.enabled || status.notice_count === 0) return;
    const requestId = recovery?.requestId || crypto.randomUUID().replaceAll('-', '');
    const originalSession = recovery ? recovery.originalSession : session;
    setInput(''); setBusy(true);
    setTurns(current => recovery ? current.map(turn => turn.requestId === requestId ? {...turn, error: undefined, recover: false} : turn) : [...current, {question, requestId, originalSession}]);
    controller.current = new AbortController();
    try {
      const response = await fetch('/api/agent/chat', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': identity.csrf_token || '', 'Idempotency-Key': requestId}, body: JSON.stringify({message: question, session_id: originalSession}), signal: controller.current.signal});
      const body = await response.json();
      if (!response.ok) {
        const reason = body.error || body.detail;
        const message = response.status === 401 ? '登录已过期，请刷新页面重新登录。' : errors[reason] || `请求未完成（HTTP ${response.status}），请检查服务后重试。`;
        setTurns(current => current.map(turn => turn.requestId === requestId ? {...turn, error: message, recover: reason === 'request_in_progress'} : turn));
        return;
      }
      const answer = body as Answer;
      setSession(answer.session_id);
      setTurns(current => current.map(turn => turn.requestId === requestId ? {...turn, answer, error: undefined, recover: false} : turn));
      if (enterprise) get<{items: SavedSession[]}>('/sessions').then(result => setSaved(result.items)).catch(() => setConnectionError('回答已完成，但会话列表暂未刷新。'));
      const card = answer.blocks.flatMap(block => block.items)[0];
      if (card) setSelected(card);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setTurns(current => current.map(turn => turn.requestId === requestId ? {...turn, error: '连接中断，结果尚不确定。可重取原请求结果。', recover: enterprise} : turn));
    } finally {setBusy(false);}
  }
  const unavailable = !status?.configured || !status.enabled || status.notice_count === 0;
  return <main className="agent-workbench">
    <div className="agent-heading"><div><h1>先问问题，再看证据。</h1><p>{status ? `当前检索 ${status.notice_count} 条已导入公告。所有日期使用北京时间。` : '正在连接本地公告库…'}</p></div><button disabled={busy} onClick={() => {setTurns([]);setSession(undefined);setSelected(null);}}>新对话</button></div>
    {enterprise && <div className="session-picker"><label htmlFor="saved-session">我的会话</label><select id="saved-session" disabled={busy} value={saved.some(item => item.session_id === session) ? session : ''} onChange={event => void restore(event.target.value)}><option value="">选择已保存的对话</option>{saved.map(item => <option value={item.session_id} key={item.session_id}>{new Date(item.updated_at * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai'})} · {item.turns} 轮</option>)}</select><small>仅你可见 · 刷新后可恢复</small>{session && <button disabled={busy} onClick={() => void restore(session)}>刷新历史</button>}</div>}
    {connectionError && <p className="agent-alert" role="alert">无法连接后端：{connectionError}</p>}
    {status && !status.configured && <p className="agent-alert" role="status">尚未配置 DeepSeek。请按 README 设置本地凭证，然后重启服务。</p>}
    {status?.notice_count === 0 && <p className="agent-alert" role="status">公告库为空。请导入已采集的真实样本包后刷新。</p>}
    <div className="agent-layout">
      <section className="conversation" aria-label="Agent 对话">
        <div className="conversation-scroll" aria-live="polite">
          {!turns.length && <div className="conversation-welcome"><h2>把公告里的问题问清楚</h2><p>筛选项目、核对预算、比较截止时间，或查看中小企业相关原文。</p><div className="question-examples">{examples.map(question => <button key={question} disabled={unavailable} onClick={() => void send(question)}>{question}</button>)}</div><p className="subtle">回答基于已保存的快照。最新更正、附件和投标资格需要人工确认。</p></div>}
          {turns.map((turn, index) => <article className="conversation-turn" key={index}>
            <div className="user-question"><span>你的问题</span><p>{turn.question}</p></div>
            {turn.error && <div className="agent-alert" role="alert">{turn.error}{turn.recover && <button disabled={busy} onClick={() => void send(turn.question, turn)}>重取本次结果</button>}</div>}
            {turn.answer && <div className="agent-answer"><div className={`answer-state ${turn.answer.response_mode}`}>{({results: '已找到证据', unavailable: '信息不足', clarify: '需要补充', review_required: '需要人工复核', out_of_scope: '超出当前范围'} as Record<string,string>)[turn.answer.response_mode]}</div><p>{turn.answer.message}</p>
              {turn.answer.blocks.map(block => <div key={block.id} className="result-block">
                {(block.operation === 'search' || block.operation === 'read') && <div className="result-count"><p>匹配 {block.total} 条，{block.items.length ? `展示第 ${(block.offset ?? 0) + 1}–${(block.offset ?? 0) + block.items.length} 条（${block.items.length} 条）` : '本页没有记录'}{block.limit ? `，每页最多 ${block.limit} 条` : ''}</p>{(block.truncated ?? block.items.length < block.total) && <p role="status">结果已截断，本页未展示全部匹配记录。{block.has_more ? '后面还有结果，可继续要求读取下一页。' : '可继续要求读取其他页并核对历史版本。'}</p>}</div>}
                {!!block.missing_project_codes.length && <p>未找到：{block.missing_project_codes.join('、')}</p>}
                {!!Object.keys(block.metrics).length && <dl className="comparison-metrics">{Object.entries(block.metrics).map(([key, value]) => <div key={key}><dt>{labels[key] || key}</dt><dd>{value}</dd></div>)}</dl>}
                {block.items.map((card, itemIndex) => <div className="result-notice" key={`${card.project_code}-${itemIndex}`}><button className="result-title" onClick={() => setSelected(card)}>{card.title}</button><small>{card.project_code}</small><div className="result-values"><span>预算 <strong>{currency(card.budget_yuan)}</strong></span><span>投标截止 <strong>{date(card.bid_deadline_at)}</strong></span></div>{turn.answer?.focus === 'buyer' && <p>采购单位：{card.buyer_name || '未知'}</p>}{turn.answer?.focus === 'publication' && <p>公告时间：{date(card.published_at)}</p>}{turn.answer?.focus === 'documents' && <p>文件获取截止：{date(card.document_deadline_at)}</p>}{!!card.packages.length && <details><summary>{card.package_count} 个采购包</summary>{card.packages.map(p => <p key={p.label}>{p.label}：{currency(p.budget_yuan)}</p>)}</details>}{!!card.eligibility.length && <details open={turn.answer?.focus === 'eligibility'}><summary>中小企业相关原文</summary>{card.eligibility.map((text, i) => <blockquote key={i}>{text}</blockquote>)}</details>}<button className="evidence-button" onClick={() => setSelected(card)}>查看原文证据</button></div>)}
              </div>)}
              <details className="run-receipt"><summary>本轮调用记录：{turn.answer.trace.length} 个工具，{(turn.answer.elapsed_ms / 1000).toFixed(1)} 秒</summary><ol>{turn.answer.trace.map((trace, i) => <li key={i}><strong>{labels[trace.tool] || trace.tool}</strong>，匹配 {trace.matches} 条<pre>{JSON.stringify(trace.arguments, null, 2)}</pre></li>)}</ol><p>{turn.answer.model}；输入 {turn.answer.usage.input_tokens} / 输出 {turn.answer.usage.output_tokens} tokens；高峰价估算 ¥{turn.answer.usage.estimated_cost_cny_peak.toFixed(4)}</p><small>运行编号：{turn.answer.run_id}</small></details>
            </div>}
          </article>)}
          {busy && <p className="agent-thinking" role="status">正在查询公告并校验证据…</p>}<div ref={end}/>
        </div>
        <form className="chat-composer" onSubmit={event => {event.preventDefault();void send();}}><label htmlFor="agent-question">向公告库提问</label><textarea id="agent-question" value={input} maxLength={2000} disabled={busy || unavailable} onChange={e => setInput(e.target.value)} onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {event.preventDefault();void send();}}} placeholder="例如：预算低于 120 万元、尚未到投标截止时间的项目有哪些？" rows={3}/><div><small>Enter 发送，Shift + Enter 换行</small><button type="submit" disabled={busy || unavailable || !input.trim()}>{busy ? '查询中…' : '发送问题'}</button></div></form>
      </section>
      <aside className="agent-evidence" aria-label="原文证据"><h2>原文证据</h2>{!selected ? <div className="evidence-empty"><p>每个结果都应有出处。</p><p>查询后选择一条公告，在这里核对来源、引用位置和采集时间。</p></div> : <><h3>{selected.title}</h3><p className="subtle">{selected.project_code}</p><a className="official-link" href={selected.source_url} target="_blank" rel="noreferrer">打开官方公告</a><dl className="evidence-metadata"><div><dt>采购单位</dt><dd>{selected.buyer_name || '未知'}</dd></div><div><dt>公告时间</dt><dd>{date(selected.published_at)}</dd></div><div><dt>采集时间</dt><dd>{date(selected.source_observed_at)}</dd></div><div><dt>文件获取截止</dt><dd>{date(selected.document_deadline_at)}</dd></div></dl><p className="review-note">当前状态未知。未来截止时间不代表项目仍然开放。</p>{selected.citations.map(citation => <details key={citation.id} className="citation"><summary>{labels[citation.field] || citation.field}</summary><blockquote>{citation.quote}</blockquote><small>原文字符 [{citation.start}, {citation.end})</small><small>{citation.locator}</small></details>)}</>}</aside>
    </div>
  </main>;
}
