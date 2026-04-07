from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_device_view", "0003_deviceview_yaml_layout"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceview",
            name="render_mode",
            field=models.CharField(
                max_length=10,
                choices=[("css", "CSS Grid (default)"), ("svg", "SVG")],
                default="css",
                help_text=(
                    "Rendering engine to use for this device view. "
                    "'CSS Grid' uses the existing HTML+CSS approach and works with both YAML and legacy CSS layouts. "
                    "'SVG' produces a scalable vector graphic and requires a YAML layout."
                ),
            ),
        ),
    ]
