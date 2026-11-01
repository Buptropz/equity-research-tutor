'use strict';
pages.translation='财报中文阅读';
let pendingImport=null;
let selectedDocument=null,selectedOrdinal=1,pendingState=null,lastRevision=null,polling=false,lastSubmitted=null;
const draftForms=new Set();
const originalRender=render;
window.acceptV11State=()=>{pendingState=null;const banner=$('#update-banner');if(banner)banner.hidden=true};
const jobNames={PENDING:'等待翻译',RUNNING:'正在翻译',PAUSED:'已暂停，可续翻',FAILED:'中断，可重试',COMPLETED:'本次任务已完成'};
const translationNames={PENDING:'尚未翻译',DONE:'已翻译 · 基础检查通过',REVIEW:'译文已保存 · 待核对',FAILED:'翻译未完成'};

function formKey(form){return 'ert-v11-draft-'+(S?.active?.id??'general')+'-'+(form.dataset.form||'')+'-'+(form.dataset.pack||'')}
function rememberForm(form){
 if(!form||!form.dataset.form)return;
 const key=formKey(form),values={};
 for(const input of form.elements){if(!input.name||input.type==='file'||input.type==='password')continue;values[input.name]=input.type==='checkbox'?input.checked:input.value}
 try{localStorage.setItem(key,JSON.stringify(values));draftForms.add(key)}catch{toast('浏览器草稿空间不足，建议先保存或导入当前内容',true)}
}
function restoreForms(){
 for(const form of document.querySelectorAll('form[data-form]')){
  try{const values=JSON.parse(localStorage.getItem(formKey(form))||'null');if(!values)continue;draftForms.add(formKey(form));for(const input of form.elements){if(!input.name||!(input.name in values)||input.type==='file')continue;if(input.type==='checkbox')input.checked=values[input.name];else input.value=values[input.name]}}
  catch{}
 }
 if(pendingImport&&$('#import-kind')){const form=$('form[data-form="document-import"]');form.querySelector('[name=title]').value=pendingImport.title;form.querySelector('[name=source_url]').value=pendingImport.url;form.querySelector('[name=report_period]').value=pendingImport.period;$('#import-kind').value=['www.sec.gov','sec.gov'].includes(new URL(pendingImport.url).hostname)?'sec':'file';form.closest('details').open=true;rememberForm(form);pendingImport=null}
 updateImportMode();
}
window.afterV11Action=(path,result)=>{
 if(lastSubmitted&&['/api/answer','/api/valuation','/api/settings','/api/company','/api/fact','/api/ai-chat','/api/document-import','/api/backup-import'].includes(path)){
  localStorage.removeItem(lastSubmitted);draftForms.delete(lastSubmitted);lastSubmitted=null;
 }
 if(result.id&&path==='/api/document-import'){selectedDocument=result.id;selectedOrdinal=1}
};
function updateImportMode(){
 const mode=$('#import-kind');if(!mode)return;
 document.querySelectorAll('[data-import-mode]').forEach(el=>el.hidden=el.dataset.importMode!==mode.value);
}

