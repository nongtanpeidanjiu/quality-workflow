# 检测 Python；找不到就自动装一份免安装版到 %USERPROFILE%\.quality-workflow\python\。
# 不改系统设置，不需要管理员权限。只用 Windows 自带的 PowerShell、curl.exe、tar.exe。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File "<插件目录>\bin\setup-python.ps1"
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
. (Join-Path $PSScriptRoot 'find-python.ps1')
$QfPy = Find-QfPython -Fresh
if ($QfPy) { Write-Output "Python 可用（已有）：$QfPy"; exit 0 }

function Show-Manual {
  Write-Output '没能自动装好 Python。请按下面任意一种办法安装，装好后再运行一次本命令：'
  Write-Output '  - 在终端运行：winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements'
  Write-Output '  - 或者下载安装：https://www.python.org/downloads/windows/ （安装时勾选 Add python.exe to PATH）'
  Write-Output '  - 国内下载较快的地址：https://registry.npmmirror.com/binary.html?path=python/3.12.10/ （选 python-3.12.10-amd64.exe）'
  exit 1
}

# 固定版本和校验值，下载后核对，不一致就不用。
$Tag = '20261110'
$PyV = '3.12.16'
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$triple = $null; $sum = $null
if ($arch -eq 'AMD64') { $triple = 'x86_64-pc-windows-msvc'; $sum = '355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc' }
elseif ($arch -eq 'ARM64') { $triple = 'aarch64-pc-windows-msvc'; $sum = 'c1b334c7b1db8f962b37a333d8362cab0ba7ebda1a7cce917fae56a1e0c8cbe7' }

$installed = $false
if (-not $triple) {
  Write-Output "这个系统（$arch）没有对应的免安装版 Python。"
} else {
  $enc = "cpython-$PyV%2B$Tag-$triple-install_only_stripped.tar.gz"
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('qflow-python-' + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  try {
    $archive = Join-Path $tmp 'python.tar.gz'
    $sys32 = Join-Path $env:SystemRoot 'System32'
    $ok = $false
    foreach ($url in @("https://registry.npmmirror.com/-/binary/python-build-standalone/$Tag/$enc",
                       "https://github.com/astral-sh/python-build-standalone/releases/download/$Tag/$enc")) {
      Write-Output ("正在下载 Python $PyV：" + ([Uri]$url).Host)
      Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
      $got = $false
      $curl = Join-Path $sys32 'curl.exe'
      if (Test-Path -LiteralPath $curl) {
        & $curl -fsSL --max-time 300 -o $archive $url 2>$null
        $got = ($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $archive)
      }
      if (-not $got) {
        try {
          [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
          Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing -TimeoutSec 300
          $got = Test-Path -LiteralPath $archive
        } catch { $got = $false }
      }
      if (-not $got) { Write-Output '下载没有成功，换下一个地址。'; continue }
      if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $sum) { Write-Output '文件校验不一致，丢弃，换下一个地址。'; continue }
      $ok = $true; break
    }
    if ($ok) {
      $stage = Join-Path $tmp 'out'
      New-Item -ItemType Directory -Force -Path $stage | Out-Null
      # 用系统自带的 tar；Git Bash 里的 tar 不认带盘符的路径。
      & (Join-Path $sys32 'tar.exe') -xzf $archive -C $stage
      if ($LASTEXITCODE -ne 0) {
        Write-Output '解压没有成功。'
      } else {
        $dest = Join-Path $QfHome 'python'
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $QfHome | Out-Null
        Move-Item -LiteralPath (Join-Path $stage 'python') -Destination $dest
        $QfPy = Find-QfPython -Fresh
        if ($QfPy) { Write-Output "Python 可用（已自动安装到 $dest）：$QfPy"; $installed = $true }
        else { Write-Output '装好的 Python 无法运行。' }
      }
    }
  } catch {
    Write-Output ("自动安装出错：" + $_.Exception.Message)
  } finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}
if ($installed) { exit 0 }

# 再试一次系统的包管理器。
Write-Output '改用 winget 安装。'
try {
  & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
} catch {}
$QfPy = Find-QfPython -Fresh
if ($QfPy) { Write-Output "Python 可用（已用 winget 安装）：$QfPy"; exit 0 }
Write-Output 'winget 没有装上。'
Show-Manual
