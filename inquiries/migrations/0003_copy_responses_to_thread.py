from django.db import migrations


def copy_responses(apps, schema_editor):
    """Replies saved before conversations existed become the first agent message of each thread."""
    Inquiry = apps.get_model("inquiries", "Inquiry")
    InquiryMessage = apps.get_model("inquiries", "InquiryMessage")
    for inq in Inquiry.objects.exclude(response=""):
        msg = InquiryMessage.objects.create(inquiry=inq, from_client=False, author=inq.assigned_agent,
                                            body=inq.response)
        if inq.responded_at:
            InquiryMessage.objects.filter(pk=msg.pk).update(created_at=inq.responded_at)


class Migration(migrations.Migration):
    dependencies = [("inquiries", "0002_inquiry_thread")]
    operations = [migrations.RunPython(copy_responses, migrations.RunPython.noop)]
