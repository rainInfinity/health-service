"""定义持久化层识别的具体数据库约束异常。"""


class UsernameConflictError(Exception):
    """表示 PostgreSQL 用户名唯一约束冲突。"""
