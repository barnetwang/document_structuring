# Windows MSYS Path Workaround for doc-str

## Problem
On Windows running bash (MSYS/git-bash), invoking the `doc-str` CLI through `terminal()` can corrupt file paths. Example failure:
```bash
doc-str list --output /tmp/doc_list.json
# Error: can't open file 'C:\\\\c\\\\Users\\\\HOME\\\\...': No such file or directory
```

The MSYS path translation layer converts `/c/Users/...` into double-escaped forms (`C:\\\\c\\\\Users\\\\...`), causing "file not found" errors even when the file exists.

## Reliable Fix: Use `execute_code` with explicit paths

Always use the Python tool to invoke doc-str on Windows:

```python
import subprocess, json, os

# Hard-coded absolute paths (no MSYS translation)
doc_str_exe = r"C:\Users\HOME\AppData\Local\hermes\hermes-agent\venv\Scripts\doc-str.exe"

result = subprocess.run(
    [doc_str_exe, "list", "--output", r"C:\Users\HOME\doc_list.json"],
    capture_output=True, text=True, cwd=r"C:\Users\HOME"  # workspace root (where documents.db lives)
)
print(result.stdout[:2000])
```

Key points:
- Use raw strings (`r"..."`) for all Windows paths — no backslash escaping issues.
- Invoke the venv `doc-str.exe` binary explicitly rather than relying on PATH resolution.
  (Alternative: `[python_exe, "-m", "doc_structuring.cli", ...]` if the exe is missing.)
- Set `cwd` to the workspace root (where `documents.db` lives), or pass `--base-dir`.

## Quick sanity check before assuming a path problem
```bash
which doc-str   # should resolve inside hermes-agent\venv\Scripts
doc-str --help  # if this works in terminal(), plain bash invocation is fine for that session
```
