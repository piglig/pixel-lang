// Install a candidate VSIX into a disposable profile, then test the installed
// extension. The development extension is an empty test-host driver only.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const crypto = require('node:crypto'), cp = require('node:child_process');
const {runTests, downloadAndUnzipVSCode, resolveCliArgsFromVSCodeExecutablePath} = require('@vscode/test-electron');
const [vsixArg, pythonArg, compilerArg, reportArg] = process.argv.slice(2);
if (!vsixArg || !pythonArg || !compilerArg || !reportArg) {
  throw Error('Usage: node scripts/test-installed.js candidate.vsix python compiler.json report.json');
}
const vsix = path.resolve(vsixArg), python = path.resolve(pythonArg), compiler = path.resolve(compilerArg), reportPath = path.resolve(reportArg);
if (fs.existsSync(reportPath)) throw Error('Report already exists');
const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-installed-vsix-'));
const extensions = path.join(root, 'extensions'), user = path.join(root, 'user'), workspace = path.join(root, 'workspace'), driver = path.join(root, 'driver');
for (const folder of [extensions, user, workspace, driver]) fs.mkdirSync(folder);
fs.writeFileSync(path.join(workspace, 'main.pxl'), 'fn main() {}\n');
fs.writeFileSync(path.join(driver, 'package.json'), JSON.stringify({name:'pixel-acceptance-driver',publisher:'pixel-tests',version:'0.0.1',engines:{vscode:'^1.90.0'}}));
const suiteName = process.env.PIXELLANG_TEST_SUITE || 'native-spatial-debug';
const suites = {'native-build-cancellation':'native-build-cancellation-integration.js','native-diagnostics':'native-diagnostics-integration.js','native-lazy-debug':'native-lazy-debug-integration.js','native-undo':'native-undo-integration.js','native-pixel-edit':'native-pixel-edit-integration.js','native-format':'native-format-integration.js','application':'application-integration.js','native-computed':'native-computed-integration.js','native-spatial-debug':'native-spatial-debug-integration.js','native-graphs':'native-graphs-integration.js','native-spatial-pointer':'native-spatial-pointer-integration.js','native-recovered':'native-recovered-integration.js'};
if (!suites[suiteName]) throw Error('Unknown installed acceptance suite');
const suite = path.resolve(__dirname, '../test', suites[suiteName]);
fs.writeFileSync(path.join(driver, 'test.js'), `
const vscode=require('vscode'), assert=require('node:assert/strict'), path=require('node:path'), fs=require('node:fs'), crypto=require('node:crypto');
exports.run=async()=>{
 const extension=vscode.extensions.getExtension('pixellang-local.pixellang-studio');
 assert(extension,'VSIX extension must be installed');
 const installed=fs.realpathSync(extension.extensionPath), relative=path.relative(fs.realpathSync(${JSON.stringify(extensions)}),installed);
 assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative),'Extension must belong to disposable installation');
 const compiler=fs.readFileSync(path.join(installed,'runtime/pixellang/artifacts/compiler.json'));
 assert.equal(crypto.createHash('sha256').update(compiler).digest('hex'),${JSON.stringify(digest(compiler))});
 try { await require(${JSON.stringify(suite)}).run(); }
 catch(error) {
  fs.writeFileSync(${JSON.stringify(path.join(root,'failure.json'))},JSON.stringify({message:String(error),stack:error.stack}));
  console.error(error);throw error;
 }
 fs.writeFileSync(${JSON.stringify(path.join(root,'result.json'))},JSON.stringify({installedPath:installed,passed:true}));
};
`);
const recoveredSource=process.env.PIXELLANG_RECOVERED_SOURCE ? path.resolve(process.env.PIXELLANG_RECOVERED_SOURCE) : undefined;
const recoveredSourceSha256=recoveredSource ? digest(recoveredSource) : undefined;
const report = {verified:false,environment:root,recoveredSource,recoveredSourceSha256,suite:suiteName,runnerSha256:digest(__filename),suiteSha256:digest(suite),vsixSha256:digest(vsix),compilerSha256:digest(compiler)};
async function main() {
 try {
  let executable = process.env.PIXELLANG_VSCODE || await downloadAndUnzipVSCode({version:'1.137.0',cachePath:path.resolve(__dirname,'../.vscode-test')});
  if (!fs.existsSync(executable) && process.platform==='darwin') executable=path.join(path.dirname(executable),'Code');
  if (!fs.existsSync(executable)) throw Error('VS Code executable not found');
  const [cli,...cliArgs]=resolveCliArgsFromVSCodeExecutablePath(executable,{reuseMachineInstall:true});
  const install=cp.spawnSync(cli,[...cliArgs,'--user-data-dir',user,'--extensions-dir',extensions,'--install-extension',vsix,'--force'],{encoding:'utf8',timeout:120000});
  report.install={exitCode:install.status,stdout:install.stdout,stderr:install.stderr};
  if(install.error || install.status!==0) throw install.error || Error(install.stderr || install.stdout);
  await runTests({vscodeExecutablePath:executable,extensionDevelopmentPath:driver,extensionTestsPath:path.join(driver,'test.js'),
   extensionTestsEnv:{PIXELLANG_TEST_LOGS:path.resolve(__dirname,'../../examples/log-analysis'),PIXELLANG_RECOVERED_SOURCE:recoveredSource,PIXELLANG_TEST_PYTHON:python,PIXELLANG_INTERACTION_REPORT:path.join(root,'interaction.json')},launchArgs:[workspace,'--user-data-dir',user,'--extensions-dir',extensions]});
  report.result=JSON.parse(fs.readFileSync(path.join(root,'result.json'),'utf8'));
  if(digest(__filename)!==report.runnerSha256 || digest(suite)!==report.suiteSha256 || digest(vsix)!==report.vsixSha256 || digest(compiler)!==report.compilerSha256) throw Error('Acceptance artifacts changed');
  if(recoveredSource && digest(recoveredSource)!==recoveredSourceSha256) throw Error('Recovered input changed');
  report.verified=report.result.passed;
 } catch(error) { report.error=String(error); if(fs.existsSync(path.join(root,'failure.json'))) report.failure=JSON.parse(fs.readFileSync(path.join(root,'failure.json'),'utf8')); throw error; }
 finally {fs.mkdirSync(path.dirname(reportPath),{recursive:true});fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n');}
 console.log(JSON.stringify(report));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
