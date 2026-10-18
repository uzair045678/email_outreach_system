# uninstall_tasks.ps1 - remove the outreach scheduled tasks.

param(
    [string]$ProjectDir = "F:\_PROJECTS\EMails"
)

$ErrorActionPreference = "Continue"

$names = @(
    "Outreach-Research", "Outreach-Send", "Outreach-Bounces", "Outreach-Replies",
    "Outreach-Followups", "Outreach-Repair", "Outreach-Summary", "Outreach-Freehire"
)

foreach ($name in $names) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "removed: $name"
    } else {
        Write-Host "not present: $name"
    }
}
