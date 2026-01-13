# 🛠 IVI TOOLBOX PRO 贡献指南

感谢你参与到 IVI 自动化工具箱的开发中！为了保持代码质量和项目的可追溯性，请在提交代码前阅读以下指南。

---

## 1. 核心开发原则

- **配置驱动**：严禁将任何敏感信息（如 Root 密码、API Keys）硬编码在代码中。所有配置必须通过 `ConfigLoader` 从 `config.json` 读取。
- **非阻塞设计**：长时间运行的任务（如日志抓取、大批量图片下载）必须在独立线程中执行，严禁阻塞 Rich UI 主线程。
- **环境兼容**：代码需同时兼容 Windows (PowerShell) 和 Linux (车载嵌入式环境)。

---

## 2. 代码风格规范 (Python)

- **类型注解**：新函数必须包含类型提示（Type Hints），例如 `def func(p: Path) -> List[str]:`。
- **UI 交互**：控制台输出必须优先使用 `rich.console` 渲染，保持界面美感和进度条反馈。
- **路径处理**：严禁使用字符串拼接路径，必须统一使用 `pathlib.Path` 以确保跨平台兼容。

---

## 3. 分支与提交说明

- **分支管理**：
  - `main`: 稳定版本，仅接受从 `develop` 合并。
  - `feature/*`: 新功能开发（如 `feature/image-watermark`）。
  - `bugfix/*`: 线上问题修复。
- **Commit Message 格式**：
  - `feat: 增加多Key轮询下载功能`
  - `fix: 修复路径拼接在特定版本Python下的报错`
  - `docs: 更新 README 运行文档`

---

## 4. 测试要求

在提交 Pull Request (PR) 之前，请确保完成以下测试：

1. **静态检查**：运行 `flake8` 或 `pylint` 检查代码规范。
2. **冒烟测试**：确保 `adb_gain_root.py` 在连接实车的情况下能正常获取 Root 权限并关闭日志。
3. **隔离性检查**：确保新代码产生的临时文件被记录在 `.gitignore` 中，不会被上传。

---

## 5. 提问与反馈

如果你发现了 Bug 或有新的需求（如接入新的图库 API），请先提交一个 **Issue** 进行讨论。

---

🚗 **Keep Coding, Keep Testing!**
