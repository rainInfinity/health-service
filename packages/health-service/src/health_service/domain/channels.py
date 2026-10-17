"""定义 API 渠道标识及普通渠道边界。"""

from enum import StrEnum


class ChannelLabel(StrEnum):
    """业务接入渠道；与 Django AppConfig.label 无关。"""

    ANDROID_CN = "android-cn"
    ANDROID_INTL = "android-intl"
    IOS_CN = "ios-cn"
    IOS_INTL = "ios-intl"
    WXAPP = "wxapp"
    MANAGER = "manager"


ORDINARY_CHANNELS = frozenset(
    {
        ChannelLabel.ANDROID_CN.value,
        ChannelLabel.ANDROID_INTL.value,
        ChannelLabel.IOS_CN.value,
        ChannelLabel.IOS_INTL.value,
        ChannelLabel.WXAPP.value,
    }
)
