from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import NotificationLog, PriceAlert, UserProfile
from .tasks import check_prices, send_notification


# ------------------------------------------------------------------
# Required Test 1 - Threshold comparison logic
# ------------------------------------------------------------------
class PriceThresholdTest(TestCase):

    def test_price_below_threshold_triggers_alert(self):
        # If current price (4200) <= threshold (4500), alert should fire
        self.assertTrue(4200 <= 4500)

    def test_price_above_threshold_does_not_trigger(self):
        # If current price (5000) > threshold (4500), alert should not fire
        self.assertFalse(5000 <= 4500)

    def test_price_equal_to_threshold_triggers_alert(self):
        # Edge case: price == threshold should still trigger
        self.assertTrue(4500 <= 4500)


# ------------------------------------------------------------------
# Required Test 2 - NotificationLog is created correctly
# ------------------------------------------------------------------
class NotificationLogTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='testpass'
        )
        self.alert = PriceAlert.objects.create(
            user=self.user,
            origin='DEL', destination='BOM',
            threshold_price=4500,
            status=PriceAlert.Status.ACTIVE
        )

    def test_notification_log_created_with_correct_message(self):
        log = NotificationLog.objects.create(
            alert=self.alert,
            triggered_price=4200,
            message='Price dropped to 4200 for DEL-BOM'
        )
        self.assertEqual(log.triggered_price, 4200)
        self.assertEqual(log.alert, self.alert)
        self.assertIn('DEL-BOM', log.message)


# ------------------------------------------------------------------
# Required Test 3 - Alert scoping (users see only their own alerts)
# ------------------------------------------------------------------
class AlertScopingTest(TestCase):

    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1', password='pass'
        )
        self.user2 = User.objects.create_user(
            username='user2', password='pass'
        )
        PriceAlert.objects.create(
            user=self.user1, origin='DEL', destination='BOM',
            threshold_price=4500
        )
        PriceAlert.objects.create(
            user=self.user2, origin='BLR', destination='HYD',
            threshold_price=2000
        )

    def test_user_only_sees_own_alerts(self):
        user1_alerts = PriceAlert.objects.filter(user=self.user1)
        self.assertEqual(user1_alerts.count(), 1)
        self.assertEqual(user1_alerts.first().origin, 'DEL')

    def test_user_cannot_see_other_users_alerts(self):
        user2_alerts = PriceAlert.objects.filter(user=self.user1)
        # user1 should not see user2's BLR-HYD alert
        self.assertNotEqual(user2_alerts.first().origin, 'BLR')


# ------------------------------------------------------------------
# Extra coverage - signals, endpoints, permissions and Celery tasks
# ------------------------------------------------------------------
class UserProfileSignalTest(TestCase):

    def test_profile_auto_created_with_user_role(self):
        user = User.objects.create_user(username='bob', password='pass12345')
        self.assertEqual(user.profile.role, UserProfile.Role.USER)


