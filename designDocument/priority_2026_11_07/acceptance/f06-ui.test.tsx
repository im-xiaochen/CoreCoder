import React from 'react';
import {render,screen,fireEvent,waitFor,cleanup,act} from '@testing-library/react';
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {GitPanel} from '../../../frontend/src/GitPanel';
import App from '../../../frontend/src/App';
const mocks=vi.hoisted(()=>({api:vi.fn(),followRun:vi.fn(),chat:vi.fn(),setToken:vi.fn()}));
vi.mock('../../../frontend/src/api',()=>mocks);
const row=(path:string,index='M',worktree='M',old_path:string|null=null)=>({path,index,worktree,old_path,untracked:false,preexisting:true,changed_during_run:true});
const review=(entries:any[],complete=true)=>({branch:'fixture',head:'1234567890abcdef',entries,complete,review_token:'review-v1'});
function deferred(){let resolve:any,reject:any;const promise=new Promise<any>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
beforeEach(()=>{mocks.api.mockReset();mocks.followRun.mockReset();Element.prototype.scrollIntoView=vi.fn();});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});

it('requires explicit refresh and confirmation; stage sends only literal selected paths and token',async()=>{
 mocks.api.mockImplementation(async(path:string)=>path.startsWith('git/status')?review([row('space 中文.txt')]):{});
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<GitPanel id="sid" writable={true}/>);expect(mocks.api).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'刷新 Git 状态'}));await screen.findByText('space 中文.txt');
 expect(screen.getByText('再次暂存会包含其余工作树修改。')).toBeTruthy();
 fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'暂存所选'}));
 expect(mocks.api.mock.calls.some(([path])=>path==='git/stage')).toBe(false);
 expect(confirm.mock.calls[0][0]).toContain('space 中文.txt');
 confirm.mockReturnValue(true);fireEvent.click(screen.getByRole('button',{name:'暂存所选'}));
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('git/stage',{id:'sid',paths:['space 中文.txt'],review_token:'review-v1',confirm:true,message:''}));
});

it('commit blocks unselected user index then includes both rename paths after explicit selection',async()=>{
 mocks.api.mockImplementation(async(path:string)=>path.startsWith('git/status')?review([row('partial.txt'),row('renamed.txt','R',' ','old.txt')]):{});
 vi.spyOn(window,'confirm').mockReturnValue(true);render(<GitPanel id="sid" writable={true}/>);
 fireEvent.click(screen.getByText('刷新 Git 状态'));await screen.findByText('partial.txt');
 const boxes=screen.getAllByRole('checkbox');fireEvent.click(boxes[0]);
 fireEvent.change(screen.getByLabelText('提交信息'),{target:{value:'reviewed delivery'}});
 const commit=screen.getByRole('button',{name:'提交所选暂存内容'}) as HTMLButtonElement;
 expect(commit.disabled).toBe(true);fireEvent.click(commit);
 expect(mocks.api.mock.calls.some(([path])=>path==='git/commit')).toBe(false);
 fireEvent.click(boxes[1]);expect(commit.disabled).toBe(false);fireEvent.click(commit);
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('git/commit',{id:'sid',paths:['partial.txt','renamed.txt','old.txt'],review_token:'review-v1',confirm:true,message:'reviewed delivery'}));
});

it.each(['readonly','incomplete'])('disables writes for %s reviews',async(kind)=>{
 mocks.api.mockResolvedValue(review([row('a.txt')],kind!=='incomplete'));
 render(<GitPanel id="sid" writable={kind!=='readonly'}/>);fireEvent.click(screen.getByText('刷新 Git 状态'));await screen.findByText('a.txt');
 fireEvent.click(screen.getByRole('checkbox'));fireEvent.change(screen.getByLabelText('提交信息'),{target:{value:'blocked'}});
 expect((screen.getByText('暂存所选') as HTMLButtonElement).disabled).toBe(true);
 expect((screen.getByText('提交所选暂存内容') as HTMLButtonElement).disabled).toBe(true);
});

