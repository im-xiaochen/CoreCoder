import React from 'react';
import {render,screen,fireEvent,waitFor,act,cleanup} from '@testing-library/react';
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import App from '../../../frontend/src/App';
const mocks=vi.hoisted(()=>({api:vi.fn(),followRun:vi.fn(()=>new Promise(()=>{})),chat:vi.fn(),setToken:vi.fn(),ApiError:class extends Error{}}));
vi.mock('../../../frontend/src/api',()=>mocks);
const make=(id:string,mode='code')=>({id,title:'Session '+id,mode,busy:false,updated_at:1,model:'offline',messages:[],timeline:[],tasks:[],pending_permission:null,tool_outcomes:[],allowed_tools:[],changes:[],undo_count:0,cwd:'E:/fixture',usage:{prompt:0,completion:0,cost:null,context:0,max_context:128000}});
const listed={skills:[{name:'independent',command:'/independent@.corecoder/skills/independent',source:'.corecoder/skills/independent',valid:true,conflict:true}]};
function deferred(){let resolve:any,reject:any;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
let a:any,b:any,list:any,post:any;
beforeEach(()=>{const values=new Map();vi.stubGlobal('localStorage',{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)});Element.prototype.scrollIntoView=vi.fn();mocks.api.mockReset();a=make('a');b=make('b');list=deferred();post=deferred();
 mocks.api.mockImplementation(async(path:string,data:any)=>{
  if(path==='bootstrap')return {token:'offline',version:'test',workspace:'E:/fixture',project:'fixture',model:'offline',key_configured:false,tools:[],mcp_tools:[],hooks:{pre:0,post:0},sessions:[a,b]};
  if(path==='sessions')return [a,b];if(path==='session?id=a')return a;if(path==='session?id=b')return b;
  if(path.startsWith('skills?'))return list.promise;if(path==='runs')return post.promise;
  if(path==='action'){a={...a,mode:data.mode};return {session:a,notice:'mode changed',sessions:[a,b]};}
  throw Error('unexpected '+path);
 });
});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
async function open(){render(<App/>);const input=await screen.findByLabelText('输入任务');await waitFor(()=>expect((screen.getByRole('button',{name:'发送任务'}) as HTMLButtonElement).disabled).toBe(true));return input as HTMLTextAreaElement;}
function submit(input:HTMLElement,text:string){fireEvent.change(input,{target:{value:text}});fireEvent.submit(input.closest('form')!);}
it('lists metadata without POST and displays source/conflict',async()=>{
 const input=await open();submit(input,'/skills');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('skills?id=a'));
 await act(async()=>{list.resolve(listed);await list.promise;});await screen.findByText(/\.corecoder\/skills\/independent.*冲突/);
 expect(screen.getByText(/\/independent@\.corecoder\/skills\/independent/)).toBeTruthy();expect(input.value).toBe('');expect(mocks.api.mock.calls.some(([p])=>p==='runs')).toBe(false);
});
it.each(['resolve','reject'])('preserves a newer draft on stale listing %s',async(kind)=>{
 const input=await open();submit(input,'/skills');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('skills?id=a'));
 fireEvent.change(input,{target:{value:'KEEP MY NEW DRAFT'}});
 await act(async()=>{if(kind==='resolve')list.resolve(listed);else list.reject(Error('STALE LIST ERROR'));try{await list.promise;}catch{}});
 expect(input.value).toBe('KEEP MY NEW DRAFT');expect(screen.queryByText('STALE LIST ERROR')).toBeNull();expect(screen.queryByText(/\.corecoder\/skills\/independent/)).toBeNull();
});
it('ignores a listing response after switching sessions',async()=>{
 const input=await open();submit(input,'/skills');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('skills?id=a'));
 fireEvent.click(screen.getByRole('button',{name:/Session b/}));await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('session?id=b'));
 fireEvent.change(input,{target:{value:'SESSION B DRAFT'}});
 await act(async()=>{list.resolve(listed);await list.promise;});expect(input.value).toBe('SESSION B DRAFT');expect(screen.queryByText(/\.corecoder\/skills\/independent/)).toBeNull();
});
it('chat refuses listing locally without GET or POST',async()=>{
 a=make('a','chat');const input=await open();submit(input,'/skills');await screen.findByText('聊天模式不读取项目技能。');
 expect(mocks.api.mock.calls.some(([p])=>p.startsWith('skills?')||p==='runs')).toBe(false);expect(input.value).toBe('/skills');
});
it('old listing completion cannot unlock a newer pending POST',async()=>{
 const input=await open();submit(input,'/skills');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('skills?id=a'));
 submit(input,'/independent actual task');await waitFor(()=>expect(mocks.api.mock.calls.filter(([p])=>p==='runs')).toHaveLength(1));
 const send=screen.getByRole('button',{name:'发送任务'}) as HTMLButtonElement;expect(send.disabled).toBe(true);
 await act(async()=>{list.resolve(listed);await list.promise;});expect(send.disabled).toBe(true);fireEvent.submit(input.closest('form')!);
 expect(mocks.api.mock.calls.filter(([p])=>p==='runs')).toHaveLength(1);expect(input.value).toBe('/independent actual task');
});
it('mode change invalidates old listing even within the same session',async()=>{
 const input=await open();submit(input,'/skills');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('skills?id=a'));
 submit(input,'/plan');await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('action',{id:'a',action:'mode',mode:'plan'}));
 await act(async()=>{list.resolve(listed);await list.promise;});expect(screen.queryByText(/\.corecoder\/skills\/independent/)).toBeNull();
});
