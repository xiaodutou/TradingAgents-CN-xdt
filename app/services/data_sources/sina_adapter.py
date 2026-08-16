"""
新浪财经数据源适配器
- K 线数据: https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
- 实时行情: https://hq.sinajs.cn/
- 免费、无需注册
"""
from typing import Optional, Dict, List
import logging
import json
import requests
import pandas as pd
from datetime import datetime, timedelta

from .base import DataSourceAdapter

logger = logging.getLogger(__name__)


class SinaAdapter(DataSourceAdapter):
    """新浪财经数据源适配器"""

    KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    QUOTE_URL = "https://hq.sinajs.cn/list="

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return "sina"

    def _get_default_priority(self) -> int:
        return 4  # 在腾讯(3)之后

    def is_available(self) -> bool:
        """检查新浪财经 API 是否可用（测试 K 线接口，更稳定）"""
        try:
            resp = requests.get(
                self.KLINE_URL,
                params={
                    "symbol": "sh000001",
                    "scale": 240,
                    "ma": "no",
                    "datalen": 1,
                },
                timeout=5,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn"
                }
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            return isinstance(data, list) and len(data) > 0
        except Exception:
            return False

    def _to_sina_code(self, code: str) -> str:
        """将 6 位代码转换为新浪格式 (sh600000 / sz000001)"""
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
        注：新浪 API 的 scale 参数：5=5分钟, 15=15分钟, 30=30分钟, 60=60分钟, 240=日, 1200=周, 7200=月
        """
        try:
            sina_code = self._to_sina_code(code)

            # 周期映射到新浪的 scale
            scale_map = {
                "day": 240,
                "week": 1200,
                "month": 7200,
                "5m": 5,
                "15m": 15,
                "30m": 30,
                "60m": 60,
            }
            scale = scale_map.get(period, 240)

            params = {
                "symbol": sina_code,
                "scale": scale,
                "ma": "no",
                "datalen": limit,
            }

            resp = requests.get(
                self.KLINE_URL,
                params=params,
                timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn"
                }
            )

            if resp.status_code != 200:
                logger.warning(f"新浪 K 线 HTTP {resp.status_code}")
                return None

            # 新浪返回的是 JSON 数组
            data = resp.json()
            if not isinstance(data, list) or len(data) == 0:
                logger.warning(f"新浪 K 线无数据: {code}")
                return None

            # 解析数据：新浪格式 {day, open, high, low, close, volume}
            items = []
            for row in data:
                try:
                    item = {
                        "time": row.get("day", ""),
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": float(row.get("volume", 0)),
                    }
                    items.append(item)
                except (ValueError, TypeError) as e:
                    logger.debug(f"新浪 K 线解析跳过: {row} - {e}")
                    continue

            # 按日期正序
            items.sort(key=lambda x: x["time"])

            logger.info(f"✅ 新浪 K 线: {code} 获取 {len(items)} 条")
            return items[-limit:] if len(items) > limit else items

        except Exception as e:
            logger.error(f"❌ 新浪 K 线失败: {e}")
            return None

    def get_realtime_quotes(self) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        """暂不支持批量行情"""
        return None

    # === 以下方法为兼容接口，新浪暂不支持 ===

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        return None

    def get_daily_basic(self, trade_date: str) -> Optional[pd.DataFrame]:
        return None

    def find_latest_trade_date(self) -> Optional[str]:
        return None

    def get_news(self, code: str, days: int = 2, limit: int = 50,
                 include_announcements: bool = True):
        return None
