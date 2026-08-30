# Plasmid Designer 冒烟测试脚本（对应 docs/FIXPLAN.md 底部验证清单）
# 用法：
#   1) 先启动后端：cd src\backend ; uvicorn app.main:app
#   2) 新开终端运行：powershell -ExecutionPolicy Bypass -File smoke_test.ps1
# 可选参数：-BaseUrl http://127.0.0.1:8000

param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$script:pass = 0
$script:fail = 0

function Add-Result([string]$name, [bool]$ok, [string]$detail = "") {
    if ($ok) { $script:pass++ } else { $script:fail++ }
    $mark = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host ("[{0}] {1} {2}" -f $mark, $name, $(if ($detail) { "- $detail" } else { "" })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}

function Post-Json([string]$path, $body, $headers = @{}) {
    return Invoke-RestMethod -Method Post -Uri "$BaseUrl$path" `
        -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 6) -Headers $headers
}

function Get-Json([string]$path, $headers = @{}) {
    return Invoke-RestMethod -Method Get -Uri "$BaseUrl$path" -Headers $headers
}

Write-Host "`n=== Plasmid Designer 冒烟测试 → $BaseUrl ===`n"

# ---------- 0. 健康检查 ----------
try {
    $h = Get-Json "/health"
    Add-Result "健康检查 /health" ($h.status -eq "healthy") "storage_mode=$($h.storage_mode)"
} catch { Add-Result "健康检查 /health" $false $_.Exception.Message }

# ---------- 1. 单任务设计主流程 ----------
$protein = "MKVLWAALLVTFLAGCDDAKRVRELTYNGSLAW"
$designId = $null
try {
    $resp = Post-Json "/api/design" @{
        sequence        = $protein
        sequence_name   = "smoke_apoA1"
        sequence_type   = "amino_acid"
        vector_id       = "pET-28a"
        cloning_method  = "gibson"
        optimize_codons = $true
        target_species  = "ecoli"
        protocol_language = "zh"
    }
    $designId = $resp.design_id
    Add-Result "提交设计任务" ($null -ne $designId) "id=$designId"
} catch { Add-Result "提交设计任务" $false $_.Exception.Message }

if ($designId) {
    $status = ""
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Get-Json "/api/design/$designId"
            $status = $r.status
            if ($status -in @("completed", "failed")) { break }
        } catch { }
    }
    Add-Result "设计任务完成轮询" ($status -eq "completed") "status=$status"

    if ($status -eq "completed") {
        # GenBank 下载
        try {
            $gb = Invoke-RestMethod -Uri "$BaseUrl/api/design/$designId/download/genbank"
            $text = if ($gb -is [string]) { $gb } else { [System.Text.Encoding]::UTF8.GetString($gb) }
            Add-Result "GenBank 下载" ($text -match "^LOCUS") "长度 $($text.Length) 字符"
        } catch { Add-Result "GenBank 下载" $false $_.Exception.Message }

        # 引物 TSV 下载
        try {
            $tsv = Invoke-RestMethod -Uri "$BaseUrl/api/design/$designId/download/primers"
            $t = if ($tsv -is [string]) { $tsv } else { [System.Text.Encoding]::UTF8.GetString($tsv) }
            Add-Result "引物 TSV 下载" ($t -match "^Name`tSequence") ($t.Split("`n")[1])
        } catch { Add-Result "引物 TSV 下载" $false $_.Exception.Message }

        # 质粒图谱
        try {
            $map = Get-Json "/api/design/$designId/map"
            Add-Result "质粒图谱数据" ($map.length -gt 0) "length=$($map.length)"
        } catch { Add-Result "质粒图谱数据" $false $_.Exception.Message }

        # 设计结果多格式导出（原 501 桩）
        foreach ($fmt in @("fasta", "genbank")) {
            try {
                $exp = Invoke-RestMethod -Uri "$BaseUrl/api/analysis/design/$designId/export`?format=$fmt"
                $e = if ($exp -is [string]) { $exp } else { [System.Text.Encoding]::UTF8.GetString($exp) }
                Add-Result "设计导出 format=$fmt" ($e.Length -gt 10) "长度 $($e.Length)"
            } catch { Add-Result "设计导出 format=$fmt" $false $_.Exception.Message }
        }
    }
}

# ---------- 2. 序列分析四端点（本次修复重点） ----------
$dna = "ATGGGATCCAACTTGAAGCTTCCCGGTACCTTAAGATCTGACTAGC"

try {
    $rs = Post-Json "/api/analysis/restriction-sites" @{ sequence = $dna; enzymes = $null }
    Add-Result "限制性酶切位点(body)" ($null -ne $rs.total) "发现 $($rs.total) 个位点"
} catch { Add-Result "限制性酶切位点(body)" $false $_.Exception.Message }

