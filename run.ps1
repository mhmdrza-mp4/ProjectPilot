# ===========================================================================
#  ProjectPilot launcher
#  Verifies and repairs the whole environment automatically, then waits for
#  one key press and opens the dashboard with nothing left to load.
#
#  Switches:
#    -NoAnim            Disable all animations
#    -NoPause           Never wait for a key (launch at once; errors close at once)
#    -Repair            Force a clean rebuild of .venv and reinstall everything
#    -AskBeforeInstall  Ask before Python is installed automatically
#    -Freeze            After a passing self-test, write constraints.txt (pinned versions)
#    -Check             Read-only report: what is ready and what a normal run would fix
#    -Diagnose          Collect logs + environment facts into one ZIP (no secrets) to share
#    -Offline           Never touch the network; install from .\wheelhouse
#    -Wheelhouse        Build .\wheelhouse (packages + Python installer) for offline PCs
#    -Update            git pull --ff-only first, then continue with the normal setup
#    -Uninstall         Remove .venv, logs and temp files (asks first, -Yes skips the question)
#    -Silent            No UI and no dashboard: plain log lines and an exit code (for CI)
#
#  Exit codes: 0 ok | 2 already running | 3 -Check: setup needed | 10 handled failure | 99 crash
#
#  Logs: %LOCALAPPDATA%\ProjectPilot\logs  (last 10 setup logs + last-crash.txt)
# ===========================================================================
param(
    [switch]$NoAnim,
    [switch]$NoPause,
    [switch]$Repair,
    [switch]$AskBeforeInstall,
    [switch]$Freeze,
    [switch]$Check,
    [switch]$Diagnose,
    [switch]$Offline,
    [switch]$Wheelhouse,
    [switch]$Update,
    [switch]$Uninstall,
    [switch]$Silent,
    [switch]$Yes
)

if ($Silent) { $NoAnim = $true; $NoPause = $true }


# Constrained Language Mode (WDAC / AppLocker) blocks the .NET calls this launcher needs
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
    Write-Host ""
    Write-Host ("  PowerShell is locked down on this PC (language mode: " + $ExecutionContext.SessionState.LanguageMode + ").")
    Write-Host "  The launcher cannot run here. Ask IT to allow run.ps1, or install by hand:"
    Write-Host "    python -m venv .venv"
    Write-Host "    .venv\Scripts\python -m pip install -e .[msp]"
    if (-not $NoPause) { [void](Read-Host "  Press Enter to close") }
    exit 10
}

Set-Location $PSScriptRoot
try { [Environment]::CurrentDirectory = $PSScriptRoot } catch { }
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Clock         = [System.Diagnostics.Stopwatch]::StartNew()
$VenvDir       = ".venv"
$VenvPy        = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Log           = Join-Path $env:TEMP ("projectpilot-setup-" + $PID + ".log")
try {
    Get-ChildItem $env:TEMP -Filter "projectpilot-*" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-2) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch { }
$ESC           = [char]27
$Inv           = [System.Globalization.CultureInfo]::InvariantCulture
$Rng           = New-Object System.Random
$PythonVersion = "3.12.8"
$RepoUrl       = "https://github.com/mhmdrza-mp4/ProjectPilot"   # used by -Update when the folder is not a git checkout
$PyMinMinor       = 12   # lowest supported Python 3 minor version
$PyTestedMaxMinor = 13   # highest minor version the project is tested with

