"""测试 Tushare 自定义 API 代理接口响应时间"""
import time
import sys
sys.path.insert(0, '.')

from tradingagents.dataflows.providers.china.tushare import get_tushare_provider

print("=" * 60)
print("Tushare 自定义 API 代理接口超时测试")
print("=" * 60)

provider = get_tushare_provider()
print(f"Provider: {provider}")
print(f"use_custom_api: {getattr(provider, 'use_custom_api', False)}")
print(f"custom_api_url: {getattr(provider, 'custom_api_url', 'N/A')}")
print(f"connected: {getattr(provider, 'connected', False)}")
print(f"api: {provider.api}")
print()

tests = [
    ("stock_basic (每日基本)", lambda: provider.api.daily_basic(trade_date="20260511", fields="ts_code,total_mv")),
    ("daily (日线历史)", lambda: provider.api.daily(ts_code="601127.SH", start_date="20260401", end_date="20260511")),
    ("stock_company (公司信息)", lambda: provider.api.stock_basic(exchange="SZSE", list_status="L", fields="ts_code,symbol,name,area,industry,list_date")),
]

for name, func in tests:
    print(f"\n测试: {name}")
    start = time.time()
    try:
        result = func()
        elapsed = time.time() - start
        if result is not None and hasattr(result, 'empty') and not result.empty:
            print(f"  ✅ 成功 - 耗时: {elapsed:.2f}s, 记录数: {len(result)}")
        elif result is not None:
            print(f"  ⚠️ 返回空数据 - 耗时: {elapsed:.2f}s")
        else:
            print(f"  ❌ 返回 None - 耗时: {elapsed:.2f}s")
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ❌ 异常: {e} - 耗时: {elapsed:.2f}s")

print("\n" + "=" * 60)
print("异步包装测试 (_run_async 模拟)")
print("=" * 60)

import asyncio

def _run_async(coro, timeout=30):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    else:
        return asyncio.run(coro)

async_tests = [
    ("get_stock_list", lambda: provider.get_stock_list()),
    ("get_historical_data", lambda: provider.get_historical_data("601127", "20260401", "20260511")),
    ("get_stock_basic_info", lambda: provider.get_stock_basic_info("601127")),
]

for name, coro_func in async_tests:
    print(f"\n测试(异步包装): {name}")
    start = time.time()
    try:
        result = _run_async(coro_func(), timeout=30)
        elapsed = time.time() - start
        if result is not None:
            if hasattr(result, 'empty'):
                if not result.empty:
                    print(f"  ✅ 成功 - 耗时: {elapsed:.2f}s, 记录数: {len(result)}")
                else:
                    print(f"  ⚠️ 返回空 DataFrame - 耗时: {elapsed:.2f}s")
            elif isinstance(result, dict) and result.get('name'):
                print(f"  ✅ 成功 - 耗时: {elapsed:.2f}s, name={result.get('name')}")
            elif isinstance(result, dict):
                print(f"  ⚠️ 返回空 dict - 耗时: {elapsed:.2f}s")
            else:
                print(f"  ✅ 成功 - 耗时: {elapsed:.2f}s")
        else:
            print(f"  ❌ 返回 None - 耗时: {elapsed:.2f}s")
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ❌ 异常: {type(e).__name__}: {e} - 耗时: {elapsed:.2f}s")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
