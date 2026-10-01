# SMPT Portfolio Ledger

COMM 371 Student Managed Portfolio Trust 的持仓与交易记录查看页面。

**在线查看：** 启用 GitHub Pages 后，地址为 `https://<用户名>.github.io/SMPT-Holding/`

## 内容

- 四个持仓快照：2025-08-31、2025-12-31、2026-04-02、2026-08-31（“Portfolio Holdings & Analysis” 持仓分析表，持仓明细来自 RBC 账户）
- 各时期的资产配置，持仓可按资产类别或行业（持仓分析表里的行业代码）分组查看
- 分析：自成立以来与 S&P/TSX、S&P 500 的收益比较，各期与持仓分析表中综合基准的比较，波动率、Beta、最大回撤等风险指标，以及每个提案之后 3/6/12 个月和至今相对基准的表现（价格来自 Yahoo Finance）
- GICS 行业配置：与 S&P 500、S&P/TSX 的行业权重比较（超配/低配），各行业市值前 15 名并标出已持有的公司，以及行业配置随快照的变化
- 2022-11 至 2026-04 的买卖提案，以及根据前后快照推断的执行情况（2025-08 之前没有快照，较早的提案靠 2025-08-31 持仓和后续报告核对）

交易日期是提案日期，不是成交日期。

## 更新

新增一个快照（例如 `2026 12 31 Portfolio Holdings & Analysis.xlsx`）时：

```bash
pip install openpyxl
python build/parse_holdings.py ~/Documents/COMM371/portfolioAnalysis   # 更新 data/holdings.json
# 如有新交易，在 data/trades.json 里手动添加
python build/parse_perf.py ~/Documents/COMM371/portfolioAnalysis      # 更新 data/performance.json（需要新的 HPR 表）
python build/fetch_prices.py      # 更新 data/prices.json（需要联网和 yfinance）
python build/fetch_sectors.py     # 更新 data/sectors.json（需要联网、yfinance、requests、beautifulsoup4）
python build/build_site.py                                   # 重新生成 index.html
```

原始 Excel 和提案 PDF 不放进这个仓库，因为里面有账户号码和同学姓名。
