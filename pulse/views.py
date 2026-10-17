import random

from django.contrib.auth import authenticate
from django.db.models import Count, Q, Value
from django.db.models.functions import Concat
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import NotificationLog, PriceAlert
from .permissions import IsAdminUser
from .serializers import LoginSerializer, PriceAlertSerializer, RegisterSerializer


def _token_response(user, status_code):
    refresh = RefreshToken.for_user(user)
    return Response(
        {
            'username': user.username,
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'role': user.profile.role,
        },
        status=status_code,
    )


# ---------------------------------------------------------------- Auth

class RegisterView(APIView):
    """POST /api/auth/register/ - public."""
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)   # 400 on duplicate username
        user = serializer.save()
        return _token_response(user, status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /api/auth/login/ - public."""
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data['username'],
            password=serializer.validated_data['password'],
        )
        if user is None:
            return Response({'detail': 'Invalid username or password.'},
                            status=status.HTTP_401_UNAUTHORIZED)
        return _token_response(user, status.HTTP_200_OK)


# ---------------------------------------------------------------- Alerts

class AlertListCreateView(generics.ListCreateAPIView):
    """GET / POST /api/alerts/ - JWT required."""
    serializer_class = PriceAlertSerializer

    def get_queryset(self):
        # A user only ever sees their own alerts
        return PriceAlert.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        # Owner comes from the token, never from the request body
        serializer.save(user=self.request.user)


class AlertDeactivateView(APIView):
    """DELETE /api/alerts/<id>/ - soft delete (status -> inactive)."""

    def delete(self, request, id):
        alert = get_object_or_404(PriceAlert, id=id)
        if alert.user != request.user:
            # Same response as "does not exist" so other users' alerts aren't exposed
            raise Http404
        alert.status = PriceAlert.Status.INACTIVE
        alert.save(update_fields=['status'])
        return Response({'status': alert.status}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------- Mock price feed

MOCK_PRICES = {
    'DEL-BOM': (3000, 7000),
    'BLR-HYD': (1500, 4000),
    'DEL-BLR': (4000, 9000),
    'BOM-GOA': (2000, 5000),
}


@require_GET
def get_flight_price(request):
    """GET /api/flights/price/?route=DEL-BOM - public, called by check_prices."""
    route = request.GET.get('route', '').strip().upper()
    price_range = MOCK_PRICES.get(route)
    if not price_range:
        return JsonResponse({'error': 'Route not found'}, status=404)
    price = random.randint(*price_range)
    return JsonResponse({'route': route, 'price': price})


# ---------------------------------------------------------------- Admin

class AdminSummaryView(APIView):
    """GET /api/admin/summary/ - role=ADMIN only."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        alert_stats = PriceAlert.objects.aggregate(
            total_alerts=Count('id'),
            active_alerts=Count('id', filter=Q(status=PriceAlert.Status.ACTIVE)),
            triggered_alerts=Count('id', filter=Q(status=PriceAlert.Status.TRIGGERED)),
        )
        notification_stats = NotificationLog.objects.aggregate(
            total_notifications=Count('id'),
        )
        top_routes = list(
            PriceAlert.objects
            .values('origin', 'destination')
            .annotate(alert_count=Count('id'))
            .annotate(route=Concat('origin', Value('-'), 'destination'))
            .order_by('-alert_count', 'origin', 'destination')
            .values('route', 'alert_count')[:5]
        )
        return Response({
            **alert_stats,
            **notification_stats,
            'top_routes': top_routes,
        })