# Logs live outside the project folder so nothing pollutes the repository
$AppDataRoot = $env:LOCALAPPDATA
if (-not $AppDataRoot) { $AppDataRoot = $env:TEMP }
$AppDataDir = Join-Path $AppDataRoot "ProjectPilot"
$LogDir     = Join-Path $AppDataDir "logs"
$SessionLog = Join-Path $LogDir ("setup-" + (Get-Date).ToString("yyyyMMdd-HHmmss", $Inv) + ".log")
$CrashLog   = Join-Path $LogDir "last-crash.txt"
try {
    if (-not (Test-Path $LogDir)) { [void](New-Item -ItemType Directory -Path $LogDir -Force) }
    Get-ChildItem $LogDir -Filter "setup-*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 9 |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch { }

function Write-SessionLog([string]$Text) {
    try {
        # never let proxy credentials (user:pass@host) reach the log
        $clean = $Text -replace '(?<=://)[^/@\s]+@', '***@'
        Add-Content -Path $SessionLog -Value $clean -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch { }
}

$OrigTitle = ""
try { $OrigTitle = $Host.UI.RawUI.WindowTitle } catch { }
function Set-Title([string]$text) {
    try { $Host.UI.RawUI.WindowTitle = $text } catch { }
}

# Project version (shown under the logo)
$script:AppVersion = ""
try {
    $ppFile = Join-Path $PSScriptRoot "pyproject.toml"
    if (Test-Path $ppFile) {
        $vm = Select-String -Path $ppFile -Pattern '^\s*version\s*=\s*["'']([^"'']+)["'']' -ErrorAction SilentlyContinue |
              Select-Object -First 1
        if ($vm) { $script:AppVersion = $vm.Matches[0].Groups[1].Value.Trim() }
    }
} catch { }

# ---------------------------------------------------------------------------
#  Enable Win32 Virtual Terminal Processing (idempotent across same session)
# ---------------------------------------------------------------------------
$script:UseVT = $false
# One Add-Type for everything native (a second compile would freeze the launcher for a second later on)
$Win32Defs = @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out int m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, int m);
[DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
[DllImport("advapi32.dll", SetLastError=true)] public static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
[DllImport("advapi32.dll", SetLastError=true)] public static extern bool GetTokenInformation(IntPtr token, int cls, out int info, int len, out int returned);
[DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
'@
try {
    if (-not ([System.Management.Automation.PSTypeName]'Win32.ConVT').Type) {
        Add-Type -Namespace Win32 -Name ConVT -MemberDefinition $Win32Defs
    }
    $hOut = [Win32.ConVT]::GetStdHandle(-11)
    $conMode = 0
    if ([Win32.ConVT]::GetConsoleMode($hOut, [ref]$conMode)) {
        $script:UseVT = [Win32.ConVT]::SetConsoleMode($hOut, ($conMode -bor 4))
    }
} catch { }
if ($env:NO_COLOR) { $script:UseVT = $false }
# report-style modes print plain lines
if ($Silent -or $Check -or $Diagnose -or $Wheelhouse -or $Uninstall) { $script:UseVT = $false }

# True-color when the console can do it, otherwise a 256-color fallback
$script:TrueColor = $false
if ($script:UseVT) {
    $osBuild = 0
    try { $osBuild = [Environment]::OSVersion.Version.Build } catch { }
    if ($env:WT_SESSION -or ($env:COLORTERM -match 'truecolor|24bit') -or ($env:ConEmuANSI -eq 'ON') -or
        $env:TERM_PROGRAM -or ($osBuild -ge 15063)) {
        $script:TrueColor = $true
    }
    if ($env:PROJECTPILOT_COLORS -eq '256') { $script:TrueColor = $false }
}

# Child processes: UTF-8 everywhere, non-interactive pip, true color for Textual
$env:PYTHONUTF8                    = '1'
$env:PYTHONIOENCODING              = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT                  = '1'
if ($script:TrueColor -and (-not $env:COLORTERM)) { $env:COLORTERM = 'truecolor' }
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$script:Anim   = ($script:UseVT -and (-not $NoAnim))
$script:SyncOn = ""
$script:SyncOff= ""
$script:EL     = ""
if ($script:UseVT) {
    $script:SyncOn  = "$ESC[?2026h"
    $script:SyncOff = "$ESC[?2026l"
    $script:EL      = "$ESC[K"
}

# ---------------------------------------------------------------------------
#  Glyphs (constructed from code points so the file stays pure 7-bit ASCII)
# ---------------------------------------------------------------------------
if ($script:UseVT) {
    $G_Solid    = [string][char]0x2588  # Full block
    $G_Shadow   = [string][char]0x2591  # Light shade
    $G_RibbonHi = [string][char]0x2501  # Heavy horizontal
    $G_RibbonLo = [string][char]0x2500  # Light horizontal
    $G_Diamond  = [string][char]0x25C6  # Black diamond
    $G_Sparkle  = [string][char]0x2726  # Four-pointed star
    $G_Dot      = [string][char]0x00B7  # Middle dot
    $G_Pending  = [string][char]0x25CB  # White circle
    $G_Ok       = [string][char]0x2713  # Check mark
    $G_Warn     = [string][char]0x25B2  # Up triangle
    $G_Fail     = [string][char]0x2717  # Ballot X
    $G_Arrow    = [string][char]0x25B8  # Small right triangle
    $G_TL       = [string][char]0x256D  # Rounded top-left
    $G_TR       = [string][char]0x256E  # Rounded top-right
    $G_BL       = [string][char]0x2570  # Rounded bottom-left
    $G_BR       = [string][char]0x256F  # Rounded bottom-right
    $G_HZ       = [string][char]0x2500  # Box horizontal
    $G_VT       = [string][char]0x2502  # Box vertical
    $G_Mid      = [string][char]0x2592  # Medium shade
    $G_HalfL    = [string][char]0x2578  # Heavy left half line
} else {
    $G_Solid    = '#'
    $G_Shadow   = '.'
    $G_RibbonHi = '='
    $G_RibbonLo = '-'
    $G_Diamond  = '*'
    $G_Sparkle  = '*'
    $G_Dot      = '.'
    $G_Pending  = 'o'
    $G_Ok       = '+'
    $G_Warn     = '!'
    $G_Fail     = 'x'
    $G_Arrow    = '>'
    $G_TL       = '+'
    $G_TR       = '+'
    $G_BL       = '+'
    $G_BR       = '+'
    $G_HZ       = '-'
    $G_VT       = '|'
    $G_Mid      = ':'
    $G_HalfL    = '-'
}

if ($env:WT_SESSION -and $script:UseVT) {
    # a single dot orbiting the cell (eight frames)
    $SpinFrames = @(0x2801,0x2802,0x2804,0x2840,0x2880,0x2820,0x2810,0x2808) |
                  ForEach-Object { [string][char]$_ }
    # equalizer bars, low to high
    $EqFrames = @(0x2581,0x2582,0x2583,0x2584,0x2585,0x2586,0x2587,0x2588) |
                ForEach-Object { [string][char]$_ }
} else {
    $SpinFrames = @('|','/','-','\')
    $EqFrames   = @('.',':','-','=','+','*','#','@')
}

# ---------------------------------------------------------------------------
#  Palette ("aurora": cyan -> indigo -> orchid) & precomputed gradient table
# ---------------------------------------------------------------------------
$C_G1      = @(34, 211, 238)    # #22D3EE  cyan
$C_G2      = @(129, 140, 248)   # #818CF8  indigo
$C_G3      = @(232, 121, 249)   # #E879F9  orchid
$C_Green   = @(52, 211, 153)    # #34D399
$C_Red     = @(248, 113, 113)   # #F87171
$C_Amber   = @(251, 191, 36)    # #FBBF24
$C_Muted   = @(148, 163, 184)   # #94A3B8
$C_Dim     = @(100, 116, 139)   # #64748B
$C_Dark    = @(51, 65, 85)      # #334155
$C_White   = @(248, 250, 252)   # #F8FAFC
$C_Bright  = @(255, 255, 255)   # Pure white flash

$script:Ansi256Cache = @{}
function Get-Ansi256([int]$r, [int]$g, [int]$b) {
    $key = ($r * 65536) + ($g * 256) + $b
    if ($script:Ansi256Cache.ContainsKey($key)) { return $script:Ansi256Cache[$key] }
    $hi = [Math]::Max($r, [Math]::Max($g, $b))
    $lo = [Math]::Min($r, [Math]::Min($g, $b))
    if (($hi - $lo) -lt 10) {
        $gray = [int][Math]::Round((($r + $g + $b) / 3.0 - 8.0) / 10.0)
        if ($gray -lt 0)  { $gray = 0 }
        if ($gray -gt 23) { $gray = 23 }
        $idx = 232 + $gray
    } else {
        $idx = 16 + (36 * [int][Math]::Round($r / 255.0 * 5.0)) +
                    (6  * [int][Math]::Round($g / 255.0 * 5.0)) +
                          [int][Math]::Round($b / 255.0 * 5.0)
    }
    $script:Ansi256Cache[$key] = $idx
    return $idx
}

# Foreground escape sequence for an RGB triple (true-color or 256-color)
function FgSeq($rgb) {
    if ($script:TrueColor) {
        return "$ESC[38;2;$($rgb[0]);$($rgb[1]);$($rgb[2])m"
    }
    return "$ESC[38;5;$(Get-Ansi256 ([int]$rgb[0]) ([int]$rgb[1]) ([int]$rgb[2]))m"
}

function Mix-Channel([double]$a, [double]$b, [double]$t) {
    $v = [int][Math]::Round($a + ($b - $a) * $t)
    if ($v -lt 0) { return 0 }
    if ($v -gt 255) { return 255 }
    return $v
}

function Mix-RGB($c1, $c2, [double]$t) {
    if ($t -le 0.0) { return $c1 }
    if ($t -ge 1.0) { return $c2 }
    return @(
        (Mix-Channel $c1[0] $c2[0] $t),
        (Mix-Channel $c1[1] $c2[1] $t),
        (Mix-Channel $c1[2] $c2[2] $t)
    )
}

$GradSteps = 256
$GradRGB   = New-Object 'object[]' $GradSteps
$GradANSI  = New-Object 'string[]' $GradSteps
for ($i = 0; $i -lt $GradSteps; $i++) {
    $pos = $i / [double]($GradSteps - 1)
    if ($pos -lt 0.5) {
        $rgb = Mix-RGB $C_G1 $C_G2 ($pos * 2.0)
    } else {
        $rgb = Mix-RGB $C_G2 $C_G3 (($pos - 0.5) * 2.0)
    }
    $GradRGB[$i]  = $rgb
    $GradANSI[$i] = FgSeq $rgb
}

function Get-GradRGB([double]$pos) {
    if ($pos -lt 0.0) { $pos = 0.0 }
    if ($pos -gt 1.0) { $pos = 1.0 }
    $idx = [int][Math]::Round($pos * ($GradSteps - 1))
    return $GradRGB[$idx]
}

function Get-GradANSI([double]$pos) {
    if (-not $script:UseVT) { return "" }
    if ($pos -lt 0.0) { $pos = 0.0 }
    if ($pos -gt 1.0) { $pos = 1.0 }
    $idx = [int][Math]::Round($pos * ($GradSteps - 1))
    return $GradANSI[$idx]
}

function Paint($rgb, [string]$text) {
    if (-not $script:UseVT) { return $text }
    return ((FgSeq $rgb) + $text + "$ESC[0m")
}

function Paint-Gradient([string]$text, [int]$offset = 0, [int]$total = 0) {
    if (-not $script:UseVT) { return $text }
    if ($total -le 0) { $total = $text.Length }
    $span = [Math]::Max($total - 1, 1)
    $sb = New-Object System.Text.StringBuilder
    for ($i = 0; $i -lt $text.Length; $i++) {
        $ch = $text[$i]
        if ($ch -eq ' ') {
            [void]$sb.Append(' ')
        } else {
            $pos = ($offset + $i) / [double]$span
            [void]$sb.Append((Get-GradANSI $pos)).Append($ch)
        }
    }
    [void]$sb.Append("$ESC[0m")
    return $sb.ToString()
}

# Gradient that travels along the text (triangle wave keeps it seamless)
function Paint-Wave([string]$text, [int]$tick, [double]$speed = 0.035) {
    if (-not $script:UseVT) { return $text }
    $sb = New-Object System.Text.StringBuilder
    $n = [Math]::Max($text.Length, 1)
    for ($i = 0; $i -lt $text.Length; $i++) {
        $ch = $text[$i]
        if ($ch -eq ' ') { [void]$sb.Append(' '); continue }
        $f = ((($i / [double]$n) * 0.8) + ($tick * $speed)) % 2.0
        if ($f -gt 1.0) { $f = 2.0 - $f }
        [void]$sb.Append((Get-GradANSI $f)).Append($ch)
    }
    [void]$sb.Append("$ESC[0m")
    return $sb.ToString()
}

# White text with a soft colored highlight sliding across it (running tasks)
function Paint-Shimmer([string]$text, [int]$tick) {
    if (-not $script:UseVT) { return $text }
    $sb = New-Object System.Text.StringBuilder
    $span = [Math]::Max($text.TrimEnd().Length, 1)
    $center = ($tick * 0.7) % ($span + 12) - 6.0
    $accent = Get-GradRGB 0.15
    for ($i = 0; $i -lt $text.Length; $i++) {
        $ch = $text[$i]
        if ($ch -eq ' ') { [void]$sb.Append(' '); continue }
        $rgb = $C_White
        $d = [Math]::Abs($i - $center)
        if ($d -lt 5.0) { $rgb = Mix-RGB $C_White $accent ((1.0 - ($d / 5.0)) * 0.9) }
        [void]$sb.Append((FgSeq $rgb)).Append($ch)
    }
    [void]$sb.Append("$ESC[0m")
    return $sb.ToString()
}

function Fmt-Sec([double]$s) {
    return ($s.ToString("0.0", $Inv) + "s")
}

function Fmt-Duration([double]$s) {
    $total = [int][Math]::Floor($s)
    if ($total -lt 60) { return ($total.ToString($Inv) + "s") }
    $h   = [int][Math]::Floor($total / 3600)
    $m   = [int][Math]::Floor(($total % 3600) / 60)
    $sec = $total % 60
    if ($h -gt 0) { return ($h.ToString($Inv) + "h " + $m.ToString($Inv) + "m") }
    return ($m.ToString($Inv) + "m " + $sec.ToString($Inv) + "s")
}

function Ease-OutCubic([double]$t) {
    if ($t -le 0.0) { return 0.0 }
    if ($t -ge 1.0) { return 1.0 }
    $inv = 1.0 - $t
    return (1.0 - ($inv * $inv * $inv))
}

function Set-Cursor([bool]$visible) {
    try {
        [Console]::CursorVisible = $visible
    } catch { }
    if ($script:UseVT) {
        if ($visible) { [Console]::Write("$ESC[?25h") }
        else          { [Console]::Write("$ESC[?25l") }
    }
}

# ---------------------------------------------------------------------------
#  Terminal Geometry & Viewport Clamping
# ---------------------------------------------------------------------------
$script:TermW = 120
$script:TermH = 30
$script:InitW = 120
$script:InitH = 30

function Update-TermSize([switch]$RecordInitial) {
    try {
        $ws = $Host.UI.RawUI.WindowSize
        if ($ws.Width -gt 20 -and $ws.Height -gt 5) {
            $script:TermW = $ws.Width
            $script:TermH = $ws.Height
            if ($RecordInitial) {
                $script:InitW = $ws.Width
                $script:InitH = $ws.Height
            }
        }
    } catch { }
}
# Classic console windows (conhost) can be resized from a script, so the dashboard logo always fits.
# Windows Terminal does not allow that: it keeps its own size (see the note shown at the end of setup).
$WantCols = 120
$WantRows = 36
function Set-ConsoleSize([int]$Cols, [int]$Rows) {
    if ($Silent -or $env:WT_SESSION) { return }
    try {
        $raw = $Host.UI.RawUI
        $max = $raw.MaxPhysicalWindowSize
        $c = [Math]::Min($Cols, $max.Width)
        $r = [Math]::Min($Rows, $max.Height)
        $win = $raw.WindowSize
        if (($win.Width -ge $c) -and ($win.Height -ge $r)) { return }
        $c = [Math]::Max($c, $win.Width)
        $r = [Math]::Max($r, $win.Height)
        $buf = $raw.BufferSize
        # the buffer must be at least as large as the window: grow it first, then the window
        $raw.BufferSize = New-Object System.Management.Automation.Host.Size ([Math]::Max($buf.Width, $c)), ([Math]::Max($buf.Height, $r))
        $raw.WindowSize = New-Object System.Management.Automation.Host.Size $c, $r
    } catch { }
}
Set-ConsoleSize $WantCols $WantRows

Update-TermSize -RecordInitial
$script:TermClock = [System.Diagnostics.Stopwatch]::StartNew()

function Get-BoardWidth {
    # asking the console for its size on every frame is slow: refresh at most ~3 times per second
    if ($script:TermClock.ElapsedMilliseconds -ge 300) {
        Update-TermSize
        $script:TermClock.Restart()
    }
    $maxW = $script:TermW - 4
    if ($maxW -lt 44) { return [Math]::Max($script:TermW - 2, 24) }
    if ($maxW -gt 76) { return 76 }
    return $maxW
}

function Fit-Text([string]$text, [int]$maxLen) {
    if ($maxLen -le 0) { return "" }
    if (-not $text) { return "" }
    if ($text.Length -le $maxLen) { return $text }
    if ($maxLen -le 3) { return $text.Substring(0, $maxLen) }
    return ($text.Substring(0, $maxLen - 3) + "...")
}

# ---------------------------------------------------------------------------
#  Logo Engine (5x5 pixel matrix + subtle 3D drop-shade & specular sweep)
# ---------------------------------------------------------------------------
$Font = @{
    'P' = @('####.', '#...#', '####.', '#....', '#....')
    'R' = @('####.', '#...#', '####.', '#..#.', '#...#')
    'O' = @('.###.', '#...#', '#...#', '#...#', '.###.')
    'J' = @('..###', '...#.', '...#.', '#..#.', '.##..')
    'E' = @('#####', '#....', '####.', '#....', '#####')
    'C' = @('.####', '#....', '#....', '#....', '.####')
    'T' = @('#####', '..#..', '..#..', '..#..', '..#..')
    'I' = @('#####', '..#..', '..#..', '..#..', '#####')
    'L' = @('#....', '#....', '#....', '#....', '#####')
}

function Build-WordMatrix([string]$word) {
    $rows = @('', '', '', '', '')
    for ($r = 0; $r -lt 5; $r++) {
        $parts = @()
        foreach ($ch in $word.ToCharArray()) {
            $parts += ($Font[[string]$ch][$r] -replace '\.', ' ')
        }
        $rows[$r] = $parts -join ' '
    }
    return $rows
}

function Build-LogoMatrix {
    $left  = Build-WordMatrix "PROJECT"
    $right = Build-WordMatrix "PILOT"
    $raw   = @()
    for ($r = 0; $r -lt 5; $r++) {
        $raw += ($left[$r] + '   ' + $right[$r])
    }
    $w = $raw[0].Length
    $out = @()
    for ($r = 0; $r -lt 5; $r++) {
        $chars = $raw[$r].ToCharArray()
        $rowChars = New-Object 'char[]' ($w + 1)
        for ($x = 0; $x -le $w; $x++) {
            $isSolid = ($x -lt $w -and $chars[$x] -eq '#')
            if ($isSolid) {
                $rowChars[$x] = [char]'#'
            } elseif ($x -gt 0 -and $chars[$x - 1] -eq '#') {
                $rowChars[$x] = [char]':'
            } else {
                $rowChars[$x] = [char]' '
            }
        }
        $out += (-join $rowChars)
    }
    return $out
}

$LogoRows  = Build-LogoMatrix
$LogoWidth = $LogoRows[0].Length

function Show-Subtitle {
    $text = "Decision support for Microsoft Project"
    if ($script:AppVersion) { $text += "  " + $G_Dot + "  v" + $script:AppVersion }
    if ($script:Anim) {
        # typewriter: muted text with a bright "cursor" on the last few letters
        $n = $text.Length
        for ($i = 4; $i -lt $n; $i += 4) {
            $head = $text.Substring(0, $i - 3)
            $tail = $text.Substring($i - 3, 3)
            [Console]::Write("`r  " + (Paint $C_Muted $head) + (Paint $C_White $tail) + $script:EL)
            Start-Sleep -Milliseconds 7
        }
        [Console]::Write("`r")
    }
    Write-Host ("  " + (Paint $C_Muted $text))
    Write-Host ""
}

function Show-Banner {
    Update-TermSize
    if ($script:UseVT) { Clear-Host }
    Write-Host ""

    $bw = Get-BoardWidth
    # Compact fallback when narrow (< 80 cols) or no VT support
    if ((-not $script:UseVT) -or $script:TermW -lt 80) {
        $compact = "P R O J E C T   P I L O T"
        $padLen  = [Math]::Max([int](($bw - $compact.Length - 8) / 2), 1)
        $wingL   = ($G_RibbonLo * $padLen) + " " + $G_Diamond + "  "
        $wingR   = "  " + $G_Diamond + " " + ($G_RibbonLo * $padLen)
        Write-Host ("  " + (Paint $C_Dim $wingL) + (Paint-Gradient $compact) + (Paint $C_Dim $wingR))
        Show-Subtitle
        return
    }

    $w = $LogoWidth
    $logoSeq = New-Object 'string[]' $w
    for ($lx = 0; $lx -lt $w; $lx++) { $logoSeq[$lx] = (Get-GradANSI ($lx / [double]($w - 1))) }

    function Render-LogoFrame([int]$revealX, [double]$shinePos, [bool]$withSparkles) {
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append($script:SyncOn)
        for ($r = 0; $r -lt 5; $r++) {
            $rowStr = $LogoRows[$r]
            $rowRev = $revealX - ($r * 2)   # each row trails the one above it
            [void]$sb.Append("  ")
            for ($x = 0; $x -lt $w; $x++) {
                $cell = $rowStr[$x]
                if ($x -ge $rowRev -or $cell -eq ' ') {
                    if ($withSparkles -and $x -lt $rowRev -and $Rng.Next(140) -eq 0) {
                        [void]$sb.Append((FgSeq $C_Muted) + $G_Dot)
                    } else {
                        [void]$sb.Append(' ')
                    }
                    continue
                }
                $baseRGB = Get-GradRGB ($x / [double]($w - 1))
                if ($withSparkles -and $cell -eq '#' -and $x -ge ($rowRev - 2)) {
                    # the freshly revealed edge shows up as a faint shaded blur first
                    [void]$sb.Append((FgSeq (Mix-RGB $baseRGB $C_Bright 0.55)) + $G_Mid)
                    continue
                }
                if ($cell -eq ':') {
                    # Subtle depth shadow
                    $sr = [int]($baseRGB[0] * 0.32)
                    $sg = [int]($baseRGB[1] * 0.32)
                    $sb2= [int]($baseRGB[2] * 0.32)
                    [void]$sb.Append((FgSeq @($sr, $sg, $sb2)) + $G_Shadow)
                    continue
                }
                # Diagonal specular light wave (cells outside the highlight come from the cache)
                $dist = [Math]::Abs(($x + $r * 2.2) - $shinePos)
                if ($dist -ge 7.5) {
                    [void]$sb.Append($logoSeq[$x]).Append($G_Solid)
                    continue
                }
                $glow = (1.0 - ($dist / 7.5)) * 0.88
                $cr = Mix-Channel $baseRGB[0] 255 $glow
                $cg = Mix-Channel $baseRGB[1] 255 $glow
                $cb = Mix-Channel $baseRGB[2] 255 $glow
                [void]$sb.Append((FgSeq @($cr, $cg, $cb)) + $G_Solid)
            }
            [void]$sb.Append("$ESC[0m$script:EL`n")
        }
        [void]$sb.Append($script:SyncOff)
        return $sb.ToString()
    }

    if ($script:Anim) {
        [Console]::Write((Render-LogoFrame 0 -1000.0 $false))
        $steps = 22
        for ($s = 1; $s -le $steps; $s++) {
            $eased = Ease-OutCubic ($s / [double]$steps)
            $rev   = [int][Math]::Round($eased * ($w + 10))
            $shine = ($eased * ($w + 24)) - 8.0
            [Console]::Write("$ESC[5A" + (Render-LogoFrame $rev $shine ($s -lt $steps)))
            Start-Sleep -Milliseconds 16
        }
        for ($sh = [int]($w * 0.45); $sh -le ($w + 18); $sh += 3) {
            [Console]::Write("$ESC[5A" + (Render-LogoFrame ($w + 10) $sh $true))
            Start-Sleep -Milliseconds 15
        }
        [Console]::Write("$ESC[5A")
    }
    [Console]::Write((Render-LogoFrame ($w + 10) -1000.0 $false))
    Show-Subtitle
}

# ---------------------------------------------------------------------------
#  In-Place Live Task Board State & Renderer
# ---------------------------------------------------------------------------
# States: 'pending' | 'running' | 'flash' | 'ok' | 'warn' | 'fail'
$TaskDefs = @(
    @{ Id = 'sys';  Label = 'System environment' }
    @{ Id = 'net';  Label = 'Network reachability' }
    @{ Id = 'py';   Label = 'Python 3.12+ runtime' }
    @{ Id = 'venv'; Label = 'Virtual environment' }
    @{ Id = 'deps'; Label = 'Workspace packages' }
    @{ Id = 'app';  Label = 'Dashboard engine' }
    @{ Id = 'msp';  Label = 'Microsoft Project link' }
    @{ Id = 'jev';  Label = 'TypeSafe JEV engine' }
)

$Tasks     = New-Object System.Collections.ArrayList
$TaskIndex = @{}
for ($i = 0; $i -lt $TaskDefs.Count; $i++) {
    $obj = [PSCustomObject]@{
        Id       = $TaskDefs[$i].Id
        Label    = $TaskDefs[$i].Label
        State    = 'pending'
        Detail   = ''
        SubNote  = ''
        Final    = ''
        Watch    = $null
        Reveal   = 1.0     # 0..1: how far the result text has faded in
        Idx      = $i      # position in the list (phase offset for the loaders)
        CacheKey = ''      # finished rows never change: they are rendered once and reused
        CacheRow = ''
    }
    [void]$Tasks.Add($obj)
    $TaskIndex[$obj.Id] = $obj
}

$script:BoardActive   = $false
$script:BoardLines    = $Tasks.Count + 2   # 1 ribbon + 1 spacer + N task rows
$script:ProgressFill  = 0.0                # Smooth current fill 0.0 .. 1.0
$script:ProgressGoal  = 0.0                # Target fill 0.0 .. 1.0
$script:AnimTick      = 0
$script:AnimClock     = [System.Diagnostics.Stopwatch]::StartNew()   # wall clock: animation speed never depends on frame cost
$script:FrameClock    = [System.Diagnostics.Stopwatch]::StartNew()
$script:FrameMs       = 33                                            # about 30 frames per second
$script:LastDrawT     = 0.0
$script:RibbonW       = -1
$script:RibbonFill    = $null                                         # cached "color + glyph" per filled cell
$script:RibbonEmpty   = ''
$script:BlendCache    = @{}                                           # (cell, highlight level) -> escape sequence
$script:ScanCache     = @{}
$script:EqSeq         = $null
$script:PulseCenter   = $null              # Set while the final ribbon pulse plays

function Recompute-Goal {
    $doneCount = 0.0
    foreach ($t in $Tasks) {
        if ($t.State -eq 'ok' -or $t.State -eq 'warn' -or $t.State -eq 'fail' -or $t.State -eq 'flash') {
            $doneCount += 1.0
        } elseif ($t.State -eq 'running') {
            # a running task creeps forward and slows down, so long steps never look frozen
            $el = 0.0
            if ($null -ne $t.Watch) { $el = $t.Watch.Elapsed.TotalSeconds }
            $doneCount += 0.30 + (0.60 * (1.0 - [Math]::Exp(-$el / 10.0)))
        }
    }
    $script:ProgressGoal = [Math]::Min(($doneCount / [double]$Tasks.Count), 1.0)
}

# Cells available for the bar itself (rest: margins, end diamonds, percent label)
function Get-RibbonInner([int]$width) { return [Math]::Max($width - 9, 16) }

function Build-RibbonCache([int]$innerW) {
    $script:RibbonW = $innerW
    $cells = New-Object 'string[]' $innerW
    for ($i = 0; $i -lt $innerW; $i++) {
        $pos = $i / [double]([Math]::Max($innerW - 1, 1))
        $cells[$i] = (Get-GradANSI $pos) + $G_RibbonHi
    }
    $script:RibbonFill  = $cells
    $script:RibbonEmpty = (FgSeq $C_Dark) + $G_RibbonLo
    $script:BlendCache  = @{}
    $script:ScanCache   = @{}
}

# A filled cell lit by a highlight of strength 0..1 (quantised to 24 levels, cached)
function Get-LitSeq([int]$i, [double]$s, [int]$innerW) {
    $lv = [int][Math]::Round($s * 24.0)
    if ($lv -le 0) { return $script:RibbonFill[$i] }
    if ($lv -gt 24) { $lv = 24 }
    $key = ($i * 32) + $lv
    $v = $script:BlendCache[$key]
    if ($null -eq $v) {
        $pos = $i / [double]([Math]::Max($innerW - 1, 1))
        $v = (FgSeq (Mix-RGB (Get-GradRGB $pos) $C_Bright ($lv / 24.0))) + $G_RibbonHi
        $script:BlendCache[$key] = $v
    }
    return $v
}

function Get-ScanSeq([double]$s) {
    $lv = [int][Math]::Round($s * 16.0)
    if ($lv -le 0) { return $script:RibbonEmpty }
    $v = $script:ScanCache[$lv]
    if ($null -eq $v) {
        $v = (FgSeq (Mix-RGB $C_Dark $C_Dim ($lv / 16.0))) + $G_RibbonLo
        $script:ScanCache[$lv] = $v
    }
    return $v
}

function Render-RibbonLine([int]$width, [double]$fill, [double]$tick) {
    $innerW = Get-RibbonInner $width
    if ($script:RibbonW -ne $innerW) { Build-RibbonCache $innerW }
    if ($fill -lt 0.0) { $fill = 0.0 }
    if ($fill -gt 1.0) { $fill = 1.0 }
    $exact  = $fill * $innerW
    $filled = [int][Math]::Floor($exact)                 # whole cells
    $half   = (($exact - $filled) -ge 0.5) -and ($filled -lt $innerW)   # half-cell precision
    $headCell = $filled
    if ($half) { $headCell = $filled + 1 }
    $animating     = ($fill -lt 0.995)
    $shimmerCenter = ($tick * 1.8) % ($innerW + 14) - 7.0
    $scanCenter    = ($tick * 0.9) % ($innerW + 20) - 10.0
    $pulse = $script:PulseCenter
    $hlCenter = $shimmerCenter
    $hlRadius = 4.5
    $hlPower  = 0.72
    if ($null -ne $pulse) { $hlCenter = [double]$pulse; $hlRadius = 9.0; $hlPower = 0.92 }
    $tailOn = (($null -eq $pulse) -and $animating)

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("  ")
    [void]$sb.Append((Paint (Get-GradRGB 0.0) $G_Diamond)).Append(" ")

    for ($i = 0; $i -lt $innerW; $i++) {
        if ($i -lt $filled) {
            # shimmer/pulse highlight and comet tail both blend towards white: combine them
            $s = 0.0
            $dHl = [Math]::Abs($i - $hlCenter)
            if ($dHl -lt $hlRadius) { $s = (1.0 - ($dHl / $hlRadius)) * $hlPower }
            if ($tailOn) {
                $dTail = $exact - $i
                if ($dTail -lt 7.0) {
                    $s2 = (1.0 - ($dTail / 7.0)) * 0.75
                    $s  = 1.0 - ((1.0 - $s) * (1.0 - $s2))
                }
            }
            if ($s -lt 0.02) {
                [void]$sb.Append($script:RibbonFill[$i])
            } else {
                [void]$sb.Append((Get-LitSeq $i $s $innerW))
            }
        } elseif ($i -eq $filled -and $half) {
            $pos = $i / [double]([Math]::Max($innerW - 1, 1))
            $rgb = Mix-RGB (Get-GradRGB $pos) $C_Bright 0.6
            [void]$sb.Append((FgSeq $rgb) + $G_HalfL)
        } elseif ($animating -and $i -eq $headCell -and $fill -gt 0.01) {
            [void]$sb.Append((Paint $C_Bright $G_Sparkle))
        } else {
            $d = [Math]::Abs($i - $scanCenter)
            if ($animating -and ($d -lt 3.5)) {
                # a faint scanner light drifts across the empty track
                [void]$sb.Append((Get-ScanSeq ((1.0 - ($d / 3.5)) * 0.8)))
            } else {
                [void]$sb.Append($script:RibbonEmpty)
            }
        }
    }
    $endColor = $C_Dark
    if ($fill -ge 0.99) { $endColor = Get-GradRGB 1.0 }
    $pct = [int][Math]::Round($fill * 100)
    $pctText = ($pct.ToString($Inv) + "%").PadLeft(4)
    $pctCol = $C_Muted
    if ($fill -ge 0.995) { $pctCol = $C_Green }
    [void]$sb.Append("$ESC[0m ").Append((Paint $endColor $G_Diamond)).Append(" ").Append((Paint $pctCol $pctText))
    return $sb.ToString()
}

# Equalizer-style loader: a row of bars that rise and fall out of phase (a different phase per step)
function Get-EqBars([int]$cells, [double]$tick, [int]$seed) {
    if (($null -eq $script:EqSeq) -or ($script:EqSeq.Count -ne $cells)) {
        $script:EqSeq = New-Object 'string[]' $cells
        for ($i = 0; $i -lt $cells; $i++) {
            $script:EqSeq[$i] = (Get-GradANSI ($i / [double]([Math]::Max($cells - 1, 1))))
        }
    }
    $sb = New-Object System.Text.StringBuilder
    $levels = $EqFrames.Count
    $t = $tick * 0.55
    for ($i = 0; $i -lt $cells; $i++) {
        $a = [Math]::Sin($t + ($i * 0.85) + $seed)
        $b = [Math]::Sin(($t * 0.37) - ($i * 1.7) + ($seed * 1.9))
        $h = ((($a * 0.65) + ($b * 0.35)) + 1.0) * 0.5
        $lv = [int][Math]::Floor($h * ($levels - 0.001))
        [void]$sb.Append($script:EqSeq[$i]).Append($EqFrames[$lv])
    }
    [void]$sb.Append("$ESC[0m")
    return $sb.ToString()
}

function Render-TaskRow($task, [int]$width, [double]$tick) {
    # Layout: "   G  label(25)  [equalizer(8)]  detail(right-aligned)  time(7)"
    $state = $task.State
    $cacheable = ((($state -eq 'pending') -or ($state -eq 'ok') -or ($state -eq 'warn') -or ($state -eq 'fail')) -and ($task.Reveal -ge 1.0))
    $cacheKey  = ''
    if ($cacheable) {
        $cacheKey = $state + '|' + $width + '|' + $task.Detail + '|' + $task.Final
        if ($task.CacheKey -eq $cacheKey) { return $task.CacheRow }
    }
    $glyph = $G_Pending
    $gCol  = $C_Dim
    $lCol  = $C_Muted
    $dCol  = $C_Muted
    $tCol  = $C_Dim

    switch ($state) {
        'pending' {
            $glyph = $G_Pending
            $gCol  = $C_Dim
            $lCol  = $C_Dim
            $dCol  = $C_Dim
            $tCol  = $C_Dim
        }
        'running' {
            $si    = ([int][Math]::Floor($tick * 0.6) + [int]$task.Idx) % $SpinFrames.Count
            $glyph = $SpinFrames[$si]
            $wave  = ([Math]::Sin(($tick * 0.25) + $task.Idx) + 1.0) * 0.5
            $gCol  = Get-GradRGB $wave
            $lCol  = $C_White
            $dCol  = $C_Muted
            $tCol  = $C_Muted
        }
        'flash' {
            $glyph = $G_Ok
            $gCol  = $C_Bright
            $lCol  = $C_Bright
            $dCol  = $C_Bright
            $tCol  = $C_Bright
        }
        'ok' {
            $glyph = $G_Ok
            $gCol  = $C_Green
            $lCol  = $C_White
            $dCol  = $C_Muted
            $tCol  = $C_Dim
        }
        'warn' {
            $glyph = $G_Warn
            $gCol  = $C_Amber
            $lCol  = $C_White
            $dCol  = $C_Amber
            $tCol  = $C_Dim
        }
        'fail' {
            $glyph = $G_Fail
            $gCol  = $C_Red
            $lCol  = $C_Red
            $dCol  = $C_Red
            $tCol  = $C_Red
        }
    }

    if (($state -eq 'ok' -or $state -eq 'warn') -and ($task.Reveal -lt 1.0)) {
        # result text fades in after the check mark lands
        $dCol = Mix-RGB $C_Dark $dCol $task.Reveal
        $tCol = Mix-RGB $C_Dark $tCol $task.Reveal
    }

    $detailText = $task.Detail
    $timeText   = $task.Final
    if ($state -eq 'running') {
        $detailText = $task.SubNote
        $timeText   = ""
        if ($null -ne $task.Watch) {
            $timeText = Fmt-Sec $task.Watch.Elapsed.TotalSeconds
        }
    }

    $timeW      = 7
    $labelMax   = 25
    $labelStr   = (Fit-Text $task.Label $labelMax).PadRight($labelMax)
    # 3 + 1 (glyph) + 2 + 25 (label) + 2 = 33 fixed cols before the detail field
    $availRight = [Math]::Max($width - 33, 20)
    $detailW    = $availRight - ($timeW + 2)
    $eqStr      = ''
    if (($state -eq 'running') -and ($detailW -ge 24)) {
        $eqStr   = Get-EqBars 8 $tick ([int]$task.Idx)
        $detailW = $detailW - 10
    }
    $detailStr  = (Fit-Text $detailText $detailW).PadLeft($detailW)
    $timeStr    = ([string]$timeText).PadLeft($timeW)

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("   ")
    [void]$sb.Append((Paint $gCol $glyph))
    [void]$sb.Append("  ")
    [void]$sb.Append((Paint $lCol $labelStr))
    [void]$sb.Append("  ")
    if ($eqStr) { [void]$sb.Append($eqStr).Append("  ") }

    if ($state -eq 'pending' -and [string]::IsNullOrEmpty($detailText)) {
        $dots = ($G_Dot * 3).PadLeft($detailW)
        [void]$sb.Append((Paint $C_Dark $dots))
    } else {
        [void]$sb.Append((Paint $dCol $detailStr))
    }
    [void]$sb.Append("  ")
    [void]$sb.Append((Paint $tCol $timeStr))
    $row = $sb.ToString()
    if ($cacheable) { $task.CacheKey = $cacheKey; $task.CacheRow = $row }
    return $row
}

function Draw-Board {
    if (-not $script:UseVT) { return }
    $bw = Get-BoardWidth
    Recompute-Goal

    # time-based easing: the bar glides at the same speed whether a frame took 5 ms or 50 ms
    $now = $script:AnimClock.Elapsed.TotalSeconds
    $dt  = $now - $script:LastDrawT
    if ($dt -gt 0.25) { $dt = 0.25 }
    if ($dt -lt 0.0)  { $dt = 0.0 }
    $script:LastDrawT = $now
    $diff = $script:ProgressGoal - $script:ProgressFill
    if ([Math]::Abs($diff) -lt 0.005) {
        $script:ProgressFill = $script:ProgressGoal
    } else {
        $script:ProgressFill += $diff * (1.0 - [Math]::Exp(-8.0 * $dt))
    }
    $tick = $now * 20.0

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append($script:SyncOn)
    if ($script:BoardActive) {
        [void]$sb.Append("$ESC[$($script:BoardLines)A`r")
    }
    [void]$sb.Append((Render-RibbonLine $bw $script:ProgressFill $tick)).Append("$script:EL`n")
    [void]$sb.Append("$script:EL`n")
    foreach ($t in $Tasks) {
        [void]$sb.Append((Render-TaskRow $t $bw $tick)).Append("$script:EL`n")
    }
    [void]$sb.Append($script:SyncOff)
    [Console]::Write($sb.ToString())
    $script:BoardActive = $true
}

# One animation frame at a steady pace. Every wait in the launcher goes through this, so the
# board keeps moving while the launcher waits for a process, a download or a network answer.
function Pump {
    if ($script:UseVT -and $script:BoardActive) {
        $script:FrameClock.Restart()
        Draw-Board
        $left = $script:FrameMs - [int]$script:FrameClock.ElapsedMilliseconds
        if ($left -gt 1) { [System.Threading.Thread]::Sleep($left) }
    } else {
        [System.Threading.Thread]::Sleep(80)
    }
}

# Runs a script block in a separate runspace so slow .NET work (DNS, proxy discovery, signature
# checks) never freezes the board. Returns the output array, or $null on timeout/error.
function Invoke-Async([string]$Script, [object[]]$ArgList = @(), [int]$TimeoutMs = 15000) {
    $ps = $null
    $ar = $null
    try {
        $ps = [powershell]::Create()
        [void]$ps.AddScript($Script)
        foreach ($a in $ArgList) { [void]$ps.AddArgument($a) }
        $ar = $ps.BeginInvoke()
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not $ar.IsCompleted) {
            if ($clock.ElapsedMilliseconds -gt $TimeoutMs) {
                try { [void]$ps.BeginStop($null, $null) } catch { }
                return $null
            }
            Pump
        }
        return ,@($ps.EndInvoke($ar))   # the comma keeps a one-item result an array
    } catch {
        return $null
    } finally {
        if ($ps -and $ar -and $ar.IsCompleted) { try { $ps.Dispose() } catch { } }
    }
}

# Runs a short external command without blocking the animation. Returns Code, Out, Err, TimedOut.
# Arguments that contain spaces must already carry their own quotes (Start-Process joins them as is).
function Invoke-Probe([string]$Exe, [string[]]$ProbeArgs = @(), [int]$TimeoutSec = 30) {
    $res = [PSCustomObject]@{ Code = 1; Out = ''; Err = ''; TimedOut = $false }
    $outF = Join-Path $env:TEMP ("projectpilot-" + $PID + "-probe.out")
    $errF = Join-Path $env:TEMP ("projectpilot-" + $PID + "-probe.err")
    try {
        $sp = @{
            FilePath               = $Exe
            NoNewWindow            = $true
            PassThru               = $true
            WorkingDirectory       = $PSScriptRoot
            RedirectStandardOutput = $outF
            RedirectStandardError  = $errF
        }
        if ($ProbeArgs -and $ProbeArgs.Count -gt 0) { $sp['ArgumentList'] = $ProbeArgs }
        $proc = Start-Process @sp
        $null = $proc.Handle   # keep the handle so the exit code stays readable
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not $proc.HasExited) {
            if ($clock.Elapsed.TotalSeconds -gt $TimeoutSec) {
                $res.TimedOut = $true
                try { & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null } catch { }
                try { $proc.Kill() } catch { }
                break
            }
            Pump
        }
        if ($res.TimedOut) { [void]$proc.WaitForExit(2000) } else { $proc.WaitForExit() }
        try { $res.Code = [int]$proc.ExitCode } catch { }
    } catch {
        $res.Code = 1
    }
    try { if (Test-Path $outF) { $res.Out = (("" + (Get-Content $outF -Raw -ErrorAction SilentlyContinue)).Trim()) } } catch { }
    try { if (Test-Path $errF) { $res.Err = (("" + (Get-Content $errF -Raw -ErrorAction SilentlyContinue)).Trim()) } } catch { }
    return $res
}

function Step-AnimFrames([int]$frames = 3, [int]$delayMs = 24) {
    if (-not $script:Anim) {
        $script:ProgressFill = $script:ProgressGoal
        Draw-Board
        return
    }
    for ($f = 0; $f -lt $frames; $f++) { Pump }
}

# One bright wave sweeping across the finished ribbon
function Play-RibbonPulse {
    if (-not $script:Anim) { return }
    $bw = Get-BoardWidth
    $innerW = Get-RibbonInner $bw
    $clk = [System.Diagnostics.Stopwatch]::StartNew()
    $dur = 520.0
    while ($clk.ElapsedMilliseconds -lt $dur) {
        $script:PulseCenter = -8.0 + (($innerW + 16) * ($clk.ElapsedMilliseconds / $dur))
        Pump
    }
    $script:PulseCenter = $null
    Draw-Board
}

function Start-Task([string]$id, [string]$subNote = "") {
    $t = $TaskIndex[$id]
    $t.State   = 'running'
    $t.SubNote = $subNote
    $t.Detail  = ''
    $t.Final   = ''
    if ($null -eq $t.Watch) {
        $t.Watch = [System.Diagnostics.Stopwatch]::StartNew()
    } else {
        $t.Watch.Start()   # resume (a task can pause while waiting for the user)
    }
    Set-Title ("ProjectPilot - " + $t.Label)
    if ($script:UseVT) { Draw-Board }
}

function Update-TaskNote([string]$id, [string]$subNote) {
    $t = $TaskIndex[$id]
    $t.SubNote = $subNote
    if ($subNote) { Set-Title ("ProjectPilot - " + $subNote) }
    if ($script:UseVT) { Draw-Board }
}

function Complete-Task([string]$id, [string]$state, [string]$detail) {
    $t = $TaskIndex[$id]
    if ($null -ne $t.Watch) {
        $t.Watch.Stop()
        $t.Final = Fmt-Sec $t.Watch.Elapsed.TotalSeconds
    }
    $t.SubNote = ''
    $t.Detail  = $detail

    if ($script:UseVT) {
        if ($script:Anim -and $state -eq 'ok') {
            $t.State = 'flash'
            Recompute-Goal
            Pump
        }
        $t.State = $state
        Recompute-Goal
        if ($script:Anim -and ($state -eq 'ok' -or $state -eq 'warn')) {
            # the result text fades in over ~130 ms (time-based, so slow frames never stretch it)
            $clk = [System.Diagnostics.Stopwatch]::StartNew()
            while ($clk.ElapsedMilliseconds -lt 130) {
                $t.Reveal = [Math]::Min($clk.ElapsedMilliseconds / 130.0, 1.0)
                Pump
            }
            $t.Reveal = 1.0
            Draw-Board
        } else {
            $t.Reveal = 1.0
            Step-AnimFrames -frames 2
        }
    } else {
        $t.State = $state
        $glyph = $G_Ok
        if ($state -eq 'warn') { $glyph = $G_Warn }
        elseif ($state -eq 'fail') { $glyph = $G_Fail }
        Write-Host ("  [" + $glyph + "] " + $t.Label.PadRight(26) + " " + $detail + "  " + $t.Final)
    }
}

# ---------------------------------------------------------------------------
#  Background Process Runner (animates board in-place while child runs)
# ---------------------------------------------------------------------------
# Copies the tail of the step logs into the persistent session log
function Save-StepLog([string]$TaskId, [string]$Note, [int]$Code) {
    $chunk = @("--- [" + $TaskId + "] " + $Note + " (exit " + $Code + ") ---")
    foreach ($f in @($Log, "$Log.err")) {
        if (Test-Path $f) {
            try {
                foreach ($ln in @(Get-Content $f -Tail 60 -ErrorAction SilentlyContinue)) { $chunk += ("  " + $ln) }
            } catch { }
        }
    }
    Write-SessionLog ($chunk -join "`n")
}

# Output (stdout) of the most recent background step
function Get-LastOutput {
    if (Test-Path $Log) {
        return ((@(Get-Content $Log -ErrorAction SilentlyContinue) -join "`n").Trim())
    }
    return ''
}

$script:LastTimedOut = $false

function Invoke-BgStep([string]$TaskId, [string]$Note, [string]$Exe, [string[]]$Arguments, [int]$TimeoutSec = 0) {
    Update-TaskNote $TaskId $Note
    if (-not $script:UseVT) {
        Write-Host ("      ... " + $Note)
    }
    $script:LastTimedOut = $false
    $clock = [System.Diagnostics.Stopwatch]::StartNew()

    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -NoNewWindow -PassThru `
         -WorkingDirectory $PSScriptRoot `
         -RedirectStandardOutput $Log -RedirectStandardError "$Log.err"
    # Touch .Handle immediately so ExitCode is populated reliably in PS 5.1
    $null = $p.Handle

    while (-not $p.HasExited) {
        if (($TimeoutSec -gt 0) -and ($clock.Elapsed.TotalSeconds -gt $TimeoutSec)) {
            # a hung child (for example a COM call blocked by a dialog) must never hang the launcher
            $script:LastTimedOut = $true
            try { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null } catch { }
            try { $p.Kill() } catch { }
            break
        }
        Pump
    }
    if ($script:LastTimedOut) { [void]$p.WaitForExit(3000) } else { $p.WaitForExit() }
    $code = 1
    try { $code = [int]$p.ExitCode } catch { }
    Save-StepLog $TaskId $Note $code
    return (($code -eq 0) -and (-not $script:LastTimedOut))
}

# Reads the tail of the last command log and turns known errors into a plain-language fix
function Get-FixHint([string]$Default) {
    $text = ""
    foreach ($f in @($Log, "$Log.err")) {
        if (Test-Path $f) {
            $text += " " + ((Get-Content $f -Tail 80 -ErrorAction SilentlyContinue) -join "`n")
        }
    }
    if ($text -match 'CERTIFICATE_VERIFY_FAILED|SSLError|SSL:') {
        return "Network security blocks pip (SSL). Turn off VPN/proxy or allow pypi.org, then run again."
    }
    if ($text -match 'ProxyError|Tunnel connection failed|407') {
        return "The proxy refused the connection. Check the proxy address and sign-in (HTTPS_PROXY), then run again."
    }
    if ($text -match 'ConnectionError|Max retries exceeded|getaddrinfo|Temporary failure|ReadTimeout|ProxyError|timed out') {
        return "pip could not reach PyPI. Check your internet, VPN or proxy, then run again."
    }
    if ($text -match 'No space left|WinError 112|not enough space') {
        return "The disk is full. Free some space, then run again."
    }
    if ($text -match 'Installer signature check failed') {
        return "The downloaded Python installer was not signed by the Python Software Foundation and was discarded. Install Python 3.12 from python.org manually."
    }
    if ($text -match 'WinError 5\b|WinError 32|Access is denied|PermissionError') {
        return "A file is locked or protected. Close editors and terminals using this folder, pause antivirus, then run again."
    }
    if ($text -match 'Microsoft Visual C\+\+|Failed building wheel|cl\.exe') {
        return "A package must be compiled. Install Microsoft C++ Build Tools or use 64-bit Python 3.12, then run again."
    }
    if (($text -match 'No matching distribution|Could not find a version') -and (Test-Wheelhouse) -and (($Offline) -or (-not $script:Online))) {
        return "The wheelhouse folder has no package for this Python version. On an online PC with the same Python (3.12 or 3.13, 64-bit) run: run.bat -Wheelhouse, then copy the folder here."
    }
    if ($text -match 'No matching distribution|Could not find a version') {
        return "A required package was not found. Check the dependency names and versions in pyproject.toml."
    }
    if ($text -match 'ModuleNotFoundError|ImportError') {
        return "A module failed to import. Run run.bat -Repair for a clean reinstall."
    }
    return $Default
}

function Show-LogPanel([string]$Title, [string]$FromFile = "") {
    $bw = Get-BoardWidth
    $inner = $bw - 4
    Write-Host ""
    Write-Host ("  " + (Paint $C_Red ($G_TL + ($G_HZ * ($bw - 2)) + $G_TR)))
    $hdr = Fit-Text ("Diagnostic Log: " + $Title) $inner
    Write-Host ("  " + (Paint $C_Red $G_VT) + " " + (Paint $C_White $hdr.PadRight($inner)) + " " + (Paint $C_Red $G_VT))
    Write-Host ("  " + (Paint $C_Red ($G_VT + ($G_HZ * ($bw - 2)) + $G_VT)))

    $lines = @()
    if ($FromFile) {
        if (Test-Path $FromFile) { $lines += @(Get-Content $FromFile -ErrorAction SilentlyContinue) }
    } else {
        if (Test-Path $Log)       { $lines += @(Get-Content $Log -ErrorAction SilentlyContinue) }
        if (Test-Path "$Log.err") { $lines += @(Get-Content "$Log.err" -ErrorAction SilentlyContinue) }
    }
    $tail = @($lines | Where-Object { $_ -and $_.Trim().Length -gt 0 } | Select-Object -Last 15)
    if ($tail.Count -eq 0) { $tail = @("(no output captured)") }

    foreach ($ln in $tail) {
        $clean = ($ln -replace "`t", "  ").TrimEnd()
        $clipped = (Fit-Text $clean $inner).PadRight($inner)
        Write-Host ("  " + (Paint $C_Red $G_VT) + " " + (Paint $C_Muted $clipped) + " " + (Paint $C_Red $G_VT))
    }
    Write-Host ("  " + (Paint $C_Red ($G_BL + ($G_HZ * ($bw - 2)) + $G_BR)))
}

function Stop-WithPause([string]$Message, [switch]$WithLog, [string]$LogTitle = "Command failed", [string]$Hint = "") {
    if ($script:UseVT -and $script:BoardActive) { Draw-Board }
    if ($WithLog) {
        Show-LogPanel $LogTitle
        $Hint = Get-FixHint $Hint
    }
    Set-Cursor $true
    Set-Title "ProjectPilot - Setup failed"
    Write-Host ""
    Write-Host ("  " + (Paint $C_Red ($G_Fail + "  " + $Message)))
    if ($Hint) {
        Write-Host ("  " + (Paint $C_Amber $G_Arrow) + "  " + (Paint $C_White ("Fix: " + $Hint)))
    }
    if (Test-Path $SessionLog) {
        Write-Host ("  " + (Paint $C_Dim ($G_Dot + "  Full log: " + $SessionLog)))
    }
    Write-Host ""
    if (-not $NoPause) { [void](Read-Host "  Press Enter to close") }
    exit 10
}

# ---------------------------------------------------------------------------
#  Interactive Prompt Helper (cleanly parks below the live board, then erases)
# ---------------------------------------------------------------------------
function Ask-YesNo([string]$Question) {
    Set-Cursor $true
    $promptStr = "  " + (Paint $C_Amber $G_Arrow) + "  " + (Paint $C_White $Question) + (Paint $C_Muted " [Y/n] ")
    [Console]::Write("`n" + $promptStr)
    $ans = Read-Host
    Set-Cursor $false
    if ($script:UseVT) {
        # Erase prompt & blank line, then return cursor to bottom of board
        [Console]::Write("$ESC[1A`r$script:EL$ESC[1A`r$script:EL")
    }
    return (-not ($ans -match '^\s*(n|no)\s*$'))
}

# ---------------------------------------------------------------------------
#  Summary Card & Launch Prompt
# ---------------------------------------------------------------------------
$SummaryRows = New-Object System.Collections.ArrayList
function Add-Summary([string]$Label, [string]$Value, [string]$Status) {
    [void]$SummaryRows.Add(@($Label, $Value, $Status))
}

$script:Notes = New-Object System.Collections.ArrayList
function Add-Note([string]$Text) {
    [void]$script:Notes.Add($Text)
    Write-SessionLog ("NOTE: " + $Text)
}

function Format-Wrapped([string]$Text, [int]$Width) {
    $out  = New-Object System.Collections.ArrayList
    $line = ''
    foreach ($word in ($Text -split '\s+')) {
        if ($word -eq '') { continue }
        if ($line.Length -eq 0) { $line = $word }
        elseif (($line.Length + 1 + $word.Length) -le $Width) { $line += ' ' + $word }
        else { [void]$out.Add($line); $line = $word }
    }
    if ($line.Length -gt 0) { [void]$out.Add($line) }
    return $out.ToArray()
}

function Show-Notes {
    if ($script:Notes.Count -eq 0) { return }
    $w = [Math]::Max((Get-BoardWidth) - 6, 24)
    Write-Host ""
    foreach ($n in $script:Notes) {
        $lines = @(Format-Wrapped $n $w)
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($i -eq 0) {
                Write-Host ("  " + (Paint $C_Amber $G_Warn) + "  " + (Paint $C_Muted $lines[$i]))
            } else {
                Write-Host ("     " + (Paint $C_Muted $lines[$i]))
            }
            if ($script:Anim) { Start-Sleep -Milliseconds 8 }
        }
    }
}

function Show-SummaryCard([string]$TotalTime) {
    $bw = Get-BoardWidth
    $inner = $bw - 2
    $badge = " Ready in " + $TotalTime + " "
    $leftH = 3
    $rightH = [Math]::Max($inner - $leftH - $badge.Length, 1)

    Write-Host ""
    $topLine = "  " + (Paint-Gradient ($G_TL + ($G_HZ * $leftH)) 0 $bw) +
               (Paint $C_White $badge) +
               (Paint-Gradient (($G_HZ * $rightH) + $G_TR) ($leftH + 1 + $badge.Length) $bw)
    Write-Host $topLine

    $labelW = 19
    $valW   = [Math]::Max($inner - $labelW - 6, 12)
    $edgeL  = Get-GradRGB 0.0
    $edgeR  = Get-GradRGB 1.0

    foreach ($row in $SummaryRows) {
        $lbl = (Fit-Text ([string]$row[0]) $labelW).PadRight($labelW)
        $val = (Fit-Text ([string]$row[1]) $valW).PadRight($valW)
        $st  = [string]$row[2]

        if ($st -eq 'ok')       { $ico = Paint $C_Green $G_Ok;   $vCol = $C_White }
        elseif ($st -eq 'warn') { $ico = Paint $C_Amber $G_Warn; $vCol = $C_Amber }
        else                    { $ico = Paint $C_Red   $G_Fail; $vCol = $C_Red   }

        $line = "  " + (Paint $edgeL $G_VT) + "  " +
                (Paint $C_Muted $lbl) + " " +
                (Paint $vCol $val) + " " +
                $ico + " " + (Paint $edgeR $G_VT)
        Write-Host $line
        if ($script:Anim) { Start-Sleep -Milliseconds 18 }
    }

    $botLine = "  " + (Paint-Gradient ($G_BL + ($G_HZ * $inner) + $G_BR) 0 $bw)
    Write-Host $botLine
}

# Waits for one key. Returns $true to open the dashboard, $false if Esc was pressed.
# Nothing is loaded after the key press: the dashboard is already pre-loaded.
function Wait-LaunchKey {
    if ($Silent) { return $false }
    if ($NoPause) { return $true }
    $label = "Press any key to open the dashboard"
    $sub   = "Esc to exit"
    $clearLen = [Math]::Min(78, [Math]::Max($script:TermW - 1, 20))
    try {
        # Drop keystrokes typed while setup was running
        while ([Console]::KeyAvailable) { [void][Console]::ReadKey($true) }
        $tick = 0
        while (-not [Console]::KeyAvailable) {
            $wave = ([Math]::Sin($tick * 0.2) + 1.0) * 0.5
            $line = "  " + (Paint (Get-GradRGB $wave) $G_Arrow) + "  " + (Paint-Wave $label $tick) +
                    (Paint $C_Dim ("   " + $G_Dot + "   " + $sub))
            [Console]::Write("`r" + $line)
            $tick++
            if ($script:Anim) { Start-Sleep -Milliseconds 50 } else { Start-Sleep -Milliseconds 120 }
        }
        $key = [Console]::ReadKey($true)
        [Console]::Write("`r" + (" " * $clearLen) + "`r")
        return ($key.Key -ne [ConsoleKey]::Escape)
    } catch {
        # Input is redirected or there is no real console: do not block
        [Console]::Write("`r" + (" " * $clearLen) + "`r")
        return $true
    }
}

# Short gradient bar that fills in right after the key press
function Play-LaunchSweep {
    if (-not $script:Anim) { return }
    $text = "Launching ProjectPilot"
    $barW = 26
    for ($f = 0; $f -le $barW; $f += 2) {
        $sb = New-Object System.Text.StringBuilder
        for ($i = 0; $i -lt $barW; $i++) {
            if ($i -lt $f) {
                [void]$sb.Append((FgSeq (Get-GradRGB ($i / [double]($barW - 1)))) + $G_RibbonHi)
            } else {
                [void]$sb.Append((FgSeq $C_Dark) + $G_RibbonLo)
            }
        }
        [void]$sb.Append("$ESC[0m")
        [Console]::Write("`r  " + (Paint (Get-GradRGB 0.2) $G_Arrow) + "  " + (Paint $C_White $text) + "  " + $sb.ToString() + $script:EL)
        Start-Sleep -Milliseconds 14
    }
    [Console]::Write("`r" + $script:EL)
}

# ---------------------------------------------------------------------------
#  Network, Python & Environment Helpers
# ---------------------------------------------------------------------------
$script:Online     = $null
$script:CleanTried = $false   # a from-scratch rebuild already happened in this run
$script:FailStage  = ''
$script:NetReason  = ''       # why the network check failed (shown to the user)
$script:PyNewer    = $false   # the chosen Python is newer than the tested range

# One HEAD request through the Windows proxy settings. 'ok' also when the server
# answered with an HTTP error: it was reachable. Otherwise the failure name.
$UrlStatusScript = @'
    param($Url, $TimeoutMs)
    try {
        $req = [System.Net.WebRequest]::Create($Url)
        $req.Method  = 'HEAD'
        $req.Timeout = $TimeoutMs
        try {
            $req.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
            $req.Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
        } catch { }
        $resp = $req.GetResponse()
        $resp.Close()
        'ok'
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) { 'ok' } else { [string]$_.Exception.Status }
    } catch {
        'error'
    }
