from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "ผู้ดูแลระบบ"
    OUTSOURCE = "outsource", "เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource)"


class User(AbstractUser):
    role = models.CharField("บทบาท", max_length=20, choices=Role.choices, default=Role.OUTSOURCE)
    # 1:1 link set manually by an administrator (never auto-matched by name).
    investigator = models.OneToOneField(
        "registry.Investigator",
        verbose_name="พนักงานสอบสวนที่ผูกไว้",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="user",
    )

    class Meta:
        verbose_name = "ผู้ใช้"
        verbose_name_plural = "ผู้ใช้"

    @property
    def effective_role(self) -> str:
        if self.is_superuser:
            return Role.ADMIN
        return self.role

    @property
    def role_label(self) -> str:
        return Role(self.effective_role).label if self.effective_role in Role.values else self.effective_role

    def display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.username
