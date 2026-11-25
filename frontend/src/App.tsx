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
import { Badge, Button, Sidebar, SidebarHeader, SidebarContent, SidebarGroup, SidebarGroupLabel, SidebarMenu, SidebarMenuItem, SidebarMenuButton, SidebarFooter } from './components/ui';
import { BidOpsMark, WorkbenchIcon, type IconName } from './components/WorkbenchIcon';

type View = 'workflow' | 'agent' | 'archive' | 'audit' | 'company' | 'screening' | 'jobs' | 'reviews' | 'ingestion';
const views: Record<View, {label: string; section: string}> = {
  workflow: {label: '完整流程', section: '工作空间'}, agent: {label: 'Agent 助手', section: '工作空间'}, archive: {label: '公告档案', section: '工作空间'},
  company: {label: '企业画像与材料', section: '业务管理'}, screening: {label: '候选与资格核对', section: '业务管理'}, jobs: {label: '持久核对任务', section: '业务管理'},
  reviews: {label: '人工复核', section: '业务管理'}, ingestion: {label: '授权更新与导入', section: '系统管理'}, audit: {label: '运行审计', section: '系统管理'},
};

export function App() {
  const [view, setView] = useState<View>('agent');
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [workflow, setWorkflow] = useState<DemoWorkflowContext | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { window.scrollTo({top: 0, left: 0, behavior: 'instant'}); }, [view]);
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
  const visibleViews: View[] = allowed ? [
    ...(workflow ? ['workflow' as const] : []), ...(canChat ? ['agent' as const] : []), 'archive',
    ...(enterprise && identity.authenticated ? ['company' as const, 'screening' as const, 'jobs' as const, 'reviews' as const, ...(!workflow || identity.permissions.includes('source:manage') ? ['ingestion' as const] : [])] : []),
    ...(identity.permissions.includes('audit:read') ? ['audit' as const] : []),
  ] : [];
  return <div className="app-shell">
    <a className="skip-link" href="#workspace-content">跳到工作内容</a>
    <Sidebar className="workbench-sidebar">
      <SidebarHeader><a className="brand" href="/" aria-label="BidOps 采购证据工作台"><BidOpsMark /><span className="brand-wordmark">BidOps<small>采购证据工作台</small></span></a></SidebarHeader>
      <SidebarContent>
        <div className="workspace-label"><span className="workspace-avatar">B</span><div>{workflow ? '本机演示空间' : enterprise ? '企业工作空间' : '本机工作空间'}<small>{identity?.organization || '正在确认身份'}</small></div><WorkbenchIcon name="chevron" /></div>
        {allowed && <nav aria-label="工作区" className="workspace-nav">{['工作空间', '业务管理', '系统管理'].map(section => {
          const entries = visibleViews.filter(key => views[key].section === section);
          return entries.length > 0 && <SidebarGroup key={section}><SidebarGroupLabel>{section}</SidebarGroupLabel><SidebarMenu>{entries.map(key => <SidebarMenuItem key={key}><SidebarMenuButton isActive={view === key} aria-current={view === key ? 'page' : undefined} onClick={() => setView(key)}><WorkbenchIcon name={key as IconName} /><span>{views[key].label}</span>{view === key && <span className="nav-active-dot" aria-hidden="true" />}</SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu></SidebarGroup>;
        })}</nav>}
      </SidebarContent>
      <SidebarFooter><div className="sidebar-principle"><WorkbenchIcon name="shield" /><strong>每个判断，都有据可查</strong><p>保留原文依据与人工决定，<br />让采购评估可追溯。</p></div><div className="sidebar-signature"><span>BidOps Workspace</span><span>证据优先</span></div></SidebarFooter>
    </Sidebar>
    <div className="workspace-stage">
      <header className="workspace-topbar"><div className="workspace-breadcrumb"><span>工作台</span><WorkbenchIcon name="chevron" /><strong>{allowed ? views[view].label : '身份验证'}</strong></div><div className="workspace-topbar-actions"><Badge variant="outline"><span className="mode-dot" />{workflow ? '本机流程演示' : enterprise ? '企业模式' : identity ? '本机模式' : '连接中'}</Badge>{enterprise && identity.authenticated && <div className="account-menu"><span className="account-avatar" aria-hidden="true">{identity.user?.name?.slice(0, 1) || 'B'}</span><span>{identity.user?.name}</span><Button variant="ghost" size="sm" onClick={() => void logout()}>退出工作台</Button></div>}</div></header>
      <div id="workspace-content" className="workspace-content" tabIndex={-1}>
        {error && <p className="agent-alert" role="alert">{error}</p>}
        {!identity && !error && <p role="status" className="login-panel">正在确认工作区…</p>}
        {identity && enterprise && !identity.authenticated && <Login identity={identity} />}
        {allowed && (view === 'workflow' && workflow ? <DemoWorkflow identity={identity} context={workflow} /> : view === 'agent' && canChat ? <AgentWorkbench identity={identity} /> : view === 'audit' ? <Audit /> : view === 'company' && enterprise ? <Company identity={identity} /> : view === 'screening' && enterprise ? <Screening identity={identity} onOpenJobs={() => setView('jobs')} /> : view === 'jobs' && enterprise ? <Jobs identity={identity} /> : view === 'reviews' && enterprise ? <Reviews identity={identity} /> : view === 'ingestion' && enterprise ? <Ingestion identity={identity} /> : <Archive />)}
      </div>
      <footer className="workspace-footer"><span><WorkbenchIcon name="shield" />根据已保存的公告提供证据。投标资格与最新状态请人工复核。</span><span className="footer-wordmark">BIDOPS / EVIDENCE WORKSPACE</span></footer>
    </div>
  </div>;
}
