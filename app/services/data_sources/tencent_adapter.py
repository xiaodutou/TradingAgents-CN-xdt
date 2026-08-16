"""
腾讯财经数据源适配器
- K 线数据: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
- 实时行情: https://qt.gtimg.cn/q=
- 免费、无需注册、IP 未被封（2026-08 验证）
"""
from typing import Optional, Dict, List
import logging
import json
import requests
import pandas as pd
from datetime import datetime, timedelta

from .base import DataSourceAdapter

logger = logging.getLogger(__name__)


class TencentAdapter(DataSourceAdapter):
    """腾讯财经数据源适配器"""

    KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    QUOTE_URL = "https://qt.gtimg.cn/q="

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return "tencent"

    def _get_default_priority(self) -> int:
        return 3  # 在 akshare(2) 和 baostock(1) 之后

    def is_available(self) -> bool:
        """检查腾讯财经 API 是否可用"""
        try:
            resp = requests.get(
                f"{self.QUOTE_URL}sh000001",
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            return resp.status_code == 200 and "v_sh000001" in resp.text
        except Exception:
            return False

    def _to_tencent_code(self, code: str) -> str:
        """将 6 位代码转换为腾讯格式 (sh600000 / sz000001)"""
        code = str(code).strip().zfill(6)
        if code.startswith(('sh', 'sz')):
            return code
        if code.startswith(('5', '6', '9', '7')):
            return f"sh{code}"
        return f"sz{code}"

    def get_kline(self, code: str, period: str = "day", limit: int = 120,
                  adj: Optional[str] = None) -> Optional[List[Dict]]:
        """
        获取 K 线数据
        period: day/week/month
        adj: none/qfq/hfq（腾讯 API 默认不复权）
        """
        try:
            tc_code = self._to_tencent_code(code)

            # 周期映射
            period_map = {"day": "day", "week": "week", "month": "month"}
            tc_period = period_map.get(period, "day")

            # 计算日期范围（多取一些数据以确保够用）
            end_date = datetime.now()
            start_date = end_date - timedelta(days=limit * 3)

            params = {
                "param": f"{tc_code},{tc_period},{start_date.strftime('%Y-%m-%d')},"
                         f"{end_date.strftime('%Y-%m-%d')},{limit},",
            }

            resp = requests.get(
                self.KLINE_URL,
                params=params,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            if resp.status_code != 200:
                logger.warning(f"腾讯 K 线 HTTP {resp.status_code}")
                return None

            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"腾讯 K 线 API 错误: {data}")
                return None

            stock_data = data.get("data", {}).get(tc_code, {})

            # 腾讯 API 返回的 key 可能是 'day'/'qfqday'/'week'/'month' 等
            kline_key = tc_period
            kline_data = stock_data.get(kline_key) or stock_data.get(f"qfq{kline_key}")

            if not kline_data:
                # 尝试其他可能的 key
                for key in stock_data:
                    if isinstance(stock_data[key], list) and len(stock_data[key]) > 0:
                        kline_data = stock_data[key]
                        break

            if not kline_data:
                logger.warning(f"腾讯 K 线无数据: {code}")
                return None

            # 解析数据：腾讯格式 [日期, 开盘, 收盘, 最高, 最低, 成交量]
            items = []
            for row in kline_data:
                if len(row) < 6:
                    continue
                try:
                    item = {
                        "time": row[0],
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]) * 100,  # 腾讯返回手数，转为股数
                    }
                    # 部分数据有成交额字段
                    if len(row) > 6:
                        try:
                            item["amount"] = float(row[6])
                        except (ValueError, TypeError):
                            pass
                    items.append(item)
                except (ValueError, TypeError) as e:
                    logger.debug(f"腾讯 K 线解析跳过: {row} - {e}")
                    continue

            # 按日期正序（腾讯返回的已经是正序）
            items.sort(key=lambda x: x["time"])

            logger.info(f"✅ 腾讯 K 线: {code} 获取 {len(items)} 条")
            return items[-limit:] if len(items) > limit else items

        except Exception as e:
            logger.error(f"❌ 腾讯 K 线失败: {e}")
            return None

    def get_realtime_quotes(self) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        """获取全市场实时行情（暂不支持批量，返回 None）"""
        return None

    def get_realtime_quote(self, code: str) -> Optional[Dict]:
        """获取单只股票实时行情（含 PE_TTM/PB）"""
        try:
            tc_code = self._to_tencent_code(code)
            resp = requests.get(
                f"{self.QUOTE_URL}{tc_code}",
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            # 腾讯行情是 GBK 编码
            text = resp.content.decode("gbk", errors="ignore")
            parts = text.split("~")

            if len(parts) < 50:
                return None

            return {
                "name": parts[1],
                "code": parts[2],
                "close": self._safe_float(parts[3]),
                "pre_close": self._safe_float(parts[4]),
                "open": self._safe_float(parts[5]),
                "volume": self._safe_float(parts[6]),  # 手
                "amount": self._safe_float(parts[37]),  # 万元
                "high": self._safe_float(parts[33]) if len(parts) > 33 else None,
                "low": self._safe_float(parts[34]) if len(parts) > 34 else None,
                "pe_ttm": self._safe_float(parts[39]) if len(parts) > 39 else None,
                "pb": self._safe_float(parts[46]) if len(parts) > 46 else None,
                "source": "tencent",
            }
        except Exception as e:
            logger.error(f"❌ 腾讯实时行情失败: {e}")
            return None

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        try:
            if val is None or val == "" or val == "N/A":
                return None
            return float(val)
        except (ValueError, TypeError):
            return None

    # === 以下方法为兼容接口，腾讯暂不支持 ===

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        return None

    def get_daily_basic(self, trade_date: str) -> Optional[pd.DataFrame]:
        return None

    def find_latest_trade_date(self) -> Optional[str]:
        return None

    def get_news(self, code: str, days: int = 2, limit: int = 50,
                 include_announcements: bool = True):
        return None
