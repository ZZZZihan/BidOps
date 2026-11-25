import { useEffect, useState } from 'react';
import { get } from './api';

export type Identity = {
  mode: 'local' | 'enterprise'; authenticated: boolean; organization: string;
  user: {name: string; roles: string[]; subject_id?: string} | null; permissions: string[]; csrf_token: string | null;
  demo?: {kind: 'local_workflow'; label: string; description: string; start_view?: string};
};

export function Login({ identity }: {identity: Identity}) {
  const failed = new URLSearchParams(window.location.search).has('login_error');
  if (identity.demo?.kind === 'local_workflow') return <main className="login-panel"><p className="eyebrow">{identity.demo.label}</p><h1>进入本机演示</h1><p>{identity.demo.description}</p><p>使用本机固定示例身份体验完整流程，非公司 SSO。企业画像与证明材料为明确虚构的演示资料。</p>{failed && <p className="agent-alert" role="alert">本机演示登录未完成，请重试或检查本机服务。</p>}<a className="login-link" href="/api/auth/login">进入本机演示</a><p className="subtle">真实 AI 问答与资格核对是否开启，以登录后的运行状态为准；登录不会自动调用模型。</p></main>;
  return <main className="login-panel"><p className="eyebrow">{identity.organization} · 企业工作区</p><h1>用企业账户进入工作台</h1><p>查询采购公告、核对证据，并保存自己的分析记录。</p>
    {failed && <p className="agent-alert" role="alert">登录未完成。请确认账户拥有 BidOps 角色，或联系身份系统管理员。</p>}
    <a className="login-link" href="/api/auth/login">企业统一登录</a><p className="subtle">账户由企业身份系统管理，个人会话按账户隔离。请勿在提问中填写凭证。</p></main>;
}

type AuditEvent = {event_id: number; observed_at: number; owner_id: string | null; event: string; metadata_json: string};
export function Audit() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [more, setMore] = useState(true);
  async function load(before?: number) {
    setLoading(true);
    try {
      const result = await get<{items: AuditEvent[]}>(`/audit?limit=50${before ? `&before=${before}` : ''}`);
      setEvents(previous => before ? [...previous, ...result.items] : result.items);
      setMore(result.items.length === 50); setError('');
    } catch {setError('无法读取审计记录，请确认登录状态和权限。');}
    finally {setLoading(false);}
  }
  useEffect(() => {void load();}, []);
  return <main className="audit-panel"><div className="agent-heading"><div><h1>运行审计</h1><p>登录、权限检查与 Agent 请求记录。提问正文不出现在此视图。</p></div><button disabled={loading} onClick={() => void load()}>刷新记录</button></div>
    {error && <p role="alert" className="agent-alert">{error}</p>}
    {!loading && !events.length && <p>还没有审计记录。</p>}
    <div className="audit-table"><table><thead><tr><th>时间（北京时间）</th><th>事件</th><th>用户标识</th><th>详情</th></tr></thead><tbody>{events.map(event => <tr key={event.event_id}><td>{new Date(event.observed_at * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai'})}</td><td>{event.event}</td><td>{event.owner_id ? event.owner_id.slice(0, 12) : '系统'}</td><td><details><summary>查看记录 #{event.event_id}</summary><pre>{JSON.stringify(JSON.parse(event.metadata_json), null, 2)}</pre></details></td></tr>)}</tbody></table></div>
    {more && !!events.length && <button disabled={loading} onClick={() => void load(events[events.length - 1].event_id)}>加载更早记录</button>}</main>;
}
