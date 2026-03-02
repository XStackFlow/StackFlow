"""Utilities for processing and filtering GitHub Action and system logs."""

import re
from typing import Optional, List

def filter_error_logs(logs: str, context_lines: int = 0) -> Optional[str]:
    """Filters log content to only include error lines and their surrounding context.
    
    Keywords: error, fail, fatal, exception, ##[error], failed:
    Also cleans up common GitHub Action log prefixes for readability.
    
    Args:
        logs: The raw log string.
        context_lines: Number of lines to include before/after detected errors.
        
    Returns:
        A string containing only the error-related lines with context, or None if none found.
    """
    if not logs:
        return None
        
    error_keywords = ["error", "fail", "fatal", "exception", "##[error]", "failed:", "panic:"]
    lines = logs.splitlines()
    
    # helper to clean a single line for output, keeping it concise but informative
    def clean_log_line(line: str) -> str:
        if "\t" in line:
            parts = line.split("\t")
            # If the middle part (often level) contains a keyword, include it
            if len(parts) > 2 and any(kw in parts[1].lower() for kw in error_keywords):
                return f"{parts[1]}: {parts[-1]}".strip()
            return parts[-1].strip()
        
        timestamp_match = re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s+', line)
        if timestamp_match:
            return line[timestamp_match.end():].strip()
        return line.strip()

    error_indices = set()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line_lower = raw_line.lower() # Check against raw line to catch LEVEL in tabs
        
        if any(kw in line_lower for kw in error_keywords):
            # 1. Add normal context window
            for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
                error_indices.add(j)
                
            # 2. Smart Traceback/Panic Detection
            # Once we see a panic, traceback, or goroutine, consume everything until a new log entry
            is_start_of_trace = any(marker in line_lower for marker in ["panic:", "traceback", "goroutine"])
            if is_start_of_trace:
                for j in range(i + 1, min(len(lines), i + 100)):
                    next_raw = lines[j]
                    next_clean = clean_log_line(next_raw)
                    
                    if not next_clean:
                        error_indices.add(j)
                        continue
                    
                    # New log markers:
                    # - Starts with YYYY-MM-DD
                    # - Starts with {"level": or {"time":
                    if re.match(r'^(?:\d{4}-\d{2}-\d{2}|{"(?:level|time)":)', next_raw) or \
                       re.match(r'^(?:\d{4}-\d{2}-\d{2}|{"(?:level|time)":)', next_clean):
                        break
                        
                    error_indices.add(j)
        i += 1
    
    if not error_indices:
        return None

    # Group consecutive indices into blocks with separators
    sorted_indices = sorted(list(error_indices))
    result_blocks = []
    current_block = []
    
    for idx_in_sorted, idx_in_lines in enumerate(sorted_indices):
        if idx_in_sorted > 0 and idx_in_lines != sorted_indices[idx_in_sorted-1] + 1:
            result_blocks.append("\n".join(current_block))
            current_block = [clean_log_line(lines[idx_in_lines])]
        else:
            current_block.append(clean_log_line(lines[idx_in_lines]))
            
    if current_block:
        result_blocks.append("\n".join(current_block))
    
    return "\n---\n".join(result_blocks)
