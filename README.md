# 夸克转存助手 (QuarkMover)

一键转存夸克网盘分享文件到你自己的网盘，生成属于你的新分享链接。

> **AI 通用版** - 支持任意兼容 OpenAI 格式的 API（DeepSeek、OpenAI、智谱、Moonshot、通义等）

## 下载

前往 [Releases](../../releases) 页面，根据你的系统下载对应版本：

| 系统 | 下载文件 |
|------|---------|
| Windows | `QuarkMover-Windows.exe` |
| macOS (Intel) | `QuarkMover-macOS-Intel` |
| macOS (Apple Silicon) | `QuarkMover-macOS-ARM` |
| Linux | `QuarkMover-Linux` |

## 使用方法

### 第一步：打开工具

- **Windows**：双击 `QuarkMover-Windows.exe`
- **macOS**：终端运行 `chmod +x QuarkMover-macOS-* && ./QuarkMover-macOS-*`
- **Linux**：终端运行 `chmod +x QuarkMover-Linux && ./QuarkMover-Linux`

工具会自动在浏览器中打开操作页面。

### 第二步：扫码登录

首次使用会自动弹出登录窗口，用夸克 App 扫描二维码即可。登录状态会保存，下次打开无需重新登录（约几天后过期需重新扫码）。

### 第三步：配置 AI（可选）

点击右上角「设置」，配置你的 AI API：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| AI API 密钥 | 你的 API Key | `sk-xxx...` |
| AI Base URL | API 基础地址 | `https://api.deepseek.com` |
| 模型名称 | 要使用的模型 | `deepseek-chat` |

**支持的 API 提供商：**

| 提供商 | Base URL | 模型名称 |
|--------|----------|----------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |
| 本地 Ollama | `http://localhost:11434/v1` | `llama3.2` |

### 第四步：开始转存

1. 复制别人的夸克分享链接（如 `pan.quark.cn/s/xxxxx`）
2. 粘贴到输入框
3. 点击「一键生成」
4. 右侧会显示你自己的新分享链接，点「一键复制」即可

## 进阶功能

- **二创模式**：切换到「完整模式」，粘贴推文内容 + 夸克链接，工具会用 AI 改写文案
- **提取码**：在设置中开启「随机提取码」，生成的分享链接会带 4 位提取码
- **有效期**：可选永久 / 1天 / 7天 / 30天
- **主题**：支持深色/浅色/跟随系统

## 从源码运行

```bash
# 克隆仓库
git clone https://github.com/YOUR_USERNAME/QuarkMover.git
cd QuarkMover

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行
python3 quark_mover.py
```

## 系统要求

- 需要本机安装 Chrome 或 Edge 浏览器（用于扫码登录）
- Windows 10+、macOS 10.15+、Ubuntu 20.04+

## 常见问题

**Q: 提示"未找到浏览器"？**
A: 请安装 [Google Chrome](https://www.google.com/chrome/) 或使用系统自带的 Edge 浏览器。

**Q: 转存失败，提示"未登录"？**
A: 登录态已过期，点击右上角「扫码登录夸克」重新登录。

**Q: 二创功能报错？**
A: 需要在设置中配置有效的 AI API Key 和 Base URL。

## License

MIT
