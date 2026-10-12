# Spec Delta

## Purpose

规定全新环境中服务如何按锁定依赖启动、读取安全配置并连接 PostgreSQL 完成首次迁移，为后续业务能力提供可复现的运行基线，而不预先承诺任何尚未实现的业务 API。

## ADDED Requirements

### Requirement: 可复现的 Web 运行环境
系统 MUST 在 Python 3.13 环境中使用提交到仓库的单一 `uv.lock` 安装 Web 服务及验证所需依赖，且启动 Web 不要求安装或加载可选的推理运行时依赖。

#### Scenario: 仅安装 Web 依赖
- **WHEN** 在干净的 Python 3.13 环境中按锁文件安装 Web 依赖，且未安装推理运行时依赖
- **THEN** Django ASGI 应用可以加载并由 ASGI 服务器启动

#### Scenario: 干净检出可复现依赖
- **WHEN** 从干净检出的仓库按已声明的锁文件运行安装命令
- **THEN** 安装过程无需重新解析依赖即可使用仓库中的锁定版本

### Requirement: PostgreSQL 配置与安全启动
服务 MUST 从运行环境读取 PostgreSQL 连接信息和应用密钥；不得默默回退到 SQLite，且不得为生产启动提供硬编码的通用密钥。

#### Scenario: 使用明确配置的数据库
- **WHEN** 提供有效的 PostgreSQL 连接信息和应用密钥
- **THEN** Django 配置指向该 PostgreSQL 实例，配置检查成功

#### Scenario: 缺少关键配置
- **WHEN** 未提供必需的数据库连接信息或应用密钥
- **THEN** 服务启动或管理命令明确报告配置错误，而不是连接 SQLite 或使用通用密钥继续运行

### Requirement: 可重复的首次迁移
服务 MUST 在全新的 PostgreSQL 数据库中创建自定义用户和业务模型所需结构，且重复执行迁移不会产生新的模式变更。

#### Scenario: 从空数据库初始化
- **WHEN** 以有效配置对空的 PostgreSQL 数据库执行迁移
- **THEN** 自定义用户模型、业务模型及 Django 所需内置表建立成功，配置检查通过

#### Scenario: 重复执行迁移
- **WHEN** 在已完成首次迁移的数据库上再次执行迁移与迁移差异检查
- **THEN** 不会重建已有业务表，也不会生成未提交的模型变更
