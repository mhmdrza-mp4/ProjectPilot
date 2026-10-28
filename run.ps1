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
# ===========================================================================
param(
    [switch]$NoAnim,
    [switch]$NoPause,
    [switch]$Repair,
    [switch]$AskBeforeInstall
)

Set-Location $PSScriptRoot
try { [Environment]::CurrentDirectory = $PSScriptRoot } catch { }
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Clock         = [System.Diagnostics.Stopwatch]::StartNew()
$VenvDir       = ".venv"
$VenvPy        = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Log           = Join-Path $env:TEMP "projectpilot-setup.log"
$ESC           = [char]27
$Inv           = [System.Globalization.CultureInfo]::InvariantCulture
$Rng           = New-Object System.Random
$PythonVersion = "3.12.8"

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
try {
    if (-not ([System.Management.Automation.PSTypeName]'Win32.ConVT').Type) {
        Add-Type -Namespace Win32 -Name ConVT -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out int m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, int m);
'@
    }
    $hOut = [Win32.ConVT]::GetStdHandle(-11)
    $conMode = 0
    if ([Win32.ConVT]::GetConsoleMode($hOut, [ref]$conMode)) {
        $script:UseVT = [Win32.ConVT]::SetConsoleMode($hOut, ($conMode -bor 4))
    }
} catch { }
if ($env:NO_COLOR) { $script:UseVT = $false }

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
}

