import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const runtimeModules=process.env.ERT_RUNTIME_MODULES||path.join(process.env.USERPROFILE||process.env.HOME||'', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const resolveRuntime=createRequire(path.join(runtimeModules,'_resolver.cjs'));
const {Workbook,SpreadsheetFile}=await import(pathToFileURL(resolveRuntime.resolve('@oai/artifact-tool')).href);

const [input,output]=process.argv.slice(2);
if(!input||!output)throw Error('Expected input.json and output.xlsx');
const d=JSON.parse(await fs.readFile(input,'utf8'));
const payload=d.valuation.payload,a=payload.assumptions,cases=payload.result.cases;
const w=Workbook.create();
const summary=w.worksheets.add('研究估值');
const model=w.worksheets.add('估值模型');
const evidence=w.worksheets.add('原始证据');
const num='#,##0.0;(#,##0.0);"-"',price='0.00;(0.00);"-"';
const text=(sheet,cell,v)=>sheet.getRange(cell).values=[[typeof v==='string'&&v.startsWith('=')?"'"+v:v]];
const formula=(sheet,cell,v)=>sheet.getRange(cell).formulas=[[v]];
for(const [s,area] of [[summary,'A1:H30'],[model,'A1:S48'],[evidence,`A1:I${Math.max(10,d.facts.length+6)}`]]){
  s.showGridLines=false;s.getRange(area).format.font.name='Arial';s.getRange(area).format.font.size=10;
  s.getRange(area).format.rowHeight=23;s.getRange(area).format.verticalAlignment='center';
  s.getRange(area).format.font.color='#243C34';s.getRange(area).format.columnWidth=16;
}
summary.tabColor='#214B3D';model.tabColor='#78916C';
for(const s of [summary,model,evidence])s.getRange('A1:A48').format.columnWidth=3;
text(summary,'B2',`${d.company.ticker} 研究估值`);summary.getRange('B2').format.font.size=16;
text(summary,'B4','当前情景');formula(summary,'C4',"='估值模型'!C5");summary.getRange('C4').format.font.color='#008000';
summary.getRange('B6:C11').values=[['指标','当前模型'],['每股价值（USD）',null],['企业价值（百万USD）',null],['股权价值（百万USD）',null],['终值现值占比',null],['计算版本','fcff-roic-v1']];
for(const [r,ref] of [[7,31],[8,29],[9,30]])formula(summary,`C${r}`,`='估值模型'!C${ref}`);
formula(summary,'C10',"='估值模型'!C28/'估值模型'!C29");summary.getRange('C7:C9').setNumberFormat(price);summary.getRange('C10').setNumberFormat('0.0%');
text(summary,'B14','三情景比较（导出时的计算快照）');
summary.getRange('B16:E19').values=[['情景','每股价值（USD）','收入增长率','营业利润率'],...cases.map(c=>[c.name,c.per_share,c.assumptions.growth,c.assumptions.margin])];
summary.getRange('C17:C19').setNumberFormat(price);summary.getRange('D17:E19').setNumberFormat('0.0%');
text(summary,'B22','比较快照不会随Excel输入修改而更新；修改假设后请在工作台重新计算并导出。');
text(summary,'B24',`估值版本 ${d.valuation.id} · 保存时间 ${d.valuation.created} · 证据快照 ${d.valuation.snapshot_id??'未绑定'}`);
text(summary,'B26','三情景的增长与利润率在基准上±3个百分点，增长受ROIC限制；不代表概率预测。');
text(model,'B2','FCFF估值模型');model.getRange('B2').format.font.size=16;
text(model,'B4','情景选择（1/2/3）');text(model,'C4',2);
model.getRange('C4').dataValidation={rule:{type:'whole',operator:'between',formula1:1,formula2:3}};
text(model,'B5','当前情景');formula(model,'C5','=CHOOSE(C4,"保守","基准","乐观")');
const cells={revenue:8,growth:9,margin:10,tax:11,roic:12,wacc:13,terminal_growth:14,terminal_roic:15,cash:16,debt:17,other_claims:18,shares:19,years:20};
const labels={revenue:'基期收入（百万USD）',growth:'预测增长率',margin:'营业利润率',tax:'有效税率',roic:'预测期ROIC',wacc:'WACC',terminal_growth:'永续增长率',terminal_roic:'稳态ROIC',cash:'可加回现金（百万USD）',debt:'债务（百万USD）',other_claims:'其他请求权（百万USD）',shares:'稀释股数（百万股）',years:'预测年限'};
for(const [key,row] of Object.entries(cells)){
  text(model,`B${row}`,labels[key]);text(model,`C${row}`,a[key]);
  model.getRange(`C${row}`).format.font.color='#0000FF';model.getRange(`C${row}`).format.fill='#FFF7DD';
  model.getRange(`C${row}`).setNumberFormat(['growth','margin','tax','roic','wacc','terminal_growth','terminal_roic'].includes(key)?'0.0%':num);
}
formula(model,'C9','=CHOOSE($C$4,C40,C41,C42)');formula(model,'C10','=CHOOSE($C$4,D40,D41,D42)');
model.getRange('C9:C10').format.font.color='#000000';model.getRange('C9:C10').format.fill='#F1F5ED';
text(model,'B38','可编辑的情景驱动');model.getRange('B39:D42').values=[['情景','增长率','营业利润率'],...cases.map(c=>[c.name,c.assumptions.growth,c.assumptions.margin])];
model.getRange('C40:D42').format.font.color='#0000FF';model.getRange('C40:D42').format.fill='#FFF7DD';model.getRange('C40:D42').setNumberFormat('0.0%');
model.getRange('E7:S7').values=[Array.from({length:15},(_,i)=>i+1)];
text(model,'E5','年末折现；金额为百万USD。超出预测年限的列显示为横线。');
for(let i=0;i<15;i++){
  const col=String.fromCharCode(69+i),prev=String.fromCharCode(68+i);
  formula(model,`${col}9`,`=IF(${col}$7<=$C$20,${i===0?'$C$8':`${prev}9`}*(1+$C$9),0)`);
  formula(model,`${col}10`,`=${col}9*$C$10`);
  formula(model,`${col}11`,`=${col}10*(1-$C$11)`);
  formula(model,`${col}12`,`=${col}11*$C$9/$C$12`);
  formula(model,`${col}13`,`=${col}11-${col}12`);
  formula(model,`${col}14`,`=${col}13/(1+$C$13)^${col}$7`);
}
model.getRange('E9:S14').setNumberFormat(num);model.getRange('E9:S14').format.font.color='#000000';
for(const [row,label] of [[9,'收入'],[10,'营业利润'],[11,'税后经营利润'],[12,'净再投资'],[13,'FCFF'],[14,'FCFF现值']])text(model,`D${row}`,label);
const outputs=[
 [24,'最后预测年NOPAT','=INDEX(E11:S11,1,C20)'],[25,'稳态第一年NOPAT','=C24*(1+C14)'],
 [26,'稳态FCFF','=C25*(1-C14/C15)'],[27,'永续增长终值','=IF(C13<=C14,"n.a.",C26/(C13-C14))'],
 [28,'终值现值','=IF(ISNUMBER(C27),C27/(1+C13)^C20,"n.a.")'],
 [29,'企业价值','=IF(ISNUMBER(C28),SUM(E14:S14)+C28,"n.a.")'],
 [30,'股权价值','=IF(ISNUMBER(C29),C29+C16-C17-C18,"n.a.")'],
 [31,'每股价值（USD）','=IF(AND(ISNUMBER(C30),C19>0,C14>=0,C14<=0.08,C14<=C15,C13>0,C13<=0.5,C9>=0,C9<=C12),C30/C19,"n.a.")']];
for(const [row,label,f] of outputs){text(model,`B${row}`,label);formula(model,`C${row}`,f);model.getRange(`C${row}`).setNumberFormat(row===31?price:num)}
text(model,'E18','每股价值敏感性：行WACC，列永续增长率（当前所选情景）');
formula(model,'E20','=C31');
const shifts=[-.01,-.005,0,.005,.01],wshifts=[-.02,-.01,0,.01,.02];
// Baseline headers are fixed at export so data-table input substitution cannot move its axes.
model.getRange('F20:J20').values=[shifts.map(x=>a.terminal_growth+x)];
model.getRange('E21:E25').values=wshifts.map(x=>[a.wacc+x]);
model.dataTables.add('E20:J25',{rowInput:'C14',columnInput:'C13'});
model.getRange('F20:J20').setNumberFormat('0.0%');model.getRange('E21:E25').setNumberFormat('0.0%');model.getRange('F21:J25').setNumberFormat(price);
model.getRange('E20').setNumberFormat(price);
text(model,'E28','输入蓝字为用户假设；黑字为公式。敏感性轴为本次导出参数附近的固定测试点。');
text(model,'E30','净再投资=NOPAT×增长率/ROIC。SBC不额外加回，未显式拆分折旧、CapEx与营运资本。');
text(model,'E32','请自行核对现金、有息债务、租赁、少数股权和稀释股数的范围；不适用于银行、保险或REIT。');
text(model,'B45','输入依据');text(model,'C45',payload.basis);model.getRange('C45:S46').format.wrapText=true;model.getRange('C45').format.rowHeight=50;
text(evidence,'B2','原始财务证据');evidence.getRange('B2').format.font.size=16;
text(evidence,'B4','金额为USD原值；官方XBRL仍须核对原表，手动录入为待核实。以下为该估值绑定快照。');
evidence.getRange(`B6:I${6+d.facts.length}`).values=[['指标','原值（USD）','期间开始','期间结束','披露日期','标签 / 位置','来源','核验状态'],...d.facts.map(f=>[f.metric,f.value,f.start,f.end,f.filed,f.tag,f.url,f.status])];
evidence.getRange(`C7:C${Math.max(7,6+d.facts.length)}`).setNumberFormat('#,##0');
evidence.getRange('G6:H46').format.columnWidth=48;evidence.getRange('B6:F46').format.columnWidth=20;
for(const [s,range] of [[summary,'B6:C6'],[summary,'B16:E16'],[model,'E7:S7'],[model,'B39:D39'],[model,'E20:J20'],[evidence,'B6:I6']]){
  s.getRange(range).format.fill='#214B3D';s.getRange(range).format.font.color='#FFFFFF';s.getRange(range).format.font.bold=true;
}
summary.getRange('B1:B30').format.columnWidth=30;summary.getRange('C1:E30').format.columnWidth=23;
model.getRange('B1:B48').format.columnWidth=29;model.getRange('C1:C48').format.columnWidth=18;model.getRange('D1:D48').format.columnWidth=20;
model.freezePanes.freezeRows(7);evidence.freezePanes.freezeRows(6);
w.recalculate();
// Compare each selector value against independently calculated Python cash flows.
for(let i=1;i<=3;i++){
  text(model,'C4',i);w.recalculate();
  const actual=Number(model.getRange('C31').values[0][0]);
  if(!Number.isFinite(actual)||Math.abs(actual-cases[i-1].per_share)>Math.max(1e-7,Math.abs(actual)*1e-8))throw Error('Python/Excel case mismatch: '+i+' '+actual);
}
text(model,'C4',2);w.recalculate();
for(let i=0;i<5;i++)for(let j=0;j<5;j++){
  const cell=model.getCell(20+i,5+j).values[0][0],expected=payload.result.sensitivity[i][j].value;
  if(expected!==null&&(!Number.isFinite(Number(cell))||Math.abs(Number(cell)-expected)>Math.max(1e-6,Math.abs(expected)*1e-7)))throw Error('Sensitivity mismatch '+i+','+j+': '+cell+' vs '+expected);
}
await fs.mkdir(path.dirname(path.resolve(output)),{recursive:true});
const file=await SpreadsheetFile.exportXlsx(w);await file.save(output);
if(process.env.ERT_EXPORT_PREVIEW){
  for(const [name,range,suffix] of [['研究估值','A1:F27','summary'],['估值模型','A1:K33','model'],['原始证据',`A1:I${Math.min(12,d.facts.length+6)}`,'evidence']]){
    const blob=await w.render({sheetName:name,range,scale:1.5,format:'png'});
    await fs.writeFile(process.env.ERT_EXPORT_PREVIEW+'_'+suffix+'.png',new Uint8Array(await blob.arrayBuffer()));
  }
}
console.log(JSON.stringify({ok:true,cases:3,sensitivity:25,output}));
