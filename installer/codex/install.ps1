# quality-workflow（Codex 版）一键安装，Windows 用：
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
# 按“电脑上什么都没有”来设计，只用 Windows 自带的 PowerShell、curl.exe、tar.exe：
# 先检测环境，缺 Python 就自动装一份免安装版（不改系统设置，不要管理员权限），再安装插件。
# 不需要 git、Node，也不要求命令路径里有 codex。
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONUTF8 = '1'
$Repo = 'nongtanpeidanjiu/quality-workflow'
$pluginSrc = Join-Path $PSScriptRoot 'marketplace\plugins\quality-workflow'
$clean = $null

if (Test-Path -LiteralPath (Join-Path $pluginSrc 'bin\qf_install.py')) {
  $root = $pluginSrc
  $from = Join-Path $PSScriptRoot 'marketplace'
} else {
  # 没有随安装包一起来：把安装程序本身先下载下来。
  Write-Output '正在下载安装程序……'
  $root = Join-Path ([IO.Path]::GetTempPath()) ('qflow-bootstrap-' + [Guid]::NewGuid().ToString('N'))
  $clean = $root
  $from = $null
  New-Item -ItemType Directory -Force -Path (Join-Path $root 'bin'), (Join-Path $root '.codex-plugin') | Out-Null
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  foreach ($f in @('bin/find-python.ps1', 'bin/setup-python.ps1', 'bin/qf_install.py', 'bin/qf_common.py', '.codex-plugin/plugin.json')) {
    $got = $false
    foreach ($base in @("https://raw.githubusercontent.com/$Repo/main/codex", "https://cdn.jsdelivr.net/gh/$Repo@main/codex")) {
      try {
        Invoke-WebRequest -Uri "$base/$f" -OutFile (Join-Path $root ($f -replace '/', '\')) -UseBasicParsing -TimeoutSec 60
        $got = $true; break
      } catch { $got = $false }
    }
    if (-not $got) {
      Write-Output '安装没有完成：连不上 GitHub，镜像也连不上。请下载安装包（压缩包），解压后运行里面的 install.ps1。'
      Remove-Item -LiteralPath $clean -Recurse -Force -ErrorAction SilentlyContinue
      exit 1
    }
  }
}

Write-Output '第 0 步：检测 Python'
. (Join-Path $root 'bin\find-python.ps1')
if (-not $QfPy) {
  Write-Output '  没有找到 Python，正在自动安装免安装版（不改系统设置）……'
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\setup-python.ps1') | ForEach-Object { "  $_" }
  $QfPy = Find-QfPython -Fresh
}
if (-not $QfPy) {
  Write-Output '安装没有完成：这台电脑没有可用的 Python，自动安装也没有成功。请按上面列出的办法装好 Python，再运行一次本脚本。'
  if ($clean) { Remove-Item -LiteralPath $clean -Recurse -Force -ErrorAction SilentlyContinue }
  exit 1
}
Write-Output "  Python：$QfPy"
Write-Output ''

$ErrorActionPreference = 'Continue'
if ($from) { & $QfPy -B (Join-Path $root 'bin\qf_install.py') install --from $from }
else { & $QfPy -B (Join-Path $root 'bin\qf_install.py') install }
$code = $LASTEXITCODE
if ($clean) { Remove-Item -LiteralPath $clean -Recurse -Force -ErrorAction SilentlyContinue }
exit $code
