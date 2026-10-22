import { useState } from 'react';
import { AgentWorkbench } from './AgentWorkbench';
import { Archive } from './Archive';
import './agent.css';

export function App() {
  const [view, setView] = useState<'agent' | 'archive'>('agent');
  return <>
    <header className="app-header">
      <a className="brand" href="/">BidOps<span>采购证据工作台</span></a>
      <nav aria-label="工作区"><button aria-current={view === 'agent' ? 'page' : undefined} onClick={() => setView('agent')}>Agent 助手</button><button aria-current={view === 'archive' ? 'page' : undefined} onClick={() => setView('archive')}>公告档案</button></nav>
    </header>
    {view === 'agent' ? <AgentWorkbench /> : <Archive />}
    <footer>根据已保存的公告提供证据。投标资格与最新状态请人工复核。</footer>
  </>;
}
