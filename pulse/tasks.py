import logging
from decimal import Decimal

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction

from .models import NotificationLog, PriceAlert

logger = logging.getLogger(__name__)


@shared_task
def check_prices():
    """Celery Beat task (every 60s): fetch prices and fire notifications."""
    active_alerts = PriceAlert.objects.filter(status=PriceAlert.Status.ACTIVE)
    routes = active_alerts.values_list('origin', 'destination').distinct()

    fired = 0
    for origin, destination in routes:
        route = f'{origin}-{destination}'

        # Backend-to-backend call to our own mock price feed
        try:
            response = requests.get(
                settings.PRICE_FEED_URL,
                params={'route': route},
                timeout=5,
            )
        except requests.RequestException as exc:
            logger.warning('Price feed unreachable for %s: %s', route, exc)
            continue
        if response.status_code != 200:
            logger.info('No price for %s (HTTP %s)', route, response.status_code)
            continue
        current_price = response.json().get('price')
        logger.info('Current price for %s: ₹%s', route, current_price)

        route_alerts = active_alerts.filter(origin=origin, destination=destination)
        for alert in route_alerts:
            if Decimal(str(current_price)) <= alert.threshold_price:
                # Fire-and-forget: don't wait for the notification to be written
                send_notification.delay(alert.id, current_price)
                fired += 1

    return f'checked {len(routes)} route(s), fired {fired} notification(s)'


@shared_task
@transaction.atomic
def send_notification(alert_id, triggered_price):
    """Async task: write a NotificationLog and mark the alert as triggered."""
    # Flip ACTIVE -> TRIGGERED atomically; if it's no longer active (already
    # triggered by an earlier queued task, or deactivated), do nothing.
    updated = PriceAlert.objects.filter(
        id=alert_id, status=PriceAlert.Status.ACTIVE
    ).update(status=PriceAlert.Status.TRIGGERED)
    if not updated:
        return f'alert {alert_id} skipped (not active)'

    alert = PriceAlert.objects.get(id=alert_id)
    message = (f'Price alert triggered! {alert.origin}-{alert.destination} '
               f'is now ₹{triggered_price} - below your threshold of '
               f'₹{alert.threshold_price}')
    NotificationLog.objects.create(
        alert=alert,
        triggered_price=triggered_price,
        message=message,
    )
    logger.info(message)
    return message