'@

function Get-UrlStatus([string]$Url, [int]$TimeoutMs = 7000) {
    $r = Invoke-Async $UrlStatusScript @($Url, $TimeoutMs) ($TimeoutMs + 4000)
    if (($null -eq $r) -or ($r.Count -eq 0)) { return 'Timeout' }
    return ([string]$r[0])
}

# pip ignores the Windows (IE) proxy settings. If a system proxy is needed to reach
# PyPI and no proxy variable is set, hand it to pip through HTTPS_PROXY.
$ProxyScript = @'
    try {
        $px = [System.Net.WebRequest]::GetSystemWebProxy()
        $u  = New-Object System.Uri("https://pypi.org/simple/")
        if (-not $px.IsBypassed($u)) {
            $pu = $px.GetProxy($u)
            if ($pu -and ($pu.Host -ne $u.Host)) {
                $pu.AbsoluteUri.TrimEnd('/') + '|' + $pu.Authority
            }
        }
    } catch { }
'@

# pip ignores the Windows (IE) proxy settings. If a system proxy is needed to reach
# PyPI and no proxy variable is set, hand it to pip through HTTPS_PROXY.
# (PAC/WPAD discovery can take seconds, so it runs off the main thread)
function Set-PipProxyFromSystem {
    if ($env:HTTPS_PROXY) { return '' }
    $r = Invoke-Async $ProxyScript @() 12000
    if (($null -eq $r) -or ($r.Count -eq 0)) { return '' }
    $line = [string]$r[0]
    if ($line -notmatch '\|') { return '' }
    $parts = $line -split '\|', 2
    $env:HTTPS_PROXY = $parts[0]
    if (-not $env:HTTP_PROXY) { $env:HTTP_PROXY = $parts[0] }
    return $parts[1]
}

