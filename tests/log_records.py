"""解析应用控制台日志，供请求和消费者行为验证使用。"""

import re
import shlex

_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ([A-Z]+)(?: (.*))?$")


def parse_console_records(output: str) -> list[dict[str, str]]:
    """将无键时间与级别前缀和后续键值字段解析为统一记录。"""
    records = []
    for line in output.splitlines():
        match = _PREFIX.fullmatch(line)
        if match is None:
            continue
        timestamp, level, fields = match.groups()
        records.append(
            {
                "timestamp": timestamp,
                "level": level,
                **dict(field.split("=", 1) for field in shlex.split(fields or "")),
            }
        )
    return records
