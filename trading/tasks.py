from celery import shared_task
from django.core.management import call_command


@shared_task
def sync_trading_daily_task():
    call_command("sync_trading_data", mode="full")
    return {"mode": "RESEARCH", "operation": "full"}
