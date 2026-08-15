# Fallback PDF Reading When doc-str Is Unavailable

## Problem
When `read_file` cannot open a PDF (binary file) and `vision_analyze` also fails, you need an alternative to at least preview the content before deciding whether to parse it with doc-str.

## Quick Preview via pdftotext

Many Linux environments ship `pdftotext` as part of `poppler-utils`:
```bash
# Check if available
which pdftotext

# Extract and preview first 200 lines
pdftotext "/path/to/file.pdf" - | head -200
```

This gives a text dump you can scan to verify the document is what the user expects before investing time in full doc-str parsing. For short documents (< ~30 pages), this may be sufficient as a one-off read without needing to index it.

## When pdftotext Fails
If `pdftotext` also errors (e.g., MSYS path mangling on Windows), fall back to `execute_code`:
```python
import subprocess
result = subprocess.run(
    [r"C:\path\to\pdftotext.exe", r"C:\Users\HOME\file.pdf", "-"],
    capture_output=True, text=True
)
print(result.stdout[:5000])  # first 5KB for preview
```

## Workflow Decision Tree
1. Try `read_file` → fails (binary).
2. Try `pdftotext` via `terminal()` for a quick preview.
3. If full structured access is needed, use `doc-str parse` via `execute_code`.
4. For one-off reads of short docs, the pdftotext output may be enough without indexing.
