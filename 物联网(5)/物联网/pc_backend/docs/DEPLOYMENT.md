# 部署说明

## 1. 安装与启动

```bash
cd pc_backend
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，然后从火山引擎边缘大模型网关（AI Gateway）控制台“查看代码”中复制实际参数：

```text
FOCUSCUBE_LLM_PROVIDER=volcengine_ai_gateway
FOCUSCUBE_LLM_BASE_URL=TODO_填写控制台给出的Base_URL
FOCUSCUBE_LLM_API_KEY=TODO_填写网关访问密钥
FOCUSCUBE_LLM_MODEL=TODO_填写该密钥关联的模型标识
```

启动：

```bash
python run.py
```

## 2. 成员 C 必须自己确认的参数

- `FOCUSCUBE_LLM_BASE_URL`：控制台示例中的 OpenAI 兼容地址；可填写 base URL，也可填写完整 `/chat/completions` 地址。
- `FOCUSCUBE_LLM_API_KEY`：AI Gateway 网关访问密钥，不能提交到仓库、文档或截图。
- `FOCUSCUBE_LLM_MODEL`：当前网关访问密钥关联的实际模型标识。
- 成员 C 电脑局域网 IPv4：Windows 使用 `ipconfig` 查看。
- TCP 8000：允许 Python 通过防火墙，或手工放行端口。
- 所有设备连接同一局域网。

## 3. 局域网地址

后端监听 `0.0.0.0:8000`。对外地址示例：

```text
http://192.168.31.100:8000
```

IP 必须替换成成员 C 电脑的实际地址。AI Gateway 只由后端调用，不需要成员 A、B、D 配置网关密钥。

## 4. 当前云服务器部署（2026-07-19）

当前生产部署已经通过 SSH 别名 `my-server` 安装到：

```text
/opt/focuscube-backend
```

运行结构：

```text
P4 / Web
  -> http://82.156.238.244/focuscube/
  -> Apache 反向代理
  -> 127.0.0.1:8001
  -> focuscube-backend.service
```

原有智能家居后端继续使用 `127.0.0.1:8000`，FocusCube 不占用或覆盖该端口。

常用检查命令：

```bash
ssh my-server 'systemctl status focuscube-backend.service --no-pager'
ssh my-server 'sudo journalctl -u focuscube-backend.service -n 100 --no-pager'
curl http://82.156.238.244/focuscube/health
curl -I http://82.156.238.244/focuscube/dashboard/
```

Web 看板入口：

```text
http://82.156.238.244/focuscube/dashboard/
```

服务已启用开机启动和异常自动重启：

```text
systemctl enabled: enabled
systemctl active: active
Restart=always
RestartSec=3s
```

服务器环境文件位于 `/opt/focuscube-backend/.env`，权限为 `0600`。AI Gateway 的 Base URL、访问密钥和模型标识已仅配置在该文件中；2026-07-19 已通过一次真实日报生成验证。不要把密钥同步回仓库。
