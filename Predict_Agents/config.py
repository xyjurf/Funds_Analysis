"""配置：数据库连接 & DeepSeek API"""

import os

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'weilian.x',
    'port': 3306,
    'database': 'Fund_DATA',
    'charset': 'utf8mb4',
}

DEEPSEEK_CONFIG = {
    'api_key': 'sk-2887aa96058a44d19aa448bd93bfb1fb',
    'base_url': 'https://api.deepseek.com/v1',
    'model': 'deepseek-v4-flash',
    'temperature': 0.3,
}

# 预测参数
TOP_N_CANDIDATES = 30000        # 入选候选基金数量（从全市场筛选）
NAV_LOOKBACK_DAYS = 120       # 回看天数
TECH_LOOKBACK_DAYS = 60       # 技术指标计算天数
PREDICT_DAYS = 7              # 预测周期（交易日）

# 结果保存目录
RESULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'result_agents')