if ($env:WT_SESSION -and $script:UseVT) {
    $SpinFrames = @(0x280B,0x2819,0x2839,0x2838,0x283C,0x2834,0x2826,0x2827,0x2807,0x280F) |
                  ForEach-Object { [string][char]$_ }
} else {
    $SpinFrames = @('|','/','-','\')
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
Update-TermSize -RecordInitial

function Get-BoardWidth {
    Update-TermSize
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

    function Render-LogoFrame([int]$revealX, [double]$shinePos, [bool]$withSparkles) {
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append($script:SyncOn)
        for ($r = 0; $r -lt 5; $r++) {
            $rowStr = $LogoRows[$r]
            [void]$sb.Append("  ")
            for ($x = 0; $x -lt $w; $x++) {
                $cell = $rowStr[$x]
                if ($x -ge $revealX -or $cell -eq ' ') {
                    if ($withSparkles -and $x -lt $revealX -and $Rng.Next(140) -eq 0) {
                        [void]$sb.Append((FgSeq $C_Muted) + $G_Dot)
                    } else {
                        [void]$sb.Append(' ')
                    }
                    continue
                }
                $baseRGB = Get-GradRGB ($x / [double]($w - 1))
                if ($cell -eq ':') {
                    # Subtle depth shadow
                    $sr = [int]($baseRGB[0] * 0.32)
                    $sg = [int]($baseRGB[1] * 0.32)
                    $sb2= [int]($baseRGB[2] * 0.32)
                    [void]$sb.Append((FgSeq @($sr, $sg, $sb2)) + $G_Shadow)
                    continue
                }
                $cr = $baseRGB[0]; $cg = $baseRGB[1]; $cb = $baseRGB[2]
                # Diagonal specular light wave
                $dist = [Math]::Abs(($x + $r * 2.2) - $shinePos)
                if ($dist -lt 7.5) {
                    $glow = (1.0 - ($dist / 7.5)) * 0.88
                    $cr = Mix-Channel $cr 255 $glow
                    $cg = Mix-Channel $cg 255 $glow
                    $cb = Mix-Channel $cb 255 $glow
                }
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
            $rev   = [int][Math]::Round($eased * ($w + 2))
            $shine = ($eased * ($w + 24)) - 8.0
            [Console]::Write("$ESC[5A" + (Render-LogoFrame $rev $shine ($s -lt $steps)))
            Start-Sleep -Milliseconds 16
        }
        for ($sh = [int]($w * 0.45); $sh -le ($w + 18); $sh += 3) {
            [Console]::Write("$ESC[5A" + (Render-LogoFrame ($w + 1) $sh $true))
            Start-Sleep -Milliseconds 15
        }
        [Console]::Write("$ESC[5A")
    }
    [Console]::Write((Render-LogoFrame ($w + 1) -1000.0 $false))
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
    }
    [void]$Tasks.Add($obj)
    $TaskIndex[$obj.Id] = $obj
}

$script:BoardActive   = $false
$script:BoardLines    = $Tasks.Count + 2   # 1 ribbon + 1 spacer + N task rows
$script:ProgressFill  = 0.0                # Smooth current fill 0.0 .. 1.0
$script:ProgressGoal  = 0.0                # Target fill 0.0 .. 1.0
$script:AnimTick      = 0
$script:PulseCenter   = $null              # Set while the final ribbon pulse plays

function Recompute-Goal {
    $doneCount = 0.0
    foreach ($t in $Tasks) {
        if ($t.State -eq 'ok' -or $t.State -eq 'warn' -or $t.State -eq 'fail' -or $t.State -eq 'flash') {
            $doneCount += 1.0
        } elseif ($t.State -eq 'running') {
            $doneCount += 0.35
        }
    }
    $script:ProgressGoal = [Math]::Min(($doneCount / [double]$Tasks.Count), 1.0)
}

function Render-RibbonLine([int]$width, [double]$fill, [int]$tick) {
    $innerW = [Math]::Max($width - 4, 20)
    $filled = [int][Math]::Round($fill * $innerW)
    $shimmerCenter = ($tick * 1.8) % ($innerW + 14) - 7.0
    $pulse = $script:PulseCenter

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("  ")
    [void]$sb.Append((Paint (Get-GradRGB 0.0) $G_Diamond)).Append(" ")

    for ($i = 0; $i -lt $innerW; $i++) {
        $pos = $i / [double]([Math]::Max($innerW - 1, 1))
        if ($i -lt $filled) {
            $rgb = Get-GradRGB $pos
            if ($null -ne $pulse) {
                $d = [Math]::Abs($i - [double]$pulse)
                if ($d -lt 9.0) {
                    $rgb = Mix-RGB $rgb $C_Bright ((1.0 - ($d / 9.0)) * 0.92)
                }
            } else {
                $d = [Math]::Abs($i - $shimmerCenter)
                if ($d -lt 4.5) {
                    $boost = (1.0 - ($d / 4.5)) * 0.72
                    $rgb = Mix-RGB $rgb $C_Bright $boost
                }
            }
            [void]$sb.Append((FgSeq $rgb) + $G_RibbonHi)
        } elseif ($i -eq $filled -and $filled -lt $innerW -and $fill -gt 0.01) {
            [void]$sb.Append((Paint $C_Bright $G_Sparkle))
        } else {
            [void]$sb.Append((FgSeq $C_Dark) + $G_RibbonLo)
        }
    }
    $endColor = $C_Dark
    if ($fill -ge 0.99) { $endColor = Get-GradRGB 1.0 }
    [void]$sb.Append("$ESC[0m ").Append((Paint $endColor $G_Diamond))
    return $sb.ToString()
}

function Render-TaskRow($task, [int]$width, [int]$tick) {
    # Layout: "   G  label(25)  detail(right-aligned)  time(7)"
    $state = $task.State
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
            $glyph = $SpinFrames[$tick % $SpinFrames.Count]
            $wave  = ([Math]::Sin($tick * 0.25) + 1.0) * 0.5
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
    $detailStr  = (Fit-Text $detailText $detailW).PadLeft($detailW)
    $timeStr    = ([string]$timeText).PadLeft($timeW)

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("   ")
    [void]$sb.Append((Paint $gCol $glyph))
    [void]$sb.Append("  ")
    [void]$sb.Append((Paint $lCol $labelStr))
    [void]$sb.Append("  ")

    if ($state -eq 'pending' -and [string]::IsNullOrEmpty($detailText)) {
        $dots = ($G_Dot * 3).PadLeft($detailW)
        [void]$sb.Append((Paint $C_Dark $dots))
    } else {
        [void]$sb.Append((Paint $dCol $detailStr))
    }
    [void]$sb.Append("  ")
    [void]$sb.Append((Paint $tCol $timeStr))
    return $sb.ToString()
}

function Draw-Board {
    if (-not $script:UseVT) { return }
    $bw = Get-BoardWidth
    Recompute-Goal
    $diff = $script:ProgressGoal - $script:ProgressFill
    if ([Math]::Abs($diff) -lt 0.005) {
        $script:ProgressFill = $script:ProgressGoal
    } else {
        $script:ProgressFill += $diff * 0.28
    }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append($script:SyncOn)
    if ($script:BoardActive) {
        [void]$sb.Append("$ESC[$($script:BoardLines)A`r")
    }
    [void]$sb.Append((Render-RibbonLine $bw $script:ProgressFill $script:AnimTick)).Append("$script:EL`n")
    [void]$sb.Append("$script:EL`n")
    foreach ($t in $Tasks) {
        [void]$sb.Append((Render-TaskRow $t $bw $script:AnimTick)).Append("$script:EL`n")
    }
    [void]$sb.Append($script:SyncOff)
    [Console]::Write($sb.ToString())
    $script:BoardActive = $true
    $script:AnimTick++
}

function Step-AnimFrames([int]$frames = 3, [int]$delayMs = 24) {
    if (-not $script:Anim) {
        $script:ProgressFill = $script:ProgressGoal
        Draw-Board
        return
    }
    for ($f = 0; $f -lt $frames; $f++) {
        Draw-Board
        Start-Sleep -Milliseconds $delayMs
    }
}

# One bright wave sweeping across the finished ribbon
function Play-RibbonPulse {
    if (-not $script:Anim) { return }
    $bw = Get-BoardWidth
    $innerW = [Math]::Max($bw - 4, 20)
    for ($c = -8; $c -le ($innerW + 8); $c += 3) {
        $script:PulseCenter = [double]$c
        Draw-Board
        Start-Sleep -Milliseconds 14
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
    if ($script:UseVT) {
        Step-AnimFrames -frames 2 -delayMs 18
    }
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
            Draw-Board
            Start-Sleep -Milliseconds 42
        }
        $t.State = $state
        Recompute-Goal
        Step-AnimFrames -frames 3 -delayMs 18
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
function Invoke-BgStep([string]$TaskId, [string]$Note, [string]$Exe, [string[]]$Arguments) {
    Update-TaskNote $TaskId $Note
    if (-not $script:UseVT) {
        Write-Host ("      ... " + $Note)
    }

    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -NoNewWindow -PassThru `
         -WorkingDirectory $PSScriptRoot `
         -RedirectStandardOutput $Log -RedirectStandardError "$Log.err"
    # Touch .Handle immediately so ExitCode is populated reliably in PS 5.1
    $null = $p.Handle

    while (-not $p.HasExited) {
        if ($script:UseVT) {
            Draw-Board
            Start-Sleep -Milliseconds 40
        } else {
            Start-Sleep -Milliseconds 100
        }
    }
    $p.WaitForExit()
    return ($p.ExitCode -eq 0)
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
    if ($text -match 'ConnectionError|Max retries exceeded|getaddrinfo|Temporary failure|ReadTimeout|ProxyError|timed out') {
        return "pip could not reach PyPI. Check your internet, VPN or proxy, then run again."
    }
    if ($text -match 'No space left|WinError 112|not enough space') {
        return "The disk is full. Free some space, then run again."
    }
    if ($text -match 'WinError 5\b|WinError 32|Access is denied|PermissionError') {
        return "A file is locked or protected. Close editors and terminals using this folder, pause antivirus, then run again."
    }
    if ($text -match 'Microsoft Visual C\+\+|Failed building wheel|cl\.exe') {
        return "A package must be compiled. Install Microsoft C++ Build Tools or use 64-bit Python 3.12, then run again."
    }
    if ($text -match 'No matching distribution|Could not find a version') {
        return "A required package was not found. Check the dependency names and versions in pyproject.toml."
    }
    if ($text -match 'ModuleNotFoundError|ImportError') {
        return "A module failed to import. Run run.bat -Repair for a clean reinstall."
    }
    return $Default
}

function Show-LogPanel([string]$Title) {
    $bw = Get-BoardWidth
    $inner = $bw - 4
    Write-Host ""
    Write-Host ("  " + (Paint $C_Red ($G_TL + ($G_HZ * ($bw - 2)) + $G_TR)))
    $hdr = Fit-Text ("Diagnostic Log: " + $Title) $inner
    Write-Host ("  " + (Paint $C_Red $G_VT) + " " + (Paint $C_White $hdr.PadRight($inner)) + " " + (Paint $C_Red $G_VT))
    Write-Host ("  " + (Paint $C_Red ($G_VT + ($G_HZ * ($bw - 2)) + $G_VT)))

    $lines = @()
    if (Test-Path $Log)       { $lines += @(Get-Content $Log -ErrorAction SilentlyContinue) }
    if (Test-Path "$Log.err") { $lines += @(Get-Content "$Log.err" -ErrorAction SilentlyContinue) }
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
    Write-Host ""
    if (-not $NoPause) { [void](Read-Host "  Press Enter to close") }
    exit 1
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
        if ($script:Anim) { Start-Sleep -Milliseconds 35 }
    }

    $botLine = "  " + (Paint-Gradient ($G_BL + ($G_HZ * $inner) + $G_BR) 0 $bw)
    Write-Host $botLine
}

# Waits for one key. Returns $true to open the dashboard, $false if Esc was pressed.
# Nothing is loaded after the key press: the dashboard is already pre-loaded.
function Wait-LaunchKey {
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
            $line = "  " + (Paint (Get-GradRGB $wave) $G_Arrow) + "  " + (Paint $C_White $label) +
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

# ---------------------------------------------------------------------------
#  Network, Python & Environment Helpers
# ---------------------------------------------------------------------------
$script:Online     = $null
$script:CleanTried = $false   # a from-scratch rebuild already happened in this run
$script:FailStage  = ''

function Test-Online {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $r = $c.BeginConnect("pypi.org", 443, $null, $null)
        $reached = $r.AsyncWaitHandle.WaitOne(4000, $false)
        $ok = ($reached -and $c.Connected)
        $c.Close()
        return $ok
    } catch {
        return $false
    }
}

function Assert-Online([string]$TaskId) {
    if ($null -eq $script:Online) { $script:Online = Test-Online }
    if (-not $script:Online) {
        Complete-Task $TaskId 'fail' 'Offline (download required)'
        Stop-WithPause "No internet connection. Something has to be installed or repaired." -Hint "Connect to the internet (or turn off VPN/proxy) and run this again."
    }
}

# A usable interpreter: 3.12+ with the venv and ensurepip modules present
function Test-Python([string]$exe) {
    if (-not $exe) { return $false }
    if (-not (Test-Path $exe)) { return $false }
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & $exe -c "import sys, venv, ensurepip; print(sys.version_info >= (3, 12))" 2>$null
    $ErrorActionPreference = $old
    return ("$out".Trim() -eq 'True')
}

function Find-Python {
    $candidates = @()

    $cmds = Get-Command python -All -ErrorAction SilentlyContinue
    foreach ($c in $cmds) {
        if ($c.Source -and ($c.Source -notlike '*\WindowsApps\*')) {
            $candidates += $c.Source
        }
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $e = & py -3 -c "import sys; print(sys.executable)" 2>$null
        $ErrorActionPreference = $old
        if ($e) {
            $eClean = "$e".Trim()
            if ($eClean -and ($eClean -notlike '*\WindowsApps\*')) {
                $candidates += $eClean
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

    foreach ($c in $candidates) {
        if (Test-Python $c) { return $c }
    }
    return $null
}

function Install-Python {
    Assert-Online 'py'

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $wingetArgs = @(
            "install", "--id", "Python.Python.3.12", "-e", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"
        )
        if (Invoke-BgStep 'py' "Installing via winget" "winget" $wingetArgs) {
            if (Find-Python) { return $true }
        }
    }

    $installer = Join-Path $env:TEMP ("python-" + $PythonVersion + "-amd64.exe")
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

    $dlOk = Invoke-BgStep 'py' "Downloading Python $PythonVersion" "curl.exe" `
        @("-L", "-f", "-s", "-o", ('"' + $installer + '"'), $url)
    if (-not $dlOk) { return $false }

    return (Invoke-BgStep 'py' "Installing Python $PythonVersion" $installer `
        @("/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=0", "Include_test=0"))
}

function Test-VenvPip {
    if (-not (Test-Path $VenvPy)) { return $false }
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $VenvPy -m pip --version *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $old
    return $ok
}

function Test-Imports([string]$Code = "import project_decision, textual") {
    if (-not (Test-Path $VenvPy)) { return $false }
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $VenvPy -c $Code *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $old
    return $ok
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
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
        if (-not (Test-Path $VenvDir)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    try { & cmd.exe /c "rmdir /s /q `"$VenvDir`"" 2>$null | Out-Null } catch { }
    return (-not (Test-Path $VenvDir))
}

function Install-Deps([string]$TaskId, [string]$Note, [bool]$Clean = $false) {
    $a = @("-m", "pip", "install", "--disable-pip-version-check", "--retries", "5", "--timeout", "30")
    if ($Clean) { $a += "--no-cache-dir" }
    $a += @("-e", ('"' + ".[msp]" + '"'))
    return (Invoke-BgStep $TaskId $Note $VenvPy $a)
}

function Save-DepsHash {
    try {
        $h = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $PSScriptRoot "pyproject.toml")).Hash
        Set-Content -Path (Join-Path $VenvDir ".pp-deps-hash") -Value $h -Encoding ASCII
    } catch { }
}

# Wipes .venv and rebuilds it from scratch, installing without any pip cache
function Rebuild-Environment([string]$TaskId, [string]$PyExe) {
    $script:FailStage  = ''
    $script:CleanTried = $true
    Assert-Online $TaskId   # never delete a working setup that cannot be re-created
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
#  Main Execution Flow
# ===========================================================================
try {
    Set-Cursor $false
    Set-Title "ProjectPilot"
    Show-Banner

    if ($script:UseVT) {
        Draw-Board
    } else {
        Write-Host ""
    }

    # -----------------------------------------------------------------------
    # 1. System Checks (64-bit OS, project files, write access, terminal, disk)
    # -----------------------------------------------------------------------
    Start-Task 'sys' 'Inspecting host'

    try {
        $osName = ((Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption) -replace '^Microsoft\s+', ''
        $osName = $osName.Trim()
    } catch {
        $osName = "Windows " + [Environment]::OSVersion.Version.ToString()
    }

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
    if ($script:InitW -lt 100 -or $script:InitH -lt 28) {
        $sysWarnings += ("Term " + $script:InitW.ToString($Inv) + "x" + $script:InitH.ToString($Inv))
    }

    $freeGBText = ""
    try {
        $drive  = New-Object System.IO.DriveInfo($PSScriptRoot.Substring(0, 1))
        $freeGB = [Math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        $freeGBText = $freeGB.ToString("0.0", $Inv) + " GB free"
        if ($freeGB -lt 1.0) {
            $sysWarnings += ("Low disk (" + $freeGBText + ")")
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

    # -----------------------------------------------------------------------
    # 2. Internet Reachability (TCP pypi.org:443)
    # -----------------------------------------------------------------------
    Start-Task 'net' 'pypi.org:443'
    $script:Online = Test-Online
    if ($script:Online) {
        Complete-Task 'net' 'ok' 'Connected'
    } else {
        Complete-Task 'net' 'warn' 'Offline (cached mode)'
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
        if ((-not $installedOk) -or (-not $py)) {
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
    Add-Summary "Python" $pyVerClean 'ok'

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
        Assert-Online 'venv'   # never delete a working setup that cannot be re-created
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
    $wantHash = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $PSScriptRoot "pyproject.toml")).Hash
    $haveHash = ""
    if (Test-Path $hashFile) {
        $haveHash = ("" + (Get-Content $hashFile -ErrorAction SilentlyContinue)).Trim()
    }
    $importsOk = Test-Imports

    if ($importsOk -and ($haveHash -eq $wantHash)) {
        Complete-Task 'deps' 'ok' 'Up to date'
        Add-Summary "Packages" "Up to date" 'ok'
    } else {
        $reasonNote = "Installing packages"
        if ($importsOk -and ($haveHash -ne $wantHash) -and ($haveHash -ne "")) {
            $reasonNote = "Syncing pyproject.toml"
        } elseif ((-not $importsOk) -and ($haveHash -ne "")) {
            $reasonNote = "Repairing packages"
        }
        Assert-Online 'deps'

        $depsStatus = "Installed just now"
        $depsOk = Install-Deps 'deps' $reasonNote $false
        if (-not $depsOk) {
            if (-not $script:CleanTried) {
                $depsOk = Rebuild-Environment 'deps' $py
                if ($depsOk) { $depsStatus = "Reinstalled from scratch" }
            } else {
                $script:FailStage = 'pip'
                $depsOk = Install-Deps 'deps' "Retrying without cache" $true
            }
        }
        if (-not $depsOk) { Stop-RebuildFailed 'deps' }

        Save-DepsHash
        Complete-Task 'deps' 'ok' 'Installed & locked'
        Add-Summary "Packages" $depsStatus 'ok'
    }

    # -----------------------------------------------------------------------
    # 6. Dashboard engine: import + pre-load now, so launching later is instant
    # -----------------------------------------------------------------------
    Start-Task 'app' 'Pre-loading dashboard'
    $warmArgs = @("-c", '"import project_decision.tui"')
    $warmOk   = Invoke-BgStep 'app' 'Pre-loading dashboard' $VenvPy $warmArgs
    $appStatus = "Pre-loaded"
    if ((-not $warmOk) -and (-not $script:CleanTried)) {
        if (-not (Rebuild-Environment 'app' $py)) { Stop-RebuildFailed 'app' }
        $warmOk = Invoke-BgStep 'app' 'Pre-loading dashboard' $VenvPy $warmArgs
        $appStatus = "Repaired & pre-loaded"
        foreach ($srow in $SummaryRows) {
            if ($srow[0] -eq "Packages") { $srow[1] = "Reinstalled from scratch"; $srow[2] = 'ok' }
        }
    }
    if (-not $warmOk) {
        Complete-Task 'app' 'fail' 'Dashboard failed to load'
        Stop-WithPause "The dashboard module could not be loaded." -WithLog -LogTitle "import project_decision.tui" -Hint "Run run.bat -Repair for a clean reinstall. If it keeps failing, the error above comes from the project code."
    }
    Complete-Task 'app' 'ok' $appStatus
    Add-Summary "Dashboard" $appStatus 'ok'

    # -----------------------------------------------------------------------
    # 7. Microsoft Project COM Integration (Notice only)
    # -----------------------------------------------------------------------
    Start-Task 'msp' 'Probing COM registry'
    if (Test-Path "Registry::HKEY_CLASSES_ROOT\MSProject.Application") {
        if (Get-Process -Name WINPROJ -ErrorAction SilentlyContinue) {
            Complete-Task 'msp' 'ok' 'Active (WINPROJ running)'
            Add-Summary "Microsoft Project" "Running" 'ok'
        } else {
            Complete-Task 'msp' 'ok' 'Installed (idle)'
            Add-Summary "Microsoft Project" "Installed (not open)" 'ok'
        }
    } else {
        Complete-Task 'msp' 'warn' 'Not detected (optional)'
        Add-Summary "Microsoft Project" "Not detected" 'warn'
    }

    # -----------------------------------------------------------------------
    # 8. TypeSafe JEV API Key Check (Notice only, never prints key)
    # -----------------------------------------------------------------------
    Start-Task 'jev' 'Checking credentials'
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
            if ($keyVal -ne '' -and $keyVal -ne 'your-key-here') {
                $hasKey = $true
            }
        }
    }
    if ($hasKey) {
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
    }

    # -----------------------------------------------------------------------
    # 9. Summary Card, one key press, straight into the dashboard
    # -----------------------------------------------------------------------
    Set-Title "ProjectPilot - Ready"
    $totalSecs = Fmt-Sec $Clock.Elapsed.TotalSeconds
    Show-SummaryCard $totalSecs

    if (-not $env:WT_SESSION) {
        Write-Host ("  " + (Paint $C_Dim ($G_Dot + "  Tip: Windows Terminal gives the best visuals (smooth spinner, sharper colors).")))
    }
    Write-Host ""

    $launch = Wait-LaunchKey
    if ($launch) {
        Set-Title "ProjectPilot"
        Set-Cursor $true
        $session = [System.Diagnostics.Stopwatch]::StartNew()
        & $VenvPy -m project_decision.tui
        $tuiExit = $LASTEXITCODE
        $session.Stop()
        if ($tuiExit -ne 0) {
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
} finally {
    if ($script:UseVT) {
        [Console]::Write("$script:SyncOff$ESC[0m")
    }
    Set-Cursor $true
    if ($OrigTitle) { Set-Title $OrigTitle }
}
