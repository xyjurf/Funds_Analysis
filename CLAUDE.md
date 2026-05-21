# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

中国公募基金数据采集系统。从天天基金网（eastmoney）爬取基金列表和历史净值数据，存储到本地 MySQL。

## Database

- **Database**: `Fund_DATA` (MySQL)
- **Tables**:
  - `fund_data` — 基金列表（代码、名称、阶段涨幅等），来源：东方财富基金排名 API
  - `fund_values` — 基金历史净值数据（单位净值、累计净值、日增长率等），2700 万+ 行，来源：天天基金 F10 API
- **Unique key**: `(fund_code, net_value_date)`，使用 `INSERT IGNORE` 避免重复

## Scripts

| Script | 功能 |
|---|---|
| `Get_Funds.py` | 从东方财富拉取全市场基金列表，创建 `fund_data` 表 |
| `Get_FundValues.py` | 从 `fund_data` 读取基金代码，逐只拉取全部历史净值到 `fund_values`（支持断点续传） |
| `Update_FundValues.py` | **增量更新** `fund_values`：预检 API 最新日期 → 跳过已有数据的基金 → 仅补缺漏数据 |
| `Show_Data.py` | 查询并打印 `fund_values` 前 N 行 |
| `check_fund_data.py` | 检查 `fund_data` 表结构和数据 |
| `copy_table.py` | 大表复制工具（分批 LIMIT/OFFSET） |

## Running Scripts

```bash
# 激活虚拟环境
source .venv/Scripts/activate

# 运行
python Get_Funds.py
python Get_FundValues.py
python Update_FundValues.py
```

## Architecture Notes

- **基金代码处理**: `fund_data` 中 fund_code 是 INT 类型（如 1, 3）, 插入 `fund_values` 前需 `.zfill(6)` 补零为 `000001`, `000003`
- **增量更新策略**: 先对 `fund_values` 做 `GROUP BY fund_code` 获取每只基金最新日期，再与 API 最新日期对比，已是最新的直接跳过（无需 API 请求）。缺漏基金多线程并发拉取
- **断点重续**: `Update_FundValues.py` 通过进度表记录已完成的 fund_code，中断后自动跳过
- **数据延迟**: 基金净值 T+1 发布，今天的数据次日才可获取。脚本预检时会动态探测 API 实际最新日期
