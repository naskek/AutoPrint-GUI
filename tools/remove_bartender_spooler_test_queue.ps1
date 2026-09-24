[CmdletBinding()]
param(
    [string]$QueueName = 'AutoPrint_TEST_Printronix',
    [string]$PortName = 'AUTOPRINT_TEST_SINK',
    [int]$ReleaseTimeoutSeconds = 15,
    [switch]$AllowSpoolerRestart
)

$ErrorActionPreference = 'Stop'

if ($QueueName -notmatch 'TEST') {
    throw "Unsafe QueueName '$QueueName': name must contain TEST."
}
if ($PortName -notmatch 'AUTOPRINT_TEST') {
    throw "Unsafe PortName '$PortName': port must contain AUTOPRINT_TEST."
}

function Get-TestPortUsers {
    param([string]$Name)

    $printManagementUsers = @(
        Get-Printer -ErrorAction SilentlyContinue |
            Where-Object { $_.PortName -eq $Name }
    )

    $cimUsers = @(
        Get-CimInstance Win32_Printer -ErrorAction SilentlyContinue |
            Where-Object { $_.PortName -eq $Name }
    )

    return @($printManagementUsers + $cimUsers)
}

function Get-AllPrintJobsSafe {
    $allJobs = @()
    foreach ($printer in @(Get-Printer -ErrorAction SilentlyContinue)) {
        try {
            $jobs = @(Get-PrintJob -PrinterName $printer.Name -ErrorAction Stop)
            foreach ($job in $jobs) {
                $allJobs += [pscustomobject]@{
                    PrinterName = $printer.Name
                    JobId       = $job.ID
                    DocumentName = $job.DocumentName
                }
            }
        }
        catch {
            # A queue that cannot be queried is treated conservatively by returning
            # a synthetic blocker so the helper will not restart the spooler.
            $allJobs += [pscustomobject]@{
                PrinterName = $printer.Name
                JobId       = $null
                DocumentName = '<unable to query queue>'
            }
        }
    }
    return @($allJobs)
}

function Remove-TestPortWithRetry {
    param(
        [string]$Name,
        [int]$Attempts = 10,
        [int]$DelayMilliseconds = 750
    )

    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            Remove-PrinterPort -Name $Name -ErrorAction Stop
            return $true
        }
        catch {
            $lastError = $_

            $remainingUsers = @(Get-TestPortUsers -Name $Name)
            if ($remainingUsers.Count -gt 0) {
                $names = ($remainingUsers | Select-Object -ExpandProperty Name -Unique) -join ', '
                throw "Refusing port cleanup: $Name became used by: $names"
            }

            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }

    if ($lastError) {
        Write-Host "TEST port is still busy: $($lastError.Exception.Message)"
    }
    return $false
}

$queue = Get-Printer -Name $QueueName -ErrorAction SilentlyContinue
if ($queue) {
    if ($queue.PortName -ne $PortName) {
        throw "Refusing cleanup: queue '$QueueName' uses unexpected port '$($queue.PortName)'."
    }

    $jobs = @(Get-PrintJob -PrinterName $QueueName -ErrorAction SilentlyContinue)
    if ($jobs.Count -gt 0) {
        $jobs | Remove-PrintJob -ErrorAction Stop
        Write-Host "Deleted $($jobs.Count) test spool job(s)."
    }

    Remove-Printer -Name $QueueName
    Write-Host "Removed TEST queue: $QueueName"
}

# Queue deletion is asynchronous inside the Windows Print Spooler. Wait until
# both PrintManagement and Win32_Printer stop reporting users of the test port.
$deadline = (Get-Date).AddSeconds($ReleaseTimeoutSeconds)
do {
    $queueStillExists = Get-Printer -Name $QueueName -ErrorAction SilentlyContinue
    $remainingUsers = @(Get-TestPortUsers -Name $PortName)

    if (-not $queueStillExists -and $remainingUsers.Count -eq 0) {
        break
    }

    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if ($queueStillExists) {
    throw "TEST queue still exists after waiting for spooler release: $QueueName"
}

$remainingUsers = @(Get-TestPortUsers -Name $PortName)
if ($remainingUsers.Count -gt 0) {
    $names = ($remainingUsers | Select-Object -ExpandProperty Name -Unique) -join ', '
    throw "Refusing port cleanup: $PortName is still reported in use by: $names"
}

$port = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
if ($port) {
    $removed = Remove-TestPortWithRetry -Name $PortName

    if (-not $removed -and $AllowSpoolerRestart) {
        $allJobs = @(Get-AllPrintJobsSafe)
        if ($allJobs.Count -gt 0) {
            Write-Host "Refusing Print Spooler restart because print jobs/unknown queue states exist:"
            $allJobs | Format-Table PrinterName, JobId, DocumentName -AutoSize
            throw "Spooler restart blocked: active or unverified print queues exist."
        }

        Write-Host "No print jobs found. Restarting Windows Print Spooler to release TEST port handle..."
        Restart-Service -Name Spooler -Force -ErrorAction Stop

        $service = Get-Service -Name Spooler
        $service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(15)
        )
        Start-Sleep -Seconds 1

        $remainingUsers = @(Get-TestPortUsers -Name $PortName)
        if ($remainingUsers.Count -gt 0) {
            $names = ($remainingUsers | Select-Object -ExpandProperty Name -Unique) -join ', '
            throw "Refusing port cleanup after spooler restart: $PortName is used by: $names"
        }

        $removed = Remove-TestPortWithRetry -Name $PortName -Attempts 5
    }

    if (-not $removed) {
        throw (
            "TEST port is still busy. No production queue was modified. " +
            "Re-run later, reboot Windows, or explicitly rerun with -AllowSpoolerRestart " +
            "when no print jobs are active."
        )
    }

    Write-Host "Removed TEST port: $PortName"
}

if (Get-Printer -Name $QueueName -ErrorAction SilentlyContinue) {
    throw "TEST queue still exists after cleanup: $QueueName"
}
if (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue) {
    throw "TEST port still exists after cleanup: $PortName"
}

Write-Host "BarTender spooler TEST resources removed."
