// Local browser regression: synthetic PNGs and intercepted requests, no board or camera.
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),zlib=require('node:zlib'),http=require('node:http');
const out=path.resolve(process.env.PHOTO_VIEWER_QA_DIR||path.join(__dirname,'..','qa','photo-viewer'));
fs.mkdirSync(path.join(out,'temp'),{recursive:true});process.env.TEMP=path.join(out,'temp');process.env.TMP=process.env.TEMP;
const {chromium}=require('playwright');
const staticRoot=path.join(__dirname,'static'),token='a'.repeat(43),base='/v/'+token,id='1'.repeat(32);let origin;
function png(width,height){
  const crc=b=>{let c=0xffffffff;for(const x of b){c^=x;for(let k=0;k<8;k++)c=c&1?(c>>>1)^0xedb88320:c>>>1;}return (c^0xffffffff)>>>0;};
  const chunk=(type,data)=>{const label=Buffer.from(type),body=Buffer.concat([label,data]),n=Buffer.alloc(4),sum=Buffer.alloc(4);n.writeUInt32BE(data.length);sum.writeUInt32BE(crc(body));return Buffer.concat([n,body,sum]);};
  const header=Buffer.alloc(13);header.writeUInt32BE(width);header.writeUInt32BE(height,4);header[8]=8;header[9]=2;
  const data=Buffer.alloc((width*3+1)*height);for(let y=0;y<height;y++)for(let x=0;x<width;x++){const at=y*(width*3+1)+1+x*3;data[at]=x%256;data[at+1]=y%256;data[at+2]=(x%8<4)!==(y%8<4)?235:35;}
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',zlib.deflateSync(data)),chunk('IEND',Buffer.alloc(0))]);
}
const fixture=png(1920,1080),reportPNG=png(1080,640),checks=[];
let jobs=[{id,status:'completed',style:'color',source_url:base+'/photos/'+id+'/source.png',result_url:base+'/photos/'+id+'/result.png'}],posts=0,photoImages=0,renamed=false;
const report=()=>({name:renamed?'新名字':'测试游客',image_name:'测试游客',status:'active',started:'2026-10-22T10:00:00+08:00',rounds:[],artworks:[],downloads:{}});
async function main(){
  const options={headless:true,env:{...process.env}};
  const executable=process.env.PHOTO_VIEWER_CHROME||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':null);if(executable)options.executablePath=executable;
  const browser=await chromium.launch(options);let server;
  try{
    const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:2,acceptDownloads:true});
    const errors=[],requests=[];
    await context.addInitScript(()=>{window.photoCanvasCalls=0;const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(...args){window.photoCanvasCalls++;return original.apply(this,args);};});
    server=http.createServer(async(req,res)=>{
      const route={request:()=>({url:()=>origin+req.url,method:()=>req.method}),fulfill:async({status=200,json,contentType,body})=>{if(json!==undefined){body=JSON.stringify(json);contentType='application/json';}res.writeHead(status,{'Content-Type':contentType||'text/plain','Cache-Control':'no-store'});res.end(body);}};
      const request=route.request(),url=new URL(request.url()),p=url.pathname;requests.push(request.method()+' '+url.href);if(request.method()==='POST')posts++;
      if(url.origin!==origin)return route.fulfill({status:403,body:'External requests are forbidden in this fixture'});
      if(p==='/api/visitor/'+token+'/photos')return route.fulfill({json:{jobs}});
      if(p==='/api/visitor/'+token)return route.fulfill({json:report()});
      if(p==='/api/leaderboards')return route.fulfill({json:{boards:[]}});
      if(p==='/api/network')return route.fulfill({json:{scope:'lan'}});
      if(p===base+'/photos/'+id+'/source.png'||p===base+'/photos/'+id+'/result.png'){photoImages++;return route.fulfill({contentType:'image/png',body:fixture});}
      if(p===base+'/report.png')return route.fulfill({contentType:'image/png',body:reportPNG});
      if(p===base)return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(staticRoot,'index.html'))});
      if(p.startsWith('/static/')){const file=path.join(staticRoot,path.basename(p));if(fs.existsSync(file))return route.fulfill({contentType:p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':p.endsWith('.svg')?'image/svg+xml':'image/webp',body:fs.readFileSync(file)});}
      return route.fulfill({status:404,body:'not found'});
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(origin+base);const result=page.getByRole('button',{name:'查看水墨作品大图',exact:true});await result.waitFor();
    await result.focus();assert.equal(await result.getAttribute('aria-haspopup'),'dialog');await page.keyboard.press('Enter');await page.locator('#image-viewer[open]').waitFor();
    await page.waitForFunction(()=>document.getElementById('viewer-image').naturalWidth===1920&&!document.getElementById('viewer-image').hidden);
    assert.equal(await page.locator('#viewer-image').getAttribute('src'),base+'/photos/'+id+'/result.png');assert.equal(await page.locator('#viewer-download').getAttribute('href'),base+'/photos/'+id+'/result.png');checks.push('Enter opens result in the existing viewer with full 1920x1080 PNG');
    const raw=page.locator('#viewer-original');assert.equal(await raw.getAttribute('href'),base+'/photos/'+id+'/result.png');assert.equal(await raw.getAttribute('target'),'_blank');assert.match(await raw.getAttribute('rel'),/noopener/);
    const popupPromise=context.waitForEvent('page');await raw.click();const popup=await popupPromise;await popup.waitForLoadState();assert.equal(popup.url(),origin+base+'/photos/'+id+'/result.png');await popup.close();checks.push('original-size link opens the validated PNG URL in a new tab');
    const downloadPromise=page.waitForEvent('download');await page.locator('#viewer-download').click();const saved=await downloadPromise;const savedPath=path.join(out,'download-result.png');await saved.saveAs(savedPath);assert.deepEqual(fs.readFileSync(savedPath),fixture);checks.push('download preserves exact synthetic PNG bytes and dimensions');
    await page.keyboard.press('Escape');assert.equal(await result.evaluate(el=>el===document.activeElement),true);await page.keyboard.press('Space');await page.locator('#image-viewer[open]').waitFor();await page.keyboard.press('Escape');checks.push('Escape restores focus and Space reopens the image');
    await page.getByRole('button',{name:'原始照片',exact:true}).click();const source=page.getByRole('button',{name:'查看原始照片大图',exact:true});await source.focus();await page.keyboard.press('Enter');assert.equal(await page.locator('#viewer-original').getAttribute('href'),base+'/photos/'+id+'/source.png');await page.keyboard.press('Escape');checks.push('original comparison uses its own source URL with keyboard access');
    const before=photoImages;await page.locator('#photo-refresh').click();await page.waitForTimeout(200);assert.equal(photoImages,before);assert.equal(posts,0);assert.equal(await page.evaluate(()=>window.photoCanvasCalls),0);checks.push('preview, download and refresh never render through canvas or submit another job');
    await page.getByRole('button',{name:'水墨作品',exact:true}).click();await result.click();await page.locator('#viewer-image').waitFor({state:'visible'});
    for(const width of [320,390,1100]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);const box=await page.locator('#image-viewer').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width+1);}
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile-viewer.png')});await page.keyboard.press('Escape');checks.push('viewer stays within 320px, 390px and desktop viewport widths');
    jobs=[{...jobs[0],result_url:'https://example.invalid/result.png',source_url:'/v/'+'b'.repeat(43)+'/photos/'+id+'/source.png'}];await page.locator('#photo-refresh').click();await page.waitForTimeout(150);assert.equal(await page.locator('.photo-comparison').isVisible(),false);assert.equal(await page.locator('.photo-job-actions a').isVisible(),false);assert.equal(requests.some(url=>url.includes('example.invalid')||url.includes('/v/'+'b'.repeat(43))),false);checks.push('foreign visitor and external image URLs are not exposed or fetched');
    await page.locator('#preview-report').click();await page.locator('#viewer-image').waitFor({state:'visible'});assert.equal(await page.locator('#viewer-original').isVisible(),false);assert.equal(await page.locator('#viewer-download').getAttribute('href'),base+'/report.png');await page.keyboard.press('Escape');checks.push('normal report viewer keeps original download without a stale photo link');
    renamed=true;await page.evaluate(()=>refresh());await page.locator('#preview-report').click();await page.locator('#viewer-canvas').waitFor({state:'visible'});assert.equal(await page.locator('#viewer-canvas').getAttribute('width'),'1080');assert.equal(await page.locator('#viewer-original').isVisible(),false);assert.match(await page.locator('#viewer-download').getAttribute('href'),/^blob:/);await page.keyboard.press('Escape');checks.push('existing renamed report canvas and blob download still work');
    assert.equal(posts,0);assert.deepEqual(errors,[]);checks.push('no script errors or unwanted uploads');
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed:checks.length,checks,posts,photoImages,errors},null,2));console.log(JSON.stringify({passed:checks.length,checks,output:out},null,2));
  }finally{await browser.close();if(server)await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{fs.writeFileSync(path.join(out,'error.txt'),error.stack);console.error(error);process.exitCode=1;});
