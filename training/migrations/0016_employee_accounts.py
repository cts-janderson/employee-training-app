import django.db.models.deletion
from django.contrib.auth.hashers import make_password
from django.db import migrations, models


def give_unusable_passwords(apps, schema_editor):
    # Historical models lack set_unusable_password(); existing employees sign in only through the demo switcher.
    Employee = apps.get_model("training", "Employee")
    for employee in Employee.objects.filter(password="").only("pk"):
        Employee.objects.filter(pk=employee.pk).update(password=make_password(None))


class Migration(migrations.Migration):

    dependencies = [
        ("training", "0015_trainingversion_content_format_outlineitem_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="password",
            field=models.CharField(default="", max_length=128, verbose_name="password"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="employee",
            name="last_login",
            field=models.DateTimeField(blank=True, null=True, verbose_name="last login"),
        ),
        migrations.AddField(
            model_name="employee",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="approval_status",
            field=models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
                                   default="approved", max_length=10),
        ),
        migrations.AddField(
            model_name="employee",
            name="approval_decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="approval_decided_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="approval_decisions", to="training.employee"),
        ),
        migrations.AddField(
            model_name="employee",
            name="link_generation",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(give_unusable_passwords, migrations.RunPython.noop),
    ]
