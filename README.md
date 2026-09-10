# WeLink Pi Bridge

通过 WeLink 私聊或专用群，在手机上调用电脑中的 Pi coding agent。所有有效消息必须以 `/` 开头，其他消息会被忽略。

## 快速开始

环境要求：

- Windows PowerShell 5 或 PowerShell 7。
- Python 3.10+。
- Node.js 和 npm。
- `welink-cli`。
- 已登录的 WeLink PC 客户端。

进入项目目录后执行：

```powershell
.\setup.ps1
```

向导会自动：

1. 刷新 WeLink Token并识别当前 UID。
2. 根据群名称查询控制群 ID。
3. 检查 Pi；未安装时询问是否自动安装。
4. 调用 `pi --list-models` 读取已认证的模型。
5. 读取 Pi 的默认模型。
6. 配置允许的工作区和默认目录。
7. 生成只保存在本机的 `config.json`。
8. 询问是否立即启动桥接服务。

Pi 的安装命令为：

```powershell
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
```

如果还没有登录模型提供方，先运行：

```powershell
pi
```

然后在 Pi 中执行：

```text
/login
```

完成后退出 Pi，再重新执行 `.\setup.ps1`。

## Pi 调用方式

桥接程序使用 Pi 官方非交互打印模式：

```text
pi --model {model} --no-session --no-approve -p
```

完整问题通过标准输入传入。Pi 只把最终回复写入标准输出，因此手机不会看到思考过程、工具调用过程或终端界面。

- `--model`：选择当前会话模型。
- `--no-session`：不创建 Pi 会话文件；最近对话由桥接程序管理。
- `--no-approve`：不加载未经信任的项目本地扩展和配置。
- `-p`：输出最终回复后退出。

## 手机命令

```text
/帮我分析当前项目的目录结构
/help
/help model
/help dir
/model list
/model current
/model switch 2
/model switch openai/gpt-5
/model help
/dir help
/dir current
/dir roots
/dir list
/dir cd 2
/dir back
/dir cd D:\workspaces\project-a
/dir root
/new
/new help
```

每个 WeLink 私聊或群聊分别保存模型和最近六轮上下文。切换模型时自动清除旧上下文。

每个会话也会独立保存 Pi 当前目录。目录只能在 `allowed_working_roots` 配置的工作区内切换；切换目录时会自动开启新对话，避免把上一个项目的上下文带入新项目。

## 启动与停止

完成配置后启动：

```powershell
.\start.ps1
```

首次启动只记录已有消息，不执行历史指令。启动后再发送一条新的 `/` 指令。

程序每5秒只查询控制群最新1条消息，并使用消息 ID 去重。请等待上一条指令被接收后再发送下一条，避免在一个轮询周期内连续发送多条导致遗漏。

停止服务：

```text
Ctrl+C
```

## WeLink Token 续期

程序启动时刷新一次 Token，之后默认每20分钟刷新。消息查询或发送失败时，还会强制刷新并重试一次。只要 WeLink PC 保持登录，一般不需要再次扫码。

## 本地文件

- `config.json`：本机配置，不提交 Git。
- `state.json`：消息去重、模型和对话状态，不提交 Git。
- `bridge.log`：运行错误，不提交 Git。

## 安全边界

Pi 默认拥有启动用户的文件、进程和网络权限。请只允许自己的 WeLink UID，并将其放在专用控制群中。当前默认使用 `--no-approve`，避免自动加载项目本地扩展；Pi 自带的文件和命令工具仍具有当前 Windows 用户权限。