try {
    $orfSeq = "ATGAAAGCATTTTTTTAATAA" + "A" * 40
    $orfs = Post-Json "/api/analysis/orfs" @{ sequence = $dna + $orfSeq; min_length = 30 }
    Add-Result "ORF 预测(body)" ($null -ne $orfs.total) "发现 $($orfs.total) 个 ORF"
} catch { Add-Result "ORF 预测(body)" $false $_.Exception.Message }

try {
    $gc = Post-Json "/api/analysis/gc-analysis" @{ sequence = $dna; window_size = 20; step_size = 10 }
    Add-Result "GC 分析(body)" ($null -ne $gc.total_gc_content) "GC=$($gc.total_gc_content)%"
} catch { Add-Result "GC 分析(body)" $false $_.Exception.Message }

try {
    $comp = Post-Json "/api/analysis/compatibility" @{
        insert_sequence = "GGATCCAAAAAA"
        vector_sequence = "GAATTCCCCCCCCGGATCC"
        enzymes         = @("EcoRI", "BamHI")
    }
    Add-Result "克隆兼容性检查(body)" $true ($comp | ConvertTo-Json -Depth 2 -Compress).Substring(0, 80)
} catch { Add-Result "克隆兼容性检查(body)" $false $_.Exception.Message }

# ---------- 3. 载体相关 ----------
try {
    $vecs = Get-Json "/api/vectors"
    Add-Result "载体列表" ($vecs.Count -ge 9) "共 $($vecs.Count) 个载体"
} catch { Add-Result "载体列表" $false $_.Exception.Message }

try {
    $vexp = Invoke-RestMethod -Uri "$BaseUrl/api/analysis/vector/pET-28a/export?format=genbank"
    $v = if ($vexp -is [string]) { $vexp } else { [System.Text.Encoding]::UTF8.GetString($vexp) }
    Add-Result "载体导出(原501桩)" ($v.Length -gt 10) "长度 $($v.Length)"
} catch { Add-Result "载体导出(原501桩)" $false $_.Exception.Message }

# ---------- 4. 认证流程 ----------
$email = "smoke_$( [guid]::NewGuid().ToString('N').Substring(0,8) )@example.com"
$token = $null
try {
    $reg = Post-Json "/api/auth/register" @{
        email            = $email
        username         = "smoke"
        password         = "Passw0rd!123"
        confirm_password = "Passw0rd!123"
    }
    $token = $reg.access_token
    Add-Result "用户注册" ($null -ne $token)
} catch { Add-Result "用户注册" $false $_.Exception.Message }

if ($token) {
    try {
        $me = Get-Json "/api/auth/me" @{ Authorization = "Bearer $token" }
        Add-Result "获取当前用户 /me" ($me.email -eq $email) $me.email
    } catch { Add-Result "获取当前用户 /me" $false $_.Exception.Message }
}

try {
    $login = Post-Json "/api/auth/login" @{ email = $email; password = "Passw0rd!123" }
    Add-Result "用户登录" ($null -ne $login.access_token)
} catch { Add-Result "用户登录" $false $_.Exception.Message }

# ---------- 5. 批量设计 ----------
try {
    $b = Post-Json "/api/design/batch" @{
        sequences       = @("MKVLWALLLLL", "MSTGSKSDFWEK")
        sequence_names  = @("seq_a", "seq_b")
        sequence_type   = "amino_acid"
        vector_id       = "pET-28a"
        cloning_method  = "gibson"
        optimize_codons = $true
        target_species  = "ecoli"
    }
    $batchId = $b.batch_id
    Add-Result "提交批量任务" ($null -ne $batchId) "id=$batchId"

    $bs = ""
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        $p = Get-Json "/api/design/batch/$batchId"
        $bs = $p.status
        if ($bs -eq "completed") { break }
    }
    Add-Result "批量任务完成" ($bs -eq "completed") "completed=$($p.completed) failed=$($p.failed)"

    if ($bs -eq "completed") {
        $zipFile = Join-Path (Get-Location) "$batchId`_results.zip"
        Invoke-WebRequest -Uri "$BaseUrl/api/design/batch/$batchId/download" -OutFile $zipFile | Out-Null
        $size = (Get-Item $zipFile).Length
        Add-Result "批量 ZIP 下载" ($size -gt 100) "$zipFile ($size bytes)"
    }
} catch { Add-Result "批量设计流程" $false $_.Exception.Message }

# ---------- 汇总 ----------
Write-Host "`n=== 结果：$script:pass 通过 / $script:fail 失败 ===" -ForegroundColor $(if ($script:fail -eq 0) { "Green" } else { "Yellow" })
if ($script:fail -gt 0) { exit 1 }