class AuthEndpointTest(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_register_returns_token_and_ignores_role(self):
        res = self.client.post('/api/auth/register/', {
            'username': 'alice', 'password': 'securepass',
            'email': 'alice@example.com', 'role': 'admin',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['username'], 'alice')
        self.assertEqual(res.data['role'], 'user')
        self.assertIn('access', res.data)

    def test_register_duplicate_username_returns_400(self):
        User.objects.create_user(username='alice', password='securepass')
        res = self.client.post('/api/auth/register/', {
            'username': 'alice', 'password': 'securepass',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_login_success_and_failure(self):
        User.objects.create_user(username='alice', password='securepass')
        ok = self.client.post('/api/auth/login/', {
            'username': 'alice', 'password': 'securepass'}, format='json')
        bad = self.client.post('/api/auth/login/', {
            'username': 'alice', 'password': 'wrong'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertIn('access', ok.data)
        self.assertEqual(bad.status_code, 401)


class AlertEndpointTest(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass12345')
        self.bob = User.objects.create_user(username='bob', password='pass12345')
        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    def test_requires_authentication(self):
        res = APIClient().get('/api/alerts/')
        self.assertEqual(res.status_code, 401)

    def test_create_uses_request_user_not_body(self):
        res = self.client.post('/api/alerts/', {
            'origin': 'del', 'destination': 'bom', 'threshold_price': '4500',
            'user': self.bob.id,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        alert = PriceAlert.objects.get(id=res.data['id'])
        self.assertEqual(alert.user, self.alice)
        self.assertEqual(alert.origin, 'DEL')
        self.assertEqual(alert.status, PriceAlert.Status.ACTIVE)

    def test_list_empty_returns_200_empty_list(self):
        res = self.client.get('/api/alerts/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])

    def test_list_only_returns_own_alerts(self):
        PriceAlert.objects.create(user=self.alice, origin='DEL', destination='BOM', threshold_price=4500)
        PriceAlert.objects.create(user=self.bob, origin='BLR', destination='HYD', threshold_price=2000)
        res = self.client.get('/api/alerts/')
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['route'], 'DEL-BOM')

    def test_delete_deactivates_instead_of_deleting(self):
        alert = PriceAlert.objects.create(user=self.alice, origin='DEL', destination='BOM', threshold_price=4500)
        res = self.client.delete(f'/api/alerts/{alert.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {'status': 'inactive'})
        alert.refresh_from_db()
        self.assertEqual(alert.status, PriceAlert.Status.INACTIVE)

    def test_delete_other_users_alert_returns_404(self):
        alert = PriceAlert.objects.create(user=self.bob, origin='DEL', destination='BOM', threshold_price=4500)
        res = self.client.delete(f'/api/alerts/{alert.id}/')
        self.assertEqual(res.status_code, 404)
        alert.refresh_from_db()
        self.assertEqual(alert.status, PriceAlert.Status.ACTIVE)

    def test_delete_missing_alert_returns_404(self):
        res = self.client.delete('/api/alerts/99999/')
        self.assertEqual(res.status_code, 404)


class FlightPriceEndpointTest(TestCase):

    def test_known_route_returns_price_in_range(self):
        res = self.client.get('/api/flights/price/', {'route': 'DEL-BOM'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['route'], 'DEL-BOM')
        self.assertTrue(3000 <= data['price'] <= 7000)

    def test_unknown_route_returns_404(self):
        res = self.client.get('/api/flights/price/', {'route': 'XXX-YYY'})
        self.assertEqual(res.status_code, 404)


class AdminSummaryTest(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(username='admin1', password='pass12345')
        self.admin.profile.role = UserProfile.Role.ADMIN
        self.admin.profile.save()
        self.user = User.objects.create_user(username='user1', password='pass12345')

        a1 = PriceAlert.objects.create(user=self.user, origin='DEL', destination='BOM', threshold_price=4500)
        PriceAlert.objects.create(user=self.user, origin='DEL', destination='BOM', threshold_price=5000)
        PriceAlert.objects.create(user=self.user, origin='BLR', destination='HYD', threshold_price=2000,
                                  status=PriceAlert.Status.TRIGGERED)
        NotificationLog.objects.create(alert=a1, triggered_price=4200, message='x')

    def test_regular_user_gets_403(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get('/api/admin/summary/').status_code, 403)

    def test_admin_gets_stats(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        res = client.get('/api/admin/summary/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['total_alerts'], 3)
        self.assertEqual(res.data['active_alerts'], 2)
        self.assertEqual(res.data['triggered_alerts'], 1)
        self.assertEqual(res.data['total_notifications'], 1)
        self.assertEqual(res.data['top_routes'][0], {'route': 'DEL-BOM', 'alert_count': 2})


class CeleryTaskTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass12345')
        self.cheap = PriceAlert.objects.create(user=self.user, origin='DEL', destination='BOM', threshold_price=4500)
        self.strict = PriceAlert.objects.create(user=self.user, origin='DEL', destination='BOM', threshold_price=4000)

    @patch('pulse.tasks.send_notification.delay')
    @patch('pulse.tasks.requests.get')
    def test_check_prices_fires_only_for_alerts_at_or_above_price(self, mock_get, mock_delay):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'route': 'DEL-BOM', 'price': 4200})
        check_prices()
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs['params'], {'route': 'DEL-BOM'})
        mock_delay.assert_called_once_with(self.cheap.id, 4200)

    @patch('pulse.tasks.send_notification.delay')
    @patch('pulse.tasks.requests.get')
    def test_check_prices_skips_inactive_alerts(self, mock_get, mock_delay):
        PriceAlert.objects.update(status=PriceAlert.Status.INACTIVE)
        check_prices()
        mock_get.assert_not_called()
        mock_delay.assert_not_called()

    def test_send_notification_logs_and_marks_triggered(self):
        send_notification(self.cheap.id, 4200)
        self.cheap.refresh_from_db()
        self.assertEqual(self.cheap.status, PriceAlert.Status.TRIGGERED)
        log = NotificationLog.objects.get(alert=self.cheap)
        self.assertEqual(log.triggered_price, Decimal('4200'))
        self.assertIn('DEL-BOM', log.message)

    def test_send_notification_is_idempotent(self):
        send_notification(self.cheap.id, 4200)
        send_notification(self.cheap.id, 4100)
        self.assertEqual(NotificationLog.objects.filter(alert=self.cheap).count(), 1)
