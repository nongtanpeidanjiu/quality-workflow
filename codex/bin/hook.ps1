# Codex 钩子入口（Windows）。只用系统自带的 PowerShell：找到 Python 后交给 codex_hook.py。
# 钩子的输入按原始字节转给 Python，输出由 Python 直接写出，中间不经过 PowerShell 的编码转换。
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'find-python.ps1')

function Read-AllStdin {
  $ms = New-Object IO.MemoryStream
  ([Console]::OpenStandardInput()).CopyTo($ms)
  return ,$ms.ToArray()
}

function Write-Utf8($text) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($text)
  $out = [Console]::OpenStandardOutput()
  $out.Write($bytes, 0, $bytes.Length); $out.Flush()
}

function Invoke-Hook($py, [byte[]]$data) {
  $env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONUTF8 = '1'
  $p = New-Object Diagnostics.Process
  $p.StartInfo.FileName = $py
  $p.StartInfo.Arguments = '-B "' + (Join-Path $root 'hooks\codex_hook.py') + '"'
  $p.StartInfo.UseShellExecute = $false
  $p.StartInfo.RedirectStandardInput = $true
  $p.StartInfo.CreateNoWindow = $true
  [void]$p.Start()
  $p.StandardInput.BaseStream.Write($data, 0, $data.Length)
  $p.StandardInput.Close()
  $p.WaitForExit()
  exit $p.ExitCode
}

$data = Read-AllStdin
if ($QfPy) { Invoke-Hook $QfPy $data }

# 没有 Python。
$text = [Text.Encoding]::UTF8.GetString($data)
$setup = 'powershell -NoProfile -ExecutionPolicy Bypass -File \"' + ((Join-Path $PSScriptRoot 'setup-python.ps1') -replace '\\', '\\') + '\"'
if ($text -match '"SessionStart"') {
  # 新任务开始时自动装一次，装好就接着往下走。
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup-python.ps1') | Out-Null
  $QfPy = Find-QfPython -Fresh
  if ($QfPy) { Invoke-Hook $QfPy $data }
  Write-Utf8 ('{"systemMessage":"quality-workflow：找不到可用的 Python，流程关卡没有生效。","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"【quality-workflow】流程关卡没有生效：找不到可用的 Python，自动安装也没有成功。请先运行 ' + $setup + '，按它的提示装好 Python，再新建任务。"}}')
} elseif ($text -match '"PreToolUse"') {
  # 已经建档的任务，关卡失效时不放行。
  if (Test-Path -LiteralPath '.flow\tasks') {
    Write-Utf8 ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"【quality-workflow】流程关卡没有生效：找不到可用的 Python。请先运行 ' + $setup + ' 装好 Python。"}}')
  }
}
exit 0
