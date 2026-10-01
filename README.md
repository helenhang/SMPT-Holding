# SMPT Portfolio Ledger

COMM 371 Student Managed Portfolio Trust 的持仓与交易记录查看页面。

**在线查看：** 启用 GitHub Pages 后，地址为 `https://<用户名>.github.io/SMPT-Holding/`

## 内容

- 四个持仓快照：2025-08-31、2025-12-31、2026-04-02、2026-08-31（来自 RBC 持仓表）
- 各时期的资产配置，持仓可按资产类别或行业（RBC 行业代码）分组查看
- 分析：自成立以来与 S&P/TSX、S&P 500 的收益比较，各期与 RBC 综合基准的比较，波动率、Beta、最大回撤等风险指标，以及每个提案之后 3/6/12 个月和至今相对基准的表现（价格来自 Yahoo Finance）
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
python build/build_site.py                                   # 重新生成 index.html
```

原始 Excel 和提案 PDF 不放进这个仓库，因为里面有账户号码和同学姓名。

"提案事后表现"做好了，open index.html 后在"分析"标签页最下面就能看到。我在无头 Chrome 里检查过交互、浅色和深色主题以及手机宽度，都没有报错。改动还没有提交。

价格数据

用 yfinance 下载了 71 个代码的日线价格，另外还有两个基准 ETF：加拿大上市的对比 XIC，美股和 ADR 对比 SPY。
只缺 Foot Locker（FL），它 2025 年被收购后退市了，页面上会注明。
我用 RBC 持仓表里的每股价格抽查过几个代码，全部对得上，包括 HDB 拆股、GRGD 大跌这种特殊情况。
新增的 data/prices.json 约 1.4MB。页面里只嵌入周线价格，所以页面总大小约 211KB。
页面上新增的内容

观察期切换： 3 个月、6 个月、12 个月、至今。也可以只看买入或只看卖出，并切换按日期或按表现排序。
"方向超额"： 买入 = 股票收益 − 基准收益；卖出则取相反数，也就是卖出后股票跑输基准才算卖对了。
统计卡片： 已评估数量、方向正确率、平均和中位数方向超额。
按学年汇总： 各学年的方向正确率和平均方向超额，以及最好、最差的提案。
逐个提案列表： 附带超额柱状条和执行情况。
个股抽屉： 新增周线股价走势，虚线是按起点对齐的基准，▲▼ 标出买入和卖出提案；每条相关交易下面会显示各观察期的方向超额。
几个主要发现（12 个月观察期，共 47 条可评估）

整体方向正确率 47%，平均方向超额 −2.6 pp，大致和随机决定差不多。
按学年差别很大：
学年	方向正确率	平均方向超额
24-25	67%	+7.9 pp
23-24	22%	−19.1 pp
23-24 年最拖后腿的是 2024-03 卖掉 Rheinmetall 和 3M：之后一年 Rheinmetall 涨了约 170%，3M 涨了约 104%。
25-26 年的提案大多还没满 12 个月，看 3 个月或 6 个月的结果会更合适。
这次还改了什么

顺手修了一个旧问题：窄屏下抽屉里的持仓表比抽屉宽了约 19px。
更新了 README 和 CLAUDE.md。
以后怎么更新价格
先运行 python build/fetch_prices.py，再运行 build_site.py。价格一更新，index.html 就会跟着变，所以"重复构建后 git status 保持干净"的检查，只在 prices.json 没变的时候成立。

需要我提交吗？