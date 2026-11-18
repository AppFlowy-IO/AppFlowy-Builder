# Exercise the installed Flutter and WinSparkle windows in the hosted runner's own desktop.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Requires a disposable GitHub-hosted Windows desktop'
}
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
$desktop = [Windows.Automation.AutomationElement]::RootElement
$children = [Windows.Automation.TreeScope]::Children
$descendants = [Windows.Automation.TreeScope]::Descendants
$any = [Windows.Automation.Condition]::TrueCondition
$proof = (Resolve-Path evidence).Path
$target = $env:TARGET_TAG -replace '^v', ''
$browserNames = @('msedge', 'chrome', 'firefox', 'iexplore', 'brave', 'opera', 'vivaldi')
$old = $null

function Save-Screen([string]$name) {
    $bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
    $image = [Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [Drawing.Graphics]::FromImage($image)
    try {
        $graphics.CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
        $image.Save((Join-Path $proof "$name.png"))
    } finally { $graphics.Dispose(); $image.Dispose() }
}

function Get-Control($window, [string[]]$names) {
    # Use the deepest enabled match, since Flutter sometimes labels a parent group too.
    $controls = $window.FindAll($descendants, $any)
    for ($i = $controls.Count - 1; $i -ge 0; $i--) {
        $control = $controls[$i]
        $label = $control.Current.Name.Replace('&', '').Trim()
        if ($label -in $names -and $control.Current.IsEnabled -and !$control.Current.IsOffscreen) {
            return $control
        }
    }
}

function Press-Control($control) {
    $pattern = $null
    if ($control.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        $pattern.Invoke()
    } else {
        $control.SetFocus()
        [Windows.Forms.SendKeys]::SendWait('{SPACE}')
    }
}

function Wait-AppControl([string]$phase, [string[]]$names, [int]$seconds, [bool]$required = $true) {
    $until = (Get-Date).AddSeconds($seconds)
    do {
        foreach ($w in $desktop.FindAll($children, $any)) {
            if ($w.Current.ProcessId -ne $old.Id) { continue }
            $button = Get-Control $w $names
            if ($button) {
                Save-Screen $phase
                "Pressing $phase ($($button.Current.Name))"
                Press-Control $button
                return
            }
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $until)
    if ($required) { throw "AppFlowy did not show $phase ($names)" }
}

function Get-InstalledVersion {
    if (Test-Path -LiteralPath $env:APP_PATH) {
        return (Get-Item -LiteralPath $env:APP_PATH).VersionInfo.ProductVersion
    }
}

function Test-Target {
    $actual = Get-InstalledVersion
    return ($actual -eq $target -or $actual -eq "$target.0")
}

# WMI observes short-lived launches between UI polls. A browser-free baseline
# also prevents ShellExecute from opening a tab in an already-running browser.
if (Get-Process -Name $browserNames -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0) {
    throw 'Expected a browser-free desktop'
}
$query = "SELECT * FROM Win32_ProcessStartTrace WHERE " + (($browserNames | ForEach-Object { "ProcessName='$_.exe'" }) -join ' OR ')
Register-CimIndicationEvent -Query $query -SourceIdentifier BrowserStarted | Out-Null
try {
    $old = Start-Process -FilePath $env:APP_PATH -PassThru
    Wait-AppControl 'anonymous' @('Anonymous') 45 $false
    Wait-AppControl 'sidebar-update' @('Update') 90
    Wait-AppControl 'update-dialog' @('Update') 25
    Wait-AppControl 'winsparkle-install' @('Install Update', 'Install update...', 'Install and Relaunch', 'Install Update and Restart') 120
    # The release Inno installer may ask for normal setup consent. Only act on
    # its AppFlowy setup wizard, after the released app has invoked WinSparkle.
    $until = (Get-Date).AddMinutes(4)
    do {
        foreach ($w in $desktop.FindAll($children, $any)) {
            if ($w.Current.ProcessId -eq $old.Id) {
                # WinSparkle can ask for consent a second time after the download.
                $button = Get-Control $w @('Install update')
            } elseif ($w.Current.Name -match '^(Setup.*AppFlowy.*|AppFlowy.*Setup.*|Select Setup Language)$') {
                $button = Get-Control $w @('OK', 'Next >', 'Install', 'Finish', 'Yes')
            } else {
                continue
            }
            if ($button) { Press-Control $button }
        }
        Start-Sleep -Seconds 1
        $old.Refresh()
    } while (!((Test-Target) -and $old.HasExited) -and (Get-Date) -lt $until)
    if (!(Test-Target) -or !$old.HasExited) { throw 'The previous process did not exit and upgrade its installed executable' }

    # The shipped Inno setup has no [Run] entry. Start the installed target if needed.
    $new = Get-Process -Name AppFlowy -ErrorAction SilentlyContinue | Where-Object Path -eq $env:APP_PATH | Select-Object -First 1
    if (!$new) { $new = Start-Process -FilePath $env:APP_PATH -PassThru }
    Start-Sleep -Seconds 15
    $new.Refresh()
    if ($new.HasExited -or $new.Id -eq $old.Id) { throw 'Updated AppFlowy failed its startup check' }
    $events = @(Get-Event -SourceIdentifier BrowserStarted -ErrorAction SilentlyContinue)
    if ($events.Count -gt 0) { throw "A browser launched during the upgrade: $($events.SourceEventArgs.NewEvent.ProcessName)" }
    Save-Screen 'updated-app'
    @{ source = $env:SOURCE_TAG; target = (Get-InstalledVersion); oldPid = $old.Id; newPid = $new.Id; browserLaunches = $events.Count } |
        ConvertTo-Json | Set-Content "$proof/result.json"
} catch {
    Save-Screen 'last-screen'
    & { foreach ($w in $desktop.FindAll($children, $any)) {
        $w.Current.Name
        if ($old -and $w.Current.ProcessId -eq $old.Id) {
            $w.FindAll($descendants, $any) | ForEach-Object { $_.Current.Name } | Select-Object -First 120
        }
    } } | Out-String | Set-Content "$proof/windows.txt"
    throw
} finally {
    Unregister-Event -SourceIdentifier BrowserStarted
}