function translationPage(){
 const docs=S.documents||[];
 const doc=docs.find(d=>d.id===selectedDocument)||docs[0];
 if(doc)selectedDocument=doc.id;
 const chunk=doc?.chunks.find(c=>c.ordinal===selectedOrdinal)||doc?.chunks[0];if(chunk)selectedOrdinal=chunk.ordinal;
 const finished=doc?.chunks.filter(c=>['DONE','REVIEW'].includes(c.status)).length||0;
 const reviews=doc?.chunks.filter(c=>c.status==='REVIEW').length||0;
 const activeJob=doc?.job&&['RUNNING','PENDING'].includes(doc.job.status);
 const numberCheck=chunk?.number_check?JSON.parse(chunk.number_check):null;const currentQuality=numberCheck?.version==='quality-v2';
 return heading('READ THE ORIGINAL, UNDERSTAND IN CHINESE','财报中文阅读','逐段翻译，保留原文与来源；长报告可暂停、续翻并导出中英对照。')+`<div class="note-card"><h3>使用现有Codex进行翻译</h3><p>每次仅发送这份财报的标题、来源与待翻译段落，使用你的Codex账号额度。全文翻译会分段在后台进行。会检查中文正文、数字、币种、单位和可识别的科目对应关系；通过基础检查仍需对照原始披露。</p></div><div class="card"><details ${docs.length?'':'open'}><summary>＋ 导入财报或章节</summary><form data-form="document-import"><div class="spacer"></div><div class="form-grid">${field('财报或章节标题','title','','text','','required placeholder="例如：年度财报 · 经营讨论与分析"')}${field('报告期（可选）','report_period','','text','按原文填写；不会自动认定为已核实')}<div><label for="import-kind">导入方式</label><select id="import-kind" name="kind"><option value="file">上传PDF / TXT / HTML</option><option value="text">粘贴原文</option><option value="sec">读取SEC财报原文链接</option></select></div>${field('原始来源链接（可选）','source_url','','url','自动读取时须填写SEC Archives原文链接；上传和粘贴可补充出处','placeholder="https://…"')}<div class="full" data-import-mode="file"><label for="report-file">选择财报文件（不超过18MB）</label><input type="file" id="report-file" name="report-file" accept=".pdf,.txt,.html,.htm"><p class="field-hint">PDF须含可提取文字；扫描件需先OCR。文本提取可能改变表格排版，可随时下载原文件核对。</p></div><div class="full" data-import-mode="text" hidden><label for="report-text">财报原文</label><textarea id="report-text" name="text" rows="10" placeholder="粘贴需要翻译的原文，保留标题、表格单位和脚注…"></textarea></div><div class="full" data-import-mode="sec" hidden><p class="muted">可从公司档案的官方财报链接复制SEC Archives地址。其他网站的财报请下载后上传，或粘贴所需章节。</p></div></div><div class="spacer"></div><button class="btn primary" type="submit">导入并保存原文</button><p class="field-hint">${S.active?'将保存到 '+e(S.active.ticker)+' 的公司档案':'尚未选股，将保存为通用阅读材料'}</p></form></details></div>`+(doc?`<div class="card"><div class="form-grid"><div><label for="document-selector">已保存财报</label><select id="document-selector">${docs.map(d=>`<option value="${d.id}" ${doc.id===d.id?'selected':''}>${e(d.title)}</option>`).join('')}</select></div><div><label for="chunk-selector">阅读段落</label><select id="chunk-selector">${doc.chunks.map(c=>`<option value="${c.ordinal}" ${chunk.ordinal===c.ordinal?'selected':''}>第${c.ordinal}段 · ${translationNames[c.status]||c.status}</option>`).join('')}</select></div></div><div class="spacer"></div><div class="row"><div><h2>${e(doc.title)}</h2><span class="subtle">${e(doc.report_period||'报告期未注明')} · 已处理 ${finished}/${doc.chunks.length} 段${reviews?' · '+reviews+'段译文待核对':''}</span></div><span class="pill ${reviews?'warning':''}">${doc.job?jobNames[doc.job.status]||doc.job.status:'尚未开始翻译'}</span></div><p class="field-hint">${doc.source_url?`来源：<a target="_blank" rel="noopener noreferrer" href="${e(doc.source_url)}">打开原始来源 ↗</a>`:'用户上传或粘贴，来源待核实'}<br>原文件SHA-256：${e(doc.sha256)}</p>${doc.job?.error?`<div class="warning-box">${e(doc.job.error)}。已完成段落会保留，续翻时自动跳过。</div>`:''}<div class="actions">${button(chunk.status==='FAILED'?'重试本段':'翻译本段','translate-one',true,`data-id="${doc.id}" data-ordinal="${chunk.ordinal}" ${activeJob||['DONE','REVIEW'].includes(chunk.status)?'disabled':''}`)}${['DONE','REVIEW'].includes(chunk.status)?button('重译本段（保留旧版）','translate-retry',false,`data-id="${doc.id}" data-ordinal="${chunk.ordinal}" ${activeJob?'disabled':''}`):''}${button(`翻译全文 / 继续剩余段落（共${doc.chunks.length}段）`,'translate-all',false,`data-id="${doc.id}" ${activeJob?'disabled':''}`)}${activeJob?button('暂停（当前段完成后生效）','translation-pause',false,`data-id="${doc.id}"`):''}${button('导出中英对照','translation-export',false,`data-id="${doc.id}"`)}${button('下载原始文件','document-download',false,`data-id="${doc.id}"`)}${doc.company_id===null&&S.active?button('归档到 '+e(S.active.ticker),'document-assign',false,`data-id="${doc.id}"`):''}</div></div>${numberCheck&&!numberCheck.ok?`<div class="warning-box">本段基础检查未通过，请对照原文。${numberCheck.issues?.length?`<ul>${numberCheck.issues.map(issue=>`<li>${e(issue)}</li>`).join('')}</ul>`:''}<br>遗漏或改变的数字：${e(numberCheck.missing.slice(0,20).join('、')||'无')}<br>额外数字：${e(numberCheck.added.slice(0,20).join('、')||'无')}</div>`:''}<div class="bilingual"><section class="card"><div class="card-head"><h2>原文 · 第${chunk.ordinal}段</h2><span class="subtle">来源版本固定保存</span></div><pre class="report-text">${e(chunk.original)}</pre></section><section class="card"><div class="card-head"><h2>中文译文</h2><span class="pill ${chunk.status==='REVIEW'?'warning':''}">${chunk.status==='DONE'&&!currentQuality?'历史译文 · 等待重新检查':translationNames[chunk.status]}</span></div>${chunk.error?`<div class="warning-box">${e(chunk.error)}</div>`:''}<pre class="report-text">${e(chunk.translated||'尚未翻译。点击“翻译本段”，或安排全文分段翻译。')}</pre>${chunk.versions?.length?`<details><summary>历史译文（${chunk.versions.length}版，均保留）</summary>${chunk.versions.map(v=>`<p class="field-hint">${e(v.created)} · ${e(v.model||'')} · ${e(v.status)}</p><pre class="report-text">${e(v.translated)}</pre>`).join('')}</details>`:''}</section></div><div class="actions">${button('上一段','translation-prev',false,selectedOrdinal<=1?'disabled':'')}${button('下一段','translation-next',false,selectedOrdinal>=doc.chunks.length?'disabled':'')}</div>`:'<div class="card empty-state"><div class="empty-icon">文</div><h2>把原始财报读成中文</h2><p>上传一份财报，或先粘贴一个章节。原文会与译文一起保存，不把翻译结果当作已经核实的财务事实。</p></div>');
}

