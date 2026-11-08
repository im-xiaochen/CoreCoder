import React from 'react';
import {render,screen,fireEvent,waitFor,cleanup,act} from '@testing-library/react';
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {ChecksPanel} from '../../../frontend/src/ChecksPanel';
const mocks=vi.hoisted(()=>({api:vi.fn(),ApiError:class ApiError extends Error {status:number;constructor(status:number){super('rejected');this.status=status;}}}));
vi.mock('../../../frontend/src/api',()=>mocks);
const config={configured:true,token:'checked-config-v1',checks:[{id:'lint',name:'Lint',command:'offline check command',cwd:'.',timeout:10,files:['*.py']}]};
const props={id:'session',writable:true,events:[],onStarted:vi.fn(async()=>{})};
function deferred(){let resolve:any,reject:any;const promise=new Promise<any>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function select(){fireEvent.click(screen.getByText('读取检查配置'));await screen.findByText('Lint (lint)');fireEvent.click(screen.getByRole('checkbox'));}
beforeEach(()=>{mocks.api.mockReset();props.onStarted.mockClear();});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

it('does not read configuration automatically and requires explicit command approval',async()=>{
 mocks.api.mockResolvedValue(config);const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<ChecksPanel {...props}/>);expect(mocks.api).not.toHaveBeenCalled();await select();
 fireEvent.change(screen.getByLabelText('可选文件路径，每行一个'),{target:{value:'one.py\n two.py'}});
 fireEvent.click(screen.getByText('授权并运行所选检查'));expect(mocks.api.mock.calls.some(([path])=>path==='checks/start')).toBe(false);
 confirm.mockReturnValue(true);fireEvent.click(screen.getByText('授权并运行所选检查'));
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('checks/start',{id:'session',check_ids:['lint'],files:['one.py','two.py'],token:'checked-config-v1',confirm:true}));
 await waitFor(()=>expect(props.onStarted).toHaveBeenCalledTimes(1));
});

it('keeps start disabled through authoritative attachment and readonly mode',async()=>{
 const attach=deferred();const started=vi.fn(()=>attach.promise);mocks.api.mockResolvedValue(config);vi.spyOn(window,'confirm').mockReturnValue(true);
 const view=render(<ChecksPanel {...props} onStarted={started}/>);await select();fireEvent.click(screen.getByText('授权并运行所选检查'));
 await waitFor(()=>expect(started).toHaveBeenCalledTimes(1));expect((screen.getByText('授权并运行所选检查') as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByText('授权并运行所选检查'));expect(mocks.api.mock.calls.filter(([path])=>path==='checks/start')).toHaveLength(1);
 view.rerender(<ChecksPanel {...props} writable={false} onStarted={started}/>);
 await act(async()=>{attach.resolve();await attach.promise;});
 expect((screen.getByText('授权并运行所选检查') as HTMLButtonElement).disabled).toBe(true);
});

it.each(['network','4xx'])('handles %s failure without an unsafe duplicate start',async(kind)=>{
 mocks.api.mockImplementation(async(path:string)=>{if(path.startsWith('checks/config'))return config;if(path==='checks/start')throw kind==='4xx'?new mocks.ApiError(403):Error('lost response');throw Error('unexpected '+path);});
 vi.spyOn(window,'confirm').mockReturnValue(true);render(<ChecksPanel {...props}/>);await select();fireEvent.click(screen.getByText('授权并运行所选检查'));
 await screen.findByRole('alert');
 await waitFor(()=>expect((screen.getByText('授权并运行所选检查') as HTMLButtonElement).disabled).toBe(kind==='network'));
 expect(props.onStarted).toHaveBeenCalledTimes(kind==='network'?1:0);
 if(kind==='network'){fireEvent.click(screen.getByText('授权并运行所选检查'));expect(mocks.api.mock.calls.filter(([path])=>path==='checks/start')).toHaveLength(1);}
});

it('loads durable history once, deduplicates event ids, displays safe logs and ignores stale record response',async()=>{
 const a='a'.repeat(32),b='b'.repeat(32),late=deferred();
 const history=[{type:'check_result',id:a,status:'failed',exit_code:7},{type:'check_result',id:b,status:'passed',exit_code:0}];
 mocks.api.mockImplementation(async(path:string)=>{if(path.startsWith('checks/history'))return history;if(path.includes('result_id='+a))return late.promise;if(path.includes('result_id='+b))return {id:b,check_id:'current',status:'passed',exit_code:0,stdout:'<img src=x onerror=alert(1)>',stderr:'raw stderr',truncated:true};throw Error('unexpected '+path);});
 const view=render(<ChecksPanel {...props} events={[history[0]] as any}/>);fireEvent.click(screen.getByText('刷新历史'));
 await screen.findByRole('button',{name:/bbbbbbbb/});expect(screen.getAllByRole('button',{name:/aaaaaaaa/})).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:/aaaaaaaa/}));fireEvent.click(screen.getByRole('button',{name:/bbbbbbbb/}));
 await screen.findByText('<img src=x onerror=alert(1)>');expect(view.container.querySelector('img')).toBeNull();
 await act(async()=>{late.resolve({id:a,check_id:'old',status:'failed',exit_code:7,stdout:'STALE OLD LOG'});await late.promise;});
 expect(screen.queryByText('STALE OLD LOG')).toBeNull();expect(screen.getByText('raw stderr')).toBeTruthy();
});
