# Tarot-o-bot 本地化版本

这是将原始 tarotobot.illo.tv 网站与后端 API 功能集成的版本。

## 功能特性

- ✅ 保持原始 tarotobot 的界面布局和样式
- ✅ 集成后端 API：预抽塔罗牌、命理推演、AI 生成占卜结果
- ✅ 支持百度翻译（英文塔罗牌名翻译成中文）
- ✅ 支持讯飞语音转文字（可选）
- ✅ 使用 Kimi (Moonshot) 模型生成占卜解读

## 安装和运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务器

**注意**：所有 API 密钥已直接写在 `server.py` 文件中，无需设置环境变量。

cd tarotobot_site

```bash
cd tarotobot_site
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### 4. 访问网站

打开浏览器访问：`http://localhost:8000`

## 使用流程

1. **首页**：点击 "Press Enter" 按钮
2. **输入信息**：填写姓名、出生日期、问题
3. **点击"开始占卜"**：系统会预抽 3 张塔罗牌
4. **结果页**：点击 3 张卡片，系统会调用后端 API 生成占卜结果
5. **查看结果**：显示 AI 生成的运势解析

## 项目结构

```
tarotobot_site/
├── server.py              # FastAPI 后端服务器
├── index.html             # 首页（已添加用户输入表单）
├── prediction/
│   └── 17/
│       └── index.html     # 结果页（已集成后端 API）
├── includes/              # 静态资源（CSS、JS、图片等）
│   ├── CSS/
│   ├── js/
│   ├── images/
│   └── ...
└── requirements.txt        # Python 依赖
```

## API 端点

- `GET /api/precards` - 预抽 3 张塔罗牌并翻译成中文
- `POST /api/divine` - 生成占卜结果（需要用户信息、问题、抽牌结果）
- `WebSocket /ws/asr` - 讯飞语音转文字代理（可选）

## 注意事项

- 所有 API 密钥通过环境变量配置，**不要硬编码在代码中**
- 如果百度翻译 API 调用失败，塔罗牌名会显示英文
- 如果外部 tarotapi.dev 不可用，会使用本地回退逻辑
- 界面样式完全保持原始 tarotobot 的设计
