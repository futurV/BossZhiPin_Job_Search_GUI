# 分享副本隐私清洗记录

清洗日期：2026-08-17

此目录从开发工作区复制而来，复制时明确排除了：

- 原 Git 仓库历史（`.git/`）
- `.env` 和本地凭据
- `chrome_profile/` 登录 Cookie、缓存和浏览器数据
- `resume/` 中的原始 PDF 简历
- `logs/` 中的投递 CSV、JD、招聘者信息、LLM 调用日志和截图
- `vectorstores/` 中的 Chroma SQLite、向量索引、简历哈希和 `keywords.json`
- `.venv/`、`node_modules/`、pytest 缓存和临时文件
- 旧版 README、旧技术文档及本地开发辅助目录

分享版不携带原 `.venv`。它体积约 1 GB，且内部包含原电脑的 Python/源码绝对路径，
既不适合跨机器使用，也可能暴露本地用户名。接收者可双击 `启动GUI.cmd`，由 `uv.lock`
在自己的电脑上创建等价环境。

清洗后的源码会再次扫描常见 API Key 前缀、原用户姓名、Windows 用户名、绝对路径、
SQLite/JSONL/CSV、PDF、Cookie 与向量缓存文件。扫描结果应为零个真实个人数据命中；
测试中使用的 `sk-test`、`sk-xxx` 等固定假凭据不属于真实密钥。