# Both hosts pip really needs: the index and the file host that serves the wheels
# A custom package index (corporate or regional mirror) set by environment or pip.ini
function Get-CustomIndexUrl {
    if ($env:PIP_INDEX_URL) { return $env:PIP_INDEX_URL.Trim() }
    $files = @()
    if ($env:PIP_CONFIG_FILE) { $files += $env:PIP_CONFIG_FILE }
    if ($env:APPDATA)         { $files += (Join-Path $env:APPDATA "pip\pip.ini") }
    if ($env:PROGRAMDATA)     { $files += (Join-Path $env:PROGRAMDATA "pip\pip.ini") }
    foreach ($f in $files) {
        if ($f -and (Test-Path $f)) {
            $m = Select-String -Path $f -Pattern '^\s*index-url\s*=\s*(\S+)' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
            if ($m) { return $m.Matches[0].Groups[1].Value.Trim() }
        }
    }
    return ''
}

function Test-Online {
    $custom = Get-CustomIndexUrl
    if ($custom -match '^https?://') {
        $root = ''
        $h    = ''
        try {
            $cu   = New-Object System.Uri($custom)
            # scheme + host + port only: credentials embedded in the URL never go into the probe
            $root = $cu.Scheme + '://' + $cu.Host
            if (-not $cu.IsDefaultPort) { $root += ':' + $cu.Port.ToString() }
            $root += '/'
            $h    = $cu.Host
        } catch { }
        if ($root) {
            $st = Get-UrlStatus $root
            if ($st -eq 'ok') { $script:NetReason = ''; return $true }
            if     ($st -match 'Trust|SecureChannel') { $script:NetReason = "SSL blocked ($h)" }
            elseif ($st -match 'NameResolution')      { $script:NetReason = "DNS failed ($h)" }
            elseif ($st -match 'Proxy')               { $script:NetReason = "Proxy problem" }
            else                                      { $script:NetReason = "Cannot reach $h" }
            return $false
        }
    }
    $bad     = Get-UrlStatus "https://pypi.org/simple/"
    $badHost = 'pypi.org'
    if ($bad -eq 'ok') {
        $bad     = Get-UrlStatus "https://files.pythonhosted.org/"
        $badHost = 'files.pythonhosted.org'
    }
    if ($bad -eq 'ok') { $script:NetReason = ''; return $true }

    if     ($bad -match 'Trust|SecureChannel') { $script:NetReason = "SSL blocked ($badHost)" }
    elseif ($bad -match 'NameResolution')      { $script:NetReason = "DNS failed ($badHost)" }
    elseif ($bad -match 'Proxy')               { $script:NetReason = "Proxy problem" }
    else                                       { $script:NetReason = "Cannot reach $badHost" }
    return $false
}

