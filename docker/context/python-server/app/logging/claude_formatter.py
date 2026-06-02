"""
Claude Code 风格的日志格式化器
提供类似 Claude Code 的视觉效果和输出格式
"""
import json
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum


class LogLevel(Enum):
    """日志级别"""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    DEBUG = "debug"


class StatusType(Enum):
    """状态类型"""
    STARTING = "starting"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ClaudeCodeFormatter:
    """Claude Code 风格的格式化器 - 纯文本版本，专为流式日志设计"""

    # 状态符号
    ICONS = {
        StatusType.STARTING: "[>]",
        StatusType.IN_PROGRESS: "[...]",
        StatusType.COMPLETED: "[OK]",
        StatusType.FAILED: "[ERR]",
        StatusType.INTERRUPTED: "[INT]"
    }

    # 日志级别符号
    LOG_STYLES = {
        LogLevel.INFO: {"icon": "[INFO]"},
        LogLevel.SUCCESS: {"icon": "[OK]"},
        LogLevel.WARNING: {"icon": "[WARN]"},
        LogLevel.ERROR: {"icon": "[ERR]"},
        LogLevel.DEBUG: {"icon": "[DBG]"}
    }

    def __init__(self, show_timestamps: bool = True):
        """
        Args:
            show_timestamps: 是否显示时间戳
        """
        self.show_timestamps = show_timestamps

    def _get_timestamp(self) -> str:
        """获取格式化的时间戳"""
        if not self.show_timestamps:
            return ""
        now = datetime.now()
        return f"[{now.strftime('%H:%M:%S.%f')[:-3]}]"

    def _create_separator(self, char: str = "─", width: int = 80) -> str:
        """创建分割线"""
        line = char * min(width, 120)  # 限制最大宽度
        return line

    def _format_json_data(self, data: Any, max_width: int = 100) -> str:
        """格式化 JSON 数据 - 保留换行"""
        try:
            if isinstance(data, (dict, list)):
                json_str = json.dumps(data, ensure_ascii=False, indent=2)
                # 保留原始格式，不强制截断
                return json_str
            else:
                data_str = str(data)
                # 保留多行字符串的换行，只在每行过长时才截断
                lines = data_str.split('\n')
                formatted_lines = []
                for line in lines:
                    if len(line) > max_width:
                        formatted_lines.append(line[:max_width] + "...")
                    else:
                        formatted_lines.append(line)
                return '\n'.join(formatted_lines)
        except Exception:
            return str(data)

    def format_api_call_start(self,
                             component: str,
                             function: str,
                             trace_id: str,
                             input_data: Optional[Dict] = None) -> str:
        """格式化 API 调用开始"""
        timestamp = self._get_timestamp()

        # 标题行
        title = f"{self.ICONS[StatusType.STARTING]} {component}.{function}"
        title_line = f"{timestamp} {title}"

        # 跟踪 ID
        trace_line = f"   trace_id: {trace_id}"

        lines = [title_line, trace_line]

        # 输入数据（如果有）
        if input_data:
            input_header = "   input:"
            input_json = self._format_json_data(input_data)

            # 处理多行输入，每行都添加适当的缩进
            if '\n' in input_json:
                lines.append(input_header)
                for line in input_json.split('\n'):
                    lines.append(f"   {line}")
            else:
                lines.extend([input_header, f"   {input_json}"])

        return "\n".join(lines)

    def format_api_call_end(self,
                           component: str,
                           function: str,
                           trace_id: str,
                           status: StatusType,
                           output_data: Optional[Any] = None,
                           duration_ms: Optional[float] = None,
                           error: Optional[str] = None) -> str:
        """格式化 API 调用结束"""
        timestamp = self._get_timestamp()

        # 状态符号
        icon = self.ICONS[status]

        # 标题行
        title = f"{icon} {component}.{function}"
        if duration_ms is not None:
            title += f" ({duration_ms:.2f}ms)"

        title_line = f"{timestamp} {title}"

        # 跟踪 ID
        trace_line = f"   trace_id: {trace_id}"

        lines = [title_line, trace_line]

        # 错误信息
        if error:
            error_header = "   error:"
            lines.extend([error_header, f"   {error}"])

        # 输出数据
        if output_data and not error:
            output_header = "   output:"
            output_json = self._format_json_data(output_data)

            # 处理多行输出，每行都添加适当的缩进
            if '\n' in output_json:
                lines.append(output_header)
                for line in output_json.split('\n'):
                    lines.append(f"   {line}")
            else:
                lines.extend([output_header, f"   {output_json}"])

        # 添加分割线
        lines.append(self._create_separator("·", 60))

        return "\n".join(lines)

    def format_status_message(self,
                             message: str,
                             level: LogLevel = LogLevel.INFO,
                             component: Optional[str] = None) -> str:
        """格式化状态消息"""
        timestamp = self._get_timestamp()
        style = self.LOG_STYLES[level]

        # 组件前缀
        prefix = f"[{component}] " if component else ""

        formatted_msg = f"{timestamp} {style['icon']} {prefix}{message}"
        return formatted_msg

    def format_progress_message(self,
                               message: str,
                               current: int,
                               total: int,
                               component: Optional[str] = None) -> str:
        """格式化进度消息"""
        timestamp = self._get_timestamp()
        percentage = (current / total * 100) if total > 0 else 0
        progress_bar = self._create_progress_bar(current, total)

        # 组件前缀
        prefix = f"[{component}] " if component else ""

        formatted_msg = f"{timestamp} [...] {prefix}{message} {progress_bar} ({current}/{total}, {percentage:.1f}%)"
        return formatted_msg

    def _create_progress_bar(self, current: int, total: int, width: int = 20) -> str:
        """创建进度条"""
        if total <= 0:
            return "=" * width

        filled = int(width * current / total)
        bar = "=" * filled + "." * (width - filled)
        return f"[{bar}]"

    def format_separator(self, title: Optional[str] = None, char: str = "═") -> str:
        """格式化分割线"""
        if title:
            separator = f" {title} "
            padding = (80 - len(separator)) // 2
            line = char * padding + separator + char * padding
        else:
            line = char * 80

        return line

    def format_request_interrupted(self, component: str, reason: str = "user") -> str:
        """格式化请求中断消息（类似 Claude Code）"""
        timestamp = self._get_timestamp()

        # 类似 Claude Code 的中断消息格式
        message = f"{component} [Request interrupted by {reason}]"
        formatted_msg = f"{timestamp} {self.ICONS[StatusType.INTERRUPTED]} {message}"

        return formatted_msg


