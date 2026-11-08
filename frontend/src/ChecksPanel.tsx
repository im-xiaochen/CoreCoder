import { useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { ToolEvent } from './types';

type Check = { id: string; name: string; command: string; cwd: string; timeout: number; files: string[] };
type Config = { configured: boolean; token: string | null; checks: Check[] };
type Result = { id: string; check_id: string; status: string; exit_code: number | null; stdout: string; stderr: string; truncated: boolean };
export function ChecksPanel({ id, writable, events, liveOutput, onStarted }: { id: string; writable: boolean; events: ToolEvent[]; liveOutput?: { id: string; text: string } | null; onStarted: () => Promise<void> }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [files, setFiles] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Result | null>(null);
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [history, setHistory] = useState<ToolEvent[]>([]);
  const resultGeneration = useRef(0);
  const load = async () => { setError(''); try { setConfig(await api<Config>(`checks/config?id=${encodeURIComponent(id)}`)); setSelected([]); } catch (e) { setError(String(e)); } };
  const start = async () => {
    if (!config || !selected.length || !writable || pending || uncertain) return;
    if (!window.confirm('授权执行所选项目检查？命令可能修改工作区。')) return;
    setPending(true); setError('');
    try { await api('checks/start', { id, check_ids: selected, files: files.trim() ? files.split('\n').map(s => s.trim()).filter(Boolean) : null, token: config.token, confirm: true }); setUncertain(true); await onStarted(); setUncertain(false); }
    catch (e) { setError(String(e)); if (!(e instanceof ApiError) || e.status >= 500) { setUncertain(true); void onStarted().catch(() => setError("无法确认运行状态，请刷新页面。")); } }
    finally { setPending(false); }
  };
  const show = async (resultId: string) => { const generation = ++resultGeneration.current; try { const value = await api<Result>(`checks/result?result_id=${encodeURIComponent(resultId)}`); if (generation === resultGeneration.current) setResult(value); } catch (e) { if (generation === resultGeneration.current) setError(String(e)); } };
  return <section>
    <h3>项目检查</h3><p>配置来源：.corecoder/checks.json。检查仅在明确授权后执行，不自动修复。</p>
    <button className="button secondary" onClick={() => void load()} disabled={pending}>读取检查配置</button>
    {error && <p role="alert">{error}</p>}
    {config && <>{!config.configured && <p>未配置检查。</p>}{config.checks.map(check => <div className="change-card" key={check.id}>
      <label><input type="checkbox" checked={selected.includes(check.id)} onChange={() => setSelected(v => v.includes(check.id) ? v.filter(x => x !== check.id) : [...v, check.id])} /> {check.name} ({check.id})</label>
      <pre className="diff-lines">{check.command}</pre><p>目录：{check.cwd} · 超时：{check.timeout} 秒 · 匹配：{check.files.join(', ') || '所有文件'}</p>
    </div>)}
      <label>可选文件路径，每行一个<textarea value={files} onChange={e => setFiles(e.target.value)} /></label>
      <button className="button secondary" disabled={!writable || pending || uncertain || !selected.length} onClick={() => void start()}>授权并运行所选检查</button>{uncertain && <p>启动结果不确定，请刷新页面后查看历史记录。</p>}
    </>}
    {liveOutput && <><h4>运行中输出 · {liveOutput.id.slice(0, 8)}</h4><pre className="diff-lines">{liveOutput.text}</pre></>}
    <h4>检查记录</h4><button onClick={() => { void api<ToolEvent[]>(`checks/history?id=${encodeURIComponent(id)}`).then(setHistory).catch(e => setError(String(e))); }}>刷新历史</button>{[...events.filter(e => e.type === 'check_result'), ...history].filter((e, i, all) => e.id && all.findIndex(x => x.id === e.id) === i).map(e => <p key={e.id}><button onClick={() => void show(e.id!)}>{e.id?.slice(0, 8)} · {e.status} · 退出码 {e.exit_code ?? '无'}</button></p>)}
    {result && <div><p>{result.check_id}：{result.status} · 退出码 {result.exit_code ?? '无'} {result.truncated ? '· 日志已截断' : ''}</p><pre className="diff-lines">{result.stdout}</pre><pre className="diff-lines">{result.stderr}</pre></div>}
  </section>;
}