# A folder with downloaded packages (built by run.bat -Wheelhouse) makes offline installs possible
function Test-Wheelhouse {
    $d = Join-Path $PSScriptRoot "wheelhouse"
    if (-not (Test-Path $d)) { return $false }
    $n = @(Get-ChildItem $d -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(whl|tar\.gz|zip)$' }).Count
    return ($n -gt 0)
}

function Assert-Online([string]$TaskId, [switch]$AllowWheelhouse) {
    if ($null -eq $script:Online) { $script:Online = Test-Online }
    if ((-not $script:Online) -and $AllowWheelhouse -and (Test-Wheelhouse)) { return }
    if (-not $script:Online) {
        $why = ''
        if ($script:NetReason) { $why = " (" + $script:NetReason + ")" }
        $hint = "Connect to the internet (or turn off VPN/proxy) and run this again. For PCs without internet: run run.bat -Wheelhouse once on an online PC and copy the wheelhouse folder here."
        if ($script:NetReason -match 'SSL') {
            $hint = "A security product is intercepting HTTPS. Ask IT to trust pypi.org and files.pythonhosted.org, or switch off the VPN/proxy, then run again."
        } elseif ($script:NetReason -match 'Proxy') {
            $hint = "The proxy did not answer. Check the proxy address and sign-in, then run again."
        }
        Complete-Task $TaskId 'fail' 'Offline (download required)'
        Stop-WithPause ("No internet connection" + $why + ". Something has to be installed or repaired.") -Hint $hint
    }
}

# Facts about an interpreter: version, bitness, and whether venv/ensurepip exist
function Get-PyInfo([string]$exe) {
    if (-not $exe) { return $null }
    if (-not (Test-Path $exe)) { return $null }
    $code = '"import sys, struct, venv, ensurepip; print(''%d.%d|%d'' % (sys.version_info[0], sys.version_info[1], struct.calcsize(''P'') * 8))"'
    $r = Invoke-Probe $exe @('-c', $code) 20
    $m = [regex]::Match($r.Out, '^(\d+)\.(\d+)\|(\d+)$')
    if (-not $m.Success) { return $null }
    return [PSCustomObject]@{
        Exe   = $exe
        Major = [int]$m.Groups[1].Value
        Minor = [int]$m.Groups[2].Value
        Bits  = [int]$m.Groups[3].Value
    }
}

# Supported: CPython 3.12 or newer, 64-bit, with venv and ensurepip
function Test-PyInfoOk($info) {
    if ($null -eq $info) { return $false }
    return (($info.Major -eq 3) -and ($info.Minor -ge $PyMinMinor) -and ($info.Bits -eq 64))
}

function Test-Python([string]$exe) {
    return (Test-PyInfoOk (Get-PyInfo $exe))
}

# Prefers the tested range (3.12-3.13, newest first); a newer Python is a fallback
function Find-Python {
    $script:PyNewer = $false
    $candidates = @()

    $cmds = Get-Command python -All -ErrorAction SilentlyContinue
    foreach ($c in $cmds) {
        if ($c.Source -and ($c.Source -notlike '*\WindowsApps\*')) {
            $candidates += $c.Source
        }
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($sel in @('-3.13', '-3.12', '-3')) {
            $r = Invoke-Probe 'py' @($sel, '-c', '"import sys; print(sys.executable)"') 15
            if ($r.Out) {
                $eClean = (($r.Out -split "`n")[0]).Trim()
                if ($eClean -and ($eClean -notlike '*\WindowsApps\*')) { $candidates += $eClean }
            }
        }
    }

    $dirs = @((Join-Path $env:LOCALAPPDATA "Programs\Python"), $env:ProgramFiles)
    foreach ($d in $dirs) {
        if ($d -and (Test-Path $d)) {
            Get-ChildItem $d -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending |
                ForEach-Object { $candidates += (Join-Path $_.FullName "python.exe") }
        }
    }

    $infos = @()
    $seen  = @{}
    foreach ($c in $candidates) {
        $key = $c.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $i = Get-PyInfo $c
        if (Test-PyInfoOk $i) { $infos += $i }
    }
    if ($infos.Count -eq 0) { return $null }

    $tested = @($infos | Where-Object { $_.Minor -le $PyTestedMaxMinor } | Sort-Object Minor -Descending)
    if ($tested.Count -gt 0) { return $tested[0].Exe }

    $newer = @($infos | Sort-Object Minor)
    $script:PyNewer = $true
    return $newer[0].Exe
}

$SigScript = @'
    param($Path)
    try {
        $sig  = Get-AuthenticodeSignature -FilePath $Path
        $subj = ''
        if ($sig.SignerCertificate) { $subj = [string]$sig.SignerCertificate.Subject }
        ([string]$sig.Status) + '|' + $subj
    } catch { 'Error|' }
'@

function Install-Python {
    # an installer shipped in .\wheelhouse allows a completely offline setup
    $localInstaller = $null
    $wh = Join-Path $PSScriptRoot "wheelhouse"
    if (Test-Path $wh) {
        $localInstaller = Get-ChildItem $wh -Filter "python-3*-amd64.exe" -ErrorAction SilentlyContinue |
                          Sort-Object Name -Descending | Select-Object -First 1
    }
    if (-not $localInstaller) { Assert-Online 'py' }

    $installer  = ''
    $downloaded = $false
    if ($localInstaller) {
        $installer = $localInstaller.FullName
    } else {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $wingetArgs = @(
                "install", "--id", "Python.Python.3.12", "-e", "--silent",
                "--accept-package-agreements", "--accept-source-agreements"
            )
            if (Invoke-BgStep 'py' "Installing via winget" "winget" $wingetArgs 300) {
                if (Find-Python) { return $true }
            }
        }

        $installer = Join-Path $env:TEMP ("python-" + $PythonVersion + "-amd64.exe")
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

        $dlOk = Invoke-BgStep 'py' "Downloading Python $PythonVersion" "curl.exe" `
            @("-L", "-f", "-s", "--connect-timeout", "20", "--max-time", "600", "--retry", "3", "-o", ('"' + $installer + '"'), $url) 900
        if (-not $dlOk) { return $false }
        $downloaded = $true
    }

    # Never run an installer without checking who signed it
    Update-TaskNote 'py' "Verifying installer signature"
    $sigOk = $false
    $sigStatus = 'unknown'
    $sr = Invoke-Async $SigScript @($installer) 60000
    if (($null -ne $sr) -and ($sr.Count -gt 0)) {
        $sp = ([string]$sr[0]) -split '\|', 2
        $sigStatus = $sp[0]
        $sigOk = (($sp[0] -eq 'Valid') -and ($sp.Count -gt 1) -and ($sp[1] -match 'Python Software Foundation'))
    }
    if (-not $sigOk) {
        try { Set-Content -Path $Log -Value ("Installer signature check failed (status: " + $sigStatus + "). The file was rejected.") } catch { }
        if ($downloaded) { Remove-Item $installer -Force -ErrorAction SilentlyContinue }
        return $false
    }

    $ok = Invoke-BgStep 'py' "Installing Python $PythonVersion" $installer `
        @("/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=0", "Include_test=0") 900
    if ($downloaded) { Remove-Item $installer -Force -ErrorAction SilentlyContinue }
    return $ok
}

# ---------------------------------------------------------------------------
#  Host checks: single instance, terminal, folder location
# ---------------------------------------------------------------------------
$script:LauncherMutex = $null
$script:MutexHeld     = $false

# Two launchers working on the same .venv would corrupt it
function Enter-SingleInstance {
    $key = ($PSScriptRoot.ToLowerInvariant() -replace '[^a-z0-9]', '_')
    if ($key.Length -gt 120) { $key = $key.Substring($key.Length - 120) }
    try {
        $script:LauncherMutex = New-Object System.Threading.Mutex($false, ("Local\ProjectPilot_" + $key))
        try {
            $script:MutexHeld = $script:LauncherMutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $script:MutexHeld = $true   # the previous run was killed; we own it now
        }
    } catch {
        $script:MutexHeld = $true       # no mutex available: never block the user
    }
    return $script:MutexHeld
}

function Get-OsName {
    try {
        $k = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
        $name  = [string]$k.ProductName
        $build = 0
        [void][int]::TryParse([string]$k.CurrentBuildNumber, [ref]$build)
        if (($build -ge 22000) -and ($name -match 'Windows 10')) { $name = $name -replace 'Windows 10', 'Windows 11' }
        $disp = [string]$k.DisplayVersion
        if ($disp) { $name += " " + $disp }
        return $name.Trim()
    } catch {
        return ("Windows " + [Environment]::OSVersion.Version.ToString())
    }
}

function Get-TerminalKind {
    if ($env:WT_SESSION)   { return 'Windows Terminal' }
    if ($env:TERM_PROGRAM) { return [string]$env:TERM_PROGRAM }
    if ($env:ConEmuANSI)   { return 'ConEmu' }
    return 'Legacy console'
}

# Folder locations that are known to cause trouble with virtual environments
function Get-PathNotes {
    $notes = @()
    $p = $PSScriptRoot
    if ($p -match '[^\x00-\x7F]') {
        $notes += "The folder path contains non-English characters. If anything fails, move ProjectPilot to a plain path such as C:\Tools\ProjectPilot."
    }
    if ($p -match '\\(OneDrive|Dropbox|Google Drive)') {
        $notes += "The folder is inside a cloud-synced folder, which can lock files in .venv. A local path such as C:\Tools\ProjectPilot is safer."
    }
    $longOk = $false
    try {
        $v = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
        $longOk = ($v -eq 1)
    } catch { }
    if ((-not $longOk) -and ($p.Length -gt 110)) {
        $notes += ("The folder path is long (" + $p.Length.ToString($Inv) + " characters) and Windows long paths are off, so package installs can fail. Use a shorter path.")
    }
    return $notes
}

function Test-VenvPip {
    if (-not (Test-Path $VenvPy)) { return $false }
    $r = Invoke-Probe $VenvPy @('-m', 'pip', '--version') 40
    return (($r.Code -eq 0) -and (-not $r.TimedOut))
}

function Test-Imports([string]$Code = "import project_decision, textual, rich") {
    if (-not (Test-Path $VenvPy)) { return $false }
    $r = Invoke-Probe $VenvPy @('-c', ('"' + $Code + '"')) 60
    return (($r.Code -eq 0) -and (-not $r.TimedOut))
}

# Deletes .venv completely (stops stray Python processes that lock it first)
function Remove-VenvDir {
    if (-not (Test-Path $VenvDir)) { return $true }
    $root = Join-Path $PSScriptRoot $VenvDir
    try {
        foreach ($proc in @(Get-Process -Name python, pythonw -ErrorAction SilentlyContinue)) {
            $pth = $null
            try { $pth = $proc.Path } catch { }
            if ($pth -and $pth.StartsWith($root + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
                try { $proc.Kill() } catch { }
            }
        }
    } catch { }
    for ($i = 0; $i -lt 3; $i++) {
        # rmdir runs as a child process: much faster than Remove-Item on thousands of files,
        # and the board keeps animating while it works
        [void](Invoke-Probe 'cmd.exe' @('/c', ('rmdir /s /q "' + $root + '"')) 180)
        if (-not (Test-Path $VenvDir)) { return $true }
        $wait = [System.Diagnostics.Stopwatch]::StartNew()
        while ($wait.ElapsedMilliseconds -lt 500) { Pump }
    }
    try { Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue } catch { }
    return (-not (Test-Path $VenvDir))
}

function Test-LogMatch([string]$Pattern) {
    foreach ($f in @($Log, "$Log.err")) {
        if (Test-Path $f) {
            $t = ((Get-Content $f -Tail 80 -ErrorAction SilentlyContinue) -join "`n")
            if ($t -match $Pattern) { return $true }
        }
    }
    return $false
}

function Install-Deps([string]$TaskId, [string]$Note, [bool]$Clean = $false, [bool]$Relink = $false) {
    $base = @("-m", "pip", "install", "--disable-pip-version-check", "--retries", "5", "--timeout", "30")
    if ($Clean)  { $base += "--no-cache-dir" }
    if ((($Offline) -or (-not $script:Online)) -and (Test-Wheelhouse)) {
        # no internet (or -Offline): install from the local wheelhouse folder only
        $base += @("--no-index", "--find-links", "wheelhouse")
    }
    if ($Relink) {
        # only re-point the editable install at this folder; offline needs the local build backend
        $base += "--no-deps"
        if (-not $script:Online) { $base += "--no-build-isolation" }
    }
    $tail = @("-e", ('"' + ".[msp]" + '"'))

    # attempt 1 uses the pinned versions in constraints.txt (when present); attempt 2 drops them
    $attempts = @()
    if (Test-Path (Join-Path $PSScriptRoot "constraints.txt")) {
        $attempts += ,($base + @("-c", "constraints.txt") + $tail)
    }
    $attempts += ,($base + $tail)

    $ok    = $false
    $first = $true
    foreach ($argset in $attempts) {
        $label = $Note
        if (-not $first) { $label = "Retrying without pinned versions" }
        $ok = Invoke-BgStep $TaskId $label $VenvPy $argset
        if ((-not $ok) -and $script:Online -and (Test-LogMatch 'CERTIFICATE_VERIFY_FAILED|SSLError|SSL:')) {
            # TLS inspection: let pip trust the Windows certificate store
            $ts = @($argset[0..2]) + "--use-feature=truststore" + @($argset[3..($argset.Count - 1)])
            $ok = Invoke-BgStep $TaskId ($label + " (Windows trust store)") $VenvPy $ts
        }
        if ($ok) {
            if (-not $first) {
                Add-Note "The pinned versions in constraints.txt could not be installed on this PC, so the newest compatible versions were used instead."
            }
            break
        }
        $first = $false
    }
    return $ok
}

# ---- fingerprint of what pyproject.toml asks to be installed ---------------
# Only the parts that decide which packages are needed are hashed, so a version
# bump or a description edit does not force a reinstall (which fails offline).
function Get-TomlArrayText([string]$Text, [string]$Key) {
    $m = [regex]::Match($Text, '(?m)^\s*' + [regex]::Escape($Key) + '\s*=\s*\[')
    if (-not $m.Success) { return '' }
    $i = $m.Index + $m.Length
    $depth = 1
    $quote = [char]0
    $sb = New-Object System.Text.StringBuilder
    while (($i -lt $Text.Length) -and ($depth -gt 0)) {
        $c = $Text[$i]
        if ($quote -ne [char]0) {
            if (($c -eq [char]92) -and ($quote -eq [char]34)) {
                [void]$sb.Append($c)
                $i++
                if ($i -lt $Text.Length) { [void]$sb.Append($Text[$i]) }
            } elseif ($c -eq $quote) {
                $quote = [char]0
                [void]$sb.Append($c)
            } else {
                [void]$sb.Append($c)
            }
        } elseif (($c -eq [char]34) -or ($c -eq [char]39)) {
            $quote = $c
            [void]$sb.Append($c)
        } elseif ($c -eq [char]35) {
            while (($i -lt $Text.Length) -and ($Text[$i] -ne [char]10)) { $i++ }
            continue
        } elseif ($c -eq [char]91) {
            $depth++
            [void]$sb.Append($c)
        } elseif ($c -eq [char]93) {
            $depth--
            if ($depth -gt 0) { [void]$sb.Append($c) }
        } else {
            [void]$sb.Append($c)
        }
        $i++
    }
    return (($sb.ToString()) -replace '\s+', ' ').Trim()
}

function Get-TomlSectionText([string]$Text, [string]$Header) {
    $m = [regex]::Match($Text, '(?ms)^\[' + [regex]::Escape($Header) + '\][ \t]*$(.*?)(?=^\[|\z)')
    if (-not $m.Success) { return '' }
    $lines = @()
    foreach ($ln in ($m.Groups[1].Value -split "`n")) {
        $s = $ln.Trim()
        if (($s.Length -gt 0) -and (-not $s.StartsWith('#'))) { $lines += $s }
    }
    return (($lines -join ' ') -replace '\s+', ' ')
}

function Get-DepsFingerprint {
    $file = Join-Path $PSScriptRoot "pyproject.toml"
    $raw  = ([System.IO.File]::ReadAllText($file)) -replace "`r", ''
    $parts = @()
    $m = [regex]::Match($raw, '(?m)^\s*requires-python\s*=\s*(.+?)\s*$')
    if ($m.Success) { $parts += ('rp=' + $m.Groups[1].Value.Trim()) }
    $parts += ('d=' + (Get-TomlArrayText $raw 'dependencies'))
    $parts += ('o=' + (Get-TomlSectionText $raw 'project.optional-dependencies'))
    $parts += ('b=' + (Get-TomlArrayText $raw 'requires'))
    $joined = ($parts -join '|')
    if ((@($parts | Where-Object { $_ -notlike 'rp=*' }) -join '|') -eq 'd=|o=|b=') {
        # nothing recognizable: fall back to the whole file (always safe)
        $joined = $raw
    }
    $cf = Join-Path $PSScriptRoot "constraints.txt"
    if (Test-Path $cf) {
        $craw   = ([System.IO.File]::ReadAllText($cf)) -replace "`r", ''
        $joined = $joined + '|c=' + $craw
    }
    $sha   = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
    return ('v2:' + (($bytes | ForEach-Object { $_.ToString('x2') }) -join ''))
}

function Save-DepsHash {
    try {
        Set-Content -Path (Join-Path $VenvDir ".pp-deps-hash") -Value (Get-DepsFingerprint) -Encoding ASCII
        Set-Content -Path (Join-Path $VenvDir ".pp-root") -Value $PSScriptRoot -Encoding UTF8
    } catch { }
}

# Folder the editable install was made for (empty when unknown)
function Get-StoredRoot {
    $f = Join-Path $VenvDir ".pp-root"
    if (Test-Path $f) {
        try { return (("" + (Get-Content $f -Encoding UTF8 -ErrorAction Stop)).Trim()) } catch { }
    }
    return ''
}

# First problem line reported by "pip check" (empty when the environment is consistent)
function Get-PipCheckProblem {
    if (-not (Test-Path $VenvPy)) { return '' }
    $r = Invoke-Probe $VenvPy @('-m', 'pip', 'check') 90
    if (($r.Code -eq 0) -or $r.TimedOut) { return '' }
    $txt = $r.Out + "`n" + $r.Err
    $first = @($txt -split "`n" | Where-Object { $_.Trim().Length -gt 0 }) | Select-Object -First 1
    return [string]$first
}

# ---- Microsoft Project: is it running elevated differently from this window? --
function Test-SelfElevated {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

$script:ElevApiReady = $false
function Initialize-ElevationApi {
    if ($script:ElevApiReady) { return $true }
    try {
        if (-not ([System.Management.Automation.PSTypeName]'Win32.ConVT').Type) {
            Add-Type -Namespace Win32 -Name ConVT -MemberDefinition $Win32Defs -ErrorAction Stop
        }
    } catch { }
    if (([System.Management.Automation.PSTypeName]'Win32.ConVT').Type) { $script:ElevApiReady = $true }
    return $script:ElevApiReady
}

# 'elevated' | 'normal' | 'denied' (cannot even be opened, usually a higher privilege) | 'unknown'
function Get-ProcElevation([int]$ProcId) {
    if (-not (Initialize-ElevationApi)) { return 'unknown' }
    $h   = [IntPtr]::Zero
    $tok = [IntPtr]::Zero
    try {
        $h = [Win32.ConVT]::OpenProcess([uint32]4096, $false, [uint32]$ProcId)     # QUERY_LIMITED_INFORMATION
        if ($h -eq [IntPtr]::Zero) { return 'denied' }
        if (-not [Win32.ConVT]::OpenProcessToken($h, [uint32]8, [ref]$tok)) {      # TOKEN_QUERY
            if ([System.Runtime.InteropServices.Marshal]::GetLastWin32Error() -eq 5) { return 'denied' }
            return 'unknown'
        }
        $info = 0
        $ret  = 0
        if (-not [Win32.ConVT]::GetTokenInformation($tok, 20, [ref]$info, 4, [ref]$ret)) { return 'unknown' }   # TokenElevation
        if ($info -ne 0) { return 'elevated' }
        return 'normal'
    } catch {
        return 'unknown'
    } finally {
        if ($tok -ne [IntPtr]::Zero) { [void][Win32.ConVT]::CloseHandle($tok) }
        if ($h -ne [IntPtr]::Zero)   { [void][Win32.ConVT]::CloseHandle($h) }
    }
}

# ---- small Python programs the launcher runs inside .venv ------------------
function Write-ProbeFile([string]$Name, [string]$Content) {
    $path = Join-Path $env:TEMP ("projectpilot-" + $PID + "-" + $Name + ".py")
    [System.IO.File]::WriteAllText($path, $Content, (New-Object System.Text.UTF8Encoding($false)))
    return $path
}

# Starts the real dashboard headlessly (no terminal needed) at two sizes and cycles
# every theme, so a broken stylesheet or widget is caught before the user sees it.
$SmokePyCode = @'
import asyncio
import sys

try:
    from project_decision.tui.app import DecisionTUI
except ImportError:
    from project_decision.tui import DecisionTUI


async def run(size, themes, keys):
    app = DecisionTUI()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for name in themes:
            app.theme = name
            await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()


async def main():
    await run((150, 44), ("cockpit", "glacier", "daylight"), ("question_mark", "escape"))
    await run((100, 28), ("cockpit",), ())


asyncio.run(main())
print("SMOKE_OK")
sys.exit(0)
'@

# Read-only look at the running Microsoft Project through COM. Prints one line:
# NOPYWIN32 | ERR:<hresult> | OK|<number of open projects>
$ComPyCode = @'
import sys

try:
    import pythoncom
    import win32com.client
except Exception:
    print("NOPYWIN32")
    sys.exit(0)

try:
    pythoncom.CoInitialize()
    app = win32com.client.GetActiveObject("MSProject.Application")
except Exception as exc:
    args = getattr(exc, "args", None)
    print("ERR:%s" % (args[0] if args else "unknown"))
    sys.exit(0)

try:
    count = int(app.Projects.Count)
except Exception as exc:
    args = getattr(exc, "args", None)
    print("ERR:%s" % (args[0] if args else "unknown"))
    sys.exit(0)

print("OK|%d" % count)
'@

# Reads the TypeSafe settings with the application's own loader (one source of truth).
# The key is never printed; the API host is only tested for a TCP connection.
$JevPyCode = @'
import socket
import sys
from urllib.parse import urlparse

try:
    from project_decision.config import load_typesafe_settings
    settings = load_typesafe_settings()
except Exception:
    print("ERR")
    sys.exit(0)

has_key = "1" if getattr(settings, "api_key", None) is not None else "0"
host = ""
for name in dir(settings):
    if name.startswith("_"):
        continue
    lowered = name.lower()
    if "url" in lowered or "endpoint" in lowered or "host" in lowered:
        value = getattr(settings, name, None)
        if isinstance(value, str) and value.startswith("http"):
            host = urlparse(value).hostname or ""
            break

reach = "NA"
if host:
    try:
        socket.create_connection((host, 443), timeout=4).close()
        reach = "UP"
    except Exception:
        reach = "DOWN"

print("KEY=%s|NET=%s" % (has_key, reach))
'@

# Runs the dashboard exactly like "python -m project_decision.tui", but writes a
# traceback to a file when it dies, so a crash is never lost with the window.
$RunnerPyCode = @'
import os
import runpy
import traceback

crash = os.environ.get("PP_CRASH_LOG", "")
try:
    runpy.run_module("project_decision.tui", run_name="__main__", alter_sys=True)
except (SystemExit, KeyboardInterrupt):
    raise
except BaseException:
    if crash:
        try:
            with open(crash, "w", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
        except Exception:
            pass
    raise
'@

# Wipes .venv and rebuilds it from scratch, installing without any pip cache
function Rebuild-Environment([string]$TaskId, [string]$PyExe) {
    $script:FailStage  = ''
    $script:CleanTried = $true
    Assert-Online $TaskId -AllowWheelhouse   # never delete a working setup that cannot be re-created
    Update-TaskNote $TaskId "Removing old $VenvDir"
    if (-not (Remove-VenvDir)) { $script:FailStage = 'remove'; return $false }
    if (-not (Invoke-BgStep $TaskId "Creating clean $VenvDir" $PyExe @("-m", "venv", $VenvDir))) {
        $script:FailStage = 'venv'
        return $false
    }
    if (-not (Install-Deps $TaskId "Clean install (no cache)" $true)) {
        $script:FailStage = 'pip'
        return $false
    }
    Save-DepsHash
    return $true
}

function Stop-RebuildFailed([string]$TaskId) {
    if ($script:FailStage -eq 'remove') {
        Complete-Task $TaskId 'fail' 'Cannot remove .venv'
        Stop-WithPause "Could not delete the old virtual environment." -Hint "Close editors, terminals and Python programs that use the .venv folder, then run again."
    } elseif ($script:FailStage -eq 'venv') {
        Complete-Task $TaskId 'fail' 'venv creation failed'
        Stop-WithPause "Could not create a new virtual environment." -WithLog -LogTitle "python -m venv" -Hint "Reinstall Python 3.12+ from python.org, then run again."
    } else {
        Complete-Task $TaskId 'fail' 'pip install failed'
        Stop-WithPause "Package installation failed, even after a clean reinstall." -WithLog -LogTitle "pip install -e .[msp]" -Hint "Check your internet connection, then run again."
    }
}

# ===========================================================================
#  Extra modes: -Check, -Diagnose, -Wheelhouse, -Update, -Uninstall
#  (plain text, no board; every function returns an exit code or a small result)
# ===========================================================================
function Protect-Text([string]$Text) {
    if (-not $Text) { return '' }
    $t = $Text -replace '(?<=://)[^/@\s]+@', '***@'
    if ($env:USERPROFILE) { $t = $t.Replace($env:USERPROFILE, '%USERPROFILE%') }
    return $t
}

function Write-Check([string]$State, [string]$Label, [string]$Detail) {
    $tag = '[ -- ]'
    if     ($State -eq 'ok')   { $tag = '[ OK ]' }
    elseif ($State -eq 'warn') { $tag = '[WARN]' }
    elseif ($State -eq 'fail') { $tag = '[FAIL]' }
    Write-Host ("  " + $tag + " " + $Label.PadRight(18) + " " + $Detail)
}

# Read-only report. Exit code: 0 ready, 3 a normal run would install/repair something, 10 blocked.
function Invoke-CheckMode {
    Write-Host ""
    Write-Host "  ProjectPilot check (read-only: nothing is installed or changed)"
    Write-Host ""
    $todo = New-Object System.Collections.ArrayList

    Write-Check 'ok' 'Windows' (Get-OsName)
    if (-not [Environment]::Is64BitOperatingSystem) {
        Write-Check 'fail' 'Architecture' '32-bit Windows is not supported'
        [void]$todo.Add('Use 64-bit Windows (the launcher cannot fix this)')
    }
    if (-not (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
        Write-Check 'fail' 'Project files' 'pyproject.toml is missing next to run.bat'
        return 10
    }
    Write-Check 'ok' 'Project files' 'pyproject.toml found'
    foreach ($pn in @(Get-PathNotes)) { Write-Check 'warn' 'Folder path' $pn }

    if ($Offline) {
        $script:Online = $false
        Write-Check 'info' 'Network' 'offline mode requested'
    } else {
        [void](Set-PipProxyFromSystem)
        $script:Online = Test-Online
        if ($script:Online) { Write-Check 'ok' 'Network' 'package index reachable' }
        else                { Write-Check 'warn' 'Network' $script:NetReason }
    }
    if (Test-Wheelhouse) { Write-Check 'ok' 'Wheelhouse' 'present: offline installs are possible' }
    else                 { Write-Check 'info' 'Wheelhouse' 'not present' }
    if (Test-Path (Join-Path $PSScriptRoot "constraints.txt")) { Write-Check 'ok' 'Pinned versions' 'constraints.txt present' }
    else { Write-Check 'info' 'Pinned versions' 'no constraints.txt (create it with: run.bat -Freeze)' }

    $py = Find-Python
    if ($py) {
        $pi = Get-PyInfo $py
        Write-Check 'ok' 'Python' ("" + $pi.Major + "." + $pi.Minor + " (64-bit)  " + (Protect-Text $py))
    } else {
        Write-Check 'warn' 'Python' 'Python 3.12+ (64-bit) not found'
        $hasLocalInstaller = $false
        $whDir = Join-Path $PSScriptRoot "wheelhouse"
        if (Test-Path $whDir) { $hasLocalInstaller = (@(Get-ChildItem $whDir -Filter "python-3*-amd64.exe" -ErrorAction SilentlyContinue).Count -gt 0) }
        if ($script:Online -or $hasLocalInstaller) { [void]$todo.Add('Install Python 3.12 automatically') }
        else { [void]$todo.Add('Install Python 3.12 (no internet and no installer in wheelhouse: blocked)') }
    }

    $venvOk = $false
    if (-not (Test-Path $VenvPy)) {
        Write-Check 'warn' 'Virtual env' '.venv is missing'
        [void]$todo.Add('Create .venv and install the packages')
    } elseif (-not (Test-Python $VenvPy)) {
        Write-Check 'warn' 'Virtual env' '.venv is broken or uses an unsupported Python'
        [void]$todo.Add('Rebuild .venv')
    } elseif (-not (Test-VenvPip)) {
        Write-Check 'warn' 'Virtual env' 'pip does not work inside .venv'
        [void]$todo.Add('Rebuild .venv')
    } else {
        $venvOk = $true
        $vi = Get-PyInfo $VenvPy
        Write-Check 'ok' 'Virtual env' ("Python " + $vi.Major + "." + $vi.Minor)
    }

    $importsOk = $false
    if ($venvOk) {
        $importsOk = Test-Imports
        $want = ''
        try { $want = Get-DepsFingerprint } catch { }
        $have = ''
        $hf = Join-Path $VenvDir ".pp-deps-hash"
        if (Test-Path $hf) { try { $have = (("" + (Get-Content $hf -ErrorAction Stop)).Trim()) } catch { } }
        $stored = Get-StoredRoot
        $moved  = (($stored -ne '') -and ($stored -ne $PSScriptRoot))
        if ($importsOk -and ($have -eq $want) -and (-not $moved)) {
            Write-Check 'ok' 'Packages' 'installed and up to date'
        } elseif ($importsOk) {
            Write-Check 'warn' 'Packages' 'installed, but pyproject/constraints changed or the folder was moved'
            [void]$todo.Add('Sync the packages with pyproject.toml')
        } else {
            Write-Check 'warn' 'Packages' 'missing or broken'
            [void]$todo.Add('Install / repair the packages')
        }
        if ($importsOk) {
            $pc = Get-PipCheckProblem
            if ($pc) { Write-Check 'warn' 'pip check' $pc; [void]$todo.Add('Repair inconsistent packages') }
        }
    }

    if (Test-Path "Registry::HKEY_CLASSES_ROOT\MSProject.Application") {
        Write-Check 'ok' 'MS Project' 'desktop version is registered'
    } else {
        Write-Check 'warn' 'MS Project' 'desktop version not found (only the bundled sample can be used)'
    }
    $procs = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue)
    if ($procs.Count -gt 0) {
        $pe = Get-ProcElevation ([int]$procs[0].Id)
        $me = Test-SelfElevated
        if (($pe -eq 'denied') -or (($pe -eq 'elevated') -and (-not $me)) -or (($pe -eq 'normal') -and $me)) {
            Write-Check 'warn' 'MS Project' 'running with a different privilege level than this window (COM will fail)'
        } else {
            Write-Check 'ok' 'MS Project' 'running'
        }
    } else {
        Write-Check 'info' 'MS Project' 'not running'
    }

    if ($venvOk -and $importsOk) {
        $jp = Write-ProbeFile 'jev' $JevPyCode
        $jr = Invoke-Probe $VenvPy @(('"' + $jp + '"')) 25
        if ($jr.Out -match 'KEY=1') { Write-Check 'ok' 'API key' 'configured' }
        elseif ($jr.Out -match 'KEY=0') { Write-Check 'info' 'API key' 'not set (the app works in its provisional mode)' }
    }

    Write-Host ""
    if ($todo.Count -eq 0) {
        Write-Host "  Ready: a normal run opens the dashboard right away."
        return 0
    }
    Write-Host "  A normal run would:"
    foreach ($t in $todo) { Write-Host ("    - " + $t) }
    return 3
}

# One ZIP with everything needed to debug a failing PC. Keys, .env files and tokens are never read.
function Invoke-DiagnoseMode {
    Write-Host ""
    Write-Host "  Collecting diagnostics (keys, .env files and tokens are never read or included) ..."
    $stamp  = (Get-Date).ToString("yyyyMMdd-HHmmss", $Inv)
    $outDir = Join-Path $AppDataDir "diagnostics"
    $work   = Join-Path $outDir ("diag-" + $stamp)
    $zip    = Join-Path $outDir ("projectpilot-diagnose-" + $stamp + ".zip")
    try { New-Item -ItemType Directory -Path $work -Force -ErrorAction Stop | Out-Null }
    catch { Write-Host ("  Cannot create " + (Protect-Text $work)); return 10 }

    $r = New-Object System.Collections.ArrayList
    [void]$r.Add("ProjectPilot diagnostics " + (Get-Date).ToString("s", $Inv))
    [void]$r.Add("PowerShell      : " + $PSVersionTable.PSVersion.ToString())
    [void]$r.Add("Windows         : " + (Get-OsName))
    [void]$r.Add("64-bit OS       : " + [Environment]::Is64BitOperatingSystem)
    [void]$r.Add("Elevated        : " + (Test-SelfElevated))
    [void]$r.Add("Terminal        : " + (Get-TerminalKind))
    [void]$r.Add("Folder          : " + (Protect-Text $PSScriptRoot))
    try {
        $drive = New-Object System.IO.DriveInfo($PSScriptRoot.Substring(0, 1))
        [void]$r.Add("Free disk       : " + ([Math]::Round($drive.AvailableFreeSpace / 1GB, 1)).ToString("0.0", $Inv) + " GB")
    } catch { }
    try {
        $lp = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -ErrorAction Stop).LongPathsEnabled
        [void]$r.Add("Long paths      : " + $lp)
    } catch { }
    foreach ($pn in @(Get-PathNotes)) { [void]$r.Add("Path note       : " + $pn) }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git -and (Test-Path (Join-Path $PSScriptRoot ".git"))) {
        $g = Invoke-Probe $git.Source @('rev-parse', '--short', 'HEAD') 15
        [void]$r.Add("Git commit      : " + $g.Out)
    }

    [void]$r.Add("")
    [void]$r.Add("--- network ---")
    foreach ($n in @('PIP_INDEX_URL', 'PIP_CONFIG_FILE', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'PIP_CERT', 'REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE')) {
        $v = [Environment]::GetEnvironmentVariable($n)
        if ($v) { [void]$r.Add($n.PadRight(19) + ": set") }
    }
    $px = Set-PipProxyFromSystem
    if ($px) { [void]$r.Add("System proxy     : " + (Protect-Text $px)) }
    $ci = Get-CustomIndexUrl
    if ($ci) {
        $ch = ''
        try { $ch = (New-Object System.Uri($ci)).Host } catch { }
        [void]$r.Add("Custom index     : " + $ch)
    }
    $script:Online = Test-Online
    [void]$r.Add("Index reachable  : " + $script:Online + " " + $script:NetReason)

    [void]$r.Add("")
    [void]$r.Add("--- python ---")
    $py = Find-Python
    if ($py) { [void]$r.Add("Selected python : " + (Protect-Text $py)) } else { [void]$r.Add("Selected python : none (3.12+ 64-bit not found)") }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pl = Invoke-Probe 'py' @('-0p') 15
        [void]$r.Add("py launcher list :")
        foreach ($ln in ($pl.Out -split "`n")) { [void]$r.Add("    " + (Protect-Text $ln.Trim())) }
    }
    $venvOk = (Test-Path $VenvPy) -and (Test-Python $VenvPy)
    [void]$r.Add("Venv usable      : " + $venvOk)
    if ($venvOk) {
        $pipv = Invoke-Probe $VenvPy @('-m', 'pip', '--version') 40
        [void]$r.Add("pip              : " + (Protect-Text $pipv.Out))
        [void]$r.Add("Imports ok       : " + (Test-Imports))
        $pc = Get-PipCheckProblem
        if ($pc) { [void]$r.Add("pip check        : " + $pc) } else { [void]$r.Add("pip check        : ok") }
        $fz = Invoke-Probe $VenvPy @('-m', 'pip', 'freeze') 90
        [void]$r.Add("")
        [void]$r.Add("--- pip freeze ---")
        foreach ($ln in ($fz.Out -split "`n")) { [void]$r.Add((Protect-Text $ln.Trim())) }
    }
    try {
        $want = Get-DepsFingerprint
        $have = ''
        $hf = Join-Path $VenvDir ".pp-deps-hash"
        if (Test-Path $hf) { $have = (("" + (Get-Content $hf -ErrorAction SilentlyContinue)).Trim()) }
        [void]$r.Add("")
        [void]$r.Add("Fingerprint     : wanted " + $want.Substring(0, [Math]::Min(14, $want.Length)) + " / installed " + $have.Substring(0, [Math]::Min(14, $have.Length)))
        [void]$r.Add("Stored root      : " + (Protect-Text (Get-StoredRoot)))
    } catch { }
    [void]$r.Add("constraints.txt  : " + (Test-Path (Join-Path $PSScriptRoot "constraints.txt")))
    $whDir = Join-Path $PSScriptRoot "wheelhouse"
    $whN = 0
    if (Test-Path $whDir) { $whN = @(Get-ChildItem $whDir -File -ErrorAction SilentlyContinue).Count }
    [void]$r.Add("wheelhouse files : " + $whN)

    [void]$r.Add("")
    [void]$r.Add("--- microsoft project ---")
    [void]$r.Add("Registered       : " + (Test-Path "Registry::HKEY_CLASSES_ROOT\MSProject.Application"))
    $procs = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue)
    [void]$r.Add("Running          : " + ($procs.Count -gt 0))
    if ($procs.Count -gt 0) { [void]$r.Add("MSP elevation    : " + (Get-ProcElevation ([int]$procs[0].Id))) }
    if ($venvOk) {
        $cp = Write-ProbeFile 'com' $ComPyCode
        $cr = Invoke-Probe $VenvPy @(('"' + $cp + '"')) 20
        [void]$r.Add("COM probe        : " + (Protect-Text $cr.Out))
        $jp = Write-ProbeFile 'jev' $JevPyCode
        $jr = Invoke-Probe $VenvPy @(('"' + $jp + '"')) 25
        [void]$r.Add("Settings probe   : " + (Protect-Text $jr.Out))
    }

    try { [System.IO.File]::WriteAllLines((Join-Path $work "report.txt"), [string[]]$r.ToArray(), (New-Object System.Text.UTF8Encoding($false))) } catch { }
    foreach ($f in @("pyproject.toml", "constraints.txt")) {
        $src = Join-Path $PSScriptRoot $f
        if (Test-Path $src) { try { Copy-Item $src (Join-Path $work $f) -ErrorAction SilentlyContinue } catch { } }
    }
    # logs are copied with paths and credentials redacted
    $logs = @()
    if (Test-Path $LogDir) {
        $logs = @(Get-ChildItem $LogDir -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 8)
    }
    foreach ($lf in $logs) {
        try {
            $txt = [System.IO.File]::ReadAllText($lf.FullName)
            [System.IO.File]::WriteAllText((Join-Path $work $lf.Name), (Protect-Text $txt), (New-Object System.Text.UTF8Encoding($false)))
        } catch { }
    }

    try {
        Compress-Archive -Path (Join-Path $work '*') -DestinationPath $zip -Force -ErrorAction Stop
    } catch {
        Write-Host ("  Could not create the ZIP: " + $_.Exception.Message)
        return 10
    }
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $outDir -Filter "projectpilot-diagnose-*.zip" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host ("  Done: " + $zip)
    Write-Host "  Send this ZIP to whoever helps you. It contains no passwords, keys or .env files."
    Write-Host ""
    if (-not ($Silent -or $NoPause)) { try { Start-Process explorer.exe ('/select,"' + $zip + '"') } catch { } }
    return 0
}

