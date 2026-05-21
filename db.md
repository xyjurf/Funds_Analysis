# 数据库文档：Fund_DATA

MySQL 数据库，存储中国公募基金的净值、分类、分红及基准指数数据。

## 表一览

| 表名 | 行数 | 数据量 | 说明 |
|---|---|---|---|
| [fund_data](#fund_data) | 23,564 | 小 | 基金列表及阶段涨幅（东方财富排名 API） |
| [fund_values](#fund_values) | 27,550,483 | 大 | 基金历史净值日线（天天基金 F10 API） |
| [fund_info](#fund_info) | 23,564 | 小 | 基金分类、公司、经理、规模补充信息 |
| [index_data](#index_data) | 34,954 | 小 | 基准指数日线行情（akshare） |
| [fund_dividends](#fund_dividends) | 46,446 | 小 | 基金分红记录 |

## fund_data

基金列表及阶段涨幅数据，来源：东方财富基金排名 API。

**表结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| fund_code | INT(11) | 基金代码（需 .zfill(6) 补零） |
| fund_name | VARCHAR(100) | 基金名称 |
| date | VARCHAR(20) | 数据日期 |
| unit_nav | DECIMAL(10,4) | 单位净值 |
| cumulative_nav | DECIMAL(10,4) | 累计净值 |
| daily_growth | DECIMAL(10,4) | 日增长率 |
| week_1 | DECIMAL(10,4) | 近 1 周涨幅 |
| month_1 | DECIMAL(10,4) | 近 1 月涨幅 |
| month_3 | DECIMAL(10,4) | 近 3 月涨幅 |
| month_6 | DECIMAL(10,4) | 近 6 月涨幅 |
| year_1 | DECIMAL(10,4) | 近 1 年涨幅 |
| year_2 | DECIMAL(10,4) | 近 2 年涨幅 |
| year_3 | DECIMAL(10,4) | 近 3 年涨幅 |
| this_year | DECIMAL(10,4) | 今年以来涨幅 |
| since_inception | DECIMAL(10,4) | 成立以来涨幅 |
| custom | VARCHAR(20) | 自定义 |
| fee | DECIMAL(10,4) | 费率 |

**统计**
- 总基金数：23,564
- 数据日期：2024-05-27 ~ 2026-05-13

**示例**

```
fund_code=4320,  fund_name=前海开源再融资主题精选,         date=2026-05-13, unit_nav=6.6208,  daily_growth=0.0300
fund_code=16370, fund_name=诺安先锋混合A,                   date=2026-05-13, unit_nav=3.0414,  daily_growth=0.0236
fund_code=22364, fund_name=汇添富科技优选混合A,             date=2026-05-13, unit_nav=5.4035,  daily_growth=0.0352
```

## fund_values

基金历史净值日线数据，来源：天天基金 F10 数据 API（逐只基金分页拉取）。

**表结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INT | 自增主键 |
| fund_code | VARCHAR(20) | 基金代码（6 位字符串） |
| net_value_date | DATE | 净值日期 |
| unit_net_value | DECIMAL(10,4) | 单位净值 |
| cumulative_net_value | DECIMAL(10,4) | 累计净值 |
| daily_growth_rate | VARCHAR(20) | 日增长率（如 "+1.23%"） |
| purchase_status | VARCHAR(20) | 申购状态 |
| redemption_status | VARCHAR(20) | 赎回状态 |
| created_at | TIMESTAMP | 记录创建时间 |

唯一键：`(fund_code, net_value_date)`

**统计**
- 总行数：27,550,483
- 覆盖基金：23,574 只
- 日期范围：2001-09-21 ~ 2026-05-13

**示例**

```
fund_code=000001, net_value_date=2026-04-28, unit_net_value=1.1420, cumulative=3.7150, daily_growth=-0.95%
fund_code=000001, net_value_date=2026-04-27, unit_net_value=1.1530, cumulative=3.7260, daily_growth=+2.13%
fund_code=000001, net_value_date=2026-04-24, unit_net_value=1.1290, cumulative=3.7020, daily_growth=+0.18%
```

## fund_info

基金分类、公司、经理等补充信息，来源：fundcode_search.js + F10 页面。

**表结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| fund_code | VARCHAR(20) | 基金代码（主键） |
| fund_name | VARCHAR(100) | 基金名称 |
| fund_type | VARCHAR(50) | 详细分类（如"混合型-灵活"） |
| fund_type_main | VARCHAR(20) | 主类别（如"混合型"） |
| fund_company | VARCHAR(100) | 基金管理人 |
| fund_manager | VARCHAR(200) | 基金经理 |
| establishment_date | DATE | 成立日期 |
| fund_size | DECIMAL(14,2) | 净资产规模（亿元） |
| fund_size_date | DATE | 规模截止日期 |
| purchase_rate | DECIMAL(6,4) | 原始申购费率 |
| discount_rate | DECIMAL(6,4) | 优惠申购费率 |
| min_purchase_amount | DECIMAL(12,2) | 最低申购金额（元） |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

**统计**
- 总记录：23,564
- 有分类数据：23,564
- 有基金公司：23,494
- 有基金经理：23,494
- 有成立日期：23,494
- 有规模数据：23,420

**主类别分布**

| 主类别 | 数量 |
|---|---|
| 混合型 | 9,138 |
| 债券型 | 7,104 |
| 指数型 | 4,701 |
| FOF | 1,178 |
| 股票型 | 1,089 |
| QDII | 351 |
| 商品 | 2 |
| 其他 | 1 |

**示例**

```
fund_code=000001, fund_name=华夏成长混合,  fund_type_main=混合型, fund_company=华夏基金,    fund_manager=郑泽鸿, fund_size=26.44亿
fund_code=000003, fund_name=中海上证转债A, fund_type_main=债券型, fund_company=中海基金,    fund_manager=梅寓寒, fund_size=0.51亿
fund_code=000006, fund_name=西部利得量化成长A, fund_type_main=混合型, fund_company=西部利得基金, fund_manager=盛丰衍, fund_size=13.37亿
```

## index_data

基准指数日线行情数据（OHLCV），来源：akshare（东方财富/新浪 API）。

**表结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INT | 自增主键 |
| index_code | VARCHAR(20) | 指数代码 |
| index_name | VARCHAR(50) | 指数名称 |
| trade_date | DATE | 交易日 |
| open_price | DECIMAL(12,4) | 开盘价 |
| close_price | DECIMAL(12,4) | 收盘价 |
| high_price | DECIMAL(12,4) | 最高价 |
| low_price | DECIMAL(12,4) | 最低价 |
| volume | BIGINT | 成交量（手） |
| amount | DECIMAL(20,2) | 成交额（元） |
| created_at | TIMESTAMP | 记录创建时间 |

唯一键：`(index_code, trade_date)`

**覆盖指数**

| 指数代码 | 名称 | 行数 | 日期范围 |
|---|---|---|---|
| 000001 | 上证指数 | 8,638 | 1990-12-19 ~ 2026-05-13 |
| 000300 | 沪深300 | 5,905 | 2002-01-04 ~ 2026-05-13 |
| 000852 | 中证1000 | 2,811 | 2014-10-17 ~ 2026-05-13 |
| 000905 | 中证500 | 5,184 | 2005-01-04 ~ 2026-05-13 |
| 399001 | 深证成指 | 8,546 | 1991-04-03 ~ 2026-05-13 |
| 399006 | 创业板指 | 3,870 | 2010-06-01 ~ 2026-05-13 |

**示例**

```
index_code=000300, index_name=沪深300, trade_date=2002-01-04, open=1316.46, close=1316.46
index_code=000300, index_name=沪深300, trade_date=2002-01-07, open=1302.08, close=1302.08
```

## fund_dividends

基金分红记录，来源：天天基金 pingzhongdata JS 中的 `Data_netWorthTrend` 数组。

**表结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INT | 自增主键 |
| fund_code | VARCHAR(20) | 基金代码 |
| dividend_date | DATE | 除权日 |
| unit_dividend | DECIMAL(10,6) | 每份分红金额（元） |
| nav_on_date | DECIMAL(10,4) | 除权日单位净值 |
| created_at | TIMESTAMP | 记录创建时间 |

唯一键：`(fund_code, dividend_date)`

**统计**
- 总分红记录：46,446
- 有分红的基金：7,246 只
- 日期范围：2002-04-22 ~ 2026-05-13

**示例**

```
fund_code=000004, dividend_date=2014-12-30, unit_dividend=0.210000, nav=1.1950
fund_code=000005, dividend_date=2014-09-24, unit_dividend=0.011100, nav=1.0180
fund_code=000005, dividend_date=2015-06-24, unit_dividend=0.050000, nav=1.0460
```

## 数据采集脚本

| 脚本 | 功能 |
|---|---|
| `Get_Funds.py` | 全市场基金列表 → `fund_data` |
| `Get_FundValues.py` | 全量历史净值（多线程） → `fund_values` |
| `Update_FundValues.py` | 增量更新 `fund_values`（断点续传） |
| `Fix_FundData.py` | 基金分类 + 详情 → `fund_info` |
| `Fetch_IndexData.py` | 基准指数行情 → `index_data` |
| `Fetch_Dividend.py` | 基金分红记录 → `fund_dividends` |
