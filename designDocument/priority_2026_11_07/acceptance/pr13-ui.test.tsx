import React from 'react';
import {render,screen,fireEvent,waitFor,cleanup,act} from '@testing-library/react';
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import App from '../../../frontend/src/App';
const mocks=vi.hoisted(()=>({api:vi.fn(),followRun:vi.fn(),chat:vi.fn(),setToken:vi.fn(),ApiError:class extends Error {}}));
vi.mock('../../../frontend/src/api',()=>mocks);
function deferred(){let resolve:any;const promise=new Promise<any>(r=>resolve=r);return {resolve,promise};}
const base={id:'session',run_id:'runA',title:'PR13 running checks',mode:'code',busy:true,model:'offline',updated_at:1,messages:[],timeline:[],tasks:[],pending_permission:null,tool_outcomes:[],allowed_tools:[],changes:[],undo_count:0,cwd:'E:/fixture',usage:{prompt:0,completion:0,cost:null,context:0,max_context:128000}};
let current:any,callbacks:any[],results:any[];
beforeEach(()=>{const stored=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(k:string)=>stored.get(k)??null,setItem:(k:string,v:string)=>stored.set(k,v),removeItem:(k:string)=>stored.delete(k),clear:()=>stored.clear()});mocks.api.mockReset();mocks.followRun.mockReset();Element.prototype.scrollIntoView=vi.fn();current={...base};callbacks=[];results=[];
 mocks.followRun.mockImplementation((rid,callback,signal)=>{callbacks.push({rid,callback,signal});return new Promise(()=>{});});
 mocks.api.mockImplementation(async(path:string)=>{
  if(path==='bootstrap')return {token:'offline',version:'test',workspace:'E:/fixture',project:'fixture',model:'offline',key_configured:true,tools:[],mcp_tools:[],hooks:{pre:0,post:0},sessions:[current]};
  if(path==='sessions')return [current];if(path.startsWith('session?id='))return current;
  if(path.startsWith('checks/result'))return results.shift();
  if(path.startsWith('checks/history'))return [];
  throw Error('unexpected '+path);
 });
});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
async function open(){render(<App/>);await screen.findByRole('button',{name:/PR13 running checks/});fireEvent.click(screen.getByRole('button',{name:'查看工作区面板'}));fireEvent.click(await screen.findByRole('button',{name:'检查'}));await waitFor(()=>expect(callbacks.length).toBe(1));}
async function event(value:any){await act(async()=>{callbacks.at(-1).callback(value);});}
it('actual App fetches authoritative running snapshots for repeated progress and renders plain text',async()=>{
 await open();results.push({stdout:'<img src=x onerror=alert(1)>',stderr:'first stderr'});
 await event({type:'check_output',id:'a'.repeat(32),run_id:'runA',session_id:'session',text:'UNTRUSTED EVENT CONTENT'});
 await screen.findByText('<img src=x onerror=alert(1)>first stderr');expect(document.querySelector('img')).toBeNull();expect(screen.queryByText('UNTRUSTED EVENT CONTENT')).toBeNull();
 results.push({stdout:'first stdout\nsecond stdout',stderr:'updated stderr'});
 await event({type:'check_output',id:'a'.repeat(32),run_id:'runA',session_id:'session'});
 await screen.findByText(/second stdoutupdated stderr/);expect(mocks.api.mock.calls.filter(([p])=>p.startsWith('checks/result'))).toHaveLength(2);
});
it.each(['check_result','done','reset'])('clears live output and rejects pending GET after %s',async(kind)=>{
 await open();results.push({stdout:'visible running log',stderr:''});await event({type:'check_output',id:'a'.repeat(32)});await screen.findByText('visible running log');
 const late=deferred();results.push(late.promise);await event({type:'check_output',id:'a'.repeat(32)});
 // Keep authority GET deferred in terminal reset/done: generation itself must invalidate old output.
 if(kind!=='check_result')mocks.api.mockImplementation(async(path:string)=>path.startsWith('session?id=')?new Promise(()=>{}):path==='sessions'?[current]:{});
 await event({type:kind,id:'a'.repeat(32),status:'failed',exit_code:7});expect(screen.queryByText('visible running log')).toBeNull();
 await act(async()=>{late.resolve({stdout:'STALE GET AFTER TERMINAL',stderr:''});await late.promise;});expect(screen.queryByText('STALE GET AFTER TERMINAL')).toBeNull();
});
