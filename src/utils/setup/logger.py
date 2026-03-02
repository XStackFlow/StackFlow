"""Logging utility with colored console output and file logging."""

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import contextlib
from contextvars import ContextVar
import json

# Context for current namespace (used to distinguish parallel logs)
NAMESPACE_CONTEXT: ContextVar[Optional[str]] = ContextVar("namespace_context", default=None)
# Context for current graph execution thread ID
THREAD_ID_CONTEXT: ContextVar[Optional[str]] = ContextVar("thread_id_context", default=None)

@contextlib.contextmanager
def thread_id_scope(thread_id: str):
    """Context manager to safe set and reset the logging thread ID context."""
    token = THREAD_ID_CONTEXT.set(thread_id)
    try:
        yield
    finally:
        THREAD_ID_CONTEXT.reset(token)

@contextlib.contextmanager
def namespace_scope(namespace: str):
    """Context manager to safe set and reset the logging namespace context."""
    token = NAMESPACE_CONTEXT.set(namespace)
    try:
        yield
    finally:
        NAMESPACE_CONTEXT.reset(token)

# Centralized log buffer: thread_id -> List of log strings
GLOBAL_LOG_BUFFER = {}

# Try to use colorlog for colored output, fallback to basic logging
try:
    import colorlog
    COLORLOG_AVAILABLE = True
except ImportError:
    COLORLOG_AVAILABLE = False


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds ANSI color codes for console output."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',  # Ma
        'RESET': '\033[0m',      # Reset
    }
    
    # Node name colors (for rotating colors based on logger name)
    NODE_COLORS = [
        '\033[94m',   # Bright Blue
        '\033[95m',   # Bright Ma
        '\033[96m',   # Bright Cyan
        '\033[92m',   # Bright Green
        '\033[93m',   # Bright Yellow
        '\033[91m',   # Bright Red
    ]
    
    # Namespace specific colors
    NAMESPACE_COLORS = {
        'access': '\033[38;5;208m',     # Orange
        'infra': '\033[95m',            # Bright Ma
        'go_segment': '\033[96m',       # Bright Cyan
        'DEFAULT': '\033[94m',          # Bright Blue
    }
    
    # Cache for node name to color mapping
    _node_color_map = {}
    _color_index = 0
    
    @classmethod
    def _get_node_color(cls, logger_name: str) -> str:
        """Get a consistent color for a node based on its logger name."""
        if logger_name not in cls._node_color_map:
            cls._node_color_map[logger_name] = cls.NODE_COLORS[cls._color_index % len(cls.NODE_COLORS)]
            cls._color_index += 1
        return cls._node_color_map[logger_name]
    
    def format(self, record):
        """Format log record with colors."""
        # Create a copy of the record to avoid modifying the original
        record_copy = logging.makeLogRecord(record.__dict__)
        
        # Add color to levelname
        if record_copy.levelname in self.COLORS:
            record_copy.levelname = (
                f"{self.COLORS[record_copy.levelname]}"
                f"{record_copy.levelname}"
                f"{self.COLORS['RESET']}"
            )
        
        # Add color to logger name (node name)
        if record_copy.name:
            node_color = self._get_node_color(record_copy.name)
            record_copy.name = (
                f"{node_color}"
                f"{record_copy.name}"
                f"{self.COLORS['RESET']}"
            )
            
        # Add Namespace Context prefix if present
        namespace_val = NAMESPACE_CONTEXT.get()
        if namespace_val:
            namespace_color = self.NAMESPACE_COLORS.get(namespace_val, self.NAMESPACE_COLORS['DEFAULT'])
            prefix = f"{namespace_color}[{namespace_val.upper()}]{self.COLORS['RESET']} "
            record_copy.msg = f"{prefix}{record_copy.msg}"
        
        return super().format(record_copy)


class PlainFormatter(logging.Formatter):
    """Formatter that strips ANSI escape sequences from log output."""
    
    # ANSI escape sequence pattern
    ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    
    def format(self, record):
        """Format log record and strip ANSI codes."""
        # Format the record first
        formatted = super().format(record)
        # Strip ANSI escape sequences
        return self.ANSI_ESCAPE.sub('', formatted)


class ConditionalStreamHandler(logging.StreamHandler):
    """Handler that only emits to console if THREAD_ID_CONTEXT is NOT set."""
    def emit(self, record):
        if THREAD_ID_CONTEXT.get() is None:
            super().emit(record)

class BufferHandler(logging.Handler):
    """Handler that captures logs into a global buffer (for UI) and persists them to session-specific files."""
    
    def emit(self, record):
        thread_id = THREAD_ID_CONTEXT.get()
        if not thread_id:
            return
            
        try:
            msg = self.format(record)
            
            # 1. Update in-memory buffer for real-time UI updates
            if thread_id not in GLOBAL_LOG_BUFFER:
                GLOBAL_LOG_BUFFER[thread_id] = []
            GLOBAL_LOG_BUFFER[thread_id].append(msg)
            
            # 2. Persist to session-specific log file (PLAIN TEXT)
            from src.utils.setup.const import SESSION_LOGS_DIR
            
            log_file = SESSION_LOGS_DIR / f"{thread_id}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)

            # We append plain text for the session file as requested
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
                
        except Exception:
            self.handleError(record)


