import React, { useState } from 'react';
import PipelineView from './PipelineView.jsx';
import TasksView from './TasksView.jsx';
import EvalView from './EvalView.jsx';

const VIEWS = [
  { key: 'pipeline', label: '\u6d41\u6c34\u7ebf\u76d1\u63a7', component: PipelineView },
  { key: 'tasks', label: '\u4efb\u52a1\u63a7\u5236\u53f0', component: TasksView },
  { key: 'eval', label: '\u8bc4\u6d4b\u770b\u677f', component: EvalView },
];

export default function App() {
  const [active, setActive] = useState('pipeline');
  const Active = VIEWS.find(v => v.key === active).component;
  return (
    <div className='app'>
      <header className='topbar'>
        <div className='brand'>RepoAegis <span>\u5de5\u4f5c\u53f0</span></div>
        <nav>
          {VIEWS.map(v => (
            <button key={v.key} className={active === v.key ? 'active' : ''} onClick={() => setActive(v.key)}>
              {v.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        <Active />
      </main>
    </div>
  );
}