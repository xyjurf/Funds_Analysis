#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基金走势预测系统 — 入口脚本

基于数据库数据和 DeepSeek LLM 的多智能体系统，
预测未来 7 个交易日的基金涨幅 Top 10 和跌幅 Bottom 10。

用法：
    python Predict_Agents/run.py                          # 使用数据库最新日期
    python Predict_Agents/run.py --date 2026-05-13        # 指定基准日期
    python Predict_Agents/run.py --date 2026-05-13 --output my_result.json
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta

# 确保能找到 Predict_Agents 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents_workflow import run_prediction
from config import PREDICT_DAYS, RESULT_DIR


def parse_args():
    parser = argparse.ArgumentParser(
        description=f'基金走势预测 — 多智能体系统（deepseek-v4-flash）'
    )
    parser.add_argument(
        '--date', '-d',
        type=str,
        default=None,
        help=f'基准日期 YYYY-MM-DD（默认使用数据库最新日期）'
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='输出结果到 JSON 文件（路径）'
    )
    parser.add_argument(
        '--save-report', '-s',
        type=str,
        default=None,
        help='保存完整报告到文本文件（路径）'
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # 确定基准日期
    target_date = args.date
    if target_date is None:
        # 使用最新数据库日期
        from db_tools import get_latest_data_date, get_latest_fund_values_date
        fund_date = get_latest_data_date()
        values_date = get_latest_fund_values_date()
        target_date = fund_date or values_date
        if target_date is None:
            print("[ERROR] 无法获取数据库日期，请手动指定 --date")
            sys.exit(1)
        print(f"[Info] 使用数据库最新日期: {target_date}")

    print(f"\n{'=' * 60}")
    print(f"  基金走势预测系统")
    print(f"  基准日期: {target_date}")
    print(f"  预测周期: 未来 {PREDICT_DAYS} 个交易日")
    print(f"  LLM 模型: deepseek-v4-flash")
    print(f"{'=' * 60}\n")

    # 执行预测
    result = run_prediction(target_date)

    # 检查错误
    if 'error' in result:
        print(f"\n[ERROR] {result['error']}")
        sys.exit(1)

    # 输出报告
    report = result.get('report', '')
    if report:
        # GBK 兼容输出
        try:
            print('\n' + report + '\n')
        except UnicodeEncodeError:
            print('\n' + report.encode('utf-8', errors='replace').decode('gbk', errors='replace') + '\n')

    # 保存 JSON
    if args.output:
        output_data = {
            'target_date': target_date,
            'report_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'predictions': result.get('predictions', {}),
            'market_analysis': result.get('market_analysis', ''),
        }
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"[OK] 结果已保存到: {args.output}")

    # 保存报告文本
    if args.save_report:
        with open(args.save_report, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"[OK] 报告已保存到: {args.save_report}")

    # 自动保存 JSON 到 result_agents 目录
    auto_path = os.path.join(
        RESULT_DIR,
        f'prediction_{target_date}.json'
    )
    output_data = {
        'target_date': target_date,
        'report_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'predictions': result.get('predictions', {}),
        'market_analysis': result.get('market_analysis', ''),
    }
    with open(auto_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 预测结果已自动保存: {auto_path}")

    # 自动保存 Markdown 报告到 result_agents 目录
    md_path = os.path.join(
        RESULT_DIR,
        f'prediction_{target_date}.md'
    )
    md_content = report
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"[OK] 报告已保存为 Markdown: {md_path}")


if __name__ == '__main__':
    main()
