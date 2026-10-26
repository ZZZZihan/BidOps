import { useEffect, useState } from 'react';
import { AgentWorkbench } from './AgentWorkbench';
import { Archive } from './Archive';
import { Company } from './Company';
import { Screening } from './Screening';
import { Jobs } from './Jobs';
import { Reviews } from './Reviews';
import { Ingestion } from './Ingestion';
import { DemoWorkflow, type DemoWorkflowContext } from './DemoWorkflow';
import { Audit, Login, type Identity } from './Enterprise';
import { get } from './api';
import './agent.css';

export function App() {
  const [view, setView] = useState<'workflow' | 'agent' | 'archive' | 'audit' | 'company' | 'screening' | 'jobs' | 'reviews' | 'ingestion'>('agent');
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [workflow, setWorkflow] = useState<DemoWorkflowContext | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    get<Identity>('/auth/me', controller.signal).then(value => {
      setIdentity(value);
      if (value.mode === 'enterprise' && !value.permissions.includes('agent:use')) setView('archive');
      if (value.demo?.kind === 'local_workflow') {
        setWorkflow({kind: 'local_workflow', label: value.demo.label, description: value.demo.description, seeded: false});
        setView('workflow');
      }
    }).catch(() => {if (!controller.signal.aborted) setError('无法确认登录状态，请刷新页面重试。');});
    return () => controller.abort();
  }, []);
  async function logout() {
    try {
      const response = await fetch('/api/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': identity?.csrf_token || ''}});
      if (!response.ok) throw new Error();
      window.location.assign('/');
    } catch {setError('退出未完成，请重试。');}
  }
  const enterprise = identity?.mode === 'enterprise';
  const allowed = identity && (!enterprise || identity.authenticated);
  const canChat = identity && (!enterprise || identity.permissions.includes('agent:use'));
  return <>
    <header className="app-header">
      <a className="brand" href="/">BidOps<span>采购证据工作台</span></a>
      {allowed && <nav aria-label="工作区">{workflow && <button aria-current={view === 'workflow' ? 'page' : undefined} onClick={() => setView('workflow')}>完整流程</button>}{canChat && <button aria-current={view === 'agent' ? 'page' : undefined} onClick={() => setView('agent')}>Agent 助手</button>}<button aria-current={view === 'archive' ? 'page' : undefined} onClick={() => setView('archive')}>公告档案</button>{enterprise && identity.authenticated && <><button aria-current={view === 'company' ? 'page' : undefined} onClick={() => setView('company')}>企业画像与材料</button><button aria-current={view === 'screening' ? 'page' : undefined} onClick={() => setView('screening')}>候选与资格核对</button><button aria-current={view === 'jobs' ? 'page' : undefined} onClick={() => setView('jobs')}>持久核对任务</button><button aria-current={view === 'reviews' ? 'page' : undefined} onClick={() => setView('reviews')}>人工复核</button>{(!workflow || identity.permissions.includes('source:manage')) && <button aria-current={view === 'ingestion' ? 'page' : undefined} onClick={() => setView('ingestion')}>授权更新与导入</button>}</>}{identity.permissions.includes('audit:read') && <button aria-current={view === 'audit' ? 'page' : undefined} onClick={() => setView('audit')}>运行审计</button>}</nav>}
      {enterprise && identity.authenticated && <div className="account-menu"><span>{identity.user?.name}</span><button onClick={() => void logout()}>退出工作台</button></div>}
    </header>
    {error && <p className="agent-alert" role="alert">{error}</p>}
    {!identity && !error && <p role="status" className="login-panel">正在确认工作区…</p>}
    {identity && enterprise && !identity.authenticated && <Login identity={identity} />}
    {allowed && (view === 'workflow' && workflow ? <DemoWorkflow identity={identity} context={workflow} /> : view === 'agent' && canChat ? <AgentWorkbench identity={identity} /> : view === 'audit' ? <Audit /> : view === 'company' && enterprise ? <Company identity={identity} /> : view === 'screening' && enterprise ? <Screening identity={identity} onOpenJobs={() => setView('jobs')} /> : view === 'jobs' && enterprise ? <Jobs identity={identity} /> : view === 'reviews' && enterprise ? <Reviews identity={identity} /> : view === 'ingestion' && enterprise ? <Ingestion identity={identity} /> : <Archive />)}
    <footer>根据已保存的公告提供证据。投标资格与最新状态请人工复核。</footer>
  </>;
}
