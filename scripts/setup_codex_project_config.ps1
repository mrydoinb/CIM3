$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$codexDir = Join-Path $root ".codex"
$rulesDir = Join-Path $codexDir "rules"

New-Item -ItemType Directory -Force -Path $rulesDir | Out-Null

@'
# Project-local Codex settings for fast CIM road iteration.
# Project-local config is loaded only when this repository is trusted by Codex.

model_reasoning_effort = "medium"
model_verbosity = "medium"

approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "cached"
project_doc_max_bytes = 65536

[sandbox_workspace_write]
network_access = false

# Fast mode is useful while repeatedly tuning Blender-visible geometry, but it
# consumes credits at a higher rate. Uncomment only when you want that tradeoff.
# service_tier = "fast"
# [features]
# fast_mode = true
'@ | Set-Content -LiteralPath (Join-Path $codexDir "config.toml") -Encoding ASCII

@'
# Allow common read-only discovery commands.
prefix_rule(
    pattern = ["rg"],
    decision = "allow",
    justification = "Fast repository search is part of normal CIM road debugging.",
)

prefix_rule(
    pattern = ["git", "status"],
    decision = "allow",
    justification = "Checking worktree state is safe and needed before edits.",
)

prefix_rule(
    pattern = ["git", "diff"],
    decision = "allow",
    justification = "Inspecting diffs is safe and needed before summaries.",
)

prefix_rule(
    pattern = ["D:\\ProgramData\\miniconda3\\envs\\cim-road\\python.exe", "-m", "py_compile"],
    decision = "allow",
    justification = "Lightweight Python syntax checks are the default verification step.",
)

prefix_rule(
    pattern = ["D:\\ProgramData\\miniconda3\\envs\\cim-road\\python.exe", "scripts\\02_generate_cim_roads.py"],
    decision = "allow",
    justification = "Generate CIM road OBJ for visual iteration.",
)

prefix_rule(
    pattern = ["D:\\ProgramData\\miniconda3\\envs\\cim-road\\python.exe", "scripts\\03_export_cim_roads_fbx.py"],
    decision = "allow",
    justification = "Export CIM road FBX after OBJ generation.",
)

prefix_rule(
    pattern = ["git", "reset", "--hard"],
    decision = "forbidden",
    justification = "Do not hard-reset the project. Use targeted restore only when the user explicitly requests it.",
)

prefix_rule(
    pattern = ["Remove-Item", "-Recurse"],
    decision = "prompt",
    justification = "Recursive deletion must be reviewed before running.",
)
'@ | Set-Content -LiteralPath (Join-Path $rulesDir "default.rules") -Encoding ASCII

Write-Host "Wrote Codex project config:"
Write-Host " - $codexDir\config.toml"
Write-Host " - $rulesDir\default.rules"
