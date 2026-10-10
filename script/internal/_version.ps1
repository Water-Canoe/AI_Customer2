# 与 app/semver.py 的发行规则对应；状态管理无需启动 Python 或读取私钥。
function Assert-SemVer([string]$Value, [string]$Label) {
    $Number = '(?:0|[1-9][0-9]*)'
    $Identifier = '(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
    $Pattern = "\A$Number\.$Number\.$Number(?:-$Identifier(?:\.$Identifier)*)?\z"
    if ($Value -cnotmatch $Pattern) {
        throw "$Label must use X.Y.Z or X.Y.Z-prerelease without leading zeroes or build metadata, for example 1.2.0-beta.2"
    }
}
