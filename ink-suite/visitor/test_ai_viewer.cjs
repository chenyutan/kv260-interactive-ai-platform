// Real browser, loopback fixture only. No board, camera, API key or external API.
'use strict';
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const out=path.resolve(process.env.AI_VIEWER_QA_DIR||path.join(__dirname,'..','qa','ai-viewer'));
fs.mkdirSync(path.join(out,'temp'),{recursive:true});process.env.TEMP=path.join(out,'temp');process.env.TMP=process.env.TEMP;
const {chromium}=require('playwright');
const staticRoot=path.join(__dirname,'static'),token='a'.repeat(43),base='/v/'+token,api='/api/visitor/'+token+'/ai';
let origin,sessionStatus='active',configured=true,jobs=[],posts=[],gets=0,postFailure=false,getFailure=false;
const checks=[],errors=[],external=[];
const capabilities=()=>({configured,model:'deepseek-flash',thinking:'disabled',presets:{creation:[
  {id:'landscape',title:'山水',description:'从山势开始，再添云水'},
  {id:'bamboo',title:'竹林',description:'先立竹干，再添枝叶'},
  {id:'plum',title:'梅花',description:'从梅枝开始完成花朵'},
  {id:'mixed',title:'自由搭配',description:'将几种主题组合成作品'}],review:[
  {id:'overview',title:'本次复盘',description:'回看已经记录的游戏成绩'},
  {id:'improve',title:'练习建议',description:'选择下一步练习方向'}]}});
const report=()=>({name:'测试游客',image_name:'测试游客',status:sessionStatus,started:'2026-10-22T10:00:00+08:00',rounds:[],artworks:[],downloads:{}});
const creation={title:'山水之间',summary:'先画山，再用竹林丰富构图。每一步都由你选择和完成。',steps:[
  {theme:'landscape',action:'A01',instruction:'双臂向两侧展开，画出山形。'},
  {theme:'bamboo',action:'A02',instruction:'单臂上举，画出竹干。'},
  {theme:'bamboo',action:'A06',instruction:'双手上托，为竹干添叶。'}]};