# Downloads every package (plus the Python installer) into .\wheelhouse for PCs without internet
function Invoke-WheelhouseMode {
    Write-Host ""
    Write-Host "  Building the offline wheelhouse (needs internet: run this once on an online PC)"
    Write-Host ""
    [void](Set-PipProxyFromSystem)
    $script:Online = Test-Online
    if (-not $script:Online) {
        Write-Host ("  No internet connection (" + $script:NetReason + "): the wheelhouse cannot be built here.")
        return 10
    }
    $py = $null
    if ((Test-Path $VenvPy) -and (Test-Python $VenvPy)) { $py = $VenvPy } else { $py = Find-Python }
    if (-not $py) {
        Write-Host "  Python 3.12+ was not found. Run run.bat once first (it installs Python)."
        return 10
    }
    $pi = Get-PyInfo $py
    $wh = Join-Path $PSScriptRoot "wheelhouse"
    New-Item -ItemType Directory -Path $wh -Force | Out-Null

    $cArgs = @()
    if (Test-Path (Join-Path $PSScriptRoot "constraints.txt")) { $cArgs = @('-c', 'constraints.txt') }

    Write-Host "  [1/3] Project packages ..."
    & $py -m pip download --disable-pip-version-check --dest $wh @cArgs ".[msp]"
    if ($LASTEXITCODE -ne 0) { Write-Host "  pip download failed (see the messages above)."; return 10 }

    $raw = ([System.IO.File]::ReadAllText((Join-Path $PSScriptRoot "pyproject.toml"))) -replace "`r", ''
    $reqText = Get-TomlArrayText $raw 'requires'
    $reqs = @()
    foreach ($m in [regex]::Matches($reqText, '["'']([^"'']+)["'']')) { $reqs += $m.Groups[1].Value }
    if ($reqs.Count -gt 0) {
        Write-Host "  [2/3] Build tools (needed to install the project offline) ..."
        & $py -m pip download --disable-pip-version-check --dest $wh @reqs
        if ($LASTEXITCODE -ne 0) { Write-Host "  Downloading the build tools failed."; return 10 }
    } else {
        Write-Host "  [2/3] Build tools: none declared"
    }

    Write-Host "  [3/3] Python installer ..."
    $installer = Join-Path $wh ("python-" + $PythonVersion + "-amd64.exe")
    if (-not (Test-Path $installer)) {
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
        & curl.exe -L -f -s --connect-timeout 20 --max-time 600 --retry 3 -o $installer $url
        if ($LASTEXITCODE -ne 0) {
            Remove-Item $installer -Force -ErrorAction SilentlyContinue
            Write-Host "  The Python installer could not be downloaded (optional: only needed on PCs without Python)."
        }
    }

    $note = "Built by run.bat -Wheelhouse on " + (Get-Date).ToString("s", $Inv) + " with Python " + $pi.Major + "." + $pi.Minor + " (64-bit)." + "`r`n" +
            "Copy this folder next to run.bat on the offline PC (64-bit Windows, same Python minor version), then run: run.bat -Offline"
    try { Set-Content -Path (Join-Path $wh "README.txt") -Value $note -Encoding ASCII } catch { }
    $n = @(Get-ChildItem $wh -File -ErrorAction SilentlyContinue).Count
    Write-Host ""
    Write-Host ("  Done: " + $n + " files in " + (Protect-Text $wh))
    Write-Host "  Copy the folder to the offline PC and run: run.bat -Offline"
    Write-Host ""
    return 0
}

