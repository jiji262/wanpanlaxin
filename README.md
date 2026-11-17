# 夸克转存助手 · QuarkMover Studio

一键（或批量）转存夸克网盘分享文件到你自己的网盘指定目录，自动生成专属于你的全新分享链接，并支持任意兼容 OpenAI 接口的大模型对推文文案进行原创二创改写。

## 核心特性

- **单条 / 批量转存换链**：支持一次性粘贴单条或多条夸克链接（兼容无 `https://` 前缀、`pan.quark.cn` / `pan.qoark.cn`、含 `?pwd=` 或「提取码：xxxx」口令），自动识别提取码并批量生成你的专属分享链接。
- **AI 原创推文二创**：自动剥离原帖中的夸克推广口令与旧链接，调用 DeepSeek / OpenAI / 智谱 GLM / Moonshot / 通义千问 / 本地 Ollama 改写为原创文案并拼接新链接。
- **双通道安全登录**：内置无头浏览器扫码登录（支持二维码自动/手动刷新），同时提供「手动粘贴 Cookie」备选通道，兼容无浏览器环境。
- **Studio 工作台体验**：遵循现代 SaaS 设计规范重构，支持浅色 / 深色 / 跟随系统三态外观、实时链接识别徽章、分步流水线进度、AI 接口延迟连通测试、快捷提取码与有效期切换，以及本机历史记录回溯。

## 快速开始

### 方式一：一键脚本启动（推荐，自动创建虚拟环境并安装依赖）

- **macOS**：双击 `启动.command`，或在终端执行 `./启动.sh`
- **Linux**：终端执行 `./启动.sh`
- **Windows**：双击 `启动.bat`

### 方式二：命令行手动运行

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python quark_mover.py
```

启动后会自动打开浏览器访问 `http://127.0.0.1:7788`（若端口占用会自动顺延）。支持 `--port 8899` 指定端口、`--no-browser` 静默后台启动。

## 支持的 AI 服务商预设

在右上角「设置」中点击预设标签即可一键填入接口地址与推荐模型，并可点击「测试连通性」验证密钥：

| 服务商 | Base URL | 推荐模型 |
|--------|----------|----------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Moonshot Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://localhost:11434/v1` | `llama3.2` |

## 项目结构

```text
├── quark_mover.py          # 多线程 Web 服务、夸克 API 流水线、AI 改写与历史管理
├── login_worker.py         # 无头浏览器二维码抓取、真刷新与登录态轮询
├── utils.py                # 跨平台浏览器探测、代理免疫 HTTP 客户端、日志与端口工具
├── static/
│   ├── index.html          # Studio 工作台前端界面
│   ├── styles.css          # 浅色/深色双主题设计系统样式
│   ├── app.js              # 前端状态同步、实时链接识别与交互控制器
│   └── qr_*.jpg            # 社群交流二维码静态资源
└── config/
    ├── config.example.json # 默认配置模板
    ├── config.json         # 本地运行配置（Git 忽略）
    ├── cookies.txt         # 本地夸克登录态（Git 忽略）
    └── history.json        # 本地转存历史记录（Git 忽略）
```

## License

MIT
