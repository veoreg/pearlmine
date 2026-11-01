# Short kernel benchmarks with a cool-down between them. Local only; stops if the card stays hot.
# Output goes to files and leftover samplers of a crashed benchmark are cleaned, so a crash cannot hang the sweep.
param([string]$Bench,[string]$Runs,[int]$Seconds=8,[int]$CoolTo=55)
$env:PATH="E:\toolchains\cuda-13.4\bin\x64;$env:PATH"
$out=[IO.Path]::GetTempFileName();$err=[IO.Path]::GetTempFileName()
foreach ($run in ($Runs -split ';')) {
  $deadline=(Get-Date).AddSeconds(150)
  while ([int](nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader) -gt $CoolTo) {
    if ((Get-Date) -gt $deadline) { "card stayed above $CoolTo C, stopping sweep"; exit 1 }
    Start-Sleep -Seconds 3
  }
  $start=[int](nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader)
  $arguments=@('--seconds',$Seconds,'--abort-trials',1)+(-split $run)
  $proc=Start-Process -FilePath $Bench -ArgumentList $arguments -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
  $null=$proc.Handle
  if (-not $proc.WaitForExit(($Seconds+60)*1000)) { Stop-Process -Id $proc.Id -Force -Confirm:$false }
  Get-CimInstance Win32_Process -Filter "Name='nvidia-smi.exe'" | Where-Object { $_.CommandLine -match '-lms' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
  if ($proc.ExitCode -ne 0) { "[$run] FAILED: " + (Get-Content $err -Tail 1); continue }
  $o=Get-Content $out
  $rate=($o | Select-String 'credited T-MAC').Line -replace 'credited T-MAC/s: ',''
  $power=($o | Select-String 'GPU power').Line -replace ' / .*',''
  $clock=($o | Select-String 'SM clock').Line -replace '\(.*\),',''
  "[$run] from ${start}C | $rate | $power | $clock"
}
Remove-Item $out,$err -ErrorAction SilentlyContinue
