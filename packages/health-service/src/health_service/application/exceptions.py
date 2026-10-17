"""定义账号应用用例的业务异常。"""


class InvalidCredentials(Exception):
    """表示用户名、密码错误或账号不可用。"""


class InvalidRefreshToken(Exception):
    """表示 Refresh 无效、已撤销或所属账号不可用。"""


class RefreshChannelMismatch(Exception):
    """表示 Refresh 与请求的普通渠道不匹配。"""
