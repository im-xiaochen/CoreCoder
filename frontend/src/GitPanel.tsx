import { useRef, useState } from 'react';
import { api } from './api';

type Entry = { path: string; old_path: string | null; index: string; worktree: string; untracked: boolean; preexisting: boolean | null; changed_during_run: boolean | null };
type Review = { branch: string; head: string | null; entries: Entry[]; complete: boolean; review_token: string };
type Diff = { text: string; truncated: boolean; binary: boolean };

export function GitPanel({ id, writable }: { id: string; writable: boolean }) {
  const [review, setReview] = useState<Review | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [diff, setDiff] = useState<(Diff & { path: string; staged: boolean }) | null>(null);
  const diffGeneration = useRef(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const refresh = async () => {
    diffGeneration.current++; setDiff(null);
    setPending(true); setError('');
    try { setReview(await api<Review>(`git/status?id=${encodeURIComponent(id)}`)); setSelected([]); setDiff(null); }
    catch (e) { setReview(null); setError(String(e)); }
    finally { setPending(false); }
  };
  const showDiff = async (path: string, staged: boolean) => {
    const generation = ++diffGeneration.current;
    setDiff(null);
    setError('');
    try { const result = await api<Diff>(`git/diff?id=${encodeURIComponent(id)}&path=${encodeURIComponent(path)}&staged=${staged}`); if (generation === diffGeneration.current) setDiff({ ...result, path, staged }); }
    catch (e) { if (generation === diffGeneration.current) { setDiff(null); setError(String(e)); } }
  };
  const mutate = async (operation: 'stage' | 'commit') => {
    if (!review || !selected.length || !writable || pending) return;
    if (!window.confirm(`${operation === 'stage' ? '暂存' : '提交'}以下路径？\n${selected.join('\n')}`)) return;
    diffGeneration.current++; setDiff(null); setPending(true); setError('');
    try {
      await api(`git/${operation}`, { id, paths: selected, review_token: review.review_token, confirm: true, message });
      await refresh();
      if (operation === 'commit') setMessage('');
    } catch (e) { setError(String(e)); setReview(null); setSelected([]); }
    finally { setPending(false); }
  };
  const toggle = (entry: Entry) => {
    const paths = [entry.path, ...(entry.old_path ? [entry.old_path] : [])];
    setSelected(current => current.includes(entry.path) ? current.filter(p => !paths.includes(p)) : [...new Set([...current, ...paths])]);
  };
  const blocking = review?.entries.filter(e => e.index !== ' ' && e.index !== '?' && !selected.includes(e.path)) || [];
  return <section>
    <h3>Git 工作区</h3>
    <button className="button secondary" disabled={pending} onClick={() => void refresh()}>刷新 Git 状态</button>
    <p>仅按需读取。任务期间变化可能来自外部编辑，不能推断归属。</p>
    {error && <p role="alert">{error}</p>}
    {review && <>
      <p>{review.branch || '分离 HEAD'} · {review.head?.slice(0, 12) || '尚无提交'} {!review.complete && '· 结果不完整，无法写入'}</p>
      {review.entries.map(entry => <div className="change-card" key={entry.path}>
        <label><input type="checkbox" checked={selected.includes(entry.path)} onChange={() => toggle(entry)} /> <code>{entry.old_path ? `${entry.old_path} → ` : ''}{entry.path}</code></label>
        <p>索引 {entry.index} / 工作树 {entry.worktree}{entry.untracked ? ' · 未跟踪' : ''} · {entry.preexisting === null ? '任务前状态未知' : entry.preexisting ? '任务前已修改' : '任务前未修改'} · {entry.changed_during_run === null ? '期间变化未知' : entry.changed_during_run ? '期间发生变化' : '期间未变化'}</p>
        {entry.index !== ' ' && entry.index !== '?' && entry.worktree !== ' ' && <p>再次暂存会包含其余工作树修改。</p>}
        <button onClick={() => void showDiff(entry.path, true)}>HEAD → 索引</button>{' '}
        <button onClick={() => void showDiff(entry.path, false)}>索引 → 工作树</button>
      </div>)}
      {diff && <><p><code>{diff.path}</code> · {diff.staged ? 'HEAD → 索引' : '索引 → 工作树'} {diff.binary ? '· 二进制文件' : ''}{diff.truncated ? ' · 差异已截断' : ''}</p><pre className="diff-lines">{diff.text}</pre></>}
      {blocking.length > 0 && <p>提交前需选择全部已暂存文件：{blocking.map(e => e.path).join('、')}</p>}
      <button className="button secondary" disabled={!writable || pending || !review.complete || !selected.length} onClick={() => void mutate('stage')}>暂存所选</button>
      <textarea aria-label="提交信息" value={message} onChange={e => setMessage(e.target.value)} placeholder="提交信息" />
      <button className="button secondary" disabled={!writable || pending || !review.complete || !selected.length || !!blocking.length || !message.trim()} onClick={() => void mutate('commit')}>提交所选暂存内容</button>
    </>}
  </section>;
}