function maintenancePanel(){
 const list=S.backups||[],report=S.diagnostics;
 return `<section class="card" id="maintenance-panel"><div class="card-head"><h2>运行检查与本地备份</h2>${button('检查运行环境','diagnostics')}</div>${report?`<p class="field-hint">最近检查：${e(report.checked_at)}</p>${report.checks.map(c=>`<div class="material row"><div><strong>${e(c.name)}</strong><div class="subtle">${e(c.detail)}</div></div><span class="pill ${c.ok?'':'warning'}">${c.ok?'可用':'需要处理'}</span></div>`).join('')}`:'<p class="muted">可以检查本地保存、数据库、Codex登录、PDF读取和Excel导出环境。</p>'}<div class="spacer"></div><h3>备份与恢复</h3><p class="muted">运行时每天自动备份一次，保留最近7份自动备份。手动备份另外保留，包含研究记录、AI对话、原文与翻译进度。</p><div class="actions">${button('立即创建备份','backup',true)}</div><div class="spacer"></div><form data-form="backup-import"><label for="backup-file">导入已下载的ZIP备份（不超过18MB）</label><input id="backup-file" type="file" accept=".zip" required><div class="spacer"></div><button class="btn" type="submit">上传备份到本机</button><p class="field-hint">上传只加入备份列表，不会立即覆盖研究记录。</p></form>${list.length?`<div class="spacer"></div><label for="backup-selector">已有备份</label><select id="backup-selector">${list.map(b=>`<option value="${e(b.name)}">${e(b.name)} · ${n(b.bytes/1024,0)} KB</option>`).join('')}</select><div class="spacer"></div><div class="actions">${button('下载所选备份','backup-download')}${button('恢复所选备份','restore')}</div><p class="field-hint">恢复会先另存当前记录；请先暂停翻译并等待当前AI任务完成。恢复后页面会重新载入。</p>`:'<p class="muted">还没有备份，可点击立即创建。</p>'}</section>`;
}

