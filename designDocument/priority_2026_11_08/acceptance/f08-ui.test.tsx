import React from 'react';
import {render,screen,fireEvent,waitFor,act,cleanup,within} from '@testing-library/react';
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import App from '../../../frontend/src/App';
const mocks=vi.hoisted(()=>({api:vi.fn(),followRun:vi.fn(()=>new Promise(()=>{})),chat:vi.fn(),setToken:vi.fn(),ApiError:class extends Error{}}));
vi.mock('../../../frontend/src/api',()=>mocks);
function deferred(){let resolve:any;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
const session=(id:string,mode='code')=>({id,title:'Budget '+id,mode,busy:false,updated_at:1,model:'offline',messages:[],timeline:[],tasks:[],pending_permission:null,tool_outcomes:[],allowed_tools:[],changes:[],undo_count:0,cwd:'E:/fixture',usage:{prompt:0,completion:0,cost:null,context:0,max_context:128000}});
function budget(remaining=4){return {limits:{requests:5,total_tokens:null,cost:'1',unknown_policy:'allow_unknown'},groups:{actual:{requests:1,tokens:15,known_cost:'0.01',unknown_cost_requests:0},in_flight:{requests:0,tokens:0,known_cost:'0',unknown_cost_requests:0},unconfirmed:{requests:2,tokens:100,known_cost:'0',unknown_cost_requests:2}},report:{requests_remaining:remaining,tokens_remaining:null,cost_remaining:null,cost_overage_known:'0',tokens_overage:0,unknown_cost_requests:2}};}
let a:any,b:any,runQueries:Map<string,any>,post:any;
beforeEach(()=>{
 const values=new Map();vi.stubGlobal('localStorage',{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)});Element.prototype.scrollIntoView=vi.fn();mocks.api.mockReset();mocks.followRun.mockClear();a=session('a');b=session('b');runQueries=new Map();post=deferred();
 mocks.api.mockImplementation(async(path:string,data:any)=>{
  if(path==='bootstrap')return {token:'offline',version:'test',workspace:'E:/fixture',project:'fixture',model:'offline',key_configured:false,tools:[],mcp_tools:[],hooks:{pre:0,post:0},sessions:[a,b]};
  if(path==='sessions')return [a,b];if(path==='session?id=a')return a;if(path==='session?id=b')return b;
  if(path.startsWith('run?id=')){const query=runQueries.get(path.slice(7));return query?.promise??{id:path.slice(7),status:'completed',budget:budget()};}
  if(path==='runs')return post.promise;
  if(path==='action')return {session:{...a,mode:data.mode},sessions:[a,b],notice:'mode changed'};
  throw Error('unexpected '+path);
 });
});
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();});
async function open(){render(<App/>);const input=await screen.findByLabelText('输入任务');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('session?id=a'));return input as HTMLTextAreaElement;}
function set(label:string,value:string){fireEvent.change(screen.getByLabelText(label),{target:{value}});}
function submit(input:HTMLElement,value='independent task'){fireEvent.change(input,{target:{value}});fireEvent.submit(input.closest('form')!);}
const posts=()=>mocks.api.mock.calls.filter(([path])=>path==='runs');
it('empty caps omit budget even with allow-unknown policy',async()=>{
 const input=await open();set('未知费用','allow_unknown');submit(input);await waitFor(()=>expect(posts()).toHaveLength(1));expect(posts()[0][1]).toEqual({id:'a',prompt:'independent task'});
});
it('zero remains explicit and POST configuration is frozen while editing later',async()=>{
 const input=await open();set('请求上限','1');set('Token 上限','0');set('费用上限','.125');set('未知费用','allow_unknown');submit(input);
 await waitFor(()=>expect(posts()).toHaveLength(1));const original={requests:1,total_tokens:0,cost:'.125',unknown_policy:'allow_unknown'};expect(posts()[0][1].budget).toEqual(original);
 set('请求上限','99');set('费用上限','999');expect(posts()[0][1].budget).toEqual(original);
 await act(async()=>{post.resolve({id:'run-b',session_id:'a',status:'running'});await post.promise;});
 expect(posts()).toHaveLength(1);expect(posts()[0][1].budget).toEqual(original);
});
it('invalid cost with plan approve neither changes mode nor posts',async()=>{
 a=session('a','plan');const input=await open();set('费用上限','NaN');submit(input,'/approve');await screen.findByText('预算必须是非负整数，费用必须是非负十进制数。');
 expect(posts()).toHaveLength(0);expect(mocks.api.mock.calls.some(([path])=>path==='action')).toBe(false);expect(input.value).toBe('/approve');
});
it('negative integer blocks submission',async()=>{
 const input=await open();set('请求上限','-1');submit(input);await screen.findByText('预算必须是非负整数，费用必须是非负十进制数。');expect(posts()).toHaveLength(0);
});
it('report distinguishes capped unknown from unlimited and older run unavailable',async()=>{
 a={...session('a'),run_id:'known'};b={...session('b'),run_id:'legacy'};
 runQueries.set('legacy',{promise:Promise.resolve({id:'legacy',status:'completed'})});
 await open();const report=await screen.findByLabelText('运行预算报告');expect(within(report).getByText(/剩余请求：4；剩余 Token：未设；剩余费用：未知/)).toBeTruthy();
 expect(within(report).getByText('已知实际')).toBeTruthy();expect(within(report).getByText('在途预留')).toBeTruthy();expect(within(report).getByText('未确认')).toBeTruthy();expect(within(report).getByText(/未知费用笔数：2/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:/Budget b/}));await screen.findByText('预算数据不可用');expect(screen.queryByLabelText('运行预算报告')).toBeNull();
});
it('one slow request stays authoritative and terminal stops further polling',async()=>{
 a={...session('a'),run_id:'slow'};const first=deferred();runQueries.set('slow',first);await open();await waitFor(()=>expect(mocks.api.mock.calls.filter(([path])=>path==='run?id=slow')).toHaveLength(1));
 vi.useFakeTimers();await act(async()=>{await vi.advanceTimersByTimeAsync(3200);});expect(mocks.api.mock.calls.filter(([path])=>path==='run?id=slow')).toHaveLength(1);
 await act(async()=>{first.resolve({id:'slow',status:'running',budget:budget(3)});await first.promise;});expect(screen.getByText(/剩余请求：3/)).toBeTruthy();
 const next=deferred();runQueries.set('slow',next);await act(async()=>{await vi.advanceTimersByTimeAsync(1500);});expect(mocks.api.mock.calls.filter(([path])=>path==='run?id=slow')).toHaveLength(2);
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000);next.resolve({id:'slow',status:'completed',budget:budget(2)});await next.promise;});expect(screen.getByText(/剩余请求：2/)).toBeTruthy();
 await act(async()=>{await vi.advanceTimersByTimeAsync(6000);});expect(mocks.api.mock.calls.filter(([path])=>path==='run?id=slow')).toHaveLength(2);
});
it('late budget response after session switch cannot replace target report',async()=>{
 a={...session('a'),run_id:'old'};b={...session('b'),run_id:'current'};const old=deferred();runQueries.set('old',old);await open();await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('run?id=old'));
 fireEvent.click(screen.getByRole('button',{name:/Budget b/}));await screen.findByText(/剩余请求：4/);
 await act(async()=>{old.resolve({id:'old',status:'completed',budget:budget(111)});await old.promise;});expect(screen.queryByText(/剩余请求：111/)).toBeNull();expect(screen.getByText(/剩余请求：4/)).toBeTruthy();
});
it('late old run within same session cannot overwrite newly accepted run report',async()=>{
 a={...session('a'),run_id:'old'};const old=deferred();runQueries.set('old',old);const input=await open();await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('run?id=old'));submit(input);
 await waitFor(()=>expect(posts()).toHaveLength(1));await act(async()=>{post.resolve({id:'new',session_id:'a',status:'running'});await post.promise;});await screen.findByText(/剩余请求：4/);
 await act(async()=>{old.resolve({id:'old',status:'completed',budget:budget(111)});await old.promise;});expect(screen.queryByText(/剩余请求：111/)).toBeNull();expect(screen.getByText(/剩余请求：4/)).toBeTruthy();
});
