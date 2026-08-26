import React, { useEffect, useState, useCallback } from 'react';
import { api } from './api.js';

const PIPELINE_NODES = [
  { key: 'pending',        label: '\u5f85\u5904\u7406',         group: 'a' },
  { key: 'intake',         label: 'Intake \u89e3\u6790',    group: 'b' },
  { key: 'research',       label: 'Research \u641c\u7d22',  group: 'b' },
  { key: 'planning',       label: 'Planning \u89c4\u5212',  group: 'b' },
  { key: 'needs_approval', label: '\u5ba1\u6279\u4fe1\u5c01',       group: 'c' },
  { key: 'coding',         label: 'Coding \u7f16\u7801',    group: 'd' },
  { key: 'verifying',      label: 'Verification \u9a8c\u8bc1', group: 'd' },
  { key: 'reviewing',      label: 'Review \u5ba1\u67e5',    group: 'd' },
  { key: 'delivering',     label: 'PR \u4ea4\u4ed8',        group: 'e' },
  { key: 'completed',      label: '\u5df2\u5b8c\u6210',         group: 'f' },
  { key: 'failed',         label: '\u5931\u8d25',           group: 'f' },
];

const STATUS_ORDER = {
  pending:0, intake:1, research:2, planning:3,
  needs_approval:4, coding:5, verifying:6, reviewing:7,
  delivering:8, completed:9, failed:10,
};

function getNodeStatus(taskStatus, nodeKey) {
  const order = STATUS_ORDER[taskStatus] ?? 99;
  const nodeOrder = STATUS_ORDER[nodeKey] ?? 99;
  if (nodeKey === taskStatus) return 'active';
  if (nodeOrder < order) return 'done';
  if (nodeKey === 'failed' && taskStatus === 'failed') return 'active';
  if (nodeKey === 'completed' && taskStatus === 'completed') return 'active';
  return 'pending';
}

function PipelineNode({ node, status }) {
  const cls = status === 'failed' ? 'pipeline-node failed' : 'pipeline-node ' + status;
  return (
    <div className={cls}>
      <div className='pipeline-dot' />
      <div className='pipeline-label'>{node.label}</div>
    </div>
  );
}

function PipelineRow({ task }) {
  const taskStatus = task?.status || 'pending';
  return (
    <div className='pipeline-row'>
      <div className='pipeline-flow'>
        <div className='pipeline-track'>
          {PIPELINE_NODES.map((node, i) => (
            <React.Fragment key={node.key}>
              <PipelineNode node={node} status={getNodeStatus(taskStatus, node.key)} />
              {i < PIPELINE_NODES.length - 1 && (
                <div className={'pipeline-arrow ' + (STATUS_ORDER[node.key] < STATUS_ORDER[taskStatus] ? 'done' : '')} />
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

function TaskCard({ task }) {
  const riskColors = { low: '#15966b', medium: '#f0b429', high: '#ff7b72', critical: '#ff4444' };
  const created = new Date(task.created_at).toLocaleString('zh-CN');
  const updated = new Date(task.updated_at).toLocaleString('zh-CN');
  return (
    <div className='task-card'>
      <div className='task-card-header'>
        <span className='task-id' title={task.task_id}>{task.task_id.slice(0, 8)}...</span>
        <span className='task-repo'>{task.repo_id}</span>
        <span className={'badge ' + task.status}>{task.status}</span>
        <span className='task-risk' style={{ color: riskColors[task.risk] || '#8aa398' }}>{task.risk}</span>
      </div>
      <PipelineRow task={task} />
      <div className='task-card-meta'>
        <span>\u8fed\u4ee3: {task.iteration}</span>
        <span>\u521b\u5efa: {created}</span>
        <span>\u66f4\u65b0: {updated}</span>
      </div>
      {task.plan?.length > 0 && (
        <details className='task-details'>
          <summary>\u8ba1\u5212 ({task.plan.length} \u6b65)</summary>
          <pre className='task-plan'>{JSON.stringify(task.plan, null, 2)}</pre>
        </details>
      )}
      {task.evidence_summary?.length > 0 && (
        <details className='task-details'>
          <summary>\u8bc1\u636e ({task.evidence_summary.length} \u6761)</summary>
          <div className='task-evidence'>
            {task.evidence_summary.slice(0, 5).map((e, i) => (
              <div key={i} className='evidence-item'>
                <span className='evidence-source'>{e.source}</span>
                <span className='evidence-locator'>{e.locator}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export default function PipelineView() {
  const [tasks, setTasks] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [taskData, healthData] = await Promise.all([
        api('/tasks?limit=20'),
        api('/health').catch(() => null),
      ]);
      setTasks(taskData.items || []);
      setHealth(healthData);
      setError('');
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const activeTasks = tasks.filter(t => !['completed','failed','cancelled'].includes(t.status));
  const doneTasks = tasks.filter(t => ['completed','failed','cancelled'].includes(t.status));

  return (
    <div className='pipeline-view'>
      <div className='pipeline-header'>
        <h2>\u6d41\u6c34\u7ebf\u76d1\u63a7</h2>
        <div className='health-indicator'>
          <span className={'health-dot ' + (health?.status === 'ok' ? 'healthy' : 'unhealthy')} />
          <span>{health?.status === 'ok' ? '\u540e\u7aef\u5728\u7ebf' : '\u540e\u7aef\u79bb\u7ebf'}</span>
        </div>
      </div>
      {error && <div className='error'>{error}</div>}
      {loading && <div className='loading'>\u52a0\u8f7d\u4e2d...</div>}
      <div className='task-section'>
        <h3>\u8fd0\u884c\u4e2d ({activeTasks.length})</h3>
        {activeTasks.length === 0 && !loading && <div className='empty-state'>\u6682\u65e0\u8fd0\u884c\u4e2d\u7684\u4efb\u52a1</div>}
        {activeTasks.map(task => <TaskCard key={task.task_id} task={task} />)}
      </div>
      <div className='task-section'>
        <h3>\u5df2\u5b8c\u6210 ({doneTasks.length})</h3>
        {doneTasks.length === 0 && !loading && <div className='empty-state'>\u6682\u65e0\u5df2\u5b8c\u6210\u7684\u4efb\u52a1</div>}
        {doneTasks.map(task => <TaskCard key={task.task_id} task={task} />)}
      </div>
    </div>
  );
}