render=function(){
 if(!S)return;
 if(page()==='translation'){
  document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.dataset.page==='translation'));
  $('#page-label').textContent=pages.translation;$('#date-label').textContent=S.today+' · 北京时间';$('#main').innerHTML=translationPage();
 }else{
  originalRender();
  if(page()==='settings')$('#main').insertAdjacentHTML('beforeend',maintenancePanel());
  if(page()==='learning'){
   const p=currentPack(),last=p?.answers.at(-1);
   if(last&&last.revision!==p.revision)$('#main').insertAdjacentHTML('afterbegin','<div class="warning-box">资料已更新。下方保留的是旧版回答，修改或审核后请提交当前版本；旧版原始回答和证据仍可在历史中查看。</div>');
  }
 }
 restoreForms();
 const footer=document.querySelector('footer');if(footer&&!footer.querySelector('[data-version]'))footer.insertAdjacentHTML('beforeend',`<span data-version>V${e(S.version||'1.1.1')}</span>`);
 if(S.revision)lastRevision=S.revision;
};

function showPending(text,reload=false){
 const banner=$('#update-banner');banner.hidden=false;banner.innerHTML=`<span>${e(text)}</span><button class="btn small" data-v11="${reload?'reload':'apply-state'}">${reload?'重新载入（保留文字草稿）':'更新页面（保留草稿）'}</button>`;
}
window.pollV11=async()=>{
 if(!S||busy||polling)return;polling=true;
 try{
  const rev=await request('/api/revision');if(rev.revision===lastRevision)return;
  const state=await request('/api/state');lastRevision=state.revision;
  const editing=document.activeElement?.matches('input,textarea,select')||[...document.querySelectorAll('form[data-form]')].some(f=>draftForms.has(formKey(f)))||[...document.querySelectorAll('input[type=file]')].some(f=>f.files.length);
  if(editing){pendingState=state;showPending('后台有新资料或任务进度。你的输入会保留。');return}
  S=state;window.acceptV11State();render();
 }catch(err){if(/校验|首页|本机/.test(err.message))showPending('本地服务已更新或重新启动。',true)}finally{polling=false}
};

async function downloadLocal(url,name){
 const response=await fetch(url,{headers:{'X-App-Token':token}});
 if(!response.ok){let message='下载未完成';try{message=(await response.json()).error||message}catch{}throw Error(message)}
 const blob=await response.blob(),link=document.createElement('a'),objectURL=URL.createObjectURL(blob);
 link.href=objectURL;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(objectURL),30000);
}
function base64File(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file)})}

document.addEventListener('input',ev=>{const form=ev.target.closest('form[data-form]');if(form)rememberForm(form)});
document.addEventListener('change',ev=>{
 if(ev.target.id==='import-kind')updateImportMode();
 if(ev.target.id==='report-file'&&ev.target.files[0]&&!document.querySelector('[name=title]').value)document.querySelector('[name=title]').value=ev.target.files[0].name;
 if(ev.target.id==='document-selector'){selectedDocument=Number(ev.target.value);selectedOrdinal=1;render()}
 if(ev.target.id==='chunk-selector'){selectedOrdinal=Number(ev.target.value);render()}
 const form=ev.target.closest('form[data-form]');if(form)rememberForm(form);
});
document.addEventListener('submit',async ev=>{
 const form=ev.target.closest('form[data-form]');if(!form)return;
 lastSubmitted=formKey(form);
 if(!['document-import','backup-import'].includes(form.dataset.form))return;
 ev.preventDefault();ev.stopImmediatePropagation();if(busy)return;
 try{
  if(form.dataset.form==='backup-import'){const file=$('#backup-file').files[0];if(!file)throw Error('请选择ZIP备份');if(file.size>18000000)throw Error('上传备份不能超过18MB');await action('/api/backup-import',{content_base64:await base64File(file)},'备份已加入列表，可选择后恢复');return}const data=Object.fromEntries(new FormData(form));delete data['report-file'];
  if(data.kind==='file'){
   const file=$('#report-file').files[0];if(!file)throw Error('请选择财报文件');
   if(file.size>18000000)throw Error('财报文件不能超过18MB');
   data.filename=file.name;data.content_base64=await base64File(file);
  }
  const result=await action('/api/document-import',data,'财报原文已保存，可以开始翻译','正在读取财报并划分段落…');
  if(result.warning||result.empty_pages?.length)toast([result.warning,result.empty_pages?.length?'部分PDF页没有可读取文字，请对照原件：'+result.empty_pages.slice(0,20).join('、')+'页':''].filter(Boolean).join(' '),true);
 }catch(err){toast(err.message,true)}
},true);

