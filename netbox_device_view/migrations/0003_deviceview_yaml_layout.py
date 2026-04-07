from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_device_view", "0002_alter_deviceview_device_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceview",
            name="yaml_layout",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "YAML-based layout definition. When provided, this takes precedence over "
                    "the legacy CSS grid_template_area field for rendering. "
                    "See the documentation for the schema reference."
                ),
            ),
        ),
    ]
