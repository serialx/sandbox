"""
统一的接口打点装饰器
"""
import asyncio
import functools
from typing import Callable, Optional

from .tracer import APITracer


def trace_api(
    component: str,
    *,
    log_input: bool = True,
    log_output: bool = True,
    max_input_length: int = 2000,
    max_output_length: int = 2000,
    exclude_fields: Optional[list] = None,
    include_timing: bool = True
):
    """
    API 接口打点装饰器

    Args:
        component: 组件名称 (如 'jupyter', 'shell', 'nodejs')
        log_input: 是否记录输入参数
        log_output: 是否记录输出结果
        max_input_length: 输入参数最大长度
        max_output_length: 输出结果最大长度
        exclude_fields: 排除的敏感字段
        include_timing: 是否包含执行时间
    """
    def decorator(func: Callable) -> Callable:
        tracer = APITracer(component)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await tracer.trace_async_call(
                    func, args, kwargs,
                    log_input=log_input,
                    log_output=log_output,
                    max_input_length=max_input_length,
                    max_output_length=max_output_length,
                    exclude_fields=exclude_fields or [],
                    include_timing=include_timing
                )
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                return tracer.trace_sync_call(
                    func, args, kwargs,
                    log_input=log_input,
                    log_output=log_output,
                    max_input_length=max_input_length,
                    max_output_length=max_output_length,
                    exclude_fields=exclude_fields or [],
                    include_timing=include_timing
                )
            return sync_wrapper

    return decorator


def trace_jupyter():
    """Jupyter 专用装饰器"""
    return trace_api(
        'jupyter',
        log_input=True,
        log_output=True,
        max_input_length=5000,
        exclude_fields=['kernel_manager', 'kernel_client'],
        include_timing=True
    )


def trace_shell():
    """Shell 专用装饰器"""
    return trace_api(
        'shell',
        max_input_length=500,
        max_output_length=2000,
        exclude_fields=['session', 'process'],
        include_timing=True
    )


def trace_nodejs():
    """Node.js 专用装饰器"""
    return trace_api(
        'nodejs',
        max_input_length=1000,
        max_output_length=2000,
        exclude_fields=['subprocess', 'temp_dir'],
        include_timing=True
    )
