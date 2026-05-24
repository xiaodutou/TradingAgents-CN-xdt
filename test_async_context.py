"""测试 FastAPI 事件循环上下文下的 _run_async 行为"""
import asyncio
import time
import sys
import concurrent.futures
sys.path.insert(0, '.')

from tradingagents.dataflows.providers.china.tushare import get_tushare_provider

def _run_async(coro, timeout=30):
    """模拟 data_source_manager.py 中的 _run_async"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        print(f"  -> 使用 run_coroutine_threadsafe (事件循环运行中)")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    else:
        print(f"  -> 使用 asyncio.run (无运行中的事件循环)")
        return asyncio.run(coro)

def test_sync_thread(provider, result_holder):
    """模拟 FastAPI 的同步请求处理线程"""
    print("\n[同步线程] 测试 get_historical_data")
    start = time.time()
    try:
        result = _run_async(provider.get_historical_data("601127", "20260401", "20260511"), timeout=30)
        elapsed = time.time() - start
        if result is not None and hasattr(result, 'empty') and not result.empty:
            result_holder['status'] = f"✅ 成功 - 耗时: {elapsed:.2f}s, 记录数: {len(result)}"
        else:
            result_holder['status'] = f"❌ 无数据 - 耗时: {elapsed:.2f}s"
    except Exception as e:
        elapsed = time.time() - start
        result_holder['status'] = f"❌ 异常: {type(e).__name__}: {e} - 耗时: {elapsed:.2f}s"

async def fastapi_simulator():
    """模拟 FastAPI 的事件循环环境"""
    print("=" * 60)
    print("FastAPI 事件循环上下文模拟测试")
    print("=" * 60)

    provider = get_tushare_provider()
    print(f"use_custom_api: {getattr(provider, 'use_custom_api', False)}")
    print(f"custom_api_url: {getattr(provider, 'custom_api_url', 'N/A')}")
    print(f"connected: {getattr(provider, 'connected', False)}")
    print()

    result_holder = {'status': 'pending'}

    # 在运行中的事件循环里，把同步任务提交到线程池
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, test_sync_thread, provider, result_holder)

    # 等待线程完成（模拟 FastAPI 等待 sync endpoint）
    # 注意：这里用较长的 timeout 以免误判
    try:
        await asyncio.wait_for(future, timeout=60)
    except asyncio.TimeoutError:
        print("\n[主循环] 等待超时 (60s)")
        result_holder['status'] = "❌ 超时"

    print(f"\n结果: {result_holder['status']}")
    print("=" * 60)

print("启动事件循环模拟...")
asyncio.run(fastapi_simulator())
