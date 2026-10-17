from django.contrib.auth.models import User
from rest_framework import serializers

from .models import PriceAlert


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)
    email = serializers.EmailField(required=False, allow_blank=True)

    class Meta:
        model = User
        # 'role' is deliberately not a field: every new account is a USER.
        fields = ['username', 'password', 'email']

    def create(self, validated_data):
        # create_user hashes the password; the post_save signal creates the profile
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class PriceAlertSerializer(serializers.ModelSerializer):
    route = serializers.SerializerMethodField()

    class Meta:
        model = PriceAlert
        fields = ['id', 'origin', 'destination', 'route', 'threshold_price',
                  'status', 'created_at']
        # user comes from request.user; status is managed by the system
        read_only_fields = ['id', 'status', 'created_at']

    def get_route(self, obj):
        return f'{obj.origin}-{obj.destination}'

    def validate_origin(self, value):
        return value.strip().upper()

    def validate_destination(self, value):
        return value.strip().upper()

    def validate_threshold_price(self, value):
        if value <= 0:
            raise serializers.ValidationError('Threshold price must be greater than 0.')
        return value

    def validate(self, attrs):
        if attrs.get('origin') == attrs.get('destination'):
            raise serializers.ValidationError('Origin and destination must be different.')
        return attrs