document.addEventListener('click',async ev=>{
 const target=ev.target.closest('[data-action],[data-v11]');if(!target)return;
 const type=target.dataset.v11||target.dataset.action;
 const known=['document-assign','read-chinese','apply-state','reload','translate-one','translate-retry','translate-all','translation-pause','translation-export','translation-prev','translation-next','document-download','diagnostics','backup','backup-download','restore'];
 if(!known.includes(type))return;
 ev.preventDefault();ev.stopImmediatePropagation();if(busy)return;
 try{
  if(type==='document-assign'){await action('/api/document-assign',{document_id:Number(target.dataset.id)},'财报已归档到当前公司');return}if(type==='read-chinese'){pendingImport={url:target.dataset.url,title:target.dataset.title,period:target.dataset.period};if(page()==='translation')render();else location.hash='translation';return}if(type==='reload'){location.reload();return}
  if(type==='apply-state'){if([...document.querySelectorAll('input[type=file]')].some(input=>input.files.length)){toast('文件已选择，请先导入或清空选择，再更新页面；文字草稿已保留',true);return}S=pendingState||await request('/api/state');pendingState=null;$('#update-banner').hidden=true;render();return}
  if(type==='translation-prev'||type==='translation-next'){selectedOrdinal+=type==='translation-prev'?-1:1;render();return}
  if(type==='translate-one'||type==='translate-retry'||type==='translate-all')await action('/api/translate',{document_id:Number(target.dataset.id),...(type==='translate-one'||type==='translate-retry'?{ordinal:Number(target.dataset.ordinal),retry:type==='translate-retry'}:{})},'已安排翻译，完成的段落会自动保存');
  else if(type==='translation-pause')await action('/api/translation-pause',{document_id:Number(target.dataset.id)},'已安排暂停，正在处理的段落会先保存');
  else if(type==='translation-export'){
   const result=await action('/api/translation-export',{document_id:Number(target.dataset.id)},'中英对照已导出');
   for(const file of result.files)await downloadLocal('/downloads/'+encodeURIComponent(file),file);
  }else if(type==='document-download')await downloadLocal('/document-file/'+target.dataset.id,'report-original.'+(S.documents.find(d=>d.id===Number(target.dataset.id))?.source_kind.split(':').at(-1)||'txt'));
  else if(type==='diagnostics')await action('/api/diagnostics',{},'运行环境检查完成','正在检查本地环境…');
  else if(type==='backup')await action('/api/backup',{},'本地备份已创建','正在备份研究记录、原文与翻译…');
  else if(type==='backup-download'){const name=$('#backup-selector').value;await downloadLocal('/backup-download/'+encodeURIComponent(name),name)}
  else if(type==='restore'){
   const name=$('#backup-selector').value;
   if(!confirm('恢复“'+name+'”？当前记录会先另存安全备份。恢复后将重新载入页面。'))return;
   await action('/api/restore',{name,confirm:true},'已恢复备份','正在校验并恢复备份…');
  }
 }catch(err){toast(err.message,true)}
},true);

if(S)render();