# git pull --ff-only. Returns 'restart' when run.ps1 / run.bat themselves changed.
function Invoke-UpdateStep {
    if ($Offline) { Add-Note "-Update was skipped because -Offline is on."; return '' }
    if (-not (Test-Path (Join-Path $PSScriptRoot ".git"))) {
        Add-Note ("-Update: this folder is not a git checkout. Download the latest ZIP from " + $RepoUrl + " and extract it over this folder.")
        return ''
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Add-Note ("-Update: git is not installed. Install Git for Windows, or download the latest ZIP from " + $RepoUrl + ".")
        return ''
    }
    $st = Invoke-Probe $git.Source @('status', '--porcelain') 30
    if ($st.Out) {
        Add-Note "-Update was skipped: this folder has local changes. Commit or stash them first."
        return ''
    }
    $before = @{}
    foreach ($f in @("run.ps1", "run.bat")) {
        $fp = Join-Path $PSScriptRoot $f
        if (Test-Path $fp) { $before[$f] = (Get-FileHash $fp -Algorithm SHA256).Hash }
    }
    Write-Host "  Updating from git ..."
    $r = Invoke-Probe $git.Source @('pull', '--ff-only') 120
    if ($r.Code -ne 0) {
        $first = (@(($r.Err + "`n" + $r.Out) -split "`n" | Where-Object { $_.Trim().Length -gt 0 }) | Select-Object -First 1)
        Add-Note ("-Update failed: " + (Protect-Text ([string]$first)))
        return ''
    }
    foreach ($f in @("run.ps1", "run.bat")) {
        $fp = Join-Path $PSScriptRoot $f
        if ((Test-Path $fp) -and $before.ContainsKey($f) -and ((Get-FileHash $fp -Algorithm SHA256).Hash -ne $before[$f])) {
            Write-Host ""
            Write-Host "  The launcher itself was updated. Run run.bat again to continue with the new version."
            Write-Host ""
            return 'restart'
        }
    }
    $line = (@($r.Out -split "`n" | Where-Object { $_.Trim().Length -gt 0 }) | Select-Object -First 1)
    Add-Note ("Update: " + [string]$line)
    return ''
}

function Invoke-UninstallMode {
    Write-Host ""
    Write-Host "  ProjectPilot uninstall. This removes:"
    Write-Host ("    - " + $VenvDir + " (the virtual environment)")
    Write-Host ("    - " + (Protect-Text $AppDataDir) + " (logs and diagnostics)")
    Write-Host "    - temporary launcher files"
    Write-Host "  It keeps: the project folder, your .env, the wheelhouse folder and Python itself."
    Write-Host ""
    if (-not $Yes) {
        if ($Silent) { Write-Host "  Refusing to delete in silent mode without -Yes."; return 10 }
        $a = Read-Host "  Continue? [y/N]"
        if ($a -notmatch '^(y|yes)$') { Write-Host "  Cancelled."; return 0 }
    }
    $bad = $false
    if (Test-Path $VenvDir) {
        if (Remove-VenvDir) { Write-Check 'ok' 'Virtual env' 'removed' }
        else { Write-Check 'fail' 'Virtual env' 'could not be removed: close editors/terminals that use it'; $bad = $true }
    } else {
        Write-Check 'info' 'Virtual env' 'not present'
    }
    try {
        if (Test-Path $AppDataDir) { Remove-Item $AppDataDir -Recurse -Force -ErrorAction SilentlyContinue }
        Write-Check 'ok' 'Logs' 'removed'
    } catch { }
    try {
        Get-ChildItem $env:TEMP -Filter "projectpilot-*" -ErrorAction SilentlyContinue | Remove-Item -Force -Recurse -ErrorAction SilentlyContinue
        Write-Check 'ok' 'Temp files' 'removed'
    } catch { }
    Write-Host ""
    if ($bad) { return 10 }
    return 0
}

