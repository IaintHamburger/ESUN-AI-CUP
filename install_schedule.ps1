$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "$env:ComSpec" -Argument ('/d /c ""{0}\RUN_AUTO.cmd""' -f $root) -WorkingDirectory $root
# The PC's time zone must be Taipei. Daily07:30 gives80min before cutoff.
if ((Get-TimeZone).Id -notin @('Taipei Standard Time','Asia/Taipei')) {
  throw 'Set Windows time zone to Taipei before installing the schedule.'
}
$trigger = New-ScheduledTaskTrigger -Daily -At '07:30'
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'ESUN-Simple-Agent' -Action $action -Trigger $trigger -Settings $settings -Description 'Automatic market data, rule-based decisions, AI review, validation and verified submission' -Force
Write-Host 'Scheduled daily07:30 Taipei. Keep this PC powered on and connected.'