it.each(['resolve','reject'])('ignores stale diff %s after a newer review/diff',async(kind)=>{
 const late=deferred();let diffs=0;
 mocks.api.mockImplementation(async(path:string)=>{
  if(path.startsWith('git/status'))return review([row('[literal].txt')]);
  if(path.startsWith('git/diff')){diffs++;if(diffs===1)return late.promise;return {text:'current indexed content',binary:true,truncated:true};}
  throw Error('unexpected '+path);
 });
 render(<GitPanel id="sid" writable={true}/>);fireEvent.click(screen.getByText('刷新 Git 状态'));await screen.findByText('[literal].txt');
 fireEvent.click(screen.getByRole('button',{name:'索引 → 工作树'}));
 fireEvent.click(screen.getByRole('button',{name:'HEAD → 索引'}));await screen.findByText('current indexed content');
 await act(async()=>{if(kind==='resolve')late.resolve({text:'STALE DIFFERENCE'});else late.reject(Error('STALE ERROR'));await late.promise.catch(()=>{});});
 expect(screen.getByText('current indexed content')).toBeTruthy();expect(screen.queryByText('STALE DIFFERENCE')).toBeNull();expect(screen.queryByRole('alert')).toBeNull();
 expect(mocks.api.mock.calls.some(([path])=>path.includes('path=%5Bliteral%5D.txt&staged=true'))).toBe(true);
 fireEvent.click(screen.getByText('刷新 Git 状态'));await waitFor(()=>expect(screen.queryByText('current indexed content')).toBeNull());
});

it.each(['refresh','stage'])('invalidates pending diff after %s changes the review generation',async(operation)=>{
 const late=deferred();let reads=0;
 mocks.api.mockImplementation(async(path:string)=>{
  if(path.startsWith('git/status')){reads++;return {...review([row('a.txt')]),review_token:'review-'+reads};}
  if(path.startsWith('git/diff'))return late.promise;
  if(path==='git/stage')return {};
  throw Error('unexpected '+path);
 });
 vi.spyOn(window,'confirm').mockReturnValue(true);
 render(<GitPanel id="sid" writable={true}/>);fireEvent.click(screen.getByText('刷新 Git 状态'));await screen.findByText('a.txt');
 fireEvent.click(screen.getByRole('button',{name:'HEAD → 索引'}));
 if(operation==='stage'){fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByText('暂存所选'));}
 else fireEvent.click(screen.getByText('刷新 Git 状态'));
 await waitFor(()=>expect(reads).toBe(2));
 await act(async()=>{late.resolve({text:'OUTDATED REVIEW DIFF',truncated:false,binary:false});await late.promise;});
 expect(screen.queryByText('OUTDATED REVIEW DIFF')).toBeNull();
 expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
});

it('actual chat App never reads Git automatically and keeps Git writes disabled',async()=>{
 const values=new Map();const storage={getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)};
 vi.stubGlobal('localStorage',storage);Object.defineProperty(window,'localStorage',{configurable:true,value:storage});
 const session={id:'a',title:'chat session',mode:'chat',busy:false,model:'offline',updated_at:1,messages:[],timeline:[],tasks:[],pending_permission:null,tool_outcomes:[],allowed_tools:[],changes:[],undo_count:0,cwd:'E:/fixture',usage:{prompt:0,completion:0,cost:null,context:0,max_context:128000}};
 mocks.api.mockImplementation(async(path:string)=>{
  if(path==='bootstrap')return {token:'offline',version:'test',workspace:'E:/fixture',project:'fixture',model:'offline',key_configured:true,tools:[],mcp_tools:[],hooks:{pre:0,post:0},sessions:[session]};
  if(path==='sessions')return [session];if(path.startsWith('session?id='))return session;
  if(path.startsWith('git/status'))return review([row('a.txt')]);throw Error('unexpected '+path);
 });
 render(<App/>);await screen.findByRole('button',{name:/chat session/});
 fireEvent.click(screen.getByRole('button',{name:'查看工作区面板'}));await screen.findByRole('button',{name:'Git'});
 expect(mocks.api.mock.calls.some(([path])=>path.startsWith('git/'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Git'}));expect(mocks.api.mock.calls.some(([path])=>path.startsWith('git/'))).toBe(false);
 fireEvent.click(screen.getByText('刷新 Git 状态'));await screen.findByText('a.txt');fireEvent.click(screen.getByRole('checkbox'));
 expect((screen.getByText('暂存所选') as HTMLButtonElement).disabled).toBe(true);
});
