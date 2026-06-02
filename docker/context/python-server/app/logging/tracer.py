"""
核心追踪器实现 - 简化版，直接使用 Python logging
"""
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, List, Optional
from datetime import datetime, timezone

from .sanitizer import sanitize_for_logging
from .claude_formatter import ClaudeCodeFormatter, StatusType


class APITracer:
    """API 调用追踪器 - 简化版"""

    def __init__(self, component: str, use_claude_format: bool = True):
        self.component = component
        self.enabled = self._check_enabled()
        self.use_claude_format = use_claude_format
        self.formatter = ClaudeCodeFormatter() if use_claude_format else None

        # 使用根 logger 避免重复输出
        self.logger = logging.getLogger(f'aio.{component}')
        # 不添加额外 handler，使用根 logger 的配置
        self.logger.setLevel(logging.INFO)
        # 确保子 logger 传播消息到根 logger
        self.logger.propagate = True

    def _check_enabled(self) -> bool:
        """检查是否启用追踪"""
        return os.getenv('LOG_TOOL_TRACE', 'false').lower() == 'true'

    async def trace_async_call(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict,
        **trace_options
    ) -> Any:
        """追踪异步函数调用"""
        if not self.enabled:
            return await func(*args, **kwargs)

        trace_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # 记录输入
        if trace_options.get('log_input', True):
            try:
                self._log_input(func.__name__, trace_id, args, kwargs, trace_options)
            except Exception:
                pass  # 日志错误不影响业务

        try:
            # 执行函数
            result = await func(*args, **kwargs)

            # 记录输出
            if trace_options.get('log_output', True):
                try:
                    self._log_output(
                        func.__name__, trace_id, result, 'success',
                        start_time if trace_options.get('include_timing', True) else None,
                        trace_options
                    )
                except Exception:
                    pass  # 日志错误不影响业务

            return result

        except Exception as e:
            # 记录错误
            try:
                self._log_output(
                    func.__name__, trace_id, {'error': str(e), 'type': type(e).__name__},
                    'error',
                    start_time if trace_options.get('include_timing', True) else None,
                    trace_options
                )
            except Exception:
                pass  # 日志错误不影响业务
            raise

    def trace_sync_call(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict,
        **trace_options
    ) -> Any:
        """追踪同步函数调用"""
        if not self.enabled:
            return func(*args, **kwargs)

        trace_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # 记录输入
        if trace_options.get('log_input', True):
            try:
                self._log_input(func.__name__, trace_id, args, kwargs, trace_options)
            except Exception:
                pass  # 日志错误不影响业务

        try:
            # 执行函数
            result = func(*args, **kwargs)

            # 记录输出
            if trace_options.get('log_output', True):
                try:
                    self._log_output(
                        func.__name__, trace_id, result, 'success',
                        start_time if trace_options.get('include_timing', True) else None,
                        trace_options
                    )
                except Exception:
                    pass  # 日志错误不影响业务

            return result

        except Exception as e:
            # 记录错误
            try:
                self._log_output(
                    func.__name__, trace_id, {'error': str(e), 'type': type(e).__name__},
                    'error',
                    start_time if trace_options.get('include_timing', True) else None,
                    trace_options
                )
            except Exception:
                pass  # 日志错误不影响业务
            raise

    def _log_input(
        self,
        func_name: str,
        trace_id: str,
        args: tuple,
        kwargs: dict,
        options: dict
    ):
        """记录输入参数"""
        if not self.enabled:
            return

        # 清理敏感字段 - 直接传递 kwargs 内容而不是包装
        cleaned_kwargs = self._clean_data(kwargs, options.get('exclude_fields', []))
        sanitized_kwargs = sanitize_for_logging(cleaned_kwargs, field_name='input')

        # 限制长度 - 直接使用清理后的 kwargs，不额外包装
        input_data = self._truncate_data(
            sanitized_kwargs, options.get('max_input_length', 2000)
        )

        if self.use_claude_format and self.formatter:
            # 使用 Claude 格式
            message = self.formatter.format_api_call_start(
                component=self.component,
                function=func_name,
                trace_id=trace_id,
                input_data=input_data
            )
            self.logger.info(message)
        else:
            # 使用 JSON 格式
            log_entry = {
                'type': 'api_input',
                'component': self.component,
                'function': func_name,
                'trace_id': trace_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'input': input_data
            }
            self.logger.info(json.dumps(log_entry, ensure_ascii=False))

    def _log_output(
        self,
        func_name: str,
        trace_id: str,
        result: Any,
        status: str,
        start_time: Optional[float],
        options: dict
    ):
        """记录输出结果"""
        if not self.enabled:
            return

        cleaned_result = self._clean_data(result, options.get('exclude_fields', []))
        sanitized_result = sanitize_for_logging(
            cleaned_result,
            field_name='output',
            include_bulky_preview=status == 'error',
        )
        output_data = self._truncate_data(
            sanitized_result, options.get('max_output_length', 2000)
        )

        duration_ms = round((time.time() - start_time) * 1000, 2) if start_time else None

        if self.use_claude_format and self.formatter:
            # 使用 Claude 格式
            status_type = StatusType.COMPLETED if status == 'success' else StatusType.FAILED
            error = result.get('error') if isinstance(result, dict) and status == 'error' else None

            message = self.formatter.format_api_call_end(
                component=self.component,
                function=func_name,
                trace_id=trace_id,
                status=status_type,
                output_data=output_data,
                duration_ms=duration_ms,
                error=error
            )
            self.logger.info(message)
        else:
            # 使用 JSON 格式
            log_entry = {
                'type': 'api_output',
                'component': self.component,
                'function': func_name,
                'trace_id': trace_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'status': status,
                'output': output_data
            }

            if duration_ms is not None:
                log_entry['duration_ms'] = duration_ms

            self.logger.info(json.dumps(log_entry, ensure_ascii=False))

    def _clean_data(self, data: Any, exclude_fields: List[str]) -> Any:
        """清理敏感字段和不可读对象"""
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                # Convert non-string keys to string for safety
                key_str = str(k) if not isinstance(k, str) else k
                if key_str not in exclude_fields and not key_str.startswith('_'):
                    cleaned_value = self._clean_data(v, exclude_fields)
                    # 只保留非 None 的值
                    if cleaned_value is not None:
                        # Preserve original key type
                        cleaned[k] = cleaned_value
            return cleaned
        elif isinstance(data, (list, tuple)):
            cleaned_items = []
            for item in data:
                # 只处理基本类型，忽略所有对象
                if isinstance(item, (str, int, float, bool, dict, list, tuple, type(None))):
                    cleaned_items.append(self._clean_data(item, exclude_fields))
                # 对象类型直接跳过，不添加到结果中
            return cleaned_items
        elif hasattr(data, '__dict__') and not isinstance(data, (str, int, float, bool, dict, list, tuple)):
            # 尝试转换为字典（适用于 Pydantic 模型等）
            if hasattr(data, 'model_dump'):
                # Pydantic v2 模型
                return self._clean_data(data.model_dump(), exclude_fields)
            elif hasattr(data, 'dict'):
                # Pydantic v1 模型或其他支持 dict() 的对象
                return self._clean_data(data.dict(), exclude_fields)
            elif hasattr(data, '__dict__'):
                # 普通对象，使用 __dict__
                return self._clean_data(data.__dict__, exclude_fields)
            else:
                # 其他对象类型直接忽略
                return None
        else:
            return data

    def _truncate_data(self, data: Any, max_length: int) -> Any:
        """限制数据长度 - 保留原始数据结构用于格式化器处理"""
        if isinstance(data, str):
            # 对于字符串，检查长度但保留换行
            if len(data) > max_length:
                # 找到合适的截断点，尽量保留完整行
                truncated = data[:max_length]
                last_newline = truncated.rfind('\n')
                if last_newline > max_length * 0.8:  # 如果在后80%找到换行，在那里截断
                    truncated = data[:last_newline + 1]
                return truncated + "... [TRUNCATED]"
            return data
        else:
            # 对于其他类型，先转换成 JSON 检查长度
            try:
                data_str = json.dumps(data, default=str, ensure_ascii=False)
                if len(data_str) > max_length:
                    # 返回截断标记，让上层处理
                    return f"[DATA TOO LARGE - {len(data_str)} chars, showing first {max_length}...]"
                return data
            except Exception:
                return str(data)[:max_length] + "... [TRUNCATED]"
