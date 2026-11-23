"""从权威 JSON 结果筛选有效记录，只读取命中页的公开标量。"""

from datetime import datetime
from decimal import Decimal
from sys import float_info

from django.db.models import (
    Case, CharField, DecimalField, F, Func, JSONField, Q, QuerySet, Value, When,
)
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.lookups import Exact

from health_service.domain.measurements import (
    MEASUREMENT_DEFINITIONS, MeasurementDefinition, MeasurementSource, Metric,
)
from health_service.models import Task
from health_service.schemas.internal.measurements import (
    MeasurementQualityData, MeasurementReadingData, MeasurementRecordData,
)

_MAX_FINITE = Decimal(str(float_info.max))
_RECORD_FIELDS = (
    "id", "submitted_at", "capture_method", "capture_mode", "duration_ms",
    "model_version_id", "model_version__model_id", "model_version__version",
)


def list_user_measurements(
    *,
    user_id: int,
    channel_label: str,
    metric: Metric,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[int, tuple[MeasurementRecordData, ...]]:
    """在全部准入条件生效后计数并按提交时间稳定分页。"""
    definition = MEASUREMENT_DEFINITIONS[metric]
    records = _valid_records(
        user_id=user_id, channel_label=channel_label, definition=definition,
        from_time=from_time, to_time=to_time,
    )
    total = records.count()
    page = _record_scalars(records, definition)[offset : offset + limit]
    return total, tuple(_record_data(row, definition) for row in page)


def latest_user_measurement(
    *, user_id: int, channel_label: str, metric: Metric,
) -> MeasurementRecordData | None:
    """以同一有效性合同只读取最近一条，不额外执行计数。"""
    definition = MEASUREMENT_DEFINITIONS[metric]
    records = _valid_records(
        user_id=user_id, channel_label=channel_label, definition=definition,
    )
    row = _record_scalars(records, definition).first()
    return None if row is None else _record_data(row, definition)


def _json_path(path: tuple[str, ...], root: str = "result_payload"):
    """从固定映射构造 JSON 路径，不允许调用方传入动态合同。"""
    expression = F(root)
    for key in path:
        expression = KeyTransform(key, expression)
    return expression


def _json_type(expression, expected: str):
    """检查 JSON 本身类型，避免文本数字或布尔绕过准入。"""
    return Exact(Func(expression, function="jsonb_typeof", output_field=CharField()), Value(expected))


def _guarded_number(key: str, root: str = "_m_payload"):
    """只在 JSON 为 number 的 CASE 分支转换，避免任意历史载荷导致 SQL 异常。"""
    expression = KeyTransform(key, F(root))
    return Case(
        When(
            _json_type(expression, "number"),
            then=Func(
                KeyTextTransform(key, F(root)),
                template="(%(expressions)s)::numeric",
                output_field=DecimalField(),
            ),
        ),
        default=Value(None),
        output_field=DecimalField(),
    )


def _source_condition(source: MeasurementSource, definition: MeasurementDefinition) -> Q:
    """同时限制任务算法、场景汇总结局和实际成功成员身份。"""
    condition = Q(model_version__algorithm_key=source.task_algorithm_key)
    if source.member is not None:
        member_path = f"result_payload__results__{source.member}"
        condition &= Q(result_payload__outcome__in=("ALL_SUCCEEDED", "PARTIALLY_SUCCEEDED"))
        condition &= Q(**{
            f"{member_path}__status": "SUCCEEDED",
            f"{member_path}__algorithmKey": definition.algorithm_key,
        })
    return condition


def _valid_records(
    *, user_id: int, channel_label: str, definition: MeasurementDefinition,
    from_time: datetime | None = None, to_time: datetime | None = None,
) -> QuerySet:
    """让成对读数、风险必要字段与来源身份在 count 和分页之前全部生效。"""
    source_conditions = [_source_condition(source, definition) for source in definition.sources]
    source_filter = source_conditions[0]
    for condition in source_conditions[1:]:
        source_filter |= condition
    records = Task.objects.filter(
        source_filter,
        user_id=user_id,
        channel__label=channel_label,
        status=Task.StatusChoices.SUCCEEDED,
        submitted_at__isnull=False,
    )
    if from_time is not None:
        records = records.filter(submitted_at__gte=from_time)
    if to_time is not None:
        records = records.filter(submitted_at__lt=to_time)
    records = records.alias(_m_payload=Case(
        *(
            When(condition, then=_json_path(source.result_path))
            for source, condition in zip(definition.sources, source_conditions, strict=True)
        ),
        default=Value(None), output_field=JSONField(),
    )).filter(_json_type(F("_m_payload"), "object"))
    for reading in definition.readings:
        name = f"_m_number_{reading.key}"
        records = records.alias(**{name: _guarded_number(reading.key)}).filter(**{
            f"{name}__gte": reading.minimum if reading.minimum is not None else -_MAX_FINITE,
            f"{name}__lte": reading.maximum if reading.maximum is not None else _MAX_FINITE,
        })
    if definition.risk_key is not None:
        records = records.alias(
            _m_risk_text=KeyTextTransform(definition.risk_key, F("_m_payload")),
        ).filter(
            _json_type(KeyTransform(definition.risk_key, F("_m_payload")), "number"),
            _m_risk_text__in=("0", "1", "2"),
        )
    return records.order_by("-submitted_at", "-pk")


def _record_scalars(records: QuerySet, definition: MeasurementDefinition) -> QuerySet:
    """仅选取公开记录标量，质量非法时只清空质量，不排除合法读数。"""
    records = records.alias(
        _m_quality=_json_path(("quality",), root="_m_payload"),
        _m_quality_score=_guarded_number("score", root="_m_quality"),
        _m_quality_threshold=KeyTextTransform("threshold", F("_m_quality")),
    )
    quality_valid = (
        Q(_m_quality_score__gte=definition.quality_threshold, _m_quality_score__lte=_MAX_FINITE)
        & Q(_m_quality_threshold=str(definition.quality_threshold))
        & Q(_json_type(KeyTransform("threshold", F("_m_quality")), "number"))
    )
    selections = {
        f"_m_value_{reading.key}": KeyTransform(reading.key, F("_m_payload"))
        for reading in definition.readings
    }
    for key in ("score", "threshold"):
        selections[f"_m_public_{key}"] = Case(
            When(quality_valid, then=KeyTransform(key, F("_m_quality"))),
            default=Value(None), output_field=JSONField(),
        )
    if definition.risk_key is not None:
        selections["_m_public_risk"] = KeyTransform(definition.risk_key, F("_m_payload"))
    return records.annotate(**selections).values(*_RECORD_FIELDS, *selections)


def _record_data(row: dict, definition: MeasurementDefinition) -> MeasurementRecordData:
    """把数据库已筛选的标量组装为记录，不二次舍入、分级或丢弃页内任务。"""
    quality = None
    if row["_m_public_score"] is not None and row["_m_public_threshold"] is not None:
        quality = MeasurementQualityData(
            score=row["_m_public_score"], threshold=row["_m_public_threshold"],
        )
    return MeasurementRecordData(
        record_id=f"{row['id']}:{definition.metric}",
        task_id=row["id"], metric=definition.metric, submitted_at=row["submitted_at"],
        capture_method=row["capture_method"], capture_mode=row["capture_mode"],
        duration_ms=row["duration_ms"],
        readings=tuple(
            MeasurementReadingData(
                code=reading.code, value=row[f"_m_value_{reading.key}"], unit=reading.unit,
            )
            for reading in definition.readings
        ),
        quality=quality, risk_level=row.get("_m_public_risk"),
        model_id=row["model_version__model_id"], version_id=row["model_version_id"],
        version=row["model_version__version"], algorithm_key=definition.algorithm_key,
    )
