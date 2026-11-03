
'use strict';
const scannedToken=location.pathname.match(/^\/v\/([A-Za-z0-9_-]{43})$/)?.[1]||null;
const scannedArt=(location.search||'').match(/[?&]art=([a-f0-9]{32})(?:&|$)/)?.[1]||null;
let scannedArtShown=false;
let token=null;
const accountState={account:null,sessions:[],epoch:0,mode:'login',loading:false,submitting:false,claiming:false,claimDismissed:false,page:'overview',controllers:new Set()};
const contentPages={gallery:0,rounds:0,photos:0,ai:0,boards:0};
let availableBoards=[];
const $=id=>document.getElementById(id);
const labels={active:'正在游玩',completed:'已完成',interrupted:'已中断',ending:'正在归档',archived:'已归档'};
const fieldLabels={score:'得分',best_combo:'最高连击',fruits_cut:'切中水果',bombs_hit:'碰到炸弹',missed:'漏切',rings:'穿环',pearls:'灵珠',lives:'剩余生命',accuracy:'准确率',rank:'评级',grade:'称号',hp:'剩余体力',enemy_hp:'对手体力',hits:'命中',blocks:'格挡',perfect_blocks:'完美格挡',kills:'击破',graze:'擦弹',elapsed:'用时',time:'游戏时间',result:'结果',perfect:'完美',good:'良好',ok:'命中',clap:'拍掌',coins:'金币',completed_stages:'通关数',enemies_defeated:'击败敌人',deaths:'失误次数',pipes_used:'进入管道',position:'名次',lap:'圈数',total_laps:'总圈数',best_lap:'最快单圈',finish_time:'完赛用时',drifts:'漂移次数',items_used:'使用道具',boosts:'加速次数',collisions:'碰撞次数'};
const node=(tag,value,className)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=String(value);if(className)n.className=className;return n;};
const number=value=>typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value):String(value??'—');
const date=value=>{const d=new Date(value);return Number.isNaN(d.getTime())?'':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});};
function setText(id,value){if($(id).textContent!==String(value))$(id).textContent=String(value);}
function download(label,url,filename){const a=node('a',label);a.href=url;a.download=filename||'';return a;}
function base(){return '/v/'+token;}
async function get(url,options={}){const controller=new AbortController(),epoch=accountState.epoch;accountState.controllers.add(controller);const external=options.signal;const abort=()=>controller.abort(external.reason);if(external?.aborted)abort();else external?.addEventListener('abort',abort,{once:true});const due=setTimeout(()=>controller.abort(),8000);try{const response=await fetch(url,{cache:'no-store',credentials:'same-origin',...options,signal:controller.signal});const value=await response.json();if(epoch!==accountState.epoch)throw new DOMException('Account changed','AbortError');if(!response.ok){const error=new Error(value.message||value.error||'暂时无法读取');error.status=response.status;error.code=value.code;throw error;}return value;}finally{clearTimeout(due);accountState.controllers.delete(controller);external?.removeEventListener('abort',abort);}}
function empty(container,title,description,symbol){if(container.dataset.empty===title)return;const card=node('div',undefined,'empty-state');card.append(node('span',symbol,'empty-symbol'),node('h3',title),node('p',description));container.replaceChildren(card);container.dataset.empty=title;}
function reconcile(container,items,key,build,update){delete container.dataset.empty;for(const child of [...container.children])if(!child.dataset.key)child.remove();const existing=new Map([...container.children].map(n=>[n.dataset.key,n]));items.forEach((item,index)=>{const id=key(item);let card=existing.get(id);if(!card){card=build(item);card.dataset.key=id;}update(card,item);if(container.children[index]!==card)container.insertBefore(card,container.children[index]||null);existing.delete(id);});for(const card of existing.values())card.remove();}
function showToast(message){setText('toast',message);$('toast').hidden=false;clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>$('toast').hidden=true,3000);}
let nameDirty=false,nameEpoch=0,lastView=null,refreshing=false,lastSuccessful=false;
let reportState={urls:[],downloadAll:null,zipAvailable:true,name:'',imageName:'',needsRename:false},viewerState=null,viewerPage=0,viewerGeneration=0,viewerBlob=null,reportSaving=false;
const reportBlobs=new Set();
function releaseReportBlob(url){if(url&&reportBlobs.delete(url))URL.revokeObjectURL(url);}
window.addEventListener('pagehide',()=>{for(const url of [...reportBlobs])releaseReportBlob(url);});
function reportNotice(message,{error=false,source=null,imageName=''}={}){setText('report-feedback',message);$('report-feedback').className='form-feedback'+(error?' error':'');const fallback=$('report-source-fallback');fallback.hidden=!source;if(source){fallback.href=source;fallback.download='墨境-未更名原始战绩.png';fallback.textContent='下载未更名源图'+(imageName?'（'+imageName+'）':'');}}
function visitorScope(){return {expectedToken:token,expectedEpoch:accountState.epoch};}
function sameVisitorScope(scope){return Boolean(scope.expectedToken&&scope.expectedToken===token&&scope.expectedEpoch===accountState.epoch);}
async function prepareReportImage(url,report,scope=visitorScope()){
  const check=()=>{if(!sameVisitorScope(scope))throw new DOMException('Account changed','AbortError');};
  check();if(!report.needsRename)return {url,renamed:false};
  const controller=new AbortController();accountState.controllers.add(controller);
  const due=setTimeout(()=>controller.abort(),30000);let source=null,canvas=null;
  try{
    const response=await fetch(url,{cache:'no-store',credentials:'same-origin',signal:controller.signal});check();
    if(!response.ok)throw new Error('原图暂时无法读取');
    const blob=await response.blob();check();
    if(typeof createImageBitmap!=='function')throw new Error('此浏览器暂不支持图片上的姓名');
    source=await createImageBitmap(blob);check();
    if(source.width!==1080||source.height<268)throw new Error('原图尺寸暂不支持姓名更新');
    canvas=document.createElement('canvas');canvas.width=source.width;canvas.height=source.height;
    const ctx=canvas.getContext('2d');if(!ctx)throw new Error('此浏览器暂不支持图片上的姓名');
    ctx.drawImage(source,0,0);ctx.fillStyle='#fffaf1';ctx.beginPath();
    const x=55,y=200,w=955,h=68,r=12;ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);ctx.quadraticCurveTo(x+w,y,x+w,y+r);ctx.lineTo(x+w,y+h-r);ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);ctx.lineTo(x+r,y+h);ctx.quadraticCurveTo(x,y+h,x,y+h-r);ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);ctx.closePath();ctx.fill();
    const text=report.name+' 的游玩战绩';let size=42;
    const font=n=>`600 ${n}px "Noto Serif CJK SC","Songti SC","SimSun",serif`;
    ctx.font=font(size);while(size>28&&ctx.measureText(text).width>930)ctx.font=font(--size);
    ctx.fillStyle='#223d3b';ctx.textBaseline='middle';ctx.fillText(text,65,234,930);
    const png=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('图片暂时无法保存')),'image/png'));check();
    const rendered=URL.createObjectURL(png);reportBlobs.add(rendered);return {url:rendered,renamed:true,blob:png};
  }finally{clearTimeout(due);accountState.controllers.delete(controller);source?.close();if(canvas){canvas.width=0;canvas.height=0;}}
}
async function downloadReportImage(url,filename,report={...reportState},button=null){
  if(reportSaving)return;const scope=visitorScope();if(!sameVisitorScope(scope))return;
  reportSaving=true;button?.setAttribute('aria-busy','true');reportNotice(report.needsRename?'正在将当前姓名写入图片，战绩内容保持原样…':'正在准备下载…');
  try{
    const prepared=await prepareReportImage(url,report,scope);
    if(!sameVisitorScope(scope)){if(prepared.renamed)releaseReportBlob(prepared.url);return;}
    const link=download('',prepared.url,filename);document.body.append(link);link.click();link.remove();
    reportNotice(prepared.renamed?'名字已保存，新战绩图片使用该名字。战绩内容保持原样。':'图片下载已交给浏览器。');
    if(prepared.renamed)setTimeout(()=>releaseReportBlob(prepared.url),60000);
  }catch(error){
    if(sameVisitorScope(scope))reportNotice('姓名图片处理未完成。'+(error.name==='AbortError'?'网络等待超时。':'网络不稳或浏览器暂不支持。')+'原始图片保留，可重试或明确下载未更名源图。',{error:true,source:url,imageName:report.imageName});
  }finally{if(sameVisitorScope(scope)){button?.removeAttribute('aria-busy');reportSaving=false;}}
}
function reportPageLink(url,index){const link=download(`第 ${index+1} 页 PNG`,url,`墨境-战绩-${index+1}.png`);link.addEventListener('click',event=>{event.preventDefault();downloadReportImage(url,link.download,{...reportState},link);});return link;}
function preview(url,title,caption,filename,originalURL=null){viewerState={urls:[url],title,caption,filename,originalURL};viewerPage=0;openViewer();}
function viewerOriginalLink(){let link=$('viewer-original');if(!link){link=node('a','打开原尺寸图片（新标签页）','button button-secondary');link.id='viewer-original';link.target='_blank';link.rel='noopener noreferrer';link.hidden=true;$('viewer-download').before(link);}return link;}
function openViewer(){const dialog=$('image-viewer');if(!dialog.open)dialog.showModal();document.body.classList.add('viewing-image');renderViewer();}
async function renderViewer(){if(!viewerState)return;const generation=++viewerGeneration;const state=viewerState;releaseReportBlob(viewerBlob);viewerBlob=null;const image=$('viewer-image');const canvas=$('viewer-canvas');canvas.hidden=true;canvas.width=0;canvas.height=0;setText('viewer-title',state.title);setText('viewer-caption',state.caption);$('viewer-loading').hidden=false;$('viewer-error').hidden=true;image.hidden=true;image.alt=state.title;
  const url=viewerState.urls[viewerPage];$('viewer-download').href=url;$('viewer-download').download=viewerState.filename||'';setText('viewer-download',viewerState.urls.length>1?'下载这一页原图 ':'下载原图 ');
  const original=viewerOriginalLink();original.hidden=!state.originalURL;if(state.originalURL)original.href=state.originalURL;else original.removeAttribute('href');
  const pager=$('viewer-pagination');pager.hidden=viewerState.urls.length<2;pager.replaceChildren();if(!pager.hidden){const previous=node('button','上一页');const next=node('button','下一页');previous.type=next.type='button';previous.disabled=viewerPage===0;next.disabled=viewerPage===viewerState.urls.length-1;previous.addEventListener('click',()=>{viewerPage--;renderViewer();});next.addEventListener('click',()=>{viewerPage++;renderViewer();});pager.append(previous,node('span',`第 ${viewerPage+1} / ${viewerState.urls.length} 页`),next);}
  let displayURL=url;if(state.report){try{const prepared=await prepareReportImage(url,state.report);if(generation!==viewerGeneration){if(prepared.renamed)releaseReportBlob(prepared.url);return;}displayURL=prepared.url;if(prepared.renamed){const bitmap=await createImageBitmap(prepared.blob);if(generation!==viewerGeneration){bitmap.close();releaseReportBlob(displayURL);return;}canvas.width=bitmap.width;canvas.height=bitmap.height;canvas.getContext('2d').drawImage(bitmap,0,0);bitmap.close();viewerBlob=displayURL;canvas.hidden=false;$('viewer-loading').hidden=true;$('viewer-download').href=displayURL;reportNotice('名字已保存，新战绩图片使用该名字。战绩内容保持原样。');return;}}catch(_){releaseReportBlob(displayURL);displayURL=url;if(generation!==viewerGeneration)return;setText('viewer-caption','当前展示未更名源图，图片上的姓名仍为“'+state.report.imageName+'”。姓名处理未完成，请联网后重试。');reportNotice('图片上的姓名暂未更新，可重试或保存未更名源图。',{error:true,source:url,imageName:state.report.imageName});}}
  image.onload=()=>{if(generation!==viewerGeneration||image.src!==new URL(displayURL,location.href).href)return;$('viewer-loading').hidden=true;image.hidden=false;};image.onerror=()=>{if(generation!==viewerGeneration)return;$('viewer-loading').hidden=true;setText('viewer-error','暂时无法读取图片，请检查网络后重试。未更名原始图片链接仍保留。');$('viewer-error').hidden=false;image.hidden=true;};if(state.report)$('viewer-download').href=displayURL;image.src=displayURL;
}
$('viewer-download').addEventListener('click',event=>{if(!viewerState?.report)return;event.preventDefault();downloadReportImage(viewerState.urls[viewerPage],`墨境-战绩-${viewerPage+1}.png`,viewerState.report,$('viewer-download'));});
$('viewer-close').addEventListener('click',()=>$('image-viewer').close());$('image-viewer').addEventListener('close',()=>{document.body.classList.remove('viewing-image');viewerGeneration++;releaseReportBlob(viewerBlob);viewerBlob=null;$('viewer-canvas').width=0;$('viewer-canvas').height=0;$('viewer-canvas').hidden=true;viewerState=null;});
$('image-viewer').addEventListener('click',event=>{if(event.target===$('image-viewer')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
$('preview-report').addEventListener('click',()=>{if(!token||!lastView)return;viewerState={urls:[...reportState.urls],report:{...reportState},title:lastView.name+' · 游玩战绩',caption:'完整战绩长图。记录更新后，关闭并重新打开可查看最新内容。',filename:'墨境-战绩.png'};viewerPage=0;openViewer();});
function configureReports(value){const info=value.report_images;const fallback=base()+'/report.png';let urls=info&&Array.isArray(info.urls)?info.urls.filter(url=>typeof url==='string'&&url.split('?')[0]===fallback):reportState.urls;if(!urls.length)urls=[fallback];const name=value.name||'游客';const imageName=typeof value.image_name==='string'?value.image_name:name;
  reportState={urls,name,imageName,needsRename:imageName!==name,zipAvailable:info?info.zip_available!==false:reportState.zipAvailable,downloadAll:typeof info?.download_all==='string'&&info.download_all===base()+'/report-images.zip'?info.download_all:reportState.downloadAll||base()+'/report-images.zip'};const allZip=urls.length>1&&reportState.zipAvailable&&!reportState.needsRename;
  const primary=$('report-download');primary.dataset.zip=String(allZip);primary.href=allZip?reportState.downloadAll:urls[0];primary.download=allZip?'墨境-全部战绩长图.zip':'墨境-战绩-1.png';setText('report-download',allZip?`下载全部战绩长图（${urls.length} 张）`:urls.length>1?'保存战绩第 1 页':'下载所有战绩长图');
  const signature=JSON.stringify(urls);if($('report-pages').dataset.signature!==signature){$('report-pages').dataset.signature=signature;$('report-pages').replaceChildren(...urls.map(reportPageLink));}$('report-pages').hidden=urls.length<2;
  setText('report-hint',urls.length>1?(allZip?`完整记录共 ${urls.length} 张长图，不会省略战绩。可逐页保存，或下载全部图片 ZIP。`:`完整记录共 ${urls.length} 页，请用上方全部逐页 PNG 入口保存，按页保留完整记录。`)+(reportState.needsRename?' 浏览器会为每一页更新当前姓名，战绩内容保持原样。':''):'PNG 图片可保存到手机相册。'+(reportState.needsRename?'预览与下载时，浏览器会更新当前姓名，战绩内容保持原样。':'预览后也可长按图片保存。'));
}
$('report-download').addEventListener('click',event=>{if(event.currentTarget.dataset.zip==='true')return;event.preventDefault();downloadReportImage(reportState.urls[0],'墨境-战绩-1.png',{...reportState},event.currentTarget);});
function artCard(art){const card=node('article',undefined,'artwork-card');const button=node('button',undefined,'artwork-image');button.type='button';const image=node('img');image.loading='lazy';button.append(image,node('span','查看作品 ','image-caption'));const info=node('div',undefined,'card-info');const heading=node('div');heading.append(node('h3'),node('p'));const original=download('下载原图 ',base()+'/art/'+art.id+'.png');info.append(heading,original);card.append(button,info);button.addEventListener('click',()=>preview(image.src,card.querySelector('h3').textContent,'作品原图，可长按保存或点击下方下载。','墨境-作品.png'));return card;}
function updateArt(card,art){const signature=JSON.stringify([art.title,art.marks,art.created]);if(card.dataset.signature===signature)return;card.dataset.signature=signature;const image=card.querySelector('img');const url=base()+'/art/'+art.id+'.png';if(image.getAttribute('src')!==url)image.src=url;image.alt=art.title||'我的作品';card.querySelector('h3').textContent=art.title||'水墨作品';card.querySelector('.card-info p').textContent=number(art.marks)+' 次落墨 · '+date(art.created);}
function roundCard(){const card=node('article',undefined,'round-card');const heading=node('div',undefined,'round-heading');heading.append(node('h3',undefined,'round-title'),node('span',undefined,'round-status'));const score=node('div',undefined,'round-score');score.append(node('strong'),node('span','排名分'));const details=node('details',undefined,'round-details');details.append(node('summary','展开各项成绩'),node('dl'));card.append(heading,node('p',undefined,'round-rule'),score,details,node('p',undefined,'round-date'));return card;}
function updateRound(card,round){const signature=JSON.stringify(round);if(card.dataset.signature===signature)return;card.dataset.signature=signature;card.querySelector('h3').textContent=round.title||'游戏';const badge=card.querySelector('.round-status');badge.textContent=labels[round.status]||'已记录';badge.className='round-status '+(round.status==='completed'?'completed':round.status==='interrupted'?'interrupted':'active');card.querySelector('.round-rule').textContent=round.rules_label||'标准玩法';card.querySelector('.round-score strong').textContent=number(round.score);const details=card.querySelector('dl');details.replaceChildren();for(const [key,label]of Object.entries(fieldLabels)){if(round.game==='kart'&&key==='rank'&&round.data?.position!==undefined)continue;const value=round.data?.[key];const displayLabel=round.game==='kart'?({rank:'名次',coins:'拾取道具',lap:round.data?.result==='finished'?'完成圈数':'当前圈数'}[key]||label):label;if(value!==undefined&&value!==null)details.append(node('dt',displayLabel),node('dd',number(value)));}if(!details.children.length)details.append(node('dt','状态'),node('dd','成绩正在记录'));card.querySelector('.round-date').textContent=(round.ended?'结束于 ':'开始于 ')+date(round.ended||round.started);}
function privateView(value,{setName=false}={}){if(!accountState.account||!token)return;lastView=value;$('private').hidden=false;$('my-link').hidden=false;$('hero-stats').hidden=false;$('account-empty').hidden=true;document.title='墨境 · '+value.name+' 的游玩报告';setText('title',value.name+' 的游玩报告');
  if(setName||!nameDirty&&document.activeElement!==$('name'))$('name').value=value.name==='游客'?'':value.name;
  setText('session-badge',labels[value.status]||'正在游玩');$('session-badge').className='badge '+(value.status==='archived'?'archived':value.status==='ending'?'ending':'');setText('session-date',date(value.started)+' 开始');setText('ranking-note',value.ranking_note||'完成一局后进入排行榜，中断记录保留在报告里。');
  const arts=Array.isArray(value.artworks)?value.artworks:[];const rounds=Array.isArray(value.rounds)?value.rounds:[];const completed=rounds.filter(r=>r.status==='completed').length;const games=new Set(rounds.map(r=>r.game));setText('art-count',arts.length);setText('round-count',completed);setText('realm-count',games.size);setText('gallery-count',arts.length+' 幅作品');setText('result-count',rounds.length+' 局记录');setText('report-name',value.name+' 的游玩报告');setText('cover-rounds',completed?completed+' 局完成':'暂无成绩');setText('cover-artworks',arts.length?arts.length+' 幅作品':'暂无作品');
  setText('archive-note',value.status==='archived'?'本次游玩已归档。作品与成绩保留在此链接，可随时下载。':value.status==='ending'?'正在保存本次游玩，请稍候。作品与成绩会保留，完成后页面自动更新。':'游玩结束时，在板卡主菜单选择“结束游玩”。此链接会保留你的作品与成绩。');
  if(scannedArt&&!scannedArtShown&&token===scannedToken){
    const art=arts.find(item=>item.id===scannedArt);
    if(art){scannedArtShown=true;preview(base()+'/art/'+art.id+'.png',art.title||'我的作品','长按图片或点击下载即可保存。','墨境-作品.png');}
  }
  if(arts.length)pageItems('gallery',arts,4,$('artworks'),$('gallery-pager'),a=>a.id,artCard,updateArt);else{empty($('artworks'),'还没有水墨作品','完成创作后，作品会保存在这次游玩中。','画');$('gallery-pager').hidden=true;}
  if(rounds.length)pageItems('rounds',[...rounds].reverse(),4,$('rounds'),$('rounds-pager'),r=>r.id,roundCard,updateRound);else{empty($('rounds'),'第一局，等你开始','完成游戏后，成绩会自动记录在这里。','游');$('rounds-pager').hidden=true;}
  configureReports(value);
  configureOtherDownloads(value);
  photoStudioSession(value);
  aiSession(value);
  applySpacePage();
}
let otherDownloads={};
function configureOtherDownloads(value){if(value.downloads&&typeof value.downloads==='object')otherDownloads={...value.downloads};const available=[['完整礼包 ZIP','zip'],['阅读报告 HTML','html'],['成绩表 CSV','csv'],['原始记录 JSON','json']].filter(([,kind])=>otherDownloads[kind]!==false);const signature=JSON.stringify(available);if($('downloads').dataset.signature!==signature){$('downloads').dataset.signature=signature;$('downloads').replaceChildren(...available.map(([label,kind])=>download(label,base()+'/report.'+kind)));}}
function boardCard(){const card=node('article',undefined,'board-card');const table=node('table');const head=node('thead');const row=node('tr');for(const label of ['名次','姓名','排名分']){const th=node('th',label);th.scope='col';row.append(th);}head.append(row);table.append(head,node('tbody'));card.append(node('h3'),node('p'),table);return card;}
function updateBoard(card,board){const signature=JSON.stringify(board);if(card.dataset.signature===signature)return;card.dataset.signature=signature;card.querySelector('h3').textContent=board.title;card.querySelector('p').textContent=board.rules_label||'标准玩法';const body=card.querySelector('tbody');body.replaceChildren();for(const item of board.items||[]){const tr=node('tr');tr.append(node('td',String(item.rank).padStart(2,'0'),'rank-number'),node('td',item.name||'游客','player-name'),node('td',number(item.score),'player-score'));body.append(tr);}}
function boardsView(value){availableBoards=Array.isArray(value.boards)?value.boards:[];const select=$('board-filter'),selected=select.value;const games=[...new Map(availableBoards.map(board=>[board.game,board.title])).entries()];const signature=JSON.stringify(games);if(select.dataset.signature!==signature){select.dataset.signature=signature;select.replaceChildren(node('option','全部游戏'));select.firstChild.value='';for(const [game,title]of games){const option=node('option',title);option.value=game;select.append(option);}select.value=games.some(([game])=>game===selected)?selected:'';}renderBoards();}
function renderBoards(){const filtered=availableBoards.filter(board=>!$('board-filter').value||board.game===$('board-filter').value);if(filtered.length)pageItems('boards',filtered,2,$('boards'),$('boards-pager'),b=>b.game+'|'+(b.rules||b.rules_label),boardCard,updateBoard);else{empty($('boards'),'暂无排行榜成绩','完成一局后显示排名，每种玩法分别统计。','榜');$('boards-pager').hidden=true;}}
function networkView(value){const host=location.hostname.toLowerCase();const privateAddress=/^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)/.test(host)||host==='[::1]'||host.endsWith('.local');const scope=value?.scope||'';const publicAccess=['public','internet','cloud'].includes(scope)&&!privateAddress||!privateAddress&&location.protocol==='https:';setText('access-badge',publicAccess?'公网访问':'局域网访问');
  const message=value?.message;setText('access-detail',message||(publicAccess?'联网即可回来下载。请保留自己的专属链接。':'当前使用局域网地址，请与板卡保持在同一网络。公网链接可在联网时访问。'));
}
function failure(error){$('connection-banner').hidden=false;setText('status',lastSuccessful?'连接暂时中断。已展开的图片与已读取的记录仍保留，恢复后会自动更新。':'暂时无法加载页面，请检查网络后重试。');$('retry').disabled=false;$('session-badge').textContent=lastSuccessful?'连接中断':'等待连接';if(error?.name!=='AbortError'&&error?.message)setText('access-detail',error.message+'。请保留当前链接。');}
async function refresh(){if(refreshing||document.hidden||!accountState.account)return;refreshing=true;const epoch=nameEpoch,accountEpoch=accountState.epoch;try{const results=await Promise.allSettled([token?get('/api/visitor/'+token):Promise.resolve(null),get('/api/leaderboards'),get('/api/network')]);if(accountEpoch!==accountState.epoch)return;let failed=false;
    if(token){if(results[0].status==='fulfilled'){if(epoch===nameEpoch)privateView(results[0].value);}else{failed=true;if(results[0].reason?.status===401){requireLogin('登录已过期，请重新登录。');return;}if(results[0].reason?.status===403){resetPrivateSession();setText('account-feedback','这次游玩无法访问，请重新扫码或选择自己的记录。');}else failure(results[0].reason);}}
    else{setText('title',(accountState.account.name||accountState.account.username)+'，你好');setText('hero-description','你的作品、成绩与灵感，分别收好。');setText('session-badge','我的账户');}
    if(results[1].status==='fulfilled')boardsView(results[1].value);else{failed=true;failure(results[1].reason);}
    if(results[2].status==='fulfilled')networkView(results[2].value);else networkView(null);
    if(!failed){lastSuccessful=true;$('connection-banner').hidden=true;setText('status','已更新');}
  }catch(error){failure(error);}finally{refreshing=false;}}
$('name').addEventListener('input',()=>{nameDirty=true;setText('name-feedback','');});
function visitorName(value){if(typeof value!=='string')throw new Error('请输入名字');const name=value.normalize('NFKC').trim();if([...name].length<1||[...name].length>24||/[\p{C}<>&]/u.test(value)||/[\p{C}<>&]/u.test(name))throw new Error('名字需为 1–24 个字，不能含控制符或 < > &');return name;}
async function saveVisitorName(rawName,{signal}={}){
  const scope=visitorScope();if(!sameVisitorScope(scope))throw new Error('请从自己的游客页面修改姓名');
  const name=visitorName(rawName),button=$('save-name');if(button.disabled)throw new Error('正在保存姓名，请稍候');
  button.disabled=true;nameEpoch++;setText('name-feedback','正在保存姓名…');$('name-feedback').className='form-feedback';
  try{
    const value=await get('/api/visitor/'+scope.expectedToken+'/name',{method:'POST',headers:{'Content-Type':'application/json','X-Ink-Visitor':'1'},body:JSON.stringify({name}),signal});
    if(!sameVisitorScope(scope))throw new DOMException('Account changed','AbortError');
    nameDirty=false;privateView(value,{setName:true});setText('name-feedback',reportState.needsRename?'名字已保存。预览或下载时会在浏览器更新图片上的姓名。':'名字已保存，新战绩图片使用该名字。');showToast('姓名已保存');
    try{const boards=await get('/api/leaderboards',{signal});if(sameVisitorScope(scope))boardsView(boards);}catch(_){/* The name was saved; rankings can refresh later. */}
    return value;
  }catch(error){if(sameVisitorScope(scope)){setText('name-feedback',error.name==='AbortError'?'连接超时，请重试保存姓名。':error.message);$('name-feedback').className='form-feedback error';}throw error;}
  finally{if(sameVisitorScope(scope))button.disabled=false;}
}
$('name-form').addEventListener('submit',async event=>{event.preventDefault();const scope=visitorScope();try{await saveVisitorName($('name').value);}catch(error){if(sameVisitorScope(scope)){setText('name-feedback',error.name==='AbortError'?'连接超时，请重试保存姓名。':error.message);$('name-feedback').className='form-feedback error';}}});
function toolInput(input,keys){if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(key=>!keys.includes(key)))throw new Error('工具输入格式无效');}
function registerVisitorTools(){const context=document.modelContext;if(!token||typeof context?.registerTool!=='function')return;const tools=[{name:'get_my_report',description:'只读查看当前游客的作品和所有游玩战绩，以及 PNG 长图、分页图片 ZIP、作品原图与其他报告的下载地址。不修改成绩或游客资料。',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},async execute(input={},options={}){toolInput(input,[]);const report=await get('/api/visitor/'+token,{signal:options.signal});return JSON.stringify({report,downloads:{png:base()+'/report.png',images:report.report_images?.zip_available===false||typeof report.image_name==='string'&&report.image_name!==report.name?null:base()+'/report-images.zip',pages:report.report_images?.urls||[base()+'/report.png'],package:report.downloads?.zip===false?null:base()+'/report.zip',html:base()+'/report.html',csv:base()+'/report.csv',json:base()+'/report.json',image_name:report.image_name||report.name,browser_title_overlay:typeof report.image_name==='string'&&report.image_name!==report.name}});}},{name:'rename_visitor',description:'明确修改当前游客的公开姓名，使用与页面“保存姓名”相同的保存动作。新的名字会用于战绩报告与排行榜；仅当用户要求改名时调用。',inputSchema:{type:'object',properties:{name:{type:'string',minLength:1,maxLength:24,description:'新的名字，1–24 个字，不能含控制符或 < > &'}},required:['name'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},async execute(input,options={}){toolInput(input,['name']);const report=await saveVisitorName(input.name,{signal:options.signal});return JSON.stringify({name:report.name,status:report.status,message:'姓名已保存'});}}];for(const tool of tools){try{Promise.resolve(context.registerTool(tool)).catch(()=>{});}catch(_){/* Experimental browser support must never break the visitor page. */}}}
// Photo uploads are explicit user actions. Polling never retries a POST: an upload
// may already have been accepted even when its HTTP response was interrupted.
const photoState={ready:false,readonly:true,file:null,previewURL:null,uploading:false,retrying:false,loading:false,jobs:[],timer:null,elapsedTimer:null,lastPoll:0,error:false,submitted:null,uploadController:null};
const photoBusy=()=>photoState.jobs.some(job=>job.status==='queued'||job.status==='processing');
function photoFeedback(message,error=false){setText('photo-upload-feedback',message);$('photo-upload-feedback').className='form-feedback photo-upload-feedback'+(error?' error':'');}
function photoControls(){
  const locked=photoState.readonly||photoState.uploading||photoState.retrying;
  $('photo-file').disabled=locked;$('photo-remove').disabled=locked;$('photo-style-group').disabled=locked;
  $('photo-submit').disabled=locked||!photoState.file||photoBusy();
  setText('photo-submit',photoState.uploading?'正在上传…':photoState.retrying?'正在读取原图…':photoBusy()?'正在处理照片':'上传并生成');
  $('photo-submit').setAttribute('aria-busy',String(photoState.uploading));
  $('photo-form').hidden=photoState.readonly;$('photo-readonly').hidden=!photoState.readonly;
  for(const card of $('photo-jobs').querySelectorAll('.photo-job')){const retry=card.querySelector('.photo-job-actions button');const job=photoState.jobs.find(item=>item.id===card.dataset.key);retry.disabled=locked||photoBusy();retry.hidden=photoState.readonly||job?.status!=='failed'||!photoURL(job,'source');card.querySelector('.photo-job-actions').hidden=retry.hidden&&card.querySelector('.photo-job-actions a').hidden;}
}
function photoReleasePreview(){if(photoState.previewURL){URL.revokeObjectURL(photoState.previewURL);photoState.previewURL=null;}$('photo-file-preview').removeAttribute('src');}
function photoClearSelection(){photoReleasePreview();photoState.file=null;$('photo-file').value='';$('photo-selection').hidden=true;photoControls();}
function photoStudioSession(value){
  if(!token)return;
  $('photo-studio').hidden=false;photoState.readonly=value.status==='archived'||value.status==='ending';
  setText('photo-readonly',value.status==='ending'?'正在归档，本页暂停接收新照片。已有作品与处理进度仍会保留。':'本次游玩已结束，可以查看和下载已有作品。要上传新照片，请在板卡主菜单重新扫码。');
  photoControls();
  if(!photoState.ready){photoState.ready=true;photoLoad();}
}
function photoURL(job,kind){
  if(!/^[A-Za-z0-9_-]{1,80}$/.test(String(job.id||'')))return null;
  const expected=base()+'/photos/'+job.id+'/'+kind+'.png';
  return job[kind+'_url']===expected?expected:null;
}
function photoImage(figure,url,alt){
  const image=figure.querySelector('img');image.alt=alt;
  const button=figure.querySelector('.photo-preview');button.disabled=!url;button.dataset.url=url||'';
  if(image.getAttribute('src')===url&&!figure.classList.contains('photo-image-error'))return;
  figure.classList.remove('photo-image-error');
  image.onload=()=>figure.classList.remove('photo-image-error');
  image.onerror=()=>figure.classList.add('photo-image-error');
  if(url)image.src=url;else image.removeAttribute('src');
}
function photoCard(job){
  const card=node('article',undefined,'photo-job');
  const heading=node('div',undefined,'photo-job-header');
  const title=node('div');title.append(node('h3'),node('p',undefined,'photo-job-meta'));
  heading.append(title,node('span',undefined,'photo-job-status'));
  const progress=node('div',undefined,'photo-job-progress');
  const activity=node('span',undefined,'photo-activity');activity.setAttribute('aria-hidden','true');
  const progressText=node('div');progressText.append(node('strong',undefined,'photo-job-stage'),node('span',undefined,'photo-job-elapsed'));
  progress.append(activity,progressText);
  const compare=node('div',undefined,'photo-comparison');
  const tabs=node('div',undefined,'photo-compare-tabs');tabs.setAttribute('aria-label','照片对照');
  const resultButton=node('button','水墨作品');resultButton.type='button';resultButton.setAttribute('aria-pressed','true');
  const sourceButton=node('button','原始照片');sourceButton.type='button';sourceButton.setAttribute('aria-pressed','false');
  const choose=source=>{card.classList.toggle('photo-show-source',source);sourceButton.setAttribute('aria-pressed',String(source));resultButton.setAttribute('aria-pressed',String(!source));};
  resultButton.addEventListener('click',()=>choose(false));sourceButton.addEventListener('click',()=>choose(true));tabs.append(resultButton,sourceButton);
  for(const [kind,label]of [['source','原始照片'],['result','水墨作品']]){
    const figure=node('figure',undefined,'photo-figure photo-figure-'+kind);const image=node('img');image.loading='lazy';image.decoding='async';
    const button=node('button',undefined,'photo-preview');button.type='button';button.disabled=true;button.setAttribute('aria-label','查看'+label+'大图');button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','image-viewer');button.append(image);
    button.addEventListener('click',()=>{const url=photoURL({id:card.dataset.key,[kind+'_url']:button.dataset.url},kind);if(!url||card.dataset.status!=='completed')return;preview(url,label,'点击下方“打开原尺寸图片”查看细节，或下载 PNG 原图。',label+'-'+card.dataset.key+'.png',url);});
    const failed=node('span','图片暂时无法读取，请刷新进度后重试。','photo-image-failed');
    figure.append(button,failed,node('figcaption',label+' · 点击查看大图'));compare.append(figure);
  }
  const error=node('p',undefined,'photo-job-error');
  const actions=node('div',undefined,'photo-job-actions');const link=download('下载作品 PNG','#');link.className='button button-secondary';
  const retry=node('button','重新生成');retry.type='button';retry.className='button button-secondary';
  retry.addEventListener('click',async()=>{
    if(photoState.readonly||photoState.uploading||photoState.retrying||photoBusy())return;
    const expectedToken=token,expectedEpoch=accountState.epoch;
    const sameSession=()=>Boolean(expectedToken&&expectedToken===token&&expectedEpoch===accountState.epoch);
    const current=photoState.jobs.find(item=>item.id===card.dataset.key);const source=current&&photoURL(current,'source');
    if(!source){photoFeedback('原图暂时无法读取，请重新选择照片。',true);return;}
    const controller=new AbortController();accountState.controllers.add(controller);
    const due=setTimeout(()=>controller.abort(),30000);
    photoState.retrying=true;retry.disabled=true;photoControls();photoFeedback('正在读取原图…');
    try{
      const response=await fetch(source,{cache:'no-store',credentials:'same-origin',signal:controller.signal});
      if(!sameSession()||controller.signal.aborted)return;
      if(!response.ok)throw new Error('原图暂时无法读取，请重新选择照片。');
      const blob=await response.blob();
      if(!sameSession()||controller.signal.aborted)return;
      if(blob.size>16*1024*1024)throw new Error('原图超过 16 MiB，请重新选择较小的照片。');
      photoState.retrying=false;
      await photoUpload(blob,current.style==='ink'?'ink':'color',{expectedToken,expectedEpoch});
    }catch(err){
      if(sameSession())photoFeedback(err.name==='AbortError'?'读取原图超时，请重试。':err.message,true);
    }finally{
      clearTimeout(due);accountState.controllers.delete(controller);
      if(sameSession()){photoState.retrying=false;retry.disabled=false;photoControls();}
    }
  });
  actions.append(link,retry);card.append(heading,progress,tabs,compare,error,actions);return card;
}
function photoUpdateCard(card,job){
  const status=job.status;const completed=status==='completed';const failed=status==='failed';const busy=status==='queued'||status==='processing';
  card.dataset.status=status;card.querySelector('h3').textContent=job.style==='ink'?'水墨作品':'青绿淡彩作品';
  const created=date(job.created||job.created_at);card.querySelector('.photo-job-meta').textContent=created||'照片创作';
  const badge=card.querySelector('.photo-job-status');badge.textContent=({queued:'等待处理',processing:'正在创作',completed:'已完成',failed:'处理未完成'})[status]||'等待更新';
  card.querySelector('.photo-job-progress').hidden=!busy;card.querySelector('.photo-job-stage').textContent=typeof job.stage==='string'&&job.stage?job.stage:status==='queued'?'正在等待处理':'正在处理照片';
  card.querySelector('.photo-job-elapsed').dataset.elapsed=String(Number.isFinite(job.elapsed_s)?Math.max(0,job.elapsed_s):0);card.querySelector('.photo-job-elapsed').dataset.received=String(performance.now());
  const result=photoURL(job,'result');const source=photoURL(job,'source');
  card.querySelector('.photo-comparison').hidden=!completed||!result;card.querySelector('.photo-compare-tabs').hidden=!completed||!result;
  if(completed&&result){photoImage(card.querySelector('.photo-figure-source'),source,'上传的原始风景照片');photoImage(card.querySelector('.photo-figure-result'),result,job.style==='ink'?'生成的水墨作品':'生成的青绿淡彩作品');card.querySelector('.photo-figure-source').hidden=!source;card.querySelector('.photo-compare-tabs button:last-child').disabled=!source;}
  const error=card.querySelector('.photo-job-error');error.hidden=!failed;error.textContent=failed?(typeof job.error==='string'&&job.error?job.error:'这张照片未能完成处理，原图已保留。可以重试或选择其他照片。'):'';
  const link=card.querySelector('.photo-job-actions a');link.hidden=!completed||!result;if(result){link.href=result;link.download='照片水墨-'+job.id+'.png';}
  const retry=card.querySelector('.photo-job-actions button');retry.hidden=!failed||photoState.readonly||!source;retry.disabled=photoState.uploading||photoState.retrying||photoBusy();card.querySelector('.photo-job-actions').hidden=link.hidden&&retry.hidden;
}
function photoElapsed(){
  if(document.hidden)return;
  for(const card of $('photo-jobs').children){if(!['queued','processing'].includes(card.dataset.status))continue;const span=card.querySelector('.photo-job-elapsed');if(!span)continue;const seconds=Math.max(0,Math.floor(Number(span.dataset.elapsed)+(performance.now()-Number(span.dataset.received))/1000));const text=seconds<60?seconds+' 秒':Math.floor(seconds/60)+' 分 '+String(seconds%60).padStart(2,'0')+' 秒';span.textContent='已用时 '+text;}
}
function photoSchedule(){clearTimeout(photoState.timer);photoState.timer=null;if(!photoState.ready||document.hidden)return;photoState.timer=setTimeout(photoLoad,photoBusy()?2000:10000);}
async function photoLoad(){
  if(!token||!photoState.ready||photoState.loading||document.hidden)return;
  const epoch=accountState.epoch;photoState.loading=true;$('photo-refresh').disabled=true;
  try{const value=await get('/api/visitor/'+token+'/photos');if(!Array.isArray(value.jobs))throw new Error('照片进度格式有误，请稍后重试。');
    photoState.jobs=value.jobs.filter(job=>job&&typeof job==='object'&&/^[A-Za-z0-9_-]{1,80}$/.test(String(job.id||'')));photoState.error=false;photoState.lastPoll=performance.now();
    setText('photo-load-feedback','');$('photo-load-feedback').className='form-feedback';
    photoRenderJobs();
    const submitted=photoState.jobs.find(job=>job.id===photoState.submitted);
    if(!photoState.file&&submitted?.status==='completed'){photoFeedback('作品已完成，可以对照原图并下载 PNG。');photoState.submitted=null;}
    else if(!photoState.file&&submitted?.status==='failed'){photoFeedback('这张照片未能完成处理，请查看下方说明后重试。',true);photoState.submitted=null;}
    photoElapsed();
  }catch(error){if(epoch===accountState.epoch){photoState.error=true;setText('photo-load-feedback',error.name==='AbortError'?'读取进度超时。照片任务可能仍在处理，请稍后刷新。':'暂时无法读取照片进度。请确认手机与板卡仍在同一网络，再点击刷新进度。');$('photo-load-feedback').className='form-feedback error';}}
  finally{if(epoch===accountState.epoch){photoState.loading=false;$('photo-refresh').disabled=false;photoControls();photoSchedule();}}
}
async function photoUpload(file,style,{expectedToken,expectedEpoch}={}){
  const sameSession=()=>Boolean(expectedToken&&expectedToken===token&&expectedEpoch===accountState.epoch);
  if(!sameSession()||photoState.readonly||photoState.uploading||photoBusy())return;
  photoState.uploading=true;photoControls();photoFeedback('正在上传照片，请保持与板卡连接…');
  const controller=new AbortController();photoState.uploadController=controller;accountState.controllers.add(controller);const due=setTimeout(()=>controller.abort(),30000);
  try{
    if(!sameSession()||controller.signal.aborted)return;
    const response=await fetch('/api/visitor/'+expectedToken+'/photos?style='+style,{method:'POST',credentials:'same-origin',headers:{'Content-Type':file.type||'application/octet-stream','X-Ink-Visitor':'1'},body:file,signal:controller.signal,cache:'no-store'});let value;try{value=await response.json();}catch(_){throw new Error('板卡暂时没有返回有效结果，请先刷新进度，确认是否已接收照片。');}
    if(!sameSession()||controller.signal.aborted)return;
    if(!response.ok)throw new Error(value.message||value.error||(response.status===409?'板卡正在处理另一张照片，请稍后再试。':'上传未完成，请稍后重试。'));
    if(!value.job||typeof value.job!=='object'||!/^[A-Za-z0-9_-]{1,80}$/.test(String(value.job.id||'')))throw new Error('照片可能已经接收，请刷新进度确认。');
    photoFeedback('照片已接收，正在生成作品。');photoClearSelection();
    const job=value.job;photoState.submitted=job.id;photoState.jobs=[job,...photoState.jobs.filter(item=>item.id!==job.id)];
    photoRenderJobs();photoElapsed();
  }catch(error){if(sameSession())photoFeedback(error.name==='AbortError'?'上传等待超时。请先刷新进度，确认照片是否已接收，再决定是否重试。':error.message,true);}
  finally{clearTimeout(due);accountState.controllers.delete(controller);if(sameSession()){photoState.uploadController=null;photoState.uploading=false;photoControls();photoLoad();}}
}
$('photo-file').addEventListener('change',()=>{
  const file=$('photo-file').files?.[0];photoReleasePreview();photoState.file=null;$('photo-selection').hidden=true;
  if(!file){photoFeedback('');photoControls();return;}
  const validType=['image/jpeg','image/png','image/webp'].includes(file.type)||!file.type&&/\.(jpe?g|png|webp)$/i.test(file.name);
  if(!validType||file.size<=0||file.size>16*1024*1024){$('photo-file').value='';photoFeedback(!validType?'请选择 JPG、PNG 或 WebP 照片。':file.size<=0?'这张图片是空文件，请重新选择。':'这张照片超过 16 MiB，请选择较小的照片。',true);photoControls();return;}
  photoState.file=file;photoState.previewURL=URL.createObjectURL(file);$('photo-file-preview').src=photoState.previewURL;setText('photo-file-name',file.name);setText('photo-file-size',(file.size/(1024*1024)).toFixed(1)+' MiB');$('photo-selection').hidden=false;photoFeedback('照片已选好，点击“上传并生成”开始。');photoControls();
});
$('photo-remove').addEventListener('click',()=>{photoClearSelection();photoFeedback('');$('photo-file').click();});
$('photo-form').addEventListener('submit',event=>{event.preventDefault();if(photoState.file){const selected=document.querySelector('input[name="photo-style"]:checked');photoUpload(photoState.file,selected?.value==='ink'?'ink':'color',{expectedToken:token,expectedEpoch:accountState.epoch});}});
$('photo-refresh').addEventListener('click',()=>photoLoad());
photoState.elapsedTimer=setInterval(photoElapsed,1000);
window.addEventListener('pagehide',()=>{photoReleasePreview();clearTimeout(photoState.timer);clearInterval(photoState.elapsedTimer);});
window.addEventListener('pageshow',event=>{if(event.persisted){if(photoState.file){photoState.previewURL=URL.createObjectURL(photoState.file);$('photo-file-preview').src=photoState.previewURL;}clearInterval(photoState.elapsedTimer);photoState.elapsedTimer=setInterval(photoElapsed,1000);photoLoad();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(photoState.timer);photoState.timer=null;}else photoLoad();});
window.addEventListener('online',()=>photoLoad());
// AI requests are explicit, bounded user submissions. Every timer below only
// reads existing jobs; an uncertain POST is never silently retried.
const aiState={ready:false,active:false,serverActive:null,capabilities:null,kind:'guide',project:'',preset:'',healthSummary:{},cleared:false,jobs:[],loading:false,sending:false,clearing:false,epoch:0,loadController:null,pageHidden:false,timer:null,historyLoaded:false,seen:new Map(),animations:new Map(),frame:null};
const aiReducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
const aiBusy=()=>aiState.jobs.some(job=>job.status==='queued'||job.status==='running');
const aiProjects=()=>Array.isArray(aiState.capabilities?.projects)?aiState.capabilities.projects.filter(item=>item&&typeof item.id==='string'&&typeof item.title==='string'):[];
const aiProject=()=>aiProjects().find(item=>item.id===aiState.project);
function aiFeedback(message,error=false){setText('ai-feedback',message);$('ai-feedback').className='form-feedback'+(error?' error':'');}
function aiThinking(){
  const waiting=Boolean((aiState.sending||aiBusy())&&!aiState.clearing);
  $('ai-thinking').hidden=!waiting;
  $('ai-thinking').classList.toggle('is-thinking',waiting&&!document.hidden&&!aiState.pageHidden&&!aiReducedMotion.matches);
}
function aiControls(){
  const active=(aiState.active&&aiState.serverActive)||(aiState.kind==='health'&&aiState.capabilities?.health_history===true);
  const configured=aiState.capabilities?.configured===true;
  const busy=aiState.sending||aiState.clearing,locked=!active||busy;
  $('ai-readonly').hidden=aiState.active&&aiState.serverActive!==false;
  setText('ai-readonly','本次游玩已结束，已有建议和固定说明仍可阅读。训练建议可根据本账户的已保存跟练记录生成，其他新建议需重新扫码开始游玩。');
  $('ai-form').hidden=aiState.composeExpanded===false;
  $('ai-compose-toggle').hidden=false;
  $('ai-compose-toggle').setAttribute('aria-expanded',String(aiState.composeExpanded!==false));
  setText('ai-compose-toggle',aiState.composeExpanded===false?'再问一条 / 更换问题 ＋':'收起提问区域 ⌃');
  for(const button of $('ai-kind').querySelectorAll('[data-kind]')){button.disabled=busy;button.hidden=button.dataset.kind==='guide'&&!aiProjects().length;button.setAttribute('aria-pressed',String(button.dataset.kind===aiState.kind&&!(aiState.kind==='guide'&&aiState.project==='system')));}
  $('ai-system-entry').disabled=busy;$('ai-system-entry').setAttribute('aria-pressed',String(aiState.kind==='guide'&&aiState.project==='system'));
  for(const button of $('ai-presets').querySelectorAll('button')){button.disabled=locked;button.setAttribute('aria-pressed',String(button.dataset.preset===aiState.preset));}
  $('ai-text').disabled=locked;
  $('ai-project').disabled=busy;
  $('ai-local-help').disabled=!aiProject()?.help||aiState.clearing;
  $('ai-submit').disabled=locked||!configured||aiBusy()||!aiState.preset||(['guide','health'].includes(aiState.kind)&&!aiProject());
  $('ai-submit').setAttribute('aria-busy',String(aiState.sending));
  setText('ai-submit',aiState.sending?'正在发送…':aiBusy()?'正在生成建议':'生成建议');
  setText('ai-service-state',!aiState.capabilities?'正在连接':configured?'DeepSeek 已就绪':'暂未启用');
  if(aiState.capabilities&&!configured)setText('ai-load-feedback','AI 助手尚未配置，已有结果仍可阅读。');
  $('ai-clear').disabled=!accountState.account||aiState.clearing||aiState.sending;
  $('ai-refresh').disabled=aiState.loading||aiState.sending||aiState.clearing;
  setText('ai-clear',aiState.clearing?'正在清空…':'清空我的AI建议');
  aiThinking();
}
function aiProjectOptions(){
  const projects=aiProjects().filter(item=>aiState.kind!=='health'||['baduanjin','taichi'].includes(item.id)),signature=JSON.stringify(projects.map(({id,title,section})=>({id,title,section})));
  if(!projects.some(item=>item.id===aiState.project))aiState.project=(aiState.kind==='guide'?projects.find(item=>item.id!=='system'):projects[0])?.id||projects[0]?.id||'';
  if($('ai-project').dataset.signature!==signature){
    $('ai-project').dataset.signature=signature;$('ai-project').replaceChildren();
    for(const section of ['system','entertainment','health','creation']){
      const group=node('optgroup');group.label=({system:'了解墨境',entertainment:'娱乐',health:'健康',creation:'创作'})[section];
      for(const item of projects.filter(item=>item.section===section)){const option=node('option',item.title);option.value=item.id;group.append(option);}
      if(group.children.length)$('ai-project').append(group);
    }
    $('ai-help').hidden=true;
  }
  $('ai-project').value=aiState.project;
  $('ai-project-area').hidden=!['guide','health'].includes(aiState.kind);
  if(!['guide','health'].includes(aiState.kind))$('ai-help').hidden=true;
  aiHealthSummary();
}
function aiHealthSummary(){
  $('ai-health-summary').hidden=aiState.kind!=='health';
  if(aiState.kind!=='health')return;
  const summary=aiState.healthSummary?.[aiState.project];
  setText('ai-health-title',summary?.course_title||aiProject()?.title||'课程记录');
  setText('ai-health-evidence',summary?.summary||(summary?.records_present?'已保存跟练记录，建议仅依据可观察到的练习情况。':'暂无本账户的跟练记录。可以先看课程示范，再完成一次跟练并返回保存。'));
  setText('ai-health-scope',summary?.records_present?'仅使用明确关联到本账户的练习记录。关节不可见不代表动作错误；建议不作身体诊断或疗效判断。':'旧的匿名练习不会自动关联到你的账户。暂无记录时，AI只说明如何开始，不会假设你完成了训练。');
}
function aiShowHelp(){
  const help=(aiState.kind==='health'?aiState.healthSummary?.[aiState.project]?.local_help:null)||aiProject()?.help;if(!help||typeof help!=='object')return;
  setText('ai-help-title',typeof help.title==='string'?help.title:aiProject().title);
  setText('ai-help-summary',typeof help.summary==='string'?help.summary:'');
  $('ai-help-tips').replaceChildren();
  if(Array.isArray(help.tips))for(const tip of help.tips)if(typeof tip==='string')$('ai-help-tips').append(node('li',tip));
  $('ai-help').hidden=false;
}
function aiPresets(){
  if(aiState.kind==='guide'&&!aiProjects().length)aiState.kind='creation';
  aiProjectOptions();
  const list=aiState.capabilities?.presets?.[aiState.kind];
  const choices=(Array.isArray(list)?list.filter(item=>item&&typeof item.id==='string'&&typeof item.title==='string'):[]).map(item=>aiState.kind==='guide'&&aiState.project==='system'?{...item,title:({rules:'项目概览',controls:'技术原理',tips:'应用场景'})[item.id]||item.title,description:({rules:'板卡上的三大板块',controls:'摄像头、推理与交互流水线',tips:'展示、娱乐、健康与创作'})[item.id]||item.description}:item);
  if(!choices.some(item=>item.id===aiState.preset))aiState.preset=choices[0]?.id||'';
  reconcile($('ai-presets'),choices,item=>item.id,item=>{const button=node('button',undefined,'ai-preset');button.type='button';button.dataset.preset=item.id;button.append(node('strong'),node('span'));button.addEventListener('click',()=>{aiState.preset=item.id;aiControls();});return button;},(button,item)=>{button.querySelector('strong').textContent=item.title;button.querySelector('span').textContent=typeof item.description==='string'?item.description:'';});
  $('ai-text').placeholder=aiState.kind==='creation'?'例如：我想画一幅有山、有竹的作品。':aiState.kind==='health'?'例如：我刚练过第一式，接下来适合复习哪些内容？':aiState.kind==='guide'?'例如：这个项目怎么操作？第一次玩应该注意什么？':'例如：我想练习更稳定地击中鼓点。';
  aiControls();
}
function aiSession(value){
  if(!token)return;
  $('ai-assistant').hidden=false;aiState.active=value.status==='active';
  aiControls();
  if(!aiState.ready){aiState.ready=true;aiLoad();}
}
// Presentation starts only after a complete, validated ready result. This is
// local text reveal, not API token streaming. Two fixed nodes per field provide
// a small luminous text tail; no per-character elements or automatic scrolling.
function aiFinishReveal(card){
  const animation=aiState.animations.get(card);if(!animation)return;
  for(const field of animation.fields){field.el.textContent=field.chars.join('');field.el.classList.remove('ai-writing');field.el.hidden=false;}
  aiState.animations.delete(card);card.dataset.revealing='false';
  card.querySelector('.ai-show-all').hidden=true;card.querySelector('.ai-reveal-label').textContent='DeepSeek 建议 · 本地校验完成';
  if(!aiState.animations.size&&aiState.frame!==null){cancelAnimationFrame(aiState.frame);aiState.frame=null;}
}
function aiPauseReveal(){
  if(aiState.frame!==null)cancelAnimationFrame(aiState.frame);aiState.frame=null;
  for(const animation of aiState.animations.values()){animation.last=null;for(const field of animation.fields)field.el.classList.remove('ai-writing');}
}
function aiPumpReveal(now){
  aiState.frame=null;if(document.hidden||aiState.pageHidden)return;
  for(const [card,animation] of aiState.animations){
    if(!card.isConnected){aiFinishReveal(card);continue;}
    if(aiReducedMotion.matches){aiFinishReveal(card);continue;}
    if(animation.last===null){animation.last=now;continue;}
    const elapsed=now-animation.last;if(elapsed<28)continue;
    animation.last=now;animation.credit+=Math.min(elapsed,80)*animation.rate/1000;
    let letters=Math.floor(animation.credit);animation.credit-=letters;
    while(letters>0&&animation.index<animation.fields.length){
      const field=animation.fields[animation.index],count=Math.min(letters,field.chars.length-field.shown);
      field.shown+=count;letters-=count;field.el.hidden=false;field.el.classList.add('ai-writing');
      const tailStart=Math.max(0,field.shown-4);
      field.prefix.data=field.chars.slice(0,tailStart).join('');field.tail.textContent=field.chars.slice(tailStart,field.shown).join('');
      if(field.shown===field.chars.length){field.el.textContent=field.chars.join('');field.el.classList.remove('ai-writing');animation.index++;}
    }
    if(animation.index===animation.fields.length)aiFinishReveal(card);
  }
  aiResumeReveal();
}
function aiResumeReveal(){if(!document.hidden&&!aiState.pageHidden&&aiState.animations.size&&aiState.frame===null)aiState.frame=requestAnimationFrame(aiPumpReveal);}
function aiBeginReveal(card,fields){
  const items=fields.filter(field=>field.text.length).map(field=>({el:field.el,chars:[...field.text],shown:0,prefix:document.createTextNode(''),tail:node('span','','ai-type-tail')}));
  if(!items.length)return;
  for(const field of items){field.el.replaceChildren(field.prefix,field.tail);field.el.hidden=true;}
  aiState.animations.set(card,{fields:items,index:0,credit:0,last:null,rate:Math.max(34,items.reduce((sum,field)=>sum+field.chars.length,0)/8)});
  card.dataset.revealing='true';card.querySelector('.ai-show-all').hidden=false;
  card.querySelector('.ai-reveal-label').textContent='内容已生成并校验 · 正在逐字展示';aiResumeReveal();
}
function aiCard(){
  const card=node('article',undefined,'ai-job'),heading=node('div',undefined,'ai-job-heading'),reveal=node('div',undefined,'ai-reveal-bar');
  heading.append(node('span',undefined,'ai-job-kind'),node('span',undefined,'ai-job-status'));
  const showAll=node('button','立即显示全文','ai-show-all');showAll.type='button';showAll.hidden=true;showAll.addEventListener('click',()=>aiFinishReveal(card));
  reveal.append(node('span','','ai-reveal-label'),showAll);
  card.append(heading,reveal,node('h4'),node('p',undefined,'ai-job-summary'),node('ol',undefined,'ai-job-steps'),node('p',undefined,'ai-job-date'));return card;
}
function aiUpdate(card,job){
  const signature=JSON.stringify(job);if(card.dataset.signature===signature)return;card.dataset.signature=signature;
  const state=['queued','running','ready','error'].includes(job.status)?job.status:'error';card.dataset.status=state;
  card.querySelector('.ai-job-kind').textContent=['guide','health'].includes(job.kind)?((aiProjects().find(item=>item.id===job.project)?.title||'项目')+(job.kind==='health'?' · 训练建议':' · AI 指南')):job.kind==='review'?'成绩复盘':'创作建议';
  card.querySelector('.ai-job-status').textContent=({queued:'等待生成',running:'正在生成',ready:'已完成',error:'未能完成'})[state];
  const result=job.result&&typeof job.result==='object'?job.result:{};
  card.querySelector('.ai-job-date').textContent=date(job.created)+(job.cache_hit?' · 已有建议复用':'');
  const contentSignature=JSON.stringify({state,result,error:job.error,kind:job.kind});
  if(card.dataset.contentSignature===contentSignature)return;card.dataset.contentSignature=contentSignature;
  aiFinishReveal(card);const fields=[];
  const write=(el,text)=>{el.hidden=false;el.textContent=text;fields.push({el,text});};
  write(card.querySelector('h4'),state==='ready'&&typeof result.title==='string'?result.title:state==='error'?'这次未能生成建议':'DeepSeek 正在整理建议');
  write(card.querySelector('.ai-job-summary'),state==='ready'&&typeof result.summary==='string'?result.summary:state==='error'?(typeof job.error==='string'?job.error:'暂时无法生成，请稍后再试。'):'无需停留在本页，完成后可回来阅读。');
  const steps=card.querySelector('.ai-job-steps');steps.replaceChildren();
  if(state==='ready'&&job.kind==='creation'&&Array.isArray(result.steps))for(const step of result.steps.slice(0,4)){if(!step||typeof step!=='object')continue;const item=node('li'),label=node('strong'),description=node('p');const theme=({landscape:'山水',bamboo:'竹',plum:'梅',mixed:'自由搭配'})[step.theme]||'创作';const title=[theme,step.action].filter(value=>typeof value==='string'&&value).join(' · ');write(label,title);write(description,typeof step.instruction==='string'?step.instruction:'');item.append(label,description);steps.append(item);}
  if(state==='ready'&&['review','guide','health'].includes(job.kind)&&Array.isArray(result.tips))for(const tip of result.tips){if(typeof tip==='string'){const item=node('li');write(item,tip);steps.append(item);}}
  steps.hidden=!steps.children.length;
  card.querySelector('.ai-reveal-bar').hidden=state!=='ready';card.querySelector('.ai-reveal-label').textContent='DeepSeek 建议 · 本地校验完成';
  const animate=state==='ready'&&aiState.historyLoaded&&aiState.seen.get(job.id)!=='ready'&&!job.cache_hit&&!aiReducedMotion.matches;
  aiState.seen.set(job.id,state);while(aiState.seen.size>256)aiState.seen.delete(aiState.seen.keys().next().value);
  if(animate)aiBeginReveal(card,fields);
}
function aiRenderJobs(){const pager=contentPager('ai-pager',$('ai-jobs')),jobs=aiState.jobs.filter(job=>job.kind===aiState.kind&&(!['guide','health'].includes(aiState.kind)||job.project===aiState.project));setText('ai-results-title',({guide:aiState.project==='system'?'项目介绍':'项目指南',creation:'创作方案',review:'成绩复盘',health:'训练建议'})[aiState.kind]+' · 我的建议');if(jobs.length)pageItems('ai',jobs,3,$('ai-jobs'),pager,job=>job.id,aiCard,aiUpdate);else{empty($('ai-jobs'),aiState.cleared?'AI历史已清空':'这个分类还没有AI建议','选择上方内容分类，再生成一条新建议。固定说明不会计入AI历史。','AI');pager.hidden=true;}for(const card of aiState.animations.keys())if(!card.isConnected)aiFinishReveal(card);}
function aiSchedule(){clearTimeout(aiState.timer);aiState.timer=null;if(token&&!document.hidden&&!aiState.pageHidden&&!aiState.clearing)aiState.timer=setTimeout(aiLoad,aiBusy()?3500:20000);}
async function aiLoad(){
  if(!token||aiState.loading||aiState.sending||aiState.clearing||document.hidden||aiState.pageHidden)return;
  const epoch=aiState.epoch,controller=new AbortController();aiState.loadController=controller;
  aiState.loading=true;$('ai-refresh').disabled=true;
  try{const value=await get('/api/visitor/'+token+'/ai',{signal:controller.signal});
    if(epoch!==aiState.epoch)return;
    if(!value||!Array.isArray(value.jobs)||!value.capabilities||typeof value.capabilities!=='object')throw new Error('invalid');
    aiState.capabilities=value.capabilities;aiState.serverActive=value.active===true;aiState.healthSummary=value.health_summary||{};
    aiState.jobs=value.jobs.filter(job=>job&&typeof job==='object'&&/^[a-f0-9]{32}$/.test(String(job.id||''))).slice(0,20);
    setText('ai-load-feedback','');$('ai-load-feedback').className='form-feedback';aiPresets();aiRenderJobs();aiState.historyLoaded=true;
  }catch(_){if(epoch===aiState.epoch){setText('ai-load-feedback','暂时无法读取 AI 建议。已有结果和已载入的本地说明仍保留，可稍后刷新。');$('ai-load-feedback').className='form-feedback error';}}
  finally{if(epoch===aiState.epoch){aiState.loading=false;aiState.loadController=null;$('ai-refresh').disabled=false;aiControls();aiSchedule();}}
}
for(const button of $('ai-kind').querySelectorAll('[data-kind]'))button.addEventListener('click',()=>{aiState.kind=button.dataset.kind;aiState.preset='';aiState.composeExpanded=true;contentPages.ai=0;$('ai-help').hidden=true;if(aiState.kind==='guide'&&aiState.project==='system')aiState.project=aiProjects().find(item=>item.id!=='system')?.id||'';aiPresets();aiRenderJobs();aiFeedback('');});
$('ai-project').addEventListener('change',()=>{aiState.project=$('ai-project').value;contentPages.ai=0;$('ai-help').hidden=true;aiPresets();aiRenderJobs();aiFeedback('');});
$('ai-local-help').addEventListener('click',aiShowHelp);
$('ai-help-close').addEventListener('click',()=>{$('ai-help').hidden=true;});
$('ai-text').addEventListener('input',()=>setText('ai-text-count',[...$('ai-text').value].length+' / 280 字'));
$('ai-form').addEventListener('submit',async event=>{
  event.preventDefault();if($('ai-submit').disabled)return;
  const text=$('ai-text').value.trim();if([...text].length>280){aiFeedback('补充说明最多 280 字。',true);return;}
  const epoch=++aiState.epoch,payload={kind:aiState.kind,preset:aiState.preset,text};
  aiState.loadController?.abort();aiState.loadController=null;aiState.loading=false;
  if(['guide','health'].includes(aiState.kind))payload.project=aiState.project;
  aiState.sending=true;aiControls();aiFeedback('正在发送，请稍候…');
  try{const value=await get('/api/visitor/'+token+'/ai',{method:'POST',headers:{'Content-Type':'application/json','X-Ink-Visitor':'1'},body:JSON.stringify(payload)});
    if(epoch!==aiState.epoch)return;
    if(!value.job||!/^[a-f0-9]{32}$/.test(String(value.job.id||'')))throw new Error('请求可能已接收，请先刷新结果。');
    aiState.jobs=[value.job,...aiState.jobs.filter(job=>job.id!==value.job.id)].slice(0,20);contentPages.ai=0;aiRenderJobs();aiFeedback('已接收，生成后会显示在“我的建议”中。');aiState.composeExpanded=false;aiControls();
  }catch(error){if(epoch===aiState.epoch)aiFeedback(error.name==='AbortError'?'等待超时，请先刷新结果，确认是否已接收。不会自动重复发送。':error.message+' 可先查看项目的本地说明。',true);}
  finally{if(epoch===aiState.epoch){aiState.sending=false;aiControls();aiLoad();}}
});
$('ai-clear').addEventListener('click',async()=>{
  if($('ai-clear').disabled)return;
  aiState.clearing=true;const epoch=++aiState.epoch,scope=visitorScope();aiState.loadController?.abort();aiState.loadController=null;aiState.loading=false;
  clearTimeout(aiState.timer);aiState.timer=null;$('ai-refresh').disabled=true;
  for(const card of [...aiState.animations.keys()])aiFinishReveal(card);
  aiControls();aiFeedback('正在清空本账户的全部 AI 建议…');
  try{
    await get('/api/account/ai/clear',{method:'POST',headers:{'Content-Type':'application/json','X-Ink-Visitor':'1'},body:'{}'});
    if(epoch!==aiState.epoch||!sameVisitorScope(scope))return;
    aiState.jobs=[];aiState.seen.clear();aiState.historyLoaded=true;aiState.cleared=true;$('ai-help').hidden=true;aiRenderJobs();
    aiFeedback('这个账户的全部 AI 建议已清空，作品和成绩已保留。');
  }catch(error){if(epoch===aiState.epoch&&sameVisitorScope(scope))aiFeedback(error.name==='AbortError'?'清空确认超时，请刷新查看结果。不会自动重复清空。':'清空未完成，已有建议仍保留。'+error.message,true);}
  finally{if(epoch===aiState.epoch&&sameVisitorScope(scope)){aiState.clearing=false;$('ai-refresh').disabled=false;aiControls();aiSchedule();}}
});
$('ai-refresh').addEventListener('click',()=>aiLoad());
$('ai-compose-toggle').addEventListener('click',()=>{aiState.composeExpanded=aiState.composeExpanded===false;aiControls();});
window.addEventListener('pagehide',()=>{aiState.pageHidden=true;clearTimeout(aiState.timer);aiPauseReveal();aiThinking();});
window.addEventListener('pageshow',event=>{aiState.pageHidden=false;if(event.persisted){aiResumeReveal();aiThinking();aiLoad();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(aiState.timer);aiPauseReveal();}else{aiResumeReveal();aiLoad();}aiThinking();});
const aiMotionChanged=()=>{if(aiReducedMotion.matches)for(const card of [...aiState.animations.keys()])aiFinishReveal(card);aiThinking();};
if(aiReducedMotion.addEventListener)aiReducedMotion.addEventListener('change',aiMotionChanged);else aiReducedMotion.addListener(aiMotionChanged);
window.addEventListener('online',()=>aiLoad());
$('retry').addEventListener('click',()=>{refresh();photoLoad();aiLoad();});window.addEventListener('online',()=>refresh());window.addEventListener('offline',()=>failure());document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
// Account access owns the entire private view. No private response is retained
// in localStorage; account/session changes cancel in-flight reads and erase DOM.
function contentPager(id,container){let pager=$(id);if(!pager){pager=node('div',undefined,'content-pager');pager.id=id;container.after(pager);}return pager;}
function pageItems(kind,items,size,container,pager,key,build,update){const total=Math.max(1,Math.ceil(items.length/size));contentPages[kind]=Math.min(Math.max(0,contentPages[kind]||0),total-1);reconcile(container,items.slice(contentPages[kind]*size,(contentPages[kind]+1)*size),key,build,update);pager.hidden=total<=1;pager.replaceChildren();if(pager.hidden)return;const previous=node('button','上一页'),next=node('button','下一页');previous.type=next.type='button';previous.disabled=contentPages[kind]===0;next.disabled=contentPages[kind]>=total-1;previous.addEventListener('click',()=>{contentPages[kind]--;pageItems(kind,items,size,container,pager,key,build,update);container.scrollIntoView({block:'start',behavior:'smooth'});});next.addEventListener('click',()=>{contentPages[kind]++;pageItems(kind,items,size,container,pager,key,build,update);container.scrollIntoView({block:'start',behavior:'smooth'});});pager.append(previous,node('span',`${contentPages[kind]+1} / ${total}`),next);}
function photoRenderJobs(){const pager=contentPager('photo-pager',$('photo-jobs'));if(photoState.jobs.length)pageItems('photos',photoState.jobs,3,$('photo-jobs'),pager,item=>item.id,photoCard,photoUpdateCard);else{empty($('photo-jobs'),'选一张风景，留一幅水墨','上传完成后，可对照原图并保存高清作品。','画');pager.hidden=true;}}
function applySpacePage(){document.body.dataset.page=accountState.page;for(const panel of document.querySelectorAll('[data-space-panel]'))panel.hidden=panel.dataset.spacePanel!==accountState.page;for(const button of $('space-navigation').querySelectorAll('button')){if(button.dataset.space===accountState.page)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}if(!token){$('private').hidden=true;$('rankings').hidden=accountState.page!=='results';$('account-empty').hidden=accountState.page==='results';}}
function showSpacePage(page,{scroll=true}={}){if(!['overview','gallery','results','ai','photos'].includes(page))return;accountState.page=page;applySpacePage();if(scroll)window.scrollTo({top:0,behavior:'instant'});}
for(const button of $('space-navigation').querySelectorAll('button'))button.addEventListener('click',()=>showSpacePage(button.dataset.space));
$('my-link').addEventListener('click',event=>{event.preventDefault();showSpacePage('overview');});
$('board-filter').addEventListener('change',()=>{contentPages.boards=0;renderBoards();});
function clearPrivateLabels(){
  photoState.retrying=false;aiState.composeExpanded=true;$('save-name').disabled=false;
  clearTimeout(showToast.timer);$('toast').hidden=true;setText('toast','');
  for(const id of ['session-date','report-name','cover-rounds','cover-artworks','ranking-note','viewer-title','viewer-caption'])setText(id,'');
  $('viewer-image').alt='';for(const id of ['viewer-download','viewer-original'])if($(id)){$(id).removeAttribute('href');$(id).removeAttribute('download');}
  viewerState=null;for(const id of ['art-count','round-count','realm-count'])setText(id,'0');
  setText('gallery-count','0 幅作品');setText('result-count','0 局记录');setText('title','我的游玩记录');setText('session-badge','等待连接');setText('hero-description','让每一次体验，都有自己的收藏。');setText('archive-note','结束游玩后，记录保留在所属账户中。');
}
function resetPrivateSession(){accountState.epoch++;for(const controller of accountState.controllers)controller.abort();accountState.controllers.clear();token=null;nameEpoch++;nameDirty=false;lastView=null;lastSuccessful=false;refreshing=false;reportSaving=false;viewerGeneration++;if($('image-viewer').open)$('image-viewer').close();$('viewer-image').removeAttribute('src');for(const url of [...reportBlobs])releaseReportBlob(url);reportState={urls:[],downloadAll:null,zipAvailable:true,name:'',imageName:'',needsRename:false};otherDownloads={};photoState.uploadController?.abort();clearTimeout(photoState.timer);photoReleasePreview();Object.assign(photoState,{ready:false,readonly:true,file:null,uploading:false,retrying:false,loading:false,jobs:[],timer:null,error:false,submitted:null,uploadController:null});$('photo-file').value='';$('photo-selection').hidden=true;aiState.epoch++;aiState.loadController?.abort();clearTimeout(aiState.timer);aiPauseReveal();for(const card of [...aiState.animations.keys()])aiFinishReveal(card);Object.assign(aiState,{ready:false,active:false,serverActive:null,capabilities:null,healthSummary:{},cleared:false,kind:'guide',project:'',preset:'',jobs:[],loading:false,sending:false,clearing:false,timer:null,historyLoaded:false,loadController:null});aiState.seen.clear();for(const name of Object.keys(contentPages))contentPages[name]=0;for(const id of ['artworks','rounds','photo-jobs','ai-jobs','downloads','report-pages','boards','ai-presets','ai-project','ai-help-tips']){delete $(id).dataset.signature;delete $(id).dataset.empty;$(id).replaceChildren();}for(const id of ['gallery-pager','rounds-pager','boards-pager','photo-pager','ai-pager'])if($(id)){$(id).replaceChildren();$(id).hidden=true;}availableBoards=[];$('board-filter').value='';$('name').value='';$('ai-text').value='';setText('ai-text-count','0 / 280 字');for(const id of ['name-feedback','photo-upload-feedback','photo-load-feedback','ai-feedback','ai-load-feedback','account-feedback','report-feedback'])setText(id,'');$('private').hidden=true;$('hero-stats').hidden=true;$('my-link').hidden=true;$('connection-banner').hidden=true;$('ai-help').hidden=true;$('ai-health-summary').hidden=true;for(const id of ['ai-health-title','ai-health-evidence','ai-health-scope'])setText(id,'');$('ai-thinking').hidden=true;for(const link of document.querySelectorAll('#private a[href]'))link.removeAttribute('href');$('report-source-fallback').hidden=true;clearPrivateLabels();}
function requireLogin(message=''){resetPrivateSession();accountState.account=null;accountState.sessions=[];accountState.loading=false;accountState.claiming=false;document.body.dataset.auth='anonymous';$('account-loading').hidden=true;$('auth-screen').hidden=false;$('account-workspace').hidden=true;$('space-navigation').hidden=true;$('logout').hidden=true;$('account-label').hidden=true;$('account-session').replaceChildren();$('auth-password').value='';$('auth-confirm').value='';$('auth-password').type='password';setText('auth-show-password','显示');setText('auth-feedback',message);$('auth-feedback').className='form-feedback'+(message?' error':'');$('auth-scan-note').hidden=!scannedToken;document.title='墨境 · 登录';}
function authMode(mode){if(accountState.submitting)return;accountState.mode=mode;$('auth-register-tab').setAttribute('aria-selected',String(mode==='register'));$('auth-login-tab').setAttribute('aria-selected',String(mode==='login'));$('auth-name-row').hidden=mode!=='register';$('auth-confirm-row').hidden=mode!=='register';$('auth-confirm').required=mode==='register';$('auth-password').autocomplete=mode==='register'?'new-password':'current-password';setText('auth-form-title',mode==='register'?'创建你的空间':'欢迎回来');setText('auth-intro',mode==='register'?'用一个账户，收藏作品与每一次游玩。':'登录后，只查看与管理自己的记录。');setText('auth-submit',mode==='register'?'注册并登录':'登录我的空间');setText('auth-feedback','');}
$('auth-login-tab').addEventListener('click',()=>authMode('login'));$('auth-register-tab').addEventListener('click',()=>authMode('register'));
$('auth-show-password').addEventListener('click',()=>{const shown=$('auth-password').type==='password';$('auth-password').type=shown?'text':'password';setText('auth-show-password',shown?'隐藏':'显示');$('auth-show-password').setAttribute('aria-label',shown?'隐藏密码':'显示密码');});
function accountPost(path,value){return get('/api/account/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Ink-Visitor':'1'},body:JSON.stringify(value)});}
function accountUI(value){accountState.account=value.account;accountState.sessions=(Array.isArray(value.sessions)?value.sessions:[]).filter(session=>/^[A-Za-z0-9_-]{43}$/.test(String(session.token||'')));document.body.dataset.auth='authenticated';$('auth-screen').hidden=true;$('account-loading').hidden=true;$('account-workspace').hidden=false;$('space-navigation').hidden=false;$('logout').hidden=false;$('account-label').hidden=false;setText('account-label',value.account.name||value.account.username);$('auth-password').value='';$('auth-confirm').value='';$('auth-name').value='';const select=$('account-session');select.replaceChildren();for(const session of accountState.sessions){const option=node('option',`${date(session.started)} · ${labels[session.status]||'已记录'}${session.current?' · 当前':''}`);option.value=session.token;select.append(option);}select.value=token||'';$('session-selector').hidden=!accountState.sessions.length;$('account-empty').hidden=Boolean(token);$('claim-panel').hidden=!scannedToken||accountState.claimDismissed||accountState.sessions.some(session=>session.token===scannedToken);setText('title',(value.account.name||value.account.username)+'，你好');setText('hero-description','作品、成绩与灵感，分别收好。');applySpacePage();}
async function selectAccountSession(next){if(next===token)return;if(next&&!accountState.sessions.some(session=>session.token===next))return;resetPrivateSession();token=next||null;$('account-session').value=token||'';$('account-empty').hidden=Boolean(token);if(token){$('session-badge').textContent='正在读取';await refresh();}else{applySpacePage();await refresh();}}
async function hydrateAccount(value){if(!value.authenticated||!value.account){requireLogin();return;}const changed=accountState.account?.id!==value.account.id;if(changed)resetPrivateSession();accountUI(value);const choice=accountState.sessions.some(session=>session.token===token)?token:accountState.sessions.some(session=>session.token===scannedToken)?scannedToken:value.active_token||accountState.sessions[0]?.token||null;await selectAccountSession(choice);applySpacePage();if(!choice)await refresh();}
async function accountRefresh(){if(accountState.loading||accountState.submitting)return;accountState.loading=true;try{const value=await get('/api/account');await hydrateAccount(value);setText('account-feedback','');}catch(error){if(error.name!=='AbortError'){if(error.status===401)requireLogin('请重新登录。');else if(accountState.account){setText('account-feedback','暂时无法刷新账户，请检查手机与板卡的连接。');}else{requireLogin('连接暂时未恢复，请连接与板卡相同的网络后重试。');}}}finally{accountState.loading=false;}}
async function accountBoot(){await accountRefresh();}
$('auth-form').addEventListener('submit',async event=>{event.preventDefault();if(accountState.submitting)return;const username=$('auth-username').value.normalize('NFKC').trim(),password=$('auth-password').value;if(username.length<3||username.length>32||!/^[-.\p{L}\p{N}_]+$/u.test(username)){setText('auth-feedback','用户名为3–32个字，可用中文、字母、数字、下划线、短横线和点。');return;}if([...password].length<8||[...password].length>128){setText('auth-feedback','密码需为8–128个字符。');return;}if(accountState.mode==='register'&&password!==$('auth-confirm').value){setText('auth-feedback','两次输入的密码不一致。');return;}accountState.submitting=true;const payload={username,password};if(accountState.mode==='register'&&$('auth-name').value.trim())payload.name=$('auth-name').value.trim();for(const button of $('auth-form').querySelectorAll('button'))button.disabled=true;setText('auth-feedback',accountState.mode==='register'?'正在创建账户…':'正在登录…');try{const value=await accountPost(accountState.mode,payload);await hydrateAccount(value);notifyAccountChange();showSpacePage('overview');showToast(accountState.mode==='register'?'账户已创建':'已登录');}catch(error){setText('auth-feedback',error.name==='AbortError'?'连接等待超时，请确认板卡网络后重试。':error.message);$('auth-feedback').className='form-feedback error';}finally{accountState.submitting=false;for(const button of $('auth-form').querySelectorAll('button'))button.disabled=false;}});
$('logout').addEventListener('click',async()=>{requireLogin();$('auth-submit').disabled=true;try{await accountPost('logout',{});notifyAccountChange();setText('auth-feedback','已退出登录。');}catch(error){setText('auth-feedback','页面内容已清除，但退出未获板卡确认。请恢复网络后再次退出，避免在共用手机上保留登录。');$('auth-feedback').className='form-feedback error';}finally{$('auth-submit').disabled=false;}});
$('account-session').addEventListener('change',()=>selectAccountSession($('account-session').value));$('account-refresh').addEventListener('click',accountRefresh);$('empty-refresh').addEventListener('click',accountRefresh);
$('claim-session').addEventListener('click',async()=>{if(!scannedToken||!accountState.account||accountState.claiming)return;accountState.claiming=true;$('claim-session').disabled=true;setText('claim-feedback','正在关联这次游玩…');try{const value=await accountPost('claim',{token:scannedToken});accountState.claimDismissed=true;if(value.account)await hydrateAccount(value);else await accountRefresh();if(accountState.sessions.some(session=>session.token===scannedToken))await selectAccountSession(scannedToken);$('claim-panel').hidden=true;history.replaceState(null,'','/');showToast('已保存到我的账户');}catch(error){setText('claim-feedback',error.code==="legacy_session"?error.message:error.status===403?'这次游玩已属于其他账户，无法关联。请在板卡结束游玩后，为自己重新开始并扫码。':error.status===409?'这条旧记录无法关联。请在板卡重新开始游玩后扫码。':error.name==='AbortError'?'关联确认超时，请刷新账户核对结果，再决定是否重试。':error.message);$('claim-feedback').className='form-feedback error';}finally{accountState.claiming=false;$('claim-session').disabled=false;}});
$('claim-dismiss').addEventListener('click',()=>{accountState.claimDismissed=true;$('claim-panel').hidden=true;});
async function showProjectIntro(){const dialog=$('project-dialog');$('project-dialog-body').replaceChildren(node('p','正在读取本地项目说明…'));dialog.showModal();try{const value=await get('/api/projects'),project=value.projects?.find(item=>item.id==='system');if(!project?.help)throw new Error('项目说明暂未就绪，请稍后重试。');setText('project-dialog-title',project.help.title||project.title);const body=$('project-dialog-body');body.replaceChildren(node('p',project.help.summary||''));const list=node('ul');for(const tip of project.help.tips||[])if(typeof tip==='string')list.append(node('li',tip));body.append(list);}catch(error){$('project-dialog-body').replaceChildren(node('p',error.name==='AbortError'?'读取中断，请稍后再试。':error.message));}}
$('empty-project').addEventListener('click',showProjectIntro);$('project-dialog-close').addEventListener('click',()=>$('project-dialog').close());$('project-dialog-done').addEventListener('click',()=>$('project-dialog').close());
$('ai-system-entry').addEventListener('click',()=>{const project=aiProjects().find(item=>item.id==='system');if(!project){showProjectIntro();return;}aiState.composeExpanded=true;aiState.kind='guide';aiState.project='system';aiState.preset='rules';contentPages.ai=0;aiPresets();aiRenderJobs();aiShowHelp();$('ai-project-area').scrollIntoView({block:'nearest',behavior:'smooth'});});
let accountChannel=null;
try{accountChannel=new BroadcastChannel('ink-realm-account');accountChannel.onmessage=event=>{if(event.data?.type!=='account-changed')return;requireLogin();accountRefresh();};}catch(_){/* Cookie checks remain authoritative without tab messaging. */}
function notifyAccountChange(){accountChannel?.postMessage({type:'account-changed'});}
window.addEventListener('pagehide',()=>{resetPrivateSession();accountState.loading=false;accountState.submitting=false;document.body.dataset.auth='loading';$('auth-screen').hidden=true;$('account-workspace').hidden=true;$('space-navigation').hidden=true;$('account-loading').hidden=false;});
window.addEventListener('pageshow',event=>{if(event.persisted)accountRefresh();});
networkView(null);accountBoot();setInterval(refresh,10000);setInterval(()=>{if(accountState.account&&!document.hidden)accountRefresh();},30000);
