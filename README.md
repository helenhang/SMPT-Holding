# SMPT Portfolio Ledger

COMM 371 Student Managed Portfolio Trust 的持仓与交易记录查看页面。

**在线查看：** 启用 GitHub Pages 后，地址为 `https://<用户名>.github.io/SMPT-Holding/`

## 内容

- 四个持仓快照：2025-08-31、2025-12-31、2026-04-02、2026-08-31（来自 RBC 持仓表）
- 各时期的资产配置，持仓可按资产类别或行业（RBC 行业代码）分组查看
- 2022-11 至 2026-04 的买卖提案，以及根据前后快照推断的执行情况（2025-08 之前没有快照，较早的提案靠 2025-08-31 持仓和后续报告核对）

交易日期是提案日期，不是成交日期。

## 更新

新增一个快照（例如 `2026 12 31 Portfolio Holdings & Analysis.xlsx`）时：

```bash
pip install openpyxl
python build/parse_holdings.py ~/Documents/COMM371/portfolioAnalysis   # 更新 data/holdings.json
# 如有新交易，在 data/trades.json 里手动添加
python build/build_site.py                                   # 重新生成 index.html
```

原始 Excel 和提案 PDF 不放进这个仓库，因为里面有账户号码和同学姓名。