const review={title:'本次游玩复盘',summary:'你完成了水果忍者与鼓动山河。下次可以先关注动作节奏。',tips:['先在舒适幅度内练习，保持连贯。','把注意力放在目标位置，逐步提高稳定性。']};
async function main(){
  let server,browser;
  try{
    server=http.createServer(async(req,res)=>{
      const send=(status,value,type='application/json')=>{res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store'});res.end(type==='application/json'?JSON.stringify(value):value);};
      const p=new URL(req.url,origin).pathname;
      if(p===api){
        if(req.method==='POST'){
          let data='';for await(const chunk of req)data+=chunk;const value=JSON.parse(data);posts.push({value,headers:req.headers});
          if(postFailure)return send(503,{message:'AI 助手暂时不可用，请稍后重试。'});
          const job={id:posts.length.toString(16).padStart(32,'0'),kind:value.kind,preset:value.preset,created:'2026-10-22T12:00:00+08:00',status:'queued',result:null,error:null,cache_hit:false};jobs.unshift(job);return send(202,{job});
        }
        gets++;if(getFailure)return send(503,{message:'暂时无法读取。'});
        return send(200,{capabilities:capabilities(),jobs,active:sessionStatus==='active'});
      }
      if(p==='/api/visitor/'+token+'/photos')return send(200,{jobs:[]});
      if(p==='/api/visitor/'+token)return send(200,report());
      if(p==='/api/leaderboards')return send(200,{boards:[]});
      if(p==='/api/network')return send(200,{scope:'same-wifi',message:'本地合成测试'});
      if(p===base||p==='/leaderboard')return send(200,fs.readFileSync(path.join(staticRoot,'index.html')),'text/html; charset=utf-8');
      if(p.startsWith('/static/')){const name=path.basename(p),file=path.join(staticRoot,name);if(fs.existsSync(file))return send(200,fs.readFileSync(file),name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.svg')?'image/svg+xml':'image/webp');}
      return send(404,{message:'fixture missing'});
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
    const options={headless:true,env:{...process.env}};
    const executable=process.env.AI_VIEWER_CHROME||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':null);if(executable)options.executablePath=executable;
    browser=await chromium.launch(options);
    const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1});
    await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort();});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(origin+base);await page.waitForFunction(()=>!document.getElementById('ai-submit').disabled);
    assert.equal(posts.length,0);assert.equal(await page.locator('#ai-presets button').count(),4);assert.equal(await page.locator('#ai-presets [aria-pressed=true]').count(),1);
    assert.equal(await page.locator('#ai-submit').textContent(),'生成建议');checks.push('authenticated page loads dynamic service presets without submitting');
    await page.locator('#ai-text').fill('我想先画山，再添竹林。');await page.locator('#ai-presets [data-preset=mixed]').click();await page.locator('#ai-submit').click();
    await page.waitForFunction(()=>document.querySelector('#ai-jobs [data-status=queued]'));
    assert.equal(posts.length,1);assert.deepEqual(posts[0].value,{kind:'creation',preset:'mixed',text:'我想先画山，再添竹林。'});assert.equal(posts[0].headers['x-ink-visitor'],'1');assert.equal(posts[0].headers.origin,origin);
    assert.equal(await page.locator('#ai-submit').isDisabled(),true);checks.push('explicit POST contains only kind/preset/text with same-origin visitor header; pending prevents duplicate send');
    jobs[0].status='running';await page.locator('#ai-refresh').click();await page.waitForFunction(()=>document.querySelector('#ai-jobs [data-status=running]'));
    const before=gets;await page.waitForTimeout(3800);assert.ok(gets>before);assert.equal(posts.length,1);checks.push('running-job timer and refresh are GET-only and never repeat AI requests');
    jobs[0].status='ready';jobs[0].result=creation;await page.locator('#ai-refresh').click();await page.waitForFunction(()=>document.querySelector('#ai-jobs [data-status=ready]'));
    assert.equal(await page.locator('.ai-job-steps li').count(),3);assert.equal(await page.locator('#ai-submit').isEnabled(),true);
    await page.locator('#ai-assistant').screenshot({path:path.join(out,'mobile-creation.png')});checks.push('completed creation displays named steps and returns to ready state');
    await page.locator('#ai-kind [data-kind=review]').click();assert.equal(await page.locator('#ai-presets button').count(),2);await page.locator('#ai-text').fill('怎样提高连击？');await page.locator('#ai-submit').click();await page.waitForFunction(()=>document.querySelectorAll('.ai-job').length===2);
    jobs[0].status='ready';jobs[0].result=review;await page.locator('#ai-refresh').click();await page.waitForFunction(()=>document.querySelector('.ai-job h4').textContent==='本次游玩复盘');
    assert.equal(posts.length,2);assert.equal(posts[1].value.kind,'review');assert.equal(await page.locator('.ai-job').first().locator('li').count(),2);checks.push('review preserves creation history and displays tips');
    await page.locator('#ai-assistant').screenshot({path:path.join(out,'mobile-history.png')});
    for(const width of [320,390,960,1280]){
      await page.setViewportSize({width,height:900});await page.locator('#ai-assistant').scrollIntoViewIfNeeded();
      const sizes=await page.locator('#ai-assistant').evaluate(root=>({overflow:document.documentElement.scrollWidth>innerWidth+1,buttons:[...root.querySelectorAll('button')].filter(button=>button.getClientRects().length).map(button=>({height:button.getBoundingClientRect().height,width:button.getBoundingClientRect().width}))}));
      assert.equal(sizes.overflow,false,`overflow at ${width}`);assert.ok(sizes.buttons.every(button=>button.height>=48&&button.width>=44));
      await page.locator('#ai-assistant').screenshot({path:path.join(out,`layout-${width}.png`)});
    }
    checks.push('320/390/960/1280px layouts have no horizontal overflow and all AI touch targets are at least 48px high');
    jobs[0].result={title:'<img src=x onerror="window.AI_INJECTED=1">',summary:'<script>window.AI_INJECTED=1</script>',tips:['<button onclick="evil()">不能变按钮</button>']};
    await page.locator('#ai-refresh').click();await page.waitForFunction(()=>document.querySelector('.ai-job h4').textContent.startsWith('<img'));
    assert.equal(await page.locator('#ai-jobs img, #ai-jobs script, #ai-jobs button:not(.ai-show-all)').count(),0);assert.equal(await page.evaluate(()=>window.AI_INJECTED),undefined);checks.push('model title/summary/tips are inert text; only the local show-full-text control may be a button');
    jobs[0].result=review;await page.locator('#ai-refresh').click();await page.waitForTimeout(120);
    postFailure=true;await page.locator('#ai-submit').click();await page.waitForFunction(()=>document.getElementById('ai-feedback').classList.contains('error'));const failures=posts.length;await page.waitForTimeout(4000);assert.equal(posts.length,failures);postFailure=false;checks.push('failed POST is reported and is never retried by timers');
    getFailure=true;await page.locator('#ai-refresh').click();await page.waitForFunction(()=>document.getElementById('ai-load-feedback').classList.contains('error'));assert.equal(await page.locator('.ai-job').count(),2);getFailure=false;checks.push('temporary GET failure preserves earlier results');
    configured=false;await page.locator('#ai-refresh').click();await page.waitForFunction(()=>document.getElementById('ai-service-state').textContent==='暂未启用');assert.equal(await page.locator('#ai-submit').isDisabled(),true);assert.equal(await page.locator('.ai-job').count(),2);checks.push('unconfigured service disables submit without hiding existing results');
    configured=true;sessionStatus='archived';await page.evaluate(()=>refresh());await page.waitForFunction(()=>document.getElementById('ai-form').hidden);await page.locator('#ai-refresh').click();await page.waitForFunction(()=>!document.getElementById('ai-readonly').hidden);
    assert.equal(await page.locator('.ai-job').count(),2);assert.equal(posts.length,failures);await page.setViewportSize({width:390,height:844});await page.locator('#ai-assistant').screenshot({path:path.join(out,'mobile-archived.png')});checks.push('archived sessions show read-only history, no form or additional submissions');
    await page.goto(origin+'/leaderboard');assert.equal(await page.locator('#ai-assistant').isVisible(),false);checks.push('public leaderboard never exposes visitor AI controls');
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,checks,posts:posts.length,gets,errors,external,camera_used:false,board_used:false,real_ai_used:false},null,2));
    console.log(JSON.stringify({ok:true,checks:checks.length,out}));
  }finally{if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{fs.writeFileSync(path.join(out,'error.txt'),error.stack);console.error(error);process.exitCode=1;});
