# Funds Analysis — 公募基金数据分析系统

中国公募基金数据采集、存储、分析与预测系统。数据来自天天基金网（东方财富），支持全市场基金净值爬取、增量更新、基金分类信息、基准指数行情，以及基于多模型集成的基金收益预测与回测。

## 数据采集

| 脚本 | 功能 |
|---|---|
| `.claude/Fund_manager/Get_Funds.py` | 从东方财富拉取全市场基金列表、阶段涨幅至 `fund_data` 表 |
| `.claude/Fund_manager/Get_FundValues.py` | 逐只基金拉取全部历史净值至 `fund_values`（多线程，支持断点续传） |
| `.claude/Fund_manager/Update_FundValues.py` | 增量更新净值数据：预检 API 最新日期 → 跳过已最新基金 → 补缺漏 |
| `.claude/Fund_manager/Fix_FundData.py` | 补充基金分类、公司、经理、规模等信息至 `fund_info` 表 |
| `.claude/Fund_manager/Fetch_IndexData.py` | 采集基准指数（上证指数、沪深300、中证500 等）日线行情 |
| `.claude/Fund_manager/Fetch_Dividend.py` | 采集基金分红记录 |

## 数据库

**Database**: `Fund_DATA` (MySQL)

| 表 | 行数 | 说明 |
|---|---|---|
| `fund_data` | ~23,564 | 基金列表及阶段涨幅（近 1 周/月/3 月/6 月/1 年/2 年/3 年） |
| `fund_values` | ~27,550,000 | 基金历史净值日线（单位净值、累计净值、日增长率），覆盖 23,574 只基金 |
| `fund_info` | ~23,564 | 基金分类（混合型/债券型/指数型等）、管理公司、基金经理、规模 |
| `index_data` | ~34,954 | 六大基准指数日线 OHLCV（上证/沪深300/中证500/中证1000/深证成指/创业板指） |
| `fund_dividends` | ~46,446 | 基金分红记录（7,246 只基金有分红数据） |

详见 [db.md](db.md)。

## 基金预测系统

`Predict_Agents/` 目录包含完整的基金收益预测与回测框架：

| 模块 | 功能 |
|---|---|
| `agents_workflow.py` | 多智能体工作流：数据收集 → 分析 → 预测 → 排名 → 决策 |
| `ensemble_models.py` | 多模型集成（LSTM、XGBoost、线性回归等） |
| `backtest.py` | 单基金回测引擎 |
| `batch_backtest.py` | 批量回测与排名 |
| `external_data.py` | 外部数据接入（宏观经济、市场指标等） |
| `db_tools.py` | 数据库查询工具 |
| `config.py` | 配置管理 |
| `run.py` | 一键运行入口 |

预测结果输出至 `result_agents/` 目录。

## 运行方式

```bash
# 激活虚拟环境
source .venv/Scripts/activate

# 数据采集
python .claude/Fund_manager/Get_Funds.py
python .claude/Fund_manager/Get_FundValues.py
python .claude/Fund_manager/Update_FundValues.py

# 基金预测
python Predict_Agents/run.py
```

## 数据说明

- 基金净值 T+1 发布，今日数据次日才可获取
- `fund_data` 中 `fund_code` 为 INT 类型（如 1, 3），查询需 `.zfill(6)` 补零为 `000001`
- `fund_values` 使用 `(fund_code, net_value_date)` 唯一键 + `INSERT IGNORE` 避免重复
- 增量更新通过进度表记录已完成的 fund_code，中断后自动跳过