# ===========================================================================
#  Main Execution Flow
# ===========================================================================
try {
    Write-SessionLog ("ProjectPilot launcher " + (Get-Date).ToString("s", $Inv) + " | PowerShell " + $PSVersionTable.PSVersion.ToString() + " | " + $PSScriptRoot)

    # read-only modes work even while another launcher window is busy
    if ($Check)    { $rc = @(Invoke-CheckMode);    exit ([int]$rc[-1]) }
    if ($Diagnose) { $rc = @(Invoke-DiagnoseMode); exit ([int]$rc[-1]) }

    if (-not (Enter-SingleInstance)) {
        Write-Host ""
        Write-Host "  ProjectPilot is already running in another window."
        Write-Host "  Close that window (or wait for its setup to finish), then run again."
        Write-Host ""
        if (-not $NoPause) { [void](Read-Host "  Press Enter to close") }
        exit 2
    }

    if ($Wheelhouse) { $rc = @(Invoke-WheelhouseMode); exit ([int]$rc[-1]) }
    if ($Uninstall)  { $rc = @(Invoke-UninstallMode);  exit ([int]$rc[-1]) }
    if ($Update) {
        $upd = @(Invoke-UpdateStep)
        if ($upd[-1] -eq 'restart') { exit 0 }
    }

    Set-Cursor $false
    Set-Title "ProjectPilot"
    if (-not $Silent) { Show-Banner }

    if ($script:UseVT) {
        Draw-Board
    } else {
        Write-Host ""
    }

    # -----------------------------------------------------------------------
    # 1. System Checks (64-bit OS, project files, write access, terminal, disk)
    # -----------------------------------------------------------------------
    Start-Task 'sys' 'Inspecting host'

    $osName = Get-OsName

    if (-not [Environment]::Is64BitOperatingSystem) {
        Complete-Task 'sys' 'fail' '32-bit OS unsupported'
        Stop-WithPause "ProjectPilot requires a 64-bit version of Windows."
    }

    if (-not (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
        Complete-Task 'sys' 'fail' 'pyproject.toml missing'
        Stop-WithPause "Keep run.bat and run.ps1 inside the ProjectPilot folder, next to pyproject.toml."
    }

    $writable = $true
    try {
        $probe = Join-Path $PSScriptRoot (".pp-probe-" + [Guid]::NewGuid().ToString("N") + ".tmp")
        [System.IO.File]::WriteAllText($probe, "ok")
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
    } catch {
        $writable = $false
    }
    if (-not $writable) {
        Complete-Task 'sys' 'fail' 'Folder not writable'
        Stop-WithPause "ProjectPilot cannot write to its own folder." -Hint "Move the folder out of protected locations (Program Files, synced read-only folders) and run again."
    }

    $sysWarnings = @()
    $termKind = Get-TerminalKind
    $termSize = $script:InitW.ToString($Inv) + "x" + $script:InitH.ToString($Inv)
    if (($script:InitW -lt 120) -or ($script:InitH -lt 34)) {
        $sysWarnings += ("Term " + $termSize)
        Add-Note ("The terminal is only " + $termSize + ". The dashboard looks best at 120x34 or larger, so maximize the window before pressing a key.")
    }
    if ($termKind -eq 'Legacy console') {
        $sysWarnings += 'Legacy console'
        Add-Note "This is the legacy Windows console. Windows Terminal draws the dashboard much better (winget install Microsoft.WindowsTerminal). Run run.bat from a Windows Terminal tab."
    }
    $pathNotes = @(Get-PathNotes)
    foreach ($pn in $pathNotes) { Add-Note $pn }
    if ($pathNotes.Count -gt 0) { $sysWarnings += 'Folder path' }

    $freeGBText = ""
    try {
        $drive  = New-Object System.IO.DriveInfo($PSScriptRoot.Substring(0, 1))
        $freeGB = [Math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        $freeGBText = $freeGB.ToString("0.0", $Inv) + " GB free"
        if ($freeGB -lt 1.0) {
            $sysWarnings += ("Low disk (" + $freeGBText + ")")
            Add-Note ("Only " + $freeGBText + " is left on this drive. Installing the packages needs about 1 GB; free some space first.")
        }
    } catch { }

    if ($sysWarnings.Count -gt 0) {
        $warnDetail = ($sysWarnings -join ", ")
        Complete-Task 'sys' 'warn' $warnDetail
        Add-Summary "System" ($osName + " (" + $warnDetail + ")") 'warn'
    } else {
        $okDetail = $osName
        if ($freeGBText) { $okDetail += " " + $G_Dot + " " + $freeGBText }
        Complete-Task 'sys' 'ok' $okDetail
        Add-Summary "Windows" $osName 'ok'
    }
    Add-Summary "Terminal" ($termKind + " " + $termSize) 'ok'

    # -----------------------------------------------------------------------
    # 2. Internet Reachability (HTTPS to pypi.org and files.pythonhosted.org)
    # -----------------------------------------------------------------------
    Start-Task 'net' 'pypi.org + files host'
    if ($Offline) {
        $script:Online    = $false
        $script:NetReason = 'Offline mode'
        Complete-Task 'net' 'ok' 'Offline mode (wheelhouse)'
        if (-not (Test-Wheelhouse)) {
            Add-Note "Offline mode is on but there is no wheelhouse folder next to run.bat. On a PC with internet run run.bat -Wheelhouse and copy the folder here."
        }
    } else {
        $proxyUsed = Set-PipProxyFromSystem
        if ($proxyUsed) { Write-SessionLog ("System proxy handed to pip: " + $proxyUsed) }
        $script:Online = Test-Online
        if ($script:Online) {
            if ($proxyUsed) {
                Complete-Task 'net' 'ok' 'Connected (via proxy)'
            } else {
                Complete-Task 'net' 'ok' 'Connected'
            }
        } else {
            if (Test-Wheelhouse) {
                Complete-Task 'net' 'warn' ($script:NetReason + " - wheelhouse")
            } else {
                Complete-Task 'net' 'warn' $script:NetReason
            }
        }
    }

    # -----------------------------------------------------------------------
    # 3. Python 3.12+ Detection & Automatic Installation
    # -----------------------------------------------------------------------
    Start-Task 'py' 'Locating interpreter'
    $py = Find-Python
    if (-not $py) {
        if ($AskBeforeInstall) {
            Complete-Task 'py' 'warn' 'Python 3.12+ missing'
            if (-not (Ask-YesNo "Python 3.12+ was not found. Install it automatically now?")) {
                Complete-Task 'py' 'fail' 'Aborted by user'
                Stop-WithPause "Python 3.12+ is required." -Hint "Install it from python.org (tick Add python.exe to PATH) and run this again."
            }
            Start-Task 'py' 'Preparing installer'
        } else {
            Update-TaskNote 'py' 'Missing - installing 3.12'
        }
        $installedOk = Install-Python
        $py = Find-Python
        # exit codes such as 3010 (reboot advised) still mean success: only a missing interpreter is a failure
        if (-not $py) {
            Complete-Task 'py' 'fail' 'Auto-install failed'
            Stop-WithPause "Python could not be installed automatically." -WithLog -LogTitle "Python Installer" -Hint "Install Python 3.12+ from python.org (tick Add python.exe to PATH), then run this again."
        }
    }

    $oldEA = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $pyVerRaw = "$(& $py --version 2>&1)".Trim()
    $ErrorActionPreference = $oldEA
    $pyVerClean = ($pyVerRaw -replace '^Python\s*', '').Trim()

    Complete-Task 'py' 'ok' ("Python " + $pyVerClean)
    Add-Summary "Python" ($pyVerClean + " (64-bit)") 'ok'
    if ($script:PyNewer) {
        Add-Note ("Python " + $pyVerClean + " is newer than the tested versions (3.12-3.13). If a package fails to install, install Python 3.12 from python.org and run again.")
    }

    # -----------------------------------------------------------------------
    # 4. Virtual Environment (.venv): verify, repair pip, or rebuild from zero
    # -----------------------------------------------------------------------
    Start-Task 'venv' 'Verifying .venv'
    $venvStatus  = "Ready"
    $needRebuild = $false
    $rebuildWhy  = ""

    if ($Repair -and (Test-Path $VenvDir)) {
        $needRebuild = $true
        $rebuildWhy  = "Repair: clean rebuild"
    } elseif ((Test-Path $VenvDir) -and (-not (Test-Path $VenvPy))) {
        $needRebuild = $true
        $rebuildWhy  = "Removing incomplete .venv"
    } elseif (Test-Path $VenvPy) {
        if (-not (Test-Python $VenvPy)) {
            $needRebuild = $true
            $rebuildWhy  = "Rebuilding broken .venv"
        } elseif (-not (Test-VenvPip)) {
            [void](Invoke-BgStep 'venv' 'Restoring pip' $VenvPy @("-m", "ensurepip", "--upgrade"))
            if (Test-VenvPip) {
                $venvStatus = "Repaired"
            } else {
                $needRebuild = $true
                $rebuildWhy  = "Rebuilding (pip unusable)"
            }
        }
    }

    if ($needRebuild) {
        Assert-Online 'venv' -AllowWheelhouse   # never delete a working setup that cannot be re-created
        Update-TaskNote 'venv' $rebuildWhy
        if (-not (Remove-VenvDir)) {
            Complete-Task 'venv' 'fail' 'Cannot remove .venv'
            Stop-WithPause "Could not delete the old virtual environment." -Hint "Close editors, terminals and Python programs that use the .venv folder, then run again."
        }
        $venvStatus = "Rebuilt"
        $script:CleanTried = $true
    }

    if (-not (Test-Path $VenvPy)) {
        if ($venvStatus -eq "Ready") { $venvStatus = "Created" }
        $venvCreated = Invoke-BgStep 'venv' "Creating $VenvDir" $py @("-m", "venv", $VenvDir)
        if (-not $venvCreated) {
            Complete-Task 'venv' 'fail' 'venv creation failed'
            Stop-WithPause "Could not create the virtual environment." -WithLog -LogTitle "python -m venv" -Hint "Reinstall Python 3.12+ from python.org, then run again."
        }
    }
    Complete-Task 'venv' 'ok' ("$VenvDir ($venvStatus)")
    Add-Summary "Environment" ("$VenvDir ($venvStatus)") 'ok'

    # -----------------------------------------------------------------------
    # 5. Packages: SHA256 lock check, install/sync, clean reinstall on failure
    # -----------------------------------------------------------------------
    Start-Task 'deps' 'Checking package lock'
    $hashFile = Join-Path $VenvDir ".pp-deps-hash"
    $wantHash = ""
    try { $wantHash = Get-DepsFingerprint } catch { $wantHash = "unreadable" }
    $haveHash = ""
    if (Test-Path $hashFile) {
        $haveHash = ("" + (Get-Content $hashFile -ErrorAction SilentlyContinue)).Trim()
    }
    $importsOk  = Test-Imports
    $storedRoot = Get-StoredRoot
    # the folder was moved after the editable install: imports may still work from the old place
    $moved = $false
    if ($importsOk -and $storedRoot) {
        $moved = ($storedRoot.TrimEnd('\') -ne $PSScriptRoot.TrimEnd('\'))
    }
    $depsState   = 'ok'
    $depsDetail  = 'Up to date'
    $depsSummary = 'Up to date'
    $needInstall = $true

    if ($importsOk -and ($haveHash -eq $wantHash) -and (-not $moved)) {
        $needInstall = $false
    } elseif ($importsOk -and (-not $moved) -and (-not $script:Online)) {
        # Everything loads and only the lock differs: never fail a working setup just because we are offline
        $needInstall = $false
        $depsState   = 'warn'
        $depsDetail  = 'Offline: kept installed'
        $depsSummary = 'Offline (kept installed)'
        Add-Note "pyproject.toml changed since the last install, but there is no internet. The installed packages still load, so ProjectPilot continues with them. Reconnect once to sync."
    } elseif ($moved) {
        if (Install-Deps 'deps' 'Re-linking moved folder' $false $true) {
            Save-DepsHash
            if ($haveHash -eq $wantHash) {
                $needInstall = $false
                $depsDetail  = 'Re-linked after move'
                $depsSummary = 'Re-linked (folder moved)'
            }
        }
    }

    if ($needInstall) {
        $reasonNote = "Installing packages"
        if ($importsOk -and ($haveHash -ne $wantHash) -and ($haveHash -ne "")) {
            $reasonNote = "Syncing pyproject.toml"
        } elseif ((-not $importsOk) -and ($haveHash -ne "")) {
            $reasonNote = "Repairing packages"
        }
        Assert-Online 'deps' -AllowWheelhouse

        $depsSummary = "Installed just now"
        $depsOk = Install-Deps 'deps' $reasonNote $false
        if (-not $depsOk) {
            if (-not $script:CleanTried) {
                $depsOk = Rebuild-Environment 'deps' $py
                if ($depsOk) { $depsSummary = "Reinstalled from scratch" }
            } else {
                $script:FailStage = 'pip'
                $depsOk = Install-Deps 'deps' "Retrying without cache" $true
            }
        }
        if (-not $depsOk) { Stop-RebuildFailed 'deps' }

        Save-DepsHash
        $depsDetail = 'Installed & locked'
    }

    # consistency of the installed set, checked whenever the environment was just changed
    $pipProblem = ''
    if ($needInstall -or $moved -or $Repair) {
        Update-TaskNote 'deps' 'Running pip check'
        $pipProblem = Get-PipCheckProblem
    }
    if ($pipProblem) {
        $depsState   = 'warn'
        $depsDetail  = 'pip check: conflict'
        $depsSummary = $depsSummary + ' (pip check)'
        Add-Note ("pip check reports a package conflict: " + $pipProblem + " - run run.bat -Repair if the dashboard misbehaves.")
    }
    Complete-Task 'deps' $depsState $depsDetail
    Add-Summary "Packages" $depsSummary $depsState

    # -----------------------------------------------------------------------
    # 6. Dashboard engine: pre-load, then a headless self-test
    # -----------------------------------------------------------------------
    Start-Task 'app' 'Pre-loading dashboard'
    $warmArgs = @("-c", '"import project_decision.tui"')
    $warmOk   = Invoke-BgStep 'app' 'Pre-loading dashboard' $VenvPy $warmArgs
    $appStatus = "Loaded"
    if ((-not $warmOk) -and (-not $script:CleanTried)) {
        if (-not (Rebuild-Environment 'app' $py)) { Stop-RebuildFailed 'app' }
        $warmOk = Invoke-BgStep 'app' 'Pre-loading dashboard' $VenvPy $warmArgs
        $appStatus = "Repaired"
        foreach ($srow in $SummaryRows) {
            if ($srow[0] -eq "Packages") { $srow[1] = "Reinstalled from scratch"; $srow[2] = 'ok' }
        }
    }
    if (-not $warmOk) {
        Complete-Task 'app' 'fail' 'Dashboard failed to load'
        Stop-WithPause "The dashboard module could not be loaded." -WithLog -LogTitle "import project_decision.tui" -Hint "Run run.bat -Repair for a clean reinstall. If it keeps failing, the error above comes from the project code."
    }

    # Start the real dashboard headlessly: catches broken styles or widgets before the user does
    $SmokePy  = Write-ProbeFile 'smoke' $SmokePyCode
    $smokeOk  = Invoke-BgStep 'app' 'Self-test (headless)' $VenvPy @(('"' + $SmokePy + '"')) 90
    $smokeTimedOut = $script:LastTimedOut
    if ($smokeOk -and ((Get-LastOutput) -notmatch 'SMOKE_OK')) { $smokeOk = $false }

    # a stale or corrupt install is the usual cause: rebuild once and test again.
    # A failure that survives the rebuild is remembered, so a real code bug does not rebuild on every launch.
    $badFile    = Join-Path $VenvDir ".pp-smoke-bad"
    $alreadyBad = $false
    if (Test-Path $badFile) {
        $alreadyBad = ((("" + (Get-Content $badFile -ErrorAction SilentlyContinue)).Trim()) -eq $wantHash)
    }
    if ((-not $smokeOk) -and (-not $smokeTimedOut) -and (-not $script:CleanTried) -and ($script:Online -or (Test-Wheelhouse)) -and (-not $alreadyBad)) {
        if (-not (Rebuild-Environment 'app' $py)) { Stop-RebuildFailed 'app' }
        $appStatus = "Repaired"
        foreach ($srow in $SummaryRows) {
            if ($srow[0] -eq "Packages") { $srow[1] = "Reinstalled from scratch"; $srow[2] = 'ok' }
        }
        $smokeOk = Invoke-BgStep 'app' 'Self-test (headless)' $VenvPy @(('"' + $SmokePy + '"')) 90
        $smokeTimedOut = $script:LastTimedOut
        if ($smokeOk -and ((Get-LastOutput) -notmatch 'SMOKE_OK')) { $smokeOk = $false }
        if (-not $smokeOk) {
            try { Set-Content -Path $badFile -Value $wantHash -Encoding ASCII } catch { }
        }
    }
    if ($smokeOk) { Remove-Item $badFile -Force -ErrorAction SilentlyContinue }

    if ($smokeOk) {
        Complete-Task 'app' 'ok' ($appStatus + " & tested")
        Add-Summary "Dashboard" ($appStatus + " & tested") 'ok'
        if ($Freeze) {
            try {
                $frozen = @(& $VenvPy -m pip freeze --exclude-editable 2>$null)
                if ($frozen.Count -gt 0) {
                    $ct = (($frozen | ForEach-Object { "$_" }) -join "`n") + "`n"
                    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "constraints.txt"), $ct, (New-Object System.Text.UTF8Encoding($false)))
                    Save-DepsHash
                    Add-Note "constraints.txt was written with the tested package versions. Commit it so every PC installs exactly these versions."
                }
            } catch { }
        }
    } else {
        # The packages import fine, so this is almost certainly a code problem: warn, do not block
        $why = "Self-test failed"
        if ($smokeTimedOut) { $why = "Self-test timed out" }
        Complete-Task 'app' 'warn' $why
        Add-Summary "Dashboard" $why 'warn'
        Add-Note ("The dashboard's automatic self-test did not pass, so it may crash when it opens. The error is in the log: " + $SessionLog)
    }

    # -----------------------------------------------------------------------
    # 7. Microsoft Project: pywin32, privilege level, read-only COM probe (notices only)
    # -----------------------------------------------------------------------
    Start-Task 'msp' 'Probing COM registry'
    $mspRegistered = Test-Path "Registry::HKEY_CLASSES_ROOT\MSProject.Application"
    $mspProcs      = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue)

    # the sample plan that the S key points at
    $samplePath = Join-Path $PSScriptRoot "examples\production-line-commissioning\Production_Line_Commissioning.mpp"
    if (-not (Test-Path $samplePath)) {
        Add-Note "The bundled sample project (examples\production-line-commissioning) was not found. The S key in the dashboard will describe it, but the file is missing."
    }

    # pywin32 is what actually talks to Microsoft Project
    $winOk = $true
    if ($mspRegistered) {
        $winCode = "import pythoncom, win32com.client"
        $winOk = Test-Imports $winCode
        if ((-not $winOk) -and $script:Online) {
            $winOk = Invoke-BgStep 'msp' 'Installing pywin32' $VenvPy @("-m", "pip", "install", "--disable-pip-version-check", "pywin32")
            if ($winOk) { $winOk = Test-Imports $winCode }
        }
    }

    if (-not $mspRegistered) {
        Complete-Task 'msp' 'warn' 'Not detected (optional)'
        Add-Summary "Microsoft Project" "Not detected" 'warn'
        Add-Note "Microsoft Project was not found on this PC. The dashboard reads plans from it; the sample guide (S key) still works."
    } elseif (-not $winOk) {
        Complete-Task 'msp' 'warn' 'pywin32 unavailable'
        Add-Summary "Microsoft Project" "pywin32 unavailable" 'warn'
        Add-Note "The pywin32 package could not be loaded, so the dashboard cannot talk to Microsoft Project. Run run.bat -Repair while online."
    } elseif ($mspProcs.Count -eq 0) {
        Complete-Task 'msp' 'ok' 'Installed (idle)'
        Add-Summary "Microsoft Project" "Installed (not open)" 'ok'
    } else {
        # Project is running: compare privilege levels, then ask it (read-only) how many plans are open
        $selfElev = Test-SelfElevated
        $projElev = Get-ProcElevation $mspProcs[0].Id
        $mismatch = $false
        if ((-not $selfElev) -and (($projElev -eq 'elevated') -or ($projElev -eq 'denied'))) { $mismatch = $true }
        if ($selfElev -and ($projElev -eq 'normal')) { $mismatch = $true }

        $ComPy  = Write-ProbeFile 'com' $ComPyCode
        $comOk  = Invoke-BgStep 'msp' 'Testing the COM link' $VenvPy @(('"' + $ComPy + '"')) 12
        $comOut = Get-LastOutput
        $comTimedOut = $script:LastTimedOut

        $mspState  = 'warn'
        $mspDetail = 'COM not answering'
        if ($comTimedOut) {
            $mspDetail = 'Not responding'
            Add-Note "Microsoft Project did not answer within 12 seconds. A dialog may be open in it; close the dialog and run again."
        } elseif ($comOut -match '^OK\|(\d+)') {
            $open = [int]$Matches[1]
            $mspState = 'ok'
            if ($open -gt 0) {
                $mspDetail = "Linked (" + $open.ToString($Inv) + " open)"
            } else {
                $mspDetail = "Running, no plan open"
                Add-Note "Microsoft Project is open but no plan is loaded. Open the plan you want to analyze, then press L in the dashboard."
            }
        } elseif ($mismatch -or ($comOut -match 'ERR:-2147024891')) {
            $mspDetail = 'Privilege mismatch'
            if ($selfElev) {
                Add-Note "This window runs as administrator but Microsoft Project does not, so they cannot talk to each other. Close this window and run run.bat normally."
            } else {
                Add-Note "Microsoft Project runs as administrator but this window does not, so they cannot talk to each other. Restart Project normally, or run run.bat as administrator."
            }
        } elseif ($comOut -match 'ERR:-2147221021') {
            $mspDetail = 'Not ready (COM)'
            Add-Note "Microsoft Project is running but is not registered for automation yet. Wait a few seconds after it starts, or restart it, then run again."
        } elseif ($comOut -match 'NOPYWIN32') {
            $mspDetail = 'pywin32 unavailable'
        } else {
            Add-Note "Microsoft Project is running but the COM test failed. If the dashboard cannot load the plan, restart Project and run again."
        }
        Complete-Task 'msp' $mspState $mspDetail
        Add-Summary "Microsoft Project" $mspDetail $mspState
    }

    # -----------------------------------------------------------------------
    # 8. TypeSafe JEV API Key Check (Notice only, never prints key)
    # -----------------------------------------------------------------------
    Start-Task 'jev' 'Checking credentials'
    $hasKey = $null
    $apiNet = 'NA'
    # Ask the application itself, so the launcher and the dashboard can never disagree
    if (Test-Path $VenvPy) {
        $JevPy = Write-ProbeFile 'jev' $JevPyCode
        $jevOk = Invoke-BgStep 'jev' 'Reading app settings' $VenvPy @(('"' + $JevPy + '"')) 20
        $jm = [regex]::Match((Get-LastOutput), 'KEY=(\d)\|NET=(\w+)')
        if ($jevOk -and $jm.Success) {
            $hasKey = ($jm.Groups[1].Value -eq '1')
            $apiNet = $jm.Groups[2].Value
        }
    }
    if ($null -eq $hasKey) {
        # fallback when the application's own loader could not run: read the variable and .env directly
        $hasKey = $false
        if ($env:TYPESAFE_API_KEY -and ($env:TYPESAFE_API_KEY.Trim() -ne '') -and ($env:TYPESAFE_API_KEY.Trim() -ne 'your-key-here')) {
            $hasKey = $true
        }
        $envFile = Join-Path $PSScriptRoot ".env"
        if ((-not $hasKey) -and (Test-Path $envFile)) {
            $m = Select-String -Path $envFile -Pattern '^\s*TYPESAFE_API_KEY\s*=\s*(\S+)' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
            if ($m) {
                $keyVal = $m.Matches[0].Groups[1].Value.Trim([char]34, [char]39).Trim()
                if ($keyVal -ne '' -and $keyVal -ne 'your-key-here') { $hasKey = $true }
            }
        }
    }
    if ($hasKey -and ($apiNet -eq 'DOWN')) {
        Complete-Task 'jev' 'warn' 'Key set, API unreachable'
        Add-Summary "TypeSafe JEV" "Key set, API unreachable" 'warn'
        Add-Note "A TypeSafe key is configured, but the TypeSafe service could not be reached from this PC (firewall, VPN or proxy?). Live JEV will fail; offline mode still works."
    } elseif ($hasKey) {
        Complete-Task 'jev' 'ok' 'Key configured'
        Add-Summary "TypeSafe JEV" "Key configured" 'ok'
    } else {
        Complete-Task 'jev' 'warn' 'Provisional mode (0.5)'
        Add-Summary "TypeSafe JEV" "No key (provisional)" 'warn'
    }

    # Settle the ribbon at 100%, then one bright pulse across it
    if ($script:UseVT) {
        $script:ProgressGoal = 1.0
        $script:ProgressFill = 1.0
        Draw-Board
        Play-RibbonPulse
        $script:BoardActive = $false   # the summary is printed below the board: never redraw over it
    }

    # -----------------------------------------------------------------------
    # 9. Summary Card, one key press, straight into the dashboard
    # -----------------------------------------------------------------------
    Set-Title "ProjectPilot - Ready"
    $totalSecs = Fmt-Sec $Clock.Elapsed.TotalSeconds
    Show-SummaryCard $totalSecs

    Show-Notes
    Write-Host ""

    $launch = Wait-LaunchKey
    if ($launch) {
        Play-LaunchSweep
        Set-Title "ProjectPilot"
        Set-Cursor $true
        $RunnerPy = Write-ProbeFile 'run' $RunnerPyCode
        $env:PP_CRASH_LOG = $CrashLog
        Remove-Item $CrashLog -Force -ErrorAction SilentlyContinue
        $session = [System.Diagnostics.Stopwatch]::StartNew()
        & $VenvPy $RunnerPy
        $tuiExit = $LASTEXITCODE
        $session.Stop()
        if ($tuiExit -ne 0) {
            if (Test-Path $CrashLog) {
                Show-LogPanel "Dashboard crash" $CrashLog
                try { Write-SessionLog ("CRASH REPORT:`n" + ((Get-Content $CrashLog -ErrorAction Stop) -join "`n")) } catch { }
                Write-Host ("  " + (Paint $C_Dim ($G_Dot + "  Crash report: " + $CrashLog)))
            }
            Stop-WithPause ("ProjectPilot exited with an error (code " + $tuiExit.ToString($Inv) + ").") -Hint "Run run.bat -Repair for a clean reinstall. If it keeps failing, check the project's own error output above."
        }
        Write-Host ""
        Write-Host ("  " + (Paint (Get-GradRGB 0.15) $G_Diamond) + "  " +
            (Paint-Gradient ("Session closed  " + $G_Dot + "  " + (Fmt-Duration $session.Elapsed.TotalSeconds))))
        Write-Host ""
    } else {
        Set-Cursor $true
        Write-Host ("  " + (Paint (Get-GradRGB 0.15) $G_Diamond) + "  " +
            (Paint-Gradient ("Setup complete  " + $G_Dot + "  dashboard not launched")))
        Write-Host ""
    }
} catch {
    $crashText = ($_ | Out-String)
    try { Set-Content -Path $CrashLog -Value ("LAUNCHER CRASH`n" + $crashText) -Encoding UTF8 } catch { }
    Write-SessionLog ("LAUNCHER CRASH: " + $crashText)
    Set-Cursor $true
    Write-Host ""
    Write-Host ("  Launcher crashed: " + $_.Exception.Message)
    Write-Host ("  Details: " + $SessionLog)
    Write-Host ""
    if (-not $NoPause) { [void](Read-Host "  Press Enter to close") }
    exit 99
} finally {
    if ($script:MutexHeld -and $script:LauncherMutex) {
        try { $script:LauncherMutex.ReleaseMutex() } catch { }
    }
    if ($script:UseVT) {
        [Console]::Write("$script:SyncOff$ESC[0m")
    }
    Set-Cursor $true
    if ($OrigTitle) { Set-Title $OrigTitle }
    try {
        Remove-Item $Log, "$Log.err" -Force -ErrorAction SilentlyContinue
        Get-ChildItem $env:TEMP -Filter ("projectpilot-" + $PID + "-*.py") -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    } catch { }
}
