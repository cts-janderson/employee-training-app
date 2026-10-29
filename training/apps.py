from django.apps import AppConfig


class TrainingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "training"

    def ready(self):
        from . import checks  # noqa: F401  Registers the deployment checks.
