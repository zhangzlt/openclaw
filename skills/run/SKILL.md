---
name: "run"
description: "启动和驱动项目：安装依赖、启动服务、运行测试、开发调试"
---

# /run — 启动和驱动项目

## 触发条件
用户请求运行项目、启动开发服务器、执行测试、调试应用。

## 执行流程

### 1. 项目检测
- 识别项目类型（通过 package.json / requirements.txt / Makefile）
- 检测包管理器（npm/yarn/pnpm/pip/poetry）

### 2. 依赖安装
```bash
# Node.js
npm install

# Python
pip install -r requirements.txt
```

### 3. 启动服务
```bash
# 根据 package.json scripts 或配置启动
npm run dev     # 开发模式
npm start       # 生产模式
npm test        # 运行测试
```

### 4. 运行检查
- 检查端口是否被占用
- 验证服务健康状态
- 读取启动日志

### 5. 常用操作
- **dev**: 启动开发服务器（热重载）
- **test**: 运行测试套件
- **build**: 构建生产版本
- **lint**: 代码检查
- **typecheck**: 类型检查
- **db:migrate**: 数据库迁移

### 6. 故障处理
- 端口占用 → 自动杀进程或换端口
- 依赖缺失 → 自动安装
- 启动失败 → 读取错误日志定位