def get_thread_logs(thread_id: str):
    """Retrieve and clear transient logs for a specific thread (for real-time updates)."""
    logs = GLOBAL_LOG_BUFFER.pop(thread_id, [])
    return logs


def get_persistent_session_logs(thread_id: str, limit: int = 100):
    """Retrieve the last 'limit' persistent logs from the session file."""
    from src.utils.setup.const import SESSION_LOGS_DIR
    from collections import deque
    import os
    
    log_file = SESSION_LOGS_DIR / f"{thread_id}.log"
    if not log_file.exists():
        return []
        
    line_count = 0
    file_size = log_file.stat().st_size
    logs_deque = deque(maxlen=limit)
    
    with open(log_file, "r", encoding="utf-8") as f:
        if file_size > 512 * 1024:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 512 * 1024))
            f.readline()
            
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Session logs are now plain text, so we just append them directly
            logs_deque.append(line)
                
    return list(logs_deque)


def _cleanup_old_logs(logs_dir: Path, keep_count: int = 5) -> None:
    """Remove old log files, keeping only the most recent ones.
    
    Args:
        logs_dir: Directory containing log files
        keep_count: Number of most recent log files to keep (default: 5)
    """
    try:
        # Get all log files matching the pattern langgraph_*.log
        log_files = list(logs_dir.glob("langgraph_*.log"))
        
        if len(log_files) <= keep_count:
            return
        
        # Sort by modification time (most recent first)
        log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        
        # Remove old log files (keep only the most recent keep_count)
        for old_log in log_files[keep_count:]:
            try:
                old_log.unlink()
            except OSError as e:
                # Silently continue - don't fail logging setup if cleanup fails
                pass
    except Exception as e:
        # Don't fail logging setup if cleanup fails - silently continue
        pass


def setup_logging(
    log_file: Optional[str] = None,
    log_level: int = logging.INFO,
    use_color: bool = True,
) -> None:
    """Setup root logger with console (conditional), central file, and buffer handlers."""
    project_root = Path(__file__).parent.parent.parent.parent
    
    if log_file is None:
        logs_dir = project_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = str(logs_dir / f"langgraph_{timestamp}.log")
        _cleanup_old_logs(logs_dir, keep_count=4)
    
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    
    # 1. CENTRAL FILE HANDLER (Unconditional)
    # plain format, no colors, strip ANSI escape sequences
    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setLevel(log_level)
    file_formatter = PlainFormatter(
        '[%(asctime)s] %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %I:%M:%S %p'
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    # 2. CONDITIONAL CONSOLE HANDLER
    # Only logs to terminal if NOT in a thread context (thread_id is None)
    console_handler = ConditionalStreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    if use_color:
        console_formatter = ColoredFormatter(
            '[%(asctime)s] %(levelname)s - %(name)s - %(message)s',
            datefmt='%I:%M:%S %p'
        )
        console_handler.setFormatter(console_formatter)
    else:
        console_handler.setFormatter(file_formatter)
    root_logger.addHandler(console_handler)
    
    # 3. BUFFER HANDLER (UI + Session Files)
    buffer_handler = BufferHandler()
    buffer_handler.setLevel(log_level)
    buffer_formatter = PlainFormatter(
        '[%(asctime)s] %(message)s',
        datefmt='%I:%M:%S %p'
    )
    buffer_handler.setFormatter(buffer_formatter)
    root_logger.addHandler(buffer_handler)
    
    # Suppress noisy OpenTelemetry exporter errors
    logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
    
    # Ensure Uvicorn and FastAPI logs propagate to the root logger so they are recorded in the file
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"]:
        l = logging.getLogger(logger_name)
        l.handlers = [] # Clear default handlers
        l.propagate = True # Force propagation to root
        l.setLevel(log_level)
    
    # Filter out noisy API endpoints from uvicorn.access logs
    from src.utils.setup.const import BLACKLISTED_API_LOGGING
    
    class APIBlacklistFilter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return not any(path in msg for path in BLACKLISTED_API_LOGGING)
    
    logging.getLogger("uvicorn.access").addFilter(APIBlacklistFilter())
    
    root_logger.info("Logging initialized - Central File: %s", log_file)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance.
    
    Args:
        name: Logger name (typically __name__). If None, returns root logger.
        
    Returns:
        Logger instance configured with console and file handlers.
    """
    # Setup logging if not already configured
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        setup_logging()
    
    return logging.getLogger(name)


def log_node_start(node_name: str, action: str = "Starting") -> None:
    """Log a divider block indicating a node is starting.
    
    Args:
        node_name: Name of the node (e.g., "RepoPreparer")
        action: Action description (default: "Starting")
    """
    logger = get_logger(__name__)
    logger.info("=" * 80)
    logger.info("%s: %s", node_name, action)
