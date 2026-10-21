# install_tasks.ps1 - register the outreach pipeline as Windows Scheduled Tasks.
#
# Run once, from an elevated PowerShell prompt:
#     powershell -ExecutionPolicy Bypass -File F:\_PROJECTS\EMails\install_tasks.ps1
#
# Tasks run as the current user and only while you are logged on (no stored
# password). The pipeline's own file locks prevent overlapping runs, so it is
# safe to trigger the frequent tasks more often than needed.

param(
    [string]$ProjectDir = "F:\_PROJECTS\EMails"
)

$ErrorActionPreference = "Stop"

$python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python venv not found at $python. Create it first (see README)."
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

function New-OutreachTask {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Description,
        [object]$Trigger
    )
    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument "-m outreach $Command" `
        -WorkingDirectory $ProjectDir
    Register-ScheduledTask `
        -TaskName "Outreach-$Name" `
        -Action $action `
        -Trigger $Trigger `
        -Settings $settings `
        -Description $Description `
        -Force | Out-Null
    Write-Host "registered: Outreach-$Name  ($Command)"
}

# Discovery + drafting at night, ready for next-day review. StartWhenAvailable
# runs a missed night task after the user next turns on and logs into Windows.
New-OutreachTask -Name "Research" -Command "research" `
    -Description "Prepare next-day company outreach drafts and prompt for approval." `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 10:30pm)

# Send loop: every 30 minutes, all day (send command self-limits to local slots).
# PS 5.1 cannot serialize TimeSpan.MaxValue into task XML, so repeat for ~10 years.
New-OutreachTask -Name "Send" -Command "send" `
    -Description "Send approved, due emails." `
    -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Days 3650))

# Read the mailbox for bounces and replies every hour.
New-OutreachTask -Name "Bounces" -Command "bounces" `
    -Description "Detect bounces and update rows." `
    -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Minutes 60) `
        -RepetitionDuration (New-TimeSpan -Days 3650))

New-OutreachTask -Name "Replies" -Command "replies" `
    -Description "Detect and classify replies." `
    -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(15) `
        -RepetitionInterval (New-TimeSpan -Minutes 60) `
        -RepetitionDuration (New-TimeSpan -Days 3650))

# freehire job watcher: hourly. Sends at most one digest email to you per
# digest_times slot (default 08/16/20/22); it never emails a company.
New-OutreachTask -Name "Freehire" -Command "freehire" `
    -Description "Poll freehire.me for new matching jobs; daily digest email to you." `
    -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(25) `
        -RepetitionInterval (New-TimeSpan -Minutes 60) `
        -RepetitionDuration (New-TimeSpan -Days 3650))

# Follow-ups and repair: twice a day is plenty.
New-OutreachTask -Name "Followups" -Command "followups" `
    -Description "Draft follow-ups for silent contacts." `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 7:30am)

New-OutreachTask -Name "Repair" -Command "repair" `
    -Description "Find replacement contacts for bounced rows." `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00am)

# Daily summary row.
New-OutreachTask -Name "Summary" -Command "summary" `
    -Description "Write today's summary row." `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00pm)

Write-Host ""
Write-Host "Done. Inspect with:  Get-ScheduledTask -TaskName 'Outreach-*'"
Write-Host "Remove with:         powershell -File F:\_PROJECTS\EMails\uninstall_tasks.ps1"
