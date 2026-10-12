from django.db import models
from django.utils import timezone


class AuditModel(models.Model):
    created_at = models.DateTimeField(editable=False, db_comment="创建时间")
    updated_at = models.DateTimeField(db_comment="最近修改时间")
    created_by = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_comment="创建操作人稳定标识快照",
    )
    updated_by = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_comment="修改操作人稳定标识快照",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        now = timezone.now()
        if self._state.adding:
            self.created_at = now
        self.updated_at = now
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"updated_at"}
        return super().save(*args, **kwargs)
