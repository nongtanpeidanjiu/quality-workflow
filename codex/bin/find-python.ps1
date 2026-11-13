# 找一个能用的 Python（3.8 以上），结果放在变量 $QfPy 里；找不到时为空。
# 用法：. "<插件目录>\bin\find-python.ps1"。只用 Windows 自带的 PowerShell。
$QfHome = if ($env:QFLOW_HOME) { $env:QFLOW_HOME } else { Join-Path $env:USERPROFILE '.quality-workflow' }
$QfPyCache = Join-Path $QfHome 'python-path.txt'

function Test-QfPython($exe) {
  if (-not $exe -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
  # 应用商店的 python 占位程序会打开商店，不能用。
  if ($exe -match 'WindowsApps') { return $false }
  try {
    & $exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Find-QfPython([switch]$Fresh) {
  if (-not $Fresh -and (Test-Path -LiteralPath $QfPyCache)) {
    $c = (Get-Content -LiteralPath $QfPyCache -Raw -ErrorAction SilentlyContinue)
    if ($c) { $c = $c.Trim(); if (Test-Path -LiteralPath $c -PathType Leaf) { return $c } }
  }
  $list = @($env:QFLOW_PYTHON, (Join-Path $QfHome 'python\python.exe'))
  if ($env:LOCALAPPDATA) {
    $base = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $base) {
      Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
        ForEach-Object { $list += (Join-Path $_.FullName 'python.exe') }
    }
  }
  foreach ($dir in ($env:Path -split ';')) {
    if ($dir -and $dir -notmatch 'WindowsApps') { $list += (Join-Path $dir 'python.exe'); $list += (Join-Path $dir 'python3.exe') }
  }
  foreach ($exe in $list) {
    if (Test-QfPython $exe) {
      try { New-Item -ItemType Directory -Force -Path $QfHome | Out-Null; [IO.File]::WriteAllText($QfPyCache, $exe) } catch {}
      return $exe
    }
  }
  try {
    $viaPy = (& py -3 -c 'import sys; print(sys.executable)' 2>$null)
    if ($LASTEXITCODE -eq 0 -and $viaPy -and (Test-QfPython $viaPy.Trim())) { return $viaPy.Trim() }
  } catch {}
  return $null
}
$QfPy = Find-QfPython
